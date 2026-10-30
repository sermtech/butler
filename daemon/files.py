"""A look at a project's files. `docs/file-explorer.md`, AC-303..AC-309.

Since docs/rich-chat.md RC-12/RC-13 (owner, 2026-10-08) the explorer also
serves a file's raw BYTES (`raw`, any kind, so an image or a video can be
shown) and takes ONE write, `save_upload`: a new file dropped into a folder,
never over an existing one. Both go through the same rules below.

PURE: no hub, no aiohttp. The routes in `butlerd.py` call `list_level` and
`preview` through `asyncio.to_thread` (D-25), because both walk a disk.

WHAT THIS EXPOSES, said plainly (the spec's 3.4): any tailnet caller can list
and read any file under any project row with one unauthenticated GET, and
since `POST /api/shortcuts` can add a row naming any directory (R2, the
tailnet is the boundary), "under a project row" bounds nothing against a
caller who writes a row first. What this module DOES bound is below, and all
of it is enforced here and none of it in the page.

THE ORDER OF THE RULES, and why each is where it is:

1. The ROOT comes from the store by row id, never from the caller (AC-304).
2. LEXICAL refusals, before any filesystem call (AC-305): absolute, drive,
   UNC, `..`, `:` (alternate data streams), NUL, reserved device names and
   the characters Windows forbids in a name. Each names its rule.
3. The root and the target are both RESOLVED and the target must be
   `is_relative_to` the root (AC-306). `Path.resolve` follows a junction
   [MEASURED 2026-09-26, Python 3.13.5, `mklink /J`], so a junction out of the
   project is seen for where it goes. The target is resolved NON-strict first
   so a path through an outside link is a 403 whether or not the far end
   exists; existence is asked after.
4. The SKIP and SECRETS name rules run on the RESOLVED components as well as
   the typed ones, because Windows opens `.env.`, `.env ` and an 8.3 alias
   like `NODE_M~1` as the canonical name [MEASURED 2026-09-26] -- a rule on
   the typed spelling alone is bypassable.

A race remains and is stated rather than hidden: between the check and the
open, something with write access to the project could swap a directory for
a junction. Whoever can do that can already start an agent there (R2).
"""
from __future__ import annotations

import codecs
import fnmatch
import os
from pathlib import Path

from daemon import artifacts
from daemon import attachments

MAX_ENTRIES = 1000
MAX_PREVIEW = 256 * 1024
# A dropped file (RC-13), the same 100 MB as anything an agent may show.
MAX_UPLOAD = artifacts.DEFAULT_CAP
BINARY_PROBE = 8192          # Orca's null-byte window

# Never listed, never served (3.2). Compared case-folded.
# `.butler` is Butler's own records inside the data root (docs/data-root.md,
# AC-414): the root project's home is the owner's folder, and settings, chats
# and the TLS key are not part of what they browse.
SKIP = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".butler"})

# Listed with `locked: true`, never previewed (3.4). A DENYLIST, incomplete by
# construction: it exists because a preview leaves no chat record behind.
SECRETS = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*",
           "id_ed25519*", "credentials.json",
           # Review 2026-10-29: AWS, npm, curl/git and PyPI credential files.
           "credentials", ".npmrc", ".netrc", ".git-credentials", ".pypirc")

_RESERVED = frozenset({"con", "prn", "aux", "nul",
                       *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))})
_FORBIDDEN = set('<>"|?*') | {chr(c) for c in range(1, 32)}


