"""The daemon's half of WhatsApp: own the sidecar, and be its only client.

docs/messaging.md MS-2..MS-6, AC-500, AC-501, AC-503, AC-505 (phase M2). The
sidecar itself is `daemon/engines/whatsapp/wa-sidecar.mjs` (a transport: the
socket, linking, the archive, the send guard); this module starts it, talks
to it, restarts it when it dies, and turns its pairing state into the one
generic document the settings page renders.

THE KOKORO PATTERN, ONE SEAM OVER (`daemon/adapters/tts_kokoro.py`)
-------------------------------------------------------------------
Spawned by this daemon, `childproc.assign`ed in the same function right after
the spawn (AC-36, D-24), bound to 127.0.0.1:0 with the port read back from
its FIRST stdout line, every later line of both streams drained to the
daemon's log with a `[whatsapp]` prefix (a full pipe blocks node; a dropped
stack trace is the only evidence of why linking failed), and stopped by
`taskkill /F /T` from `butlerd._restart_cleanup` (restart AND shutdown) and
from `make_app`'s cleanup.

DIFFERENCES FROM KOKORO, AND WHY
--------------------------------
- STARTED ONLY WHEN `whatsapp_enabled` IS ON (MS-2), and started or stopped
  when that setting changes, without a restart. Off means not spawned.
- A PER-BOOT RANDOM TOKEN (AC-500). Every request carries `X-Butler-Token`;
  the sidecar answers 401 without it. Loopback is not a boundary on a
  machine that runs a tailnet proxy, and the sidecar holds a live link to
  the user's WhatsApp.
- RESTARTED WITH BACKOFF when it dies (MS-2): each restart is logged; after
  `MAX_RESTARTS` crashes in a row it stays down and says so (`failed`), so a
  sidecar that dies at boot cannot loop. A run that lasted `STABLE_S` resets
  the count. Turning the switch off and on again starts afresh.
- THE CONFIG IS PUSHED (`POST /config`) at every start and on every change
  of the three settings it carries (MS-14a). A refused push at start fails
  that start: a sidecar answering people on a stale list is worse than none.

Nothing blocking runs on the event loop: spawning and killing go through
`asyncio.to_thread`, HTTP through aiohttp (D-1, D-25).

THE AGENT'S READ AND SEND TOOLS (phases M4 and M5, MS-7..MS-13, MS-18b OUT)
--------------------------------------------------------------------------
- READING (AC-508, AC-509) never touches the sidecar: `Archive` opens
  `whatsapp.db` READ-ONLY (`mode=ro`) and ports an earlier prototype's store read API
  (`whatsapp-store.js`: listContacts, listChats, readChat, searchMessages,
  resolveRecipient, resolveChat) with its semantics, so a name matching zero
  or several chats resolves to NO jid and names the candidates. The sidecar
  is the archive's only writer (MS-1).
- SENDING has ONE door, `WhatsApp.send` (MS-10): the loopback route, the
  agent tools and the `whatsapp` notification channel all reach the sidecar's
  guard through it, so the rate limit, the confirmation and the audit apply
  to every one of them. The send switch is the `whatsapp_send` SETTING, read
  at each send and never taken from a caller (AC-513).
- A FILE an agent sends must lie inside the conversation's project folder or
  the data root (`file_inside`, AC-562). The door itself never opens a path a
  caller names: the tool reads the bytes and uploads them.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import aiohttp

from daemon import childproc
from daemon.containment import (FILE_RULE, RECORDS_RULE, FileRefused,
                                RecordsRefused, file_inside, records_dirs)
from daemon import phone
# A guest's standing chat is marked on the record itself (RV-21).
from daemon.chats import GUEST_ORIGIN, is_guest_chat  # noqa: F401
# Moved to the channel-neutral module with two-way Telegram (T1); kept as
# names here because this module raises and tests them.
from daemon.phone import FAILED, MAX_WAITING, MediaError  # noqa: F401

# Windows only, and 0 elsewhere so the kwarg is always legal. Both node and
# `taskkill` are console programs: a daemon with no console of its own gets
# one ALLOCATED for each, which the owner sees as a black window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "daemon" / "engines" / "whatsapp" / "wa-sidecar.mjs"

ENABLE_KEY = "whatsapp_enabled"
#: settings key -> the sidecar's `/config` field (wa-sidecar.mjs header).
CONFIG_FIELDS = {"whatsapp_answer_chats": "answer",
                 "whatsapp_answer_all": "answer_everyone",
                 "whatsapp_group_trigger": "trigger"}

BOOT_TIMEOUT_S = 30.0       # node up and its port printed
REQUEST_TIMEOUT_S = 20.0    # one call into the sidecar (QR render included)
BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
MAX_RESTARTS = 5
STABLE_S = 120.0

TOKEN_HEADER = "X-Butler-Token"
# The /events stream (M6): reconnect delays while the child is still up, and
# the longest line read (a 65,536-character message escaped as JSON fits).
EVENTS_BACKOFF_S = (1.0, 2.0, 5.0)
EVENT_LINE_MAX = 1 << 20
#: How often the link is read while the sidecar runs (RV-13, RV-14): linked
#: means the owner's waiting messages can be resumed; logged out or replaced
#: means WhatsApp stopped (`STOP_REASONS`).
LINK_POLL_S = 15.0
#: The sidecar's terminal pairing states (MS-6: it does not reconnect from
#: these), in the words the owner is told (RV-14, AC-601).
STOP_REASONS = {
    "logged_out": "the phone logged Butler out (Linked devices)",
    "replaced": "the link was opened by another program, which closed "
                "Butler's",
    "forbidden": "WhatsApp refused the link (403); the account may be "
                 "restricted",
}

READ_KEY = "whatsapp_read"
SEND_KEY = "whatsapp_send"
#: The recipient that means "the user's own chat" (`wa.py me`, the default
#: notification target). Resolved by the door from the linked account.
ME = "me"
#: What marks a message a Butler sent, for `Archive.is_known` (RV-20): the
#: sidecar's id prefix (RV-12) and the text marker U+2063 (MS-15). KEEP EQUAL
#: to BUTLER_ID_PREFIX and MARKER in whatsapp-inbound.js (a test reads both).
BUTLER_ID_PREFIX = "3EB0B0B7E1A5"
MARKER = "\u2063"
#: One file per send, as the sidecar (MS-10, AC-515).
MAX_FILE_BYTES = 100 * 1024 * 1024
#: A send uploads to WhatsApp's servers before the sidecar answers; a 100 MB
#: video takes minutes. Cut short, "not sent" would be a lie the caller acts
#: on by sending again.
SEND_TIMEOUT_S = 600.0


def sidecar_argv() -> list[str]:
    """The command that runs the sidecar. A function, so a test can point
    the owner at a fake sidecar honouring the same contract."""
    node = shutil.which("node")
    if node is None:
        raise SidecarError("node is not on PATH; the WhatsApp service needs "
                           "Node 22 or later")
    return [node, str(SIDECAR)]


class SidecarError(RuntimeError):
    """The sidecar would not start, or answered with something unusable."""


class Unavailable(phone.ChannelError):
    """No sidecar to talk to right now. `poll` says whether that is about to
    change on its own (starting, restarting) or needs the user (off, failed)."""

    def __init__(self, reason: str, poll: bool):
        super().__init__(reason)
        self.reason = reason
        self.poll = poll


class ReplyFailed(phone.ChannelError):
    """A reply, or the typing mark, did not go out; the message says why."""


class SendUncertain(RuntimeError):
    """The sidecar took a send and never answered: it may have gone out.
    Never reported as "not sent", because that invites a second send."""


# --------------------------------------------------------------------------- #
# The page's pairing document (MS-5, AC-503, AC-505)
# --------------------------------------------------------------------------- #
# Which buttons apply in each of the sidecar's pairing states
# (`whatsapp-pairing.js` PairingStatus). Here and not in the page, because the
# page may not name the states (AC-41). An unknown status is an error: a new
# state the daemon was not taught must not render with no way out.
_LINK = {"id": "link", "label": "Link WhatsApp", "method": "POST",
         "query": "", "confirm": ""}
_CANCEL = {"id": "disconnect", "label": "Cancel", "method": "DELETE",
           "query": "", "confirm": ""}
_DISCONNECT = {"id": "disconnect", "label": "Disconnect", "method": "DELETE",
               "query": "", "confirm": ""}
_UNLINK = {"id": "unlink", "label": "Unlink", "method": "DELETE",
           "query": "unlink=true",
           "confirm": "Unlink? Butler logs out of WhatsApp and deletes the "
                      "saved link on this computer. Linking again needs your "
                      "phone. Saved messages stay."}
ACTIONS = {
    "idle": (_LINK,),
    "connecting": (_CANCEL,),
    "awaiting_scan": (_CANCEL,),
    "paired": (_DISCONNECT, _UNLINK),
    "disconnected": (_LINK, _UNLINK),
    # MS-6: 440 and 401 are terminal; the sidecar never loops on them, and
    # the only way on is the user's (AC-505).
    "replaced": (_LINK, _UNLINK),
    "logged_out": (_LINK,),
    # RV-5: 403 is terminal too; check the account on the phone first.
    "forbidden": (_LINK, _UNLINK),
    "error": (_LINK, _UNLINK),
}
#: The states the page re-reads every 2 s; every other state changes only
#: when the user presses something.
POLL = frozenset({"connecting", "awaiting_scan"})


def page_doc(view: dict) -> dict:
    """The sidecar's `GET /pairing` answer as the page renders it.

    The words (headline, detail -- the phone steps and, after two reissued
    codes, the passkey hint) are the sidecar's `describePairing`, pinned by
    its own tests; nothing here rewrites them, so there is one copy.
    """
    state = view.get("state") or {}
    status = state.get("status")
    if status not in ACTIONS:
        raise SidecarError(f"the WhatsApp service reported an unknown "
                           f"pairing state {status!r}")
    words = view.get("view") or {}
    return {"ok": True, "status": status,
            "headline": str(words.get("headline", "")),
            "detail": str(words.get("detail", "")),
            "tone": str(words.get("tone", "neutral")),
            "qr": view.get("qrDataUrl") or None,
            "qr_count": int(state.get("qrCount") or 0),
            "poll": status in POLL,
            "actions": [dict(a) for a in ACTIONS[status]]}


# --------------------------------------------------------------------------- #
# The owner
# --------------------------------------------------------------------------- #
class WhatsApp:
    """One sidecar per daemon, started and stopped by the enable switch.

    `state` is one of: `disabled` (switch off), `starting`, `running`,
    `restarting` (waiting out a backoff after a crash), `failed` (gave up
    after `max_restarts`; `reason` says why).
    """

    def __init__(self, settings, records: Path, *, backoff=BACKOFF_S,
                 max_restarts: int = MAX_RESTARTS,
                 boot_timeout_s: float = BOOT_TIMEOUT_S,
                 stable_s: float = STABLE_S, link_poll_s: float = LINK_POLL_S):
        self.settings = settings
        self.records = Path(records)
        # Per BOOT: the same for every restart of the child within this
        # process, new for every daemon process (AC-500).
        self.token = secrets.token_urlsafe(32)
        self.backoff = tuple(backoff)
        self.max_restarts = max_restarts
        self.boot_timeout_s = boot_timeout_s
        self.stable_s = stable_s
        self.link_poll_s = link_poll_s
        self.state = "disabled"
        self.reason = ""
        self.restarts = 0
        self.port: int | None = None
        self._proc: subprocess.Popen | None = None
        self._tail = collections.deque(maxlen=50)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._switch = asyncio.Lock()
        self._http: aiohttp.ClientSession | None = None
        self._listening = False
        # The inbound side (M6/M7): `Inbound`, set by `make_app`; None means
        # owner items are logged and dropped. `_events_task` holds the
        # sidecar's `/events` stream open while the child runs.
        self.inbound = None
        # The answer hook (docs/messaging.md MS-20, M8): set by the
        # `whatsapp` notifier's `start`. Every owner TEXT is offered to it
        # BEFORE `inbound`: an answer must not queue behind the very phone
        # turn that is blocked on the question.
        self.questions = None
        self._events_task: asyncio.Task | None = None
        self._events_http: aiohttp.ClientSession | None = None
        self._event_failures = 0
        # RV-14: reads the link while the child runs; and whether the owner
        # was already told of the current stop (once per stop, not per poll
        # or retry; cleared when linked again or switched on again).
        self._link_task: asyncio.Task | None = None
        self._stop_told = False

    # -- lifecycle ---------------------------------------------------------- #
    async def start(self) -> None:
        """Called once from the daemon's startup. Spawns only if enabled."""
        self._loop = asyncio.get_running_loop()
        if not self._listening:
            self._listening = True
            self.settings.on_change(self._on_setting)
        if self.settings.get(ENABLE_KEY):
            await self._enable()

    async def stop(self) -> None:
        """Stop the child and the supervisor. Idempotent; the daemon's
        cleanup, restart and shutdown paths all call it."""
        async with self._switch:
            await self._halt("disabled")
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self._events_http is not None:
            await self._events_http.close()
            self._events_http = None

    def pid(self) -> int | None:
        proc = self._proc
        return None if proc is None or proc.poll() is not None else proc.pid

    def _on_setting(self, key: str, _value) -> None:
        """`Settings.on_change` listener. Synchronous and possibly off the
        loop, so it only schedules; the work runs on the loop."""
        if self._loop is None or (key != ENABLE_KEY
                                  and key not in CONFIG_FIELDS):
            return
        self._loop.call_soon_threadsafe(self._schedule, key)

    def _schedule(self, key: str) -> None:
        if key == ENABLE_KEY:
            if self.settings.get(ENABLE_KEY):
                # Said at once, so a page reading right after the switch
                # flips is told "starting" rather than "off".
                if self.state in ("disabled", "failed"):
                    self.state, self.reason = "starting", ""
                self._loop.create_task(self._enable())
            else:
                self._loop.create_task(self._disable())
        elif self.state == "running":
            self._loop.create_task(self._push_config_logged())

    async def _enable(self) -> None:
        async with self._switch:
            if self._task is not None and not self._task.done():
                return
            if not self.settings.get(ENABLE_KEY):
                return
            self.restarts = 0
            self._stop_told = False
            self.state, self.reason = "starting", ""
            self._task = asyncio.get_running_loop().create_task(
                self._supervise())

    async def _disable(self) -> None:
        async with self._switch:
            await self._halt("disabled")

    async def _halt(self, state: str) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self._kill)
        self.state, self.reason, self.port = state, "", None

    async def _supervise(self) -> None:
        """Run the child until the switch goes off; restart it on a crash."""
        last = "the service exited"
        while True:
            started = time.monotonic()
            try:
                await asyncio.to_thread(self._spawn)
                await self._push_config()
            except asyncio.CancelledError:
                raise
            except Exception as e:                        # noqa: BLE001
                last = str(e).splitlines()[0] if str(e) else repr(e)
                print(f"[whatsapp] start failed: {e}", file=sys.stderr,
                      flush=True)
                await asyncio.to_thread(self._kill)
            else:
                self.state, self.reason = "running", ""
                print(f"[whatsapp] sidecar up, pid {self.pid()}, port "
                      f"{self.port}", file=sys.stderr, flush=True)
                proc = self._proc
                self._events_task = asyncio.get_running_loop().create_task(
                    self._events())
                self._link_task = asyncio.get_running_loop().create_task(
                    self._watch_link())
                try:
                    code = await asyncio.to_thread(proc.wait)
                finally:
                    await self._stop_events()
                last = f"the service exited with code {code}"
                print(f"[whatsapp] sidecar exited with code {code}",
                      file=sys.stderr, flush=True)
                if time.monotonic() - started >= self.stable_s:
                    self.restarts = 0
            self.port = None
            self.restarts += 1
            if self.restarts > self.max_restarts:
                self.state = "failed"
                self.reason = (f"The WhatsApp service stopped {self.restarts}"
                               f" times in a row and was left off. Turn "
                               f"WhatsApp off and on to try again. Last "
                               f"error: {last}")
                print(f"[whatsapp] giving up after {self.restarts} failed "
                      f"starts{self._tail_text()}", file=sys.stderr,
                      flush=True)
                self._stopped(f"its service stopped {self.restarts} times "
                              f"in a row and was left off")
                return
            delay = self.backoff[min(self.restarts, len(self.backoff)) - 1]
            self.state = "restarting"
            print(f"[whatsapp] restart {self.restarts}/{self.max_restarts} "
                  f"in {delay:g} s", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)

    # -- the child process (blocking; always called through to_thread) ----- #
    def _spawn(self) -> None:
        self.records.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, BUTLER_WA_TOKEN=self.token,
                   BUTLER_WA_DIR=str(self.records))
        self._tail.clear()
        proc = subprocess.Popen(
            sidecar_argv(), cwd=str(REPO), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, creationflags=NO_WINDOW)
        # Before anything else (AC-36, D-24): a child that dies with the
        # daemon is the whole point, and a hard kill runs no cleanup.
        childproc.assign(proc)
        self._proc = proc
        errors = threading.Thread(target=self._drain, args=(proc.stderr,),
                                  daemon=True, name="whatsapp-stderr")
        errors.start()
        first: list[str] = []
        got = threading.Event()

        def read_port():
            first.append(proc.stdout.readline())
            got.set()
        threading.Thread(target=read_port, daemon=True,
                         name="whatsapp-port").start()
        if not got.wait(self.boot_timeout_s):
            raise SidecarError(f"the sidecar printed no port in "
                               f"{self.boot_timeout_s:g} s{self._tail_text()}")
        line = first[0].strip()
        if not line:
            # End of output: the child died before listening. Its last words
            # are on stderr; let the drain finish so the error carries them.
            code = proc.wait(timeout=5)
            errors.join(timeout=2)
            raise SidecarError(f"the sidecar exited with code {code} before "
                               f"printing its port{self._tail_text()}")
        if not line.isdigit():
            raise SidecarError(f"the sidecar's first line was {line!r}, not "
                               f"a port{self._tail_text()}")
        self.port = int(line)
        threading.Thread(target=self._drain, args=(proc.stdout,),
                         daemon=True, name="whatsapp-stdout").start()

    def _drain(self, stream) -> None:
        for line in stream:
            line = line.rstrip()
            self._tail.append(line)
            print(f"[whatsapp] {line}", file=sys.stderr, flush=True)

    def _tail_text(self) -> str:
        return (":\n  " + "\n  ".join(self._tail)) if self._tail else ""

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        childproc.kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    # -- talking to it ------------------------------------------------------ #
    def unavailable(self) -> Unavailable | None:
        """Why there is no sidecar to ask, or None when there is."""
        if self.state == "running" and self.port is not None:
            return None
        if self.state == "disabled":
            return Unavailable("WhatsApp is off. Turn it on to link "
                               "a phone.", poll=False)
        if self.state == "failed":
            return Unavailable(self.reason, poll=False)
        return Unavailable("The WhatsApp service is starting.", poll=True)

    async def request(self, method: str, path: str, *,
                      body: dict | None = None) -> tuple[int, dict]:
        """One call into the sidecar, with the token. Raises `Unavailable`."""
        down = self.unavailable()
        if down is not None:
            raise down
        try:
            return await self._call(method, path, body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise Unavailable(f"The WhatsApp service is not answering "
                              f"({type(e).__name__}).", poll=True) from e

    def _url(self, path: str) -> str:
        """Where the sidecar is. With `_headers`, the only spelling of the
        port and the token: `_call`, `media` and the `/events` stream all
        use both (AC-500)."""
        return f"http://127.0.0.1:{self.port}{path}"

    def _headers(self) -> dict:
        return {TOKEN_HEADER: self.token}

    async def _call(self, method: str, path: str, body: dict | None, *,
                    form: aiohttp.FormData | None = None,
                    timeout_s: float | None = None) -> tuple[int, dict]:
        """The raw call: the port, the token, JSON back. Every JSON request
        this daemon makes to the sidecar goes through here (AC-500). `form`
        sends a multipart body instead of `body`."""
        if self._http is None:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S))
        payload = {"json": body} if form is None else {"data": form}
        extra = ({"timeout": aiohttp.ClientTimeout(total=timeout_s)}
                 if timeout_s is not None else {})
        async with self._http.request(method, self._url(path),
                                      headers=self._headers(), **payload,
                                      **extra) as r:
            data = await r.json(content_type=None)
            return r.status, data if isinstance(data, dict) else {}

    # -- inbound: the /events stream (MS-3, M6) ------------------------------ #
    async def _events(self) -> None:
        """Hold `GET /events` open while the child runs, and hand every item
        to `_dispatch` IN ORDER. A dropped stream with the child still up is
        reconnected after `EVENTS_BACKOFF_S`; a child that dies ends this
        task (`_supervise` cancels it) and the next start opens a new one.
        Never fatal: an item that fails is logged and the next one read."""
        while True:
            try:
                await self._read_events()
                why = "the stream ended"
            except asyncio.CancelledError:
                raise
            except Exception as e:                        # noqa: BLE001
                why = f"{type(e).__name__}: {e}"
            delay = EVENTS_BACKOFF_S[min(self._event_failures,
                                         len(EVENTS_BACKOFF_S) - 1)]
            self._event_failures += 1
            print(f"[whatsapp] events stream lost ({why}); reconnecting in "
                  f"{delay:g} s", file=sys.stderr, flush=True)
            await asyncio.sleep(delay)

    async def _read_events(self) -> None:
        if self._events_http is None:
            # No total timeout (the stream is meant to stay open), and a read
            # buffer that fits one 65,536-character message as UTF-8 JSON.
            self._events_http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
                read_bufsize=EVENT_LINE_MAX)
        async with self._events_http.get(self._url("/events"),
                                         headers=self._headers()) as r:
            if r.status != 200:
                raise SidecarError(f"/events answered {r.status}")
            self._event_failures = 0
            while True:
                line = await r.content.readline()
                if not line:
                    return
                if line.strip():
                    await self._dispatch(json.loads(line))

    async def _dispatch(self, item: dict) -> None:
        """One inbound item. Owner messages and missed counts go to
        `inbound`; GUEST messages and takeovers go to `inbound.guests`
        (docs/messaging.md 6.1, M9), never to the owner's path, and neither
        is logged with its content. A guest item is never offered to the
        question hook: guests never answer questions (MS-21)."""
        kind = item.get("type")
        try:
            if kind == "ready":
                # RV-11: items the sidecar had to drop while nobody read
                # its stream; said once in the own chat.
                dropped = int(item.get("dropped") or 0)
                if dropped and self.inbound is not None:
                    self.inbound.dropped(dropped)
                elif dropped:
                    print(f"[whatsapp] {dropped} inbound items were dropped "
                          f"and nothing routes them here", file=sys.stderr,
                          flush=True)
                return
            if kind == "message" and item.get("tag") == "owner":
                # The question rules see it inside `PhoneTurns.message`,
                # after a voice note is heard (RV-15).
                if self.inbound is None:
                    print("[whatsapp] an owner message arrived and nothing "
                          "routes it here", file=sys.stderr, flush=True)
                    return
                await self.inbound.owner(item)
            elif kind == "message" and item.get("tag") == "guest":
                guests = getattr(self.inbound, "guests", None)
                if guests is None:
                    print("[whatsapp] a guest message was archived and "
                          "nothing answers guests here", file=sys.stderr,
                          flush=True)
                    return
                await guests.message(item)
            elif kind == "takeover":
                guests = getattr(self.inbound, "guests", None)
                if guests is not None:
                    guests.takeover(item)
            elif kind == "missed":
                if self.inbound is not None:
                    self.inbound.missed(item)
            else:
                print(f"[whatsapp] unknown inbound item type {kind!r}",
                      file=sys.stderr, flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"[whatsapp] inbound {kind} item failed: {e!r}",
                  file=sys.stderr, flush=True)

    async def _stop_events(self) -> None:
        for name in ("_events_task", "_link_task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # -- the link (RV-13, RV-14) --------------------------------------------- #
    async def _watch_link(self) -> None:
        """Read `/status` every `link_poll_s` while the child runs. Linked:
        the owner's messages from before a restart are resumed (once per
        process, `PhoneTurns.resume`). Logged out or replaced: WhatsApp
        stopped, and the owner is told once (RV-14). A failed read is
        logged; a dead child is the supervisor's to see."""
        while True:
            await asyncio.sleep(self.link_poll_s)
            try:
                status, body = await self._call("GET", "/status", None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"[whatsapp] the link could not be read "
                      f"({type(e).__name__})", file=sys.stderr, flush=True)
                continue
            if status != 200:
                print(f"[whatsapp] the link could not be read (HTTP "
                      f"{status})", file=sys.stderr, flush=True)
                continue
            state = body.get("status")
            if body.get("linked") and state == "paired":
                self._stop_told = False
                if self.inbound is not None:
                    await self.inbound.resume()
            elif state in STOP_REASONS:
                self._stopped(STOP_REASONS[state])

    def _stopped(self, why: str) -> None:
        """WhatsApp stopped answering: tell the owner ONCE per stop."""
        if self._stop_told:
            return
        self._stop_told = True
        print(f"[whatsapp] stopped: {why}", file=sys.stderr, flush=True)
        if self.inbound is not None:
            self.inbound.stopped(why)

    # -- the reply door, typing and media (MS-17, MS-18) -------------------- #
    async def reply(self, jid: str, *, text: str | None = None,
                    quote: str | None = None,
                    voice: bytes | None = None,
                    file: tuple[str, bytes] | None = None,
                    caption: str | None = None) -> str:
        """Answer in the user's OWN chat (`POST /reply`): `text`, or `voice`
        (OGG/Opus bytes) sent as a push-to-talk note, or `file` (name, bytes)
        with an optional `caption` (docs/rich-chat.md O-1), quoted to `quote`
        when given. Not the send door: the sidecar refuses every other chat.
        Answers the message id; raises `Unavailable` or `ReplyFailed`."""
        down = self.unavailable()
        if down is not None:
            raise down
        body = form = None
        if voice is None and file is None:
            body = {"jid": jid, "text": text}
            if quote:
                body["quote"] = quote
        else:
            form = aiohttp.FormData()
            form.add_field("jid", jid)
            if voice is not None:
                form.add_field("ptt", "true")
            if quote:
                form.add_field("quote", quote)
            if file is not None and caption:
                form.add_field("caption", caption)
            if voice is not None:
                form.add_field("files", voice, filename="voice.ogg",
                               content_type="audio/ogg")
            else:
                form.add_field("files", file[1], filename=file[0],
                               content_type="application/octet-stream")
        try:
            status, answer = await self._call("POST", "/reply", body,
                                              form=form,
                                              timeout_s=SEND_TIMEOUT_S)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ReplyFailed(f"the WhatsApp service did not answer "
                              f"({type(e).__name__})") from e
        if status != 200:
            raise ReplyFailed(str(answer.get("error") or answer.get("reason")
                                  or f"HTTP {status}"))
        return str(answer.get("messageId") or "")

    async def guest_reply(self, jid: str, text: str,
                          quote: str | None = None) -> str:
        """Answer a GUEST (`POST /guest-reply`, MS-14b): text only, into a
        chat the sidecar itself checks is on the answer list (or a saved
        contact while answer-everyone is on), starting "Butler: ". Not the
        send door and not the own-chat reply door. Answers the message id;
        raises `Unavailable` or `ReplyFailed`."""
        down = self.unavailable()
        if down is not None:
            raise down
        body = {"jid": jid, "text": text}
        if quote:
            body["quote"] = quote
        try:
            status, answer = await self._call("POST", "/guest-reply", body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ReplyFailed(f"the WhatsApp service did not answer "
                              f"({type(e).__name__})") from e
        if status != 200:
            raise ReplyFailed(str(answer.get("error") or answer.get("reason")
                                  or f"HTTP {status}"))
        return str(answer.get("messageId") or "")

    async def own_name(self) -> str | None:
        """The linked account's display name, or None when it has none."""
        status, body = await self.request("GET", "/status")
        name = str(body.get("name") or "").strip() if status == 200 else ""
        return name or None

    async def presence(self, jid: str, state: str) -> None:
        """Typing (`composing`) or not (`paused`) in the own chat."""
        status, answer = await self.request("POST", "/presence",
                                            body={"jid": jid, "state": state})
        if status != 200:
            raise ReplyFailed(str(answer.get("error") or f"HTTP {status}"))

    async def media(self, media_id: str) -> bytes:
        """The bytes of an inbound file an event named (`GET /media/<id>`).
        Raises `Unavailable` or `MediaError` with the sidecar's reason."""
        down = self.unavailable()
        if down is not None:
            raise down
        if self._http is None:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S))
        try:
            async with self._http.get(
                    self._url("/media/" + urllib.parse.quote(media_id,
                                                             safe="")),
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT_S)) as r:
                if r.status != 200:
                    answer = await r.json(content_type=None)
                    raise MediaError(str((answer or {}).get("error")
                                         or f"HTTP {r.status}"))
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise MediaError(f"the WhatsApp service did not answer "
                             f"({type(e).__name__})") from e

    # -- the send door (MS-10..MS-13, AC-511..AC-516) ------------------------ #
    async def own_jid(self) -> str | None:
        """The linked account's own bare phone JID, or None when not linked."""
        status, body = await self.request("GET", "/status")
        if status != 200 or not body.get("linked") or not body.get("jid"):
            return None
        return bare_jid(str(body["jid"]))

    async def send(self, fields: dict,
                   file: tuple[str, bytes] | None = None) -> tuple[int, dict]:
        """THE ONE DOOR to the sidecar's guarded `/send` (MS-10).

        `fields` are an earlier prototype's body fields (`recipient`, `text`, `caption`,
        `confirm`), passed through as given: JSON `confirm` counts only as
        boolean true and a form's only as the string "true", which is the
        sidecar's rule, not this one's. `file` is `(name, bytes)`, sent as
        the one multipart file. The answer is the sidecar's status and body
        UNCHANGED (409 confirm or ambiguous, 403 refusals, 503 not linked,
        502 socket error, 413 too large).

        The send switch is the SETTING, read here on every send (AC-513); a
        `send_enabled` in `fields` is dropped, because a caller that could
        set it would make the user's switch decorative. `recipient` "me" is
        the linked account's own chat.

        Raises `Unavailable` (no sidecar, or it refused the connection: the
        send certainly did not happen) or `SendUncertain`.
        """
        down = self.unavailable()
        if down is not None:
            raise down
        fields = {k: v for k, v in fields.items()
                  if k != "send_enabled" and v is not None}
        enabled = bool(self.settings.get(SEND_KEY))
        if fields.get("recipient") == ME:
            own = await self.own_jid()
            if own is None:
                return 503, {"ok": False, "reason": "not_linked",
                             "error": "WhatsApp is not connected -- link a "
                                      "device first"}
            fields["recipient"] = own
        form = None
        body = None
        if file is None:
            body = {**fields, "send_enabled": enabled}
        else:
            name, data = file
            form = aiohttp.FormData()
            for key, value in fields.items():
                form.add_field(key, _form_value(value))
            form.add_field("send_enabled", _form_value(enabled))
            form.add_field("files", data, filename=name,
                           content_type="application/octet-stream")
        try:
            return await self._call("POST", "/send", body, form=form,
                                    timeout_s=SEND_TIMEOUT_S)
        except aiohttp.ClientConnectorError as e:
            raise Unavailable(f"The WhatsApp service is not answering "
                              f"({type(e).__name__}).", poll=True) from e
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise SendUncertain(
                f"the WhatsApp service took the message and did not answer "
                f"({type(e).__name__}); it may have been sent. Check the "
                f"chat before sending again.") from e

    def config_body(self) -> dict:
        return {field: self.settings.get(key)
                for key, field in CONFIG_FIELDS.items()}

    async def _push_config(self) -> None:
        # `_call`, not `request`: that one refuses while the state still
        # says "starting", and this IS the start.
        status, data = await self._call("POST", "/config", self.config_body())
        if status != 200:
            raise SidecarError(f"the sidecar refused its config ({status}): "
                               f"{data.get('error')}")

    async def _push_config_logged(self) -> None:
        try:
            await self._push_config()
        except Exception as e:                            # noqa: BLE001
            print(f"[whatsapp] config push failed: {e}", file=sys.stderr,
                  flush=True)



