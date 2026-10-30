"""A changed setting takes effect with no owner restart (AC-713..AC-716).

Five settings are read once, at construction (`butlerd.build_hub`). Two of
them build something cheap -- a recognizer adapter over a GGUF the engine
already holds (`asr_latency`), and the end-of-turn callable (`endpointer`) --
so they are SWAPPED live: the new engine is built and warmed off the turn
path and handed to the hub in one assignment (AC-713). The other three load a
model or rebuild the brain composite, so a change to one RESTARTS the daemon
by itself, but only once nothing is running (AC-715). The page is told what
the running daemon has not applied yet by the daemon, never by a baseline of
its own (AC-716).

Every assertion that matters is on the CALLER (D-18): the next capture's
pipeline, `os.execv` reached through the real route, the frame on the wire.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import StubWs, attach_session, make_hub               # noqa: E402
from test_parallel_agents import _brain                              # noqa: E402
from daemon import apply_settings as apply_mod                       # noqa: E402
from daemon import butlerd, ports                                    # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                         # noqa: E402
from daemon.ports import TurnContext                                 # noqa: E402
from daemon.settings import BY_KEY, Settings                         # noqa: E402

DEADLINE_S = 5.0


async def until(cond, what: str, deadline_s: float = DEADLINE_S):
    """Poll `cond` on a deadline whose failure names what did not happen."""
    end = time.monotonic() + deadline_s
    while not cond():
        if time.monotonic() > end:
            raise AssertionError(f"within {deadline_s} s: {what}")
        await asyncio.sleep(0.01)


class RecordingAsr(FakeAsr):
    """A fake recognizer that records every adapter a capture OPENED."""

    opened: list = []

    def open(self, cfg, on_partial, on_final):
        RecordingAsr.opened.append(self)
        return super().open(cfg, on_partial, on_final)


class GatedAsr(FakeAsr):
    """A fake recognizer whose warm-up waits until the test lets it finish."""

    gate: asyncio.Event | None = None
    warming = 0

    async def warm(self) -> None:
        GatedAsr.warming += 1
        if GatedAsr.gate is not None:
            await GatedAsr.gate.wait()
        await super().warm()


class FakeEndpointer:
    """Stands in for `SmartTurnEndpointer`: no child process, warm recorded."""

    fail = False
    built: list = []

    def __init__(self):
        self.warmed = False
        FakeEndpointer.built.append(self)

    async def warm(self) -> float:
        if FakeEndpointer.fail:
            raise RuntimeError("the end-of-turn model would not load")
        self.warmed = True
        return 1.0

    async def __call__(self, pcm: bytes) -> float:
        return 0.0


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A records folder of this test's own, and fast applier timings.

    The timings are shortened HERE only beside
    `test_the_shipped_timings_are_the_measured_ones`, which pins the values
    that ship (test/AGENTS.md)."""
    folder = tmp_path / "state"
    folder.mkdir()
    monkeypatch.setenv("BUTLER_STATE", str(folder))
    monkeypatch.setattr(apply_mod, "POLL_S", 0.01)
    monkeypatch.setattr(apply_mod, "SETTLE_S", 0.05)
    monkeypatch.setitem(ports.ASRS, "recording", RecordingAsr)
    monkeypatch.setitem(ports.ASRS, "gated", GatedAsr)
    monkeypatch.setitem(butlerd.ENDPOINTERS, "smartturn", FakeEndpointer)
    RecordingAsr.opened = []
    GatedAsr.gate, GatedAsr.warming = None, 0
    FakeEndpointer.fail, FakeEndpointer.built = False, []
    return folder


@pytest.fixture
def execs(monkeypatch):
    """Every `os.execv` the daemon reached, instead of replacing pytest."""
    seen = []
    monkeypatch.setattr(butlerd.os, "execv", lambda *a: seen.append(a))
    return seen


def _hub(state, asr="recording", **values):
    if values:
        Settings(state / "settings.json").update(values)
    return butlerd.build_hub({"adapters": {"asr": asr, "tts": "fake",
                                           "brain": "fake"},
                              "asr": {"model": "parakeet-unified-en-0.6b"}})


