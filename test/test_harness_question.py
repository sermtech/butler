"""A coding agent may ask you a question, and you answer it.

AC-234, AC-249, AC-250 (`docs/parallel-agents.md` phase 3 and section 9).
**AC-49 IS REVERSED HERE**: until 2026-09-12 `harness_pi.cancellation_for`
answered every dialog itself and told the agent the user had dismissed it, so
a blocked agent looked exactly like one that had decided on its own and
nobody ever learned it asked.

Four claims carry this file and each is a way the feature could lie.

**THE QUESTION IS BROADCAST** (AC-249). `send_for_turn` drops anything for a
turn the session does not own (D-6), and the device that ANSWERS is usually
not the device that asked -- the desk starts the agent and the phone is what
is in your hand when it stops. So the two-session case is not thoroughness:
a hub using `send_for_turn` passes every single-session assertion in this
file and ships the recorded Codex failure (a phone reading "Thinking" while
the desktop sits blocked).

**A LATE ANSWER IS REFUSED AND NEVER FORWARDED** (AC-250). The assertion with
teeth is the ANSWER COUNT ON THE CHILD, not the status code: a daemon that
answered 410 and wrote to the child anyway satisfies every status check here,
and what it produces is the owner believing they approved something the agent
already defaulted on. That failure is NOT recoverable.

**BOTH 180-SECOND TIMERS ARE SUSPENDED.** A waiting child emits nothing by
definition, which is exactly what `HARNESS_IDLE_S` exists to catch, so the
budget is shortened here and the question is held for several multiples of
it. `test_harness_idle.py` set that precedent and the reason is the same: at
the shipped 180 s the suite would have to wait out a real budget to assert
anything. The shipped constant is asserted separately, so shortening it in a
test cannot quietly become shortening it in the daemon.

**WHAT CLOSES IT GOES INTO THE TRANSCRIPT.** Nothing about a PENDING question
is ever written to disk -- every child is in a kill-on-close job object, so a
persisted question is one whose only possible answerer is already dead -- and
the line that IS written is what makes a reload and a rewind honest.
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

from conftest import attach_session, make_hub                # noqa: E402
# The turn driver is IMPORTED and never copied -- `test_desk.py`'s rule for
# `test_desk.py`'s reason: two copies of an instrument drift, and a drifted
# instrument is a test passing for the wrong reason.
from test_conversation import hub_turn                       # noqa: E402
from daemon import butlerd, ports                            # noqa: E402
from daemon import hub as hub_mod                            # noqa: E402
from daemon.adapters import brain_harness as bh              # noqa: E402
from daemon.adapters import harness_pi                       # noqa: E402
from daemon.chats import ChatStore                           # noqa: E402
from daemon.settings import Settings                         # noqa: E402

# Short, so the suite does not wait out a real budget -- `test_harness_idle.py`
# uses 0.4 for the same reason. Every wait below is a MULTIPLE of this, so the
# claim being made is a ratio and not a number of seconds.
IDLE_S = 0.3

SELECT = ports.Ask(qid="pi-ui-1", method="select",
                   title="The scheduler is off",
                   message="Three jobs are overdue. How do you want me to "
                           "proceed?",
                   options=("Turn it on and run the backlog",
                            "Turn it on, skip the backlog",
                            "Leave it off and just report"))
CONFIRM = ports.Ask(qid="pi-ui-2", method="confirm",
                    message="Delete build/ and rebuild from scratch?")
INPUT = ports.Ask(qid="pi-ui-3", method="input",
                  message="What should the new branch be called?",
                  placeholder="feature/...", prefill="feature/")
EDITOR = ports.Ask(qid="pi-ui-4", method="editor",
                   message="Edit the commit message")


def _hub(tmp_path, ask=(), harness_for=None):
    """A hub whose brain talks to a fake coding agent that ASKS something.

    `harness_only` rather than `harness`: what is under test is the dialog
    path, and the router in front of the other brain would need a scripted
    completion server for every turn without changing one assertion here.

    `harness_for` overrides the ONE fake this builds, for the test that needs
    two conversations to hold two distinguishable children.
    """
    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    harness = ports.build(ports.HARNESSES, "fake", ask=ask)
    chats = ChatStore(tmp_path / "chats")
    hub = make_hub(tmp_path)
    hub.brain = ports.build(ports.BRAINS, "harness_only",
                            harness_for=harness_for or (lambda _chat=None: harness),
                            spec_for=harness_spec_resolver(prefs, chats),
                            chats=chats,
                            cap_for=lambda: prefs["harness_agents_max"])
    hub.settings = prefs
    return hub, attach_session(hub), harness, chats


def frames(s, kind):
    """Every text frame of one type this session was sent, in order."""
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == kind]


async def until(predicate, what, timeout=6.0):
    """Poll until it is true, and FAIL rather than hang (test/AGENTS.md).

    A test that waits forever for an event reports nothing at all -- no
    output, no exit code -- and the port most likely to produce that is this
    one, whose whole new behaviour is a stream that deliberately stops.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{what} did not happen within {timeout:.0f}s")


