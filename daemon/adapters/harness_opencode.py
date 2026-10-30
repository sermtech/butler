"""opencode behind the Harness port. docs/chats-and-terminals.md CT-7, OD-2.

An earlier prototype has no opencode chat backend, so this is built from opencode
1.19.3's own machine output, recorded on this desk 2026-10-29
(`test/assets/harness/opencode-*.jsonl`, model
`opencode/deepseek-v4.1-flash-free`).

**Transport (OD-2, decided by this build): `opencode run --format json`, ONE
PROCESS PER PROMPT**, not `opencode serve` and not ACP. It is the simplest
thing that gives everything the port needs [MEASURED 2026-10-29]: streamed
JSON events, tool events with their real outcome, and a session to continue
(`--session <id>`; a continued run remembered the previous one). The prompt
goes on STDIN (measured: `opencode run` reads a piped message), never on the
command line, so nothing the owner says is re-parsed by `cmd`. The cost is a
process start per prompt instead of a held child.

  `_Mapper`          -- pure. One JSON record in, zero or more events out.
  `OpenCodeHarness`  -- the transport: `start` checks and records the spec,
                        each `prompt` runs one process to its exit.

**The mapping** (opencode run event -> HarnessEvent):

  text       -> TextDelta(part.text) + TextDelta("\\n\\n")  (a WHOLE part:
                opencode run prints a text part when it is complete, so the
                block break follows at once, harness_pi's `_close_text` rule)
  reasoning  -> Activity("thinking")  [assumed: not seen without --thinking]
  tool_use   -> ToolStart + ToolEnd(part.callID, part.tool,
                is_error = state.status == "error")  (opencode prints a tool
                only when it has FINISHED, so both arrive together)
  step_finish -> TurnEnd(that step's text parts)
  error      -> remembered; the prompt ends Failed at the exit
  process exit rc 0, no error      -> AgentEnd
  process exit with an error or rc != 0 -> Failed (error, else stderr tail)
  an exit after abort()            -> AgentEnd (barge-in, D-4)

**One terminal event per prompt (AC-48)** comes from the PROCESS EXIT, which
happens once per run, and the guard drops anything a superseded run says.

**Session**: every event carries `sessionID`; the first one is stored in the
spec's session file (`session_id_file`), and the next run passes
`--session <id>`. A dead id is `rc=1` with "Session not found" on stderr
[MEASURED]: the id is forgotten and the Failed message carries those words,
so `brain_harness` replays the turn on a fresh session.

**Permissions** (OD-1, owner 2026-10-29: "like an earlier prototype", i.e. bypass):
`--auto` approves every permission the owner's config does not explicitly
deny. `harness_tools: read_only` runs opencode's own read-only `plan` agent
WITHOUT `--auto`; a permission asked there is auto-rejected by `opencode run`
[MEASURED: "permission requested: bash (echo hi); auto-rejecting", the tool
ended as an error]. No question reaches a human, so no `Ask` is raised.

`model` is opencode's `provider/model`; None is opencode's configured
default. `append_prompt` (the persona file), `memory_url` and `chat_id` are
NOT used: `opencode run` has no system-prompt flag and Butler's tools have no
opencode bridge yet.

Spawned through `cmd /c opencode` on Windows (opencode is `opencode.cmd`
there, like pi), so every argv value is refused if it carries a cmd
metacharacter (`harness_pi.reject_shell_metacharacters`).
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import shutil
import sys
import time
from typing import AsyncIterator

from .. import chats as chats_mod
from .. import childproc, ports
from ..ports import (Activity, AgentEnd, Failed, HarnessCaps, HarnessEvent,
                     HarnessSpec, TextDelta, ToolEnd, ToolStart, TurnEnd)
from . import session_id_file
from .harness_pi import (_END_OF_STREAM, _Framer, READ_CHUNK,
                         reject_shell_metacharacters)


def opencode_command(os_name: str) -> tuple[str, ...]:
    """`cmd /c opencode` on Windows (opencode.cmd, CreateProcess does not
    resolve PATHEXT), plain `opencode` elsewhere -- `harness_pi.pi_command`'s
    rule."""
    return ("cmd", "/c", "opencode") if os_name == "nt" else ("opencode",)


OPENCODE = opencode_command(os.name)

#: The read-only agent `harness_tools: read_only` selects (opencode built-in).
READ_ONLY_AGENT = "plan"

#: How long a killed run is given to report its exit.
DISPOSE_TIMEOUT_S = 5.0

#: The model list (`list_models`): harness_pi's TTL and reason.
MODELS_TTL_S = 300.0
MODELS_TIMEOUT_S = 60.0
_model_cache: list = []          # [taken_at, models] while it is fresh

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class _Mapper:
    """One RUN's events, normalized. Pure: no I/O, no clock."""

    def __init__(self) -> None:
        self._pending = False
        self._aborting = False
        self._error: str | None = None
        self._step: list[str] = []
        self._seen: set[str] = set()      # part ids already mapped

    @property
    def pending(self) -> bool:
        return self._pending

    def prompt_sent(self) -> None:
        self._pending = True
        self._aborting = False
        self._error = None
        self._step = []
        self._seen.clear()

    def abort_requested(self) -> None:
        if self._pending:
            self._aborting = True

    def feed(self, obj: dict) -> list[HarnessEvent]:
        if not self._pending or self._aborting:
            return []
        kind = obj.get("type")
        part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
        if kind == "error":
            error = obj.get("error") or {}
            data = error.get("data") if isinstance(error, dict) else None
            message = (data.get("message") if isinstance(data, dict) else None)
            name = error.get("name") if isinstance(error, dict) else None
            self._error = (f"opencode: {name or 'error'}: "
                           f"{message or json.dumps(error)[:300]}")
            return []
        key = str(part.get("id") or part.get("callID") or "")
        if key and key in self._seen:
            return []                     # a part is mapped once
        if kind == "step_start":
            self._step = []
            return []
        if kind == "text":
            text = str(part.get("text") or "")
            if not text.strip():
                return []
            self._seen.add(key)
            self._step.append(text.strip())
            return [TextDelta(text=text), TextDelta(text="\n\n")]
        if kind == "reasoning":
            self._seen.add(key)
            return [Activity(kind="thinking",
                             chars=len(str(part.get("text") or "")))]
        if kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"),
                                                    dict) else {}
            status = state.get("status")
            if status not in ("completed", "error"):
                return []                 # not finished: nothing to report
            self._seen.add(key)
            tool_id = str(part.get("callID") or part.get("id") or "")
            name = str(part.get("tool") or "")
            return [ToolStart(tool_id=tool_id, name=name),
                    ToolEnd(tool_id=tool_id, name=name,
                            is_error=status == "error")]
        if kind == "step_finish":
            self._seen.add(key)
            text, self._step = "\n\n".join(self._step), []
            return [TurnEnd(text=text)]
        return []

    def exited(self, returncode: int | None, stderr: str) -> list[HarnessEvent]:
        """The run's process is gone: THE terminal event of this prompt."""
        if not self._pending:
            return []
        self._pending = False
        if self._aborting:
            return [AgentEnd()]
        if self._error:
            detail = f" ({stderr})" if stderr else ""
            return [Failed(message=self._error + detail)]
        if returncode != 0:
            return [Failed(message=f"opencode exited (rc={returncode}): "
                                   f"{stderr or '(no output)'}")]
        return [AgentEnd()]


