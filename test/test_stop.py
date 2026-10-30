"""Stop, from the page, reaching the engine. AC-111, D-1, D-4, D-6.

The protocol gains ONE message and the daemon answers it with the path
barge-in already takes:

    satellite -> daemon   {"type": "stop", "turn": <int>}

The owner's ruling (docs/voice-control.md section 3, D1 = B) is that stop
stops the SPEECH and the TURN, because in `harness_only` mode the thing worth
stopping is usually the coding agent rather than the sentence.

A CONVERSATION is not a turn (test/AGENTS.md), and the risk this criterion
carries is the oldest one here: a stop that leaves the daemon in a state
where the next thing said is not heard (D-1, AC-2). So the four-turn cases
live here rather than in `test_conversation.py` -- a new home for the same
rule, because that file is not the only place a multi-turn claim may live.
Turn two is stopped and turns three and four have to be ordinary.

D-39 lives here too, because it is this criterion's own failure mode: the
turn the owner starts JUST AFTER a stop must still end. It is the one case
in the file that needs the clock tuned, and the docstring on it says why.
"""
import asyncio
import collections
import json
import time

import pytest

from conftest import (StubTts, attach_session, make_hub, silence_pcm,
                      tone_pcm)
from daemon import hub as hub_mod
from daemon.hub import IDLE, SPEAKING, THINKING

TURNS = 4


class HeldTts(StubTts):
    """Holds ONE nominated turn's first segment until the test lets go.

    So a stop can land while Butler is genuinely SPEAKING rather than after
    the reply already finished -- with the fast fakes every turn is over in
    microseconds, and a stop that arrives afterwards would exercise nothing
    but `owns()` (D-13: an instrument that cannot fail is not one).
    """

    def __init__(self, hold: int):
        super().__init__()
        self.hold = hold
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.said: list[str] = []

    # `speed` accepted and ignored: a double with no rate control honestly
    # honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        if handle == self.hold:
            self.reached.set()
            await self.release.wait()
        yield b"\x00\x00" * 240


async def drive_turn(hub, s, turn: int, text: str) -> None:
    """One whole turn through the reply path, playing the satellite's part.

    A local driver rather than a shared one: `test_conversation.py` has the
    same shape and is off limits to this file, and a helper that reaches into
    another test module couples two suites that fail for different reasons.
    It plays the half a real device plays -- reporting the speaker quiet --
    because a turn that only ever re-arms on the playback TIMEOUT looks
    identical here while adding two seconds to every turn.
    """
    s.turn = turn
    s.state = THINKING
    s.spoken = []
    s.audio_ms_sent = 0.0
    s.playback_done = asyncio.Event()
    s.speaking_started = False

    async def confirm():
        for _ in range(500):
            if s.audio_ms_sent > 0:
                await hub._playback_done(s, {"turn": turn})
                return
            await asyncio.sleep(0.01)

    helper = asyncio.create_task(confirm())
    try:
        await asyncio.wait_for(hub._reply(s, turn, text), timeout=20)
    finally:
        helper.cancel()


def replies(s) -> list[str]:
    return [json.loads(m)["text"] for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == "reply"]


def frames(s, kind: str) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == kind]


class SpeaksThenHolds(StubTts):
    """Sends real audio and THEN parks, ONCE per turn it was told to hold.

    `HeldTts` above parks BEFORE it yields anything, which is right for a
    stop landing mid-speech and wrong for D-39: with no audio sent,
    `_await_playback` returns immediately and the window that defect lives in
    never opens. What opens it is a turn that HAS sent audio and is then
    cancelled, because the playback gate is measured in the audio sent.
    """

    MS = 400          # 24 kHz mono int16: 48 bytes is a millisecond

    def __init__(self):
        super().__init__()
        self._parked: set = set()
        self.reached = collections.defaultdict(asyncio.Event)
        self.release = collections.defaultdict(asyncio.Event)

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        yield b"\x00\x00" * (24 * self.MS)
        # Once per TURN and not per sentence: the reply is several `Say`
        # intents and parking on each would need a release per sentence, which
        # is timing the test would be inventing rather than measuring. The
        # handle is whatever the DAEMON minted, because nothing here may pick
        # a turn id (D-6).
        if handle not in self._parked:
            self._parked.add(handle)
            self.reached[handle].set()
            await self.release[handle].wait()


