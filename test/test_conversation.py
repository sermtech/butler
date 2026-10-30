"""Several turns in a row, which is the state one turn cannot reach. D-1, AC-2.

The suite proved re-arm ONE turn deep: drive a terminal path, then show that
a fresh wake is honoured. That is not the same claim as "Butler holds a
conversation", and the difference is where this project's oldest defect
lives -- a microphone that comes back once and then latches on the turn
after. Reported from the room on 2026-08-17: the second turn did not re-arm
while the first did.

Every test here therefore runs at least FOUR turns and asserts on the ones
after the first, and each one varies what the earlier turn did: answered
normally, failed at the endpoint, was interrupted. A conversation is where
per-turn state that was never cleaned up becomes visible, and the brain is
the newest thing holding per-turn state (an HTTP request per turn, keyed by
turn id).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, StubResolver, StubTts,        # noqa: E402
                      StubWs, attach_session, make_hub, silence_pcm,
                      tone_pcm)
from daemon import hub as hub_mod, ports                       # noqa: E402
from daemon.modes import ModeStore                             # noqa: E402
from daemon.ports import Final                                 # noqa: E402
from daemon.chats import ChatStore                       # noqa: E402
from daemon.settings import Settings                           # noqa: E402

TURNS = 4
FINAL_TIMEOUT_S = 15.0
SPEAK_TIMEOUT_S = 20.0


# --------------------------------------------------------------------------- #
# over the wire, with the daemon the satellite actually talks to
# --------------------------------------------------------------------------- #
async def one_turn(sat, expect_reply: bool = True) -> int:
    """Wake, speak, be answered, report the speaker quiet. As a device does."""
    await sat.send(type="wake_candidate", word="hey_butler", score=0.92,
                   ts_ms=int(time.time() * 1000))
    start = await sat.next("capture_start", timeout=10.0)
    turn = start["turn"]
    await sat.send_pcm(tone_pcm(600))
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint")

    final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
    assert final.get("turn") == turn, f"final belongs to {turn}: {final}"
    if expect_reply:
        end = await sat.next("speak_end", timeout=SPEAK_TIMEOUT_S)
        assert end.get("turn") == turn, f"speak_end names its turn: {end}"
        # The satellite's half of the playback gate. Withholding it is what
        # the timeout in _await_playback exists for, but a real device sends
        # it, and a daemon that only re-arms on the TIMEOUT would look
        # identical here while adding two seconds to every turn.
        await sat.send(type="playback_done", turn=turn)
    return turn


@pytest.mark.asyncio
async def test_four_turns_in_a_row_on_one_socket(satellite):
    """The reported failure, at the level the report came from.

    Four complete turns without reconnecting, each answered, each with its
    own id. A daemon that leaks a turn claim -- a pipeline never closed, a
    state never returned to IDLE -- passes the one-turn version of this test
    and fails here on turn two.
    """
    turns = []
    for i in range(TURNS):
        turns.append(await one_turn(satellite))
        assert turns[-1] == turns[0] + i, (
            f"turn {i + 1} got id {turns[-1]}, expected {turns[0] + i}: the "
            f"daemon either skipped a turn or reused one")
    assert len(set(turns)) == TURNS


@pytest.mark.asyncio
async def test_an_interrupted_turn_does_not_poison_the_next_one(satellite):
    """Barge-in leaves the most per-turn state behind: a cancelled reply task,
    an aborted synthesis, and now an aborted HTTP request. Then three ordinary
    turns have to work."""
    await satellite.send(type="wake_candidate", word="hey_butler", score=0.9,
                         ts_ms=int(time.time() * 1000))
    first = (await satellite.next("capture_start", timeout=10.0))["turn"]
    await satellite.send_pcm(tone_pcm(400))
    # Wake again mid-turn: the daemon abandons `first` and mints a new turn.
    await satellite.send(type="wake_candidate", word="hey_butler", score=0.95,
                         ts_ms=int(time.time() * 1000))
    second = (await satellite.next("capture_start", timeout=10.0))["turn"]
    assert second != first

    await satellite.send_pcm(tone_pcm(600))
    await satellite.send_pcm(silence_pcm(1200))
    await satellite.send(type="capture_end_hint")
    await satellite.next("final", timeout=FINAL_TIMEOUT_S)
    await satellite.next("speak_end", timeout=SPEAK_TIMEOUT_S)
    await satellite.send(type="playback_done", turn=second)

    for _ in range(3):
        await one_turn(satellite)


@pytest.mark.asyncio
async def test_a_recognizer_failure_is_spoken_and_the_next_turn_is_ordinary(
        tmp_path):
    """D-26: a turn Butler could not finish hearing must not sound like silence.

    An empty Final is a legal turn end and means the room was quiet (D-7,
    AC-6) -- the daemon answers it with nothing at all, which is correct. A
    Final marked `failed` is the other fact: the recognizer ran out of budget
    or its engine died, and the utterance is gone. Ending that one silently is
    what shipped: the owner dictated for half a minute and got a turn that
    completed normally having heard nothing.

    Two turns, because the failure is per-turn state and this suite's rule is
    that one turn proves a path works once (D-1). Turn 2 is ordinary and must
    reach the brain.
    """
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=ports.build(ports.BRAINS, "fake"),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"))
    s = attach_session(hub)

    s.turn = 1
    await asyncio.wait_for(
        hub._on_final(s, 1, Final(text="", failed=True), "silence"),
        FINAL_TIMEOUT_S)

    assert hub_mod.Hub.ASR_FAILED in hub.tts.said, (
        f"the turn ended in silence, which is what a quiet room sounds like: "
        f"{hub.tts.said}")
    assert hub.brain.seen == [], (
        f"an utterance nobody heard was handed to the brain: {hub.brain.seen}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the failed turn did not re-arm (D-1)")

    s.turn = 2
    await asyncio.wait_for(
        hub._on_final(s, 2, Final(text="what time is it"), "silence"),
        FINAL_TIMEOUT_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), SPEAK_TIMEOUT_S)

    assert [t for t, _turn in hub.brain.seen] == ["what time is it"], (
        f"the turn after a recognizer failure did not reach the brain: "
        f"{hub.brain.seen}")


# --------------------------------------------------------------------------- #
# with the real brain, against a server that records what it was asked
# --------------------------------------------------------------------------- #
async def hub_turn(hub, s, turn: int, text: str = "explain the GIL") -> None:
    """One turn through the hub's reply path, playing the satellite's part."""
    s.turn = turn
    s.state = hub_mod.THINKING
    # WHICH CONVERSATION, exactly as `_begin_turn` decides it (AC-246). This
    # driver reproduces that method by hand and is the second copy its own
    # docstring warns about: the field it forgot was this one, and a turn with
    # a blank `turn_chat` files its question and its transcript nowhere.
    chat = hub.chat_for(s)
    s.turn_chat = chat
    # docs/parallel-agents.md phase 6, item 1: `_begin_turn` also registers
    # the turn in these two dicts now, and `owns()`/`_on_ask_human` read them
    # rather than the scalar above -- a driver that only learned `turn_chat`
    # would file this turn's question and record nowhere (the same failure
    # the comment above already names, one field further along).
    s.turn_by_chat[chat] = turn
    s.chat_by_turn[turn] = chat
    # docs/parallel-agents.md phase 7 item 2 (AC-236): `_begin_turn` also
    # stamps the turn-start record in the SAME breath now, and a driver that
    # forgot it would leave `Hub._elapsed_ms` reading zero for a turn that
    # has genuinely been running -- the same drift the two comments above
    # already name, one field further along again.
    hub._turn_started[chat] = time.time()
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


def endpoint_hub(tmp_path, server):
    hub = make_hub(tmp_path)
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(server.base_url))
    return hub, attach_session(hub)


def replies(s) -> list[str]:
    return [json.loads(m)["text"] for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == "reply"]


@pytest.mark.asyncio
async def test_the_endpoint_brain_answers_four_turns_in_a_row(fake_openai,
                                                              tmp_path):
    """Each turn gets its own answer, and the session is IDLE between them.

    Scripted with a DIFFERENT answer per turn, so a brain that replayed the
    first turn's response -- a cached session, a stream never drained -- is
    visible rather than plausible.
    """
    hub, s = endpoint_hub(tmp_path, fake_openai)
    for i in range(TURNS):
        fake_openai.verdict(route="converse")
        fake_openai.answer(f"This is answer number {i}.")

    for i in range(TURNS):
        await hub_turn(hub, s, turn=100 + i, text=f"question {i}")
        assert s.state == hub_mod.IDLE, (
            f"after turn {i + 1} the session sat in {s.state!r}; the next "
            f"wake would be refused as busy (D-1)")
        assert s.turn is None, f"turn {i + 1} left its claim behind"
        assert f"This is answer number {i}." in replies(s), (
            f"turn {i + 1} did not get its own answer: {replies(s)}")

    assert len([b for b in fake_openai.asks if b.body.get("stream")]) == TURNS


@pytest.mark.asyncio
async def test_a_failed_turn_does_not_take_the_next_one_with_it(fake_openai,
                                                                tmp_path):
    """D-1 in its original shape: an error must not latch the microphone off.

    The endpoint answers 500 on the second turn, which is the realistic
    failure -- a model being swapped out underneath a shared server -- and
    the turns after it must be ordinary.
    """
    hub, s = endpoint_hub(tmp_path, fake_openai)
    fake_openai.verdict(route="converse")
    fake_openai.answer("The first turn worked.")
    fake_openai.verdict(route="converse")
    fake_openai.answer("", status=500, body="model is loading")
    for i in range(2):
        fake_openai.verdict(route="converse")
        fake_openai.answer(f"Recovered turn {i}.")

    await hub_turn(hub, s, turn=200)
    assert s.state == hub_mod.IDLE

    await hub_turn(hub, s, turn=201)
    errors = [json.loads(m) for m in s.ws.sent
              if isinstance(m, str) and json.loads(m)["type"] == "error"]
    assert errors and errors[-1]["turn"] == 201, (
        f"the failed turn was silent; the page has nothing to re-arm on: "
        f"{s.ws.text_types()}")
    assert s.state == hub_mod.IDLE, "a failed turn latched the session"

    for turn in (202, 203):
        await hub_turn(hub, s, turn=turn)
        assert s.state == hub_mod.IDLE
    assert "Recovered turn 1." in replies(s), replies(s)


# --------------------------------------------------------------------------- #
# with the real recognizer, which is the half a fake cannot speak for
# --------------------------------------------------------------------------- #
@pytest.mark.needs_gpu
@pytest.mark.slow
@pytest.mark.asyncio
async def test_four_turns_through_the_real_recognizer(real_satellite):
    """The one component a scripted fake cannot stand in for here.

    `asr_fake` builds a fresh scripted session per turn and always finalizes;
    transcribe.cpp holds a worker thread, a streaming graph and per-utterance
    state, and "the second turn never produced a final" is indistinguishable
    from a dead microphone at the page -- the session sits in CAPTURING, the
    wake detector is not re-armed, and nothing says why.

    Real words, not a tone: a turn that finalizes EMPTY re-arms through a
    different path (AC-6) and would not exercise the reply at all.
    """
    # Which engine actually answered, from the daemon's own handshake. The
    # fixture asks for the real one by environment; nothing until now checked
    # that it got it, and a silent fall back to the fake would make this test
    # a slower copy of the one above.
    ready = next((m for m in real_satellite.seen if m.get("type") == "ready"),
                 None)
    assert ready and ready["stt"].get("arch") not in (None, "fake"), (
        f"this daemon is not running the real recognizer: {ready}")

    wav = Path(__file__).resolve().parents[0] / "assets" / "short_question.wav"
    if not wav.exists():
        pytest.skip("test/assets/short_question.wav has not been generated")
    import wave
    with wave.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())

    heard = []
    for i in range(TURNS):
        await real_satellite.send(type="wake_candidate", word="hey_butler", score=0.92,
                                  ts_ms=int(time.time() * 1000))
        start = await real_satellite.next("capture_start", timeout=20.0)
        turn = start["turn"]
        await real_satellite.send_pcm(pcm)
        await real_satellite.send_pcm(silence_pcm(1200))
        await real_satellite.send(type="capture_end_hint")

        final = await real_satellite.next("final", timeout=60.0)
        assert final.get("turn") == turn, (
            f"turn {i + 1}: final named {final.get('turn')}, expected {turn}")
        heard.append((final.get("text") or "").strip())
        if heard[-1]:
            end = await real_satellite.next("speak_end", timeout=SPEAK_TIMEOUT_S)
            assert end.get("turn") == turn
            await real_satellite.send(type="playback_done", turn=turn)

    # Every turn transcribed the WHOLE fixture. Asserting merely that the text
    # is non-empty would pass against the scripted fake too -- it emits one
    # word per 560 ms of audio, so a 1.08 s clip gives it "what" and nothing
    # more -- and a test that cannot tell which engine answered proves nothing
    # about the engine it names (D-13).
    normalised = [" ".join(t.lower().strip(" .?!").split()) for t in heard]
    assert normalised == ["what time is it"] * TURNS, (
        f"the recognizer did not transcribe every turn of the conversation: "
        f"{heard}")


# --------------------------------------------------------------------------- #
# with the harness brain, which holds a CHILD and a reader across turns
# --------------------------------------------------------------------------- #
def harness_hub(tmp_path, server):
    """A hub whose brain talks to a (fake) coding agent. 7.4."""
    from daemon.settings import Settings

    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    harness = ports.build(ports.HARNESSES, "fake")
    chats = ChatStore(tmp_path / "chats")
    hub = make_hub(tmp_path)
    hub.brain = ports.build(ports.BRAINS, "harness",
                            resolver=StubResolver(server.base_url),
                            harness_for=lambda _chat=None: harness,
                            spec_for=harness_spec_resolver(prefs, chats),
                            chats=chats,
                            cap_for=lambda: prefs["harness_agents_max"])
    return hub, attach_session(hub), harness


@pytest.mark.asyncio
async def test_the_harness_brain_answers_four_turns_on_one_held_child(
        fake_openai, tmp_path):
    """The newest per-turn state in the daemon is a CHILD and its reader task.

    One turn deep, every one of these passes against a brain that respawns on
    every question, or that leaves the previous prompt's ending queued for the
    next turn to mistake for its own. Four turns is where both become visible
    (test/AGENTS.md), and this is the CALLER-side witness for AC-59: the
    adapter-level claim is in `test_brain_harness.py`, and only a real hub
    conversation proves the daemon's own turn machinery -- `_let_go` on every
    exit, the re-arm, the reply path -- does not take the child away between
    questions (D-18).

    The count has INVERTED twice. It was one child for the whole conversation
    (held from boot), then four (AC-46, 2026-08-18), and is one again with
    AC-59 (2026-08-19) -- but for a different reason than the first time: the
    child is created by the first TURN, not by boot, and any turn that ends
    badly still drops it.
    """
    hub, s, harness = harness_hub(tmp_path, fake_openai)
    await hub.brain.warm()
    for _ in range(TURNS):
        fake_openai.verdict(route="converse")

    for i in range(TURNS):
        await hub_turn(hub, s, turn=400 + i, text=f"question {i}")
        assert s.state == hub_mod.IDLE, (
            f"after turn {i + 1} the session sat in {s.state!r}; the next "
            f"wake would be refused as busy (D-1)")
        assert s.turn is None, f"turn {i + 1} left its claim behind"
        assert harness.prompts[-1] == f"question {i}", (
            f"turn {i + 1} did not reach the child: {harness.prompts}")

    assert len(harness.specs) == 1, (
        f"a {TURNS}-turn conversation spawned {len(harness.specs)} children; "
        f"AC-59 holds one across the whole conversation, and every extra "
        f"spawn is 1.5-2.3 s of dead air the owner hears")
    assert harness.disposals == 0, (
        f"{TURNS} turns that all ended cleanly disposed {harness.disposals} "
        f"child(ren); only a turn that failed, was interrupted or was "
        f"abandoned may drop one (AC-59)")
    assert hub.brain.host.harness is not None, (
        "no child is held after four clean turns, so the fifth question pays "
        "a spawn (AC-59)")
    assert len({s.session_file for s in harness.specs}) == 1, (
        f"the conversation did not stay on one session file, so the agent "
        f"forgot it between messages: {harness.specs}")
    assert len(harness.prompts) == TURNS, harness.prompts
    answer = "It holds three policies and one of them is not wired up."
    # Stripped: this sentence opens the answer's second text block, so its
    # frame leads with the paragraph break the page renders (ports.TextDelta).
    assert [r.strip() for r in replies(s)].count(answer) == TURNS, (
        f"a turn went unanswered or was answered twice: {replies(s)}")


class DyingTts(StubTts):
    """Speaks the reflex line, then dies on the answer's first segment.

    The measured shape of plan.md 3.8 / D-4: one dead segment abandons the
    REST of the reply, so `hub._reply` BREAKS out of the brain's generator
    without ever seeing a `Done`. That break is the one commit `ecc2b86` did
    not close, and a brain that owns a child process for the turn leaks it
    there (AC-59, AC-36).
    """

    def __init__(self, die_on: int = 2):
        super().__init__()
        self.calls = 0
        self.die_on = die_on

    # `speed` accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.calls += 1
        if self.calls >= self.die_on:
            raise RuntimeError("the synthesizer died mid-answer")
        yield b"\x00" * 960
        return


class BrokenWs(StubWs):
    """Raises ONCE, on the first conversation-tier reply frame.

    Not a `ConnectionResetError` or a `RuntimeError`: those two are the
    expected socket deaths and `SatelliteSession.send` swallows them by
    design. This is the UNEXPECTED one, which travels up through `_speak`
    into `_reply`'s loop and is caught by its `except Exception` -- the
    second uncovered break out of the brain's turn generator. It raises only
    once so the error frame the handler sends afterwards can still go out;
    a stub that kept raising would test the error path instead of this one.
    """

    def __init__(self):
        super().__init__()
        self.broke = False

    async def send_str(self, s):
        payload = json.loads(s)
        if (not self.broke and payload.get("type") == "reply"
                and payload.get("tier") == "conversation"):
            self.broke = True
            raise ValueError("the wire went wrong in a way nobody expected")
        await super().send_str(s)


@pytest.mark.asyncio
async def test_a_synthesis_failure_mid_answer_ends_the_voice_and_not_the_agent(
        fake_openai, tmp_path):
    """plan.md 3.8 under the text/voice split (docs/filing-and-pacing.md
    item 2, the owner's delegated ruling of 2026-09-26).

    This test used to be `..._still_disposes_the_child`: `_speak` returned
    False on a dead segment and `_reply` BROKE out of the turn generator with
    the prompt still in flight, so the caller had to dispose the child it
    walked away from (D-18, AC-59). That break is GONE BY DESIGN, not
    unguarded: text is never gated on synthesis, so a dead segment ends the
    VOICE -- loudly, with nothing later attempted and the unheard text
    marked -- while the text leg reads the brain to its own end. The prompt
    therefore COMPLETES and its child is held exactly as after any finished
    turn (AC-59), so there is no unfinished child to leak. The other break
    out of the generator, an exception in the loop, keeps its disposal test
    just below.

    No `gc.collect()` here, deliberately, for the reason the old test gave.
    """
    hub, s, harness = harness_hub(tmp_path, fake_openai)
    hub.tts = DyingTts()
    fake_openai.verdict(route="converse")

    await hub_turn(hub, s, turn=700, text="explain the GIL")

    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the session did not re-arm after a synthesis failure (D-1)")
    sent = [json.loads(m) for m in s.ws.sent if isinstance(m, str)]
    assert any(m["type"] == "error" for m in sent), (
        "the dead segment was not reported: 3.8 says LOUDLY")
    assert hub.tts.calls == hub.tts.die_on, (
        f"the voice tried again after it died ({hub.tts.calls} calls), which "
        f"would skip the sentence the engine choked on (3.8)")
    shown = [m for m in sent if m["type"] == "reply"]
    marks = [m for m in sent if m["type"] == "unspoken"]
    assert len(shown) >= 2 and [m["from"] for m in marks] == [0], (
        f"the whole answer must be shown and marked unheard from the sentence "
        f"that died: shown={shown} marks={marks}")
    assert harness.disposals == 0 and hub.brain.host.harness is not None, (
        "the prompt ran to its end, so its child is held like any finished "
        "turn's (AC-59); a synthesis failure is not a reason to kill the agent")


@pytest.mark.asyncio
async def test_an_exception_in_the_reply_loop_still_disposes_the_child(
        fake_openai, tmp_path):
    """The same contract, through the break `_reply`'s own `except` hides.

    An unexpected exception raised while the daemon is consuming the brain's
    intents is swallowed by `_reply`'s `except Exception`, which sends an
    error frame and re-arms -- and never touches the generator it walked away
    from. Same suspended frame, same live child, and no `Done` was ever seen
    (D-18, AC-59).
    """
    hub, s, harness = harness_hub(tmp_path, fake_openai)
    s.ws = BrokenWs()
    fake_openai.verdict(route="converse")

    await hub_turn(hub, s, turn=710, text="explain the GIL")

    assert s.ws.broke, (
        "the stub never raised, so this test proved nothing about the "
        "exception path (D-13)")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the session did not re-arm after an unexpected exception (D-1)")
    assert harness.disposals >= 1, (
        "an exception in the reply loop left the turn generator suspended "
        "holding a live child whose prompt never ended (AC-59, AC-36, D-18)")
    assert hub.brain.host.harness is None, (
        "the host still holds a child after the turn ended")


# --------------------------------------------------------------------------- #
# AC-29 is RETIRED (2026-08-18): there is no ceremony between the owner and
# the coding agent any more
# --------------------------------------------------------------------------- #
def harness_hub_in_mode(tmp_path, server, mode: str):
    """A harness-mode hub sitting at a named permission mode.

    The mode STORE is set for real, because the claim is that it changes
    nothing about what reaches the agent. `QuietTts` says the words and makes
    no audio, so a turn does not wait out the playback gate.
    """
    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    store = ModeStore(tmp_path / "mode.json")
    store.set(mode, authorized=True)
    harness = ports.build(ports.HARNESSES, "fake")
    chats = ChatStore(tmp_path / "chats")
    hub = hub_mod.Hub(
        asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
        brain=ports.build(ports.BRAINS, "harness",
                          resolver=StubResolver(server.base_url),
                          harness_for=lambda _chat=None: harness,
                          spec_for=harness_spec_resolver(prefs, chats),
                          chats=chats,
                          cap_for=lambda: prefs["harness_agents_max"]),
        modes=store, asr_config=ports.AsrConfig(model="test"), settings=prefs)
    return hub, attach_session(hub), harness


async def utterance(hub, s, turn: int, text: str) -> None:
    """One whole utterance through `_on_final`, which is where the daemon's
    own branches live -- including the one that used to eat the next thing
    said as an answer to a parked question."""
    s.turn = turn
    await asyncio.wait_for(hub._on_final(s, turn, Final(text=text), "silence"),
                           20)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), 20)


@pytest.mark.parametrize("mode", ["secretary", "paranoid"])
@pytest.mark.asyncio
async def test_a_writing_dispatch_reaches_the_agent_with_no_repeat_back(
        fake_openai, tmp_path, mode):
    """AC-29 RETIRED 2026-08-18: the harness runs at full capability always.

    The router returns a `dispatch` verdict whose action WRITES -- the exact
    utterance that used to be repeated back and parked until the owner said
    yes. It now goes straight to the coding agent, in every permission mode,
    and the mode store is set for real here to say so: `paranoid` and
    `secretary` are the same answer, because the permission modes gate
    nothing for the harness until Phase 2's worker dispatch.

    Two turns rather than one, and the second is the assertion with teeth: a
    parked confirmation takes the NEXT thing said, whatever it is, so a
    daemon that still parks would answer turn 2 with a cancellation instead
    of handing it to the agent (test/AGENTS.md, a conversation is not a turn).
    """
    hub, s, harness = harness_hub_in_mode(tmp_path, fake_openai, mode)
    await hub.brain.warm()
    fake_openai.verdict(route="dispatch",
                        action={"summary": "rename the endpointing module",
                                "writes": True})

    await utterance(hub, s, 1, "rename the endpointing module")

    assert harness.prompts == ["rename the endpointing module"], (
        f"a writing dispatch in {mode!r} did not reach the coding agent: "
        f"{harness.prompts}")
    assert not any("yes or no" in t.lower() for t in replies(s)), (
        f"a repeat-back was spoken for a ceremony that no longer exists: "
        f"{replies(s)}")

    fake_openai.verdict(route="converse")
    await utterance(hub, s, 2, "what does the endpointing module do")

    assert harness.prompts[-1] == "what does the endpointing module do", (
        f"the turn after a write was eaten as the answer to a parked "
        f"question: {harness.prompts}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the write turn did not re-arm (D-1)")


@pytest.mark.asyncio
async def test_a_conversation_leaves_no_request_behind(fake_openai, tmp_path):
    """The brain holds one HTTP request per turn so barge-in can close it
    (D-4). Held is the operative word: an entry that outlives its turn is a
    socket nobody will ever close, and four turns is where that starts to
    look like a leak rather than a race."""
    hub, s = endpoint_hub(tmp_path, fake_openai)
    for i in range(TURNS):
        fake_openai.verdict(route="converse")
        fake_openai.answer(f"Answer {i}.")
        await hub_turn(hub, s, turn=300 + i)

    inflight = hub.brain._router._client._open
    assert inflight == {}, (
        f"{len(inflight)} request(s) still registered after the conversation "
        f"ended: {sorted(inflight)}")


# --------------------------------------------------------------------------- #
# Phase 1's two refusals, which are BEHAVIOUR and not an unhandled branch
#
# plan.md 4.10 and step 7 of the build order claim both of these are spoken
# and tested. They were spoken and untested until 2026-08-18, which is the
# same shape of claim D-12 exists for: a plan sentence describing a test
# nobody wrote reads as coverage to whoever comes next.
# --------------------------------------------------------------------------- #
def phase1_hub(tmp_path, rules):
    """A hub on the scripted brain, driven by rules the test supplies.

    `brain_fake` is the Brain port's second implementation and emits whatever
    it is scripted to (daemon/adapters/AGENTS.md). That is the only way to put
    a `Dispatch` or a `Steer` in front of the hub at all: the router turns a
    `dispatch` verdict into `AskConfirm` and never into either of them (4.10),
    so these branches are reachable only from a brain that proposes one
    directly -- which is exactly what a swapped-in agent framework would do.
    `QuietTts` says the words and makes no audio, so the turn does not wait
    out the playback gate once per assertion.
    """
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=ports.build(ports.BRAINS, "fake", rules=rules),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"))
    return hub, attach_session(hub)


@pytest.mark.asyncio
async def test_dispatch_and_steer_are_answered_in_words(tmp_path):
    """Phase 1 owns no Worker Manager and SAYS SO (plan.md 4.10, AC-47, D-1).

    A turn that makes no sound is indistinguishable from a dead microphone,
    and an intent with no branch is how one arrives: the hub would run out of
    the intent loop, speak nothing, and re-arm as if it had answered. So the
    refusal is asserted on the WORDS, not on "the turn ended".

    Two turns, and the second is the other intent, because these are separate
    branches of the same loop: a hub that answered `Dispatch` and dropped
    `Steer` looks correct one turn deep (test/AGENTS.md).
    """
    hub, s = phase1_hub(tmp_path, [
        ("dispatch it", lambda t, c: [
            ports.Dispatch(ports.Action(summary="rename the module",
                                        writes=True)), ports.Done("reflex")]),
        ("tell the worker", lambda t, c: [
            ports.Steer("w-1", t), ports.Done("reflex")]),
    ])

    await hub_turn(hub, s, turn=500, text="dispatch it now")
    assert replies(s) == ["I cannot run workers yet."], (
        f"a Dispatch left the turn silent or answered something else: "
        f"{replies(s)}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the refused dispatch did not re-arm (D-1, AC-2)")

    await hub_turn(hub, s, turn=501, text="tell the worker to stop")
    assert replies(s) == ["I cannot run workers yet."] * 2, (
        f"a Steer was dropped where a Dispatch was answered: {replies(s)}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the refused steer did not re-arm (D-1, AC-2)")


@pytest.mark.asyncio
async def test_a_brain_that_cannot_proceed_refuses_the_confirmation_aloud(
        tmp_path):
    """The other half of `_on_ask_confirm`, and the one AC-29 did not retire.

    A brain WITHOUT `CAN_PROCEED` -- every endpoint boot, and all of Phase 2's
    dispatch until the Worker Manager exists -- has nothing that can carry the
    work out, so the proposal is spoken and nothing is spawned. Its sibling
    above (`test_a_writing_dispatch_reaches_the_agent_with_no_repeat_back`)
    asserts the opposite branch on a brain that CAN. The capability is read
    off the adapter and never off its name (daemon/AGENTS.md), so this test
    scripts the fake and never sets a flag.

    The refusal NAMES the work, because "I cannot do that yet" with no subject
    leaves the owner unable to tell which of the last two things they said was
    refused. Second turn: an ordinary utterance is answered normally, which is
    what proves nothing was parked waiting for a yes.
    """
    hub, s = phase1_hub(tmp_path, [
        ("delete the logs", lambda t, c: [
            ports.AskConfirm(ports.Action(summary="delete the logs",
                                          writes=True)),
            ports.Done("reflex")]),
    ])

    await hub_turn(hub, s, turn=510, text="delete the logs")
    assert replies(s) == [
        "I would need to delete the logs, but I cannot do that yet."], (
        f"a proposal no brain can carry out was not refused in words: "
        f"{replies(s)}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the refused confirmation did not re-arm (D-1, AC-2)")

    await hub_turn(hub, s, turn=511, text="what did you make of that")
    assert replies(s)[1:] == ["Let me think about that.",
                              "Here is the longer answer to your question."], (
        f"the turn after the refusal was eaten as an answer to a question "
        f"nobody asked: {replies(s)}")


# --------------------------------------------------------------------------- #
# AC-64/AC-65 -- the conversation reaches the models that decide the next turn
# --------------------------------------------------------------------------- #
def router_bodies(server) -> list[dict]:
    """Every body the ROUTER sent, in order.

    Told apart from the reply requests by the response format the router is
    the only caller to use, rather than by position: a warm() at boot and a
    reply on the same server would both shift an index, and a test that
    counted requests would then assert about the wrong one.
    """
    return [a.body for a in server.asks
            if a.body.get("response_format", {}).get("type") == "json_schema"]


def turns_in(body: dict) -> list[tuple]:
    return [(m["role"], m["content"]) for m in body["messages"]]


@pytest.mark.asyncio
async def test_the_router_sees_the_conversation(fake_openai, tmp_path):
    """AC-65, and the defect the owner actually reported.

    `Router.verdict` used to build `[system, user]` -- one utterance, no
    history -- so a follow-up was classified by a model that had never seen
    what it was following up ON. Routes `answer` and `clarify` are answered by
    that model and NEVER reach the substantive brain, so the owner had to
    restate the context of every question or be answered from nothing.

    Asserted on the ROUTER's own request body rather than on the reply's,
    because the reply model has had `ctx.history` wired since it was written
    (brain_endpoint) and the router is the half that was missing.
    """
    hub, s = endpoint_hub(tmp_path, fake_openai)
    for _ in range(2):
        fake_openai.verdict(route="converse")
        fake_openai.answer("Paris is the capital of France.")

    await hub_turn(hub, s, turn=800, text="what is the capital of France")
    await hub_turn(hub, s, turn=801, text="how many people live there")

    first, second = router_bodies(fake_openai)
    assert turns_in(first)[1:] == [
        ("user", "what is the capital of France")], (
        f"the FIRST turn's router was given a history out of nowhere: "
        f"{turns_in(first)}")
    assert turns_in(second)[1:] == [
        ("user", "what is the capital of France"),
        ("assistant", "Paris is the capital of France."),
        ("user", "how many people live there")], (
        f"the router classified a follow-up with no idea what it followed: "
        f"{turns_in(second)}")


@pytest.mark.asyncio
async def test_history_holds_what_was_SPOKEN_and_not_what_was_generated(
        fake_openai, tmp_path):
    """D-20, and the whole reason this is filled in the hub and not the brain.

    A reply is synthesized segment by segment and barge-in can land between
    two of them. Only the segments whose audio actually reached the satellite
    were heard, and telling the next turn's model it said the rest is the
    context-desync defect: it answers a question it never actually answered,
    or refers back to advice nobody received. `SatelliteSession.spoken` has
    recorded exactly the right thing since it was written -- a segment cut off
    mid-stream is deliberately not appended -- and nothing read it.

    Driven by taking the turn away mid-answer, which is what `abandon()` does.

    Asserted on `hub.history` DIRECTLY rather than on the router's request
    body, so this test is aimed at the recording rule alone. Reading it
    through the router made it fail for two different reasons -- the wrong
    text recorded, and the history not reaching the router at all -- and a
    mutation run then reddened it alongside the test above for both. This
    rule still has two honest witnesses, and `mutate_history.py` names both.
    """
    hub, s = endpoint_hub(tmp_path, fake_openai)

    class StealsAfterOneSegment(StubTts):
        """Takes the turn away BEFORE the second segment produces any audio.

        Deterministic on purpose. The first version of this test raced a
        watcher task against the stub, and the stub is instant -- both
        segments were spoken before the watcher ran, so it asserted that the
        history matched what was spoken while nothing had been interrupted at
        all (D-13: an instrument that cannot fail is not one).
        """

        def __init__(self):
            super().__init__()
            self.calls = 0

        # `speed` accepted and ignored: a double with no rate control
        # honestly honours 1.0 and nothing else (AC-107).
        async def synthesize(self, text, voice, *, speed=1.0, handle=None):
            self.calls += 1
            if self.calls >= 2:
                s.turn = None        # barge-in, landing between two segments
                return
            yield b"\x00" * 960

    hub.tts = StealsAfterOneSegment()
    fake_openai.verdict(route="converse")
    fake_openai.answer("The first sentence. The second sentence.")
    fake_openai.verdict(route="converse")
    fake_openai.answer("Fine.")

    await hub_turn(hub, s, turn=810, text="tell me two things")

    heard = list(s.spoken)
    assert heard == ["The first sentence."], (
        f"the stub did not interrupt between the two segments, so this test "
        f"is measuring nothing: {heard}")

    assert list(hub.history) == [
        ("user", "tell me two things"),
        ("assistant", "The first sentence.")], (
        f"history carried something other than what the room heard: "
        f"{list(hub.history)}")
    assert "The second sentence." not in str(list(hub.history)), (
        "the interrupted turn told the next one it had said a sentence the "
        "owner never heard (D-20)")


@pytest.mark.asyncio
async def test_a_new_conversation_clears_the_history(fake_openai, tmp_path):
    """Ending a conversation ends it for the ROUTER too (AC-64, AC-63).

    A history that survived the rotation would make "new conversation" a lie
    told by the one model that runs on every single utterance -- the owner
    would have ended a conversation that keeps deciding how the next one is
    routed.
    """
    hub, s, _harness = harness_hub(tmp_path, fake_openai)
    fake_openai.verdict(route="converse")
    await hub_turn(hub, s, turn=820, text="the conversation being ended")
    assert hub.history, "nothing was recorded, so the clear proves nothing"

    await hub._new_conversation(s, 821)

    assert not hub.history, (
        f"the ended conversation is still in front of the router: "
        f"{list(hub.history)}")


@pytest.mark.asyncio
async def test_a_turn_that_finishes_late_records_no_answer(fake_openai,
                                                           tmp_path):
    """The one interleaving `s.spoken` cannot survive (AC-64, D-6, D-20).

    `abandon()` awaits the reply task on a 3 s bound and then starts the next
    capture, which CLEARS `s.spoken`. A reply task wedged past that bound
    therefore reaches `_remember` after the next turn has begun filling that
    list, and recording then files the NEXT turn's answer under THIS turn's
    question -- the same desync the invariant exists to prevent, arriving by
    the clock instead of by a barge-in.

    Driven directly rather than by wedging a real task, because a test that
    has to hang for three seconds to prove a rule is a test nobody will keep
    (test/AGENTS.md: no unbounded waits). What is asserted is the rule itself:
    with the session on a DIFFERENT turn, the answer is not recorded.

    `_remember` no longer writes the USER half at all
    (docs/filing-and-pacing.md item 2 regression) -- that line is recorded in
    `_reply`, at turn start, so its position in `hub.history` is decided by
    when the owner actually spoke rather than by when an abandoned turn's
    coroutine happens to unwind. This test seeds it by hand, the way `_reply`
    would have by the time `_remember` runs for real.
    """
    hub, s = endpoint_hub(tmp_path, fake_openai)
    s.spoken = ["an answer belonging to the turn that came after"]
    s.turn = 902

    hub.history.append(("user", "the question whose turn ran long"))
    hub._remember(s, 901, "the question whose turn ran long")

    assert list(hub.history) == [("user",
                                  "the question whose turn ran long")], (
        f"a late turn claimed the next turn's answer as its own: "
        f"{list(hub.history)}")

    # And the ordinary case still records the assistant half, or the guard
    # above is just a switch that turns recording off (D-13).
    s.turn = 903
    hub.history.append(("user", "a question whose turn ended on time"))
    hub._remember(s, 903, "a question whose turn ended on time")
    assert list(hub.history)[-2:] == [
        ("user", "a question whose turn ended on time"),
        ("assistant", "an answer belonging to the turn that came after")], (
        f"the guard dropped the answer of a turn that still owned itself: "
        f"{list(hub.history)}")


# --------------------------------------------------------------------------- #
# AC-110: Butler can be told not to speak
#
# Four turns, because the whole risk of muting is a terminal path (D-1, AC-2).
# A muted turn produces NO audio, so the playback gate has nothing to wait for
# and the satellite has nothing to drain -- and one turn cannot tell "the turn
# ended" from "the turn ended once and then latched".
# --------------------------------------------------------------------------- #
def muted_hub(tmp_path, server, speak: bool):
    """An endpoint-mode hub whose `speak` setting is set for real.

    The endpoint brain and the real `FakeTts` on purpose: the fake yields
    paced 24 kHz chunks, so "no audio was produced" is a claim about binary
    frames on the wire rather than about a stub that never made any.
    """
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("speak", speak)
    hub = make_hub(tmp_path, settings=prefs)
    hub.brain = ports.build(ports.BRAINS, "endpoint",
                            resolver=StubResolver(server.base_url))
    return hub, attach_session(hub)


def audio_frames(s) -> list:
    return [m for m in s.ws.sent if isinstance(m, bytes)]


@pytest.mark.parametrize("speak", [True, False])
@pytest.mark.asyncio
async def test_muting_removes_the_audio_and_nothing_else(fake_openai, tmp_path,
                                                         speak):
    """AC-110, over four turns, and both values of the setting.

    Parametrized rather than written once, because "muted" only means
    anything against the same conversation UNMUTED: every assertion below
    except the audio one is identical in both columns, which is the whole
    claim -- muting output is not dropping the answer.

    What a muted turn must still do: send its `reply` text, bracket itself
    with speak_start/speak_end (the frame the page's drain and re-arm hang
    off -- speak_end means "no more audio is coming", which is exactly true
    here), record the answer in `hub.history` for the next turn's router
    (AC-64, D-20), and land on `_rearm`. A daemon that silences the reply by
    returning early from `_speak` passes turn one and leaves the page in its
    thinking state forever afterwards.
    """
    hub, s = muted_hub(tmp_path, fake_openai, speak)
    answers = [f"answer number {i}" for i in range(TURNS)]
    for i in range(TURNS):
        fake_openai.verdict(route="converse")
        fake_openai.answer(answers[i])

    for i in range(TURNS):
        await hub_turn(hub, s, turn=1100 + i, text=f"question {i}")
        assert s.state == hub_mod.IDLE and s.turn is None, (
            f"turn {i} did not re-arm with speak={speak}; a muted turn must "
            f"end on the same terminal path as a spoken one (D-1, AC-2)")

    types = s.ws.text_types()
    assert types.count("speak_start") == TURNS, (
        f"{types.count('speak_start')} speak_start frames for {TURNS} turns "
        f"with speak={speak}: the page re-arms off this bracket, so a turn "
        f"that skips it never gives the microphone back (D-1)")
    assert types.count("speak_end") == TURNS, (
        f"{types.count('speak_end')} speak_end frames for {TURNS} turns with "
        f"speak={speak}: {types}")

    for answer in answers:
        assert answer in replies(s), (
            f"{answer!r} never reached the page with speak={speak}; muting "
            f"output is not dropping the answer (AC-110)")

    spoken = [text for role, text in hub.history if role == "assistant"]
    for answer in answers[-3:]:
        assert any(answer in text for text in spoken), (
            f"{answer!r} is not in the rolling transcript with "
            f"speak={speak}: {list(hub.history)} (AC-64, D-20)")

    if speak:
        assert audio_frames(s), (
            "no audio at all with speak=True, so the muted column below "
            "proves nothing")
    else:
        assert not audio_frames(s), (
            f"{len(audio_frames(s))} audio frames were sent with "
            f"speak=False (AC-110)")


@pytest.mark.asyncio
async def test_a_muted_turn_still_reaches_the_record(fake_openai, tmp_path):
    """AC-110 against `chats.py`, which is the DURABLE half (AC-35).

    `hub.history` is bounded and the record is not, and they are allowed to
    differ -- so "the transcript still has it" is not the same claim as "the
    conversation survives a restart with it". Two turns, because the record
    is appended to rather than rewritten and a store that only ever holds the
    first message looks correct after one.
    """
    from daemon.butlerd import harness_spec_resolver

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    prefs.set("speak", False)
    harness = ports.build(ports.HARNESSES, "fake")
    chats = ChatStore(tmp_path / "chats")
    hub = make_hub(tmp_path, settings=prefs)
    hub.brain = ports.build(ports.BRAINS, "harness",
                            resolver=StubResolver(fake_openai.base_url),
                            harness_for=lambda _chat=None: harness,
                            spec_for=harness_spec_resolver(prefs, chats),
                            chats=chats,
                            cap_for=lambda: prefs["harness_agents_max"])
    s = attach_session(hub)
    for _ in range(2):
        fake_openai.verdict(route="converse")

    for i in range(2):
        await hub_turn(hub, s, turn=1200 + i, text=f"question {i}")

    assert not audio_frames(s), "the turns were not actually muted"
    messages = chats.current()["messages"]
    roles = [m["role"] for m in messages]
    assert roles.count("user") == 2 and roles.count("assistant") >= 2, (
        f"a muted conversation did not reach the record: {messages} "
        f"(AC-110, AC-35)")


@pytest.mark.asyncio
async def test_barge_in_on_a_muted_reply_leaves_three_ordinary_turns(
        fake_openai, tmp_path):
    """AC-110's terminal path under the one thing that ends a turn early.

    Muting removes the audio, which removes the playback gate and the
    synthesis abort with it -- so a muted turn reaches `abandon()` holding
    less per-turn state than a spoken one, and there is no audio boundary for
    a barge-in to land on. Three ordinary turns AFTER the interruption,
    because the defect this suite exists for is a microphone that comes back
    once and then latches (D-1, AC-2).

    The endpoint is slowed per chunk so the barge-in genuinely lands while
    the brain is still generating; without that the muted reply completes
    before `abandon()` is reached and the test proves nothing.
    """
    hub, s = muted_hub(tmp_path, fake_openai, speak=False)
    fake_openai.chunk_delay_s = 0.05
    fake_openai.verdict(route="converse")
    fake_openai.answer("Cut here. And then a long tail that never reaches the "
                       "owner because the wake word landed first.")

    s.turn = 500
    s.state = hub_mod.THINKING
    s.reply_task = asyncio.create_task(hub._reply(s, 500, "the interrupted one"))
    await asyncio.sleep(0.45)
    await asyncio.wait_for(s.abandon(), timeout=10)

    assert s.state == hub_mod.IDLE and s.turn is None, (
        "a barge-in on a muted reply did not return the session to idle")
    # The instrument itself: the interruption has to land INSIDE the muted
    # reply, not before it starts. Without this the test passes against a
    # barge-in that arrived while the brain was still generating -- which
    # never exercises `_speak` at all (D-13, and test/AGENTS.md's rule about
    # a fixture that only rules out one wrong answer).
    assert replies(s), (
        "the barge-in landed before the muted reply began, so nothing about "
        "muted speech was interrupted")
    assert not any("wake word" in r for r in replies(s)), (
        f"the whole answer was delivered, so nothing was cut off: "
        f"{replies(s)}")
    assert list(hub.history)[-1] == ("assistant", "Cut here."), (
        f"the delivered half of an interrupted muted reply is not in the "
        f"transcript: {list(hub.history)} (AC-64, D-20)")

    fake_openai.chunk_delay_s = 0.0
    for i in range(3):
        fake_openai.verdict(route="converse")
        fake_openai.answer(f"ordinary answer {i}")
    for i in range(3):
        await hub_turn(hub, s, turn=510 + i, text=f"ordinary question {i}")
        assert s.state == hub_mod.IDLE and s.turn is None, (
            f"turn {i} after an interrupted muted turn did not re-arm (D-1)")
        assert f"ordinary answer {i}" in replies(s), (
            f"turn {i} after an interrupted muted turn went unanswered: "
            f"{replies(s)}")
    assert not audio_frames(s), "the conversation was not actually muted"


# --------------------------------------------------------------------------- #
# the ambient back-fill across a conversation (AC-69, AC-70, docs/ambient.md)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_back_fill_belongs_to_ONE_turn_and_never_reaches_a_later_one(
        tmp_path):
    """Per-turn state keyed by turn id, which is this file's oldest subject.

    The ambient clip is exactly the shape that has leaked here before: it is
    stored by one request, read by another, and named by a turn id. One turn
    proves the decode works; only a CONVERSATION proves the clip does not
    survive into the next question. Four turns, and the room is overheard in
    turn 2 only -- so turns 3 and 4 are the assertion, and turn 1 shows the
    field starts empty rather than being emptied.

    AC-70 rides along at the end and it is the reason the leak matters: a
    remark made to somebody else in the room reaching a later turn is that
    remark being handed to a coding agent as the background for a different
    instruction.
    """
    from test_ambient import (OVERHEARD, RecordingAsr, ambient_hub,   # noqa
                              run_turn, store_backfill)

    hub, s = ambient_hub(tmp_path)
    heard = []
    for turn in range(1, TURNS + 1):
        s.turn = turn
        if turn == 2:
            store_backfill(hub, s, turn)
        await run_turn(hub, s, turn, text=f"question number {turn}")
        assert s.state == hub_mod.IDLE and s.turn is None, (
            f"turn {turn} did not re-arm (D-1, AC-2)")
        heard.append(hub.brain.turns[-1][1].overheard)

    assert heard == ["", OVERHEARD, "", ""], (
        f"the back-fill did not belong to exactly one turn: {heard}")
    assert len(hub.asr.clips) == 1, (
        f"the clip was decoded {len(hub.asr.clips)} times across four turns; "
        f"a stored clip that survives its turn is decoded again and charged "
        f"to a question it has nothing to do with")
    assert s.ambient == b"" and s.ambient_turn is None, (
        "the conversation ended with a half-minute of the room still held")
    recorded = " ".join(said for _role, said in hub.history)
    assert OVERHEARD not in recorded, (
        f"what the room said entered the rolling transcript, so every later "
        f"turn is routed as though the owner had said it (AC-70): "
        f"{list(hub.history)!r}")
