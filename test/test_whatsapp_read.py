"""WhatsApp read tools (docs/messaging.md MS-7, MS-8, phase M4).

AC-508 (read gate: off, the four tools are not offered anywhere and the
routes refuse; on, they answer through a READ-ONLY open), AC-509 (a name
matching zero or several chats returns candidates and NO jid), and the
holding-together of the three places the tools are declared:
`daemon/whatsapp.py` (endpoint brain), `daemon/adapters/pi_memory.ts` (pi)
and the spawn spec that tells the extension which to keep.

Against a fixture archive built with the sidecar's schema
(`test/assets/wa_archive.py`). Nothing here touches WhatsApp or the owner's
records.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from assets import wa_archive                                 # noqa: E402
from conftest import StubResolver                             # noqa: E402
from daemon import butlerd                                    # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon import whatsapp as wa                             # noqa: E402
from daemon.adapters.brain_endpoint import EndpointBrain      # noqa: E402
from daemon.adapters.harness_pi import (MEMORY_EXTENSION,     # noqa: E402
                                        PiHarness)
from daemon.chats import ChatStore                            # noqa: E402
from daemon.ports import TurnContext                          # noqa: E402
from daemon.settings import Settings                          # noqa: E402
from test_listeners import _Three, _hub                       # noqa: E402

URL = "https://127.0.0.1:8815"
NOW = int(time.time())
BUDGET_S = 15.0


@pytest.fixture
def db(tmp_path) -> Path:
    return wa_archive.build(tmp_path / "whatsapp.db", NOW)


# --------------------------------------------------------------------------- #
# The store's read API, ported (MS-7, AC-509)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["ana", "Ana", "ANA"])
def test_a_name_matching_several_contacts_resolves_to_no_jid(db, name):
    """AC-509. Three contacts match, the wanted one is not first: a resolver
    that took the first match would answer a jid here."""
    with wa.Archive(db) as a:
        got = a.resolve_chat(name)
    assert got["jid"] is None
    assert {c["jid"] for c in got["candidates"]} == {
        wa_archive.ANA_SILVA, wa_archive.ANA_COSTA, wa_archive.ANABELA}


def test_a_name_matching_nothing_has_no_jid_and_no_candidates(db):
    with wa.Archive(db) as a:
        assert a.resolve_chat("zzz") == {"jid": None, "name": None,
                                         "candidates": []}


@pytest.mark.parametrize("given,jid", [
    ("bruno", wa_archive.BRUNO),
    ("Anabela", wa_archive.ANABELA),            # exact, yet also in "ana"
    ("family", wa_archive.FAMILY),              # a group: no contact row
    ("+1 (555) 000-0003", wa_archive.BRUNO),    # a number in any shape
    (wa_archive.LID, wa_archive.LID),
])
def test_a_unique_match_resolves(db, given, jid):
    with wa.Archive(db) as a:
        assert a.resolve_chat(given)["jid"] == jid


def test_the_archive_is_opened_read_only(db):
    """AC-508: `mode=ro`, so even a bug in a reader cannot write."""
    before = db.read_bytes()
    with wa.Archive(db) as a:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            a.con.execute("DELETE FROM messages")
    for name, args in (("whatsapp_list_chats", {}),
                       ("whatsapp_read_chat", {"chat": "bruno"}),
                       ("whatsapp_search_messages", {"query": "dinner"}),
                       ("whatsapp_list_contacts", {"query": "a"})):
        wa.read_tool(db, name, args)
    assert db.read_bytes() == before


def test_read_chat_is_newest_first_and_names_the_sender(db):
    got = wa.read_tool(db, "whatsapp_read_chat", {"chat": "bruno", "count": 3})
    lines = got["text"].splitlines()
    assert lines[0] == "Bruno Lima, newest first:"
    assert lines[1].endswith("Bruno: bring the charger")
    assert lines[2].endswith("You: ok see you")
    assert lines[3].endswith("Bruno: dentist at 3pm tomorrow")
    assert len(lines) == 4 and got["jid"] == wa_archive.BRUNO


def test_read_chat_on_an_ambiguous_name_answers_candidates_not_messages(db):
    got = wa.read_tool(db, "whatsapp_read_chat", {"chat": "ana"})
    assert got["jid"] is None and len(got["candidates"]) == 3
    assert got["text"].startswith('"ana" matches 3 chats. Say which one:')
    assert "beach photo" not in got["text"]


def test_list_search_and_contacts(db):
    chats = wa.read_tool(db, "whatsapp_list_chats", {"limit": 2})["text"]
    assert chats.splitlines()[0] == "2 chats:"
    assert "Family Group [group]" in chats
    groups = wa.read_tool(db, "whatsapp_list_chats",
                          {"groups_only": True})["text"]
    assert groups.startswith("1 chats:\n- Family Group [group]")
    hits = wa.read_tool(db, "whatsapp_search_messages",
                        {"query": "DINNER"})["text"]
    assert hits.startswith("2 matches:")     # case-folded, newest first
    assert hits.index("dinner sunday") < hits.index("old dinner plan")
    contacts = wa.read_tool(db, "whatsapp_list_contacts",
                            {"query": "lima"})["text"]
    assert contacts == f"1 contacts:\n- Bruno Lima -- {wa_archive.BRUNO}"
    none = wa.read_tool(db, "whatsapp_search_messages", {"query": "qqq"})
    assert none["text"] == 'No stored message contains "qqq".'


def test_bad_arguments_are_refused_by_name(db):
    for name, args, word in (
            ("whatsapp_read_chat", {}, "chat"),
            ("whatsapp_read_chat", {"chat": "bruno", "count": 0}, "count"),
            ("whatsapp_list_chats", {"limit": "9"}, "limit"),
            ("whatsapp_search_messages", {"query": " "}, "query")):
        with pytest.raises(wa.ToolRefused, match=word):
            wa.read_tool(db, name, args)


def test_no_archive_yet_says_so(tmp_path):
    got = wa.read_tool(tmp_path / "none.db", "whatsapp_list_chats", {})
    assert got["text"] == wa.EMPTY_ARCHIVE


def test_the_python_wrappers_equal_the_sidecars():
    """`whatsapp-store.js` WRAPPER_KEYS and `tools/wa.py` WRAPPERS must stay
    equal (the store's own comment): a wrapper one side peels and the
    other does not reads a different message."""
    js = (REPO / "daemon" / "engines" / "whatsapp" /
          "whatsapp-store.js").read_text(encoding="utf-8")
    block = re.search(r"const WRAPPER_KEYS = \[(.*?)\];", js, re.S).group(1)
    keys = tuple(re.findall(r"'(\w+)'", block))
    src = (REPO / "tools" / "wa.py").read_text(encoding="utf-8")
    py = re.search(r"WRAPPERS = \((.*?)\)", src, re.S).group(1)
    assert tuple(re.findall(r'"(\w+)"', py)) == keys


# --------------------------------------------------------------------------- #
# The gate (AC-508): offered only while `whatsapp_read` is on
# --------------------------------------------------------------------------- #
def _prefs(tmp_path, **values) -> Settings:
    prefs = Settings(tmp_path / "settings.json")
    for k, v in values.items():
        prefs.set(k, v)
    return prefs


def test_offered_follows_the_two_switches(tmp_path):
    prefs = _prefs(tmp_path)
    assert wa.offered_names(prefs) == ()
    prefs.set("whatsapp_read", True)
    assert wa.offered_names(prefs) == wa.READ_NAMES
    prefs.set("whatsapp_enabled", True)       # whatsapp_send defaults on
    assert wa.offered_names(prefs) == wa.READ_NAMES + wa.SEND_NAMES
    prefs.set("whatsapp_send", False)
    assert wa.offered_names(prefs) == wa.READ_NAMES


async def _collect(stream):
    return [i async for i in stream]


@pytest.mark.asyncio
async def test_the_endpoint_brain_offers_nothing_while_reading_is_off(
        tmp_path, db, fake_openai):
    agent = wa.AgentTools(_prefs(tmp_path), db, tmp_path,
                          forbidden=(tmp_path / ".butler",), ask=None)
    brain = EndpointBrain(StubResolver(fake_openai.base_url),
                          whatsapp_tools=agent.offered,
                          whatsapp_call=agent.call)
    fake_openai.verdict(route="converse")
    fake_openai.answer("Hi.")
    await asyncio.wait_for(_collect(brain.turn("hi", TurnContext(
        turn_id=1, mode="secretary"))), BUDGET_S)
    assert all("tools" not in a.body for a in fake_openai.asks)


@pytest.mark.asyncio
async def test_the_endpoint_brain_offers_and_runs_a_read(tmp_path, db,
                                                         fake_openai):
    prefs = _prefs(tmp_path, whatsapp_read=True)
    agent = wa.AgentTools(prefs, db, tmp_path,
                          forbidden=(tmp_path / ".butler",), ask=None)
    brain = EndpointBrain(StubResolver(fake_openai.base_url),
                          whatsapp_tools=agent.offered,
                          whatsapp_call=agent.call)
    fake_openai.verdict(route="converse")
    fake_openai.answer(tool_calls=[{
        "id": "c1", "name": "whatsapp_read_chat",
        "arguments": json.dumps({"chat": "ana"})}])
    fake_openai.answer("Which Ana?")
    await asyncio.wait_for(_collect(brain.turn("what did ana say", TurnContext(
        turn_id=1, mode="secretary"))), BUDGET_S)
    first, second = fake_openai.asks[1].body, fake_openai.asks[2].body
    assert "tools" not in fake_openai.asks[0].body, "the router got tools"
    assert [t["function"]["name"] for t in first["tools"]] == list(
        wa.READ_NAMES)
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"].startswith('"ana" matches 3 chats')
    # A switch reaches the next request, not the next boot.
    prefs.set("whatsapp_read", False)
    fake_openai.verdict(route="converse")
    fake_openai.answer("Ok.")
    await asyncio.wait_for(_collect(brain.turn("hi", TurnContext(
        turn_id=2, mode="secretary"))), BUDGET_S)
    assert "tools" not in fake_openai.asks[-1].body


def test_build_hub_hands_the_endpoint_brain_the_two_callables(tmp_path,
                                                              monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    converse = hub.brain._converse
    assert converse._wa_tools() == ()
    hub.settings.set("whatsapp_read", True)
    assert [t["function"]["name"] for t in converse._wa_tools()] == list(
        wa.READ_NAMES)


# --------------------------------------------------------------------------- #
# pi: the spec names the tools, the extension keeps only those (AC-508)
# --------------------------------------------------------------------------- #
def _spec(tmp_path, tools, **values):
    prefs = _prefs(tmp_path, harness_cwd=str(tmp_path), harness_tools=tools,
                   **values)
    return butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"), (), memory_url=URL)()


def test_reading_off_leaves_the_spec_as_it_was(tmp_path):
    spec = _spec(tmp_path, "read_only")
    assert spec.memory_url == URL
    assert not any(t.startswith("whatsapp_") for t in spec.tools)


def test_reading_on_names_the_tools_on_the_url_and_the_allow_list(tmp_path):
    spec = _spec(tmp_path, "read_only", whatsapp_read=True)
    assert spec.memory_url == URL + "?whatsapp=" + ",".join(wa.READ_NAMES)
    assert spec.tools[-4:] == wa.READ_NAMES
    argv = PiHarness()._argv(spec)          # passes the cmd refusal (AC-120)
    assert argv[argv.index("--butler-url") + 1] == spec.memory_url
    full = _spec(tmp_path, "full", whatsapp_read=True, whatsapp_enabled=True)
    assert full.tools == ()
    assert full.memory_url.endswith(",whatsapp_send")


def test_the_extension_declares_the_same_tools_as_the_daemon():
    ts = MEMORY_EXTENSION.read_text(encoding="utf-8")
    for tool in wa.READ_TOOLS + wa.SEND_TOOLS:
        fn = tool["function"]
        assert f'"{fn["name"]}"' in ts, fn["name"]
        for param in fn["parameters"]["properties"]:
            assert re.search(rf"\b{param}: \{{", ts), (fn["name"], param)
    assert "/api/whatsapp/read/" in ts and '"/api/whatsapp/send"' in ts
    listed = re.search(r"const WHATSAPP_TOOLS = \[(.*?)\];", ts, re.S)
    assert tuple(re.findall(r'"(\w+)"', listed.group(1))) == wa.TOOL_NAMES


GATE = """
import(process.argv[1]).then(async (m) => {
  const tools = []; const handlers = {}; let active = null;
  const pi = {
    registerFlag() {}, registerTool(t) { tools.push(t.name); },
    getFlag(k) { return k === "butler-url" ? process.argv[2] || undefined : undefined; },
    on(e, f) { handlers[e] = f; },
    getActiveTools() { return ["read", ...tools]; },
    setActiveTools(a) { active = a; },
  };
  m.default(pi);
  await handlers.session_start({}, {});
  console.log(JSON.stringify(active));
});
"""


@pytest.mark.parametrize("url,kept", [
    (URL, []),
    (URL + "?whatsapp=whatsapp_read_chat", ["whatsapp_read_chat"]),
    ("", []),
    (URL + "?whatsapp=" + ",".join(wa.READ_NAMES), list(wa.READ_NAMES)),
])
def test_the_extension_deactivates_every_tool_the_url_does_not_name(url, kept):
    """AC-508 inside pi, with a stand-in for pi's extension API. Measured
    against real pi 0.87.1 at build: `setActiveTools` at session_start leaves
    only the named WhatsApp tools active, with and without `--tools`."""
    node = shutil.which("node")
    assert node, "node is not on PATH"
    out = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "-e", GATE,
         MEMORY_EXTENSION.as_uri(), url],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    active = json.loads(out.stdout.strip().splitlines()[-1])
    assert [t for t in active if t.startswith("whatsapp_")] == kept
    assert "read" in active and "memory_search" in active


# --------------------------------------------------------------------------- #
# The routes: loopback only (AC-541), gated (AC-508)
# --------------------------------------------------------------------------- #
ROUTE = "/api/whatsapp/read/whatsapp_read_chat"


@pytest.mark.asyncio
async def test_the_read_routes_answer_this_machine_only(tmp_path,
                                                         monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    hub.settings.set("whatsapp_read", True)
    wa_archive.build(tmp_path / "whatsapp" / "whatsapp.db", NOW)
    async with _Three(hub, monkeypatch) as three:
        async with three.http.post(three.url(hub_mod.PROXIED, ROUTE),
                                   headers=await three.who(hub_mod.PROXIED),
                                   json={"chat": "bruno"}) as r:
            body = await r.json()
            assert r.status == 403 and "bruno" not in json.dumps(body)
        async with three.http.post(three.url(hub_mod.LOCAL, ROUTE),
                                   json={"chat": "bruno"}) as r:
            body = await r.json()
            assert r.status == 200 and body["jid"] == wa_archive.BRUNO
            assert "bring the charger" in body["text"]


@pytest.mark.asyncio
async def test_the_read_routes_refuse_while_reading_is_off_and_never_guess(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    wa_archive.build(tmp_path / "whatsapp" / "whatsapp.db", NOW)
    async with _Three(hub, monkeypatch) as three:
        async with three.http.post(three.url(hub_mod.LOCAL, ROUTE),
                                   json={"chat": "bruno"}) as r:
            body = await r.json()
            assert r.status == 403 and "reading is off" in body["error"]
            assert "charger" not in json.dumps(body)
        hub.settings.set("whatsapp_read", True)
        async with three.http.post(three.url(hub_mod.LOCAL, ROUTE),
                                   json={"chat": "ana"}) as r:
            body = await r.json()
            assert r.status == 200 and body["jid"] is None
            assert len(body["candidates"]) == 3
        async with three.http.post(three.url(hub_mod.LOCAL, ROUTE),
                                   json={"chat": ""}) as r:
            assert r.status == 400
        async with three.http.post(
                three.url(hub_mod.LOCAL, "/api/whatsapp/read/nope"),
                json={}) as r:
            assert r.status == 404
