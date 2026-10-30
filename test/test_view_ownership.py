"""Which conversation is this frame about? AC-238's mechanism, AC-246, AC-247.

`docs/parallel-agents.md` phase 6. The owner ruled (D4) that **a device speaks
the conversation that device is showing**, and the review found the ruling had
no mechanism at all: the daemon had no way to learn what any device was
looking at, and the only way to stop speech was `abandon()`, which in harness
mode DISPOSES the coding agent.

Four claims carry this file and each is a way the feature could lie.

**THE MUTE MUST LEAVE THE CHILD UP, AND THE NEGATIVE IS THE POINT.** A daemon
that went quiet by abandoning the turn satisfies every assertion about silence
in this file -- no audio, the room quiet, the state re-armed -- and what it
produces is the agent you just looked away from being killed mid-edit, which
is what AC-242 and AC-243 exist to prevent and is NOT recoverable. So the
assertion with teeth is `harness.disposals` and `harness.aborts`, and the
silence is the second half of it.

**THE HANDS-FREE PATH IS ASSERTED POSITIVELY** (AC-246). Butler's first
sentence is that it is an always-on voice assistant, so a turn with no page
open anywhere is the NORMAL case. A device that has never said what it is
showing hears everything and its turn lands in the conversation the daemon is
HOLDING -- and that is checked rather than left to be inferred from the mute
test passing, because "nobody is muted" and "nothing is ever muted" look
identical from one direction.

**THE FIELD IS ASSERTED, NOT THE FRAME NAME.** `test_protocol_doc.py` compares
frame NAMES only, so a `ready` that forgot `chat` or a `capture_start` that
forgot it would both go green there. That gap is named in the spec and it is
real, so the field-level assertions live here.

**AN ATTACHMENT IS ASSERTED FROM BOTH ENDS.** A route that filed the file
under the right conversation and a turn that took everything pending are
indistinguishable unless the test drives a turn in the OTHER conversation and
then comes back -- so it does both: B's turn must get nothing, and A's must
still get it afterwards.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub                 # noqa: E402
from test_attachments import PNG, _http, _post                # noqa: E402
from test_typed_turn import type_at                           # noqa: E402
from daemon import ports                                      # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon.adapters.tts_fake import FakeTts                   # noqa: E402
from daemon.chats import ChatStore                            # noqa: E402
from daemon.settings import Settings                          # noqa: E402
from daemon import attachments as attach_mod                   # noqa: E402


class GatedTts(FakeTts):
    """The fake speech engine, held on the first segment of a REAL reply.

    "Mid-reply" has to be a moment and not a race. The fake harness produces
    its events as fast as the reader takes them, so without a gate the whole
    answer is synthesized before a test could possibly look away, and the
    assertion would be measuring the scheduler.

    `ready` is what makes the gate land in the right place rather than on
    whichever segment happens to be first. Harness mode speaks a STATUS before
    it spawns anything (`Say(keep=False)`, D-10), and a status sends no `reply`
    frame -- so a gate on call number one would stop the answer before a word
    of it had been spoken, and the test would be asserting that a reply nobody
    had heard any of went quiet. The predicate asks whether any of the ANSWER
    has been heard yet -- `s.spoken`, which only a completed audio leg fills.
    Not the page's `spoken` flag: since text stopped waiting for audio
    (docs/filing-and-pacing.md item 2) that flag goes out before a word of
    the sentence is synthesized, so it no longer means anything was heard.
    """

    def __init__(self, ready):
        super().__init__(speed=0.0)
        self.ready = ready
        self.held = asyncio.Event()      # the gate is holding a real segment
        self.gate = asyncio.Event()
        self.calls = 0
        self.calls_at_gate = 0

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.calls += 1
        if not self.held.is_set() and self.ready():
            self.calls_at_gate = self.calls
            self.held.set()
            await self.gate.wait()
        async for chunk in super().synthesize(text, voice, speed=speed,
                                              handle=handle):
            yield chunk


class Agents:
    """One fake child per spawn, newest last (`test_parallel_agents.py`).

    The single-fake fixture below is the honest shape for every test that
    drives ONE conversation, and it is the one shape that cannot tell "two
    children" from "one child started twice" -- the fake refuses `start()` on
    a live child. The two tests that assert which conversation a turn RUNS in
    need that difference, so they hand this in instead.
    """

    def __init__(self) -> None:
        self.all: list = []

    def mint(self, _chat=None):
        self.all.append(ports.build(ports.HARNESSES, "fake"))
        return self.all[-1]

    @property
    def specs(self) -> list:
        return [spec for child in self.all for spec in child.specs]


def harness_hub(tmp_path, tts=None, harness_for=None):
    """A hub whose brain talks to a fake coding agent and holds conversations.

    `harness_only` rather than `harness`, `test_harness_question.py`'s reason:
    what is under test is ownership, and the router in front of the other
    brain would need a scripted completion server for every turn without
    changing one assertion here.

    `harness_for` overrides the ONE fake this builds by default, for the tests
    that need two children to be distinguishable from one child started twice.
    """
    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    harness = ports.build(ports.HARNESSES, "fake")
    chats = ChatStore(tmp_path / "chats")
    hub = make_hub(tmp_path)
    hub.tts = tts if tts is not None else ports.build(ports.TTSS, "fake")
    hub.brain = ports.build(ports.BRAINS, "harness_only",
                            harness_for=harness_for or (lambda _chat=None: harness),
                            spec_for=harness_spec_resolver(prefs, chats),
                            chats=chats,
                            # Phase 5 made the cap a setting and gave the brain
                            # the reader for it. This file was born in a
                            # worktree that predated it, which is the one thing
                            # two parallel branches cannot tell each other.
                            cap_for=lambda: prefs["harness_agents_max"])
    hub.settings = prefs
    hub.attachments = attach_mod.AttachmentStore(tmp_path / "attachments")
    return hub, attach_session(hub), harness, chats


def replies(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == "reply"]


def errors(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == "error"]


async def until(predicate, what, timeout=6.0):
    """Poll until it is true, and FAIL rather than hang (test/AGENTS.md)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{what} did not happen within {timeout:.0f}s")


