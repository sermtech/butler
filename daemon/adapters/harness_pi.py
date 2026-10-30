"""pi behind the Harness port. docs/harness-mode.md step 7.2, AC-44.

Two layers in one file, because the mapping must be testable without paying
for a child:

  `_Framer` + `_Mapper`  -- pure. Bytes in, JSONL records out; one record in,
                            zero or more `HarnessEvent`s out. Every test in
                            `test/test_harness_pi.py` above the live one
                            drives these with lines recorded from a real
                            child.
  `PiHarness`            -- the transport. Spawns `cmd /c pi --mode rpc`,
                            drains stdout with ONE reader task, turns a
                            blocking dialog into an `Ask` for a human
                            (AC-234) and routes the answer back to it, and
                            owns the one-terminal-event guard.

**FRAMING: split on LF and nothing else.** pi's own rpc.md warns that a
generic line reader is not protocol-compliant, because Node's `readline`
also splits on U+2028 and U+2029, which are legal inside JSON strings -- one
event would arrive as two unparseable fragments. Python's `readline` splits
on `\\n` alone and would be compliant; `_Framer` does the same split by hand
only because a single record here can be megabytes (a whole tool result) and
`asyncio.StreamReader.readline` has a byte cap that turns an oversized
record into a lost stream. Do not "improve" either half.

**Everything about a child is fixed at spawn** -- working directory, model,
session file, tool posture -- so there is no setter here and never will be
one: moving directory is dispose-plus-start with a new `HarnessSpec` (AC-31).
`spec.tools` drives ONE flag: a non-empty allow-list becomes `--tools a,b,c`
(read_only), and an empty tuple builds no flag at all, so the child holds pi's
own default set including `bash` (full). That is the `harness_tools` SETTING,
a plain knob re-adding the minimal `--tools` plumbing 653a44d deleted -- NOT
the spoken grant AC-29 retired on 2026-08-18. `--tools` gated only pi's
BUILT-IN tools when that was measured (2026-08-18); on pi 0.87 it also hides
an extension tool it does not name (MEASURED 2026-09-26, `_argv`'s note).

**Exactly one terminal event per prompt (AC-48), and the guard lives HERE.**
pi has no error event at all: a provider that breaks shows up as a turn whose
message carries `stopReason: "error"`, and pi then emits an ordinary
`agent_end`. It also RETRIES transient failures by itself, emitting a whole
`agent_end` per attempt [MEASURED 2026-08-17] -- three terminal events for
one prompt, which is the announce-it-five-times failure this criterion is
named after. So the adapter turns pi's retry off at start and gates the
stream twice: NOTHING is mapped once the prompt is over (not merely the
terminal events -- a caller speaks `text_delta`, so a retry's text is a
second answer), and a terminal event counts only for a prompt pi ACCEPTED,
matched on the id `prompt()` minted. And because this is the only place an
ending can come from, `_read_stdout` closes the prompt in a `finally` and
maps each record inside its own guard: a record the mapper cannot read costs
one event and never the turn's ending (D-1).

**Every argument on the command line is checked for cmd metacharacters.**
`cmd /c` re-parses what it is handed, so a value carrying `" % & | < > ^` is
an instruction; a model value ran a second command on this machine
[MEASURED 2026-08-17]. Refused loudly at construction and at spawn, never
escaped (AC-31, plan.md 4.4c). The rule SURVIVED AC-29's retirement and its
inputs did too: `harness_model` and `harness_cwd` are settings the owner
types, and since step 7.7 a SPOKEN project name decides the working
directory, so a misrecognition reaches this argv without anyone typing it.

**A startup verdict is taken at the child's CLOSE, not at the first stderr
line** (plan.md 4.4c): stdout and stderr are different pipes and an early
verdict is a race. A bogus provider makes pi print one line and exit 1
[MEASURED], and that exit is what `start()` reports.

**Per-child state, and every handler bails when the child is not the current
one** (plan.md 4.4c). On Windows a killed child keeps emitting, and a
fragment from a dead one prefixing its successor's first line is a parse
failure and a turn that never ends. `dispose` nulls the handle BEFORE it
kills, so anything already in flight takes the superseded path.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import AsyncIterator


from .. import childproc, ports
from ..ports import (Activity, AgentEnd, Failed, HarnessCaps, HarnessEvent,
                     HarnessSpec, TextDelta, ToolEnd, ToolStart, TurnEnd)


def pi_command(os_name: str) -> tuple[str, ...]:
    """How pi is launched: `cmd /c pi` on Windows, where pi is pi.cmd and
    CreateProcess does not resolve PATHEXT (plan.md 4.5); plain `pi` on macOS,
    where it is an executable on PATH (docs/macos.md)."""
    return ("cmd", "/c", "pi") if os_name == "nt" else ("pi",)


PI = pi_command(os.name)

# Bringing a child up gets its own budget, separate from anything per-prompt
# (D-5): one shared watchdog killed a worker that was merely still loading.
# Generous against the 1.5-2.3 s startup docs/harness-mode.md section 1
# measures [MEASURED 2026-08-17, 21 runs], because a machine under load must
# not look like a broken harness.
STARTUP_TIMEOUT_S = 90.0
# A control command pi answers without asking a model. Nothing here waits on
# an LLM: `prompt` is deliberately NOT one of these -- its answer is the
# terminal event, and its budget belongs to the caller's turn.
COMMAND_TIMEOUT_S = 15.0
# stdin EOF is enough for a clean exit -- the child exits 0 on it [MEASURED] --
# and this is how long that is given before the tree is killed. It is not the
# AC-36 mechanism: a HARD kill of butlerd never reaches this code at all, and
# `childproc.assign` at spawn is what covers that (D-24).
DISPOSE_TIMEOUT_S = 5.0

READ_CHUNK = 1 << 16

# Listing pi's registry costs a whole node start, and the settings panel asks
# for it every time it is built. The registry changes when the owner edits
# their models.json, not per turn, so a few minutes of staleness costs at most
# one model that has just been added (models.Catalogue makes the same trade).
MODELS_TTL_S = 300.0
MODELS_TIMEOUT_S = 60.0
_model_cache: list = []          # [taken_at, models] while it is fresh

# `pi --list-models` prints a padded table. Everything that is not a row --
# the header, and the extension banners pi writes at startup -- has to be
# skipped by SHAPE rather than by matching their text, because those banners
# come from whatever extensions the owner has installed.
_MODEL_COLUMNS = 6
# CSI escapes, in case pi ever decides this output is a terminal's.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _parse_model_table(text: str) -> list[str]:
    """`provider/model` for every row of pi's model table, in pi's order.

    Order is preserved rather than sorted, for the reason `models.py` keeps a
    server's order: a registry that lists a provider first is saying
    something. Duplicates are dropped -- the same pair cannot appear twice
    meaningfully -- and the columns after the first two are pi's own facts
    about the model, which Butler does not model.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in _ANSI.sub("", text).splitlines():
        fields = line.split()
        if len(fields) < _MODEL_COLUMNS or fields[0] == "provider":
            continue
        if fields[0].startswith("["):        # an extension's startup banner
            continue
        pair = f"{fields[0]}/{fields[1]}"
        if pair not in seen:
            seen.add(pair)
            found.append(pair)
    return found

