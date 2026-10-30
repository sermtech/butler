"""Notification channels: docs/automations.md section 7, phase 7a.

AC-471 (a token never leaves its channel file), AC-472 (delivery: off the
loop, 15 s timeout, plain text, at most 600 characters, a failure with a
reason and never a reroute) and AC-473 (channels are FILES, kinds are a
closed table, a malformed file is listed with its problem, a dropped-in file
works with no restart).

NOTHING HERE REACHES api.telegram.org. Every send goes to `FakeBotApi`, a
stdlib HTTP server on 127.0.0.1 that records what it was sent, and the
autouse fixture points the adapter's module-level API base at a dead local
port first -- so a test that forgot to name the fake fails with a network
error instead of talking to Telegram.
"""
import asyncio
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import butlerd                          # noqa: E402
from daemon import notify                           # noqa: E402
from daemon import ports                            # noqa: E402
from daemon.adapters import notify_telegram         # noqa: E402

TOKEN = "987654321:AAHsecretSECRETsecretSECRETsecret_zz-9"
CHAT = "555000111"
OTHER_TOKEN = "123456789:BBHanotherANOTHERanotherANOTHER_yy-8"
OTHER_CHAT = "777000222"


# --------------------------------------------------------------------------- #
# A local fake of the Bot API methods the adapter calls
# --------------------------------------------------------------------------- #
def _multipart(ctype: str, raw: bytes) -> dict:
    """A multipart/form-data body as {name: str} for text parts and
    {name: {"filename", "content_type", "bytes"}} for files."""
    from email.parser import BytesParser
    from email.policy import HTTP
    msg = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {ctype}\r\n\r\n".encode() + raw)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        if part.get_filename():
            out[name] = {"filename": part.get_filename(),
                         "content_type": part.get_content_type(),
                         "bytes": data}
        else:
            out[name] = data.decode("utf-8")
    return out


