"""The smart turn mode, wired to the real model. plan.md 4.2, D-5, D-10.

Three separate claims, and they fail for different reasons:

  * the window handed to the model is anchored at SPEECH ONSET, not at the
    start of the buffer -- Butler's pre-roll puts the wake phrase in front of
    the command, and smart-turn issue #12 measured identical speech swinging
    from p=0.012 to over 0.99 purely on what preceded it;
  * scoring never runs on the event loop -- it is a child process precisely
    so it cannot, and the daemon is single-threaded asyncio;
  * boot wires it, warms it, and refuses to pretend when it cannot.

The model tests are marked slow: they load the real ONNX graph in a real
child process. The wiring and anchoring tests are not -- they are the ones
that must run on every commit.
"""
import asyncio
import sys
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub, silence_pcm, tone_pcm  # noqa: E402
from daemon import butlerd, endpointing, ports  # noqa: E402,F401
from daemon import hub as hub_mod  # noqa: E402
from daemon.adapters.endpointer_smartturn import SmartTurnEndpointer  # noqa: E402
from daemon.pipeline import RMS_OFF, RMS_ON, TurnPipeline, rms  # noqa: E402
from daemon.settings import Settings  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"
MODEL = Path(__file__).resolve().parents[1] / "models" / "smart-turn-v3.2-cpu.onnx"
LEAD_BYTES = int(endpointing.PRE_SPEECH_LEAD_MS * endpointing.BYTES_PER_MS)


