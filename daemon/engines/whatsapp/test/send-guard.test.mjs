/**
 * The guards on outbound WhatsApp (ported from an earlier prototype, AC-512/AC-513/AC-514).
 *
 * These run on the SERVER, beside the socket, not in the agent tool. The tool
 * is one caller; a route, a task, or a future surface are others, and a guard
 * that lives in the caller is a guard the next caller does not have.
 *
 * The owner chose to run this on their PRIMARY number and to allow sending to
 * anyone in their address book. That is the widest of the options offered, so
 * these are the compensating controls: a kill switch, a rate limit, a refusal
 * to guess a name, and a confirmation the first time a given recipient comes
 * up. Every decision is appended to an audit file whether it sent or not --
 * a refusal nobody can see is indistinguishable from a message nobody sent.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { createSendGuard } from '../whatsapp-send-guard.js';
import { openWhatsAppStore } from '../whatsapp-store.js';

const ALICE = '15551230000@s.whatsapp.net';
const BOB = '15559876543@s.whatsapp.net';

/**
 * @param {object} [opts]
 * @param {object} [opts.settings] - the whatsapp_tools block
 * @param {(store: any) => void} [opts.seed]
 * @param {() => string[]} [opts.getOwnJids]
 */
function harness(opts = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-send-'));
  const store = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  (opts.seed || ((s) => s.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }])))(store);
  const auditPath = path.join(dir, 'whatsapp-sent.jsonl');
  let clock = 1_700_000_000_000;
  const guard = createSendGuard({
    store,
    auditPath,
    readSettings: () => ({ send_enabled: true, send_rate_per_minute: 3, ...(opts.settings || {}) }),
    now: () => clock,
    ...(opts.getOwnJids ? { getOwnJids: opts.getOwnJids } : {}),
  });
  return {
    guard,
    store,
    auditPath,
    tick: (ms) => { clock += ms; },
    audit: () => fs.readFileSync(auditPath, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)),
  };
}

test('sending is refused outright while the kill switch is off', () => {
  const h = harness({ settings: { send_enabled: false } });
  const verdict = h.guard.check({ recipient: 'Alice', confirm: true });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.reason, 'send_disabled');
});

test('THE KILL SWITCH IS RE-READ ON EVERY CALL, not captured at construction', () => {
  const h = harness();
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }).ok, true);
  // Switching it off mid-session has to take effect without a restart, or the
  // control is a lie at exactly the moment someone reaches for it.
  h.guard._setSettingsForTest({ send_enabled: false, send_rate_per_minute: 3 });
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }).reason, 'send_disabled');
});

test('a recipient who is not in the address book is refused', () => {
  const h = harness();
  const verdict = h.guard.check({ recipient: '+15550009999', confirm: true });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.reason, 'unknown_recipient');
  assert.equal(verdict.jid, null);
});

test('AN AMBIGUOUS NAME IS REFUSED WITH ITS CANDIDATES, never resolved to one', () => {
  const h = harness({
    seed: (s) => s.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }, { id: BOB, name: 'Alice Bertrand' }]),
  });
  const verdict = h.guard.check({ recipient: 'Alice', confirm: true });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.reason, 'ambiguous_recipient');
  assert.equal(verdict.candidates.length, 2);
});

test('the first message to a recipient needs confirmation; the next does not', () => {
  const h = harness();
  const first = h.guard.check({ recipient: 'Alice' });
  assert.equal(first.ok, false);
  assert.equal(first.reason, 'confirmation_required');
  // The resolved identity comes back WITH the refusal, so the caller can show
  // the owner exactly who it was about to message.
  assert.equal(first.jid, ALICE);
  assert.equal(first.name, 'Alice Alvarez');

  const confirmed = h.guard.check({ recipient: 'Alice', confirm: true });
  assert.equal(confirmed.ok, true);
  h.guard.record(confirmed);

  const later = h.guard.check({ recipient: 'Alice' });
  assert.equal(later.ok, true, 'a recipient already messaged this session should not re-prompt');
});

test('confirmation is remembered per RECIPIENT, not globally', () => {
  const h = harness({
    seed: (s) => s.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }, { id: BOB, name: 'Bob Brown' }]),
  });
  h.guard.record(h.guard.check({ recipient: 'Alice', confirm: true }));
  // Confirming Alice must not silently authorise Bob.
  assert.equal(h.guard.check({ recipient: 'Bob' }).reason, 'confirmation_required');
});

