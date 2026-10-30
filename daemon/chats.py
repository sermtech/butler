"""Butler's record of a harness conversation: ONE flat JSON file per chat.

plan.md 4.4c, AC-35, and docs/harness-mode.md 10.2 by the owner's ruling of
2026-08-18.

```
state/chats/chat-<ms>-<rand>.json
```

```json
{"id": "chat-1776568428712-0kze41",
 "createdAt": 1776568428712, "updatedAt": 1776610601555,
 "harness": "pi",
 "sessionRef": "chat-1776568428712-0kze41-pi.session.json",
 "messages": [{"id": "msg-...", "role": "user", "content": "...",
               "timestamp": 1776568436645}]}
```

Rewritten WHOLE on every append, which is the reference implementation's own
write pattern and the price of its simplicity: the file's size is re-paid per
message. At voice-conversation lengths that is noise, and the alternative --
the per-session directory with an append-only event log, rotation and a
new/resumed/lost vocabulary -- is what the owner replaced.

`id`, `createdAt`, `updatedAt`, `messages` and the `{id, role, content,
timestamp}` message shape are taken VERBATIM from the reference
[READ 2026-08-18, `E:\\PROJECTS\\prototype\\CHAT-SESSIONS\\`]. The reference's
per-backend pair (`piSdkSessionId`, `claudeSessionId`) collapses to `harness`
plus one `sessionRef`, because a Butler chat runs one harness.

**`parentChatId` and `parentMessageId` ARRIVED 2026-09-10 and that is a
REVERSAL** (AC-190, `docs/rewind.md`). This docstring used to name them among
the fields deliberately not copied, "because Butler has one current
conversation" and no forking, and it said the list "is not to be extended".
The owner asked for a rewind, and a rewind that cannot say where a copy came
from is a sidebar of untitled duplicates. The reason that sentence was written
still holds for the other five and they stay out: `title` and `titleSource`
(the title is DERIVED from the first utterance), `pinned`, `archived` and
`channelId`. "The same system as the reference" is still the SHAPE and still
not a licence to cargo-cult `pinned`.

**`project` ARRIVED 2026-09-11 and is the SECOND extension** (the owner's
backlog item 4). A conversation belongs to a workspace, so the sidebar can
group by it and a reopened chat can say where its agent was working. The owner
chose this over MOVING the file into the project's own folder, and that choice
is what keeps the extension cheap: `_attach`, `_records`, `session_ref`, the
rewind's rename and `rotate` all still assume one directory and all are
untouched. It is written AT BIRTH and nowhere else (`rotate`, the rewind's
copy, and the one-time filing of blank legacy records -- `docs/filing-and-
pacing.md` 8, R-A, 2026-09-26): there is no method that re-files a record,
so a move is unrepresentable rather than guarded, and the agent's working
directory is DERIVED from this field at every spawn (R-B).

**`unspokenFrom` ARRIVED 2026-09-26 and is the THIRD extension**, on an
assistant MESSAGE rather than the chat (docs/filing-and-pacing.md 2.5): an
offset into `content` from which the text was shown and never heard, because
text now goes out before its audio and a stop can land in between. Written
only by `mark_unspoken`, which the hub calls; `content` is never rewritten.

**`agent` and `agentElapsedMs` ARRIVED 2026-10-27 and are the FOURTH
extension**, on the assistant message that ends a coding agent's turn
(docs/lantern-ui.md 8, AC-695): which agent finished (the chat's `harness`
when it did -- the chat-level field follows the setting, so a past turn needs
its own copy) and after how many milliseconds of the hub's turn clock. Written
only by `mark_finished`, which the hub calls; a stopped answer gets neither.

**The transcript and the agent's context are still two different things.**
`messages` is Butler's durable record and is what the page redisplays;
`sessionRef` points at the AGENT's own memory -- a session file pi is aimed
at, an opaque id a claude adapter would pass to `--resume` -- and that is
losable. What the flat file gives up with the event log is the tool-level
record: `messages` holds roles and text, so a resumed page can show what the
agent SAID and no longer what it DID (10.5).

**Re-attach reads `updatedAt`, which IS the explicit last-active stamp 4.4c
demands** -- one field instead of a ceremony. Never the newest NAME: sorting
resumed whatever happened to come first, which is not the conversation
anybody was having.

**A failure here is LOUD and never fatal** (D-1). A conversation history
cannot make Butler unsafe, so an unreadable one costs the history and never
the microphone -- unlike `modes.py`, which fails fast for the opposite
reason.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

# What a harness says when the session it was pointed at is gone. ONE narrow
# pattern, and deliberately narrow: matching "not found" or "session" alone
# would discard a conversation because a tool could not find a file (4.4c).
# INERT for pi, which CREATES a missing session file rather than refusing it
# [READ 2026-08-18]. It is kept rather than built out because the claude
# adapter re-earns it on day one -- eight of the predecessor's chats died on a
# stored id that outlived what it pointed at, one silent for two weeks -- and
# that is the moment this grows a test of its own (10.7 item 2).
MISSING_SESSION = re.compile(
    r"(no (conversation|session) found with (session )?id"
    r"|session not found|no such session|unknown session id)", re.I)


# How much of a first utterance becomes a conversation's TITLE (AC-164). A
# conversation has NO name: inventing a naming ceremony -- asking a model for
# one, or asking the owner -- was ruled out of scope, so what a sidebar row
# shows is the first thing the owner said, cut to a length a row can hold.
TITLE_CHARS = 60


def names_a_missing_session(message: str) -> bool:
    """Is this the harness saying the session reference is DEAD?

    The one signal that may discard a stored reference. Everything else -- a
    provider error, a refused prompt, a child that exited -- leaves it alone.
    """
    return bool(MISSING_SESSION.search(message or ""))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mint_id(prefix: str) -> str:
    """The reference's own id shape: a prefix, epoch milliseconds, and noise."""
    return f"{prefix}-{_now_ms()}-{secrets.token_hex(3)}"


#: A model name (docs/chat-model.md): letters, digits and . _ : / @ + - .
MODEL_NAME = re.compile(r"[A-Za-z0-9._:/@+\-]{1,200}")


def _blank() -> dict:
    now = _now_ms()
    return {"id": _mint_id("chat"), "createdAt": now, "updatedAt": now,
            "harness": "", "model": "", "sessionRef": "", "parentChatId": "",
            "parentMessageId": "", "project": "", "sessionGen": 0,
            "messages": []}


def _gen_suffix(chat: dict) -> str:
    """What distinguishes this conversation's CURRENT session file from the one
    a rewind moved away.

    Empty for generation 0, which is every conversation that has never been
    rewound -- so the shipped naming is unchanged and no file has to be
    renamed. `_reap_files` globs `<chat id>-*.session.json` and still catches
    these, because the suffix sits inside that wildcard.
    """
    gen = int(chat.get("sessionGen") or 0)
    return f"-r{gen}" if gen else ""


def _minted_name(chat: dict, ref: str) -> str | None:
    """The FILE NAME of `ref` when it is a session file Butler would have
    minted for THIS record, else None.

    `<chat id>-<harness>[-r<gen>].session.json` -- what `session_ref` mints
    and what a rewind's `_move_session` renames onto a snapshot, both named
    after the record that holds them. Tested on the last path component, so a
    legacy ABSOLUTE reference and the bare name stored since 2026-09-26 answer
    the same way. The directory is never read: it is what went stale when
    `state/` was copied (`docs/e2e-journeys.md`).
    """
    harness = chat.get("harness") or ""
    name = ref.replace("\\", "/").rsplit("/", 1)[-1]
    pattern = (re.escape(str(chat.get("id") or "")) + "-" + re.escape(harness)
               + r"(-r\d+)?\.session\.json")
    return name if harness and re.fullmatch(pattern, name) else None


