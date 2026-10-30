"""Automations, phase 7b: what a run's RESULT does.
docs/automations.md AC-469 (unread), AC-470 (the desk's one line), AC-472
(delivery per channel), and the busy race phases 5-6 left open.

* CHANNELS: a run that ends sends ONE message per named channel through the
  real `notify.Channels` and the real Telegram adapter, to `FakeBotApi` (a
  local stdlib server -- nothing here reaches api.telegram.org). Each result
  is recorded on the run; a failure changes nothing else and nothing is
  rerouted; a slow service never delays the run's own record (D-1). A NEW
  skip row notifies once, a fold into it does not.
* THE DESK: a session whose socket PEER is loopback, listening, with a
  speaker, hears one fixed line; a phone -- even one calling itself "desk" --
  hears nothing; no desk means silence recorded as "no desk"; a busy desk
  holds the line until idle and drops it past the wait.
* UNREAD: a run's chat is unread until it is opened on any device, and the
  open is broadcast.
* THE RACE: the owner's turn wins the project's slot between the runner's
  busy check and the run's own claim; the run WAITS and retries in the same
  chat, never `completed` with the refusal as its answer.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubWs, attach_session                   # noqa: E402
from test_automations import (Clock, _auto, _finish,         # noqa: E402
                              _project, _store, et, never_busy)
from test_library_voice import _harness_hub                   # noqa: E402
from test_notify import CHAT, FakeBotApi, _write              # noqa: E402
from daemon import butlerd, ports                             # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon.adapters import notify_telegram                   # noqa: E402
from test_schedule import EASTERN                             # noqa: E402


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """A dead local port as the API base, so nothing here reaches Telegram."""
    monkeypatch.setattr(notify_telegram, "API", "http://127.0.0.1:9")


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBotApi()
    monkeypatch.setattr(notify_telegram, "API", fake.url)
    try:
        yield fake
    finally:
        fake.close()


# --------------------------------------------------------------------------- #
# the store's half
# --------------------------------------------------------------------------- #
def test_a_new_skip_is_reported_once_and_a_fold_is_not(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 22, 10, 0))
    ended = []
    store.decide(et(2026, 9, 25, 12, 0), never_busy, ended=ended)
    assert [r["status"] for r in ended] == ["skipped_missed"]
    assert ended[0]["occurrenceCount"] == 3
    again = []
    store.decide(et(2026, 9, 27, 12, 0), never_busy, ended=again)
    assert again == [], "a skip folded into an existing row notified again"
    [row] = store.runs(a["id"])
    assert row["occurrenceCount"] == 5


def test_a_waiting_run_that_becomes_a_skip_is_reported(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    _auto(store, recipe, et(2026, 9, 26, 8, 0))
    store.decide(et(2026, 9, 26, 9, 0, 30), lambda _p: "busy")
    ended = []
    store.decide(et(2026, 9, 26, 10, 5), lambda _p: "busy", ended=ended)
    assert [r["status"] for r in ended] == ["skipped_busy"]


def test_seen_and_unseen(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    run = store.run_now(a["id"], et(2026, 9, 26, 8, 1))
    store.run_started(run["id"], "chat-1", et(2026, 9, 26, 8, 1))
    assert store.unseen_chats() == {"chat-1"}
    assert [r["id"] for r in store.mark_seen("chat-1",
                                              et(2026, 9, 26, 9))] == \
        [run["id"]]
    assert store.unseen_chats() == set()
    assert store.mark_seen("chat-1", et(2026, 9, 26, 9)) == []


def test_note_keeps_one_entry_per_event(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    run = store.run_now(a["id"], et(2026, 9, 26, 8, 1))
    store.note(run["id"], "needs_you", "t1", channels={})
    store.note(run["id"], "completed", "t2", channels={"x": {"ok": True}})
    store.note(run["id"], "completed", "t2", spoken="no desk")
    [row] = store.runs(a["id"])
    assert row["notify"] == [
        {"event": "needs_you", "at": "t1", "channels": {}},
        {"event": "completed", "at": "t2", "channels": {"x": {"ok": True}},
         "spoken": "no desk"}]


# --------------------------------------------------------------------------- #
# the hub: a real run, the fake agent, the real channel registry
# --------------------------------------------------------------------------- #
def _with_clock(hub, at):
    clock = Clock(at)
    hub.automations.tz = EASTERN
    hub.scheduler.clock = clock
    return clock


def _setup(tmp_path, monkeypatch, channels=("owner",), prompt="Review."):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    recipe = hub.library.recipes.save(None, "Weekly review", row, prompt)
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    a = hub.automations.save({"recipe": recipe.id, "channels": list(channels),
                              "schedule": {"preset": "daily",
                                           "time": "09:00"}}, clock())
    monkeypatch.setattr(hub.scheduler, "enqueue", lambda run: None)
    return hub, row, a


async def _run(hub, a):
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    await _finish(hub)
    return hub.automations.runs(a["id"])[0]


@pytest.mark.asyncio
async def test_a_completed_run_sends_one_message_per_channel(
        tmp_path, monkeypatch, bot):
    """AC-472, the spec's "one of two channels fails": `owner` is sent,
    `sam` (no file) fails naming itself, and NOTHING goes anywhere else
    -- one request reaches the service, for owner's chat."""
    hub, _row, a = _setup(tmp_path, monkeypatch, ("owner", "sam"))
    _write(hub.channels.folder, "owner", label="Owner's phone")
    done = await _run(hub, a)
    assert done["status"] == "completed", done
    [entry] = done["notify"]
    assert entry["event"] == "completed"
    assert entry["channels"]["owner"] == {
        "ok": True, "detail": "", "label": "Owner's phone",
        "service": "Telegram"}
    assert entry["channels"]["sam"] == {
        "ok": False, "detail": "channel sam not found",
        "label": "sam", "service": ""}
    assert entry["spoken"] == "no desk"
    assert len(bot.requests) == 1, bot.requests
    body = bot.requests[0]["body"]
    assert body["chat_id"] == CHAT and "parse_mode" not in body
    assert body["text"].startswith("Weekly review in SITE: completed\n\n")
    assert done["summary"] and done["summary"][:40] in body["text"]


