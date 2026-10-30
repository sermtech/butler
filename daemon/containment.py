"""Where a file an agent names may come from. ONE rule for every door that
takes a path from a model: the WhatsApp send (docs/messaging.md MS-18b,
AC-562, RV-2/AC-587) and `show_file` (docs/rich-chat.md RC-11, AC-614..616).

Two copies of this rule would drift, and the WhatsApp one already paid for
its review, so it moved here whole rather than being written a second time.

PURE: no hub, no aiohttp. Opens nothing; the caller reads the REAL path it is
handed back, so what is judged is what is read.
"""
from __future__ import annotations

import os
from pathlib import Path

from daemon import files

FILE_RULE = ("a file an agent sends or shows must be inside the "
             "conversation's project folder or Butler's data root")

RECORDS_RULE = ("a file an agent sends or shows may never come from "
                "Butler's records folder")

SECRETS_RULE = ("a file an agent sends or shows may never match the secrets "
                "list (.env, keys, certificates, credentials)")


class FileRefused(ValueError):
    """A file the rule refuses; the message names the rule."""


class RecordsRefused(FileRefused):
    """A file inside Butler's records (`RECORDS_RULE`, RV-2, AC-587)."""


class SecretRefused(FileRefused):
    """A file whose name is on `files.SECRETS` (AC-614)."""


def records_dirs(where) -> tuple:
    """What a tool's file may NEVER come from, whatever the roots (RV-2,
    AC-587): Butler's records (the WhatsApp session, notify and trigger
    secrets), the TLS folder, and `<data>/.butler` itself even when
    `BUTLER_STATE` moved the records elsewhere. `where` is a
    `paths.Paths`."""
    from daemon import paths as paths_mod
    return (where.records, where.ssl,
            Path(where.data) / paths_mod.RECORDS_DIR)


def _under(folded: Path, root) -> bool:
    base = Path(os.path.normcase(os.path.realpath(root)))
    return folded == base or folded.is_relative_to(base)


def file_inside(path, roots, forbidden) -> Path:
    """The REAL path of `path` when it lies inside one of `roots`, inside
    none of `forbidden` (`records_dirs`), and is not named like a secret,
    compared after every path is resolved, so a link or junction into the
    records is inside them and a link out of a project is outside it. The
    secrets list is checked on the name as given AND the real name, because
    a link can rename (files.py's own reason, rule 4). Raises `FileRefused`
    naming the rule. Opens nothing."""
    real = Path(os.path.realpath(path))
    folded = Path(os.path.normcase(str(real)))
    if not forbidden:
        raise ValueError("file_inside needs the records folders to refuse")
    for secret in forbidden:
        if _under(folded, secret):
            raise RecordsRefused(f"{RECORDS_RULE}; {real} is inside "
                                 f"{os.path.realpath(secret)}")
    if files.is_secret(Path(path).name) or files.is_secret(real.name):
        raise SecretRefused(f"{SECRETS_RULE}; {real.name} does")
    for root in roots:
        if root and _under(folded, root):
            return real
    raise FileRefused(f"{FILE_RULE}; {real} is not")
