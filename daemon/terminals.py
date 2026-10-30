"""In-page terminals: the daemon's side of the terminal host.

`docs/chats-and-terminals.md` CT-1..CT-6, AC-760..AC-764. A terminal is a PTY
owned by a DETACHED Node process (`daemon/terminal_host/`, ported from
an earlier prototype), so a daemon restart does not end the shells. This module is the
host's only client and the owner of the routes that expose it.

**The closed table (AC-760).** A request names a project ROW and an agent KEY.
`launch_argv` turns the key into an argv through `shortcuts.TERMINAL_AGENTS`
(or the plain shell); nothing from the request ever reaches a command line,
and the host's `spawn` op takes a file and args only from this module.

**The token never crosses the pipe (AC-764).** The hello is a mutual HMAC
challenge-response (`terminal_host/protocol.js`): each side proves it holds
`host-v2.token` over a nonce the other chose. This client checks the HOST's
proof first and sends nothing more to a process that fails it.

**Survival (AC-763).** The terminal list lives in `<records>/terminals/
terminals.json`. A started daemon connects WITHOUT spawning a host
(`Terminals.adopt`); a host that is not there means every record is ended.
A host is spawned only when a terminal is requested.

**seq (AC-762).** Output events carry a per-terminal `seq`; a snapshot returns
the seq it covers. A view gets `replay(seq=n)` and then only events after n.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import WSMsgType, web


log = logging.getLogger("butler.terminals")

PROTOCOL_VERSION = 2
HOST_ENTRY = Path(__file__).resolve().parent / "terminal_host" / "host.js"
MAX_LINE_BYTES = 32 * 1024 * 1024
WRITE_CHUNK_CHARS = 1024 * 1024
CONNECT_BUDGET_S = 8.0
REQUEST_TIMEOUT_S = 30.0
HOST_LOG_MAX_BYTES = 4 * 1024 * 1024

#: The plain shell, beside the closed coding-agent table.
SHELL = "shell"

#: "No host is there" is decided ONLY by these on connect; any other connect
#: error is raised (guessing a host is gone and starting a second one is how
#: two hosts end up fighting over one directory).
GONE = (FileNotFoundError, ConnectionRefusedError)


def launch_argv() -> tuple[str, list[str]]:
    """(file, args) for a terminal: the plain shell, the ONLY program a
    terminal runs (docs/agent-and-terminal-settings.md SW-8, AC-760). The
    owner types `claude`, `pi` or `opencode` in it. Windows: `cmd.exe`;
    elsewhere the login shell."""
    if os.name == "nt":
        return "cmd.exe", []
    return os.environ.get("SHELL") or "/bin/zsh", ["-l"]


def host_paths(terminals_dir: Path) -> dict[str, Path | str]:
    """Where one terminals directory's host lives; the same hash the host
    computes (`terminal_host/protocol.js#hostPaths`), so a temp directory can
    never reach a live host."""
    resolved = os.path.abspath(terminals_dir)
    key = hashlib.sha256(resolved.encode("utf8")).hexdigest()[:12]
    name = f"butler-terminal-host-v{PROTOCOL_VERSION}-{key}"
    pipe = (r"\\.\pipe" + "\\" + name if os.name == "nt"
            else os.path.join(resolved, f"host-v{PROTOCOL_VERSION}.sock"))
    return {
        "pipe": pipe,
        "token": Path(resolved) / f"host-v{PROTOCOL_VERSION}.token",
        "info": Path(resolved) / f"host-v{PROTOCOL_VERSION}.json",
        "log": Path(resolved) / "host.log",
    }


def proof(token: str, role: str, nonce: str) -> str:
    """HMAC-SHA256 over the OTHER side's nonce (`protocol.js#proof`)."""
    return hmac.new(token.encode("utf8"),
                    f"butler-terminal-host:{role}:{nonce}".encode("utf8"),
                    hashlib.sha256).hexdigest()