def _rooted_ref(chat: dict) -> str:
    """What `sessionRef` should HOLD: the minted file name, an opaque id
    untouched, or "" for a path Butler did not mint.

    An opaque id is a harness's own (`caps.native_resume`, AC-44) and carries
    no directory to go stale. A path that is not one of this record's minted
    names is CLEARED rather than kept or refused: keeping it is a write
    outside the store on the next turn, and refusing the record would lose
    the conversation's words over its agent's memory (D-1). Clearing is
    `clear_session_ref`'s meaning, so `needs_seed` hands the next child the
    transcript (AC-193).
    """
    ref = str(chat.get("sessionRef") or "")
    name = _minted_name(chat, ref)
    if name is not None:
        return name
    return "" if ("/" in ref or "\\" in ref) else ref


def _turns(chat: dict) -> int:
    """How many times the owner spoke. ONE definition, two callers: the
    current conversation's count in `ready`, and every row of the chat list
    (AC-164). Two copies would let a sidebar disagree with the header."""
    return sum(1 for m in chat["messages"] if m.get("role") == "user")


#: The `origin` kind that marks a GUEST's standing chat (docs/messaging.md
#: MS-14b): someone other than the owner writes there. No owner message from
#: any channel may land in it (AC-519, AC-536, AC-609), its lines are never
#: indexed for memory search (AC-607) and never seeded into a turn (AC-608).
GUEST_ORIGIN = "whatsapp_guest"


def is_guest_chat(row: dict) -> bool:
    """Is this chat record, listing row or transcript a guest's standing
    chat? Every one of those shapes carries `origin`."""
    origin = row.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == GUEST_ORIGIN


def _window(messages: list, limit: int | None) -> list:
    """The newest `limit` messages, or all of them. One definition, so the
    current conversation and a past one are cut the same way (AC-164)."""
    return list(messages[-limit:] if limit else messages)


def _title(chat: dict) -> str:
    """The first thing the owner SAID, truncated (AC-164).

    The FIRST USER message and not the first message: a conversation whose
    record opens with an assistant line would otherwise be titled with
    Butler's own words.

    Empty when nobody has spoken yet, deliberately: "New conversation" is UI
    copy, the page owns its own words, and a title the daemon invented would
    be indistinguishable on the wire from one the owner said.

    **ONE EXCEPTION, AND IT IS MARKED: A SCHEDULED RUN** (docs/automations.md
    AU-8, AC-462). Its first user message is a filled recipe nobody typed, so
    it is titled "<recipe> - <Mon 28 Sep 09:00>" from the record's `origin`,
    fixed at birth. The objection above is answered by the mark rather than
    overridden silently: every row carries `origin`, so the page can tell a
    daemon-given title from one the owner said.
    """
    origin = chat.get("origin")
    if isinstance(origin, dict) and origin.get("title"):
        return str(origin["title"])
    for m in chat["messages"]:
        if m.get("role") != "user":
            continue
        text = " ".join(str(m.get("content") or "").split())
        if text:
            return (text if len(text) <= TITLE_CHARS
                    else text[:TITLE_CHARS].rstrip() + "...")
    return ""


# How much of a matching message a search row carries back (AC-187). Enough to
# read the phrase in context and short enough for a 264 px sidebar column.
SNIPPET_CHARS = 90


