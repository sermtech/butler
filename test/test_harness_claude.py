"""Claude Code behind the Harness port (docs/chats-and-terminals.md CT-7,
AC-770). The mapping is driven by a run RECORDED from the real CLI
(`test/assets/harness/claude-multi-tool-run.jsonl`, claude-code 2.1.280,
recorded by an earlier prototype); the transport by `harness_replay.ClaudeProc`, which
replays the same lines. The conformance suite holds the port contract; this
file holds what is Claude Code's own.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import chats as chats_mod  # noqa: E402
from daemon import ports  # noqa: E402
from daemon.adapters import harness_claude  # noqa: E402
from daemon.adapters.harness_claude import _Mapper  # noqa: E402
from daemon.ports import (Activity, AgentEnd, Failed, HarnessSpec,  # noqa: E402
                          TERMINAL_EVENTS, TextDelta, ToolEnd, ToolStart,
                          TurnEnd)
import harness_replay  # noqa: E402
from harness_replay import (CLAUDE_RUN, ClaudeProc, records,  # noqa: E402
                            claude_failed_result, claude_interrupted_result,
                            claude_dead_resume_result)

EVENT_TIMEOUT = 5.0
RECORDED_SESSION = records(CLAUDE_RUN)[0]["session_id"]


def _map(lines, mapper=None, prompt=True):
    mapper = mapper or _Mapper()
    if prompt:
        mapper.prompt_sent()
    out = []
    for obj in lines:
        out += mapper.feed(obj)
    return out


def _terminals(events):
    return [e for e in events if isinstance(e, TERMINAL_EVENTS)]


# -- the pure mapping ------------------------------------------------------- #
def test_the_recorded_run_maps_to_the_normalized_events():
    """Two messages, four tools (one FAILED), one result: AC-48's one ending,
    the harness's own tool ids, the real outcome, and a TurnEnd per message."""
    events = _map(records(CLAUDE_RUN))

    starts = [e for e in events if isinstance(e, ToolStart)]
    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert [s.tool_id for s in starts] == [
        "toolu_01N7gfKBc1vfqDZTief3VhGH", "toolu_01MTqcnX2tYqdxmSbQ2L4LuA",
        "toolu_019gpE7Hx49C4dAAL7Z5XoaT", "toolu_01NZmhaQrDGjn8s8hSpDewnR"]
    assert all(s.name for s in starts)
    outcome = {e.tool_id: e.is_error for e in ends}
    # true, false and ABSENT in the recording: absent means ok.
    assert outcome == {"toolu_01N7gfKBc1vfqDZTief3VhGH": False,
                       "toolu_01NZmhaQrDGjn8s8hSpDewnR": True,
                       "toolu_019gpE7Hx49C4dAAL7Z5XoaT": False,
                       "toolu_01MTqcnX2tYqdxmSbQ2L4LuA": False}
    assert {e.tool_id: e.name for e in ends} == {
        s.tool_id: s.name for s in starts}

    turns = [e.text for e in events if isinstance(e, TurnEnd)]
    assert len(turns) == 2
    assert turns[0].startswith("I'll run the ping")
    assert turns[1].startswith("Three of the four steps succeeded")

    terminals = _terminals(events)
    assert terminals == [AgentEnd()] and events[-1] is terminals[0]


def test_the_deltas_are_the_turns_joined_by_a_paragraph_break():
    """`TextDelta`s concatenate VERBATIM (ports.TextDelta); the break between
    the text either side of the tools is the adapter's to emit."""
    events = _map(records(CLAUDE_RUN))
    deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
    turns = [e.text for e in events if isinstance(e, TurnEnd)]
    assert deltas.strip() == "\n\n".join(turns)
    assert "other.\n\n" in deltas


