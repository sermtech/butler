/**
 * The reply door, typing presence and the missed count (docs/messaging.md
 * MS-17, MS-18a, MS-19; AC-520, AC-524, AC-539). Phase M6/M7.
 *
 * POST /reply answers the USER'S OWN CHAT and nothing else: a reply to a
 * message that already passed the inbound filter does not pass the send
 * guard (MS-10, as an earlier prototype), so the route itself refuses every other chat.
 * It quotes the triggering message, carries the marker on every text, and
 * sends a push-to-talk voice note when asked. POST /presence shows "typing"
 * in the own chat only. An `append` delivery in the own chat that Butler did
 * not send is a message the user wrote while Butler was down: it is emitted
 * as `missed`, never routed.
 *
 * The socket is a fake: nothing here reaches WhatsApp.
 */
import { test, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { createApp } from '../wa-http.js';
import { MARKER, classifyMissed, newButlerId } from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, silent, upsert, ALICE, SELF_LID, SELF_PN } from './fakes.mjs';

const TOKEN = 'test-token-0123456789';
const OGG = Buffer.concat([Buffer.from('OggS'), Buffer.alloc(60, 1)]);
const apps = [];
after(async () => { for (const a of apps) await a.close(); });

async function harness({ link = true } = {}) {
  const dir = tempDir('wa-reply-');
  const fake = fakeBaileys();
  const events = [];
  const app = createApp({ token: TOKEN, dir, baileys: fake.deps, log: silent });
  apps.push(app);
  app.store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  if (link) await app.wa.startPairing();
  await new Promise((r) => app.server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const call = (p, init = {}) => fetch(base + p, { ...init, headers: { 'x-butler-token': TOKEN, ...(init.headers || {}) } });
  const json = (p, body) => call(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const voice = (fields, bytes = OGG) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) form.append(k, String(v));
    form.append('files', new Blob([bytes]), 'voice.ogg');
    return call('/reply', { method: 'POST', body: form });
  };
  const file = (fields, name, bytes) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) form.append(k, String(v));
    form.append('files', new Blob([bytes]), name);
    return call('/reply', { method: 'POST', body: form });
  };
  const audit = () => {
    const f = path.join(dir, 'sent.jsonl');
    return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  };
  // The owner's own message, as the phone delivers it, so it can be quoted.
  const ownMessage = (id, text = 'what is on my calendar') => {
    fake.last().emit('messages.upsert', upsert(SELF_LID, { conversation: text }, { fromMe: true, id }));
  };
  return { app, fake, dir, call, json, voice, file, audit, ownMessage, events, sent: fake.sent };
}

// ---- POST /reply (MS-17) ----

test('A REPLY GOES TO THE OWN CHAT, QUOTED TO THE TRIGGER, ENDING WITH THE MARKER', async () => {
  const h = await harness();
  h.ownMessage('Q1');
  const r = await h.json('/reply', { jid: SELF_LID, text: 'Three meetings.', quote: 'Q1' });
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.ok, true);
  assert.equal(body.messageId, h.sent[0].opts.messageId);
  assert.equal(h.sent.length, 1);
  assert.equal(h.sent[0].jid, SELF_LID);
  assert.equal(h.sent[0].content.text, 'Three meetings.' + MARKER);
  assert.equal(h.sent[0].opts.quoted.key.id, 'Q1', 'the reply must quote the message that triggered it');
  assert.equal(h.sent[0].opts.quoted.message.conversation, 'what is on my calendar');
});

test('the quote is found in the archive when the live cache no longer has it', async () => {
  const h = await harness();
  h.app.store.ingestMessages([{ key: { remoteJid: SELF_LID, id: 'OLD1', fromMe: true }, messageTimestamp: 1790950000,
    message: { conversation: 'archived question' } }]);
  const r = await h.json('/reply', { jid: SELF_LID, text: 'answer', quote: 'OLD1' });
  assert.equal(r.status, 200);
  assert.equal(h.sent[0].opts.quoted.key.id, 'OLD1');
  assert.equal(h.sent[0].opts.quoted.message.conversation, 'archived question');
});