class FakeBotApi:
    """Records every request; answers as `mode` says.

    ok       200 {"ok": true, "result": ...}
    502      502 with an HTML body (what a proxy in front of Telegram sends)
    401      401 {"ok": false, "description": "Unauthorized"}
    echo     400 whose description ECHOES the token -- the case where only
             the registry's scrub stands between the token and the page
    slow     sleeps `delay` seconds, then 200

    `mode` applies to every method but `getUpdates`, which follows
    `updates_mode` ("ok", or "409": another program is reading this bot).

    THE INBOUND HALF (docs/messaging.md section 8). `push(update)` queues an
    update with the next `update_id`; `getUpdates` keeps Telegram's offset
    rules -- an `offset` confirms (drops) every update below it, a negative
    one keeps only the last updates -- and LONG-POLLS: it answers as soon as
    an update is queued, else after `timeout` seconds capped at `hold_s`.
    `sendMessage` and `sendVoice` answer a `message_id` (1, 2, ... in
    request order); `getFile` names `files/<file_id>`, and a GET of
    `/file/bot<token>/files/<file_id>` serves `files[file_id]`.
    """

    def __init__(self):
        self.requests: list[dict] = []
        self.mode = "ok"
        self.updates_mode = "ok"
        self.delay = 2.0
        self.hold_s = 0.3
        self.updates: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self._next_update = 1000
        self._next_message = 0
        self._cond = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):                  # quiet
                pass

            def do_GET(self):
                name = self.path.rsplit("/", 1)[-1]
                fake.downloads.append(self.path)
                if "/file/bot" in self.path and name in fake.files:
                    self._send(200, fake.files[name],
                               "application/octet-stream")
                else:
                    self._send(404, b"Not Found", "text/plain")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                ctype = self.headers.get("Content-Type") or ""
                body = (_multipart(ctype, raw)
                        if ctype.startswith("multipart/form-data")
                        else json.loads(raw.decode("utf-8")))
                method = self.path.rsplit("/", 1)[-1]
                rec = {"path": self.path, "method": method,
                       "content_type": ctype, "body": body}
                fake.requests.append(rec)
                if method == "getUpdates":
                    return self._updates(body)
                if fake.mode == "slow":
                    time.sleep(fake.delay)
                if fake.mode == "502":
                    self._send(502, b"<html>Bad Gateway</html>", "text/html")
                elif fake.mode == "401":
                    self._send(401, json.dumps(
                        {"ok": False, "error_code": 401,
                         "description": "Unauthorized"}).encode(),
                        "application/json")
                elif fake.mode == "echo":
                    self._send(400, json.dumps(
                        {"ok": False, "error_code": 400,
                         "description": f"Bad Request: token {TOKEN} and "
                                        f"chat {CHAT} are wrong"}).encode(),
                        "application/json")
                else:
                    rec["result"] = fake._result(method, body)
                    self._json(200, {"ok": True, "result": rec["result"]})

            def _updates(self, body):
                if fake.updates_mode == "409":
                    return self._json(409, {
                        "ok": False, "error_code": 409,
                        "description": "Conflict: terminated by other "
                                       "getUpdates request; make sure that "
                                       "only one bot instance is running"})
                offset = int(body.get("offset") or 0)
                wait = min(float(body.get("timeout") or 0), fake.hold_s)
                with fake._cond:
                    if offset > 0:
                        fake.updates = [u for u in fake.updates
                                        if u["update_id"] >= offset]
                    elif offset < 0:
                        fake.updates = fake.updates[offset:]
                    fake._cond.wait_for(lambda: fake.updates, timeout=wait)
                    result = list(fake.updates)
                self._json(200, {"ok": True, "result": result})

            def _json(self, code, doc):
                self._send(code, json.dumps(doc).encode(), "application/json")

            def _send(self, code, body, ctype):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:                         # client timed out
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def _result(self, method: str, body: dict):
        if method in ("sendMessage", "sendVoice", "sendPhoto", "sendVideo",
                      "sendAudio", "sendDocument"):
            with self._cond:
                self._next_message += 1
                return {"message_id": self._next_message,
                        "chat": {"id": int(body.get("chat_id") or 0)}}
        if method == "getFile":
            return {"file_id": body["file_id"],
                    "file_path": f"files/{body['file_id']}"}
        return True

    def push(self, update: dict) -> int:
        """Queue one update; answers its `update_id`."""
        with self._cond:
            uid = self._next_update
            self._next_update += 1
            self.updates.append({"update_id": uid, **update})
            self._cond.notify_all()
        return uid

    def calls(self, method: str) -> list[dict]:
        return [r for r in self.requests if r["method"] == method]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """A dead local port as the default API base, so nothing in this file
    can reach Telegram by forgetting to name the fake."""
    monkeypatch.setattr(notify_telegram, "API", "http://127.0.0.1:9")


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBotApi()
    monkeypatch.setattr(notify_telegram, "API", fake.url)
    try:
        yield fake
    finally:
        fake.close()


def _write(folder: Path, name: str, **doc) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = {"kind": "telegram", "label": name.title(),
            "bot_token": TOKEN, "chat_id": CHAT}
    base.update(doc)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


def _no_secret(blob, *secrets):
    text = json.dumps(blob) if not isinstance(blob, str) else blob
    for s in secrets or (TOKEN, CHAT):
        assert s not in text, f"a secret reached {text[:300]!r}"


# --------------------------------------------------------------------------- #
# The registry: a channel is a file (AC-473)
# --------------------------------------------------------------------------- #
def test_the_port_and_the_closed_table():
    """AC-473. `telegram` resolves through ports.NOTIFIERS and so does
    `whatsapp` (docs/messaging.md AC-516), nothing else; the adapter
    satisfies the port."""
    assert set(ports.NOTIFIERS) == {"telegram", "whatsapp"}
    assert ports.NOTIFIERS["telegram"] is notify_telegram.TelegramNotifier
    assert isinstance(notify_telegram.TelegramNotifier(TOKEN, CHAT),
                      ports.Notifier)


def test_a_channel_is_a_file_and_its_stem_is_its_name(tmp_path):
    """AC-473, AC-471. One file, listed by its stem, with no token and no
    chat id on the row -- and exactly the six keys the spec names."""
    _write(tmp_path, "owner", label="Owner's phone")
    rows = notify.Channels(tmp_path).listing(default="owner")
    assert rows == [{"name": "owner", "kind": "telegram",
                     "label": "Owner's phone", "configured": True,
                     "problem": "", "default": True}]
    _no_secret(rows)


