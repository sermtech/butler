"""Butler daemon entry point. plan.md 4.1, 4.9.

Boots the adapters named in config, warms them, and serves the satellite
page plus the protocol v1 WebSocket.

Adapter selection is explicit and fails loudly on an unknown name. There is
deliberately no fallback: a daemon that quietly starts with a stub ASR
because the real one failed to load is a daemon that appears to work and
answers nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import wave
from pathlib import Path

from aiohttp import WSCloseCode, web

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from daemon import connections as conn_mod  # noqa: E402
from daemon import paths as paths_mod       # noqa: E402
from daemon import desk as desk_mod         # noqa: E402
from daemon import dialogue                 # noqa: E402
from daemon import hub as hub_mod          # noqa: E402
from daemon import modes as modes_mod      # noqa: E402
from daemon import models as models_mod     # noqa: E402
from daemon import memory as memory_mod     # noqa: E402
from daemon import memory_index as memory_index_mod   # noqa: E402
from daemon import library as library_mod  # noqa: E402
from daemon import automations as automations_mod  # noqa: E402
from daemon import schedule as schedule_mod  # noqa: E402
from daemon import notify as notify_mod    # noqa: E402
from daemon import triggers as triggers_mod  # noqa: E402
from daemon import persona as persona_mod   # noqa: E402
from daemon import attachments as attach_mod  # noqa: E402
from daemon import containment as containment_mod  # noqa: E402
from daemon import artifacts as artifacts_mod  # noqa: E402
from daemon import guide as guide_mod      # noqa: E402
from daemon import chats as chats_mod      # noqa: E402
from daemon import settings as settings_mod  # noqa: E402
from daemon import apply_settings as apply_mod  # noqa: E402
from daemon import shortcuts as shortcuts_mod  # noqa: E402
from daemon import files as files_mod      # noqa: E402
from daemon import whatsapp as whatsapp_mod  # noqa: E402
from daemon import phone as phone_mod      # noqa: E402
from daemon.adapters import notify_telegram  # noqa: E402
from daemon import phonelink as phonelink_mod  # noqa: E402
from daemon import access as access_mod  # noqa: E402
from daemon import terminals as terminals_mod  # noqa: E402
from daemon import ports                   # noqa: E402
from daemon.adapters import asr_fake, brain_fake, tts_fake  # noqa: E402,F401
from daemon.adapters.endpointer_smartturn import (  # noqa: E402
    SmartTurnEndpointer)
from daemon.adapters.harness_pi import (  # noqa: E402
    reject_shell_metacharacters)

DEFAULT_PORT = 8140

# The end-of-turn model is NOT a port at all (plan.md 4.10 caps the seams at
# six: the fifth is `Harness`, the sixth `Notifier`); it is one async
# callable the Hub holds,
# with nothing plugging into it. It is still selected by name
# and still fails loudly on an unknown one, because "which engine" is a config
# decision everywhere else in this file and a special case here would be the
# thing nobody remembers. "none" is the honest way to run without it -- the
# smart turn mode then refuses out loud rather than pretending.
ENDPOINTERS = {"smartturn": SmartTurnEndpointer, "none": None}
# Targets answered by an ENGINE Butler already holds, not fetched over HTTP.
# Kept in their own table rather than bent into TARGETS, because the two fail
# differently and the handler must not blur that: a remote target can be
# unreachable and gets a 502, while a local one is either present or the
# adapter is broken, which is a bug and not a network condition. `info` is a
# synchronous property by contract and must not load anything (D-25), so this
# needs no catalogue and no timeout.
LOCAL_TARGETS = {
    # An EMPTY voice list is a failure, not an answer (AC-108). Since the list
    # became the engine's rather than a tuple in the adapter, it is empty
    # exactly when the sidecar could not be asked -- before a successful
    # `warm()`, or when it is dead -- and answering 200 with `[]` renders as a
    # picker with nothing in it, as though the engine simply had no voices.
    # That is the empty-list-as-though-all-were-well defect this table's own
    # `LocalListUnavailable` exists for, and it arrived the moment the
    # hardcoded fallback went away.
    # Names only, and only the ones that speak the owner's language (AC-154,
    # AC-156). `voice_ids` is the one place that knows an entry is
    # `{id, language}`.
    #
    # The FALLBACK to the unfiltered list is the part to read twice. A
    # language with no voice installed must not empty this list, because an
    # empty list here RAISES by design -- that is AC-108, where empty means
    # "the engine could not be asked" and renders as a picker with nothing in
    # it. Answering a language nothing speaks with EVERY voice is the honest
    # shape: the owner sees what the engine can actually say, picks one, and
    # hears it in the wrong accent rather than hearing a fault.
    "tts_voices": lambda hub: _nonempty(
        (ports.voice_ids(hub.tts.info, _language_of(hub))
         or ports.voice_ids(hub.tts.info)),
        "the speech engine has not reported its voices yet; it is still "
        "warming up, or the sidecar is not answering"),
    # The registry itself, so the harness picker offers what this daemon can
    # actually build. A list written into the settings schema would be wrong
    # the day a second harness landed, and wrong invisibly (AC-42).
    "harnesses": lambda hub: sorted(ports.HARNESSES),
    # `projects` IS GONE from this table, with the setting that fed it (the
    # owner's backlog item 4, 2026-09-11). It filled a datalist under the
    # shortcuts pane's path field, and now that the ROWS are the project list
    # that datalist would offer the owner exactly the paths already on screen
    # above it. A control whose every suggestion is already visible is noise,
    # so the page's fetch of it went in the same commit.
}
# The handler checks LOCAL_TARGETS first, so a name in both tables would be
# silently shadowed and the remote one would become unreachable with no error
# anywhere. Cheap to assert at import, and a boot-time failure beats a picker
# that quietly shows the wrong list.


def _language_of(hub) -> str:
    """The owner's language, or "" when there is no settings store.

    A free function rather than a lambda inline above, because `LOCAL_TARGETS`
    entries are read by people deciding what a picker will show and a
    conditional buried in one is how a picker quietly starts lying.

    `auto` answers "" -- EVERY voice -- and that is the honest picker for it
    (AC-213). Under automatic language there is no one language to narrow to,
    because the answer changes per message; narrowing to whatever the last
    turn happened to be would make the list move under the owner for reasons
    the page cannot explain. The control is DIMMED under `auto` anyway
    (`Settings.INERT_UNDER_AUTO_LANGUAGE`), so what it shows is what they get
    back when they turn it off.
    """
    settings = getattr(hub, "settings", None)
    if settings is None:
        return ""
    chosen = str(settings.get("language", "") or "")
    return "" if chosen == settings_mod.AUTO_LANGUAGE else chosen


def _nonempty(values: list, why: str) -> list:
    """Return `values`, or raise `LocalListUnavailable` if it is empty.

    The one shape every local lister needs and the reason it is a helper
    rather than a check inside each lambda: "could not answer" and "answered
    nothing" are the same bytes on the wire, and only one of them is true
    (AC-108). A picker rendered from an empty 200 says the engine has no
    voices; a 502 says nobody could be asked, which is what actually
    happened.
    """
    if not values:
        raise LocalListUnavailable(why)
    return values


class LocalListUnavailable(RuntimeError):
    """A local lister could not answer. Loud, never an empty list.

    Same rule as `models.ModelsUnavailable` and for the same reason: an empty
    picker rendered as though all were well is how a setting silently stops
    working (plan.md 4.4b).
    """


async def _harness_models(hub) -> list[str]:
    """What the CONFIGURED harness can be pointed at (`harness_model`, 7.4).

    Answered by the harness's own lister -- `pi --offline --list-models` for
    pi, 532 rows on this machine [MEASURED 2026-08-18] -- and the daemon never
    learns its vocabulary (AC-41): it looks the class up in the registry and
    calls one method. The class rather than a running child, because the
    picker must answer in endpoint mode too, before any harness has ever been
    started.

    Async, and in its own table, because this one SHELLS OUT. A `LOCAL_TARGETS`
    lambda is called synchronously on the event loop, and a subprocess there
    freezes every satellite for as long as it takes (D-25).
    """
    name = str((hub.settings.get("harness", "") if hub.settings else "")).strip()
    harness = ports.HARNESSES.get(name)
    if harness is None:
        raise LocalListUnavailable(
            f"the harness setting names {name!r}, which is not a harness this "
            f"daemon has; known: {sorted(ports.HARNESSES)}")
    lister = getattr(harness, "list_models", None)
    if lister is None:
        raise LocalListUnavailable(
            f"the {name!r} harness cannot list its own models, so nothing can "
            f"fill this picker")
    try:
        return await lister()
    except Exception as e:                            # noqa: BLE001
        # TRANSLATED at the boundary, never swallowed. Everything above is the
        # caller's mistake -- a harness name that does not exist, one that
        # cannot list at all -- and this is the WORLD's: pi not installed, the
        # 60 s lister timeout spent, an empty registry, a table whose shape
        # changed. `PiHarness.list_models` says all of those with a
        # RuntimeError, and an untranslated one leaves this route as a bare
        # 500 with no `error` key, so the picker can only report "HTTP 500"
        # (models.py: the two failures are different sentences).
        raise LocalListUnavailable(
            f"the {name!r} harness could not list its models: {e}") from e


async def _whatsapp_chats(_hub) -> list[dict]:
    """The archive's people and groups for the "Butler answers" picker
    (docs/messaging.md MS-14a): `{value, label}` entries, read-only, off the
    loop. No archive, or nothing in it, is a 502 saying so (AC-108's rule)."""
    try:
        return await asyncio.to_thread(
            whatsapp_mod.chat_choices,
            paths_mod.resolve().whatsapp / "whatsapp.db")
    except whatsapp_mod.ArchiveMissing as e:
        raise LocalListUnavailable(whatsapp_mod.EMPTY_ARCHIVE) from e


LOCAL_ASYNC_TARGETS = {"harness_models": _harness_models,
                       "whatsapp_chats": _whatsapp_chats}

# The line a voice audition speaks (AC-109). SHORT and fixed, for the reason
# `tts_kokoro.WARM_TEXT` is: the audition exists to let two voice NAMES be told
# apart, and a long sentence costs a synthesis the owner has to sit through
# before they can try the next one. It lives here rather than in the adapter
# because it is the DAEMON's line -- an adapter's warm-up text is its own
# business, and this one is read by a person.
PREVIEW_TEXT = "Hello. This is how I sound."
# What the preview registers its synthesis under. A turn id is an int, so a
# tuple can never collide with one however many turns have passed -- which is
# the whole requirement: `Tts.abort(handle)` finds in-flight synthesis by this
# value, so a preview sharing a live turn's handle would be cancelled by that
# turn's barge-in, or would silence the reply it played over. Shaped like the
# adapter's own `("warm",)` and `("anon", n)` handles.
PREVIEW_HANDLE = ("preview",)


# The most ambient audio one request may carry, derived from the SETTING's own
# ceiling rather than written down twice (AC-68): the largest window the owner
# can choose, at the one format every satellite sends -- 16 kHz mono int16, so
# 32000 bytes per second. 60 s is 1.92 MB.
#
# A ceiling is not optional here. The body is read into memory before anything
# looks at it, and a client bug or a body from anywhere on the tailnet must not
# be able to make a single-user daemon hold an arbitrary amount of a room. It
# is deliberately NOT aiohttp's `client_max_size`: raising that would loosen
# every other route on the way to loosening this one, and its default (1 MB)
# would refuse a perfectly legal 60 s window. So the read below is bounded here
# and the stream is never handed to `Request.read()`.
AMBIENT_MAX_BYTES = (int(settings_mod.BY_KEY["ambient_seconds"].maximum)
                     * ports.SAMPLE_RATE_IN * 2)
# The read size for both raw-PCM routes. Named for the shape rather than for
# ambient, because dictation reads its body exactly the same way and a
# constant naming one caller is a constant the second caller looks wrong using.
PCM_READ_CHUNK = 64 * 1024

# The most one attachment may carry, DERIVED from the setting's own maximum
# rather than written down twice (AC-197) -- `AMBIENT_MAX_BYTES`' idiom and its
# reason. This is the absolute cap a hand-edited settings file cannot raise;
# `post_attach` additionally enforces the VALUE the owner chose, which is the
# lesson `dictate_max_seconds` bought: reading only `.maximum` made that
# control inert.
ATTACHMENT_MAX_BYTES = (int(settings_mod.BY_KEY["attachment_max_mb"].maximum)
                        * 1_000_000)
ATTACHMENT_DEFAULT_MB = int(settings_mod.BY_KEY["attachment_max_mb"].default)


async def post_ambient(hub, request) -> web.Response:
    """The last N seconds of the room, for one turn (AC-68, AC-69, AC-70).

    `POST /api/ambient?turn=<id>` with a raw 16 kHz mono int16 body. The page
    sends it at capture_start -- the first moment a daemon-minted turn id
    exists (D-6) -- and does not await the answer.

    202 and nothing else on the happy path, because there is nothing for the
    page to do with a fuller answer: it has already discarded its own copy.
    202 AND DISCARD when the turn is not the one a session owns, or the window
    is off; the body says which, for a person reading it, and neither is an
    error the page should draw. A body past the ceiling IS refused (413),
    because that one is a bug rather than a race.

    Never fatal (D-1). Everything this route can lose is CONTEXT; the turn,
    the reply and the microphone are on the other side of the socket and are
    untouched by anything that happens here.
    """
    try:
        turn = int(request.query.get("turn", ""))
    except (TypeError, ValueError):
        return web.json_response(
            {"error": "name the turn: POST /api/ambient?turn=<id>"},
            status=400)
    pcm = bytearray()
    while True:
        chunk = await request.content.read(PCM_READ_CHUNK)
        if not chunk:
            break
        pcm += chunk
        if len(pcm) > AMBIENT_MAX_BYTES:
            # Refused by SIZE, and the read stops here rather than draining a
            # body that is already too big to keep.
            return web.json_response(
                {"error": f"an ambient clip may not exceed "
                          f"{AMBIENT_MAX_BYTES} bytes, which is the largest "
                          f"window this daemon offers", "turn": turn},
                status=413)
    stored = hub.take_ambient(turn, bytes(pcm))
    return web.json_response({"stored": stored, "turn": turn,
                              "bytes": len(pcm)}, status=202)


DICTATE_MAX_BYTES = (int(settings_mod.BY_KEY["dictate_max_seconds"].maximum)
                     * ports.SAMPLE_RATE_IN * 2)
DICTATE_DEFAULT_SECONDS = int(settings_mod.BY_KEY["dictate_max_seconds"].default)


# Control characters that become a SPACE rather than being deleted, because
# deleting them JOINS the words either side: "git status\nls" would become
# "git statusls", which is a command nobody dictated. Carriage return, line
# feed, tab, vertical tab and form feed -- every whitespace control there is.
_PASTE_TO_SPACE = "\r\n\t\v\f"


def paste_safe(text: str) -> str:
    """Transcript in, something that cannot execute a command out (AC-123).

    The dictation route's answer is pasted into WHATEVER the owner's caret is
    in, and that can be a terminal. A newline in a shell is not a character,
    it is a RETURN: dictate "check the tests" into a prompt that already holds
    `rm -rf build` and a stray line feed runs it. The recognizer is not
    supposed to emit one, which is exactly the reason to enforce it here
    rather than to trust that -- an engine swap, a model change or a
    punctuation setting are all one line away from making it untrue, and the
    failure is silent and irreversible.

    Two rules, and the difference between them is the whole point (the shape
    is `scripts/core/terminal-dictation.js` in an earlier prototype's tree, which paid
    for it first):

    - whitespace controls become a SPACE, never nothing, so words either side
      of them cannot be welded into a word nobody said;
    - every other C0 control and DEL is DELETED, because ESC in a terminal is
      the start of an ANSI escape sequence and there is no reading of a
      dictated sentence in which one belongs.

    Idempotent by construction: its output contains none of its inputs.
    """
    out = []
    for ch in text:
        if ch in _PASTE_TO_SPACE:
            out.append(" ")
        elif ch < " " or ch == "\x7f":
            continue
        else:
            out.append(ch)
    return "".join(out).strip()


async def post_dictate(hub, request) -> web.Response:
    """One held-key clip, decoded and answered as TEXT (AC-113, AC-115).

    `POST /api/dictate` with a raw 16 kHz mono int16 body. The desk agent
    captures while the hotkey is down, posts on release, and pastes the
    answer where the owner's caret already is.

    The body is read off `request.content` in chunks against a ceiling derived
    from the setting's own maximum -- the ambient rule, for the ambient reason
    (`daemon/AGENTS.md`): aiohttp's `client_max_size` is neither raised for
    every other route nor relied on for this one.

    It differs from `post_ambient` in exactly one way, and the difference is
    the point. Ambient is fire-and-forget and answers 202, because everything
    it can lose is context. A dictation IS the product, so it answers the text
    -- and every failure gets a status the caller can tell apart, because what
    the desk agent does with a 200 is paste its body into whatever the owner
    had focused. An empty 200, or an apology in a `text` field, ends up inside
    their document and Butler cannot take it back (AC-115).

    409 is a REFUSAL and not a failure: the recognizer is busy, the owner
    presses the key again. 422 is a clip nobody spoke into. 502 is the
    recognizer itself falling over. None of the three carries text.
    """
    # The owner's own ceiling, not just the schema's. Reading only `.maximum`
    # made the control INERT -- it saved, reported success and bounded nothing,
    # which is `asr_latency`'s `high` defect by the front door
    # (`daemon/AGENTS.md`, AC-7b). `DICTATE_MAX_BYTES` stays as the absolute
    # cap so a hand-edited settings file cannot raise it past what the schema
    # allows, and the daemon enforces the setting rather than trusting the
    # desk agent to: a body can arrive from anywhere on the tailnet.
    ceiling = DICTATE_MAX_BYTES
    if hub.settings is not None:
        ceiling = min(ceiling,
                      int(hub.settings.get("dictate_max_seconds",
                                           DICTATE_DEFAULT_SECONDS))
                      * ports.SAMPLE_RATE_IN * 2)
    pcm = bytearray()
    while True:
        chunk = await request.content.read(PCM_READ_CHUNK)
        if not chunk:
            break
        pcm += chunk
        if len(pcm) > ceiling:
            # Refused by SIZE, and the read stops here rather than draining a
            # body already too big to keep. A key held down and never released
            # is the case this exists for.
            return web.json_response(
                {"error": f"a dictation may not exceed {ceiling} bytes, "
                          f"which is the longest clip this daemon is set to "
                          f"decode"}, status=413)
    try:
        text = await hub.dictate(bytes(pcm))
    except hub_mod.AsrBusy as e:
        return web.json_response({"error": str(e)}, status=409)
    except Exception as e:                                # noqa: BLE001
        print(f"[butler] a dictation of {len(pcm)} bytes would not decode: "
              f"{e!r}")
        return web.json_response(
            {"error": f"the recognizer could not decode that clip: {e}"},
            status=502)
    # Sanitised HERE, at the one boundary where text leaves the daemon
    # towards a paster (AC-123). A second route that pastes must call the same
    # function, which is why it has a name rather than being three lines
    # inline. The emptiness check comes AFTER, because a clip that decoded to
    # nothing but control characters is a clip nobody spoke into.
    text = paste_safe(text)
    if not text:
        return web.json_response(
            {"error": "nothing was said in that clip"}, status=422)
    return web.json_response({"text": text})


async def post_attach(hub, request) -> web.Response:
    """One file the owner is showing Butler (AC-197..AC-199).

    `POST /api/attach?device=<id>&name=<filename>` with the FILE as the body.
    No multipart: there is none anywhere in this daemon, and the two raw-PCM
    routes already established that the body IS the payload and the metadata
    rides in the query.

    Read off `request.content` in `PCM_READ_CHUNK` pieces against a ceiling
    DERIVED from `attachment_max_mb` rather than written down twice (D-12),
    with the read ABORTED when it is crossed rather than draining a body
    already too large -- `post_ambient`'s shape, for `post_ambient`'s reason:
    aiohttp's `client_max_size` is neither raised for every other route nor
    relied on for this one.

    **IT HANGS ON THE SESSION, PENDING AGAINST THE CONVERSATION THAT DEVICE
    IS SHOWING** (AC-199, AC-247). The next turn from that satellite IN THAT
    CONVERSATION takes it, whichever door the turn comes in by, which is what
    makes "show Butler this and ask about it out loud" work -- a field on the
    `typed` frame would have made an attachment a typed-only thing and changed
    `plan.md` 4.3 for the privilege. Which conversation that is comes from
    `hub.chat_for`, so this route holds no opinion of its own about views.

    Refusals name what was wrong: 400 for no device or a device with no
    session, 413 for a body past the ceiling, 415 for bytes this daemon will
    not keep (AC-198 -- the TYPE is sniffed and never trusted from the caller,
    because the file is written to disk and a coding agent may be pointed at
    it).
    """
    if hub.attachments is None:
        return web.json_response(
            {"error": "this daemon keeps no attachments"}, status=404)
    device = (request.query.get("device") or "").strip()
    session = next((s for s in list(hub.sessions)
                    if s.device_id == device), None) if device else None
    if session is None:
        # Refused rather than attached to whatever session happens to be
        # connected: an attachment is context for the NEXT thing this device
        # says, and putting it on another device's session would hand a file
        # to a question asked in a different room (D-6).
        return web.json_response(
            {"error": "name the device this is attached from: "
                      "/api/attach?device=<id>; no session here has that id"},
            status=400)
    ceiling = ATTACHMENT_MAX_BYTES
    if hub.settings is not None:
        ceiling = min(ceiling,
                      int(hub.settings.get("attachment_max_mb",
                                           ATTACHMENT_DEFAULT_MB)) * 1_000_000)
    data = bytearray()
    while True:
        chunk = await request.content.read(PCM_READ_CHUNK)
        if not chunk:
            break
        data += chunk
        if len(data) > ceiling:
            return web.json_response(
                {"error": f"an attachment may not exceed {ceiling} bytes, "
                          f"which is what this daemon is set to accept"},
                status=413)
    if not data:
        return web.json_response({"error": "that file is empty"}, status=400)
    mode = "text_then_vision"
    if hub.settings is not None:
        mode = str(hub.settings.get("document_reading", mode))
    try:
        # Off the event loop: this parses a PDF, which is real work on a
        # single-threaded daemon holding a microphone (D-25).
        att = await asyncio.to_thread(
            hub.attachments.add, bytes(data),
            request.query.get("name") or "attachment", mode)
    except attach_mod.Unsupported as e:
        return web.json_response({"error": str(e)}, status=415)
    except Exception as e:                                # noqa: BLE001
        print(f"[butler] an attachment of {len(data)} bytes would not "
              f"store: {e!r}")
        return web.json_response(
            {"error": f"that file could not be stored: {e}"}, status=502)
    # IT HANGS ON THE CONVERSATION THAT DEVICE IS SHOWING (AC-247), resolved
    # by the hub and never here: a file pending against the DEVICE alone
    # arrives in whichever conversation that device speaks in next, and a
    # coding agent then reads it off disk.
    chat = hub.pend_attachment(session, att)
    print(f"[butler] {att.name!r} attached from {device[:8]} "
          f"in {chat or 'no named conversation'} "
          f"({att.kind}, {att.bytes} bytes, {len(att.text)} chars read)")
    return web.json_response(att.public())


async def post_answer(hub, request) -> web.Response:
    """The human's answer to a question a coding agent asked (AC-234, AC-250).

    `POST /api/answer` with `{"qid": ..., "value"|"confirmed"|"cancelled": ...}`.

    **A ROUTE AND NOT A SOCKET FRAME**, which is `post_ambient`'s precedent
    and, here, a decision rather than a convention: only a route can tell the
    answerer their answer was TOO LATE. A frame sent into the socket would be
    accepted by a daemon that no longer holds the question and the owner would
    walk away believing they had approved something the agent already
    defaulted on -- the recorded Codex failure this criterion is named after
    (`docs/parallel-agents.md` 9.4). It also works from a device whose socket
    is reconnecting.

    **A `qid` this daemon does not hold is 410 Gone with the REASON**, and
    nothing is written to the child. The four reasons are all nameable: the
    agent resolved it on its own timeout, the conversation was stopped, the
    child died, or the daemon restarted (`hub.QUESTION_GONE`).

    400 for a body that does not say what the answer IS: exactly one of
    `value`, `confirmed` and `cancelled`, refused rather than guessed at,
    because a dialog answered with the wrong key unblocks the agent with a
    default nobody chose.
    """
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        return web.json_response({"error": "the body must be json"},
                                 status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "the body must be a json object"},
                                 status=400)
    qid = str(body.get("qid") or "").strip()
    if not qid:
        return web.json_response(
            {"error": "name the question: {\"qid\": ..., \"value\": ...}. The "
                      "id is the coding agent's own and it is on the "
                      "`question` frame"}, status=400)
    try:
        answer = ports.Answer(cancelled=bool(body.get("cancelled", False)),
                              value=(None if body.get("value") is None
                                     else str(body["value"])),
                              confirmed=(None if body.get("confirmed") is None
                                         else bool(body["confirmed"])))
    except ValueError as e:
        return web.json_response({"error": str(e), "qid": qid}, status=400)
    if qid.startswith(hub.PROPOSAL_PREFIX):
        # A schedule or recipe proposal's card (docs/proactive.md PR-21,
        # docs/recipe-tools.md RT-3): the daemon's own question, never a
        # harness's, answered in the hub's block.
        verdict = await hub.answer_proposal(qid, answer)
    else:
        verdict = await hub.answer_question(qid, answer)
    if not verdict.get("ok"):
        return web.json_response({**verdict, "qid": qid}, status=410)
    return web.json_response(verdict)


