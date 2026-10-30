"""A child per conversation, and the cap the owner sets.

`docs/parallel-agents.md` phase 5: AC-241 (one child per conversation, and no
conversation disposes another's), AC-237 (the cap is the daemon's and
unbypassable) and AC-245 (a slot can always be freed, and the refusal says
how).

Everything here runs against the FAKE harness and `harness_for` MINTS one per
spawn. That is not a detail: while `_Host` held one child, a fixture handing
back the same fake every time was the honest shape, and with a child per
conversation it is the one shape that cannot tell "two children" from "one
child started twice" -- the fake refuses `start()` on a live child, which is
how the change was noticed at all.

AC-59 and AC-60 are NOT re-asserted here. Their witnesses are in
`test_brain_harness.py` -- one spec across two turns reuses one child, and
each of the four spec-changing events replaces it -- and this file exists
because the MEANING of that comparison changed while the rule did not.

**WHAT THIS FILE RECORDS RATHER THAN ASSERTS, because it is the one place the
spec contradicts itself:** `docs/parallel-agents.md` D3 says a `done`
conversation does not hold a slot ("the child was disposed when the turn
ended") and AC-59 says a child is HELD for the whole conversation. Both cannot
be true. AC-59 wins here, because it is in `plan.md` and is the owner's own
reversal of AC-46, so a conversation that finished a turn goes on holding its
agent and its slot. `test_the_five_states_and_the_live_child_after_each`
drives all five and is the executable record of which row diverges.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (StubResolver, pi_kwargs,                 # noqa: E402
                      skip_without_pi)
from daemon import butlerd, ports                              # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain         # noqa: E402
from daemon.adapters.harness_fake import FakeHarness           # noqa: E402
from daemon.chats import ChatStore                             # noqa: E402
from daemon.ports import (Ask, AskHuman, Answer, Done, Say,    # noqa: E402
                          TurnContext)
from daemon.settings import BY_KEY, Settings                   # noqa: E402

TURN_BUDGET_S = 15.0


def _prefs(tmp_path, projects=(), **overrides) -> Settings:
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    # THIS FILE'S OWN CONCERN IS AC-241 (one child per CONVERSATION) AND THE
    # GLOBAL CAP, NOT docs/filing-and-pacing.md's PER-PROJECT ONE. Every
    # fixture here shares ONE `harness_cwd` -- none of these conversations
    # ever records a `project` -- so the per-project limit reads all of
    # them as ONE project and would refuse the second child in it. Raised
    # to the setting's own maximum (10, review 2026-09-16: was 50, which the
    # schema no longer allows -- 10 is also the shipped `harness_agents_max`
    # default this file never exceeds, so it is still past every count this
    # file ever builds without being a second guessed number): that feature
    # gets its own prefs in test_harness_project_limit.py instead of
    # coupling every test here to a number it is not about.
    prefs.set("project_agent_limit", 10)
    for key, value in overrides.items():
        prefs.set(key, value)
    prefs.project_paths = tuple(str(p) for p in projects)
    return prefs


class _Agents:
    """Every child the brain has spawned, newest last.

    `kwargs_for` is how a test scripts ONE of them: the replay of a failed
    turn gets a FRESH child, so a `fail_on` applied to every child fails the
    replay too and the test would be measuring a broken harness rather than a
    cap. It takes the spawn's index and answers that child's constructor
    arguments.
    """

    def __init__(self, kwargs_for=None, **kwargs) -> None:
        self.kwargs_for = kwargs_for or (lambda i: kwargs)
        self.all: list[FakeHarness] = []

    def mint(self, _chat=None) -> FakeHarness:
        self.all.append(FakeHarness(**self.kwargs_for(len(self.all))))
        return self.all[-1]

    @property
    def prompts(self) -> list[str]:
        return [p for child in self.all for p in child.prompts]


def _brain(tmp_path, server, prefs=None, **kwargs):
    """A real `HarnessBrain` over the daemon's own resolvers.

    The spec resolver and the cap resolver are both built from ONE `Settings`,
    which is what makes a test that writes `harness_agents_max` a test of the
    setting rather than of a number it passed itself (AC-237).
    """
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    agents = _Agents(**kwargs)
    brain = HarnessBrain(
        resolver=StubResolver(server.base_url), harness_for=agents.mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats,
                                               prefs.project_paths),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        # Wired so `_prefs`'s own `project_agent_limit` (raised past every
        # count this file builds) actually reaches `_Host` -- without this
        # the constructor's OWN default (1) would apply regardless of what
        # `_prefs` sets, and every multi-conversation test here would read
        # its own fixtures as one project over the limit.
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])
    return brain, agents, chats, prefs


async def _turn(brain, text="what does the endpointing module do", turn_id=1):
    """One whole turn's intents, on a deadline (test/AGENTS.md)."""
    async def collect():
        ctx = TurnContext(turn_id=turn_id, mode="secretary")
        return [i async for i in brain.turn(text, ctx)]
    return await asyncio.wait_for(collect(), TURN_BUDGET_S)