def wav_pcm(name: str) -> bytes:
    with wave.open(str(ASSETS / name), "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def feed_frames(p: TurnPipeline, pcm: bytes) -> None:
    """Push audio the way a satellite does: one 32 ms frame at a time."""
    step = 32 * 32
    for i in range(0, len(pcm), step):
        p.feed(pcm[i:i + step])


# --------------------------------------------------------------------------- #
# 1. Anchoring -- the arithmetic
# --------------------------------------------------------------------------- #
def test_the_window_starts_a_lead_before_speech_onset():
    """Upstream builds [onset - pre_speech_ms, now]; so do we."""
    utt = b"\x11\x22" * 40_000                     # 2.5 s of something
    onset = 30_000                                 # ~937 ms in
    st = endpointing.TurnState(
        silence_ms=300, speech_ms=1000, elapsed_ms=2000, voiced=True,
        transcript="", utterance=utt, onset_offset=onset)

    window = endpointing.SmartTurnEnd(None).window(st)
    assert len(window) == len(utt) - (onset - LEAD_BYTES)
    assert window == utt[onset - LEAD_BYTES:]


def test_the_window_never_starts_before_the_buffer_does():
    """Speech that begins in the first half second still gets whole audio."""
    utt = b"\x11\x22" * 8_000
    st = endpointing.TurnState(
        silence_ms=300, speech_ms=500, elapsed_ms=800, voiced=True,
        transcript="", utterance=utt, onset_offset=200)
    assert endpointing.SmartTurnEnd(None).window(st) == utt


def test_the_window_stays_on_a_sample_boundary():
    """int16 is two bytes: an odd offset shifts every sample by one byte and
    hands the model white noise that still has the right length."""
    utt = b"\x11\x22" * 40_000
    st = endpointing.TurnState(
        silence_ms=300, speech_ms=1000, elapsed_ms=2000, voiced=True,
        transcript="", utterance=utt, onset_offset=LEAD_BYTES + 777)
    assert len(endpointing.SmartTurnEnd(None).window(st)) % 2 == 0


def test_a_state_without_an_onset_scores_what_it_always_did():
    """onset_offset defaults to 0, so a caller that cannot compute one keeps
    today's behaviour instead of crashing."""
    utt = b"\x11\x22" * 8_000
    st = endpointing.TurnState(
        silence_ms=300, speech_ms=500, elapsed_ms=800, voiced=True,
        transcript="", utterance=utt)
    assert endpointing.SmartTurnEnd(None).window(st) == utt


# --------------------------------------------------------------------------- #
# 2. Anchoring -- what the pipeline reports
# --------------------------------------------------------------------------- #
def make_pipeline(policy=None, endpointer=None):
    finals = []
    p = TurnPipeline(
        turn=1, asr=ports.build(ports.ASRS, "fake", ["hello"]),
        on_partial=lambda _p: None,
        on_final=lambda f, reason: finals.append((f, reason)),
        asr_config=ports.AsrConfig(model="test"),
        endpointer=endpointer, policy=policy, poll_s=0.001)
    return p, finals


def test_onset_is_the_first_speech_after_the_preroll():
    """The wake phrase is IN the buffer and must not be the anchor.

    plan.md 4.2: the satellite back-fills from ~416 ms before its detector
    fired and keeps buffering until capture_start, so the flush carries the
    tail of "hey jarvis" plus the gap after it. The turn's speech starts
    after that, and that is what the model should be scored from.
    """
    p, _ = make_pipeline()
    preroll = tone_pcm(384) + silence_pcm(616)      # wake tail, then the gap
    p.note_lookback(len(preroll))
    feed_frames(p, preroll)
    feed_frames(p, tone_pcm(640))                   # the command

    assert p.onset_offset() == len(preroll), (
        f"onset landed at {p.onset_offset()} rather than at the end of the "
        f"{len(preroll)}-byte pre-roll")


def test_without_a_preroll_onset_is_simply_the_first_speech():
    """A native mic source (Phase 5) declares no pre-roll and must not need
    to: the first loud frame is the onset."""
    p, _ = make_pipeline()
    feed_frames(p, silence_pcm(160) + tone_pcm(500))
    assert p.onset_offset() == 160 * 32


def test_the_offset_follows_the_buffer_as_it_scrolls():
    """The utterance buffer keeps only the last 9 s. An offset measured
    against the whole turn would point past the end of it, and the window
    would be sliced from the wrong place -- or from nowhere."""
    p, _ = make_pipeline()
    feed_frames(p, tone_pcm(500))
    early = p.onset_offset()
    feed_frames(p, tone_pcm(2000) + silence_pcm(8000))
    assert p.onset_offset() == 0, (
        f"the onset scrolled out of the buffer but the offset is still "
        f"{p.onset_offset()} (it was {early})")
    assert p.onset_offset() <= len(bytes(p._utt))


@pytest.mark.asyncio
async def test_the_endpointer_is_not_given_the_wake_phrase():
    """The call site, not the helper (D-18).

    A window() that anchors correctly proves nothing if the pipeline hands
    the policy a state with no onset in it. This drives a real TurnPipeline
    with a real SmartTurnEnd and asserts on the bytes that actually reach the
    endpointer: the wake phrase is loud, it IS in the buffer, and the model
    must not see it.
    """
    seen = []

    async def endpointer(pcm):
        seen.append(pcm)
        return 0.9                      # confident, so the turn ends promptly

    p, finals = make_pipeline(
        policy=endpointing.SmartTurnEnd(endpointer))
    preroll = tone_pcm(384) + silence_pcm(616)
    p.note_lookback(len(preroll))
    feed_frames(p, preroll)
    feed_frames(p, tone_pcm(640) + silence_pcm(400))

    await asyncio.wait_for(p.run(), timeout=10)

    assert finals and finals[0][1] == "endpoint", f"wrong ending: {finals}"
    assert seen, "the policy never consulted the endpointer"
    window = seen[0]
    buffered = bytes(p._utt)
    assert rms(buffered[:32 * 100]) >= RMS_ON, (
        "the fixture is wrong: the wake phrase must be inside the buffer for "
        "this test to mean anything")
    assert rms(window[:32 * 100]) < RMS_OFF, (
        "the model was handed the wake phrase: the window starts in loud "
        "audio, so it was not anchored at the command's onset")
    assert len(window) == len(buffered) - (len(preroll) - LEAD_BYTES)


class _StubPipeline:
    def __init__(self):
        self.noted = []

    def note_lookback(self, n_bytes):
        self.noted.append(n_bytes)


@pytest.mark.asyncio
async def test_the_hub_tells_the_pipeline_how_much_was_preroll(tmp_path):
    """The other call site (D-18).

    The pipeline can only skip the wake phrase if something tells it where
    the back-fill ends, and the only thing that knows is the `preroll`
    header. Until this test existed the daemon validated the byte count and
    threw it away, so every anchoring test above could pass with the wake
    phrase still going to the model in production.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.state = hub_mod.CAPTURING
    s.pipeline = _StubPipeline()

    await hub._preroll(s, {"type": "preroll", "turn": 1, "bytes": 22528,
                           "ms": 704, "lookback": 13312})
    assert s.pipeline.noted == [13312]


@pytest.mark.asyncio
async def test_a_junk_preroll_count_is_refused_rather_than_believed(tmp_path):
    """A miscounted pre-roll moves the model's window. Refuse it loudly and
    leave the pipeline on its safe default rather than anchoring on garbage.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.state = hub_mod.CAPTURING
    s.pipeline = _StubPipeline()

    await hub._preroll(s, {"type": "preroll", "bytes": "lots"})
    assert s.pipeline.noted == []
    assert "error" in s.ws.text_types()


# --------------------------------------------------------------------------- #
# 3. Boot wiring
# --------------------------------------------------------------------------- #
def test_butlerd_hands_the_hub_a_real_endpointer(tmp_path, capsys):
    """The setting must not name a mode the daemon cannot run.

    Before this, `endpointing.build("smart")` always fell back to patient
    silence because nothing ever supplied an endpointer -- the setting looked
    like it did nothing.
    """
    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "fake"}})
    assert hub.endpointer is not None, "boot wired no endpointer at all"

    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("turn_mode", "smart")
    capsys.readouterr()
    policy = hub._policy_for_turn()
    assert policy.name == "smart", f"smart mode fell back to {policy.name}"
    assert "no endpointer" not in capsys.readouterr().out