@pytest.mark.asyncio
async def test_a_service_failure_is_recorded_and_changes_nothing_else(
        tmp_path, monkeypatch, bot):
    """The mock's "Telegram to Owner's phone failed: 401 Unauthorized"."""
    bot.mode = "401"
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner", label="Owner's phone")
    done = await _run(hub, a)
    assert done["status"] == "completed" and done["reason"] == ""
    ch = done["notify"][0]["channels"]["owner"]
    assert ch["ok"] is False and ch["detail"] == "401 Unauthorized"
    assert len(bot.requests) == 1          # tried once, never in a loop


@pytest.mark.asyncio
async def test_a_slow_service_never_delays_the_runs_own_record(
        tmp_path, monkeypatch, bot):
    """D-1: the run is `completed` on disk while its message is still in
    flight; the send is a task started after the record, not a step in it."""
    bot.mode, bot.delay = "slow", 1.5
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if hub.automations.runs(a["id"])[0]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    row = hub.automations.runs(a["id"])[0]
    assert row["status"] == "completed"
    assert not row.get("notify") or "channels" not in row["notify"][0], \
        "the run's record waited for the channel"
    await _finish(hub)
    assert hub.automations.runs(a["id"])[0]["notify"][0]["channels"][
        "owner"]["ok"] is True


@pytest.mark.asyncio
async def test_needs_you_is_sent_once_and_the_end_is_sent_again(
        tmp_path, monkeypatch, bot):
    """AC-463's Gherkin: the channels say the run needs you; the answer lets
    it complete, and that is a second message. Two `notify` entries."""
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ports.Ask(
            qid="q-run", method="confirm", message="Delete 40 drafts?"))
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if len(bot.requests) == 1:
            break
        await asyncio.sleep(0.01)
    assert "Weekly review in SITE: needs you" in \
        bot.requests[0]["body"]["text"]
    await hub.answer_question("q-run", ports.Answer(confirmed=False))
    await _finish(hub)
    done = hub.automations.runs(a["id"])[0]
    assert done["status"] == "completed"
    assert [e["event"] for e in done["notify"]] == ["needs_you", "completed"]
    assert len(bot.requests) == 2


