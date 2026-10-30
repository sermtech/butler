"""A model per chat (docs/chat-model.md CM-1..CM-9, AC-780..AC-784, AC-788).

One rule: a chat has its own model or none; none follows its agent's
default. Picking sets THIS chat only and never `harness_model`. The proof is
the SPAWN SPEC a turn would get (`_Host.desired_for`), not a stored field.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from daemon import butlerd                                   # noqa: E402
from daemon import ports                                     # noqa: E402
from daemon import settings as settings_mod                  # noqa: E402
from test_chat_agent import _hub, _client_for                # noqa: E402


def _model_of(host, chat):
    return host.desired_for(chat).model


@pytest.mark.asyncio
async def test_a_chats_own_model_reaches_its_spawn_on_any_agent(
        tmp_path, monkeypatch):
    """AC-780 (CM-1, CM-2): the chat's model is on the next spawn even when
    the chat's agent is not the setting's; "" clears it back to the
    agent's default (None on the spec)."""
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model="m-fake")
    host = hub.brain.host
    chats = host._chats
    client = await _client_for(hub, monkeypatch)
    try:
        cid = chats.current()["id"]
        await client.post(f"/api/chats/{cid}/agent", json={"agent": "claude"})
        assert _model_of(host, chats.current()) is None
        r = await client.post(f"/api/chats/{cid}/model",
                              json={"model": "sonnet"})
        assert r.status == 200, await r.text()
        doc = await r.json()
        assert doc["model"] == "sonnet"
        assert _model_of(host, chats.current()) == "sonnet"
        r = await client.post(f"/api/chats/{cid}/model", json={"model": ""})
        assert r.status == 200 and (await r.json())["model"] == ""
        assert _model_of(host, chats.current()) is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_chats_model_changes_nothing_else(tmp_path, monkeypatch):
    """AC-781 (CM-1): the setting is untouched, and another chat on the
    setting's agent still gets the setting's model."""
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model="m-fake")
    host = hub.brain.host
    chats = host._chats
    client = await _client_for(hub, monkeypatch)
    try:
        first = chats.current()
        chats.append(first["id"], "user", "hello")
        r = await client.post(f"/api/chats/{first['id']}/model",
                              json={"model": "m-other"})
        assert r.status == 200, await r.text()
        assert _model_of(host, first) == "m-other"
        second = chats.rotate(None)
        assert _model_of(host, second) == "m-fake"
    finally:
        await client.close()
    assert hub.settings.get("harness_model") == "m-fake"


@pytest.mark.asyncio
async def test_a_started_chats_model_can_change_and_persists(tmp_path,
                                                            monkeypatch):
    """CM-3 / AC-782: unlike the agent, the model changes after the first
    message; the record keeps it, so a reload (a fresh store over the same
    files) still spawns with it. The spec differing is what makes the held
    child be replaced on the next turn (AC-60)."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    host = hub.brain.host
    chats = host._chats
    cid = chats.current()["id"]
    chats.append(cid, "user", "hello")
    before = host.desired_for(chats.current())
    client = await _client_for(hub, monkeypatch)
    try:
        r = await client.post(f"/api/chats/{cid}/model",
                              json={"model": "m-two"})
        assert r.status == 200, await r.text()
    finally:
        await client.close()
    after = host.desired_for(chats.current())
    assert after != before and after.model == "m-two"
    disk = json.loads(next(tmp_path.glob(f"**/{cid}.json")).read_text(
        encoding="utf-8"))
    assert disk["model"] == "m-two"


@pytest.mark.asyncio
async def test_switching_an_empty_chats_agent_clears_its_model(
        tmp_path, monkeypatch):
    """AC-783 (CM-4): a pi model name means nothing to Claude Code."""
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model="m-fake")
    chats = hub.brain.host._chats
    cid = chats.current()["id"]
    client = await _client_for(hub, monkeypatch)
    try:
        await client.post(f"/api/chats/{cid}/model", json={"model": "m-x"})
        r = await client.post(f"/api/chats/{cid}/agent",
                              json={"agent": "claude"})
        doc = await r.json()
        cur = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()
    assert chats.current()["model"] == ""
    assert doc["model"] == "" and doc["model_default"] == ""
    assert cur["model"] == "" and cur["agent"] == "claude"


@pytest.mark.asyncio
async def test_the_session_names_the_chats_model_and_its_default(
        tmp_path, monkeypatch):
    """CM-8: `model` (own or empty) and `model_default` (the setting's
    model when the chat is on the setting's agent, else empty)."""
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model="m-fake")
    client = await _client_for(hub, monkeypatch)
    try:
        cur = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()
    assert (cur["model"], cur["model_default"]) == ("", "m-fake")


@pytest.mark.asyncio
async def test_the_models_route_serves_default_favourites_recent_and_all(
        tmp_path, monkeypatch):
    """AC-784 (CM-7, CM-8, CM-D1): per agent, from its own lister; a pick
    lands at the front of that agent's recents (daemon-wide, max 5)."""
    hub = _hub(tmp_path, monkeypatch, harness="fake",
               model_favourites=["opus", "not-a-claude-model"])
    chats = hub.brain.host._chats
    cid = chats.current()["id"]
    client = await _client_for(hub, monkeypatch)
    try:
        await client.post(f"/api/chats/{cid}/agent", json={"agent": "claude"})
        for m in ("haiku", "sonnet", "haiku"):
            await client.post(f"/api/chats/{cid}/model", json={"model": m})
        doc = await (await client.get("/api/agents/claude/models")).json()
        bad = await client.get("/api/agents/nope/models")
    finally:
        await client.close()
    assert doc["models"] == ["haiku", "sonnet", "opus"]
    assert doc["favourites"] == ["opus"]
    assert doc["recent"] == ["haiku", "sonnet"]
    assert doc["default"] == ""
    assert bad.status == 404


@pytest.mark.asyncio
async def test_a_model_name_cannot_carry_shell_syntax(tmp_path, monkeypatch):
    """The model rides the agent's argv, and pi starts through `cmd /c`:
    a metacharacter is refused and nothing is stored. Unknown chat: 404."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    chats = hub.brain.host._chats
    cid = chats.current()["id"]
    client = await _client_for(hub, monkeypatch)
    try:
        for bad in ("a&calc", "a b", "x|y", 'q"', "x" * 201):
            r = await client.post(f"/api/chats/{cid}/model",
                                  json={"model": bad})
            assert r.status == 400, bad
        r = await client.post("/api/chats/chat-none/model",
                              json={"model": "ok"})
        assert r.status == 404
    finally:
        await client.close()
    assert chats.current()["model"] == ""


def test_the_setting_is_the_default_model_and_not_a_quick_chip():
    """CM-1, CM-D2: `harness_model` is what new chats on the default agent
    start with, edited in Settings; the composer's per-chat chip replaced
    its quick chip."""
    spec = settings_mod.BY_KEY["harness_model"]
    assert spec.label == "Default model"
    assert not spec.quick
