"""Every Harness adapter must pass this. See plan.md 4.10, 4.4c and AC-44.

Written against the fake FIRST, with no consumer anywhere in the daemon, for
the reason the Asr suite was written before its second implementation: a
suite written after `harness_pi` landed would describe pi rather than the
port, and the one thing this port has to survive is being pointed at a
different coding agent (docs/harness-mode.md step 7.1).

What a conformance suite can prove is that an adapter HOLDS the contract.
That a barge-in actually reaches the child, that the daemon announces once,
that a respawn carries the session file -- those are caller properties and
they live in `test_brain_harness.py`, `test_conversation.py` and
`test_project_switch.py` (D-18: three green-but-broken contracts in this
repository earned that split).
"""
import asyncio
import dataclasses
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import ports  # noqa: E402
from daemon.ports import (HARNESSES, Harness, HarnessCaps,  # noqa: F401,E402
                          HarnessSpec, TERMINAL_EVENTS)
from conftest import (PI_CONFORMANCE_PROMPT, pi_kwargs,  # noqa: E402
                      skip_without_pi)
import harness_replay  # noqa: E402

# The two events that END a prompt come from ports.py rather than a copy here
# (AC-48): `brain_harness` (7.4) and the Worker Manager (4.5) isinstance()
# against the same tuple, and two copies of a constant drift -- which is a
# test passing for the wrong reason (test/AGENTS.md).

# Nothing in this file waits forever. The failure this port is most likely to
# have is a terminal event that never arrives -- the predecessor emitted its
# error only on a JS exception, so a provider failure held the turn lock
# forever (plan.md 4.4c) -- and a suite that HANGS on it reports nothing at
# all: no output, no exit code, killed by whatever runs it. Five seconds is
# far above anything the fake needs; a real child's startup is paid in
# `start()` and never here.
EVENT_TIMEOUT = 5.0

# Per adapter, because a scripted answer and a model thinking are different
# things (D-5: the load budget and the per-operation budget are never the
# same watchdog). pi's is generous -- a bridged model took 2.3 s to its first
# word here and a local one takes 20 s -- and it is still a DEADLINE, which
# is the property that matters: this suite must fail rather than hang.
EVENT_TIMEOUTS = {"fake": 5.0, "pi": 60.0,
                  "claude-replay": 5.0, "opencode-replay": 5.0,
                  "claude": 120.0, "opencode": 120.0}

# Claude Code and opencode (docs/chats-and-terminals.md CT-7, AC-770), twice
# each. The REPLAY rows run the adapter's real transport -- framing, the
# one-terminal-event guard, abort, dispose, the session file -- against
# output RECORDED from the real CLI (`test/harness_replay.py`), so they are
# scripted and every property below is a contract for them. The LIVE rows
# talk to the real CLI, which costs the owner's subscription or provider
# quota per prompt and this suite sends about ten: they run only with
# BUTLER_LIVE_AGENTS=1 and the CLI installed, and SKIP otherwise -- an absent
# engine is never a failing contract.
REPLAY = {"claude-replay": ("claude", harness_replay.install_claude),
          "opencode-replay": ("opencode", harness_replay.install_opencode)}
LIVE_PAID = {"claude", "opencode"}

# One param per adapter, in the Asr suite's shape: (registry name, kwargs).
# pi is marked `slow` because every test that starts one pays a real child
# (2.7 s to a ready child on this machine, plus the model's own time), and it
# is here because an adapter is not done until it passes the same suite as the
# fake (adapters/AGENTS.md, AC-7a, AC-44).
#
# The kwargs are pi's own vocabulary and live in conftest beside the live
# test's, because both must talk to the same child. The system prompt among
# them is not decoration: the properties below -- several turns, a tool call
# that FAILS, text in every turn -- are scripted for the fake and have to be
# ASKED of a real agent, or this suite would be measuring the model's mood.
ADAPTERS = [
    pytest.param(("fake", {}), id="fake"),
    pytest.param(("pi", pi_kwargs()), id="pi", marks=[pytest.mark.slow]),
    pytest.param(("claude-replay", {}), id="claude-replay"),
    pytest.param(("opencode-replay", {}), id="opencode-replay"),
    pytest.param(("claude", {"model": "haiku",
                             "append_system_prompt": PI_CONFORMANCE_PROMPT}),
                 id="claude", marks=[pytest.mark.slow]),
    pytest.param(("opencode", {"model": "opencode/deepseek-v4.1-flash-free"}),
                 id="opencode", marks=[pytest.mark.slow]),
]