def _said(intents) -> list[str]:
    return [i.text for i in intents if isinstance(i, Say) and i.spoken]


def _users(chat) -> list[str]:
    return [m["content"] for m in chat["messages"] if m["role"] == "user"]


def _assistant(chat) -> list[str]:
    return [m["content"] for m in chat["messages"] if m["role"] == "assistant"]


# --------------------------------------------------------------------------- #
# AC-241 -- one child per conversation, and no crossed wires
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_conversations_hold_two_children(tmp_path, fake_openai):
    """AC-241. Two live conversations, two agents, and both still up.

    The load-bearing assertion is the NEGATIVE one: a `_Host` that kept ONE
    child answers both turns correctly and disposes the first conversation's
    agent to do it, so every transcript and every spoken line in this test
    passes against the defect. What separates them is `disposals` on the child
    that was NOT asked anything.

    This is the test the instrument-validation mutation aims at: make the
    lookup ignore the chat id and conversation B answers out of conversation
    A's repository, silently.
    """
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)

    first = chats.current()["id"]
    await _turn(brain, "what does the endpointing module do", turn_id=1)
    second = await brain.new_conversation()
    await _turn(brain, "how does the wake word work", turn_id=2)

    assert len(agents.all) == 2, (
        f"two conversations did not get two children: {agents.all}")
    assert brain.host.live() == 2, (
        f"the host is not holding two children: {brain.host.live()}")
    assert agents.all[0].disposals == 0, (
        "the second conversation's turn disposed the first conversation's "
        "agent -- that agent may have been mid-edit (AC-241)")
    assert agents.all[1].disposals == 0, agents.all[1].disposals

    # Each child is pointed at its OWN conversation's session file, which is
    # the fact a shared child cannot have.
    files = {child.specs[-1].session_file for child in agents.all}
    assert len(files) == 2, (
        f"both children were started on the same session file, so one agent "
        f"is resuming the other's conversation: {files}")
    assert first in agents.all[0].specs[-1].session_file
    assert second in agents.all[1].specs[-1].session_file


@pytest.mark.asyncio
async def test_neither_conversation_writes_into_the_others_record(
        tmp_path, fake_openai):
    """AC-241's other half: two transcripts, no interleaving.

    The measured failure this is named after is two conversations appended
    into one record, which is NOT recoverable once written. Asserted in both
    directions -- what each record HAS and what it does NOT -- because a
    daemon that appended everything to both would satisfy either half alone.
    """
    brain, _, chats, _ = _brain(tmp_path, fake_openai)

    first = chats.current()["id"]
    await _turn(brain, "question about endpointing", turn_id=1)
    second = await brain.new_conversation()
    await _turn(brain, "question about the wake word", turn_id=2)

    a = chats.find(first)
    b = chats.find(second)
    assert _users(a) == ["question about endpointing"], a["messages"]
    assert _users(b) == ["question about the wake word"], b["messages"]
    assert _assistant(a) and _assistant(b), (
        "one of the two conversations kept no answer at all")