# --------------------------------------------------------------------------- #
# a device says what it is showing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_device_says_what_it_is_SHOWING(tmp_path):
    """The frame the daemon had no way to receive (AC-238's mechanism).

    Driven through `_on_text` and not by calling the handler: the message
    SHAPE is half the contract, and a daemon that implemented this under
    another name would pass a handler-level test while answering the page
    "unsupported message type in protocol v1".
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    assert s.showing is None, "a fresh session claims to be showing something"

    await hub._on_text(s, json.dumps({"type": "showing", "chat": "chat-a"}))
    assert s.showing == "chat-a", s.showing
    assert not errors(s), errors(s)

    await hub._on_text(s, json.dumps({"type": "showing", "chat": None}))
    assert s.showing is None, (
        f"null means nothing is on screen and must clear it: {s.showing!r}")


@pytest.mark.asyncio
async def test_a_showing_frame_that_is_not_an_id_is_refused_and_changes_NOTHING(
        tmp_path):
    """A refusal that left the view half-written would mute this device
    against a conversation nobody named."""
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    await hub._on_text(s, json.dumps({"type": "showing", "chat": "chat-a"}))

    await hub._on_text(s, json.dumps({"type": "showing", "chat": 7}))

    assert s.showing == "chat-a", (
        f"a refused frame overwrote the view anyway: {s.showing!r}")
    assert errors(s), "the refusal was silent"


# --------------------------------------------------------------------------- #
# AC-246 -- a turn always names its conversation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ready_tells_a_fresh_device_WHICH_conversation_it_is_on(tmp_path):
    """AC-246, and the field-level assertion `test_protocol_doc.py` cannot
    make: it compares frame NAMES only, so a `ready` that forgot `chat` goes
    green there while a device has no way to know where its next word lands.
    """
    hub, s, _harness, chats = harness_hub(tmp_path)

    await hub._on_text(s, json.dumps({"type": "hello", "device_id": "desk"}))

    ready = [json.loads(m) for m in s.ws.sent
             if isinstance(m, str) and json.loads(m)["type"] == "ready"][-1]
    assert "chat" in ready, f"ready names no conversation: {sorted(ready)}"
    assert ready["chat"] == chats.current()["id"], ready["chat"]


@pytest.mark.asyncio
async def test_a_SPOKEN_turn_names_its_conversation(tmp_path):
    """AC-246 on the frame that starts one. What a spoken turn starts may be a
    full-capability coding agent in a directory nobody said out loud, which is
    AC-31's failure arriving through a door AC-31 does not guard."""
    hub, s, _harness, chats = harness_hub(tmp_path)

    await hub._start_capture(s)
    try:
        start = [json.loads(m) for m in s.ws.sent
                 if isinstance(m, str)
                 and json.loads(m)["type"] == "capture_start"][-1]
        assert "chat" in start, (
            f"capture_start names no conversation: {sorted(start)}")
        assert start["chat"] == chats.current()["id"], start["chat"]
    finally:
        await s.abandon()


