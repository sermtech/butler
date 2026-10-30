"""The user talks to Butler from WhatsApp (docs/messaging.md M6/M7).

AC-519 (which chat: root and current, `/new`), AC-520 (the reply quoted,
split, typing, nothing spoken at the desk, queued behind another device),
AC-521 (one at a time, the busy line), AC-522 (voice notes in, the language
from the owner's words), AC-523 (media refusals said in the chat), AC-524
(one line for messages missed while down), AC-539 (voice replies in the
language received), AC-560 (an image reaches the turn's attachments),
AC-561 (a document follows `document_reading`).

Driven at the HUB with a fake WhatsApp door that records what Butler said in
the chat; the sidecar's own half (quote, ptt, presence, missed) is pinned by
its node tests (`reply.test.mjs`), and the stream between the two by
`test_whatsapp_events.py`. ffmpeg is replaced at `decode_audio` /
`encode_voice_note`, except in the one test that says it uses the real one.
Nothing here touches WhatsApp.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubTts, attach_session, make_hub      # noqa: E402
from test_attachments import PNG                             # noqa: E402
from test_library_voice import _harness_hub                  # noqa: E402
from daemon import attachments as attach_mod                 # noqa: E402
from daemon import dialogue, ports                           # noqa: E402
from daemon import hub as hub_mod                            # noqa: E402
from daemon import phone as phone_mod                        # noqa: E402
from daemon import whatsapp as wa_mod                        # noqa: E402
from daemon.settings import Settings                         # noqa: E402

OWN = "45200000000001@lid"
VOICE_PCM = b"\x10\x20" * 16000          # one second, not silence


class FakeWa:
    """The daemon's WhatsApp owner as `Inbound` sees it: what was said in
    the chat (`said`: text, quote), voice notes sent, typing marks, and the
    bytes behind each media id."""

    def __init__(self):
        self.questions = None        # notify.Questions' hook (MS-22)
        self.said: list[tuple[str, str | None]] = []
        self.voices: list[tuple[bytes, str | None]] = []
        self.marks: list[str] = []
        self.files: dict[str, bytes] = {}
        self.fetched: list[str] = []
        self.voice_error: Exception | None = None

    async def reply(self, jid, *, text=None, quote=None, voice=None):
        assert jid == OWN, jid
        if voice is not None:
            if self.voice_error is not None:
                raise self.voice_error
            self.voices.append((voice, quote))
        else:
            self.said.append((text, quote))
        return "SENT"

    async def presence(self, jid, state):
        self.marks.append(state)

    async def media(self, media_id):
        self.fetched.append(media_id)
        return self.files[media_id]


class Recorder:
    """A brain that keeps every (text, TurnContext) and answers `answer`,
    optionally held at `gate` so a turn stays in flight."""

    def __init__(self, answer="Noted.", gate: asyncio.Event | None = None):
        self.seen = []
        self.answer = answer
        self.gate = gate

    async def turn(self, text, ctx):
        self.seen.append((text, ctx))
        if self.gate is not None:
            await self.gate.wait()
        yield ports.Say(self.answer, tier="conversation")
        yield ports.Done("conversation")

    async def abort(self, turn_id):
        pass

    async def warm(self):
        pass

    @property
    def info(self):
        return {"engine": "recorder"}


class SpeakingTts(StubTts):
    """Records every synthesis (text, voice) and yields real-looking PCM."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.calls.append((text, voice))
        if self.fail is not None:
            raise self.fail
        yield b"\x01\x02" * 2400


def _hub(tmp_path, brain=None, **settings):
    hub = make_hub(tmp_path)
    hub.brain = brain or Recorder()
    hub.tts = SpeakingTts()
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("phone_reply", "text")
    for key, value in settings.items():
        hub.settings.set(key, value)
    hub.attachments = attach_mod.AttachmentStore(tmp_path / "attachments")
    return hub


def _inbound(hub, wa=None):
    wa = wa or FakeWa()
    return wa_mod.Inbound(hub, wa,
                          queue_folder=hub.settings.path.parent / "phone",
                          missed_settle_s=0.05, busy_poll_s=0.01,
                          typing_every_s=60.0), wa