@pytest.mark.asyncio
async def test_going_back_to_a_conversation_reuses_ITS_child(tmp_path,
                                                             fake_openai):
    """AC-59 and AC-241 together, which is the pair the registry exists for.

    Conversation A, conversation B, then back to A: the third turn must reuse
    A's child rather than start a fourth. One child per conversation is what
    makes the spec comparison mean "the child for THIS chat", and a host that
    compared against the LAST child would respawn here -- correctly answering
    the question and paying the spawn every time the owner switched.
    """
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)

    first = chats.current()["id"]
    await _turn(brain, "first conversation", turn_id=1)
    await brain.new_conversation()
    await _turn(brain, "second conversation", turn_id=2)

    brain.session_open(first)
    await _turn(brain, "back in the first", turn_id=3)

    assert len(agents.all) == 2, (
        f"going back to a conversation started a THIRD child instead of "
        f"reusing the one it already holds: {agents.all}")
    assert agents.all[0].prompts == ["first conversation", "back in the first"]
    assert agents.all[1].prompts == ["second conversation"]


@pytest.mark.asyncio
async def test_an_answer_reaches_the_child_that_ASKED(tmp_path, fake_openai):
    """AC-234 meeting AC-241: a question is routed by `qid`, not by "the" child.

    With one child, "the child" was the right answer for free. With two, an
    answer written into whichever child happened to be held is a `select`
    resolved in another project's repository -- and the owner would be told it
    worked.

    What this canNOT prove is two conversations waiting at the same time: one
    turn runs at a time until `docs/parallel-agents.md` phase 6, so the second
    conversation's question cannot be open while the first one's is. What it
    does prove is the routing rule itself, against two live children.
    """
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)
    await _turn(brain, "first conversation", turn_id=1)
    await brain.new_conversation()
    await _turn(brain, "second conversation", turn_id=2)
    assert len(agents.all) == 2, agents.all

    host = brain.host
    host.note_question(chats.current()["id"], "q-second")

    assert host.child_for_question("q-second") is agents.all[1], (
        "the answer would have gone to the wrong conversation's agent")
    assert host.child_for_question("q-nobody-asked") is None, (
        "an unknown question found a child, so a late answer would be "
        "written into an agent that never asked (AC-250)")
    assert await brain.answer_question("q-nobody-asked",
                                       Answer(value="yes")) is False


# --------------------------------------------------------------------------- #
# AC-237 / AC-245 -- the cap, and the sentence it refuses with
# --------------------------------------------------------------------------- #
async def _fill(brain, chats, projects):
    """One conversation per entry, each holding a child, in that project.

    `projects` is a list of directories; each conversation
    is BORN in its project (`new_conversation(project)`, AC-287), the one
    way a conversation gets a project, rather than a hand-built spec -- so
    the per-project counts in the refusal come from the same place they
    would in the room.
    """
    for i, project in enumerate(projects):
        await brain.new_conversation(str(project))
        await _turn(brain, f"question {i}", turn_id=100 + i)


def _dirs(tmp_path, names) -> list[Path]:
    out = []
    for name in names:
        d = tmp_path / name
        d.mkdir()
        out.append(d)
    return out


@pytest.mark.asyncio
async def test_the_cap_refuses_the_eleventh_and_names_where_the_slots_are(
        tmp_path, fake_openai):
    """AC-237 and AC-245, at the shipped default of ten.

    Ten conversations hold a slot, the eleventh is refused, and the refusal is
    the DAEMON's sentence: it names which projects hold the slots and what
    frees one, rather than stating a number. The owner's own question is what
    earned that -- "refusing to create more is fine, but I don't know how to
    KILL/STOP an agent so there are more slots available?" -- so a refusal
    naming only the limit is the defect this asserts against.

    Three separate claims, because a daemon can get any one of them right
    while failing the others: no eleventh child STARTED, nothing was QUEUED
    (the refused text never reaches any child, now or later), and the sentence
    says where the slots are.
    """
    butler, site, data = _dirs(tmp_path, ["butler", "SITE",
                                            "Workspace-Data"])
    prefs = _prefs(tmp_path, projects=[butler, site, data])
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)
    assert prefs["harness_agents_max"] == 10, "the shipped default moved"

    await _fill(brain, chats, [butler] * 4 + [site] * 3 + [data] * 3)
    assert brain.host.live() == 10, brain.host.live()

    await brain.new_conversation()
    eleventh = chats.current()["id"]
    intents = await _turn(brain, "one agent too many", turn_id=999)

    assert len(agents.all) == 10, (
        f"an eleventh child was started at a cap of ten: {len(agents.all)}")
    assert brain.host.live() == 10, brain.host.live()
    assert "one agent too many" not in agents.prompts, (
        "the refused message was sent to an agent anyway")

    said = " ".join(_said(intents))
    assert said.endswith(
        "10 of 10 agents are running. 4 in butler, 3 in SITE, "
        "3 in Workspace-Data. Open one and press stop to free a slot."), said
    assert sum(isinstance(i, Done) for i in intents) == 1, (
        f"a refused turn must still end exactly once (AC-48): {intents}")

    # The record carries the question and the refusal, in that order: a
    # conversation showing a question with nothing under it reads as an agent
    # that ignored the owner.
    record = chats.find(eleventh)
    assert _users(record) == ["one agent too many"], record["messages"]
    assert _assistant(record) and "of 10 agents" in _assistant(record)[-1], (
        f"the refusal was spoken and not kept: {record['messages']}")


