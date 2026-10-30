/**
 * Persistent store for WhatsApp contacts, chats and messages.
 *
 * Baileys is a stateless socket: it emits events and remembers NOTHING between
 * restarts, and `makeInMemoryStore` was removed in v7. The contact list, the
 * chat list and the message history are therefore not capabilities to enable
 * but state this application has to accumulate itself -- from events
 * (`messaging-history.set`, `contacts.upsert`, `chats.upsert`,
 * `messages.upsert`) that the socket was already receiving and discarding.
 *
 * Storage is `node:sqlite`, which is built into Node 22 and needs no flag on
 * v22.17 (it prints one ExperimentalWarning at open). That keeps a searchable
 * archive out of a native dependency: `better-sqlite3` would add a build
 * toolchain on Windows, and a JSON file would have to be read whole to answer
 * "what did Alice say about dinner".
 *
 * PRIVACY: this file is a plaintext archive of the owner's private
 * conversations. It lives in the records directory the daemon passes the sidecar
 * (BUTLER_WA_DIR, `Paths.whatsapp`) and is never written to the repo. The sidecar
 * is its ONLY writer.
 */
import { DatabaseSync } from 'node:sqlite';
import fs from 'fs';
import path from 'path';
import { jidDigits } from './whatsapp-pairing.js';
// A cycle (inbound imports this file too), safe because the prefix is read only
// inside openWhatsAppStore, long after both modules finished loading.
import { BUTLER_ID_PREFIX } from './whatsapp-inbound.js';

/**
 * Pull the human-readable text out of a Baileys message.
 *
 * ONE DEFINITION SITE: `whatsapp-socket.js` imports this rather than keeping
 * its own copy. The two had already drifted apart once in this codebase's
 * history for the JID parser, and a text extractor that disagrees with itself
 * means the agent reads a different message than the one it replies to.
 *
 * @param {any} msg - Baileys WAMessage (or anything shaped like one)
 * @returns {string|null}
 */
export function extractMessageText(msg) {
  const m = unwrapMessage(msg?.message);
  return m.conversation
    || m.extendedTextMessage?.text
    || m.imageMessage?.caption
    || m.documentMessage?.caption
    || m.videoMessage?.caption
    || null;
}

// Each of these wraps `{ message: {...} }` around the real message. KEEP EQUAL to
// the Python twin: WRAPPERS in tools/wa.py (M4).
const WRAPPER_KEYS = [
  'associatedChildMessage',
  'ephemeralMessage',
  'viewOnceMessage',
  'viewOnceMessageV2',
  'viewOnceMessageV2Extension',
  'documentWithCaptionMessage',
  'editedMessage',
];
const MAX_WRAPPER_DEPTH = 3;

/**
 * Peel album-child, ephemeral, view-once, document-with-caption and edited
 * wrappers (up to three levels) and return the inner message object. The
 * routing handler classifies THIS, so an album photo or a view-once video is
 * seen as the plain imageMessage/videoMessage it is.
 *
 * @param {any} message - the `.message` of a Baileys WAMessage
 * @returns {any} the inner message object, `{}` when there is none
 */
export function unwrapMessage(message) {
  let m = message || {};
  for (let depth = 0; depth < MAX_WRAPPER_DEPTH; depth++) {
    const key = WRAPPER_KEYS.find((k) => m[k]?.message);
    if (!key) break;
    m = m[key].message;
  }
  return m;
}

/**
 * Baileys timestamps arrive as a number OR as a protobuf Long. Stored verbatim,
 * a Long sorts as an object and silently scrambles the order of history.
 * @param {any} ts
 * @returns {number}
 */
export function toEpochSeconds(ts) {
  if (ts == null) return 0;
  if (typeof ts === 'number') return ts;
  if (typeof ts === 'string') return Number(ts) || 0;
  if (typeof ts.toNumber === 'function') return ts.toNumber();
  if (typeof ts.low === 'number') return ts.low;
  return 0;
}

