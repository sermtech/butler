/**
 * The sidecar's review fixes (docs/messaging.md 6.5, RV-4..RV-12, criteria
 * AC-590..AC-598; evidence .references/whatsapp-review-2026-10-08.md).
 *
 * Driven through the real sidecar pieces (createApp, createWhatsApp, the
 * store) with Baileys replaced by the fake in fakes.mjs. Nothing here reaches
 * WhatsApp or the internet.
 */
import { test, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import { createApp, EVENT_BUFFER_MAX, REPLY_RATE_PER_MINUTE } from '../wa-http.js';
import { openWhatsAppStore, BUSY_TIMEOUT_MS } from '../whatsapp-store.js';
import {
  createWhatsApp, reconnectDelay, makeTextLogger, RECONNECT_BASE_MS, RECONNECT_MAX_MS,
} from '../whatsapp-socket.js';
import { describePairing, reducePairing, IDLE_PAIRING } from '../whatsapp-pairing.js';
import {
  BUTLER_ID_PREFIX, newButlerId, isButlerId, classifyInbound,
} from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, until, SELF_LID, ALICE, ME } from './fakes.mjs';

const TOKEN = 'test-token-0123456789';
const OGG = Buffer.concat([Buffer.from('OggS'), Buffer.alloc(60, 1)]);
const apps = [];
after(async () => { for (const a of apps) await a.close().catch(() => {}); });

const nowS = () => Math.floor(Date.now() / 1000);
const own = (id, message, ts = nowS()) => ({ key: { remoteJid: SELF_LID, id, fromMe: true }, messageTimestamp: ts, message });
const voiceNote = (id) => own(id, { audioMessage: { ptt: true, seconds: 3, mimetype: 'audio/ogg; codecs=opus' } });

/** A real sidecar app on the fake socket, its /events stream read into `events`. */
async function harness({ dir = tempDir('wa-rv-'), subscribe = true, fake = fakeBaileys(), ...opts } = {}) {
  const logs = [];
  const app = createApp({ token: TOKEN, dir, baileys: fake.deps, log: (...a) => logs.push(a.join(' ')), ...opts });
  apps.push(app);
  await app.wa.startPairing();
  await new Promise((r) => app.server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const call = (p, init = {}) => fetch(base + p, { ...init, headers: { 'x-butler-token': TOKEN, ...(init.headers || {}) } });
  const json = (p, body) => call(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const events = [];
  let reader = null;
  let pump = null;
  const attach = async () => {
    const stream = await call('/events');
    reader = stream.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    pump = (async () => {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buffered += decoder.decode(value);
        let nl;
        while ((nl = buffered.indexOf('\n')) >= 0) { events.push(JSON.parse(buffered.slice(0, nl))); buffered = buffered.slice(nl + 1); }
      }
    })();
  };
  if (subscribe) await attach();
  const upsert = (type, ...messages) => fake.last().emit('messages.upsert', { type, messages });
  /** Items after `ready` once `n` arrived, or what came within `ms`. */
  const items = async (n, ms = 1000) => {
    const deadline = Date.now() + ms;
    while (events.length < n + 1 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 10));
    await new Promise((r) => setTimeout(r, 60));
    return events.slice(1);
  };
  const detach = async () => { await reader?.cancel(); await pump?.catch(() => {}); };
  const close = async () => { await detach(); await app.close(); apps.splice(apps.indexOf(app), 1); };
  return { app, fake, dir, call, json, events, attach, detach, upsert, items, close, logs };
}

// ---------------------------------------------------------------- RV-4 (AC-590)

