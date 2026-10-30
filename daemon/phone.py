"""The owner's phone as a channel into their conversation, for ANY chat app.

docs/messaging.md MS-16..MS-18b, MS-24; AC-519..AC-523, AC-533, AC-539,
AC-560, AC-561. A message the OWNER sends from their phone -- their own
WhatsApp chat, or the Telegram chat of a bot with `inbound` on -- is an OWNER
TURN in the chat `phone_chat` names, run on a headless `hub.PhoneSession`
(nothing is spoken at the desk) through `Hub._typed`, the door every typed
turn takes. One turn at a time per chat, in arrival order; the answer goes
back quoted to the message, as a voice note and/or text per `phone_reply`.

This module was `whatsapp.Inbound`'s owner half until two-way Telegram (T1)
needed the same rules; it knows no chat app. A CHANNEL is the transport and
supplies three calls, each raising a `ChannelError` (with a reason that never
quotes a secret) when it cannot:

    await channel.reply(where, text=..., quote=..., voice=...) -> message id
    await channel.presence(where, "composing" | "paused")
    await channel.media(ref) -> bytes

and one attribute, `channel.questions`: `notify.Questions`' answer hook while
a question channel of that kind is started, else None. `PhoneTurns.message`
offers every message to it before queueing (MS-22, RV-15).

`where` is the chat app's id for the owner's chat and `quote` the id of the
message being answered; `voice` is OGG/Opus bytes. The channel also decides
what a `Message` is: its adapter turns its own update into one.

What differs per chat app is passed in, never branched on: the KIND (the
turn's typed reason, which is also where its questions go, MS-21), the
longest text one message may carry, and the largest file it lets Butler fetch.
"""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import typing
from dataclasses import dataclass
from pathlib import Path

from daemon import childproc, ports
from daemon.chats import is_guest_chat

# Windows only, and 0 elsewhere so the kwarg is always legal: ffmpeg is a
# console program, and a daemon without a console would get one ALLOCATED.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: What the user types to start over (MS-16).
NEW_COMMAND = "/new"
#: Messages WAITING per chat (the one being answered is not counted); the
#: next one is answered with `BUSY_LINE` and dropped (MS-17, AC-521).
MAX_WAITING = 10
BUSY_LINE = "I am still working on your earlier messages."
#: Butler's additions to a transcript (MS-18). The voice prefix is an earlier prototype's.
VOICE_PREFIX = ("[Voice note, auto-transcribed: may contain misheard words; "
                "ask if unclear]")
VIDEO_PREFIX = ("[Video; its sound auto-transcribed: may contain misheard "
                "words]")
NO_WORDS = "I could not hear any words in that voice note."
NO_WORDS_VIDEO = ("I could not hear any words in that video, and I cannot "
                  "watch it.")
IMAGE_QUESTION = "What is in this image?"
DOCUMENT_QUESTION = "What is in this document?"
TOO_LARGE = ("That file is {mb} MB. I only take files up to {limit} MB, so I "
             "did not download it.")
NO_STORE = "This Butler keeps no attachments, so I cannot look at that file."
#: docs/rich-chat.md O-1: a file the agent showed that could not be sent.
SHOWN_TOO_LARGE = "{name} is too large to send here ({mb} MB)."
SHOWN_GONE = "I could not send {name}: the file is no longer available."
SHOWN_FAILED = "I could not send {name} here."
NO_ANSWER = "That turn ended without an answer."
FAILED = "That did not work: {why}"
#: A sticker, location, contact card, poll... (RV-9, AC-595): what a channel
#: marks as `Media(kind=UNSUPPORTED_KIND)`, answered with `UNSUPPORTED`.
UNSUPPORTED_KIND = "unsupported"
UNSUPPORTED = ("I can read text, voice notes, photos, videos and documents "
               "here, not that.")
#: The longest recording Butler transcribes (RV-16, AC-603) [DECIDED]: a
#: longer one would hold its chat's queue for minutes.
MAX_AUDIO_S = 10 * 60
TOO_LONG = ("That recording is {minutes} minutes long. I only transcribe up "
            "to {limit} minutes, so I did not read it.")
#: 16 kHz mono int16, what `decode_audio` answers.
PCM_BYTES_PER_S = 16000 * 2
#: After a restart (RV-13, AC-600): the message that was IN FLIGHT is not run
#: again, because what it already did is unknown.
RESTARTED_LINE = ("I restarted while working on your message from {at}; "
                  "send it again if it still matters.")
#: A WAITING message's file that cannot be fetched after a restart.
GONE_LINE = ("I restarted before I could read your {what} from {at}, and it "
             "can no longer be fetched; send it again if it still matters.")