def _msg(mid, text="", media=None):
    item = {"type": "message", "tag": "owner", "id": mid, "jid": OWN,
            "text": text, "ts": 1790950000}
    if media is not None:
        item["media"] = media
    return item


async def _settle(inbound, timeout=10.0):
    """Every chat's queue drained and its worker finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = [t for t in inbound._workers.values() if not t.done()]
        if not busy:
            return
        await asyncio.wait(busy, timeout=0.05)
    raise AssertionError("the WhatsApp queue never drained")


async def _until(cond, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


# --------------------------------------------------------------------------- #
# a turn, and its answer (AC-520)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_message_is_an_owner_turn_and_the_answer_comes_back_quoted(
        tmp_path):
    hub = _hub(tmp_path, Recorder("Three meetings today."))
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "what is on my calendar"))
    await _settle(inbound)
    assert [t for t, _ in hub.brain.seen] == ["what is on my calendar"]
    assert wa.said == [("Three meetings today.", "M1")]
    assert wa.marks[0] == "composing" and wa.marks[-1] == "paused"
    assert not any(isinstance(s, hub_mod.PhoneSession) for s in hub.sessions)


@pytest.mark.asyncio
async def test_NOTHING_IS_SPOKEN_AT_THE_DESK_for_a_phone_turn(tmp_path):
    """AC-520. With `speak` on and a desk connected, the engine is never
    asked and no audio reaches any socket: the user is on the phone."""
    hub = _hub(tmp_path, Recorder("A spoken-length answer."), speak=True)
    desk = attach_session(hub, "desk")
    desk.peer = "127.0.0.1"
    desk.caps = {"wake": True, "tts": True}
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "hello"))
    await _settle(inbound)
    assert wa.said == [("A spoken-length answer.", "M1")]
    assert hub.tts.calls == [], f"the desk synthesized {hub.tts.calls}"
    assert not any(isinstance(m, bytes) for m in desk.ws.sent)


@pytest.mark.asyncio
async def test_the_page_is_shown_the_turn_with_its_whatsapp_reason(tmp_path):
    """AC-519: the page renders the phone's turn live. Its frames reach every
    page, and the `final` says where it came from."""
    hub = _hub(tmp_path, Recorder("Answer for the page."))
    page = attach_session(hub, "page")
    inbound, _wa = _inbound(hub)
    await inbound.owner(_msg("M1", "from my phone"))
    await _settle(inbound)
    frames = [json.loads(m) for m in page.ws.sent if isinstance(m, str)]
    finals = [f for f in frames if f["type"] == "final"]
    assert finals and finals[0]["reason"] == "whatsapp"
    assert finals[0]["text"] == "from my phone"
    replies = [f["text"] for f in frames if f["type"] == "reply"]
    assert replies == ["Answer for the page."]


def test_the_page_marks_and_adopts_a_phone_turn():
    """The page half of AC-519 and AC-533 (a source pin, test/AGENTS.md "Grep
    gates"): a `final` whose reason is a phone channel is adopted for the
    chat on screen, gets its "via <app>" mark through textContent, and plays
    no cue. The page's PHONE_VIA names EXACTLY the daemon's PHONE_REASONS, so
    a channel added on one side cannot go unmarked on the other (D-18)."""
    import re
    from daemon import hub as hub_mod
    src = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        "utf-8")
    code = re.sub(r"//[^\n]*", "", src)
    table = re.search(r"const PHONE_VIA = \{([^}]*)\};", code)
    assert table, "web/app.js has no PHONE_VIA table"
    assert set(re.findall(r"(\w+):", table.group(1))) == set(
        hub_mod.PHONE_REASONS)
    assert 'whatsapp: "via WhatsApp"' in table.group(1)
    assert 'telegram: "via Telegram"' in table.group(1)
    assert re.search(r'PHONE_VIA\[m\.reason\] && m\.chat\s*&& m\.chat === '
                     r'liveChatId && state !== CAPTURE', code)
    assert "const phone = PHONE_VIA[m.reason];" in code
    assert "markVia(openItem, phone)" in code
    assert re.search(r'via\.textContent = words', code)
    assert "if (!phone) playCue(\"submitted\")" in code


@pytest.mark.asyncio
async def test_a_long_answer_is_split_at_line_boundaries(tmp_path):
    line = "x" * 999 + "\n"
    hub = _hub(tmp_path, Recorder(line * 70))       # 70,000 characters
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "long please"))
    await _settle(inbound)
    assert len(wa.said) == 2
    assert all(len(t) <= wa_mod.TEXT_LIMIT for t, _ in wa.said)
    assert all(q == "M1" for _, q in wa.said)
    assert wa.said[0][0].endswith("x") and wa.said[1][0].startswith("x")
    assert sum(t.count("x") for t, _ in wa.said) == 999 * 70


def test_split_text_cuts_an_overlong_single_line():
    parts = phone_mod.split_text("a" * 25, limit=10)
    assert parts == ["a" * 10, "a" * 10, "a" * 5]
    assert phone_mod.split_text("one\ntwo\nthree", limit=8) == ["one\ntwo", "three"]


# --------------------------------------------------------------------------- #
# one at a time, in order (AC-520, AC-521)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_messages_run_one_at_a_time_and_the_eleventh_waiting_is_busy(
        tmp_path):
    gate = asyncio.Event()
    hub = _hub(tmp_path, Recorder("ok", gate=gate))
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M0", "first"))
    await _until(lambda: len(hub.brain.seen) == 1, "the first turn")
    for i in range(1, 12):
        await inbound.owner(_msg(f"M{i}", f"message {i}"))
    # Ten wait; the eleventh waiting one is answered at once and dropped.
    assert wa.said == [(phone_mod.BUSY_LINE, "M11")]
    assert len(hub.brain.seen) == 1, "a second turn started while one ran"
    gate.set()
    await _settle(inbound)
    assert [t for t, _ in hub.brain.seen] == (
        ["first"] + [f"message {i}" for i in range(1, 11)])
    assert [q for _, q in wa.said[1:]] == [f"M{i}" for i in range(0, 11)]


@pytest.mark.asyncio
async def test_a_message_queues_behind_a_turn_from_another_device(tmp_path):
    """AC-520: the chat is answering the page; the phone's message waits."""
    gate = asyncio.Event()
    hub = _hub(tmp_path, Recorder("ok", gate=gate))
    page = attach_session(hub, "page")
    await hub._typed(page, {"type": "typed", "text": "from the page"})
    await _until(lambda: len(hub.brain.seen) == 1, "the page's turn")
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "from the phone"))
    await asyncio.sleep(0.2)
    assert len(hub.brain.seen) == 1, "the phone's turn did not wait"
    gate.set()
    await _settle(inbound)
    assert [t for t, _ in hub.brain.seen] == ["from the page",
                                              "from the phone"]
    assert wa.said == [("ok", "M1")]