@contextlib.asynccontextmanager
async def running_turn(hub, s, turn=700, text="tidy up the scheduler"):
    """A turn in flight, because a waiting turn is the whole subject.

    Every other harness test awaits the turn to completion; a turn parked on a
    question never completes until something answers it, so it has to run as a
    task beside the assertions.
    """
    task = asyncio.create_task(hub_turn(hub, s, turn=turn, text=text))
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@contextlib.asynccontextmanager
async def _http(hub, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# the four dialog methods reach the hub UNANSWERED (AC-234, AC-49 reversed)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ask", [SELECT, CONFIRM, INPUT, EDITOR],
                         ids=lambda a: a.method)
@pytest.mark.asyncio
async def test_a_dialog_of_every_method_reaches_a_human(tmp_path, monkeypatch,
                                                        ask):
    """AC-234. All four shapes cross the port and land on the wire.

    All FOUR, including `editor`, because the fake carries no policy: what
    auto-cancels `editor` is `harness_pi`, one layer down, and asserting the
    exception here as well would make the port's own behaviour depend on
    which adapter is behind it.

    The wire is asserted rather than the event, so a daemon that received the
    `Ask` and dropped it is visible. The OPTIONS matter as much as the text: a
    select cannot be answered by a text box, so a frame that arrived without
    them would draw a card nobody can use.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=ask)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"),
                    f"a {ask.method} dialog reaching the wire")
        sent = frames(s, "question")[0]
        assert sent["qid"] == ask.qid, (
            f"the daemon re-minted the question id: {sent['qid']!r} is not "
            f"the harness's own {ask.qid!r}. The answer has to reach the "
            f"dialog that raised it (AC-234)")
        assert sent["method"] == ask.method, sent
        assert sent["message"] == ask.message, sent
        assert sent["options"] == list(ask.options), (
            f"a {ask.method} reached the page without its options: {sent}")
        assert sent["placeholder"] == ask.placeholder, sent
        assert sent["prefill"] == ask.prefill, sent
        assert sent["deadline_ms"] is None, (
            f"Butler invented a deadline: {sent['deadline_ms']!r}. The clock "
            f"is the harness's and is passed through or absent (AC-234)")
        assert harness.answers == [], (
            f"the daemon answered the agent's question itself: "
            f"{harness.answers}. That is AC-49, and it is reversed")


@pytest.mark.asyncio
async def test_the_deadline_is_passed_through_and_never_invented(
        tmp_path, monkeypatch):
    """AC-234. pi's own `timeout`, drawn rather than run.

    pi auto-resolves a timed dialog AGENT-SIDE -- "the client does not need to
    track timeouts" -- and its resolved default is byte-identical to the
    cancel Butler used to send, so a Butler-invented deadline would be
    indistinguishable from the owner having said no. The number on the wire
    must therefore be the harness's or absent, which is what the parametrized
    case above asserts for the absent half.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    timed = ports.Ask(qid="pi-ui-9", method="confirm", message="proceed?",
                      deadline_ms=300_000)
    hub, s, harness, _ = _hub(tmp_path, ask=timed)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the timed question")
        assert frames(s, "question")[0]["deadline_ms"] == 300_000


