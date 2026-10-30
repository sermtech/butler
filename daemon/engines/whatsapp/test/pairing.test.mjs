import { test } from 'node:test';
import assert from 'node:assert';
import {
  IDLE_PAIRING,
  reducePairing,
  describePairing,
  phoneFromJid,
  jidDigits,
  PASSKEY_HINT_QR_COUNT,
} from '../whatsapp-pairing.js';

/**
 * The pairing state machine behind the settings page's WhatsApp pairing block (ported from an earlier prototype, AC-503).
 *
 * It is pure on purpose. The thing it reports on -- whether a companion device
 * can be linked at all -- cannot be exercised in a test: it depends on Meta's
 * servers, on the account, and (since the mid-2026 passkey rollout) on whether
 * that account has been moved onto a WebAuthn-gated linking flow no headless
 * client can satisfy. So the machine owns every decision that does NOT need the
 * network, and the socket adapter owns nothing but event forwarding.
 *
 * THE RULE THIS FILE EXISTS TO PIN: the panel must never CLAIM the passkey gate.
 * We cannot detect it -- a gated account and a phone left on the kitchen table
 * produce the same observable (QR after QR, connection never opens). Reporting
 * "blocked by passkey" would be inventing a diagnosis from an absence. What we
 * may do is say the QR was reissued N times and name the passkey gate as one
 * explanation the user can check on their own phone.
 */

const NOW = 1_760_000_000_000;

test('a fresh state is idle with nothing to show', () => {
  assert.deepStrictEqual(IDLE_PAIRING, {
    status: 'idle',
    qr: null,
    qrCount: 0,
    phone: null,
    name: null,
    error: null,
    lastDisconnectCode: null,
    startedAt: null,
    qrAt: null,
  });
});

test('start moves to connecting and clears the previous attempt', () => {
  const failed = reducePairing(IDLE_PAIRING, { type: 'error', message: 'boom' }, NOW);
  const next = reducePairing(failed, { type: 'start' }, NOW);

  assert.strictEqual(next.status, 'connecting');
  assert.strictEqual(next.error, null, 'a new attempt must not show the old attempt failure');
  assert.strictEqual(next.qr, null);
  assert.strictEqual(next.qrCount, 0);
  assert.strictEqual(next.startedAt, NOW);
});

test('each QR is counted, because the count is the only signal we have', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  s = reducePairing(s, { type: 'qr', qr: 'qr-one' }, NOW + 1000);

  assert.strictEqual(s.status, 'awaiting_scan');
  assert.strictEqual(s.qr, 'qr-one');
  assert.strictEqual(s.qrCount, 1);
  assert.strictEqual(s.qrAt, NOW + 1000);

  s = reducePairing(s, { type: 'qr', qr: 'qr-two' }, NOW + 20000);
  assert.strictEqual(s.qr, 'qr-two', 'the panel must show the CURRENT code, not the expired one');
  assert.strictEqual(s.qrCount, 2);
});

test('open pairs the session and drops the QR', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  s = reducePairing(s, { type: 'qr', qr: 'qr-one' }, NOW);
  s = reducePairing(s, { type: 'open', id: '15551234567:22@s.whatsapp.net', name: 'Owner' }, NOW);

  assert.strictEqual(s.status, 'paired');
  assert.strictEqual(s.phone, '+15551234567');
  assert.strictEqual(s.name, 'Owner');
  assert.strictEqual(s.qr, null, 'a scanned code must not stay on screen');
});

test('a logged-out close is distinct from an ordinary drop', () => {
  const start = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);

  const droppedOut = reducePairing(start, { type: 'close', statusCode: 401, loggedOut: true }, NOW);
  assert.strictEqual(droppedOut.status, 'logged_out');
  assert.strictEqual(droppedOut.lastDisconnectCode, 401);

  const dropped = reducePairing(start, { type: 'close', statusCode: 428, loggedOut: false }, NOW);
  assert.strictEqual(dropped.status, 'disconnected');
  assert.strictEqual(dropped.lastDisconnectCode, 428);
});

