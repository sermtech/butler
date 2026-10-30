"""Automations: the store, the decision, the scheduler and the runner.
docs/automations.md phase 5 -- AC-457..AC-465.

Three groups, and what each would catch:

* THE DECISION, pure over the files with an INJECTED CLOCK and a fixed
  America/New_York (`test_schedule.py`'s own, because `zoneinfo` has no data
  here). Missed within grace is ONE run (the latest) and everything else ONE
  folded `skipped_missed`; busy waits, then skips, and identical skips fold;
  a removed project or recipe is a folded `skipped_unavailable`; history is
  capped at 50 and never evicts a live row; a restart repeats nothing.
* THE HUB, with a fake coding agent behind `harness_only`. A run's birth
  takes NOTHING from the owner (AC-461: current chat, its hold, the router
  memory, where a hands-free turn lands); a run is an ordinary typed turn
  that synthesizes zero audio with `speak` on and is titled from its origin
  (AC-462); a busy project never stops the owner (AC-460); a question makes
  the run `needs_you` and an answer completes it (AC-463); the tick never
  waits on a run (AC-458).
* THE ROUTES, driven through the real app.

No test sleeps to a wall-clock minute (section 11): every tick is called by
hand at an instant the test chose.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session                          # noqa: E402
from test_library_voice import _harness_hub                   # noqa: E402
from test_schedule import EASTERN                             # noqa: E402
from daemon import automations as au                          # noqa: E402
from daemon import butlerd, ports                             # noqa: E402
from daemon import library as lib                             # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402

ROOT, CH = "s-root", "s-site"
TICK = timedelta(seconds=au.TICK_S)


def et(*args) -> datetime:
    """A wall time in New York, as an aware instant."""
    return datetime(*args, tzinfo=EASTERN)


def _store(tmp_path, rows=None, channels=()):
    rows = {ROOT, CH} if rows is None else rows
    library = lib.Library(tmp_path / "library", rows=lambda: set(rows),
                          root_id=lambda: ROOT)
    recipe = library.recipes.save(None, "Weekly review", CH,
                                  "Review since {since=Monday}.")
    store = au.AutomationStore(tmp_path / "library", library, EASTERN,
                               default_channels=lambda: list(channels))
    return store, library, recipe, rows


def _auto(store, recipe_, now, **kw):
    body = {"recipe": recipe_.id, "schedule": {"preset": "daily",
                                               "time": "09:00"}}
    body.update(kw)
    return store.save(body, now)


def never_busy(_pid):
    return None


# --------------------------------------------------------------------------- #
# saving (section 4)
# --------------------------------------------------------------------------- #
def test_save_computes_next_run_and_the_label(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 10, 0))
    assert a["project"] == CH and a["enabled"] is True
    assert a["graceMinutes"] == 60 and a["channels"] == []
    assert a["schedule"] == {"preset": {"preset": "daily", "time": "09:00"},
                             "cron": "0 9 * * *", "label": "Daily at 09:00"}
    assert au.parse_iso(a["nextRunAt"]) == et(2026, 9, 27, 9, 0)
    assert a["nextRunAt"].endswith("-04:00")        # the offset is visible


def test_a_new_automation_takes_the_default_channel(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path, channels=("owner",))
    a = _auto(store, recipe, et(2026, 9, 26, 10, 0))
    assert a["channels"] == ["owner"]
    b = _auto(store, recipe, et(2026, 9, 26, 10, 0), channels=[])
    assert b["channels"] == []                      # unticking persists
    c = store.save({"id": b["id"], "enabled": False}, et(2026, 9, 26, 10, 0))
    assert c["channels"] == []


@pytest.mark.parametrize("body,rule", [
    ({"recipe": "rcp-00000000"}, "no recipe"),
    ({"project": ROOT}, "belongs to one project"),
    ({"values": {"when": "x"}}, "no placeholder called when"),
    ({"schedule": {"cron": "61 9 * * *"}}, "schedule minute"),
    ({"schedule": {"cron": "0 9 31 2 *"}}, "no occurrence in 5 years"),
    ({"schedule": {"preset": "hourly", "time": "09:00"}}, "schedule preset"),
    ({"graceMinutes": 1441}, "graceMinutes"),
    ({"graceMinutes": True}, "graceMinutes"),
    ({"channels": ["Not Ok"]}, "channels"),
    ({"enabled": "yes"}, "enabled"),
    ({"precheck": "rm -rf"}, "keeps no prechecks"),
])
def test_every_refusal_names_its_rule(tmp_path, body, rule):
    store, _lib, recipe, _ = _store(tmp_path)
    with pytest.raises(lib.LibraryRefused, match=rule):
        _auto(store, recipe, et(2026, 9, 26, 10, 0), **body)
    assert store.automations() == []


def test_a_global_recipe_needs_a_project(tmp_path):
    store, library, _recipe, _ = _store(tmp_path)
    g = library.recipes.save(None, "Brief", lib.GLOBAL, "Brief me.")
    with pytest.raises(lib.LibraryRefused, match="not a project row"):
        store.save({"recipe": g.id, "schedule": {"cron": "0 7 * * *"}},
                   et(2026, 9, 26, 10))
    a = store.save({"recipe": g.id, "project": ROOT,
                    "schedule": {"cron": "30 7 * * MON-FRI"}},
                   et(2026, 9, 26, 10))
    assert a["schedule"]["preset"] == {"preset": "weekdays", "time": "07:30"}


def test_turning_one_back_on_does_not_catch_up_the_off_stretch(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 1, 10, 0), enabled=False)
    b = store.save({"id": a["id"], "enabled": True}, et(2026, 9, 26, 10, 0))
    assert au.parse_iso(b["nextRunAt"]) == et(2026, 9, 27, 9, 0)


# --------------------------------------------------------------------------- #
# THE DECISION (AC-457, AC-459, AC-460, AC-464, AC-465)
# --------------------------------------------------------------------------- #
def test_due_now_is_one_pending_run_and_next_advances(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    due, touched = store.decide(et(2026, 9, 26, 9, 0, 30), never_busy)
    assert [r["status"] for r in due] == ["pending"] and touched == {CH}
    assert au.parse_iso(due[0]["scheduledFor"]) == et(2026, 9, 26, 9, 0)
    assert au.parse_iso(store.get(a["id"])["nextRunAt"]) == \
        et(2026, 9, 27, 9, 0)


def test_a_restart_repeats_no_decided_occurrence(tmp_path):
    """AC-457: `nextRunAt` is on disk after every decision, so a second
    store over the same files -- a restart -- decides nothing twice: the
    same occurrence is never a second row. A row decided but never started
    is started again (still within grace), never lost."""
    store, library, recipe, _ = _store(tmp_path)
    _auto(store, recipe, et(2026, 9, 26, 8, 0))
    now = et(2026, 9, 26, 9, 1)
    [first] = store.decide(now, never_busy)[0]
    again = au.AutomationStore(tmp_path / "library", library, EASTERN)
    redo = again.decide(now + TICK, never_busy)[0]
    assert [r["id"] for r in redo] == [first["id"]]
    assert len(again.all_runs()) == 1
    assert again.decide(now + 2 * TICK, never_busy,
                        queued={first["id"]})[0] == []


def test_missed_within_grace_at_boot_is_exactly_one_run(tmp_path):
    """AC-459, the spec's Gherkin: due at 09:00, boots at 09:40."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 25, 10, 0))
    due, _ = store.decide(et(2026, 9, 26, 9, 40), never_busy)
    assert len(due) == 1
    assert au.parse_iso(due[0]["scheduledFor"]) == et(2026, 9, 26, 9, 0)
    assert [r["status"] for r in store.runs(a["id"])] == ["pending"]


