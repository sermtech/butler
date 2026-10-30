"""What only transcribe.cpp can be asked. See test/AGENTS.md.

The conformance suite proves every Asr adapter honours the port. It cannot
prove that an option belonging to ONE engine actually reaches that engine,
because the port no longer carries such options at all (AC-41): `backend` and
`latency` are constructor kwargs on this adapter, named in
`config/models.json` under `asr_engine`.

That gap is the D-18 shape. Conformance builds the adapter with defaults, so
a regression that dropped constructor kwargs on the floor would leave every
conformance case green -- the engine would simply fall back to its own
defaults, which are the same values, and nothing would look wrong until
someone set `backend` to something deliberate and it silently did nothing.

The finalize-budget cases below (D-26) are the same shape from the other
side: the budget is this adapter's own arithmetic, no other Asr has one, and
the property is about how long it WAITS -- which a suite that only asserts
"exactly one Final arrived" cannot see. They run in the fast tier against a
scripted stand-in for `stt_engine`, because the rule under test is the
budget, not the recognizer.
"""
import json
import re
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import ports  # noqa: E402
from daemon.adapters import asr_transcribecpp as tcpp  # noqa: E402


@pytest.mark.slow
@pytest.mark.needs_gpu
@pytest.mark.asyncio
async def test_a_constructor_backend_reaches_the_engine():
    """AC-41's other half, asserted on the ENGINE rather than the object.

    Built with an explicit non-default backend, the loaded model must report
    running on it. Asserting `adapter._backend == "cpu"` would only prove the
    constructor stored a string; what matters is that the string survived the
    trip into `StreamingSession` and changed what was loaded.

    Both backends are built and COMPARED rather than asserting "cpu" alone.
    Asserting one value bakes in an assumption about this machine -- that
    `auto` resolves to Vulkan, true on the 1080 Ti -- and on a host with no
    Vulkan device `auto` would report CPU too, so a dropped kwarg would
    coincidentally satisfy the assertion. Comparing makes the test carry its
    own premise: if the two agree, the environment cannot tell a working
    kwarg from a broken one, and it says so instead of passing.
    """
    asked = ports.build(ports.ASRS, "transcribecpp", backend="cpu")
    await asked.warm()
    default = ports.build(ports.ASRS, "transcribecpp")
    await default.warm()

    for name, adapter in (("cpu", asked), ("default", default)):
        assert adapter.info.get("warmed") is True, (
            f"{name}: warm() did not populate info, so this would be "
            f"asserting on the unwarmed placeholder: {adapter.info}")

    asked_backend = str(asked.info.get("backend", ""))
    default_backend = str(default.info.get("backend", ""))
    assert asked_backend.lower() != default_backend.lower(), (
        f"this host resolves the default backend to {default_backend!r}, the "
        f"same as an explicit 'cpu', so nothing here can distinguish a "
        f"constructor kwarg that reached the engine from one that was "
        f"dropped. The test is inconclusive on this machine rather than "
        f"passing -- run it where the default is a GPU backend.")
    assert "cpu" in asked_backend.lower(), (
        f"the adapter was constructed with backend='cpu' and the engine "
        f"reports {asked_backend!r}. The constructor kwarg did not reach the "
        f"engine -- invisible whenever default and requested agree (AC-41).")


# --------------------------------------------------------------------------- #
# the whole-clip decode (docs/ambient.md section 1)
# --------------------------------------------------------------------------- #
ASSETS = Path(__file__).resolve().parent / "assets"
MANIFEST = {f["filename"]: f
            for f in json.loads((ASSETS / "manifest.json").read_text(
                encoding="utf-8"))["fixtures"]}

# The four spoken fixtures end to end: 12.61 s of real speech, the same pool
# `test/manual/spike_ambient_offline.py` tiles to produce its table.
POOL = ("long_utterance.wav", "two_sentences.wav", "wake_then_command.wav",
        "short_question.wav")


