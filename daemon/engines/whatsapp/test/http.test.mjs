/**
 * The sidecar's HTTP door (ported from an earlier prototype's whatsapp-send-route and
 * whatsapp-pairing-routes tests; AC-504, AC-512..AC-515, AC-518, plus the token
 * contract of AC-500).
 *
 * POST /send is the ONLY door to the socket. What is pinned here is that the
 * ROUTE enforces the guards, so no caller can skip them, and that every
 * request without the per-boot token is refused before anything else happens.
 * The socket is a fake: nothing here reaches WhatsApp.
 */
import { test, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { createApp } from '../wa-http.js';
import { MARKER } from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, silent, upsert, until, ALICE, GROUP, SELF_LID } from './fakes.mjs';

const TOKEN = 'test-token-0123456789';
const apps = [];
after(async () => { for (const a of apps) await a.close(); });

/**
 * @param {object} [o]
 * @param {boolean} [o.sendDefault]
 * @param {Error} [o.sendError]
 * @param {number} [o.maxUploadBytes]
 * @param {boolean} [o.link] - false leaves the socket unopened (not linked)
 */
async function harness(o = {}) {
  const dir = tempDir('wa-http-');
  const fake = fakeBaileys({ sendError: o.sendError });
  const app = createApp({
    token: TOKEN, dir, baileys: fake.deps, log: silent,
    ...(o.sendDefault === undefined ? {} : { sendDefault: o.sendDefault }),
    ...(o.maxUploadBytes ? { maxUploadBytes: o.maxUploadBytes } : {}),
  });
  apps.push(app);
  app.store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  app.store.ingestHistory({ chats: [{ id: GROUP, name: 'Family Trip' }] });
  if (o.link !== false) await app.wa.startPairing();
  await new Promise((r) => app.server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const headers = { 'x-butler-token': TOKEN };
  const call = (p, init = {}) => fetch(base + p, { ...init, headers: { ...headers, ...(init.headers || {}) } });
  const send = (body) => call('/send', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const sendFile = ({ name, bytes, mime = 'application/octet-stream', ...fields }) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) form.append(k, String(v));
    form.append('files', new Blob([bytes], { type: mime }), name);
    return call('/send', { method: 'POST', body: form });
  };
  const audit = () => {
    const f = path.join(dir, 'sent.jsonl');
    return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  };
  return { app, fake, dir, base, call, send, sendFile, audit, sends: fake.sent };
}

// ---- the token (AC-500) ----

test('EVERY ROUTE REFUSES A REQUEST WITHOUT THE TOKEN, and acts on nothing (401)', async () => {
  const h = await harness();
  const routes = [['GET', '/pairing'], ['POST', '/pairing'], ['DELETE', '/pairing'], ['GET', '/status'],
    ['POST', '/config'], ['POST', '/send'], ['GET', '/media/abc'], ['GET', '/events'], ['GET', '/nonsense']];
  for (const [method, p] of routes) {
    const none = await fetch(h.base + p, { method });
    assert.equal(none.status, 401, `${method} ${p} without a token`);
    const wrong = await fetch(h.base + p, { method, headers: { 'x-butler-token': 'wrong' } });
    assert.equal(wrong.status, 401, `${method} ${p} with a wrong token`);
  }
  // CONSEQUENCE, not only status: a refused POST /send reached neither guard nor socket.
  await fetch(h.base + '/send', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ recipient: 'Alice', text: 'hi', confirm: true }) });
  assert.equal(h.sends.length, 0);
  assert.equal(h.audit().length, 0, 'an unauthenticated request must not even be audited as a decision');
  // And a refused POST /pairing opened no second socket.
  assert.equal(h.fake.calls.length, 1);
});

test('createApp refuses to run without a token or a records directory', () => {
  assert.throws(() => createApp({ dir: tempDir() }), /token/);
  assert.throws(() => createApp({ token: 'x' }), /dir/);
});

// ---- send: an earlier prototype's route tests ----

test('a confirmed message to a known contact is delivered to the socket', async () => {
  const h = await harness();
  const res = await h.send({ recipient: 'Alice', text: 'on my way', confirm: true });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.ok, true);
  assert.equal(body.messageId, h.sends[0].opts.messageId);
  assert.equal(h.sends.length, 1);
  assert.equal(h.sends[0].jid, ALICE);
});

