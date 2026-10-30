/**
 * The inbound filter (MS-14, MS-14a/b/c, MS-15): PURE. Given one Baileys
 * message it answers "who is this for": the user themselves (`owner`), a person
 * or group the user asked Butler to answer (`guest`), or a sign that the human
 * took over a guest chat (`takeover`). Anything else is archived elsewhere and
 * returns null here.
 *
 * Kept free of sockets and timers so every branch is a plain function test
 * (`test/inbound.test.mjs`). `whatsapp-socket.js` feeds it and forwards the
 * result to the daemon on the NDJSON stream.
 *
 * What it keeps from an earlier prototype (channels/whatsapp.js 307-351): only `notify`
 * routes (the bot's own sends and offline-queued deliveries are `append`);
 * `fromMe` is answered only in the user's own chat, matched by phone JID or
 * LID with the server part compared; media is classified on the UNWRAPPED
 * message.
 *
 * What differs (each from docs/messaging.md):
 * - MS-15: a message in the own chat that ends with the marker U+2063 was sent
 *   by a Butler (this one or another on the same number) and is never routed.
 * - MS-14a: other people are answered only when on the answer list (or the
 *   answer-everyone flag is on and the sender is a saved contact). In a group
 *   the trigger is the trigger WORD at the start of the message, or a reply to
 *   one of Butler's own messages. A mention of the user is NOT a trigger.
 * - MS-14c: the user's own message in a guest chat is a `takeover`.
 * - RV-12: a message whose id carries BUTLER_ID_PREFIX was sent by a Butler
 *   (any socket on this number) and is never routed, missed or a takeover --
 *   voice notes included, which carry no marker.
 * - RV-9: in the own chat, a sticker, location, contact card or poll is an
 *   owner item with `unsupported` set (the daemon answers a fixed line), and an
 *   edit is an owner item whose text is "(edited) <new text>".
 */
import crypto from 'node:crypto';
import { jidDigits } from './whatsapp-pairing.js';
import { extractMessageText, unwrapMessage, toEpochSeconds } from './whatsapp-store.js';

/** INVISIBLE SEPARATOR: ends every message Butler sends (MS-15). */
export const MARKER = '\u2063';

/** Inbound files bigger than this are refused BEFORE any download. */
export const MAX_INBOUND_BYTES = 100 * 1024 * 1024;

const MEDIA_KEYS = ['imageMessage', 'documentMessage', 'audioMessage', 'videoMessage'];

export const endsWithMarker = (text) => typeof text === 'string' && text.endsWith(MARKER);

/**
 * Every message a Butler sends carries this id prefix (RV-12, F-16). Baileys
 * takes a caller's id as is (rc14 lib/Socket/messages-send.js:1086-1087:
 * `messageId: generateMessageIDV2(...)` is followed by `...options`, and
 * lib/Utils/messages.js:608 uses `options?.messageId`), and WhatsApp Web's own
 * ids are '3EB0' + 18 uppercase hex (lib/Utils/generics.js:134-148
 * generateMessageIDV2),
 * so Butler's keep that exact shape: '3EB0', 8 fixed hex, 10 random hex. A
 * foreign Web id matches by chance once in 2^32.
 */
export const BUTLER_ID_PREFIX = '3EB0B0B7E1A5';
export const newButlerId = () => BUTLER_ID_PREFIX + crypto.randomBytes(5).toString('hex').toUpperCase();
export const isButlerId = (id) => typeof id === 'string' && id.startsWith(BUTLER_ID_PREFIX);

/** WAProto Message.ProtocolMessage.Type.MESSAGE_EDIT. */
const MESSAGE_EDIT = 14;

/**
 * An edit: Baileys upserts the protocol message itself (rc14
 * lib/Socket/chats.js:917-918 upsertMessage emits every message before
 * :1013 processMessage runs, and lib/Utils/process-message.js:364 turns
 * MESSAGE_EDIT into a messages.update), carrying the
 * edited message's id and its new content. null when `inner` is not an edit.
 */
export function editOf(inner) {
  const p = inner?.protocolMessage;
  if (!p?.editedMessage) return null;
  if (Number(p.type) !== MESSAGE_EDIT && p.type !== 'MESSAGE_EDIT') return null;
  return { original: p.key?.id || null, inner: unwrapMessage(p.editedMessage) };
}