@pytest.mark.asyncio
async def test_a_new_skip_notifies_once_and_its_folds_do_not(
        tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    clock = hub.scheduler.clock
    clock.at = et(2026, 10, 1, 12, 0)          # three days missed
    await hub.scheduler.tick()
    await _finish(hub)
    assert len(bot.requests) == 1
    assert "skipped (missed)" in bot.requests[0]["body"]["text"]
    clock.at = et(2026, 10, 3, 12, 0)          # two more, folded
    await hub.scheduler.tick()
    await _finish(hub)
    [row] = hub.automations.runs(a["id"])
    assert row["occurrenceCount"] == 6
    assert len(bot.requests) == 1, "a folded skip was sent again"


# --------------------------------------------------------------------------- #
# the desk's one line (AC-470)
# --------------------------------------------------------------------------- #
class _Speaker(StubWs):
    """A page's socket that reports its speaker quiet as soon as the daemon
    finishes sending -- what `onDrained` does -- so the playback gate does
    not sit out its whole budget."""

    def __init__(self):
        super().__init__()
        self.session = None

    async def send_str(self, s):
        await super().send_str(s)
        m = json.loads(s)
        if m.get("type") == "speak_end" and self.session is not None:
            hub = self.session.hub
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(hub._playback_done(
                    self.session, {"turn": m["turn"]})))


def _device(hub, peer, name, wake=True, tts=True):
    ws = _Speaker()
    s = hub_mod.SatelliteSession(hub, ws)
    ws.session = s
    s.device_id, s.peer = name, peer
    s.caps = {"wake": wake, "tts": tts}
    hub.sessions.add(s)
    return s


def _types(s):
    return [json.loads(m)["type"] for m in s.ws.sent if isinstance(m, str)]