def _build(name: str, kwargs: dict):
    """Build an adapter, skipping the ones this machine does not have."""
    if name == "pi":
        skip_without_pi()
    if name in LIVE_PAID:
        if os.environ.get("BUTLER_LIVE_AGENTS") != "1":
            pytest.skip(f"the live {name} row costs real quota per prompt; "
                        f"set BUTLER_LIVE_AGENTS=1 to run it")
        if shutil.which(name) is None:
            pytest.skip(f"{name} is not installed on this machine")
    registry = REPLAY[name][0] if name in REPLAY else name
    return ports.build(ports.HARNESSES, registry, **kwargs)


# How to ask each adapter for a turn that emits NO text deltas. The fake
# scripts it; a real adapter only enters that state by being pointed at a
# provider that does not stream, which is a measured cloud one
# (docs/harness-mode.md section 1). An adapter with no such switch still runs
# the test in its ordinary mode, because the property under test -- turn_end
# carries the whole message -- holds either way.
#
# pi has NO entry, and that is a real gap rather than an omission: AC-47's
# no-delta recovery is never exercised against a live child. It needs a
# provider that streams nothing, and the one this project measured behaving
# that way -- claudebridge-local, docs/harness-mode.md section 1 -- is not
# answering on this machine (a `Connection error.` in 1.5 s, re-checked
# 2026-08-17), while codexbridge-local streams. Nothing here can fake it:
# whether text arrives as deltas is the provider's behaviour, not pi's, so
# the mapping half stays proven on the recorded lines in
# test/test_harness_pi.py::test_a_no_stream_provider_recovers_text_from_turn_end
# and this row is owed the day a non-streaming provider is reachable again.
NO_DELTA = {"fake": {"stream": False}}

# Which adapters SCRIPT their answer. For those, the shape the tests below
# need is a contract and a miss is a defect. A live agent has to be ASKED for
# the same shape (the row's `append_system_prompt`), and a model that does not
# comply is the model's mood, not a broken adapter -- a suite that failed
# there would be measuring the model on the day it ran (test/AGENTS.md).
SCRIPTED = {"fake", "claude-replay", "opencode-replay"}

# How to ask each adapter for a prompt that FAILS: the constructor kwargs that
# arm it, and the prompt that trips it. Unlike NO_DELTA there is no ordinary
# mode to fall back on -- an adapter with no failure route cannot reach
# `Failed` at all, and an unreachable terminal event is the predecessor's
# defect exactly (plan.md 4.4c). An adapter missing from this table SKIPS the
# test and owes the row.
#
# pi's row names a model that does not exist. Both worlds it can land in are
# the same failure: a provider that answers rejects the model id, and one that
# is not listening cannot be reached at all -- either way the turn carries
# `stopReason: "error"` and the prompt must end as `Failed` [MEASURED
# 2026-08-17: a 400 in 2.5 s, and a connection error in 1.5 s].
FAIL_ON = {"fake": {"kwargs": {"fail_on": "detonate"},
                    "prompt": "detonate the repository"},
           "pi": {"kwargs": pi_kwargs(
                      model="butler-conformance-no-such-model"),
                  "prompt": "say the word ok"},
           "claude-replay": {"kwargs": {}, "prompt": "detonate the repository"},
           "opencode-replay": {"kwargs": {},
                               "prompt": "detonate the repository"},
           # An unknown model: opencode answers an `error` event and rc=1
           # [MEASURED 2026-10-29]; Claude Code refuses at its first turn.
           "claude": {"kwargs": {"model": "butler-conformance-no-such-model"},
                      "prompt": "say the word ok"},
           "opencode": {"kwargs": {"model": "opencode/butler-no-such-model"},
                        "prompt": "say the word ok"}}