def test_several_within_grace_are_ONE_run_the_latest_and_one_skip(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0),
              schedule={"cron": "*/15 9 * * *"})
    due, _ = store.decide(et(2026, 9, 26, 9, 40), never_busy)
    assert len(due) == 1
    assert au.parse_iso(due[0]["scheduledFor"]) == et(2026, 9, 26, 9, 30)
    skip = [r for r in store.runs(a["id"]) if r["status"] == "skipped_missed"]
    assert len(skip) == 1 and skip[0]["occurrenceCount"] == 2


def test_missed_beyond_grace_for_three_days_is_one_row_counting_3(tmp_path):
    """AC-459, the spec's Gherkin: the PC off for three days."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 22, 10, 0))
    due, _ = store.decide(et(2026, 9, 25, 12, 0), never_busy)
    assert due == []
    [row] = store.runs(a["id"])
    assert row["status"] == "skipped_missed"
    assert row["occurrenceCount"] == 3
    assert au.parse_iso(row["scheduledFor"]) == et(2026, 9, 23, 9, 0)
    assert au.parse_iso(row["lastOccurrenceAt"]) == et(2026, 9, 25, 9, 0)
    assert "60 minutes" in row["reason"]


def test_the_grace_boundary_is_grace_plus_two_ticks(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    _auto(store, recipe, et(2026, 9, 26, 8, 0), graceMinutes=0)
    edge = et(2026, 9, 26, 9, 0) + 2 * TICK
    assert len(store.decide(edge, never_busy)[0]) == 1
    store2, _l2, recipe2, _ = _store(tmp_path / "b")
    _auto(store2, recipe2, et(2026, 9, 26, 8, 0), graceMinutes=0)
    assert store2.decide(edge + timedelta(seconds=1), never_busy)[0] == []


def test_busy_waits_then_starts_when_the_slot_frees(tmp_path):
    """AC-460: waiting_busy, retried every tick, starts within grace."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    busy = {CH: "SITE already has an agent running there"}
    due, _ = store.decide(et(2026, 9, 26, 9, 0, 30), busy.get)
    assert due == []
    [row] = store.runs(a["id"])
    assert row["status"] == "waiting_busy" and "nothing was stopped" in \
        row["reason"]
    assert store.decide(et(2026, 9, 26, 9, 5), busy.get)[0] == []
    due, _ = store.decide(et(2026, 9, 26, 9, 10), never_busy)
    assert [r["id"] for r in due] == [row["id"]]
    assert store.runs(a["id"])[0]["status"] == "pending"


