"""Dictation: a clip in, its text out, and never beside another decode.

`docs/desk-hotkeys.md`. The desk agent captures while a key is held, POSTs the
clip at `POST /api/dictate`, and pastes what comes back. Butler never speaks
and no brain is involved -- this route is the `Asr` port and nothing else.

Three claims are what this file exists for.

**AC-115 -- a failure never writes.** Every failure answers a status the
caller can tell apart and NEVER an empty 200. The desk agent pastes whatever
comes back into whatever the owner had focused, so an error sentence in a
200 body ends up inside their document, and Butler cannot undo it.

**AC-116 -- the recognizer is never used by two callers at once.** Two
`Session`s on one `transcribe_cpp.Model` return EMPTY transcripts on BOTH
sides, 9 times out of 9, silently
(`test/manual/spike_ambient_concurrent.py`). Dictation is the third caller of
that model, after the turn pipeline and ambient. It is REFUSED rather than
queued: a key released with nothing appearing, then text arriving four seconds
later into whatever is focused by then, is worse than a refusal.

**The ceiling is a setting.** A held key can run for minutes, and the daemon
holds no more of a room than it would ever decode -- the same rule as the
ambient body ceiling, for the same reason (D-1).
"""
import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, attach_session, silence_pcm,      # noqa: E402
                      tone_pcm)
from daemon import butlerd, hub as hub_mod, ports                 # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                      # noqa: E402
from daemon.adapters.brain_fake import FakeBrain                  # noqa: E402
from daemon.modes import ModeStore                                # noqa: E402
from daemon.settings import BY_KEY, Settings                      # noqa: E402

DICTATED = "what time is it"          # FakeAsr.DEFAULT_SCRIPT, joined


class RecordingAsr(FakeAsr):
    """The fake recognizer, plus a record of every whole-clip decode.

    Counting the calls is what makes "refused" different from "decoded and
    then discarded": a daemon that ran the decode and threw the text away
    would answer 409 and still corrupt the live turn's transcript, which is
    the entire defect AC-116 exists to prevent.
    """

    def __init__(self, fail: bool = False, gate: asyncio.Event | None = None):
        super().__init__()
        self.clips: list[bytes] = []
        self._fail = fail
        self._gate = gate

    async def transcribe(self, pcm: bytes, cfg) -> str:
        self.clips.append(pcm)
        if self._gate is not None:
            await self._gate.wait()
        if self._fail:
            raise RuntimeError("the decoder fell over")
        return await super().transcribe(pcm, cfg)


def dictate_hub(tmp_path, asr=None):
    """A Hub on a settings store of its own.

    Its own `Settings` because the suite must never write the owner's
    `state/settings.json` (test/AGENTS.md).
    """
    settings = Settings(tmp_path / "settings.json")
    hub = hub_mod.Hub(asr=asr if asr is not None else RecordingAsr(),
                      tts=QuietTts(),
                      brain=FakeBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=settings)
    return hub


async def dictate_client(hub, monkeypatch):
    """A test client on the REAL app, wired to this hub."""
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    return client