# --------------------------------------------------------------------------- #
# which chat (AC-519)
# --------------------------------------------------------------------------- #
def _phone_hub(tmp_path, monkeypatch):
    """A real hub with conversations (a fake coding agent behind
    `harness_only`). `speak` off: a desk turn would otherwise sit out the
    playback gate on a stub socket (test/AGENTS.md, known fixture traps)."""
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    hub.settings.set("phone_reply", "text")
    return hub


async def _chats(hub):
    root = hub.shortcuts.root_id()
    return [c["id"] for c in await asyncio.to_thread(
        hub.brain.session_list, 50, hub.project_filter(root))]


async def _user_lines(hub, chat):
    record = await asyncio.to_thread(hub.brain.session_chat, chat)
    return [m["content"] for m in record["messages"] if m["role"] == "user"]


@pytest.mark.asyncio
async def test_root_mode_births_a_root_chat_when_there_is_none_and_reuses_it(
        tmp_path, monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    inbound, wa = _inbound(hub)
    assert await _chats(hub) == []
    await inbound.owner(_msg("M1", "first from the phone"))
    await _settle(inbound)
    await inbound.owner(_msg("M2", "second from the phone"))
    await _settle(inbound)
    chats = await _chats(hub)
    assert len(chats) == 1, chats
    assert await _user_lines(hub, chats[0]) == ["first from the phone",
                                                "second from the phone"]
    assert [q for _, q in wa.said] == ["M1", "M2"]


@pytest.mark.asyncio
async def test_root_mode_answers_in_the_NEWEST_root_chat(tmp_path,
                                                         monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    desk = attach_session(hub, "desk")
    older = (await hub.new_conversation(None))["id"]
    desk.showing = older
    await hub._typed(desk, {"type": "typed", "text": "older chat"})
    await _until(lambda: not hub._turn_in_flight(older), "the desk turn")
    newer = (await hub.new_conversation(None))["id"]
    desk.showing = newer
    await hub._typed(desk, {"type": "typed", "text": "newer chat"})
    await _until(lambda: not hub._turn_in_flight(newer), "the desk turn")
    inbound, _wa = _inbound(hub)
    await inbound.owner(_msg("M1", "to the newest"))
    await _settle(inbound)
    assert await _user_lines(hub, newer) == ["newer chat", "to the newest"]
    assert await _user_lines(hub, older) == ["older chat"]


@pytest.mark.asyncio
async def test_CURRENT_mode_continues_the_chat_the_owner_last_used(
        tmp_path, monkeypatch):
    """AC-519: a desk turn followed by a WhatsApp message is ONE
    conversation -- even when that chat is neither the newest nor the one
    the daemon holds."""
    hub = _phone_hub(tmp_path, monkeypatch)
    hub.settings.set("phone_chat", "current")
    desk = attach_session(hub, "desk")
    held = (await hub.new_conversation(None))["id"]
    desk.showing = held
    await hub._typed(desk, {"type": "typed", "text": "the held chat"})
    await _until(lambda: not hub._turn_in_flight(held), "the desk turn")
    talked = (await hub.new_conversation(None, focus=False))["id"]
    desk.showing = talked
    await hub._typed(desk, {"type": "typed", "text": "at the desk"})
    await _until(lambda: not hub._turn_in_flight(talked), "the desk turn")
    # A scheduled run then writes a NEWER root chat, and moves nothing.
    newest = (await hub.new_conversation(None, focus=False))["id"]
    run = hub_mod.RunSession(hub, newest, "automation:x")
    hub.sessions.add(run)
    await hub._typed(run, {"type": "typed", "text": "a recipe"})
    await _until(lambda: not hub._turn_in_flight(newest), "the run")
    hub.sessions.discard(run)
    assert hub.last_owner_chat == talked
    assert hub.current_chat_id() == held
    assert (await _chats(hub))[0] == newest
    inbound, _wa = _inbound(hub)
    await inbound.owner(_msg("M1", "and now from the phone"))
    await _settle(inbound)
    assert await _user_lines(hub, talked) == ["at the desk",
                                              "and now from the phone"]
    assert await _user_lines(hub, held) == ["the held chat"]
    assert await _user_lines(hub, newest) == ["a recipe"]
    # ... and the phone's turn is the owner's latest too.
    assert hub.last_owner_chat == talked


@pytest.mark.asyncio
async def test_a_scheduled_run_does_not_move_last_owner_chat(tmp_path,
                                                             monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    desk = attach_session(hub, "desk")
    mine = (await hub.new_conversation(None))["id"]
    desk.showing = mine
    await hub._typed(desk, {"type": "typed", "text": "mine"})
    await _until(lambda: not hub._turn_in_flight(mine), "the desk turn")
    run_chat = (await hub.new_conversation(None, focus=False))["id"]
    run = hub_mod.RunSession(hub, run_chat, "automation:x")
    hub.sessions.add(run)
    await hub._typed(run, {"type": "typed", "text": "a recipe"})
    await _until(lambda: not hub._turn_in_flight(run_chat), "the run")
    assert hub.last_owner_chat == mine


@pytest.mark.asyncio
async def test_new_starts_a_fresh_chat_and_the_next_message_goes_there(
        tmp_path, monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "in the old chat"))
    await _settle(inbound)
    [old] = await _chats(hub)
    await inbound.owner(_msg("M2", "/new"))
    await _settle(inbound)
    assert wa.said[-1] == (hub.NEW_CONVERSATION, "M2")
    await inbound.owner(_msg("M3", "in the new chat"))
    await _settle(inbound)
    chats = await _chats(hub)
    assert len(chats) == 2
    new = next(c for c in chats if c != old)
    assert await _user_lines(hub, new) == ["in the new chat"]
    assert await _user_lines(hub, old) == ["in the old chat"]


@pytest.mark.asyncio
async def test_new_in_current_mode_moves_the_phone_to_the_fresh_chat(
        tmp_path, monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    hub.settings.set("phone_chat", "current")
    desk = attach_session(hub, "desk")
    talked = (await hub.new_conversation(None))["id"]
    desk.showing = talked
    await hub._typed(desk, {"type": "typed", "text": "at the desk"})
    await _until(lambda: not hub._turn_in_flight(talked), "the desk turn")
    inbound, _wa = _inbound(hub)
    await inbound.owner(_msg("M1", "/new"))
    await _settle(inbound)
    assert hub.last_owner_chat != talked
    fresh = hub.last_owner_chat
    await inbound.owner(_msg("M2", "fresh start"))
    await _settle(inbound)
    assert await _user_lines(hub, fresh) == ["fresh start"]
    assert await _user_lines(hub, talked) == ["at the desk"]


# --------------------------------------------------------------------------- #
# voice notes in (AC-522) and the recognizer's one-at-a-time rule
# --------------------------------------------------------------------------- #
def _voice_media(mid="V1"):
    return {"kind": "audio", "id": mid, "ptt": True, "bytes": 4000,
            "tooLarge": False, "mimetype": "audio/ogg; codecs=opus"}


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """`decode_audio` answers fixed PCM; `encode_voice_note` records."""
    encoded = []
    monkeypatch.setattr(phone_mod, "decode_audio", lambda data: VOICE_PCM)

    def encode(pcm, rate):
        encoded.append((pcm, rate))
        return b"OggS" + b"\x00" * 16
    monkeypatch.setattr(phone_mod, "encode_voice_note", encode)
    return encoded


@pytest.mark.asyncio
async def test_a_voice_note_is_transcribed_prefixed_and_its_language_comes_from_the_words(
        tmp_path, monkeypatch, no_ffmpeg):
    """AC-522: Butler's recognizer hears it; the warning prefix is added;
    the language is decided from the OWNER'S WORDS, never from the English
    prefix, and is stored on the turn."""
    seen = []

    def detect(text, candidates):
        seen.append(text)
        return "pt" if text.startswith("bom dia") else "en"
    monkeypatch.setattr(dialogue, "detect_language", detect)
    hub = _hub(tmp_path, language="auto")
    hub.asr._script = ["bom dia", "Butler"]
    inbound, wa = _inbound(hub)
    wa.files["V1"] = b"ogg bytes"
    await inbound.owner(_msg("N1", media=_voice_media()))
    await _settle(inbound)
    text, ctx = hub.brain.seen[0]
    assert text == phone_mod.VOICE_PREFIX + "\nbom dia Butler"
    assert seen == ["bom dia Butler"]
    assert ctx.language == "pt"
    assert wa.said == [("Noted.", "N1")]


@pytest.mark.asyncio
async def test_a_typed_message_takes_its_language_from_detect_language(
        tmp_path, monkeypatch):
    monkeypatch.setattr(dialogue, "detect_language",
                        lambda text, c: "pt" if "obrigado" in text else None)
    hub = _hub(tmp_path, language="auto")
    inbound, _wa = _inbound(hub)
    await inbound.owner(_msg("M1", "muito obrigado"))
    await _settle(inbound)
    # A one-word answer the detector will not name keeps the chat's language
    # (AC-209's latch, carried from one message to the next).
    await inbound.owner(_msg("M2", "sim"))
    await _settle(inbound)
    assert [ctx.language for _, ctx in hub.brain.seen] == ["pt", "pt"]


@pytest.mark.asyncio
async def test_a_voice_note_with_no_words_is_answered_with_the_fixed_line(
        tmp_path, monkeypatch):
    monkeypatch.setattr(phone_mod, "decode_audio", lambda data: b"\x00" * 32000)
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["V1"] = b"ogg"
    await inbound.owner(_msg("N1", media=_voice_media()))
    await _settle(inbound)
    assert wa.said == [(phone_mod.NO_WORDS, "N1")]
    assert hub.brain.seen == []


@pytest.mark.asyncio
async def test_a_clip_is_decoded_in_30_s_windows(tmp_path):
    hub = _hub(tmp_path)
    sizes = []
    real = hub.asr.transcribe

    async def counting(pcm, cfg):
        sizes.append(len(pcm))
        return await real(pcm, cfg)
    hub.asr.transcribe = counting
    await hub.transcribe_clip(b"\x01\x02" * (16000 * 70))   # 70 s
    assert sizes == [hub_mod.CLIP_WINDOW_BYTES, hub_mod.CLIP_WINDOW_BYTES,
                     2 * 16000 * 10]
    assert hub_mod.CLIP_WINDOW_S == 30


@pytest.mark.asyncio
async def test_a_clip_never_decodes_while_a_live_turn_holds_the_recognizer(
        tmp_path):
    """AC-116's rule, for a voice note: the desk's streaming session and a
    whole-clip decode never run on one model at once. It WAITS (a voice
    note is not refused like a dictation), and it takes `_decoding`."""
    hub = _hub(tmp_path)
    busy = {"live": True}
    hub._recognizer_in_use = lambda: busy["live"]
    calls = []
    real = hub.asr.transcribe

    async def counting(pcm, cfg):
        calls.append(hub._decoding.locked())
        return await real(pcm, cfg)
    hub.asr.transcribe = counting
    task = asyncio.create_task(hub.transcribe_clip(VOICE_PCM))
    await asyncio.sleep(0.6)
    assert calls == [], "decoded while a live turn held the recognizer"
    busy["live"] = False
    assert await asyncio.wait_for(task, 5) != ""
    assert calls == [True], "the decode did not hold the whole-clip lock"
    # And it queues behind another whole-clip decode.
    async with hub._decoding:
        task = asyncio.create_task(hub.transcribe_clip(VOICE_PCM))
        await asyncio.sleep(0.3)
        assert len(calls) == 1
    await asyncio.wait_for(task, 5)
    assert len(calls) == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="uses the REAL ffmpeg, which is not on PATH")
def test_real_ffmpeg_round_trip_pcm_to_ogg_and_back():
    """Uses the REAL ffmpeg on PATH: a voice note encodes to OGG/Opus and a
    received one decodes to 16 kHz mono int16."""
    import math
    import struct
    tone = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10)))
                    for i in range(24000))
    ogg = phone_mod.encode_voice_note(tone, 24000)
    assert ogg[:4] == b"OggS"
    pcm = phone_mod.decode_audio(ogg)
    assert abs(len(pcm) - 32000) < 3200, len(pcm)    # ~1 s at 16 kHz int16


