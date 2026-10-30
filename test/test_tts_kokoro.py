"""Properties of the real Kokoro adapter that the port cannot state. AC-7b.

`test/conformance/test_tts_conformance.py` holds what EVERY Tts must do. This
file holds what only a real engine can be asked: that the voice argument the
daemon passes actually changes the audio, that the SPEED does too (AC-107),
that the voice list is the engine's own (AC-108), and that a voice the engine
cannot speak fails loudly instead of quietly coming out in some other voice.

Why this file exists at all. The `voice` setting is marked `restart: False`
and its help text says "takes effect on the next reply", the hub reads it per
reply and passes it to `synthesize`, and until the streaming transport landed
the adapter DROPPED it: the sidecar bound one voice from `KOKORO_VOICE` at
boot and warned to stderr. So the settings page reported success, the wire
said `speak_start voice=bm_george`, and the room heard `af_heart` -- the exact
failure AC-7b names, where a control that saves and changes nothing teaches
the user that the whole panel is decoration.

NOT A SLOW FILE ANY MORE, and the split is deliberate (test/AGENTS.md: a
per-engine file is not automatically `slow`). Half of what is asked here is a
CLOCK-free claim about the request the adapter builds -- what it puts in the
`/tts` body, and what it refuses to send at all -- and that is answered by a
stand-in that records the body, in the fast tier, exactly as
`conftest.FakeOpenAI` answers the same question for the completion wire. Only
the cases that need real audio out of a real ONNX graph carry `slow`.
"""
import struct
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import ports                                    # noqa: E402
from daemon.adapters import tts_kokoro                      # noqa: E402

SHORT = "It is twenty past four."

# What the stand-in below answers `GET /voices` with. Deliberately NOT the
# eleven names the adapter used to hardcode: it holds a name that tuple never
# had (`bm_fable`) and two non-English packs the installed engine also ships,
# so a test asserting on this list can tell "asked the engine" from "read its
# own list", and can see the English filter working (AC-108, spec D4).
# `{id, language}` since AC-155: the pool states each voice's language and
# the daemon no longer guesses one from the name's prefix.
POOL_VOICES = [{"id": "af_heart", "language": "en-us"},
               {"id": "af_nova", "language": "en-us"},
               {"id": "am_puck", "language": "en-us"},
               {"id": "bf_lily", "language": "en-gb"},
               {"id": "bm_fable", "language": "en-gb"},
               {"id": "ef_dora", "language": "es"},
               {"id": "jf_alpha", "language": "ja"}]


class _RecordingSidecar:
    """A stand-in for the node sidecar that KEEPS the bodies it was sent.

    `conftest.FakeOpenAI` is this same instrument for the completion wire, and
    it exists for the same reason (AC-38): "the speed is stored" and "the speed
    is on the wire" are different claims and only the second is AC-107. The
    real sidecar can be asked to speak but never asked what it was told.

    It writes the frame layout from the literals in the sidecar's own header
    rather than importing the adapter's constants, so it is an independent
    second end of the wire instead of a mirror of the reader under test.
    """

    def __init__(self, voices):
        self.voices = voices          # None = /voices fails, like a dead pool
        self.bodies: list[dict] = []
        self.voice_requests = 0
        self._runner = None
        self.port = 0

    async def start(self):
        app = web.Application()
        app.router.add_post("/tts", self._tts)
        app.router.add_get("/voices", self._voices)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self._runner.cleanup()

    async def _voices(self, _req):
        self.voice_requests += 1
        if self.voices is None:
            # What a sidecar whose pool never came up actually does: it can
            # answer the socket and not the question.
            return web.Response(status=500, text="TTS service not ready")
        return web.json_response({"voices": self.voices})

    async def _tts(self, req):
        self.bodies.append(await req.json())
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "application/octet-stream"
        await resp.prepare(req)
        pcm = b"\x00\x01" * 240
        await resp.write(_frame(0, ports.SAMPLE_RATE_OUT, pcm))
        await resp.write(_frame(2, 0, b""))
        await resp.write_eof()
        return resp


def _frame(kind: int, rate: int, payload: bytes) -> bytes:
    return (b"BTL1" + struct.pack("<III", kind, rate, len(payload)) + payload)


