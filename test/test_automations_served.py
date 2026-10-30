"""What the automation routes SERVE for the simpler page (docs/lantern-ui.md
section 8, Step 6): `nextWords`, a run's `statusWord` and `group`, and the
heartbeat's `awakeWords`. The page prints them verbatim and words no date,
status or setting itself (AC-41).

What each test would catch:

* `nextWords` built from `when_words` and the schedule label, on every row
  route and the preview answer, with the clock the scheduler uses -- an
  injected one here, so "tomorrow" is a fact of the test, not of today.
* A heartbeat's next check skips quiet hours, because none runs there
  (PR-11): "10:00 PM today" inside a 22:00-08:00 window would be a lie.
* Every run status maps to exactly one word and one group, the statuses
  read from the spec's own table rather than a list written here.
"""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import TestClient, TestServer         # noqa: E402

from test_automations import _project, _with_clock, et, _store  # noqa: E402
from test_library_voice import _harness_hub                   # noqa: E402
from daemon import automations as au                          # noqa: E402
from daemon import butlerd                                    # noqa: E402

REPO = Path(__file__).resolve().parents[1]
GROUPS = {"ok", "failed", "needs", "skipped", "pending"}
# Tuesday 28 Oct 2031, 10:00 in New York: far from any real "today", so a
# route that ignored the injected clock could not say "tomorrow" by luck.
TUESDAY = et(2031, 10, 28, 10, 0)


async def _client(hub, monkeypatch):
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    async def quiet(**_payload):
        pass
    monkeypatch.setattr(hub, "broadcast", quiet)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    return client


# --------------------------------------------------------------------------- #
# nextWords
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_next_words_on_every_row_route_and_the_preview(
        tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, TUESDAY)
    recipe = hub.library.recipes.save(None, "Brief", row, "Brief me.")
    client = await _client(hub, monkeypatch)
    want = "Next: 7:00 AM tomorrow, then weekdays at 07:00"
    try:
        weekdays = {"preset": "weekdays", "time": "07:00"}
        r = await client.post("/api/schedule/preview", json=weekdays)
        assert (await r.json())["nextWords"] == want
        r = await client.post("/api/automations", json={
            "recipe": recipe.id, "schedule": weekdays})
        a = await r.json()
        assert r.status == 200 and a["nextWords"] == want
        [listed] = (await (await client.get("/api/automations")).json())[
            "automations"]
        assert listed["nextWords"] == want
        panel = (await (await client.get(
            f"/api/library?project={row}")).json())["automations"]
        assert [x["nextWords"] for x in panel["project"]] == [want]
        r = await client.post("/api/automations/bulk",
                              json={"ids": [a["id"]], "enabled": False})
        assert r.status == 200
        [listed] = (await (await client.get("/api/automations")).json())[
            "automations"]
        assert listed["nextWords"] == "Turned off"
    finally:
        await client.close()


@pytest.mark.parametrize("schedule,want", [
    ({"preset": "daily", "time": "18:30"},
     "Next: 6:30 PM today, then daily at 18:30"),
    ({"preset": "weekly", "time": "09:00", "days": ["FRI"]},
     "Next: 9:00 AM on Fri 31 Oct, then every Friday at 09:00"),
    ({"preset": "monthly", "nth": 1, "day": "TUE", "time": "08:00"},
     "Next: 8:00 AM on Tue 4 Nov, then monthly on the 1st Tuesday at 08:00"),
    ({"cron": "15 10 1 * *"},
     "Next: 10:15 AM on Sat 1 Nov, then on a custom schedule"),
])
def test_next_words_reads_each_kind_of_label(tmp_path, schedule, want):
    store, _lib, recipe, _ = _store(tmp_path)
    a = store.save({"recipe": recipe.id, "schedule": schedule}, TUESDAY)
    assert store.public(a, now=TUESDAY)["nextWords"] == want