def terminal_cwd(settings) -> str:
    """Where a terminal opens when the CALLER named no directory.

    The ladder is `harness_cwd`, then the TMP scratch dir -- the same tail
    `harness_spec_resolver` uses and for the same reason (AC-31, amended
    2026-08-18): there is always somewhere harmless to start, and it is never a
    guess at a named project.

    `terminal_cwd` used to head that ladder and BE this whole function's
    answer, because the route took no path from the caller. The owner retired
    the setting on 2026-09-09 in favour of a pane of project rows with a chord
    each, so the directory now normally arrives in the request and this is only
    the fallback for a caller that named none (`docs/shortcuts.md` D4, AC-144).
    The `projects` allow-list is deliberately NOT reinstated as a gate on the
    caller's path: the rows the owner types in the pane are exactly the list
    they would be checked against, and a second list to keep in step is the
    drift a store exists to avoid. `harness_spec_resolver` keeps its own
    `projects` check inline regardless, because a birth hands it a path
    chosen by a click or off a MICROPHONE, and that one is still gated.
    """
    cwd = str(settings.get("harness_cwd", "") or "").strip() if settings else ""
    if not cwd:
        scratch = REPO / "TMP"
        scratch.mkdir(parents=True, exist_ok=True)
        cwd = str(scratch)
    return cwd


# What the terminal action runs, and it is a WHOLE command line for `wt.exe`
# rather than a bare console command. `cmd` because pi is `pi.cmd` and
# CreateProcess does not read PATHEXT (plan.md 4.5); `/k` because the owner is
# meant to keep typing in it after the agent exits.
#
# `{agent}` is filled from `shortcuts.TERMINAL_AGENTS` -- a closed table of
# three program names -- and NEVER from the request. A caller names a KEY of
# that table; if it could name the value, this route would be a remote shell on
# an unauthenticated tailnet surface (AC-141).
WINDOWS_TERMINAL_ARGV = ("wt.exe", "-d", "{cwd}", "cmd", "/k", "{agent}")

# macOS (docs/macos.md): Terminal.app through osascript. The directory and the
# agent's words arrive as osascript ARGUMENTS and each is shell-quoted by
# AppleScript's `quoted form of`, so neither is ever AppleScript or shell text.
_MAC_TERMINAL_SCRIPT = """on run argv
set cmdline to "cd " & quoted form of (item 1 of argv) & " &&"
repeat with i from 2 to count of argv
set cmdline to cmdline & " " & quoted form of (item i of argv)
end repeat
tell application "Terminal"
activate
do script cmdline
end tell
end run"""
MAC_TERMINAL_ARGV = ("osascript", "-e", _MAC_TERMINAL_SCRIPT, "{cwd}",
                     "{agent}")
TERMINAL_ARGV = (WINDOWS_TERMINAL_ARGV if os.name == "nt"
                 else MAC_TERMINAL_ARGV)


def spawn_terminal(cwd: str, agent: str = shortcuts_mod.DEFAULT_AGENT) -> None:
    """Open a terminal running a coding agent in `cwd` (Windows Terminal, or
    Terminal.app on macOS through `MAC_TERMINAL_ARGV`), OUTSIDE the Job
    Object.

    **No `childproc.assign`, deliberately** (AC-119). Every other child in this
    repository is assigned to the kill-on-close Job Object immediately after
    spawning, and `daemon/childproc.py` exists to make that the default -- so
    the next reader's correct instinct is to add it here. It must not be
    added. `POST /api/restart` lands between turns, and closing the terminal
    the owner is typing in, mid-command, is exactly what AC-119 forbids. Here
    it is not even reachable: `wt.exe` hands the command to Windows Terminal,
    which owns the resulting `cmd` itself, so the terminal is not a descendant
    of this daemon at all. (The desk agent could not do this either way: it IS
    in the job, and the job sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and
    nothing else, so `CREATE_BREAKAWAY_FROM_JOB` from inside it fails with
    ERROR_ACCESS_DENIED. That is why the daemon spawns and the agent asks.)

    **`wt.exe` and NOT `cmd` in a `CREATE_NEW_CONSOLE`**, which is what shipped
    for one commit and is what the owner reported as "a ghost frame terminal,
    plus another cmd". MEASURED 2026-09-09, spawning from the live daemon and
    enumerating processes and visible windows:

      * `CREATE_NEW_CONSOLE` -> `cmd.exe` plus a `conhost.exe` of its own, a
        Windows Terminal window titled generically `'Terminal'`, and **no
        `node.exe` anywhere 16 s later -- pi never started at all.** An empty
        frame and a bare cmd is exactly the two-window report.
      * `wt.exe -d <cwd> cmd /k pi` -> `cmd.exe` parented to
        `WindowsTerminal.exe` with `node.exe` running under it, ONE window,
        titled `'pi - TMP'` by pi itself, and no `conhost.exe` of ours.

    Returns NOTHING, and that is the honest answer rather than a missing
    feature. `wt.exe` is a launcher: it hands off to Windows Terminal and its
    own pid is unreliable -- measured exiting inside 0.5 s in one run and still
    alive at 1 s in another, depending on whether an existing Terminal window
    adopted the command. Any pid this function returned would be that
    launcher's, so a caller could neither wait on it nor kill the terminal with
    it. `test_desk.py` finds the real terminal by a unique marker in its
    command line, which is what a caller would have to do too.
    """
    # SPLICED, not interpolated (`docs/shortcuts.md` 9.1). The table's value is
    # an argv TUPLE because a command can carry a flag -- claude carries
    # `--dangerously-skip-permissions` (D9, AC-150) -- and `subprocess` quotes
    # any list element containing a space, so one element holding both words
    # would reach `cmd` as a single quoted token and cmd would look for a
    # program with that literal name.
    #
    # Only an element that IS `{agent}` is replaced, which is what leaves the two
    # callers that override this whole template working: `test_desk.py`'s AC-119
    # host and `probe_terminal.py` both supply their own trailing command and
    # neither has an `{agent}` slot to splice into.
    command = shortcuts_mod.TERMINAL_AGENTS[agent]
    argv: list[str] = []
    for part in TERMINAL_ARGV:
        if part == "{agent}":
            argv.extend(command)
        else:
            argv.append(part.format(cwd=cwd))
    subprocess.Popen(argv)


async def post_terminal(hub, request) -> web.Response:
    """Open a coding agent in a terminal (AC-136, AC-141, AC-120).

    `POST /api/terminal` with `{"cwd", "agent"}`, both optional, answering
    `{"cwd", "agent"}`.

    **IT TAKES A PATH FROM THE CALLER AGAIN, and that is a REVERSAL** of the
    property set on 2026-09-09 -- owner's ruling, `docs/shortcuts.md` D4. The
    cost is one line and is not re-litigated: an unauthenticated tailnet
    request can once again choose where a full-capability coding agent starts.
    What the owner asked for is a pane of project rows with a chord each, and a
    route that could not be told which row was pressed cannot serve it.

    Every other gate survives, and one is new.

    * `agent` is a KEY of the closed `shortcuts.TERMINAL_AGENTS` table and is
      resolved to a command by `spawn_terminal`. Nothing from the request is
      ever interpolated into the command line, which is the difference between
      this route and a remote shell.
    * `reject_shell_metacharacters` on the directory, the SAME helper
      `harness_pi._argv` uses and not a second copy (AC-120). It is
      load-bearing rather than defence in depth: the directory goes onto
      `wt.exe`'s own command line, which wt parses and which then carries a
      nested `cmd /k <agent>`.
    * `Path(cwd).is_dir()`, checked HERE because nothing downstream will.
      `wt.exe -d missing` does not fail -- it opens a window and complains
      inside it -- so without this the route answers 200 and the room hears the
      success cue for a terminal that never opened (AC-7b).

    A blank `cwd` falls back to `terminal_cwd(settings)` rather than being
    refused, so a caller with nothing configured still gets a harmless scratch
    directory instead of an error (AC-31 as amended).

    Refusals and no empty 200s, the rule `/api/dictate` follows: the desk agent
    branches on the status alone and cues the room, so a failure that answered
    200 would beep "done" with no window anywhere.
    """
    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001
        # A malformed or absent body is not an error: every field is optional
        # and the settings answer for all of them. Refusing here would break
        # the one caller that sends nothing.
        body = {}
    if not isinstance(body, dict):
        body = {}
    cwd = str(body.get("cwd") or "").strip() or terminal_cwd(hub.settings)
    agent = str(body.get("agent") or "").strip() or shortcuts_mod.DEFAULT_AGENT
    if agent not in shortcuts_mod.TERMINAL_AGENTS:
        return web.json_response(
            {"error": f"no coding agent called {agent!r}; this daemon can "
                      f"start {sorted(shortcuts_mod.TERMINAL_AGENTS)}"},
            status=400)
    try:
        reject_shell_metacharacters("cwd", cwd)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not Path(cwd).is_dir():
        return web.json_response(
            {"error": f"{cwd} is not a directory, so no terminal can be "
                      f"opened there. Check the shortcut's path."},
            status=400)
    try:
        spawn_terminal(cwd, agent)
    except OSError as e:
        print(f"[butler] a {agent} terminal in {cwd} would not start: {e!r}",
              flush=True)
        return web.json_response(
            {"error": f"the terminal would not start: {e}"}, status=502)
    print(f"[butler] {agent} open in {cwd}; it outlives this daemon on "
          f"purpose (AC-119)", flush=True)
    return web.json_response({"cwd": cwd, "agent": agent})


# --------------------------------------------------------------------------- #
# the shortcuts pane (docs/shortcuts.md 4.1)
# --------------------------------------------------------------------------- #
def _annotate_busy(hub, rows: list[dict]) -> None:
    """Mark each project row `busy` -- a BUSY child (a turn in flight) holds
    its one granted slot in that project RIGHT NOW (docs/filing-and-pacing.md
    1.7, phase 5 step 1: "the page must know a collision is coming before it
    commits").

    IN-MEMORY ONLY, unlike `chats` above: `_Host.busy_children_for_project`
    reads `_project_claims`/`_idle_since` directly, no disk walk at all -- so
    this runs on EVERY answer, `GET` and `POST` alike, and the page never has
    to carry a stale flag forward the way `paintShortcuts` does for `chats`
    (that one is GET-only for a stated cost reason; this one costs nothing
    extra to compute on a write too).

    Reads the SAME state `_enforce_project_limit` itself decides a real
    collision from (D-18), so this can never disagree with what actually
    starting a turn in that project would find. A daemon with no harness
    host (endpoint mode, or a test `Hub` with no session store) has no
    children to be busy, so every row reads `False` -- honest, since nothing
    here could ever collide.
    """
    host = getattr(hub.session_brain(), "host", None)
    for row in rows:
        row["busy"] = bool(
            host is not None
            and host.busy_children_for_project(row["path"]))



async def get_shortcuts(hub, _request) -> web.Response:
    """The pane's whole state: `{open, dictate, rows, agents, keys, bare_keys}`.

    `agents` and `keys` ride along so nothing in `web/` writes down the three
    program names or the key table -- a page that holds a closed daemon table is
    naming one (AC-41). 404 when this daemon has no store, which is what every
    Hub built by a test that does not know shortcuts exist gets.

    **EACH ROW CARRIES ITS CONVERSATION COUNT** (`chats`), so a rail can say
    how much is in a workspace without asking per row. It is ONE walk of
    `state/chats/` for every row rather than one per row, and it goes off the
    event loop for the reason the scoped listing does (D-25) -- this is that
    same walk, and it is now on the path of a page load.
    In ENDPOINT MODE there are no conversations at all: `session_brain()`
    answers None, every count is 0, and the route still serves the rows,
    because the pane is a terminal launcher there and does not stop being one.
    The count is on the GET alone. `POST /api/shortcuts` answers the same
    payload and deliberately does NOT pay for a directory walk: it is the route
    a sidebar DRAG posts to on every release, and a count nobody looked at is
    not worth reading every chat on disk for.
    """
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    payload = hub.shortcuts.public()
    counts: dict[str, int] = {}
    brain = hub.session_brain()
    if brain is not None:
        # UNBOUNDED, unlike the listing: `CHATS_LISTED` cuts what a sidebar
        # draws, and a count cut to the same bound would quietly under-report
        # the workspace with the most conversations in it -- which is the one
        # the number exists to point at.
        for chat in await asyncio.to_thread(brain.session_list):
            found = _row_for(hub, str(chat.get("project") or ""))
            if found is not None:
                counts[found.id] = counts.get(found.id, 0) + 1
    for row in payload["rows"]:
        row["chats"] = counts.get(row["id"], 0)
    _annotate_busy(hub, payload["rows"])
    return web.json_response(payload)


async def post_shortcuts(hub, request) -> web.Response:
    """Change ONE thing about the pane, and say which.

    The body carries EXACTLY ONE of these keys, and a body carrying none or
    more than one is a 400 naming what was allowed:

      {"open": bool}                       RETIRED with the projects pane;
                                           accepted so an existing
                                           `shortcuts.json` round-trips
      {"show_chords": bool}                whether the chord slots show (D8)
      {"sidebar_collapsed": bool}          whether the LIST is hidden (AC-166)
      {"rail_collapsed": bool}             whether the RAIL is icons-only
                                           (u-railB1 item 3, 2026-09-16)
      {"sidebar_width": <px>}              the list's width, clamped
      {"chat_grouping": "off"|"state"|"project"}  how the chat list is
                                           grouped (D1/C, docs/triage-list.md
                                           phase 3, 2026-09-16); RETIRED on
                                           the page with the tree, like
                                           `rail_collapsed`
      {"expanded_projects": [row ids]}     which project nodes the sidebar
                                           tree shows open (AC-291)
      {"right_panel": ""|"files"|"library"}  which ONE panel the desk's
                                           right column shows; anything else
                                           is a 400 (docs/automations.md
                                           AU-10; it replaced `files_open`)
      {"dictate": <chord>}                 the dictation binding (AC-137)
      {"row": {id?, path, label?, chords}} add or MODIFY a project row

    One route rather than three because it is one resource and the page makes
    one call per edit. It is strict about the shape for the reason
    `post_connections` refuses a body carrying a key value: a caller that sent
    two fields believes both were applied, and quietly honouring one means the
    other fails later for a reason nobody connects to this request.

    A chord is either a canonical string or a capture object -- `{"code":
    "NumpadMultiply", "ctrl": true}` from a browser `KeyboardEvent`, or
    `{"key": "numpad_multiply"}` from the picker fallback. The DAEMON maps it,
    so there is no key table in JavaScript to drift (`docs/shortcuts.md` D3).

    The list is re-served on success, because every edit changes what the pane
    must draw -- a chord is canonicalised on the way in and a new row is given
    an id, so the page cannot know the result without being told.
    """
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001
        return web.json_response({"error": "body must be JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"},
                                 status=400)
    # `sidebar_collapsed` is view state and lives HERE rather than in the
    # browser, for the reason the fold above it already does: AC-8 bans
    # browser storage outright, and two devices must not disagree about a
    # page they are both looking at (AC-166).
    named = [k for k in ("open", "show_chords", "sidebar_collapsed",
                         "rail_collapsed", "sidebar_width", "chat_grouping",
                         "expanded_projects", "right_panel", "dictate",
                         "row")
             if k in body]
    if len(named) != 1:
        return web.json_response(
            {"error": "the body carries exactly one of open, show_chords, "
                      "sidebar_collapsed, rail_collapsed, sidebar_width, "
                      "chat_grouping, expanded_projects, right_panel, dictate "
                      "or row; got "
                      f"{sorted(body) or 'nothing'}"},
            status=400)
    try:
        if named[0] == "open":
            hub.shortcuts.set_open(bool(body["open"]))
        elif named[0] == "show_chords":
            hub.shortcuts.set_show_chords(bool(body["show_chords"]))
        elif named[0] == "sidebar_collapsed":
            hub.shortcuts.set_sidebar_collapsed(
                bool(body["sidebar_collapsed"]))
        elif named[0] == "rail_collapsed":
            # u-railB1 item 3, 2026-09-16: the RAIL's own collapse, stored
            # beside `sidebar_collapsed` and wired the same way -- see
            # `daemon/shortcuts.py`'s `set_rail_collapsed`.
            hub.shortcuts.set_rail_collapsed(bool(body["rail_collapsed"]))
        elif named[0] == "sidebar_width":
            hub.shortcuts.set_sidebar_width(body["sidebar_width"])
        elif named[0] == "chat_grouping":
            # D1/C, docs/triage-list.md phase 3: wired the same way as
            # `rail_collapsed` beside it, refused by `ShortcutError` below
            # for a value off the closed three-item set.
            hub.shortcuts.set_chat_grouping(body["chat_grouping"])
        elif named[0] == "expanded_projects":
            hub.shortcuts.set_expanded_projects(body["expanded_projects"])
        elif named[0] == "right_panel":
            hub.shortcuts.set_right_panel(body["right_panel"])
        elif named[0] == "dictate":
            hub.shortcuts.set_dictate(body["dictate"])
        else:
            row = body["row"]
            if not isinstance(row, dict):
                return web.json_response(
                    {"error": "row must be an object carrying at least a "
                              "path"}, status=400)
            hub.shortcuts.upsert(
                sid=str(row.get("id") or ""), path=row.get("path") or "",
                label=row.get("label") or "",
                chords=row.get("chords") or {}, must_exist=True)
    except shortcuts_mod.ShortcutError as e:
        return web.json_response({"error": str(e)}, status=400)
    payload = hub.shortcuts.public()
    _annotate_busy(hub, payload["rows"])
    return web.json_response(payload)


# --------------------------------------------------------------------------- #
# RECIPES AND TASKS (docs/automations.md section 5, phases 1 and 3).
#
# Every route reaches `daemon/library.py` through `asyncio.to_thread`, because
# every store call is disk (D-1). A refusal is a 400 carrying the store's own
# sentence, which names the rule; an unknown id is a 404 naming it -- the
# `KeyError` shape `ShortcutStore.get` uses. Every change broadcasts ONE
# `{type:"library", what, project}` frame: a hint to re-read, never the data,
# `settings`' own rule. `project` is "" for the Global section -- a global
# recipe or a ROOT-row task -- so a page shows it whatever project it is on.
# --------------------------------------------------------------------------- #
async def _library_body(request):
    """The JSON object a library write carries, or a 400 response."""
    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001
        return None, web.json_response({"error": "the body must be JSON"},
                                       status=400)
    if not isinstance(body, dict):
        return None, web.json_response(
            {"error": "the body must be a JSON object"}, status=400)
    return body, None


async def _library_call(fn, *args, **kwargs):
    """Run one store call off the loop; answer `(result, error response)`."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs), None
    except library_mod.LibraryRefused as e:
        return None, web.json_response({"error": str(e)}, status=400)
    except KeyError as e:
        return None, web.json_response({"error": e.args[0] if e.args
                                        else str(e)}, status=404)


def _library_scope(hub, project: str) -> str:
    """What a `library` frame names: "" for the Global section."""
    if project == library_mod.GLOBAL or (
            hub.shortcuts is not None and project == hub.shortcuts.root_id()):
        return ""
    return project


async def library_changed(hub, what: str, project: str) -> None:
    """The ONE `library` broadcast (docs/automations.md 5, AC-467)."""
    await hub.broadcast(type="library", what=what,
                        project=_library_scope(hub, project))


def _with_hub(hub, handler):
    """An aiohttp handler for a module-level `handler(hub, request)`."""
    async def route(request):
        return await handler(hub, request)
    return route


def _no_library(hub):
    if hub.library is None:
        return web.json_response(
            {"error": "this daemon holds no library"}, status=404)
    return None


async def get_library(hub, request) -> web.Response:
    """`GET /api/library?project=<row id>` -- the panel's one read. No
    project is the root row, which is where a chat with no project lives."""
    refused = _no_library(hub)
    if refused:
        return refused
    project = request.query.get("project", "") or hub.library.root_id()
    if project not in hub.library.rows():
        return web.json_response(
            {"error": f"no project with id {project!r}"}, status=404)
    used, err = await _used_by(hub)
    if err:
        return err
    now, tz = _word_clock(hub)
    doc, err = await _library_call(hub.library.panel, project, used, now, tz)
    if err:
        return err
    if hub.automations is not None:
        doc["automations"], err = await _library_call(
            hub.automations.panel, project, now)
    return err or web.json_response(doc)


def _word_clock(hub):
    """The clock and zone every served time word is read in
    (docs/lantern-ui.md section 8): the scheduler's, so a test's injected
    clock is the one the words follow. A daemon holding no automations has
    no scheduler, and reads the machine's own."""
    if hub.scheduler is None:
        return automations_mod.utcnow(), schedule_mod.local_zone()
    return hub.scheduler.clock(), hub.automations.tz


async def _used_by(hub):
    """`Recipe.public`'s `usedBy` map, read fresh per request; a daemon
    holding no automation store has no automation using anything."""
    if hub.automations is None:
        return {}, None
    return await _library_call(hub.automations.used_by)


async def get_recipes(hub, request) -> web.Response:
    refused = _no_library(hub)
    if refused:
        return refused
    used, err = await _used_by(hub)
    if err:
        return err
    found, err = await _library_call(hub.library.recipes.recipes)
    return err or web.json_response(
        {"recipes": [r.public(used) for r in found]})


