"""The schedule engine, pure (docs/automations.md phase 4, AC-455, AC-456).

Every test passes its own clock: `now` / `after` are arguments, never read
from the wall. The zone is `EASTERN`, a hand-built PEP 495 tzinfo for
America/New_York, because this Windows Python ships NO IANA zone data
(`zoneinfo.ZoneInfo("America/New_York")` raises ZoneInfoNotFoundError: the
`tzdata` package is not installed) [MEASURED 2026-09-26]. Its 2026
transitions are pinned first, so the oracle is checked before anything leans
on it: spring forward Sun 8 Mar 02:00 -> 03:00, fall back Sun 1 Nov
02:00 -> 01:00.

Calendar facts used below, from a hand calendar rather than the engine:
May 2026 opens on a Friday (Mondays 4/11/18/25); Sep 2026 opens on a
Tuesday (Tuesdays 1/8/15/22/29); Feb 2026 opens on a Sunday and has no 5th
Monday; 2028 is the next leap year.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone, tzinfo

import pytest

from daemon import schedule
from daemon.schedule import ScheduleError

UTC = timezone.utc
HOUR = timedelta(hours=1)
ZERO = timedelta(0)


def _first_sunday_on_or_after(d: datetime) -> datetime:
    return d + timedelta(days=(6 - d.weekday()) % 7)


class _Eastern(tzinfo):
    """US Eastern with the 2007+ rules, PEP 495 fold-aware.

    Adapted from the reference `USTimeZone` in the CPython datetime docs.
    The gap is wall 02:00-02:59 on the second Sunday of March; the fold is
    wall 01:00-01:59 on the first Sunday of November, fold=0 being the
    first (EDT) pass.
    """

    std = timedelta(hours=-5)

    def _range(self, year):
        start = _first_sunday_on_or_after(datetime(year, 3, 8, 2))
        end = _first_sunday_on_or_after(datetime(year, 11, 1, 2))
        return start, end

    def dst(self, dt):
        if dt is None or dt.tzinfo is None:
            return ZERO
        start, end = self._range(dt.year)
        naive = dt.replace(tzinfo=None)
        if start + HOUR <= naive < end - HOUR:
            return HOUR
        if end - HOUR <= naive < end:            # the fold
            return ZERO if dt.fold else HOUR
        if start <= naive < start + HOUR:        # the gap
            return HOUR if dt.fold else ZERO
        return ZERO

    def utcoffset(self, dt):
        return self.std + self.dst(dt)

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"

    def fromutc(self, dt):
        start, end = self._range(dt.year)
        std_time = (dt + self.std).replace(tzinfo=None)
        dst_time = std_time + HOUR
        if end <= dst_time < end + HOUR:          # second pass of the fold
            return std_time.replace(tzinfo=self, fold=1)
        if std_time < start or dst_time >= end:
            return std_time.replace(tzinfo=self)
        return dst_time.replace(tzinfo=self)


EASTERN = _Eastern()


def et(y, mo, d, h=0, mi=0, fold=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=EASTERN, fold=fold)


def utc(y, mo, d, h=0, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def u(dt: datetime) -> datetime:
    """Compare instants in UTC. Two datetimes sharing one tzinfo compare by
    WALL time and ignore fold, which would hide every fall-back defect."""
    return dt.astimezone(UTC)


def nexts(cron, after, n=5):
    return [u(x) for x in schedule.preview(cron, after, EASTERN, n)]


# --------------------------------------------------------------- the oracle

def test_the_test_zone_has_the_known_2026_transitions():
    """The oracle first. 8 Mar and 1 Nov 2026 are the published US dates."""
    assert u(et(2026, 3, 8, 1, 59)) == utc(2026, 3, 8, 6, 59)
    assert u(et(2026, 3, 8, 3, 0)) == utc(2026, 3, 8, 7, 0)
    assert u(et(2026, 11, 1, 1, 30, fold=0)) == utc(2026, 11, 1, 5, 30)
    assert u(et(2026, 11, 1, 1, 30, fold=1)) == utc(2026, 11, 1, 6, 30)
    assert utc(2026, 11, 1, 6, 30).astimezone(EASTERN).fold == 1


@pytest.mark.skipif(time.tzname[0] != "Eastern Standard Time",
                    reason="the OS cross-check needs this machine's zone to be US Eastern")
def test_the_test_zone_agrees_with_the_operating_system_all_2026():
    """Two independent sources for one fact: the hand rules above and the
    Windows zone behind time.localtime, hour by hour for all of 2026."""
    t = utc(2026, 1, 1)
    while t < utc(2027, 1, 1):
        os_off = time.localtime(t.timestamp()).tm_gmtoff
        assert t.astimezone(EASTERN).utcoffset().total_seconds() == os_off, t
        t += HOUR


# --------------------------------------------------------------- the parser

@pytest.mark.parametrize("text, field", [
    ("", "expression"),
    ("0 9 * *", "expression"),
    ("0 9 * * * *", "expression"),
    ("@daily", "expression"),
    ("60 9 * * *", "minute"),
    ("-1 9 * * *", "minute"),
    ("*/0 9 * * *", "minute"),
    ("*/61 9 * * *", "minute"),
    ("*/15/2 9 * * *", "minute"),
    ("1,,2 9 * * *", "minute"),
    ("x 9 * * *", "minute"),
    ("0 24 * * *", "hour"),
    ("0 5-3 * * *", "hour"),
    ("0 9 1--5 * *", "day of month"),
    ("0 9 0 * *", "day of month"),
    ("0 9 32 * *", "day of month"),
    ("0 9 L * *", "day of month"),
    ("0 9 * 13 *", "month"),
    ("0 9 * FOO *", "month"),
    ("0 9 * * 8", "day of week"),
    ("0 9 * * MONDAY", "day of week"),
    ("0 9 * * FRI-MON", "day of week"),
    ("0 9 * * TUE#6", "day of week"),
    ("0 9 * * TUE#0", "day of week"),
    ("0 9 * * TUE#", "day of week"),
    ("0 9 * * MON-FRI#1", "day of week"),
    ("0 9 * * TUE#1/2", "day of week"),
    ("0 9 * * */8", "day of week"),
    ("0 9 #1 * *", "day of month"),
    ("\u0661 9 * * *", "minute"),  # an Arabic-Indic digit: int() takes it, we do not
])
def test_invalid_input_is_refused_naming_the_field(text, field):
    """AC-455: refused, and the message names the field that is wrong."""
    with pytest.raises(ScheduleError) as err:
        schedule.parse(text)
    assert err.value.field == field
    assert str(err.value).startswith(field + ":")


def test_an_absurdly_long_expression_is_refused_before_it_is_split():
    with pytest.raises(ScheduleError) as err:
        schedule.parse("0 " * 500)
    assert err.value.field == "expression"


@pytest.mark.parametrize("text, minutes", [
    ("*/15 * * * *", {0, 15, 30, 45}),
    ("5/15 * * * *", {5, 20, 35, 50}),       # N/step is N-max/step (Orca #15723)
    ("5 * * * *", {5}),
    ("10-20/5 * * * *", {10, 15, 20}),
    ("1,2,40-42 * * * *", {1, 2, 40, 41, 42}),
    ("*/60 * * * *", {0}),
])
def test_steps_ranges_and_lists(text, minutes):
    assert schedule.parse(text).minutes == frozenset(minutes)


def test_names_are_case_insensitive_and_sunday_is_0_and_7():
    c = schedule.parse("0 9 * jan,Mar-MAY mon,WED")
    assert c.months == frozenset({1, 3, 4, 5})
    assert c.weekdays == frozenset({1, 3})
    assert schedule.parse("0 9 * * 7").weekdays == frozenset({0})
    assert schedule.parse("0 9 * * SUN").weekdays == frozenset({0})
    assert schedule.parse("0 9 * * 1-7").weekdays == frozenset(range(7))
    assert schedule.parse("0 9 * * MON-FRI").weekdays == frozenset({1, 2, 3, 4, 5})


def test_the_nth_weekday_extension_parses():
    c = schedule.parse("0 8 * * TUE#1,2#3,SUN#5,7#2")
    assert c.nth == frozenset({(2, 1), (2, 3), (0, 5), (0, 2)})
    assert c.weekdays == frozenset()


# ------------------------------------------------- day matching (Orca's table)

EVERY_DAY_OF_MAY = list(range(1, 32))


def may_days(text):
    c = schedule.parse(text)
    return [d for d in range(1, 32) if schedule.matches_day(c, date(2026, 5, d))]


@pytest.mark.parametrize("text, days", [
    # Both day fields restricted: Vixie cron ORs them.
    ("0 9 1-31 * 1", EVERY_DAY_OF_MAY),
    ("0 9 1,15 * 1", [1, 4, 11, 15, 18, 25]),
    # Either field a star (a star STEP is still a star): AND.
    ("0 9 * * 1", [4, 11, 18, 25]),
    ("0 9 1,15 * *", [1, 15]),
    ("0 9 */2 * 1", [11, 25]),
    ("0 9 */1 * 1", [4, 11, 18, 25]),
    ("0 9 * * */2", [2, 3, 5, 7, 9, 10, 12, 14, 16, 17, 19, 21, 23, 24,
                     26, 28, 30, 31]),
    ("0 9 */3 * 1,5", [1, 4, 22, 25]),
    ("0 9 * * 0", [3, 10, 17, 24, 31]),
    ("0 9 * * 7", [3, 10, 17, 24, 31]),
    ("0 9 * MAY MON", [4, 11, 18, 25]),
    ("0 9 * JUN MON", []),
    ("0 9 * * 1-5", [1, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 18, 19, 20, 21,
                     22, 25, 26, 27, 28, 29]),
    # The extension: nth weekday. May 2026's Mondays are 4/11/18/25.
    ("0 9 * * MON#1", [4]),
    ("0 9 * * MON#4", [25]),
    ("0 9 * * MON#5", []),                 # no 5th Monday in May 2026
    ("0 9 * * FRI#5", [29]),               # May 2026 has five Fridays
    ("0 9 * * MON#1,FRI", [1, 4, 8, 15, 22, 29]),
    # MON#1 restricts, so beside a restricted day of month it ORs too.
    ("0 9 15 * MON#1", [4, 15]),
])
def test_day_matching(text, days):
    assert may_days(text) == days


def test_nth_weekday_boundaries_across_a_year():
    """(day - 1) // 7 + 1 is the ordinal: day 7 is still the 1st, day 8 the
    2nd. Tuesdays of Sep 2026 are 1/8/15/22/29."""
    c = schedule.parse("0 8 * * TUE#1")
    sep = [d for d in range(1, 31) if schedule.matches_day(c, date(2026, 9, d))]
    assert sep == [1]
    c2 = schedule.parse("0 8 * * TUE#2")
    assert [d for d in range(1, 31)
            if schedule.matches_day(c2, date(2026, 9, d))] == [8]
    c5 = schedule.parse("0 8 * * TUE#5")
    assert [d for d in range(1, 31)
            if schedule.matches_day(c5, date(2026, 9, d))] == [29]
    # 7 Jul 2026 is a Tuesday on day 7 -- the last day that is still "1st".
    assert schedule.matches_day(c, date(2026, 7, 7))


def test_a_fifth_weekday_skips_the_months_without_one():
    """Decision: a month with no 5th Monday is skipped, never moved to the
    last Monday. 2026's months with a 5th Monday: Mar, Jun, Aug, Nov."""
    got = nexts("0 9 * * MON#5", et(2026, 1, 1), 4)
    assert got == [u(et(2026, 3, 30, 9)), u(et(2026, 6, 29, 9)),
                   u(et(2026, 8, 31, 9)), u(et(2026, 11, 30, 9))]


