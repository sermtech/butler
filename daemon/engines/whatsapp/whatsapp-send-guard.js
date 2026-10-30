/**
 * The guards on outbound WhatsApp messages (ported from an earlier prototype, MS-10..MS-12).
 *
 * WHY THESE EXIST. Reading WhatsApp through an unofficial client is passive and
 * looks like any linked device. SENDING is the part that carries real risk, in
 * two directions at once: unsolicited outbound is the pattern Meta's detection
 * keys on (and a ban on a personal number is unappealable), and a wrong
 * recipient reaches a real person from the user's own number.
 *
 * They live HERE, beside the socket, not in a caller: a guard that lives in a
 * caller is a guard the next caller lacks.
 *
 * Order of `check` (MS-11), each pinned by `test/send-guard.test.mjs`:
 *   1. The send switch (boolean, re-read on every call; default ON, MS-12).
 *   2. Resolve the recipient; ambiguity refuses with candidates.
 *   3. Known recipients only: a contact, or (MS-12) a group in the archive.
 *      The linked account's OWN chats are exempt.
 *   4. A rolling per-minute rate limit, charged only to messages actually sent.
 *   5. First send to each recipient per process is held for confirmation.
 *      The account's own chats are exempt from 3 and 5, never from 1 and 4.
 */
import fs from 'fs';
import path from 'path';

const DEFAULT_RATE_PER_MINUTE = 10;

/**
 * @param {object} deps
 * @param {any} deps.store - an open WhatsApp store (for recipient resolution)
 * @param {string} deps.auditPath - JSONL file every decision is appended to
 * @param {() => {send_enabled?: boolean, send_rate_per_minute?: number}} deps.readSettings -
 *   CALLED PER CHECK. Captured once, the send switch would be a lie the moment
 *   someone reaches for it.
 * @param {() => number} [deps.now]
 * @param {() => string[]} [deps.getOwnJids] - the linked account's own bare
 *   JIDs, CALLED PER CHECK because the socket (and so its user) can be relinked
 *   while the guard lives. Empty means no self exemption.
 */
export function createSendGuard({ store, auditPath, readSettings, now = Date.now, getOwnJids = () => [] }) {
  /** Timestamps of messages actually SENT, for the rolling window. */
  const sent = [];
  /** JIDs confirmed at least once in this process. */
  const confirmed = new Set();
  /** Test seam for the "re-read on every call" contract. */
  let settingsOverride = null;

  const settings = () => settingsOverride || readSettings() || {};

  /**
   * Decide whether one message may be sent. Pure apart from reading settings
   * and the store: it records nothing, so a check that is never acted on
   * cannot consume quota.
   *
   * @param {{recipient: string, confirm?: boolean}} req
   * @param {{send_enabled?: boolean, send_rate_per_minute?: number}} [override]
   *   Per-request settings: the daemon passes the send switch on EVERY send, so
   *   it is re-read there and a stale copy cannot outlive a click.
   * @returns {{ok: boolean, jid: string|null, name: string|null, reason: string|null, candidates: Array<object>, limit?: number}}
   */
  function check({ recipient, confirm = false }, override = {}) {
    const cfg = { ...settings(), ...override };
    const deny = (reason, extra = {}) => ({ ok: false, jid: null, name: null, reason, candidates: [], ...extra });

    if (cfg.send_enabled !== true) return deny('send_disabled');

    // resolveChat, not resolveRecipient: a group has no contact row, and MS-12
    // makes groups in the archive valid recipients by name as well as by JID.
    const resolved = store.resolveChat(recipient);
    if (!resolved.jid) {
      return resolved.candidates.length
        ? deny('ambiguous_recipient', { candidates: resolved.candidates })
        : deny('unknown_recipient');
    }
    const isSelf = getOwnJids().includes(resolved.jid);
    // "Known" means exactly that: a number that resolves syntactically is a
    // stranger unless the user saved it or wrote in its chat themselves
    // (store.isKnownContact, RV-20); a push name alone is the sender's own
    // claim. Cold outbound to strangers is the shape that gets numbers banned.
    // A group is known when the archive has its chat.
    if (!isSelf && !store.isKnownContact(resolved.jid) && !store.isKnownGroup(resolved.jid)) {
      return deny('unknown_recipient');
    }

    const limit = Number(cfg.send_rate_per_minute) > 0
      ? Number(cfg.send_rate_per_minute)
      : DEFAULT_RATE_PER_MINUTE;
    const cutoff = now() - 60_000;
    while (sent.length && sent[0] < cutoff) sent.shift();
    if (sent.length >= limit) {
      return { ...deny('rate_limited'), jid: resolved.jid, name: resolved.name, limit };
    }

    if (!confirm && !isSelf && !confirmed.has(resolved.jid)) {
      // The identity rides along with the refusal so the caller can show the
      // owner WHO it was about to message. A bare "confirm?" invites a reflexive
      // yes, which is the failure this control exists to prevent.
      return { ok: false, jid: resolved.jid, name: resolved.name, reason: 'confirmation_required', candidates: [] };
    }

    return { ok: true, jid: resolved.jid, name: resolved.name, reason: null, candidates: [] };
  }

  /**
   * Record the outcome of a checked message: charge the rate limit, remember
   * the confirmation, and audit. Called AFTER the send is attempted, so a
   * refusal is audited without consuming quota.
   *
   * @param {object} verdict - the return value of `check`
   * @param {{text?: string, error?: string, kind?: string, fileName?: string, bytes?: number}} [meta]
   *   For a file: its kind, name and size -- the bytes themselves are never
   *   passed here, for the same reason the text is not logged.
   */
  function record(verdict, meta = {}) {
    if (verdict.ok) {
      sent.push(now());
      if (verdict.jid) confirmed.add(verdict.jid);
    }
    audit(verdict, meta);
  }

  /** One audit line; the body is never in it. */
  function audit(verdict, meta, extra = {}) {
    fs.mkdirSync(path.dirname(auditPath), { recursive: true });
    fs.appendFileSync(auditPath, JSON.stringify({
      at: new Date(now()).toISOString(),
      ok: !!verdict.ok,
      jid: verdict.jid || null,
      name: verdict.name || null,
      reason: verdict.reason || null,
      // The message body is NOT logged. The archive already holds the
      // conversation; a second plaintext copy in a log nobody prunes is a
      // gratuitous place for it to leak from.
      textLength: typeof meta.text === 'string' ? meta.text.length : null,
      ...(meta.kind ? { kind: meta.kind, fileName: meta.fileName || null, bytes: meta.bytes ?? null } : {}),
      error: meta.error || null,
      ...extra,
    }) + '\n');
  }

  /**
   * Audit a REPLY in the user's own chat (POST /reply, MS-17). It answers a
   * message that already passed the inbound filter, so it is neither checked
   * nor charged to the rate limit (MS-10, as an earlier prototype), but every decision is
   * still one line (AC-514), marked `reply`.
   * @param {{ok: boolean, jid: string, reason?: string|null}} outcome
   * @param {{text?: string, error?: string, kind?: string, fileName?: string, bytes?: number}} [meta]
   * @param {object} [extra] - more fields for the line (`guest: true` for a guest reply)
   */
  function note(outcome, meta = {}, extra = {}) {
    audit({ ok: outcome.ok, jid: outcome.jid, name: null, reason: outcome.reason || null }, meta, { reply: true, ...extra });
  }

  return {
    check,
    record,
    note,
    /** @param {object|null} override */
    _setSettingsForTest(override) { settingsOverride = override; },
  };
}