@pytest.fixture(params=ADAPTERS)
def adapter(request, monkeypatch):
    name = request.param[0]
    monkeypatch.setattr(sys.modules[__name__], "EVENT_TIMEOUT",
                        EVENT_TIMEOUTS[name])
    if name in REPLAY:
        REPLAY[name][1](monkeypatch)
    return request.param


@pytest.fixture
def harness(adapter):
    return _build(*adapter)


def _spec(tmp_path, name="session.json") -> HarnessSpec:
    """What the daemon would spawn: a directory, a session file, and the
    harness's own default model and tool set (AC-29 retired 2026-08-18)."""
    return HarnessSpec(model=None, cwd=str(tmp_path),
                       session_file=str(tmp_path / name))


@pytest_asyncio.fixture
async def started(harness, tmp_path):
    """A live child and its event stream, disposed however the test ends.

    The teardown is not tidiness: from step 7.2 a leaked child is a real
    process that outlives the run, and this project already counted seven
    orphans in one day (AC-36).
    """
    await harness.start(_spec(tmp_path))
    stream = harness.subscribe()
    try:
        yield harness, stream
    finally:
        await harness.dispose()


async def _next_event(stream, waiting_for):
    """`anext` with a deadline. NEVER call bare `anext` in this file.

    A harness that stops producing is not a hypothesis: it is what the
    predecessor did whenever a provider failed rather than threw. Waited on
    without a deadline that becomes a suite with no output and no exit code,
    which reports the one defect this file owns (AC-48) as an infrastructure
    problem. `StopAsyncIteration` passes through untouched -- a stream that
    ENDS is a real answer to some of these tests.
    """
    try:
        return await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
    except asyncio.TimeoutError:
        pytest.fail(f"waited {EVENT_TIMEOUT:.0f}s for {waiting_for} and "
                    f"nothing arrived; the stream neither produced nor ended")


async def _drain_to_terminal(stream, events, waiting_for):
    """Read until the one terminal event, or until the stream ends.

    A stream that ends without one returns what it had, so the caller's own
    count assertion names the defect instead of this helper raising.
    """
    while True:
        try:
            event = await _next_event(stream, waiting_for)
        except StopAsyncIteration:
            return events
        events.append(event)
        if isinstance(event, TERMINAL_EVENTS):
            return events


TAIL_WINDOW = 2.0


async def _drain_the_tail(stream):
    """Everything queued BEHIND the terminal event, read before disposing.

    Reading stops AT the first terminal event, so a second one queued behind
    it is invisible to the tests above -- proved: a fake yielding `Failed`
    and then `AgentEnd` passed the terminal-count assertion unchanged (D-13).

    Before disposing, because dispose is what makes this check DEAD: a real
    adapter drops the child on the floor there -- `harness_pi._drain` returns
    the moment the handle is nulled -- so a tail read afterwards is empty
    however many terminal events were waiting in it. The window is a settle
    time and not a deadline: nothing ends this stream, so timing out with an
    empty tail is the passing case.
    """
    tail = []
    deadline = time.perf_counter() + TAIL_WINDOW
    while True:
        left = deadline - time.perf_counter()
        if left <= 0:
            return tail
        try:
            tail.append(await asyncio.wait_for(anext(stream), left))
        except (asyncio.TimeoutError, StopAsyncIteration):
            return tail