@pytest.mark.asyncio
async def test_a_device_showing_NOTHING_lands_in_the_conversation_the_daemon_HOLDS(
        tmp_path):
    """AC-246's hands-free answer, asserted positively.

    Butler is an always-on voice assistant, so an utterance with no page open
    anywhere is the normal case rather than an edge one. It goes to the
    conversation the daemon is holding -- `ChatStore.current()`, the record
    `_attach()` chose at boot -- and the second half of the test is what makes
    the first mean anything: OPEN another conversation and the next turn goes
    there instead, so this is the daemon's pointer being read rather than one
    id being right by accident.
    """
    hub, s, _harness, chats = harness_hub(tmp_path)
    first = chats.current()["id"]

    await type_at(hub, s, "where does this go")
    assert s.turn_chat == first, (s.turn_chat, first)

    second = chats.rotate()["id"]
    assert second != first
    await type_at(hub, s, "and where does this one go")
    assert s.turn_chat == second, (s.turn_chat, second)


@pytest.mark.asyncio
async def test_a_device_that_SAYS_what_it_shows_overrules_the_pointer(tmp_path):
    """The other half of the one resolver: what you are looking at is what you
    are talking to."""
    hub, s, _harness, chats = harness_hub(tmp_path)
    held = chats.current()["id"]

    await hub._on_text(s, json.dumps({"type": "showing",
                                      "chat": "chat-elsewhere"}))
    assert hub.chat_for(s) == "chat-elsewhere", hub.chat_for(s)
    assert hub.current_chat_id() == held, (
        "the daemon's own pointer moved, which is not what a view may do")


# --------------------------------------------------------------------------- #
# AC-241 / AC-246 -- the conversation a turn NAMES is the one it writes into
# --------------------------------------------------------------------------- #
def _roles(chat) -> list[str]:
    return [m["role"] for m in chat["messages"]]


@pytest.mark.asyncio
async def test_a_turn_records_into_the_conversation_it_STARTED_in(tmp_path):
    """AC-241, and the claim `Hub.open_conversation`'s own docstring makes.

    That docstring says a page which opens a conversation mid-reply "is
    answered by the turn that is already running, INTO THE RECORD IT STARTED
    IN". It was not true: `ChatStore.append` read `current()` at WRITE time,
    so the owner clicking another row while an agent was mid-answer moved
    every remaining message of that turn into the row they clicked. The turn
    went on doing its work in the first conversation's directory, so the
    record and the work disagreed -- and the conversation that asked the
    question kept the question and nothing else.

    The gate is ARMED rather than always on: the first turn is what gives the
    first conversation something to hold, and a gate that fired on it would
    be measuring the fixture.
    """
    armed: list = []
    seen: list = []
    tts = GatedTts(lambda: bool(armed)
                   and any(r["spoken"] for r in replies(seen[0])))
    agents = Agents()
    hub, s, _harness, chats = harness_hub(tmp_path, tts=tts,
                                          harness_for=agents.mint)
    seen.append(s)
    first = chats.current()["id"]

    await type_at(hub, s, "the question that starts conversation A")
    second = chats.rotate()["id"]
    assert hub.open_conversation(first) is not None, first
    armed.append(True)

    turn = asyncio.create_task(type_at(hub, s, "the second question in A"))
    try:
        await until(tts.held.is_set, "a real segment reached the engine")
        # The owner clicks the other row while the agent is still answering.
        assert hub.open_conversation(second) is not None, second
        tts.gate.set()
        await asyncio.wait_for(turn, timeout=20)
    finally:
        tts.gate.set()
        turn.cancel()

    kept = chats.find(first)
    moved = chats.find(second)
    assert _roles(kept) == ["user", "assistant", "user", "assistant"], (
        f"the turn's answer left the conversation it was asked in: "
        f"{kept['messages']}")
    assert moved["messages"] == [], (
        f"a turn in {first} wrote into {second}, which the owner merely "
        f"clicked: {moved['messages']}")