test('a reply with no quote is sent unquoted (the missed-messages line)', async () => {
  const h = await harness();
  const r = await h.json('/reply', { jid: SELF_PN, text: 'I was offline.' });
  assert.equal(r.status, 200);
  assert.equal(h.sent[0].opts.quoted, undefined);
});

test('AN UNKNOWN QUOTE IS A 404 AND NOTHING IS SENT', async () => {
  const h = await harness();
  const r = await h.json('/reply', { jid: SELF_LID, text: 'answer', quote: 'NEVER' });
  assert.equal(r.status, 404);
  assert.equal((await r.json()).reason, 'unknown_quote');
  assert.equal(h.sent.length, 0);
});

test('A REPLY TO ANY OTHER CHAT IS REFUSED (403): the reply door is the own chat only', async () => {
  const h = await harness();
  const r = await h.json('/reply', { jid: ALICE, text: 'hi' });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).reason, 'not_own_chat');
  assert.equal(h.sent.length, 0);
});

test('a reply while not linked is a 503 and nothing is sent', async () => {
  const h = await harness({ link: false });
  const r = await h.json('/reply', { jid: SELF_LID, text: 'hi' });
  assert.equal(r.status, 503);
  assert.equal(h.sent.length, 0);
});

test('an empty reply is a 400: a blank message is a caller bug', async () => {
  const h = await harness();
  assert.equal((await h.json('/reply', { jid: SELF_LID, text: '  ' })).status, 400);
  assert.equal(h.sent.length, 0);
});

test('a reply is audited without its body, marked as a reply, and never charges the send rate', async () => {
  const h = await harness();
  for (let i = 0; i < 12; i++) assert.equal((await h.json('/reply', { jid: SELF_LID, text: 'part ' + i })).status, 200);
  const lines = h.audit();
  assert.equal(lines.length, 12);
  assert.equal(lines[0].reply, true);
  assert.equal(lines[0].textLength, 'part 0'.length);
  assert.ok(!JSON.stringify(lines).includes('part 0'), 'the body must not be in the audit');
  // The agent's own send door still has its whole minute of quota.
  const s = await h.json('/send', { recipient: 'Alice', text: 'x', confirm: true, send_enabled: true });
  assert.equal(s.status, 200);
});

// ---- voice notes out (MS-18a, AC-539) ----

test('A VOICE REPLY IS A PUSH-TO-TALK OGG/OPUS NOTE, QUOTED', async () => {
  const h = await harness();
  h.ownMessage('Q2');
  const r = await h.voice({ jid: SELF_LID, quote: 'Q2', ptt: 'true' });
  assert.equal(r.status, 200);
  const c = h.sent[0].content;
  assert.equal(c.ptt, true);
  assert.equal(c.mimetype, 'audio/ogg; codecs=opus');
  assert.ok(Buffer.from(c.audio).equals(OGG));
  assert.equal(h.sent[0].opts.quoted.key.id, 'Q2');
  assert.equal(h.audit()[0].kind, 'voice');
});

test('a voice note that is not an OGG file is a 400 and nothing is sent', async () => {
  const h = await harness();
  const r = await h.voice({ jid: SELF_LID, ptt: 'true' }, Buffer.from('RIFF....WAVEfmt '));
  assert.equal(r.status, 400);
  assert.equal(h.sent.length, 0);
});

// ---- a shown file out (docs/rich-chat.md O-1) ----

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');
const PDF = Buffer.from('%PDF-1.4 1 0 obj <<>> endobj');

test('A NON-PTT IMAGE GOES OUT AS AN IMAGE WITH ITS CAPTION, QUOTED', async () => {
  const h = await harness();
  h.ownMessage('Q3');
  const r = await h.file({ jid: SELF_LID, quote: 'Q3', caption: 'the chart' }, 'chart.png', PNG);
  assert.equal(r.status, 200);
  const c = h.sent[0].content;
  assert.ok(Buffer.from(c.image).equals(PNG));
  assert.equal(c.mimetype, 'image/png');
  assert.equal(c.caption, 'the chart');
  assert.equal(c.ptt, undefined);
  assert.equal(h.sent[0].opts.quoted.key.id, 'Q3');
  const line = h.audit()[0];
  assert.equal(line.kind, 'file');
  assert.equal(line.fileName, 'chart.png');
  assert.equal(line.bytes, PNG.length);
});

