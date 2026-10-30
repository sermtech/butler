"""Harness mode, on the CALLER side. docs/harness-mode.md 7.4, plan.md 4.4c.

The conformance suite proves an adapter HOLDS the Harness contract. This file
proves the daemon USES it (D-18): that the router still runs in front, that a
turn always makes a sound (AC-47), that a multi-turn answer is announced ONCE
(AC-48), that barge-in reaches the child, that a child is HELD across turns
that completed and replaced when the spec changes (AC-59, AC-60), and that a
failed turn is replayed once from the verbatim input before anything is said
about it (plan.md 4.4c).

These assertions have now been inverted TWICE, and the record of that is the
point. They began as "a routine turn starts nothing" (a child held open from
boot); on 2026-08-18 the owner replaced AC-46 with one child per message and
they became "a turn starts exactly one child and leaves none behind"; on
2026-08-19 the owner reversed that after living with it, and AC-59 makes them
"a turn REUSES the held child, and only a changed spec replaces it". Every
version is read off the same instrument -- the fake's record of every spec it
was started with, which fails however fast a spawn is -- so what changed each
time is the expected count and never the witness. The PID half of the same
claim, against a real child, is at the bottom and is marked `slow`.

Nothing here waits without a deadline. The failure this port is most likely to
have is an ending that never arrives, and a test that hangs on it reports
nothing at all -- no output, no exit code (test/AGENTS.md).
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver, pi_kwargs, skip_without_pi   # noqa: E402
from daemon import butlerd, ports                               # noqa: E402
from daemon.adapters.brain_harness import (HarnessBrain,        # noqa: E402
                                           HarnessConverse)
from daemon.adapters.harness_fake import (DEFAULT_SCRIPT,  # noqa: E402
                                         FakeHarness, ScriptedTurn)
from daemon.ports import (Done, Say, TextDelta, TurnContext,     # noqa: E402
                          WorkerEvent)
from daemon.chats import ChatStore                        # noqa: E402
from daemon.settings import Settings                            # noqa: E402

TURN_BUDGET_S = 15.0


def _prefs(tmp_path, projects=(), **overrides) -> Settings:
    """Settings, plus the PROJECT LIST the spec resolver gates against.

    The list is no longer a setting: it is the shortcuts pane's rows since
    2026-09-11 (the owner's backlog item 4), so it is carried beside the store
    rather than in it and handed to `harness_spec_resolver` as a callable.
    Kept on this helper so every caller here still writes one line.
    """
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    # This file's own concern predates docs/filing-and-pacing.md's per-
    # PROJECT limit and several fixtures here share ONE `harness_cwd`
    # across conversations that are meant to coexist -- raised to the
    # setting's own maximum (10, review 2026-09-16: was 50, which the
    # schema no longer allows), the same fix `test_parallel_agents.py`'s
    # own `_prefs` carries and for the same reason.
    prefs.set("project_agent_limit", 10)
    for key, value in overrides.items():
        prefs.set(key, value)
    prefs.project_paths = tuple(projects)
    return prefs


def _resolver(tmp_path, prefs, chats):
    """The daemon's own spec resolver, built from one `prefs`.

    The project list is no longer a setting -- the daemon reads
    `ShortcutStore.paths` since 2026-09-11 (backlog item 4) -- so in this file
    it rides on the Settings object and is unpacked here, and every fixture
    below still passes one thing around.
    """
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    return butlerd.harness_spec_resolver(
        prefs, chats, getattr(prefs, "project_paths", ()))


def _brain(tmp_path, server, harness=None, prefs=None, **kwargs):
    """A HarnessBrain over the fake harness, with the router on a fake server.

    The router IS in the path -- every test here pays a verdict -- because the
    thing being asserted is what the composite does, and a test that reached
    past the router would say nothing about the mode the owner actually runs.

    `harness_for` hands back the SAME fake every turn on purpose. Production
    builds a new adapter per message (`butlerd.harness_factory`), but the
    fake's `specs`/`prompts`/`disposals` records are the only evidence AC-46
    can be asserted on, and a factory that minted a new object per turn would
    throw that record away between the two turns the criterion is about.
    """
    child = harness if harness is not None else FakeHarness(**kwargs)
    # The daemon's OWN resolver, not a stub: `harness_cwd` reaching the spec is
    # the claim these tests make, and a hand-built spec would prove it about
    # the test instead (AC-7b).
    # Materialised HERE rather than inside `_resolver`, because the agent cap
    # is read off the same store (AC-237): two `Settings` objects would let a
    # test raise the cap on one and have the child spawned against the other.
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    brain = HarnessBrain(
        resolver=StubResolver(server.base_url), harness_for=lambda _chat=None: child,
        spec_for=_resolver(tmp_path, prefs, chats),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])
    return brain, child


async def _turn(brain, text="what does the endpointing module do", turn_id=1,
                budget: float = TURN_BUDGET_S):
    """One whole turn's intents, on a deadline.

    `budget` exists because a LIVE turn is a different instrument from a
    scripted one: a real pi pays a 1.5-2.3 s spawn and then a model's own
    time, and the fake pays neither. A caller that wrapped this in a longer
    `wait_for` would be advertising a deadline the INNER one always wins --
    a live turn slower than 15 s would fail rather than being covered, which
    is a test lying about what it measures (D-13).
    """
    async def collect():
        ctx = TurnContext(turn_id=turn_id, mode="secretary")
        return [i async for i in brain.turn(text, ctx)]
    return await asyncio.wait_for(collect(), budget)


def _said(intents) -> list[str]:
    """The sentences spoken, WITHOUT the block separator a piece may lead
    with: that is layout for the page (speech.drain_sentences), and
    `speech.split_sentences` trims it before synthesis. The break itself is
    asserted on the record, in
    `test_text_either_side_of_a_tool_call_is_two_paragraphs`."""
    return [i.text.strip() for i in intents
            if isinstance(i, Say) and i.spoken]


def _dones(intents) -> list:
    return [i for i in intents if isinstance(i, Done)]


def _answers(intents) -> list[str]:
    """What was said MINUS the reflex line, which every harness turn opens
    with. Without this split "the turn said something" is trivially true and
    AC-47 stops being a claim about the answer (D-13)."""
    return [t for t in _said(intents) if t != HarnessConverse.NARRATION]


# --------------------------------------------------------------------------- #
# AC-47 -- a harness turn is never silent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_no_stream_turn_is_still_spoken(tmp_path, fake_openai):
    """The measured provider that emits ZERO text deltas (section 1).

    `claudebridge-local` put its whole answer in the turn's message and
    streamed nothing. A brain that mapped only `text_delta` would have spoken
    nothing at all there, and a turn that makes no sound is indistinguishable
    from a dead microphone.
    """
    brain, harness = _brain(tmp_path, fake_openai, stream=False)

    intents = await _turn(brain)

    said = _answers(intents)
    assert said, f"a no-delta turn said nothing but the reflex line: {intents}"
    assert "It holds three policies and one of them is not wired up." in said, (
        f"the turn_end text was never recovered: {said}")
    assert len(_dones(intents)) == 1, f"expected exactly one Done: {intents}"


@pytest.mark.asyncio
async def test_what_the_room_HEARD_is_what_the_record_KEEPS(tmp_path,
                                                            fake_openai):
    """AC-47's other half, and it was missing for as long as AC-47 existed.

    The test above asserts what a non-streaming provider SAID. Nothing
    asserted what it KEPT -- and until 2026-09-11 it kept nothing at all:
    `answered` was appended to only inside the TextDelta branch, so a provider
    that streams no deltas spoke a whole answer and recorded the empty string.
    MEASURED before the fix: 95 characters spoken, 0 recorded. The page drew
    the answer live and the same conversation reopened showed the question with
    "no reply came back for this turn" under it.

    That is D-18 in its usual shape -- the callee was green and the caller's
    record was empty -- which is why this asserts on the STORE rather than on
    the intents. `_brain` does not hand back its chat store, so this builds its
    own the way the harness-only helper does.
    """
    from daemon.adapters.brain_harness import HarnessOnlyBrain

    child = FakeHarness(stream=False)
    chats = ChatStore(tmp_path / "chats")
    prefs = _prefs(tmp_path)
    brain = HarnessOnlyBrain(
        harness_for=lambda _chat=None: child,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"])

    intents = await _turn(brain)
    await brain.dispose()

    said = _answers(intents)
    assert said, f"nothing was said, so this proves nothing: {intents}"
    kept = [m.get("content") or "" for m in chats.current()["messages"]
            if m.get("role") == "assistant"]
    assert kept and kept[0].strip(), (
        f"the room heard {said!r} and the record kept {kept!r} -- a "
        f"conversation reopened later shows the question with no answer")
    for piece in said:
        assert piece.strip() in kept[0], (
            f"{piece!r} was spoken and is not in the record {kept[0]!r}")


@pytest.mark.asyncio
async def test_a_streamed_turn_is_not_spoken_twice(tmp_path, fake_openai):
    """The other direction, and it is the one the recovery can break.

    A fallback that fires whenever `turn_end` carries text would speak every
    sentence a second time on every streaming provider -- the answer read out,
    then read out again -- which is worse than the silence it exists to
    prevent.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    said = _said(await _turn(brain))

    assert said.count(
        "It holds three policies and one of them is not wired up.") == 1, (
        f"the streamed answer was spoken and then repeated from turn_end: "
        f"{said}")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False], ids=["stream", "no-stream"])