@pytest.mark.asyncio
async def test_nothing_is_queued_when_a_slot_frees(tmp_path, fake_openai):
    """AC-237's "nothing is silently queued", asserted after a slot returns.

    A queue is a promise and Butler has no surface that shows one, so the
    refused message must be GONE -- not delivered late when an agent finishes.
    The freed slot is then proved usable by sending again, or "nothing was
    queued" would pass against a daemon that simply never spawns.
    """
    project, = _dirs(tmp_path, ["one"])
    prefs = _prefs(tmp_path, projects=[project], harness_agents_max=2)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [project, project])
    await brain.new_conversation()
    await _turn(brain, "refused text", turn_id=3)
    assert "refused text" not in agents.prompts

    # A slot comes back: the daemon drops one conversation's child the way a
    # barge-in does.
    await brain.host.dispose(chats.find(chats.listing()[-1]["id"])["id"])
    assert brain.host.live() == 1

    await _turn(brain, "asked again", turn_id=4)
    assert agents.prompts[-1] == "asked again", (
        f"the freed slot did not take a new agent: {agents.prompts}")
    assert "refused text" not in agents.prompts, (
        "the refused message was held and delivered when a slot freed -- "
        "that is a queue, and nothing draws it")


@pytest.mark.asyncio
async def test_the_caps_help_text_says_what_this_daemon_ACTUALLY_does(
        tmp_path, fake_openai):
    """The settings page told the owner two things that are not true.

    It said a slot is freed by pressing stop "or let one finish". Measured:
    AC-59 holds a child for the whole CONVERSATION, so letting a turn finish
    is exactly what FILLS the cap -- at a cap of three, three finished
    conversations make Butler say "3 of 3 agents are running" when none is.
    And `Hub._stop` returns early unless the frame names the turn THIS session
    owns (`test_stop.py::test_a_stop_naming_another_turn_changes_nothing` is
    that rule's witness), so a conversation with no turn in flight cannot be
    stopped at all.

    This pins the CLAIM against the BEHAVIOUR in one test, because either one
    alone goes green while the pair disagree. Whether a finished conversation
    should keep its child is the owner's ruling and is open in `handoff.md`;
    what is not open is the daemon describing a mechanism it does not have.
    """
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)
    await _turn(brain, "one whole finished turn", turn_id=1)

    assert brain.host.live() == 1, (
        f"a finished turn released its child, which would make the help "
        f"text's 'let one finish' true and this test the wrong one: "
        f"{brain.host.live()}")

    text = BY_KEY["harness_agents_max"].help
    assert "let one finish" not in text, (
        f"the help still says finishing frees a slot, and finishing is what "
        f"fills the cap: {text!r}")
    assert "delete" in text.lower(), (
        f"the help names no way to free a slot that this daemon actually "
        f"has: {text!r}")


