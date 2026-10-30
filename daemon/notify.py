"""Notification channels: one FILE per bot (docs/automations.md 7, phase 7a).

AC-471..AC-474, AU-19, the owner's ruling R-A: "keep it modular so i can copy
telegram skill and add a different bot to different pc/ users in the future."
So a bot is a file, a kind of service is an adapter, and nothing here knows
what runs an automation.

THE FILE. `<records>/notify/<name>.json` (`paths.Paths.notify`); the stem IS
the channel name, `[a-z0-9-]{1,32}`:

    {"kind": "telegram", "label": "Owner's phone",
     "bot_token": "<digits>:<35+ chars>", "chat_id": "123456789"}

Adding a second bot, or taking this one to another PC, is dropping a file in
that folder -- no code change and no restart: `Channels` re-reads the folder
on every use and re-parses a file only when its stat changed. The settings
page's Add writes the same file, through `memory._atomic`.

THE CLOSED TABLE. `kind` resolves only through `ports.NOTIFIERS` (adapters
register at import), so a file cannot name code. A file that breaks a rule is
NOT dropped: it is listed `configured: false` with `problem` naming the rule,
and a send through it fails with that problem.

SECRECY (AC-471). Every field an adapter declares `secret` in `FIELDS` is
secret (Telegram's `inbound` switch is the one field that is not). A field
with a `default` may be absent from the file. A listing row is exactly name,
kind, label, configured, problem and default;
a problem never quotes the value it judged; and every failure detail an
adapter returns has each secret value scrubbed out here, in `send`, the one
place every send passes through. Nothing in this module logs a title, a text
or a field value.

NO FALLBACK (AC-472). A missing channel is `failed: channel <name> not
found`, a malformed one fails with its problem, and nothing is ever rerouted
to another channel or to the default.

The routes live here too, so `butlerd` wires them with one call:
GET /api/notify, POST /api/notify, POST /api/notify/{name}/test,
DELETE /api/notify/{name}. Unauthenticated, like every route on this daemon
(R2): see daemon/AGENTS.md for what the POST routes expose.
"""
from __future__ import annotations

import asyncio
import collections
import functools
import json
import re
import socket
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from . import adapters  # noqa: F401  (registers every Notifier kind)
from . import memory as memory_mod
from . import ports

NAME = re.compile(r"[a-z0-9-]{1,32}")
NAME_RULE = "the name must be 1-32 of a-z, 0-9 and -"
#: AC-472: at most this many characters of the ANSWER leave the machine per
#: send. It is also the exfiltration bound docs/automations.md 12 names.
ANSWER_CAP = 600
#: The title is one line, bounded for the same reason.
TITLE_CAP = 200
MORE = "\n..."
LABEL_CAP = 80
BASE_FIELDS = ("kind", "label")


class ChannelRefused(ValueError):
    """A channel the page asked to save breaks a rule; the message names it
    and never quotes a secret."""


def clip(text: str) -> str:
    """At most ANSWER_CAP characters, cut at a line end, marked when cut."""
    text = str(text).strip()
    if len(text) <= ANSWER_CAP:
        return text
    head = text[:ANSWER_CAP - len(MORE)]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return head.rstrip() + MORE


def _title(title: str) -> str:
    return " ".join(str(title).split())[:TITLE_CAP]


@dataclass(frozen=True)
class Channel:
    name: str
    kind: str
    label: str
    problem: str
    secrets: tuple          # the values to scrub from any failure detail
    notifier: object | None  # a ports.Notifier, None when `problem`

    def public(self, default: str) -> dict:
        # AC-471: these six keys and no others. A handler that serialised the
        # dataclass instead would ship `secrets` and the notifier's token.
        return {"name": self.name, "kind": self.kind, "label": self.label,
                "configured": not self.problem, "problem": self.problem,
                "default": self.name == default}