async def post_recipe(hub, request) -> web.Response:
    """`{id?, name, project, prompt}`: create, or replace the one with that
    id. Answers the saved recipe, placeholders included."""
    refused = _no_library(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    saved, err = await _library_call(
        hub.library.recipes.save, str(body.get("id") or "") or None,
        body.get("name"), body.get("project"), body.get("prompt"))
    if err:
        return err
    await library_changed(hub, "recipes", saved.project)
    used, err = await _used_by(hub)
    return err or web.json_response(saved.public(used))


async def delete_recipe(hub, request) -> web.Response:
    """Delete one recipe; 409 while an automation runs it."""
    refused = _no_library(hub)
    if refused:
        return refused
    rid = request.match_info.get("rid", "")
    if hub.automations is not None:
        # A recipe an automation runs cannot vanish under it (section 5):
        # 409 naming them, so the owner knows what to change first.
        users, err = await _library_call(hub.automations.uses, rid)
        if err:
            return err
        if users:
            names = ", ".join(
                f"{u['schedule']['label']} in "
                f"{hub._project_label(u['project'])}" for u in users)
            return web.json_response(
                {"error": f"{len(users)} automation(s) run this recipe "
                          f"({names}); delete or change them first",
                 "automations": [u["id"] for u in users]}, status=409)
    gone, err = await _library_call(hub.library.recipes.delete, rid)
    if err:
        return err
    await library_changed(hub, "recipes", gone.project)
    return web.json_response({"ok": True, "id": gone.id})


async def post_recipe_expand(hub, request) -> web.Response:
    """`{values}` -> `{text}`, through THE ONE EXPANSION (AU-3, AC-448):
    the panel's Run, a task's Start and the slash picker all ask here, so
    the page never fills a placeholder itself."""
    refused = _no_library(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    recipe, err = await _library_call(hub.library.recipes.get,
                                      request.match_info.get("rid", ""))
    if err:
        return err
    if recipe.problem:
        return web.json_response({"error": recipe.problem}, status=400)
    text, err = await _library_call(library_mod.expand, recipe.prompt,
                                    body.get("values") or {})
    return err or web.json_response({"text": text})


async def get_tasks(hub, request) -> web.Response:
    """`?project=<row id>` for one project's, else every task."""
    refused = _no_library(hub)
    if refused:
        return refused
    found, err = await _library_call(hub.library.tasks.tasks,
                                     request.query.get("project") or None)
    if err:
        return err
    now, tz = _word_clock(hub)
    return web.json_response({"tasks": [
        library_mod.served_task(t, now, tz) for t in found]})


async def post_task(hub, request) -> web.Response:
    """`{project, text}` adds an OWNER task; `{id, text?, done?}` edits one.
    A task from a model arrives by `POST /api/tasks/agent` and nowhere else,
    so this route never takes a `source`."""
    refused = _no_library(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    if "source" in body:
        return web.json_response(
            {"error": "source is not the page's to set; a task added here is "
                      "the owner's"}, status=400)
    tid = str(body.get("id") or "")
    if tid:
        task, err = await _library_call(hub.library.tasks.edit, tid,
                                        text=body.get("text"),
                                        done=body.get("done"))
    else:
        task, err = await _library_call(hub.library.tasks.add,
                                        body.get("project"), body.get("text"))
    if err:
        return err
    await library_changed(hub, "tasks", task["project"])
    return web.json_response(task)


async def delete_task(hub, request) -> web.Response:
    refused = _no_library(hub)
    if refused:
        return refused
    gone, err = await _library_call(hub.library.tasks.delete,
                                    request.match_info.get("tid", ""))
    if err:
        return err
    await library_changed(hub, "tasks", gone["project"])
    return web.json_response({"ok": True, "id": gone["id"]})


# --------------------------------------------------------------------------- #
# AUTOMATIONS (docs/automations.md section 5, phase 5). The same shape as the
# library routes: every store call off the loop, a refusal a 400 naming its
# rule, an unknown id a 404, and every change ONE `library` frame with
# `what: "automations"` scoped to the automation's project.
# --------------------------------------------------------------------------- #
def _no_automations(hub):
    if hub.automations is None:
        return web.json_response(
            {"error": "this daemon holds no automations"}, status=404)
    return None


async def get_automations(hub, request) -> web.Response:
    """Every automation, each with its recipe's name, what would stop it
    running (`problem`) and its newest history row (`last`)."""
    refused = _no_automations(hub)
    if refused:
        return refused
    found, err = await _library_call(hub.automations.listing,
                                     hub.scheduler.clock())
    return err or web.json_response({"automations": found})


async def post_automation(hub, request) -> web.Response:
    """`{id?, recipe, project?, values, schedule, enabled, graceMinutes,
    channels}`: create, or change the one with that id."""
    refused = _no_automations(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    saved, err = await _library_call(hub.automations.save, body,
                                     hub.scheduler.clock())
    if err:
        return err
    await library_changed(hub, "automations", saved["project"])
    public, err = await _library_call(hub.automations.public, saved,
                                      now=hub.scheduler.clock())
    return err or web.json_response(public)


async def post_automations_bulk(hub, request) -> web.Response:
    """`{ids, enabled}`: turn many on or off at once (AC-468)."""
    refused = _no_automations(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    saved, err = await _library_call(hub.automations.set_enabled,
                                     body.get("ids"), body.get("enabled"),
                                     hub.scheduler.clock())
    if err:
        return err
    for project in {a["project"] for a in saved}:
        await library_changed(hub, "automations", project)
    return web.json_response({"ok": True, "ids": [a["id"] for a in saved]})


async def delete_automation(hub, request) -> web.Response:
    """The automation and its history go together."""
    refused = _no_automations(hub)
    if refused:
        return refused
    gone, err = await _library_call(hub.automations.delete,
                                    request.match_info.get("aid", ""))
    if err:
        return err
    await library_changed(hub, "automations", gone["project"])
    return web.json_response({"ok": True, "id": gone["id"]})


async def post_automation_run(hub, request) -> web.Response:
    """Run now: a MANUAL run, allowed while disabled (AC-465). Answers the
    run row -- `pending` when it was queued, a skip naming why when not."""
    refused = _no_automations(hub)
    if refused:
        return refused
    run, err = await _library_call_async(
        hub.scheduler.run_now(request.match_info.get("aid", "")))
    return err or web.json_response(run)


async def post_heartbeat(hub, request) -> web.Response:
    """`{project, checklist?, minutes?, enabled?, channels?, silence?}`:
    the Library's heartbeat editor, ONE write (docs/proactive.md PR-8,
    PR-9, AC-481). The project's one heartbeat is created on its first
    save and updated by every later one; the checklist file is replaced
    atomically, and nothing is written when any field is refused (400
    naming the rule). Answers the row as the panel reads it."""
    refused = _no_automations(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    saved, err = await _library_call(hub.automations.save_heartbeat, body,
                                     hub.scheduler.clock())
    if err:
        return err
    await library_changed(hub, "automations", saved["project"])
    return web.json_response(saved)


async def get_prechecks(hub, request) -> web.Response:
    """`{folder, names}`: the precheck scripts an automation may NAME
    (docs/proactive.md PR-23, AC-493). Read-only by design: no route
    creates, edits or uploads a script; the owner places the file."""
    refused = _no_automations(hub)
    if refused:
        return refused
    found, err = await _library_call(hub.automations.precheck_names)
    return err or web.json_response(found)


async def _library_call_async(coro):
    """`_library_call` for a coroutine that does its own thread hops."""
    try:
        return await coro, None
    except library_mod.LibraryRefused as e:
        return None, web.json_response({"error": str(e)}, status=400)
    except KeyError as e:
        return None, web.json_response({"error": e.args[0] if e.args
                                        else str(e)}, status=404)


async def get_automation_runs(hub, request) -> web.Response:
    """One automation's history, newest first (AC-464)."""
    refused = _no_automations(hub)
    if refused:
        return refused
    runs, err = await _library_call(hub.automations.history,
                                    request.match_info.get("aid", ""),
                                    hub.scheduler.clock())
    return err or web.json_response({"runs": runs})


async def post_schedule_preview(hub, request) -> web.Response:
    """`{cron}` or `{preset, time, ...}` -> `{cron, preset, label, next}`,
    the next five runs in the machine's zone, or a 400 naming the field
    (AC-455). The editor's "Next" line and its "same as the cron" line are
    this answer; the page parses no schedule itself."""
    refused = _no_automations(hub)
    if refused:
        return refused
    body, err = await _library_body(request)
    if err:
        return err
    tz = hub.automations.tz

    def preview():
        now = hub.scheduler.clock()
        got = hub.automations._schedule(body, now)
        nxt = schedule_mod.preview(got["cron"], now, tz, 5)
        return {**got, "next": [automations_mod.iso(t, tz) for t in nxt],
                "nextWords": automations_mod.next_words(
                    nxt[0], got["label"], True, now, tz)}
    doc, err = await _library_call(preview)
    return err or web.json_response(doc)


async def delete_shortcut(hub, request) -> web.Response:
    """Forget one row. 404 on an id this daemon does not have.

    A list that can only grow is a trap whose only exit is hand-editing a file
    the owner was never told about (AC-134), which is `connections.py`'s
    recorded reason for having a remove at all.
    """
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    sid = request.match_info.get("sid", "")
    try:
        hub.shortcuts.remove(sid)
    except shortcuts_mod.ShortcutError as e:
        return web.json_response({"error": str(e)}, status=400)
    except KeyError as e:
        return web.json_response({"error": str(e)}, status=404)
    return web.json_response(hub.shortcuts.public())


async def post_project_stop(hub, request) -> web.Response:
    """Stop whichever conversation(s) hold a BUSY slot in one project, on an
    EXPLICIT confirmed gesture and on NO OTHER PATH
    (docs/filing-and-pacing.md 1.7, phase 5 steps 2-4; round 3/4's ruling:
    "Starting a new chat on this project will STOP the current running
    agent... if i decline, the whole gesture is cancelled").

    **THE ONE PLACE A ROUTE EVER CALLS `hub.stop_conversation`.** The
    ordinary turn path (`_Host.plan()` -> `_enforce_project_limit`) still
    raises `CapReached` on every BUSY collision exactly as it did before
    `0a9fd9a` wired the capability in at all -- `_enforce_project_limit`
    never calls `stop_for`, and nothing here changes that. So an ordinary
    message, however it is worded and however many times it collides, can
    never reach the stopping behaviour: it has to name a PROJECT ROW on
    THIS route, which on the page is the confirm dialog's own "yes" button
    and nothing else. A separate route is the whole of how the two are told
    apart -- there is no flag on the turn path that could accidentally turn
    a refusal into a stop.

    This route only ever STOPS; it never files or creates a chat. Declining
    the confirm means this is never called at all, so "no route is called;
    no chat is created; nothing is stopped" (round 4) holds by construction
    -- there is no branch here for a decline to take.

    Finds the busy chat ids the SAME way the confirm dialog's own read did
    (`_Host.busy_children_for_project`, D-18) rather than trusting
    whatever the page believed when it last asked -- a chat that finished
    on its own between that read and the click (TOCTOU) is not an error:
    `Hub.stop_conversation`'s `LookupError` for "nothing to stop" is
    swallowed here, because the goal state ("nothing busy left in this
    project") already holds either way.

    400 for a project id no row has -- the page listed these rows itself,
    so an id it does not recognise means the two disagree about what exists.
    Answers
    `{"stopped": [chat ids actually stopped]}`, possibly empty.
    """
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    pid = request.match_info.get("id", "")
    row = next((r for r in hub.shortcuts.all() if r.id == pid), None)
    if row is None:
        return web.json_response(
            {"error": f"no project with id {pid!r}"}, status=400)
    host = getattr(hub.session_brain(), "host", None)
    busy = host.busy_children_for_project(row.path) if host is not None else []
    stopped: list[str] = []
    for chat_id in busy:
        try:
            await hub.stop_conversation(chat_id)
        except LookupError:
            # Already gone by the time we got here -- the goal state holds
            # either way, so this is not a failure the caller needs to hear
            # about (see docstring's TOCTOU paragraph).
            continue
        stopped.append(chat_id)
    return web.json_response({"stopped": stopped})


async def get_project_files(hub, request, *, preview: bool) -> web.Response:
    """`GET /api/project/{id}/files` lists one level; `.../file` previews one
    file (`docs/file-explorer.md` 3.1, AC-303..AC-310). READ-ONLY, JSON only.

    Every rule is `daemon/files.py`'s; this is the wire. The ROOT is the row's
    and nothing on the request can name it, so a query key other than `path`
    is refused by name rather than ignored -- a caller that sent `root`
    believes it was honoured (AC-304). The work walks a disk and goes through
    `asyncio.to_thread` (D-25). Each preview is one log line, because it is a
    read that leaves no chat record behind (3.4).
    """
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    extra = sorted(set(request.query) - {"path"})
    if extra:
        return web.json_response(
            {"error": f"the only parameter is path; got {extra}"}, status=400)
    sid = request.match_info.get("id", "")
    rel = request.query.get("path", "")
    work = files_mod.preview if preview else files_mod.list_level
    try:
        answer = await asyncio.to_thread(work, hub.shortcuts, sid, rel)
    except files_mod.FileError as e:
        return web.json_response({"error": str(e)}, status=e.status)
    if preview:
        read = min(answer["size"], files_mod.MAX_PREVIEW)
        print(f"[butler] file preview {sid} {answer['path']} {read}",
              flush=True)
    return web.json_response(answer)


def guide_state(hub) -> dict:
    """What is on NOW, for `butler_help` (docs/butler-help.md BH-5): read at
    each call, so a flipped setting is in the next answer (AC-655). Names and
    on/off only: a channel is its name and kind, never its token (AC-651).
    Blocking (it lists folders): call it from a thread."""
    prefs = hub.settings

    def on(key):
        return "on" if prefs.get(key) else "off"

    def shown(key):
        # The label the settings page shows, which is what the guide's
        # text names ("Coding agent only", not "harness_only").
        value = prefs.get(key)
        spec = next(x for x in settings_mod.SCHEMA if x.key == key)
        return str((spec.labels or {}).get(value, value))
    state = {
        "brain_mode": f"brain mode {shown('brain_mode')}",
        "harness_tools": f"agent tools {shown('harness_tools')}",
        "speak": f"spoken replies {on('speak')}",
        "listening": f"microphone listening {on('stt_enabled')}",
        "ambient": (f"on, the last {prefs.get('ambient_seconds')} s"
                    if prefs.get("ambient_seconds") else "off"),
        "quiet": (f"on, {prefs.get('quiet_start')} to {prefs.get('quiet_end')}"
                  if prefs.get("quiet_hours") else "off"),
        "devices": f"phone link port {prefs.get('phone_link_port')}",
    }
    service = getattr(getattr(hub, "whatsapp", None), "state", "not built")
    state["whatsapp"] = (f"{on('whatsapp_enabled')} (service {service}); "
                         f"reading {on('whatsapp_read')}, sending "
                         f"{on('whatsapp_send')}")
    if hub.channels is not None:
        names = [f"{c.name} ({c.kind}{', not working' if c.problem else ''})"
                 for c in hub.channels.all()]
        state["channels"] = ", ".join(names) or "none set up"
    if hub.library is not None:
        recipes = [r.name for r in hub.library.recipes.recipes()]
        state["recipes"] = (f"{len(recipes)}: " + ", ".join(recipes[:20])
                            if recipes else "none yet")
    if hub.automations is not None:
        autos = hub.automations.automations()
        live = [str(a.get("name") or a.get("kind") or a["id"])
                for a in autos if a.get("enabled")]
        state["automations"] = (f"{len(autos)} ({len(live)} on: "
                                + ", ".join(live[:20]) + ")"
                                if autos else "none yet")
    return state


def serve_shown(path: Path, kind: str, media: str, name: str, *,
                download: bool, preview: bool) -> web.StreamResponse:
    """One file's bytes for the page (docs/rich-chat.md section 4): the
    artifact route and the explorer's raw route answer through this, so the
    headers that keep a hostile file inert are written once.

    `nosniff` and `CSP: sandbox` on everything; text kinds typed as plain
    text (AC-628); script-capable files and every `file` kind as an
    attachment (AC-629). `FileResponse` answers Range (206), so a video
    seeks and iOS plays it (AC-630). `preview` reads at most 256 KiB."""
    attach = download or not artifacts_mod.inline(kind, name)
    disposition = "attachment" if attach else "inline"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Content-Disposition": (
            f'{disposition}; filename="{attach_mod.safe_name(name)}"'),
        "Cache-Control": "private, max-age=3600",
    }
    if preview and kind in artifacts_mod.TEXT_KINDS:
        data, cut = artifacts_mod.preview_bytes(path)
        if cut:
            headers["X-Butler-Truncated"] = "1"
        headers["Content-Type"] = "text/plain; charset=utf-8"
        return web.Response(body=data, headers=headers)
    headers["Content-Type"] = artifacts_mod.served_type(kind, media)
    return web.FileResponse(path, headers=headers)


async def get_project_raw(hub, request) -> web.StreamResponse:
    """`GET /api/project/{id}/raw?path=` -- one file's BYTES for the
    explorer (docs/rich-chat.md RC-12), under `files.raw`'s rules, which are
    the preview's. Open to the tailnet like the preview (R2)."""
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    extra = sorted(set(request.query) - {"path", "download"})
    if extra:
        return web.json_response(
            {"error": f"the parameters are path and download; got {extra}"},
            status=400)
    sid = request.match_info.get("id", "")
    rel = request.query.get("path", "")
    try:
        path, kind, media = await asyncio.to_thread(
            files_mod.raw, hub.shortcuts, sid, rel)
    except files_mod.FileError as e:
        return web.json_response({"error": str(e)}, status=e.status)
    print(f"[butler] file raw {sid} {rel}", flush=True)
    return serve_shown(path, kind, media, path.name,
                       download=request.query.get("download") == "1",
                       preview=False)


async def post_project_upload(hub, request) -> web.Response:
    """`POST /api/project/{id}/upload?path=<dir>&name=<file>` -- a file
    dropped on the explorer (docs/rich-chat.md RC-13). The body IS the file
    (no multipart, `post_attach`'s shape), read in chunks and ABORTED past
    `files.MAX_UPLOAD` (413) rather than drained. A WRITE open to the
    tailnet (R2, owner 2026-10-08 item 3); it only ever adds a file."""
    if hub.shortcuts is None:
        return web.json_response(
            {"error": "this daemon holds no shortcuts store"}, status=404)
    extra = sorted(set(request.query) - {"path", "name"})
    if extra:
        return web.json_response(
            {"error": f"the parameters are path and name; got {extra}"},
            status=400)
    name = request.query.get("name", "")
    if not name:
        return web.json_response({"error": "name the file"}, status=400)
    data = bytearray()
    while True:
        chunk = await request.content.read(PCM_READ_CHUNK)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > files_mod.MAX_UPLOAD:
            return web.json_response(
                {"error": f"the file is larger than {files_mod.MAX_UPLOAD} "
                          f"bytes"}, status=413)
    sid = request.match_info.get("id", "")
    try:
        answer = await asyncio.to_thread(
            files_mod.save_upload, hub.shortcuts, sid,
            request.query.get("path", ""), name, bytes(data))
    except files_mod.FileError as e:
        return web.json_response({"error": str(e)}, status=e.status)
    print(f"[butler] file upload {sid} {answer['path']} {answer['size']}",
          flush=True)
    return web.json_response(answer)


# What `desk/browse.py` gets before the daemon gives up on it. Generous: the
# budget is a PERSON deciding, and a dialog the owner walked away from must not
# leave a process behind (see `post_browse`).
BROWSE_TIMEOUT_S = 300.0
BROWSE_SCRIPT = REPO / "desk" / "browse.py"


def _browse_blocking(start: str) -> tuple[int, str, str]:
    """Run the chooser as a child and answer `(returncode, stdout, stderr)`.

    A CHILD PROCESS, which is the whole of why this is allowed to exist at all
    (`docs/shortcuts.md` D6). The picker the owner overruled ran on the desk
    agent's single decision thread, so it stopped the settings poll and refused
    every dictation until somebody dismissed it. This blocks nothing that
    serves a hotkey or a turn: it is a separate process, and the caller waits
    for it on a worker thread.

    Assigned to the Job Object like every other child (AC-36, D-24) -- the
    deliberate exception is `spawn_terminal`, which must OUTLIVE the daemon. A
    dialog must not: an orphaned modal nobody can trace to Butler is exactly
    the leak `childproc.py` exists for.
    """
    from daemon import childproc

    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(BROWSE_SCRIPT), "--start", start],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    childproc.assign(proc)
    try:
        out, err = proc.communicate(timeout=BROWSE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return 1, "", (f"nobody answered the folder chooser within "
                       f"{BROWSE_TIMEOUT_S:.0f} s, so it was closed")
    return proc.returncode, (out or "").strip(), (err or "").strip()


async def post_browse(hub, request) -> web.Response:
    """Ask the owner for a directory. 200 with a path, or 204 on cancel.

    204 rather than 200-with-an-empty-string, because "the owner pressed
    Cancel" is not an answer the page should draw anything for and an empty
    path is exactly the shape a bug produces. 502 when the dialog itself could
    not open -- a python with no tkinter, no display, a session with no input
    desktop -- carrying the reason, never a silent cancel (AC-7b).

    Off the event loop, because it waits for a person (D-25).
    """
    try:
        body = await request.json()
    except Exception:                                     # noqa: BLE001
        body = {}
    start = str((body or {}).get("start") or "").strip()
    if not start and hub.settings is not None:
        start = terminal_cwd(hub.settings)
    code, chosen, why = await asyncio.to_thread(_browse_blocking, start)
    if code != 0:
        print(f"[butler] the folder chooser failed: {why!r}", flush=True)
        return web.json_response(
            {"error": why or "the folder chooser would not open"}, status=502)
    if not chosen:
        return web.Response(status=204)
    return web.json_response({"path": chosen})


def wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw PCM from the `Tts` port in a WAV container.

    The port yields HEADERLESS 24 kHz mono int16 PCM (`ports.SAMPLE_RATE_OUT`,
    one channel, two bytes per sample), because a satellite is told the format
    once at `hello` and every chunk after that is bare samples. A browser
    given those bytes has nothing to decode, so the audition has to declare
    the format itself -- and the three numbers below ARE the declaration.

    Written through `wave` rather than by hand: `parse_frame_header` in the
    kokoro adapter is this project's cautionary tale about a sample rate that
    quietly became wrong, and the symptom of getting one of these three
    numbers wrong is a voice that sounds slightly off with nothing reporting
    it.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)                       # mono
        out.setsampwidth(2)                       # int16
        out.setframerate(ports.SAMPLE_RATE_OUT)   # 24 kHz
        out.writeframes(pcm)
    return buf.getvalue()


async def tts_preview(hub, voice: str) -> web.Response:
    """One fixed line in a NAMED voice, as WAV bytes (AC-109).

    A plain HTTP route and never a socket frame. `daemon/AGENTS.md`: nothing
    is spoken outside a turn and nothing is sent between turns either, because
    the page drops frames for a turn it does not own (D-6) -- so a preview
    through the socket would need an exception carved into that rule, where
    this needs none. The page plays the bytes itself.

    It changes NO setting, and that is the criterion rather than a detail:
    auditioning a voice must not become choosing it.

    Two failures kept apart, the same split `get_models` makes. A voice this
    engine does not have is the CALLER's mistake and answers 400 naming it; an
    engine that cannot say the line is the WORLD's and answers 502 carrying
    the reason. Neither is ever a 200 with an empty body -- a 44-byte WAV
    header plays as silence, which reads as "this voice is inaudible" rather
    than "nobody could be asked" (AC-108's rule, one layer down).

    Module-level rather than a closure in `make_app` so it can be driven
    against an engine that has been MADE to fail; a route only reachable over
    HTTP can only be tested against engines that work.
    """
    voice = (voice or "").strip()
    if not voice:
        return web.json_response(
            {"error": "name a voice: /api/tts/preview?voice=<name>"},
            status=400)
    try:
        known = _nonempty(
            ports.voice_ids(hub.tts.info),
            "the speech engine has not reported its voices yet, so there is "
            "nothing to audition; it is still warming up, or the sidecar is "
            "not answering")
    except LocalListUnavailable as e:
        return web.json_response({"error": str(e), "voice": voice}, status=502)
    if voice not in known:
        return web.json_response(
            {"error": f"no voice named {voice!r}; this engine speaks {known}",
             "voice": voice}, status=400)
    try:
        # `speed` so the audition is what the owner will actually hear
        # (AC-107): a voice tried at 1.0 and then spoken at 0.8 is not the
        # thing that was auditioned. Read per request for the same reason the
        # hub reads it per turn.
        chunks = [chunk async for chunk in hub.tts.synthesize(
            PREVIEW_TEXT, voice, speed=hub._speed(), handle=PREVIEW_HANDLE)]
    except Exception as e:                            # noqa: BLE001
        return web.json_response(
            {"error": f"the speech engine could not say the preview line in "
                      f"{voice!r}: {e}", "voice": voice}, status=502)
    pcm = b"".join(chunks)
    if not pcm:
        return web.json_response(
            {"error": f"the speech engine produced no audio for {voice!r}",
             "voice": voice}, status=502)
    return web.Response(body=wav_bytes(pcm), content_type="audio/wav")


# The longest text one replay may carry. DERIVED and not chosen, the same
# idiom as `AMBIENT_MAX_BYTES` and `DICTATE_MAX_BYTES` and for the same reason
# (D-12): a second number written down here would drift away from the one the
# daemon already enforces on a single utterance. `hub.TYPED_MAX_CHARS` IS that
# number -- the most the owner may type in one message -- so a reply Butler
# will not read again is a reply longer than anything it can be asked.
SAY_MAX_CHARS = hub_mod.TYPED_MAX_CHARS
# Distinct from `PREVIEW_HANDLE` for `PREVIEW_HANDLE`'s own reason: a handle is
# what `Tts.abort` matches on, so a replay sharing the preview's handle would
# be cancelled by an audition started in the settings surface, and vice versa.
SAY_HANDLE = ("say",)


async def tts_say(hub, body: dict) -> web.Response:
    """Say a message AGAIN, as WAV bytes (AC-183).

    `POST /api/tts/say` with `{"text": "..."}`. The page keeps no audio --
    `playChunk` posts samples into a worklet and never retains them -- so
    "play this reply again" is RE-SYNTHESIS rather than playback of a
    recording. That is stated on the criterion rather than hidden: a voice or
    a speed changed since the reply was first spoken gives a different
    rendering of the same words.

    It is `tts_preview`'s shape with the text supplied instead of the voice,
    and it keeps the three properties that route is written around. It changes
    NO setting. Its handle can never collide with an int turn id, so it cannot
    abort a live reply. And an engine that produces nothing is a 502 rather
    than an empty 200, because a 44-byte WAV header plays as silence, which
    reads as "this message has no sound in it".

    It works while `speak` is OFF, and that is the point of it: muting removes
    the audio from every reply (AC-110), and this is the one control that asks
    for one back. Nothing here reads that setting.

    **IT DETECTS ON THE TEXT IT IS ABOUT TO SPEAK** (AC-214). There is no turn
    and no session here -- a replay is a button on a message that may be an
    hour old -- so the conversation's current language is the wrong answer and
    the message's own language is the right one. Without this, replaying a
    Portuguese answer after the conversation moved back to English would read
    Portuguese words with English sounds, which is the failure `docs/language.md`
    was written to remove, arriving through the one control that exists to
    hear a reply again.
    """
    text = (body or {}).get("text")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return web.json_response(
            {"error": "name the text to say: {\"text\": \"...\"}"}, status=400)
    if len(text) > SAY_MAX_CHARS:
        # Refused by length with the number named, never truncated: half a
        # reply read aloud and then stopping sounds exactly like the engine
        # failing mid-sentence, which is the one failure this route must not
        # imitate.
        return web.json_response(
            {"error": f"that message is {len(text)} characters and the "
                      f"ceiling for one replay is {SAY_MAX_CHARS}"},
            status=413)
    # The same resolver a turn uses, so a replay and the reply it is replaying
    # cannot pick different voices (AC-212). `detect_language` abstains on a
    # short message and `_default_language` answers for it, which is the
    # conversation-level fallback a route with no session can honestly give.
    language = dialogue.detect_language(
        text, ports.voice_languages(hub.tts.info)) or hub._default_language()
    try:
        chunks = [chunk async for chunk in hub.tts.synthesize(
            text, hub._voice_for(language), speed=hub._speed(),
            handle=SAY_HANDLE)]
    except Exception as e:                                # noqa: BLE001
        return web.json_response(
            {"error": f"the speech engine could not say that message: {e}"},
            status=502)
    pcm = b"".join(chunks)
    if not pcm:
        return web.json_response(
            {"error": "the speech engine produced no audio for that message"},
            status=502)
    return web.Response(body=wav_bytes(pcm), content_type="audio/wav")

# What `brain_mode` may name, and the only thing it may name (4.4, 7.5,
# AC-66). Three REAL modes since 2026-08-19: the router plus a conversation
# model, the router plus a coding agent, and a coding agent with no router at
# all. The fake is NOT on this list: it is a test double, selected by config
# or by BUTLER_BRAIN like every other port's fake, and a permission-relevant
# switch should offer the owner real answers rather than a stub among them.
# The KEYS are the stored values and the labels the owner reads are the
# schema's (`Setting.labels`), so a control can be renamed without an existing
# settings.json booting into a mode nobody chose.
BRAIN_MODES = {"endpoint": "endpoint", "harness": "harness",
               "harness_only": "harness_only"}

def chat_harness(settings, chat: dict | None) -> str:
    """Which coding agent a CONVERSATION runs on (AC-771, CT-8).

    The one it was BORN with: the record's `harness`, which
    `ChatStore.session_ref` writes at the conversation's first spawn from
    this same answer -- so a new conversation takes the `harness` setting and
    keeps it across restarts and later setting changes. A record with none
    (never spawned yet, or a legacy record from before per-chat agents, which
    were all pi chats under a pi setting) takes the setting.
    """
    recorded = str((chat or {}).get("harness") or "").strip()
    return recorded or str(settings.get("harness", "") or "").strip()


# The owner-facing name of each coding agent the registry holds; an unknown
# name is shown as-is (CT-8). The page draws it and names no setting (AC-41).
AGENT_LABELS = {"pi": "pi", "claude": "Claude Code", "opencode": "opencode"}


def allowed_agents(settings) -> set[str]:
    """The agents a chat may start on (docs/agent-and-terminal-settings.md
    SW-1, SW-4): the `chat_agents` setting plus the default agent, so the
    default is always allowed."""
    return ({str(n) for n in (settings.get("chat_agents") or [])}
            | {str(settings.get("harness", "") or "").strip()})


def offered_agents(settings) -> list[dict]:
    """The agents the page offers when a chat starts
    (docs/chat-agent-picker.md AP-3): the allowed ones the daemon has an
    owner-facing label for, in `AGENT_LABELS`' order. `fake` is registered
    (AC-7a) and never offered."""
    allowed = allowed_agents(settings)
    return [{"name": n, "label": AGENT_LABELS[n]}
            for n in AGENT_LABELS if n in ports.HARNESSES and n in allowed]


def _with_choice(hub, brain, doc: dict) -> dict:
    """The open conversation's agent and model choice (AP-3, CM-8): what to
    offer, the defaults, and whether this chat's agent can still change."""
    default = str(hub.settings.get("harness", "") or "").strip()
    doc["agents"] = offered_agents(hub.settings)
    doc["agent_default"] = default
    doc["agent_fixed"] = brain.is_agent_fixed(doc.get("id") or "")
    # docs/chat-model.md CM-8: the chat's own model and what "Default" means.
    doc["model"] = str(doc.get("model") or "")
    doc["model_default"] = default_model(hub.settings, doc.get("agent") or "")
    return doc


def _refused_agent(settings, agent: str) -> web.Response | None:
    """400 for an agent a chat may not start on: one this daemon does not
    have, or one switched off in Settings (SW-3). None when it may."""
    if agent not in ports.HARNESSES:
        return web.json_response(
            {"error": f"{agent!r} is not a coding agent this daemon has; "
                      f"known: {sorted(ports.HARNESSES)}"}, status=400)
    if agent not in allowed_agents(settings):
        return web.json_response(
            {"error": f"{agent!r} is switched off for chats; turn it on in "
                      f"Settings, Harness, Chat agents (chat_agents)"},
            status=400)
    return None


def default_model(settings, agent: str) -> str:
    """What a chat with no model of its own runs on, when Butler can name it
    (CM-8): the `harness_model` setting for the setting's agent; "" for any
    other agent, whose own default Butler cannot name."""
    if agent == str(settings.get("harness", "") or "").strip():
        return str(settings.get("harness_model", "") or "").strip()
    return ""


RECENT_MODELS = 5            # CM-7, CM-D1: per agent, daemon-wide


def _recent_path() -> Path:
    return paths_mod.resolve().records / "model_recent.json"


def recent_models() -> dict:
    path = _recent_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def remember_model(agent: str, model: str) -> None:
    """Put `model` first in `agent`'s recents (CM-D1: one list per agent for
    the whole daemon, every device)."""
    table = recent_models()
    row = [m for m in table.get(agent, []) if m != model]
    table[agent] = ([model] + row)[:RECENT_MODELS]
    path = _recent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _with_agent(hub, rows: list[dict]) -> list[dict]:
    """Add `agent` (the conversation's coding agent, `chat_harness`) and
    `agent_label` to each chat row or open-chat document (CT-8, AC-688).

    Its own step beside `_filed`, for `_with_status`'s reason: the rows that
    `_filed` also annotates (delete, rewind) have exact-shape tests. A chat
    that has not spawned yet has no recorded `harness`, so it shows the agent
    the setting would give it.
    """
    settings = getattr(hub, "settings", None) or {}
    for row in rows:
        name = chat_harness(settings, row)
        row["agent"] = name
        row["agent_label"] = AGENT_LABELS.get(name, name)
    return rows


def harness_factory(settings):
    """The conversation's agent, resolved into ONE adapter, on demand (AC-60).

    The second callable a harness brain is handed, and the same rule as
    `harness_spec_resolver`: a preference reaches an adapter through a
    resolver, never by handing over the settings store (4.10). Called only
    when a child is actually being SPAWNED, which since AC-59 is the first
    turn of a conversation and any turn whose spec no longer matches the
    running one -- which is why the
    `harness` setting is no longer restart-flagged: switching coding agents
    takes effect on the next thing said.

    BUILDING an adapter starts no process; `Harness.start` does. That is what
    makes this cheap enough to call per turn and safe to call for a
    capability read between turns.

    A name that is not in the registry raises, loudly and naming the
    alternatives, exactly as `ports.build` does at boot. The cost of moving
    that check out of boot is stated rather than hidden: a hand-edited
    settings file naming a harness this daemon does not have is now a spoken
    failure on the next question instead of a daemon that will not start. The
    settings page cannot produce one -- `harness` renders from
    `choices_from="harnesses"`, which is the registry itself.
    """
    def build(chat: dict | None = None):
        # The CONVERSATION's agent (`chat_harness`, AC-771); None is a
        # capability read with no conversation, which gets the setting's.
        return ports.build(ports.HARNESSES, chat_harness(settings, chat))

    return build


# The read-only allow-list the `harness_tools` setting maps `read_only` to. Its
# members are pi's own read tools -- `pi --tools read,grep,find,ls` is pi's
# documented read-only mode. `full` maps to () -- no restriction, pi's default
# set. This is the minimal slice of the plumbing 653a44d deleted, re-added and
# driven by a plain setting rather than the retired spoken grant (AC-29).
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")

# The library tools pi's extension sends to `POST /api/library/agent`
# (`task_add` has its own route). Every other name is refused there (AC-665).
LIBRARY_AGENT_TOOLS = (library_mod.REMINDER_ADD, library_mod.SCHEDULE_PROPOSE,
                       library_mod.RECIPE_FIND, library_mod.RECIPE_PROPOSE)


def harness_spec_resolver(settings, chats, projects=None, append_for=None,
                          memory_url: str | None = None):
    """Preferences plus ONE NAMED conversation, resolved into ONE spawn.

    The `TargetResolver` idea one seam out (docs/architecture-seams.md 3.4).
    A brain adapter that drives a coding agent needs configuration no
    connection can serve -- `harness_model` names a model in the HARNESS's own
    registry and `harness_cwd` is not a model at all -- but that is a reason
    for a SECOND resolver, not for handing an adapter the settings store. The
    adapter calls this per turn and gets back the frozen record of what the
    child should be; it reads no setting and knows no path, and it decides
    nothing about what the child may DO -- a harness runs at full capability
    (AC-29, retired 2026-08-18).

    `HarnessSpec` is built by the daemon and never by the adapter
    (docs/harness-mode.md section 5), and this is where that happens.

    The session reference comes from the CHAT record rather than a fixed name
    (10.2): a spoken "new conversation" mints a new chat file, so this
    resolver answers with a different reference from the very next spawn --
    which is the whole of the rotation now that a child lives for one message.
    The reference is named per harness, and the harness is the
    CONVERSATION's (`chat_harness`, AC-771), the same answer
    `harness_factory` builds from; a pi session file handed to a claude
    child would resume a conversation into a reader that cannot read it.

    `append_for` is `persona.Persona.append_file` (docs/identity-memory.md
    4.2, AC-373): the path of the rendered identity file, or None. A callable
    for the reason `projects` is one, and read per spawn check, so an edited
    identity is a different spec on the very next turn (AC-374). None -- a
    resolver built without a persona, as the suite's direct constructions
    are -- appends nothing, which is also what a blank identity appends.

    `memory_url` is this daemon's LOOPBACK address, or None
    (docs/identity-memory.md 12.6, AC-440). Set, every spec carries it -- the
    adapter turns it into the memory extension -- and read_only's allow-list
    gains the two memory tools, because pi's `--tools` gates extension tools
    too and a tool the rules describe but the allow-list hides is a tool the
    agent is told to call and cannot. `full` has no allow-list to extend.
    """
    def resolve(cwd: str | None = None,
                chat: dict | None = None) -> ports.HarnessSpec:
        """What the child SHOULD be, from the settings, right now.

        `chat` is WHICH CONVERSATION this child is for, and it is what the
        session reference is minted into (`docs/parallel-agents.md` phase 4).
        Every SPAWN path names it: `_Host.desired()` passes the conversation it
        holds, so a second conversation's child can never take its session file
        out of the first one's record. None means the conversation the store is
        currently on, which is what the two GATE-ONLY callers use -- a birth
        (`_Host.new_conversation`) and `_session_cwd` build a spec purely to
        find out whether AC-31 allows a directory and throw the answer away.

        A blank working directory DEFAULTS to `<repo>/TMP` rather than raising
        (AC-31, amended 2026-08-18 by the owner): a child can always start in a
        harmless scratch dir, so a blank `harness_cwd` no longer means no child
        at all. What did NOT change is the refusal to GUESS a named project --
        the default is the scratch dir, never a spoken name, and an unknown
        spoken project is still refused aloud on the `cwd` path below.

        `cwd` is the conversation's recorded project (R-B), and it is
        RE-CHECKED here against the configured list rather than trusted. The daemon matched
        the name; this is the second gate, and it is the one on the path
        every spawn takes -- so an adapter cannot put a child anywhere the
        owner did not configure, however it came to hold the string (AC-31).
        """
        # The list is the shortcuts pane's ROWS since 2026-09-11 (the owner's
        # backlog item 4), where it used to be the `projects` setting. A
        # CALLABLE rather than the store itself, so this resolver still knows
        # nothing about shortcuts and a test can hand it a list; and called per
        # spawn rather than captured, because the owner adds a row and expects
        # the very next thing they say to reach it.
        allowed = tuple(projects() if callable(projects) else (projects or ()))
        # COMPARED AS DIRECTORIES AND NEVER AS STRINGS (review, 2026-09-11).
        # `cwd not in allowed` was a THIRD equality rule for one directory and
        # the strictest of the three: `ShortcutStore.row_for` filed a
        # conversation under a row spelled `E:\X\` or `e:\x` and this refused to
        # start a child in it, and `brain_harness._session_cwd` then swallowed
        # the refusal and spawned in `harness_cwd` while the rail went on saying
        # the conversation lived in the row. One rule answers for one directory
        # now: `shortcuts.same_dir`, the same one the listing and the migration
        # use. It is NOT a softening -- a directory matching no row is still
        # refused, which is the whole of AC-31.
        wanted = shortcuts_mod.same_dir(cwd) if cwd is not None else None
        if wanted is not None and not any(
                shortcuts_mod.same_dir(p) == wanted for p in allowed):
            raise ValueError(
                f"{cwd!r} is not one of the configured projects, so no child "
                f"may be started there. A tool-enabled agent in a repository "
                f"nobody configured is what AC-31 exists to prevent; the "
                f"list is the projects pane on the main page.")
        cwd = cwd or str(settings.get("harness_cwd", "") or "").strip()
        if not cwd:
            # Blank means the TMP scratch dir, not a raise and never a NAMED
            # guess (AC-31, amended 2026-08-18). Created here so the very first
            # message in a fresh clone has somewhere harmless to run.
            scratch = REPO / "TMP"
            scratch.mkdir(parents=True, exist_ok=True)
            cwd = str(scratch)
        # Blank means the harness's own default, and `HarnessSpec` insists
        # that be spelled None -- an empty string eventually reaches a child
        # as a model literally named "".
        record = chat if chat is not None else chats.current()
        harness = chat_harness(settings, record)
        # `harness_model` names a model in the SETTING's harness's registry
        # (its picker lists that harness's models), so it reaches only a
        # conversation running that harness; a conversation born on another
        # agent gets that agent's own default (AC-771).
        # docs/chat-model.md CM-2: the conversation's OWN model first.
        model = (str(record.get("model") or "").strip()
                 or default_model(settings, harness))
        # The `harness_tools` knob (not the retired grant, AC-29): read_only is
        # a read allow-list the adapter turns into `--tools`, full is no
        # restriction. A plain setting read per message, so it reaches the very
        # next child.
        tools = (READ_ONLY_TOOLS
                 if str(settings.get("harness_tools", "")) == "read_only"
                 else ())
        # The WhatsApp tools the settings allow NOW (docs/messaging.md MS-8,
        # MS-18b): they ride on the extension's URL, so switching one is a
        # different SPEC and AC-60 replaces a held child on its next turn --
        # the extension deactivates every WhatsApp tool the URL does not
        # name (AC-508, AC-562).
        whatsapp = whatsapp_mod.offered_names(settings) if memory_url else ()
        url = (f"{memory_url}?whatsapp={','.join(whatsapp)}" if whatsapp
               else memory_url)
        if tools and memory_url:
            # pi's `--tools` gates extension tools too, so every tool the
            # rules describe is named here (docs/automations.md AU-9).
            tools = (tools + memory_mod.TOOL_NAMES + library_mod.TOOL_NAMES
                     + artifacts_mod.TOOL_NAMES + guide_mod.TOOL_NAMES
                     + whatsapp)
        if automations_mod.from_event(record):
            # O-2 [DECIDED] (docs/proactive.md 12, PR-28): a chat born from a
            # trigger's payload -- an email, a webhook body: a stranger's
            # prompt -- gets the READ tools whatever `harness_tools` says, and
            # of the extension's only `memory_search`: no memory write, no
            # task or reminder, and no WhatsApp tool on the URL. A different
            # spec, so AC-60 never reuses a fuller child for it.
            tools = READ_ONLY_TOOLS + (("memory_search",) if memory_url
                                       else ())
            url = memory_url
        return ports.HarnessSpec(
            model=model or None, cwd=cwd, tools=tools,
            session_file=chats.session_ref(record, harness),
            append_prompt=append_for() if append_for is not None else None,
            memory_url=url or None,
            chat_id=str(record.get("id") or "") or None
            if memory_url else None)

    return resolve


WEB_DIR = REPO / "web"
CONFIG = REPO / "config" / "models.json"
# How much of a conversation `GET /api/session/current` serves (AC-35). The
# newest messages, because the page renders a fixed number of recent TURNS
# (`HISTORY_MAX`, eight) and drops the rest on the floor -- an unbounded
# answer would ship a thousand-turn transcript to draw eight of them. Several
# times eight turns' worth, because a turn is two messages and the owner may
# have scrolled.
MESSAGES_SERVED = 100
# How many conversations `GET /api/chats` lists (AC-164). Bounded for
# MESSAGES_SERVED's reason one level up: a daemon that has run for a year
# holds a chat file per conversation and the sidebar draws the recent ones,
# so an unbounded answer would read every record on disk to fill a list
# nobody scrolls to the end of. The newest end is what is served, so the
# bound cuts the conversations the owner is least likely to want.
CHATS_LISTED = 200
# One wording for every route that needs a conversation the daemon does not
# have. Endpoint mode holds no chat store at all, which is a different fact
# from a daemon whose record is empty.
NO_CONVERSATION = ("this daemon is not running a harness, so it holds no "
                   "conversation")


def _dir_name(path: str) -> str:
    """The last component of a directory path, on either separator.

    `Path(...).name` is not usable here: a POSIX interpreter reading a Windows
    path treats the whole string as one name, and this runs against records
    written on a machine that is not necessarily the one reading them. It is
    also the exact rule `web/app.js` applies to `project` today, which is what
    makes the label the daemon now sends a REPLACEMENT for the page's own
    guess rather than a second one beside it.
    """
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path


def _row_for(hub, path: str):
    """The project ROW a conversation's directory belongs to, or None.

    One hop, so every caller here derives the row the same way
    (`ShortcutStore.row_for`, which is where the argument lives). A daemon
    with no shortcuts store -- every Hub in a test that does not know the pane
    exists -- has no rows, and "no row" is the right answer rather than a
    crash.
    """
    return (hub.shortcuts.row_for(path)
            if hub.shortcuts is not None and path else None)


def _filed(hub, rows: list[dict]) -> list[dict]:
    """Say which project row each listed conversation is in, IN WORDS.

    `project` keeps its meaning -- the absolute path the birth wrote -- so
    nothing reading it today breaks. What is added is the pair a page can draw
    and scope by without knowing anything about directories: `projectId`, the
    row it is filed under, and `projectLabel`, what that row is called.

    Both are DERIVED per request (AC-218). Nothing is stored twice, so
    re-spelling a row's path cannot orphan a conversation -- see
    `ShortcutStore.row_for`. A conversation whose row was REMOVED keeps its
    path, matches no row, and gets an empty id with the directory's own name as
    the label: it is still listed and still openable (AC-225), it is simply in
    no scope but the unscoped one, because the directory it names is no longer
    a project.

    Mutates in place and answers the same list, because the rows were built
    for this response and there is no second reader to surprise.

    **DELIBERATELY DOES NOT CARRY `status`.** This same function also
    annotates `GET /api/session/current`, `GET /api/chats/{id}`,
    `DELETE /api/chats/{id}` and the rewind answer, and each of those has its
    OWN "exact shape" test enumerating every key it may carry (10.2's
    discipline) -- growing this function would have silently widened all four
    at once for a criterion (AC-263) that names only the LISTING route. See
    `_with_status`, applied ONLY in `get_chats`.
    """
    for row in rows:
        path = str(row.get("project") or "")
        found = _row_for(hub, path)
        row["projectId"] = found.id if found is not None else ""
        if found is not None:
            row["projectLabel"] = found.label or _dir_name(found.path)
        else:
            row["projectLabel"] = _dir_name(path) if path else ""
    return rows


def _with_status(hub, rows: list[dict], unseen=frozenset()) -> list[dict]:
    """`GET /api/chats`'s own extra: what each row is DOING right now
    (AC-263, `docs/triage-list.md` phase 1).

    A SEPARATE step from `_filed`, applied after it, and only in `get_chats`
    -- see `_filed`'s own docstring for why growing that shared function
    would have reopened three OTHER routes' locked shapes for a criterion
    that names only this one.

    `status` and `elapsed_ms` are `hub.status_for`/`hub._elapsed_ms`, the SAME
    derivation `GET /api/chats/{cid}/status` already answers for one
    conversation -- read here rather than re-implemented, so the two can
    never disagree about what "running" means. Both are in-memory reads
    (never a disk hit), so looping them over every row this route already
    built costs no new I/O: a page painting N rows must not turn into N
    requests by polling the single-conversation route once per visible row.

    **`action` JOINED THE SAME WAY** (owner's ruling 2026-09-16,
    `docs/triage-list.md` phase 2 continued): `hub.action_for` reads
    `_Host.last_action`, the one thing `ToolStart` writes and a turn's own
    ending clears -- read here rather than a second registry (D-18), and
    None for a conversation with nothing to report, same shape as the other
    two fields.

    **`unread` JOINED 2026-09-26** (docs/automations.md AC-469): true for a
    scheduled run's chat nobody has opened on any device yet. `unseen` is
    `AutomationStore.unseen_chats()`, read off the loop by the route.

    Mutates in place and answers the same list, for `_filed`'s own reason.
    """
    for row in rows:
        row["status"] = hub.status_for(row["id"])
        row["elapsed_ms"] = hub._elapsed_ms(row["id"])
        row["action"] = hub.action_for(row["id"])
        row["unread"] = row["id"] in unseen
    return rows


def _chat_scope(hub, raw):
    """Turn `?project=` into the predicate `ChatStore` filters on.

    THREE VALUES AND ALL THREE ARE REAL. Absent (`None`) is every
    conversation, which is what the route answered before this existed. The
    EMPTY string is the conversations with no project at all -- a real group
    that never empties on today's corpus, and the one `chats.py` refuses to
    attribute to anything (D1/A). Anything else is a row ID.

    An id no row has raises `KeyError` naming it, which the route answers as a
    400 -- the page LISTED these rows, so an id it does not recognise means
    the two disagree about what exists. The predicate itself is
    `Hub.project_filter`, the one "go to project X" also asks (D-18). There is deliberately no "unknown scope shows everything": a
    filter that silently stops filtering is the house's fallback rule broken
    on the one surface whose whole job is to leave things out.
    """
    if raw is None:
        return None
    wanted = str(raw).strip()
    if not wanted:
        return lambda project: not project
    return hub.project_filter(wanted)      # KeyError names the id
# Where Butler's records live is `paths.resolve()`'s answer and nobody else's
# (docs/data-root.md): `BUTLER_DATA` / `--data`, `BUTLER_STATE` for a test or a
# scratch daemon, else the legacy `state/`. Resolved at CALL time, not import,
# so a test's `monkeypatch.setenv("BUTLER_STATE", ...)` is the whole override --
# the suite otherwise shares the owner's live records (test/AGENTS.md), and a
# connections test writes a file carrying a key POINTER.
# Same reasoning, sharper: the real registry holds the owner's plaintext keys
# for three cloud providers, so a test must be able to aim at a fixture one.
PI_REGISTRY = Path(os.environ.get("BUTLER_PI_REGISTRY")
                   or conn_mod.DEFAULT_PI_REGISTRY)


def load_config() -> dict:
    """The config at `BUTLER_CONFIG`, else `config/models.json`, else fakes.

    The override is not a convenience: the test suite otherwise reads the
    owner's real `config/models.json` (transcribe.cpp, kokoro, a GPU backend)
    and every daemon fixture boots real engines and fails -- exactly why
    `BUTLER_STATE` exists (test/AGENTS.md). conftest forces it somewhere
    hermetic, so a real config on disk never reaches a test, and a fresh clone
    with neither still gets the default.

    That default names a fake ASR and a fake TTS and NO brain at all: since
    7.5 the brain is the owner's `brain_mode` setting, and a name here would
    outrank it on every machine without a config file (AC-7b). It is NOT a
    fallback for a broken config: an unreadable file raises.
    """
    path = Path(os.environ.get("BUTLER_CONFIG") or CONFIG)
    if not path.exists():
        return {"adapters": {"asr": "fake", "tts": "fake"},
                "asr": {"model": "parakeet-unified-en-0.6b"}}
    return json.loads(path.read_text(encoding="utf-8"))


def build_hub(cfg: dict) -> hub_mod.Hub:
    names = dict(cfg.get("adapters") or {})

    # The test stub is a real adapter swap, not a special case in the daemon:
    # BUTLER_TEST_STUB_STT just selects the fake by name. That is what makes
    # it the Asr port's second implementation rather than a debug branch
    # (AC-7a).
    if os.environ.get("BUTLER_TEST_STUB_STT") == "1":
        names["asr"] = "fake"
        names.setdefault("tts", "fake")
        names.setdefault("brain", "fake")

    # Per-port override, so a test can run the REAL recognizer while keeping
    # the fake speech and brain. AC-1a needs this: whether the pre-roll bytes
    # reached the ASR is only observable if something actually transcribes
    # them, and a scripted fake would pass the test without the audio.
    for port_name, env in (("asr", "BUTLER_ASR"), ("tts", "BUTLER_TTS"),
                           ("brain", "BUTLER_BRAIN")):
        chosen = os.environ.get(env)
        if chosen:
            names[port_name] = chosen

    # `asr` holds what crosses the PORT; `asr_engine` holds one adapter's own
    # options -- compute backend, chunking preset, quant. Split in the config
    # file rather than guessed here, so the seam is visible where someone
    # edits it, and an option the adapter does not accept is a loud TypeError
    # at boot instead of a setting that silently does nothing (AC-41).
    where = paths_mod.resolve()
    records = where.records
    # ONE store for what was shown (docs/rich-chat.md RC-3): the
    # conversation store points into it, the hub snapshots into it.
    shown = artifacts_mod.ArtifactStore(where.artifacts)
    # SOUL.md and USER.md (docs/identity-memory.md 11, AC-420, AC-427):
    # created from their templates only when absent, THEN the retired
    # `identity` setting is moved into any that still say nothing -- both
    # before the store is built, so the store never sees the retired key and
    # its file-backed settings read the files the migration just wrote.
    persona_files = persona_mod.files_for(where.persona)
    created = [f.path.name for f in persona_files.values() if f.ensure()]
    if created:
        print(f"[butler] created {created} from their templates in "
              f"{where.persona}", flush=True)
    persona_mod.migrate_identity(records / "settings.json", persona_files)
    prefs = settings_mod.Settings(records / "settings.json",
                                  files=persona_files)
    asr_engine_opts = dict(cfg.get("asr_engine") or {})
    # D-36 CLOSED. The Recognition group reaches the recognizer.
    #
    # These three were inert from the day they were written: `build_hub` read
    # the adapter's options out of `config/models.json` and constructed
    # `prefs` afterwards, so the store could not have been consulted even if
    # something had asked it to. All three saved, reported success, broadcast
    # to every satellite and showed a restart badge, and changed nothing.
    #
    # THE RULING (2026-09-10): the SETTING wins, and `config/models.json`
    # keeps only what is not a setting -- which adapter to load. Two places
    # naming one value is D-35's shape, and `plan.md` 4.6a already says which
    # of them is the contract: "the schema IS the contract". A value left in
    # `asr_engine` for one of these keys is therefore a REFUSAL rather than a
    # merge, for the reason the `language` check below gives.
    for key, setting in (("model", "asr_model"),
                         ("backend", "asr_backend"),
                         ("latency", "asr_latency")):
        chosen = str(prefs.get(setting, "") or "").strip()
        stale = asr_engine_opts.pop(key, None)
        if stale is not None:
            # LOUD, and never fatal. This raised SystemExit for about an hour
            # on 2026-09-10 and took the owner's daemon down with it: their
            # `asr_engine` has carried `{"backend": "cpu"}` since long before
            # these settings did anything, so the refusal fired on a config
            # that had always been correct. A daemon that will not start is
            # the worst outcome this file can produce -- there is no
            # microphone at all, and no page to fix it from (D-1). The
            # setting simply wins and the dead key is named so it can be
            # deleted.
            print(f"[butler] config/models.json still sets "
                  f"asr_engine.{key}={stale!r}; the {setting!r} SETTING owns "
                  f"that now and is winning. Delete the key from the config "
                  f"file (AC-152, D-36).", flush=True)
        # Unconditional, and worth being clear about: all three of these
        # settings have a NON-BLANK default, so "the owner never chose" and
        # "the owner chose the default" are the same state and there is no
        # way to tell them apart. The setting therefore wins even at its
        # default -- which means an owner who set `backend: cpu` in the
        # config file and never opened the page gets `auto` on the next
        # restart. That is the ruling applied honestly rather than softened,
        # and the line above is how they find out.
        asr_engine_opts[key] = chosen or stale
    # The language is the OWNER's, and there is exactly one place it comes
    # from (AC-152). The adapter takes a `language` constructor kwarg that
    # WINS over the per-session `AsrConfig`, so a `language` here would make
    # the setting silently inert again -- D-36 in the very file the fix
    # exists to close. Refused loudly rather than merged or ignored, because
    # the failure it prevents is invisible: everything saves, everything
    # reports success, and the recognizer decodes something else.
    stale_language = asr_engine_opts.pop("language", None)
    if stale_language is not None:
        # Loud and never fatal, for the reason the loop above now carries.
        # DROPPED rather than honoured, unlike the three above: the language
        # is per-turn and the Hub puts the owner's on every `AsrConfig`, so
        # a constructor kwarg here would beat it on every single turn --
        # which is the whole defect (D-35, D-36).
        print(f"[butler] config/models.json sets "
              f"asr_engine.language={stale_language!r}; ignoring it. The "
              f"language is a SETTING (Settings > Recognition > Language) "
              f"and a value here would override it on every turn. Delete "
              f"the key (AC-152).", flush=True)
    asr = ports.build(ports.ASRS, names.get("asr", "fake"), **asr_engine_opts)
    tts = ports.build(ports.TTSS, names.get("tts", "fake"))

    store = modes_mod.ModeStore(records / "mode.json")
    conns = conn_mod.ConnectionStore(records / "connections.json",
                                     pi_registry=PI_REGISTRY)
    # The binding table, beside the other stores and with its path handed in
    # for their reason: a test must be able to point it somewhere that is not
    # the owner's live `state/` (test/AGENTS.md).
    shorts = shortcuts_mod.ShortcutStore(records / "shortcuts.json")
    # ONE-TIME (AC-144). Reads the settings FILE rather than `prefs`, because
    # `terminal_cwd` is gone from the schema in the same commit and
    # `Settings._load` drops a key it no longer knows -- which is exactly why
    # the migration cannot be a `prefs.get`.
    migrated = shorts.migrate_terminal_cwd(records / "settings.json")
    if migrated:
        print(f"[butler] the retired terminal_cwd setting ({migrated}) is now "
              f"a shortcut row bound to "
              f"{shortcuts_mod.MIGRATED_TERMINAL_CHORD} (AC-144)", flush=True)
    # ONE-TIME, and the same reason again: `projects` is retired from the schema
    # in this commit, so its values are read off the FILE. The DATA ROOT joins
    # them as the ROOT project the owner asked for -- the legacy layout's data
    # root is the repo itself, a migrated one is the owner's home folder
    # (docs/data-root.md, AC-415) -- as an ordinary row so every chord, button
    # and gate works on it with no branch. The root row is ENSURED on every
    # boot, so a data root that moved gets its row without a second flag.
    made = shorts.migrate_projects(records / "settings.json", where.data)
    if made:
        print(f"[butler] the projects pane is now the one project list; "
              f"{len(made)} row(s) added from the retired projects setting and "
              f"Butler's own directory: {made}", flush=True)

    # `brain_mode` is the owner's choice between the two real brains, read
    # ONCE here because the brain is constructed at boot -- which is why the
    # setting is restart-flagged rather than silently taking effect never
    # (AC-7b). The config file and BUTLER_BRAIN still win, because that is how
    # every other port is overridden in a test, and because a machine with a
    # config file has already said what it wants.
    names.setdefault("brain", BRAIN_MODES[str(prefs.get("brain_mode", ""))])

    # A brain that talks to a server needs the one resolver this daemon
    # builds -- the owner's {conn, model} pair plus its cap, resolved per turn
    # (4.4b, docs/architecture-seams.md 3.4). Which brains those are is a
    # CAPABILITY the adapter declares, not a name this file tests: the next
    # one that needs a resolver adds `NEEDS_TARGETS` and nothing here changes
    # (the same rule as AC-35's `native_resume`). An unknown adapter name
    # still reaches `ports.build` and still fails loudly there.
    brain_name = names["brain"]
    brain_cls = ports.BRAINS.get(brain_name)
    brain_kwargs = {}
    if getattr(brain_cls, "NEEDS_TARGETS", False):
        brain_kwargs["resolver"] = conn_mod.SettingsResolver(conns, prefs)
    # Who the owner is and who Butler is (docs/identity-memory.md 4.2): the
    # conversation model gets the persona's labelled blocks per turn, through
    # a bound method rather than the store, by the same capability rule.
    # Memory (docs/identity-memory.md 4.5 and 12). ONE store: the hub writes
    # it on "remember that", the memory routes and the endpoint brain's tools
    # read and write it, and the persona reads its index into both paths
    # (AC-433, D-18). Its search is an FTS5 CACHE over the note files and
    # the conversation records, opened here -- fail fast without FTS5,
    # rebuilt when missing, corrupt or of another schema (AC-431). At BOOT,
    # before the loop serves anything, so the rebuild blocks nobody (D-1).
    index = memory_index_mod.MemoryIndex(
        records / "index" / "memory.sqlite", records / "memory",
        records / "chats",
        project_label=lambda path: getattr(shorts.row_for(path), "label",
                                           None) or None)
    started = time.monotonic()
    rebuilt = index.open()
    print(f"[butler] memory index "
          f"{'REBUILT because ' + rebuilt if rebuilt else 'reused'} "
          f"in {time.monotonic() - started:.2f} s: {index.path}", flush=True)
    memory = memory_mod.MemoryStore(records / "memory", index=index)
    # Provenance (12.11): a note file that changed while Butler was stopped is
    # recorded as a hand edit NOW, before any write of its own lands on top.
    found = memory.reconcile()
    if found:
        print(f"[butler] memory ledger: {len(found)} note change(s) made "
              f"outside Butler recorded as hand edits: {found}", flush=True)
    # Recipes and tasks (docs/automations.md AU-1, AU-2): under the records,
    # scoped by the pane's ROW IDS through two callables read per call, so a
    # row added or removed reaches the very next request and this store
    # knows nothing about shortcuts.
    library = library_mod.Library(
        where.library, rows=lambda: {r.id for r in shorts.all()},
        root_id=shorts.root_id,
        projects=lambda: [(r.id, r.label or settings_mod.project_name(r.path),
                           r.path) for r in shorts.all()])
    # Where a coding agent reaches the memory tools: this daemon's LOOPBACK
    # address, which only `main` knows and writes into the environment
    # (BUTLER_SELF_URL, the BUTLER_DATA precedent). Blank -- every test's
    # `make_app({})` -- gives a harness child no extension and no rules, so
    # nothing advertises a tool that is not there (12.6).
    self_url = os.environ.get("BUTLER_SELF_URL", "").strip()
    # A CONFIGURED URL's hostname, not a request: the one read of LOOPBACK
    # outside `hub._is_local` (AC-541's source test names it). `main` writes
    # MAIN's 127.0.0.1 address, which `_is_local` trusts.
    if self_url and (urllib.parse.urlsplit(self_url).hostname
                     not in hub_mod.LOOPBACK):
        raise SystemExit(f"[butler] BUTLER_SELF_URL={self_url!r} is not a "
                         f"loopback address; the memory tools are served to "
                         f"this machine only (docs/identity-memory.md 12.6)")
    # The Memory rules go exactly where the tools are (AC-438): the endpoint
    # brain gets the store in-process, a harness brain only with a URL.
    has_tools = (getattr(brain_cls, "NEEDS_MEMORY", False)
                 or (getattr(brain_cls, "NEEDS_HARNESS", False)
                     and bool(self_url)))
    persona = persona_mod.Persona(
        persona_files, memory, records / "prompt", rules=has_tools,
        projects=lambda: [(name, path, rid == shorts.root_id())
                          for rid, name, path in library.projects()])
    # BOOT is the one moment no child is alive, so no live spec names any
    # rendered file; the next spec re-renders what it needs (AC-373).
    pruned = persona.prune()
    if pruned:
        print(f"[butler] removed {len(pruned)} rendered prompt file(s) no "
              f"live child names: {pruned}", flush=True)
    if getattr(brain_cls, "NEEDS_PERSONA", False):
        brain_kwargs["persona_for"] = persona.blocks
    if getattr(brain_cls, "NEEDS_MEMORY", False):
        brain_kwargs["memory"] = memory
    if getattr(brain_cls, "NEEDS_TASKS", False):
        # The library tools for the endpoint brain (docs/automations.md
        # AU-9; docs/proactive.md PR-20, PR-21), through the hub's ONE
        # dispatcher. This brain holds no conversations, so everything it
        # adds is the ROOT row's -- the Global section, where a chat with no
        # project lives. `hub` is bound below, before any turn can call this.
        async def endpoint_task(name, args, chat, turn):
            return await hub.library_tool(name, args, library.root_id(),
                                          chat, turn)
        brain_kwargs["tasks"] = endpoint_task
    if getattr(brain_cls, "NEEDS_GUIDE", False):
        # `butler_help` for the endpoint brain (docs/butler-help.md BH-2):
        # the live state is read at each call (BH-5). `hub` is bound below,
        # before any turn can call this.
        async def endpoint_guide(topic):
            return guide_mod.help_text(topic, await asyncio.to_thread(
                guide_state, hub))
        brain_kwargs["guide"] = endpoint_guide
    if getattr(brain_cls, "NEEDS_WHATSAPP", False):
        # The WhatsApp tools for a brain that runs them in-process
        # (docs/messaging.md MS-8, MS-18b): the archive read-only, sends
        # through the one door (`whatsapp_mod.door`), files only from the
        # data root -- this brain's one project, the root row's.
        # A held first send is put to the owner by the hub (RV-2, AC-588);
        # `hub` is bound below, before any turn can call this.
        async def endpoint_ask(chat, title, message, turn):
            return await hub.ask_owner(chat, title, message, turn)
        agent = whatsapp_mod.AgentTools(
            prefs, where.whatsapp / "whatsapp.db", where.data,
            forbidden=containment_mod.records_dirs(where), ask=endpoint_ask)
        brain_kwargs.update(whatsapp_tools=agent.offered,
                            whatsapp_call=agent.call)
    if getattr(brain_cls, "NEEDS_HARNESS", False):
        # The same capability rule one seam further out (docs/harness-mode.md
        # 7.4). A brain that TALKS to a coding agent gets TWO callables -- one
        # that builds an adapter from the current `harness` setting and one
        # that resolves what that adapter should be started as -- not the
        # settings store and not a path, because an adapter holding the store
        # can read anything in it and the seam stops being a seam. No child
        # is built here at all: one is started by the first turn that needs it
        # (AC-59), never by a boot nobody has spoken to.
        conversations = chats_mod.ChatStore(
            records / "chats",
            artifacts=shown)
        # ONE-TIME PER BOOT (R5, `docs/filing-and-pacing.md`: "no unprojected
        # conversations"), after `shorts.migrate_projects` above -- which is
        # what gives the data root a matchable row for the pane to file these
        # under.
        # `chats.py`'s own comment used to say these were deliberately NOT
        # attributed to the root; that ruling is OVERTURNED here rather than
        # silently reversed (AC-97's docstring, D-20's pattern).
        filed = conversations.migrate_unfiled(where.data)
        if filed:
            print(f"[butler] {len(filed)} conversation(s) with no project are "
                  f"now filed under Butler's own directory (R5, no "
                  f"unprojected conversations): {filed}", flush=True)
        brain_kwargs.update(
            harness_for=harness_factory(prefs),
            spec_for=harness_spec_resolver(prefs, conversations,
                                           shorts.paths,
                                           persona.append_file,
                                           memory_url=self_url or None),
            # Butler's own record of the conversation: one flat JSON file per
            # chat (AC-35, 10.2). Handed over because this adapter is the only
            # thing that sees the conversation -- unlike the settings store,
            # which no adapter may hold (4.10).
            chats=conversations,
            # How many coding agents may be alive at once (AC-237). A THIRD
            # resolver for the same reason as the other two: a callable, not
            # the store and not a number, so the owner's change reaches the
            # next thing they say without a restart.
            cap_for=lambda: prefs["harness_agents_max"],
            # One active chat per PROJECT, and how long an idle one keeps
            # the slot (docs/filing-and-pacing.md 1.6). Two more callables,
            # for `cap_for`'s own reason.
            project_limit_for=lambda: prefs["project_agent_limit"],
            project_grace_for=lambda: prefs["project_idle_grace_minutes"])
    brain = ports.build(ports.BRAINS, brain_name, **brain_kwargs)

    # The model the ADAPTER was just built with, so the two agree: the
    # adapter raises when an AsrConfig names a different catalogue slug, and
    # the setting has just moved the adapter (D-36).
    asr_cfg = ports.AsrConfig(**{**(cfg.get("asr") or
                                    {"model": "parakeet-unified-en-0.6b"}),
                                 **({"model": asr_engine_opts["model"]}
                                    if "model" in asr_engine_opts else {})})
    hub = hub_mod.Hub(asr=asr, tts=tts, brain=brain, modes=store,
                      asr_config=asr_cfg, settings=prefs,
                      endpointer=build_endpointer(
                          endpointer_choice(prefs, names)),
                      connections=conns,
                      shortcuts=shorts, memory=memory, library=library,
                      attachments=attach_mod.AttachmentStore(
                          where.attachments))
    hub.artifacts = shown
    # The spoken model choice (docs/chat-model.md CM-10) uses the routes' own
    # rules; hub.py cannot import this module, so they are handed over.
    hub.chat_models = hub_mod.ChatModels(
        agent_of=lambda chat: chat_harness(prefs, chat), labels=AGENT_LABELS,
        remember=remember_model, valid=chats_mod.MODEL_NAME.fullmatch)
    # THE CONFIRM-TO-STOP MECHANISM'S OTHER HALF (docs/filing-and-pacing.md
    # 1.7, phase 5), wired POST-CONSTRUCTION rather than threaded through
    # `brain_kwargs` like `cap_for`/`project_limit_for`/`project_grace_for`
    # above: this is the one resolver that needs the Hub ITSELF
    # (`hub.stop_conversation`), and the Hub cannot exist before the brain
    # it is built from -- the reverse of the other three, which are all
    # settings-shaped and need nothing but `prefs`.
    host = getattr(brain, "host", None)
    if host is not None:
        host.set_stop_for(hub.stop_conversation)
    # AUTOMATIONS (docs/automations.md phase 5): the store beside the
    # library's, and the scheduler that needs this hub. Built here, STARTED
    # by the app (`_on_start`) -- a tick needs a running loop, and a hub a
    # test builds must not start deciding on its own. A new automation's
    # channels start as `notify_default` when that setting names one, else
    # none (AC-474). THE CHANNEL REGISTRY is built HERE, once, and handed to
    # the scheduler (a run's result) and to the channel routes (`make_app`)
    # alike -- one folder, one stat cache, one scrub (phase 7b, D-18).
    def default_channels():
        name = str(prefs.get("notify_default", "") or "").strip()
        return [name] if name else []
    # The heartbeat's checklists and quiet hours' held lines are records
    # too (docs/proactive.md PR-9, PR-16), spelled only in `paths.py`.
    # TRIGGER SOURCES (docs/proactive.md PR-26..PR-30): files like the
    # channels; the store refuses an automation bound to a name that is not
    # one. Started beside the scheduler by the app, like it.
    sources = triggers_mod.Sources(where.triggers)
    hub.automations = automations_mod.AutomationStore(
        where.library, library, schedule_mod.local_zone(),
        default_channels=default_channels, heartbeat=where.heartbeat,
        prechecks=where.prechecks, sources=sources.names,
        quiet=lambda: (prefs["quiet_start"], prefs["quiet_end"])
        if prefs["quiet_hours"] else None)
    hub.channels = notify_mod.Channels(where.notify)
    hub.scheduler = automations_mod.Scheduler(
        hub.automations, hub, hub.channels,
        held=automations_mod.HeldSpeech(where.held_speech))
    hub.triggers = triggers_mod.Triggers(
        sources, where.trigger_cursors, hub.scheduler, hub.channels, prefs,
        schedule_mod.local_zone())

    # SETTINGS THAT TAKE EFFECT WITH NO OWNER RESTART (AC-713..AC-716).
    # The two cheap engines are rebuilt from the CURRENT value, warmed, and
    # only then handed to the hub; a capture already open keeps its own.
    async def swap_asr():
        # The latency only. Model and backend stay what this process booted
        # with: a change to either is applied by the restart (AC-715), and
        # building them here would load a second GGUF beside the first.
        fresh = ports.build(ports.ASRS, names.get("asr", "fake"),
                            **{**asr_engine_opts,
                               "latency": prefs["asr_latency"]})
        await fresh.warm()
        hub.asr = fresh

    async def swap_endpointer():
        fresh = build_endpointer(endpointer_choice(prefs, {}))
        if fresh is not None:
            await fresh.warm()
        hub.endpointer = fresh

    hub.applier = apply_mod.Applier(
        prefs, {"asr_latency": swap_asr, "endpointer": swap_endpointer},
        busy=hub.busy, restart=lambda: do_restart(hub))
    return hub


def endpointer_choice(prefs, names: dict) -> str:
    """Which end-of-turn model to build: `BUTLER_ENDPOINTER`, else the
    SETTING (AC-714).

    Until 2026-10-27 this read only the environment and the config file, so
    the `endpointer` setting saved, reported success and did nothing -- D-36's
    shape. The setting wins over the config file by D-36's ruling; a key left
    there is named loudly and never fatal. The environment still wins because
    that is how every port is pinned for a test (`BUTLER_ASR`, `BUTLER_BRAIN`).
    """
    stale = names.get("endpointer")
    if stale is not None:
        print(f"[butler] config/models.json still sets adapters.endpointer="
              f"{stale!r}; the 'endpointer' SETTING owns that now and is "
              f"winning. Delete the key from the config file (AC-714).",
              flush=True)
    return os.environ.get("BUTLER_ENDPOINTER") or str(prefs["endpointer"])


def build_endpointer(chosen: str):
    """The callable that answers "have they finished speaking?".

    Held by the Hub and handed to endpointing.SmartTurnEnd for every turn in
    smart mode. None disables the smart mode entirely -- and says so when the
    setting asks for it (endpointing.build), rather than running patient
    silence under the smart name.
    """
    if chosen not in ENDPOINTERS:
        raise KeyError(f"no endpointer named {chosen!r}; "
                       f"known: {sorted(ENDPOINTERS)}")
    factory = ENDPOINTERS[chosen]
    return factory() if factory else None


async def warmup(hub: hub_mod.Hub) -> None:
    """Pay every cold start at boot, never mid-conversation (D-10).

    An earlier prototype observed a cold start measured in minutes when the streaming
    graph warmed up during a real turn. Kokoro's own cold start is 1.2 s
    measured -- small, but it lands on the first thing the owner ever says.
    """
    # The brain joined this list on 2026-08-17. It is the one with the worst
    # cold start of the three -- 15.85 s for the SMALLEST model on this
    # machine [MEASURED, plan.md 4.4] against Kokoro's 1.2 s -- so leaving it
    # out was D-10 aimed at the largest target in the system (AC-42).
    for name, port in (("asr", hub.asr), ("tts", hub.tts),
                       ("brain", hub.brain)):
        try:
            await port.warm()
        except Exception as e:                        # noqa: BLE001
            # A warm failure is not fatal -- the daemon still answers, just
            # slowly on the first turn -- but it must be loud, because the
            # symptom otherwise is "the first thing I said was ignored".
            print(f"[butler] WARNING: {name} warmup failed: {e!r}")
    # The language detector is warmed HERE and not lazily on the first turn,
    # for the same reason as the three above (D-10): loading its model costs
    # 497 ms [MEASURED 2026-09-11] on a single-threaded daemon, which is every
    # satellite frozen, and it would land on the first thing the owner ever
    # says. Classifying afterwards is 0.02 ms, so nothing else about it needs
    # a thread. Its candidate set is the SPEECH engine's languages, which is
    # why this runs after `tts.warm()` filled them (AC-207, AC-108).
    #
    # Loud and never fatal: with no detector every turn falls back to the
    # conversation's current language, which is a worse answer rather than a
    # dead microphone (D-1).
    try:
        dialogue.warm_detector(ports.voice_languages(hub.tts.info))
    except Exception as e:                            # noqa: BLE001
        print(f"[butler] WARNING: the language detector would not load "
              f"({e!r}); automatic language will keep whatever language each "
              f"conversation starts in")
    await warm_endpointer(hub)


async def warm_endpointer(hub: hub_mod.Hub) -> None:
    """Start the smart-turn child and load its model, on its own budget.

    Two rules, and they pull in opposite directions on purpose:

    * D-10, D-5. The load happens here, at boot, with LOAD_TIMEOUT_S rather
      than the 2 s an inference gets. Left to the first turn, the spawn plus
      the model load land inside the policy's per-inference timeout, blow it,
      and the turn ends on the silence cap -- smart mode behaving exactly like
      patient mode on the first thing the owner ever says, which is also the
      hardest version of this to diagnose later.
    * Fail fast, and fail where it is visible. If the setting says "smart" and
      the model will not load, the daemon does not start. Booting anyway would
      mean running patient silence while every UI still reports "smart"; the
      whole reason the mode exists is that it behaves differently.

    When the setting does NOT say smart, a broken endpointer is a loud warning
    and the callable is dropped, so `endpointing.build` refuses the smart mode
    out loud if someone switches to it later rather than silently degrading.
    """
    if hub.endpointer is None:
        return
    try:
        ms = await hub.endpointer.warm()
    except Exception as e:                            # noqa: BLE001
        if hub._turn_mode() == "smart":
            raise SystemExit(
                f"[butler] FATAL: turn_mode is 'smart' but the smart-turn "
                f"endpointer will not start: {e!r}. Fix the model (see "
                f"README, Appendix B) or set turn_mode to 'patient'.") from e
        print(f"[butler] WARNING: smart-turn endpointer failed to warm "
              f"({e!r}); the 'smart' turn mode is UNAVAILABLE this run")
        hub.endpointer = None
        return
    print(f"[butler] smart-turn endpointer warm in {ms:.0f} ms "
          f"({hub.endpointer.model_path.name})")


def _restart_argv() -> list[str]:
    """The ORIGINAL command line, so the re-exec comes back IDENTICAL.

    `[python, -X, utf8, butlerd.py, <the same flags>]` -- same script, same
    `--tls`/`--port`/`--host`, so the daemon returns on the SAME port with the
    SAME posture and re-reads `state/settings.json` and `config/models.json`.
    That re-read is the whole point: `brain_mode`, `asr_engine` and the other
    restart-flagged settings are read ONCE at boot (build_hub), so changing one
    on the settings page does nothing until a stop/start -- and this IS the
    stop/start, without the owner leaving the room.
    """
    return [sys.executable, "-X", "utf8", sys.argv[0], *sys.argv[1:]]


async def _restart_cleanup(hub) -> None:
    """Stop the child processes this daemon owns, BEFORE the re-exec.

    THE ONE THING TO GET RIGHT. `os.execv` replaces the process IMAGE but keeps
    the process and its open handles, so the Win32 kill-on-close Job Object
    (`childproc.py`) persists and its members -- the kokoro sidecar and the
    kokoro worker under it -- survive into the new image, which then spawns a
    SECOND sidecar. That is one leaked node tree per restart. `atexit` does not
    run under `execv` either, so `_stop_all_sidecars` never fires. So the stop
    has to be explicit and it has to happen HERE, before `do_restart` execs.

    Reached through the adapters' OWN stop paths rather than an invented one:
    `KokoroTts.close` -> `_Sidecar.stop` -> `taskkill /F /T` of the tree
    (tts_kokoro.py). The fakes have no `close`/`dispose`, so both branches are
    skipped for a test daemon -- which is why the order test below drives real
    recorders rather than the fixture. Loud, never fatal: a teardown that
    raised here would abort the restart and leave the daemon half-torn-down.
    """
    # The coding-agent child, which since AC-59 is normally RUNNING when this
    # HTTP request lands: a conversation holds one between turns, and this
    # request arrives between turns. `execv` keeps this process's handles, so
    # skipping it would carry a full-capability agent into the new image with
    # nobody holding it (AC-62, AC-36, D-24).
    dispose = getattr(hub.brain, "dispose", None)
    if dispose is not None:
        try:
            await dispose()
        except Exception as e:                            # noqa: BLE001
            print(f"[butler] restart: brain dispose failed: {e!r}", flush=True)
    # The kokoro sidecar: the node process the Job Object would otherwise carry
    # across the exec. Off the event loop because the stop path shells out to
    # taskkill and blocks (D-25); a restart can afford the wait, the loop can't.
    close = getattr(hub.tts, "close", None)
    if close is not None:
        try:
            await asyncio.to_thread(close)
        except Exception as e:                            # noqa: BLE001
            print(f"[butler] restart: tts close failed: {e!r}", flush=True)
    # The desk agent, for the same reason and with a sharper cost: it holds a
    # system-wide keyboard hook, so an orphan carried across the exec goes on
    # suppressing the owner's key while a second agent is spawned beside it
    # (AC-122). Unconditional -- `stop` is idempotent and cheap when nothing
    # is running, which is the common case since hotkeys ship off.
    try:
        await asyncio.to_thread(desk_mod.stop)
    except Exception as e:                            # noqa: BLE001
        print(f"[butler] restart: desk agent stop failed: {e!r}", flush=True)
    # The WhatsApp sidecar (docs/messaging.md MS-2, AC-500), for the kokoro
    # sidecar's reason: the Job Object would carry it across the exec, and the
    # new image would start a second one holding the same session. A hub a
    # test builds by hand has none.
    whatsapp = getattr(hub, "whatsapp", None)
    if whatsapp is not None:
        try:
            await whatsapp.stop()
        except Exception as e:                        # noqa: BLE001
            print(f"[butler] restart: whatsapp stop failed: {e!r}",
                  flush=True)
    # The Telegram reader (docs/messaging.md MS-25): its long poll is a
    # daemon thread the exec ends anyway; stopping the loop first means no
    # update is taken by an image that is about to go.
    telegram = getattr(hub, "telegram", None)
    if telegram is not None:
        await telegram.close()


async def do_restart(hub) -> None:
    """Stop children, then REPLACE this process image with a fresh boot.

    Cleanup strictly BEFORE `os.execv`, because the exec keeps the Job Object
    handle and a surviving sidecar becomes a leak (see `_restart_cleanup`). On
    Windows `os.execv` is spawn-then-exit rather than a true in-place replace,
    so the daemon comes back on a NEW pid on the same port; on POSIX it keeps
    the pid. Either way the port, the flags and the settings re-read are what
    matter, not the pid.
    """
    print("[butler] RESTART: stopping children before re-exec", flush=True)
    await _restart_cleanup(hub)
    argv = _restart_argv()
    print(f"[butler] RESTART: re-exec {argv}", flush=True)
    os.execv(sys.executable, argv)


# Set when the daemon should stop serving and exit (docs/desktop-app.md
# DA-11, AC-543). `serve_forever` waits on it; `do_shutdown` sets it.
SHUTDOWN = web.AppKey("butler.shutdown", asyncio.Event)


async def do_shutdown(hub, app: web.Application) -> None:
    """Stop children through the restart's own teardown, then stop serving.

    The SAME `_restart_cleanup` as a restart (brain children, the kokoro
    sidecar, the desk agent), so there is one teardown to keep right; then
    `serve_forever` returns, `runner.cleanup()` runs `on_cleanup` (the
    scheduler) and the process exits 0.
    """
    print("[butler] SHUTDOWN: stopping children, then the listeners",
          flush=True)
    await _restart_cleanup(hub)
    app[SHUTDOWN].set()


@web.middleware
async def revalidate(request, handler):
    """Make the browser check before reusing anything we serve.

    Without a Cache-Control header a browser applies HEURISTIC caching --
    typically a fraction of the time since Last-Modified -- and will happily
    reuse app.js from its memory cache without asking. The page then runs an
    older build than the daemon is serving, and the symptom is not an error:
    it is controls quietly missing, which reads as "the feature was not
    built" rather than "the tab is stale".

    `no-cache` is revalidate-before-use, NOT no-store: the ETag still
    answers 304 for anything unchanged, so this costs one conditional
    request per asset, not a re-download. For a single-user daemon serving
    its own page over a LAN, that is the right trade in every direction.
    """
    response = await handler(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def make_app(cfg: dict, local_url: str = "") -> web.Application:
    """The app. `local_url` is how the DESK AGENT reaches this daemon.

    Empty means no agent is started at all, which is what every test gets:
    `make_app({})` must never spawn a process that installs a system-wide
    keyboard hook on the machine running the suite (AC-121). Only `main`
    knows the scheme and port, so only `main` fills this in.
    """
    hub = build_hub(cfg)
    # One catalogue for the process: the panel builds a picker per model
    # setting, and each one asking the endpoint separately would compete
    # with whatever turn the owner is in.
    catalogue = models_mod.Catalogue()
    # The one gate runs first (docs/remote-access.md RA-1, AC-720).
    app = web.Application(middlewares=[access_mod.gate, revalidate])
    hub_mod.attach(app, hub)
    access_mod.add_routes(app, hub, paths_mod.resolve().records)
    # In-page terminals (docs/chats-and-terminals.md CT-1..CT-6): a project
    # row is a terminal's directory, so no store means no terminals.
    if hub.shortcuts is not None:
        terminals_mod.add_routes(app, hub.shortcuts,
                                 paths_mod.resolve().records, hub.settings)
    app[SHUTDOWN] = asyncio.Event()

    async def health(_req):
        return web.json_response({"ok": True, "proto": hub_mod.PROTO,
                                  "mode": hub.modes.mode})

    async def get_mode(_req):
        return web.json_response({"mode": hub.modes.mode})

    async def index(_req):
        # add_static alone answers 403 for the directory itself, so opening
        # http://host:8140/ -- the only URL anyone actually types -- would
        # serve an error page instead of the satellite. Route it explicitly.
        return web.FileResponse(WEB_DIR / "index.html")

    app.router.add_get("/api/health", health)
    def _settings_body() -> dict:
        """The schema, the values, AND the binding table.

        `bindings` is composed HERE rather than inside `Settings.describe()`
        (`docs/shortcuts.md` 4.2): the settings schema stays the settings
        schema and the shortcuts store stays a store, and the route is the one
        place that holds both. The desk agent reads this key on the same poll it
        already makes, so a rebind reaches the hook within `POLL_SECONDS` and
        no second surface exists to keep in step.

        A daemon with no store serves NO `bindings` key at all rather than an
        empty list, because the agent treats those as different facts: absent
        means "this daemon does not serve bindings, keep what is running", and
        empty would mean "unbind every key on the owner's machine" (AC-140).
        """
        body = hub.settings.describe()
        if hub.shortcuts is not None:
            body["bindings"] = hub.shortcuts.bindings()
        return body

    async def get_settings(_req):
        return web.json_response(_settings_body())

    async def post_settings(req):
        """Change preferences from the web UI.

        Unlike the permission mode, this needs no escalation ceremony: how
        long Butler waits before submitting is a preference, not a boundary
        (settings.py). A bad value is a 400 naming what was allowed, never a
        silent clamp -- and the whole request is validated before anything is
        written, so one bad field cannot half-apply a form.
        """
        try:
            body = await req.json()
        except Exception:                             # noqa: BLE001
            return web.json_response({"error": "body must be json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "body must be a json object of settings"},
                status=400)
        # RA-7: `lan_access` is restart-flagged and AC-715 restarts by
        # itself, and a LAN bind with no certificate refuses to boot. Refuse
        # the write instead of restarting into a dead daemon.
        if body.get("lan_access") is True and \
                not all(p.exists() for p in cert_paths()):
            return web.json_response(
                {"error": "Open on the local network needs a certificate. "
                          "Make one first: python -X utf8 "
                          "scripts\\make-cert.py"}, status=400)
        try:
            applied = hub.settings.update(body)
        except settings_mod.SettingsError as e:
            return web.json_response({"error": str(e)}, status=400)
        await _announce_settings(applied)
        return web.json_response(_settings_body())

    async def post_settings_reset(req):
        group = req.query.get("group")
        try:
            applied = hub.settings.reset(group)
        except settings_mod.SettingsError as e:
            return web.json_response({"error": str(e)}, status=400)
        await _announce_settings(applied)
        return web.json_response(_settings_body())

    async def _announce_settings(applied: dict) -> None:
        """Tell every satellite what changed.

        Broadcast rather than answered only to the caller, because the point
        of a server-side setting is that a second device is not left showing
        a stale value (D-2, AC-8).
        """
        if not applied:
            return
        # `capture_ceiling_ms` rides beside `turn_mode` here too (AC-92's
        # other half): `turn_mode` was ALREADY echoed on every settings
        # change, changed or not, so the panel tracks a spoken change the
        # same way (`web/app.js applyTurnMode`) -- this is the third of the
        # three frames that already carry the mode, not a fourth mechanism.
        # `pending` rides here too (AC-716), so a restart setting changed on
        # the phone shows on the desk's bar without a re-read.
        await hub.broadcast(type="settings", changed=applied,
                            turn_mode=hub.settings.turn_mode,
                            capture_ceiling_ms=hub._capture_ceiling_ms(),
                            pending=hub.settings.pending())
        # The page's detector loads what `ready.wake` said; a change to any
        # wake word, the threshold or the cooldown re-sends it (AC-805).
        if hub.WAKE_SETTINGS & applied.keys():
            await hub.broadcast(type="wake", **hub.wake_frame())

    async def post_restart(req):
        """Restart the whole daemon so restart-flagged settings take effect.

        The response is sent FIRST and the stop-and-exec is scheduled AFTER it
        has flushed, so the page gets its ack and can lean on its own
        auto-reconnect (app.js RECONNECT_MS) to come back. A bare `os.execv`
        inside the handler would replace the process before the 200 left the
        socket and the page would see a dropped connection with no answer.

        Owner-only by the SAME network posture as the rest of this API -- TLS
        on the tailnet, no auth machinery -- because there is none anywhere
        else here and restarting is no more powerful than POST /api/mode
        raising the permission mode across every device at once.
        """
        loop = asyncio.get_running_loop()

        def _go():
            loop.create_task(do_restart(hub))
        # call_later, not create_task now: the 0.1 s lets the response body
        # reach the wire before the process image is replaced.
        loop.call_later(0.1, _go)
        return web.json_response({"ok": True, "restarting": True})

    async def post_shutdown(req):
        """Stop Butler (docs/desktop-app.md DA-11, AC-543): the desktop app's
        Quit. LOCAL ONLY -- unlike restart, nothing brings it back, so a
        phone (PROXIED, or MAIN off this machine) gets 403. Acked first,
        then torn down, for `post_restart`'s reason."""
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "shutdown answers this machine only"}, status=403)
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: loop.create_task(
            do_shutdown(hub, req.app)))
        return web.json_response({"ok": True, "stopping": True})

    async def post_mode(req):
        """Raise or lower the permission mode from the web UI.

        This is the authorized path AC-10 names. Escalation is refused by
        voice for everyone and refused over the WebSocket, and until now no
        client path existed at all -- so the mode could only ever go down,
        and the UI half of AC-10 was unimplemented rather than merely
        untested.
        """
        try:
            body = await req.json()
            to = body["mode"]
        except Exception:                             # noqa: BLE001
            return web.json_response(
                {"error": 'body must be {"mode": "<name>"}'}, status=400)
        try:
            hub.modes.set(to, authorized=True)
        except modes_mod.ModeError as e:
            return web.json_response({"error": str(e)}, status=400)
        await hub.broadcast(type="mode", mode=hub.modes.mode)
        return web.json_response({"mode": hub.modes.mode})

    async def get_models(req):
        """What a target actually serves, so the page can offer it (AC-30).

        The page holds no model names. It asks here, renders the answer, and a
        model that stops being served stops being offered -- which a list
        written into the schema could never do.

        Two failures are kept apart on purpose. An unknown target is the
        CALLER's mistake and answers 400 naming the targets that exist; an
        endpoint that will not answer is the WORLD's and gets a 502 carrying
        what went wrong. Flattening either into a 200 with an empty list would
        draw an empty picker and say nothing was wrong.
        """
        # A CONNECTION is asked by id and resolved through the store, which
        # is the only thing that reads a key (4.4b). `target` remains for the
        # lists that are not connections at all: an engine Butler already
        # holds, and the legacy single endpoint the settings still name.
        conn_id = req.query.get("connection")
        if conn_id:
            try:
                resolved = hub.connections.resolve(conn_id)
            except (KeyError, conn_mod.ConnectionError_) as e:
                # 400: naming a connection that does not exist, or one whose
                # key pointer is broken, is the CALLER's mistake. The message
                # names the connection; it never names the key.
                return web.json_response({"error": str(e),
                                          "connection": conn_id}, status=400)
            try:
                found = await catalogue.models(resolved.base_url,
                                               api_key=resolved.api_key)
            except models_mod.ModelsUnavailable as e:
                return web.json_response({"error": str(e),
                                          "connection": conn_id}, status=502)
            return web.json_response({"connection": conn_id,
                                      "models": found, "favourites": []})

        target = req.query.get("target", "")
        local = LOCAL_TARGETS.get(target)
        if local is not None:
            # No catalogue: the answer is already in memory, and caching a
            # value that costs nothing would only let it go stale.
            return web.json_response({"target": target, "models": local(hub),
                                      "favourites": []})
        local_async = LOCAL_ASYNC_TARGETS.get(target)
        if local_async is not None:
            # An engine Butler holds, but one that has to go and look. A
            # failure here is a 502 carrying the reason, not a 200 with an
            # empty list: the picker draws a loud red note from the first and
            # says nothing at all about the second.
            try:
                found = await local_async(hub)
            except LocalListUnavailable as e:
                return web.json_response({"error": str(e), "target": target},
                                         status=502)
            return web.json_response({"target": target, "models": found,
                                      "favourites": []})
        # Everything remote is a CONNECTION now. `endpoint_url` and its
        # `target=endpoint` are gone: two ways to name a server is exactly
        # the drift connections replaced (plan.md 4.4b).
        return web.json_response(
            {"error": f"name a connection, or a local target. Got "
                      f"target={target!r}; local targets: "
                      f"{sorted(set(LOCAL_TARGETS) | set(LOCAL_ASYNC_TARGETS))}"},
            status=400)

    async def get_session(_req):
        """The conversation this page is joining: `{id, harness, messages}`.

        Straight out of the chat file (AC-35, 10.2). What it no longer
        carries is whether the AGENT still remembers any of it: the
        new/resumed/lost line went with `daemon/sessions.py` on 2026-08-18,
        and 10.5 records the loss rather than replacing it with a guess.

        404 rather than an empty object when nothing here holds a
        conversation: endpoint mode has no chat at all, and an empty
        transcript is a different fact from a daemon that keeps none.

        BOUNDED, at the newest `MESSAGES_SERVED`. A conversation runs for as
        long as the owner keeps talking, so an unbounded answer grows without
        limit to feed a page that shows eight turns. `turns` in `ready` still
        counts the whole conversation, which is why the count and the
        transcript length are allowed to disagree.

        **ANNOTATED BY `_filed`, like every listed conversation** (review,
        2026-09-11). `project` is unchanged -- the absolute path the birth
        wrote -- and `projectId` rides beside it, because the page had nothing
        else to compare with: it matched `row.path === chatProject` as RAW
        STRINGS, so re-spelling a row's path with a trailing separator left the
        daemon still filing conversations under it while the tile's "you are
        here" mark disappeared and its popover flipped from "working here" back
        to "work here". One annotator for every conversation that crosses the
        wire, live or past, or the page needs two rules.
        """
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        doc = _with_choice(hub, brain, _with_agent(hub, _filed(
            hub, [brain.session_transcript(limit=MESSAGES_SERVED)]))[0])
        # **AND WHAT THE CODING AGENT IS BLOCKED ON** (AC-249). This is the
        # whole of the recovery story and it needs no protocol: a reloaded tab
        # and a second device both ask the route they were already asking, and
        # a daemon that restarted answers with none, correctly, because every
        # child died with it. It rides on the LIVE conversation's document
        # rather than on a route of its own, so a page that draws a
        # conversation cannot draw it without the question in it.
        doc["questions"] = hub.questions_for(doc.get("id") or "")
        return web.json_response(doc)

    async def post_session_new(req):
        """Start a new conversation from the page, BORN IN A PROJECT (AC-63,
        AC-287, `docs/filing-and-pacing.md` 8, R-A).

        `{"project": "<row id>"}` names the project, and that is the ONE
        moment the chat's project is set: there is no route that re-files a
        chat afterwards (`POST /api/project` is deleted), so the page's old
        two-step birth -- rotate, then file -- is one call and a chat is never
        briefly unprojected or filed wherever the previous one lived. An
        absent or empty body births in the ROOT row (D-A1). An id no row has
        is a 400 naming it, and so is a directory AC-31's gate refuses at
        birth (D-A2); nothing is minted on either.

        The same `Hub.new_conversation` the spoken "new conversation" uses
        (D-18), so the page's New chat now also clears the router's short
        memory (AC-64), which this route used to skip.

        It rotates the RECORD and nothing else. The held child is not disposed
        here, because rotating mints a new `sessionRef` and the next turn's
        `ensure()` sees a spec that no longer matches and replaces the child on
        its own -- one invalidation rule, not two that can disagree (AC-60).

        404 in endpoint mode, matching `GET /api/session/current`. Answers
        `{"id", "project", "projectId"}`.
        """
        if hub.session_brain() is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        wanted = str(body.get("project") or "").strip() or None
        # docs/chat-agent-picker.md AP-1: checked BEFORE the birth, so a
        # refused agent mints nothing.
        agent = str(body.get("agent") or "").strip()
        refused = _refused_agent(hub.settings, agent) if agent else None
        if refused is not None:
            return refused
        try:
            born = await hub.new_conversation(wanted)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=400)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        if agent:
            hub.session_brain().choose_agent(born["id"], agent)
        print(f"[butler] a new conversation was started from the page: "
              f"{born['id']}, born in {born['project']} (AC-63, AC-287)")
        return web.json_response(born)

    async def post_chat_model(req):
        """This conversation's own model, or "" for its agent's default
        (docs/chat-model.md CM-1, CM-3, AC-780). Any time, from the next
        turn. The name is checked for shape only (it rides the argv): a model
        the catalogue does not list is kept, not replaced (CM-5)."""
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        try:
            body = await req.json()
            model = str(body["model"]).strip()
        except Exception:                                 # noqa: BLE001
            return web.json_response(
                {"error": 'body must be {"model": "<name>" or ""}'},
                status=400)
        cid = req.match_info["cid"]
        try:
            chat = brain.set_model(cid, model)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=404)
        agent = chat_harness(hub.settings, chat)
        if model:
            await asyncio.to_thread(remember_model, agent, model)
        doc = _with_agent(hub, [{"id": cid, "harness": chat["harness"],
                                 "model": model}])[0]
        return web.json_response(_with_choice(hub, brain, doc))

    async def get_agent_models(req):
        """What the chat chip's sheet lists for one agent (CM-7, CM-8,
        AC-784): its resolved default, the shared favourites it lists, its
        recents, and its whole catalogue from its own lister."""
        agent = req.match_info["agent"]
        harness = ports.HARNESSES.get(agent)
        lister = getattr(harness, "list_models", None)
        if harness is None or lister is None:
            return web.json_response(
                {"error": f"{agent!r} is not a coding agent that lists "
                          f"models"}, status=404)
        try:
            models = list(await lister())
        except Exception as e:                            # noqa: BLE001
            return web.json_response(
                {"error": f"{agent} could not list its models: {e}"},
                status=502)
        listed = set(models)
        favourites = [m for m in (hub.settings.get("model_favourites") or [])
                      if m in listed]
        return web.json_response({
            "agent": agent,
            "default": default_model(hub.settings, agent),
            "favourites": favourites,
            "recent": (await asyncio.to_thread(recent_models)).get(agent,
                                                                   []),
            "models": models})

    async def post_chat_agent(req):
        """Change the agent of a conversation nobody has spoken in yet
        (docs/chat-agent-picker.md AP-2, AC-775). 409 once it has started:
        a conversation stays with the agent it began with."""
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        try:
            body = await req.json()
            agent = str(body["agent"]).strip()
        except Exception:                                 # noqa: BLE001
            return web.json_response(
                {"error": 'body must be {"agent": "<name>"}'}, status=400)
        refused = _refused_agent(hub.settings, agent)
        if refused is not None:
            return refused
        cid = req.match_info["cid"]
        try:
            chat = brain.choose_agent(cid, agent)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=404)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)
        doc = _with_agent(hub, [{"id": cid, "harness": chat["harness"]}])[0]
        return web.json_response(_with_choice(hub, brain, doc))

    async def post_session_open(req):
        """Make a conversation the one the next turn goes to (AC-239, AC-240).

        `{"id": "chat-..."}` in, and out comes `GET /api/session/current`'s
        exact shape -- `_filed`-annotated, bounded at the same
        `MESSAGES_SERVED` -- so the page repaints the conversation it just
        opened with the renderer it already has, and learns which project row
        it is in from the same answer.

        **A POST BECAUSE IT CHANGES WHICH CONVERSATION BUTLER IS HOLDING.**
        `GET /api/chats/{id}` is the read that peeks at a record and is
        unchanged; this one moves the pointer, and a GET that did it would be
        a write behind a verb every cache and every probe treats as safe.
        Until it existed the page had to LEAVE a past conversation before it
        could send anything (`backToLive()`), which is the owner's own rule
        inverted -- the chat you click is the chat you talk to.

        **IT SPAWNS NOTHING** (AC-239). Browsing history starts no coding
        agent; the first send is what does, because the spawn is reachable
        only from a turn.

        404 in endpoint mode on `NO_CONVERSATION`, like every session route
        beside it, and 404 naming the id when no record has it -- `get_chat`'s
        wording, because they answer the same question about the same key.
        400 for a body naming nothing.
        """
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            body = {}
        chat_id = str((body or {}).get("id") or "").strip()
        if not chat_id:
            return web.json_response(
                {"error": 'name the conversation to open: '
                          '{"id": "chat-..."}'}, status=400)
        opened = hub.open_conversation(chat_id, limit=MESSAGES_SERVED)
        if opened is None:
            return web.json_response(
                {"error": f"no conversation {chat_id!r}"}, status=404)
        print(f"[butler] the page opened conversation {opened['id']}; the "
              f"next turn goes there and nothing was spawned (AC-239, AC-240)")
        doc = _with_choice(hub, brain,
                           _with_agent(hub, _filed(hub, [opened]))[0])
        # **AND `questions`, LIKE THE TWO GETS BESIDE IT** (AC-249). "The exact
        # shape of `GET /api/session/current`" is a claim this route makes in
        # its own first line and it was false: the two GETs each add this line
        # and this one did not, so a device that connects AFTER the broadcast
        # and then clicks the conversation with the open card drew no card and
        # had no way to learn there was one. That claim has now broken twice in
        # two commits, which is why `test_chats.py` compares the three key sets
        # rather than any one of them against a list.
        doc["questions"] = hub.questions_for(doc.get("id") or "")
        # OPENED ON ANY DEVICE IS READ ON EVERY DEVICE (AC-469, AU-20): a run
        # chat's unread dot clears here, and every page re-reads its tree.
        await hub.chat_seen(opened["id"])
        return web.json_response(doc)

    async def post_session_rewind(req):
        """Go back to an earlier point in the live conversation (AC-189).

        `{"message": "msg-..."}` in, and out comes everything the page needs
        to repaint: the dropped prompt for the composer, the id of the
        conversation the whole original was copied into, the truncated
        transcript, and the new chat list. `delete_chat`'s shape and
        `delete_chat`'s reason -- a page that splices its own array can
        disagree with disk and a page that repaints from the answer cannot
        (AC-195).

        THE LIVE CONVERSATION ONLY (R1). A past conversation is read-only
        (AC-164) and rewinding one would truncate a record the daemon is not
        holding, leaving the owner looking at something nothing can continue.

        Refusals: 409 while this conversation's agent is running or waiting
        on a question (AC-396); 400 for a body with no message, an id this conversation does
        not have, or an ASSISTANT message -- which is not a point a
        conversation can be continued FROM, because what follows it is the
        owner's next instruction and there would be nothing to put in the box
        (R2, AC-191). 404 in endpoint mode, like the session routes beside it.

        Nothing is done to a held child. The rewind clears `sessionRef`, so
        the next turn's `ensure()` sees a changed spec and replaces it
        (AC-60) -- and the new child is re-seeded with the KEPT transcript,
        because the rewind took the old one's memory away with the dropped
        turns (AC-192, AC-193).
        """
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            body = {}
        message_id = str((body or {}).get("message") or "").strip()
        if not message_id:
            return web.json_response(
                {"error": 'name the message to rewind to: '
                          '{"message": "msg-..."}'}, status=400)
        # NOT UNDER A RUNNING AGENT (AC-396, which is AC-248 met). The page
        # hides the control while THIS device is busy (AC-194), which is the
        # wrong test once a turn can run on another device, in the background,
        # or on after a reload: the child's next `record()` would append to a
        # record just truncated, and the transcript would then show a
        # conversation that never happened -- not recoverable. Refused before
        # anything is copied, in words that say what to do.
        live = hub.current_chat_id()
        if hub.status_for(live) in ("running", "waiting"):
            return web.json_response(
                {"error": "this conversation's agent is still running; stop "
                          "it or let it finish, then rewind"}, status=409)
        try:
            done = brain.session_rewind(message_id)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        if done is None:
            return web.json_response(
                {"error": f"this conversation has no message {message_id!r}"},
                status=400)
        answer = brain.session_transcript(limit=MESSAGES_SERVED)
        return web.json_response(
            {"prompt": done["prompt"], "snapshot": done["snapshot"],
             "session": answer,
             "chats": _filed(hub, brain.session_list(limit=CHATS_LISTED))})

    async def get_connections(req):
        """Where Butler may ask, with the keys stripped (AC-34).

        Serves `Connection.public()` rather than the dataclass, so putting a
        resolved value on this wire would take a deliberate edit to a method
        whose docstring says not to.
        """
        return web.json_response(
            {"connections": [c.public() for c in hub.connections.all()]})

    async def post_connections(req):
        """Add one of the owner's own. The body carries the NAME of an
        environment variable, never a key value (4.4b)."""
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            return web.json_response({"error": "body must be JSON"},
                                     status=400)
        cid = str(body.get("id") or "").strip()
        base_url = str(body.get("base_url") or "").strip()
        if not cid or not base_url:
            return web.json_response(
                {"error": "id and base_url are required"}, status=400)
        auth = body.get("auth") or {"from": "none"}
        if not isinstance(auth, dict):
            # A non-dict `auth` used to reach `.get()` and raise
            # AttributeError, which aiohttp turns into a 500 -- an unhandled
            # crash on a route reachable from the tailnet, where the contract
            # everywhere else in this file is a 400 naming what was allowed.
            return web.json_response(
                {"error": "auth must be an object, e.g. "
                          "{'from': 'env', 'var': 'NAME'}"}, status=400)
        if auth.get("from") not in ("none", "env", "pi"):
            return web.json_response(
                {"error": f"unknown auth source {auth.get('from')!r}; "
                          f"known: none, env, pi"}, status=400)
        if "key" in auth or "apiKey" in auth or "value" in auth:
            # Refused rather than ignored. A caller that sent a key believes
            # it was stored; silently dropping it means the connection fails
            # later for a reason nobody connects to this request (4.4b).
            return web.json_response(
                {"error": "auth carries a POINTER, never a value: use "
                          "{'from': 'env', 'var': 'NAME'}"}, status=400)
        try:
            hub.connections.add_manual(conn_mod.Connection(
                id=cid, label=str(body.get("label") or cid),
                base_url=base_url, auth=auth, origin="manual"))
        except conn_mod.ConnectionError_ as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "id": cid})

    async def delete_connection(req):
        """Forget one of the owner's own connections."""
        cid = req.match_info.get("cid", "")
        try:
            hub.connections.remove_manual(cid)
        except conn_mod.ConnectionError_ as e:
            return web.json_response({"error": str(e)}, status=400)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.json_response({"ok": True, "id": cid})

    async def post_connections_reload(req):
        """Re-import pi's providers. Manual entries are untouched (4.4b)."""
        try:
            hub.connections.reload_pi()
        except conn_mod.ConnectionError_ as e:
            return web.json_response({"error": str(e)}, status=502)
        return web.json_response(
            {"connections": [c.public() for c in hub.connections.all()]})

    app.router.add_get("/api/connections", get_connections)
    app.router.add_post("/api/connections", post_connections)
    app.router.add_post("/api/connections/reload", post_connections_reload)
    app.router.add_delete("/api/connections/{cid}", delete_connection)
    async def get_chats(_req):
        """The conversations on disk, NEWEST FIRST: `{chats: [...]}` (AC-164).

        Butler has written one flat JSON file per conversation since AC-35,
        and until this route existed `GET /api/session/current` was the only
        way to any of them -- so every conversation but the live one was
        unreachable from the page that wrote it.

        A row is `{id, title, turns, updatedAt}`. The TITLE is the first
        thing the owner said, truncated (`chats.TITLE_CHARS`): a conversation
        has no name, and inventing a naming ceremony was ruled out of scope.

        BOUNDED at `CHATS_LISTED`, for the same reason the transcript is
        bounded at `MESSAGES_SERVED`.

        Listing is read-only. DELETE arrived 2026-09-10 on the owner's ask
        ("previous chats should be CRUD, so user can delete if needed") and
        is the route below; there is still no RENAME, because the title is
        DERIVED from the first utterance and storing one means a `title`
        field `chats.py` says in its own module docstring is deliberately not
        carried. That is the owner's ruling to make, not a route's.

        **`?q=` SEARCHES, and it is the SAME shape** (AC-185, AC-186, AC-187,
        `docs/chat-search.md`). One rule and it lives here, because the page
        holds only titles and a page-side filter would be a second rule that
        hides rows the daemon would have shown -- invisibly, since a row that
        is not there looks like a conversation that does not exist. A content
        match carries a `snippet` saying why the row is there; a title match
        does not need one. A blank `q` is the plain listing, byte for byte.
        OFF THE EVENT LOOP, and only this branch is: `_records()` reads and
        parses every conversation on disk, which the plain listing pays once
        per page load and a search pays on every keystroke the debounce lets
        through (D-25, `docs/chat-search.md` D3).

        **`?project=` SCOPES, AND THE SCOPING IS THE DAEMON'S** (AC-229,
        `docs/parallel-agents.md` phase 1). `?q=`'s ruling in a second place
        and for the same reason: a page-side filter is a second rule that
        hides rows the daemon would have shown, invisibly, because a row that
        is not drawn looks like a conversation that does not exist. Three
        values, all three real -- a row id, the empty string for the
        conversations filed nowhere, and ABSENT for everything, which is what
        the page sends when no project is chosen. An id no row has is a 400
        naming it (`_chat_scope`).
        It COMPOSES with `?q=`: both in force means matched AND scoped.
        A scoped listing goes off the event loop like a search does and for
        the identical reason -- it is the same walk of `state/chats/`, and
        this one fires on every click along the rail (D-25). The unscoped,
        unsearched listing is untouched and still answered inline, so a page
        load costs exactly what it cost before.

        **EVERY ROW ALSO CARRIES `status`, `elapsed_ms` AND `action`**
        (AC-263, `docs/triage-list.md` phase 1 and 2), in all three branches
        -- plain, `?q=`, `?project=` -- through `_with_status`, applied
        AFTER `_filed` and only here (see `_filed`'s own docstring for why
        it is a second step rather than a growth of that shared function).
        Derived from `hub.status_for`/`hub._elapsed_ms`/`hub.action_for`,
        in-memory reads with no added disk I/O, so a page painting the whole
        list never has to poll `GET /api/chats/{cid}/status` once per
        visible row.
        """
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        query = (_req.query.get("q") or "").strip()
        try:
            belongs = _chat_scope(hub, _req.query.get("project"))
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=400)
        unseen = (await asyncio.to_thread(hub.automations.unseen_chats)
                  if hub.automations is not None else set())
        if belongs is None and not query:
            return web.json_response(
                {"chats": _with_status(hub, _with_agent(hub, _filed(
                    hub, brain.session_list(limit=CHATS_LISTED))), unseen)})
        rows = await asyncio.to_thread(brain.session_search, query,
                                       CHATS_LISTED, belongs)
        answer = {"chats": _with_status(
            hub, _with_agent(hub, _filed(hub, rows)), unseen)}
        if query:
            answer["q"] = query
        return web.json_response(answer)

    async def get_chat(req):
        """One past conversation: `{id, harness, project, projectId, ...}`.

        The same shape and the same bound as `GET /api/session/current`
        (AC-164), because the page renders a past conversation with the same
        code that renders the live one -- a second shape here would be a second
        renderer there. That is why the `_filed` annotation landed on BOTH in
        one edit: adding it to the live route alone would have made "the same
        shape" false the moment it shipped.

        An id nobody has is a 404 that NAMES it, never a 500 and never an
        empty 200: the page would draw an empty 200 as a conversation that
        exists and happens to be silent, and the owner would think their
        record was lost.

        The id never becomes a path component -- `ChatStore.find` matches it
        against the ids it just read off disk -- so nothing a caller sends
        can escape `state/chats/`.
        """
        chat_id = req.match_info.get("cid", "")
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        chat = brain.session_chat(chat_id, limit=MESSAGES_SERVED)
        if chat is None:
            return web.json_response(
                {"error": f"no conversation with id {chat_id!r}"}, status=404)
        doc = _with_choice(hub, brain,
                           _with_agent(hub, _filed(hub, [chat]))[0])
        # `questions` rides here too, and it is EMPTY for a past conversation
        # rather than absent (AC-249). "The same shape as
        # `GET /api/session/current`" is a claim this route makes in its own
        # first line, and a field on one of the two is how that claim stopped
        # being true the last time. `questions_for` answers from the one
        # registry, so this needs no second rule and becomes correct on its
        # own the day a second conversation can be live.
        doc["questions"] = hub.questions_for(doc.get("id") or "")
        return web.json_response(doc)

    async def get_chat_status(req):
        """One conversation's turn status: `{id, status, elapsed_ms}`, plus
        `phase`, `detail`, `chars`, `thought_chars` and `last_event_ms` while a
        turn runs (AC-236, `docs/parallel-agents.md` phase 7; AC-391).

        **THE RECOVERY ROUTE.** Nothing served the turn-start over HTTP
        before this: `hub.status_for` and `hub._elapsed_ms` derive both facts
        from state the daemon already holds, and a reloaded tab had no way to
        ask for either, so its own clock always restarted at zero even when a
        turn had been running for minutes. This is the first thing that reads
        them from outside the process.

        READ-ONLY, and stays that way -- a diagnostic that edits
        configuration is a defect this repo has already paid for. Calling it
        a thousand times changes nothing about the turn it describes.

        Same id rule as `get_chat`: an id nobody has is a 404 naming it,
        never an empty 200 a page would read as "this conversation exists
        and is merely idle". 404 in endpoint mode on `NO_CONVERSATION`, like
        every route in this family -- a brain with no session store cannot
        report a turn status for a conversation it cannot hold.
        """
        chat_id = req.match_info.get("cid", "")
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        if brain.session_chat(chat_id, limit=1) is None:
            return web.json_response(
                {"error": f"no conversation with id {chat_id!r}"}, status=404)
        # While a turn runs, the SAME evidence its heartbeat frame carries
        # (AC-391, `hub.progress_for`, one builder): a reloaded or second page
        # gets no frames, so this answer is its only view of the work.
        return web.json_response(
            {"id": chat_id, "status": hub.status_for(chat_id),
             "elapsed_ms": hub._elapsed_ms(chat_id),
             **hub.progress_for(chat_id)})

    async def delete_chat(req):
        """Throw one conversation away for good (owner, 2026-09-10).

        Answers the NEW LIST rather than `{ok: true}`, which is
        `delete_shortcut` and `delete_connection`'s shape and the most
        useful decision here: a page that splices its own local array can
        disagree with disk, and a page that repaints from this response
        cannot. `session` rides along on every 200, changed or not, so the
        page has ONE code path whether or not it just deleted the
        conversation Butler was holding.

        THE CODING AGENT GOES WITH IT. `session_delete` disposes the child
        this conversation was holding before it unlinks, because a deleted
        conversation has no next turn for AC-60's spec comparison to run in --
        it is the one place that rule cannot reach, and until 2026-09-12 the
        child stayed alive, kept its slot in `harness_agents_max`, and could
        not be opened or stopped by anything short of a restart.

        THE LIVE CONVERSATION MAY BE DELETED, and the store rotates before
        it unlinks (`ChatStore.delete`). Refusing was the obvious
        alternative and is the control-that-lies failure: the row is listed,
        it carries a remove button, and the conversation the owner most
        wants gone is usually the one they just had. "Press New chat first"
        is two steps for one intent.

        EFFECT-IDEMPOTENT, RESPONSE-HONEST: a second DELETE of the same id
        is a 404. `get_chat` already 404s on an unknown id precisely so the
        page cannot read an empty 200 as a conversation that exists and is
        silent, and a 200 here would tell a satellite it deleted something
        it did not -- which is how a two-device race becomes invisible. The
        page treats this 404 as benign and refreshes.

        KNOWN AND BOUNDED: nothing is broadcast, so a SECOND satellite keeps
        showing the row until it reloads or its socket reconnects, and
        clicking it there answers 404. `POST /api/session/new` has had
        exactly this staleness since it shipped. Closing it means a new
        outbound frame in protocol v1, which is a plan.md 4.3 change and not
        a route's to make quietly.

        404 in endpoint mode like every other route in this family: a daemon
        holding no conversation cannot delete one, and that is a different
        fact from a delete that failed.
        """
        chat_id = req.match_info.get("cid", "")
        brain = hub.session_brain()
        if brain is None:
            return web.json_response({"error": NO_CONVERSATION}, status=404)
        if not await brain.session_delete(chat_id):
            return web.json_response(
                {"error": f"no conversation with id {chat_id!r}"}, status=404)
        return web.json_response(
            {"deleted": chat_id,
             "chats": _filed(hub, brain.session_list(limit=CHATS_LISTED)),
             "session": brain.session_state().get("session", "")})

    app.router.add_get("/api/session/current", get_session)
    app.router.add_post("/api/session/new", post_session_new)
    app.router.add_post("/api/chats/{cid}/agent", post_chat_agent)   # AP-2
    app.router.add_post("/api/chats/{cid}/model", post_chat_model)   # CM-1
    app.router.add_get("/api/agents/{agent}/models", get_agent_models)
    app.router.add_post("/api/session/open", post_session_open)
    app.router.add_post("/api/session/rewind", post_session_rewind)
    app.router.add_get("/api/chats", get_chats)
    app.router.add_get("/api/chats/{cid}", get_chat)
    app.router.add_get("/api/chats/{cid}/status", get_chat_status)
    app.router.add_delete("/api/chats/{cid}", delete_chat)
    async def get_tts_preview(req):
        """Hear a voice before choosing it (AC-109). The work is in
        `tts_preview`, which is module-level so a broken engine can be put
        behind it in a test."""
        return await tts_preview(hub, req.query.get("voice", ""))

    app.router.add_get("/api/tts/preview", get_tts_preview)

    async def post_tts_say(req):
        """Say one message again (AC-183). The work is in `tts_say`, which is
        module-level for `tts_preview`'s reason: a route only reachable over
        HTTP can only be tested against engines that work."""
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            body = {}
        return await tts_say(hub, body)

    app.router.add_post("/api/tts/say", post_tts_say)

    async def take_ambient(req):
        """The satellite's ambient ring. The work is in `post_ambient`, which
        is module-level so the ceiling and the discard rules can be driven
        without a socket."""
        return await post_ambient(hub, req)

    app.router.add_post("/api/ambient", take_ambient)

    async def take_dictation(req):
        """The desk agent's held-key clip. The work is in `post_dictate`,
        which is module-level so the ceiling and the refusals can be driven
        without a socket."""
        return await post_dictate(hub, req)

    app.router.add_post("/api/dictate", take_dictation)

    async def take_attachment(req):
        """One file the owner is showing Butler. The work is in `post_attach`,
        which is module-level so the ceiling, the sniff and the refusals can be
        driven without a socket."""
        return await post_attach(hub, req)

    app.router.add_post("/api/attach", take_attachment)

    async def take_answer(req):
        """The human's answer to a coding agent's question (AC-234, AC-250).
        The work is in `post_answer`, which is module-level for `post_attach`'s
        reason: the refusals are most of the feature and they have to be
        drivable without a socket."""
        return await post_answer(hub, req)

    app.router.add_post("/api/answer", take_answer)

    async def take_terminal(req):
        """The desk agent's Ctrl-chord. The work is in `post_terminal`, which
        is module-level so the gate and the refusals can be driven without a
        socket."""
        return await post_terminal(hub, req)

    app.router.add_post("/api/terminal", take_terminal)

    async def read_shortcuts(req):
        """The pane. The work is module-level so the store's refusals can be
        driven without a socket, like every other route in this file."""
        return await get_shortcuts(hub, req)

    async def write_shortcuts(req):
        return await post_shortcuts(hub, req)

    async def drop_shortcut(req):
        return await delete_shortcut(hub, req)

    async def take_browse(req):
        return await post_browse(hub, req)

    async def take_project_stop(req):
        """The confirm dialog's "yes" button, and nothing else on the page
        (docs/filing-and-pacing.md 1.7, phase 5). The work is module-level so
        the refusals and the TOCTOU swallow can be driven without a socket,
        like every route here."""
        return await post_project_stop(hub, req)

    async def read_project_files(req):
        return await get_project_files(hub, req, preview=False)

    async def read_project_file(req):
        return await get_project_files(hub, req, preview=True)

    async def read_project_raw(req):
        return await get_project_raw(hub, req)

    async def write_project_upload(req):
        return await post_project_upload(hub, req)

    app.router.add_get("/api/shortcuts", read_shortcuts)
    app.router.add_post("/api/shortcuts", write_shortcuts)
    app.router.add_delete("/api/shortcuts/{sid}", drop_shortcut)
    app.router.add_post("/api/browse", take_browse)
    app.router.add_post("/api/project/{id}/stop", take_project_stop)
    app.router.add_get("/api/project/{id}/files", read_project_files)
    app.router.add_get("/api/project/{id}/file", read_project_file)
    app.router.add_get("/api/project/{id}/raw", read_project_raw)
    app.router.add_post("/api/project/{id}/upload", write_project_upload)
    async def memory_route(req, run):
        """The shared shape of the two memory routes (docs/identity-memory.md
        12.6, AC-433, AC-441).

        LOCAL ONLY (`hub.is_local`, AC-541). The caller is the pi extension
        on this machine; a
        note's text re-enters every future prompt, so this is the one route
        that does not take R2's tailnet posture. 400 for a refused or
        malformed call, with the reason, because the reason is what the
        agent needs to try again. The work is disk and SQLite, so it runs off
        the event loop (D-1).
        """
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "the memory routes answer this machine only"},
                status=403)
        try:
            body = await req.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "the body must be JSON"},
                                     status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "the body must be an object"},
                                     status=400)
        try:
            return web.json_response(await asyncio.to_thread(run, body))
        except memory_mod.MemoryRefused as e:
            return web.json_response({"error": str(e)}, status=400)

    def _search(body):
        hits = hub.memory.search(
            body.get("query"),
            body.get("limit", memory_mod.SEARCH_LIMIT_DEFAULT))
        return {"hits": hits, "text": memory_mod.render_hits(hits)}

    def _write(body):
        note, old = hub.memory.write(body.get("title"), body.get("text"),
                                     body.get("private", False))
        print(f"[butler] memory note written by an agent: {note.path}"
              f"{' superseding ' + str([o.id for o in old]) if old else ''}",
              flush=True)
        return {"id": note.id, "title": note.title,
                "superseded": [o.id for o in old],
                "text": memory_mod.render_written(note, old)}

    async def post_memory_search(req):
        return await memory_route(req, _search)

    async def post_task_agent(req):
        """`task_add` from pi's extension (docs/automations.md AU-9,
        AC-454): `{text, chat}`, LOOPBACK ONLY for the memory routes' reason
        -- a model's words become a row the owner reads as a to-do. The
        PROJECT is the named conversation's, resolved here; nothing in the
        body can choose it. The reply's cap counts per turn of THAT
        conversation (`_turn_started` is when its current turn began)."""
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "the task tool answers this machine only"},
                status=403)
        body, err = await _library_body(req)
        if err:
            return err
        chat = body.get("chat")
        brain = hub.session_brain()
        if not isinstance(chat, str) or not chat or brain is None:
            return web.json_response(
                {"error": "task_add needs the conversation it was called in"},
                status=400)
        record = await asyncio.to_thread(brain.session_chat, chat, 1)
        if record is None:
            return web.json_response(
                {"error": f"no conversation {chat!r}"}, status=404)
        row = hub.shortcuts.row_for(str(record.get("project") or "")) \
            if hub.shortcuts is not None else None
        project = row.id if row is not None else hub.library.root_id()
        # docs/lantern-ui.md section 8: the task names the harness the
        # RECORD says this conversation runs as; the body cannot name one.
        found, err = await _library_call(
            hub.library.agent_add, body.get("text"), project, chat,
            hub._turn_started.get(chat), why=body.get("why", ""),
            about=body.get("about"), agent=str(record.get("harness") or ""))
        if err:
            return err
        task, added = found
        if not added:
            return web.json_response({"id": task["id"],
                                      "text": library_mod.render_open(task)})
        print(f"[butler] task added by an agent in {chat}: {task['id']} "
              f"({task['project']}, about {task['about'] or '-'})",
              flush=True)
        await library_changed(hub, "tasks", task["project"])
        return web.json_response({"id": task["id"],
                                  "text": library_mod.render_added(task)})

    async def post_library_agent(req):
        """`reminder_add`, `schedule_propose`, `recipe_find` and
        `recipe_propose` from pi's extension (docs/proactive.md PR-20, PR-21;
        docs/recipe-tools.md AC-665): `{tool, args, chat}`, LOOPBACK
        ONLY, `post_task_agent`'s rule and reason. The project is the named
        conversation's; the work is `hub.library_tool`, the dispatcher the
        endpoint brain reaches too. A refusal is the model's to read, so it
        comes back as 200 text, as the endpoint brain is shown it."""
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "the library tools answer this machine only"},
                status=403)
        body, err = await _library_body(req)
        if err:
            return err
        tool, chat = body.get("tool"), body.get("chat")
        brain = hub.session_brain()
        if tool not in LIBRARY_AGENT_TOOLS or not isinstance(
                chat, str) or not chat or brain is None:
            return web.json_response(
                {"error": f"name the tool ({', '.join(LIBRARY_AGENT_TOOLS)}) "
                          f"and the conversation it was called in"},
                status=400)
        record = await asyncio.to_thread(brain.session_chat, chat, 1)
        if record is None:
            return web.json_response(
                {"error": f"no conversation {chat!r}"}, status=404)
        row = hub.shortcuts.row_for(str(record.get("project") or ""))             if hub.shortcuts is not None else None
        project = row.id if row is not None else hub.library.root_id()
        text = await hub.library_tool(tool, body.get("args"), project, chat,
                                      hub._turn_started.get(chat))
        return web.json_response({"text": text})

    async def post_memory_write(req):
        return await memory_route(req, _write)

    async def post_artifact_show(req):
        """`show_file` and `show_ui` from pi's extension (docs/rich-chat.md
        RC-1, RC-2): `{tool, args, chat}`, LOOPBACK ONLY (`is_local`). The
        file rule is `containment.file_inside` against the named
        conversation's project and the data root -- the WhatsApp send's own
        guard (RC-11, AC-615). A refusal is the model's to read, so it comes
        back as 200 text (`post_library_agent`'s rule)."""
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "the show tools answer this machine only"},
                status=403)
        try:
            body = await req.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "the body must be JSON"},
                                     status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "the body must be an object"},
                                     status=400)
        tool, chat, args = body.get("tool"), body.get("chat"), body.get("args")
        if tool not in artifacts_mod.TOOL_NAMES or not isinstance(
                chat, str) or not chat:
            return web.json_response(
                {"error": "name the tool (show_file or show_ui) and the "
                          "conversation it was called in"}, status=400)
        brain = hub.session_brain()
        if brain is None or hub.artifacts is None:
            return web.json_response(
                {"error": "this brain mode keeps no conversation record to "
                          "show anything in"}, status=409)
        record = await asyncio.to_thread(brain.session_chat, chat, 1)
        if record is None:
            return web.json_response(
                {"error": f"no conversation {chat!r}"}, status=404)
        entry, refusal = await show_tool(tool, args, chat, record)
        if refusal:
            return web.json_response({"text": f"NOT SHOWN. {refusal}"})
        await hub.show_artifact(chat, "assistant", entry)
        return web.json_response({"text": artifacts_mod.shown_line(entry),
                                  "artifact": entry})

    async def show_tool(tool, args, chat, record):
        """(entry, None) or (None, the reason a model is told)."""
        if not isinstance(args, dict):
            return None, "The arguments must be a JSON object."
        if tool == artifacts_mod.SHOW_UI:
            try:
                layout = artifacts_mod.validate_ui(args)
                return await asyncio.to_thread(
                    hub.artifacts.put_ui, chat, layout), None
            except artifacts_mod.Refused as e:
                return None, f"{e}. Fix the layout and call show_ui again."
        path, caption = args.get("path"), args.get("caption")
        if not isinstance(path, str) or not path.strip():
            return None, "path must be the file to show."
        if caption is not None and not isinstance(caption, str):
            return None, "caption must be text."
        where = paths_mod.resolve()
        roots = [where.data]
        if record.get("project"):
            roots.append(record["project"])
        # A relative path is the agent's own: its working folder is the
        # conversation's project (`_session_cwd`), else the data root.
        base = Path(record.get("project") or where.data)
        try:
            real = containment_mod.file_inside(
                base / path, roots, containment_mod.records_dirs(where))
            entry = await asyncio.to_thread(
                hub.artifacts.snapshot, chat, real, caption)
        except containment_mod.FileRefused as e:
            print(f"[butler] show_file refused in {chat}: {e}", flush=True)
            return None, f"{e}."
        except artifacts_mod.Refused as e:
            return None, f"{e}."
        except OSError as e:
            return None, f"Could not read {path}: {e.strerror or e}."
        return entry, None

    async def post_guide_agent(req):
        """`butler_help` from pi's extension (docs/butler-help.md BH-2,
        AC-650): `{topic?}`, LOOPBACK ONLY, answered as text."""
        if not hub_mod.is_local(req):
            return web.json_response(
                {"error": "butler_help answers this machine only"},
                status=403)
        try:
            body = await req.json() if req.can_read_body else {}
        except json.JSONDecodeError:
            return web.json_response({"error": "the body must be JSON"},
                                     status=400)
        topic = body.get("topic") if isinstance(body, dict) else None
        state = await asyncio.to_thread(guide_state, hub)
        return web.json_response({"text": guide_mod.help_text(
            topic if isinstance(topic, str) else None, state)})

    async def get_artifact(req):
        """`GET /api/artifacts/{cid}/{aid}` -- what was shown, as it was
        when shown (RC-3). Open to the tailnet like `/api/chats` (R2, owner
        2026-10-08 item 3). Both ids are KEYS matched against what is listed
        on disk, never joined onto a path (AC-631); an unknown one is 404
        and says nothing about which half."""
        if hub.artifacts is None:
            return web.json_response({"error": "no such artifact"},
                                     status=404)
        extra = sorted(set(req.query) - {"download", "preview"})
        if extra:
            return web.json_response(
                {"error": f"the parameters are download and preview; got "
                          f"{extra}"}, status=400)
        found = await asyncio.to_thread(
            hub.artifacts.find, req.match_info["cid"], req.match_info["aid"])
        if found is None:
            return web.json_response({"error": "no such artifact"},
                                     status=404)
        path, entry = found
        return serve_shown(path, entry["kind"], entry["media"], entry["name"],
                           download=req.query.get("download") == "1",
                           preview=req.query.get("preview") == "1")

    app.router.add_post("/api/memory/search", post_memory_search)
    app.router.add_post("/api/memory/write", post_memory_write)
    app.router.add_post("/api/tasks/agent", post_task_agent)
    app.router.add_post("/api/library/agent", post_library_agent)
    app.router.add_post("/api/artifacts/show", post_artifact_show)
    app.router.add_post("/api/guide/agent", post_guide_agent)
    app.router.add_get("/api/artifacts/{cid}/{aid}", get_artifact)
    # Recipes and tasks (docs/automations.md 5). Module-level bodies, like
    # the shortcuts routes, so a test can drive them without a socket.
    for method, route, handler in (
            ("GET", "/api/library", get_library),
            ("GET", "/api/recipes", get_recipes),
            ("POST", "/api/recipes", post_recipe),
            ("DELETE", "/api/recipes/{rid}", delete_recipe),
            ("POST", "/api/recipes/{rid}/expand", post_recipe_expand),
            ("GET", "/api/tasks", get_tasks),
            ("POST", "/api/tasks", post_task),
            ("DELETE", "/api/tasks/{tid}", delete_task),
            ("GET", "/api/automations", get_automations),
            ("POST", "/api/automations", post_automation),
            ("POST", "/api/automations/bulk", post_automations_bulk),
            ("DELETE", "/api/automations/{aid}", delete_automation),
            ("POST", "/api/automations/{aid}/run", post_automation_run),
            ("GET", "/api/automations/{aid}/runs", get_automation_runs),
            ("POST", "/api/schedule/preview", post_schedule_preview),
            ("POST", "/api/heartbeat", post_heartbeat),
            ("GET", "/api/prechecks", get_prechecks)):
        app.router.add_route(method, route, _with_hub(hub, handler))
    app.router.add_get("/api/models", get_models)
    app.router.add_get("/api/mode", get_mode)
    app.router.add_post("/api/mode", post_mode)
    app.router.add_post("/api/restart", post_restart)
    app.router.add_post("/api/shutdown", post_shutdown)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", post_settings)
    app.router.add_post("/api/settings/reset", post_settings_reset)
    phonelink_mod.add_routes(app, hub)    # DA-15, DA-16 (AC-553, AC-554)
    # Notification channels (docs/automations.md 7, phase 7a): one file per
    # bot under the records; `notify.py` owns the store and its four routes,
    # and adding the first channel announces `notify_default` like any write.
    if hub.channels is not None:
        notify_mod.add_routes(app, hub.channels, hub.settings,
                              _announce_settings)
    # Trigger sources (docs/proactive.md PR-26, PR-27): the settings group's
    # collection, and `/api/hooks/{name}` on EVERY listener -- other
    # programs call it, and the source's token is its guard (AC-498).
    triggers = getattr(hub, "triggers", None)
    if triggers is not None:
        triggers_mod.add_routes(app, triggers)

    # WhatsApp (docs/messaging.md MS-2..MS-6, phase M2): the sidecar owner,
    # started by `_on_start` only when `whatsapp_enabled` is on, and the
    # pairing routes the settings group names (`Group.pairing`, AC-503).
    # Records under `Paths.whatsapp`, never the repo (AC-501). A hub with
    # no settings (a test shape, like `hub.channels is None` above) has no
    # switch to read, so it gets no WhatsApp and no pairing routes.
    whatsapp = (whatsapp_mod.WhatsApp(hub.settings,
                                      paths_mod.resolve().whatsapp)
                if hub.settings is not None else None)
    hub.whatsapp = whatsapp
    # The user's own chat is a channel into their conversation (M6/M7,
    # docs/messaging.md MS-16..MS-19): owner messages become turns.
    if whatsapp is not None:
        whatsapp.inbound = whatsapp_mod.Inbound(
            hub, whatsapp, queue_folder=paths_mod.resolve().phone_queue)
    # Two-way Telegram (docs/messaging.md section 8, T1..T3): the reader of
    # the ONE channel file with `inbound` on, whose owner messages are phone
    # turns like WhatsApp's. Started by `_on_start`; it reads nothing until a
    # channel says inbound. Needs the channel folder and the settings.
    telegram = (telegram_inbound(hub, paths_mod.resolve().phone_queue)
                if hub.channels is not None and hub.settings is not None
                else None)
    hub.telegram = telegram

    async def whatsapp_pairing(req):
        """GET reads the link's state; POST starts linking (idempotent in
        the sidecar, AC-504); DELETE disconnects and keeps the link, or with
        `unlink=true` logs out and deletes the session -- only with
        `confirm=true` in the query or `{"confirm": true}` in the body, 409
        otherwise, because an unlink cannot be undone without the phone.

        Open to the tailnet like every settings route (R2): the satellite
        page on the owner's phone is where they link and unlink, and an
        unlink costs a re-scan, not a message sent in their name (the send
        door, MS-10, is the loopback-only one). 503 with `poll` when there
        is no sidecar to ask: off, starting, or given up.
        """
        method = req.method
        path = "/pairing"
        if method == "DELETE":
            body = {}
            if req.can_read_body:
                try:
                    body = await req.json()
                except Exception:                     # noqa: BLE001
                    return web.json_response(
                        {"error": "body must be json"}, status=400)
                if not isinstance(body, dict):
                    return web.json_response(
                        {"error": "body must be a json object"}, status=400)
            unlink = (req.query.get("unlink") == "true"
                      or body.get("unlink") is True)
            confirmed = (req.query.get("confirm") == "true"
                         or body.get("confirm") is True)
            if unlink and not confirmed:
                return web.json_response(
                    {"error": "unlinking logs out and deletes the saved "
                              "link; send confirm=true to do it"},
                    status=409)
            if unlink:
                path = "/pairing?unlink=true"
        try:
            status, answer = await whatsapp.request(method, path)
        except whatsapp_mod.Unavailable as e:
            return web.json_response(
                {"ok": False, "error": e.reason, "poll": e.poll,
                 "actions": []}, status=503)
        if status != 200:
            return web.json_response(
                {"ok": False, "error": f"the WhatsApp service answered "
                                       f"{status}: {answer.get('error')}",
                 "poll": False, "actions": []}, status=502)
        try:
            return web.json_response(whatsapp_mod.page_doc(answer))
        except whatsapp_mod.SidecarError as e:
            return web.json_response(
                {"ok": False, "error": str(e), "poll": False,
                 "actions": []}, status=502)

    if whatsapp is not None:
        for method in ("GET", "POST", "DELETE"):
            app.router.add_route(method, "/api/whatsapp/pairing",
                                 whatsapp_pairing)
        _whatsapp_agent_routes(app, hub, whatsapp)
    if WEB_DIR.exists():
        app.router.add_get("/", index)
        app.router.add_static("/", WEB_DIR, show_index=False)

    async def _on_start(_app):
        await warmup(hub)
        # After the warm-up, never before: the agent's first act is to read
        # settings off this daemon, and a boot that is still loading models
        # answers it slowly for no benefit. Spawning is off the loop because
        # it shells out (D-25), and its failure is loud but never fatal --
        # hotkeys are worth less than the microphone (D-1, AC-121, AC-122).
        if local_url:
            await asyncio.to_thread(desk_mod.start_if_enabled,
                                    hub.settings, local_url)
        # The automations scheduler (docs/automations.md AU-6): after the
        # warm-up, so a run missed while Butler was off is caught up by the
        # FIRST tick against a brain that is ready to answer it (AC-459).
        if hub.scheduler is not None:
            await hub.scheduler.start()
        # After the warm-up too: a setting changed from here on is put into
        # effect with no owner restart (AC-713..AC-716).
        if hub.applier is not None:
            hub.applier.start()
        # PR-30: the trigger pollers, beside the scheduler they feed.
        if triggers is not None:
            await triggers.start()
        # Spawns nothing unless `whatsapp_enabled` is on (MS-2); listens for
        # the switch either way.
        if whatsapp is not None:
            await whatsapp.start()
        if telegram is not None:
            notify_telegram.serve(telegram)
            await telegram.open()

    async def _on_shutdown(_app):
        # aiohttp waits for every open handler before it stops, and a page's
        # WebSocket never ends on its own: one connected page held a
        # `POST /api/shutdown` for 31.9 s [MEASURED 2026-10-07], past the
        # desktop app's 15 s kill (DA-11). Close them; the page reconnects.
        for s in list(hub.sessions):
            if not s.headless:
                await s.ws.close(code=WSCloseCode.GOING_AWAY,
                                 message=b"Butler is stopping")

    async def _on_cleanup(_app):
        if triggers is not None:
            await triggers.stop()          # before what it fires into
        if hub.scheduler is not None:
            await hub.scheduler.stop()
        # The question channels' in-memory hooks (MS-20): before WhatsApp,
        # which they listen through.
        if getattr(hub, "phone_questions", None) is not None:
            await hub.phone_questions.stop()
        if whatsapp is not None:
            whatsapp_mod.withdraw(whatsapp)
            await whatsapp.stop()
        if telegram is not None:
            notify_telegram.withdraw(telegram)
            await telegram.close()
    app.on_startup.append(_on_start)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
    return app


