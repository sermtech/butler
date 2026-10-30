"""Proactive P2: the heartbeat.
docs/proactive.md section 4.1 (PR-8..PR-13), AC-481..AC-484.

* ONE ROW PER PROJECT (AC-481): an automation with `kind: "heartbeat"` and
  no recipe; a second save for the same project updates the first; its
  checklist is `<Paths.heartbeat>/<row id>.md`, written atomically by the
  Library's one route.
* EMPTY MEANS NO MODEL CALL (AC-482): a checklist of only blank lines,
  headings, empty bullets, empty checkboxes or code fences births no chat,
  sends the agent nothing, folds into one `skipped_empty` row and tells
  nobody. The failure this catches is a model bill for an empty list.
* CADENCE (AC-483): interval presets only; a missed tick is never caught up
  and leaves no row; no run inside quiet hours; a busy project is a folded
  `skipped_busy` that notifies nobody.
* ONE STANDING CHAT (AC-484): every run of a project's heartbeat lands in
  that project's single "Heartbeat" chat, across a restart.

Real runs go through the fake coding agent; nothing reaches Telegram.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_automations import (CH, ROOT, Clock, _finish,         # noqa: E402
                              _project, et, never_busy)
from test_library_voice import _harness_hub                     # noqa: E402
from test_notify import FakeBotApi, _write                      # noqa: E402
from test_schedule import EASTERN                               # noqa: E402
from daemon import automations as au                            # noqa: E402
from daemon import butlerd, ports                               # noqa: E402
from daemon import library as lib                               # noqa: E402
from daemon import schedule as sched                            # noqa: E402
from daemon.adapters import notify_telegram                     # noqa: E402
from daemon.adapters.harness_fake import ScriptedTurn           # noqa: E402


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


def _store(tmp_path, rows=(ROOT, CH)):
    library = lib.Library(tmp_path / "library", rows=lambda: set(rows),
                          root_id=lambda: ROOT)
    store = au.AutomationStore(tmp_path / "library", library, EASTERN,
                               heartbeat=tmp_path / "heartbeat")
    return store


def _beat(store, now, project=CH, checklist="- check new invoices",
          **kw):
    return store.save_heartbeat({"project": project, "checklist": checklist,
                                 **kw}, now)


# --------------------------------------------------------------------------- #
# AC-481: one row per project, its checklist a file
# --------------------------------------------------------------------------- #
def test_one_heartbeat_per_project_and_its_checklist_file(tmp_path):
    store = _store(tmp_path)
    now = et(2026, 10, 7, 10, 0)
    a = _beat(store, now)
    assert a["kind"] == au.HEARTBEAT and "recipe" not in a
    assert a["project"] == CH and a["enabled"] is True
    assert a["silence"] is True                     # PR-3: ON for heartbeats
    assert a["graceMinutes"] == 0                   # PR-11: no catch-up
    assert a["schedule"] == {"preset": {"preset": "every", "minutes": 60},
                             "cron": "0 * * * *", "label": "Every 60 minutes"}
    assert au.parse_iso(a["nextRunAt"]) == et(2026, 10, 7, 11, 0)
    path = tmp_path / "heartbeat" / f"{CH}.md"
    assert path.read_text(encoding="utf-8") == "- check new invoices\n"
    # A second save for the same project UPDATES the first (PR-8).
    b = _beat(store, now, checklist="- is the build red?", minutes=15)
    assert b["id"] == a["id"]
    assert [x["id"] for x in store.automations()] == [a["id"]]
    assert b["schedule"]["cron"] == "*/15 * * * *"
    assert path.read_text(encoding="utf-8") == "- is the build red?\n"
    assert not list((tmp_path / "heartbeat").glob("*.tmp"))
    # The root row's is the global one, and a separate row.
    g = _beat(store, now, project=ROOT)
    assert g["id"] != a["id"] and len(store.automations()) == 2


@pytest.mark.parametrize("body,rule", [
    ({"minutes": 45}, "every 15, 30, 60, 120 or 240 minutes"),
    ({"minutes": "60"}, "every 15, 30, 60, 120 or 240 minutes"),
    ({"project": "s-gone"}, "not a project row"),
    ({"checklist": 7}, "checklist is text"),
    ({"checklist": "x" * (au.CHECKLIST_MAX + 1)}, "checklist is"),
    ({"enabled": "yes"}, "enabled"),
    ({"recipe": "rcp-00000000"}, "no field recipe"),
])
def test_every_heartbeat_refusal_names_its_rule_and_writes_nothing(
        tmp_path, body, rule):
    store = _store(tmp_path)
    full = {"project": CH, "checklist": "- x", **body}
    with pytest.raises(lib.LibraryRefused, match=rule):
        store.save_heartbeat(full, et(2026, 10, 7, 10, 0))
    assert store.automations() == []
    assert not (tmp_path / "heartbeat").exists() or not list(
        (tmp_path / "heartbeat").iterdir())


def test_the_automation_route_shape_cannot_make_a_second_heartbeat(tmp_path):
    store = _store(tmp_path)
    now = et(2026, 10, 7, 10, 0)
    a = _beat(store, now)
    b = store.save({"kind": "heartbeat", "project": CH,
                    "schedule": {"preset": "every", "minutes": 30}}, now)
    assert b["id"] == a["id"] and len(store.automations()) == 1
    with pytest.raises(lib.LibraryRefused, match="kind"):
        store.save({"kind": "nonsense", "project": CH}, now)
    with pytest.raises(lib.LibraryRefused, match="kind cannot change"):
        store.save({"id": a["id"], "kind": "reminder", "text": "x",
                    "runAt": "2026-10-08T09:00"}, now)
    with pytest.raises(lib.LibraryRefused, match="every"):
        store.save({"id": a["id"], "schedule": {"cron": "0 9 * * *"}}, now)


def test_a_store_without_a_checklist_folder_refuses_heartbeats(tmp_path):
    library = lib.Library(tmp_path / "library", rows=lambda: {ROOT, CH},
                          root_id=lambda: ROOT)
    store = au.AutomationStore(tmp_path / "library", library, EASTERN)
    with pytest.raises(lib.LibraryRefused, match="no heartbeat"):
        _beat(store, et(2026, 10, 7, 10, 0))


def test_the_longest_checklist_still_fits_one_typed_message():
    """`_typed` refuses past TYPED_MAX_CHARS, so the cap on the checklist
    is what keeps a full one from failing every run."""
    from daemon import settings
    full = au.heartbeat_prompt("x" * au.CHECKLIST_MAX) + "\n\n" + \
        au.SILENCE_FOOTER
    assert len(full) <= settings.TYPED_MAX_CHARS


@pytest.mark.parametrize("minutes,cron", [
    (15, "*/15 * * * *"), (30, "*/30 * * * *"), (60, "0 * * * *"),
    (120, "0 */2 * * *"), (240, "0 */4 * * *")])
def test_interval_presets_compile_to_cron(minutes, cron):
    assert sched.compile_preset({"preset": "every",
                                 "minutes": minutes}) == cron
    assert sched.EVERY_MINUTES == (15, 30, 60, 120, 240)


# --------------------------------------------------------------------------- #
# PR-10: what "empty" means
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "", "\n\n  \n", "# Heartbeat\n\n## Inbox\n", "-\n*\n+\n1.\n",
    "- [ ]\n- [x]\n* [ ]\n", "```\n```\n", "# List\n\n- \n- [ ] \n~~~\n~~~",
])
def test_an_effectively_empty_checklist(text):
    assert au.checklist_empty(text) is True, text


@pytest.mark.parametrize("text", [
    "check the inbox", "# Inbox\n- new invoices?", "- [ ] backup log",
    "```\nls build/\n```", "#hashtag is not a heading", "1. tell me",
])
def test_a_checklist_with_content(text):
    assert au.checklist_empty(text) is False, text


# --------------------------------------------------------------------------- #
# the hub: real runs through the fake agent
# --------------------------------------------------------------------------- #
def _hub(tmp_path, monkeypatch, checklist="- check new invoices",
         channels=("owner",)):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    clock = Clock(et(2026, 9, 28, 10, 30))
    hub.automations.tz = EASTERN
    hub.scheduler.clock = clock
    a = hub.automations.save_heartbeat(
        {"project": row, "checklist": checklist, "channels": list(channels)},
        clock())
    monkeypatch.setattr(hub.scheduler, "enqueue",
                        lambda run: hub.scheduler._queued.add(run["id"])
                        or _pending.append(run))
    _pending.clear()
    return hub, row, a, clock


_pending: list[dict] = []


async def _tick(hub, at):
    """One decision at `at`, then dispatch what it queued, then wait for
    every run to end."""
    hub.scheduler.clock.at = at
    await hub.scheduler.tick()
    while _pending:
        run = _pending.pop(0)
        await hub.scheduler.dispatch(run)
        hub.scheduler._queued.discard(run["id"])
    await _finish(hub)


def _fake(hub, *texts):
    fake = ports.build(ports.HARNESSES, "fake",
                       script=tuple(ScriptedTurn(t) for t in texts)
                       if texts else None)
    hub.brain.host._harness_for = lambda _chat=None: fake
    return fake


@pytest.mark.asyncio
async def test_an_empty_checklist_makes_no_model_call_and_no_chat(
        tmp_path, monkeypatch, bot):
    """AC-482."""
    hub, row, a, _clock = _hub(tmp_path, monkeypatch, checklist="# Inbox\n- ")
    _write(hub.channels.folder, "owner")
    fake = _fake(hub)
    births = []
    real_birth = hub.new_conversation

    async def spy(*args, **kw):
        births.append(kw)
        return await real_birth(*args, **kw)
    monkeypatch.setattr(hub, "new_conversation", spy)
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    await _tick(hub, et(2026, 9, 28, 12, 0, 20))
    assert fake.prompts == [], "an empty checklist reached the model"
    assert births == [], "an empty checklist birthed a chat"
    [run] = hub.automations.runs(a["id"])
    assert run["status"] == "skipped_empty" and run["chat"] == ""
    assert run["occurrenceCount"] == 2, "the two empty ticks did not fold"
    assert not run.get("notify"), "an empty checklist notified"
    assert bot.requests == []


@pytest.mark.asyncio
async def test_a_heartbeat_prompt_is_preamble_checklist_and_footer(
        tmp_path, monkeypatch):
    hub, row, a, _clock = _hub(tmp_path, monkeypatch,
                               checklist="- {not} a placeholder\n- build red?",
                               channels=())
    fake = _fake(hub, "[SILENT]")
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    [prompt] = fake.prompts
    assert prompt.startswith(au.HEARTBEAT_PREAMBLE)
    # The checklist is the user's own text, verbatim: no expansion runs on
    # it, so braces are not placeholders (AC-448 untouched).
    assert "- {not} a placeholder\n- build red?" in prompt
    assert prompt.endswith("\n\n" + au.SILENCE_FOOTER)
    [run] = hub.automations.runs(a["id"])
    assert run["status"] == "completed" and run["silent"] is True


@pytest.mark.asyncio
async def test_every_run_lands_in_the_one_heartbeat_chat(
        tmp_path, monkeypatch):
    """AC-484, across a restart of the store."""
    hub, row, a, _clock = _hub(tmp_path, monkeypatch, channels=())
    _fake(hub, "Two new invoices.", "[SILENT]", "One more invoice.")
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    await _tick(hub, et(2026, 9, 28, 12, 0, 20))
    hub.automations = au.AutomationStore(
        hub.automations.root, hub.automations.library, EASTERN,
        heartbeat=hub.automations.heartbeat)
    hub.scheduler.store = hub.automations
    await _tick(hub, et(2026, 9, 28, 13, 0, 20))
    runs = hub.automations.runs(a["id"])
    assert [r["status"] for r in runs] == ["completed"] * 3
    chats = {r["chat"] for r in runs}
    assert len(chats) == 1 and "" not in chats
    [chat] = chats
    assert hub.automations.get(a["id"])["chat"] == chat
    record = await asyncio.to_thread(hub.brain.session_chat, chat)
    assert record["title"] == "Heartbeat"
    users = [m["content"] for m in record["messages"] if m["role"] == "user"]
    assert len(users) == 3, "a heartbeat run went to another chat"


@pytest.mark.asyncio
async def test_a_deleted_heartbeat_chat_is_born_again(tmp_path, monkeypatch):
    hub, row, a, _clock = _hub(tmp_path, monkeypatch, channels=())
    _fake(hub, "One.", "Two.")
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    first = hub.automations.runs(a["id"])[0]["chat"]
    assert first
    hub.automations.set_chat(a["id"], "chat-that-is-gone")
    await _tick(hub, et(2026, 9, 28, 12, 0, 20))
    second = hub.automations.runs(a["id"])[0]["chat"]
    assert second and second != "chat-that-is-gone"
    assert hub.automations.get(a["id"])["chat"] == second


# --------------------------------------------------------------------------- #
# AC-483: cadence
# --------------------------------------------------------------------------- #
def test_a_missed_tick_is_never_caught_up_and_leaves_no_row(tmp_path):
    store = _store(tmp_path)
    a = _beat(store, et(2026, 10, 7, 7, 30))           # next 08:00
    due, _ = store.decide(et(2026, 10, 7, 10, 30), never_busy)
    assert due == [] and store.runs(a["id"]) == []
    assert au.parse_iso(store.get(a["id"])["nextRunAt"]) == \
        et(2026, 10, 7, 11, 0)
    due, _ = store.decide(et(2026, 10, 7, 11, 0, 30), never_busy)
    assert len(due) == 1 and due[0]["status"] == "pending"


def test_no_heartbeat_runs_inside_quiet_hours(tmp_path):
    store = _store(tmp_path)
    a = _beat(store, et(2026, 10, 7, 22, 30))          # next 23:00
    due, _ = store.decide(et(2026, 10, 7, 23, 0, 30), never_busy,
                          quiet=True)
    assert due == [] and store.runs(a["id"]) == []
    assert au.parse_iso(store.get(a["id"])["nextRunAt"]) == \
        et(2026, 10, 8, 0, 0)


def test_quiet_hours_do_not_touch_an_ordinary_automation(tmp_path):
    store = _store(tmp_path)
    recipe = store.library.recipes.save(None, "Nightly", CH, "Check it.")
    a = store.save({"recipe": recipe.id, "schedule": {"preset": "daily",
                                                      "time": "23:00"}},
                   et(2026, 10, 7, 22, 30))
    due, _ = store.decide(et(2026, 10, 7, 23, 0, 30), never_busy,
                          quiet=True)
    assert [r["automation"] for r in due] == [a["id"]]


def test_a_busy_project_is_one_folded_skip(tmp_path):
    store = _store(tmp_path)
    a = _beat(store, et(2026, 10, 7, 9, 30))
    busy = lambda _p: "the project's agent limit is full"      # noqa: E731
    for hour in (10, 11, 12):
        due, _ = store.decide(et(2026, 10, 7, hour, 0, 30), busy)
        assert due == []
    [row] = store.runs(a["id"])
    assert row["status"] == "skipped_busy" and row["occurrenceCount"] == 3


@pytest.mark.asyncio
async def test_a_busy_heartbeat_notifies_nobody(tmp_path, monkeypatch, bot):
    hub, row, a, _clock = _hub(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    monkeypatch.setattr(hub.scheduler, "_refusal",
                        lambda _pid: "the project's agent limit is full")
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    [run] = hub.automations.runs(a["id"])
    assert run["status"] == "skipped_busy"
    assert not run.get("notify") and bot.requests == []


@pytest.mark.asyncio
async def test_a_heartbeat_with_a_finding_is_delivered_like_any_run(
        tmp_path, monkeypatch, bot):
    hub, row, a, _clock = _hub(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    _fake(hub, "Two new invoices came in.")
    await _tick(hub, et(2026, 9, 28, 11, 0, 20))
    [run] = hub.automations.runs(a["id"])
    assert run["status"] == "completed" and not run.get("silent")
    assert len(bot.requests) == 1
    assert bot.requests[0]["body"]["text"].startswith(
        "Heartbeat in SITE: completed\n\n")


# --------------------------------------------------------------------------- #
# the panel and the route
# --------------------------------------------------------------------------- #
def test_the_panel_pins_the_heartbeat_apart_from_the_list(tmp_path):
    store = _store(tmp_path)
    now = et(2026, 10, 7, 10, 0)
    recipe = store.library.recipes.save(None, "Weekly", CH, "Review.")
    store.save({"recipe": recipe.id, "schedule": {"cron": "0 9 * * *"}}, now)
    a = _beat(store, now, checklist="- one\n- two\n\n# head")
    doc = store.panel(CH)
    assert doc["heartbeat"]["id"] == a["id"]
    assert doc["heartbeat"]["checklist"] == "- one\n- two\n\n# head\n"
    assert doc["heartbeat"]["recipeName"] == "Heartbeat"
    # The page counts and lists nothing itself: the daemon serves the
    # PR-10 count and the PR-11 presets.
    assert doc["heartbeat"]["checklistItems"] == 2
    assert doc["heartbeatMinutes"] == [15, 30, 60, 120, 240]
    assert doc["heartbeatDefault"] == 60
    assert all(x.get("kind") != au.HEARTBEAT for x in doc["project"])
    assert len(doc["project"]) == 1
    assert store.panel(ROOT)["heartbeat"] is None
    # The full page's listing carries it, named, with no recipe lookup.
    names = sorted(x["recipeName"] for x in store.listing())
    assert names == ["Heartbeat", "Weekly"]


@pytest.mark.asyncio
async def test_the_route_writes_the_checklist_and_the_row(
        tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        r = await client.post("/api/heartbeat", json={
            "project": row, "checklist": "- invoices?", "minutes": 30,
            "enabled": True})
        assert r.status == 200, await r.text()
        got = await r.json()
        assert got["kind"] == "heartbeat" and got["checklist"] == \
            "- invoices?\n"
        path = Path(hub.automations.heartbeat) / f"{row}.md"
        assert path.read_text(encoding="utf-8") == "- invoices?\n"
        r = await client.post("/api/heartbeat", json={
            "project": row, "checklist": "- x", "minutes": 45})
        assert r.status == 400 and "every 15" in (await r.json())["error"]
        assert path.read_text(encoding="utf-8") == "- invoices?\n"
        lib_doc = await (await client.get(
            f"/api/library?project={row}")).json()
        assert lib_doc["automations"]["heartbeat"]["id"] == got["id"]
    finally:
        await client.close()
