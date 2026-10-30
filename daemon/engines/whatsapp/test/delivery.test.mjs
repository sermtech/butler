/**
 * Live or missed is decided PER MESSAGE in the owner's own chat, never from
 * the batch label Baileys puts on a `messages.upsert` (docs/messaging.md
 * MS-19, 6.4; the 2026-10-08 laptop defect: 11 voice notes, 7 answers).
 *
 * Baileys buffers events and flushes them as ONE upsert whose `type` is
 * the FIRST buffered message's (rc14 lib/Utils/event-buffer.js:572-575,
 * consolidateEvents: `const type = messageUpsertList[0].type`; rc14 also
 * flushes early when a differently typed upsert arrives, :108-124), and it
 * labels every message the server held while the socket was reconnecting
 * `append` (lib/Socket/messages-recv.js:1432:
 * `node.attrs.offline ? 'append' : 'notify'`).
 * So a voice note the owner sent while Butler was UP can arrive labelled
 * `append` (behind Butler's own reply echo, or after a socket blip), and
 * Butler's own voice-note echo can arrive labelled `notify`.
 *
 * Driven through the real sidecar (createApp) and its real /events stream;
 * Baileys is the fake from fakes.mjs. Nothing here reaches WhatsApp.
 */
import { test, after } from 'node:test';
import assert from 'node:assert';
import { createApp } from '../wa-http.js';
import { LIVE_LATE_S, ownChatType, newButlerId } from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, silent, SELF_LID, ALICE } from './fakes.mjs';

const TOKEN = 'test-token-0123456789';
const OGG = Buffer.concat([Buffer.from('OggS'), Buffer.alloc(60, 1)]);
const apps = [];
after(async () => { for (const a of apps) await a.close(); });

const nowS = () => Math.floor(Date.now() / 1000);

/** One own-chat message as the phone delivers it (`fromMe` in the self chat). */
const own = (id, message, ts = nowS()) => ({
  key: { remoteJid: SELF_LID, id, fromMe: true }, messageTimestamp: ts, message,
});
const voiceNote = (id, ts) => own(id, { audioMessage: { ptt: true, seconds: 4, mimetype: 'audio/ogg; codecs=opus' } }, ts);

async function harness() {
  const dir = tempDir('wa-delivery-');
  const fake = fakeBaileys();
  const app = createApp({ token: TOKEN, dir, baileys: fake.deps, log: silent });
  apps.push(app);
  await app.wa.startPairing();
  await new Promise((r) => app.server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const call = (p, init = {}) => fetch(base + p, { ...init, headers: { 'x-butler-token': TOKEN, ...(init.headers || {}) } });
  const events = [];
  const stream = await call('/events');
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
  const upsert = (type, ...messages) => fake.last().emit('messages.upsert', { type, messages });
  /** Butler's own voice reply (POST /reply, ptt): the socket records its id. */
  const replyVoice = async (quote) => {
    const form = new FormData();
    form.append('jid', SELF_LID);
    form.append('ptt', 'true');
    if (quote) form.append('quote', quote);
    form.append('files', new Blob([OGG]), 'voice.ogg');
    const r = await call('/reply', { method: 'POST', body: form });
    const body = await r.text();
    assert.equal(r.status, 200, body);
    return JSON.parse(body).messageId;
  };
  /** Every item after `ready`, once `n` arrived (or what came in 1 s). */
  const items = async (n) => {
    const deadline = Date.now() + 1000;
    while (events.length < n + 1 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 10));
    await new Promise((r) => setTimeout(r, 50));       // anything extra shows up too
    return events.slice(1);
  };
  const close = async () => { await reader.cancel(); await pump.catch(() => {}); };
  return { upsert, replyVoice, items, close, fake };
}

