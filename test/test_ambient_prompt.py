"""AC-70's last clause: a brain READS what was overheard, and is TOLD which
half is which. `docs/ambient.md` 4 and section 6 step 3.

`test_ambient.py` proves the hub carries the room's words to the turn and
keeps them out of the utterance, the history and the record. This file proves
the other half: that the two brains put those words in front of a model with a
label saying they were overheard and are not the request -- and that they do it
on the WIRE, not merely in a field. "The context was stored" and "the context
is in the prompt the model received" are different claims and only the second
is this criterion (the rule AC-38 set for token caps).

Three things are asserted here and each one is a different failure.

**The label reaches the model.** A model handed two blocks of text with no
labels acts on either, and the reader on the far end of the harness prompt is
a full-capability coding agent in the owner's repository. So the block says it
was overheard, that it is not a request, and that only the request is the
request.

**The utterance is untouched.** The room's words are never merged into what the
owner said: the user message is byte-for-byte the utterance, and the harness
prompt ENDS with the request. The mutation that matters most for this file is
making the two indistinguishable -- concatenating them into one string -- and
both assertions redden on it.

**Empty adds NOTHING.** Ambient ships off (AC-68), so `ctx.overheard` is "" on
almost every turn, and an empty overheard must leave the request exactly as it
was before this feature existed: the same message list, and the same prompt
string. A prompt that gains an empty labelled section changes every turn's
behaviour for a feature nobody turned on.

**The ROUTER is deliberately blind to it**, and that is a decision rather than
an oversight, so it has a test. The router emits `dispatch` verdicts whose
`action.summary` becomes the prompt handed to the coding agent (AC-73), with
`hub._on_ask_confirm` going straight to `brain.proceed()`. A router shown the
room's words can write a summary composed OUT of them, and by the time that
summary reaches the child the label is gone and a remark made to somebody else
in the room is indistinguishable from an instruction. Deciding which slice of
the buffer a request means is explicitly out of scope (`docs/ambient.md` 7).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver                              # noqa: E402
from daemon import butlerd, dialogue                           # noqa: E402
from daemon.adapters.brain_endpoint import (EndpointConverse,  # noqa: E402
                                            REPLY_SYSTEM)
from daemon.adapters.brain_harness import HarnessBrain         # noqa: E402
from daemon.adapters.harness_fake import FakeHarness           # noqa: E402
from daemon.chats import ChatStore                             # noqa: E402
from daemon.ports import TurnContext                           # noqa: E402
from daemon.settings import Settings                           # noqa: E402

TURN_BUDGET_S = 15.0

# The same pair `test_ambient.py` uses, and for the same reason: every
# assertion below is a substring search, so the room's words and the owner's
# words share NO words. An overlap would make "the room leaked into the
# request" and "the request is in the request" the same observation.
OVERHEARD = "tell margaret the shipment slipped to friday"
UTTERANCE = "send that"
# What the router would have made of that utterance on the dispatch route
# (AC-73). It shares no words with either of the two above.
INSTRUCTION = "forward the delivery update"


def _ctx(turn_id=1, overheard="", history=()):
    return TurnContext(turn_id=turn_id, mode="secretary", history=history,
                       overheard=overheard)


async def _drain(stream) -> list:
    async def collect():
        return [intent async for intent in stream]
    return await asyncio.wait_for(collect(), TURN_BUDGET_S)


def _harness_brain(tmp_path, server):
    """A `HarnessBrain` over the fake child, the router on the fake server.

    The router is IN the path on purpose: what the owner runs is the
    composite, and a test that reached past the router could not see whether
    the router was shown the room (the last test in this file).
    """
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    chats = ChatStore(tmp_path / "chats")
    child = FakeHarness()
    brain = HarnessBrain(
        resolver=StubResolver(server.base_url), harness_for=lambda _chat=None: child,
        spec_for=butlerd.harness_spec_resolver(prefs, chats), chats=chats,
        cap_for=lambda: prefs["harness_agents_max"])
    return brain, child, chats


def _labelled(block: str) -> None:
    """The label says which half is which, in words a model can act on."""
    said = block.lower()
    assert "overheard" in said, (
        f"the block never says the text was OVERHEARD, so the model is "
        f"reading a half-minute of a room as though it were addressed to "
        f"Butler (AC-70): {block!r}")
    assert "not a request" in said, (
        f"the block never says the overheard text is not a request, which is "
        f"the whole of AC-70: {block!r}")
    assert "only the request" in said, (
        f"the block never says which half IS the request, so a model has two "
        f"blocks of text and no rule for choosing between them: {block!r}")


# --------------------------------------------------------------------------- #
# endpoint mode -- the conversation model
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_conversation_model_is_told_which_half_is_which(fake_openai):
    """AC-70 on the wire, for `brain_mode=endpoint`."""
    brain = EndpointConverse(StubResolver(fake_openai.base_url))

    await _drain(brain.turn(UTTERANCE, _ctx(overheard=OVERHEARD)))

    messages = fake_openai.last_body()["messages"]
    carrying = [m for m in messages if OVERHEARD in m["content"]]
    assert len(carrying) == 1, (
        f"the room's words reached the model {len(carrying)} times; they "
        f"belong in exactly one labelled block: {messages!r}")
    assert messages[-1] == {"role": "user", "content": UTTERANCE}, (
        f"the utterance the model was asked to answer is not what the owner "
        f"said -- the room was merged into it, so a remark made to somebody "
        f"else is now the request (AC-70): {messages[-1]!r}")
    assert carrying[0] is not messages[-1]
    _labelled(carrying[0]["content"])


@pytest.mark.asyncio
async def test_an_empty_overheard_leaves_the_endpoint_request_untouched(
        fake_openai):
    """AC-68: off is the normal case, and off must cost nothing.

    Spelled out as the exact message list rather than as "no block appeared",
    because the failure to catch is a request that gained an empty section:
    every turn's behaviour changes for a feature nobody turned on, and no
    other test in the suite would see it.
    """
    brain = EndpointConverse(StubResolver(fake_openai.base_url))
    history = (("user", "what time is it"), ("assistant", "Just past four."))

    await _drain(brain.turn(UTTERANCE, _ctx(history=history)))

    assert fake_openai.last_body()["messages"] == [
        {"role": "system", "content": REPLY_SYSTEM},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "Just past four."},
        {"role": "user", "content": UTTERANCE},
    ], ("an empty overheard changed the request: this is the message list "
        "the daemon sent before ambient existed and it must be identical "
        "(AC-68, AC-70)")


# --------------------------------------------------------------------------- #
# harness mode -- the coding agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_coding_agent_is_prompted_with_a_labelled_block(
        tmp_path, fake_openai):
    """AC-70 where it matters most: the prompt that reaches a coding agent.

    The child gets ONE string, so the label is the only thing separating half
    a minute of a room from the sentence the owner actually said. The request
    is LAST and the block is in front of it, so the instruction cannot be read
    as a continuation of the room.
    """
    brain, child, _chats = _harness_brain(tmp_path, fake_openai)

    await _drain(brain.turn(UTTERANCE, _ctx(overheard=OVERHEARD)))

    assert len(child.prompts) == 1, f"one prompt per turn: {child.prompts!r}"
    prompt = child.prompts[0]
    assert prompt.rstrip().endswith(UTTERANCE), (
        f"the prompt does not END with what the owner said, so the room and "
        f"the request are one run of text and the agent cannot tell them "
        f"apart (AC-70): {prompt!r}")
    assert OVERHEARD in prompt, f"the context never reached the child: {prompt!r}"
    assert prompt.index(OVERHEARD) < prompt.rindex(UTTERANCE), (
        f"the room was appended AFTER the request, which reads as more "
        f"instruction: {prompt!r}")
    _labelled(prompt[:prompt.index(OVERHEARD)])


@pytest.mark.asyncio
async def test_an_empty_overheard_prompts_the_child_with_the_utterance_alone(
        tmp_path, fake_openai):
    """AC-68 again, and here byte-identical is literally the string.

    A labelled block wrapped around every prompt would change what a coding
    agent is asked on every turn of every conversation, with ambient off.
    """
    brain, child, _chats = _harness_brain(tmp_path, fake_openai)

    await _drain(brain.turn(UTTERANCE, _ctx()))

    assert child.prompts == [UTTERANCE], (
        f"a turn with nothing overheard was wrapped anyway: {child.prompts!r}")


@pytest.mark.asyncio
async def test_a_dispatched_turn_carries_the_context_and_the_cleaned_request(
        tmp_path, fake_openai):
    """The dispatch route: `hub._on_ask_confirm` -> `proceed` (AC-73, AC-70).

    This is the shortest path in the daemon from a spoken sentence to a
    full-capability agent, and it is the one that has to hold BOTH rules at
    once: the child is prompted with the router's cleaned instruction, and the
    room rides in front of it labelled rather than folded into it.
    """
    brain, child, _chats = _harness_brain(tmp_path, fake_openai)

    await _drain(brain.proceed(UTTERANCE, _ctx(overheard=OVERHEARD),
                               instruction=INSTRUCTION))

    prompt = child.prompts[0]
    assert prompt.rstrip().endswith(INSTRUCTION), (
        f"the cleaned instruction is not what the child was asked last: "
        f"{prompt!r}")
    assert OVERHEARD in prompt and prompt.index(OVERHEARD) < prompt.rindex(
        INSTRUCTION), f"the room is not in front of the request: {prompt!r}"
    _labelled(prompt[:prompt.index(OVERHEARD)])


@pytest.mark.asyncio
async def test_the_room_never_enters_butlers_own_record(tmp_path, fake_openai):
    """AC-70's third negative, at the seam that owns the durable record.

    `chats.py` holds what the owner SAID (D-20). The brain now builds a prompt
    out of the room's words, so the record is one line away from holding them
    too -- and unlike the rolling transcript it never expires.
    """
    brain, _child, chats = _harness_brain(tmp_path, fake_openai)

    await _drain(brain.turn(UTTERANCE, _ctx(overheard=OVERHEARD)))

    said = " ".join(m["content"] for m in chats.messages())
    assert UTTERANCE in said, f"the owner's own words are missing: {said!r}"
    assert OVERHEARD not in said, (
        f"the room was filed as the owner's instruction, permanently "
        f"(AC-70, D-20): {said!r}")


# --------------------------------------------------------------------------- #
# the router is blind to it, on purpose
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_router_is_never_told_what_the_room_said(
        tmp_path, fake_openai):
    """The decision, with the argument in this file's docstring.

    The router's `dispatch` verdict carries a summary that becomes the prompt
    to a coding agent with no label on it. Feeding it the room is the shortest
    path from a stray remark to an action, and nothing in the field ships an
    answer to which slice of the buffer a request meant
    (`.references/ambient-mode.md` 5). The substantive brain gets the context
    labelled instead; the classifier gets the utterance.
    """
    brain, _child, _chats = _harness_brain(tmp_path, fake_openai)

    await _drain(brain.turn(UTTERANCE, _ctx(overheard=OVERHEARD)))

    router_ask = json.dumps(fake_openai.asks[0].body)
    assert UTTERANCE in router_ask, (
        "the first ask was not the router's -- this test is looking at the "
        "wrong request")
    assert OVERHEARD not in router_ask, (
        f"the router was shown the room, so a remark made to somebody else "
        f"can become the `action.summary` a coding agent is prompted with, "
        f"unlabelled (AC-70, AC-73): {router_ask!r}")


def test_an_empty_overheard_builds_no_block():
    """The one rule both callers rest on, asserted where it lives.

    Both prompts above are byte-identical when nothing was overheard only
    because this returns the empty string, and whitespace counts: a decode
    that produced a newline is a quiet room, not context.
    """
    assert dialogue.overheard_note(_ctx()) == ""
    assert dialogue.overheard_note(_ctx(overheard="   \n ")) == ""
    assert OVERHEARD in dialogue.overheard_note(_ctx(overheard=OVERHEARD))