# --------------------------------------------------------------------------- #
# WhatsApp: the agent's read routes, the send door, the link status
# (docs/messaging.md MS-8, MS-10, MS-13, MS-18b; phases M4, M5)
# --------------------------------------------------------------------------- #
def telegram_inbound(hub, queue_folder: Path,
                     **timing) -> notify_telegram.Inbound:
    """The Telegram reader with its phone turns (docs/messaging.md MS-25,
    AC-533): the same `phone.PhoneTurns` WhatsApp's owner messages take,
    with Telegram's kind, message size and file cap, persisted under
    `queue_folder` (`Paths.phone_queue`, RV-13). `timing` is the reader's
    clocks, for tests."""
    reader = notify_telegram.Inbound(hub.channels, **timing)
    reader.turns = phone_mod.PhoneTurns(
        hub, reader, kind=notify_telegram.KIND,
        text_limit=notify_telegram.TEXT_LIMIT,
        max_file_mb=notify_telegram.MAX_FILE_MB, queue_folder=queue_folder)
    return reader


class _Refused(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status, self.error = status, error


async def _whatsapp_send_body(req) -> tuple[dict, tuple[str, bytes] | None]:
    """The two body shapes of the send door (MS-10): JSON, or multipart with
    EXACTLY ONE file in `files` (100 MB, refused while reading, 413) plus
    string fields. Values pass through as given; the sidecar judges them."""
    if not req.content_type.startswith("multipart/"):
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            raise _Refused(400, "the body must be JSON or multipart")
        if not isinstance(body, dict):
            raise _Refused(400, "the body must be a JSON object")
        return dict(body), None
    fields: dict = {}
    upload = None
    reader = await req.multipart()
    while (part := await reader.next()) is not None:
        if part.filename is None:
            raw = await part.read()
            if len(raw) > 1024 * 1024:
                raise _Refused(413, f"field {part.name} is too large")
            fields[part.name] = raw.decode("utf-8")
            continue
        if part.name != "files":
            raise _Refused(400, "the file goes in the field named files")
        if upload is not None:
            raise _Refused(400, "exactly one file per send")
        data = bytearray()
        while chunk := await part.read_chunk():
            data += chunk
            if len(data) > whatsapp_mod.MAX_FILE_BYTES:
                raise _Refused(413, "file is over the upload limit")
        upload = (part.filename, bytes(data))
    return fields, upload


def _whatsapp_agent_routes(app, hub, whatsapp) -> None:
    """LOOPBACK ONLY, every one (`hub.is_local`, AC-511, AC-541): the archive
    is the user's private correspondence and a send goes out in their name,
    so none of these takes R2's tailnet posture.

    POST /api/whatsapp/read/{tool}  one read tool (`whatsapp_mod.READ_NAMES`)
        over the archive opened read-only; 403 while `whatsapp_read` is off,
        400 for arguments the tool refuses (AC-508, AC-509).
    POST /api/whatsapp/send  the ONE send door (MS-10): forwarded to the
        sidecar's guard through `WhatsApp.send`, the send switch from the
        setting, its status and body passed through unchanged plus `text`,
        what a model is told. A tool's file carries `source` (the path it
        READ, after realpath) and `chat`: checked against that
        conversation's project and the data root (AC-562) and never opened.
    GET  /api/whatsapp/status  the sidecar's link state and counts.
    """
    def local_only():
        return web.json_response(
            {"ok": False, "reason": "not_local",
             "error": "the WhatsApp agent routes answer this machine only"},
            status=403)

    async def read(req):
        if not hub_mod.is_local(req):
            return local_only()
        name = req.match_info["tool"]
        if name not in whatsapp_mod.READ_NAMES:
            return web.json_response({"error": f"no tool {name!r}"},
                                     status=404)
        if not whatsapp_mod.read_on(hub.settings):
            return web.json_response(
                {"error": "WhatsApp reading is off (Butler may read, on the "
                          "settings page)"}, status=403)
        try:
            body = await req.json() if req.can_read_body else {}
        except json.JSONDecodeError:
            return web.json_response({"error": "the body must be JSON"},
                                     status=400)
        try:
            answer = await asyncio.to_thread(
                whatsapp_mod.read_tool,
                paths_mod.resolve().whatsapp / "whatsapp.db", name, body)
        except whatsapp_mod.ToolRefused as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(answer)

    async def roots_for(chat) -> list:
        """Where a tool's file may come from: the data root, plus the
        named conversation's project folder."""
        roots = [paths_mod.resolve().data]
        if chat is None:
            return roots
        brain = hub.session_brain()
        record = (await asyncio.to_thread(brain.session_chat, chat, 1)
                  if brain is not None and isinstance(chat, str) else None)
        if record is None:
            raise _Refused(404, f"no conversation {chat!r}")
        if record.get("project"):
            roots.append(record["project"])
        return roots

    async def send(req):
        if not hub_mod.is_local(req):
            return local_only()
        try:
            fields, upload = await _whatsapp_send_body(req)
            source = fields.pop("source", None)
            chat = fields.pop("chat", None)
            # A TOOL caller names its conversation (`chat`) or its file's
            # real path (`source`); tools/wa.py, a human at a shell, names
            # neither and keeps its own `confirm` (RV-2, AC-588).
            tool = source is not None or chat is not None
            if source is not None and (upload is None
                                       or not isinstance(source, str)):
                raise _Refused(400, "source describes a tool's file and "
                                    "needs the file")
            if upload is not None and tool:
                if not isinstance(source, str):
                    raise _Refused(400, "a tool's file needs its source")
                try:
                    containment_mod.file_inside(
                        source, await roots_for(chat),
                        containment_mod.records_dirs(paths_mod.resolve()))
                except containment_mod.FileRefused as e:
                    inside = isinstance(e, containment_mod.RecordsRefused)
                    rule = (containment_mod.RECORDS_RULE if inside
                            else containment_mod.FILE_RULE)
                    print(f"[butler] whatsapp: a tool's file was refused "
                          f"({rule})", flush=True)
                    return web.json_response(
                        {"ok": False, "reason": "file_in_records" if inside
                         else "file_outside_roots",
                         "error": str(e),
                         "text": f"NOT SENT. {e}."}, status=403)
        except _Refused as e:
            return web.json_response({"ok": False, "error": e.error},
                                     status=e.status)
        try:
            if tool:
                async def ask(message):
                    if not isinstance(chat, str) or not chat:
                        raise LookupError("no conversation was named to ask "
                                          "the user in")
                    return await hub.ask_owner(
                        chat, whatsapp_mod.CONFIRM_TITLE, message)
                status, answer = await whatsapp_mod.tool_send(
                    whatsapp, fields, upload, ask)
            else:
                status, answer = await whatsapp.send(fields, upload)
        except whatsapp_mod.Unavailable as e:
            return web.json_response(
                {"ok": False, "error": e.reason, "poll": e.poll,
                 "text": f"NOT SENT. {e.reason}"}, status=503)
        except whatsapp_mod.SendUncertain as e:
            return web.json_response(
                {"ok": False, "reason": "send_uncertain", "error": str(e),
                 "text": f"UNKNOWN: {e}"}, status=504)
        recipient = str(fields.get("recipient") or "")
        return web.json_response(
            {**answer, "text": whatsapp_mod.render_send(status, answer,
                                                        recipient)},
            status=status)

    async def status(req):
        if not hub_mod.is_local(req):
            return local_only()
        try:
            code, answer = await whatsapp.request("GET", "/status")
        except whatsapp_mod.Unavailable as e:
            return web.json_response(
                {"ok": False, "error": e.reason, "poll": e.poll}, status=503)
        return web.json_response(answer, status=code)

    whatsapp_mod.serve(whatsapp)
    app.router.add_post("/api/whatsapp/read/{tool}", read)
    app.router.add_post("/api/whatsapp/send", send)
    app.router.add_get("/api/whatsapp/status", status)


def cert_paths() -> tuple[Path, Path]:
    """Where the TLS certificate and key are looked for: the one spelling
    `ssl_context` and the `lan_access` write share."""
    ssl_dir = paths_mod.resolve().ssl
    return (Path(os.environ.get("BUTLER_TLS_CERT") or (ssl_dir / "cert.pem")),
            Path(os.environ.get("BUTLER_TLS_KEY") or (ssl_dir / "key.pem")))


def ssl_context(mode: bool | None) -> ssl.SSLContext | None:
    """A TLS context, or None for plain HTTP. `mode` is on / off / auto.

    TLS is the DEFAULT: the microphone is the whole point, and a browser
    refuses `getUserMedia` on any non-localhost origin that is not HTTPS, so a
    phone or tablet on the LAN can only work over TLS. `mode is None` (the
    default) serves TLS whenever a cert is present and plain HTTP otherwise --
    so a fresh clone with no cert still boots, and this machine, which has one
    (scripts/make-cert.py), serves https/wss with no flag. `mode is True`
    (`--tls`) DEMANDS it and fails fast if the cert is missing, because serving
    HTTP that a phone will then silently deny the mic on is the worse failure.
    `mode is False` (`--no-tls`, and what the test suite forces) is plain HTTP.
    """
    if mode is False:
        return None
    cert, key = cert_paths()
    if not cert.exists() or not key.exists():
        if mode is True:
            raise SystemExit(
                f"[butler] --tls needs a certificate and none was found at "
                f"{cert} and {key}. Generate one with:  python -X utf8 "
                f"scripts\\make-cert.py")
        return None                          # auto + no cert: plain HTTP
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


def bind_host(arg_host: str | None, settings_path: Path) -> str:
    """MAIN's bind address (docs/remote-access.md RA-7, AC-730). An explicit
    `--host` wins; otherwise `lan_access` in the settings file decides, read
    BEFORE binding: true is every interface, false (the default) loopback."""
    if arg_host is not None:
        return arg_host
    lan = settings_mod.Settings(settings_path).get("lan_access")
    return "0.0.0.0" if lan else "127.0.0.1"


def require_tls_off_loopback(host: str, ctx: ssl.SSLContext | None) -> None:
    """A bind beyond loopback is served over TLS or not at all (RA-7)."""
    if ctx is None and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"[butler] binding {host} exposes Butler to the network and needs "
            f"TLS, but no certificate is available (or --no-tls was given). "
            f"Generate one with:  python -X utf8 scripts\\make-cert.py")


