"""Proactive P2: quiet hours and held speech.
docs/proactive.md section 4.2 (PR-14..PR-16), AC-485..AC-487.

* THE WINDOW (AC-485): `schedule.in_quiet` is pure (now and zone are
  arguments), wraps midnight, and a start equal to its end is refused -- in
  the pure check and at the settings write, where the old value survives.
* HELD, NOT SPOKEN (AC-486): inside quiet hours an automation's result line
  goes to `Paths.held_speech` instead of the desk; its channel message is
  sent at once (the phone has its own do-not-disturb). Anything else that
  speaks -- a reply, a reminder through `Hub.announce` -- is untouched. The
  failure this catches is a desk talking at 03:00.
* THE MORNING LINE (AC-487): after `quiet_end`, at the first tick that finds
  the desk connected, idle and speaking, Butler says ONE line naming at most
  5 held items and counting the rest, the expired and the dropped; the file
  is then empty. A restart in between loses nothing. At most 50 are held;
  older than 24 h expire unsaid.

Every instant is injected (`Clock`); nothing sleeps to a wall-clock minute.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_automations import _finish, et                        # noqa: E402
from test_automations_results import (_device, _run, _setup,     # noqa: E402
                                      _types)
from test_notify import FakeBotApi, _write                      # noqa: E402
from test_schedule import EASTERN                               # noqa: E402
from daemon import automations as au                            # noqa: E402
from daemon import hub as hub_mod                               # noqa: E402
from daemon import paths as paths_mod                           # noqa: E402
from daemon import ports                                        # noqa: E402
from daemon import schedule as sched                            # noqa: E402
from daemon import settings as settings_mod                     # noqa: E402
from daemon.adapters import notify_telegram                     # noqa: E402


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
# AC-485: the pure window
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wall,quiet", [
    ((2026, 10, 7, 21, 59), False), ((2026, 10, 7, 22, 0), True),
    ((2026, 10, 7, 23, 30), True), ((2026, 10, 8, 3, 0), True),
    ((2026, 10, 8, 7, 59), True), ((2026, 10, 8, 8, 0), False),
    ((2026, 10, 8, 14, 0), False),
])
def test_the_window_wraps_midnight(wall, quiet):
    # The instant arrives in UTC and the window is read in the zone given.
    now = et(*wall).astimezone(sched.UTC)
    assert sched.in_quiet(now, EASTERN, "22:00", "08:00") is quiet


@pytest.mark.parametrize("wall,quiet", [
    ((2026, 10, 7, 12, 59), False), ((2026, 10, 7, 13, 0), True),
    ((2026, 10, 7, 13, 59), True), ((2026, 10, 7, 14, 0), False),
])
def test_a_daytime_window_does_not_wrap(wall, quiet):
    assert sched.in_quiet(et(*wall), EASTERN, "13:00", "14:00") is quiet


def test_the_zone_decides_not_the_instant():
    """Same instant, two zones: 03:00 in New York is 07:00 UTC."""
    now = et(2026, 10, 8, 3, 0)
    assert sched.in_quiet(now, EASTERN, "22:00", "08:00") is True
    assert sched.in_quiet(now, sched.UTC, "22:00", "06:00") is False


@pytest.mark.parametrize("start,end,field", [
    ("22:00", "22:00", "quiet"), ("8:00", "22:00", "time"),
    ("24:00", "08:00", "time"), ("22:00", "", "time"),
])
def test_a_bad_window_is_refused_naming_why(start, end, field):
    with pytest.raises(sched.ScheduleError) as got:
        sched.in_quiet(et(2026, 10, 8, 3), EASTERN, start, end)
    assert got.value.field == field


# --------------------------------------------------------------------------- #
# PR-14: the settings, validated at write
# --------------------------------------------------------------------------- #
def test_the_settings_and_their_defaults(tmp_path):
    s = settings_mod.Settings(tmp_path / "s.json")
    assert s.get("quiet_hours") is True
    assert s.get("quiet_start") == "22:00" and s.get("quiet_end") == "08:00"
    for key in ("quiet_hours", "quiet_start", "quiet_end"):
        spec = settings_mod.BY_KEY[key]
        assert spec.group == "quiet" and not spec.restart, key
    assert settings_mod.BY_KEY["quiet_start"].kind == "text"


@pytest.mark.parametrize("value", ["25:00", "8:00", "08:0", "noon", " "])
def test_a_time_that_is_not_hh_mm_is_refused_and_the_old_one_kept(
        tmp_path, value):
    s = settings_mod.Settings(tmp_path / "s.json")
    with pytest.raises(settings_mod.SettingsError, match="HH:MM"):
        s.set("quiet_start", value)
    assert s.get("quiet_start") == "22:00"


def test_start_equal_to_end_is_refused_at_write(tmp_path):
    s = settings_mod.Settings(tmp_path / "s.json")
    with pytest.raises(settings_mod.SettingsError,
                       match="cannot start and end at the same time"):
        s.set("quiet_end", "22:00")
    assert s.get("quiet_end") == "08:00"
    with pytest.raises(settings_mod.SettingsError, match="same time"):
        s.update({"quiet_start": "07:00", "quiet_end": "07:00"})
    assert (s.get("quiet_start"), s.get("quiet_end")) == ("22:00", "08:00")
    # Moving both at once is one write and is checked as one.
    s.update({"quiet_start": "08:00", "quiet_end": "22:00"})
    assert (s.get("quiet_start"), s.get("quiet_end")) == ("08:00", "22:00")


# --------------------------------------------------------------------------- #
# PR-16: the held file
# --------------------------------------------------------------------------- #
def test_the_held_file_lives_in_the_records(tmp_path, monkeypatch):
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    where = paths_mod.resolve()
    assert where.held_speech == tmp_path / "held-speech.json"
    assert where.heartbeat == tmp_path / "heartbeat"


def _held(tmp_path):
    return au.HeldSpeech(tmp_path / "held-speech.json")


def test_the_morning_line_names_them_in_order(tmp_path):
    held = _held(tmp_path)
    t = et(2026, 10, 7, 23, 0)
    for i, text in enumerate(("Build watch finished", "Inbox digest finished",
                              "Backup check failed")):
        held.hold(text, t + timedelta(minutes=i))
    line, _token = held.morning(et(2026, 10, 8, 8, 5))
    assert line == ("While you were away: Build watch finished. Inbox digest "
                    "finished. Backup check failed.")


def test_at_most_five_are_named_and_the_rest_counted(tmp_path):
    held = _held(tmp_path)
    t = et(2026, 10, 7, 23, 0)
    for i in range(12):
        held.hold(f"Job {i} finished", t + timedelta(minutes=i))
    line, _token = held.morning(et(2026, 10, 8, 8, 5))
    assert line == ("While you were away: Job 0 finished. Job 1 finished. "
                    "Job 2 finished. Job 3 finished. Job 4 finished. "
                    "And 7 more in your chats.")


def test_fifty_are_held_and_the_51st_drops_the_oldest_counted(tmp_path):
    held = _held(tmp_path)
    t = et(2026, 10, 7, 23, 0)
    for i in range(51):
        held.hold(f"Job {i} finished", t + timedelta(seconds=i))
    doc = json.loads((tmp_path / "held-speech.json").read_text("utf-8"))
    assert len(doc["held"]) == au.HELD_MAX == 50
    assert doc["held"][0]["text"] == "Job 1 finished"   # the oldest went
    assert doc["dropped"] == 1
    line, _token = held.morning(et(2026, 10, 8, 8, 5))
    assert line.startswith("While you were away: Job 1 finished. ")
    assert line.endswith(" And 46 more in your chats.")    # 45 + 1 dropped


def test_older_than_a_day_expires_unsaid_and_is_counted(tmp_path):
    held = _held(tmp_path)
    held.hold("Old job finished", et(2026, 10, 7, 7, 0))
    held.hold("New job failed", et(2026, 10, 7, 23, 0))
    line, _token = held.morning(et(2026, 10, 8, 8, 5))
    assert line == ("While you were away: New job failed. "
                    "And 1 more in your chats.")
    assert "Old job" not in line


def test_a_restart_loses_nothing_and_clearing_empties_the_file(tmp_path):
    _held(tmp_path).hold("Build watch finished", et(2026, 10, 7, 23, 0))
    _held(tmp_path).hold("Backup check failed", et(2026, 10, 8, 2, 0))
    again = _held(tmp_path)                       # a restart at 03:00
    line, token = again.morning(et(2026, 10, 8, 8, 0))
    assert line == ("While you were away: Build watch finished. "
                    "Backup check failed.")
    again.clear(token)
    assert again.morning(et(2026, 10, 8, 8, 1)) is None
    doc = json.loads((tmp_path / "held-speech.json").read_text("utf-8"))
    assert doc == {"held": [], "dropped": 0}


def test_a_line_held_after_the_snapshot_survives_the_clear(tmp_path):
    held = _held(tmp_path)
    held.hold("A finished", et(2026, 10, 8, 7, 0))
    _line, token = held.morning(et(2026, 10, 8, 8, 0))
    held.hold("B finished", et(2026, 10, 8, 8, 0))
    held.clear(token)
    line, _ = held.morning(et(2026, 10, 8, 8, 1))
    assert line == "While you were away: B finished."


# --------------------------------------------------------------------------- #
# AC-486: held, not spoken -- through a real run
# --------------------------------------------------------------------------- #
def _night(hub, start="22:00", end="08:00"):
    hub.settings.set("quiet_start", start)
    hub.settings.set("quiet_end", end)
    hub.settings.set("quiet_hours", True)


@pytest.mark.asyncio
async def test_a_result_at_night_is_held_and_its_message_still_sent(
        tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    _night(hub)
    hub.automations.save({"id": a["id"], "silence": False},
                         hub.scheduler.clock())
    hub.scheduler.clock.at = et(2026, 9, 28, 23, 30)
    desk = _device(hub, "127.0.0.1", "desk")
    done = await _run(hub, a)
    assert done["status"] == "completed"
    assert "announce" not in _types(desk), "the desk spoke in quiet hours"
    assert not any(isinstance(m, bytes) for m in desk.ws.sent)
    assert len(bot.requests) == 1, "the channel message was held too"
    assert done["notify"][0]["spoken"] == "held: quiet hours"
    doc = json.loads(hub.scheduler.held.path.read_text("utf-8"))
    assert [e["text"] for e in doc["held"]] == ["Weekly review finished"]


@pytest.mark.asyncio
async def test_outside_quiet_hours_the_desk_speaks_as_before(
        tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    _night(hub)
    hub.scheduler.clock.at = et(2026, 9, 28, 14, 0)
    hub.automations.save({"id": a["id"], "silence": False},
                         hub.scheduler.clock())
    desk = _device(hub, "127.0.0.1", "desk")
    done = await _run(hub, a)
    assert done["notify"][0]["spoken"] == "said"
    assert "announce" in _types(desk)
    assert not hub.scheduler.held.path.exists()


@pytest.mark.asyncio
async def test_quiet_hours_off_holds_nothing(tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    _night(hub)
    hub.settings.set("quiet_hours", False)
    hub.scheduler.clock.at = et(2026, 9, 28, 23, 30)
    hub.automations.save({"id": a["id"], "silence": False},
                         hub.scheduler.clock())
    desk = _device(hub, "127.0.0.1", "desk")
    done = await _run(hub, a)
    assert done["notify"][0]["spoken"] == "said"
    assert "announce" in _types(desk)


@pytest.mark.asyncio
async def test_a_failure_alert_at_night_is_held(tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    _night(hub)
    hub.scheduler.clock.at = et(2026, 9, 28, 2, 0)
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake", fail_on="Review")
    desk = _device(hub, "127.0.0.1", "desk")
    done = await _run(hub, a)
    assert done["status"] == "failed", done
    assert "announce" not in _types(desk)
    assert len(bot.requests) == 1
    doc = json.loads(hub.scheduler.held.path.read_text("utf-8"))
    assert [e["text"] for e in doc["held"]] == ["Weekly review failed"]


@pytest.mark.asyncio
async def test_an_explicit_announcement_is_never_held(tmp_path, monkeypatch):
    """PR-15: what the owner asked for (a reminder, P3) goes through the
    one door, `Hub.announce`, which quiet hours do not touch."""
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    _night(hub)
    hub.scheduler.clock.at = et(2026, 9, 28, 23, 30)
    _device(hub, "127.0.0.1", "desk")
    assert await hub.announce("Reminder: call the bank.") == "said"
    assert not hub.scheduler.held.path.exists()


# --------------------------------------------------------------------------- #
# AC-487: the morning line, through the tick
# --------------------------------------------------------------------------- #
async def _morning(hub):
    await hub.scheduler.tick()
    task = hub.scheduler._morning_task
    if task is not None:
        await asyncio.wait_for(task, 5)
    await _finish(hub)


def _announced(desk):
    return [json.loads(m)["text"] for m in desk.ws.sent if isinstance(m, str)
            and json.loads(m)["type"] == "announce"]


@pytest.mark.asyncio
async def test_after_quiet_end_the_desk_hears_one_line_then_the_file_empties(
        tmp_path, monkeypatch):
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    _night(hub)
    held = hub.scheduler.held
    held.hold("Build watch finished", et(2026, 9, 27, 23, 30))
    held.hold("Backup check failed", et(2026, 9, 28, 2, 0))
    desk = _device(hub, "127.0.0.1", "desk")
    hub.scheduler.clock.at = et(2026, 9, 28, 7, 59)      # still quiet
    await _morning(hub)
    assert _announced(desk) == []
    hub.scheduler.clock.at = et(2026, 9, 28, 8, 5)
    await _morning(hub)
    assert _announced(desk) == ["While you were away: Build watch finished. "
                                "Backup check failed."]
    assert held.morning(hub.scheduler.clock()) is None
    await _morning(hub)                                   # said ONCE
    assert len(_announced(desk)) == 1


@pytest.mark.asyncio
async def test_no_desk_or_speech_off_keeps_the_file_for_later(
        tmp_path, monkeypatch):
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    _night(hub)
    hub.scheduler.held.hold("Build watch finished", et(2026, 9, 28, 2, 0))
    hub.scheduler.clock.at = et(2026, 9, 28, 8, 5)
    await _morning(hub)                                   # no desk at all
    assert hub.scheduler.held.morning(hub.scheduler.clock()) is not None
    hub.settings.set("speak", False)
    desk = _device(hub, "127.0.0.1", "desk")
    await _morning(hub)
    assert _announced(desk) == []
    assert hub.scheduler.held.morning(hub.scheduler.clock()) is not None
    hub.settings.set("speak", True)
    await _morning(hub)
    assert _announced(desk) == ["While you were away: Build watch finished."]
    assert hub.scheduler.held.morning(hub.scheduler.clock()) is None


@pytest.mark.asyncio
async def test_a_busy_desk_is_waited_for_and_never_spoken_over(
        tmp_path, monkeypatch):
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    monkeypatch.setattr(hub_mod, "ANNOUNCE_POLL_S", 0.02)
    monkeypatch.setattr(hub_mod, "ANNOUNCE_WAIT_S", 0.2)
    _night(hub)
    hub.scheduler.held.hold("Build watch finished", et(2026, 9, 28, 2, 0))
    hub.scheduler.clock.at = et(2026, 9, 28, 8, 5)
    desk = _device(hub, "127.0.0.1", "desk")
    desk.state, desk.turn = hub_mod.THINKING, 777
    await _morning(hub)
    assert _announced(desk) == [], "it spoke over the owner's turn"
    assert hub.scheduler.held.morning(hub.scheduler.clock()) is not None
    desk.state, desk.turn = hub_mod.IDLE, None
    await _morning(hub)
    assert len(_announced(desk)) == 1


@pytest.mark.asyncio
async def test_the_tick_never_waits_on_the_morning_line(tmp_path, monkeypatch):
    """AC-458 kept: a desk that stays busy holds the morning TASK, never
    the decision."""
    hub, _row, _a = _setup(tmp_path, monkeypatch, channels=())
    monkeypatch.setattr(hub_mod, "ANNOUNCE_POLL_S", 0.02)
    monkeypatch.setattr(hub_mod, "ANNOUNCE_WAIT_S", 30.0)
    _night(hub)
    hub.scheduler.held.hold("Build watch finished", et(2026, 9, 28, 2, 0))
    hub.scheduler.clock.at = et(2026, 9, 28, 8, 5)
    desk = _device(hub, "127.0.0.1", "desk")
    desk.state, desk.turn = hub_mod.THINKING, 777
    await asyncio.wait_for(hub.scheduler.tick(), 2)
    first = hub.scheduler._morning_task
    assert first is not None and not first.done()
    await asyncio.wait_for(hub.scheduler.tick(), 2)
    assert hub.scheduler._morning_task is first, "a second line was queued"
    desk.state, desk.turn = hub_mod.IDLE, None
    await asyncio.wait_for(first, 5)
    assert len(_announced(desk)) == 1
