"""Telegram: the first `Notifier` (docs/automations.md 7, AC-471, AC-472), and
two-way since T1 (docs/messaging.md section 8, AC-528, AC-530..AC-533).

SENDING is the telegram skill's approach (`tg.py` `api_call`): stdlib
`urllib.request` POSTs JSON to `<API>/bot<token>/<method>`, inside
`asyncio.to_thread` (D-1), with a 15 s timeout. PLAIN TEXT, no `parse_mode`:
a model's Markdown is not Telegram's, and one malformed entity fails the
whole send.

THE URL CARRIES THE TOKEN, so no failure text here is ever built from an
exception string or a URL: an HTTP failure is its status plus Telegram's own
`description`, a timeout says how long it waited, and a network failure names
the OS error and nothing else. `daemon/notify.py` then scrubs every secret
field's value out of whatever detail comes back from a send, and this module
scrubs its own log lines the same way, because `description` is the
service's text and this module cannot promise what it quotes.

TWO-WAY (MS-25). A channel file with `"inbound": true` makes its `chat_id`
the OWNER, and `Inbound` (one per daemon, wired by `butlerd.make_app`) reads
that bot:

- A long-poll `getUpdates` loop on a DAEMON THREAD per poll (never the
  event loop, never the default executor: a 25 s poll there would hold the
  process at exit), with its own timeout longer than a send's, cancelled at
  shutdown. The read position is persisted after every update, BEFORE the
  update is handled, as `<notify>/<name>.offset`: a restart never replays a
  message to an agent that may run with full tools, and never skips one
  (AC-531). The first start of a bot answers no backlog: it records where
  Telegram is and starts from there (the trigger sources' first-IMAP-poll
  rule).
- OWNER ONLY (AC-530): an update is used only when BOTH its chat id and its
  sender id are the owner's; everything else is dropped with a log line that
  names no content, no id and no token.
- A 409 (another program reads this bot -- the owner's own telegram skill
  is exactly that case) is the channel's `problem` and the poll backs off
  (`CONFLICT_BACKOFF_S`), never a tight loop (AC-532).
- One inbound channel at a time: with two, neither is read and both say why.
- An owner message goes to `phone.PhoneTurns` (kind "telegram"), the same
  machinery WhatsApp uses (AC-533): it offers the message to the question
  rules (`notify.Questions`, through `self.questions`) and else makes it a
  turn. Its queue file resumes, once the poll is running again, what a
  restart interrupted (RV-13, AC-599).
- QUESTIONS (MS-23, AC-528): confirm and select are inline keyboards, input
  is a forced reply. The callback data is a short random TOKEN plus the
  option's number, mapped IN MEMORY to the question (64-byte limit; nothing
  about a pending question goes on disk). A tap is acknowledged
  (`answerCallbackQuery`) with what it came to, and the question message is
  edited to say it, buttons removed.

`API` is module-level so a test points it at a local fake Bot API; nothing in
the suite reaches api.telegram.org.
"""
from __future__ import annotations

import asyncio
import collections
import http.client
import json
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from daemon import artifacts, phone, ports

API = "https://api.telegram.org"
#: AC-472's number: a local model can be slow, a notification service is not,
#: and a run never waits on it anyway -- it is sent after the run is recorded.
TIMEOUT = 15.0
#: An upload or a download of a file may take longer than a text send.
FILE_TIMEOUT = 120.0
#: docs/rich-chat.md O-1: (kind, media type) -> (Bot API method, upload field).
_FILE_METHODS = {
    **{("image", t): ("sendPhoto", "photo")
       for t in ("image/jpeg", "image/png", "image/webp")},
    ("video", "video/mp4"): ("sendVideo", "video"),
    ("audio", "audio/mpeg"): ("sendAudio", "audio"),
    ("audio", "audio/mp4"): ("sendAudio", "audio"),
}
#: How long Telegram holds one `getUpdates` open when nothing arrives.
POLL_S = 25
#: Our own deadline for one long poll: the server's hold plus slack, and
#: longer than a send's, so a quiet chat is never read as a dead service.
POLL_TIMEOUT = POLL_S + 10.0
#: After a failed poll (network, 5xx), the next waits this long, growing.
RETRY_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
#: After a 409 the next poll waits this long, growing (AC-532): another
#: program holds the bot, and asking again at once only fights it.
CONFLICT_BACKOFF_S = (30.0, 60.0, 120.0, 300.0)
#: How often the channel files are re-read for an inbound switch.
RESCAN_S = 5.0

