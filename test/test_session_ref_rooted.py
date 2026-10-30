"""A Butler-minted session reference resolves against the store's CURRENT root.

`docs/e2e-journeys.md`'s test-setup note, 2026-09-26: a chat record stored
`sessionRef` as an ABSOLUTE path (`E:\\PROJECTS\\butler\\state\\chats\\chat-...
-pi.session.json`). Copying `state/` anywhere -- a scratch daemon's own
`BUTLER_STATE`, a backup restore, a moved repo, the second Butler on the
laptop -- left the copy pointing at the ORIGINAL directory, so a turn on the
copy appended to the original's agent memory and a rewind on the copy RENAMED
the original's session file.

Butler MINTS that path from the chat id, the harness and the rewind generation
(`ChatStore.session_ref`), so the directory in it was redundant state that
went stale. The record now stores the FILE NAME only and the store joins it
onto its own root at use time. Legacy records are re-rooted at boot when the
name is one Butler would have minted for that record; a path that is not is
CLEARED, loudly, and never followed. An opaque native-resume id (AC-44,
`caps.native_resume`) is not a path and passes through untouched.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import chats                                        # noqa: E402


def _hashes(root: Path) -> dict[str, str]:
    """Every file under `root`, by relative path, with its SHA-256."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _record(root: Path, chat_id: str) -> dict:
    return json.loads((root / f"{chat_id}.json").read_text(encoding="utf-8"))