# ------------------------------------------------------- next and preview

def test_preview_gives_the_next_five_in_order():
    got = nexts("0 9 * * MON", et(2026, 9, 26, 12))
    assert got == [u(et(2026, 9, 28, 9)), u(et(2026, 10, 5, 9)),
                   u(et(2026, 10, 12, 9)), u(et(2026, 10, 19, 9)),
                   u(et(2026, 10, 26, 9))]


def test_next_is_strictly_after():
    assert u(schedule.next_after(schedule.parse("0 9 * * *"),
                                 et(2026, 5, 4, 9), EASTERN)) == u(et(2026, 5, 5, 9))
    assert u(schedule.next_after(schedule.parse("0 9 * * *"),
                                 et(2026, 5, 4, 8, 59), EASTERN)) == u(et(2026, 5, 4, 9))


def test_next_rolls_over_hours_days_months_and_years():
    c = schedule.parse("*/15 9-10 * * *")
    assert u(schedule.next_after(c, et(2026, 5, 4, 10, 45), EASTERN)) == u(et(2026, 5, 5, 9))
    assert u(schedule.next_after(schedule.parse("0 0 1 * *"), et(2026, 12, 15), EASTERN)) \
        == u(et(2027, 1, 1))


def test_the_31st_skips_short_months():
    got = nexts("0 9 31 * *", et(2026, 1, 1), 4)
    assert got == [u(et(2026, 1, 31, 9)), u(et(2026, 3, 31, 9)),
                   u(et(2026, 5, 31, 9)), u(et(2026, 7, 31, 9))]