def test_a_missing_folder_is_an_empty_list(tmp_path):
    assert notify.Channels(tmp_path / "nope").listing(default="") == []


@pytest.mark.parametrize("stem,doc,problem", [
    ("slack", {"kind": "slack"}, "unknown kind 'slack'"),
    ("short", {"bot_token": "123:abc"},
     "bot_token is not <digits>:<35+ chars>"),
    ("nochat", {"chat_id": "me"}, "chat_id is not a number"),
    ("extra", {"aliases": {}}, "unknown field 'aliases'"),
    ("Bad_Name", {}, "the name must be 1-32 of a-z, 0-9 and -"),
])
def test_a_malformed_file_is_listed_with_its_problem(tmp_path, stem, doc,
                                                     problem):
    """AC-473. Listed `configured: false` with the rule it broke -- never
    hidden, and the problem text never quotes the secret it judged."""
    _write(tmp_path, stem, **doc)
    [row] = notify.Channels(tmp_path).listing(default="")
    assert row["configured"] is False
    assert row["problem"] == problem
    _no_secret(row)


def test_a_file_that_is_not_json_is_listed_with_its_problem(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "broken.json").write_text("{not json " + TOKEN,
                                          encoding="utf-8")
    [row] = notify.Channels(tmp_path).listing(default="")
    assert row["configured"] is False
    assert row["problem"].startswith("is not valid JSON")
    _no_secret(row)


def test_an_edit_on_disk_is_read_on_the_next_use(tmp_path):
    """AC-473. Re-read by stat: the same registry object sees the new label
    and the new problem with no restart."""
    path = _write(tmp_path, "owner", label="Old")
    channels = notify.Channels(tmp_path)
    assert channels.listing(default="")[0]["label"] == "Old"
    _write(tmp_path, "owner", label="New label")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert channels.listing(default="")[0]["label"] == "New label"
    path.unlink()
    assert channels.listing(default="") == []


@pytest.mark.asyncio
async def test_a_copied_channel_file_sends_with_no_restart(tmp_path, bot):
    """AC-473's Gherkin: sam.json copied in from another PC is listed and
    usable by the registry that was already running."""
    live = tmp_path / "notify"
    channels = notify.Channels(live)
    assert channels.listing(default="") == []
    elsewhere = _write(tmp_path / "other-pc", "sam",
                       bot_token=OTHER_TOKEN, chat_id=OTHER_CHAT)
    live.mkdir()
    shutil.copy(elsewhere, live / "sam.json")
    assert [r["name"] for r in channels.listing(default="")] == ["sam"]
    result = await channels.send("sam", "t", "hello")
    assert result == ports.NotifyResult(True, "")
    assert bot.requests[-1]["path"] == f"/bot{OTHER_TOKEN}/sendMessage"
    assert bot.requests[-1]["body"]["chat_id"] == OTHER_CHAT


def test_save_and_remove_round_trip(tmp_path):
    """The file the settings page's Add writes is the file a hand copy is."""
    channels = notify.Channels(tmp_path / "notify")
    channels.save({"name": "owner", "kind": "telegram",
                   "label": "Owner's phone", "bot_token": TOKEN,
                   "chat_id": CHAT})
    on_disk = json.loads((tmp_path / "notify" / "owner.json")
                         .read_text(encoding="utf-8"))
    # `inbound` (docs/messaging.md MS-25) is written even when the form left
    # it out, so a copied file shows the switch it can flip.
    assert on_disk == {"kind": "telegram", "label": "Owner's phone",
                       "bot_token": TOKEN, "chat_id": CHAT, "inbound": False}
    # Same name replaces it -- the only way to change a token.
    channels.save({"name": "owner", "kind": "telegram", "label": "Phone",
                   "bot_token": OTHER_TOKEN, "chat_id": OTHER_CHAT})
    assert channels.listing(default="")[0]["label"] == "Phone"
    (tmp_path / "notify" / "owner.offset").write_text("{}", "utf-8")
    channels.remove("owner")
    assert not (tmp_path / "notify" / "owner.json").exists()
    # An inbound read position goes with its channel (MS-25).
    assert not (tmp_path / "notify" / "owner.offset").exists()
    assert channels.listing(default="") == []
    with pytest.raises(KeyError):
        channels.remove("owner")