# The extension UI methods that BLOCK the agent until the client answers
# (rpc.md, Extension UI Protocol). The rest -- notify, setStatus, setWidget,
# setTitle, set_editor_text -- are fire-and-forget, and answering one would
# put a line on the child's stdin its protocol never asked for.
DIALOG_METHODS = frozenset({"select", "confirm", "input", "editor"})

# The two STATED exceptions to AC-49's reversal, and they are exceptions with
# reasons rather than a fallback (`docs/parallel-agents.md` 9.1, D2 option C's
# warning about lists that go stale -- which is why this list is two entries
# long and each carries its argument).
#
# `editor` is auto-cancelled because it is the one dialog NO Butler surface can
# render or dictate: it asks the client to open a text editor on a buffer, and
# a page with a one-line composer and a room with a microphone can do neither.
# It is also the one method with NO agent-side timeout path at all [READ,
# pi-coding-agent/docs/rpc.md], so leaving it unanswered blocks the child
# forever with nothing on any screen that could unblock it.
AUTO_CANCELLED_METHODS = frozenset({"editor"})


def cancellation_for(request: dict, prompt_pending: bool) -> dict | None:
    """The reply to a dialog Butler will NOT show a human. AC-49, REVERSED.

    Until 2026-09-12 this answered all four dialog methods with
    `cancelled: true` and told the extension the user dismissed the dialog,
    which was honest only because nobody was ever asked. The owner reversed
    that on 2026-09-11 (`docs/parallel-agents.md` D2, ruling A): a question
    now reaches a human and the conversation waits. What it cost is stated in
    `daemon/AGENTS.md` rather than argued here -- an unattended run that would
    have carried on now stops.

    Two cases still cancel, both with a mechanical reason:

      `editor`  -- see `AUTO_CANCELLED_METHODS`. No surface can draw it and
                   it has no agent-side timeout, so waiting is a permanent
                   block rather than a question.
      UNPROMPTED -- three `extension_ui_request`s arrive before the first
                   response even in the simplest configuration [MEASURED],
                   and a dialog raised while no prompt is in flight has no
                   turn to attach to, nothing on the wire that could carry it
                   and no conversation for it to make `waiting`.

    Anything else returns None, which now means "hand it to the human"
    instead of "ignore it".
    """
    method = request.get("method")
    if method not in DIALOG_METHODS:
        return None
    if method in AUTO_CANCELLED_METHODS or not prompt_pending:
        return {"type": "extension_ui_response", "id": request.get("id"),
                "cancelled": True}
    return None


def ask_for(request: dict) -> ports.Ask:
    """One `extension_ui_request` as the port's own `Ask` (AC-234).

    pi's vocabulary stops here (AC-41): `params` and its key names are read
    in this file and nowhere above it. The id is pi's and is never re-minted
    -- `Ask.qid` carries `ToolStart.tool_id`'s rule for its reason.

    `timeout` is pi's own number of MILLISECONDS and it is carried rather
    than acted on: pi resolves a timed dialog agent-side, so Butler draws the
    clock and never runs one (`docs/parallel-agents.md` 9.1).
    """
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}
    options = params.get("options")
    if not isinstance(options, list):
        options = []
    deadline = params.get("timeout")
    return ports.Ask(
        qid=str(request.get("id")),
        method=str(request.get("method")),
        title=str(params.get("title") or ""),
        message=str(params.get("message") or ""),
        # PLAIN STRINGS. pi may send objects for a select; the label is what
        # is drawn as a button and what a voice answer says, and a structured
        # option would be unsayable (ports.Ask).
        options=tuple(
            str(o.get("label") if isinstance(o, dict) else o) for o in options),
        placeholder=str(params.get("placeholder") or ""),
        prefill=str(params.get("value") or params.get("initialValue") or ""),
        deadline_ms=int(deadline) if isinstance(deadline, (int, float)) else None)