def test_feb_29_waits_for_the_leap_year():
    assert nexts("0 0 29 2 *", et(2026, 5, 15), 2) == [u(et(2028, 2, 29)),
                                                      u(et(2032, 2, 29))]


def test_a_never_occurring_expression_is_refused_at_preview():
    """AC-455: no occurrence in 5 years -> refused, naming the expression."""
    for text in ("0 0 31 2 *", "0 0 30 2 *", "0 9 31 4,6,9,11 *"):
        with pytest.raises(ScheduleError) as err:
            schedule.preview(text, et(2026, 1, 1), EASTERN, 5)
        assert err.value.field == "expression"


def test_a_naive_clock_is_refused():
    with pytest.raises(ValueError):
        schedule.next_after(schedule.parse("0 9 * * *"), datetime(2026, 5, 4), EASTERN)


def test_results_are_in_the_zone_given():
    got = schedule.preview("0 9 * * *", et(2026, 5, 4), EASTERN, 1)[0]
    assert got.tzinfo is EASTERN and (got.hour, got.minute) == (9, 0)
    # Offset is carried, so an ISO string is unambiguous on disk (AU-12).
    assert got.isoformat() == "2026-05-04T09:00:00-04:00"


# ----------------------------------------------------------------- DST

def test_spring_forward_runs_at_the_first_valid_minute_after():
    """AC-456. 02:30 does not exist on 8 Mar 2026; the run is 03:00 EDT,
    not skipped (Orca skips it) and not 03:30 (the pre-transition offset)."""
    got = nexts("30 2 * * *", et(2026, 3, 7, 12), 3)
    assert got == [u(et(2026, 3, 8, 3, 0)), u(et(2026, 3, 9, 2, 30)),
                   u(et(2026, 3, 10, 2, 30))]
    assert got[0] == utc(2026, 3, 8, 7, 0)


