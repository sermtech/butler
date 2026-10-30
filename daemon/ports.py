"""The six seams -- Source, Asr, Tts, Brain, Harness, Notifier. See plan.md 4.10.

This file is the whole contract. Adapters live in daemon/adapters/ and are
the ONLY place a concrete engine is named outside config/. Nothing here
imports an engine, a model, or aiohttp; if it ever does, the seam has
leaked.

Why exactly six, and why these shapes, is argued in plan.md 4.10. The short
version, because it governs every edit to this file:

  Source  -- where audio comes from. Swappable so the native mic (Phase 5)
             and a WAV file (tests) are the same thing as a satellite.
  Asr     -- a stateful streaming SESSION, not a transcribe() call, because
             partials arrive mid-utterance and finalize() must emit a Final
             even for silence (D-7).
  Tts     -- ALWAYS an async iterator of PCM chunks, even for engines that
             cannot stream. Measured: collapsing the stream into one WAV
             costs +3252 ms on a 3-sentence reply.
  Brain   -- an utterance in, typed INTENTS out. Never a reply string: a
             string throws away tool use the moment an agent framework is
             swapped in behind this port.
  Harness -- the coding agent a brain talks to (4.4c) and the Worker Manager
             dispatches to (4.5). A CHILD PROCESS, not a call: one is
             started and disposed by the CALLER, and starting one is
             measured at 1.5-2.3 s, which is why a caller holds one across a
             conversation rather than per message (AC-59).
  Notifier -- where a finished automation's result is SENT off the machine
             (docs/automations.md 7, AU-19). A bot is a FILE and a service
             is an adapter, because the owner asked for "a different bot to
             different pc/ users" with no code change (R-A).

`Notifier` is the sixth, added 2026-09-26 with its argument in plan.md 4.10.
`Harness` was promoted from a sub-seam on 2026-08-17 with the argument in
plan.md 4.10, and this docstring said FOUR until its code landed, because a
contract file claiming a port that does not exist is the failure these
documents must never commit (D-12).

The load-bearing rule, which is a security property and not tidiness: the
Brain PROPOSES and butlerd DISPOSES. A Brain can emit Dispatch; only the
Mode Store decides whether a worker is ever spawned. A swapped-in brain must
not be able to widen what Butler is allowed to do (AC-9, AC-10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, runtime_checkable

# Audio format, fixed across every port. Satellites downsample to this before
# sending; adapters that produce anything else convert at their own edge.
SAMPLE_RATE_IN = 16_000    # mic path: 16 kHz mono int16
SAMPLE_RATE_OUT = 24_000   # speech path: 24 kHz mono int16 (Kokoro's native)


def _primary_subtag(tag: str) -> str:
    """The BCP-47 primary subtag: `pt-BR` -> `pt`, `en` -> `en`, `` -> ``."""
    return tag.strip().lower().split("-")[0]


def voice_ids(info: dict, language: str = "") -> list[str]:
    """The names in `Tts.info["voices"]`, optionally only for one language.

    THE ONE PLACE that knows the shape of an entry (AC-155). Three callers
    needed the names the day the entries stopped being plain strings -- the
    picker, the preview route and a seam test -- and three copies of
    `[v["id"] for v in ...]` is three places to forget when a fourth field
    arrives. `Tts.info` was itself the lesson: adding one key to it broke
    four implementations in two files.

    Matching is case-insensitive because the two engines disagree on case for
    the same locale and neither is wrong -- the recognizer catalogue says
    `pt-BR` and Kokoro's own table says `pt-br`. Normalising HERE, at the one
    seam that compares them, is the alternative to a mapping table in the
    core that would have to be maintained per engine pair.

    **A `language` with NO REGION matches on the PRIMARY SUBTAG** (AC-208).
    `pt` finds every `pt-BR` and `pt-PT` voice; `pt-BR` still matches only
    `pt-BR`, so nothing an owner picked by hand is widened. That is a RULE and
    not a locale table, which is the same argument the case-insensitivity
    above is making: the three vocabularies in play disagree by construction
    and cannot be reconciled by a list. The detector answers ISO-639-1 (`pt`),
    Kokoro reports `pt-br`, and the recognizer catalogue says `pt-BR` -- so
    without this, `voice_ids(info, "pt")` returned nothing and automatic
    language selection would silently have no voice to choose.

    A blank `language` means "no filter" rather than "voices with no
    language". That is what makes this safe to call before anything has a
    language: the shipped default is blank, and a picker that answered it
    with nothing would read as an engine with no voices.
    """
    entries = info.get("voices") or []
    want = language.strip().lower()
    if not want:
        return [v["id"] for v in entries]
    broad = "-" not in want
    out = []
    for v in entries:
        have = str(v.get("language", "")).strip().lower()
        if have == want or (broad and _primary_subtag(have) == want):
            out.append(v["id"])
    return out


def voice_languages(info: dict) -> list[str]:
    """Every PRIMARY SUBTAG this engine has a voice for, sorted, unique.

    The candidate set for automatic language detection (AC-207): Butler may
    only decide a turn is in a language it can answer in, so the classifier is
    restricted to exactly this list. Offering it every language it knows would
    let it pick one with no voice behind it, and the turn would then be
    answered in the engine's default -- a detector that is right and a room
    that hears the wrong thing.

    PRIMARY SUBTAGS rather than locales, and that is what the detector can
    honestly answer: it names a language, not a region, and `pt-BR` versus
    `pt-PT` is a VOICE choice the owner makes. Reducing here keeps that
    asymmetry in the one place that already knows an entry's shape.

    Reads the ENGINE rather than a list in the daemon, AC-42's rule: a tuple
    here is wrong the day a voice pack is enabled, and wrong invisibly.
    """
    subtags = {_primary_subtag(str(v.get("language") or ""))
               for v in (info.get("voices") or [])}
    return sorted(s for s in subtags if s)


# --------------------------------------------------------------------------- #
# Source -- audio in
# --------------------------------------------------------------------------- #
@runtime_checkable
class Source(Protocol):
    """One stream of microphone audio from one place.

    A WebSocket satellite, the desk mic (Phase 5), or a WAV file in a test.
    The pipeline consumes only this, which is what makes AC-24 achievable:
    the native source and the WS source run the identical downstream code.
    """

    device_id: str

    def frames(self) -> AsyncIterator[bytes]:
        """Yield 16 kHz mono int16 PCM. Ends when the source closes."""
        ...

    def alive(self) -> bool:
        """False once liveness lapses, so a capture can be torn down when the
        device vanishes mid-utterance (AC-17) rather than hanging forever."""
        ...


# --------------------------------------------------------------------------- #
# Asr -- speech to text
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AsrConfig:
    """Per-session facts EVERY recognizer understands.

    Deliberately only two. This carried a compute backend and a chunking
    preset until 2026-08-17, which are one engine's deployment facts: a cloud
    recognizer has no backend axis and no chunk size to pick, so it would
    have arrived to find two fields it must accept and ignore -- and the
    shared contract file ended up explaining another engine's internals.

    Engine-specific options are CONSTRUCTOR kwargs on the adapter, named in
    `config/models.json` under its own key and validated by the adapter at
    boot, so a typo is a loud failure rather than a silently ignored setting
    (AC-41).
    """
    model: str
    language: str | None = None    # None = model default; streaming rejects "auto"


@dataclass(frozen=True)
class Partial:
    """An in-flight transcript. `committed` will not change; `tentative` may."""
    committed: str
    tentative: str


@dataclass(frozen=True)
class Final:
    """The END of a turn, which is not the same thing as its text.

    An empty `text` is legal and must still be emitted, or claims and timers
    leak and the next wake is refused as busy (D-7, AC-6).

    `failed` says WHICH empty this is, and that is a different fact rather
    than a different ending (D-26). An empty text with `failed` False means
    the room was quiet; with it True it means the recognizer could not finish
    -- its budget expired, or its engine died mid-utterance -- and the
    utterance is gone. The two were indistinguishable until 2026-08-19, so a
    long dictation the engine could not decode in time completed normally and
    was answered with silence. Defaulted False so an adapter that has no way
    to fail still constructs a Final in one argument; an adapter that DOES
    synthesize an empty Final of its own owes the caller this flag.

    `lang` is the locale the recognizer was ASKED for on this utterance, as
    the engine echoes it back (AC-160). It is the stamp: a transcript that
    does not carry it cannot be audited later, and the absence of one is
    itself the marker of a turn recorded before this existed. None means no
    language was chosen, never "English".

    It is deliberately NOT a resolved value, and this comment said it was
    until a review on 2026-09-10 checked. There is nothing to resolve it
    against: `capabilities.languages` is a static tuple and no API on a
    loaded model reports what it actually decoded. Measured -- with `pt-BR`
    asked for and the English-only parakeet loaded, the engine refuses every
    frame and decodes nothing, and this field still reads `pt-BR`. That is
    the honest reading of "what was asked for", and it is the more useful
    one on a failed turn, because the request is the explanation. The guard
    that stops that mismatch reaching the engine is `Hub._speakable_language`.
    """
    text: str
    first_partial_ms: int | None = None
    finalize_ms: int | None = None
    failed: bool = False
    lang: str | None = None


@runtime_checkable
class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None:
        """Push 16 kHz mono int16. Non-blocking; the engine works elsewhere."""
        ...

    def finalize(self) -> None:
        """End the utterance. MUST cause exactly one Final, even for silence."""
        ...

    def reset_turn(self) -> None:
        """Abandon the current utterance without emitting a Final (barge-in)."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class Asr(Protocol):
    def open(self, cfg: AsrConfig, on_partial, on_final) -> AsrSession:
        """Callbacks may fire from a worker thread; the caller marshals."""
        ...

    async def transcribe(self, pcm: bytes, cfg: AsrConfig) -> str:
        """One COMPLETE clip in, its text out, once. Deliberately not a
        session.

        The other half of this port, and the opposite shape on purpose: no
        partials, no Final, no turn. `open`/`feed`/`finalize` is for audio
        that is still arriving and whose end nobody knows yet; this is for
        audio that is already over. Butler's ambient buffer holds the last N
        seconds of the room and decodes it at the moment the wake word fires
        (docs/ambient.md section 1), so there is nothing to stream and
        nothing to endpoint -- the clip's boundaries are already decided.

        Returning a plain `str` rather than a `Final` is the same
        distinction. A `Final` is a TURN's ending: `first_partial_ms`,
        `finalize_ms` and `failed` all describe how a live utterance
        finished, and a clip that was over before the call began has none of
        those facts to report.

        `pcm` is 16 kHz mono int16 -- the same bytes `AsrSession.feed` takes,
        SAMPLE_RATE_IN, and the format every satellite already sends. An
        engine wanting anything else converts at its own edge, and one of
        them does: transcribe.cpp's offline entry point wants FLOAT32, and
        handed these very bytes it ACCEPTS them and returns an empty
        transcript -- fast, confident and empty [MEASURED 2026-08-19]. It
        rejects a numpy int16 array loudly and the raw buffer silently, so
        the shape a caller would naturally pass is the one that fails
        quietly. That conversion is the adapter's problem precisely so it can
        never be a caller's.

        An EMPTY or all-zero clip returns "" and MUST NOT raise. A buffer of
        the room is silent almost all of the time, so a quiet clip is this
        method's normal input rather than an error; an adapter that threw
        would put an exception on the path that has to work every time
        (D-1).

        Async because the decode BLOCKS and butlerd is single-threaded: 30 s
        of audio costs about 1.9 s of CPU here [MEASURED 2026-08-19,
        test/manual/spike_ambient_offline.py], which as an inline call is
        every satellite frozen rather than one turn waiting. The adapter owns
        the thread hop.
        """
        ...

    async def warm(self) -> None:
        """Load the model and run one throwaway utterance, at boot.

        Not optional and not symmetry with Tts: an earlier prototype measured a cold start
        in MINUTES when the streaming graph warmed up mid-conversation, and
        without this the load lands on the first thing the owner ever says --
        on the event loop, freezing every satellite while it happens (D-10).
        """
        ...

    @property
    def info(self) -> dict:
        """Whatever the adapter wants the UI to show: arch, backend, variant.

        Read from the LOADED MODEL, never hard-coded per model slug (plan.md
        4.2). Deliberately not named here: which file format that model came
        in. A cloud recognizer has no local model file at all and still has
        capabilities to report.

        WARNING for adapters: this must not trigger a model load on the event
        loop. It is read during the hello handshake, and an adapter that
        lazily opens a session here turns a satellite connecting into a
        multi-second freeze for everyone. Warm at boot instead.
        """
        ...