def test_next_words_for_a_reminder_and_an_event(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    once = store.save({"kind": "reminder", "text": "Call mum",
                       "runAt": "2031-10-28T17:00"}, TUESDAY)
    assert store.public(once, now=TUESDAY)["nextWords"] == \
        "Next: 5:00 PM today"
    store.sources = lambda: {"inbox"}
    ev = store.save({"recipe": recipe.id, "source": "inbox"}, TUESDAY)
    assert store.public(ev, now=TUESDAY)["nextWords"] == "When inbox fires"
    done = {**once, "enabled": False, "doneAt": au.iso(TUESDAY, store.tz)}
    assert store.public(done, now=TUESDAY)["nextWords"] == "Done"


# --------------------------------------------------------------------------- #
# the heartbeat: quiet hours, and awakeWords
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_heartbeat_row_words_quiet_hours(tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, et(2031, 10, 28, 21, 30))
    hub.settings.set("quiet_start", "22:00")
    hub.settings.set("quiet_end", "08:00")
    hub.settings.set("quiet_hours", True)
    client = await _client(hub, monkeypatch)
    try:
        r = await client.post("/api/heartbeat", json={
            "project": row, "checklist": "- invoices?", "minutes": 60})
        saved = await r.json()
        assert r.status == 200, saved
        # 22:00 falls inside quiet hours, where no check runs (PR-11).
        assert saved["nextWords"] == \
            "Next: 8:00 AM tomorrow, then every 60 minutes"
        assert saved["awakeWords"] == "while you are awake"

        hub.settings.set("quiet_hours", False)
        beat = (await (await client.get(
            f"/api/library?project={row}")).json())["automations"]["heartbeat"]
        assert beat["nextWords"] == \
            "Next: 10:00 PM today, then every 60 minutes"
        assert beat["awakeWords"] == ""
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# statusWord and group
# --------------------------------------------------------------------------- #
def _spec_statuses() -> set[str]:
    """The `status` row of docs/automations.md's runs table."""
    text = (REPO / "docs" / "automations.md").read_text(encoding="utf-8")
    [line] = [x for x in text.splitlines() if x.startswith("| `status` |")]
    cell = line.split("|")[2]
    return set(re.findall(r"`([a-z_]+)`", cell))


def _group_of(status: str) -> str:
    if status == "completed":
        return "ok"
    if status == "needs_you":
        return "needs"
    if status in ("failed", "stopped"):
        return "failed"
    if status in au.SKIPS:
        return "skipped"
    assert status in au.WAITING | {"running"}, status
    return "pending"


def test_every_status_has_one_word_and_one_group():
    statuses = _spec_statuses()
    assert len(statuses) == 12, sorted(statuses)
    assert au.STATUSES == statuses
    assert set(au.WORDS) == statuses == set(au.GROUP)
    assert set(au.GROUP.values()) == GROUPS
    for s in statuses:
        assert au.GROUP[s] == _group_of(s), s
        assert au.WORDS[s] and au.WORDS[s].isascii(), s


@pytest.mark.asyncio
async def test_every_served_run_carries_its_word_and_group(
        tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, TUESDAY)
    recipe = hub.library.recipes.save(None, "Brief", row, "Brief me.")
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, TUESDAY)
    statuses = sorted(au.STATUSES)
    runs = [{"id": f"run-{i}", "automation": a["id"], "n": i + 1,
             "trigger": "manual", "scheduledFor": a["nextRunAt"],
             "status": s, "reason": "", "chat": "", "startedAt": "",
             "endedAt": "", "seenAt": "", "summary": ""}
            for i, s in enumerate(statuses)]
    # The scheduler would settle the live rows before the read: boot
    # `recover()` fails the running ones and the first tick starts the
    # waiting one. Neither is under test here.
    async def no_start():
        pass
    monkeypatch.setattr(hub.scheduler, "start", no_start)
    hub.automations._write(hub.automations._runs_path, runs)
    client = await _client(hub, monkeypatch)
    try:
        served = (await (await client.get(
            f"/api/automations/{a['id']}/runs")).json())["runs"]
        assert {r["status"] for r in served} == au.STATUSES
        for run in served:
            assert run["statusWord"] == au.WORDS[run["status"]]
            assert run["group"] == _group_of(run["status"])
        [listed] = (await (await client.get("/api/automations")).json())[
            "automations"]
        last = statuses[-1]
        assert (listed["last"]["status"], listed["last"]["statusWord"],
                listed["last"]["group"]) == (last, au.WORDS[last],
                                             _group_of(last))
        # The stored history is untouched: the words are served, not kept.
        stored = json.loads(hub.automations._runs_path.read_text(
            encoding="utf-8"))
        assert all("statusWord" not in r and "group" not in r
                   for r in stored)
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# Step 6b: the times on the full page are the daemon's words too
# --------------------------------------------------------------------------- #
def test_the_house_wording_reads_the_past_day_and_a_duration():
    """`schedule.when_words` is the ONE wording of an instant (read-back,
    `nextWords`, and since Step 6b a run's and a task's time); "yesterday"
    is its past-day word. `took_words` is m:ss, h:mm:ss past an hour."""
    from daemon import schedule as sched
    now, tz = TUESDAY, TUESDAY.tzinfo
    assert sched.when_words(et(2031, 10, 28, 15, 6), now, tz) ==         "3:06 PM today"
    assert sched.when_words(et(2031, 10, 27, 9, 12), now, tz) == \
        "9:12 AM yesterday"
    assert sched.when_words(et(2031, 10, 24, 7, 0), now, tz) == \
        "7:00 AM on Fri 24 Oct"
    assert sched.when_words(et(2031, 10, 29, 9, 5), now, tz) == \
        "9:05 AM tomorrow"
    start = et(2031, 10, 28, 7, 0)
    assert sched.took_words(start, start + timedelta(seconds=151)) == "2:31"
    assert sched.took_words(start, start + timedelta(seconds=42)) == "0:42"
    assert sched.took_words(start, start + timedelta(seconds=3726)) == \
        "1:02:06"
    assert sched.took_words(start, start) == "0:00"