def _needs_a_compliant_answer(name: str, what: str) -> None:
    """The agent did not produce the shape this property needs.

    Split by adapter because the same miss means two different things. A
    scripted adapter that stopped following its script would let the
    assertion below pass while proving nothing, which is D-13 exactly, so it
    FAILS. A live agent asked for several turns with a failing tool call may
    simply answer in one, and skipping says so out loud instead of turning a
    model's day into a broken contract. Where the property is still proven is
    named in the skip, because a silent gap is the thing this suite exists to
    prevent.
    """
    if name in SCRIPTED:
        pytest.fail(f"{name} scripts its answer, and {what}. The script is "
                    f"the contract here: without that shape this test proves "
                    f"nothing (D-13)")
    pytest.skip(f"{what}. The row's append_system_prompt asks for it and this "
                f"model did not comply, so this run cannot prove the "
                f"property; it is proven on lines recorded from a real child "
                f"in test/test_harness_pi.py")


async def _one_prompt(harness, stream, text="what does this module do"):
    """Drive one prompt to its terminal event and return everything seen."""
    await harness.prompt(text)
    return await _drain_to_terminal(
        stream, [], "the terminal event that ends a prompt")


def test_satisfies_the_protocol(harness):
    """Structurally a Harness.

    Catches an adapter that grew a differently-named method and would only
    blow up inside a turn, which for this port means inside a conversation
    with a tool-enabled agent.
    """
    assert isinstance(harness, ports.Harness)


def test_caps_is_a_frozen_capability_record(harness):
    """AC-35, AC-44. Callers read capabilities; they never test the name.

    Frozen because a caller that could flip `native_resume` on the record it
    was handed would be deciding a capability rather than reading one -- and
    the predecessor's name test is what stranded eight dead chats.
    """
    caps = harness.caps
    assert isinstance(caps, HarnessCaps)
    for name in ("native_resume", "steerable", "images"):
        value = getattr(caps, name)
        assert isinstance(value, bool), f"caps.{name} is {value!r}, not a bool"
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.native_resume = not caps.native_resume


@pytest.mark.asyncio
async def test_exactly_one_terminal_event_per_prompt(adapter, started):
    """AC-48. One prompt, one ending, however many turns the agent took.

    The scripted answer runs SEVERAL turns with tools, which is the case that
    matters: `agent_end` fires when the whole loop settles, never per
    `turn_end`. A harness that ends per turn makes Butler announce a
    five-turn answer five times, and the announcement is the whole reason a
    voice assistant cares about this event.
    """
    events = await _one_prompt(started[0], started[1])

    turn_ends = [e for e in events if isinstance(e, ports.TurnEnd)]
    terminals = [e for e in events if isinstance(e, TERMINAL_EVENTS)]
    assert len(terminals) == 1, (
        f"expected exactly one of agent_end/error, got {len(terminals)}: "
        f"{terminals}")
    assert events[-1] is terminals[0], (
        "the terminal event is not last -- something arrived after the "
        "prompt was declared over")
    # Last, because the two assertions above hold whatever the agent did and
    # only the multi-turn case sharpens them.
    if len(turn_ends) <= 1:
        _needs_a_compliant_answer(
            adapter[0], f"the answer took {len(turn_ends)} turn(s), so this "
                        f"run cannot tell a per-prompt ending from a per-turn "
                        f"one")


@pytest.mark.asyncio
async def test_abort_ends_the_prompt_once_and_the_child_answers_again(started):
    """Barge-in is routine, and it is not a failure (D-4, D-1).

    Two properties in one test because they are one behaviour: an abort
    ENDS the prompt -- exactly one terminal event still, never two, never
    none -- and it leaves the child usable, which is the measured behaviour
    of a real abort settling in 1.51 s (docs/harness-mode.md section 1). An
    adapter that answered an abort with silence would leave the caller's
    reader task waiting forever, which is a held microphone.
    """
    harness, stream = started
    await harness.prompt("read the whole repository")
    seen = [await _next_event(stream, "the first event of the answer"),
            await _next_event(stream, "the second event of the answer")]
    assert not any(isinstance(e, TERMINAL_EVENTS) for e in seen), (
        "this adapter finished within two events, so the abort below would "
        "land after the prompt and the test would prove nothing (D-13)")

    await harness.abort()

    await _drain_to_terminal(
        stream, seen, "the terminal event an aborted prompt still owes")
    assert len([e for e in seen if isinstance(e, TERMINAL_EVENTS)]) == 1, (
        f"an aborted prompt produced "
        f"{len([e for e in seen if isinstance(e, TERMINAL_EVENTS)])} "
        f"terminal events; AC-48 says exactly one, aborted or not")

    again = await _one_prompt(harness, stream)
    assert len([e for e in again if isinstance(e, TERMINAL_EVENTS)]) == 1
    assert any(isinstance(e, ports.TurnEnd) for e in again), (
        "the child answered nothing after an abort -- the abort took the "
        "child down with the prompt")