# --------------------------------------------------------------------------- #
# Tts -- text to speech
# --------------------------------------------------------------------------- #
@runtime_checkable
class Tts(Protocol):
    def synthesize(self, text: str, voice: str, *, speed: float = 1.0,
                   handle=None) -> AsyncIterator[bytes]:
        """Yield 24 kHz mono int16 PCM chunks as they become available.

        An adapter that can only produce whole audio yields exactly one chunk.
        That is a deliberately cheap concession: defining this port as
        "returns audio" would permanently bake in the measured +3252 ms that
        the current whole-WAV HTTP hop costs a 3-sentence reply.

        `speed` is the speaking rate, 1.0 being the engine's own (AC-107). It
        is a KEYWORD argument with a default because it arrived after four
        doubles were already implementing this port -- `Tts.info` broke four
        in two files on the day it landed, all with the same one-line gap --
        and because 1.0 is the only value an engine with no rate control can
        honestly claim to honour. An adapter that CANNOT honour the number it
        is given refuses it loudly and never clamps or drops it: a control
        that saves, reports success and changes nothing is what teaches the
        owner the whole panel is decoration (AC-7b).

        `handle` names this synthesis so `abort` can find it, and it is part
        of the CONTRACT rather than an adapter extension. It was not, briefly,
        and the result was an abort that matched nothing: the daemon called
        abort(turn) while synthesis had registered anonymously, so the code
        read as correct, the fake's assertion passed, and no real synthesis
        was ever cancelled. A handle-less abort is not an abort.
        """
        ...

    async def abort(self, handle) -> None:
        """Cancel in-flight synthesis for `handle`; None means everything.

        MUST be dispatched outside any serializing command queue in the
        adapter. An abort queued behind the synthesis it cancels is a fake
        fix -- an earlier prototype found that one the hard way (D-4).
        """
        ...

    async def warm(self) -> None:
        """Synthesize something silent at boot. Cold start is 1.2 s measured,
        and paying it mid-conversation is the D-10 failure."""
        ...

    @property
    def info(self) -> dict:
        """At minimum {"engine": str, "voice": str, "sample_rate": int,
        "voices": [{"id": str, "language": str}, ...]}.

        `voices` carries a LANGUAGE per entry since 2026-09-10 (AC-155), and
        that change was forced rather than chosen. To offer the owner a voice
        for the language they picked, something has to know which voice
        speaks what -- and if the daemon learns it by reading an `id` prefix,
        then the daemon knows one engine's naming convention, which is
        exactly what `test_seams.py`'s AC-41 grep gate exists to prevent. So
        the engine states the fact and the daemon reads it. Use `voice_ids`
        below rather than reaching into the dicts, so this shape is known in
        one place.

        The precedent is `Asr.info["languages"]`, which has carried the same
        fact since it was written; this is the speech half catching up.

        `voice` is the adapter's OWN default -- what a caller gets for
        `synthesize(text, "")` -- and `voices` is everything it can speak.
        They answer different questions, and the daemon needs the first one
        because a blank voice is legal at this port but never on the wire:
        the satellite displays the name, and "" renders as a blank the owner
        cannot tell from a fault (AC-41, AC-7b).

        `voices` is the one that earns its place. The settings schema used to
        hardcode eleven Kokoro voice names, which was wrong the moment a
        second engine existed; discovering them here is what lets the picker
        be filled by the adapter instead of declared by the daemon (AC-42,
        completed by AC-41).

        And since AC-108 the list is the ENGINE's, which is a stronger claim
        than "the adapter's": a tuple in an adapter is still a list written
        into the daemon, and it was wrong by a factor of two and a half
        against the pool the engine actually had. So an adapter that can ask
        its engine ASKS IT -- in `warm()`, or wherever else it can afford to
        block -- and caches the answer for this property to serve.

        Same warning as Asr.info, and for the same reason: this is read
        during the hello handshake, so an adapter that starts its engine
        here turns one satellite connecting into a freeze for all of them.
        Serve it from a cache and fill the cache at boot. An adapter that
        could not fill it says so where it can be heard -- a failed `warm()`
        -- and never by inventing a list (D-25, AC-108).
        """
        ...