def test_spring_forward_collisions_run_once():
    """02:00, 02:15, 02:30, 02:45 and 03:00 all land on 03:00 EDT -- one run."""
    c = schedule.parse("*/15 * * * *")
    got = list(schedule.between(c, et(2026, 3, 8, 1, 30), et(2026, 3, 8, 3, 30), EASTERN))
    assert [u(x) for x in got] == [
        u(et(2026, 3, 8, 1, 45)), u(et(2026, 3, 8, 3, 0)),
        u(et(2026, 3, 8, 3, 15)), u(et(2026, 3, 8, 3, 30))]


def test_fall_back_runs_the_ambiguous_hour_once_at_the_first():
    """AC-456. 01:30 happens twice on 1 Nov 2026; the run is the first
    (EDT, 05:30Z), and asking again from there goes to the next DAY -- Orca's
    #20154 returned the same repeat forever."""
    c = schedule.parse("30 1 * * *")
    first = schedule.next_after(c, et(2026, 10, 31, 12), EASTERN)
    assert u(first) == utc(2026, 11, 1, 5, 30)
    assert first.fold == 0
    again = schedule.next_after(c, first, EASTERN)
    assert u(again) == u(et(2026, 11, 2, 1, 30))
    # From inside the second pass, too.
    assert u(schedule.next_after(c, et(2026, 11, 1, 1, 45, fold=1), EASTERN)) \
        == u(et(2026, 11, 2, 1, 30))


def test_fall_back_every_half_hour_skips_the_repeated_hour():
    c = schedule.parse("*/30 * * * *")
    got = [u(x) for x in schedule.between(c, et(2026, 11, 1, 0, 0),
                                          et(2026, 11, 1, 3, 0), EASTERN)]
    assert got == [utc(2026, 11, 1, 4, 30),   # 00:30 EDT
                   utc(2026, 11, 1, 5, 0),    # 01:00 EDT
                   utc(2026, 11, 1, 5, 30),   # 01:30 EDT
                   utc(2026, 11, 1, 7, 0),    # 02:00 EST
                   utc(2026, 11, 1, 7, 30),   # 02:30 EST
                   utc(2026, 11, 1, 8, 0)]    # 03:00 EST