def _form_value(value) -> str:
    """A multipart field: booleans as the sidecar's "true"/"false"."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def bare_jid(jid: str) -> str:
    """`1555:12@s.whatsapp.net` -> `1555@s.whatsapp.net` (the sidecar's
    `bareJid`): the device part dropped, a leading `+` dropped."""
    user, _, server = str(jid).partition("@")
    return f"{user.split(':')[0].lstrip('+')}@{server}"


# --------------------------------------------------------------------------- #
# The door, for callers that hold no app (MS-13)
# --------------------------------------------------------------------------- #
# A notification channel is built by `notify.Channels` from a FILE and is
# handed nothing but its fields, so it reaches the send door here. `make_app`
# serves its owner and withdraws it at cleanup.
_door: WhatsApp | None = None


def serve(owner: WhatsApp) -> None:
    global _door
    _door = owner


def withdraw(owner: WhatsApp) -> None:
    global _door
    if _door is owner:
        _door = None


def door() -> WhatsApp:
    """The running daemon's WhatsApp owner. Raises `Unavailable`."""
    if _door is None:
        raise Unavailable("WhatsApp is not running in this Butler.",
                          poll=False)
    return _door


# --------------------------------------------------------------------------- #
# The agent's tools, in the OpenAI function shape (MS-8, MS-18b)
# --------------------------------------------------------------------------- #
# ONE definition. The endpoint brain sends these; `adapters/pi_memory.ts`
# declares the same names and parameters for pi, and a test holds the two
# together, because a TypeScript file cannot import this one. Names and
# parameters are an earlier prototype's (`shared-agent-tools.js`), except the send tool's
# `file`, which the spec names (AC-562).
def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(required)}}}