@pytest.mark.parametrize("change,why", [
    ({"name": "../evil"}, "name"),
    ({"name": ""}, "name"),
    ({"kind": "slack"}, "unknown kind"),
    ({"bot_token": "nope"}, "bot_token"),
    ({"chat_id": ""}, "chat_id"),
    ({"parse_mode": "HTML"}, "unknown field"),
])
def test_save_refuses_and_writes_nothing(tmp_path, change, why):
    channels = notify.Channels(tmp_path / "notify")
    doc = {"name": "owner", "kind": "telegram", "label": "x",
           "bot_token": TOKEN, "chat_id": CHAT}
    doc.update(change)
    with pytest.raises(notify.ChannelRefused) as e:
        channels.save(doc)
    assert why in str(e.value)
    _no_secret(str(e.value))
    assert not (tmp_path / "notify").exists() or not list(
        (tmp_path / "notify").iterdir())


# --------------------------------------------------------------------------- #
# Delivery (AC-472)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_send_is_plain_text_to_the_right_chat(tmp_path, bot):
    _write(tmp_path, "owner")
    result = await notify.Channels(tmp_path).send(
        "owner", "Weekly review in SITE: completed", "**All** done.")
    assert result == ports.NotifyResult(True, "")
    [req] = bot.requests
    assert req["path"] == f"/bot{TOKEN}/sendMessage"
    assert req["content_type"] == "application/json"
    assert req["body"] == {"chat_id": CHAT,
                           "text": "Weekly review in SITE: completed\n\n"
                                   "**All** done."}
    assert "parse_mode" not in req["body"]


def test_clip_caps_at_600_and_cuts_at_a_line_end():
    """AC-472. At most 600 characters of answer, cut at a line end, and it
    says it was cut."""
    lines = [f"line {i:03d} " + "x" * 40 for i in range(100)]
    text = "\n".join(lines)
    clipped = notify.clip(text)
    assert len(clipped) <= notify.ANSWER_CAP == 600
    assert clipped.endswith("\n...")
    kept = clipped[:-len("\n...")]
    assert text.startswith(kept) and kept.endswith(lines[len(
        kept.split("\n")) - 1]), "the cut fell inside a line"
    assert notify.clip("short") == "short"
    one_line = "y" * 5000
    assert len(notify.clip(one_line)) <= 600


@pytest.mark.asyncio
async def test_what_reaches_the_service_is_capped(tmp_path, bot):
    _write(tmp_path, "owner")
    title = "T " * 400
    await notify.Channels(tmp_path).send("owner", title, "z" * 10_000)
    text = bot.requests[-1]["body"]["text"]
    head, _, answer = text.partition("\n\n")
    assert len(answer) <= 600
    assert len(head) <= notify.TITLE_CAP and "\n" not in head


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,detail", [("502", "502 Bad Gateway"),
                                         ("401", "401 Unauthorized")])
async def test_a_service_failure_is_a_result_with_its_reason(tmp_path, bot,
                                                             mode, detail):
    """AC-472's Gherkin: Telegram answering 502 is `failed: 502 Bad
    Gateway`, a result and not an exception, and it is not retried."""
    bot.mode = mode
    _write(tmp_path, "owner")
    result = await notify.Channels(tmp_path).send("owner", "t", "x")
    assert result == ports.NotifyResult(False, detail)
    assert len(bot.requests) == 1, "a failure must not be retried in a loop"


@pytest.mark.asyncio
async def test_a_reason_that_echoes_the_secret_is_scrubbed(tmp_path, bot):
    """AC-471. The service's own text is external input; if it quotes the
    token or the chat id they are scrubbed before anyone sees the detail."""
    bot.mode = "echo"
    _write(tmp_path, "owner")
    result = await notify.Channels(tmp_path).send("owner", "t", "x")
    assert result.ok is False
    assert result.detail.startswith("400 Bad Request")
    _no_secret(result.detail)