@pytest.mark.asyncio
async def test_dispose_then_start_is_a_fresh_child(harness, tmp_path):
    """Pinned here because every change to what a child IS is this call pair.

    Moving the working directory or the model means disposing one child and
    starting another on the same adapter instance -- the spoken project
    switch does it mid-conversation (AC-31). The old stream ENDING is half of
    it: a reader task that never sees its iterator close is a task leaked per
    respawn.
    """
    await harness.start(_spec(tmp_path, name="first.json"))
    stream = harness.subscribe()
    await _one_prompt(harness, stream)
    await harness.dispose()

    with pytest.raises(StopAsyncIteration):
        await _next_event(stream, "the old stream to END after dispose")

    await harness.start(_spec(tmp_path, name="second.json"))
    fresh = harness.subscribe()
    try:
        events = await _one_prompt(harness, fresh)
        assert len([e for e in events if isinstance(e, TERMINAL_EVENTS)]) == 1
        assert any(isinstance(e, ports.TurnEnd) for e in events)
    finally:
        await harness.dispose()


@pytest.mark.asyncio
async def test_dispose_mid_answer_ends_the_stream(started):
    """The half of dispose a respawn mid-answer actually uses.

    `test_dispose_then_start_is_a_fresh_child` disposes AFTER the answer, with
    the adapter parked waiting for the next prompt -- a state any adapter ends
    cleanly, and a mutation proved it: deleting the fake's mid-answer guard
    left that test green. A barge-in followed by a respawn disposes MID-
    CONVERSATION, so the stream must end from inside an answer too, with
    events still to come. An adapter that only closed the idle stream would
    leak a reader task per respawn.
    """
    harness, stream = started
    await harness.prompt("read the whole repository")
    first = await _next_event(stream, "the first event of the answer")
    assert not isinstance(first, TERMINAL_EVENTS), (
        "this adapter answered in one event, so the dispose below lands "
        "after the answer and the test proves nothing (D-13)")

    await harness.dispose()

    with pytest.raises(StopAsyncIteration):
        await _next_event(stream, "the stream to END on a mid-answer dispose")


@pytest.mark.asyncio
async def test_tool_events_carry_the_harness_own_id_and_a_real_outcome(
        adapter, started):
    """Both halves are transposed predecessor defects (plan.md 4.4c).

    It discarded the SDK's own tool id and kept one current-step slot, so
    parallel calls overwrote each other -- 8 of 19 starts never closed. And
    it hardcoded a successful outcome, making an error literally
    unreachable. A harness gives Butler both for free; a real failing tool
    in the scripted answer is what makes the second half able to fail.
    """
    events = await _one_prompt(started[0], started[1])
    starts = [e for e in events if isinstance(e, ports.ToolStart)]
    ends = [e for e in events if isinstance(e, ports.ToolEnd)]

    if not starts:
        _needs_a_compliant_answer(
            adapter[0], "the answer used no tools, so this run proves nothing "
                        "about tool events")
    assert len(ends) == len(starts), (
        f"{len(starts)} tool starts and {len(ends)} ends -- an unclosed tool "
        f"call is a narration that never finishes")
    ids = [e.tool_id for e in starts]
    assert all(ids), f"a tool start carried no id: {starts}"
    assert len(set(ids)) == len(ids), (
        f"tool ids repeat: {ids}. Two parallel calls sharing an id is the "
        f"single-current-slot defect wearing the harness's own vocabulary")
    assert sorted(e.tool_id for e in ends) == sorted(ids), (
        "a tool ended under an id nothing started")
    for end in ends:
        assert isinstance(end.is_error, bool), (
            f"tool_end outcome is {end.is_error!r}, not a bool")
    if not any(e.is_error for e in ends):
        _needs_a_compliant_answer(
            adapter[0], "no tool in this answer FAILED, so an adapter that "
                        "hardcoded a successful outcome would pass this run "
                        "unchanged -- which is the predecessor's defect "
                        "exactly")


