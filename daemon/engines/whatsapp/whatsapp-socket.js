/**
 * The Baileys socket, ported from an earlier prototype's channels/whatsapp.js (MS-1, MS-5,
 * MS-6, MS-7). It owns the connection, the pairing state, the archive feed and
 * the inbound stream; it knows nothing of chats, agents or speech.
 *
 * What it keeps exactly (each pinned by `test/socket-config.test.mjs`, AC-502):
 *   - `version` from `fetchLatestBaileysVersion()`, no fallback: a stale
 *     version is refused with 405 BEFORE any QR. That function never throws:
 *     on any failure it returns the BUNDLED version with `isLatest: false`
 *     (rc14 lib/Utils/generics.js:179-212, fetchLatestBaileysVersion: the
 *     catch at :206-211), so `isLatest` is checked and a failed
 *     fetch is a pairing error the page shows (RV-5).
 *   - `markOnlineOnConnect: false`: default true silences the user's phone.
 *   - `syncFullHistory: false` WITH `shouldSyncHistoryMessage`: rc.9 dropped
 *     EVERY history sync (contacts included) when the filter was absent; rc14's
 *     default filter is the same one Butler passes (lib/Defaults/index.js:65-67,
 *     `syncType !== FULL`), but it is passed explicitly so this never hangs on
 *     a Baileys default. The initial sync arrives once per link.
 *   - `getMessage` from the archive, or recipients see "This message took a
 *     while".
 *   - Archiving on its own `messages.upsert` subscription, separate from the
 *     routing one: the archive keeps every type and sender.
 *   - `startPairing` is idempotent: never a second socket.
 *
 * What differs from an earlier prototype:
 *   - The device is named 'Butler' (MS-5).
 *   - MS-6: 401 (unlinked from the phone), 440 (the same session opened
 *     elsewhere) and 403 (forbidden, RV-5) are TERMINAL and never loop; 515
 *     (restart required, normal after a scan) reconnects at once; any other
 *     close, and a reconnect that fails, retries after 3 s doubling to 5 min,
 *     reset when a connection opens (RV-5).
 *   - Routing is `classifyInbound` (whatsapp-inbound.js): events tagged owner,
 *     guest or takeover, handed to `emit`; the sidecar forwards them on
 *     /events. No agent, router or prompt lives here.
 *   - Media is not downloaded on arrival: the event carries a media id and the
 *     daemon fetches the bytes (GET /media/<id>); transcription is the
 *     daemon's, not the sidecar's.
 *   - MS-19: live or missed in the own chat is decided per MESSAGE by
 *     `ownChatType` (written after this sidecar started, delivered within
 *     LIVE_LATE_S), never by Baileys' batch label; a missed one is emitted
 *     as `missed`, never routed; a Butler's own sends (BUTLER_ID_PREFIX, which
 *     every send here carries, RV-12) are neither.
 *   - RV-4: a message id reaches the daemon at most once, across reconnects
 *     and sidecar restarts (the archive's `routed` table).
 *   - RV-8: an album's files reach the daemon as ONE item (whatsapp-album.js).
 *   - RV-6: an archive lock never drops a routed message: a lookup that fails
 *     is logged and answered "no", a routed-id write that fails is logged and
 *     the message routed anyway.
 *   - RV-10: Baileys' own logger prints text only (`makeTextLogger`).
 *   - Replies quote the triggering message: routed messages are kept in a
 *     bounded cache, and the archive answers for anything older (`quoted`).
 */
import fs from 'fs';
import path from 'path';
import crypto from 'node:crypto';
import {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  proto,
} from '@whiskeysockets/baileys';
import qrcode from 'qrcode';
import { IDLE_PAIRING, reducePairing, describePairing, phoneFromJid } from './whatsapp-pairing.js';
import { unwrapMessage } from './whatsapp-store.js';
import {
  classifyInbound, classifyMissed, ownChatType, ownJidsOf, mediaOf, declaredBytes, bareJid, MAX_INBOUND_BYTES,
  isGuestChat, newButlerId, albumParentOf, albumSizeOf,
} from './whatsapp-inbound.js';
import { createAlbumGate, ALBUM_SETTLE_MS } from './whatsapp-album.js';
import { textOnly } from './wa-console.js';

