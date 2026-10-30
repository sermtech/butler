"""Three listeners, one trust function, a local-only shutdown, one launcher.

docs/desktop-app.md phase D1: AC-540 (listeners), AC-541 (trust comes from
the LISTENER a request arrived on, never from its peer address alone),
AC-542 (one launcher, `scripts/launch.py`), AC-543 (`POST /api/shutdown`).

AC-541 is the one that is NOT recoverable: `tailscale serve` forwards every
phone from 127.0.0.1 onto the PROXIED listener, so a peer-address check would
make every phone the desk and hand it the agent routes. So every test here
uses REAL sockets on 127.0.0.1 port 0 (never 8140-8142) and reads the
listener off the accepting socket exactly as the daemon does.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from conftest import DAEMON, free_port                       # noqa: E402
from daemon import butlerd                                   # noqa: E402
from daemon import hub as hub_mod                            # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import launch                                                # noqa: E402

AGENT_ROUTES = ("/api/tasks/agent", "/api/memory/search",
                "/api/memory/write", "/api/shutdown")


def _hub(tmp_path, monkeypatch):
    """A hub on the fakes with a real memory store and library under
    tmp_path, so a route that is let through has something to answer with."""
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.delenv("BUTLER_SELF_URL", raising=False)
    return butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                           "brain": "endpoint"}})


#: The Tailscale login and the ts.net Host `_Three.who` presents on PROXIED
#: (`tailscale serve` keeps the phone's Host; RA-3 accepts only that suffix).
OWNER_LOGIN = "owner@test.example"
TS_HOST = "desk.tail1234.ts.net"


class _Three:
    """The daemon's own `serve` over three real sockets: MAIN on `main_host`,
    LOCAL and PROXIED on 127.0.0.1, every port chosen by the OS."""

    def __init__(self, hub, monkeypatch, main_host="127.0.0.1"):
        self.cleanups: list[str] = []

        async def cleanup(h):
            assert h is hub
            self.cleanups.append("cleanup")
        monkeypatch.setattr(butlerd, "_restart_cleanup", cleanup)
        monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
        self.app = butlerd.make_app({})
        self.socks = {
            hub_mod.MAIN: butlerd.listen_socket(main_host, 0),
            hub_mod.LOCAL: butlerd.listen_socket("127.0.0.1", 0),
            hub_mod.PROXIED: butlerd.listen_socket("127.0.0.1", 0),
        }
        self.main_host = main_host
        self.runner = None
        self.hub = hub
        self._cookie = None

    def url(self, kind: str, path: str) -> str:
        host = self.main_host if kind == hub_mod.MAIN else "127.0.0.1"
        return f"http://{host}:{self.socks[kind].getsockname()[1]}{path}"

    async def __aenter__(self):
        self.runner = await butlerd.serve(self.app, None, self.socks)
        # Butler's own local callers send the RV-1 header (AC-586); the
        # listener rule must refuse PROXIED and foreign peers even so.
        self.http = aiohttp.ClientSession(
            headers={hub_mod.LOCAL_HEADER: "1"})
        return self

    async def __aexit__(self, *exc):
        await self.http.close()
        await self.runner.cleanup()

    async def who(self, kind: str) -> dict:
        """The headers that get a request PAST the gate
        (docs/remote-access.md RA-2) without making it local: on PROXIED
        the owner's Tailscale login, on MAIN from another address a paired
        device. What a route then decides is what these tests are about."""
        if kind == hub_mod.PROXIED:
            if not self.hub.settings.get("tailscale_owners"):
                self.hub.settings.update({"tailscale_owners": OWNER_LOGIN})
            return {"Tailscale-User-Login": OWNER_LOGIN, "Host": TS_HOST}
        if kind == hub_mod.MAIN and self.main_host != "127.0.0.1":
            if self._cookie is None:
                async with self.http.post(
                        self.url(hub_mod.LOCAL, "/api/devices")) as r:
                    code = (await r.json())["code"]
                async with self.http.post(
                        self.url(hub_mod.MAIN, "/api/pair"),
                        json={"code": code, "name": "test"}) as r:
                    assert r.status == 200, await r.text()
                    self._cookie = r.cookies["butler_device"].value
            return {"Cookie": f"butler_device={self._cookie}"}
        return {}


async def _post(three, kind, path, body=None):
    async with three.http.post(three.url(kind, path),
                               headers=await three.who(kind),
                               json=body if body is not None else {}) as r:
        return r.status, await r.json()


async def _session_on(three, hub, kind, device):
    """Connect a page over /ws on listener `kind` and return its session."""
    ws = await three.http.ws_connect(three.url(kind, "/ws"),
                                     headers=await three.who(kind))
    await ws.send_json({"type": "hello", "proto": 1, "device_id": device,
                        "caps": {"wake": True, "tts": True}})
    await ws.receive_json()
    [s] = [x for x in hub.sessions if x.device_id == device]
    return ws, s


# --------------------------------------------------------------------------- #
# AC-540: the listeners
# --------------------------------------------------------------------------- #
def test_local_and_proxied_bind_loopback_only_even_when_main_is_everywhere():
    """AC-540. LOCAL and PROXIED are 127.0.0.1 whatever `--host` says; MAIN
    keeps today's host."""
    a, b, c = free_port(), free_port(), free_port()
    socks = butlerd.listener_sockets("0.0.0.0", a, b, c)
    try:
        assert set(socks) == {hub_mod.MAIN, hub_mod.LOCAL, hub_mod.PROXIED}
        assert socks[hub_mod.MAIN].getsockname() == ("0.0.0.0", a)
        assert socks[hub_mod.LOCAL].getsockname() == ("127.0.0.1", b)
        assert socks[hub_mod.PROXIED].getsockname() == ("127.0.0.1", c)
    finally:
        for s in socks.values():
            s.close()


