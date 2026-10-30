"""Wake arbitration. plan.md 4.2, AC-15, AC-16.

Two devices in one room hear the same wake word. Exactly one must capture,
and it must be the one that heard it BEST -- not the one whose packet
arrived first. First-to-report is the Home Assistant failure mode: the
device with the worst microphone often reports soonest because it fires on
less evidence.

So a candidate opens a short window, rivals join it, and the highest score
wins when the window closes. In Phase 1 there is only ever one satellite,
which makes this degenerate -- but the window still exists and is still
tested, because retrofitting arbitration into a code path that assumed a
single device is how you get a race nobody can reproduce.

Known limitation, stated in plan.md 8.3: scores from different microphones
with different gains are not strictly comparable. Raw comparison ships
first; per-device normalization waits for real distribution data.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

WINDOW_MS = 300


@dataclass
class Candidate:
    device_id: str
    score: float
    session: object          # the SatelliteSession that reported it
    client_ts_ms: int | None = None   # when the DEVICE says it fired
    recv_ms: float | None = None      # when the daemon actually received it
    word: str = ""                     # which wake word fired (AC-807)


class WakeArbiter:
    """One window at a time, daemon-wide.

    A second window cannot open while one is resolving: two overlapping
    windows would let two devices each win "their" arbitration and both
    start capturing, which is the exact outcome this class exists to
    prevent.
    """

    def __init__(self, window_ms: int = WINDOW_MS):
        self.window_s = window_ms / 1000.0
        self._candidates: list[Candidate] = []
        self._resolving = False
        self._lock = asyncio.Lock()

    @staticmethod
    def _log(candidate: Candidate) -> None:
        recv = ("" if candidate.recv_ms is None
                else f" recv_ms={candidate.recv_ms:.1f}")
        print(f"[butler][wake] device={candidate.device_id} "
              f"word={candidate.word} "
              f"score={candidate.score:.3f} "
              f"client_ts_ms={candidate.client_ts_ms}{recv}")

    async def submit(self, candidate: Candidate) -> list[Candidate]:
        """Add a candidate. The FIRST caller waits out the window and returns
        the ranked list; later callers within the same window return [].

        The try/finally is the whole safety story. The resolving caller is a
        satellite's own handler task, and that task is cancelled whenever the
        satellite disconnects -- which is a normal event, not an exception.
        Without the finally, `_resolving` stayed True forever, every later
        candidate returned [], and NO device on ANY satellite could capture
        again until the daemon restarted. That is precisely the
        microphone-off latch D-1 exists to forbid, and it was reachable by
        closing a browser tab at the wrong moment.
        """
        # Log EVERY candidate, winner or loser, with both clocks -- and log it
        # BEFORE the early return below, because the rivals are the whole
        # point. Open question 8.3 (are scores comparable across devices?)
        # cannot be settled by reasoning, only by a week of real
        # distributions, and comparing distributions requires both sides.
        # The field's lesson is that arrival order is not distance order:
        # WiFi jitter swamps the ~30 ms of time-of-flight between rooms, so
        # both timestamps are recorded to make a per-device offset possible
        # later.
        self._log(candidate)

        async with self._lock:
            self._candidates.append(candidate)
            if self._resolving:
                return []
            self._resolving = True

        try:
            await asyncio.sleep(self.window_s)
        finally:
            async with self._lock:
                ranked = sorted(self._candidates,
                                key=lambda c: c.score, reverse=True)
                # Clear on EVERY exit, including cancellation: a window that
                # was abandoned must not hand its candidates to the next one,
                # or a device that has stopped listening can win a turn it
                # cannot serve.
                self._candidates = []
                self._resolving = False
        return ranked