async def _capture(hub):
    """Start one capture the way a won wake does, and stop its task."""
    s = attach_session(hub)
    await hub._start_capture(s)
    s.task.cancel()
    return s


# --------------------------------------------------------------------------- #
# Which settings still need a restart
# --------------------------------------------------------------------------- #
def test_only_the_expensive_engine_settings_still_need_a_restart():
    """AC-713, AC-715. A model on the GPU or a rebuilt brain restarts;
    a recognizer over the same GGUF and the end-of-turn callable do not.
    Asserted both ways: a stale flag tells the owner to wait for a restart
    the daemon no longer needs (AC-7b pointing the other way)."""
    for key in ("asr_model", "asr_backend", "brain_mode"):
        assert BY_KEY[key].restart, f"{key} lost its restart flag"
    for key in ("asr_latency", "endpointer"):
        assert not BY_KEY[key].restart, (
            f"{key} is swapped live (AC-713) but still says it needs a "
            f"restart")


def test_the_shipped_timings_are_the_measured_ones():
    """The poll and the settle the fixture shortens, as they ship."""
    assert apply_mod.POLL_S == 1.0
    assert apply_mod.SETTLE_S == 5.0


# --------------------------------------------------------------------------- #
# AC-714: the end-of-turn setting is READ at all
# --------------------------------------------------------------------------- #
def test_the_end_of_turn_setting_is_read_at_boot(state, monkeypatch):
    """AC-714 (D-36's shape). `build_endpointer` read only the config file
    and `BUTLER_ENDPOINTER`; the setting saved, reported success and did
    nothing. The setting now decides, and the test override still wins."""
    monkeypatch.delenv("BUTLER_ENDPOINTER", raising=False)
    assert _hub(state, endpointer="none").endpointer is None, (
        "the end-of-turn setting said none and boot wired one anyway")
    hub = _hub(state, endpointer="smartturn")
    assert isinstance(hub.endpointer, FakeEndpointer), hub.endpointer

    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    assert _hub(state, endpointer="smartturn").endpointer is None, (
        "BUTLER_ENDPOINTER no longer pins the endpointer for a test")


def test_a_config_key_for_the_endpointer_is_named_and_loses(
        state, monkeypatch, capsys):
    """AC-714. One place names the value (D-35): a key left in the config
    file is said out loud and the SETTING wins, never a refusal to boot."""
    monkeypatch.delenv("BUTLER_ENDPOINTER", raising=False)
    Settings(state / "settings.json").update({"endpointer": "none"})
    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "fake",
                                          "endpointer": "smartturn"}})
    assert hub.endpointer is None, "the config file beat the setting"
    assert "endpointer" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# AC-713: cheap engines are swapped live
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_latency_change_reaches_the_next_capture_with_no_restart(
        state, execs, monkeypatch):
    """AC-713. The next capture OPENS a recognizer built with the new
    latency, over the model and backend this process booted with, and
    nothing restarts."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    hub = _hub(state, asr_backend="cpu")
    booted = hub.asr
    hub.applier.start()
    hub.settings.update({"asr_latency": "low"})
    await until(lambda: hub.asr is not booted, "the recognizer was swapped")

    await _capture(hub)
    used = RecordingAsr.opened[-1]
    assert used is hub.asr and used is not booted, (
        "the next capture opened the recognizer the daemon booted with")
    assert used.engine_options == {"model": "parakeet-unified-en-0.6b",
                                   "backend": "cpu", "latency": "low"}, (
        used.engine_options)
    await asyncio.sleep(0.2)
    assert execs == [], "a live setting restarted the daemon"


@pytest.mark.asyncio
async def test_the_new_recognizer_is_warmed_before_it_is_swapped_in(
        state, monkeypatch):
    """AC-713. Swapped only after its warm-up returned: a cold engine
    handed to the hub pays its load on the owner's next sentence (D-10)."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    GatedAsr.gate = asyncio.Event()
    hub = _hub(state, asr="gated")
    booted = hub.asr
    hub.applier.start()
    hub.settings.update({"asr_latency": "low"})
    await until(lambda: GatedAsr.warming == 1, "the new recognizer warmed")
    await asyncio.sleep(0.05)
    assert hub.asr is booted, "a recognizer still warming was swapped in"
    GatedAsr.gate.set()
    await until(lambda: hub.asr is not booted, "the warm one was swapped in")
    assert hub.asr.engine_options["latency"] == "low"