def test_a_port_of_zero_removes_that_listener():
    """AC-540: 0 turns LOCAL or PROXIED off (MAIN is always there)."""
    socks = butlerd.listener_sockets("127.0.0.1", free_port(), 0, 0)
    try:
        assert set(socks) == {hub_mod.MAIN}
    finally:
        for s in socks.values():
            s.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX TIME_WAIT semantics")
def test_a_restart_rebinds_its_port_over_its_own_time_wait():
    """The in-page Restart re-execs the daemon in place; the new image binds
    the SAME port while the connections the old one just closed sit in
    TIME_WAIT. Without SO_REUSEADDR that bind is EADDRINUSE on macOS, and the
    Mac mini's Butler died there and stayed down (2026-10-27, reproduced on a
    scratch daemon: TIME_WAIT + FIN_WAIT_2 on the port, then Errno 48).

    The sequence is the restart's: listen, accept a client, the SERVER closes
    that connection first (so the TIME_WAIT is on the server's port), close
    the listener, bind the same port again through the same function."""
    port = free_port()
    first = butlerd.listen_socket("127.0.0.1", port)
    first.listen()
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = first.accept()
    conn.close()                      # server side closes first -> TIME_WAIT
    time.sleep(0.2)
    client.close()
    first.close()
    second = butlerd.listen_socket("127.0.0.1", port)
    second.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows reuse semantics")
def test_windows_never_lets_another_socket_share_the_port():
    """On Windows SO_REUSEADDR is NOT the POSIX option: it lets a second
    process bind a port already in use and silently take its traffic (the
    port-8765 hijack in the owner's notes). So the POSIX fix above must never
    reach Windows, where a restart already works (os.execv is spawn-then-exit
    there)."""
    sock = butlerd.listen_socket("127.0.0.1", free_port())
    try:
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
    finally:
        sock.close()


