"""Two-way Telegram (docs/messaging.md section 8, phases T1..T3).

AC-528 (buttons for confirm and select, a reply for input, tokens in memory
only), AC-530 (owner only), AC-531 (the offset persists: a restart neither
replays nor loses an update), AC-532 (a second reader is the channel's
problem and polling backs off), AC-533 (messages are owner turns in the
`phone_chat` target, voice notes as AC-522, replies as AC-539).

Driven through the REAL adapter and its real long-poll loop against
`FakeBotApi` (`test_notify.py`), a local HTTP server that keeps Telegram's
offset rules. The autouse fixture points the adapter at a dead local port
first, so nothing here can reach api.telegram.org. ffmpeg is replaced at
`phone.decode_audio` / `phone.encode_voice_note`.

The assertion with teeth for questions is the ANSWER COUNT ON THE CHILD
(`test_harness_question.py`'s rule): a tap reaches a blocked coding agent
and unblocks it, so every "this is not an answer" also says the child was
handed nothing.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session                            # noqa: E402
from test_attachments import PNG                               # noqa: E402
from test_automations import _finish, _project, _with_clock    # noqa: E402
from test_library_voice import _harness_hub                    # noqa: E402
from test_notify import CHAT, TOKEN, FakeBotApi, _write        # noqa: E402
from test_schedule import EASTERN                              # noqa: E402
from test_whatsapp_inbound import Recorder                     # noqa: E402
from test_whatsapp_inbound import _hub as _plain_hub           # noqa: E402
from test_whatsapp_questions import CONFIRM, SELECT, Children  # noqa: E402
from daemon import butlerd, notify, phone, ports               # noqa: E402
from daemon.adapters import notify_telegram                    # noqa: E402

OWNER = int(CHAT)
STRANGER = 424242
GROUP = -1001234
VOICE_PCM = b"\x10\x20" * 16000
HEAD = "Butler needs you"


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """A dead local port as the default API base: nothing in this file can
    reach Telegram by forgetting to name the fake."""
    monkeypatch.setattr(notify_telegram, "API", "http://127.0.0.1:9")


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBotApi()
    monkeypatch.setattr(notify_telegram, "API", fake.url)
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def no_ffmpeg(monkeypatch):
    encoded = []
    monkeypatch.setattr(phone, "decode_audio", lambda data: VOICE_PCM)

    def encode(pcm, rate):
        encoded.append((pcm, rate))
        return b"OggS" + b"\x00" * 16
    monkeypatch.setattr(phone, "encode_voice_note", encode)
    return encoded


# --------------------------------------------------------------------------- #
# updates, as Telegram sends them
# --------------------------------------------------------------------------- #
def _message(mid: int, text: str = "", *, chat: int = OWNER,
             frm: int = OWNER, reply_to: int | None = None, **extra) -> dict:
    m = {"message_id": mid, "date": 1790950000,
         "chat": {"id": chat, "type": "private" if chat > 0 else "group"},
         "from": {"id": frm, "is_bot": False, "first_name": "Someone"}}
    if text:
        m["text"] = text
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to,
                                 "chat": {"id": chat}}
    m.update(extra)
    return {"message": m}


def _tap(question_mid: int, data: str, *, frm: int = OWNER,
         chat: int = OWNER, cid: str = "CQ1") -> dict:
    return {"callback_query": {
        "id": cid, "from": {"id": frm, "is_bot": False},
        "message": {"message_id": question_mid, "chat": {"id": chat}},
        "data": data}}


async def _until(cond, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _offset(folder: Path, name: str = "phone") -> int | None:
    f = folder / f"{name}.offset"
    return json.loads(f.read_text("utf-8"))["offset"] if f.exists() else None


def _said(bot) -> list[tuple[str, int | None]]:
    """Every text Butler sent, with the message id it quoted."""
    return [(r["body"]["text"],
             (r["body"].get("reply_parameters") or {}).get("message_id"))
            for r in bot.calls("sendMessage")]


def _questions(bot) -> list[dict]:
    return [r for r in bot.calls("sendMessage")
            if r["body"]["text"].startswith(HEAD)]


def _buttons(question: dict) -> list[tuple[str, str]]:
    rows = question["body"]["reply_markup"]["inline_keyboard"]
    return [(b["text"], b["callback_data"]) for row in rows for b in row]


async def _open(hub, **timing):
    """The inbound owner exactly as `make_app` wires it, on short clocks,
    and served (the question channel `own()` reaches)."""
    timing = {"rescan_s": 0.05, "poll_s": 1, "retry_backoff_s": (0.05,),
              "conflict_backoff_s": (0.3,), **timing}
    tg = butlerd.telegram_inbound(
        hub, Path(hub.channels.folder).parent / "phone", **timing)
    tg.turns.busy_poll_s = 0.01
    notify_telegram.serve(tg)
    await tg.open()
    return tg


async def _close(hub, tg):
    try:
        for task in list(tg.turns._workers.values()):
            if not task.done():
                task.cancel()
        await hub.phone_questions.stop()
    finally:
        notify_telegram.withdraw(tg)
        await tg.close()


async def _ready(tg, folder: Path):
    await _until(lambda: tg.polling == "phone", "the bot polled")
    await _until(lambda: _offset(folder) is not None, "the offset recorded")


def _simple_hub(tmp_path, brain=None, **settings):
    hub = _plain_hub(tmp_path, brain, **settings)
    hub.channels = notify.Channels(tmp_path / "notify")
    _write(hub.channels.folder, "phone", inbound=True)
    return hub


# --------------------------------------------------------------------------- #
# the channel file (MS-25)
# --------------------------------------------------------------------------- #
def test_inbound_is_optional_so_an_existing_channel_file_still_loads(
        tmp_path):
    folder = tmp_path / "notify"
    _write(folder, "old")
    _write(folder, "two-way", inbound=True)
    channels = {c.name: c for c in notify.Channels(folder).all()}
    assert channels["old"].problem == "" and not channels["old"].notifier.inbound
    assert channels["two-way"].problem == ""
    assert channels["two-way"].notifier.inbound is True


def test_the_add_forms_text_is_stored_as_a_boolean(tmp_path):
    channels = notify.Channels(tmp_path / "notify")
    channels.save({"name": "phone", "kind": "telegram", "label": "",
                   "bot_token": TOKEN, "chat_id": CHAT, "inbound": "true"})
    doc = json.loads((tmp_path / "notify" / "phone.json").read_text("utf-8"))
    assert doc["inbound"] is True
    channels.save({"name": "phone", "kind": "telegram", "label": "",
                   "bot_token": TOKEN, "chat_id": CHAT, "inbound": ""})
    doc = json.loads((tmp_path / "notify" / "phone.json").read_text("utf-8"))
    assert doc["inbound"] is False


@pytest.mark.parametrize("doc,why", [
    ({"inbound": "maybe"}, "inbound must be true or false"),
    ({"inbound": True, "chat_id": "-100123"},
     "inbound needs the owner's own chat: chat_id must be a user id, not a "
     "group"),
])
def test_a_bad_inbound_is_the_channels_problem(tmp_path, doc, why):
    _write(tmp_path / "notify", "phone", **doc)
    [c] = notify.Channels(tmp_path / "notify").all()
    assert c.problem == why


def test_the_inbound_field_is_offered_and_is_not_secret():
    [kind] = [k for k in notify.Channels.kinds() if k["kind"] == "telegram"]
    fields = {f["key"]: f for f in kind["fields"]}
    assert list(fields) == ["bot_token", "chat_id", "inbound"]
    assert fields["bot_token"]["secret"] and fields["chat_id"]["secret"]
    assert not fields["inbound"]["secret"]


def test_telegram_declares_the_inbound_capability():
    """AC-525 for the second kind; the six-ports half is
    `test_whatsapp_questions.py`'s."""
    assert ports.inbound(ports.NOTIFIERS["telegram"])


