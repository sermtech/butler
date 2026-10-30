"""Typing is a peer of speaking. AC-162, AC-163, D-1, D-6, D-18, D-20.

Protocol v1 gains ONE inbound message and nothing downstream changes:

    satellite -> daemon   {"type": "typed", "text": "<what was typed>"}

`docs/familiar-ui.md` step 2, and the three rulings in its section 2 that
this file exists to hold in place:

* **R6** -- a typed turn obeys the EXISTING `speak` setting. There is no
  typed-is-silent rule, so the parametrized case below runs the same
  conversation in both columns and the ONLY difference allowed is audio.
* **R7** -- a typed message cancels a capture in flight, through the path
  barge-in already takes, so the abandoned capture re-arms (D-1).
* **AC-163** -- the DAEMON mints the id. A `turn` in the body is ignored,
  because a satellite naming a turn it does not own is D-6.

Asserted on the CALLER wherever there is a choice (D-18): a `_typed` that
built a perfectly good Final and never reached `_on_final` would satisfy
every assertion made about `_on_final` itself, and this project's three
worst defects were all green at the callee.
"""
import asyncio
import json
import time

import pytest

from conftest import (QuietTts, attach_session, make_hub, silence_pcm,
                      tone_pcm)
from daemon import hub as hub_mod
from daemon import ports
from daemon.hub import (CAPTURING, IDLE, TYPED_CHARS_PER_SECOND,
                        TYPED_MAX_CHARS)
from daemon.settings import BY_KEY, Settings

TURNS = 4

# The fake brain's answer to this one, so a test can tell "the turn ran" from
# "some frames were sent" (daemon/adapters/brain_fake.py).
ASKED = "what time is it"
ANSWER = "It is twenty past four."


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def frames(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent if isinstance(m, str)]


def frames_of(s, kind: str) -> list[dict]:
    return [m for m in frames(s) if m.get("type") == kind]


def replies(s) -> list[str]:
    return [m["text"] for m in frames_of(s, "reply")]


def audio_frames(s) -> list:
    return [m for m in s.ws.sent if isinstance(m, bytes)]


async def type_at(hub, s, text, **extra):
    """Drive one typed turn through `_on_text` and wait for it to finish.

    Through `_on_text` rather than by calling the handler, for the reason
    `test_stop.py` gives about its own message: the message SHAPE is half the
    contract, and a daemon that implemented this under another name would
    pass a handler-level test while answering the page "unsupported message
    type in protocol v1".

    The satellite's half is played too -- reporting the speaker quiet -- or
    every turn here waits out the playback gate for two seconds.
    """
    before = s.task

    async def confirm():
        for _ in range(1000):
            if s.audio_ms_sent > 0 and s.turn is not None:
                await hub._playback_done(s, {"turn": s.turn})
                return
            await asyncio.sleep(0.005)

    helper = asyncio.create_task(confirm())
    try:
        await hub._on_text(s, json.dumps({"type": "typed", "text": text,
                                          **extra}))
        if s.task is not None and s.task is not before:
            await asyncio.wait_for(s.task, timeout=20)
        # The reply runs in its own task, which `_on_final` starts and does
        # not await -- so waiting only on `s.task` would assert against a turn
        # that has not answered yet.
        if s.reply_task is not None:
            await asyncio.wait_for(asyncio.shield(s.reply_task), timeout=20)
    finally:
        helper.cancel()


async def speak_at(hub, s, text):
    """The same turn, arriving the way a recognizer delivers one."""
    turn = hub._begin_turn(s)
    s.state = CAPTURING

    async def confirm():
        for _ in range(1000):
            if s.audio_ms_sent > 0 and s.turn is not None:
                await hub._playback_done(s, {"turn": s.turn})
                return
            await asyncio.sleep(0.005)

    helper = asyncio.create_task(confirm())
    try:
        await asyncio.wait_for(
            hub._on_final(s, turn, ports.Final(text=text), "silence"),
            timeout=20)
        if s.reply_task is not None:
            await asyncio.wait_for(asyncio.shield(s.reply_task), timeout=20)
    finally:
        helper.cancel()