@pytest.mark.asyncio
async def test_turn_end_carries_the_message_even_when_nothing_streamed(
        adapter, tmp_path):
    """AC-47 rides on this shape.

    A measured cloud provider emitted ZERO text deltas and put the whole
    answer in `turn_end` (docs/harness-mode.md section 1). An adapter that
    only reported deltas would have spoken nothing at all there, and a turn
    that makes no sound is indistinguishable from a dead microphone.
    """
    name, kwargs = adapter
    harness = _build(name, {**kwargs, **NO_DELTA.get(name, {})})
    await harness.start(_spec(tmp_path))
    stream = harness.subscribe()
    try:
        events = await _one_prompt(harness, stream)
    finally:
        await harness.dispose()

    deltas = [e for e in events if isinstance(e, ports.TextDelta)]
    turn_ends = [e for e in events if isinstance(e, ports.TurnEnd)]
    assert turn_ends, "the answer produced no turn_end at all"
    empty = [e for e in turn_ends if not e.text.strip()]
    if empty:
        # A turn that says nothing is ordinary for a live agent -- a
        # tool-only turn carries no text at all -- and it is a defect for a
        # fake whose every scripted turn has words in it.
        _needs_a_compliant_answer(
            name, f"{len(empty)} of {len(turn_ends)} turn_end(s) carried no "
                  f"message, and on a provider that does not stream that is "
                  f"the only text there would ever be")
    if name in NO_DELTA:
        assert deltas == [], (
            f"this adapter was asked for a no-delta turn and streamed "
            f"{len(deltas)} deltas anyway, so the no-stream path is not "
            f"being exercised (D-13)")


@pytest.mark.asyncio
async def test_the_deltas_keep_a_paragraph_break_between_turns(adapter,
                                                               started):
    """`TextDelta`s concatenate VERBATIM into the answer (ports.TextDelta).

    So the separator between two text blocks -- one either side of a tool
    call -- is the ADAPTER's to emit: its caller has no event to hang one on.
    pi streams each block from its own first character, and without this the
    answer reached the chat record as "directory.E:/..."
    (docs/e2e-journeys.md).
    """
    name = adapter[0]
    harness, stream = started
    events = await _one_prompt(harness, stream)

    texts = [e.text.strip() for e in events
             if isinstance(e, ports.TurnEnd) and e.text.strip()]
    deltas = "".join(e.text for e in events if isinstance(e, ports.TextDelta))
    if len(texts) < 2 or not deltas:
        _needs_a_compliant_answer(
            name, "the answer had fewer than two turns with text in them, so "
                  "there was no block boundary to keep")
    assert deltas.strip() == "\n\n".join(texts), (
        f"the streamed answer {deltas!r} is not the turns {texts!r} joined "
        f"by a paragraph break")