@pytest.mark.asyncio
async def test_a_turn_RUNS_in_the_conversation_the_device_is_showing(tmp_path):
    """AC-246. The one resolver has to reach the BRAIN, not only the mute.

    `hub.chat_for` answers from `s.showing` and `brain_harness.turn()` answered
    from `ChatStore.current()`, and nothing handed one to the other. Measured:
    the desk shows A, the daemon's pointer is at B, and an utterance typed at
    the desk spawns B's agent in B's directory, is recorded in B, and is
    spoken aloud to the desk because the mute compares against A.

    The children are MINTED per spawn, because a fixture handing back one fake
    cannot tell "the turn ran in A" from "the turn ran in B on A's child".
    """
    agents = Agents()
    hub, desk, _harness, chats = harness_hub(tmp_path, harness_for=agents.mint)
    first = chats.current()["id"]

    await type_at(hub, desk, "the question that starts conversation A")
    second = chats.rotate()["id"]
    assert chats.current()["id"] == second
    await hub._on_text(desk, json.dumps({"type": "showing", "chat": first}))

    await type_at(hub, desk, "the desk is looking at A and talking to A")

    assert _roles(chats.find(first)) == ["user", "assistant",
                                         "user", "assistant"], (
        f"the turn was recorded somewhere else: {chats.find(first)}")
    assert chats.find(second)["messages"] == [], (
        f"the turn ran in the daemon's pointer rather than in the "
        f"conversation the device said it was showing: "
        f"{chats.find(second)['messages']}")
    assert len(agents.all) == 1, (
        f"a second coding agent was spawned for the conversation nobody was "
        f"looking at: {[spec.session_file for spec in agents.specs]}")
    assert first in agents.specs[-1].session_file, (
        f"the child is resuming the wrong conversation's session file: "
        f"{agents.specs[-1].session_file}")


# --------------------------------------------------------------------------- #
# AC-238 -- looking away mutes the REST of a reply and touches no child
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_looking_away_mid_reply_MUTES_the_rest_and_leaves_the_child_UP(
        tmp_path):
    """AC-238. The speech stops, the text keeps arriving, and the coding agent
    is untouched.

    **THE ASSERTION WITH TEETH IS THE CHILD.** Before this existed the only
    way to go quiet was `abandon()`, which reaches `HarnessConverse.abort` and
    disposes the child -- so a daemon that muted by abandoning would pass
    every silence check here and would have killed the agent the owner looked
    away from. `disposals` and `aborts` are therefore asserted, and the fake
    harness records both precisely so a daemon that called them cannot look
    like one that did not.
    """
    seen = []
    tts = GatedTts(lambda: bool(seen[0].spoken))
    # THREE sentences: the one heard, the one the gate holds (already in
    # synthesis when the view moves, so it is spoken to its end -- the
    # granularity `_speak_text`'s docstring states), and one still QUEUED,
    # which is what the look-away must mute.
    from daemon.adapters.harness_fake import FakeHarness, ScriptedTurn
    harness = FakeHarness(script=(
        ScriptedTurn("Let me look at the endpointing module."),
        ScriptedTurn("It holds three policies. One of them is not wired up.")))
    hub, s, _unused, chats = harness_hub(tmp_path, tts=tts,
                                         harness_for=lambda _chat=None: harness)
    seen.append(s)
    mine = chats.current()["id"]
    await hub._on_text(s, json.dumps({"type": "showing", "chat": mine}))

    turn = asyncio.create_task(type_at(hub, s, "look at the endpointing module"))
    try:
        await until(tts.held.is_set, "a real segment reached the engine")
        # Look somewhere else, mid-reply.
        await hub._on_text(s, json.dumps({"type": "showing",
                                          "chat": "chat-somewhere-else"}))
        tts.gate.set()
        await asyncio.wait_for(turn, timeout=20)
    finally:
        tts.gate.set()
        turn.cancel()

    said = replies(s)
    assert [r for r in said if r["spoken"]], (
        "nothing was spoken before the owner looked away, so this test would "
        f"pass against a daemon that never speaks at all: {said}")
    # WHERE the page draws the shown-not-heard line. Two ways reach it since
    # text stopped waiting for audio (docs/filing-and-pacing.md item 2): a
    # sentence whose TEXT went out after the view moved carries
    # `spoken=False`, and one whose text was already out, queued for audio,
    # is named by the turn's `unspoken` correction. The page marks from the
    # EARLIER of the two to the end.
    marked = [i for i, r in enumerate(said) if not r["spoken"]]
    marked += [m["from"] for m in (json.loads(x) for x in s.ws.sent
                                   if isinstance(x, str))
               if m["type"] == "unspoken"]
    assert marked, (
        f"the rest of the reply was not delivered shown-not-heard: {said}")
    # The LAST word is the one the criterion is about: whatever was still to
    # come after the view moved is written, not spoken -- and something
    # before it was heard.
    assert 0 < min(marked) <= len(said) - 1, (marked, said)

    # Nothing reached the engine after the view moved: going quiet means not
    # asking, so the synthesizer is not left working on a reply nobody hears.
    assert tts.calls == tts.calls_at_gate, (
        f"the engine was asked for {tts.calls - tts.calls_at_gate} more "
        f"segments after the owner looked away: {tts.spoken}")

    assert harness.disposals == 0, (
        f"the coding agent was DISPOSED when the owner looked away "
        f"({harness.disposals} times). That is the failure AC-242 and AC-243 "
        f"exist to prevent and it is not recoverable -- the child may have "
        f"been mid-edit.")
    assert harness.aborts == 0, (
        f"the coding agent was ABORTED when the owner looked away "
        f"({harness.aborts} times); a mute touches nothing")