test('a pdf goes out as a document with its name', async () => {
  const h = await harness();
  const r = await h.file({ jid: SELF_LID }, 'report.pdf', PDF);
  assert.equal(r.status, 200);
  const c = h.sent[0].content;
  assert.ok(Buffer.from(c.document).equals(PDF));
  assert.equal(c.fileName, 'report.pdf');
  assert.equal(c.mimetype, 'application/pdf');
});

test('A FILE TO ANY OTHER CHAT IS STILL REFUSED (403)', async () => {
  const h = await harness();
  const r = await h.file({ jid: ALICE }, 'chart.png', PNG);
  assert.equal(r.status, 403);
  assert.equal((await r.json()).reason, 'not_own_chat');
  assert.equal(h.sent.length, 0);
});

// ---- typing (MS-17) ----

test('POST /presence shows typing in the own chat, and refuses any other chat or state', async () => {
  const h = await harness();
  assert.equal((await h.json('/presence', { jid: SELF_LID, state: 'composing' })).status, 200);
  assert.equal((await h.json('/presence', { jid: SELF_LID, state: 'paused' })).status, 200);
  assert.deepEqual(h.fake.presence, [{ state: 'composing', jid: SELF_LID }, { state: 'paused', jid: SELF_LID }]);
  assert.equal((await h.json('/presence', { jid: ALICE, state: 'composing' })).status, 403);
  assert.equal((await h.json('/presence', { jid: SELF_LID, state: 'online' })).status, 400);
  assert.equal(h.fake.presence.length, 2);
});

test('the reply and presence routes need the token like every other', async () => {
  const h = await harness();
  for (const p of ['/reply', '/presence']) {
    assert.equal((await fetch(`http://127.0.0.1:${h.app.server.address().port}${p}`, { method: 'POST' })).status, 401);
  }
  assert.equal(h.sent.length, 0);
});

// ---- missed while down (MS-19, AC-524) ----

const own = [SELF_LID, SELF_PN];
const msg = (jid, message, id = 'X1') => ({ key: { remoteJid: jid, id, fromMe: true }, messageTimestamp: 1790950000, message });

test('AN APPEND DELIVERY IN THE OWN CHAT IS COUNTED AS MISSED, never routed', () => {
  const m = classifyMissed({ msg: msg(SELF_LID, { conversation: 'are you there?' }), type: 'append', ownJids: own });
  assert.deepEqual(m, { type: 'missed', jid: SELF_LID, id: 'X1', ts: 1790950000 });
});

test('missed ignores a live delivery, another chat, Butler\'s own sends and marked text', () => {
  const base = { ownJids: own };
  assert.equal(classifyMissed({ ...base, msg: msg(SELF_LID, { conversation: 'live' }), type: 'notify' }), null);
  assert.equal(classifyMissed({ ...base, msg: msg(ALICE, { conversation: 'other chat' }), type: 'append' }), null);
  assert.equal(classifyMissed({ ...base, msg: msg(SELF_LID, { audioMessage: { ptt: true } }, newButlerId()), type: 'append' }), null);
  assert.equal(classifyMissed({ ...base, msg: msg(SELF_LID, { conversation: 'a reply' + MARKER }), type: 'append' }), null);
  assert.equal(classifyMissed({ ...base, msg: msg(SELF_LID, {}), type: 'append' }), null);
});

test('the socket emits missed items for own-chat appends, but not for the voice note it sent itself', async () => {
  const h = await harness();
  const events = [];
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
  const mine = await (await h.voice({ jid: SELF_LID, ptt: 'true' })).json();
  h.fake.last().emit('messages.upsert', upsert(SELF_LID, { audioMessage: { ptt: true } }, { fromMe: true, id: mine.messageId, type: 'append' }));
  const anHourAgo = Math.floor(Date.now() / 1000) - 3600;
  h.fake.last().emit('messages.upsert', upsert(SELF_LID, { conversation: 'while you were down' }, { fromMe: true, id: 'D1', type: 'append', ts: anHourAgo }));
  const deadline = Date.now() + 2000;
  while (events.length < 2 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events.map((e) => e.type), ['ready', 'missed']);
  assert.equal(events[1].id, 'D1');
  await reader.cancel();
  await pump.catch(() => {});
});