# --------------------------------------------------------------------------- #
# Brain -- the agentic middle
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Action:
    """A proposed unit of work. A PROPOSAL: nothing here has happened yet."""
    summary: str
    harness: str | None = None
    cwd: str | None = None
    writes: bool = False
    """Nothing in production reads this, DELIBERATELY, and it is not dead.

    It gated the spoken write grant until the owner retired AC-29 on
    2026-08-18; its remaining reader is `test/test_router.py`. Phase 2's
    worker dispatch is what re-earns it: AC-9's repeat-back over a WORKER
    survived that retirement, and "does this unit of work write" is the
    question it asks. Deleting it would mean re-deriving the same flag from
    the router in the commit that builds the Worker Manager.

    The cost is real and is paid on every utterance, which is why it is
    written down here rather than left to look free: this field is a property
    in the per-turn router schema (`dialogue.verdict_schema`), and schema
    properties the turn does not need measured a point of routing accuracy
    (4.4). A capability nothing reads is a claim rather than a contract -- the
    rule that deleted `HarnessCaps.tool_allowlist` in the same commit -- so if
    Phase 2 lands without reading this, it goes.
    """


@dataclass(frozen=True)
class Say:
    text: str
    tier: Literal["reflex", "conversation"] = "reflex"
    spoken: bool = True
    """False when this text belongs on the SCREEN and not in the room.

    One thing needs it today: the fragment left over when a reply is cut off
    at the owner's token cap (AC-40). Reading half a sentence aloud sounds
    like a broken Butler, and dropping it would hide what the model actually
    produced -- so it is shown and not said. It is a flag rather than a new
    intent because AC-32 wants the same thing for a different reason: a long
    answer is spoken as a pointer and rendered in full.

    Unspoken text never enters `TurnContext.history`. History holds what was
    SAID (see below); a model told it said something the user never heard is
    the context-desync defect that invariant exists to prevent.
    """
    keep: bool = True
    """False when this is a spoken STATUS rather than part of the answer.

    It is heard in the room and it is nowhere else: no `reply` frame, so the
    page's transcript never shows it, and never in `s.spoken` -- so it is not
    in `TurnContext.history` and not in `chats.py`.

    ONE thing needs it, and it is the reason the flag exists rather than a
    hypothetical: `brain_harness.NARRATION` is spoken before every harness
    spawn, because the agent's own thinking time is silence and silence reads
    as a dead microphone (D-10). It is a STATUS -- but it was being recorded
    like an answer, so every reply in the transcript and in Butler's own
    record opened with "Working on it." followed by the real answer, and a
    conversation reopened next month reads as Butler having said it twice.

    Distinct from `spoken=False` above and the two are near-opposites:
    that one is SEEN and not heard, this one is HEARD and not kept. Neither
    enters `history`, and for the same reason -- it holds what the owner and
    Butler actually exchanged.
    """