test('the rate limit refuses the message past the ceiling', () => {
  const h = harness();
  for (let i = 0; i < 3; i++) {
    const v = h.guard.check({ recipient: 'Alice', confirm: true });
    assert.equal(v.ok, true, `send ${i + 1} should be allowed`);
    h.guard.record(v);
  }
  const over = h.guard.check({ recipient: 'Alice', confirm: true });
  assert.equal(over.ok, false);
  assert.equal(over.reason, 'rate_limited');
});

test('the rate limit is a rolling window, not a permanent cap', () => {
  const h = harness();
  for (let i = 0; i < 3; i++) h.guard.record(h.guard.check({ recipient: 'Alice', confirm: true }));
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }).reason, 'rate_limited');
  h.tick(61_000);
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }).ok, true, 'the window must expire');
});

test('ONLY RECORDED SENDS COUNT AGAINST THE LIMIT', () => {
  const h = harness();
  // A refused or never-attempted check must not consume quota, or a chatty
  // agent could rate-limit the owner out of their own account without ever
  // sending anything.
  for (let i = 0; i < 10; i++) h.guard.check({ recipient: 'Alice', confirm: true });
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }).ok, true);
});

test('every decision is audited, refusals included', () => {
  const h = harness();
  h.guard.record(h.guard.check({ recipient: 'Alice', confirm: true }), { text: 'hello there' });
  h.guard.record(h.guard.check({ recipient: '+15550009999', confirm: true }), { text: 'to a stranger' });

  const lines = h.audit();
  assert.equal(lines.length, 2);
  assert.equal(lines[0].ok, true);
  assert.equal(lines[0].jid, ALICE);
  assert.equal(lines[1].ok, false);
  assert.equal(lines[1].reason, 'unknown_recipient');
});

test('the audit records the message length, NOT the message', () => {
  const h = harness();
  h.guard.record(h.guard.check({ recipient: 'Alice', confirm: true }), { text: 'a private thing' });
  const line = h.audit()[0];
  // The archive already holds the conversation; a second plaintext copy in a
  // log nobody prunes is a gratuitous place for it to leak from.
  assert.equal(line.textLength, 15);
  assert.equal(line.text, undefined);
});

const OWN_PHONE = '15555550100@s.whatsapp.net';
const OWN_LID = '45200000000001@lid';
const own = () => [OWN_PHONE, OWN_LID];

test("THE OWNER'S OWN PHONE JID NEEDS NO CONFIRMATION", () => {
  // Messaging yourself is the attachment path's main use; a confirm round-trip
  // to reach your own chat protects nobody. Self has no contact row either,
  // and is not the stranger the address-book rule exists to stop.
  const h = harness({ getOwnJids: own });
  const v = h.guard.check({ recipient: OWN_PHONE });
  assert.equal(v.ok, true);
  assert.equal(v.jid, OWN_PHONE);
});

test("the owner's own LID needs no confirmation either", () => {
  const h = harness({ getOwnJids: own });
  assert.equal(h.guard.check({ recipient: OWN_LID }).ok, true);
});

test("THE KILL SWITCH STILL REFUSES THE OWNER'S OWN CHAT", () => {
  const h = harness({ settings: { send_enabled: false }, getOwnJids: own });
  assert.equal(h.guard.check({ recipient: OWN_PHONE }).reason, 'send_disabled');
});

test('sends to self still count against the rate limit', () => {
  const h = harness({ getOwnJids: own });
  for (let i = 0; i < 3; i++) h.guard.record(h.guard.check({ recipient: OWN_PHONE }));
  assert.equal(h.guard.check({ recipient: OWN_PHONE }).reason, 'rate_limited');
});

test('knowing the own jids does not pre-confirm anyone else', () => {
  const h = harness({ getOwnJids: own });
  assert.equal(h.guard.check({ recipient: 'Alice' }).reason, 'confirmation_required');
  assert.equal(h.guard.check({ recipient: '+15550009999', confirm: true }).reason, 'unknown_recipient');
});

