"""Feedback while an agent works: elapsed time and a last action, as data.

`docs/parallel-agents.md` phase 7 (AC-233, AC-235, AC-236). The DAEMON half
only -- nothing here draws any of this; the page is a separate task. Four
claims, each a way the feature could look done and not be.

**`GET /api/chats/{id}/status` CLOSES AC-236's OWN "STILL OPEN"** (the
recovery route, built alongside the page half). Nothing served the turn-start
over HTTP before this, so a reloaded tab always started the clock at zero even
when a turn had been running for minutes -- `hub.status_for` and
`hub._elapsed_ms` existed and nothing outside the process could read either.
READ-ONLY, like every route in this family: it changes nothing, so the same
GET a page issues on every reload can be issued a thousand times a second
with no effect on the daemon it is asking about.

**A TURN'S START IS RECORDED, so elapsed is MEASURED rather than guessed**
(AC-236, item 2) -- `Hub._turn_started`, written in `_begin_turn` beside
`turn_by_chat`/`chat_by_turn`.

**`ToolStart`/`ToolEnd` REACH THE WIRE** (item 3). They used to fall through
`HarnessConverse._answer`'s dispatch with no branch and no trailing `else`,
so every tool call the fake harness's own DEFAULT script makes -- and every
one `harness_pi.py` has ever emitted -- reached nobody.

**A CONVERSATION WITH NO TOOL EVENT SHOWS ITS AGE, NOT A SENTENCE**
(AC-235, item 5) -- the owner's stated common case, silent text generation
with no tools at all. One `worker_event` still goes out, right after the
child is confirmed up, carrying `elapsed_ms` and a blank last action.

**THE STATUS IS DERIVED, NOT A SECOND SOURCE OF TRUTH** (item 1). `idle`,
`running` and `waiting` read state `Hub`/`_Host` already hold for other
reasons (`turn_by_chat`, `questions_open`, a live child); only the last
terminal OUTCOME (`done`/`error`) is new state, and it is the one bit
nothing else distinguishes -- both leave no child behind.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_conversation import hub_turn                          # noqa: E402
from test_harness_question import (CONFIRM, _hub, _http, frames,  # noqa: E402
                                   running_turn, until)
from daemon import ports                                        # noqa: E402
from daemon.adapters import brain_harness as bh                 # noqa: E402
from daemon.adapters.harness_fake import ScriptedTurn            # noqa: E402

# Shortened, like `test_harness_question.py`'s own IDLE_S: this suite must
# not wait out the shipped 180s budget to prove a question suspends nothing.
IDLE_S = 0.3

NO_TOOLS = (ScriptedTurn("all done, nothing to look at all"),)


# --------------------------------------------------------------------------- #
# item 3 + item 4 -- ToolStart/ToolEnd reach the wire with a last action
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tool_events_reach_the_wire_with_a_last_action(tmp_path):
    """FakeHarness's DEFAULT script has three tool calls across one prompt,
    one of which FAILS -- the same script every other harness suite relies
    on for the reason `harness_fake.py`'s docstring gives: an all-succeeding
    script lets an adapter hardcode success and still pass.
    """
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]

    await hub_turn(hub, s, turn=900, text="tidy up")

    events = frames(s, "worker_event")
    assert events, "no worker_event frame reached the wire at all"
    assert events[0]["kind"] == "started" and events[0]["detail"] == "", (
        f"the first worker_event must announce the turn before any tool "
        f"fires, with no last action yet: {events[0]}")
    starts = [e for e in events if e["kind"] == "tool_start"]
    ends = [e for e in events if e["kind"] == "tool_end"]
    assert [e["detail"] for e in starts] == ["read", "grep", "read"], (
        f"the last-action line is not the harness's own tool name, in "
        f"order: {starts}")
    assert any(e["detail"] == "grep failed" for e in ends), (
        f"a failed tool's real outcome never reached the wire: {ends}")
    assert all(isinstance(e["elapsed_ms"], int) and e["elapsed_ms"] >= 0
               for e in events), events
    assert all(e["chat"] == chat_id for e in events), (
        f"a worker_event did not name the conversation it belongs to: "
        f"{events}")


# --------------------------------------------------------------------------- #
# item 5 (AC-235) -- no tool event, still an age to show
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_toolless_turn_still_gets_a_frame_to_show_its_age(tmp_path):
    """AC-235's stated worst case: a plain text answer with no tools at all.
    A feature that only speaks when a tool fires would miss it entirely."""
    hub, s, harness, chats = _hub(
        tmp_path, harness_for=lambda _chat=None: ports.build(
            ports.HARNESSES, "fake", script=NO_TOOLS))

    await hub_turn(hub, s, turn=901, text="quick one")

    events = frames(s, "worker_event")
    assert len(events) == 1, (
        f"a tool-less turn must still carry exactly one worker_event so a "
        f"page has a start time to show age from, and no more than one "
        f"since nothing else happened: {events}")
    assert events[0]["kind"] == "started" and events[0]["detail"] == "", (
        f"a tool-less turn's one frame must name no last action, because "
        f"there was none: {events[0]}")
    assert events[0]["elapsed_ms"] >= 0, events[0]


# --------------------------------------------------------------------------- #
# item 2 (AC-236) -- the turn-start timestamp is real, not guessed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_elapsed_is_measured_from_when_the_turn_actually_started(
        tmp_path, monkeypatch):
    """A turn parked on a question is the one STABLE way to read
    `_elapsed_ms` mid-flight without racing the fake's own event loop --
    `test_harness_question.py`'s own reason for using this fixture.

    Driven through `hub._typed`, the REAL entry point, rather than
    `test_conversation.hub_turn`: that driver reproduces `_begin_turn` by
    hand and is a second copy of it, so a test built on it alone could not
    tell "the daemon writes this" from "the test driver does" -- exactly
    the drift `hub_turn`'s own docstring warns about, one field further
    along again.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, chats = _hub(tmp_path, ask=CONFIRM)
    chat_id = chats.current()["id"]

    await hub._typed(s, {"text": "go slow"})
    await until(lambda: frames(s, "question"), "the question")
    assert hub._turn_started.get(chat_id) is not None, (
        "no turn-start timestamp was recorded for this conversation "
        "(AC-236); elapsed has nothing to be measured from")
    first = hub._elapsed_ms(chat_id)
    await asyncio.sleep(0.05)
    second = hub._elapsed_ms(chat_id)
    assert second > first, (
        f"elapsed did not grow while a real turn sat waiting on a human: "
        f"{first} then {second}")
    async with _http(hub, monkeypatch) as client:
        answer = await client.post(
            "/api/answer", json={"qid": CONFIRM.qid, "confirmed": True})
        assert answer.status == 200, await answer.json()
    await asyncio.wait_for(s.task, timeout=10)


