"""Endpointing invariants. plan.md 4.2, AC-2, AC-6, D-1.

pipeline.py claims "a turn ALWAYS ends". These tests are what makes that a
fact rather than a comment. They drive TurnPipeline directly with a fake
clock, so six simulated seconds cost milliseconds of real time.

Two of these were written after a review found the code violating its own
docstring, so each one names the defect it caught.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import adapters, ports  # noqa: F401,E402
from daemon.pipeline import (EOU_MAX_WAIT_MS, NO_SPEECH_MS,  # noqa: E402
                             TurnPipeline)

VOICED_FRAME = b"\x00\x20" * 512      # ~32 ms of non-silent audio
SILENT_FRAME = b"\x00\x00" * 512


def frame_at(rms_target: float, ms: int = 32) -> bytes:
    """A frame whose RMS is approximately `rms_target` (0..1 full scale).

    A square wave, so RMS equals amplitude and the test can state the level
    it means rather than a sample value nobody can read.
    """
    import array
    amp = int(rms_target * 32768)
    return array.array("h", [amp if i % 2 else -amp
                             for i in range(16 * ms)]).tobytes()


class FakeClock:
    """Simulated monotonic time. Advances only when the pipeline asks."""

    def __init__(self, step_ms: float = 50.0):
        self.now = 0.0
        self.step = step_ms / 1000.0

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def make_pipeline(clock, endpointer=None, script=None):
    finals = []
    asr = ports.build(ports.ASRS, "fake", script if script is not None else [])
    p = TurnPipeline(
        turn=1, asr=asr,
        on_partial=lambda _p: None,
        on_final=lambda f, reason: finals.append((f, reason)),
        asr_config=ports.AsrConfig(model="test"),
        endpointer=endpointer, clock=clock, poll_s=0.001)
    return p, finals


async def feed_silence_gradually(p, frames: int, every_s: float = 0.001):
    """Stream silence the way a satellite does -- a frame at a time.

    Matters more than it looks: silence is accumulated from ARRIVING frames,
    not from wall-clock time, so pre-loading the whole gap before run()
    starts makes every cap fire on the first iteration and skips the logic
    under test.
    """
    for _ in range(frames):
        p.feed(SILENT_FRAME)
        await asyncio.sleep(every_s)


def make_pipeline(clock, endpointer=None, script=None, policy=None):
    finals = []
    asr = ports.build(ports.ASRS, "fake", script if script is not None else [])
    p = TurnPipeline(
        turn=1, asr=asr,
        on_partial=lambda _p: None,
        on_final=lambda f, reason: finals.append((f, reason)),
        asr_config=ports.AsrConfig(model="test"),
        endpointer=endpointer, clock=clock, poll_s=0.001, policy=policy)
    return p, finals


def test_the_end_phrase_counts_while_it_is_still_TENTATIVE():
    """MEASURED ON THE OWNER'S OWN TURNS, 2026-09-15, twice.

    Two turns on their laptop ran ~34 s each and ended because the owner
    pressed stop. Their transcript in `state/voice-trace.log` stopped growing
    at '...resultado da sua pesquisa anterior.' and stayed there while they
    went on talking; the words they actually said -- 'O que aconteceu? Pode
    ir' -- appear ONLY in that turn's `final`, which is read off `committed`
    AFTER `finalize()` flushes `tentative` into it. So the phrase sat in the
    engine's volatile suffix for the whole turn: invisible to the policy,
    which read `committed` alone, and visible to the owner, because the page
    draws `committed + tentative`. Their words: "it captures it, but it never
    sends it".

    The fake recogniser emits the SAME split -- the word it has just heard is
    `tentative` -- so this is the shape of the defect rather than an
    imitation of it.
    """
    from daemon.endpointing import EndWordEnd

    clock = FakeClock()
    script = ["please", "fix", "the", "thing", "send", "it"]
    p, finals = make_pipeline(clock, script=script,
                              policy=EndWordEnd(phrases=("send it",)))
    # 120 frames of 32 ms is 3.8 s, and the fake emits one word per 560 ms
    # (`asr_fake.CHUNK_MS`), so all six words actually play out.
    for _ in range(120):
        p.feed(frame_at(0.2))
    for _ in range(60):                 # and a pause after the last one
        p.feed(SILENT_FRAME)
    asyncio.run(p.run())

    assert finals, "the turn produced no final at all"
    final, reason = finals[0]
    assert reason == "end_word", (
        f"the phrase was the last thing said and the room went quiet, so the "
        f"turn was ended by {reason!r} instead -- the policy is not reading "
        f"the same text the page draws")
    assert final.text == "please fix the thing", (
        f"the phrase was not stripped out of what the brain sees: "
        f"{final.text!r}")


def test_a_phrase_that_is_NOT_last_does_not_submit_and_says_so(capsys):
    """The other half: this must not become "anywhere in the sentence".

    Reading `tentative` widens WHAT the policy can see, never WHERE the phrase
    may be. "send it back to the previous version" keeps listening -- and the
    turn then ends the way the owner's did, by hand, so this also holds the
    line that tells them WHY.
    """
    from daemon.endpointing import EndWordEnd

    clock = FakeClock()
    script = ["please", "send", "it", "back", "to", "the", "older", "one"]
    p, finals = make_pipeline(clock, script=script,
                              policy=EndWordEnd(phrases=("send it",)))
    for _ in range(160):               # 5.1 s: all eight words (560 ms each)
        p.feed(frame_at(0.2))
    for _ in range(20):
        p.feed(SILENT_FRAME)

    async def go():
        task = asyncio.create_task(p.run())
        await asyncio.sleep(0.1)       # let it poll with the phrase mid-text
        assert "send it" in (p._transcript or ""), p._transcript
        await p.finalize_now("client_hint")     # the owner taps stop
        await task

    asyncio.run(go())

    assert finals, "the turn produced no final at all"
    final, reason = finals[0]
    assert reason != "end_word", (
        f"a phrase in the MIDDLE of a sentence submitted the turn: "
        f"{final.text!r}")
    assert "send it" in final.text, (
        f"the phrase was stripped from a turn that did NOT end on it: "
        f"{final.text!r}")
    out = capsys.readouterr().out
    assert "the end phrase 'send it' was heard" in out, (
        f"a turn that ended with the phrase unspent said nothing about it, "
        f"so the owner's own tap is not a diagnosis: {out!r}")


@pytest.mark.asyncio
async def test_a_long_utterance_is_not_cut_off_at_the_hard_cap():
    """The hard cap bounds SILENCE, not the length of what someone says.

    Caught a real defect: the cap was measured from turn start, so every
    utterance longer than 6 s of speech was force-finalized mid-sentence.
    The spike measured it against silenceMs (static/app.js:198) and was
    right to. "Talk me through whether Butler should use a message bus"
    with any thinking pauses is already past 6 s.
    """
    clock = FakeClock(step_ms=100)
    p, finals = make_pipeline(clock, script=["talking", "for", "a", "while"])

    async def keep_talking():
        # 20 simulated seconds of continuous speech.
        for _ in range(200):
            p.feed(VOICED_FRAME)
            await asyncio.sleep(0)

    talker = asyncio.create_task(keep_talking())
    runner = asyncio.create_task(p.run())
    await talker
    await asyncio.sleep(0.05)

    assert not runner.done(), (
        f"the turn ended while the user was still speaking: {finals}")
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_silence_past_the_cap_ends_the_turn_even_if_the_model_says_no():
    """Fail-open: an endpointer that never says "done" cannot hold the turn.

    This is the cap doing its actual job -- bounding how long we wait for a
    verdict once the user has stopped, not how long they may speak.
    """
    clock = FakeClock(step_ms=100)

    async def never_done(_pcm):
        return 0.0

    p, finals = make_pipeline(clock, endpointer=never_done, script=["hello"])
    for _ in range(10):
        p.feed(VOICED_FRAME)
    for _ in range(int(EOU_MAX_WAIT_MS / 32) + 20):
        p.feed(SILENT_FRAME)

    await asyncio.wait_for(p.run(), timeout=10)
    assert finals, "silence past the hard cap did not end the turn"
    assert finals[0][1] == "hard_cap", f"ended for the wrong reason: {finals}"


@pytest.mark.asyncio
async def test_a_wedged_endpointer_cannot_hang_the_turn():
    """D-1: a turn ALWAYS ends.

    Caught a real defect: the endpointer was awaited with no timeout inside
    the poll loop, so a wedged smart-turn child blocked the loop and the
    hard cap was never re-evaluated. The child process is exactly the thing
    most likely to wedge -- it is a separate process doing numpy and ONNX
    work under GPU contention.
    """
    clock = FakeClock(step_ms=100)
    hung = asyncio.Event()

    async def wedged(_pcm):
        hung.set()
        await asyncio.sleep(3600)     # never returns

    p, finals = make_pipeline(clock, endpointer=wedged, script=["hello"])
    for _ in range(10):
        p.feed(VOICED_FRAME)
    # Just past EOU_SILENCE_MS, so the endpointer IS consulted, but well short
    # of the hard cap -- otherwise the cap fires first and the wedge is never
    # exercised, which is how this test would pass while testing nothing.
    for _ in range(12):
        p.feed(SILENT_FRAME)

    feeder = asyncio.create_task(
        feed_silence_gradually(p, int(EOU_MAX_WAIT_MS / 32) + 60))
    await asyncio.wait_for(p.run(), timeout=30)
    feeder.cancel()

    assert hung.is_set(), "the test never actually reached the endpointer"
    assert finals, "a wedged endpointer prevented the turn from ending"


@pytest.mark.asyncio
async def test_without_an_endpointer_a_short_pause_does_not_end_the_turn():
    """A breath is not the end of a sentence.

    EOU_SILENCE_MS is the cadence at which a MODEL gets asked, not evidence
    on its own. With no endpointer -- a pipeline built without one, or a
    daemon started with endpointer "none" -- ending at 280 ms cuts people off
    mid-thought. The field's floor for pure-silence endpointing is
    500-900 ms.
    """
    clock = FakeClock(step_ms=100)
    p, finals = make_pipeline(clock, script=["hello"])
    for _ in range(20):
        p.feed(VOICED_FRAME)
    for _ in range(12):            # ~384 ms: past 280, short of 700
        p.feed(SILENT_FRAME)

    runner = asyncio.create_task(p.run())
    await asyncio.sleep(0.05)
    assert not runner.done(), (
        f"a 384 ms pause ended the turn: {finals}")

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_without_an_endpointer_a_real_pause_does_end_the_turn():
    """The other half: the floor must not become a hang.

    Without this, raising the threshold could be 'fixed' by never ending a
    turn at all, and the test above would still pass.
    """
    clock = FakeClock(step_ms=100)
    p, finals = make_pipeline(clock, script=["hello"])
    for _ in range(20):
        p.feed(VOICED_FRAME)

    feeder = asyncio.create_task(feed_silence_gradually(p, 60))
    await asyncio.wait_for(p.run(), timeout=15)
    feeder.cancel()

    assert finals, "silence past the floor never ended the turn"
    assert finals[0][1] == "silence", (
        # Renamed deliberately when endpointing became a policy: "endpoint"
        # implies a model was consulted, and in this branch none was. The
        # reason travels to the satellite and into the logs, so it should say
        # what actually happened.
        f"ended for the wrong reason: {finals[0][1]}")


@pytest.mark.asyncio
async def test_a_noisy_room_still_endpoints():
    """The energy gate must key off the room's floor, not an absolute level.

    RMS_OFF is 0.012 (about -38 dBFS) and getUserMedia leaves
    autoGainControl at its default of ON, so a room whose post-AGC floor
    sits above that never accumulates a millisecond of silence. Every
    threshold below is then unreachable and the turn runs to the ABSOLUTE
    ceiling -- 120 s of a hot microphone before anyone gets an answer.

    This got worse, not better, when the hard cap was correctly moved onto
    silence: the old elapsed-time cap at least ended the turn at 6 s. So the
    adaptive floor is what keeps that fix safe.

    Here the "silence" between words sits at 0.015 -- above RMS_OFF and
    below RMS_ON -- which is an ordinary room with AGC on, not a pathological
    case.
    """
    clock = FakeClock(step_ms=100)
    room = frame_at(0.015)
    speech = frame_at(0.060)

    p, finals = make_pipeline(clock, script=["hello", "there"])
    for _ in range(30):
        p.feed(room)                # let the floor estimate settle
    for _ in range(20):
        p.feed(speech)

    async def keep_the_room_noisy():
        for _ in range(int(EOU_MAX_WAIT_MS / 32) + 60):
            p.feed(room)
            await asyncio.sleep(0.001)

    feeder = asyncio.create_task(keep_the_room_noisy())
    await asyncio.wait_for(p.run(), timeout=30)
    feeder.cancel()

    assert finals, "a noisy room never ended the turn"
    assert finals[0][1] != "absolute_cap", (
        "the turn only ended at the 120 s safety net, so the energy gate "
        "never saw the gaps between words as silence")


@pytest.mark.asyncio
async def test_silence_only_turn_ends_with_an_empty_final():
    """AC-6 / D-7: wake fired, nobody spoke."""
    clock = FakeClock(step_ms=100)
    p, finals = make_pipeline(clock)
    for _ in range(int(NO_SPEECH_MS / 32) + 20):
        p.feed(SILENT_FRAME)

    await asyncio.wait_for(p.run(), timeout=10)
    assert finals, "a silent turn never ended"
    assert finals[0][0].text == ""
    assert finals[0][1] == "no_speech"


@pytest.mark.asyncio
async def test_a_dead_source_ends_the_turn_and_discards_the_text():
    """AC-17: the capturing satellite vanished mid-utterance.

    The partial transcript must be discarded rather than acted on -- half a
    sentence is not a command, and acting on it is how a dropped phone
    deletes something.
    """
    clock = FakeClock(step_ms=100)
    p, finals = make_pipeline(clock, script=["delete", "everything"])
    for _ in range(40):
        p.feed(VOICED_FRAME)

    alive = {"v": True}

    async def kill_soon():
        await asyncio.sleep(0.02)
        alive["v"] = False

    asyncio.create_task(kill_soon())
    await asyncio.wait_for(p.run(alive=lambda: alive["v"]), timeout=10)

    assert finals, "a dead source left the turn hanging"
    assert finals[0][1] == "source_lost"
    assert finals[0][0].text == "", (
        f"a discarded turn must not carry text: {finals[0][0].text!r}")


class FakeSettings:
    """Minimal stand-in for the settings store."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, fallback=None):
        return self._values.get(key, fallback)