@pytest.mark.asyncio
async def test_deleting_a_conversation_DISPOSES_its_child_and_frees_the_slot(
        tmp_path, fake_openai):
    """AC-241, AC-245: a deleted conversation must not keep a coding agent.

    `session_delete` disposed nothing and reasoned from AC-60 that the next
    turn's `ensure()` would see a changed spec -- but a DELETED conversation
    has no next turn, and no route, no click and no spoken phrase can reach it
    again. Measured at a cap of 2: delete the first conversation and its child
    was still alive and still counted, while the conversation was gone from
    the sidebar and could not be opened or stopped. The only way out was
    restarting the daemon.

    Two assertions and they are different claims: the CHILD is disposed (the
    process itself, which may be holding a repository open), and the SLOT
    comes back, proved by the send that was refused a moment earlier now
    starting an agent.
    """
    project, = _dirs(tmp_path, ["one"])
    prefs = _prefs(tmp_path, projects=[project], harness_agents_max=2)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [project, project])
    assert brain.host.live() == 2, brain.host.live()
    # The OLDEST of the two, which `_fill` birthed first (newest-first list).
    first = chats.listing()[-1]["id"]

    await brain.new_conversation()
    await _turn(brain, "refused at the cap", turn_id=3)
    assert len(agents.all) == 2, agents.all

    assert await brain.session_delete(first) is True, first
    assert agents.all[0].disposals == 1, (
        f"the deleted conversation's coding agent is still running with "
        f"nothing in any UI that can reach it: "
        f"{agents.all[0].disposals} disposal(s)")
    assert agents.all[1].disposals == 0, (
        "deleting one conversation disposed another's agent, which may have "
        "been mid-edit (AC-241)")
    assert brain.host.live() == 1, (
        f"the deleted conversation is still holding a slot: "
        f"{brain.host.live()}")

    await _turn(brain, "asked again", turn_id=4)
    assert agents.prompts[-1] == "asked again", (
        f"the slot the delete should have freed did not take a new agent: "
        f"{agents.prompts}")


@pytest.mark.asyncio
async def test_the_cap_is_a_SETTING_and_takes_effect_on_the_next_turn(
        tmp_path, fake_openai):
    """AC-237. The owner's number, changed while the daemon runs.

    Two claims in one test and they are different: a NON-DEFAULT cap is
    honoured at all, and changing it takes effect on the next thing said with
    no restart -- which is why `harness_agents_max` is not restart-flagged and
    why the cap is a callable rather than a number captured at construction.
    """
    project, = _dirs(tmp_path, ["one"])
    prefs = _prefs(tmp_path, projects=[project], harness_agents_max=2)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [project, project])
    await brain.new_conversation()
    intents = await _turn(brain, "a third", turn_id=3)
    assert len(agents.all) == 2, (
        f"a cap of two started {len(agents.all)} children")
    assert "2 of 2 agents are running" in " ".join(_said(intents)), _said(
        intents)

    # Raised while the daemon is running, and NOT restart-flagged: the value
    # is read at the moment a child is about to start, so there is nothing to
    # reload and a restart badge on this control would be a lie.
    assert BY_KEY["harness_agents_max"].restart is False, (
        "the cap is read per spawn, so flagging it for restart would tell the "
        "owner to restart for a change that already took effect")
    prefs.set("harness_agents_max", 3)
    await _turn(brain, "a third, again", turn_id=4)
    assert len(agents.all) == 3, (
        f"raising the cap did not reach the next turn: {len(agents.all)}")
    assert agents.prompts[-1] == "a third, again"

    # And lowering it refuses the next NEW conversation rather than killing
    # anything that is already running.
    prefs.set("harness_agents_max", 1)
    await brain.new_conversation()
    intents = await _turn(brain, "a fourth", turn_id=5)
    assert len(agents.all) == 3, "lowering the cap started another child"
    assert brain.host.live() == 3, (
        "lowering the cap killed agents that were already running; a cap "
        "refuses new work, it does not reap")
    assert "3 of 1 agents are running" in " ".join(_said(intents)), _said(
        intents)


@pytest.mark.asyncio
async def test_a_replacement_at_the_cap_is_not_an_eleventh_child(
        tmp_path, fake_openai):
    """AC-237, and the ordering rule inside `plan()`.

    A conversation that ALREADY holds a slot is replacing its own child, so
    the count cannot go up and it must never be refused. Three ways in, all
    of them at a full cap: a spec change (a changed `harness_model`; the
    spoken project MOVE that used to be this case is deleted, R-A), the one
    `restart()` replay of a failed turn, and a new conversation, which is the
    one that IS refused because it holds nothing.

    The failure this catches is the obvious implementation -- check the cap
    first, then dispose -- which refuses every turn in every conversation the
    moment the cap is full, so a full Butler answers nothing at all.
    """
    here, there = _dirs(tmp_path, ["here", "there"])
    prefs = _prefs(tmp_path, projects=[here, there], harness_agents_max=2)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [here, here])
    assert brain.host.live() == 2

    # A spec change at the cap: a changed model replaces this
    # conversation's child and stays at two.
    prefs.set("harness_model", "some-other-model")
    intents = await _turn(brain, "moved", turn_id=10)
    assert len(agents.all) == 3, (
        f"the spec change did not replace the child: {agents.all}")
    assert brain.host.live() == 2, brain.host.live()
    assert agents.all[-1].specs[-1].model == "some-other-model"
    assert "agents are running" not in " ".join(_said(intents)), (
        "a conversation replacing its OWN child was refused by the cap")


