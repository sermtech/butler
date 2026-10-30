"""The daemon must stay answerable. D-1, D-10, plan.md 3.8.

Three defects an adversarial review found that the whole suite missed,
because each one is about what happens to EVERYONE ELSE while one turn is
busy. Butler is single-threaded asyncio, so a blocking call is not one
satellite waiting -- it is every satellite, the heartbeat, and the worker
announcements, all stopped. D-1 says the daemon must never latch the
microphone off; freezing is indistinguishable from that to the user.
"""
import asyncio
import time

import pytest

from conftest import StubTts, attach_session, make_hub
from daemon import ports
from daemon.pipeline import TurnPipeline


class BlockingAsr:
    """An Asr whose finalize blocks a real thread, like the real one does.

    transcribe.cpp's adapter waits on a threading.Event until its worker has
    delivered the Final -- its own docstring says to call it with
    asyncio.to_thread, and for a while nothing did.
    """

    def __init__(self, block_s: float = 0.4):
        self.block_s = block_s
        self.warmed = False

    def open(self, cfg, on_partial, on_final):
        return _BlockingSession(self.block_s, on_final)

    async def warm(self):
        self.warmed = True

    @property
    def info(self):
        return {"arch": "blocking", "backend": "none"}


class _BlockingSession:
    def __init__(self, block_s, on_final):
        self.block_s = block_s
        self._on_final = on_final
        self._done = False

    def feed(self, pcm):
        pass

    def finalize(self):
        if self._done:
            return
        self._done = True
        time.sleep(self.block_s)          # a real blocking wait, not a sleep(0)
        self._on_final(ports.Final(text="hello"))

    def reset_turn(self):
        pass

    def close(self):
        pass


@pytest.mark.asyncio
async def test_finalizing_does_not_freeze_the_event_loop():
    """A blocking ASR finalize must not stall every other satellite.

    The failure is invisible from the turn being finalized -- it completes
    fine. What breaks is everything else: while this blocks, no other
    session's messages are read, no heartbeat goes out, and a second
    satellite's wake word is simply not processed until it returns.
    """
    finals = []
    p = TurnPipeline(
        turn=1, asr=BlockingAsr(block_s=0.4),
        on_partial=lambda _p: None,
        on_final=lambda f, reason: finals.append(f),
        asr_config=ports.AsrConfig(model="test"), poll_s=0.001)

    ticks = 0

    async def other_work():
        """Stands in for every other satellite on the loop."""
        nonlocal ticks
        for _ in range(40):
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(other_work())
    await p.finalize_now()
    ticker.cancel()

    assert finals, "the turn did not finalize"
    assert ticks >= 10, (
        f"the event loop only advanced {ticks} times during a 400 ms "
        f"finalize -- the daemon was frozen for everyone")


@pytest.mark.asyncio
async def test_boot_warms_the_recognizer_not_just_speech(tmp_path):
    """D-10: pay every cold start at boot.

    warmup()'s own docstring cited the multi-minute STT cold start as the
    reason it exists, and then warmed only TTS. Combined with a model load
    that happens on the event loop, the worst cold start in the system
    landed on the first thing the owner ever said.
    """
    from daemon import butlerd

    hub = make_hub(tmp_path)
    hub.asr = BlockingAsr()
    await butlerd.warmup(hub)

    assert hub.asr.warmed, "boot warmed speech but not recognition"


class CountingBrain:
    """A Brain that records whether boot ever warmed it."""

    def __init__(self):
        self.warms = 0

    async def turn(self, text, ctx):
        return
        yield                                    # pragma: no cover

    async def warm(self):
        self.warms += 1

    @property
    def info(self):
        return {"engine": "counting"}


@pytest.mark.asyncio
async def test_boot_warms_the_brain(tmp_path):
    """D-10, D-18: the port having warm() is not the daemon calling it.

    This assertion is on the CALLER on purpose. Conformance proves every
    Brain CAN be warmed; only this proves butlerd does it, and that gap is
    the one that has bitten this project three times -- an abort that named
    no handle, a playback gate the reply path never called, per-session
    barge-in. Each time the callee's own tests were green.

    It matters most for the brain of the three. The measured cold load of
    the SMALLEST model on this machine is 15.85 s (plan.md 4.4), which is
    ten times Kokoro's, and it would land on the first thing the owner ever
    said.
    """
    from daemon import butlerd

    hub = make_hub(tmp_path)
    hub.brain = CountingBrain()
    await butlerd.warmup(hub)

    assert hub.brain.warms == 1, (
        f"boot warmed speech and recognition but not the brain "
        f"(warm called {hub.brain.warms} times)")


@pytest.mark.asyncio
async def test_a_brain_that_will_not_warm_does_not_stop_the_boot(tmp_path):
    """A slow first turn is survivable; a daemon that will not start is not.

    Same rule the asr and tts warmups already follow: loud, never fatal
    (D-1). Butler with a cold brain still answers; Butler that refused to
    boot answers nothing, and the owner has no microphone at all.
    """
    from daemon import butlerd

    class RefusingBrain(CountingBrain):
        async def warm(self):
            raise RuntimeError("no endpoint configured")

    hub = make_hub(tmp_path)
    hub.brain = RefusingBrain()
    await butlerd.warmup(hub)            # must not raise


class DyingTts(StubTts):
    """Fails on any segment containing a marker word."""

    def __init__(self):
        super().__init__()
        self.attempts = []

    # `speed` is accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.attempts.append(text)
        if "explode" in text:
            raise RuntimeError("synthesis died")
        yield b"\x00\x00" * 240



@pytest.mark.asyncio
async def test_a_dead_segment_abandons_the_rest_of_the_reply(tmp_path):
    """plan.md 3.8: abandon the remainder loudly, never skip it silently.

    The Conversation tier yields one Say per sentence. Stepping over a
    sentence the engine choked on leaves Butler sounding like it finished a
    complete answer while a piece of it was never spoken -- worse than an
    audible failure, because nothing signals that anything went wrong.
    """
    hub = make_hub(tmp_path)
    hub.tts = DyingTts()
    s = attach_session(hub, "desk")
    s.turn = 41

    ok = await hub._speak(s, 41, "This will explode now.", "reflex")
    assert ok is False, "a failed segment reported success"

    errors = [t for t in s.ws.text_types() if t == "error"]
    assert errors, "the failure was not reported to the satellite"