const MEDIA_CACHE_MAX = 200;
/** Routed messages kept to be quoted: bounded. */
const RECENT_MAX = 200;
/** RV-5: the first retry waits this long, doubling to the cap. */
export const RECONNECT_BASE_MS = 3000;
export const RECONNECT_MAX_MS = 5 * 60 * 1000;

/** The wait before retry number `failures` (0-based): base doubling, capped. */
export const reconnectDelay = (failures, base = RECONNECT_BASE_MS, max = RECONNECT_MAX_MS) =>
  Math.min(base * 2 ** Math.min(failures, 30), max);
/** The two presence states a reply shows (MS-17). */
export const PRESENCE_STATES = Object.freeze(['composing', 'paused']);

/** Default inbound settings (MS-14a): nobody is answered until the user says so. */
export const DEFAULT_CONFIG = Object.freeze({ answer: [], answer_everyone: false, trigger: 'Butler' });

/**
 * Validate a POST /config body. Fail fast: a malformed list must not quietly
 * widen or narrow who Butler answers.
 * @param {any} body
 */
export function parseConfig(body) {
  const bad = (m) => { throw new Error(`config: ${m}`); };
  if (!body || typeof body !== 'object' || Array.isArray(body)) bad('body must be an object');
  const out = {};
  if ('answer' in body) {
    if (!Array.isArray(body.answer) || !body.answer.every((j) => typeof j === 'string' && j.includes('@'))) {
      bad('answer must be a list of JIDs');
    }
    out.answer = body.answer;
  }
  if ('answer_everyone' in body) {
    if (typeof body.answer_everyone !== 'boolean') bad('answer_everyone must be a boolean');
    out.answer_everyone = body.answer_everyone;
  }
  if ('trigger' in body) {
    if (typeof body.trigger !== 'string' || !body.trigger.trim()) bad('trigger must be a non-empty string');
    out.trigger = body.trigger.trim();
  }
  if ('send_rate_per_minute' in body) {
    const n = body.send_rate_per_minute;
    if (!Number.isInteger(n) || n < 1 || n > 60) bad('send_rate_per_minute must be an integer 1..60');
    out.send_rate_per_minute = n;
  }
  return out;
}

/**
 * @param {object} opts
 * @param {string} opts.dir - records directory (session/ lives inside it)
 * @param {ReturnType<import('./whatsapp-store.js').openWhatsAppStore>} opts.store
 * @param {(event: object) => void} [opts.emit] - inbound items for the daemon
 * @param {object} [opts.baileys] - injectable pieces of Baileys, so the socket
 *   options can be asserted without opening a connection to WhatsApp
 * @param {number} [opts.reconnectDelayMs] - the first retry's wait (RV-5)
 * @param {number} [opts.maxReconnectDelayMs] - the cap it doubles to
 * @param {number} [opts.albumSettleMs] - how long an album waits for its next file
 * @param {(...a: any[]) => void} [opts.log] - never given message content
 */
