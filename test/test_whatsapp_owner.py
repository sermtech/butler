"""The daemon owns the WhatsApp sidecar (docs/messaging.md MS-2..MS-6, phase M2).

AC-500 (lifecycle: started only when enabled, job object at spawn, loopback
port read back, every request carries the per-boot token, a crash restarts
it with backoff and logs its output), AC-501 (records under
`Paths.whatsapp`), AC-503/AC-505 (the pairing routes the settings group
names, and the states they surface).

Against a FAKE sidecar (`test/assets/fake_wa_sidecar.py`) that honours the
real process contract and writes every request it receives into the records
folder, so each assertion reads a CONSEQUENCE on the sidecar's side, never
only a status code (test/AGENTS.md). Nothing here touches WhatsApp.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from daemon import butlerd, childproc
from daemon import whatsapp as whatsapp_mod
from daemon.settings import Settings

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "test" / "assets" / "fake_wa_sidecar.py"


def _requests(records: Path) -> list[dict]:
    f = records / "requests.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text("utf-8").splitlines()]


def _boots(records: Path) -> int:
    f = records / "boots.txt"
    return len(f.read_text("utf-8").split()) if f.exists() else 0


def _alive(pid) -> bool:
    if pid is None:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


async def _until(cond, timeout=15.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def fake_argv(monkeypatch):
    monkeypatch.setattr(whatsapp_mod, "sidecar_argv",
                        lambda: [sys.executable, "-X", "utf8", str(FAKE)])


@pytest.fixture
def state(tmp_path, monkeypatch, fake_argv):
    """A daemon's own records folder, never the owner's (test/AGENTS.md)."""
    (tmp_path / "registry.json").write_text('{"providers": {}}', "utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.delenv("BUTLER_SELF_URL", raising=False)
    return tmp_path


def _write_settings(state: Path, **values):
    values.setdefault("hotkeys_enabled", False)
    (state / "settings.json").write_text(json.dumps(values), "utf-8")


@pytest_asyncio.fixture
async def served(state):
    """make_app on the state folder; yields (client, hub's owner, records)."""
    built = {}
    real_build = butlerd.build_hub

    def build(cfg):
        built["hub"] = real_build(cfg)
        return built["hub"]
    butlerd.build_hub = build
    try:
        app = butlerd.make_app({})
    finally:
        butlerd.build_hub = real_build
    client = TestClient(TestServer(app))
    await client.start_server()
    wa = built["hub"].whatsapp
    try:
        yield client, wa, state / "whatsapp"
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# Off by default (MS-2, MS-5)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_off_by_default_nothing_is_spawned_and_pairing_says_so(served):
    client, wa, records = served
    r = await client.get("/api/whatsapp/pairing")
    doc = await r.json()
    assert r.status == 503, doc
    assert doc["poll"] is False, "an OFF service must not make the page poll"
    assert doc["actions"] == []
    assert "off" in doc["error"].lower()
    assert wa.pid() is None
    assert _boots(records) == 0, "a sidecar started with WhatsApp off"


# --------------------------------------------------------------------------- #
# Started when enabled: token, port, records, config (AC-500, AC-501)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_enabled_at_boot_it_starts_with_the_token_and_pushes_config(
        state):
    _write_settings(state, whatsapp_enabled=True,
                    whatsapp_answer_chats=["15551234567@s.whatsapp.net"],
                    whatsapp_group_trigger="Jarvis")
    built = {}
    real_build = butlerd.build_hub
    butlerd.build_hub = lambda cfg: built.setdefault("hub", real_build(cfg))
    try:
        app = butlerd.make_app({})
    finally:
        butlerd.build_hub = real_build
    async with TestClient(TestServer(app)) as client:
        wa = built["hub"].whatsapp
        await _until(lambda: wa.state == "running", what="running")
        records = state / "whatsapp"
        # AC-501: the records folder the sidecar was given is the data
        # root's, and the fake wrote there.
        assert _boots(records) == 1
        assert REPO not in records.resolve().parents
        reqs = _requests(records)
        assert reqs and reqs[0]["path"] == "/config", reqs
        assert reqs[0]["token_ok"] is True, "the config push had no token"
        assert reqs[0]["body"] == {
            "answer": ["15551234567@s.whatsapp.net"],
            "answer_everyone": False, "trigger": "Jarvis"}
        # The sidecar refuses a caller without the token (the contract the
        # daemon relies on); the daemon's own route gets through.
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{wa.port}/status") as r:
                assert r.status == 401
        r = await client.get("/api/whatsapp/pairing")
        assert r.status == 200, await r.text()
        # Every request the DAEMON made carried the token (the 401 probe
        # above is the test's own).
        assert all(q["token_ok"] for q in _requests(records)
                   if q["path"] != "/status")
        pid = wa.pid()
        assert _alive(pid)
    # make_app's cleanup stopped it.
    assert not _alive(pid), "the sidecar outlived the app's cleanup"


