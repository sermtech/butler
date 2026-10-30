"""The search over memory notes and past conversations: a SQLite FTS5 CACHE.

docs/identity-memory.md 12.3 (AC-431, AC-432). Python's own `sqlite3`, no
embeddings, no graph (the owner's ruling 1).

A CACHE AND NOTHING ELSE. Every row is derived from a file -- a note under
`<records>/memory/` or a conversation record `<records>/chats/chat-*.json` --
so the database can be deleted at any moment and loses nothing. `open()` at
boot deletes and rebuilds a file that will not open, fails `quick_check`, or
carries another schema number; `sync()` then brings the rows in line with the
files.

SYNC BY STAT, before every search. It compares each file's (mtime_ns, size)
with the row it produced, re-reads what moved and drops what is gone -- so a
chat `chats.py` just appended to, and a note the owner edited by hand, are
found by the very next search with no hook anywhere else. An agent's own
`*.session.json` is its context, not Butler's record, and is never indexed.
A file that cannot be read is remembered as BROKEN at its stamp, so it is
logged once rather than on every search, and retried when it changes.

Blocking by nature: every caller on the event loop goes through
`asyncio.to_thread` (D-1), and the owning `MemoryStore` holds one lock over
the connection.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from . import memory as memory_mod
from .chats import _title as chat_title
from .chats import is_guest_chat

#: Bumped whenever the tables change; an index with another number is rebuilt.
#: 2: the porter tokenizer and the provenance table (12.11).
#: 3: a guest's chat is never indexed (RV-21, AC-607); the bump rebuilds every
#: index that already holds one, so old guest text is gone at the next open.
SCHEMA = 3
#: How much of a matching conversation a hit carries, in FTS5 tokens.
SNIPPET_TOKENS = 18
#: More query words than this add noise and no recall.
MAX_TERMS = 16

_DDL = (
    "CREATE TABLE docs (rowid INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
    " kind TEXT NOT NULL, id TEXT, mtime INTEGER, size INTEGER, title TEXT,"
    " date TEXT, project TEXT, status TEXT, private INTEGER,"
    " supersedes TEXT, text TEXT)",
    # porter + diacritic folding, Memoose's tokenizer (`store/schema.py:42-43`):
    # "voce" finds "voce" with its circumflex and back, and English
    # inflections meet
    # ("puppies" finds "puppy"). Porter is ENGLISH stemming -- Portuguese only
    # gets the folding, plus porter's "-s" rule by coincidence (12.11).
    "CREATE VIRTUAL TABLE fts USING fts5(title, body,"
    " tokenize='porter unicode61 remove_diacritics 2')",
    # The ledger, indexed (12.11). Derived from `<memory>/provenance.jsonl`,
    # which is the record; this table is dropped and re-read when it moves.
    "CREATE TABLE provenance (seq INTEGER PRIMARY KEY, at TEXT, action TEXT,"
    " who TEXT, note TEXT, sha TEXT, other TEXT)",
    "CREATE INDEX provenance_note ON provenance(note)",
    "CREATE TABLE ledger_stamp (id INTEGER PRIMARY KEY CHECK (id = 1),"
    " mtime INTEGER, size INTEGER)",
)


def _fts5_probe() -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    finally:
        con.close()


def require_fts5() -> None:
    """Fail fast at boot when this Python's SQLite has no FTS5 (ruling 1)."""
    try:
        _fts5_probe()
    except sqlite3.OperationalError as e:
        raise SystemExit(
            f"[butler] memory search needs SQLite's FTS5 and this Python's "
            f"SQLite {sqlite3.sqlite_version} does not have it ({e}). Use a "
            f"standard CPython build (python.org or uv), whose sqlite3 ships "
            f"FTS5.") from e


def _terms(query: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", (query or "").lower())
             if len(w) > 1]
    return list(dict.fromkeys(words))[:MAX_TERMS]