test('RV-4: AN OWN-CHAT MESSAGE REDELIVERED AFTER A RECONNECT IS ROUTED ONCE', async () => {
  const h = await harness({ reconnectDelayMs: 5 });
  h.upsert('notify', own('R1', { conversation: 'book the table' }));
  await h.items(1);
  // The socket drops before the ack; the server delivers the same message again.
  h.fake.last().emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 428 } } } });
  await until(() => h.fake.sockets.length === 2, 'the reconnect');
  h.upsert('append', own('R1', { conversation: 'book the table' }));
  h.upsert('notify', own('R1', { conversation: 'book the table' }));
  const got = await h.items(2, 300);
  assert.deepEqual(got.map((e) => e.id), ['R1'], 'the same request must not run twice');
  await h.close();
});

test('RV-4: ...AND ACROSS A SIDECAR RESTART (the routed ids live in the archive)', async () => {
  const dir = tempDir('wa-rv4-');
  const first = await harness({ dir });
  first.upsert('notify', voiceNote('V1'));
  assert.equal((await first.items(1)).length, 1);
  await first.close();
  const second = await harness({ dir });
  second.upsert('append', voiceNote('V1'));
  second.upsert('notify', own('V2', { conversation: 'a new one' }));
  const got = await second.items(2, 300);
  assert.deepEqual(got.map((e) => e.id), ['V2']);
  await second.close();
});

test('RV-4: a redelivered missed message is counted once', async () => {
  const h = await harness();
  const old = nowS() - 3600;
  h.upsert('append', own('OLD', { conversation: 'while you were down' }, old));
  h.upsert('append', own('OLD', { conversation: 'while you were down' }, old));
  const got = await h.items(2, 300);
  assert.deepEqual(got.map((e) => [e.type, e.id]), [['missed', 'OLD']]);
  await h.close();
});

// ---------------------------------------------------------------- RV-5 (AC-591)

function bootSocket(extra = {}, fakeOpts = {}) {
  const dir = tempDir('wa-rv5-');
  const store = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  const fake = fakeBaileys(fakeOpts);
  const logs = [];
  // The socket takes its Baileys pieces at creation; a test swaps the fetch here.
  let fetchVersion = fake.deps.fetchLatestBaileysVersion;
  const baileys = { ...fake.deps, fetchLatestBaileysVersion: (...a) => fetchVersion(...a) };
  const wa = createWhatsApp({ dir, store, baileys, log: (...a) => logs.push(a.join(' ')), ...extra });
  return { dir, store, fake, wa, logs, setFetch: (fn) => { fetchVersion = fn; } };
}
const closeWith = (sock, statusCode) => sock.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode } } } });

test('RV-5: A FAILED VERSION FETCH IS A VISIBLE ERROR, never a link on the bundled version', async () => {
  const { fake, wa, setFetch } = bootSocket();
  // Baileys' own fetch never throws: it returns the BUNDLED version, flagged.
  setFetch(async () => ({ version: [2, 3000, 1], isLatest: false, error: new Error('getaddrinfo ENOTFOUND') }));
  const state = await wa.startPairing();
  assert.equal(fake.calls.length, 0, 'no socket may open on the bundled version');
  assert.equal(state.status, 'error');
  assert.match(state.error, /current WhatsApp Web version/);
  assert.match(state.error, /ENOTFOUND/);
  assert.equal(describePairing(state).tone, 'error');
  wa.stop();
});

test('RV-5: a saved link whose reconnect cannot fetch the version keeps retrying, then connects', async () => {
  const { fake, wa, setFetch } = bootSocket({ reconnectDelayMs: 5, maxReconnectDelayMs: 20 });
  await wa.startPairing();
  let ok = false;
  let tries = 0;
  setFetch(async () => {
    tries += 1;
    return ok ? { version: [2, 3000, 2], isLatest: true } : { version: [2, 3000, 1], isLatest: false };
  });
  closeWith(fake.last(), 428);
  await until(() => tries >= 3, 'retries while the fetch fails');
  assert.equal(wa.getPairing().status === 'error' || wa.getPairing().status === 'connecting', true);
  assert.equal(fake.calls.length, 1, 'nothing connected on the bundled version');
  ok = true;
  await until(() => fake.calls.length === 2, 'the reconnect once the fetch works');
  assert.deepEqual(fake.calls[1].version, [2, 3000, 2]);
  wa.stop();
});