def test_busy_past_grace_every_day_is_one_skipped_busy_counting_3(tmp_path):
    """AC-460/AC-464, the spec's Gherkin: busy through each day's window."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 23, 8, 0))
    busy = {CH: "SITE already has an agent running there"}
    for day in (23, 24, 25):
        store.decide(et(2026, 9, day, 9, 0, 30), busy.get)
        store.decide(et(2026, 9, day, 10, 3), busy.get)
    rows = store.runs(a["id"])
    assert [r["status"] for r in rows] == ["skipped_busy"]
    assert rows[0]["occurrenceCount"] == 3
    assert "stayed busy for the whole 60-minute window" in rows[0]["reason"]


def test_a_removed_project_or_recipe_skips_unavailable_and_folds(tmp_path):
    """AC-465: the automation stays; each due occurrence is one folded
    skipped_unavailable naming which."""
    store, library, recipe, rows = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 23, 8, 0))
    rows.discard(CH)
    store.decide(et(2026, 9, 23, 9, 1), never_busy)
    store.decide(et(2026, 9, 24, 9, 1), never_busy)
    [row] = store.runs(a["id"])
    assert row["status"] == "skipped_unavailable"
    assert row["occurrenceCount"] == 2 and "project was removed" in \
        row["reason"]
    assert store.get(a["id"])["id"] == a["id"]
    rows.add(CH)
    library.recipes.delete(recipe.id)
    store.decide(et(2026, 9, 25, 9, 1), never_busy)
    assert "recipe was deleted" in store.runs(a["id"])[0]["reason"]


def test_history_holds_50_and_never_evicts_a_live_row(tmp_path):
    """AC-464."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    live = store.run_now(a["id"], et(2026, 9, 26, 8, 1))      # pending
    for i in range(60):
        run = store.run_now(a["id"], et(2026, 9, 26, 8, 2))
        store.update_run(run["id"], status="completed")
    store.decide(et(2026, 9, 26, 8, 3), never_busy, queued={live["id"]})
    rows = store.runs(a["id"])
    assert len(rows) == au.HISTORY_CAP
    assert live["id"] in [r["id"] for r in rows]
    assert max(r["n"] for r in rows) == 61                  # never reused