# --------------------------------------------------------------------------- #
# AC-249 -- it reaches EVERY device, including one that does not own the turn
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_question_reaches_a_session_that_does_not_own_the_turn(
        tmp_path, monkeypatch):
    """AC-249, and this is the assertion the whole criterion rests on.

    The second session is given NO claim on the turn, which is what a phone in
    another room is: `SatelliteSession.owns(turn)` answers False for it, so
    `send_for_turn` would drop the frame silently. It is asserted that the
    phone got the question AND that it got it with the options, because half a
    card is a card nobody can answer.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, desk, harness, _ = _hub(tmp_path, ask=SELECT)
    phone = attach_session(hub, name="phone")

    async with running_turn(hub, desk, turn=701):
        await until(lambda: frames(phone, "question"),
                    "the question reaching the phone")

    assert not phone.owns(701), (
        "the second session owns the turn, so this test cannot tell a "
        "broadcast from a send_for_turn (D-13)")
    on_phone = frames(phone, "question")[0]
    assert on_phone["qid"] == SELECT.qid
    assert on_phone["options"] == list(SELECT.options), on_phone
    assert frames(desk, "question"), "the desk did not get its own question"


# --------------------------------------------------------------------------- #
# AC-250 -- a late answer is refused with the REASON, and never forwarded
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unknown_qid_is_410_and_never_reaches_the_child(
        tmp_path, monkeypatch):
    """AC-250, the criterion this feature cannot recover from getting wrong.

    pi drops an unknown dialog id SILENTLY, so a forwarded stale answer
    produces the Codex failure exactly: the human believes they approved and
    the agent already took the default. The assertion is therefore what the
    CHILD received, and the status is only the second half of it.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer", json={"qid": "no-such-question", "value": "x"})
            status, doc = answer.status, await answer.json()

    assert status == 410, (
        f"a question this daemon does not hold answered {status}; it has to "
        f"be refused, because a satellite cannot otherwise tell an answer "
        f"that landed from one that was too late (AC-250)")
    assert doc["reason"] == "daemon_restarted", doc
    assert harness.answers == [], (
        f"a stale answer reached the child anyway: {harness.answers}. That is "
        f"the owner believing they approved something the agent already "
        f"defaulted on (AC-250)")


@pytest.mark.parametrize("how,reason", [
    ("expired", "agent_resolved"),
    ("cancelled", "conversation_stopped"),
    ("agent_gone", "child_gone"),
])
@pytest.mark.asyncio
async def test_a_closed_question_is_refused_by_NAME(tmp_path, monkeypatch,
                                                    how, reason):
    """AC-250. All four reasons are nameable and the 410 names one.

    A refusal saying only "gone" leaves the owner unable to tell "the agent
    already decided" from "you were too slow" from "this daemon restarted",
    and each of those calls for a different next move.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the question")
        await hub.close_question(SELECT.qid, how)
        async with _http(hub, monkeypatch) as client:
            answer = await client.post("/api/answer",
                                       json={"qid": SELECT.qid, "value": "x"})
            status, doc = answer.status, await answer.json()

    assert status == 410, doc
    assert doc["reason"] == reason, doc
    assert harness.answers == [], harness.answers
    closed = frames(s, "question_closed")
    assert closed and closed[0]["how"] == how, (
        f"nothing told the page the question had closed: {closed}. Without it "
        f"the card stays up over a question that can no longer be answered")


@pytest.mark.asyncio
async def test_an_answer_with_no_shape_is_refused_before_the_child(
        tmp_path, monkeypatch):
    """400, not a guess. A dialog answered with the wrong key unblocks the
    agent with a default nobody chose, so a body that does not say what the
    answer IS is refused rather than filled in."""
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            blank = await client.post("/api/answer", json={"qid": SELECT.qid})
            noid = await client.post("/api/answer", json={"value": "x"})
            assert blank.status == 400, await blank.json()
            assert noid.status == 400, await noid.json()

    assert harness.answers == [], harness.answers


# --------------------------------------------------------------------------- #
# the answer goes back and the SAME child carries on
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_answer_unblocks_the_same_child_and_the_turn_finishes(
        tmp_path, monkeypatch):
    """AC-234's second half: answering it resumes that agent.

    One child across the whole thing (AC-59): a daemon that answered by
    respawning would satisfy "the turn finished" and would have thrown away
    everything the agent had done before it asked.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": SELECT.qid, "value": SELECT.options[0]})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    assert [q for q, _ in harness.answers] == [SELECT.qid], harness.answers
    assert harness.answers[0][1].value == SELECT.options[0], harness.answers
    assert len(harness.specs) == 1, (
        f"answering a question respawned the child: {harness.specs}")
    assert harness.disposals == 0, (
        f"the turn that was answered disposed {harness.disposals} child(ren); "
        f"a turn that ended cleanly holds its child (AC-59)")
    closed = frames(s, "question_closed")
    assert closed and closed[0]["how"] == "answered", closed