@pytest.mark.asyncio
async def test_a_policy_may_raise_the_absolute_ceiling():
    """Caller-side compliance for the long-form ceiling (D-18).

    EndWordEnd declaring `max_turn_ms` proves nothing if the pipeline never
    reads it -- and for a while it did not, so a long-form turn was cut at
    the conversational ceiling and the mode quietly failed at the one thing
    it exists for. Asserting the attribute left that green.
    """
    from daemon import endpointing

    clock = FakeClock(step_ms=100)
    finals = []
    asr = ports.build(ports.ASRS, "fake", ["still", "thinking"])
    p = TurnPipeline(
        turn=1, asr=asr, on_partial=lambda _p: None,
        on_final=lambda f, r: finals.append((f, r)),
        asr_config=ports.AsrConfig(model="test"),
        policy=endpointing.EndWordEnd(), poll_s=0.001,
        clock=clock,
        # A ceiling far below the policy's own, so ignoring the policy ends
        # the turn almost immediately.
        settings=FakeSettings(absolute_max_turn_ms=1000))

    for _ in range(20):
        p.feed(VOICED_FRAME)

    runner = asyncio.create_task(p.run())
    await asyncio.sleep(0.25)          # simulated time races well past 1000 ms

    assert not runner.done(), (
        f"long-form turn was cut at the conversational ceiling: {finals}")
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