test('stop returns to idle', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  s = reducePairing(s, { type: 'qr', qr: 'qr-one' }, NOW);
  assert.deepStrictEqual(reducePairing(s, { type: 'stop' }, NOW), IDLE_PAIRING);
});

test('the reducer never mutates the state it was handed', () => {
  const before = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  const snapshot = { ...before };
  reducePairing(before, { type: 'qr', qr: 'qr-one' }, NOW);
  assert.deepStrictEqual(before, snapshot);
});

test('an unknown event throws instead of silently doing nothing', () => {
  assert.throws(
    () => reducePairing(IDLE_PAIRING, { type: 'teleport' }, NOW),
    /teleport/,
    'fail fast: a typo in an event name must not read as a no-op transition'
  );
});

test('jidDigits drops the device suffix -- the mention stripper builds a RegExp from it', () => {
  // `@15551234567:22` would never match a real mention, so the bot's own @tag
  // would survive into the prompt and the agent would answer its own name.
  assert.strictEqual(jidDigits('15551234567:22@s.whatsapp.net'), '15551234567');
  assert.strictEqual(jidDigits('15551234567@s.whatsapp.net'), '15551234567');
  assert.strictEqual(jidDigits(undefined), '');
});

test('phoneFromJid strips the device suffix and the domain', () => {
  assert.strictEqual(phoneFromJid('15551234567:22@s.whatsapp.net'), '+15551234567');
  assert.strictEqual(phoneFromJid('15551234567@s.whatsapp.net'), '+15551234567');
  assert.strictEqual(phoneFromJid(''), null);
  assert.strictEqual(phoneFromJid(null), null);
});

test('one unscanned QR does NOT accuse the passkey gate', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  s = reducePairing(s, { type: 'qr', qr: 'qr-one' }, NOW);

  const view = describePairing(s);
  assert.strictEqual(view.tone, 'neutral');
  assert.doesNotMatch(
    view.headline + view.detail,
    /passkey/i,
    'the ordinary case -- a code waiting to be scanned -- must read as normal'
  );
});

test('repeated QRs RAISE the passkey gate as a possibility, never as a finding', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  for (let i = 0; i < PASSKEY_HINT_QR_COUNT; i++) {
    s = reducePairing(s, { type: 'qr', qr: `qr-${i}` }, NOW);
  }

  const view = describePairing(s);
  const text = view.headline + ' ' + view.detail;
  assert.match(text, /passkey/i, 'after repeated reissues the user needs to know this gate exists');
  assert.match(text, /if|may|might|check/i, 'it must be phrased as something to check, not something we measured');
  assert.doesNotMatch(text, /\bis blocked\b|\bblocked by\b/i, 'we cannot observe the gate, so we must not assert it');
});

test('paired reports success and names the number that got linked', () => {
  let s = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  s = reducePairing(s, { type: 'open', id: '15551234567@s.whatsapp.net', name: 'Owner' }, NOW);

  const view = describePairing(s);
  assert.strictEqual(view.tone, 'success');
  assert.match(view.headline + view.detail, /\+15551234567/);
});

test('an error is reported as an error, with its message intact', () => {
  const s = reducePairing(IDLE_PAIRING, { type: 'error', message: 'ENOTFOUND web.whatsapp.com' }, NOW);
  const view = describePairing(s);
  assert.strictEqual(view.tone, 'error');
  assert.match(view.detail, /ENOTFOUND web\.whatsapp\.com/);
});

test('a 440 close (session opened elsewhere) is terminal and says so (MS-6)', () => {
  const start = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  const replaced = reducePairing(start, { type: 'close', statusCode: 440, loggedOut: false, replaced: true }, NOW);
  assert.strictEqual(replaced.status, 'replaced');
  assert.strictEqual(replaced.lastDisconnectCode, 440);
  const view = describePairing(replaced);
  assert.match(view.detail, /open in another program/i);
  assert.strictEqual(view.tone, 'warning');
});

test('a 401 close asks the user to link again (MS-6)', () => {
  const start = reducePairing(IDLE_PAIRING, { type: 'start' }, NOW);
  const out = reducePairing(start, { type: 'close', statusCode: 401, loggedOut: true }, NOW);
  assert.match(describePairing(out).detail, /Unlinked from the phone: link again/);
});