def test_the_shipped_clocks():
    """The long poll waits longer than a send may (AC-472's 15 s), and a
    409 waits at least half a minute before the next try (AC-532)."""
    assert notify_telegram.TIMEOUT == 15.0
    assert notify_telegram.POLL_TIMEOUT > notify_telegram.TIMEOUT
    assert notify_telegram.POLL_TIMEOUT > notify_telegram.POLL_S
    assert min(notify_telegram.CONFLICT_BACKOFF_S) >= 30.0


# --------------------------------------------------------------------------- #
# AC-530: the owner only
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ONLY_THE_OWNERS_MESSAGES_are_used_and_nothing_else_is_logged(
        tmp_path, bot, capsys):
    """AC-530. Someone else in the owner's chat, the owner in a group, and a
    stranger's own chat are all dropped; the drop lines carry no content and
    never the token; only the owner's message becomes a turn."""
    hub = _simple_hub(tmp_path, Recorder("Done."))
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(11, "SECRET-one", frm=STRANGER))
        bot.push(_message(12, "SECRET-two", chat=GROUP))
        bot.push(_message(13, "SECRET-three", chat=STRANGER, frm=STRANGER))
        bot.push(_tap(99, "abcd 1", frm=STRANGER))
        bot.push(_message(14, "from the owner"))
        await _until(lambda: ("Done.", 14) in _said(bot), "the owner's answer")
        assert [t for t, _ in hub.brain.seen] == ["from the owner"]
        assert _said(bot) == [("Done.", 14)]
        assert bot.calls("answerCallbackQuery") == []
        assert all(r["body"]["chat_id"] == CHAT
                   for r in bot.calls("sendMessage"))
    finally:
        await _close(hub, tg)
    err = capsys.readouterr().err
    assert err.count("dropped an update") == 4, err
    assert "SECRET" not in err and TOKEN not in err