READ_TOOLS = (
    _fn("whatsapp_list_chats",
        "List the user's WhatsApp conversations, most recently active "
        "first. Use to find the exact name of a chat before reading it.",
        {"groups_only": {"type": "boolean",
                         "description": "Only list group chats."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "How many chats to list (default 30)."}}),
    _fn("whatsapp_read_chat",
        "Read recent messages from one WhatsApp conversation, newest first. "
        "Accepts a contact name, a group name, or a phone number.",
        {"chat": {"type": "string",
                  "description": "Contact name, group name, or phone "
                                 "number."},
         "count": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "How many messages (default 30)."}},
        ("chat",)),
    _fn("whatsapp_search_messages",
        "Search the text of stored WhatsApp messages across every "
        "conversation.",
        {"query": {"type": "string", "description": "Text to look for."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "Maximum hits (default 30)."}},
        ("query",)),
    _fn("whatsapp_list_contacts",
        "Look up the user's WhatsApp contacts, optionally filtered by name. "
        "Use to confirm exactly who a name refers to before messaging them.",
        {"query": {"type": "string",
                   "description": "Filter by name or number."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "Maximum contacts (default 50)."}}),
)
SEND_TOOLS = (
    _fn("whatsapp_send",
        "Send a WhatsApp message, or one file, from the user's own number. "
        "Use recipient \"me\" for the user's own chat. For ANYONE ELSE, ASK "
        "THE USER FIRST, naming the recipient and the exact text or file, "
        "unless they have just told you to send exactly this. The first "
        "message to a person is held and Butler itself asks the user to "
        "approve it; the answer says whether it was sent. Never send in "
        "bulk, and never write to someone who has not written to the user "
        "first unless the user says so. Always tell the user whether it was "
        "sent.",
        {"recipient": {"type": "string",
                       "description": "\"me\", or a saved contact or group: "
                                      "name, phone number or chat id."},
         "text": {"type": "string",
                  "description": "The message. Required unless file is "
                                 "given."},
         "file": {"type": "string",
                  "description": "A file to send, inside this project's "
                                 "folder or Butler's data root. Its type "
                                 "decides how it appears (inline image, "
                                 "video or audio, else a document). At most "
                                 "100 MB."},
         "caption": {"type": "string",
                     "description": "Caption for the file."}},
        ("recipient",)),
)
READ_NAMES = tuple(t["function"]["name"] for t in READ_TOOLS)
SEND_NAMES = tuple(t["function"]["name"] for t in SEND_TOOLS)
TOOL_NAMES = READ_NAMES + SEND_NAMES