@pytest.mark.asyncio
async def test_a_stopped_turn_cannot_rearm_the_turn_that_replaced_it(
        tmp_path, monkeypatch):
    """D-39: the turn started just after a stop must still END.

    MEASURED 2026-09-11, in a real browser against a real daemon: a turn
    started about three seconds after stopping the previous one never
    finished. The `reply` frames arrived, `speak_end` never did, and the page
    sat in its speaking state with the Stop button showing until it was
    reloaded. Six-second and eleven-second gaps were fine, which is what
    said this is a WINDOW rather than a broken reply path.

    The window is a race between two budgets and the test sets both, which is
    the only way to make it deterministic -- with the shipped numbers it is
    0.6 s wide and opens only when the stopped turn sent more than a second
    of audio. `stop` cancels `_reply`; a cancelled task's `finally` may still
    await, so that `finally` goes on to sit out `_await_playback`;
    `abandon()` gives up waiting after `ABANDON_TASK_S` and returns; the next
    turn begins; and THEN the stopped turn reaches `_rearm` and clears a turn
    it does not own. Everything after that is silent by design -- `_speak`
    bails on `owns()` and `send_for_turn` drops `speak_end` -- which is why
    nothing in the daemon log said a word about it.

    Asserted TWICE on purpose, and the second is the one that matters. The
    first is the steal itself, read off `s.turn` the moment the stopped turn
    lets go. The second is what the owner sees: a `speak_end` for the new
    turn. A fix that kept the turn id and still dropped the ending would
    satisfy the first assertion alone.
    """
    # Both halves of the race, shrunk. The DEFECT does not depend on either
    # number -- it depends on the gate outlasting the give-up -- so the test
    # keeps that order and nothing else about them.
    monkeypatch.setattr(hub_mod, "ABANDON_TASK_S", 0.05)
    monkeypatch.setattr(hub_mod, "PLAYBACK_GRACE_S", 0.4)
    gate = SpeaksThenHolds.MS / 1000.0 + 0.4        # what the zombie waits out

    hub = make_hub(tmp_path)
    hub.tts = SpeaksThenHolds()
    s = attach_session(hub)

    s.turn = 901
    s.state = THINKING
    s.reply_task = asyncio.create_task(hub._reply(s, 901, "the stopped one"))
    await asyncio.wait_for(hub.tts.reached[901].wait(), timeout=5)
    assert s.audio_ms_sent > 0, (
        "the stopped turn sent no audio, so its playback gate returns at once "
        "and this test cannot open the window it is about (D-13)")

    await asyncio.wait_for(
        hub._on_text(s, json.dumps({"type": "stop", "turn": 901})), timeout=5)
    assert s.turn is None, "the stop did not end the turn it named"

    # The next question, through the door the page uses. `_begin_turn` mints
    # the id, so nothing here names a turn the daemon did not.
    await asyncio.wait_for(
        hub._on_text(s, json.dumps({"type": "typed", "text": "and the next"})),
        timeout=5)
    turn = s.turn
    assert turn is not None and turn != 901, (
        f"the turn after the stop never started: {turn!r}")
    await asyncio.wait_for(hub.tts.reached[turn].wait(), timeout=5)

    # Sit out the stopped turn's playback gate, which is when it lets go.
    await asyncio.sleep(gate + 0.3)
    assert s.turn == turn, (
        f"the STOPPED turn re-armed the session while turn {turn} was "
        f"speaking: s.turn={s.turn!r}. Everything turn {turn} does after this "
        f"is dropped by owns(), so it never ends (D-39, D-6)")

    hub.tts.release[turn].set()
    for _ in range(400):
        if [f for f in frames(s, "speak_end") if f.get("turn") == turn]:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail(
            f"turn {turn} never sent speak_end, so the page stays in its "
            f"speaking state with the Stop button showing until it is "
            f"reloaded; frames were "
            f"{[json.loads(m)['type'] for m in s.ws.sent if isinstance(m, str)]}")

    hub.tts.release[901].set()