@pytest.mark.asyncio
async def test_a_timeout_is_a_failure_with_a_reason(bot):
    bot.mode, bot.delay = "slow", 3.0
    notifier = notify_telegram.TelegramNotifier(TOKEN, CHAT, timeout=0.5)
    t0 = time.monotonic()
    result = await notifier.send("t", "x")
    assert time.monotonic() - t0 < 2.5
    assert result == ports.NotifyResult(False, "timed out after 0.5 s")


def test_the_default_timeout_is_15_seconds():
    assert notify_telegram.TIMEOUT == 15.0
    assert notify_telegram.TelegramNotifier(TOKEN, CHAT).timeout == 15.0


@pytest.mark.asyncio
async def test_an_unreachable_service_is_a_failure_with_a_reason():
    """The autouse dead port: a network error, named, with no URL in it."""
    result = await notify_telegram.TelegramNotifier(TOKEN, CHAT).send("t", "x")
    assert result.ok is False and result.detail.startswith("network error")
    _no_secret(result.detail)


@pytest.mark.asyncio
async def test_a_send_does_not_block_the_loop(tmp_path, bot):
    """D-1. The HTTP call runs in a thread: the loop keeps ticking while a
    slow service holds the request."""
    bot.mode, bot.delay = "slow", 0.6
    _write(tmp_path, "owner")
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1
    ticker = asyncio.create_task(tick())
    try:
        result = await notify.Channels(tmp_path).send("owner", "t", "x")
    finally:
        ticker.cancel()
    assert result.ok
    assert ticks >= 10, f"the loop ticked {ticks} times during a 0.6 s send"


@pytest.mark.asyncio
async def test_a_missing_channel_fails_and_nothing_is_rerouted(tmp_path, bot):
    """AC-472's Gherkin: "sam" was deleted; sending to it records the
    failure and NOTHING reaches any other channel instead."""
    _write(tmp_path, "owner")
    result = await notify.Channels(tmp_path).send("sam", "t", "x")
    assert result == ports.NotifyResult(False, "channel sam not found")
    assert bot.requests == []


@pytest.mark.asyncio
async def test_a_malformed_channel_fails_with_its_problem(tmp_path, bot):
    _write(tmp_path, "owner", bot_token="bad")
    result = await notify.Channels(tmp_path).send("owner", "t", "x")
    assert result == ports.NotifyResult(
        False, "bot_token is not <digits>:<35+ chars>")
    assert bot.requests == []


# --------------------------------------------------------------------------- #
# The settings schema
# --------------------------------------------------------------------------- #
def test_notify_default_is_a_setting_in_the_notify_group(tmp_path):
    from daemon.settings import BY_KEY, GROUPS, Settings
    spec = BY_KEY["notify_default"]
    assert (spec.group, spec.kind, spec.default, spec.restart) == (
        "notify", "text", "", False)
    assert GROUPS["notify"].collection == "/api/notify"
    served = {g["key"]: g for g in Settings(tmp_path / "s.json")
              .describe()["groups"]}
    assert served["notify"]["collection"] == "/api/notify"
    assert served["look"]["collection"] == ""


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def served(tmp_path, monkeypatch, bot):
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.delenv("BUTLER_SELF_URL", raising=False)
    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "fake"}})
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    frames = []

    async def spy(**payload):
        frames.append(payload)
    monkeypatch.setattr(hub, "broadcast", spy)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        yield hub, client, tmp_path / "notify", frames, bot
    finally:
        await client.close()


OWNER = {"name": "owner", "kind": "telegram", "label": "Owner's phone",
          "bot_token": TOKEN, "chat_id": CHAT}


async def _json(r):
    return r.status, await r.json()


@pytest.mark.asyncio
async def test_the_list_route_describes_kinds_and_no_items(served):
    hub, client, folder, frames, bot = served
    status, doc = await _json(await client.get("/api/notify"))
    assert status == 200
    assert doc["items"] == []
    assert [k["kind"] for k in doc["kinds"]] == ["telegram", "whatsapp"]
    kind = doc["kinds"][0]
    assert [f["key"] for f in kind["fields"]] == ["bot_token", "chat_id",
                                                  "inbound"]
    # The token and the chat id are secret; the inbound switch is not.
    assert [f["secret"] for f in kind["fields"]] == [True, True, False]