def read_on(settings) -> bool:
    return bool(settings.get(READ_KEY))


def send_on(settings) -> bool:
    """Sending is offered only while WhatsApp itself is on: a send tool with
    no link behind it can only ever answer "not running"."""
    return bool(settings.get(ENABLE_KEY)) and bool(settings.get(SEND_KEY))


def offered(settings) -> tuple:
    """The tools a model may be shown NOW (AC-508, AC-562)."""
    return ((READ_TOOLS if read_on(settings) else ())
            + (SEND_TOOLS if send_on(settings) else ()))


def offered_names(settings) -> tuple:
    return tuple(t["function"]["name"] for t in offered(settings))


# --------------------------------------------------------------------------- #
# The archive, read-only (MS-7, MS-8; an earlier prototype's `whatsapp-store.js` read API)
# --------------------------------------------------------------------------- #
EMPTY_ARCHIVE = ("The WhatsApp archive is empty. Link a phone on Butler's "
                 "settings page; contacts and history arrive on the first "
                 "connection.")
#: Bounds every list a model is shown; an earlier prototype had none.
MAX_LIMIT = 200


class ToolRefused(ValueError):
    """A tool call with arguments that cannot be run; the message says why,
    for the model to correct and try again."""


class ArchiveMissing(RuntimeError):
    """No `whatsapp.db` yet: nothing was ever linked."""


def archive_uri(path: Path) -> str:
    """`file:<path>?mode=ro`: SQLite refuses every write through it."""
    return ("file:" + urllib.parse.quote(Path(path).as_posix(), safe="/:")
            + "?mode=ro")


