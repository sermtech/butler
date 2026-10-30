"""The agent is chosen PER CHAT (docs/chats-and-terminals.md CT-8, AC-771).

A conversation keeps the harness it was born with -- the `harness` setting's
value at its first spawn, recorded by `ChatStore.session_ref` -- across
setting changes and restarts. Proven on the CALLER (D-18): the daemon's
resolver and factory (`butlerd.chat_harness`), and `_Host.ensure`, which is
what actually builds the child for a turn.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import butlerd  # noqa: E402
from daemon import chats as chats_mod  # noqa: E402
from daemon.adapters.harness_claude import ClaudeHarness  # noqa: E402
from daemon.adapters.harness_fake import FakeHarness  # noqa: E402
import harness_replay  # noqa: E402


def _hub(tmp_path, monkeypatch, **settings):
    (tmp_path / "settings.json").write_text(
        json.dumps({"harness": "fake", "harness_cwd": str(tmp_path),
                    # every agent on, unless a test says otherwise
                    # (docs/agent-and-terminal-settings.md SW-1)
                    "chat_agents": ["pi", "claude", "opencode"],
                    **settings}), encoding="utf-8")
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    return butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                           "brain": "harness"}})


def _born(host, chats, words="hello"):
    """A conversation with a message (so it is on disk) and its first spawn
    decision taken, which is where its agent is recorded."""
    chat = chats.current()
    chats.append(chat["id"], "user", words)
    host.desired_for(chat)
    return chat


def test_a_chat_keeps_the_agent_it_was_born_with(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    host = hub.brain.host
    chats = host._chats
    first = _born(host, chats)
    born_spec = host.desired_for(first)
    assert first["harness"] == "fake"
    assert born_spec.session_file.endswith("-fake.session.json")

    hub.settings.set("harness", "claude")

    assert host.desired_for(first) == born_spec, (
        "the setting changed and the old conversation's child would be "
        "replaced: it no longer runs the agent it was born with (AC-771)")
    assert isinstance(host._harness_for(first), FakeHarness)

    second = chats.rotate(None)
    chats.append(second["id"], "user", "a new question")
    assert host.desired_for(second).session_file.endswith(
        "-claude.session.json")
    assert second["harness"] == "claude"
    assert isinstance(host._harness_for(second), ClaudeHarness)

    # A restart reads the records back: the agents are on disk.
    again = chats_mod.ChatStore(chats.root)
    assert again.find(first["id"])["harness"] == "fake"
    assert again.find(second["id"])["harness"] == "claude"
    assert butlerd.chat_harness(hub.settings, again.find(first["id"])) == "fake"


def test_a_record_with_no_agent_takes_the_setting(tmp_path, monkeypatch):
    """Legacy records (all pi chats under a pi setting) and a conversation
    that never spawned have no agent yet."""
    hub = _hub(tmp_path, monkeypatch)
    assert butlerd.chat_harness(hub.settings, {"harness": ""}) == "fake"
    assert butlerd.chat_harness(hub.settings, None) == "fake"
    hub.settings.set("harness", "opencode")
    assert butlerd.chat_harness(hub.settings, {"harness": ""}) == "opencode"
    assert butlerd.chat_harness(hub.settings, {"harness": "pi"}) == "pi"


def test_harness_model_reaches_only_the_settings_agent(tmp_path, monkeypatch):
    """`harness_model` names a model in the SETTING's harness's registry, so
    a chat on another agent gets that agent's own default."""
    hub = _hub(tmp_path, monkeypatch, harness_model="fake/model-1")
    host = hub.brain.host
    chats = host._chats
    on_fake = _born(host, chats)
    assert host.desired_for(on_fake).model == "fake/model-1"
    hub.settings.set("harness", "claude")
    assert host.desired_for(on_fake).model is None, (
        "a model picked from another agent's registry reached this chat's "
        "agent")
    hub.settings.set("harness", "fake")
    assert host.desired_for(on_fake).model == "fake/model-1"


@pytest.mark.asyncio
async def test_the_turns_child_is_the_chats_agent_not_the_settings(
        tmp_path, monkeypatch):
    """The CALLER: `_Host.ensure` builds the child a turn prompts, and it
    must build the conversation's agent after the setting moved on."""
    spawned = harness_replay.install_claude(monkeypatch)
    hub = _hub(tmp_path, monkeypatch, harness="claude")
    host = hub.brain.host
    chat = _born(host, host._chats)
    hub.settings.set("harness", "fake")

    spec = await host.plan(chat)
    await host.ensure(chat, spec)
    try:
        assert isinstance(host._children[chat["id"]].harness, ClaudeHarness)
        assert spawned and spawned[0].argv[0] == "claude"
    finally:
        await host.dispose(chat["id"])


