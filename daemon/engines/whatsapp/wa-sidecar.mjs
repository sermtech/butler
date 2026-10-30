// WhatsApp sidecar: Butler's transport in front of Baileys (docs/messaging.md,
// MS-1..MS-6, M1). A PORT of an earlier prototype's WhatsApp code: the socket, pairing,
// archive, send guard and media rules are an earlier prototype's, with the differences
// listed in the headers of whatsapp-socket.js and whatsapp-inbound.js. It knows nothing of chats,
// agents or speech; the daemon (Python) is the only client.
//
//   GET    /pairing           -> {ok, state, view, qrDataUrl}   (QR as a PNG data URL)
//   POST   /pairing           -> start linking (idempotent: never a second socket)
//   DELETE /pairing           -> disconnect, keep the link
//   DELETE /pairing?unlink=true -> log out and delete session/
//   POST   /send              -> JSON {recipient, text, confirm, send_enabled}
//                                or multipart (one file in `files`, 100 MB cap)
//                                guard order MS-11: send switch, resolve, known
//                                recipient (contact or archived group), rate
//                                limit, first-send confirmation; 409 confirm or
//                                ambiguous, 403 other refusals, 503 not linked,
//                                502 socket error; every text ends with U+2063
//   POST   /reply             -> an answer in the user's OWN chat (MS-17): JSON
//                                {jid, text, quote} or multipart {jid, quote,
//                                ptt:"true"} + one OGG/Opus file (a voice note),
//                                or {jid, quote, caption} + one file a turn
//                                SHOWED (docs/rich-chat.md O-1);
//                                not the send guard: 403 not_own_chat for any
//                                other chat, 404 unknown_quote, 503 not linked,
//                                429 rate_limited past 30 a minute (RV-12);
//                                audited as a reply, never charged to the rate
//   POST   /guest-reply       -> {jid, text, quote?}: Butler's answer to a GUEST
//                                (MS-14b): text only, must start "Butler: "
//                                (400), only into a chat on the answer list or
//                                a saved contact under answer_everyone (403
//                                not_guest_chat), quote from the live cache
//                                only (404); audited `guest: true`, marker
//                                appended, not charged to the send rate
//   POST   /presence          -> {jid, state: composing|paused}, own chat only
//   GET    /media/<id>        -> bytes of an inbound file named by an event
//                                (413 over 100 MB, 404 unknown or expired id)
//   GET    /status            -> {linked, jid, phone, name, contacts, chats, messages}
//   POST   /config            -> {answer: [jid], answer_everyone, trigger,
//                                 send_rate_per_minute}: who Butler answers (MS-14a)
//   GET    /events            -> ONE long-lived NDJSON stream of inbound items:
//                                {type:'ready', dropped: n} first, then what
//                                waited while nobody listened (RV-11, up to
//                                200; `dropped` counts the older ones lost),
//                                then {type:'message', tag:'owner'|'guest',
//                                quoted: <id>|null, ...} -- an owner item may
//                                carry `album: [media...]` (RV-8), `unsupported`
//                                ('sticker'|'location'|'contact'|'poll') or
//                                `edited: <original id>` (RV-9) --
//                                {type:'takeover', jid}
//                                and {type:'missed', jid, id, ts} (an own-chat
//                                message written while Butler was down, MS-19).
//                                Each message id is sent at most once (RV-4).
//
// EVERY request must carry `X-Butler-Token` equal to env BUTLER_WA_TOKEN (401
// otherwise). The process binds 127.0.0.1:0 and prints the chosen port as the
// FIRST stdout line, then only logs (to stderr, never message content).
//
// Environment (the daemon sets all of them):
//   BUTLER_WA_TOKEN  per-boot random token (required)
//   BUTLER_WA_DIR    records directory: session/ (SECRET, deleting it unlinks),
//                    whatsapp.db (plaintext archive) and sent.jsonl (audit)
//   BUTLER_WA_SEND   process default for the send switch: "false"/"0" = off,
//                    anything else or unset = ON (MS-12); a request may override
//   BUTLER_WA_RATE   sends per minute, 1..60 (default 10)
//
// Run from the repo root so node_modules (@whiskeysockets/baileys, qrcode) resolves:
//   node daemon/engines/whatsapp/wa-sidecar.mjs
//
// THE CONSOLE GUARD COMES BEFORE BAILEYS LOADS (RV-10, F-20). A static
// `import` is hoisted: the whole graph (wa-http.js -> Baileys -> libsignal)
// would load and run before any line of this file, so a library print during
// load would go out unguarded -- on STDOUT, ahead of the port line. Only
// wa-console.js (no dependencies) is imported statically; everything else is
// a dynamic import AFTER guardConsole().
import { guardConsole } from './wa-console.js';

// OUR lines go straight to the streams; `console` belongs to the libraries
// from here on and prints text only (wa-console.js: libsignal dumped whole
// session keys into butler.log through console.info).
const say = (line) => process.stdout.write(line + '\n');
const warn = (...parts) => process.stderr.write(parts.map(String).join(' ') + '\n');
guardConsole();
const { createApp } = await import('./wa-http.js');

function fail(message) {
  warn(`wa-sidecar: ${message}`);
  process.exit(1);
}

const token = process.env.BUTLER_WA_TOKEN;
const dir = process.env.BUTLER_WA_DIR;
if (!token) fail('BUTLER_WA_TOKEN is required');
if (!dir) fail('BUTLER_WA_DIR is required');

const sendDefault = !['false', '0'].includes(String(process.env.BUTLER_WA_SEND ?? '').toLowerCase());

const app = createApp({ token, dir, sendDefault, log: warn });
if (process.env.BUTLER_WA_RATE) {
  try {
    app.wa.setConfig({ send_rate_per_minute: Number(process.env.BUTLER_WA_RATE) });
  } catch (err) {
    fail(`BUTLER_WA_RATE: ${err.message}`);
  }
}

app.server.listen(0, '127.0.0.1', () => {
  // The daemon reads this line to learn where we are.
  say(String(app.server.address().port));
  // An already-linked account reconnects at boot; an unlinked one waits for
  // POST /pairing so nothing touches the network before the user asks.
  if (app.wa.hasSession()) {
    app.wa.startPairing().catch((err) => warn('wa-sidecar: reconnect failed:', err.message));
  }
});

let closing = false;
async function shutdown() {
  if (closing) return;
  closing = true;
  await app.close();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
