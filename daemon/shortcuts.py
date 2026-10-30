"""The project shortcuts: one binding table, owned by the owner.

`docs/shortcuts.md`. A shortcut ROW is a directory, a label, and a chord per
coding agent. Together with one dictation chord they are the WHOLE binding
table -- the thing that replaced `terminal_cwd`, the hardcoded
`Ctrl + numpad *`, and the "starred row" idea, all three at once.

WHY A STORE AND NOT A SETTING
-----------------------------
`Setting` of `kind="list"` is a flat list of STRINGS written whole
(`settings.py:888-896`): it strips or lowercases each entry, refuses anything
that is not a string, and has no notion of an id, a per-entry field, or a
partial modification. A row is `{id, path, label, chords}` and needs add,
remove, MODIFY and per-field validation. So this is `connections.py`'s shape
and for `connections.py`'s reason -- an owner-authored record the settings
schema cannot model -- down to the frozen dataclass that validates a
HAND-EDITED file, the `save()` after every mutation, and the path handed in by
`build_hub` rather than discovered here.

ONE DELIBERATE IMPROVEMENT ON CONNECTIONS
-----------------------------------------
A bad row is LOGGED AND SKIPPED at load rather than raising out of the
constructor. `ConnectionStore` raises, which stops the daemon booting;
`settings.py`'s own rule is that a bad preference is never worth refusing to
boot over, because a daemon that will not start leaves no microphone at all
(D-1). Losing one hand-edited row and saying so is strictly better than losing
Butler.

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
Chord SEMANTICS. `desk/actions.py` holds the one key table and the one parser,
and it is imported here rather than copied: a name must not mean one key in the
browser, another in this store, and a third on the hook. This module calls it
so a bad chord is a named 400 at the moment the owner tries to bind it, and the
hook calls it again at bind time because this file is hand-editable
(`docs/shortcuts.md` 4.3).
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from desk import actions as actions_mod

# The TERMINAL COMMAND TABLE, and it is not a Butler harness registry.
# `ports.HARNESSES` holds the adapters the voice brain dispatches through
# (`plan.md` 4.10); this is a closed set of three commands a chord or a click may
# start in a console the owner types in. A request names a KEY of this table
# and never a command, which is what keeps an unauthenticated tailnet route
# from being a remote shell (`desk/AGENTS.md`: actions are a closed set).
#
# The value is an ARGV TUPLE, not a string, and that matters (AC-150,
# `docs/shortcuts.md` 9.1). `subprocess` quotes any list element containing a
# space, so "claude --dangerously-skip-permissions" as ONE element reaches `cmd`
# as a single quoted token and cmd looks for a program with that literal name.
# `spawn_terminal` therefore SPLICES this tuple into its argv.
#
# claude carries `--dangerously-skip-permissions` by the owner's ruling of
# 2026-09-09 (D9). Said once and not argued: every claude started from this
# table can write and run commands with no confirmation. That is the posture the
# owner already set when they retired AC-29 -- a coding agent runs at full
# capability in every permission mode -- reaching a second launcher rather than
# a new decision. The flag lives HERE, on the daemon's side of the closed table,
# so no request can add, remove or forge one.
#
# INSERTION ORDER IS THE ORDER THE PAGE RENDERS (D10), the same rule
# `settings.GROUPS` carries. It is the order the owner named them in.
TERMINAL_AGENTS = {
    "claude": ("claude", "--dangerously-skip-permissions"),
    "pi": ("pi",),
    "opencode": ("opencode",),
}
DEFAULT_AGENT = "pi"

# The chord the dictation action ships bound to, and the chord the migration
# gives the row it makes from `terminal_cwd`. numpad `*` for the reason
# `docs/desk-hotkeys.md` D2 records: suppression stops a key producing its
# character system-wide, so a bare binding may only be a key almost nothing
# needs.
DEFAULT_DICTATE = "numpad_multiply"

# How wide the page's sidebar is, in CSS pixels, and the bounds a DRAG may not
# leave (the owner's second-batch item 2, 2026-09-11: "user should be able to
# increase size of sidebar so it is easier to manage the settings/ options
# there"). The default is what the stylesheet shipped with.
#
# Bounded because the number arrives from a pointer rather than from a form: 0
# hides the sidebar with no handle left to get it back, and a width past the
# window leaves no page beside it. Clamped rather than refused -- a drag that
# snapped back to nothing would read as a broken handle rather than as a limit.
#
# THE LIST'S (now the project tree's) width, and NOT the whole sidebar: the
# page draws `#sidebar` as the narrow strip PLUS this number, and since
# 2026-09-26 the drag measures the list too. It used to measure the whole
# box and write that back here, so every release grew the sidebar by the
# strip's width (e2e M2/M3). All three grew by 64 px when the project rows
# moved beside the list (2026-09-11): the 264 px default had clipped every
# row title -- "Read me the last three errors from the log" needed 286 px and
# had 143. An owner who had already dragged their own width keeps it -- this
# is the default and the floor, not a migration.
SIDEBAR_DEFAULT_W = 328
SIDEBAR_MIN_W = 264
SIDEBAR_MAX_W = 624
MIGRATED_TERMINAL_CHORD = "ctrl+numpad_multiply"

# THE CHAT LIST'S GROUPING AXIS (D1/C, `docs/triage-list.md` phase 3). A
# closed set of THREE, not a bool: "off" is today's flat day-bucketed list,
# "state" is the four state buckets, "project" is the rail's own filter
# offered a second way (the owner's ruling, over this spec's own
# recommendation of state-only -- the cost is that it duplicates the rail).
CHAT_GROUPINGS = ("off", "state", "project")
#: What the desk's right-hand column may show, ONE at a time
#: (`docs/automations.md` AU-10, AC-466). "" is shut.
RIGHT_PANELS = ("", "files", "library")


def _clamp_width(value) -> int:
    """A stored sidebar width, or the default for anything unusable.

    Anything that is not a number at all -- a hand-edited file, a page sending
    a string -- takes the DEFAULT rather than a bound, because "unreadable" and
    "dragged to the edge" are different facts and only the second one is the
    owner expressing a preference.
    """
    try:
        width = int(float(value))
    except (TypeError, ValueError):
        return SIDEBAR_DEFAULT_W
    return max(SIDEBAR_MIN_W, min(SIDEBAR_MAX_W, width))


def whole_disk(path: str) -> str | None:
    """Why `path` may not be a project, or None (docs/project-roots.md PJ-2).

    A row is the file explorer's root, and the explorer reads and writes
    everything inside it: a drive root or the home folder itself would open
    the whole disk. A bare drive (`C:`) counts as its root."""
    raw = path.strip()
    if len(raw) == 2 and raw[1] == ":" and raw[0].isalpha():
        raw += "\\"
    typed = Path(raw)
    where = typed.resolve()
    # The typed form too: resolving an unreachable share loses its root.
    if typed.parent == typed or where.parent == where:
        return f"{path} is a whole drive; choose a folder inside it"
    if where == Path.home().resolve():
        return (f"{path} is your whole home folder; choose a folder "
                f"inside it")
    return None


def not_a_folder(path: str) -> str | None:
    """Why `path` cannot be added as a project here, or None (PJ-4, AC-754).

    Absolute first: a relative path would be read against the daemon's own
    working directory, and on macOS a Windows spelling like
    `\\Volumes\\share\\X` is relative, because a backslash is a plain
    character there."""
    where = Path(path.strip())
    if not where.is_absolute():
        return f"{path} is not a full path on this machine"
    if not where.is_dir():
        return f"{path} is not a folder this machine can open"
    return None


def same_dir(path: str) -> str:
    r"""One directory's identity, for comparing two SPELLINGS of it.

    Separators normalised, trailing separators stripped, case folded, IN THAT
    ORDER, because this is Windows: `E:\X`, `E:\X\`, `e:\x` and `E:/X` are one
    folder and four strings. Used only to compare two strings -- never to
    resolve, open or match anything on disk, so it does not touch the
    filesystem and must not, since the migration runs at boot before the owner
    can fix a bad path.

    **THE SEPARATOR HALF ARRIVED LATE AND THE ORDER IS WHY IT HAD TO**
    (review, 2026-09-11). Without it there were THREE equality rules for one
    directory and the strictest was the spawn gate: a row spelled
    `E:/PROJECTS/butler` -- which `POST /api/shortcuts` accepts, and which is
    how this repository's own documents write paths -- matched NO conversation
    at all, while `E:\PROJECTS\butler\` and `e:\projects\butler` were filed
    correctly and still refused a child. Normalising AFTER the rstrip would
    leave `E:/X/` unequal to `E:\X`, so the two steps are not interchangeable.

    It is PUBLIC because it has a caller outside this module:
    `butlerd.harness_spec_resolver`'s membership test is the spawn gate, and a
    gate with its own comparison rule is how "the listing files it here" and
    "a child may start here" came to disagree about one directory.
    """
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _migratable_paths(value) -> list[str]:
    """The directories in a retired `projects` value, and nothing else.

    This reads a HAND-EDITABLE file that no longer has a schema behind it, so
    it validates rather than coerces. Measured before it did [2026-09-11]:
    `str(p)` over anything turned `None`, `42` and `{"a": 1}` into rows whose
    paths were the words "None", "42" and "{'a': 1}", and a `projects` that was
    a plain STRING iterated its CHARACTERS -- `"E:/P/one"` became seven rows
    called `E`, `:`, `/`, `P`, `o`, `n`, `e`. All permanent, because the
    once-flag is set in the same call.

    A non-list, or an entry that is not a non-blank string, is DROPPED with a
    loud line. `settings.py`'s rule: a bad preference is never worth refusing
    to boot over, and losing one entry while saying so beats inventing a row.
    """
    if not isinstance(value, list):
        if value:
            print(f"[butler] the retired projects setting is a "
                  f"{type(value).__name__} and not a list; nothing migrated "
                  f"from it", flush=True)
        return []
    out = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif entry:
            print(f"[butler] ignoring {entry!r} in the retired projects "
                  f"setting: a project is a directory path", flush=True)
    return out


class ShortcutError(RuntimeError):
    """A shortcut could not be used, and the message says which and why.

    Deliberately loud, and mapped to a 400 by the routes. The alternative --
    storing something the desk agent will later refuse -- means the owner is
    told their binding saved and then finds it does nothing, which is AC-7b on
    a control whose whole purpose is to take over a key.
    """


@dataclass(frozen=True)
class Shortcut:
    """One project row: where, what it is called, and a chord per agent.

    `chords` maps an agent name from `TERMINAL_AGENTS` to a canonical chord
    string, or to `""` for "this button has no chord". An absent agent and an
    empty chord mean the same thing and both are normal.
    """

    id: str
    path: str
    label: str = ""
    chords: dict = field(default_factory=dict)

    def __post_init__(self):
        """Validate the SHAPE here so a hand-edited file fails like a bad
        request does.

        `state/shortcuts.json` is a file the owner can edit, and a `chords`
        that is not an object used to blow up later inside `public()` -- which
        runs over EVERY row, so one malformed line hides all the good ones.
        That is the recorded defect `connections.py:66-78` exists for, met
        again here.

        Only the shape. Whether a chord names a real key is
        `desk/actions.py`'s question and is asked where the answer can be a
        sentence the owner reads.
        """
        if not isinstance(self.id, str) or not self.id.strip():
            raise ShortcutError(
                f"a shortcut needs a non-empty id; got {self.id!r}")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ShortcutError(
                f"shortcut {self.id!r} has no directory, so nothing could be "
                f"opened for it")
        if not isinstance(self.label, str):
            raise ShortcutError(
                f"shortcut {self.id!r} has a non-text label {self.label!r}")
        if not isinstance(self.chords, dict):
            raise ShortcutError(
                f"shortcut {self.id!r} has a non-object chords "
                f"{self.chords!r}; it maps an agent name to a chord, such as "
                f"{{'pi': 'ctrl+numpad_multiply'}}")
        for agent, chord in self.chords.items():
            if agent not in TERMINAL_AGENTS:
                raise ShortcutError(
                    f"shortcut {self.id!r} names the agent {agent!r}, which "
                    f"this daemon cannot start; known: "
                    f"{list(TERMINAL_AGENTS)}")
            if not isinstance(chord, str):
                raise ShortcutError(
                    f"shortcut {self.id!r} has a non-text chord for "
                    f"{agent!r}: {chord!r}")

    def public(self) -> dict:
        """What crosses the wire. Everything, because nothing here is secret.

        It exists anyway, for `Connection.public()`'s reason: serialising the
        dataclass wholesale is safe today and starts leaking the day somebody
        adds a resolved field to it.
        """
        return {"id": self.id, "path": self.path, "label": self.label,
                "chords": {a: c for a, c in sorted(self.chords.items()) if c}}


def new_id() -> str:
    """An id the owner never has to type or see.

    Time-ordered so the file reads in the order rows were added, with random
    tail so two rows created in one millisecond cannot collide.
    """
    return f"s-{int(time.time() * 1000)}-{secrets.token_hex(2)}"


class ShortcutStore:
    """The rows, the dictation chord, and the page's own view state.

    VIEW STATE LIVES HERE and not in the settings schema
    (`docs/shortcuts.md` D1). It is a few booleans in the same answer the page
    already fetches, and keeping them here is what lets `web/` name no setting
    at all -- AC-41 is enforced by review rather than by any grep over that
    directory, so the cheap way to keep it true is to leave the page nothing to
    name.

    **`open` IS RETIRED AND THE KEY IS KEPT** (2026-09-11). It was the
    projects PANE's open-or-shut, and the pane became the rail: a rail does not
    fold and a popover is a gesture, so nothing writes it any more. The field,
    the property, `set_open` and the route's branch all stay, because this file
    is on the owner's disk with `"open": true` already in it and a key the
    loader stops reading is a key the next `save()` silently drops. Removing it
    is a decision about an existing `shortcuts.json`, not a tidy-up.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._rows: dict[str, Shortcut] = {}
        self._open = False
        # Whether the pane shows its chord slots (`docs/shortcuts.md` D8,
        # option 1). Here beside `open` for `open`'s reason and at `open`'s cost
        # -- one boolean in a file the page already fetches -- and OFF by
        # default, because the owner's stated usage is that most rows carry no
        # chord at all and "unbound" printed three times on every row is noise
        # they cannot act on.
        self._show_chords = False
        # Whether the page's SIDEBAR is collapsed (AC-166,
        # `docs/familiar-ui.md` 6.3). It is here rather than in a setting for
        # the reason `open` and `show_chords` are: AC-8 bans browser storage
        # outright, so the only place view state can live and still survive a
        # reload is the daemon -- and this store already carries the two other
        # pieces of view state the page has. It is deliberately NOT a setting,
        # so `web/` still names none (AC-41).
        self._sidebar_collapsed = False
        self._sidebar_width = SIDEBAR_DEFAULT_W
        # Whether the page's PROJECT RAIL is collapsed to icons (u-railB1,
        # item 3, 2026-09-16). `sidebar_collapsed`'s twin and for its own
        # reason: this hides the LIST and leaves the rail, and never the other
        # way round -- there is no flag anywhere that hides the rail itself,
        # because a filter you cannot see is a filter you forget is on.
        self._rail_collapsed = False
        # HOW THE CHAT LIST IS GROUPED (D1/C, `docs/triage-list.md` phase 3,
        # owner's ruling 2026-09-16: BOTH axes over this spec's own
        # recommendation of state-only). A CLOSED three-value choice --
        # "off", "state", "project" -- and view state rather than a setting
        # for `rail_collapsed`'s own reason: AC-8 bans browser storage, so
        # two devices looking at the same list must agree about how it is
        # arranged, and this store already carries every other fact like it.
        self._chat_grouping = "off"
        # WHICH PROJECTS THE SIDEBAR TREE SHOWS OPEN (`docs/filing-and-
        # pacing.md` 8, D-D2, AC-291): row ids, or None for "never written",
        # which `public()` answers as the root alone so a fresh install opens
        # on Butler's own chats. View state here for `sidebar_collapsed`'s
        # reason -- AC-8 bans browser storage, and a phone and a desk looking
        # at one tree must agree about it (D-2). `rail_collapsed` and
        # `chat_grouping` above are RETIRED with the tree (2026-09-26): the
        # page no longer reads or writes either, and they stay as inert keys
        # for `open`'s recorded reason -- a key the loader stops reading is a
        # key the next `save()` silently drops from the owner's file.
        self._expanded: list[str] | None = None
        # WHICH ONE PANEL THE DESK'S RIGHT-HAND COLUMN SHOWS: "", "files" or
        # "library" (`docs/automations.md` AU-10, AC-466). It REPLACED the
        # bool `files_open` (`docs/file-explorer.md` 3.3, AC-312), migrated
        # once in `load`: two bools would make "both open" a representable
        # state nobody can draw at 300 px. View state here for
        # `sidebar_collapsed`'s reason, shut by default. A phone never writes
        # it (D-2).
        self._right_panel = ""
        self._dictate = DEFAULT_DICTATE
        self._migrated = False
        # A SECOND flag and not a reuse of the one above. The two migrations run
        # at different times on an existing install -- `terminal_cwd` shipped
        # 2026-09-09 and this one 2026-09-11 -- so one flag would make whichever
        # ran second a silent no-op on every daemon that had already booted.
        self._migrated_projects = False
        # BUTLER'S OWN DIRECTORY, for identifying the ROOT row (R10,
        # `docs/filing-and-pacing.md` D3: it cannot be deleted or renamed).
        # NOT persisted -- `state/shortcuts.json` stores rows, not which one is
        # root -- and set by `migrate_projects` on EVERY boot that calls it,
        # never only the boot that actually migrates something, because a row
        # matching it may already exist from a PRIOR boot's migration. Empty
        # until then, which is a safe default: nothing compares equal to "" by
        # `same_dir`, so `is_root` answers False for every row rather than
        # guessing.
        self._app_dir = ""
        self.load()

    # -- persistence -------------------------------------------------------- #
    def load(self) -> None:
        self._rows = {}
        self._open = False
        self._show_chords = False
        self._sidebar_collapsed = False
        self._sidebar_width = SIDEBAR_DEFAULT_W
        self._rail_collapsed = False
        self._chat_grouping = "off"
        self._expanded = None
        self._right_panel = ""
        self._dictate = DEFAULT_DICTATE
        self._migrated = False
        self._migrated_projects = False
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # Loud and NOT fatal, `settings.py`'s rule: a shortcut file nobody
            # can parse must not cost the owner their microphone (D-1).
            print(f"[butler] shortcuts file {self.path} unreadable ({e}); "
                  f"starting with none", flush=True)
            return
        if not isinstance(raw, dict):
            print(f"[butler] shortcuts file {self.path} is not an object "
                  f"({type(raw).__name__}); starting with none", flush=True)
            return
        self._open = bool(raw.get("open"))
        self._show_chords = bool(raw.get("show_chords"))
        self._sidebar_collapsed = bool(raw.get("sidebar_collapsed"))
        self._sidebar_width = _clamp_width(raw.get("sidebar_width"))
        self._rail_collapsed = bool(raw.get("rail_collapsed"))
        # A hand-edited value outside the closed set is treated the way an
        # unreadable width is (`_clamp_width`'s own rule): "off" rather than
        # a raise, because a bad row here costs one preference and not the
        # boot (D-1's argument, one file over).
        grouping = raw.get("chat_grouping")
        self._chat_grouping = grouping if grouping in CHAT_GROUPINGS else "off"
        # A hand-edited value that is not a list of ids reads as "never
        # written", `chat_grouping`'s rule: one preference, never the boot.
        expanded = raw.get("expanded_projects")
        self._expanded = ([e for e in expanded if isinstance(e, str)]
                          if isinstance(expanded, list) else None)
        # ONE-TIME MIGRATION (AC-466): a file written before `right_panel`
        # existed carries `files_open`, and `true` means the files panel. A
        # file that has `right_panel` is past it -- `save` never writes the
        # old key again -- so the migration cannot overrule a later choice.
        # A hand-edited value off the closed set reads as shut, `chat_
        # grouping`'s rule: one preference, never the boot.
        if "right_panel" in raw:
            panel = raw.get("right_panel")
            self._right_panel = panel if panel in RIGHT_PANELS else ""
        else:
            self._right_panel = "files" if raw.get("files_open") is True \
                else ""
        self._migrated = bool(raw.get("migrated"))
        self._migrated_projects = bool(raw.get("migrated_projects"))
        chord = raw.get("dictate")
        if isinstance(chord, str) and chord.strip():
            self._dictate = chord.strip()
        for sid, entry in (raw.get("rows") or {}).items():
            entry = entry or {}
            try:
                self._rows[sid] = Shortcut(
                    id=sid, path=entry.get("path") or "",
                    label=entry.get("label") or "",
                    chords=dict(entry.get("chords") or {}))
            except (ShortcutError, TypeError, ValueError) as e:
                # SKIPPED, not raised. See the module docstring: connections
                # takes the whole daemon down here and this must not.
                print(f"[butler] ignoring bad shortcut {sid!r} in "
                      f"{self.path.name}: {e}", flush=True)
                continue
            # PJ-3 (docs/project-roots.md): kept and usable, named once.
            why = whole_disk(self._rows[sid].path)
            if why:
                print(f"[butler] project {sid!r}: {why} (it still works; "
                      f"move it on the settings page)", flush=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "open": self._open,
            "show_chords": self._show_chords,
            "sidebar_collapsed": self._sidebar_collapsed,
            "sidebar_width": self._sidebar_width,
            "rail_collapsed": self._rail_collapsed,
            "chat_grouping": self._chat_grouping,
            "expanded_projects": self._expanded,
            "right_panel": self._right_panel,
            "migrated": self._migrated,
            "migrated_projects": self._migrated_projects,
            "dictate": self._dictate,
            "rows": {r.id: {"path": r.path, "label": r.label,
                            "chords": dict(r.chords)}
                     for r in self._rows.values()},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- reading ------------------------------------------------------------ #
    @property
    def open(self) -> bool:
        return self._open

    @property
    def show_chords(self) -> bool:
        return self._show_chords

    @property
    def sidebar_collapsed(self) -> bool:
        return self._sidebar_collapsed

    @property
    def rail_collapsed(self) -> bool:
        return self._rail_collapsed

    @property
    def chat_grouping(self) -> str:
        return self._chat_grouping

    @property
    def sidebar_width(self) -> int:
        return self._sidebar_width

    @property
    def dictate(self) -> str:
        return self._dictate

    def all(self) -> list[Shortcut]:
        return list(self._rows.values())

    def paths(self) -> tuple[str, ...]:
        """THE project list: every row's directory, in the order the pane draws.

        The owner's ruling of 2026-09-11, and it makes one sentence in
        `docs/shortcuts.md` D4 true for the first time -- "the rows the owner
        types in the pane are exactly the list they would be checked against,
        and a second list to keep in step is the drift `connections.py` was
        written to avoid." That drift had happened: measured against the owner's
        own files, the `projects` SETTING held one directory that was not a row
        and all three rows were unreachable by voice, so clicking claude on a
        project worked while asking for it out loud was refused.

        Read by both of AC-31's gates -- `hub._on_final`'s matcher and
        `butlerd.harness_spec_resolver`'s re-check -- so a directory is
        reachable by voice exactly when it is a row. The cost is stated rather
        than buried: the spoken switch now reaches every row the owner ever
        added for a terminal, which is a WIDER surface than the old setting and
        is the owner's to accept.
        """
        return tuple(r.path for r in self._rows.values())

    def row_for(self, path: str) -> Shortcut | None:
        r"""The row a DIRECTORY belongs to, compared as directories.

        This is what makes a conversation's workspace a DERIVED fact rather
        than a second stored one (AC-218, AC-229). A chat record keeps the
        absolute path `work_in` wrote and nothing else; WHICH pane row that is
        is answered here, on demand, by `same_dir` -- so `E:\X`, `E:\X\` and
        `e:\x` are one folder and three strings. Editing a row's path to add a
        trailing separator or to change its case therefore leaves every
        conversation still filed under it, because nothing was ever keyed on
        the spelling. There is no migration to run and nothing to keep in step,
        which is the whole reason it is derived: two stored copies of one fact
        is the drift the two project lists already cost this project once.

        WHAT IT DOES NOT COVER, stated rather than left to be discovered:
        moving a project to a genuinely DIFFERENT directory. The old
        conversations keep the old path and stop matching any row, which is the
        truthful answer -- the work happened there, and nothing here can know
        whether the new directory is the same repository or another one.

        Two rows may name one directory. `match_project` refuses that name
        ALOUD rather than guessing (AC-31); this answers the FIRST in pane
        order instead, because a listing has no voice to refuse with and both
        rows point at the same folder anyway.
        """
        key = same_dir(path or "")
        if not key:
            return None
        for row in self._rows.values():
            if same_dir(row.path) == key:
                return row
        return None

    def get(self, sid: str) -> Shortcut:
        row = self._rows.get(sid)
        if row is None:
            raise KeyError(
                f"no shortcut with id {sid!r}; known: {sorted(self._rows)}")
        return row

    def is_root(self, row: Shortcut) -> bool:
        """Is ROW Butler's own root project (R10, `docs/filing-and-pacing.md`
        D3: undeletable, unrenameable)?

        Compared as a DIRECTORY (`same_dir`), the ONE predicate every piece of
        root-protection uses -- never the row's id or its label, both of which
        the owner may change freely. `_app_dir` is set by `migrate_projects`
        on every boot that calls it, so this answers correctly whether or not
        THIS boot actually minted the row.
        """
        return bool(self._app_dir) and (
            same_dir(row.path) == same_dir(self._app_dir))

    def root_id(self) -> str:
        """The root row's id, or "" when this store does not know one yet
        (no `migrate_projects` has run, or its row was never matched).

        A KEY the page can compare against, so filing a conversation born with
        no scope (`docs/filing-and-pacing.md` R4, "All chats" -> Butler) never
        has to know app_dir's path or guess which row is home (AC-41).
        """
        for row in self._rows.values():
            if self.is_root(row):
                return row.id
        return ""

    def _ordered_rows(self) -> list[Shortcut]:
        """Rows in DISPLAY order: the root first, then every other row in the
        order they were added (owner's ruling, 2026-09-16: "butler project
        should also be at top of the list, as the local").

        Display-time only. `_rows`, `paths()`, `bindings()` and the file on
        disk are UNCHANGED -- `migrate_projects`'s own "the root LAST" append
        order is about DEDUPE, not about where it is drawn, and reordering the
        stored rows would be a bigger change than the owner asked for and puts
        the chord table at risk for no reason.
        """
        return sorted(self._rows.values(), key=lambda r: not self.is_root(r))

    def public(self) -> dict:
        """Everything the page needs, so the page names nothing of its own.

        `agents` and `keys` ride along because otherwise `web/` would have to
        write down the three program names and the key table -- and a page that
        writes down a closed daemon table is naming one (AC-41). It costs a
        handful of bytes on a fetch that happens once per page load.

        `rows` is in DISPLAY order (`_ordered_rows`), root first; `root_id`
        rides beside it so a caller that needs the IDENTITY rather than the
        position -- filing a conversation born with no project scope -- does
        not have to assume position 0 is root forever.
        """
        return {
            "open": self._open,
            "show_chords": self._show_chords,
            "sidebar_collapsed": self._sidebar_collapsed,
            "sidebar_width": self._sidebar_width,
            "rail_collapsed": self._rail_collapsed,
            "chat_grouping": self._chat_grouping,
            "expanded_projects": self.expanded_projects(),
            "right_panel": self._right_panel,
            "dictate": self._dictate,
            "root_id": self.root_id(),
            "rows": [r.public() for r in self._ordered_rows()],
            "agents": list(TERMINAL_AGENTS),
            "keys": sorted(actions_mod.KEYS),
            "bare_keys": list(actions_mod.BARE_SAFE),
        }

    def bindings(self) -> list[dict]:
        """The binding table as the desk agent reads it (`docs/shortcuts.md` 4.2).

        The dictation chord first, then one terminal binding per non-empty
        chord on every row. Every chord in here was canonicalised on the way
        in, so this is a projection rather than a parse -- and the desk agent
        parses it again anyway, because this file is hand-editable and the hook
        is the last gate before a key is suppressed system-wide.
        """
        out = [{"chord": self._dictate, "action": "dictate"}]
        for row in self._rows.values():
            for agent, chord in sorted(row.chords.items()):
                if not chord:
                    continue
                out.append({"chord": chord, "action": "terminal",
                            "cwd": row.path, "agent": agent,
                            "label": row.label or row.path})
        return out

    # -- writing ------------------------------------------------------------ #
    def set_open(self, is_open: bool) -> None:
        """RETIRED with the projects pane; see the class docstring. No caller
        in `web/` remains, and the route's branch is kept so an existing
        `shortcuts.json` round-trips unchanged."""
        self._open = bool(is_open)
        self.save()

    def set_show_chords(self, showing: bool) -> None:
        """Reveal or hide the chord slots (D8).

        A VIEW state and nothing more: it changes no binding, so a device that
        turns it on does not alter what any key does. It is stored rather than
        held in the page for `open`'s reason -- the owner asked for a pane that
        stays how they left it, and a per-origin copy is how one laptop ended up
        with four disagreeing copies of a voice setting (D-2, AC-8).
        """
        self._show_chords = bool(showing)
        self.save()

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Remember whether the page's sidebar is collapsed (AC-166).

        `set_show_chords`'s twin, and it is here for exactly that method's
        reason: it changes no binding and starts nothing, but the owner asked
        for a page that stays how they left it, and a per-origin copy is how
        one laptop ended up with four disagreeing copies of a preference
        (D-2, AC-8).
        """
        self._sidebar_collapsed = bool(collapsed)
        self.save()

    def set_right_panel(self, panel) -> None:
        """Remember which panel the desk's right-hand column shows
        (`docs/automations.md` AU-10, AC-466): one of `RIGHT_PANELS`. Anything
        else is REFUSED by name rather than coerced -- a caller that sent
        `true` would otherwise be told a panel opened that it never named.
        """
        if not isinstance(panel, str) or panel not in RIGHT_PANELS:
            raise ShortcutError(
                f"right_panel is one of {list(RIGHT_PANELS)}, got {panel!r}")
        self._right_panel = panel
        self.save()

    def expanded_projects(self) -> list[str]:
        """The row ids the tree shows OPEN, in the order they were written.

        Never written, the root alone (D-D2). An id no row has is dropped
        here rather than served, so a removed row's open state is never
        inherited by anything and the page is never handed an id it cannot
        draw.
        """
        if self._expanded is None:
            root = self.root_id()
            return [root] if root else []
        return [sid for sid in self._expanded if sid in self._rows]

    def set_expanded_projects(self, ids) -> None:
        """Remember which project nodes the tree shows open (AC-291).

        A list of row-id STRINGS and nothing else, refused by name otherwise:
        a caller that sent a map or a bare id believes it was applied.
        """
        if not isinstance(ids, list) or not all(
                isinstance(sid, str) for sid in ids):
            raise ShortcutError(
                f"expanded_projects is a list of project row ids, got "
                f"{ids!r}")
        self._expanded = list(dict.fromkeys(ids))
        self.save()

    def set_rail_collapsed(self, collapsed: bool) -> None:
        """Remember whether the page's project rail is collapsed to icons.

        `set_sidebar_collapsed`'s twin and here for exactly its reason: this
        changes no binding and starts nothing, but the owner asked for a rail
        that stays how they left it, and a per-device copy is how one laptop
        ended up with four disagreeing copies of a preference (D-2, AC-8).
        There is no lower state than "icons" -- the rail itself never hides.
        """
        self._rail_collapsed = bool(collapsed)
        self.save()

    def set_chat_grouping(self, value) -> str:
        """Change how the chat list is grouped (D1/C).

        Refuses anything off the closed three-value set -- `set_sidebar_
        width`'s own rule for a bad type: a caller sending something this
        page never offers is a malformed request, not a preference, and the
        stored choice is left exactly as it was rather than silently reset.
        """
        if value not in CHAT_GROUPINGS:
            raise ShortcutError(
                f"chat_grouping must be one of {CHAT_GROUPINGS}; got "
                f"{value!r}")
        self._chat_grouping = value
        self.save()
        return self._chat_grouping

    def set_sidebar_width(self, width) -> int:
        """Remember how wide the page's sidebar is, CLAMPED (item 2).

        `set_sidebar_collapsed`'s twin and here for its reason. The clamp is
        the difference between them: `collapsed` is a boolean a checkbox
        produces and this is a number a DRAG produces, so it has to survive a
        pointer that went to the edge of the screen or off it. Answers what was
        actually stored, so the page can correct itself from the reply rather
        than keeping a copy of the bounds (AC-41).
        """
        try:
            wanted = int(float(width))
        except (TypeError, ValueError):
            # LOUD, and the width is left alone. A non-number is a malformed
            # request rather than a preference, and quietly resetting to the
            # default would throw away a width the owner chose because a caller
            # sent the wrong type (the house rule: refusals are sentences the
            # owner reads).
            raise ShortcutError(
                f"a sidebar width is a number of pixels; got {width!r}")
        self._sidebar_width = max(SIDEBAR_MIN_W, min(SIDEBAR_MAX_W, wanted))
        self.save()
        return self._sidebar_width

    def set_dictate(self, chord) -> str:
        """Rebind dictation. Refuses a chord no key table knows (AC-137).

        A blank is refused rather than stored: dictation with no chord is the
        feature switched off by a control that does not say so, and the honest
        way to switch it off is `hotkeys_enabled`.
        """
        canonical = self._chord(chord, "the dictation chord")
        if not canonical:
            raise ShortcutError(
                "the dictation chord cannot be empty; turn hotkeys off "
                "instead of unbinding it")
        self._collide(canonical, owner="dictate")
        self._dictate = canonical
        self.save()
        return canonical

    def upsert(self, sid: str, path: str, label: str = "",
               chords: dict | None = None,
               must_exist: bool = False) -> Shortcut:
        """Add a row, or MODIFY the one with this id.

        Upsert rather than add-only, because a list that only grows is a trap
        whose only exit is hand-editing a file the owner was never told about
        -- `connections.py`'s recorded reason for having a remove at all, one
        step further (AC-134).

        Every chord is canonicalised and checked against every other chord in
        the store HERE, so the refusal is a sentence in the pane rather than a
        line in `butler.log` two seconds later.

        **THE ROOT ROW'S DIRECTORY CANNOT BE EDITED AWAY** (R10,
        `docs/filing-and-pacing.md` D3). The label is untouched by this rule --
        renaming what the pane CALLS the root is harmless -- but its PATH is
        the one thing every other piece of root-protection identifies it by
        (`is_root`), so letting it drift would make "this is the root" stop
        being true of the row the owner is looking at with no warning.

        **NOR CAN A SECOND ROW BE CREATED AT THE ROOT'S DIRECTORY** (review,
        2026-09-16). `is_root` attaches to a DIRECTORY, not to a row's
        identity, so a brand-new row -- an `sid` this store has never seen --
        whose path IS the app directory (or a `same_dir` respelling of it)
        would satisfy `is_root()` exactly as the real root row does the
        moment it exists, and `remove()`'s guard, which asks only
        `is_root()`, would then refuse to ever delete it: a second,
        permanently undeletable row with no route out except hand-editing
        `state/shortcuts.json`. Refusing the CREATE (and an edit that would
        move an ORDINARY row onto the root's directory) is the cheaper
        dedupe rule `migrate_projects` already applies, applied here at the
        write surface too -- but `migrate_projects` itself does not call
        `upsert()` at all (it writes `_rows` directly and dedupes on its own
        `same_dir` set before minting), so this guard cannot make that
        migration refuse its own root row.

        `must_exist` is the route's (PJ-4, AC-754): the owner's path must be
        an existing folder on this machine. Off for internal callers, which
        may hold a row whose folder has gone.
        """
        sid = str(sid or "").strip() or new_id()
        path = str(path or "").strip()
        existing = self._rows.get(sid)
        # PJ-2: on create or move only; a row already on disk keeps its
        # path (PJ-3, named at load).
        if path and (existing is None or same_dir(path)
                     != same_dir(existing.path)):
            why = (not_a_folder(path) if must_exist else None) \
                or whole_disk(path)
            if why:
                raise ShortcutError(why)
        if (existing is not None and self.is_root(existing)
                and same_dir(path) != same_dir(existing.path)):
            raise ShortcutError(
                f"{existing.label or existing.path} is Butler's own root "
                f"project and its directory cannot be changed")
        if (self._app_dir and same_dir(path) == same_dir(self._app_dir)
                and not (existing is not None and self.is_root(existing))):
            raise ShortcutError(
                f"{path} is Butler's own root project's directory; it "
                f"already has a row and a second one cannot be created")
        cleaned: dict[str, str] = {}
        for agent, chord in (chords or {}).items():
            if agent not in TERMINAL_AGENTS:
                raise ShortcutError(
                    f"{agent!r} is not a coding agent this daemon can start; "
                    f"known: {list(TERMINAL_AGENTS)}")
            canonical = self._chord(chord, f"the {agent} chord")
            if canonical:
                self._collide(canonical, owner=sid)
                cleaned[agent] = canonical
        row = Shortcut(id=sid, path=path, label=str(label or "").strip(),
                       chords=cleaned)
        self._rows[sid] = row
        self.save()
        return row

    def remove(self, sid: str) -> None:
        """Drop a row, unless it is the ROOT (R10).

        Butler's own project is "ALWAYS existing/available" in the owner's own
        words, and `docs/filing-and-pacing.md` R5 made it the mandatory
        fallback for every chat born with no project -- so a caller that could
        remove it would leave a future birth with nowhere to land.
        """
        if sid not in self._rows:
            raise KeyError(f"no shortcut with id {sid!r}")
        row = self._rows[sid]
        if self.is_root(row):
            raise ShortcutError(
                f"{row.label or row.path} is Butler's own root project and "
                f"cannot be removed")
        del self._rows[sid]
        self.save()

    # -- chords ------------------------------------------------------------- #
    def _chord(self, value, what: str) -> str:
        try:
            return actions_mod.canonical_chord(value)
        except actions_mod.BindingError as e:
            # TRANSLATED at the boundary so the routes have ONE exception to
            # map to a 400, and never swallowed: what `BindingError` says is
            # exactly what the owner needs to read.
            raise ShortcutError(f"{what} was refused: {e}") from e

    def _collide(self, chord: str, owner: str) -> None:
        """Refuse a chord already bound to something else.

        `Table.__init__` refuses a duplicate too, and that is the load-bearing
        gate. This one exists so the owner finds out at the click rather than
        having their whole table refused on the next poll -- and the refusal
        names what already holds the chord, which the hook cannot do.
        """
        if owner != "dictate" and chord == self._dictate:
            raise ShortcutError(
                f"{chord} already dictates; one chord does one thing")
        for row in self._rows.values():
            if row.id == owner:
                continue
            for agent, existing in row.chords.items():
                if existing == chord:
                    raise ShortcutError(
                        f"{chord} already opens {agent} in "
                        f"{row.label or row.path}; one chord does one thing")

    # -- the one-time migration --------------------------------------------- #
    def migrate_terminal_cwd(self, settings_path: Path) -> str:
        """Turn a `terminal_cwd` setting into a row, ONCE (AC-144).

        Read from the settings FILE rather than from the `Settings` store,
        because the setting is being retired from the schema in the same commit
        and `Settings._load` drops a key it no longer knows. This is the whole
        reason the migration is not simply `settings.get("terminal_cwd")`.

        The `migrated` flag is what makes it once. Without it, deleting every
        row would bring the old directory back on the next restart -- a row the
        owner explicitly removed, reappearing, which is worse than never having
        migrated at all.

        Answers the path it migrated, or "" if it did nothing.
        """
        if self._migrated:
            return ""
        # Marked done even when there is nothing to migrate, so an owner who
        # sets `terminal_cwd` in a hand-edited file after this daemon has
        # booted once does not get a surprise row later.
        self._migrated = True
        cwd = ""
        try:
            raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cwd = str(raw.get("terminal_cwd") or "").strip()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            # A fresh clone has no settings file at all, which is the common
            # case and not a failure.
            cwd = ""
        if not cwd or self._rows:
            self.save()
            return ""
        sid = new_id()
        self._rows[sid] = Shortcut(
            id=sid, path=cwd, label=Path(cwd).name or cwd,
            chords={DEFAULT_AGENT: MIGRATED_TERMINAL_CHORD})
        self.save()
        return cwd

    def migrate_projects(self, settings_path: Path, app_dir: Path) -> list[str]:
        """Turn the retired `projects` setting into rows, and mint the ROOT row.

        The owner's backlog item 4: the rows become the one project table, so
        every directory that was reachable by voice before this change must
        still be, or the change takes a capability away while claiming to add
        one. `migrate_terminal_cwd`'s shape exactly -- read the settings FILE
        rather than the store, because the key is retired from the schema in the
        same commit and `Settings._load` drops one it no longer knows.

        `app_dir` is the DATA ROOT (`daemon/paths.py`: Butler's own repository
        on the legacy layout, the owner's home folder once migrated,
        `docs/data-root.md`), and it becomes an ordinary row with
        an id rather than a special case in the matcher: everything that works
        on a row -- the three agent buttons, a chord, the spoken switch, the
        spawn gate -- then works on it with no branch anywhere.

        ONCE, on its own flag. `migrate_terminal_cwd`'s `migrated` is not
        reused: the two run at different times on an existing install, and one
        flag would make whichever ran second a no-op. Without the flag, deleting
        a row brings the directory back on the next restart, and a row the owner
        explicitly removed reappearing is worse than never having migrated.

        Answers the paths it added, newest last, or `[]` if it did nothing.

        **SETS `_app_dir` UNCONDITIONALLY, EVEN WHEN ALREADY MIGRATED** (R10,
        `docs/filing-and-pacing.md` D3). `is_root` needs to answer correctly
        on every boot, including the SECOND and later ones where a row
        matching `app_dir` already exists from a prior run and this method
        returns early -- so the identity has to be recorded before the early
        return, not only on the boot that actually mints the row.
        """
        self._app_dir = str(app_dir)
        if self._migrated_projects:
            # THE ROOT ROW IS ENSURED ON EVERY BOOT (`docs/data-root.md`,
            # AC-415). The once-flag protects rows the owner REMOVED from
            # coming back; the root cannot be removed, so the only way it is
            # missing here is that the data root MOVED -- and a root with no
            # row leaves `root_id` blank and every root birth homeless (R5).
            if any(same_dir(p) == same_dir(app_dir) for p in self.paths()):
                return []
            sid = new_id()
            self._rows[sid] = Shortcut(id=sid, path=str(app_dir),
                                       label=Path(app_dir).name or str(app_dir))
            self.save()
            return [str(app_dir)]
        self._migrated_projects = True
        wanted: list[str] = []
        try:
            raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                wanted = _migratable_paths(raw.get("projects"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            # A fresh clone has no settings file, which is the common case and
            # not a failure.
            wanted = []
        # The root LAST, so an owner who already listed it keeps their own row
        # and its chords rather than gaining a duplicate underneath.
        wanted.append(str(app_dir))
        # Deduped on the NORMALIZED directory, not on the raw string. The
        # owner's own file carries a trailing backslash on one row, and
        # `E:\X\` beside `E:\X` used to make TWO rows for one directory --
        # which `match_project` then reports as ambiguous and refuses out loud
        # forever, so the migration could permanently break the spoken switch
        # for the root project it had just added. Found by review 2026-09-11.
        have = {same_dir(p) for p in self.paths()}
        made = []
        for path in wanted:
            key = same_dir(path)
            if key in have:
                continue
            have.add(key)
            sid = new_id()
            # No chord: a chord is the owner's to choose by pressing keys
            # (`docs/shortcuts.md` 9), and a migration that bound one would be
            # taking a key on their machine without being asked.
            self._rows[sid] = Shortcut(id=sid, path=path,
                                       label=Path(path).name or path)
            made.append(path)
        self.save()
        return made