KIND = "telegram"
#: One Telegram text message.
TEXT_LIMIT = 4096
#: The Bot API's own `getFile` limit for a bot.
MAX_FILE_MB = 20
CONFLICT = ("another program is reading this bot: use a separate bot for "
            "Butler")
TOO_MANY = "only one Telegram channel may have inbound on; {n} do"
UNKNOWN_TAP = "That question is no longer known to Butler."
#: Kept by name for its tests; the line and the reply are `phone`'s.
UNSUPPORTED = phone.UNSUPPORTED
REPLY_TEXT = "Reply to this message with your answer."
#: Question tokens remembered (MS-23), like `notify.SENT_REMEMBERED`.
TOKENS_REMEMBERED = 64
#: Telegram caps a callback answer's text.
ACK_CAP = 200

_TOKEN = re.compile(r"\d+:[A-Za-z0-9_-]{35,}")
_CHAT = re.compile(r"-?\d{1,20}")
_REASON_CAP = 200
_FLAGS = {"true": True, "on": True, "yes": True, "1": True,
          "false": False, "off": False, "no": False, "0": False, "": False}


def _one_line(text: str) -> str:
    return " ".join(str(text).split())[:_REASON_CAP]


def _flag(value):
    """True/False for a JSON bool or the Add form's text, else None."""
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return _FLAGS.get(value.strip().lower())
    return None


class TelegramError(phone.ChannelError):
    """A Bot API call did not do what was asked; the message is the status
    and Telegram's own description, never a URL or an exception string."""


