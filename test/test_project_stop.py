"""A GENERAL "stop a named conversation" capability -- docs/filing-and-
pacing.md 1.7 / section 4 phase 5, DAEMON HALF ONLY.

Section 1.7 found that nothing like this existed: `abandon()` is barge-in on
a session's own FRONT-DOOR turn, and the stop control and Escape both act on
the conversation a device is showing (`docs/parallel-agents.md` D5). Stopping
a turn belonging to a DIFFERENT conversation, possibly owned by a DIFFERENT
session, or by no session at all (`017f8a1`'s orphaned turns), is the thing
this file builds and proves.

D4 is DECIDED as option A, the general capability, over a narrow one-off --
the spec's own argument: a narrow version still has to solve the hard part to
work at all.

This file does NOT wire the capability into `_enforce_project_limit`'s
BUSY-collision refusal -- `test_harness_project_limit.py` already pins that a
busy collision refuses, and the "collision still refuses" section below pins
that wiring `stop_for` does not, on its own, change that: the ordinary turn
path never calls `stop_for` and still never will after this file's own
additions.

**EXTENDED for phase 5 steps 1-4** (the page's confirm dialog is built in
`web/index.html`/`web/app.js` and is proved by a real-browser screenshot per
the task, not by anything here): `_Host.busy_children_for_project` is the
daemon-reachable half of "the page must know a collision is coming before it
commits" (step 1), and `POST /api/project/{id}/stop` is the ONE route that
may call `hub.stop_conversation` from a collision at all -- a confirmed
gesture and nothing else on the ordinary turn path can reach it (steps 2-4).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, StubResolver, attach_session,      # noqa: E402
                      make_hub)
from daemon import butlerd                                        # noqa: E402
from daemon import hub as hub_mod                                  # noqa: E402
from daemon import ports                                          # noqa: E402
from daemon import shortcuts as shortcuts_mod                      # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                       # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain            # noqa: E402
from daemon.adapters.harness_fake import FakeHarness               # noqa: E402
from daemon.chats import ChatStore                                 # noqa: E402
from daemon.hub import IDLE, SPEAKING                               # noqa: E402
from daemon.modes import ModeStore                                  # noqa: E402

from test_hub_bargein import RecordingBrain                        # noqa: E402
from test_parallel_agents import _prefs, _said, _turn               # noqa: E402


def _harness_brain(tmp_path, fake_openai, prefs=None, stop_for=None,
                    harness_for=None):
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    agents: list[FakeHarness] = []

    def _mint(_chat=None):
        h = FakeHarness()
        agents.append(h)
        return h

    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url),
        harness_for=harness_for or _mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats,
                                               prefs.project_paths),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"],
        stop_for=stop_for)
    return brain, agents, chats, prefs


# --------------------------------------------------------------------------- #
# Hub.stop_conversation -- the general capability
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stop_conversation_ends_a_turn_owned_by_another_session(
        tmp_path):
    """The caller of `stop_conversation` is never the session that owns the
    turn -- that is the whole point of a NAMED-conversation stop. Reuses
    `abandon()`'s exact abort-then-rearm sequence (D-18)."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    owner = attach_session(hub, "desk")
    owner.state = SPEAKING
    turn = hub._begin_turn(owner)
    chat = owner.turn_chat

    stopped = await hub.stop_conversation(chat)

    assert stopped is True
    assert hub.brain.aborted == [turn], (
        f"the brain was not told to stop the named conversation's turn: "
        f"{hub.brain.aborted}")
    assert not owner.owns(turn), "the turn is still owned after being stopped"
    assert owner.state == IDLE, "the owning session was not re-armed (D-1)"
    assert owner.turn is None