@dataclass(frozen=True)
class Clarify:
    question: str


@dataclass(frozen=True)
class AskConfirm:
    action: Action


@dataclass(frozen=True)
class Dispatch:
    action: Action


@dataclass(frozen=True)
class Steer:
    worker_id: str
    message: str


@dataclass(frozen=True)
class Done:
    tier: Literal["reflex", "conversation"]
    truncated: bool = False
    """The reply STOPPED at the owner's token cap rather than finishing.

    Carried so the page can mark it (AC-40). Deliberately not an error: a cap
    doing its job is a bound the owner configured, and the audible-error rule
    is for failures Butler did not choose (plan.md 3.8).
    """


@dataclass(frozen=True)
class AskHuman:
    """The CODING AGENT has a question and is blocked on the answer (AC-234).

    This is AC-49 reversed (`docs/parallel-agents.md` D2, the owner's ruling
    of 2026-09-11). It is NOT `AskConfirm`, and the difference decides
    everything downstream: `AskConfirm` is the ROUTER proposing an action the
    daemon rules on and answers by running a second stream, while this is a
    child process parked inside a tool call on a dialog whose id it minted.
    Routing this one back as a prompt would both fail to unblock the dialog
    and write a user message nobody said into the record.

    So the intent carries the port's own `Ask` verbatim and the brain keeps
    yielding: the daemon broadcasts the question, the answer comes back
    through `POST /api/answer`, and the SAME stream resumes when the child
    does. The brain does not wait here -- it waits on the next event, with
    the idle deadline suspended, because a waiting child emits nothing by
    definition (`brain_harness.HARNESS_IDLE_S`).
    """
    ask: "Ask"


@dataclass(frozen=True)
class WorkerEvent:
    """The coding agent's own activity, between the narration line and the
    answer -- the silence a 27-second generation and a hung daemon used to
    share (`docs/parallel-agents.md` phase 7, AC-233, AC-235).

    `kind` is this file's own word ("started", "tool_start", "tool_end"),
    never the harness's: `ToolStart`/`ToolEnd` already carry the adapter's
    vocabulary (`tool_id`, the harness's own name) and this intent exists so
    the wire does not have to. `detail` is the LAST ACTION line -- a tool
    name, plainly, the same "adapter's vocabulary, not the daemon's" reason
    `Ask.options` is a plain string (AC-41) -- and it is blank for "started",
    which is what lets a page tell "still going, no tool yet" from "the last
    thing it did".

    Carries no timing of its own: `Hub` stamps `elapsed_ms` at send time from
    the turn-start record `_begin_turn` already keeps, so a page gets one
    number to show rather than a start time to do arithmetic on.

    Three more kinds since 2026-09-26 (AC-388, AC-390): "thinking" and
    "tool_input" pass a harness `Activity` up, and "text" reports answer text
    as it ARRIVES, before the sentence splitter releases any of it; `chars`
    says how much arrived. They are EVIDENCE for the heartbeat and are never sent as a
    `worker_event` frame of their own: one per token would flood the socket,
    and the `progress` frame already carries the running count.
    """
    kind: str
    detail: str = ""
    chars: int = 0


# Closed set on purpose. Adding a member is a protocol change that every
# adapter and the announcer must be updated for -- which is the point.
Intent = (Say | Clarify | AskConfirm | Dispatch | Steer | Done | AskHuman
          | WorkerEvent)


@dataclass(frozen=True)
class RunningWorker:
    worker_id: str
    harness: str
    cwd: str
    task: str