# --------------------------------------------------------------------------- #
# images and documents in (AC-523, AC-560, AC-561)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_image_is_stored_and_ATTACHED_to_the_owner_turn(tmp_path):
    """AC-560: stored by `AttachmentStore` like a page upload and on the
    turn's context, so the model that answers gets it by the page's own
    code (AC-815); no
    caption asks the fixed question."""
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["I1"] = PNG
    await inbound.owner(_msg("P1", media={"kind": "image", "id": "I1",
                                          "bytes": len(PNG),
                                          "tooLarge": False}))
    await _settle(inbound)
    text, ctx = hub.brain.seen[0]
    assert text == phone_mod.IMAGE_QUESTION
    assert len(ctx.attachments) == 1, ctx.attachments
    att = ctx.attachments[0]
    assert att.kind == "image" and att.media_type == "image/png"
    assert Path(att.path).read_bytes() == PNG
    assert Path(att.path).parent == tmp_path / "attachments"


@pytest.mark.asyncio
async def test_an_image_caption_is_the_question(tmp_path):
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["I1"] = PNG
    await inbound.owner(_msg("P1", "is this mould?",
                             media={"kind": "image", "id": "I1",
                                    "tooLarge": False}))
    await _settle(inbound)
    text, ctx = hub.brain.seen[0]
    assert text == "is this mould?" and len(ctx.attachments) == 1


