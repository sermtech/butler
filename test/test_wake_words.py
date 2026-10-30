"""Two wake words, one setting each (`docs/wake-words.md` section 2,
AC-803..AC-805, AC-807, AC-808).

The daemon owns which words exist, which are on, and what each one does:
`screen` talks to the conversation the device shows, `new` starts a new one
in that conversation's project before the turn is minted. A word that is off
(or that the daemon never heard of) is refused before it can barge in, so a
refused word changes nothing anywhere.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub                   # noqa: E402
from test_chat_belongs_to_project import _frames, _hub          # noqa: E402
from daemon import butlerd                                      # noqa: E402
from daemon.hub import IDLE, SPEAKING                           # noqa: E402
from daemon.settings import BY_KEY, Settings, SettingsError     # noqa: E402

BUTLER = {"id": "hey_butler", "phrase": "hey butler",
          "url": "models/hey_butler.onnx"}
JARVIS = {"id": "hey_jarvis", "phrase": "hey jarvis",
          "url": "models/hey_jarvis_v0.1.onnx"}


def _sent(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent if isinstance(m, str)]


def _of(s, kind: str) -> list[dict]:
    return [m for m in _sent(s) if m.get("type") == kind]


async def _settle(*sessions) -> None:
    """Cancel each capture's pipeline task, so nothing outlives the test."""
    for s in sessions:
        if s.task is not None and not s.task.done():
            s.task.cancel()
            try:
                await s.task
            except (asyncio.CancelledError, Exception):     # noqa: BLE001
                pass


def _prefs_hub(tmp_path, **values):
    prefs = Settings(tmp_path / "settings.json")
    if values:
        prefs.update(values)
    hub = make_hub(tmp_path, settings=prefs)
    hub.arbiter.window_s = 0
    return hub, prefs


# --------------------------------------------------------------------------- #
# AC-803: two settings, their defaults
# --------------------------------------------------------------------------- #
def test_each_word_is_a_listening_setting_with_its_default(tmp_path):
    prefs = Settings(tmp_path / "settings.json")
    assert prefs.get("wake_hey_butler") == "screen"
    assert prefs.get("wake_hey_jarvis") == "off"
    for key in ("wake_hey_butler", "wake_hey_jarvis"):
        spec = BY_KEY[key]
        assert spec.group == "listen" and not spec.restart and not spec.advanced
        assert spec.choices == ("off", "screen", "new")
        assert spec.labels == {"off": "Off",
                               "screen": "Conversation on screen",
                               "new": "New conversation"}
        assert spec.help, f"{key} explains nothing"


# --------------------------------------------------------------------------- #
# AC-804: every word off is refused, and the old values survive
# --------------------------------------------------------------------------- #
def test_turning_the_last_word_off_is_refused(tmp_path):
    prefs = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match="wake word"):
        prefs.set("wake_hey_butler", "off")
    assert prefs.get("wake_hey_butler") == "screen"
    assert Settings(tmp_path / "settings.json").get("wake_hey_butler") == \
        "screen", "the refused value reached the file"


def test_both_off_in_one_save_is_refused_and_one_off_is_not(tmp_path):
    prefs = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        prefs.update({"wake_hey_butler": "off", "wake_hey_jarvis": "off"})
    assert (prefs.get("wake_hey_butler"), prefs.get("wake_hey_jarvis")) == \
        ("screen", "off")
    # The swap is legal: one word on is enough.
    prefs.update({"wake_hey_butler": "off", "wake_hey_jarvis": "new"})
    assert (prefs.get("wake_hey_butler"), prefs.get("wake_hey_jarvis")) == \
        ("off", "new")