test('RV-5: NON-TERMINAL CLOSES BACK OFF 3 s DOUBLING TO 5 min, reset when a connection opens', async () => {
  assert.equal(RECONNECT_BASE_MS, 3000);
  assert.equal(RECONNECT_MAX_MS, 300_000);
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 7, 8].map((n) => reconnectDelay(n)),
    [3000, 6000, 12000, 24000, 48000, 96000, 192000, 300000, 300000]);
  const { fake, wa, logs } = bootSocket({ reconnectDelayMs: 4, maxReconnectDelayMs: 16 });
  await wa.startPairing();
  for (let i = 1; i <= 4; i++) {
    closeWith(fake.last(), 428);
    await until(() => fake.calls.length === i + 1, `reconnect ${i}`);
  }
  fake.last().emit('connection.update', { connection: 'open' });
  closeWith(fake.last(), 408);
  await until(() => fake.calls.length === 6, 'the reconnect after an open');
  const waits = logs.filter((l) => /reconnecting in/.test(l)).map((l) => Number(l.match(/in ([\d.]+) s/)[1]) * 1000);
  assert.deepEqual(waits, [4, 8, 16, 16, 4]);
  wa.stop();
});

test('RV-5: 403 IS TERMINAL with its own words, and never reconnects', async () => {
  const { fake, wa } = bootSocket({ reconnectDelayMs: 5 });
  await wa.startPairing();
  closeWith(fake.last(), 403);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(fake.calls.length, 1);
  assert.equal(wa.getPairing().status, 'forbidden');
  const view = describePairing(wa.getPairing());
  assert.match(view.detail, /403/);
  assert.match(view.detail, /will not retry/);
  const again = reducePairing(IDLE_PAIRING, { type: 'close', statusCode: 403, forbidden: true }, 0);
  assert.equal(again.status, 'forbidden');
});

// ---------------------------------------------------------------- RV-6 (AC-592)

test('RV-6: THE ARCHIVE IS WAL WITH A 5 s BUSY TIMEOUT: a reader never fails a write', async () => {
  const dir = tempDir('wa-rv6-');
  const file = path.join(dir, 'whatsapp.db');
  const store = openWhatsAppStore(file);
  assert.equal(BUSY_TIMEOUT_MS, 5000);
  const reader = new DatabaseSync(file);
  assert.equal(reader.prepare('PRAGMA journal_mode').get().journal_mode, 'wal');
  reader.exec('BEGIN');
  reader.prepare('SELECT COUNT(*) FROM messages').get();          // a held read transaction
  store.ingestMessages([{ key: { remoteJid: ALICE, id: 'W1', fromMe: false }, messageTimestamp: 1, message: { conversation: 'hi' } }]);
  reader.exec('COMMIT');
  reader.close();
  assert.equal(store.readChat(ALICE).length, 1);
  store.close();
});

test('RV-6: a write WAITS for another process\'s write lock instead of failing at once', async () => {
  const dir = tempDir('wa-rv6b-');
  const file = path.join(dir, 'whatsapp.db');
  const store = openWhatsAppStore(file);
  const holder = spawn(process.execPath, ['-e', `
    const { DatabaseSync } = require('node:sqlite');
    const db = new DatabaseSync(${JSON.stringify(file)});
    db.exec('BEGIN IMMEDIATE');
    process.stdout.write('locked\\n');
    setTimeout(() => { db.exec('COMMIT'); db.close(); }, 700);
  `], { stdio: ['ignore', 'pipe', 'ignore'] });
  await new Promise((resolve) => holder.stdout.on('data', (d) => { if (String(d).includes('locked')) resolve(); }));
  const t0 = Date.now();
  store.ingestMessages([{ key: { remoteJid: ALICE, id: 'W2', fromMe: false }, messageTimestamp: 1, message: { conversation: 'waited' } }]);
  assert.ok(Date.now() - t0 >= 200, 'the write went through while the lock was held?');
  assert.equal(store.readChat(ALICE)[0].text, 'waited');
  await new Promise((r) => holder.on('exit', r));
  store.close();
});