export function createWhatsApp({
  dir, store, emit = () => {}, baileys = {}, reconnectDelayMs = RECONNECT_BASE_MS,
  maxReconnectDelayMs = RECONNECT_MAX_MS, albumSettleMs = ALBUM_SETTLE_MS, log = console.error,
}) {
  const createSocket = baileys.makeWASocket || makeWASocket;
  const fetchVersion = baileys.fetchLatestBaileysVersion || fetchLatestBaileysVersion;
  const downloadMedia = baileys.downloadMediaMessage || downloadMediaMessage;
  const sessionPath = path.join(dir, 'session');

  let config = { ...DEFAULT_CONFIG, send_rate_per_minute: undefined };
  let stopped = false;
  let sock = null;
  let reconnectTimer = null;
  /** Retries since a connection last opened (RV-5 backoff). */
  let failures = 0;
  let pairing = { ...IDLE_PAIRING };
  const applyPairing = (event) => { pairing = reducePairing(pairing, event, Date.now()); };

  /** id -> { msg, inner }: what GET /media/<id> downloads. Bounded, oldest out. */
  const mediaCache = new Map();
  function cacheMedia(msg, inner) {
    const id = crypto.randomUUID();
    mediaCache.set(id, { msg, inner });
    if (mediaCache.size > MEDIA_CACHE_MAX) mediaCache.delete(mediaCache.keys().next().value);
    return id;
  }

  const ownJids = () => ownJidsOf(sock?.user);
  const isOwnChat = (jid) => typeof jid === 'string' && jid.includes('@')
    && !jid.endsWith('@g.us') && ownJids().includes(bareJid(jid));

  /** `jid|id` -> the routed WAMessage, so a reply can quote it (MS-17). */
  const recent = new Map();
  const remember = (msg) => {
    recent.set(`${msg.key.remoteJid}|${msg.key.id}`, msg);
    if (recent.size > RECENT_MAX) recent.delete(recent.keys().next().value);
  };
  /** When this sidecar started, epoch s: own-chat messages written before it
   *  were sent while Butler was down (MS-19, `ownChatType`). */
  const liveSince = Math.floor(Date.now() / 1000);

  /** RV-6: a lookup the archive cannot answer (locked past its busy timeout)
   *  is "not known", logged; it never throws out of routing. */
  const knownContact = (j) => {
    try { return store.isKnownContact(j); } catch (err) {
      log('[wa] contact lookup failed; treated as unknown:', err.message);
      return false;
    }
  };

  /**
   * Hand one item to the daemon, at most once per message id (RV-4). `ids`
   * are the messages it stands for (several for an album). If the routed-id
   * write fails (RV-6) the item still goes: a lost message is worse than a
   * possible repeat.
   */
  function deliver(item, ids) {
    const jid = item.jid;
    let fresh = ids.length === 0;
    for (const id of ids) {
      try {
        if (store.claimRouted(jid, id)) fresh = true;
      } catch (err) {
        log('[wa] could not record a routed id; routing anyway:', err.message);
        fresh = true;
      }
    }
    if (!fresh) {
      log(`[wa] ${item.type} ${ids.join(',')} was already routed; dropped the redelivery`);
      return;
    }
    emit(item);
  }
  const albums = createAlbumGate({ emit: deliver, settleMs: albumSettleMs });

  /** Was this message already handed on? A failing read is "no" (RV-6). */
  const routedBefore = (jid, id) => {
    try { return store.isRouted(jid, id); } catch (err) {
      log('[wa] routed-id lookup failed:', err.message);
      return false;
    }
  };

  /** Retry after the backoff wait (RV-5); never while stopped. */
  function scheduleReconnect(why) {
    if (stopped) return;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    const delay = reconnectDelay(failures, reconnectDelayMs, maxReconnectDelayMs);
    failures += 1;
    log(`[wa] ${why}; reconnecting in ${delay / 1000} s`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      // 'connecting' while it runs, so POST /pairing returns this attempt
      // instead of opening a second socket (AC-504).
      applyPairing({ type: 'start' });
      connect().catch((err) => {
        applyPairing({ type: 'error', message: err.message });
        scheduleReconnect('reconnect failed');
      });
    }, delay);
    reconnectTimer.unref?.();
  }

  /**
   * Open a socket. Called for the first link, for a saved session at boot, and
   * for every reconnect; the previous socket is never reused.
   */
  async function connect() {
    if (stopped) return null;
    fs.mkdirSync(sessionPath, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(sessionPath);

    // THE CLIENT VERSION IS NOT OPTIONAL (an earlier prototype, measured 2026-08-17): the
    // bundled version goes stale and WhatsApp answers 405 before any QR.
    // Deliberately no fallback to the bundled value -- and Baileys' fetch
    // falls back by itself, flagged only by `isLatest: false` (RV-5).
    const { version, isLatest, error } = await fetchVersion();
    if (isLatest !== true) {
      throw new Error('Could not fetch the current WhatsApp Web version'
        + `${error?.message ? ` (${textOnly([error.message])})` : ''}; `
        + 'not connecting with the bundled one, which WhatsApp refuses.');
    }
    if (stopped) return null;

    const mine = createSocket({
      version,
      auth: state,
      // MS-5: the name in the phone's Linked devices list.
      browser: ['Butler', 'Chrome', '127.0.0'],
      // THE USER'S PHONE MUST KEEP RINGING: the Baileys default (true) makes
      // this session the foreground device and silences the phone.
      markOnlineOnConnect: false,
      syncFullHistory: false,
      // Explicit beside syncFullHistory: rc.9 substituted `() => !!syncFullHistory`
      // when it was absent and dropped EVERY history sync, contacts too; rc14
      // defaults to this same filter (lib/Defaults/index.js:65-67).
      shouldSyncHistoryMessage: (m) => m.syncType !== proto.HistorySync.HistorySyncType.FULL,
      // Re-encrypt a failed decrypt from the archive; unknown key => undefined.
      getMessage: async (key) => store.getMessage(key),
      logger: makeTextLogger(log),
    });
    sock = mine;
    // Events of a socket that is no longer current (a late close from the one
    // just replaced) must not drive reconnects or state.
    const current = () => sock === mine;

    mine.ev.on('creds.update', saveCreds);

    // THE ARCHIVE IS FED ON EVERY CONNECTION. The initial history sync arrives
    // once per link and is not re-delivered on demand.
    mine.ev.on('messaging-history.set', (payload) => {
      try { store.ingestHistory(payload); } catch (err) { log('[wa] history failed to store:', err.message); }
    });
    // RV-6: a failed write is logged, never thrown into Baileys' emitter.
    const archive = (what, fn) => (payload) => {
      try { fn(payload); } catch (err) { log(`[wa] ${what} failed to store:`, err.message); }
    };
    mine.ev.on('contacts.upsert', archive('contacts', (c) => store.ingestContacts(c)));
    mine.ev.on('contacts.update', archive('contacts', (c) => store.ingestContacts(c)));
    mine.ev.on('chats.upsert', archive('chats', (c) => store.ingestChats(c)));
    mine.ev.on('chats.update', archive('chats', (c) => store.ingestChats(c)));
    // Archiving is SEPARATE from routing: every type and sender is kept.
    mine.ev.on('messages.upsert', ({ messages }) => {
      try { store.ingestMessages(messages); } catch (err) { log('[wa] message failed to store:', err.message); }
    });

    mine.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
      if (!current()) return;
      if (qr) {
        applyPairing({ type: 'qr', qr });
        log('[wa] QR issued, count', pairing.qrCount);
      }
      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        const replaced = statusCode === DisconnectReason.connectionReplaced;
        const forbidden = statusCode === DisconnectReason.forbidden;
        applyPairing({ type: 'close', statusCode, loggedOut, replaced, forbidden });
        if (loggedOut || replaced || forbidden) {
          // MS-6: terminal. A loop here would keep kicking the other program
          // (440), hammer a dead session (401) or a refusing server (403).
          log(`[wa] terminal close (code=${statusCode}); not reconnecting`);
          stopped = true;
          sock = null;
          return;
        }
        if (statusCode === DisconnectReason.restartRequired) {
          // MS-6: normal right after a scan. Reconnect AT ONCE and show it as
          // connecting rather than as a failure.
          applyPairing({ type: 'start' });
          connect().catch((err) => {
            applyPairing({ type: 'error', message: err.message });
            scheduleReconnect('reconnect failed');
          });
          return;
        }
        scheduleReconnect(`connection closed (code=${statusCode})`);
      }
      if (connection === 'open') {
        failures = 0;
        applyPairing({ type: 'open', id: mine.user?.id, name: mine.user?.name });
        log('[wa] connected');
      }
    });

    // Routing: SECOND messages.upsert subscription. Pure filter, then emit.
    mine.ev.on('messages.upsert', ({ messages, type }) => {
      if (!current()) return;
      for (const msg of messages) {
        let item;
        let parent = null;
        try {
          const jid = msg?.key?.remoteJid;
          // RV-4: redelivered after it was handed on -- archived, never routed twice.
          if (jid && msg.key.id && routedBefore(jid, msg.key.id)) continue;
          const size = albumSizeOf(unwrapMessage(msg.message));
          if (size) { albums.size(jid, msg.key.id, size); continue; }
          // Per MESSAGE, never the batch's label (docs/messaging.md 6.4).
          const kind = ownChatType({ msg, type, ownJids: ownJids(), liveSince, now: Math.floor(Date.now() / 1000) });
          const missed = classifyMissed({ msg, type: kind, ownJids: ownJids() });
          if (missed) { deliver(missed, [missed.id]); continue; }
          item = classifyInbound({ msg, type: kind, ownJids: ownJids(), config, isKnownContact: knownContact });
          if (item?.media) parent = albumParentOf(msg.message);
        } catch (err) {
          log('[wa] inbound filter failed:', err.message);
          continue;
        }
        if (!item) continue;
        if (item.media) item.media.id = cacheMedia(msg, unwrapMessage(msg.message));
        // Guest messages too: a group reply quotes the message it answers.
        if (item.tag === 'owner' || item.tag === 'guest') remember(msg);
        albums.offer(item, parent);
      }
    });

    return mine;
  }

  function closeSocket() {
    stopped = true;
    albums.flush();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    const old = sock;
    sock = null;
    try { old?.end(); } catch (err) { log('[wa] error closing:', err.message); }
  }

  /** Does a saved session exist? The sidecar reconnects at boot only if so. */
  const hasSession = () => fs.existsSync(path.join(sessionPath, 'creds.json'));

  /**
   * Open a connection and report the pairing state. Idempotent (AC-504): an
   * attempt already in flight is returned as-is, never a second socket, which
   * WhatsApp would count as another device.
   */
  async function startPairing() {
    if (['connecting', 'awaiting_scan', 'paired'].includes(pairing.status)) return pairing;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    // Logged out from the phone: those credentials are dead and would only
    // 401 again, so linking again starts from a clean session.
    if (pairing.status === 'logged_out') fs.rmSync(sessionPath, { recursive: true, force: true });
    stopped = false;
    failures = 0;
    applyPairing({ type: 'start' });
    try {
      await connect();
    } catch (err) {
      applyPairing({ type: 'error', message: err.message });
      // A saved link keeps trying (RV-5): a GitHub outage at boot must not
      // leave the account unlinked until someone opens the page.
      if (hasSession()) scheduleReconnect('connecting failed');
    }
    return pairing;
  }

  /** Disconnect: close the socket and keep the link (credentials stay). */
  function cancelPairing() {
    closeSocket();
    applyPairing({ type: 'stop' });
    return pairing;
  }

  /**
   * Unlink: log out of WhatsApp, then delete session/ (it IS the credential).
   * Logout needs the socket OPEN (it sends a node to WhatsApp and then ends the
   * socket itself, lib/Socket/socket.js:572-594), so it runs BEFORE
   * closeSocket(); after end() it fails and the phone keeps the device linked.
   * A failed logout is logged and the local teardown still happens.
   */
  async function unlink() {
    const old = sock;
    stopped = true;
    try { await old?.logout?.(); } catch (err) { log('[wa] logout failed:', err.message); }
    closeSocket();
    fs.rmSync(sessionPath, { recursive: true, force: true });
    applyPairing({ type: 'stop' });
    return pairing;
  }

  /** Download a cached inbound file. Errors carry `.code` for the HTTP layer. */
  async function download(id) {
    const entry = mediaCache.get(id);
    const fail = (code, message) => Object.assign(new Error(message), { code });
    if (!entry) throw fail('unknown_media', 'unknown or expired media id');
    if (!sock) throw fail('not_connected', 'WhatsApp is not connected');
    const { kind, media } = mediaOf(entry.inner);
    const declared = declaredBytes(media);
    if (declared != null && declared > MAX_INBOUND_BYTES) {
      throw Object.assign(fail('too_large', 'file is over the limit; not downloaded'), { bytes: declared });
    }
    let buffer;
    try {
      buffer = await downloadMedia({ ...entry.msg, message: entry.inner }, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage });
    } catch (err) {
      throw fail('download_failed', err.message);
    }
    return { buffer, kind, mimetype: media.mimetype || 'application/octet-stream', fileName: media.fileName || null };
  }

  return {
    get sock() { return sock; },
    store,
    hasSession,
    startPairing,
    cancelPairing,
    unlink,
    stop: closeSocket,
    getPairing: () => pairing,
    ownJids,
    getConfig: () => config,
    setConfig(body) { config = { ...config, ...parseConfig(body) }; return config; },
    download,
    /**
     * The one path to the socket; the guard (or the own-chat rule of /reply)
     * has already said yes. `opts` is Baileys' (`{quoted}`). Every send gets a
     * Butler message id (RV-12), so its echo -- and any other Butler's on this
     * number -- is never routed or "missed", voice notes included.
     */
    async sendMessage(jid, content, opts = {}) {
      if (!sock?.user) throw Object.assign(new Error('WhatsApp is not connected -- link a device first'), { code: 'not_connected' });
      return sock.sendMessage(jid, content, { ...opts, messageId: newButlerId() });
    },
    isOwnChat,
    /**
     * The message `id` in `jid` as Baileys quotes it: the live routed copy, else
     * the archive's content (the own chat's messages are all `fromMe`). null
     * when neither has it.
     */
    quoted(jid, id) {
      const live = recent.get(`${jid}|${id}`);
      if (live) return live;
      const message = store.getMessage({ remoteJid: jid, id });
      return message ? { key: { remoteJid: jid, id, fromMe: true }, message } : null;
    },
    /** May a guest reply go into `jid` now (MS-14a)? `isGuestChat` over the live config. */
    isGuestChat: (jid) => isGuestChat({ jid, ownJids: ownJids(), config, isKnownContact: knownContact }),
    /**
     * A routed message still in the live cache, or null. Guest replies quote
     * only from here: the archive's copy is rebuilt as the user's own
     * (`fromMe`), which a guest's message is not.
     */
    recentMessage: (jid, id) => recent.get(`${jid}|${id}`) || null,
    /** "typing" in a chat (MS-17). The caller has checked chat and state. */
    async presence(jid, state) {
      if (!sock?.user) throw Object.assign(new Error('WhatsApp is not connected -- link a device first'), { code: 'not_connected' });
      await sock.sendPresenceUpdate(state, jid);
    },
    /** A copy of the pairing state for GET /pairing, QR rendered to a PNG data URL. */
    async pairingView() {
      return {
        ok: true,
        state: pairing,
        view: describePairing(pairing),
        qrDataUrl: pairing.qr ? await qrcode.toDataURL(pairing.qr, { margin: 1, width: 320 }) : null,
      };
    },
    status() {
      return {
        ok: true,
        status: pairing.status,
        linked: !!sock?.user,
        jid: sock?.user?.id || null,
        lid: sock?.user?.lid || null,
        phone: phoneFromJid(sock?.user?.id),
        name: sock?.user?.name || null,
        ...store.counts(),
      };
    },
  };
}

