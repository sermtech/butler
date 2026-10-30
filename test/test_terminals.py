"""In-page terminals: the closed table, the host handshake, survival.

`docs/chats-and-terminals.md` AC-760, AC-762, AC-763, AC-764 (AC-761 is the
lead's gate wiring and is proved where the gate is). Most tests here run a
REAL terminal host (`daemon/terminal_host/host.js`, plain node) with a real
`cmd.exe` / login shell behind it, in a temp records folder: the host's pipe
name hashes that folder, so a test can never reach a live host. Every test
kills its host by the PID in the host's own info file.

Refusals are proved by their CONSEQUENCE: the count of `spawn` requests that
reached the host and the count of hosts started, never by status alone.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import shortcuts as shortcuts_mod            # noqa: E402
from daemon.settings import Settings                     # noqa: E402
from daemon import terminals as T                        # noqa: E402

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("node") is None, reason="node is required")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class Env:
    def __init__(self, tmp_path: Path, enabled: bool = True):
        self.settings = Settings(tmp_path / "settings.json")
        if enabled:
            self.settings.update({"terminals_enabled": True})
        self.records = tmp_path / "records"
        self.project = tmp_path / "proj"
        self.project.mkdir()
        self.store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
        self.store.upsert("p1", str(self.project), "Proj")
        self.clients: list[TestClient] = []
        self.terms: list[T.Terminals] = []

    @property
    def tdir(self) -> Path:
        return self.records / "terminals"

    async def client(self) -> tuple[TestClient, T.Terminals]:
        app = web.Application()
        terms = T.add_routes(app, self.store, self.records, self.settings)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.clients.append(client)
        self.terms.append(terms)
        return client, terms

    def host_pid(self) -> int | None:
        try:
            return json.loads((self.tdir / "host-v2.json").read_text())["pid"]
        except FileNotFoundError:
            return None


@pytest_asyncio.fixture
async def env(tmp_path):
    e = Env(tmp_path)
    yield e
    for c in e.clients:
        await c.close()
    pid = e.host_pid()
    if pid:
        kill_pid(pid)


async def make_shell(client: TestClient, cols=100, rows=30) -> dict:
    r = await client.post("/api/terminals", json={
        "project": "p1", "cols": cols, "rows": rows})
    body = await r.json()
    assert r.status == 200, body
    return body["terminal"]


async def read_until(ws, needle: str, timeout=20.0) -> list[dict]:
    """Frames from `ws` until the accumulated text contains `needle`."""
    frames, text = [], ""
    deadline = time.monotonic() + timeout
    while needle not in text:
        left = deadline - time.monotonic()
        assert left > 0, f"never saw {needle!r}; got {text[-300:]!r}"
        frame = await asyncio.wait_for(ws.receive_json(), left)
        frames.append(frame)
        text += frame.get("data", "") if frame["type"] in ("replay", "output") else ""
    return frames


# ---- AC-760: the closed table ------------------------------------------

def test_a_terminal_is_the_plain_shell():
    """AC-760, AC-797 (docs/agent-and-terminal-settings.md SW-8): the one
    thing a terminal runs is the plain shell; the owner types an agent's
    name there. Nothing a caller sends picks another program."""
    file, args = T.launch_argv()
    if os.name == "nt":
        assert (file, args) == ("cmd.exe", [])
    else:
        assert args == ["-l"]
    assert not hasattr(T, "agents")


class SpawnCount:
    """Counts `spawn` requests that reach a host, and hosts started."""

    def __init__(self, monkeypatch):
        self.spawns = 0
        self.hosts = 0
        real_request, real_spawn = T.HostClient.request, T.spawn_host

        async def request(client, op, **params):
            if op == "spawn":
                self.spawns += 1
            return await real_request(client, op, **params)

        def spawn_host(d):
            self.hosts += 1
            return real_spawn(d)

        monkeypatch.setattr(T.HostClient, "request", request)
        monkeypatch.setattr(T, "spawn_host", spawn_host)


@pytest.mark.asyncio
async def test_a_refused_request_spawns_nothing(env, monkeypatch):
    """AC-760: unknown agent, a command line as the agent, a bad body, an
    unknown row and a missing directory all refuse with NO spawn and no host."""
    count = SpawnCount(monkeypatch)
    client, _terms = await env.client()
    ok = {"project": "p1", "cols": 80, "rows": 24}
    cases = [
        ({**ok, "agent": "claude"}, 400),
        ({**ok, "agent": "calc"}, 400),
        ({**ok, "agent": "claude && calc"}, 400),
        ({**ok, "agent": 7}, 400),
        ({**ok, "cols": "80"}, 400),
        ({**ok, "rows": 0}, 400),
        ({**ok, "cols": True}, 400),
        ({k: v for k, v in ok.items() if k != "project"}, 400),
        ({**ok, "project": "nope"}, 404),
    ]
    for body, want in cases:
        r = await client.post("/api/terminals", json=body)
        assert r.status == want, (body, r.status, await r.text())
    r = await client.post("/api/terminals", data=b"not json")
    assert r.status == 400
    r = await client.post("/api/terminals", json=[1])
    assert r.status == 400
    # A row whose directory is gone.
    gone = env.project.parent / "gone"
    gone.mkdir()
    env.store.upsert("gone", str(gone), "Gone")
    gone.rmdir()
    r = await client.post("/api/terminals", json={**ok, "project": "gone"})
    assert r.status == 400
    assert count.spawns == 0 and count.hosts == 0
    assert (await (await client.get("/api/terminals")).json())["terminals"] == []


# ---- the real host: echo, ordering, replay (AC-762) ----------------------

@pytest.mark.asyncio
async def test_listing_create_echo_and_delete(env):
    """The contract: list shape, create, byte echo through the PTY, delete
    ends the shell process."""
    client, terms = await env.client()
    listing = await (await client.get("/api/terminals")).json()
    assert listing == {"terminals": []}
    rec = await make_shell(client)
    assert set(rec) == {"id", "project", "agent", "cwd", "title", "alive",
                        "exit_code", "created"}
    assert rec["alive"] and rec["agent"] == "shell" and rec["project"] == "p1"
    assert os.path.samefile(rec["cwd"], env.project)
    ws = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    first = await ws.receive_json()
    assert first["type"] == "replay" and isinstance(first["seq"], int)
    await ws.send_json({"type": "input", "data": "echo BUTLER_ECHO_7731\r"})
    await read_until(ws, "BUTLER_ECHO_7731")
    shell_pid = (await terms.client.request("list"))["sessions"][0]["pid"]
    assert pid_alive(shell_pid)
    r = await client.delete(f"/api/terminals/{rec['id']}")
    assert r.status == 200 and await r.json() == {"ok": True}
    for _ in range(100):
        if not pid_alive(shell_pid):
            break
        await asyncio.sleep(0.1)
    assert not pid_alive(shell_pid)
    assert (await client.delete(f"/api/terminals/{rec['id']}")).status == 404
    assert (await client.get("/ws/terminal/nope")).status == 404
    await ws.close()


@pytest.mark.asyncio
async def test_reattach_mid_stream_has_no_gap_or_duplicate(env):
    """AC-762: a view that attaches while output is flowing gets a replay and
    then output whose seq is exactly replay.seq+1, +2, ... with no gap."""
    client, terms = await env.client()
    rec = await make_shell(client)
    ws1 = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    await ws1.receive_json()
    if os.name == "nt":
        cmd = "for /L %i in (1,1,4000) do @echo ROW%i\r"
    else:
        cmd = "for i in $(seq 1 4000); do echo ROW$i; done\r"
    await ws1.send_json({"type": "input", "data": cmd})
    await read_until(ws1, "ROW200")
    ws2 = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    replay = await ws2.receive_json()
    assert replay["type"] == "replay" and "ROW" in replay["data"]
    seqs = []
    while True:
        frame = await asyncio.wait_for(ws2.receive_json(), 20)
        assert frame["type"] == "output"
        seqs.append(frame["seq"])
        if "ROW4000" in frame["data"]:
            break
    assert seqs[0] == replay["seq"] + 1
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    await ws1.close()
    await ws2.close()


@pytest.mark.asyncio
async def test_exit_frame_when_the_shell_ends(env):
    """The page learns the shell ended: an exit frame, then the list says so."""
    client, _ = await env.client()
    rec = await make_shell(client)
    ws = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    await ws.receive_json()
    await ws.send_json({"type": "input", "data": "exit 3\r"})
    while True:
        frame = await asyncio.wait_for(ws.receive_json(), 20)
        if frame["type"] == "exit":
            break
    assert frame["code"] == 3
    (t,) = (await (await client.get("/api/terminals")).json())["terminals"]
    assert t["alive"] is False and t["exit_code"] == 3
    # An ended terminal still attaches: empty replay, then its exit.
    ws2 = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    assert (await ws2.receive_json())["type"] == "replay"
    assert (await ws2.receive_json()) == {"type": "exit", "code": 3}
    await ws.close()


# ---- AC-763: a daemon restart keeps the shells ----------------------------

@pytest.mark.asyncio
async def test_daemon_restart_readopts_the_same_shell(env):
    """AC-763: drop the daemon side entirely, build a new one over the same
    records; the host and the shell PID are unchanged, the id/project/agent
    survive and the screen is restored by the replay."""
    client, terms = await env.client()
    rec = await make_shell(client)
    ws = await client.ws_connect(f"/ws/terminal/{rec['id']}")
    await ws.receive_json()
    await ws.send_json({"type": "input", "data": "echo BEFORE_RESTART_5512\r"})
    await read_until(ws, "BEFORE_RESTART_5512")
    host_pid = env.host_pid()
    shell_pid = (await terms.client.request("list"))["sessions"][0]["pid"]
    await ws.close()
    await client.close()                 # the old daemon is gone
    terms.client.close()
    await asyncio.sleep(0.3)

    client2, terms2 = await env.client()  # on_startup adopts, never spawns
    assert env.host_pid() == host_pid and pid_alive(host_pid)
    assert pid_alive(shell_pid)
    (t,) = (await (await client2.get("/api/terminals")).json())["terminals"]
    assert (t["id"], t["project"], t["agent"], t["alive"]) == (
        rec["id"], "p1", "shell", True)
    ws2 = await client2.ws_connect(f"/ws/terminal/{rec['id']}")
    replay = await ws2.receive_json()
    assert "BEFORE_RESTART_5512" in replay["data"]
    await ws2.send_json({"type": "input", "data": "echo AFTER_RESTART_8820\r"})
    await read_until(ws2, "AFTER_RESTART_8820")
    assert (await terms2.client.request("list"))["sessions"][0]["pid"] == shell_pid
    await ws2.close()


@pytest.mark.asyncio
async def test_a_dead_host_marks_terminals_ended_and_a_new_one_starts(env, monkeypatch):
    """No host at adopt time: records go to alive=false, nothing is spawned;
    a new terminal then starts exactly one host."""
    client, terms = await env.client()
    rec = await make_shell(client)
    await client.close()
    terms.client.close()
    kill_pid(env.host_pid())
    await asyncio.sleep(1.0)
    count = SpawnCount(monkeypatch)
    client2, _ = await env.client()
    (t,) = (await (await client2.get("/api/terminals")).json())["terminals"]
    assert t["id"] == rec["id"] and t["alive"] is False
    assert count.hosts == 0 and count.spawns == 0
    await make_shell(client2)
    assert count.hosts == 1 and count.spawns == 1


# ---- AC-764: the token never crosses the pipe ------------------------------

class Squatter(asyncio.Protocol):
    """Holds the pipe name while no host runs and answers hello with a
    challenge whose proof is wrong, recording every byte it is sent."""

    received = b""

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        Squatter.received += data
        msg = json.loads(data.split(b"\n")[0])
        if msg.get("type") == "hello":
            self.transport.write((json.dumps({
                "type": "challenge", "nonce": "ab" * 32,
                "proof": "00" * 32}) + "\n").encode())


@pytest.mark.asyncio
async def test_a_squatter_learns_nothing_and_is_refused(tmp_path, monkeypatch):
    """AC-764: a process that grabbed the pipe name cannot prove the token;
    the client sends it only the hello nonce - never an auth message, and the
    token text appears nowhere in what it received."""
    tdir = tmp_path / "terminals"
    tdir.mkdir()
    token = "SECRET-TOKEN-xyzzy-0451"
    (tdir / "host-v2.token").write_text(token)
    Squatter.received = b""
    loop = asyncio.get_running_loop()
    pipe = str(T.host_paths(tdir)["pipe"])
    if os.name == "nt":
        servers = await loop.start_serving_pipe(Squatter, pipe)
    else:
        servers = [await loop.create_unix_server(Squatter, pipe)]
    monkeypatch.setattr(T, "CONNECT_BUDGET_S", 1.0)
    client = T.HostClient(tdir)
    try:
        with pytest.raises(T.HostError):
            await client.connect(spawn_if_absent=False)
    finally:
        for s in servers if isinstance(servers, list) else [servers]:
            s.close()
    assert not client.connected
    assert b'"hello"' in Squatter.received
    assert b'"auth"' not in Squatter.received
    assert token.encode() not in Squatter.received


@pytest.mark.asyncio
async def test_the_proof_matches_the_hosts(env, monkeypatch):
    """The Python HMAC and the host's agree (a real hello_ok), and a client
    holding a WRONG token is refused before it sends anything else."""
    client, terms = await env.client()
    await make_shell(client)
    token = (env.tdir / "host-v2.token").read_text().strip()
    assert terms.client.hello["version"] == 2
    good = T.HostClient(env.tdir)
    await good.connect(spawn_if_absent=False)
    assert good.hello["hostPid"] == env.host_pid()
    good.close()
    (env.tdir / "host-v2.token").write_text("not-the-token")
    monkeypatch.setattr(T, "CONNECT_BUDGET_S", 1.0)
    bad = T.HostClient(env.tdir)
    with pytest.raises(T.HostError):
        await bad.connect(spawn_if_absent=False)
    (env.tdir / "host-v2.token").write_text(token)



# ---- docs/agent-and-terminal-settings.md: the switch (SW-6, SW-7) --------

@pytest.mark.asyncio
async def test_terminals_are_off_by_default_and_spawn_nothing(tmp_path,
                                                              monkeypatch):
    """AC-794: with `terminals_enabled` at its default (off) every route and
    the socket answer 404 and no host process starts."""
    count = SpawnCount(monkeypatch)
    off = Env(tmp_path, enabled=False)
    assert off.settings.get("terminals_enabled") is False
    client, _terms = await off.client()
    try:
        assert (await client.get("/api/terminals")).status == 404
        r = await client.post("/api/terminals", json={
            "project": "p1", "cols": 80, "rows": 24})
        assert r.status == 404 and "off" in (await r.json())["error"]
        assert (await client.delete("/api/terminals/t_x")).status == 404
        assert (await client.get("/ws/terminal/t_x")).status == 404
    finally:
        await client.close()
    assert count.spawns == 0 and count.hosts == 0


@pytest.mark.asyncio
async def test_switching_terminals_off_ends_the_open_ones(env):
    """AC-795 (SW-7): off means off -- every open shell ends."""
    client, terms = await env.client()
    rec = await make_shell(client)
    shell_pid = (await terms.client.request("list"))["sessions"][0]["pid"]
    assert pid_alive(shell_pid)
    env.settings.update({"terminals_enabled": False})
    for _ in range(100):
        if not pid_alive(shell_pid) and rec["id"] not in terms.records:
            break
        await asyncio.sleep(0.1)
    assert not pid_alive(shell_pid) and rec["id"] not in terms.records
    assert (await client.get("/api/terminals")).status == 404