test('RV-6: a lookup the archive cannot answer never throws out of routing', async () => {
  const dir = tempDir('wa-rv6c-');
  const real = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  const locked = () => { throw new Error('database is locked'); };
  const store = { ...real, isKnownContact: locked, claimRouted: locked, isRouted: locked };
  const fake = fakeBaileys();
  const events = [];
  const logs = [];
  const wa = createWhatsApp({ dir, store, baileys: fake.deps, emit: (e) => events.push(e), log: (...a) => logs.push(a.join(' ')) });
  wa.setConfig({ answer_everyone: true });
  await wa.startPairing();
  fake.last().emit('messages.upsert', { type: 'notify', messages: [{ key: { remoteJid: ALICE, id: 'G1', fromMe: false }, messageTimestamp: nowS(), message: { conversation: 'hello' } }] });
  fake.last().emit('messages.upsert', { type: 'notify', messages: [own('O1', { conversation: 'still answered' })] });
  assert.deepEqual(events.map((e) => [e.tag, e.id]), [['owner', 'O1']], 'the owner message is routed, the unknowable guest is not');
  assert.ok(logs.some((l) => /contact lookup failed/.test(l)));
  assert.ok(logs.some((l) => /routing anyway/.test(l)));
  wa.stop();
  real.close();
});

// ---------------------------------------------------------------- RV-7 (AC-593)

test('RV-7: AN AUDIT WRITE THAT FAILS AFTER A SUCCESSFUL SEND STILL ANSWERS "sent"', async () => {
  const h = await harness({ subscribe: false });
  h.app.wa.setConfig({ answer: [ALICE] });
  fs.mkdirSync(path.join(h.dir, 'sent.jsonl'));            // appendFileSync now throws EISDIR
  const send = await h.json('/send', { recipient: ME.id.replace(':88', ''), text: 'note to self', confirm: true, send_enabled: true });
  const reply = await h.json('/reply', { jid: SELF_LID, text: 'done' });
  const guest = await h.json('/guest-reply', { jid: ALICE, text: 'Butler: she is out.' });
  for (const [what, r] of [['send', send], ['reply', reply], ['guest-reply', guest]]) {
    const body = await r.json();
    assert.equal(r.status, 200, `${what}: ${JSON.stringify(body)}`);
    assert.ok(isButlerId(body.messageId), what);
  }
  assert.equal(h.fake.sent.length, 3);
  assert.equal(h.logs.filter((l) => /AUDIT WRITE FAILED/.test(l)).length, 3);
  assert.ok(h.logs.every((l) => !l.includes('note to self') && !l.includes('she is out')), 'the log carries no content');
  await h.close();
});

// ---------------------------------------------------------------- RV-8 (AC-594)

const albumChild = (id, parent, kind = 'imageMessage', caption) => own(id, {
  associatedChildMessage: {
    message: {
      [kind]: { mimetype: kind === 'imageMessage' ? 'image/jpeg' : 'video/mp4', ...(caption ? { caption } : {}) },
      messageContextInfo: { messageAssociation: { associationType: 1, parentMessageKey: { remoteJid: SELF_LID, fromMe: true, id: parent } } },
    },
  },
});
const albumParent = (id, images, videos = 0) => own(id, { albumMessage: { expectedImageCount: images, expectedVideoCount: videos } });

