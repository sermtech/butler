/**
 * The WhatsApp store: contacts, chats and messages persisted from Baileys events.
 *
 * WHY THIS EXISTS AT ALL. Baileys is a stateless socket -- it emits events and
 * remembers nothing between restarts, and `makeInMemoryStore` was REMOVED in
 * v7. So a contact list, a chat list and searchable history are not features
 * to switch on; they are state this application has to accumulate itself from
 * events that were already arriving and being dropped on the floor.
 *
 * Everything here runs against a temp file. No socket, no WhatsApp, no
 * DATA_ROOT: the store is fed recorded event payloads shaped like the real
 * ones, which is the only way these assertions can run without a linked phone.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { openWhatsAppStore, extractMessageText, unwrapMessage } from '../whatsapp-store.js';

function tempDb() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  return path.join(dir, 'whatsapp.db');
}

const ALICE = '15551230000@s.whatsapp.net';
const BOB = '15559876543@s.whatsapp.net';
const GROUP = '120363000000000000@g.us';

/** Baileys delivers timestamps as number OR as a protobuf Long. Both appear. */
const asLong = (n) => ({ low: n, high: 0, unsigned: false, toNumber: () => n });

function msg(jid, id, text, ts, extra = {}) {
  return {
    key: { remoteJid: jid, id, fromMe: false, ...(extra.participant ? { participant: extra.participant } : {}) },
    messageTimestamp: extra.long ? asLong(ts) : ts,
    pushName: extra.pushName || 'Alice',
    message: { conversation: text },
  };
}

test('the history payload is persisted and survives a reopen', () => {
  const dbPath = tempDb();
  const store = openWhatsAppStore(dbPath);
  store.ingestHistory({
    contacts: [
      { id: ALICE, name: 'Alice Alvarez', notify: 'Alice' },
      { id: BOB, notify: 'Bob' },
    ],
    chats: [
      { id: ALICE, conversationTimestamp: 1700000000 },
      { id: GROUP, name: 'Family', conversationTimestamp: 1700000100 },
    ],
    messages: [msg(ALICE, 'M1', 'first message', 1700000000)],
  });
  store.close();

  // The whole point: a process restart must not lose the address book.
  const reopened = openWhatsAppStore(dbPath);
  assert.equal(reopened.listContacts().length, 2);
  assert.equal(reopened.listChats().length, 2);
  assert.equal(reopened.readChat(ALICE).length, 1);
  reopened.close();
});

test('a group chat is distinguishable from a DM without re-parsing the JID everywhere', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestHistory({ chats: [{ id: ALICE }, { id: GROUP, name: 'Family' }] });
  const groups = store.listChats({ groupsOnly: true });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].jid, GROUP);
  assert.equal(groups[0].name, 'Family');
  store.close();
});

test('getMessage returns the stored plaintext, and undefined for an unknown key', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestMessages([msg(ALICE, 'M7', 'the original', 1700000000)]);
  // Baileys hands back exactly what it needs to re-encrypt: the message content.
  assert.deepEqual(store.getMessage({ remoteJid: ALICE, id: 'M7' }), { conversation: 'the original' });
  assert.equal(store.getMessage({ remoteJid: ALICE, id: 'NOPE' }), undefined);
  store.close();
});

test('a message is stored once, however many times the event is replayed', () => {
  const store = openWhatsAppStore(tempDb());
  const m = msg(ALICE, 'DUP', 'only once', 1700000000);
  store.ingestMessages([m]);
  store.ingestMessages([m]);
  // Baileys re-delivers on reconnect; an append-only store would double every
  // message the agent later reads back.
  assert.equal(store.readChat(ALICE).length, 1);
  store.close();
});

test('readChat returns newest first and honours the count', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestMessages([
    msg(ALICE, 'A', 'oldest', 1700000000),
    msg(ALICE, 'B', 'middle', 1700000100),
    msg(ALICE, 'C', 'newest', 1700000200),
    msg(BOB, 'D', 'other chat', 1700000300),
  ]);
  const recent = store.readChat(ALICE, 2);
  assert.deepEqual(recent.map((m) => m.text), ['newest', 'middle']);
  store.close();
});

test('a protobuf Long timestamp orders the same as a plain number', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestMessages([
    msg(ALICE, 'L1', 'earlier', 1700000000, { long: true }),
    msg(ALICE, 'L2', 'later', 1700000900),
  ]);
  // A Long stored verbatim sorts as an object and silently scrambles history.
  assert.deepEqual(store.readChat(ALICE).map((m) => m.text), ['later', 'earlier']);
  store.close();
});

