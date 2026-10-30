"""A chat BELONGS to a project, from birth (`docs/filing-and-pacing.md` 8,
the owner's rulings R-A, R-B and R-C of 2026-09-26, AC-287..AC-290).

Copied from Orca's model: a session carries an immutable project id set in
the one call that creates it, the working directory is derived from that at
spawn, and "go to project X" navigates and never mutates.

  - birth names the project in ONE call, and a birth naming nothing goes to
    the ROOT row (AC-287);
  - a move is UNREPRESENTABLE: the route, the Hub method, the brain method and
    the store method that re-filed a chat are gone, and no code outside the
    birth, the rewind's copy and the legacy migration writes a record's
    `project` (AC-288);
  - the spawned child's `cwd` is the TURN's conversation's project, in either
    order across two projects (AC-289);
  - "work in X" / "go to X" opens X's newest chat or births one there, changes
    no record's project, stops nothing, and the speaking device is told
    (AC-290).
"""
import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import QuietTts, StubResolver, attach_session     # noqa: E402
from daemon import butlerd, hub as hub_mod, ports               # noqa: E402
from daemon.adapters import brain_harness                       # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain          # noqa: E402
from daemon.adapters.harness_fake import FakeHarness            # noqa: E402
from daemon.chats import ChatStore                              # noqa: E402
from daemon.modes import ModeStore                              # noqa: E402
from daemon.ports import Final                                  # noqa: E402
from daemon.settings import Settings, spoken_project_request    # noqa: E402
from daemon.shortcuts import ShortcutStore                      # noqa: E402

TURN_BUDGET_S = 15.0
DAEMON = Path(__file__).resolve().parents[1] / "daemon"


class _Agents:
    """A FRESH fake per spawn, because one conversation per project may hold
    a child at once and a single shared fake refuses a second `start()`.
    `specs` and `disposals` read across every child, newest last."""

    def __init__(self):
        self.all: list[FakeHarness] = []

    def mint(self, _chat=None) -> FakeHarness:
        self.all.append(FakeHarness())
        return self.all[-1]

    @property
    def specs(self):
        return [spec for child in self.all for spec in child.specs]

    @property
    def disposals(self) -> int:
        return sum(child.disposals for child in self.all)


def _hub(tmp_path, fake_openai):
    """A harness-mode hub with THREE rows: the root (butler), crawler, docs.

    `harness_cwd` is a FOURTH directory on purpose, so a spawn that fell back
    to the default instead of reading the chat's project is visible in the
    spec rather than indistinguishable from success.
    """
    dirs = {n: tmp_path / n for n in ("butler", "crawler", "docs", "scratch")}
    for d in dirs.values():
        d.mkdir()
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(dirs["scratch"]))
    shorts = ShortcutStore(tmp_path / "shortcuts.json")
    shorts.upsert("", str(dirs["crawler"]), "Crawler")
    shorts.upsert("", str(dirs["docs"]), "Docs")
    # The ROOT row, minted the way a boot mints it.
    shorts.migrate_projects(tmp_path / "settings.json", dirs["butler"])
    modes = ModeStore(tmp_path / "mode.json")
    modes.set("secretary", authorized=True)
    child = _Agents()
    chats = ChatStore(tmp_path / "chats")
    chats.migrate_unfiled(dirs["butler"])
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
    rows = {r.label or "Butler": r for r in shorts.all()}
    return hub, child, chats, {n: str(d) for n, d in dirs.items()}, rows


def _row(hub, path):
    return next(r for r in hub.shortcuts.all() if r.path == path)


async def _final(hub, s, turn: int, text: str) -> None:
    s.turn = turn
    await asyncio.wait_for(
        hub._on_final(s, turn, Final(text=text), "silence"), TURN_BUDGET_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), TURN_BUDGET_S)


def _frames(s, kind: str) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == kind]


def _projects(chats) -> dict[str, str]:
    """Every listed conversation's project, as the live store answers it.

    It read a FRESH store off disk until 2026-09-26 -- "what a restart
    sees" -- and that stopped being the question when a conversation with no
    messages stopped being written (the ghost-chat defect): a chat just born
    is in memory only, and a restart rightly forgets it. The birth tests ask
    where a chat was BORN, which is the held record's `project`.
    """
    return {c["id"]: c["project"] for c in chats.listing()}


async def _call(hub, monkeypatch, method: str, path: str, body=None):
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        kw = {} if body is None else {"json": body}
        resp = await client.request(method, path, **kw)
        try:
            return resp.status, await resp.json()
        except Exception:                                  # noqa: BLE001
            return resp.status, {}
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# AC-287: birth names the project, in one call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_new_chat_is_born_in_the_project_it_names(tmp_path,
                                                          fake_openai,
                                                          monkeypatch):
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    crawler = _row(hub, dirs["crawler"])

    status, doc = await _call(hub, monkeypatch, "POST", "/api/session/new",
                              {"project": crawler.id})

    assert status == 200, doc
    assert _projects(_chats)[doc["id"]] == dirs["crawler"], (
        "the new chat was not born in the project the request named")
    assert hub.current_chat_id() == doc["id"]
    assert doc.get("projectId") == crawler.id