def test_manual_run_works_while_disabled(tmp_path):
    """AC-465: `trigger: manual`, allowed while disabled."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0), enabled=False)
    run = store.run_now(a["id"], et(2026, 9, 26, 8, 1))
    assert (run["status"], run["trigger"]) == ("pending", "manual")
    assert store.decide(et(2026, 9, 27, 9, 1), never_busy)[0] == []


def test_boot_fails_what_was_in_flight(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    run = store.run_now(a["id"], et(2026, 9, 26, 8, 1))
    store.update_run(run["id"], status="running")
    [changed] = store.recover(et(2026, 9, 26, 9, 0))
    assert changed["status"] == "failed" and "stopped" in changed["reason"]


def test_deleting_an_automation_takes_its_history(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    store.run_now(a["id"], et(2026, 9, 26, 8, 1))
    store.delete(a["id"])
    assert store.automations() == [] and store.all_runs() == []


def test_this_feature_never_stops_anything():
    """AC-460, at the source: no line of the automations module calls a
    stop, an abandon or a dispose -- the owner's work is never a cost a
    schedule may pay."""
    code = Path(au.__file__).read_text(encoding="utf-8")
    body = code.split('"""', 2)[2]            # past the module docstring
    for call in (r"\.stop_conversation\(", r"\.abandon\(", r"\.dispose\("):
        assert not re.search(call, body), call


# --------------------------------------------------------------------------- #
# THE HUB: the birth, the run, busy, approval, the tick (fake coding agent)
# --------------------------------------------------------------------------- #
def _project(hub, tmp_path, name="site", label="SITE"):
    folder = tmp_path / name
    folder.mkdir()
    hub.shortcuts.upsert(sid="", path=str(folder), label=label)
    return next(r.id for r in hub.shortcuts.all() if r.label == label)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def _with_clock(hub, at):
    clock = Clock(at)
    hub.automations.tz = EASTERN
    hub.scheduler.clock = clock
    return clock


async def _finish(hub):
    """Wait for every run watcher to record its ending."""
    for _ in range(600):
        if not hub.scheduler._tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("a run never ended")


@pytest.mark.asyncio
async def test_a_runs_birth_takes_nothing_from_the_owner(tmp_path,
                                                        monkeypatch):
    """AC-461: after a run's birth the owner's conversation is still the
    current one and still HELD, the router's memory is untouched, and a
    hands-free turn still lands in the owner's conversation."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    mine = (await hub.new_conversation(row))["id"]
    hub.history.append(("user", "what were we doing"))
    before = list(hub.history)
    born = await hub.new_conversation(row, focus=False,
                                      origin={"kind": "automation",
                                              "title": "Weekly - Fri"})
    assert born["id"] != mine
    assert hub.current_chat_id() == mine
    assert hub.brain.host._chats.is_open(mine)
    assert hub.brain.host._chats.is_open(born["id"])
    assert list(hub.history) == before
    s = attach_session(hub)                   # hands-free: shows nothing
    assert hub.chat_for(s) == mine


@pytest.mark.asyncio
async def test_a_scheduled_run_is_a_silent_typed_turn_in_its_own_chat(
        tmp_path, monkeypatch):
    """AC-462 and the spec's first Gherkin: due at 09:00, the clock reaches
    it, a chat titled "<recipe> - <date>" exists in the project with the
    filled text as its first message, the run completes, and the owner's
    current conversation and router memory are unchanged. With `speak` ON,
    zero audio is synthesized."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    recipe = hub.library.recipes.save(None, "Weekly review", row,
                                      "Review since {since=Monday}.")
    owner = (await hub.new_conversation(None))["id"]
    hub.history.append(("user", "keep this"))
    memory = list(hub.history)
    said = []
    real = hub.tts.synthesize

    def spy(text, *a, **kw):
        said.append(text)
        return real(text, *a, **kw)
    monkeypatch.setattr(hub.tts, "synthesize", spy)
    assert hub.settings.get("speak", True) is True
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "weekly", "time": "09:00", "days": ["MON"]}}, clock())
    clock.at = et(2026, 9, 28, 9, 0, 20)
    [run] = await hub.scheduler.tick()
    await hub.scheduler.dispatch(run)
    await _finish(hub)
    [done] = hub.automations.runs(a["id"])
    assert done["status"] == "completed", done
    assert done["summary"] and done["chat"]
    record = await asyncio.to_thread(hub.brain.session_chat, done["chat"])
    assert record["title"] == "Weekly review - Mon 28 Sep 09:00"
    assert record["project"] == str(tmp_path / "site")
    # A new automation has silence on, so the one footer follows the one
    # expansion (docs/proactive.md PR-2, AC-477).
    assert [m["content"] for m in record["messages"]
            if m["role"] == "user"] == ["Review since Monday.\n\n"
                                        + au.SILENCE_FOOTER]
    rows = await asyncio.to_thread(hub.brain.session_list)
    listed = next(r for r in rows if r["id"] == done["chat"])
    assert listed["origin"]["kind"] == "automation"
    assert listed["origin"]["run"] == done["id"]
    assert hub.current_chat_id() == owner
    assert list(hub.history) == memory
    assert said == []
    assert not any(isinstance(s, hub_mod.RunSession) for s in hub.sessions)


