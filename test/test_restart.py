"""POST /api/restart: the re-exec restart, and the one rule it hangs on.

The whole daemon reads its restart-flagged settings (brain_mode, asr_engine,
...) ONCE at boot, so changing one on the settings page does nothing until a
stop/start. This endpoint is that stop/start without leaving the room: it
re-execs the process with its original argv.

`os.execv` cannot be exercised in-process -- it would replace the pytest
process -- so it is monkeypatched to a sentinel here and the LIVE proof (same
port back, settings re-read, exactly one kokoro sidecar, no leak) is a
scratch-daemon run recorded in the session handoff. What IS testable and what
these tests pin:

  * the route is registered and acks with a 200 BEFORE anything execs, so the
    page gets its answer and its auto-reconnect brings it back;
  * children are stopped BEFORE the exec. This is the load-bearing rule -- the
    exec keeps the Job Object handle, so a sidecar not stopped first survives
    into the new image and a second one is spawned beside it (butlerd
    `_restart_cleanup`). Asserted by ORDER against real recorders, because the
    all-fakes fixture has no close/dispose to observe (D-18: the assertion is
    on the caller, `do_restart`, not on a fake that happens to look right).
"""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from daemon import butlerd


class _RecordingHub:
    """A hub stand-in whose tts.close and brain.dispose append to one log, so
    `do_restart`'s ORDER against os.execv is observable. Real recorders rather
    than the fixture's fakes, which expose neither method (test/AGENTS.md)."""

    def __init__(self, log):
        outer = self

        class _Tts:
            def close(self):
                outer.log.append("tts.close")

        class _Brain:
            async def dispose(self):
                outer.log.append("brain.dispose")

        self.log = log
        self.tts = _Tts()
        self.brain = _Brain()


@pytest.mark.asyncio
async def test_cleanup_runs_before_exec(monkeypatch):
    """The sidecar/child stop happens BEFORE os.execv, never after.

    Red first: `do_restart` and `_restart_cleanup` do not exist yet, so this
    fails at import/attribute lookup until the handler is written. Once they
    exist, the mutation that matters is reordering exec ahead of cleanup --
    which lands "execv" first in the log and fails this exactly.
    """
    log = []
    monkeypatch.setattr(butlerd.os, "execv",
                        lambda *_a: log.append("execv"))
    await butlerd.do_restart(_RecordingHub(log))

    assert log[-1] == "execv", f"execv did not run last: {log}"
    assert "tts.close" in log, f"the sidecar was never stopped: {log}"
    assert log.index("tts.close") < log.index("execv"), (
        f"the kokoro sidecar must be stopped BEFORE the exec, or it leaks "
        f"across the restart (butlerd._restart_cleanup): {log}")
    assert log.index("brain.dispose") < log.index("execv"), (
        f"a harness child must be disposed before the exec: {log}")


@pytest.mark.asyncio
async def test_restart_endpoint_is_registered_and_acks_before_exec(monkeypatch):
    """POST /api/restart -> 200 {restarting: true}, and it reaches os.execv.

    In-process, NOT against the shared subprocess `daemon` fixture: a real
    POST there would actually restart it mid-suite. os.execv is a sentinel so
    the pytest process survives, and the endpointer is forced off so the
    in-process boot spawns no smart-turn child.
    """
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    reached = []
    monkeypatch.setattr(butlerd.os, "execv",
                        lambda *_a: reached.append("execv"))

    app = butlerd.make_app(butlerd.load_config())
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/restart")
        assert r.status == 200, f"restart returned {r.status}"
        body = await r.json()
        assert body.get("restarting") is True, body
        # The exec is scheduled AFTER the response flushes; give it its beat.
        await asyncio.sleep(0.25)
    assert reached == ["execv"], (
        f"the restart handler did not reach os.execv exactly once: {reached}")
