"""The spoken project switch. AC-31, docs/harness-mode.md 7.7, AC-290.

"Work in project X" NAVIGATES since 2026-09-26 (`docs/filing-and-pacing.md`
8, R-C): it opens X's newest conversation, or births one there, and the next
coding agent runs in X because that conversation belongs to X. It used to
MOVE the conversation the owner was in; that move is deleted (R-A), and
`test_chat_belongs_to_project.py` carries the criteria that replaced it. It is
still a security boundary wearing a convenience's clothes: the cost of
guessing is a tool-enabled agent writing in the wrong checkout. Everything
here is about failing CLOSED.

  - matching is EXACT on the spoken form of a CONFIGURED project. No fuzz, no
    prefix, no nearest neighbour, and the test carries a near-miss name;
  - a name that matches nothing is refused ALOUD, names what was heard, and
    changes nothing at all -- not the child, not `harness_cwd`;
  - an empty project list refuses every switch, which is a daemon whose pane
    has no rows;
  - the phrase is inert outside harness mode, where there is nothing to go to;
  - and going somewhere never re-files the conversation being left.

The clause about a move revoking the spoken write grant went with AC-29 on
2026-08-18: there is no grant to revoke, and the project list is now the only
thing bounding where the agent runs.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, StubResolver, attach_session,   # noqa: E402
                      make_hub)
from daemon import butlerd, hub as hub_mod, ports               # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain          # noqa: E402
from daemon.adapters.harness_fake import FakeHarness            # noqa: E402
from daemon.modes import ModeStore                              # noqa: E402
from daemon.shortcuts import Shortcut, ShortcutStore             # noqa: E402
from daemon.ports import Final                                  # noqa: E402
from daemon.chats import ChatStore                        # noqa: E402
from daemon.settings import (AmbiguousProject, Settings,        # noqa: E402
                             match_project, spoken_project_request)

TURN_BUDGET_S = 15.0


def _projects(tmp_path) -> tuple[str, str]:
    """Two real directories, spoken as their final component (7.7)."""
    butler = tmp_path / "butler"
    crawler = tmp_path / "smart-crawler"
    butler.mkdir()
    crawler.mkdir()
    return str(butler), str(crawler)


def _store(tmp_path, paths) -> ShortcutStore:
    """The project list, which since 2026-09-11 is the shortcuts pane's ROWS.

    A real store and not a stub list: this is the ONE table both of AC-31's
    gates read, and a fixture that faked it would prove the gates work against
    something the daemon does not use (the owner's backlog item 4).
    """
    store = ShortcutStore(tmp_path / "shortcuts.json")
    for path in paths:
        store.upsert("", path)
    return store


class _Agents:
    """A FRESH fake per spawn. "work in X" navigates (R-C), so the
    conversation being left KEEPS its child while X's gets one -- two live at
    once, and one shared fake refuses a second `start()`. `specs` and
    `prompts` read across every child, oldest first."""

    def __init__(self):
        self.all: list[FakeHarness] = []

    def mint(self, _chat=None) -> FakeHarness:
        self.all.append(FakeHarness())
        return self.all[-1]

    @property
    def specs(self):
        return [spec for child in self.all for spec in child.specs]

    @property
    def prompts(self):
        return [p for child in self.all for p in child.prompts]


def _hub(tmp_path, fake_openai, projects=None, mode="secretary"):
    """A hub in harness mode with a configured project list."""
    butler, crawler = _projects(tmp_path)
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", butler)
    shorts = _store(tmp_path,
                    [butler, crawler] if projects is None else projects)
    modes = ModeStore(tmp_path / "mode.json")
    modes.set(mode, authorized=True)
    child = _Agents()
    chats = ChatStore(tmp_path / "chats")
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url),
        harness_for=child.mint,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, shorts.paths),
        chats=chats,
        cap_for=lambda: prefs["harness_agents_max"])
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=brain, modes=modes,
                      asr_config=ports.AsrConfig(model="test"), settings=prefs,
                      shortcuts=shorts)
    return hub, child, prefs, (butler, crawler)


async def _final(hub, s, turn: int, text: str) -> None:
    s.turn = turn
    await asyncio.wait_for(
        hub._on_final(s, turn, Final(text=text), "silence"), TURN_BUDGET_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), TURN_BUDGET_S)


def _said(s) -> list[str]:
    return [json.loads(m).get("text", "") for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == "reply"]


# --------------------------------------------------------------------------- #
# the refusal, which is the whole criterion (AC-31)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_near_miss_is_refused_aloud_and_nothing_changes(tmp_path,
                                                                fake_openai):
    """"smartcrawlr" is not "smart crawler", and the difference matters.

    A near miss is the shape a real misrecognition takes, and it is exactly
    where a fuzzy matcher would put a tool-enabled agent in a repository
    nobody named. Refused OUT LOUD -- silence would read as done -- naming
    what was heard, and nothing moves: not the child, not the setting.

    The EXPLICIT form, because that is the one that can only be navigation.
    The bare "work in smartcrawlr" is an instruction for the agent and goes
    to it; see `test_only_an_explicit_switch_refuses_a_name_it_does_not_know`.

    "Nothing moves" is asserted on the NEXT turn's child since AC-46 was
    replaced: the refusal itself starts nothing, so where the agent actually
    runs is only observable the next time one is spawned.
    """
    hub, child, prefs, (butler, _) = _hub(tmp_path, fake_openai)
    await hub.brain.warm()
    s = attach_session(hub)

    await _final(hub, s, 1, "work in project smartcrawlr")

    assert child.specs == [], (
        f"a name that matches nothing spawned a child: {child.specs}")
    assert prefs.get("harness_cwd") == butler, "the setting was rewritten"
    assert any("smartcrawlr" in t.lower() for t in _said(s)), (
        f"the refusal was not spoken, or did not name what was heard: "
        f"{_said(s)}")
    assert child.prompts == [], (
        f"the refused switch was handed to the coding agent instead: "
        f"{child.prompts}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the refusal did not re-arm (D-1)")

    fake_openai.verdict(route="converse")
    await _final(hub, s, 2, "what does the endpointing module do")
    assert child.specs[-1].cwd == butler, (
        f"the working directory moved on a name that matches nothing: "
        f"{child.specs[-1].cwd}")


@pytest.mark.asyncio
async def test_an_empty_projects_list_refuses_every_switch(tmp_path,
                                                           fake_openai):
    """The shipped state. Nothing configured means nothing is reachable by
    voice, including the directory the harness is already running in.

    Asked explicitly, because with no projects at all the bare form can only
    be work: an empty list would otherwise refuse every sentence that starts
    "work on".
    """
    hub, child, prefs, (butler, crawler) = _hub(tmp_path, fake_openai,
                                                projects=[])
    await hub.brain.warm()
    s = attach_session(hub)

    await _final(hub, s, 1, "work in project smart crawler")
    await _final(hub, s, 2, "work in project butler")
    fake_openai.verdict(route="converse")
    await _final(hub, s, 3, "what does the endpointing module do")

    assert len(child.specs) == 1, (
        f"the two refusals spawned children of their own: {child.specs}")
    assert child.specs[-1].cwd == butler, (
        f"an unconfigured project was reachable by voice: {child.specs}")
    assert sum("do not have" in t.lower() for t in _said(s)) == 2, (
        f"both switches were not refused out loud: {_said(s)}")


# --------------------------------------------------------------------------- #
# the happy half
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_known_project_takes_the_next_turn_there(tmp_path,
                                                         fake_openai):
    """pi fixes its working directory at spawn, so going to a project can only
    ever reach the NEXT child. Turn 1 establishes where the agent was; turn 2
    goes to the project; turn 3 is the assertion, because the spec of the
    child that answers is the only honest account of where the work ran.

    A DIFFERENT conversation now (R-C): crawler had none, so one was born
    there, and the conversation turn 1 was in keeps its own project and its
    own session file.
    """
    hub, child, prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    await hub.brain.warm()
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    await _final(hub, s, 1, "what does the endpointing module do")
    assert child.specs[-1].cwd == butler, child.specs
    was = child.specs[-1].session_file

    await _final(hub, s, 2, "work in smart crawler")

    assert any("smart crawler" in t.lower() for t in _said(s)), (
        f"going there was never confirmed out loud: {_said(s)}")
    assert prefs.get("harness_cwd") == butler, (
        "a spoken switch rewrote the default working directory, so the next "
        "restart would come back somewhere the owner never chose")

    fake_openai.verdict(route="converse")
    await _final(hub, s, 3, "and which policy is not wired up")
    assert child.specs[-1].cwd == crawler, (
        f"the turn after going there did not run in the named project: "
        f"{child.specs}")
    assert child.specs[-1].session_file != was, (
        "the turn in crawler ran in the conversation that was left, which "
        "means that conversation was MOVED rather than navigated away from")
    assert child.prompts == ["what does the endpointing module do",
                             "and which policy is not wired up"], (
        f"the switch phrase reached the coding agent: {child.prompts}")


# --------------------------------------------------------------------------- #
# most sentences beginning "work on" are WORK (AC-31)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,refused", [
    # Ordinary work. The bare form names something that is not a configured
    # project, so it is an instruction for the agent and not navigation.
    ("work on the endpointing module", False),
    ("work in butler and fix the failing tests", False),
    ("switch to the other branch", False),
    # The EXPLICIT form can only be navigation, so a name that matches
    # nothing is still refused out loud -- the near miss AC-31 is about.
    ("work in project smartcrawlr", True),
])
@pytest.mark.asyncio
async def test_only_an_explicit_switch_refuses_a_name_it_does_not_know(
        tmp_path, fake_openai, said, refused):
    """A refusal is for navigation, never for work.

    Found by review: "work on the endpointing module" was answered with "I do
    not have a project called the endpointing module" and never reached the
    agent, which makes the feature a liability -- most sentences that begin
    "work on" are work. So the BARE form is intercepted only when the name
    matches a configured project exactly, and everything else falls through
    to the agent. The explicit form ("work in project X") can only be
    navigation, so it keeps the spoken refusal, which is AC-31 itself and is
    not weakened here.
    """
    hub, child, prefs, (butler, _) = _hub(tmp_path, fake_openai)
    await hub.brain.warm()
    s = attach_session(hub)
    fake_openai.verdict(route="converse")

    await _final(hub, s, 1, said)

    spoke_refusal = any("do not have" in t.lower() for t in _said(s))
    assert spoke_refusal is refused, (
        f"{said!r} was {'not ' if refused else ''}refused out loud: "
        f"{_said(s)}")
    assert child.prompts == ([] if refused else [said]), (
        f"{said!r} reached the coding agent: {child.prompts}")
    # A refused navigation phrase spawns nothing at all (AC-46), and work
    # spawns exactly one child -- in the default project either way.
    assert [spec.cwd for spec in child.specs] == ([] if refused else [butler]), (
        f"{said!r} moved the working directory: {child.specs}")


@pytest.mark.asyncio
async def test_the_switch_is_inert_outside_harness_mode(tmp_path, fake_openai):
    """Endpoint mode has no child and no working directory, so the phrase is
    an ordinary utterance and goes to the brain like anything else."""
    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")
    hub.shortcuts = _store(tmp_path, [str(tmp_path)])
    s = attach_session(hub)

    await _final(hub, s, 1, "work in smart crawler")

    assert [text for text, _turn in hub.brain.seen] == [
        "work in smart crawler"], (
        f"the daemon answered a phrase it has nothing to do with: "
        f"{hub.brain.seen}")


# --------------------------------------------------------------------------- #
# where the work happens after a move (AC-31)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_work_after_going_there_runs_in_that_repository(tmp_path,
                                                              fake_openai):
    """The project list is now the ONLY bound on where the agent writes.

    This test used to assert that moving revoked the spoken write grant; the
    owner retired that ceremony on 2026-08-18 (AC-29), so the claim that
    survives is the one AC-31 always made and the grant merely rode on: a
    writing instruction after "work in X" is carried out by the child in the
    NAMED repository, with no ceremony in between -- in X's own conversation,
    since the phrase navigates (R-C) rather than moving the one being left.
    """
    hub, child, prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    await hub.brain.warm()
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    await _final(hub, s, 1, "what does the endpointing module do")
    was = child.specs[-1].session_file

    await _final(hub, s, 2, "work in smart crawler")

    fake_openai.verdict(route="dispatch",
                        action={"summary": "rename it here", "writes": True})
    await _final(hub, s, 3, "rename the endpointing module")

    # The router's CLEANED instruction, not the transcript (AC-73, AC-28,
    # 2026-08-19). This line read `"rename the endpointing module"` until the
    # dispatch route started prompting the child with `action.summary`; what
    # it asserts is unchanged -- the instruction reached the moved child --
    # and only which string carries it has moved. The two differ here on
    # purpose, so this test also fails if the cleaning is silently dropped.
    assert child.prompts[-1] == "rename it here", (
        f"the writing instruction did not reach the moved child: "
        f"{child.prompts}")
    assert child.specs[-1].cwd == crawler, (
        f"the write ran somewhere other than the named project: "
        f"{child.specs[-1].cwd}")
    assert child.specs[-1].session_file != was, (
        "the write ran in the conversation that was left, re-filed into "
        "crawler -- a move, which R-A deleted")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the dispatched turn did not re-arm (D-1)")


# --------------------------------------------------------------------------- #
# the matcher itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,expected", [
    ("work in project butler", ("butler", True)),
    ("work in butler", ("butler", False)),
    ("Work in project smart crawler.", ("smart crawler", True)),
    ("switch to project butler", ("butler", True)),
    ("work on project butler", ("butler", True)),
    ("work on the endpointing module", ("the endpointing module", False)),
    ("what does the endpointing module do", None),
    ("I would work in butler if I could", None),
    ("work in", None),
    ("", None),
])
def test_the_phrase_is_recognised_only_at_the_start(said, expected):
    """A prefix at the START, and a name after it. A phrase found anywhere in
    an utterance would move a tool-enabled agent because somebody described
    what they were doing.

    The second half of the answer is whether the phrasing said "project",
    which is what decides whether an unknown name is refused aloud or handed
    to the agent as work.
    """
    heard = spoken_project_request(said)
    got = None if heard is None else (heard.name, heard.explicit)
    assert got == expected


def _row(path, label=""):
    """A bare `Shortcut`, the real row shape `match_project` is handed in
    `hub.py` -- not a stub, so a test proving matching works proves it against
    the same object the daemon reads `.path` and `.label` off of."""
    return Shortcut(id="s-" + path, path=path, label=label)


@pytest.mark.parametrize("heard,wins", [
    ("butler", True),
    ("smart crawler", True),
    ("SMART CRAWLER", True),
    ("smartcrawlr", False),
    ("smart crawlers", False),
    ("crawler", False),           # not a prefix match, not a substring match
    ("smart", False),
    ("", False),
])
def test_matching_is_exact_on_the_spoken_form(tmp_path, heard, wins):
    """EXACT, on the normalized final path component: lower case, and `-` and
    `_` read as spaces. Nothing fuzzy goes near a security boundary (AC-31)."""
    rows = [_row(str(tmp_path / "butler")), _row(str(tmp_path / "smart-crawler")),
            _row(str(tmp_path / "some_other_thing"))]
    found = match_project(heard, rows)
    assert (found is not None) is wins, f"{heard!r} matched {found!r}"
    if wins:
        assert found in [r.path for r in rows]


def test_underscores_and_dashes_are_spoken_the_same_way(tmp_path):
    """Nobody says "some underscore other". Both separators read as a space,
    which is the assumption 7.7 records: a project is a directory spoken by
    its final component."""
    rows = [_row(str(tmp_path / "some_other_thing"))]
    assert match_project("some other thing", rows) == rows[0].path


def test_a_hyphenated_typed_name_matches_the_folder_too(tmp_path):
    """The HEARD/typed side gets the SAME '-'/'_' -> ' ' normalization as the
    folder side. Before this, only the folder name was normalized, so a
    typed "e2e-proj-a" never matched a spoken "e2e proj a" would
    (docs/e2e-journeys.md S11)."""
    row = _row(str(tmp_path / "e2e-proj-a"))
    assert match_project("e2e-proj-a", [row]) == row.path


def test_the_rows_own_label_is_matched_too(tmp_path):
    """A row relabelled "classes" over a folder called CRS101 is reachable by
    the name the TREE SHOWS, not only by its folder name -- the owner's own
    unreachable project (docs/e2e-journeys.md S11)."""
    row = _row(str(tmp_path / "CRS101"), label="classes")
    assert match_project("classes", [row]) == row.path
    assert match_project("crs101", [row]) == row.path


def test_a_label_and_a_different_rows_folder_name_are_still_ambiguous(tmp_path):
    """Two DIFFERENT directories answering to one spoken name is refused
    whether both sides matched by folder name, both by label, or -- as here
    -- one of each."""
    a = _row(str(tmp_path / "CRS101"), label="classes")
    b = _row(str(tmp_path / "classes"))
    with pytest.raises(AmbiguousProject):
        match_project("classes", [a, b])


def test_a_row_matching_itself_on_both_sides_is_not_ambiguous(tmp_path):
    """A row whose label happens to equal its own folder name answers to one
    name from two directions -- that is one project, not two (deduped on
    PATH before the ambiguity count)."""
    row = _row(str(tmp_path / "butler"), label="butler")
    assert match_project("butler", [row]) == row.path


# --------------------------------------------------------------------------- #
# ONE DIRECTORY, ONE RULE (AC-31, AC-218; review 2026-09-11)
#
# There were THREE equality rules for one directory and the strictest of them
# was the spawn gate. MEASURED against a scratch daemon, with the conversations
# already filed and the ROW then re-spelled:
#
#   row spelling                 listing files it   agent may spawn there
#   E:\PROJECTS\butler           True               True
#   E:\PROJECTS\butler\          True               False
#   e:\projects\butler           True               False
#   E:/PROJECTS/butler           False              False   <- orphans outright
#
# So a row the listing happily filed conversations under was a directory the
# coding agent refused to start in, and `brain_harness._session_cwd` then
# swallowed that refusal and started the child in `harness_cwd` while the rail
# went on saying the conversation lived in the row.
#
# BOTH COLUMNS ARE ASSERTED IN ONE TEST on purpose. The defect is that the two
# answers disagreed, so a witness that could only see one of them would have
# been green for three of the four rows while the feature was broken.
# --------------------------------------------------------------------------- #
def _spellings(path: str) -> list[tuple[str, str]]:
    r"""The four ways the owner and this repository's own docs write one path.

    Named, so a failure says WHICH spelling rather than quoting two strings
    that differ by one character.
    """
    return [("as typed", path),
            ("trailing separator", path + "\\"),
            ("lower case", path.lower()),
            ("forward slashes", path.replace("\\", "/"))]


@pytest.mark.parametrize("how,spelled", _spellings("E:\\PROJECTS\\butler"),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_one_rule_answers_for_one_directory_on_both_sides(tmp_path, how,
                                                          spelled):
    r"""A row's spelling decides NOTHING (AC-218, AC-31).

    The conversation was filed under `E:\PROJECTS\butler` -- the spelling the
    record already carries -- and the ROW is then spelled four different ways,
    which is what the pane's own edit button produces. Every spelling must
    answer the same on both sides:

      * `ShortcutStore.row_for` files the conversation under the row, and
      * `harness_spec_resolver` lets a child start in the recorded directory.

    The literal `E:\...` is deliberate rather than a `tmp_path`: nothing here
    touches the filesystem, the case-folding row needs a path with upper case
    in it that a temp directory cannot be relied on to have, and the drive
    letter is what the owner's own rows look like.
    """
    recorded = "E:\\PROJECTS\\butler"
    store = ShortcutStore(tmp_path / "shortcuts.json")
    row = store.upsert("", spelled, label="Butler")

    filed = store.row_for(recorded)
    assert filed is not None and filed.id == row.id, (
        f"the {how} spelling files NOTHING: a conversation recorded as "
        f"{recorded} is in no workspace while the row says {spelled}")

    resolve = butlerd.harness_spec_resolver(
        Settings(tmp_path / "settings.json"),
        ChatStore(tmp_path / "chats"), store.paths)
    spec = resolve(recorded)
    assert spec.cwd == recorded, (
        f"the {how} spelling files the conversation and then REFUSES a child "
        f"in it, which is the disagreement this test exists for")


@pytest.mark.parametrize("how,spelled", _spellings("E:\\PROJECTS\\butler"),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_a_directory_matching_no_row_is_still_refused(tmp_path, how, spelled):
    r"""The gate did not get WEAKER, which is the other half of AC-31.

    Normalising separators must not turn the membership test into "anything
    that looks like a path". A sibling directory, a directory whose name merely
    starts with the row's, and a path that only shares a prefix are all still a
    refusal -- whatever the configured row is spelled like.
    """
    store = ShortcutStore(tmp_path / "shortcuts.json")
    store.upsert("", spelled, label="Butler")
    resolve = butlerd.harness_spec_resolver(
        Settings(tmp_path / "settings.json"),
        ChatStore(tmp_path / "chats"), store.paths)

    for stranger in ("E:\\PROJECTS\\other-project",
                     "E:\\PROJECTS\\butler-two",
                     "E:\\PROJECTS",
                     "E:\\PROJECTS\\butler\\daemon",
                     "E:/PROJECTS/butler-two"):
        with pytest.raises(ValueError):
            resolve(stranger)
        assert store.row_for(stranger) is None, (
            f"{stranger} is not {spelled} and the listing said it was")


@pytest.mark.asyncio
@pytest.mark.parametrize("how,respell",
                         [("trailing separator", lambda p: p + "\\"),
                          ("upper case", lambda p: p.upper()),
                          ("forward slashes", lambda p: p.replace("\\", "/"))])
async def test_a_RESPELT_row_still_spawns_where_the_CONVERSATION_says(
        tmp_path, fake_openai, how, respell):
    r"""The silent fallback, and it is the half a page cannot see (AC-31).

    `brain_harness._session_cwd` reads the project off the RECORD after a
    restart, asks the spawn gate whether it is still usable, and on a refusal
    starts the child in `harness_cwd` instead -- loudly in the console and
    invisibly everywhere else. So while the gate compared raw strings, editing a
    row's path was enough to move a running coding agent out of the repository
    the rail said it was in.

    `harness_cwd` is the OTHER project here, so the fallback is visible in the
    spec rather than being indistinguishable from success. The brain is built
    FRESH over the existing record, which is what a restart looks like -- and
    since R-B (2026-09-26) the record is the ONLY source of the directory, so
    this branch is every spawn's, not a restart's alone.
    """
    butler, crawler = _projects(tmp_path)
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", crawler)
    shorts = _store(tmp_path, [butler, crawler])
    row = next(r for r in shorts.all() if r.path == butler)

    chats = ChatStore(tmp_path / "chats")
    chats.rotate(butler)                    # born in butler (AC-287)
    chats.append(chats.current()["id"], "user",
                 "where does this conversation live")
    shorts.upsert(sid=row.id, path=respell(butler))

    child = FakeHarness()
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url),
        harness_for=lambda _chat=None: child,
        spec_for=butlerd.harness_spec_resolver(prefs, chats, shorts.paths),
        chats=chats,
        cap_for=lambda: prefs["harness_agents_max"])

    assert brain.host.desired().cwd == butler, (
        f"the row re-spelled with a {how} sent this conversation's coding "
        f"agent to {brain.host.desired().cwd} -- the `harness_cwd` fallback -- "
        f"while the rail went on saying it works in {butler}")


def test_the_resolver_refuses_a_directory_nobody_configured(tmp_path):
    """The SECOND gate, on the path every spawn takes (AC-31).

    The hub matches the spoken name, and this checks the answer again before
    a `HarnessSpec` exists. Two gates rather than one because the first is a
    branch in a turn and the second is the only thing between an adapter
    holding a string and a child running in it -- and only the second is on
    the path a future caller would also take.
    """
    butler, crawler = _projects(tmp_path)
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", butler)
    shorts = _store(tmp_path, [butler])
    resolve = butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"), shorts.paths)

    assert resolve(butler).cwd == butler
    with pytest.raises(ValueError):
        resolve(crawler)
    with pytest.raises(ValueError):
        resolve(str(tmp_path / "somewhere-else"))


def test_a_blank_working_directory_defaults_to_the_tmp_scratch_dir(tmp_path):
    """Blank `harness_cwd` DEFAULTS to `<repo>/TMP` (AC-31, amended 2026-08-18).

    The resolver used to RAISE on a blank working directory. The owner ruled it
    should default to a scratch dir instead: a child can always start somewhere
    harmless, so a blank setting no longer means no child at all. The dir is
    created if it does not exist, and the default is the scratch dir -- never a
    guess at a NAMED project, which stays refused aloud (the test above).
    """
    prefs = Settings(tmp_path / "settings.json")     # harness_cwd left blank
    resolve = butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"))
    spec = resolve()
    assert spec.cwd == str(butlerd.REPO / "TMP")
    assert (butlerd.REPO / "TMP").is_dir(), "the scratch dir was not created"


def test_an_explicit_working_directory_still_wins_over_the_tmp_default(tmp_path):
    """An explicit `harness_cwd` beats the scratch default (AC-31)."""
    butler = tmp_path / "butler"
    butler.mkdir()
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(butler))
    resolve = butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"))
    assert resolve().cwd == str(butler)


def test_the_harness_tools_setting_drives_the_spec_tool_posture(tmp_path):
    """`harness_tools` is a plain persistent knob, not the retired grant (AC-29).

    read_only resolves to the read allow-list on the spec; full to no
    restriction. Read per message like every other harness setting, and
    defaulting to read_only so a wake-word false accept cannot reach a write
    tool until the owner flips it (owner's decision 2026-08-18). This is NOT
    the spoken grant AC-29 retired -- no ceremony, no permission-mode coupling.
    """
    butler = tmp_path / "butler"
    butler.mkdir()
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(butler))
    resolve = butlerd.harness_spec_resolver(
        prefs, ChatStore(tmp_path / "chats"))

    assert resolve().tools == ("read", "grep", "find", "ls"), (
        "the shipped default is read_only")

    prefs.set("harness_tools", "full")
    assert resolve().tools == (), "full means no restriction"

    prefs.set("harness_tools", "read_only")
    assert resolve().tools == ("read", "grep", "find", "ls")


@pytest.mark.asyncio
async def test_a_mode_phrase_still_reaches_the_mode_store(tmp_path,
                                                          fake_openai):
    """Three whole-utterance matchers now share `_on_final`, and the oldest
    must keep working: a spoken turn mode is answered by the daemon and never
    by the coding agent (7.7's "keeps passing")."""
    hub, child, prefs, _ = _hub(tmp_path, fake_openai)
    await hub.brain.warm()
    s = attach_session(hub)

    await _final(hub, s, 1, "switch to long form")
    await _final(hub, s, 2, "new conversation")

    assert hub.settings.turn_mode == "endword", "the mode phrase was swallowed"
    assert child.prompts == [], (
        f"a daemon-answered phrase went to the coding agent: {child.prompts}")


def test_the_three_spoken_matchers_are_disjoint(tmp_path):
    """No utterance may mean two things, and not because of branch ORDER.

    This test used to assert disjointness over the bare phrases only, which
    was a claim about a set the collision was not in: `spoken_mode_request`
    strips a leading "switch to " and `spoken_project_request` treats it as a
    prefix, so "switch to long form" was BOTH -- and only the order the two
    branches were written in decided which one the owner got. A test
    asserting something false is worse than no test, so the overlap is
    removed in the matcher and the phrases that carried it are asserted here.
    """
    from daemon.settings import (SPOKEN_MODES, SPOKEN_NEW_CONVERSATION,
                                 spoken_mode_request, spoken_new_conversation)

    phrases = [p for group in SPOKEN_MODES.values() for p in group]
    phrases += list(SPOKEN_NEW_CONVERSATION)
    # The whole-utterance phrases themselves, AND every way of introducing
    # them that the project matcher also recognises as a prefix.
    phrases += [f"{prefix}{p}"
                for prefix in ("switch to ", "go to ", "use ", "change to ")
                for p in [q for group in SPOKEN_MODES.values() for q in group]
                + list(SPOKEN_NEW_CONVERSATION)]
    for phrase in phrases:
        assert spoken_project_request(phrase) is None, (
            f"{phrase!r} is both a project switch and something else, so "
            f"which one the owner gets is decided by branch order")
    for phrase in ("work in project butler", "work in butler",
                   "work on the endpointing module"):
        assert spoken_mode_request(phrase) is None, phrase
        assert spoken_new_conversation(phrase) is False, phrase


# --------------------------------------------------------------------------- #
# A CLICK NAMES THE PROJECT AT BIRTH (`docs/filing-and-pacing.md` 8, AC-287)
#
# These three drove `POST /api/project`, the clicked MOVE, until 2026-09-26.
# The move is deleted (R-A); what the page clicks now is "+ New chat" on a
# project, which names that project in the one call that creates the chat.
# What they protected still applies to that call and is kept: the id is a KEY
# of what the daemon listed, never a path, and a refusal is proved by its
# consequence rather than its status.
# --------------------------------------------------------------------------- #
async def _call(method: str, path: str, body=None) -> tuple[int, dict]:
    """Drive the real route and answer `(status, payload)`.

    The body is read BEFORE the client closes, `test_dictate.py`'s rule:
    reading it after the `finally` raises `ClientConnectionError`, which reads
    like a daemon fault and is a fault in the harness.
    """
    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        resp = await client.request(method, path, json=body)
        try:
            return resp.status, await resp.json()
        except Exception:                                  # noqa: BLE001
            return resp.status, {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_clicking_new_chat_on_a_project_births_it_there(
        tmp_path, fake_openai, monkeypatch):
    """The conversation KNOWS where it is from its first instant, which is the
    half a terminal launch never had."""
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    row = next(r for r in hub.shortcuts.all() if r.path == crawler)

    status, payload = await _call("POST", "/api/session/new",
                                  {"project": row.id})

    assert status == 200, payload
    assert payload["project"] == crawler
    assert hub.brain.session_transcript()["project"] == crawler


@pytest.mark.asyncio
async def test_a_project_id_nobody_has_births_nothing(tmp_path, fake_openai,
                                                      monkeypatch):
    """Refused by its CONSEQUENCE and not by its status.

    A route that answered 400 and minted a conversation anyway satisfies every
    status assertion that could be written here.
    """
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    before = hub.current_chat_id()

    status, payload = await _call("POST", "/api/session/new",
                                  {"project": "s-nope"})

    assert status == 400, payload
    assert "s-nope" in str(payload), (
        f"the refusal does not name the id it refused: {payload!r}")
    assert hub.current_chat_id() == before


@pytest.mark.asyncio
async def test_the_route_takes_an_ID_and_never_a_PATH(tmp_path, fake_openai,
                                                      monkeypatch):
    """`ChatStore.find`'s rule, met again (backlog item 4).

    The id is a KEY of what the daemon LISTED, resolved by ENUMERATING the
    rows -- so a directory the owner never configured is an id no row has,
    rather than a path this route joins onto anything. A caller that could name
    a directory would be AC-31's second gate reached from a new door.
    """
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    before = hub.current_chat_id()

    outside = str(tmp_path / "somewhere-else")
    status, payload = await _call("POST", "/api/session/new",
                                  {"project": crawler, "path": outside,
                                   "cwd": outside})

    assert status == 400, payload
    assert hub.current_chat_id() == before
    assert not any(spec.cwd == outside for spec in child.specs), (
        f"a caller named a directory and reached the spawn: {child.specs}")


def test_a_conversation_carries_the_project_it_belongs_to(tmp_path):
    """One field on the record, set at BIRTH (AC-287), and old records read
    as "no project".

    The owner chose this over MOVING the file into the project folder
    (2026-09-11): it buys the sidebar its grouping with no change to
    `_attach`, `_records`, `session_ref`, the rewind's rename or `rotate`, and
    it is reversible in one commit.
    """
    chats = ChatStore(tmp_path / "chats")
    assert chats.current()["project"] == ""
    assert chats.listing()[0]["project"] == ""

    chats.rotate(str(tmp_path))
    assert chats.current()["project"] == str(tmp_path)
    assert chats.listing()[0]["project"] == str(tmp_path)

    # Written through with its first message, so a restart does not forget
    # where the conversation is. Before one, it is in memory only: a record
    # with no messages is never on disk (the ghost-chat defect).
    chats.append(chats.current()["id"], "user", "something was said")
    again = ChatStore(tmp_path / "chats")
    assert again.current()["project"] == str(tmp_path)


# --------------------------------------------------------------------------- #
# WHAT THE 2026-09-11 REVIEW FOUND (the record and the agent disagreeing)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_restarted_daemon_answers_from_the_recorded_project(
        tmp_path, fake_openai):
    """The record is the half that SURVIVES, so the agent has to read it.

    A move used to live in `_Host._cwd`, this process's memory, and a restart
    re-attached to the same chat file with a new host -- so the page showed the
    workspace while the next child spawned in `harness_cwd` (AC-7b). Since R-B
    the record is the ONLY source (`_cwd` is deleted); this keeps proving a
    restarted host reads it.
    """
    hub, child, prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    await _final(hub, s, 1, "work in project smart crawler")
    assert hub.brain.session_transcript()["project"] == crawler
    # Something said in it, or there is no record for a restart to find: a
    # chat with no messages is never on disk (the ghost-chat defect).
    hub.brain.host.record(hub.brain.session_transcript()["id"], "user",
                          "what is in here?")

    # A SECOND host over the SAME chat directory is what a restart is: new
    # process memory, same record on disk.
    chats = ChatStore(tmp_path / "chats")
    shorts = _store(tmp_path, [butler, crawler])
    restarted = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url),
        harness_for=lambda _chat=None: FakeHarness(),
        spec_for=butlerd.harness_spec_resolver(prefs, chats, shorts.paths),
        chats=chats,
        cap_for=lambda: prefs["harness_agents_max"])
    assert restarted.host.desired().cwd == crawler, (
        "a restarted daemon forgot which project the conversation belongs to "
        "and went back to the default working directory")


@pytest.mark.asyncio
async def test_a_new_conversation_stays_in_the_same_project(tmp_path,
                                                            fake_openai):
    """A birth that names no project carries the one being left
    (`ChatStore.rotate`'s None).

    The spoken "new conversation" names the project of the conversation the
    device is on (D-A1); this is the brain-level default underneath it, and a
    blank project here would send the agent silently back to the default
    directory the first time a new conversation is started.
    """
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    await _final(hub, s, 1, "work in project smart crawler")

    await hub.brain.new_conversation()

    assert hub.brain.session_transcript()["project"] == crawler, (
        "the new conversation forgot the workspace while the agent stayed in "
        "it")
    assert hub.brain.host.desired().cwd == crawler


@pytest.mark.asyncio
async def test_a_project_whose_folder_is_gone_is_refused_at_birth(
        tmp_path, fake_openai):
    """`POST /api/terminal` has refused a missing directory since AC-148, and
    a birth refuses it too (D-A2) -- the gate `work_in` used to run at the
    move -- so the owner is never told "working in X" and left to discover on
    the next question that no child can start there.
    """
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    monkeypatched = tmp_path / "vanished"
    monkeypatched.mkdir()
    hub.shortcuts.upsert("", str(monkeypatched), "Vanished")
    monkeypatched.rmdir()
    row = next(r for r in hub.shortcuts.all()
               if r.path == str(monkeypatched))

    before = hub.current_chat_id()
    with pytest.raises(ValueError):
        await hub.new_conversation(row.id)

    assert hub.current_chat_id() == before, (
        "a conversation was born in a directory that does not exist")
    assert not any(spec.cwd == str(monkeypatched) for spec in child.specs)


@pytest.mark.asyncio
async def test_two_projects_with_one_spoken_name_are_refused_aloud(
        tmp_path, fake_openai):
    """AC-31's rule reaching the case the pane made possible.

    Two rows can share a final path component -- which the SETTING never
    really could, being one hand-curated list -- and picking either is exactly
    the guess AC-31 exists to refuse. Asserted by its CONSEQUENCES: nothing
    moved, and the refusal is its OWN sentence, because "I have no project
    called that" and "I have two" need different next actions from the owner.
    """
    hub, child, _prefs, (butler, crawler) = _hub(tmp_path, fake_openai)
    twin = tmp_path / "elsewhere" / "butler"
    twin.mkdir(parents=True)
    hub.shortcuts.upsert("", str(twin), "Other butler")
    s = attach_session(hub)
    said = []
    hub._speak = lambda sess, turn, text, tier: said.append(text) or asyncio.sleep(0)

    await _final(hub, s, 1, "work in project butler")

    assert said and "more than one" in said[0].lower(), (
        f"an ambiguous project was not refused with its own sentence: {said}")
    assert hub.brain.session_transcript()["project"] == ""
    assert child.specs == [] or all(spec.cwd != str(twin)
                                    for spec in child.specs)
