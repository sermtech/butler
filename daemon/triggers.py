"""Event triggers: mail, calendar and webhook SOURCES that start automations.
docs/proactive.md section 7 -- PR-26..PR-30, AC-496..AC-499.

A SOURCE IS A FILE (PR-26, `notify.py`'s pattern copied, not shared):
`<records>/triggers/<name>.json` (`paths.Paths.triggers`), the stem is the
name, `[a-z0-9-]{1,32}`:

    {"kind": "imap", "label": "Accountant mail", "host": "imap.gmail.com",
     "port": "993", "user": "me@gmail.com", "password": "<app password>",
     "folder": "INBOX", "from_contains": "accountant", ...}

`kind` resolves only through the CLOSED table `KINDS` (imap, ics, webhook),
so a file cannot name code. Sources are not a port: nothing outside this
module polls, and a seventh port needs plan.md 4.10 first. A file that
breaks a rule is listed `configured: false` with `problem` naming the rule;
it is never dropped and never polled. `Sources` re-reads the folder on every
use and re-parses a file only when its stat changed.

SECRETS (AU-19's ruling, extended here). `connections.py` is the only module
that reads a secret from the environment or pi's registry; an IMAP app
password, a private calendar URL and a webhook token are stored in plain text
in their source file -- the exposure a channel's bot token already has
(docs/automations.md 12) -- and read only here. A listing row is name, kind,
label, configured, problem and hook; no value a kind marks `secret` is ever
served, logged or quoted in a problem, and every failure detail has each
secret scrubbed out in `_scrub`, the one place every failure passes through.
Failure text is built from the exception's TYPE and the server's own words,
never from a URL.

KINDS (PR-27).
- imap: TLS on 993 only. The FIRST poll of a mailbox (or after its
  UIDVALIDITY changed) records the highest UID and fires nothing, so linking
  an inbox of 3,000 messages never floods (AC-497). After that each new UID
  that passes the filters fires once, keyed `<uidvalidity>:<uid>`. Messages
  are read with BODY.PEEK, so nothing is marked read.
- ics: the URL is a secret (a private calendar link is a credential). Each
  occurrence -- RRULE expanded by `recurring-ical-events` (O-3) -- fires ONCE
  at its start minus `lead_minutes`, keyed `<UID>|<start>`; the keys fired
  are kept in the cursor file, so a restart never fires one twice. An
  occurrence whose start has passed is marked and never fired late.
- webhook: `POST /api/hooks/<name>` with header `X-Butler-Token`, compared in
  constant time against a 32-byte token generated on create and shown ONCE
  (the create answer). Missing or wrong: 401 and nothing recorded. A JSON
  body of at most 16 KB (413 past it). At most 10 events a minute per
  source; the rest are held IN MEMORY and coalesced into ONE event when the
  minute frees (AC-498). The route is on every listener: other programs
  call it, and the token is its guard, not the listener.

AN EVENT IS DATA (PR-29). A source hands `Scheduler.fire` `{source, key,
text}`; the text reaches a prompt only through `library.expand`'s `{event}`,
labelled as data, and the chat it births gets read-only tools whatever the
Tools setting says (`automations.from_event`, O-2).

POLLERS (PR-30). One `Triggers` task, started and stopped beside the
Scheduler; every network and disk touch through `asyncio.to_thread` (D-1).
A failing source shows `last check failed: ...` as its problem and messages
the channels of the automations bound to it once per 24 h (PR-7's policy).
The `triggers_enabled` setting pauses every poll and refuses every hook.
"""
from __future__ import annotations

import asyncio
import collections
import email
import email.policy
import hmac
import imaplib
import json
import re
import secrets
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from aiohttp import web

from . import memory as memory_mod

UTC = timezone.utc
NAME = re.compile(r"[a-z0-9-]{1,32}")
NAME_RULE = "the name must be 1-32 of a-z, 0-9 and -"
LABEL_CAP = 80
BASE_FIELDS = ("kind", "label")
#: How often the task looks at every source. A source polls on its own
#: `poll_minutes`; a calendar's fire times are checked on every loop, so a
#: 15-minute lead is kept to within this, not to within a poll.
LOOP_S = 30
#: PR-30, PR-7's policy: at most one message per failing source per day.
ALERT_EVERY = timedelta(hours=24)
DETAIL_CAP = 200
#: PR-27's bounds.
TEXT_CHARS = 2000                 # of an email's text part / an event's notes
IMAP_TIMEOUT_S = 30.0
IMAP_NEW_MAX = 50                 # new UIDs read per poll; older ones counted
IMAP_FIRE_MAX = 10                # events per poll; the rest coalesced
FETCH_TIMEOUT_S = 15.0
ICS_MAX_BYTES = 5 * 1024 * 1024
ICS_AHEAD = timedelta(days=2)     # how far ahead a poll expands occurrences
ICS_KEEP = timedelta(days=2)      # fired keys are forgotten this long after
HOOK_MAX_BYTES = 16 * 1024
HOOK_PER_MINUTE = 10
HOOK_WINDOW = timedelta(minutes=1)
#: Bodies kept for the one coalesced event; past this they are only counted.
HOOK_HELD_KEEP = 20
TOKEN_BYTES = 32
HOOK_HEADER = "X-Butler-Token"
OFF = "triggers are off (Settings, Triggers)"
ENABLED = "triggers_enabled"


