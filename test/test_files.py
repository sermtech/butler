"""The file explorer's RULES, without a socket. `docs/file-explorer.md` 3.1-3.4,
AC-303..AC-309.

`daemon/files.py` is pure: it takes the shortcuts store and a row id and never
sees a hub, so every refusal here is driven directly and the route tests
(`test_files_routes.py`) only have to prove the wiring.

THE ORDER OF THE RULES IS THE CONTRACT, and three facts measured on this
machine (2026-09-26, Python 3.13.5) decide it:

* `Path.resolve` FOLLOWS a junction made by `mklink /J`, so containment
  checked on the resolved path sees where a junction really goes.
* Symlinks CANNOT be created here without the privilege (WinError 1314), so
  the symlink case skips with that reason printed, and the junction carries
  AC-306 alone.
* An 8.3 short name, a trailing dot and a trailing space all resolve to the
  canonical long name (`NODE_M~1`, `.env.`, `.env `). So a name rule checked
  only on what the CALLER typed is bypassable, and the SKIP and SECRETS sets
  are checked again on the RESOLVED components.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import files                                         # noqa: E402
from daemon import shortcuts as shortcuts_mod                    # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32


def junction(link: Path, target: Path) -> None:
    """A REAL Windows junction. No admin needed, unlike a symlink."""
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                           str(target)], capture_output=True, text=True)
    assert done.returncode == 0, f"mklink /J failed: {done.stdout}{done.stderr}"


def short_name(path: Path) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    assert n, f"no short name for {path}"
    return Path(buf.value).name


@pytest.fixture
def project(tmp_path):
    """A project row over a small tree, plus a directory OUTSIDE it."""
    root = tmp_path / "Repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8", newline="")
    (root / "README.md").write_text("# readme\n", encoding="utf-8", newline="")
    (root / "a.txt").write_text("alpha\n", encoding="utf-8", newline="")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote] url=https://tok@x\n")
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8", newline="")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stolen.txt").write_text("not yours\n", encoding="utf-8", newline="")
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    row = store.upsert("", str(root), "Repo")
    return store, row.id, root, outside


def names(listing: dict) -> list[str]:
    return [e["name"] for e in listing["entries"]]


# --------------------------------------------------------------------------- #
# AC-303 -- one level, sorted, capped
# --------------------------------------------------------------------------- #
def test_one_level_directories_first_then_casefolded_names(project):
    store, sid, root, _ = project

    got = files.list_level(store, sid, "")

    # Case-folded order, exactly: ".env" < "a.txt" < "readme.md".
    assert names(got) == ["src", ".env", "a.txt", "README.md"]
    assert got["project"] == sid and got["path"] == ""
    assert got["truncated"] is False and got["total"] == 4
    by = {e["name"]: e for e in got["entries"]}
    assert by["src"]["kind"] == "dir" and "size" not in by["src"]
    assert by["a.txt"]["kind"] == "file" and by["a.txt"]["size"] == 6
    assert isinstance(by["a.txt"]["mtime"], float)
    assert by["a.txt"]["outside"] is False


def test_only_the_IMMEDIATE_children_are_listed(project):
    store, sid, root, _ = project

    got = files.list_level(store, sid, "src")

    assert names(got) == ["main.py"] and got["path"] == "src"


def test_windows_separators_are_the_same_path(project):
    store, sid, root, _ = project
    (root / "src" / "deep").mkdir()
    (root / "src" / "deep" / "x.txt").write_text("x")

    assert names(files.list_level(store, sid, "src\\deep")) == ["x.txt"]
    assert files.list_level(store, sid, "src\\deep")["path"] == "src/deep"


def test_the_listing_is_capped_at_1000_and_says_how_many_there_were(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(1005):
        (root / f"f{i:04d}.txt").write_bytes(b"")
    (root / "zdir").mkdir()
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    sid = store.upsert("", str(root), "Big").id

    got = files.list_level(store, sid, "")

    assert len(got["entries"]) == files.MAX_ENTRIES == 1000
    assert got["truncated"] is True and got["total"] == 1006
    # The cap is applied AFTER sorting, so the directory is never the one cut.
    assert got["entries"][0]["name"] == "zdir"


# --------------------------------------------------------------------------- #
# AC-304 -- the root is the row's
# --------------------------------------------------------------------------- #
def test_an_unknown_row_is_a_404_naming_it(project):
    store, _, _, _ = project

    with pytest.raises(files.FileError) as e:
        files.list_level(store, "s-nope", "")

    assert e.value.status == 404 and "s-nope" in str(e.value)


def test_a_row_whose_directory_is_gone_is_a_409_naming_the_path(tmp_path):
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    gone = tmp_path / "gone"
    sid = store.upsert("", str(gone), "Gone").id

    for call in (files.list_level, files.preview):
        with pytest.raises(files.FileError) as e:
            call(store, sid, "x.txt" if call is files.preview else "")
        assert e.value.status == 409 and str(gone) in str(e.value)


def test_a_row_spelled_in_another_CASE_is_the_same_root(project):
    """Orca's recorded trap: `C:\\Repo` versus `c:\\repo`. Both sides are
    resolved, so the root is canonical however the row spells it."""
    store, _, root, outside = project
    sid = store.upsert("", str(root).lower(), "lower").id
    junction(root / "escape", outside)

    assert names(files.list_level(store, sid, "SRC")) == ["main.py"]
    with pytest.raises(files.FileError) as e:
        files.list_level(store, sid, "escape")
    assert e.value.status == 403


# --------------------------------------------------------------------------- #
# AC-305 -- lexical refusals, BEFORE the disk
# --------------------------------------------------------------------------- #
LEXICAL = [
    ("../outside", ".."),
    ("src/../../x", ".."),
    ("src\\..\\..\\x", ".."),
    ("...", ".."),
    ("/x", "absolute"),
    ("\\x", "absolute"),
    ("C:/Windows", "drive"),
    ("c:foo", "drive"),
    ("\\\\host\\share", "UNC"),
    ("//host/share", "UNC"),
    ("a.txt:stream", "':'"),
    ("a.txt::$DATA", "':'"),
    ("a\x00b", "NUL"),
    ("CON", "reserved"),
    ("nul.txt", "reserved"),
    ("src/COM1", "reserved"),
    ("lpt9.log", "reserved"),
    ("a*.txt", "forbids"),
    ("what?", "forbids"),
]


@pytest.mark.parametrize("rel, rule", LEXICAL)
@pytest.mark.parametrize("call", ["list_level", "preview"])
def test_a_lexical_refusal_is_a_400_NAMING_THE_RULE_and_touches_no_disk(
        project, monkeypatch, rel, rule, call):
    """The 400 must name the rule, so the `..` mutation fails HERE, on the
    refusal, and not later on containment (phase 1's mutation instruction)."""
    store, sid, _, _ = project

    def no_disk(*a, **k):
        raise AssertionError(f"the disk was touched for {rel!r}")

    # Scoped to the call alone: pytest's own reporter resolves paths, so a
    # patch still live while it formats a failure crashes the report.
    with monkeypatch.context() as m, pytest.raises(files.FileError) as e:
        m.setattr(pathlib.Path, "resolve", no_disk)
        m.setattr(os, "scandir", no_disk)
        m.setattr(os, "stat", no_disk)
        getattr(files, call)(store, sid, rel)

    assert e.value.status == 400, str(e.value)
    assert rule in str(e.value), f"{rel!r} refused for the wrong rule: {e.value}"


# --------------------------------------------------------------------------- #
# AC-306 -- containment after resolution, with a REAL junction
# --------------------------------------------------------------------------- #
def test_a_junction_OUT_of_the_project_is_listed_as_outside_and_never_followed(
        project):
    store, sid, root, outside = project
    junction(root / "escape", outside)

    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by["escape"]["kind"] == "link"
    assert by["escape"]["outside"] is True

    for call, rel in ((files.list_level, "escape"),
                      (files.preview, "escape/stolen.txt"),
                      (files.preview, "escape/not-there.txt")):
        with pytest.raises(files.FileError) as e:
            call(store, sid, rel)
        assert e.value.status == 403, f"{rel}: {e.value}"
        assert "outside" in str(e.value)


def test_a_junction_that_stays_INSIDE_the_project_is_followed(project):
    store, sid, root, _ = project
    junction(root / "alias", root / "src")

    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by["alias"]["kind"] == "link" and by["alias"]["outside"] is False
    assert names(files.list_level(store, sid, "alias")) == ["main.py"]
    assert files.preview(store, sid, "alias/main.py")["text"] == "print('hi')\n"


def test_a_DANGLING_junction_costs_one_row_not_the_listing(project):
    """A junction whose target was deleted is still listed; stat-ing through
    it would raise and take every sibling down with it."""
    store, sid, root, _ = project
    doomed = root / "doomed"
    doomed.mkdir()
    junction(root / "dangling", doomed)
    doomed.rmdir()

    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by["dangling"]["kind"] == "link" and "a.txt" in by
    with pytest.raises(files.FileError) as e:
        files.list_level(store, sid, "dangling")
    assert e.value.status == 404


def test_a_symlink_out_of_the_project_is_refused_too(project):
    store, sid, root, outside = project
    try:
        os.symlink(outside, root / "sl", target_is_directory=True)
    except OSError as e:
        pytest.skip(f"this process cannot create a symlink ({e}); the "
                    f"junction tests carry AC-306 alone")

    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by["sl"]["outside"] is True
    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, "sl/stolen.txt")
    assert e.value.status == 403