def _check(name: str, doc) -> tuple[str, str, str, type | None]:
    """(kind, label, problem, adapter class) for one channel document."""
    if not NAME.fullmatch(name):
        return "", name, NAME_RULE, None
    if not isinstance(doc, dict):
        return "", name, "is not a JSON object", None
    kind = doc.get("kind")
    label = doc.get("label", "")
    label = (" ".join(label.split())[:LABEL_CAP]
             if isinstance(label, str) else "") or name
    cls = ports.NOTIFIERS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        shown = kind[:32] if isinstance(kind, str) else type(kind).__name__
        return "", label, f"unknown kind {shown!r}", None
    known = set(BASE_FIELDS) | {f["key"] for f in cls.FIELDS}
    extra = sorted(k for k in doc if k not in known)
    if extra:
        return kind, label, f"unknown field {extra[0][:32]!r}", None
    return kind, label, cls.problem(doc), cls


def _values(cls, doc: dict) -> dict:
    """The adapter's fields from a checked document: strings stripped, an
    absent field with a `default` given it (an older file, MS-25)."""
    out = {}
    for f in cls.FIELDS:
        value = doc.get(f["key"], f.get("default"))
        out[f["key"]] = value.strip() if isinstance(value, str) else value
    return out


class Channels:
    """The folder of channel files, re-read by stat on every use."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._cache: dict[str, tuple[tuple, Channel]] = {}
        self._lock = threading.Lock()
        # name -> what a running inbound channel reports (`listing`). Written
        # by its owner (the Telegram poller), on the loop; never on disk.
        self.live_problems: dict[str, str] = {}

    # -- reading (blocking: call through asyncio.to_thread on the loop) ---- #
    def all(self) -> list[Channel]:
        with self._lock:
            if not self.folder.is_dir():
                self._cache.clear()
                return []
            found, seen = [], set()
            for path in sorted(self.folder.glob("*.json")):
                try:
                    st = path.stat()
                except FileNotFoundError:
                    continue            # removed between the glob and here
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

    def get(self, name: str) -> Channel | None:
        return next((c for c in self.all() if c.name == name), None)

    def listing(self, default: str) -> list[dict]:
        rows = [c.public(default) for c in self.all()]
        # What a RUNNING channel found out (docs/messaging.md MS-25, AC-532:
        # "another program is reading this bot") is shown as its problem
        # when its file has none. `configured` stays true: it can still SEND.
        for row in rows:
            live = self.live_problems.get(row["name"], "")
            if live and not row["problem"]:
                row["problem"] = live
        return rows

    @staticmethod
    def kinds() -> list[dict]:
        """What the page's Add form offers, from the closed table."""
        return [{"kind": k, "label": cls.LABEL,
                 "fields": [dict(f) for f in cls.FIELDS]}
                for k, cls in sorted(ports.NOTIFIERS.items())]

    @staticmethod
    def _read(path: Path) -> Channel:
        name = path.stem
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as e:
            return Channel(name, "", name,
                           f"cannot be read ({type(e).__name__})", (), None)
        except json.JSONDecodeError as e:
            # The position only: the message's own text quotes nothing, but
            # the document it failed on may hold a token.
            return Channel(name, "", name, f"is not valid JSON (line "
                           f"{e.lineno} column {e.colno})", (), None)
        kind, label, problem, cls = _check(name, doc)
        if problem or cls is None:
            return Channel(name, kind, label, problem, (), None)
        values = _values(cls, doc)
        secrets = tuple(str(values[f["key"]]) for f in cls.FIELDS
                        if f.get("secret"))
        return Channel(name, kind, label, "", secrets, cls(**values))

    # -- writing ------------------------------------------------------------ #
    def save(self, body: dict) -> Channel:
        """Create or replace `<name>.json` from the page's Add form."""
        if not isinstance(body, dict):
            raise ChannelRefused("the body must be a JSON object")
        name = body.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ChannelRefused(NAME_RULE)
        doc = {k: v for k, v in body.items() if k != "name"}
        for k, v in list(doc.items()):
            if isinstance(v, str):
                doc[k] = v.strip()
        kind, label, problem, cls = _check(name, doc)
        if problem or cls is None:
            raise ChannelRefused(problem)
        on_disk = {"kind": kind, "label": label}
        on_disk.update(_values(cls, doc))
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            memory_mod._atomic(self.folder / f"{name}.json",
                               json.dumps(on_disk, indent=2) + "\n")
        return self.get(name)

    def remove(self, name: str) -> None:
        if not NAME.fullmatch(name):
            raise ChannelRefused(NAME_RULE)
        with self._lock:
            path = self.folder / f"{name}.json"
            if not path.is_file():
                raise KeyError(f"channel {name} not found")
            path.unlink()
            # An inbound channel's read position (docs/messaging.md MS-25)
            # goes with it: a new channel of that name is a new reader.
            (self.folder / f"{name}.offset").unlink(missing_ok=True)

    # -- sending ------------------------------------------------------------ #
    async def send(self, name: str, title: str,
                   text: str) -> ports.NotifyResult:
        """One send through one named channel. Never raises for a delivery
        failure, never reroutes (AC-472)."""
        channel = await asyncio.to_thread(self.get, name)
        if channel is None:
            result = ports.NotifyResult(False, f"channel {name} not found")
        elif channel.notifier is None:
            result = ports.NotifyResult(False, channel.problem)
        else:
            result = await channel.notifier.send(_title(title), clip(text))
            detail = result.detail
            for secret in channel.secrets:
                if secret:
                    detail = detail.replace(secret, "<redacted>")
            result = ports.NotifyResult(result.ok, detail)
        if not result.ok:
            print(f"[notify] {name}: failed: {result.detail}", flush=True)
        return result


