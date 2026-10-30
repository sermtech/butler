"""A file the agent SHOWED on a phone turn is also sent to that phone chat
(docs/rich-chat.md O-1): after the text answer, quoted to the message, image
audio and video inline, anything else as a document; a layout (`ui`) sends
nothing; an oversize file, a missing one and a refused one are each SAID as
one line. Telegram picks its Bot API method from the BYTES, not the name.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_telegram_inbound import OWNER, _close, _open, _ready  # noqa: E402
from test_telegram_inbound import _simple_hub, bot  # noqa: E402,F401
from test_telegram_inbound import no_real_telegram  # noqa: E402,F401
from test_whatsapp_inbound import Recorder                      # noqa: E402
from test_attachments import PNG                                # noqa: E402
from daemon import phone                                        # noqa: E402
from daemon import whatsapp as wa_mod                           # noqa: E402

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n" + b"x" * 64
OWN = "own@lid"


class Store:
    """`hub.artifacts.find(chat, id)` over files in a folder."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.entries: dict[str, dict] = {}

    def add(self, aid: str, name: str, data: bytes, kind: str, **extra):
        path = self.folder / f"{aid}.data"
        path.write_bytes(data)
        entry = {"id": aid, "kind": kind, "media": "x/y", "name": name,
                 "size": len(data), **extra}
        self.entries[aid] = entry
        return entry

    def find(self, chat, aid):
        if chat != "chat-1" or aid not in self.entries:
            return None
        return self.folder / f"{aid}.data", self.entries[aid]


class Channel:
    def __init__(self, fail_files: bool = False):
        self.sent: list[dict] = []
        self.fail_files = fail_files

    async def reply(self, where, *, text=None, quote=None, voice=None,
                    file=None, caption=None):
        if file is not None and self.fail_files:
            raise phone.ChannelError("the chat app said no")
        self.sent.append({"where": where, "text": text, "quote": quote,
                          "file": file, "caption": caption})
        return "1"


def _turns(tmp_path, channel, store, max_file_mb=1):
    hub = SimpleNamespace(settings=SimpleNamespace(get=lambda k: "text"),
                          artifacts=store)
    return phone.PhoneTurns(hub, channel, kind="whatsapp", text_limit=1000,
                            max_file_mb=max_file_mb,
                            queue_folder=tmp_path / "phone")


def _watch(*artifacts, chat="chat-1", answer="Here it is."):
    watch = phone._Watch("chat-1")
    watch.frame({"type": "reply", "chat": "chat-1", "text": answer})
    for entry in artifacts:
        watch.frame({"type": "artifact", "chat": chat, "role": "assistant",
                     "artifact": entry})
    return watch


MSG = phone.Message(where=OWN, id="M1", text="show me")


@pytest.mark.asyncio
async def test_a_shown_image_follows_the_text_quoted_to_the_message(tmp_path):
    store = Store(tmp_path)
    entry = store.add("a1", "chart.png", PNG, "image", caption="the chart")
    channel = Channel()
    await _turns(tmp_path, channel, store)._deliver(
        MSG, _watch(entry), None, False)
    assert [s["text"] for s in channel.sent] == ["Here it is.", None]
    assert channel.sent[1] == {"where": OWN, "text": None, "quote": "M1",
                               "file": ("chart.png", PNG),
                               "caption": "the chart"}


@pytest.mark.asyncio
async def test_a_ui_artifact_sends_nothing_extra(tmp_path):
    store = Store(tmp_path)
    entry = store.add("u1", "layout.json", b"{}", "ui")
    channel = Channel()
    await _turns(tmp_path, channel, store)._deliver(
        MSG, _watch(entry), None, False)
    assert [s["text"] for s in channel.sent] == ["Here it is."]


@pytest.mark.asyncio
async def test_an_artifact_for_another_chat_is_not_sent(tmp_path):
    store = Store(tmp_path)
    entry = store.add("a1", "chart.png", PNG, "image")
    channel = Channel()
    await _turns(tmp_path, channel, store)._deliver(
        MSG, _watch(entry, chat="chat-2"), None, False)
    assert [s["text"] for s in channel.sent] == ["Here it is."]


