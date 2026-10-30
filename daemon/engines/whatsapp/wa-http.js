/**
 * The sidecar's HTTP surface (MS-3, MS-10, MS-11, MS-12, MS-15), as a factory so
 * tests can run it on a fake socket. `wa-sidecar.mjs` is the process around it.
 *
 * EVERY request must carry `X-Butler-Token` equal to the per-boot token (401
 * otherwise): the daemon is the only client, and loopback alone is not a
 * boundary on a machine that runs a tailnet proxy.
 *
 * Routes are listed in the header of wa-sidecar.mjs. Bodies for /send are
 * an earlier prototype's: JSON `{recipient, text, confirm, send_enabled}` or multipart with
 * exactly one file (field `files`) plus string fields. `confirm` is true only
 * as JSON boolean `true` or the multipart string "true". The send switch
 * `send_enabled` is read from the request on EVERY send (JSON boolean, or the
 * strings "true"/"false" in a form), else the process default (env
 * BUTLER_WA_SEND, default ON, MS-12).
 *
 * Review fixes (docs/messaging.md 6.5):
 * - RV-7: once a message went out, a failing audit write is logged loudly and
 *   the answer is still "sent" -- a 500 there made the caller send it again.
 * - RV-11: items published while no /events subscriber is attached wait in a
 *   bounded buffer (EVENT_BUFFER_MAX, oldest dropped and counted) and follow
 *   the next subscriber's `ready`, which carries `dropped`.
 * - RV-12: /reply is limited to REPLY_RATE_PER_MINUTE (429 `rate_limited`):
 *   two programs answering each other on one number stop there.
 */
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import { openWhatsAppStore } from './whatsapp-store.js';
import { createWhatsApp, PRESENCE_STATES } from './whatsapp-socket.js';
import { createSendGuard } from './whatsapp-send-guard.js';
import { buildOutboundContent, buildVoiceNote } from './whatsapp-media.js';
import { MARKER, GUEST_PREFIX } from './whatsapp-inbound.js';

const MEDIA_KINDS = ['image', 'video', 'audio', 'document'];
const JSON_LIMIT = 1024 * 1024;
const MULTIPART_OVERHEAD = 1024 * 1024;

export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
/** RV-11: events kept while nobody listens. */
export const EVENT_BUFFER_MAX = 200;
/** RV-12: replies per rolling minute, attempts counted. */
export const REPLY_RATE_PER_MINUTE = 30;

/** Thrown for a request the client got wrong; becomes `status` + JSON body. */
class HttpError extends Error {
  constructor(status, body) {
    super(body.error || body.reason || String(status));
    this.status = status;
    this.body = { ok: false, ...body };
  }
}

function tokensEqual(a, b) {
  const x = crypto.createHash('sha256').update(String(a)).digest();
  const y = crypto.createHash('sha256').update(String(b)).digest();
  return crypto.timingSafeEqual(x, y);
}