@pytest.mark.asyncio
async def test_a_confirm_is_answered_yes_or_no_and_not_with_text(
        tmp_path, monkeypatch):
    """The shape reaches the adapter intact. `{confirmed}` is what a confirm
    is waiting for, and a typed "yes" is not that."""
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=CONFIRM)

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            answer = await client.post("/api/answer",
                                       json={"qid": CONFIRM.qid,
                                             "confirmed": False})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    assert harness.answers[0][1] == ports.Answer(confirmed=False), \
        harness.answers


# --------------------------------------------------------------------------- #
# BOTH 180-second timers are suspended (AC-234)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_idle_deadline_is_suspended_while_a_question_waits(
        tmp_path, monkeypatch):
    """AC-234. A waiting child emits nothing, which is what D-34 fires on.

    The question is held for SIX TIMES the idle budget. At the shipped 180 s
    that is eighteen minutes of a human thinking about a question, which is
    ordinary -- the prior art is unanimous that a permission gate waits, and
    the one product that refuses to (Jules) is the one with complaints about
    decisions nobody made.

    Three things are asserted together, because each alone has a way of
    passing while the feature is broken: the question is still open, NOTHING
    was respawned (a timeout exits through `_give_up`, which disposes), and
    the turn still finishes when it is finally answered.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        await asyncio.sleep(IDLE_S * 6)

        assert frames(s, "question_closed") == [], (
            f"the question closed itself while nobody was answering it: "
            f"{frames(s, 'question_closed')}. That is the bucket emptying "
            f"after three minutes, which is one of the three ways AC-234 "
            f"fails")
        assert hub.questions_for(
            hub.brain.session_state()["session"]), (
            "the daemon stopped holding the question, so a reloading page "
            "would draw nothing (AC-249)")
        assert harness.disposals == 0 and len(harness.specs) == 1, (
            f"the idle deadline fired on a waiting child: "
            f"{harness.disposals} disposal(s), {len(harness.specs)} spawn(s). "
            f"`HARNESS_IDLE_S` has to be suspended while a question is open")
        assert not task.done(), "the turn ended while its question was open"

        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": SELECT.qid, "value": SELECT.options[1]})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    assert harness.answers, "the answer never reached the child"


def test_the_shipped_idle_budget_is_still_the_measured_one():
    """The tests above shorten `HARNESS_IDLE_S`; this is what stops that
    becoming a change to the daemon. 180 s is MEASURED -- 146 gaps in six real
    pi turns, max 78.6 s -- and the suspension above is a separate rule from
    the number."""
    assert bh.HARNESS_IDLE_S >= 60, (
        f"the idle budget is {bh.HARNESS_IDLE_S}s; a coding agent legitimately "
        f"thinks for minutes")


def test_the_page_suspends_its_own_watchdog_too():
    """The other 180-second timer (AC-234). A grep, because nothing in this
    directory can run `app.js` -- and the daemon suspending its half alone
    would leave the page giving up on the turn at three minutes and taking the
    card with it. `test_visualiser_contract.py` set the precedent for reading
    `web/` this way."""
    source = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        encoding="utf-8")
    head = source.split("const budget = WATCHDOG_MS[state];")[0]
    assert "if (openQuestions.size) return;" in head.split("function armWatchdog")[-1], (
        "`armWatchdog` does not return early while a question is open, so the "
        "page's own 180 s thinking watchdog fires on a turn that is waiting "
        "for the owner and calls `goListen()` under it (AC-234)")


# --------------------------------------------------------------------------- #
# what closes a question goes into the TRANSCRIPT (AC-234, section 9.4)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_answer_is_written_into_the_transcript(tmp_path, monkeypatch):
    """A reload and a rewind have to be honest about the pause.

    Nothing about a PENDING question is written -- every child is in a
    kill-on-close job object, so a persisted question has no possible answerer
    and the next reader would trust it -- and the line written when it CLOSES
    is the whole of what survives.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, chats = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        assert not any("Butler asked" in m["content"]
                       for m in chats.messages()), (
            "a question was written to the record before it was answered")
        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": SELECT.qid, "value": SELECT.options[2]})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    note = [m["content"] for m in chats.messages() if "Butler asked" in m["content"]]
    assert len(note) == 1, (
        f"the closed question left {len(note)} lines in the transcript: "
        f"{note}")
    assert SELECT.message in note[0], note
    assert SELECT.options[2] in note[0], note