@pytest.mark.asyncio
async def test_a_user_role_artifact_is_not_collected():
    watch = phone._Watch("chat-1")
    watch.frame({"type": "artifact", "chat": "chat-1", "role": "user",
                 "artifact": {"id": "a1"}})
    assert watch.shown == []


@pytest.mark.asyncio
async def test_an_oversize_file_is_said_as_one_line(tmp_path):
    store = Store(tmp_path)
    entry = store.add("a1", "big.pdf", PDF + b"0" * (2 * 1024 * 1024), "file")
    channel = Channel()
    await _turns(tmp_path, channel, store)._deliver(
        MSG, _watch(entry), None, False)
    assert [s["file"] for s in channel.sent] == [None, None]
    assert channel.sent[1]["text"] == "big.pdf is too large to send here (2 MB)."
    assert channel.sent[1]["quote"] == "M1"


@pytest.mark.asyncio
async def test_a_refused_file_is_said_and_logged_never_fatal(tmp_path, capfd):
    store = Store(tmp_path)
    first = store.add("a1", "one.pdf", PDF, "file")
    second = store.add("a2", "two.pdf", PDF, "file")
    channel = Channel(fail_files=True)
    await _turns(tmp_path, channel, store)._deliver(
        MSG, _watch(first, second), None, False)
    assert [s["text"] for s in channel.sent] == [
        "Here it is.", "I could not send one.pdf here.",
        "I could not send two.pdf here."]
    assert ("[whatsapp] shown file one.pdf for M1 was NOT delivered: "
            "the chat app said no") in capfd.readouterr().err


@pytest.mark.asyncio
async def test_a_missing_artifact_and_no_store_are_said(tmp_path):
    store = Store(tmp_path)
    ghost = {"id": "nope", "kind": "image", "name": "ghost.png"}
    for artifacts in (store, None):
        channel = Channel()
        await _turns(tmp_path, channel, artifacts)._deliver(
            MSG, _watch(ghost), None, False)
        assert [s["text"] for s in channel.sent] == [
            "Here it is.",
            "I could not send ghost.png: the file is no longer available."]


@pytest.mark.asyncio
async def test_whatsapp_reply_posts_one_file_with_a_caption_and_no_ptt(
        tmp_path):
    wa = wa_mod.WhatsApp(SimpleNamespace(get=lambda k: None), tmp_path)
    wa.unavailable = lambda: None
    seen = {}

    async def call(method, path, body, form=None, timeout_s=None):
        seen.update(path=path, body=body, form=form)
        return 200, {"messageId": "W9"}
    wa._call = call
    got = await wa.reply(OWN, file=("chart.png", PNG), caption="the chart",
                         quote="Q1")
    assert got == "W9" and seen["path"] == "/reply" and seen["body"] is None
    fields = {o["name"]: (o.get("filename"), v)
              for o, _h, v in seen["form"]._fields}
    assert fields["jid"][1] == OWN and fields["quote"][1] == "Q1"
    assert fields["caption"][1] == "the chart"
    assert fields["files"] == ("chart.png", PNG)
    assert "ptt" not in fields


@pytest.mark.asyncio
async def test_telegram_picks_the_method_by_the_bytes_not_the_name(
        tmp_path, bot):
    hub = _simple_hub(tmp_path, Recorder())
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        await tg.reply(str(OWNER), file=("photo.png", PNG), caption="cap",
                       quote="5")
        await tg.reply(str(OWNER), file=("report.png", PDF))
        await tg.reply(str(OWNER), file=("notes.txt", b"plain words"))
        sent = [r for r in bot.requests
                if r["method"] in ("sendPhoto", "sendDocument")]
        assert [r["method"] for r in sent] == ["sendPhoto", "sendDocument",
                                               "sendDocument"]
        photo = sent[0]["body"]
        assert photo["photo"] == {"filename": "photo.png",
                                  "content_type": "application/octet-stream",
                                  "bytes": PNG}
        assert photo["caption"] == "cap"
        assert '"message_id": 5' in photo["reply_parameters"]
        assert sent[1]["body"]["document"]["filename"] == "report.png"
        assert "caption" not in sent[1]["body"]
    finally:
        await _close(hub, tg)