def test_an_unknown_endpointer_name_is_refused_like_any_other_adapter():
    with pytest.raises(KeyError):
        butlerd.build_endpointer("psychic")


def test_the_endpointer_can_be_turned_off_explicitly():
    """"none" is the honest way to run without it: smart mode then refuses
    out loud instead of quietly running a different policy."""
    assert butlerd.build_endpointer("none") is None


class _BrokenEndpointer:
    model_path = Path("nowhere.onnx")

    async def warm(self):
        raise FileNotFoundError("smart turn model missing: nowhere.onnx")


@pytest.mark.asyncio
async def test_boot_refuses_to_start_smart_with_a_broken_endpointer(tmp_path):
    """Fail fast and loud: never patient silence under the smart name."""
    hub = make_hub(tmp_path, endpointer=_BrokenEndpointer())
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("turn_mode", "smart")

    with pytest.raises(SystemExit) as e:
        await butlerd.warm_endpointer(hub)
    assert "smart" in str(e.value) and "nowhere.onnx" in str(e.value)


@pytest.mark.asyncio
async def test_a_broken_endpointer_in_another_mode_warns_and_stands_down(
        tmp_path, capsys):
    """Patient and endword do not need the model, so a missing one must not
    stop the daemon -- but it must be loud, and smart must not silently
    become available later."""
    hub = make_hub(tmp_path, endpointer=_BrokenEndpointer())
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("turn_mode", "patient")

    await butlerd.warm_endpointer(hub)

    out = capsys.readouterr().out
    assert "WARNING" in out and "UNAVAILABLE" in out
    assert hub.endpointer is None