def _pi_answer(method: str, answer: ports.Answer) -> dict:
    """A typed `Answer` in pi's own spelling. The one place it is written.

    `{cancelled}` for a dismissal, `{confirmed}` for a confirm, `{value}` for
    everything else -- rpc.md's Extension UI Protocol. It raises on a shape
    the method cannot take rather than sending a field pi will ignore: a
    dialog answered with the wrong key unblocks the agent with a default
    nobody chose, which is exactly the failure AC-250 is named after.
    """
    if answer.cancelled:
        return {"cancelled": True}
    if method == "confirm":
        if answer.confirmed is None:
            raise ValueError(
                f"a {method!r} dialog is answered yes or no; got value="
                f"{answer.value!r}")
        return {"confirmed": bool(answer.confirmed)}
    if answer.value is None:
        raise ValueError(
            f"a {method!r} dialog is answered with a value; got confirmed="
            f"{answer.confirmed!r}")
    return {"value": answer.value}

_END_OF_STREAM = object()

# `cmd /c` takes a command LINE and re-parses it, so these seven characters
# are instructions rather than text: `"` closes the quoting an argument was
# wrapped in, `&` `|` start a second command, `<` `>` redirect, `^` escapes
# the next character, and `%` expands an environment variable. A model value
# of `claude" & echo PWNED & rem ` printed PWNED on this machine [MEASURED
# 2026-08-17]. Nothing pi accepts needs any of them, and from step 7.4
# `harness_model` and `harness_cwd` are settings the owner types, which is
# where a mistyped value stops being a typo and becomes an execution path.
CMD_METACHARACTERS = '"%&|<>^'

#: Butler's memory tools for pi (docs/identity-memory.md 12.6, AC-440): an
#: extension SHIPPED IN THIS REPOSITORY, loaded by path with `--extension`
#: and told where the daemon is with the `--butler-url` flag it registers.
#: Never installed into the owner's pi config -- that file belongs to every
#: pi they run by hand (4.2's reason for not writing `~/.pi/agent/AGENTS.md`).
MEMORY_EXTENSION = Path(__file__).with_name("pi_memory.ts")


def reject_shell_metacharacters(name: str, value: str) -> str:
    """Fail fast, naming the value, before anything reaches `cmd`.

    Loud rather than quoted or escaped: quoting rules for `cmd` differ from
    the ones for the program behind it, so an escape that looks right is the
    kind of fix that works until the day it does not (plan.md 4.5).
    """
    bad = sorted({c for c in str(value) if c in CMD_METACHARACTERS})
    if bad:
        raise ValueError(
            f"{name}={value!r} contains {' '.join(bad)}, which cmd reads as "
            f"an instruction rather than text -- and pi is spawned through "
            f"`cmd /c` because it is pi.cmd (plan.md 4.5). Nothing pi accepts "
            f"needs these characters")
    return str(value)