def listen_socket(host: str, port: int) -> socket.socket:
    """A bound TCP socket for one listener. Bound HERE rather than by aiohttp
    so its real port (0 asks the OS) is known before the first request, and
    the LISTENERS map is complete before anything can arrive (AC-541)."""
    family, kind, proto, _, addr = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0]
    sock = socket.socket(family, kind, proto)
    # POSIX only: the in-page Restart re-execs IN PLACE and binds the same
    # port while the connections the old image closed sit in TIME_WAIT; without
    # this the bind is EADDRINUSE and the daemon stays down (macOS, 2026-10-27).
    # asyncio's own servers set it for the same reason. NEVER on Windows, where
    # SO_REUSEADDR lets another process take a port already in use.
    if os.name == "posix":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(addr)
    return sock


def listener_sockets(host: str, port: int, local_port: int,
                     proxy_port: int) -> dict[str, socket.socket]:
    """MAIN on `host:port` as today; LOCAL and PROXIED on 127.0.0.1 ONLY,
    each dropped when its port is 0 (docs/desktop-app.md DA-5, AC-540).

    Trust is looked up BY PORT (`hub.listener_of`), so two listeners on one
    port are refused rather than left for Windows to split between them.
    """
    wanted = {hub_mod.MAIN: (host, port),
              hub_mod.LOCAL: ("127.0.0.1", local_port),
              hub_mod.PROXIED: ("127.0.0.1", proxy_port)}
    wanted = {k: v for k, v in wanted.items() if k == hub_mod.MAIN or v[1]}
    fixed = [p for _, p in wanted.values() if p]
    if len(set(fixed)) != len(fixed):
        raise SystemExit(f"[butler] two listeners on one port: {wanted}")
    return {k: listen_socket(h, p) for k, (h, p) in wanted.items()}


