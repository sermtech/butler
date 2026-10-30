"""Owner messages sent BACK TO BACK are each answered, in order, each quoted
to its own trigger (docs/messaging.md MS-17, AC-520, AC-521, 6.4).

The 2026-10-08 laptop defect (11 voice notes, 7 answers) was in the
sidecar's live-or-missed decision (`delivery.test.mjs`); these pin the
daemon's half against the same report: text and voice notes pushed through
the fake sidecar's real `/events` stream while the previous note is still
being TRANSCRIBED and while a turn is mid-answer, and the same burst over
Telegram, which shares `phone.PhoneTurns`. Plus the butler.log lines that
make a lost message visible: one per message received, one per reply sent
or not delivered, ids only, never content.

Nothing here touches WhatsApp or Telegram.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_telegram_inbound import (_close, _message, _open, _ready,  # noqa: E402
                                   _said, _simple_hub, bot,
                                   no_real_telegram)
from test_whatsapp_events import (_push, _requests, _until,         # noqa: E402
                                  fake_argv)
from test_whatsapp_inbound import Recorder, _hub, no_ffmpeg          # noqa: E402
from daemon import phone, ports                                      # noqa: E402
from daemon import whatsapp as whatsapp_mod                          # noqa: E402

OWN = "15559990000@s.whatsapp.net"
_ = (bot, no_real_telegram, fake_argv, no_ffmpeg)       # pytest fixtures


class Echo(Recorder):
    """Answers each turn with ITS OWN last line, after `gate` when one is
    set for that line, so every reply says which message it answers."""

    def __init__(self):
        super().__init__()
        self.gates: dict[str, asyncio.Event] = {}

    async def turn(self, text, ctx):
        self.seen.append((text, ctx))
        words = text.splitlines()[-1]
        if words in self.gates:
            await self.gates[words].wait()
        await asyncio.sleep(0.05)
        yield ports.Say(f"answer to {words}", tier="conversation")
        yield ports.Done("conversation")


class HeldEars:
    """`hub.asr.transcribe` for voice notes: each call hears the next word
    of `words`, and a word in `gates` is held until its gate opens."""

    def __init__(self, words):
        self.words = list(words)
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []

    async def transcribe(self, pcm, cfg):
        word = self.words.pop(0)
        self.started.append(word)
        if word in self.gates:
            await self.gates[word].wait()
        return word


def _voice(mid, media_id):
    return {"type": "message", "tag": "owner", "id": mid, "jid": OWN,
            "text": "", "media": {"kind": "audio", "id": media_id,
                                  "ptt": True, "bytes": 9,
                                  "tooLarge": False}}


def _text(mid, text):
    return {"type": "message", "tag": "owner", "id": mid, "jid": OWN,
            "text": text}


@pytest.mark.asyncio
async def test_a_BURST_of_text_and_voice_notes_is_answered_one_by_one_in_order(
        tmp_path, fake_argv, no_ffmpeg, capfd):
    """Five messages through the real /events path: E3 arrives while E2 is
    still being transcribed, E4 while E3's turn is mid-answer. Every one
    gets its own answer, in order, quoted to it; the log names each id."""
    brain = Echo()
    hub = _hub(tmp_path, brain)
    hub.settings.set("whatsapp_enabled", True)
    ears = HeldEars(["two", "three", "five"])
    hub.asr.transcribe = ears.transcribe
    ears.gates["two"] = asyncio.Event()
    brain.gates["three"] = asyncio.Event()
    records = tmp_path / "whatsapp"
    (records / "media").mkdir(parents=True)
    for media_id in ("V2", "V3", "V5"):
        (records / "media" / media_id).write_bytes(b"ogg bytes")
    wa = whatsapp_mod.WhatsApp(hub.settings, records)
    wa.inbound = whatsapp_mod.Inbound(hub, wa, busy_poll_s=0.01,
                                      queue_folder=tmp_path / "phone")
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", "running")
        await _push(wa, _text("E1", "one"))
        await _push(wa, _voice("E2", "V2"))
        await _until(lambda: ears.started == ["two"], "E2 transcribing")
        await _push(wa, _voice("E3", "V3"))            # during E2's decode
        await asyncio.sleep(0.2)
        ears.gates["two"].set()
        await _until(lambda: len(brain.seen) == 3, "E3's turn running")
        await _push(wa, _text("E4", "four"))            # E3 mid-answer
        await _push(wa, _voice("E5", "V5"))
        await asyncio.sleep(0.2)
        brain.gates["three"].set()
        await _until(lambda: len(_requests(records, "/reply")) == 5,
                     "five replies at the sidecar")
        await asyncio.sleep(0.3)                       # nothing extra comes
        replies = [r["body"] for r in _requests(records, "/reply")]
        assert replies == [
            {"jid": OWN, "text": f"answer to {w}", "quote": q}
            for w, q in (("one", "E1"), ("two", "E2"), ("three", "E3"),
                         ("four", "E4"), ("five", "E5"))]
        err = capfd.readouterr().err
        for mid, shape in (("E1", "text"), ("E2", "voice"), ("E3", "voice"),
                           ("E4", "text"), ("E5", "voice")):
            assert f"[whatsapp] owner message {mid} received ({shape})" in err
            assert f"[whatsapp] reply to {mid} sent" in err
        assert "answer to" not in err, "message content reached the log"
    finally:
        await wa.stop()


@pytest.mark.asyncio
async def test_a_reply_that_cannot_be_delivered_is_logged_with_its_message_id(
        tmp_path, capfd):
    class Refusing:
        questions = None

        async def reply(self, where, *, text=None, quote=None, voice=None):
            raise phone.ChannelError("the chat app said no")

        async def presence(self, where, state):
            pass

        async def media(self, ref):
            raise AssertionError("no media here")

    hub = _hub(tmp_path, Recorder("private words"))
    turns = phone.PhoneTurns(hub, Refusing(), kind="whatsapp",
                             text_limit=100, max_file_mb=1, busy_poll_s=0.01,
                             queue_folder=tmp_path / "phone")
    await turns.message(phone.Message(where=OWN, id="F1",
                                      text="secret question"))
    await _until(lambda: all(t.done() for t in turns._workers.values()),
                 "the turn")
    err = capfd.readouterr().err
    assert "[whatsapp] owner message F1 received (text)" in err
    assert ("[whatsapp] reply to F1 was NOT delivered: the chat app said no"
            in err)
    assert "secret question" not in err and "private words" not in err


@pytest.mark.asyncio
async def test_TELEGRAM_back_to_back_messages_are_each_answered_in_order(
        tmp_path, bot, no_ffmpeg, capfd):
    """The same guarantee over Telegram (same `phone.PhoneTurns`): a voice
    note held in transcription, a text behind it, a text during a turn."""
    brain = Echo()
    hub = _simple_hub(tmp_path, brain)
    ears = HeldEars(["two"])
    hub.asr.transcribe = ears.transcribe
    ears.gates["two"] = asyncio.Event()
    brain.gates["three"] = asyncio.Event()
    bot.files["VOICE2"] = b"ogg bytes"
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(1, "one"))
        bot.push(_message(2, voice={"file_id": "VOICE2", "duration": 2,
                                    "mime_type": "audio/ogg",
                                    "file_size": 9}))
        await _until(lambda: ears.started == ["two"], "2 transcribing")
        bot.push(_message(3, "three"))
        await asyncio.sleep(0.2)
        ears.gates["two"].set()
        await _until(lambda: len(brain.seen) == 3, "3's turn running")
        bot.push(_message(4, "four"))
        await asyncio.sleep(0.2)
        brain.gates["three"].set()
        await _until(lambda: len(_said(bot)) == 4, "four answers")
        await asyncio.sleep(0.3)
        assert _said(bot) == [("answer to one", 1), ("answer to two", 2),
                              ("answer to three", 3), ("answer to four", 4)]
        err = capfd.readouterr().err
        for mid in (1, 2, 3, 4):
            assert f"[telegram] owner message {mid} received" in err
            assert f"[telegram] reply to {mid} sent" in err
    finally:
        await _close(hub, tg)