test('EVERY OUTBOUND TEXT ENDS WITH THE MARKER U+2063 (MS-15, AC-518)', async () => {
  const h = await harness();
  await h.send({ recipient: 'Alice', text: 'on my way', confirm: true });
  assert.equal(h.sends[0].content.text, 'on my way' + MARKER);
  assert.ok(h.sends[0].content.text.endsWith('\u2063'));
});

test('a text already ending with the marker does not get a second one', async () => {
  const h = await harness();
  await h.send({ recipient: 'Alice', text: 'once' + MARKER, confirm: true });
  assert.equal(h.sends[0].content.text, 'once' + MARKER);
});

test('a file caption ends with the marker; a file with no caption carries no text', async () => {
  const h = await harness();
  await h.sendFile({ name: 'photo.jpg', bytes: Buffer.from([0xff, 0xd8, 1, 2]), recipient: 'Alice', caption: 'look', confirm: 'true' });
  assert.equal(h.sends[0].content.caption, 'look' + MARKER);
  await h.sendFile({ name: 'report.pdf', bytes: Buffer.from('%PDF-1.4 x'), recipient: 'Alice', confirm: 'true' });
  assert.equal(h.sends[1].content.caption, undefined);
});

test('the first message to a recipient is HELD for confirmation, and nothing is sent', async () => {
  const h = await harness();
  const res = await h.send({ recipient: 'Alice', text: 'on my way' });
  const body = await res.json();
  assert.equal(res.status, 409);
  assert.equal(body.reason, 'confirmation_required');
  assert.equal(body.name, 'Alice Alvarez');
  assert.equal(h.sends.length, 0, 'a held message must not reach the socket');
});

test('an unknown recipient is refused and never reaches the socket', async () => {
  const h = await harness();
  const res = await h.send({ recipient: '+15550009999', text: 'hello stranger', confirm: true });
  assert.equal(res.status, 403);
  assert.equal((await res.json()).reason, 'unknown_recipient');
  assert.equal(h.sends.length, 0);
});

test('A GROUP IN THE ARCHIVE IS A VALID RECIPIENT through the same route (MS-12, AC-513)', async () => {
  const h = await harness();
  const res = await h.send({ recipient: 'Family Trip', text: 'dinner at eight', confirm: true });
  assert.equal(res.status, 200);
  assert.equal(h.sends[0].jid, GROUP);
});

test('THE SEND SWITCH IS A BOOLEAN PASSED PER REQUEST AND RE-READ ON EVERY SEND (MS-12)', async () => {
  const h = await harness();
  const off = await h.send({ recipient: 'Alice', text: 'nope', confirm: true, send_enabled: false });
  assert.equal(off.status, 403);
  assert.equal((await off.json()).reason, 'send_disabled');
  assert.equal(h.sends.length, 0);
  // Flipping it back on takes effect on the very next request: no restart, no cache.
  const on = await h.send({ recipient: 'Alice', text: 'yes', confirm: true, send_enabled: true });
  assert.equal(on.status, 200);
  assert.equal(h.sends.length, 1);
});

test('the send switch defaults ON, and the process default (env) can turn it off', async () => {
  const on = await harness();
  assert.equal((await on.send({ recipient: 'Alice', text: 'a', confirm: true })).status, 200);
  const off = await harness({ sendDefault: false });
  const res = await off.send({ recipient: 'Alice', text: 'a', confirm: true });
  assert.equal(res.status, 403);
  assert.equal(off.sends.length, 0);
  // A request still overrides the process default.
  assert.equal((await off.send({ recipient: 'Alice', text: 'a', confirm: true, send_enabled: true })).status, 200);
});

test('a non-boolean send_enabled is a 400, not a silent default', async () => {
  const h = await harness();
  const res = await h.send({ recipient: 'Alice', text: 'a', confirm: true, send_enabled: 'false' });
  assert.equal(res.status, 400);
  assert.equal(h.sends.length, 0);
});

test('confirm is true only as boolean true in JSON', async () => {
  const h = await harness();
  assert.equal((await h.send({ recipient: 'Alice', text: 'a', confirm: 'true' })).status, 409);
  assert.equal((await h.send({ recipient: 'Alice', text: 'a', confirm: 1 })).status, 409);
  assert.equal(h.sends.length, 0);
});