@pytest.mark.asyncio
async def test_stop_conversation_aborts_a_background_turn_without_touching_the_front_door(
        tmp_path):
    """A turn tracked in `turn_by_chat` that is NOT this session's front
    door (the rare case: AC-238's write-not-heard mute never moves the
    front door, so this is closer to a defensive case than an everyday
    one) must still be reachable -- and stopping it must not touch the
    session's OWN current front-door turn or task."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")

    # A background entry with no front-door status at all.
    s.turn_by_chat["chat-bg"] = 100
    s.chat_by_turn[100] = "chat-bg"

    # The session's OWN live front-door turn, which must be left alone.
    front_task = asyncio.create_task(asyncio.sleep(3600))
    s.turn = 200
    s.turn_by_chat["chat-fg"] = 200
    s.chat_by_turn[200] = "chat-fg"
    s.state = SPEAKING
    s.reply_task = front_task

    try:
        stopped = await hub.stop_conversation("chat-bg")

        assert stopped is True
        assert hub.brain.aborted == [100]
        assert "chat-bg" not in s.turn_by_chat, (
            "the background turn is still owned after being stopped")
        assert s.turn == 200, "the front door turn was disturbed"
        assert s.state == SPEAKING, "the front door's state was disturbed"
        assert s.reply_task is front_task and not front_task.done(), (
            "an unrelated live task was cancelled")
    finally:
        front_task.cancel()
        try:
            await front_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_stop_conversation_reaches_an_orphaned_turn_with_no_session(
        tmp_path):
    """017f8a1: a harness turn survives its socket closing and is tracked
    only in `Hub._orphaned`. The capability must still end it with NO
    `SatelliteSession` attached at all."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    turn = 42
    task = asyncio.create_task(asyncio.sleep(3600))
    hub._orphaned["chat-orphan"] = (turn, task)

    try:
        stopped = await hub.stop_conversation("chat-orphan")
        assert stopped is True
        assert hub.brain.aborted == [turn]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_stop_conversation_on_an_idle_held_child_disposes_it(
        tmp_path, fake_openai):
    """No turn in flight, but the child is still HELD (AC-59): stopping it
    is a DISPOSE, never an abort -- there is nothing running to interrupt."""
    brain, agents, chats, _ = _harness_brain(tmp_path, fake_openai)
    host = brain.host
    chat = chats.current()
    await host.ensure(chat, await host.plan(chat))
    host.note_idle(chat["id"])

    hub = make_hub(tmp_path)
    hub.brain = brain

    stopped = await hub.stop_conversation(chat["id"])

    assert stopped is True
    assert agents[0].disposals == 1, "the idle held child was never disposed"
    assert agents[0].aborts == 0, (
        "an idle child was ABORTED rather than plainly disposed")
    assert host.child(chat["id"]) is None


@pytest.mark.asyncio
async def test_stop_conversation_fails_loudly_for_nothing_to_stop(tmp_path):
    """A chat naming no turn in flight and no held child anywhere: fail
    fast rather than silently reporting success (root AGENTS.md's standing
    rule)."""
    hub = make_hub(tmp_path)

    with pytest.raises(LookupError):
        await hub.stop_conversation("chat-does-not-exist")


