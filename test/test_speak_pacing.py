"""AC-284..AC-286: the text lane must not wait for the prior sentence's audio
to finish synthesizing and sending (docs/filing-and-pacing.md item 2).

The owner's complaint, verbatim: "chat streaming/ appearing on chat lane is
waiting/ pausing to accompany the tts... chat should appear when is
available. tts answer can keep normal pacing as it is now." Traced in
`docs/filing-and-pacing.md` section 2.1: `_speak` sent one Say's `reply`
frame and THEN, in the same call, synthesized and streamed its audio -- so
`_reply`'s `async for intent in stream:` could not call `anext()` on the
brain generator, which is what lets it produce sentence N+1 at all, until
sentence N's whole audio leg had returned.
"""
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubTts, StubResolver, attach_session, make_hub  # noqa: E402
from daemon import hub as hub_mod, ports                              # noqa: E402
from daemon.settings import Settings                                  # noqa: E402


class GatedTts(StubTts):
    """A TTS double whose FIRST synthesize call genuinely blocks on an
    asyncio.Event, so the ordering claim below is proven by a real await
    rather than a sleep-and-hope race (test/AGENTS.md: an ordering test must
    be driven by a TTS that genuinely awaits, not merely a slow one).

    Every call after the gate opens returns at once -- `Event.wait()` never
    blocks again once `set()` has run -- which is fine here: only the FIRST
    sentence's audio needs to be slow to tell the two designs apart.
    """

    def __init__(self):
        super().__init__()
        self.said: list[str] = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        self.started.set()
        await self.gate.wait()
        yield b"\x00\x00" * 240        # ~5ms of int16 mono @ 24kHz silence


def replies(s) -> list[str]:
    return [json.loads(m)["text"] for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == "reply"]


async def start_turn(hub, s, turn: int, text: str) -> asyncio.Task:
    """`test_conversation.hub_turn`'s own setup, launched as a background
    TASK instead of awaited in place -- this test has to observe the wire
    WHILE the turn is still mid-flight, which an awaited helper cannot do.
    """
    s.turn = turn
    s.state = hub_mod.THINKING
    chat = hub.chat_for(s)
    s.turn_chat = chat
    s.turn_by_chat[chat] = turn
    s.chat_by_turn[turn] = chat
    hub._turn_started[chat] = time.time()
    s.spoken = []
    s.audio_ms_sent = 0.0
    s.playback_done = asyncio.Event()
    s.speaking_started = False

    async def confirm():
        for _ in range(2000):
            if s.audio_ms_sent > 0:
                await hub._playback_done(s, {"turn": turn})
                return
            await asyncio.sleep(0.01)

    asyncio.create_task(confirm())
    return asyncio.create_task(hub._reply(s, turn, text))


@pytest.mark.asyncio
async def test_sentence_two_text_arrives_before_sentence_one_audio_finishes(
        fake_openai, tmp_path):
    """AC-284, RED before the fix.

    Before the split, `_speak(say_1)` synthesized and SENT say_1's whole
    audio before returning, and `_reply`'s loop cannot ask the brain for
    say_2 -- and therefore cannot send say_2's text -- until that call
    returns. With say_1's audio genuinely blocked on `GatedTts.gate`, say_2's
    `reply` frame cannot have reached the wire yet under the old design,
    however fast the model itself produced the second sentence.
    """
    hub = make_hub(tmp_path)
    hub.tts = GatedTts()
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(fake_openai.base_url))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    fake_openai.answer("First sentence here. Second sentence here.")

    task = await start_turn(hub, s, 900, "two sentences please")
    try:
        await asyncio.wait_for(hub.tts.started.wait(), timeout=5.0)
        # Sentence one's audio has STARTED and is blocked on the gate. A few
        # passes of the event loop prove sentence two's text is not merely
        # late -- under the old design it is unreachable here, not racing to
        # arrive.
        for _ in range(5):
            await asyncio.sleep(0.02)
        got = replies(s)
        assert any("First sentence" in r for r in got), (
            f"sentence one's own text should already be out: {got}")
        assert any("Second sentence" in r for r in got), (
            f"sentence two's text frame had not reached the wire while "
            f"sentence one's audio was still blocked -- text is waiting on "
            f"audio, the owner's exact complaint (AC-284): {got}")
    finally:
        hub.tts.gate.set()
        await asyncio.wait_for(task, timeout=15.0)

    # AC-285: audio pacing/ordering is unchanged -- both segments were
    # synthesized in order, and both landed in s.spoken (what hub.history and
    # chats.py both read) in that same order.
    assert hub.tts.said == ["First sentence here.", "Second sentence here."], (
        f"synthesis order changed: {hub.tts.said}")
    assert s.spoken == ["First sentence here.", "Second sentence here."], (
        f"s.spoken must fill in Say order, unchanged (D-20): {s.spoken}")