def test_the_break_is_emitted_when_the_block_ends_not_after_the_tools():
    """Latency (harness_pi `_close_text`): the sentence before a tool call is
    released when its block stops, before the tool runs."""
    events = _map(records(CLAUDE_RUN))
    first_start = next(i for i, e in enumerate(events)
                       if isinstance(e, ToolStart))
    assert TextDelta(text="\n\n") in events[:first_start]


def test_thinking_and_tool_arguments_are_activity_never_text():
    """Redacted thinking and tool-argument streaming re-arm the idle deadline
    (AC-388) and are never spoken."""
    events = _map(records(CLAUDE_RUN))
    kinds = {e.kind for e in events if isinstance(e, Activity)}
    assert kinds == {"thinking", "tool_input"}


def test_nothing_is_mapped_without_a_prompt_in_flight():
    """AC-48: a stray line before a prompt or after its ending is no answer."""
    mapper = _Mapper()
    assert _map(records(CLAUDE_RUN), mapper, prompt=False) == []
    _map(records(CLAUDE_RUN), mapper)
    assert _map(records(CLAUDE_RUN), mapper, prompt=False) == []


def test_a_second_result_cannot_end_one_prompt_twice():
    """AC-48's guard: one prompt, one terminal event, whatever the child says."""
    lines = records(CLAUDE_RUN)
    events = _map(lines + [lines[-1], claude_failed_result()])
    assert len(_terminals(events)) == 1


def test_a_failed_result_is_failed_with_its_reason():
    events = _map([records(CLAUDE_RUN)[0], claude_failed_result()])
    assert len(_terminals(events)) == 1
    assert isinstance(events[-1], Failed)
    assert "API Error: 529" in events[-1].message


def test_an_interrupted_turn_is_not_a_failure_and_says_nothing_more():
    """Barge-in (D-4): the interrupt's is_error result is AgentEnd, and the
    words the child had in flight are not spoken."""
    mapper = _Mapper()
    mapper.prompt_sent()
    lines = records(CLAUDE_RUN)
    before = []
    for obj in lines[:15]:
        before += mapper.feed(obj)
    mapper.abort_requested()
    after = []
    for obj in lines[15:-1] + [claude_interrupted_result()]:
        after += mapper.feed(obj)
    assert after == [TextDelta(text="\n\n"), AgentEnd()]


def test_a_dead_resume_is_ruled_on_at_the_close_with_stderr():
    """The lone is_error result of a refused `--resume` has no reason in it;
    the CLI's words are on stderr, read at the close. Those words are what
    `brain_harness` replays on (AC-35)."""
    mapper = _Mapper(resumed=True)
    mapper.prompt_sent()
    assert mapper.feed(claude_dead_resume_result()) == []
    assert mapper.pending
    events = mapper.closed(
        "rc=1: No conversation found with session ID: x")
    assert len(events) == 1 and isinstance(events[0], Failed)
    assert chats_mod.names_a_missing_session(events[0].message)


def test_a_first_spawn_error_result_is_not_deferred():
    """With no `--resume` there is no dead session to wait for."""
    events = _map([claude_dead_resume_result()], _Mapper(resumed=False))
    assert len(events) == 1 and isinstance(events[0], Failed)


def test_a_child_that_dies_mid_prompt_still_ends_it_once():
    mapper = _Mapper()
    mapper.prompt_sent()
    for obj in records(CLAUDE_RUN)[:20]:
        mapper.feed(obj)
    events = mapper.closed("the claude child exited (rc=3)")
    assert _terminals(events) == [Failed(message="the claude child exited "
                                                  "(rc=3)")]
    assert mapper.closed("again") == []


# -- the transport, over replayed children --------------------------------- #
def _spec(tmp_path, **fields):
    return HarnessSpec(**{"model": None, "cwd": str(tmp_path),
                          "session_file": str(tmp_path / "c.session.json"),
                          **fields})


async def _drain(stream):
    seen = []
    while True:
        event = await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
        seen.append(event)
        if isinstance(event, TERMINAL_EVENTS):
            return seen