@dataclass(frozen=True)
class TurnContext:
    turn_id: int
    mode: str

    history: tuple[tuple[str, str], ...] = ()   # (role, text), bounded
    """Bounded rolling transcript.

    INVARIANT: an assistant entry holds what was SPOKEN, never what was
    generated. When a reply is cut off by barge-in, only the segments whose
    audio actually reached the satellite belong here, and the entry is marked
    interrupted.

    This is the single most-corroborated lesson from the prior-art review --
    LiveKit truncates to the synchronized transcript, Pipecat changed to the
    same behaviour in 1.5.0, and OpenAI's Realtime API exposes
    conversation.item.truncate for exactly this. Get it wrong and the model
    believes it said three sentences the user never heard, so the next turn
    answers a question that was already answered, or refers back to advice
    nobody received.

    FILLED since AC-64 (2026-08-19), by `hub._remember` from
    `SatelliteSession.spoken` -- the segments whose audio actually reached the
    satellite, which is what makes the invariant above hold rather than merely
    be written down. It was empty for as long as this docstring said so, and
    that sentence outlived the fact by one commit (D-12).
    """
    workers: tuple[RunningWorker, ...] = field(default=())
    """Live workers. Empty means the adapter MUST NOT offer `steer` in its
    verdict schema: measured, including it unconditionally cost a point of
    routing accuracy and every failure was a spurious steer. Build the schema
    per turn (plan.md 4.4)."""
    overheard: str = ""
    """What the ROOM was saying before the wake word (AC-70, docs/ambient.md).

    A SEPARATE field, and the separation is the whole criterion. `history`
    above holds the conversation Butler is IN -- what the owner said to it and
    what it said back -- and this holds a half-minute of a room that was not
    addressing it at all: the other side of a phone call, somebody else in the
    kitchen, the television. Merging the two would file a remark made to
    another person as the owner's own instruction, and the reader on the far
    end of that is a full-capability coding agent.

    So three things are true of it by construction rather than by care. It
    never joins the utterance a `Brain` is given, so nothing here can be
    mistaken for what was asked. It never enters `hub.history`, so a leak
    cannot outlive the turn that caused it. And it never enters `chats.py`,
    Butler's durable record, which holds what the owner SAID (D-20).

    Empty is the normal value and means one of three things a caller must
    treat identically: ambient is off (AC-68's shipped default), no clip
    arrived for this turn, or the room was quiet. A brain that reads this owes
    the model a label saying which half is which -- context that was overheard,
    not an instruction -- because a model handed two blocks of text with no
    labels will act on either.
    """
    attachments: tuple = ()
    """What the owner SHOWED Butler for this turn (AC-202, docs/attachments.md).

    `attachments.Attachment` values: an id, the name the owner's file had, the
    kind (`image` or `document`), the media type, the PATH the bytes are at,
    and whatever text could be read out of it without a model.

    HERE rather than in `Brain.turn`'s signature, and beside `overheard` rather
    than merged into the utterance, for `overheard`'s reason: what the owner
    SAID has to stay byte-for-byte what they said, in the record and on the
    wire, and a filename folded into it is a word they did not speak.

    THE PATH IS MOST OF THE FEATURE. A coding agent can open a file, so an
    attachment is useful in harness mode even when no model can see --
    which is why this field carries a path and not bytes, and why a
    brain that cannot see is still expected to do something with it rather
    than to refuse.

    Empty is the normal value. A brain that reads this owes the model the same
    kind of LABEL `overheard` owes it: what is attached is not what was asked.
    """
    language: str | None = None
    """What language this turn is IN (AC-209, docs/language-auto.md).

    A BCP-47 tag -- a primary subtag when it was detected from the owner's
    words, a full locale when they pinned one in Settings. None means no
    decision was reached and a reader should say whatever it would have said
    before this field existed, which is English for Butler's own fixed lines.

    Decided ONCE, from the owner's utterance, before anything is spoken --
    `Hub._turn_language`. So it is a fact about the turn rather than a running
    guess, and `speak_start` can announce a voice it is sure of.

    What reads it is deliberately narrow: the eight lines Butler says that no
    model wrote (`dialogue.line`). It is NOT a language instruction for the
    brain -- the owner ruled that a model answers in the language it hears
    (`docs/language.md` D2), and that ruling is not reopened by this field
    existing. A brain that starts reading it is reversing a decision, not
    using a parameter.

    Carried HERE rather than as an argument to `Brain.turn`, beside `overheard`
    and `attachments`, for their reason: the utterance stays byte-for-byte what
    the owner said and everything ABOUT the turn rides on the context.
    """
    chat: str = ""
    """WHICH CONVERSATION this turn is in (AC-241, AC-246).

    The daemon decides it ONCE, in `hub._begin_turn`, through the one resolver
    `hub.chat_for` -- what the device says it is SHOWING, and otherwise the
    conversation the daemon is holding. It is carried rather than re-derived
    because two places used to answer this question independently: the hub
    resolved it from the device's view and `brain_harness.turn()` resolved it
    from `ChatStore.current()`, and nothing handed one to the other. Measured
    2026-09-12: the desk showing A with the pointer at B ran the desk's
    utterance through B's coding agent, in B's directory, into B's record,
    while the mute compared against A.

    A conversation id and NOT engine vocabulary, so AC-44 is untouched: an
    adapter that keeps no conversations simply ignores it, exactly as
    `brain_endpoint` ignores `workers`.

    Empty means the daemon can name none, which is every brain with no session
    store -- endpoint mode holds no conversations at all. An adapter that DOES
    hold them treats empty as "the conversation a turn goes to", because that
    is the only answer a caller who named none can have meant.
    """


@runtime_checkable
class Brain(Protocol):
    def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]:
        """Consume one finalized utterance, yield intents as they are decided.

        Yields rather than returns so a Say can start synthesizing while the
        rest of the reply is still being generated. Every intent belongs to
        ctx.turn_id; butlerd discards intents for an abandoned turn (D-6), so
        a slow external brain cannot speak over a newer turn.
        """
        ...

    async def abort(self, turn_id: int) -> None:
        """Stop working on `turn_id`. Called on barge-in.

        The daemon calls this BEFORE cancelling its own reader task, for the
        same reason `Tts.abort` exists: cancelling the consumer is not
        cancelling the producer (D-4). Stop reading first and the brain goes
        on generating into a socket nobody drains -- an HTTP stream still
        billing, or a harness child still working -- and the next turn queues
        behind the abandoned one. Measured on the TTS side of exactly this
        shape: 7887 ms to the next reply's first audio before the engine
        could be told to stop.

        An unknown or already-finished turn_id is a NO-OP, never an error.
        Barge-in races completion by nature, and a brain that raised on a
        turn that had just ended would turn a routine race into a failed
        abandon -- which is a held microphone (D-1).

        Asserted on the CALLER as well as here (D-18): conformance can only
        prove an adapter accepts the call.
        """
        ...

    async def warm(self) -> None:
        """Whatever makes the first real turn not pay a cold start (D-10).

        A router health check, a one-token completion, nothing at all for a
        fake. Called once at boot beside Asr.warm and Tts.warm. The number
        that makes this worth a port method rather than an adapter detail:
        the cold load of the SMALLEST model on this machine is 15.85 s
        [MEASURED, plan.md 4.4], ten times Kokoro's, and untended it lands on
        the first thing the owner ever says.

        NOT the keep-warm loop. Keep-warm (4.4a) is daemon policy about a
        shared GPU -- it has settings, an interval and two refusals, and it
        stays hub-side. This runs once.

        Loud but never fatal: butlerd logs a warm failure and boots anyway,
        because a slow first turn is survivable and a daemon that refuses to
        start leaves the owner with no microphone at all (D-1).
        """
        ...

    @property
    def info(self) -> dict:
        """At minimum {"engine": str}. What the UI shows for the brain.

        This one is not cosmetic. `brain_fake` answers five substrings and
        says "Let me think about that" to everything else, so a daemon
        quietly running it is indistinguishable from a daemon whose real
        model is being unhelpful. Naming the engine in hello is what tells
        those apart.

        Read during the hello handshake -- must not connect or load.
        """
        ...