FOUR = ("First sentence here. Second sentence here. "
        "Third sentence here. Fourth sentence here.")


@pytest.mark.asyncio
async def test_every_sentence_text_arrives_while_sentence_one_audio_is_blocked(
        fake_openai, tmp_path):
    """AC-284 at its full depth, RED against phase 1 (`359cb96`).

    Phase 1 was a lookahead of exactly ONE: sentence N+1's text went out,
    then `_reply` awaited sentence N's whole audio leg before asking the brain
    for sentence N+2. So a two-sentence reply looked fixed and a four-sentence
    one still paced its third and fourth sentences behind the voice. The
    owner's ruling of 2026-09-26 (delegated): text is shown as soon as the
    brain produces it, NEVER paced by synthesis.
    """
    hub = make_hub(tmp_path)
    hub.tts = GatedTts()
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(fake_openai.base_url))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    fake_openai.answer(FOUR)

    task = await start_turn(hub, s, 910, "four sentences please")
    try:
        await asyncio.wait_for(hub.tts.started.wait(), timeout=5.0)
        for _ in range(10):
            await asyncio.sleep(0.02)
        got = replies(s)
        assert len(got) == 4 and "Fourth sentence" in got[-1], (
            f"every sentence's text must be on the wire while sentence one's "
            f"audio is still blocked -- text is waiting on audio (AC-284): "
            f"{got}")
        # Audio did not run ahead with it: still only sentence one asked for.
        assert hub.tts.said == ["First sentence here."], hub.tts.said
    finally:
        hub.tts.gate.set()
        await asyncio.wait_for(task, timeout=15.0)

    # AC-285: audio in order, one at a time, and history from audio only.
    assert hub.tts.said == ["First sentence here.", "Second sentence here.",
                            "Third sentence here.", "Fourth sentence here."]
    assert s.spoken == hub.tts.said


class StepTts(StubTts):
    """The first `free` syntheses answer at once; every later one blocks
    until it is CANCELLED. A barge-in can then land at a known point: some
    sentences genuinely synthesized and sent, one mid-synthesis, the rest
    never asked for."""

    def __init__(self, free: int = 1):
        super().__init__()
        self.said: list[str] = []
        self.free = free
        self.blocked = asyncio.Event()
        self._never = asyncio.Event()      # set only by abort()

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        if len(self.said) > self.free:
            self.blocked.set()
            await self._never.wait()
            return                         # aborted: the stream just ends
        yield b"\x00\x00" * 240

    async def abort(self, handle) -> None:
        # Like the kokoro sidecar's /abort: the synthesis in flight ENDS. So
        # a daemon that only aborted the engine and did not also stop its
        # own queue would go straight on to the next sentence -- which is
        # what the stop test below has to be able to see.
        await super().abort(handle)
        self._never.set()


@contextlib.asynccontextmanager
async def reaped(task: asyncio.Task):
    """Whatever the assertions say, the turn does not outlive the test: a
    red run left `_reply` parked on a blocked synthesis and the fixture's
    server teardown hung behind it."""
    try:
        yield
    finally:
        if not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5.0)