@pytest.mark.asyncio
async def test_a_device_that_never_said_what_it_shows_HEARS_EVERYTHING(tmp_path):
    """The asymmetry, asserted on its own (AC-238, AC-246).

    Muting on an ABSENCE would turn a page-side bug, a client older than the
    frame, or a satellite that is only a speaker in a room, into a daemon that
    has silently stopped talking -- AC-7b in the one organ the owner cannot
    see. So a device that never declared hears the whole reply.
    """
    hub, s, _harness, _chats = harness_hub(tmp_path)

    await type_at(hub, s, "say something")

    said = replies(s)
    assert said, "nothing was said at all"
    assert all(r["spoken"] for r in said), (
        f"a device that never said what it is showing was muted: {said}")


def test_the_page_declares_the_view_from_ONE_writer():
    """The page's half, as a GREP (AC-238's mechanism).

    Nothing in this directory can run `app.js`, and
    `test_harness_question.py`'s watchdog gate set the precedent for reading
    `web/` this way. The rule is not "the page sends the frame" but "there is
    exactly one place that can forget to": three sites assigned `liveChatId`
    before this phase, and a fourth that assigned without declaring would
    leave the daemon speaking the wrong conversation with nothing on screen
    saying so (D-18).
    """
    source = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        encoding="utf-8")
    assert 'type: "showing"' in source, (
        "the page never tells the daemon what it is showing, so a device "
        "speaks whatever conversation replies next (AC-238)")
    assert "function setLiveChat(" in source, (
        "the one writer of `liveChatId` is gone; see this test's docstring")
    writes = [ln for ln in source.splitlines()
              if "liveChatId =" in ln and "let liveChatId" not in ln]
    assert len(writes) == 1, (
        f"`liveChatId` is assigned in {len(writes)} places and must be "
        f"assigned in one, inside `setLiveChat`: {writes}")


# --------------------------------------------------------------------------- #
# AC-247 -- an attachment belongs to the conversation it was attached in
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_file_attached_in_A_is_still_As_after_the_device_speaks_in_B(
        tmp_path, monkeypatch):
    """AC-247. The photo attached to the site chat does not arrive in the
    trading one.

    Both ends, because one of them alone proves nothing: B's turn must get
    NOTHING, and A's must still get it afterwards. A route that filed the file
    correctly and a turn that took everything pending are indistinguishable
    until the file comes back.
    """
    hub, s, harness, chats = harness_hub(tmp_path)
    a = chats.current()["id"]
    await hub._on_text(s, json.dumps({"type": "showing", "chat": a}))
    # A turn first, so `rotate()` does not REAP an empty conversation out from
    # under the test -- the store deliberately does not keep a record nothing
    # was ever said in.
    await type_at(hub, s, "start something here")
    b = chats.rotate()["id"]
    assert b != a

    async with _http(hub, monkeypatch) as client:
        status, doc = await _post(client, PNG, name="the kitchen.png")
    assert status == 200, doc
    assert [p.chat for p in s.attachments] == [a], s.attachments

    # A turn in the OTHER conversation. It must not carry the file.
    await hub._on_text(s, json.dumps({"type": "showing", "chat": b}))
    await type_at(hub, s, "what did you change in here")
    assert s.turn_chat == b, s.turn_chat
    assert [p.chat for p in s.attachments] == [a], (
        f"the attachment was taken by a turn in another conversation: "
        f"{s.attachments}")

    # Back to the conversation it was attached in.
    await hub._on_text(s, json.dumps({"type": "showing", "chat": a}))
    await type_at(hub, s, "what is in this photo")
    assert s.attachments == [], (
        f"the attachment never reached the conversation it was attached in: "
        f"{s.attachments}")
    assert any("the kitchen.png" in p for p in harness.prompts), (
        f"the file did not reach the coding agent's prompt: {harness.prompts}")