/** Content Butler cannot read (RV-9): the proto key -> the name in the item. */
const UNSUPPORTED = {
  stickerMessage: 'sticker',
  lottieStickerMessage: 'sticker',
  stickerPackMessage: 'sticker',
  locationMessage: 'location',
  liveLocationMessage: 'location',
  contactMessage: 'contact',
  contactsArrayMessage: 'contact',
  pollCreationMessage: 'poll',
  pollCreationMessageV2: 'poll',
  pollCreationMessageV3: 'poll',
  pollCreationMessageV4: 'poll',
  pollCreationMessageV5: 'poll',
};
export const unsupportedOf = (inner) => {
  const key = Object.keys(UNSUPPORTED).find((k) => inner?.[k]);
  return key ? UNSUPPORTED[key] : null;
};

/** WAProto MessageAssociation.AssociationType.MEDIA_ALBUM. */
const MEDIA_ALBUM = 1;

/**
 * The album a photo or video belongs to (RV-8): its parent message's id, read
 * from `messageContextInfo.messageAssociation` on the message or on its
 * unwrapped `associatedChildMessage`. null for anything else.
 */
export function albumParentOf(message) {
  for (const m of [message, unwrapMessage(message)]) {
    const a = m?.messageContextInfo?.messageAssociation;
    if (a && (Number(a.associationType) === MEDIA_ALBUM || a.associationType === 'MEDIA_ALBUM')) {
      return a.parentMessageKey?.id || null;
    }
  }
  return null;
}

/** An album's announced size from its parent `albumMessage`, or null. */
export function albumSizeOf(inner) {
  const a = inner?.albumMessage;
  if (!a) return null;
  const n = Number(a.expectedImageCount || 0) + Number(a.expectedVideoCount || 0);
  return n > 0 ? n : null;
}

/** `digits@server`, device suffix stripped: the one comparable form of a JID. */
export function bareJid(jid) {
  return `${jidDigits(jid)}@${String(jid).split('@')[1]}`;
}

/** The linked account's own bare JIDs (phone and LID) from `sock.user`. */
export function ownJidsOf(user) {
  return [user?.id, user?.lid].filter(Boolean).map(bareJid);
}

/** fileLength arrives as a number, a string or a protobuf Long. */
export function declaredBytes(media) {
  const n = media?.fileLength;
  if (n == null) return null;
  if (typeof n === 'number') return n;
  if (typeof n === 'string') return Number(n);
  if (typeof n.toNumber === 'function') return n.toNumber();
  if (typeof n.low === 'number') return n.low;
  return null;
}

/** The first media object in an unwrapped message, with its kind. */
export function mediaOf(inner) {
  const key = MEDIA_KEYS.find((k) => inner[k]);
  return key ? { kind: key.replace('Message', ''), media: inner[key] } : null;
}

/** contextInfo lives inside whichever content type carries it. */
function contextOf(inner) {
  for (const v of Object.values(inner)) {
    if (v && typeof v === 'object' && v.contextInfo) return v.contextInfo;
  }
  return null;
}

/**
 * Is this a reply to one of Butler's own messages? Butler sends AS the user, so
 * the quoted participant alone cannot tell Butler from the human: the marker on
 * the quoted text does (MS-15).
 */
function repliesToButler(inner, ownJids) {
  const ctx = contextOf(inner);
  if (!ctx?.participant || !ctx.quotedMessage) return false;
  if (!ownJids.includes(bareJid(ctx.participant))) return false;
  return endsWithMarker(extractMessageText({ message: ctx.quotedMessage }));
}

/**
 * The text after a leading trigger word, or null when the message does not
 * start with it. The word must end at a boundary: "Butlerian" is not "Butler",
 * and an apostrophe (ASCII or the typographic U+2019) is part of the word, so
 * "Butler's car" talks ABOUT Butler, not to it (RV-24, AC-611).
 */