@pytest.mark.asyncio
async def test_a_question_is_filed_under_the_TURNS_conversation(tmp_path,
                                                                monkeypatch):
    """AC-249's `chat`, and it was the POINTER rather than the turn.

    `_on_ask_human` read `current_chat_id()` while holding the turn that could
    have answered it. Measured: a turn running in A with the daemon's pointer
    at B put `chat=B` on the card and left `questions_for(A)` empty -- so a
    page repainting conversation A would draw no question, and one repainting
    B would draw a question about work it is not doing. The page is
    accidentally immune today only because it ignores `q.chat`, which is not a
    property this daemon may rely on.

    Children are MINTED per spawn: a fixture handing back one fake cannot hold
    two conversations at once, and the fake refuses `start()` on a live child.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    minted: list = []

    def mint(_chat=None):
        minted.append(ports.build(ports.HARNESSES, "fake", ask=SELECT))
        return minted[-1]

    hub, s, _harness, chats = _hub(tmp_path, ask=SELECT, harness_for=mint)
    first = chats.current()["id"]
    second = chats.rotate()["id"]
    assert chats.current()["id"] == second
    # The device is looking at the FIRST conversation; the daemon's pointer is
    # on the second.
    await hub._on_text(s, json.dumps({"type": "showing", "chat": first}))

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        card = frames(s, "question")[0]
        assert card["chat"] == first, (
            f"the card names {card['chat']!r}; the turn is in {first!r}. A "
            f"question filed under the pointer is one the conversation that "
            f"asked it cannot show (AC-249)")
        assert [q["qid"] for q in hub.questions_for(first)] == [SELECT.qid], (
            f"questions_for({first!r}) is empty, so a page opening the "
            f"conversation that is waiting draws no card: "
            f"{hub.questions_for(first)}")
        assert hub.questions_for(second) == [], (
            f"the question was filed under the conversation nobody is talking "
            f"to: {hub.questions_for(second)}")

        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": SELECT.qid, "value": SELECT.options[0]})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_the_answer_NOTE_goes_into_the_conversation_that_asked(
        tmp_path, monkeypatch):
    """The transcript line follows the QUESTION, not whatever is current now.

    `Hub.answer_question` already holds `question["chat"]`, and
    `brain.record_note` threw it away: the line landed in whichever
    conversation `ChatStore.current()` named at WRITE time. Driven by starting
    a new conversation while the agent is parked on its dialog, which is an
    ordinary thing to do while waiting -- and then answering.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, chats = _hub(tmp_path, ask=SELECT)
    asked_in = chats.current()["id"]

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        # The owner starts a new conversation while the agent waits.
        moved_to = chats.rotate()["id"]
        assert moved_to != asked_in

        async with _http(hub, monkeypatch) as client:
            answer = await client.post(
                "/api/answer",
                json={"qid": SELECT.qid, "value": SELECT.options[1]})
            assert answer.status == 200, await answer.json()
        await asyncio.wait_for(task, timeout=10)

    kept = chats.find(asked_in)["messages"]
    assert [m for m in kept if "Butler asked" in m["content"]], (
        f"the line left the conversation the question was asked in: {kept}")
    assert not [m for m in chats.find(moved_to)["messages"]
                if "Butler asked" in m["content"]], (
        f"a conversation that was never asked anything records having been: "
        f"{chats.find(moved_to)['messages']}")