def _write_record(root: Path, chat: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{chat['id']}.json").write_text(json.dumps(chat), encoding="utf-8")


def _msg(role: str, content: str, mid: str) -> dict:
    return {"id": mid, "role": role, "content": content, "timestamp": 1}


def _original_with_memory(tmp_path: Path) -> tuple[Path, str]:
    """A store with one spoken conversation and a pi session file beside it."""
    root = tmp_path / "original" / "chats"
    store = chats.ChatStore(root)
    cid = store.current()["id"]
    store.append(cid, "user", "first question")
    store.append(cid, "assistant", "first answer")
    ref = Path(store.session_ref(store.current(), "pi"))
    # Refuse to write anywhere but the store: under a red run or a mutation
    # `session_ref` can hand back a bare name, and writing that resolves
    # against the CWD -- three such files once landed in the repo root.
    assert ref.parent == root, f"session_ref left the store: {ref}"
    ref.write_text('{"agent": "remembers the first question"}',
                   encoding="utf-8")
    return root, cid


def test_a_copied_store_resolves_its_session_file_inside_the_copy(tmp_path):
    root, cid = _original_with_memory(tmp_path)
    copy = tmp_path / "copy" / "chats"
    shutil.copytree(root, copy)

    store = chats.ChatStore(copy)
    ref = Path(store.session_ref(store.current(), "pi"))

    assert ref.parent == copy, (
        f"the copy's conversation resolves its agent memory OUTSIDE the "
        f"copy, so a turn here writes into the original: {ref}")
    assert ref.name == f"{cid}-pi.session.json"


def test_the_record_stores_a_file_name_and_never_a_directory(tmp_path):
    root, cid = _original_with_memory(tmp_path)

    stored = _record(root, cid)["sessionRef"]

    assert stored == f"{cid}-pi.session.json", (
        f"the record carries a directory, which goes stale the moment "
        f"`state/` is copied or moved: {stored!r}")


def test_a_rewind_in_a_copy_leaves_the_original_byte_identical(tmp_path):
    root, cid = _original_with_memory(tmp_path)
    copy = tmp_path / "copy" / "chats"
    shutil.copytree(root, copy)
    before = _hashes(root)

    store = chats.ChatStore(copy)
    store.append(cid, "user", "second question")
    done = store.rewind(store.messages()[-1]["id"])

    assert _hashes(root) == before, (
        "a rewind on the COPY renamed or rewrote files in the ORIGINAL store")
    assert (copy / f"{done['snapshot']}-pi.session.json").exists(), (
        "the copy's own agent memory did not move onto its snapshot")
    assert _record(copy, done["snapshot"])["sessionRef"] == (
        f"{done['snapshot']}-pi.session.json")


def test_a_legacy_absolute_reference_is_re_rooted_at_boot(tmp_path, capsys):
    root = tmp_path / "chats"
    plain = {"id": "chat-1000000000000-aaaaaa", "createdAt": 1,
             "updatedAt": 1000, "harness": "pi", "sessionGen": 0,
             "sessionRef": "E:\\PROJECTS\\butler\\state\\chats\\"
                           "chat-1000000000000-aaaaaa-pi.session.json",
             "messages": [_msg("user", "old", "msg-1")]}
    rewound = {"id": "chat-2000000000000-bbbbbb", "createdAt": 2,
               "updatedAt": 2000, "harness": "pi", "sessionGen": 1,
               "sessionRef": "C:\\somewhere\\else\\"
                             "chat-2000000000000-bbbbbb-pi-r1.session.json",
               "messages": [_msg("user", "newer", "msg-2")]}
    _write_record(root, plain)
    _write_record(root, rewound)

    store = chats.ChatStore(root)

    assert _record(root, plain["id"])["sessionRef"] == (
        "chat-1000000000000-aaaaaa-pi.session.json")
    assert _record(root, rewound["id"])["sessionRef"] == (
        "chat-2000000000000-bbbbbb-pi-r1.session.json")
    assert "re-rooted" in capsys.readouterr().out, (
        "a migration of the owner's records happened silently")
    live = store.for_turn(rewound["id"])
    assert Path(store.session_ref(live, "pi")) == (
        root / "chat-2000000000000-bbbbbb-pi-r1.session.json")


def test_a_path_butler_did_not_mint_is_cleared_and_never_followed(
        tmp_path, capsys):
    """Not re-rooted (the name is not this record's), not kept (it would be
    a write outside the store), not a refusal of the record (D-1: the words
    survive). Cleared loudly -- `clear_session_ref`'s meaning -- so the next
    child is re-seeded with the transcript (AC-193)."""
    root = tmp_path / "chats"
    outside = tmp_path / "outside" / "someone-elses.session.json"
    outside.parent.mkdir()
    outside.write_text('{"not": "Butler\'s"}', encoding="utf-8")
    before = _hashes(outside.parent)
    chat = {"id": "chat-1000000000000-cccccc", "createdAt": 1,
            "updatedAt": 1000, "harness": "pi", "sessionGen": 0,
            "sessionRef": str(outside),
            "messages": [_msg("user", "q", "msg-1"),
                         _msg("assistant", "a", "msg-2"),
                         _msg("user", "q2", "msg-3")]}
    _write_record(root, chat)

    store = chats.ChatStore(root)

    assert _record(root, chat["id"])["sessionRef"] == ""
    assert "not one Butler minted" in capsys.readouterr().out
    assert store.needs_seed(), (
        "the cleared reference is not re-seeded, so the agent silently "
        "forgets the conversation")
    store.rewind("msg-3")
    store.session_ref(store.current(), "pi")
    assert _hashes(outside.parent) == before, (
        "a file outside the store was moved or written")


def test_an_opaque_native_resume_id_is_untouched(tmp_path):
    """AC-44: an id the harness handed back is not a path Butler can root."""
    root = tmp_path / "chats"
    chat = {"id": "chat-1000000000000-dddddd", "createdAt": 1,
            "updatedAt": 1000, "harness": "claude", "sessionGen": 0,
            "sessionRef": "3f2a9c1e-7b44-4d0e-9a51-0c6f1e2d8b77",
            "messages": [_msg("user", "q", "msg-1")]}
    _write_record(root, chat)

    store = chats.ChatStore(root)

    assert _record(root, chat["id"])["sessionRef"] == chat["sessionRef"]
    assert store.session_ref(store.current(), "claude") == chat["sessionRef"]
    assert not store.needs_seed()