#: How often a queued message re-asks whether its chat is still answering.
BUSY_POLL_S = 0.25
#: Typing is re-shown at this interval while a turn runs (MS-17).
TYPING_EVERY_S = 10.0
#: About two minutes of speech at 150 words a minute [TARGET] (MS-18a).
VOICE_MAX_WORDS = 300
REST_IN_TEXT = "The rest is in the text."
#: ffmpeg gets this long per decode or encode.
FFMPEG_TIMEOUT_S = 120.0



class ChannelError(RuntimeError):
    """A channel could not deliver, fetch or show something; the message says
    why and never quotes a secret. Every chat app's own failures subclass it,
    so this module catches one type and names no chat app."""


class MediaError(ChannelError):
    """An inbound file could not be fetched or decoded; the message says
    why, and is said in the chat (MS-18)."""


class VoiceFailed(RuntimeError):
    """A voice reply could not be made or sent; said after the text."""


@dataclass(frozen=True)
class Media:
    """A file the owner sent. `kind` is audio, video, image or document;
    `ref` is the channel's own handle for fetching it; `too_large` is the
    channel's verdict against its own cap, made BEFORE any download."""
    kind: str
    ref: str
    size: int = 0
    too_large: bool = False
    file_name: str = ""
    #: The length the chat app declares (audio and video), None when it
    #: declares none (RV-16).
    seconds: float | None = None


@dataclass(frozen=True)
class Message:
    """One owner message, in no chat app's vocabulary. `quoted` is what the
    question rules read (`ports.Incoming.quoted`): the ref of the message it
    answers, "" when it quotes nothing."""
    where: str
    id: str
    text: str = ""
    media: Media | None = None
    quoted: str = ""
    #: A voice note's transcript, made on ARRIVAL so a spoken "sim" can
    #: answer a question (RV-15); None until then, and for every other kind.
    heard: str | None = None
    #: Every file of an album, in order (RV-8, AC-594); `media` is the
    #: first. Empty for anything that is not an album.
    album: tuple[Media, ...] = ()


def shape(m: Message) -> str:
    """What a message IS, for the log (text, voice, video, image, document):
    never what it says."""
    if m.media is None:
        return "text"
    if len(m.album) > 1:
        return f"album of {len(m.album)}"
    return "voice" if m.media.kind == "audio" else m.media.kind


def log(kind: str, line: str) -> None:
    """One butler.log line about an owner message: ids and shapes only,
    never content (docs/messaging.md 6.4: a lost message must show)."""
    print(f"[{kind}] {line}", file=sys.stderr, flush=True)


async def offer_answer(hook, message: Message, say) -> bool:
    """Was `message` an answer to a question Butler showed (MS-22, MS-24)?
    `hook` is `notify.Questions`'s (None while no question channel is
    started); what it says back goes in the chat quoted to the message, so
    an answer is never taken silently. A voice note answers by its
    transcript (RV-15, AC-602); every other file is never an answer."""
    if hook is None:
        return False
    if message.media is None:
        text = message.text
    elif message.heard is not None:
        text = message.heard
    else:
        return False
    try:
        said = await hook(ports.Incoming(text, message.quoted))
    except Exception as e:                                # noqa: BLE001
        print(f"[phone] an answer failed: {e!r}", file=sys.stderr,
              flush=True)
        said = FAILED.format(why=e)
    if said is None:
        return False
    log("phone", f"message {message.id} was taken as a question's answer")
    await say(message.where, said, message.id)
    return True


# -- ffmpeg (argv lists, job-assigned, off the loop) ------------------------ #
def _ffmpeg(args: list[str], data: bytes) -> bytes:
    """Run ffmpeg once, BLOCKING (call through `asyncio.to_thread`). Raises
    `MediaError` naming ffmpeg's last error line."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise MediaError("ffmpeg is not on PATH; voice notes need it")
    proc = subprocess.Popen(
        [exe, "-hide_banner", "-loglevel", "error", *args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=NO_WINDOW)
    childproc.assign(proc)                        # AC-36, D-24
    try:
        out, err = proc.communicate(data, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise MediaError(f"ffmpeg took longer than {FFMPEG_TIMEOUT_S:g} s")
    if proc.returncode != 0:
        lines = err.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(f"ffmpeg failed ({proc.returncode}): "
                         f"{lines[-1] if lines else 'no error text'}")
    return out


def decode_audio(data: bytes) -> bytes:
    """Any audio or video file -> 16 kHz mono int16 PCM, the recognizer's
    input (MS-18). Read from a temporary FILE, not a pipe: an MP4 whose index
    sits at its end cannot be read from a pipe. Blocking."""
    import tempfile
    fd, name = tempfile.mkstemp(prefix="butler-phone-", suffix=".media")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return _ffmpeg(["-i", name, "-vn", "-f", "s16le", "-acodec",
                        "pcm_s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                       b"")
    finally:
        os.unlink(name)


def encode_voice_note(pcm: bytes, rate: int) -> bytes:
    """Mono int16 PCM at `rate` -> OGG/Opus, what WhatsApp and Telegram both
    play as a voice note (MS-18a). Blocking."""
    return _ffmpeg(["-f", "s16le", "-ar", str(rate), "-ac", "1", "-i",
                    "pipe:0", "-c:a", "libopus", "-b:a", "32k",
                    "-application", "voip", "-f", "ogg", "pipe:1"], pcm)


# -- text shaping ----------------------------------------------------------- #
def split_text(text: str, limit: int) -> list[str]:
    """Parts of at most `limit` characters, cut at LINE boundaries; a single
    line longer than a message is cut inside it (MS-17)."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current += line
    if current:
        parts.append(current)
    return [p.strip("\n") for p in parts if p.strip()]


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
_TABLE_ROW = re.compile(r"^\s*\|")
_LEAD = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+)")


