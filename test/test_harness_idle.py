"""A harness that goes SILENT must not hold the turn forever (D-34).

`brain_harness._answer` reads the harness event stream. A child that refuses
a prompt fails fast and visibly -- pi answers `stopReason: error` and the
adapter turns it into `Failed` -- and that path has always ended the turn. A
child that ACCEPTS the prompt and then emits nothing is a different failure
and there was no bound on it at all.

MEASURED 2026-09-09, before the fix: a harness that starts, accepts the
prompt and never emits a terminal event left the turn open past 25 s with one
`Say` delivered -- the fixed line spoken before the spawn (D-10). So the room
hears "let me look at that" and then silence, and the microphone stays held
(D-1). That is what turn 1 of 2026-09-08 looks like in `butler.log`: a wake,
a user message in `chats/`, and then nothing for 158 s.

**The bound is on SILENCE, never on duration.** A coding agent legitimately
thinks for minutes, and pi emits deltas and tool events the whole time it is
working. A total budget would cut off real work; an idle budget only fires
when nothing at all is arriving.

This looks like it contradicts `test/AGENTS.md`'s "one deadline for a whole
read, never one per event, because a budget spent per event never expires
against a child that keeps talking". It is the same observation used
deliberately: a child that keeps talking is a child that is WORKING, and the
rule was written for `harness_pi`'s own reads, where the total is what needed
bounding. Here the total must NOT be bounded.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import chats as chats_mod                          # noqa: E402
from daemon.adapters.brain_harness import HarnessOnlyBrain     # noqa: E402
from daemon.ports import (AgentEnd, Done, HarnessSpec, Say,    # noqa: E402
                          TextDelta, TurnContext)

IDLE_S = 0.4          # short, so the suite does not wait out a real budget


class _Caps:
    native_resume = False


class ScriptedHarness:
    """Starts, accepts a prompt, and emits what it was told to -- or nothing.

    `gaps` is a list of `(delay_seconds, event_or_None)`. `None` means "wait
    that long and then stop yielding", which is the silent-child case; the
    stream simply never produces another item.
    """

    caps = _Caps()

    def __init__(self, gaps):
        self.gaps = list(gaps)
        self.disposed = 0
        self.aborted: list[int | None] = []

    async def start(self, spec) -> None:
        return None

    async def prompt(self, text: str) -> None:
        return None

    def subscribe(self):
        gaps = list(self.gaps)

        async def _stream():
            for delay, event in gaps:
                await asyncio.sleep(delay)
                if event is None:
                    await asyncio.sleep(3600)     # silent forever
                yield event
        return _stream()

    async def abort(self, turn_id=None) -> None:
        self.aborted.append(turn_id)

    async def dispose(self) -> None:
        self.disposed += 1

    @property
    def info(self) -> dict:
        return {"engine": "scripted"}


def build(tmp_path, harness):
    chats = chats_mod.ChatStore(tmp_path)
    return HarnessOnlyBrain(
        harness_for=lambda _chat=None: harness,
        # `chat` is which conversation the child is for, and the spawn path
        # names it (`docs/parallel-agents.md` phase 4). Ignored here: what this
        # file measures is a CLOCK, and one fixed spec is what keeps a spawn
        # out of the measurement.
        spec_for=lambda cwd=None, chat=None: HarnessSpec(
            model=None, cwd=str(tmp_path),
            session_file=str(tmp_path / "s.json")),
        chats=chats,
        # The agent cap (AC-237). One turn, one conversation, one child:
        # the shipped default is what this file runs on, so nothing here
        # ever reaches it.
        cap_for=lambda: 10)


async def drive(brain, budget: float = 10.0):
    """Run one turn and answer `(intent names, seconds)`.

    Bounded by `asyncio.wait_for` so a regression FAILS instead of hanging;
    a test that waits forever reports nothing at all (test/AGENTS.md).
    """
    ctx = TurnContext(turn_id=1, mode="secretary", history=())
    seen: list[str] = []

    async def _run():
        async for intent in brain.turn("hello", ctx):
            seen.append(type(intent).__name__)
            if isinstance(intent, Done):
                return

    started = time.monotonic()
    await asyncio.wait_for(_run(), budget)
    return seen, time.monotonic() - started


@pytest.mark.asyncio
async def test_a_silent_harness_ends_the_turn(tmp_path, monkeypatch):
    """D-34. The turn ends, the room is told, and the microphone comes back.

    Without the deadline this does not fail with an assertion -- it runs until
    `drive`'s own budget expires, which is the shape of the defect: nothing
    ends.
    """
    import daemon.adapters.brain_harness as bh
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    harness = ScriptedHarness([(0.0, None)])
    brain = build(tmp_path, harness)

    seen, took = await drive(brain)

    assert "Done" in seen, (
        f"the turn never ended; the owner heard {seen} and is still waiting")
    assert took < IDLE_S * 6, (
        f"the turn took {took:.1f} s against a {IDLE_S:.1f} s idle budget")
    assert any(n == "Say" for n in seen), (
        "the turn ended silently; a room that hears nothing cannot tell a "
        "failure from Butler ignoring it (AC-7b, D-1)")


@pytest.mark.asyncio
async def test_a_working_harness_is_never_cut_off(tmp_path, monkeypatch):
    """The other half, and the one that matters more.

    A coding agent thinks for minutes. The budget must measure SILENCE, so a
    child that keeps emitting -- deltas, tool events -- runs as long as it
    likes. A total-duration budget would pass the test above and destroy real
    work, which is the more expensive failure.
    """
    import daemon.adapters.brain_harness as bh
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    # Eight gaps of just under the budget: far past it in TOTAL, never idle.
    step = IDLE_S * 0.6
    script = [(step, TextDelta(f"part {i}. ")) for i in range(8)]
    script.append((step, AgentEnd()))
    harness = ScriptedHarness(script)
    brain = build(tmp_path, harness)

    seen, took = await drive(brain)

    assert took > IDLE_S * 3, (
        f"the fixture did not actually outrun the budget in total: {took:.1f}s")
    assert "Done" in seen and seen.count("Say") >= 1, (
        f"a working harness was cut off: {seen}")


@pytest.mark.asyncio
async def test_the_budget_is_a_setting_a_person_can_raise(tmp_path):
    """It is a guess until somebody measures a slower machine.

    Written as a module constant with a name rather than a literal in the
    loop, so the value is findable and the docstring above it is where the
    provenance lives.
    """
    import daemon.adapters.brain_harness as bh

    assert hasattr(bh, "HARNESS_IDLE_S"), (
        "the idle budget has no name, so nobody can find it to change it")
    assert bh.HARNESS_IDLE_S >= 60, (
        f"a budget under a minute will cut off a coding agent that is "
        f"thinking: {bh.HARNESS_IDLE_S}")


@pytest.mark.asyncio
async def test_a_silence_timeout_is_not_replayed(tmp_path, monkeypatch):
    """One budget of silence, not two (D-34).

    The replay-once rule exists for a transient blip. A child that accepted
    the prompt and then said nothing for the whole budget is not evidence of
    one, and replaying it costs a SECOND full budget before the room hears
    anything -- six minutes at the measured 180 s. Asserted on the wall clock
    rather than on a call count, because what the owner actually pays is time.
    """
    import daemon.adapters.brain_harness as bh
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    harness = ScriptedHarness([(0.0, None)])
    brain = build(tmp_path, harness)

    _seen, took = await drive(brain)

    assert took < IDLE_S * 1.9, (
        f"the turn took {took:.2f} s against a {IDLE_S:.2f} s budget, which "
        f"is two budgets: the timeout was replayed")


@pytest.mark.asyncio
async def test_the_measured_gap_would_not_trip_the_budget(tmp_path):
    """The number is MEASURED, and this is what it was measured against.

    Six real pi turns, 146 gaps [MEASURED 2026-09-09]: p95 7.27 s, p99
    40.2 s, max 78.6 s -- a turn that finished correctly after a 78.6 s
    silence, because a parallel tool batch emits nothing until it returns.
    The first draft of this constant was 120 s, chosen on the reasoning that
    it was well past any healthy gap, and it would have killed that turn.
    """
    import daemon.adapters.brain_harness as bh

    assert bh.HARNESS_IDLE_S >= 78.6 * 2, (
        f"the budget is {bh.HARNESS_IDLE_S} s against a MEASURED healthy "
        f"maximum of 78.6 s. That gap scales with the directory a tool "
        f"searches rather than with the model, so a bigger project pushes "
        f"past it with nothing wrong")