def test_two_listeners_on_one_port_are_refused():
    """The listener is looked up BY PORT, so two on one port would make the
    trust lookup ambiguous: refuse at boot, never guess."""
    p = free_port()
    with pytest.raises(SystemExit, match="port"):
        butlerd.listener_sockets("127.0.0.1", p, p, 0)


@pytest.mark.asyncio
async def test_all_three_listeners_answer_the_same_app(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        for kind in three.socks:
            async with three.http.get(three.url(kind, "/api/health"),
                                      headers=await three.who(kind)) as r:
                assert r.status == 200, kind
                assert (await r.json())["ok"] is True


# --------------------------------------------------------------------------- #
# AC-541: trust comes from the listener
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_proxied_listener_is_never_local_even_from_loopback(
        tmp_path, monkeypatch):
    """AC-541, the defect this phase exists for. The PROXIED request comes
    from 127.0.0.1 -- exactly what `tailscale serve` delivers -- and every
    loopback-only route refuses it, with no CONSEQUENCE: no note, no task,
    no teardown. The same requests on LOCAL are let through."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        for path in AGENT_ROUTES:
            status, body = await _post(three, hub_mod.PROXIED, path,
                                       {"title": "t", "text": "a fact",
                                        "query": "fact", "chat": "c"})
            assert status == 403, (path, body)
        assert hub.memory.notes() == []
        assert hub.library.tasks.tasks() == []
        await asyncio.sleep(0.3)                    # past the shutdown delay
        assert three.cleanups == []
        assert not three.app[butlerd.SHUTDOWN].is_set()

        status, body = await _post(three, hub_mod.LOCAL, "/api/memory/write",
                                   {"title": "Ana", "text": "Ana is ten."})
        assert status == 200, body
        status, body = await _post(three, hub_mod.LOCAL, "/api/memory/search",
                                   {"query": "Ana"})
        assert status == 200 and body["hits"], body
        # Past the gate: refused for its BODY (no conversation), not its
        # listener.
        status, body = await _post(three, hub_mod.LOCAL, "/api/tasks/agent",
                                   {"text": "x", "chat": "chat-nobody"})
        assert status not in (403, 200), body


@pytest.mark.asyncio
async def test_the_desk_is_a_page_on_local_or_on_main_from_loopback_never_proxied(
        tmp_path, monkeypatch):
    """AC-541 for the socket: THE DESK (AC-470) is decided by the listener a
    page's WebSocket arrived on. A phone through `tailscale serve` (PROXIED,
    peer 127.0.0.1) is never the desk and never hears an announcement."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        wp, phone = await _session_on(three, hub, hub_mod.PROXIED, "phone")
        wl, app_window = await _session_on(three, hub, hub_mod.LOCAL, "app")
        wm, tab = await _session_on(three, hub, hub_mod.MAIN, "tab")
        try:
            assert phone.peer == "127.0.0.1"         # the trap's precondition
            assert not phone.is_desk
            assert app_window.is_desk
            assert tab.is_desk                       # today's behaviour, kept
        finally:
            for ws in (wp, wl, wm):
                await ws.close()


def _lan_address() -> str | None:
    """A non-loopback address of THIS machine, so a real socket can arrive on
    MAIN from a peer that is not loopback. No packet is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") else ip


@pytest.mark.asyncio
async def test_main_from_another_address_is_not_local(tmp_path, monkeypatch):
    """AC-541 / AC-543 on MAIN: a peer that is not loopback is refused, as
    today. A real socket bound to this machine's LAN address."""
    ip = _lan_address()
    if ip is None:
        pytest.skip("this machine has no non-loopback IPv4 address")
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        for path in AGENT_ROUTES:
            status, _ = await _post(three, hub_mod.MAIN, path, {"chat": "c"})
            assert status == 403, path
        ws, s = await _session_on(three, hub, hub_mod.MAIN, "lan")
        try:
            assert s.peer == ip and not s.is_desk
        finally:
            await ws.close()
        await asyncio.sleep(0.3)
        assert three.cleanups == []


