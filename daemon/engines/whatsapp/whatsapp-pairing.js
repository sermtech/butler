/**
 * Pairing state for the WhatsApp channel: a pure state machine plus the wording
 * the settings page shows. No Baileys import, no socket, no timers -- `whatsapp-socket.js`
 * forwards connection events into `reducePairing` and `wa-sidecar.mjs` serves
 * the result.
 *
 * WHY THIS IS A SEPARATE MODULE: linking a companion device is the one part of
 * this channel that cannot be tested against the real thing. It depends on Meta's
 * servers and on the account, and since the mid-2026 passkey rollout some accounts
 * require a WebAuthn assertion during linking that no headless client can produce.
 * Keeping every decision that does NOT need the network in here means the untestable
 * surface is reduced to event forwarding.
 *
 * THE HONESTY RULE: we cannot OBSERVE the passkey gate. A gated account and a phone
 * nobody picked up produce the same trace -- QR after QR, connection never opens. So
 * `describePairing` names the gate as something to check and never as something we
 * found. See `test/whatsapp-pairing.test.mjs`, which pins the wording.
 */

/** QRs reissued in one attempt before the copy mentions the passkey gate at all. */
export const PASSKEY_HINT_QR_COUNT = 2;

/** @typedef {'idle'|'connecting'|'awaiting_scan'|'paired'|'disconnected'|'logged_out'|'replaced'|'forbidden'|'error'} PairingStatus */

/** @type {Readonly<{status: PairingStatus, qr: string|null, qrCount: number, phone: string|null, name: string|null, error: string|null, lastDisconnectCode: number|null, startedAt: number|null, qrAt: number|null}>} */
export const IDLE_PAIRING = Object.freeze({
  status: 'idle',
  qr: null,
  qrCount: 0,
  phone: null,
  name: null,
  error: null,
  lastDisconnectCode: null,
  startedAt: null,
  qrAt: null,
});

/**
 * The bare digits of a WhatsApp JID.
 *
 * A logged-in JID carries a device suffix (`15551234567:22@s.whatsapp.net`), which
 * is not part of the number. This was open-coded in three places before it lived
 * here (the channel's mention stripper, the session-status route, and this panel),
 * and the mention stripper is the one that matters: it builds a RegExp from the
 * result, so a stray `:22` there silently stops stripping the bot's own @mention.
 *
 * @param {string|null|undefined} jid
 * @returns {string} digits only, or '' when there is nothing to extract
 */
export function jidDigits(jid) {
  if (!jid) return '';
  return String(jid).split('@')[0].split(':')[0].replace(/^\+/, '');
}

/**
 * Extract a display phone number from a WhatsApp JID.
 *
 * @param {string|null|undefined} jid
 * @returns {string|null} `+<digits>`, or null when there is no number to show
 */
export function phoneFromJid(jid) {
  const digits = jidDigits(jid);
  return digits ? `+${digits}` : null;
}

/**
 * Fold a connection event into the pairing state. Never mutates `state`.
 *
 * @param {typeof IDLE_PAIRING} state
 * @param {{type: string, [k: string]: any}} event
 * @param {number} now - injected so the machine stays pure and testable
 * @returns {typeof IDLE_PAIRING}
 */
export function reducePairing(state, event, now) {
  switch (event.type) {
    case 'start':
      // A retry must not wear the previous attempt's failure: the panel would
      // show a stale error over a live QR and read as broken.
      return { ...IDLE_PAIRING, status: 'connecting', startedAt: now };

    case 'qr':
      return {
        ...state,
        status: 'awaiting_scan',
        qr: event.qr,
        qrCount: state.qrCount + 1,
        qrAt: now,
        error: null,
      };

    case 'open':
      return {
        ...state,
        status: 'paired',
        qr: null,
        phone: phoneFromJid(event.id),
        name: event.name || null,
        error: null,
        lastDisconnectCode: null,
      };

    case 'close':
      return {
        ...state,
        // MS-6: 401, 440 and 403 (RV-5) are terminal and each has its own words.
        status: event.loggedOut ? 'logged_out' : event.replaced ? 'replaced'
          : event.forbidden ? 'forbidden' : 'disconnected',
        qr: null,
        lastDisconnectCode: event.statusCode ?? null,
      };

    case 'error':
      return { ...state, status: 'error', qr: null, error: event.message || 'Unknown error' };

    case 'stop':
      return { ...IDLE_PAIRING };

    default:
      throw new Error(`Unknown pairing event: ${event.type}`);
  }
}

/**
 * The headline/detail/tone the settings page renders.
 *
 * @param {typeof IDLE_PAIRING} state
 * @returns {{headline: string, detail: string, tone: 'neutral'|'success'|'warning'|'error'}}
 */
export function describePairing(state) {
  switch (state.status) {
    case 'idle':
      return {
        headline: 'Not linked',
        detail: 'Start pairing to get a QR code. Nothing connects to WhatsApp until you do.',
        tone: 'neutral',
      };

    case 'connecting':
      return {
        headline: 'Connecting',
        detail: 'Opening a connection to WhatsApp and waiting for a QR code.',
        tone: 'neutral',
      };

    case 'awaiting_scan': {
      const base = 'On your phone: WhatsApp > Settings > Linked devices > Link a device, then scan this code.';
      if (state.qrCount < PASSKEY_HINT_QR_COUNT) {
        return { headline: 'Scan this code', detail: base, tone: 'neutral' };
      }
      // Deliberately a prompt to look, not a verdict. Both a gated account and an
      // unattended phone produce exactly this trace, and only the phone can tell
      // the two apart.
      return {
        headline: `Still waiting (code reissued ${state.qrCount} times)`,
        detail:
          `${base} If your phone showed a passkey prompt, or said to keep both apps open and scan again, ` +
          'check whether this account now requires a passkey to link a device -- WhatsApp began rolling that ' +
          'out in mid-2026 and no headless client can complete it.',
        tone: 'warning',
      };
    }

    case 'paired':
      return {
        headline: 'Linked',
        detail: `Paired as ${state.phone || 'unknown number'}${state.name ? ` (${state.name})` : ''}.`,
        tone: 'success',
      };

    case 'logged_out':
      return {
        headline: 'Logged out',
        detail: 'Unlinked from the phone: link again.',
        tone: 'warning',
      };

    case 'replaced':
      return {
        headline: 'Link open elsewhere',
        detail: 'This link is open in another program. Close it there, or link again here.',
        tone: 'warning',
      };

    case 'forbidden':
      return {
        headline: 'Refused by WhatsApp',
        detail: 'WhatsApp refused this link (403): the account may be restricted or banned. ' +
          'Check WhatsApp on the phone before linking again; Butler will not retry by itself.',
        tone: 'error',
      };

    case 'disconnected':
      return {
        headline: 'Disconnected',
        detail: `The connection closed${state.lastDisconnectCode ? ` (code ${state.lastDisconnectCode})` : ''}.`,
        tone: 'warning',
      };

    case 'error':
      return { headline: 'Pairing failed', detail: state.error || 'Unknown error', tone: 'error' };

    default:
      throw new Error(`Unknown pairing status: ${state.status}`);
  }
}
