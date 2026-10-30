"""Memory: markdown notes plus a search over them and past conversations.

docs/identity-memory.md 4.5 (v1, AC-378..AC-384) and 12 (v2, AC-430..AC-444).

THE FILES ARE THE RECORD (AC-430). One `.md` per note under `<records>/memory/`,
named `<YYYY-MM-DD>-<HHMMSS>-<slug>.md`; the stem is the note's ID. A small
front matter (title, date, status, private, source, supersedes) is written and
read here with no YAML library. A file with NO front matter -- a v1 note, or
one the owner dropped in by hand -- is an active, non-private note titled by
its first line and dated by its modification time, so v1 needs no migration.
The FTS5 index (`memory_index.py`) is a CACHE rebuilt from these files; nothing
is stored only there.

ONE WRITE PATH (AC-433, D-18): `write()`. The daemon route pi's extension
calls, the endpoint brain's tool call and the spoken "remember that" all end
here, so the scan, the cap and the supersede rule cannot differ between them.

THE SCAN (AC-436, 12.7) refuses before anything is written. It is a pattern
list and says so: a paraphrase passes, and a false fact is still a fact. The
structural defences are the label (notes are not instructions), the spoken
"Noted:" line, "forget that", and files the owner can read.

SUPERSEDE (AC-434): a write whose normalized title matches an ACTIVE note marks
that note superseded -- its text kept, dated, pointing at the new id -- so a
correction replaces a fact instead of sitting beside it. "forget that" deletes
the newest note and re-activates what it superseded (AC-435).

THE ALWAYS-ON INDEX (AC-442, 12.4): active, non-private notes, newest first,
one line each, capped. Private notes reach a model only through search
(AC-437).

A BROKEN FILE COSTS A LOG LINE, NEVER A TURN (AC-384, D-1).

Everything here does disk work, so callers on the event loop reach it through
`asyncio.to_thread`. One lock serialises the note files and the index, because
two writes of one title racing would each supersede the same note.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

#: The whole always-on index, overflow line included (4.5): about 500 tokens
#: at ~4 characters per token, an estimate until phase 5 measures it.
INDEX_MAX_CHARS = 2000
#: One note's line in the index.
LINE_MAX_CHARS = 200
#: A note is ONE fact (ruling 3). The cap is what keeps a model from filing a
#: conversation summary as a "note".
NOTE_MAX_CHARS = 300
TITLE_MAX_CHARS = 80
#: A run with no whitespace this long is a key, a hash or base64, never a fact
#: a person says ("very long lines", 12.7).
LONGEST_RUN = 60
#: How many words of a title name its file. ASCII only (the repository rule
#: for file names on Windows); the title keeps its accents in the CONTENT.
SLUG_WORDS = 6
SLUG_MAX_CHARS = 40
#: `memory_search`'s `limit` (AC-432).
SEARCH_LIMIT_DEFAULT = 5
SEARCH_LIMIT_MAX = 10
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

ACTIVE = "active"
SUPERSEDED = "superseded"

# -- the tools, in the OpenAI function shape (AC-439) ----------------------- #
# ONE definition. The endpoint brain sends these; `adapters/pi_memory.ts`
# declares the same names and parameters for pi, and a test holds the two
# together, because a TypeScript file cannot import this one.
TOOLS = (
    {"type": "function", "function": {
        "name": "memory_search",
        "description": (
            "Search Butler's saved notes and past conversations. Use before "
            "answering anything personal or about past decisions."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Words to look for."},
                "limit": {"type": "integer", "minimum": 1,
                          "maximum": SEARCH_LIMIT_MAX,
                          "description": "Most hits of each kind (default 5)."},
            },
            "required": ["query"],
        },
    }},
    {"type": "function", "function": {
        "name": "memory_write",
        "description": (
            "Save ONE durable fact about the user's life (a person, "
            "preference, decision, date or commitment), under 300 "
            "characters. Writing an existing title replaces that note."),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string",
                          "description": "A short topic, reused to correct it."},
                "text": {"type": "string",
                         "description": "The fact, one or two sentences."},
                "private": {"type": "boolean",
                            "description": "True keeps it out of the "
                                           "always-shown list."},
            },
            "required": ["title", "text"],
        },
    }},
)
TOOL_NAMES = tuple(t["function"]["name"] for t in TOOLS)


class MemoryRefused(ValueError):
    """A write the scan or the caps refused. Nothing was written, and the
    message is the reason in plain words, for the caller to pass on."""


@dataclass(frozen=True)
class Note:
    id: str
    path: Path
    title: str
    text: str
    date: str           # "YYYY-MM-DD HH:MM:SS"
    created: float      # epoch seconds, what "newest" means
    status: str = ACTIVE
    private: bool = False
    source: str = "owner"
    supersedes: str = ""
    superseded_by: str = ""
    superseded_on: str = ""

    @property
    def line(self) -> str:
        """What v1 called the one-liner, and what "forget that" reads back."""
        return self.text


# -- the scan (AC-436) ------------------------------------------------------ #
_FILL = r"(?:[\w'-]+\s+){0,5}"
_INJECTION = [
    (re.compile(rf"\b(?:ignore|forget|override)\s+{_FILL}(?:previous|prior|"
                rf"above|earlier|all|any)\s+{_FILL}(?:instructions?|rules|"
                rf"prompts?|messages)\b", re.I),
     "it reads like an instruction to ignore instructions"),
    (re.compile(rf"\bdisregard\s+{_FILL}(?:instructions?|rules|guidelines|"
                rf"prompts?)\b", re.I),
     "it reads like an instruction to ignore instructions"),
    (re.compile(r"\bsystem\s*prompt\b|\bdeveloper\s+message\b", re.I),
     "it talks about the system prompt"),
    (re.compile(r"<\s*/?\s*(?:system|assistant|user|tool|developer)\s*>"
                r"|<\|[a-z_]+\|>|\[/?INST\]", re.I),
     "it carries a chat role marker"),
    (re.compile(r"\byou\s+are\s+now\b|\bnew\s+instructions?\s*:", re.I),
     "it tries to redefine the assistant"),
    (re.compile(rf"\bdo\s+not\s+{_FILL}tell\s+{_FILL}(?:the\s+)?"
                rf"(?:user|owner)\b", re.I),
     "it asks to hide something from the user"),
]
_CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I)
# Zero-width, bidirectional overrides and the BOM: invisible to the owner
# reading the file, visible to a model.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def scan(title: str, text: str) -> tuple[str, str]:
    """`(title, text)` cleaned of surrounding space, or `MemoryRefused`."""
    if not isinstance(title, str) or not isinstance(text, str):
        raise MemoryRefused("a note needs a title and a text, both strings")
    title, text = title.strip(), text.strip()
    if not title or not text:
        raise MemoryRefused("a note needs both a title and a text")
    if "\n" in title or "\r" in title:
        raise MemoryRefused("the title must be one line")
    if len(title) > TITLE_MAX_CHARS:
        raise MemoryRefused(f"the title is {len(title)} characters, over "
                            f"{TITLE_MAX_CHARS}")
    if len(text) > NOTE_MAX_CHARS:
        raise MemoryRefused(f"the note is {len(text)} characters, over "
                            f"{NOTE_MAX_CHARS}; save one short fact")
    scan_content(f"{title}\n{text}")
    return title, text


def scan_content(whole: str) -> None:
    """The content half of the scan, or `MemoryRefused`. ONE definition of
    AC-436's patterns: a note's title and text go through it, and so does a
    task an agent adds (docs/automations.md AU-9, AC-454)."""
    if any(len(run) >= LONGEST_RUN for run in whole.split()):
        raise MemoryRefused(f"it has a run of {LONGEST_RUN} or more characters "
                            f"with no space, which is a key or code, not a "
                            f"fact")
    if _INVISIBLE.search(whole):
        raise MemoryRefused("it holds invisible characters")
    if _CREDENTIAL_URL.search(whole):
        raise MemoryRefused("it holds a web address with a password in it")
    for pattern, why in _INJECTION:
        if pattern.search(whole):
            raise MemoryRefused(why)


# -- the file --------------------------------------------------------------- #
_FIELDS = ("title", "date", "status", "private", "source", "supersedes",
           "superseded_by", "superseded_on")


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:SLUG_WORDS]
    return "-".join(words)[:SLUG_MAX_CHARS].strip("-") or "note"


def topic(title: str) -> str:
    """The supersede key (12.2): case-folded, every run of non-alphanumerics
    one space."""
    return " ".join(re.findall(r"\w+", title.casefold()))


def derive_title(text: str) -> str:
    """A title for "remember that" (no model names it): the first line, cut
    at a word boundary under the cap."""
    first = " ".join(text.split())
    if len(first) <= TITLE_MAX_CHARS:
        return first
    return first[:TITLE_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,.;:") or \
        first[:TITLE_MAX_CHARS]


def _render(note: Note) -> str:
    head = ["---", f"title: {note.title}", f"date: {note.date}",
            f"status: {note.status}",
            f"private: {'true' if note.private else 'false'}",
            f"source: {note.source}"]
    for key in ("supersedes", "superseded_by", "superseded_on"):
        value = getattr(note, key)
        if value:
            head.append(f"{key}: {value}")
    return "\n".join(head + ["---", note.text, ""])


def parse(path: Path) -> Note:
    """One note from its file. Raises `ValueError` (malformed) or
    `UnicodeDecodeError` / `OSError`; `MemoryStore.notes` logs and skips."""
    raw = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    st = path.stat()
    stripped = raw.lstrip("\n")
    if stripped.startswith("---\n"):
        end = stripped.find("\n---", 3)
        if end == -1:
            raise ValueError("its front matter never closes")
        fields = {}
        for line in stripped[4:end].split("\n"):
            if not line.strip():
                continue
            key, sep, value = line.partition(":")
            if not sep or key.strip() not in _FIELDS:
                raise ValueError(f"unknown front matter line {line!r}")
            fields[key.strip()] = value.strip()
        text = stripped[end + 4:].lstrip("-").strip()
        title = fields.get("title", "")
        if not title or not text:
            raise ValueError("it has no title or no text")
        date = fields.get("date", "")
        try:
            created = time.mktime(time.strptime(date, DATE_FORMAT))
        except ValueError:
            raise ValueError(f"its date {date!r} is not {DATE_FORMAT}")
        status = fields.get("status", ACTIVE)
        if status not in (ACTIVE, SUPERSEDED):
            raise ValueError(f"its status {status!r} is not active or "
                             f"superseded")
        return Note(id=path.stem, path=path, title=title, text=text,
                    date=date, created=created, status=status,
                    private=fields.get("private", "false").lower() == "true",
                    source=fields.get("source", "owner"),
                    supersedes=fields.get("supersedes", ""),
                    superseded_by=fields.get("superseded_by", ""),
                    superseded_on=fields.get("superseded_on", ""))
    text = raw.strip()
    if not text:
        raise ValueError("it is empty")
    first = next(ln.strip() for ln in text.splitlines() if ln.strip())
    return Note(id=path.stem, path=path, title=first.lstrip("#").strip()
                or first, text=text, created=st.st_mtime,
                date=time.strftime(DATE_FORMAT, time.localtime(st.st_mtime)))


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)


def _one_line(text: str) -> str:
    return " ".join(text.split())


# -- the provenance ledger (AC-445, 12.11) ----------------------------------- #
# APPEND-ONLY JSONL beside the notes, and a SOURCE OF TRUTH rather than part of
# the cache: it cannot be rebuilt from the note files, because "forget that"
# deletes a file and a restore clears the supersede fields -- afterwards
# nothing on disk says either happened. Memoose keeps the same ledger as a
# table (`store/schema.py:66-70`) because its database IS its record; here the
# database is a cache, so the ledger lives in a file and the index reads it.
LEDGER = "provenance.jsonl"
WHO_AGENT = "agent-tool"        # memory_write, from either brain
WHO_SPOKEN = "spoken-command"   # "remember that ..." / "forget that"
WHO_HAND = "hand-edit"          # a file changed outside the store


def _stamp(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _sha(path: Path) -> str:
    """What version of a note a ledger line is about: 16 hex of its bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def read_ledger(path: Path) -> list[dict]:
    """Every well-formed line, in order. A malformed line is a loud log line
    and skipped -- a damaged history line must never fail a turn (D-1)."""
    try:
        raw = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return []
    entries = []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or not isinstance(
                    entry.get("note"), str):
                raise ValueError("not an object with a note id")
        except ValueError as e:
            print(f"[butler] memory ledger {path} line {n} is skipped: {e}",
                  flush=True)
            continue
        entries.append(entry)
    return entries


