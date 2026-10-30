/**
 * Guest replies, the sidecar's half (docs/messaging.md MS-14a, MS-14b; AC-535,
 * AC-537). Phase M9.
 *
 * End to end through the HTTP surface: the daemon's config arrives on POST
 * /config, Baileys delivers messages, the /events stream carries what is
 * answered, and POST /guest-reply is the door a guest answer goes out of. That
 * door decides the chat ITSELF from the config at the moment of the reply: a
 * chat on the answer list, or a saved contact's one-to-one chat while
 * answer-everyone is on. Never the user's own chat, never anyone else. Every
 * text starts "Butler: " and ends with the marker; every outcome is audited.
 *
 * The socket is a fake: nothing here reaches WhatsApp.
 */
import { test, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { createApp } from '../wa-http.js';
import { MARKER, GUEST_PREFIX, isGuestChat } from '../whatsapp-inbound.js';
import { fakeBaileys, tempDir, silent, upsert, until, ME, ALICE, BOB, GROUP, SELF_LID, SELF_PN } from './fakes.mjs';

const TOKEN = 'test-token-0123456789';
const apps = [];
after(async () => { for (const a of apps) await a.close(); });

async function harness() {
  const dir = tempDir('wa-guest-');
  const fake = fakeBaileys();
  const app = createApp({ token: TOKEN, dir, baileys: fake.deps, log: silent });
  apps.push(app);
  app.store.ingestContacts([{ id: ALICE, name: 'Alice Alvarez' }]);
  await app.wa.startPairing();
  await new Promise((r) => app.server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const call = (p, init = {}) => fetch(base + p, { ...init, headers: { 'x-butler-token': TOKEN, ...(init.headers || {}) } });
  const json = (p, body) => call(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const audit = () => {
    const f = path.join(dir, 'sent.jsonl');
    return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  };
  // The /events stream, as the daemon reads it: one JSON item per line.
  const items = [];
  const events = await call('/events');
  const reader = events.body.getReader();
  let buffered = '';
  (async () => {
    for (;;) {
      let chunk;
      try { chunk = await reader.read(); } catch { return; }
      if (chunk.done) return;
      buffered += Buffer.from(chunk.value).toString('utf8');
      let at;
      while ((at = buffered.indexOf('\n')) >= 0) {
        const line = buffered.slice(0, at);
        buffered = buffered.slice(at + 1);
        if (line.trim()) items.push(JSON.parse(line));
      }
    }
  })();
  await until(() => items.length >= 1, 'the ready item');
  const emit = (payload) => fake.last().emit('messages.upsert', payload);
  return { app, fake, dir, call, json, audit, items, emit, sent: fake.sent };
}

const routed = (h) => h.items.filter((i) => i.type !== 'ready');
const settle = () => new Promise((r) => setTimeout(r, 50));

// ---- AC-535: who is answered, end to end through /config and /events ----

test('AC-535 END TO END: a listed DM on every message, a listed group only on the word or a reply to Butler', async () => {
  const h = await harness();
  assert.equal((await h.json('/config', { answer: [ALICE, GROUP], trigger: 'Butler' })).status, 200);
  h.emit(upsert(ALICE, { conversation: 'first, no trigger word' }, { id: 'A1' }));
  h.emit(upsert(ALICE, { conversation: 'second' }, { id: 'A2' }));
  h.emit(upsert(GROUP, { conversation: 'just chatting' }, { participant: BOB, id: 'G0' }));
  h.emit(upsert(GROUP, {
    extendedTextMessage: { text: '@15555550100 are you coming?', contextInfo: { mentionedJid: [ME.id, SELF_PN] } },
  }, { participant: BOB, id: 'G1' }));
  h.emit(upsert(GROUP, { conversation: 'Butler, when is lunch?' }, { participant: BOB, id: 'G2' }));
  h.emit(upsert(GROUP, {
    extendedTextMessage: { text: 'and dinner?', contextInfo: { participant: ME.id, quotedMessage: { conversation: 'Butler: noon' + MARKER } } },
  }, { participant: BOB, id: 'G3' }));
  h.emit(upsert(BOB, { conversation: 'Bob is not listed' }, { id: 'B1' }));
  await until(() => routed(h).length >= 4, 'four guest items');
  await settle();
  assert.deepEqual(routed(h).map((i) => [i.id, i.tag, i.trigger || null]), [
    ['A1', 'guest', null], ['A2', 'guest', null], ['G2', 'guest', 'word'], ['G3', 'guest', 'reply'],
  ], 'a mention of the user (G1) and plain chat (G0) are not triggers; Bob is not listed');
});

test('AC-535: answer-everyone is OFF by default, so a saved contact who is not listed gets nothing', async () => {
  const h = await harness();
  assert.equal(h.app.wa.getConfig().answer_everyone, false);
  h.emit(upsert(ALICE, { conversation: 'a saved contact writes' }));
  await settle();
  assert.deepEqual(routed(h), []);
  assert.equal((await h.json('/guest-reply', { jid: ALICE, text: GUEST_PREFIX + 'hi' })).status, 403);
});

// ---- AC-537: the guest door ----

test('A GUEST REPLY GOES ONLY INTO A LISTED CHAT, STARTS "Butler: " AND ENDS WITH THE MARKER', async () => {
  const h = await harness();
  await h.json('/config', { answer: [ALICE] });
  const r = await h.json('/guest-reply', { jid: ALICE, text: 'Butler: she is away until five.' });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).messageId, h.sent[0].opts.messageId);
  assert.equal(h.sent.length, 1);
  assert.equal(h.sent[0].jid, ALICE);
  assert.equal(h.sent[0].content.text, 'Butler: she is away until five.' + MARKER);
  assert.equal(h.sent[0].opts.quoted, undefined, 'a DM reply is not quoted');
  const line = h.audit().at(-1);
  assert.equal(line.ok, true);
  assert.equal(line.guest, true);
  assert.equal(line.jid, ALICE);
  assert.equal(line.text, undefined, 'the audit never carries the body');
});

test('the door refuses every chat Butler does not answer, audited, and sends nothing', async () => {
  const h = await harness();
  await h.json('/config', { answer: [ALICE] });
  for (const jid of [BOB, GROUP, SELF_LID, SELF_PN, 'status@broadcast']) {
    const r = await h.json('/guest-reply', { jid, text: 'Butler: hello' });
    assert.equal(r.status, 403, jid);
    assert.equal((await r.json()).reason, 'not_guest_chat');
  }
  assert.equal(h.sent.length, 0);
  assert.ok(h.audit().every((l) => l.guest === true && l.ok === false && l.reason === 'not_guest_chat'));
});

test('taking a chat off the list closes the door at once', async () => {
  const h = await harness();
  await h.json('/config', { answer: [ALICE] });
  assert.equal((await h.json('/guest-reply', { jid: ALICE, text: 'Butler: one' })).status, 200);
  await h.json('/config', { answer: [] });
  assert.equal((await h.json('/guest-reply', { jid: ALICE, text: 'Butler: two' })).status, 403);
  assert.equal(h.sent.length, 1);
});

test('a text without the prefix, or with nothing after it, is refused before anything is sent', async () => {
  const h = await harness();
  await h.json('/config', { answer: [ALICE] });
  for (const text of ['she is away', 'butler: lower case', 'Butler:', 'Butler:    ', '', 7]) {
    assert.equal((await h.json('/guest-reply', { jid: ALICE, text })).status, 400, String(text));
  }
  assert.equal(h.sent.length, 0);
});

test('answer-everyone opens the door to a SAVED contact\'s one-to-one chat only', async () => {
  const h = await harness();
  await h.json('/config', { answer_everyone: true });
  assert.equal((await h.json('/guest-reply', { jid: ALICE, text: 'Butler: hi Alice' })).status, 200);
  assert.equal((await h.json('/guest-reply', { jid: BOB, text: 'Butler: hi stranger' })).status, 403);
  assert.equal((await h.json('/guest-reply', { jid: GROUP, text: 'Butler: hi group' })).status, 403);
});

test('a group reply quotes the guest message it answers, from the live cache only', async () => {
  const h = await harness();
  await h.json('/config', { answer: [GROUP] });
  h.emit(upsert(GROUP, { conversation: 'Butler, is she free?' }, { participant: BOB, id: 'Q1' }));
  await until(() => routed(h).length === 1, 'the guest item');
  const r = await h.json('/guest-reply', { jid: GROUP, text: 'Butler: not today.', quote: 'Q1' });
  assert.equal(r.status, 200);
  assert.equal(h.sent[0].opts.quoted.key.id, 'Q1');
  assert.equal(h.sent[0].opts.quoted.key.participant, BOB);
  const unknown = await h.json('/guest-reply', { jid: GROUP, text: 'Butler: x', quote: 'NOPE' });
  assert.equal(unknown.status, 404);
  assert.equal(h.sent.length, 1);
});

test('isGuestChat is pure and reads the config it is given', () => {
  const base = { ownJids: [SELF_PN, SELF_LID], isKnownContact: (j) => j === ALICE };
  assert.equal(isGuestChat({ ...base, jid: ALICE, config: { answer: [] } }), false);
  assert.equal(isGuestChat({ ...base, jid: '15551230000:3@s.whatsapp.net', config: { answer: [ALICE] } }), true);
  assert.equal(isGuestChat({ ...base, jid: SELF_PN, config: { answer: [SELF_PN] } }), false, 'never the own chat');
  assert.equal(isGuestChat({ ...base, jid: ALICE, config: { answer_everyone: true } }), true);
  assert.equal(isGuestChat({ ...base, jid: GROUP, config: { answer_everyone: true } }), false, 'a group needs the list');
});
