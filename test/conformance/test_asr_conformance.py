"""Every Asr adapter must pass this. See plan.md 4.10 and AC-7a.

These are the properties butlerd relies on, not the properties any one
engine happens to have. Adding an adapter means adding it to ADAPTERS here;
if it cannot pass, the pipeline cannot use it, and finding that out in this
file is much cheaper than finding it out in a live conversation.

Real adapters are added as they are written. Today only the fake exists,
which is exactly the situation this suite is designed to survive: the
contract is pinned before the second implementation shows up, so the second
implementation has something to be measured against.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import ports  # noqa: E402
from daemon.ports import AsrConfig  # noqa: E402

# One param per adapter: (registry name, constructor kwargs). Wrapped in a
# tuple so pytest sees ONE value, not two argnames.
ADAPTERS = [
    pytest.param(("fake", {}), id="fake"),
    pytest.param(("transcribecpp", {}), id="transcribecpp",
                 marks=[pytest.mark.needs_gpu, pytest.mark.slow]),
]

ADAPTER_NAMES = [p.values[0][0] for p in ADAPTERS]

SILENCE_1S = b"\x00\x00" * 16_000


@pytest.fixture(params=ADAPTERS)
def asr(request):
    name, kwargs = request.param
    return ports.build(ports.ASRS, name, **kwargs)


def _session(asr, script=None):
    partials, finals = [], []
    sess = asr.open(AsrConfig(model="test"), partials.append, finals.append)
    return sess, partials, finals


def test_satisfies_the_protocol(asr):
    """The adapter is structurally an Asr.

    Catches an adapter that grew a differently-named method and would only
    blow up at runtime deep inside a turn.
    """
    assert isinstance(asr, ports.Asr)


def test_finalize_on_silence_still_emits_exactly_one_final(asr):
    """D-7 / AC-6: the final is the turn's END, not its text.

    Catches the defect class where an empty utterance emits nothing, the
    turn claim never releases, and the next wake is refused as busy.
    """
    sess, _partials, finals = _session(asr)
    sess.feed(SILENCE_1S)
    sess.finalize()
    assert len(finals) == 1, f"expected exactly one Final, got {len(finals)}"
    assert finals[0].text == ""
    assert finals[0].failed is False, (
        "a quiet room was reported as a recognizer FAILURE (D-26): the daemon "
        "speaks about one of those and answers the other with nothing, so an "
        "adapter that marks every empty Final makes Butler apologise for "
        "every pause")


def test_finalize_with_no_audio_at_all_still_emits_a_final(asr):
    """The zero-byte case: wake fired, the user said nothing, capture ended
    instantly. Catches an adapter that only finalizes if it saw audio."""
    sess, _partials, finals = _session(asr)
    sess.finalize()
    assert len(finals) == 1


def test_finalize_is_idempotent(asr):
    """A second finalize must not produce a second Final.

    Catches double-finalize from a race between the endpointer and a client
    hint, which would emit two turn-ends and desynchronise the session.
    """
    sess, _partials, finals = _session(asr)
    sess.feed(SILENCE_1S)
    sess.finalize()
    sess.finalize()
    assert len(finals) == 1


def test_reset_turn_emits_no_final(asr):
    """Barge-in abandons an utterance; it does not end it.

    Catches an adapter that treats reset as finalize, which would speak a
    reply to the utterance the user just interrupted.
    """
    sess, _partials, finals = _session(asr)
    sess.feed(SILENCE_1S)
    sess.reset_turn()
    assert finals == []


def _speech_case(name: str):
    """An adapter instance that WILL produce words, plus the audio to feed it.

    The fake is scripted, so silence of the right length is enough. A real
    engine needs real speech, which is why this is per-adapter rather than
    one shared blob -- a conformance suite that can only be satisfied by a
    fake is not testing anything.
    """
    if name == "fake":
        # Must be NON-silent: the fake only advances its script on voiced
        # audio, so that silence can still produce an empty Final through the
        # daemon (D-7). Feeding silence here would assert nothing.
        voiced = (b"\x00\x20" * (16 * 560 * 6))
        return ports.build(ports.ASRS, "fake", ["what", "time", "is", "it"]), voiced
    # The LONG fixture (5.19 s), not the 1.08 s one. parakeet buffered
    # streaming emits its first update after chunk+right ms of audio: 1120 ms
    # at "balanced" (70,7,7) and 320 ms at "low" (70,2,2). MEASURED
    # 2026-08-16 on short_question.wav: balanced -> 0 partials, low -> 1.
    #
    # ONE partial is the trap. The regression check below is pairwise, so a
    # single partial makes it iterate zero times -- the test passes while
    # asserting nothing about the property it is named after. The long fixture
    # produces several partials at the DEFAULT latency, so the assertion has
    # something to bite on and the adapter is exercised in the preset the
    # daemon actually runs.
    wav = Path(__file__).resolve().parents[1] / "assets" / "long_utterance.wav"
    if not wav.exists():
        pytest.skip(f"needs {wav.name}; run scripts/make-fixtures")
    import wave
    with wave.open(str(wav), "rb") as w:
        return ports.build(ports.ASRS, name), w.readframes(w.getnframes())


@pytest.mark.parametrize("name", ADAPTER_NAMES)
def test_committed_text_never_regresses(name):
    """Committed is a promise; tentative is a guess.

    Catches an adapter that revises already-committed words, which makes the
    satellite's transcript flicker backwards mid-sentence.
    """
    adapter, pcm = _speech_case(name)
    partials, finals = [], []
    sess = adapter.open(AsrConfig(model="test"), partials.append, finals.append)
    for i in range(0, len(pcm), 16 * 560):
        sess.feed(pcm[i:i + 16 * 560])
    sess.finalize()
    assert len(partials) >= 2, (
        f"need at least two partials for a pairwise regression check, got "
        f"{len(partials)} -- with fewer, this test asserts nothing and passes "
        f"for the wrong reason")
    committed = [p.committed for p in partials]
    for earlier, later in zip(committed, committed[1:]):
        assert later.startswith(earlier), (
            f"committed regressed: {earlier!r} -> {later!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ADAPTER_NAMES)
async def test_a_whole_clip_is_transcribed_in_one_call(name):
    """`transcribe` is the OTHER shape of this port, and every adapter has it.

    Not a session: no partials, no Final, no turn. A clip that is already
    over goes in and its text comes back once (docs/ambient.md section 1).
    Catches an adapter that implemented only the streaming half, which is a
    daemon that cannot back-fill what the room was saying before the wake
    word.

    Asserted on WORDS, never on a length. The measurement this method exists
    for was first reported as a 16x speedup over an EMPTY transcript, because
    the engine's own Result reprs at ~214 characters whatever it decoded --
    so a size that looks plausible is exactly the failure shape here.
    """
    adapter, pcm = _speech_case(name)
    text = await adapter.transcribe(pcm, AsrConfig(model="test"))
    assert isinstance(text, str)
    assert text.split(), (
        f"{name} transcribed real speech to {text!r}. An empty string is what "
        f"this engine returns for the WRONG SAMPLE FORMAT -- fast, confident "
        f"and empty -- so a blank here is a defect and not a quiet room")


@pytest.mark.asyncio
@pytest.mark.parametrize("clip", [b"", SILENCE_1S], ids=["empty", "silence"])
async def test_a_quiet_clip_transcribes_to_nothing_rather_than_raising(asr, clip):
    """A quiet room is the AMBIENT BUFFER'S NORMAL STATE, not an error.

    The buffer holds the last N seconds of a room that is silent almost all
    of the time, so both the zero-length clip (nothing captured yet) and the
    all-zero one (nobody spoke) have to come back as empty text. An adapter
    that raises on either turns resting into an exception per wake, which is
    the D-1 shape: the failure arrives on the path that must always work.
    """
    text = await asr.transcribe(clip, AsrConfig(model="test"))
    assert text == "", f"a quiet clip transcribed to {text!r}"


def test_close_is_safe_twice(asr):
    """Shutdown paths run more than once under error handling."""
    sess, _p, _f = _session(asr)
    sess.close()
    sess.close()


def test_info_is_cheap_enough_for_the_handshake(asr):
    """Reading info must not load the model. See ports.py's own warning.

    This is the adapter the warning was written for, and until 2026-08-17
    nothing checked it: `info` opened a throwaway session when `_info` was
    unset, which is a blocking GGUF and Vulkan load. `butlerd.warmup`
    swallows a warm failure on purpose (loud, never fatal -- D-1), so one
    failed warm left that path reachable for the life of the process, from
    `hub._hello`, on the event loop, on EVERY satellite connect.

    Budget rationale is in the Tts suite's copy of this test.
    """
    started = time.perf_counter()
    info = asr.info
    elapsed = time.perf_counter() - started
    assert isinstance(info, dict)
    assert elapsed < 0.25, (
        f"reading info took {elapsed * 1000:.0f} ms -- it is read on the "
        f"event loop during the handshake, so this is every satellite "
        f"frozen for that long, each time any device connects")


def test_info_reports_what_the_ui_needs(asr):
    """Capabilities come from the loaded model, never hard-coded per slug.

    Catches an adapter that cannot say what it is, which leaves the HUD
    claiming a backend the daemon is not actually using.
    """
    info = asr.info
    assert isinstance(info, dict)
    for key in ("arch", "backend"):
        assert key in info, f"info missing {key!r}: {info}"