@pytest.mark.asyncio
async def test_an_endpointer_change_reaches_the_next_capture(
        state, execs, monkeypatch):
    """AC-713, AC-714. On, the next capture's pipeline holds the warmed
    new endpointer; off, the next one holds none."""
    monkeypatch.delenv("BUTLER_ENDPOINTER", raising=False)
    hub = _hub(state, endpointer="none", turn_mode="smart")
    assert hub.endpointer is None
    hub.applier.start()

    hub.settings.update({"endpointer": "smartturn"})
    await until(lambda: hub.endpointer is not None, "an endpointer was wired")
    assert hub.endpointer.warmed, "an endpointer was wired before its warm-up"
    s = await _capture(hub)
    assert s.pipeline.endpointer is hub.endpointer, (
        "the next capture did not get the new endpointer")

    hub.settings.update({"endpointer": "none"})
    await until(lambda: hub.endpointer is None, "the endpointer was dropped")
    s = await _capture(hub)
    assert s.pipeline.endpointer is None
    assert execs == [], "a live setting restarted the daemon"


@pytest.mark.asyncio
async def test_a_failed_warm_keeps_the_running_engine_and_says_so(
        state, monkeypatch, capsys):
    """AC-713, D-1. A new engine that will not warm is never swapped in,
    the failure is loud, and the next change still applies."""
    monkeypatch.delenv("BUTLER_ENDPOINTER", raising=False)
    hub = _hub(state, endpointer="none")
    hub.applier.start()
    FakeEndpointer.fail = True
    hub.settings.update({"endpointer": "smartturn"})
    await until(lambda: "would not load" in capsys.readouterr().out,
                "the failed warm-up was said")
    assert hub.endpointer is None, "an engine that failed to warm was wired"

    FakeEndpointer.fail = False
    hub.settings.update({"endpointer": "none"})
    hub.settings.update({"endpointer": "smartturn"})
    await until(lambda: hub.endpointer is not None,
                "a later change applied after a failure")


# --------------------------------------------------------------------------- #
# AC-715: expensive settings restart the daemon by itself, once idle
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_restart_setting_waits_for_the_turn_then_restarts(
        state, execs, monkeypatch):
    """AC-715. Never in the middle of a turn: the restart waits while a
    turn is in flight and happens, once, as soon as it ends."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    hub = _hub(state)
    hub.applier.start()
    s = attach_session(hub)
    turn = hub._begin_turn(s)
    hub.settings.update({"brain_mode": "harness"})
    await asyncio.sleep(0.3)
    assert execs == [], "the daemon restarted in the middle of a turn"

    s._rearm(turn)
    await until(lambda: len(execs) == 1, "the daemon restarted once idle")
    await asyncio.sleep(0.1)
    assert len(execs) == 1, f"restarted {len(execs)} times"


@pytest.mark.asyncio
async def test_putting_the_value_back_cancels_the_restart(
        state, execs, monkeypatch):
    """AC-715. The restart applies what the daemon has NOT applied; a value
    put back before it settles owes nothing."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.setattr(apply_mod, "SETTLE_S", 0.3)
    hub = _hub(state)
    hub.applier.start()
    hub.settings.update({"asr_model": "nemotron-3.5-asr-streaming-0.6b"})
    # Long enough for the waiter to start and see the change pending,
    # shorter than the settle: the put-back must reach a RUNNING waiter.
    await asyncio.sleep(0.1)
    hub.settings.update({"asr_model": "parakeet-unified-en-0.6b"})
    await asyncio.sleep(0.6)
    assert execs == [], "a value put back still restarted the daemon"


