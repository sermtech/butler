"""The daemon holds the sidecar's /events stream (docs/messaging.md MS-3, M6).

Against the FAKE sidecar process (`test/assets/fake_wa_sidecar.py`), which
serves a real NDJSON stream a test pushes items into. What is pinned:

* owner items reach the router IN ORDER, a guest item never reaches it
  (logged WITHOUT its content; the guest path is `test_whatsapp_guests.py`),
  and a `missed` item is counted (AC-524);
* the stream comes back when the sidecar restarts after a crash;
* end to end, an owner message becomes a turn and the reply reaches the
  sidecar's `/reply` quoted to it, with typing shown (AC-520), and an image
  is fetched from `/media/<id>` and attached (AC-560).

Nothing here touches WhatsApp.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_attachments import PNG                              # noqa: E402
from test_whatsapp_inbound import Recorder, _hub               # noqa: E402
from daemon import phone                                      # noqa: E402
from daemon import whatsapp as whatsapp_mod                    # noqa: E402
from daemon.settings import Settings                           # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "test" / "assets" / "fake_wa_sidecar.py"
OWN = "15559990000@s.whatsapp.net"


@pytest.fixture
def fake_argv(monkeypatch):
    monkeypatch.setattr(whatsapp_mod, "sidecar_argv",
                        lambda: [sys.executable, "-X", "utf8", str(FAKE)])


async def _until(cond, what, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


async def _push(wa, item, streams=1, timeout=15.0) -> None:
    """Hand `item` to the fake's open /events streams, once one is open."""
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as http:
        while time.monotonic() < deadline:
            if wa.port is not None:
                async with http.get(f"http://127.0.0.1:{wa.port}/_streams",
                                    headers=wa._headers()) as r:
                    open_streams = (await r.json())["streams"]
                if open_streams >= streams:
                    async with http.post(f"http://127.0.0.1:{wa.port}/_push",
                                         json=item,
                                         headers=wa._headers()) as r:
                        assert (await r.json())["streams"] >= streams
                    return
            await asyncio.sleep(0.05)
    raise AssertionError("the daemon never opened the /events stream")


def _requests(records: Path, path: str) -> list[dict]:
    f = records / "requests.jsonl"
    if not f.exists():
        return []
    rows = [json.loads(line) for line in f.read_text("utf-8").splitlines()]
    return [r for r in rows if r["path"].split("?")[0] == path]


class StubInbound:
    def __init__(self):
        self.owned: list[dict] = []
        self.missed_items: list[dict] = []

    async def owner(self, item):
        self.owned.append(item)

    def missed(self, item):
        self.missed_items.append(item)


def _owner(mid, text="hello", **extra):
    return {"type": "message", "tag": "owner", "id": mid, "jid": OWN,
            "text": text, **extra}


@pytest.mark.asyncio
async def test_items_reach_the_router_in_order_and_guests_are_not_routed(
        tmp_path, fake_argv, capfd):
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, tmp_path / "whatsapp")
    stub = wa.inbound = StubInbound()
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", "running")
        await _push(wa, _owner("A1", "first"))
        await _push(wa, {"type": "message", "tag": "guest", "id": "G1",
                         "jid": "1555@s.whatsapp.net",
                         "text": "a private guest secret"})
        await _push(wa, {"type": "takeover", "jid": "1555@s.whatsapp.net"})
        await _push(wa, {"type": "missed", "jid": OWN, "id": "D1"})
        await _push(wa, _owner("A2", "second"))
        await _until(lambda: len(stub.owned) == 2, "two owner items")
        assert [i["id"] for i in stub.owned] == ["A1", "A2"]
        assert [i["id"] for i in stub.missed_items] == ["D1"]
        err = capfd.readouterr().err
        # This stub has no guest path; the real one is test_whatsapp_guests.
        assert "nothing answers guests here" in err
        assert "a private guest secret" not in err, "guest content logged"
    finally:
        await wa.stop()


@pytest.mark.asyncio
async def test_the_stream_comes_back_when_the_sidecar_restarts(
        tmp_path, fake_argv):
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("whatsapp_enabled", True)
    wa = whatsapp_mod.WhatsApp(prefs, tmp_path / "whatsapp", backoff=(0.05,))
    stub = wa.inbound = StubInbound()
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", "running")
        await _push(wa, _owner("B1"))
        await _until(lambda: len(stub.owned) == 1, "the first item")
        first = wa.pid()
        subprocess.run(["taskkill", "/F", "/PID", str(first)],
                       capture_output=True)
        await _until(lambda: wa.state == "running"
                     and wa.pid() not in (None, first), "a restarted sidecar")
        await _push(wa, _owner("B2"))
        await _until(lambda: len(stub.owned) == 2, "an item after restart")
        assert [i["id"] for i in stub.owned] == ["B1", "B2"]
        assert all(r["token_ok"] for r in _requests(tmp_path / "whatsapp",
                                                     "/events"))
    finally:
        await wa.stop()


@pytest.mark.asyncio
async def test_end_to_end_a_message_is_answered_quoted_through_the_sidecar(
        tmp_path, fake_argv):
    hub = _hub(tmp_path, Recorder("Here is the answer."))
    hub.settings.set("whatsapp_enabled", True)
    records = tmp_path / "whatsapp"
    (records / "media").mkdir(parents=True)
    (records / "media" / "IMG1").write_bytes(PNG)
    wa = whatsapp_mod.WhatsApp(hub.settings, records)
    wa.inbound = whatsapp_mod.Inbound(hub, wa, busy_poll_s=0.01,
                                      queue_folder=tmp_path / "phone")
    await wa.start()
    try:
        await _until(lambda: wa.state == "running", "running")
        await _push(wa, _owner("E1", "what time is it"))
        await _push(wa, _owner("E2", "", media={
            "kind": "image", "id": "IMG1", "tooLarge": False}))
        await _until(lambda: len(_requests(records, "/reply")) == 2,
                     "two replies at the sidecar")
        replies = [r["body"] for r in _requests(records, "/reply")]
        assert replies == [
            {"jid": OWN, "text": "Here is the answer.", "quote": "E1"},
            {"jid": OWN, "text": "Here is the answer.", "quote": "E2"}]
        states = [r["body"]["state"] for r in _requests(records, "/presence")]
        assert states[0] == "composing" and states[-1] == "paused"
        (text, _), (question, ctx) = hub.brain.seen
        assert text == "what time is it"
        assert question == phone.IMAGE_QUESTION
        assert len(ctx.attachments) == 1
        assert Path(ctx.attachments[0].path).read_bytes() == PNG
        assert all(r["token_ok"] for r in _requests(records, "/media/IMG1"))
    finally:
        await wa.stop()