@pytest.mark.asyncio
async def test_stop_conversation_does_not_disturb_an_unrelated_conversation(
        tmp_path):
    """Stopping chat A must leave a genuinely different, unrelated
    conversation on a DIFFERENT session running untouched."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    a = attach_session(hub, "desk")
    b = attach_session(hub, "phone")
    # DISTINCT conversations: with no session store on the fake brain,
    # `chat_for` falls back to the one conversation the daemon is holding
    # for BOTH sessions unless each has said what it is showing (AC-238).
    a.showing = "chat-a"
    a.state = SPEAKING
    turn_a = hub._begin_turn(a)
    b.showing = "chat-b"
    b.state = SPEAKING
    turn_b = hub._begin_turn(b)

    await hub.stop_conversation(a.turn_chat)

    assert hub.brain.aborted == [turn_a]
    assert b.owns(turn_b), "an unrelated conversation's turn was stopped too"
    assert b.state == SPEAKING


# --------------------------------------------------------------------------- #
# _Host.stop_conversation -- the caller brain_harness.py gets
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_host_stop_conversation_forwards_to_the_injected_resolver(
        tmp_path, fake_openai):
    calls = []

    async def fake_stop(chat_id):
        calls.append(chat_id)
        return True

    brain, _agents, _chats, _prefs_ = _harness_brain(
        tmp_path, fake_openai, stop_for=fake_stop)

    result = await brain.host.stop_conversation("chat-x")

    assert result is True
    assert calls == ["chat-x"]


@pytest.mark.asyncio
async def test_host_stop_conversation_fails_loudly_when_unwired(
        tmp_path, fake_openai):
    """Every fixture across this suite (and every one that predates this
    file) builds a `_Host` with no `stop_for` at all -- the honest default
    is a loud refusal, not a silent no-op: calling this with nothing wired
    is a construction bug, not a runtime "nothing to stop"."""
    brain, _agents, _chats, _prefs_ = _harness_brain(tmp_path, fake_openai)

    with pytest.raises(RuntimeError):
        await brain.host.stop_conversation("chat-x")


# --------------------------------------------------------------------------- #
# THE COLLISION STILL REFUSES -- wiring the capability changes nothing
# about the confirm-to-stop gesture until a caller actually invokes it
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_busy_collision_still_refuses_with_stop_for_wired(
        tmp_path, fake_openai):
    """`_enforce_project_limit` does not call `stop_for` at all yet -- the
    confirm has no UI, and a daemon that silently stops a busy agent
    because a page asked is exactly what the owner's confirm ruling exists
    to prevent (docs/filing-and-pacing.md 1.7, phase 5 step 2 -- not built
    here). Merely WIRING the resolver must not change the outcome
    `test_harness_project_limit.py` already pins.
    """
    calls = []

    async def spy_stop(chat_id):
        calls.append(chat_id)
        return True

    prefs = _prefs(tmp_path, project_agent_limit=1)
    brain, agents, chats, _ = _harness_brain(
        tmp_path, fake_openai, prefs=prefs, stop_for=spy_stop)
    host = brain.host

    first = chats.current()
    await host.ensure(first, await host.plan(first))   # busy: no note_idle

    await brain.new_conversation()
    said = _said(await _turn(brain, "second project attempt", turn_id=2))

    assert len(agents) == 1, (
        f"a second child was spawned into a project already BUSY: {agents}")
    assert any("agent" in s.lower() for s in said), (
        f"the refusal did not name the collision: {said}")
    assert calls == [], (
        f"stop_for was invoked automatically during a busy collision -- "
        f"the confirm has no UI yet and this must still refuse: {calls}")


# --------------------------------------------------------------------------- #
# _Host.busy_children_for_project -- the confirm dialog's own pre-check
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_busy_children_for_project_finds_a_genuinely_busy_child(
        tmp_path, fake_openai):
    """Spawned via `ensure()`, `_idle_since` never touched -- exactly the
    shape a child is in the instant after spawn and before its own
    `_answer`/`_give_up` ever runs (mirroring `test_harness_project_
    limit.py`'s own reproduction of BUSY)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    prefs = _prefs(tmp_path, project_agent_limit=1)
    prefs.set("harness_cwd", str(proj))
    brain, agents, chats, _ = _harness_brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host
    chat = chats.current()

    await host.ensure(chat, await host.plan(chat))

    assert host.busy_children_for_project(str(proj)) == [chat["id"]]


@pytest.mark.asyncio
async def test_busy_children_for_project_excludes_an_idle_held_child(
        tmp_path, fake_openai):
    """The D1=A case (section 1.6): idle-but-held does not collide, so it
    must not read as busy either -- the whole point of this method is to
    never disagree with `_enforce_project_limit`'s own read of the same two
    dicts."""
    proj = tmp_path / "proj"
    proj.mkdir()
    prefs = _prefs(tmp_path, project_agent_limit=1)
    prefs.set("harness_cwd", str(proj))
    brain, agents, chats, _ = _harness_brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host
    chat = chats.current()
    await host.ensure(chat, await host.plan(chat))

    host.note_idle(chat["id"])

    assert host.busy_children_for_project(str(proj)) == []


@pytest.mark.asyncio
async def test_busy_children_for_project_is_empty_for_an_unclaimed_directory(
        tmp_path, fake_openai):
    brain, _agents, _chats, _ = _harness_brain(tmp_path, fake_openai)

    assert brain.host.busy_children_for_project(str(tmp_path / "nothing")) == []


@pytest.mark.asyncio
async def test_busy_children_for_project_compares_as_directories(
        tmp_path, fake_openai):
    """`same_dir`, not string equality -- a trailing separator or a
    different case must still match (the exact defect `same_dir` itself was
    written to fix, per `shortcuts.py`)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    prefs = _prefs(tmp_path, project_agent_limit=1)
    prefs.set("harness_cwd", str(proj))
    brain, agents, chats, _ = _harness_brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host
    chat = chats.current()
    await host.ensure(chat, await host.plan(chat))

    assert host.busy_children_for_project(str(proj) + "\\") == [chat["id"]]


# --------------------------------------------------------------------------- #
# The HTTP surface: GET /api/shortcuts' `busy` flag and
# POST /api/project/{id}/stop, the confirm dialog's "yes" button and the
# ONLY route that may call `hub.stop_conversation` from a collision.
# --------------------------------------------------------------------------- #
def _project_hub(tmp_path, fake_openai, project_agent_limit=1):
    """A real `Hub` over a real `HarnessBrain` and a real `ShortcutStore`,
    wired the same way `butlerd.build_hub` wires `stop_for` post-
    construction (`build_hub`'s own comment: the Hub cannot exist before the
    brain it is built from). Its own store rather than `state/` (test/
    AGENTS.md): the suite must never touch the owner's files.
    """
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    prefs = _prefs(tmp_path, project_agent_limit=project_agent_limit)
    brain, agents, chats, _ = _harness_brain(tmp_path, fake_openai, prefs=prefs)
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=brain,
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=prefs, shortcuts=store)
    brain.host.set_stop_for(hub.stop_conversation)
    return hub, store, prefs, brain, agents, chats


async def _call(method: str, path: str) -> tuple[int, dict]:
    """Drive the real route through the real app -- `test_shortcuts.py`'s
    own `call()`, reused in shape rather than imported, because that one is
    module-private and this file already builds its own `Hub`."""
    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.request(method, path)
        payload = {} if answer.status == 204 else await answer.json()
        return answer.status, payload
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_shortcuts_marks_a_project_busy_when_its_child_is_busy(
        tmp_path, fake_openai, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    hub, store, prefs, brain, agents, chats = _project_hub(tmp_path, fake_openai)
    prefs.set("harness_cwd", str(proj))
    row = store.upsert(sid="", path=str(proj), label="Proj")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, before = await _call("GET", "/api/shortcuts")
    assert status == 200, before
    assert before["rows"][0]["busy"] is False, (
        f"a project with nothing spawned in it read busy: {before['rows']}")

    chat = chats.current()
    await brain.host.ensure(chat, await brain.host.plan(chat))

    status, after = await _call("GET", "/api/shortcuts")
    assert status == 200, after
    busy = {r["id"]: r["busy"] for r in after["rows"]}
    assert busy == {row.id: True}, (
        f"a genuinely busy project did not read busy through the route: "
        f"{busy}")


@pytest.mark.asyncio
async def test_post_project_stop_stops_the_busy_child_and_the_project_reads_idle_after(
        tmp_path, fake_openai, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    hub, store, prefs, brain, agents, chats = _project_hub(tmp_path, fake_openai)
    prefs.set("harness_cwd", str(proj))
    row = store.upsert(sid="", path=str(proj), label="Proj")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    chat = chats.current()
    await brain.host.ensure(chat, await brain.host.plan(chat))

    status, body = await _call("POST", f"/api/project/{row.id}/stop")

    assert status == 200, body
    assert body["stopped"] == [chat["id"]], (
        f"the busy chat was not named as stopped: {body}")
    assert agents[0].disposals == 1, "the busy child was never disposed"
    assert brain.host.child(chat["id"]) is None
    _s, after = await _call("GET", "/api/shortcuts")
    assert after["rows"][0]["busy"] is False, (
        "the project still reads busy after its only child was stopped")


@pytest.mark.asyncio
async def test_post_project_stop_with_nothing_busy_stops_nothing(
        tmp_path, fake_openai, monkeypatch):
    """No collision, no error -- the confirm's "yes" button reaching this
    route a moment too late (the TOCTOU the docstring names) must not read
    as a failure: the goal state, nothing busy left, already holds."""
    proj = tmp_path / "proj"
    proj.mkdir()
    hub, store, prefs, brain, agents, chats = _project_hub(tmp_path, fake_openai)
    row = store.upsert(sid="", path=str(proj), label="Proj")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, body = await _call("POST", f"/api/project/{row.id}/stop")

    assert status == 200, body
    assert body["stopped"] == []


@pytest.mark.asyncio
async def test_post_project_stop_for_an_unknown_project_id_is_a_400(
        tmp_path, fake_openai, monkeypatch):
    """The page listed these rows itself -- `post_project`'s own reasoning
    for the same status on the same kind of mismatch."""
    hub, store, prefs, brain, agents, chats = _project_hub(tmp_path, fake_openai)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, body = await _call("POST", "/api/project/s-nope/stop")

    assert status == 400, body


@pytest.mark.asyncio
async def test_post_project_stop_never_reaches_an_ordinary_turn_that_collides(
        tmp_path, fake_openai, monkeypatch):
    """The distinguishing mechanism itself, proved at the ROUTE level rather
    than the unit level `test_a_busy_collision_still_refuses_with_stop_for_
    wired` already covers above: an ordinary second turn into a BUSY project
    still refuses through `/api/chats` (a plain conversation turn, not this
    route), with the first child untouched -- confirming nothing on that
    path was widened by this route's existence."""
    proj = tmp_path / "proj"
    proj.mkdir()
    hub, store, prefs, brain, agents, chats = _project_hub(tmp_path, fake_openai)
    prefs.set("harness_cwd", str(proj))
    store.upsert(sid="", path=str(proj), label="Proj")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    first = chats.current()
    await brain.host.ensure(first, await brain.host.plan(first))

    await brain.new_conversation()
    said = _said(await _turn(brain, "second project attempt", turn_id=2))

    assert len(agents) == 1, (
        f"a second child was spawned into a project already BUSY, with no "
        f"confirm route ever called: {agents}")
    assert any("agent" in s.lower() for s in said), (
        f"the refusal did not name the collision: {said}")
    assert brain.host.child(first["id"]) is not None, (
        "the first, untouched child disappeared from an ordinary collision")
