"""The WhatsApp send door, files out, and the notification channel
(docs/messaging.md MS-10..MS-13, MS-18b OUT; phase M5).

AC-511 (loopback door: 403 from the PROXIED listener even from 127.0.0.1,
and from a peer that is not loopback), AC-513 (the send switch is the
SETTING, read at each send, and a caller cannot override it), the status
passthrough of AC-512/AC-515, AC-516 (the `whatsapp` channel) and AC-562
(a tool's file must lie inside the conversation's project or the data
root).

Against the FAKE sidecar (`test/assets/fake_wa_sidecar.py`), which records
every request it receives in the records folder: every refusal here is
proved by its CONSEQUENCE, no `/send` reaching the sidecar, never by status
alone (test/AGENTS.md). Nothing here touches WhatsApp.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from assets import wa_archive                                 # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon import notify as notify_mod                       # noqa: E402
from daemon import whatsapp as wa                             # noqa: E402
from test_listeners import _Three, _hub, _lan_address          # noqa: E402
from test_whatsapp_owner import FAKE, _requests, _until        # noqa: E402

SEND = "/api/whatsapp/send"
NOW = int(time.time())


@pytest.fixture
def fake_argv(monkeypatch):
    monkeypatch.setattr(wa, "sidecar_argv",
                        lambda: [sys.executable, "-X", "utf8", str(FAKE)])
    monkeypatch.setenv("FAKE_WA_LINKED", "1")


def _sends(records: Path) -> list[dict]:
    return [q for q in _requests(records) if q["path"] == "/send"]


class _Door:
    """A hub with WhatsApp ON over the fake sidecar, served on three real
    listeners (`test_listeners._Three`), records under tmp_path."""

    def __init__(self, tmp_path, monkeypatch, main_host="127.0.0.1"):
        self.hub = _hub(tmp_path, monkeypatch)
        self.hub.settings.set("whatsapp_enabled", True)
        self.three = _Three(self.hub, monkeypatch, main_host=main_host)
        self.records = tmp_path / "whatsapp"

    async def __aenter__(self):
        await self.three.__aenter__()
        await _until(lambda: self.hub.whatsapp.state == "running",
                     what="the fake sidecar")
        return self

    async def __aexit__(self, *exc):
        await self.three.__aexit__(*exc)

    async def post(self, body=None, *, kind=hub_mod.LOCAL, data=None):
        kwargs = {"data": data} if data is not None else {"json": body}
        async with self.three.http.post(self.three.url(kind, SEND),
                                        headers=await self.three.who(kind),
                                        **kwargs) as r:
            return r.status, await r.json()


@pytest.fixture
def door(tmp_path, monkeypatch, fake_argv):
    return _Door(tmp_path, monkeypatch)


# --------------------------------------------------------------------------- #
# AC-511: the door answers this machine only
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_door_refuses_the_proxied_listener_and_sends_nothing(door):
    """AC-511 + AC-541: `tailscale serve` delivers every phone from
    127.0.0.1 on PROXIED. Refused, and the sidecar never saw a /send."""
    async with door:
        status, body = await door.post(
            {"recipient": "me", "text": "hi"}, kind=hub_mod.PROXIED)
        assert status == 403 and body["reason"] == "not_local"
        assert _sends(door.records) == []
        status, body = await door.post({"recipient": "me", "text": "hi"})
        assert status == 200, body
        assert len(_sends(door.records)) == 1


@pytest.mark.asyncio
async def test_the_door_refuses_a_peer_that_is_not_loopback(
        tmp_path, monkeypatch, fake_argv):
    ip = _lan_address()
    if ip is None:
        pytest.skip("this machine has no non-loopback IPv4 address")
    async with _Door(tmp_path, monkeypatch, main_host=ip) as d:
        status, _ = await d.post({"recipient": "me", "text": "hi"},
                                 kind=hub_mod.MAIN)
        assert status == 403
        async with d.three.http.get(d.three.url(
                hub_mod.MAIN, "/api/whatsapp/status"),
                headers=await d.three.who(hub_mod.MAIN)) as r:
            assert r.status == 403
        assert _sends(d.records) == []


# --------------------------------------------------------------------------- #
# Forwarding: the switch is the setting's, statuses pass through
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_send_switch_is_read_at_each_send_and_never_the_callers(
        door):
    """AC-513: on, then off without a restart; a caller's own
    `send_enabled` is dropped."""
    async with door:
        status, _ = await door.post({"recipient": "me", "text": "one",
                                     "send_enabled": False})
        assert status == 200
        door.hub.settings.set("whatsapp_send", False)
        status, body = await door.post({"recipient": "me", "text": "two",
                                        "send_enabled": True})
        assert status == 403 and body["reason"] == "send_disabled"
        assert "switched off" in body["text"]
        sent = [q["body"]["send_enabled"] for q in _sends(door.records)]
        assert sent == [True, False]


@pytest.mark.asyncio
async def test_me_is_the_linked_accounts_own_bare_jid(door):
    async with door:
        status, body = await door.post({"recipient": "me", "text": "hi"})
        assert status == 200 and body["text"] == "Sent to Somebody."
        (q,) = _sends(door.records)
        assert q["body"]["recipient"] == wa_archive.OWN
        assert q["token_ok"] is True


@pytest.mark.asyncio
async def test_me_while_not_linked_is_503_and_sends_nothing(
        door, monkeypatch):
    monkeypatch.setenv("FAKE_WA_LINKED", "0")
    async with door:
        status, body = await door.post({"recipient": "me", "text": "hi"})
        assert status == 503 and body["reason"] == "not_linked"
        assert _sends(door.records) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("recipient,status,reason", [
    ("ambiguous", 409, "ambiguous_recipient"),
    ("Bruno", 409, "confirmation_required"),
    ("stranger", 403, "unknown_recipient"),
    ("offline", 503, None),
    ("broken", 502, "send_failed"),
])
async def test_the_sidecars_answer_passes_through_unchanged(
        door, recipient, status, reason):
    """MS-10: 409 confirm/ambiguous, 403 refusals, 503 not linked, 502 the
    socket; the body keeps every field and gains the model's `text`."""
    async with door:
        got, body = await door.post({"recipient": recipient, "text": "x"})
        assert got == status
        assert body.get("reason") == reason
        assert body["text"].startswith("NOT SENT")
        if reason == "ambiguous_recipient":
            assert [c["jid"] for c in body["candidates"]] == [
                "1@s.whatsapp.net", "2@s.whatsapp.net"]
        if reason == "confirmation_required":
            assert body["jid"] == "Bruno" and "confirm" in body["text"]