@pytest.mark.asyncio
async def test_stop_reaches_both_engines_and_ends_the_turn(tmp_path):
    """AC-111 in one place: the speech engine, the brain, and the re-arm.

    Asserted on the CALLER (D-18). The engines' own aborts have been proven
    by conformance for months; what was missing until this criterion is
    anything that ASKS them from a page-initiated stop. `tts.abort(turn)` is
    what reaches the kokoro sidecar's own `/abort` -- an abort that stops the
    daemon's wait and not the engine reads as working and is D-4 exactly.

    Driven through `_on_text` rather than by calling the handler, because the
    message shape is half the contract: a daemon that implemented this under
    another name would pass a handler-level test and answer the page with
    "unsupported message type".
    """
    hub = make_hub(tmp_path)
    hub.tts = HeldTts(hold=601)
    s = attach_session(hub)

    s.turn = 601
    s.state = THINKING
    s.reply_task = asyncio.create_task(hub._reply(s, 601, "a long question"))
    await asyncio.wait_for(hub.tts.reached.wait(), timeout=5)
    assert s.state == SPEAKING, (
        f"the reply never started speaking, so this stop lands somewhere the "
        f"criterion is not about: {s.state!r}")

    await asyncio.wait_for(
        hub._on_text(s, json.dumps({"type": "stop", "turn": 601})), timeout=10)

    assert hub.tts.aborted == [601], (
        f"the stop did not reach the speech ENGINE for the turn it named, so "
        f"the sidecar keeps synthesizing a reply nobody will hear (D-4): "
        f"{hub.tts.aborted}")
    assert hub.brain.aborted == [601], (
        f"the stop silenced Butler and left the brain working; the owner's "
        f"ruling is that stop stops the TURN (D1 = B): {hub.brain.aborted}")
    assert s.state == IDLE and s.turn is None, (
        f"the stopped turn did not re-arm: state={s.state!r} turn={s.turn!r} "
        f"(D-1, AC-2)")
    assert not [m for m in s.ws.sent
                if isinstance(m, str) and json.loads(m)["type"] == "error"], (
        "the stop was answered with an error frame")
    hub.tts.release.set()