def frames(s, kind: str) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == kind]


async def until(predicate, what: str, budget: float = 5.0) -> None:
    """Poll a condition on a deadline: a turn that never gets there is the
    failure, and a test that hangs on it reports nothing (test/AGENTS.md)."""
    deadline = time.monotonic() + budget
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_stop_marks_the_shown_tail_unspoken_and_history_holds_only_audio(
        fake_openai, tmp_path):
    """AC-286 as ruled (2026-09-26, delegated): a stop while sentence two is
    mid-synthesis leaves all four texts on screen, tells the page from which
    reply frame on nothing was heard, ends synthesis, and remembers only the
    sentence whose audio was actually sent (AC-64, D-20)."""
    hub = make_hub(tmp_path)
    hub.tts = StepTts(free=1)
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(fake_openai.base_url))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    fake_openai.answer(FOUR)

    task = await start_turn(hub, s, 920, "four sentences please")
    s.reply_task = task                    # what `_on_final` would have set
    async with reaped(task):
        await until(lambda: hub.tts.blocked.is_set() and len(replies(s)) == 4,
                    "sentence two mid-synthesis with every text shown")
        await s.abandon()                  # the stop / barge-in path
        await asyncio.wait([task], timeout=10.0)   # it ends CANCELLED
        assert task.done()

    assert len(replies(s)) == 4, "shown text is never retracted"
    marks = frames(s, "unspoken")
    assert [(m["turn"], m["from"]) for m in marks] == [(920, 1)], (
        f"the page must be told reply frame 1 onward was never heard: "
        f"{marks}")
    assert s.spoken == ["First sentence here."], s.spoken
    assert hub.history[-1] == ("assistant", "First sentence here."), (
        f"the model may only be told what the room heard (D-20): "
        f"{hub.history}")
    # Stop ENDS synthesis: the engine was told, and sentences three and four
    # were never asked for at all.
    assert 920 in hub.tts.aborted, hub.tts.aborted
    assert hub.tts.said == ["First sentence here.", "Second sentence here."]


@pytest.mark.asyncio
async def test_a_turn_spoken_to_its_end_sends_no_unspoken_mark(
        fake_openai, tmp_path):
    """The mark is a correction, never noise: a reply heard in full says
    nothing extra (the negative control for the test above)."""
    hub = make_hub(tmp_path)
    hub.tts = StepTts(free=99)
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(fake_openai.base_url))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    fake_openai.answer(FOUR)
    task = await start_turn(hub, s, 930, "four sentences please")
    async with reaped(task):
        await asyncio.wait_for(asyncio.shield(task), timeout=15.0)
    assert frames(s, "unspoken") == []
    assert len(s.spoken) == 4


@pytest.mark.asyncio
async def test_speech_off_records_every_shown_sentence_even_when_stopped(
        fake_openai, tmp_path):
    """Speech OFF: everything shown IS the answer (AC-110's rule in
    `_speak_audio`), so a stop marks nothing and forgets nothing that was
    shown -- the muted legs are drained, not cancelled."""
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("speak", False)
    hub = make_hub(tmp_path, settings=prefs)
    hub.tts = StepTts(free=0)             # would block if it were ever asked
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(fake_openai.base_url))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    fake_openai.answer(FOUR)
    task = await start_turn(hub, s, 940, "four sentences please")
    s.reply_task = task
    async with reaped(task):
        await until(lambda: len(replies(s)) == 4, "every text shown")
        await s.abandon()
        await asyncio.wait([task], timeout=10.0)   # it ends CANCELLED
        assert task.done()
    assert hub.tts.said == [], "a muted reply never asks the engine"
    assert frames(s, "unspoken") == []
    assert " ".join(s.spoken) == FOUR