/** How long a write waits for another connection's lock (RV-6, F-12). */
export const BUSY_TIMEOUT_MS = 5000;
/** Routed ids older than this are forgotten at open: a redelivery that late is
 *  "missed" by its own timestamp anyway (whatsapp-inbound.js LIVE_LATE_S). */
const ROUTED_KEEP_S = 7 * 24 * 3600;

/** @param {string} jid */
function isGroupJid(jid) {
  return String(jid || '').endsWith('@g.us');
}

/**
 * Open (creating if needed) the WhatsApp store.
 * @param {string} dbPath - absolute path to the SQLite file
 */
export function openWhatsAppStore(dbPath) {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new DatabaseSync(dbPath);

  // RV-6 (F-12): WAL lets the daemon and tools/wa.py read while this process
  // writes, and the busy timeout makes a write WAIT for another writer instead
  // of failing at once with "database is locked". Timeout first: switching the
  // journal mode itself needs the lock.
  db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}; PRAGMA journal_mode = WAL;`);

  db.exec(`
    CREATE TABLE IF NOT EXISTS contacts (
      jid TEXT PRIMARY KEY,
      name TEXT,
      notify TEXT,
      updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS chats (
      jid TEXT PRIMARY KEY,
      name TEXT,
      is_group INTEGER NOT NULL DEFAULT 0,
      last_message_at INTEGER,
      updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS messages (
      jid TEXT NOT NULL,
      id TEXT NOT NULL,
      from_me INTEGER NOT NULL DEFAULT 0,
      sender TEXT,
      push_name TEXT,
      ts INTEGER NOT NULL DEFAULT 0,
      text TEXT,
      raw TEXT,
      PRIMARY KEY (jid, id)
    );
    CREATE INDEX IF NOT EXISTS messages_by_chat ON messages (jid, ts DESC);
    -- RV-4 (F-6): every message id handed to the daemon, so a redelivery
    -- (reconnect, sidecar restart) is never routed a second time.
    CREATE TABLE IF NOT EXISTS routed (
      jid TEXT NOT NULL,
      id TEXT NOT NULL,
      at INTEGER NOT NULL,
      PRIMARY KEY (jid, id)
    );
  `);

  // Re-delivery is normal, not exceptional: Baileys replays messages on every
  // reconnect. Upsert everywhere, so the archive the agent reads back is not
  // three copies of the same conversation.
  const putContact = db.prepare(`
    INSERT INTO contacts (jid, name, notify, updated_at) VALUES (?, ?, ?, ?)
    ON CONFLICT(jid) DO UPDATE SET
      name = COALESCE(excluded.name, contacts.name),
      notify = COALESCE(excluded.notify, contacts.notify),
      updated_at = excluded.updated_at
  `);
  const putChat = db.prepare(`
    INSERT INTO chats (jid, name, is_group, last_message_at, updated_at) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(jid) DO UPDATE SET
      name = COALESCE(excluded.name, chats.name),
      is_group = excluded.is_group,
      last_message_at = MAX(COALESCE(excluded.last_message_at, 0), COALESCE(chats.last_message_at, 0)),
      updated_at = excluded.updated_at
  `);
  const putMessage = db.prepare(`
    INSERT INTO messages (jid, id, from_me, sender, push_name, ts, text, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(jid, id) DO UPDATE SET
      text = COALESCE(excluded.text, messages.text),
      raw = COALESCE(excluded.raw, messages.raw),
      ts = excluded.ts
  `);

  const now = () => Math.floor(Date.now() / 1000);

  db.prepare('DELETE FROM routed WHERE at < ?').run(now() - ROUTED_KEEP_S);
  const claimRoutedStmt = db.prepare('INSERT OR IGNORE INTO routed (jid, id, at) VALUES (?, ?, ?)');
  const isRoutedStmt = db.prepare('SELECT 1 FROM routed WHERE jid = ? AND id = ?');
  // RV-20: see isKnownContact. char(8291) is the marker U+2063 (MS-15).
  const isKnownContactStmt = db.prepare(`
    SELECT 1 FROM contacts WHERE jid = ? AND TRIM(COALESCE(name, '')) != ''
    UNION ALL
    SELECT 1 FROM messages WHERE jid = ? AND from_me = 1
      AND substr(id, 1, ${BUTLER_ID_PREFIX.length}) != '${BUTLER_ID_PREFIX}'
      AND (text IS NULL OR substr(text, -1) != char(8291))
    LIMIT 1
  `);

  /** @param {any[]} contacts */
  function ingestContacts(contacts = []) {
    for (const c of contacts) {
      if (!c?.id) continue;
      // RV-20 (F-3): `name` is the ADDRESS-BOOK name and nothing else. Baileys
      // fills it from app-state contact actions (rc14 processContactAction,
      // lib/Utils/sync-action-utils.js:23, and lidContactAction,
      // lib/Utils/chat-utils.js:835-845: `fullName || firstName || username`)
      // and from history sync, whose name ingestHistory re-derives from the
      // raw conversation (lib/Utils/history.js:58). What a SENDER chooses --
      // the push name and a business's verified name, emitted for every
      // inbound message by lib/Socket/chats.js:917-924 -- is `notify`, never
      // `name`.
      putContact.run(c.id, c.name ?? null, c.notify ?? c.verifiedName ?? null, now());
    }
  }

  /** @param {any[]} chats */
  function ingestChats(chats = []) {
    for (const c of chats) {
      if (!c?.id) continue;
      putChat.run(
        c.id,
        c.name ?? c.subject ?? null,
        isGroupJid(c.id) ? 1 : 0,
        toEpochSeconds(c.conversationTimestamp),
        now()
      );
    }
  }

  /** @param {any[]} messages */
  function ingestMessages(messages = []) {
    for (const m of messages) {
      const jid = m?.key?.remoteJid;
      const id = m?.key?.id;
      if (!jid || !id) continue;
      const ts = toEpochSeconds(m.messageTimestamp);
      putMessage.run(
        jid,
        id,
        m.key.fromMe ? 1 : 0,
        m.key.participant ?? null,
        m.pushName ?? null,
        ts,
        extractMessageText(m),
        m.message ? JSON.stringify(m.message) : null
      );
      // A chat the owner has never opened still needs a row, or the message is
      // unreachable from listChats.
      putChat.run(jid, null, isGroupJid(jid) ? 1 : 0, ts, now());
    }
  }

  return {
    ingestContacts,
    ingestChats,
    ingestMessages,

    /**
     * Record that message `id` in `jid` went to the daemon. True the FIRST
     * time only: the one atomic "route at most once" check (RV-4).
     */
    claimRouted(jid, id) {
      return Number(claimRoutedStmt.run(jid, id, now()).changes) === 1;
    },

    /** Was message `id` in `jid` already handed to the daemon? */
    isRouted(jid, id) {
      return !!isRoutedStmt.get(jid, id);
    },

    /**
     * Handle a `messaging-history.set` payload (chats + contacts + messages).
     * @param {{contacts?: any[], chats?: any[], messages?: any[]}} payload
     */
    ingestHistory(payload = {}) {
      // RV-20 (F-3): in Baileys rc14 a history contact's `name` is
      // `chat.displayName || chat.name || chat.username`
      // (lib/Utils/history.js:58), and `username`/`displayName` are chosen by
      // the OTHER person, so that field can no longer prove "saved in the
      // address book". The raw conversation is pushed intact into
      // `payload.chats` (history.js:100) and its own `name` is still the
      // address-book name: take the contact's name from there and only there.
      const savedName = new Map();
      for (const chat of payload.chats ?? []) {
        if (chat?.id && chat.name) savedName.set(chat.id, chat.name);
      }
      ingestContacts((payload.contacts ?? []).map((c) => ({ ...c, name: savedName.get(c?.id) })));
      ingestChats(payload.chats);
      ingestMessages(payload.messages);
    },

    /**
     * The `getMessage` callback Baileys needs to re-encrypt a failed decrypt.
     * Returns the message CONTENT, which is what it re-encrypts.
     * @param {{remoteJid?: string|null, id?: string|null}} key
     */
    getMessage(key) {
      if (!key?.remoteJid || !key?.id) return undefined;
      const row = db.prepare('SELECT raw FROM messages WHERE jid = ? AND id = ?').get(key.remoteJid, key.id);
      if (!row?.raw) return undefined;
      try {
        return JSON.parse(String(row.raw));
      } catch {
        return undefined;
      }
    },

    /**
     * A person is KNOWN (the send guard and answer-everyone, RV-20, AC-606)
     * when the user saved them -- an address-book `name` -- or has written in
     * their chat themselves. A row alone is not enough: Baileys makes one,
     * with the sender's own push name in `notify`, for anyone who ever wrote
     * (lib/Socket/chats.js:917-924), group members included. A message a
     * Butler sent (its id prefix, RV-12, or the marker, MS-15) is not the
     * user writing. KEEP EQUAL to the Python twin, daemon/whatsapp.py
     * `Archive.is_known`.
     * @param {string} jid
     */
    isKnownContact(jid) {
      return !!isKnownContactStmt.get(jid, jid);
    },

    /**
     * MS-12: a group is a valid recipient when it is in the archive's chats.
     * @param {string} jid
     */
    isKnownGroup(jid) {
      return !!db.prepare('SELECT 1 FROM chats WHERE jid = ? AND is_group = 1').get(jid);
    },

    /** Row counts, for /status (AC-507: contacts greater than 0 after a link). */
    counts() {
      const n = (t) => Number(db.prepare(`SELECT COUNT(*) AS n FROM ${t}`).get().n);
      return { contacts: n('contacts'), chats: n('chats'), messages: n('messages') };
    },

    /** @param {{query?: string, limit?: number}} [opts] */
    listContacts(opts = {}) {
      const limit = opts.limit || 500;
      if (opts.query) {
        return db.prepare(`
          SELECT jid, name, notify FROM contacts
          WHERE LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(notify, '')) LIKE ? OR jid LIKE ?
          ORDER BY COALESCE(name, notify, jid) LIMIT ?
        `).all(`%${opts.query.toLowerCase()}%`, `%${opts.query.toLowerCase()}%`, `%${opts.query}%`, limit);
      }
      return db.prepare('SELECT jid, name, notify FROM contacts ORDER BY COALESCE(name, notify, jid) LIMIT ?').all(limit);
    },

    /** @param {{groupsOnly?: boolean, limit?: number}} [opts] */
    listChats(opts = {}) {
      const limit = opts.limit || 200;
      const where = opts.groupsOnly ? 'WHERE is_group = 1' : '';
      return db.prepare(`
        SELECT c.jid AS jid,
               COALESCE(c.name, ct.name, ct.notify) AS name,
               c.is_group AS isGroup,
               c.last_message_at AS lastMessageAt
        FROM chats c LEFT JOIN contacts ct ON ct.jid = c.jid
        ${where}
        ORDER BY COALESCE(c.last_message_at, 0) DESC LIMIT ?
      `).all(limit);
    },

    /**
     * Most recent messages in a chat, NEWEST FIRST.
     * @param {string} jid
     * @param {number} [count]
     */
    readChat(jid, count = 50) {
      return db.prepare(`
        SELECT jid, id, from_me AS fromMe, sender, push_name AS pushName, ts, text
        FROM messages WHERE jid = ? ORDER BY ts DESC, id DESC LIMIT ?
      `).all(jid, count);
    },

    /**
     * @param {string} query
     * @param {number} [limit]
     */
    searchMessages(query, limit = 50) {
      return db.prepare(`
        SELECT jid, id, from_me AS fromMe, push_name AS pushName, ts, text
        FROM messages WHERE text IS NOT NULL AND LOWER(text) LIKE ?
        ORDER BY ts DESC LIMIT ?
      `).all(`%${String(query).toLowerCase()}%`, limit);
    },

    /**
     * Turn what a person typed into a JID -- a raw JID, a phone number in any
     * human format, or a contact name.
     *
     * A NAME THAT MATCHES SEVERAL CONTACTS RESOLVES TO NOTHING. Picking the
     * first match is how a message reaches the wrong person from the owner's
     * real number, and the caller cannot tell a guess from a match unless this
     * refuses to guess.
     *
     * @param {string} input
     * @returns {{jid: string|null, name: string|null, candidates: Array<{jid: string, name: string|null}>}}
     */
    resolveRecipient(input) {
      const raw = String(input || '').trim();
      if (!raw) return { jid: null, name: null, candidates: [] };

      const known = (jid) => {
        const row = db.prepare('SELECT jid, COALESCE(name, notify) AS name FROM contacts WHERE jid = ?').get(jid);
        return row ? { jid: String(row.jid), name: row.name ? String(row.name) : null, candidates: [] } : null;
      };

      // A full JID, or a phone number in any of the shapes people type.
      if (raw.includes('@')) return known(raw) || { jid: raw, name: null, candidates: [] };
      const digits = jidDigits(raw.replace(/[\s()-]/g, ''));
      if (/^\d{6,}$/.test(digits)) {
        const jid = `${digits}@s.whatsapp.net`;
        return known(jid) || { jid, name: null, candidates: [] };
      }

      const matches = db.prepare(`
        SELECT jid, COALESCE(name, notify) AS name FROM contacts
        WHERE LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(notify, '')) LIKE ?
        ORDER BY COALESCE(name, notify) LIMIT 10
      `).all(`%${raw.toLowerCase()}%`, `%${raw.toLowerCase()}%`)
        .map((r) => ({ jid: String(r.jid), name: r.name ? String(r.name) : null }));

      if (matches.length === 1) return { jid: matches[0].jid, name: matches[0].name, candidates: [] };
      return { jid: null, name: null, candidates: matches };
    },

    /**
     * Resolve anything the owner might call a conversation: a JID, a phone
     * number, a contact name, or a GROUP SUBJECT. Groups have no contact row,
     * so contact lookup alone cannot reach them -- and the failure reads like
     * a misspelling rather than a missing lookup.
     *
     * Ambiguity refuses here for the same reason it refuses in
     * `resolveRecipient`: the caller cannot tell a guess from a match.
     *
     * @param {string} input
     * @returns {{jid: string|null, name: string|null, candidates: Array<{jid: string, name: string|null}>}}
     */
    resolveChat(input) {
      const asContact = this.resolveRecipient(input);
      const raw = String(input || '').trim();
      if (!raw) return { jid: null, name: null, candidates: [] };
      // A JID or phone number is unambiguous -- take it without consulting chats.
      if (asContact.jid && (raw.includes('@') || /^[\d\s()+-]{6,}$/.test(raw))) return asContact;

      const chatMatches = db.prepare(`
        SELECT jid, name FROM chats
        WHERE name IS NOT NULL AND LOWER(name) LIKE ? LIMIT 10
      `).all(`%${raw.toLowerCase()}%`)
        .map((r) => ({ jid: String(r.jid), name: r.name ? String(r.name) : null }));

      const all = [...chatMatches];
      for (const c of asContact.jid
        ? [{ jid: asContact.jid, name: asContact.name }]
        : asContact.candidates) {
        if (!all.some((m) => m.jid === c.jid)) all.push(c);
      }

      if (all.length === 1) return { jid: all[0].jid, name: all[0].name, candidates: [] };
      return { jid: null, name: null, candidates: all };
    },

    close() {
      db.close();
    },
  };
}