@pytest.mark.asyncio
async def test_a_stop_naming_another_turn_changes_nothing(tmp_path):
    """D-6: a message from the page naming a turn the daemon is not on is
    IGNORED, never acted on.

    The page mints nothing -- the daemon does -- so a stale `stop` is what
    arrives when the owner presses stop at the same moment a wake word starts
    a new turn. Acting on it would take the microphone away from the turn
    they are now in, which is the shape `_playback_done` is already guarded
    against.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 700
    s.state = SPEAKING

    await hub._on_text(s, json.dumps({"type": "stop", "turn": 699}))

    assert hub.tts.aborted == [], (
        f"a stop for turn 699 aborted the engine while the daemon was on 700: "
        f"{hub.tts.aborted}")
    assert hub.brain.aborted == [], (
        f"a stale stop reached the brain: {hub.brain.aborted}")
    assert s.turn == 700 and s.state == SPEAKING, (
        f"a stale stop ended the turn the daemon was actually on: "
        f"turn={s.turn!r} state={s.state!r}")


@pytest.mark.asyncio
async def test_a_stop_on_turn_two_leaves_the_conversation_working(tmp_path):
    """The whole criterion, four turns deep (D-1, AC-2, test/AGENTS.md).

    One turn proves a terminal path works ONCE, and this project's oldest
    defect is a microphone that comes back once and then latches on the turn
    after. Turn two is stopped mid-speech; turns three and four have to be
    ordinary, answered, and their own.
    """
    hub = make_hub(tmp_path)
    hub.tts = HeldTts(hold=802)
    s = attach_session(hub)

    await drive_turn(hub, s, 801, "question one")
    assert s.state == IDLE and s.turn is None, "turn one did not re-arm"

    s.turn = 802
    s.state = THINKING
    s.spoken = []
    s.speaking_started = False
    s.reply_task = asyncio.create_task(hub._reply(s, 802, "question two"))
    await asyncio.wait_for(hub.tts.reached.wait(), timeout=5)
    await asyncio.wait_for(
        hub._on_text(s, json.dumps({"type": "stop", "turn": 802})), timeout=10)
    hub.tts.release.set()

    assert hub.tts.aborted == [802], (
        f"turn two's stop did not reach the engine: {hub.tts.aborted}")
    assert s.state == IDLE and s.turn is None, (
        "the stopped turn did not re-arm, so nothing said after it is heard")

    for turn in (803, 804):
        await drive_turn(hub, s, turn, f"question {turn}")
        assert s.state == IDLE and s.turn is None, (
            f"turn {turn} did not re-arm after a stopped turn (D-1, AC-2)")

    heard = [t for _role, t in hub.history if _role == "user"]
    assert heard[-2:] == ["question 803", "question 804"], (
        f"the turns after the stop were not heard at all: {heard}")
    # ALL FOUR turns show the second sentence's TEXT now, including the
    # stopped one (docs/filing-and-pacing.md 2.5, D7=A/D8=A, AC-286): text
    # goes out as soon as it exists, with no gate on any sentence's audio,
    # so by the time `HeldTts.reached` fires for turn 802's FIRST segment,
    # its SECOND sentence's text is already on the wire. That is no longer an
    # accepted gap: the stopped turn's `unspoken` frame marks it shown and
    # never heard (`test_speak_pacing.py` pins that). `hub.history` above
    # proves the page is where it stops: turn 802 contributed no assistant
    # line to it,
    # because `_remember` still reads only `s.spoken`, which the stopped
    # turn's second sentence never reached (its audio leg was never even
    # attempted).
    assert replies(s).count("Here is the longer answer to your question.") == 4, (
        f"{TURNS} turns, one of them stopped: all four should show the "
        f"second sentence's TEXT, the stopped one included: {replies(s)}")
    assert hub.tts.aborted == [802], (
        f"a later turn was aborted by the stop that ended turn two: "
        f"{hub.tts.aborted}")


# --------------------------------------------------------------------------- #
# over the wire, where the message shape is the contract
# --------------------------------------------------------------------------- #
async def one_turn(sat) -> int:
    """Wake, speak, be answered, report the speaker quiet. As a device does."""
    await sat.send(type="wake_candidate", word="hey_butler", score=0.92,
                   ts_ms=int(time.time() * 1000))
    start = await sat.next("capture_start", timeout=10.0)
    turn = start["turn"]
    await sat.send_pcm(tone_pcm(600))
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint")
    final = await sat.next("final", timeout=15.0)
    assert final.get("turn") == turn, f"final belongs to {turn}: {final}"
    end = await sat.next("speak_end", timeout=20.0)
    assert end.get("turn") == turn
    await sat.send(type="playback_done", turn=turn)
    return turn


@pytest.mark.asyncio
async def test_the_daemon_accepts_stop_and_keeps_answering(satellite):
    """Four wakes on one socket, the second one stopped, over a real socket.

    The wire is where the SHAPE is checked: protocol v1 rejects any message
    type it does not know with an `error` frame and carries on capturing, so
    a daemon that never implemented this answers the stop with a complaint
    and then finalizes the turn anyway. Both are asserted by their absence --
    no error, and no `final` for a turn that was stopped.
    """
    turns = [await one_turn(satellite)]

    await satellite.send(type="wake_candidate", word="hey_butler", score=0.92,
                         ts_ms=int(time.time() * 1000))
    stopped = (await satellite.next("capture_start", timeout=10.0))["turn"]
    turns.append(stopped)
    await satellite.send_pcm(tone_pcm(300))
    await satellite.send(type="stop", turn=stopped)
    # After the stop there is no pipeline left to finalize, so the hint is a
    # no-op -- and it is exactly what WOULD produce a final if the stop had
    # been rejected as an unknown message type.
    await satellite.send(type="capture_end_hint")
    with pytest.raises(AssertionError):
        await satellite.next("final", timeout=3.0)
    assert not [m for m in satellite.seen if m.get("type") == "error"], (
        f"the daemon refused the stop message: "
        f"{[m for m in satellite.seen if m.get('type') == 'error']}")

    turns.append(await one_turn(satellite))
    turns.append(await one_turn(satellite))
    assert len(set(turns)) == TURNS, (
        f"the daemon reused a turn id across a stop: {turns}")
    assert turns == sorted(turns), f"turn ids went backwards: {turns}"


# --------------------------------------------------------------------------- #
# STOP NAMES THE CONVERSATION ON SCREEN (docs/parallel-agents.md D5, the
# 2026-09-26 e2e audit S18). The frame is now {"type": "stop", "turn"?, "chat"}.
# --------------------------------------------------------------------------- #
def _two_turns(hub):
    """One session holding a BACKGROUND turn (chat-bg, 100) and a live FRONT
    DOOR turn (chat-fg, 200) -- what a device is left with after it started an
    agent in one conversation and then sent a message in another."""
    from test_hub_bargein import RecordingBrain
    hub.brain = RecordingBrain()
    s = attach_session(hub)
    s.turn_by_chat["chat-bg"] = 100
    s.chat_by_turn[100] = "chat-bg"
    s.turn = 200
    s.turn_by_chat["chat-fg"] = 200
    s.chat_by_turn[200] = "chat-fg"
    s.state = SPEAKING
    return s


@pytest.mark.asyncio
async def test_a_stop_for_a_background_turn_stops_that_turn_and_not_the_front_door(
        tmp_path):
    """The audit's S18, daemon half: `_stop` accepted any turn the session
    OWNS and then called `abandon()` with no argument -- the FRONT DOOR -- so
    a stop naming a background conversation's turn killed whatever turn this
    device happened to have started last (D-18: assert on what the handler
    passes on, not only on the predicate it checked)."""
    hub = make_hub(tmp_path)
    s = _two_turns(hub)

    await hub._on_text(s, json.dumps({"type": "stop", "turn": 100,
                                      "chat": "chat-bg"}))

    assert hub.brain.aborted == [100], (
        f"the stop named turn 100 in chat-bg and the brain was told to abort "
        f"{hub.brain.aborted}")
    assert s.turn == 200 and s.state == SPEAKING, (
        f"a stop for a background conversation ended the front door: "
        f"turn={s.turn!r} state={s.state!r}")
    assert "chat-bg" not in s.turn_by_chat


@pytest.mark.asyncio
async def test_a_stop_whose_chat_is_not_its_turns_chat_changes_nothing(
        tmp_path):
    """The page names the conversation it is SHOWING. A turn this session
    owns in a DIFFERENT conversation is not the one on screen, so the stop is
    stale and is ignored (D-6) rather than acted on."""
    hub = make_hub(tmp_path)
    s = _two_turns(hub)

    await hub._on_text(s, json.dumps({"type": "stop", "turn": 200,
                                      "chat": "chat-bg"}))

    assert hub.brain.aborted == [], (
        f"a stop for chat-bg aborted chat-fg's turn: {hub.brain.aborted}")
    assert s.turn == 200 and s.state == SPEAKING


@pytest.mark.asyncio
async def test_a_stop_naming_only_a_conversation_reaches_a_turn_owned_by_another_socket(
        tmp_path):
    """A RELOADED page knows its conversation is running (the status route
    says so) but not the turn id, and the turn belongs to the socket that
    started it. Stop on screen must still stop it -- through
    `stop_conversation`, the one "wherever it is owned" path."""
    from test_hub_bargein import RecordingBrain
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    owner = attach_session(hub, "desk")
    owner.state = SPEAKING
    turn, chat = 300, "chat-running"
    owner.turn = turn
    owner.turn_by_chat[chat] = turn
    owner.chat_by_turn[turn] = chat
    reloaded = attach_session(hub, "desk-reloaded")

    await hub._on_text(reloaded, json.dumps({"type": "stop", "chat": chat}))

    assert hub.brain.aborted == [turn], (
        f"a chat-only stop from another socket did not reach the running "
        f"turn: {hub.brain.aborted}")
    assert not owner.owns(turn)


@pytest.mark.asyncio
async def test_a_chat_only_stop_for_a_conversation_with_no_turn_does_nothing(
        tmp_path):
    """Stop is about a TURN. A conversation with nothing in flight -- a press
    that raced the turn's own end -- must not dispose a held child or raise
    `stop_conversation`'s "nothing to stop" into the socket handler."""
    from test_hub_bargein import RecordingBrain
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub)

    await hub._on_text(s, json.dumps({"type": "stop", "chat": "chat-idle"}))

    assert hub.brain.aborted == []
    assert not [f for f in frames(s, "error")], frames(s, "error")
