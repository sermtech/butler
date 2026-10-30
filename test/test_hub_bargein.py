"""Barge-in reaches the engine, and it reaches every device. AC-5, D-4, D-6.

Driven at the Hub level with stub sessions rather than over a WebSocket,
because both behaviours here are about what the daemon does to OTHER
sessions and to the speech engine -- neither is visible from one satellite's
socket, which is exactly why both defects survived the protocol suite.
"""
import asyncio

import pytest

from conftest import StubTts, attach_session, make_hub
from daemon.hub import CAPTURING, IDLE, SPEAKING, THINKING


class RecordingTts(StubTts):
    """Captures the handle every synthesis was registered under."""

    def __init__(self):
        super().__init__()
        self.handles = []

    # `speed` is accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.handles.append(handle)
        yield b"\x00\x00" * 240



@pytest.mark.asyncio
async def test_the_daemon_registers_synthesis_under_the_turn_id(tmp_path):
    """The abort in abandon() can only match if _speak names the turn.

    This is the assertion that was missing when the abort was first added:
    the adapter honoured handles, the conformance suite proved it, and the
    daemon still called synthesize() anonymously -- so abort(turn) matched
    nothing and barge-in cancelled precisely zero synthesis while every test
    stayed green. Contract compliance has to be checked on the CALLER too.
    """
    hub = make_hub(tmp_path)
    hub.tts = RecordingTts()
    s = attach_session(hub, "desk")
    s.turn = 11

    await hub._speak(s, 11, "The tests are passing.", "reflex")

    assert hub.tts.handles, "nothing was synthesized"
    assert set(hub.tts.handles) == {11}, (
        f"synthesis registered under {hub.tts.handles}, not the turn id -- "
        f"abort(11) would match nothing")


class GatedTts(StubTts):
    """Streams a segment at a time and blocks until the test lets it through,
    so an interruption can be placed at an exact point in the reply."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.reached = asyncio.Event()

    # `speed` is accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.reached.set()
        await self.gate.wait()
        yield b"\x00\x00" * 240



@pytest.mark.asyncio
async def test_a_completed_reply_records_every_segment_as_spoken(tmp_path):
    """The baseline for the invariant below."""
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.turn = 21

    await hub._speak(s, 21, "Done. I pushed the branch.", "reflex")

    assert s.spoken == ["Done.", "I pushed the branch."]
    assert s.interrupted is False


@pytest.mark.asyncio
async def test_an_interrupted_reply_records_only_what_was_heard(tmp_path):
    """ports.TurnContext.history's invariant, enforced at the source.

    Three independent frameworks converged on this: LiveKit truncates to the
    synchronized transcript, Pipecat changed to the same in 1.5.0, and the
    Realtime API exposes conversation.item.truncate for it. Record the whole
    reply and the model believes it said sentences the user never heard, so
    the next turn re-answers, or refers back to advice nobody received.

    A segment cut off part-way through counts as NOT spoken. Half a sentence
    is not something the assistant said.
    """
    hub = make_hub(tmp_path)
    hub.tts = GatedTts()
    s = attach_session(hub, "desk")
    s.turn = 22

    speaking = asyncio.create_task(
        hub._speak(s, 22, "Done. I pushed the branch. Tests are green.",
                   "reflex"))
    await asyncio.wait_for(hub.tts.reached.wait(), timeout=2)

    # Barge-in lands while the FIRST segment is still being synthesized.
    s.turn = 23
    hub.tts.gate.set()
    await asyncio.wait_for(speaking, timeout=2)

    assert s.spoken == [], (
        f"recorded speech the user never heard: {s.spoken}")
    assert s.interrupted is True


class SilentAfterFirstTts(StubTts):
    """Produces audio for the first segment and nothing afterwards.

    Not hypothetical: an engine that has been aborted, or that fails on a
    segment, returns an empty stream. The daemon must not claim the user
    heard a sentence for which no sample was ever sent.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    # `speed` is accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.calls += 1
        if self.calls == 1:
            yield b"\x00\x00" * 240
        return