async def post_dictate(hub, body: bytes, monkeypatch) -> tuple[int, dict]:
    """POST one clip at the real route, and answer `(status, payload)`.

    The body is read BEFORE the client is closed. Returning the response
    object instead makes every assertion on a body fail with
    `ClientConnectionError` from the `finally`, which reads like a daemon
    fault and is a fault in the harness.
    """
    client = await dictate_client(hub, monkeypatch)
    try:
        answer = await client.post(
            "/api/dictate", data=body,
            headers={"Content-Type": "application/octet-stream"})
        return answer.status, await answer.json()
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# the setting
# --------------------------------------------------------------------------- #
def test_the_ceiling_is_a_setting_and_bounds_the_body():
    """A held key can run for minutes, and the body ceiling comes from here.

    Written as a schema assertion rather than a constant check because
    `butlerd.DICTATE_MAX_BYTES` is DERIVED from this maximum -- the ambient
    rule (`daemon/AGENTS.md`), so the ceiling is never written down twice and
    cannot drift from the control the owner actually turns.
    """
    spec = BY_KEY.get("dictate_max_seconds")
    assert spec is not None, (
        "the body ceiling is derived from this setting; without it in the "
        "schema the route bounds nothing and a stuck key holds arbitrary "
        "memory in the daemon (D-1)")
    assert spec.kind == "number"
    assert spec.minimum is not None and spec.minimum >= 1, (
        f"a ceiling of zero seconds refuses every dictation: {spec.minimum!r}")
    assert spec.maximum is not None and spec.maximum <= 600, (
        f"the ceiling is what one decode can pay for at 15-18x real time "
        f"[MEASURED]: {spec.maximum!r}")
    assert butlerd.DICTATE_MAX_BYTES == (
        int(spec.maximum) * ports.SAMPLE_RATE_IN * 2), (
        "the byte ceiling is derived from the setting's own maximum, or the "
        "two drift and the control stops describing the route")


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_voiced_clip_comes_back_as_text(tmp_path, monkeypatch):
    """AC-113's daemon half: the clip is decoded and the text is the answer."""
    hub = dictate_hub(tmp_path)
    clip = tone_pcm(400)

    status, payload = await post_dictate(hub, clip, monkeypatch)

    assert status == 200, (
        f"a voiced clip was not decoded: {status}")
    assert payload["text"] == DICTATED, (
        "the route answered something other than what the recognizer said")
    assert hub.asr.clips == [clip], (
        f"the bytes that reached the recognizer are not the bytes that were "
        f"posted: {len(hub.asr.clips)} decodes")


# --------------------------------------------------------------------------- #
# AC-115 -- a failure never writes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_quiet_clip_is_refused_rather_than_answered_empty(
        tmp_path, monkeypatch):
    """AC-115. An empty 200 is the one answer this route may never give.

    The desk agent pastes a 200's body. An empty string pasted is invisible,
    so the owner presses the key again -- and a route that answered 200 for
    "I heard nothing" is indistinguishable from one that answered it for "the
    recognizer is broken".
    """
    hub = dictate_hub(tmp_path)

    status, payload = await post_dictate(hub, silence_pcm(400), monkeypatch)

    assert status == 422, (
        f"a quiet clip must be a refusal the desk agent can tell apart, "
        f"never an empty 200: {status}")
    assert not payload.get("text"), (
        "a refusal carried text, which is what gets pasted")


@pytest.mark.asyncio
async def test_a_decode_that_fails_is_a_502_and_carries_no_text(
        tmp_path, monkeypatch):
    """AC-115, and the failure that would put Butler's words in the document.

    A recognizer that falls over is the WORLD's failure and answers 502 -- the
    same split `get_models` and the voice preview make. What must never happen
    is a 200 whose body is an apology: the desk agent pastes that.
    """
    hub = dictate_hub(tmp_path, asr=RecordingAsr(fail=True))

    status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

    assert status == 502, (
        f"a broken recognizer answered something a caller would paste: "
        f"{status}")
    assert not payload.get("text"), (
        "the failure body carried text, which is what gets pasted")