async def test_text_either_side_of_a_tool_call_is_two_paragraphs(
        tmp_path, fake_openai, stream):
    """The e2e audit's glue (docs/e2e-journeys.md), asserted on the STORE.

    The default script is two agent turns with tools before each, so the
    answer is two text blocks. They reached the record as "...module.It
    holds" on a real pi child. The break must survive on both paths into the
    record: the streamed deltas, and the `turn_end` recovery a non-streaming
    provider takes (AC-47) -- and the SPOKEN pieces must still be the
    sentences, with the break riding as leading whitespace that
    `speech.split_sentences` trims before synthesis.
    """
    from daemon.adapters.brain_harness import HarnessOnlyBrain

    child = FakeHarness(stream=stream)
    chats = ChatStore(tmp_path / "chats")
    prefs = _prefs(tmp_path)
    brain = HarnessOnlyBrain(
        harness_for=lambda _chat=None: child,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"])

    intents = await _turn(brain)
    await brain.dispose()

    kept = [m.get("content") or "" for m in chats.current()["messages"]
            if m.get("role") == "assistant"]
    assert len(kept) == 1, kept
    first, second = (t.text for t in DEFAULT_SCRIPT)
    assert first in kept[0] and second in kept[0], kept
    between = kept[0].split(first, 1)[1].split(second, 1)[0]
    assert "\n\n" in between, (
        f"the two text blocks were joined with {between!r}: {kept[0]!r}")
    assert [p.strip() for p in _answers(intents)] == [first, second], intents


@pytest.mark.asyncio
async def test_a_turn_that_produces_no_text_at_all_still_says_something(
        tmp_path, fake_openai):
    """AC-47's third exit: the fixed line. Silence is the one wrong answer."""
    brain, harness = _brain(
        tmp_path, fake_openai, script=(ScriptedTurn(""),))

    intents = await _turn(brain)

    assert _answers(intents) == [HarnessConverse.NOTHING_CAME_BACK], intents
    assert len(_dones(intents)) == 1


# --------------------------------------------------------------------------- #
# the router is still in front (4.4)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_router_still_runs_in_front(tmp_path, fake_openai):
    """Harness mode replaces the BACK half of the dialogue, not the routing.

    A trivia question answered by the router must never reach a tool-enabled
    child: it is slower, it costs the harness's context, and it is the whole
    reason the composite exists rather than a second brain with its own copy
    of the route-to-intent mapping.

    Since 2026-08-18 it also proves something sharper. The reflex line and the
    spawn both live in `HarnessConverse.turn`, so a router answer must produce
    NEITHER -- a daemon that said "working on it" and spawned a coding agent
    before deciding the router could answer would pay 1.5-2.3 s for every
    trivia question.
    """
    brain, harness = _brain(tmp_path, fake_openai)
    await brain.warm()
    # AFTER the warm, which spends a real verdict of its own (dialogue.Router
    # checks the grammar path at boot rather than trusting /health).
    fake_openai.verdict(route="answer", answer="It is four o'clock.")

    intents = await _turn(brain, text="what time is it")

    assert _said(intents) == ["It is four o'clock."], intents
    assert harness.prompts == [], (
        f"the router answered and the harness was prompted anyway: "
        f"{harness.prompts}")
    assert harness.specs == [], (
        f"a turn the router answered still spawned a coding agent: "
        f"{harness.specs}")


# --------------------------------------------------------------------------- #
# the reflex line -- one fixed sentence, before the spawn (D-10, 10.7)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_reflex_line_comes_before_the_spawn(tmp_path, fake_openai):
    """The whole mitigation for the dead air a child per message buys.

    pi takes 1.5-2.3 s to come up [MEASURED 2026-08-17, 21 runs] and that
    lands as silence after the owner stops speaking, on EVERY question --
    which reads as a dead microphone (D-10). So the line is yielded BEFORE
    `start()` is awaited, not after it: asserted by driving the turn one
    intent at a time and checking that no child had been started when the
    line came out. A line spoken after the spawn would still be first in the
    transcript and would fill none of the gap.
    """
    brain, harness = _brain(tmp_path, fake_openai)
    ctx = TurnContext(turn_id=51, mode="secretary")
    stream = brain.turn("what does the endpointing module do", ctx)

    first = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)

    assert isinstance(first, Say) and first.text == HarnessConverse.NARRATION, (
        f"the first thing a harness turn produced was not the reflex line: "
        f"{first!r}")
    assert harness.specs == [], (
        f"the reflex line was spoken AFTER the child was started, so it "
        f"covers none of the spawn's silence: {harness.specs}")
    await stream.aclose()


@pytest.mark.asyncio
async def test_no_line_is_ever_spoken_twice_in_one_turn(tmp_path, fake_openai):
    """The trap the reflex line walks into, and the reason the first-tool
    narration is GONE rather than merely different.

    `NARRATION` used to be spoken on the turn's first tool. Adding a fixed
    line at the top of the turn without removing that one would say "Working
    on it." twice for any turn that touches a file -- which is most of them.
    Five tools here, so a narration that came back would be caught however
    the two were combined.
    """
    script = (ScriptedTurn("The module holds three policies.",
                           tools=(("read", False), ("grep", False),
                                  ("find", False), ("ls", False),
                                  ("read", True))),)
    brain, harness = _brain(tmp_path, fake_openai, script=script)

    said = _said(await _turn(brain))

    assert said.count(HarnessConverse.NARRATION) == 1, (
        f"the reflex line was spoken {said.count(HarnessConverse.NARRATION)} "
        f"time(s) in one turn: {said}")
    assert said[0] == HarnessConverse.NARRATION, (
        f"the reflex line did not come first: {said}")
    assert len(said) == len(set(said)), (
        f"a five-tool turn said the same sentence twice: {said}")


# --------------------------------------------------------------------------- #
# AC-48 -- one ending, one announcement
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_multi_turn_answer_ends_the_turn_once(tmp_path, fake_openai):
    """The default script is two agent turns. `agent_end` fires when the whole
    loop settles, so the daemon announces once -- not once per turn_end, which
    for a five-turn answer is the announce-it-five-times failure."""
    brain, harness = _brain(tmp_path, fake_openai)

    intents = await _turn(brain)

    assert len(_dones(intents)) == 1, (
        f"a two-turn answer produced {len(_dones(intents))} Done intents: "
        f"{intents}")
    assert isinstance(intents[-1], Done), f"Done was not last: {intents}"


@pytest.mark.asyncio
async def test_an_error_event_is_spoken_and_the_turn_ends(tmp_path,
                                                          fake_openai):
    """AC-47 and AC-48 together, after the replay below has been spent.

    A failure is a value the caller SPEAKS, never an exception it swallows:
    the predecessor's error event fired only on a JS exception, so a provider
    failure ended nothing and held the turn lock forever.
    """
    brain, harness = _brain(tmp_path, fake_openai, fail_on="detonate")

    intents = await _turn(brain, text="detonate the repository")

    assert HarnessConverse.FAILED in _said(intents), (
        f"the failure was silent: {intents}")
    assert len(_dones(intents)) == 1, f"the turn did not end once: {intents}"


