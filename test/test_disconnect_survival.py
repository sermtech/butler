"""A disconnect must not kill a harness turn. Owner's ruling, 2026-09-16.

`Hub.handle`'s `finally` (hub.py:1061-1064) called `session.abandon()`
unconditionally on every socket close, and `abandon()` aborts the brain and
cancels the reply task whenever the front-door turn is THINKING or SPEAKING
(hub.py:477-528). For a harness turn that means killing the coding agent
doing the owner's real work -- the long local run the owner is complaining
cannot survive a refresh, a closed laptop, or a moment of dropped wifi.
`docs/parallel-agents.md` phase 7 (329c965, d742f15) is what made this
visible: the page now counts up on something F5 destroys.

Two claims, and each is the negative of the other so neither passes by
accident. A HARNESS turn -- `HAS_SESSIONS` true, a live child running a
prompt -- must SURVIVE: nothing aborted, nothing disposed, and the answer
still lands in the record, recoverable through
`GET /api/chats/{cid}/status` (AC-236's route, unused for exactly this case
until now). A CONVERSATIONAL turn -- no session store, no chat id a
reconnecting page could ever recover through that route -- gets the OLD
teardown: the brain is aborted and the turn does not outlive its socket.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import make_hub                                  # noqa: E402
from daemon import butlerd, hub as hub_mod, ports               # noqa: E402
from daemon.adapters import brain_harness as bh                 # noqa: E402
from daemon.chats import ChatStore                              # noqa: E402
from daemon.ports import Done, Say                              # noqa: E402
from daemon.settings import Settings                            # noqa: E402

from test_harness_question import SELECT                        # noqa: E402

# Short, so the suite does not wait out a real budget -- test_harness_
# question.py's precedent, same reason.
IDLE_S = 0.3


def _harness_hub(tmp_path):
    """A hub whose brain talks to a fake coding agent that ASKS something,
    so the fake blocks (`FakeHarness.asks`) and the test can be sure the
    child is genuinely still working rather than racing its completion."""
    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    harness = ports.build(ports.HARNESSES, "fake", ask=SELECT)
    chats = ChatStore(tmp_path / "chats")
    hub = make_hub(tmp_path)
    hub.brain = ports.build(ports.BRAINS, "harness_only",
                            harness_for=lambda _chat=None: harness,
                            spec_for=harness_spec_resolver(prefs, chats),
                            chats=chats,
                            cap_for=lambda: prefs["harness_agents_max"])
    hub.settings = prefs
    return hub, harness, chats


class HangingBrain:
    """A conversational brain with no session store -- `HAS_SESSIONS` is
    absent, like the real endpoint brain -- that blocks until aborted.

    `abort()` CANCELS the pending future rather than resolving it, because
    that is what a real adapter's abort does -- it cancels the in-flight
    HTTP request, so the awaiting coroutine raises rather than proceeding
    to a normal answer (see `dialogue.Dialogue.turn`'s `except
    llm.Aborted`). A fake that resolved instead of cancelling would let the
    generator race its own `abort()` to a "successful" answer and prove
    nothing about being cut short.
    """
    ENGINE = "hanging"

    def __init__(self):
        self.aborted: list[int] = []
        self.ran_to_completion = False
        self._fut: asyncio.Future | None = None

    async def turn(self, text, ctx):
        self._fut = asyncio.get_running_loop().create_future()
        await self._fut
        self.ran_to_completion = True
        yield Say("done", tier="conversation")
        yield Done("conversation")

    async def abort(self, turn_id):
        self.aborted.append(turn_id)
        if self._fut is not None and not self._fut.done():
            self._fut.cancel()

    async def warm(self):
        pass

    @property
    def info(self):
        return {"engine": self.ENGINE}


@contextlib.asynccontextmanager
async def _app(hub, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def _hello(ws, device="test-desk"):
    await ws.send_str(json.dumps({"type": "hello", "proto": 1,
                                  "device_id": device, "name": device,
                                  "caps": {"wake": True, "tts": True}}))
    return await _next(ws, "ready")


async def _next(ws, kind, timeout=6.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {kind!r}")
        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        if msg.type.name != "TEXT":
            continue
        m = json.loads(msg.data)
        if m.get("type") == kind:
            return m


async def until(predicate, what, timeout=6.0):
    """Poll until it is true, and FAIL rather than hang (test/AGENTS.md)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{what} did not happen within {timeout:.0f}s")


# --------------------------------------------------------------------------- #
# a HARNESS turn survives (owner's ruling)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_harness_turn_survives_a_socket_close_and_still_records(
        tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, harness, chats = _harness_hub(tmp_path)
    chat_id = chats.current()["id"]

    async with _app(hub, monkeypatch) as client:
        ws = await client.ws_connect("/ws")
        await _hello(ws)
        await ws.send_str(json.dumps({"type": "typed", "text": "tidy up"}))
        await _next(ws, "question")   # the child is really running (AC-234)

        await ws.close()              # the disconnect

        await asyncio.sleep(0.3)
        assert harness.aborts == 0, (
            f"the disconnect aborted the running child: {harness.aborts} "
            f"abort(s). A refresh must not kill a harness turn")
        assert harness.disposals == 0, (
            f"the disconnect disposed the child: {harness.disposals} "
            f"disposal(s). AC-59 holds it for the whole conversation")

        status = hub.status_for(chat_id)
        assert status in ("waiting", "running"), (
            f"status_for({chat_id!r}) reads {status!r} with no session "
            f"attached; a reconnecting page recovers through exactly this "
            f"route (GET /api/chats/{{cid}}/status, AC-236) and must not "
            f"read a still-running turn as over")

        answer = await client.post(
            "/api/answer",
            json={"qid": SELECT.qid, "value": SELECT.options[0]})
        assert answer.status == 200, await answer.json()

        await until(
            lambda: any(m["role"] == "assistant"
                       for m in chats.find(chat_id)["messages"]),
            "the answer landing in the record with nobody connected",
            timeout=6.0)
        # The message lands mid-`finally`, before `_rearm` -- give the
        # orphaned task the rest of its own teardown before asking whether
        # it reached a terminal status (D-1: every terminal path re-arms).
        await until(lambda: chat_id not in hub._orphaned,
                   "the orphaned task's own bookkeeping clearing itself",
                   timeout=6.0)

    assert harness.disposals == 0, (
        "a turn that finished cleanly disposed its child; AC-59 holds it "
        "for the whole conversation")
    assert hub.status_for(chat_id) in ("done", "idle"), (
        f"the surviving turn never reached a terminal status: "
        f"{hub.status_for(chat_id)!r} (D-1: every terminal path re-arms)")


@pytest.mark.asyncio
async def test_a_surviving_harness_turn_is_not_marked_idle_by_the_disconnect(
        tmp_path, monkeypatch):
    """docs/filing-and-pacing.md 1.6, section 7's risk #2, named explicitly.

    `_idle_since` (`daemon/adapters/brain_harness.py`) is set at the two
    places a turn actually ENDS (`HarnessConverse._answer`, `._give_up`) and
    NOWHERE else. If a socket close ever set it instead, the per-project
    reaper (`_Host._enforce_project_limit`) could reap this conversation's
    slot for a SECOND chat on the same project while the owner is still
    waiting on the very turn that just survived the disconnect (`017f8a1`)
    -- silence being read as "done" a second way, one layer under D-1.

    Asserted at both ends: NOT idle the moment the socket closes (busy is
    busy, whoever is watching), and idle once the turn actually finishes,
    or a new chat on this project would refuse forever with nothing ever
    freeing the slot.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, harness, chats = _harness_hub(tmp_path)
    chat_id = chats.current()["id"]

    async with _app(hub, monkeypatch) as client:
        ws = await client.ws_connect("/ws")
        await _hello(ws)
        await ws.send_str(json.dumps({"type": "typed", "text": "tidy up"}))
        await _next(ws, "question")   # the child is really running (AC-234)

        await ws.close()              # the disconnect

        await asyncio.sleep(0.3)
        assert chat_id not in hub.brain.host._idle_since, (
            "the disconnect marked a still-BUSY turn's project idle -- a "
            "second chat on the same project could reap this one's slot "
            "while the owner is still waiting on the answer")

        answer = await client.post(
            "/api/answer",
            json={"qid": SELECT.qid, "value": SELECT.options[0]})
        assert answer.status == 200, await answer.json()

        await until(
            lambda: any(m["role"] == "assistant"
                       for m in chats.find(chat_id)["messages"]),
            "the answer landing in the record with nobody connected",
            timeout=6.0)
        await until(lambda: chat_id not in hub._orphaned,
                   "the orphaned task's own bookkeeping clearing itself",
                   timeout=6.0)

    assert chat_id in hub.brain.host._idle_since, (
        "the turn completed but its project was never marked idle -- a "
        "new chat on the same project would refuse forever, with nothing "
        "ever freeing the slot")


# --------------------------------------------------------------------------- #
# status_for's own branch, isolated from the full disconnect (D-13 witness:
# the full-flow test above passes on "waiting" -- the open question --
# whether or not this branch exists at all, so it cannot catch this alone)
# --------------------------------------------------------------------------- #
def test_status_for_reads_the_orphaned_registry_with_no_session_left(
        tmp_path):
    """A chat in `_orphaned` reads "running" even with no session in
    `self.sessions` and no live child to ask -- the exact shape a turn
    `_detach_survivable` let outlive its socket leaves behind once its
    question (if any) is long answered and it is mid-generation again."""
    hub, harness, chats = _harness_hub(tmp_path)
    chat_id = chats.current()["id"]
    assert hub.status_for(chat_id) != "running", (
        "an untouched conversation with no session and no orphaned entry "
        "already reads running; this test proves nothing")
    # `(turn, task)`, the real shape `hub.py:792` declares -- a bare Future
    # passed here for a while and only because `status_for` checks
    # MEMBERSHIP and never unpacks. The task slot is None because THIS test
    # is synchronous and has no loop to make one on: it isolates
    # `status_for`'s branch, nothing more. A Future here needed
    # `get_event_loop()` to invent a loop, which Python 3.12 removed -- so
    # this line passed only on the older interpreter the suite was being run
    # under by mistake, and raised the moment it met the one Butler runs on.
    hub._orphaned[chat_id] = (999, None)
    assert hub.status_for(chat_id) == "running", (
        f"status_for({chat_id!r}) is {hub.status_for(chat_id)!r} with the "
        f"chat in hub._orphaned; a reconnecting page would read a still-"
        f"running turn as over")


# --------------------------------------------------------------------------- #
# a CONVERSATIONAL turn does not
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_conversational_turn_does_not_survive_a_socket_close(
        tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    brain = HangingBrain()
    hub.brain = brain

    async with _app(hub, monkeypatch) as client:
        ws = await client.ws_connect("/ws")
        await _hello(ws)
        await ws.send_str(json.dumps({"type": "typed", "text": "hello"}))
        await until(
            lambda: hub.sessions and
                   next(iter(hub.sessions)).state == hub_mod.THINKING,
            "the turn reaching THINKING", timeout=3.0)

        await ws.close()              # the disconnect

        await until(lambda: brain.aborted, "the brain being aborted",
                   timeout=3.0)

    assert brain.aborted, (
        "a conversational turn's disconnect did not abort the brain; only "
        "a HARNESS turn may survive a socket close (owner's ruling, "
        "2026-09-16)")
    assert not brain.ran_to_completion, (
        "the conversational turn ran to completion after its socket "
        "closed -- it should have been cut short, not merely unheard")