@pytest.mark.asyncio
async def test_a_birth_naming_no_project_goes_to_the_root(tmp_path,
                                                          fake_openai,
                                                          monkeypatch):
    """D-A1: R4 already ruled a chat with no project context belongs to
    Butler, and the root row is the one project guaranteed to exist."""
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    # Leave the root first, so "carried from the previous chat" and "the
    # root" are different answers.
    await hub.new_conversation(_row(hub, dirs["docs"]).id)

    status, doc = await _call(hub, monkeypatch, "POST", "/api/session/new")

    assert status == 200, doc
    assert _projects(_chats)[doc["id"]] == dirs["butler"]


@pytest.mark.asyncio
async def test_an_unknown_project_is_refused_and_mints_nothing(tmp_path,
                                                               fake_openai,
                                                               monkeypatch):
    """Refused by CONSEQUENCE: a 400 that minted a chat anyway would pass
    every status assertion."""
    hub, _child, _chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    before = hub.current_chat_id()

    status, doc = await _call(hub, monkeypatch, "POST", "/api/session/new",
                              {"project": "s-nope"})

    assert status == 400, doc
    assert "s-nope" in str(doc)
    assert hub.current_chat_id() == before, "a refused birth rotated anyway"


@pytest.mark.asyncio
async def test_a_project_whose_folder_is_gone_cannot_be_born_in(tmp_path,
                                                                fake_openai,
                                                                monkeypatch):
    """D-A2: the gate `work_in` used to run at the move now runs at birth."""
    hub, _child, _chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    gone = tmp_path / "vanished"
    gone.mkdir()
    row = hub.shortcuts.upsert("", str(gone), "Vanished")
    gone.rmdir()
    before = hub.current_chat_id()

    status, doc = await _call(hub, monkeypatch, "POST", "/api/session/new",
                              {"project": row.id})

    assert status == 400, doc
    assert hub.current_chat_id() == before


@pytest.mark.asyncio
async def test_a_page_birth_clears_the_routers_short_memory(tmp_path,
                                                           fake_openai,
                                                           monkeypatch):
    """ONE definition of a birth (D-18). The spoken "new conversation" always
    cleared `hub.history` (AC-64) and the route never did, so the page's New
    chat prompted the new conversation's router with the old one's turns."""
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    hub.history.append({"role": "user", "content": "the old conversation"})

    status, _doc = await _call(hub, monkeypatch, "POST", "/api/session/new",
                               {"project": _row(hub, dirs["docs"]).id})

    assert status == 200
    assert list(hub.history) == []


# --------------------------------------------------------------------------- #
# AC-288: a move is unrepresentable
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_move_route_is_gone(tmp_path, fake_openai, monkeypatch):
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    before = _projects(_chats)

    status, _doc = await _call(hub, monkeypatch, "POST", "/api/project",
                               {"id": _row(hub, dirs["crawler"]).id})

    assert status in (404, 405), f"POST /api/project still answers {status}"
    assert _projects(_chats) == before


def test_no_method_that_moves_a_chat_exists():
    """The NAMES are gone, so a caller written tomorrow cannot reach them."""
    assert not hasattr(ChatStore, "set_project")
    assert not hasattr(hub_mod.Hub, "switch_project")
    assert not hasattr(brain_harness._Host, "work_in")
    assert not hasattr(brain_harness.HarnessBrain, "work_in")
    assert not hasattr(brain_harness.HarnessOnlyBrain, "work_in")


# Every function in the daemon allowed to WRITE a record's `project`. The
# birth (`rotate`), the rewind's copy (the birth of a copy), and the one-time
# filing of blank legacy records. Anything else writing it is a move.
PROJECT_WRITERS = {("chats.py", "rotate"), ("chats.py", "rewind"),
                   ("chats.py", "migrate_unfiled")}


def _project_writes(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "project"):
                    found.add((path.name, fn.name))
    return found


def test_only_the_birth_writes_a_records_project():
    """An AST sweep of the whole daemon, so the rule holds for code that does
    not exist yet: a new writer fails here before it ships."""
    writers = set()
    for path in DAEMON.rglob("*.py"):
        writers |= _project_writes(path)
    assert writers == PROJECT_WRITERS, (
        f"something other than a birth writes a chat's project: "
        f"{sorted(writers - PROJECT_WRITERS)}")


@pytest.mark.asyncio
async def test_a_spoken_switch_moves_no_record(tmp_path, fake_openai):
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    await _final(hub, s, 1, "what does the endpointing module do")
    before = _projects(_chats)

    await _final(hub, s, 2, "work in project crawler")

    after = _projects(_chats)
    assert {k: after[k] for k in before} == before, (
        "a spoken switch re-filed an existing conversation")


