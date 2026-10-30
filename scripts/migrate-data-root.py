"""Copy Butler's legacy records out of the repository into a DATA ROOT.

`docs/data-root.md`, AC-417..AC-419, the owner's "two folders" ruling. The
repository becomes code only; the data root becomes the root project's home,
with Butler's own records in its hidden `.butler` folder.

    .venv\\Scripts\\python.exe -X utf8 scripts\\migrate-data-root.py --data E:\\Butler-Data --dry-run
    .venv\\Scripts\\python.exe -X utf8 scripts\\migrate-data-root.py --data E:\\Butler-Data

What it does, in order:

1. REFUSES while anything listens on the daemon's port (default 8140 or
   BUTLER_PORT): a store the live daemon is writing copies torn. It checks the
   port and kills nothing. `--dry-run` only warns.
2. COPIES -- never moves -- `state\\`, `ssl\\` and `butler.log*` from the
   source (default: this repository) into the layout `daemon/paths.py`
   names. A file that already exists at the destination is NEVER overwritten
   (robocopy's /XC /XN /XO rule): it is the newer truth if the new daemon
   has already run. `state\\attachments` goes to `<data>\\attachments`, where
   the owner and an agent can open it.
3. VERIFIES by hash that nothing is left to copy (robocopy /L's question):
   every source file has a destination, and any destination that differs is
   listed as kept. A missing file is exit 4.
4. REWRITES the old attachments directory to the new one inside Butler's own
   chat records. An agent's `*.session.json` is its memory and is left alone.
5. SHORTCUTS: the repository's row stays (chats filed there keep their
   project, because that is where they ran) and is labelled
   "butler (code)"; a row for the data root, labelled "Butler", is added and
   becomes the root when the daemon boots with BUTLER_DATA set.
6. Writes a starter `<data>\\AGENTS.md` only if there is none.
7. Prints the rollback.

Idempotent: a second run copies nothing and changes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from daemon import paths as paths_mod          # noqa: E402
from daemon import shortcuts as shortcuts_mod  # noqa: E402

ROOT_LABEL = "Butler"
CODE_LABEL = "butler (code)"

# No language instruction: `docs/language.md` D2 (a model answers in the
# language it hears). Who Butler is and who the owner is live in SOUL.md and
# USER.md beside this file, which the daemon creates at boot
# (`docs/identity-memory.md` 11); this file is for what is true of THIS folder.
STARTER_AGENTS = """# About this home folder

This is Butler's home: the folder everyday conversations run in. Files the
owner keeps here, and anything they attach (in `attachments/`), are theirs.
Butler's own records are in `.butler/`; leave that folder alone.

Who Butler is and who the owner is are in SOUL.md and USER.md beside this
file (also editable in Settings > Identity).
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(src: paths_mod.Paths, dst: paths_mod.Paths) -> list[tuple[Path, Path]]:
    """Every (source file, destination file) pair, in a stable order."""
    pairs: list[tuple[Path, Path]] = []
    if src.records.is_dir():
        for f in sorted(src.records.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src.records)
            if f.is_relative_to(src.attachments):
                target = dst.attachments / f.relative_to(src.attachments)
            elif len(rel.parts) == 1 and rel.name.startswith("voice-trace.log"):
                target = dst.logs / rel.name
            else:
                target = dst.records / rel
            pairs.append((f, target))
    if src.ssl.is_dir():
        pairs += [(f, dst.ssl / f.name)
                  for f in sorted(src.ssl.iterdir()) if f.is_file()]
    pairs += [(f, dst.logs / f.name)
              for f in sorted(src.code.glob("butler.log*")) if f.is_file()]
    return pairs


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _swap(value, old: list[str], new: str):
    """Every string in a parsed JSON value, with an old prefix replaced."""
    if isinstance(value, str):
        for o in old:
            value = value.replace(o, new)
        return value
    if isinstance(value, list):
        return [_swap(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _swap(v, old, new) for k, v in value.items()}
    return value


def rewrite_attachment_paths(src: paths_mod.Paths,
                             dst: paths_mod.Paths) -> tuple[list[str], int]:
    """Butler's chat records point at the new attachments folder. Answers
    (records rewritten, agent session files that still name the old one)."""
    old = str(src.attachments)
    spellings = [old, old.replace("\\", "/")]
    chats = dst.records / "chats"
    changed, sessions = [], 0
    for f in sorted(chats.glob("*.json")) if chats.is_dir() else []:
        text = f.read_text(encoding="utf-8")
        if f.name.endswith(".session.json"):
            sessions += any(s in text for s in spellings + [
                old.replace("\\", "\\\\")])
            continue
        record = json.loads(text)
        swapped = _swap(record, spellings, str(dst.attachments))
        if swapped != record:
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(swapped, indent=2), encoding="utf-8")
            tmp.replace(f)
            changed.append(f.name)
    return changed, sessions