@pytest.mark.asyncio
async def test_a_failed_prompt_is_still_exactly_one_terminal_event(
        adapter, tmp_path):
    """AC-48's other half: `Failed` REPLACES `AgentEnd`, never joins it.

    Without a way to reach it, `Failed` is a branch no adapter emits and no
    test covers, which is the predecessor's shape precisely: its error event
    fired only on a JS exception, so a provider failure produced no terminal
    event at all and held the turn lock forever (plan.md 4.4c). 7.4 speaks
    this event as the failure (AC-47), and it can only do that if the fake
    can produce one.
    """
    name, kwargs = adapter
    scripted = FAIL_ON.get(name)
    if scripted is None:
        pytest.skip(f"{name} has no scripted way to fail a prompt, so it "
                    f"owes this suite a FAIL_ON row: an adapter that can "
                    f"never emit Failed cannot be shown to end the turn "
                    f"when its provider breaks")
    harness = _build(name, {**kwargs, **scripted["kwargs"]})
    await harness.start(_spec(tmp_path))
    stream = harness.subscribe()
    try:
        seen = await _one_prompt(harness, stream, scripted["prompt"])
        # The tail matters as much as the answer: reading stops at the first
        # terminal event, so an adapter that yields Failed AND THEN AgentEnd
        # passes every assertion below unless something looks past the ending.
        # Read INSIDE the try, because disposing first discards it (D-13).
        seen += await _drain_the_tail(stream)
    finally:
        await harness.dispose()

    terminals = [e for e in seen if isinstance(e, TERMINAL_EVENTS)]
    assert len(terminals) == 1, (
        f"a failed prompt produced {len(terminals)} terminal events: "
        f"{terminals}. AC-48 says exactly one, failed or not")
    assert isinstance(terminals[0], ports.Failed), (
        f"the prompt failed and ended with {terminals[0]!r}. An adapter that "
        f"reports a failure as AgentEnd makes Butler announce an answer it "
        f"never got")
    assert terminals[0].message.strip(), (
        "Failed carried no message. The caller SPEAKS this (AC-47), so an "
        "empty one is a turn that makes no sound")


@pytest.mark.asyncio
async def test_prompt_before_start_raises(harness):
    """Fail fast: there is no child to prompt yet.

    An adapter that swallowed this would turn a caller that forgot to start
    into a turn that simply never ends -- the silent failure, from the one
    port that runs someone else's process. `RuntimeError` specifically, not a
    bare `Exception`: an AttributeError from a renamed method satisfies the
    bare version, so the test would go on passing after `prompt` stopped
    existing (D-13).
    """
    with pytest.raises(RuntimeError):
        await harness.prompt("hello")


def test_info_is_cheap_enough_for_the_handshake(harness):
    """D-25, with a clock, exactly as the other three suites do it.

    Nothing reads this during the hello handshake TODAY because the port has
    no consumer yet -- but the HUD will name the attached harness, and this
    is the one port whose adapter is a child process, so an `info` that
    asked the child a question would freeze every satellite for as long as
    the child took to answer. Serve it from a static table.
    """
    started_at = time.perf_counter()
    info = harness.info
    elapsed = time.perf_counter() - started_at
    assert isinstance(info, dict)
    assert isinstance(info.get("engine"), str) and info["engine"], (
        f"info must name the engine so the owner can see which harness is "
        f"attached: {info}")
    assert elapsed < 0.25, (
        f"reading info took {elapsed * 1000:.0f} ms on the event loop")


# A spec is built by the DAEMON and describes what a child IS. These are the
# contradictions that would otherwise surface as a child that spawned and
# then misbehaved, which is the failure 4.4c's last rule names: fail fast at
# construction, not at first use.
CONTRADICTIONS = [
    pytest.param(dict(cwd=""), id="no working directory"),
    pytest.param(dict(session_file=""), id="no session file"),
    pytest.param(dict(model=""), id="blank model instead of None"),
]


@pytest.mark.parametrize("override", CONTRADICTIONS)
def test_a_contradictory_spec_raises_at_construction(tmp_path, override):
    """plan.md 4.4c: fail fast at construction, not at first use.

    A blank working directory is AC-31 failing silently -- a coding agent
    spawned wherever the daemon happens to stand. A blank model is the other
    kind of contradiction: None already means "the harness's own default", so
    an empty string is a second spelling of it that some adapter will
    eventually pass through as a model named "". The tool-set contradictions
    that used to sit here went with AC-29 on 2026-08-18; there is no tool set
    to contradict.
    """
    fields = dict(model=None, cwd=str(tmp_path),
                  session_file=str(tmp_path / "session.json"))
    fields.update(override)
    with pytest.raises(ValueError):
        HarnessSpec(**fields)