# --------------------------------------------------------------------------- #
# AC-162: the same turn, arriving by a different door
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_typed_turn_and_a_spoken_one_produce_the_same_record(tmp_path):
    """AC-162, and it is the whole criterion: same router, same brain, same
    record.

    Two hubs rather than two turns of one, so the comparison is not confused
    by the rolling transcript already holding the first question when the
    second is asked. What must match: the rolling transcript (AC-64, D-20),
    what the brain was asked, what reached the page, and the ORDER of the
    frames -- a daemon that answered a typed turn correctly but never sent
    `speak_end` would leave the page thinking forever and pass every
    content assertion (D-1).

    The one difference allowed is the `final` frame's `reason`, which is the
    field that exists to say how a turn ended.

    On the REAL `tts_fake` rather than `QuietTts`, because the assistant half
    of the record is `s.spoken`, which is filled only by audio that actually
    reached the satellite -- with a silent double both columns record the
    question and neither records an answer, and the comparison passes while
    proving nothing (D-13).
    """
    typed_hub = make_hub(tmp_path / "typed")
    typed = attach_session(typed_hub)

    spoken_hub = make_hub(tmp_path / "spoken")
    spoken = attach_session(spoken_hub)

    await type_at(typed_hub, typed, ASKED)
    await speak_at(spoken_hub, spoken, ASKED)

    assert list(typed_hub.history) == list(spoken_hub.history), (
        f"a typed turn was recorded differently from a spoken one, so the "
        f"next turn's router sees a different conversation (AC-64, D-20): "
        f"{list(typed_hub.history)} vs {list(spoken_hub.history)}")
    assert list(typed_hub.history) == [("user", ASKED), ("assistant", ANSWER)], (
        f"the typed turn did not reach the transcript as a question and an "
        f"answer at all: {list(typed_hub.history)}")

    assert [t for t, _ in typed_hub.brain.seen] == [ASKED], (
        f"the brain was not asked what was typed: {typed_hub.brain.seen}")
    assert replies(typed) == replies(spoken) == [ANSWER], (
        f"the page was told something different: {replies(typed)} vs "
        f"{replies(spoken)}")

    def shape(session):
        out = []
        for m in frames(session):
            m = dict(m)
            # The heartbeat is CLOCK-driven (docs/turn-feedback.md, AC-389):
            # how many beats a turn gets and the milliseconds in them are
            # timings, not the shape of the turn -- both doors send it.
            if m.get("type") == "progress":
                continue
            m.pop("turn", None)          # minted per hub; compared separately
            if m.get("type") == "final":
                m.pop("reason", None)    # the one field that MUST differ
            out.append(m)
        return out

    assert shape(typed) == shape(spoken), (
        f"the wire differs beyond the way the turn arrived:\n"
        f"typed:  {shape(typed)}\nspoken: {shape(spoken)}")
    assert frames_of(typed, "final")[0]["reason"] == "typed", (
        f"a typed turn's final does not say how it arrived: "
        f"{frames_of(typed, 'final')}")
    assert typed.state == IDLE and typed.turn is None, (
        f"the typed turn did not re-arm: state={typed.state!r} "
        f"turn={typed.turn!r} (D-1, AC-2)")


@pytest.mark.asyncio
async def test_a_typed_turn_gets_no_capture_start(tmp_path):
    """Nothing is being captured, so nothing says a capture started (AC-7b).

    `capture_start` means "you won arbitration, start streaming the
    microphone". Sending it for a turn with no pipeline behind it would put
    the page into a capture the daemon is not in, and the audio it then
    uploaded would be fed to nothing.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await type_at(hub, s, ASKED)

    kinds = s.ws.text_types()
    assert "capture_start" not in kinds, (
        f"a typed turn told the page to start capturing: {kinds}")
    assert kinds[0] == "final", (
        f"the `final` is the frame that names a typed turn, and it must be "
        f"the first the page sees: {kinds}")


# --------------------------------------------------------------------------- #
# AC-163: the daemon mints the id
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_daemon_mints_the_turn_id_and_ignores_the_pages(tmp_path):
    """AC-163, D-6: a satellite may not name a turn it does not own.

    The teeth are in the id being one the daemon would have minted ANYWAY --
    a test that only checked "not 9999" would pass against a daemon that used
    the page's number plus one.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)
    hub._turn_seq = 40

    await type_at(hub, s, ASKED, turn=9999, turn_id=9999, id=9999)

    minted = [m["turn"] for m in frames(s) if "turn" in m]
    assert minted, f"no frame carried a turn at all: {frames(s)}"
    assert set(minted) == {41}, (
        f"the daemon did not mint this turn: it sent {sorted(set(minted))} "
        f"where its own sequence says 41 (AC-163, D-6)")
    assert hub.brain.seen == [(ASKED, 41)], (
        f"the brain was handed a turn id the page chose: {hub.brain.seen}")