@pytest.mark.asyncio
async def test_a_document_follows_document_reading_like_a_page_upload(
        tmp_path):
    """AC-561: the text is read locally first (the default), and not read at
    all under `vision`, exactly as `AttachmentStore.add` does for the page."""
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["D1"] = b"the lease ends in March"
    media = {"kind": "document", "id": "D1", "fileName": "lease.txt",
             "tooLarge": False}
    await inbound.owner(_msg("F1", "when does it end?", media=media))
    await _settle(inbound)
    hub.settings.set("document_reading", "vision")
    await inbound.owner(_msg("F2", "again", media=media))
    await _settle(inbound)
    (_, read), (_, looked) = hub.brain.seen
    assert read.attachments[0].text == "the lease ends in March"
    assert read.attachments[0].name == "lease.txt"
    assert looked.attachments[0].text == ""


@pytest.mark.asyncio
async def test_a_file_over_100_MB_is_refused_in_the_chat_before_download(
        tmp_path):
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("F1", media={"kind": "video", "id": "BIG",
                                          "bytes": 200 * 1024 * 1024,
                                          "tooLarge": True}))
    await _settle(inbound)
    assert wa.fetched == [], "the oversize file was downloaded"
    assert wa.said == [(phone_mod.TOO_LARGE.format(mb=200, limit=100), "F1")]
    assert hub.brain.seen == []