@pytest.mark.asyncio
async def test_a_busy_project_never_stops_the_owner(tmp_path, monkeypatch):
    """AC-460: the owner's agent in SITE is working (blocked on its own
    question, so it stays busy); the run waits, the owner's turn keeps
    running, and past grace the run is skipped_busy. Nothing is stopped."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    recipe = hub.library.recipes.save(None, "Weekly review", row, "Review.")
    ask = ports.Ask(qid="q-owner", method="confirm", message="Go on?")
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ask)
    stopped = []

    async def no_stop(chat_id):
        stopped.append(chat_id)
    monkeypatch.setattr(hub, "stop_conversation", no_stop)
    real_abandon = hub_mod.SatelliteSession.abandon

    async def spy_abandon(self, turn=None):
        stopped.append(("abandon", turn))
        await real_abandon(self, turn)
    monkeypatch.setattr(hub_mod.SatelliteSession, "abandon", spy_abandon)
    owner = attach_session(hub)
    owner.showing = (await hub.new_conversation(row))["id"]
    await hub._typed(owner, {"type": "typed", "text": "tidy up"})
    for _ in range(300):
        if hub.status_for(owner.showing) == "waiting":
            break
        await asyncio.sleep(0.01)
    assert hub.status_for(owner.showing) == "waiting"
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, clock())
    clock.at = et(2026, 9, 28, 9, 0, 20)
    assert await hub.scheduler.tick() == []
    assert hub.automations.runs(a["id"])[0]["status"] == "waiting_busy"
    clock.at = et(2026, 9, 28, 10, 3)
    await hub.scheduler.tick()
    [row_] = hub.automations.runs(a["id"])
    assert row_["status"] == "skipped_busy"
    assert hub.status_for(owner.showing) == "waiting"      # still working
    assert stopped == []
    await hub.answer_question("q-owner", ports.Answer(confirmed=True))


@pytest.mark.asyncio
async def test_a_run_that_asks_needs_you_and_an_answer_completes_it(
        tmp_path, monkeypatch):
    """AC-463: the run's agent asks; the run is needs_you, the question is
    broadcast to every page as today, and answering lets it complete."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    recipe = hub.library.recipes.save(None, "Tidy", row, "Tidy the drafts.")
    ask = ports.Ask(qid="q-run", method="confirm",
                    message="Delete 40 old drafts?")
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ask)
    page = attach_session(hub)
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, clock())
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if hub.automations.runs(a["id"])[0]["status"] == "needs_you":
            break
        await asyncio.sleep(0.01)
    row_ = hub.automations.runs(a["id"])[0]
    assert row_["status"] == "needs_you" and row_["neededYou"] is True
    questions = [json.loads(m) for m in page.ws.sent if isinstance(m, str)
                 and json.loads(m).get("type") == "question"]
    assert questions and questions[-1]["chat"] == row_["chat"]
    got = await hub.answer_question("q-run", ports.Answer(confirmed=False))
    assert got["ok"]
    await _finish(hub)
    done = hub.automations.runs(a["id"])[0]
    assert done["status"] == "completed" and done["neededYou"] is True
    assert done["trigger"] == "manual"