@pytest.mark.asyncio
async def test_confirm_counts_only_as_json_true(door):
    async with door:
        status, _ = await door.post({"recipient": "Bruno", "text": "x",
                                     "confirm": "true"})
        assert status == 409
        status, _ = await door.post({"recipient": "Bruno", "text": "x",
                                     "confirm": True})
        assert status == 200


# --------------------------------------------------------------------------- #
# Files through the door (AC-515)
# --------------------------------------------------------------------------- #
def _form(fields: dict, files=()) -> aiohttp.FormData:
    form = aiohttp.FormData()
    for k, v in fields.items():
        form.add_field(k, v)
    for name, data in files:
        form.add_field("files", data, filename=name,
                       content_type="application/octet-stream")
    return form


@pytest.mark.asyncio
async def test_one_file_is_forwarded_with_its_fields(door):
    async with door:
        status, body = await door.post(data=_form(
            {"recipient": "Bruno", "confirm": "true", "caption": "look"},
            [("photo.png", b"\x89PNG" + b"x" * 100)]))
        assert status == 200, body
        (q,) = _sends(door.records)
        assert q["body"]["fields"] == {"recipient": "Bruno",
                                       "confirm": "true", "caption": "look",
                                       "send_enabled": "true"}
        assert [(f["field"], f["name"], f["size"])
                for f in q["body"]["files"]] == [("files", "photo.png", 104)]


@pytest.mark.asyncio
async def test_two_files_or_too_many_bytes_never_reach_the_sidecar(
        door, monkeypatch):
    monkeypatch.setattr(wa, "MAX_FILE_BYTES", 1000)
    async with door:
        status, body = await door.post(data=_form(
            {"recipient": "me"}, [("a.txt", b"a"), ("b.txt", b"b")]))
        assert status == 400 and "one file" in body["error"]
        status, body = await door.post(data=_form(
            {"recipient": "me"}, [("big.bin", b"x" * 1001)]))
        assert status == 413
        assert _sends(door.records) == []