test('searchMessages finds a substring across chats', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestMessages([
    msg(ALICE, 'S1', 'dinner at eight', 1700000000),
    msg(BOB, 'S2', 'what about dinner tomorrow', 1700000100),
    msg(BOB, 'S3', 'unrelated', 1700000200),
  ]);
  const hits = store.searchMessages('dinner');
  assert.equal(hits.length, 2);
  store.close();
});

test('resolveRecipient accepts a phone number in any human format', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  for (const input of ['+1 555 123 0000', '15551230000', ALICE]) {
    const r = store.resolveRecipient(input);
    assert.equal(r.jid, ALICE, `failed for ${input}`);
  }
  store.close();
});

test('resolveRecipient matches a contact by name, case-insensitively', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  assert.equal(store.resolveRecipient('alice').jid, ALICE);
  store.close();
});

test('AN AMBIGUOUS NAME RESOLVES TO NOTHING AND LISTS THE CANDIDATES', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([
    { id: ALICE, name: 'Alice Alvarez' },
    { id: BOB, name: 'Alice Bertrand' },
  ]);
  const r = store.resolveRecipient('alice');
  // Guessing here is how a message reaches the WRONG PERSON from the owner's
  // real number. The caller must be forced to disambiguate.
  assert.equal(r.jid, null);
  assert.equal(r.candidates.length, 2);
  store.close();
});

test('an unknown recipient resolves to nothing rather than a fabricated JID', () => {
  const store = openWhatsAppStore(tempDb());
  const r = store.resolveRecipient('Nobody I Know');
  assert.equal(r.jid, null);
  assert.equal(r.candidates.length, 0);
  store.close();
});

test('resolveChat finds a group by its name, which is not in the address book', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  store.ingestHistory({
    chats: [{ id: GROUP, name: 'Family Trip' }],
  });
  // A group has no contact row, so contact resolution alone cannot reach it --
  // which would make "read my group chat" impossible while looking like a
  // spelling problem.
  assert.equal(store.resolveChat('Family Trip').jid, GROUP);
  assert.equal(store.resolveChat('family').jid, GROUP);
  // And a person still resolves through the same call.
  assert.equal(store.resolveChat('Alice').jid, ALICE);
  store.close();
});

test('resolveChat refuses to guess between a group and a person with similar names', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Book Club Anna' }]);
  store.ingestHistory({
    chats: [{ id: GROUP, name: 'Book Club' }],
  });
  const r = store.resolveChat('book club');
  assert.equal(r.jid, null);
  assert.equal(r.candidates.length, 2);
  store.close();
});

test('extractMessageText reads the shapes a real chat produces', () => {
  assert.equal(extractMessageText({ message: { conversation: 'plain' } }), 'plain');
  assert.equal(extractMessageText({ message: { extendedTextMessage: { text: 'reply' } } }), 'reply');
  assert.equal(extractMessageText({ message: { imageMessage: { caption: 'a photo' } } }), 'a photo');
  assert.equal(extractMessageText({ message: {} }), null);
  assert.equal(extractMessageText({}), null);
});

test('unwrapMessage peels every wrapper, up to three levels', () => {
  const inner = { imageMessage: { caption: 'x' } };
  for (const key of ['associatedChildMessage', 'ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2',
    'viewOnceMessageV2Extension', 'documentWithCaptionMessage', 'editedMessage']) {
    assert.equal(unwrapMessage({ [key]: { message: inner } }), inner, key);
  }
  const three = { ephemeralMessage: { message: { viewOnceMessage: { message: { associatedChildMessage: { message: inner } } } } } };
  assert.equal(unwrapMessage(three), inner);
  const plain = { conversation: 'hi' };
  assert.equal(unwrapMessage(plain), plain);
  assert.deepEqual(unwrapMessage(undefined), {});
});

test('extractMessageText finds captions and text inside wrappers', () => {
  assert.equal(extractMessageText({ message: { associatedChildMessage: { message: { imageMessage: { caption: 'album' } } } } }), 'album');
  assert.equal(extractMessageText({ message: { ephemeralMessage: { message: { conversation: 'vanishing' } } } }), 'vanishing');
  assert.equal(extractMessageText({ message: { documentWithCaptionMessage: { message: { documentMessage: { caption: 'doc' } } } } }), 'doc');
});

test('a group counts as known when it is in the archive chats, a stranger DM chat does not (MS-12)', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  store.ingestHistory({
    chats: [{ id: GROUP, name: 'Family Trip' }, { id: BOB, name: 'Bob who is not a contact' }],
  });
  assert.equal(store.isKnownGroup(GROUP), true);
  assert.equal(store.isKnownGroup('120363999999999999@g.us'), false);
  assert.equal(store.isKnownContact(ALICE), true);
  // A chat row exists for anyone who ever wrote, which must not make them known.
  assert.equal(store.isKnownContact(BOB), false);
  assert.equal(store.isKnownGroup(BOB), false);
  store.close();
});