@pytest.mark.asyncio
async def test_harness_record_keeps_the_shown_answer_and_marks_where_speech_stopped(
        fake_openai, tmp_path):
    """The record half of the ruling, end to end in harness mode: the agent
    finishes its answer while the voice is still on sentence two (so the
    whole answer is on record), a stop lands, and the record keeps ALL of
    the shown text with `unspokenFrom` at the first unheard sentence -- the
    same line the page was told to draw, so a reload shows the same thing."""
    from test_chats import _hub as harness_hub
    hub, _child, store = harness_hub(tmp_path, fake_openai)
    # Two free: the harness says its STATUS line ("Working on it.", keep
    # False -- heard, never shown) before the answer, and that is a synthesis.
    hub.tts = StepTts(free=2)
    s = attach_session(hub)
    s.turn = 950
    chat = store.current()["id"]         # what `_begin_turn` registers
    s.chat_by_turn[950] = chat
    s.turn_by_chat[chat] = 950
    await asyncio.wait_for(
        hub._on_final(s, 950, ports.Final(text="what does it do"), "silence"),
        10.0)
    task = s.reply_task
    async with reaped(task):
        await until(lambda: hub.tts.blocked.is_set() and any(
            m["role"] == "assistant" for m in store.messages()),
            "the agent's whole answer recorded while sentence two is heard")
        await s.abandon()
        await asyncio.wait([task], timeout=10.0)

    last = store.messages()[-1]
    assert last["role"] == "assistant"
    cut = last.get("unspokenFrom")
    assert isinstance(cut, int), f"no boundary on record: {last}"
    assert last["content"][:cut].strip() == "Let me look at the endpointing module."
    assert last["content"][cut:].strip() == hub.tts.said[2].strip(), (
        f"the unheard tail is sentence two: {last['content'][cut:]!r}")
    assert [m["from"] for m in frames(s, "unspoken")] == [1]
    assert hub.history[-1] == ("assistant", "Let me look at the endpointing module.")


def test_the_store_marks_only_this_turns_answer_and_only_a_matching_tail(tmp_path):
    """`ChatStore.mark_unspoken` refuses, loudly, rather than guess: an
    answer from BEFORE this turn (the agent was stopped before it recorded
    one) and a tail the answer does not end with are both left unmarked."""
    from daemon import chats
    store = chats.ChatStore(tmp_path / "chats")
    cid = store.current()["id"]
    store.append(cid, "user", "q")
    store.append(cid, "assistant", "One. Two.")
    stamp = store.messages()[-1]["timestamp"]

    store.mark_unspoken(cid, "Two.", since_ms=stamp + 1)     # an older answer
    assert "unspokenFrom" not in store.messages()[-1]
    store.mark_unspoken(cid, "Three.", since_ms=stamp)       # not its tail
    assert "unspokenFrom" not in store.messages()[-1]
    store.mark_unspoken(cid, "Two.", since_ms=stamp)
    assert store.messages()[-1]["unspokenFrom"] == len("One. ")
    # And it is ON DISK, which is what a reload reads.
    again = chats.ChatStore(tmp_path / "chats")
    assert again.find(cid)["messages"][-1]["unspokenFrom"] == len("One. ")


class HangingBrain:
    """Yields its sentences, then either keeps generating forever (until
    aborted) or fails -- the two exits where `_reply` is NOT waiting on the
    voice when the turn ends, so nothing but the voice's own stop can end
    the audio leg."""

    def __init__(self, sentences, fail: bool = False, deaf: bool = False,
                 later=()):
        # `deaf`: abort() does not end the stream. A real brain's abort
        # awaits I/O (a child's disposal, an HTTP cancel), so the turn is
        # NOT guaranteed to unwind before the TTS abort wakes the voice --
        # a double whose abort ends the stream at once hides that window.
        self.sentences, self.fail, self.deaf = sentences, fail, deaf
        self.later = later                 # yielded once `go` is set
        self._aborted = asyncio.Event()
        self.go = asyncio.Event()          # the test says when to fail

    async def turn(self, text, ctx):
        for sentence in self.sentences:
            yield ports.Say(sentence, tier="conversation")
        if self.later:
            await self.go.wait()
            for sentence in self.later:
                yield ports.Say(sentence, tier="conversation")
        if self.fail:
            await self.go.wait()
            raise RuntimeError("the model went away mid-answer")
        await self._aborted.wait()

    async def abort(self, turn_id) -> None:
        if not self.deaf:
            self._aborted.set()

    async def warm(self) -> None:
        return None

    @property
    def info(self) -> dict:
        return {"brain": "hanging"}


