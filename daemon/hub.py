"""Satellite registry, protocol v1, and the per-session state machine.

plan.md 4.3. One WebSocket per satellite; text frames are JSON, binary
frames are raw PCM (16 kHz mono int16 upstream, 24 kHz mono int16 down --
direction disambiguates).

Two rules here are load-bearing and everything else is plumbing (there used
to be a third -- the spoken write grant -- and the owner retired it on
2026-08-18: the harness runs at full capability and nothing is repeated back
or parked, AC-29):

1. The DAEMON mints turn ids. The spike let the client stamp them, which is
   fine for one page and indefensible for N satellites -- a device could
   name a turn it does not own. Every outbound message is filtered through
   `send_for_turn`, so a late message for an abandoned turn is dropped at
   the wire rather than trusted to be ignored on the far side (D-6).

2. Every terminal path re-arms the turn it ENDED. Normal reply, empty
   utterance, malformed frame, source lost, exception: all of them end at
   `_rearm(turn)`. The inherited Phoenix defect was a sticky error latching
   the microphone off until a restart, and the only reliable cure is having
   exactly one exit (D-1, AC-2). That exit NAMES its turn, because a
   terminal path reaches it seconds after its own ownership check and would
   otherwise re-arm whatever turn had started meanwhile (D-6, D-39).
"""
from __future__ import annotations

import asyncio
import collections
import ipaddress
import json
import time
import typing
from dataclasses import dataclass, replace

from aiohttp import WSMsgType, web
from yarl import URL

from . import arbiter as arb
from . import dialogue
from . import endpointing
from . import library as library_mod
from . import memory as memory_mod
from . import notify as notify_mod
from . import settings as settings_mod
from . import trace
from . import speech
from . import ports
from .ports import (Answer, AskConfirm, AskHuman, AsrConfig, Clarify,
                    Dispatch, Done, Say, Steer, TurnContext, WorkerEvent)
from .settings import AUTO_LANGUAGE

PROTO = 1

# How long past the audio we sent we will wait for the satellite to say it
# finished playing. Covers the client's queue depth and jitter; generous,
# because the cost of waiting slightly too long is a beat of silence, and
# the cost of not waiting is the device transcribing its own reply.
PLAYBACK_GRACE_S = 2.0

# What "this machine" is: aiohttp's `request.remote` and a URL's hostname both
# spell loopback one of these ways. Read ONLY by `_is_local` (and by
# `build_hub`'s check that BUTLER_SELF_URL names this machine, a configured
# URL rather than a request); a source test holds that (AC-541).
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

# THE LISTENERS (docs/desktop-app.md DA-5, AC-540). One app answers on up to
# three sockets: MAIN is today's (`--host`/`--port`, TLS), LOCAL and PROXIED
# are plain HTTP on 127.0.0.1. `tailscale serve` forwards every phone onto
# PROXIED FROM 127.0.0.1, so a peer address cannot say who is local there.
MAIN, LOCAL, PROXIED = "main", "local", "proxied"
# port -> listener kind, written by `butlerd.serve` BEFORE the app starts. An
# app served without it (aiohttp's TestServer) has one listener, MAIN.
LISTENERS = web.AppKey("butler.listeners", dict)
#: The paired device's id on a request the gate let through as a DEVICE
#: (`daemon/access.py`, RA-10): a socket remembers it so removing the
#: device closes the socket.
PAIRED = web.RequestKey("butler.paired", object)


def _is_local(listener: str, peer: str) -> bool:
    """THE trust decision (DA-6, AC-541): is this caller on this machine?

    True on LOCAL whatever the peer; on MAIN only from a loopback peer
    (today's rule); NEVER on PROXIED, whose peer is always loopback because
    the proxy is. An unknown listener is a KeyError, never a guess.
    """
    return {LOCAL: True, MAIN: peer in LOOPBACK, PROXIED: False}[listener]


def listener_of(request: web.Request) -> str:
    """Which listener `request` arrived on, read off the ACCEPTING socket
    (`sockname`), never a header: a header is whatever the caller sent."""
    ports = request.app.get(LISTENERS)
    if ports is None:
        return MAIN
    return ports[request.transport.get_extra_info("sockname")[1]]


#: RV-1 (docs/messaging.md 6.5, AC-586): every local route ALSO needs this
#: header with the value "1". A web page cannot add a custom header to a
#: cross-site request without a CORS preflight, which Butler never answers,
#: so a page in the owner's browser cannot reach a local route; Butler's own
#: callers (pi extension, tools/wa.py, the desktop app, the settings page)
#: send it.
LOCAL_HEADER = "X-Butler-Local"
#: The host spellings a page served by Butler on this machine has in its
#: `Origin` (a browser brackets IPv6).
ORIGIN_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def own_origins(request: web.Request) -> frozenset:
    """Butler's own origins: every LISTENING port (`LISTENERS`, or the
    accepting socket's port for an app served without them) under each
    loopback host spelling, http and https, plus the port-less form for a
    scheme's default port."""
    ports = request.app.get(LISTENERS)
    numbers = set(ports) if ports else {
        request.transport.get_extra_info("sockname")[1]}
    found = set()
    for port in numbers:
        for host in ORIGIN_HOSTS:
            for scheme, default in (("http", 80), ("https", 443)):
                found.add(f"{scheme}://{host}:{port}")
                if port == default:
                    found.add(f"{scheme}://{host}")
    return frozenset(found)


def is_local(request: web.Request) -> bool:
    """`_is_local` for an HTTP request: every loopback-only route asks this,
    and nothing else (AC-441, AC-454, AC-543).

    RV-1 (AC-586): a caller on this machine must ALSO send `LOCAL_HEADER:
    1`, and an `Origin`, when present, must be one of `own_origins`. The
    listener rule alone let any web page in the owner's browser POST to
    the LOCAL listener (a CORS "simple request" needs no preflight)."""
    if not _is_local(listener_of(request), request.remote or ""):
        return False
    if request.headers.get(LOCAL_HEADER) != "1":
        return False
    origin = request.headers.get("Origin")
    return origin is None or origin in own_origins(request)


def ws_origin_ok(request: web.Request) -> bool:
    """RV-25 (docs/messaging.md 6.5, AC-612): may this `/ws` handshake open?

    A browser always sends `Origin` on a WebSocket and a page cannot forge
    it, so a missing Origin is a non-browser client (tests, tools) and keeps
    today's behaviour. Otherwise the Origin must be a page Butler served:
    one of `own_origins` (the loopback spellings, the ONLY answer on LOCAL),
    or, on MAIN and PROXIED, the very URL this request was addressed to --
    the listener's scheme plus the `Host` header (`tailscale serve` keeps the
    phone's Host and terminates TLS, so PROXIED is always https). Plain http
    by Host is accepted only for an IP literal: a hostname over http is what
    a DNS-rebinding page looks like, and TLS is what rules that out."""
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    if origin in own_origins(request):
        return True
    listener = listener_of(request)
    host = request.headers.get("Host")
    if listener == LOCAL or not host:
        return False
    scheme = "https" if listener == PROXIED else request.scheme
    if scheme != "https":
        try:
            ipaddress.ip_address(URL(f"http://{host}").host or "")
        except ValueError:
            return False
    return origin.lower() == f"{scheme}://{host}".lower()

# THE ANNOUNCER (plan.md AC-14, docs/automations.md AU-18): a line nobody
# asked for waits for the desk to be idle, polled at this interval, and is
# DROPPED after this long rather than said late. [DECIDED 2026-09-26]: two
# minutes covers an ordinary spoken exchange; a result line said later than
# that is about a moment the owner has moved past, and the unread dot and the
# channel message already carry it.
ANNOUNCE_WAIT_S = 120.0
ANNOUNCE_POLL_S = 0.25

# How much conversation every turn's models are given (AC-64). Entries, not
# turns: one turn is a user entry plus at most one assistant entry, so this is
# three exchanges. Bounded because the ROUTER reads it on every single
# utterance and its whole value is being fast -- an unbounded transcript would
# grow the one prompt that runs before Butler can answer anything. Three
# exchanges is what a follow-up needs ("do that again for Boston" refers to
# the last thing said, not to the tenth); a conversation longer than that is
# what the substantive model's own memory is for -- pi resumes its whole
# session file, and the endpoint brain is answering from this window either
# way. [DECIDED 2026-08-19; not measured, and a routing-accuracy run against a
# larger window is the honest way to change it.]
HISTORY_ENTRIES = 6

# How long the daemon will wait for a brain to let go of a turn -- closing the
# turn generator it stopped reading, and aborting on barge-in. Both awaits
# reach a coding agent's teardown -- a held child is disposed on both paths
# (`brain_harness` -> `harness_pi._close_child`: stdin EOF on a 5 s budget,
# then `taskkill /F /T`), and BOTH sit on the barge-in path, where a hang is
# indistinguishable from a dead microphone (D-1). Measured against a real pi
# on 2026-08-18: abort 0.00 s, dispose 0.04 s. The budget is set above pi's
# measured 1.51 s abort settle so the graceful stop is never cut short, and
# well below the unbounded worst case it replaces. Expiring is loud and never
# fatal: whatever survives is the Job Object's problem, which is what AC-36
# built it for.
BRAIN_LETGO_S = 3.0

# How long `abandon()` will wait for a cancelled turn's task to actually stop.
# Named rather than written inline because it is one half of a RACE and the
# other half is `PLAYBACK_GRACE_S` above (D-39): a cancelled `_reply` runs its
# `finally` -- a cancelled task may still await, only a second cancel
# interrupts it -- and that `finally` sits out the playback gate. Whenever the
# gate outlasts this budget, barge-in returns while the stopped turn is still
# unwinding, and the stopped turn reaches `_rearm` AFTER the next turn has
# begun. `_rearm` takes the turn it is ending for exactly that reason, so this
# number decides latency and no longer decides correctness.
ABANDON_TASK_S = 3.0

# How often a running turn tells its page it is still alive, and what it has
# produced (AC-389, docs/turn-feedback.md). 5 s, so the page can say "no word
# from Butler" after 15 s (3x) without ever mistaking one late beat for a
# death -- the lesson of the backlog entry this answers: a poll too tight for
# the thing it watched read a daemon that was loading as a daemon that was
# down. A beat is one small JSON frame per running turn per 5 s.
PROGRESS_EVERY_S = 5.0

# How many characters of TYPED text one turn may carry (AC-162).
#
# DERIVED rather than chosen, following `butlerd.DICTATE_MAX_BYTES` and
# `butlerd.AMBIENT_MAX_BYTES`: the ceiling on how much the owner may hand
# Butler in one go already exists as `dictate_max_seconds`'s own maximum, and
# a second number written here is the pair that drifts (D-12). A dictation
# ceiling is in SECONDS OF SPEECH, so it is converted at the one
# characters-per-second figure this project has actually measured -- the
# whole-clip decode ceiling recorded in `daemon/AGENTS.md` returned 3922
# characters for 240 s of speech, which is 16 a second [MEASURED 2026-08-19].
# The product is what the owner could have SAID in the time this daemon is
# willing to listen, which is exactly the peer a typed turn is meant to be.
#
# A REFUSAL and never a truncation: half a question answered as though it were
# whole is this project's recurring failure, and the owner cannot see which
# half was dropped. It is checked here rather than at the socket, because
# `max_msg_size` is 16 MB and is about frames rather than about utterances.
#
# The number itself lives in `settings.py` since docs/automations.md phase 1,
# because `library.expand` caps a filled recipe at the same ceiling and
# cannot import this module (it imports that one): ONE definition, two names.
TYPED_CHARS_PER_SECOND = settings_mod.TYPED_CHARS_PER_SECOND
TYPED_MAX_CHARS = settings_mod.TYPED_MAX_CHARS
# The chat apps a phone turn can arrive from (docs/messaging.md MS-17, 8):
# each is the typed reason of its `PhoneSession` AND the notifier kind its
# questions go back to (MS-21), so the two can never disagree.
PHONE_REASONS = frozenset({"whatsapp", "telegram"})
# Typed reasons whose text is WORK for the brain and never a spoken command
# (`_on_final`): a scheduled run's filled recipe, and a phone message
# (docs/messaging.md MS-17; `/new` is the router's, not `_on_final`'s).
WORK_REASONS = frozenset({"recipe"}) | PHONE_REASONS
# A clip from outside the room (a voice note) is decoded in windows of this
# many seconds of 16 kHz mono int16 (docs/messaging.md MS-18; R1 in
# `.references/README.md`: a 30 s whole-clip decode is the fast path).
CLIP_WINDOW_S = 30
CLIP_WINDOW_BYTES = CLIP_WINDOW_S * 16000 * 2
# How often a waiting clip decode re-asks whether a live turn still holds
# the recognizer.
RECOGNIZER_POLL_S = 0.25

# How far back a CORRECTION may reach, in turns, per connected satellite
# (AC-97). A correction names the turn it supersedes; the daemon refuses any
# turn it did not mint FOR THAT SESSION, so a session has to remember the ids
# it was told about -- and a memory with no bound grows for as long as a
# tablet stays awake on a wall.
#
# [DECIDED 2026-09-10], not measured, and being too small is recoverable in
# one keystroke: a correction naming an older turn is refused out loud, and
# the owner re-sends the text as an ordinary typed turn -- which is exactly
# what a RETRY is. A hundred ints per satellite is the whole price.
CORRECTABLE_TURNS = 100

# How many CLOSED questions are remembered, so a late answer can be told why
# it is late (AC-250). Bounded because a daemon that ran for a week would
# otherwise keep every question it ever closed to answer one 410 -- and the
# only thing the entry holds is a short string, so the cost of being wrong is
# a refusal that says "this daemon does not hold that question" instead of
# naming which of the four reasons. Never the reverse: an id NOT in either map
# is refused, never forwarded.
QUESTIONS_REMEMBERED = 64

# The two kinds of daemon proposal (`Hub._propose`): ONE mechanism, one
# proposal per conversation of either kind (docs/recipe-tools.md RT-3,
# AC-664).
SCHEDULE_KIND, RECIPE_KIND = "schedule", "recipe"

# Why a question is no longer answerable, in the daemon's own words (AC-250,
# `docs/parallel-agents.md` 9.4). Four reasons, all nameable, because pi drops
# an unknown dialog id SILENTLY -- so a 410 that said only "gone" would leave
# the owner unable to tell "the agent already decided" from "you were too
# slow" from "this daemon restarted", which is the recorded Codex failure the
# criterion is named after.
#
# `answered` is a fifth and it is here because it is TRUE: a double press, or
# two devices answering the same card, is the ordinary case and reporting it
# as a restart would be a lie about a working system.
QUESTION_GONE = {
    "expired": ("agent_resolved",
                "the coding agent resolved that question on its own timeout, "
                "so your answer would have changed nothing"),
    "cancelled": ("conversation_stopped",
                  "that conversation was stopped, and its question went with "
                  "it"),
    "agent_gone": ("child_gone",
                   "the coding agent that asked is gone, so there is nothing "
                   "left to answer"),
    "answered": ("already_answered",
                 "that question was already answered"),
}
QUESTION_UNKNOWN = (
    "daemon_restarted",
    "this daemon does not hold that question. Nothing survives a restart -- "
    "every coding agent dies with the daemon, so a question it remembered "
    "would have nobody left to answer it")

# Session states. Named because the announcer's queueing rules (AC-14) key
# off them and "idle-but-live" was not a testable boundary.
IDLE = "idle"            # wake-listening
CAPTURING = "capturing"
THINKING = "thinking"
SPEAKING = "speaking"


@dataclass(frozen=True)
class ChatModels:
    """What the spoken model choice (docs/chat-model.md CM-10) needs from
    `butlerd`, which this module cannot import (butlerd imports it). Wired by
    `build_hub`, so the voice uses the SAME rules the routes do (D-18):
    `agent_of` is `chat_harness`, `labels` is `AGENT_LABELS`, `remember` is
    `remember_model`, `valid` is `chats.MODEL_NAME.fullmatch`."""
    agent_of: typing.Callable
    labels: dict
    remember: typing.Callable
    valid: typing.Callable


class Pending(typing.NamedTuple):
    """One attachment, and the conversation it was attached IN (AC-247).

    A pair rather than a field on the attachment record itself, because the
    conversation is a fact about this DEVICE's view at one moment and the
    attachment is a file on disk that outlives the turn: the store that wrote
    it has no opinion about conversations and must not gain one.
    """
    chat: str
    att: object


class AsrBusy(RuntimeError):
    """A whole-clip decode was refused because the recognizer is in use.

    Raised only by `Hub.dictate` (AC-116). It is a REFUSAL and not a failure:
    the caller is expected to say so and let the owner press the key again,
    which is why it is a distinct type rather than a generic error the route
    would have to tell apart by reading a message.
    """