# --------------------------------------------------------------------------- #
# AC-531: the offset
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_first_start_answers_no_backlog_and_records_the_offset(
        tmp_path, bot):
    hub = _simple_hub(tmp_path, Recorder())
    bot.push(_message(1, "old one"))
    last = bot.push(_message(2, "old two"))
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        await asyncio.sleep(0.3)
        assert hub.brain.seen == []
        assert _offset(hub.channels.folder) == last + 1
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_A_RESTART_NEITHER_REPLAYS_NOR_LOSES_an_update(tmp_path, bot):
    """AC-531. One message before the restart, one while Butler is down:
    each becomes exactly one turn, and the restarted poller asks from the
    persisted offset."""
    hub = _simple_hub(tmp_path, Recorder("ok"))
    folder = hub.channels.folder
    tg = await _open(hub)
    try:
        await _ready(tg, folder)
        first = bot.push(_message(21, "before the restart"))
        await _until(lambda: ("ok", 21) in _said(bot), "the first answer")
        await _until(lambda: _offset(folder) == first + 1, "the offset")
    finally:
        await _close(hub, tg)
    bot.push(_message(22, "while Butler was down"))
    asked_before = len(bot.calls("getUpdates"))
    tg = await _open(hub)
    try:
        await _until(lambda: ("ok", 22) in _said(bot), "the missed message")
        await asyncio.sleep(0.3)
        assert [t for t, _ in hub.brain.seen] == ["before the restart",
                                                  "while Butler was down"]
        assert bot.calls("getUpdates")[asked_before]["body"]["offset"] == (
            first + 1)
    finally:
        await _close(hub, tg)