# --------------------------------------------------------------------------- #
# item 1 -- the status is derived, not a second source of truth
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_status_for_derives_the_five_words(tmp_path, monkeypatch):
    """Idle, running, waiting, done, error -- each from state already held
    elsewhere except the last terminal outcome, which `_Host.last_outcome`
    exists to be the one new bit of (item 1's own argument).
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]

    # IDLE: nobody has spoken into this conversation.
    assert hub.status_for(chat_id) == "idle", hub.status_for(chat_id)

    # RUNNING: `turn_by_chat` set by hand, the idiom `test/AGENTS.md` names
    # for `owns()`'s own fallback test -- a real generation gives no stable
    # window to catch between "running" and "done" without racing the
    # fake's own event loop, and this asserts the DERIVATION rather than
    # the timing.
    s.turn_by_chat[chat_id] = 12345
    assert hub.status_for(chat_id) == "running", (
        "a turn registered in turn_by_chat did not read as running")
    del s.turn_by_chat[chat_id]

    # DONE: a turn that finished cleanly.
    await hub_turn(hub, s, turn=960, text="finish this one")
    assert hub.status_for(chat_id) == "done", hub.status_for(chat_id)

    # WAITING: a child parked on a dialog, on a SECOND conversation so it
    # does not disturb the DONE row just proven.
    waiting_hub, ws, wharness, wchats = _hub(tmp_path / "waiting",
                                             ask=CONFIRM)
    wait_chat = wchats.current()["id"]
    async with running_turn(waiting_hub, ws) as task:
        await until(lambda: frames(ws, "question"), "the question")
        assert waiting_hub.status_for(wait_chat) == "waiting", (
            waiting_hub.status_for(wait_chat))
        async with _http(waiting_hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": CONFIRM.qid, "confirmed": True})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    # ERROR: a turn that failed, on a THIRD conversation for the same
    # reason.
    err_hub, es, eharness, echats = _hub(
        tmp_path / "error", harness_for=lambda _chat=None: ports.build(
            ports.HARNESSES, "fake", fail_on="boom"))
    err_chat = echats.current()["id"]
    await hub_turn(err_hub, es, turn=970, text="boom")
    assert err_hub.status_for(err_chat) == "error", (
        err_hub.status_for(err_chat))


# --------------------------------------------------------------------------- #
# the recovery route -- BUILD 1, closing AC-236's "still open"
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_status_route_answers_a_real_waiting_turns_growing_clock(
        tmp_path, monkeypatch):
    """A reloaded tab must recover the clock rather than start it at zero.

    Driven on a WAITING turn, like `test_elapsed_is_measured_...` above, for
    the same reason: it is the one stable way to read a real turn's elapsed
    time mid-flight without racing the fake's own event loop.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, chats = _hub(tmp_path, ask=CONFIRM)
    chat_id = chats.current()["id"]

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            r1 = await client.get(f"/api/chats/{chat_id}/status")
            assert r1.status == 200, await r1.json()
            body1 = await r1.json()
            assert body1["id"] == chat_id, body1
            assert body1["status"] == "waiting", body1
            assert isinstance(body1["elapsed_ms"], int) and body1["elapsed_ms"] >= 0, body1

            await asyncio.sleep(0.05)
            r2 = await client.get(f"/api/chats/{chat_id}/status")
            body2 = await r2.json()
            assert body2["elapsed_ms"] > body1["elapsed_ms"], (
                f"elapsed did not grow between two GETs of the SAME route "
                f"against a real running turn: {body1} then {body2}")

            answer = await client.post(
                "/api/answer", json={"qid": CONFIRM.qid, "confirmed": True})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_the_status_route_404s_an_id_nobody_has(tmp_path, monkeypatch):
    """`get_chat`'s own rule in a second place: an empty 200 would tell the
    page a conversation exists and is merely idle."""
    hub, s, harness, chats = _hub(tmp_path)
    async with _http(hub, monkeypatch) as client:
        r = await client.get("/api/chats/chat-nobody-has/status")
        assert r.status == 404, await r.json()
        body = await r.json()
    assert "chat-nobody-has" in body.get("error", ""), body