@pytest.mark.asyncio
async def test_a_child_that_will_not_start_is_a_spoken_failure(tmp_path,
                                                               fake_openai):
    """AC-46's replacement removes "no child" as a category.

    There is nothing to be missing between turns any more, so a turn cannot
    refuse for want of a child: it spawns one, and a spawn that fails is a
    failed TURN -- spoken, ended once, and re-armed (AC-47, AC-48, D-1). The
    old `NO_CHILD` refusal existed because a boot spawn had already failed
    loudly somewhere the owner could see; nothing fails at boot now.
    """
    class RefusingHarness(FakeHarness):
        async def start(self, spec):
            raise RuntimeError("this child will not start")

    brain, harness = _brain(tmp_path, fake_openai, harness=RefusingHarness())

    intents = await _turn(brain, text="what does the endpointing module do")

    assert _answers(intents) == [HarnessConverse.FAILED], intents
    assert len(_dones(intents)) == 1, f"the turn did not end: {intents}"
    # The QUESTION has to be in the record too. It used to be written past the
    # spawn, so a failed spawn recorded only Butler's apology: the owner's
    # live chat file carries two assistant entries with no question in front
    # of them [READ 2026-08-19], which reads back as Butler apologising to
    # nobody and hides which utterance was lost.
    recorded = [(m["role"], m["content"])
                for m in brain.session_transcript()["messages"]]
    assert recorded == [("user", "what does the endpointing module do"),
                        ("assistant", HarnessConverse.FAILED)], (
        f"a failed spawn left the transcript without the question that "
        f"caused it: {recorded}")


# --------------------------------------------------------------------------- #
# replay a failed turn ONCE, from the verbatim input (plan.md 4.4c)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_failed_turn_is_replayed_once_from_the_same_input(
        tmp_path, fake_openai):
    """Butler owns the retry policy -- which is why `harness_pi` turns pi's
    own auto-retry off -- and this is the half that was owned by nobody.

    Verbatim, never rebuilt from a text history: rebuilding is what silently
    drops images. And the room hears nothing about it, because a retry the
    owner can hear is an apology, and a voice assistant that apologises for
    recovering is worse than one that just recovers.
    """
    brain, harness = _brain(tmp_path, fake_openai, fail_on="detonate",
                            fail_once=True)

    intents = await _turn(brain, text="detonate the repository")

    assert harness.prompts == ["detonate the repository"] * 2, (
        f"the failed turn was not replayed verbatim exactly once: "
        f"{harness.prompts}")
    assert HarnessConverse.FAILED not in _said(intents), (
        f"a turn that recovered still announced a failure: {intents}")
    assert "It holds three policies and one of them is not wired up." in _said(
        intents), f"the replay's answer was never spoken: {intents}"
    assert len(harness.specs) == 2, (
        f"the replay reused the failed child instead of spawning a fresh one "
        f"on the same session reference: {harness.specs}")
    assert harness.specs[-1].session_file == harness.specs[0].session_file, (
        "the replay was pointed at a different session file, so the second "
        "attempt lost the conversation (AC-35)")


@pytest.mark.asyncio
async def test_a_turn_that_already_spoke_is_not_replayed(tmp_path, fake_openai):
    """The other half of the replay rule, and it had no test at all.

    The condition is "nothing has been said yet", not merely "first attempt":
    a turn that streamed a sentence into the room and THEN failed must not be
    asked again, because the owner would hear the first half of the answer,
    then the whole answer -- one and a half answers for one question. Removing
    the `spoke == 0` half of that condition left the entire fast tier green
    until this test existed, because the fake could only fail a prompt it had
    said nothing about (`fail_after` is why it can now do both).
    """
    script = (ScriptedTurn("The module holds three policies. "
                           "There is more to say about it."),)
    brain, harness = _brain(tmp_path, fake_openai, script=script,
                            fail_on="detonate", fail_after=6)

    intents = await _turn(brain, text="detonate the repository")

    said = _said(intents)
    assert harness.prompts == ["detonate the repository"], (
        f"a turn that had already spoken was replayed anyway, so the owner "
        f"hears the answer one and a half times: {harness.prompts}")
    assert "The module holds three policies." in said, (
        f"the sentence spoken before the failure was lost: {said}")
    assert said.count(HarnessConverse.FAILED) == 1, (
        f"the failure after a half-answer was not spoken exactly once: {said}")
    assert len(_dones(intents)) == 1, f"the turn did not end once: {intents}"


@pytest.mark.asyncio
async def test_a_replay_that_fails_too_is_spoken_once(tmp_path, fake_openai):
    """One replay, never a loop, and one spoken failure at the end of it."""
    brain, harness = _brain(tmp_path, fake_openai, fail_on="detonate")

    intents = await _turn(brain, text="detonate the repository")

    assert harness.prompts == ["detonate the repository"] * 2, (
        f"expected exactly two attempts: {harness.prompts}")
    assert _said(intents).count(HarnessConverse.FAILED) == 1, (
        f"the failure was announced "
        f"{_said(intents).count(HarnessConverse.FAILED)} times: {intents}")


# --------------------------------------------------------------------------- #
# barge-in (AC-43, D-4, D-18)
# --------------------------------------------------------------------------- #
async def _drain(stream) -> list:
    return [i async for i in stream]


async def _into_the_answer(brain, turn_id: int, text: str):
    """Drive a turn until the CHILD is up and ANSWERING, then stop.

    The first intent of every harness turn is now the reflex line, which is
    yielded before the spawn -- so a barge-in test that pulled one intent and
    aborted would be aborting a turn that has no child yet, and would prove
    nothing about reaching one. Since phase 7 (`329c965`) the SECOND intent
    is `WorkerEvent(kind="started")`, yielded once the child is up but still
    BEFORE `_answer` sends the prompt -- so it is preamble in exactly the
    reflex line's sense and is skipped for the same reason: a caller that
    stopped here would abort a turn that has not sent anything yet, which is
    a different (and separately covered) window from "abort while answering".
    Pulls until something other than either arrives.
    """
    ctx = TurnContext(turn_id=turn_id, mode="secretary")
    stream = brain.turn(text, ctx)
    while True:
        intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
        if isinstance(intent, Say) and intent.text == HarnessConverse.NARRATION:
            continue
        if isinstance(intent, WorkerEvent) and intent.kind == "started":
            continue
        return stream, intent


@pytest.mark.asyncio
async def test_abort_reaches_the_harness_child(tmp_path, fake_openai):
    """D-18: conformance proves the adapter CAN be aborted; only this proves
    the daemon's brain forwards it. Cancelling our own reader would leave the
    child working on an answer nobody will hear, and the next turn queues
    behind it."""
    brain, harness = _brain(tmp_path, fake_openai)

    stream, first = await _into_the_answer(brain, 7, "read the whole repository")
    assert first is not None

    await brain.abort(7)
    assert harness.aborts == 1, (
        f"brain.abort(7) did not reach Harness.abort(): {harness.aborts} "
        f"call(s)")
    await stream.aclose()


@pytest.mark.asyncio
async def test_abort_also_disposes_the_child(tmp_path, fake_openai):
    """AC-46 on the interruption path, which is the one that leaks.

    Barge-in cancels the reply task, so the turn's generator is left suspended
    and its `finally` runs whenever the garbage collector gets to it. With a
    child per message that is one live coding agent per interruption, waiting
    on a collector. So the abort disposes explicitly, in the same call, after
    telling the child to stop -- pi settles an abort in 1.51 s and still emits
    its terminal event [MEASURED 2026-08-17], which is why it gets the
    graceful stop first rather than the reference implementation's tree-kill.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    stream, _ = await _into_the_answer(brain, 13, "read the whole repository")
    assert harness.disposals == 0, "the child was gone before the barge-in"

    await brain.abort(13)

    assert harness.disposals >= 1, (
        "barge-in aborted the prompt and left the child running; with a child "
        "per message that is one leaked coding agent per interruption "
        "(AC-46, AC-36)")
    assert brain.host.harness is None, (
        "the host still holds a child after a barge-in, so the next turn "
        "would find one it did not start")
    await stream.aclose()


@pytest.mark.asyncio
async def test_a_barged_in_turn_says_nothing_more(tmp_path, fake_openai):
    """A stream that ends because WE killed the child is not a failure.

    The child going away mid-answer is otherwise the ordinary shape of a
    crash, which spends the one-replay budget and speaks a failure. On a
    barge-in that would ask the coding agent the abandoned question a second
    time -- a write the owner interrupted, carried out anyway -- and then
    announce a failure over the top of whatever they interrupted it with.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    stream, _ = await _into_the_answer(brain, 15, "read the whole repository")
    await brain.abort(15)
    rest = await asyncio.wait_for(_drain(stream), TURN_BUDGET_S)

    assert HarnessConverse.FAILED not in _said(rest), (
        f"a barge-in was announced as a failure: {_said(rest)}")
    assert len(harness.prompts) == 1, (
        f"the interrupted prompt was replayed after the owner cut it off: "
        f"{harness.prompts}")