# --------------------------------------------------------------------------- #
# fail fast: an empty message is not a turn, and a huge one is refused
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", [
    {"text": ""},
    {"text": "   "},
    {"text": "\n\t  \r\n"},
    {},                    # no text at all
    {"text": None},
    {"text": 42},
    {"text": ["what", "time"]},
])
@pytest.mark.asyncio
async def test_a_typed_message_with_no_text_is_refused(tmp_path, body):
    """Fail fast: refused, never turned into an empty turn.

    An empty final is legal from a RECOGNIZER -- a quiet room is a real
    event (AC-6) -- and it is not legal from a keyboard: nobody pressed Enter
    on nothing by accident, and minting a turn for it would burn an id, wake
    the brain and put a blank question in the record.

    The refusal is asserted by its CONSEQUENCES and not by the error frame:
    a daemon that complained and started the turn anyway satisfies every
    status-shaped check that could be written here (test_shortcuts.py's rule).
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    await hub._on_text(s, json.dumps({"type": "typed", **body}))
    await asyncio.sleep(0)

    assert hub._turn_seq == 0, (
        f"an empty typed message minted turn {hub._turn_seq}")
    assert hub.brain.seen == [], f"the brain was woken: {hub.brain.seen}"
    assert s.turn is None and s.state == IDLE, (
        f"the session was left holding a turn: turn={s.turn!r} "
        f"state={s.state!r} (D-1)")
    assert list(hub.history) == [], (
        f"an empty question reached the record: {list(hub.history)}")
    assert [m["type"] for m in frames(s)] == ["error"], (
        f"an empty typed message was not refused out loud: {frames(s)}")


@pytest.mark.asyncio
async def test_the_ceiling_refuses_and_the_character_below_it_does_not(tmp_path):
    """The bound holds, in BOTH directions.

    One direction alone is not an instrument: a daemon that refused every
    typed message would pass the refusal half, and one with no ceiling at all
    would pass the acceptance half.

    WHAT THIS CANNOT CATCH, said rather than left to be discovered: a change
    to the ceiling's VALUE. `TYPED_MAX_CHARS` is derived from
    `dictate_max_seconds`'s own maximum, and this reads the derivation
    instead of keeping a second copy of the number -- so raising the rate
    moves the test's own expectation with it. MEASURED by mutation on
    2026-09-10: multiplying `TYPED_CHARS_PER_SECOND` by a thousand left this
    green. That is the deliberate trade behind the derivation, and the
    defect worth catching is not a policy change but a MISSING CHECK, which
    is what the mutation harness aims at. The derivation itself is pinned
    below, so replacing it with a literal reddens.

    Refused and never TRUNCATED: half a question answered as though it were
    whole is the failure the owner cannot see.
    """
    assert TYPED_MAX_CHARS == (
        int(BY_KEY["dictate_max_seconds"].maximum) * TYPED_CHARS_PER_SECOND), (
        f"the typed ceiling stopped being derived from the dictation "
        f"ceiling, so the two now drift independently (D-12): "
        f"{TYPED_MAX_CHARS}")
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    too_long = "a" * (TYPED_MAX_CHARS + 1)
    await hub._on_text(s, json.dumps({"type": "typed", "text": too_long}))
    await asyncio.sleep(0)

    assert hub._turn_seq == 0 and hub.brain.seen == [], (
        f"text past the ceiling started a turn: seq={hub._turn_seq} "
        f"brain={hub.brain.seen}")
    errors = frames_of(s, "error")
    assert len(errors) == 1 and str(TYPED_MAX_CHARS) in errors[0]["message"], (
        f"the refusal does not say what the ceiling is: {errors}")

    at_the_line = "b" * TYPED_MAX_CHARS
    await type_at(hub, s, at_the_line)

    assert [t for t, _ in hub.brain.seen] == [at_the_line], (
        f"the longest allowed message was refused too, so the case above "
        f"proves nothing: {hub.brain.seen}")
    assert not any(t == "user" and len(t2) != TYPED_MAX_CHARS
                   for t, t2 in hub.history), (
        f"the accepted message was truncated on its way to the record: "
        f"{[(r, len(t)) for r, t in hub.history]}")


# --------------------------------------------------------------------------- #
# R7: a typed message cancels a capture in flight
# --------------------------------------------------------------------------- #
class RecordingPipeline:
    """A `TurnPipeline` stand-in that records the one thing R7 is about."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def uses_recognizer(self):
        return False

    def feed(self, data):
        pass