@pytest.mark.asyncio
async def test_the_desk_hears_one_line_and_a_phone_calling_itself_desk_does_not(
        tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    desk = _device(hub, "127.0.0.1", "desk-browser")
    phone = _device(hub, "100.64.0.7", "desk")          # a string, not a peer
    done = await _run(hub, a)
    assert done["notify"][0]["spoken"] == "said"
    kinds = _types(desk)
    assert [k for k in kinds if k in ("announce", "speak_start",
                                      "speak_end")] == [
        "announce", "speak_start", "speak_end"]
    [ann] = [json.loads(m) for m in desk.ws.sent if isinstance(m, str)
             and json.loads(m)["type"] == "announce"]
    assert ann["text"] == "Your Weekly review run in SITE finished."
    assert ann["chat"] == done["chat"]
    assert any(isinstance(m, bytes) for m in desk.ws.sent), "no audio"
    assert "reply" not in kinds            # heard, and in no transcript
    assert desk.state == hub_mod.IDLE and desk.turn is None   # re-armed
    assert not {"announce", "speak_start"} & set(_types(phone))
    assert not any(isinstance(m, bytes) for m in phone.ws.sent)


@pytest.mark.asyncio
async def test_no_listening_desk_means_silence_recorded_as_no_desk(
        tmp_path, monkeypatch):
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    phone = _device(hub, "100.64.0.7", "phone")
    deaf = _device(hub, "127.0.0.1", "desk", wake=False)   # mic not armed
    assert await hub.announce("x finished.") == "no desk"
    assert _types(phone) == [] and _types(deaf) == []
    hub.settings.set("speak", False)
    _device(hub, "::1", "desk2")
    assert await hub.announce("x finished.") == "off"


@pytest.mark.asyncio
async def test_a_busy_desk_holds_the_line_then_drops_it(tmp_path,
                                                       monkeypatch):
    """AC-14's rule: never over a turn. It waits for idle and speaks at the
    boundary; past the wait it is dropped, not held."""
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    monkeypatch.setattr(hub_mod, "ANNOUNCE_POLL_S", 0.02)
    monkeypatch.setattr(hub_mod, "ANNOUNCE_WAIT_S", 0.3)
    desk = _device(hub, "127.0.0.1", "desk")
    desk.state, desk.turn = hub_mod.THINKING, 777
    assert (await hub.announce("x finished.")).startswith("dropped:")
    assert _types(desk) == []
    monkeypatch.setattr(hub_mod, "ANNOUNCE_WAIT_S", 5.0)
    said = asyncio.create_task(hub.announce("x finished."))
    await asyncio.sleep(0.2)
    assert _types(desk) == [], "it spoke over the owner's turn"
    desk.state, desk.turn = hub_mod.IDLE, None
    assert await said == "said"
    assert _types(desk)[0] == "announce"


@pytest.mark.asyncio
async def test_the_desk_is_the_sockets_peer_and_caps_update_it(
        tmp_path, monkeypatch):
    """D-18's caller half: `handle` records the PEER of the accepted socket,
    and the page's `caps` frame is what says it is listening."""
    hub = _harness_hub(tmp_path, monkeypatch)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "hello", "proto": 1, "device_id": "d",
                            "caps": {"wake": False, "tts": True}})
        await ws.receive_json()
        [s] = [x for x in hub.sessions if x.device_id == "d"]
        assert s.peer == "127.0.0.1" and s.is_desk
        assert hub.desk_session() is None                 # not listening
        await ws.send_json({"type": "caps",
                            "caps": {"wake": True, "tts": True}})
        for _ in range(100):
            if s.caps.get("wake"):
                break
            await asyncio.sleep(0.01)
        assert hub.desk_session() is s
        await ws.send_json({"type": "caps", "caps": {"wake": "yes"}})
        err = await ws.receive_json()
        assert err["type"] == "error" and "caps" in err["message"]
        await ws.close()
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# unread (AC-469)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_run_chat_is_unread_until_opened_on_any_device(
        tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    owner = (await hub.new_conversation(None))["id"]
    done = await _run(hub, a)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    frames = []
    real = hub.broadcast

    async def spy(**payload):
        frames.append(payload)
        await real(**payload)
    monkeypatch.setattr(hub, "broadcast", spy)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        rows = {c["id"]: c for c in
                (await (await client.get("/api/chats")).json())["chats"]}
        assert rows[done["chat"]]["unread"] is True
        assert rows[done["chat"]]["origin"]["kind"] == "automation"
        assert owner not in rows or rows[owner]["unread"] is False
        r = await client.post("/api/session/open", json={"id": done["chat"]})
        assert r.status == 200
        assert {"type": "library", "what": "runs",
                "project": a["project"]} in frames
        rows = {c["id"]: c for c in
                (await (await client.get("/api/chats")).json())["chats"]}
        assert rows[done["chat"]]["unread"] is False
        assert hub.automations.runs(a["id"])[0]["seenAt"]
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# THE RACE (phases 5-6's known limit, fixed here)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_run_that_loses_the_slot_waits_and_retries_in_its_chat(
        tmp_path, monkeypatch, bot):
    """The owner's turn holds SITE's slot, but the runner's busy check
    answered "free" -- the sub-second window between that check and the
    run's own claim, reproduced by making the check stale. The agent limit
    refuses the RUN. It must read `waiting_busy`, never `completed` with the
    refusal as its summary, notify nobody, and stop nothing; once the owner's
    turn ends, the next tick starts it again in the SAME chat."""
    hub, row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ports.Ask(
            qid="q-owner", method="confirm", message="Go on?"))
    owner = attach_session(hub)
    owner.showing = (await hub.new_conversation(row))["id"]
    await hub._typed(owner, {"type": "typed", "text": "tidy up"})
    for _ in range(300):
        if hub.status_for(owner.showing) == "waiting":
            break
        await asyncio.sleep(0.01)
    stale = {"on": True}
    real_refusal = hub.run_refusal
    monkeypatch.setattr(hub, "run_refusal", lambda pid: None if stale["on"]
                        else real_refusal(pid))
    first = await _run(hub, a)
    assert first["status"] == "waiting_busy", first
    assert "nothing was stopped" in first["reason"]
    assert first["summary"] == "" and first["chat"]
    assert hub.status_for(owner.showing) == "waiting"     # untouched
    assert bot.requests == [] and not first.get("notify")
    record = await asyncio.to_thread(hub.brain.session_chat, first["chat"])
    assert any("already has an agent" in m["content"]
               for m in record["messages"] if m["role"] == "assistant")
    # The owner's turn ends; the slot is free; the next tick retries.
    hub.brain.host._harness_for = lambda _chat=None: ports.build(ports.HARNESSES, "fake")
    await hub.answer_question("q-owner", ports.Answer(confirmed=True))
    for _ in range(300):
        if hub.status_for(owner.showing) == "done":
            break
        await asyncio.sleep(0.01)
    stale["on"] = False
    hub.scheduler.clock.at = et(2026, 9, 28, 8, 5)
    [again] = await hub.scheduler.tick()
    assert again["id"] == first["id"]
    await hub.scheduler.dispatch(again)
    await _finish(hub)
    done = hub.automations.runs(a["id"])[0]
    assert done["status"] == "completed", done
    assert done["chat"] == first["chat"], "the retry was born a second chat"
    rows = await asyncio.to_thread(hub.brain.session_list)
    assert sum(1 for c in rows if (c.get("origin") or {}).get("run")
               == first["id"]) == 1
    assert len(bot.requests) == 1