@pytest.mark.asyncio
async def test_a_file_butler_will_not_keep_is_refused_by_name_in_the_chat(
        tmp_path):
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["Z1"] = b"PK\x03\x04 not a docx"
    await inbound.owner(_msg("F1", media={"kind": "document", "id": "Z1",
                                          "fileName": "archive.zip",
                                          "tooLarge": False}))
    await _settle(inbound)
    assert len(wa.said) == 1 and wa.said[0][1] == "F1"
    assert "archive.zip" in wa.said[0][0]
    assert hub.brain.seen == []


@pytest.mark.asyncio
async def test_a_videos_sound_is_transcribed_into_the_turn(tmp_path,
                                                           no_ffmpeg):
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    wa.files["V1"] = b"mp4"
    await inbound.owner(_msg("F1", "look at this",
                             media={"kind": "video", "id": "V1",
                                    "tooLarge": False}))
    await _settle(inbound)
    text, _ctx = hub.brain.seen[0]
    assert text.startswith("look at this\n\n" + phone_mod.VIDEO_PREFIX)
    assert " ".join(hub.asr._script) in text


# --------------------------------------------------------------------------- #
# voice replies out (AC-539)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_voice_text_sends_a_voice_note_IN_THE_LANGUAGE_RECEIVED_then_the_text(
        tmp_path, monkeypatch, no_ffmpeg):
    monkeypatch.setattr(dialogue, "detect_language",
                        lambda t, c: "pt" if "obrigado" in t else "en")
    hub = _hub(tmp_path, Recorder("De nada."), language="auto",
               phone_reply="voice_text", voice="stub_a")
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "muito obrigado"))
    await _settle(inbound)
    assert [v for v in wa.voices] == [(b"OggS" + b"\x00" * 16, "M1")]
    assert wa.said == [("De nada.", "M1")]
    # The owner's own voice is English; a Portuguese message gets the first
    # Portuguese voice.
    assert {voice for _, voice in hub.tts.calls} == {"stub_b"}
    assert no_ffmpeg[0][1] == hub.tts.info["sample_rate"]
    await inbound.owner(_msg("M2", "thank you very much"))
    await _settle(inbound)
    assert hub.tts.calls[-1][1] == "stub_a", "the user's own voice speaks it"