THREE = ["First sentence here.", "Second sentence here.",
         "Third sentence here."]


@pytest.mark.asyncio
async def test_a_stop_while_the_model_is_still_generating_starts_no_more_synthesis(
        tmp_path):
    """Stop ends synthesis even when the brain is mid-answer, which is when
    `_reply` is waiting on the MODEL and not on the voice. The engine abort
    ends sentence two's synthesis quietly; a queue that were not halted
    first would start sentence three in that gap (D-4)."""
    hub = make_hub(tmp_path)
    hub.tts = StepTts(free=1)
    hub.brain = HangingBrain(THREE, deaf=True)
    s = attach_session(hub)
    task = await start_turn(hub, s, 960, "three sentences please")
    s.reply_task = task
    async with reaped(task):
        await until(lambda: hub.tts.blocked.is_set() and len(replies(s)) == 3,
                    "three texts shown, sentence two mid-synthesis")
        await s.abandon()
        await asyncio.wait([task], timeout=10.0)
    assert hub.tts.said == THREE[:2], (
        f"a stopped turn asked the engine for more: {hub.tts.said}")
    assert [m["from"] for m in frames(s, "unspoken")] == [1]
    assert s.spoken == THREE[:1]


@pytest.mark.asyncio
async def test_a_brain_failure_mid_answer_ends_the_audio_leg(tmp_path):
    """A failed turn ends its audio too: nothing is left synthesizing behind
    the error, and what was shown and not heard is marked."""
    hub = make_hub(tmp_path)
    hub.tts = StepTts(free=1)
    hub.brain = HangingBrain(THREE, fail=True)
    s = attach_session(hub)
    task = await start_turn(hub, s, 970, "three sentences please")
    async with reaped(task):
        await until(lambda: hub.tts.blocked.is_set(), "sentence two blocked")
        hub.brain.go.set()
        await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
    assert frames(s, "error"), "the failure is said, loudly"
    voice_tasks = [t for t in asyncio.all_tasks()
                   if t.get_coro().__qualname__ == "_TurnVoice._run"]
    assert voice_tasks == [], f"an audio leg outlived its turn: {voice_tasks}"
    assert [m["from"] for m in frames(s, "unspoken")] == [1]


@pytest.mark.asyncio
async def test_speech_off_stop_drains_muted_legs_rather_than_cancelling(
        tmp_path):
    """The rule itself, with the queue held full: when speech is OFF a stop
    DRAINS, because a muted leg asks no engine and everything a muted reply
    showed is its answer (AC-110). Driven on `_TurnVoice` directly because
    through `_reply` the muted legs drain faster than any stop can land."""
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("speak", False)
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)
    s.turn = 980
    s.spoken = []
    voice = hub_mod._TurnVoice(hub, s, 980, "")
    for i, sentence in enumerate(THREE):
        voice.shown(i, sentence, (sentence, "conversation", True, i == 0))
    voice.halt()                      # what `abandon()` does first
    await voice.stop()                # what `_reply`'s finally does
    assert s.spoken == THREE
    assert voice.unheard_from() is None


class MuteOnTts(StepTts):
    """Answers every sentence but one with audio; that one yields NOTHING,
    the shape an engine abort or an unspeakable segment has."""

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        if "Second" in text:
            return
        yield b"\x00\x00" * 240


