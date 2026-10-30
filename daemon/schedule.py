"""The schedule engine: 5-field cron, presets, occurrences (docs/automations.md
AU-11, AU-12; AC-455, AC-456).

PURE. No I/O, no clock: every function that needs "now" takes it as an
argument, and every function that needs a zone takes a tzinfo. The daemon
passes `local_zone()`; the tests pass a fixed America/New_York.

THE GRAMMAR (AU-11). Five whitespace-separated fields: minute (0-59), hour
(0-23), day of month (1-31), month (1-12 or JAN-DEC), day of week (0-7 or
SUN-SAT, 0 and 7 both Sunday). Each field is a comma list of terms; a term
is `*`, `N`, `A-B`, any of those with `/step`, or -- day of week only --
`D#n`, "the nth D of the month" (n = 1-5). `N/step` means `N-max/step`
(Vixie; Orca #15723). A step wider than the field is refused, since it can
only mean one value. Nothing else is accepted: no `L`, no `W`, no `?`, no
`@daily`, no seconds field. A bad expression raises ScheduleError naming the
field.

DAY OF MONTH vs DAY OF WEEK (decided: Vixie cron's rule, as Orca and
robfig/cron do). A day field is RESTRICTED when none of its comma terms
starts with `*` -- so `*/2` is a star and `1-31` is not. When BOTH are
restricted a day matches if EITHER matches; otherwise both must. `D#n`
terms restrict like any other, so `0 9 15 * MON#1` is "the 15th OR the
first Monday". Presets always write `*` in day of month, so they AND.

THE NTH WEEKDAY. The ordinal of a date is (day - 1) // 7 + 1. A month with
no 5th such weekday is SKIPPED (decided): `MON#5` never falls back to the
last Monday, because "the 5th" silently becoming "the 4th" is a run on a
day nobody asked for.

LOCAL TIME AND DST (AU-12). Occurrences are generated as WALL times on each
matching local day, then resolved to an instant:
- a wall time that exists once is that instant;
- an AMBIGUOUS one (fall back) is the FIRST pass (fold=0), and the repeat
  never runs, so `30 1 * * *` runs once on 1 Nov and `*/30` pauses for the
  repeated hour;
- a NONEXISTENT one (spring forward) runs at the first valid minute after
  it -- 02:30 on 8 Mar 2026 in New York runs at 03:00 EDT -- and several
  that land on one instant run once.
Instants are compared in UTC everywhere. Two aware datetimes that share one
tzinfo compare and subtract by WALL time and ignore fold, so a local
comparison across a fall-back is wrong by an hour with no error.

SEARCH BOUNDS. Occurrences are found by walking DAYS, at most 5 x 366 of
them (AU-11), then the hour and minute lists -- no minute scan. An
expression with nothing in that window raises ScheduleError at `preview`
("no occurrence in 5 years"), which is how a save refuses it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Iterator

UTC = timezone.utc
MINUTE = timedelta(minutes=1)
DAY = timedelta(days=1)
SCAN_DAYS = 5 * 366
MAX_LEN = 256

MONTHS = {n: i for i, n in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
# Cron numbering: 0 = Sunday.
DAYS = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
DAY_CODES = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
DAY_FULL = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday")
WEEK_ORDER = (1, 2, 3, 4, 5, 6, 0)        # Monday first, as the editor lists them
WEEKDAYS = frozenset({1, 2, 3, 4, 5})
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
_DIGITS = re.compile(r"[0-9]+")           # ASCII only: int() takes other scripts' digits
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")


class ScheduleError(ValueError):
    """A refused schedule. `field` names what is wrong: one of the five cron
    fields, "expression" for the whole, or a preset key."""

    def __init__(self, field: str, detail: str):
        super().__init__(f"{field}: {detail}")
        self.field = field


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset
    hours: frozenset
    days: frozenset          # day of month
    months: frozenset
    weekdays: frozenset      # plain day-of-week terms, 0 = Sunday
    nth: frozenset           # (weekday, n) from `D#n` terms
    dom_restricted: bool
    dow_restricted: bool


# ------------------------------------------------------------------ parsing

def _number(token: str, field: str, names: dict | None) -> int:
    up = token.upper()
    if names is not None and up in names:
        return names[up]
    if not _DIGITS.fullmatch(token):
        raise ScheduleError(field, f"{token!r} is not a number"
                            + (" or a name" if names else ""))
    return int(token)


def _field(text: str, field: str, lo: int, hi: int, names: dict | None = None,
           dow: bool = False) -> tuple[frozenset, frozenset]:
    values: set[int] = set()
    nth: set[tuple[int, int]] = set()
    width = 7 if dow else hi - lo + 1
    for term in text.split(","):
        if not term:
            raise ScheduleError(field, f"empty item in {text!r}")
        if "#" in term:
            if not dow:
                raise ScheduleError(field, f"{term!r}: '#' is only for day of week")
            day, _, n = term.partition("#")
            if not _DIGITS.fullmatch(n) or not 1 <= int(n) <= 5:
                raise ScheduleError(field, f"{term!r}: n in D#n must be 1 to 5")
            d = _number(day, field, names)
            if not lo <= d <= hi:
                raise ScheduleError(field, f"{term!r}: {d} is outside {lo}-{hi}")
            nth.add((d % 7, int(n)))
            continue
        span, slash, step_text = term.partition("/")
        step = 1
        if slash:
            if not _DIGITS.fullmatch(step_text) or not 1 <= int(step_text) <= width:
                raise ScheduleError(field, f"{term!r}: step must be 1 to {width}")
            step = int(step_text)
        if span == "*":
            start, end = lo, hi
        elif "-" in span:
            parts = span.split("-")
            if len(parts) != 2 or not all(parts):
                raise ScheduleError(field, f"{term!r} is not a range A-B")
            start, end = (_number(p, field, names) for p in parts)
        else:
            start = _number(span, field, names)
            end = hi if slash else start
        for v in (start, end):
            if not lo <= v <= hi:
                raise ScheduleError(field, f"{term!r}: {v} is outside {lo}-{hi}")
        if start > end:
            raise ScheduleError(field, f"{term!r} runs backwards")
        values.update(v % 7 if dow else v for v in range(start, end + 1, step))
    return frozenset(values), frozenset(nth)


def _restricted(text: str) -> bool:
    return not any(t.split("/")[0] == "*" for t in text.split(","))


def parse(text: str) -> Cron:
    """Parse a 5-field cron. Raises ScheduleError naming the field."""
    if len(text) > MAX_LEN:
        raise ScheduleError("expression", f"longer than {MAX_LEN} characters")
    parts = text.split()
    if len(parts) != 5:
        raise ScheduleError(
            "expression", "needs 5 fields (minute hour day-of-month month "
            f"day-of-week), got {len(parts)}")
    mi, hr, dom, mon, dow = parts
    minutes, _ = _field(mi, "minute", 0, 59)
    hours, _ = _field(hr, "hour", 0, 23)
    days, _ = _field(dom, "day of month", 1, 31)
    months, _ = _field(mon, "month", 1, 12, MONTHS)
    weekdays, nth = _field(dow, "day of week", 0, 7, DAYS, dow=True)
    return Cron(" ".join(parts), minutes, hours, days, months, weekdays, nth,
                _restricted(dom), _restricted(dow))


def _as_cron(cron: Cron | str) -> Cron:
    return cron if isinstance(cron, Cron) else parse(cron)


# ------------------------------------------------------------- day matching

def matches_day(cron: Cron, d: date) -> bool:
    if d.month not in cron.months:
        return False
    wd = d.isoweekday() % 7
    dom_ok = d.day in cron.days
    dow_ok = wd in cron.weekdays or (wd, (d.day - 1) // 7 + 1) in cron.nth
    if cron.dom_restricted and cron.dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


# ------------------------------------------------------------ wall -> instant

def _exists(wall: datetime, tz: tzinfo) -> datetime | None:
    """The FIRST instant (UTC) this wall time names in tz, or None in a gap.
    fold=0 is the earlier of an ambiguous pair (PEP 495)."""
    instant = wall.replace(tzinfo=tz, fold=0).astimezone(UTC)
    return instant if instant.astimezone(tz).replace(tzinfo=None) == wall else None


def _resolve(wall: datetime, tz: tzinfo) -> datetime:
    instant = _exists(wall, tz)
    if instant is not None:
        return instant
    probe = wall
    for _ in range(24 * 60):               # no zone has a gap of a day
        probe += MINUTE
        instant = _exists(probe, tz)
        if instant is not None:
            return instant
    raise RuntimeError(f"no valid local minute within a day after {wall}")


def _day_instants(cron: Cron, d: date, tz: tzinfo) -> Iterator[datetime]:
    """The day's runs in UTC, ascending, each once. Wall order resolves to
    non-decreasing instants (a gap maps to its end, a fold to its first
    pass), so dropping a repeat of the previous one is the whole dedupe."""
    last = None
    for h in sorted(cron.hours):
        for m in sorted(cron.minutes):
            instant = _resolve(datetime(d.year, d.month, d.day, h, m), tz)
            if instant != last:
                last = instant
                yield instant


def _aware(dt: datetime, name: str) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {dt!r}")
    return dt.astimezone(UTC)


# -------------------------------------------------------------- occurrences

def next_after(cron: Cron, after: datetime, tz: tzinfo) -> datetime | None:
    """The first run strictly after `after`, in tz; None if none in 5 years."""
    t = _aware(after, "after")
    day = t.astimezone(tz).date()
    for _ in range(SCAN_DAYS):
        if matches_day(cron, day):
            for instant in _day_instants(cron, day, tz):
                if instant > t:
                    return instant.astimezone(tz)
        day += DAY
    return None


def latest_at_or_before(cron: Cron, now: datetime, tz: tzinfo) -> datetime | None:
    """The last run at or before `now`, in tz; None if none in 5 years."""
    t = _aware(now, "now")
    day = t.astimezone(tz).date()
    for _ in range(SCAN_DAYS):
        if matches_day(cron, day):
            best = None
            for instant in _day_instants(cron, day, tz):
                if instant > t:
                    break
                best = instant
            if best is not None:
                return best.astimezone(tz)
        day -= DAY
    return None


def between(cron: Cron, after: datetime, until: datetime,
            tz: tzinfo) -> Iterator[datetime]:
    """Every run with after < run <= until, ascending, in tz. Cost is one
    resolve per slot on each matching day, so an every-minute schedule over
    a long outage is 1440 per day -- bound `after` if that matters."""
    lo, hi = _aware(after, "after"), _aware(until, "until")
    day, end = lo.astimezone(tz).date(), hi.astimezone(tz).date()
    while day <= end:
        if matches_day(cron, day):
            for instant in _day_instants(cron, day, tz):
                if lo < instant <= hi:
                    yield instant.astimezone(tz)
        day += DAY


def preview(cron: Cron | str, now: datetime, tz: tzinfo,
            count: int = 5) -> list[datetime]:
    """The next `count` runs after `now` (AC-455's `next: [5 times]`).
    Raises ScheduleError when there is no run in 5 years."""
    c = _as_cron(cron)
    out: list[datetime] = []
    t = now
    for _ in range(count):
        nxt = next_after(c, t, tz)
        if nxt is None:
            if not out:
                raise ScheduleError("expression", "no occurrence in 5 years")
            break
        out.append(nxt)
        t = nxt
    return out


# ------------------------------------------------------------------ presets

def _day_code(value, field: str) -> int:
    if not isinstance(value, str) or value.upper() not in DAYS:
        raise ScheduleError(field, f"{value!r} is not one of MON..SUN")
    return DAYS[value.upper()]


#: A heartbeat's interval presets in minutes, and the cron each compiles to
#: (docs/proactive.md PR-11). Each divides the hour or the day evenly, so
#: every check falls at the same minutes each day.
EVERY_MINUTES = (15, 30, 60, 120, 240)
_EVERY_CRON = {15: "*/15 * * * *", 30: "*/30 * * * *", 60: "0 * * * *",
               120: "0 */2 * * *", 240: "0 */4 * * *"}


def compile_preset(preset: dict) -> str:
    """{preset, time, [days], [nth, day]} or {preset: "every", minutes} ->
    cron. Every preset compiles to cron and the engine knows only cron
    (AU-11). `every` is the heartbeat's interval (PR-11); `preset_of` does
    not map a cron back to it, so an ordinary automation's editor never
    meets a preset it has no fields for."""
    kind = preset.get("preset")
    allowed = {"daily": {"time"}, "weekdays": {"time"},
               "weekly": {"time", "days"}, "monthly": {"time", "nth", "day"},
               "every": {"minutes"}}
    if kind not in allowed:
        raise ScheduleError("preset", f"{kind!r} is not one of "
                            "daily, weekdays, weekly, monthly, every")
    extra = set(preset) - allowed[kind] - {"preset"}
    if extra:
        raise ScheduleError(sorted(extra)[0], f"not used by the {kind} preset")
    if kind == "every":
        minutes = preset.get("minutes")
        if type(minutes) is not int or minutes not in _EVERY_CRON:
            raise ScheduleError("minutes", f"{minutes!r}: every 15, 30, 60, "
                                f"120 or 240 minutes")
        return _EVERY_CRON[minutes]
    hour, minute = parse_hhmm(preset.get("time"))
    head = f"{minute} {hour} * *"
    if kind == "daily":
        return f"{head} *"
    if kind == "weekdays":
        return f"{head} MON-FRI"
    if kind == "weekly":
        days = preset.get("days")
        if not isinstance(days, list) or not days:
            raise ScheduleError("days", "pick at least one day")
        codes = [_day_code(d, "days") for d in days]
        if len(set(codes)) != len(codes):
            raise ScheduleError("days", f"{days!r} names a day twice")
        ordered = [DAY_CODES[d] for d in WEEK_ORDER if d in codes]
        return f"{head} {','.join(ordered)}"
    nth = preset.get("nth")
    if type(nth) is not int or not 1 <= nth <= 5:
        raise ScheduleError("nth", f"{nth!r} is not 1 to 5")
    return f"{head} {DAY_CODES[_day_code(preset.get('day'), 'day')]}#{nth}"


def preset_of(cron: Cron | str) -> dict | None:
    """The preset this cron is equal to, or None when it is custom. Equal
    means the same runs, not the same text: `0 9 * * 1-5` is "weekdays"."""
    c = _as_cron(cron)
    every_dom = not c.dom_restricted and c.days == frozenset(range(1, 32))
    if (len(c.minutes) != 1 or len(c.hours) != 1 or not every_dom
            or c.months != frozenset(range(1, 13))):
        return None
    hhmm = f"{next(iter(c.hours)):02d}:{next(iter(c.minutes)):02d}"
    if c.nth:
        if c.weekdays or len(c.nth) != 1:
            return None
        wd, n = next(iter(c.nth))
        return {"preset": "monthly", "nth": n, "day": DAY_CODES[wd], "time": hhmm}
    if c.weekdays == frozenset(range(7)):
        return {"preset": "daily", "time": hhmm}
    if c.weekdays == WEEKDAYS:
        return {"preset": "weekdays", "time": hhmm}
    return {"preset": "weekly", "time": hhmm,
            "days": [DAY_CODES[d] for d in WEEK_ORDER if d in c.weekdays]}


#: How `describe` labels a cron no preset equals; `automations.next_words`
#: reads it to say "on a custom schedule" rather than quote the cron.
CUSTOM = "Custom: "


def describe(cron: Cron | str) -> str:
    """A human label: "Daily at 09:00", "Monthly on the 1st Tuesday at
    08:00"; a custom cron is "Custom: <cron>"."""
    c = _as_cron(cron)
    p = preset_of(c)
    if p is None:
        return f"{CUSTOM}{c.text}"
    at = f"at {p['time']}"
    if p["preset"] == "daily":
        return f"Daily {at}"
    if p["preset"] == "weekdays":
        return f"Weekdays {at}"
    if p["preset"] == "monthly":
        return f"Monthly on the {ORDINAL[p['nth']]} {DAY_FULL[DAYS[p['day']]]} {at}"
    if len(p["days"]) == 1:
        return f"Every {DAY_FULL[DAYS[p['days'][0]]]} {at}"
    return f"Every {', '.join(d.title() for d in p['days'])} {at}"



# ------------------------------------------------------------------ wording

def when_words(at: datetime, now: datetime, tz: tzinfo) -> str:
    """"5:00 PM today", "9:12 AM yesterday", "9:00 AM tomorrow", "9:00 AM
    on Fri 9 Oct" -- THE wording of one instant: the read-back's (PR-20),
    every `nextWords`, and a run's and a task's time on the page
    (docs/lantern-ui.md section 8, Step 6b). English like every read-back
    (D5)."""
    local, today = at.astimezone(tz), now.astimezone(tz).date()
    clock = (f"{local.hour % 12 or 12}:{local.minute:02d} "
             f"{'AM' if local.hour < 12 else 'PM'}")
    if local.date() == today:
        return f"{clock} today"
    if local.date() == today + timedelta(days=1):
        return f"{clock} tomorrow"
    if local.date() == today - timedelta(days=1):
        return f"{clock} yesterday"
    return f"{clock} on {local:%a} {local.day} {local:%b}"


def took_words(start: datetime, end: datetime) -> str:
    """How long something took, as the page's clocks read: "2:31", and
    "1:02:06" past an hour."""
    total = max(0, round((end - start).total_seconds()))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def input_values(at: datetime, tz: tzinfo) -> dict:
    """An instant as `<input type=date>` and `<input type=time>` VALUES in
    `tz` -- machine values, not words -- so a page fills a picker without
    reading a clock of its own."""
    local = at.astimezone(tz)
    return {"date": f"{local:%Y-%m-%d}", "time": f"{local:%H:%M}"}

# -------------------------------------------------------------- quiet hours

def parse_hhmm(text) -> tuple[int, int]:
    """"22:00" -> (22, 0). Exactly HH:MM, 00:00 to 23:59, or ScheduleError
    naming the `time` field."""
    match = _TIME.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ScheduleError("time", f"{text!r} is not HH:MM, 00:00 to 23:59")
    return int(match[1]), int(match[2])


def in_quiet(now: datetime, tz: tzinfo, start: str, end: str) -> bool:
    """Is `now` inside the quiet window [start, end) read on tz's WALL clock
    (docs/proactive.md PR-14, AC-485)? A start later than its end wraps
    midnight: 22:00-08:00 holds 23:30 and 03:00, not 08:00. A start equal
    to its end is refused: it would mean either never or always, and the
    settings write refuses it for that reason."""
    s, e = parse_hhmm(start), parse_hhmm(end)
    if s == e:
        raise ScheduleError("quiet", f"quiet hours cannot start and end at "
                            f"the same time ({start})")
    local = _aware(now, "now").astimezone(tz)
    t = (local.hour, local.minute)
    return s <= t < e if s < e else (t >= s or t < e)


# --------------------------------------------------------------- local zone

class _OsZone(tzinfo):
    """The machine's zone, DST included, read from the OS via time.localtime.

    Needed because this Windows Python has no IANA zone data (`zoneinfo`
    raises without the `tzdata` package) and `datetime.now().astimezone()`
    is a FIXED offset that would be an hour wrong across every transition.
    PEP 495: fold picks the earlier (0) or later (1) of an ambiguous wall
    time, and in a gap fold=0 uses the offset before it, fold=1 the one after.
    Assumes at most one transition within any two days, true of every zone.
    """

    @staticmethod
    def _offset(ts: float) -> int:
        return time.localtime(ts).tm_gmtoff

    def _instants(self, wall: datetime) -> tuple[list[float], int, int]:
        t = (wall.replace(tzinfo=None, fold=0) - datetime(1970, 1, 1)).total_seconds()
        before, after = self._offset(t - 86400), self._offset(t + 86400)
        found = sorted({t - off for off in (before, after)
                        if self._offset(t - off) == off})
        return found, before, after

    def utcoffset(self, dt):
        if dt is None:
            return None
        found, before, after = self._instants(dt)
        t = (dt.replace(tzinfo=None, fold=0) - datetime(1970, 1, 1)).total_seconds()
        if found:
            return timedelta(seconds=t - found[min(dt.fold, len(found) - 1)])
        return timedelta(seconds=after if dt.fold else before)

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None if dt is None else time.localtime(
            dt.astimezone(UTC).timestamp()).tm_zone

    def fromutc(self, dt):
        ts = (dt.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds()
        wall = (dt.replace(tzinfo=None) + timedelta(seconds=self._offset(ts)))
        found, _, _ = self._instants(wall)
        fold = 1 if len(found) == 2 and ts == found[1] else 0
        return wall.replace(tzinfo=self, fold=fold)


_LOCAL = _OsZone()


def local_zone() -> tzinfo:
    """The machine's local zone as a DST-aware tzinfo (AU-12)."""
    return _LOCAL