# --------------------------------------------------------------------------- #
# docs/triage-list.md phase 2 continued -- a background row's last action
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tool_start_notes_the_action_and_the_turns_end_clears_it(
        tmp_path):
    """The owner's failure mode, named exactly: a stale tool name outliving
    the work it described is worse than showing nothing.

    Spies on `_Host.note_action` (call-through, so the real dict is still
    written) rather than reading `hub.action_for` mid-turn: the fake harness
    yields its whole scripted answer to one `prompt()` call with no stable
    point to pause at, so the only race-free way to prove ToolStart reaches
    `_Host` is to watch the call itself, exactly as `test_worker_status.py`'s
    own `frames()` watches the wire for the same reason.
    """
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]
    host = hub.brain.host
    seen: list[tuple[str, str]] = []
    original = host.note_action

    def spy(cid, detail):
        seen.append((cid, detail))
        original(cid, detail)

    host.note_action = spy

    await hub_turn(hub, s, turn=900, text="tidy up")

    assert seen == [(chat_id, "read"), (chat_id, "grep"), (chat_id, "read")], (
        f"ToolStart never reached _Host.note_action, or reached it with the "
        f"wrong tool names/order: {seen}")
    assert hub.action_for(chat_id) is None, (
        "the last action must be cleared the moment the turn ends -- a row "
        "saying what a finished agent was doing is worse than saying "
        "nothing")