@asynccontextmanager
async def recording_sidecar(voices=POOL_VOICES):
    rec = _RecordingSidecar(voices)
    await rec.start()
    try:
        yield rec
    finally:
        await rec.stop()


def pointed_at(rec: _RecordingSidecar) -> tts_kokoro.KokoroTts:
    """An adapter that talks to the stand-in instead of spawning node.

    The one poke at an internal is `alive`, and it is the smallest lie that
    works: the adapter's `_ensure_started` spawns a child whenever the sidecar
    is not running, and nothing in the port lets a caller say "it already is".
    No process handle is invented, so `close()` and the atexit sweep still
    find `proc is None` and do nothing -- a fake PID here is how a test suite
    ends up sending taskkill somewhere it did not mean to.
    """
    tts = tts_kokoro.KokoroTts(port=rec.port)
    tts._sidecar.alive = lambda: True
    # The adapter pools one sidecar per (node, port) for the life of the
    # PROCESS, and the OS hands out ephemeral ports again. Dropping this one
    # from the pool keeps a later test from inheriting an instance that is
    # already marked alive and already holds a voice list -- which would make
    # the "cannot be asked" case pass or fail on port luck.
    tts_kokoro._SIDECARS.pop(("node", rec.port), None)
    return tts


# --------------------------------------------------------------------------- #
# AC-107 -- the speed reaches the engine
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_speaking_speed_is_on_the_wire():
    """AC-107, asserted on the body the sidecar received.

    Three layers dropped this number before the criterion existed -- the port
    had no parameter, the `/tts` body had no field, and the sidecar passed
    only the voice to `synthesizeStream` -- and every one of those failures
    sounds exactly like a working Butler. Storing the setting proves nothing;
    this is the only claim that matters (AC-38).
    """
    async with recording_sidecar() as rec:
        tts = pointed_at(rec)
        chunks = [c async for c in tts.synthesize(SHORT, "af_heart",
                                                  speed=0.8)]
        assert chunks, "the stand-in produced no audio; the test is broken"
        # And the DEFAULT is on the wire too: an adapter that omits the field
        # when nobody asked leaves the engine to guess, which is the same
        # silent-default failure one layer down.
        async for _ in tts.synthesize(SHORT, "af_heart"):
            pass

    fast, plain = rec.bodies[0], rec.bodies[1]
    assert fast["speed"] == 0.8, (
        f"the sidecar was sent {fast!r}: the speed the caller asked for never "
        f"reached the engine")
    assert plain["speed"] == 1.0, (
        f"the sidecar was sent {plain!r}: the port's default speed is not on "
        f"the wire, so the engine is deciding it")


@pytest.mark.asyncio
@pytest.mark.parametrize("speed", [0.0, -1.0, float("nan"), float("inf")],
                         ids=["zero", "negative", "nan", "infinite"])
async def test_a_speed_the_engine_cannot_honour_is_refused_loudly(speed):
    """AC-107. Refused, never clamped and never dropped.

    kokoro-js puts this number straight into a float32 tensor and validates
    nothing, so a zero, a negative or a NaN is not an error the engine will
    report -- it is audio that comes out wrong, or does not come out. Silently
    substituting 1.0 would be worse: the owner sets a speed, hears no change,
    and learns the panel is decoration (AC-7b).

    Asserted with the stand-in rather than a bare `pytest.raises`, because
    WHERE it is refused is the property: nothing may reach the engine.
    """
    async with recording_sidecar() as rec:
        tts = pointed_at(rec)
        with pytest.raises(ValueError):
            _ = [c async for c in tts.synthesize(SHORT, "af_heart",
                                                 speed=speed)]
    assert rec.bodies == [], (
        f"speed={speed!r} was sent to the engine anyway: {rec.bodies!r}")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_speed_changes_the_audio_the_engine_produces():
    """AC-107 against the real engine: slower speech is longer audio.

    The wire test above proves the number was sent. This is the other half and
    the one the room hears -- the same sentence at 0.7 and at 1.3 must not
    come back the same size. An engine that accepted the field and ignored it
    would pass every other test in this file.
    """
    tts = ports.build(ports.TTSS, "kokoro")
    slow = sum([len(c) async for c in tts.synthesize(SHORT, "af_heart",
                                                     speed=0.7)])
    quick = sum([len(c) async for c in tts.synthesize(SHORT, "af_heart",
                                                      speed=1.3)])
    assert slow and quick, "one of the two speeds produced no audio at all"
    assert slow > quick, (
        f"0.7x produced {slow} bytes and 1.3x produced {quick}: the speed "
        f"reached the sidecar and the engine did nothing with it")