@pytest.mark.asyncio
async def test_mirror_speaks_only_after_a_voice_note_and_text_never_does(
        tmp_path, monkeypatch, no_ffmpeg):
    hub = _hub(tmp_path, phone_reply="mirror")
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "typed"))
    await _settle(inbound)
    assert wa.voices == []
    wa.files["V1"] = b"ogg"
    await inbound.owner(_msg("N1", media=_voice_media()))
    await _settle(inbound)
    assert [q for _, q in wa.voices] == ["N1"]
    hub.settings.set("phone_reply", "text")
    await inbound.owner(_msg("N2", media=_voice_media()))
    await _settle(inbound)
    assert [q for _, q in wa.voices] == ["N1"]
    assert [q for _, q in wa.said] == ["M1", "N1", "N2"]


@pytest.mark.asyncio
async def test_a_failed_synthesis_still_sends_the_text_and_says_why(
        tmp_path, no_ffmpeg):
    hub = _hub(tmp_path, Recorder("The answer."), phone_reply="voice_text")
    hub.tts.fail = RuntimeError("kokoro is down")
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "hi"))
    await _settle(inbound)
    assert wa.voices == []
    assert wa.said == [("The answer.\n\n(voice reply failed: kokoro is "
                        "down)", "M1")]


@pytest.mark.asyncio
async def test_code_and_links_are_not_spoken_and_a_long_answer_is_capped(
        tmp_path, no_ffmpeg):
    answer = ("See [the docs](https://example.com/x) and https://bad.example."
              "\n\n```\nrm -rf /\n```\n\n| a | b |\n|---|---|\n\n"
              + "This is a sentence of ten words for the cap test. " * 40)
    hub = _hub(tmp_path, Recorder(answer), phone_reply="voice_text")
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "explain"))
    await _settle(inbound)
    spoken = " ".join(text for text, _ in hub.tts.calls)
    for unsaid in ("https", "rm -rf", "|", "```", "example.com"):
        assert unsaid not in spoken, unsaid
    assert "See the docs" in spoken
    assert len(spoken.split()) <= phone_mod.VOICE_MAX_WORDS + 8
    assert spoken.endswith(phone_mod.REST_IN_TEXT)
    assert wa.said[0][0] == answer.strip(), "the TEXT is the whole answer"


