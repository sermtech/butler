"""The current chat's model by voice (docs/chat-model.md CM-10, AC-787).

"use sonnet" sets THIS chat's model, "back to the default model" clears it,
and both are answered aloud. Matched against the shared favourites first,
then the chat's agent's own catalogue; one match is taken, none and several
are ANSWERED and nothing changes -- never a guess.

What each group would catch:

* THE PARSER: only an utterance that starts with a model phrase is one; a
  sentence that merely mentions a model ("tell me about sonnet") is not, and
  the turn-mode, new-conversation and explicit-project phrases stay theirs.
* THE MATCH: favourites before the catalogue, an exact name before a
  substring, several returned rather than one picked.
* THE HUB: the model reaches the NEXT SPAWN SPEC (`desired_for`), the
  spoken answers, a bare "use X" naming no model falls through to the agent
  as work, and a bare name that is a configured project stays navigation.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from conftest import attach_session                          # noqa: E402
from daemon import settings as settings_mod                  # noqa: E402
from daemon.ports import Final                               # noqa: E402
from test_chat_agent import _hub                             # noqa: E402

BUDGET_S = 15.0
# harness_fake.FAKE_MODELS
SMALL, LARGE = "fakeprovider/fake-small", "fakeprovider/fake-large"


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,name,explicit", [
    ("use sonnet", "sonnet", False),
    ("Use Sonnet.", "sonnet", False),
    ("switch to opus", "opus", False),
    ("use deepseek on this chat", "deepseek", True),
    ("use the deepseek model", "deepseek", True),
    ("change the model to fake large", "fake large", True),
    ("set the model to gpt 5", "gpt 5", True),
    ("switch to model haiku", "haiku", True),
    ("use the default model", "", True),
    ("back to the default model", "", True),
    ("Go back to the default model.", "", True),
    ("use the default model on this chat", "", True),
])
def test_spoken_model_request(said, name, explicit):
    got = settings_mod.spoken_model_request(said)
    assert got is not None, said
    assert (got.name, got.explicit) == (name, explicit)


@pytest.mark.parametrize("said", [
    "tell me about sonnet",
    "I would use sonnet for this",
    "which model is this chat on",
    "use",
    "use the model",
    "switch to long form",          # a turn mode (SPOKEN_MODES)
    "use smart turn",               # a turn mode
    "switch to new chat",           # a new conversation
    "switch to project butler",     # explicit navigation
    "remember that I use sonnet",
])
def test_not_a_model_request(said):
    assert settings_mod.spoken_model_request(said) is None


# --------------------------------------------------------------------------- #
# the match
# --------------------------------------------------------------------------- #
CATALOGUE = ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner",
             "anthropic/claude-sonnet-4-5", "openai/gpt-5.1",
             "openai/gpt-5.1-mini", "sonnet"]


@pytest.mark.parametrize("heard,favourites,found", [
    ("sonnet", [], ["sonnet"]),                         # exact beats substring
    ("deepseek", [], ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]),
    ("deepseek chat", [], ["deepseek/deepseek-chat"]),
    ("Deepseek-Reasoner", [], ["deepseek/deepseek-reasoner"]),
    ("deepseek", ["deepseek/deepseek-chat"], ["deepseek/deepseek-chat"]),
    ("gpt 5.1", [], ["openai/gpt-5.1"]),
    ("mini", [], ["openai/gpt-5.1-mini"]),
    ("seek", [], []),                                   # mid-word is no match
    ("llama", [], []),
    # a favourite the agent does not list is not this agent's model
    ("llama", ["meta/llama-4"], []),
])
def test_match_models(heard, favourites, found):
    assert settings_mod.match_models(heard, favourites, CATALOGUE) == found


def test_spoken_model_names_say_the_tail_unless_tails_collide():
    assert settings_mod.model_spoken_names(
        ["deepseek/deepseek-chat", "openai/gpt-5.1"]) == [
        "deepseek chat", "gpt 5.1"]
    assert settings_mod.model_spoken_names(
        ["deepseek/deepseek-chat", "openrouter/deepseek/deepseek-chat"]) == [
        "deepseek deepseek chat", "openrouter deepseek deepseek chat"]


# --------------------------------------------------------------------------- #
# the hub
# --------------------------------------------------------------------------- #
async def _say(hub, s, text):
    """One spoken final through `_on_final`, playback confirmed the way a
    page does; answers the spoken lines and the text handed to the agent."""
    worked = []

    async def reply(_s, _turn, said):
        worked.append(said)
    hub._reply = reply
    seen = len(s.ws.sent)
    turn = hub._begin_turn(s)

    async def confirm():
        while True:
            if s.audio_ms_sent > 0 and s.turn is not None:
                await hub._playback_done(s, {"turn": s.turn})
            await asyncio.sleep(0.005)
    helper = asyncio.create_task(confirm())
    try:
        await asyncio.wait_for(hub._on_final(s, turn, Final(text=text),
                                             "silence"), BUDGET_S)
        if s.reply_task is not None:
            await asyncio.wait_for(s.reply_task, BUDGET_S)
    finally:
        helper.cancel()
    frames = [json.loads(m) for m in s.ws.sent[seen:] if isinstance(m, str)]
    return [f["text"] for f in frames if f.get("type") == "reply"], worked


def _model_of(hub, chat):
    return hub.brain.host.desired_for(chat).model


@pytest.mark.asyncio
async def test_one_match_sets_this_chats_model_for_the_next_spawn(
        tmp_path, monkeypatch):
    """AC-787: the model reaches the next spawn spec, the setting is
    untouched, the pick lands in the agent's recents, and it is said."""
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model=SMALL)
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    said, worked = await _say(hub, s, "use fake large")
    assert said == ["Fake large from the next reply."]
    assert worked == []
    assert chat["model"] == LARGE
    assert _model_of(hub, chat) == LARGE
    assert hub.settings.get("harness_model") == SMALL
    from daemon import butlerd
    assert butlerd.recent_models()["fake"] == [LARGE]


