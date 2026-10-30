"""The router's cleaned instruction reaches the harness. AC-73, AC-28.

The router already understands the utterance better than the transcript does:
its system prompt says the words were DICTATED and may carry false starts and
mis-hearings, and a `dispatch` verdict answers with `action.summary` -- what
was MEANT. Until AC-73 that summary reached exactly one place, the line spoken
when no brain can do the work ("I would need to X, but I cannot do that yet"),
and the coding agent was prompted with the raw transcript. The router's
understanding was discarded precisely where it was worth having
(`docs/ambient.md` section 5).

So the claim here is observable and it is asserted where the defect lives: on
`harness.prompts`, the fake's record of every string a child was actually
prompted with. Asserting on an intermediate variable would prove the daemon
computed something, which was never in doubt -- it computed it before, too.

Three properties, and the second and third are what keep the fix honest:

* the prompt DIFFERS from the transcript when the router cleaned it;
* the RECORD still shows what the owner said. `hub.history` feeds the next
  turn's router and `chats.py` is what the page redisplays, and a paraphrase
  filed as the owner's words is D-20's context desync arriving through the
  front door (AC-64);
* an absent or blank summary prompts with the transcript rather than with
  nothing. That is the router declining to rewrite, not a fallback: plan.md
  3.8 already routes an unusable verdict to the conversation model instead of
  to an error.

Every case runs several turns. What was added is an argument rather than
state, but "the cleaned instruction leaked into the next question" is the
failure a one-turn test cannot see (test/AGENTS.md).
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, StubResolver, attach_session)   # noqa: E402
from daemon import hub as hub_mod, ports                        # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain          # noqa: E402
from daemon.butlerd import harness_spec_resolver                # noqa: E402
from daemon.chats import ChatStore                              # noqa: E402
from daemon.modes import ModeStore                              # noqa: E402
from daemon.ports import (Action, AskConfirm, Done, Final)      # noqa: E402
from daemon.settings import Settings                            # noqa: E402

TURN_BUDGET_S = 20.0

# The utterance as DICTATED, and what the router made of it. They differ in
# the only way that matters: one is a prompt and the other is a recording of
# somebody talking.
DICTATED = "uh can you um rename the the endpointing module i mean"
CLEANED = "rename the endpointing module"


def harness_hub(tmp_path, server, brain_cls=HarnessBrain):
    """A hub whose brain drives a (fake) coding agent, on a fake router.

    The router is IN the path: the claim is about what the composite does with
    a verdict, and a test that reached past the router would be asserting
    about itself. `QuietTts` says the words and makes no audio, so no turn
    waits out the playback gate.
    """
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    harness = ports.build(ports.HARNESSES, "fake")
    chats = ChatStore(tmp_path / "chats")
    hub = hub_mod.Hub(
        asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
        brain=brain_cls(resolver=StubResolver(server.base_url),
                        harness_for=lambda _chat=None: harness,
                        spec_for=harness_spec_resolver(prefs, chats),
                        chats=chats,
                        cap_for=lambda: prefs["harness_agents_max"]),
        modes=ModeStore(tmp_path / "mode.json"),
        asr_config=ports.AsrConfig(model="test"), settings=prefs)
    return hub, attach_session(hub), harness, chats


async def utterance(hub, s, turn: int, text: str) -> None:
    """One whole utterance through `_on_final`, where the daemon's own
    branches live -- including `_on_ask_confirm`, which is the only route from
    a routed instruction to the coding agent."""
    s.turn = turn
    await asyncio.wait_for(hub._on_final(s, turn, Final(text=text), "silence"),
                           TURN_BUDGET_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), TURN_BUDGET_S)


# --------------------------------------------------------------------------- #
# AC-73 -- what reaches the child is what was MEANT
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_cleaned_instruction_is_what_reaches_the_harness(
        fake_openai, tmp_path):
    """The defect, at the seam it was found at (docs/ambient.md 5).

    Turn 1 is the criterion itself: a `dispatch` verdict whose summary differs
    from the transcript, and the child must be prompted with the summary.

    Turn 2 is `converse`, and it is the assertion with teeth for the shape of
    the fix: a converse verdict carries no summary, so its prompt is the raw
    utterance. A cleaned instruction remembered on the adapter -- the obvious
    wrong implementation -- passes turn 1 and prompts turn 2 with turn 1's
    words.

    Turn 3 is a second dispatch with a DIFFERENT summary, because "the last
    summary wins" is the other sticky-state failure and it survives turn 2.
    """
    hub, s, harness, _chats = harness_hub(tmp_path, fake_openai)
    fake_openai.verdict(route="dispatch", action={"summary": CLEANED,
                                                  "writes": True})
    await utterance(hub, s, 1, DICTATED)

    assert harness.prompts == [CLEANED], (
        f"the coding agent was prompted with the raw transcript instead of "
        f"the router's cleaned instruction (AC-73, AC-28): {harness.prompts}")
    assert harness.prompts[0] != DICTATED, (
        "the fixture no longer distinguishes cleaned from raw, so this test "
        "cannot fail: pick a summary that differs from the transcript (D-13)")

    fake_openai.verdict(route="converse")
    await utterance(hub, s, 2, "what does it do now")

    assert harness.prompts[-1] == "what does it do now", (
        f"a converse turn was prompted with something other than what was "
        f"said -- a cleaned instruction outlived its turn: {harness.prompts}")

    fake_openai.verdict(route="dispatch",
                        action={"summary": "run the test suite"})
    await utterance(hub, s, 3, "and then uh run the the test suite")

    assert harness.prompts[-1] == "run the test suite", (
        f"the second dispatch was prompted with the first turn's instruction "
        f"or with its own transcript: {harness.prompts}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "a dispatched turn did not re-arm (D-1, AC-2)")


@pytest.mark.asyncio
async def test_the_record_holds_what_was_SAID_not_the_paraphrase(
        fake_openai, tmp_path):
    """AC-64, D-20: cleaning the PROMPT must not rewrite the transcript.

    Two stores, and they are different claims (D-18). `hub.history` is the
    rolling window the NEXT turn's router reads, so a paraphrase there is fed
    back into the thing that produced it; `chats.py` is what the page
    redisplays, so a paraphrase there shows the owner words they never said.

    Turn 2 is what proves the first turn's history entry is honest where it is
    actually read: the router's own request body carries it.
    """
    hub, s, harness, chats = harness_hub(tmp_path, fake_openai)
    fake_openai.verdict(route="dispatch", action={"summary": CLEANED})
    await utterance(hub, s, 1, DICTATED)

    assert ("user", DICTATED) in list(hub.history), (
        f"the rolling transcript recorded the router's paraphrase as the "
        f"owner's words (AC-64, D-20): {list(hub.history)}")
    said = [m["content"] for m in chats.messages() if m["role"] == "user"]
    assert said == [DICTATED], (
        f"Butler's durable record shows the paraphrase rather than the "
        f"utterance (AC-35, D-20): {said}")

    fake_openai.verdict(route="converse")
    await utterance(hub, s, 2, "what did you change")

    router_bodies = [a.body for a in fake_openai.asks
                     if a.body.get("response_format", {}).get(
                         "type") == "json_schema"]
    turn2 = [m["content"] for m in router_bodies[-1]["messages"]
             if m["role"] == "user"]
    assert DICTATED in turn2, (
        f"the next turn's router was told the owner said the paraphrase: "
        f"{turn2}")
    assert CLEANED not in turn2, (
        f"the cleaned instruction leaked into the conversation the router "
        f"reads: {turn2}")
    assert harness.prompts[0] == CLEANED, (
        "the record assertions above are vacuous unless the prompt was "
        "actually cleaned (D-13)")


# --------------------------------------------------------------------------- #
# The router declining to rewrite is not a fallback (plan.md 3.8)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_dispatch_with_no_summary_prompts_with_the_utterance(
        fake_openai, tmp_path):
    """A verdict that named work and then did not say what it was.

    The schema cannot make `summary` non-empty (llama.cpp drops what would say
    so, D-11), so this arrives as an ordinary verdict and `Dialogue._intents`
    treats it exactly like one that would not parse: the turn falls through to
    the conversation half with the raw utterance (plan.md 3.8). What must
    never happen is the child being prompted with the empty string.

    Turn 2 is an ordinary dispatch, because a fall-through that poisoned the
    next turn would be the parked-confirmation defect wearing a new hat.
    """
    hub, s, harness, _chats = harness_hub(tmp_path, fake_openai)
    fake_openai.verdict(route="dispatch", action={"summary": "   "})
    await utterance(hub, s, 1, DICTATED)

    assert harness.prompts == [DICTATED], (
        f"a dispatch carrying no instruction did not fall through to the "
        f"utterance itself (plan.md 3.8): {harness.prompts}")

    fake_openai.verdict(route="dispatch", action={"summary": CLEANED})
    await utterance(hub, s, 2, DICTATED)

    assert harness.prompts[-1] == CLEANED, (
        f"the turn after an empty summary was not cleaned: "
        f"{harness.prompts}")


class BlankProposalBrain(HarnessBrain):
    """A brain that PROPOSES work without naming it.

    `ports.Action` permits an empty summary and `brain_fake` emits `AskConfirm`
    freely, so `hub._on_ask_confirm` can be handed one -- the router path
    cannot produce it today, which is exactly why the guard needs a witness
    that is not the router (D-18). A swapped-in agent framework is the caller
    this is really about.
    """

    async def turn(self, text, ctx):
        yield AskConfirm(Action(summary="   "))
        yield Done("reflex")


@pytest.mark.asyncio
async def test_a_blank_proposal_never_prompts_the_child_with_nothing(
        fake_openai, tmp_path):
    """An empty prompt is a turn the agent cannot answer and the owner cannot
    diagnose: the room hears the harness saying nothing came back, and nothing
    in the log says why. Two turns, because a guard that fires once and then
    latches is the defect D-1 is about."""
    hub, s, harness, _chats = harness_hub(tmp_path, fake_openai,
                                          brain_cls=BlankProposalBrain)

    await utterance(hub, s, 1, DICTATED)
    await utterance(hub, s, 2, "and stop when the tests pass")

    assert harness.prompts == [DICTATED, "and stop when the tests pass"], (
        f"a proposal that named no work prompted the coding agent with "
        f"something other than the utterance: {harness.prompts}")