test('counts reports contacts, chats and messages (AC-507 reads these after a link)', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  store.ingestHistory({
    chats: [{ id: GROUP, name: 'Family' }],
    messages: [msg(ALICE, 'C1', 'hello', 1700000000)],
  });
  // The message also creates a chat row for Alice, so chats is 2.
  assert.deepEqual(store.counts(), { contacts: 1, chats: 2, messages: 1 });
  store.close();
});

test('RV-20 (AC-606): known = an address-book name or the user\'s own message; never a push name or a Butler send', async () => {
  const { MARKER, newButlerId } = await import('../whatsapp-inbound.js');
  const store = openWhatsAppStore(tempDb());
  const CAROL = '15553330000@s.whatsapp.net';
  const DAN = '15554440000@s.whatsapp.net';
  const ERIN = '15555550000@s.whatsapp.net';
  const own = (jid, id, message) => ({ key: { remoteJid: jid, id, fromMe: true }, messageTimestamp: 1700000000, message });
  // Bob: only what Baileys emits for an inbound message (lib/Socket/chats.js:917-924).
  store.ingestContacts([{ id: BOB, notify: 'Bob B', verifiedName: 'Bob Biz' }]);
  store.ingestMessages([msg(BOB, 'B1', 'hello', 1700000000)]);
  assert.equal(store.isKnownContact(BOB), false, 'a push name is the sender\'s own claim');
  // A later push name never makes a saved contact unknown, nor a blank name known.
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  store.ingestContacts([{ id: ALICE, notify: 'Ali' }]);
  assert.equal(store.isKnownContact(ALICE), true);
  store.ingestContacts([{ id: CAROL, name: '  ' }]);
  assert.equal(store.isKnownContact(CAROL), false);
  // The user wrote to Dan themselves (a voice note: no text).
  store.ingestMessages([own(DAN, 'D1', { audioMessage: { ptt: true } })]);
  assert.equal(store.isKnownContact(DAN), true);
  // Butler wrote to Erin (a guest reply): its id prefix, or the marker on an older send.
  store.ingestMessages([own(ERIN, newButlerId(), { audioMessage: { ptt: true } })]);
  store.ingestMessages([own(ERIN, 'OLD1', { conversation: 'Butler: hi' + MARKER })]);
  assert.equal(store.isKnownContact(ERIN), false, 'a Butler send is not the user writing');
  // The name Bob chose for himself stays visible, in `notify`.
  assert.deepEqual(store.listContacts({ query: 'bob' }).map((c) => [c.name, c.notify]), [[null, 'Bob B']]);
  store.close();
});

// Baileys rc14 (lib/Utils/history.js:58) builds a history contact's `name` as
// `chat.displayName || chat.name || chat.username`; `username` and
// `displayName` are chosen by the OTHER person. The raw conversation, pushed
// intact into `payload.chats` (history.js:100), keeps the address-book `name`.
test('RV-20 (AC-606): a history contact whose name came only from username/displayName is not known', () => {
  const store = openWhatsAppStore(tempDb());
  const CAROL = '15553330000@s.whatsapp.net';
  store.ingestHistory({
    // What rc14 emits: contact.name is filled from username / displayName.
    contacts: [
      { id: ALICE, name: 'Alice Alvarez' },
      { id: BOB, name: 'bob_the_stranger', username: 'bob_the_stranger' },
      { id: CAROL, name: 'Carol Chosen' },
    ],
    // The raw conversations: Alice has an address-book name, Bob has only a
    // username, Carol only a displayName.
    chats: [
      { id: ALICE, name: 'Alice Alvarez', conversationTimestamp: 1700000000 },
      { id: BOB, username: 'bob_the_stranger', conversationTimestamp: 1700000000 },
      { id: CAROL, displayName: 'Carol Chosen', conversationTimestamp: 1700000000 },
    ],
  });
  assert.equal(store.isKnownContact(ALICE), true, 'the conversation has an address-book name');
  assert.equal(store.isKnownContact(BOB), false, 'a username is the other person\'s own claim');
  assert.equal(store.isKnownContact(CAROL), false, 'a displayName is the other person\'s own claim');
  store.close();
});

test('RV-20 (AC-606): an app-state contacts.upsert name still counts as known', () => {
  const store = openWhatsAppStore(tempDb());
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  assert.equal(store.isKnownContact(ALICE), true);
  store.close();
});