class FileError(Exception):
    """A refusal the route turns into `status` with this message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def is_secret(name: str) -> bool:
    low = name.casefold()
    return any(fnmatch.fnmatchcase(low, pat) for pat in SECRETS)


def clean_relative(rel: str) -> tuple[str, ...]:
    """The caller's path as components, or a 400 naming the rule it broke.

    Touches no disk (AC-305). `""` and `.` are the root.
    """
    rel = rel or ""
    if "\x00" in rel:
        raise FileError(400, "the path contains a NUL byte")
    if rel[:2] in ("\\\\", "//", "\\/", "/\\"):
        raise FileError(400, f"the path {rel!r} is a UNC path; name a path "
                             f"relative to the project")
    if len(rel) >= 2 and rel[1] == ":" and rel[0].isalpha():
        raise FileError(400, f"the path {rel!r} names a drive; name a path "
                             f"relative to the project")
    if rel[:1] in ("/", "\\"):
        raise FileError(400, f"the path {rel!r} is absolute; name a path "
                             f"relative to the project")
    if ":" in rel:
        raise FileError(400, f"the path {rel!r} contains ':', which on NTFS "
                             f"names an alternate data stream")
    parts = tuple(p for p in rel.replace("\\", "/").split("/")
                  if p not in ("", "."))
    for part in parts:
        if not part.rstrip(". "):
            raise FileError(400, f"the path {rel!r} contains a '..' "
                                 f"component; the explorer never leaves the "
                                 f"project")
        # Windows drops a trailing dot or space when it names a file, so
        # `.env ` would be checked as one name and written as `.env`
        # (review 2026-10-29: it slipped past the SECRETS and SKIP checks).
        if part != part.rstrip(". "):
            raise FileError(400, f"the name {part!r} ends in a dot or a "
                                 f"space, which Windows drops")
        bad = sorted(set(part) & _FORBIDDEN)
        if bad:
            raise FileError(400, f"the name {part!r} holds {bad!r}, which "
                                 f"Windows forbids in a file name")
        if part.split(".")[0].rstrip(" ").casefold() in _RESERVED:
            raise FileError(400, f"the name {part!r} is a reserved Windows "
                                 f"device name")
    return parts


def _open(store, sid: str, rel: str) -> tuple[Path, tuple[str, ...]]:
    """The resolved root and the caller's components, in the rule order: row
    id (404), then the lexical rules (400, no disk), THEN the root's own
    resolution (409) -- so a refused path never reaches the disk (AC-305)."""
    try:
        row = store.get(sid)
    except KeyError as e:
        raise FileError(404, e.args[0]) from None
    parts = clean_relative(rel)
    try:
        root = Path(row.path).resolve(strict=True)
    except OSError:
        raise FileError(409, f"project {sid!r} names {row.path}, which does "
                             f"not exist any more") from None
    if not root.is_dir():
        raise FileError(409, f"project {sid!r} names {row.path}, which is "
                             f"not a directory")
    return root, parts


def _check_skip(parts, shown: str) -> None:
    for part in parts:
        if part.casefold() in SKIP:
            raise FileError(404, f"{shown!r} is inside {part!r}, which the "
                                 f"explorer keeps hidden")


def resolve_inside(root: Path, parts: tuple[str, ...]) -> Path:
    """The target, resolved, proven inside ROOT, existing, and not skipped."""
    shown = "/".join(parts)
    _check_skip(parts, shown)
    target = root.joinpath(*parts).resolve(strict=False)
    if not target.is_relative_to(root):
        raise FileError(403, f"{shown!r} resolves outside the project")
    if not target.exists():
        raise FileError(404, f"{shown!r} does not exist in the project")
    _check_skip(target.relative_to(root).parts, shown)
    return target


def _entry(e: os.DirEntry, root: Path) -> dict:
    link = e.is_symlink() or e.is_junction()
    outside = False
    if link:
        outside = not Path(e.path).resolve(strict=False).is_relative_to(root)
    # A link to a directory sorts with the directories even when it points
    # outside and cannot be opened; `outside` is what the page reads for that.
    is_dir = e.is_dir()
    st = e.stat(follow_symlinks=not outside)
    out = {"name": e.name,
           "kind": "link" if link else ("dir" if is_dir else "file"),
           "mtime": st.st_mtime, "outside": outside,
           "locked": (not is_dir) and is_secret(e.name)}
    if not is_dir and not outside:
        out["size"] = st.st_size
    out["_dir"] = is_dir
    return out


def list_level(store, sid: str, rel: str) -> dict:
    """ONE directory level (AC-303): dirs first, case-folded, capped."""
    root, parts = _open(store, sid, rel)
    target = resolve_inside(root, parts)
    if not target.is_dir():
        raise FileError(400, f"{'/'.join(parts)!r} is a file, not a "
                             f"directory; preview it instead")
    entries, skipped = [], []
    with os.scandir(target) as it:
        for e in it:
            if e.name.casefold() in SKIP:
                skipped.append(e.name)
                continue
            entries.append(_entry(e, root))
    entries.sort(key=lambda x: (not x["_dir"], x["name"].casefold()))
    for x in entries:
        del x["_dir"]
    return {"project": sid, "path": "/".join(parts),
            "entries": entries[:MAX_ENTRIES],
            "truncated": len(entries) > MAX_ENTRIES, "total": len(entries),
            "skipped": sorted(skipped, key=str.casefold)}


def preview(store, sid: str, rel: str) -> dict:
    """One file as bounded, labelled TEXT, never its bytes (AC-308, AC-309)."""
    root, parts = _open(store, sid, rel)
    shown = "/".join(parts)
    target = resolve_inside(root, parts)
    if target.is_dir():
        raise FileError(400, f"{shown!r} is a directory; list it instead")
    if is_secret(parts[-1]) or is_secret(target.name):
        raise FileError(403, f"{shown!r} matches the secrets list and is "
                             f"never previewed")
    size = target.stat().st_size
    with open(target, "rb") as fh:
        data = fh.read(MAX_PREVIEW)
    truncated = size > len(data)
    # What the page draws it as (RC-12): the chat's classifier, from bytes.
    kind, media = artifacts.classify(data[:BINARY_PROBE], target.name)
    answer = {"path": shown, "size": size, "text": None,
              "truncated": truncated, "binary": False, "lossy": False,
              "type": None, "kind": kind, "media": media}
    if b"\x00" in data[:BINARY_PROBE]:
        answer["binary"] = True
        try:
            answer["type"] = attachments.sniff(data[:BINARY_PROBE],
                                               target.name)[1]
        except attachments.Unsupported:
            pass
        return answer
    # `final=not truncated`: a cut through a multibyte character leaves a
    # partial tail that is dropped rather than called lossy.
    try:
        answer["text"] = codecs.getincrementaldecoder("utf-8")(
            "strict").decode(data, final=not truncated)
    except UnicodeDecodeError:
        answer["lossy"] = True
        answer["text"] = codecs.getincrementaldecoder("utf-8")(
            "replace").decode(data, final=not truncated)
    return answer


def raw(store, sid: str, rel: str) -> tuple[Path, str, str]:
    """(path, kind, media) for one file's BYTES (RC-12), under `preview`'s
    rules exactly: row id, lexical, resolved inside, SKIP, SECRETS."""
    root, parts = _open(store, sid, rel)
    shown = "/".join(parts)
    target = resolve_inside(root, parts)
    if target.is_dir():
        raise FileError(400, f"{shown!r} is a directory; list it instead")
    if is_secret(parts[-1]) or is_secret(target.name):
        raise FileError(403, f"{shown!r} matches the secrets list and is "
                             f"never served")
    with open(target, "rb") as fh:
        head = fh.read(BINARY_PROBE)
    kind, media = artifacts.classify(head, target.name)
    return target, kind, media


def save_upload(store, sid: str, rel_dir: str, name: str,
                data: bytes) -> dict:
    """A dropped file, written NEW into the folder `rel_dir` (RC-13).

    The name is ONE component under `clean_relative`'s rules, never a SKIP
    or SECRETS name (an upload must not plant a `.env` an agent then
    trusts), and never over an existing file: `open(..., "xb")` makes the
    existence check and the create one step, so a race cannot turn it into
    an overwrite (409)."""
    root, parts = _open(store, sid, rel_dir)
    folder = resolve_inside(root, parts)
    if not folder.is_dir():
        raise FileError(400, f"{'/'.join(parts)!r} is not a folder")
    named = clean_relative(name)
    if len(named) != 1:
        raise FileError(400, f"the name {name!r} must be one file name, "
                             f"not a path")
    (leaf,) = named
    if leaf.casefold() in SKIP or is_secret(leaf):
        raise FileError(403, f"{leaf!r} is a name the explorer keeps hidden "
                             f"or treats as a secret; it is not written")
    if len(data) > MAX_UPLOAD:
        raise FileError(413, f"{leaf} is {len(data)} bytes; the limit is "
                             f"{MAX_UPLOAD}")
    target = folder / leaf
    if not target.resolve(strict=False).is_relative_to(root):
        raise FileError(403, f"{leaf!r} resolves outside the project")
    try:
        with open(target, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise FileError(409, f"{'/'.join(parts + named)!r} already exists; "
                             f"an upload never replaces a file") from None
    return {"path": "/".join(parts + named), "size": len(data)}