async def _client(hub, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_the_route_refuses_all_off_with_a_reason_the_page_shows(
        tmp_path, monkeypatch):
    hub, prefs = _prefs_hub(tmp_path)
    s = attach_session(hub)
    client = await _client(hub, monkeypatch)
    try:
        r = await client.post("/api/settings",
                              json={"wake_hey_butler": "off"})
        body = await r.json()
    finally:
        await client.close()
    assert r.status == 400 and "wake word" in body["error"], body
    assert prefs.get("wake_hey_butler") == "screen"
    assert _of(s, "wake") == [], "a refused save was broadcast"


# --------------------------------------------------------------------------- #
# AC-805: ready and the broadcast carry words, threshold, cooldown
# --------------------------------------------------------------------------- #
async def _ready(hub) -> dict:
    s = attach_session(hub, "fresh")
    await hub._hello(s, {"device_id": "fresh"})
    return _of(s, "ready")[0]


@pytest.mark.asyncio
async def test_ready_lists_only_the_words_that_are_on(tmp_path):
    hub, prefs = _prefs_hub(tmp_path)
    assert (await _ready(hub))["wake"] == {
        "words": [BUTLER], "threshold": 0.5, "cooldown_ms": 1300}

    prefs.update({"wake_hey_jarvis": "new", "wake_threshold": 0.7,
                  "wake_cooldown_ms": 1500})
    assert (await _ready(hub))["wake"] == {
        "words": [BUTLER, JARVIS], "threshold": 0.7, "cooldown_ms": 1500}

    prefs.update({"wake_hey_butler": "off"})
    assert (await _ready(hub))["wake"]["words"] == [JARVIS]


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [
    ("wake_hey_butler", "new"),
    ("wake_hey_jarvis", "screen"),
    ("wake_threshold", 0.6),
    ("wake_cooldown_ms", 1400),
])
async def test_a_change_to_any_of_the_four_is_broadcast(
        tmp_path, monkeypatch, key, value):
    hub, prefs = _prefs_hub(tmp_path)
    phone = attach_session(hub, "phone")
    desk = attach_session(hub, "desk")
    client = await _client(hub, monkeypatch)
    try:
        r = await client.post("/api/settings", json={key: value})
        assert r.status == 200, await r.text()
    finally:
        await client.close()
    ready = await _ready(hub)
    for s in (phone, desk):
        frames = _of(s, "wake")
        assert len(frames) == 1, f"{s.device_id} got {frames}"
        frame = dict(frames[0])
        del frame["type"]
        # ONE builder for both frames (D-18): the broadcast IS ready's field.
        assert frame == ready["wake"]


@pytest.mark.asyncio
async def test_an_unrelated_setting_broadcasts_no_wake_frame(
        tmp_path, monkeypatch):
    hub, prefs = _prefs_hub(tmp_path)
    s = attach_session(hub)
    client = await _client(hub, monkeypatch)
    try:
        r = await client.post("/api/settings", json={"turn_mode": "smart"})
        assert r.status == 200
    finally:
        await client.close()
    assert _of(s, "settings") and _of(s, "wake") == []


# --------------------------------------------------------------------------- #
# AC-807: the word is required; off and unknown are refused before anything
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("candidate,reason", [
    ({"score": 0.9, "word": "hey_jarvis"}, "word_off"),
    ({"score": 0.9, "word": "hey_computer"}, "unknown_word"),
    ({"score": 0.9}, "unknown_word"),
])
async def test_a_refused_word_changes_nothing(tmp_path, candidate, reason):
    hub, _ = _prefs_hub(tmp_path)
    desk = attach_session(hub, "desk")
    phone = attach_session(hub, "phone")
    desk.turn = 3
    desk.state = SPEAKING

    await hub._wake_candidate(phone, candidate)

    assert _of(phone, "wake_reject") == [
        {"type": "wake_reject", "reason": reason}]
    assert desk.state == SPEAKING and desk.turn == 3, (
        "a refused word barged in on the desk")
    assert hub.tts.aborted == []
    assert _of(phone, "capture_start") == [] and phone.state == IDLE
    assert phone.turn is None, "a refused word minted a turn"


@pytest.mark.asyncio
async def test_a_word_that_is_on_still_barges_in(tmp_path):
    """The control for the test above: the same wake with a word that is on."""
    hub, _ = _prefs_hub(tmp_path)
    desk = attach_session(hub, "desk")
    phone = attach_session(hub, "phone")
    desk.turn = 3
    desk.state = SPEAKING

    await hub._wake_candidate(phone, {"score": 0.9, "word": "hey_butler"})

    assert desk.state == IDLE and hub.tts.aborted == [3]
    assert len(_of(phone, "capture_start")) == 1
    await _settle(phone)