@pytest.mark.asyncio
async def test_a_segment_that_produced_no_audio_is_not_recorded_as_spoken(
        tmp_path):
    """Silence is not speech.

    Catches the lazier version of the invariant, where the daemon records a
    segment because it finished iterating rather than because anything was
    heard. An aborted or failed engine returns an empty stream, and counting
    that as spoken puts words in the assistant's mouth.
    """
    hub = make_hub(tmp_path)
    hub.tts = SilentAfterFirstTts()
    s = attach_session(hub, "desk")
    s.turn = 31

    await hub._speak(s, 31, "Done. I pushed the branch. Tests are green.",
                     "reflex")

    assert s.spoken == ["Done."], (
        f"recorded segments that produced no audio: {s.spoken}")


@pytest.mark.asyncio
async def test_abandon_tells_the_speech_engine_to_stop(tmp_path):
    """D-4: cancelling the consumer is not cancelling the producer.

    Kokoro has no cancel route today, so an abandoned reply keeps
    synthesizing and the NEXT reply queues ~6.7 s behind it. The daemon has
    to at least call abort; whether the engine can honour it is the
    adapter's problem, not a reason to never ask.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.turn = 7
    s.state = SPEAKING

    await s.abandon()

    assert hub.tts.aborted == [7], (
        f"abort was not sent to the engine for the abandoned turn: "
        f"{hub.tts.aborted}")


@pytest.mark.asyncio
async def test_an_idle_session_does_not_abort_anything(tmp_path):
    """Aborting when nothing is speaking would cancel the NEXT reply on
    engines that key abort by session rather than by turn."""
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    await s.abandon()
    assert hub.tts.aborted == []


@pytest.mark.asyncio
async def test_a_wake_on_one_device_silences_another(tmp_path):
    """AC-5 across devices.

    Say the wake word at the phone while the desk is mid-reply: the desk must
    stop. Barge-in used to abandon only the session that heard the wake, so
    the desk kept talking over the answer. One satellite cannot observe this,
    which is why the protocol suite passed with the bug in place.
    """
    hub = make_hub(tmp_path)
    desk = attach_session(hub, "desk")
    phone = attach_session(hub, "phone")

    desk.turn = 3
    desk.state = SPEAKING

    await hub._wake_candidate(phone, {"score": 0.9, "word": "hey_butler"})

    assert desk.state == IDLE, (
        f"the desk kept going while the phone took the turn: {desk.state}")
    assert desk.turn is None, "the desk still owns its abandoned turn"
    assert hub.tts.aborted == [3], (
        "the desk's speech was not aborted at the engine")
    assert phone.turn is not None and phone.turn != 3, (
        "the phone did not get a fresh server-minted turn")


@pytest.mark.asyncio
async def test_the_winning_device_keeps_its_own_turn(tmp_path):
    """The sweep must not abandon the session that just won."""
    hub = make_hub(tmp_path)
    desk = attach_session(hub, "desk")

    await hub._wake_candidate(desk, {"score": 0.9, "word": "hey_butler"})
    won = desk.turn
    assert won is not None

    await asyncio.sleep(0)
    assert desk.turn == won, "the winner abandoned its own new turn"


@pytest.mark.asyncio
async def test_a_background_conversations_turn_survives_a_wake_word_elsewhere(
        tmp_path):
    """Phase 8's scoping: `_barge_in` must not abandon a turn that is not
    AUDIBLE on its own session (`docs/parallel-agents.md` D4, AC-238).

    `desk` began a turn in "chat-a" and then declared it is showing
    "chat-b" -- exactly what `hub._speak` already mutes FOR mid-reply
    (`Say(spoken=False)`, AC-238) rather than abandoning -- so its
    front-door turn is a background conversation nobody is looking at. A
    wake word on `phone`, an unrelated device, must not touch it: the room
    it would be "interrupting" is silent for this turn already.
    """
    hub = make_hub(tmp_path)
    desk = attach_session(hub, "desk")
    phone = attach_session(hub, "phone")

    desk.showing = "chat-a"
    turn = hub._begin_turn(desk)
    desk.state = SPEAKING
    desk.showing = "chat-b"          # looked away mid-reply

    await hub._wake_candidate(phone, {"score": 0.9, "word": "hey_butler"})

    assert desk.state == SPEAKING, (
        f"a wake word elsewhere ended a turn nobody could hear: {desk.state}")
    assert desk.owns(turn), "the background turn was abandoned"
    assert turn not in hub.tts.aborted, (
        "the speech engine was told to abort a turn producing no audio")
    assert turn not in hub.brain.aborted, (
        "the coding agent behind a background turn was aborted by an "
        "unrelated wake word")


@pytest.mark.asyncio
async def test_an_audible_turn_still_ends_on_a_wake_word_elsewhere(tmp_path):
    """The positive control for the test above: a turn the session IS
    showing still barges in exactly as before -- widening what survives
    must not widen past turns that are actually audible."""
    hub = make_hub(tmp_path)
    desk = attach_session(hub, "desk")
    phone = attach_session(hub, "phone")

    desk.showing = "chat-a"
    turn = hub._begin_turn(desk)
    desk.state = SPEAKING

    await hub._wake_candidate(phone, {"score": 0.9, "word": "hey_butler"})

    assert desk.state == IDLE, (
        f"an audible turn survived a wake word elsewhere: {desk.state}")
    assert not desk.owns(turn), "the audible turn was not ended"
    assert turn in hub.tts.aborted, (
        "the speech engine was never told to abort the audible turn")


class RecordingBrain:
    """A Brain that records aborts, and logs them into a shared timeline.

    The timeline is what makes the ORDER assertable. `abort` reaching the
    brain eventually is not the contract -- reaching it before the reader
    task is cancelled is, because a cancelled reader stops us consuming
    intents while the brain keeps generating them. That is D-4 in a second
    place: cancelling the consumer is not cancelling the producer.
    """

    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.aborted = []

    async def turn(self, text, ctx):
        return
        yield                                        # pragma: no cover

    async def abort(self, turn_id):
        self.log.append(("brain_abort", turn_id))
        self.aborted.append(turn_id)

    async def warm(self):
        return None

    @property
    def info(self):
        return {"engine": "recording"}


async def _never_ends(log):
    """Stands in for a reply task waiting on a slow brain."""
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        log.append(("task_cancelled", None))
        raise


@pytest.mark.asyncio
async def test_abandon_tells_the_brain_to_stop_while_thinking(tmp_path):
    """AC-43. The brain is the one engine barge-in never reached.

    THINKING is precisely the window where the brain is doing the expensive
    thing -- an HTTP stream against a model, or a harness child mid-turn --
    and nothing was telling it to stop. The reply task got cancelled, which
    stops US reading, while the model kept generating and the next turn
    queued behind it. Exactly the defect D-4 records for TTS, one port over.
    """
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")
    s.turn = 5
    s.state = THINKING

    await s.abandon()

    assert hub.brain.aborted == [5], (
        f"the brain was never told to stop working on the abandoned turn: "
        f"{hub.brain.aborted}")


@pytest.mark.asyncio
async def test_abandon_tells_the_brain_to_stop_while_speaking(tmp_path):
    """SPEAKING counts too: `turn()` yields intents as they are decided, so
    a brain can still be generating while the first sentences are already
    being spoken. Stopping the audio without stopping the generation leaves
    the expensive half running."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")
    s.turn = 9
    s.state = SPEAKING

    await s.abandon()

    assert hub.brain.aborted == [9], (
        f"a brain still streaming intents was not stopped: "
        f"{hub.brain.aborted}")


