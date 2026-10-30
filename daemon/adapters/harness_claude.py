"""Claude Code behind the Harness port. docs/chats-and-terminals.md CT-7.

Ported from an earlier prototype's tested backend (`scripts/dashboard/server/lib/
backends/claude-backend.js` and `claude-translate.js`, claude 2.1.170 to
2.1.294), in harness_pi's two layers:

  `_Mapper`        -- pure. One stream-json record in, zero or more
                      `HarnessEvent`s out. `test/test_harness_claude.py`
                      drives it with a run recorded from a real CLI
                      (`test/assets/harness/claude-multi-tool-run.jsonl`).
  `ClaudeHarness`  -- the transport. ONE persistent child per conversation:

    claude --print --input-format stream-json --output-format stream-json
           --verbose --include-partial-messages
           --permission-mode bypassPermissions --add-dir <cwd>
           [--model m] [--tools Read,Grep,Glob --strict-mcp-config]
           [--append-system-prompt-file <persona>] [--resume <id>]

  stdin takes one JSON line per prompt; stdout is LF-delimited stream-json,
  split by harness_pi's `_Framer`.

**The mapping** (stream-json record -> HarnessEvent):

  system/init                      -> (the session id, stored in the spec's
                                       session file; no event)
  content_block_delta text_delta   -> TextDelta
  content_block_stop of a text block, message_stop
                                   -> TextDelta("\\n\\n") (the block break,
                                       at the END of the block, harness_pi's
                                       `_close_text` rule)
  content_block_delta thinking_delta / system thinking_tokens
                                   -> Activity("thinking")  (Claude Code
                                       REDACTS thinking: the deltas are empty
                                       [MEASURED by an earlier prototype, 2.1.280])
  content_block_delta input_json_delta
                                   -> Activity("tool_input")
  content_block_start tool_use     -> ToolStart(the block's own id, name)
  user tool_result                 -> ToolEnd(tool_use_id, is_error is True)
  message_stop                     -> TurnEnd(that message's text blocks)
  result success                   -> AgentEnd
  result is_error                  -> Failed (or AgentEnd after abort())
  the child's close mid-prompt     -> Failed with the stderr tail

**One terminal event per prompt (AC-48).** Claude Code runs its own loop and
writes ONE `result` per prompt, retries included. The guard is still here,
pi's shape: nothing is mapped while no prompt is in flight, the first
terminal closes the prompt, and the reader's `finally` ends a prompt whose
child died.

**Abort is an interrupt, not a kill** [MEASURED by an earlier prototype on 2.1.294]: a
`control_request` interrupt on stdin ends the turn with a `result`
`{is_error: true, subtype: error_during_execution}` in 0.04-0.21 s and the
child lives on. That result is barge-in, so it maps to AgentEnd, never
Failed (D-4); anything the child says between the interrupt and its result
is dropped. If no result comes within `INTERRUPT_GRACE_S` the tree is killed
and the prompt still ends once.

**A dead `--resume`** (claude keys sessions by cwd, so a moved folder orphans
every id) is ONE `result` is_error with no `system/init` before it and "No
conversation found with session ID" on STDERR. The verdict waits for the
child's close, where stderr is complete; the stored id is then forgotten
(`session_id_file.forget`) and the Failed message carries the CLI's words, so
`brain_harness._forget_a_dead_session` replays the turn on a fresh session.

**Permissions** (OD-1, decided 2026-10-29 by the owner: "like an earlier prototype"):
`bypassPermissions`, as an earlier prototype runs it. No tool asks a human, so this
adapter raises no `Ask` and `answer` always answers False.

**What a spec field becomes here**: `model` -> `--model`; a non-empty `tools`
(the `harness_tools` read_only setting) -> Claude Code's own read set plus
`--strict-mcp-config` (an earlier prototype MEASURED that `--tools` alone leaves MCP
tools and the account's connectors in); `append_prompt` (the persona FILE,
AC-373) -> `--append-system-prompt-file`. `memory_url` and `chat_id` are NOT
used: Butler's memory, task and library tools reach pi through its extension
(`pi_memory.ts`) and Claude Code has no such bridge yet, so a Claude Code chat
runs without them.

claude is a real executable (`claude.exe`), spawned WITHOUT `cmd /c`, so no
argument is re-parsed by a shell and the metacharacter refusal pi needs does
not apply; the prompt goes on stdin anyway.
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys
from typing import AsyncIterator

from .. import chats as chats_mod
from .. import childproc, ports
from ..ports import (Activity, AgentEnd, Failed, HarnessCaps, HarnessEvent,
                     HarnessSpec, TextDelta, ToolEnd, ToolStart, TurnEnd)
from . import session_id_file
from .harness_pi import _END_OF_STREAM, _Framer, READ_CHUNK

CLAUDE = ("claude",)

#: OD-1 (owner, 2026-10-29): Claude Code chats run as an earlier prototype runs them.
PERMISSION_MODE = "bypassPermissions"

#: What the `harness_model` picker offers (`ClaudeHarness.list_models`).
MODEL_ALIASES = ("haiku", "sonnet", "opus")

#: What `harness_tools: read_only` becomes: Claude Code's own read-only tools.
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

#: How long an interrupted turn is given to end itself before the tree is
#: killed. An earlier prototype MEASURED 0.04-0.21 s on 2.1.294 and uses 3 s.
INTERRUPT_GRACE_S = 3.0

#: stdin EOF ends an idle child; this is how long that is given before the
#: tree is killed (harness_pi's DISPOSE_TIMEOUT_S, same reason).
DISPOSE_TIMEOUT_S = 5.0


class _Mapper:
    """One child's stream-json, normalized. Pure: no I/O, no clock.

    `resumed` says the child was spawned with `--resume`, which is the only
    case where an is_error `result` before any `system/init` is a dead session
    whose verdict must wait for stderr (module docstring).
    """

    def __init__(self, resumed: bool = False) -> None:
        self._resumed = resumed
        self._open: dict[str, str] = {}
        self._pending = False
        self._aborting = False
        self._error: str | None = None
        self._init_seen = False
        self._deferred: str | None = None
        self._text_index: object = None      # the open text block's index
        self._blocks: list[str] = []         # this message's text blocks
        self._turn_ended = False

    @property
    def pending(self) -> bool:
        return self._pending

    @property
    def deferred(self) -> bool:
        """A failure is waiting for the child's close to be ruled on."""
        return self._deferred is not None

    def prompt_sent(self) -> None:
        self._open.clear()
        self._pending = True
        self._aborting = False
        self._error = None
        self._deferred = None
        self._text_index = None
        self._blocks = []
        self._turn_ended = False

    def abort_requested(self) -> None:
        """Barge-in: what the child says next is not an answer, and its
        is_error `result` is not a failure (D-4)."""
        if self._pending:
            self._aborting = True

    def feed(self, obj: dict) -> list[HarnessEvent]:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self._init_seen = True
            return []
        if not self._pending:
            return []
        if kind == "result":
            return self._result(obj)
        if self._aborting:
            return []
        if kind == "stream_event":
            event = obj.get("event")
            return self._stream(event) if isinstance(event, dict) else []
        if kind == "system" and obj.get("subtype") == "thinking_tokens":
            # Redacted thinking: no text, only the fact that it is happening,
            # which is what re-arms the caller's idle deadline (AC-388).
            return [Activity(kind="thinking", chars=0)]
        if kind == "user":
            return self._tool_results(obj)
        # `assistant` echoes repeat what the stream already said; `system`
        # status lines, `rate_limit_event` and `control_response` are not
        # the answer.
        return []

    def closed(self, reason: str) -> list[HarnessEvent]:
        """The child is gone: a prompt in flight ends now (D-1, AC-48)."""
        if self._deferred is not None:
            reason = f"{self._deferred}: {reason}"
        self._error = reason
        return self.terminal()

    def terminal(self) -> list[HarnessEvent]:
        """The one ending this prompt is allowed, or nothing at all."""
        if not self._pending:
            return []
        out = self._close_text()
        self._pending = False
        message, self._error = self._error, None
        if self._aborting:
            # Barge-in, whatever the child said about it (D-4).
            return out + [AgentEnd()]
        if message:
            return out + [Failed(message=message)]
        return out + [AgentEnd()]

    # -- the pieces -------------------------------------------------------- #
    def _stream(self, event: dict) -> list[HarnessEvent]:
        kind = event.get("type")
        if kind == "message_start":
            self._blocks = []
            return self._close_text()
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                self._open[tool_id] = name
                return self._close_text() + [ToolStart(tool_id=tool_id,
                                                       name=name)]
            return []
        if kind == "content_block_delta":
            return self._delta(event)
        if kind == "content_block_stop":
            if (self._text_index is not None
                    and event.get("index") == self._text_index):
                return self._close_text()
            return []
        if kind == "message_stop":
            out = self._close_text()
            text = "\n\n".join(b.strip() for b in self._blocks if b.strip())
            self._blocks = []
            self._turn_ended = True
            return out + [TurnEnd(text=text)]
        return []

    def _delta(self, event: dict) -> list[HarnessEvent]:
        delta = event.get("delta") or {}
        kind = delta.get("type")
        if kind == "text_delta":
            text = str(delta.get("text") or "")
            if not text:
                return []
            index = event.get("index")
            out: list[HarnessEvent] = []
            if self._text_index is None or index != self._text_index:
                # A new text block. One left open (no stop seen) is closed
                # here instead, so two blocks never weld together.
                out += self._close_text()
                self._text_index = index
                self._blocks.append("")
            self._blocks[-1] += text
            return out + [TextDelta(text=text)]
        if kind == "input_json_delta":
            size = len(str(delta.get("partial_json") or ""))
            return [Activity(kind="tool_input", chars=size)] if size else []
        if kind == "thinking_delta":
            return [Activity(kind="thinking",
                             chars=len(str(delta.get("thinking") or "")))]
        return []

    def _close_text(self) -> list[HarnessEvent]:
        """End the open text block with a paragraph break (harness_pi's
        `_Mapper._close_text`, and its latency argument: the sentence before a
        tool call is released before the tool runs)."""
        if self._text_index is None:
            return []
        self._text_index = None
        return [TextDelta(text="\n\n")]

    def _tool_results(self, obj: dict) -> list[HarnessEvent]:
        message = obj.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            return []
        out: list[HarnessEvent] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id") or "")
            name = self._open.pop(tool_id, "")
            # `is_error` is true, false or ABSENT, and absent means ok
            # [MEASURED by an earlier prototype, 2.1.280]. Never hardcoded (4.4c).
            out.append(ToolEnd(tool_id=tool_id, name=name,
                               is_error=block.get("is_error") is True))
        return out

    def _result(self, obj: dict) -> list[HarnessEvent]:
        """Claude Code's one `result` per prompt: the terminal event."""
        if self._aborting:
            return self.terminal()
        text = obj.get("result") if isinstance(obj.get("result"), str) else ""
        if obj.get("is_error"):
            errors = obj.get("errors")
            reason = (text or ("; ".join(str(e) for e in errors)
                               if isinstance(errors, list) and errors else "")
                      or str(obj.get("subtype") or "")
                      or "the Claude Code turn failed with no reason given")
            if self._resumed and not self._init_seen:
                # Possibly a dead `--resume`: the WHY is on stderr, which is a
                # different pipe. Ruled on at the child's close (`closed`).
                self._deferred = f"Claude Code refused to start ({reason})"
                return []
            self._error = reason
            return self.terminal()
        out: list[HarnessEvent] = []
        if not self._turn_ended and text.strip():
            # A stream that carried no message events: the result is the
            # whole answer (AC-47's no-delta recovery).
            out.append(TurnEnd(text=text.strip()))
        return out + self.terminal()