def spawn_host(terminals_dir: Path) -> int:
    """Start a DETACHED host for `terminals_dir`; its stdio goes to host.log,
    never to ours (a child holding our pipe would outlive and pin it)."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not on PATH; the terminal host is a Node "
                           "process (daemon/terminal_host/host.js)")
    paths = host_paths(terminals_dir)
    terminals_dir.mkdir(parents=True, exist_ok=True)
    logp = Path(paths["log"])
    mode = "wb" if logp.exists() and logp.stat().st_size > HOST_LOG_MAX_BYTES else "ab"
    kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS alone: combined with CREATE_NEW_CONSOLE it is
        # invalid (WinError 87). Hidden, in its own process group.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
        kwargs["startupinfo"] = startup
    else:
        kwargs["start_new_session"] = True
    with open(logp, mode) as out:
        child = subprocess.Popen(
            [node, str(HOST_ENTRY),
             f"--terminals-dir={os.path.abspath(terminals_dir)}"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out,
            cwd=str(HOST_ENTRY.parent), close_fds=True, **kwargs)
    log.info("started a terminal host (pid %s) for %s", child.pid, terminals_dir)
    return child.pid


class HostError(Exception):
    """The host refused one request, or the connection failed. `code` is the
    host's (`not_found`, `bad_request`...) or ours (`host_disconnected`,
    `host_timeout`)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"terminal host: {message}")
        self.code = code


class _Wire(asyncio.Protocol):
    """NDJSON framing for one connection."""

    def __init__(self, client: "HostClient"):
        self.client = client
        self.transport = None
        self.buf = bytearray()

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data: bytes):
        self.buf += data
        while True:
            nl = self.buf.find(b"\n")
            if nl < 0:
                if len(self.buf) > MAX_LINE_BYTES:
                    self.client.log_protocol("line past the limit")
                    self.transport.close()
                return
            line = bytes(self.buf[:nl])
            del self.buf[:nl + 1]
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError as err:
                self.client.log_protocol(f"bad NDJSON line: {err}")
                self.transport.close()
                return
            self.client.on_message(self, msg)

    def connection_lost(self, exc):
        self.client.on_lost(self)