class SourceRefused(ValueError):
    """A source the page asked to save breaks a rule; the message names it
    and never quotes a secret."""


class SourceFailed(RuntimeError):
    """A poll or a check that did not work. The message is built here from
    the error's type and the server's own words, and is scrubbed again by
    the caller before it is shown or sent."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse(text) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def _whole(doc: dict, key: str, minimum: int,
           maximum: int = 10_080) -> int | str:
    """`doc[key]` as a whole number in range, or the problem naming it."""
    raw = str(doc.get(key, "")).strip()
    if not raw.isdigit() or not minimum <= int(raw) <= maximum:
        return f"{key} is a whole number from {minimum} to {maximum}"
    return int(raw)


def _clean(text: str) -> bool:
    """No control characters: a value that reaches a protocol line."""
    return not any(ord(c) < 32 or ord(c) == 127 for c in text)


# --------------------------------------------------------------------------- #
# The kinds (PR-27): a CLOSED table, each a set of fields and three verbs
# --------------------------------------------------------------------------- #
class ImapKind:
    LABEL = "Mail (IMAP)"
    FIELDS = (
        {"key": "host", "label": "IMAP server, e.g. imap.gmail.com"},
        {"key": "port", "label": "port (993, TLS)"},
        {"key": "user", "label": "user (the mail address)"},
        {"key": "password", "label": "app password", "secret": True,
         "help": "An app password, not your account password."},
        {"key": "folder", "label": "folder (INBOX)"},
        {"key": "from_contains", "label": "from contains (optional)"},
        {"key": "subject_contains", "label": "subject contains (optional)"},
        {"key": "poll_minutes", "label": "check every N minutes (5, min 2)"},
    )
    STORED = ()
    DEFAULTS = {"port": "993", "folder": "INBOX", "poll_minutes": "5",
                "from_contains": "", "subject_contains": ""}

    @staticmethod
    def problem(doc: dict) -> str:
        host = str(doc.get("host", ""))
        if not host or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            return "host is a server name like imap.gmail.com"
        if str(doc.get("port", "")).strip() != "993":
            return "port is 993: only IMAP over TLS is supported"
        if not str(doc.get("user", "")) or not _clean(str(doc["user"])):
            return "user is the mail address to log in as"
        if not str(doc.get("password", "")):
            return "password is needed (an app password)"
        folder = str(doc.get("folder", ""))
        if not folder or not _clean(folder) or len(folder) > 200:
            return "folder is a mailbox name like INBOX"
        for key in ("from_contains", "subject_contains"):
            if not _clean(str(doc.get(key, ""))):
                return f"{key} is one line of text"
        got = _whole(doc, "poll_minutes", 2)
        return got if isinstance(got, str) else ""

    @staticmethod
    def poll_minutes(doc: dict) -> int:
        return int(doc["poll_minutes"])

    @staticmethod
    def _open(doc: dict):
        """Connected and logged in, the folder selected read-only; answers
        `(connection, uidvalidity)`. Every failure is a `SourceFailed`."""
        host, port = doc["host"], int(doc["port"])
        try:
            conn = IMAP_OPEN(host, port)
        except ssl.SSLError as e:
            raise SourceFailed(f"the TLS handshake with {host}:{port} failed "
                               f"({type(e).__name__})") from None
        except OSError as e:
            raise SourceFailed(f"could not reach {host}:{port} "
                               f"({type(e).__name__})") from None
        try:
            conn.login(doc["user"], doc["password"])
            folder = '"' + doc["folder"].replace("\\", "\\\\").replace(
                '"', '\\"') + '"'
            typ, _ = conn.select(folder, readonly=True)
            if typ != "OK":
                _close(conn)
                raise SourceFailed(f"the folder {doc['folder']} could not be "
                                   f"opened")
            _, got = conn.response("UIDVALIDITY")
            uidvalidity = int(got[0]) if got and got[0] else 0
        except imaplib.IMAP4.error as e:
            _close(conn)
            said = e.args[0] if e.args else ""
            if isinstance(said, bytes):
                said = said.decode("utf-8", "replace")
            raise SourceFailed("the mail server refused: "
                               + " ".join(str(said).split())[:160]) from None
        except (OSError, ValueError) as e:
            _close(conn)
            raise SourceFailed(f"the mail server answered badly "
                               f"({type(e).__name__})") from None
        return conn, uidvalidity

    @classmethod
    def check(cls, doc: dict) -> str:
        conn, _ = cls._open(doc)
        _close(conn)
        return f"logged in and opened {doc['folder']}"

    @classmethod
    def poll(cls, name: str, doc: dict, cursor: dict, now: datetime,
             tz) -> tuple[list[dict], dict]:
        conn, uidvalidity = cls._open(doc)
        try:
            last = cursor.get("last")
            if cursor.get("uidvalidity") != uidvalidity or \
                    not isinstance(last, int):
                # AC-497: the first poll (or a mailbox whose UIDs were reset)
                # only learns where "now" is. Nothing fires.
                uids = _uids(conn, "ALL")
                return [], {**cursor, "uidvalidity": uidvalidity,
                            "last": max(uids, default=0)}
            new = sorted(u for u in _uids(conn, f"UID {last + 1}:*")
                         if u > last)
            if not new:
                return [], cursor
            unread = max(0, len(new) - IMAP_NEW_MAX)
            matched = []
            for uid in new[-IMAP_NEW_MAX:]:
                mail = _message(conn, uid)
                if mail is not None and _passes(doc, mail):
                    matched.append((uid, mail))
            if unread:
                print(f"[butler] triggers: {name}: {unread} older new "
                      f"messages were not read (at most {IMAP_NEW_MAX} a "
                      f"poll)", flush=True)
            one = IMAP_FIRE_MAX if len(matched) <= IMAP_FIRE_MAX \
                else IMAP_FIRE_MAX - 1
            events = [{"source": name, "key": f"{uidvalidity}:{uid}",
                       "text": mail["text"]} for uid, mail in matched[:one]]
            rest = matched[one:]
            if rest:
                # A flood is ONE more run, not one per message.
                lines = [f"{len(rest)} more matching messages arrived at "
                         f"once; their senders and subjects:"]
                lines += [f"- {m['from']}: {m['subject']}" for _, m in rest]
                events.append({"source": name,
                               "key": f"{uidvalidity}:{rest[0][0]}-"
                                      f"{rest[-1][0]}",
                               "text": "\n".join(lines)})
            return events, {**cursor, "uidvalidity": uidvalidity,
                            "last": new[-1]}
        except imaplib.IMAP4.error as e:
            said = e.args[0] if e.args else ""
            if isinstance(said, bytes):
                said = said.decode("utf-8", "replace")
            raise SourceFailed("the mail server refused: "
                               + " ".join(str(said).split())[:160]) from None
        except OSError as e:
            raise SourceFailed(f"the connection to {doc['host']} broke "
                               f"({type(e).__name__})") from None
        finally:
            _close(conn)

    @staticmethod
    def due(name, doc, cursor, now, tz):
        return [], cursor           # mail fires only on a poll


def _imap_ssl(host: str, port: int):
    return imaplib.IMAP4_SSL(host, port,
                             ssl_context=ssl.create_default_context(),
                             timeout=IMAP_TIMEOUT_S)


#: The connection factory, at module level so a test points it at a local
#: fake server speaking plain IMAP (the TLS layer is the stdlib's own).
IMAP_OPEN = _imap_ssl


def _close(conn) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _uids(conn, criterion: str) -> list[int]:
    typ, data = conn.uid("SEARCH", None, criterion)
    if typ != "OK":
        raise SourceFailed("the mail server refused a search")
    return [int(x) for x in b" ".join(d for d in data if d).split()]


def _message(conn, uid: int) -> dict | None:
    """From, subject, date and the first `TEXT_CHARS` of the text part, or
    None for a UID that vanished between the search and the fetch."""
    typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
    raw = next((d[1] for d in data or () if isinstance(d, tuple)), None)
    if typ != "OK" or raw is None:
        return None
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain",))
    try:
        body = part.get_content() if part is not None else ""
    except (LookupError, ValueError) as e:
        body = f"(the text could not be decoded: {type(e).__name__})"
    sender = " ".join(str(msg.get("From", "")).split())
    subject = " ".join(str(msg.get("Subject", "")).split())
    when = " ".join(str(msg.get("Date", "")).split())
    text = (f"From: {sender}\nSubject: {subject}\nDate: {when}\n\n"
            f"{str(body).strip()[:TEXT_CHARS]}")
    return {"from": sender, "subject": subject, "text": text}


def _passes(doc: dict, mail: dict) -> bool:
    for key, field in (("from_contains", "from"),
                       ("subject_contains", "subject")):
        want = str(doc.get(key, "")).strip().casefold()
        if want and want not in mail[field].casefold():
            return False
    return True


class IcsKind:
    LABEL = "Calendar (.ics link)"
    FIELDS = (
        {"key": "url", "label": "calendar link (.ics, private)",
         "secret": True,
         "help": "The calendar's secret address in iCal format."},
        {"key": "lead_minutes", "label": "minutes before each event (15)"},
        {"key": "poll_minutes", "label": "re-read every N minutes (15)"},
    )
    STORED = ()
    DEFAULTS = {"lead_minutes": "15", "poll_minutes": "15"}

    @staticmethod
    def problem(doc: dict) -> str:
        url = str(doc.get("url", ""))
        if not re.fullmatch(r"(https?|webcal)://[^\s]{1,2000}", url):
            return "url is the calendar's http(s) or webcal link"
        for key, low in (("lead_minutes", 0), ("poll_minutes", 2)):
            got = _whole(doc, key, low, 1440)
            if isinstance(got, str):
                return got
        return ""

    @staticmethod
    def poll_minutes(doc: dict) -> int:
        return int(doc["poll_minutes"])

    @staticmethod
    def _fetch(doc: dict):
        import icalendar                    # O-3: needed only here
        url = doc["url"]
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        req = urllib.request.Request(url, headers={"User-Agent": "Butler"})
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
                raw = r.read(ICS_MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise SourceFailed(f"the calendar answered HTTP {e.code}") \
                from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise SourceFailed(f"could not reach the calendar "
                               f"({type(reason).__name__})") from None
        if len(raw) > ICS_MAX_BYTES:
            raise SourceFailed(f"the calendar is larger than "
                               f"{ICS_MAX_BYTES // (1024 * 1024)} MB")
        try:
            return icalendar.Calendar.from_ical(raw)
        except ValueError as e:
            raise SourceFailed(f"the link did not answer a calendar "
                               f"({type(e).__name__})") from None

    @classmethod
    def check(cls, doc: dict) -> str:
        cal = cls._fetch(doc)
        n = len(cal.walk("VEVENT"))
        return f"read the calendar: {n} events"

    @classmethod
    def poll(cls, name, doc, cursor, now, tz):
        import recurring_ical_events        # O-3
        cal = cls._fetch(doc)
        lead = timedelta(minutes=int(doc["lead_minutes"]))
        try:
            found = recurring_ical_events.of(cal).between(
                now, now + ICS_AHEAD + lead)
        except (ValueError, TypeError, KeyError) as e:
            raise SourceFailed(f"the calendar's events could not be read "
                               f"({type(e).__name__})") from None
        upcoming = []
        for ev in found:
            if ev.get("DTSTART") is None:
                continue
            start = _instant(ev.get("DTSTART").dt, tz)
            if start <= now:
                continue                     # already under way
            uid = str(ev.get("UID", "")) or str(ev.get("SUMMARY", ""))
            upcoming.append({"key": f"{uid}|{_iso(start)}",
                             "start": _iso(start),
                             "fire": _iso(start - lead),
                             "text": _event_text(ev, start, tz)})
        upcoming.sort(key=lambda o: o["fire"])
        return cls.due(name, doc, {**cursor, "upcoming": upcoming}, now, tz)

    @staticmethod
    def due(name, doc, cursor, now, tz):
        """Every occurrence whose fire time has come fires ONCE (AC-497);
        one whose start has passed is marked without firing."""
        fired = dict(cursor.get("fired") or {})
        events, keep = [], []
        for occ in cursor.get("upcoming") or ():
            start, fire = _parse(occ["start"]), _parse(occ["fire"])
            if start is None or fire is None or start <= now:
                if occ["key"] not in fired:
                    fired[occ["key"]] = _iso(now)
                continue
            if occ["key"] in fired:
                keep.append(occ)
                continue
            if fire <= now:
                fired[occ["key"]] = _iso(now)
                events.append({"source": name, "key": occ["key"],
                               "text": occ["text"]})
            keep.append(occ)
        fired = {k: v for k, v in fired.items()
                 if (_parse(k.rpartition("|")[2]) or now) > now - ICS_KEEP}
        return events, {**cursor, "upcoming": keep, "fired": fired}


def _instant(value, tz) -> datetime:
    """An event time as an aware instant: a whole day is local midnight, a
    floating time is local."""
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else \
            value.replace(tzinfo=tz).astimezone(UTC)
    if isinstance(value, date):
        return datetime.combine(value, time(0), tz).astimezone(UTC)
    raise SourceFailed("an event has no start time")


def _event_text(ev, start: datetime, tz) -> str:
    local = start.astimezone(tz)
    lines = [f"Event: {' '.join(str(ev.get('SUMMARY', '')).split())}",
             f"Starts: {local:%a %d %b %Y %H:%M}"]
    end = ev.get("DTEND")
    if end is not None:
        lines.append(f"Ends: {_instant(end.dt, tz).astimezone(tz):%H:%M}")
    where = " ".join(str(ev.get("LOCATION", "")).split())
    if where:
        lines.append(f"Location: {where}")
    notes = str(ev.get("DESCRIPTION", "")).strip()
    if notes:
        lines += ["", notes[:TEXT_CHARS]]
    return "\n".join(lines)


class WebhookKind:
    LABEL = "Webhook (another program calls Butler)"
    FIELDS = ()
    #: Generated by `Sources.save`, never typed or served after creation.
    STORED = ({"key": "token", "secret": True},)
    DEFAULTS = {}

    @staticmethod
    def problem(doc: dict) -> str:
        if not re.fullmatch(r"[0-9a-f]{%d}" % (2 * TOKEN_BYTES),
                            str(doc.get("token", ""))):
            return (f"token is {TOKEN_BYTES} bytes as hex; add the source "
                    f"again to get a new one")
        return ""

    @staticmethod
    def check(doc: dict) -> str:
        return (f"ready: POST JSON to its hook with the {HOOK_HEADER} "
                f"header")


#: PR-26: THE closed table. A file names a key here or it is not usable.
KINDS = {"imap": ImapKind, "ics": IcsKind, "webhook": WebhookKind}


# --------------------------------------------------------------------------- #
# The folder of source files (copied from `notify.Channels`)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Source:
    name: str
    kind: str
    label: str
    problem: str
    secrets: tuple           # the values to scrub from any failure detail
    doc: dict | None         # the checked document, None when `problem`

    def public(self, failing: str = "") -> dict:
        # AC-496: these keys and no others. `secrets` and `doc` never leave.
        shown = self.problem or (f"last check failed: {failing}"
                                 if failing else "")
        row = {"name": self.name, "kind": self.kind, "label": self.label,
               "configured": not self.problem, "problem": shown}
        if self.kind == "webhook":
            row["hook"] = f"/api/hooks/{self.name}"
        return row


def _fields(cls) -> tuple:
    return tuple(cls.FIELDS) + tuple(cls.STORED)


def _check(name: str, doc) -> tuple[str, str, str, type | None]:
    """(kind, label, problem, kind class) for one source document."""
    if not NAME.fullmatch(name):
        return "", name, NAME_RULE, None
    if not isinstance(doc, dict):
        return "", name, "is not a JSON object", None
    kind = doc.get("kind")
    label = doc.get("label", "")
    label = (" ".join(label.split())[:LABEL_CAP]
             if isinstance(label, str) else "") or name
    cls = KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        shown = kind[:32] if isinstance(kind, str) else type(kind).__name__
        return "", label, f"unknown kind {shown!r}", None
    known = set(BASE_FIELDS) | {f["key"] for f in _fields(cls)}
    extra = sorted(k for k in doc if k not in known)
    if extra:
        return kind, label, f"unknown field {extra[0][:32]!r}", None
    return kind, label, cls.problem({**cls.DEFAULTS, **doc}), cls


class Sources:
    """The folder of source files, re-read by stat on every use."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._cache: dict[str, tuple[tuple, Source]] = {}
        self._lock = threading.Lock()

    def all(self) -> list[Source]:
        with self._lock:
            if not self.folder.is_dir():
                self._cache.clear()
                return []
            found, seen = [], set()
            for path in sorted(self.folder.glob("*.json")):
                try:
                    st = path.stat()
                except FileNotFoundError:
                    continue
                stamp = (st.st_mtime_ns, st.st_size)
                cached = self._cache.get(path.name)
                if cached is None or cached[0] != stamp:
                    cached = (stamp, self._read(path))
                    self._cache[path.name] = cached
                seen.add(path.name)
                found.append(cached[1])
            for gone in set(self._cache) - seen:
                del self._cache[gone]
            return found

    def get(self, name: str) -> Source | None:
        return next((s for s in self.all() if s.name == name), None)

    def names(self) -> set[str]:
        """Every source file's name, usable or not: what an automation may
        be bound to (`AutomationStore.sources`)."""
        return {s.name for s in self.all()}

    @staticmethod
    def kinds() -> list[dict]:
        """What the page's Add form offers: only the TYPED fields."""
        return [{"kind": k, "label": cls.LABEL,
                 "fields": [dict(f) for f in cls.FIELDS]}
                for k, cls in sorted(KINDS.items())]

    @staticmethod
    def _read(path: Path) -> Source:
        name = path.stem
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as e:
            return Source(name, "", name,
                          f"cannot be read ({type(e).__name__})", (), None)
        except json.JSONDecodeError as e:
            return Source(name, "", name, f"is not valid JSON (line "
                          f"{e.lineno} column {e.colno})", (), None)
        kind, label, problem, cls = _check(name, doc)
        if problem or cls is None:
            return Source(name, kind, label, problem, (), None)
        values = {**cls.DEFAULTS, **{k: str(v).strip() for k, v in
                                     doc.items() if k not in BASE_FIELDS}}
        hidden = tuple(values[f["key"]] for f in _fields(cls)
                       if f.get("secret") and values.get(f["key"]))
        return Source(name, kind, label, "", hidden, values)

    def save(self, body: dict) -> tuple[Source, str]:
        """Create or replace `<name>.json`; answers the source and, for a
        webhook, its NEW token -- the one time it is shown (PR-27)."""
        if not isinstance(body, dict):
            raise SourceRefused("the body must be a JSON object")
        name = body.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise SourceRefused(NAME_RULE)
        doc = {k: (v.strip() if isinstance(v, str) else v)
               for k, v in body.items() if k != "name"}
        cls = KINDS.get(doc.get("kind")) if isinstance(doc.get("kind"),
                                                       str) else None
        token = ""
        if cls is not None:
            if "token" in doc and cls is WebhookKind:
                raise SourceRefused("a webhook's token is generated by "
                                    "Butler, never given")
            for k, v in cls.DEFAULTS.items():
                if not str(doc.get(k, "")).strip():
                    doc[k] = v
            if cls is WebhookKind:
                token = secrets.token_hex(TOKEN_BYTES)
                doc["token"] = token
        kind, label, problem, cls = _check(name, doc)
        if problem or cls is None:
            raise SourceRefused(problem)
        on_disk = {"kind": kind, "label": label}
        on_disk.update({f["key"]: str(doc.get(f["key"], ""))
                        for f in _fields(cls)})
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            memory_mod._atomic(self.folder / f"{name}.json",
                               json.dumps(on_disk, indent=2) + "\n")
        return self.get(name), token

    def remove(self, name: str) -> None:
        if not NAME.fullmatch(name):
            raise SourceRefused(NAME_RULE)
        with self._lock:
            path = self.folder / f"{name}.json"
            if not path.is_file():
                raise KeyError(f"trigger source {name} not found")
            path.unlink()