/** Digit runs this long are phone numbers or JIDs: never printed (RV-10). */
const PHONE_RUN = /\d{7,}/g;
/** Field names printed as is; anything else (a JID used as a key) is '?'. */
const FIELD_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,40}$/;

/**
 * Baileys' pino-shaped logger, TEXT only (RV-10, F-18). Baileys calls
 * `logger.warn(obj, msg)` or `logger.warn(msg)` with objects such as
 * `{ key: fullMessage.key, err }` or `{ tag, attrs, content }`: printed, those
 * put binary nodes and message keys with phone numbers into butler.log. So
 * only the message string is kept (through wa-console.js textOnly, digit
 * runs masked), plus the NAMES of the object's fields. Only warn and up.
 */
export function makeTextLogger(log) {
  const noop = () => {};
  const line = (level) => (obj, msg) => {
    const words = typeof obj === 'string' ? obj : typeof msg === 'string' ? msg : '';
    const fields = obj && typeof obj === 'object' && !Array.isArray(obj)
      ? Object.keys(obj).map((k) => (FIELD_NAME.test(k) ? k : '?')) : [];
    const text = textOnly([words || (obj instanceof Error ? `[${obj.name}]` : '(no message)')]).replace(PHONE_RUN, '<n>');
    log(`[wa] baileys ${level}: ${text}${fields.length ? ` (fields: ${fields.join(', ')})` : ''}`);
  };
  const logger = {
    level: 'warn',
    trace: noop,
    debug: noop,
    info: noop,
    warn: line('warn'),
    error: line('error'),
    fatal: line('fatal'),
    child: () => logger,
  };
  return logger;
}