async def tell_owner(hub, title: str, text: str, *, besides: str) -> None:
    """One line nobody asked for that the owner must hear (docs/messaging.md
    RV-14, AC-601): through the `notify_default` channel, unless that
    channel is of kind `besides` (the very service that stopped), and said
    at the desk through `Hub.announce`, the one door for unsolicited speech.
    Each outcome is logged; nothing is retried or rerouted (AC-472)."""
    name = str(hub.settings.get("notify_default") or "").strip()
    channels = getattr(hub, "channels", None)
    if not name or channels is None:
        print("[notify] no notify_default channel: the owner is told at the "
              "desk only", flush=True)
    else:
        channel = await asyncio.to_thread(channels.get, name)
        if channel is not None and channel.kind == besides:
            print(f"[notify] {name} is a {besides} channel, the one that "
                  f"stopped: not sent there", flush=True)
        else:
            await channels.send(name, title, text)
    said = await hub.announce(text)
    print(f"[notify] {title}: at the desk: {said}", flush=True)


# --------------------------------------------------------------------------- #
# A coding agent's questions on the owner's phone (docs/messaging.md 7)
# --------------------------------------------------------------------------- #
# MS-20..MS-24, AC-525..AC-527, AC-529. The CHANNEL shows a question and says
# which of its messages a reply quoted (the INBOUND capability, ports.py);
# everything else -- which questions go out, what a reply means, and the one
# answer door -- is decided HERE, once, so Telegram's buttons (MS-23) plug in
# as a channel and inherit the same rules.

PHONE_QUESTIONS = "phone_questions"
#: `Questions.asked` origin for a scheduled run: every inbound channel that
#: reaches the owner. A phone turn's origin is its channel's KIND; a desk or
#: page turn's is "" and nothing goes out (MS-21, AC-526).
EVERY_CHANNEL = "*"
#: The methods a reply in a chat can answer. Anything else (pi's `editor` is
#: auto-cancelled before it gets here) is not sent.
PHONE_METHODS = ("select", "confirm", "input")
#: Question messages sent -> the question, IN MEMORY ONLY (daemon/AGENTS.md:
#: nothing about a pending question goes on disk) and bounded. Kept after the
#: question closes, so a late quoted reply is told WHY it was too late
#: (AC-529) instead of becoming a normal message.
SENT_REMEMBERED = 64
CLOSED_LINE = "That question was already closed: {why}"
ANSWERED_LINE = "Answered: {value}"
SELECT_HINT = "Reply with a number from 1 to {n}."
CONFIRM_HINT = "Reply 1 for yes or 2 for no."
INPUT_HINT = "Reply to the question with your answer."
VALUE_CAP = 200
#: What an UNQUOTED message must be, after `_plain`, to be read as an answer
#: at all: a bare number or a bare yes/no in English or Portuguese (MS-22).
BARE_ANSWER = re.compile(r"[0-9]{1,3}|yes|no|sim|nao")
YES = ("1", "yes", "sim")
NO = ("2", "no", "nao")