@pytest.mark.asyncio
async def test_a_sentence_that_produced_no_audio_is_not_counted_heard(tmp_path):
    """HEARD is `s.spoken` growing, never merely the leg returning: a
    sentence whose synthesis sent no audio was not heard, so the page is
    told so even though the turn ran to its end."""
    hub = make_hub(tmp_path)
    hub.tts = MuteOnTts()
    hub.brain = HangingBrain(THREE)
    s = attach_session(hub)
    task = await start_turn(hub, s, 990, "three sentences please")
    async with reaped(task):
        await until(lambda: len(hub.tts.said) == 3, "all three synthesized")
        await hub.brain.abort(990)           # the model finishes
        await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
    assert s.spoken == [THREE[0], THREE[2]]
    assert [m["from"] for m in frames(s, "unspoken")] == [1]


class DieOnTts(StepTts):
    """Raises on the sentence carrying `Second` -- a dead segment (3.8)."""

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        if "Second" in text:
            raise RuntimeError("the synthesizer died mid-answer")
        yield b"\x00\x00" * 240


@pytest.mark.asyncio
async def test_a_dead_voice_stays_dead_for_sentences_that_arrive_later(
        tmp_path):
    """plan.md 3.8 across the split: once a segment dies, NO later sentence
    is synthesized -- not even one the model produces after the failure --
    because speaking it would skip the sentence the engine choked on. Its
    text is still shown, and marked unheard with the rest."""
    hub = make_hub(tmp_path)
    hub.tts = DieOnTts()
    hub.brain = HangingBrain(THREE[:2], later=THREE[2:])
    s = attach_session(hub)
    task = await start_turn(hub, s, 995, "three sentences please")
    async with reaped(task):
        await until(lambda: frames(s, "error"), "the dead segment reported")
        hub.brain.go.set()                  # the model says one more thing
        await until(lambda: len(replies(s)) == 3, "the late sentence shown")
        await hub.brain.abort(995)          # and finishes
        await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
    assert hub.tts.said == THREE[:2], (
        f"the voice came back after it died: {hub.tts.said}")
    assert [m["from"] for m in frames(s, "unspoken")] == [1]


@pytest.mark.asyncio
async def test_the_project_slot_is_free_once_the_prompt_ends_while_audio_is_still_owed(
        tmp_path):
    """The project SLOT tracks the agent's PROMPT, never the voice
    (docs/filing-and-pacing.md 1.7, daemon/AGENTS.md). With text decoupled
    from audio, a turn can sit in its audio leg and `_await_playback` for
    many seconds after the agent finished; a slot still BUSY then makes New
    chat ask to "stop the current running agent" about an idle one, and a
    "yes" would stop a finished turn.

    Audio is blocked on `GatedTts.gate` for the whole assertion, so the turn
    is provably still speaking when the slot is read.
    """
    from test_harness_question import _hub

    hub, s, harness, _ = _hub(tmp_path)
    hub.tts = GatedTts()
    task = await start_turn(hub, s, 910, "tidy up the scheduler")
    host = hub.brain.host
    try:
        last = harness.script[-1].text
        for _ in range(500):
            if any(last in r for r in replies(s)):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError(f"the answer's last sentence never reached "
                                 f"the page: {replies(s)}")
        await asyncio.sleep(0.05)
        assert not task.done(), "the turn ended; the audio leg was not owed"
        assert not hub.tts.gate.is_set()
        assert host.busy_children_for_project(str(tmp_path)) == [], (
            "the prompt is over and only speech is owed, yet the project "
            "slot still reads BUSY -- New chat would ask to stop an idle "
            "agent")
        # And a turn in the same project is granted, not refused (D1=A
        # reclaims an idle slot with no confirm).
        await host._enforce_project_limit("chat-other", str(tmp_path))
    finally:
        hub.tts.gate.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