async def _spawn(argv: list[str], cwd: str):
    """The only place a real process is created, so tests can script one."""
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
    # Claude Code starts its own sub-processes: the job object covers them
    # when butlerd is hard-killed (AC-36, D-24).
    childproc.assign(proc)
    return proc


async def _kill(proc) -> None:
    """The ONE tree kill (`childproc.kill_tree`); a seam for tests."""
    await asyncio.to_thread(childproc.kill_tree, proc.pid)


class _Child:
    """One claude process and everything that belongs to it alone (per-child
    buffers: harness_pi's `_Child` rule and reason)."""

    def __init__(self, proc, spec: HarnessSpec, resumed: bool) -> None:
        self.proc = proc
        self.spec = spec
        self.queue: asyncio.Queue = asyncio.Queue()
        self.framer = _Framer()
        self.mapper = _Mapper(resumed=resumed)
        self.stderr: collections.deque = collections.deque(maxlen=50)
        self.tasks: list[asyncio.Task] = []
        self.stderr_task: asyncio.Task | None = None
        self.seq = 0

    def tail(self) -> str:
        return " | ".join(self.stderr) if self.stderr else "(no output)"


@ports.register(ports.HARNESSES, "claude")
class ClaudeHarness:
    """`claude --print` in stream-json mode, held for one conversation.

    Constructor kwargs are Claude Code's own vocabulary (AC-41): `model` is
    what a spec naming no model falls back to (None: the subscription's
    default), `append_system_prompt` is TEXT appended to the system prompt
    (the conformance row's instructions).
    """

    def __init__(self, model: str | None = None,
                 append_system_prompt: str | None = None,
                 interrupt_grace_s: float = INTERRUPT_GRACE_S) -> None:
        for name, value in (("model", model),
                            ("append_system_prompt", append_system_prompt)):
            if value is not None and not str(value).strip():
                raise ValueError(
                    f"{name}={value!r}: None means Claude Code's own default, "
                    f"and a blank string is a second spelling of it")
        self._model = model
        self._append_system_prompt = append_system_prompt
        self._interrupt_grace_s = interrupt_grace_s
        self._child: _Child | None = None
        self._caps = HarnessCaps(
            # Claude Code resumes by an opaque id it prints at init. The id
            # is kept INSIDE the spec's session file (session_id_file), so
            # no caller handles it differently from pi's file.
            native_resume=True,
            steerable=False,       # a second user line mid-turn is refused
            images=True,           # stream-json user content takes images
        )

    # -- lifecycle --------------------------------------------------------- #
    async def start(self, spec: HarnessSpec) -> None:
        if self._child is not None:
            raise RuntimeError(
                "start() on a live child. Changing what a child IS means "
                "dispose() first (AC-31)")
        session_id = session_id_file.read(spec.session_file)
        proc = await _spawn(self._argv(spec, session_id), spec.cwd)
        child = _Child(proc, spec, resumed=session_id is not None)
        self._child = child
        child.stderr_task = asyncio.create_task(self._read_stderr(child))
        child.tasks.append(asyncio.create_task(self._read_stdout(child)))
        child.tasks.append(child.stderr_task)
        # No readiness handshake: in stream-json input mode Claude Code says
        # nothing until the first prompt arrives. A child that dies at
        # startup (a bad model, a lost login) ends that prompt as Failed
        # with its stderr, at the close (AC-47).

    def _argv(self, spec: HarnessSpec, session_id: str | None) -> list[str]:
        argv = [*CLAUDE, "--print",
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--verbose", "--include-partial-messages",
                "--permission-mode", PERMISSION_MODE,
                "--add-dir", spec.cwd]
        model = spec.model or self._model
        if model:
            argv += ["--model", model]
        if spec.tools:
            # read_only. An allow-list fails CLOSED the day a new built-in
            # ships, and `--strict-mcp-config` keeps the owner's MCP servers
            # and account connectors out (an earlier prototype, MEASURED 2026-08-03).
            argv += ["--tools", ",".join(READ_ONLY_TOOLS),
                     "--strict-mcp-config"]
        if self._append_system_prompt:
            argv += ["--append-system-prompt", self._append_system_prompt]
        if spec.append_prompt:
            # The persona FILE (AC-373), on every spawn: it is identity, not
            # a one-time seed.
            argv += ["--append-system-prompt-file", spec.append_prompt]
        if session_id:
            argv += ["--resume", session_id]
        return argv

    async def dispose(self) -> None:
        """Idempotent, never raises, and it ENDS the subscribed stream."""
        child, self._child = self._child, None
        if child is None:
            return
        child.queue.put_nowait(_END_OF_STREAM)
        try:
            await self._close_child(child)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: disposing the claude child raised "
                  f"{e!r}", file=sys.stderr, flush=True)
        for task in child.tasks:
            task.cancel()
        child.tasks.clear()

    async def _close_child(self, child: _Child) -> None:
        proc = child.proc
        if proc.returncode is not None:
            return
        busy = child.mapper.pending
        try:
            proc.stdin.close()
        except Exception:                                   # noqa: BLE001
            pass
        if not busy:
            # An idle child exits on stdin EOF; a busy one would finish its
            # turn first, which is exactly what a dispose mid-answer is not.
            try:
                await asyncio.wait_for(proc.wait(), DISPOSE_TIMEOUT_S)
                return
            except Exception:                               # noqa: BLE001
                pass
        await _kill(proc)
        try:
            await asyncio.wait_for(proc.wait(), DISPOSE_TIMEOUT_S)
        except Exception:                                   # noqa: BLE001
            pass

    # -- the stream -------------------------------------------------------- #
    def subscribe(self) -> AsyncIterator[HarnessEvent]:
        if self._child is None:
            raise RuntimeError("subscribe() before start(): no child to read")
        return self._drain(self._child)

    async def _drain(self, child: _Child) -> AsyncIterator[HarnessEvent]:
        while True:
            item = await child.queue.get()
            if item is _END_OF_STREAM or child is not self._child:
                return
            yield item

    async def _read_stdout(self, child: _Child) -> None:
        """The one reader; its `finally` ends a prompt whose child died."""
        try:
            while True:
                chunk = await child.proc.stdout.read(READ_CHUNK)
                if child is not self._child:
                    return
                if not chunk:
                    break
                for record in child.framer.feed(chunk):
                    self._handle(child, record)
            for record in child.framer.flush():
                self._handle(child, record)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: the claude reader stopped on {e!r}; "
                  f"the prompt is being ended", file=sys.stderr, flush=True)
        finally:
            await self._on_close(child)

    async def _read_stderr(self, child: _Child) -> None:
        while True:
            line = await child.proc.stderr.readline()
            if child is not self._child or not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                child.stderr.append(text)

    def _handle(self, child: _Child, record: str) -> None:
        """One record; a record can never cost the turn its ending (D-1)."""
        if not record.strip():
            return
        try:
            obj = json.loads(record)
            if not isinstance(obj, dict):
                return
            if (obj.get("type") == "system" and obj.get("subtype") == "init"
                    and obj.get("session_id")):
                session_id_file.write(child.spec.session_file,
                                      str(obj["session_id"]))
            for event in child.mapper.feed(obj):
                child.queue.put_nowait(event)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: claude sent a record this adapter "
                  f"could not handle ({e!r}): {record[:200]!r}",
                  file=sys.stderr, flush=True)

    async def _on_close(self, child: _Child) -> None:
        """The child's stdout ended: it is gone, and so is any prompt."""
        if child is not self._child:
            return
        try:
            await asyncio.wait_for(child.proc.wait(), DISPOSE_TIMEOUT_S)
        except Exception:                                   # noqa: BLE001
            pass
        # stderr is a separate pipe: its reader is let finish (bounded), so
        # the verdict below reads the whole of it -- the dead-session words
        # live there (module docstring).
        try:
            await asyncio.wait_for(asyncio.shield(child.stderr_task), 1.0)
        except Exception:                                   # noqa: BLE001
            pass
        if child is not self._child:
            return
        tail = child.tail()
        if (child.mapper.deferred
                and chats_mod.names_a_missing_session(tail)):
            # The stored session is gone. Forgotten HERE, because this file
            # is the adapter's: the next child starts fresh (AC-35).
            session_id_file.forget(child.spec.session_file)
        reason = (f"the claude child exited (rc={child.proc.returncode}): "
                  f"{tail}")
        for event in child.mapper.closed(reason):
            child.queue.put_nowait(event)
        child.queue.put_nowait(_END_OF_STREAM)

    # -- driving it -------------------------------------------------------- #
    async def prompt(self, text: str) -> None:
        child = self._child
        if child is None or child.proc.returncode is not None:
            raise RuntimeError(
                "prompt() with no live child. Failing here rather than "
                "queueing, because a prompt that goes nowhere is a turn that "
                "never ends")
        if child.mapper.pending:
            raise RuntimeError(
                "prompt() while a prompt is in flight: a second stream-json "
                "user line mid-turn corrupts the child's input")
        child.mapper.prompt_sent()
        await self._write(child, {"type": "user",
                                  "message": {"role": "user",
                                              "content": text}})

    async def answer(self, qid: str, answer: ports.Answer) -> bool:
        """No question is ever raised here (bypassPermissions, OD-1), so no
        id is held: always False, never a hopeful write (AC-250)."""
        return False

    async def abort(self) -> None:
        """Interrupt over stdin; kill the tree if the turn does not end."""
        child = self._child
        if child is None or not child.mapper.pending:
            return
        child.mapper.abort_requested()
        child.seq += 1
        try:
            await self._write(child, {
                "type": "control_request",
                "request_id": f"butler-interrupt-{child.seq}",
                "request": {"subtype": "interrupt"}})
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: the claude interrupt did not reach the "
                  f"child ({e!r}); killing it", file=sys.stderr, flush=True)
            await _kill(child.proc)
            return
        child.tasks.append(asyncio.create_task(self._interrupt_deadline(child)))

    async def _interrupt_deadline(self, child: _Child) -> None:
        """anthropics/claude-code #98713 (an earlier prototype): an interrupt can be
        acknowledged and ignored, so the fallback keys on the turn ENDING."""
        await asyncio.sleep(self._interrupt_grace_s)
        if child is self._child and child.mapper.pending:
            print(f"[butler] WARNING: claude did not end an interrupted turn "
                  f"in {self._interrupt_grace_s:.0f}s; killing it",
                  file=sys.stderr, flush=True)
            await _kill(child.proc)

    async def _write(self, child: _Child, obj: dict) -> None:
        if child is not self._child:
            return
        child.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await child.proc.stdin.drain()

    # -- what it is -------------------------------------------------------- #
    @classmethod
    async def list_models(cls) -> list[str]:
        """The `harness_model` picker's choices: the aliases `claude --model`
        documents for the latest model of each family [READ `claude --help`,
        2.1.312]. A full model name typed by hand works too."""
        return list(MODEL_ALIASES)

    @property
    def caps(self) -> HarnessCaps:
        return self._caps

    @property
    def info(self) -> dict:
        """Static, and it asks the child NOTHING (D-25)."""
        child = self._child
        return {"engine": "claude",
                "model": (child.spec.model if child else None) or self._model,
                "running": child is not None,
                "cwd": child.spec.cwd if child else None}
