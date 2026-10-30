"""Butler knows Butler (docs/butler-help.md, AC-641..AC-655).

The always-on feature index, `butler_help` on both brains, the drift gate
that makes a feature unshippable without the agent being told about it, and
the live state read per call. Each test names its criterion and the failure
it catches.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from conftest import StubResolver                             # noqa: E402
from daemon import artifacts, butlerd, dialogue, guide        # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon import library, memory, settings                  # noqa: E402
from daemon import whatsapp as wa                             # noqa: E402
from daemon.adapters.brain_endpoint import EndpointBrain      # noqa: E402
from daemon.adapters.harness_pi import MEMORY_EXTENSION       # noqa: E402
from daemon.guide_features import EXCLUDED_GROUPS, FEATURES   # noqa: E402
from daemon.ports import TurnContext                          # noqa: E402
from test_artifacts import _harness_hub                       # noqa: E402
from test_listeners import _Three                             # noqa: E402
from test_memory_v2 import TURN_BUDGET_S, _collect, _persona  # noqa: E402

LANGUAGES = ("english", "portuguese", "spanish", "french", "german")


# --------------------------------------------------------------------------- #
# The table and its drift gate (AC-646, AC-647, AC-653)
# --------------------------------------------------------------------------- #
def test_every_settings_group_is_covered_or_excluded_with_a_reason():
    """AC-646. Fails when a settings group ships that the agent is never
    told about."""
    covered = {g for f in FEATURES for g in f.groups}
    for key in settings.GROUPS:
        assert key in covered or EXCLUDED_GROUPS.get(key, "").strip(), key
    unknown = (covered | set(EXCLUDED_GROUPS)) - set(settings.GROUPS)
    assert not unknown, f"no such settings group: {sorted(unknown)}"


def test_every_offered_tool_is_covered_by_a_feature():
    """AC-647. Read off each module's own TOOL_NAMES, never a list written
    here: a new tool is a failure until the table names it."""
    covered = {t for f in FEATURES for t in f.tools}
    offered = (memory.TOOL_NAMES + library.TOOL_NAMES + wa.TOOL_NAMES
               + artifacts.TOOL_NAMES + guide.TOOL_NAMES)
    assert set(offered) <= covered, sorted(set(offered) - covered)
    assert covered <= set(offered), sorted(covered - set(offered))


def test_every_phrase_the_guide_says_to_speak_is_one_the_daemon_matches():
    """AC-653: checked against the matchers themselves, and the phrase
    really appears in the detail it is attached to."""
    pairs = [(f, m, p) for f in FEATURES for m, p in f.say]
    assert pairs, "no spoken command is described"
    for f, matcher, phrase in pairs:
        assert matcher.startswith("spoken_"), matcher
        assert getattr(settings, matcher)(phrase), (f.key, matcher, phrase)
        assert phrase.casefold() in f.detail.casefold(), (f.key, phrase)


def test_the_table_is_well_formed():
    keys = [f.key for f in FEATURES]
    assert len(keys) == len(set(keys)), keys
    states = {"brain_mode", "harness_tools", "whatsapp", "channels",
              "recipes", "automations", "ambient", "quiet", "speak",
              "listening", "devices"}
    for f in FEATURES:
        assert re.fullmatch(r"[a-z][a-z_]*", f.key), f.key
        assert f.index and f.detail and f.name, f.key
        assert set(f.state) <= states, (f.key, f.state)
        text = f.name + f.index + f.detail
        assert text.isascii(), f.key
        assert not any(w in text.casefold() for w in LANGUAGES), f.key


# --------------------------------------------------------------------------- #
# The always-on block (AC-641..AC-643, AC-654)
# --------------------------------------------------------------------------- #
def test_the_block_is_capped_and_names_every_feature():
    """AC-642: paid on every turn."""
    block = guide.block()
    assert len(block) <= guide.BLOCK_CAP, len(block)
    for f in FEATURES:
        assert f"- {f.name}: {f.index}" in block, f.key


def test_the_block_holds_no_state():
    """AC-643: it takes no input at all, so no setting can change it and
    a held pi child is never respawned by it."""
    assert inspect.signature(guide.block).parameters == {}
    assert guide.block() == guide.block()


def test_the_policy_holds_the_five_rules_and_names_no_language():
    """AC-654 (BH-4)."""
    p = guide.POLICY
    for rule in ("at most one per reply", "never mid-task",
                 "already on or in use", "call butler_help",
                 "never guess steps",
                 "memory_write", "Never claim a feature"):
        assert rule in p, rule
    assert not any(w in p.casefold() for w in LANGUAGES)


def test_the_block_rides_with_the_rules_and_only_there(tmp_path):
    """AC-641: after the reminder rules, before the index; absent where the
    tools are absent."""
    store, persona = _persona(tmp_path, True)
    store.write("tea", "Owner takes his tea black.")
    blocks = persona.blocks()
    at = blocks.index(dialogue.REMINDER_RULES)
    assert blocks[at + 1] == dialogue.RECIPE_RULES
    assert blocks[at + 2] == guide.block()
    assert blocks[at + 3].startswith(dialogue.MEMORY_HEADING)
    store2, bare = _persona(tmp_path / "b", False)
    assert bare.blocks() == ()


# --------------------------------------------------------------------------- #
# help_text (AC-644, AC-645)
# --------------------------------------------------------------------------- #
STATE = {"brain_mode": "harness", "harness_tools": "read_only",
         "whatsapp": "off (service off); reading off, sending off",
         "channels": "none set up", "recipes": "none yet",
         "automations": "none yet", "ambient": "off", "quiet": "off",
         "speak": "spoken replies on", "listening": "microphone listening on",
         "devices": "phone link port 8443"}


def test_no_topic_answers_every_feature_with_its_state():
    """AC-644."""
    text = guide.help_text(None, STATE)
    for f in FEATURES:
        assert f"- {f.key} ({f.name})" in text, f.key
        for k in f.state:
            assert STATE[k] in text, (f.key, k)
    covered = {k for f in FEATURES for k in f.state}
    assert covered == set(STATE), set(STATE) - covered


def test_a_topic_answers_its_detail_state_and_settings_blurbs():
    """AC-645, by key and by name; an unknown topic lists the topics."""
    for f in FEATURES:
        for asked in (f.key, f.name.upper()):
            text = guide.help_text(asked, STATE)
            assert f.detail in text, asked
            for g in f.groups:
                blurb = settings.GROUPS[g].blurb
                if blurb:
                    assert blurb in text, (asked, g)
    # A settings group's own name finds the feature that covers it.
    for key, group in settings.GROUPS.items():
        owner = next(f for f in FEATURES if key in f.groups)
        assert owner.detail in guide.help_text(group.label, STATE), key
    quiet = guide.help_text("quiet hours", STATE)
    assert "Starts, Ends" in quiet and "always answered" in quiet
    text = guide.help_text("teleport", STATE)
    assert "no Butler feature" in text and FEATURES[0].key in text


# --------------------------------------------------------------------------- #
# The endpoint brain (AC-648)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_endpoint_brain_offers_and_routes_butler_help(
        tmp_path, fake_openai):
    store, persona = _persona(tmp_path, True)
    asked = []

    async def helper(topic):
        asked.append(topic)
        return "Recipes: say run and the name."
    brain = EndpointBrain(StubResolver(fake_openai.base_url),
                          persona_for=persona.blocks, memory=store,
                          guide=helper)
    fake_openai.verdict(route="converse")
    fake_openai.answer(tool_calls=[{"id": "c1", "name": "butler_help",
                                    "arguments": json.dumps(
                                        {"topic": "recipes"})}])
    fake_openai.answer("You can save that as a recipe.")
    await asyncio.wait_for(_collect(brain.turn(
        "I ask for this report every week", TurnContext(
            turn_id=1, mode="secretary"))), TURN_BUDGET_S)
    first, second = fake_openai.asks[1].body, fake_openai.asks[2].body
    names = [t["function"]["name"] for t in first["tools"]]
    assert names[-1] == "butler_help" and "memory_search" in names
    assert guide.block() in [m["content"] for m in first["messages"]]
    assert asked == ["recipes"]
    assert second["messages"][-1]["content"] == \
        "Recipes: say run and the name."


@pytest.mark.asyncio
async def test_a_brain_without_a_guide_offers_no_new_tool(tmp_path,
                                                         fake_openai):
    store, persona = _persona(tmp_path, True)
    brain = EndpointBrain(StubResolver(fake_openai.base_url),
                          persona_for=persona.blocks, memory=store)
    fake_openai.verdict(route="converse")
    fake_openai.answer("hello")
    await asyncio.wait_for(_collect(brain.turn("hi", TurnContext(
        turn_id=1, mode="secretary"))), TURN_BUDGET_S)
    names = [t["function"]["name"] for t in fake_openai.asks[1].body["tools"]]
    assert "butler_help" not in names


def test_build_hub_hands_the_endpoint_brain_a_guide(tmp_path, monkeypatch):
    """D-18, the CALLER: the daemon really builds the endpoint brain with
    a guide (a callee test alone stays green if `build_hub` forgets)."""
    from test_listeners import _hub
    hub = _hub(tmp_path, monkeypatch)
    assert hub.brain._converse._guide is not None


# --------------------------------------------------------------------------- #
# The harness (AC-649) and the route (AC-650, AC-651, AC-655)
# --------------------------------------------------------------------------- #
def test_the_extension_declares_butler_help_as_the_daemon_does():
    ts = MEMORY_EXTENSION.read_text(encoding="utf-8")
    (tool,) = guide.TOOLS
    fn = tool["function"]
    assert f'name: "{fn["name"]}"' in ts
    assert fn["description"] in ts
    assert fn["parameters"]["properties"]["topic"]["description"] in ts
    assert '"/api/guide/agent"' in ts


def test_butler_help_joins_the_read_only_allow_list(tmp_path):
    from daemon.chats import ChatStore
    prefs = settings.Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    prefs.set("harness_tools", "read_only")
    spec = butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"), (),
        memory_url="https://127.0.0.1:1/")()
    assert "butler_help" in spec.tools


TOKEN = "987654321:AAHsecretSECRETsecretSECRETsecret_zz-9"


@pytest.mark.asyncio
async def test_the_route_reads_state_per_call_and_never_a_secret(
        tmp_path, monkeypatch):
    """AC-650 (local only), AC-651 (a planted bot token never appears),
    AC-655 (a flipped setting is in the very next answer)."""
    hub = _harness_hub(tmp_path, monkeypatch)
    folder = butlerd.paths_mod.resolve().notify
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "phone.json").write_text(json.dumps({
        "kind": "telegram", "label": "Phone", "bot_token": TOKEN,
        "chat_id": "555000111"}), encoding="utf-8")
    three = _Three(hub, monkeypatch)
    async with three:
        async def ask(kind=hub_mod.LOCAL, **body):
            async with three.http.post(three.url(kind, "/api/guide/agent"),
                                       headers=await three.who(kind),
                                       json=body) as r:
                return r.status, await r.json()
        status, body = await ask(kind=hub_mod.PROXIED)
        assert status == 403
        status, body = await ask()
        assert status == 200
        assert "phone (telegram)" in body["text"]
        assert TOKEN not in body["text"] and "555000111" not in body["text"]
        hub.settings.set("quiet_hours", True)
        _, after = await ask()
        quiet = next(f for f in FEATURES if "quiet" in f.state)
        line = next(x for x in after["text"].splitlines()
                    if x.startswith(f"- {quiet.key} ({quiet.name})"))
        assert "on, " in line.split("Now:")[1], line
        _, one = await ask(topic=quiet.key)
        assert quiet.detail in one["text"]