def test_a_missing_path_inside_is_a_404_naming_it(project):
    store, sid, _, _ = project

    with pytest.raises(files.FileError) as e:
        files.list_level(store, sid, "nope/deeper")
    assert e.value.status == 404 and "nope/deeper" in str(e.value)


# --------------------------------------------------------------------------- #
# AC-307 -- the SKIP set
# --------------------------------------------------------------------------- #
def test_the_skip_set_is_absent_and_NAMED(project):
    store, sid, _, _ = project

    got = files.list_level(store, sid, "")

    assert ".git" not in names(got) and "node_modules" not in names(got)
    assert got["skipped"] == [".git", "node_modules"]


@pytest.mark.parametrize("rel", [".git", ".git/config", "node_modules/left-pad",
                                 ".GIT/config", "src/../.git"])
def test_a_path_through_the_skip_set_is_a_404_naming_the_rule(project, rel):
    store, sid, _, _ = project
    call = files.preview if rel.endswith("config") else files.list_level
    if ".." in rel:
        call = files.list_level

    with pytest.raises(files.FileError) as e:
        call(store, sid, rel)
    if ".." in rel:
        assert e.value.status == 400        # the lexical rule is first
    else:
        assert e.value.status == 404 and "hidden" in str(e.value), e.value


def test_an_8dot3_SHORT_NAME_cannot_reach_the_skip_set(project):
    """`NODE_M~1` resolves to `node_modules`: the rule runs on the resolved
    components as well as the typed ones."""
    store, sid, root, _ = project
    alias = short_name(root / "node_modules")
    assert alias.upper() != "NODE_MODULES", "no 8.3 alias on this volume"

    with pytest.raises(files.FileError) as e:
        files.list_level(store, sid, alias)
    assert e.value.status == 404 and "hidden" in str(e.value)


