"""opencode behind the Harness port (docs/chats-and-terminals.md CT-7,
AC-770, OD-2). The mapping is driven by runs RECORDED from opencode 1.19.3 on
this desk (2026-10-29, `test/assets/harness/opencode-*.jsonl`); the transport
by `harness_replay.OpenCodeProc`, one replayed process per prompt.
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
from daemon.adapters.harness_opencode import _Mapper  # noqa: E402
from daemon.ports import (AgentEnd, Failed, HarnessSpec,  # noqa: E402
                          TERMINAL_EVENTS, TextDelta, ToolEnd, ToolStart,
                          TurnEnd)
import harness_replay  # noqa: E402
from harness_replay import (ASSETS, OPENCODE_ERROR, OPENCODE_TOOLS,  # noqa: E402
                            records)

EVENT_TIMEOUT = 5.0
RECORDED_SESSION = records(OPENCODE_TOOLS)[0]["sessionID"]


def _map(lines, mapper=None, rc=0, stderr=""):
    mapper = mapper or _Mapper()
    mapper.prompt_sent()
    out = []
    for obj in lines:
        out += mapper.feed(obj)
    return out + mapper.exited(rc, stderr)


def _terminals(events):
    return [e for e in events if isinstance(e, TERMINAL_EVENTS)]


# -- the pure mapping ------------------------------------------------------- #
def test_the_recorded_tool_run_maps_to_the_normalized_events():
    """Two steps, two tools (the read FAILED), one ending at the exit."""
    events = _map(records(OPENCODE_TOOLS))
    starts = [e for e in events if isinstance(e, ToolStart)]
    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert [(s.tool_id, s.name) for s in starts] == [
        ("call_01_m9eoxoupkpgy95utouxnil44", "read"),
        ("call_00_kocalcpcw8tfg93fko7cekct", "bash")]
    assert {e.tool_id: e.is_error for e in ends} == {
        "call_01_m9eoxoupkpgy95utouxnil44": True,
        "call_00_kocalcpcw8tfg93fko7cekct": False}
    assert [e.text for e in events if isinstance(e, TurnEnd)] == [
        "I'll run both calls.", "done"]
    assert _terminals(events) == [AgentEnd()] and events[-1] == AgentEnd()


def test_the_deltas_are_the_turns_joined_by_a_paragraph_break():
    events = _map(records(OPENCODE_TOOLS))
    deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert deltas == "I'll run both calls.\n\ndone\n\n"


def test_an_error_event_ends_the_prompt_failed_once():
    """[MEASURED] an unknown model: one `error` record and rc=1."""
    events = _map(records(OPENCODE_ERROR), rc=1)
    assert len(_terminals(events)) == 1 and isinstance(events[-1], Failed)
    assert "UnknownError" in events[-1].message


def test_a_rejected_permission_is_a_failed_tool():
    """read_only (no `--auto`): opencode auto-rejects the ask and the tool
    ends as an error [MEASURED 2026-10-29]."""
    events = _map(records(ASSETS / "opencode-permission-rejected.jsonl"))
    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert [(e.name, e.is_error) for e in ends] == [("bash", True)]


def test_a_nonzero_exit_without_an_error_record_is_failed_with_stderr():
    events = _map([], rc=1, stderr="Error: Session not found")
    assert isinstance(events[-1], Failed)
    assert chats_mod.names_a_missing_session(events[-1].message)


def test_an_aborted_run_is_not_a_failure_and_says_nothing_more():
    mapper = _Mapper()
    mapper.prompt_sent()
    lines = records(OPENCODE_TOOLS)
    for obj in lines[:2]:
        mapper.feed(obj)
    mapper.abort_requested()
    after = []
    for obj in lines[2:]:
        after += mapper.feed(obj)
    assert after == []
    assert mapper.exited(1, "killed") == [AgentEnd()]


def test_one_prompt_ends_once():
    """AC-48's guard: a second exit, and anything after the ending, is
    nothing."""
    mapper = _Mapper()
    events = _map(records(OPENCODE_TOOLS), mapper)
    assert len(_terminals(events)) == 1
    assert mapper.exited(0, "") == []
    assert [e for obj in records(OPENCODE_TOOLS)
            for e in mapper.feed(obj)] == []


def test_a_part_is_mapped_once():
    """A part repeated on the wire is not a second answer."""
    lines = records(OPENCODE_TOOLS)
    events = _map(lines[:3] + lines[1:3] + lines[3:])
    assert len([e for e in events if isinstance(e, ToolStart)]) == 2
    assert len([e for e in events if isinstance(e, TurnEnd)]) == 2


# -- the transport, over replayed runs ------------------------------------- #
def _spec(tmp_path, **fields):
    return HarnessSpec(**{"model": None, "cwd": str(tmp_path),
                          "session_file": str(tmp_path / "o.session.json"),
                          **fields})


async def _drain(stream):
    seen = []
    while True:
        event = await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
        seen.append(event)
        if isinstance(event, TERMINAL_EVENTS):
            return seen


@pytest.mark.asyncio
async def test_each_prompt_is_one_run_continuing_one_session(monkeypatch,
                                                             tmp_path):
    """OD-2: `opencode run --format json`, the prompt on STDIN, `--auto`
    (OD-1), and the session id from the first run passed to the next."""
    spawned = harness_replay.install_opencode(monkeypatch)
    spec = _spec(tmp_path)
    harness = ports.build(ports.HARNESSES, "opencode")
    await harness.start(spec)
    try:
        assert spawned == []                       # start spawns nothing
        stream = harness.subscribe()
        await harness.prompt('say "hi" & exit')
        await _drain(stream)
        await harness.prompt("again")
        await _drain(stream)
    finally:
        await harness.dispose()
    first, second = spawned
    assert first.argv[-5:] == ["opencode", "run", "--format", "json",
                               "--auto"]
    assert "--session" not in first.argv
    assert first.written == ['say "hi" & exit']
    assert all('say "hi"' not in a for a in first.argv)
    assert json.loads(Path(spec.session_file).read_text()) == {
        "session_id": RECORDED_SESSION}
    assert second.argv[second.argv.index("--session") + 1] == RECORDED_SESSION
    assert first.cwd == second.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_read_only_is_the_plan_agent_without_auto(monkeypatch,
                                                        tmp_path):
    spawned = harness_replay.install_opencode(monkeypatch)
    harness = ports.build(ports.HARNESSES, "opencode")
    await harness.start(_spec(tmp_path, tools=("read", "grep"),
                              model="opencode/big-pickle"))
    try:
        stream = harness.subscribe()
        await harness.prompt("look")
        await _drain(stream)
    finally:
        await harness.dispose()
    argv = spawned[0].argv
    assert argv[argv.index("--agent") + 1] == "plan"
    assert "--auto" not in argv
    assert argv[argv.index("--model") + 1] == "opencode/big-pickle"


@pytest.mark.asyncio
async def test_a_dead_session_is_forgotten_and_named(monkeypatch, tmp_path):
    harness_replay.install_opencode(monkeypatch, missing_session=True)
    spec = _spec(tmp_path)
    Path(spec.session_file).write_text(json.dumps({"session_id": "ses_gone"}))
    harness = ports.build(ports.HARNESSES, "opencode")
    await harness.start(spec)
    try:
        stream = harness.subscribe()
        await harness.prompt("hello")
        seen = await _drain(stream)
    finally:
        await harness.dispose()
    assert len(_terminals(seen)) == 1 and isinstance(seen[-1], Failed)
    assert chats_mod.names_a_missing_session(seen[-1].message)
    assert not Path(spec.session_file).exists()


@pytest.mark.asyncio
async def test_abort_kills_the_run_and_the_next_prompt_continues(
        monkeypatch, tmp_path):
    spawned = harness_replay.install_opencode(monkeypatch)
    monkeypatch.setattr(harness_replay, "LINE_DELAY_S", 0.05)
    harness = ports.build(ports.HARNESSES, "opencode")
    await harness.start(_spec(tmp_path))
    try:
        stream = harness.subscribe()
        await harness.prompt("read everything")
        await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
        await harness.abort()
        seen = await _drain(stream)
        assert _terminals(seen) == [AgentEnd()]
        assert spawned[0].returncode == 1
        monkeypatch.setattr(harness_replay, "LINE_DELAY_S", 0.0)
        await harness.prompt("again")
        again = await _drain(stream)
        assert _terminals(again) == [AgentEnd()]
        assert "--session" in spawned[1].argv
    finally:
        await harness.dispose()


@pytest.mark.parametrize("model", ['m" & echo PWNED', "a|b", "x%PATH%"])
def test_a_shell_metacharacter_in_the_model_is_refused(model):
    """opencode runs under `cmd /c` on Windows (it is opencode.cmd)."""
    with pytest.raises(ValueError):
        ports.build(ports.HARNESSES, "opencode", model=model)


@pytest.mark.asyncio
async def test_a_missing_cli_fails_at_start(monkeypatch, tmp_path):
    from daemon.adapters import harness_opencode
    monkeypatch.setattr(harness_opencode, "_installed", lambda: False)
    harness = ports.build(ports.HARNESSES, "opencode")
    with pytest.raises(RuntimeError, match="not installed"):
        await harness.start(_spec(tmp_path))