test("the audit records a file's kind, name and size, never its bytes", () => {
  const h = harness();
  h.guard.record(h.guard.check({ recipient: 'Alice', confirm: true }),
    { kind: 'image', fileName: 'photo.jpg', bytes: 1234, text: 'a caption' });
  const line = h.audit()[0];
  assert.equal(line.kind, 'image');
  assert.equal(line.fileName, 'photo.jpg');
  assert.equal(line.bytes, 1234);
  assert.equal(line.textLength, 9);
  assert.equal(line.text, undefined);
});

// ---- Butler differences (MS-11, MS-12) ----

const GROUP = '120363000000000000@g.us';

test('A GROUP IN THE ARCHIVE IS A VALID RECIPIENT, by jid and by name (MS-12)', () => {
  const h = harness({
    seed: (s) => s.ingestHistory({ chats: [{ id: GROUP, name: 'Family Trip' }] }),
  });
  const byJid = h.guard.check({ recipient: GROUP, confirm: true });
  assert.equal(byJid.ok, true);
  assert.equal(byJid.jid, GROUP);
  const byName = h.guard.check({ recipient: 'family trip', confirm: true });
  assert.equal(byName.ok, true);
  assert.equal(byName.jid, GROUP);
});

test('a group that is NOT in the archive is refused like any stranger', () => {
  const h = harness();
  const v = h.guard.check({ recipient: '120363111111111111@g.us', confirm: true });
  assert.equal(v.ok, false);
  assert.equal(v.reason, 'unknown_recipient');
});

test('a group passes through the same guard: confirmation, rate limit and switch all apply', () => {
  const h = harness({ seed: (s) => s.ingestHistory({ chats: [{ id: GROUP, name: 'Family Trip' }] }) });
  assert.equal(h.guard.check({ recipient: GROUP }).reason, 'confirmation_required');
  for (let i = 0; i < 3; i++) h.guard.record(h.guard.check({ recipient: GROUP, confirm: true }));
  assert.equal(h.guard.check({ recipient: GROUP, confirm: true }).reason, 'rate_limited');
  assert.equal(h.guard.check({ recipient: GROUP, confirm: true }, { send_enabled: false }).reason, 'send_disabled');
});

test('a chat row for someone who merely wrote does not make them a known recipient', () => {
  const h = harness({
    seed: (s) => s.ingestMessages([{
      key: { remoteJid: '15557770000@s.whatsapp.net', id: 'X', fromMe: false },
      messageTimestamp: 1700000000,
      message: { conversation: 'hi, wrong number' },
    }]),
  });
  assert.equal(h.guard.check({ recipient: '+15557770000', confirm: true }).reason, 'unknown_recipient');
});

test('the per-request switch overrides the stored settings on every call (MS-12)', () => {
  const h = harness({ settings: { send_enabled: true } });
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }, { send_enabled: false }).reason, 'send_disabled');
  assert.equal(h.guard.check({ recipient: 'Alice', confirm: true }, { send_enabled: true }).ok, true);
});

// RV-20 (AC-606, F-3): Baileys (rc.9, unchanged in rc14) emits `contacts.update {id, notify: pushName}`
// for EVERY inbound message (lib/Socket/chats.js:917-924), group members
// included, so "a contact row exists" meant "ever wrote". Known is now a saved
// (address-book) name, or a chat the user wrote in themselves.
test('RV-20: a push name alone is not a known recipient; a saved name or the user\'s own message is', () => {
  const CAROL = '15553330000@s.whatsapp.net';
  const DAVE = '15554440000@s.whatsapp.net';
  const h = harness({
    seed: (s) => {
      s.ingestContacts([{ id: BOB, notify: 'Bob B' }]);                 // wrote once
      s.ingestContacts([{ id: DAVE, verifiedName: 'Dave Biz LLC' }]);  // a business wrote
      s.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);         // saved
      s.ingestMessages([{                                               // the user wrote to Carol
        key: { remoteJid: CAROL, id: 'U1', fromMe: true },
        messageTimestamp: 1700000000,
        message: { conversation: 'see you at 6' },
      }]);
    },
  });
  assert.equal(h.guard.check({ recipient: BOB, confirm: true }).reason, 'unknown_recipient');
  assert.equal(h.guard.check({ recipient: DAVE, confirm: true }).reason, 'unknown_recipient');
  assert.equal(h.guard.check({ recipient: ALICE, confirm: true }).ok, true);
  assert.equal(h.guard.check({ recipient: CAROL, confirm: true }).ok, true);
});