test('text is required -- an empty send is a bug, not a blank message', async () => {
  const h = await harness();
  assert.equal((await h.send({ recipient: 'Alice', confirm: true })).status, 400);
  assert.equal((await h.send({ recipient: 'Alice', text: '   ', confirm: true })).status, 400);
  assert.equal((await h.send({ text: 'x', confirm: true })).status, 400);
  assert.equal(h.sends.length, 0);
});

test('A FAILED SOCKET SEND REPORTS FAILURE AND IS AUDITED AS ONE', async () => {
  const h = await harness({ sendError: new Error('socket closed') });
  const res = await h.send({ recipient: 'Alice', text: 'will not arrive', confirm: true });
  const body = await res.json();
  assert.equal(res.status, 502);
  assert.equal(body.ok, false);
  const line = h.audit().at(-1);
  assert.equal(line.ok, false);
  assert.match(line.error, /socket closed/);
});

test('a send is audited without the message body, and the length is the text without the marker', async () => {
  const h = await harness();
  await h.send({ recipient: 'Alice', text: 'a private thing', confirm: true });
  const line = h.audit().at(-1);
  assert.equal(line.ok, true);
  assert.equal(line.jid, ALICE);
  assert.equal(line.textLength, 15);
  assert.equal(line.text, undefined);
  assert.ok(!fs.readFileSync(path.join(h.dir, 'sent.jsonl'), 'utf8').includes('private'));
});

test('refusals are audited too', async () => {
  const h = await harness();
  await h.send({ recipient: '+15550009999', text: 'x', confirm: true });
  await h.send({ recipient: 'Alice', text: 'x' });
  assert.deepEqual(h.audit().map((l) => l.reason), ['unknown_recipient', 'confirmation_required']);
});

test('not linked is a 503 once the guard has said yes (and a refusal still wins over it)', async () => {
  const h = await harness({ link: false });
  const ok = await h.send({ recipient: 'Alice', text: 'hi', confirm: true });
  assert.equal(ok.status, 503);
  const refused = await h.send({ recipient: '+15550009999', text: 'hi', confirm: true });
  assert.equal(refused.status, 403);
  assert.equal(h.sends.length, 0);
});

const JPG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5, 6, 7, 8]);
const PDF = Buffer.from('%PDF-1.4 fake body for the test');

test('a multipart jpg to a confirmed contact goes out as an inline image', async () => {
  const h = await harness();
  const res = await h.sendFile({ name: 'photo.jpg', bytes: JPG, mime: 'image/jpeg', recipient: 'Alice', caption: 'look', confirm: 'true' });
  assert.equal(res.status, 200);
  const c = h.sends[0].content;
  assert.ok(Buffer.isBuffer(c.image));
  assert.ok(c.image.equals(JPG));
  assert.equal(c.mimetype, 'image/jpeg');
});

test('a multipart pdf goes out as a document', async () => {
  const h = await harness();
  const res = await h.sendFile({ name: 'report.pdf', bytes: PDF, recipient: 'Alice', confirm: 'true' });
  assert.equal(res.status, 200);
  const c = h.sends[0].content;
  assert.ok(c.document.equals(PDF));
  assert.equal(c.mimetype, 'application/pdf');
  assert.equal(c.fileName, 'report.pdf');
});

test('A FILE TO AN UNCONFIRMED CONTACT IS HELD, and nothing is sent', async () => {
  const h = await harness();
  const res = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: 'Alice' });
  assert.equal(res.status, 409);
  assert.equal((await res.json()).reason, 'confirmation_required');
  assert.equal(h.sends.length, 0);
});

test('a file to an unknown recipient is refused, and the send switch stops files too', async () => {
  const h = await harness();
  const unknown = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: '+15550009999', confirm: 'true' });
  assert.equal(unknown.status, 403);
  const off = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: 'Alice', confirm: 'true', send_enabled: 'false' });
  assert.equal(off.status, 403);
  assert.equal((await off.json()).reason, 'send_disabled');
  assert.equal(h.sends.length, 0);
});

test('confirm must be the string "true" in a form -- any other value is not a confirmation', async () => {
  const h = await harness();
  const res = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: 'Alice', confirm: 'yes' });
  assert.equal(res.status, 409);
  assert.equal(h.sends.length, 0);
});