# --------------------------------------------------------------------------- #
# AC-532: another program reads this bot
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_409_is_the_channels_problem_and_polling_BACKS_OFF(
        tmp_path, bot, capsys):
    hub = _simple_hub(tmp_path, Recorder())
    bot.updates_mode = "409"
    tg = await _open(hub, conflict_backoff_s=(0.3,))
    try:
        await _until(lambda: bot.calls("getUpdates"), "the first poll")
        await _until(lambda: [r["problem"] for r in hub.channels.listing("")]
                     == [notify_telegram.CONFLICT], "the problem shown")
        start = len(bot.calls("getUpdates"))
        await asyncio.sleep(1.0)
        polls = len(bot.calls("getUpdates")) - start
        assert polls <= 5, f"{polls} polls in one second: a tight loop"
        bot.updates_mode = "ok"
        await _until(lambda: [r["problem"] for r in hub.channels.listing("")]
                     == [""], "the problem cleared")
    finally:
        await _close(hub, tg)
    assert TOKEN not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_while_another_program_reads_the_bot_no_question_is_sent_there(
        tmp_path, bot):
    """AC-532 meets AC-528: during a 409 the other program receives the
    taps, so the channel is not offered for questions until reading works
    again."""
    hub = _simple_hub(tmp_path, Recorder())
    tg = await _open(hub, conflict_backoff_s=(0.3,))
    try:
        await _ready(tg, hub.channels.folder)
        assert notify_telegram.TelegramNotifier.own(hub.settings) is tg
        bot.updates_mode = "409"
        await _until(lambda: [r["problem"] for r in hub.channels.listing("")]
                     == [notify_telegram.CONFLICT], "the problem shown")
        assert notify_telegram.TelegramNotifier.own(hub.settings) is None
        bot.updates_mode = "ok"
        await _until(lambda: notify_telegram.TelegramNotifier.own(
            hub.settings) is tg, "offered again")
    finally:
        await _close(hub, tg)


def test_the_conflict_line_is_the_specs():
    assert notify_telegram.CONFLICT == (
        "another program is reading this bot: use a separate bot for Butler")


@pytest.mark.asyncio
async def test_two_inbound_channels_poll_neither_and_say_why(tmp_path, bot):
    hub = _simple_hub(tmp_path, Recorder())
    _write(hub.channels.folder, "second", inbound=True)
    tg = await _open(hub)
    try:
        want = notify_telegram.TOO_MANY.format(n=2)
        await _until(lambda: [r["problem"] for r in hub.channels.listing("")]
                     == [want, want], "both refused")
        assert bot.calls("getUpdates") == [] and tg.polling == ""
        assert notify_telegram.TelegramNotifier.own(hub.settings) is None
    finally:
        await _close(hub, tg)