# --------------------------------------------------------------------------- #
# Harness -- the coding agent
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HarnessCaps:
    """What a harness can do, read by CAPABILITY and never by name (AC-35).

    `native_resume` says the harness hands back an opaque session id to
    resume by; pi resumes by being pointed at its own session FILE, and
    claude by an id it prints on first output, so the two need different
    callers -- which is exactly what a name test gets wrong on the third
    harness. The predecessor tested names and stranded eight dead chats.

    `steerable` is disclosed HERE and not acted on anywhere: there is no
    `steer` method on this port, so `True` advertises nothing an adapter
    author can call today. It is read by the Worker Manager (plan.md 4.5),
    which is what makes it a field ahead of its consumer rather than a dead
    control (docs/architecture-seams.md phase 5). Adding the method is a
    PORT change -- every adapter gains it in the same commit -- so an
    adapter answering True is stating a fact about its engine, not
    promising Butler a call.
    """
    native_resume: bool
    steerable: bool          # accepts a message mid-turn
    images: bool


@dataclass(frozen=True)
class HarnessSpec:
    """The frozen record of what a child IS. Built by the daemon, never by
    the adapter, and a new one means a NEW child.

    Every field is fixed at spawn for the harnesses that exist, so there is
    no setter anywhere: moving the working directory and switching the model
    are both dispose-plus-start (AC-31). That is why callers record the spec
    they asked for -- it is the only honest account of what the running child
    was pointed at.

    `tools` is a plain ALLOW-list, empty by default: `()` means no
    restriction, which is the harness's own full set (`bash` included). It is
    NOT the retired spoken grant (AC-29) and carries no mode, no ceremony and
    no repeat-back -- it is filled from the persistent `harness_tools` setting
    (read_only or full) and the adapter turns a non-empty tuple into the
    harness's read-only flag. Re-added on 2026-08-18 as the minimal slice of
    the plumbing 653a44d deleted, driven by a knob rather than a grant.

    Validation is here rather than at the adapter because a contradictory
    spec must fail at CONSTRUCTION and not at first use (plan.md 4.4c): the
    predecessor's unvalidated model spec became a child that died at startup,
    which reads as the harness being broken.
    """
    model: str | None        # None = the harness's own default; "" is not
    cwd: str
    session_file: str
    tools: tuple[str, ...] = ()   # () = no restriction; else a read allow-list
    append_prompt: str | None = None
    """A FILE whose contents are appended to the child's system prompt, or
    None for nothing appended (docs/identity-memory.md 4.2, AC-373).

    A Butler-minted, content-addressed PATH and never the text: the owner's
    identity is prose, prose holds quotes and ampersands, and a harness
    spawned through `cmd` re-parses its line. Being content-addressed is what
    makes it a spec FIELD rather than a side channel -- an edited identity is
    a different path, so AC-60's one comparison replaces the held child on
    its next turn (AC-374) with no second freshness rule."""
    memory_url: str | None = None
    """Butler's own LOOPBACK address, where the child may reach the memory
    tools, or None for no memory tools (docs/identity-memory.md 12.6,
    AC-440). A port word, not a harness word: the pi adapter turns it into
    its extension and flag, and another adapter would turn it into its own
    mechanism. Constant for a daemon's life, so it never forces a respawn."""
    chat_id: str | None = None
    """WHICH CONVERSATION this child serves, for the task tool
    (docs/automations.md AU-9, AC-454): a task an agent adds is filed under
    this conversation's project, so the daemon needs to know whose it is --
    and a child is held for ONE conversation (AC-59), so this never changes
    under a running child. None with no `memory_url` (no tools, nothing to
    file). A conversation id, not engine vocabulary."""

    def __post_init__(self):
        if not self.cwd:
            raise ValueError(
                "HarnessSpec needs a working directory: a tool-enabled agent "
                "spawned wherever the daemon happens to stand is AC-31 "
                "failing silently")
        if not self.session_file:
            raise ValueError(
                "HarnessSpec needs a session file; Butler owns the path and a "
                "conversation that cannot be named cannot be resumed (AC-35)")
        if self.model is not None and not self.model.strip():
            raise ValueError(
                "model is None for the harness's own default; a blank string "
                "is a second spelling of that, and an adapter will eventually "
                "pass it through as a model literally named ''")


@dataclass(frozen=True)
class TextDelta:
    """A fragment of the answer as it is generated. NOT guaranteed: a
    measured provider emitted none at all and put the whole message in
    TurnEnd, so nothing may depend on these arriving (AC-47).

    Concatenated VERBATIM, the deltas of a prompt are its answer. So the
    separator between two text blocks -- the text either side of a tool call
    -- is the ADAPTER's to emit: the caller has no event to hang one on.
    Emitted as "\\n\\n" when a block ENDS rather than when the next begins,
    because that whitespace is what lets the caller's sentence splitter
    release the block's last sentence -- before a tool runs, not after."""
    text: str


@dataclass(frozen=True)
class Activity:
    """The child is PRODUCING, and it is not the answer (AC-388,
    docs/turn-feedback.md). `kind` is "thinking" (the model's reasoning) or
    "tool_input" (the model writing a tool call's arguments); `chars` is how
    much of it arrived in this piece.

    Evidence and nothing else: it re-arms the caller's idle deadline and it
    is counted for the heartbeat, and none of it is spoken, shown or
    recorded -- reasoning is the model's scratch work (`harness_pi._delta`).
    It exists because dropping it made a model that reasons for four minutes
    indistinguishable from a silent child, and the idle deadline then ended a
    turn that was producing tokens the whole time."""
    kind: str
    chars: int


@dataclass(frozen=True)
class ToolStart:
    tool_id: str
    """The HARNESS's own id, never one Butler minted.

    The predecessor discarded the id it was given and kept a single
    current-step slot, so parallel calls overwrote each other -- 8 of 19
    starts never closed. A voice assistant narrating tools would announce
    eight things it never finished (plan.md 4.4c).
    """
    name: str


@dataclass(frozen=True)
class ToolEnd:
    tool_id: str             # matches its ToolStart
    name: str
    is_error: bool
    """The REAL outcome. The predecessor hardcoded success, which made a
    failed tool literally unreachable in its own transcript."""


@dataclass(frozen=True)
class TurnEnd:
    """One agent turn settled. `text` is the WHOLE message of that turn,
    always -- it is the only text a non-streaming provider ever sends."""
    text: str