class _Bot:
    """One bot's Bot API calls, BLOCKING (call them off the loop)."""

    def __init__(self, token: str, api: str | None, timeout: float):
        self.token = token
        self.api = api
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.api or API}/{path}"

    def call(self, method: str, payload: dict, *, timeout: float | None = None,
             upload: tuple[str, str, bytes] | None = None
             ) -> tuple[int, object, str]:
        """(status, result, "") on success, (status, None, reason) otherwise;
        status 0 when no answer came. `upload` is (field, filename, bytes),
        sent as multipart with `payload` as its text fields."""
        wait = self.timeout if timeout is None else timeout
        if upload is None:
            data = json.dumps(payload).encode()
            ctype = "application/json"
        else:
            data, ctype = _multipart(payload, upload)
        req = urllib.request.Request(
            self._url(f"bot{self.token}/{method}"), data=data,
            headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, None, _http_failure(e)
        except urllib.error.URLError as e:
            return 0, None, _network_failure(e.reason, wait)
        except (OSError, http.client.HTTPException) as e:
            return 0, None, _network_failure(e, wait)
        try:
            answer = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return status, None, (f"{status} answered with a body that is "
                                  f"not JSON")
        if isinstance(answer, dict) and answer.get("ok") is True:
            return status, answer.get("result"), ""
        why = answer.get("description") if isinstance(answer, dict) else ""
        return status, None, (f"{status} "
                              f"{_one_line(why or 'the service said not ok')}")

    def download(self, file_path: str) -> bytes:
        """The bytes of a file `getFile` named. Raises `phone.MediaError`."""
        url = self._url(f"file/bot{self.token}/{file_path}")
        try:
            with urllib.request.urlopen(url, timeout=FILE_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise phone.MediaError(f"the download answered {e.code}") from None
        except urllib.error.URLError as e:
            raise phone.MediaError(
                _network_failure(e.reason, FILE_TIMEOUT)) from None
        except (OSError, http.client.HTTPException) as e:
            raise phone.MediaError(_network_failure(e, FILE_TIMEOUT)) from None


def _multipart(fields: dict, upload: tuple[str, str, bytes]
               ) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        text = value if isinstance(value, str) else json.dumps(value)
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{name}\"\r\n\r\n{text}\r\n").encode()
    field, filename, data = upload
    out += (f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _http_failure(e: urllib.error.HTTPError) -> str:
    why = ""
    try:
        answer = json.loads(e.read().decode("utf-8"))
        if isinstance(answer, dict):
            why = answer.get("description") or ""
    except (OSError, UnicodeDecodeError, ValueError):
        why = ""
    return f"{e.code} {_one_line(why or e.reason or '')}".strip()


def _network_failure(reason, timeout: float) -> str:
    if isinstance(reason, TimeoutError):
        return f"timed out after {timeout:g} s"
    if isinstance(reason, OSError) and reason.strerror:
        return f"network error: {_one_line(reason.strerror)}"
    return f"network error: {type(reason).__name__}"


@ports.register(ports.NOTIFIERS, "telegram")
class TelegramNotifier:
    LABEL = "Telegram"
    #: What a channel file of this kind holds beyond `kind` and `label`. The
    #: token and the chat id are SECRET: a chat id plus a leaked token is the
    #: whole bot (AC-471), so neither is ever served, and the page's inputs
    #: for them are write-only. `inbound` is a switch, absent in older files.
    FIELDS = (
        {"key": "bot_token", "label": "Bot token", "secret": True,
         "help": "From @BotFather: digits, a colon, then 35 or more "
                 "characters."},
        {"key": "chat_id", "label": "Chat id", "secret": True,
         "help": "The number of the chat the bot writes to; negative for a "
                 "group."},
        {"key": "inbound", "label": "Inbound (true or false)",
         "secret": False, "default": False,
         "help": "true to talk to Butler from this chat: the chat id must "
                 "be your own user id, and no other program may read this "
                 "bot."},
    )

    #: The optional inbound capability (docs/messaging.md MS-20): `own`
    #: answers the running `Inbound`, which does start/stop/ask.
    INBOUND = True

    @classmethod
    def problem(cls, doc: dict) -> str:
        """"" when `doc` can build a channel, else the rule it breaks. Never
        quotes the value it judged. `inbound` is written back into `doc` as
        a bool, which is what `Channels.save` then stores (the
        `notify_whatsapp` precedent)."""
        token = doc.get("bot_token")
        if not isinstance(token, str) or not token.strip():
            return "bot_token is missing"
        if not _TOKEN.fullmatch(token.strip()):
            return "bot_token is not <digits>:<35+ chars>"
        chat = doc.get("chat_id")
        if isinstance(chat, int) and not isinstance(chat, bool):
            chat = str(chat)
        if not isinstance(chat, str) or not chat.strip():
            return "chat_id is missing"
        if not _CHAT.fullmatch(chat.strip()):
            return "chat_id is not a number"
        inbound = _flag(doc.get("inbound"))
        if inbound is None:
            return "inbound must be true or false"
        if inbound and chat.strip().startswith("-"):
            return ("inbound needs the owner's own chat: chat_id must be a "
                    "user id, not a group")
        if "inbound" in doc:
            doc["inbound"] = inbound
        return ""

    @classmethod
    def own(cls, settings):
        """The running inbound reader, or None while no channel is read --
        including during a 409, when the OTHER program would get the taps."""
        live = _live
        return (live if live is not None and live.polling
                and not live.conflicted else None)

    def __init__(self, bot_token: str, chat_id, inbound=False, *,
                 api: str | None = None, timeout: float = TIMEOUT):
        self._token = str(bot_token).strip()
        self._chat = str(chat_id).strip()
        self.inbound = bool(_flag(inbound))
        self._api = api
        self.timeout = timeout

    def bot(self) -> _Bot:
        return _Bot(self._token, self._api, self.timeout)

    @property
    def owner(self) -> str:
        return self._chat

    @property
    def secrets(self) -> tuple[str, str]:
        return (self._token, self._chat)

    async def send(self, title: str, text: str) -> ports.NotifyResult:
        body = f"{title}\n\n{text}" if title else text
        _status, _result, why = await asyncio.to_thread(
            self.bot().call, "sendMessage",
            {"chat_id": self._chat, "text": body})
        return ports.NotifyResult(not why, why)


# --------------------------------------------------------------------------- #
# Two-way: the inbound reader (docs/messaging.md section 8)
# --------------------------------------------------------------------------- #
_live: "Inbound | None" = None


def serve(inbound: "Inbound") -> None:
    """Make `inbound` what `TelegramNotifier.own` answers (the question
    channel). Called by `make_app` beside `whatsapp.serve`."""
    global _live
    _live = inbound


def withdraw(inbound: "Inbound") -> None:
    global _live
    if _live is inbound:
        _live = None


async def _on_daemon_thread(fn):
    """`fn()` on a DAEMON thread, awaited. A cancelled await abandons
    the thread, which ends with its own timeout; unlike the default
    executor, nothing waits for it at interpreter exit."""
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def deliver(ok: bool, value) -> None:
        if done.done():
            return
        if ok:
            done.set_result(value)
        else:
            done.set_exception(value)

    def run() -> None:
        try:
            value, ok = fn(), True
        except BaseException as e:                        # noqa: BLE001
            value, ok = e, False
        try:
            loop.call_soon_threadsafe(deliver, ok, value)
        except RuntimeError:
            pass                    # the loop is closed: nobody is waiting

    threading.Thread(target=run, daemon=True, name="telegram-poll").start()
    return await done


class Inbound:
    """The daemon's one reader of the inbound Telegram channel (MS-25).

    Lifecycle: `open()` starts a supervisor that re-reads the channel files
    every `rescan_s` and polls the ONE channel with `inbound` on, replacing
    the poll when that file changes; `close()` stops both. Capability (for
    `notify.Questions`): `start(on_message)`, `stop()`, `ask(question,
    where)`. Transport (for `phone.PhoneTurns`): `reply`, `presence`,
    `media`. `turns` is set by `butlerd.telegram_inbound`."""

    def __init__(self, channels, *, rescan_s: float = RESCAN_S,
                 poll_s: float = POLL_S,
                 retry_backoff_s: tuple = RETRY_BACKOFF_S,
                 conflict_backoff_s: tuple = CONFLICT_BACKOFF_S):
        self.channels = channels
        self.rescan_s = rescan_s
        self.poll_s = poll_s
        self.retry_backoff_s = retry_backoff_s
        self.conflict_backoff_s = conflict_backoff_s
        self.turns = None                  # phone.PhoneTurns
        self.questions = None              # notify.Questions' hook
        self.polling = ""                  # the channel name being read
        self.conflicted = False            # another program has the bot
        self._notifier: TelegramNotifier | None = None
        self._name = ""
        self._bot: _Bot | None = None
        self._supervisor: asyncio.Task | None = None
        self._poll: asyncio.Task | None = None
        self._problems: set[str] = set()   # names whose problem we set
        # token -> (question message id, its text); message id -> token.
        # IN MEMORY ONLY (MS-23) and bounded, like `notify.Questions._sent`.
        self._tokens: collections.OrderedDict = collections.OrderedDict()
        self._by_message: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------- #
    async def open(self) -> None:
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.get_running_loop().create_task(
                self._supervise())

    async def close(self) -> None:
        task, self._supervisor = self._supervisor, None
        await _cancel(task)
        await self._halt()

    async def _supervise(self) -> None:
        while True:
            try:
                found = await asyncio.to_thread(self._inbound_channels)
                await self._adopt(found)
            except asyncio.CancelledError:
                raise
            except Exception as e:                        # noqa: BLE001
                self._log(f"reading the channel files failed: "
                          f"{type(e).__name__}")
            await asyncio.sleep(self.rescan_s)

    def _inbound_channels(self) -> list:
        return [c for c in self.channels.all()
                if c.kind == KIND and c.notifier is not None
                and c.notifier.inbound]

    async def _adopt(self, found: list) -> None:
        if len(found) > 1:
            await self._halt()
            for c in found:
                self._problem(c.name, TOO_MANY.format(n=len(found)))
            return
        if not found:
            await self._halt()
            return
        [c] = found
        if c.notifier is self._notifier and self._poll is not None \
                and not self._poll.done():
            return
        await self._halt()
        self._notifier, self._name = c.notifier, c.name
        self._bot = c.notifier.bot()
        self._poll = asyncio.get_running_loop().create_task(
            self._read(c.name, self._bot))

    async def _halt(self) -> None:
        task, self._poll = self._poll, None
        await _cancel(task)
        self.polling = ""
        self.conflicted = False
        self._notifier, self._name, self._bot = None, "", None
        for name in self._problems:
            self.channels.live_problems.pop(name, None)
        self._problems.clear()

    def _problem(self, name: str, text: str) -> None:
        if text:
            self.channels.live_problems[name] = text
            self._problems.add(name)
        else:
            self.channels.live_problems.pop(name, None)
            self._problems.discard(name)

    def _log(self, text: str) -> None:
        for secret in (self._notifier.secrets if self._notifier else ()):
            if secret:
                text = text.replace(secret, "<redacted>")
        print(f"[telegram] {self._name or '-'}: {text}", file=sys.stderr,
              flush=True)

    # -- the read position (AC-531) ----------------------------------------- #
    def _offset_path(self, name: str) -> Path:
        return Path(self.channels.folder) / f"{name}.offset"

    def _read_offset(self, name: str, bot: _Bot) -> int | None:
        """The persisted offset for THIS bot, None when there is none. A file
        written for another bot (the token changed) is not this bot's."""
        path = self._offset_path(name)
        if not path.is_file():
            return None
        doc = json.loads(path.read_text("utf-8"))
        if doc.get("bot") != bot.token.split(":", 1)[0]:
            return None
        return int(doc["offset"])

    def _write_offset(self, name: str, bot: _Bot, offset: int) -> None:
        from daemon import memory as memory_mod
        memory_mod._atomic(self._offset_path(name), json.dumps(
            {"bot": bot.token.split(":", 1)[0], "offset": offset}) + "\n")

    # -- the long poll (AC-531, AC-532) ------------------------------------- #
    async def _read(self, name: str, bot: _Bot) -> None:
        try:
            offset = await asyncio.to_thread(self._read_offset, name, bot)
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._problem(name, f"{name}.offset cannot be read "
                                f"({type(e).__name__}); delete it to start "
                                f"from now")
            return
        failures = conflicts = 0
        while offset is None:
            # A first start answers no backlog: ask for the LAST update only,
            # and start after it (MS-25).
            status, result, why = await _on_daemon_thread(
                lambda: bot.call("getUpdates", {"offset": -1, "timeout": 0}))
            if why:
                delay = self._failed(name, status, why, failures, conflicts)
                failures += status != 409
                conflicts += status == 409
                await asyncio.sleep(delay)
                continue
            offset = (result[-1]["update_id"] + 1) if result else 0
            await asyncio.to_thread(self._write_offset, name, bot, offset)
        self._problem(name, "")
        self.polling = name
        # Before the first new update: what a restart left waiting runs
        # first, and the one in flight is told (RV-13, AC-599, AC-600).
        if self.turns is not None:
            await self.turns.resume()
        failures = conflicts = 0
        while True:
            status, result, why = await _on_daemon_thread(
                lambda: bot.call(
                    "getUpdates",
                    {"offset": offset, "timeout": self.poll_s,
                     "allowed_updates": ["message", "callback_query"]},
                    timeout=self.poll_s + (POLL_TIMEOUT - POLL_S)))
            if why:
                delay = self._failed(name, status, why, failures, conflicts)
                failures += status != 409
                conflicts += status == 409
                await asyncio.sleep(delay)
                continue
            if failures or conflicts:
                self._log("reading again")
            failures = conflicts = 0
            self.conflicted = False
            self._problem(name, "")
            for update in result or ():
                uid = int(update.get("update_id", -1))
                if uid < offset:
                    continue
                # PERSISTED FIRST: a restart while this update is handled
                # loses it rather than running it twice (AC-531).
                offset = uid + 1
                await asyncio.to_thread(self._write_offset, name, bot, offset)
                try:
                    await self._update(update)
                except asyncio.CancelledError:
                    raise
                except Exception as e:                    # noqa: BLE001
                    self._log(f"an update failed: {type(e).__name__}")

    def _failed(self, name: str, status: int, why: str, failures: int,
                conflicts: int) -> float:
        """Say what went wrong once, and answer how long to wait."""
        if status == 409:
            if not conflicts:
                self._log(f"409: {CONFLICT}")
            self._problem(name, CONFLICT)
            self.conflicted = True
            table = self.conflict_backoff_s
            return table[min(conflicts, len(table) - 1)]
        if not failures:
            self._log(f"reading failed: {why}")
        table = self.retry_backoff_s
        return table[min(failures, len(table) - 1)]

    # -- one update (AC-530) ------------------------------------------------ #
    def _is_owner(self, chat_id, from_id) -> bool:
        owner = self._notifier.owner if self._notifier else ""
        return bool(owner) and str(chat_id) == owner and str(from_id) == owner

    async def _update(self, update: dict) -> None:
        if "callback_query" in update:
            await self._tap(update["callback_query"])
            return
        msg = update.get("message")
        if not isinstance(msg, dict):
            self._log("skipped an update that is not a message")
            return
        if not self._is_owner((msg.get("chat") or {}).get("id"),
                              (msg.get("from") or {}).get("id")):
            self._log("dropped an update from another chat or sender")
            return
        message = self._message(msg)
        if self.turns is None:
            self._log("an owner message arrived and nothing routes it")
            return
        await self.turns.message(message)

    @property
    def owner_chat(self) -> str:
        return self._notifier.owner if self._notifier else ""

    def _message(self, msg: dict) -> phone.Message:
        """Telegram's message as a channel-neutral one. Neither text nor a
        file Butler reads (a sticker, a location) is `phone.UNSUPPORTED_KIND`
        media, which `PhoneTurns` answers with the fixed line."""
        text = str(msg.get("text") or msg.get("caption") or "")
        media = None
        for key, kind in (("voice", "audio"), ("audio", "audio"),
                          ("video", "video"), ("video_note", "video"),
                          ("document", "document")):
            if isinstance(msg.get(key), dict):
                media = self._media(kind, msg[key])
                break
        else:
            photos = msg.get("photo")
            if isinstance(photos, list) and photos:
                largest = max(photos, key=lambda p: (
                    p.get("file_size") or 0,
                    (p.get("width") or 0) * (p.get("height") or 0)))
                media = self._media("image", largest)
        if media is None and not text:
            media = phone.Media(kind=phone.UNSUPPORTED_KIND, ref="")
        reply_to = msg.get("reply_to_message")
        quoted = ""
        if isinstance(reply_to, dict):
            replied = str(reply_to.get("message_id"))
            # A reply to a question carries its token; a reply to anything
            # else still QUOTES something, so it is never read as an
            # unquoted bare answer (AC-527's rule).
            quoted = self._by_message.get(replied, f"message:{replied}")
        return phone.Message(where=self.owner_chat,
                             id=str(msg.get("message_id")), text=text,
                             media=media, quoted=quoted)

    @staticmethod
    def _media(kind: str, f: dict) -> phone.Media:
        size = int(f.get("file_size") or 0)
        seconds = f.get("duration")
        return phone.Media(kind=kind, ref=str(f.get("file_id") or ""),
                           size=size,
                           too_large=size > MAX_FILE_MB * 1024 * 1024,
                           file_name=str(f.get("file_name") or ""),
                           seconds=None if seconds is None else float(seconds))

    # -- questions (MS-23, AC-528) ------------------------------------------ #
    async def start(self, on_message) -> None:
        self.questions = on_message

    async def stop(self) -> None:
        self.questions = None

    async def ask(self, question: ports.Ask,
                  where: str) -> ports.NotifyResult:
        bot = self._bot
        if bot is None:
            return ports.NotifyResult(False, "no Telegram channel has "
                                             "inbound on")
        token = secrets.token_hex(4)
        text = question_text(question, where)
        if question.method == "input":
            markup = {"force_reply": True}
        elif question.method == "confirm":
            markup = {"inline_keyboard": [[
                {"text": "Yes", "callback_data": f"{token} 1"},
                {"text": "No", "callback_data": f"{token} 2"}]]}
        else:
            markup = {"inline_keyboard": [
                [{"text": option, "callback_data": f"{token} {i}"}]
                for i, option in enumerate(question.options, 1)]}
        _status, result, why = await asyncio.to_thread(
            bot.call, "sendMessage", {"chat_id": self.owner_chat,
                                      "text": text, "reply_markup": markup})
        if why:
            return ports.NotifyResult(False, self._scrub(why))
        mid = str(result["message_id"])
        self._tokens[token] = (mid, text)
        self._by_message[mid] = token
        while len(self._tokens) > TOKENS_REMEMBERED:
            _old, (old_mid, _t) = self._tokens.popitem(last=False)
            self._by_message.pop(old_mid, None)
        return ports.NotifyResult(True, "", token)

    async def _tap(self, cq: dict) -> None:
        """A button press: answered through the question rules, acknowledged,
        and the question message edited to say what it came to."""
        message = cq.get("message") or {}
        if not self._is_owner((message.get("chat") or {}).get("id"),
                              (cq.get("from") or {}).get("id")):
            self._log("dropped an update from another chat or sender")
            return
        token, _, number = str(cq.get("data") or "").partition(" ")
        # Read BEFORE the await: an `ask` meanwhile may evict the token.
        shown = self._tokens.get(token)
        said = None
        if shown is not None and self.questions is not None:
            try:
                said = await self.questions(ports.Incoming(number, token))
            except Exception as e:                        # noqa: BLE001
                said = phone.FAILED.format(why=e)
        await self._call("answerCallbackQuery", {
            "callback_query_id": str(cq.get("id")),
            "text": (said or UNKNOWN_TAP)[:ACK_CAP]})
        if said is not None:
            mid, text = shown
            await self._call("editMessageText", {
                "chat_id": self.owner_chat, "message_id": int(mid),
                "text": f"{text}\n\n{said}"})

    async def _call(self, method: str, payload: dict) -> None:
        """A courtesy call (acknowledge, edit): a failure is logged."""
        bot = self._bot
        if bot is None:
            return
        _status, _result, why = await asyncio.to_thread(bot.call, method,
                                                        payload)
        if why:
            self._log(f"{method} failed: {why}")

    # -- the transport phone.PhoneTurns uses (AC-533) ----------------------- #
    def _scrub(self, text: str) -> str:
        for secret in (self._notifier.secrets if self._notifier else ()):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _require(self) -> _Bot:
        if self._bot is None:
            raise TelegramError("no Telegram channel has inbound on")
        return self._bot

    async def reply(self, where: str, *, text: str | None = None,
                    quote: str | None = None,
                    voice: bytes | None = None,
                    file: tuple[str, bytes] | None = None,
                    caption: str | None = None) -> str:
        bot = self._require()
        payload: dict = {"chat_id": where}
        if quote:
            payload["reply_parameters"] = {"message_id": int(quote),
                                           "allow_sending_without_reply": True}
        if file is not None:
            # docs/rich-chat.md O-1: the method follows the BYTES, never the name.
            name, data = file
            kind, media = artifacts.classify(data[:4096], name)
            method, field = _FILE_METHODS.get(
                (kind, media), ("sendDocument", "document"))
            if caption:
                payload["caption"] = caption
            _status, result, why = await asyncio.to_thread(
                lambda: bot.call(method, payload, timeout=FILE_TIMEOUT,
                                 upload=(field, name, data)))
        elif voice is None:
            payload["text"] = text
            _status, result, why = await asyncio.to_thread(
                bot.call, "sendMessage", payload)
        else:
            _status, result, why = await asyncio.to_thread(
                lambda: bot.call("sendVoice", payload, timeout=FILE_TIMEOUT,
                                 upload=("voice", "voice.ogg", voice)))
        if why:
            raise TelegramError(self._scrub(why))
        return str((result or {}).get("message_id") or "")

    async def presence(self, where: str, state: str) -> None:
        """Telegram shows typing for about five seconds per call and has no
        "stopped": only "composing" sends anything."""
        if state != "composing":
            return
        bot = self._require()
        _status, _result, why = await asyncio.to_thread(
            bot.call, "sendChatAction", {"chat_id": where,
                                         "action": "typing"})
        if why:
            raise TelegramError(self._scrub(why))

    async def media(self, ref: str) -> bytes:
        bot = self._require()
        _status, result, why = await asyncio.to_thread(
            bot.call, "getFile", {"file_id": ref})
        if why:
            raise phone.MediaError(self._scrub(why))
        path = str((result or {}).get("file_path") or "")
        if not path:
            raise phone.MediaError("Telegram named no file to download")
        return await asyncio.to_thread(bot.download, path)


async def _cancel(task) -> None:
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def question_text(question: ports.Ask, where: str) -> str:
    """MS-23: "Butler needs you (<run or chat>): <question>". The choices
    are buttons; an input says how to answer."""
    head = f"Butler needs you ({where}): " if where else "Butler needs you: "
    asked = "\n".join(x for x in (question.title, question.message) if x)
    text = head + asked
    return f"{text}\n{REPLY_TEXT}" if question.method == "input" else text