class MemoryStore:
    """The note files, their always-on index, and the search over them.

    `index` is a `memory_index.MemoryIndex` or None. None is a store built
    without a search (the suite's direct constructions): `search` then
    raises, loudly, rather than answering nothing -- an empty result would
    tell a model the owner has no memories.
    """

    def __init__(self, root: Path, index=None):
        self.root = Path(root)
        self.index_db = index
        self._lock = threading.RLock()
        # Two caches keyed on a file's (mtime_ns, size), so `reconcile` --
        # run before every write, forget and search -- re-reads only what
        # moved. Without them it re-read the ledger and re-hashed every note
        # per call: MEASURED ~130 ms a write at ~150 notes.
        self._ledger_seen: tuple | None = None
        self._ledger_state: dict[str, str] = {}
        self._hashes: dict[str, tuple] = {}
        # And a third, for `notes()`: a parsed note per path and stamp. The
        # index is built every turn, and re-opening every note file for it
        # was MEASURED at ~0.1 ms a file on this machine.
        self._parsed: dict[Path, tuple] = {}

    # -- reading ------------------------------------------------------------ #
    def notes(self) -> list[Note]:
        """Every readable note, NEWEST FIRST by creation (12.4). A malformed
        file is a loud line and left out (AC-384)."""
        if not self.root.is_dir():
            return []
        found = []
        with self._lock:
            paths = list(self.root.glob("*.md"))
            for gone in set(self._parsed) - set(paths):
                del self._parsed[gone]
            for path in paths:
                stamp = _stamp(path)
                cached = self._parsed.get(path)
                if cached is not None and cached[0] == stamp:
                    if cached[1] is not None:
                        found.append(cached[1])
                    continue
                try:
                    note = parse(path)
                except (OSError, UnicodeDecodeError, ValueError) as e:
                    # Logged once per version of the file, not once a turn.
                    print(f"[butler] memory: {path} is left out (AC-384): "
                          f"{e}", flush=True)
                    note = None
                self._parsed[path] = (stamp, note)
                if note is not None:
                    found.append(note)
        found.sort(key=lambda n: (n.created, n.path.name), reverse=True)
        return found

    def active(self) -> list[Note]:
        return [n for n in self.notes() if n.status == ACTIVE]

    def index(self) -> str:
        """The capped always-on index, or "" when nothing belongs in it
        (AC-442). Private notes are never here (AC-437)."""
        lines = []
        for n in self.active():
            if n.private:
                continue
            text = _one_line(n.text)
            body = text if topic(text).startswith(topic(n.title)) \
                else f"{n.title}: {text}"
            lines.append(f"- {n.date[:10]} {body}"[:LINE_MAX_CHARS])
        whole = "\n".join(lines)
        if len(whole) <= INDEX_MAX_CHARS:
            return whole
        for k in range(len(lines) - 1, -1, -1):
            omitted = len(lines) - k
            tail = (f"({omitted} older note(s) omitted; memory_search finds "
                    f"them, and every note is a file in {self.root})")
            text = "\n".join(lines[:k] + [tail])
            if len(text) <= INDEX_MAX_CHARS:
                print(f"[butler] memory index over {INDEX_MAX_CHARS} "
                      f"characters: the {omitted} oldest note(s) are left out "
                      f"of the prompt and are still in {self.root}",
                      flush=True)
                return text
        raise AssertionError("unreachable: the overflow line alone fits")

    # -- provenance: who changed which note, when (AC-445) ------------------ #
    @property
    def ledger(self) -> Path:
        return self.root / LEDGER

    def _known(self) -> dict[str, str]:
        """note id -> the sha of the last version the LEDGER recorded ("" for
        a deletion). Re-read whenever the file's stamp is not the one this
        store last read or wrote, so an outside append is still seen."""
        stamp = _stamp(self.ledger)
        if stamp != self._ledger_seen:
            known: dict[str, str] = {}
            for entry in read_ledger(self.ledger):
                known[entry["note"]] = entry.get("sha") or ""
            self._ledger_state, self._ledger_seen = known, stamp
        return self._ledger_state

    def _log(self, action: str, who: str, note: str, sha: str = "",
             **extra) -> None:
        """Append ONE ledger line. Append-only: nothing here ever rewrites or
        truncates the file, so a line once written is history."""
        entry = {"at": time.strftime(DATE_FORMAT), "action": action,
                 "who": who, "note": note, "sha": sha, **extra}
        self.root.mkdir(parents=True, exist_ok=True)
        fresh = self._ledger_seen == _stamp(self.ledger)
        with open(self.ledger, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=True, sort_keys=True)
                    + "\n")
        if fresh:
            # The cache was current before this line, so it is current after
            # it with this one entry applied; otherwise the next read re-reads.
            self._ledger_state[note] = sha
            self._ledger_seen = _stamp(self.ledger)

    def reconcile(self) -> list[str]:
        """Record, as `who: hand-edit`, every note file that changed without
        the store: a new file the ledger never saw ("found" -- a v1 note or
        one the owner dropped in), a changed one ("edit"), a missing one
        ("delete"). Run at boot and before every store operation, so a hand
        edit is attributed BEFORE the store's own next change lands on top of
        it. Answers the note ids it logged."""
        with self._lock:
            known = self._known()
            logged = []
            present = {}
            if self.root.is_dir():
                for path in self.root.glob("*.md"):
                    try:
                        stamp = _stamp(path)
                        seen = self._hashes.get(path.stem)
                        if seen is None or seen[0] != stamp:
                            seen = (stamp, _sha(path))
                            self._hashes[path.stem] = seen
                        present[path.stem] = seen[1]
                    except OSError:
                        continue
            for note_id, sha in present.items():
                if note_id not in known or (known[note_id] and
                                            known[note_id] != sha):
                    action = "found" if note_id not in known else "edit"
                    self._log(action, WHO_HAND, note_id, sha)
                    logged.append(note_id)
                elif not known[note_id]:
                    # Deleted, then a file of that name came back.
                    self._log("found", WHO_HAND, note_id, sha)
                    logged.append(note_id)
            for note_id, sha in known.items():
                if sha and note_id not in present:
                    self._log("delete", WHO_HAND, note_id)
                    logged.append(note_id)
            return logged

    # -- writing: the ONE path (AC-433) -------------------------------------- #
    def write(self, title: str, text: str, private: bool = False,
              source: str = "agent") -> tuple[Note, list[Note]]:
        """Scan, supersede, write. Answers `(note, superseded)`.

        Raises `MemoryRefused` with nothing written (AC-436)."""
        if not isinstance(private, bool):
            raise MemoryRefused("private must be true or false")
        title, text = scan(title, text)
        who = WHO_SPOKEN if source == "owner" else WHO_AGENT
        with self._lock:
            self.reconcile()
            self.root.mkdir(parents=True, exist_ok=True)
            now = time.time()
            stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))
            path = self.root / f"{stamp}-{_slug(title)}.md"
            n = 2
            while path.exists():
                path = self.root / f"{stamp}-{_slug(title)}-{n}.md"
                n += 1
            key = topic(title)
            old = [o for o in self.active() if topic(o.title) == key]
            date = time.strftime(DATE_FORMAT, time.localtime(now))
            note = Note(id=path.stem, path=path, title=title, text=text,
                        date=date, created=now, private=private,
                        source=source,
                        supersedes=",".join(o.id for o in old))
            _atomic(path, _render(note))
            self._log("remember-command" if who == WHO_SPOKEN else
                      ("supersede" if old else "write"), who, note.id,
                      _sha(path), superseded=[o.id for o in old])
            for o in old:
                # A front-matter-less (v1 or hand-made) note GAINS one here:
                # that is the only way to mark it superseded and keep its text.
                _atomic(o.path, _render(replace(
                    o, status=SUPERSEDED, superseded_by=note.id,
                    superseded_on=date)))
                self._log("superseded", who, o.id, _sha(o.path),
                          superseded_by=note.id)
            self._sync()
        return note, old

    def remember(self, text: str) -> Path:
        """The spoken "remember that ..." (AC-378, ruling 7): the owner's
        words verbatim as the text, the title derived from them, through
        `write` like every other note."""
        return self.write(derive_title(text), text, source="owner")[0].path

    def forget_last(self) -> str | None:
        """Delete the NEWEST note by creation, re-activate what it
        superseded, and answer its text -- or None when there is nothing
        (AC-379, AC-435). Superseded notes are history, not "the last thing
        I remembered", so only an active note is forgotten."""
        with self._lock:
            self.reconcile()
            notes = self.active()
            if not notes:
                return None
            gone = notes[0]
            gone.path.unlink()
            wanted = set(filter(None, gone.supersedes.split(",")))
            restored = []
            for n in self.notes():
                if n.id in wanted and n.status == SUPERSEDED:
                    _atomic(n.path, _render(replace(
                        n, status=ACTIVE, superseded_by="",
                        superseded_on="")))
                    restored.append(n)
            self._log("forget", WHO_SPOKEN, gone.id,
                      restored=[n.id for n in restored])
            for n in restored:
                self._log("restore", WHO_SPOKEN, n.id, _sha(n.path))
            self._sync()
            return gone.text

    # -- search (AC-432) ----------------------------------------------------- #
    def search(self, query: str, limit: int = SEARCH_LIMIT_DEFAULT) -> list:
        if self.index_db is None:
            raise RuntimeError("this memory store was built without a search "
                               "index")
        if isinstance(limit, bool) or not isinstance(limit, int) or not (
                1 <= limit <= SEARCH_LIMIT_MAX):
            raise MemoryRefused(f"limit must be a whole number from 1 to "
                                f"{SEARCH_LIMIT_MAX}")
        if not isinstance(query, str):
            raise MemoryRefused("query must be text")
        with self._lock:
            self.reconcile()
            return self.index_db.search(query, limit)

    def _sync(self) -> None:
        if self.index_db is not None:
            self.index_db.sync()

    # -- the tools, for any caller (AC-433, AC-439) -------------------------- #
    def call_tool(self, name: str, args) -> str:
        """Run one tool call and answer the text a model is shown.

        A refused or malformed call is ANSWERED, never raised: it is model
        output, and plan.md 3.8's exception for bad model output applies --
        the model is told why and may try again. An unknown tool is the same.
        """
        if not isinstance(args, dict):
            return "Error: the arguments must be a JSON object."
        try:
            if name == "memory_search":
                hits = self.search(args.get("query"),
                                   args.get("limit", SEARCH_LIMIT_DEFAULT))
                return render_hits(hits)
            if name == "memory_write":
                note, old = self.write(args.get("title"), args.get("text"),
                                       args.get("private", False))
                return render_written(note, old)
        except MemoryRefused as e:
            return f"Error: not saved or not searched: {e}."
        return f"Error: there is no tool called {name!r}."


def render_written(note: Note, old: list[Note]) -> str:
    said = f"Saved note {note.id} ({note.title})."
    if old:
        said += (" It replaced: " + "; ".join(
            f"{o.id} ({o.date[:10]}: {_one_line(o.text)})" for o in old)
            + ", kept as history.")
    return said


def render_hits(hits: list) -> str:
    """The ONE rendering of a search result, shown to both brains."""
    if not hits:
        return "No saved notes or past conversations matched."
    out = []
    for h in hits:
        if h["kind"] == "note":
            saved = (f" (saved by {h['saved_by']})" if h.get("saved_by")
                     else "")
            line = (f"[note {h['id']}] {h['date'][:10]} {h['title']}"
                    f"{' (private)' if h['private'] else ''}{saved}"
                    f": {h['text']}")
            if h.get("previous"):
                p = h["previous"]
                line += (f" (replaced an earlier note of {p['date'][:10]}: "
                         f"{p['text']})")
        else:
            line = (f"[chat {h['id']}] {h['date'][:10]} \"{h['title']}\" "
                    f"in project {h['project'] or '(none)'}: {h['snippet']}")
        out.append(line)
    return "\n".join(out)