# --------------------------------------------------------------------------- #
# AC-108 -- the voice list is the engine's
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_voice_list_comes_from_the_engine():
    """AC-108. `info["voices"]` is what the pool reported, filtered to English.

    The adapter used to answer this from an eleven-name tuple while the pool
    reported 28, so the picker was honest about what the DAEMON declared and
    the daemon under-declared by a factor of two and a half -- AC-42's
    complaint exactly: a list written into the daemon is wrong the day the
    engine changes, and wrong invisibly.

    `bm_fable` proves the list was asked for rather than recited: the tuple
    this replaced never carried that name.

    The adapter passes the pool's answer through WHOLE since AC-155, non-
    English packs included. It used to drop everything outside
    `ENGLISH_PREFIXES`, which was the right instinct built on a guess -- it
    read a language off a NAME, and it hardcoded an answer that is now the
    owner's `language` setting. Which voices to OFFER is the daemon's
    decision now, made against that setting; this port reports what the
    engine can say.
    """
    async with recording_sidecar() as rec:
        tts = pointed_at(rec)
        await tts.warm()
        voices = tts.info["voices"]

    assert rec.voice_requests == 1, (
        f"the sidecar was asked for its voices {rec.voice_requests} times; "
        f"warm() is the one place that asks")
    assert voices == POOL_VOICES, (
        f"info reported {voices!r}, not what the pool said it has "
        f"({POOL_VOICES!r})")
    assert {v["id"] for v in voices} >= {"ef_dora", "jf_alpha"}, (
        "the adapter is still filtering non-English packs out of the port; "
        "that decision moved to the daemon with AC-155")


@pytest.mark.asyncio
async def test_reading_info_never_asks_the_sidecar_anything():
    """D-25, and the reason AC-108 is not a one-liner.

    `info` is a synchronous property read ON THE EVENT LOOP during the hello
    handshake. An adapter that answered "what can you speak" by asking the
    sidecar would turn one device connecting into a freeze for every device --
    which is why the list is fetched at `warm()` and cached, and why this test
    counts requests rather than timing anything: a cache that refreshes itself
    on every read is fast exactly until the sidecar is slow.
    """
    async with recording_sidecar() as rec:
        tts = pointed_at(rec)
        await tts.warm()
        asked_after_warm = rec.voice_requests
        for _ in range(5):
            _ = tts.info
        assert rec.voice_requests == asked_after_warm, (
            "reading info went and asked the sidecar; that read happens on "
            "the event loop for every satellite that connects")


@pytest.mark.asyncio
async def test_a_sidecar_that_cannot_be_asked_for_voices_fails_loudly():
    """AC-108. Loud, never a quietly empty picker.

    Same rule `butlerd.LocalListUnavailable` and `models.ModelsUnavailable`
    already hold: an empty list rendered as though all were well is how a
    setting silently stops working. The daemon's warmup catches this, prints
    it and boots anyway (D-1) -- what must not happen is warm() returning
    happily with nothing to show.
    """
    async with recording_sidecar(voices=None) as rec:
        tts = pointed_at(rec)
        with pytest.raises(tts_kokoro.SidecarError):
            await tts.warm()
        assert tts.info["voices"] == [], (
            "the adapter invented a voice list for a pool that would not "
            "answer")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_real_engine_offers_more_than_the_daemon_used_to_declare():
    """AC-108 against the real sidecar. MEASURED 2026-08-19: the pool reports
    28 voices and the adapter offered 11 of them.

    The number is not asserted exactly -- upgrading kokoro-js is allowed to
    change it, and a test that pins it would fail for the right reason on the
    wrong day. What is asserted is that it is no longer the eleven, that it
    holds a name that tuple never had, and that every entry says what
    language it speaks (AC-155).
    """
    tts = ports.build(ports.TTSS, "kokoro")
    await tts.warm()
    voices = tts.info["voices"]

    assert len(voices) > 11, (
        f"the engine reports {len(voices)} voices; the deleted tuple had 11")
    assert "bm_fable" in ports.voice_ids(tts.info), (
        f"a voice the old tuple never carried is missing: {voices!r}")
    assert all(isinstance(v, dict) and v.get("id") for v in voices), (
        f"a voice reached the port without an id: {voices!r}")
    assert all("language" in v for v in voices), (
        f"the engine reported a voice without saying what language it "
        f"speaks, which is the fact AC-155 exists for: {voices!r}")