# -- the page is told which agent a chat runs (CT-8, AC-688) ---------------- #
@pytest.mark.asyncio
async def test_chat_payloads_name_the_agent_recorded_or_the_default(
        tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    hub = _hub(tmp_path, monkeypatch, harness="claude")
    chats = hub.brain.host._chats
    born = _born(hub.brain.host, chats)           # recorded: claude
    assert born["harness"] == "claude"
    hub.settings.set("harness", "opencode")
    fresh = chats.rotate(None)                    # never spawned: no record
    assert fresh["harness"] == ""

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        cur = await (await client.get("/api/session/current")).json()
        listing = await (await client.get("/api/chats")).json()
        one = await (await client.get(f"/api/chats/{born['id']}")).json()
        opened = await (await client.post(
            "/api/session/open", json={"id": born["id"]})).json()
    finally:
        await client.close()

    assert (cur["agent"], cur["agent_label"]) == ("opencode", "opencode")
    assert (one["agent"], one["agent_label"]) == ("claude", "Claude Code")
    assert (opened["agent"], opened["agent_label"]) == (
        "claude", "Claude Code")
    rows = {c["id"]: c for c in listing["chats"]}
    assert rows[born["id"]]["agent"] == "claude"
    assert rows[fresh["id"]]["agent"] == "opencode"


def test_an_unknown_agent_is_labelled_as_is():
    rows = butlerd._with_agent(
        type("H", (), {"settings": {"harness": "pi"}})(),
        [{"harness": "mystery"}, {"harness": ""}])
    assert rows[0]["agent_label"] == "mystery"
    assert (rows[1]["agent"], rows[1]["agent_label"]) == ("pi", "pi")


def test_the_page_paints_the_agent_chip_with_textcontent_only():
    import re
    app = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        encoding="utf-8")
    body = re.search(r"function paintChatAgent\(doc\) \{(.*?)^\}", app,
                     re.S | re.M).group(1)
    assert 'textContent = name' in body and "innerHTML" not in body
    assert "agent_label" in body
    assert "harness" not in body, "the page must not name a setting (AC-41)"
    html = (Path(__file__).resolve().parents[1] / "web"
            / "index.html").read_text(encoding="utf-8")
    assert 'id="chatAgent"' in html


# -- choosing the agent from the page (docs/chat-agent-picker.md, option C) -- #