@pytest.mark.asyncio
async def test_a_reloading_page_is_served_the_open_question(tmp_path,
                                                            monkeypatch):
    """AC-249's second half: recovery needs NO protocol.

    The daemon holds the question and serves it on demand, so a reload and a
    second device both work through the route they were already asking. MCP
    deleted server-initiated requests and resumability in its 2026-07-28
    revision because redelivery across a reconnect cannot be made to work.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the question")
        async with _http(hub, monkeypatch) as client:
            got = await client.get("/api/session/current")
            doc = await got.json()

    assert [q["qid"] for q in doc["questions"]] == [SELECT.qid], doc
    assert doc["questions"][0]["options"] == list(SELECT.options), doc


@pytest.mark.asyncio
async def test_a_turn_that_ends_takes_its_question_with_it(tmp_path,
                                                           monkeypatch):
    """The card cannot outlive the child that raised it.

    A stopped, failed or barged-in turn has already disposed its child, so a
    question still on screen would be a control that lies -- and the answer
    would be refused by a 410 the owner had no way to predict.
    """
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    hub, s, harness, _ = _hub(tmp_path, ask=SELECT)

    async with running_turn(hub, s) as task:
        await until(lambda: frames(s, "question"), "the question")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    assert hub.questions_for(hub.brain.session_state()["session"]) == [], (
        "the daemon still holds a question for a turn that is over")
    closed = frames(s, "question_closed")
    assert closed and closed[0]["how"] == "cancelled", closed


# --------------------------------------------------------------------------- #
# the two STATED exceptions, in the adapter that has them (`harness_pi`)
# --------------------------------------------------------------------------- #
def test_editor_is_still_auto_cancelled_and_the_other_three_are_not():
    """AC-234's one method exception, and it is mechanical rather than taste.

    `editor` asks the client to open a text editor on a buffer: no Butler
    surface can render it and no voice can dictate it, and it is the one
    method with NO agent-side timeout path at all, so leaving it unanswered
    blocks the child forever with nothing on any screen that could unblock it.
    """
    for method in ("select", "confirm", "input"):
        assert harness_pi.cancellation_for({"id": "x", "method": method},
                                           True) is None, (
            f"a {method} dialog is still being answered by the daemon; that "
            f"is AC-49 and it is reversed (AC-234)")
    cancelled = harness_pi.cancellation_for({"id": "x", "method": "editor"},
                                            True)
    assert cancelled == {"type": "extension_ui_response", "id": "x",
                         "cancelled": True}, cancelled


def test_a_dialog_raised_with_no_prompt_in_flight_is_still_auto_cancelled():
    """The second stated exception. Three `extension_ui_request`s arrive
    UNPROMPTED before the first response even in the simplest configuration
    [MEASURED], and a dialog raised while no prompt is running has no turn to
    attach to and no conversation for it to make `waiting`."""
    for method in ("select", "confirm", "input", "editor"):
        assert harness_pi.cancellation_for({"id": "x", "method": method},
                                           False) is not None, (
            f"an UNPROMPTED {method} dialog was left for a human; there is no "
            f"turn it could be drawn against")
    assert harness_pi.cancellation_for({"id": "x", "method": "setStatus"},
                                       True) is None, (
        "a fire-and-forget method was answered; writing to the child's stdin "
        "for one puts a line there its protocol never asked for")


def test_the_pi_adapter_writes_pi_own_spelling_and_nothing_else():
    """AC-41. `{value}` / `{confirmed}` / `{cancelled}` stays in this file.

    And it RAISES on a shape the method cannot take rather than sending a
    field pi would ignore: a dialog answered with the wrong key unblocks the
    agent with a default nobody chose, which is AC-250's failure arriving
    through the front door instead of the back.
    """
    assert harness_pi._pi_answer("select", ports.Answer(value="a")) == \
        {"value": "a"}
    assert harness_pi._pi_answer("confirm", ports.Answer(confirmed=True)) == \
        {"confirmed": True}
    assert harness_pi._pi_answer("input", ports.Answer(cancelled=True)) == \
        {"cancelled": True}
    with pytest.raises(ValueError):
        harness_pi._pi_answer("confirm", ports.Answer(value="yes"))
    with pytest.raises(ValueError):
        harness_pi._pi_answer("input", ports.Answer(confirmed=True))


def test_an_extension_request_becomes_an_Ask_with_pi_own_id():
    """`qid` is pi's and Butler never mints one -- `ToolStart.tool_id`'s rule
    for its reason: the answer has to be routed back to the dialog that raised
    it, and an id Butler invented would have to be mapped onto that one."""
    ask = harness_pi.ask_for({
        "id": "ext-42", "type": "extension_ui_request", "method": "select",
        "params": {"title": "pick one", "message": "which?",
                   "options": ["a", {"label": "b"}], "timeout": 120000}})
    assert ask.qid == "ext-42"
    assert ask.method == "select"
    assert ask.options == ("a", "b"), (
        f"a structured option survived into the port: {ask.options}. They are "
        f"drawn as buttons AND said by a voice answer, so a record with a "
        f"hidden value would be unsayable")
    assert ask.deadline_ms == 120000, (
        "pi's own timeout was dropped; Butler draws the clock it was handed "
        "and never invents one (AC-234)")


def test_the_harness_port_still_has_exactly_one_closed_event_union():
    """The union is CLOSED, so `Ask` is in it or no adapter may emit one."""
    members = set(ports.HarnessEvent.__args__)
    assert ports.Ask in members, (
        "Ask is not a member of HarnessEvent, so an adapter emitting one is "
        "emitting something outside the contract")
    assert ports.AskHuman in set(ports.Intent.__args__), (
        "AskHuman is not a member of Intent, so a brain yielding one is "
        "yielding something the hub is not required to handle")