@pytest.mark.asyncio
async def test_the_running_app_restarts_itself_through_the_settings_route(
        state, execs, monkeypatch):
    """AC-715 on the caller: `make_app` starts the applier, and a change
    POSTed to the real route reaches `os.execv` with nobody pressing
    Restart."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    app = butlerd.make_app({})
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/settings", json={"brain_mode": "harness"})
        assert r.status == 200
        await until(lambda: len(execs) == 1,
                    "the app restarted itself to apply brain_mode")


@pytest.mark.asyncio
async def test_a_coding_agent_mid_prompt_holds_the_restart(
        tmp_path, fake_openai):
    """AC-715. "Is anything running" asks the agent's own bookkeeping too:
    a child in the middle of a prompt holds the restart, and a child held
    idle between turns (AC-59) does not, or the restart would wait forever
    on a conversation the owner merely left open."""
    hub = make_hub(tmp_path)
    brain, agents, _chats, _ = _brain(tmp_path, fake_openai)
    hub.brain = brain
    assert hub.busy() is None, hub.busy()

    stream = brain.turn("what does the endpointing module do",
                        TurnContext(turn_id=1, mode="secretary"))
    while not agents.all:
        await asyncio.wait_for(anext(stream), DEADLINE_S)
    assert hub.busy() is not None, (
        "a coding agent in the middle of a prompt reads as idle")

    async def rest():
        return [i async for i in stream]
    await asyncio.wait_for(rest(), DEADLINE_S)
    assert brain.host.live() == 1, "the child was not held after its turn"
    assert hub.busy() is None, (
        f"an agent held idle between turns blocks the restart: {hub.busy()}")
    await brain.dispose()


@pytest.mark.asyncio
async def test_a_whole_clip_decode_or_an_orphaned_turn_holds_the_restart(
        tmp_path):
    """AC-715. The recognizer mid-decode and a turn that outlived its
    socket are running work too."""
    hub = make_hub(tmp_path)
    assert hub.busy() is None
    async with hub._decoding:
        assert hub.busy() is not None, "a whole-clip decode reads as idle"
    task = asyncio.get_running_loop().create_future()
    hub._orphaned["c1"] = (1, task)
    assert hub.busy() is not None, "an orphaned turn reads as idle"


# --------------------------------------------------------------------------- #
# AC-716: the daemon says what it has not applied
# --------------------------------------------------------------------------- #
def test_the_store_says_what_it_has_not_applied(tmp_path):
    """AC-716. `pending` is every restart-flagged value that differs from
    what this process booted with: a live setting never appears, and a
    value put back leaves."""
    prefs = Settings(tmp_path / "settings.json")
    assert prefs.describe()["pending"] == []
    prefs.update({"asr_latency": "low", "brain_mode": "harness"})
    assert prefs.describe()["pending"] == ["brain_mode"]
    prefs.update({"brain_mode": "endpoint"})
    assert prefs.describe()["pending"] == []


@pytest.mark.asyncio
async def test_the_route_and_the_frame_carry_what_is_pending(
        state, execs, monkeypatch):
    """AC-716 on the wire: the POST answer, the GET and the `settings`
    broadcast every other page receives all name the waiting key, so a
    change made on the phone shows on the desk's bar (AC-86's known limit,
    closed)."""
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.setattr(apply_mod, "SETTLE_S", 30.0)
    app = butlerd.make_app({})
    async with TestClient(TestServer(app)) as client:
        other = attach_session(app["hub"], "phone")
        r = await client.post("/api/settings", json={"brain_mode": "harness"})
        assert (await r.json())["pending"] == ["brain_mode"]
        got = await (await client.get("/api/settings")).json()
        assert got["pending"] == ["brain_mode"]
        frames = [json.loads(m) for m in other.ws.sent if isinstance(m, str)]
        sent = [f for f in frames if f["type"] == "settings"]
        assert sent and sent[-1]["pending"] == ["brain_mode"], sent
    assert execs == [], "restarted before the change settled"