@pytest.mark.asyncio
async def test_the_spawn_line_is_prototypes(monkeypatch, tmp_path):
    """OD-1 (owner): bypassPermissions, the cwd added, the prompt on STDIN as
    one stream-json user line, and no `--resume` for a fresh conversation."""
    spawned = harness_replay.install_claude(monkeypatch)
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(_spec(tmp_path))
    try:
        argv = spawned[0].argv
        assert argv[:2] == ["claude", "--print"]
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in argv and "--include-partial-messages" in argv
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
        assert spawned[0].cwd == str(tmp_path)
        for flag in ("--resume", "--model", "--tools"):
            assert flag not in argv
        stream = harness.subscribe()
        await harness.prompt("what is here & why?")
        await _drain(stream)
        assert spawned[0].written[0] == {
            "type": "user",
            "message": {"role": "user", "content": "what is here & why?"}}
    finally:
        await harness.dispose()


@pytest.mark.asyncio
async def test_the_session_id_is_kept_in_the_session_file_and_resumed(
        monkeypatch, tmp_path):
    """AC-35/AC-44: the id Claude Code prints at init is kept INSIDE the file
    Butler minted, and the next child of the conversation resumes it."""
    spawned = harness_replay.install_claude(monkeypatch)
    spec = _spec(tmp_path)
    first = ports.build(ports.HARNESSES, "claude")
    await first.start(spec)
    try:
        stream = first.subscribe()
        await first.prompt("hello")
        await _drain(stream)
    finally:
        await first.dispose()
    assert json.loads(Path(spec.session_file).read_text()) == {
        "session_id": RECORDED_SESSION}

    second = ports.build(ports.HARNESSES, "claude")
    await second.start(spec)
    await second.dispose()
    argv = spawned[1].argv
    assert argv[argv.index("--resume") + 1] == RECORDED_SESSION


@pytest.mark.asyncio
@pytest.mark.parametrize("tools,expected", [
    ((), None),
    (("read", "grep", "find", "ls"), ["--tools", "Read,Grep,Glob",
                                      "--strict-mcp-config"]),
])
async def test_harness_tools_read_only_is_claudes_read_set(
        monkeypatch, tmp_path, tools, expected):
    """`harness_tools`: full is no flag, read_only is Claude Code's OWN read
    tools plus `--strict-mcp-config` (pi's names mean nothing to it)."""
    spawned = harness_replay.install_claude(monkeypatch)
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(_spec(tmp_path, tools=tools))
    await harness.dispose()
    argv = spawned[0].argv
    if expected is None:
        assert "--tools" not in argv and "--strict-mcp-config" not in argv
    else:
        i = argv.index("--tools")
        assert argv[i:i + 3] == expected


@pytest.mark.asyncio
async def test_the_persona_file_and_model_reach_the_child(monkeypatch,
                                                          tmp_path):
    """AC-373: the persona is a FILE path, never argv text."""
    spawned = harness_replay.install_claude(monkeypatch)
    persona = tmp_path / "persona.md"
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(_spec(tmp_path, model="sonnet",
                              append_prompt=str(persona)))
    await harness.dispose()
    argv = spawned[0].argv
    assert argv[argv.index("--append-system-prompt-file") + 1] == str(persona)
    assert argv[argv.index("--model") + 1] == "sonnet"


@pytest.mark.asyncio
async def test_abort_interrupts_and_the_child_answers_again(monkeypatch,
                                                            tmp_path):
    """The interrupt goes on stdin, the prompt ends ONCE as AgentEnd, and the
    same process answers the next prompt (an earlier prototype, MEASURED 2.1.294)."""
    spawned = harness_replay.install_claude(monkeypatch)
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(_spec(tmp_path))
    try:
        stream = harness.subscribe()
        await harness.prompt("read everything")
        await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
        await harness.abort()
        seen = await _drain(stream)
        assert seen[-1] == AgentEnd()
        assert spawned[0].written[-1]["type"] == "control_request"
        assert spawned[0].written[-1]["request"] == {"subtype": "interrupt"}
        assert spawned[0].returncode is None
        again = await _drain_after(harness, stream, "again")
        assert len(_terminals(again)) == 1
        assert any(isinstance(e, TurnEnd) for e in again)
        assert len(spawned) == 1
    finally:
        await harness.dispose()