test('a file over the upload cap is refused with 413 and never sent (AC-515)', async () => {
  const h = await harness({ maxUploadBytes: 8 });
  const res = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: 'Alice', confirm: 'true' });
  assert.equal(res.status, 413);
  assert.equal(h.sends.length, 0);
});

test('two files in one send is a 400: exactly one file per send', async () => {
  const h = await harness();
  const form = new FormData();
  form.append('recipient', 'Alice');
  form.append('confirm', 'true');
  form.append('files', new Blob([JPG]), 'a.jpg');
  form.append('files', new Blob([JPG]), 'b.jpg');
  const res = await h.call('/send', { method: 'POST', body: form });
  assert.equal(res.status, 400);
  assert.equal(h.sends.length, 0);
});

test('a multipart request with no file still needs text', async () => {
  const h = await harness();
  const form = new FormData();
  form.append('recipient', 'Alice');
  form.append('confirm', 'true');
  const res = await h.call('/send', { method: 'POST', body: form });
  assert.equal(res.status, 400);
  assert.match((await res.json()).error, /text is required/);
  assert.equal(h.sends.length, 0);
});

test('A FILE SEND IS AUDITED BY KIND, NAME AND SIZE -- never its bytes', async () => {
  const h = await harness();
  await h.sendFile({ name: 'photo.jpg', bytes: JPG, mime: 'image/jpeg', recipient: 'Alice', text: 'hi', confirm: 'true' });
  const line = h.audit().at(-1);
  assert.equal(line.ok, true);
  assert.equal(line.kind, 'image');
  assert.equal(line.fileName, 'photo.jpg');
  assert.equal(line.bytes, JPG.length);
  assert.equal(line.textLength, 2);
  assert.ok(!fs.readFileSync(path.join(h.dir, 'sent.jsonl'), 'utf8').includes(JPG.toString('base64')));
});

test('a failed socket send of a file reports 502 and is audited as a failure', async () => {
  const h = await harness({ sendError: new Error('socket closed') });
  const res = await h.sendFile({ name: 'report.pdf', bytes: PDF, recipient: 'Alice', confirm: 'true' });
  assert.equal(res.status, 502);
  const line = h.audit().at(-1);
  assert.equal(line.ok, false);
  assert.equal(line.reason, 'send_failed');
  assert.equal(line.kind, 'document');
});

test("the linked account's own chat needs no confirmation through the route, but the rate limit still applies", async () => {
  const h = await harness();
  const res = await h.sendFile({ name: 'photo.jpg', bytes: JPG, recipient: '15555550100@s.whatsapp.net' });
  assert.equal(res.status, 200);
  assert.equal(h.sends[0].jid, '15555550100@s.whatsapp.net');
  await h.call('/config', { method: 'POST', body: JSON.stringify({ send_rate_per_minute: 1 }) });
  const again = await h.send({ recipient: '15555550100@s.whatsapp.net', text: 'second' });
  assert.equal(again.status, 403);
  assert.equal((await again.json()).reason, 'rate_limited');
});

// ---- pairing, status, config ----

test('GET /pairing hands the browser a rendered QR plus the state that explains it', async () => {
  const h = await harness({ link: false });
  await h.call('/pairing', { method: 'POST' });
  h.fake.last().emit('connection.update', { qr: '2@abcdef,ghijkl,mnopqr=' });
  const body = await (await h.call('/pairing')).json();
  assert.match(body.qrDataUrl, /^data:image\/png;base64,/);
  assert.equal(body.state.status, 'awaiting_scan');
  assert.equal(body.view.headline, 'Scan this code');
});

test('POST /pairing twice opens ONE socket; DELETE closes it and keeps the session', async () => {
  const h = await harness({ link: false });
  await h.call('/pairing', { method: 'POST' });
  await h.call('/pairing', { method: 'POST' });
  assert.equal(h.fake.calls.length, 1);
  const sock = h.fake.last();
  const res = await h.call('/pairing', { method: 'DELETE' });
  assert.equal(res.status, 200);
  assert.equal(sock.ended, true);
  assert.ok(fs.existsSync(path.join(h.dir, 'session')));
  const gone = await (await h.call('/pairing')).json();
  assert.equal(gone.state.status, 'idle');
  assert.equal(gone.qrDataUrl, null);
});