# --------------------------------------------------------------------------- #
# AC-533: owner turns in the phone_chat target
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_message_is_an_owner_turn_in_the_root_chat_answered_quoted(
        tmp_path, monkeypatch, bot):
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    hub.settings.set("phone_reply", "text")
    _write(hub.channels.folder, "phone", inbound=True)
    page = attach_session(hub, "page")
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(31, "first from telegram"))
        await _until(lambda: [q for _, q in _said(bot)] == [31],
                     "the answer")
        root = hub.shortcuts.root_id()
        [chat] = await asyncio.to_thread(hub.brain.session_list, None,
                                         hub.project_filter(root))
        record = await asyncio.to_thread(hub.brain.session_chat, chat["id"])
        assert [m["content"] for m in record["messages"]
                if m["role"] == "user"] == ["first from telegram"]
        assert hub.last_owner_chat == chat["id"]
        frames = [json.loads(m) for m in page.ws.sent if isinstance(m, str)]
        finals = [f for f in frames if f["type"] == "final"]
        assert finals[0]["reason"] == "telegram"
        assert [r["body"]["action"] for r in bot.calls("sendChatAction")][
            :1] == ["typing"]
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_new_from_telegram_starts_a_fresh_chat(tmp_path, monkeypatch,
                                                     bot):
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    hub.settings.set("phone_reply", "text")
    _write(hub.channels.folder, "phone", inbound=True)
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(41, "in the old chat"))
        await _until(lambda: [q for _, q in _said(bot)] == [41], "answer 1")
        bot.push(_message(42, "/new"))
        await _until(lambda: (hub.NEW_CONVERSATION, 42) in _said(bot),
                     "the new chat")
        bot.push(_message(43, "in the new chat"))
        await _until(lambda: 43 in [q for _, q in _said(bot)], "answer 2")
        root = hub.shortcuts.root_id()
        rows = await asyncio.to_thread(hub.brain.session_list, None,
                                       hub.project_filter(root))
        assert len(rows) == 2
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_long_answer_is_split_at_telegrams_4096(tmp_path, bot):
    line = "x" * 999 + "\n"
    hub = _simple_hub(tmp_path, Recorder(line * 9))     # 9,000 characters
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(51, "long please"))
        await _until(lambda: len(_said(bot)) == 3, "three parts")
        assert all(len(t) <= 4096 and q == 51 for t, q in _said(bot))
        assert sum(t.count("x") for t, _ in _said(bot)) == 999 * 9
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_voice_note_is_transcribed_and_the_reply_is_a_voice_note_then_text(  # noqa: E501
        tmp_path, bot, no_ffmpeg):
    """AC-533 via AC-522 and AC-539: Butler's recognizer hears the note
    (fetched with getFile), the warning prefix is added, and with
    `voice_text` the answer goes back as an OGG/Opus `sendVoice` quoted to
    the note, then as text."""
    hub = _simple_hub(tmp_path, Recorder("Heard you."),
                      phone_reply="voice_text")
    hub.asr._script = ["bom", "dia"]
    bot.files["VOICE1"] = b"ogg bytes"
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(61, voice={"file_id": "VOICE1", "duration": 2,
                                     "mime_type": "audio/ogg",
                                     "file_size": 9}))
        await _until(lambda: _said(bot), "the text answer")
        [(text, _ctx)] = hub.brain.seen
        assert text == phone.VOICE_PREFIX + "\nbom dia"
        assert [r["body"]["file_id"] for r in bot.calls("getFile")] == [
            "VOICE1"]
        [voice] = bot.calls("sendVoice")
        assert voice["body"]["chat_id"] == CHAT
        assert voice["body"]["voice"]["bytes"].startswith(b"OggS")
        assert json.loads(voice["body"]["reply_parameters"])[
            "message_id"] == 61
        assert _said(bot) == [("Heard you.", 61)]
        assert bot.requests.index(voice) < bot.requests.index(
            bot.calls("sendMessage")[0])
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_photo_is_stored_and_attached_to_the_owner_turn(tmp_path,
                                                                bot):
    """MS-18b IN: the LARGEST size of a photo is fetched, stored by the
    AttachmentStore exactly as a page upload, and attached; no caption asks
    what is in it."""
    hub = _simple_hub(tmp_path, Recorder())
    bot.files["BIG"] = PNG
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(71, photo=[
            {"file_id": "SMALL", "width": 90, "height": 90, "file_size": 10},
            {"file_id": "BIG", "width": 800, "height": 800,
             "file_size": len(PNG)}]))
        await _until(lambda: hub.brain.seen, "the turn")
        text, ctx = hub.brain.seen[0]
        assert text == phone.IMAGE_QUESTION
        [att] = ctx.attachments
        assert att.kind == "image" and Path(att.path).read_bytes() == PNG
        assert [r["body"]["file_id"] for r in bot.calls("getFile")] == ["BIG"]
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_file_over_telegrams_20_MB_is_refused_before_download(
        tmp_path, bot):
    hub = _simple_hub(tmp_path, Recorder())
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(81, document={"file_id": "HUGE",
                                        "file_name": "big.pdf",
                                        "file_size": 30 * 1024 * 1024}))
        await _until(lambda: _said(bot), "the refusal")
        assert _said(bot) == [(phone.TOO_LARGE.format(mb=30, limit=20), 81)]
        assert bot.calls("getFile") == [] and hub.brain.seen == []
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_message_butler_cannot_read_is_answered_not_dropped(
        tmp_path, bot):
    hub = _simple_hub(tmp_path, Recorder())
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        bot.push(_message(91, sticker={"file_id": "S1"}))
        await _until(lambda: _said(bot), "the line")
        assert _said(bot) == [(notify_telegram.UNSUPPORTED, 91)]
        assert hub.brain.seen == []
    finally:
        await _close(hub, tg)