async def _drain_after(harness, stream, text):
    await harness.prompt(text)
    return await _drain(stream)


class _DeafClaude(ClaudeProc):
    """Acknowledges nothing: anthropics/claude-code #98713 (an earlier prototype)."""

    def write(self, raw: bytes) -> None:
        for line in raw.decode("utf-8").split("\n"):
            if line.strip() and json.loads(line).get("type") == "control_request":
                self.written.append(json.loads(line))
                return
        super().write(raw)


@pytest.mark.asyncio
async def test_an_ignored_interrupt_kills_the_tree_and_still_ends_once(
        monkeypatch, tmp_path):
    spawned: list = []

    async def _spawn(argv, cwd):
        proc = _DeafClaude(argv, cwd)
        spawned.append(proc)
        return proc

    async def _kill(proc):
        proc.kill()

    monkeypatch.setattr(harness_claude, "_spawn", _spawn)
    monkeypatch.setattr(harness_claude, "_kill", _kill)
    monkeypatch.setattr(harness_replay, "LINE_DELAY_S", 0.05)
    harness = ports.build(ports.HARNESSES, "claude", interrupt_grace_s=0.1)
    await harness.start(_spec(tmp_path))
    try:
        stream = harness.subscribe()
        await harness.prompt("read everything")
        await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
        await harness.abort()
        seen = await _drain(stream)
        assert _terminals(seen) == [AgentEnd()]
        assert spawned[0].returncode == 1          # killed, not exited
    finally:
        await harness.dispose()


@pytest.mark.asyncio
async def test_a_dead_session_is_forgotten_and_named(monkeypatch, tmp_path):
    """AC-35: the CLI's own words reach the Failed message (so the turn is
    replayed) and the stored id is dropped (so the replay starts fresh)."""
    spawned = harness_replay.install_claude(monkeypatch, dead_resume=True)
    spec = _spec(tmp_path)
    Path(spec.session_file).write_text(
        json.dumps({"session_id": harness_replay.DEAD_SESSION}))
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(spec)
    try:
        assert harness_replay.DEAD_SESSION in spawned[0].argv
        stream = harness.subscribe()
        await harness.prompt("hello")
        seen = await _drain(stream)
    finally:
        await harness.dispose()
    assert len(_terminals(seen)) == 1 and isinstance(seen[-1], Failed)
    assert chats_mod.names_a_missing_session(seen[-1].message)
    assert not Path(spec.session_file).exists()


@pytest.mark.asyncio
async def test_an_unreadable_session_file_refuses_to_start(monkeypatch,
                                                           tmp_path):
    """Fail fast: a fresh session in its place would forget the conversation
    with nothing saying so."""
    spawned = harness_replay.install_claude(monkeypatch)
    spec = _spec(tmp_path)
    Path(spec.session_file).write_text("{not json")
    harness = ports.build(ports.HARNESSES, "claude")
    with pytest.raises(RuntimeError, match="unreadable"):
        await harness.start(spec)
    assert spawned == []


@pytest.mark.asyncio
async def test_no_question_is_ever_held(monkeypatch, tmp_path):
    """bypassPermissions raises no Ask, so an answer is never forwarded
    hopefully (AC-250)."""
    harness_replay.install_claude(monkeypatch)
    harness = ports.build(ports.HARNESSES, "claude")
    await harness.start(_spec(tmp_path))
    try:
        assert await harness.answer("q1", ports.Answer(confirmed=True)) is False
    finally:
        await harness.dispose()
