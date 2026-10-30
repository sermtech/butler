/**
 * The inbound filter, driven through the socket the way Baileys drives it
 * (ported from an earlier prototype's whatsapp-self-chat test; AC-517, AC-518, AC-523,
 * AC-535, AC-538).
 *
 * Self chat: the user talks to Butler from their OWN "Message yourself" chat,
 * because the linked device IS the user's number. Everything typed on the phone
 * arrives `fromMe`, so the exception must be exactly one chat wide. The bot's
 * own replies cannot loop: Baileys emits locally-sent messages as `append`.
 *
 * New here: events are TAGGED (owner / guest), a `takeover` event reports the
 * human typing in a guest chat, a group needs the trigger word or a reply to
 * Butler (a mention of the user is not a trigger), and anything ending with the
 * marker U+2063 is never routed (MS-15).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { openWhatsAppStore } from '../whatsapp-store.js';
import { createWhatsApp } from '../whatsapp-socket.js';
import { MARKER, MAX_INBOUND_BYTES, newButlerId } from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, silent, upsert, ME, SELF_LID, SELF_PN, ALICE, BOB, GROUP } from './fakes.mjs';

async function boot(config, fakeOpts) {
  const dir = tempDir('wa-inbound-');
  const store = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  const fake = fakeBaileys(fakeOpts);
  const events = [];
  const wa = createWhatsApp({ dir, store, emit: (e) => events.push(e), baileys: fake.deps, log: silent });
  if (config) wa.setConfig(config);
  await wa.startPairing();
  const emit = (payload) => fake.last().emit('messages.upsert', payload);
  return { fake, wa, events, store, emit };
}

const mine = (jid, message, o = {}) => upsert(jid, message, { fromMe: true, ...o });

// ---- the user's own chat (an earlier prototype self-chat tests) ----

test('a message typed in my own chat (LID form) is routed as an OWNER item', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { conversation: 'what is on my calendar' }, { id: 'A1', pushName: 'Me' }));
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'message');
  assert.equal(events[0].tag, 'owner');
  assert.equal(events[0].text, 'what is on my calendar');
  assert.equal(events[0].jid, SELF_LID, 'the reply must go back to the chat it came from');
  assert.equal(events[0].id, 'A1');
  wa.stop();
});

test('the phone-number form of my own chat counts too', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_PN, { conversation: 'hi' }));
  assert.equal(events.length, 1);
  assert.equal(events[0].tag, 'owner');
  wa.stop();
});

test('my messages in ANY other chat are never routed as owner turns', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(ALICE, { conversation: 'to a friend' }));
  emit(mine('15555550100-1500000000@g.us', { conversation: 'to my own group' }));
  assert.equal(events.length, 0);
  wa.stop();
});

test("the bot's own sends (type append) never re-enter, and offline deliveries are not answered", async () => {
  const { emit, events, wa } = await boot();
  // Butler's own reply echoes back as `append`, carrying the marker (MS-15).
  emit(mine(SELF_LID, { conversation: 'agent reply' + MARKER }, { type: 'append' }));
  emit(upsert(ALICE, { conversation: 'queued while down' }, { type: 'append' }));
  assert.equal(events.length, 0);
  // The user's own unmarked message written BEFORE this sidecar started is
  // COUNTED (MS-19), never routed as a turn. The label alone decides
  // nothing: docs/messaging.md 6.4, delivery.test.mjs.
  const anHourAgo = Math.floor(Date.now() / 1000) - 3600;
  emit(mine(SELF_LID, { conversation: 'written while down' }, { type: 'append', ts: anHourAgo }));
  assert.deepEqual(events.map((e) => e.type), ['missed']);
  wa.stop();
});

test('the own chat is matched on the server part: a group id starting with my digits is not my chat', async () => {
  const { emit, events, wa } = await boot();
  emit(mine('15555550100@g.us', { conversation: 'not my chat' }));
  assert.equal(events.length, 0);
  wa.stop();
});

// ---- MS-15: the cross-talk marker ----

test('an owner reply names the message it QUOTES, so a question can be answered by quoting it (MS-22)', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { extendedTextMessage: { text: '2', contextInfo: { stanzaId: 'R7', participant: SELF_PN, quotedMessage: { conversation: 'Butler needs you' } } } }, { id: 'Q1' }));
  emit(mine(SELF_LID, { conversation: 'not a reply' }, { id: 'Q2' }));
  assert.deepEqual(events.map((e) => [e.id, e.text, e.quoted]), [['Q1', '2', 'R7'], ['Q2', 'not a reply', null]]);
  wa.stop();
});

test('AN INBOUND SELF-CHAT MESSAGE ENDING WITH THE MARKER IS ARCHIVED BUT NOT ROUTED (MS-15)', async () => {
  const { emit, events, store, wa } = await boot();
  emit(mine(SELF_LID, { conversation: 'another Butler said this' + MARKER }, { id: 'MARKED' }));
  assert.equal(events.length, 0, 'two agents on one number would answer each other forever');
  assert.equal(store.readChat(SELF_LID).length, 1, 'but the archive keeps every message');
  emit(mine(SELF_LID, { conversation: 'the human said this' }));
  assert.equal(events.length, 1, 'an unmarked message still routes');
  wa.stop();
});

test('a marker only counts at the END of the text', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { conversation: 'a' + MARKER + 'b' }));
  assert.equal(events.length, 1);
  wa.stop();
});

test('a captioned file whose caption ends with the marker is not routed either', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { imageMessage: { caption: 'chart' + MARKER, mimetype: 'image/png' } }));
  assert.equal(events.length, 0);
  wa.stop();
});

// ---- guests: DMs (MS-14a) ----

test('with the default empty answer list nobody else is answered (AC-517)', async () => {
  const { emit, events, wa } = await boot();
  emit(upsert(ALICE, { conversation: 'hello butler' }));
  emit(upsert(GROUP, { conversation: 'Butler, hello' }, { participant: ALICE }));
  assert.equal(events.length, 0);
  wa.stop();
});

test('a listed DM is answered on EVERY message, tagged guest', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(upsert(ALICE, { conversation: 'no trigger word needed' }, { pushName: 'Alice' }));
  assert.equal(events.length, 1);
  assert.equal(events[0].tag, 'guest');
  assert.equal(events[0].jid, ALICE);
  assert.equal(events[0].pushName, 'Alice');
  emit(upsert(BOB, { conversation: 'but Bob is not listed' }));
  assert.equal(events.length, 1);
  wa.stop();
});

test('a listed person is matched by bare jid, device suffix and all', async () => {
  const { emit, events, wa } = await boot({ answer: ['15551230000:12@s.whatsapp.net'] });
  emit(upsert(ALICE, { conversation: 'hi' }));
  assert.equal(events.length, 1);
  wa.stop();
});

test('"answer every saved contact" answers a saved contact and nobody else', async () => {
  const { emit, events, store, wa } = await boot({ answer_everyone: true });
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  emit(upsert(ALICE, { conversation: 'a saved contact writes' }));
  emit(upsert(BOB, { conversation: 'a stranger writes' }));
  assert.deepEqual(events.map((e) => e.jid), [ALICE]);
  wa.stop();
});

test('a voice note from a listed person is routed with its media descriptor', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(upsert(ALICE, { audioMessage: { ptt: true, seconds: 3, mimetype: 'audio/ogg; codecs=opus', fileLength: 4096 } }));
  assert.equal(events.length, 1);
  const m = events[0].media;
  assert.equal(m.kind, 'audio');
  assert.equal(m.ptt, true);
  assert.equal(m.seconds, 3);
  assert.equal(m.bytes, 4096);
  assert.equal(m.tooLarge, false);
  assert.match(m.id, /^[0-9a-f-]{36}$/);
  wa.stop();
});

// ---- guests: groups (MS-14a) ----

test('IN A LISTED GROUP, ONLY THE TRIGGER WORD AT THE START ANSWERS', async () => {
  const { emit, events, wa } = await boot({ answer: [GROUP] });
  const say = (text, participant = ALICE) => emit(upsert(GROUP, { conversation: text }, { participant }));
  say('just chatting about dinner');
  say('can someone ask Butler about it');
  say('Butlerian jihad is a book');
  assert.equal(events.length, 0);
  say('Butler, what time is it?');
  assert.equal(events.length, 1);
  assert.equal(events[0].tag, 'guest');
  assert.equal(events[0].trigger, 'word');
  assert.equal(events[0].text, 'what time is it?', 'the trigger word is stripped from the text');
  assert.equal(events[0].sender, ALICE, 'the group member who wrote it');
  assert.equal(events[0].isGroup, true);
  say('  butler: lower case works');
  assert.equal(events[1].text, 'lower case works');
  wa.stop();
});

test('RV-24 (AC-611): an apostrophe is part of the word, ASCII or typographic', async () => {
  const { emit, events, wa } = await boot({ answer: [GROUP] });
  const say = (text) => emit(upsert(GROUP, { conversation: text }, { participant: ALICE }));
  say("Butler's car is outside");
  say('Butler\u2019s car is outside');
  say("butler's turn");
  assert.equal(events.length, 0, 'talking ABOUT Butler is not a trigger');
  say('Butler, what time is it?');
  say('butler what is the plan');
  assert.deepEqual(events.map((e) => e.text), ['what time is it?', 'what is the plan']);
  wa.stop();
});

test('RV-20 (AC-606): answer-everyone ignores a push name alone; a saved name or a chat the user wrote in counts', async () => {
  const CAROL = '15553330000@s.whatsapp.net';
  const { emit, events, store, wa } = await boot({ answer_everyone: true });
  // What Baileys emits for every inbound message (lib/Socket/chats.js:917-924).
  store.ingestContacts([{ id: BOB, notify: 'Bob (his own push name)' }]);
  store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  // Carol has no name at all, but the user wrote to her themselves.
  store.ingestMessages(mine(CAROL, { conversation: 'see you at 6' }, { id: 'U1' }).messages);
  emit(upsert(BOB, { conversation: 'hi, I wrote to you once' }));
  emit(upsert(ALICE, { conversation: 'a saved contact writes' }));
  emit(upsert(CAROL, { conversation: 'the user wrote to me' }));
  assert.deepEqual(events.map((e) => e.jid), [ALICE, CAROL]);
  wa.stop();
});

test('the trigger word is a setting', async () => {
  const { emit, events, wa } = await boot({ answer: [GROUP], trigger: 'Jarvis' });
  emit(upsert(GROUP, { conversation: 'Butler hello' }, { participant: ALICE }));
  assert.equal(events.length, 0);
  emit(upsert(GROUP, { conversation: 'jarvis hello' }, { participant: ALICE }));
  assert.equal(events.length, 1);
  wa.stop();
});

test('A MENTION OF THE USER IS NOT A TRIGGER: in a group that names the human', async () => {
  const { emit, events, wa } = await boot({ answer: [GROUP] });
  emit(upsert(GROUP, {
    extendedTextMessage: { text: '@15555550100 are you coming?', contextInfo: { mentionedJid: [ME.id, SELF_PN] } },
  }, { participant: ALICE }));
  assert.equal(events.length, 0);
  wa.stop();
});

test('A REPLY TO ONE OF BUTLER\'S OWN MESSAGES IS A TRIGGER; a reply to the human is not', async () => {
  const { emit, events, wa } = await boot({ answer: [GROUP] });
  const replyTo = (quotedText) => emit(upsert(GROUP, {
    extendedTextMessage: {
      text: 'and what about tomorrow?',
      contextInfo: { participant: ME.id, quotedMessage: { conversation: quotedText } },
    },
  }, { participant: ALICE }));
  // Butler sends AS the user: only the marker tells its words from the human's.
  replyTo('I will be there at five' );
  assert.equal(events.length, 0, 'the quoted message is the human user, not Butler');
  replyTo('Butler: it is five o\'clock' + MARKER);
  assert.equal(events.length, 1);
  assert.equal(events[0].trigger, 'reply');
  assert.equal(events[0].text, 'and what about tomorrow?');
  wa.stop();
});

test('a reply to Butler in a group that is NOT listed is ignored', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(upsert(GROUP, {
    extendedTextMessage: { text: 'hm', contextInfo: { participant: ME.id, quotedMessage: { conversation: 'x' + MARKER } } },
  }, { participant: BOB }));
  assert.equal(events.length, 0);
  wa.stop();
});

// ---- takeover (MS-14c) ----

test('THE USER TYPING IN A GUEST CHAT EMITS A TAKEOVER, and is never answered (MS-14c)', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE, GROUP] });
  emit(mine(ALICE, { conversation: 'I will take this one' }));
  assert.deepEqual(events.map((e) => e.type), ['takeover']);
  assert.equal(events[0].jid, ALICE);
  emit(mine(GROUP, { conversation: 'me again' }));
  assert.equal(events.length, 2);
  assert.equal(events[1].jid, GROUP);
  wa.stop();
});

test('a takeover needs the user\'s own NON-marker message: Butler\'s own text in a guest chat is not one', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(mine(ALICE, { conversation: 'Butler: sure' + MARKER }));
  assert.equal(events.length, 0);
  wa.stop();
});

test('the user typing in a chat that is NOT a guest chat is nothing at all', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(mine(BOB, { conversation: 'chatting with Bob' }));
  assert.equal(events.length, 0);
  wa.stop();
});

test('RV-23: in ONE append batch, Butler\'s own echo is not a takeover and the user\'s message behind it IS', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  // Baileys flushes a buffered batch under the FIRST message's label: here
  // Butler's own guest reply (its id prefix, RV-12) and then the human.
  const echo = mine(ALICE, { conversation: 'Butler: sure' }, { id: newButlerId() }).messages[0];
  const human = mine(ALICE, { conversation: 'I will take it from here' }, { id: 'HUMAN1' }).messages[0];
  emit({ type: 'append', messages: [echo, human] });
  assert.deepEqual(events.map((e) => [e.type, e.jid, e.id]), [['takeover', ALICE, 'HUMAN1']]);
  // A redelivery of the same takeover is dropped by the routed table (RV-4).
  emit({ type: 'append', messages: [human] });
  assert.equal(events.length, 1);
  wa.stop();
});

test('RV-23: the user\'s message in a guest chat written BEFORE the sidecar started is not a takeover', async () => {
  const { emit, events, wa } = await boot({ answer: [ALICE] });
  emit(mine(ALICE, { conversation: 'old' }, { ts: Math.floor(Date.now() / 1000) - 3600 }));
  assert.equal(events.length, 0);
  wa.stop();
});

// ---- media (an earlier prototype attachments tests; the daemon downloads, AC-523) ----

test('a captioned image is routed with its caption as the text', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { imageMessage: { caption: 'what plant is this?', mimetype: 'image/png' } }));
  assert.equal(events[0].text, 'what plant is this?');
  assert.equal(events[0].media.kind, 'image');
  assert.equal(events[0].media.mimetype, 'image/png');
  wa.stop();
});

test('an album child (associatedChildMessage) is classified as the image it is, and downloads unwrapped', async () => {
  const { emit, events, fake, wa } = await boot();
  emit(mine(SELF_LID, { associatedChildMessage: { message: { imageMessage: { mimetype: 'image/jpeg' } } } }));
  assert.equal(events.length, 1);
  assert.equal(events[0].media.kind, 'image');
  const file = await wa.download(events[0].media.id);
  assert.equal(file.kind, 'image');
  assert.ok(fake.downloads[0].message.imageMessage, 'download must get the unwrapped message under .message');
  assert.ok(fake.downloads[0].key, 'download must still get the key');
  wa.stop();
});

test('the albumMessage parent carries no media and is ignored', async () => {
  const { emit, events, wa } = await boot();
  emit(mine(SELF_LID, { albumMessage: { expectedImageCount: 3 } }));
  assert.equal(events.length, 0);
  wa.stop();
});

test('a document keeps its name and type for the daemon', async () => {
  const { emit, events, wa } = await boot();
  const mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  emit(mine(SELF_LID, { documentMessage: { fileName: 'budget.xlsx', mimetype, caption: 'check this' } }));
  assert.equal(events[0].media.kind, 'document');
  assert.equal(events[0].media.fileName, 'budget.xlsx');
  assert.equal(events[0].text, 'check this');
  wa.stop();
});

test('a 150 MB document is flagged tooLarge and is NEVER downloaded (MS-18)', async () => {
  const { emit, events, fake, wa } = await boot();
  emit(mine(SELF_LID, { documentMessage: { fileName: 'big.zip', mimetype: 'application/zip', fileLength: 150 * 1024 * 1024 } }));
  assert.equal(events[0].media.tooLarge, true);
  assert.equal(events[0].media.bytes, 150 * 1024 * 1024);
  await assert.rejects(wa.download(events[0].media.id), (e) => e.code === 'too_large' && e.bytes === 150 * 1024 * 1024);
  assert.equal(fake.downloads.length, 0, 'the size is judged from the declared length, before any download');
  wa.stop();
});

test('fileLength as a string or a protobuf Long is read for the size cap', async () => {
  const { emit, events, wa } = await boot();
  const big = MAX_INBOUND_BYTES + 1;
  emit(mine(SELF_LID, { videoMessage: { fileLength: String(big) } }));
  emit(mine(SELF_LID, { videoMessage: { fileLength: { toNumber: () => big } } }));
  assert.deepEqual(events.map((e) => e.media.tooLarge), [true, true]);
  wa.stop();
});

test('a media id that was never issued is unknown, not a crash', async () => {
  const { wa } = await boot();
  await assert.rejects(wa.download('nope'), (e) => e.code === 'unknown_media');
  wa.stop();
});

// ---- config validation (the daemon owns the settings) ----

test('POST /config values are validated: a malformed list must not quietly change who is answered', async () => {
  const { wa } = await boot();
  assert.throws(() => wa.setConfig({ answer: 'alice' }), /answer/);
  assert.throws(() => wa.setConfig({ answer: ['not-a-jid'] }), /answer/);
  assert.throws(() => wa.setConfig({ answer_everyone: 'yes' }), /answer_everyone/);
  assert.throws(() => wa.setConfig({ trigger: '  ' }), /trigger/);
  assert.throws(() => wa.setConfig({ send_rate_per_minute: 0 }), /send_rate/);
  assert.deepEqual(wa.getConfig().answer, [], 'a refused config changes nothing');
  wa.stop();
});

test('a partial config keeps the other settings', async () => {
  const { wa } = await boot({ answer: [ALICE], trigger: 'Jarvis' });
  wa.setConfig({ answer_everyone: true });
  assert.deepEqual(wa.getConfig().answer, [ALICE]);
  assert.equal(wa.getConfig().trigger, 'Jarvis');
  assert.equal(wa.getConfig().answer_everyone, true);
  wa.stop();
});