@dataclass(frozen=True)
class AgentEnd:
    """The prompt is over: the loop settled, however many turns it took.

    Fires ONCE per prompt, never per TurnEnd. For a voice assistant that is
    the difference between announcing an answer once and announcing it five
    times (AC-48).
    """


@dataclass(frozen=True)
class Failed:
    """The prompt is over because it broke. The other terminal event.

    Deliberately not named `Error`: it is a value the caller SPEAKS, not an
    exception it catches. The predecessor emitted its equivalent only on a
    JS exception, so a provider failure left the turn lock held forever --
    which is why exactly one of Failed and AgentEnd is required per prompt,
    asserted in conformance AND on the caller (AC-48, D-18).
    """
    message: str


@dataclass(frozen=True)
class Ask:
    """The child is BLOCKED on a question for a human (AC-234, AC-49 reversed).

    Until 2026-09-12 `harness_pi.cancellation_for` answered every one of these
    itself and told the agent the user had dismissed the dialog, so a blocked
    coding agent looked exactly like one that had decided on its own and
    nobody ever learned it asked (`docs/parallel-agents.md` D2).

    **`qid` is the HARNESS's own id and Butler never mints one.** It is
    `ToolStart.tool_id`'s rule for `ToolStart.tool_id`'s reason: the answer
    has to be routed back to the dialog that raised it, and an id Butler
    invented would have to be mapped onto that one -- which is the mapping
    the predecessor got wrong when it kept a single current-step slot.

    `method` is the harness's own word for the SHAPE of the question, and the
    shape decides what can answer it: `select` carries `options` and a text
    box cannot answer it, `confirm` is a yes or no, `input` is free text with
    a `placeholder` and a `prefill`. It is a plain string rather than an enum
    because it is the adapter's vocabulary, not the daemon's (AC-41).

    `options` are PLAIN STRINGS, deliberately: they are drawn as buttons AND
    they are what a voice answer says, so an option that were a record with a
    hidden value would be unsayable.

    **`deadline_ms` is PASSED THROUGH and never invented** (research
    2026-09-12, `docs/parallel-agents.md` 9.1). pi's own RPC lets an extension
    attach a timeout and pi AUTO-RESOLVES the dialog agent-side when it
    expires -- "the client does not need to track timeouts". So an adapter
    carries the number when its harness gave one and None when it did not,
    and Butler draws the clock it was handed. Butler NEVER auto-answers: the
    resolved default of a timed dialog is byte-identical to a dismissal, so a
    Butler-invented deadline would be indistinguishable from the owner having
    said no.
    """
    qid: str
    method: str
    title: str = ""
    message: str = ""
    options: tuple[str, ...] = ()
    placeholder: str = ""
    prefill: str = ""
    deadline_ms: int | None = None


@dataclass(frozen=True)
class Answer:
    """What a human said back to an `Ask`, in the DAEMON's vocabulary.

    Typed rather than a dict, so the harness's own spelling of a reply --
    pi's `{value}` / `{confirmed}` / `{cancelled}` -- stays inside its adapter
    where every other piece of its vocabulary already is (AC-41). A dict
    crossing this port would be the caller composing the harness's protocol.

    `cancelled` is the human dismissing the dialog, which is what Butler used
    to send unasked for every question. It is still reachable, now as a
    deliberate act rather than as the only behaviour.
    """
    cancelled: bool = False
    value: str | None = None
    confirmed: bool | None = None

    def __post_init__(self):
        if self.cancelled:
            if self.value is not None or self.confirmed is not None:
                raise ValueError(
                    "Answer(cancelled=True) carries no value: a dismissal and "
                    "a choice are different answers and a reader would have "
                    "to guess which one won")
            return
        given = [f for f in ("value", "confirmed")
                 if getattr(self, f) is not None]
        if len(given) != 1:
            raise ValueError(
                f"an Answer is exactly one of cancelled, value or confirmed; "
                f"got {given or 'none of them'}. Failing here rather than at "
                f"the adapter, because a half-filled answer reaches a blocked "
                f"coding agent and unblocks it with something nobody said")


# Closed set, like Intent and for the same reason: adding a member is a
# protocol change every adapter and every consumer must be updated for.
HarnessEvent = (TextDelta | ToolStart | ToolEnd | TurnEnd | AgentEnd | Failed
                | Ask | Activity)

# The two events that END a prompt, as one tuple every reader isinstance()s
# against. It lives beside the union because three separate readers need it --
# the conformance suite, `brain_harness` (7.4) and the Worker Manager (4.5) --
# and a copy per reader drifts the day a third terminal event lands, leaving a
# reader that waits forever for an ending it no longer recognises (AC-48).
TERMINAL_EVENTS = (AgentEnd, Failed)