class _Framer:
    """Bytes from a pipe, LF-delimited records out. See the module docstring.

    Holds the partial tail between reads, because a pipe read boundary lands
    anywhere -- and with records that carry whole tool results, mid-record is
    the ordinary case rather than the edge one.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[str]:
        self._buf += chunk
        if b"\n" not in self._buf:
            return []
        *records, self._buf = self._buf.split(b"\n")
        return [self._decode(r) for r in records]

    def flush(self) -> list[str]:
        """Whatever was left when the pipe closed, if it is not blank."""
        tail, self._buf = self._buf, b""
        return [self._decode(tail)] if tail.strip() else []

    @staticmethod
    def _decode(record: bytes) -> str:
        # `errors="replace"`: a mangled byte costs one line's text, and
        # raising here would take the whole event stream down with it.
        return record.decode("utf-8", "replace").rstrip("\r")


class _Mapper:
    """One child's event stream, normalized. Pure: no I/O, no clock.

    Stateful on purpose, and the state is the contract: a DICT of open tool
    calls keyed by pi's own `toolCallId` (the predecessor kept one current
    slot and lost 8 of 19 parallel calls), WHICH prompt is in flight, whether
    pi has accepted it, and whether it has already failed.

    Two gates, and they are different rules. Nothing at all is mapped while
    no prompt is pending: pi talks at startup, after an abort settles, and
    once per retry attempt it makes on its own, and 7.4 SPEAKS `text_delta`,
    so a stray delta is a second answer to a finished turn (AC-48). And a
    terminal event is refused until pi's `response` for THIS prompt's id has
    arrived, because a late `agent_end` belonging to prompt N would otherwise
    end prompt N+1 the instant it was sent.
    """

    def __init__(self) -> None:
        self._open: dict[str, str] = {}
        self._pending = False
        self._prompt_id: str | None = None
        self._accepted = False
        self._error: str | None = None
        # The text block that has streamed text and not yet been CLOSED,
        # keyed (message, contentIndex): see `_close_text`.
        self._message = 0
        self._text_block: tuple[int, object] | None = None

    @property
    def pending(self) -> bool:
        return self._pending

    def prompt_sent(self, prompt_id: str) -> None:
        self._open.clear()
        self._pending = True
        self._prompt_id = str(prompt_id)
        self._accepted = False
        self._error = None
        self._text_block = None

    def feed(self, obj: dict) -> list[HarnessEvent]:
        if not self._pending:
            return []
        kind = obj.get("type")
        if kind in ("turn_start", "message_start"):
            # A new message: its `contentIndex` counts from 0 again.
            self._message += 1
            return self._close_text()
        if kind == "message_end":
            return self._close_text()
        if kind == "message_update":
            event = obj.get("assistantMessageEvent") or {}
            if event.get("type") in ("text_end", "toolcall_start"):
                return self._close_text()
            return self._delta(event)
        if kind == "tool_execution_start":
            tool_id = str(obj.get("toolCallId") or "")
            name = str(obj.get("toolName") or "")
            self._open[tool_id] = name
            return [ToolStart(tool_id=tool_id, name=name)]
        if kind == "tool_execution_end":
            tool_id = str(obj.get("toolCallId") or "")
            name = str(obj.get("toolName") or self._open.get(tool_id, ""))
            self._open.pop(tool_id, None)
            # `isError` comes from pi. Hardcoding an outcome here is the
            # predecessor's defect, which made a failed tool literally
            # unreachable in its own transcript (plan.md 4.4c).
            return [ToolEnd(tool_id=tool_id, name=name,
                            is_error=bool(obj.get("isError")))]
        if kind == "turn_end":
            return self._close_text() + self._turn_end(obj)
        if kind == "agent_end":
            return self.terminal()
        if kind == "response":
            return self._response(obj)
        return []

    def closed(self, reason: str) -> list[HarnessEvent]:
        """The child is gone. A prompt in flight ends, or it ends never.

        `force`, because a child that died before accepting the prompt is
        exactly the case where waiting for its acceptance is waiting forever
        (D-1): the caller cannot tell that from a dead microphone.
        """
        self._error = reason
        return self.terminal(force=True)

    def terminal(self, force: bool = False) -> list[HarnessEvent]:
        """The one ending this prompt is allowed, or nothing at all.

        Nothing at all is the important half: pi's own retry emits an
        `agent_end` per attempt, the child talks at startup and after an abort
        settles, and a slow `agent_end` from the PREVIOUS prompt arrives after
        the next one is sent. A caller announces once per terminal event
        (AC-48), so a terminal is taken only for a prompt pi has said it
        accepted.
        """
        if not self._pending or not (self._accepted or force):
            return []
        self._pending = False
        message, self._error = self._error, None
        if message:
            return [Failed(message=message)]
        return [AgentEnd()]

    # -- the pieces -------------------------------------------------------- #
    def _delta(self, event: dict) -> list[HarnessEvent]:
        # `text_delta` is the ANSWER. `thinking_delta` is the model's private
        # reasoning and `toolcall_delta` is a tool call's arguments being
        # written; neither may reach the room, so they become `Activity` --
        # EVIDENCE the idle deadline re-arms on and the heartbeat counts, and
        # nothing else (AC-388). Dropping them made a model reasoning for
        # four minutes look exactly like a silent child.
        kind = event.get("type")
        if kind in ("thinking_delta", "toolcall_delta"):
            size = len(str(event.get("delta") or ""))
            return ([Activity(kind="thinking" if kind == "thinking_delta"
                              else "tool_input", chars=size)] if size else [])
        if kind != "text_delta":
            return []
        text = str(event.get("delta") or "")
        if not text:
            return []
        block = (self._message, event.get("contentIndex"))
        if self._text_block is not None and block != self._text_block:
            # A new block while the last one was never closed: a stream with
            # no `text_end` in it. The break goes here instead.
            text = "\n\n" + text
        self._text_block = block
        return [TextDelta(text=text)]

    def _close_text(self) -> list[HarnessEvent]:
        """End the open text block with a paragraph break, or do nothing.

        ONE DELTA STREAM, MANY TEXT BLOCKS. pi streams every text block --
        one per message, so one per tool round, and sometimes two in one
        message -- from its own first character, with nothing separating it
        from the block before. The caller concatenates deltas verbatim
        (ports.TextDelta), so without a break the text either side of a tool
        call reached the room, the page and the chat record glued: "...in
        this directory.I can explain it..." [MEASURED 2026-09-26, live child].

        At the END of a block rather than the start of the next, and that is
        latency, not taste: pi sends `text_end` the moment a block is done,
        and the next block's first delta only after the tool has run and the
        model has thought again -- 3-8 s later [MEASURED 2026-09-26, live
        child]. The break is the whitespace `speech.drain_sentences` waits
        for, so the sentence before a tool call is spoken BEFORE the tool
        runs, and the last one before `agent_end`.
        """
        if self._text_block is None:
            return []
        self._text_block = None
        return [TextDelta(text="\n\n")]

    def _turn_end(self, obj: dict) -> list[HarnessEvent]:
        message = obj.get("message") or {}
        # pi has no error EVENT. A provider that broke arrives as a stop
        # reason on the turn's message, and `agent_end` follows regardless --
        # so an adapter watching for an error event announces an answer it
        # never got. "aborted" is Butler's own barge-in and is NOT a failure
        # (D-4): apologising every time the owner interrupts is worse than
        # the interruption.
        if message.get("stopReason") == "error":
            self._error = str(message.get("errorMessage")
                              or "the harness turn failed with no reason given")
        return [TurnEnd(text=_text_of(message))]

    def _response(self, obj: dict) -> list[HarnessEvent]:
        """pi's answer to a command: this prompt's acceptance, or its refusal.

        The id is the whole point. A response for some OTHER prompt says
        nothing about this one, and only `prompt` and `parse` can end a turn:
        a refused prompt produces no `agent_start`, no turn and no `agent_end`
        ever, so an adapter watching only the event stream would wait out a
        turn the child had already declined.
        """
        if (obj.get("command") not in ("prompt", "parse")
                or str(obj.get("id")) != self._prompt_id):
            return []
        if obj.get("success"):
            self._accepted = True
            return []
        self._error = (f"pi refused the prompt: "
                       f"{obj.get('error') or 'no reason given'}")
        return self.terminal(force=True)


def _text_of(message: dict) -> str:
    """The text blocks of one message, in order.

    This is the whole answer on a provider that does not stream -- measured,
    one emitted zero deltas and put everything here -- and AC-47 rides on it.
    Tool-call blocks are skipped: they are reported as tool events and
    reading a JSON argument list aloud is not an answer.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    blocks = [str(b.get("text") or "") for b in (content or [])
              if isinstance(b, dict) and b.get("type") == "text"]
    # A paragraph break between blocks, the same one `_Mapper._close_text`
    # puts between their deltas: on a non-streaming provider this is the whole
    # answer, and the two paths must read the same.
    return "\n\n".join(b for b in blocks if b).strip()


