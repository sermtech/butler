"""Where Butler's CODE, its RECORDS and the ROOT PROJECT'S HOME live.

`docs/data-root.md`, AC-410..AC-416. The owner's ruling of 2026-09-26, "two
folders": the repository is code only; the DATA ROOT (for this owner
`E:\\Butler-Data`, set by `start.bat`, never written here) is the root
project's working directory, holding the owner's files and `attachments\\`,
with Butler's own records in ONE hidden `.butler\\` inside it.

THIS IS THE ONE PLACE a record path is spelled. A new record directory is
added here as a field, never as a `REPO / "state" / ...` literal elsewhere
(an earlier prototype's "sanctioned path-definition sites" rule, for its reason: the
two copies drift and the daemon reads one while a script writes the other).

RESOLUTION ORDER: the `--data` flag > `BUTLER_DATA` > the LEGACY layout.
With nothing set, every path is what it was before this module existed --
`<repo>\\state`, `<repo>\\ssl`, and the root project is the repo -- so an
install that has not been migrated behaves byte for byte as before. An
explicitly-set data root MUST already exist (fail fast): a typo booting an
empty Butler somewhere else looks exactly like losing every conversation.

`BUTLER_STATE` keeps its old meaning and ONLY that: "Butler's records live
here". conftest and every scratch-daemon recipe set it, and neither the
certificate nor the root project moves with it -- a scratch `--tls` daemon
keeps finding the one cert, and its root row stays where it was.

The working directory is NEVER consulted: `start.bat` does `cd /d` into the
code tree, so a cwd-relative record path would read the repository's copy.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]

#: Butler's own records inside a data root. Hidden by its leading dot, and
#: skipped by the file explorer (`files.SKIP`, AC-414).
RECORDS_DIR = ".butler"


class DataRootError(Exception):
    """A data root that cannot be used. Its message names the path and says
    what to do; the daemon lets it end the boot."""


@dataclass(frozen=True)
class Paths:
    code: Path          # the repository: daemon/, web/, config/
    data: Path          # the ROOT project's working directory
    records: Path       # settings, shortcuts, mode, connections, chats,
                        # memory, prompt
    ssl: Path           # cert.pem, key.pem
    logs: Path          # voice-trace.log (butler.log is start.bat's)
    attachments: Path   # what the owner attached, openable by an agent
    persona: Path       # SOUL.md and USER.md (docs/identity-memory.md 11.1)

    @property
    def library(self) -> Path:
        """Recipes, tasks, automations and runs (docs/automations.md AU-1).
        Always inside the records, so a `BUTLER_STATE` override moves it with
        every other record and no layout spells it twice."""
        return self.records / "library"

    @property
    def artifacts(self) -> Path:
        """What an agent SHOWED in a conversation, one folder per chat
        (docs/rich-chat.md RC-3): snapshots, so records only."""
        return self.records / "artifacts"

    @property
    def notify(self) -> Path:
        """One JSON file per notification channel (docs/automations.md 7,
        AC-473). Inside the records for `library`'s reason."""
        return self.records / "notify"

    @property
    def triggers(self) -> Path:
        """One JSON file per trigger source, `<name>.json`
        (docs/proactive.md PR-26, AC-496): `notify`'s shape, secrets
        included."""
        return self.records / "triggers"

    @property
    def trigger_cursors(self) -> Path:
        """Where each trigger source has got to, `<name>.json` (PR-27,
        AC-497): the IMAP UID and the calendar occurrences already fired,
        so a restart neither floods nor fires twice. A subfolder, so the
        sources' own listing never reads one as a source."""
        return self.triggers / "cursors"

    @property
    def heartbeat(self) -> Path:
        """One Markdown checklist per project, `<row id>.md`
        (docs/proactive.md PR-9, AC-481). Inside the records, never a
        project's own folder: the project is the owner's."""
        return self.records / "heartbeat"

    @property
    def prechecks(self) -> Path:
        """Precheck scripts the OWNER places by hand (docs/proactive.md
        PR-23, AC-493): an automation names one; no route writes here."""
        return self.records / "prechecks"

    @property
    def held_speech(self) -> Path:
        """Spoken lines held through quiet hours (docs/proactive.md PR-16,
        AC-487): a FILE, so a restart overnight loses nothing."""
        return self.records / "held-speech.json"

    @property
    def phone_queue(self) -> Path:
        """The owner's phone messages received and not yet answered, one
        file per chat app (`<kind>.json`, docs/messaging.md RV-13, AC-599,
        AC-600): a restart runs again what never started and says so about
        what was in flight. Holds message TEXT, so records only."""
        return self.records / "phone"

    @property
    def whatsapp(self) -> Path:
        """The WhatsApp sidecar's records (docs/messaging.md MS-4, AC-501):
        `session/` (the link's credentials, SECRET: deleting it unlinks),
        `whatsapp.db` (the archive) and `sent.jsonl` (the send audit). Inside
        the records, never the repository, for `library`'s reason."""
        return self.records / "whatsapp"


