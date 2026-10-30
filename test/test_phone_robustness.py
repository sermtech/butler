"""The daemon's phone path survives restarts and edge cases
(docs/messaging.md 6.5, RV-13..RV-18).

AC-599, AC-600 (RV-13): the phone queue is a file; after a restart a message
that never STARTED runs again in order, the one in flight is NOT run again
and its chat is told, an answered one is never repeated -- WhatsApp and
Telegram alike. AC-601 (RV-14): WhatsApp stopping is said ONCE per stop,
through `notify_default` (never a WhatsApp channel) and at the desk.
AC-602 (RV-15): a voice note is heard before the question rules see it, so
a spoken "sim" answers while the asking turn is still in flight. AC-603
(RV-16): a recording over 10 minutes is refused before transcription.
AC-604 (RV-17): `/new` waits its turn. AC-605 (RV-18): one reply path, by
source.

A RESTART is simulated the way a process death looks from the file: the
first `Inbound`'s workers are cancelled mid-turn (nothing else is written),
and a second `Inbound` on ANOTHER hub reads the same queue folder.
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_telegram_inbound import (OWNER, _close, _open,  # noqa: E402
                                   _ready, _said)
from test_telegram_inbound import _simple_hub, bot  # noqa: E402,F401
from test_telegram_inbound import no_real_telegram  # noqa: E402,F401
from test_whatsapp_inbound import (OWN, FakeWa, Recorder, _chats,  # noqa: E402
                                   _hub, _inbound, _msg, _phone_hub, _settle,
                                   _until, _user_lines, _voice_media,
                                   no_ffmpeg)  # noqa: F401
from test_attachments import PNG                            # noqa: E402
from daemon import notify, phone, ports                     # noqa: E402
from daemon import whatsapp as wa_mod                       # noqa: E402
from daemon.adapters import notify_telegram                 # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _hub_in(folder: Path, brain):
    folder.mkdir(parents=True, exist_ok=True)
    return _hub(folder, brain)


def _wa_inbound(hub, folder: Path, wa=None):
    wa = wa or FakeWa()
    return wa_mod.Inbound(hub, wa, queue_folder=folder, busy_poll_s=0.01,
                          typing_every_s=60.0), wa


def _file(folder: Path, kind: str = "whatsapp") -> list[dict]:
    return json.loads((folder / f"{kind}.json").read_text("utf-8"))[
        "messages"]


async def _crash(turns) -> None:
    """A process death, as the queue file sees it: every worker stops where
    it is and nothing more is written."""
    workers = list(turns._workers.values())
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


def _hhmm(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).strftime("%H:%M")


# --------------------------------------------------------------------------- #
# RV-13: a restart neither loses nor repeats a message (AC-599, AC-600)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_restart_reruns_the_waiting_in_order_and_tells_the_one_in_flight(
        tmp_path):
    folder = tmp_path / "phone"
    gate = asyncio.Event()
    hub_a = _hub_in(tmp_path / "a", Recorder("first answer", gate=gate))
    before, _wa = _wa_inbound(hub_a, folder)
    try:
        await before.owner(_msg("M1", "first"))
        await _until(lambda: len(hub_a.brain.seen) == 1, "the first turn")
        await before.owner(_msg("M2", "second"))
        await before.owner(_msg("M3", "third"))
        on_disk = _file(folder)
        assert [(e["id"], e["state"]) for e in on_disk] == [
            ("M1", "started"), ("M2", "waiting"), ("M3", "waiting")]
        assert on_disk[1]["text"] == "second"
        arrived = on_disk[0]["arrived"]
        await _crash(before)
    finally:
        gate.set()

    hub_b = _hub_in(tmp_path / "b", Recorder("answered after"))
    after, wa = _wa_inbound(hub_b, folder)
    # The first NEW message resumes the old ones first, so they keep their
    # place ahead of it.
    await after.owner(_msg("M4", "fourth"))
    await _settle(after)
    assert [t for t, _ in hub_b.brain.seen] == ["second", "third", "fourth"], (
        "the waiting messages did not run again in order, or the one in "
        "flight ran twice")
    assert (phone.RESTARTED_LINE.format(at=_hhmm(arrived)), None) in wa.said
    assert [q for t, q in wa.said if t == "answered after"] == [
        "M2", "M3", "M4"]
    assert _file(folder) == []

    # A third process: everything was answered, so nothing runs or is said.
    hub_c = _hub_in(tmp_path / "c", Recorder("never"))
    again, wa_c = _wa_inbound(hub_c, folder)
    await again.resume()
    await _settle(again)
    assert hub_c.brain.seen == [] and wa_c.said == []


@pytest.mark.asyncio
async def test_a_waiting_voice_note_keeps_its_transcript_and_a_lost_file_is_said(
        tmp_path, monkeypatch, no_ffmpeg):
    """A voice note heard before the restart is not fetched again (the
    sidecar's media cache dies with it); an image that cannot be fetched
    any more gets a line naming it, never silence."""
    folder = tmp_path / "phone"
    heard = []

    async def transcribe(pcm):
        heard.append(pcm)
        return "call the bank"
    gate = asyncio.Event()
    hub_a = _hub_in(tmp_path / "a", Recorder("ok", gate=gate))
    monkeypatch.setattr(hub_a, "transcribe_clip", transcribe)
    before, wa_a = _wa_inbound(hub_a, folder)
    wa_a.files["V1"] = b"ogg"
    wa_a.files["IMG"] = b"png"
    try:
        await before.owner(_msg("M1", "first"))
        await _until(lambda: len(hub_a.brain.seen) == 1, "the first turn")
        await before.owner(_msg("V", "", media=_voice_media("V1")))
        await before.owner(_msg("I", "look", media={
            "kind": "image", "id": "IMG", "bytes": 3, "tooLarge": False}))
        assert [e["heard"] for e in _file(folder)] == [
            None, "call the bank", None]
        await _crash(before)
    finally:
        gate.set()

    class EmptyCache(FakeWa):
        """The new sidecar: its media cache died with the old one."""

        async def media(self, media_id):
            self.fetched.append(media_id)
            raise phone.MediaError("unknown_media")
    hub_b = _hub_in(tmp_path / "b", Recorder("done"))
    after, wa = _wa_inbound(hub_b, folder, EmptyCache())
    await after.resume()
    await _settle(after)
    assert wa.fetched == ["IMG"], "the voice note was fetched again"
    assert [t for t, _ in hub_b.brain.seen] == [
        f"{phone.VOICE_PREFIX}\ncall the bank"]
    gone = [t for t, q in wa.said if q == "I"]
    assert len(gone) == 1 and gone[0].startswith(
        "I restarted before I could read your image from "), wa.said


@pytest.mark.asyncio
async def test_telegram_resumes_its_queue_when_it_polls_again(tmp_path, bot):
    """AC-599 for Telegram: the same file, written by the same queue, is
    resumed by the reader before the first new update."""
    folder = tmp_path / "phone"
    queue = phone.PhoneQueue(folder, notify_telegram.KIND)
    flying = phone.Message(where=str(OWNER), id="51", text="first")
    await queue.add(flying)
    await queue.start(flying)
    for mid, text in (("52", "second"), ("53", "third")):
        await queue.add(phone.Message(where=str(OWNER), id=mid, text=text))
    arrived = _file(folder, "telegram")[0]["arrived"]

    hub = _simple_hub(tmp_path, Recorder("from telegram"))
    tg = await _open(hub)
    try:
        await _ready(tg, hub.channels.folder)
        await _until(lambda: len([q for _, q in _said(bot)
                                  if q in (52, 53)]) == 2, "both answers")
        assert [t for t, _ in hub.brain.seen] == ["second", "third"]
        assert (phone.RESTARTED_LINE.format(at=_hhmm(arrived)),
                None) in _said(bot)
        assert [q for t, q in _said(bot) if t == "from telegram"] == [52, 53]
        # The answered message leaves the file just AFTER its reply is sent;
        # a loaded machine showed that gap (2026-10-08), so wait for it.
        await _until(lambda: _file(folder, "telegram") == [],
                     "the answered messages to leave the queue file")
    finally:
        await _close(hub, tg)


def test_a_queue_file_that_cannot_be_read_is_kept_aside_and_said(
        tmp_path, capfd):
    folder = tmp_path / "phone"
    folder.mkdir()
    (folder / "whatsapp.json").write_text("{not json", encoding="utf-8")
    queue = phone.PhoneQueue(folder, "whatsapp")
    assert queue.recovered == []
    assert [p.name.startswith("whatsapp.json.bad-")
            for p in folder.iterdir()] == [True]
    assert "NOT resumed" in capfd.readouterr().err


# --------------------------------------------------------------------------- #
# RV-14: WhatsApp stopping is said once per stop (AC-601)
# --------------------------------------------------------------------------- #
class FakeChannels:
    """`notify.Channels` as `tell_owner` uses it: `get` and `send`."""

    def __init__(self, kind: str):
        self.kind = kind
        self.sent: list[tuple[str, str, str]] = []

    def get(self, name):
        return SimpleNamespace(kind=self.kind) if name == "phone" else None

    async def send(self, name, title, text):
        self.sent.append((name, title, text))
        return ports.NotifyResult(True, "")


def _told_hub(tmp_path, kind: str):
    hub = _hub(tmp_path, Recorder())
    hub.channels = FakeChannels(kind)
    hub.settings.set("notify_default", "phone")
    hub.announced = []

    async def announce(text, chat=""):
        hub.announced.append(text)
        return "said"
    hub.announce = announce
    return hub


def _line(state: str) -> str:
    return wa_mod.STOPPED_LINE.format(why=wa_mod.STOP_REASONS[state])


async def _watch(wa, script: list[dict]) -> None:
    """Run the link watch over `script` (one `/status` body per poll)."""
    polls = []

    async def call(method, path, body, **_kw):
        assert (method, path) == ("GET", "/status")
        polls.append(path)
        return 200, script[min(len(polls), len(script)) - 1]
    wa._call = call
    task = asyncio.get_running_loop().create_task(wa._watch_link())
    try:
        await _until(lambda: len(polls) > len(script) + 2, "the polls")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


PAIRED = {"status": "paired", "linked": True}
OUT = {"status": "logged_out", "linked": False}
REPLACED = {"status": "replaced", "linked": False}


@pytest.mark.asyncio
async def test_a_terminal_close_is_told_once_per_stop_and_never_on_whatsapp(
        tmp_path):
    hub = _told_hub(tmp_path, "telegram")
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa", link_poll_s=0.01)
    wa.inbound = wa_mod.Inbound(hub, wa, queue_folder=tmp_path / "phone")
    await _watch(wa, [PAIRED, OUT, OUT, OUT, PAIRED, REPLACED, REPLACED])
    await _until(lambda: len(hub.announced) == 2, "two desk lines")
    await asyncio.sleep(0.05)
    lines = [_line("logged_out"), _line("replaced")]
    assert [(n, t) for n, _title, t in hub.channels.sent] == [
        ("phone", lines[0]), ("phone", lines[1])], (
        "not once per stop: repeated polls of one stop were each told, or a "
        "second stop after relinking was not")
    assert hub.announced == lines

    # notify_default names a WhatsApp channel: the dead service itself.
    hub = _told_hub(tmp_path / "w", "whatsapp")
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "w" / "wa",
                         link_poll_s=0.01)
    wa.inbound = wa_mod.Inbound(hub, wa, queue_folder=tmp_path / "w" / "p")
    await _watch(wa, [OUT])
    await _until(lambda: hub.announced, "the desk line")
    assert hub.channels.sent == [] and hub.announced == [_line("logged_out")]


@pytest.mark.asyncio
async def test_a_sidecar_given_up_is_told_once(tmp_path):
    hub = _told_hub(tmp_path, "telegram")
    hub.settings.set(wa_mod.ENABLE_KEY, True)
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa", backoff=(0.01,),
                         max_restarts=2)
    wa.inbound = wa_mod.Inbound(hub, wa, queue_folder=tmp_path / "phone")

    def spawn():
        raise wa_mod.SidecarError("node is gone")
    wa._spawn = spawn
    try:
        await wa._enable()
        await _until(lambda: wa.state == "failed", "given up")
        await _until(lambda: hub.announced, "the desk line")
        await asyncio.sleep(0.05)
        assert len(hub.channels.sent) == 1 and len(hub.announced) == 1
        assert "stopped 3 times in a row" in hub.announced[0]
    finally:
        await wa.stop()


@pytest.mark.asyncio
async def test_linked_again_resumes_the_owners_messages(tmp_path):
    """RV-13's WhatsApp trigger: the link watch resumes the queue once the
    account is linked, without waiting for a new message."""
    folder = tmp_path / "phone"
    queue = phone.PhoneQueue(folder, "whatsapp")
    await queue.add(phone.Message(where=OWN, id="W1", text="still waiting"))
    hub = _hub(tmp_path, Recorder("now answered"))
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa", link_poll_s=0.01)
    fake = FakeWa()
    wa.inbound, _ = _wa_inbound(hub, folder, fake)
    await _watch(wa, [PAIRED])
    await _settle(wa.inbound)
    assert fake.said == [("now answered", "W1")]


# --------------------------------------------------------------------------- #
# RV-15: a voice note can answer a question (AC-602)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_spoken_sim_answers_the_question_while_its_turn_waits(
        tmp_path, monkeypatch, no_ffmpeg):
    gate = asyncio.Event()
    hub = _hub(tmp_path, Recorder("done", gate=gate))
    inbound, wa = _inbound(hub)
    heard = []

    async def transcribe(pcm):
        heard.append(pcm)
        return "Sim."
    monkeypatch.setattr(hub, "transcribe_clip", transcribe)
    offered = []

    async def hook(incoming):
        offered.append(incoming)
        if notify.answer_for("confirm", (), incoming.text) == ports.Answer(
                confirmed=True):
            return "Answered: yes"
        return None
    wa.questions = hook
    wa.files["V1"] = b"ogg"
    wa.files["IMG"] = PNG
    try:
        await inbound.owner(_msg("M1", "delete the old branch"))
        await _until(lambda: len(hub.brain.seen) == 1, "the asking turn")
        await inbound.owner(_msg("A1", "", media=_voice_media("V1")))
        assert offered[-1] == ports.Incoming("Sim.", ""), offered
        assert wa.said == [("Answered: yes", "A1")], (
            "the spoken answer was not taken while the turn that asked waits")
        # Any other file is never an answer, even captioned "1".
        await inbound.owner(_msg("I1", "1", media={
            "kind": "image", "id": "IMG", "bytes": 3, "tooLarge": False}))
        assert len(offered) == 2, "an image was offered as an answer"
    finally:
        gate.set()
    await _settle(inbound)
    assert [t for t, _ in hub.brain.seen][0] == "delete the old branch"
    assert len(hub.brain.seen) == 2 and len(heard) == 1


# --------------------------------------------------------------------------- #
# RV-16: recordings over 10 minutes are refused (AC-603)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_recording_over_ten_minutes_is_refused_before_transcription(
        tmp_path, monkeypatch, no_ffmpeg):
    assert phone.MAX_AUDIO_S == 600
    hub = _hub(tmp_path, Recorder())
    inbound, wa = _inbound(hub)
    heard = []

    async def transcribe(pcm):
        heard.append(pcm)
        return "words"
    monkeypatch.setattr(hub, "transcribe_clip", transcribe)
    wa.files["LONG"] = b"ogg"
    await inbound.owner(_msg("L1", "", media={**_voice_media("LONG"),
                                              "seconds": 601}))
    assert wa.said == [(phone.TOO_LONG.format(minutes=11, limit=10), "L1")]
    assert wa.fetched == [] and heard == [] and hub.brain.seen == []

    # Declared nothing: the decoded sound is measured before transcription.
    monkeypatch.setattr(phone, "MAX_AUDIO_S", 60)
    monkeypatch.setattr(phone, "decode_audio",
                        lambda data: b"\x01\x00" * 16000 * 61)
    await inbound.owner(_msg("L2", "", media=_voice_media("LONG")))
    assert wa.said[-1] == (phone.TOO_LONG.format(minutes=2, limit=1), "L2")
    assert heard == []


def test_both_chat_apps_carry_the_declared_length():
    item = _msg("V", "", media={**_voice_media("V1"), "seconds": 42})
    assert wa_mod.owner_message(item).media.seconds == 42.0
    assert wa_mod.owner_message(_msg("V", "", media=_voice_media(
        "V1"))).media.seconds is None
    media = notify_telegram.Inbound._media("audio", {"file_id": "F",
                                                     "duration": 700})
    assert media.seconds == 700.0


# --------------------------------------------------------------------------- #
# RV-17: /new waits its turn (AC-604)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_new_is_answered_in_its_place_and_moves_only_what_follows(
        tmp_path, monkeypatch):
    hub = _phone_hub(tmp_path, monkeypatch)
    inbound, wa = _inbound(hub)
    await inbound.owner(_msg("M1", "in the old chat"))
    await inbound.owner(_msg("M2", "/new"))
    await inbound.owner(_msg("M3", "in the new chat"))
    await _settle(inbound)
    assert [q for _, q in wa.said] == ["M1", "M2", "M3"], (
        "/new was answered out of order")
    assert wa.said[1][0] == hub.NEW_CONVERSATION
    chats = await _chats(hub)
    assert len(chats) == 2
    lines = sorted([await _user_lines(hub, c) for c in chats])
    assert lines == [["in the new chat"], ["in the old chat"]]


# --------------------------------------------------------------------------- #
# RV-9 on the daemon side: what Butler cannot read is answered
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unsupported_whatsapp_item_is_answered_and_an_edit_is_a_message(
        tmp_path):
    hub = _hub(tmp_path, Recorder("noted"))
    inbound, wa = _inbound(hub)
    await inbound.owner({**_msg("S1", ""), "unsupported": "sticker"})
    assert wa.said == [(phone.UNSUPPORTED, "S1")]
    await inbound.owner({**_msg("E1", "(edited) at five"), "edited": "X1"})
    await _settle(inbound)
    assert [t for t, _ in hub.brain.seen] == ["(edited) at five"]
    assert notify_telegram.UNSUPPORTED == phone.UNSUPPORTED


# --------------------------------------------------------------------------- #
# RV-18: one reply path, no dead branch (AC-605), by source
# --------------------------------------------------------------------------- #
def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text("utf-8"))


def _functions(tree) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_there_is_one_reply_path_for_owner_messages():
    phone_tree = _tree("daemon/phone.py")
    # Every text reply in the shared machinery is in `say`; the only other
    # `channel.reply` is the voice note and a shown file (rich-chat O-1).
    owners = []
    for name, fn in _functions(phone_tree).items():
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "reply"
                    and ast.unparse(node.func.value) == "self.channel"):
                owners.append((name, sorted(k.arg for k in node.keywords)))
    assert owners == [("_send_shown", ["caption", "file", "quote"]),
                      ("_voice_reply", ["quote", "voice"]),
                      ("say", ["quote", "text"])], owners
    # No chat app keeps its own copy of it, or its own answer offer.
    for rel in ("daemon/whatsapp.py", "daemon/adapters/notify_telegram.py"):
        names = _functions(_tree(rel))
        assert "_reply_logged" not in names and "_say" not in names, rel
        calls = [n for n in ast.walk(_tree(rel)) if isinstance(n, ast.Call)
                 and ast.unparse(n.func).endswith("offer_answer")]
        assert calls == [], f"{rel} offers answers itself"


def test_the_turn_wait_has_no_dead_branch():
    turn = _functions(_tree("daemon/phone.py"))["_turn"]
    assert not any(isinstance(n, ast.Continue) for n in ast.walk(turn))


# --------------------------------------------------------------------------- #
# The sidecar's wave-1 contracts on the daemon side (RV-5, RV-8, RV-11, RV-12)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_forbidden_is_a_page_state_and_a_stop(tmp_path):
    """RV-5: 403 is terminal. The page gets its actions (an unknown state
    would be a SidecarError there), and RV-14 tells the owner once."""
    doc = wa_mod.page_doc({"state": {"status": "forbidden"},
                           "view": {"headline": "Refused by WhatsApp"}})
    assert [a["id"] for a in doc["actions"]] == ["link", "unlink"]
    assert doc["poll"] is False
    hub = _told_hub(tmp_path, "telegram")
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa", link_poll_s=0.01)
    wa.inbound = wa_mod.Inbound(hub, wa, queue_folder=tmp_path / "phone")
    await _watch(wa, [{"status": "forbidden", "linked": False}])
    await _until(lambda: hub.announced, "the desk line")
    assert hub.announced == [_line("forbidden")]


@pytest.mark.asyncio
async def test_an_album_is_one_turn_with_every_file(tmp_path):
    """RV-8 (AC-594): `album` lists every file; ONE turn carries them all."""
    hub = _hub(tmp_path, Recorder("two photos"))
    inbound, wa = _inbound(hub)
    wa.files["P1"] = PNG
    wa.files["P2"] = PNG
    files = [{"kind": "image", "id": f"P{i}", "bytes": len(PNG),
              "tooLarge": False} for i in (1, 2)]
    await inbound.owner({**_msg("A1", "which is better", media=files[0]),
                         "album": files, "albumId": "PARENT"})
    await _settle(inbound)
    assert wa.fetched == ["P1", "P2"]
    [(text, ctx)] = hub.brain.seen
    assert text == "which is better" and len(ctx.attachments) == 2
    assert wa.said == [("two photos", "A1")]


@pytest.mark.asyncio
async def test_a_rate_limited_reply_is_not_delivered_and_logged_by_id(
        tmp_path, capfd):
    """RV-12: `/reply` may answer 429; that reply is NOT delivered, said in
    the log by message id, never by content."""
    hub = _hub(tmp_path, Recorder())
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa")
    wa.state, wa.port = "running", 1

    async def call(method, path, body, **_kw):
        return 429, {"reason": "rate_limited",
                     "error": "more than 30 replies in a minute"}
    wa._call = call
    inbound = wa_mod.Inbound(hub, wa, queue_folder=tmp_path / "phone")
    await inbound.say(OWN, "private words", "R1")
    err = capfd.readouterr().err
    assert ("[whatsapp] reply to R1 was NOT delivered: more than 30 replies "
            "in a minute") in err
    assert "private words" not in err


@pytest.mark.asyncio
async def test_items_dropped_while_nobody_read_are_said_once(tmp_path):
    """RV-11: `ready` carries `dropped`; more than none is one line in the
    own chat, none is nothing."""
    class Linked(FakeWa):
        async def own_jid(self):
            return OWN
    hub = _hub(tmp_path, Recorder())
    fake = Linked()
    wa = wa_mod.WhatsApp(hub.settings, tmp_path / "wa")
    wa.inbound = wa_mod.Inbound(hub, fake, queue_folder=tmp_path / "phone")
    await wa._dispatch({"type": "ready", "dropped": 0})
    await wa._dispatch({"type": "ready", "dropped": 3})
    await _until(lambda: fake.said, "the line")
    await asyncio.sleep(0.05)
    assert fake.said == [(wa_mod.DROPPED_LINE.format(n=3, noun="messages"),
                          None)]
