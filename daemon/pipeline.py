"""One turn of capture: audio in, exactly one Final out. plan.md 4.2.

This is where the endpoint policy lives, and its location is the point. The
spike authored the policy in the browser (web/app.js EOU_* and the
ON/OFF/HANG_MS energy gate) and only asked the server for the smart-turn
verdict. Butler moves BOTH here, because a native microphone source (Phase
5) has no page to author them and AC-24 promises that both sources drive
the identical pipeline. Leaving it client-side would make AC-24 a rewrite
discovered four phases late.

The one invariant everything else depends on: a turn ALWAYS ends. Silence
ends it, the hard cap ends it, a dead source ends it, an exception ends it.
Every path emits exactly one Final, and an empty Final is a legal turn end
(D-7) -- it is the turn's END, not its text.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time

import numpy as np

from . import endpointing
from . import trace
from .ports import AsrConfig, Final

# --- energy gate (was ON/OFF/HANG_MS in web/app.js) ------------------------ #
RMS_ON = 0.020          # above this, speech has started
RMS_OFF = 0.012         # below this, we are in silence (hysteresis)
MIN_SPEECH_MS = 200     # shorter than this is a cough, not a turn

# Those two are FLOORS, not absolutes. getUserMedia leaves autoGainControl on
# by default, so a room's amplified noise floor can sit above RMS_OFF -- and
# then silence never accumulates, no endpoint check ever runs, and the turn
# survives to the absolute ceiling with the microphone open. The thresholds
# therefore track the quietest thing recently heard and rise with the room.
# They only ever rise: dropping below the tuned constants in a silent room
# would make the gate fire on the noise of the microphone itself.
FLOOR_MARGIN = 2.0      # silence is anything within this multiple of the floor
FLOOR_DECAY = 1.02      # how fast the estimate is allowed to climb per frame

# --- endpoint policy ------------------------------------------------------- #
# The smart-mode numbers moved to endpointing.py, which is where the policy
# that uses them lives, and they are cited there. These aliases are kept
# because they are the names plan.md 4.2 and the tests use -- but there is
# exactly ONE definition of each, on purpose: two copies of a tuning constant
# is how the value the code runs and the value everyone reads drift apart
# (D-12).
EOU_SILENCE_MS = endpointing.SMART_FIRST_CHECK_MS
EOU_RECHECK_MS = endpointing.SMART_RECHECK_MS
EOU_THRESHOLD = endpointing.SMART_THRESHOLD

# With no endpointer wired, 280 ms of silence is not evidence that anyone
# finished a sentence -- it is an ordinary pause for breath. The 280 ms is
# the cadence at which a MODEL gets asked; without a model to ask, ending
# there cuts people off mid-thought, and the field's floor for pure-silence
# endpointing is 500-900 ms. butlerd now wires a real endpointer, so this
# branch is what a TurnPipeline built without one runs -- tests, and the
# daemon started with endpointer "none".
NO_ENDPOINTER_SILENCE_MS = 700

# Hard cap on how long we wait for a verdict AFTER the user stops -- measured
# against accumulated SILENCE, never against how long they have been talking.
# The spike had this right (static/app.js:198, `silenceMs >= EOU_MAX_WAIT_MS`)
# and the first port of it here got it wrong, capping elapsed turn time
# instead, which force-finalized every utterance longer than 6 s of speech
# mid-sentence. "Talk me through whether Butler should use a message bus"
# with two thinking pauses is already past that.
EOU_MAX_WAIT_MS = endpointing.SMART_MAX_WAIT_MS

NO_SPEECH_MS = 6000     # woke but never spoke -> empty final, re-arm (AC-6)

# Absolute ceiling on one turn, as a safety net rather than a policy. Nothing
# should ever reach it: it exists so the "a turn ALWAYS ends" invariant holds
# even if the energy gate latches voiced and the silence cap can never fire.
# Generous on purpose -- cutting someone off is a worse failure than a leaked
# claim, and the claim leak is what this is insuring against.
ABSOLUTE_MAX_TURN_MS = 120_000

# The endpointer is a CHILD PROCESS doing numpy and ONNX work under GPU
# contention, which makes it the single most likely thing in this loop to
# wedge. Awaiting it without a bound let it block the poll loop so the caps
# above were never re-evaluated -- the invariant was false. The bound lives
# with the policy that does the awaiting (endpointing.ENDPOINT_TIMEOUT_S).

FRAME_MS = 32           # granularity of the energy gate
BYTES_PER_MS = 32       # 16 kHz * 2 bytes / 1000
UTT_MAX_BYTES = 9 * 16000 * 2   # rolling 9 s for the smart-turn model


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(a * a)))


class TurnPipeline:
    """Drives one capture from capture_start to exactly one Final.

    `endpointer` is an optional async callable taking the utterance PCM and
    returning a probability that the speaker has finished. None means the
    energy gate plus the hard cap decide alone.

    The audio it is given is NOT the whole buffer. The buffer starts with the
    pre-roll, which holds the tail of the wake phrase, so the window is
    anchored at speech onset instead -- `onset_offset` below, and
    endpointing.PRE_SPEECH_LEAD_MS for why.
    """

    def __init__(self, turn: int, asr, on_partial, on_final,
                 asr_config: AsrConfig, endpointer=None, clock=time.monotonic,
                 poll_s: float = 0.05, policy=None, on_closing=None,
                 settings=None):
        # Tuned constants are DEFAULTS, not law: every one of them is an
        # opinion about a room, and the person in the room can see them all
        # on the settings page.
        def opt(key, fallback):
            return fallback if settings is None else settings.get(key, fallback)

        self.rms_on = opt("rms_on", RMS_ON)
        self.rms_off = opt("rms_off", RMS_OFF)
        self.min_speech_ms = opt("min_speech_ms", MIN_SPEECH_MS)
        self.no_speech_ms = opt("no_speech_ms", NO_SPEECH_MS)
        self.absolute_max_turn_ms = opt("absolute_max_turn_ms",
                                        ABSOLUTE_MAX_TURN_MS)
        self.turn = turn
        self.on_final = on_final
        self.clock = clock
        self.endpointer = endpointer
        self.poll_s = poll_s
        self.on_closing = on_closing
        # A policy decides WHEN a turn is over (endpointing.py); the caps
        # below are safety nets that apply whatever it decides, so a policy
        # bug cannot hold the microphone open (D-1).
        if policy is not None:
            self.policy = policy
        elif endpointer is not None:
            self.policy = endpointing.SmartTurnEnd(endpointer)
        else:
            self.policy = endpointing.SilenceEnd(
                silence_ms=NO_ENDPOINTER_SILENCE_MS)
        self._transcript = ""
        self._warned = False

        self._utt = bytearray()
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._voiced = False
        # Where speech actually started, so a model-based policy can anchor
        # its window there instead of at the start of the buffer. Absolute
        # byte positions in the whole turn's audio, converted to an offset
        # into the (rolling, front-trimmed) buffer by `onset_offset`.
        self._fed_bytes = 0          # everything ever fed, before trimming
        self._dropped_bytes = 0      # how much has scrolled off the front
        self._onset_abs: int | None = None
        # Bytes of back-fill captured BEFORE the wake word fired (hub._preroll,
        # AC-800). The wake phrase lives in there, so neither voicing nor the
        # onset may start inside it. 0 for a source with no pre-roll at all:
        # the native mic (Phase 5) and every test.
        self._lookback_bytes = 0
        # Seeded at the tuned constant, never at whatever arrives first.
        self._floor: float = self.rms_off
        self._started = clock()
        self._finalized = False
        self._closed = False
        # Why the turn ended, carried across to the Final. Set here as well as
        # in _finalize because a real engine can deliver its Final from a
        # worker thread without _finalize ever having run.
        self._reason = ""

        def _watch(partial):
            # **THE TRANSCRIPT THE POLICY READS IS THE TRANSCRIPT THE PAGE
            # DRAWS** (2026-09-15). This was `partial.committed` alone, and the
            # engine's split is not cosmetic: `tentative` is the VOLATILE
            # SUFFIX (`transcribe_cpp.StreamText`: "committed is append-only
            # and stable; tentative is the volatile suffix"`), so the LAST
            # words of an utterance -- which is exactly where an end phrase
            # lives -- can sit there for the whole turn. MEASURED on two of
            # the owner's own turns, ~34 s each: the committed text stopped
            # growing at '...resultado da sua pesquisa anterior.' while they
            # went on talking, and the phrase they said appears ONLY in the
            # turn's `final`, which is read off `committed` after `finalize()`
            # flushed `tentative` into it. The PAGE showed it (it draws both
            # halves) and the policy never did, so the turn ended only when
            # they pressed stop -- their words: "it captures it, but it never
            # sends it". One text for the screen and the decision.
            merged = " ".join(x for x in (partial.committed, partial.tentative)
                              if x).strip()
            self._transcript = merged or self._transcript
            # The TAIL, because `EndWordEnd` anchors its match to the end of
            # the transcript: "did the end phrase actually arrive" is a
            # question only the tail can answer, and it is the question a
            # turn that never closed is really asking. It is now the same
            # text the owner is looking at, which is what makes a trace line
            # and a screen agree about what was heard.
            trace.trace("partial", turn=self.turn,
                        chars=len(self._transcript),
                        tail=trace.tail(self._transcript))
            on_partial(partial)

        # Is this pipeline's streaming session still USING the engine? Not the
        # same question as "is the turn over", and that difference is the whole
        # reason this exists (AC-116). `asyncio.to_thread` cannot be cancelled:
        # cancelling the awaiting coroutine returns at once and the worker
        # thread runs to completion, so a turn dropped by barge-in keeps
        # decoding after the session has been reset to IDLE and this pipeline
        # has been closed. Nothing the Hub can observe about a SESSION is true
        # through that window; only a mark the worker thread itself clears is.
        self._asr_in_use = True
        self._finalizing = False
        self._session = asr.open(asr_config, _watch, self._handle_final)
        # The turn's first trace line, carrying the policy that will decide
        # when it ends. In `endword` that policy needs a PHRASE and this is
        # the only place the file says so, which is what makes a turn that
        # never closed readable afterwards. The PHRASES themselves are on the
        # line too since 2026-09-15: the first question about a turn that ran
        # to the ceiling is whether what the owner said was even configured,
        # and that answer lived in a settings file on another machine -- so
        # the line named the policy and not the words that policy was waiting
        # for. Empty for the timed policies, which wait for no word.
        phrases = "|".join(getattr(self.policy, "phrases", ()))
        trace.trace("turn_open", turn=self.turn,
                    policy=type(self.policy).__name__,
                    max_turn_ms=round(max(self.absolute_max_turn_ms,
                                          getattr(self.policy,
                                                  "max_turn_ms", 0))),
                    phrases=repr(phrases) if phrases else "")

    async def _maybe_warn(self, st) -> None:
        """Tell the satellite once when the turn is about to end.

        Being cut off is only unpleasant because it is a surprise. A warning
        the user can cancel by continuing to speak turns it into a prompt.
        Once per silence run: repeating it every poll would be a stutter.
        """
        if self.on_closing is None:
            return
        if not self.policy.closing_soon(st):
            self._warned = False
            return
        if self._warned:
            return
        self._warned = True
        await self.on_closing()

    # -- audio in ----------------------------------------------------------- #
    def note_lookback(self, n_bytes: int) -> None:
        """The first `n_bytes` of this turn's audio were captured BEFORE the
        wake word fired (AC-800): the satellite's lookback ring, holding the
        tail of the wake phrase (plan.md 4.2).

        THE FIRE POINT IS WHERE THE TURN STARTS. Everything after it -- the
        frames the satellite buffered while arbitration ran, then live audio
        -- is the owner's turn, even though it arrives in one burst. Two
        things read the boundary: the energy gate, which may only latch
        voicing on a frame after it (otherwise the wake phrase arms the
        silence clock and the quiet while the owner waits for the cue ends
        the turn -- the Mac mini's iPhone trace, 2026-10-29), and the onset
        anchor a model-based policy scores from (PRE_SPEECH_LEAD_MS). The
        recognizer is not one of them: it is fed every byte.
        """
        self._lookback_bytes = max(0, int(n_bytes))

    def onset_offset(self) -> int:
        """Byte offset into the CURRENT buffer where voicing began, or 0.

        0 also means "not found yet", and the two cases want the same
        treatment: score from the front of what we have.
        """
        if self._onset_abs is None:
            return 0
        return max(0, self._onset_abs - self._dropped_bytes)

    def feed(self, pcm: bytes) -> None:
        """Push captured audio. Safe to call after finalize; it is ignored."""
        if self._finalized or self._closed:
            return
        self._session.feed(pcm)
        chunk_start = self._fed_bytes
        self._fed_bytes += len(pcm)
        self._utt.extend(pcm)
        if len(self._utt) > UTT_MAX_BYTES:
            overflow = len(self._utt) - UTT_MAX_BYTES
            self._dropped_bytes += overflow
            del self._utt[:overflow]

        # Energy gate, frame by frame, so a long chunk cannot hide a pause.
        step = FRAME_MS * BYTES_PER_MS
        for i in range(0, len(pcm), step):
            frame = pcm[i:i + step]
            ms = len(frame) / BYTES_PER_MS
            level = rms(frame)
            off, on = self._thresholds(level)
            # The wake phrase sits before the fire point and is never this
            # turn's speech (AC-800): ONE test for both the voicing latch below
            # and the onset anchor, so the two cannot disagree.
            after_fire = chunk_start + i >= self._lookback_bytes
            if self._onset_abs is None and level >= on and after_fire:
                self._onset_abs = chunk_start + i
            # EDGES ONLY, never per frame: a line every 20 ms would bury the
            # decisions in the noise they are meant to explain. The count of
            # `speech_end` lines between `turn_open` and `turn_end` IS the
            # discarded-pause number -- the one an earlier prototype's trace made countable
            # ("17 discarded pauses; one turn in nine never closed").
            was_silent = self._silence_ms > 0
            if self._voiced:
                if level < off:
                    self._silence_ms += ms
                    if not was_silent:
                        trace.trace("speech_end", turn=self.turn,
                                    speech_ms=round(self._speech_ms),
                                    tail=trace.tail(self._transcript))
                else:
                    if was_silent:
                        trace.trace("speech_resume", turn=self.turn,
                                    after_silence_ms=round(self._silence_ms))
                    self._silence_ms = 0.0
                    self._speech_ms += ms
            elif level >= on and after_fire:
                self._voiced = True
                self._speech_ms += ms
                self._silence_ms = 0.0
                trace.trace("speech_start", turn=self.turn,
                            level=f"{level:.3f}")

    def _thresholds(self, level: float) -> tuple[float, float]:
        """Current (off, on) thresholds, lifted to sit above the room.

        Thresholds are computed BEFORE the floor is updated, and the floor is
        only updated from frames quiet enough to be plausible room noise.
        Both details are load-bearing: seeding the estimate from a speech
        frame lifted the gate above the speech that produced it, and the
        microphone went deaf.
        """
        lifted = self._floor * FLOOR_MARGIN
        off = max(self.rms_off, lifted)
        on = max(self.rms_on, lifted * (self.rms_on / self.rms_off))

        if level < on:
            # Climb slowly toward a noisier room, drop instantly to a quieter
            # one. Speech never moves the estimate.
            self._floor = min(level, self._floor * FLOOR_DECAY + 1e-6)
        return off, on

    # -- the decision loop -------------------------------------------------- #
    async def run(self, alive=lambda: True) -> None:
        """Poll until this turn ends. Returns only once a Final was emitted.

        Every exit path finalizes. That is not defensive coding, it is the
        D-1 invariant: an error that leaves the session in CAPTURING has
        switched the microphone off until a restart.
        """
        try:
            while not self._finalized:
                await asyncio.sleep(self.poll_s)
                elapsed_ms = (self.clock() - self._started) * 1000

                if not alive():
                    # AC-17: the capturing device vanished mid-utterance. The
                    # partial transcript is discarded, not acted upon.
                    await self._end_turn(discard=True, reason="source_lost")
                    break
                # A policy may raise the ceiling for itself. Long-form mode
                # exists so someone can stop, think and resume; holding it to
                # a ceiling sized for conversation makes the mode fail at the
                # one thing it is for. It may only ever RAISE it -- a policy
                # cannot shorten the safety net below the configured value.
                ceiling = max(self.absolute_max_turn_ms,
                              getattr(self.policy, "max_turn_ms", 0))
                if elapsed_ms >= ceiling:
                    await self._end_turn(reason="absolute_cap")
                    break
                # Below `min_speech_ms` is a cough, not a turn, so the
                # no-speech timer still runs (AC-801). It used to stop at the
                # first voiced frame, and a sliver of the wake word after the
                # fire point then held the recognizer to the absolute cap.
                if not self._voiced or self._speech_ms < self.min_speech_ms:
                    if elapsed_ms >= self.no_speech_ms:
                        await self._end_turn(reason="no_speech")
                        break
                    continue

                st = endpointing.TurnState(
                    silence_ms=self._silence_ms, speech_ms=self._speech_ms,
                    elapsed_ms=elapsed_ms, voiced=self._voiced,
                    transcript=self._transcript, utterance=bytes(self._utt),
                    onset_offset=self.onset_offset())

                await self._maybe_warn(st)
                reason = await self.policy.should_finalize(st)
                if reason:
                    await self._end_turn(reason=reason)
                    break
        except asyncio.CancelledError:
            # Barge-in cancels the turn. No Final: the turn was abandoned, not
            # ended, and a Final here would speak a reply nobody wants (D-6).
            self._closed = True
            self._session.reset_turn()
            raise
        except Exception as e:                      # noqa: BLE001
            # Loud, and still ends the turn. Swallowing this would leak the
            # capture claim and refuse the next wake as busy.
            print(f"[butler] turn {self.turn} pipeline failed: {e!r}")
            await self._end_turn(reason="error")
            raise

    # -- ending ------------------------------------------------------------- #
    async def finalize_now(self, reason: str = "client_hint") -> None:
        """The satellite said the user is done (capture_end_hint)."""
        await self._end_turn(reason=reason)

    async def _end_turn(self, *, discard: bool = False, reason: str = "") -> None:
        """End the turn, off the event loop.

        The real engine's finalize() BLOCKS until its worker thread has
        delivered the Final -- its own docstring says so and says to call it
        with asyncio.to_thread. Calling it inline froze the whole daemon for
        the duration, and the duration is bounded only by that adapter's 15 s
        watchdog. Butler is single-threaded asyncio, so that is not one
        satellite waiting: it is every satellite, the heartbeat, and the
        worker announcements, all stopped.

        The `_finalized` flag is set BEFORE the await so a concurrent caller
        (the poll loop and a capture_end_hint arriving together) returns
        immediately rather than finalizing twice.
        """
        if self._finalized:
            return                      # exactly one Final per turn, ever
        self._finalized = True
        self._reason = reason
        # **A TURN THAT ENDS WITH THE PHRASE UNSPENT SAYS SO** (2026-09-15).
        # Twice in one afternoon the owner ended a turn by hand -- the page's
        # stop button is the only thing that sends `capture_end_hint` --
        # because the endword had not fired, and the trace could not say why:
        # it showed the transcript but never whether the phrase was in it or
        # what the room was doing. Now their own tap is the diagnosis.
        if reason and reason != "end_word":
            heard = [p for p in getattr(self.policy, "phrases", ())
                     if p in (self._transcript or "").lower()]
            if heard:
                print(f"[butler] turn {self.turn}: the end phrase {heard[0]!r} "
                      f"was heard and the turn ended by {reason!r} instead -- "
                      f"it was not the last words, or the room was not quiet "
                      f"for {getattr(self.policy, 'pause_ms', 0):.0f} ms after "
                      f"it", flush=True)
        # `reason` is the whole diagnosis of a turn: `end_word` versus
        # `absolute_cap` is the difference between Butler hearing the phrase
        # and Butler waiting out its ceiling, and nothing else in the file
        # distinguishes them.
        trace.trace("turn_end", turn=self.turn, reason=reason or "none",
                    discard=discard,
                    elapsed_ms=round((self.clock() - self._started) * 1000),
                    silence_ms=round(self._silence_ms),
                    tail=trace.tail(self._transcript))
        if discard:
            self._session.reset_turn()
            self._asr_in_use = False      # nothing was ever handed to a thread
            self.on_final(Final(text=""), reason)
            return
        self._finalizing = True
        await asyncio.to_thread(self._finalize_blocking)

    def _finalize_blocking(self) -> None:
        """`finalize`, on a worker thread, clearing the mark THERE (AC-116).

        The mark cannot be cleared in the coroutine's own `finally`: that runs
        the moment the await is cancelled, while this thread carries on
        decoding for as long as the adapter's finalize budget allows. A caller
        asking "is the recognizer free" in that window would be told yes and
        would open a second `Session` on the same `Model`, which returns EMPTY
        transcripts on BOTH sides, silently, 9 times out of 9.
        """
        try:
            self._session.finalize()
        finally:
            self._finalizing = False
            self._asr_in_use = False

    def uses_recognizer(self) -> bool:
        """True while this pipeline's session may still be using the engine.

        Read by `Hub._recognizer_in_use` to decide whether a whole-clip decode
        may start. True from construction -- `asr.open` allocates the session
        before a single byte is fed -- until either the finalize thread
        returns, a discarded turn resets the session, or `close` stops it.
        """
        return self._asr_in_use

    def _handle_final(self, final: Final) -> None:
        self._finalized = True
        # The end phrase was addressed to Butler, not part of the
        # instruction: "...and run the tests. send it." must not reach the
        # brain with the "send it" still on the end.
        stripped = self.policy.strip(final.text or "")
        if stripped != (final.text or ""):
            # `replace` rather than a fresh Final: rebuilding one field by
            # field silently drops whatever field the port grew last, and the
            # newest one -- `failed` (D-26) -- is the difference between the
            # daemon speaking about a lost utterance and saying nothing.
            final = dataclasses.replace(final, text=stripped)
        self.on_final(final, self._reason)

    def close(self) -> None:
        self._closed = True
        # Only the finalize THREAD may clear the mark once it holds it.
        # `close` is reached from `abandon()`, which arrives precisely when a
        # finalize is still running and nothing can stop it (AC-116); clearing
        # here would hand a caller the engine mid-decode. When no finalize was
        # ever started, stopping the session is the end of its use of the
        # engine and this is the only place that can say so.
        if not self._finalizing:
            self._asr_in_use = False
        self._session.close()
