"""Ambient: the last N seconds of the room, decoded once and kept apart.

`docs/ambient.md`. The satellite holds a ring of PCM and POSTs it at
capture_start; the daemon stores it against that turn, decodes it after the
live final and before the brain, and hands the text to the turn as its OWN
field.

Three claims are what this file exists for, and only the third of them is a
safety property.

**AC-68 -- off is off.** The window ships at 0 and 0 means no work at all:
nothing stored, nothing decoded, and a turn byte-identical to the one before
ambient existed. A feature that costs something while switched off is a
feature the owner turns off by uninstalling it.

**AC-69 -- the back-fill is QUEUED, never concurrent.** Two `Session`s on one
`transcribe_cpp.Model` return empty transcripts on both sides, 9 times out of
9, silently (`test/manual/spike_ambient_concurrent.py`). So the decode waits
for the live final and the turn pays about 2 s for a 30 s window. There is
deliberately no "drop it if the brain got there first" branch to test: with a
queue the back-fill is always ready before the brain, and a branch that can
never run is not a rule.

**AC-70 -- what was OVERHEARD is not what was SAID TO Butler.** A remark made
to another person in the room must never be recorded as the owner's
instruction or reach a coding agent as one. The assertions here are therefore
negative ones -- the overheard text is NOT in `hub.history`, NOT in the
utterance handed to the brain -- and a positive test that only checked the
field arrived would pass against a daemon that also merged it into both.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, attach_session, silence_pcm,      # noqa: E402
                      tone_pcm)
from daemon import butlerd, hub as hub_mod, ports                 # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                      # noqa: E402
from daemon.modes import ModeStore                                # noqa: E402
from daemon.ports import Done, Final, Say, TurnContext            # noqa: E402
from daemon.settings import BY_KEY, Settings                      # noqa: E402

TURN_TIMEOUT_S = 20.0

# What the room was overheard saying, and what the owner then said to Butler.
# Deliberately share no words: every AC-70 assertion below is a substring
# search, and an overlap would make "the overheard text leaked into the
# history" and "the utterance is in the history" the same observation.
OVERHEARD = "tell margaret the shipment slipped to friday"
UTTERANCE = "send that"


class RecordingBrain:
    """A `Brain` that keeps the whole `TurnContext` it was handed.

    `brain_fake` records `(text, turn_id)`, which cannot answer either half of
    AC-70: the question is what ELSE was in the context and what was NOT.
    """

    def __init__(self):
        self.turns: list[tuple[str, TurnContext]] = []

    async def turn(self, text, ctx):
        self.turns.append((text, ctx))
        yield Say("Sent.", tier="reflex")
        yield Done("reflex")

    async def abort(self, turn_id) -> None:
        return None

    async def warm(self) -> None:
        return None

    @property
    def info(self) -> dict:
        return {"engine": "recording"}


class RecordingAsr(FakeAsr):
    """The fake recognizer, plus a record of every whole-clip decode.

    Counting the calls is the only way to assert "off means NO WORK": a
    daemon that decoded an empty clip and threw the empty string away would
    produce identical output and pay the thread hop on every turn.
    """

    def __init__(self, transcript: str = OVERHEARD, fail: bool = False):
        super().__init__()
        self.clips: list[bytes] = []
        self._transcript = transcript
        self._fail = fail

    async def transcribe(self, pcm: bytes, cfg) -> str:
        self.clips.append(pcm)
        if self._fail:
            raise RuntimeError("the decoder fell over")
        return self._transcript


def ambient_hub(tmp_path, asr=None, brain=None):
    """A Hub on a settings store of its own, with ambient switched ON.

    Its own `Settings` because the suite must never write the owner's
    `state/settings.json` (test/AGENTS.md), and ambient is a setting the hub
    reads per turn.
    """
    settings = Settings(tmp_path / "settings.json")
    settings.set("ambient_seconds", 30)
    hub = hub_mod.Hub(asr=asr if asr is not None else RecordingAsr(),
                      tts=QuietTts(),
                      brain=brain if brain is not None else RecordingBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=settings)
    return hub, attach_session(hub)


def store_backfill(hub, s, turn: int, pcm: bytes | None = None) -> None:
    """What `capture_start` plus the page's POST leave behind, in that order.

    The turn is set FIRST because that is the production sequence and the
    daemon depends on it: the daemon mints the id and announces it, and only
    then can the page name a turn in its request (D-6).
    """
    s.turn = turn
    assert hub.take_ambient(turn, tone_pcm(400) if pcm is None else pcm), (
        "the clip was refused for the turn the session owns")


async def run_turn(hub, s, turn: int, text: str = UTTERANCE) -> None:
    """One turn from the final onwards, the way `_on_final` reaches it."""
    s.turn = turn
    s.state = hub_mod.CAPTURING
    await asyncio.wait_for(hub._on_final(s, turn, Final(text=text), "silence"),
                           TURN_TIMEOUT_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), TURN_TIMEOUT_S)


# --------------------------------------------------------------------------- #
# AC-68 -- the setting
# --------------------------------------------------------------------------- #
def test_the_window_is_a_number_of_seconds_and_ships_off():
    """AC-68, and the key the satellite already reads.

    `web/app.js` reads `values.ambient_seconds` and treats 0 as off; that name
    was the web agent's guess, and a mismatch here makes the whole feature
    silently inert on both sides -- the page keeps no ring, the daemon is
    never POSTed to, and nothing anywhere reports a fault.
    """
    spec = BY_KEY.get("ambient_seconds")
    assert spec is not None, (
        "the page reads values.ambient_seconds; without it in the schema the "
        "window is never served and the feature is inert (AC-68)")
    assert spec.default == 0, (
        f"ambient ships OFF: a room recorded by default is not a default "
        f"(AC-68, AC-72), got {spec.default!r}")
    assert spec.group == "listen" and spec.kind == "number"
    assert spec.restart is False, (
        "the window is read per turn, so flagging it restart would tell the "
        "owner to reboot for a setting that already took effect (AC-7b)")
    assert spec.minimum == 0, "0 is how the control says off"
    assert spec.maximum is not None and 30 <= spec.maximum <= 120, (
        f"the ceiling is what one decode can pay for: 30 s costs about 2 s "
        f"and 240 s about 16 s [MEASURED], got {spec.maximum!r}")
    assert "2" in spec.help and "4" in spec.help, (
        f"the help text carries the MEASURED cost so the owner chooses with "
        f"the number in front of them (AC-68): {spec.help!r}")


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
async def post_ambient(hub, turn, body: bytes, monkeypatch):
    """POST one clip at the real route, through the real app."""
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        return await client.post(
            f"/api/ambient?turn={turn}", data=body,
            headers={"Content-Type": "application/octet-stream"})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_clip_is_stored_against_the_turn_the_session_owns(
        tmp_path, monkeypatch):
    """The happy path: 202, and the bytes are held for that turn."""
    hub, s = ambient_hub(tmp_path)
    s.turn = 7
    s.state = hub_mod.CAPTURING
    clip = tone_pcm(400)

    answer = await post_ambient(hub, 7, clip, monkeypatch)

    assert answer.status == 202, (
        f"the page fires this without awaiting it; anything but an accept "
        f"is a red line on a page mid-turn: {answer.status}")
    assert s.ambient == clip, (
        "the clip was accepted and dropped, so every ambient turn would run "
        "with no context and nothing would say why")


@pytest.mark.asyncio
async def test_a_clip_for_a_turn_the_session_has_moved_past_is_discarded(
        tmp_path, monkeypatch):
    """D-6, and this is the one with teeth.

    A back-fill is fired without await, so it races the turn it belongs to. If
    the daemon stored it by the turn it was TOLD rather than by the turn the
    session owns, a clip of the room from an abandoned turn would arrive as
    the next turn's context -- audio the owner has already moved on from,
    handed to a coding agent as the background for a different instruction.
    """
    hub, s = ambient_hub(tmp_path)
    s.turn = 9                       # the session has moved on to turn 9
    s.state = hub_mod.CAPTURING

    answer = await post_ambient(hub, 8, tone_pcm(400), monkeypatch)

    assert answer.status == 202, (
        f"a late back-fill is not the page's fault and must not read as an "
        f"error: {answer.status}")
    assert s.ambient == b"", (
        "a clip for an abandoned turn was stored against the turn the owner "
        "is now in (D-6)")


@pytest.mark.asyncio
async def test_a_body_bigger_than_the_biggest_window_is_refused(
        tmp_path, monkeypatch):
    """The daemon holds no more of a room than it would ever decode.

    30 s is 960 KB and the schema's ceiling is what bounds this. Without a
    limit a client bug -- or a body from anywhere on the tailnet -- makes the
    daemon hold arbitrary memory, and the microphone is what pays for it
    (D-1).
    """
    hub, s = ambient_hub(tmp_path)
    s.turn = 3
    s.state = hub_mod.CAPTURING
    over = b"\x01\x02" * (butlerd.AMBIENT_MAX_BYTES // 2 + 1024)

    answer = await post_ambient(hub, 3, over, monkeypatch)

    assert answer.status == 413, (
        f"a body past the ceiling was accepted: {answer.status}")
    assert s.ambient == b"", "the oversized body was stored anyway"


@pytest.mark.asyncio
async def test_nothing_is_stored_while_the_window_is_off(tmp_path, monkeypatch):
    """AC-68: off is off on the DAEMON side too.

    The page keeps no ring while the window is 0, so this only fires for a
    stale tab -- which is exactly the case where "off" has to mean something
    the daemon enforces rather than something a client remembers.
    """
    hub, s = ambient_hub(tmp_path)
    hub.settings.set("ambient_seconds", 0)
    s.turn = 4
    s.state = hub_mod.CAPTURING

    answer = await post_ambient(hub, 4, tone_pcm(400), monkeypatch)

    assert answer.status == 202
    assert s.ambient == b"", (
        "a clip was held with ambient switched off, so a room is being kept "
        "by a daemon the owner told not to (AC-68, AC-72)")


# --------------------------------------------------------------------------- #
# the turn wiring -- AC-69 and AC-70
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_backfill_is_decoded_and_reaches_the_turn_as_its_own_field(
        tmp_path):
    """AC-69 (queued) and the positive half of AC-70.

    Decoded AFTER the final -- the turn only starts here -- and BEFORE the
    brain, which is the whole reason the queue is affordable: nothing is
    dropped, so there is no branch that arrives without context.
    """
    hub, s = ambient_hub(tmp_path)
    store_backfill(hub, s, 1)

    await run_turn(hub, s, 1)

    assert len(hub.asr.clips) == 1, (
        f"the stored clip was never decoded: {len(hub.asr.clips)} decodes")
    text, ctx = hub.brain.turns[0]
    assert ctx.overheard == OVERHEARD, (
        f"the back-fill did not reach the turn context: {ctx.overheard!r}")
    assert text == UTTERANCE, (
        f"the brain was prompted with something other than what the owner "
        f"said: {text!r}")


@pytest.mark.asyncio
async def test_what_was_overheard_never_becomes_what_was_said(tmp_path):
    """AC-70, and it is the criterion this whole feature is gated on.

    Three places the room's words must not appear: the utterance handed to
    the brain, the history handed to the brain, and the hub's own rolling
    transcript afterwards. The last one is what a coding agent and the router
    both read on the NEXT turn, so a leak there outlives the turn that caused
    it.
    """
    hub, s = ambient_hub(tmp_path)
    store_backfill(hub, s, 1)

    await run_turn(hub, s, 1)

    text, ctx = hub.brain.turns[0]
    assert OVERHEARD not in text, (
        f"the room's words were merged into the utterance, so a remark made "
        f"to somebody else is now an instruction: {text!r}")
    joined = " ".join(said for _role, said in ctx.history)
    assert OVERHEARD not in joined, (
        f"the room's words were handed over as conversation history: "
        f"{ctx.history!r}")
    recorded = " ".join(said for _role, said in hub.history)
    assert OVERHEARD not in recorded, (
        f"the room's words entered the rolling transcript, so the NEXT turn "
        f"is routed as though the owner had said them (AC-70, D-20): "
        f"{list(hub.history)!r}")
    assert ("user", UTTERANCE) in list(hub.history), (
        f"the owner's own words are what the record keeps: "
        f"{list(hub.history)!r}")


@pytest.mark.asyncio
async def test_a_backfill_that_will_not_decode_costs_the_context_not_the_turn(
        tmp_path):
    """D-1. A failure here loses context; it must never lose the turn."""
    hub, s = ambient_hub(tmp_path, asr=RecordingAsr(fail=True))
    store_backfill(hub, s, 1)

    await run_turn(hub, s, 1)

    assert hub.brain.turns, "a failed back-fill took the whole turn with it"
    _text, ctx = hub.brain.turns[0]
    assert ctx.overheard == "", (
        f"a failed decode left something in the field: {ctx.overheard!r}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the turn did not re-arm after a failed back-fill (D-1, AC-2)")


@pytest.mark.asyncio
async def test_with_ambient_off_the_turn_does_no_ambient_work_at_all(tmp_path):
    """AC-68: off costs nothing, and "nothing" is measured in decodes.

    Asserted on the decode COUNT rather than on the field, because a daemon
    that decoded an empty clip every turn and discarded the empty string
    would pass every other assertion in this file while paying a thread hop
    per turn.
    """
    hub, s = ambient_hub(tmp_path)
    hub.settings.set("ambient_seconds", 0)

    await run_turn(hub, s, 1)

    assert hub.asr.clips == [], (
        f"ambient is off and the daemon decoded something anyway: "
        f"{len(hub.asr.clips)} clips")
    _text, ctx = hub.brain.turns[0]
    assert ctx.overheard == "", (
        f"a turn with ambient off carried an overheard field: "
        f"{ctx.overheard!r}")


@pytest.mark.asyncio
async def test_a_clip_whose_own_turn_never_used_it_is_not_inherited(tmp_path):
    """AC-70 by the route a leak actually takes, and the keyed read is why.

    A turn that ends on an EMPTY final never reaches `_reply` at all (AC-6's
    silent re-arm), so the clip stored for it is never read. The next turn
    must not find it lying there: half a minute of a room from before a wake
    the owner abandoned is context for a question nobody asked, and the brain
    that would be handed it is a coding agent.

    `_start_capture` also clears the pair, so in production two things stop
    this. That is exactly why the clip is keyed by TURN as well: a test that
    only ever drove the capture path would pass against a daemon whose clip
    is not keyed at all.
    """
    hub, s = ambient_hub(tmp_path)
    store_backfill(hub, s, 1)

    await run_turn(hub, s, 1, text="")           # a quiet room: AC-6, no reply
    assert hub.brain.turns == [], "an empty final reached the brain (AC-6)"

    await run_turn(hub, s, 2, text=UTTERANCE)

    _text, ctx = hub.brain.turns[0]
    assert ctx.overheard == "", (
        f"turn 2 was handed the clip stored for turn 1: {ctx.overheard!r}")
    assert hub.asr.clips == [], (
        f"a clip nobody asked for was decoded a turn late: "
        f"{len(hub.asr.clips)} decodes")


@pytest.mark.asyncio
async def test_a_silent_room_decodes_to_nothing_and_says_nothing(tmp_path):
    """The normal case for a buffer of a room: it is quiet.

    The fake refuses to transcribe silence for the same reason the real
    adapter returns "" for it (daemon/adapters/AGENTS.md), so this is the one
    ambient test that runs the REAL decode path of the fake rather than a
    scripted answer.
    """
    hub, s = ambient_hub(tmp_path, asr=FakeAsr())
    store_backfill(hub, s, 1, silence_pcm(400))

    await run_turn(hub, s, 1)

    _text, ctx = hub.brain.turns[0]
    assert ctx.overheard == "", (
        f"silence was transcribed into context: {ctx.overheard!r}")