def test_a_junction_INTO_the_skip_set_is_refused(project):
    store, sid, root, _ = project
    junction(root / "g", root / ".git")

    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, "g/config")
    assert e.value.status == 404 and "hidden" in str(e.value)


# --------------------------------------------------------------------------- #
# AC-308 -- preview is bounded and labelled
# --------------------------------------------------------------------------- #
def test_a_small_text_file_is_previewed_whole(project):
    store, sid, _, _ = project

    got = files.preview(store, sid, "src/main.py")

    assert got == {"path": "src/main.py", "size": 12, "text": "print('hi')\n",
                   "truncated": False, "binary": False, "lossy": False,
                   "type": None, "kind": "code", "media": "text/plain"}


def test_a_big_text_file_is_cut_at_256_KiB_and_SAYS_so(project):
    store, sid, root, _ = project
    (root / "big.txt").write_bytes(b"a" * (1024 * 1024))

    got = files.preview(store, sid, "big.txt")

    assert files.MAX_PREVIEW == 256 * 1024
    assert len(got["text"]) == files.MAX_PREVIEW and got["truncated"] is True
    assert got["size"] == 1024 * 1024


def test_a_cut_through_a_multibyte_character_is_NOT_called_lossy(project):
    store, sid, root, _ = project
    (root / "edge.txt").write_bytes(b"a" * (files.MAX_PREVIEW - 1)
                                    + "\u00e9".encode("utf-8") + b"tail")

    got = files.preview(store, sid, "edge.txt")

    assert got["truncated"] is True and got["lossy"] is False
    assert "\ufffd" not in got["text"]