# --------------------------------------------------------------------------- #
# missed while down (AC-524)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_messages_missed_while_down_get_ONE_line_counting_them(
        tmp_path):
    hub = _hub(tmp_path)
    inbound, wa = _inbound(hub)
    for i in range(3):
        inbound.missed({"type": "missed", "jid": OWN, "id": f"D{i}"})
    await _until(lambda: wa.said, "the missed line")
    await asyncio.sleep(0.2)
    assert wa.said == [(wa_mod.MISSED_LINE.format(n=3, noun="messages"),
                        None)]
    assert hub.brain.seen == [], "a missed message was answered"


@pytest.mark.asyncio
async def test_the_owners_message_is_answered_inside_quiet_hours(tmp_path):
    """Owner, 2026-10-08: "if user sends messages himself through whatsapp
    channel, it should always respond at any time." Quiet hours hold only
    speech nobody asked for (docs/proactive.md PR-14); the owner's own
    message is a turn and its answer goes back at once. The window is built
    around NOW, and the instrument checks it really is quiet now, so the
    test cannot pass by running in the daytime."""
    from datetime import datetime, timedelta
    from daemon import schedule
    zone = schedule.local_zone()
    now = datetime.now(zone)
    start = (now - timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=1)).strftime("%H:%M")
    hub = _hub(tmp_path, Recorder("Yes, I'm here."), quiet_hours=True,
               quiet_start=start, quiet_end=end)
    assert schedule.in_quiet(now, zone, start, end)
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("Q1", "are you there?"))
    await _settle(inbound)
    assert wa.said == [("Yes, I'm here.", "Q1")]