@pytest.mark.asyncio
async def test_the_brain_is_stopped_before_the_reader_is_cancelled(tmp_path):
    """AC-43's ordering half, and the reason this is not just "call abort".

    Cancel the reader first and the brain is still generating into a socket
    nobody is draining. The engine has to be told before we stop listening
    to it, which is the same lesson as the TTS abort and is asserted here on
    the CALLER (D-18) rather than trusted to the adapter.
    """
    log = []
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain(log)
    s = attach_session(hub, "desk")
    s.turn = 3
    s.state = THINKING
    s.reply_task = asyncio.create_task(_never_ends(log))
    await asyncio.sleep(0)                  # let the task actually start

    await s.abandon()

    kinds = [entry[0] for entry in log]
    assert "brain_abort" in kinds, f"the brain was never aborted: {log}"
    assert "task_cancelled" in kinds, f"the reader was never cancelled: {log}"
    assert kinds.index("brain_abort") < kinds.index("task_cancelled"), (
        f"the reader was cancelled BEFORE the brain was told to stop, so "
        f"the brain kept generating into a socket nobody was reading: {log}")


@pytest.mark.asyncio
async def test_an_idle_session_does_not_abort_the_brain(tmp_path):
    """Same reasoning as the TTS version: aborting when nothing is running
    would cancel the NEXT turn on any brain that keys abort by session."""
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")

    await s.abandon()

    assert hub.brain.aborted == []