def spoken_form(text: str) -> str:
    """The reply as it is SAID in a voice note (MS-18a): fenced code and table
    rows dropped, links reduced to their words, bare URLs dropped, markdown
    marks removed. Butler's own: the desk reads its sentences as written."""
    text = _FENCE.sub("\n", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    kept = []
    for line in text.splitlines():
        if _TABLE_ROW.match(line):
            continue
        kept.append(_LEAD.sub("", line))
    text = "\n".join(kept).replace("`", "").replace("**", "").replace("__", "")
    text = text.replace("*", "")
    return " ".join(text.split())


def cap_words(text: str, max_words: int = VOICE_MAX_WORDS) -> tuple[str, bool]:
    """At most `max_words`, cut at a sentence end where one fits; and whether
    anything was cut."""
    from daemon import speech
    if len(text.split()) <= max_words:
        return text, False
    out: list[str] = []
    count = 0
    for sentence in speech.split_sentences(text):
        n = len(sentence.split())
        if count + n > max_words:
            break
        out.append(sentence.strip())
        count += n
    if not out:
        out = [" ".join(text.split()[:max_words])]
    return " ".join(out), True


def _primary(tag) -> str:
    return str(tag or "").strip().lower().split("-")[0]


def voice_for(hub, language: str | None) -> str:
    """The voice a reply in `language` is spoken in (MS-18a, AC-539): the
    user's chosen voice when it speaks that language, else the first voice
    the engine has for it. Raises `VoiceFailed` when it has none."""
    chosen = hub._voice()
    if not language:
        return chosen or hub._voice_for(None)
    if chosen and _primary(hub._voice_language(chosen)) == _primary(language):
        return chosen
    found = ports.voice_ids(hub.tts.info, language)
    if not found:
        raise VoiceFailed(f"no voice speaks {language}")
    return found[0]


class _Watch:
    """What a phone turn's session was sent: the reply pieces (with whether
    each is spoken) and an error. `on_frame` is synchronous."""

    def __init__(self, chat: str):
        self.chat = chat
        self.pieces: list[tuple[str, bool]] = []
        self.error = ""
        #: Files the agent SHOWED in this chat, in order (docs/rich-chat.md
        #: O-1); `_deliver` sends them after the text.
        self.shown: list[dict] = []

    def frame(self, m: dict) -> None:
        if m.get("type") == "reply" and m.get("chat") == self.chat:
            self.pieces.append((str(m.get("text") or ""),
                                bool(m.get("spoken", True))))
        elif (m.get("type") == "artifact" and m.get("chat") == self.chat
              and m.get("role") == "assistant"):
            self.shown.append(dict(m.get("artifact") or {}))
        elif m.get("type") == "error":
            self.error = str(m.get("message") or "the turn failed")

    def answer(self) -> str:
        return "".join(t for t, _ in self.pieces).strip()

    def spoken(self) -> str:
        return "".join(t for t, said in self.pieces if said).strip()


# -- the persisted queue (RV-13) -------------------------------------------- #
#: A persisted message's state. An ANSWERED message (or one refused or
#: failed, which is also said in the chat) is DELETED from the file, so the
#: file only ever holds these two.
WAITING = "waiting"
STARTED = "started"


def _clock(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).strftime("%H:%M")


class PhoneQueue:
    """`Paths.phone_queue / <kind>.json`: every owner message of one chat app
    that was received and not yet answered (docs/messaging.md RV-13, AC-599,
    AC-600), so a restart neither loses nor repeats one.

        {"messages": [{"id", "where", "shape", "text", "quoted", "heard",
                       "media": {kind, ref, size, too_large, file_name,
                                 seconds} | null, "album": [<media>, ...],
                       "arrived": <epoch s>, "state": "waiting"|"started"}]}

    In arrival order. Read ONCE, at construction (boot, before any message
    can be written over it): what it held is `recovered`, for
    `PhoneTurns.resume`. Every change rewrites the file through
    `memory._atomic`, off the loop; a later snapshot is never overwritten by
    an earlier one. A message is STARTED just before its turn is minted:
    from then on its effects are unknown, and a restart says so instead of
    running it twice."""

    def __init__(self, folder: Path, kind: str):
        self.path = Path(folder) / f"{kind}.json"
        self._entries: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._written = 0
        self.recovered: list[dict] = self._load()
        for e in self.recovered:
            self._entries[(e["where"], e["id"])] = e

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            doc = json.loads(self.path.read_text("utf-8"))
            entries = list(doc["messages"])
            for e in entries:
                if e["state"] not in (WAITING, STARTED):
                    raise ValueError(f"unknown state {e['state']!r}")
                message_of(e)
        except (OSError, ValueError, KeyError, TypeError) as e:
            # Kept beside, never deleted: it is the only record of what the
            # owner sent. Loud, and the phone keeps working (D-1).
            bad = self.path.with_name(
                f"{self.path.name}.bad-{int(time.time())}")
            self.path.replace(bad)
            print(f"[phone] {self.path.name} could not be read "
                  f"({type(e).__name__}: {e}); moved to {bad.name}, and its "
                  f"messages are NOT resumed", file=sys.stderr, flush=True)
            return []
        return entries

    def entry(self, m: Message) -> dict | None:
        return self._entries.get((m.where, m.id))

    async def add(self, m: Message) -> None:
        self._entries[(m.where, m.id)] = {
            "id": m.id, "where": m.where, "shape": shape(m), "text": m.text,
            "quoted": m.quoted, "heard": m.heard,
            "media": None if m.media is None else dataclasses.asdict(m.media),
            "album": [dataclasses.asdict(a) for a in m.album],
            "arrived": time.time(), "state": WAITING}
        await self._save()

    async def heard(self, m: Message) -> None:
        e = self.entry(m)
        if e is not None:
            e["heard"] = m.heard
            await self._save()

    async def start(self, m: Message) -> None:
        e = self.entry(m)
        if e is not None and e["state"] != STARTED:
            e["state"] = STARTED
            await self._save()

    async def done(self, m: Message) -> None:
        if self._entries.pop((m.where, m.id), None) is not None:
            await self._save()

    async def _save(self) -> None:
        self._seq += 1
        text = json.dumps({"messages": list(self._entries.values())},
                          indent=1) + "\n"
        await asyncio.to_thread(self._write, self._seq, text)

    def _write(self, seq: int, text: str) -> None:
        from daemon import memory
        with self._lock:
            if seq <= self._written:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                memory._atomic(self.path, text)
            except OSError as e:
                # Loud, never fatal: the phone keeps answering (D-1); only
                # what a restart could recover is at risk.
                print(f"[phone] {self.path.name} could NOT be written: {e}",
                      file=sys.stderr, flush=True)
                return
            self._written = seq


def message_of(e: dict) -> Message:
    """A persisted entry as the message it was."""
    media = e["media"]
    return Message(where=str(e["where"]), id=str(e["id"]),
                   text=str(e["text"]), quoted=str(e["quoted"]),
                   heard=e["heard"],
                   media=None if media is None else Media(**media),
                   album=tuple(Media(**a) for a in e["album"]))


class _Prepared(typing.NamedTuple):
    text: str              # the turn's text
    words: str             # the owner's own words, for the language
    attachments: tuple
    voice_in: bool         # the user sent a voice note (`mirror`)


class PhoneTurns:
    """The owner's messages from one chat app, as turns (MS-16..MS-18b).

    `message(m)` is called by the channel's reader IN ORDER. It persists the
    message (RV-13), transcribes a voice note at once (RV-15), offers it to
    the question rules, and queues it; the turn runs in one worker per chat
    of the chat app (`where`), which picks the Butler chat when the message
    STARTS, so a queued `/new` moves the messages behind it (RV-17). Every
    refusal and failure is SAID in the chat, quoted, because a silent drop
    reads as being ignored (MS-18). `say` is the one text reply path
    (RV-18)."""

    def __init__(self, hub, channel, *, kind: str, text_limit: int,
                 max_file_mb: int, queue_folder: Path,
                 busy_poll_s: float = BUSY_POLL_S,
                 typing_every_s: float = TYPING_EVERY_S):
        self.hub = hub
        self.channel = channel
        self.kind = kind
        self.text_limit = text_limit
        self.max_file_mb = max_file_mb
        self.busy_poll_s = busy_poll_s
        self.typing_every_s = typing_every_s
        self.queue = PhoneQueue(queue_folder, kind)
        self._waiting: dict[str, collections.deque] = {}
        self._workers: dict[str, asyncio.Task] = {}
        # The latched language per chat (AC-209): each message is a new
        # session, so the latch is carried here.
        self._languages: dict[str, str | None] = {}
        # A root chat born here and still empty: not on disk, so not
        # "newest", until its first turn writes it (chats.py).
        self._fresh_root: str | None = None
        # RV-13: whether `resume` ran, and when each resumed message arrived.
        self._resumed = False
        self._arrived_before: dict[str, float] = {}

    def _setting(self, key: str):
        return self.hub.settings.get(key)

    # -- after a restart (RV-13, AC-599, AC-600) ---------------------------- #
    async def resume(self) -> None:
        """Once per process, before any new message is queued: what the
        queue file held. A message that never STARTED runs again, in order;
        the one that was in flight is NOT run again (what it did is unknown)
        and its chat is told. Called when the channel can answer again
        (WhatsApp linked, Telegram polling) or by the first new message."""
        if self._resumed:
            return
        self._resumed = True
        recovered, self.queue.recovered = self.queue.recovered, []
        in_flight = []
        for e in recovered:
            m = message_of(e)
            if e["state"] == STARTED:
                in_flight.append((m, e["arrived"]))
                continue
            log(self.kind, f"owner message {m.id} resumed after a restart")
            self._arrived_before[m.id] = e["arrived"]
            self._enqueue(m, limit=False)
        for m, arrived in in_flight:
            log(self.kind, f"owner message {m.id} was being answered at a "
                           f"restart; not run again")
            await self.say(m.where, RESTARTED_LINE.format(at=_clock(arrived)),
                           None)
            await self.queue.done(m)

    # -- arrival (in order) ------------------------------------------------- #
    async def message(self, m: Message) -> None:
        answering = sum(1 for t in self._workers.values() if not t.done())
        waiting = sum(len(q) for q in self._waiting.values())
        log(self.kind, f"owner message {m.id} received ({shape(m)}); "
                       f"{answering} answering, {waiting} waiting")
        await self.resume()
        if m.media is not None and m.media.kind == UNSUPPORTED_KIND:
            await self.say(m.where, UNSUPPORTED, m.id)
            return
        try:
            await self.queue.add(m)
            if m.media is not None and m.media.kind == "audio":
                heard = await self._hear(m)
                if heard is None:
                    await self.queue.done(m)
                    return
                m = dataclasses.replace(m, heard=heard)
                await self.queue.heard(m)
            if await offer_answer(self.channel.questions, m, self.say):
                await self.queue.done(m)
                return
            if not self._enqueue(m):
                log(self.kind, f"owner message {m.id} refused: {MAX_WAITING} "
                               f"already waiting")
                await self.say(m.where, BUSY_LINE, m.id)
                await self.queue.done(m)
        except Exception as e:                            # noqa: BLE001
            log(self.kind, f"owner message {m.id} failed: {e!r}")
            await self.say(m.where, FAILED.format(why=e), m.id)

    def _enqueue(self, m: Message, *, limit: bool = True) -> bool:
        waiting = self._waiting.setdefault(m.where, collections.deque())
        if limit and len(waiting) >= MAX_WAITING:
            return False
        waiting.append(m)
        worker = self._workers.get(m.where)
        if worker is None or worker.done():
            self._workers[m.where] = asyncio.get_running_loop().create_task(
                self._work(m.where))
        return True

    # -- which chat (MS-16) ------------------------------------------------- #
    async def _target(self) -> str:
        """The chat a message goes to NOW: "" when this brain holds no
        conversations (endpoint mode answers without one)."""
        brain = self.hub.session_brain()
        if brain is None:
            return ""
        if self._setting("phone_chat") == "current":
            chat = await self._current(brain)
            if chat:
                return chat
            # A guest's chat is never the owner's (RV-22): the root rule.
        if self._fresh_root:
            record = await asyncio.to_thread(brain.session_chat,
                                             self._fresh_root, 1)
            if record is not None and not record.get("messages"):
                return self._fresh_root
            self._fresh_root = None
        root = self.hub.shortcuts.root_id() if self.hub.shortcuts else ""
        if not root:
            raise LookupError("this Butler has no root project to answer in")
        # The newest root chat that is not a GUEST's: a guest's standing
        # chat lives in the root project too, and the owner's words must
        # never land in it (AC-519, AC-536).
        rows = await asyncio.to_thread(brain.session_list, None,
                                       self.hub.project_filter(root))
        newest = [r for r in rows if not is_guest_chat(r)]
        if newest:
            return newest[0]["id"]
        self._fresh_root = (await self.hub.new_conversation(
            root, focus=False))["id"]
        return self._fresh_root

    async def _current(self, brain) -> str:
        """`last_owner_chat`; before the owner's first turn since boot, or
        once that chat is deleted, the conversation the daemon holds. ""
        when that one is a GUEST's standing chat (RV-22, AC-609): the owner
        typing in it on the page, or opening it, must not route the phone's
        turns -- which run with tools -- into a stranger's conversation."""
        chat = self.hub.last_owner_chat
        if not (chat and await asyncio.to_thread(brain.session_chat, chat,
                                                 1) is not None):
            chat = self.hub.current_chat_id()
        record = (await asyncio.to_thread(brain.session_chat, chat, 1)
                  if chat else None)
        return "" if record is None or is_guest_chat(record) else chat

    async def _new_chat(self, m: Message) -> None:
        """`/new` (MS-16): a fresh chat where `phone_chat` points -- a new
        root chat, or a new chat in the current chat's project. Unfocused:
        the desk stays where it is."""
        brain = self.hub.session_brain()
        if brain is None:
            await self.say(m.where, "This Butler keeps no conversations, so "
                                    "there is nothing new to start.", m.id)
            return
        if self._setting("phone_chat") == "current":
            project = await self.hub._project_id_of(await self._current(brain))
            born = await self.hub.new_conversation(project, focus=False)
            self.hub.last_owner_chat = born["id"]
        else:
            born = await self.hub.new_conversation(None, focus=False)
            self._fresh_root = born["id"]
        await self.say(m.where, self.hub.NEW_CONVERSATION, m.id)

    # -- one chat's queue (MS-17, AC-520, AC-521) --------------------------- #
    async def _work(self, where: str) -> None:
        waiting = self._waiting[where]
        while waiting:
            m = waiting.popleft()
            typing_ = asyncio.get_running_loop().create_task(
                self._typing(m.where))
            try:
                await self._answer(m)
            except asyncio.CancelledError:
                # Left in the queue file: a restart runs it again or, if it
                # had started, says so (RV-13).
                raise
            except Exception as e:                        # noqa: BLE001
                log(self.kind, f"owner message {m.id} failed: {e!r}")
                await self.say(m.where, FAILED.format(why=e), m.id)
            finally:
                typing_.cancel()
                await self._presence(m.where, "paused")
            await self.queue.done(m)

    async def _typing(self, where: str) -> None:
        while True:
            await self._presence(where, "composing")
            await asyncio.sleep(self.typing_every_s)

    async def _presence(self, where: str, state: str) -> None:
        # Typing is a courtesy: a refusal is logged and costs no answer.
        try:
            await self.channel.presence(where, state)
        except ChannelError as e:
            print(f"[{self.kind}] typing mark not shown: {e}",
                  file=sys.stderr, flush=True)

    async def _answer(self, m: Message) -> None:
        if m.media is None and m.text.strip().lower() == NEW_COMMAND:
            # In its place in the queue (RV-17, AC-604).
            await self.queue.start(m)
            await self._new_chat(m)
            return
        prepared = await self._prepare(m)
        if prepared is None:
            return
        chat = await self._target()
        # ONE TURN AT A TIME PER CHAT, whoever is answering in it (desk, page
        # or another phone message). STARTED is on disk before the turn can
        # act (RV-13), and no `await` lies between the last check and
        # `_typed` minting the turn, so nothing can start in between.
        while True:
            while self.hub._turn_in_flight(chat):
                await asyncio.sleep(self.busy_poll_s)
            await self.queue.start(m)
            if not self.hub._turn_in_flight(chat):
                break
        watch, language = await self._turn(chat, m, prepared)
        await self._deliver(m, watch, language, prepared.voice_in)

    async def _turn(self, chat: str, m: Message,
                    prepared: _Prepared) -> tuple[_Watch, str | None]:
        from daemon.hub import Pending, PhoneSession
        watch = _Watch(chat)
        session = PhoneSession(self.hub, chat, f"{self.kind}:{m.id}",
                               watch.frame, prepared.words,
                               self._languages.get(chat), reason=self.kind)
        for att in prepared.attachments:
            session.attachments.append(Pending(chat, att))
        self.hub.sessions.add(session)
        try:
            await self.hub._typed(session, {"type": "typed",
                                            "text": prepared.text})
            # Until neither the turn nor its reply is running; either may be
            # replaced while the other runs, so both are re-read each time.
            while True:
                pending = [x for x in (session.task, session.reply_task)
                           if x is not None and not x.done()]
                if not pending:
                    break
                await asyncio.wait(pending)
        finally:
            self.hub.sessions.discard(session)
        self._languages[chat] = session.language
        return watch, session.language

    # -- media in (MS-18, MS-18b IN; AC-522, AC-523, AC-560, AC-561) -------- #
    async def _fetch(self, m: Message, media: Media) -> bytes | None:
        """The file's bytes, or None after SAYING why not."""
        try:
            return await self.channel.media(media.ref)
        except ChannelError as e:
            arrived = self._arrived_before.get(m.id)
            if arrived is not None:
                what = "voice note" if media.kind == "audio" else media.kind
                await self.say(m.where, GONE_LINE.format(
                    what=what, at=_clock(arrived)), m.id)
            else:
                await self.say(m.where, f"I could not fetch that "
                                        f"{media.kind}: {e}", m.id)
            return None

    async def _too_long(self, m: Message, seconds: float | None) -> bool:
        """RV-16 (AC-603): past `MAX_AUDIO_S`, SAID, before transcription."""
        if seconds is None or seconds <= MAX_AUDIO_S:
            return False
        await self.say(m.where, TOO_LONG.format(
            minutes=math.ceil(seconds / 60), limit=MAX_AUDIO_S // 60), m.id)
        return True

    async def _too_large(self, m: Message, media: Media) -> bool:
        if not media.too_large:
            return False
        await self.say(m.where, TOO_LARGE.format(
            mb=round(media.size / (1024 * 1024)), limit=self.max_file_mb),
            m.id)
        return True

    async def _sound(self, m: Message, media: Media) -> bytes | None:
        """An audio or video file's sound as PCM, or None after SAYING why
        not: too large, declared too long (before any download), not
        fetched, or decoded too long (before any transcription). A decode
        failure raises."""
        if (await self._too_large(m, media)
                or await self._too_long(m, media.seconds)):
            return None
        data = await self._fetch(m, media)
        if data is None:
            return None
        pcm = await asyncio.to_thread(decode_audio, data)
        if await self._too_long(m, len(pcm) / PCM_BYTES_PER_S):
            return None
        return pcm

    async def _hear(self, m: Message) -> str | None:
        """A voice note's words, or None after SAYING why there are none."""
        try:
            pcm = await self._sound(m, m.media)
            if pcm is None:
                return None
            heard = await self.hub.transcribe_clip(pcm)
        except Exception as e:                            # noqa: BLE001
            await self.say(m.where, f"I could not transcribe that voice "
                                    f"note: {e}", m.id)
            return None
        if not heard:
            await self.say(m.where, NO_WORDS, m.id)
            return None
        return heard

    async def _video(self, m: Message,
                     media: Media) -> tuple[str, str] | None:
        """A video's words and the block the turn shows for it; None after
        SAYING why there is no turn. Its frames are never seen."""
        try:
            pcm = await self._sound(m, media)
            if pcm is None:
                return None
            heard = await self.hub.transcribe_clip(pcm)
        except Exception as e:                            # noqa: BLE001
            heard, why = "", f"(its sound could not be read: {e})"
        else:
            why = "(no words in its sound)"
        return heard, f"{VIDEO_PREFIX}\n{heard or why}"

    async def _attach(self, m: Message, media: Media):
        """An image or a document, stored EXACTLY as a page upload
        (AC-197..AC-206); None after SAYING why not."""
        if await self._too_large(m, media):
            return None
        data = await self._fetch(m, media)
        if data is None:
            return None
        store = self.hub.attachments
        if store is None:
            await self.say(m.where, NO_STORE, m.id)
            return None
        from daemon import attachments as attach_mod
        mode = str(self._setting("document_reading") or "text_then_vision")
        name = media.file_name or f"{self.kind}-{media.kind}"
        try:
            return await asyncio.to_thread(store.add, data, name, mode)
        except attach_mod.Unsupported as e:
            await self.say(m.where, str(e), m.id)
            return None

    async def _prepare(self, m: Message) -> _Prepared | None:
        """The turn's text, words and attachments; or None after SAYING why
        there is no turn. An album (RV-8, AC-594) is ONE turn with every
        file; a file that cannot be read stops it, said."""
        text = m.text.strip()
        if m.media is None:
            return _Prepared(text, text, (), False)
        if m.media.kind == "audio":
            # Heard on arrival; a message resumed from before a restart that
            # had not been heard yet is heard now.
            heard = m.heard if m.heard is not None else await self._hear(m)
            if heard is None:
                return None
            return _Prepared(f"{VOICE_PREFIX}\n{heard}", heard, (), True)
        attachments, bodies, words = [], [], []
        for media in m.album or (m.media,):
            if media.kind == "video":
                got = await self._video(m, media)
                if got is None:
                    return None
                bodies.append(got[1])
                if got[0]:
                    words.append(got[0])
                continue
            att = await self._attach(m, media)
            if att is None:
                return None
            attachments.append(att)
        if not attachments and not words and not text:
            await self.say(m.where, NO_WORDS_VIDEO, m.id)
            return None
        lead = text
        if not lead and attachments:
            lead = (IMAGE_QUESTION if any(a.kind == "image"
                                          for a in attachments)
                    else DOCUMENT_QUESTION)
        return _Prepared("\n\n".join(x for x in (lead, *bodies) if x),
                         " ".join(words) or text, tuple(attachments), False)

    # -- the answer out (MS-17, MS-18a; AC-520, AC-539) --------------------- #
    async def _deliver(self, m: Message, watch: _Watch,
                       language: str | None, voice_in: bool) -> None:
        answer = watch.answer()
        if not answer:
            answer = (FAILED.format(why=watch.error) if watch.error
                      else NO_ANSWER)
        form = self._setting("phone_reply")
        spoken = watch.spoken()
        if spoken and (form == "voice_text"
                       or (form == "mirror" and voice_in)):
            try:
                await self._voice_reply(m, spoken, language)
            except VoiceFailed as e:
                answer += f"\n\n(voice reply failed: {e})"
        for part in split_text(answer, self.text_limit):
            await self.say(m.where, part, m.id)
        for entry in watch.shown:
            await self._send_shown(m, watch.chat, entry)

    async def _send_shown(self, m: Message, chat: str,
                          entry: dict) -> None:
        """A file the agent SHOWED, sent after the text answer (docs/rich-chat.md
        O-1). A `ui` layout has no file (RC-5). Too large, missing or refused
        is SAID as one line naming the file and logged, never fatal."""
        if entry.get("kind") == "ui":
            return
        name = str(entry.get("name") or "file")
        store = self.hub.artifacts
        found = None
        if store is not None:
            found = await asyncio.to_thread(store.find, chat, entry.get("id"))
        if found is None:
            log(self.kind, f"shown file {name} for {m.id} is gone")
            await self.say(m.where, SHOWN_GONE.format(name=name), m.id)
            return
        # The size from the disk BEFORE the read: a shown video may be
        # 100 MB, and a chat app's limit is smaller.
        size = (await asyncio.to_thread(found[0].stat)).st_size
        if size > self.max_file_mb * 1024 * 1024:
            await self.say(m.where, SHOWN_TOO_LARGE.format(
                name=name, mb=round(size / (1024 * 1024))), m.id)
            return
        data = await asyncio.to_thread(found[0].read_bytes)
        try:
            await self.channel.reply(m.where, file=(name, data),
                                     caption=entry.get("caption"), quote=m.id)
        except ChannelError as e:
            log(self.kind, f"shown file {name} for {m.id} was NOT "
                           f"delivered: {e}")
            await self.say(m.where, SHOWN_FAILED.format(name=name), m.id)

    async def _voice_reply(self, m: Message, spoken: str,
                           language: str | None) -> None:
        """Synthesize the reply in the language RECEIVED and send it as a
        voice note, quoted. Every failure is a `VoiceFailed`."""
        from daemon import dialogue, speech
        try:
            text = spoken_form(spoken)
            if not text:
                raise VoiceFailed("nothing in the answer can be spoken")
            text, cut = cap_words(text)
            if cut:
                text += " " + dialogue.line(REST_IN_TEXT, language)
            voice = voice_for(self.hub, language)
            pcm = bytearray()
            for segment in speech.segments(text):
                async for chunk in self.hub.tts.synthesize(
                        segment, voice, speed=self.hub._speed(),
                        handle=(self.kind, m.id)):
                    pcm += chunk
            if not pcm:
                raise VoiceFailed("the speech engine produced no audio")
            rate = int(self.hub.tts.info.get("sample_rate") or 24000)
            ogg = await asyncio.to_thread(encode_voice_note, bytes(pcm), rate)
            await self.channel.reply(m.where, voice=ogg, quote=m.id)
            log(self.kind, f"voice reply to {m.id} sent")
        except VoiceFailed:
            raise
        except Exception as e:                            # noqa: BLE001
            raise VoiceFailed(str(e) or type(e).__name__) from e

    async def say(self, where: str, text: str, quote: str | None) -> None:
        """One text in the owner's chat, quoted when `quote` is given. A
        failure is logged: there is no other way left to tell the user."""
        about = quote or "no message"
        try:
            await self.channel.reply(where, text=text, quote=quote)
        except ChannelError as e:
            log(self.kind, f"reply to {about} was NOT delivered: {e}")
            return
        log(self.kind, f"reply to {about} sent")