test('RV-8: AN ALBUM ARRIVING WITHIN THE WINDOW IS ONE OWNER ITEM WITH ALL ITS FILES', async () => {
  const h = await harness({ albumSettleMs: 150 });
  h.upsert('notify', albumChild('C1', 'ALB', 'imageMessage', 'the kitchen'));
  h.upsert('notify', albumChild('C2', 'ALB'));
  // Typed AFTER the photos: must not be answered before them.
  h.upsert('notify', own('T1', { conversation: 'which one is best?' }));
  await new Promise((r) => setTimeout(r, 60));
  h.upsert('notify', albumChild('C3', 'ALB', 'videoMessage'));
  h.upsert('notify', albumChild('C2', 'ALB'));                     // a redelivery inside the window
  const got = await h.items(2, 1500);
  assert.equal(got.length, 2, JSON.stringify(got));
  const [album, text] = got;
  assert.equal(album.tag, 'owner');
  assert.equal(album.id, 'C1');
  assert.equal(album.albumId, 'ALB');
  assert.deepEqual(album.album.map((m) => m.kind), ['image', 'image', 'video']);
  assert.equal(new Set(album.album.map((m) => m.id)).size, 3, 'every file has its own media id');
  assert.equal(album.media.id, album.album[0].id, 'media is the first file, for a daemon that knows no albums');
  assert.equal(album.text, 'the kitchen');
  assert.equal(text.id, 'T1');
  for (const m of album.album) assert.equal((await h.call('/media/' + m.id)).status, 200);
  await h.close();
});

test('RV-8: an album whose announced size is in goes at once; one in another chat is not held', async () => {
  const h = await harness({ albumSettleMs: 60_000 });
  h.app.wa.setConfig({ answer: [ALICE] });
  h.upsert('notify', albumParent('ALB2', 2));
  h.upsert('notify', albumChild('D1', 'ALB2'));
  h.fake.last().emit('messages.upsert', { type: 'notify', messages: [{ key: { remoteJid: ALICE, id: 'A1', fromMe: false }, messageTimestamp: nowS(), message: { conversation: 'hi' } }] });
  h.upsert('notify', albumChild('D2', 'ALB2'));
  const got = await h.items(2, 1000);
  assert.deepEqual(got.map((e) => [e.tag, e.id, e.album ? e.album.length : 0]), [['guest', 'A1', 0], ['owner', 'D1', 2]]);
  await h.close();
});

// ---------------------------------------------------------------- RV-9 (AC-595)

test('RV-9: STICKER, LOCATION, CONTACT CARD AND POLL ARE OWNER ITEMS MARKED unsupported', async () => {
  const h = await harness();
  h.upsert('notify', own('S1', { stickerMessage: { mimetype: 'image/webp' } }));
  h.upsert('notify', own('L1', { locationMessage: { degreesLatitude: 1, degreesLongitude: 2 } }));
  h.upsert('notify', own('K1', { contactMessage: { displayName: 'Bruno', vcard: 'BEGIN:VCARD' } }));
  h.upsert('notify', own('P1', { pollCreationMessageV3: { name: 'lunch?', options: [{ optionName: 'yes' }] } }));
  const got = await h.items(4);
  assert.deepEqual(got.map((e) => [e.tag, e.id, e.unsupported, e.text]),
    [['owner', 'S1', 'sticker', ''], ['owner', 'L1', 'location', ''], ['owner', 'K1', 'contact', ''], ['owner', 'P1', 'poll', '']]);
  assert.ok(got.every((e) => !e.media));
  await h.close();
});

test('RV-9: AN EDITED MESSAGE REACHES BUTLER AS "(edited) <new text>"', async () => {
  const h = await harness();
  h.upsert('notify', own('E1', {
    protocolMessage: { type: 14, key: { remoteJid: SELF_LID, fromMe: true, id: 'ORIG' }, editedMessage: { conversation: 'meet at 8, not 7' } },
  }));
  h.upsert('notify', own('E2', {
    editedMessage: { message: { protocolMessage: { type: 14, key: { id: 'ORIG2' }, editedMessage: { extendedTextMessage: { text: 'second fix' } } } } },
  }));
  const got = await h.items(2);
  assert.deepEqual(got.map((e) => [e.tag, e.id, e.edited, e.text]),
    [['owner', 'E1', 'ORIG', '(edited) meet at 8, not 7'], ['owner', 'E2', 'ORIG2', '(edited) second fix']]);
  await h.close();
});