@pytest.mark.asyncio
async def test_a_typed_message_abandons_a_capture_and_the_session_rearms(
        tmp_path):
    """R7, D-1: the capture is abandoned through the EXISTING terminal path.

    Asserted on the caller (D-18). `abandon()`'s own behaviour has been
    proven since barge-in was written; what is new is whether `_typed` calls
    it -- and a `_typed` that reset the session by hand would look identical
    from every angle except the one that matters: the pipeline would be left
    open, still holding the recognizer, and the microphone would come back
    once and then latch.

    The abandoned capture must also produce NO final: it was abandoned, not
    ended, and a final for it would answer an utterance the owner replaced.

    THE PIPELINE'S `close()` IS THE ASSERTION WITH TEETH, and it took a
    mutation to find that out. A first version asserted `s.pipeline is None`
    -- which the typed turn's own `_rearm` sets at the end regardless, so
    deleting the barge-in left this test GREEN while the recognizer stayed
    open under the whole reply (D-13, 2026-09-10). Only `abandon()` closes
    it, and only closing it releases the recognizer.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    captured = hub._begin_turn(s)
    s.state = CAPTURING
    pipeline = RecordingPipeline()
    s.pipeline = pipeline

    await type_at(hub, s, ASKED)

    assert pipeline.closed, (
        "the abandoned capture's pipeline was never closed, so the typed "
        "turn reset the session by hand instead of going through abandon() "
        "-- the recognizer is still open on an utterance nobody will hear "
        "(R7, D-18, AC-116)")
    assert s.pipeline is None, (
        "the capture's pipeline is still attached after the typed turn "
        "ended (D-1)")
    assert hub.brain.seen == [(ASKED, captured + 1)], (
        f"the typed turn did not get a NEW id after the capture it replaced: "
        f"{hub.brain.seen} (captured turn was {captured})")
    assert not [m for m in frames(s) if m.get("turn") == captured], (
        f"the abandoned capture produced wire traffic of its own: "
        f"{[m for m in frames(s) if m.get('turn') == captured]} (D-6)")
    assert s.state == IDLE and s.turn is None, (
        f"the session did not re-arm after the typed turn: "
        f"state={s.state!r} turn={s.turn!r} (D-1, AC-2)")
    assert replies(s) == [ANSWER], (
        f"the typed question was not answered: {replies(s)}")


@pytest.mark.asyncio
async def test_a_refused_typed_message_costs_no_capture(tmp_path):
    """The refusals above are checked BEFORE the barge-in, and that matters.

    A message Butler will not act on must not destroy a capture the owner is
    half way through -- otherwise a stray Enter in an empty composer takes
    the sentence they were saying.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    captured = hub._begin_turn(s)
    s.state = CAPTURING
    s.pipeline = RecordingPipeline()

    await hub._on_text(s, json.dumps({"type": "typed", "text": "   "}))
    await asyncio.sleep(0)

    assert s.turn == captured and s.state == CAPTURING, (
        f"an empty typed message ended the capture in flight: "
        f"turn={s.turn!r} state={s.state!r}")
    assert s.pipeline is not None and not s.pipeline.closed, (
        "an empty typed message tore down the capture's pipeline")


# --------------------------------------------------------------------------- #
# R6: the `speak` setting governs a typed turn exactly as it governs a spoken
# one -- and no more than it does
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("speak", [True, False])
@pytest.mark.asyncio
async def test_the_speak_setting_governs_a_typed_turn(tmp_path, speak):
    """R6, AC-110: no new speech rule for typed turns.

    Both columns run because "typed turns are silent" and "the setting is
    obeyed" are indistinguishable with `speak` off. With it ON a typed turn
    must produce audio, which is the assertion that actually catches the
    branch this ruling forbids; with it OFF it must produce none and still
    answer, bracket itself and reach the record (AC-110).

    Four turns, because muting is a terminal path and one turn cannot tell
    "the turn ended" from "the turn ended once and then latched" (D-1, AC-2).
    """
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("speak", speak)
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)

    for i in range(TURNS):
        await type_at(hub, s, ASKED)
        assert s.state == IDLE and s.turn is None, (
            f"typed turn {i} did not re-arm with speak={speak} (D-1, AC-2)")

    kinds = s.ws.text_types()
    assert kinds.count("speak_start") == TURNS, (
        f"{kinds.count('speak_start')} speak_start frames for {TURNS} typed "
        f"turns with speak={speak}: the page leaves its thinking state on "
        f"this bracket (AC-110, D-1)")
    assert kinds.count("speak_end") == TURNS, (
        f"{kinds.count('speak_end')} speak_end frames for {TURNS} typed "
        f"turns with speak={speak}: {kinds}")
    assert replies(s) == [ANSWER] * TURNS, (
        f"muting a typed turn dropped the answer: {replies(s)}")
    assert [t for r, t in hub.history if r == "assistant"] == [ANSWER] * 3, (
        f"a typed turn's answer did not reach the rolling transcript with "
        f"speak={speak}: {list(hub.history)} (AC-64, D-20)")

    if speak:
        assert audio_frames(s), (
            "a typed turn produced NO audio with speak=True, so the muted "
            "column proves nothing -- and R6 says a typed question is "
            "answered out loud exactly when a spoken one is")
    else:
        assert not audio_frames(s), (
            f"{len(audio_frames(s))} audio frames from a typed turn with "
            f"speak=False (AC-110)")