/** Read the whole body, refusing (413) past `limit` bytes. */
async function readBody(req, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new HttpError(413, { error: 'request body too large' });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

/**
 * The token-checked server around one WhatsApp handle, with its records in
 * `dir` (session/, whatsapp.db, sent.jsonl: MS-4). `baileys` is injectable.
 */
export function createApp({
  token, dir, baileys, sendDefault = true, maxUploadBytes = MAX_UPLOAD_BYTES, reconnectDelayMs, albumSettleMs, log,
}) {
  if (!token) throw new Error('createApp: token is required');
  if (!dir) throw new Error('createApp: dir is required');
  const warn = log || ((...parts) => process.stderr.write(parts.map(String).join(' ') + '\n'));

  /** Live NDJSON subscribers of GET /events. */
  const subscribers = new Set();
  /** RV-11: lines published while nobody listened, oldest first. */
  const waiting = [];
  let dropped = 0;
  const publish = (item) => {
    const line = JSON.stringify(item) + '\n';
    if (subscribers.size === 0) {
      waiting.push(line);
      if (waiting.length > EVENT_BUFFER_MAX) { waiting.shift(); dropped += 1; }
      return;
    }
    for (const res of subscribers) res.write(line);
  };

  const store = openWhatsAppStore(path.join(dir, 'whatsapp.db'));
  const wa = createWhatsApp({
    dir, store, emit: publish, baileys, reconnectDelayMs, albumSettleMs, ...(log ? { log } : {}),
  });

  /**
   * RV-7: the audit line of a send that already went out. Its failure (disk
   * full, sent.jsonl held open) is logged, never turned into "not sent".
   */
  const auditSent = (write) => {
    try { write(); } catch (err) {
      warn(`[wa] AUDIT WRITE FAILED after a successful send; the message WAS sent: ${err.code || err.name}: ${err.message}`);
    }
  };

  /** RV-12: start times of recent /reply attempts. */
  const replyTimes = [];
  const replyAllowed = () => {
    const cutoff = Date.now() - 60_000;
    while (replyTimes.length && replyTimes[0] < cutoff) replyTimes.shift();
    if (replyTimes.length >= REPLY_RATE_PER_MINUTE) return false;
    replyTimes.push(Date.now());
    return true;
  };

  const guard = createSendGuard({
    store,
    auditPath: path.join(dir, 'sent.jsonl'),
    readSettings: () => ({ send_rate_per_minute: wa.getConfig().send_rate_per_minute }),
    getOwnJids: () => wa.ownJids(),
  });

  const asBool = (v, name, multipart) => {
    if (typeof v === 'boolean') return v;
    if (multipart && (v === 'true' || v === 'false')) return v === 'true';
    throw new HttpError(400, { error: `${name} must be a boolean` });
  };

  /** Parse the two /send body shapes into one request. */
  async function parseSend(req) {
    const contentType = req.headers['content-type'] || '';
    const multipart = contentType.startsWith('multipart/form-data');
    if (!multipart) {
      let body;
      try { body = JSON.parse((await readBody(req, JSON_LIMIT)).toString('utf8') || '{}'); } catch (err) {
        if (err instanceof HttpError) throw err;
        throw new HttpError(400, { error: 'body must be JSON' });
      }
      return {
        recipient: body.recipient,
        text: body.text,
        caption: body.caption,
        confirm: body.confirm === true,
        sendEnabled: body.send_enabled === undefined ? sendDefault : asBool(body.send_enabled, 'send_enabled', false),
        file: null,
      };
    }
    const raw = await readBody(req, maxUploadBytes + MULTIPART_OVERHEAD);
    let form;
    try {
      form = await new Response(raw, { headers: { 'content-type': contentType } }).formData();
    } catch {
      throw new HttpError(400, { error: 'malformed multipart body' });
    }
    const files = form.getAll('files').filter((f) => typeof f !== 'string');
    if (files.length > 1) throw new HttpError(400, { error: 'exactly one file per send' });
    const file = files[0] || null;
    if (file && file.size > maxUploadBytes) throw new HttpError(413, { error: 'file is over the upload limit' });
    const field = (k) => (typeof form.get(k) === 'string' ? form.get(k) : undefined);
    return {
      recipient: field('recipient'),
      text: field('text'),
      caption: field('caption'),
      confirm: field('confirm') === 'true',
      sendEnabled: field('send_enabled') === undefined ? sendDefault : asBool(field('send_enabled'), 'send_enabled', true),
      file: file && { buffer: Buffer.from(await file.arrayBuffer()), name: file.name, size: file.size },
    };
  }

  /** POST /send: the one door to the socket; guard order per MS-11. */
  async function send(req) {
    const { recipient, text, caption, confirm, sendEnabled, file } = await parseSend(req);
    if (!recipient || typeof recipient !== 'string') throw new HttpError(400, { error: 'recipient is required' });
    if (!file && (!text || typeof text !== 'string' || !text.trim())) {
      // An empty body is a caller bug, not a blank message the user wanted.
      throw new HttpError(400, { error: 'text is required' });
    }

    // Shaped BEFORE the guard so a refusal is audited with the file's kind.
    // MS-15: every outbound text ends with the marker. A file with no caption
    // carries no text and so no marker.
    const marked = (s) => (s.endsWith(MARKER) ? s : s + MARKER);
    let content;
    let meta;
    if (!file) {
      content = { text: marked(text) };
      meta = { text };
    } else {
      const cap = caption || text || undefined;
      try {
        content = buildOutboundContent({ buffer: file.buffer, fileName: file.name, caption: cap && marked(cap) });
      } catch (err) {
        throw new HttpError(400, { error: err.message });
      }
      meta = { text: cap, kind: MEDIA_KINDS.find((k) => content[k]), fileName: file.name, bytes: file.size };
    }

    const verdict = guard.check({ recipient, confirm }, { send_enabled: sendEnabled });
    if (!verdict.ok) {
      guard.record(verdict, meta);
      const status = verdict.reason === 'confirmation_required' || verdict.reason === 'ambiguous_recipient' ? 409 : 403;
      throw new HttpError(status, {
        reason: verdict.reason, jid: verdict.jid, name: verdict.name, candidates: verdict.candidates, limit: verdict.limit,
      });
    }

    if (!wa.sock?.user) throw new HttpError(503, { error: 'WhatsApp is not connected -- link a device first' });

    let sent;
    try {
      sent = await wa.sendMessage(verdict.jid, content);
    } catch (err) {
      // Recorded as a FAILURE: reporting success on a throw is how "I sent it"
      // becomes false.
      guard.record({ ...verdict, ok: false, reason: 'send_failed' }, { ...meta, error: err.message });
      throw new HttpError(502, { reason: 'send_failed', error: err.message });
    }
    auditSent(() => guard.record(verdict, meta));
    return { ok: true, jid: verdict.jid, name: verdict.name, messageId: sent?.key?.id ?? null };
  }

  /** A JSON body, or a 400 naming the problem. */
  async function readJson(req) {
    try {
      return JSON.parse((await readBody(req, JSON_LIMIT)).toString('utf8') || '{}');
    } catch (err) {
      if (err instanceof HttpError) throw err;
      throw new HttpError(400, { error: 'body must be JSON' });
    }
  }

  /** The own chat or a 403: the reply door and typing reach nothing else. */
  function ownChat(jid) {
    if (!wa.sock?.user) throw new HttpError(503, { error: 'WhatsApp is not connected -- link a device first' });
    if (!wa.isOwnChat(jid)) throw new HttpError(403, { reason: 'not_own_chat', error: 'replies go to your own chat only' });
    return jid;
  }

  /**
   * POST /reply (MS-17, MS-18a): an answer to the user's OWN message, in the
   * user's own chat. JSON `{jid, text, quote}`, or multipart `{jid, quote,
   * ptt: "true"}` with one OGG/Opus file sent as a push-to-talk voice note,
   * or multipart `{jid, quote, caption?}` with one file WITHOUT ptt: sent as an
   * image, video or audio where WhatsApp renders it inline, else a document
   * (docs/rich-chat.md O-1).
   * Not the send guard (MS-10): the route itself refuses every other chat, and
   * the reply is audited (`guard.note`) without charging the rate limit.
   * `quote` names the triggering message; unknown is a 404, never a silent
   * unquoted send.
   */
  async function reply(req) {
    const contentType = req.headers['content-type'] || '';
    let jid; let text; let quote; let file = null; let ptt = false; let fileName; let caption;
    if (contentType.startsWith('multipart/form-data')) {
      const raw = await readBody(req, maxUploadBytes + MULTIPART_OVERHEAD);
      let form;
      try {
        form = await new Response(raw, { headers: { 'content-type': contentType } }).formData();
      } catch {
        throw new HttpError(400, { error: 'malformed multipart body' });
      }
      const field = (k) => (typeof form.get(k) === 'string' ? form.get(k) : undefined);
      const files = form.getAll('files').filter((f) => typeof f !== 'string');
      if (files.length !== 1) throw new HttpError(400, { error: 'exactly one file per reply' });
      jid = field('jid');
      quote = field('quote');
      ptt = field('ptt') === 'true';
      caption = field('caption');
      fileName = files[0].name;
      file = Buffer.from(await files[0].arrayBuffer());
    } else {
      const body = await readJson(req);
      ({ jid, text, quote } = body);
    }
    if (typeof jid !== 'string') throw new HttpError(400, { error: 'jid is required' });
    if (quote !== undefined && (typeof quote !== 'string' || !quote)) throw new HttpError(400, { error: 'quote must be a message id' });
    let content;
    let meta;
    if (file) {
      if (ptt) {
        try { content = buildVoiceNote(file); } catch (err) { throw new HttpError(400, { error: err.message }); }
        meta = { kind: 'voice', fileName: 'voice.ogg', bytes: file.length };
      } else {
        try { content = buildOutboundContent({ buffer: file, fileName, caption }); } catch (err) { throw new HttpError(400, { error: err.message }); }
        meta = { kind: 'file', fileName, bytes: file.length };
      }
    } else {
      if (typeof text !== 'string' || !text.trim()) throw new HttpError(400, { error: 'text is required' });
      content = { text: text.endsWith(MARKER) ? text : text + MARKER };
      meta = { text };
    }
    ownChat(jid);
    let opts;
    if (quote) {
      const quoted = wa.quoted(jid, quote);
      if (!quoted) throw new HttpError(404, { reason: 'unknown_quote', error: `no message ${quote} in that chat to quote` });
      opts = { quoted };
    }
    if (!replyAllowed()) {
      guard.note({ ok: false, jid, reason: 'rate_limited' }, meta);
      throw new HttpError(429, {
        reason: 'rate_limited', limit: REPLY_RATE_PER_MINUTE, error: `more than ${REPLY_RATE_PER_MINUTE} replies in a minute`,
      });
    }
    let sent;
    try {
      sent = await wa.sendMessage(jid, content, opts);
    } catch (err) {
      guard.note({ ok: false, jid, reason: 'send_failed' }, { ...meta, error: err.message });
      throw new HttpError(502, { reason: 'send_failed', error: err.message });
    }
    auditSent(() => guard.note({ ok: true, jid }, meta));
    return { ok: true, jid, messageId: sent?.key?.id ?? null };
  }

  /**
   * POST /guest-reply `{jid, text, quote?}` (MS-14b, M9): Butler's answer to a
   * GUEST, text only (guests neither send nor receive files, MS-18b). The
   * route decides the chat itself, from the config at this moment: only a
   * chat on the answer list, or a saved contact's one-to-one chat while
   * answer-everyone is on (`isGuestChat`); 403 `not_guest_chat` otherwise,
   * audited. The text must start "Butler: " (400 otherwise). `quote` is
   * looked up in the live cache only (404 `unknown_quote`). Every outcome is
   * one audit line marked `guest`; not charged to the per-minute send rate
   * (the daemon holds a guest chat to 20 replies an hour).
   */
  async function guestReply(req) {
    const { jid, text, quote } = await readJson(req);
    if (typeof jid !== 'string') throw new HttpError(400, { error: 'jid is required' });
    if (typeof text !== 'string' || !text.startsWith(GUEST_PREFIX) || !text.slice(GUEST_PREFIX.length).trim()) {
      throw new HttpError(400, { error: `a guest reply is text that starts with "${GUEST_PREFIX}"` });
    }
    if (quote !== undefined && (typeof quote !== 'string' || !quote)) throw new HttpError(400, { error: 'quote must be a message id' });
    if (!wa.sock?.user) throw new HttpError(503, { error: 'WhatsApp is not connected -- link a device first' });
    const meta = { text };
    if (!wa.isGuestChat(jid)) {
      guard.note({ ok: false, jid, reason: 'not_guest_chat' }, meta, { guest: true });
      throw new HttpError(403, { reason: 'not_guest_chat', error: 'that chat is not one Butler answers' });
    }
    let opts;
    if (quote) {
      const quoted = wa.recentMessage(jid, quote);
      if (!quoted) throw new HttpError(404, { reason: 'unknown_quote', error: `no message ${quote} in that chat to quote` });
      opts = { quoted };
    }
    const content = { text: text.endsWith(MARKER) ? text : text + MARKER };
    let sent;
    try {
      sent = await wa.sendMessage(jid, content, opts);
    } catch (err) {
      guard.note({ ok: false, jid, reason: 'send_failed' }, { ...meta, error: err.message }, { guest: true });
      throw new HttpError(502, { reason: 'send_failed', error: err.message });
    }
    auditSent(() => guard.note({ ok: true, jid }, meta, { guest: true }));
    return { ok: true, jid, messageId: sent?.key?.id ?? null };
  }

  /** POST /presence `{jid, state}`: "typing" in the own chat (MS-17). */
  async function presence(req) {
    const { jid, state } = await readJson(req);
    if (!PRESENCE_STATES.includes(state)) {
      throw new HttpError(400, { error: `state must be one of ${PRESENCE_STATES.join(', ')}` });
    }
    ownChat(jid);
    try { await wa.presence(jid, state); } catch (err) {
      throw new HttpError(502, { reason: 'presence_failed', error: err.message });
    }
    return { ok: true };
  }

  async function route(req, res, url) {
    const m = req.method;
    const p = url.pathname;
    const json = (status, body) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(body));
    };

    if (p === '/pairing') {
      if (m === 'GET') return json(200, await wa.pairingView());
      if (m === 'POST') { await wa.startPairing(); return json(200, await wa.pairingView()); }
      if (m === 'DELETE') {
        if (url.searchParams.get('unlink') === 'true') await wa.unlink(); else wa.cancelPairing();
        return json(200, await wa.pairingView());
      }
    }
    if (p === '/status' && m === 'GET') return json(200, wa.status());
    if (p === '/config' && m === 'POST') {
      let body;
      try { body = JSON.parse((await readBody(req, JSON_LIMIT)).toString('utf8') || '{}'); } catch (err) {
        if (err instanceof HttpError) throw err;
        throw new HttpError(400, { error: 'body must be JSON' });
      }
      try { return json(200, { ok: true, config: wa.setConfig(body) }); } catch (err) {
        throw new HttpError(400, { error: err.message });
      }
    }
    if (p === '/send' && m === 'POST') return json(200, await send(req));
    if (p === '/reply' && m === 'POST') return json(200, await reply(req));
    if (p === '/guest-reply' && m === 'POST') return json(200, await guestReply(req));
    if (p === '/presence' && m === 'POST') return json(200, await presence(req));
    if (p.startsWith('/media/') && m === 'GET') {
      let file;
      try { file = await wa.download(decodeURIComponent(p.slice('/media/'.length))); } catch (err) {
        const status = { unknown_media: 404, too_large: 413, not_connected: 503 }[err.code] || 502;
        throw new HttpError(status, { reason: err.code || 'download_failed', error: err.message, bytes: err.bytes });
      }
      res.writeHead(200, {
        'content-type': file.mimetype,
        'content-length': file.buffer.length,
        'x-butler-media-kind': file.kind,
        ...(file.fileName ? { 'x-butler-file-name': encodeURIComponent(file.fileName) } : {}),
      });
      return res.end(file.buffer);
    }
    if (p === '/events' && m === 'GET') {
      res.writeHead(200, { 'content-type': 'application/x-ndjson', 'cache-control': 'no-cache' });
      // RV-11: what arrived while nobody listened follows `ready`, in order.
      res.write(JSON.stringify({ type: 'ready', dropped }) + '\n');
      if (dropped) warn(`[wa] ${dropped} inbound items were dropped while no /events subscriber was attached`);
      for (const line of waiting.splice(0)) res.write(line);
      dropped = 0;
      subscribers.add(res);
      req.on('close', () => subscribers.delete(res));
      return undefined;
    }
    throw new HttpError(404, { error: `no route for ${m} ${p}` });
  }

  const server = http.createServer(async (req, res) => {
    try {
      if (!tokensEqual(req.headers['x-butler-token'] || '', token)) {
        throw new HttpError(401, { error: 'missing or wrong X-Butler-Token' });
      }
      await route(req, res, new URL(req.url, 'http://127.0.0.1'));
    } catch (err) {
      if (res.headersSent) return res.end();
      const e = err instanceof HttpError ? err : new HttpError(500, { error: err.message });
      res.writeHead(e.status, { 'content-type': 'application/json', ...(e.status === 413 ? { connection: 'close' } : {}) });
      return res.end(JSON.stringify(e.body));
    }
    return undefined;
  });

  return {
    server,
    wa,
    store,
    guard,
    /** Stop the socket, end every /events stream, close the listener and the archive. */
    async close() {
      wa.stop();
      for (const res of subscribers) res.end();
      subscribers.clear();
      server.closeAllConnections?.();
      await new Promise((resolve) => server.close(resolve));
      store.close();
    },
  };
}