@pytest.mark.asyncio
async def test_screen_talks_to_the_conversation_on_screen(
        tmp_path, fake_openai):
    hub, _child, chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    hub.arbiter.window_s = 0
    s = attach_session(hub)
    shown = await hub.new_conversation(None)
    s.showing = shown["id"]
    before = len(chats.listing())

    await hub._wake_candidate(s, {"score": 0.9, "word": "hey_butler"})

    assert [f["chat"] for f in _frames(s, "capture_start")] == [shown["id"]]
    assert _frames(s, "opened") == [] and len(chats.listing()) == before
    await _settle(s)


@pytest.mark.asyncio
async def test_new_starts_a_conversation_before_the_turn_is_minted(
        tmp_path, fake_openai):
    hub, _child, chats, _dirs, rows = _hub(tmp_path, fake_openai)
    hub.arbiter.window_s = 0
    hub.settings.update({"wake_hey_jarvis": "new"})
    s = attach_session(hub)
    crawler = rows["Crawler"]
    shown = await hub.new_conversation(crawler.id)
    s.showing = shown["id"]
    before = {c["id"] for c in chats.listing()}

    await hub._wake_candidate(s, {"score": 0.9, "word": "hey_jarvis"})

    born = [c for c in chats.listing() if c["id"] not in before]
    assert len(born) == 1, "no conversation (or several) was born"
    new_id = born[0]["id"]
    assert [f["chat"] for f in _frames(s, "opened")] == [new_id], (
        "the device was not told it moved")
    assert [f["chat"] for f in _frames(s, "capture_start")] == [new_id], (
        "the turn was minted in the old conversation")
    types = [m["type"] for m in _sent(s)]
    assert types.index("opened") < types.index("capture_start")
    assert born[0]["project"] == crawler.path, (
        "the new conversation was not born in the shown one's project")
    assert _frames(s, "speak_start") == [], "the birth spoke a reflex line"
    await _settle(s)


@pytest.mark.asyncio
async def test_a_failed_birth_rejects_the_wake_and_holds_no_microphone(
        tmp_path, fake_openai):
    """D-1: a refused birth mints no turn, so nothing is left to re-arm, and
    the page is told why. The words never land in the old conversation."""
    hub, _child, chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    hub.arbiter.window_s = 0
    hub.settings.update({"wake_hey_butler": "new"})
    s = attach_session(hub)
    shown = await hub.new_conversation(None)
    s.showing = shown["id"]

    async def refuse(*a, **kw):
        raise ValueError("refused at birth")
    hub.new_conversation = refuse

    await hub._wake_candidate(s, {"score": 0.9, "word": "hey_butler"})

    assert _frames(s, "wake_reject") == [
        {"type": "wake_reject", "reason": "new_failed"}]
    assert _frames(s, "capture_start") == [] and s.state == IDLE
    assert s.turn is None and s.showing == shown["id"]


@pytest.mark.asyncio
async def test_new_in_brain_only_mode_talks_to_the_screen(tmp_path):
    """AC-808's behaviour half: no conversations, so `new` is `screen`."""
    hub, _ = _prefs_hub(tmp_path, wake_hey_butler="new")
    s = attach_session(hub)
    assert hub.session_brain() is None

    await hub._wake_candidate(s, {"score": 0.9, "word": "hey_butler"})

    assert len(_of(s, "capture_start")) == 1 and _of(s, "opened") == []
    await _settle(s)


# --------------------------------------------------------------------------- #
# AC-808: `new` is dimmed, with its reason, in brain-only mode
# --------------------------------------------------------------------------- #
def _inactive(prefs) -> dict:
    return {d["key"]: d["inactive"] for d in prefs.describe()["settings"]}


def test_new_is_dimmed_only_in_brain_only_mode(tmp_path):
    prefs = Settings(tmp_path / "settings.json")
    assert prefs.get("brain_mode") == "endpoint"
    assert _inactive(prefs)["wake_hey_butler"] == "", "screen is never dimmed"

    prefs.update({"wake_hey_butler": "new"})
    why = _inactive(prefs)["wake_hey_butler"]
    assert "conversation" in why, f"no reason given: {why!r}"
    assert _inactive(prefs)["wake_hey_jarvis"] == "", "off is not dimmed"

    prefs.update({"brain_mode": "harness"})
    assert _inactive(prefs)["wake_hey_butler"] == ""
