"""How a turn decides it is over. plan.md 4.2.

Three policies, chosen by a server-side setting, because "when should it
stop listening" is not one question with one right answer:

  smart    -- a model decides. Trained to tell "trailing off with an um,
              still thinking" from "finished a sentence", which is the
              actual fix for being cut off mid-thought. A longer timer is
              the crude version of the same goal and makes every turn slow,
              including the quick ones.
  patient  -- plain silence, with presets described by feel rather than by
              milliseconds. The fallback for when the model is wrong or
              switched off (butlerd's endpointer "none").
  endword  -- no timer at all. Talk, pause, think, resume; nothing is
              submitted until you say the word. This is the mode for
              composing a real instruction for a coding agent, which is the
              job Butler actually exists to do.

The policies do NOT own the safety nets. The no-speech timeout and the
absolute ceiling stay in pipeline.py and apply to every mode, so a policy
bug cannot hold the microphone open forever (D-1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Patience presets, named for how they feel rather than what they measure.
# Borrowed from the owner's previous project, where framing them as Quick /
# Balanced / Patient rather than as numbers was the thing that made them
# usable. (silence_ms, max_wait_ms).
PATIENCE = {
    "quick": (400, 4000),
    "balanced": (600, 8000),
    "patient": (800, 15000),
}
DEFAULT_PATIENCE = "balanced"

# Deliberately awkward phrases. A natural one ("done", "okay") submits your
# half-finished thought the first time you say it in passing, and the whole
# point of this mode is that nothing submits until you mean it.
DEFAULT_END_PHRASES = ("over to you", "send it", "go ahead", "over")

#: Spellings a recogniser has been OBSERVED to produce for a configured end
#: phrase, keyed by that phrase. This is not fuzziness and not edit distance:
#: in this mode a false submit costs a half-finished instruction, so every
#: entry is a string that was really committed to a real transcript, and one
#: that was really spoken by the owner.
#:
#: Seeded 2026-09-15 from the laptop's own `state/voice-trace.log`. Decoding
#: Portuguese, the recogniser spelled the English end phrase phonetically, and
#: FIVE long-form turns on that machine have no `turn_end` at all -- they ran
#: to the five-minute ceiling because a literal match on `send it` could never
#: fire. The same speaker got the literal spelling on other turns, which is
#: what made a deterministic bug look intermittent.
#:
#: Add an entry only from a trace line. A guessed spelling is a phrase the
#: owner cannot say and cannot debug.
END_PHRASE_ALIASES = {
    "send it": ("sandit", "sand it", "sanded", "sended"),
}

# A pause after the end phrase before submitting. Without it, "send it back
# to the previous version" would submit on the first two words.
END_WORD_PAUSE_MS = 600

# Nothing forces the end-word mode to finish, by design -- but a turn still
# has to end eventually, so it gets its own generous ceiling rather than
# borrowing the conversational one.
END_WORD_MAX_MS = 300_000


# --------------------------------------------------------------------------- #
# Smart mode constants. Every one of these is a number somebody chose, so
# every one of them says who chose it and against what.
# --------------------------------------------------------------------------- #
# 16 kHz mono int16 (ports.SAMPLE_RATE_IN). Used to turn a millisecond lead
# into a byte offset.
BYTES_PER_MS = 32

# The window the model expects. smart-turn v3 is a Whisper Tiny encoder over
# an 8 s window: it reads at most the LAST 8 s and zero-pads at the FRONT
# ("insert padding at the beginning ... such that the audio data is at the end
# of the input vector" -- smart-turn README). The number lives in the engine
# as turn_engine.CHUNK_SECONDS; it is repeated here because it is what makes
# the anchoring below meaningful, and pipeline.py deliberately keeps 9 s of
# audio so the model always has a full window to trim.
SMART_WINDOW_MS = 8000

# Where the scored window STARTS: speech onset minus this.
#
# Upstream builds the segment as [first speech onset - pre_speech_ms, now]
# (base_smart_turn.py `_process_speech_segment`), with pre_speech_ms defaulting
# to 500 since pipecat 0.0.99 / PR #3377 -- the same release whose notes credit
# short-utterance accuracy to getting this lead-in right.
#
# It is load-bearing rather than cosmetic. smart-turn issue #12 measured
# IDENTICAL speech swinging from p=0.012 to over 0.99 purely on how much audio
# preceded it. Butler's pre-roll (plan.md 4.2) starts the utterance buffer
# BEFORE the wake word fired, so without anchoring the model scores
# "[hey jarvis] [gap] [command]" -- a completed phrase sitting in front of the
# command, biasing it toward COMPLETE, which is the failure direction that cuts
# the user off mid-thought.
PRE_SPEECH_LEAD_MS = 500

# First check after this much silence. The spike's EOU_SILENCE_MS
# [MEASURED-SPIKE, plan.md Appendix A]; upstream triggers its single inference
# at VADParams.stop_secs = 200 ms, so Butler asks slightly later.
SMART_FIRST_CHECK_MS = 280

# Re-ask this often while the silence continues. The spike's EOU_RECHECK_MS.
# Upstream has NO recheck -- one inference per VAD-stop and then a 3 s timer --
# and smart-turn issue #32 reports the score still sitting near 0.4 at the
# first check and only becoming confident after ~1000-1200 ms of silence. The
# recheck loop is what makes an early first check safe, so it stays.
SMART_RECHECK_MS = 450

# Finalize at or above this probability. The ONNX output is already a sigmoid.
#
# DEVIATION, FLAGGED FOR THE OWNER, NOT CHANGED HERE: upstream hardcodes 0.5 in
# two independent places (smart-turn inference.py:58 and pipecat
# local_smart_turn_v3.py:174) and its published accuracy figures are the
# figures AT 0.5. smart-turn issue #41 benchmarked Incomplete precision at only
# 52-58% on two production datasets, i.e. about half of the model's
# "keep waiting" verdicts are already wrong -- so the model over-waits, and
# 0.6 makes an already-hesitant model more hesitant. That is an argument for
# lowering this to 0.5, not for keeping 0.6. It needs a measurement on Butler's
# own audio before it moves (plan.md section 9 item 16); changing it blind
# would just swap one uncalibrated number for another.
SMART_THRESHOLD = 0.6

# How long we keep asking after the user stops, measured against accumulated
# SILENCE and never against elapsed turn time. Then silence wins (plan.md 4.2).
SMART_MAX_WAIT_MS = 6000

# One inference is a child-process round trip doing numpy and ONNX work under
# GPU contention -- the likeliest thing in the loop to wedge. Bounded so a
# wedged child degrades to "no verdict" instead of holding the microphone
# open (D-1). Separate from the endpointer's own model-load budget (D-5).
ENDPOINT_TIMEOUT_S = 2.0


@dataclass
class TurnState:
    """What a policy is allowed to look at."""
    silence_ms: float
    speech_ms: float
    elapsed_ms: float
    voiced: bool
    transcript: str        # committed text so far, lowercased by the caller
    utterance: bytes       # rolling PCM, for a model-based policy
    onset_offset: int = 0
    """Byte offset into `utterance` where voicing actually began.

    0 means "the buffer already starts at speech", which is what every
    non-wake caller sees. It is not the same as "the buffer starts at the
    turn": the wake path prepends a pre-roll containing the tail of the wake
    phrase, and a model-based policy must not score that (PRE_SPEECH_LEAD_MS).
    Defaulted so that a caller which cannot compute it degrades to today's
    behaviour rather than to a crash.
    """


@runtime_checkable
class EndPolicy(Protocol):
    name: str

    async def should_finalize(self, st: TurnState) -> str | None:
        """Return a reason string to end the turn, or None to keep listening."""
        ...

    def closing_soon(self, st: TurnState) -> bool:
        """True when the turn is about to end and the user deserves warning.

        Drives the satellite's 'closing' tick. Being cut off is only
        unpleasant because it is a surprise; a warning you can cancel by
        continuing to speak turns it into a prompt.
        """
        ...

    def strip(self, text: str) -> str:
        """Remove anything that was control, not content."""
        ...


class SilenceEnd:
    """Plain silence. The fallback, and what runs with no model wired."""

    name = "patient"

    def __init__(self, patience: str = DEFAULT_PATIENCE,
                 silence_ms: float | None = None,
                 max_wait_ms: float | None = None):
        if patience not in PATIENCE:
            raise ValueError(
                f"unknown patience {patience!r}; known: {sorted(PATIENCE)}")
        self.patience = patience
        preset_silence, preset_max = PATIENCE[patience]
        # Explicit values win, for the one caller that has a number rather
        # than a feeling: the no-endpointer fallback, whose 700 ms floor was
        # picked against the field's 500-900 ms range.
        self.silence_ms = preset_silence if silence_ms is None else silence_ms
        self.max_wait_ms = preset_max if max_wait_ms is None else max_wait_ms

    async def should_finalize(self, st: TurnState) -> str | None:
        if not st.voiced:
            return None
        if st.silence_ms >= self.silence_ms:
            return "silence"
        return None

    def closing_soon(self, st: TurnState) -> bool:
        # Warn a third of the way through the pause, so there is time to
        # react before it submits.
        return st.voiced and st.silence_ms >= self.silence_ms * 0.66

    def strip(self, text: str) -> str:
        return text


class SmartTurnEnd:
    """A model decides, with silence only as the cue to ask it.

    `endpointer` is an async callable taking the utterance PCM and returning
    a probability that the speaker has finished. It MUST do its work off the
    event loop -- see adapters/endpointer_smartturn.py, which is the one
    Butler ships.
    """

    name = "smart"

    def __init__(self, endpointer, threshold: float = SMART_THRESHOLD,
                 first_check_ms: float = SMART_FIRST_CHECK_MS,
                 recheck_ms: float = SMART_RECHECK_MS,
                 max_wait_ms: float = SMART_MAX_WAIT_MS,
                 timeout_s: float = ENDPOINT_TIMEOUT_S,
                 pre_speech_lead_ms: float = PRE_SPEECH_LEAD_MS,
                 clock=None):
        self.timeout_s = timeout_s
        import time as _time
        self.endpointer = endpointer
        self.threshold = threshold
        self.first_check_ms = first_check_ms
        self.recheck_ms = recheck_ms
        self.max_wait_ms = max_wait_ms
        self.pre_speech_lead_ms = pre_speech_lead_ms
        self.clock = clock or _time.monotonic
        self._last_check = 0.0
        self._last_p = 0.0

    def window(self, st: TurnState) -> bytes:
        """The slice of the utterance the model should actually score.

        Anchored at speech onset minus PRE_SPEECH_LEAD_MS, never at the start
        of the buffer, because the buffer starts before the wake word. The
        lead is kept rather than trimmed to the onset exactly: the energy gate
        fires on the first frame loud enough to count, which is already a few
        tens of milliseconds into the first phoneme, and upstream's window
        includes half a second of run-up.

        Clamped at the front of the buffer, so a caller that reports no onset
        (offset 0) gets exactly what it would have got before this existed.
        """
        start = max(0, int(st.onset_offset) -
                    int(self.pre_speech_lead_ms * BYTES_PER_MS))
        # int16 samples are 2 bytes: an odd offset would shift every sample by
        # one byte and hand the model white noise that still looks like audio.
        start -= start % 2
        return st.utterance[start:]

    async def should_finalize(self, st: TurnState) -> str | None:
        if not st.voiced:
            return None
        # Fail open: the model gets a bounded window to be right, then
        # silence wins. Waiting on a wedged child forever is the failure
        # this cap exists to prevent.
        if st.silence_ms >= self.max_wait_ms:
            return "hard_cap"
        if st.silence_ms < self.first_check_ms:
            return None

        now = self.clock()
        if self._last_check and (now - self._last_check) * 1000 < self.recheck_ms:
            return None
        self._last_check = now
        try:
            import asyncio
            self._last_p = await asyncio.wait_for(
                self.endpointer(self.window(st)), timeout=self.timeout_s)
        except Exception as e:                        # noqa: BLE001
            # The endpointer is a child process doing numpy and ONNX work
            # under GPU contention -- the likeliest thing here to wedge.
            # Degrade to "no verdict" and let the cap end the turn; hanging
            # on it would hold the microphone open (D-1).
            print(f"[butler] endpointer unavailable ({e!r}); "
                  f"falling back to the silence cap")
            self._last_p = 0.0
            return None
        return "endpoint" if self._last_p >= self.threshold else None

    def closing_soon(self, st: TurnState) -> bool:
        # The model has leaned toward "done" without crossing the line yet.
        return st.voiced and self._last_p >= self.threshold * 0.75

    def strip(self, text: str) -> str:
        return text


class EndWordEnd:
    """Nothing submits until you say the word.

    No silence timer at all: pause as long as you like, think, and resume.
    The phrase is control rather than content, so it is stripped before the
    text goes anywhere.

    Two rules, and both were earned by a turn that never closed. The phrase
    counts at the END of what has been said so far, never in the middle. And
    it counts in its MISHEARD spellings as well as its literal one, because
    the transcript is whatever a recogniser wrote, not what was said -- see
    `END_PHRASE_ALIASES`.
    """

    name = "endword"

    #: How long one long-form turn may run before the safety net ends it.
    #: The pipeline's own ceiling is sized for a conversation; this mode
    #: exists precisely so someone can stop, think, and resume, so it needs
    #: its own and a much larger one. Read by the pipeline via
    #: `policy.max_turn_ms` -- for a while this value existed and nothing
    #: read it, which cut long-form turns at the conversational ceiling and
    #: made the mode quietly fail at the one thing it is for.
    max_turn_ms = END_WORD_MAX_MS

    def __init__(self, phrases=DEFAULT_END_PHRASES,
                 pause_ms: float = END_WORD_PAUSE_MS):
        # Longest first, so "over to you" wins over "over".
        self.phrases = tuple(sorted((p.strip().lower() for p in phrases),
                                    key=len, reverse=True))
        # Every spelling this phrase may arrive in, longest first for the same
        # reason as `self.phrases` above.
        renderings = sorted((r for p in self.phrases
                             for r in (p,) + END_PHRASE_ALIASES.get(p, ())),
                            key=len, reverse=True)
        self.pause_ms = pause_ms
        # IGNORECASE is load-bearing and was missing until 2026-08-20. The
        # phrases are lowercased above and `_heard_end_phrase` lowercases the
        # transcript, so DETECTION worked -- but `strip` runs on the original
        # text, where a recogniser has capitalised after sentence punctuation.
        # The turn therefore ended correctly and kept its control phrase, and
        # "Can you hear me now? Send it" reached the brain, and in harness mode
        # a full-capability coding agent, with the "Send it" still on it. Found
        # in `state/chats/`; the tests above it all used lowercase input, which
        # is the one case a real transcript almost never is.
        #
        # The trailing class is anything that is NOT a word character, rather
        # than the `.!?` it used to be (2026-09-15). A recogniser punctuates
        # with whatever fits the clause it is still building, so a phrase at
        # the end arrives as `pode ir,` or `pode ir:` as readily as `pode ir.`
        # -- and every one of those made the end phrase unmatchable, which is a
        # turn that runs to the ceiling. Written as a negation so it cannot be
        # defeated by a character nobody thought of: the ellipsis and the
        # curly quotes are not in any list anyone remembers to extend.
        self._pattern = re.compile(
            r"(?:" + "|".join(re.escape(r) for r in renderings) +
            r")[^\w]*$", re.IGNORECASE)

    def _heard_end_phrase(self, transcript: str) -> bool:
        return bool(self._pattern.search(transcript.strip().lower()))

    async def should_finalize(self, st: TurnState) -> str | None:
        if not self._heard_end_phrase(st.transcript):
            return None
        # The pause is what separates the command from the same words used
        # in passing: "send it back to the old version" keeps going, "send
        # it." followed by quiet does not.
        if st.silence_ms < self.pause_ms:
            return None
        return "end_word"

    def closing_soon(self, st: TurnState) -> bool:
        return self._heard_end_phrase(st.transcript)

    def strip(self, text: str) -> str:
        """Take the end phrase off the transcript.

        It was addressed to Butler, not part of the instruction, and leaving
        it in means the brain reads "...and run the tests over" -- which at
        best wastes tokens and at worst changes the meaning.
        """
        return self._pattern.sub("", text.strip()).strip().rstrip(",;:") or ""


def build(mode: str, *, endpointer=None, patience: str = DEFAULT_PATIENCE,
          settings=None) -> EndPolicy:
    """Make the policy named by the server-side setting.

    Falls back from smart to patient ONLY when no endpointer exists, and
    says so out loud: silently running a different policy than the one the
    setting names is how a user concludes the setting does nothing.
    """
    def opt(key, fallback):
        # Settings are optional so every existing caller and test keeps
        # working with the tuned defaults.
        return fallback if settings is None else settings.get(key, fallback)

    if mode == "endword":
        return EndWordEnd(phrases=opt("end_phrases", DEFAULT_END_PHRASES))
    if mode == "smart":
        if endpointer is None:
            print("[butler] turn mode is 'smart' but no endpointer is wired; "
                  "falling back to patient silence")
            return SilenceEnd(
                patience,
                silence_ms=opt("no_endpointer_silence_ms", None))
        return SmartTurnEnd(
            endpointer,
            threshold=opt("smart_threshold", 0.6),
            first_check_ms=opt("eou_first_check_ms", 280),
            recheck_ms=opt("eou_recheck_ms", 450),
            max_wait_ms=opt("eou_max_wait_ms", 6000))
    if mode == "patient":
        return SilenceEnd(patience)
    raise ValueError(f"unknown turn mode {mode!r}")