# --------------------------------------------------------------------------- #
# AC-528: questions as buttons
# --------------------------------------------------------------------------- #
async def _live(tmp_path, monkeypatch, children, **settings):
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    hub.settings.set("phone_reply", "text")
    for key, value in settings.items():
        hub.settings.set(key, value)
    hub.brain.host._harness_for = children
    _write(hub.channels.folder, "phone", inbound=True)
    tg = await _open(hub)
    await _ready(tg, hub.channels.folder)
    return hub, tg


def et(*args):
    return datetime(*args, tzinfo=EASTERN)


async def _run(hub, recipe_name, project, clock):
    recipe = hub.library.recipes.save(None, recipe_name, project,
                                      f"{recipe_name} now.")
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, clock())
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    return a


@pytest.mark.asyncio
async def test_a_confirm_is_two_buttons_and_a_TAP_answers_it(
        tmp_path, monkeypatch, bot):
    """AC-528: the question from a Telegram turn goes back to Telegram with
    Yes/No buttons whose callback data is a short token (64-byte limit);
    the tap reaches the child once, is acknowledged, and the question
    message is edited to say what was answered, buttons gone."""
    kids = Children(CONFIRM)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(101, "tidy the drafts"))
        await _until(lambda: _questions(bot), "the question")
        [q] = _questions(bot)
        assert q["body"]["text"] == (
            "Butler needs you (tidy the drafts): Delete 40 old drafts?")
        buttons = _buttons(q)
        assert [t for t, _ in buttons] == ["Yes", "No"]
        assert all(len(d.encode()) <= 64 for _, d in buttons)
        qmid = q["result"]["message_id"]
        bot.push(_tap(qmid, buttons[0][1], cid="TAP1"))
        await _until(lambda: kids.answers(), "the child answered")
        assert kids.answers() == [("q-confirm", ports.Answer(confirmed=True))]
        await _until(lambda: bot.calls("editMessageText"), "the edit")
        [ack] = bot.calls("answerCallbackQuery")
        assert ack["body"]["callback_query_id"] == "TAP1"
        assert ack["body"]["text"] == "Answered: yes"
        [edit] = bot.calls("editMessageText")
        assert edit["body"]["message_id"] == qmid
        assert edit["body"]["text"].endswith("\n\nAnswered: yes")
        assert "reply_markup" not in edit["body"]
        # MS-23: the token map is memory; the only files are the channel's.
        assert sorted(p.name for p in hub.channels.folder.iterdir()) == [
            "phone.json", "phone.offset"]
        await _until(lambda: 101 in [m for _, m in _said(bot)],
                     "the turn's own answer")
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_select_is_one_button_per_option(tmp_path, monkeypatch, bot):
    kids = Children(SELECT)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(111, "the backlog"))
        await _until(lambda: _questions(bot), "the question")
        [q] = _questions(bot)
        buttons = _buttons(q)
        assert [t for t, _ in buttons] == list(SELECT.options)
        assert len(q["body"]["reply_markup"]["inline_keyboard"]) == 3
        bot.push(_tap(q["result"]["message_id"], buttons[1][1]))
        await _until(lambda: kids.answers(), "the child answered")
        assert kids.answers() == [("q-select", ports.Answer(value="Skip it"))]
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_an_input_is_answered_by_REPLYING_to_the_question(
        tmp_path, monkeypatch, bot):
    ask = ports.Ask(qid="q-input", method="input", message="Branch name?")
    kids = Children(ask)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(121, "make a branch"))
        await _until(lambda: _questions(bot), "the question")
        [q] = _questions(bot)
        assert q["body"]["reply_markup"]["force_reply"] is True
        assert q["body"]["text"].endswith(
            "\nReply to this message with your answer.")
        bot.push(_message(122, "feature/phone",
                          reply_to=q["result"]["message_id"]))
        await _until(lambda: kids.answers(), "the child answered")
        assert kids.answers() == [("q-input",
                                   ports.Answer(value="feature/phone"))]
        await _until(lambda: ("Answered: feature/phone", 122) in _said(bot),
                     "the answered line")
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_LATE_TAP_gets_the_closed_reason_and_never_reaches_the_child(
        tmp_path, monkeypatch, bot):
    kids = Children(CONFIRM)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(131, "tidy the drafts"))
        await _until(lambda: _questions(bot), "the question")
        [q] = _questions(bot)
        got = await hub.answer_question("q-confirm",
                                        ports.Answer(confirmed=False))
        assert got["ok"]
        bot.push(_tap(q["result"]["message_id"], _buttons(q)[0][1]))
        await _until(lambda: bot.calls("answerCallbackQuery"), "the ack")
        [ack] = bot.calls("answerCallbackQuery")
        assert ack["body"]["text"] == ("That question was already closed: "
                                       "that question was already answered")
        assert kids.answers() == [("q-confirm",
                                   ports.Answer(confirmed=False))]
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_tap_butler_does_not_know_is_acknowledged_and_answers_nothing(
        tmp_path, monkeypatch, bot):
    """A button from before a restart: its token is gone with the memory
    that held it. The tap is acknowledged (no spinner left) and nothing is
    answered."""
    kids = Children(CONFIRM)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(141, "tidy the drafts"))
        await _until(lambda: _questions(bot), "the question")
        bot.push(_tap(9999, "deadbeef 1"))
        await _until(lambda: bot.calls("answerCallbackQuery"), "the ack")
        assert bot.calls("answerCallbackQuery")[0]["body"]["text"] == (
            notify_telegram.UNKNOWN_TAP)
        assert kids.answers() == []
        await hub.answer_question("q-confirm", ports.Answer(confirmed=False))
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_with_TWO_open_a_tap_answers_ITS_question_and_a_bare_1_neither(
        tmp_path, monkeypatch, bot):
    """AC-528 with AC-527's rule: a button names its question, so a tap on
    B answers B and leaves A open; an UNQUOTED "1" typed while both are open
    could mean either, so it answers neither and becomes an ordinary turn."""
    kids = Children(
        ports.Ask(qid="q-a", method="confirm", message="Delete A?"),
        ports.Ask(qid="q-b", method="confirm", message="Delete B?"))
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
        a = await _run(hub, "Tidy A", _project(hub, tmp_path, "a", "A"),
                       clock)
        await _until(lambda: len(_questions(bot)) == 1, "question A")
        b = await _run(hub, "Tidy B", _project(hub, tmp_path, "b", "B"),
                       clock)
        await _until(lambda: len(_questions(bot)) == 2, "question B")
        qa, qb = _questions(bot)
        assert "Delete A?" in qa["body"]["text"]
        assert "Delete B?" in qb["body"]["text"]

        bot.push(_message(151, "1"))
        await _until(lambda: 151 in [m for _, m in _said(bot)],
                     "the ordinary answer to 151")
        assert kids.answers() == [], (
            f"an unquoted '1' with two questions open reached a child: "
            f"{kids.answers()}")

        bot.push(_tap(qb["result"]["message_id"], _buttons(qb)[0][1]))
        await _until(lambda: kids.answers(), "the tap's answer")
        assert kids.answers() == [("q-b", ports.Answer(confirmed=True))]
        assert hub.open_question("q-a") is not None
        await hub.answer_question("q-a", ports.Answer(confirmed=False))
        await _finish(hub)
        assert [hub.automations.runs(x["id"])[0]["status"]
                for x in (a, b)] == ["completed", "completed"]
    finally:
        await _close(hub, tg)


@pytest.mark.asyncio
async def test_a_strangers_tap_is_dropped(tmp_path, monkeypatch, bot):
    kids = Children(CONFIRM)
    hub, tg = await _live(tmp_path, monkeypatch, kids)
    try:
        bot.push(_message(161, "tidy the drafts"))
        await _until(lambda: _questions(bot), "the question")
        [q] = _questions(bot)
        bot.push(_tap(q["result"]["message_id"], _buttons(q)[0][1],
                      frm=STRANGER))
        await asyncio.sleep(0.5)
        assert kids.answers() == []
        assert bot.calls("answerCallbackQuery") == []
        await hub.answer_question("q-confirm", ports.Answer(confirmed=False))
    finally:
        await _close(hub, tg)