def _plain(text: str) -> str:
    """Lower case, accents dropped ("nao" for the accented word), outer
    spaces and trailing full stops or bangs removed: how a reply is
    compared."""
    folded = unicodedata.normalize("NFKD", str(text))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return folded.strip().lower().rstrip(".!").strip()


def answer_for(method: str, options, text: str):
    """The `ports.Answer` that `text` gives a question of this shape, or the
    HINT (a str) to say back when it does not fit one. Never guesses: a
    number out of range, or a word that is no option, is a hint."""
    if method == "input":
        value = str(text).strip()
        return ports.Answer(value=value) if value else INPUT_HINT
    word = _plain(text)
    if method == "confirm":
        if word in YES:
            return ports.Answer(confirmed=True)
        if word in NO:
            return ports.Answer(confirmed=False)
        return CONFIRM_HINT
    if method == "select":
        if word.isdigit() and 1 <= int(word) <= len(options):
            return ports.Answer(value=options[int(word) - 1])
        for option in options:
            if _plain(option) == word:
                return ports.Answer(value=option)
        return SELECT_HINT.format(n=len(options))
    raise ValueError(f"no phone answer for a {method!r} question")


def _said(answer: ports.Answer) -> str:
    if answer.confirmed is not None:
        return "yes" if answer.confirmed else "no"
    value = " ".join(str(answer.value).split())
    return value if len(value) <= VALUE_CAP else value[:VALUE_CAP] + "..."


@dataclass(frozen=True)
class _Sent:
    qid: str
    method: str
    options: tuple


class Questions:
    """The owner's phone as a second place to answer an agent (MS-20..24).

    `asked` is called by `Hub._on_ask_human` beside the broadcast and only
    SCHEDULES the send: a slow phone service never holds the turn. Every
    answer goes through `Hub.answer_question`, the one door (AC-529), and
    only an explicit reply answers: nothing here ever answers on its own.
    """

    def __init__(self, hub):
        self.hub = hub
        self._live: dict[str, object] = {}     # kind -> a started channel
        self._sent: collections.OrderedDict = collections.OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    # -- out (MS-21, MS-22) -------------------------------------------------- #
    def asked(self, origin: str, chat: str, ask: ports.Ask) -> None:
        """A question was raised in a turn from `origin` (see EVERY_CHANNEL)."""
        settings = self.hub.settings
        if not origin or settings is None or not settings.get(PHONE_QUESTIONS):
            return
        if ask.method not in PHONE_METHODS:
            print(f"[notify] a {ask.method} question is not sent to the "
                  f"phone: a reply cannot answer it", flush=True)
            return
        kinds = ([k for k, cls in sorted(ports.NOTIFIERS.items())
                  if ports.inbound(cls)] if origin == EVERY_CHANNEL
                 else [origin])
        task = asyncio.get_running_loop().create_task(
            self._send(kinds, chat, ask))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, kinds: list, chat: str, ask: ports.Ask) -> None:
        where = await self._where(chat)
        for kind in kinds:
            try:
                channel = await self._channel(kind)
                if channel is None:
                    continue
                result = await channel.ask(ask, where)
            except Exception as e:                        # noqa: BLE001
                result = ports.NotifyResult(False, f"{type(e).__name__}: {e}")
            if not result.ok:
                print(f"[notify] question {ask.qid} NOT shown on {kind}: "
                      f"{result.detail}", flush=True)
                continue
            self._sent[(kind, result.ref)] = _Sent(ask.qid, ask.method,
                                                   tuple(ask.options))
            while len(self._sent) > SENT_REMEMBERED:
                self._sent.popitem(last=False)

    async def _where(self, chat: str) -> str:
        """The run's or chat's title: what the question is about."""
        brain = self.hub.session_brain()
        if brain is None or not chat:
            return ""
        record = await asyncio.to_thread(brain.session_chat, chat, 1)
        return str((record or {}).get("title") or "")

    async def _channel(self, kind: str):
        """The started channel of this kind to the owner, or None while that
        kind cannot reach them. Asked of the kind on EVERY question, so one
        switched off since (a Telegram channel whose `inbound` went off) is
        sent nothing; started once, on the first question."""
        reaches = ports.NOTIFIERS[kind].own(self.hub.settings)
        if reaches is None:
            return None
        channel = self._live.get(kind)
        if channel is None:
            channel = reaches
            await channel.start(functools.partial(self._incoming, kind))
            self._live[kind] = channel
        return channel

    async def stop(self) -> None:
        live, self._live = self._live, {}
        for channel in live.values():
            await channel.stop()

    # -- in (MS-22, MS-24; AC-527, AC-529) ----------------------------------- #
    async def _incoming(self, kind: str, message: ports.Incoming):
        """None when `message` is not an answer (the channel treats it as a
        normal message), else what to say back to it.

        QUOTED: it answers the question that message showed, open or not.
        UNQUOTED: only a bare number or yes/no, and only while EXACTLY ONE
        question shown on this channel is open -- with two open, "1" could
        confirm the wrong agent's action, which cannot be undone (AC-527)."""
        if message.quoted:
            sent = self._sent.get((kind, message.quoted))
            if sent is None:
                return None
        else:
            if not BARE_ANSWER.fullmatch(_plain(message.text)):
                return None
            open_ = {s.qid: s for (k, _), s in self._sent.items()
                     if k == kind and self.hub.open_question(s.qid)}
            if len(open_) != 1:
                return None
            [sent] = open_.values()
        return await self._answer(sent, message.text)

    async def _answer(self, sent: _Sent, text: str) -> str:
        if self.hub.open_question(sent.qid) is None:
            return CLOSED_LINE.format(why=self.hub.why_closed(sent.qid))
        answer = answer_for(sent.method, sent.options, text)
        if isinstance(answer, str):
            return answer
        got = await self.hub.answer_question(sent.qid, answer)
        if not got.get("ok"):
            return CLOSED_LINE.format(why=got.get("error") or got.get("reason"))
        return ANSWERED_LINE.format(value=_said(answer))


