"""Scripted child processes that REPLAY recorded harness output.

For `test_harness_claude.py`, `test_harness_opencode.py` and the
`claude-replay` / `opencode-replay` rows of the harness conformance suite:
the adapter's real transport (framing, the one-terminal-event guard, abort,
dispose, the session file) runs against lines recorded from the real CLIs,
with no model paid for.

Recordings (`test/assets/harness/`):
  claude-multi-tool-run.jsonl  -- claude-code 2.1.280, recorded by an earlier prototype
                                  (`test/fixtures/agent-activity/claude-code/`):
                                  two messages with text, four tools, one FAILED.
  opencode-*.jsonl             -- opencode 1.19.3 on this desk, 2026-10-29.

What is NOT a recording, and says so: Claude Code's interrupted `result`
and dead-resume lines are the shapes an earlier prototype MEASURED on 2.1.219/2.1.294
and wrote into its tests; the failing claude `result` ("API Error ...") is
[assumed] from an earlier prototype's handling of `result.is_error` with text.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets" / "harness"
CLAUDE_RUN = ASSETS / "claude-multi-tool-run.jsonl"
OPENCODE_TOOLS = ASSETS / "opencode-tools.jsonl"
OPENCODE_ERROR = ASSETS / "opencode-error.jsonl"

#: The prompt word that makes a replayed child FAIL (conformance FAIL_ON).
FAIL_WORD = "detonate"
#: Between replayed lines, so an abort or a dispose lands MID-answer.
LINE_DELAY_S = 0.002

DEAD_SESSION = "2bccfa6e-6c1e-4eec-81b5-fb962ed3d554"


def lines(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def records(path: Path) -> list[dict]:
    return [json.loads(l) for l in lines(path)]


def claude_failed_result() -> dict:
    """[assumed] A provider failure: is_error with the reason as text."""
    return {"type": "result", "subtype": "success", "is_error": True,
            "result": "API Error: 529 overloaded (detonated by the replay)",
            "session_id": "replay-session"}


def claude_interrupted_result() -> dict:
    """[MEASURED by an earlier prototype on 2.1.294] what an interrupt ends a turn with:
    is_error, NO `result` key."""
    return {"type": "result", "subtype": "error_during_execution",
            "is_error": True, "terminal_reason": "aborted_tools",
            "session_id": "replay-session"}


def claude_dead_resume_result() -> dict:
    """[MEASURED by an earlier prototype on 2.1.219] the WHOLE stdout of a `--resume`
    whose session is gone; the reason is on stderr."""
    return {"type": "result", "subtype": "error_during_execution",
            "is_error": True, "num_turns": 0, "duration_ms": 0,
            "session_id": DEAD_SESSION, "total_cost_usd": 0}


class _Pipe:
    """A readable pipe: `read(n)` and `readline()`, b"" at EOF."""

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    def put(self, data: bytes) -> None:
        self._q.put_nowait(data)

    def eof(self) -> None:
        self._q.put_nowait(b"")

    async def read(self, _n: int) -> bytes:
        return await self._q.get()

    async def readline(self) -> bytes:
        return await self._q.get()


class _Proc:
    """The asyncio.subprocess.Process surface the adapters use."""

    def __init__(self, argv, cwd) -> None:
        self.argv, self.cwd = list(argv), cwd
        self.pid = -1
        self.returncode: int | None = None
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.stdin = self
        self.written: list[dict | str] = []
        self.task: asyncio.Task | None = None

    def emit(self, obj) -> None:
        line = obj if isinstance(obj, str) else json.dumps(obj)
        self.stdout.put((line + "\n").encode("utf-8"))

    def exit(self, rc: int) -> None:
        if self.returncode is not None:
            return
        self.returncode = rc
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.stdout.eof()
        self.stderr.eof()

    def kill(self) -> None:
        self.exit(1)

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.005)
        return self.returncode

    async def drain(self) -> None:
        return None


class ClaudeProc(_Proc):
    """A persistent `claude --print` child: a user line replays the recorded
    run, an interrupt ends the turn the way 2.1.294 does, stdin EOF exits.
    `dead_resume` replays the dead-`--resume` startup failure instead."""

    def __init__(self, argv, cwd, dead_resume: bool = False) -> None:
        super().__init__(argv, cwd)
        self.dead_resume = dead_resume
        self.interrupted = False
        self.stdin_closed = False

    def write(self, raw: bytes) -> None:
        for line in raw.decode("utf-8").split("\n"):
            if not line.strip():
                continue
            msg = json.loads(line)
            self.written.append(msg)
            if msg.get("type") == "user":
                self.interrupted = False
                self.task = asyncio.ensure_future(
                    self._answer(str(msg["message"]["content"])))
            elif msg.get("type") == "control_request":
                self.interrupted = True

    def close(self) -> None:
        # stdin EOF: an idle child exits now, a busy one after its turn.
        self.stdin_closed = True
        if self.task is None or self.task.done():
            self.exit(0)

    async def _answer(self, text: str) -> None:
        try:
            await self._replay(text)
        finally:
            if self.stdin_closed:
                self.exit(0)

    async def _replay(self, text: str) -> None:
        if self.dead_resume:
            self.stderr.put(f"No conversation found with session ID: "
                            f"{DEAD_SESSION}\n".encode())
            self.emit(claude_dead_resume_result())
            await asyncio.sleep(0)
            self.exit(1)
            return
        if FAIL_WORD in text:
            self.emit(lines(CLAUDE_RUN)[0])          # system/init
            self.emit(claude_failed_result())
            return
        for line in lines(CLAUDE_RUN):
            if self.interrupted:
                self.emit({"type": "control_response",
                           "response": {"subtype": "success"}})
                self.emit(claude_interrupted_result())
                return
            self.emit(line)
            await asyncio.sleep(LINE_DELAY_S)


class OpenCodeProc(_Proc):
    """One `opencode run --format json`: the message arrives on stdin, the
    recorded run is replayed, the process exits."""

    def __init__(self, argv, cwd, missing_session: bool = False) -> None:
        super().__init__(argv, cwd)
        self.missing_session = missing_session
        self._text = b""

    def write(self, raw: bytes) -> None:
        self._text += raw

    def close(self) -> None:
        self.written.append(self._text.decode("utf-8"))
        self.task = asyncio.ensure_future(self._answer(self.written[-1]))

    async def _answer(self, text: str) -> None:
        if self.missing_session:
            # [MEASURED 2026-10-29] rc=1, nothing on stdout, this on stderr.
            self.stderr.put(b"\x1b[91m\x1b[1mError: \x1b[0mSession not found\n")
            await asyncio.sleep(0)
            self.exit(1)
            return
        script = OPENCODE_ERROR if FAIL_WORD in text else OPENCODE_TOOLS
        for line in lines(script):
            self.emit(line)
            await asyncio.sleep(LINE_DELAY_S)
        self.exit(1 if script is OPENCODE_ERROR else 0)


def install_claude(monkeypatch, **proc_kwargs) -> list[ClaudeProc]:
    """Route `harness_claude`'s spawn and kill to replayed children; the
    returned list fills with every child spawned."""
    from daemon.adapters import harness_claude
    spawned: list[ClaudeProc] = []

    async def _spawn(argv, cwd):
        proc = ClaudeProc(argv, cwd, **proc_kwargs)
        spawned.append(proc)
        return proc

    async def _kill(proc):
        proc.kill()

    monkeypatch.setattr(harness_claude, "_spawn", _spawn)
    monkeypatch.setattr(harness_claude, "_kill", _kill)
    return spawned


def install_opencode(monkeypatch, **proc_kwargs) -> list[OpenCodeProc]:
    """Route `harness_opencode`'s spawn and kill to replayed runs."""
    from daemon.adapters import harness_opencode
    spawned: list[OpenCodeProc] = []

    async def _spawn(argv, cwd):
        proc = OpenCodeProc(argv, cwd, **proc_kwargs)
        spawned.append(proc)
        return proc

    async def _kill(proc):
        proc.kill()

    monkeypatch.setattr(harness_opencode, "_spawn", _spawn)
    monkeypatch.setattr(harness_opencode, "_kill", _kill)
    monkeypatch.setattr(harness_opencode, "_installed", lambda: True)
    return spawned
