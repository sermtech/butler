"""A conversation with NO MESSAGES is never on disk (the ghost-chat defect).

`docs/e2e-journeys.md` section 4, S19, the owner's report: zero-message rows
labelled "(nothing was said)" piled up in the sidebar. `rotate` WROTE a
fresh empty record at birth, and only a LATER `rotate` reaped it -- every
other way out (opening another chat, deleting the current one) leaked the
file. Reaping on one exit was a patch; this file pins the structural rule
that replaced it: a newborn lives only in memory and `ChatStore._write`
refuses to persist a record whose `messages` is empty, so there is nothing
left behind to reap.

The test is `not messages`, never "zero USER turns": an assistant-only
record (Butler speaking an error into an empty chat) is a real record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import chats                                        # noqa: E402
from test_chats import _hub, _http, _on_disk, _msg              # noqa: E402


def _empty_files(root: Path) -> list[str]:
    """Every conversation record on disk that holds no messages."""
    found = []
    for path in sorted(root.glob("chat-*.json")):
        if path.name.endswith(".session.json"):
            continue
        if not json.loads(path.read_text(encoding="utf-8")).get("messages"):
            found.append(path.name)
    return found


def _ids(store) -> list[str]:
    return [row["id"] for row in store.listing()]


def test_a_fresh_store_writes_nothing_and_still_lists_the_open_chat(
        tmp_path):
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    newborn = store.current()["id"]
    assert list(root.glob("chat-*.json")) == [], (
        "booting on an empty directory wrote a record nobody spoke into")
    assert _ids(store) == [newborn], (
        "the conversation the owner is IN must still be a row, from memory")


def test_new_chat_then_open_another_leaves_no_ghost(tmp_path):
    """The audit's reproduction: New chat, then open another chat."""
    root = tmp_path / "chats"
    past = _on_disk(root, "chat-1000000000000-aaaaaa", 1000,
                    [_msg("user", "an old question")])
    store = chats.ChatStore(root)
    newborn = store.rotate()["id"]
    assert not (root / f"{newborn}.json").exists(), (
        "a New chat was written to disk before anything was said in it")
    assert _ids(store) == [newborn, past]

    assert store.open(past) is not None
    assert _ids(store) == [past], (
        "the chat left behind by New chat is still a row -- a ghost")
    assert _empty_files(root) == []


def test_deleting_the_current_chat_leaves_no_ghost(tmp_path):
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    spoken = store.current()["id"]
    store.append(spoken, "user", "delete me")
    assert store.delete(spoken) is True
    successor = store.current()["id"]
    assert successor != spoken
    assert _empty_files(root) == [] and list(root.glob("chat-*.json")) == []
    assert _ids(store) == [successor]

    # and deleting the unwritten newborn itself is a real delete, not a 404
    assert store.delete(successor) is True
    assert store.current()["id"] != successor
    assert list(root.glob("chat-*.json")) == []


def test_a_restart_mints_no_empty_file(tmp_path):
    root = tmp_path / "chats"
    chats.ChatStore(root)
    chats.ChatStore(root)
    assert list(root.glob("chat-*.json")) == []

    store = chats.ChatStore(root)
    store.append(store.current()["id"], "user", "kept")
    store.rotate()
    chats.ChatStore(root)
    assert _empty_files(root) == []
    assert len(list(root.glob("chat-*.json"))) == 1


def test_the_first_message_persists_the_record_with_its_project(tmp_path):
    root = tmp_path / "chats"
    project = tmp_path / "proj"
    project.mkdir()
    store = chats.ChatStore(root)
    newborn = store.rotate(str(project))["id"]
    store.append(newborn, "user", "hello")
    record = json.loads((root / f"{newborn}.json").read_text(encoding="utf-8"))
    assert record["project"] == str(project)
    assert [m["content"] for m in record["messages"]] == ["hello"]