class MemoryIndex:
    def __init__(self, db_path: Path, notes_root: Path, chats_root: Path,
                 project_label=None):
        self.path = Path(db_path)
        self.notes_root = Path(notes_root)
        self.chats_root = Path(chats_root)
        # path -> the pane row's label, or None. A callable so this module
        # knows nothing about shortcuts (the `ChatStore.listing` rule).
        self._label = project_label
        self._con: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def open(self) -> str | None:
        """Open, rebuilding when needed, then sync. Answers why it rebuilt,
        or None when the existing file was reused."""
        require_fts5()
        reason = None
        if not self.path.exists():
            reason = "missing"
        else:
            con = sqlite3.connect(self.path)
            try:
                check = con.execute("PRAGMA quick_check").fetchone()[0]
                version = con.execute("PRAGMA user_version").fetchone()[0]
                if check != "ok":
                    reason = f"it failed quick_check ({check})"
                elif version != SCHEMA:
                    reason = f"its schema is {version}, this build uses {SCHEMA}"
            except sqlite3.DatabaseError as e:
                reason = f"it is unreadable ({e})"
            finally:
                con.close()
            if reason:
                self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        if reason:
            for ddl in _DDL:
                self._con.execute(ddl)
            self._con.execute(f"PRAGMA user_version = {SCHEMA}")
            self._con.commit()
        self.sync()
        return reason

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # -- keeping it in line with the files ---------------------------------- #
    def _files(self) -> dict[str, Path]:
        found = {}
        if self.notes_root.is_dir():
            for p in self.notes_root.glob("*.md"):
                found[str(p)] = p
        if self.chats_root.is_dir():
            for p in self.chats_root.glob("chat-*.json"):
                if not p.name.endswith(".session.json"):
                    found[str(p)] = p
        return found

    def _sync_ledger(self) -> bool:
        """Re-read the provenance ledger when its stamp moved. Whole-file,
        because it is small and append-only; True when it was re-read."""
        con = self._need()
        path = self.notes_root / memory_mod.LEDGER
        try:
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = (0, 0)
        row = con.execute("SELECT mtime, size FROM ledger_stamp").fetchone()
        if row is not None and tuple(row) == stamp:
            return False
        con.execute("DELETE FROM provenance")
        for e in memory_mod.read_ledger(path):
            other = {k: v for k, v in e.items()
                     if k not in ("at", "action", "who", "note", "sha")}
            con.execute("INSERT INTO provenance (at, action, who, note, sha,"
                        " other) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(e.get("at", "")), str(e.get("action", "")),
                         str(e.get("who", "")), e["note"],
                         str(e.get("sha", "")), json.dumps(other)))
        con.execute("INSERT OR REPLACE INTO ledger_stamp (id, mtime, size)"
                    " VALUES (1, ?, ?)", stamp)
        return True

    def history(self, note_id: str) -> list[dict]:
        """Every ledger line about one note, oldest first."""
        self.sync()
        return [{"at": at, "action": action, "who": who, "sha": sha,
                 **json.loads(other or "{}")}
                for at, action, who, sha, other in self._need().execute(
                    "SELECT at, action, who, sha, other FROM provenance"
                    " WHERE note = ? ORDER BY seq", (note_id,))]

    def sync(self) -> tuple[int, int]:
        """`(re-indexed, removed)` after bringing the rows in line."""
        con = self._need()
        ledger_moved = self._sync_ledger()
        files = self._files()
        have = {path: (rowid, mtime, size) for rowid, path, mtime, size in
                con.execute("SELECT rowid, path, mtime, size FROM docs")}
        removed = 0
        for path, (rowid, _, _) in have.items():
            if path not in files:
                self._drop(rowid)
                removed += 1
        changed = 0
        for path, p in files.items():
            try:
                st = p.stat()
            except OSError:
                continue       # vanished between the glob and the stat
            old = have.get(path)
            if old is not None and (old[1], old[2]) == (st.st_mtime_ns,
                                                         st.st_size):
                continue
            if old is not None:
                self._drop(old[0])
            self._add(p, st)
            changed += 1
        if changed or removed or ledger_moved:
            con.commit()
        return changed, removed

    def _drop(self, rowid: int) -> None:
        self._con.execute("DELETE FROM docs WHERE rowid = ?", (rowid,))
        self._con.execute("DELETE FROM fts WHERE rowid = ?", (rowid,))

    def _add(self, p: Path, st) -> None:
        row = {"path": str(p), "mtime": st.st_mtime_ns, "size": st.st_size}
        try:
            if p.suffix == ".md":
                note = memory_mod.parse(p)
                row.update(kind="note", id=note.id, title=note.title,
                           date=note.date, project="", status=note.status,
                           private=int(note.private),
                           supersedes=note.supersedes, text=note.text)
                body = note.text
            else:
                chat = json.loads(p.read_bytes().decode("utf-8"))
                messages = chat["messages"]
                stamp = float(chat.get("updatedAt") or 0) / 1000.0
                project = str(chat.get("project") or "")
                row.update(kind="chat", id=str(chat["id"]),
                           title=chat_title(chat),
                           date=time.strftime(memory_mod.DATE_FORMAT,
                                              time.localtime(stamp)),
                           project=project, status="", private=0,
                           supersedes="", text="")
                body = "\n".join(f"{m.get('role')}: {m.get('content') or ''}"
                                 for m in messages)
                if is_guest_chat(chat):
                    # RV-21 (AC-607): a STRANGER's words, never searchable by
                    # an owner turn with tools. Kept as a row with no text so
                    # the stat sync does not re-read it on every search.
                    row.update(kind="guest", title="", date="", project="")
                    body = None
        except (OSError, UnicodeDecodeError, ValueError, KeyError,
                TypeError) as e:
            # Remembered as BROKEN at this stamp: one log line, not one per
            # search, and retried the moment the file changes (AC-384).
            print(f"[butler] memory index: {p} is left out of search: {e!r}",
                  flush=True)
            row.update(kind="broken", id="", title="", date="", project="",
                       status="", private=0, supersedes="", text="")
            body = None
        cur = self._con.execute(
            "INSERT INTO docs (path, kind, id, mtime, size, title, date,"
            " project, status, private, supersedes, text) VALUES"
            " (:path, :kind, :id, :mtime, :size, :title, :date, :project,"
            " :status, :private, :supersedes, :text)", row)
        if body is not None:
            self._con.execute("INSERT INTO fts (rowid, title, body)"
                              " VALUES (?, ?, ?)",
                              (cur.lastrowid, row["title"], body))

    # -- the search (AC-432) ------------------------------------------------ #
    def search(self, query: str, limit: int) -> list[dict]:
        """Active notes first, then conversations, each up to `limit`."""
        terms = _terms(query)
        if not terms:
            return []
        self.sync()
        match = " OR ".join(f'"{t}"*' for t in terms)
        con = self._need()
        hits = []
        for (id_, title, date, text, private, supersedes) in con.execute(
                "SELECT d.id, d.title, d.date, d.text, d.private, d.supersedes"
                " FROM fts JOIN docs d ON d.rowid = fts.rowid"
                " WHERE fts MATCH ? AND d.kind = 'note'"
                " AND d.status = 'active'"
                " ORDER BY bm25(fts, 4.0, 1.0) LIMIT ?", (match, limit)):
            who = con.execute(
                "SELECT who FROM provenance WHERE note = ? AND action NOT IN"
                " ('superseded', 'delete', 'forget') ORDER BY seq DESC"
                " LIMIT 1", (id_,)).fetchone()
            hit = {"kind": "note", "id": id_, "title": title, "date": date,
                   "text": text, "private": bool(private), "previous": None,
                   "saved_by": who[0] if who else ""}
            if supersedes:
                ids = [s for s in supersedes.split(",") if s]
                prev = con.execute(
                    "SELECT id, date, text FROM docs WHERE kind = 'note' AND"
                    f" id IN ({','.join('?' * len(ids))})"
                    " ORDER BY date DESC LIMIT 1", ids).fetchone()
                if prev:
                    hit["previous"] = {"id": prev[0], "date": prev[1],
                                       "text": prev[2]}
            hits.append(hit)
        for (id_, title, date, project, snippet) in con.execute(
                "SELECT d.id, d.title, d.date, d.project,"
                " snippet(fts, 1, '', '', '...', ?)"
                " FROM fts JOIN docs d ON d.rowid = fts.rowid"
                " WHERE fts MATCH ? AND d.kind = 'chat'"
                " ORDER BY bm25(fts, 4.0, 1.0) LIMIT ?",
                (SNIPPET_TOKENS, match, limit)):
            label = self._label(project) if (self._label and project) else None
            hits.append({"kind": "chat", "id": id_, "title": title,
                         "date": date,
                         "project": label or (Path(project).name if project
                                              else ""),
                         "project_path": project,
                         "snippet": " ".join(snippet.split())})
        return hits

    def _need(self) -> sqlite3.Connection:
        if self._con is None:
            raise RuntimeError("the memory index was never opened")
        return self._con