# --------------------------------------------------------------------------- #
# The voice itself, against the real engine
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_requested_voice_reaches_the_engine():
    """Two voices, one sentence, different audio.

    An adapter that ignores `voice` passes every other test in the suite:
    the audio is well-formed, correctly aligned and the right length. Only
    comparing two voices can tell "spoke as asked" from "spoke anyway".
    """
    tts = ports.build(ports.TTSS, "kokoro")
    first = b"".join([c async for c in tts.synthesize(SHORT, "af_heart")])
    second = b"".join([c async for c in tts.synthesize(SHORT, "bm_george")])

    assert first and second, "one of the voices produced no audio at all"
    assert first != second, (
        "both voices produced byte-identical audio; the adapter is speaking "
        "in whatever voice the sidecar booted with and discarding the one "
        "the daemon asked for")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_voice_the_engine_cannot_speak_fails_loudly():
    """Fail fast, and prove the error survives a streamed response.

    Two things at once. A voice the engine does not have must raise rather
    than silently substituting another one -- substitution is how the defect
    above stayed invisible for a day. And because synthesis now streams, the
    HTTP status is already 200 by the time kokoro rejects the voice, so this
    is also the only test that exercises the error carried IN the stream. A
    transport that dropped that frame would end the reply early and sound
    exactly like a short answer.
    """
    tts = ports.build(ports.TTSS, "kokoro")
    with pytest.raises(tts_kokoro.SidecarError):
        _ = [c async for c in tts.synthesize(SHORT, "xx_nonexistent")]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_bad_voice_does_not_poison_the_next_reply():
    """D-1 at the adapter: a refused voice must not leave the engine unusable.

    The failure path tears down a streaming response mid-flight; if that left
    the sidecar's job registry or the worker's FIFO in a bad state, the next
    turn would be silent -- and silence is indistinguishable from Butler
    ignoring the user.
    """
    tts = ports.build(ports.TTSS, "kokoro")
    with pytest.raises(tts_kokoro.SidecarError):
        _ = [c async for c in tts.synthesize(SHORT, "xx_nonexistent")]

    chunks = [c async for c in tts.synthesize(SHORT, "af_heart")]
    assert chunks, "a rejected voice left the engine unable to speak"


# --------------------------------------------------------------------------- #
# THE PHONEMIZER, and the accents it used to eat.
# --------------------------------------------------------------------------- #
PT_ACCENTED = "A manuten\u00e7\u00e3o do cora\u00e7\u00e3o \u00e9 uma quest\u00e3o s\u00e9ria."
PT_PLAIN = "A manutencao do coracao e uma questao seria."
EN_MIXED = "Hello, I am Butler. 42 tests passed in 2.5 seconds."

#: The phonemize call, run in node because the phonemizer is a JS module. Kept
#: as one invocation so the espeak WASM is instantiated once per case rather
#: than once per assertion.
_PHON_JS = """
import { phonemize, languageOf } from './daemon/engines/tts/tts-phonemes.js';
const text = process.argv[2], voice = process.argv[3];
process.stdout.write(await phonemize(text, languageOf(voice)));
"""


def _phonemize(text: str, voice: str) -> str:
    """Phonemes for `text` in `voice`'s language, from the REAL phonemizer."""
    import subprocess
    import tempfile
    repo = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", dir=repo,
                                     delete=False, encoding="utf-8") as fh:
        fh.write(_PHON_JS)
        script = fh.name
    try:
        out = subprocess.run(
            ["node", script, text, voice], cwd=str(repo), capture_output=True,
            text=True, encoding="utf-8", timeout=180)
    finally:
        Path(script).unlink(missing_ok=True)
    assert out.returncode == 0, f"node failed: {out.stderr[-600:]}"
    return out.stdout.strip()