# --------------------------------------------------------------------------- #
# AC-289: cwd is derived from the turn's conversation at spawn
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_agent_runs_where_the_conversation_belongs(tmp_path,
                                                             fake_openai):
    hub, child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    first = (await hub.new_conversation(_row(hub, dirs["crawler"]).id))["id"]
    # Said into, or the next rotation reaps it as a blank record.
    hub.brain.host.record(first, "user", "a crawler question")
    second = (await hub.new_conversation(_row(hub, dirs["docs"]).id))["id"]
    fake_openai.verdict(route="converse")

    assert hub.open_conversation(second) is not None
    await _final(hub, s, 1, "what is in here")
    assert hub.open_conversation(first) is not None
    await _final(hub, s, 2, "and what is in here")
    assert hub.open_conversation(second) is not None
    await _final(hub, s, 3, "and back again")

    # WHICH child answered each prompt, and where it runs. The third turn is
    # answered by the docs conversation's HELD child (AC-59), so asserting on
    # the spawn list alone would miss where it ran.
    where = {p: c.specs[-1].cwd for c in child.all for p in c.prompts}
    assert where == {"what is in here": dirs["docs"],
                     "and what is in here": dirs["crawler"],
                     "and back again": dirs["docs"]}, (
        f"the agent did not follow the conversation's project: {where}")


def test_the_host_holds_no_working_directory_of_its_own(tmp_path,
                                                        fake_openai):
    """R-B: `_cwd` was the second copy of a fact the record already holds."""
    hub, _child, _chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    assert not hasattr(hub.brain.host, "_cwd")


# --------------------------------------------------------------------------- #
# AC-290: "work in X" / "go to X" navigates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,expected", [
    ("go to project crawler", ("crawler", True)),
    ("go to crawler", ("crawler", False)),
    ("work in project crawler", ("crawler", True)),
])
def test_go_to_is_a_project_phrase(said, expected):
    heard = spoken_project_request(said)
    assert heard is not None and (heard.name, heard.explicit) == expected


@pytest.mark.asyncio
async def test_work_in_opens_the_projects_newest_chat(tmp_path, fake_openai):
    hub, child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    crawler = _row(hub, dirs["crawler"]).id
    older = (await hub.new_conversation(crawler))["id"]
    hub.brain.host.record(older, "user", "older crawler chat")
    newer = (await hub.new_conversation(crawler))["id"]
    hub.brain.host.record(newer, "user", "newer crawler chat")
    home = (await hub.new_conversation(None))["id"]
    hub.brain.host.record(home, "user", "a butler chat")
    s = attach_session(hub)
    s.showing = home
    fake_openai.verdict(route="converse")
    await _final(hub, s, 1, "what is in here")
    live_before = hub.brain.host.live()
    before = _projects(_chats)

    await _final(hub, s, 2, "work in crawler")

    assert s.showing == newer, "the speaking device is not showing the chat"
    assert [f["chat"] for f in _frames(s, "opened")] == [newer], (
        "the page was not told which conversation it is now on")
    assert hub.current_chat_id() == newer
    after = _projects(_chats)
    assert {k: after[k] for k in before} == before, "navigation re-filed a chat"
    assert hub.brain.host.live() == live_before and child.disposals == 0, (
        "navigation stopped a held agent")


@pytest.mark.asyncio
async def test_go_to_a_project_with_no_chats_births_one_there(tmp_path,
                                                              fake_openai):
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    s = attach_session(hub)

    await _final(hub, s, 1, "go to project docs")

    opened = _frames(s, "opened")
    assert len(opened) == 1, opened
    assert _projects(_chats)[opened[0]["chat"]] == dirs["docs"]
    assert s.showing == opened[0]["chat"]


@pytest.mark.asyncio
async def test_the_next_turn_after_navigating_runs_in_that_project(
        tmp_path, fake_openai):
    hub, child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    s.showing = hub.current_chat_id()

    await _final(hub, s, 1, "work in project crawler")
    fake_openai.verdict(route="converse")
    await _final(hub, s, 2, "what is in here")

    assert child.specs[-1].cwd == dirs["crawler"], child.specs


@pytest.mark.asyncio
async def test_an_unknown_project_is_still_refused_and_navigates_nowhere(
        tmp_path, fake_openai):
    hub, _child, _chats, _dirs, _rows = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    before = hub.current_chat_id()

    await _final(hub, s, 1, "go to project crawlr")

    assert _frames(s, "opened") == []
    assert hub.current_chat_id() == before
    said = [f["text"].lower() for f in _frames(s, "reply")]
    assert any("crawlr" in t for t in said), said


@pytest.mark.asyncio
async def test_a_spoken_new_conversation_is_born_where_the_device_is(
        tmp_path, fake_openai):
    """D-C2: the spoken "new conversation" tells the device, and is born in
    the project of the conversation that device was on."""
    hub, _child, _chats, dirs, _rows = _hub(tmp_path, fake_openai)
    docs = (await hub.new_conversation(_row(hub, dirs["docs"]).id))["id"]
    s = attach_session(hub)
    s.showing = docs

    await _final(hub, s, 1, "new conversation")

    opened = _frames(s, "opened")
    assert len(opened) == 1 and opened[0]["chat"] != docs, opened
    assert s.showing == opened[0]["chat"], (
        "the device went on showing the old chat, so the next turn would have "
        "gone back into it")
    assert _projects(_chats)[opened[0]["chat"]] == dirs["docs"]
