"""`scripts/migrate-data-root.py`: legacy `state/` into a data root.

`docs/data-root.md`, AC-417..AC-419. Every test builds a FAKE legacy layout
under `tmp_path` and migrates THAT -- never the owner's `state/` and never
`E:\\Butler-Data`.
"""
import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from daemon import shortcuts as shortcuts_mod  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "migrate_data_root", REPO / "scripts" / "migrate-data-root.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def legacy(tmp_path):
    """A repository-shaped source: state/, ssl/, butler.log."""
    src = tmp_path / "repo"
    state = src / "state"
    (state / "chats").mkdir(parents=True)
    (state / "memory").mkdir()
    (state / "attachments").mkdir()
    (src / "ssl").mkdir()
    site = tmp_path / "SITE"
    site.mkdir()
    att = state / "attachments" / "att-1-abc.png"
    att.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (state / "settings.json").write_text('{"speak": true}', encoding="utf-8")
    (state / "mode.json").write_text('{"mode": "secretary"}', encoding="utf-8")
    (state / "voice-trace.log").write_text("trace\n", encoding="utf-8")
    (state / "memory" / "2026-09-26-note.md").write_text("gate code\n",
                                                         encoding="utf-8")
    (state / "shortcuts.json").write_text(json.dumps({
        "migrated": True, "migrated_projects": True,
        "rows": {
            "s-1-code": {"path": str(src), "label": "butler", "chords": {}},
            "s-2-site": {"path": str(site), "label": "SITE",
                           "chords": {"claude": "ctrl+numpad2"}}}}),
        encoding="utf-8")
    (state / "chats" / "chat-1-aaaaaa.json").write_text(json.dumps({
        "id": "chat-1-aaaaaa", "project": str(src),
        "sessionRef": "chat-1-aaaaaa-pi.session.json",
        "messages": [{"role": "user",
                      "text": f"look at {att} please"}]}), encoding="utf-8")
    (state / "chats" / "chat-1-aaaaaa-pi.session.json").write_text(
        f'{{"said": "{att.as_posix()}"}}', encoding="utf-8")
    (src / "ssl" / "cert.pem").write_text("CERT", encoding="utf-8")
    (src / "ssl" / "key.pem").write_text("KEY", encoding="utf-8")
    (src / "butler.log").write_text("log\n", encoding="utf-8")
    return src


def run(src, data, *extra, port=None):
    return mig.main(["--source", str(src), "--data", str(data),
                     "--port", str(port or free_port()), *extra])


def test_everything_is_copied_to_its_new_place_and_the_source_is_untouched(
        legacy, tmp_path):
    """AC-417: records into <data>/.butler, logs into .butler/logs, the cert
    into .butler/ssl, attachments beside the owner's files -- COPIED."""
    before = tree_hash(legacy)
    data = tmp_path / "Butler-Data"

    assert run(legacy, data) == 0

    rec = data / ".butler"
    for rel in ("settings.json", "mode.json", "shortcuts.json",
                "memory/2026-09-26-note.md", "chats/chat-1-aaaaaa.json",
                "chats/chat-1-aaaaaa-pi.session.json",
                "ssl/cert.pem", "ssl/key.pem",
                "logs/voice-trace.log", "logs/butler.log"):
        assert (rec / rel).is_file(), f"{rel} was not copied"
    assert (rec / "mode.json").read_bytes() == \
        (legacy / "state" / "mode.json").read_bytes()
    assert (data / "attachments" / "att-1-abc.png").read_bytes() == \
        b"\x89PNG\r\n\x1a\nfake"
    assert not (rec / "attachments").exists()
    assert tree_hash(legacy) == before, "the source was modified"


