"""Construction-time settings take effect with no owner restart (AC-713..AC-716).

`build_hub` constructs five things from settings once, at boot. Which route a
change takes is decided per setting by what rebuilding it COSTS
[MEASURED 2026-10-27, docs/lessons/daemon.md]:

* LIVE SWAP (AC-713) -- `asr_latency` and `endpointer`. The new engine is
  built and warmed off the turn path, then handed to the hub in ONE
  assignment, so the next capture reads it and a capture already running
  keeps the engine it opened. Neither old engine holds anything of its own to
  dispose: the recognizer's GGUF is the engine's shared cache and the
  smart-turn child is a module-wide pool. A warm-up that fails is said out
  loud and the running engine stays (D-1).
* RESTART WHEN IDLE (AC-715) -- every setting the schema flags `restart`
  (`asr_model`, `asr_backend`, `brain_mode`). Once `Settings.pending()` has
  been unchanged for `SETTLE_S` and the hub says nothing is running
  (`Hub.busy`), the daemon restarts itself (`butlerd.do_restart`). A value
  put back before then owes nothing.

Polling, never a timer that decides on its own: the waiter only re-reads
`pending()` and `busy()` every `POLL_S`, and nothing here blocks the loop.
"""
from __future__ import annotations

import asyncio
import time

from .settings import BY_KEY

# How often the waiter asks "is anything running?" while a restart is owed.
POLL_S = 1.0
# How long the pending set must sit unchanged before the restart: long enough
# for the owner to change two settings in a row and read the bar saying so,
# short enough to feel automatic [DECIDED 2026-10-27].
SETTLE_S = 5.0


class Applier:
    """Puts every construction-time setting change into effect.

    `swaps` maps a live key to an async callable that builds, warms and
    installs the engine from the CURRENT value; `busy` answers why a restart
    now would cut something off (None when idle); `restart` replaces the
    process. All three are the daemon's: this class reads no engine.
    """

    def __init__(self, settings, swaps: dict, busy, restart) -> None:
        self._settings = settings
        self._swaps = dict(swaps)
        self._busy = busy
        self._restart = restart
        # One swap per key at a time; each reads the value when it RUNS, so
        # the last of two quick changes is what ends up installed.
        self._locks = {key: asyncio.Lock() for key in self._swaps}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiter: asyncio.Task | None = None
        self._changed_at = 0.0
        # Strong references: a task only the loop holds can be collected.
        self._tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        """Listen from now on. Called once, on the running loop (`_on_start`)."""
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._settings.on_change(self._on_change)
        if self._settings.pending():
            self._arm()

    def _on_change(self, key: str, _value) -> None:
        """`Settings.on_change` listener: synchronous, so it only schedules."""
        self._loop.call_soon_threadsafe(self._schedule, key)

    def _schedule(self, key: str) -> None:
        if key in self._swaps:
            self._keep(self._loop.create_task(self._swap(key)))
        elif BY_KEY[key].restart and (self._settings.pending()
                                      or self._waiter_running()):
            # A change, or a value put back: either way the pending set moved,
            # so the settle starts again and the waiter re-reads it.
            self._changed_at = time.monotonic()
            self._arm()

    def _keep(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _swap(self, key: str) -> None:
        async with self._locks[key]:
            try:
                await self._swaps[key]()
                print(f"[butler] {key} applied live: "
                      f"{self._settings.get(key)!r}, no restart", flush=True)
            except Exception as e:                        # noqa: BLE001
                # Loud and never fatal (D-1): the running engine stays, and
                # the next change to this setting tries again.
                print(f"[butler] {key}: the new engine would not start "
                      f"({e!r}); still running the previous one", flush=True)

    def _waiter_running(self) -> bool:
        return self._waiter is not None and not self._waiter.done()

    def _arm(self) -> None:
        if not self._waiter_running():
            self._waiter = self._loop.create_task(self._restart_when_idle())

    async def _restart_when_idle(self) -> None:
        said = None
        try:
            while True:
                pending = self._settings.pending()
                if not pending:
                    print("[butler] restart no longer needed: every "
                          "restart setting is back to what is running",
                          flush=True)
                    return
                busy = self._busy()
                settled = time.monotonic() - self._changed_at >= SETTLE_S
                if busy is None and settled:
                    break
                if busy is not None and busy != said:
                    print(f"[butler] {pending} wait for a restart; it "
                          f"happens by itself once nothing is running "
                          f"(now: {busy})", flush=True)
                    said = busy
                await asyncio.sleep(POLL_S)
            print(f"[butler] AUTO-RESTART to apply {pending}: nothing is "
                  f"running", flush=True)
            await self._restart()
        except Exception as e:                            # noqa: BLE001
            # Loud, never fatal: the daemon keeps serving the old values, the
            # bar keeps naming them, and the next change re-arms (D-1).
            print(f"[butler] automatic restart failed: {e!r}", flush=True)