def rewrite_shortcuts(dst: paths_mod.Paths, code: Path) -> list[str]:
    """Label the code row, add the data root's row. Answers what changed."""
    path = dst.records / "shortcuts.json"
    if not path.is_file():
        return []                    # a fresh store: the daemon mints the root
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.setdefault("rows", {})
    same = shortcuts_mod.same_dir
    changes = []
    code_rows = [r for r in rows.values() if same(r["path"]) == same(str(code))]
    for row in code_rows:
        if row.get("label") != CODE_LABEL:
            row["label"] = CODE_LABEL
            changes.append(f"relabelled {row['path']} as {CODE_LABEL!r}")
    if not code_rows:
        rows[shortcuts_mod.new_id()] = {"path": str(code), "label": CODE_LABEL,
                                        "chords": {}}
        changes.append(f"added {CODE_LABEL!r} at {code}")
    if not any(same(r["path"]) == same(str(dst.data)) for r in rows.values()):
        rows[shortcuts_mod.new_id()] = {"path": str(dst.data),
                                        "label": ROOT_LABEL, "chords": {}}
        changes.append(f"added the root {ROOT_LABEL!r} at {dst.data}")
    if changes:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        tmp.replace(path)
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="migrate-data-root")
    ap.add_argument("--data", required=True,
                    help="the data root to create or fill, e.g. E:\\Butler-Data")
    ap.add_argument("--source", default=str(REPO),
                    help="the legacy layout to copy from (default: this repo)")
    ap.add_argument("--code", default=None,
                    help="the directory the old root row names "
                         "(default: --source)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("BUTLER_PORT", 8140)))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    source = Path(args.source).resolve()
    code = Path(args.code) if args.code else source
    data = Path(args.data)
    src = paths_mod.legacy(source)
    dst = paths_mod.layout(data)
    dry = args.dry_run
    tag = "DRY RUN -- " if dry else ""

    target = data.resolve()
    for tree in {source, paths_mod.CODE}:
        if target == tree or tree in target.parents:
            print(f"refused: the data root {data} is inside {tree}; it must "
                  f"be a separate folder")
            return 2
    if port_busy(args.port):
        message = (f"something is listening on 127.0.0.1:{args.port}; stop "
                   f"Butler (stop.bat) first -- copying a store it is "
                   f"writing gives a torn copy. Nothing was changed.")
        if not dry:
            print(f"refused: {message}")
            return 3
        print(f"WARNING: {message}")

    pairs = plan(src, dst)
    print(f"{tag}migrating {src.records} -> {dst.records}")
    todo = [(s, d) for s, d in pairs if not d.exists()]
    for s, d in pairs:
        state = "copy" if (s, d) in todo else "keep (already there)"
        print(f"  {state:22s} {s.relative_to(source)} -> {d}")
    if dry:
        print(f"{tag}{len(todo)} file(s) would be copied, "
              f"{len(pairs) - len(todo)} already present. Nothing written.")
        return 0

    data.mkdir(exist_ok=True)
    for s, d in todo:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)

    missing = [d for _, d in pairs if not d.exists()]
    differs = [d for s, d in pairs if d.exists() and sha(s) != sha(d)]
    print(f"verify: {len(pairs)} file(s), {len(missing)} missing, "
          f"{len(differs)} differ from the source (kept: the destination is "
          f"newer or was rewritten below)")
    for d in differs:
        print(f"  kept   {d}")
    if missing:
        for d in missing:
            print(f"  MISSING {d}")
        return 4

    changed, sessions = rewrite_attachment_paths(src, dst)
    for name in changed:
        print(f"  attachments path rewritten in {name}")
    if sessions:
        print(f"  {sessions} agent session file(s) still name "
              f"{src.attachments}; left as the agent's own memory")
    for line in rewrite_shortcuts(dst, code):
        print(f"  shortcuts: {line}")
    dst.attachments.mkdir(exist_ok=True)
    agents = data / "AGENTS.md"
    if not agents.exists():
        agents.write_text(STARTER_AGENTS, encoding="utf-8")
        print(f"  wrote a starter {agents}")

    print(f"""
done. Nothing under {source} was changed or deleted.
Start Butler with BUTLER_DATA={data} (start.bat sets it).
ROLLBACK: stop Butler, change start.bat's BUTLER_DATA line back (or
`git checkout` the previous start.bat), and start it again -- the daemon then
reads {src.records} exactly as before. Anything written after the migration
lives only in {dst.records}. Delete {data} once the rollback is no longer
wanted; quarantine {src.records} (move it out of the repo) once the new
layout has been lived with.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