def test_a_file_that_is_not_utf8_is_shown_and_LABELLED_lossy(project):
    store, sid, root, _ = project
    (root / "latin.txt").write_bytes(b"caf\xe9\n")

    got = files.preview(store, sid, "latin.txt")

    assert got["lossy"] is True and got["text"] == "caf\ufffd\n"


def test_a_binary_is_NAMED_and_carries_no_content(project):
    store, sid, root, _ = project
    (root / "pic.png").write_bytes(PNG)
    (root / "blob.bin").write_bytes(b"\x01\x00\x02")

    png = files.preview(store, sid, "pic.png")
    assert png["binary"] is True and png["text"] is None
    assert png["type"] == "image/png" and png["size"] == len(PNG)
    blob = files.preview(store, sid, "blob.bin")
    assert blob["binary"] is True and blob["text"] is None
    assert blob["type"] is None


def test_a_directory_is_not_previewed(project):
    store, sid, _, _ = project

    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, "src")
    assert e.value.status == 400 and "directory" in str(e.value)


# --------------------------------------------------------------------------- #
# AC-309 -- secrets are listed, never previewed
# --------------------------------------------------------------------------- #
SECRETS = [".env", ".env.local", "server.pem", "tls.KEY", "cert.pfx",
           "cert.p12", "id_rsa", "id_rsa.pub", "id_ed25519", "credentials.json"]


@pytest.mark.parametrize("name", SECRETS)
def test_a_secret_is_listed_LOCKED_and_its_preview_is_403(project, name):
    store, sid, root, _ = project
    (root / name).write_text("s3cret\n", encoding="utf-8", newline="")

    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by[name]["locked"] is True

    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, name)
    assert e.value.status == 403 and "secret" in str(e.value)


def test_an_ordinary_file_is_not_locked(project):
    store, sid, _, _ = project
    by = {e["name"]: e for e in files.list_level(store, sid, "")["entries"]}
    assert by["a.txt"]["locked"] is False and by["src"]["locked"] is False


@pytest.mark.parametrize("spelling", [".env.", ".env ", ".ENV"])
def test_a_secret_cannot_be_reached_by_ANOTHER_SPELLING(project, spelling):
    """Windows strips a trailing dot or space and ignores case, so each of
    these OPENS `.env`. A trailing dot or space is refused as a name
    (400, `clean_relative`, review 2026-10-29); a case spelling reaches the
    secret rule, which runs on the resolved name (403)."""
    store, sid, _, _ = project

    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, spelling)
    if spelling.endswith((".", " ")):
        assert e.value.status == 400 and "dot or a space" in str(e.value)
    else:
        assert e.value.status == 403 and "secret" in str(e.value)


def test_a_secret_cannot_be_reached_by_its_8dot3_name(project):
    store, sid, root, _ = project
    (root / "credentials.json").write_text("{}")
    alias = short_name(root / "credentials.json")
    assert alias.lower() != "credentials.json", "no 8.3 alias on this volume"

    with pytest.raises(files.FileError) as e:
        files.preview(store, sid, alias)
    assert e.value.status == 403 and "secret" in str(e.value)


def test_butlers_records_folder_is_never_listed(project):
    """AC-414 (`docs/data-root.md`): the root project's home holds Butler's
    own records in `.butler`, and the explorer is for the owner's files."""
    store, sid, root, _ = project
    (root / ".butler" / "chats").mkdir(parents=True)

    got = files.list_level(store, sid, "")

    assert ".butler" not in names(got)
    with pytest.raises(files.FileError):
        files.list_level(store, sid, ".butler")