@pytest.mark.asyncio
async def test_a_barge_in_before_the_prompt_is_ever_sent_says_nothing(
        tmp_path, fake_openai):
    """The SECOND window phase 7 opened, and this is its own witness.

    `yield WorkerEvent(kind="started")` (`brain_harness.py`, `turn()`) is a
    new suspension point BEFORE `_answer` is ever called -- the child is up
    but nothing has been prompted yet. A barge-in landing exactly there used
    to crash: `_answer`'s first line re-reads `self._host.child(chat_id)`
    with no `_aborted` check in front of it, and abort() had already disposed
    that child. `test_a_barged_in_turn_says_nothing_more` no longer exercises
    this window on purpose -- `_into_the_answer` now skips past
    `WorkerEvent(kind="started")` the same way it skips the reflex line, to
    keep testing "abort mid-answer" rather than "abort before any answer" --
    so this test is what is left to catch a regression here.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    ctx = TurnContext(turn_id=17, mode="secretary")
    stream = brain.turn("read the whole repository", ctx)
    intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
    assert isinstance(intent, Say) and intent.text == HarnessConverse.NARRATION
    intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
    assert isinstance(intent, WorkerEvent) and intent.kind == "started", (
        f"expected the pre-answer progress frame, got {intent!r}")

    await brain.abort(17)
    rest = await asyncio.wait_for(_drain(stream), TURN_BUDGET_S)

    assert HarnessConverse.FAILED not in _said(rest), (
        f"a barge-in before the prompt was sent was announced as a failure: "
        f"{_said(rest)}")
    assert not harness.prompts, (
        f"a prompt was sent after the owner had already been cut off: "
        f"{harness.prompts}")


@pytest.mark.asyncio
async def test_a_barge_in_during_the_first_spawn_does_not_prompt_the_child(
        tmp_path, fake_openai):
    """A THIRD window, and this one does not crash either -- it answers.

    `abort()` can land while `yield Say(NARRATION)` is suspended, BEFORE
    `ensure()` ever runs. On a conversation's FIRST turn there is no child
    yet for `abort()` to dispose (`held is None` in `abort()`, a no-op past
    setting `_aborted`), so nothing stops `ensure()` -- resumed right after --
    from spawning one anyway: `plan()` already decided a real `HarnessSpec`
    (there was no existing child to compare against and reuse). By the time
    `_answer`'s `held = self._host.child(chat_id)` runs, `held` is that
    freshly spawned child and is not None at all, so the `held is None` guard
    added for the other two windows cannot catch this one -- only a direct
    `self._aborted` check placed after `ensure` (and before `_answer` is ever
    called) does. Without it the fresh child is prompted and the room hears
    an answer to a turn the owner had already cut off.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    ctx = TurnContext(turn_id=1, mode="secretary")
    stream = brain.turn("read the whole repository", ctx)
    intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
    assert isinstance(intent, Say) and intent.text == HarnessConverse.NARRATION

    await brain.abort(1)
    rest = await asyncio.wait_for(_drain(stream), TURN_BUDGET_S)

    assert HarnessConverse.FAILED not in _said(rest), (
        f"a barge-in during the first spawn was announced as a failure: "
        f"{_said(rest)}")
    assert not harness.prompts, (
        f"a child spawned after the barge-in was prompted anyway: "
        f"{harness.prompts}")
    assert brain.host.harness is None, (
        "the child spawned after the barge-in was left running")


@pytest.mark.asyncio
async def test_aborting_another_turn_leaves_a_live_one_alone(tmp_path,
                                                             fake_openai):
    """AC-43's second clause, and this port makes it easy to get wrong:
    `Harness.abort()` takes no turn id, so a brain that forwarded every abort
    would kill the reply to the utterance that CAUSED the barge-in."""
    brain, harness = _brain(tmp_path, fake_openai)

    stream, _ = await _into_the_answer(brain, 9, "read the whole repository")

    await brain.abort(8)                    # an older, different turn

    assert harness.aborts == 0, (
        "aborting turn 8 reached the child that is answering turn 9 -- this "
        "abort is keyed by session, not by turn")
    assert harness.disposals == 0, (
        "aborting turn 8 disposed the child answering turn 9")
    rest = await asyncio.wait_for(_drain(stream), TURN_BUDGET_S)
    assert rest, "the live turn was killed by another turn's abort"


@pytest.mark.asyncio
async def test_a_barged_in_turn_does_not_poison_the_next_one(tmp_path,
                                                             fake_openai):
    """The abandoned prompt's ending used to be the NEXT turn's ending.

    A held-open child owed exactly one terminal event per prompt (AC-48) and
    nobody was reading when the aborted one arrived, so the next thing the
    owner said was answered with silence and a `Done`. A child per message
    cannot do that -- the stream dies with the child -- and this test is what
    would notice if any of it came back. A conversation is where per-turn
    state that was never cleaned up becomes visible (test/AGENTS.md).
    """
    brain, harness = _brain(tmp_path, fake_openai)

    stream, _ = await _into_the_answer(brain, 11, "read the whole repository")
    await brain.abort(11)
    await stream.aclose()

    said = _said(await _turn(brain, turn_id=12))
    assert "It holds three policies and one of them is not wired up." in said, (
        f"the turn after a barge-in was answered with the abandoned turn's "
        f"ending: {said}")


@pytest.mark.asyncio
async def test_a_barge_in_then_a_settings_change_still_answers(tmp_path,
                                                               fake_openai):
    """Two pieces of per-turn state meeting, which is where D-17 lives.

    This used to be about ORDER -- settle the interrupted child, then
    reconcile it with the settings -- and both halves are gone: the settings
    are read fresh into every spawn, and no stream survives a turn. What
    survives is the claim that mattered: the turn after a barge-in plus a
    changed model is answered, by a child carrying the new model.
    """
    prefs = _prefs(tmp_path)
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)

    stream, _ = await _into_the_answer(brain, 21, "read the whole repository")
    await brain.abort(21)
    await stream.aclose()

    prefs.set("harness_model", "fakeprovider/fake-large")
    said = _said(await _turn(brain, turn_id=22))

    assert "It holds three policies and one of them is not wired up." in said, (
        f"the turn after a barge-in plus a model change was not answered: "
        f"{said}")
    assert harness.specs[-1].model == "fakeprovider/fake-large", (
        f"the changed model did not reach the next child: {harness.specs}")