test('DELETE /pairing?unlink=true logs out and deletes session/', async () => {
  const h = await harness({ link: false });
  await h.call('/pairing', { method: 'POST' });
  const sock = h.fake.last();
  await h.call('/pairing?unlink=true', { method: 'DELETE' });
  assert.equal(sock.loggedOut, true);
  assert.equal(fs.existsSync(path.join(h.dir, 'session')), false);
});

test('GET /status reports the link and what it archived', async () => {
  const h = await harness();
  const s = await (await h.call('/status')).json();
  assert.equal(s.linked, true);
  assert.equal(s.phone, '+15555550100');
  assert.equal(s.contacts, 1);
  assert.equal(s.chats, 1);
});

test('POST /config sets who is answered; a malformed body is a 400 that changes nothing', async () => {
  const h = await harness();
  const bad = await h.call('/config', { method: 'POST', body: JSON.stringify({ answer: 'alice' }) });
  assert.equal(bad.status, 400);
  assert.deepEqual(h.app.wa.getConfig().answer, []);
  const ok = await h.call('/config', { method: 'POST', body: JSON.stringify({ answer: [ALICE], trigger: 'Jarvis' }) });
  assert.equal(ok.status, 200);
  assert.deepEqual((await ok.json()).config.answer, [ALICE]);
  const notJson = await h.call('/config', { method: 'POST', body: 'nope' });
  assert.equal(notJson.status, 400);
});

// ---- media and events ----

test('GET /media/<id> returns the bytes of an inbound file; unknown id is 404; oversize is 413', async () => {
  const h = await harness();
  const events = [];
  h.app.wa.setConfig({ answer: [ALICE] });
  const stream = await h.call('/events');
  const reader = stream.body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  const pump = (async () => {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffered += decoder.decode(value);
      let nl;
      while ((nl = buffered.indexOf('\n')) >= 0) { events.push(JSON.parse(buffered.slice(0, nl))); buffered = buffered.slice(nl + 1); }
    }
  })();

  h.fake.last().emit('messages.upsert', upsert(ALICE, { audioMessage: { ptt: true, seconds: 2, mimetype: 'audio/ogg; codecs=opus' } }));
  h.fake.last().emit('messages.upsert', upsert(ALICE, { documentMessage: { fileName: 'big.zip', fileLength: 200 * 1024 * 1024 } }));
  await until(() => events.length === 3, 'two inbound items on the stream');

  assert.deepEqual(events[0], { type: 'ready', dropped: 0 });
  assert.equal(events[1].tag, 'guest');
  const media = await h.call('/media/' + events[1].media.id);
  assert.equal(media.status, 200);
  assert.equal(media.headers.get('content-type'), 'audio/ogg; codecs=opus');
  assert.equal(media.headers.get('x-butler-media-kind'), 'audio');
  assert.equal(Buffer.from(await media.arrayBuffer()).toString(), 'ogg');

  const big = await h.call('/media/' + events[2].media.id);
  assert.equal(big.status, 413);
  assert.equal((await big.json()).bytes, 200 * 1024 * 1024);
  assert.equal(h.fake.downloads.length, 1, 'the oversize file was never downloaded');

  assert.equal((await h.call('/media/never-issued')).status, 404);
  await reader.cancel();
  await pump.catch(() => {});
});

test('GET /events streams owner and takeover items as one line of JSON each', async () => {
  const h = await harness();
  h.app.wa.setConfig({ answer: [ALICE] });
  const stream = await h.call('/events');
  assert.equal(stream.headers.get('content-type'), 'application/x-ndjson');
  const reader = stream.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  const read = async (n) => {
    while (text.split('\n').filter(Boolean).length < n) {
      const { value, done } = await reader.read();
      if (done) break;
      text += decoder.decode(value);
    }
    return text.split('\n').filter(Boolean).map((l) => JSON.parse(l));
  };
  h.fake.last().emit('messages.upsert', upsert(SELF_LID, { conversation: 'hello' }, { fromMe: true }));
  h.fake.last().emit('messages.upsert', upsert(ALICE, { conversation: 'mine now' }, { fromMe: true }));
  const items = await read(3);
  assert.deepEqual(items.map((i) => i.type), ['ready', 'message', 'takeover']);
  assert.equal(items[1].tag, 'owner');
  assert.equal(items[2].jid, ALICE);
  await reader.cancel();
});