def test_the_token_is_random_per_boot_and_long(tmp_path):
    prefs = Settings(tmp_path / "s.json")
    a = whatsapp_mod.WhatsApp(prefs, tmp_path / "wa")
    b = whatsapp_mod.WhatsApp(prefs, tmp_path / "wa")
    assert a.token != b.token
    assert len(a.token) >= 32


def test_paths_whatsapp_is_inside_the_records(tmp_path, monkeypatch):
    """AC-501: spelled once, in paths.py, under the records."""
    from daemon import paths
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    where = paths.resolve()
    assert where.whatsapp == where.records / "whatsapp"
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("BUTLER_DATA", str(root))
    monkeypatch.delenv("BUTLER_STATE")
    where = paths.resolve()
    assert where.whatsapp == root / ".butler" / "whatsapp"


@pytest.mark.asyncio
async def test_the_child_is_assigned_to_the_job_at_spawn(state, monkeypatch):
    """AC-36, D-24 at this call site: no assign, an orphan after a hard kill."""
    assigned = []
    real = childproc.assign
    monkeypatch.setattr(childproc, "assign",
                        lambda p: (assigned.append(p.pid), real(p)))
    prefs = Settings(state / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, state / "whatsapp")
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", what="running")
        assert assigned == [wa.pid()]
    finally:
        await wa.stop()


# --------------------------------------------------------------------------- #
# The switch starts and stops it; config changes are pushed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_switch_starts_and_stops_it_without_a_restart(served):
    client, wa, records = served
    r = await client.post("/api/settings", json={"whatsapp_enabled": True})
    assert r.status == 200
    # Said at once: the page reading right after the switch is told to poll.
    r = await client.get("/api/whatsapp/pairing")
    if r.status == 503:
        assert (await r.json())["poll"] is True
    await _until(lambda: wa.state == "running", what="running")
    pid = wa.pid()
    assert _alive(pid)
    r = await client.post("/api/settings", json={"whatsapp_enabled": False})
    assert r.status == 200
    await _until(lambda: wa.state == "disabled" and not _alive(pid),
                 what="stopped")
    r = await client.get("/api/whatsapp/pairing")
    assert r.status == 503 and (await r.json())["poll"] is False


@pytest.mark.asyncio
async def test_a_config_setting_change_is_pushed(served):
    client, wa, records = served
    await client.post("/api/settings", json={"whatsapp_enabled": True})
    await _until(lambda: wa.state == "running", what="running")
    before = len(_requests(records))
    r = await client.post("/api/settings", json={"whatsapp_answer_all": True})
    assert r.status == 200
    await _until(lambda: len(_requests(records)) > before, what="a push")
    last = _requests(records)[-1]
    assert last["path"] == "/config" and last["token_ok"]
    assert last["body"]["answer_everyone"] is True
    # A setting the sidecar does not carry is not pushed.
    count = len(_requests(records))
    await client.post("/api/settings", json={"phone_reply": "text"})
    await asyncio.sleep(0.3)
    assert len(_requests(records)) == count


# --------------------------------------------------------------------------- #
# Crash: restart with backoff, logged, capped (AC-500)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_crash_restarts_it_and_logs_the_restart(state, capfd):
    prefs = Settings(state / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, state / "whatsapp", backoff=(0.05,))
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", what="running")
        first = wa.pid()
        subprocess.run(["taskkill", "/F", "/PID", str(first)],
                       capture_output=True)
        await _until(lambda: wa.state == "running" and wa.pid() not in
                     (None, first), what="a restarted sidecar")
        assert wa.restarts == 1
        assert _boots(state / "whatsapp") == 2
        err = capfd.readouterr().err
        assert "[whatsapp] restart 1/" in err
        assert "[whatsapp] fake-wa: listening" in err, (
            "the sidecar's own output was not drained into the log")
        # The config is pushed again to the new process, with the token.
        assert [q["path"] for q in _requests(state / "whatsapp")].count(
            "/config") == 2
    finally:
        await wa.stop()