def _scrub(source: Source, text: str) -> str:
    """Every secret of `source` out of `text`, then one line, capped. THE
    place every failure detail passes through before it is shown, logged
    or sent (AC-496)."""
    for value in source.secrets:
        if value:
            text = text.replace(value, "<redacted>")
    return " ".join(str(text).split())[:DETAIL_CAP]


# --------------------------------------------------------------------------- #
# The poller, the hook and the cursors (PR-27, PR-30)
# --------------------------------------------------------------------------- #
class Triggers:
    """One task over every source. `tick(now)` is one pass; tests call it
    with an instant they chose, as the scheduler's tests call `tick()`."""

    def __init__(self, sources: Sources, cursors: Path, scheduler, channels,
                 settings, tz, clock=utcnow, loop_s: float = LOOP_S):
        self.sources = sources
        self.cursors = Path(cursors)
        self.scheduler = scheduler
        self.channels = channels
        self.settings = settings
        self.tz = tz
        self.clock = clock
        self.loop_s = loop_s
        self._next_poll: dict[str, datetime] = {}
        self._task: asyncio.Task | None = None
        # The webhook's minute (AC-498): accepted call times, and the bodies
        # past the limit waiting to be coalesced. IN MEMORY: a restart drops
        # what was held, and says so in the log.
        self._hook_times: dict[str, collections.deque] = {}
        self._hook_held: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    # -- lifetime ----------------------------------------------------------- #
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):         # noqa: BLE001
            pass
        self._task = None
        held = sum(len(v) for v in self._hook_held.values())
        if held:
            print(f"[butler] triggers: {held} held webhook calls were "
                  f"dropped at stop", flush=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:                            # noqa: BLE001
                # Loud and never fatal (D-1): the next pass retries.
                print(f"[butler] triggers: the pass failed: {e!r}",
                      flush=True)
            await asyncio.sleep(self.loop_s)

    def _on(self) -> bool:
        return bool(self.settings.get(ENABLED))

    # -- cursors (blocking) ------------------------------------------------- #
    def cursor(self, name: str) -> dict:
        try:
            doc = json.loads((self.cursors / f"{name}.json")
                             .read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(doc, dict):
            raise ValueError(f"the cursor of {name} is not an object")
        return doc

    def _write_cursor(self, name: str, doc: dict) -> None:
        with self._lock:
            self.cursors.mkdir(parents=True, exist_ok=True)
            memory_mod._atomic(self.cursors / f"{name}.json",
                               json.dumps(doc, indent=1,
                                          ensure_ascii=False) + "\n")

    def forget(self, name: str) -> None:
        """A replaced or removed source starts from nothing: a new mailbox's
        UIDs say nothing about the old one's."""
        with self._lock:
            (self.cursors / f"{name}.json").unlink(missing_ok=True)
        self._next_poll.pop(name, None)
        self._hook_times.pop(name, None)
        self._hook_held.pop(name, None)

    # -- the listing -------------------------------------------------------- #
    def listing(self) -> list[dict]:
        out = []
        for s in self.sources.all():
            failing = ""
            if not s.problem:
                failing = (self.cursor(s.name).get("status") or {}).get(
                    "error", "")
            out.append(s.public(failing))
        return out

    # -- one pass ----------------------------------------------------------- #
    async def tick(self, now: datetime | None = None) -> list[dict]:
        """Poll what is due, fire what came due, flush what the webhook
        minute held. Answers the run rows written."""
        now = self.clock() if now is None else now
        if not self._on():
            return []
        written = []
        for s in await asyncio.to_thread(self.sources.all):
            if s.problem or s.doc is None:
                continue
            if s.kind == "webhook":
                written += await self._flush(s, now)
                continue
            written += await self._pass(s, now)
        return written

    async def _pass(self, s: Source, now: datetime) -> list[dict]:
        kind = KINDS[s.kind]
        cursor = await asyncio.to_thread(self.cursor, s.name)
        polling = now >= self._next_poll.get(s.name, now)
        try:
            if polling:
                self._next_poll[s.name] = now + timedelta(
                    minutes=kind.poll_minutes(s.doc))
                events, after = await asyncio.to_thread(
                    kind.poll, s.name, s.doc, cursor, now, self.tz)
            else:
                events, after = kind.due(s.name, s.doc, cursor, now, self.tz)
        except SourceFailed as e:
            await self._failed(s, cursor, _scrub(s, str(e)), now)
            return []
        written = []
        for event in events:
            written += await self.scheduler.fire(s.name, event)
        status = after.get("status") or {}
        if polling and status.get("error"):
            after = {**after, "status": {}}       # it works again
        if after != cursor:
            await asyncio.to_thread(self._write_cursor, s.name, after)
        return written

    async def _failed(self, s: Source, cursor: dict, detail: str,
                      now: datetime) -> None:
        """PR-30: shown as the source's problem; told once per 24 h to the
        channels of the automations bound to it (PR-7's policy)."""
        status = dict(cursor.get("status") or {})
        last = _parse(status.get("alertAt"))
        alert = last is None or now - last >= ALERT_EVERY
        status.update(error=detail, errorAt=_iso(now))
        if alert:
            status["alertAt"] = _iso(now)
        await asyncio.to_thread(self._write_cursor, s.name,
                                {**cursor, "status": status})
        print(f"[butler] triggers: {s.name} failed: {detail}", flush=True)
        if not alert or self.channels is None:
            return
        names = await asyncio.to_thread(self._channels_for, s.name)
        for channel in names:
            await self.channels.send(
                channel, f"Trigger {s.label}: failing",
                f"Butler could not check {s.label} ({s.kind}): {detail}. "
                f"Automations started by it will not run until it works; "
                f"you will hear about this at most once a day.")

    def _channels_for(self, name: str) -> list[str]:
        store = self.scheduler.store
        seen: list[str] = []
        for a in store.automations():
            if a.get("source") == name and a.get("enabled"):
                seen += [c for c in a.get("channels") or () if c not in seen]
        return seen

    # -- the page's "send test" -------------------------------------------- #
    async def check(self, name: str) -> dict:
        s = await asyncio.to_thread(self.sources.get, name)
        if s is None:
            raise KeyError(f"trigger source {name} not found")
        if s.problem:
            return {"ok": False, "detail": s.problem}
        try:
            detail = await asyncio.to_thread(KINDS[s.kind].check, s.doc)
        except SourceFailed as e:
            return {"ok": False, "detail": _scrub(s, str(e))}
        return {"ok": True, "detail": _scrub(s, detail)}

    # -- the webhook (AC-498) ----------------------------------------------- #
    def authorised(self, name: str, token: str) -> Source | None:
        """The webhook source `name` when `token` is its token, compared in
        constant time; None for an unknown name, another kind, a broken
        file or a wrong token alike, so a caller learns nothing by trying."""
        s = self.sources.get(name) if NAME.fullmatch(name) else None
        if s is None or s.kind != "webhook" or s.doc is None:
            hmac.compare_digest(token.encode(), b"0" * 2 * TOKEN_BYTES)
            return None
        if not hmac.compare_digest(token.encode("utf-8", "replace"),
                                   s.doc["token"].encode()):
            return None
        return s

    async def hook(self, s: Source, body, now: datetime | None = None
                   ) -> dict:
        """One accepted call: an event now, or held past 10 a minute."""
        now = self.clock() if now is None else now
        text = json.dumps(body, indent=1, ensure_ascii=False)
        times = self._hook_times.setdefault(s.name, collections.deque())
        while times and now - times[0] >= HOOK_WINDOW:
            times.popleft()
        if len(times) >= HOOK_PER_MINUTE:
            held = self._hook_held.setdefault(s.name, [])
            held.append(text if len(held) < HOOK_HELD_KEEP else "")
            return {"ok": True, "coalesced": True, "runs": 0}
        times.append(now)
        runs = await self.scheduler.fire(s.name, {
            "source": s.name, "key": "hook-" + secrets.token_hex(8),
            "text": text})
        return {"ok": True, "coalesced": False, "runs": len(runs)}

    async def _flush(self, s: Source, now: datetime) -> list[dict]:
        held = self._hook_held.get(s.name)
        if not held:
            return []
        times = self._hook_times.setdefault(s.name, collections.deque())
        while times and now - times[0] >= HOOK_WINDOW:
            times.popleft()
        if len(times) >= HOOK_PER_MINUTE:
            return []
        self._hook_held[s.name] = []
        times.append(now)
        bodies = [b for b in held if b]
        text = (f"{len(held)} calls arrived past the limit of "
                f"{HOOK_PER_MINUTE} a minute and were coalesced into this "
                f"one event. The first {len(bodies)} bodies, oldest "
                f"first:\n" + "\n---\n".join(bodies))
        return await self.scheduler.fire(s.name, {
            "source": s.name, "key": "hook-" + secrets.token_hex(8),
            "text": text})


# --------------------------------------------------------------------------- #
# Routes (docs/automations.md 5). Every disk touch through to_thread (D-1).
# --------------------------------------------------------------------------- #
def add_routes(app: web.Application, triggers: Triggers) -> None:
    """GET/POST /api/triggers, POST /api/triggers/{name}/test, DELETE
    /api/triggers/{name}: the settings group's collection (AC-496, AC-41).
    Unauthenticated like every settings route (R2). POST /api/hooks/{name}:
    on EVERY listener, guarded by the source's token (AC-498)."""

    async def doc() -> dict:
        items = await asyncio.to_thread(triggers.listing)
        return {"noun": "trigger source", "items": items,
                "kinds": triggers.sources.kinds()}

    def refused(message: str, status: int = 400) -> web.Response:
        return web.json_response({"error": message}, status=status)

    async def get_triggers(_req):
        return web.json_response(await doc())

    async def post_triggers(req):
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            return refused("the body must be JSON")
        try:
            saved, token = await asyncio.to_thread(triggers.sources.save,
                                                   body)
        except SourceRefused as e:
            return refused(str(e))
        await asyncio.to_thread(triggers.forget, saved.name)
        answer = await doc()
        if token:
            # SHOWN ONCE (PR-27): this answer only, never a GET. The row's
            # label carries it too, because the settings page draws rows
            # and nothing else from this answer.
            hook = f"/api/hooks/{saved.name}"
            answer["created"] = {"name": saved.name, "hook": hook,
                                 "header": HOOK_HEADER, "token": token}
            for row in answer["items"]:
                if row["name"] == saved.name:
                    row["label"] = (f"{row['label']} -- copy now, shown "
                                    f"once: POST {hook} with header "
                                    f"{HOOK_HEADER}: {token}")
        return web.json_response(answer)

    async def post_test(req):
        name = req.match_info["name"]
        if not NAME.fullmatch(name):
            return refused(NAME_RULE)
        try:
            return web.json_response(await triggers.check(name))
        except KeyError as e:
            return refused(e.args[0], 404)

    async def delete_trigger(req):
        name = req.match_info["name"]
        try:
            await asyncio.to_thread(triggers.sources.remove, name)
        except SourceRefused as e:
            return refused(str(e))
        except KeyError as e:
            return refused(e.args[0], 404)
        await asyncio.to_thread(triggers.forget, name)
        return web.json_response(await doc())

    async def post_hook(req):
        token = req.headers.get(HOOK_HEADER, "")
        s = await asyncio.to_thread(triggers.authorised,
                                    req.match_info["name"], token)
        if s is None:
            return refused(f"missing or wrong {HOOK_HEADER}", 401)
        if req.content_length is not None and \
                req.content_length > HOOK_MAX_BYTES:
            return refused(f"the body is over {HOOK_MAX_BYTES} bytes", 413)
        raw = await req.content.read(HOOK_MAX_BYTES + 1)
        if len(raw) > HOOK_MAX_BYTES:
            return refused(f"the body is over {HOOK_MAX_BYTES} bytes", 413)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return refused("the body must be JSON")
        if not triggers._on():
            return refused(OFF, 503)
        return web.json_response(await triggers.hook(s, body), status=202)

    app.router.add_get("/api/triggers", get_triggers)
    app.router.add_post("/api/triggers", post_triggers)
    app.router.add_post("/api/triggers/{name}/test", post_test)
    app.router.add_delete("/api/triggers/{name}", delete_trigger)
    app.router.add_post("/api/hooks/{name}", post_hook)