@pytest.mark.asyncio
async def test_the_busy_check_agrees_with_the_agents_own_decision(
        tmp_path, monkeypatch):
    """`_Host.refusal_for` is a READ-ONLY copy of what `plan()` decides for
    a new conversation (D-18): driven through the same four states -- an
    empty project, a busy agent there, an idle one there (evicted, D1=A),
    and the global cap full -- the two must agree every time."""
    from daemon.adapters.brain_harness import CapReached
    hub = _harness_hub(tmp_path, monkeypatch)
    home = _project(hub, tmp_path)
    away = _project(hub, tmp_path, "owner", "OWNER")
    host = hub.brain.host
    hub.settings.set("speak", False)      # no playback gate to sit out
    ask = ports.Ask(qid="q-busy", method="confirm", message="Go on?")
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ask)

    async def agree(row_id):
        path = next(r.path for r in hub.shortcuts.all() if r.id == row_id)
        said = host.refusal_for(path)
        chat = (await hub.new_conversation(row_id, focus=False))["id"]
        try:
            await host.plan(host.chat_for_turn(chat))
            decided = None
        except CapReached as e:
            decided = str(e)
        host._release_project_claim(chat)
        assert said == decided, (said, decided)
        return said

    assert await agree(home) is None                         # empty
    owner = attach_session(hub)
    owner.showing = (await hub.new_conversation(home))["id"]
    await hub._typed(owner, {"type": "typed", "text": "work"})
    for _ in range(300):
        if hub.status_for(owner.showing) == "waiting":
            break
        await asyncio.sleep(0.01)
    assert "already has an agent" in await agree(home)       # busy
    await hub.answer_question("q-busy", ports.Answer(confirmed=True))
    for _ in range(300):
        if hub.status_for(owner.showing) == "done":
            break
        await asyncio.sleep(0.01)
    assert hub.status_for(owner.showing) == "done"
    hub.settings.set("harness_agents_max", 1)
    assert "agents are running" in await agree(away)         # cap full
    assert await agree(home) is None                         # idle: evicted


@pytest.mark.asyncio
async def test_a_run_and_the_owner_never_interrupt_each_other(tmp_path,
                                                             monkeypatch):
    """AC-460 and D-18's caller half: a run that STARTS while the owner's
    turn in another project is in flight does not barge in on it, and the
    owner typing somewhere else afterwards does not barge in on the run. A
    run is headless: it is not in the room, so the room's rules skip it."""
    hub = _harness_hub(tmp_path, monkeypatch)
    home = _project(hub, tmp_path)
    work = _project(hub, tmp_path, "owner", "OWNER")
    recipe = hub.library.recipes.save(None, "Tidy", work, "Tidy up.")
    counter = iter(range(100))
    # Every child asks with its OWN qid, so several can be open at once.
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", ask=ports.Ask(
            qid=f"q-{next(counter)}", method="confirm", message="Go on?"))
    owner = attach_session(hub)
    owner.showing = (await hub.new_conversation(home))["id"]
    await hub._typed(owner, {"type": "typed", "text": "look around"})
    for _ in range(300):
        if hub.status_for(owner.showing) == "waiting":
            break
        await asyncio.sleep(0.01)
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, clock())
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if hub.automations.runs(a["id"])[0]["status"] == "needs_you":
            break
        await asyncio.sleep(0.01)
    assert hub.status_for(owner.showing) == "waiting", \
        "starting a run barged in on the owner's turn"
    chat = hub.automations.runs(a["id"])[0]["chat"]
    other = attach_session(hub, name="phone")
    other.showing = (await hub.new_conversation(None))["id"]
    await hub._typed(other, {"type": "typed", "text": "hello"})
    await asyncio.sleep(0.2)
    assert hub.status_for(chat) == "waiting", \
        "the owner typing elsewhere barged in on a scheduled run"
    for qid in list(hub._questions):
        await hub.answer_question(qid, ports.Answer(confirmed=True))
    await _finish(hub)