def test_the_root_moves_to_the_data_root_and_the_repo_becomes_a_code_row(
        legacy, tmp_path):
    """AC-418: every chat's project path still names a row, the old root's
    chats stay under "butler (code)", and the new root is the data root."""
    data = tmp_path / "Butler-Data"
    assert run(legacy, data) == 0

    store = shortcuts_mod.ShortcutStore(data / ".butler" / "shortcuts.json")
    by_path = {shortcuts_mod.same_dir(r.path): r for r in store.all()}
    assert by_path[shortcuts_mod.same_dir(str(legacy))].label == "butler (code)"
    assert by_path[shortcuts_mod.same_dir(str(data))].label == "Butler"
    assert by_path[shortcuts_mod.same_dir(str(tmp_path / "SITE"))].chords == {
        "claude": "ctrl+numpad2"}
    store.migrate_projects(data / ".butler" / "settings.json", data)
    assert store.get(store.root_id()).path == str(data)
    chat = json.loads((data / ".butler" / "chats" / "chat-1-aaaaaa.json")
                      .read_text(encoding="utf-8"))
    assert shortcuts_mod.same_dir(chat["project"]) in by_path


def test_stored_attachment_paths_in_butlers_records_follow_the_file(
        legacy, tmp_path):
    """AC-417: Butler's own chat record is rewritten; the AGENT's session
    file is its memory and is left as it was."""
    data = tmp_path / "Butler-Data"
    assert run(legacy, data) == 0

    chat = (data / ".butler" / "chats" / "chat-1-aaaaaa.json").read_text(
        encoding="utf-8")
    assert str(data / "attachments" / "att-1-abc.png").replace("\\", "\\\\") \
        in chat
    assert "state" not in json.loads(chat)["messages"][0]["text"]
    session = data / ".butler" / "chats" / "chat-1-aaaaaa-pi.session.json"
    assert session.read_bytes() == (
        legacy / "state" / "chats" / "chat-1-aaaaaa-pi.session.json"
    ).read_bytes()


def test_a_starter_agents_md_is_written_only_when_absent(legacy, tmp_path):
    data = tmp_path / "Butler-Data"
    assert run(legacy, data) == 0
    text = (data / "AGENTS.md").read_text(encoding="utf-8")
    assert "About this home folder" in text
    for word in ("language", "Portuguese", "English"):
        assert word not in text, f"language.md D2: the starter names {word!r}"

    (data / "AGENTS.md").write_text("mine", encoding="utf-8")
    assert run(legacy, data) == 0
    assert (data / "AGENTS.md").read_text(encoding="utf-8") == "mine"


def test_it_never_overwrites_a_file_already_in_the_data_root(legacy, tmp_path):
    """AC-419, the robocopy /XC /XN /XO rule: a destination that exists is
    the NEWER truth (the new daemon may have written it)."""
    data = tmp_path / "Butler-Data"
    (data / ".butler").mkdir(parents=True)
    (data / ".butler" / "mode.json").write_text('{"mode": "newer"}',
                                                encoding="utf-8")

    assert run(legacy, data) == 0
    assert (data / ".butler" / "mode.json").read_text(encoding="utf-8") == \
        '{"mode": "newer"}'


def test_a_second_run_changes_nothing(legacy, tmp_path):
    """AC-419: idempotent."""
    data = tmp_path / "Butler-Data"
    assert run(legacy, data) == 0
    first = tree_hash(data)
    assert run(legacy, data) == 0
    assert tree_hash(data) == first


def test_dry_run_writes_nothing(legacy, tmp_path, capsys):
    data = tmp_path / "Butler-Data"
    assert run(legacy, data, "--dry-run") == 0
    assert not data.exists()
    out = capsys.readouterr().out
    assert "settings.json" in out and "DRY RUN" in out


def test_it_refuses_while_a_daemon_listens(legacy, tmp_path):
    """AC-419: copying a store the live daemon is writing is a torn copy.
    The port is checked, and nothing is killed."""
    data = tmp_path / "Butler-Data"
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert run(legacy, data, port=port) == 3
    assert not data.exists()


def test_it_refuses_a_data_root_inside_the_source(legacy):
    assert run(legacy, legacy / "inside") == 2
    assert not (legacy / "inside").exists()