@runtime_checkable
class Harness(Protocol):
    """One coding agent. The CALLER decides how long it lives.

    Two callers, which is why this is a port and not a parameter: a brain
    talks to one as a conversation (4.4c) and the Worker Manager dispatches
    to one as a background job (4.5). Neither may branch on harness NAME --
    what differs between them is read from `caps` (AC-44).

    Lifetime is deliberately NOT part of this contract, and the history is
    the argument: a child has been held from boot, spawned per message
    (AC-46), and is now held per conversation (AC-59) without this file
    changing once. What an adapter owes is the same either way -- everything
    about a child is fixed at `start`, and `dispose` must leave nothing
    behind.

    A conversation therefore lives in the SPEC, not in the object: the same
    session reference handed to the next child is what carries it, and an
    adapter that kept state between `dispose` and the next `start` would be
    keeping something the caller has already decided it does not have.
    """

    async def start(self, spec: HarnessSpec) -> None:
        """Bring up the child described by `spec`, ready to be prompted.

        Called INSIDE the turn, by the first turn of a conversation and by
        any turn whose spec no longer matches the running child (AC-59,
        AC-60). The measured startup is 1.5-2.3 s and it is the harness
        coming up rather than any model choice, so no model choice makes it
        cheaper: it is dead air, covered by one spoken line before the spawn
        (D-10, docs/harness-mode.md 10.7).
        Raising is how a failed spawn becomes a failed turn, which the caller
        SPEAKS (AC-47) -- it must never leave a half-started child the caller
        cannot dispose.
        """
        ...

    def subscribe(self) -> AsyncIterator[HarnessEvent]:
        """The child's normalized event stream, drained by ONE reader.

        Ends when the child is disposed, so the reader task ends with it. An
        iterator that merely stopped producing would leak a task per
        respawn, and a respawn happens mid-conversation (AC-31).
        """
        ...

    async def prompt(self, text: str) -> None:
        """Send one utterance. Exactly one terminal event will follow it."""
        ...

    async def answer(self, qid: str, answer: Answer) -> bool:
        """Answer an `Ask` the child raised, and say whether it still held it.

        The RETURN VALUE is the whole point and it is not politeness: the
        harness under this port drops an unknown dialog id SILENTLY, so a
        late answer forwarded hopefully leaves the human believing they
        approved something the agent already defaulted on (AC-250, the
        recorded Codex failure in `docs/parallel-agents.md` 9.4). False means
        this child no longer holds that id -- it resolved the dialog on its
        own timeout, or it is gone -- and the caller must report that rather
        than reporting success.

        Out of band, never a prompt: the child is parked INSIDE a tool call,
        so the answer has to reach the dialog rather than the agent loop.
        """
        ...

    async def abort(self) -> None:
        """Stop the current prompt. Barge-in, not a failure.

        The prompt still ends with its one terminal event -- an abort that
        produced silence would leave the reader waiting forever, which is
        indistinguishable from a dead microphone (D-1, AC-48). Measured on a
        real child: the abort settled in 1.51 s and the next prompt answered
        normally, so the child stays usable and this is not dispose.
        """
        ...

    async def dispose(self) -> None:
        """Tear the child down. Idempotent, and never raises.

        This is half of every change to what a child IS: the working
        directory and the model are fixed at spawn, so changing either is
        dispose-plus-start (AC-31). A child must not outlive the daemon
        either way (AC-36, D-24).
        """
        ...

    @property
    def caps(self) -> HarnessCaps:
        ...

    @property
    def info(self) -> dict:
        """At minimum {"engine": str}. What the UI shows for the harness.

        Same warning as the engine ports: static, cheap, no question asked
        of the child. This is the port whose adapter IS a child process, so
        an `info` that talked to it would freeze the loop for as long as the
        child took to answer (D-25).
        """
        ...


@dataclass(frozen=True)
class NotifyResult:
    """What one send came to. `detail` is "" on success and otherwise the
    SERVICE's own reason ("502 Bad Gateway", "401 Unauthorized") -- never an
    exception string, because the request URL can carry a secret."""
    ok: bool
    detail: str = ""
    # The service's own id for what was sent, when the caller needs to know
    # a later reply is TO it (`ask`, MS-22): a WhatsApp message id, a
    # Telegram callback token. "" otherwise.
    ref: str = ""


@dataclass(frozen=True)
class Incoming:
    """One message the OWNER sent on an INBOUND channel (MS-20).

    `quoted` is the `NotifyResult.ref` of a message this one answers --
    WhatsApp's quoted message id, a Telegram button's callback token -- and
    "" when it answers nothing in particular. The channel says WHICH message
    was quoted; deciding what that means is the daemon's (`notify.Questions`),
    so the rules for an answer are written once for every channel."""
    text: str
    quoted: str = ""


def inbound(adapter) -> bool:
    """Does this notifier (or notifier class) declare the inbound capability
    (MS-20)? Declared on the CLASS, never set per instance."""
    cls = adapter if isinstance(adapter, type) else type(adapter)
    return getattr(cls, "INBOUND", False) is True


@runtime_checkable
class Notifier(Protocol):
    """One configured notification channel: a bot, not a service.

    docs/automations.md 7 (AU-19, AC-471..AC-473). The caller knows no
    service; the adapter class states what a channel FILE of its kind holds
    (`LABEL`, `FIELDS`, `problem`) and `daemon/notify.py` builds one per
    file through `NOTIFIERS`, a closed table -- a file cannot name code.

    `send` NEVER raises for a delivery failure: a service that is down, a
    token it refuses, and a timeout are all `NotifyResult(False, reason)`,
    because a failed notification must change nothing else about a run
    (AC-472). It raises only for a programming error. Blocking I/O runs off
    the loop (D-1).

    INBOUND, AN OPTIONAL CAPABILITY, NOT A SEVENTH PORT (docs/messaging.md
    MS-20, AC-525; the argument is in plan.md 4.10). A class that declares
    `INBOUND = True` also implements:

    * `own(settings)`, a classmethod: the channel that reaches the OWNER
      (WhatsApp's own chat), or None while this kind cannot (switched off).
    * `async start(on_message)`: from now on hand every owner message to
      `await on_message(Incoming)`. It answers None when the message is not
      for the caller -- the adapter then treats it exactly as it would have
      -- or the text to say back in the chat, quoted to that message.
    * `async stop()`: no more calls to `on_message`. Idempotent.
    * `async ask(question: Ask, where: str) -> NotifyResult`: show a coding
      agent's question to the owner, `where` naming the run or chat; `ref`
      is what a reply to it will carry as `Incoming.quoted`. Never raises
      for a delivery failure, like `send`.

    Callers test for it with `inbound()`, never by kind name.
    """

    async def send(self, title: str, text: str) -> NotifyResult:
        ...


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
# A dict, not a plugin system. config/models.json names the key; adding an
# adapter means importing it here. If this ever grows entry-point discovery,
# the abstraction tax has outgrown the benefit (plan.md section 7).
SOURCES: dict[str, type] = {}
ASRS: dict[str, type] = {}
TTSS: dict[str, type] = {}
BRAINS: dict[str, type] = {}
HARNESSES: dict[str, type] = {}
NOTIFIERS: dict[str, type] = {}


def register(registry: dict[str, type], name: str):
    def _wrap(cls):
        if name in registry:
            raise ValueError(f"adapter {name!r} registered twice")
        registry[name] = cls
        return cls
    return _wrap


def build(registry: dict[str, type], name: str, *args, **kwargs):
    """Fail fast and loudly: an unknown adapter name is a config error, not a
    reason to fall back to a default."""
    if name not in registry:
        raise KeyError(
            f"no adapter named {name!r}; known: {sorted(registry)}")
    return registry[name](*args, **kwargs)