export function stripTrigger(text, trigger) {
  const escaped = trigger.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^\\s*${escaped}(?![\\p{L}\\p{N}_'\\u2019])[\\s,:;.!-]*`, 'iu');
  return re.test(text) ? text.replace(re, '').trim() : null;
}

/** Every guest reply starts with this (MS-14b); /guest-reply refuses others. */
export const GUEST_PREFIX = 'Butler: ';

/**
 * May Butler answer INTO this chat as a guest reply (MS-14a, M9)? The chat is
 * on the answer list, or answer-everyone is on and it is a one-to-one chat
 * with a saved contact. Never the user's own chat (that is /reply's), never a
 * broadcast or status JID. Read from the config at the moment of the reply,
 * so taking someone off the list closes the door at once. PURE.
 * @param {{jid: string, ownJids: string[], config: {answer?: string[], answer_everyone?: boolean}, isKnownContact: (jid: string) => boolean}} p
 */
export function isGuestChat({ jid, ownJids, config, isKnownContact }) {
  if (typeof jid !== 'string' || !jid.includes('@')) return false;
  const isGroup = jid.endsWith('@g.us');
  if (!isGroup && !jid.endsWith('@s.whatsapp.net') && !jid.endsWith('@lid')) return false;
  if (!isGroup && ownJids.includes(bareJid(jid))) return false;
  if ((config.answer || []).map(bareJid).includes(bareJid(jid))) return true;
  return !isGroup && config.answer_everyone === true && isKnownContact(jid);
}

/**
 * @param {object} p
 * @param {any} p.msg - Baileys WAMessage
 * @param {string} p.type - the `messages.upsert` type
 * @param {string[]} p.ownJids - bare JIDs of the linked account
 * @param {{answer: string[], answer_everyone: boolean, trigger: string}} p.config
 * @param {(jid: string) => boolean} p.isKnownContact
 * @returns {null | {type: 'message', tag: 'owner'|'guest', [k: string]: any} | {type: 'takeover', jid: string, id: string, ts: number}}
 */
export function classifyInbound({ msg, type, ownJids, config, isKnownContact }) {
  if (type !== 'notify') return null;
  const jid = msg?.key?.remoteJid;
  if (!jid) return null;
  const isGroup = jid.endsWith('@g.us');
  if (!isGroup && !jid.endsWith('@s.whatsapp.net') && !jid.endsWith('@lid')) return null;

  const ownChat = !isGroup && ownJids.includes(bareJid(jid));
  const outer = unwrapMessage(msg.message);
  // RV-9: an edit in the own chat is its NEW text; its media is not re-sent.
  const edit = ownChat ? editOf(outer) : null;
  const inner = edit ? edit.inner : outer;
  const found = edit ? null : mediaOf(inner);
  const text = edit ? extractMessageText({ message: inner }) : extractMessageText(msg);
  const unsupported = ownChat && !text && !found ? unsupportedOf(inner) : null;
  if (!text && !found && !unsupported) return null;
  // The marker is read off the text the message carries, which for a captioned
  // file is its caption.
  const marked = endsWithMarker(text);
  // RV-12: sent by a Butler, whatever it carries.
  const ours = marked || isButlerId(msg.key.id);
  const ts = toEpochSeconds(msg.messageTimestamp);

  const base = {
    type: 'message',
    id: msg.key.id,
    jid,
    sender: msg.key.participant || jid,
    pushName: msg.pushName || null,
    ts,
    isGroup,
    text: edit ? `(edited) ${text}` : text || '',
    // The id of the message this one replies to, or null: how the daemon
    // knows a reply QUOTES the question Butler sent (MS-22).
    quoted: contextOf(inner)?.stanzaId || null,
    ...(found ? {
      media: {
        kind: found.kind,
        mimetype: found.media.mimetype || null,
        fileName: found.media.fileName || null,
        seconds: found.media.seconds ?? null,
        ptt: !!found.media.ptt,
        bytes: declaredBytes(found.media),
        tooLarge: (declaredBytes(found.media) ?? 0) > MAX_INBOUND_BYTES,
      },
    } : {}),
  };

  const listed = (config.answer || []).map(bareJid);
  const isListed = (j) => listed.includes(bareJid(j));

  if (ownChat) {
    // The user's own chat. MS-15: Butler's own words, from any socket, are
    // archived and never answered, or two agents on one number answer forever.
    if (ours) return null;
    return {
      ...base,
      tag: 'owner',
      ...(edit ? { edited: edit.original } : {}),
      ...(unsupported ? { unsupported } : {}),
    };
  }

  if (msg.key.fromMe) {
    // The user typed this themselves in someone else's chat. Never answered;
    // in a chat Butler answers it is the human taking over (MS-14c).
    if (ours) return null;
    const guestChat = isListed(jid) || (config.answer_everyone && !isGroup);
    // The id lets the routed table drop a redelivered takeover (RV-4).
    return guestChat ? { type: 'takeover', jid, id: msg.key.id, ts } : null;
  }

  if (isGroup) {
    if (!isListed(jid)) return null;
    const stripped = stripTrigger(text || '', config.trigger);
    if (stripped !== null) return { ...base, text: stripped, tag: 'guest', trigger: 'word' };
    if (repliesToButler(inner, ownJids)) return { ...base, tag: 'guest', trigger: 'reply' };
    return null;
  }

  const sender = base.sender;
  if (isListed(sender) || (config.answer_everyone && isKnownContact(sender))) {
    return { ...base, tag: 'guest' };
  }
  return null;
}

/**
 * Delivered later than this after it was written, an own-chat message is
 * treated as missed rather than answered (MS-19's "an hour-old question
 * answered now is a surprise"): a laptop that slept with Butler running.
 * [DECIDED 2026-10-08]
 */
export const LIVE_LATE_S = 15 * 60;

/**
 * The upsert type ONE message in the user's own chat, or one the user wrote in
 * any other chat, is classified under (docs/messaging.md 6.4). PURE.
 *
 * Baileys' `type` is a BATCH label, not a per-message fact: its event buffer
 * flushes many messages as one upsert typed after the FIRST of them
 * (rc14 lib/Utils/event-buffer.js:572-575, consolidateEvents; rc14 also
 * flushes the buffer when a differently typed upsert arrives, :108-124, so
 * a batch no longer mixes labels, but the label is still the batch's), and a
 * message the server held during a socket reconnect is `append` though Butler
 * was up the whole time (lib/Socket/messages-recv.js:1432,
 * `node.attrs.offline`). Read as
 * the message's own, it dropped live voice notes as `missed` (2026-10-08:
 * 11 sent back to back, 7 answered) and could route Butler's own voice-note
 * echo as the owner speaking. So in the own chat the label is replaced:
 * - a message a Butler sent (its id carries BUTLER_ID_PREFIX, RV-12) is
 *   `append` (never routed; not missed either);
 * - else `notify` (live) when it was written after this sidecar started
 *   (`liveSince`) and reached it within LIVE_LATE_S (`now`, both epoch s);
 * - else `append` (missed while down, MS-19).
 * The user's OWN messages in every other chat get the same per-message label
 * (RV-23, AC-610): a takeover typed right after Butler's guest reply can be
 * flushed in one batch behind that reply's `append` echo, and read by the
 * batch label it was dropped. Everyone else's messages keep Baileys' label.
 *
 * @param {{msg: any, type: string, ownJids: string[], liveSince: number, now: number}} p
 */
export function ownChatType({ msg, type, ownJids, liveSince, now }) {
  const jid = msg?.key?.remoteJid;
  if (!jid) return type;
  const ownChat = !jid.endsWith('@g.us') && ownJids.includes(bareJid(jid));
  if (!ownChat && !msg?.key?.fromMe) return type;
  if (isButlerId(msg.key.id)) return 'append';
  const ts = toEpochSeconds(msg.messageTimestamp);
  return ts >= liveSince && now - ts <= LIVE_LATE_S ? 'notify' : 'append';
}

/**
 * MS-19: a message the user wrote in their own chat while Butler was DOWN.
 * Baileys delivers those as `append` once the socket is back, and they are
 * archived, never answered (an hour-old question answered now is a surprise).
 * The daemon counts them and says so in ONE line. PURE, like classifyInbound.
 *
 * Not missed: a live (`notify`) delivery, any other chat, text ending with the
 * marker (a Butler wrote it), and anything a Butler sent (its id prefix,
 * RV-12): a voice note or an uncaptioned file carries no marker, so its own
 * `append` echo would otherwise count as a message the user wrote.
 *
 * @param {{msg: any, type: string, ownJids: string[]}} p
 * @returns {null | {type: 'missed', jid: string, id: string, ts: number}}
 */
export function classifyMissed({ msg, type, ownJids }) {
  if (type !== 'append') return null;
  const jid = msg?.key?.remoteJid;
  if (!jid || jid.endsWith('@g.us') || !ownJids.includes(bareJid(jid))) return null;
  if (isButlerId(msg.key.id)) return null;
  const text = extractMessageText(msg);
  if (!text && !mediaOf(unwrapMessage(msg.message))) return null;
  if (endsWithMarker(text)) return null;
  return { type: 'missed', jid, id: msg.key.id, ts: toEpochSeconds(msg.messageTimestamp) };
}