def _snippet(chat: dict, needle: str) -> str:
    """The first message whose text holds `needle`, around the hit.

    Whitespace-collapsed, because a chat record holds markdown and a snippet
    is one line in a narrow column. `needle` is already lowercase and already
    whitespace-collapsed; the message is collapsed here so a phrase split
    across a line break still matches, which is the common case for anything
    Butler said.

    "" when nothing matched, which is what the caller tests -- so a message
    that matched but produced no readable excerpt cannot become a row with
    nothing on it.
    """
    for m in chat["messages"]:
        text = " ".join(str(m.get("content") or "").split())
        at = text.lower().find(needle)
        if at < 0:
            continue
        start = max(0, at - SNIPPET_CHARS // 3)
        cut = text[start:start + SNIPPET_CHARS]
        return ("..." if start else "") + cut + ("..." if
                                                 start + SNIPPET_CHARS < len(text)
                                                 else "")
    return ""


class ChatStore:
    """`state/chats/`, the conversations Butler HOLDS, and which of them a
    turn goes to.

    A RECORD, not a preference store. It is handed to the harness brain
    because that brain is the only thing that sees the conversation, while
    the settings store stays out of every adapter (4.10).

    **THE ONE SLOT BECAME A REGISTRY** (AC-239, AC-240,
    `docs/parallel-agents.md` phase 4). `self._chat` was a single dict
    assigned in two places, so "the live conversation" was the only
    conversation this store could write to -- and the page had to leave a past
    one (`backToLive()`) before it could send anything, which is the owner's
    own rule inverted: the chat you click is the chat you talk to.
    `_open` holds every conversation this store is HOLDING, keyed by id, and
    `_current` names the one a turn goes to. `open(id)` puts a conversation in
    both.
    **ONE DICT PER RECORD, ALWAYS.** `open()` hands back the dict it already
    holds rather than re-reading the file: `_write` rewrites a record WHOLE
    from the dict in memory, so two dicts for one conversation is one of them
    silently overwriting the other's messages.
    **OPENING WRITES NOTHING AND SPAWNS NOTHING** (AC-239). It moves a
    pointer; the first send is what starts a child, because the spawn is
    reachable only from `turn()` and the spec it resolves is this
    conversation's (AC-60).
    BOUNDED BY THE OWNER'S CLICKS and not by a number, stated rather than
    discovered: every conversation opened stays held until it is deleted or
    rotated away, so browsing two hundred rows holds two hundred transcripts
    in memory. The expensive half -- a child per conversation -- is capped in
    its own phase; this half is a parsed JSON file each.
    """

    def __init__(self, root, artifacts=None) -> None:
        self.root = Path(root)
        # What an agent SHOWED (docs/rich-chat.md RC-3): an
        # `artifacts.ArtifactStore` whose folders follow their conversation
        # through `delete` and `rewind`. None in a store that never shows
        # anything (most tests).
        self.artifacts = artifacts
        # Shown during a turn, waiting for the message that turn writes
        # (RC-4): keyed (chat, role), taken by the next `append` of that role
        # in that chat. Memory only: a shown file's SNAPSHOT is on disk the
        # moment it is shown, and only the pointer waits.
        self._shown: dict[tuple[str, str], list[dict]] = {}
        self._purge_empty()
        self._reroot_session_refs()
        chat = self._attach()
        self._open: dict[str, dict] = {chat["id"]: chat}
        self._current: str = chat["id"]
        # R5's fallback for a record MINTED with no project (D-18: a
        # DAEMON-side backstop, so the invariant holds whatever the page did
        # or did not send). "" until `migrate_unfiled` tells this store the
        # root's own directory -- the same boot sequence that closes the
        # window for every EXISTING record.
        self._app_dir: str = ""

    # -- attaching ---------------------------------------------------------- #
    def _records(self) -> list[dict]:
        """Every readable chat on disk, NEWEST FIRST.

        The one place that knows which files in `state/chats/` are Butler's
        record, so re-attaching and the chat list cannot disagree about it --
        `-<harness>.session.json` is the AGENT's context and is not a
        conversation.

        A corrupt file is a LOUD skip and never the end of the walk (D-1):
        one hand-mangled record must not hide every other conversation the
        owner had, which is the difference between losing one chat and
        losing the list.

        Ties keep the order the directory listed them in, because
        `list.sort` is stable -- so the FIRST of two equal stamps is still
        the one `_attach` picks, exactly as the strictly-greater comparison
        this replaced did.
        """
        found = []
        for path in sorted(self.root.glob("chat-*.json")):
            if path.name.endswith(".session.json"):
                continue          # the AGENT's context, not Butler's record
            chat = self._read(path)
            if chat is not None:
                found.append(chat)
        found.sort(key=lambda c: c["updatedAt"], reverse=True)
        return found

    def _purge_empty(self) -> None:
        """Remove every record on disk that holds NO MESSAGES, loudly, at
        every boot that finds one (the ghost-chat defect,
        `docs/e2e-journeys.md` section 4).

        `_write` refuses to persist such a record, so none can appear from
        here on; these are what earlier builds left behind, when `rotate`
        wrote every newborn at birth and only a later `rotate` reaped it. The
        test is `_write`'s own -- `not messages` -- so an assistant-only record
        stays. The sidecar beside one goes with it (`_reap_files`).

        Parsed QUIETLY and only a well-formed empty record qualifies: an
        unreadable one is not this method's to judge or delete, and
        `_records()` reports it loudly on the walk `_attach` does next --
        once, not twice.
        """
        for path in sorted(self.root.glob("chat-*.json")):
            if path.name.endswith(".session.json"):
                continue
            try:
                chat = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(chat, dict) or chat.get("messages") not in (
                    None, []):
                continue          # `_read` reads a missing list as [] too
            print(f"[butler] removing {path.name}: a conversation record with "
                  f"no messages, which Butler no longer writes (a New chat "
                  f"nobody spoke into, left by an earlier build)")
            self._reap_files(path)

    def _reroot_session_refs(self) -> None:
        """Persist `_rooted_ref` for every record that still holds a
        directory in `sessionRef`, LOUDLY, at every boot that finds one.

        Records written before 2026-09-26 stored an ABSOLUTE path, so a copy
        of `state/` pointed back into the original and a turn or a rewind on
        the copy wrote there (`docs/e2e-journeys.md`). `_read` already
        re-roots in memory; this writes it down so the stale directory
        leaves the file too, and says so once rather than on every listing.
        An unreadable record is `_records()`'s to report, not this walk's.
        """
        for path in sorted(self.root.glob("chat-*.json")):
            if path.name.endswith(".session.json"):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            chat = self._read(path)
            if chat is None or raw.get("sessionRef", "") == chat["sessionRef"]:
                continue
            if chat["sessionRef"]:
                print(f"[butler] chat {chat['id']}: session reference "
                      f"re-rooted to {chat['sessionRef']!r} in {self.root} "
                      f"(was {raw['sessionRef']!r})")
            else:
                print(f"[butler] chat {chat['id']}: session reference "
                      f"{raw['sessionRef']!r} is not one Butler minted for "
                      f"this record; cleared rather than followed outside "
                      f"{self.root}. The words are kept and the next turn is "
                      f"re-seeded with them (AC-193)")
            self._write(chat)

    def _attach(self) -> dict:
        """Which conversation the FIRST turn after a restart goes to: the one
        with the newest `updatedAt`, or a fresh one when there is nothing --
        held in memory only, like every newborn, until something is said.

        It no longer means "the one live chat" (phase 4). Nothing on disk
        records which conversations were open when the daemon stopped, so a
        restart comes back holding exactly one -- the conversation anybody was
        having -- and every other row on disk is reached by opening it. That is
        AC-35 unchanged: the transcript and the agent's own reference both come
        back with this record.
        """
        found = self._records()
        return found[0] if found else _blank()

    def _read(self, path: Path) -> dict | None:
        """One chat file, or None and a LOUD line. Never an exception (D-1)."""
        try:
            chat = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(chat, dict):
                raise ValueError(f"{type(chat).__name__}, not an object")
            chat.setdefault("id", path.stem)
            chat["updatedAt"] = float(chat.get("updatedAt") or 0)
            chat.setdefault("createdAt", chat["updatedAt"])
            chat.setdefault("harness", "")
            chat.setdefault("sessionRef", "")
            # AC-190. Absent on every record written before rewind existed,
            # which is what `setdefault` is for: a conversation nobody
            # branched carries "" rather than being unreadable.
            # Absent on every record written before workspaces existed, which
            # is what `setdefault` is for: a bare read of one of these old
            # files (before `migrate_unfiled` has had a chance to run this
            # boot) reads as belonging to no project rather than being
            # unreadable. **THIS USED TO BE THE PERMANENT ANSWER, ON PURPOSE
            # -- "they were not all about Butler, and inventing that would be
            # the record stating a fact nobody established." That ruling is
            # OVERTURNED, R5, `docs/filing-and-pacing.md`, the owner's ruling
            # of 2026-09-16: "no unprojected conversations."** `""` here is
            # now a TRANSIENT read, closed by `ChatStore.migrate_unfiled` at
            # boot for every record that reaches it, not a permanent
            # attribution this method makes on its own.
            chat.setdefault("project", "")
            # 0 on every record written before a rewind could replace the held
            # child, which is what `session_ref` renders as no suffix at all --
            # so existing session files keep the names they have and nothing
            # needs migrating.
            chat.setdefault("sessionGen", 0)
            # A minted reference is a FILE NAME joined onto `self.root` at use
            # time; a legacy absolute one is re-rooted here, quietly, and
            # `_reroot_session_refs` persists and reports it once at boot.
            chat["sessionRef"] = _rooted_ref(chat)
            chat.setdefault("parentChatId", "")
            chat.setdefault("parentMessageId", "")
            messages = chat.get("messages") or []
            # STRICT, and the strictness is what keeps a reader simple: every
            # caller -- `turns`, `_title`, the transcript routes -- may assume
            # a list of objects, so none of them needs a shape check of its
            # own and none of them can be the one that crashes a route on a
            # hand-edited file. A record that is not that shape is unreadable,
            # which is the loud skip below rather than a silent empty
            # transcript (AC-164, D-1).
            if not isinstance(messages, list) or not all(
                    isinstance(m, dict) for m in messages):
                raise ValueError("messages is not a list of objects")
            chat["messages"] = messages
            return chat
        except (OSError, ValueError, TypeError) as e:
            print(f"[butler] chat record {path} is unreadable ({e}); "
                  f"skipping it. The conversation it held is lost; the "
                  f"microphone is not (D-1)")
            return None

    # -- reading ------------------------------------------------------------ #
    def current(self) -> dict:
        """The conversation a turn goes to right now."""
        return self._open[self._current]

    def is_open(self, chat_id: str) -> bool:
        """Is this conversation one this store is HOLDING?

        The test `delete` uses, where it used to ask "is this the live one".
        A held record's dict is what `_write` rewrites its file from, so
        unlinking one of those without taking it out of the registry is a
        delete that undoes itself on the next append -- and that is now true
        of every open conversation rather than of one.
        """
        return chat_id in self._open

    def open(self, chat_id: str) -> dict | None:
        """Make one conversation the one a turn goes to. None if there is no
        such record (AC-239, AC-240).

        THE ID IS A KEY OF WHAT THIS STORE LISTED, never a path component --
        `find()`'s rule and `find()`'s reason.

        The held dict WINS over the file. A conversation this store is already
        holding has every message the daemon has appended in it, and its file
        is rewritten whole from that dict, so re-reading the record here would
        make a second dict for one file and one of them would silently lose
        whatever the other wrote next.

        It changes a pointer and writes NOTHING -- no spawn, no session file,
        no record touched (AC-239). What the next turn does follows from the
        pointer alone: `desired()` resolves THIS conversation's session file,
        the spec differs from the running child's, and `ensure()` replaces it
        through the one comparison AC-60 already requires. There is
        deliberately no dispose call here, for `rotate`'s reason.
        """
        held = self._open.get(chat_id)
        if held is not None:
            self._current = chat_id
            return held
        for chat in self._records():
            if chat["id"] == chat_id:
                self._open[chat_id] = chat
                self._current = chat_id
                return chat
        return None

    def summary(self) -> dict:
        """What `ready` tells the page. In memory: this is read on the event
        loop during the satellite handshake, every time a device connects
        (D-25), so it may not go and count a file.

        STILL ONE ID, and that is a decision rather than an omission (phase 4).
        `session` is the conversation a turn would go to right now, which is
        what a page joining needs to know; it used to also mean "the only
        conversation this daemon holds" and no longer does. Sending the whole
        registry would be a protocol change (plan.md 4.3) drawing a list no
        satellite can act on until a frame can name a conversation -- which is
        its own phase. The staleness that follows is the one
        `POST /api/session/new` has always had: a SECOND satellite is not told
        that the conversation moved until it reconnects.
        """
        return {"session": self.current()["id"], "turns": self.turns()}

    def turns(self) -> int:
        return _turns(self.current())

    def messages(self, limit: int | None = None) -> list[dict]:
        """The transcript, oldest first, optionally the newest `limit` of it."""
        return _window(self.current()["messages"], limit)

    # -- the conversations that are NOT current (AC-164) --------------------- #
    def listing(self, limit: int | None = None,
                belongs=None) -> list[dict]:
        """Every conversation on disk, NEWEST FIRST, at most `limit` of them.

        `belongs` is a predicate over a record's `project` PATH -- None for
        "every conversation", which is what the route sends when nothing is
        scoped. This module deliberately does not know what a project ROW is
        (AC-229): it reads the directory and matches on a rule it was HANDED,
        so the store that owns the rows resolves them in the route that holds
        both. A second dependency here to answer a question the caller could
        have answered is how the two project lists drifted in the first place.

        The scope is applied BEFORE the bound, `search`'s rule and for its
        reason: bounding first would scope the newest `limit` conversations and
        call that "every chat in this project".

        `{id, title, turns, updatedAt, project, origin, harness}` per row and
        nothing else (`harness` names the agent of a running row, "pi 1:42",
        docs/lantern-ui.md section 4) --
        the messages are the other route's job, because a sidebar that shipped
        every transcript to draw twenty titles would grow without limit for
        a page that draws one conversation. `project` joined them 2026-09-11 so
        the sidebar can say which workspace a conversation belongs to without
        opening it.

        Read from DISK rather than from anything held in memory: the current
        chat's file is rewritten whole on every append (10.2), so the
        directory is the record and there is no second copy to disagree with.
        **ONE EXCEPTION, AND IT IS THE CURRENT NEWBORN.** A conversation with
        no messages is never on disk (`_write`), so the one the owner is IN
        straight after New chat is added from memory -- the row they are
        looking at. Only the CURRENT one: a newborn left behind by opening
        another chat stays HELD (a second device may still be showing it,
        and `for_turn` must reach it) but is never listed, which is what
        keeps a ghost row unrepresentable.

        `updatedAt` is an int here because it is epoch MILLISECONDS
        everywhere else Butler writes one; `_read` widens it to float only
        so an unreadable stamp cannot crash the sort.
        """
        found = self._records()
        if not self.current()["messages"]:
            found.insert(0, self.current())
        rows = [{"id": c["id"], "title": _title(c), "turns": _turns(c),
                 "updatedAt": int(c["updatedAt"]), "project": c["project"],
                 "origin": c.get("origin"), "harness": c.get("harness") or ""}
                for c in found
                if belongs is None or belongs(c["project"])]
        return rows[:limit] if limit else rows

    def search(self, query: str, limit: int | None = None,
               belongs=None) -> list[dict]:
        """`listing()`, filtered by a substring of the TITLE or the CONTENT
        (AC-185, AC-186, AC-187, `docs/chat-search.md`).

        The walk is the one `listing()` already does -- every record on disk,
        read and parsed -- so matching against message text costs a substring
        test per record and not a second pass over the directory. The route
        runs it off the event loop, because this one is hot in a way the plain
        listing is not: it fires on every debounce tick while the owner types.

        A CONTENT match carries a `snippet` and a title match does not (D2).
        A row that matched on something the owner cannot see is a row they
        have to open to find out why it is there.

        BOUNDED AFTER THE MATCH, deliberately (D4): bounding first would
        search the newest `limit` conversations and call that "every chat".

        A blank query answers `listing()` exactly, so the route has one shape
        and the page has one rendering path.

        `belongs` is `listing`'s scope predicate and the two COMPOSE: a scope
        and a query in force together mean matched AND scoped, never either.
        """
        needle = " ".join((query or "").split()).lower()
        if not needle:
            return self.listing(limit=limit, belongs=belongs)
        rows = []
        for chat in self._records():
            if belongs is not None and not belongs(chat["project"]):
                continue
            title = _title(chat)
            row = {"id": chat["id"], "title": title, "turns": _turns(chat),
                   "project": chat["project"], "origin": chat.get("origin"),
                   "harness": chat.get("harness") or "",
                   "updatedAt": int(chat["updatedAt"]), "snippet": ""}
            if needle in title.lower():
                rows.append(row)
                continue
            snippet = _snippet(chat, needle)
            if snippet:
                row["snippet"] = snippet
                rows.append(row)
        return rows[:limit] if limit else rows

    def find(self, chat_id: str, limit: int | None = None) -> dict | None:
        """One conversation by id, in `session_transcript`'s shape, or None.

        **The id is a KEY of what this store LISTED, never a path
        component.** Nothing here joins it onto `self.root`, so
        `..\\..\\secret` is an id no conversation has rather than a file
        outside `state/chats/`: a traversal is not refused, it is
        unrepresentable. That is `butlerd.TERMINAL_AGENTS`' rule in a second
        place, and for the same reason -- the caller names an entry the
        daemon itself found, so a check nobody remembers to write cannot be
        the thing standing between an HTTP request and the filesystem.

        None rather than an empty transcript for an id nobody has: the route
        turns that into a 404 naming it, and an empty 200 would tell the page
        it is showing a conversation that exists and happens to be silent.

        A HELD dict answers first, `open()`'s rule: it is what the file is
        written from, and a newborn nobody has spoken in has no file at all.
        """
        held = self._open.get(chat_id)
        for chat in [held] if held is not None else self._records():
            if chat["id"] == chat_id:
                return {"id": chat["id"], "harness": chat["harness"],
                        "model": chat.get("model", ""),
                        "project": chat["project"],
                        "title": _title(chat),
                        "origin": chat.get("origin"),
                        "messages": _window(chat["messages"], limit)}
        return None

    def title(self) -> str:
        """What this conversation is CALLED, by the rule the list already uses.

        `_title` had exactly one caller -- the listed rows -- until the live
        conversation's own header needed it (2026-09-15). A second copy of
        the rule is how the row and the header come to disagree about what a
        conversation is called, which is AC-164's argument for `_turns` and
        applies here word for word. Empty is a real answer: nobody has spoken
        yet, so the page keeps its own words for that (see `_title`).
        """
        return _title(self.current())

    # -- the agent's own reference ------------------------------------------ #
    def migrate_unfiled(self, app_dir) -> list[str]:
        """Every conversation with no project is filed under the Butler root,
        ONCE per boot that finds one (R5, `docs/filing-and-pacing.md`, the
        owner's ruling of 2026-09-16: "no unprojected conversations").

        `ShortcutStore.migrate_projects`'s shape: walk every record ON DISK --
        `_records()`, the same walk `listing()` already does -- and mint
        nothing that was not already there.

        **THIS IS RETROACTIVE BIRTH, NOT A MOVE.** A record written before
        `project` existed, or one whose birth-time filing never reached this
        store, was never told where it belongs; recording that for the first
        time is what birth always was for every OTHER conversation, just late.
        It is not the same act as re-filing an already-filed conversation --
        nothing can do that (`docs/filing-and-pacing.md` 8, R-A) -- and it
        touches ONLY records whose `project` is empty.

        **A HELD record is mutated IN PLACE before it is written.** `_open`'s
        dict wins over the file (the class docstring's own rule): if the
        conversation this migration is about to touch is also the one this
        process is currently holding, writing a FRESH copy read off disk would
        be silently undone the moment anybody appends to the held one, the
        exact hazard `delete`'s own docstring names for unlinking a held file
        out from under it.

        Answers the ids it touched, or `[]` when there was nothing to do --
        which is the ordinary case on every boot after the first, since the
        trigger (`project == ""`) is a fact about the record and not a value
        that stops existing after one read, so a second run is a genuine
        no-op and needs no flag of its own to stay correct.

        **SETS `self._app_dir` UNCONDITIONALLY, EVEN WHEN THERE IS NOTHING TO
        TOUCH** (`ShortcutStore.migrate_projects`'s own rule, for the same
        reason). `rotate()`'s backstop needs the root's own path on every
        boot after the first too, not only the one that finds something to
        migrate.

        **A BLANK RECORD NEVER REACHES THIS WALK** (2026-09-17, then
        2026-09-26). `_attach`'s fresh-boot placeholder used to be written to
        disk, and this migration filed it under the Butler root before the
        owner had said a word -- pinning `_session_cwd`, which prefers a
        recorded project over `harness_cwd`, to a directory nobody chose. A
        record with no messages is no longer on disk at all (`_write`,
        `_purge_empty`), so the in-memory placeholder is not in `_records()`
        and stays unfiled until it is spoken into.
        """
        path = str(app_dir)
        self._app_dir = path
        touched: list[str] = []
        for chat in self._records():
            if chat.get("project"):
                continue
            live = self._open.get(chat["id"], chat)
            live["project"] = path
            self._write(live)
            touched.append(live["id"])
        return touched

    def session_ref(self, chat: dict, harness: str) -> str:
        """What the next child of THIS conversation resumes on, captured on
        first use.

        Named per HARNESS, and re-minted when the harness changes: a pi
        session file means nothing to claude, and handing one over would
        resume a conversation into a reader that cannot read it (AC-44).

        **THE CONVERSATION IS AN ARGUMENT, AND THAT IS THE HINGE OF THE
        REGISTRY** (phase 4). It used to mint into `self._chat` -- the one live
        slot -- while being called per spawn from a resolver closure that names
        no conversation, so a second conversation's child would have had its
        session file written into the first one's record: the wrong agent
        memory, attached to the wrong transcript, with nothing on any wire
        saying so.
        The record must be one this store HOLDS, asserted here rather than
        assumed (D-18): a dict off `_records()` is a fresh read of a file, and
        writing a session reference into it would be overwritten by the next
        append from the dict that is actually held.
        """
        if chat.get("id") not in self._open:
            raise ValueError(
                f"a session reference may only be minted into a conversation "
                f"this store is holding; {chat.get('id')!r} is not one of "
                f"{sorted(self._open)}")
        if not chat["sessionRef"] or chat["harness"] != harness:
            chat["harness"] = harness
            chat["sessionRef"] = (f"{chat['id']}-{harness}"
                                  f"{_gen_suffix(chat)}.session.json")
            self._write(chat)
        return self._resolve_ref(chat)

    @staticmethod
    def agent_fixed(chat: dict) -> bool:
        """Has this conversation's agent been settled for good
        (docs/chat-agent-picker.md AP-2)? Once anything is said, or a child
        has a session in it, the conversation belongs to that agent."""
        return bool(chat.get("messages")) or bool(chat.get("sessionRef"))

    def is_agent_fixed(self, chat_id: str) -> bool:
        """A conversation this store does not hold has been spoken in (an
        empty one is never written), so its agent is fixed: no file read."""
        chat = self._open.get(chat_id)
        return True if chat is None else self.agent_fixed(chat)

    def choose_agent(self, chat_id: str, harness: str) -> dict:
        """Set the agent of a HELD conversation nobody has spoken in yet
        (AP-1 at birth, AP-2 afterwards). `session_ref` then mints the first
        session for this agent rather than the setting's. Not written: a
        record with no messages is never persisted (`rotate`'s ghost rule).
        KeyError for a conversation this store does not hold, ValueError
        once the agent is fixed."""
        chat = self._open.get(chat_id)
        if chat is None:
            raise KeyError(f"no open conversation {chat_id!r}")
        if self.agent_fixed(chat):
            raise ValueError(
                f"{chat_id} has already started on {chat['harness'] or 'its agent'};"
                f" a conversation keeps the agent it began with")
        chat["harness"] = harness
        # docs/chat-model.md CM-4: a model name belongs to one agent.
        chat["model"] = ""
        return chat

    def set_model(self, chat_id: str, model: str) -> dict:
        """This conversation's own model, or "" to follow its agent's default
        (docs/chat-model.md CM-1, CM-3). Changeable at any time: the next
        turn's spec differs, so the held child is replaced then (AC-60).
        Written when the record is on disk (anything said); a newborn keeps
        it in memory until its first message writes it (`rotate`'s ghost
        rule). KeyError for a conversation this store does not hold;
        ValueError for a name that is not a model name (it rides the argv,
        and pi starts through `cmd /c`), checked HERE so every caller is."""
        if model and not MODEL_NAME.fullmatch(model):
            raise ValueError(f"{model[:60]!r} is not a model name: letters, "
                             f"digits and . _ : / @ + - only")
        chat = self._open.get(chat_id)
        if chat is None:
            raise KeyError(f"no open conversation {chat_id!r}")
        chat["model"] = model
        if chat["messages"]:
            self._write(chat)
        return chat

    def _resolve_ref(self, chat: dict) -> str:
        """The reference a child is handed: a minted NAME joined onto THIS
        store's root now, or an opaque id as it is (AC-44).

        The record holds only the name, never the directory
        (`docs/e2e-journeys.md`, 2026-09-26): a stored absolute path made a
        copy of `state/` write into the original's agent memory.
        """
        name = _minted_name(chat, chat["sessionRef"])
        return str(self.root / name) if name else chat["sessionRef"]

    def clear_session_ref(self) -> None:
        """The harness says its own reference is gone, so Butler forgets it.

        Cleared, never emptied of history: the RECORD is kept and only the
        pointer to the agent's memory goes (AC-35).

        What clearing does NOT buy, stated because the obvious reading is
        wrong: for a reference Butler MINTS -- which is every file-based
        harness, pi included -- `session_ref` derives the name from the chat
        id and the harness name, so the very next call hands back a
        byte-identical path and a replay walks straight back into it. The
        clear is only meaningful for a harness that hands back an OPAQUE id
        Butler could not have guessed (`caps.native_resume`, a claude
        adapter's `--resume`). That is the same fact as the one below --
        the whole path is inert for pi -- seen from the store's side, and it
        is why nothing here mints a nonce: a fresh name for pi would be a
        conversation deliberately abandoned, which is what `rotate()` is for.
        """
        chat = self.current()
        print(f"[butler] chat {chat['id']}: the harness says its own session "
              f"reference is gone; clearing {chat['sessionRef']!r}. Butler's "
              f"record is kept; the agent's context is not (AC-35)")
        chat["sessionRef"] = ""
        self._write(chat)

    # -- removing ------------------------------------------------------------ #
    def delete(self, chat_id: str) -> bool:
        """Take one conversation off disk. True when there was one to take.

        THE ID IS A KEY OF WHAT THIS STORE LISTED, never a path component --
        `find()`'s rule and `find()`'s reason. The path unlinked is the one
        the walk itself produced, so a traversal is not refused here, it is
        unrepresentable.

        DELETING A HELD CONVERSATION TAKES IT OUT OF THE REGISTRY FIRST, and
        that order is the whole correctness of this method. A held record is a
        dict in memory and `_write` recreates its file from that dict on the
        next append -- so unlinking underneath one is a delete that silently
        undoes itself the moment anybody speaks, having already reported
        success. Doing it first also means a crash between the two steps leaves
        the old conversation orphaned but PRESENT, which is the recoverable way
        round.
        **THE TEST IS "IS IT HELD", NOT "IS IT THE LIVE ONE"** (phase 4). The
        two were the same thing while this store had one slot; with a registry,
        a conversation the owner opened and then deleted is held without being
        current, and an identity test against the current one would walk
        straight into the undo above. Deleting the CURRENT conversation rotates
        as well, because something has to be current afterwards; deleting any
        other held one is an eviction and leaves the current conversation
        exactly where it was.

        The agent's own context goes with it. `_records()` deliberately skips
        `-<harness>.session.json`, so without this the owner deletes a
        conversation and the coding agent's memory of it stays on disk with
        nothing in any UI that can reach it. Only what Butler MINTED: an
        opaque native-resume id belongs to the harness and is out of reach
        from here, which `clear_session_ref` records at more length.

        Nothing is done to a held child. Rotating mints a new `sessionRef`,
        so the next turn's `ensure()` sees a changed spec and replaces the
        child on its own -- `new_conversation`'s argument, reused rather than
        duplicated, because two invalidation rules eventually disagree
        (AC-60).

        A held NEWBORN has no file (`_write`), so it is found in the registry
        rather than on disk; its path is built from an id this store minted
        and matched as a registry key, never from the wire string alone.
        """
        found = None
        for path in sorted(self.root.glob("chat-*.json")):
            if path.name.endswith(".session.json"):
                continue
            chat = self._read(path)
            if chat is not None and chat["id"] == chat_id:
                found = (chat, path)
                break
        if found is None and chat_id in self._open:
            held = self._open[chat_id]
            found = (held, self.root / f"{held['id']}.json")
        if found is None:
            return False
        chat, path = found
        if chat["id"] == self._current:
            self.rotate()             # which unregisters it on the way
        else:
            self._open.pop(chat["id"], None)
        print(f"[butler] chat {chat['id']} deleted from the page "
              f"({_turns(chat)} turns, title {_title(chat)!r}). The "
              f"daemon console is the only record that it existed.")
        self._reap_files(path)
        for key in [k for k in self._shown if k[0] == chat["id"]]:
            del self._shown[key]
        if self.artifacts is not None:
            # With its conversation (AC-622). Loud, never fatal (D-1).
            try:
                self.artifacts.remove_chat(chat["id"])
            except OSError as e:
                print(f"[butler] WARNING: chat {chat['id']}'s shown files "
                      f"were not removed: {e}", flush=True)
        return True

    def show(self, chat_id: str, role: str, entry: dict) -> None:
        """Hang one shown thing on the NEXT `role` message of `chat_id`
        (docs/rich-chat.md RC-4). An agent's tool call happens mid-turn and
        the turn's answer is written at its end, so the entry waits here
        until that `append` takes it."""
        self._shown.setdefault((chat_id, role), []).append(entry)

    def _reap_files(self, path: Path) -> None:
        """One record and the agent context beside it, LOUD and never fatal.

        Sidecars are found by GLOB off the record's own filename rather than
        by building a name from the id, so nothing off the wire is joined
        onto a path. Bounded, and stated rather than discovered: a
        hand-edited record whose `id` field disagrees with its filename keeps
        its sidecar, because that is the only case where the two differ.

        **"NOT THERE" IS SUCCESS, and saying otherwise was a LIE IN THE LOG**
        (found 2026-09-15 in the owner's daemon log, twice). Deleting a
        conversation nobody spoke in reaches here with no file to unlink -- a
        record with no messages is never written (`_write`) -- and the
        `FileNotFoundError` that raised used to print "the file is still
        there" beside its own `WinError 2, The system cannot find the file
        specified`. Whoever read it went looking for a file that never
        existed. The goal is that
        the file IS GONE, and it is.
        """
        for victim in [path] + sorted(
                self.root.glob(f"{path.stem}-*.session.json")):
            try:
                victim.unlink()
            except FileNotFoundError:
                continue            # already gone, which is what was wanted
            except OSError as e:
                print(f"[butler] WARNING: could not remove {victim} ({e}). "
                      f"The conversation is gone from the list and the file "
                      f"is still there; the microphone is unaffected (D-1)")

    # -- writing ------------------------------------------------------------ #
    def rotate(self, project: str | None = None, focus: bool = True,
               origin: dict | None = None) -> dict:
        """A new conversation is a new RECORD, BORN IN A PROJECT, and the old
        one stays -- on disk if anything was said in it, nowhere if not.

        **THE PROJECT IS AN ARGUMENT, AND THIS IS THE ONE CALL THAT SETS IT**
        (`docs/filing-and-pacing.md` 8, R-A, AC-287). A chat belongs to the
        project it is born in and no method re-files it afterwards, so the
        link has to be made HERE, in the same statement that mints the record
        -- a second call to file it was how a chat was once briefly
        unprojected, or filed wherever the PREVIOUS conversation lived.
        `project` is a directory the caller has already put through AC-31's
        gate (`_Host.new_conversation`). None carries the outgoing
        conversation's project, falling back to the root -- the spoken "new
        conversation" with no daemon-side row table (every test Hub built
        without one).

        **THE NEWBORN IS NOT WRITTEN** (the ghost-chat defect,
        `docs/e2e-journeys.md` section 4). This used to write the fresh record
        at birth and REAP the outgoing one here if nothing had been said in
        it -- a patch on one exit, so opening another chat or deleting the
        current one left a zero-message file behind for good and the sidebar
        filled with "(nothing was said)" rows. Now `_write` refuses to persist
        a record with no messages, so a newborn lives only in the registry
        until its first message, and leaving it leaves nothing to reap.

        IT MINTS AND REGISTERS, where it used to replace the one slot (phase
        4). The outgoing conversation is UNHELD: "new conversation" ends the
        one the owner was having, so nothing may write to its record again
        until somebody opens it. An outgoing one nobody spoke in simply
        ceases to exist here.

        **`focus=False` IS A BIRTH THAT TAKES NOTHING AWAY** (docs/automations
        .md AU-7, AC-461): a scheduled run's chat. The new record is minted and
        HELD, so its turn can write to it through `for_turn`, and the current
        one is neither unheld nor replaced -- a run at 09:00 must not end the
        conversation the owner is having. Still this one method, so a run and
        New chat cannot come to disagree about what a birth is. `origin` is
        the run's mark (AU-8); it titles the record and rides every row.
        """
        outgoing = self.current()
        if focus:
            self._open.pop(outgoing["id"], None)
        fresh = _blank()
        if origin is not None:
            fresh["origin"] = dict(origin)
        # **R5's BACKSTOP** ("no unprojected conversations"): a birth that
        # names nothing lands in the outgoing conversation's project, else in
        # the root. `self._app_dir` is "" until `migrate_unfiled` has run
        # once, which is the ordinary boot order (`butlerd.py` migrates
        # before the brain that can reach this method is built).
        fresh["project"] = (project if project is not None
                            else outgoing["project"] or self._app_dir)
        self._open[fresh["id"]] = fresh
        if focus:
            self._current = fresh["id"]
        return fresh

    # -- rewinding (AC-189..AC-193, docs/rewind.md) --------------------------- #
    def needs_seed(self) -> bool:
        """Has this conversation a transcript the harness cannot remember?

        `messages` and NO `sessionRef`. That is the reference
        implementation's `appendFile && !sessionId` guard exactly, and the
        `sessionRef` half is the important word: it is Butler's own mark that
        this conversation has been handed to a child, set by `session_ref` on
        first use and CLEARED by `rewind` and by `clear_session_ref`.

        THE FIRST VERSION ASKED THE FILESYSTEM -- "is there a session file at
        that path" -- and it was wrong twice over. It seeded on EVERY turn
        against any harness whose memory is not a file Butler names (caught
        by the fake, which creates none: the second prompt of every held-child
        test arrived wrapped in a prior-conversation block). And it was a
        `Path.exists()` on a port that promises no such thing --
        `caps.native_resume` exists precisely because a harness may hand back
        an opaque id (AC-44). The mark Butler already keeps answers the
        question for both kinds.

        SELF-LIMITING: `ensure()` sets the reference on the turn right after
        this is read, so the seed is sent once. And it doubles as recovery for
        a reference the harness itself said was dead, which is the second job
        the reference implementation gives the same condition.

        NEVER A GUEST'S CHAT (RV-21, AC-608): its lines are what a stranger
        wrote, recorded as Butler's notes, and a seed would hand them to an
        agent with tools as "Assistant:" turns it believes it said.
        """
        chat = self.current()
        if is_guest_chat(chat):
            return False
        return bool(chat["messages"]) and not chat["sessionRef"]

    def rewind(self, message_id: str) -> dict | None:
        """Drop one message of the live conversation and everything after it.

        THE COPY IS MADE FIRST AND IT IS THE WHOLE POINT (AC-190). This is the
        one route in this daemon that takes something the owner said out of
        the conversation they are in, and what makes that acceptable is that
        the entire original becomes an ordinary conversation in the sidebar
        before anything is truncated. A failure between the two steps leaves
        the record WHOLE rather than gone, which is the recoverable way round
        -- `delete`'s rotate-then-unlink order, for `delete`'s reason.

        The target is NOT kept: it goes back to the composer so the owner can
        say it differently, which is what a rewind is usually for (R3).

        USER MESSAGES ONLY (R2). An assistant message is not a point a
        conversation can be continued FROM: what follows it is the owner's
        next instruction, and there would be nothing to put in the box.

        **THE AGENT'S MEMORY MOVES WITH THE DROPPED TURNS** (AC-192). Butler's
        `sessionRef` is a path Butler MINTS from the chat id, so nulling it
        buys nothing -- `session_ref` hands back a byte-identical path on the
        next call and the child resumes a file that still holds every dropped
        turn (`clear_session_ref` records that at length). So the FILE is
        renamed onto the snapshot, whose transcript it actually matches, and
        the truncated conversation is left with none. The next child starts
        fresh, and `needs_seed` above is what gives it back the kept turns.

        Answers `{"prompt", "snapshot", "id"}`, or None when no message here
        has that id -- which the route turns into a 400 naming it.
        """
        chat = self.current()
        at = next((i for i, m in enumerate(chat["messages"])
                   if m.get("id") == message_id), -1)
        if at < 0:
            return None
        target = chat["messages"][at]
        if target.get("role") != "user":
            raise ValueError(
                f"a rewind target must be something you said; {message_id} is "
                f"a {target.get('role')!r} message")

        snapshot = _blank()
        snapshot["createdAt"] = chat["createdAt"]
        snapshot["updatedAt"] = chat["updatedAt"]
        snapshot["harness"] = chat["harness"]
        snapshot["model"] = chat.get("model", "")
        snapshot["parentChatId"] = chat["id"]
        # The copy belongs to the same workspace as the original. Missed when
        # the field landed: `_read`, `listing`, `search` and `find` all learned
        # it and this is the one writer that builds a record BY HAND, so a
        # rewound conversation reported no project at all. Found by review
        # 2026-09-11.
        snapshot["project"] = chat["project"]
        snapshot["parentMessageId"] = message_id
        snapshot["messages"] = list(chat["messages"])
        snapshot["sessionRef"] = self._move_session(chat, snapshot)
        if self.artifacts is not None:
            # The copy names the same artifact ids, under its OWN id, so it
            # keeps showing them whatever later happens to the original.
            self.artifacts.copy_chat(chat["id"], snapshot["id"])
        self._write(snapshot)

        chat["messages"] = chat["messages"][:at]
        chat["sessionRef"] = ""
        # AND THE GENERATION MOVES, which is the whole of AC-192 actually
        # working. Blanking `sessionRef` alone buys nothing and this file has
        # always said so: the id does not change in a rewind, so the next
        # `session_ref` re-mints a BYTE-IDENTICAL path, `HarnessSpec` compares
        # EQUAL, and `ensure()` keeps the held child -- which still holds every
        # turn the owner just took back, in its own memory, whatever the files
        # on disk say. The owner reported exactly that on 2026-09-11
        # ("it seems llm still has the whole conversation in memory") and
        # `disposals == 0` is the measurement.
        #
        # A generation rather than a disposal call here, because AC-60 says the
        # spec comparison IS the invalidation rule and a list of events that
        # also end a child is the list that goes stale. `new_conversation` gets
        # this free from `rotate()` minting a new chat id; a rewind keeps the
        # id, so it has to move something else the spec is built from.
        chat["sessionGen"] = int(chat.get("sessionGen") or 0) + 1
        chat["updatedAt"] = _now_ms()
        self._write(chat)
        print(f"[butler] chat {chat['id']} rewound at {message_id}: "
              f"{len(snapshot['messages']) - at} message(s) dropped into "
              f"{snapshot['id']}, which is in the sidebar (AC-190)")
        return {"prompt": str(target.get("content") or ""),
                "snapshot": snapshot["id"], "id": chat["id"]}

    def _move_session(self, chat: dict, snapshot: dict) -> str:
        """Rename the harness's session file onto the snapshot. "" if none.

        A rename and not a copy: two records pointing at one session file
        would have the truncated conversation and its snapshot writing into
        each other's memory, which is worse than either of them having none.

        LOUD and never fatal (D-1). If the rename fails the snapshot simply
        has no agent memory attached, the truncated conversation is left
        pointing at nothing, and the next child starts fresh and is re-seeded
        -- which is the safe direction: the failure that matters is a child
        that still REMEMBERS the dropped turns, and that cannot happen here
        because the truncated record's reference is cleared either way.
        """
        name = _minted_name(chat, chat.get("sessionRef") or "")
        if name is None:
            return ""       # none, or an opaque id: not a file Butler owns
        source = self.root / name
        target = self.root / f"{snapshot['id']}-{chat['harness']}.session.json"
        try:
            if source.exists():
                source.replace(target)
                return target.name
        except OSError as e:
            print(f"[butler] WARNING: could not move the agent's session for "
                  f"{chat['id']} onto the rewind snapshot ({e}). The snapshot "
                  f"keeps the words and not the agent's memory of them; the "
                  f"next turn starts a fresh child (D-1)")
        return ""

    def for_turn(self, chat_id: str) -> dict:
        """The record a turn NAMED, or the current one when it named none.

        The registry read every turn path goes through since AC-241 was
        finished on 2026-09-12. A turn is told which conversation it is in by
        the daemon (`ports.TurnContext.chat`), and this turns that id into the
        one dict this store holds for it -- never a fresh read, for `open()`'s
        reason: `_write` rewrites a record WHOLE from the dict in memory, so a
        second dict for one conversation is one of them silently overwriting
        the other's messages.

        IT DOES NOT MOVE THE POINTER, and that is the difference from
        `open()`. A turn in the conversation ONE device is showing must not
        re-address every other device's next utterance; it does REGISTER one
        it was not already holding, because writing into a record means
        holding the one dict for it, and `POST /api/session/new` on another
        device unholds whatever was current -- so a desk showing A can be
        talking to a conversation the store let go of a second ago.

        A blank id is the hands-free answer and is NOT a fallback: every brain
        with no session store names none, and the conversation a turn goes to
        is the only thing that could have been meant. An id NO RECORD HAS is
        the one unresolvable case -- `showing` is deliberately not validated
        against the store (`hub._showing`) -- and it is LOUD and lands in the
        current conversation, because refusing the turn would take the
        microphone away over a satellite's stale string (D-1).
        """
        if chat_id:
            held = self._open.get(chat_id)
            if held is not None:
                return held
            for chat in self._records():
                if chat["id"] == chat_id:
                    self._open[chat_id] = chat
                    return chat
            print(f"[butler] WARNING: a turn named conversation {chat_id!r}, "
                  f"which no record has; it goes to {self._current!r} "
                  f"instead. A device is showing a conversation that is not "
                  f"there (AC-246)")
        return self.current()

    def append(self, chat_id: str, role: str, content: str,
               stopped: bool = False) -> None:
        """One message into a NAMED conversation, and its whole file rewritten.

        **THE CONVERSATION IS AN ARGUMENT, AND THAT IS AC-241 finally being
        true of the record as well as of the children.** It used to read
        `current()` at WRITE time, so a turn that started in A and was still
        answering when the owner clicked B put every remaining message of that
        turn into B -- the agent did the work in A's directory, A's record
        stopped at the question, and B gained an answer to something nobody
        asked it. Not recoverable once written, which is AC-241's own words.

        A conversation this store is HOLDING is written through the dict it
        holds, for `open()`'s reason. One it is not holding is read, appended
        and written WITHOUT being registered: nothing else holds a dict for it,
        so there is nothing to overwrite, and this is how the line a closed
        question leaves behind still reaches the conversation that asked it
        after the owner has moved on to another one.

        An id no record has is a LOUD drop and never a write somewhere else: a
        message in the wrong conversation is worse than a message nowhere,
        because the wrong one is what the owner will read.

        `stopped` marks an answer the owner STOPPED (AC-394,
        docs/turn-feedback.md): `content` is what was shown before the stop,
        possibly empty, and the page draws a "stopped" note under it. Written
        only when true, so every other message is byte-identical to before.
        """
        chat = self._open.get(chat_id) if chat_id else None
        if chat is None:
            chat = next((c for c in self._records() if c["id"] == chat_id),
                        None)
        if chat is None:
            print(f"[butler] WARNING: a {role} message named conversation "
                  f"{chat_id!r}, which no record has; it is dropped rather "
                  f"than written into another conversation (AC-241)")
            return
        message = {"id": _mint_id("msg"), "role": role, "content": content,
                   "timestamp": _now_ms()}
        if stopped:
            message["stopped"] = True
        # Written only when something was shown, so every other message is
        # byte-identical to before (AC-623).
        shown = self._shown.pop((chat["id"], role), None)
        if shown:
            message["artifacts"] = shown
        chat["messages"].append(message)
        chat["updatedAt"] = _now_ms()
        self._write(chat)

    def mark_unspoken(self, chat_id: str, tail: str, since_ms: int) -> None:
        """Mark the end of the LAST assistant message as shown and never
        heard: `unspokenFrom`, an offset into its `content` (the owner's
        delegated ruling of 2026-09-26, docs/filing-and-pacing.md item 2).

        The text is not touched -- the record keeps everything the page
        showed -- and the page draws `content[unspokenFrom:]` with the same
        `.unspoken` treatment it drew live, so a reload shows the same thing.

        Refused LOUDLY, never guessed, unless the last message is an
        assistant message written during this turn (`since_ms`) whose content
        ends with `tail`. A turn stopped before the agent finished has no
        answer on record (the adapter records at its terminal event), and
        marking the PREVIOUS turn's answer would be a lie about the one
        thing the owner cannot check.
        """
        chat, last = self._turn_answer(chat_id, since_ms)
        body, tail = (str(last.get("content") or "").rstrip()
                      if last else ""), tail.strip()
        if last is None or not tail or not body.endswith(tail):
            print(f"[butler] conversation {chat_id!r}: no answer of this "
                  f"turn on record ends with the unspoken tail, so none is "
                  f"marked ({len(tail)} chars shown and not heard)")
            return
        last["unspokenFrom"] = len(body) - len(tail)
        self._write(chat)

    def mark_finished(self, chat_id: str, elapsed_ms: int,
                      since_ms: int) -> str | None:
        """Stamp this turn's answer with WHICH agent finished it and after
        how long: `agent` (the record's `harness`, the agent this turn's
        child ran as) and `agentElapsedMs` (the hub's turn clock read at the
        agent's end). The page's finished row, "pi finished in butler, 2:31"
        (docs/lantern-ui.md 8, AC-695), redraws from these after a reload.

        Answers the agent name stamped, or None, LOUDLY, when this turn has
        no finished answer on record: a stopped one (`stopped`) did not
        finish, and an older one is the previous turn's -- `mark_unspoken`'s
        rule, read through the same lookup.
        """
        chat, last = self._turn_answer(chat_id, since_ms)
        if last is None or last.get("stopped"):
            print(f"[butler] conversation {chat_id!r}: no finished answer of "
                  f"this turn on record, so no agent is named as finishing")
            return None
        last["agent"] = chat["harness"]
        last["agentElapsedMs"] = int(elapsed_ms)
        self._write(chat)
        return chat["harness"]

    def _turn_answer(self, chat_id: str,
                     since_ms: int) -> tuple[dict | None, dict | None]:
        """The conversation and its LAST message when that is an assistant
        message written during this turn (`since_ms`), else (chat, None).
        The one reading of "this turn's answer" both markers share."""
        chat = self._open.get(chat_id) if chat_id else None
        if chat is None:
            chat = next((c for c in self._records() if c["id"] == chat_id),
                        None)
        last = chat["messages"][-1] if chat and chat["messages"] else None
        if (last is None or last.get("role") != "assistant"
                or int(last.get("timestamp") or 0) < since_ms):
            return chat, None
        return chat, last

    def _write(self, chat: dict) -> None:
        """Atomic replace, LOUD on failure and never fatal (D-1).

        Atomic because the whole file is rewritten per message: a torn write
        is not a lost message, it is a lost CONVERSATION, and the boot that
        follows re-attaches from exactly these files.

        **A RECORD WITH NO MESSAGES IS NEVER ON DISK, AND THIS IS THE ONE
        PLACE THAT RULE LIVES** (the ghost-chat defect, `docs/e2e-journeys.md`
        section 4). Every writer comes through here -- a birth's
        `session_ref`, `clear_session_ref`, a rewind to the first message --
        so none of them can leave a blank row in the sidebar, and a writer
        added tomorrow cannot either. For such a record the file is REMOVED
        rather than written: a rewind to the first message empties a record
        that WAS on disk, and leaving the old file would bring the dropped
        turns back at the next boot. The sidecar is left alone -- the
        conversation is still live. `not messages`, never zero USER turns: an
        assistant-only record is a real one.
        """
        path = self.root / f"{chat['id']}.json"
        if not chat["messages"]:
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                print(f"[butler] WARNING: could not remove the emptied chat "
                      f"record {path} ({e!r}); the next boot removes it (D-1)")
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(chat, f, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except (OSError, ValueError) as e:
            print(f"[butler] WARNING: could not write the chat record "
                  f"{path} ({e!r}). The conversation is still happening; it "
                  f"will not be fully resumable")