def test_the_trust_core_by_listener_and_peer():
    """The one function every trust decision reaches (DA-6)."""
    f = hub_mod._is_local
    assert f(hub_mod.LOCAL, "127.0.0.1")
    assert f(hub_mod.MAIN, "127.0.0.1") and f(hub_mod.MAIN, "::1")
    assert not f(hub_mod.MAIN, "100.64.0.7")
    assert not f(hub_mod.PROXIED, "127.0.0.1")
    assert not f(hub_mod.PROXIED, "::1")
    with pytest.raises(KeyError):
        f("somewhere", "127.0.0.1")


def _names(tree: ast.AST, ident: str):
    """(enclosing function or '<module>', line) for every use of `ident`."""
    found = []

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            here = where
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
            if (isinstance(child, ast.Name) and child.id == ident) or (
                    isinstance(child, ast.Attribute) and child.attr == ident):
                found.append(here)
            walk(child, here)
    walk(tree, "<module>")
    return found


def test_no_trust_check_reads_LOOPBACK_or_the_peer_outside_the_one_function():
    """AC-541's source test. `LOOPBACK` is read in exactly two places: the
    trust core, and `build_hub`'s check that `BUTLER_SELF_URL` names this
    machine (a configured URL, not a request). A request's `.remote` is read
    only where the trust function and the socket handler take it, and by
    the one gate (`access.decide`, docs/remote-access.md RA-1) and its
    pairing rate limit's key (`access._peer`, RA-8)."""
    loopback, remote = {}, {}
    for path in sorted((REPO / "daemon").rglob("*.py")):
        rel = path.relative_to(REPO / "daemon").as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for where in _names(tree, "LOOPBACK"):
            loopback.setdefault((rel, where), 0)
            loopback[(rel, where)] += 1
        for where in _names(tree, "remote"):
            remote.setdefault((rel, where), 0)
            remote[(rel, where)] += 1
    assert set(loopback) == {("hub.py", "<module>"), ("hub.py", "_is_local"),
                             ("butlerd.py", "build_hub")}, loopback
    assert loopback[("hub.py", "<module>")] == 1          # the definition
    assert set(remote) == {("hub.py", "is_local"), ("hub.py", "handle"),
                           ("access.py", "decide"),
                           ("access.py", "_peer")}, \
        remote