async def _spawn(argv: list[str], cwd: str):
    """The only place a real process is created, so tests can script one.

    `limit` is the StreamReader's buffer, not a record cap -- `_Framer` does
    the splitting -- but a single read must still be able to hold a large
    tool result.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
    # `proc.pid` is cmd.exe -- pi is pi.cmd -- and node appears underneath it
    # a moment later, so the wrapper is assigned immediately and the job
    # covers the descendant (AC-36, D-24). `dispose` is the clean path and
    # cannot be this guard: nothing in this file runs when butlerd is
    # hard-killed. Today pi also exits on stdin EOF; that is one child's
    # courtesy, not a rule, and it says nothing about a child that has
    # stopped reading its stdin.
    childproc.assign(proc)
    return proc


class _Child:
    """One pi process and everything that belongs to it alone.

    Per-child buffers are a rule rather than tidiness (plan.md 4.4c): a
    killed child keeps emitting on Windows, and a fragment of its last line
    prefixing the successor's first one fails to parse and ends a turn never.
    """

    def __init__(self, proc, spec: HarnessSpec) -> None:
        self.proc = proc
        self.spec = spec
        self.queue: asyncio.Queue = asyncio.Queue()
        self.framer = _Framer()
        self.mapper = _Mapper()
        self.waiting: dict[str, asyncio.Future] = {}
        # Every dialog this child has raised and nobody has answered yet:
        # pi's own id -> pi's own method (AC-234). Per CHILD, like every other
        # buffer here and for the same reason -- a killed child keeps emitting
        # on Windows, and an answer routed to a dead child's dialog id would
        # reach its successor as an id it never raised. `answer()` looks here
        # before it writes anything, so "does this child still hold that
        # question" is one lookup rather than a guess (AC-250).
        self.asked: dict[str, str] = {}
        self.stderr: collections.deque = collections.deque(maxlen=50)
        self.tasks: list[asyncio.Task] = []
        self.seq = 0

    def next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"butler-{prefix}-{self.seq}"

    def tail(self) -> str:
        return " | ".join(self.stderr) if self.stderr else "(no output)"


@ports.register(ports.HARNESSES, "pi")
class PiHarness:
    """`pi --mode rpc`. How long the child lives is the CALLER's business.

    Constructor kwargs are pi's own vocabulary and never cross the port
    (AC-41): `provider` and `model` are what a spec that names no model falls
    back to, and `append_system_prompt` is how a caller shapes the agent
    without teaching the daemon pi's flags. A spec's model always wins -- it
    is the owner's choice for this child, and the constructor's is a default
    from config.
    """

    def __init__(self, provider: str | None = None, model: str | None = None,
                 append_system_prompt: str | None = None,
                 startup_timeout_s: float = STARTUP_TIMEOUT_S,
                 command_timeout_s: float = COMMAND_TIMEOUT_S) -> None:
        # Fail fast at CONSTRUCTION on a contradictory option rather than at
        # first use (plan.md 4.4c): the predecessor's unvalidated model spec
        # became a child that died at startup, which reads as the harness
        # being broken rather than the setting being wrong.
        for name, value in (("provider", provider), ("model", model),
                            ("append_system_prompt", append_system_prompt)):
            if value is None:
                continue
            if not str(value).strip():
                raise ValueError(
                    f"{name}={value!r}: None means 'let pi decide', and a "
                    f"blank string is a second spelling of it that ends up on "
                    f"the command line as an empty argument")
            reject_shell_metacharacters(name, value)
        self._provider = provider
        self._model = model
        self._append_system_prompt = append_system_prompt
        self._startup_timeout_s = startup_timeout_s
        self._command_timeout_s = command_timeout_s
        self._child: _Child | None = None
        self._caps = HarnessCaps(
            # pi resumes by being pointed at its own session FILE, not by an
            # opaque id handed back to the caller. That difference is read
            # from caps and never from the name (AC-35, AC-44).
            native_resume=False,
            steerable=True,        # rpc.md: `steer` delivers mid-run
            images=True,           # rpc.md: prompt takes ImageContent blocks
        )

    # -- lifecycle --------------------------------------------------------- #
    async def start(self, spec: HarnessSpec) -> None:
        if self._child is not None:
            raise RuntimeError(
                "start() on a live child. Changing what a child IS means "
                "dispose() first, because cwd, model and session file are all "
                "fixed at spawn (AC-31)")
        proc = await _spawn(self._argv(spec), spec.cwd)
        child = _Child(proc, spec)
        self._child = child
        child.tasks.append(asyncio.create_task(self._read_stdout(child)))
        child.tasks.append(asyncio.create_task(self._read_stderr(child)))
        try:
            # Ready is not "the process exists": pi loads extensions before it
            # will take a prompt, and a caller that prompted before it was
            # ready would be told the child is fine and then wait forever.
            # Most of the 1.5-2.3 s a spawn costs (AC-59) is spent here.
            # This command is answered when the child is actually listening.
            await self._command(child, {"type": "get_state"},
                                self._startup_timeout_s)
            # Butler owns the retry policy (4.4c: replay once, from the
            # daemon). pi's own retry emits a whole agent_end per attempt.
            await self._command(child,
                                {"type": "set_auto_retry", "enabled": False},
                                self._command_timeout_s)
        except Exception:
            await self.dispose()
            raise

    def _argv(self, spec: HarnessSpec) -> list[str]:
        """Launched through `PI` (`cmd /c pi` on Windows, see `pi_command`).

        Every value the DAEMON put in the spec is checked here, not only the
        constructor's: the session file is a path Butler builds, and from 7.7
        a spoken project name decides the working directory. `cmd` re-parses
        this line, so an unchecked value is a command.

        `spec.tools` drives the ONE tool flag: a non-empty allow-list becomes
        `--tools a,b,c` (pi's documented read-only mode when it is
        `read,grep,find,ls`), and an EMPTY tuple builds no flag at all -- pi's
        own default set, `bash` included. `--tools ""` and no flag are
        different instructions, so the empty case must emit nothing rather than
        an empty value. This is the branch 653a44d deleted, re-added on
        2026-08-18 and driven by the `harness_tools` SETTING, not the spoken
        grant AC-29 retired. NOTE, REVISED 2026-09-26: on 2026-08-18 `--tools`
        gated pi's BUILT-IN tools only and an installed extension could still
        shell out under read_only [MEASURED then]. pi 0.87 documents the
        allow-list as applying to extension tools too, and it does: with
        `--tools read` the memory extension's `memory_search` was NOT offered
        to the model [MEASURED 2026-09-26, docs/identity-memory.md 12.6] --
        which is why read_only's list must name the memory tools. Whether
        every installed extension is gated the same way was not re-measured.
        """
        session = reject_shell_metacharacters("spec.session_file",
                                               spec.session_file)
        argv = [*PI, "--mode", "rpc", "--session", session]
        model = spec.model or self._model
        if model:
            argv += ["--model", reject_shell_metacharacters("model", model)]
        if self._provider:
            argv += ["--provider", self._provider]
        if spec.tools:
            # An ALLOW-list, comma-joined into pi's `--tools`. Empty means NO
            # flag (pi's default is everything), never `--tools ""` -- those
            # are different instructions and the empty one restricts to nothing.
            argv += ["--tools", reject_shell_metacharacters(
                "spec.tools", ",".join(spec.tools))]
        if self._append_system_prompt:
            argv += ["--append-system-prompt", self._append_system_prompt]
        if spec.append_prompt:
            # A PATH Butler minted and never the text (AC-373): pi reads a
            # file path given here as content [MEASURED 2026-09-26,
            # docs/identity-memory.md 3]. Checked like every spec value,
            # because `cmd` re-parses this line. NEVER `--no-context-files`
            # beside it: the project's own AGENTS.md is per-project identity
            # (AC-376).
            argv += ["--append-system-prompt", reject_shell_metacharacters(
                "spec.append_prompt", spec.append_prompt)]
        if spec.memory_url:
            # The memory tools (AC-440). Both values are checked like every
            # other spec value, because `cmd` re-parses this line. The
            # allow-list half -- `memory_search,memory_write` in read_only's
            # `--tools` -- is the resolver's, which built `spec.tools`.
            argv += ["--extension", reject_shell_metacharacters(
                         "memory extension", str(MEMORY_EXTENSION)),
                     "--butler-url", reject_shell_metacharacters(
                         "spec.memory_url", spec.memory_url)]
            if spec.chat_id:
                # Which conversation this child serves, so `task_add` files
                # under ITS project (docs/automations.md AU-9, AC-454).
                argv += ["--butler-chat", reject_shell_metacharacters(
                    "spec.chat_id", spec.chat_id)]
        return argv

    async def dispose(self) -> None:
        """Idempotent, never raises, and it ENDS the subscribed stream."""
        # The handle is nulled FIRST so that anything already in flight takes
        # the superseded path instead of driving a child that is being killed
        # (plan.md 4.4c).
        child, self._child = self._child, None
        if child is None:
            return
        child.queue.put_nowait(_END_OF_STREAM)
        for future in child.waiting.values():
            if not future.done():
                future.set_exception(RuntimeError("the child was disposed"))
        child.waiting.clear()
        try:
            await self._close_child(child)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: disposing the pi child raised {e!r}",
                  file=sys.stderr, flush=True)
        for task in child.tasks:
            task.cancel()
        child.tasks.clear()

    async def _close_child(self, child: _Child) -> None:
        proc = child.proc
        if proc.returncode is not None:
            return
        try:
            proc.stdin.close()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(proc.wait(), DISPOSE_TIMEOUT_S)
            return
        except asyncio.TimeoutError:
            pass
        except Exception:                                   # noqa: BLE001
            return
        # Kill the TREE. `cmd /c` is the parent of the node process that does
        # the work, so terminating what we spawned would leave pi running
        # (the same lesson tts_kokoro's sidecar taught, AC-36).
        await asyncio.to_thread(childproc.kill_tree, proc.pid)
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
                return                      # disposed, or a superseded child
            yield item

    async def _read_stdout(self, child: _Child) -> None:
        """The one reader, and the one place a prompt's ending comes from.

        The close handling is in a `finally` because this task dying is the
        one failure that produces NO terminal event at all -- the
        predecessor's held-turn-lock defect exactly (plan.md 4.4c, AC-48).
        Whatever takes the reader down, the prompt still ends and the stream
        still closes, so the caller hears something rather than waiting on a
        microphone that reads as dead (D-1).
        """
        try:
            while True:
                chunk = await child.proc.stdout.read(READ_CHUNK)
                # Re-checked after every await (plan.md 4.4c): a reader parked
                # on a read can otherwise wake up and feed a stream that was
                # disposed while it slept.
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
            print(f"[butler] WARNING: the pi reader stopped on {e!r}; the "
                  f"prompt is being ended", file=sys.stderr, flush=True)
        finally:
            await self._on_close(child)

    def _handle(self, child: _Child, record: str) -> None:
        """One record, and a record can never cost the turn its ending.

        A well-formed JSON record can still be shaped in a way pi's own
        vocabulary does not describe -- a string where an object belongs --
        and mapping it raises. Skipping it loudly costs one event; letting it
        out of here would kill the reader above (AC-48, D-1).
        """
        try:
            self._on_record(child, record)
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: pi sent a record this adapter could not "
                  f"handle ({e!r}): {record[:200]!r}",
                  file=sys.stderr, flush=True)

    async def _read_stderr(self, child: _Child) -> None:
        """Drained, kept, and never a verdict on its own.

        Drained because an undrained pipe fills and the child blocks; kept
        because pi's reason for dying is on it; not a verdict because stdout
        and stderr are different pipes and ruling on the first error line is
        a race (plan.md 4.4c).
        """
        while True:
            line = await child.proc.stderr.readline()
            if child is not self._child or not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                child.stderr.append(text)

    def _on_record(self, child: _Child, record: str) -> None:
        if not record.strip():
            return
        try:
            obj = json.loads(record)
        except json.JSONDecodeError as e:
            # Loud, and not fatal to the stream: one unreadable record costs
            # one event, while raising in the reader would cost the turn its
            # ending (D-1).
            print(f"[butler] WARNING: pi sent an unreadable record "
                  f"({e}): {record[:200]!r}", file=sys.stderr, flush=True)
            return
        if not isinstance(obj, dict):
            return
        if obj.get("type") == "response":
            future = child.waiting.pop(str(obj.get("id")), None)
            if future is not None and not future.done():
                future.set_result(obj)
        if obj.get("type") == "extension_ui_request":
            reply = cancellation_for(obj, child.mapper.pending)
            if reply is not None:
                child.tasks.append(
                    asyncio.create_task(self._write(child, reply)))
                return
            if obj.get("method") in DIALOG_METHODS:
                # A question for a human (AC-234). It goes on the QUEUE
                # directly rather than through `_Mapper.feed`, because the
                # mapper's whole job is pi's agent-loop vocabulary for the
                # prompt in flight, and this record is neither -- it is the
                # extension UI protocol, and the gate it needs
                # (`mapper.pending`) has already been applied by
                # `cancellation_for` one line up.
                ask = ask_for(obj)
                child.asked[ask.qid] = ask.method
                child.queue.put_nowait(ask)
            return
        for event in child.mapper.feed(obj):
            child.queue.put_nowait(event)

    async def _on_close(self, child: _Child) -> None:
        """The child's stdout ended: it is gone, and so is any prompt.

        A prompt whose child died must still end, or the caller waits forever
        for an answer that cannot arrive -- indistinguishable from a dead
        microphone (D-1, AC-48). The verdict is taken HERE, at the close,
        with whatever stderr had to say by then.
        """
        if child is not self._child:
            return
        # `wait()` so the return code is settled rather than guessed at.
        try:
            await asyncio.wait_for(child.proc.wait(), DISPOSE_TIMEOUT_S)
        except Exception:                                   # noqa: BLE001
            pass
        if child is not self._child:
            return
        reason = (f"the pi child exited (rc={child.proc.returncode}): "
                  f"{child.tail()}")
        for event in child.mapper.closed(reason):
            child.queue.put_nowait(event)
        for future in child.waiting.values():
            if not future.done():
                future.set_exception(RuntimeError(reason))
        child.waiting.clear()
        # Every outstanding question dies with the child that asked it
        # (AC-250's "the child died"). Cleared rather than left, so a late
        # answer is refused here instead of being written onto a dead pipe.
        child.asked.clear()
        child.queue.put_nowait(_END_OF_STREAM)

    # -- driving it -------------------------------------------------------- #
    async def prompt(self, text: str) -> None:
        child = self._child
        if child is None or child.proc.returncode is not None:
            raise RuntimeError(
                "prompt() with no live child. Failing here rather than "
                "queueing, because a prompt that goes nowhere is a turn that "
                "never ends")
        # Marked in flight BEFORE the write, so a child that answers instantly
        # cannot have its terminal event dropped as unsolicited. The id is
        # KEPT rather than minted and thrown away: it is what tells this
        # prompt's ending from the previous prompt's, which pi can still be
        # emitting (AC-48).
        prompt_id = child.next_id("prompt")
        child.mapper.prompt_sent(prompt_id)
        await self._write(child, {"id": prompt_id, "type": "prompt",
                                  "message": text})

    async def answer(self, qid: str, answer: ports.Answer) -> bool:
        """Unblock a dialog this child raised. False if it no longer holds it.

        Never hopeful (AC-250): pi drops an unknown `extension_ui_response`
        id SILENTLY, so writing one anyway would report success to a human
        whose answer changed nothing, which is the recorded Codex failure.
        The lookup is `child.asked`, so a disposed or replaced child, a
        dialog pi already resolved on its own timeout, and an id nobody
        raised all answer the same way -- False, and the CALLER says which.
        """
        child = self._child
        if child is None or qid not in child.asked:
            return False
        method = child.asked.pop(qid)
        await self._write(child, {"type": "extension_ui_response", "id": qid,
                                  **_pi_answer(method, answer)})
        return True

    async def abort(self) -> None:
        """Barge-in. pi settles it in 1.51 s and stays usable [MEASURED], and
        the aborted prompt still ends with its own terminal event.

        Inert when nothing is running: barge-in races completion by nature,
        and raising on a turn that had just ended would turn a routine race
        into a failed abandon, which is a held microphone (D-1).
        """
        child = self._child
        if child is None or not child.mapper.pending:
            return
        try:
            await self._write(child, {"type": "abort"})
        except Exception as e:                              # noqa: BLE001
            print(f"[butler] WARNING: the pi abort did not reach the child "
                  f"({e!r})", file=sys.stderr, flush=True)

    async def _command(self, child: _Child, command: dict,
                       timeout_s: float) -> dict:
        """One control command, awaited by id. Never used for a prompt."""
        request_id = child.next_id(command["type"])
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        child.waiting[request_id] = future
        await self._write(child, {**command, "id": request_id})
        try:
            answer = await asyncio.wait_for(future, timeout_s)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"pi did not answer {command['type']} in {timeout_s:.0f}s; "
                f"stderr: {child.tail()}") from None
        finally:
            child.waiting.pop(request_id, None)
        if child is not self._child:
            raise RuntimeError("the child was replaced while it was starting")
        if not answer.get("success"):
            raise RuntimeError(
                f"pi refused {command['type']}: {answer.get('error')}")
        return answer

    async def _write(self, child: _Child, obj: dict) -> None:
        if child is not self._child:
            return
        child.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await child.proc.stdin.drain()

    # -- what it is -------------------------------------------------------- #
    @classmethod
    async def list_models(cls) -> list[str]:
        """Every `provider/model` pi could be pointed at (`harness_model`).

        pi's OWN registry, read with `pi --offline --list-models`: no HTTP, no
        connection, and nothing the daemon has to understand -- it asks for a
        list and gets one (AC-41). `--offline` because this must not become a
        network call on the settings page.

        `provider/model` rather than a bare id, which is what `--model`
        documents and what `_argv` passes through: 532 rows here carry the
        same model id under several providers [MEASURED 2026-08-18], and a
        picker offering `gpt-5.4-mini` three times would be asking the owner
        to choose between three identical strings.

        A CLASSMETHOD, so the picker answers before any child exists, and
        deliberately NOT part of `info` -- that is read on the event loop
        during every satellite handshake and must never shell out (D-25).
        """
        now = time.monotonic()
        if _model_cache and (now - _model_cache[0]) < MODELS_TTL_S:
            return list(_model_cache[1])
        proc = await asyncio.create_subprocess_exec(
            *PI, "--offline", "--list-models",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(),
                                              MODELS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(
                f"pi did not list its models within {MODELS_TIMEOUT_S:.0f}s"
            ) from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"pi --offline --list-models exited {proc.returncode}: "
                f"{(err or out).decode('utf-8', 'replace')[-200:]}")
        # BOTH pipes. pi prints the table on stderr [MEASURED 2026-08-18], and
        # an adapter reading only stdout would report that pi knows no models
        # at all -- an empty picker that looks like a configuration problem.
        found = _parse_model_table(
            (out + b"\n" + err).decode("utf-8", "replace"))
        if not found:
            raise RuntimeError(
                "pi listed no models this adapter could read; the registry is "
                "empty or the table's shape has changed")
        _model_cache[:] = [now, found]
        return list(found)

    @property
    def caps(self) -> HarnessCaps:
        return self._caps

    @property
    def info(self) -> dict:
        """Static, and it asks the child NOTHING (D-25).

        This is the one port whose adapter IS a child process, and `info` is
        read on the event loop during the satellite handshake: a question put
        to the child here would freeze every satellite for as long as the
        child took to answer.
        """
        child = self._child
        return {"engine": "pi",
                "provider": self._provider,
                "model": (child.spec.model if child else None) or self._model,
                "running": child is not None,
                "cwd": child.spec.cwd if child else None}