# --------------------------------------------------------------------------- #
# Routes (docs/automations.md 5). Every disk touch through to_thread (D-1).
# --------------------------------------------------------------------------- #
TEST_TITLE = "Butler test"


def add_routes(app: web.Application, channels: Channels, prefs,
               announce) -> None:
    """Wire the four channel routes. `prefs` is the settings store and
    `announce(applied)` the daemon's settings broadcast, so adding the FIRST
    channel sets `notify_default` visibly on every device (AC-474)."""

    async def doc() -> dict:
        items = await asyncio.to_thread(channels.listing,
                                        prefs.get("notify_default"))
        return {"noun": "channel", "items": items, "kinds": channels.kinds()}

    def refused(message: str, status: int = 400) -> web.Response:
        return web.json_response({"error": message}, status=status)

    async def get_notify(_req):
        return web.json_response(await doc())

    async def post_notify(req):
        try:
            body = await req.json()
        except Exception:                                 # noqa: BLE001
            return refused("the body must be JSON")
        try:
            saved = await asyncio.to_thread(channels.save, body)
        except ChannelRefused as e:
            return refused(str(e))
        if not prefs.get("notify_default"):
            await announce(prefs.update({"notify_default": saved.name}))
        return web.json_response(await doc())

    async def post_test(req):
        name = req.match_info["name"]
        if not NAME.fullmatch(name):
            return refused(NAME_RULE)
        if await asyncio.to_thread(channels.get, name) is None:
            return refused(f"channel {name} not found", 404)
        result = await channels.send(
            name, TEST_TITLE,
            f"If you can read this, Butler on {socket.gethostname()} reaches "
            f"this chat through the channel '{name}'.")
        return web.json_response({"ok": result.ok, "detail": result.detail})

    async def delete_notify(req):
        name = req.match_info["name"]
        try:
            await asyncio.to_thread(channels.remove, name)
        except ChannelRefused as e:
            return refused(str(e))
        except KeyError as e:
            return refused(e.args[0], 404)
        return web.json_response(await doc())

    app.router.add_get("/api/notify", get_notify)
    app.router.add_post("/api/notify", post_notify)
    app.router.add_post("/api/notify/{name}/test", post_test)
    app.router.add_delete("/api/notify/{name}", delete_notify)