# --------------------------------------------------------------------------- #
# AC-562: a tool's file must be inside the project or the data root
# --------------------------------------------------------------------------- #
@pytest.fixture
def data_root(tmp_path_factory, monkeypatch):
    # A SIBLING of tmp_path, never inside it: `_hub` makes tmp_path the
    # records folder (BUTLER_STATE), and no file inside the records may be
    # sent (RV-2, AC-587).
    root = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("BUTLER_DATA", str(root))
    return root


@pytest.mark.asyncio
async def test_a_tools_file_outside_the_roots_is_refused_naming_the_rule(
        door, data_root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    inside = data_root / "report.txt"
    inside.write_text("hello", encoding="utf-8")
    async with door:
        status, body = await door.post(data=_form(
            {"recipient": "me", "source": str(outside)},
            [("secret.txt", b"private")]))
        assert status == 403 and body["reason"] == "file_outside_roots"
        assert wa.FILE_RULE in body["error"]
        assert _sends(door.records) == []
        status, body = await door.post(data=_form(
            {"recipient": "me", "source": str(inside)},
            [("report.txt", b"hello")]))
        assert status == 200, body
        (q,) = _sends(door.records)
        assert "source" not in q["body"]["fields"]
        assert "chat" not in q["body"]["fields"]


@pytest.mark.asyncio
async def test_a_tools_file_may_come_from_its_conversations_project(
        door, data_root, tmp_path_factory):
    project = tmp_path_factory.mktemp("proj")
    (project / "out.pdf").write_bytes(b"%PDF-1.4")

    class _Sessions:
        def session_chat(self, chat, limit=None):
            return {"id": chat, "project": str(project)} \
                if chat == "chat-1" else None
    door.hub.session_brain = lambda: _Sessions()
    async with door:
        fields = {"recipient": "me", "source": str(project / "out.pdf")}
        status, body = await door.post(data=_form(
            {**fields, "chat": "chat-1"}, [("out.pdf", b"%PDF-1.4")]))
        assert status == 200, body
        status, body = await door.post(data=_form(
            fields, [("out.pdf", b"%PDF-1.4")]))       # no chat: no project
        assert status == 403
        status, body = await door.post(data=_form(
            {**fields, "chat": "chat-nobody"}, [("out.pdf", b"%PDF-1.4")]))
        assert status == 404
        assert len(_sends(door.records)) == 1


def _junction(link: Path, target: Path):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


@pytest.mark.asyncio
async def test_the_in_process_tool_applies_the_rule_before_reading(
        door, data_root, tmp_path):
    """AC-562 on the endpoint brain's path. A JUNCTION inside the data root
    that leads out of it is outside: the rule compares REAL paths."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keys.txt").write_text("k", encoding="utf-8")
    _junction(data_root / "door", elsewhere)
    (data_root / "ok.txt").write_text("fine", encoding="utf-8")
    agent = wa.AgentTools(door.hub.settings, data_root / "none.db", data_root,
                          forbidden=(data_root / ".butler",), ask=None)
    try:
        async with door:
            said = await agent.call("whatsapp_send", {
                "recipient": "me", "file": "door/keys.txt"}, "c", 1)
            assert said.startswith("NOT SENT.") and wa.FILE_RULE in said
            said = await agent.call("whatsapp_send", {
                "recipient": "me", "file": str(tmp_path / "elsewhere" /
                                               "keys.txt")}, "c", 1)
            assert wa.FILE_RULE in said
            assert _sends(door.records) == []
            said = await agent.call("whatsapp_send", {
                "recipient": "me", "file": "ok.txt", "caption": "here"},
                "c", 1)
            assert said == "Sent to Somebody.", said
            (q,) = _sends(door.records)
            assert q["body"]["files"][0]["name"] == "ok.txt"
            assert "confirm" not in q["body"]["fields"]       # AC-588
    finally:
        os.rmdir(data_root / "door")          # the junction, not its target


@pytest.mark.asyncio
async def test_the_in_process_send_tool_is_gated_by_both_switches(
        tmp_path, monkeypatch):
    from daemon.settings import Settings
    prefs = Settings(tmp_path / "s.json")
    agent = wa.AgentTools(prefs, tmp_path / "none.db", tmp_path,
                          forbidden=(tmp_path / ".butler",), ask=None)
    assert (await agent.call("whatsapp_send", {"recipient": "me",
                                               "text": "x"}, "c", 1)
            ).startswith("NOT SENT. WhatsApp sending is off")
    assert wa.SEND_NAMES[0] not in [t["function"]["name"]
                                    for t in agent.offered()]


# --------------------------------------------------------------------------- #
# AC-516: the `whatsapp` notification channel
# --------------------------------------------------------------------------- #
def test_the_channel_kind_is_offered_from_its_fields():
    kinds = {k["kind"]: k for k in notify_mod.Channels.kinds()}
    assert [f["key"] for f in kinds["whatsapp"]["fields"]] == ["to"]
    assert kinds["whatsapp"]["fields"][0]["secret"] is True


@pytest.fixture
def channels(tmp_path, monkeypatch):
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    wa_archive.build(tmp_path / "whatsapp" / "whatsapp.db", NOW)
    return notify_mod.Channels(tmp_path / "notify")


def _saved(channels, name) -> dict:
    return json.loads((channels.folder / f"{name}.json").read_text("utf-8"))


def test_a_name_is_resolved_once_and_stored_as_its_chat_id(channels):
    channels.save({"name": "bruno", "kind": "whatsapp", "to": "bruno"})
    assert _saved(channels, "bruno")["to"] == wa_archive.BRUNO
    channels.save({"name": "fam", "kind": "whatsapp", "to": "Family Group"})
    assert _saved(channels, "fam")["to"] == wa_archive.FAMILY
    channels.save({"name": "mine", "kind": "whatsapp"})
    assert _saved(channels, "mine")["to"] == "me"
    assert all(c.public("")["configured"] for c in channels.all())


def test_an_ambiguous_or_unknown_name_is_refused_with_candidates(channels):
    with pytest.raises(notify_mod.ChannelRefused) as e:
        channels.save({"name": "ana", "kind": "whatsapp", "to": "ana"})
    assert wa_archive.ANA_SILVA in str(e.value)
    assert wa_archive.ANABELA in str(e.value)
    with pytest.raises(notify_mod.ChannelRefused, match="saved contact"):
        channels.save({"name": "x", "kind": "whatsapp",
                       "to": "+1 555 123 4567"})
    assert not (channels.folder / "ana.json").exists()
    assert not (channels.folder / "x.json").exists()


@pytest.mark.asyncio
async def test_a_channel_sends_through_the_door_and_never_quotes_the_id(
        door, tmp_path):
    channels = notify_mod.Channels(tmp_path / "notify")
    wa_archive.build(tmp_path / "whatsapp" / "whatsapp.db", NOW)
    channels.save({"name": "bruno", "kind": "whatsapp", "to": "Bruno"})
    channels.save({"name": "mine", "kind": "whatsapp", "to": "me"})
    async with door:
        result = await channels.send("mine", "Run done", "All good.")
        assert result.ok, result.detail
        result = await channels.send("bruno", "Run done", "All good.")
        assert result.ok, result.detail
        to = [q["body"]["recipient"] for q in _sends(door.records)]
        assert to == [wa_archive.OWN, wa_archive.BRUNO]
        assert all(q["body"]["confirm"] is True for q in _sends(
            door.records)), "saving the channel is the confirmation"
        assert _sends(door.records)[0]["body"]["text"] == \
            "Run done\n\nAll good."
        door.hub.settings.set("whatsapp_send", False)
        result = await channels.send("bruno", "t", "x")
        assert not result.ok and "send_disabled" in result.detail
        assert wa_archive.BRUNO not in result.detail


@pytest.mark.asyncio
async def test_a_channel_with_whatsapp_off_fails_and_says_why(tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(wa, "_door", None)
    channels = notify_mod.Channels(tmp_path / "notify")
    channels.save({"name": "mine", "kind": "whatsapp", "to": "me"})
    result = await channels.send("mine", "t", "x")
    assert not result.ok and "not running" in result.detail