@pytest.mark.asyncio
async def test_a_failed_turns_ONE_replay_still_works_at_the_cap(tmp_path,
                                                                fake_openai):
    """AC-237 and 4.4c's replay, together.

    A failed prompt is replayed once on a FRESH child (`_Host.restart`), and
    that child is a replacement rather than an addition -- the conversation
    disposes its own before it asks for another. At a full cap the wrong
    implementation refuses the replay, and the room hears the failure line for
    a blip that would have recovered.
    """
    project, = _dirs(tmp_path, ["one"])
    prefs = _prefs(tmp_path, projects=[project], harness_agents_max=2)
    # Only the child the failing turn spawns is broken: its replacement must
    # work, or this measures a broken fake instead of the cap.
    brain, agents, chats, _ = _brain(
        tmp_path, fake_openai, prefs=prefs,
        kwargs_for=lambda i: {"fail_on": "blip"} if i == 1 else {})

    await _fill(brain, chats, [project, project])
    assert brain.host.live() == 2

    intents = await _turn(brain, "a blip please", turn_id=20)
    said = " ".join(_said(intents))
    assert "could not finish" not in said, (
        f"the replay was refused by the cap rather than run: {said}")
    assert "agents are running" not in said, said
    assert len(agents.all) == 3, (
        f"the replay did not get a fresh child: {agents.all}")
    assert brain.host.live() == 2, (
        f"the replay's child was an ADDITION rather than a replacement: "
        f"{brain.host.live()}")
    assert agents.prompts.count("a blip please") == 2, (
        f"the prompt was not replayed verbatim exactly once: {agents.prompts}")


@pytest.mark.asyncio
async def test_the_cap_cannot_be_bypassed_by_going_to_another_project(
        tmp_path, fake_openai):
    """AC-237's second Gherkin: "work in project X" and then speak.

    The cap lives at the only construction site, which is reachable only from
    `turn()`, so there is no second door -- not the route, not the page, not
    the microphone. "work in X" now NAVIGATES (R-C), and when X has no
    conversation it births one there -- the same `new_conversation(project)`
    driven here -- so the conversation it lands in holds nothing, and the
    next question is still refused.
    """
    here, there = _dirs(tmp_path, ["here", "there"])
    prefs = _prefs(tmp_path, projects=[here, there], harness_agents_max=1)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [here])
    await brain.new_conversation(str(there))
    intents = await _turn(brain, "now do the work", turn_id=30)

    assert len(agents.all) == 1, (
        f"going to another project started a child past the cap: "
        f"{agents.all}")
    assert "1 of 1 agents are running" in " ".join(_said(intents)), _said(
        intents)


@pytest.mark.asyncio
async def test_a_waiting_agent_is_named_in_the_refusal(tmp_path, fake_openai):
    """AC-245's second facet: how many of the slots are waiting on YOU.

    A child blocked on a dialog is silent by definition and holds its slot
    anyway -- the accepted cost of D2 landing on D3 -- so the refusal has to
    be able to say so, or the owner reads "agents are running" about an agent
    that is waiting for them.

    The waiting clause is a SECOND facet over the same children rather than a
    bucket of its own: the per-project counts always sum to the cap, and this
    one says how many of them are blocked.
    """
    project, = _dirs(tmp_path, ["one"])
    prefs = _prefs(tmp_path, projects=[project], harness_agents_max=1)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)

    await _fill(brain, chats, [project])
    brain.host.note_question(chats.current()["id"], "q-1")

    await brain.new_conversation()
    intents = await _turn(brain, "refused", turn_id=40)
    said = " ".join(_said(intents))
    assert "1 in one, 1 waiting on you" in said, said

    brain.host.close_question("q-1")
    await brain.new_conversation()
    intents = await _turn(brain, "refused again", turn_id=41)
    assert "waiting on you" not in " ".join(_said(intents)), (
        "the refusal claimed an agent was waiting after the question closed")