@pytest.mark.asyncio
async def test_a_body_past_the_ceiling_is_refused(tmp_path, monkeypatch):
    """The daemon holds no more audio than it would ever decode (D-1).

    A stuck key, or a body from anywhere on the tailnet, must not make the
    daemon accumulate arbitrary memory -- and the microphone is what pays for
    it, which is the ambient ceiling's reasoning one route over.
    """
    hub = dictate_hub(tmp_path)
    over = b"\x01\x02" * (butlerd.DICTATE_MAX_BYTES // 2 + 1024)

    status, _payload = await post_dictate(hub, over, monkeypatch)

    assert status == 413, (
        f"a body past the ceiling was accepted: {status}")
    assert hub.asr.clips == [], (
        "the oversized clip was decoded before it was refused")


@pytest.mark.asyncio
async def test_the_owners_ceiling_bounds_the_body_not_just_the_schemas(
        tmp_path, monkeypatch):
    """The setting's VALUE is enforced, or the control is inert (AC-7b).

    Bounding only by `Setting.maximum` makes `dictate_max_seconds` a control
    that saves, reports success and decides nothing -- `asr_latency`'s `high`
    by the front door (`daemon/AGENTS.md`). Enforced in the DAEMON rather than
    in the desk agent because a body can arrive from anywhere on the tailnet,
    and a client-side ceiling bounds only the well-behaved client.

    The body here is deliberately over the owner's ceiling and well under the
    schema's, so a route that reads the wrong one answers 200.
    """
    hub = dictate_hub(tmp_path)
    spec = BY_KEY["dictate_max_seconds"]
    hub.settings.set("dictate_max_seconds", spec.minimum)
    over_owner = b"\x01\x02" * (int(spec.minimum) * ports.SAMPLE_RATE_IN)
    assert len(over_owner) < butlerd.DICTATE_MAX_BYTES, (
        "the fixture must be under the SCHEMA ceiling or it proves nothing")

    status, _payload = await post_dictate(hub, over_owner + b"\x01\x02",
                                          monkeypatch)

    assert status == 413, (
        f"the owner set a {spec.minimum}s ceiling and a longer clip was "
        f"accepted: {status}")
    assert hub.asr.clips == [], (
        "the over-length clip was decoded before it was refused")


# --------------------------------------------------------------------------- #
# AC-116 -- never two decodes at once
# --------------------------------------------------------------------------- #
class BlockingFinalizeAsr(RecordingAsr):
    """A recognizer whose streaming `finalize` can be held open.

    The instrument for the one hole a session-state check cannot see:
    `asyncio.to_thread` is not cancellable, so `pipeline._end_turn`'s finalize
    keeps decoding on a worker thread after `abandon()` has already reset the
    session to IDLE.
    """

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()     # finalize has started decoding
        self.release = threading.Event()     # the test lets it return

    def open(self, cfg, on_partial, on_final):
        inner = super().open(cfg, on_partial, on_final)
        outer = self

        class _HeldSession:
            def feed(self, pcm):
                inner.feed(pcm)

            def reset_turn(self):
                inner.reset_turn()

            def close(self):
                inner.close()

            def finalize(self):
                # The wait happens BEFORE the inner call, so the fake's own
                # lock is never held while the test is blocked on it.
                outer.entered.set()
                assert outer.release.wait(10), "the test never released finalize"
                inner.finalize()

        return _HeldSession()


async def until(predicate, what: str, timeout: float = 5.0) -> None:
    """Poll for a condition instead of sleeping a fixed amount.

    A fixed sleep passes on a fast machine and flakes on a slow one, and what
    these tests assert is an OVERLAP -- so the wait has to end when the
    overlap actually exists (test/AGENTS.md: no unbounded await).
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, f"timed out: {what}"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_dictation_is_refused_while_a_real_capture_is_open(
        tmp_path, monkeypatch):
    """AC-116 against a REAL `TurnPipeline`, not against a faked state.

    The earlier version of this test set `s.state` by hand, which asserted
    `_recognizer_in_use`'s implementation rather than the property -- and that
    is exactly why it was blind to the finalize hole the next test covers
    (test/AGENTS.md: assert on observable behaviour, not internals).

    The decode COUNT is the assertion with teeth. A daemon that answered 409
    and ran the decode anyway would satisfy the status check while destroying
    the live turn's transcript, which is the defect this criterion is named
    after.
    """
    hub = dictate_hub(tmp_path)
    s = attach_session(hub)
    await hub._start_capture(s)
    try:
        status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

        assert status == 409, (
            f"a dictation ran while a capture held the recognizer: {status}")
        assert not payload.get("text"), (
            f"a refusal carried text, and the desk agent pastes whatever is "
            f"in that field (AC-115): {payload!r}")
        assert hub.asr.clips == [], (
            f"the clip was decoded beside a live capture, which returns EMPTY "
            f"transcripts on BOTH sides silently: {len(hub.asr.clips)} decodes")
    finally:
        s.task.cancel()
        s.pipeline.close()


@pytest.mark.asyncio
async def test_a_dictation_is_refused_while_an_abandoned_finalize_decodes_on(
        tmp_path, monkeypatch):
    """The hole a state check cannot see, and the reason this step exists.

    `asyncio.to_thread` cannot be cancelled: cancelling the awaiting coroutine
    returns at once and the worker thread runs to completion. So `abandon()`
    -- barge-in, or a dropped socket mid-utterance -- sets the session IDLE and
    closes the pipeline while transcribe.cpp is still decoding, for as long as
    the adapter's finalize budget allows (up to 60 s).

    Every session state reads free here. Only a mark the PIPELINE owns, and
    that only the worker thread itself clears, can be true through this.
    """
    asr = BlockingFinalizeAsr()
    hub = dictate_hub(tmp_path, asr=asr)
    s = attach_session(hub)
    await hub._start_capture(s)
    try:
        # The turn is ended by the pipeline's OWN run loop, not by a
        # `finalize_now` the test awaits itself -- speech, then enough silence
        # for the policy to call it. That matters: `abandon()` cancels
        # `s.task`, and in production `s.task` is the coroutine suspended on
        # `to_thread(finalize)`. A fixture that finalizes on some other task
        # leaves that coroutine's cancellation untested, and this test then
        # cannot tell a mark cleared on the THREAD from one cleared in the
        # coroutine's `finally` -- which is the whole defect.
        s.pipeline.feed(tone_pcm(300))
        s.pipeline.feed(silence_pcm(1200))
        await until(asr.entered.is_set, "finalize to start decoding")
        assert not s.task.done(), (
            "the run loop must still own the finalize, or cancelling it "
            "proves nothing")

        await s.abandon()
        assert s.state == hub_mod.IDLE and s.pipeline is None, (
            f"the fixture is not reproducing the hole: this test is only "
            f"meaningful once the session looks idle "
            f"({s.state!r}, pipeline={s.pipeline!r})")

        status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

        assert status == 409, (
            f"a dictation was allowed while an abandoned turn was still "
            f"decoding on a worker thread: {status}")
        assert not payload.get("text"), (
            f"a refusal carried text (AC-115): {payload!r}")
        assert asr.clips == [], (
            f"a second decode ran on the same model while the first was in "
            f"flight -- the 9-of-9 empty-both-sides defect: "
            f"{len(asr.clips)} decodes")
    finally:
        asr.release.set()


@pytest.mark.asyncio
async def test_a_dictation_is_allowed_while_a_coding_agent_turn_is_thinking(
        tmp_path, monkeypatch):
    """The guard reads the RECOGNIZER, not the turn -- and that is the point.

    `stt_engine` tears the stream down before it emits the final, so nothing
    is decoding through `thinking` or `speaking`. This daemon runs
    `harness_only`, where a coding-agent turn sits in `thinking` for minutes;
    refusing there would mean dictation stops working during exactly the wait
    `docs/desk-hotkeys.md` section 1 says it should survive.

    The state is set by hand deliberately: what this asserts is that the state
    is IRRELEVANT, so the honest fixture is one where it is set to the value
    that used to refuse.
    """
    hub = dictate_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 5
    s.state = hub_mod.THINKING          # a harness turn, minutes long
    assert s.pipeline is None, "no streaming session survives into thinking"

    status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

    assert status == 200, (
        f"dictation was refused while a coding agent was merely slow, which "
        f"is the window it exists to work in: {status}")
    assert payload["text"] == DICTATED


@pytest.mark.asyncio
async def test_a_second_dictation_is_refused_while_the_first_is_decoding(
        tmp_path, monkeypatch):
    """AC-116 against ITSELF, which the session-state check cannot cover.

    Two keypresses in quick succession reach the route with every session
    still idle, so nothing about the turn machinery stops them. Only a real
    exclusion does. The first decode is held open on an event, so the second
    request arrives while the recognizer is genuinely in use rather than
    merely likely to be.
    """
    gate = asyncio.Event()
    hub = dictate_hub(tmp_path, asr=RecordingAsr(gate=gate))
    client = await dictate_client(hub, monkeypatch)
    try:
        first = asyncio.ensure_future(client.post(
            "/api/dictate", data=tone_pcm(400),
            headers={"Content-Type": "application/octet-stream"}))
        # Wait for the decode to actually be in flight rather than sleeping:
        # a fixed sleep here would pass on a fast machine and flake on a slow
        # one, and what is being asserted is an overlap.
        for _ in range(200):
            if hub.asr.clips:
                break
            await asyncio.sleep(0.01)
        assert hub.asr.clips, "the first decode never started"

        second = await client.post(
            "/api/dictate", data=tone_pcm(400),
            headers={"Content-Type": "application/octet-stream"})

        assert second.status == 409, (
            f"a second dictation ran beside the first: {second.status}")
        assert not (await second.json()).get("text"), (
            "the busy refusal carried text, which is what gets pasted "
            "(AC-115); 409 is the refusal the desk agent meets most often")
        assert len(hub.asr.clips) == 1, (
            f"two clips reached the recognizer at once: "
            f"{len(hub.asr.clips)} decodes")

        gate.set()
        assert (await first).status == 200, (
            "the refusal took the first dictation with it")
    finally:
        gate.set()
        await client.close()


# --------------------------------------------------------------------------- #
# AC-123 -- what is pasted cannot execute a command
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw, want, why", [
    ("check the tests\nrm -rf build", "check the tests rm -rf build",
     "a line feed in a shell is a RETURN, not a character"),
    # CRLF is TWO controls, so it becomes two spaces. Deliberately not
    # collapsed: runs of whitespace are cosmetic, and collapsing them is
    # behaviour this rule does not need in order to be safe.
    ("git status\r\nls", "git status  ls",
     "CRLF must not weld two commands into one word"),
    ("a\tb", "a b", "a tab is whitespace and stays whitespace"),
    ("hello\x1b[31mworld", "hello[31mworld",
     "ESC starts an ANSI escape sequence and is deleted outright"),
    ("plain sentence", "plain sentence", "ordinary text is untouched"),
    ("  padded  ", "padded", "the answer is stripped, as it always was"),
])
def test_the_answer_cannot_carry_a_return_or_an_escape(raw, want, why):
    """AC-123, rule by rule.

    The two halves are different on purpose. Whitespace controls become a
    SPACE, because deleting them joins the words either side and invents a
    command nobody dictated -- "git status\nls" would become "git statusls".
    Everything else in C0, and DEL, is deleted, because there is no reading of
    a dictated sentence in which an ESC belongs.
    """
    assert butlerd.paste_safe(raw) == want, why


def test_the_sanitiser_is_idempotent():
    """Running it twice must not differ from running it once.

    The route sanitises at one boundary today. A second paster would call it
    again on text that has already been through it, and a rule that is not
    idempotent turns that into a silent difference between two call paths.
    """
    once = butlerd.paste_safe("a\r\nb\x1bc\td")
    assert butlerd.paste_safe(once) == once


@pytest.mark.asyncio
async def test_a_transcript_with_a_newline_never_reaches_the_caller(
        tmp_path, monkeypatch):
    """AC-123 through the ROUTE, which is what actually protects the owner.

    A unit test on `paste_safe` proves the function; only this proves the
    route calls it. The recognizer is not supposed to emit a line feed, and
    that is precisely why it is enforced rather than trusted -- an engine
    swap or a punctuation setting is one line away from making it untrue, and
    the failure is silent and lands in the owner's shell.
    """
    class NewlineAsr(RecordingAsr):
        async def transcribe(self, pcm, cfg):
            self.clips.append(pcm)
            return "deploy the branch\nrm -rf /"

    hub = dictate_hub(tmp_path, asr=NewlineAsr())

    status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

    assert status == 200
    assert "\n" not in payload["text"] and "\r" not in payload["text"], (
        f"the route answered text carrying a RETURN, and the desk agent "
        f"pastes it into whatever the caret is in: {payload['text']!r}")
    assert payload["text"] == "deploy the branch rm -rf /", (
        f"the newline was deleted rather than spaced, which welds the words "
        f"either side into something nobody said: {payload['text']!r}")


@pytest.mark.asyncio
async def test_a_clip_of_nothing_but_control_characters_is_422(
        tmp_path, monkeypatch):
    """Sanitising happens BEFORE the emptiness check, or the route answers
    200 with an empty string -- the one thing AC-115 forbids."""
    class ControlOnlyAsr(RecordingAsr):
        async def transcribe(self, pcm, cfg):
            self.clips.append(pcm)
            return "\x00\x1b\x07"

    hub = dictate_hub(tmp_path, asr=ControlOnlyAsr())

    status, payload = await post_dictate(hub, tone_pcm(400), monkeypatch)

    assert status == 422, f"an empty 200 escaped through the sanitiser: {status}"
    assert not payload.get("text")


# --------------------------------------------------------------------------- #
# the REAL recognizer -- everything above this line runs on a fake
# --------------------------------------------------------------------------- #
def _fixture_pcm(name: str) -> bytes:
    """The raw 16 kHz mono int16 samples of a fixture, WITHOUT the WAV header.

    The 44 header bytes are the point of this helper. The route takes
    HEADERLESS PCM, and a caller that posts the whole file hands the decoder
    22 near-full-scale int16 samples in front of the speech -- a burst on
    every utterance. Pipecat shipped exactly that bug across three local STT
    services (PR #4896, `.references/push-to-talk-prior-art.md`), and it is
    invisible in a test that only checks the words came back roughly right.
    `wave` is used rather than a 44-byte slice because the header is not
    always 44 bytes.
    """
    import wave
    with wave.open(str(Path(__file__).resolve().parents[1] / "test" /
                        "assets" / name), "rb") as w:
        assert w.getframerate() == ports.SAMPLE_RATE_IN and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def _manifest() -> dict:
    """`asr_observed_parakeet` per fixture: what the REAL engine returned.

    Asserting against `transcript` would be asserting the recognizer is
    perfect -- it is not, and the manifest says so: the observed text differs
    in casing, punctuation and a couple of function words.
    """
    import json
    path = Path(__file__).resolve().parents[1] / "test" / "assets" / "manifest.json"
    return {f["filename"]: f for f in json.loads(path.read_text())["fixtures"]}


def _normalise(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_route_decodes_real_speech(real_stt_daemon):
    """AC-113 against parakeet, which nothing above this line touches.

    Every other test in this file runs on `FakeAsr`, which returns its script
    whether or not the bytes arrived. This is the only one that can tell
    "the route decodes" from "the fake answered".
    """
    import aiohttp

    want = _manifest()["short_question.wav"]["asr_observed_parakeet"]
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{real_stt_daemon}/api/dictate",
                                data=_fixture_pcm("short_question.wav")) as r:
            status, payload = r.status, await r.json()

    assert status == 200, f"real speech was not decoded: {status} {payload}"
    assert _normalise(payload["text"]) == _normalise(want), (
        f"parakeet returned {payload['text']!r}; the manifest records "
        f"{want!r} for this fixture")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_silence_is_refused_rather_than_answered_empty(
        real_stt_daemon):
    """AC-115 against the real engine, where the empty answer is genuine.

    The fake refuses to transcribe silence by construction. This proves the
    422 fires on a decode that really did come back with nothing, which is
    the case the owner meets when they press the key and say nothing.
    """
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{real_stt_daemon}/api/dictate",
                                data=_fixture_pcm("silence_2s.wav")) as r:
            status, payload = r.status, await r.json()

    assert status == 422, (
        f"real silence answered {status} rather than a refusal the desk agent "
        f"can tell apart: {payload}")
    assert not payload.get("text")