# --------------------------------------------------------------------------- #
# AC-59 -- one child per CONVERSATION, held across turns that completed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_child_is_held_across_turns(tmp_path, fake_openai):
    """AC-59's routine path, and the reason the owner reversed AC-46.

    Two turns, ONE child, two prompts. The count is the whole assertion: under
    AC-46 this said two children, and every question paid the 1.5-2.3 s spawn
    that produced. Asserted on the fake's record of every spec it was started
    with, which fails however fast a spawn is -- a stopwatch here would pass
    against a spawn the fake makes free.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    await _turn(brain, turn_id=1)
    await _turn(brain, turn_id=2)

    assert len(harness.specs) == 1, (
        f"two turns started {len(harness.specs)} children; AC-59 holds one "
        f"across the conversation: {harness.specs}")
    assert harness.prompts == ["what does the endpointing module do"] * 2, (
        f"the held child was not prompted twice: {harness.prompts}")
    assert harness.disposals == 0, (
        f"the child was disposed between two turns that both ended cleanly: "
        f"{harness.disposals} disposal(s)")


@pytest.mark.asyncio
async def test_a_turn_abandoned_before_the_next_one_never_orphans_its_child(
        tmp_path, fake_openai):
    """The PERMANENT orphan, and the reason `_Host` may not hold two (D-18).

    The aggravating interleaving: turn 1 is abandoned mid-answer with its
    generator suspended, turn 2 runs to completion, and only then is turn 1's
    frame finalized. By then turn 1's backstop is guarded off
    (`self._turn_id == ctx.turn_id` is False), so nothing it does can reach
    the child it started -- not late, not at all.

    Holding a child (AC-59) changes what the wrong answer LOOKS like without
    changing this claim. The old defect was turn 2 overwriting the slot and
    orphaning turn 1's child; the new one is turn 2 REUSING it -- putting a
    second prompt on a child whose first prompt is still in flight, and then
    reading the first prompt's ending as turn 2's answer. Both are refused by
    the same guard at the top of `turn()`: a `_turn_id` that is still set
    means the previous turn never released, so its child is dropped LOUDLY
    before this turn asks for one. Loud rather than silent because a warning
    that leaks a coding agent is a description of the bug; never fatal,
    because housekeeping may not cost the microphone (D-1).

    `harness_for` mints a NEW fake per turn, unlike every other test in this
    file: telling "turn 1's child was disposed" from "some child was
    disposed" is the whole assertion, and one shared record cannot.
    """
    children: list[FakeHarness] = []

    def mint(_chat=None):
        children.append(FakeHarness())
        return children[-1]

    chats = ChatStore(tmp_path / "chats")
    prefs = _prefs(tmp_path)
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"])

    # Turn 1, driven into the answer and then walked away from -- exactly
    # what `hub._reply` does when a segment fails to synthesize (3.8, D-4).
    first, _ = await _into_the_answer(brain, 1, "read the whole repository")
    assert len(children) == 1, children

    # Turn 2 runs to completion in the meantime. Its `ensure()` would REUSE
    # turn 1's child -- same spec -- if the guard at the top of `turn()` did
    # not drop it first.
    await _turn(brain, turn_id=2)
    assert len(children) == 2, (
        f"turn 2 reused the child whose prompt turn 1 left in flight, so a "
        f"second prompt went to a busy child and turn 1's ending is queued "
        f"where turn 2 reads: {children}")
    assert children[0].disposals >= 1, (
        "turn 1's child was still held when turn 2 asked for one, and "
        "nothing disposed it: the turn's own backstop can no longer reach it "
        "(AC-59, AC-36, D-18)")

    # And finalizing turn 1's frame afterwards must not touch turn 2's child,
    # which turn 2 completed and is therefore HELD (AC-59).
    await first.aclose()
    assert children[0].disposals >= 1
    assert children[1].disposals == 0, (
        "closing turn 1's abandoned frame disposed turn 2's child; the "
        "backstop's turn-id guard is what stops a late finalizer from killing "
        "whatever is current (D-6, D-18)")
    assert brain.host.harness is children[1], (
        "the host is not holding the child turn 2 completed on")


@pytest.mark.asyncio
async def test_a_conversation_parked_on_a_question_survives_a_different_conversations_turn_starting(
        tmp_path, fake_openai):
    """docs/filing-and-pacing.md's KNOWN DEFECT (5305a6d), and it is a
    DIFFERENT claim from the one above: that test proves the SAME
    conversation's second turn correctly disposes the first. This proves the
    guard must NOT fire for a SECOND, unrelated conversation.

    `HarnessConverse` used to hold `_turn_id`/`_chat_id`/`_aborted`/
    `_completed` as SCALARS on the one instance shared by every conversation
    (AC-59, AC-241's own `_Host` is keyed by chat id; `HarnessConverse` was
    not). The reentrancy guard at the top of `turn()` compared against that
    one scalar, so conversation B merely STARTING a turn read as "the
    previous turn never released" and disposed conversation A's child --
    even though A was not abandoned, walked away from, or finished: it was
    correctly PARKED on `Ask`, waiting on a human (AC-234, AC-49 reversed by
    AC-249/AC-250). The guard's own comment called this path "unreachable in
    production" because `hub._reply` always closes the generator on exit --
    a premise AC-249 falsified the day a turn could legitimately suspend on
    a question instead of ending or being walked away from.

    Driven against a real `HarnessBrain`/`_Host`, with a NEW fake MINTED per
    spawn (`test_parallel_agents.py`'s own rule): a fixture that hands back
    the same fake for both conversations cannot tell "conversation A's own
    child was disposed" from "some child, somewhere, was disposed", and that
    blindness is exactly how this shipped unnoticed.
    """
    from daemon.ports import Ask, AskHuman

    children: list[FakeHarness] = []

    def mint(_chat=None):
        children.append(FakeHarness(
            ask=Ask(qid=f"q-{len(children) + 1}", method="confirm",
                    message="proceed?")))
        return children[-1]

    chats = ChatStore(tmp_path / "chats")
    prefs = _prefs(tmp_path)
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])

    first_id = chats.current()["id"]

    async def _park_on_a_question(turn_id: int, chat_id: str):
        """Drive a turn until its own `Ask` arrives, and stop pulling there.

        Stopping AT the yield rather than draining further is what leaves the
        turn genuinely in flight: the generator is suspended mid-`_answer`,
        holding its child, exactly what a coding agent parked on a human
        looks like from the daemon's side.
        """
        ctx = TurnContext(turn_id=turn_id, mode="secretary", chat=chat_id)
        stream = brain.turn("do the thing", ctx)
        while True:
            intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
            if isinstance(intent, AskHuman):
                return stream

    # Conversation A's turn parks on its own question first -- which is what
    # gives it a message, so `rotate()` below does not REAP it as one of the
    # empty placeholders `daemon/AGENTS.md` warns about (a chat with zero
    # messages is indistinguishable from one nobody ever spoke into).
    stream_a = await _park_on_a_question(201, first_id)
    assert len(children) == 1, children
    assert children[0].disposals == 0

    second_id = chats.rotate()["id"]
    assert second_id != first_id, "the fixture needs two real conversations"

    # Conversation B's turn starts -- a SEPARATE conversation, a SEPARATE
    # child -- while A is still parked and has released nothing.
    stream_b = await _park_on_a_question(202, second_id)
    assert len(children) == 2, (
        f"conversation B did not spawn its own child: {children}")

    assert children[0].disposals == 0, (
        f"conversation A's child was disposed merely because conversation "
        f"B's turn started: {children[0].disposals} disposal(s). The "
        f"reentrancy guard fired across two unrelated conversations instead "
        f"of only within one (AC-241, D-18)")

    await stream_a.aclose()
    await stream_b.aclose()


@pytest.mark.asyncio
async def test_the_child_survives_the_turn_being_walked_away_from(tmp_path,
                                                                  fake_openai):
    """AC-59 asserted where the daemon actually stops, which is `Done`.

    `hub._reply` breaks out of this generator on `Done` and then CLOSES it
    through `_let_go`, so both moments matter and they are different claims
    (D-18): at `Done` the host must still hold the child, and closing the
    generator afterwards must not take it away either -- the turn completed,
    so its `finally` has nothing to clean up.

    The second half is the one with teeth. A `finally` that disposed
    unconditionally would pass the first assertion and still respawn on every
    question, because the close is where `hub._reply` always ends up.
    """
    brain, harness = _brain(tmp_path, fake_openai)

    ctx = TurnContext(turn_id=1, mode="secretary")
    stream = brain.turn("what does the endpointing module do", ctx)
    while True:
        intent = await asyncio.wait_for(anext(stream), TURN_BUDGET_S)
        if isinstance(intent, Done):
            break

    assert harness.disposals == 0, (
        f"the child was disposed at the turn's ending; AC-59 holds it for the "
        f"next question: {harness.disposals} disposal(s)")
    assert brain.host.harness is not None, (
        "the host let go of the child at `Done`")
    assert brain.host.spec is not None, (
        "the host holds a child but no record of what it was started as")

    await stream.aclose()

    assert harness.disposals == 0, (
        "closing the generator of a turn that COMPLETED disposed its child, "
        "so every question pays a spawn again -- `_let_go` runs on every exit "
        "from `hub._reply` (AC-59)")
    assert brain.host.harness is not None, (
        "the host dropped the child when the finished turn was closed")


# --------------------------------------------------------------------------- #
# AC-60 -- `HarnessSpec` equality IS the invalidation rule
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_new_conversation_replaces_the_held_child(tmp_path,
                                                          fake_openai):
    """The first of the owner's four ways to end a conversation (AC-60).

    Rotating the chat record mints a new chat id and a new `sessionRef`, so the
    next turn resolves a spec no conversation is holding and `ensure()` starts
    a child for it. Nothing in `new_conversation` disposes anything: if it did,
    there would be two mechanisms for one rule and the day they disagreed the
    agent would keep answering out of the conversation the owner just ended.

    The session file is asserted DIFFERENT, which is the whole point -- a
    second child pointed at the OLD session file would be resuming the
    conversation it was supposed to leave behind.

    **THE CLAIM NARROWED WITH AC-241 AND THE OLD ONE IS KEPT HERE.** While
    `_Host` held ONE child this test read "a new conversation REPLACES the
    held child", asserted as one disposal. With a child per conversation the
    old conversation keeps its own agent -- pressing New chat is not a way to
    kill nine background agents -- so the disposal count is now ZERO and what
    is asserted instead is that the new conversation got a child of its OWN,
    on its own session file. `harness_for` therefore mints a fake per spawn,
    because one shared fake cannot tell two live children apart (and refuses
    `start()` on a live child, which is how this was noticed).
    """
    children: list[FakeHarness] = []

    def mint(_chat=None):
        children.append(FakeHarness())
        return children[-1]

    prefs = _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])

    await _turn(brain, turn_id=1)
    first = children[0].specs[-1]

    await brain.new_conversation()
    await _turn(brain, turn_id=2)

    assert len(children) == 2, (
        f"a new conversation did not get a child of its own: {children}")
    assert children[1].specs[-1].session_file != first.session_file, (
        f"the new conversation's child was pointed at the OLD session file, "
        f"so the agent resumed the conversation the owner just ended: "
        f"{children[1].specs}")
    assert children[0].disposals == 0, (
        "pressing New chat disposed the conversation it left; with a child "
        "per conversation that agent may still be working (AC-241)")
    assert brain.host.live() == 2, (
        f"two conversations, two agents: {brain.host.live()}")


@pytest.mark.asyncio
async def test_a_changed_working_directory_replaces_the_held_child(
        tmp_path, fake_openai):
    """The third way (AC-60, AC-31). Everything about a child is fixed at
    spawn, working directory included, so a changed `cwd` is dispose-plus-
    start -- and with a child held, this is the case that would otherwise
    leave a tool-enabled agent answering from a directory nobody means now.

    A conversation's OWN project never changes (`docs/filing-and-pacing.md`
    8, R-A: the spoken move that used to drive this test is deleted), so the
    `cwd` field changes for one conversation only when it is UNPROJECTED and
    the `harness_cwd` default it falls back to is edited.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    prefs = _prefs(tmp_path, projects=[str(elsewhere)])
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)
    await _turn(brain, turn_id=1)
    assert harness.specs[-1].cwd == str(tmp_path)

    prefs.set("harness_cwd", str(elsewhere))
    await _turn(brain, turn_id=2)

    assert len(harness.specs) == 2, (
        f"a changed working directory did not replace the held child, so the "
        f"agent is still running in {harness.specs[-1].cwd}: {harness.specs}")
    assert harness.specs[-1].cwd == str(elsewhere)
    assert harness.specs[-1].session_file == harness.specs[0].session_file, (
        "a changed working directory started a different conversation; only "
        "the directory changed (AC-31, AC-35)")
    # The DISPOSAL, not just the second spawn. Without it this test passes
    # unchanged against an implementation where spec equality does nothing at
    # all and every turn respawns -- which is what it did under AC-46, and a
    # test that cannot tell those apart is not testing AC-60 (D-13).
    assert harness.disposals == 1, (
        f"the child in the old directory was left running while a second one "
        f"answered from the new one: {harness.disposals} disposal(s)")