@pytest.mark.slow
def test_an_accent_does_not_multiply_the_phonemes():
    """D-37: espeak read UTF-8 as Latin-1 and SPOKE THE CHARACTER NAMES.

    `-b=1` on the espeak-ng command line names the input encoding, and in
    this WASM build any explicit value makes it read the UTF-8 bytes
    Emscripten put into argv as single-byte characters. Every accented letter
    became mojibake, and espeak then pronounced the NAME of each character it
    thought it found -- an a-tilde as "a til", a c-cedilla and a-tilde pair as
    "libra" and "simbolo de copyright" and "ordinal feminino".

    THE ASSERTION IS A COUNT, not a string, for two reasons. Pinning the
    exact phoneme string would break on any espeak-ng upgrade and teach the
    next reader to delete the test; and this file is pure ASCII by project
    rule, so the IPA cannot be written down here anyway. The count is what
    separates the two cases unmistakably: MEASURED 2026-09-10, the accented
    sentence gave 8 phoneme groups when correct and 30 when mojibake, against
    the unaccented control's 8. Anything past 1.6x the control is the bug.
    """
    accented = _phonemize(PT_ACCENTED, "pf_dora").split()
    plain = _phonemize(PT_PLAIN, "pf_dora").split()

    assert plain, "the phonemizer produced nothing for plain Portuguese"
    assert accented, "the phonemizer produced nothing for accented Portuguese"
    assert len(accented) <= len(plain) * 1.6, (
        f"the accented sentence phonemized to {len(accented)} groups against "
        f"{len(plain)} for the same sentence with the accents stripped. "
        f"espeak is reading the UTF-8 bytes as single characters and speaking "
        f"their NAMES; the cause is a `-b` flag on its command line, and the "
        f"fix is to remove it rather than to pick a different value (D-37).")


@pytest.mark.slow
def test_the_accented_sentence_is_not_the_stripped_one():
    """The control that makes the count above mean something.

    A phonemizer that silently DROPPED every accent would also satisfy the
    count -- and would be a different bug with the same shape, since Portuguese
    nasal vowels carry meaning. So the two must not phonemize alike.
    """
    accented = _phonemize(PT_ACCENTED, "pf_dora")
    plain = _phonemize(PT_PLAIN, "pf_dora")
    assert accented != plain, (
        "accented and unaccented Portuguese produced IDENTICAL phonemes, so "
        "the accents are being stripped rather than pronounced")


@pytest.mark.slow
def test_english_phonemes_are_untouched_by_the_encoding_fix():
    """The regression half: English must not move.

    The `-b` flag was byte-identical for English, which is exactly why it
    survived so long. If a future change to the espeak arguments moves
    English, that is a bigger deal than the Portuguese fix and must be seen.
    """
    out = _phonemize(EN_MIXED, "bm_george")
    assert out, "the phonemizer produced nothing for English"
    assert len(out.split()) >= 8, (
        f"English phonemized to only {len(out.split())} groups, which is "
        f"fewer than the sentence has words: {out!r}")


@pytest.mark.slow
def test_a_decomposed_accent_is_normalised_before_phonemizing():
    """D-37's second half, and the INTERMITTENT one.

    A decomposed a-tilde is TWO codepoints -- a bare `a` and a combining
    tilde -- and espeak reads the base letter and throws the mark away.
    MEASURED before the fix: the same sentence in NFD turned the nasal in
    "nao-with-tilde" into "nau" and lost every other accent with it.

    Nothing upstream normalises, so whatever the model emitted is what
    reaches the phonemizer, and a model may emit either form. That is what
    makes this one intermittent where the argv defect was constant -- and it
    is why the assertion compares the two FORMS rather than pinning a string:
    the only property that matters is that they cannot disagree.
    """
    import unicodedata
    nfc = unicodedata.normalize("NFC", PT_ACCENTED)
    nfd = unicodedata.normalize("NFD", PT_ACCENTED)
    assert nfc != nfd, (
        "the fixture has no composable accent in it, so this test would "
        "pass on any implementation")

    assert _phonemize(nfd, "pf_dora") == _phonemize(nfc, "pf_dora"), (
        "the same sentence phonemizes differently depending on whether its "
        "accents are composed. espeak reads the base letter and drops the "
        "combining mark, so NFD text loses its accents silently (D-37).")