def test_a_daily_run_keeps_its_wall_clock_across_both_transitions():
    got = nexts("0 9 * * *", et(2026, 3, 7, 10), 2)
    assert got == [utc(2026, 3, 8, 13, 0), utc(2026, 3, 9, 13, 0)]
    got = nexts("0 9 * * *", et(2026, 10, 31, 10), 2)
    assert got == [utc(2026, 11, 1, 14, 0), utc(2026, 11, 2, 14, 0)]


# ---------------------------------------------------- latest and between

def test_latest_at_or_before():
    c = schedule.parse("0 9 * * MON")
    assert u(schedule.latest_at_or_before(c, et(2026, 5, 13, 12), EASTERN)) \
        == u(et(2026, 5, 11, 9))
    assert u(schedule.latest_at_or_before(c, et(2026, 5, 11, 9), EASTERN)) \
        == u(et(2026, 5, 11, 9))


def test_latest_on_the_fall_back_day_is_the_first_pass():
    c = schedule.parse("30 1 * * *")
    got = schedule.latest_at_or_before(c, et(2026, 11, 1, 1, 50, fold=1), EASTERN)
    assert u(got) == utc(2026, 11, 1, 5, 30)


def test_latest_on_the_spring_forward_day_is_the_moved_run():
    c = schedule.parse("30 2 * * *")
    got = schedule.latest_at_or_before(c, et(2026, 3, 8, 3, 5), EASTERN)
    assert u(got) == utc(2026, 3, 8, 7, 0)
    # Before the moved run, the latest is yesterday's.
    got = schedule.latest_at_or_before(c, et(2026, 3, 8, 1, 59), EASTERN)
    assert u(got) == u(et(2026, 3, 7, 2, 30))


def test_latest_is_none_when_nothing_in_five_years():
    c = schedule.parse("0 0 31 2 *")
    assert schedule.latest_at_or_before(c, et(2026, 5, 1), EASTERN) is None


def test_between_is_after_exclusive_until_inclusive_and_counts_missed_days():
    """The PC off from Fri 18:00 to Mon 10:00: three 09:00 daily runs missed
    (Sat, Sun, Mon) -- the count AC-459's folded row carries."""
    c = schedule.parse("0 9 * * *")
    got = list(schedule.between(c, et(2026, 5, 1, 18), et(2026, 5, 4, 10), EASTERN))
    assert [u(x) for x in got] == [u(et(2026, 5, 2, 9)), u(et(2026, 5, 3, 9)),
                                   u(et(2026, 5, 4, 9))]
    assert list(schedule.between(c, et(2026, 5, 2, 9), et(2026, 5, 2, 9), EASTERN)) == []
    assert len(list(schedule.between(c, et(2026, 5, 2, 8), et(2026, 5, 2, 9), EASTERN))) == 1


# ----------------------------------------------------- presets, round trip

@pytest.mark.parametrize("preset, cron", [
    ({"preset": "daily", "time": "09:00"}, "0 9 * * *"),
    ({"preset": "weekdays", "time": "07:05"}, "5 7 * * MON-FRI"),
    ({"preset": "weekly", "days": ["WED", "mon"], "time": "18:30"}, "30 18 * * MON,WED"),
    ({"preset": "monthly", "nth": 1, "day": "TUE", "time": "08:00"}, "0 8 * * TUE#1"),
    ({"preset": "monthly", "nth": 5, "day": "fri", "time": "23:59"}, "59 23 * * FRI#5"),
])
def test_presets_compile_to_cron_and_back(preset, cron):
    assert schedule.compile_preset(preset) == cron
    back = schedule.preset_of(cron)
    assert schedule.compile_preset(back) == cron