async def _spawn(argv: list[str], cwd: str):
    """The only place a real process is created, so tests can script one."""
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
    # `cmd /c` is the parent of opencode.exe: the job covers the tree when
    # butlerd is hard-killed (AC-36, D-24).
    childproc.assign(proc)
    return proc


async def _kill(proc) -> None:
    """The ONE tree kill (`childproc.kill_tree`); a seam for tests."""
    await asyncio.to_thread(childproc.kill_tree, proc.pid)


def _installed() -> bool:
    """Is the opencode CLI on PATH? A seam for tests."""
    return shutil.which("opencode") is not None


class _Run:
    """One `opencode run` process and everything that belongs to it alone."""

    def __init__(self, proc) -> None:
        self.proc = proc
        self.framer = _Framer()
        self.mapper = _Mapper()
        self.stderr: collections.deque = collections.deque(maxlen=50)
        self.tasks: list[asyncio.Task] = []

    def tail(self) -> str:
        return " | ".join(self.stderr)


@ports.register(ports.HARNESSES, "opencode")
class OpenCodeHarness:
    """`opencode run --format json`, one process per prompt, one session per
    conversation. `model` is the fallback when a spec names none."""

    def __init__(self, model: str | None = None) -> None:
        if model is not None:
            if not str(model).strip():
                raise ValueError(
                    f"model={model!r}: None means opencode's own default, and "
                    f"a blank string is a second spelling of it")
            reject_shell_metacharacters("model", model)
        self._model = model
        self._spec: HarnessSpec | None = None
        self._queue: asyncio.Queue | None = None
        self._run: _Run | None = None
        self._caps = HarnessCaps(native_resume=True, steerable=False,
                                 images=False)

    # -- lifecycle --------------------------------------------------------- #
    async def start(self, spec: HarnessSpec) -> None:
        if self._spec is not None:
            raise RuntimeError(
                "start() on a live child. Changing what a child IS means "
                "dispose() first (AC-31)")
        if not _installed():
            # Fail at START, not at the first prompt: a missing CLI is a
            # spawn failure the caller speaks (AC-47).
            raise RuntimeError("opencode is not installed on this machine")
        self._argv(spec, session_id_file.read(spec.session_file))  # checks
        self._spec = spec
        self._queue = asyncio.Queue()

    def _argv(self, spec: HarnessSpec, session_id: str | None) -> list[str]:
        """Every value is checked: `cmd /c` re-parses this line."""
        argv = [*OPENCODE, "run", "--format", "json"]
        if spec.tools:
            argv += ["--agent", READ_ONLY_AGENT]
        else:
            argv += ["--auto"]
        model = spec.model or self._model
        if model:
            argv += ["--model", reject_shell_metacharacters("model", model)]
        if session_id:
            argv += ["--session", reject_shell_metacharacters(
                "session id", session_id)]
        reject_shell_metacharacters("spec.cwd", spec.cwd)
        return argv

    async def dispose(self) -> None:
        """Idempotent, never raises, and it ENDS the subscribed stream."""
        queue, run = self._queue, self._run
        self._spec = self._queue = self._run = None
        if queue is None:
            return
        queue.put_nowait(_END_OF_STREAM)
        if run is not None:
            try:
                if run.proc.returncode is None:
                    await _kill(run.proc)
                    await asyncio.wait_for(run.proc.wait(), DISPOSE_TIMEOUT_S)
            except Exception as e:                          # noqa: BLE001
                print(f"[butler] WARNING: disposing the opencode run raised "
                      f"{e!r}", file=sys.stderr, flush=True)
            for task in run.tasks:
                task.cancel()

    # -- the stream -------------------------------------------------------- #
    def subscribe(self) -> AsyncIterator[HarnessEvent]:
        if self._queue is None:
            raise RuntimeError("subscribe() before start(): no child to read")
        return self._drain(self._queue)

    async def _drain(self, queue: asyncio.Queue) -> AsyncIterator[HarnessEvent]:
        while True:
            item = await queue.get()
            if item is _END_OF_STREAM or queue is not self._queue:
                return
            yield item

    async def _read_stdout(self, run: _Run, queue: asyncio.Queue) -> None:
        """The run's one reader; its `finally` is the prompt's ending."""
        try:
            while True:
                chunk = await run.proc.stdout.read(READ_CHUNK)
                if run is not self._run:
                    return
                if not chunk:
                    break
                for record in run.framer.feed(chunk):
                    self._handle(run, queue, record)
            for record in run.framer.flush():
                self._handle(run, queue, record)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: the opencode reader stopped on {e!r}; "
                  f"the prompt is being ended", file=sys.stderr, flush=True)
        finally:
            await self._on_exit(run, queue)

    async def _read_stderr(self, run: _Run) -> None:
        while True:
            line = await run.proc.stderr.readline()
            if not line:
                return
            text = _ANSI.sub("", line.decode("utf-8", "replace")).strip()
            if text:
                run.stderr.append(text)

    def _handle(self, run: _Run, queue: asyncio.Queue, record: str) -> None:
        """One record; a record can never cost the turn its ending (D-1)."""
        if not record.strip():
            return
        try:
            obj = json.loads(record)
            if not isinstance(obj, dict):
                return
            sid = obj.get("sessionID")
            if sid and self._spec is not None and run.mapper.pending:
                session_id_file.write(self._spec.session_file, str(sid))
            for event in run.mapper.feed(obj):
                queue.put_nowait(event)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: opencode sent a record this adapter "
                  f"could not handle ({e!r}): {record[:200]!r}",
                  file=sys.stderr, flush=True)

    async def _on_exit(self, run: _Run, queue: asyncio.Queue) -> None:
        if run is not self._run:
            return
        try:
            await asyncio.wait_for(run.proc.wait(), DISPOSE_TIMEOUT_S)
        except Exception:                                   # noqa: BLE001
            pass
        try:
            # stderr carries the reason a run died; let its reader finish.
            await asyncio.wait_for(asyncio.shield(run.tasks[1]), 1.0)
        except Exception:                                   # noqa: BLE001
            pass
        if run is not self._run:
            return
        tail = run.tail()
        if (run.mapper.pending and run.proc.returncode != 0
                and self._spec is not None
                and chats_mod.names_a_missing_session(tail)):
            session_id_file.forget(self._spec.session_file)
        for event in run.mapper.exited(run.proc.returncode, tail):
            queue.put_nowait(event)
        self._run = None

    # -- driving it -------------------------------------------------------- #
    async def prompt(self, text: str) -> None:
        spec, queue = self._spec, self._queue
        if spec is None or queue is None:
            raise RuntimeError(
                "prompt() with no live child. Failing here rather than "
                "queueing, because a prompt that goes nowhere is a turn that "
                "never ends")
        if self._run is not None:
            raise RuntimeError("prompt() while a prompt is in flight")
        argv = self._argv(spec, session_id_file.read(spec.session_file))
        proc = await _spawn(argv, spec.cwd)
        if self._spec is not spec:          # disposed during the spawn
            await _kill(proc)
            return
        run = _Run(proc)
        run.mapper.prompt_sent()
        self._run = run
        run.tasks.append(asyncio.create_task(self._read_stdout(run, queue)))
        run.tasks.append(asyncio.create_task(self._read_stderr(run)))
        try:
            proc.stdin.write(text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception as e:                              # noqa: BLE001
            # The reader still ends the prompt at the exit (D-1).
            print(f"[butler] WARNING: the prompt did not reach opencode "
                  f"({e!r})", file=sys.stderr, flush=True)

    async def answer(self, qid: str, answer: ports.Answer) -> bool:
        """No question is ever raised here (module docstring): always False,
        never a hopeful write (AC-250)."""
        return False

    async def abort(self) -> None:
        """Barge-in: the run is killed and the prompt still ends once, as
        AgentEnd (D-4). The session is on disk, so the next prompt continues
        it."""
        run = self._run
        if run is None or not run.mapper.pending:
            return
        run.mapper.abort_requested()
        try:
            await _kill(run.proc)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: killing the opencode run raised {e!r}",
                  file=sys.stderr, flush=True)

    # -- what it is -------------------------------------------------------- #
    @classmethod
    async def list_models(cls) -> list[str]:
        """Every `provider/model` opencode could run (`opencode models`, its
        own registry), for the `harness_model` picker. Cached like pi's list
        (a node-sized start per call); async because it shells out (D-25)."""
        now = time.monotonic()
        if _model_cache and (now - _model_cache[0]) < MODELS_TTL_S:
            return list(_model_cache[1])
        proc = await asyncio.create_subprocess_exec(
            *OPENCODE, "models", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(),
                                              MODELS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"opencode did not list its models within "
                               f"{MODELS_TIMEOUT_S:.0f}s") from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode models exited {proc.returncode}: "
                f"{(err or out).decode('utf-8', 'replace')[-200:]}")
        found = [line.strip() for line in
                 _ANSI.sub("", out.decode("utf-8", "replace")).splitlines()
                 if re.fullmatch(r"[^\s/]+/\S+", line.strip())]
        if not found:
            raise RuntimeError("opencode listed no models this adapter could "
                               "read")
        _model_cache[:] = [now, found]
        return list(found)

    @property
    def caps(self) -> HarnessCaps:
        return self._caps

    @property
    def info(self) -> dict:
        """Static, and it asks nothing (D-25)."""
        spec = self._spec
        return {"engine": "opencode",
                "model": (spec.model if spec else None) or self._model,
                "running": spec is not None,
                "cwd": spec.cwd if spec else None}
