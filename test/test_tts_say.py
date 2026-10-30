"""One reply, read aloud again on demand. AC-183, `docs/mic-optional.md`.

    POST /api/tts/say  {"text": "..."}  ->  200 audio/wav

The page keeps no audio: `playChunk` posts samples into a worklet and never
retains them. So "play this message again" is RE-SYNTHESIS, and this route is
`tts_preview`'s shape with the text supplied instead of a voice.

Three properties carry it and each has a test, because each one is a way the
control could lie:

* it CHANGES NO SETTING -- replaying a reply is not choosing anything;
* it works while `speak` is OFF, which is the whole point of it. Muting
  removes the audio from every reply (AC-110) and this is the one control
  that asks for a piece of it back, so a route that read `speak` would make
  the button dead exactly when it is wanted;
* it cannot collide with a live turn, the same handle rule `tts_preview`
  carries -- and it needs its OWN handle rather than the preview's, or an
  audition started in the settings surface would abort a replay and vice
  versa.

It is refused by LENGTH rather than truncated, and the ceiling is DERIVED from
`hub.TYPED_MAX_CHARS` rather than written down a second time (D-12). Half a
reply read aloud and then stopping sounds exactly like the engine failing
mid-sentence, which is the one failure this route must not imitate.
"""
import io
import wave

import aiohttp
import pytest

from conftest import StubTts, attach_session, make_hub
from daemon import butlerd, ports
from daemon.hub import SPEAKING, THINKING

SAMPLE = "That is the second thing you asked about, and it is already done."


@pytest.mark.asyncio
async def test_a_message_comes_back_as_playable_wav(daemon):
    """The page plays these bytes itself, so the CONTAINER is the contract --
    `tts_preview`'s reason, and the kokoro adapter's `parse_frame_header` is
    this project's cautionary tale about a rate that quietly became wrong."""
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{daemon}/api/tts/say",
                          json={"text": SAMPLE}) as r:
            assert r.status == 200, f"say returned {r.status}: {await r.text()}"
            assert r.headers.get("Content-Type", "").startswith("audio/wav"), (
                f"served as {r.headers.get('Content-Type')!r}; a browser will "
                f"not decode this")
            body = await r.read()

    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE", (
        f"not a WAV container: {body[:16]!r}")
    with wave.open(io.BytesIO(body), "rb") as w:
        assert w.getnchannels() == 1, f"{w.getnchannels()} channels, want mono"
        assert w.getsampwidth() == 2, f"{w.getsampwidth()}-byte samples"
        assert w.getframerate() == ports.SAMPLE_RATE_OUT, (
            f"declared {w.getframerate()} Hz; the port is "
            f"{ports.SAMPLE_RATE_OUT} Hz")
        assert w.getnframes() > 0, "a WAV header with no audio behind it"


@pytest.mark.asyncio
async def test_replaying_a_message_changes_no_setting(daemon):
    """The whole settings document, before and after, for AC-109's reason: a
    route that helpfully applied and restored a value would be caught by this
    comparison and would not be caught by watching one key."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/settings") as r:
            before = await r.json()
        async with s.post(f"{daemon}/api/tts/say",
                          json={"text": SAMPLE}) as r:
            assert r.status == 200, await r.text()
            await r.read()
        async with s.get(f"{daemon}/api/settings") as r:
            after = await r.json()

    assert before == after, (
        "replaying a message changed the settings; this control exists to be "
        "consequence-free (AC-183)")


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "},
                                  {"text": 7}, {"text": None}])
async def test_a_body_with_nothing_to_say_is_refused_by_name(daemon, body):
    """400 and a reason, never a 200 with a 44-byte header in it. A WAV with
    no audio plays as silence, which reads as "this message has no sound in
    it" rather than "the request was wrong" (AC-7b)."""
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{daemon}/api/tts/say", json=body) as r:
            assert r.status == 400, f"{body!r} answered {r.status}, not 400"
            answer = await r.json()
    assert answer.get("error"), f"a 400 with no reason in it: {answer}"


@pytest.mark.asyncio
async def test_a_message_past_the_ceiling_is_refused_and_not_truncated(daemon):
    """The ceiling is READ from the derivation rather than written down again.

    `test_typed_turn.py` records why that trade is deliberate: a test holding
    a second copy of the number pins the VALUE, and the value is allowed to
    move with `dictate_max_seconds`. What must not move is the CHECK, so the
    assertion is the refusal and the character below it.
    """
    ceiling = butlerd.SAY_MAX_CHARS
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{daemon}/api/tts/say",
                          json={"text": "a" * (ceiling + 1)}) as r:
            too_long = r.status
            answer = await r.json()
        async with s.post(f"{daemon}/api/tts/say",
                          json={"text": "a" * ceiling}) as r:
            at_ceiling = r.status
            await r.read()

    assert too_long == 413, f"a message past the ceiling answered {too_long}"
    assert str(ceiling) in str(answer), (
        f"the refusal does not name the ceiling: {answer}")
    assert at_ceiling == 200, (
        f"the character below the ceiling was refused with {at_ceiling}; the "
        f"check is off by one")