# --------------------------------------------------------------------------- #
# AC-245 -- a slot is a child process, through all five states
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_five_states_and_the_live_child_after_each(tmp_path,
                                                             fake_openai):
    """AC-245's slot table, driven, with the one row that DIVERGES named.

    `docs/parallel-agents.md` D3:

        running   holds a slot
        waiting   holds a slot
        done      free   (the child was disposed when the turn ended)
        error     free
        idle      free

    Four of those five are true here and are asserted. **`done` is not**, and
    that is a spec conflict rather than a defect in this code: AC-59 holds a
    child for the whole CONVERSATION, so a conversation that finished a turn
    goes on holding its agent, and under "a slot is a child process" it goes
    on holding a slot. AC-59 is in `plan.md`, is the owner's own reversal of
    AC-46, and was not weakened to satisfy a table in a proposal -- so what is
    asserted is ONE, with the spec's expectation of zero named in the message.

    The consequence, which is the owner's to rule on: nothing frees that slot
    except a failure, a barge-in, a changed spec or a restart, so ten finished
    conversations fill a cap of ten with nothing running.
    """
    prefs = _prefs(tmp_path, harness_agents_max=4)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)
    host = brain.host

    # IDLE: a conversation nobody has spoken into has no child at all.
    assert host.live() == 0, "a conversation held a child before its first turn"

    # RUNNING: mid-turn, with the answer still arriving.
    ctx = TurnContext(turn_id=1, mode="secretary")
    running = brain.turn("what does the endpointing module do", ctx)
    first = await asyncio.wait_for(anext(running), TURN_BUDGET_S)
    assert isinstance(first, Say)
    await asyncio.wait_for(anext(running), TURN_BUDGET_S)
    assert host.live() == 1, (
        f"a running conversation is not holding a child: {host.live()}")

    # DONE: the turn ends. AC-59 KEEPS the child; the spec's table says free.
    async for intent in running:
        if isinstance(intent, Done):
            break
    await running.aclose()
    assert host.live() == 1, (
        f"expected ONE child after a completed turn (AC-59 holds it for the "
        f"whole conversation); docs/parallel-agents.md D3 expects zero, and "
        f"that row is the conflict this test records: {host.live()}")

    # ERROR: a failed turn disposes, and the slot returns with nobody asked.
    broken, failing, chats2, _ = _brain(tmp_path / "b", fake_openai,
                                        prefs=_prefs(tmp_path / "b"),
                                        fail_on="boom")
    intents = await _turn(broken, "boom", turn_id=2)
    assert "could not finish" in " ".join(_said(intents)), _said(intents)
    assert broken.host.live() == 0, (
        f"a failed turn left its child up, so the slot never comes back: "
        f"{broken.host.live()}")

    # WAITING: a child blocked on a dialog holds its slot, doing nothing.
    ask = Ask(qid="q-1", method="confirm", message="ok?")
    asking, blocked, chats3, _ = _brain(tmp_path / "c", fake_openai,
                                        prefs=_prefs(tmp_path / "c"), ask=ask)
    ctx = TurnContext(turn_id=3, mode="secretary")
    # The CONVERSE half directly, not the composite: `Dialogue.turn` wraps this
    # generator in another one, and closing the outer leaves the inner to the
    # garbage collector -- so the dispose in this frame's `finally` would be
    # scheduled rather than done, and the last assertion below would be
    # measuring the collector (test/AGENTS.md).
    waiting = asking.converse.turn("go on", ctx)
    seen = None
    while not isinstance(seen, AskHuman):
        seen = await asyncio.wait_for(anext(waiting), TURN_BUDGET_S)
    assert asking.host.live() == 1, (
        "a conversation waiting on the owner is not holding its child; the "
        "agent is parked on a dialog and the process is up (AC-245)")
    assert asking.host.questions_open(chats3.current()["id"]), (
        "the open question is not registered against the child, so the idle "
        "deadline would end the turn under the card (AC-234)")
    await waiting.aclose()
    assert asking.host.live() == 0, (
        "closing a turn with a question still open left the child behind")


