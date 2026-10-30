"""Every Tts adapter must pass this. See plan.md 4.10 and AC-7a.

The properties here are the ones the speech path depends on, and most of
them exist because an earlier prototype paid for the corresponding defect (section 6):
chunked output so playback can start early, byte alignment so the playback
worklet never gets half a sample, an abort that is reachable while
synthesis is in flight, and loud failure instead of a silent skip.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import ports  # noqa: E402

ADAPTERS = [
    pytest.param(("fake", {}), id="fake"),
    pytest.param(("kokoro", {}), id="kokoro", marks=[pytest.mark.slow]),
    # Needs the pinned binary: python -X utf8 scripts/fetch-pocket-tts.py
    pytest.param(("pocket", {}), id="pocket", marks=[pytest.mark.slow]),
]

# No shared voice constant. It was "af_heart", a KOKORO name, which meant the
# suite that exists to prove any adapter can be swapped in was itself the thing
# blocking a second engine: Piper would fail every case here on a voice it has
# never heard of, and the failure would read as "Piper is broken".
#
# The voice now comes from the adapter, through the same `info["voice"]` the
# settings picker reads (AC-41, AC-42). The suite asks rather than declares --
# which is the property it is supposed to be testing.
SHORT = "It is twenty past four."
THREE_SENTENCES = ("The tests are passing. "
                   "I pushed the branch. "
                   "Nothing else is running.")
# Long enough that finishing it costs far more than abandoning it. Ten short
# sentences at Kokoro's measured ~450 ms each is roughly 4.5 s of synthesis,
# and the abort test below is only meaningful if the abandoned work is big
# compared with the one sentence an abort is allowed to strand.
MANY_SENTENCES = " ".join(
    f"This is sentence number {n}." for n in range(1, 11))


@pytest.fixture(params=ADAPTERS)
def tts(request):
    name, kwargs = request.param
    return ports.build(ports.TTSS, name, **kwargs)


@pytest.fixture
def voice(tts):
    """The voice THIS adapter says it speaks by default.

    Read from `info`, never declared here, so adding an engine costs a row in
    ADAPTERS and nothing else. A shared constant made the suite engine-
    specific, which is the one thing a conformance suite must not be.
    """
    return tts.info["voice"]


@pytest.mark.asyncio
async def test_satisfies_the_protocol(tts):
    assert isinstance(tts, ports.Tts)


@pytest.mark.asyncio
async def test_yields_at_least_one_chunk(tts, voice):
    """Catches an adapter that returns nothing and leaves the turn silent
    while the session waits forever for a speak_end."""
    chunks = [c async for c in tts.synthesize(SHORT, voice)]
    assert chunks, "adapter yielded no audio"
    assert all(isinstance(c, (bytes, bytearray)) for c in chunks)


@pytest.mark.asyncio
async def test_chunks_are_int16_aligned(tts, voice):
    """Odd-length chunks split a sample across a boundary.

    Catches the class of bug where the playback worklet drifts by half a
    sample per chunk and the reply slowly turns into noise.
    """
    async for chunk in tts.synthesize(SHORT, voice):
        assert len(chunk) % 2 == 0, f"chunk of {len(chunk)} bytes is not int16-aligned"


@pytest.mark.asyncio
async def test_longer_text_produces_more_audio(tts, voice):
    """A weak but load-bearing sanity check: the adapter is actually reading
    the text. Catches a stub wired to a fixed buffer, which would make every
    downstream timing test meaningless."""
    short_bytes = sum([len(c) async for c in tts.synthesize(SHORT, voice)])
    long_bytes = sum([len(c) async for c in tts.synthesize(THREE_SENTENCES, voice)])
    assert long_bytes > short_bytes


@pytest.mark.asyncio
async def test_streams_rather_than_arriving_all_at_once(tts, voice):
    """More than one chunk for a multi-sentence reply.

    This is the measured one: collapsing the stream into a single blob cost
    +3252 ms on a 3-sentence reply through the HTTP sidecar. An adapter that
    yields exactly one chunk for three sentences is allowed by the port but
    is a latency regression, so it must be a deliberate, visible choice.
    """
    chunks = [c async for c in tts.synthesize(THREE_SENTENCES, voice)]
    assert len(chunks) > 1, (
        "adapter returned one blob for three sentences; if that is "
        "unavoidable for this engine, xfail this test with the reason")


@pytest.mark.asyncio
async def test_abort_actually_stops_an_in_flight_synthesis(tts, voice):
    """Abort must reach THIS synthesis, matched by handle.

    The weaker test below only proves abort() exists. That was not enough:
    the daemon called abort(turn) while synthesize() registered anonymously,
    so nothing matched, the old assertion passed, and barge-in cancelled
    nothing. An abort that cannot find its synthesis is not an abort (D-4).
    """
    handle = ("turn", 42)
    chunks = []
    stream = tts.synthesize(THREE_SENTENCES, voice, handle=handle)
    async for chunk in stream:
        chunks.append(chunk)
        if len(chunks) == 1:
            await tts.abort(handle)
    full = [c async for c in tts.synthesize(THREE_SENTENCES, voice,
                                            handle=("turn", 43))]
    assert len(chunks) < len(full), (
        f"abort did not shorten the stream: {len(chunks)} chunks aborted "
        f"vs {len(full)} unaborted -- the handle did not match")


@pytest.mark.asyncio
async def test_abort_frees_the_engine_not_just_the_caller(tts, voice):
    """After an abort, the NEXT reply must not queue behind the abandoned one.

    This is the half of D-4 that an adapter can satisfy on paper and fail in
    the room. Cancelling the caller's wait makes barge-in look instant --
    AC-5 stops the audio either way -- while the engine keeps synthesizing a
    reply nobody will hear, so the answer to the interrupting utterance
    arrives a whole abandoned reply late. MEASURED at ~6.7 s through the
    whole-WAV sidecar, and it is the reason plan.md section 5 step 8 says the
    transport rewrite must add a cancel route and not just streaming.

    Self-calibrating rather than a fixed millisecond bound: the control is
    how long the abandoned text takes to synthesize in full on this machine.

    The abort fires on a CLOCK, not after the first chunk. Written the
    obvious way it passed against the very transport it exists to condemn:
    the whole-WAV sidecar yields its first chunk only when the last sentence
    is done, so "abort after chunk one" aborted a synthesis that had already
    finished and the engine was free either way (D-13).
    """
    control = time.monotonic()
    async for _ in tts.synthesize(MANY_SENTENCES, voice, handle=("turn", 200)):
        pass
    full_ms = (time.monotonic() - control) * 1000

    handle = ("turn", 201)

    async def drain():
        async for _ in tts.synthesize(MANY_SENTENCES, voice, handle=handle):
            pass

    abandoned = asyncio.create_task(drain())
    # A quarter of the way in: far enough that the engine is genuinely
    # working, early enough that most of the synthesis is still ahead of it.
    await asyncio.sleep(full_ms / 4000)
    await tts.abort(handle)

    started = time.monotonic()
    async for _ in tts.synthesize(SHORT, voice, handle=("turn", 202)):
        break
    after_abort_ms = (time.monotonic() - started) * 1000
    abandoned.cancel()

    # The floor keeps this honest for an adapter with no real delays: a fake
    # produces both numbers near zero, and half of nearly zero is a bound
    # nothing can meet. Against a real engine full_ms dominates and the floor
    # never applies.
    budget = max(50.0, full_ms / 2)
    assert after_abort_ms < budget, (
        f"the next reply waited {after_abort_ms:.0f} ms after an abort, "
        f"against {full_ms:.0f} ms to synthesize the abandoned text in full: "
        f"the abort freed the caller but not the engine")


@pytest.mark.asyncio
async def test_abort_does_not_kill_an_unrelated_synthesis(tts, voice):
    """Aborting turn N must not silence turn N+1.

    The failure this guards is worse than the one above: a barge-in would
    cancel the reply the user just asked for, and it would look like the
    daemon simply never answered.
    """
    await tts.abort(("turn", 100))
    chunks = [c async for c in tts.synthesize(SHORT, voice,
                                              handle=("turn", 101))]
    assert chunks, "an unrelated abort silenced the next reply"


@pytest.mark.asyncio
async def test_synthesize_accepts_a_speaking_speed(tts, voice, request):
    """AC-107. Speed is part of the PORT, so every adapter takes it.

    This is all a port property can say. The fake has no speaking rate to
    change, and "the number reached the engine" can only be asked of one
    engine -- `test/test_tts_kokoro.py` asserts it on the body the sidecar
    actually received, which is the AC-38 rule (stored and on the wire are
    different claims).

    What this catches is the adapter that never grew the parameter: the hub
    passes `speed=` on every reply, so the failure is a TypeError inside the
    turn, which the room hears as silence.

    Pocket has no rate control and REFUSES any speed but 1.0 rather than
    speaking at 1.0 (`test/test_tts_pocket.py`); no fixing step is planned
    while the pinned binary has no speed option.
    """
    if request.node.callspec.id == "pocket":
        request.applymarker(pytest.mark.xfail(
            strict=True, raises=ValueError,
            reason="Pocket TTS has no speed control; it refuses speed != 1.0"))
    chunks = [c async for c in tts.synthesize(SHORT, voice, speed=0.85)]
    assert chunks, "adapter yielded no audio when it was given a speed"


@pytest.mark.asyncio
async def test_abort_is_callable_and_does_not_raise(tts):
    """Abort must be reachable, not queued behind the synthesis it cancels.

    This test cannot prove the dispatch path is outside the queue -- that is
    asserted at the adapter level -- but it does catch an adapter that has
    no abort at all (D-4).
    """
    await tts.abort("some-handle")


@pytest.mark.asyncio
async def test_warm_completes(tts):
    """Cold start is 1.2 s measured for Kokoro; D-10 says pay it at boot."""
    await tts.warm()


@pytest.mark.asyncio
async def test_failure_is_loud():
    """A sentence that cannot be synthesized raises; it is never silently
    skipped, which would make Butler quietly drop half an answer.

    Not parametrized, deliberately. It builds its own fake because `fail_on`
    is a fake-only lever, and it used to ACCEPT the adapter fixtures while
    ignoring them -- so the kokoro case ran a second identical assertion
    against the fake and looked like real coverage (D-13, the same shape as
    the info test above). The real engine's version of this property lives in
    `test/test_tts_kokoro.py`, where a voice it cannot speak fails loudly.
    """
    failing = ports.build(ports.TTSS, "fake", speed=0.0, fail_on="explode")
    with pytest.raises(Exception):
        _ = [c async for c in failing.synthesize("please explode now",
                                                 "stub_voice")]


@pytest.mark.asyncio
async def test_info_reports_what_the_ui_needs(tts, voice):
    """An adapter must be able to say what it is and what it can speak.

    WARMED FIRST, since AC-108. `voices` is the ENGINE's list now, not a tuple
    in the adapter, and an adapter that has never started its engine has never
    asked it -- so "what can you speak" is a question only a warmed adapter
    can answer. `warm()` is where the asking happens precisely because `info`
    may not go and look (D-25, and the timing test below is what holds it to
    that). Nothing is given up: boot warms every engine port before the first
    satellite handshake, so this is the state the page ever sees.

    Mirrors the same property on the Asr suite. Two things ride on it. The
    hello payload shows the owner which engine is actually running, and a
    HUD naming an engine the daemon is not using is worse than no HUD. And
    `voices` is what lets the settings picker be DISCOVERED rather than
    declared: the schema used to hardcode eleven Kokoro voice names, which was
    wrong the moment a second engine existed (AC-42, closed by AC-41).

    Not asserted here, and now assertable: that `info["voice"]` appears in
    `info["voices"]`. It was unassertable while the suite shared one voice
    name across adapters, which it no longer does -- the `voice` fixture reads
    each adapter's own default. Adding it is a new property and belongs in a
    red-first pass, not smuggled into a cleanup.
    """
    await tts.warm()
    info = tts.info
    assert isinstance(info, dict)
    for key in ("engine", "voice", "voices", "sample_rate"):
        assert key in info, f"info missing {key!r}: {info}"
    # NON-EMPTY, not merely present. `hub._announced_voice()` falls back to
    # this when the setting is blank, and puts the result straight on the
    # wire. An adapter reporting "" would ship a blank label the owner cannot
    # tell from a fault -- AC-7b, which has shipped here once. Presence alone
    # would not have caught it.
    assert isinstance(info["voice"], str) and info["voice"], (
        f"voice must be a non-empty default this engine will actually use, "
        f"got {info['voice']!r}")
    assert isinstance(info["voices"], list) and info["voices"], \
        f"voices must be a non-empty list, got {info['voices']!r}"
    # `{id, language}` per entry since AC-155, and the LANGUAGE is the half
    # that earns the shape change. The daemon has to offer the owner a voice
    # for the language they chose, and the only alternative to the engine
    # saying so is the daemon reading an `af_`/`pf_` prefix -- one engine's
    # naming convention, in the core, which the AC-41 gate forbids.
    for v in info["voices"]:
        assert isinstance(v, dict), (
            f"a voice must be a {{id, language}} entry, got "
            f"{type(v).__name__}: {v!r}")
        assert isinstance(v.get("id"), str) and v["id"], (
            f"a voice must carry a non-empty id: {v!r}")
        assert isinstance(v.get("language", None), str), (
            f"voice {v.get('id')!r} does not say what language it speaks; an "
            f"engine that cannot tell reports an empty string, not nothing: "
            f"{v!r}")
    assert info["voice"] in ports.voice_ids(info), (
        f"the default voice {info['voice']!r} is not one this engine says it "
        f"can speak: {ports.voice_ids(info)}")
    assert info["sample_rate"] == ports.SAMPLE_RATE_OUT, (
        f"an adapter that reports {info['sample_rate']} while the port fixes "
        f"{ports.SAMPLE_RATE_OUT} would be resampled by nobody")


def test_info_is_cheap_enough_for_the_handshake(tts):
    """Reading info must not load a model or start a child process.

    `hub._hello` reads all three engine infos synchronously, ON THE EVENT
    LOOP, every time a device connects. An adapter that lazily starts its
    engine there turns one satellite connecting into a multi-second freeze
    for every satellite, the heartbeat and the worker announcements
    (D-10, and the no-blocking rule in daemon/AGENTS.md).

    Timed rather than compared, and that is the point. The first version of
    this test read `info` twice and asserted the two were equal, which an
    adapter that reloads a model on every single call would also pass -- it
    would just pass slowly. Worse, it built its own `fake` internally while
    taking the parametrized fixture, so the kokoro case ran, went green, and
    never once called `KokoroTts.info` (D-13: a test that survives the
    defect it is named after is not a test).

    The budget is deliberately loose. A model load on this machine is 15.85 s
    for the smallest one measured [plan.md 4.4]; an attribute read is
    microseconds. Anything between is still wrong, and 250 ms is far enough
    from both that a slow CI box cannot make this flap.
    """
    started = time.perf_counter()
    info = tts.info
    elapsed = time.perf_counter() - started
    assert isinstance(info, dict)
    assert elapsed < 0.25, (
        f"reading info took {elapsed * 1000:.0f} ms -- it is read on the "
        f"event loop during the handshake, so this is every satellite "
        f"frozen for that long, each time any device connects")