class SatelliteSession:
    # A HEADLESS session is not a device in a room: it is a scheduled run's
    # (`RunSession`, docs/automations.md AU-5). It interrupts nobody, is never
    # read aloud and never writes the router's short memory -- the four
    # places that ask are `_typed`, `_speaking`, `_reply` and `_remember`.
    headless = False
    # The `reason` a TYPED turn from this session carries (`_typed`): a page
    # types, a scheduled run is a recipe, a phone message is its chat app's
    # kind ("whatsapp", "telegram").
    # The last two are WORK (`WORK_REASONS`): no spoken command claims them.
    typed_reason = "typed"
    # Is a turn from this session the OWNER's own (`Hub.last_owner_chat`,
    # docs/messaging.md MS-16)? A device or the phone, yes; a scheduled run,
    # no.
    owner_turn = True
    # The owner's OWN WORDS when the turn's text carries more than they said
    # (a voice note's transcript under its warning prefix): what the turn's
    # language is decided from (AC-522). None means the text itself.
    language_words: str | None = None

    def __init__(self, hub: "Hub", ws: web.WebSocketResponse):
        self.hub = hub
        self.ws = ws
        # The paired device behind this socket (RA-10), None for the owner.
        self.paired: str | None = None
        self.device_id = "unknown"
        self.name = "unnamed"
        self.caps: dict = {}
        # The socket's PEER address and the LISTENER it arrived on, set by
        # `Hub.handle` from the accepted request; "" and MAIN for a session
        # no socket was accepted for. What makes a session THE DESK
        # (`is_desk`), because the page's own `device_id` is caller-supplied
        # (AC-470), and the listener because a proxied peer is always
        # loopback (AC-541).
        self.peer = ""
        self.listener = MAIN
        self.connected_at = time.monotonic()
        self.state = IDLE
        # The FRONT-DOOR turn: what `abandon()`, `s.state`, `s.task` and
        # `s.reply_task` track, exactly as before (docs/parallel-agents.md
        # phase 6, item 1). This is the turn a wake word or a typed message
        # would barge in on -- it is NOT "every turn this session may still
        # emit for" any more, because that is now `turn_by_chat`/`chat_by_turn`
        # below. Kept as a separate scalar rather than folded into those two
        # dicts because `_barge_in` calls `abandon()`, and `abandon()` only
        # ever touches THIS name (`docs/parallel-agents.md` phase 8, partial:
        # `_barge_in` is now scoped by whether a session HEARS its own
        # front-door turn, but it still only ever ends the front door).
        self.turn: int | None = None
        # Every turn this session may still legitimately emit for, keyed by
        # the CONVERSATION it belongs to -- one live turn per chat rather than
        # one per session. `SatelliteSession.owns(turn)` used to be `turn ==
        # self.turn`, a single slot, so a background conversation's reply was
        # dropped the moment this session's front door moved to a different
        # conversation -- server-side by `owns()`/`send_for_turn`, and again
        # page-side by `app.js`'s single-scalar `currentTurn`. A second turn
        # for a chat ALREADY in here still supersedes the first (D-6's
        # stale-turn discard, unchanged), just scoped to one conversation
        # instead of the whole session.
        self.turn_by_chat: dict[str, int] = {}
        # The reverse index -- which chat a turn was minted for, fixed at
        # `_begin_turn` and never re-read (AC-246) -- and what makes `owns()`
        # O(1). This is what `_reply`, `_speak` and `_on_ask_human` read
        # instead of the scalar `turn_chat` below, because they run inside a
        # SPECIFIC turn's own closure and a newer turn on this session must
        # not be able to step on a background turn's answer to "which
        # conversation am I in".
        self.chat_by_turn: dict[int, str] = {}
        self.pipeline = None
        self.task: asyncio.Task | None = None
        self.reply_task: asyncio.Task | None = None
        self.alive = True
        # Segments whose audio actually reached this satellite, for the
        # current turn. Feeds the history invariant in ports.TurnContext:
        # what was SPOKEN, not what was generated.
        self.spoken: list[str] = []
        self.interrupted = False
        # How many `reply` frames each LIVE turn has sent, by turn id. The
        # page counts the same frames the same way, so "from frame N on,
        # nothing was heard" (`{type: "unspoken"}`) names one position both
        # sides agree on (docs/filing-and-pacing.md item 2, AC-286). Ends
        # with the turn's ownership, beside `chat_by_turn`.
        self.reply_frames: dict[int, int] = {}
        # Each live turn's AUDIO LEG (`_TurnVoice`), by turn id, so
        # `abandon()` can halt it before it aborts the engine. Registered
        # and released by `_reply`.
        self.voices: dict[int, "_TurnVoice"] = {}
        # The ambient back-fill for the turn named beside it: the last N
        # seconds of the room, POSTed by the satellite at capture_start and
        # decoded after the final (AC-68, AC-69). Kept as a PAIR because the
        # request is fired without await and therefore races the turn it
        # belongs to -- bytes with no turn beside them would arrive late and
        # become the NEXT turn's context, which is a clip of a room the owner
        # has already moved on from handed to a coding agent as the background
        # for a different instruction (D-6).
        self.ambient: bytes = b""
        self.ambient_turn: int | None = None
        # What language this CONVERSATION is in, or None until a turn decides
        # (AC-209). Set on every turn by `Hub._turn_language`, from the
        # owner's own words when the setting is `auto` and from the setting
        # otherwise -- and it is the LATCH: a turn the detector will not name
        # keeps whatever is here, so a one-word answer cannot flip the voice.
        #
        # On the SESSION rather than only on the turn's `TurnContext`, because
        # `_speak` is reached from six places that have no context at all --
        # the spoken mode change, the new conversation, both project lines and
        # the ASR failure -- and every one of them must be said in the language
        # the conversation is in.
        self.language: str | None = None
        # What the owner ATTACHED and has not asked about yet (AC-199), as
        # `Pending(chat, att)` pairs. An ambient clip names the TURN it was
        # captured for; an attachment is put here BEFORE there is a turn to
        # name, so what it names is the CONVERSATION that was on screen when
        # it was attached (AC-247). It is consumed by the next turn from this
        # session IN THAT CONVERSATION, whichever door that turn comes in by
        # -- which is what makes "show Butler this and ask about it out loud"
        # work at all, and what stops the photo attached to the site chat
        # arriving in the trading one.
        self.attachments: list = []
        # The task ids "what are my tasks" last read to THIS device, in the
        # order it numbered them, so "mark task 2 done" means the second one
        # the owner HEARD (docs/automations.md 8, AC-453). Per socket, like
        # `showing`: the numbers were said to one pair of ears.
        self.read_tasks: list[str] = []
        # What this device says it is SHOWING, or None until it says (AC-238,
        # AC-247, `docs/parallel-agents.md` D4). Per SOCKET and never
        # persisted: the daemon's own pointer is where a turn goes, and this
        # is the separate fact of which conversation is in front of a pair of
        # eyes. A dropped socket forgets it, which is right -- the device may
        # be in another room by the time it comes back, and it re-declares on
        # `ready`.
        self.showing: str | None = None
        # The FRONT-DOOR turn's conversation -- a convenience mirror of
        # `chat_by_turn[self.turn]`, kept as its own scalar because
        # `capture_start` needs it the instant `_begin_turn` returns and a
        # couple of tests reproduce `_begin_turn` by hand
        # (`test_conversation.py`'s `hub_turn` driver). Fixed at `_begin_turn`
        # and never re-read (AC-246). Empty when the daemon cannot name one at
        # all, which is every brain with no session store: endpoint mode holds
        # no conversations, so nothing may be keyed by it there.
        #
        # NOT what `_reply`, `_speak` or `_on_ask_human` read any more --
        # those run inside a specific turn's own closure and use
        # `chat_by_turn[turn]` instead, because a newer turn beginning on this
        # session would otherwise overwrite the answer an older, still-live
        # background turn depends on.
        self.turn_chat: str = ""
        # Every turn this session was TOLD about, oldest first (AC-97, D-6).
        # A correction may name one of these and nothing else. The typed
        # message avoids D-6 by reading no id from the body at all; a
        # correction cannot, because pointing at an earlier turn is the whole
        # of what it does -- so the id is checked against what this session
        # was actually given rather than trusted because it is a number.
        self.seen_turns: collections.deque = collections.deque(
            maxlen=CORRECTABLE_TURNS)
        # "I finished sending" and "you finished playing" are different
        # moments, and conflating them is what makes a satellite transcribe
        # its own reply (see _await_playback).
        self.audio_ms_sent = 0.0
        self.playback_done = asyncio.Event()
        self.speaking_started = False

    def take_backfill(self, turn: int) -> bytes:
        """The ambient clip for `turn`, ONCE, and nothing for any other turn.

        Reading it is what discards it: a half-minute of the room is up to
        1.9 MB and it is audio nobody addressed to Butler, so it is held for
        exactly as long as the turn that may use it (AC-72). Clearing on a
        MISMATCH too, and that is the important half: a clip stored for a turn
        that was then abandoned would otherwise sit here until something else
        overwrote it, and the only thing that reads this is the next turn.
        """
        pcm = self.ambient if self.ambient_turn == turn else b""
        self.ambient = b""
        self.ambient_turn = None
        return pcm

    def take_attachments(self, chat: str) -> tuple:
        """Whatever the owner attached IN `chat`, ONCE (AC-199, AC-247).

        Reading is what clears it, `take_backfill`'s rule for a related
        reason: an attachment the owner put up and then never asked about must
        not silently ride along on a question about something else.

        The CONVERSATION is the key and it is the whole of AC-247. Until
        2026-09-12 this took everything pending, so a photo attached while
        reading one conversation arrived in whichever conversation spoke next
        -- and a coding agent then read that file off disk, which is the one
        failure here that cannot be undone. What is left behind on a mismatch
        is left PENDING rather than dropped: the owner attached it to a
        conversation they have not asked their question in yet.

        `take_backfill`'s clear-on-mismatch rule deliberately does NOT apply.
        A stale ambient clip is a half-minute of a room nobody addressed to
        Butler; a pending attachment is a file the owner chose, and the
        conversation it names is still there to go back to.
        """
        held = tuple(p.att for p in self.attachments if p.chat == chat)
        self.attachments = [p for p in self.attachments if p.chat != chat]
        return held

    @property
    def is_desk(self) -> bool:
        """Is this THE DESK -- a device on the machine Butler runs on?
        (docs/automations.md AU-18, AC-470). The socket is local by the one
        trust rule (`_is_local`, AC-541); a headless run never is."""
        return not self.headless and _is_local(self.listener, self.peer)

    def hears(self, chat: str) -> bool:
        """Is this device SHOWING the conversation `chat`, so it may be spoken
        aloud here (AC-238)?

        **A device speaks the conversation that device is showing** -- the
        owner's ruling (`docs/parallel-agents.md` D4). There is no arbiter, no
        hand-over control and nothing stored about who "has" the voice, so
        this predicate is the whole mechanism.

        Both halves must be NAMED for a reply to go quiet, and that asymmetry
        is deliberate. A device that has never said what it is showing is the
        hands-free case -- no page open, a client older than the frame, or a
        satellite that is a speaker in a room -- and it hears everything,
        because Butler's first sentence is that it is hands-free. A turn with
        no conversation the daemon can name (endpoint mode) is audible for the
        same reason. Muting on an ABSENCE would turn a page-side bug, or a
        brain with no session store, into a daemon that has silently stopped
        speaking, which is AC-7b in the one organ the owner cannot see.
        """
        if not chat or self.showing is None:
            return True
        return self.showing == chat

    def owns(self, turn: int | None) -> bool:
        """Is `turn` still LIVE for this session -- one of the turns it may
        still emit for, whichever conversation it is in?

        The single predicate behind stale-turn discard. It guards every
        outbound message and every step of the reply loop, so a turn that was
        abandoned or superseded produces no further wire traffic and no
        further synthesis (D-6, AC-5).

        **PER CONVERSATION, not per session** (docs/parallel-agents.md phase
        6, item 1). Used to be `turn == self.turn`, one slot for the whole
        session, so a background conversation's turn stopped being "owned"
        the instant this session's front door moved to a DIFFERENT
        conversation -- the reply was still being generated, correctly, and
        every frame for it was dropped anyway. A turn is live here until its
        OWN conversation gets a newer one (`turn_by_chat[chat]` moved on) or
        it is explicitly ended (`_rearm`, `abandon`) -- so two turns in
        different chats on one socket are both owned at once, and a second
        turn in the SAME chat still supersedes the first exactly as before.

        `turn == self.turn` is still checked FIRST and always answers True:
        the front door is always owned, whether or not `_begin_turn` (the
        only writer of `chat_by_turn`) ever ran for it. Dozens of tests set
        `s.turn` directly to stand in for "this session is mid-turn" without
        going through `_begin_turn` at all -- test/AGENTS.md's own precedent
        for a shared driver reproducing daemon internals by hand -- and the
        fallback is what keeps them describing the SAME session they always
        did rather than a session that owns nothing. It costs nothing in
        production: `_begin_turn` sets `self.turn` and registers
        `chat_by_turn[turn]` in the same breath, so the two never disagree
        about the front-door turn there.
        """
        if turn is None:
            return False
        if turn == self.turn:
            return True
        chat = self.chat_by_turn.get(turn)
        return chat is not None and self.turn_by_chat.get(chat) == turn

    # -- sending ------------------------------------------------------------ #
    async def send(self, **payload) -> None:
        if self.ws.closed:
            return
        try:
            await self.ws.send_str(json.dumps(payload))
        except (ConnectionResetError, RuntimeError):
            self.alive = False

    async def send_for_turn(self, turn: int, **payload) -> None:
        """Drop anything belonging to a turn this session has moved past.

        This is the server half of stale-turn discard. Relying on the client
        to ignore it would leave the daemon talking about a turn nobody is
        listening to, and a test that only checked the client would pass
        while the bug lived (D-6, AC-5).

        **EVERY FRAME GAINS `chat`, looked up from the turn rather than from
        any current pointer** (docs/parallel-agents.md phase 6, item 3). With
        a single turn slot the session itself WAS the answer to "which
        conversation is this"; now that two turns can be live at once, the
        page cannot key staleness per conversation from a frame that does not
        say which one it is answering for. No caller passes `chat` itself
        (`**payload` never carries it), so there is nothing to collide with.
        """
        if not self.owns(turn):
            return
        await self.send(turn=turn, chat=self.chat_by_turn.get(turn, ""),
                        **payload)

    async def send_audio(self, turn: int, chunk: bytes) -> None:
        if not self.owns(turn) or self.ws.closed:
            return
        try:
            await self.ws.send_bytes(chunk)
            # 24 kHz mono int16: 48 bytes per millisecond of audio.
            self.audio_ms_sent += len(chunk) / 48.0
        except (ConnectionResetError, RuntimeError):
            self.alive = False

    # -- turn lifecycle ------------------------------------------------------ #
    async def abandon(self, turn: int | None = None) -> None:
        """Barge-in: drop whatever this session was doing, speak nothing more.

        Deliberately does NOT emit a final for the abandoned turn -- it was
        abandoned, not ended, and a final here would produce a reply to an
        utterance the user interrupted.

        **ONLY THE FRONT-DOOR TURN, not every turn this session owns**
        (docs/parallel-agents.md phase 6, item 5). `_barge_in` calls this for
        a non-idle session only when that session would actually SPEAK its
        front-door turn (`docs/parallel-agents.md` phase 8, partial -- see
        `_barge_in`'s own docstring) -- but `abandon()` itself must not widen
        into abandoning every conversation this session happens to be
        tracking in `turn_by_chat`. It only ever touched `self.turn`; that
        stays true, so a background turn tracked here for a DIFFERENT
        conversation is left running.

        **`turn` IS THE ESCAPE HATCH** (docs/filing-and-pacing.md 1.7,
        `Hub.stop_conversation`). Every existing caller passes nothing, which
        means "the front door" exactly as before -- `_stop`, `_barge_in` and
        every test here. An EXPLICIT turn that is NOT this session's front
        door lets `Hub.stop_conversation` reuse this same abort sequence for
        a turn belonging to a conversation this session merely happens to be
        tracking in the background, without a second copy of the teardown
        (D-18, `_barge_in`'s own argument: "the copy that drifts is the one
        that leaves a microphone held"). The two paths diverge in exactly two
        ways, both because there is no PER-TURN state for anything but the
        front door -- `self.state` is a single scalar `_speak` sets for
        whichever turn is speaking right now, so it cannot say whether a
        background turn is the one making sound. Aborting the engine is
        therefore UNCONDITIONAL for a background turn rather than
        state-gated (both `abort()` calls are keyed by turn id/handle and are
        harmless no-ops against a turn that is not, or is no longer, actually
        running -- `HarnessConverse.abort`'s own `turn_id != self._turn_id`
        guard, `ports.Tts`'s handle match). And `self.task`/`self.reply_task`
        /`self.pipeline`/`self.turn`/`self.state` are left COMPLETELY alone:
        those describe whatever THIS session is doing right now, which for a
        background turn is a different, live front-door turn this call must
        not disturb.

        AN ASSERTION ON THE CALLER (D-18), not just the callee: an explicit
        `turn` must be one THIS session actually tracks. `turn=None` (every
        caller except `stop_conversation`) always names this session's own
        front door and needs no check -- it is a self-referential read, never
        a guess. `stop_conversation` is safe today only because it sources
        `turn` from `s.turn_by_chat.get(chat_id)` first; nothing enforced
        that a future caller would do the same, and forwarding a stale or
        another session's id here would abort or dispose whatever THAT id
        happens to mean to this session, silently.
        """
        if turn is not None:
            assert turn in self.chat_by_turn, (
                f"abandon(turn={turn}) named a turn this session is not "
                f"tracking (D-18): the caller must find it in a session's "
                f"own turn_by_chat/chat_by_turn first, as stop_conversation() "
                f"does, rather than forward an id from elsewhere.")
        abandoned_turn = self.turn if turn is None else turn
        is_front_door = abandoned_turn == self.turn
        # The turn's own AUDIO QUEUE before anything else. The engine abort
        # below ends the synthesis in flight, and an aborted synthesis ends
        # quietly -- so a queue still running would simply start the NEXT
        # sentence's synthesis in the gap before its task is cancelled, and
        # the engine would work on a reply nobody hears (D-4). Halting first
        # closes that gap (docs/filing-and-pacing.md item 2).
        if abandoned_turn is not None and abandoned_turn in self.voices:
            self.voices[abandoned_turn].halt()
        # The PRODUCER first, and before any task is cancelled (AC-43, D-4).
        # THINKING is where the brain is doing the expensive thing; SPEAKING
        # counts too, because `turn()` yields intents as they are decided, so
        # generation can still be running while the first sentences are
        # already in the room. Cancelling the reply task stops US reading and
        # leaves the model generating into a socket nobody drains.
        if abandoned_turn is not None:
            do_abort = (self.state in (THINKING, SPEAKING) if is_front_door
                       else True)
            speaking = (self.state == SPEAKING if is_front_door else True)
            if do_abort:
                try:
                    # BOUNDED, for the reason the task cancel below is. This
                    # reaches `HarnessConverse.abort` -> the child's
                    # abort AND its disposal, and an unbounded await on a
                    # wedged `taskkill` holds the microphone through the one
                    # moment the owner is talking to it (D-1). It did not
                    # need a bound when abort was one stdin write.
                    await asyncio.wait_for(
                        self.hub.brain.abort(abandoned_turn),
                        timeout=BRAIN_LETGO_S)
                except Exception as e:                     # noqa: BLE001
                    # We are here to stop it, not to hear about it. A raise
                    # that escaped would abandon abandon() half-done: tasks
                    # still running, state never back to IDLE, microphone
                    # held (D-1).
                    print(f"[butler] brain abort failed for turn "
                          f"{abandoned_turn}: {e!r}")
                if speaking:
                    # Tell the ENGINE, not just our own task. Cancelling the
                    # reply task stops us reading chunks, but the
                    # synthesizer keeps working on a reply nobody will hear
                    # and the next request queues behind it -- measured at
                    # ~6.7 s with Kokoro. Cancelling the consumer is not
                    # cancelling the producer (D-4).
                    try:
                        await self.hub.tts.abort(abandoned_turn)
                    except Exception as e:                 # noqa: BLE001
                        print(f"[butler] tts abort failed for turn "
                              f"{abandoned_turn}: {e!r}")

        if is_front_door:
            for task in (self.task, self.reply_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        # BOUNDED. These tasks sit on aiohttp writes, a child
                        # process pool and a transcribe.cpp worker thread,
                        # and a bare `await task` on a cancellation that
                        # never lands hangs barge-in forever -- with the
                        # microphone held. Pipecat hit exactly this and now
                        # guards it with a timeout. Waiting less is strictly
                        # better than waiting forever: the stale-turn filter
                        # already makes an orphaned task harmless (D-1, D-6)
                        # -- harmless since `_rearm` names its turn, and NOT
                        # before that (D-39).
                        await asyncio.wait_for(asyncio.shield(task),
                                               timeout=ABANDON_TASK_S)
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        # Anything at all: cancelled (CancelledError is a
                        # BaseException, so it has to be named), timed out,
                        # or failed. HOW the task ended cannot be allowed to
                        # affect barge-in -- we are here to stop it, not to
                        # hear about it.
                        pass
            self.task = self.reply_task = None
            if self.pipeline:
                self.pipeline.close()
                self.pipeline = None
        # Ends the abandoned turn's OWNERSHIP too, mirroring what `self.turn
        # = None` used to mean on its own: `owns(abandoned_turn)` must go
        # false from here. Only its own entry -- a live turn tracked for a
        # DIFFERENT chat is untouched, which is the whole of the "do not
        # abandon everything" rule above.
        if abandoned_turn is not None:
            self.reply_frames.pop(abandoned_turn, None)
            chat = self.chat_by_turn.pop(abandoned_turn, None)
            if chat is not None and self.turn_by_chat.get(chat) == abandoned_turn:
                del self.turn_by_chat[chat]
        if is_front_door:
            self.turn = None
            self.state = IDLE

    def _rearm(self, turn: int | None) -> None:
        """The single exit. Every terminal path lands here (D-1).

        `turn` is the turn that is ENDING, and it is required (D-6, D-39). A
        terminal path reaches this line after awaits that can last SECONDS --
        `_await_playback` alone is bounded at the audio sent plus
        `PLAYBACK_GRACE_S` -- and a session can have moved on to a new turn in
        that window. Re-arming then takes the microphone away from the turn the
        owner is now in, which is the one thing D-1 is about, arriving from the
        one direction D-1 does not cover.

        MEASURED 2026-09-11: a `stop` cancels `_reply`, whose `finally` keeps
        running (a cancelled task's `finally` may await; only a SECOND cancel
        interrupts it) and sits out the 3.6 s playback gate. `abandon()` waits
        3.0 s and gives up, so the next turn starts 0.6 s before the stopped
        turn's `finally` reached this line and cleared `turn`. The new turn then
        owned nothing: `_speak` bailed on `owns()`, `_end_speaking` was dropped
        by `send_for_turn`, and NO `speak_end` was ever sent -- the page sat in
        its speaking state with the Stop button showing until it was reloaded.

        The check is HERE rather than at the seven call sites because this is
        where the mutation happens, and a rule enforced at call sites is a rule
        the eighth call site does not have (D-18). `None` is accepted for the
        same reason it is required: a caller with no turn to name is saying so
        explicitly rather than defaulting into the unguarded old behaviour.

        **A BACKGROUND TURN ENDING DOES NOT CLEAR THE FRONT DOOR**
        (docs/parallel-agents.md phase 6, item 1). If a newer turn has since
        become `self.turn` -- a different conversation this session has since
        addressed -- then `turn` ending here is not what `s.state`/`s.task`/
        `s.pipeline` describe any more, and clearing them would take the
        microphone away from the turn the owner is now in, exactly the D-1
        failure this method exists to prevent. Only ITS OWN ownership entry
        is released; the front door moves on only when the turn ending here
        IS the front door.
        """
        if turn is not None and not self.owns(turn):
            return
        if turn is not None:
            self.reply_frames.pop(turn, None)
            chat = self.chat_by_turn.pop(turn, None)
            if chat is not None and self.turn_by_chat.get(chat) == turn:
                del self.turn_by_chat[chat]
        if turn is None or turn == self.turn:
            self.pipeline = None
            self.turn = None
            self.state = IDLE


class _NoSocket:
    """A socket that is always closed: `SatelliteSession.send` returns at
    its `closed` check, so nothing a headless session is sent reaches a
    wire. The two writers raise, because reaching one would be a caller that
    skipped that check (D-18)."""

    closed = True

    async def send_str(self, data: str) -> None:
        raise RuntimeError("a headless session has no socket")

    async def send_bytes(self, data: bytes) -> None:
        raise RuntimeError("a headless session has no socket")


class RunSession(SatelliteSession):
    """A scheduled run's SESSION: a `SatelliteSession` with no socket
    (docs/automations.md AU-5, AC-462).

    It exists so a run is an ORDINARY TYPED TURN in its own chat, through
    `Hub._typed` and everything behind it -- the router, the brain, the
    record, the heartbeat, `_rearm` -- and never a second agent pathway
    (D-18). What differs is declared by `headless` and read in four places
    (`_typed`, `_barge_in`, `_speaking`, `_reply`/`_remember`): it interrupts
    nobody and nobody's wake word interrupts it, it is never read aloud, and
    it leaves the router's short memory alone.

    `device_id` is "automation:<id>", `showing` is its own chat -- so
    `chat_for` sends its turn there and nowhere else -- and every frame it
    is sent, a broadcast included, is handed to `on_frame` instead of a wire.
    That is how the runner learns the answer, a question (`needs_you`) and
    the turn's end without a second channel into the brain. The playback
    gate needs nothing: no audio is ever sent, so `_await_playback` returns
    at once.
    """

    headless = True
    typed_reason = "recipe"
    owner_turn = False

    def __init__(self, hub: "Hub", chat: str, device_id: str,
                 on_frame=None):
        super().__init__(hub, _NoSocket())
        self.device_id = device_id
        self.name = device_id
        self.caps = {"tts": False}
        self.showing = chat
        self.on_frame = on_frame
        self.stopped = False

    async def send(self, **payload) -> None:
        if self.on_frame is not None:
            self.on_frame(payload)

    async def abandon(self, turn: int | None = None) -> None:
        # Only the owner's own stop reaches here (`stop_conversation`); the
        # room's barge-in skips a headless session. Remembered so the run
        # records `stopped` rather than `completed`.
        self.stopped = True
        await super().abandon(turn)


# --------------------------------------------------------------------------- #
# The owner's phone: WhatsApp and Telegram (docs/messaging.md MS-16, MS-17, 8;
# AC-519, AC-520, AC-533)
# --------------------------------------------------------------------------- #
class PhoneSession(RunSession):
    """One message from the owner's phone (their own WhatsApp chat, or a
    Telegram bot with inbound on), as a TURN: a `RunSession` (headless, so
    nothing is spoken at the desk, AC-520, and no wake word interrupts it)
    that differs in three declared ways.

    * It is the OWNER talking (`owner_turn`): it moves `last_owner_chat`
      like any device does (MS-16).
    * Its typed reason is its chat app's kind (`reason`, one of
      `PHONE_REASONS`), which is WORK like a recipe (no spoken command claims
      it), is how a page marks the turn "via WhatsApp", and is where its
      questions go (`Hub._question_origin`, MS-21).
    * Its turn frames are SHOWN on every page as well as handed to
      `on_frame` (MS-16: "the page shows it live"). A page adopts the turn
      only for the chat it is showing (`web/app.js`); the frames are the
      ordinary ones, so the transcript renders exactly as a typed turn's.

    `words` are the owner's own words (the transcript without its warning
    prefix) and `language` is the chat's latched language, carried by the
    caller from one message to the next because each message is a new
    session (AC-209's latch, AC-522).
    """

    owner_turn = True

    def __init__(self, hub: "Hub", chat: str, device_id: str, on_frame,
                 words: str, language: str | None, *, reason: str):
        if reason not in PHONE_REASONS:
            raise ValueError(f"no phone channel is called {reason!r}; known: "
                             f"{sorted(PHONE_REASONS)}")
        super().__init__(hub, chat, device_id, on_frame=on_frame)
        self.typed_reason = reason
        self.language_words = words
        self.language = language

    async def send_for_turn(self, turn: int, **payload) -> None:
        if not self.owns(turn):
            return
        frame = dict(turn=turn, chat=self.chat_by_turn.get(turn, ""),
                     **payload)
        await self.send(**frame)
        for page in [p for p in self.hub.sessions if not p.headless]:
            await page.send(**frame)


class _TurnProgress:
    """What one running turn has ACTUALLY produced, for its heartbeat
    (AC-389, AC-390, docs/turn-feedback.md 4).

    Fed only from what `_reply` sees -- a shown `Say`, a `WorkerEvent`, an
    `AskHuman`, the end of the text leg -- so nothing here is estimated.
    `last` moves only on EVIDENCE: the heartbeat is not evidence, and a
    daemon that is alive beside an agent that has been quiet for two minutes
    is two facts the page must be able to tell apart.
    """

    def __init__(self) -> None:
        self.started = self.last = time.monotonic()
        self.phase = "starting"
        self.detail = ""
        self.chars = 0             # answer characters SHOWN this turn
        self.drafted = 0           # answer characters ARRIVED (harness text)
        self.thought_chars = 0     # reasoning + tool-argument characters
        # A coding agent reported in (any `WorkerEvent`; `started` comes
        # first, once its child is up). Only such a turn can FINISH (AC-695).
        self.agent_ran = False
        # Set once, at the agent's end: `{agent, agent_elapsed_ms}`, frozen
        # from then on while the voice plays out (docs/lantern-ui.md 8).
        self.finished: dict = {}

    def note(self, phase: str, detail: str = "", chars: int = 0,
             thought: int = 0) -> None:
        self.phase, self.detail = phase, detail
        self.chars += chars
        self.thought_chars += thought
        self.last = time.monotonic()

    def worker(self, intent: WorkerEvent) -> None:
        """One harness activity, in this frame's vocabulary. `tool_end`
        reads as `thinking`: the tool returned and the model is working on
        what it got back, which is all that is known."""
        self.agent_ran = True
        if intent.kind == "tool_start":
            self.note("tool", intent.detail)
        elif intent.kind == "text":
            # Arrived, maybe not yet shown: the splitter holds a list or a
            # fence until it ends, and the model is writing all the while.
            self.drafted += intent.chars
            self.note("writing")
        elif intent.kind == "tool_input":
            self.note("tool_input", thought=intent.chars)
        elif intent.kind in ("thinking", "tool_end"):
            self.note("thinking", thought=intent.chars)
        else:                      # "started": the child is up, nothing yet
            self.note("starting")

    def frame(self, elapsed_ms: int) -> dict:
        # `chars` is the answer so far: what ARRIVED where the brain reports
        # it (harness mode), what was SHOWN where it does not (endpoint).
        return {"elapsed_ms": elapsed_ms, "phase": self.phase,
                "detail": self.detail,
                "chars": max(self.chars, self.drafted),
                "thought_chars": self.thought_chars,
                "last_event_ms": round((time.monotonic() - self.last) * 1000),
                **self.finished}


class _TurnVoice:
    """One turn's AUDIO LEG, run as its own task beside the text leg
    (docs/filing-and-pacing.md item 2, AC-284..AC-286).

    `_reply` sends each Say's text the moment the brain yields it and hands
    the audio job here; this plays the jobs strictly in order, one at a time,
    so the voice keeps exactly the pacing it had (AC-285) while the page
    keeps up with the MODEL. Phase 1 (`359cb96`) was a lookahead of one:
    sentence N+2's text still waited on sentence N's synthesis, which on
    kokoro fp32 with one CPU worker is the owner's complaint unchanged for
    any answer longer than two sentences.

    **WHAT WAS SHOWN AND NOT HEARD IS TRACKED HERE**, because this is the
    only place that knows. Every shown Say is an entry with the index of its
    `reply` frame; an entry is `heard` once its audio leg completed. What is
    left unheard when the turn ends -- a stop, a barge-in, a dead segment, a
    look-away -- is what `Hub._mark_unspoken` tells the page and the record.
    `s.spoken` is untouched by any of this: `_speak_audio` is still its only
    writer, so the router's history holds what the room heard (AC-64, D-20).
    """

    def __init__(self, hub: "Hub", s: SatelliteSession, turn: int,
                 chat: str):
        self.hub, self.s, self.turn, self.chat = hub, s, turn, chat
        s.voices[turn] = self
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None
        # [frame index, text, heard] per SHOWN Say, in frame order.
        self.entries: list[list] = []
        # A segment's synthesis FAILED (not merely a lost turn). plan.md 3.8:
        # the REMAINDER of the reply is abandoned loudly, never silently
        # skipped -- so the voice says nothing more this turn (no later
        # sentence is attempted, which would skip the one Kokoro choked on),
        # the error frame is `_speak_audio`'s, and everything shown from the
        # failed sentence on is marked unheard. The TEXT leg is NOT stopped:
        # text is never gated on synthesis (the owner's delegated ruling of
        # 2026-09-26), and a TTS crash no longer walks away from a coding
        # agent mid-prompt -- AC-238's argument, reached by another cause.
        self.broke = False

    def shown(self, frame: int, text: str, job) -> None:
        """A Say whose text frame went out. `job` is None when no audio is
        owed for it (`spoken=False`: the token cap's tail, or AC-238's mute
        at text time) -- shown and never heard. The page already marks those
        from the frame itself; they are entries anyway, so the RECORD gets
        the same boundary the page draws."""
        entry = [frame, text, False]
        self.entries.append(entry)
        if job is not None:
            self.put(job, entry)

    def put(self, job, entry=None) -> None:
        """Queue one audio job; `entry` is None for a status line (`keep`
        False), which is heard and never shown."""
        self.jobs.put_nowait((job, entry))
        if not self.broke and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            item = await self.jobs.get()
            if item is None:
                return
            job, entry = item
            # A LOOK-AWAY after this sentence's text went out still mutes
            # its audio (AC-238, docs/filing-and-pacing.md 2.3 item 4): the
            # check at text time cannot see a queue, so it is made again at
            # the moment the audio would start. Skipped, it stays unheard.
            if not self.s.hears(self.chat):
                continue
            # HEARD means `s.spoken` grew -- the one record of audio that
            # reached the room -- and not merely that the leg returned: an
            # engine abort ends a synthesis quietly, with no chunk sent.
            before = len(self.s.spoken)
            if not await self.hub._speak_audio(self.s, self.turn, *job):
                self.broke = self.s.owns(self.turn)
                return
            if entry is not None:
                entry[2] = len(self.s.spoken) > before

    async def finish(self) -> bool:
        """Let every queued job play out, in order. False if the audio leg
        broke, which abandons the rest of the reply."""
        if self.task is not None:
            self.jobs.put_nowait(None)
            await self.task
            self.task = None
        return not self.broke

    def halt(self) -> None:
        """`abandon()`'s half of `stop()`, synchronous so it lands before
        the engine abort does. Speech OFF is left to `stop()` to drain."""
        if (self.task is not None and not self.task.done()
                and self.hub._speaking(self.s)):
            self.task.cancel()

    async def stop(self) -> None:
        """The turn is over before its audio is. Speech ON: cancel whatever
        is queued or mid-synthesis -- `abandon()` has already told the
        ENGINE (`tts.abort(turn)`), and this is what stops the NEXT queued
        sentence starting a fresh synthesis. Speech OFF: drain instead,
        because a muted leg asks no engine and costs nothing, and everything
        a muted reply showed IS its answer (AC-110)."""
        if self.task is None or self.task.done():
            return
        if not self.hub._speaking(self.s):
            self.jobs.put_nowait(None)
            await asyncio.wait([self.task], timeout=ABANDON_TASK_S)
            return
        self.task.cancel()
        await asyncio.wait([self.task], timeout=ABANDON_TASK_S)

    def unheard_from(self) -> int | None:
        """The first shown frame whose audio never completed, or None."""
        return next((e[0] for e in self.entries if not e[2]), None)


class Hub:
    # Rotating throws the agent's context away, so it is said out loud (AC-35).
    # A silent rotation leaves the owner talking to something that has just
    # forgotten the last ten minutes, with nothing to say that it had.
    NEW_CONVERSATION = "Starting a new conversation."
    # What a spoken turn-mode change says (`_apply_turn_mode`). Each line
    # starts with the mode's settings label -- Pause, Smart, Long form -- so
    # the voice and the sheet use one vocabulary (AC-812,
    # `test_settings_wording.py`).
    TURN_MODE_SAID = {
        "endword": "Long form. I will wait until you say send it.",
        "smart": "Smart mode. I will listen for a finished sentence.",
        "patient": "Pause mode. I will answer when you pause.",
    }
    # Navigation is confirmed and a miss is REFUSED, both out loud and both
    # naming the project (AC-31, AC-290). Silence after "work in project X"
    # reads as done, and the one thing that must never happen is the owner
    # believing they are talking in one project while the screen and the next
    # spawn are in another.
    WORKING_IN = "Working in {name}."
    # STAYS ENGLISH, deliberately, and so do `PROJECT_FAILED` and the three
    # turn-mode confirmations (`TURN_MODE_SAID`, AC-211). They answer a phrase Butler only
    # LISTENS for in English -- `settings.EXPLICIT_PROJECT_PREFIXES`,
    # `SPOKEN_MODES` -- so translating the answer without translating the
    # command would mean replying in Portuguese to a sentence that had to be
    # said in English. Hearing a command in another language is a separate
    # feature the owner has not asked for.
    NO_SUCH_PROJECT = ("I do not have a project called {name}. Nothing "
                       "changed.")
    # Reachable since the project list became every row in the shortcuts pane
    # (backlog item 4): two rows can share a final path component, and picking
    # one is exactly the guess AC-31 refuses. English like the line above and
    # for its reason -- it answers a phrase Butler only listens for in English.
    AMBIGUOUS_PROJECT = ("I have more than one project called {name}. Nothing "
                         "changed.")
    # Reachable when a project has no conversations and one cannot be BORN
    # there: birth runs AC-31's second gate and the `is_dir` check
    # (`_Host.new_conversation`, D-A2), and a row whose folder was renamed
    # fails both. Nothing is started by navigation itself, so the line says
    # what actually happened rather than naming a child.
    PROJECT_FAILED = "I could not open {name}, so nothing changed."
    # A recognizer that ran out of budget delivers the SAME empty Final a
    # quiet room does, so until `Final.failed` existed the daemon answered a
    # lost half-minute of dictation exactly the way it answers silence: with
    # nothing (D-26). Silence is the one wrong ending here, for AC-47's
    # reason one layer up: it is indistinguishable from a dead microphone,
    # and the owner's next move should be to say it again rather than to
    # wait. It reports what happened and offers no advice; what to do about
    # it is theirs.
    ASR_FAILED = "I could not finish transcribing that one."
    # Memory's read-backs (docs/identity-memory.md 4.5, AC-378, AC-379). They
    # STAY ENGLISH, like the turn-mode lines: they answer phrases Butler only
    # listens for in English (`settings.SPOKEN_REMEMBER_PREFIX`).
    REMEMBERED = "I'll remember that {text}."
    REMEMBER_WHAT = "Remember what?"
    FORGOT = "I forgot that {text}."
    NOTHING_TO_FORGET = "There is nothing to forget."
    # The current chat's model by voice (docs/chat-model.md CM-10, AC-787).
    # English, like every line answering an English-only command table. A
    # miss and a tie are ANSWERED and change nothing: never a guess.
    MODEL_SET = "{name} from the next reply."
    MODEL_DEFAULT = "The default model from the next reply."
    NO_SUCH_MODEL = "I don't have a model called {name} on {agent}."
    SEVERAL_MODELS = "Several match {name}; say {choices}."
    MODELS_UNLISTED = "I could not list the models on {agent}, so nothing changed."
    MODEL_FAILED = "I could not change this chat's model, so nothing changed."
    MEMORY_FAILED = "I could not save that note."
    # The scan or the cap refused it (docs/identity-memory.md 12.7, AC-436):
    # said with the reason, because "I could not save that" alone reads as
    # a broken disk.
    MEMORY_REFUSED = "I did not save that: {why}."
    # Recipes and tasks by voice (docs/automations.md 8, AC-451, AC-453).
    # ENGLISH, like the lines above (language.md D5), and every one of them
    # says whether anything changed, so a misheard command is heard at once.
    ADDED_TASK = "Added task: {text}."
    ADD_TASK_WHAT = "Add what task?"
    TASK_REFUSED = "I did not add that: {why}."
    NO_TASKS = "You have no open tasks in {project}."
    TASKS_HEAD = "Your tasks in {project}:"
    TASKS_MORE = "And {n} more."
    MARKED_DONE = "Marked done: {text}."
    NO_SUCH_TASK_NUMBER = ("I have not read you a task {n}. Nothing changed. "
                           "Ask what are my tasks first.")
    NO_SUCH_TASK = "I have no open task called {text}. Nothing changed."
    TWO_TASKS = "{n} open tasks match {text}. Nothing changed."
    NO_SUCH_RECIPE = "I have no recipe called {name}. Nothing started."
    TWO_RECIPES = ("{n} recipes are called {name}. Nothing started. Run it "
                   "from the Library.")
    RECIPE_NEEDS = "That recipe needs a value for {names}. Run it from the Library."
    RUNNING_RECIPE = "Running {name}."
    RECIPE_FAILED = "I could not start {name}."
    LIBRARY_FAILED = "I could not reach the task list."

    def __init__(self, asr, tts, brain, modes, asr_config: AsrConfig,
                 endpointer=None, settings=None, connections=None,
                 shortcuts=None, attachments=None, memory=None,
                 library=None):
        self.asr = asr
        self.tts = tts
        self.brain = brain
        self.modes = modes
        # Where Butler may ask for a completion (plan.md 4.4b). Held like the
        # other stores and NOT a port: a connection is a place to ask, not an
        # engine. None until the brain needs one, so every existing test that
        # builds a Hub keeps working without knowing connections exist.
        self.connections = connections
        # The keyboard binding table and the shortcuts pane's own state
        # (`docs/shortcuts.md` 4.1). A plain store like the one above, held for
        # the same reason and defaulting to None for the same one: no turn
        # touches it, and every existing test that builds a Hub keeps working
        # without knowing shortcuts exist. Nothing in this file reads it -- the
        # routes do.
        self.shortcuts = shortcuts
        # Where a file the owner attached is kept (AC-197, docs/attachments.md).
        # A plain store like the two above, held for their reason and
        # defaulting to None for theirs: no turn touches it, so every existing
        # test that builds a Hub keeps working. The ROUTE writes it and the
        # SESSION holds what is pending; nothing in this file reads the store.
        self.attachments = attachments
        # Notes the owner asked Butler to keep (docs/identity-memory.md 4.5).
        # A plain store like the three above, None in a Hub built without
        # one; `_on_final` writes it on "remember that" and nothing else here
        # touches it -- the persona reads its index (daemon/persona.py).
        self.memory = memory
        # Recipes and tasks (docs/automations.md, AU-1). A plain store like
        # the ones above, None in a Hub built without one; `_on_final` reads
        # and writes it for the spoken "run X" and the task commands, and
        # the routes do the rest.
        self.library = library
        # Automations and their scheduler (docs/automations.md phase 5). Set by
        # `build_hub` after construction, because the scheduler needs this
        # hub; None on every hub a test builds without them.
        self.automations = None
        self.scheduler = None
        # What puts a construction-time setting change into effect
        # (`apply_settings.Applier`, AC-713..AC-716). Set by `build_hub`,
        # started by the app; None on every hub a test builds without it.
        self.applier = None
        # What an agent or the owner SHOWED in a conversation
        # (`artifacts.ArtifactStore`, docs/rich-chat.md RC-3). Set by
        # `build_hub` beside the conversation store that points into it;
        # None on every hub a test builds without one.
        self.artifacts = None
        # The notification channel registry (`notify.Channels`), built by
        # `build_hub` beside the scheduler that sends through it.
        self.channels = None
        # The spoken model choice's rules (`ChatModels`), set by `build_hub`;
        # None on every hub a test builds without it, and then inert.
        self.chat_models = None
        self.asr_config = asr_config
        # The last language refused as unspeakable by the loaded recognizer,
        # so `_speakable_language` says so once instead of once per turn.
        self._language_warned: str | None = None
        # ONE gate over every WHOLE-CLIP decode (AC-116). Two `Session`s on
        # one transcribe.cpp `Model` return EMPTY transcripts on BOTH sides, 9
        # times out of 9, silently (`test/manual/spike_ambient_concurrent.py`),
        # so the ambient back-fill and a dictation may never decode at once.
        # `_overheard` WAITS on it, because it is already sequenced after the
        # live final and can only ever contend with a dictation; `dictate`
        # never waits, because a key released with nothing appearing and text
        # arriving seconds later into whatever is focused by THEN is worse
        # than a refusal.
        self._decoding = asyncio.Lock()
        # Every `TurnPipeline` that may still be using the streaming engine
        # (AC-116). Held here rather than on the session because `_rearm`
        # drops the session's reference while a finalize thread can still be
        # running. `_recognizer_in_use` prunes it.
        self._pipelines: set = set()
        # The chat of the OWNER's latest turn on any device (docs/messaging.md
        # MS-16), set in `_begin_turn`; "" until the first one since boot.
        self.last_owner_chat = ""
        # THE QUESTIONS A CODING AGENT IS BLOCKED ON (AC-234, AC-249, AC-250).
        # In MEMORY, in this process, and NOWHERE ELSE. The argument is
        # `daemon/childproc.py`: every child is in a kill-on-close job object,
        # so a question written to disk is one whose only possible answerer is
        # already dead -- and the next reader would trust it and draw a card
        # nobody can answer. What DOES go on disk is a line in the transcript
        # when the question closes, which is what makes a reload and a rewind
        # honest.
        # `_questions` is qid -> the open record; `_closed` remembers WHY the
        # last few closed, so a late answer is refused with a reason rather
        # than with a shrug.
        self._questions: dict[str, dict] = {}
        self._closed: collections.OrderedDict = collections.OrderedDict()
        # The timer that draws down a deadline the HARNESS gave us. There is
        # deliberately no timer for a question without one: Butler never
        # invents a deadline and never auto-answers (AC-234).
        self._question_timers: dict[str, asyncio.Task] = {}
        # The owner's phone as a second place to answer (docs/messaging.md
        # 7, AC-526): told of every question, it decides which go out, and
        # every answer it reads comes back through `answer_question`.
        self.phone_questions = notify_mod.Questions(self)
        self.endpointer = endpointer
        self.settings = settings
        self.arbiter = arb.WakeArbiter(
            window_ms=(settings.get("arbitration_window_ms", arb.WINDOW_MS)
                       if settings else arb.WINDOW_MS))
        self.sessions: set[SatelliteSession] = set()
        # The rolling transcript every turn's models are given (AC-64,
        # ports.TurnContext.history). It lives on the HUB rather than on a
        # satellite because a turn belongs to the conversation Butler is
        # HOLDING and not to the device that started it -- a phone joining
        # halfway through is joining that one, not starting a second.
        # It follows the conversation rather than the daemon: `_new_
        # conversation` and `open_conversation` both clear it, because the
        # router's short memory of the chat the owner just left would
        # otherwise prompt the first turn of the one they moved to.
        # ONE AT A TIME is still true and this deque is why it has to be:
        # `chats.py` can hold several conversations since AC-239/AC-240, and
        # nothing here is keyed by one. Bounded and never persisted: this is
        # the router's short memory, and the durable record is `chats.py`.
        self.history: collections.deque = collections.deque(
            maxlen=HISTORY_ENTRIES)
        self._turn_seq = 0
        # WHEN THE CURRENT TURN STARTED, per conversation (docs/parallel-
        # agents.md phase 7 item 2, AC-236). Set in `_begin_turn`, beside
        # `turn_by_chat`/`chat_by_turn`, so elapsed is MEASURED rather than
        # guessed and lives on the daemon rather than in a page that can
        # reload. Never explicitly cleared: the next turn for this chat
        # overwrites it, and a status frame is only ever sent while a turn is
        # actually in flight (D-1 -- nothing here can latch a stale time,
        # because nothing reads it once the turn that set it has ended).
        self._turn_started: dict[str, float] = {}
        # A harness turn's TURN ID and `reply_task`, keyed by chat, for a
        # session whose SOCKET closed while the turn was still running
        # (owner's ruling, 2026-09-16: a disconnect must not kill a harness
        # turn). The task is otherwise referenced only by the
        # `SatelliteSession` that is about to leave `self.sessions` -- once
        # nothing else holds it, asyncio can destroy a suspended Task with
        # nobody left to end it ("Task was destroyed but it is pending"),
        # which is a leaked child process and a permanently busy
        # `harness_agents_max` slot (D-1, AC-245). This dict is that
        # reference. The done-callback that removes an entry is added at the
        # SAME point it is added, so it cannot outlive the task it names;
        # `status_for` reads it to answer "running" for a chat with no
        # session left attached at all. The TURN ID rides beside the task
        # (docs/filing-and-pacing.md 1.7) because `Hub.stop_conversation`
        # needs it to call `brain.abort(turn)` -- there is no session left
        # here to read `chat_by_turn` off.
        self._orphaned: dict[str, tuple[int, asyncio.Task]] = {}
        # WHAT EACH RUNNING TURN HAS PRODUCED, per conversation (AC-389..
        # AC-391). Written and removed by `_reply` only, identity-checked on
        # removal so a newer turn of the same chat is never cleared by an
        # older one's unwind. Read by the heartbeat and by the status route
        # through ONE builder, `_progress_frame` (D-18).
        self._progress: dict[str, _TurnProgress] = {}
        # This table IS the protocol v1 text message set. Built once here
        # rather than per frame, and in one place so the rejection below can
        # honestly claim to know what is and is not supported.
        self._handlers = {
            "hello": self._hello,
            "wake_candidate": self._wake_candidate,
            "preroll": self._preroll,
            "capture_end_hint": self._capture_end_hint,
            "playback_done": self._playback_done,
            "mode_request": self._mode_request,
            "stop": self._stop,
            "typed": self._typed,
            "showing": self._showing,
            "caps": self._caps,
            "pong": self._pong,
        }

    def _voice(self) -> str:
        """The voice SETTING for this reply, read per turn so a change on
        the settings page takes effect on the next thing Butler says.

        Blank is the normal, shipped default and means "whatever this engine
        speaks by default" -- resolved by the adapter, not named here. This
        used to return a hardcoded Kokoro voice name, in core turn logic:
        swap the engine and Butler would ask for a voice that does not exist.
        The name is deliberately not repeated in this comment, because
        AC-41's gate is a literal grep over this file and prose naming the
        thing defeats it just as effectively as code would.
        """
        if self.settings is None:
            return ""
        return self.settings.get("voice", "")

    def _speed(self) -> float:
        """The speaking RATE for this reply, read per turn like `_voice`
        (AC-107), so a change takes effect on the next thing Butler says.

        1.0 is the engine's own rate and is what a store-less Hub asks for:
        it is the only value an engine with no rate control can honestly
        claim to honour, so it is also the right answer when there is nobody
        to ask (ports.Tts.synthesize).

        The schema's range is narrow on purpose and the reason lives one
        layer away, which is why it is named here too: `speech.py` caps a
        segment in WORDS to bound the milliseconds AC-4 and AC-5 are written
        in, and speed is exactly the constant that turns words into seconds.
        Widening the range without re-sizing those caps stretches how much of
        a reply survives an interruption, silently.
        """
        if self.settings is None:
            return 1.0
        return float(self.settings.get("tts_speed", 1.0))

    def _speaking(self, s: SatelliteSession | None = None) -> bool:
        """Whether this reply is read ALOUD (AC-110).

        Never for a HEADLESS session (docs/automations.md AU-5, AC-462): a
        scheduled run has no speaker, so with `speak` on it synthesizes
        nothing and waits on no playback -- the muted branch, whose every
        other half (the frame, the brackets, `s.spoken`) is kept.

        Read per turn like `_voice`, so going quiet takes effect on the next
        thing Butler says rather than at the next restart.

        Muting removes the audio and nothing else, and every other half of a
        reply is load-bearing somewhere: the `reply` frame is how the owner
        gets the answer at all, the speak brackets are what the page leaves
        its thinking state on -- `speak_end` means "no more audio is coming",
        which is exactly true of a muted reply -- and the segment still
        enters `s.spoken`, because what the next turn's router is told has to
        be the answer that was delivered (AC-64, D-20). Skipping any of them
        turns "do not speak" into "do not answer", and skipping the brackets
        would hold the microphone (D-1, AC-2).
        """
        if s is not None and s.headless:
            return False
        if self.settings is None:
            return True
        return bool(self.settings.get("speak", True))

    def _turn_language(self, s: SatelliteSession, text: str) -> str | None:
        """What language THIS turn is in. Decided once, before anything is
        said (AC-209).

        Called from `_on_final` on the owner's own words, so the answer is
        known before `_begin_speaking` announces a voice and cannot change
        under a reply that is already playing. One language per turn: a
        sentence that switches half way through is out of scope and is stated
        as a limit in `docs/language-auto.md` rather than half-handled.

        With `language` set to a locale, THAT is the answer and no detector
        runs: the owner pinned it and a classifier second-guessing them is the
        control-that-lies failure pointing at its own setting.

        With `auto`, the text decides -- restricted to the languages the
        speech engine has a VOICE for, because deciding a turn is in a
        language Butler cannot answer in buys nothing (`ports.voice_languages`).

        **AND IT LATCHES.** An abstention -- a one-word turn, a detector that
        would not load -- keeps the language the conversation is already in.
        MEASURED: every long utterance was named correctly and none was named
        wrong, while "yes", "no" and "sim" all fell below the floor
        (`test/manual/spike_language_id.py`, 2026-09-11). So the latch is not
        a safety net bolted on afterwards; it is the half of the design that
        makes short answers work at all, and without it "sim" would flip a
        Portuguese conversation into an English voice.
        """
        setting = self._language()
        if setting and setting != AUTO_LANGUAGE:
            s.language = setting
            return s.language
        detected = dialogue.detect_language(
            text, ports.voice_languages(self.tts.info))
        if detected:
            s.language = detected
        elif s.language is None:
            s.language = self._default_language()
        return s.language

    def _default_language(self) -> str | None:
        """The language a conversation starts in, before anything is said.

        The setting when it names one; otherwise what the ENGINE's own default
        voice speaks, because that is the voice a first turn would be answered
        in and the fixed lines must match the room rather than a constant.
        None when nothing can say -- a store-less Hub, an engine that reports
        no voices -- and None means English for `dialogue.line`, which is what
        every one of these lines said before this existed.
        """
        setting = self._language()
        if setting and setting != AUTO_LANGUAGE:
            return setting
        return self._voice_language(str(self.tts.info.get("voice") or ""))

    def _language(self) -> str:
        """The language SETTING, read per turn (AC-152).

        The same shape as `_voice` above and for the same reason: the
        pipeline is rebuilt for every turn and the engine takes a language
        per `open()`, so a change on the settings page lands on the next
        thing the owner says without a restart. Nothing is reloaded -- the
        GGUF is cached per (path, backend) inside the adapter, and the
        locale is an argument to the stream rather than a property of the
        model.

        Blank means "whatever this recognizer speaks by default" and is
        resolved by the ADAPTER, never here: no locale is named in this file,
        for the reason `_voice` states about voice names.

        `auto` is the one value that is NOT a locale and is Butler's own word
        rather than an engine's (AC-207): it means the turn's language comes
        from what the owner said. Callers that want a locale ask
        `_turn_language` or `_default_language`; nothing downstream of this
        method may pass `auto` to a port.
        """
        if self.settings is None:
            return ""
        return str(self.settings.get("language", "") or "")

    def _speakable_language(self) -> str | None:
        """The language setting, but only if THIS recognizer speaks it.

        The guard exists because the schema's list and the loaded model's
        list are different things and nothing made them agree. `language`
        offers every locale the CATALOGUE's models can do -- it has to, it is
        a static schema and `asr_model` picks the model separately -- while
        the loaded model speaks whatever it speaks. Choosing `pt-BR` with the
        English-only parakeet loaded is therefore reachable, and MEASURED
        2026-09-10 it costs, per turn: `open()` succeeding, one engine error
        per audio FRAME (about 150 lines on a 5 s turn), then `finalize`
        waiting out its whole 15 s floor and ending with "I could not finish
        transcribing that one".

        That is loud, and the microphone does come back -- but a turn that
        dies after fifteen seconds every single time, blaming the decoder for
        a settings mismatch, is D-1's spirit if not its letter: a microphone
        that answers nothing is indistinguishable from one that is off.

        So an unsupported language is dropped to None -- the model's own
        default, which is a language it can actually decode -- and SAID, once
        per distinct mismatch rather than per turn, because a line printed
        150 times is a line nobody reads. This is a degradation and not a
        silent one; the alternative that fails fast fails every turn forever,
        and the setting that would fix it cannot be applied because
        `asr_model` is still inert (D-36).

        An adapter that reports no `languages` at all is trusted, not
        second-guessed: the fake reports none, and a recognizer that will not
        say what it speaks has not earned a veto over the owner's choice.

        **`auto` LEAVES THE RECOGNIZER ALONE** (AC-215), which is the same
        answer blank has always given and is now the measured one rather than
        the merely available one. Unpinned decoded English perfectly AND
        Portuguese better than either pin, while pinning the wrong locale lost
        a whole sentence [MEASURED 2026-09-11,
        `test/manual/spike_asr_unpinned.py`]. The substitution that used to
        happen here -- a model claiming to need a locale was handed its FIRST
        one, which is `en-US` -- went with the `stt_engine` guard that claimed
        it: see that file for why the claim was false. So the whole reason
        `auto` can work on a multilingual recognizer is that nothing pins it.
        """
        speaks = self.asr.info.get("languages") or []
        want = self._language()
        if want == AUTO_LANGUAGE:
            want = ""
        if not want:
            return None
        if not speaks or want in speaks:
            return want
        if self._language_warned != want:
            self._language_warned = want
            print(f"[butler] the language setting is {want!r} but the loaded "
                  f"recognizer speaks {list(speaks)}; decoding in its default "
                  f"instead. Pick a model that speaks {want!r}, or a language "
                  f"it does.", flush=True)
        return None

    def _asr_config(self) -> AsrConfig:
        """`self.asr_config` with the owner's language on it (AC-152).

        THE ONE PLACE a preference reaches the recognizer. The config file
        supplies the model and the adapter's own engine options; the language
        is the owner's and comes from the store, so the two sources never
        both name it -- which is D-35, the defect this exists to close.

        Every caller that opens or decodes goes through here rather than
        reaching for `self.asr_config` directly: live turns, dictation and
        the ambient back-fill all decode the same room, and a language that
        reached only one of them would transcribe the same voice two ways.
        """
        return replace(self.asr_config, language=self._speakable_language())

    def _voice_for(self, language: str | None) -> str:
        """THE voice for a turn in `language`, and it is never blank.

        ONE resolver, and that is the whole of AC-212. `_speak` used to hand
        the engine the RAW `self._voice()` while `speak_start` announced a
        resolved one, so with a blank voice and a Portuguese language the wire
        said a Portuguese voice and the room heard the engine's English
        default reading Portuguese words -- AC-7b's exact shape, inside the
        method whose own docstring said it existed to prevent it. Announced
        and spoken cannot diverge now because there is nothing left to
        diverge: both ask this.

        Blank is legal at the port and not on the wire: the page renders the
        announced string, so "" is a blank label the owner cannot tell from a
        fault. The order is the owner's choice, then the language, then the
        engine's own default.

        **UNDER `auto` THE `voice` SETTING IS NOT CONSULTED AT ALL** (AC-213),
        and that is the one place this method is allowed to ignore a stored
        preference. The owner's live daemon has a Portuguese voice saved; if
        that kept winning, turning automatic language on would answer every
        English turn in a Portuguese voice and the feature would be decoration.
        The page DIMS the control and says why (`Settings.INERT_UNDER_AUTO_LANGUAGE`)
        rather than leaving it looking live -- a setting that is silently
        overruled is AC-7b, and dimming is the existing way to say so (AC-66).
        The value is still stored and still validated, so turning `auto` off
        gives the owner their voice back unchanged.

        A language with NO voice falls through to the engine's default rather
        than to nothing, which is `LOCAL_TARGETS["tts_voices"]`'s rule: the
        owner hears the right words in the wrong accent instead of hearing a
        fault.
        """
        chosen = "" if self._language() == AUTO_LANGUAGE else self._voice()
        if chosen:
            return chosen
        # No language decided yet -- a reply reached without going through
        # `_on_final`, or a route with no session at all -- falls back to the
        # conversation-level answer rather than to "no filter". "No filter"
        # would quietly reinstate the engine's English default for an owner
        # who pinned a locale, which is the AC-156 failure this whole seam
        # exists to remove.
        language = language or self._default_language()
        for_language = ports.voice_ids(self.tts.info, language or "")
        if for_language:
            return for_language[0]
        return str(self.tts.info.get("voice") or "")

    def _voice_language(self, voice: str) -> str | None:
        """What language a named voice speaks, as the ENGINE reports it.

        Read off the VOICE rather than off the setting, and that is the whole
        point of `speak_start.lang` (AC-160). The setting says what was asked
        for; only the voice says what the room will actually hear, and every
        AC-7b failure in this project lives in the gap between those two.
        """
        for entry in self.tts.info.get("voices") or []:
            if entry.get("id") == voice:
                return entry.get("language") or None
        return None

    def _turn_mode(self) -> str:
        return self.settings.turn_mode if self.settings else "patient"

    def _patience(self) -> str:
        return self.settings.patience if self.settings else "balanced"

    def _policy_for_turn(self):
        """Build the end-of-turn policy fresh for every turn.

        Fresh because SmartTurnEnd carries per-turn state (when it last asked
        the model, what it last heard) and EndWordEnd is matched against a
        transcript that only exists inside one turn. Reusing one instance
        would leak the previous turn's answer into this one's first decision.
        """
        return endpointing.build(self._turn_mode(),
                                 endpointer=self.endpointer,
                                 patience=self._patience(),
                                 settings=self.settings)

    def _capture_ceiling_ms(self) -> int:
        """The live safety-net ceiling ONE capture may run to, exactly as
        `TurnPipeline` itself computes it at spawn
        (`ceiling = max(absolute_max_turn_ms, policy.max_turn_ms)`,
        pipeline.py). A READ of that same arithmetic rather than a second
        copy of it (D-18): the page's own anti-latch watchdog is honest only
        if it tracks what the daemon actually intends to wait, per MODE,
        instead of a flat guess (docs/voice-control.md section 0, AC-92's
        reverted 30000-vs-300000 experiment). `endword` reports its own much
        larger ceiling (`EndWordEnd.max_turn_ms`); every other mode falls
        back to the ordinary conversational one.
        """
        from .pipeline import ABSOLUTE_MAX_TURN_MS
        absolute = (self.settings.get("absolute_max_turn_ms", ABSOLUTE_MAX_TURN_MS)
                   if self.settings else ABSOLUTE_MAX_TURN_MS)
        policy_ms = getattr(self._policy_for_turn(), "max_turn_ms", 0)
        return int(max(absolute, policy_ms))

    def _setting(self, key: str):
        """A setting's value, or its schema default on a Hub built with no
        settings store (most hub tests): the schema is the one copy."""
        return (self.settings.get(key) if self.settings
                else settings_mod.DEFAULTS[key])

    def _wake_role(self, word: str) -> str | None:
        """The role of wake word `word` (`off`/`screen`/`new`), or None for a
        word the catalogue does not hold (AC-807)."""
        if not any(w.id == word for w in settings_mod.WAKE_WORDS):
            return None
        return self._setting(settings_mod.wake_key(word))

    WAKE_SETTINGS = frozenset(
        [settings_mod.wake_key(w.id) for w in settings_mod.WAKE_WORDS]
        + ["wake_threshold", "wake_cooldown_ms"])

    def wake_frame(self) -> dict:
        """What the page's detector loads: the words that are on, in catalogue
        order, with the threshold and cooldown (AC-805). ONE builder for
        `ready.wake` and the `wake` broadcast (D-18); a change to any of
        `WAKE_SETTINGS` is broadcast (`butlerd._announce_settings`)."""
        return {
            "words": [{"id": w.id, "phrase": w.phrase, "url": w.url}
                      for w in settings_mod.WAKE_WORDS
                      if self._wake_role(w.id) != "off"],
            "threshold": self._setting("wake_threshold"),
            "cooldown_ms": self._setting("wake_cooldown_ms"),
        }

    def mint_turn(self) -> int:
        self._turn_seq += 1
        return self._turn_seq

    async def broadcast(self, **payload) -> None:
        for s in list(self.sessions):
            await s.send(**payload)

    async def show_artifact(self, chat: str, role: str, entry: dict) -> None:
        """One thing SHOWN in `chat` (docs/rich-chat.md RC-4): onto the next
        `role` message of that conversation's record, and to every page as
        an `artifact` frame (plan.md 4.3). Broadcast rather than sent for a
        turn, because a tool's POST arrives on no socket; the page draws it
        only for the conversation on screen, and a reload draws it from the
        record."""
        brain = self.session_brain()
        if brain is None:
            raise LookupError("this brain mode keeps no conversation record "
                              "to show anything in")
        brain.show(chat, role, entry)
        await self.broadcast(type="artifact", chat=chat, role=role,
                             artifact=entry)

    async def _show_attachments(self, chat: str, taken: tuple) -> tuple:
        """The owner's own attachments, shown in THEIR turn (rich-chat O-2).
        Kept as snapshots like an agent's, because the attachment folder is
        the agent's to read and not the transcript's. Loud and never fatal
        (D-1): a copy that fails costs the picture in the transcript, never
        the turn or the attachment itself."""
        if not taken or not chat or self.artifacts is None                 or self.session_brain() is None:
            return taken
        for att in taken:
            try:
                entry = await asyncio.to_thread(
                    self.artifacts.snapshot, chat, att.path, None, att.name)
                await self.show_artifact(chat, "user", entry)
            except (OSError, ValueError, LookupError) as e:
                print(f"[butler] WARNING: attachment {att.name!r} is not "
                      f"shown in the transcript: {e}", flush=True)
        return taken

    # -- the WebSocket handler ---------------------------------------------- #
    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if not ws_origin_ok(request):   # RV-25, AC-612: before any upgrade
            raise web.HTTPForbidden(text="cross_site_origin")
        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024,
                                   heartbeat=20)
        await ws.prepare(request)
        session = SatelliteSession(self, ws)
        session.peer = request.remote or ""
        session.listener = listener_of(request)
        session.paired = request.get(PAIRED)
        self.sessions.add(session)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if session.pipeline is not None:
                        session.pipeline.feed(msg.data)
                    # Audio outside a capture is not an error; a satellite may
                    # still be draining its pre-roll when a turn is abandoned.
                    continue
                if msg.type == WSMsgType.ERROR:
                    break
                if msg.type != WSMsgType.TEXT:
                    continue
                await self._on_text(session, msg.data)
        finally:
            session.alive = False
            # A disconnect must not kill a harness turn (owner's ruling,
            # 2026-09-16). `abandon()` aborts the brain and cancels
            # `reply_task` whenever the front-door turn is THINKING or
            # SPEAKING -- correct for a barge-in, wrong for a socket that
            # merely closed while a coding agent was doing real work in a
            # child process nothing here needs to stop. Everything else
            # (no turn, endpoint mode, no live child) gets the old
            # teardown unchanged.
            if self._turn_survivable(session):
                self._detach_survivable(session)
            else:
                await session.abandon()
            self.sessions.discard(session)
        return ws

    def _turn_survivable(self, s: SatelliteSession) -> bool:
        """Should `s`'s FRONT-DOOR turn keep running after its socket
        closes?

        Only a brain that `HAS_SESSIONS` holds a conversation worth
        recovering at all -- endpoint mode has no session store, so there
        is no chat id a reconnecting page could ever ask
        `GET /api/chats/{cid}/status` about, and the old teardown is
        correct there unchanged. And only a chat with a LIVE CHILD is real
        background work: a turn the router answered itself
        (route=answer/clarify) never touched `_Host` at all, so there is
        nothing running in the background to protect.
        """
        if s.turn is None:
            return False
        if not getattr(self.brain, "HAS_SESSIONS", False):
            return False
        host = getattr(self.session_brain(), "host", None)
        if host is None:
            return False
        chat = s.chat_by_turn.get(s.turn, "")
        return bool(chat) and host.child(chat) is not None

    def _detach_survivable(self, s: SatelliteSession) -> None:
        """Let a harness turn's `reply_task` keep running with no session
        attached, instead of the abort-and-cancel `abandon()` does.

        Ownership is left untouched on purpose -- `s.turn`, `turn_by_chat`
        and `chat_by_turn` still say this (now-discarded) session owns the
        turn, so `_reply`'s own `s.owns(turn)` checks keep answering True
        and it runs to its natural end exactly as it would with the socket
        open. That is safe with no socket at all: `send()` and
        `send_for_turn()` already no-op on a closed `ws` (D-6, AC-5), and
        `_let_go` and `_await_playback` are already bounded (D-1), so
        nothing in the reply path can hang waiting for a satellite that is
        gone.

        `self._orphaned` is the strong reference that keeps the task alive
        once `session` leaves `self.sessions` (see its own comment). What
        ENDS a survived turn is nothing new: the harness's own terminal
        event (`AgentEnd`/`Failed`) or, if the child goes silent, D-34's
        `HARNESS_IDLE_S` -- both already reach `_reply`'s `finally`, which
        calls `s._rearm(turn)` unconditionally and needs no I/O to do it.

        `s.task`, the capture pipeline, is left alone rather than
        cancelled: it has nothing left to read (no more audio will arrive
        on a closed socket) and already winds down on its own from
        `alive=False`, set by the caller before this runs.
        """
        task = s.reply_task
        turn = s.turn
        chat = s.chat_by_turn.get(turn or -1, "")
        if task is None or task.done() or not chat or turn is None:
            return
        self._orphaned[chat] = (turn, task)
        task.add_done_callback(lambda _t, c=chat: self._orphaned.pop(c, None))

    async def _on_text(self, s: SatelliteSession, raw: str) -> None:
        try:
            m = json.loads(raw)
        except json.JSONDecodeError as e:
            # Loud, never swallowed -- and never fatal to the socket. Killing
            # the connection on a bad frame is the microphone-off failure.
            await s.send(type="error", message=f"malformed json: {e}")
            return
        if not isinstance(m, dict):
            await s.send(type="error", message="frame must be a json object")
            return

        kind = m.get("type")
        handler = self._handlers.get(kind)
        if handler is None:
            await s.send(
                type="error",
                message=(f"unsupported message type {kind!r} in protocol v{PROTO}"
                         + (" -- endpointing is server-side; there is no "
                            "check_turn (plan.md 4.2)"
                            if kind == "check_turn" else "")))
            return
        await handler(s, m)

    # -- handlers ------------------------------------------------------------ #
    async def _hello(self, s: SatelliteSession, m: dict) -> None:
        s.device_id = str(m.get("device_id") or "unknown")
        s.name = str(m.get("name") or s.device_id)
        s.caps = m.get("caps") or {}
        # All three engines, because "which engine is actually running" is a
        # question about the whole daemon and reporting one of three answered
        # it for the least surprising one. Every `info` here is a synchronous
        # property read ON THE EVENT LOOP, which is why the port docstrings
        # forbid loading or connecting inside one: this runs every time a
        # device connects, and a blocking read freezes all of them (AC-42).
        # `session` is None outside harness mode, and a cheap in-memory
        # summary inside it -- the same D-25 rule the three `info` reads
        # follow: this runs on the event loop every time a device connects,
        # so it may not go and look anything up (AC-35).
        brain = self.session_brain()
        # `chat` IS WHICH CONVERSATION THIS FRESH DEVICE IS ON (AC-246), and
        # it is not the same fact as `session`: that one is the harness's
        # summary and is None outside harness mode, while this is the answer
        # to "if I talk now, where does it go" -- which is `chat_for(s)`, the
        # one resolver, so a reconnecting device that has not re-declared its
        # view is told exactly what the daemon would do with its next word.
        # `capture_ceiling_ms` rides beside `turn_mode` (AC-92's other half):
        # the number this page's own watchdog should give one capture before
        # giving up on its own side, so a fresh connection or an older page
        # that ignores the field both still have a fallback.
        await s.send(type="ready", proto=PROTO, mode=self.modes.mode,
                     device_id=s.device_id, stt=self.asr.info,
                     tts=self.tts.info, brain=self.brain.info,
                     turn_mode=self._turn_mode(),
                     capture_ceiling_ms=self._capture_ceiling_ms(),
                     wake=self.wake_frame(),
                     chat=self.chat_for(s),
                     session=brain.session_state() if brain else None)

    async def _barge_in(self) -> None:
        """End every turn in flight that is AUDIBLE, across every device.

        Barge-in is about the ROOM, not about every conversation a device
        happens to be tracking. Across DEVICES: nothing from an old turn can
        be emitted afterwards (AC-5), and `abandon()` is the existing
        terminal path, so every session it touches re-arms (D-1).

        Doing this per-session was wrong in a way the tests could not see:
        with one satellite it looks identical, but say the wake word at the
        phone while the desk is mid-reply and the desk just keeps talking over
        the answer you are about to get. AC-5's test passes either way, which
        is what made cross-device scope worth writing down, and it is
        UNCHANGED here.

        **SCOPED BY CONVERSATION SINCE `docs/parallel-agents.md` phase 8**
        (partial: `_barge_in` only). A session's front-door turn is ended
        only if that session would actually SPEAK it -- `other.hears(...)`,
        the D4 predicate (AC-238) `_speak` already mutes by, given the front
        door's own conversation (`other.chat_by_turn.get(other.turn, "")`,
        `_speak`'s exact idiom). A device that looked away mid-reply is
        already running that turn written-not-spoken; a wake word or typed
        message ELSEWHERE is not the room interrupting a turn making no
        sound, and abandoning it in harness mode would dispose the coding
        agent the owner is not even looking at (AC-242, AC-243's argument one
        layer up). A turn skipped here is left EXACTLY as it was -- no new
        state, nothing latched, D-1 untouched -- because `hears()` answers
        True for every session that has not declared a view at all (the
        hands-free default), which is every session before this change and
        every existing test: nothing that was audible before is spared now.

        The conversation the NEW turn (not yet minted here) will belong to is
        deliberately NOT consulted: neither caller knows it yet at this point
        (`_wake_candidate` calls this before arbitration decides a winner;
        `_typed` calls this before `_begin_turn` mints anything), and it would
        not change the answer if they did -- what decides whether an OLD turn
        must stop is whether it is making sound right now, not what the new
        one turns out to be about.

        TWO callers, and that is why it has a name: a wake word (`_wake_candidate`)
        and a typed message (`_typed`, R7 in `docs/familiar-ui.md`). A second
        copy of a teardown is precisely the drift D-18 is about, and the copy
        that drifts is the one that leaves a microphone held.
        """
        for other in list(self.sessions):
            # A headless session (a scheduled run) makes no sound, so the
            # room cannot interrupt it: the owner's wake word ends nothing a
            # schedule started (docs/automations.md AU-5, AC-460's mirror).
            if other.state == IDLE or other.headless:
                continue
            turn_chat = other.chat_by_turn.get(other.turn, "")
            if other.hears(turn_chat):
                await other.abandon()

    def _begin_turn(self, s: SatelliteSession) -> int:
        """Mint a turn and give this session a clean slate for it.

        THE DAEMON MINTS THE ID, on both paths into a turn (AC-163). A
        satellite naming a turn it does not own is D-6, which is why the typed
        message carries no id at all and any the page sent would be ignored.

        Shared by the spoken path (`_start_capture`) and the typed one
        (`_typed`) because a typed turn is a PEER of a spoken one (AC-162): a
        second copy of this reset would drift, and the field it forgot -- a
        stale `spoken` list, a `playback_done` already set, an ambient clip
        from the turn before -- fails silently in whichever path was not
        edited.
        """
        turn = self.mint_turn()
        s.turn = turn
        # WHICH CONVERSATION THIS TURN IS IN, fixed here and never re-read
        # (AC-246). Both doors pass through this method, so a spoken turn and
        # a typed one name their conversation the same way. Fixed rather than
        # resolved on demand because the daemon's pointer can move mid-turn --
        # another device opening a conversation would otherwise silently
        # re-address a reply that is already half spoken, and the mute in
        # `_speak` would compare against a conversation this turn was never in.
        chat = self.chat_for(s)
        s.turn_chat = chat
        # OWNERSHIP, per conversation (docs/parallel-agents.md phase 6, item
        # 1). A turn already live for THIS chat is superseded -- the old D-6
        # stale-turn-discard rule, now scoped to one conversation instead of
        # the whole session -- while a turn live for a DIFFERENT chat is left
        # alone, which is what lets two conversations both keep emitting on
        # one socket.
        s.turn_by_chat[chat] = turn
        s.chat_by_turn[turn] = chat
        # The chat the OWNER last spoke, typed or messaged in, from any
        # device (docs/messaging.md MS-16): `phone_chat: current` answers a
        # WhatsApp message there. A scheduled run is not the owner.
        if s.owner_turn and chat:
            self.last_owner_chat = chat
        # ELAPSED, MEASURED rather than guessed (AC-236). Beside the two
        # dicts above for the same reason they are fixed here: both doors
        # into a turn pass through this method, so a spoken turn and a typed
        # one time themselves the same way.
        self._turn_started[chat] = time.time()
        # Told about, and therefore correctable (AC-97). Here rather than at
        # the wire for the reason this method exists at all: both doors into
        # a turn pass through it, so a spoken turn and a typed one are equally
        # correctable without either path knowing about the other.
        s.seen_turns.append(turn)
        s.spoken = []
        s.interrupted = False
        # A new turn drops whatever the previous one never used (AC-72). The
        # keyed read in `take_backfill` already makes a stale clip unreachable;
        # this is what stops it being HELD until the turn after that.
        s.ambient = b""
        s.ambient_turn = None
        s.audio_ms_sent = 0.0
        s.playback_done = asyncio.Event()
        s.speaking_started = False
        return turn

    async def _typed(self, s: SatelliteSession, m: dict) -> None:
        """`{"type": "typed", "text": "...", "corrects": n?}` -- the owner
        typed a question, or typed the one Butler misheard (AC-97).

        AC-162: typing is a PEER of speaking, not a second kind of turn.
        Everything past this method is the path a spoken utterance already
        takes -- the same `_on_final`, so the three daemon-answered utterances
        still work typed; the same router, the same brain, the same
        `_remember`, the same record and the same `_rearm` (D-1).

        Three rules, and none of them is new machinery:

        * **The daemon mints the id** (AC-163). Anything in the body that
          looks like one is ignored -- there is no read of `m["turn"]` here at
          all, because a satellite naming a turn it does not own is D-6.
        * **A typed turn obeys the EXISTING `speak` setting** (R6,
          `docs/familiar-ui.md` 2). There is deliberately no branch for it:
          `_speak` reads `_speaking()` per reply exactly as it does for a
          spoken turn, so a typed question is answered out loud precisely when
          a spoken one would be. The cost the owner accepted is that typing
          quietly also means turning speech off.
        * **A typed message CANCELS a capture in flight** (R7). Pressing Enter
          is deliberate and a live capture is provisional, so the newer intent
          wins -- through `_barge_in`, the path a wake word already takes, and
          never a second teardown.

        No `capture_start` is sent, because nothing is being captured: that
        frame means "you won arbitration, start streaming the microphone", and
        sending it would put the page into a capture it is not in (AC-7b). The
        `final` frame `_on_final` emits is the first the page sees for this
        turn and is what carries the id, with `reason="typed"`.

        Refused rather than started, and the refusal is an `error` frame that
        changes nothing: an empty message is not a turn, and a paste past the
        ceiling is not truncated into one. Both are checked BEFORE the
        barge-in, so a message Butler will not act on cannot cost the owner a
        capture they were half way through.

        **A CORRECTION IS A NEW TURN, NEVER AN EDIT** (AC-97). `corrects` is
        optional and names the turn this one supersedes. It changes NOTHING
        about how the turn runs -- same barge-in, same freshly minted id, same
        `_on_final`, same router, same brain, same `_remember` -- and that is
        the criterion rather than a shortcut: the original utterance stays in
        the rolling transcript and in `chats.py` exactly as it was heard,
        because Butler's record is what was SAID and a record that could be
        rewritten would be a record nobody can trust (D-20). Nothing here
        deletes, amends or hides the turn being corrected.
        **"AND THERE IS NO ROUTE IN THIS DAEMON THAT COULD" WAS TRUE UNTIL
        2026-09-10 AND IS NOT NOW** (AC-189, `docs/rewind.md`, the owner's
        ruling). `POST /api/session/rewind` truncates the live conversation at
        a message and gives its text back to the composer. The argument above
        is kept whole because half of it still binds and the half that lost is
        worth knowing: a conversation is not only a record, it is also the
        agent's working context, and there is no way to say "forget the last
        three exchanges" to a model except by taking them out of the transcript
        it resumes from. What survives of D-20 is that nothing is SILENTLY
        rewritten -- a rewind copies the whole original into an ordinary
        conversation in the sidebar before it truncates anything, so the
        dropped turns are still on disk and one click away.
        None of that reaches THIS method: a correction is still a new turn,
        still mints its own id, and still edits nothing.
        RETRY needs none of this and has no field: re-asking is the same text
        typed again, which is an ordinary turn, and a second way to say
        something the protocol already says is worse than no feature.

        The reference is REFUSED, never guessed at, and it is the one id the
        page may name (D-6). A turn this session was not told about -- one
        another satellite owned, one from before this socket opened, one
        nobody ever minted -- is an `error` frame and no turn at all, checked
        beside the other two refusals and for their reason. What it may name
        is `s.seen_turns`, bounded at `CORRECTABLE_TURNS`.

        It rides back out on the `final` frame, which is the one frame that
        exists exactly once per turn and already names it. That is what lets
        the page attach the pair without GUESSING which final is its own: the
        microphone stays live while the composer has focus (AC-162), so a
        wake word can land between the send and the answer, and a page
        assuming "the next final is mine" would hang the correction off a
        spoken turn.
        """
        text = m.get("text")
        if not isinstance(text, str):
            await s.send(type="error",
                         message="typed needs a text string")
            return
        text = text.strip()
        if not text:
            await s.send(type="error",
                         message="typed needs some text; an empty message is "
                                 "not a turn")
            return
        if len(text) > TYPED_MAX_CHARS:
            await s.send(type="error",
                         message=(f"typed text is {len(text)} characters and "
                                  f"the ceiling is {TYPED_MAX_CHARS}; nothing "
                                  f"was started and nothing was truncated"))
            return
        corrects = m.get("corrects")
        if corrects is not None:
            # `bool` is a subclass of `int`, so an unguarded check would let
            # `true` name turn 1 -- a JSON encoder away from a page
            # correcting the wrong turn without ever saying a number.
            if isinstance(corrects, bool) or not isinstance(corrects, int):
                await s.send(type="error",
                             message=("corrects must be the id of a turn this "
                                      "daemon minted, or absent"))
                return
            if corrects not in s.seen_turns:
                await s.send(
                    type="error",
                    message=(f"turn {corrects} is not a turn this device was "
                             f"told about, so there is nothing to correct; "
                             f"nothing was started. Send it as an ordinary "
                             f"typed message instead"))
                return

        # A HEADLESS session (a scheduled run, docs/automations.md AU-5) is
        # not in the room, so it interrupts nothing: a run starting at 09:00
        # must never cut off the owner's own reply (AC-460).
        if not s.headless:
            await self._barge_in()
        turn = self._begin_turn(s)
        if corrects is not None:
            print(f"[butler] turn {turn} corrects turn {corrects}: a new turn "
                  f"that supersedes an older one and never an edit of it -- "
                  f"the original stays in the record (AC-97, D-20)")
        # THINKING from the moment the turn exists, not once the brain is
        # reached: `abandon()` only aborts the brain from THINKING or SPEAKING,
        # so a turn sitting IDLE in this window would survive its own barge-in.
        s.state = THINKING
        # A TASK, for the reason the spoken path's final is one: `_on_final`
        # can await a whole spoken answer (the mode change, the new
        # conversation, the project switch), and awaiting it here would block
        # this session's socket read loop -- so the page could not send `stop`
        # for the turn it is watching (AC-111). Parked on `s.task`, which the
        # pipeline owns on a spoken turn and which is free on this one, so
        # `abandon()` cancels it with no new machinery.
        # The session names its reason (`typed_reason`). A scheduled run's
        # text is a FILLED RECIPE nobody said and a WhatsApp message came
        # from a phone, so both are WORK, never a spoken command (AC-451's
        # rule, `WORK_REASONS`) -- a prompt reading "run X" must not run a
        # recipe, and a phone must not move the desk to another project.
        s.task = asyncio.create_task(
            self._on_final(s, turn, ports.Final(text=text), s.typed_reason,
                           corrects=corrects))

    async def _wake_candidate(self, s: SatelliteSession, m: dict) -> None:
        # THE WORD FIRST (AC-807): a word the catalogue lacks, or one that is
        # off, is refused before `_barge_in` and before arbitration, so it
        # ends no turn anywhere and cannot win (or crowd) a window.
        word = m.get("word")
        role = self._wake_role(word) if isinstance(word, str) else None
        if role is None or role == "off":
            reason = "unknown_word" if role is None else "word_off"
            print(f"[butler][wake] device={s.device_id} word={word!r} "
                  f"refused: {reason}")
            await s.send(type="wake_reject", reason=reason)
            return

        # Before the new turn is minted, so nothing from an old one can be
        # emitted afterwards (AC-5).
        await self._barge_in()

        try:
            score = float(m.get("score", 0.0))
        except (TypeError, ValueError):
            await s.send(type="error", message="wake_candidate needs a numeric score")
            return

        ranked = await self.arbiter.submit(arb.Candidate(
            device_id=s.device_id, score=score, session=s,
            client_ts_ms=m.get("ts_ms"),
            recv_ms=time.monotonic() * 1000, word=word))
        if not ranked:
            return          # a rival opened the window; it announces the result

        winner = ranked[0]
        for c in ranked[1:]:
            await c.session.send(type="wake_reject", reason="lost_arbitration")
        # The WINNER's word decides, after arbitration and before the turn is
        # minted (AC-807), so `capture_start` names the new conversation. In
        # brain only there are no conversations and `new` is `screen`.
        w = winner.session
        if self._wake_role(winner.word) == "new" and \
                self.session_brain() is not None:
            try:
                born = await self._birth_here(w)
            except Exception as e:                    # noqa: BLE001
                # REJECTED, not carried on in the old conversation: the owner
                # asked for a fresh one, and words meant for it must not reach
                # the agent in the one on screen. No turn was minted, so the
                # page's `wake_reject` re-arm is the whole recovery (D-1).
                print(f"[butler][wake] device={w.device_id} word="
                      f"{winner.word}: the new conversation could not be "
                      f"started: {e!r}")
                await w.send(type="wake_reject", reason="new_failed")
                return
            print(f"[butler][wake] word={winner.word} started conversation "
                  f"{born['id']} in {born['project']} (AC-807)")
        await self._start_capture(w)

    async def _start_capture(self, s: SatelliteSession) -> None:
        turn = self._begin_turn(s)
        s.state = CAPTURING

        loop = asyncio.get_running_loop()

        def on_partial(p):
            asyncio.run_coroutine_threadsafe(
                s.send_for_turn(turn, type="partial", committed=p.committed,
                                tentative=p.tentative), loop)

        def on_final(final, reason):
            asyncio.run_coroutine_threadsafe(
                self._on_final(s, turn, final, reason), loop)

        async def on_closing():
            await s.send_for_turn(turn, type="closing")

        from .pipeline import TurnPipeline
        # Pruned here as well as in `_recognizer_in_use`, so a daemon that is
        # never dictated to does not hold one dead pipeline per turn.
        self._pipelines = {p for p in self._pipelines if p.uses_recognizer()}
        s.pipeline = TurnPipeline(
            turn=turn, asr=self.asr, on_partial=on_partial, on_final=on_final,
            asr_config=self._asr_config(), endpointer=self.endpointer,
            policy=self._policy_for_turn(), on_closing=on_closing,
            settings=self.settings)

        self._pipelines.add(s.pipeline)
        trace.trace("capture_start", turn=turn, device=s.device_id[:8])
        # `chat` NAMES THE CONVERSATION THIS SPOKEN TURN IS IN (AC-246). The
        # frame that says "you won arbitration, start streaming" is the first
        # thing a device learns about its own turn, and what it starts may be
        # a full-capability coding agent in a directory nobody said out loud.
        # Empty when this daemon holds no conversations at all.
        await s.send(type="capture_start", turn=turn,
                    chat=s.chat_by_turn.get(turn, ""))
        s.task = asyncio.create_task(s.pipeline.run(alive=lambda: s.alive))

    async def _preroll(self, s: SatelliteSession, m: dict) -> None:
        """Header for the back-fill flush. The client's `turn` is ignored --
        the server already knows which turn this session owns."""
        if s.state != CAPTURING:
            await s.send(type="error", message="preroll outside a capture")
            return
        # The declared byte count is VALIDATED, not enforced: the flush that
        # follows is ordinary PCM on the same socket and the pipeline takes it
        # as it arrives. Rejecting a non-integer is still worth doing -- it is
        # a client bug, and a client that miscounts its own buffer is about to
        # lose the first syllables of the command (AC-1a).
        try:
            declared = int(m.get("bytes", 0))
        except (TypeError, ValueError):
            await s.send(type="error", message="preroll bytes must be an integer")
            return
        # `lookback` is how much of the flush was captured BEFORE the wake
        # word fired: the turn starts there (AC-800). It is REQUIRED (AC-802):
        # without it the wake phrase arms the silence clock and the turn ends
        # before the owner speaks, so a header that cannot say it is refused
        # rather than guessed at.
        try:
            lookback = int(m["lookback"])
        except (KeyError, TypeError, ValueError):
            await s.send(type="error",
                         message="preroll lookback must be an integer")
            return
        if not 0 <= lookback <= declared:
            await s.send(type="error",
                         message=f"preroll lookback {lookback} is outside the "
                                 f"{declared} bytes flushed")
            return
        # The pipeline reads it for one thing only: where this turn's speech
        # may begin. Every byte still reaches the recognizer as ordinary audio.
        if s.pipeline is not None:
            s.pipeline.note_lookback(lookback)

    async def _capture_end_hint(self, s: SatelliteSession, m: dict) -> None:
        if s.pipeline is not None:
            await s.pipeline.finalize_now()

    async def _playback_done(self, s: SatelliteSession, m: dict) -> None:
        """The satellite's speaker has actually gone quiet.

        Ignored unless it names the current turn: a late confirmation for an
        abandoned reply must not release the turn the user is now in.
        """
        if s.owns(m.get("turn")):
            s.playback_done.set()

    async def _stop(self, s: SatelliteSession, m: dict) -> None:
        """`{"type": "stop", "turn"?: n, "chat": id}` -- the page ends the
        turn of the conversation on screen (AC-111).

        Stop stops the SPEECH and the TURN, which is the owner's ruling of
        2026-08-19 (docs/voice-control.md section 3, D1 = B) rather than a
        reading of the word: this daemon runs `harness_only`, so the thing
        worth stopping is usually the coding agent and not the sentence. It
        is also the only irreversible act on the page -- the agent's partial
        work is gone -- and that is why it is deliberate rather than a guess
        about what "stop" meant.

        IGNORED unless it names a turn this session is actually on (D-6).
        The daemon mints turn ids, so a `stop` for anything else is a stale
        press -- the owner hit it as a wake word started the next turn -- and
        acting on it would take the microphone away from the turn they are
        now in, exactly as a late `playback_done` would.

        `abandon()` rather than a second copy of it: barge-in already does
        every part of this and is the proven path -- `tts.abort(turn)` so the
        abort reaches the ENGINE (the kokoro sidecar's own `/abort`; an abort
        that only stops the daemon's wait reads as working and is D-4), the
        brain's abort, which in harness mode is `abort()` then `dispose()`,
        and the cancel-and-re-arm that leaves the session IDLE. A second copy
        is precisely the drift D-18 is about. Nothing new is sent: the turn's
        own terminal frames already went out, and anything sent after this
        names a turn the page no longer owns and is dropped at the wire.

        **THE FRAME NAMES THE CONVERSATION ON SCREEN** (`docs/parallel-
        agents.md` D5; the 2026-09-26 e2e audit, S18): `{"type": "stop",
        "turn"?: n, "chat": id|null}`. Two defects lived here. The named
        turn was only CHECKED -- `owns()` -- and then `abandon()` ended the
        FRONT DOOR, so a stop naming a background conversation's turn killed
        whatever this device had started last (D-18: the callee's own tests
        were green). And a page that RELOADED mid-turn knows its
        conversation is running but not the turn id, which belongs to the
        socket that started it, so it had no way to stop it at all.

        With a turn: it must be owned here AND be in the named chat, or the
        press is stale and ignored; the turn itself is what is abandoned.
        With only a chat: the turn in flight for it, wherever it is owned,
        through `stop_conversation` -- and ONLY a turn in flight, because a
        stop that raced the turn's own end must not dispose a held child.
        """
        turn = m.get("turn")
        chat = m.get("chat")
        if chat is not None and not isinstance(chat, str):
            await s.send(type="error",
                         message="stop.chat must be a conversation id or null")
            return
        if turn is not None:
            if not s.owns(turn):
                return
            if chat is not None and s.chat_by_turn.get(turn) != chat:
                return
            await s.abandon(None if turn == s.turn else turn)
            return
        if chat and self._turn_in_flight(chat):
            await self.stop_conversation(chat)

    async def stop_conversation(self, chat_id: str) -> bool:
        """Stop a NAMED conversation's turn, wherever it is owned -- the
        general capability `docs/filing-and-pacing.md` 1.7 found missing.
        D4 = A: the general capability, not a narrow one-off scoped to the
        confirm-to-stop gesture, on the spec's own argument that a narrow
        version still has to solve the hard part to work at all.

        Unlike `_stop`, the CALLER here is never assumed to be the session
        that owns the turn -- a page action naming a conversation on
        screen is stopping something that may belong to a different
        device, or to no device at all.

        REUSES `SatelliteSession.abandon()`'s exact abort-then-rearm
        sequence rather than a second teardown (D-18): the only new code
        here is FINDING which session, if any, owns the turn. Four cases,
        and this is the whole of what tells them apart:

        - A turn tracked in some session's `turn_by_chat` (its own front
          door, or -- rarely -- a turn it is merely tracking in the
          background): `s.abandon(turn)` does the abort and the ownership
          cleanup, front-door state included when it applies.
        - An ORPHANED turn (`self._orphaned`, `017f8a1`): no session is
          left to call `abandon` on at all, so the brain is aborted
          directly; the surviving `_reply` task's own `finally` does the
          rest once the aborted stream ends, exactly as it would with a
          session still attached.
        - No turn in flight, but a HELD child (AC-59, idle): a DISPOSE,
          never an abort -- there is nothing running to interrupt.
        - Neither a turn nor a held child: raise rather than silently
          report success for a conversation this daemon has nothing to
          stop (root AGENTS.md's fail-fast rule) -- covers both "this chat
          id does not exist" and "it exists but never held a child".

        Returns True once something was actually stopped or disposed.
        """
        for s in list(self.sessions):
            turn = s.turn_by_chat.get(chat_id)
            if turn is not None:
                await s.abandon(turn)
                return True
        orphan = self._orphaned.get(chat_id)
        if orphan is not None:
            turn, _task = orphan
            try:
                await asyncio.wait_for(self.brain.abort(turn),
                                       timeout=BRAIN_LETGO_S)
            except Exception as e:                        # noqa: BLE001
                # Same posture as `abandon()`: we are here to stop it, not
                # to hear about it (D-1) -- and there is no session state
                # here to leave half-reset even if this raised.
                print(f"[butler] brain abort failed for orphaned chat "
                      f"{chat_id!r} turn {turn}: {e!r}")
            return True
        host = getattr(self.session_brain(), "host", None)
        if host is not None and host.child(chat_id) is not None:
            await host.dispose(chat_id)
            return True
        raise LookupError(
            f"chat {chat_id!r} has no turn in flight and no held child -- "
            f"there is nothing here to stop")

    async def _mode_request(self, s: SatelliteSession, m: dict) -> None:
        """Down by voice is instant; up by voice is refused (4.6 asymmetry)."""
        to = str(m.get("to") or "")
        try:
            self.modes.set(to, authorized=False)
        except Exception as e:                       # noqa: BLE001
            await s.send(type="error", message=str(e))
            return
        await self.broadcast(type="mode", mode=self.modes.mode)

    def session_brain(self):
        """The brain, when it holds a conversation that outlives a turn.

        The same capability rule as `_working_brain` and for the same reason
        (AC-44): endpoint mode has no session store and no context to lose, so
        the spoken rotation and the session route simply do not apply to it,
        and the next brain that drives a harness declares `HAS_SESSIONS`
        rather than editing this file.
        """
        return self.brain if getattr(self.brain, "HAS_SESSIONS", False) else None

    # -- a coding agent's question (AC-234, AC-249, AC-250) ------------------ #
    def questions_for(self, chat_id: str) -> list[dict]:
        """Every outstanding question in one conversation, oldest first.

        Served by `GET /api/session/current` and by nothing else, which is
        what makes recovery need NO protocol (`docs/parallel-agents.md` 9.4):
        a reloaded tab and a second device both ask the same route they were
        already asking, and a daemon that restarted has no agent left to
        answer so it correctly reports none. MCP deleted server-initiated
        requests and resumability in its 2026-07-28 revision for exactly this
        reason -- redelivery across a reconnect cannot be made to work.
        """
        return [q["frame"] for q in self._questions.values()
                if q["chat"] == chat_id]

    # -- the status a background turn cannot say out loud (phase 7) --------- #
    def _elapsed_ms(self, chat_id: str) -> int:
        """How long THIS conversation's current turn has been running.

        Reads `_turn_started`, never writes it: `_begin_turn` is the one
        writer, and a chat this daemon has never begun a turn for reads as
        zero rather than raising -- the same "answer nothing rather than
        guess" rule `last_outcome` follows one layer down.
        """
        started = self._turn_started.get(chat_id)
        if started is None:
            return 0
        return max(0, round((time.time() - started) * 1000))

    def _turn_in_flight(self, chat_id: str) -> bool:
        """Is a turn running for `chat_id` right now, owned by any session
        or ORPHANED by one that closed (`_detach_survivable`)? The one
        derivation `status_for`'s "running" and a chat-only `stop` share."""
        if chat_id in self._orphaned:
            return True
        return any(chat_id in s.turn_by_chat for s in self.sessions)

    def busy(self) -> str | None:
        """Why restarting NOW would cut something off, or None when idle.

        The automatic restart's one question (AC-715): it never lands in the
        middle of a turn or kills an agent at work. Read from the state that
        already says so, never a second copy: a turn in flight in any session,
        page, phone or scheduled run alike (`turn_by_chat`), or orphaned by a
        closed socket (`_orphaned`); the recognizer still decoding
        (`_recognizer_in_use`, `_decoding`); and the coding agent's own
        bookkeeping (`_Host.busy`), because the restart kills processes and
        their owner is the one to ask. A child held IDLE between turns (AC-59)
        is not busy: it is disposed by the restart and resumes its session
        file on the next turn (AC-62).
        """
        if self._orphaned or any(s.turn_by_chat for s in self.sessions):
            return "a turn is running"
        if self._recognizer_in_use() or self._decoding.locked():
            return "the recognizer is still decoding"
        host = getattr(self.brain, "host", None)
        if host is not None and host.busy():
            return "a coding agent is working"
        return None

    def status_for(self, chat_id: str) -> str:
        """One of five words -- idle, running, waiting, done, error --
        DERIVED from state already held elsewhere, never a second copy of it
        (docs/parallel-agents.md phase 7 item 1, AC-233).

        "running" reads `turn_by_chat`, the same per-session registry
        `owns()` and `_begin_turn` already maintain -- a turn is in flight
        for this chat exactly when some session still has it registered.
        "waiting" and "a child up" read the harness host directly, because a
        coding agent's dialog and its process are that adapter's own
        bookkeeping (AC-234, AC-241) and asking Hub to keep a second copy is
        the drift this rule exists to avoid. The one bit nothing else holds
        is the last terminal OUTCOME, which is `_Host.last_outcome`'s whole
        reason to exist.

        A brain with no session store (`session_brain()` answers None)
        cannot hold a child at all, so every conversation reads "idle" --
        honest, since nothing here has ever run a turn to fail or finish.
        """
        host = getattr(self.session_brain(), "host", None)
        if host is None:
            return "idle"
        if host.questions_open(chat_id):
            return "waiting"
        # ORPHANED FIRST: a turn `_detach_survivable` let outlive its
        # socket (owner's ruling, 2026-09-16) has no session left in
        # `self.sessions` at all, so the scan below would never find it --
        # this dict is the one thing that still knows it is running.
        if self._turn_in_flight(chat_id):
            return "running"
        if host.child(chat_id) is not None:
            return "done"
        return host.last_outcome(chat_id)

    def _progress_frame(self, chat_id: str, progress: _TurnProgress) -> dict:
        """The heartbeat's evidence, one builder for the frame and the status
        route (D-18). Elapsed is `_elapsed_ms`'s -- the turn start
        `_begin_turn` records -- so the clock a reloaded page recovers and
        the one a live page is sent cannot disagree."""
        elapsed = (self._elapsed_ms(chat_id) if chat_id in self._turn_started
                   else round((time.monotonic() - progress.started) * 1000))
        return progress.frame(elapsed)

    def progress_for(self, chat_id: str) -> dict:
        """The running turn's evidence for `chat_id`, or {} when none runs
        (AC-391). The status route spreads this into its answer."""
        progress = self._progress.get(chat_id)
        return self._progress_frame(chat_id, progress) if progress else {}

    async def _heartbeat(self, s: "SatelliteSession", turn: int, chat: str,
                         progress: _TurnProgress) -> None:
        """A beat at once and every `PROGRESS_EVERY_S` while `turn` is owned
        (AC-389). Cancelled by `_reply`'s `finally` on every exit, and it
        stops by itself the moment the turn is no longer this session's, so
        a stopped turn is never reported alive (D-1, D-6)."""
        while s.owns(turn):
            await s.send_for_turn(turn, type="progress",
                                  **self._progress_frame(chat, progress))
            await asyncio.sleep(PROGRESS_EVERY_S)

    async def _agent_finished(self, s: "SatelliteSession", turn: int,
                              chat: str, progress: _TurnProgress,
                              started_ms: int) -> None:
        """The agent ended: say WHICH and after HOW LONG, live and on record
        (AC-695, docs/lantern-ui.md 8 "Finished row").

        Read at `Done`, not at `speak_end`: the agent's time is its own, and
        `speak_end` waits for the whole playout, while the page draws "pi
        finished in butler, 2:31" ABOVE the answer being spoken. The time is
        the heartbeat's own `elapsed_ms` (`_progress_frame`), so the finished
        row can never read less than the working row last did. The record is
        stamped FIRST and the frame carries what it stamped, so a live page
        and a reloaded one draw one number. One extra beat goes out at once
        rather than waiting up to `PROGRESS_EVERY_S`, which a short or muted
        playout would never reach. A turn no agent reported in to (a refused
        or failed spawn) and a stopped one (no `Done` is read once the turn
        is not owned) finish nothing.
        """
        host = getattr(self.session_brain(), "host", None)
        if not progress.agent_ran or not chat or host is None:
            return
        elapsed = self._progress_frame(chat, progress)["elapsed_ms"]
        agent = host.mark_finished(chat, elapsed, since_ms=started_ms)
        if agent is None:
            return
        progress.finished = {"agent": agent, "agent_elapsed_ms": elapsed}
        await s.send_for_turn(turn, type="progress",
                              **self._progress_frame(chat, progress))

    def action_for(self, chat_id: str) -> str | None:
        """The last tool this conversation's child started, or None
        (owner's ruling 2026-09-16, `docs/triage-list.md` phase 2 continued).

        A read of `_Host.last_action`, never a second copy of it (D-18):
        `ToolStart` writes it and a turn's own ending clears it, in exactly
        the two places `status_for` above already reads `_Host` from. None
        for a brain with no session store, same as `status_for`'s own
        "idle" -- and None for a conversation the daemon has never noted a
        tool for, or whose turn has since ended.
        """
        host = getattr(self.session_brain(), "host", None)
        if host is None:
            return None
        return host.last_action(chat_id)

    async def _on_worker_event(self, s: "SatelliteSession", turn: int,
                               intent: WorkerEvent) -> None:
        """A coding agent's own activity reaches the page as elapsed time
        and a last action (AC-233, AC-235; docs/parallel-agents.md phase 7).

        **ROUTED THROUGH `send_for_turn`, exactly like `reply` -- and unlike
        `question` (AC-249).** A question is broadcast because the device
        that ANSWERS is usually not the one that asked; this is progress on
        a turn only ONE session's socket is driving (`_reply` runs once, on
        the originating session), so the same D-6/AC-5 gate that drops a
        stale reply drops a stale status: a superseded or abandoned turn
        must emit nothing further here either.
        """
        if intent.kind in ("thinking", "tool_input", "text"):
            return      # evidence for the heartbeat, never a frame per token
        chat_id = s.chat_by_turn.get(turn, "")
        await s.send_for_turn(turn, type="worker_event", kind=intent.kind,
                              detail=intent.detail,
                              elapsed_ms=self._elapsed_ms(chat_id))

    async def _on_ask_human(self, s: "SatelliteSession", turn: int,
                            intent: AskHuman) -> None:
        """A coding agent asked; every device is told (AC-249).

        **BROADCAST, never `send_for_turn`.** That method drops anything for a
        turn the session does not own (D-6), and the device that ANSWERS is
        usually not the device that asked -- the desk starts the agent and the
        phone is what is in your hand when it stops. The recorded failure in
        this whole area is visibility rather than waiting: Codex #27006 is a
        phone reading "Thinking" while the desktop sits blocked, and a
        question sent only to the owning session is that bug by construction.

        **IT IS FILED UNDER THE TURN'S CONVERSATION, NOT THE POINTER**
        (AC-241, AC-246). This read `current_chat_id()` while holding the turn
        that could have answered it, so a turn running in A with the daemon's
        pointer on B put `chat=B` on the card and left `questions_for(A)`
        empty: the conversation that is waiting draws no question and the one
        that is not draws somebody else's. The page is immune only because it
        ignores `q.chat`, which is not a property this daemon may rely on.
        """
        ask = intent.ask
        chat_id = s.chat_by_turn.get(turn, "")
        frame = {"type": "question", "chat": chat_id, "qid": ask.qid,
                 "method": ask.method, "title": ask.title,
                 "message": ask.message, "options": list(ask.options),
                 "placeholder": ask.placeholder, "prefill": ask.prefill,
                 "deadline_ms": ask.deadline_ms}
        self._questions[ask.qid] = {
            "frame": frame, "chat": chat_id, "turn": turn,
            "method": ask.method, "asked_at": time.time()}
        self._closed.pop(ask.qid, None)
        print(f"[butler] turn {turn}: the coding agent is asking "
              f"({ask.method}) and is waiting for an answer (AC-234)")
        # The `type` is a LITERAL keyword rather than `**frame`, and that is
        # not style: `test_protocol_doc.py` reads every frame this daemon
        # sends off the AST, so a name reaching the wire only through a dict
        # is a frame plan.md 4.3 can never be checked against (D-12).
        await self.broadcast(
            type="question", chat=frame["chat"], qid=frame["qid"],
            method=frame["method"], title=frame["title"],
            message=frame["message"], options=frame["options"],
            placeholder=frame["placeholder"], prefill=frame["prefill"],
            deadline_ms=frame["deadline_ms"])
        self.phone_questions.asked(self._question_origin(s), chat_id, ask)
        if ask.deadline_ms:
            # The harness's OWN clock, drawn down rather than run (AC-234,
            # `docs/parallel-agents.md` 9.1). pi auto-resolves a timed dialog
            # agent-side, so at this moment the question stops being
            # answerable and the card has to go -- and Butler sends the child
            # NOTHING, because it has already answered itself.
            self._question_timers[ask.qid] = asyncio.create_task(
                self._expire_question(ask.qid, ask.deadline_ms / 1000.0))

    @staticmethod
    def _question_origin(s: "SatelliteSession") -> str:
        """Where a question's turn came from, for the phone (MS-21, AC-526):
        a phone turn's channel kind (its typed reason, `PHONE_REASONS`), every
        inbound channel for a scheduled run, and "" for a desk or page turn,
        whose question stays on the devices that already show it."""
        if isinstance(s, PhoneSession):
            return s.typed_reason
        if isinstance(s, RunSession):
            return notify_mod.EVERY_CHANNEL
        return ""

    def open_question(self, qid: str) -> dict | None:
        """The OPEN question's frame, or None once it is closed or unknown."""
        question = self._questions.get(qid)
        return None if question is None else question["frame"]

    def why_closed(self, qid: str) -> str:
        """The words a late answer to `qid` is refused with (AC-250)."""
        return QUESTION_GONE.get(self._closed.get(qid, ""),
                                 QUESTION_UNKNOWN)[1]

    async def _expire_question(self, qid: str, after_s: float) -> None:
        try:
            await asyncio.sleep(after_s)
        except asyncio.CancelledError:
            return
        await self.close_question(qid, "expired")

    async def close_question(self, qid: str, how: str) -> bool:
        """Take a question down everywhere, and say why (AC-234).

        `question_closed` is what removes the card. Without it the owner is
        looking at a question that cannot be answered and has no way to learn
        that -- which is the Codex complaint in `docs/parallel-agents.md` 9.2
        verbatim: "Codex later responds that the operation timed out, even
        though I eventually approved it".

        The brain is told to stop waiting on it, so the idle deadline re-arms.
        Nothing is sent to the CHILD on any path but `answered`.
        """
        question = self._questions.pop(qid, None)
        timer = self._question_timers.pop(qid, None)
        if timer is not None and not timer.done():
            timer.cancel()
        if question is None:
            return False
        self._remember_closed(qid, how)
        waiter = question.get("owner")
        if waiter is not None:
            # The daemon's own question (`ask_owner`): closed unanswered.
            if not waiter.done():
                waiter.set_result(None)
        else:
            brain = self.session_brain()
            if brain is not None and how != "answered":
                brain.forget_question(qid)
        await self.broadcast(type="question_closed", chat=question["chat"],
                             qid=qid, how=how)
        return True

    async def close_questions_for_turn(self, turn: int, how: str) -> None:
        """Every question this turn raised, closed when the turn ends.

        A turn that ended -- answered, stopped, failed, barged in on -- has no
        child left holding its dialogs, so a card still on screen is a control
        that lies. Called from `_reply`'s `finally`, where every ending
        arrives, rather than from the tidy path alone (D-18).
        """
        for qid in [q for q, rec in self._questions.items()
                    if rec["turn"] == turn]:
            await self.close_question(qid, how)

    async def answer_question(self, qid: str, answer: Answer) -> dict:
        """The human's answer, on its way back to the child (AC-250).

        Answers `{"ok": True, ...}` or a refusal carrying the REASON, which
        the route turns into a 410. It is never forwarded hopefully: the
        harness drops an unknown dialog id silently, so a late answer written
        anyway leaves the owner believing they approved something the agent
        already defaulted on -- the one failure in this feature that is not
        recoverable.
        """
        question = self._questions.get(qid)
        if question is None:
            reason, said = QUESTION_GONE.get(self._closed.get(qid, ""),
                                             QUESTION_UNKNOWN)
            return {"ok": False, "reason": reason, "error": said}
        brain = self.session_brain()
        waiter = question.get("owner")
        if waiter is not None:
            # The daemon's own question (`ask_owner`, RV-2): no child holds
            # it, so the answer goes to the daemon's waiter. Only an
            # explicit yes is a yes.
            if not waiter.done():
                waiter.set_result(answer.confirmed is True
                                  and not answer.cancelled)
            held = True
        else:
            held = False
            if brain is not None:
                held = await brain.answer_question(qid, answer)
        if not held:
            # The child let go of it first. Closing rather than leaving it
            # open, so the card goes away on every device at the same moment
            # the owner is told their answer was too late.
            await self.close_question(qid, "agent_gone")
            reason, said = QUESTION_GONE["agent_gone"]
            return {"ok": False, "reason": reason, "error": said}
        self._questions.pop(qid, None)
        self._remember_closed(qid, "answered")
        timer = self._question_timers.pop(qid, None)
        if timer is not None and not timer.done():
            timer.cancel()
        if brain is not None:
            # THE CONVERSATION THAT ASKED, not whatever is current now
            # (AC-241). The pair has been held since the question was raised
            # and was thrown away here, so answering minutes later -- after
            # starting another conversation, which is the ordinary thing to do
            # while waiting -- wrote the line into the wrong transcript.
            brain.record_note(question["chat"],
                              self._question_note(question, answer))
        await self.broadcast(type="question_closed", chat=question["chat"],
                             qid=qid, how="answered")
        print(f"[butler] the coding agent's question {qid} was answered; it "
              f"is carrying on (AC-234)")
        return {"ok": True, "qid": qid, "chat": question["chat"]}

    def _remember_closed(self, qid: str, how: str) -> None:
        """Why a question is no longer answerable, BOUNDED (AC-250).

        The 410 a late answer gets names one of four reasons and this is where
        they are kept. One writer rather than two: the trim lived only on the
        `close_question` path, so every ANSWERED question stayed remembered
        for the life of the daemon -- unbounded growth on the one path the
        owner takes every time, which is the half that actually runs.
        """
        self._closed[qid] = how
        while len(self._closed) > QUESTIONS_REMEMBERED:
            self._closed.popitem(last=False)

    @staticmethod
    def _question_note(question: dict, answer: Answer) -> str:
        """"Butler asked X, you answered Y" -- the transcript's own line.

        Written when the question CLOSES and never while it is open, which is
        the whole of what is allowed to persist here: a reload and a rewind
        then show a conversation in which the agent asked and was answered,
        instead of one with an unexplained gap where the pause was.
        """
        frame = question["frame"]
        asked = (frame.get("message") or frame.get("title")
                 or f"a {question['method']} question")
        if answer.cancelled:
            said = "dismissed it"
        elif answer.confirmed is not None:
            said = "answered yes" if answer.confirmed else "answered no"
        else:
            said = f"answered \"{answer.value}\""
        return f"Butler asked: {asked} -- you {said}."

    # -- the daemon's OWN yes/no question (docs/messaging.md 6.5 RV-2) ------- #
    #: Its id prefix. Unlike a harness's dialog (`ports.Ask`: "Butler never
    #: mints one") this question is the DAEMON's, like a schedule proposal's
    #: `prop-`: nothing in a child waits on it, the daemon itself does.
    OWNER_ASK_PREFIX = "owner-"

    def _turn_running(self, chat_id: str, turn: int | None):
        """`(session or None, turn)` for the turn running in `chat_id` (and
        equal to `turn` when one is named), or None. An ORPHANED turn has no
        session left; its question then stays on the devices."""
        for s in list(self.sessions):
            t = (s.turn_by_chat.get(chat_id) if turn is None
                 else turn if s.chat_by_turn.get(turn) == chat_id else None)
            if t is not None:
                return s, t
        orphan = self._orphaned.get(chat_id)
        if orphan is not None and turn in (None, orphan[0]):
            return None, orphan[0]
        return None

    async def ask_owner(self, chat_id: str, title: str, message: str,
                        turn: int | None = None) -> bool | None:
        """Ask the owner a yes/no question IN the running turn of `chat_id`
        through the existing question door, and wait for the answer.

        RV-2 (AC-588): a send the WhatsApp guard holds for confirmation is
        put to the OWNER here, never decided by the model. The card is the
        harness question's (broadcast, `questions_for`, the phone through
        `phone_questions`, answered only through `answer_question`), so
        every device that can answer a coding agent can answer this.

        True only for an explicit yes; False for no or a dismissal; None
        when the question closed unanswered (the turn ended or was stopped).
        Butler never answers it itself and invents no deadline. Raises
        `LookupError` when no turn is running there to ask in.
        """
        import secrets
        found = self._turn_running(chat_id, turn)
        if found is None:
            raise LookupError(f"no turn is running in conversation "
                              f"{chat_id!r} to ask the owner in")
        s, turn = found
        qid = self.OWNER_ASK_PREFIX + secrets.token_hex(6)
        ask = ports.Ask(qid=qid, method="confirm", title=title,
                        message=message)
        frame = {"type": "question", "chat": chat_id, "qid": qid,
                 "method": ask.method, "title": ask.title,
                 "message": ask.message, "options": [], "placeholder": "",
                 "prefill": "", "deadline_ms": None}
        waiter = asyncio.get_running_loop().create_future()
        self._questions[qid] = {
            "frame": frame, "chat": chat_id, "turn": turn,
            "method": ask.method, "asked_at": time.time(), "owner": waiter}
        self._closed.pop(qid, None)
        print(f"[butler] turn {turn}: Butler is asking the owner ({qid}) and "
              f"sends nothing until they answer (RV-2)", flush=True)
        await self.broadcast(
            type="question", chat=frame["chat"], qid=frame["qid"],
            method=frame["method"], title=frame["title"],
            message=frame["message"], options=frame["options"],
            placeholder=frame["placeholder"], prefill=frame["prefill"],
            deadline_ms=frame["deadline_ms"])
        self.phone_questions.asked(
            "" if s is None else self._question_origin(s), chat_id, ask)
        try:
            return await asyncio.shield(waiter)
        finally:
            if not waiter.done():
                # The asker went away (its request was cancelled): the card
                # must not stay up as a control that can no longer act.
                await self.close_question(qid, "agent_gone")
    def open_conversation(self, chat_id: str, limit: int | None = None):
        """Make one conversation the one the next turn goes to (AC-240).

        The transcript in `session_transcript`'s shape, or None for an id
        nobody has. It exists HERE rather than in the route for one reason:
        the ROUTER'S SHORT MEMORY BELONGS TO THE CONVERSATION, and only the
        hub holds it. `self.history` is the rolling transcript handed to every
        brain as `TurnContext.history`, so leaving it in place would prompt
        the opened conversation's first turn with the last turns of the one the
        owner just left -- the crossed wire, on the one model that runs on
        every single utterance. `_new_conversation` clears it for the same
        reason and states the same argument (AC-64).
        It is NOT repopulated from the opened record: the agent's own memory
        comes back through `sessionRef` and, when a rewind took that away,
        through `seed_note` (AC-193). Inventing a second path to the same
        context would be two mechanisms for one fact.
        Nothing is spawned, disposed or aborted (AC-239). A background turn is
        not cancelled either -- one turn at a time is still true, and a page
        that opens a conversation mid-reply is answered by the turn that is
        already running, into the record it started in.
        """
        brain = self.session_brain()
        if brain is None:
            return None
        opened = brain.session_open(chat_id, limit=limit)
        if opened is None:
            return None
        self.history.clear()
        return opened

    async def new_conversation(self, project_id: str | None = None,
                               focus: bool = True,
                               origin: dict | None = None) -> dict:
        """Start a conversation BORN IN A PROJECT. THE ONE DEFINITION of a
        birth (`docs/filing-and-pacing.md` 8, R-A, AC-287; D-18).

        Three callers and one body: the page's New chat
        (`POST /api/session/new`), the spoken "new conversation", and "go to
        project X" when X has nothing to open. The page's route used to call
        the brain directly and so never cleared the router's short memory
        below, which the spoken path always did (AC-64) -- two definitions
        of one act, and the page's was the one missing a line.

        `project_id` is a ROW ID and never a path: resolved by enumerating the
        rows (`ShortcutStore.get`, a `KeyError` naming an unknown id), so a
        directory nobody configured is an id no row has (AC-31). None is the
        ROOT row (D-A1): R4 ruled that a chat created with no project context
        belongs to Butler, and the root is the one project that always exists
        (AC-279). A daemon with no row table at all -- every test Hub built
        without one -- hands the brain None, which carries the outgoing
        conversation's project.

        Raises `LookupError` when this brain holds no conversations,
        `KeyError` for an unknown row, `ValueError` when AC-31's gate refuses
        the directory at birth. Nothing is minted on any of them.

        **`focus=False` IS A SCHEDULED RUN'S BIRTH** (docs/automations.md
        AU-7, AC-461): the same gate and the same one place a project is set,
        but the owner's current conversation stays current and held, and the
        router's short memory is NOT cleared -- a run at 09:00 must not end
        or rewrite the conversation the owner is having. `origin` marks the
        record (AU-8). A fourth caller of this ONE body, never a second birth.
        """
        brain = self.session_brain()
        if brain is None:
            raise LookupError("this brain holds no conversations")
        row = None
        if self.shortcuts is not None:
            if project_id is None:
                project_id = self.shortcuts.root_id() or None
            if project_id is not None:
                row = self.shortcuts.get(project_id)   # KeyError names the id
        chat_id = await brain.new_conversation(row.path if row else None,
                                               focus=focus, origin=origin)
        # The router's short memory goes with the conversation (AC-64). A
        # history that survived would make "new conversation" a lie told by
        # the one model that runs on every single utterance. After the birth,
        # so a REFUSED birth leaves the conversation the owner is still in
        # with its memory intact. Not for an unfocused birth: nothing the
        # owner is in has changed.
        if focus:
            self.history.clear()
        return {"id": chat_id, "project": row.path if row else None,
                "projectId": row.id if row else ""}

    async def _tell_device(self, s: SatelliteSession, chat_id: str) -> None:
        """This device is now SHOWING `chat_id`, because the daemon moved it
        (D-C2, AC-290).

        Set here AND told, because the two halves fail differently. Without
        `showing`, `chat_for` goes on resolving this device to the old chat,
        so the NEXT spoken turn lands back in the conversation the owner just
        left. Without the frame, the page goes on drawing the old transcript
        while the daemon answers into the new one. The page answers
        `{type: "opened"}` by re-reading `/api/session/current`, which
        re-declares `showing` itself -- the same value, so the order cannot
        matter. Other devices are NOT told: the staleness `ChatStore.summary`
        already documents.
        """
        s.showing = chat_id
        await s.send(type="opened", chat=chat_id)

    async def _project_id_of(self, chat_id: str) -> str | None:
        """Which ROW a conversation belongs to, or None (the root) when its
        directory matches no row any more -- the project was removed."""
        brain = self.session_brain()
        if brain is None or self.shortcuts is None or not chat_id:
            return None
        # A disk read, so off the event loop (D-25).
        chat = await asyncio.to_thread(brain.session_chat, chat_id, 1)
        row = self.shortcuts.row_for(str((chat or {}).get("project") or ""))
        return row.id if row is not None else None

    async def _birth_here(self, s: SatelliteSession) -> dict:
        """A new conversation in the project of the one THIS DEVICE is on
        (D-A1), and the device is told (D-C2). Shared by the spoken "new
        conversation" and a wake word set to `new` (AC-807, D-18). Raises
        what `new_conversation` raises; the caller decides what a refusal
        means for its turn."""
        born = await self.new_conversation(
            await self._project_id_of(self.chat_for(s)))
        await self._tell_device(s, born["id"])
        return born

    async def _new_conversation(self, s: SatelliteSession, turn: int) -> None:
        """The SPOKEN "new conversation" (AC-35): born in the project of the
        conversation THIS DEVICE is on (D-A1), and the device is told (D-C2).

        This rotates a RECORD and nothing else. A child may well be running,
        and it is deliberately NOT disposed here: rotating mints a new session
        reference, so the next turn's `ensure()` sees a spec that no longer
        matches and replaces the child on its own (AC-60). The guard survives
        only to keep D-1 -- an exception on this path would skip the re-arm
        below and hold the microphone.
        """
        await self._speak(s, turn,
                          dialogue.line(self.NEW_CONVERSATION, s.language),
                          "reflex")
        try:
            born = await self._birth_here(s)
            print(f"[butler] turn {turn}: a new conversation was asked for; "
                  f"it is {born['id']}, born in {born['project']} (AC-35, "
                  f"AC-287)")
        except Exception as e:                        # noqa: BLE001
            print(f"[butler] turn {turn}: the new conversation could not be "
                  f"started cleanly: {e!r}")
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)

    def project_filter(self, row_id: str):
        """The predicate "this record's project is row `row_id`", over a
        record's `project` PATH. ONE definition (D-18): `GET /api/chats`'s
        `?project=` scope and "go to project X" both ask it.

        `ChatStore` deliberately knows nothing about rows (AC-229), so the
        rule is built here, where both the rows and the paths are known, and
        handed down. `KeyError` names an unknown id.
        """
        if self.shortcuts is None:
            raise KeyError(f"no project with id {row_id!r}; this daemon holds "
                           f"no projects")
        row = self.shortcuts.get(row_id)          # KeyError names the id

        def belongs(project: str) -> bool:
            found = self.shortcuts.row_for(project) if project else None
            return found is not None and found.id == row.id

        return belongs

    async def go_to_project(self, path: str) -> str:
        """Open the newest conversation in the project at `path`, or start one
        born there when it has none. Answers the conversation's id
        (`docs/filing-and-pacing.md` 8, R-C, AC-290).

        NAVIGATION, never a move: no record's `project` changes, and nothing
        is spawned, disposed or stopped -- `open_conversation` and
        `new_conversation` are the SAME two acts the page's click and its New
        chat perform (D-18), and neither touches a child (AC-239, AC-60). A
        running agent in the project being left keeps running.

        `path` is one `match_project` already chose from the configured rows,
        and it is resolved back to its ROW rather than trusted as a
        directory, so nothing here can name a directory no row has.
        """
        brain = self.session_brain()
        if brain is None or self.shortcuts is None:
            raise LookupError("this brain holds no conversations")
        row = self.shortcuts.row_for(path)
        if row is None:
            raise KeyError(f"no project row for {path!r}")
        newest = await asyncio.to_thread(
            brain.session_list, 1, self.project_filter(row.id))
        if newest:
            if self.open_conversation(newest[0]["id"]) is None:
                raise KeyError(f"no conversation {newest[0]['id']!r}")
            return newest[0]["id"]
        return (await self.new_conversation(row.id))["id"]

    def project_paths(self) -> tuple:
        """THE project list: every row in the shortcuts pane (backlog item 4).

        One table, which is the owner's ruling of 2026-09-11. It used to be the
        `projects` SETTING while the pane's rows launched terminals, and the two
        drifted until clicking claude on a project worked and asking for it out
        loud was refused.

        A daemon built with no store -- which is every Hub in a test that does
        not know shortcuts exist -- has no projects, and that is the right
        answer rather than a crash: the spoken switch then refuses everything,
        which is what an empty list has always meant.
        """
        return self.shortcuts.paths() if self.shortcuts is not None else ()

    async def _go_to_project_turn(self, s: SatelliteSession, turn: int,
                                  heard: str, found: str | None,
                                  clash=None) -> None:
        """Go to a project, or refuse ALOUD and change nothing (AC-31, AC-290).

        Matching is EXACT on the spoken form of a configured project, and
        `_on_final` has already done it -- one match per utterance, so the
        branch that decides whether this is navigation at all and the branch
        that decides where to go cannot disagree. No fuzz, no prefix, no
        nearest neighbour: an unrecognised name fails CLOSED, and the refusal
        says what was HEARD, so a misrecognition is diagnosable from the room
        rather than from a log.

        NAVIGATION since 2026-09-26 (R-C): it opens the project's newest
        conversation or births one there (`go_to_project`). It used to MOVE
        the conversation the owner was in, re-filing the record and the agent
        with it (`work_in`, deleted with every other move).
        """
        if clash is not None:
            # A DIFFERENT refusal from the one below, because the two are
            # different facts and only one of them is the owner's to fix by
            # adding a row: "I have no project called that" and "I have two"
            # need different next actions.
            print(f"[butler] turn {turn}: refusing to go to {heard!r}: "
                  f"{clash}")
            await self._speak(s, turn,
                              self.AMBIGUOUS_PROJECT.format(name=heard),
                              "reflex")
        elif found is None:
            print(f"[butler] turn {turn}: refusing to go to {heard!r}: it "
                  f"is not one of the configured projects, and Butler does "
                  f"not guess a working directory for a tool-enabled agent "
                  f"(AC-31)")
            await self._speak(s, turn,
                              self.NO_SUCH_PROJECT.format(name=heard),
                              "reflex")
        else:
            name = settings_mod.project_name(found)
            try:
                chat_id = await self.go_to_project(found)
            except Exception as e:                        # noqa: BLE001
                # Loud, never fatal (D-1). Reachable when the project has no
                # conversation to open and one cannot be born there (D-A2).
                print(f"[butler] turn {turn}: could not go to {found}: {e!r}")
                await self._speak(s, turn,
                                  self.PROJECT_FAILED.format(name=name),
                                  "reflex")
            else:
                # Translated BEFORE `.format`, so a translation keeps `{name}`
                # rather than being handed an already-substituted string it
                # could never match (`dialogue.LINES`).
                await self._speak(
                    s, turn,
                    dialogue.line(self.WORKING_IN, s.language).format(
                        name=name),
                    "reflex")
                await self._tell_device(s, chat_id)
                print(f"[butler] turn {turn}: went to {found}; this device "
                      f"now shows {chat_id} (AC-290)")
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)

    def _working_brain(self):
        """The brain, when it can CARRY OUT dispatched work itself.

        A CAPABILITY read off the adapter, never a name test: the endpoint
        brain and the fake have no coding agent behind them and keep Phase 1's
        spoken refusal, and the next brain that drives a harness declares the
        flag rather than editing this file (daemon/AGENTS.md).
        """
        return self.brain if getattr(self.brain, "CAN_PROCEED", False) else None

    # Nothing is said OUTSIDE a turn: the page drops frames for a turn it
    # does not own (D-6). Unsolicited speech is `announce` below, which MINTS
    # a turn of its own on the desk and tells the page to adopt it -- the one
    # door for a line nobody asked for (AC-14, docs/automations.md AU-18).

    async def _pong(self, s: SatelliteSession, m: dict) -> None:
        return

    async def _caps(self, s: SatelliteSession, m: dict) -> None:
        """`{"type": "caps", "caps": {"wake": bool, "tts": bool}}` -- what
        this page can do NOW (docs/automations.md phase 7b, AC-470).

        `hello.caps` is sent once, when the socket opens at boot -- before the
        microphone is armed and usually before the speaker exists -- so it
        cannot say whether the page is LISTENING. The page re-sends this
        whenever either half changes, and the announcer reads `wake` as
        "wake-listening" and `tts` as "has a speaker"."""
        caps = m.get("caps")
        if not isinstance(caps, dict) or not all(
                isinstance(v, bool) for v in caps.values()):
            await s.send(type="error",
                         message="caps must be an object of true/false")
            return
        s.caps = dict(caps)

    def desk_session(self) -> SatelliteSession | None:
        """The desk session that may speak an announcement, or None
        (docs/automations.md AU-18, AC-470): socket peer loopback, socket
        open, wake-listening and with a speaker. Two desk tabs are ONE desk,
        so only the newest speaks -- never both, which would say it twice."""
        found = [s for s in self.sessions
                 if s.is_desk and not s.ws.closed and s.caps.get("wake") is True
                 and s.caps.get("tts") is True]
        return max(found, key=lambda s: s.connected_at) if found else None

    async def announce(self, text: str, chat: str = "") -> str:
        """Say ONE short line nobody asked for, on the DESK ONLY, never over
        a turn (plan.md AC-14, docs/automations.md AU-18, AC-470). Answers
        what happened, for the run's record: "said", "off" (the `speak`
        setting), "no desk", "dropped: ..." or "not said: ...".

        AC-14's queueing rule, reduced to what a run result needs: it WAITS
        while the desk is in any state but idle and speaks at the boundary,
        never preempting; past `ANNOUNCE_WAIT_S` it is dropped, not held.
        AC-14a's hold-and-replay does NOT apply (AU-18): with no desk nothing
        is said and nothing is kept -- the unread dot and the channel message
        already carry the result.

        The line is a TURN of its own on the desk, minted here, because the
        page drops every frame for a turn it does not own (D-6): an
        `announce` frame names it so the page adopts it, then the ordinary
        `speak_start` / audio / `speak_end` brackets and `playback_done`
        follow, and `_rearm` is the exit (D-1). The room can still interrupt
        it: the wake word's `_barge_in` ends a SPEAKING session's turn as it
        ends any other. `keep=False`: heard, and in no transcript."""
        if not self.settings.get("speak", True):
            return "off"
        waited = 0.0
        while True:
            s = self.desk_session()
            if s is None:
                return "no desk"
            if s.state == IDLE and s.turn is None:
                break
            if waited >= ANNOUNCE_WAIT_S:
                return (f"dropped: the desk stayed busy for "
                        f"{ANNOUNCE_WAIT_S:g} s")
            await asyncio.sleep(ANNOUNCE_POLL_S)
            waited += ANNOUNCE_POLL_S
        # Claimed with no await since the idle check, so a turn the owner
        # starts now finds this one in place and barges in on it.
        turn = self.mint_turn()
        s.turn = turn
        s.chat_by_turn[turn] = ""          # no conversation: always audible
        s.spoken = []
        s.interrupted = False
        s.audio_ms_sent = 0.0
        s.playback_done = asyncio.Event()
        s.speaking_started = False
        outcome = "said"
        try:
            await s.send(type="announce", turn=turn, text=text, chat=chat)
            if not await self._speak(s, turn, text, "reflex", keep=False):
                outcome = ("not said: speech failed" if s.owns(turn)
                           else "not said: interrupted")
            await self._end_speaking(s, turn)
            await self._await_playback(s, turn)
        finally:
            s._rearm(turn)
        print(f"[butler] announced on the desk ({s.device_id}): {text!r} -> "
              f"{outcome}", flush=True)
        return outcome

    # -- which conversation is in front of this device ----------------------- #
    async def _showing(self, s: SatelliteSession, m: dict) -> None:
        """`{"type": "showing", "chat": "chat-..."|null}` -- this device says
        which conversation is on its screen (AC-238, AC-247).

        **A FRAME AND NOT A ROUTE, AND NOT A FIELD ON AN EXISTING ONE.**
        `POST /api/session/open` was the obvious candidate and it answers a
        different question: it moves the conversation the DAEMON is holding,
        which is one fact for the whole house, while this is one fact per pair
        of eyes -- two devices can show two conversations and only one of them
        can be the daemon's pointer. `hello` was the other candidate and it is
        sent once, before the page has a conversation to name, while this
        changes every time the owner clicks a row. And a route would have to
        identify the device the way `/api/attach` does, by a query parameter
        the socket already establishes for free.

        It is state on the SOCKET, so a reconnect forgets it and the page
        re-declares from its `ready` handler. That is the honest lifetime: a
        device that is gone is not showing anything.

        `null` means "nothing on screen" and is not the same as never having
        said -- but both hear everything, because `hears()` mutes only on a
        conversation NAMED and different. Nothing here is validated against
        the store: an id this daemon does not hold costs that one device its
        audio and nothing else, and it is VISIBLE, because every muted segment
        arrives as a `reply` frame marked shown-not-heard.
        """
        chat = m.get("chat")
        if chat is not None and not isinstance(chat, str):
            await s.send(type="error",
                         message="showing.chat must be a conversation id or "
                                 "null")
            return
        s.showing = chat or None

    def run_refusal(self, project_id: str) -> str | None:
        """What would refuse a NEW agent in this project right now -- the
        project's own limit or the global cap -- or None when one may start
        (docs/automations.md AU-13). The scheduler asks before it births a
        run's chat, so a busy project WAITS; nothing here stops anything.
        None too when this brain holds no child at all (endpoint mode): the
        birth itself then refuses, loudly, as a failed run."""
        host = getattr(self.session_brain(), "host", None)
        if host is None or self.shortcuts is None:
            return None
        row = self.shortcuts.get(project_id)      # KeyError names the id
        return host.refusal_for(row.path)

    def current_chat_id(self) -> str:
        """The conversation a turn goes to right now, or "" when this daemon
        holds no conversations at all.

        `session_state` and never `session_transcript`: the id is all that is
        wanted and that one is the in-memory summary the handshake already
        reads, so nothing here walks a transcript (D-25).
        """
        brain = self.session_brain()
        if brain is None:
            return ""
        return str(brain.session_state().get("session") or "")

    def chat_for(self, s: SatelliteSession) -> str:
        """Which conversation this device's next turn -- or next attachment --
        belongs to. THE ONE RESOLVER (AC-246, D-18).

        Two sites asked this question independently before there was a
        mechanism for the first half of it, and a third was about to: a spoken
        turn, a typed turn and an attached file. A rule enforced at call sites
        is a rule the fourth call site does not have, and getting it wrong
        starts a coding agent in the wrong repository -- AC-31's exact failure
        arriving through a door AC-31 does not guard.

        **A device that has SAID what it is showing gets that conversation.**
        That is D4's ruling applied past the voice: what you are looking at is
        what you are talking to.

        **A device that has NOT said gets the conversation the daemon is
        HOLDING** -- `ChatStore.current()`, the same record `_attach()` chose
        at boot. This is the hands-free answer and it is written down here
        rather than left implied: Butler is an always-on voice assistant, so
        an utterance with no page open anywhere is the NORMAL case, not an
        edge one, and it lands in the conversation the owner was last having.
        The alternative -- refusing to act until some device declares a view
        -- would make a screen a precondition for talking, which is the
        product inverted.
        """
        if s.showing:
            return s.showing
        return self.current_chat_id()

    def pend_attachment(self, s: SatelliteSession, att) -> str:
        """Hang a file on the conversation that device is SHOWING, and say
        which one that was (AC-247).

        Here rather than in the route, because `chat_for` is the resolver and
        a second caller deciding for itself is how the rule gets a fourth
        spelling (D-18). The route's job is the bytes.
        """
        chat = self.chat_for(s)
        s.attachments.append(Pending(chat, att))
        return chat

    # -- the ambient back-fill ----------------------------------------------- #
    def _ambient_seconds(self) -> int:
        """The ambient window, read per turn like `_voice` and `_speak`.

        0 is off and is the shipped default (AC-68). A store-less Hub -- every
        older test that builds one -- answers 0, so ambient is something a
        daemon has to be configured INTO rather than something it does unless
        told not to.
        """
        if self.settings is None:
            return 0
        return int(self.settings.get("ambient_seconds", 0) or 0)

    def take_ambient(self, turn: int, pcm: bytes) -> bool:
        """Hold one satellite's ambient clip for `turn`. True if it was kept.

        Called by `POST /api/ambient` (butlerd) and never by the socket: the
        ring is a megabyte and binary frames on that socket are fed straight
        into the live turn's pipeline, so audio arriving there would become
        part of what the owner is treated as having SAID (AC-70).

        Two ways it is dropped, and both answer 202 rather than an error,
        because the page fires this without awaiting it and a red line
        mid-turn about a lost back-fill is worse than the lost back-fill
        (D-1).

        * The window is OFF. Off means the daemon stores nothing, not that it
          trusts every page to have stopped sending -- a tab left open across
          the change is exactly the case where the setting has to mean
          something here (AC-68, AC-72).
        * No session owns that turn. The daemon mints turn ids and a late
          clip names one that has been abandoned or superseded, so it is
          discarded rather than attached to whatever turn is current (D-6).
        """
        if self._ambient_seconds() <= 0:
            print(f"[butler] turn {turn}: an ambient clip arrived with the "
                  f"window off; discarding it (AC-68)")
            return False
        for s in list(self.sessions):
            if s.owns(turn):
                s.ambient = pcm
                s.ambient_turn = turn
                return True
        print(f"[butler] turn {turn}: an ambient clip arrived for a turn no "
              f"session owns any more; discarding it rather than handing it "
              f"to the turn the owner is now in (D-6)")
        return False

    def _recognizer_in_use(self) -> bool:
        """True while any streaming session may still be using the engine.

        Asked of the PIPELINES rather than of session state, and the
        difference is the whole of AC-116. Session state is both too coarse
        and too narrow to stand in for this. Too coarse: `stt_engine` tears
        the stream down before it emits the final, so nothing decodes through
        `THINKING` or `SPEAKING`, and refusing there would stop dictation
        working for the several minutes a coding-agent turn spends thinking --
        which is the wait `docs/desk-hotkeys.md` section 1 says dictation
        exists to survive. Too narrow: `asyncio.to_thread` cannot be
        cancelled, so an abandoned turn's `finalize` keeps decoding after the
        session has been reset to IDLE and the pipeline dropped, and every
        state reads free through exactly the window that matters.

        The set is held HERE because `_rearm` drops the session's own
        reference at the moment the thread may still be running. Pruned on
        every ask and on every capture, so a daemon that is never dictated to
        does not accumulate one dead pipeline per turn.
        """
        self._pipelines = {p for p in self._pipelines if p.uses_recognizer()}
        return bool(self._pipelines)

    async def dictate(self, pcm: bytes) -> str:
        """One held-key clip in, its text out (AC-113, AC-116).

        The second caller of `Asr.transcribe` and the first that is not part
        of a turn: the desk agent captures while a key is held and pastes what
        comes back, so nothing here speaks, records or reaches a brain.

        REFUSED, never queued, on both of the ways the recognizer can be busy
        -- a turn holding a streaming session, and the other whole-clip decode
        already running. The check and the acquire have no `await` between
        them, so no second request can pass the check while this one is
        acquiring.

        Known and accepted: a turn that BEGINS during a decode is not blocked.
        Closing that window means gating `_start_capture` on this lock, which
        would make a wake word wait on a dictation and drop the audio it was
        woken by -- a worse failure than the one it prevents, and one the owner
        would meet far more often. The exposure is the length of a decode
        (about 2 s on a typical clip), it needs a wake on ANOTHER satellite
        while this key is held, and it surfaces as `ASR_FAILED` on that turn
        rather than as silence (D-26).
        """
        if self._recognizer_in_use():
            raise AsrBusy("a turn is using the recognizer")
        if self._decoding.locked():
            raise AsrBusy("another dictation is still decoding")
        async with self._decoding:
            text = (await self.asr.transcribe(pcm, self._asr_config()) or "")
        return text.strip()

    async def transcribe_clip(self, pcm: bytes) -> str:
        """A FINISHED clip from outside the room -- a WhatsApp voice note or a
        video's sound (docs/messaging.md MS-18, AC-522) -- decoded by Butler's
        own recognizer in `CLIP_WINDOW_S` windows, joined.

        It WAITS, and never collides with the desk: AC-116's two rules, held
        for every window. A live turn's streaming session holds the engine
        (`_recognizer_in_use`, asked of the pipelines), so a window waits
        while one does; and every whole-clip decode takes `_decoding`, so it
        queues behind an ambient back-fill or a dictation and they behind it.
        The re-check after the lock is taken has no `await` before the decode
        starts, so no turn's capture can slip in between. A turn that BEGINS
        mid-window is not blocked: `dictate`'s accepted exposure, for its
        reason (gating a wake on a voice note would drop the room's audio).
        Holding the lock per WINDOW rather than per note keeps a dictation's
        refusal to one window long.

        The decode itself is off the loop (the adapter's `to_thread`). An
        engine failure raises: the caller says so in the chat (MS-18).
        """
        heard = []
        for start in range(0, len(pcm), CLIP_WINDOW_BYTES):
            window = pcm[start:start + CLIP_WINDOW_BYTES]
            while True:
                while self._recognizer_in_use():
                    await asyncio.sleep(RECOGNIZER_POLL_S)
                await self._decoding.acquire()
                if not self._recognizer_in_use():
                    break
                self._decoding.release()
            try:
                text = await self.asr.transcribe(window, self._asr_config())
            finally:
                self._decoding.release()
            if (text or "").strip():
                heard.append(text.strip())
        return " ".join(heard)

    async def _overheard(self, s: SatelliteSession, turn: int) -> str:
        """Decode this turn's back-fill, or answer "" (AC-69, AC-70).

        QUEUED, not concurrent, and that is the owner's ruling of 2026-08-19
        against this design's own first draft. Two `Session`s on one
        transcribe.cpp `Model` return EMPTY transcripts on BOTH sides, 9 times
        out of 9, with no exception and nothing on stderr
        (`test/manual/spike_ambient_concurrent.py`, docs/ambient.md 3) -- a
        turn that answers nothing, intermittently, only when ambient is on. So
        this runs where there is no contention: after the live final, before
        the brain. It COSTS about 2 s on a 30 s window [MEASURED 2026-08-19]
        and the owner pays it on every ambient turn; the setting's help text
        carries the number rather than promising it is free.

        Off costs nothing at all: with no window there is no stored clip, so
        there is no call, no thread hop and no turn that differs from today's.

        Loud and NEVER fatal (D-1). A back-fill that will not decode loses the
        context; the turn goes on without it, because an answer the owner does
        not get is worse than an answer that missed the room.

        The decode blocks for seconds and this loop is single-threaded, so the
        thread hop is mandatory -- it belongs to the ADAPTER, which is where
        `Asr.transcribe` puts it (`asr_transcribecpp.transcribe` ->
        `asyncio.to_thread(self._decode_clip, ...)`), so this await never
        holds the loop.
        """
        pcm = s.take_backfill(turn)
        if not pcm:
            return ""
        try:
            # Waits rather than refusing: the only other whole-clip caller is
            # a dictation, which is bounded by `dictate_max_seconds` and is
            # the one thing this can queue behind (AC-116).
            async with self._decoding:
                text = (await self.asr.transcribe(pcm, self._asr_config()) or "")
        except Exception as e:                            # noqa: BLE001
            print(f"[butler] turn {turn}: the ambient back-fill would not "
                  f"decode ({len(pcm)} bytes): {e!r} -- answering without it")
            return ""
        print(f"[butler] turn {turn}: ambient back-fill decoded, "
              f"{len(pcm) / 32000.0:.1f}s of room -> {len(text.strip())} "
              f"chars of context (AC-69)")
        return text.strip()

    # -- the reply path ------------------------------------------------------ #
    async def _on_final(self, s: SatelliteSession, turn: int, final, reason: str,
                        corrects: int | None = None) -> None:
        if not s.owns(turn):
            return                       # abandoned while the ASR was finishing
        await s.send_for_turn(
            turn, type="final", text=final.text,
            timings={"first_partial_ms": final.first_partial_ms,
                     "finalize_ms": final.finalize_ms},
            # AC-97. The turn this one supersedes, or None. ALWAYS on the
            # frame rather than only on a correction, so there is ONE final
            # shape on the wire -- a key that appears sometimes cannot be
            # told from a daemon that forgot it, and `test_typed_turn.py`
            # diffs a typed turn's whole wire against a spoken one's.
            corrects=corrects,
            # AC-160. The locale the recognizer was asked for on this
            # utterance, echoed back by the engine rather than re-read from
            # the store -- so a turn that ran before a setting changed still
            # carries what it actually ran with.
            lang=final.lang,
            reason=reason)

        text = (final.text or "").strip()
        # The pipeline's `turn_end` says why the turn STOPPED; this says what
        # came out of it. The pair is what separates "the end phrase was heard"
        # from "the recogniser produced nothing and the ceiling fired", which
        # look identical from outside and have opposite fixes.
        trace.trace("final", turn=turn, reason=reason, chars=len(text),
                    failed=bool(final.failed),
                    first_partial_ms=final.first_partial_ms,
                    finalize_ms=final.finalize_ms,
                    lang=final.lang or "-", tail=trace.tail(text))

        # WHAT LANGUAGE THIS TURN IS IN, decided once and here (AC-209).
        # Before every branch below, because all six of them SPEAK -- the ASR
        # failure, the mode change, the new conversation and both project
        # lines -- and none of them has a `TurnContext` to carry it. And
        # before `_begin_speaking` can fire, so `speak_start` announces a
        # voice that is settled for the whole turn.
        #
        # A FAILED final still gets one: `final.text` is empty by then, so the
        # detector abstains and the latch answers, which is the conversation's
        # own language and is the right thing to apologise in.
        self._turn_language(
            s, text if s.language_words is None else s.language_words)

        # The recognizer could not finish, so this turn's audio is gone
        # (D-26). Said BEFORE every branch below, because none of them can
        # act on an utterance nobody heard, and because the empty text this
        # arrives with would otherwise fall through to AC-6's silent re-arm
        # and be answered exactly like a quiet room. Every path still
        # re-arms (D-1).
        if final.failed:
            print(f"[butler] turn {turn}: the recognizer failed to finalize; "
                  f"the utterance is lost and the room is told (D-26)")
            await self._speak(s, turn,
                              dialogue.line(self.ASR_FAILED, s.language),
                              "reflex")
            await self._end_speaking(s, turn)
            await self._await_playback(s, turn)
            s._rearm(turn)
            return

        # A RECIPE'S OWN TURN (docs/automations.md 8, AC-451) is WORK and
        # never a command: its text is the filled prompt, not something the
        # owner said, so none of the spoken branches below may claim it -- a
        # recipe whose prompt began "run" would otherwise run a recipe.
        if reason in WORK_REASONS:
            s.state = THINKING
            s.reply_task = asyncio.create_task(self._reply(s, turn, text))
            return

        # A spoken mode change is answered by the daemon, not the brain. It is
        # a preference, not an instruction to reason about, and routing it to
        # a model would make a deterministic setting depend on a model's mood.
        if text and self.settings:
            requested = settings_mod.spoken_mode_request(text)
            if requested:
                await self._apply_turn_mode(s, turn, requested)
                s._rearm(turn)
                return

        # And so is starting the conversation over (AC-35). Deterministic, and
        # destructive: the agent's context goes. Whole-utterance matched and
        # disjoint from the mode phrases above, because two matchers claiming
        # one phrase would be decided by the order of these two branches
        # rather than by what the owner meant.
        if text and self.session_brain() is not None and (
                settings_mod.spoken_new_conversation(text)):
            await self._new_conversation(s, turn)
            s._rearm(turn)
            return

        # And so is going to another project (AC-31, AC-290). A deterministic
        # instruction, matched against a configured list, and never routed
        # through a model: what a model would add here is the ability to
        # guess, and the cost of a guess is a tool-enabled agent in the wrong
        # checkout. NAVIGATION, so it needs conversations to navigate between
        # (`session_brain`) and rows to name (`shortcuts`); inert otherwise.
        if (text and self.session_brain() is not None
                and self.shortcuts is not None):
            heard = settings_mod.spoken_project_request(text)
            if heard is not None:
                clash = None
                try:
                    found = settings_mod.match_project(
                        heard.name, self.shortcuts.all())
                except settings_mod.AmbiguousProject as e:
                    # A BARE name that is ambiguous is not a switch at all, so
                    # it falls through to work below exactly as an unknown bare
                    # name does. Only the explicit form, which can only be
                    # navigation, is refused for it.
                    found, clash = None, e
                # The BARE form is only a switch when it names a configured
                # project exactly. "work on the endpointing module" and "work
                # in butler and fix the failing tests" are work, and answering
                # them with a refusal meant the instruction reached nobody at
                # all. The EXPLICIT form ("work in project X") can only be
                # navigation, so a name that matches nothing is still refused
                # out loud -- that is AC-31 and it is not weakened here.
                if found is not None or heard.explicit:
                    await self._go_to_project_turn(s, turn, heard.name,
                                                   found, clash)
                    s._rearm(turn)
                    return

        # And so is the current chat's model (docs/chat-model.md CM-10,
        # AC-787): set from the next reply, or answered and left alone. A BARE
        # "use X" naming no model is work and falls through; a bare name that
        # is a configured project is navigation's, never a model's.
        if (text and self.session_brain() is not None
                and self.chat_models is not None):
            asked = settings_mod.spoken_model_request(text)
            if asked is not None and (asked.explicit or
                                      not self._names_a_project(asked.name)):
                if await self._model_turn(s, turn, asked):
                    return

        # And so is memory (docs/identity-memory.md 4.5, AC-378, AC-379).
        # Matched on `text` -- the owner's REQUEST and nothing else. The room
        # is decoded later, inside `_reply`, and a model's words never come
        # back through here, so neither can write a note (AC-380).
        if text and self.memory is not None:
            note = settings_mod.spoken_remember(text)
            if note is not None or settings_mod.spoken_forget(text):
                await self._memory_turn(s, turn, note)
                s._rearm(turn)
                return

        # And so are recipes and tasks (docs/automations.md 8, AC-451,
        # AC-453): exact, deterministic, never routed through a model, and
        # answered aloud whether or not anything changed. A BARE "run X" that
        # names no recipe exactly is not a command at all and falls through
        # to work below -- "run the tests" is an instruction to the agent.
        if text and self.library is not None:
            if await self._library_turn(s, turn, text):
                return

        if not text:
            # AC-6: the final IS the turn end. Nothing to answer; re-arm.
            s._rearm(turn)
            return

        s.state = THINKING
        s.reply_task = asyncio.create_task(self._reply(s, turn, text))

    # -- the current chat's model by voice (docs/chat-model.md CM-10) ------- #
    def _names_a_project(self, name: str) -> bool:
        """Does `name` name a configured project (or two)? Such a bare
        "switch to X" is navigation's, whatever models match X."""
        if self.shortcuts is None:
            return False
        try:
            return settings_mod.match_project(
                name, self.shortcuts.all()) is not None
        except settings_mod.AmbiguousProject:
            return True

    async def _model_turn(self, s: SatelliteSession, turn: int,
                          asked) -> bool:
        """Set or clear the model of the chat THIS DEVICE is on (`chat_for`),
        and say so (AC-787). False when a BARE request names no model: the
        utterance is work and belongs to the agent.

        Favourites first, then the chat's AGENT's own catalogue
        (`match_models`); exactly one is taken, none or several are answered
        and nothing changes. The change goes through `set_model`, the route's
        own rule, so the next turn's spawn spec differs and the held child is
        replaced then (CM-3, AC-60); a running turn is left alone. Not
        recorded in the transcript, like every daemon-answered line. Open
        pages see it on their next read of the chat: no frame announces a
        chat's model.
        """
        brain = self.session_brain()
        rules = self.chat_models
        cid = self.chat_for(s)
        chat = await asyncio.to_thread(brain.session_chat, cid, 1)
        agent = rules.agent_of(chat)
        label = rules.labels.get(agent, agent)
        chosen, spoken = "", self.MODEL_DEFAULT
        if asked.name:
            lister = getattr(ports.HARNESSES.get(agent), "list_models", None)
            try:
                if lister is None:
                    raise LookupError(f"{agent!r} cannot list its models")
                catalogue = list(await lister())
            except Exception as e:                    # noqa: BLE001
                print(f"[butler] turn {turn}: {agent} could not list its "
                      f"models for {asked.name!r}: {e!r} (CM-10)")
                await self._speak(s, turn, self.MODELS_UNLISTED.format(
                    agent=label), "reflex")
                await self._end_turn(s, turn)
                return True
            found = settings_mod.match_models(
                asked.name, self.settings.get("model_favourites") or [],
                catalogue)
            if not found and not asked.explicit:
                return False                    # "use tabs" is work
            if len(found) == 1 and rules.valid(found[0]):
                chosen = found[0]
                name = settings_mod.model_spoken_names([chosen])[0]
                spoken = self.MODEL_SET.format(name=name[:1].upper() + name[1:])
            else:
                if len(found) > 1:
                    said = settings_mod.model_spoken_names(found[:3])
                    spoken = self.SEVERAL_MODELS.format(
                        name=asked.name, choices=" or ".join(
                            [", ".join(said[:-1]), said[-1]]))
                else:                   # none, or a name argv cannot carry
                    spoken = self.NO_SUCH_MODEL.format(name=asked.name,
                                                       agent=label)
                print(f"[butler] turn {turn}: no model set for "
                      f"{asked.name!r}: {found[:3]} on {agent} (AC-787)")
                await self._speak(s, turn, spoken, "reflex")
                await self._end_turn(s, turn)
                return True
        try:
            brain.set_model(cid, chosen)
        except KeyError as e:
            print(f"[butler] turn {turn}: the model could not be set: {e!r}")
            spoken = self.MODEL_FAILED
        else:
            if chosen:
                await asyncio.to_thread(rules.remember, agent, chosen)
            print(f"[butler] turn {turn}: chat {cid} is on "
                  f"{chosen or 'its default model'} from its next reply "
                  f"(CM-10)")
        await self._speak(s, turn, spoken, "reflex")
        await self._end_turn(s, turn)
        return True

    # -- recipes and tasks by voice (docs/automations.md 8) ------------------ #
    async def _library_project(self, s: SatelliteSession) -> str:
        """The ROW the conversation this device is on belongs to -- the root
        when it has none, when its row was removed, or when this brain holds
        no conversations at all (endpoint mode)."""
        found = await self._project_id_of(self.chat_for(s))
        return found or self.library.root_id()

    async def _library_turn(self, s: SatelliteSession, turn: int,
                            text: str) -> bool:
        """Answer one spoken task or recipe command. True when it WAS one and
        the turn is dealt with (re-armed, or handed to a recipe's reply);
        False when the utterance is not a command and belongs to the brain."""
        # A yes or no is a command only while a proposal (a schedule or a
        # recipe) waits in this conversation (docs/proactive.md PR-21,
        # docs/recipe-tools.md RT-3); a reminder only in
        # one of its four English shapes (PR-20). Both disjoint from the
        # task and run phrases by construction (test_reminders.py).
        answer = settings_mod.spoken_confirm(text)
        if answer is not None and \
                self._pending_proposal(self.chat_for(s)) is not None:
            return await self._proposal_turn(s, turn, answer)
        remind = settings_mod.spoken_remind(text)
        if remind is not None and self.automations is not None:
            return await self._remind_turn(s, turn, remind)
        added = settings_mod.spoken_task_add(text)
        listing = settings_mod.spoken_task_list(text)
        done = settings_mod.spoken_task_done(text)
        run = settings_mod.spoken_run_request(text)
        if added is None and not listing and done is None and run is None:
            return False
        project = await self._library_project(s)
        if run is not None:
            recipes = await asyncio.to_thread(self.library.recipes.recipes)
            found = settings_mod.match_recipes(
                run.name, [r for r in recipes
                           if r.project in (project, library_mod.GLOBAL)])
            if not found and not run.explicit:
                return False                    # "run the tests" is work
            if len(found) == 1:
                return await self._recipe_turn(s, turn, found[0], project)
            spoken = (self.NO_SUCH_RECIPE.format(name=run.name) if not found
                      else self.TWO_RECIPES.format(n=len(found),
                                                   name=run.name))
            print(f"[butler] turn {turn}: refusing to run {run.name!r}: "
                  f"{len(found)} recipe(s) answer to it (AC-451)")
        else:
            try:
                spoken = await self._task_command(s, turn, project, added,
                                                  listing, done)
            except OSError as e:
                print(f"[butler] turn {turn}: the task store failed: {e!r}")
                spoken = self.LIBRARY_FAILED
        await self._speak(s, turn, spoken, "reflex")
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)
        s._rearm(turn)
        return True

    async def _task_command(self, s: SatelliteSession, turn: int,
                            project: str, added, listing: bool,
                            done) -> str:
        """Do one task command and answer the line to say (AC-453). The disk
        work is a thread hop (D-1); a change is broadcast as `library`."""
        tasks = self.library.tasks
        if added is not None:
            if not added:
                return self.ADD_TASK_WHAT
            try:
                task = await asyncio.to_thread(tasks.add, project, added)
            except library_mod.LibraryRefused as e:
                return self.TASK_REFUSED.format(why=str(e).rstrip(" ."))
            await self._library_changed("tasks", project)
            return self.ADDED_TASK.format(text=task["text"].rstrip(" .!?"))
        mine = [t for t in await asyncio.to_thread(tasks.tasks, project)
                if not t.get("done")]
        name = self._project_label(project)
        if listing:
            # Oldest first, numbered, at most five (section 8); the numbers
            # are remembered for "mark task N done" on THIS device.
            s.read_tasks = [t["id"] for t in mine[:5]]
            if not mine:
                return self.NO_TASKS.format(project=name)
            said = [self.TASKS_HEAD.format(project=name)] + [
                f"{n}, {t['text'].rstrip(' .!?')}."
                for n, t in enumerate(mine[:5], 1)]
            if len(mine) > 5:
                said.append(self.TASKS_MORE.format(n=len(mine) - 5))
            return " ".join(said)
        if done.number is not None:
            ids = s.read_tasks
            if not 1 <= done.number <= len(ids):
                return self.NO_SUCH_TASK_NUMBER.format(n=done.number)
            hits = [t for t in mine if t["id"] == ids[done.number - 1]]
            if not hits:
                return self.NO_SUCH_TASK_NUMBER.format(n=done.number)
        else:
            wanted = settings_mod._spoken_form(done.text)
            hits = [t for t in mine
                    if settings_mod._spoken_form(t["text"]).rstrip(" .!?")
                    == wanted]
            if len(hits) != 1:
                return (self.TWO_TASKS if hits else self.NO_SUCH_TASK).format(
                    n=len(hits), text=done.text)
        task = await asyncio.to_thread(tasks.edit, hits[0]["id"], done=True)
        await self._library_changed("tasks", project)
        return self.MARKED_DONE.format(text=task["text"].rstrip(" .!?"))

    def _project_label(self, row_id: str) -> str:
        """How a project is SAID: its label, else its folder's spoken name."""
        row = None
        if self.shortcuts is not None:
            row = next((r for r in self.shortcuts.all() if r.id == row_id),
                       None)
        if row is None:
            return "this project"
        return row.label or settings_mod.project_name(row.path)

    async def chat_seen(self, chat_id: str) -> None:
        """A conversation was OPENED on some device: a scheduled run's chat
        stops being unread everywhere (AC-469, AU-20). One `library` frame
        with `what: "runs"` per project that changed, so every page re-reads
        its tree; nothing at all when the chat is no run's."""
        if self.automations is None or self.scheduler is None:
            return
        changed = await asyncio.to_thread(self.automations.mark_seen,
                                          chat_id, self.scheduler.clock())
        autos = {a["id"]: a for a in
                 await asyncio.to_thread(self.automations.automations)}
        for project in {autos[r["automation"]]["project"] for r in changed
                        if r["automation"] in autos}:
            await self._library_changed("runs", project)

    async def _library_changed(self, what: str, project: str) -> None:
        """The `library` hint (docs/automations.md 5, AC-467), with the
        routes' own scope rule: "" for the Global section."""
        root = self.library.root_id()
        await self.broadcast(type="library", what=what,
                             project="" if project in (root,
                                                       library_mod.GLOBAL)
                             else project)

    async def _recipe_turn(self, s: SatelliteSession, turn: int, recipe,
                           project: str) -> bool:
        """Run one recipe as THIS DEVICE's turn (AU-4, AC-451).

        Filled by THE ONE EXPANSION with no values, so a placeholder without
        a default is refused aloud naming it and nothing starts. With
        conversations, a chat is BORN in the recipe's project (a global one:
        the project on screen) through the one birth, the device is told
        (`opened`, AC-290), this turn ends, and the filled text starts as a
        NEW turn of this device in that chat -- so it is recorded, routed,
        answered and re-armed like anything the owner said there. With none
        (endpoint mode) the filled text simply IS this turn.
        """
        try:
            text = await asyncio.to_thread(library_mod.expand, recipe.prompt)
        except library_mod.LibraryRefused:
            needs = [p.name for p in recipe.placeholders()
                     if p.default is None]
            await self._speak(s, turn, self.RECIPE_NEEDS.format(
                names=", ".join(needs) or "a value"), "reflex")
            await self._end_turn(s, turn)
            return True
        if self.session_brain() is None:
            s.state = THINKING
            s.reply_task = asyncio.create_task(self._reply(s, turn, text))
            return True
        where = project if recipe.project == library_mod.GLOBAL \
            else recipe.project
        await self._speak(s, turn, self.RUNNING_RECIPE.format(
            name=recipe.name), "reflex")
        try:
            born = await self.new_conversation(where)
            await self._tell_device(s, born["id"])
        except Exception as e:                            # noqa: BLE001
            # Loud and never fatal (D-1): a birth refused (a removed row, a
            # directory AC-31 will not start in) is said, and nothing runs.
            print(f"[butler] turn {turn}: could not start recipe "
                  f"{recipe.id} in {where}: {e!r}")
            await self._speak(s, turn, self.RECIPE_FAILED.format(
                name=recipe.name), "reflex")
            await self._end_turn(s, turn)
            return True
        await self._end_turn(s, turn)
        print(f"[butler] turn {turn}: running recipe {recipe.id} "
              f"({recipe.name}) in {born['id']} (AC-451)")
        # A NEW turn of this device, begun only after this one re-armed, so
        # the old turn's ownership is released first and the new one is
        # adopted by the page from its own `final` (reason "recipe").
        new_turn = self._begin_turn(s)
        s.state = THINKING
        s.task = asyncio.create_task(self._on_final(
            s, new_turn, ports.Final(text=text), "recipe"))
        return True

    # -- reminders, the library tools, schedule proposals -------------------- #
    # docs/proactive.md section 5 (PR-17..PR-22, AC-488..AC-492). English
    # read-backs, like every line above (language.md D5).
    REMINDER_SET = "Reminder set for {when}: {text}."
    REMINDER_REFUSED = "I did not set that reminder: {why}."
    PROPOSAL_ASK = "{label}, run '{name}' in {where}. Create it?"
    PROPOSAL_MADE = ("Created {name}: {label}, in {where}. Its first run has "
                     "started in its own chat.")
    PROPOSAL_DECLINED = "Not created. Nothing was saved."
    PROPOSAL_FAILED = "I could not create {name}: {why}."
    # recipe_propose's read-back and yes (docs/recipe-tools.md RT-3).
    RECIPE_ASK = "Save a recipe named '{name}' in {where}? It runs: '{head}'"
    RECIPE_ASK_CHARS = 120
    RECIPE_MADE = ("Saved the recipe '{name}' in {where}. Say \"run recipe "
                   "{name}\" to run it.")
    #: A proposal's question id; `POST /api/answer` routes this prefix here.
    PROPOSAL_PREFIX = "prop-"
    #: A proposal nobody answered is forgotten after this long.
    PROPOSAL_TTL_S = 600

    async def _remind_turn(self, s: SatelliteSession, turn: int,
                           req) -> bool:
        """A spoken reminder (PR-20, AC-490): saved, read back, re-armed.
        Listed under the conversation's project, like a spoken task."""
        from . import automations as au
        from . import schedule as sched
        project = await self._library_project(s)
        now, tz = self.scheduler.clock(), self.automations.tz
        try:
            at = au.remind_instant(req, now, tz)
            row = await asyncio.to_thread(self.automations.save, {
                "kind": au.REMINDER, "project": project, "text": req.text,
                "runAt": au.iso(at, tz)}, now)
            await self._library_changed("automations", project)
            spoken = self.REMINDER_SET.format(
                when=sched.when_words(at, now, tz),
                text=row["text"].rstrip(" .!?"))
        except library_mod.LibraryRefused as e:
            spoken = self.REMINDER_REFUSED.format(why=str(e).rstrip(" ."))
        except OSError as e:
            print(f"[butler] turn {turn}: the reminder store failed: {e!r}")
            spoken = self.LIBRARY_FAILED
        await self._speak(s, turn, spoken, "reflex")
        await self._end_turn(s, turn)
        return True

    async def library_tool(self, name: str, args, project: str, chat: str,
                           turn_key) -> str:
        """THE ONE DISPATCHER for `library.TOOLS`, for both brains: the
        endpoint brain's callable and pi's loopback route both end here
        (D-18). Answers the text the model is shown; refusals are answered,
        never raised (plan.md 3.8). `project` is the conversation's, decided
        by the caller from the chat, never by the model."""
        if name == library_mod.TASK_ADD:
            text, task = await asyncio.to_thread(
                self.library.call_tool, name, args, project, chat, turn_key)
            if task is not None:
                # The MAIN list's, not the conversation's (todo-list AC-671).
                await self._library_changed("tasks", task["project"])
            return text
        if name not in library_mod.TOOL_NAMES:
            return f"Error: there is no tool called {name!r}."
        if name == library_mod.RECIPE_FIND:
            # Read-only, off the loop (D-1): recipe-tools.md RT-1.
            return await asyncio.to_thread(self.library.find, args, project)
        if self.automations is None or self.scheduler is None:
            return f"Error: this Butler keeps no automations, so {name} " \
                   f"cannot run."
        if name == library_mod.REMINDER_ADD:
            text, row = await asyncio.to_thread(
                self.automations.agent_reminder, args, project, chat,
                turn_key, self.scheduler.clock())
            if row is not None:
                await self._library_changed("automations", project)
            return text
        if name == library_mod.RECIPE_PROPOSE:
            return await self._propose(RECIPE_KIND, args, project, chat)
        return await self._propose(SCHEDULE_KIND, args, project, chat)

    def _pending_proposal(self, chat: str | None) -> dict | None:
        """The proposal (a schedule or a recipe, `p["kind"]`) waiting in
        this conversation, or None. Kept
        on the hub, in memory only: nothing about it is on disk until yes
        (AC-492), so a restart forgets it and nothing is created."""
        book = self.__dict__.get("_proposals")
        p = book.get(chat or "") if book else None
        if p is None or self.scheduler is None:
            return None
        if (self.scheduler.clock() - p["at"]).total_seconds() > \
                self.PROPOSAL_TTL_S:
            book.pop(p["chat"], None)
            return None
        return p

    async def _propose(self, kind: str, args, project: str, chat: str
                       ) -> str:
        """`schedule_propose` (PR-21) or `recipe_propose` (recipe-tools.md
        RT-3): checked by every rule, then ASKED -- on the page as a question
        card, and through the model's own reply by voice. WRITES NOTHING
        (AC-492, AC-661); one proposal per conversation of EITHER kind, a
        newer one replacing an older (AC-664)."""
        import secrets
        now = self.scheduler.clock()
        try:
            if kind == RECIPE_KIND:
                p = await asyncio.to_thread(self.library.check_proposal,
                                            args, project)
            else:
                p = await asyncio.to_thread(self.automations.check_proposal,
                                            args, project, now)
        except library_mod.LibraryRefused as e:
            return f"Error: nothing was proposed: {e}."
        where = self._project_label(project)
        if kind == RECIPE_KIND:
            head = p["prompt"][:self.RECIPE_ASK_CHARS]
            if len(p["prompt"]) > self.RECIPE_ASK_CHARS:
                head += "..."
            readback = self.RECIPE_ASK.format(name=p["name"], where=where,
                                              head=head)
        else:
            readback = self.PROPOSAL_ASK.format(label=p["label"],
                                                name=p["name"], where=where)
        p.update(kind=kind, readback=readback,
                 qid=self.PROPOSAL_PREFIX + secrets.token_hex(4),
                 chat=chat or "", at=now)
        book = self.__dict__.setdefault("_proposals", {})
        old = book.get(p["chat"])
        book[p["chat"]] = p
        if old is not None:
            await self.broadcast(type="question_closed", chat=old["chat"],
                                 qid=old["qid"], how="cancelled")
        await self.broadcast(
            type="question", chat=p["chat"], qid=p["qid"], method="confirm",
            title=("Save this recipe?" if kind == RECIPE_KIND
                   else "Create this schedule?"),
            message=p["readback"], options=[],
            placeholder="", prefill="", deadline_ms=0)
        print(f"[butler] {kind} proposed in {p['chat'] or 'no chat'}: "
              f"{p['readback']!r} -- nothing written until yes (AC-492)",
              flush=True)
        return (f"Nothing is created yet. Ask the user exactly this, then "
                f"stop and wait for their answer: \"{p['readback']}\" Butler "
                f"creates it only if they say yes.")

    async def _proposal_turn(self, s: SatelliteSession, turn: int,
                             yes: bool) -> bool:
        """A spoken yes or no to the proposal waiting here (PR-21)."""
        p = self._pending_proposal(self.chat_for(s))
        spoken = await self._settle_proposal(p, yes)
        await self._speak(s, turn, spoken, "reflex")
        await self._end_turn(s, turn)
        return True

    async def answer_proposal(self, qid: str, answer: Answer) -> dict:
        """The page's question card answered (`POST /api/answer`). A
        proposal no longer open is refused with the reason, and nothing is
        created -- the 410 shape of `answer_question`."""
        book = self.__dict__.get("_proposals") or {}
        p = next((x for x in book.values() if x["qid"] == qid), None)
        if p is None or self._pending_proposal(p["chat"]) is not p:
            return {"ok": False, "reason": "expired",
                    "error": "That proposal is no longer open, so "
                             "nothing was created. Ask again."}
        said = await self._settle_proposal(p, answer.confirmed is True)
        return {"ok": True, "qid": qid, "chat": p["chat"], "text": said}

    async def _settle_proposal(self, p: dict, yes: bool) -> str:
        """Close the question everywhere, note the answer in its chat, and
        on yes create it. A schedule (PR-22): the recipe and the automation
        (enabled, silence on), then ONE visible manual run at once; a failed
        first run turns it off (`Scheduler._deliver`). A recipe (RT-3,
        AC-662): the recipe alone, in the conversation's project; nothing
        runs. Answers the line to say."""
        self.__dict__.get("_proposals", {}).pop(p["chat"], None)
        await self.broadcast(type="question_closed", chat=p["chat"],
                             qid=p["qid"], how="answered")
        note = getattr(self.session_brain(), "record_note", None)
        if note is not None and p["chat"]:
            note(p["chat"], f"Butler asked: {p['readback']} -- you answered "
                            f"{'yes' if yes else 'no'}.")
        if not yes:
            return self.PROPOSAL_DECLINED
        if p["kind"] == RECIPE_KIND:
            try:
                recipe = await asyncio.to_thread(
                    self.library.recipes.save, None, p["name"], p["project"],
                    p["prompt"])
            except library_mod.LibraryRefused as e:
                # Re-checked by `save`: the library may have changed while
                # the question waited.
                return self.PROPOSAL_FAILED.format(name=p["name"],
                                                   why=str(e).rstrip(" ."))
            await self._library_changed("recipes", p["project"])  # AC-669
            print(f"[butler] recipe {recipe.id} saved on the owner's yes "
                  f"(RT-3)", flush=True)
            return self.RECIPE_MADE.format(
                name=recipe.name, where=self._project_label(p["project"]))
        try:
            _recipe, auto = await asyncio.to_thread(
                self.automations.create_proposal, p, self.scheduler.clock())
        except library_mod.LibraryRefused as e:
            return self.PROPOSAL_FAILED.format(name=p["name"],
                                               why=str(e).rstrip(" ."))
        await self._library_changed("recipes", p["project"])
        await self._library_changed("automations", p["project"])
        print(f"[butler] schedule {auto['id']} created on the owner's yes; "
              f"its first run starts now (PR-22)", flush=True)
        run = await self.scheduler.run_now(auto["id"], trial=True)
        if run["status"] != "pending":
            return (f"Created {p['name']}, but its first run did not start: "
                    f"{run['reason'].rstrip(' .')}.")
        return self.PROPOSAL_MADE.format(
            name=p["name"], label=p["label"],
            where=self._project_label(p["project"]))

    async def _end_turn(self, s: SatelliteSession, turn: int) -> None:
        """The terminal sequence `_reply` ends on, for a turn it did not run.

        Guarded on still OWNING the turn, because everything above it awaits:
        a barge-in that landed mid-answer has already re-armed this session
        and may have minted a new turn, and re-arming again would take that
        new turn's microphone away from it (D-1, D-6).
        """
        if not s.owns(turn):
            return
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)
        s._rearm(turn)

    async def _memory_turn(self, s: SatelliteSession, turn: int,
                           note: str | None) -> None:
        """Keep or drop a note and SAY what happened (AC-378, AC-379).

        `note` is the text after "remember that" ("" for nothing after it),
        or None for "forget that". The read-back is the whole point of
        speaking: a misrecognised note is HEARD at once, and "forget that"
        undoes it (docs/identity-memory.md 9). The disk work is a thread hop
        (nothing blocking on the event loop), and a write that fails is said
        aloud and logged rather than swallowed -- the owner believing a note
        was kept is the one wrong ending here.
        """
        try:
            if note is None:
                gone = await asyncio.to_thread(self.memory.forget_last)
                spoken = (self.FORGOT.format(text=gone.rstrip(" .!?"))
                          if gone is not None else self.NOTHING_TO_FORGET)
            elif not note:
                spoken = self.REMEMBER_WHAT
            else:
                path = await asyncio.to_thread(self.memory.remember, note)
                print(f"[butler] turn {turn}: memory note written: {path}")
                spoken = self.REMEMBERED.format(text=note.rstrip(" .!?"))
        except memory_mod.MemoryRefused as e:
            print(f"[butler] turn {turn}: memory note refused: {e}")
            spoken = self.MEMORY_REFUSED.format(why=str(e).rstrip(" ."))
        except OSError as e:
            print(f"[butler] turn {turn}: the memory store failed: {e!r}")
            spoken = self.MEMORY_FAILED
        await self._speak(s, turn, spoken, "reflex")
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)

    async def _apply_turn_mode(self, s: SatelliteSession, turn: int,
                               mode: str) -> None:
        """Change the turn mode and say so out loud.

        Spoken confirmation is not decoration here. The whole point of these
        modes is that they change when Butler stops listening, so a silent
        change would leave the user guessing why their next sentence was cut
        off -- or why it never submitted at all.
        """
        try:
            self.settings.set("turn_mode", mode)
        except (KeyError, ValueError) as e:
            await s.send_for_turn(turn, type="error", message=str(e))
            return
        # `capture_ceiling_ms` travels with every turn_mode change too, not
        # only at `ready` (AC-92's other half): a page that never reconnects
        # must not keep the OLD mode's budget for the rest of the conversation.
        await self.broadcast(type="turn_mode", turn_mode=mode,
                             capture_ceiling_ms=self._capture_ceiling_ms())
        await self._speak(s, turn, self.TURN_MODE_SAID[mode], "reflex")
        await self._end_speaking(s, turn)
        await self._await_playback(s, turn)

    async def _reply(self, s: SatelliteSession, turn: int, text: str) -> None:
        truncated = False
        # Recorded HERE, before this turn does anything else that could
        # await -- not in `_remember`'s `finally`, where it used to live.
        # `_reply` can be ABANDONED (a stop, a barge-in) and keep running in
        # the background long after the NEXT turn has already started and
        # finished; `_let_go`'s own docstring says a caller-abandoned
        # generator is only finalized whenever this coroutine eventually
        # unwinds. Deferring the user line to that unwind meant its position
        # in `hub.history` was decided by scheduling, not by the order the
        # owner actually spoke -- measured: a stopped turn's own question
        # landing AFTER the two ordinary turns that followed it
        # (docs/filing-and-pacing.md item 2 regression). The snapshot below
        # is what `ctx.history` reads, taken BEFORE this turn's own line
        # joins the list, so a turn is never handed its own utterance as
        # prior context.
        # A HEADLESS turn (a scheduled run) neither reads nor writes the
        # router's short memory: that is the owner's conversation's, and a
        # run in another chat must leave it exactly as it was (AC-461).
        history_snapshot = () if s.headless else tuple(self.history)
        if text.strip() and not s.headless:
            self.history.append(("user", text))
        # The audio leg, run beside the text leg (docs/filing-and-pacing.md
        # item 2). The chat and the start time are taken NOW: both are read
        # again in the `finally`, after a stop may already have released
        # this turn's ownership entries.
        voice = _TurnVoice(self, s, turn, s.chat_by_turn.get(turn, ""))
        started_ms = int(time.time() * 1000)
        # THE HEARTBEAT (AC-389): at once, then every `PROGRESS_EVERY_S`, and
        # cancelled first thing in the `finally` below, so no exit can leave
        # it reporting a turn that is over (D-1).
        progress = _TurnProgress()
        if voice.chat:
            self._progress[voice.chat] = progress
        beat = asyncio.create_task(
            self._heartbeat(s, turn, voice.chat, progress))
        try:
            # The back-fill is decoded HERE: after the final, which is what
            # made this turn start, and before the brain, which is the first
            # thing that could use it (AC-69). It rides in its own field and
            # is never merged into `text` or into `history`, because what was
            # overheard is not what was said to Butler (AC-70).
            ctx = TurnContext(turn_id=turn, mode=self.modes.mode,
                              history=history_snapshot,
                              overheard=await self._overheard(s, turn),
                              # Taken here and nowhere else (AC-199, AC-202).
                              # Reading clears the session's list, so an
                              # attachment belongs to exactly one turn -- and
                              # it rides on the CONTEXT rather than being
                              # folded into the utterance, which is
                              # `overheard`'s rule and its reason: what the
                              # owner SAID has to stay byte-for-byte what they
                              # said, in the record and on the wire.
                              # Taken for THIS CONVERSATION and no other
                              # (AC-247): a file attached while reading
                              # another chat stays pending there.
                              attachments=await self._show_attachments(
                                  s.chat_by_turn.get(turn, ""),
                                  s.take_attachments(
                                      s.chat_by_turn.get(turn, ""))),
                              # Decided in `_on_final` before anything spoke
                              # (AC-209), and carried here so the ADAPTERS'
                              # own fixed lines are said in it too. It is not
                              # a language instruction for the model: a model
                              # answers in the language it hears
                              # (`docs/language.md` D2, unchanged).
                              language=s.language,
                              # WHICH CONVERSATION, decided once in
                              # `_begin_turn` and carried rather than resolved
                              # again (AC-241, AC-246). The brain used to read
                              # `ChatStore.current()` for itself, so a device
                              # showing A while the daemon's pointer sat on B
                              # ran A's utterance through B's agent and wrote
                              # it into B's record. Two answers to one
                              # question is D-18; this is the one answer.
                              chat=s.chat_by_turn.get(turn, ""))
            stream = self.brain.turn(text, ctx)
            while stream is not None:
                # A proposal the daemon rules on continues the SAME turn on a
                # second stream rather than a second turn, so the owner hears
                # one answer to one question.
                follow = None
                try:
                    async for intent in stream:
                        if not s.owns(turn):
                            return       # barge-in landed; say nothing more
                        if isinstance(intent, Say):
                            # The TEXT goes now and the AUDIO joins the
                            # queue: nothing here waits on synthesis
                            # (AC-284). `_speak_text` still decides the mute
                            # (AC-238) and sends the frame; the voice task
                            # plays the jobs in order (AC-285).
                            before = s.reply_frames.get(turn, 0)
                            job = await self._speak_text(s, turn, intent.text,
                                                         intent.tier,
                                                         spoken=intent.spoken,
                                                         keep=intent.keep)
                            if job is False:
                                break
                            if s.reply_frames.get(turn, 0) > before:
                                voice.shown(before, intent.text, job)
                                progress.note("writing",
                                              chars=len(intent.text))
                            elif job is not None:
                                voice.put(job)       # a status: heard, unshown
                            continue
                        if isinstance(intent, (Clarify, AskConfirm, Dispatch,
                                               Steer)):
                            # These SPEAK INLINE through `_speak`. The queue
                            # plays out first, so two audio legs never
                            # interleave on one socket and the room hears
                            # the answer in the order it was given.
                            if not await voice.finish():
                                break
                        if isinstance(intent, Clarify):
                            await self._speak(s, turn, intent.question,
                                              "reflex")
                        elif isinstance(intent, AskConfirm):
                            follow = await self._on_ask_confirm(
                                s, turn, text, ctx, intent)
                            break
                        elif isinstance(intent, AskHuman):
                            # The coding agent is blocked on a human (AC-234).
                            # The loop CONTINUES: the brain has not finished,
                            # it is parked on the same stream waiting for the
                            # child, and the answer arrives on a route rather
                            # than through this generator. Breaking here --
                            # `AskConfirm`'s shape -- would end the turn and
                            # dispose the child that is waiting. The voice
                            # keeps playing what was already said meanwhile.
                            progress.note("asking")
                            await self._on_ask_human(s, turn, intent)
                        elif isinstance(intent, WorkerEvent):
                            # Elapsed time and a last action, never the
                            # answer itself (docs/parallel-agents.md phase 7).
                            # The loop CONTINUES for the same reason AskHuman
                            # does: the brain has not finished.
                            progress.worker(intent)
                            await self._on_worker_event(s, turn, intent)
                        elif isinstance(intent, (Dispatch, Steer)):
                            await self._speak(
                                s, turn, "I cannot run workers yet.", "reflex")
                        elif isinstance(intent, Done):
                            truncated = intent.truncated
                            await self._agent_finished(s, turn, voice.chat,
                                                       progress, started_ms)
                            break
                finally:
                    await self._let_go(stream, turn)
                stream = follow
            # Every word is on the page; now the voice finishes at its own
            # pace. INSIDE the try, so a barge-in landing during the playout
            # is a CancelledError here and the `finally` below still runs
            # whole (a cancel raised inside a `finally` would skip the rest).
            progress.note("speaking")
            await voice.finish()
        except asyncio.CancelledError:
            raise
        except Exception as e:                        # noqa: BLE001
            print(f"[butler] turn {turn} reply failed: {e!r}")
            await s.send_for_turn(turn, type="error", message=f"reply failed: {e}")
        finally:
            beat.cancel()
            if voice.chat and self._progress.get(voice.chat) is progress:
                del self._progress[voice.chat]
            # Whatever audio is still queued or mid-synthesis is over with
            # the turn (a stop, a barge-in, a lost turn, a failure). A no-op
            # when `finish()` already ran. BEFORE `_remember`, which reads
            # `s.spoken` -- that list must be final when it is read.
            await voice.stop()
            s.voices.pop(turn, None)
            await self._mark_unspoken(s, turn, voice, started_ms)
            # A question outlives nothing (AC-234). Every ending arrives here
            # -- answered, stopped, failed, barged in on -- and each of them
            # has already taken the child that raised the dialog, so a card
            # left on screen would be a control that lies. Before the
            # ownership check, like `_remember` below and for its reason: the
            # question was really asked whichever way the turn ended.
            await self.close_questions_for_turn(turn, "cancelled")
            # The user's own line is ALREADY in `hub.history` -- recorded at
            # the top of this method, before the turn could do anything that
            # awaits. What runs here is only the ASSISTANT half, and it is
            # still recorded on every exit and before the ownership check,
            # because the turn a barge-in interrupted still happened: the
            # owner heard part of an answer, and the next turn's router has
            # to know that or it classifies a follow-up to a question it
            # believes was never asked (AC-64, D-1's every-path discipline
            # applied to state instead of to the microphone).
            self._remember(s, turn, text)
            if s.owns(turn):
                await self._end_speaking(s, turn, truncated=truncated)
                await self._await_playback(s, turn)
                # Every path re-arms the turn it ENDED (D-1, AC-2). Naming it
                # is what stops a cancelled turn re-arming the one that
                # replaced it while this `finally` sat out the playback gate
                # (D-39) -- and skipping it then costs nothing, because the
                # session that moved on has its own terminal path.
                s._rearm(turn)

    async def _mark_unspoken(self, s: SatelliteSession, turn: int,
                             voice: _TurnVoice, started_ms: int) -> None:
        """Say where the HEARD part of this turn's answer ended, when some of
        what was shown never reached the room (docs/filing-and-pacing.md
        item 2, the owner's delegated ruling of 2026-09-26, AC-286).

        Text is shown the moment the brain yields it and audio follows at its
        own pace, so a stop or a barge-in can land with sentences on screen
        that were never spoken. They STAY -- retracting what the owner may
        already be reading is worse -- and are marked with the page's existing
        shown-not-heard treatment (`.unspoken`, AC-40), which until now was
        only ever written forward from a `spoken=False` frame.

        Two places learn it, from ONE position (D-18):
        * the page, `{type: "unspoken", turn, chat, from}`, where `from` is
          the index of the first unheard `reply` frame of the turn -- a
          position both sides count the same way (`_send_reply`, the page's
          own per-turn frame count). Sent while the turn is still owned:
          `abandon()` releases ownership only after this `finally` has run;
        * the record, through the harness host, which marks the LAST
          assistant message of this turn with `unspokenFrom`, so a reload
          draws the same line. Endpoint mode keeps no record and is skipped.

        `hub.history` is NOT touched here and needs nothing: `_remember`
        reads `s.spoken`, which only a completed audio leg ever appends to
        (AC-64, D-20).
        """
        first = voice.unheard_from()
        if first is None:
            return
        await s.send_for_turn(turn, type="unspoken", **{"from": first})
        host = getattr(self.session_brain(), "host", None)
        if host is None or not voice.chat:
            return
        tail = " ".join(e[1] for e in voice.entries if e[0] >= first).strip()
        host.mark_unspoken(voice.chat, tail, since_ms=started_ms)

    def _remember(self, s: SatelliteSession, turn: int, text: str) -> None:
        """The ASSISTANT half of one turn into the rolling transcript.

        The USER half is recorded at the top of `_reply`, not here -- this
        method used to append both, at turn END, and that let a stopped
        turn's own QUESTION land out of order: `_reply` for an abandoned
        turn can keep running in the background well after the next turn has
        already started and finished (`_let_go`'s docstring), and appending
        the user line only when that background coroutine finally unwound
        meant its position in `hub.history` was decided by scheduling rather
        than by when the owner actually spoke (docs/filing-and-pacing.md item
        2 regression, MEASURED: a stopped turn's question landing between two
        LATER turns' own questions). Splitting the writes fixes that without
        changing what the assistant half is gated on.

        `s.spoken` is the whole reason the assistant half can be honest, and
        it has been right since it was written with nothing reading it: a
        segment cut off mid-stream by barge-in is deliberately NOT appended
        there, so what arrives here is exactly the audio that reached the
        satellite. Telling the next turn's model it said a sentence the owner
        never heard is the context-desync defect `ports.TurnContext.history`
        was written to prevent, and it is the most-corroborated finding in
        `.references/` (D-20).

        ONE assistant entry per turn rather than one per segment: a reply is
        split into sentences for synthesis, and a transcript that showed six
        consecutive assistant messages would be describing Butler's speech
        pipeline rather than the conversation. A turn that said nothing at all
        adds no assistant entry -- that is what an interrupted turn looks
        like, and inventing an empty one for it would tell the model it
        answered with silence.

        The reflex line rides along, because the owner HEARD it. It is noise
        in front of a model, and it is the honest noise: the invariant is what
        was spoken, not what was worth spoken.

        **`s.spoken` is only OURS while the session still owns this turn**
        (D-6's identity re-check, one more time). `abandon()` awaits the reply
        task on a 3 s bound and then starts the next capture, which clears
        that list -- so a reply task that is wedged past the bound reaches
        this line after the NEXT turn has begun filling it, and recording
        then would file the next turn's answer under this turn's question.
        The assistant half is dropped in that case, because what this turn
        was heard to say is no longer knowable and a guess is the exact
        desync the invariant exists to prevent. (The user half has no such
        guard because it needs none: it was already recorded, in order, at
        turn start.)
        """
        if not text.strip() or s.headless:
            return
        if not (s.turn is None or s.turn == turn):
            print(f"[butler] turn {turn} finished after turn {s.turn} had "
                  f"started; dropping its answer, because what this turn was "
                  f"heard to say is gone (D-20)")
            return
        heard = " ".join(seg for seg in s.spoken if seg.strip()).strip()
        if heard:
            self.history.append(("assistant", heard))

    async def _let_go(self, stream, turn: int) -> None:
        """Finalize a turn generator the daemon stopped reading (D-18, D-1).

        The daemon BREAKS out of the brain's generator on four routes and
        resumes none of them: the `Done`, an `AskConfirm` it continues on a
        second stream, a synthesis failure that abandons the rest of the
        reply (3.8, D-4), and any exception the loop's own `except` swallows.
        A suspended async generator is finalized whenever the garbage
        collector says so -- and a harness turn holds a live
        full-capability child in that frame, so the collector would be
        deciding when a coding agent stops running. Worse in one
        interleaving: the NEXT turn starts first, the adapter's backstop no
        longer owns the turn, and the child is never disposed at all. AC-59
        holds a child ACROSS turns but only across ones that ENDED, and this
        is the caller half of that rule: closing the generator is what tells
        an unfinished turn to let its child go.

        So the CALLER lets go explicitly. The adapter still disposes at its
        own terminal event -- both halves, because a contract needs an
        assertion on the caller and the callee's tests were green (D-18).

        Bounded, loud and never fatal: this runs before `_rearm()`, so a
        teardown that wedged here would cost the microphone, which is the one
        thing housekeeping may never do (D-1, AC-36 owns the survivors).
        """
        close = getattr(stream, "aclose", None)
        if close is None:                 # a plain iterator has nothing to shut
            return
        try:
            await asyncio.wait_for(close(), timeout=BRAIN_LETGO_S)
        except Exception as e:                        # noqa: BLE001
            print(f"[butler] turn {turn}: the brain would not let go of the "
                  f"turn within {BRAIN_LETGO_S}s: {e!r}")

    async def _on_ask_confirm(self, s: SatelliteSession, turn: int, text: str,
                              ctx: TurnContext, intent: AskConfirm):
        """Rule on a proposal. Returns the stream to continue on, or None.

        The Brain proposes and butlerd disposes (plan.md 4.10): the router
        turns a `dispatch` verdict into `AskConfirm` and never into an action,
        so this is the ONLY route from a routed instruction to a coding agent.
        Two answers, since the owner retired AC-29's spoken grant on
        2026-08-18:

        * a brain that can carry the work out itself -- the instruction
          proceeds, whatever it does and whatever the permission mode says.
          There is no repeat-back, nothing is parked, and no tool set is
          widened, because the harness always runs at full capability;
        * anything else -- Phase 2 owns worker dispatch, so the proposal is
          spoken and nothing is ever spawned.

        The proposal's SUMMARY is what the agent is prompted with (AC-73,
        AC-28). The router is told the utterance was dictated and may carry
        false starts and mis-hearings, and `action.summary` is what it made of
        it; until this line that understanding reached only the refusal spoken
        two branches up, and the brain that could actually do the work was
        handed the raw transcript. `text` still travels beside it, because it
        is what the OWNER said and it is what the record holds (D-20, AC-64):
        this cleans the prompt, never the transcript.

        A blank summary prompts with the utterance instead. That is not a
        fallback in the banned sense -- it is the router declining to rewrite,
        the same shape as plan.md 3.8's fall-through -- and an empty prompt is
        a turn no agent can answer and nobody can diagnose.
        """
        action = intent.action
        brain = self._working_brain()
        if brain is None:
            await self._speak(
                s, turn,
                f"I would need to {action.summary}, "
                f"but I cannot do that yet.", "reflex")
            return None
        return brain.proceed(text, ctx,
                             instruction=action.summary.strip() or None)

    async def _await_playback(self, s: SatelliteSession, turn: int) -> None:
        """Hold the turn open until the satellite's speaker is actually quiet.

        The daemon finishes SENDING audio long before the satellite finishes
        PLAYING it -- it writes at socket speed, not at 24 kHz. Treating
        speak_end as the end of the turn re-arms the wake detector while the
        device's own speaker is still producing the tail of the reply, and
        the device then hears itself. That is Home Assistant's open Voice PE
        bug, and it is the single most likely real-world failure here given
        that browser echo cancellation may not attenuate our own playback at
        all (see .references/audio-internals.md).

        The wait is bounded by the audio we actually sent plus a margin, and
        expiring is not an error: a satellite that never reports back must
        still get its microphone returned (D-1).
        """
        if not s.audio_ms_sent:
            return
        budget = s.audio_ms_sent / 1000.0 + PLAYBACK_GRACE_S
        try:
            await asyncio.wait_for(s.playback_done.wait(), timeout=budget)
        except (asyncio.TimeoutError, TimeoutError):
            print(f"[butler] turn {turn}: no playback_done within "
                  f"{budget:.1f}s ({s.audio_ms_sent:.0f}ms of audio sent); "
                  f"re-arming anyway")

    async def _speak(self, s: SatelliteSession, turn: int, text: str,
                     tier: str, spoken: bool = True, keep: bool = True) -> bool:
        """Speak one reply, text leg then audio leg, back to back. Returns
        False if the rest of the turn must be abandoned -- a failed segment
        is not something to step over.

        This is the single-call shape every caller here still uses except
        `_reply`'s own Say loop (Clarify, the AskConfirm refusal, the
        "cannot run workers yet" line, and every test that calls
        `hub._speak` directly). Only that loop decouples the two legs --
        text at once, audio through `_TurnVoice` (docs/filing-and-pacing.md
        2.5) -- and it plays its queue out before any of these inline
        callers speaks, so two audio legs never interleave. This wrapper is
        byte-for-byte what a single `_speak` call always was.
        """
        job = await self._speak_text(s, turn, text, tier, spoken, keep)
        if job is False:
            return False
        if job is None:
            return True
        return await self._speak_audio(s, turn, *job)

    async def _speak_text(self, s: SatelliteSession, turn: int, text: str,
                          tier: str, spoken: bool = True, keep: bool = True):
        """The TEXT LEG of one Say: decide muting, send the `reply` frame,
        and say what the AUDIO LEG still owes.

        Split out of the old single-call `_speak` so `_reply`'s Say loop can
        send sentence N+1's text while sentence N's audio is still being
        synthesized (docs/filing-and-pacing.md item 2, AC-284) -- ONLY this
        half moved earlier; `_speak_audio` is what `_speak` always did after
        sending the frame, unchanged in behaviour, ordering or pacing
        (AC-285).

        Returns False if the turn is no longer owned (the caller stops),
        None if there is nothing left for the audio leg to do (a muted or
        text-only Say, already complete), or a `(text, tier, keep, opening)`
        tuple for `_speak_audio`.

        `spoken=False` sends the text to the page and stops there (AC-40).
        It deliberately does not touch the session state or the speak
        brackets: a speak_start with no audio behind it puts the satellite
        into its speaking state waiting for a drain that never comes, and the
        text is not recorded in `s.spoken` either, because history holds what
        the user actually HEARD (D-20).

        **A DEVICE SPEAKS THE CONVERSATION IT IS SHOWING** (AC-238, the
        owner's ruling in `docs/parallel-agents.md` D4). A caller asking for
        audio is OVERRULED into `spoken=False` when this device has said it is
        looking at some other conversation -- so looking away mid-reply
        delivers the rest written and marked shown-not-heard, at the next
        SENTENCE boundary, which is the granularity the ruling names and is
        what a `Say` already is on both substantive tiers. Checked FRESH for
        every Say here, at TEXT time -- and again by `_TurnVoice` when each
        queued sentence's AUDIO would start, because text now runs ahead of
        audio: a sentence already shown with `spoken=True` is muted by a
        look-away that lands before its audio, and the turn's `unspoken`
        frame then says so (docs/filing-and-pacing.md 2.3 item 4, 2.5).

        **THE CHILD IS NOT TOUCHED, AND THAT IS THE WHOLE POINT.** The only
        way to go quiet before this existed was `abandon()`, which in harness
        mode disposes the coding agent -- so going quiet by that path would
        kill the agent you just looked away from, which is exactly what AC-242
        and AC-243 exist to prevent. This branch calls nothing, aborts
        nothing and ends nothing: the turn runs to its own end and the record
        is complete, only silent.

        The limit, stated rather than left to be found: a single enormous
        `Say` is spoken to its end. The check is per `Say` and not per
        segment, because the `reply` frame carrying `spoken` goes out once per
        `Say` -- muting inside one would leave the page told that text was
        heard when half of it was not, which is the wrong lie to tell about
        the one thing the owner cannot see.

        `keep=False` is a spoken STATUS (`ports.Say.keep`): it is heard and it
        is nowhere else -- NO `reply` frame, so the page's transcript never
        shows it, and never in `s.spoken`, so it reaches neither the rolling
        history nor `chats.py`. There is no new protocol field for it and that
        is deliberate: "the page does not draw this" and "no frame is sent"
        are the same thing, and a `transient` flag on `reply` would be a frame
        every satellite has to learn to ignore.
        """
        if not s.owns(turn):
            return False
        turn_chat = s.chat_by_turn.get(turn, "")
        if spoken and not s.hears(turn_chat):
            # Looked away. Loud in the log because a daemon that has gone
            # quiet is indistinguishable from a broken speaker from the room,
            # and the page's own evidence is the shown-not-heard mark below.
            print(f"[butler] turn {turn}: this device is showing "
                  f"{s.showing!r} and the turn is in {turn_chat!r}; the "
                  f"rest of this reply is written, not spoken (AC-238). "
                  f"The coding agent is untouched.")
            spoken = False
        if not spoken:
            await self._send_reply(s, turn, text, tier, spoken=False)
            return None
        s.state = SPEAKING
        if keep:
            await self._send_reply(s, turn, text, tier, spoken=True)
        # READ BEFORE `_begin_speaking`, because that is what sets it. A reply
        # is several `Say` intents -- the conversation tier yields one per
        # sentence -- and each arrives here as its own call, so `speech.segments`
        # restarted at index 0 every time and the 8-word FIRST cap was applied
        # to EVERY sentence of every answer. That cap buys time to first audio
        # (AC-4) and there is only one first audio; past it the tight cap costs
        # a round trip per eight words and makes the clause search run far more
        # often than it was ever measured doing.
        opening = not s.speaking_started
        await self._begin_speaking(s, turn)
        return (text, tier, keep, opening)

    async def _send_reply(self, s: SatelliteSession, turn: int, text: str,
                          tier: str, spoken: bool) -> None:
        """The ONE sender of a `reply` frame, so the per-turn count that
        `{type: "unspoken", from}` indexes into cannot drift from the frames
        the page actually received (D-18). Counted only while the turn is
        owned, because `send_for_turn` drops the frame otherwise."""
        if s.owns(turn):
            s.reply_frames[turn] = s.reply_frames.get(turn, 0) + 1
        await s.send_for_turn(turn, type="reply", text=text, tier=tier,
                              spoken=spoken)

    async def _speak_audio(self, s: SatelliteSession, turn: int, text: str,
                           tier: str, keep: bool, opening: bool) -> bool:
        """The AUDIO LEG of one Say: synthesize and stream its segments.

        ONLY this method appends to `s.spoken` (AC-64, D-20) -- the router's
        rolling history (`_remember`) and `chats.py`'s harness-mode record
        both read what was actually SPOKEN, not what was generated, and that
        stays true whichever leg sent the text frame first: this is the one
        place that list is ever written, exactly as it was inside the old
        single-call `_speak`.
        """
        # Read ONCE for this reply rather than per segment, so a setting
        # changed from another device mid-answer cannot half-mute one (AC-110).
        aloud = self._speaking(s)
        # The RESOLVED voice, and the same one `speak_start` announced
        # (AC-212). This line used to be `self._voice()` inline at the
        # `synthesize` call below -- the raw setting -- so a blank voice meant
        # the wire named the language's voice and the engine was handed "" and
        # used its own English default.
        voice = self._voice_for(s.language)
        try:
            # Segment by segment, first one capped (4.7). Synthesizing the
            # whole reply as one unit would both blow AC-4 on a long opener
            # and make barge-in abandon the entire answer instead of at most
            # one segment.
            first_cap = (speech.FIRST_SEGMENT_MAX_WORDS if opening
                         else speech.LATER_SEGMENT_MAX_WORDS)
            for segment in speech.segments(text, first_max_words=first_cap):
                if not s.owns(turn):
                    s.interrupted = True
                    return False
                # The one place a segment's real boundaries are visible. The
                # trace sits above both the mute check and `synthesize`, so a
                # muted turn and a failing engine still leave the record that
                # says where the splitter actually cut.
                trace.trace("segment", turn=turn, tier=tier,
                            words=len(segment.split()),
                            text=trace.tail(segment, 120))
                if not aloud:
                    # Muted (AC-110). The engine is never asked, so there is
                    # no audio, no playback to wait for and nothing to abort
                    # -- and the turn still ends on the same speak_end and
                    # the same `_rearm` as a spoken one. Recorded as spoken
                    # anyway: the owner read it, and telling the next turn's
                    # router the answer was never given is the same desync in
                    # the other direction (AC-64, D-20). Unless it is a
                    # STATUS, which was never an answer to record either way.
                    if keep:
                        s.spoken.append(segment)
                    continue
                # handle=turn is what lets abandon()'s abort find this
                # synthesis. Without it the abort matches nothing and reads
                # as working (ports.Tts.synthesize).
                sent_any = False
                async for chunk in self.tts.synthesize(segment, voice,
                                                       speed=self._speed(),
                                                       handle=turn):
                    if not s.owns(turn):
                        # Cut off mid-segment. The segment is NOT recorded as
                        # spoken: the user heard part of a sentence, and
                        # telling the model it said the whole thing is the
                        # context-desync bug (ports.TurnContext.history).
                        s.interrupted = True
                        return False
                    await s.send_audio(turn, chunk)
                    sent_any = True
                if sent_any and keep:
                    s.spoken.append(segment)
        except Exception as e:                        # noqa: BLE001
            # Fail loud on a dead segment, and abandon the REST of the reply
            # rather than skipping silently to the next one -- a half-spoken
            # answer that sounds complete is worse than an audible failure
            # (D-4, plan.md 3.8).
            await s.send_for_turn(turn, type="error",
                                  message=f"speech failed: {e}")
            return False
        return True

    async def _begin_speaking(self, s: SatelliteSession, turn: int) -> None:
        """Send speak_start once per TURN, not once per intent.

        A reply can be several Say intents -- the Conversation tier streams
        sentence by sentence -- and bracketing each one would tell the
        satellite the reply ended several times. The page re-arms its wake
        detector on that boundary, so it would come back mid-reply while the
        speaker is still going: the very failure the playback gate exists to
        prevent, reintroduced one level up.
        """
        if s.speaking_started:
            return
        s.speaking_started = True
        # The SAME call `_speak` makes, on the same `s.language`, so what is
        # announced and what is synthesized are one value (AC-212).
        voice = self._voice_for(s.language)
        await s.send_for_turn(turn, type="speak_start",
                              voice=voice,
                              # AC-160. The language of the VOICE, so the wire
                              # says what the room will hear rather than what
                              # the settings asked for.
                              lang=self._voice_language(voice))

    async def _end_speaking(self, s: SatelliteSession, turn: int,
                            truncated: bool = False) -> None:
        """`truncated` says the reply stopped at the owner's token cap rather
        than finishing (AC-40). It rides the end of the reply because that is
        the one frame that exists exactly once per turn."""
        if not s.speaking_started:
            return
        s.speaking_started = False
        await s.send_for_turn(turn, type="speak_end", truncated=truncated)


def attach(app: web.Application, hub: Hub) -> None:
    app["hub"] = hub
    app.router.add_get("/ws", hub.handle)