@pytest.mark.asyncio
async def test_a_sidecar_that_cannot_start_gives_up_and_says_why(
        state, monkeypatch, capfd):
    monkeypatch.setenv("FAKE_WA_DIE_AT_BOOT", "1")
    prefs = Settings(state / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, state / "whatsapp", backoff=(0.01,),
                               max_restarts=2)
    await wa.start()
    try:
        await _until(lambda: wa.state == "failed", what="failed")
        assert _boots(state / "whatsapp") == 3, "not capped at 1 + 2 retries"
        down = wa.unavailable()
        assert down is not None and down.poll is False
        assert "code 3" in down.reason
        assert "dying at boot" in capfd.readouterr().err
        await asyncio.sleep(0.2)
        assert _boots(state / "whatsapp") == 3, "it kept looping"
    finally:
        await wa.stop()


# --------------------------------------------------------------------------- #
# The pairing routes (AC-503, AC-505)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def running(served):
    client, wa, records = served
    await client.post("/api/settings", json={"whatsapp_enabled": True})
    await _until(lambda: wa.state == "running", what="running")
    return client, wa, records


@pytest.mark.asyncio
async def test_pairing_routes_proxy_and_shape_the_document(running):
    client, wa, records = running
    r = await client.get("/api/whatsapp/pairing")
    doc = await r.json()
    assert r.status == 200
    assert doc["status"] == "idle" and doc["poll"] is False
    assert [a["id"] for a in doc["actions"]] == ["link"]
    # The sidecar's words pass through untouched; the page sets them as text.
    assert doc["detail"] == "<b>detail for idle</b>"

    r = await client.post("/api/whatsapp/pairing")
    doc = await r.json()
    assert doc["status"] == "awaiting_scan" and doc["poll"] is True
    assert doc["qr"].startswith("data:image/png;base64,")
    assert doc["qr_count"] == 1
    assert [a["id"] for a in doc["actions"]] == ["disconnect"]

    r = await client.delete("/api/whatsapp/pairing")
    doc = await r.json()
    assert doc["status"] == "idle" and doc["qr"] is None
    sent = [(q["method"], q["path"]) for q in _requests(records)]
    assert ("POST", "/pairing") in sent and ("DELETE", "/pairing") in sent


@pytest.mark.asyncio
async def test_unlink_without_confirmation_is_refused_and_never_sent(running):
    client, wa, records = running
    r = await client.delete("/api/whatsapp/pairing?unlink=true")
    assert r.status == 409
    r = await client.delete("/api/whatsapp/pairing", json={"unlink": True})
    assert r.status == 409
    assert not any("unlink" in q["path"] for q in _requests(records)), (
        "an unconfirmed unlink reached the sidecar")
    r = await client.delete("/api/whatsapp/pairing?unlink=true",
                            json={"confirm": True})
    assert r.status == 200
    assert any(q["path"] == "/pairing?unlink=true" and q["token_ok"]
               for q in _requests(records))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,actions", [
    ("replaced", ["link", "unlink"]),      # 440: terminal, said (MS-6)
    ("logged_out", ["link"]),              # 401: link again
    ("paired", ["disconnect", "unlink"]),
])
async def test_terminal_and_linked_states_reach_the_page(state, monkeypatch,
                                                         status, actions):
    monkeypatch.setenv("FAKE_WA_STATUS", status)
    prefs = Settings(state / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, state / "whatsapp")
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", what="running")
        code, view = await wa.request("GET", "/pairing")
        doc = whatsapp_mod.page_doc(view)
        assert doc["status"] == status
        assert [a["id"] for a in doc["actions"]] == actions
        assert doc["poll"] is False, "a terminal state must not poll"
    finally:
        await wa.stop()


def test_an_unknown_pairing_state_is_an_error_not_a_blank():
    with pytest.raises(whatsapp_mod.SidecarError):
        whatsapp_mod.page_doc({"state": {"status": "teleported"},
                               "view": {}})


@pytest.mark.asyncio
async def test_a_dead_sidecar_is_a_503_that_polls(running):
    client, wa, records = running
    wa.port = 1       # nothing listens there: the service is not answering
    r = await client.get("/api/whatsapp/pairing")
    doc = await r.json()
    assert r.status == 503 and doc["poll"] is True


# --------------------------------------------------------------------------- #
# Restart and shutdown stop it (`_restart_cleanup`, D-24)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_restart_cleanup_stops_the_sidecar(running):
    client, wa, records = running
    pid = wa.pid()
    assert _alive(pid)

    class _Hub:
        tts = brain = None
        whatsapp = wa
    await butlerd._restart_cleanup(_Hub())
    assert not _alive(pid)
    assert wa.state == "disabled"
