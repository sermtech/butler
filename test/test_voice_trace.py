"""The voice trace: one line per stage of a turn. `daemon/trace.py`.

WHY IT IS TESTED AT ALL, given it is "only logging". The whole value of this
file is that it is there on the day an intermittent fault happens, in a
process nobody attached a debugger to. A trace that silently stopped writing
is worse than none: it is an instrument that reads zero and is believed. So
the gates below are the four properties that make it trustworthy -- a turn
leaves a readable record, a pause is COUNTABLE, a broken writer cannot cost a
turn, and the off switch is real.

The counting one is the point of the feature. An earlier prototype's own trace is what let
them write "17 discarded pauses; one turn in nine never closed" about a bug
that looked random; the same sentence about Butler needs one `speech_end`
line per pause and no more.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import adapters, ports, trace  # noqa: F401,E402
from daemon.pipeline import TurnPipeline  # noqa: E402

from test_pipeline_endpointing import (FakeClock, frame_at,  # noqa: E402
                                       SILENT_FRAME)


@pytest.fixture(autouse=True)
def scratch_trace(tmp_path, monkeypatch):
    """Every test writes its own trace file and closes the handle after.

    `trace` keeps ONE module-level handle, so without this a test would
    append into whatever file the previous one opened -- and on Windows the
    open handle would stop `tmp_path` being cleaned up.
    """
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setenv("BUTLER_TRACE", "1")
    trace.close()
    yield tmp_path / "voice-trace.log"
    trace.close()


def read(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("---")]


def stages(lines) -> list[str]:
    """The stage word of each line: `+12ms dt=+3ms STAGE k=v` -> STAGE."""
    return [ln.split()[2] for ln in lines if len(ln.split()) > 2]


# EIGHT voiced frames and not six. Each is 32 ms, so six is 192 ms -- just
# under `min_speech_ms` (200), which makes the pipeline `continue` without ever
# consulting the policy, and the turn then runs to the ABSOLUTE CAP. That is a
# real 37 s per test on Windows, where `asyncio.sleep(0.001)` rounds up to the
# ~15 ms timer granularity and the cap costs 2,400 polls. Measured: 149 s for
# this file at six frames, 3 s at eight.
def run_turn(clock, frames, endpointer=None):
    """One pipeline, fed `frames`, run to its natural end."""
    asr = ports.build(ports.ASRS, "fake", [])
    p = TurnPipeline(
        turn=7, asr=asr,
        on_partial=lambda _p: None,
        on_final=lambda f, reason: None,
        asr_config=ports.AsrConfig(model="test"),
        endpointer=endpointer, clock=clock, poll_s=0.001)
    for f in frames:
        p.feed(f)
    asyncio.run(p.run())
    return p


def test_a_turn_leaves_a_readable_record(scratch_trace):
    """Open, speech, end: the three lines a turn that worked always has.

    Asserted as an ORDERED subsequence rather than an exact list, because
    the partial and resume lines in between depend on the fake engine's
    script and are not what this gate is about.
    """
    clock = FakeClock()
    run_turn(clock, [frame_at(0.2)] * 8 + [SILENT_FRAME] * 40)

    lines = read(scratch_trace)
    assert lines, ("the turn wrote no trace at all, so the instrument reads "
                   "zero on a working pipeline and would be believed")
    seen = stages(lines)
    for stage in ("turn_open", "speech_start", "speech_end", "turn_end"):
        assert stage in seen, (
            f"no `{stage}` line in the trace; a turn's record is not "
            f"readable without it: {seen}")
    assert (seen.index("turn_open") < seen.index("speech_start")
            < seen.index("turn_end")), f"the stages are out of order: {seen}"


def test_the_turn_end_line_carries_the_reason(scratch_trace):
    """`reason` is the whole diagnosis, so it must be ON the line.

    `end_word` versus `absolute_cap` is the difference between Butler
    hearing the phrase and Butler waiting out its ceiling. They look
    identical from outside the daemon and have opposite fixes.
    """
    clock = FakeClock()
    run_turn(clock, [frame_at(0.2)] * 8 + [SILENT_FRAME] * 40)

    end = [ln for ln in read(scratch_trace) if " turn_end " in ln]
    assert end, "no turn_end line"
    assert "reason=" in end[0] and "reason=none" not in end[0], (
        f"turn_end carries no reason, so a turn that hit its ceiling reads "
        f"exactly like one that heard its end phrase: {end[0]}")
    assert "turn=7" in end[0], f"the line does not say which turn: {end[0]}"


def test_a_pause_is_countable_and_is_logged_once(scratch_trace):
    """ONE `speech_end` per pause -- the number the feature exists for.

    A line per FRAME would bury the decisions in the noise they explain, and
    a line per pause is what makes "pauses per closed turn" a number you can
    grep. Two pauses here, separated by speech, so a per-frame writer fails
    loudly rather than merely looking verbose.
    """
    clock = FakeClock()
    loud, quiet = frame_at(0.2), SILENT_FRAME
    run_turn(clock, [loud] * 4 + [quiet] * 8 + [loud] * 4 + [quiet] * 40)

    seen = stages(read(scratch_trace))
    pauses = seen.count("speech_end")
    assert pauses == 2, (
        f"expected exactly 2 speech_end lines for 2 pauses, got {pauses}. "
        f"More means it is writing per frame and the count is useless; "
        f"fewer means a pause went unrecorded: {seen}")
    assert seen.count("speech_resume") == 1, (
        f"the resumed speech between the two pauses was not recorded, so a "
        f"pause and a pause-that-was-cancelled read alike: {seen}")


def test_a_broken_trace_never_costs_the_turn(scratch_trace, monkeypatch):
    """D-1, on the one path that runs from the audio thread.

    A full disk or a locked file must cost a trace line and never the
    microphone. Driven by making the writer itself raise, which is the only
    way to be sure the guard is real rather than merely present.
    """
    def explode(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(trace, "_ensure_locked", explode)
    trace.trace("this must not raise", turn=1)

    clock = FakeClock()
    p = run_turn(clock, [frame_at(0.2)] * 8 + [SILENT_FRAME] * 40)
    assert p._finalized, (
        "the turn did not end while the trace writer was raising, so a full "
        "disk would leave the microphone switched off (D-1)")


def test_the_off_switch_is_real(scratch_trace, monkeypatch):
    """`BUTLER_TRACE=0` writes nothing at all.

    Asserted on the FILE and not on `enabled()`, because a flag that reads
    false while the writer still appends is the shape of an off switch that
    is decoration.
    """
    monkeypatch.setenv("BUTLER_TRACE", "0")
    clock = FakeClock()
    run_turn(clock, [frame_at(0.2)] * 8 + [SILENT_FRAME] * 40)
    assert not scratch_trace.exists() or read(scratch_trace) == [], (
        "BUTLER_TRACE=0 still wrote lines")


def test_it_rotates_rather_than_growing_without_limit(scratch_trace,
                                                      monkeypatch):
    """One previous file, and the live one starts again.

    A log that grows forever on an always-on daemon is the trap
    `delete_shortcut`'s docstring already names in another place: a thing
    whose only exit is hand-editing a file the owner was never told about.
    """
    monkeypatch.setattr(trace, "MAX_BYTES", 2_000)
    for i in range(400):
        trace.trace("filler", turn=i, note="x" * 40)

    assert scratch_trace.exists(), "the live trace file is gone after rotating"
    assert scratch_trace.stat().st_size <= 2_000 + 4_000, (
        f"the live file is {scratch_trace.stat().st_size} bytes, so rotation "
        f"is not bounding it")
    assert scratch_trace.with_suffix(".log.1").exists(), (
        "nothing was kept: rotation threw the previous file away instead of "
        "keeping one")


def test_the_turn_open_line_names_the_end_phrases(scratch_trace):
    """WHAT THE POLICY IS WAITING FOR, on the line that names the policy.

    2026-09-15, from a real diagnosis. Five long-form turns on the owner's
    laptop had no `turn_end` at all, and the first question -- was the phrase
    they said even configured on that machine -- could only be answered by
    reading a settings file over ssh on a machine with a different list. The
    trace named the policy and never the words, so the one line that exists
    to explain a turn that will not close explained half of it.
    """
    from daemon.endpointing import EndWordEnd, SilenceEnd

    def open_with(policy):
        asr = ports.build(ports.ASRS, "fake", [])
        TurnPipeline(
            turn=7, asr=asr,
            on_partial=lambda _p: None,
            on_final=lambda _f, _r: None,
            asr_config=ports.AsrConfig(model="test"),
            clock=FakeClock(), poll_s=0.001, policy=policy)

    open_with(EndWordEnd(phrases=("send it", "pode ir")))
    open_with(SilenceEnd("balanced"))

    openings = [ln for ln in read(scratch_trace) if " turn_open " in ln]
    assert len(openings) == 2, f"expected two turn_open lines: {openings}"
    assert "pode ir" in openings[0] and "send it" in openings[0], (
        f"the end phrases are not on the turn_open line, so a turn that ran "
        f"to its ceiling cannot say whether the phrase was configured: "
        f"{openings[0]}")
    assert "phrases=" not in openings[1], (
        f"a timed policy claims to be waiting for a word: {openings[1]}")


def test_the_tail_is_the_end_of_the_transcript(scratch_trace):
    """`tail()` keeps the END, because that is where the decision is made.

    `EndWordEnd` anchors its match to the end of the transcript, so a trace
    showing the HEAD could not answer "did the end phrase arrive" -- which is
    the single question a turn that never closed is asking.
    """
    text = "please go and " + ("x" * 200) + " over to you"
    got = trace.tail(text, n=30)
    assert "over to you" in got, (
        f"the tail does not contain the end of the transcript: {got}")
    assert "please go and" not in got, (
        f"the tail kept the head instead of the end: {got}")