test('RV-9: unsupported and edits are the OWN chat\'s only; a guest\'s sticker stays unanswered', () => {
  const ownJids = [SELF_LID];
  const config = { answer: [ALICE], answer_everyone: false, trigger: 'Butler' };
  const msg = { key: { remoteJid: ALICE, id: 'X', fromMe: false }, messageTimestamp: 1, message: { stickerMessage: {} } };
  assert.equal(classifyInbound({ msg, type: 'notify', ownJids, config, isKnownContact: () => true }), null);
  const edit = { ...msg, message: { protocolMessage: { type: 14, key: { id: 'O' }, editedMessage: { conversation: 'x' } } } };
  assert.equal(classifyInbound({ msg: edit, type: 'notify', ownJids, config, isKnownContact: () => true }), null);
});

// ---------------------------------------------------------------- RV-10 (AC-596)

test('RV-10: BAILEYS\' LOGGER PRINTS TEXT ONLY: the message and field names, no keys, no numbers', async () => {
  const { fake, wa, logs } = bootSocket();
  await wa.startPairing();
  const logger = fake.calls[0].logger;
  logger.error({ key: { remoteJid: '15551230000@s.whatsapp.net', id: 'ABC', participant: '15559876543@s.whatsapp.net' }, err: new Error('boom') }, 'failed to decrypt message');
  logger.warn({ tag: 'presence', attrs: { from: '15551230000@s.whatsapp.net' }, content: Buffer.from('secret') }, 'recv invalid presence node');
  logger.warn('no sessions for 15551230000@s.whatsapp.net');
  logger.child({ msgId: 'X' }).error({ fullErrorNode: { tag: 'x' } }, 'stream errored');
  logger.warn({ ['15551230000@s.whatsapp.net']: 1 }, 'a map keyed by jid');
  logger.info({ privKey: 'PRIV' }, 'info is not printed');
  const all = logs.join('\n');
  assert.match(all, /failed to decrypt message \(fields: key, err\)/);
  assert.match(all, /recv invalid presence node \(fields: tag, attrs, content\)/);
  assert.match(all, /stream errored \(fields: fullErrorNode\)/);
  assert.match(all, /a map keyed by jid \(fields: \?\)/);
  for (const bad of ['15551230000', '15559876543', 'ABC', 'secret', 'PRIV', '{', '"']) {
    assert.ok(!all.includes(bad), `"${bad}" reached the log: ${all}`);
  }
  assert.equal(logs.filter((l) => l.includes('baileys')).length, 5);
  assert.equal(typeof makeTextLogger, 'function');
  wa.stop();
});

// ---------------------------------------------------------------- RV-11 (AC-597)

test('RV-11: EVENTS PUBLISHED WITH NO SUBSCRIBER WAIT AND FOLLOW THE NEXT ready', async () => {
  const h = await harness({ subscribe: false });
  h.upsert('notify', own('B1', { conversation: 'first' }));
  h.upsert('notify', own('B2', { conversation: 'second' }));
  await h.attach();
  const got = await h.items(2);
  assert.deepEqual(h.events[0], { type: 'ready', dropped: 0 });
  assert.deepEqual(got.map((e) => e.id), ['B1', 'B2']);
  // Delivered once: a later subscriber does not get them again.
  await h.detach();
  h.events.length = 0;
  await h.attach();
  assert.deepEqual(await h.items(0, 100), []);
  await h.close();
});

test('RV-11: the buffer is bounded; the oldest are dropped and counted', async () => {
  const h = await harness({ subscribe: false });
  const extra = 5;
  for (let i = 0; i < EVENT_BUFFER_MAX + extra; i++) h.upsert('notify', own('N' + i, { conversation: 'm' + i }));
  await h.attach();
  const got = await h.items(EVENT_BUFFER_MAX, 3000);
  assert.equal(EVENT_BUFFER_MAX, 200);
  assert.deepEqual(h.events[0], { type: 'ready', dropped: extra });
  assert.equal(got.length, EVENT_BUFFER_MAX);
  assert.equal(got[0].id, 'N' + extra);
  assert.ok(h.logs.some((l) => /5 inbound items were dropped/.test(l)));
  await h.close();
});