def _digits(raw: str) -> str:
    """The sidecar's `jidDigits`."""
    return str(raw).split("@")[0].split(":")[0].removeprefix("+")


class Archive:
    """One read-only connection to the sidecar's archive. A context manager:
    open per call, closed after, so nothing holds the file between turns."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.con: sqlite3.Connection | None = None

    def __enter__(self) -> "Archive":
        if not self.path.is_file():
            raise ArchiveMissing(str(self.path))
        self.con = sqlite3.connect(archive_uri(self.path), uri=True)
        self.con.row_factory = sqlite3.Row
        return self

    def __exit__(self, *exc) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None

    def _all(self, sql: str, *params) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, params)]

    def _one(self, sql: str, *params) -> dict | None:
        row = self.con.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def is_known(self, jid: str) -> bool:
        """The guard's rule (RV-20, AC-606): a contact with an ADDRESS-BOOK
        name, a chat the user wrote in themselves (not a Butler send: its id
        prefix or the marker), or a group in the chats. A push name alone is
        the sender's own claim, archived for anyone who ever wrote. KEEP
        EQUAL to `isKnownContact` in whatsapp-store.js."""
        return bool(
            self._one("SELECT 1 FROM contacts WHERE jid = ? AND "
                      "TRIM(COALESCE(name, '')) != ''", jid)
            or self._one("SELECT 1 FROM messages WHERE jid = ? AND "
                         "from_me = 1 AND substr(id, 1, ?) != ? AND "
                         "(text IS NULL OR substr(text, -1) != ?) LIMIT 1",
                         jid, len(BUTLER_ID_PREFIX), BUTLER_ID_PREFIX,
                         MARKER)
            or self._one("SELECT 1 FROM chats WHERE jid = ? AND "
                         "is_group = 1", jid))

    def list_contacts(self, query: str | None = None,
                      limit: int = 500) -> list[dict]:
        if query:
            q = f"%{query.lower()}%"
            return self._all(
                "SELECT jid, name, notify FROM contacts WHERE "
                "LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(notify, "
                "'')) LIKE ? OR jid LIKE ? ORDER BY COALESCE(name, notify, "
                "jid) LIMIT ?", q, q, f"%{query}%", limit)
        return self._all("SELECT jid, name, notify FROM contacts ORDER BY "
                         "COALESCE(name, notify, jid) LIMIT ?", limit)

    def list_chats(self, groups_only: bool = False,
                   limit: int = 200) -> list[dict]:
        where = "WHERE is_group = 1" if groups_only else ""
        return self._all(
            "SELECT c.jid AS jid, COALESCE(c.name, ct.name, ct.notify) AS "
            "name, c.is_group AS isGroup, c.last_message_at AS lastMessageAt "
            f"FROM chats c LEFT JOIN contacts ct ON ct.jid = c.jid {where} "
            "ORDER BY COALESCE(c.last_message_at, 0) DESC LIMIT ?", limit)

    def chat_name(self, jid: str) -> str | None:
        """What the user's phone calls this chat: a group's subject, a
        contact's saved or pushed name; None when the archive has neither."""
        row = self._one("SELECT COALESCE(c.name, ct.name, ct.notify) AS name "
                        "FROM (SELECT ? AS jid) q LEFT JOIN chats c ON "
                        "c.jid = q.jid LEFT JOIN contacts ct ON ct.jid = "
                        "q.jid", jid)
        return (row or {}).get("name") or None

    def read_chat(self, jid: str, count: int = 50) -> list[dict]:
        """Most recent messages in a chat, NEWEST FIRST."""
        return self._all(
            "SELECT jid, id, from_me AS fromMe, sender, push_name AS "
            "pushName, ts, text FROM messages WHERE jid = ? ORDER BY ts "
            "DESC, id DESC LIMIT ?", jid, count)

    def search_messages(self, query: str, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT jid, id, from_me AS fromMe, push_name AS pushName, ts, "
            "text FROM messages WHERE text IS NOT NULL AND LOWER(text) LIKE "
            "? ORDER BY ts DESC LIMIT ?", f"%{str(query).lower()}%", limit)

    def resolve_recipient(self, raw_input) -> dict:
        """A JID, a phone number in any shape, or a contact name.

        A NAME MATCHING SEVERAL CONTACTS RESOLVES TO NOTHING (AC-509):
        picking the first is how a message reaches the wrong person.
        """
        raw = str(raw_input or "").strip()
        if not raw:
            return {"jid": None, "name": None, "candidates": []}

        def known(jid):
            row = self._one("SELECT jid, COALESCE(name, notify) AS name "
                            "FROM contacts WHERE jid = ?", jid)
            return ({"jid": row["jid"], "name": row["name"],
                     "candidates": []} if row else None)

        if "@" in raw:
            return known(raw) or {"jid": raw, "name": None, "candidates": []}
        digits = _digits(re.sub(r"[\s()-]", "", raw))
        if re.fullmatch(r"\d{6,}", digits):
            jid = f"{digits}@s.whatsapp.net"
            return known(jid) or {"jid": jid, "name": None, "candidates": []}
        q = f"%{raw.lower()}%"
        matches = [{"jid": r["jid"], "name": r["name"]} for r in self._all(
            "SELECT jid, COALESCE(name, notify) AS name FROM contacts WHERE "
            "LOWER(COALESCE(name, '')) LIKE ? OR LOWER(COALESCE(notify, '')) "
            "LIKE ? ORDER BY COALESCE(name, notify) LIMIT 10", q, q)]
        if len(matches) == 1:
            return {**matches[0], "candidates": []}
        return {"jid": None, "name": None, "candidates": matches}

    def resolve_chat(self, raw_input) -> dict:
        """Anything the user might call a conversation, a GROUP SUBJECT
        included (groups have no contact row). Refuses to guess, as
        `resolve_recipient`."""
        as_contact = self.resolve_recipient(raw_input)
        raw = str(raw_input or "").strip()
        if not raw:
            return {"jid": None, "name": None, "candidates": []}
        if as_contact["jid"] and ("@" in raw or re.fullmatch(
                r"[\d\s()+-]{6,}", raw)):
            return as_contact
        found = [{"jid": r["jid"], "name": r["name"]} for r in self._all(
            "SELECT jid, name FROM chats WHERE name IS NOT NULL AND "
            "LOWER(name) LIKE ? LIMIT 10", f"%{raw.lower()}%")]
        extra = ([{"jid": as_contact["jid"], "name": as_contact["name"]}]
                 if as_contact["jid"] else as_contact["candidates"])
        for c in extra:
            if not any(m["jid"] == c["jid"] for m in found):
                found.append(c)
        if len(found) == 1:
            return {**found[0], "candidates": []}
        return {"jid": None, "name": None, "candidates": found}


def _stamp(ts) -> str:
    """An earlier prototype's `waTimestamp`: UTC, minutes."""
    if not ts:
        return "unknown"
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M")


def _limit(args: dict, key: str, default: int) -> int:
    value = args.get(key, default)
    if value is None:
        return default
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or int(value) != value or not 1 <= value <= MAX_LIMIT):
        raise ToolRefused(f"{key} must be a whole number from 1 to "
                          f"{MAX_LIMIT}")
    return int(value)


def _text_arg(args: dict, key: str, required: bool) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolRefused(f"{key} must be non-blank text")
    return value.strip()


def ambiguous_text(query: str, candidates: list[dict]) -> str:
    if not candidates:
        return f'No chat or contact matches "{query}".'
    return (f'"{query}" matches {len(candidates)} chats. Say which one:\n'
            + "\n".join(f"- {c['name'] or c['jid']} ({c['jid']})"
                        for c in candidates))


def read_tool(path: Path, name: str, args) -> dict:
    """Run one read tool over the archive at `path` (blocking: call through
    `asyncio.to_thread`). Answers `{"text", ...}`; `whatsapp_read_chat` adds
    `jid` and `candidates`, and an unresolved chat has `jid` None (AC-509).
    Raises `ToolRefused` for arguments that cannot be run."""
    if name not in READ_NAMES:
        raise ToolRefused(f"there is no tool called {name!r}")
    if not isinstance(args, dict):
        raise ToolRefused("the arguments must be a JSON object")
    try:
        with Archive(path) as archive:
            return _read(archive, name, args)
    except ArchiveMissing:
        return {"text": EMPTY_ARCHIVE, "jid": None, "candidates": []}


def chat_choices(path: Path) -> list[dict]:
    """The archive's chats, people and groups, as `{value, label}` choices
    for the "Butler answers" picker (MS-14a), most recently active first.
    Raises `ArchiveMissing` when there is nothing to pick from: an empty
    picker drawn as though all were well is the failure `GET /api/models`
    exists to refuse. Blocking."""
    with Archive(path) as archive:
        # People and groups only: `status@broadcast` and the like are chats
        # in the archive and nobody Butler could answer.
        chats = [c for c in archive.list_chats(limit=MAX_LIMIT)
                 if JID.fullmatch(c["jid"])]
    if not chats:
        raise ArchiveMissing(str(path))
    return [{"value": c["jid"],
             "label": f"{c['name'] or c['jid']}"
                      f"{' (group)' if c['isGroup'] else ''}"}
            for c in chats]


def _who(m: dict, fallback) -> str:
    if m["fromMe"]:
        return "You"
    return m.get("pushName") or m.get("sender") or fallback or "Them"