def test_an_assistant_only_record_is_a_real_record(tmp_path):
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    spoken = store.current()["id"]
    store.append(spoken, "assistant", "I could not reach the model.")
    store.rotate()
    assert (root / f"{spoken}.json").exists(), (
        "Butler's own words were not persisted; the rule is `not messages`, "
        "not zero user turns")
    assert spoken in _ids(store)


def test_boot_removes_existing_empty_records_and_nothing_else(tmp_path,
                                                             capsys):
    root = tmp_path / "chats"
    ghost = _on_disk(root, "chat-1000000000000-aaaaaa", 1000, [])
    (root / f"{ghost}-pi.session.json").write_text("{}", encoding="utf-8")
    real = _on_disk(root, "chat-2000000000000-bbbbbb", 2000,
                    [_msg("user", "real")])
    (root / f"{real}-pi.session.json").write_text("{}", encoding="utf-8")
    butler_only = _on_disk(root, "chat-3000000000000-cccccc", 3000,
                           [_msg("assistant", "an error, spoken")])

    store = chats.ChatStore(root)

    left = sorted(p.name for p in root.iterdir())
    assert left == sorted([f"{real}.json", f"{real}-pi.session.json",
                           f"{butler_only}.json"]), left
    out = capsys.readouterr().out
    assert ghost in out and "no messages" in out, (
        f"an empty record was removed without a loud line naming it: {out}")
    assert store.current()["id"] == butler_only


def test_an_unwritten_newborn_is_reachable_by_every_lookup(tmp_path):
    """find, open, for_turn and session_ref must not assume the file exists."""
    root = tmp_path / "chats"
    past = _on_disk(root, "chat-1000000000000-aaaaaa", 1000,
                    [_msg("user", "old")])
    store = chats.ChatStore(root)
    newborn = store.rotate(str(tmp_path))["id"]

    found = store.find(newborn)
    assert found is not None and found["messages"] == []
    assert found["project"] == str(tmp_path)

    ref = store.session_ref(store.current(), "pi")
    assert ref and not (root / f"{newborn}.json").exists(), (
        "minting a session reference persisted an empty record")

    store.open(past)
    assert store.for_turn(newborn)["id"] == newborn, (
        "a device still SHOWING the newborn must still reach it")
    assert store.open(newborn)["id"] == newborn

    store.append(newborn, "user", "now it exists")
    record = json.loads((root / f"{newborn}.json").read_text(encoding="utf-8"))
    assert str(root / record["sessionRef"]) == ref, record
    assert record["project"] == str(tmp_path)


def test_rewinding_to_the_first_message_leaves_no_empty_file(tmp_path):
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    live = store.current()["id"]
    store.append(live, "user", "first")
    first = store.current()["messages"][0]["id"]
    store.append(live, "assistant", "answer")

    answer = store.rewind(first)

    assert answer["prompt"] == "first"
    assert not (root / f"{live}.json").exists(), (
        "the rewound conversation, now empty, still has a file -- which "
        "holds the OLD messages and would resurrect them on restart")
    assert (root / f"{answer['snapshot']}.json").exists()
    assert _empty_files(root) == []
    assert _ids(store) == [live, answer["snapshot"]]


@pytest.mark.asyncio
async def test_the_routes_serve_a_newborn_and_forget_it_on_leaving(
        tmp_path, fake_openai, monkeypatch):
    root = tmp_path / "chats"
    past = _on_disk(root, "chat-1000000000000-aaaaaa", 1000,
                    [_msg("user", "old")])
    hub, _, _ = _hub(tmp_path, fake_openai)

    async with _http(hub, monkeypatch) as client:
        born = await client.post("/api/session/new", json={})
        assert born.status == 200, await born.text()
        current = await (await client.get("/api/session/current")).json()
        newborn = current["id"]
        listed = await (await client.get("/api/chats")).json()
        assert newborn != past
        assert newborn in [c["id"] for c in listed["chats"]]

        opened = await client.post("/api/session/open", json={"id": past})
        assert opened.status == 200, await opened.text()
        listed = await (await client.get("/api/chats")).json()

    assert [c["id"] for c in listed["chats"]] == [past]
    assert _empty_files(root) == []