@pytest.mark.asyncio
async def test_add_writes_the_file_and_the_first_one_becomes_default(served):
    """AC-474's second clause: adding the FIRST channel while notify_default
    is blank sets it, as a visible settings write."""
    hub, client, folder, frames, bot = served
    status, doc = await _json(await client.post("/api/notify", json=OWNER))
    assert status == 200, doc
    assert json.loads((folder / "owner.json").read_text(
        encoding="utf-8"))["bot_token"] == TOKEN
    assert hub.settings.get("notify_default") == "owner"
    assert [f for f in frames if f["type"] == "settings"][-1]["changed"] == {
        "notify_default": "owner"}
    assert doc["items"][0]["default"] is True
    # A second channel does not move the default.
    status, _ = await _json(await client.post("/api/notify", json={
        **OWNER, "name": "sam", "bot_token": OTHER_TOKEN,
        "chat_id": OTHER_CHAT}))
    assert status == 200
    assert hub.settings.get("notify_default") == "owner"


@pytest.mark.asyncio
async def test_a_refused_add_is_a_400_and_writes_nothing(served):
    hub, client, folder, frames, bot = served
    status, doc = await _json(await client.post("/api/notify", json={
        **OWNER, "bot_token": "short"}))
    assert status == 400 and "bot_token" in doc["error"]
    assert not folder.exists() or not list(folder.iterdir())
    assert hub.settings.get("notify_default") == ""
    assert frames == []


@pytest.mark.asyncio
async def test_send_test_answers_the_services_own_words(served):
    hub, client, folder, frames, bot = served
    await client.post("/api/notify", json=OWNER)
    status, doc = await _json(await client.post("/api/notify/owner/test"))
    assert (status, doc) == (200, {"ok": True, "detail": ""})
    assert bot.requests[-1]["body"]["chat_id"] == CHAT
    _no_secret(bot.requests[-1]["body"]["text"])
    bot.mode = "401"
    status, doc = await _json(await client.post("/api/notify/owner/test"))
    assert (status, doc) == (200, {"ok": False, "detail": "401 Unauthorized"})
    status, doc = await _json(await client.post("/api/notify/nobody/test"))
    assert status == 404 and "nobody" in doc["error"]


@pytest.mark.asyncio
async def test_remove_deletes_the_file(served):
    hub, client, folder, frames, bot = served
    await client.post("/api/notify", json=OWNER)
    status, doc = await _json(await client.delete("/api/notify/owner"))
    assert status == 200 and doc["items"] == []
    assert not (folder / "owner.json").exists()
    status, doc = await _json(await client.delete("/api/notify/owner"))
    assert status == 404
    status, doc = await _json(await client.delete("/api/notify/..%2Fx"))
    assert status in (400, 404)


@pytest.mark.asyncio
async def test_the_page_never_receives_a_token_or_a_chat_id(served):
    """AC-471, the Gherkin row. Every route body this feature answers and
    every frame it broadcasts, including a failure whose service text
    quotes the token, carries neither secret."""
    hub, client, folder, frames, bot = served
    bodies = []
    for method, url, body in (
            ("POST", "/api/notify", OWNER),
            ("GET", "/api/notify", None),
            ("POST", "/api/notify/owner/test", None),
            ("GET", "/api/settings", None)):
        r = await client.request(method, url, json=body)
        bodies.append((url, r.status, await r.text()))
    bot.mode = "echo"
    r = await client.post("/api/notify/owner/test")
    bodies.append(("echo", r.status, await r.text()))
    r = await client.post("/api/notify", json={**OWNER, "chat_id": "x"})
    bodies.append(("refused", r.status, await r.text()))
    r = await client.delete("/api/notify/owner")
    bodies.append(("delete", r.status, await r.text()))
    for url, status, text in bodies:
        assert status < 500, (url, status, text)
        _no_secret(text)
    assert frames, "the first add must have broadcast a settings frame"
    _no_secret(frames)