# --------------------------------------------------------------------------- #
# the failures a fake engine can be MADE to have
# --------------------------------------------------------------------------- #
class DeadTts(StubTts):
    """Raises the way a sidecar that is not running does."""

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        raise RuntimeError("the sidecar is not answering")
        yield b""                                     # pragma: no cover


class MuteTts(StubTts):
    """Answers, and produces no audio at all -- the quieter failure."""

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        return
        yield b""                                     # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("tts", [DeadTts, MuteTts])
async def test_a_synthesis_failure_is_a_loud_502(tmp_path, tts):
    """Never empty audio, `tts_preview`'s rule for `tts_preview`'s reason."""
    hub = make_hub(tmp_path)
    hub.tts = tts()

    resp = await butlerd.tts_say(hub, {"text": SAMPLE})

    assert resp.status == 502, f"answered {resp.status}, not 502"


@pytest.mark.asyncio
async def test_a_muted_butler_can_still_be_asked_to_say_one_message(tmp_path):
    """AC-183's reason for existing, asserted rather than assumed.

    `speak` off removes the audio from every reply (AC-110). This control is
    how the owner gets one piece of it back, so a route that consulted the
    setting would be dead exactly when it is wanted -- and it would be dead
    SILENTLY, because the button would still be there.
    """
    from daemon.settings import Settings

    hub = make_hub(tmp_path)
    hub.tts = RecordingTts()
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("speak", False)
    assert not hub._speaking(), (
        "the fixture did not actually mute this daemon, so the assertion "
        "below would pass against a route that reads the setting (D-13)")

    resp = await butlerd.tts_say(hub, {"text": SAMPLE})

    assert resp.status == 200, (
        f"a muted daemon refused to replay one message: {resp.body!r}")
    assert hub.tts.said == [SAMPLE], (
        f"the engine was asked for something else: {hub.tts.said}")


class RecordingTts(StubTts):
    """Records what it was asked to say, in which voice, under which handle."""

    def __init__(self):
        super().__init__()
        self.handles: list = []
        self.said: list = []
        self.voices: list = []

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.handles.append(handle)
        self.said.append(text)
        self.voices.append(voice)
        yield b"\x00\x00" * 240


@pytest.mark.asyncio
async def test_a_replay_during_a_live_reply_cannot_collide_with_it(tmp_path):
    """`tts_preview`'s handle rule, and one more claim beside it.

    The daemon uses the TURN ID as the synthesis handle, so a replay
    registered under one would be cancelled by that turn's barge-in or would
    take the reply's abort with it. It must also differ from the PREVIEW's
    handle: both are consequence-free auditions, and a shared handle means an
    audition started in the settings surface aborts a replay running on the
    main page.
    """
    hub = make_hub(tmp_path)
    hub.tts = RecordingTts()
    s = attach_session(hub)
    s.turn = 909
    s.state = THINKING

    await hub._speak(s, 909, "The first sentence.", "reflex")
    resp = await butlerd.tts_say(hub, {"text": SAMPLE})
    await hub._speak(s, 909, "The second sentence.", "reflex")

    assert resp.status == 200, f"the replay failed: {resp.body!r}"
    replay = hub.tts.handles[1]
    assert not isinstance(replay, int), (
        f"the replay registered under {replay!r}, which is the shape of a "
        f"turn id -- barge-in on that turn would cut the replay off")
    assert replay != butlerd.PREVIEW_HANDLE, (
        "the replay and the voice audition share one handle, so each can "
        "abort the other")
    assert hub.tts.handles[0] == 909 and hub.tts.handles[2] == 909, (
        f"the reply's own synthesis lost its turn handle: {hub.tts.handles}")
    assert s.spoken == ["The first sentence.", "The second sentence."], (
        f"the live reply did not survive the replay: {s.spoken}")
    assert s.turn == 909 and s.state == SPEAKING, (
        f"the replay disturbed the turn it played over: turn={s.turn!r} "
        f"state={s.state!r}")


# --------------------------------------------------------------------------- #
# AC-214: a replay detects on the text it is about to speak
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("text,want", [
    ("Run the tests again and tell me which ones failed.", "en-US"),
    ("Voce pode rodar os testes e me dizer quantos falharam?", "pt-BR"),
])
async def test_a_replay_is_read_in_the_MESSAGES_own_language(tmp_path, text,
                                                             want):
    """AC-214. There is no turn and no session here, so the text decides.

    A replay is a button on a message that may be an hour old, and the
    conversation may have moved on since. Reading the conversation's CURRENT
    language would answer a Portuguese message in an English voice -- the
    failure this whole feature removes, arriving through the one control that
    exists to hear a reply again.

    Teeth: the two rows differ only in what is being replayed. A route that
    ignored the text passes one of them by coincidence, never both.
    """
    from daemon.settings import AUTO_LANGUAGE, Settings

    hub = make_hub(tmp_path)
    hub.tts = RecordingTts()
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("language", AUTO_LANGUAGE)

    resp = await butlerd.tts_say(hub, {"text": text})

    assert resp.status == 200, f"the replay failed: {resp.body!r}"
    assert hub.tts.voices, "the engine was never asked for a voice"
    assert hub.tts.voices[0] in ports.voice_ids(hub.tts.info, want), (
        f"{text[:30]!r} was replayed with {hub.tts.voices[0]!r}, which does "
        f"not speak {want}")