def _read(archive: Archive, name: str, args: dict) -> dict:
    if name == "whatsapp_list_chats":
        chats = archive.list_chats(bool(args.get("groups_only")),
                                   _limit(args, "limit", 30))
        if not chats:
            return {"text": EMPTY_ARCHIVE}
        lines = [f"- {c['name'] or c['jid']}"
                 f"{' [group]' if c['isGroup'] else ''} -- last activity "
                 f"{_stamp(c['lastMessageAt'])}" for c in chats]
        return {"text": f"{len(chats)} chats:\n" + "\n".join(lines)}
    if name == "whatsapp_read_chat":
        chat = _text_arg(args, "chat", True)
        count = _limit(args, "count", 30)
        target = archive.resolve_chat(chat)
        if not target["jid"]:
            return {"text": ambiguous_text(chat, target["candidates"]),
                    "jid": None, "candidates": target["candidates"]}
        shown = target["name"] or target["jid"]
        messages = archive.read_chat(target["jid"], count)
        if not messages:
            return {"text": f"No stored messages for {shown}.",
                    "jid": target["jid"], "candidates": []}
        lines = [f"[{_stamp(m['ts'])}] {_who(m, target['name'])}: "
                 f"{m['text'] or '(media or unsupported message)'}"
                 for m in messages]
        return {"text": f"{shown}, newest first:\n" + "\n".join(lines),
                "jid": target["jid"], "candidates": []}
    if name == "whatsapp_search_messages":
        query = _text_arg(args, "query", True)
        hits = archive.search_messages(query, _limit(args, "limit", 30))
        if not hits:
            return {"text": f'No stored message contains "{query}".'}
        lines = [f"[{_stamp(m['ts'])}] "
                 f"{'You' if m['fromMe'] else (m['pushName'] or 'Them')} in "
                 f"{m['jid']}: {m['text']}" for m in hits]
        return {"text": f"{len(hits)} matches:\n" + "\n".join(lines)}
    query = _text_arg(args, "query", False)
    contacts = archive.list_contacts(query, _limit(args, "limit", 50))
    if not contacts:
        return {"text": f'No contact matches "{query}".' if query
                else EMPTY_ARCHIVE}
    lines = [f"- {c['name'] or c['notify'] or '(no name)'} -- {c['jid']}"
             for c in contacts]
    return {"text": f"{len(contacts)} contacts:\n" + "\n".join(lines)}


# --------------------------------------------------------------------------- #
# Files out (MS-18b, AC-562)
# --------------------------------------------------------------------------- #
# The rule itself lives in `daemon/containment.py`, shared with `show_file`
# (docs/rich-chat.md RC-11).


# --------------------------------------------------------------------------- #
# What a model is told after a send (an earlier prototype's explanations)
# --------------------------------------------------------------------------- #
def render_send(status: int, body: dict, recipient: str) -> str:
    if status == 200 and body.get("ok"):
        return f"Sent to {body.get('name') or body.get('jid')}."
    who = body.get("name") or body.get("jid") or recipient
    candidates = ", ".join(
        f"{c.get('name') or c.get('jid')} ({c.get('jid')})"
        for c in body.get("candidates") or ())
    explain = {
        "confirmation_required": (
            f"NOT SENT. This would be the first message to {who} since "
            f"Butler started, and it needs the user's confirmation."),
        "owner_declined": (f"NOT SENT. The user said no to sending this to "
                           f"{who}."),
        "owner_not_answered": (
            f"NOT SENT. The user did not answer whether to send this to "
            f"{who} (the question closed), so nothing was sent."),
        "no_turn_to_ask": f"NOT SENT. {body.get('error')}.",
        "file_in_records": f"NOT SENT. {body.get('error')}.",
        "ambiguous_recipient": (
            f'NOT SENT. "{recipient}" matches several chats: {candidates}. '
            f"Ask which one."),
        "unknown_recipient": (
            f'NOT SENT. "{recipient}" is not one of the user\'s saved '
            f"WhatsApp contacts or groups."),
        "send_disabled": ("NOT SENT. WhatsApp sending is switched off in "
                          "Butler's settings (Butler may send)."),
        "rate_limited": (f"NOT SENT. The send rate limit "
                         f"({body.get('limit') or 'configured'}/minute) was "
                         f"reached."),
        "send_failed": (f"NOT SENT. WhatsApp rejected it: "
                        f"{body.get('error') or 'unknown error'}."),
        "file_outside_roots": f"NOT SENT. {body.get('error')}.",
    }.get(body.get("reason"))
    return explain or (f"NOT SENT ({status}): "
                       f"{body.get('error') or body.get('reason') or 'unknown error'}.")


# --------------------------------------------------------------------------- #
# The endpoint brain's two callables (`NEEDS_WHATSAPP`)
# --------------------------------------------------------------------------- #
class AgentTools:
    """The tools for a brain that runs them in this process. `offered` and
    `call` are what the brain is handed (callables, never the settings
    store, 4.10); both read the settings at USE, so a switch reaches the very
    next request.

    `forbidden` is `records_dirs` (RV-2, AC-587). `ask` is the daemon's
    owner question, `Hub.ask_owner(chat, title, message, turn)` (AC-588)."""

    def __init__(self, settings, archive: Path, data_root: Path, *,
                 forbidden, ask):
        self.settings = settings
        self.archive = Path(archive)
        self.data_root = Path(data_root)
        self.forbidden = tuple(forbidden)
        self.ask = ask

    def offered(self) -> tuple:
        return offered(self.settings)

    async def call(self, name: str, args, chat: str, turn: int) -> str:
        """One tool call in turn `turn` of conversation `chat`, answered as
        text; refusals are ANSWERED (plan.md 3.8), never raised."""
        if name in READ_NAMES:
            if not read_on(self.settings):
                return ("Error: WhatsApp reading is off (Butler may read, "
                        "on the settings page).")
            try:
                answer = await asyncio.to_thread(read_tool, self.archive,
                                                 name, args)
            except ToolRefused as e:
                return f"Error: {e}."
            return answer["text"]
        if name in SEND_NAMES:
            if not send_on(self.settings):
                return ("NOT SENT. WhatsApp sending is off (WhatsApp and "
                        "Butler may send, on the settings page).")

            async def ask(message: str):
                return await self.ask(chat, CONFIRM_TITLE, message, turn)
            # This brain holds no conversation, so its project is the root
            # row, which IS the data root (docs/automations.md AU-9).
            return await agent_send(args, roots=(self.data_root,),
                                    base=self.data_root,
                                    forbidden=self.forbidden, ask=ask)
        return f"Error: there is no tool called {name!r}."


#: The owner's question when a tool's send is held for confirmation.
CONFIRM_TITLE = "Send on WhatsApp?"
#: How much of a held text the question quotes.
CONFIRM_PREVIEW = 80


def confirm_question(fields: dict, file_name: str | None,
                     held: dict) -> str:
    """"Send <text preview or file name> to <name>?" (RV-2)."""
    who = held.get("name") or held.get("jid") or fields.get("recipient")
    if file_name:
        what = file_name
    else:
        text = " ".join(str(fields.get("text") or "").split())
        if len(text) > CONFIRM_PREVIEW:
            text = text[:CONFIRM_PREVIEW - 3] + "..."
        what = f'"{text}"'
    return f"Send {what} to {who}?"


async def tool_send(owner: WhatsApp, fields: dict,
                    upload: tuple[str, bytes] | None,
                    ask) -> tuple[int, dict]:
    """A TOOL's send (RV-2, AC-588): the ONE place a model's send is
    confirmed. A tool never sets `confirm` (it is dropped here, whatever
    the caller sent); when the guard holds the send for confirmation (409
    `confirmation_required`) the OWNER is asked through the question door,
    `ask(message) -> True | False | None`, and only their yes re-sends it
    confirmed. A no, or a question that closed unanswered, is a refusal the
    model is shown. Raises what `WhatsApp.send` raises."""
    fields = {k: v for k, v in fields.items() if k != "confirm"}
    status, held = await owner.send(fields, upload)
    if status != 409 or held.get("reason") != "confirmation_required":
        return status, held
    who = {k: held[k] for k in ("name", "jid") if held.get(k)}
    try:
        yes = await ask(confirm_question(
            fields, upload[0] if upload else None, held))
    except LookupError as e:
        return 409, {"ok": False, "reason": "no_turn_to_ask",
                     "error": f"this first message needs the user's "
                              f"confirmation and {e}", **who}
    if yes is not True:
        reason = "owner_not_answered" if yes is None else "owner_declined"
        print(f"[butler] whatsapp: a tool's send was not confirmed by the "
              f"owner ({reason})", flush=True)
        return 403, {"ok": False, "reason": reason, **who}
    return await owner.send({**fields, "confirm": True}, upload)


async def agent_send(args, *, roots, base: Path, forbidden, ask) -> str:
    """`whatsapp_send` run in this process: check the arguments, apply the
    file rule BEFORE reading a byte, read the file, and go through the one
    door as a TOOL (`tool_send`). Answers the text a model is shown."""
    if not isinstance(args, dict):
        return "Error: the arguments must be a JSON object."
    recipient = args.get("recipient")
    if not isinstance(recipient, str) or not recipient.strip():
        return "NOT SENT. recipient must be non-blank text."
    recipient = recipient.strip()
    text, file_arg, caption = (args.get("text"), args.get("file"),
                               args.get("caption"))
    for key, value in (("text", text), ("file", file_arg),
                       ("caption", caption)):
        if value is not None and not isinstance(value, str):
            return f"NOT SENT. {key} must be text."
    if not (text and text.strip()) and not file_arg:
        return "NOT SENT. Give text or file (or both)."
    fields = {"recipient": recipient, "text": text or None,
              "caption": caption or None}
    upload = None
    if file_arg:
        try:
            real = file_inside(Path(base, file_arg), roots, forbidden)
        except FileRefused as e:
            return f"NOT SENT. {e}."
        try:
            size = real.stat().st_size
            if size > MAX_FILE_BYTES:
                return (f"NOT SENT. {real.name} is {size} bytes; the limit "
                        f"is {MAX_FILE_BYTES}.")
            data = await asyncio.to_thread(real.read_bytes)
        except OSError as e:
            return f"NOT SENT. Could not read {real}: {e.strerror or e}."
        upload = (real.name, data)
    try:
        status, body = await tool_send(door(), fields, upload, ask)
    except Unavailable as e:
        return f"NOT SENT. {e.reason}"
    except SendUncertain as e:
        return f"UNKNOWN: {e}"
    return render_send(status, body, recipient)


