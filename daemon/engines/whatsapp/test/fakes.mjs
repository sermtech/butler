// Test doubles shared by the sidecar tests. NOTHING here opens a real WhatsApp
// connection or reaches the internet: Baileys is replaced at its three seams
// (makeWASocket, fetchLatestBaileysVersion, downloadMediaMessage), exactly as
// an earlier prototype's tests inject `baileys` into setupWhatsApp.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const ME = { id: '15555550100:88@s.whatsapp.net', lid: '45200000000001:88@lid', name: 'Test User' };
export const SELF_LID = '45200000000001@lid';
export const SELF_PN = '15555550100@s.whatsapp.net';
export const ALICE = '15551230000@s.whatsapp.net';
export const BOB = '15559876543@s.whatsapp.net';
export const GROUP = '120363000000000000@g.us';

export const tempDir = (prefix = 'wa-test-') => fs.mkdtempSync(path.join(os.tmpdir(), prefix));

export const silent = () => {};

/** Poll until `fn()` is truthy; fail loudly rather than hang. */
export async function until(fn, what = 'condition', tries = 200) {
  for (let i = 0; i < tries; i++) {
    if (fn()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/**
 * A Baileys stand-in. Each makeWASocket call makes a NEW socket (reconnects
 * open new ones), recorded in `sockets` with its options in `calls`.
 */
export function fakeBaileys({ user = ME, sendError, download } = {}) {
  const calls = [];
  const sockets = [];
  const sent = [];
  const downloads = [];
  const presence = [];
  const makeSocket = () => {
    const handlers = new Map();
    const socket = {
      ev: { on: (e, fn) => { if (!handlers.has(e)) handlers.set(e, []); handlers.get(e).push(fn); } },
      user,
      ended: false,
      loggedOut: false,
      end() { this.ended = true; },
      // As Baileys (lib/Socket/socket.js:572-594): logout sends a node to
      // WhatsApp, which needs an OPEN socket ("Connection Closed" otherwise),
      // and then ends the socket itself.
      async logout() {
        if (this.ended) throw new Error('Connection Closed');
        this.loggedOut = true;
        this.ended = true;
      },
      updateMediaMessage: () => {},
      sendPresenceUpdate: async (state, jid) => { presence.push({ state, jid }); },
      sendMessage: async (jid, content, opts) => {
        if (sendError) throw sendError;
        sent.push({ jid, content, opts });
        // As Baileys (rc14): a caller's `messageId` IS the message's id
        // (lib/Socket/messages-send.js:1086-1087, `...options` after the generated one).
        return { key: { id: opts?.messageId || 'SENT' + sent.length } };
      },
      emit: (e, payload) => { for (const fn of handlers.get(e) || []) fn(payload); },
    };
    sockets.push(socket);
    return socket;
  };
  return {
    calls,
    sockets,
    sent,
    downloads,
    presence,
    last: () => sockets.at(-1),
    deps: {
      makeWASocket: (opts) => { calls.push(opts); return makeSocket(); },
      fetchLatestBaileysVersion: async () => ({ version: [2, 3000, 1043857760], isLatest: true }),
      downloadMediaMessage: download || (async (msg) => { downloads.push(msg); return Buffer.from('ogg'); }),
    },
  };
}

/** An inbound `messages.upsert` payload for one message. */
// Stamped NOW by default: an own-chat message written before the sidecar
// started is MISSED, whatever its label (docs/messaging.md 6.4).
export const upsert = (remoteJid, message, { fromMe = false, type = 'notify', id = 'M' + Math.random(), participant, pushName, ts = Math.floor(Date.now() / 1000) } = {}) => ({
  type,
  messages: [{
    key: { remoteJid, id, fromMe, ...(participant ? { participant } : {}) },
    messageTimestamp: ts,
    ...(pushName ? { pushName } : {}),
    message,
  }],
});