@pytest.mark.asyncio
async def test_the_daemons_teardown_takes_every_child(tmp_path, fake_openai):
    """AC-62 with a child per conversation.

    `butlerd._restart_cleanup` calls `dispose()` on the brain, and `os.execv`
    keeps the process handles: a teardown that dropped only the current
    conversation's child would leave the other nine running with nobody
    holding them.
    """
    brain, agents, chats, _ = _brain(tmp_path, fake_openai)
    await _turn(brain, "one", turn_id=1)
    await brain.new_conversation()
    await _turn(brain, "two", turn_id=2)
    assert brain.host.live() == 2

    await brain.dispose()

    assert brain.host.live() == 0, brain.host.live()
    assert all(child.disposals == 1 for child in agents.all), (
        f"a restart left a coding agent running: "
        f"{[c.disposals for c in agents.all]}")


# --------------------------------------------------------------------------- #
# The one claim the fake cannot make: two REAL processes
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.asyncio
async def test_two_conversations_are_two_REAL_pi_processes(tmp_path,
                                                           fake_openai):
    """AC-241 by PID, which is the only evidence that survives the fake.

    Everything above proves the registry against an object that records what
    it was asked. A slot is a CHILD PROCESS (AC-245), and whether two
    conversations are two processes -- and whether disposing one leaves the
    other alive -- is a fact about the operating system. It reuses
    `test_child_lifetime.py`'s snapshot walk rather than copying it, for that
    file's own reason: two copies of an instrument drift, and a drifted
    instrument passes for the wrong reason. A PID there is paired with its
    creation time, because Windows recycles PIDs freely.

    No prompt is sent. `plan()` plus `ensure()` is the whole spawn path, so
    this costs two pi starts (1.5-2.3 s each [MEASURED]) and no model call at
    all -- which is what makes a real-process assertion affordable here.

    It reaches `PiHarness._child.proc.pid`, which is a private read and is
    stated rather than hidden: nothing on the port exposes a pid, because
    nothing in the daemon needs one. The alternative is no real-process
    witness at all.
    """
    skip_without_pi()
    from test_child_lifetime import _survivors, _taskkill, _tree

    prefs = _prefs(tmp_path)
    chats = ChatStore(tmp_path / "chats")
    started = []

    def mint(_chat=None):
        started.append(ports.build(ports.HARNESSES, "pi", **pi_kwargs()))
        return started[-1]

    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, ()),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        # Same reason as `_brain()` above: both processes share ONE
        # `harness_cwd`, and this test's own subject is AC-241 (two REAL
        # processes), not the per-project limit.
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])
    host = brain.host

    trees = []
    try:
        first = chats.current()
        await asyncio.wait_for(host.ensure(first, await host.plan(first)), 60)
        chats.rotate()
        second = chats.current()
        await asyncio.wait_for(host.ensure(second, await host.plan(second)),
                               60)

        assert host.live() == 2, host.live()
        pids = [child._child.proc.pid for child in started]
        assert len(set(pids)) == 2, f"one process answered for two: {pids}"
        trees = [_tree(pid) for pid in pids]
        assert all(trees), f"a conversation's child is not a process: {trees}"
        assert not ({p.pid for p in trees[0]} & {p.pid for p in trees[1]}), (
            f"the two conversations share a process: {trees}")

        # Disposing ONE leaves the other running. This is the assertion the
        # fake's `disposals` counter stands in for everywhere else, and the
        # only one that can see an agent that was killed mid-edit.
        await asyncio.wait_for(host.dispose(first["id"]), 60)
        assert host.live() == 1, host.live()
        assert _survivors(trees[0]) == [], (
            f"the disposed conversation's process is still up: "
            f"{_survivors(trees[0])}")
        assert _survivors(trees[1]), (
            "disposing one conversation's agent took the other's with it -- "
            "that agent may have been mid-edit (AC-241)")

        await asyncio.wait_for(host.dispose(second["id"]), 60)
        assert _survivors(trees[1]) == [], _survivors(trees[1])
    finally:
        # A red run must not leak a coding agent per attempt onto the machine
        # it is measuring (test/AGENTS.md).
        for tree in trees:
            for proc in _survivors(tree):
                _taskkill(proc.pid)