# --------------------------------------------------------------------------- #
# over the wire, where the message shape is the contract
# --------------------------------------------------------------------------- #
async def spoken_turn(sat) -> int:
    """Wake, speak, be answered, report the speaker quiet. As a device does."""
    await sat.send(type="wake_candidate", word="hey_butler", score=0.92,
                   ts_ms=int(time.time() * 1000))
    turn = (await sat.next("capture_start", timeout=10.0))["turn"]
    await sat.send_pcm(tone_pcm(600))
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint")
    final = await sat.next("final", timeout=15.0)
    assert final.get("turn") == turn, f"final belongs to {turn}: {final}"
    end = await sat.next("speak_end", timeout=20.0)
    assert end.get("turn") == turn
    await sat.send(type="playback_done", turn=turn)
    return turn


async def typed_turn(sat, text: str, **extra) -> int:
    await sat.send(type="typed", text=text, **extra)
    final = await sat.next("final", timeout=15.0)
    assert (final.get("text") or "") == text, (
        f"the daemon heard something other than what was typed: {final}")
    assert final.get("reason") == "typed", f"{final}"
    turn = final["turn"]
    end = await sat.next("speak_end", timeout=20.0)
    assert end.get("turn") == turn
    await sat.send(type="playback_done", turn=turn)
    return turn


@pytest.mark.asyncio
async def test_the_daemon_answers_typed_and_spoken_turns_on_one_socket(
        satellite):
    """The message is in protocol v1, and the microphone survives it.

    Over a real socket, because the SHAPE is the contract: protocol v1
    answers an unknown type with an `error` frame, so a daemon that
    implemented this under another name fails here and nowhere else.

    Four turns, alternating, and the spoken ones on both sides of the typed
    ones are the assertion with teeth: a typed turn that left the daemon
    unable to hear the next wake word is D-1 in the organ this project has
    paid for most often.
    """
    turns = [await spoken_turn(satellite)]
    turns.append(await typed_turn(satellite, ASKED))
    turns.append(await spoken_turn(satellite))
    turns.append(await typed_turn(satellite, "and now a second typed one"))

    assert not [m for m in satellite.seen if m.get("type") == "error"], (
        f"the daemon refused a typed message: "
        f"{[m for m in satellite.seen if m.get('type') == 'error']}")
    assert len(set(turns)) == TURNS, f"a turn id was reused: {turns}"
    assert turns == sorted(turns), f"turn ids went backwards: {turns}"


@pytest.mark.asyncio
async def test_typing_over_the_wire_cancels_a_capture_and_leaves_it_working(
        satellite):
    """R7 end to end, then two ordinary turns (D-1).

    The abandoned capture must never finalize -- and the `capture_end_hint`
    afterwards is what WOULD produce a final if the typed message had been
    rejected as an unknown type, which is what makes its absence evidence.
    """
    await satellite.send(type="wake_candidate", word="hey_butler", score=0.92,
                         ts_ms=int(time.time() * 1000))
    abandoned = (await satellite.next("capture_start", timeout=10.0))["turn"]
    await satellite.send_pcm(tone_pcm(300))

    typed = await typed_turn(satellite, ASKED)
    assert typed != abandoned, (
        f"the typed turn reused the abandoned capture's id: {typed}")
    assert not [m for m in satellite.seen
                if m.get("type") == "final" and m.get("turn") == abandoned], (
        "the abandoned capture finalized anyway, so the owner is answered "
        "twice for one intent")

    await satellite.send(type="capture_end_hint")
    with pytest.raises(AssertionError):
        await satellite.next("final", timeout=3.0)

    for _ in range(2):
        await spoken_turn(satellite)
    assert not [m for m in satellite.seen if m.get("type") == "error"], (
        f"{[m for m in satellite.seen if m.get('type') == 'error']}")