def _run(aid, n, status, scheduled, **kw):
    row = {"id": f"run-{n}", "automation": aid, "n": n, "trigger": "scheduled",
           "scheduledFor": au.iso(scheduled, TUESDAY.tzinfo), "status": status,
           "reason": "", "chat": "", "startedAt": "", "endedAt": "",
           "seenAt": "", "summary": "", "neededYou": False}
    row.update(kw)
    return row


@pytest.mark.asyncio
async def test_a_served_run_says_when_it_ran_and_how_long_it_took(
        tmp_path, monkeypatch):
    """D-18: asserted on the ROUTES the page reads (`/runs` and the row's
    `last`), with the scheduler's injected clock and zone."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, TUESDAY)
    tz = hub.automations.tz
    recipe = hub.library.recipes.save(None, "Brief", row, "Brief me.")
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, TUESDAY)
    older, yesterday = et(2031, 10, 21, 7, 0), et(2031, 10, 27, 9, 12)
    today = et(2031, 10, 28, 9, 0)
    runs = [
        _run(a["id"], 1, "skipped_missed", older, endedAt=au.iso(older, tz),
             occurrenceCount=3,
             lastOccurrenceAt=au.iso(et(2031, 10, 23, 7, 0), tz)),
        _run(a["id"], 2, "failed", yesterday,
             startedAt=au.iso(yesterday, tz),
             endedAt=au.iso(yesterday + timedelta(seconds=750), tz)),
        _run(a["id"], 3, "completed", today, startedAt=au.iso(today, tz),
             endedAt=au.iso(today + timedelta(seconds=151), tz)),
    ]

    async def no_start():
        pass
    monkeypatch.setattr(hub.scheduler, "start", no_start)
    hub.automations._write(hub.automations._runs_path, runs)
    client = await _client(hub, monkeypatch)
    try:
        served = {r["n"]: r for r in (await (await client.get(
            f"/api/automations/{a['id']}/runs")).json())["runs"]}
        assert served[3]["whenWords"] == "9:00 AM today"
        assert served[3]["tookWords"] == "2:31"
        assert served[2]["whenWords"] == "9:12 AM yesterday"
        assert served[2]["tookWords"] == "12:30"
        assert served[1]["whenWords"] == "7:00 AM on Tue 21 Oct"
        # A skip never started: no duration, and the fold's last time.
        assert served[1]["tookWords"] == ""
        assert served[1]["lastOccurrenceWords"] == "7:00 AM on Thu 23 Oct"
        assert served[3]["lastOccurrenceWords"] == ""
        [listed] = (await (await client.get("/api/automations")).json())[
            "automations"]
        assert (listed["last"]["whenWords"], listed["last"]["tookWords"]) \
            == ("9:00 AM today", "2:31")
        stored = json.loads(hub.automations._runs_path.read_text(
            encoding="utf-8"))
        assert all("whenWords" not in r and "tookWords" not in r
                   for r in stored)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_served_task_says_when_it_was_added_and_done(
        tmp_path, monkeypatch):
    """`addedWords` on every task and `doneWords` on a done one, on both
    routes that serve tasks (`/api/tasks` for the full page, `/api/library`
    for the panel); the stored list keeps only its ISO times."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, TUESDAY)
    tz = hub.automations.tz
    new = hub.library.tasks.add(row, "Retest the header")
    old = hub.library.tasks.add(row, "Renew the token")
    hub.library.tasks.edit(old["id"], done=True)
    stored = json.loads(hub.library.tasks.path.read_text(encoding="utf-8"))
    for t in stored:
        if t["id"] == new["id"]:
            t["createdAt"] = au.iso(et(2031, 10, 28, 15, 6), tz)
        else:
            t["createdAt"] = au.iso(et(2031, 10, 21, 8, 0), tz)
            t["doneAt"] = au.iso(et(2031, 10, 27, 9, 12), tz)
    hub.library.tasks.path.write_text(json.dumps(stored), encoding="utf-8")
    client = await _client(hub, monkeypatch)
    try:
        listed = {t["id"]: t for t in (await (await client.get(
            "/api/tasks")).json())["tasks"]}
        assert listed[new["id"]]["addedWords"] == "3:06 PM today"
        assert listed[new["id"]]["doneWords"] == ""
        assert listed[old["id"]]["addedWords"] == "8:00 AM on Tue 21 Oct"
        assert listed[old["id"]]["doneWords"] == "9:12 AM yesterday"
        panel = (await (await client.get(
            f"/api/library?project={row}")).json())["tasks"]["project"]
        assert [t["addedWords"] for t in panel["open"]] == ["3:06 PM today"]
        assert [t["doneWords"] for t in panel["done"]] == \
            ["9:12 AM yesterday"]
        kept = json.loads(hub.library.tasks.path.read_text(encoding="utf-8"))
        assert all("addedWords" not in t for t in kept)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_reminder_editor_starts_from_served_input_values(
        tmp_path, monkeypatch):
    """The reminder editor's `<input type=date/time>` VALUES are the
    daemon's (Step 6b: the page reads no clock): an hour from now for a new
    one (`remindInput`), the stored time for a one-shot (`runAtInput`), both
    in the zone the daemon reads `runAt` in."""
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    _with_clock(hub, et(2031, 10, 28, 23, 30))
    client = await _client(hub, monkeypatch)
    try:
        r = await client.post("/api/automations", json={
            "kind": "reminder", "project": row, "text": "Call mum",
            "runAt": "2031-10-29T17:05"})
        assert r.status == 200, await r.json()
        autos = (await (await client.get(
            f"/api/library?project={row}")).json())["automations"]
        assert autos["remindInput"] == {"date": "2031-10-29", "time": "00:30"}
        [once] = autos["project"]
        assert once["runAtInput"] == {"date": "2031-10-29", "time": "17:05"}
    finally:
        await client.close()