def _wav(name: str) -> bytes:
    """The fixture's raw 16 kHz mono int16 frames -- what the port takes."""
    path = ASSETS / name
    if not path.exists():
        pytest.skip(f"needs {name}; run scripts/make-fixtures")
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def _norm(text: str) -> str:
    """Lower case, punctuation to spaces, whitespace collapsed.

    test/AGENTS.md: the recorded fixtures differ from ground truth in casing,
    punctuation and a couple of function words, so nothing here asserts on a
    raw string.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_recorded_clip_comes_back_as_the_words_that_were_spoken():
    """The only place a REAL transcript can be asserted on (test/AGENTS.md).

    Conformance can prove every adapter returns a non-empty string for
    speech; the fake satisfies that by reciting a script. That the transcribe
    path actually decodes THIS audio into THESE words is a claim only the
    real engine can be asked, and it is the claim the two instrument traps in
    `spike_ambient_offline.py` defeated: `Session.run()` answers text='' for
    the int16 the rest of the audio path carries, and `Result.__repr__` is
    ~214 characters of timings whatever was decoded, so a size or a repr
    length reads exactly like a working decoder while the transcript is
    empty.

    backend='cpu' rather than the default: the measurement this exists for
    was made on the CPU backend (the 1080 Ti is contended with llama-swap,
    which is WHY ambient decodes on the CPU), so this reproduces it rather
    than measuring whichever device `auto` picked today.
    """
    asr = ports.build(ports.ASRS, "transcribecpp", backend="cpu")
    text = await asr.transcribe(_wav("long_utterance.wav"),
                                ports.AsrConfig(model="test"))

    got = _norm(text).split()
    truth = _norm(MANIFEST["long_utterance.wav"]["transcript"]).split()
    missing = [w for w in truth if w not in got]
    assert not missing, (
        f"the recognizer dropped {missing} from a 5.19 s recorded clip.\n"
        f"  wanted (ground truth): {' '.join(truth)}\n"
        f"  got:                   {' '.join(got)}\n"
        f"An EMPTY got here is the int16/float32 trap, not a quiet fixture.")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_thirty_seconds_decodes_whole_rather_than_truncating():
    """AC-71's target length, asserted end to end -- the point of the port.

    Thirty seconds is the size the owner asked for, and the failure this
    guards is SILENT truncation: a decoder that stops early returns a
    plausible transcript of the beginning, and nothing about the string says
    the rest of the clip was thrown away.

    So the clip is the 12.61 s fixture pool tiled and cut at exactly 30 s,
    and the assertion counts a phrase that occurs ONCE per repeat. Two means
    the third repeat -- which begins at 25.2 s -- was dropped.

    [MEASURED 2026-08-19, cpu backend, parakeet-unified-en-0.6b]: 5.19 s ->
    94 chars in 0.3 s, 30 s -> 507 chars in 1.53 s. No ceiling was found:
    60 s -> 980 chars, 120 s -> 1965, 240 s -> 3922, all growing linearly.
    `Session.limits.effective_max_audio_ms` is 0 for this model, which the
    binding documents as unbounded, and its truncation failure mode is an
    `OutputTruncated` EXCEPTION carrying the partial transcript rather than a
    short string -- so a clip too long to decode is loud.
    """
    cfg = ports.AsrConfig(model="test")
    asr = ports.build(ports.ASRS, "transcribecpp", backend="cpu")
    pool = b"".join(_wav(name) for name in POOL)
    clip = (pool * 3)[:30 * ports.SAMPLE_RATE_IN * 2]
    assert len(clip) == 30 * ports.SAMPLE_RATE_IN * 2

    long_text = _norm(await asr.transcribe(clip, cfg))
    short_text = _norm(await asr.transcribe(_wav("long_utterance.wav"), cfg))

    assert long_text.count("message bus") == 3, (
        f"the pool repeats three times inside 30 s and the phrase came back "
        f"{long_text.count('message bus')} times -- fewer means the decode "
        f"stopped early and said nothing about it:\n{long_text}")
    assert len(long_text) > 4 * len(short_text), (
        f"30 s of audio produced {len(long_text)} characters against "
        f"{len(short_text)} for 5.19 s; the transcript is not growing with "
        f"the clip, which is truncation wearing a plausible face")


# --------------------------------------------------------------------------- #
# the finalize budget (D-26)
# --------------------------------------------------------------------------- #
BYTES_PER_S = ports.SAMPLE_RATE_IN * 2
FLOOR_S = 0.15          # a whole test tier's worth of 15 s waits is not a suite


class ScriptedEngine:
    """A stand-in for `stt_engine.StreamingSession`, timed rather than real.

    It reproduces the only two things the budget cares about: `finalize()`
    answers LATER, from another thread, and how much later is scripted. The
    real engine is a 600 MB GGUF whose decode time is the very quantity under
    test, so measuring the budget against it would measure the machine.

    `delay_s` is read at each `finalize()`, so one session can answer quickly
    for one utterance and never for the next.
    """

    def __init__(self, emit, delay_s: float, text: str = "the words",
                 **_kwargs):
        self._emit = emit
        self.delay_s = delay_s
        self.text = text
        self.info = {"arch": "scripted", "backend": "none"}
        self._timers: list[threading.Timer] = []
        self.fed_bytes = 0

    def feed(self, pcm: bytes) -> None:
        self.fed_bytes += len(pcm)

    def finalize(self) -> None:
        t = threading.Timer(self.delay_s, self._final)
        t.daemon = True
        self._timers.append(t)
        t.start()

    def _final(self) -> None:
        self._emit("final", {"text": self.text, "finalize_ms": 1})

    def reset_turn(self) -> None:
        self.fed_bytes = 0

    def close(self) -> None:
        for t in self._timers:
            t.cancel()


def scripted_session(monkeypatch, delay_s: float, **kwargs):
    """A real `TranscribeCppSession` over a scripted engine, and its Finals."""
    import types

    engines = {}

    def build(emit, **engine_kwargs):
        engines["e"] = ScriptedEngine(emit, delay_s, **engine_kwargs)
        return engines["e"]

    monkeypatch.setattr(
        tcpp, "_stt_engine",
        lambda: types.SimpleNamespace(StreamingSession=build))
    finals = []
    sess = tcpp.TranscribeCppSession(lambda _p: None, finals.append,
                                     FLOOR_S, **kwargs)
    return sess, finals, engines["e"]


def test_the_budget_scales_with_the_audio_and_is_bounded():
    """The derivation at the constant, asserted rather than trusted (D-26).

    Three claims, and each one is a separate way the old fixed 15 s failed:
    a short turn is unchanged (the floor), a long turn gets more (the whole
    defect), and no turn gets an unbounded wait (D-1 -- a finalize that never
    returns is a microphone that never comes back).
    """
    floor = tcpp.FINALIZE_FLOOR_S
    assert tcpp.finalize_budget_s(1.0, floor) == floor
    assert tcpp.finalize_budget_s(5.0, floor) == floor

    twenty = tcpp.finalize_budget_s(20.0, floor)
    assert twenty > 13.64, (
        f"20 s of audio measured 13.64 s of decode on this machine and the "
        f"budget for it is {twenty} s -- the measurement the constant is "
        f"derived from already blows it")
    assert twenty > tcpp.finalize_budget_s(10.0, floor) > floor, (
        "the budget does not grow with the audio, which is the defect")

    assert tcpp.finalize_budget_s(10_000.0, floor) == tcpp.FINALIZE_MAX_S, (
        "the budget is unbounded above; a wedged engine holds the turn, the "
        "claim and the microphone forever (D-1)")


def test_a_long_utterance_is_given_time_to_decode(monkeypatch):
    """D-26 itself: 20 s of audio must not be judged by a 15 s stopwatch.

    The engine answers after the floor has expired and well inside a budget
    scaled to the audio. With a fixed budget this Final is the empty one,
    which is exactly the shipped defect: a turn that completed normally
    having heard nothing.
    """
    sess, finals, engine = scripted_session(monkeypatch, delay_s=FLOOR_S * 3)
    try:
        sess.feed(b"\x00\x20" * (10 * BYTES_PER_S))     # 20 s of audio
        sess.finalize()
    finally:
        sess.close()

    assert len(finals) == 1
    assert finals[0].text == "the words", (
        f"a 20 s utterance was cut off at a fixed budget and ended empty: "
        f"{finals[0]!r}")
    assert finals[0].failed is False


def test_a_timeout_is_a_failure_and_not_a_silent_turn(monkeypatch):
    """The two facts must be distinguishable (D-26, D-7).

    An empty Final is a legal turn end and means "you said nothing". A
    finalize that ran out of budget means "I could not finish listening", and
    until this flag existed the owner could not tell them apart -- the daemon
    could not either, so it said nothing at all.
    """
    sess, finals, engine = scripted_session(monkeypatch, delay_s=30.0)
    started = time.perf_counter()
    try:
        sess.feed(b"\x00\x20" * (BYTES_PER_S // 20))    # 50 ms of audio
        sess.finalize()
    finally:
        sess.close()
    elapsed = time.perf_counter() - started

    assert len(finals) == 1, "the turn did not end; the claim never releases"
    assert finals[0].text == ""
    assert finals[0].failed is True, (
        f"the timeout produced a Final indistinguishable from silence: "
        f"{finals[0]!r}")
    assert elapsed < 5.0, (
        f"the floor did not bound the wait: {elapsed:.1f} s")


def test_an_engine_error_is_a_failure_too(monkeypatch):
    """The other synthesised empty Final. Same fact, same flag.

    The engine tore the turn down and will never send a Final, so the adapter
    ends the turn itself (D-7). That is not silence either.
    """
    sess, finals, engine = scripted_session(monkeypatch, delay_s=30.0)
    started = time.perf_counter()
    try:
        sess.feed(b"\x00\x20" * BYTES_PER_S)            # 1 s of audio
        # Finalize on another thread, because it blocks until a Final is
        # delivered -- and the error is what delivers this one.
        t = threading.Thread(target=sess.finalize, daemon=True)
        t.start()
        time.sleep(0.05)
        engine._emit("error", {"message": "engine exploded"})
        t.join(timeout=10.0)
    finally:
        sess.close()
    elapsed = time.perf_counter() - started

    assert len(finals) == 1
    assert finals[0].text == ""
    assert finals[0].failed is True, (
        f"an engine error ended the turn as if the room had been quiet: "
        f"{finals[0]!r}")
    assert elapsed < 1.0, (
        f"the Final took {elapsed:.2f} s, which is the budget expiring rather "
        f"than the error path delivering it -- this test would then be a "
        f"second copy of the timeout one (D-13)")


@pytest.mark.parametrize("abandon", [False, True],
                         ids=["next_utterance", "reset_turn"])
def test_the_audio_count_is_per_utterance(monkeypatch, abandon):
    """The budget is for THIS utterance, and both ways one ends must clear it.

    A counter that accumulated across a session would hand the second
    utterance the first one's budget -- which is the same defect wearing the
    opposite sign: a wedged engine on a two-word question would be waited out
    for as long as the longest thing said all conversation.

    `feed` after a delivered Final starts the next utterance, and
    `reset_turn` abandons one (barge-in). Both are exercised here because
    they are two different lines in `_start_turn_locked`'s callers.
    """
    sess, finals, engine = scripted_session(monkeypatch, delay_s=0.01)
    try:
        sess.feed(b"\x00\x20" * (10 * BYTES_PER_S))     # 20 s of audio
        if abandon:
            sess.reset_turn()
        else:
            sess.finalize()
            assert finals and finals[0].text == "the words"

        engine.delay_s = 30.0
        started = time.perf_counter()
        sess.feed(b"\x00\x20" * (BYTES_PER_S // 20))    # 50 ms, a new turn
        sess.finalize()
        elapsed = time.perf_counter() - started
    finally:
        sess.close()

    assert finals[-1].failed is True and finals[-1].text == ""
    assert elapsed < 5.0, (
        f"the second utterance waited {elapsed:.1f} s -- it inherited the "
        f"previous utterance's audio count")