async def serve(app: web.Application, ctx: ssl.SSLContext | None,
                socks: dict[str, socket.socket]) -> web.AppRunner:
    """Start `app` on every socket in `socks` and return the runner.

    The port -> listener map goes into the app BEFORE `setup()` (which runs
    `on_startup`, the warm-up) and before any site accepts. TLS is MAIN's
    only: LOCAL and PROXIED are plain HTTP on loopback, where a browser
    already treats the origin as secure (DA-1) and `tailscale serve` brings
    its own certificate (DA-2).
    """
    ports = {s.getsockname()[1]: kind for kind, s in socks.items()}
    if len(ports) != len(socks):
        raise SystemExit(f"[butler] two listeners on one port: {ports}")
    app[hub_mod.LISTENERS] = ports
    runner = web.AppRunner(app)
    await runner.setup()
    for kind, sock in socks.items():
        await web.SockSite(runner, sock, ssl_context=(
            ctx if kind == hub_mod.MAIN else None)).start()
    return runner


async def serve_forever(app: web.Application, ctx: ssl.SSLContext | None,
                        socks: dict[str, socket.socket]) -> None:
    """`serve`, then wait for `POST /api/shutdown` (or Ctrl+C, which
    `asyncio.run` turns into a cancellation) and clean up either way."""
    runner = await serve(app, ctx, socks)
    try:
        await app[SHUTDOWN].wait()
    finally:
        await runner.cleanup()