@pytest.mark.asyncio
async def test_the_tick_never_waits_on_a_run(tmp_path, monkeypatch):
    """AC-458: a dispatch that HANGS does not delay another automation's
    decision. The runner's birth is made to block forever; a later tick
    still decides the second automation."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    other = _project(hub, tmp_path, "owner", "OWNER")
    r1 = hub.library.recipes.save(None, "One", row, "One.")
    r2 = hub.library.recipes.save(None, "Two", other, "Two.")
    hang = asyncio.Event()

    async def blocked(*_a, **_kw):
        await hang.wait()
    monkeypatch.setattr(hub, "new_conversation", blocked)
    clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
    hub.scheduler.tick_s = 0.05
    a1 = hub.automations.save({"recipe": r1.id, "schedule": {
        "cron": "0 9 * * *"}}, clock())
    a2 = hub.automations.save({"recipe": r2.id, "schedule": {
        "cron": "5 9 * * *"}}, clock())
    await hub.scheduler.start()
    try:
        clock.at = et(2026, 9, 28, 9, 0, 20)
        for _ in range(100):
            if hub.automations.runs(a1["id"]):
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)             # the dispatch is now hung
        clock.at = et(2026, 9, 28, 9, 5, 20)
        for _ in range(100):
            if hub.automations.runs(a2["id"]):
                break
            await asyncio.sleep(0.02)
        assert hub.automations.runs(a2["id"]), \
            "a hung dispatch delayed another automation's decision"
        assert au.parse_iso(hub.automations.get(a2["id"])["nextRunAt"]) == \
            et(2026, 9, 29, 9, 5)
    finally:
        hang.set()
        await hub.scheduler.stop()


# --------------------------------------------------------------------------- #
# the routes (section 5)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_routes(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    recipe = hub.library.recipes.save(None, "Weekly review", row,
                                      "Review since {since=Monday}.")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    frames = []

    async def spy(**payload):
        frames.append(payload)
    monkeypatch.setattr(hub, "broadcast", spy)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        r = await client.post("/api/schedule/preview",
                              json={"cron": "0 8 * * TUE#1"})
        body = await r.json()
        assert r.status == 200, body
        assert body["preset"] == {"preset": "monthly", "nth": 1,
                                  "day": "TUE", "time": "08:00"}
        assert len(body["next"]) == 5
        r = await client.post("/api/schedule/preview",
                              json={"cron": "0 8 * * TUE#6"})
        assert r.status == 400 and "day of week" in (await r.json())["error"]
        r = await client.post("/api/automations", json={
            "recipe": recipe.id, "values": {"since": "Friday"},
            "schedule": {"preset": "daily", "time": "09:00"}})
        a = await r.json()
        assert r.status == 200, a
        assert a["recipeName"] == "Weekly review" and a["last"] is None
        assert frames[-1] == {"type": "library", "what": "automations",
                              "project": row}
        r = await client.get("/api/automations")
        assert [x["id"] for x in (await r.json())["automations"]] == [a["id"]]
        r = await client.get(f"/api/library?project={row}")
        panel = (await r.json())["automations"]
        assert [x["id"] for x in panel["project"]] == [a["id"]]
        r = await client.delete(f"/api/recipes/{recipe.id}")
        body = await r.json()
        assert r.status == 409 and body["automations"] == [a["id"]]
        r = await client.post("/api/automations/bulk",
                              json={"ids": [a["id"]], "enabled": False})
        assert r.status == 200
        assert hub.automations.get(a["id"])["enabled"] is False
        r = await client.post("/api/automations/bulk",
                              json={"ids": ["aut-nobody"], "enabled": True})
        assert r.status == 404
        monkeypatch.setattr(hub.scheduler, "enqueue", lambda run: None)
        r = await client.post(f"/api/automations/{a['id']}/run")
        run = await r.json()
        assert r.status == 200 and run["trigger"] == "manual"
        r = await client.get(f"/api/automations/{a['id']}/runs")
        assert [x["id"] for x in (await r.json())["runs"]] == [run["id"]]
        r = await client.post("/api/automations", json={
            "recipe": recipe.id, "schedule": {"cron": "nope"}})
        assert r.status == 400
        r = await client.delete(f"/api/automations/{a['id']}")
        assert r.status == 200 and hub.automations.all_runs() == []
        r = await client.delete(f"/api/recipes/{recipe.id}")
        assert r.status == 200
    finally:
        await client.close()