@pytest.mark.asyncio
async def test_a_changed_tool_posture_replaces_the_held_child(tmp_path,
                                                              fake_openai):
    """The fourth way (AC-60), and the one a list of events would forget.

    `harness_tools` is neither the model nor the directory nor the
    conversation -- it is the fourth field of `HarnessSpec`, and it reaches
    the child through the same equality check as the other three. That is the
    argument for comparing the spec rather than enumerating the events: a
    fifth field gets this behaviour without anyone remembering to add it.
    """
    prefs = _prefs(tmp_path)                       # default read_only
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)
    await _turn(brain, turn_id=1)
    assert harness.specs[-1].tools == ("read", "grep", "find", "ls")

    prefs.set("harness_tools", "full")
    await _turn(brain, turn_id=2)

    assert len(harness.specs) == 2, (
        f"changing the tool posture did not replace the held child, so the "
        f"agent is still running under the old one: {harness.specs}")
    assert harness.specs[-1].tools == (), (
        f"`full` did not reach the replacement child: {harness.specs}")
    assert harness.disposals == 1, (
        f"the read_only child was left running beside the full one: "
        f"{harness.disposals} disposal(s)")


@pytest.mark.asyncio
async def test_an_unchanged_setting_does_not_replace_the_held_child(
        tmp_path, fake_openai):
    """The other direction, and without it the rule above is trivially met.

    A resolver called per turn answers with a NEW `HarnessSpec` object every
    time; only its VALUE is stable. An `ensure()` comparing identity rather
    than equality would respawn on every question while passing every test
    above, because each of those changes a value too (D-13).
    """
    prefs = _prefs(tmp_path)
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)

    await _turn(brain, turn_id=1)
    prefs.set("voice", "bm_george")        # a setting no spec carries
    await _turn(brain, turn_id=2)

    assert len(harness.specs) == 1, (
        f"a fresh spec object with identical values replaced the child, so "
        f"every turn respawns: {harness.specs}")


@pytest.mark.asyncio
async def test_disposing_the_brain_drops_the_held_child(tmp_path, fake_openai):
    """AC-62: the daemon's own teardown reaches the child it now holds.

    `butlerd._restart_cleanup` calls `brain.dispose()` if the brain has one,
    and had nothing to call while a child lived for one message. `os.execv`
    keeps this process's handles, so a coding agent still running would
    survive into the new image with nobody holding it (AC-36, D-24).
    """
    brain, harness = _brain(tmp_path, fake_openai)
    await _turn(brain, turn_id=1)
    assert harness.disposals == 0

    await brain.dispose()

    assert harness.disposals == 1, (
        f"restarting the daemon left the held coding agent running: "
        f"{harness.disposals} disposal(s)")
    assert brain.host.harness is None