class HostClient:
    """One authenticated connection to the host of a terminals directory."""

    def __init__(self, terminals_dir: Path):
        self.terminals_dir = Path(terminals_dir)
        self.paths = host_paths(self.terminals_dir)
        self.hello: dict | None = None
        self.spawned_pid: int | None = None
        #: set by the owner; called with each host event after start_events()
        self.on_event = None
        self.on_disconnect = None
        self._wire: _Wire | None = None
        self._phase = "closed"
        self._token = ""
        self._nonce = ""
        self._hello_done: asyncio.Future | None = None
        self._pending: dict[int, tuple[asyncio.Future, str]] = {}
        self._next_rid = 1
        self._queued: list | None = []
        self._closing = False

    @property
    def connected(self) -> bool:
        return self._wire is not None and self._phase == "open"

    def log_protocol(self, what: str) -> None:
        log.error("terminal host protocol error: %s", what)

    # -- connecting -----------------------------------------------------

    async def _open(self) -> _Wire:
        loop = asyncio.get_running_loop()
        pipe = str(self.paths["pipe"])
        if os.name == "nt":
            _t, wire = await loop.create_pipe_connection(
                lambda: _Wire(self), pipe)
        else:
            _t, wire = await loop.create_unix_connection(
                lambda: _Wire(self), pipe)
        wire.transport = _t     # connection_made may run a tick later
        return wire

    async def connect(self, spawn_if_absent: bool = True) -> dict:
        """Connect and complete the hello, starting a host once when none
        answers and `spawn_if_absent`. A token race right after a start (the
        host writes its token only after it wins the pipe) is retried within
        the budget."""
        deadline = time.monotonic() + CONNECT_BUDGET_S
        delay = 0.05
        last: Exception | None = None
        while True:
            wire = None
            try:
                wire = await self._open()
            except GONE as err:
                last = err
                if self.spawned_pid is None:
                    if not spawn_if_absent:
                        raise
                    self.spawned_pid = await asyncio.to_thread(
                        spawn_host, self.terminals_dir)
                    deadline = time.monotonic() + CONNECT_BUDGET_S
            if wire is not None:
                try:
                    self.hello = await self._handshake(wire)
                    return self.hello
                except HostError as err:
                    wire.transport.close()
                    if err.code not in ("bad_token", "bad_host_proof", "no_token", "closed"):
                        raise
                    last = err
            if time.monotonic() >= deadline:
                raise HostError(
                    "host_unreachable",
                    f"no answer within {CONNECT_BUDGET_S}s ({last!r}); see {self.paths['log']}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.5)

    async def _handshake(self, wire: _Wire) -> dict:
        try:
            self._token = Path(self.paths["token"]).read_text("utf8").strip()
        except FileNotFoundError as err:
            raise HostError("no_token", f"no token file yet: {err}") from err
        self._wire = wire
        self._phase = "challenge"
        self._queued = []
        self._hello_done = asyncio.get_running_loop().create_future()
        self._nonce = secrets.token_hex(32)
        self._send({"type": "hello", "version": PROTOCOL_VERSION,
                    "nonce": self._nonce})
        try:
            return await asyncio.wait_for(self._hello_done, 5.0)
        except asyncio.TimeoutError as err:
            raise HostError("hello_timeout", "no hello reply") from err
        finally:
            if self._phase != "open":
                self._wire = None

    def _fail_hello(self, code: str, message: str) -> None:
        if self._hello_done is not None and not self._hello_done.done():
            self._hello_done.set_exception(HostError(code, message))

    def _send(self, msg: dict) -> None:
        line = (json.dumps(msg) + "\n").encode("utf8")
        if len(line) > MAX_LINE_BYTES:
            raise HostError("request_too_large", "request past the line limit; not sent")
        self._wire.transport.write(line)

    # -- inbound ---------------------------------------------------------

    def on_message(self, wire: _Wire, msg: dict) -> None:
        if wire is not self._wire:
            return
        kind = msg.get("type")
        if self._phase == "challenge":
            if kind == "error":
                return self._fail_hello(msg.get("code", "refused"), msg.get("message", ""))
            nonce = msg.get("nonce")
            if (kind != "challenge" or not isinstance(nonce, str)
                    or not hmac.compare_digest(str(msg.get("proof", "")).encode("utf8"),
                                                proof(self._token, "host", self._nonce).encode("utf8"))):
                # Nothing more is sent: it cannot prove it holds the token.
                wire.transport.close()
                return self._fail_hello(
                    "bad_host_proof",
                    "the process on the pipe could not prove it holds the token")
            self._phase = "auth"
            self._send({"type": "auth", "proof": proof(self._token, "client", nonce)})
        elif self._phase == "auth":
            if kind != "hello_ok":
                return self._fail_hello(msg.get("code", "refused"), msg.get("message", ""))
            self._phase = "open"
            if self._hello_done and not self._hello_done.done():
                self._hello_done.set_result(msg)
        elif kind == "reply":
            pending = self._pending.pop(msg.get("rid"), None)
            if pending is None:
                return
            fut, op = pending
            if fut.done():
                return
            if msg.get("ok"):
                fut.set_result(msg.get("result") or {})
            else:
                err = msg.get("error") or {}
                fut.set_exception(HostError(err.get("code", "failed"),
                                            f"{op}: {err.get('message')}"))
        elif kind == "event":
            if self._queued is not None:
                self._queued.append(msg)
            elif self.on_event:
                self.on_event(msg)

    def start_events(self) -> None:
        """Deliver the events queued since hello, then every later one."""
        queued, self._queued = self._queued, None
        for msg in queued or []:
            if self.on_event:
                self.on_event(msg)

    def on_lost(self, wire: _Wire) -> None:
        if wire is not self._wire:
            return
        self._wire = None
        was_open = self._phase == "open"
        self._phase = "closed"
        self._fail_hello("closed", "the host closed the connection during hello")
        err = HostError("host_disconnected", "terminal host disconnected")
        for fut, _op in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()
        if was_open and self.on_disconnect and not self._closing:
            self.on_disconnect()

    # -- requests ----------------------------------------------------------

    async def request(self, op: str, **params):
        if not self.connected:
            raise HostError("host_disconnected", "terminal host is not connected")
        rid = self._next_rid
        self._next_rid += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = (fut, op)
        # The envelope last: a param can never override type/rid/op.
        try:
            self._send({**params, "type": "request", "rid": rid, "op": op})
        except HostError:
            self._pending.pop(rid, None)
            raise
        try:
            return await asyncio.wait_for(fut, REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError as err:
            self._pending.pop(rid, None)
            raise HostError("host_timeout", f"no answer to {op}") from err

    async def write(self, tid: str, data: str) -> None:
        """Input, split so no line can pass the limit; in order."""
        for i in range(0, max(len(data), 1), WRITE_CHUNK_CHARS):
            await self.request("write", id=tid, data=data[i:i + WRITE_CHUNK_CHARS])

    def close(self) -> None:
        """Disconnect on purpose; the host and its shells keep running."""
        self._closing = True
        if self._wire is not None:
            self._wire.transport.close()


class Terminals:
    """The registry of terminals, the host connection and the fan-out."""

    def __init__(self, records: Path, shortcuts):
        self.dir = Path(records) / "terminals"
        self.file = self.dir / "terminals.json"
        self.shortcuts = shortcuts
        self.records: dict[str, dict] = {}
        self.client: HostClient | None = None
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.file.read_text("utf8"))
        except FileNotFoundError:
            return
        self.records = {t["id"]: t for t in raw["terminals"]}

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"terminals": list(self.records.values())},
                                  indent=1), "utf8")
        os.replace(tmp, self.file)

    @staticmethod
    def public(rec: dict) -> dict:
        return {k: rec[k] for k in ("id", "project", "agent", "cwd", "title",
                                    "alive", "exit_code", "created")}

    # -- the host ---------------------------------------------------------

    async def _connect(self, spawn: bool) -> HostClient | None:
        """Connected client, or None when no host runs and `spawn` is false.
        Reconciles the registry with the host's live sessions on every new
        connection."""
        async with self._lock:
            if self.client is not None and self.client.connected:
                return self.client
            client = HostClient(self.dir)
            client.on_event = self._on_event
            client.on_disconnect = self._on_disconnect
            try:
                hello = await client.connect(spawn_if_absent=spawn)
            except GONE:
                self.client = None
                self._reconcile(set())
                return None
            self.client = client
            live = {s["id"] for s in hello["sessions"]}
            self._reconcile(live)
            for orphan in live - set(self.records):
                await client.request("kill", id=orphan)
            client.start_events()
            return client

    def _reconcile(self, live: set[str]) -> None:
        changed = False
        for rec in self.records.values():
            if rec["alive"] and rec["id"] not in live:
                rec["alive"] = False
                changed = True
        if changed:
            self._save()

    async def adopt(self, _app=None) -> None:
        """Daemon start (AC-763): re-adopt a running host's sessions without
        starting one. A failure is logged and surfaces on the next request."""
        try:
            await self._connect(spawn=False)
        except Exception:
            log.exception("could not adopt the terminal host")

    async def close(self, _app=None) -> None:
        """Daemon stop: disconnect. The host and its shells keep running."""
        if self.client is not None:
            self.client.close()

    def _on_disconnect(self) -> None:
        log.warning("terminal host connection dropped")
        for qs in self.subs.values():
            for q in qs:
                q.put_nowait({"event": "disconnect"})

    def _on_event(self, msg: dict) -> None:
        tid = msg.get("id")
        if msg.get("event") == "exit":
            rec = self.records.get(tid)
            if rec is not None:
                rec["alive"] = False
                rec["exit_code"] = msg.get("exitCode")
                self._save()
        for q in self.subs.get(tid, ()):
            q.put_nowait(msg)

    # -- operations -------------------------------------------------------

    def listing(self) -> dict:
        return {"terminals": [self.public(r) for r in self.records.values()]}

    async def end_all(self) -> None:
        """Every terminal ends (SW-7: switching terminals off is off)."""
        for tid in list(self.records):
            await self.delete(tid)

    async def create(self, project: str, cols: int, rows: int) -> dict:
        """Open a shell. Everything is validated BEFORE the host is
        touched, so a refusal spawns nothing (AC-760)."""
        agent = SHELL
        file, args = launch_argv()
        row = self.shortcuts.get(project)           # KeyError: unknown row
        cwd = row.path
        if not Path(cwd).is_dir():
            raise NotADirectoryError(f"project directory {cwd!r} does not exist")
        client = await self._connect(spawn=True)
        tid = "t_" + secrets.token_hex(6)
        # Registered before the spawn so no output event can precede it.
        label = row.label or Path(cwd).name
        rec = {"id": tid, "project": project, "agent": agent, "cwd": cwd,
               "title": f"{label} - {agent}", "alive": True, "exit_code": None,
               "created": time.time()}
        self.records[tid] = rec
        try:
            await client.request("spawn", id=tid, file=file, args=args,
                                 cwd=cwd, cols=cols, rows=rows)
        except Exception:
            del self.records[tid]
            raise
        self._save()
        return self.public(rec)

    async def delete(self, tid: str) -> None:
        rec = self.records[tid]                     # KeyError: unknown
        if rec["alive"]:
            client = await self._connect(spawn=False)
            if client is not None:
                try:
                    await client.request("kill", id=tid)
                except HostError as err:
                    if err.code != "not_found":
                        raise
        del self.records[tid]
        self._save()
        for q in self.subs.pop(tid, ()):
            q.put_nowait({"event": "exit", "id": tid, "exitCode": None})

    def subscribe(self, tid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(tid, set()).add(q)
        return q

    def unsubscribe(self, tid: str, q: asyncio.Queue) -> None:
        self.subs.get(tid, set()).discard(q)


def _int(body: dict, key: str, lo: int, hi: int) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{key} must be an integer in {lo}..{hi}")
    return value


OFF = "terminals are off; turn them on in Settings, Access, In-page terminals"


def add_routes(app: web.Application, shortcuts, records: Path,
               settings) -> Terminals:
    """Register the terminal routes on `app` and return the `Terminals`.

    GET /api/terminals, POST /api/terminals, DELETE /api/terminals/{tid},
    GET /ws/terminal/{tid}. The caller (make_app) puts all four behind the
    owner-only gate (AC-761, `access.OWNER_ONLY`); nothing here
    authenticates. Terminals never speak (owner decision 3).

    The `terminals_enabled` setting (SW-6, SW-7): while off every route and
    the socket answer 404 and nothing reaches the host; switching it off
    ends every open terminal, and a boot with it off ends any a running host
    still holds.
    """
    terms = Terminals(Path(records), shortcuts)

    def enabled() -> bool:
        return bool(settings.get("terminals_enabled"))

    async def start(app_):
        await terms.adopt(app_)
        if not enabled():
            await terms.end_all()
        loop = asyncio.get_running_loop()

        def changed(key, value):
            if key == "terminals_enabled" and not value:
                loop.call_soon_threadsafe(
                    lambda: loop.create_task(terms.end_all()))
        settings.on_change(changed)

    app.on_startup.append(start)
    app.on_cleanup.append(terms.close)

    def off() -> web.Response:
        return web.json_response({"error": OFF}, status=404)

    async def list_terminals(req):
        if not enabled():
            return off()
        return web.json_response(terms.listing())

    async def create_terminal(req):
        if not enabled():
            return off()
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "body must be JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be an object"}, status=400)
        project = body.get("project")
        if not isinstance(project, str):
            return web.json_response({"error": "project must be a string"}, status=400)
        # SW-8: a terminal is the shell; any other program is refused.
        if body.get("agent", SHELL) != SHELL:
            return web.json_response(
                {"error": "a terminal runs the shell only; type the agent's "
                          "name in it"}, status=400)
        try:
            cols, rows = _int(body, "cols", 1, 500), _int(body, "rows", 1, 200)
        except ValueError as err:
            return web.json_response({"error": str(err)}, status=400)
        try:
            shortcuts.get(project)
        except KeyError:
            return web.json_response({"error": f"no project row {project!r}"}, status=404)
        try:
            rec = await terms.create(project, cols, rows)
        except NotADirectoryError as err:
            return web.json_response({"error": str(err)}, status=400)
        except HostError as err:
            return web.json_response({"error": str(err), "code": err.code}, status=503)
        return web.json_response({"terminal": rec})

    async def delete_terminal(req):
        if not enabled():
            return off()
        try:
            await terms.delete(req.match_info["tid"])
        except KeyError:
            return web.json_response({"error": "no such terminal"}, status=404)
        except HostError as err:
            return web.json_response({"error": str(err), "code": err.code}, status=503)
        return web.json_response({"ok": True})

    async def terminal_socket(req):
        if not enabled():
            raise web.HTTPNotFound(text=OFF)
        tid = req.match_info["tid"]
        rec = terms.records.get(tid)
        if rec is None:
            raise web.HTTPNotFound(text="no such terminal")
        ws = web.WebSocketResponse(max_msg_size=MAX_LINE_BYTES)
        await ws.prepare(req)
        q = terms.subscribe(tid)
        try:
            await _serve_socket(terms, tid, ws, q)
        finally:
            terms.unsubscribe(tid, q)
        return ws

    app.router.add_get("/api/terminals", list_terminals)
    app.router.add_post("/api/terminals", create_terminal)
    app.router.add_delete("/api/terminals/{tid}", delete_terminal)
    app.router.add_get("/ws/terminal/{tid}", terminal_socket)
    return terms


