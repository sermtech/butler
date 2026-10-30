"""A turn is not over until the speaker is quiet. AC-5a, D-1, D-14.

The daemon writes audio at socket speed and the satellite plays it at
24 kHz, so "I finished sending" arrives long before "you finished playing".
Re-arming on the first of those puts the wake detector back to work while
the device's own speaker is still finishing the reply -- Home Assistant's
open Voice PE bug, where the satellite transcribes its own answer.

Butler cannot lean on echo cancellation to save it here: Chrome has
historically excluded AudioContext output from the canceller's reference
signal, and a phone hearing the desk speaker has no reference signal at all
(.references/audio-internals.md).
"""
import asyncio

import pytest

from conftest import attach_session, make_hub
from daemon.hub import IDLE, SPEAKING


async def confirm_playback_when_asked(hub, s):
    """Play the satellite's part: report playback_done once audio has arrived.

    Every end-to-end reply test needs this, because _reply deliberately will
    not finish until the speaker has gone quiet. Polling for the first byte
    rather than sleeping keeps the tests honest about WHEN the gate opens.
    """
    for _ in range(300):
        if s.audio_ms_sent > 0:
            await hub._playback_done(s, {"turn": s.turn})
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_turn_waits_for_the_speaker_to_go_quiet(tmp_path):
    """The reply path must not finish while audio is still playing."""
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 5
    s.audio_ms_sent = 1500.0

    waiter = asyncio.create_task(hub._await_playback(s, 5))
    await asyncio.sleep(0.05)
    assert not waiter.done(), "the daemon did not wait for playback at all"

    s.playback_done.set()
    await asyncio.wait_for(waiter, timeout=2)


@pytest.mark.asyncio
async def test_a_silent_satellite_still_gets_its_microphone_back(tmp_path):
    """D-1: the wait is bounded.

    A satellite that never reports back -- an older client, a dropped
    message -- must not hold the turn open forever. Waiting slightly too
    long costs a beat of silence; never re-arming costs the microphone.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 6
    s.audio_ms_sent = 10.0        # tiny, so the budget is essentially grace

    await asyncio.wait_for(hub._await_playback(s, 6), timeout=10)


@pytest.mark.asyncio
async def test_nothing_spoken_means_nothing_to_wait_for(tmp_path):
    """An empty or refused reply must not pay the grace period."""
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 7
    s.audio_ms_sent = 0.0

    await asyncio.wait_for(hub._await_playback(s, 7), timeout=0.2)


@pytest.mark.asyncio
async def test_a_stale_playback_report_does_not_release_the_live_turn(tmp_path):
    """D-6 again, on the new message.

    A confirmation for the reply the user just interrupted must not end the
    turn they are in now -- that would re-arm mid-sentence and drop the
    capture in progress.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 9

    await hub._playback_done(s, {"type": "playback_done", "turn": 8})
    assert not s.playback_done.is_set(), (
        "a stale playback_done released the current turn")

    await hub._playback_done(s, {"type": "playback_done", "turn": 9})
    assert s.playback_done.is_set()


@pytest.mark.asyncio
async def test_audio_duration_is_counted_from_bytes_actually_sent(tmp_path):
    """The budget is derived from this, so it has to be right.

    24 kHz mono int16 is 48 bytes per millisecond.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 10

    await s.send_audio(10, b"\x00\x00" * 12_000)   # 24000 bytes = 500 ms
    assert abs(s.audio_ms_sent - 500.0) < 0.01, s.audio_ms_sent


@pytest.mark.asyncio
async def test_the_reply_path_actually_waits_for_playback(tmp_path):
    """Caller-side compliance, which the helper's own tests cannot show.

    Testing `_await_playback` in isolation proves the gate works; it proves
    nothing about `_reply` using it. Deleting the call left every other test
    in this file green, which is the same trap that hid the anonymous TTS
    handle -- a contract needs an assertion on the caller too.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 13
    s.state = "thinking"

    replying = asyncio.create_task(hub._reply(s, 13, "what time is it"))
    await asyncio.sleep(0.3)

    assert not replying.done(), (
        "the turn finished without waiting for the speaker to go quiet")
    assert s.state == SPEAKING and s.turn == 13

    await hub._playback_done(s, {"turn": 13})
    await asyncio.wait_for(replying, timeout=5)
    assert s.state == IDLE


@pytest.mark.asyncio
async def test_a_multi_sentence_reply_sends_one_speak_start_and_one_end(
        tmp_path):
    """speak_start/speak_end bracket the TURN, not each intent.

    The Conversation tier streams a reply as several Say intents. Bracketing
    each one tells the satellite the reply ended several times, and the page
    re-arms its wake detector on that boundary -- so it comes back mid-reply
    with the speaker still going. That is the playback bug this whole change
    exists to fix, reintroduced one level up.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 12
    s.state = "thinking"

    helper = asyncio.create_task(confirm_playback_when_asked(hub, s))
    # Falls through the fake brain's rules to the two-sentence reply.
    await asyncio.wait_for(
        hub._reply(s, 12, "tell me something substantive"), timeout=15)
    helper.cancel()

    kinds = s.ws.text_types()
    assert kinds.count("speak_start") == 1, (
        f"expected one speak_start for the turn, got {kinds.count('speak_start')}"
        f": {kinds}")
    assert kinds.count("speak_end") == 1, (
        f"expected one speak_end for the turn, got {kinds.count('speak_end')}"
        f": {kinds}")
    assert kinds.index("speak_start") < kinds.index("speak_end")


@pytest.mark.asyncio
async def test_a_full_reply_ends_idle_after_playback_is_reported(tmp_path):
    """End to end through the reply path, not just the helper."""
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 11
    s.state = "thinking"

    helper = asyncio.create_task(confirm_playback_when_asked(hub, s))
    await asyncio.wait_for(hub._reply(s, 11, "what time is it"), timeout=10)
    helper.cancel()

    assert s.state == IDLE
    assert s.turn is None