@pytest.mark.asyncio
async def test_a_child_that_dies_mid_prompt_is_replaced_once(tmp_path,
                                                             fake_openai):
    """A crash mid-answer spends the SAME one-replay budget as any failure.

    This is the ordinary shape of a harness going away -- and it survives the
    lifecycle change, because a child can still die between the spawn and the
    ending. Respawning on the same session file and asking once is what pi's
    own auto-retry used to do badly (a whole `agent_end` per attempt).
    """
    class DiesOnce(FakeHarness):
        """Ends the stream mid-answer, exactly once, with no terminal event."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.died = False

        async def prompt(self, text: str) -> None:
            await super().prompt(text)
            if not self.died:
                self.died = True
                await self.dispose()

    brain, harness = _brain(tmp_path, fake_openai, harness=DiesOnce())

    said = _said(await _turn(brain))

    assert "It holds three policies and one of them is not wired up." in said, (
        f"the turn whose child died was not recovered: {said}")
    assert len(harness.specs) == 2, (
        f"the replacement child was never spawned: {harness.specs}")
    assert harness.specs[-1].session_file == harness.specs[0].session_file, (
        "the replacement was pointed at a different session file")


# --------------------------------------------------------------------------- #
# AC-66/AC-67 -- three brain modes, and the third one has no router
# --------------------------------------------------------------------------- #
def _harness_only(tmp_path, prefs=None, harness=None):
    """The third mode, built the way `build_hub` builds it: NO resolver.

    That absence is the assertion's foundation. `HarnessOnlyBrain` declares no
    `NEEDS_TARGETS`, so the daemon hands it no `TargetResolver` at all -- a
    brain that reached for a router would fail here rather than quietly
    working against whatever server a test happened to start.
    """
    from daemon.adapters.brain_harness import HarnessOnlyBrain

    child = harness if harness is not None else FakeHarness()
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    brain = HarnessOnlyBrain(
        harness_for=lambda _chat=None: child,
        spec_for=_resolver(tmp_path, prefs, chats),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"])
    return brain, child


@pytest.mark.asyncio
async def test_harness_only_mode_sends_everything_to_the_agent(tmp_path,
                                                               fake_openai):
    """AC-67: no router, so nothing is answered instead of being sent on.

    The utterance is one the router would certainly have taken for itself --
    `test_the_router_still_runs_in_front` uses exactly this one to prove the
    opposite in `brain + harness` mode. Here it must reach the agent, and the
    server must be asked NOTHING: this brain holds no `llm.Client` and no
    resolver, so a verdict request would mean a router came back by another
    route.
    """
    brain, harness = _harness_only(tmp_path)

    intents = await _turn(brain, text="what time is it")

    assert harness.prompts == ["what time is it"], (
        f"the utterance did not reach the coding agent: {harness.prompts}")
    assert fake_openai.asks == [], (
        f"harness-only mode asked a model server for something; the only "
        f"thing that would is a router: {fake_openai.asks}")
    assert len(_dones(intents)) == 1, f"the turn did not end once: {intents}"


@pytest.mark.asyncio
async def test_harness_only_mode_still_holds_one_child(tmp_path, fake_openai):
    """AC-59 belongs to the mode, not to the router in front of it.

    The lifecycle lives in `_Host`/`HarnessConverse`, which both harness
    brains share, and a mode that re-implemented the turn would be the place
    that quietly lost it -- which is exactly why this brain delegates rather
    than copying (D-18: the claim is asserted where the second caller is).
    """
    brain, harness = _harness_only(tmp_path)

    await _turn(brain, turn_id=1)
    await _turn(brain, turn_id=2)

    assert len(harness.specs) == 1, (
        f"harness-only mode spawned a child per message: {harness.specs}")
    assert len(harness.prompts) == 2, harness.prompts


@pytest.mark.asyncio
async def test_harness_only_mode_keeps_the_conversation_controls(tmp_path,
                                                                 fake_openai):
    """The capabilities the hub reads, on the brain that declares them.

    `HAS_SESSIONS` is how the spoken "new conversation" and "work in project
    X" find a brain at all (`hub.session_brain`; the second capability,
    `CAN_SWITCH_PROJECT`, went with the move on 2026-09-26). A mode that
    declared it and then had no method behind it would be a capability the
    hub calls into nothing -- so the birth is exercised rather than read off
    the class, WITH a project, which is what "work in X" and New chat both
    hand it now.

    `harness_for` MINTS since AC-241: the new conversation gets a child of its
    own while the old one keeps its agent, so two are live at once and one
    shared fake refuses `start()` on the second.
    """
    from daemon.adapters.brain_harness import HarnessOnlyBrain

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    prefs = _prefs(tmp_path, projects=[str(elsewhere)])
    children: list[FakeHarness] = []

    def mint(_chat=None):
        children.append(FakeHarness())
        return children[-1]

    chats = ChatStore(tmp_path / "chats")
    brain = HarnessOnlyBrain(
        harness_for=mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats,
                                               prefs.project_paths),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"])

    assert getattr(HarnessOnlyBrain, "HAS_SESSIONS", False)
    await _turn(brain, turn_id=1)
    first = children[0].specs[-1]

    await brain.new_conversation(str(elsewhere))
    await _turn(brain, turn_id=2)

    harness = children[-1]
    assert harness.specs[-1].session_file != first.session_file, (
        "the new conversation did not reach the next child in harness-only "
        "mode")
    assert harness.specs[-1].cwd == str(elsewhere), (
        "the new conversation's project did not reach the next child in "
        "harness-only mode")
    assert brain.session_transcript()["messages"], (
        "harness-only mode kept no record of the conversation (AC-35)")


def test_the_daemon_builds_all_three_brain_modes(tmp_path, monkeypatch):
    """AC-66's reader. A mode the settings page offers and `build_hub` cannot
    construct is the AC-7b failure with a restart in the middle of it: the
    owner picks it, restarts, and the daemon does not come back.

    Driven through `BRAIN_MODES` itself rather than a literal list, so a
    fourth mode added to that table without a `build_hub` path fails here.
    """
    import json

    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    for mode, brain_name in butlerd.BRAIN_MODES.items():
        (tmp_path / "settings.json").write_text(
            json.dumps({"brain_mode": mode, "harness": "fake",
                        "harness_cwd": str(tmp_path)}), encoding="utf-8")
        hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake"}})
        assert hub.brain.info["engine"] == brain_name, (
            f"brain_mode={mode!r} built {hub.brain.info} instead of "
            f"{brain_name!r}")

    # And the third one carries no router, which is the whole of what it IS.
    from daemon.adapters.brain_harness import HarnessOnlyBrain
    assert not getattr(HarnessOnlyBrain, "NEEDS_TARGETS", False), (
        "harness-only mode asks the daemon for a TargetResolver, so it has a "
        "router after all (AC-67)")


# --------------------------------------------------------------------------- #
# the settings that spawn it (AC-7b: a control that changes nothing is a lie)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_harness_settings_reach_the_child_it_spawns(tmp_path,
                                                              fake_openai):
    """`harness_model` and `harness_cwd` are read into the spec, and the spec
    is the only honest account of what a child was started as."""
    work = tmp_path / "elsewhere"
    work.mkdir()
    prefs = _prefs(tmp_path, harness_cwd=str(work),
                   harness_model="codexbridge-local/gpt-5.4-mini")
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)

    await _turn(brain)

    assert harness.specs[-1].cwd == str(work)
    assert harness.specs[-1].model == "codexbridge-local/gpt-5.4-mini"
    # The tool posture reaches the spec too, from the `harness_tools` setting
    # (a plain knob, not the retired grant). The shipped default is read_only,
    # so an unset preference resolves to the read allow-list (AC-29 amended
    # 2026-08-18).
    assert harness.specs[-1].tools == ("read", "grep", "find", "ls"), (
        f"the default read_only posture did not reach the spec: "
        f"{harness.specs[-1].tools}")


@pytest.mark.asyncio
async def test_a_changed_setting_reaches_the_next_child(tmp_path, fake_openai):
    """Everything about a child is fixed at spawn, so a changed model can only
    reach the NEXT one -- which is now the next message rather than a
    compare-and-respawn (AC-31, AC-46). The session file must survive it: the
    change happens mid-conversation and the conversation has to come with it.
    """
    prefs = _prefs(tmp_path)
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)
    await _turn(brain)
    first = harness.specs[-1]

    prefs.set("harness_model", "claudebridge-local/claude-haiku-4-5")
    await _turn(brain, turn_id=2)

    assert harness.specs[-1].model == "claudebridge-local/claude-haiku-4-5", (
        f"changing the model did not reach the next child: {harness.specs}")
    assert harness.specs[-1].session_file == first.session_file, (
        "the next child was pointed at a different session file, so the "
        "conversation did not survive the change (AC-35)")


@pytest.mark.asyncio
async def test_a_blank_working_directory_defaults_to_the_tmp_scratch_dir(
        tmp_path, fake_openai):
    """AC-31, amended 2026-08-18 by the owner. A blank `harness_cwd` no longer
    refuses the turn: it DEFAULTS to a scratch dir (`TMP`), so a child starts
    somewhere harmless and the turn is answered normally. What did NOT change
    is the refusal to guess a NAMED project -- the default is the scratch dir,
    never a spoken name."""
    prefs = Settings(tmp_path / "settings.json")       # harness_cwd unset
    brain, harness = _brain(tmp_path, fake_openai, prefs=prefs)

    intents = await _turn(brain)

    assert len(harness.specs) == 1, (
        f"a blank working directory did not start exactly one child: "
        f"{harness.specs}")
    assert harness.specs[-1].cwd == str(butlerd.REPO / "TMP"), (
        f"the child was not pointed at the TMP scratch dir: {harness.specs}")
    assert _answers(intents) and HarnessConverse.FAILED not in _answers(
        intents), intents


def test_the_daemon_builds_harness_mode_from_the_harness_setting(tmp_path,
                                                                 monkeypatch):
    """The `harness` setting's reader, which is what makes it a control (AC-7b).

    `build_hub` is the only place that turns a brain's declared capability
    into the callables it needs, and nothing else in the suite goes through
    that branch: a daemon that named the wrong kwarg would boot every other
    test unchanged and fail the first time somebody set `BUTLER_BRAIN=harness`.

    Aimed at a scratch state directory and a scratch pi registry, because the
    real ones are the owner's and the real one holds plaintext keys
    (test/AGENTS.md).
    """
    import json

    from daemon.adapters.harness_fake import FakeHarness

    (tmp_path / "settings.json").write_text(
        json.dumps({"harness": "fake", "harness_cwd": str(tmp_path)}),
        encoding="utf-8")
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "harness"}})

    assert hub.brain.info["engine"] == "harness", hub.brain.info
    assert hub.brain.host.harness is None, (
        "the daemon built a harness child at boot; a child is started by the "
        "first turn that needs one, not by a restart nobody has spoken to "
        "(AC-59, HarnessConverse.warm)")
    assert isinstance(hub.brain.host._harness_for(), FakeHarness), (
        "the `harness` setting did not decide which harness would be built")
    assert hub.brain.host.desired().cwd == str(tmp_path), (
        "harness_cwd did not reach the spec the daemon would spawn with")


def test_the_harness_setting_is_read_per_message_not_at_boot(tmp_path,
                                                             monkeypatch):
    """The `restart` flag came off that setting, so it has to mean something.

    A flag that was removed while the value was still read once at boot is
    AC-7b in its purest form: the page stops warning about a restart that is
    still required. The factory is asked twice across a settings change.
    """
    import json

    from daemon.adapters.harness_fake import FakeHarness

    (tmp_path / "settings.json").write_text(
        json.dumps({"harness": "fake", "harness_cwd": str(tmp_path)}),
        encoding="utf-8")
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "harness"}})
    build = hub.brain.host._harness_for
    assert isinstance(build(), FakeHarness)

    hub.settings.set("harness", "pi")

    from daemon.adapters.harness_pi import PiHarness
    assert isinstance(build(), PiHarness), (
        "the harness setting was read once at boot, so the control it is "
        "rendered as no longer matches what it does")
    described = hub.settings.describe()["settings"]
    assert not any(s["key"] == "harness" and s["restart"] for s in described), (
        "the `harness` setting still warns about a restart nobody needs")
    assert any(s["key"] == "brain_mode" and s["restart"] for s in described), (
        "`brain_mode` lost its restart flag; the Brain composite is still "
        "built once at boot and that flag is still true")


# --------------------------------------------------------------------------- #
# the live half of AC-59: the SAME pid answers the next question
# --------------------------------------------------------------------------- #
TERSE = ("You are terse. Answer in one short sentence. Do not use any tools.")


LIVE_BUDGET_S = 180.0


async def _live_tree(harness, tree_of, deadline_s: float = 60.0) -> list:
    """The running child's process tree, caught WHILE the turn is in flight.

    Sampling after the turn is too late by construction -- the child is
    disposed at its terminal event -- so this polls the adapter's own handle
    while a turn task runs beside it, and waits for the tree to be at least
    two deep. Two deep matters for the reason `test_child_lifetime.py` gives:
    `cmd /c pi` is the parent and node is the grandchild, and the grandchild
    is the half that gets missed.
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        child = getattr(harness, "_child", None)
        if child is not None:
            tree = tree_of(child.proc.pid)
            if len(tree) >= 2:
                return tree
        await asyncio.sleep(0.05)
    return []