@pytest.mark.asyncio
async def test_a_barge_in_clears_the_action_it_interrupted(tmp_path):
    """The OTHER terminal path: barge-in disposes the child through
    `HarnessConverse.abort`, never through `turn()`'s own `finally` (D-1's
    every-path discipline applied to this one bit of state).

    `ask=CONFIRM` is what gives this a stable point to abort FROM without
    racing the fake's own event loop -- `test_harness_question.py`'s own
    reason for the fixture. The action is set BY HAND right before the
    abort, standing in for a tool that started before the question did.
    """
    hub, s, harness, chats = _hub(tmp_path, ask=CONFIRM)
    chat_id = chats.current()["id"]
    host = hub.brain.host

    async with running_turn(hub, s, turn=901) as task:
        await until(lambda: frames(s, "question"), "the question")
        host.note_action(chat_id, "editing web/index.html")
        assert host.last_action(chat_id) == "editing web/index.html"
        await hub.brain.abort(901)
        await asyncio.wait_for(task, timeout=5)

    assert host.last_action(chat_id) is None, (
        "a barged-in turn left its last action behind for the next reader "
        "of this conversation's row")


@pytest.mark.asyncio
async def test_dispose_clears_the_action_with_no_turn_generator_involved(
        tmp_path):
    """`dispose()`'s OWN clear, isolated from `turn()`'s `finally` entirely.

    `dispose_all()` (AC-62, a restart) tears a child down directly and never
    runs the generator's `finally` -- so a real held child is spawned, an
    action is noted on it, and it is disposed with NO turn in flight to reach
    the other clear. If this clear lived only in `turn()`'s `finally`, this
    is the one case that would still show a stale action.
    """
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]
    host = hub.brain.host

    await hub_turn(hub, s, turn=902, text="finish this one")
    assert host.child(chat_id) is not None, (
        "AC-59 should have held this conversation's child after a clean end")
    host.note_action(chat_id, "editing web/index.html")
    assert host.last_action(chat_id) == "editing web/index.html"

    await host.dispose_all()

    assert host.last_action(chat_id) is None, (
        "dispose_all() (what a restart calls, AC-62) must clear the last "
        "action itself -- no turn generator runs to do it for this path")


@pytest.mark.asyncio
async def test_the_listing_carries_the_last_action_too(tmp_path, monkeypatch):
    """docs/triage-list.md phase 2 continued (owner's ruling 2026-09-16).

    `GET /api/chats` reads `hub.action_for`, which reads `_Host.last_action`
    -- the SAME field `ToolStart` writes and a turn's own end clears, never a
    second copy of it (D-18). Set and cleared BY HAND, the idiom
    `test_status_for_derives_the_five_words` already uses: a real generation
    gives no stable window to catch mid-flight without racing the fake's own
    event loop.
    """
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]
    s.turn_by_chat[chat_id] = 4242
    hub.brain.host.note_action(chat_id, "editing web/index.html")

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats")).json()
    row = next(c for c in doc["chats"] if c["id"] == chat_id)
    assert row["status"] == "running", row
    assert row["action"] == "editing web/index.html", (
        f"a loud row's last action did not ride on the same listing "
        f"`status`/`elapsed_ms` already do: {row}")

    del s.turn_by_chat[chat_id]
    hub.brain.host.clear_action(chat_id)
    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats")).json()
    row = next(c for c in doc["chats"] if c["id"] == chat_id)
    assert row["action"] is None, (
        f"a cleared action must not go on being served to the listing: "
        f"{row}")


@pytest.mark.asyncio
async def test_the_status_route_is_read_only(tmp_path, monkeypatch):
    """A GET that changed anything would be the diagnostic-that-edits-
    configuration defect this repo has already paid for once. Asserted as a
    NEGATIVE: ten reads in a row must not move the status or disturb the
    child (`harness.disposals`/`harness.aborts` stay at zero throughout)."""
    hub, s, harness, chats = _hub(tmp_path)
    chat_id = chats.current()["id"]
    await hub_turn(hub, s, turn=990, text="finish this one")
    before = hub.status_for(chat_id)
    async with _http(hub, monkeypatch) as client:
        for _ in range(10):
            r = await client.get(f"/api/chats/{chat_id}/status")
            assert r.status == 200
    after = hub.status_for(chat_id)
    assert before == after == "done", (before, after)
    assert harness.disposals == 0 and harness.aborts == 0, (
        f"reading the status route touched the child: disposals="
        f"{harness.disposals}, aborts={harness.aborts}")
