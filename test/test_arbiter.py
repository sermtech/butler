"""Wake arbitration. plan.md 4.2, AC-15, AC-16, D-1.

The arbiter is a daemon-wide singleton guarding a shared window, which makes
it the one object in Butler that can disable the microphone for EVERY device
at once. These tests exist mostly for that: correctness of the ranking is
easy, and surviving a cancellation is the part that bites.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon.arbiter import Candidate, WakeArbiter  # noqa: E402


def cand(device: str, score: float) -> Candidate:
    return Candidate(device_id=device, score=score, session=object())


@pytest.mark.asyncio
async def test_highest_score_wins_not_first_to_report():
    """AC-15: the device that heard it BEST answers, not the one that
    reported soonest.

    First-to-report is the documented failure mode -- the microphone with the
    worst signal often fires earliest because it needs less evidence.
    """
    arb = WakeArbiter(window_ms=60)

    async def late_but_better():
        await asyncio.sleep(0.02)
        return await arb.submit(cand("desk", 0.88))

    first = asyncio.create_task(arb.submit(cand("phone", 0.61)))
    second = asyncio.create_task(late_but_better())
    ranked, also = await asyncio.gather(first, second)

    winners = ranked or also
    assert winners, "nobody won the arbitration"
    assert winners[0].device_id == "desk", (
        f"the louder candidate should win, got {winners[0].device_id}")
    assert [c.device_id for c in winners[1:]] == ["phone"]


@pytest.mark.asyncio
async def test_only_one_caller_resolves_a_window():
    """Two overlapping windows would let two devices each win 'their'
    arbitration and both start capturing -- the exact outcome this class
    exists to prevent."""
    arb = WakeArbiter(window_ms=40)
    results = await asyncio.gather(
        arb.submit(cand("a", 0.5)),
        arb.submit(cand("b", 0.9)),
        arb.submit(cand("c", 0.7)),
    )
    resolved = [r for r in results if r]
    assert len(resolved) == 1, f"{len(resolved)} callers resolved one window"
    assert len(resolved[0]) == 3, "the window lost candidates"


@pytest.mark.asyncio
async def test_every_candidate_is_logged_including_the_losers(capsys):
    """Open question 8.3 needs both sides of the comparison.

    The first version logged after the early return that rivals take, so
    only the window-opener was ever recorded -- which is exactly backwards:
    the rivals are the reason the log exists. Comparing per-device score
    distributions is impossible if only one device per window is written
    down, and nothing would have looked wrong for weeks.
    """
    arb = WakeArbiter(window_ms=40)
    await asyncio.gather(
        arb.submit(Candidate("desk", 0.88, object(), client_ts_ms=1000,
                             recv_ms=5.0)),
        arb.submit(Candidate("phone", 0.61, object(), client_ts_ms=1010,
                             recv_ms=7.5)),
    )
    out = capsys.readouterr().out
    assert "device=desk" in out and "score=0.880" in out
    assert "device=phone" in out and "score=0.610" in out, (
        f"the losing candidate was never logged:\n{out}")
    assert "client_ts_ms=1010" in out, "client clock missing for the rival"


@pytest.mark.asyncio
async def test_a_cancelled_window_does_not_disable_wake_forever():
    """D-1, and the worst bug this file can catch.

    `_resolving` was set before an await with no finally, so cancelling the
    resolving task -- which happens whenever a satellite disconnects
    mid-window, since its handler task is cancelled -- left the flag stuck
    True. Every later wake_candidate then returned [] and NO device could
    ever capture again, on any satellite, until the daemon restarted. That
    is the microphone-off latch the whole project is built to avoid.
    """
    arb = WakeArbiter(window_ms=200)

    task = asyncio.create_task(arb.submit(cand("desk", 0.9)))
    await asyncio.sleep(0.02)          # let it claim the window
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ranked = await asyncio.wait_for(arb.submit(cand("phone", 0.8)), timeout=2)
    assert ranked, "wake is permanently dead after one cancelled window"
    assert ranked[0].device_id == "phone"


@pytest.mark.asyncio
async def test_a_cancelled_window_leaves_no_stale_candidates():
    """The next window must not inherit the abandoned one's candidates, or a
    device that is no longer listening can win a turn it cannot serve."""
    arb = WakeArbiter(window_ms=200)
    task = asyncio.create_task(arb.submit(cand("ghost", 0.99)))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ranked = await asyncio.wait_for(arb.submit(cand("desk", 0.5)), timeout=2)
    assert [c.device_id for c in ranked] == ["desk"], (
        f"a cancelled window leaked its candidates: "
        f"{[c.device_id for c in ranked]}")