@pytest.mark.asyncio
async def test_a_missing_model_is_named_before_a_child_is_started(monkeypatch):
    """The parent checks the file itself, so the failure reads as a sentence
    instead of as BrokenProcessPool from a child that died on import."""
    from daemon.adapters import endpointer_smartturn as mod
    ep = SmartTurnEndpointer()
    monkeypatch.setattr(mod._turn_engine(), "MODEL_PATH",
                        Path("no-such-model.onnx"))
    with pytest.raises(FileNotFoundError) as e:
        await ep.warm()
    assert "no-such-model.onnx" in str(e.value)
    assert not ep.warmed


# --------------------------------------------------------------------------- #
# 4. The real model
# --------------------------------------------------------------------------- #
pytestmark_model = pytest.mark.skipif(
    not MODEL.exists(), reason=f"{MODEL.name} is not on disk")


@pytest.mark.slow
@pytest.mark.needs_audio
@pytestmark_model
@pytest.mark.asyncio
async def test_it_scores_a_real_utterance():
    """End to end on real speech, with the timing split the engine reports.

    The split matters: feature extraction and inference degrade for different
    reasons under load (turn_engine.score_timed), and a single wall number
    cannot tell a GIL problem from a busy CPU.
    """
    ep = SmartTurnEndpointer()
    load_ms = await ep.warm()
    assert ep.warmed

    audio = wav_pcm("short_question.wav")
    t0 = time.perf_counter()
    p = await ep(audio)
    wall_ms = (time.perf_counter() - t0) * 1000

    assert 0.0 <= p <= 1.0, f"probability out of range: {p}"
    assert p > 0.5, (
        f"'What time is it' is a complete question; the model scored {p:.3f}")
    assert wall_ms < endpointing.ENDPOINT_TIMEOUT_S * 1000, (
        f"one inference took {wall_ms:.0f} ms, past the {endpointing.ENDPOINT_TIMEOUT_S}s "
        f"budget the policy allows it")
    assert ep.last["feature_ms"] > 0 and ep.last["inference_ms"] > 0
    print(f"\n[measured] load {load_ms:.0f} ms; score {wall_ms:.1f} ms "
          f"(features {ep.last['feature_ms']} ms, inference "
          f"{ep.last['inference_ms']} ms) p={p:.4f}")


@pytest.mark.slow
@pytest.mark.needs_audio
@pytestmark_model
@pytest.mark.asyncio
async def test_scoring_never_blocks_the_event_loop():
    """The defect this exists to prevent, and it is not hypothetical.

    The same mistake was made with the recognizer's finalize() and fixed with
    asyncio.to_thread; here a thread is not enough either, because the numpy
    feature pipeline fights the STT worker for the GIL (turn_engine.get_pool:
    ~30 ms becomes ~2200 ms in-process). Butler is single-threaded asyncio,
    so blocking is not one satellite waiting -- it is all of them, plus the
    heartbeat.

    The instrument is a coroutine spinning on `sleep(0)`. It counts loop
    iterations, so an inference that ran inline would show a flat stretch of
    zero: nothing else on the loop can run while it does.
    """
    ep = SmartTurnEndpointer()
    await ep.warm()
    audio = wav_pcm("long_utterance.wav")

    spins = 0
    running = True

    async def spinner():
        nonlocal spins
        while running:
            await asyncio.sleep(0)
            spins += 1

    task = asyncio.create_task(spinner())
    await asyncio.sleep(0)
    before = spins
    t0 = time.perf_counter()
    await ep(audio)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    during = spins - before
    running = False
    await task

    assert elapsed_ms > 5, (
        f"the call returned in {elapsed_ms:.1f} ms -- too fast to prove "
        f"anything about blocking; the model did not run")
    assert during > 200, (
        f"the event loop advanced only {during} times during a "
        f"{elapsed_ms:.0f} ms inference: the daemon was frozen for every "
        f"other satellite")
    print(f"\n[measured] loop advanced {during} times during a "
          f"{elapsed_ms:.0f} ms inference")