# --------------------------------------------------------------------------- #
# AC-543: POST /api/shutdown
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_shutdown_on_local_tears_down_then_stops(tmp_path, monkeypatch):
    """AC-543: acked first, then the existing teardown (children, sidecars,
    desk agent), THEN the serve loop's stop event -- never the other way."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        stop = three.app[butlerd.SHUTDOWN]
        order = three.cleanups
        real_set = stop.set

        def set_():
            order.append("stop")
            real_set()
        monkeypatch.setattr(stop, "set", set_)
        status, body = await _post(three, hub_mod.LOCAL, "/api/shutdown")
        assert status == 200 and body["stopping"] is True
        await asyncio.wait_for(stop.wait(), 5)
        assert order == ["cleanup", "stop"]


def _scratch_env(tmp_path) -> dict:
    state = tmp_path / "state"
    state.mkdir()
    # Scratch-daemon rule: no system-wide keyboard hook on the real desk.
    (state / "settings.json").write_text(
        json.dumps({"hotkeys_enabled": False,
                    "tailscale_owners": OWNER_LOGIN}), encoding="utf-8")
    return {**os.environ, "BUTLER_STATE": str(state),
            "BUTLER_ENDPOINTER": "none", "BUTLER_TEST_STUB_STT": "1",
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _http(method: str, url: str, headers: dict | None = None) -> int:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method=method, data=b"{}" if
                                 method == "POST" else None,
                                 headers={"Content-Type": "application/json",
                                          hub_mod.LOCAL_HEADER: "1",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.mark.asyncio
async def test_a_real_daemon_refuses_shutdown_on_proxied_and_exits_on_local(
        tmp_path):
    """AC-543 end to end: a scratch butlerd on three free ports. PROXIED
    answers 403 and the daemon stays up; LOCAL answers 200 and the PROCESS
    exits 0 on its own -- inside the app's 15 s (DA-11) even with a page
    still connected, which held it 31.9 s before `_on_shutdown`."""
    main, local, proxied = free_port(), free_port(), free_port()
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(DAEMON), "--no-tls",
         "--host", "127.0.0.1", "--port", str(main),
         "--local-port", str(local), "--proxy-port", str(proxied)],
        cwd=str(REPO), env=_scratch_env(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 60
        while _try_health(local) is False:
            assert proc.poll() is None, proc.stdout.read()
            assert time.monotonic() < deadline, "never healthy on LOCAL"
            time.sleep(0.2)
        assert _http("POST", f"http://127.0.0.1:{proxied}/api/shutdown",
                     {"Tailscale-User-Login": OWNER_LOGIN,
                      "Host": TS_HOST}) == 403
        time.sleep(0.5)
        assert proc.poll() is None, "a PROXIED shutdown stopped the daemon"
        async with aiohttp.ClientSession(
                headers={hub_mod.LOCAL_HEADER: "1"}) as http:
            ws = await http.ws_connect(f"http://127.0.0.1:{main}/ws")
            await ws.send_json({"type": "hello", "proto": 1,
                                "device_id": "page",
                                "caps": {"wake": False, "tts": False}})
            await ws.receive_json()
            asked = time.monotonic()
            async with http.post(f"http://127.0.0.1:{local}/api/shutdown",
                                 json={}) as r:
                assert r.status == 200
            while proc.poll() is None and time.monotonic() - asked < 15:
                await asyncio.sleep(0.1)
            assert proc.poll() == 0, f"still up {time.monotonic() - asked}s"
            await ws.close()
        out = proc.stdout.read()
        assert "SHUTDOWN" in out, out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def _try_health(port: int) -> bool:
    try:
        return _http("GET", f"http://127.0.0.1:{port}/api/health") == 200
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# AC-542: one launcher
# --------------------------------------------------------------------------- #
def test_start_bat_is_a_thin_call_to_the_launcher():
    text = (REPO / "start.bat").read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().upper().startswith("REM")]
    assert any("scripts\\launch.py" in ln for ln in code), code
    for gone in ("netstat", "make-cert", "daemon.paths", "butlerd"):
        assert not any(gone in ln for ln in code), gone


def test_the_daemon_is_started_with_tls_and_both_new_listeners():
    """AC-540's "by default": the launcher is what both start.bat and the
    app run, so it carries the ports."""
    argv = launch.daemon_argv()
    assert argv[:3] == [sys.executable, "-u", "-X"] and argv[3] == "utf8"
    assert Path(argv[4]) == REPO / "daemon" / "butlerd.py"
    assert argv[5:] == ["--tls", "--local-port", "8141",
                        "--proxy-port", "8142"]


def test_a_missing_data_root_is_refused_with_the_migrate_hints(tmp_path,
                                                               capsys):
    data = tmp_path / "nowhere"
    rc = launch.main([], env={"BUTLER_DATA": str(data)})
    out = capsys.readouterr().out
    assert rc == 1
    assert f'scripts\\migrate-data-root.py --data "{data}" --dry-run' in out
    assert f'scripts\\migrate-data-root.py --data "{data}"\n' in out
    assert "missing or not migrated" in out


def test_a_data_root_without_records_is_refused(tmp_path, capsys):
    """Not migrated: starting anyway would look like losing every chat."""
    rc = launch.main([], env={"BUTLER_DATA": str(tmp_path)})
    assert rc == 1
    assert "missing or not migrated" in capsys.readouterr().out


def test_the_data_root_comes_from_env_then_registry_then_the_default(
        monkeypatch):
    monkeypatch.setattr(launch, "_registry_data", lambda: r"C:\Saved")
    assert launch.data_root({"BUTLER_DATA": r"D:\Env"}) == r"D:\Env"
    assert launch.data_root({}) == r"C:\Saved"
    monkeypatch.setattr(launch, "_registry_data", lambda: None)
    assert launch.data_root({}) == r"E:\Butler-Data"


class _Spawned:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return self

    pid = 4242

    def poll(self):
        return None


def _migrated(tmp_path):
    (tmp_path / ".butler" / "ssl").mkdir(parents=True)
    (tmp_path / ".butler" / "ssl" / "cert.pem").write_text("x")
    log = tmp_path / ".butler" / "logs" / "butler.log"
    return log


@pytest.mark.parametrize("detached", [False, True])
def test_launch_appends_the_banner_and_starts_the_daemon(
        tmp_path, monkeypatch, detached):
    """AC-542: appends, never overwrites; cleans the ports first; and
    `--detached` is DETACHED_PROCESS ALONE (with CREATE_NEW_CONSOLE it is
    WinError 87), unbuffered, output into the log."""
    log = _migrated(tmp_path)
    log.parent.mkdir(parents=True)
    log.write_text("the run before\n", encoding="utf-8")
    cleaned, spawned = [], _Spawned()
    monkeypatch.setattr(launch, "clean_ports", cleaned.append)
    monkeypatch.setattr(launch.subprocess, "Popen", spawned)
    monkeypatch.setattr(launch, "_announce", lambda *a: 0)
    env = {"BUTLER_DATA": str(tmp_path)}
    rc = launch.main(["--detached"] if detached else [], env=env)
    assert rc == 0
    text = log.read_text(encoding="utf-8")
    assert text.startswith("the run before\n===== Butler started ")
    assert cleaned == [(8140, 8141, 8142)]
    [(argv, kw)] = spawned.calls
    assert argv == launch.daemon_argv() and kw["cwd"] == str(REPO)
    assert kw["env"]["BUTLER_DATA"] == str(tmp_path)
    assert kw["stderr"] == subprocess.STDOUT
    flags = kw["creationflags"]
    if detached:
        assert flags == subprocess.DETACHED_PROCESS
    else:
        assert flags == subprocess.CREATE_NEW_CONSOLE
        assert kw["startupinfo"].wShowWindow == launch.SW_SHOWMINNOACTIVE
    assert not (flags & subprocess.DETACHED_PROCESS
                and flags & subprocess.CREATE_NEW_CONSOLE)


def test_launch_generates_a_certificate_when_there_is_none(tmp_path,
                                                           monkeypatch):
    (tmp_path / ".butler").mkdir()
    ran = []
    monkeypatch.setattr(launch, "clean_ports", lambda ports: None)
    monkeypatch.setattr(launch.subprocess, "Popen", _Spawned())
    monkeypatch.setattr(launch, "_announce", lambda *a: 0)
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda argv, **kw: ran.append((argv, kw)))
    assert launch.main(["--detached"], env={"BUTLER_DATA": str(tmp_path)}) \
        == 0
    [(argv, kw)] = ran
    assert Path(argv[-1]) == REPO / "scripts" / "make-cert.py"
    assert kw["check"] is True
    assert kw["env"]["BUTLER_DATA"] == str(tmp_path)


def test_clean_ports_kills_only_listeners_on_the_named_ports(monkeypatch):
    netstat = (
        "  TCP    0.0.0.0:8140           0.0.0.0:0              LISTENING       11\r\n"
        "  TCP    127.0.0.1:8141         0.0.0.0:0              LISTENING       12\r\n"
        "  TCP    127.0.0.1:81410        0.0.0.0:0              LISTENING       13\r\n"
        "  TCP    127.0.0.1:50000        127.0.0.1:8142         ESTABLISHED     14\r\n"
        "  TCP    [::]:8140              [::]:0                 LISTENING       11\r\n")
    assert launch.listening_pids(netstat, (8140, 8141, 8142)) == {11, 12}