@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_same_real_child_answers_the_next_turn(tmp_path, fake_openai):
    """AC-59's PID half, against a child that really exists.

    The fake's record proves Butler ASKED for one child per conversation; only
    a real process proves the same one is still there for the next question,
    which is the whole benefit -- and only a real process can show the
    conversation surviving in pi's own session file rather than in Butler's
    record of it.

    Identity is a PID paired with its CREATION TIME, never a bare PID, and the
    helpers are IMPORTED from `test_child_lifetime` rather than copied: this
    machine carries 147 detached `git fsmonitor--daemon` orphans and 8 of them
    named a LIVE process as their parent when it was measured [2026-08-18],
    which is what made that file intermittently red while passing alone. Two
    copies of that discipline would drift, and the drifting one would be the
    one accusing Butler of a leak it did not cause (test/AGENTS.md).
    """
    skip_without_pi()
    from test_child_lifetime import _survivors, _tree

    harness = ports.build(ports.HARNESSES, "pi",
                          **pi_kwargs(append_system_prompt=TERSE))
    brain, _ = _brain(tmp_path, fake_openai, harness=harness)
    try:
        ctx = TurnContext(turn_id=1, mode="secretary")
        turn = asyncio.create_task(_drain(brain.turn("say ok", ctx)))
        tree = await _live_tree(harness, _tree)
        assert len(tree) >= 2, (
            "no two-deep pi tree was ever seen during a live turn, so this "
            "test is measuring nothing")
        await asyncio.wait_for(turn, LIVE_BUDGET_S)

        # The turn is over and AC-59 says the child is still there, ready for
        # the next question. `_survivors` is the SAME instrument the old
        # assertion used and its sense is inverted: the pair must still match.
        assert _survivors(tree), (
            "the child that answered turn 1 is gone, so the next question "
            "pays a spawn -- AC-59 holds it across turns that completed")

        # And the next message goes to THAT child rather than a new one.
        ctx = TurnContext(turn_id=2, mode="secretary")
        turn = asyncio.create_task(_drain(brain.turn("say ok again", ctx)))
        second = await _live_tree(harness, _tree)
        assert second and second[0].pid == tree[0].pid, (
            f"the second message started a child of its own instead of "
            f"reusing the held one: {second} against {tree}")
        await asyncio.wait_for(turn, LIVE_BUDGET_S)
    finally:
        # The job object kills orphans when this process dies, and a test that
        # leaks for the whole run is still leaking (test/AGENTS.md). This is
        # now the ONLY thing that stops the held child outliving the test.
        await harness.dispose()
        assert not _survivors(tree), (
            f"disposing the brain's harness left the child running: "
            f"{_survivors(tree)}")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_only_the_first_live_turn_pays_the_spawn(tmp_path, fake_openai):
    """The benefit, measured rather than assumed (AC-59).

    This test has now asserted three different things, which is the honest
    record of two reversals: that no turn paid a spawn (a child held from
    boot), that BOTH turns did (AC-46), and now that only the first does. The
    numbers are PRINTED and not asserted on, because the model's own time
    dominates both and this machine's load is not a contract -- what is
    asserted is the mechanism, off the adapter's own handle: the second turn
    talks to the child the first one started.
    """
    skip_without_pi()
    harness = ports.build(ports.HARNESSES, "pi",
                          **pi_kwargs(append_system_prompt=TERSE))
    brain, _ = _brain(tmp_path, fake_openai, harness=harness)
    try:
        started = time.perf_counter()
        first = await _turn(brain, text="say ok", turn_id=1,
                            budget=LIVE_BUDGET_S)
        first_s = time.perf_counter() - started
        # The adapter's own handle, read BETWEEN the turns: this is the
        # assertion with teeth, and it is why the timings below stay a print.
        held = getattr(harness, "_child", None)
        assert held is not None, (
            "the first live turn left no child behind, so the second one "
            "pays a spawn (AC-59)")

        started = time.perf_counter()
        later = await _turn(brain, text="say ok again", turn_id=2,
                            budget=LIVE_BUDGET_S)
        later_s = time.perf_counter() - started
        assert getattr(harness, "_child", None) is held, (
            "the second live turn talked to a different child than the one "
            "the first turn started (AC-59)")
    finally:
        await harness.dispose()

    print(f"[AC-59] live pi turns: {first_s:.2f}s then {later_s:.2f}s; only "
          f"the first includes a spawn measured at 1.5-2.3 s")
    assert _answers(first), f"the first live turn said nothing: {first}"
    assert _answers(later), f"the second live turn said nothing: {later}"
    assert len(_dones(first)) == 1 and len(_dones(later)) == 1


@pytest.mark.asyncio
async def test_a_rewind_replaces_the_held_child(tmp_path, fake_openai):
    """The owner, 2026-09-11: "rewinding doesn't seem to prune context from
    llm to that message. it seems llm still has the whole conversation in
    memory."

    They were right, and the cause is two docstrings that disagree.
    `session_rewind` says nulling `sessionRef` is what makes the next
    `ensure()` see a changed spec and replace the child. `chats.rewind` says,
    in the same repository, that nulling it "buys nothing -- `session_ref`
    hands back a byte-identical path on the next call", which is why it renames
    the FILE instead. The second one is true: the live conversation keeps its
    id through a rewind, so the re-minted path is the same string, the spec
    compares EQUAL, and the held child (AC-59) is never disposed. The file on
    disk moved; the agent's own memory did not.

    `new_conversation` gets this right for free because `rotate()` mints a new
    chat ID, so the path really does change. That is the difference, and it is
    why the argument was reused and did not survive the reuse.

    Asserted on the CHILD and not on the record: a rewind that wrote every file
    correctly and left the same process answering is exactly what the owner
    reported, and every record-level assertion passes through it.
    """
    brain, harness = _brain(tmp_path, fake_openai)
    await _turn(brain, turn_id=1)
    first = harness.specs[-1]
    chats = brain.host._chats
    live = chats.current()["id"]
    chats.append(live, "user", "the thing I want to take back")
    chats.append(live, "assistant", "an answer to it")
    target = chats.current()["messages"][0]["id"]

    assert brain.session_rewind(target) is not None
    await _turn(brain, turn_id=2)

    assert harness.disposals >= 1, (
        "the rewind left the SAME child answering, so the agent still holds "
        "every turn the owner just took back")
    assert len(harness.specs) == 2, (
        f"no new child was spawned after the rewind: {harness.specs}")
    assert harness.specs[-1].session_file != first.session_file, (
        f"the new child was pointed at the same session file the rewind moved "
        f"away: {harness.specs[-1].session_file}")