@pytest.mark.asyncio
async def test_a_thinking_session_does_not_abort_the_speech_engine(tmp_path):
    """The two aborts have DIFFERENT state guards, and that is deliberate.

    The brain is stopped while THINKING or SPEAKING; the speech engine only
    while SPEAKING, because during THINKING nothing has been synthesized yet.
    The reason it matters is the one the idle test already gives: an engine
    that keys abort by SESSION rather than by handle would cancel the next
    reply, which is the reply to the utterance that just interrupted.

    Found by mutation: widening the speech guard to match the brain's broke
    no test, so nothing was holding the two apart (D-13).
    """
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")
    s.turn = 15
    s.state = THINKING

    await s.abandon()

    assert hub.brain.aborted == [15], "the brain should still be stopped"
    assert hub.tts.aborted == [], (
        f"the speech engine was aborted for a turn that had not started "
        f"speaking: {hub.tts.aborted}")


@pytest.mark.asyncio
async def test_a_capturing_session_does_not_abort_the_brain(tmp_path):
    """CAPTURING is the state that actually exercises the guard.

    The idle test above cannot: with no turn claimed, `abandoned_turn is
    not None` short-circuits and the state is never even looked at, so that
    test passes just as happily with the state guard deleted. Here a turn
    IS claimed and the brain has not been asked anything yet -- the user is
    still speaking -- so only the state check stands between us and an abort
    for work that was never started.
    """
    hub = make_hub(tmp_path)
    hub.brain = RecordingBrain()
    s = attach_session(hub, "desk")
    s.turn = 12
    s.state = CAPTURING

    await s.abandon()

    assert hub.brain.aborted == [], (
        f"the brain was aborted for a turn it had never been given: "
        f"{hub.brain.aborted}")


@pytest.mark.asyncio
async def test_a_brain_that_raises_on_abort_does_not_break_barge_in(tmp_path):
    """D-1: barge-in must survive an engine that misbehaves.

    We are here to STOP the brain, not to hear about it. A raise from abort
    that propagated would abandon `abandon()` half-done -- the tasks never
    cancelled, the state never returned to IDLE, and the microphone left
    held, which is the exact latched-off failure D-1 forbids.
    """
    class RefusingBrain(RecordingBrain):
        async def abort(self, turn_id):
            raise RuntimeError("brain abort exploded")

    hub = make_hub(tmp_path)
    hub.brain = RefusingBrain()
    s = attach_session(hub, "desk")
    s.turn = 4
    s.state = THINKING

    await s.abandon()                       # must not raise

    assert s.state == IDLE, "barge-in left the session out of IDLE"
    assert s.turn is None, "barge-in left a turn claimed"