def layout(root: Path, code: Path = CODE) -> Paths:
    """The MIGRATED layout: the data root is the root project's home and
    Butler's records are one hidden folder inside it."""
    records = Path(root) / RECORDS_DIR
    return Paths(code=code, data=Path(root), records=records,
                 ssl=records / "ssl", logs=records / "logs",
                 attachments=Path(root) / "attachments", persona=Path(root))


def legacy(code: Path = CODE) -> Paths:
    """The layout before `docs/data-root.md`: everything inside the code
    tree. `scripts/migrate-data-root.py` reads a source through this.

    The persona files go to `state/`, not to the data root: here the data root
    IS the repository, and a file the owner edits must not sit in the code's
    git working tree (docs/identity-memory.md 11.1)."""
    state = Path(code) / "state"
    return Paths(code=Path(code), data=Path(code), records=state,
                 ssl=Path(code) / "ssl", logs=state,
                 attachments=state / "attachments", persona=state)


def resolve(data: str | None = None, env=None, check: bool = True) -> Paths:
    """The paths for this process. `data` is the `--data` flag.

    `check=False` skips the existence test, for the voice trace, which asks
    on every line and must never raise (D-1); the daemon has already asked
    with the check at boot.
    """
    env = os.environ if env is None else env
    raw = (data or env.get("BUTLER_DATA") or "").strip()
    if raw:
        root = Path(raw)
        if check:
            if not root.is_dir():
                raise DataRootError(
                    f"the data root {root} does not exist or is not a "
                    f"directory. Create it, or migrate into it with: "
                    f".venv\\Scripts\\python.exe -X utf8 "
                    f"scripts\\migrate-data-root.py --data \"{root}\"")
            resolved = root.resolve()
            if resolved == CODE or CODE in resolved.parents:
                raise DataRootError(
                    f"the data root {root} is inside Butler's code tree "
                    f"{CODE}; it must be a separate folder (docs/data-root.md)")
        found = layout(root)
    else:
        found = legacy()
    override = (env.get("BUTLER_STATE") or "").strip()
    if override:
        state = Path(override)
        found = Paths(
            code=found.code, data=found.data, records=state, ssl=found.ssl,
            logs=state / "logs" if raw else state,
            attachments=found.attachments if raw else state / "attachments",
            persona=found.persona if raw else state)
    return found


def main(argv=None, env=None) -> int:
    """`python -m daemon.paths <field>` prints one path, for `start.bat`.

    Exit 2 with the reason on stderr when the data root cannot be used, so
    the launcher stops rather than starting a daemon that will.
    """
    argv = sys.argv[1:] if argv is None else argv
    fields = Paths.__dataclass_fields__
    if len(argv) != 1 or argv[0] not in fields:
        print(f"usage: python -m daemon.paths {{{','.join(fields)}}}",
              file=sys.stderr)
        return 1
    try:
        print(getattr(resolve(env=env), argv[0]))
    except DataRootError as e:
        print(f"[butler] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