def main() -> None:
    # Line-buffer stdout. Python block-buffers it whenever it is not a
    # terminal, so running the daemon under a service manager, a pipe, or a
    # log file swallows everything it prints until the buffer fills or the
    # process exits. That silently disables the wake-candidate log that open
    # question 8.3 depends on, and makes "why did it do that" undiagnosable
    # in exactly the situation where the daemon runs unattended.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    # The daemon runs Parakeet / transcribe.cpp IN-PROCESS: opt it out of
    # Windows power throttling so the E-cores never get its speech work
    # (engines/winqos.py; loud on failure, never fatal).
    sys.path.insert(0, str(REPO / "daemon" / "engines"))
    import winqos  # noqa: PLC0415
    winqos.apply("butlerd")

    ap = argparse.ArgumentParser(prog="butlerd")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("BUTLER_PORT", DEFAULT_PORT)))
    # Unset: `lan_access` decides (RA-7, AC-730); a given value wins.
    ap.add_argument("--host", default=None)
    # The desktop app's LOCAL listener and `tailscale serve`'s PROXIED one
    # (docs/desktop-app.md DA-5): plain HTTP on 127.0.0.1, 0 = off. OFF unless
    # asked: `scripts/launch.py` passes 8141/8142, and every test or scratch
    # daemon that boots this file bare must not race the live one for them.
    ap.add_argument("--local-port", type=int, default=0)
    ap.add_argument("--proxy-port", type=int, default=0)
    # TLS is the default when a cert exists (LAN satellites need a secure
    # context for the mic). --tls demands it, --no-tls forces plain HTTP, and
    # BUTLER_TLS=0/1 is the same override for a service manager; a flag wins
    # over the env, and neither means auto.
    ap.add_argument("--tls", dest="tls", action="store_true",
                    help="require https/wss (fail if ssl/cert.pem is missing)")
    ap.add_argument("--no-tls", dest="tls", action="store_false",
                    help="force plain http even if a cert is present")
    ap.set_defaults(tls=None)
    # The data root (docs/data-root.md). A flag wins over BUTLER_DATA, and it
    # is written INTO the environment so every later `paths.resolve()` -- and
    # every child, the desk agent included -- sees the same answer.
    ap.add_argument("--data", default=None,
                    help="the data root: the root project's home, with "
                         "Butler's records in its .butler folder")
    args = ap.parse_args()
    if args.data:
        os.environ["BUTLER_DATA"] = args.data
    try:
        where = paths_mod.resolve()
    except paths_mod.DataRootError as e:
        raise SystemExit(f"[butler] {e}") from e

    tls = args.tls
    if tls is None:
        tls = {"1": True, "0": False}.get(os.environ.get("BUTLER_TLS"))

    where.records.mkdir(parents=True, exist_ok=True)
    print(f"[butler] code {where.code}; data root {where.data}; records "
          f"{where.records}", flush=True)
    ctx = ssl_context(tls)
    host = bind_host(args.host, where.records / "settings.json")
    require_tls_off_loopback(host, ctx)
    scheme = "https" if ctx else "http"
    # Loopback, never `args.host`: the agent runs on THIS machine by
    # definition, and 0.0.0.0 is a bind address rather than somewhere to
    # connect. MAIN's BOUND port, so `--port 0` names the real one.
    socks = listener_sockets(host, args.port, args.local_port,
                             args.proxy_port)
    local = f"{scheme}://127.0.0.1:{socks[hub_mod.MAIN].getsockname()[1]}"
    # Where a coding agent reaches the memory tools (docs/identity-memory.md
    # 12.6): written INTO the environment like BUTLER_DATA, because only this
    # function knows the scheme and port and `build_hub` reads it.
    os.environ["BUTLER_SELF_URL"] = local
    app = make_app(load_config(), local)
    for kind, sock in socks.items():
        host, port = sock.getsockname()[:2]
        print(f"[butler] listening ({kind}) on "
              f"{scheme if kind == hub_mod.MAIN else 'http'}://{host}:{port}",
              flush=True)
    asyncio.run(serve_forever(app, ctx, socks))


if __name__ == "__main__":
    main()