# --------------------------------------------------------------------------- #
# A notification channel's recipient (MS-13, AC-516)
# --------------------------------------------------------------------------- #
#: A stored channel target: "me" or a chat id.
JID = re.compile(r"[0-9A-Za-z.:_-]+@(s\.whatsapp\.net|g\.us|lid)")


def channel_target(value, archive: Path) -> tuple[str, str]:
    """`(jid or "me", "")` for a channel's `to`, or `("", problem)`.

    "me" stays "me" (the door resolves it per send, from the linked account).
    A chat id is taken as stored; a name or number is RESOLVED against the
    archive, refused when it matches several chats (the candidates named) or
    is not a saved contact or group. Blocking."""
    raw = str(value or "").strip()
    if raw in ("", ME):
        return ME, ""
    if "@" in raw:
        return (raw, "") if JID.fullmatch(raw) else (
            "", "to is not a WhatsApp chat id")
    try:
        with Archive(archive) as a:
            found = a.resolve_chat(raw)
            if not found["jid"]:
                if not found["candidates"]:
                    return "", "to matches no saved contact or group"
                return "", ("to matches several chats, give one chat id: "
                            + "; ".join(f"{c['name'] or '?'} ({c['jid']})"
                                        for c in found["candidates"]))
            if not a.is_known(found["jid"]):
                return "", "to is not a saved contact or group"
            return found["jid"], ""
    except ArchiveMissing:
        return "", ("WhatsApp has no saved contacts yet; link a phone "
                    "first, or use me")


# --------------------------------------------------------------------------- #
# The user talks to Butler from WhatsApp (phases M6 and M7)
# --------------------------------------------------------------------------- #
# docs/messaging.md MS-16..MS-19, MS-18a, MS-18b IN; AC-517..AC-524, AC-539,
# AC-560, AC-561. The turn machinery is `phone.PhoneTurns`, shared with
# two-way Telegram; what is WhatsApp's own is here: the sidecar's item shape,
# the one line for messages missed while down (MS-19), and guests (M9).

#: One WhatsApp text, with the marker the sidecar appends (MS-17).
TEXT_LIMIT = 65536 - 1
#: The sidecar refuses to fetch larger files (MS-18).
MAX_FILE_MB = 100
MISSED_LINE = ("I was offline and did not answer {n} {noun}; send again what "
               "still matters.")
#: RV-11: the sidecar dropped items while no reader was attached.
DROPPED_LINE = ("I may have missed {n} {noun} while my link to WhatsApp was "
                "interrupted; send again what still matters.")
#: RV-14 (AC-601): said once per stop, through `notify_default` and the desk.
STOPPED_TITLE = "WhatsApp stopped"
STOPPED_LINE = ("WhatsApp stopped: {why}. I am not answering WhatsApp until "
                "it works again; see WhatsApp in Butler's settings.")
#: How long missed items are gathered before the ONE line is sent (MS-19).
MISSED_SETTLE_S = 3.0


def owner_message(item: dict) -> phone.Message:
    """The sidecar's `/events` owner item as a channel-neutral message. An
    item with `unsupported` set (a sticker, location, contact card or poll,
    RV-9) becomes `phone.UNSUPPORTED_KIND` media, answered with the fixed
    line; an edit arrives as plain text "(edited) <new text>" (plus
    `edited`, the original's id) and is an ordinary message; an album
    carries every file in `album` (RV-8)."""
    media = item.get("media")
    album = tuple(_media(m) for m in item.get("album") or ())
    if item.get("unsupported"):
        media_of = phone.Media(kind=phone.UNSUPPORTED_KIND, ref="")
    else:
        media_of = _media(media) if media else None
    return phone.Message(
        where=str(item["jid"]), id=str(item["id"]),
        text=str(item.get("text") or ""),
        quoted=str(item.get("quoted") or ""), media=media_of, album=album)


def _media(media: dict) -> phone.Media:
    """One file of a sidecar item (`media`, or an entry of an album's
    `album`, RV-8)."""
    seconds = media.get("seconds")
    return phone.Media(
        kind=str(media.get("kind") or ""), ref=str(media.get("id") or ""),
        size=int(media.get("bytes") or 0),
        too_large=bool(media.get("tooLarge")),
        file_name=str(media.get("fileName") or ""),
        seconds=None if seconds is None else float(seconds))


class Inbound(phone.PhoneTurns):
    """The owner's messages from their own chat, as turns (MS-16..MS-19),
    plus the guest path beside them (M9)."""

    def __init__(self, hub, wa: WhatsApp, *, queue_folder: Path,
                 missed_settle_s: float = MISSED_SETTLE_S,
                 busy_poll_s: float = phone.BUSY_POLL_S,
                 typing_every_s: float = phone.TYPING_EVERY_S):
        super().__init__(hub, wa, kind="whatsapp", text_limit=TEXT_LIMIT,
                         max_file_mb=MAX_FILE_MB, queue_folder=queue_folder,
                         busy_poll_s=busy_poll_s,
                         typing_every_s=typing_every_s)
        self.wa = wa
        self._told: set[asyncio.Task] = set()
        self.missed_settle_s = missed_settle_s
        self._missed = 0
        self._missed_jid = ""
        self._missed_task: asyncio.Task | None = None
        # Everyone else (MS-14a..MS-14c): a separate path that never reaches
        # a turn, a session or a tool (AC-536). Here only so the events
        # reader finds it beside the owner's path.
        self.guests = Guests(hub, wa)

    async def owner(self, item: dict) -> None:
        await self.message(owner_message(item))

    def missed(self, item: dict) -> None:
        """MS-19: count it; ONE line goes out once the burst settles."""
        phone.log(self.kind, f"owner message {item.get('id')} missed: "
                             f"written while Butler was down")
        self._missed += 1
        self._missed_jid = str(item["jid"])
        if self._missed_task is None or self._missed_task.done():
            self._missed_task = asyncio.get_running_loop().create_task(
                self._tell_missed())

    async def _tell_missed(self) -> None:
        await asyncio.sleep(self.missed_settle_s)
        n, self._missed = self._missed, 0
        await self.say(self._missed_jid, MISSED_LINE.format(
            n=n, noun="message" if n == 1 else "messages"), None)

    def dropped(self, n: int) -> None:
        """RV-11: the sidecar dropped `n` items no reader took; whatever
        they were, the owner hears it once, in their own chat."""
        phone.log(self.kind, f"{n} inbound items were dropped before Butler "
                             f"read them")
        self._spawn(self._tell_dropped(n))

    async def _tell_dropped(self, n: int) -> None:
        try:
            jid = await self.wa.own_jid()
        except phone.ChannelError as e:
            phone.log(self.kind, f"the dropped-items line was NOT sent: {e}")
            return
        if jid is None:
            phone.log(self.kind, "the dropped-items line was NOT sent: no "
                                 "linked account")
            return
        await self.say(jid, DROPPED_LINE.format(
            n=n, noun="message" if n == 1 else "messages"), None)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._told.add(task)
        task.add_done_callback(self._told.discard)

    def stopped(self, why: str) -> None:
        """RV-14 (AC-601): WhatsApp stopped answering. The owner is told
        where they can still hear it -- never through WhatsApp itself."""
        from daemon import notify
        self._spawn(notify.tell_owner(
            self.hub, STOPPED_TITLE, STOPPED_LINE.format(why=why),
            besides=self.kind))


# --------------------------------------------------------------------------- #
# Everyone else: guest replies (phase M9)
# --------------------------------------------------------------------------- #
# docs/messaging.md MS-14a..MS-14c, MS-18b's guest rule; AC-535..AC-538,
# AC-563. A message from anyone but the user is a GUEST message, and THIS
# CLASS IS THE WHOLE OF ITS PATH. It never builds a session, never calls
# `Hub._typed` or a brain's turn, never reads USER.md, the memory notes or
# another chat, and reaches a model only through the one callable `ask`
# (`adapters.brain_endpoint.GuestCall`, a completion that cannot carry
# tools). `test_whatsapp_guests.py` holds that apart by source (AC-536).
# Its records are notes in the guest's own standing chat; nothing about a
# guest is spoken at the desk and no question is ever sent to one.

#: Every guest reply starts with this, so nobody takes it for the human. The
#: sidecar's `/guest-reply` refuses any text that does not.
GUEST_PREFIX = "Butler: "
#: The identity a guest turn gets beside SOUL.md, and nothing else (MS-14b).
GUEST_RULE = ("You are answering {name} on behalf of {user}. You are not "
              "talking to {user}. Do not share {user}'s private information. "
              "This is a WhatsApp chat: answer in a few plain sentences.")