async def _client_for(hub, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_a_new_chat_is_born_on_the_agent_it_names(tmp_path, monkeypatch):
    """AC-774 (AP-1): the birth records the agent, and the FIRST turn's
    child is that agent although the setting names another. An unknown agent
    is a 400 and nothing is minted."""
    spawned = harness_replay.install_claude(monkeypatch)
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    host = hub.brain.host
    client = await _client_for(hub, monkeypatch)
    try:
        before = host._chats.current()["id"]
        r = await client.post("/api/session/new", json={"agent": "nope"})
        assert r.status == 400 and "nope" in (await r.json())["error"]
        assert host._chats.current()["id"] == before
        r = await client.post("/api/session/new", json={"agent": "claude"})
        assert r.status == 200, await r.text()
        born = await r.json()
    finally:
        await client.close()
    chat = host._chats.current()
    assert chat["id"] == born["id"] and chat["harness"] == "claude"
    host._chats.append(chat["id"], "user", "hello")
    spec = await host.plan(chat)
    await host.ensure(chat, spec)
    try:
        assert isinstance(host._children[chat["id"]].harness, ClaudeHarness)
        assert spawned and spawned[0].argv[0] == "claude"
    finally:
        await host.dispose(chat["id"])


@pytest.mark.asyncio
async def test_an_empty_chat_can_change_agent_until_its_first_message(
        tmp_path, monkeypatch):
    """AC-775 (AP-2): while nothing has been said the agent can change;
    after the first message it is a 409 and the record is unchanged."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    chats = hub.brain.host._chats
    cid = chats.current()["id"]
    client = await _client_for(hub, monkeypatch)
    try:
        r = await client.post(f"/api/chats/{cid}/agent",
                              json={"agent": "opencode"})
        assert r.status == 200, await r.text()
        doc = await r.json()
        assert (doc["agent"], doc["agent_label"]) == ("opencode", "opencode")
        assert chats.current()["harness"] == "opencode"
        r = await client.post(f"/api/chats/{cid}/agent",
                              json={"agent": "nope"})
        assert r.status == 400
        r = await client.post("/api/chats/chat-none/agent",
                              json={"agent": "pi"})
        assert r.status == 404
        chats.append(cid, "user", "hello")
        r = await client.post(f"/api/chats/{cid}/agent", json={"agent": "pi"})
        assert r.status == 409, await r.text()
    finally:
        await client.close()
    assert chats.current()["harness"] == "opencode"


@pytest.mark.asyncio
async def test_the_session_offers_the_agents_and_says_what_is_fixed(
        tmp_path, monkeypatch):
    """AC-776 (AP-3): the page is told the agents to offer (labelled ones
    only, in a stable order), the default, whether the agent can still
    change, and whether the model chip applies to this chat (AP-6)."""
    hub = _hub(tmp_path, monkeypatch, harness="claude")
    chats = hub.brain.host._chats
    client = await _client_for(hub, monkeypatch)
    try:
        empty = await (await client.get("/api/session/current")).json()
        await client.post(f"/api/chats/{empty['id']}/agent",
                          json={"agent": "opencode"})
        other = await (await client.get("/api/session/current")).json()
        chats.append(empty["id"], "user", "hello")
        started = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()
    assert [a["name"] for a in empty["agents"]] == ["pi", "claude",
                                                    "opencode"]
    assert empty["agents"][1]["label"] == "Claude Code"
    assert empty["agent_default"] == "claude"
    assert empty["agent_fixed"] is False and started["agent_fixed"] is True
    assert other["agent"] == "opencode"


@pytest.mark.asyncio
async def test_a_past_chat_reports_its_agent_fixed_without_a_file_scan(
        tmp_path, monkeypatch):
    """AP-2 with the simplification of 2026-10-29: a conversation the store
    no longer holds has been spoken in (an empty one is never written), so
    `GET /api/chats/{id}` says its agent is fixed."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    chats = hub.brain.host._chats
    past = chats.current()
    chats.append(past["id"], "user", "hello")
    chats.rotate(None)
    assert not chats.is_open(past["id"])
    client = await _client_for(hub, monkeypatch)
    try:
        doc = await (await client.get(f"/api/chats/{past['id']}")).json()
    finally:
        await client.close()
    assert doc["agent_fixed"] is True



# -- chat agents are switched on in Settings (docs/agent-and-terminal-settings.md)

@pytest.mark.asyncio
async def test_a_fresh_install_offers_only_pi(tmp_path, monkeypatch):
    """AC-790 (SW-1, SW-2): `chat_agents` defaults to pi alone, so the page
    is offered one agent and draws no choice."""
    from daemon import settings as settings_mod
    assert settings_mod.BY_KEY["chat_agents"].default == ["pi"]
    hub = _hub(tmp_path, monkeypatch, harness="pi", chat_agents=["pi"])
    client = await _client_for(hub, monkeypatch)
    try:
        cur = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()
    assert [a["name"] for a in cur["agents"]] == ["pi"]


@pytest.mark.asyncio
async def test_a_switched_off_agent_is_refused_and_nothing_changes(
        tmp_path, monkeypatch):
    """AC-791 (SW-3): the routes enforce the setting; the default agent is
    always allowed (AC-792, SW-4 by construction)."""
    hub = _hub(tmp_path, monkeypatch, harness="opencode", chat_agents=["pi"])
    chats = hub.brain.host._chats
    client = await _client_for(hub, monkeypatch)
    try:
        before = chats.current()["id"]
        r = await client.post("/api/session/new", json={"agent": "claude"})
        assert r.status == 400 and "chat_agents" in (await r.json())["error"]
        assert chats.current()["id"] == before
        cid = chats.current()["id"]
        r = await client.post(f"/api/chats/{cid}/agent",
                              json={"agent": "claude"})
        assert r.status == 400 and chats.current()["harness"] == ""
        r = await client.post(f"/api/chats/{cid}/agent",
                              json={"agent": "opencode"})
        assert r.status == 200, await r.text()
        cur = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()
    assert [a["name"] for a in cur["agents"]] == ["pi", "opencode"]


def test_a_chat_on_a_switched_off_agent_keeps_it(tmp_path, monkeypatch):
    """AC-793 (SW-5, SW-D1): switching an agent off stops NEW chats on it;
    a chat already on it still spawns it."""
    hub = _hub(tmp_path, monkeypatch, harness="claude")
    host = hub.brain.host
    chat = _born(host, host._chats)
    assert chat["harness"] == "claude"
    hub.settings.update({"harness": "fake", "chat_agents": ["pi"]})
    assert butlerd.chat_harness(hub.settings, chat) == "claude"