@pytest.mark.parametrize("preset, field", [
    ({"preset": "hourly", "time": "09:00"}, "preset"),
    ({"time": "09:00"}, "preset"),
    ({"preset": "daily"}, "time"),
    ({"preset": "daily", "time": "24:00"}, "time"),
    ({"preset": "daily", "time": "9:00"}, "time"),
    ({"preset": "daily", "time": "09:60"}, "time"),
    ({"preset": "daily", "time": "09:00", "days": ["MON"]}, "days"),
    ({"preset": "weekly", "time": "09:00"}, "days"),
    ({"preset": "weekly", "days": [], "time": "09:00"}, "days"),
    ({"preset": "weekly", "days": ["MON", "MON"], "time": "09:00"}, "days"),
    ({"preset": "weekly", "days": ["MONDAY"], "time": "09:00"}, "days"),
    ({"preset": "monthly", "nth": 6, "day": "TUE", "time": "09:00"}, "nth"),
    ({"preset": "monthly", "nth": True, "day": "TUE", "time": "09:00"}, "nth"),
    ({"preset": "monthly", "nth": 1, "day": "XYZ", "time": "09:00"}, "day"),
    ({"preset": "monthly", "nth": 1, "time": "09:00"}, "day"),
])
def test_bad_presets_are_refused_naming_the_field(preset, field):
    with pytest.raises(ScheduleError) as err:
        schedule.compile_preset(preset)
    assert err.value.field == field


@pytest.mark.parametrize("cron, preset", [
    ("0 9 * * *", {"preset": "daily", "time": "09:00"}),
    ("0 9 */1 * 0-7", {"preset": "daily", "time": "09:00"}),
    ("0 9 * * 1-5", {"preset": "weekdays", "time": "09:00"}),
    ("0 9 * * MON,TUE,WED,THU,FRI", {"preset": "weekdays", "time": "09:00"}),
    ("0 9 * * 5,1", {"preset": "weekly", "days": ["MON", "FRI"], "time": "09:00"}),
    ("0 8 * * 2#1", {"preset": "monthly", "nth": 1, "day": "TUE", "time": "08:00"}),
])
def test_a_cron_that_equals_a_preset_is_recognised(cron, preset):
    """So the editor can say "Same as the cron ..." either way round."""
    assert schedule.preset_of(cron) == preset


@pytest.mark.parametrize("cron", [
    "0 9 1 * *",          # a day of month
    "0 9 1-31 * MON",     # OR: fires EVERY day, so not "weekly on Monday"
    "0 9 * 5 *",          # a month
    "0 9,17 * * *",       # two hours
    "*/15 * * * *",
    "0 9 * * MON#1,FRI#3",
    "0 9 * * MON#1,FRI",
    "0 9 */2 * *",
])
def test_a_cron_no_preset_can_say_is_custom(cron):
    assert schedule.preset_of(cron) is None


@pytest.mark.parametrize("cron, label", [
    ("0 9 * * *", "Daily at 09:00"),
    ("5 7 * * 1-5", "Weekdays at 07:05"),
    ("0 9 * * MON", "Every Monday at 09:00"),
    ("30 18 * * MON,WED", "Every Mon, Wed at 18:30"),
    ("0 8 * * TUE#1", "Monthly on the 1st Tuesday at 08:00"),
    ("0 8 * * TUE#2", "Monthly on the 2nd Tuesday at 08:00"),
    ("0 8 * * TUE#3", "Monthly on the 3rd Tuesday at 08:00"),
    ("0 8 * * SUN#5", "Monthly on the 5th Sunday at 08:00"),
    ("0 9 1 * *", "Custom: 0 9 1 * *"),
])
def test_describe(cron, label):
    assert schedule.describe(cron) == label


# --------------------------------------------------------- the local zone

def test_the_local_zone_round_trips_every_half_hour_of_2026():
    """local_zone() is the OS's zone as a PEP 495 tzinfo. Whatever the
    machine's zone, UTC -> local -> UTC is the identity and the offset is
    the one time.localtime reports."""
    lz = schedule.local_zone()
    t = utc(2026, 1, 1)
    while t < utc(2027, 1, 1):
        wall = t.astimezone(lz)
        assert wall.astimezone(UTC) == t, t
        assert wall.utcoffset().total_seconds() == time.localtime(t.timestamp()).tm_gmtoff
        t += timedelta(minutes=30)


@pytest.mark.skipif(time.tzname[0] != "Eastern Standard Time",
                    reason="needs this machine's zone to be US Eastern")
def test_the_local_zone_gives_the_same_schedule_as_the_test_zone():
    lz = schedule.local_zone()
    for text in ("30 2 * * *", "30 1 * * *", "*/30 * * * *", "0 9 * * MON#1"):
        for start in (et(2026, 3, 7, 12), et(2026, 10, 31, 12)):
            a = [u(x) for x in schedule.preview(text, start, EASTERN, 8)]
            b = [u(x) for x in schedule.preview(text, start.astimezone(lz), lz, 8)]
            assert a == b, text