test('A LIVE VOICE NOTE FLUSHED BEHIND BUTLER\'S OWN REPLY ECHO (batch labelled append) IS ROUTED, NOT MISSED', async () => {
  const h = await harness();
  h.upsert('notify', voiceNote('N1'));
  const echo = await h.replyVoice('N1');
  // Baileys' buffer: the echo first (append), the owner's next note behind it.
  h.upsert('append', voiceNote(echo), voiceNote('N2'));
  const got = await h.items(2);
  assert.deepEqual(got.map((e) => [e.type, e.tag || null, e.id]),
    [['message', 'owner', 'N1'], ['message', 'owner', 'N2']]);
  assert.equal(got[1].media.kind, 'audio');
  await h.close();
});

test('a message the owner sent DURING A SOCKET BLIP (offline delivery, written after boot) is routed', async () => {
  const h = await harness();
  h.upsert('append', voiceNote('B1'), own('B2', { conversation: 'and this too' }));
  const got = await h.items(2);
  assert.deepEqual(got.map((e) => [e.type, e.tag || null, e.id]),
    [['message', 'owner', 'B1'], ['message', 'owner', 'B2']]);
  await h.close();
});

test('BUTLER\'S OWN VOICE-NOTE ECHO flushed behind a live message (batch labelled notify) is never routed', async () => {
  const h = await harness();
  const echo = await h.replyVoice();
  h.upsert('notify', voiceNote('N1'), voiceNote(echo));
  const got = await h.items(2);
  assert.deepEqual(got.map((e) => [e.type, e.tag || null, e.id]), [['message', 'owner', 'N1']]);
  await h.close();
});

test('MS-19 holds: written BEFORE this sidecar started, or delivered later than LIVE_LATE_S, is missed', async () => {
  const h = await harness();
  const before = nowS() - 3600;
  h.upsert('append', voiceNote('OLD', before));
  h.upsert('notify', own('OLD2', { conversation: 'an hour old' }, before));
  const got = await h.items(2);
  assert.deepEqual(got.map((e) => [e.type, e.id]), [['missed', 'OLD'], ['missed', 'OLD2']]);
  assert.ok(LIVE_LATE_S >= 60 && LIVE_LATE_S <= 3600, `LIVE_LATE_S is ${LIVE_LATE_S}`);
  await h.close();
});

test('ownChatType: per message, by when it was written and how late it came; other people keep the label', () => {
  const ownJids = [SELF_LID];
  const MINE = newButlerId();
  const at = (id, ts, jid = SELF_LID) => ({ key: { remoteJid: jid, id, fromMe: true }, messageTimestamp: ts });
  const base = { ownJids, liveSince: 1000, now: 1000 + 2 * LIVE_LATE_S };
  const late = 1000 + LIVE_LATE_S;                       // exactly LIVE_LATE_S old
  assert.equal(ownChatType({ ...base, msg: at('A', late), type: 'append' }), 'notify');
  assert.equal(ownChatType({ ...base, msg: at('B', late - 1), type: 'notify' }), 'append', 'delivered too late');
  assert.equal(ownChatType({ ...base, msg: at('C', 999), type: 'notify', now: 1001 }), 'append', 'before boot');
  assert.equal(ownChatType({ ...base, msg: at(MINE, late), type: 'notify' }), 'append', 'our own send');
  const theirs = (id, ts) => ({ key: { remoteJid: ALICE, id, fromMe: false }, messageTimestamp: ts });
  assert.equal(ownChatType({ ...base, msg: theirs('D', 1), type: 'notify' }), 'notify', 'someone else keeps the label');
  assert.equal(ownChatType({ ...base, msg: theirs('E', 1), type: 'append' }), 'append', 'someone else keeps the label');
  // RV-23 (AC-610): what the USER wrote in another chat is labelled per message
  // too -- a takeover flushed behind Butler's own `append` echo is live.
  assert.equal(ownChatType({ ...base, msg: at('F', late, ALICE), type: 'append' }), 'notify', 'a takeover in an append batch');
  assert.equal(ownChatType({ ...base, msg: at('G', 999, ALICE), type: 'notify', now: 1001 }), 'append', 'typed before boot');
  assert.equal(ownChatType({ ...base, msg: at(MINE, late, ALICE), type: 'notify' }), 'append', 'our own guest reply');
});