// ---------------------------------------------------------------- RV-12 (AC-598)

test('RV-12: EVERY BUTLER SEND CARRIES THE BUTLER ID PREFIX, in WhatsApp Web\'s id shape', async () => {
  const h = await harness({ subscribe: false });
  h.app.wa.setConfig({ answer: [ALICE] });
  await h.json('/send', { recipient: ME.id.replace(':88', ''), text: 'to me', confirm: true, send_enabled: true });
  await h.json('/reply', { jid: SELF_LID, text: 'text reply' });
  const form = new FormData();
  form.append('jid', SELF_LID);
  form.append('ptt', 'true');
  form.append('files', new Blob([OGG]), 'voice.ogg');
  await h.call('/reply', { method: 'POST', body: form });
  await h.json('/guest-reply', { jid: ALICE, text: 'Butler: hi' });
  assert.equal(h.fake.sent.length, 4);
  for (const s of h.fake.sent) {
    assert.match(s.opts.messageId, /^3EB0[0-9A-F]{18}$/, 'the shape of Baileys\' generateMessageIDV2');
    assert.ok(s.opts.messageId.startsWith(BUTLER_ID_PREFIX));
  }
  assert.equal(new Set(h.fake.sent.map((s) => s.opts.messageId)).size, 4);
  await h.close();
});

test('RV-12: AN OWN-CHAT MESSAGE WITH THE PREFIX IS NEVER ROUTED OR MISSED -- another Butler\'s voice note', async () => {
  const h = await harness();
  const theirs = newButlerId();                       // what a second Butler on this number sends
  h.upsert('notify', voiceNote(theirs));
  h.upsert('notify', own(newButlerId(), { conversation: 'a text without the marker' }));
  h.upsert('append', own(newButlerId(), { conversation: 'old' }, nowS() - 3600));
  h.upsert('notify', voiceNote('PHONE1'));
  const got = await h.items(1, 300);
  assert.deepEqual(got.map((e) => e.id), ['PHONE1']);
  // Nor a takeover in a guest chat.
  const ownJids = [SELF_LID];
  const config = { answer: [ALICE], answer_everyone: false, trigger: 'Butler' };
  const fromMe = { key: { remoteJid: ALICE, id: newButlerId(), fromMe: true }, messageTimestamp: 1, message: { conversation: 'unmarked' } };
  assert.equal(classifyInbound({ msg: fromMe, type: 'notify', ownJids, config, isKnownContact: () => false }), null);
  assert.equal(classifyInbound({ msg: { ...fromMe, key: { ...fromMe.key, id: 'HUMAN' } }, type: 'notify', ownJids, config, isKnownContact: () => false }).type, 'takeover');
  await h.close();
});

test(`RV-12: /reply IS LIMITED TO ${REPLY_RATE_PER_MINUTE} A MINUTE (429), audited`, async () => {
  const h = await harness({ subscribe: false });
  assert.equal(REPLY_RATE_PER_MINUTE, 30);
  const codes = [];
  for (let i = 0; i <= REPLY_RATE_PER_MINUTE; i++) codes.push((await h.json('/reply', { jid: SELF_LID, text: 'r' + i })).status);
  assert.deepEqual(codes.slice(0, REPLY_RATE_PER_MINUTE), Array(REPLY_RATE_PER_MINUTE).fill(200));
  assert.equal(codes.at(-1), 429);
  assert.equal(h.fake.sent.length, REPLY_RATE_PER_MINUTE);
  const audit = fs.readFileSync(path.join(h.dir, 'sent.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  assert.equal(audit.at(-1).reason, 'rate_limited');
  await h.close();
});
