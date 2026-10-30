"""A voice can be heard before it is chosen. AC-109.

    GET /api/tts/preview?voice=<name>  ->  200 audio/wav

A plain HTTP route returning bytes the page plays itself, and NOT a socket
frame: `daemon/AGENTS.md` says nothing is spoken outside a turn and nothing
is sent between turns either, because the page drops frames for a turn it
does not own (D-6). A preview through the socket would need an exception
carved into that rule; this keeps the rule intact.

Three things the route must never do, one test each:

* change a setting -- auditioning a voice is not choosing it;
* answer 200 with an empty body -- a synthesis failure is a LOUD 502, the
  same rule `LocalListUnavailable` already holds for an empty voice list;
* disturb a live turn -- the adapter registers syntheses by `handle` so
  `abort` can find them, and a preview sharing a turn's handle would make
  barge-in cancel the preview or the preview cancel the reply.
"""
import io
import wave

import aiohttp
import pytest

from conftest import StubTts, attach_session, make_hub
from daemon import butlerd, ports
from daemon.hub import SPEAKING, THINKING


# --------------------------------------------------------------------------- #
# over the wire, against the daemon a satellite actually talks to
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_named_voice_comes_back_as_playable_wav(daemon):
    """The page plays these bytes itself, so the CONTAINER is the contract.

    The port yields raw 24 kHz mono int16 PCM with no header at all
    (`ports.SAMPLE_RATE_OUT`), so the daemon has to frame it. Parsed with the
    `wave` module rather than compared byte by byte: what matters is that a
    decoder agrees about the rate, the channel count and the sample width,
    and `parse_frame_header` in the kokoro adapter is this project's own
    cautionary tale about a sample rate that quietly became wrong -- it
    sounds slightly off and nothing else reports it.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/tts/preview?voice=fake_low") as r:
            assert r.status == 200, f"preview returned {r.status}: {await r.text()}"
            assert r.headers.get("Content-Type", "").startswith("audio/wav"), (
                f"served as {r.headers.get('Content-Type')!r}; a browser will "
                f"not decode this")
            body = await r.read()

    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE", (
        f"not a WAV container: {body[:16]!r}")
    with wave.open(io.BytesIO(body), "rb") as w:
        assert w.getnchannels() == 1, f"{w.getnchannels()} channels, want mono"
        assert w.getsampwidth() == 2, f"{w.getsampwidth()}-byte samples, want int16"
        assert w.getframerate() == ports.SAMPLE_RATE_OUT, (
            f"declared {w.getframerate()} Hz; the port is "
            f"{ports.SAMPLE_RATE_OUT} Hz")
        assert w.getnframes() > 0, "a WAV header with no audio behind it"


@pytest.mark.asyncio
async def test_auditioning_a_voice_changes_no_setting(daemon):
    """AC-109's whole point: hearing a voice must not be choosing it.

    The entire settings document, before and after, because the risk is not
    only `voice`: a route that helpfully "applied then restored" would be
    caught by the same comparison, and a route that changed something else
    would not be caught by watching one key.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/settings") as r:
            before = await r.json()
        async with s.get(f"{daemon}/api/tts/preview?voice=fake_high") as r:
            assert r.status == 200, await r.text()
            await r.read()
        async with s.get(f"{daemon}/api/settings") as r:
            after = await r.json()

    assert before == after, (
        "auditioning a voice changed the settings; a preview that commits "
        "what it plays is a control the owner cannot back out of (AC-109)")


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?voice=no_such_voice", "?voice=", ""])
async def test_a_voice_the_engine_does_not_have_is_refused_by_name(daemon,
                                                                   query):
    """400 naming what was asked for, the way `/api/models` refuses an
    unknown target: a caller's mistake is a different fact from an engine
    that could not answer, and flattening either into silence or a 200 with
    no audio is what teaches the owner the panel is decoration (AC-7b)."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/tts/preview{query}") as r:
            assert r.status == 400, (
                f"{query!r} answered {r.status}, not 400")
            body = await r.json()
    assert body.get("error"), f"a 400 with no reason in it: {body}"


# --------------------------------------------------------------------------- #
# the failures a fake engine can be MADE to have
# --------------------------------------------------------------------------- #
class DeadTts(StubTts):
    """Raises the way a sidecar that is not running does."""

    VOICES = ({"id": "stub_a", "language": "en-US"},
              {"id": "stub_b", "language": "pt-BR"})

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        raise RuntimeError("the sidecar is not answering")
        yield b""                                     # pragma: no cover


class MuteTts(StubTts):
    """Answers, and produces no audio at all.

    The quieter failure and the one worth a test: an aborted or broken engine
    returns an empty stream, and a 200 carrying a 44-byte WAV header plays as
    silence -- which is indistinguishable from a voice the owner cannot hear.
    """

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        return
        yield b""                                     # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("tts", [DeadTts, MuteTts])
async def test_a_synthesis_failure_is_a_loud_502(tmp_path, tts):
    """Never empty audio. A preview that answers 200 with nothing in it is
    the empty-picker defect one layer down (AC-108, `LocalListUnavailable`):
    it renders as "this voice sounds like silence" instead of "nobody could
    be asked"."""
    hub = make_hub(tmp_path)
    hub.tts = tts()

    resp = await butlerd.tts_preview(hub, "stub_a")

    assert resp.status == 502, f"answered {resp.status}, not 502"
    assert b"stub_a" in resp.body, (
        f"the failure does not name the voice that failed: {resp.body!r}")


class RecordingTts(StubTts):
    """Records the handle every synthesis was registered under, and paces the
    reply so a preview can genuinely arrive in the middle of one."""

    def __init__(self):
        super().__init__()
        self.handles: list = []

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.handles.append(handle)
        yield b"\x00\x00" * 240


@pytest.mark.asyncio
async def test_a_preview_during_a_live_reply_cannot_collide_with_it(tmp_path):
    """The failure this route can actually cause (docs/voice-control.md 7).

    The adapter registers in-flight synthesis by `handle` so `abort(handle)`
    can find it, and the daemon uses the TURN ID as that handle. A preview
    that registered under a turn id -- or under the same handle as a reply --
    would be cancelled by that turn's barge-in, or would take the reply's
    abort with it. So the preview's handle must not be a turn id at all, and
    the reply it overlapped must still be complete.
    """
    hub = make_hub(tmp_path)
    hub.tts = RecordingTts()
    s = attach_session(hub)
    s.turn = 909
    s.state = THINKING

    await hub._speak(s, 909, "The first sentence.", "reflex")
    resp = await butlerd.tts_preview(hub, "stub_b")
    await hub._speak(s, 909, "The second sentence.", "reflex")

    assert resp.status == 200, f"the preview failed: {resp.body!r}"
    preview = hub.tts.handles[1]
    assert not isinstance(preview, int), (
        f"the preview registered under {preview!r}, which is the shape of a "
        f"turn id -- barge-in on that turn would abort the audition, and "
        f"aborting the audition would silence the turn (AC-109)")
    assert preview != 909, (
        f"the preview shares a handle with the live turn: {hub.tts.handles}")
    assert hub.tts.handles[0] == 909 and hub.tts.handles[2] == 909, (
        f"the reply's own synthesis lost its turn handle: {hub.tts.handles}")
    assert s.spoken == ["The first sentence.", "The second sentence."], (
        f"the live reply did not survive the preview: {s.spoken}")
    assert s.turn == 909 and s.state == SPEAKING, (
        f"the preview disturbed the turn it played over: turn={s.turn!r} "
        f"state={s.state!r}")