@pytest.mark.asyncio
async def test_back_to_the_default_model_clears_it(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch, harness="fake", harness_model=SMALL)
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    hub.brain.set_model(chat["id"], LARGE)
    said, _ = await _say(hub, s, "back to the default model")
    assert said == ["The default model from the next reply."]
    assert chat["model"] == ""
    assert _model_of(hub, chat) == SMALL


@pytest.mark.asyncio
async def test_several_matches_are_named_and_nothing_changes(tmp_path,
                                                             monkeypatch):
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    said, worked = await _say(hub, s, "use fake")
    assert said == ["Several match fake; say fake small or fake large."]
    assert worked == [] and chat["model"] == ""


@pytest.mark.asyncio
async def test_a_favourite_answers_before_the_catalogue(tmp_path,
                                                       monkeypatch):
    hub = _hub(tmp_path, monkeypatch, harness="fake",
               model_favourites=[LARGE])
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    said, _ = await _say(hub, s, "switch to fake")
    assert said == ["Fake large from the next reply."]
    assert chat["model"] == LARGE


@pytest.mark.asyncio
async def test_an_unknown_model_is_answered_on_the_explicit_form(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    hub.brain.set_model(chat["id"], SMALL)
    said, worked = await _say(hub, s, "change the model to llama")
    assert said == ["I don't have a model called llama on fake."]
    assert worked == [] and chat["model"] == SMALL


@pytest.mark.asyncio
async def test_the_agent_label_names_the_chats_own_agent(tmp_path,
                                                        monkeypatch):
    """The chat's agent, not the setting's: a Claude Code chat matches
    Claude Code's catalogue and is named by its label."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    s = attach_session(hub)
    chats = hub.brain.host._chats
    chat = chats.current()
    chats.choose_agent(chat["id"], "claude")
    said, _ = await _say(hub, s, "use the llama model")
    assert said == ["I don't have a model called llama on Claude Code."]
    said, _ = await _say(hub, s, "switch to opus")
    assert said == ["Opus from the next reply."]
    assert _model_of(hub, chat) == "opus"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["use tabs instead of spaces",
                                  "tell me about fake large"])
async def test_a_sentence_that_is_not_a_model_choice_is_work(
        tmp_path, monkeypatch, text):
    """A bare "use X" naming no model, and a sentence that merely mentions
    one, reach the agent untouched and change nothing."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    said, worked = await _say(hub, s, text)
    assert worked == [text] and said == []
    assert chat["model"] == ""


@pytest.mark.asyncio
async def test_a_bare_project_name_stays_navigation(tmp_path, monkeypatch):
    """Disjoint by construction: "switch to X" where X is a configured
    project is never a model request, even if a model matches X."""
    hub = _hub(tmp_path, monkeypatch, harness="fake")
    proj = tmp_path / "fake-large"
    proj.mkdir()
    hub.shortcuts.upsert("fl", str(proj))
    asked = settings_mod.spoken_model_request("switch to fake large")
    assert asked is not None and not asked.explicit
    assert hub._names_a_project(asked.name)
    s = attach_session(hub)
    chat = hub.brain.host._chats.current()
    await _say(hub, s, "switch to fake large")
    assert chat.get("model", "") == ""