async def _serve_socket(terms: Terminals, tid: str, ws: web.WebSocketResponse,
                        q: asyncio.Queue) -> None:
    """Replay the screen, then stream output in order; pass input down."""
    rec = terms.records[tid]
    snap = None
    if rec["alive"]:
        try:
            client = await terms._connect(spawn=False)
            if client is not None:
                snap = await client.request("snapshot", id=tid)
        except HostError as err:
            if err.code != "not_found":
                await ws.close(code=1011, message=str(err).encode()[:120])
                return
    if snap is None:
        # Ended (or already gone): an empty screen and its exit.
        await ws.send_json({"type": "replay", "data": "", "seq": 0})
        await ws.send_json({"type": "exit", "code": rec["exit_code"]})
        await ws.close()
        return
    covered = snap["seq"]
    await ws.send_json({"type": "replay", "data": snap["ansi"], "seq": covered})

    async def pump() -> None:
        while True:
            ev = await q.get()
            kind = ev["event"]
            if kind == "output":
                if ev["seq"] > covered:
                    await ws.send_json({"type": "output", "data": ev["data"],
                                        "seq": ev["seq"]})
            elif kind == "exit":
                await ws.send_json({"type": "exit", "code": ev.get("exitCode")})
                return
            elif kind == "disconnect":
                return      # the page reattaches and gets a fresh replay

    async def reader() -> None:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                return
            try:
                frame = json.loads(msg.data)
                kind = frame["type"]
            except (ValueError, KeyError, TypeError):
                await ws.close(code=1003, message=b"bad frame")
                return
            client = terms.client
            if client is None or not client.connected:
                return
            try:
                if kind == "input" and isinstance(frame.get("data"), str):
                    await client.write(tid, frame["data"])
                elif (kind == "resize" and all(
                        isinstance(frame.get(k), int) and not isinstance(frame.get(k), bool)
                        for k in ("cols", "rows"))):
                    await client.request("resize", id=tid, cols=frame["cols"],
                                         rows=frame["rows"])
                else:
                    await ws.close(code=1003, message=b"bad frame")
                    return
            except HostError:
                continue    # the shell ended under us; pump reports the exit

    tasks = [asyncio.ensure_future(pump()), asyncio.ensure_future(reader())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if not ws.closed:
        await ws.close()
