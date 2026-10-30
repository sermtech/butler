/**
 * The options the socket hands to Baileys, and how it reacts to closes
 * (ported from an earlier prototype's whatsapp-config test; AC-502, AC-504, AC-505).
 *
 * Three Baileys defaults are WRONG for a personal account and each presents as
 * something other than a config mistake: markOnlineOnConnect (the phone stops
 * ringing), getMessage (recipients see "This message took a while"), and
 * syncFullHistory without shouldSyncHistoryMessage (every history sync is
 * dropped: 0 contacts, and the sync comes once per link).
 *
 * Baileys is injected because asserting these against the real one would open
 * a socket to WhatsApp; the options object is fully determined before any
 * network call.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { proto } from '@whiskeysockets/baileys';
import { openWhatsAppStore } from '../whatsapp-store.js';
import { createWhatsApp } from '../whatsapp-socket.js';
import { fakeBaileys, tempDir, silent, until, ALICE, GROUP } from './fakes.mjs';
import path from 'node:path';
import fs from 'node:fs';

function boot(fakeOpts = {}, extra = {}) {
  const dir = tempDir('wa-socket-');
  const store = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  const fake = fakeBaileys(fakeOpts);
  const events = [];
  const wa = createWhatsApp({ dir, store, emit: (e) => events.push(e), baileys: fake.deps, reconnectDelayMs: 20, log: silent, ...extra });
  return { dir, store, fake, wa, events };
}

async function connectOnce() {
  const { fake, wa } = boot();
  await wa.startPairing();
  wa.stop();
  assert.equal(fake.calls.length, 1, 'expected exactly one socket to be opened');
  return fake.calls[0];
}

test('the phone keeps its push notifications -- markOnlineOnConnect is off', async () => {
  const opts = await connectOnce();
  assert.equal(opts.markOnlineOnConnect, false, 'Baileys defaults this to true, which stops the phone from ringing');
});

test('the linked device is named Butler in the phone list (MS-5)', async () => {
  const opts = await connectOnce();
  assert.equal(opts.browser[0], 'Butler');
});

test('a failed decrypt can be retried -- getMessage is supplied and answers from the archive', async () => {
  const { fake, wa, store } = boot();
  await wa.startPairing();
  const opts = fake.calls[0];
  assert.equal(typeof opts.getMessage, 'function', 'without this, recipients see "This message took a while"');
  assert.equal(await opts.getMessage({ remoteJid: ALICE, id: 'NOPE' }), undefined,
    'an unknown key must resolve, not throw -- Baileys calls this mid-decrypt');
  fake.last().emit('messages.upsert', {
    type: 'notify',
    messages: [{ key: { remoteJid: ALICE, id: 'RETRY', fromMe: true }, messageTimestamp: 1700000700, message: { conversation: 'resend me' } }],
  });
  assert.deepEqual(await opts.getMessage({ remoteJid: ALICE, id: 'RETRY' }), { conversation: 'resend me' });
  wa.stop();
  store.close();
});

test('how much private history lands on disk is OUR decision, stated explicitly', async () => {
  const opts = await connectOnce();
  assert.equal(opts.syncFullHistory, false, 'left implicit, Baileys defaults it to true');
});

test('recent history is synced and only the FULL sync is refused', async () => {
  // Baileys rc.9 replaced an absent `shouldSyncHistoryMessage` with
  // `() => !!syncFullHistory` (rc14's default is the same filter Butler passes,
  // lib/Defaults/index.js:65-67, but it stays explicit), so `syncFullHistory: false` alone discarded
  // EVERY history notification. Measured 2026-10-02: 0 contacts after linking.
  const opts = await connectOnce();
  const T = proto.HistorySync.HistorySyncType;
  assert.equal(typeof opts.shouldSyncHistoryMessage, 'function', 'absent, Baileys syncs nothing');
  for (const kept of [T.INITIAL_BOOTSTRAP, T.RECENT, T.PUSH_NAME, T.ON_DEMAND]) {
    assert.equal(opts.shouldSyncHistoryMessage({ syncType: kept }), true, `syncType ${kept} must be kept`);
  }
  assert.equal(opts.shouldSyncHistoryMessage({ syncType: T.FULL }), false, 'the full archive stays off disk');
});

test('the client version still comes from the live fetch, not the bundled default', async () => {
  const opts = await connectOnce();
  assert.deepEqual(opts.version, [2, 3000, 1043857760], 'a stale version is refused with 405 before any QR');
});

test('printQRInTerminal is not passed back (Baileys v7 removed it)', async () => {
  const opts = await connectOnce();
  assert.equal('printQRInTerminal' in opts, false);
});

// ---- the archive is wired to the socket, not merely built beside it ----

test('the initial history sync reaches the archive', async () => {
  const { fake, wa } = boot();
  await wa.startPairing();
  fake.last().emit('messaging-history.set', {
    contacts: [{ id: ALICE, name: 'Alice Alvarez' }],
    chats: [{ id: GROUP, name: 'Family', conversationTimestamp: 1700000000 }],
    messages: [],
  });
  assert.equal(wa.store.listContacts().length, 1, 'contacts.length -- the address book never arrived');
  assert.equal(wa.store.listChats({ groupsOnly: true })[0].name, 'Family');
  wa.stop();
});

test('an incoming message is archived even from a sender nobody answers', async () => {
  const { fake, wa, events } = boot();
  await wa.startPairing();
  fake.last().emit('messages.upsert', {
    type: 'notify',
    messages: [{ key: { remoteJid: ALICE, id: 'X1', fromMe: false }, messageTimestamp: 1700000500, pushName: 'Alice', message: { conversation: 'archived anyway' } }],
  });
  assert.equal(wa.store.readChat(ALICE)[0].text, 'archived anyway');
  assert.equal(events.length, 0, 'the routing filter dropped it; the archive is a different question');
  wa.stop();
});

test("the user's OWN replies are archived -- half of every conversation", async () => {
  const { fake, wa } = boot();
  await wa.startPairing();
  fake.last().emit('messages.upsert', {
    type: 'notify',
    messages: [{ key: { remoteJid: ALICE, id: 'MINE', fromMe: true }, messageTimestamp: 1700000600, message: { conversation: 'what I said back' } }],
  });
  const chat = wa.store.readChat(ALICE);
  assert.equal(chat.length, 1);
  assert.equal(chat[0].fromMe, 1);
  wa.stop();
});

// ---- one socket (AC-504) ----

test('LINKING TWICE RETURNS THE ATTEMPT IN FLIGHT, never a second socket (AC-504)', async () => {
  const { fake, wa } = boot();
  const [a, b] = await Promise.all([wa.startPairing(), wa.startPairing()]);
  await wa.startPairing();
  assert.equal(fake.calls.length, 1, 'a second socket would count as another linked device');
  assert.equal(a.status, 'connecting');
  assert.equal(b.status, 'connecting');
  wa.stop();
});

test('a QR becomes a PNG data URL and the state that explains it', async () => {
  const { fake, wa } = boot({ user: undefined });
  await wa.startPairing();
  fake.last().emit('connection.update', { qr: '2@abcdef,ghijkl,mnopqr=' });
  const v = await wa.pairingView();
  assert.match(v.qrDataUrl, /^data:image\/png;base64,/);
  assert.equal(v.state.status, 'awaiting_scan');
  assert.equal(v.view.headline, 'Scan this code');
  wa.stop();
});

test('no QR means no image, not a broken one', async () => {
  const { wa } = boot();
  assert.equal((await wa.pairingView()).qrDataUrl, null);
});

// ---- reconnect policy (MS-6, AC-505) ----

const closeWith = (sock, statusCode) => sock.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode } } } });

test('515 (restart required, normal after a scan) reconnects AT ONCE (MS-6)', async () => {
  const { fake, wa } = boot({}, { reconnectDelayMs: 60_000 });
  await wa.startPairing();
  closeWith(fake.last(), 515);
  await until(() => fake.calls.length === 2, 'the immediate reconnect');
  assert.equal(wa.getPairing().status, 'connecting', 'a 515 is not a failure to show');
  wa.stop();
});

test('440 (connection replaced) is TERMINAL: says so and never reconnects (MS-6)', async () => {
  const { fake, wa } = boot({}, { reconnectDelayMs: 5 });
  await wa.startPairing();
  closeWith(fake.last(), 440);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(fake.calls.length, 1, 'a loop here would keep kicking the other program');
  assert.equal(wa.getPairing().status, 'replaced');
  assert.match((await wa.pairingView()).view.detail, /open in another program/i);
  assert.equal(wa.status().linked, false);
});

test('401 (unlinked from the phone) is terminal and asks to link again (MS-6)', async () => {
  const { fake, wa } = boot({}, { reconnectDelayMs: 5 });
  await wa.startPairing();
  closeWith(fake.last(), 401);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(fake.calls.length, 1);
  assert.equal(wa.getPairing().status, 'logged_out');
  assert.match((await wa.pairingView()).view.detail, /link again/i);
});

test('linking again after 401 starts from a clean session (the old credentials are dead)', async () => {
  const { fake, wa, dir } = boot();
  await wa.startPairing();
  fs.writeFileSync(path.join(dir, 'session', 'stale.json'), '{}');
  closeWith(fake.last(), 401);
  await wa.startPairing();
  assert.equal(fs.existsSync(path.join(dir, 'session', 'stale.json')), false);
  assert.equal(fake.calls.length, 2);
  wa.stop();
});

test('any other close reconnects after the delay', async () => {
  const { fake, wa } = boot({}, { reconnectDelayMs: 10 });
  await wa.startPairing();
  closeWith(fake.last(), 428);
  await until(() => fake.calls.length === 2, 'the delayed reconnect');
  wa.stop();
});

test('a late close from a socket we already replaced does not start another reconnect', async () => {
  const { fake, wa } = boot({}, { reconnectDelayMs: 10 });
  await wa.startPairing();
  const first = fake.last();
  closeWith(first, 515);
  await until(() => fake.calls.length === 2, 'the 515 reconnect');
  closeWith(first, 428); // stale socket
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(fake.calls.length, 2);
  wa.stop();
});

test('Disconnect closes the socket and keeps the session; Unlink logs out and deletes it', async () => {
  const { fake, wa, dir } = boot();
  await wa.startPairing();
  const sock = fake.last();
  const sessionDir = path.join(dir, 'session');
  assert.ok(fs.existsSync(sessionDir));
  wa.cancelPairing();
  assert.equal(sock.ended, true);
  assert.ok(fs.existsSync(sessionDir), 'disconnect must keep the link');
  await wa.startPairing();
  const second = fake.last();
  await wa.unlink();
  assert.equal(second.loggedOut, true);
  assert.equal(fs.existsSync(sessionDir), false, 'the session folder IS the credential');
});

test('Unlink tells WhatsApp while the socket is still open: logout first, end after', async () => {
  const { fake, wa } = boot();
  await wa.startPairing();
  const sock = fake.last();
  const order = [];
  const logout = sock.logout.bind(sock);
  sock.logout = async () => { order.push(`logout(ended=${sock.ended})`); await logout(); };
  const end = sock.end.bind(sock);
  sock.end = () => { order.push('end'); end(); };
  await wa.unlink();
  assert.deepEqual(order.slice(0, 2), ['logout(ended=false)', 'end'], 'a logout on a closed socket fails and the phone keeps the device linked');
  assert.equal(sock.loggedOut, true);
});

test('Unlink still closes the socket and deletes session/ when logout fails, and says so', async () => {
  const lines = [];
  const { fake, wa, dir } = boot({}, { log: (...a) => lines.push(a.join(' ')) });
  await wa.startPairing();
  const sock = fake.last();
  sock.logout = async () => { throw new Error('boom'); };
  await wa.unlink();
  assert.equal(sock.ended, true, 'the socket is closed even though logout failed');
  assert.equal(fs.existsSync(path.join(dir, 'session')), false, 'the session folder IS the credential');
  assert.ok(lines.some((l) => l.includes('logout failed') && l.includes('boom')), 'the failure is logged, not swallowed');
});

test('the status route data counts what the link archived', async () => {
  const { fake, wa } = boot();
  await wa.startPairing();
  fake.last().emit('messaging-history.set', { contacts: [{ id: ALICE, name: 'Alice' }], chats: [], messages: [] });
  const s = wa.status();
  assert.equal(s.linked, true);
  assert.equal(s.phone, '+15555550100');
  assert.equal(s.contacts, 1);
  wa.stop();
});