#: What a guest's file, image or voice note gets (MS-18b, AC-563).
GUEST_MEDIA_LINE = "I can only read text here."
#: What a guest gets when no model can be asked without tools (AC-536).
GUEST_CANNOT_REPLY = "I cannot reply here right now."
#: At most this many replies per guest chat in any rolling hour (MS-14b).
GUEST_REPLIES_PER_HOUR = 20
GUEST_HOUR_S = 3600.0
#: How long Butler stays quiet after the user writes in a guest chat (MS-14c).
TAKEOVER_S = 30 * 60.0
#: Messages of one guest's own exchange shown back to the model, in memory
#: only: never read from the chat record, which the owner may also type in.
GUEST_HISTORY = 10
GUEST_TITLE = "WhatsApp: {name}"
SILENT_NOTE = ("Butler answered {n} times here in the last hour and stays "
               "quiet until {at}.")
TAKEOVER_NOTE = ("You wrote in this chat yourself; Butler stays quiet here "
                 "until {at}.")


def _clock_text(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%H:%M")


def soul_block(text: str) -> str:
    """SOUL.md's text, labelled as every brain labels it."""
    from daemon import dialogue
    return dialogue.soul_note(text)


class Guests:
    """Replies to the people and groups the user chose (MS-14a..MS-14c).

    `message(item)` is called by the events reader IN ORDER and only queues;
    each guest chat is answered one message at a time. `ask(system, history,
    text)` is the one way to a model and `soul()` answers SOUL.md's text;
    both are built from the daemon on first use when not given. `clock` is
    epoch seconds, injected by tests."""

    def __init__(self, hub, wa, *, ask=None, soul=None, archive=None,
                 clock=time.time):
        self.hub = hub
        self.wa = wa
        self._ask = ask
        self._soul = soul
        self._built = False
        records = getattr(wa, "records", None)
        self._archive = (Path(archive) if archive is not None
                         else Path(records) / "whatsapp.db" if records
                         else None)
        self.clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiting: dict[str, int] = {}
        self.tasks: set[asyncio.Task] = set()
        self._sent: dict[str, collections.deque] = {}
        self._quiet_until: dict[str, float] = {}
        self._silence_noted: set[str] = set()
        self._history: dict[str, collections.deque] = {}
        self._chats: dict[str, str] = {}
        self._user: str | None = None

    def _build(self) -> None:
        """The daemon's own `soul` and `ask`, once. With no connections to
        ask through, `ask` stays None and every guest gets
        `GUEST_CANNOT_REPLY`, the reason logged."""
        if self._built:
            return
        self._built = True
        if self._soul is None:
            from daemon import paths, persona
            self._soul = persona.soul_file(paths.resolve().persona).effective
        if self._ask is None:
            if self.hub.connections is None or self.hub.settings is None:
                print("[whatsapp] guests cannot be answered: this daemon has "
                      "no connections to ask a model through",
                      file=sys.stderr, flush=True)
                return
            from daemon import connections
            from daemon.adapters.brain_endpoint import GuestCall
            self._ask = GuestCall(connections.SettingsResolver(
                self.hub.connections, self.hub.settings))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # -- arrival (in order) ------------------------------------------------- #
    async def message(self, item: dict) -> None:
        key = bare_jid(str(item["jid"]))
        if self._waiting.get(key, 0) >= MAX_WAITING:
            print(f"[whatsapp] a guest message in {key} was dropped: "
                  f"{MAX_WAITING} are already waiting", file=sys.stderr,
                  flush=True)
            return
        self._waiting[key] = self._waiting.get(key, 0) + 1
        self._spawn(self._one(key, item))

    def takeover(self, item: dict) -> None:
        """MS-14c: the user wrote in a guest chat; quiet there for 30 min."""
        key = bare_jid(str(item["jid"]))
        until = self.clock() + TAKEOVER_S
        self._quiet_until[key] = until
        print(f"[whatsapp] the owner wrote in guest chat {key}; quiet there "
              f"until {_clock_text(until)}", file=sys.stderr, flush=True)
        chat = self._chats.get(key)
        if chat:
            self._spawn(self._note(chat, TAKEOVER_NOTE.format(
                at=_clock_text(until))))

    async def _one(self, key: str, item: dict) -> None:
        try:
            async with self._locks.setdefault(key, asyncio.Lock()):
                await self._answer(key, item)
        except Exception as e:                            # noqa: BLE001
            print(f"[whatsapp] a guest message in {key} failed: {e!r}",
                  file=sys.stderr, flush=True)
        finally:
            self._waiting[key] -= 1

    # -- one guest message -------------------------------------------------- #
    async def _answer(self, key: str, item: dict) -> None:
        now = self.clock()
        if self._quiet_until.get(key, 0.0) > now:
            print(f"[whatsapp] a guest message in {key} was not answered: "
                  f"the owner took over", file=sys.stderr, flush=True)
            return
        sent = self._sent.setdefault(key, collections.deque())
        while sent and sent[0] <= now - GUEST_HOUR_S:
            sent.popleft()
        group = bool(item.get("isGroup"))
        name = await self._name(key, item)
        who = ((str(item.get("pushName") or "")
                or _digits(item.get("sender") or key)) if group else name)
        if len(sent) >= GUEST_REPLIES_PER_HOUR:
            if key not in self._silence_noted:
                self._silence_noted.add(key)
                await self._note(await self._chat(key, name), SILENT_NOTE.format(
                    n=len(sent), at=_clock_text(sent[0] + GUEST_HOUR_S)))
            print(f"[whatsapp] a guest message in {key} was not answered: "
                  f"{GUEST_REPLIES_PER_HOUR} replies this hour",
                  file=sys.stderr, flush=True)
            return
        self._silence_noted.discard(key)
        chat = await self._chat(key, name)
        media = item.get("media")
        if media:
            # Never fetched, never stored, never shown to a model (AC-563).
            kind = media.get("kind") or "file"
            await self._note(chat, f"{who} sent a {kind}; it was not opened.")
            answer = GUEST_MEDIA_LINE
        else:
            text = str(item.get("text") or "").strip()
            await self._note(chat, f"{who} wrote: {text}")
            answer = await self._model(key, name,
                                       f"{who}: {text}" if group else text)
        reply = GUEST_PREFIX + answer
        # RV-23 (AC-610): the owner may have taken over WHILE the answer was
        # being made; the check at the top was before the model call.
        if self._quiet_until.get(key, 0.0) > self.clock():
            print(f"[whatsapp] a guest reply in {key} was NOT sent: the "
                  f"owner took over while it was being written",
                  file=sys.stderr, flush=True)
            return
        try:
            await self.wa.guest_reply(str(item["jid"]), reply,
                                      str(item["id"]) if group else None)
        except (Unavailable, ReplyFailed) as e:
            print(f"[whatsapp] a guest reply in {key} was NOT delivered: {e}",
                  file=sys.stderr, flush=True)
            return
        sent.append(now)
        await self._note(chat, reply)

    async def _model(self, key: str, name: str, text: str) -> str:
        """The answer to one guest message, without the prefix."""
        self._build()
        if self._ask is None:
            return GUEST_CANNOT_REPLY
        user = await self._user_name()
        system = (soul_block(self._soul()),
                  GUEST_RULE.format(name=name, user=user))
        history = self._history.setdefault(
            key, collections.deque(maxlen=GUEST_HISTORY))
        try:
            answer = str(await self._ask(system, list(history), text)).strip()
        except Exception as e:                            # noqa: BLE001
            print(f"[whatsapp] a guest reply in {key} could not be made "
                  f"({type(e).__name__}: {e}); sent the fixed line",
                  file=sys.stderr, flush=True)
            return GUEST_CANNOT_REPLY
        if not answer:
            print(f"[whatsapp] the model answered a guest in {key} with "
                  f"nothing; sent the fixed line", file=sys.stderr,
                  flush=True)
            return GUEST_CANNOT_REPLY
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": answer})
        return answer

    async def _user_name(self) -> str:
        """The linked account's name, for the guest rule; "the user" when
        WhatsApp has none for it."""
        if self._user is None:
            try:
                self._user = await self.wa.own_name() or ""
            except Unavailable:
                return "the user"
        return self._user or "the user"

    async def _name(self, key: str, item: dict) -> str:
        """The chat's name as the user's phone shows it."""
        found = None
        if self._archive is not None:
            try:
                found = await asyncio.to_thread(self._archive_name, key)
            except ArchiveMissing:
                found = None
        if found:
            return found
        if not item.get("isGroup") and item.get("pushName"):
            return str(item["pushName"])
        return _digits(key)

    def _archive_name(self, key: str) -> str | None:
        with Archive(self._archive) as archive:
            return archive.chat_name(key)

    # -- the standing chat -------------------------------------------------- #
    async def _chat(self, key: str, name: str) -> str:
        """The guest's standing chat in the root project, found by its
        `origin` or born unfocused; "" when this brain keeps no chats."""
        brain = self.hub.session_brain()
        if brain is None:
            return ""
        known = self._chats.get(key)
        if known and await asyncio.to_thread(brain.session_chat, known,
                                             1) is not None:
            return known
        root = self.hub.shortcuts.root_id() if self.hub.shortcuts else ""
        rows = await asyncio.to_thread(
            brain.session_list, None,
            self.hub.project_filter(root) if root else None)
        for row in rows:
            if is_guest_chat(row) and row["origin"].get("jid") == key:
                self._chats[key] = row["id"]
                return row["id"]
        born = await self.hub.new_conversation(
            None, focus=False, origin={"kind": GUEST_ORIGIN, "jid": key,
                                       "title": GUEST_TITLE.format(
                                           name=name)})
        self._chats[key] = born["id"]
        return born["id"]

    async def _note(self, chat: str, text: str) -> None:
        """One line of Butler's own into the guest's chat. Never the owner's
        role: these words are a guest's or Butler's, not the user's."""
        note = getattr(self.hub.session_brain(), "record_note", None)
        if not chat or note is None:
            return
        await asyncio.to_thread(note, chat, text)
