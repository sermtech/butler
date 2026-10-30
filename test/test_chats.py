"""The chat record: one flat JSON file per conversation. AC-35, plan.md 4.4c.

docs/harness-mode.md 10.2, by the owner's ruling of 2026-08-18. This file
replaces `test_harness_sessions.py`, and what it no longer tests is the point
of the ruling: there is no `meta.json`, no append-only `events.jsonl`, no
rotation-as-a-fresh-directory, no numbered `harness-N.json`, and no
`new`/`resumed`/`lost` vocabulary on the wire. The page can no longer be told
whether the AGENT still remembers this conversation, and that loss is
recorded in 10.5 rather than faked here.

Four properties carry the replacement:

  - the conversation continues across turns and across a daemon RESTART, on
    the chat with the newest `updatedAt` -- which IS the explicit last-active
    stamp 4.4c demands, now one field instead of a ceremony;
  - a spoken "new conversation" mints a new FILE, and the old one's messages
    do not come with it;
  - a corrupt or unreadable record is LOUD and never fatal (D-1): a
    conversation history cannot make Butler unsafe;
  - the agent's own memory comes back through `sessionRef`, which only a
    real child can prove and which the `slow` test at the bottom does.
"""
import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (QuietTts, StubResolver, attach_session,   # noqa: E402
                      pi_kwargs, skip_without_pi)
from daemon import butlerd, chats, hub as hub_mod, ports        # noqa: E402
from daemon import shortcuts as shortcuts_mod                   # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain          # noqa: E402
from daemon.adapters.harness_fake import FakeHarness            # noqa: E402
from daemon.modes import ModeStore                              # noqa: E402
from daemon.ports import Final                                  # noqa: E402
from daemon.settings import Settings, spoken_new_conversation   # noqa: E402

TURN_BUDGET_S = 15.0


# --------------------------------------------------------------------------- #
# harnesses
# --------------------------------------------------------------------------- #
def _prefs(tmp_path):
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    return prefs


class _Agents:
    """Every fake child one brain has spawned, with the records aggregated.

    A conversation holds a child of its OWN since AC-241, so a fixture that
    hands the same fake back on every spawn refuses `start()` the moment a
    second conversation takes a turn -- which is what the spoken "new
    conversation" now is. This mints one per spawn and aggregates `specs`,
    `prompts` and `disposals` across them, which is exactly what every
    assertion in this file was already reading: while only one child could
    exist, one fake's records WERE the aggregate.
    """

    def __init__(self) -> None:
        self.all: list[FakeHarness] = []

    def mint(self, _chat=None) -> FakeHarness:
        self.all.append(FakeHarness())
        return self.all[-1]

    @property
    def specs(self) -> list:
        return [spec for child in self.all for spec in child.specs]

    @property
    def prompts(self) -> list:
        return [p for child in self.all for p in child.prompts]

    @property
    def disposals(self) -> int:
        return sum(child.disposals for child in self.all)


def _brain(tmp_path, fake_openai, store=None, harness=None, prefs=None):
    """A harness brain over one chat store. No hub: most of AC-35 is below
    the protocol, and a hub here would only add turn bookkeeping."""
    prefs = prefs if prefs is not None else _prefs(tmp_path)
    store = store if store is not None else chats.ChatStore(tmp_path / "chats")
    child = harness if harness is not None else _Agents()
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url),
        harness_for=(lambda _chat=None: harness) if harness is not None else child.mint,
        spec_for=butlerd.harness_spec_resolver(prefs, store),
        chats=store,
        cap_for=lambda: prefs["harness_agents_max"])
    return brain, child, store


def _hub(tmp_path, fake_openai, store=None, harness=None, shortcuts=None):
    """The same brain behind a real Hub, for the spoken half.

    `shortcuts` is the PROJECT TABLE, and it is optional because most of this
    file predates workspaces: a Hub built without one has no rows, which is
    what every daemon that does not know the pane exists looks like.
    """
    prefs = _prefs(tmp_path)
    modes = ModeStore(tmp_path / "mode.json")
    modes.set("secretary", authorized=True)
    brain, child, store = _brain(tmp_path, fake_openai, store=store,
                                 harness=harness, prefs=prefs)
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=brain, modes=modes,
                      asr_config=ports.AsrConfig(model="test"), settings=prefs,
                      shortcuts=shortcuts)
    return hub, child, store


async def _answer(brain, text: str) -> list[str]:
    """One turn through the converse half, past the router.

    The router is not the subject of this file, and a real verdict per turn
    would put the scripted server's mood in front of a question about what a
    chat file carries.
    """
    ctx = ports.TurnContext(turn_id=0, mode="secretary")
    return [i.text async for i in brain.proceed(text, ctx)
            if isinstance(i, ports.Say) and i.spoken]


async def _final(hub, s, turn: int, text: str) -> None:
    """One whole turn, from the recognizer's final to the re-arm, on a
    deadline: a turn that never ends is what this path fails at, and a test
    that hangs on it reports nothing at all (test/AGENTS.md)."""
    s.turn = turn
    await asyncio.wait_for(
        hub._on_final(s, turn, Final(text=text), "silence"), TURN_BUDGET_S)
    if s.reply_task is not None:
        await asyncio.wait_for(asyncio.shield(s.reply_task), TURN_BUDGET_S)


def _said(s) -> list[str]:
    return [json.loads(m).get("text", "") for m in s.ws.sent
            if isinstance(m, str) and json.loads(m).get("type") == "reply"]


def _users(store) -> list[str]:
    return [m["content"] for m in store.messages() if m["role"] == "user"]


@contextlib.asynccontextmanager
async def _http(hub, monkeypatch):
    """The daemon's HTTP surface over one hub, for the chat routes (AC-164).

    Bodies are read INSIDE the block: reading `.json()` after the client is
    closed raises `ClientConnectionError`, which reads like a daemon fault
    and is a fault in the harness (test/AGENTS.md).
    """
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _msg(role: str, content: str, ts: int = 1) -> dict:
    return {"id": f"msg-{role}-{ts}", "role": role, "content": content,
            "timestamp": ts}


def _on_disk(root: Path, cid: str, updated: int, messages: list,
             harness: str = "fake", project: str = "") -> str:
    """One chat record written straight to disk, as a past conversation is.

    Written rather than appended through the store, because what AC-164
    serves is what a PREVIOUS run of the daemon left behind -- a fixture
    that used the live store could only ever produce the current chat.

    `project` is the absolute path the birth wrote, verbatim, because that is
    the only thing a record carries about its workspace (AC-218) -- the ROW is
    derived from it per request and is deliberately not on disk.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{cid}.json").write_text(json.dumps(
        {"id": cid, "createdAt": 1, "updatedAt": updated, "harness": harness,
         "sessionRef": "", "project": project, "messages": messages}),
        encoding="utf-8")
    return cid


# --------------------------------------------------------------------------- #
# the conversation survives (AC-35)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_turns_accumulate_in_one_chat_file(tmp_path, fake_openai):
    """Two turns, one file, and the same reference handed to both children.

    A conversation that minted a new reference per message would give the
    agent amnesia between questions -- which is exactly what a per-message
    child would look like if the reference did not outlive it (AC-46).
    """
    brain, child, store = _brain(tmp_path, fake_openai)

    await asyncio.wait_for(_answer(brain, "first question"), TURN_BUDGET_S)
    await asyncio.wait_for(_answer(brain, "second question"), TURN_BUDGET_S)

    files = list((tmp_path / "chats").glob("chat-*.json"))
    files = [f for f in files if not f.name.endswith(".session.json")]
    assert len(files) == 1, f"two turns wrote {len(files)} chat files: {files}"
    assert _users(store) == ["first question", "second question"], (
        f"the record is not the conversation: {store.messages()}")
    assert store.turns() == 2, store.summary()
    assert len({s.session_file for s in child.specs}) == 1, (
        f"each message was pointed at a different reference, so the agent "
        f"forgets between questions: {child.specs}")


@pytest.mark.asyncio
async def test_a_restart_reattaches_to_the_newest_updated_at(tmp_path,
                                                             fake_openai):
    """Re-attach reads `updatedAt`, never a position in a sorted list (4.4c).

    THREE chats, and the wanted one is in the MIDDLE by name, because two
    positions are wrong and a fixture with only two files can only rule out
    one of them. An earlier version of this test put the wanted chat FIRST by
    name and claimed to catch "whatever the directory listed first"; it did
    not -- mutating the comparison to `if best is None` kept every assertion
    green, which is D-13's own failure mode (an instrument that survives the
    defect it is named after). Now the newest NAME is stale, the oldest NAME
    is stale, and only `updatedAt` picks the conversation anybody was having.
    That exact defect resumed conversations nobody was having.

    **IT SURVIVED THE REGISTRY UNCHANGED, and the spec predicted it would go
    red** (`docs/parallel-agents.md` phase 4). "The live conversation" did
    become plural, but `_attach` still chooses the ONE a boot comes back
    holding, so every assertion here is the claim it always was and is kept
    verbatim. What changed is what it no longer says: this is not the only
    conversation the store can write to any more, and
    `test_a_restart_HOLDS_ONE_conversation_and_the_rest_are_openable` is the
    half that says so.
    """
    root = tmp_path / "chats"
    root.mkdir(parents=True)
    oldest_name = {"id": "chat-0000000000000-aaaaaa", "createdAt": 1,
                   "updatedAt": 5, "harness": "fake", "sessionRef": "",
                   "messages": [{"id": "msg-0", "role": "user",
                                 "content": "the month before last",
                                 "timestamp": 5}]}
    wanted = {"id": "chat-1000000000000-mmmmmm", "createdAt": 1,
              "updatedAt": 9000, "harness": "fake", "sessionRef": "",
              "messages": [{"id": "msg-1", "role": "user",
                            "content": "the one we were having",
                            "timestamp": 9000}]}
    stale = {"id": "chat-9000000000000-zzzzzz", "createdAt": 1,
             "updatedAt": 10, "harness": "fake", "sessionRef": "",
             "messages": [{"id": "msg-2", "role": "user",
                           "content": "last week", "timestamp": 10}]}
    for chat in (oldest_name, wanted, stale):
        (root / f"{chat['id']}.json").write_text(json.dumps(chat), "utf-8")

    store = chats.ChatStore(root)

    assert store.current()["id"] == wanted["id"], (
        f"the store re-attached to {store.current()['id']}, which is not the "
        f"conversation anybody was having (AC-35, 4.4c)")
    assert _users(store) == ["the one we were having"], store.messages()

    # And the transcript survives the restart through the brain, which is the
    # claim the page actually depends on.
    brain, _, _ = _brain(tmp_path, fake_openai, store=store)
    assert brain.session_state() == {"session": wanted["id"], "turns": 1}
    assert brain.session_transcript()["messages"][0]["content"] == (
        "the one we were having")


@pytest.mark.asyncio
async def test_the_reference_survives_the_restart_and_the_harness_names_it(
        tmp_path, fake_openai):
    """The AGENT's memory half: `sessionRef` is captured once and re-served.

    Named per harness rather than fixed, because a pi session file means
    nothing to a claude child and handing one over would resume a
    conversation into a reader that cannot read it (AC-44).
    """
    root = tmp_path / "chats"
    first, child, store = _brain(tmp_path, fake_openai,
                                 store=chats.ChatStore(root))
    await asyncio.wait_for(_answer(first, "remember this"), TURN_BUDGET_S)
    was = child.specs[-1].session_file

    again, child2, store2 = _brain(tmp_path, fake_openai,
                                   store=chats.ChatStore(root))
    await asyncio.wait_for(_answer(again, "and now"), TURN_BUDGET_S)

    assert store2.current()["id"] == store.current()["id"], "a new chat file"
    assert child2.specs[-1].session_file == was, (
        f"the restarted daemon pointed the child somewhere else, so the "
        f"agent lost the conversation: {child2.specs[-1].session_file} "
        f"was {was}")
    # Named for the HARNESS setting, which is what would actually be spawned
    # in production -- the fake stands in for the child, not for the setting.
    assert f"-{_prefs(tmp_path).get('harness')}.session.json" in Path(was).name, (
        f"the reference is not named for the harness that owns it: {was}")
    assert _users(store2) == ["remember this", "and now"], store2.messages()


# --------------------------------------------------------------------------- #
# rotation: a new conversation is a spoken command, matched whole (AC-35)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,expected", [
    ("new conversation", True),
    ("New conversation.", True),
    ("start a new conversation", True),
    ("new chat", True),
    ("let's talk about something else", False),
    ("add a new conversation view to the settings page", False),
    ("what does new conversation mean", False),
    ("", False),
])
def test_the_rotation_phrase_is_matched_on_the_whole_utterance(said, expected):
    """The same rule `spoken_mode_request` follows: a passing mention is not
    a command. Throwing the agent's context away mid-sentence because
    somebody described the feature is the failure this prevents."""
    assert spoken_new_conversation(said) is expected


@pytest.mark.asyncio
async def test_new_conversation_mints_a_new_file(tmp_path, fake_openai):
    """Spoken, answered by the DAEMON, and said back out loud.

    No rotation machinery survives: a new conversation is a new FILE (10.2).
    The turn AFTER the rotation is where it becomes observable, because
    rotating is now a record operation with no child in it (AC-46), so what
    proves the agent moved is what the NEXT child is pointed at.
    """
    hub, child, store = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    await _final(hub, s, 1, "what does the endpointing module do")
    was = store.current()["id"]
    was_ref = child.specs[-1].session_file

    await _final(hub, s, 2, "new conversation")
    fake_openai.verdict(route="converse")
    await _final(hub, s, 3, "so what were we saying")

    assert store.current()["id"] != was, "the conversation did not rotate"
    files = [f for f in (tmp_path / "chats").glob("chat-*.json")
             if not f.name.endswith(".session.json")]
    assert len(files) == 2, (
        f"a new conversation is a new FILE, and the old one stays: {files}")
    assert child.specs[-1].session_file != was_ref, (
        f"the child kept the old reference, so the agent is still in the old "
        f"conversation: {child.specs[-1].session_file}")
    assert hub_mod.Hub.NEW_CONVERSATION in _said(s), (
        f"the rotation was never spoken: {_said(s)}")
    assert child.prompts == ["what does the endpointing module do",
                             "so what were we saying"], (
        f"the rotation phrase went to the coding agent: {child.prompts}")
    assert s.state == hub_mod.IDLE and s.turn is None, (
        "the rotation turn did not re-arm (D-1)")
    assert _users(store) == ["so what were we saying"], (
        f"the fresh conversation started with the old one's messages in it: "
        f"{store.messages()}")


@pytest.mark.asyncio
async def test_the_spoken_rotation_backstops_an_unfiled_outgoing_conversation(
        tmp_path, fake_openai):
    """R5's daemon-side backstop (`docs/filing-and-pacing.md` finding 3,
    D-18): the spoken "new conversation" phrase and the page's New chat
    button both mint through the SAME `ChatStore.rotate()`, so a fix there
    covers both callers without a second copy of the rule. Neither may ever
    produce a chat with no project -- even if the OUTGOING conversation
    somehow reached this turn unfiled, which is the exact gap the page's
    `newConversation()` can leave open (`shortcuts.root_id` not loaded yet,
    so it never calls `postProject` at all) and which this test forces
    directly rather than relying on that race actually landing.
    """
    hub, child, store = _hub(tmp_path, fake_openai)
    s = attach_session(hub)
    app_dir = tmp_path / "app"
    # Tell the store where root is (as `migrate_unfiled` would at boot)
    # BEFORE anything is unfiled, so this exercises `rotate()`'s own
    # backstop rather than `migrate_unfiled` filing the record itself.
    store.migrate_unfiled(str(app_dir))
    await _final(hub, s, 1, "what does the endpointing module do")
    # Force the live conversation back to unfiled -- simulating a caller
    # upstream of this store failing to file it, which is the failure mode
    # this backstop exists for regardless of how it is reached.
    store.current()["project"] = ""

    await _final(hub, s, 2, "new conversation")

    assert store.current()["project"] == str(app_dir), (
        "a conversation rotated in with no project to inherit and no "
        "backstop -- R5's 'no unprojected conversations' invariant broke")


# --------------------------------------------------------------------------- #
# loud, never fatal (D-1)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_corrupt_chat_record_is_loud_and_not_fatal(tmp_path, capsys):
    """A conversation history cannot make Butler unsafe.

    Unlike `modes.py`, which fails fast for the opposite reason: a mode that
    cannot be read must not be guessed, while a transcript that cannot be
    read costs the transcript and never the microphone.
    """
    root = tmp_path / "chats"
    root.mkdir(parents=True)
    (root / "chat-1000000000000-aaaaaa.json").write_text("{ not json",
                                                         encoding="utf-8")
    (root / "chat-1000000000001-bbbbbb.json").write_text('"a string"',
                                                          encoding="utf-8")

    store = chats.ChatStore(root)

    out = capsys.readouterr().out.lower()
    assert out.count("unreadable") == 2, (
        f"an unreadable chat record was skipped SILENTLY, which is how a "
        f"conversation disappears without anybody knowing: {out}")
    assert store.current()["messages"] == [], (
        "the store carried on with something it could not read")
    store.append(store.current()["id"], "user", "and Butler still answers")
    assert _users(store) == ["and Butler still answers"], (
        "a corrupt record on disk stopped the daemon recording the next turn")


@pytest.mark.asyncio
async def test_a_write_failure_is_loud_and_never_costs_the_turn(tmp_path,
                                                                fake_openai,
                                                                capsys):
    """A failure to persist is a failure to be RESUMABLE, and no more (D-1).

    An answer nobody hears is worse than a conversation nobody can resume, so
    the turn carries on and the failure goes to the log.
    """
    brain, _, store = _brain(tmp_path, fake_openai)
    # A file where the store wants a directory: every write fails from here.
    store.root = tmp_path / "not-a-directory"
    store.root.write_text("in the way", encoding="utf-8")

    said = await asyncio.wait_for(_answer(brain, "answer me anyway"),
                                  TURN_BUDGET_S)

    assert said, "a record that could not be written silenced the turn"
    assert "could not write the chat record" in capsys.readouterr().out.lower(), (
        "the failure to persist the conversation was swallowed")


def test_only_a_missing_session_is_read_as_one():
    """The narrow regex, and the reason it is narrow (4.4c, 10.2).

    Clearing a stored reference costs the agent its context, so the gate is
    the harness's LITERAL words. A rule that fired on "not found" would throw
    a conversation away because a tool could not find a file. INERT for pi,
    which creates a missing session file rather than refusing it; it is here
    for the claude adapter, which re-earns it on day one (10.7).
    """
    assert chats.names_a_missing_session(
        "No conversation found with session ID: abc123")
    assert chats.names_a_missing_session("Session not found")
    assert not chats.names_a_missing_session("ENOENT: file not found")
    assert not chats.names_a_missing_session("the provider returned 503")
    assert not chats.names_a_missing_session("")


def test_deleting_an_empty_held_conversation_does_not_cry_wolf(tmp_path,
                                                               capsys):
    """MEASURED 2026-09-15, twice, in the owner's daemon log:

        [butler] chat chat-...-1bc5be deleted from the page (0 turns, title '')
        [butler] WARNING: could not remove .../chat-...-1bc5be.json ([WinError
                 2] The system cannot find the file specified ...) The
                 conversation is gone from the list and the file is still
                 there; the microphone is unaffected (D-1)

    The file was NOT still there, and the same line said so. `delete` on a
    HELD conversation rotates first, and `rotate` DISCARDS an outgoing
    conversation nobody ever spoke in -- so the record is unlinked once by the
    rotation and `delete` then reaps the SAME path. The message sends whoever
    reads it looking for a file that does not exist.
    """
    store = chats.ChatStore(tmp_path / "chats")
    held = store.current()["id"]
    assert store.delete(held) is True, (
        "the store did not find the conversation it is holding")
    out = capsys.readouterr().out
    assert "deleted from the page" in out, out
    assert "WARNING" not in out, (
        f"deleting a held conversation nobody ever spoke in reported a failure "
        f"that did not happen: {out}")
    assert not (tmp_path / "chats" / f"{held}.json").exists()


def test_a_real_removal_failure_is_still_loud(tmp_path, capsys, monkeypatch):
    """The other half, so the fix for the row above cannot be "never warn".

    A record that IS on disk and cannot be removed -- a lock, a permission --
    is a file that really is still there, and this project's contract is that
    the failure is LOUD and never fatal (D-1).
    """
    store = chats.ChatStore(tmp_path / "chats")
    past = store.current()["id"]
    store.append(past, "user", "something was said")
    store.rotate()                 # `past` is a PAST conversation now
    assert (tmp_path / "chats" / f"{past}.json").exists()

    def refuse(self, *a, **kw):
        raise PermissionError(13, "in use by another process")

    monkeypatch.setattr(chats.Path, "unlink", refuse)
    assert store.delete(past) is True
    out = capsys.readouterr().out
    assert "WARNING" in out and "still there" in out, (
        f"a file that really could not be removed went unreported: {out}")


@pytest.mark.asyncio
async def test_the_transcript_the_route_serves_is_bounded(tmp_path,
                                                          fake_openai,
                                                          monkeypatch):
    """A conversation grows forever; the page shows eight turns (AC-35).

    The NEWEST end is what is served, because that is the part the page
    renders, and `ready`'s `turns` still counts the whole conversation -- the
    count and the transcript are allowed to disagree.
    """
    from aiohttp.test_utils import TestClient, TestServer

    hub, _, store = _hub(tmp_path, fake_openai)
    for i in range(butlerd.MESSAGES_SERVED + 40):
        store.append(store.current()["id"], "user", f"turn {i}")

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        doc = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()

    # `questions` arrived with AC-249 and is the recovery story in one field:
    # what a coding agent is BLOCKED on rides on the conversation's own
    # document, so a reloading page and a second device both draw the card
    # through the route they were already asking.
    # `title` arrived 2026-09-15 with the pane's own header: the route named
    # no conversation, so a reload showed a real transcript under the words
    # "current conversation" while the row beside it named the same chat.
    assert set(doc) == {"id", "harness", "project", "projectId", "origin",
                        "projectLabel", "title", "messages", "questions",
                        "agent", "agent_label", "agents", "agent_default",
                        "agent_fixed", "model",
                        "model_default"}, (
        f"the route serves the chat file plus `_filed`'s annotation and "
        f"nothing else (10.2): {set(doc)}")
    assert len(doc["messages"]) == butlerd.MESSAGES_SERVED, (
        f"the route served {len(doc['messages'])} messages for a page that "
        f"shows eight turns")
    said = [m["content"] for m in doc["messages"]]
    assert f"turn {butlerd.MESSAGES_SERVED + 39}" in said, (
        "the OLDEST end of the record was served, so the page redisplays a "
        "conversation that ended long ago")
    assert "turn 0" not in said, "the window is not a window"
    assert hub.brain.session_state()["turns"] == butlerd.MESSAGES_SERVED + 40, (
        "the turn count was cut down with the transcript")


@pytest.mark.asyncio
async def test_the_live_header_and_the_row_name_a_conversation_the_same_way(
        tmp_path, fake_openai, monkeypatch):
    """ONE rule for what a conversation is called (AC-164's shape, 2026-09-15).

    The pane's own header and the row that opens it are the same fact drawn
    twice. Measured on the scratch surface before this existed: a reload put a
    real conversation on screen under the header "current conversation" while
    the row beside it showed the owner's own first words -- because
    `/api/session/current` carried no title and the page fell back to its own
    placeholder. The row path and the header path are now the SAME function
    (`ChatStore.title`), which is the only version of this that cannot drift.
    """
    from aiohttp.test_utils import TestClient, TestServer

    hub, _, store = _hub(tmp_path, fake_openai)
    store.append(store.current()["id"], "user", "please fix the rail")
    expected = "please fix the rail"

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        current = await (await client.get("/api/session/current")).json()
        listed = await (await client.get("/api/chats")).json()
        opened = await (await client.post(
            "/api/session/open", json={"id": current["id"]})).json()
    finally:
        await client.close()

    assert current["title"] == expected, (
        f"the live conversation is served with {current['title']!r}, so the "
        f"pane draws its own placeholder over a real conversation")
    row = next(c for c in listed["chats"] if c["id"] == current["id"])
    assert row["title"] == current["title"], (
        f"the row and the header disagree about the same conversation: "
        f"{row['title']!r} vs {current['title']!r}")
    assert opened["title"] == expected, (
        f"opening a conversation by id answers without its title: {opened}")


@pytest.mark.asyncio
async def test_the_page_can_start_a_new_conversation(tmp_path, fake_openai,
                                                     monkeypatch):
    """AC-63: the CLICK half of ending a conversation.

    A conversation is now HELD until something ends it (AC-59), so the ways of
    ending one are the whole of the owner's control over it: saying "new
    conversation", and this button. Without the route the page has no way at
    all, and the spoken phrase is the only exit -- which is a control the owner
    asked for and does not have.

    Asserted on the STORE as well as on the answer, because a route that
    minted an id and rotated nothing would return a plausible body and leave
    the agent in the conversation the owner just ended (D-18).
    """
    from aiohttp.test_utils import TestClient, TestServer

    hub, _, store = _hub(tmp_path, fake_openai)
    store.append(store.current()["id"], "user", "the conversation that is being ended")
    before = store.current()["id"]

    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.post("/api/session/new")
        doc = await answer.json()
        current = await (await client.get("/api/session/current")).json()
    finally:
        await client.close()

    assert answer.status == 200, doc
    assert doc["id"] != before, (
        f"the route answered with the conversation it was asked to leave: "
        f"{doc}")
    assert store.current()["id"] == doc["id"], (
        "the route minted an id the store knows nothing about, so the next "
        "turn resumes the conversation the owner ended")
    assert current["messages"] == [], (
        f"the new conversation was served carrying the old one's messages: "
        f"{current['messages']}")


@pytest.mark.asyncio
async def test_starting_a_new_conversation_is_404_without_a_harness(
        tmp_path, monkeypatch):
    """The same shape as `GET /api/session/current` (AC-63).

    Endpoint mode holds no conversation, so there is no second one to start.
    404 rather than a cheerful 200 over nothing: a page that got an id back
    would show a conversation this daemon does not have.
    """
    from aiohttp.test_utils import TestClient, TestServer

    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=ports.build(ports.BRAINS, "fake"),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=_prefs(tmp_path))
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.post("/api/session/new")
    finally:
        await client.close()

    assert answer.status == 404, await answer.text()


# --------------------------------------------------------------------------- #
# past conversations are REACHABLE (AC-164, docs/familiar-ui.md 7 step 1)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_chat_list_is_newest_first(tmp_path, fake_openai,
                                             monkeypatch):
    """`GET /api/chats` orders by `updatedAt`, not by name and not by walk.

    THREE files, and the newest is in the MIDDLE by name, for the same
    reason `test_a_restart_reattaches_to_the_newest_updated_at` needs three:
    with two, "newest stamp" and "last listed" are the same answer and the
    fixture rules out one wrong choice while claiming to rule out both.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-0000000000000-aaaaaa", 5,
             [_msg("user", "the month before last")])
    _on_disk(root, "chat-5000000000000-mmmmmm", 9000,
             [_msg("user", "the one we were having")])
    _on_disk(root, "chat-9000000000000-zzzzzz", 10,
             [_msg("user", "last week")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats")
        doc = await answer.json()

    assert answer.status == 200, doc
    assert [c["id"] for c in doc["chats"]] == ["chat-5000000000000-mmmmmm",
                                               "chat-9000000000000-zzzzzz",
                                               "chat-0000000000000-aaaaaa"], (
        f"the conversations came back in some other order than newest "
        f"first, so the sidebar's top row is not the last thing the owner "
        f"was talking about: {[c['id'] for c in doc['chats']]}")
    # `projectId` and `projectLabel` joined 2026-09-11 (AC-229) and `project`
    # stayed: the path is still the record's own word for where the agent was,
    # and the pair beside it is the ROW that path belongs to, DERIVED per
    # request. `status` and `elapsed_ms` joined 2026-09-16 (AC-263,
    # `docs/triage-list.md` phase 1) through `_with_status`, applied only to
    # THIS route -- `GET /api/chats/{id}` and the others `_filed` also
    # annotates did NOT grow, and their own exact-shape tests pin that.
    # `action` joined the same day (phase 2 continued, owner's ruling): the
    # last tool `_Host.last_action` remembers for this conversation, cleared
    # the moment its turn ends, so an idle or done row carries `None` rather
    # than a stale tool name.
    # `origin` joined 2026-09-26 (docs/automations.md AU-8): a scheduled
    # run's chat is titled by the daemon, and the row says so; None on every
    # chat the owner started. `unread` joined the same day (AC-469): true
    # only for a run's chat nobody has opened yet.
    # Pinned as an exact set for the reason it was pinned before -- a row
    # that quietly grows a field is a second shape for the page to draw.
    # `harness` joined 2026-10-27 (docs/lantern-ui.md section 4): a running
    # row reads "pi 1:42", and the agent's name is the record's, never a
    # guess in the page.
    assert set(doc["chats"][0]) == {"id", "title", "turns", "updatedAt",
                                    "project", "projectId", "projectLabel",
                                    "status", "elapsed_ms", "action",
                                    "origin", "unread", "harness", "agent",
                                    "agent_label"}, (
        f"a row carries exactly what a sidebar row draws: "
        f"{set(doc['chats'][0])}")
    assert doc["chats"][0]["updatedAt"] == 9000, doc["chats"][0]


@pytest.mark.asyncio
async def test_a_listed_row_names_its_own_agent(tmp_path, fake_openai,
                                                monkeypatch):
    """docs/lantern-ui.md section 4: a running chat row reads "<agent> m:ss"
    ("pi 1:42"), and the name is the record's `harness` -- the page never
    guesses it. Three rows with three different agents, the one asked about
    in the MIDDLE, so a row that copied its neighbour's name is caught."""
    root = tmp_path / "chats"
    _on_disk(root, "chat-0000000000000-aaaaaa", 5,
             [_msg("user", "first")], harness="claude")
    _on_disk(root, "chat-5000000000000-mmmmmm", 9000,
             [_msg("user", "second")], harness="pi")
    _on_disk(root, "chat-9000000000000-zzzzzz", 10,
             [_msg("user", "third")], harness="")
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats")).json()
        found = await (await client.get("/api/chats?q=second")).json()

    by_id = {c["id"]: c["harness"] for c in doc["chats"]}
    assert by_id == {"chat-0000000000000-aaaaaa": "claude",
                     "chat-5000000000000-mmmmmm": "pi",
                     "chat-9000000000000-zzzzzz": ""}, by_id
    assert [c["harness"] for c in found["chats"]] == ["pi"], found


@pytest.mark.asyncio
async def test_a_title_is_the_first_thing_the_owner_said_truncated(
        tmp_path, fake_openai, monkeypatch):
    """A conversation has no name, so the title is the first UTTERANCE.

    Three claims in one fixture, because they are one rule: it comes from
    the owner and not from Butler (the second chat opens with an assistant
    line), it is CUT rather than served whole, and a conversation the owner
    has not spoken into has no title at all rather than a phrase the daemon
    invented. That last one is an ASSISTANT-ONLY record since 2026-09-26: a
    record with no messages is never on disk any more (the ghost-chat defect).
    """
    long_one = ("I would like you to go through the endpointing module and "
                "tell me what it does with a silence that never ends")
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-aaaaaa", 30, [_msg("user", long_one)])
    _on_disk(root, "chat-1000000000000-bbbbbb", 20,
             [_msg("assistant", "Butler spoke first"),
              _msg("user", "and then the owner did", 2)])
    _on_disk(root, "chat-1000000000000-cccccc", 10,
             [_msg("assistant", "I could not reach the model.")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats")).json()

    titles = {c["id"]: c["title"] for c in doc["chats"]}
    cut = titles["chat-1000000000000-aaaaaa"]
    assert len(cut) <= chats.TITLE_CHARS + 3, (
        f"the title was served whole, so a sidebar row holds a paragraph: "
        f"{cut!r}")
    assert cut.endswith("..."), f"the cut is invisible to a reader: {cut!r}"
    assert long_one.startswith(cut[:-3].rstrip()), (
        f"the title is not the beginning of what was said: {cut!r}")
    assert titles["chat-1000000000000-bbbbbb"] == "and then the owner did", (
        f"the title came from Butler's own words rather than the owner's: "
        f"{titles['chat-1000000000000-bbbbbb']!r}")
    assert titles["chat-1000000000000-cccccc"] == "", (
        f"a conversation nobody spoke into was given a name the daemon "
        f"invented: {titles['chat-1000000000000-cccccc']!r}")
    turns = {c["id"]: c["turns"] for c in doc["chats"]}
    assert turns == {"chat-1000000000000-aaaaaa": 1,
                     "chat-1000000000000-bbbbbb": 1,
                     "chat-1000000000000-cccccc": 0}, (
        f"the turn count is not the number of times the owner spoke: {turns}")


@pytest.mark.asyncio
async def test_a_past_conversation_is_readable_by_id(tmp_path, fake_openai,
                                                     monkeypatch):
    """The whole point: a conversation that is NOT the current one opens.

    Asserted against a record the live store is not attached to, because a
    route that quietly served `current()` whatever it was asked for would
    pass every other test in this section.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-oldone", 10,
             [_msg("user", "what did we decide about the endpointer"),
              _msg("assistant", "to leave it alone", 2)])
    _on_disk(root, "chat-2000000000000-current", 900,
             [_msg("user", "the conversation Butler is in now")])
    store = chats.ChatStore(root)
    hub, _, _ = _hub(tmp_path, fake_openai, store=store)
    assert store.current()["id"] == "chat-2000000000000-current"

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats/chat-1000000000000-oldone")
        doc = await answer.json()

    assert answer.status == 200, doc
    assert set(doc) == {"id", "harness", "project", "projectId", "origin",
                        "projectLabel", "title", "messages", "questions",
                        "agent", "agent_label", "agents", "agent_default",
                        "agent_fixed", "model",
                        "model_default"}, (
        f"a past conversation is served in the shape the page already "
        f"renders (`/api/session/current`): {set(doc)}")
    assert doc["id"] == "chat-1000000000000-oldone", doc
    assert [m["content"] for m in doc["messages"]] == [
        "what did we decide about the endpointer", "to leave it alone"], doc


@pytest.mark.asyncio
async def test_an_unknown_chat_id_is_a_404_that_names_it(tmp_path,
                                                         fake_openai,
                                                         monkeypatch):
    """Never a 500 and never an empty 200.

    An empty 200 is the dangerous one: the page would draw it as a
    conversation that exists and happens to be silent, and the owner would
    read that as their record having been lost.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-aaaaaa", 10, [_msg("user", "hello")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats/chat-1000000000000-nosuch")
        doc = await answer.json()

    assert answer.status == 404, doc
    assert "chat-1000000000000-nosuch" in doc["error"], (
        f"the refusal does not name the id it refused, so a page showing it "
        f"cannot say which conversation is missing: {doc}")


@pytest.mark.asyncio
async def test_a_chat_id_cannot_reach_a_file_outside_the_chats_directory(
        tmp_path, fake_openai, monkeypatch):
    """The id comes off the wire, so it must not be able to name a path.

    The bait is a real, well-formed chat record one directory ABOVE
    `state/chats/`, so a route that joined the id onto the store's root
    would answer 200 with its contents and every status assertion here would
    still pass. The assertion with teeth is therefore that the bait's words
    appear in NO body.

    THREE of the five forms reach the handler carrying a real path [MEASURED
    2026-09-10]: `..\\secret` and `..%5Csecret` arrive as a backslash, which
    is a separator on this machine and not one aiohttp's router treats as a
    path segment, and `..%2Fsecret` arrives DECODED as `../secret`. The other
    two are answered by the router before Butler sees them, and are kept
    because which of the five a future aiohttp lets through is not this
    repository's decision to make.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-aaaaaa", 10, [_msg("user", "hello")])
    _on_disk(tmp_path, "secret", 20, [_msg("user", "PRIVATE-BAIT-TEXT")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    seen = []
    async with _http(hub, monkeypatch) as client:
        for attempt in ("..\\secret", "../secret", "..%2Fsecret",
                        "..%5Csecret", str(tmp_path / "secret")):
            answer = await client.get(f"/api/chats/{attempt}")
            seen.append((attempt, answer.status, await answer.text()))
        listed = await (await client.get("/api/chats")).json()

    for attempt, status, text in seen:
        assert status == 404, f"{attempt!r} was answered {status}: {text}"
        assert "PRIVATE-BAIT-TEXT" not in text, (
            f"{attempt!r} read a chat record from outside state/chats/: "
            f"{text}")
    assert [c["id"] for c in listed["chats"]] == [
        "chat-1000000000000-aaaaaa"], (
        f"the walk left the chats directory: {listed}")


@pytest.mark.asyncio
async def test_one_corrupt_record_does_not_hide_the_others(tmp_path,
                                                           fake_openai,
                                                           monkeypatch,
                                                           capsys):
    """A bad file costs its own conversation and never the list (D-1).

    The failure this catches is the whole sidebar going 500 because one
    record was hand-edited -- which would make every conversation
    unreachable to fix the one that is broken.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-aaaaaa", 10, [_msg("user", "first")])
    _on_disk(root, "chat-3000000000000-cccccc", 30, [_msg("user", "third")])
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat-2000000000000-bbbbbb.json").write_text(
        '{"id": "chat-2000000000000-bbbbbb", "updatedAt": 20, '
        '"messages": ["not an object"]}', encoding="utf-8")
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    capsys.readouterr()

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats")
        doc = await answer.json()
        one = await client.get("/api/chats/chat-1000000000000-aaaaaa")
        one_doc = await one.json()
        bad = await client.get("/api/chats/chat-2000000000000-bbbbbb")

    assert answer.status == 200, doc
    assert {c["id"] for c in doc["chats"]} == {"chat-3000000000000-cccccc",
                                               "chat-1000000000000-aaaaaa"}, (
        f"one unreadable record took the whole list with it: {doc}")
    assert "unreadable" in capsys.readouterr().out.lower(), (
        "the corrupt record was skipped SILENTLY, which is how a "
        "conversation disappears without anybody knowing (D-1)")
    assert one.status == 200 and one_doc["id"] == "chat-1000000000000-aaaaaa"
    assert bad.status == 404, (
        f"a record nothing can read was served anyway: {bad.status}")


@pytest.mark.asyncio
async def test_the_chat_list_is_bounded(tmp_path, fake_openai, monkeypatch):
    """A daemon that has run for a year must not answer with every chat.

    The newest end is what survives the cut, for `MESSAGES_SERVED`'s reason:
    the rows the owner might open are the recent ones.
    """
    root = tmp_path / "chats"
    for i in range(butlerd.CHATS_LISTED + 5):
        _on_disk(root, f"chat-{1000000000000 + i}-aaaaaa", 100 + i,
                 [_msg("user", f"conversation {i}")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats")).json()

    assert len(doc["chats"]) == butlerd.CHATS_LISTED, (
        f"the route listed {len(doc['chats'])} conversations")
    titles = [c["title"] for c in doc["chats"]]
    assert f"conversation {butlerd.CHATS_LISTED + 4}" in titles, (
        "the OLDEST end was served, so the sidebar shows conversations from "
        "a year ago and not the one that just ended")
    assert "conversation 0" not in titles, "the window is not a window"


@pytest.mark.asyncio
async def test_a_past_conversation_is_bounded_like_the_current_one(
        tmp_path, fake_openai, monkeypatch):
    """The SAME bound, not a second one: `MESSAGES_SERVED` (AC-164).

    A route with a bound of its own would drift from the one
    `/api/session/current` uses, and the page renders both with the same
    code -- so the transcript would change length when a conversation
    stopped being the current one.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-oldone", 10,
             [_msg("user", f"turn {i}", i)
              for i in range(butlerd.MESSAGES_SERVED + 40)])
    _on_disk(root, "chat-2000000000000-current", 900,
             [_msg("user", "the current one")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get(
            "/api/chats/chat-1000000000000-oldone")).json()
        listed = await (await client.get("/api/chats")).json()

    said = [m["content"] for m in doc["messages"]]
    assert len(said) == butlerd.MESSAGES_SERVED, (
        f"a past conversation served {len(said)} messages where the current "
        f"one serves {butlerd.MESSAGES_SERVED}")
    assert f"turn {butlerd.MESSAGES_SERVED + 39}" in said, (
        "the OLDEST end of the record was served")
    assert "turn 0" not in said, "the window is not a window"
    rows = {c["id"]: c for c in listed["chats"]}
    assert rows["chat-1000000000000-oldone"]["turns"] == (
        butlerd.MESSAGES_SERVED + 40), (
        f"the LIST's turn count was cut down with the transcript, so the "
        f"sidebar under-reports a long conversation: {rows}")


@pytest.mark.asyncio
async def test_the_chat_routes_are_404_without_a_harness(tmp_path,
                                                         monkeypatch):
    """The same shape as `GET /api/session/current` (AC-63, AC-164).

    Endpoint mode holds no chat store at all, so there is nothing on disk to
    list. 404 rather than an empty list: "this daemon keeps no record" and
    "you have had no conversations" are different facts, and a page told the
    second would draw an empty sidebar and never say why.
    """
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=ports.build(ports.BRAINS, "fake"),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=_prefs(tmp_path))

    async with _http(hub, monkeypatch) as client:
        listed = await client.get("/api/chats")
        listed_text = await listed.text()
        one = await client.get("/api/chats/chat-1000000000000-aaaaaa")
        one_text = await one.text()

    assert listed.status == 404, listed_text
    assert one.status == 404, one_text


# --------------------------------------------------------------------------- #
# the live half (AC-35's only real proof)
# --------------------------------------------------------------------------- #
TERSE = ("You are terse. Answer in one short sentence. Do not use any tools "
         "unless you are asked to.")
LIVE_BUDGET_S = 180.0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_real_session_survives_a_real_restart(tmp_path, fake_openai):
    """The one thing the fake cannot prove: what a REAL child remembers.

    Everything above asserts that the same `sessionRef` is handed to the next
    child. Whether pi actually reads its conversation back out of that file is
    a fact about pi, and only a real child can answer it. If this fails, AC-35
    is not met and that is a FINDING rather than a test to relax.

    The D-13 mutation that guards it: point the second child at a DIFFERENT
    reference -- break `ChatStore.session_ref` so it re-mints -- and this test
    must FAIL. A recall test that passes with the agent's memory taken away is
    measuring the model's guessing, not the record.
    """
    skip_without_pi()
    harness = ports.build(ports.HARNESSES, "pi",
                          **pi_kwargs(append_system_prompt=TERSE))
    prefs = _prefs(tmp_path)
    root = tmp_path / "chats"
    brain, _, _ = _brain(tmp_path, fake_openai, store=chats.ChatStore(root),
                         harness=harness, prefs=prefs)
    try:
        await asyncio.wait_for(
            _answer(brain, "remember this number: 4173. say ok"),
            LIVE_BUDGET_S)
    finally:
        await harness.dispose()

    second = ports.build(ports.HARNESSES, "pi",
                         **pi_kwargs(append_system_prompt=TERSE))
    brain2, _, store2 = _brain(tmp_path, fake_openai,
                               store=chats.ChatStore(root), harness=second,
                               prefs=prefs)
    try:
        said = await asyncio.wait_for(
            _answer(brain2, "what number did I ask you to remember"),
            LIVE_BUDGET_S)
    finally:
        await second.dispose()

    assert "4173" in " ".join(said), (
        f"a restarted daemon re-attached to the same chat and the child could "
        f"not answer a question from before the restart, so the conversation "
        f"did NOT survive: {said}")
    assert _users(store2), "Butler's own record was lost"


# --------------------------------------------------------------------------- #
# DELETE (owner's ask, 2026-09-10: "previous chats should be CRUD, so user can
# delete if needed"). Listing and reading were AC-164's whole scope.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_past_conversation_can_be_deleted(tmp_path, fake_openai,
                                                  monkeypatch):
    """The row goes from the list AND the file goes from disk.

    Both halves, because either alone is a bug wearing the other's clothes:
    a list that drops a row it still holds is a conversation the owner can
    neither reach nor remove, and a file removed while the list keeps the
    row is a 404 on the next click.

    The answer carries the NEW LIST rather than `{ok: true}`, which is what
    lets the page repaint from disk instead of splicing its own array --
    `delete_shortcut`'s shape, asserted here so it cannot quietly become
    something cheaper.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-1000000000000-aaaaaa", 10, [_msg("user", "keep me")])
    doomed = _on_disk(root, "chat-2000000000000-bbbbbb", 20,
                      [_msg("user", "throw me away")])
    _on_disk(root, "chat-3000000000000-cccccc", 30,
             [_msg("user", "the live one")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.delete("/api/chats/" + doomed)
        doc = await answer.json()
        listed = await (await client.get("/api/chats")).json()

    assert answer.status == 200, doc
    assert doc["deleted"] == doomed, doc
    assert set(doc) == {"deleted", "chats", "session"}, (
        "the delete answer carries the new list and the live id, so the "
        "page repaints rather than splicing: " + repr(set(doc)))
    assert doomed not in [c["id"] for c in doc["chats"]], (
        "the answer still lists the conversation it says it deleted")
    assert doomed not in [c["id"] for c in listed["chats"]], (
        "a fresh GET still lists it, so the answer and the disk disagree")
    assert not (root / (doomed + ".json")).exists(), (
        "the row left the list and the file is still on disk")
    assert (root / "chat-1000000000000-aaaaaa.json").exists(), (
        "deleting one conversation took another with it")


@pytest.mark.asyncio
async def test_deleting_the_live_conversation_rotates_before_it_unlinks(
        tmp_path, fake_openai, monkeypatch):
    """THE TRAP, and the only test here with real teeth.

    A HELD record is a dict in memory and `_write` recreates its file from
    that dict on the next append. So a delete that merely unlinks the live
    record reports success, leaves the list looking right, and is silently
    UNDONE the moment anybody speaks -- with the deleted words back in the
    file, because they never left the dict.

    **THE RULE IS NOW "IS IT HELD" AND THIS CASE IS ONE OF TWO**
    (`docs/parallel-agents.md` phase 4). It proved the trap for the one slot
    the store used to have, which was both "held" and "current" at once; the
    registry splits those, and
    `test_deleting_a_HELD_conversation_that_is_not_current_does_not_rotate` is
    the same trap for a conversation the owner opened and left. Every
    assertion below is unchanged -- deleting the CURRENT conversation still
    rotates, because something has to be current afterwards.

    The assertion that catches it is therefore neither the status nor the
    list: it is that after a delete AND a subsequent append, the old id has
    no file and the old words are nowhere on disk.
    """
    root = tmp_path / "chats"
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    store.append(store.current()["id"], "user", "SECRET-BAIT-TEXT")
    live = store.current()["id"]

    async with _http(hub, monkeypatch) as client:
        answer = await client.delete("/api/chats/" + live)
        doc = await answer.json()

    assert answer.status == 200, doc
    assert store.current()["id"] != live, (
        "the store is still pointed at the conversation it just deleted, "
        "so the next thing said goes into a file that should not exist")
    assert doc["session"] == store.current()["id"], (
        "the answer's `session` is not the conversation Butler now holds, "
        "so a page repainting from it marks the wrong row")

    store.append(store.current()["id"], "user", "a word in the new conversation")

    assert not (root / (live + ".json")).exists(), (
        "the deleted conversation's file came BACK when the next message "
        "was appended -- the store was still holding it in memory")
    on_disk = " ".join(p.read_text(encoding="utf-8")
                       for p in root.glob("chat-*.json"))
    assert "SECRET-BAIT-TEXT" not in on_disk, (
        "the deleted words are still on disk after an append")


@pytest.mark.asyncio
async def test_deleting_a_conversation_takes_the_agents_memory_with_it(
        tmp_path, fake_openai, monkeypatch):
    """The `-<harness>.session.json` sidecar goes too.

    `_records()` deliberately skips those files, so nothing in any list or
    any route can reach one. Left behind, the owner deletes a conversation
    and the coding agent's memory of it stays on disk forever with no UI
    that can see it, which is the opposite of what "delete" means on a
    button the owner pressed.
    """
    root = tmp_path / "chats"
    doomed = _on_disk(root, "chat-4000000000000-dddddd", 40,
                      [_msg("user", "with an agent")])
    sidecar = root / (doomed + "-pi.session.json")
    sidecar.write_text('{"type":"session"}', encoding="utf-8")
    _on_disk(root, "chat-5000000000000-eeeeee", 50, [_msg("user", "keep")])
    other = root / "chat-5000000000000-eeeeee-pi.session.json"
    other.write_text('{"type":"session"}', encoding="utf-8")
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.delete("/api/chats/" + doomed)
        assert answer.status == 200, await answer.text()

    assert not sidecar.exists(), (
        "the conversation went and the agent's own context stayed, "
        "unreachable from every route Butler serves")
    assert other.exists(), (
        "deleting one conversation took another conversation's agent "
        "context with it")


@pytest.mark.asyncio
async def test_deleting_an_unknown_id_is_a_404_and_so_is_deleting_twice(
        tmp_path, fake_openai, monkeypatch):
    """Effect-idempotent, response-honest.

    A second DELETE must not answer 200. `get_chat` already 404s on an
    unknown id so the page cannot read an empty 200 as a conversation that
    exists and happens to be silent; a 200 here would tell a satellite it
    deleted something it did not, which is how a two-device race becomes
    invisible. The 404 NAMES the id, like the read route's.
    """
    root = tmp_path / "chats"
    doomed = _on_disk(root, "chat-6000000000000-ffffff", 60,
                      [_msg("user", "once")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        first = await client.delete("/api/chats/" + doomed)
        assert first.status == 200, await first.text()
        second = await client.delete("/api/chats/" + doomed)
        second_body = await second.text()
        unknown = await client.delete("/api/chats/chat-nobody-has-this")
        unknown_body = await unknown.text()

    assert second.status == 404, "a second delete answered " + str(second.status)
    assert doomed in second_body, (
        "the 404 does not name the id it refused: " + second_body)
    assert unknown.status == 404, unknown_body
    assert "chat-nobody-has-this" in unknown_body, unknown_body


@pytest.mark.asyncio
async def test_a_delete_cannot_reach_a_file_outside_the_chats_directory(
        tmp_path, fake_openai, monkeypatch):
    """The read route's traversal fixture, aimed at the destructive verb.

    The same five forms and the same bait, because this is the route where
    getting it wrong UNLINKS the file rather than serving it, and the id
    reaches `ChatStore.delete` off the wire exactly as it reaches `find`.
    The assertion with teeth is that the bait is still on disk afterwards.

    404 OR 405, and the 405 is worth naming rather than loosening quietly
    [MEASURED 2026-09-10]. `../secret` normalises to `/api/secret`, which no
    Butler route claims -- so it lands on the `add_static("/")` mount, and a
    static resource serves GET and HEAD only. The read route therefore gets
    404 (no such file) and this one gets 405 (wrong method for a static
    resource) from the SAME path. Both are the router refusing before Butler
    sees anything; what matters is that neither reaches an unlink, which the
    bait assertion is what actually proves.
    """
    root = tmp_path / "chats"
    _on_disk(root, "chat-7000000000000-aaaaaa", 70, [_msg("user", "mine")])
    _on_disk(tmp_path, "secret", 80, [_msg("user", "PRIVATE-BAIT-TEXT")])
    bait_path = tmp_path / "secret.json"
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    seen = []
    async with _http(hub, monkeypatch) as client:
        for attempt in ("..\\secret", "../secret", "..%2Fsecret",
                        "..%5Csecret", str(tmp_path / "secret")):
            answer = await client.delete("/api/chats/" + attempt)
            seen.append((attempt, answer.status, await answer.text()))

    for attempt, status, text in seen:
        assert status in (404, 405), (
            repr(attempt) + " was answered " + str(status) + ": " + text)
    assert bait_path.exists(), (
        "a chat id off the wire unlinked a file outside state/chats/")
    assert (root / "chat-7000000000000-aaaaaa.json").exists(), (
        "the traversal attempts took a real conversation with them")


@pytest.mark.asyncio
async def test_deleting_is_404_without_a_harness(tmp_path, fake_openai,
                                                 monkeypatch):
    """Endpoint mode holds no conversations, so it cannot delete one.

    The same 404 the rest of this family answers, and a different fact from
    a delete that was attempted and failed.
    """
    hub, _, _ = _hub(tmp_path, fake_openai)
    hub._brain = None

    async with _http(hub, monkeypatch) as client:
        answer = await client.delete("/api/chats/chat-1000000000000-aaaaaa")
        body = await answer.text()

    assert answer.status == 404, body


def test_rotating_reaps_a_conversation_nobody_ever_spoke_into(tmp_path):
    """New chat, walked away from, leaves NOTHING behind.

    This is where the blank rows come from. `_attach` is not the multiplier
    -- it mints only when the directory is empty -- `rotate` is: press New
    chat, say nothing, and a zero-message file sits in the sidebar for good.
    One was on the owner's disk the day this was written, and a scratch
    daemon minted another within a second of booting.

    The second half is the one that needs care. `_turns` counts only what
    the OWNER said, so an assistant-only record would be reaped by a turn
    test -- and Butler greeting, or speaking an error, into a conversation
    nobody replied to IS a record. Only the owner may throw that away.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)

    empty_id = store.current()["id"]
    store.rotate()
    assert not (root / (empty_id + ".json")).exists(), (
        "a conversation nobody ever spoke into was left on disk, which is "
        "the blank row in the sidebar")

    store.append(store.current()["id"], "assistant", "I could not reach the model.")
    spoken_id = store.current()["id"]
    store.rotate()
    assert (root / (spoken_id + ".json")).exists(), (
        "a record holding only Butler's own words was reaped; `_turns` "
        "counts the OWNER's messages and must not be the reap test")


# --------------------------------------------------------------------------- #
# search (AC-185..AC-187, docs/chat-search.md)
#
# The matching lives in the DAEMON and not in the page, which is the whole of
# D1: the page holds titles and the daemon holds transcripts, so a page-side
# filter would hide rows the daemon would have shown -- and a row that is not
# drawn looks exactly like a conversation that does not exist.
# --------------------------------------------------------------------------- #
def _searchable(root: Path) -> None:
    """Three conversations, each matching a different way.

    One matches on its TITLE only, one on its CONTENT only, and one matches
    nothing -- so a search that quietly returned everything, and a search that
    only ever looked at titles, both fail here. Their stamps put the
    content-only match NEWEST, so "newest first survived the filter" is a
    claim with teeth rather than a coincidence of the walk order.
    """
    _on_disk(root, "chat-1000000000000-aaaaaa", 100,
             [_msg("user", "remind me about the barometer")])
    _on_disk(root, "chat-2000000000000-bbbbbb", 900,
             [_msg("user", "what did we decide"),
              _msg("assistant", "You decided to keep the BAROMETER on the "
                                "shelf by the door, out of the sun.")])
    _on_disk(root, "chat-3000000000000-cccccc", 50,
             [_msg("user", "set a timer for ten minutes")])


@pytest.mark.asyncio
async def test_a_search_matches_the_title_and_the_content(tmp_path,
                                                          fake_openai,
                                                          monkeypatch):
    """AC-185, AC-186. `?q=` filters and keeps the shape and the order.

    THE TEETH ARE THE CONTENT-ONLY ROW. A search that only read titles passes
    every other assertion here -- it returns one row, in the right shape,
    newest first -- and is exactly the search the page could already have run
    for itself, which is not what was asked for.
    """
    root = tmp_path / "chats"
    _searchable(root)
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats?q=BAROMETER")
        doc = await answer.json()
        plain = await (await client.get("/api/chats")).json()

    assert answer.status == 200, doc
    assert [c["id"] for c in doc["chats"]] == ["chat-2000000000000-bbbbbb",
                                              "chat-1000000000000-aaaaaa"], (
        f"the search did not match the title AND the content, newest first: "
        f"{[c['id'] for c in doc['chats']]}")
    assert len(plain["chats"]) == 3, (
        f"the unfiltered listing changed: {plain['chats']}")


@pytest.mark.asyncio
async def test_a_blank_query_is_the_plain_listing(tmp_path, fake_openai,
                                                  monkeypatch):
    """AC-185. One shape, so the page has one rendering path.

    Asserted as EQUALITY with the unfiltered answer rather than as a count: a
    search branch that returned the right rows carrying an extra field would
    pass a length check and give the page a second row shape to draw.
    """
    root = tmp_path / "chats"
    _searchable(root)
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        plain = await (await client.get("/api/chats")).json()
        blank = await (await client.get("/api/chats?q=")).json()
        spaces = await (await client.get("/api/chats?q=%20%20")).json()

    assert blank["chats"] == plain["chats"], (
        f"a blank query is not the plain listing: {blank}")
    assert spaces["chats"] == plain["chats"], (
        f"a whitespace query is not the plain listing: {spaces}")


@pytest.mark.asyncio
async def test_a_content_match_says_WHY_and_a_title_match_does_not(
        tmp_path, fake_openai, monkeypatch):
    """AC-187. A row that matched on something the owner cannot see has to
    carry the reason, or it is a row they must open to understand."""
    root = tmp_path / "chats"
    _searchable(root)
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/chats?q=barometer")).json()

    by_id = {c["id"]: c for c in doc["chats"]}
    content = by_id["chat-2000000000000-bbbbbb"]
    title = by_id["chat-1000000000000-aaaaaa"]
    assert "BAROMETER" in content["snippet"], (
        f"the content match carries no excerpt of what matched: {content}")
    assert len(content["snippet"]) <= chats.SNIPPET_CHARS + 6, (
        f"the snippet is not bounded: {len(content['snippet'])} chars")
    assert title["snippet"] == "", (
        f"a title match carries an excerpt it does not need: {title}")


@pytest.mark.asyncio
async def test_a_search_that_matches_nothing_is_an_empty_list_not_a_404(
        tmp_path, fake_openai, monkeypatch):
    """An empty result is a fact about the query; a 404 is a fact about the
    daemon. Flattening the two would make "you have no conversations" and
    "none of them say that" the same answer on the wire."""
    root = tmp_path / "chats"
    _searchable(root)
    hub, _, _ = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats?q=zzzznothing")
        doc = await answer.json()

    assert answer.status == 200, doc
    assert doc["chats"] == [], doc


@pytest.mark.asyncio
async def test_searching_is_404_without_a_harness(tmp_path, fake_openai,
                                                  monkeypatch):
    """The routes beside it are, and a search that answered 200 with an empty
    list in endpoint mode would tell the page this daemon holds conversations
    and none of them matched."""
    hub, _, _ = _hub(tmp_path, fake_openai)
    hub.brain = ports.build(ports.BRAINS, "fake")

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats?q=anything")
        doc = await answer.json()

    assert answer.status == 404, doc
    assert doc["error"] == butlerd.NO_CONVERSATION, doc


# --------------------------------------------------------------------------- #
# rewind (AC-189..AC-193, docs/rewind.md)
#
# This is the first route in this daemon that removes something the owner SAID
# from the conversation they are in, which is why AC-190 -- the copy -- is the
# criterion with teeth and is asserted in every case below that touches disk.
# It OVERTURNS AC-97's "a correction is a new turn, never an edit" (D-20); what
# survives of that ruling is that nothing is silently rewritten, and the copy
# is the whole of how.
# --------------------------------------------------------------------------- #
def _spoken(store, texts) -> list[str]:
    """Drive real appends through the store and answer the message ids.

    Through `append` rather than written to disk, because what a rewind aims
    at is an id the STORE minted -- a fixture that wrote its own ids could
    pass against a daemon that matched on something else entirely.
    """
    for role, text in texts:
        store.append(store.current()["id"], role, text)
    return [m["id"] for m in store.messages()]


@pytest.mark.asyncio
async def test_a_rewind_truncates_and_the_whole_original_survives(
        tmp_path, fake_openai, monkeypatch):
    """AC-189 and AC-190 together, because separately neither is the rule.

    The record is truncated at the target, the target's text comes back for
    the composer, and the WHOLE original -- including everything dropped --
    is a second conversation on disk carrying its parentage. A rewind that
    truncated and did not copy would pass every assertion about the
    truncation, which is the failure this criterion exists for.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    ids = _spoken(store, [("user", "first question"),
                          ("assistant", "first answer"),
                          ("user", "second question"),
                          ("assistant", "second answer")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=store)

    async with _http(hub, monkeypatch) as client:
        answer = await client.post("/api/session/rewind",
                                   json={"message": ids[2]})
        doc = await answer.json()

    assert answer.status == 200, doc
    assert doc["prompt"] == "second question", (
        f"the dropped prompt did not come back for the composer: {doc}")
    assert [m["content"] for m in doc["session"]["messages"]] == [
        "first question", "first answer"], (
        f"the record was not truncated at the target: {doc['session']}")

    kept = store.current()
    assert kept["sessionRef"] == "", (
        "the harness reference survived the rewind, so the next child would "
        "resume a session that still remembers the dropped turns (AC-192)")
    copy = json.loads((root / f"{doc['snapshot']}.json").read_text("utf-8"))
    assert [m["content"] for m in copy["messages"]] == [
        "first question", "first answer", "second question",
        "second answer"], (
        f"the copy does not hold the whole original, so the rewind DESTROYED "
        f"what the owner said: {copy['messages']}")
    assert copy["parentChatId"] == kept["id"], copy
    assert copy["parentMessageId"] == ids[2], copy
    assert doc["snapshot"] in [c["id"] for c in doc["chats"]], (
        f"the copy is not in the list the page repaints from: {doc['chats']}")


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["assistant", "unknown"])
async def test_a_rewind_that_is_refused_changes_NOTHING(tmp_path, fake_openai,
                                                        monkeypatch, which):
    """AC-191, asserted by its CONSEQUENCE and not by the status.

    A route that answered 400 and truncated anyway satisfies every
    status-shaped check that could be written here, and the thing it
    truncated is a conversation the owner cannot get back except through a
    copy this path never made.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    ids = _spoken(store, [("user", "first question"),
                          ("assistant", "first answer")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=store)
    target = ids[1] if which == "assistant" else "msg-nobody-has-this"

    async with _http(hub, monkeypatch) as client:
        answer = await client.post("/api/session/rewind",
                                   json={"message": target})
        doc = await answer.json()

    assert answer.status == 400, doc
    assert doc.get("error"), f"a 400 with no reason in it: {doc}"
    assert [m["content"] for m in store.messages()] == [
        "first question", "first answer"], (
        f"a REFUSED rewind truncated the record anyway: {store.messages()}")
    assert len(list(root.glob("chat-*.json"))) == 1, (
        f"a refused rewind left a copy behind: "
        f"{[p.name for p in root.glob('chat-*.json')]}")


@pytest.mark.asyncio
async def test_a_rewind_with_no_message_named_is_refused(tmp_path, fake_openai,
                                                         monkeypatch):
    """A body with nothing to aim at is a 400 rather than a rewind to the
    start, which is the shape a missing field would otherwise produce."""
    store = chats.ChatStore(tmp_path / "chats")
    _spoken(store, [("user", "first question")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=store)

    async with _http(hub, monkeypatch) as client:
        for body in ({}, {"message": ""}, {"message": "   "}):
            answer = await client.post("/api/session/rewind", json=body)
            doc = await answer.json()
            assert answer.status == 400, (body, doc)
    assert len(store.messages()) == 1, store.messages()


@pytest.mark.asyncio
async def test_rewinding_is_404_without_a_harness(tmp_path, fake_openai,
                                                  monkeypatch):
    """The session routes beside it are, for the same reason: a daemon that
    holds no conversation cannot go back to a point in one."""
    hub, _, _ = _hub(tmp_path, fake_openai)
    hub.brain = ports.build(ports.BRAINS, "fake")

    async with _http(hub, monkeypatch) as client:
        answer = await client.post("/api/session/rewind",
                                   json={"message": "msg-1"})
        doc = await answer.json()

    assert answer.status == 404, doc
    assert doc["error"] == butlerd.NO_CONVERSATION, doc


def test_the_agents_memory_moves_onto_the_copy(tmp_path):
    """AC-192, and it is the defect the reference implementation SHIPPED.

    Butler's `sessionRef` is a path Butler MINTS from the chat id, so nulling
    it buys nothing -- `session_ref` hands back a byte-identical path on the
    next call. The FILE has to move, or the next child resumes a session that
    still holds every dropped turn while the transcript says otherwise.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    ids = _spoken(store, [("user", "first question"),
                          ("assistant", "first answer"),
                          ("user", "second question")])
    ref = Path(store.session_ref(store.current(), "pi"))
    ref.write_text('{"agent": "remembers the second question"}',
                   encoding="utf-8")

    done = store.rewind(ids[2])

    assert not ref.exists(), (
        f"the harness session file is still at the truncated conversation's "
        f"own path, so the next child resumes the dropped turns: {ref}")
    moved = root / f"{done['snapshot']}-pi.session.json"
    assert moved.exists(), (
        f"the agent's memory of the dropped turns was destroyed rather than "
        f"moved onto the copy that holds them: "
        f"{[p.name for p in root.glob('*.session.json')]}")
    copy = json.loads((root / f"{done['snapshot']}.json").read_text("utf-8"))
    # A NAME, joined onto the store root at use (test_session_ref_rooted.py).
    assert copy["sessionRef"] == moved.name, copy
    assert store.current()["sessionRef"] == "", store.current()


def test_the_next_turn_after_a_rewind_is_seeded_and_the_one_after_is_not(
        tmp_path):
    """AC-193's guard, which is the half that is easy to get wrong.

    The seed exists because the rewind takes the harness's memory away with
    the dropped turns, so the child that starts next has forgotten the KEPT
    turns as well. It must fire ONCE: a seed on every turn would re-send the
    whole conversation as prior context forever, growing until the cap cuts
    the oldest turns off.

    The first version of this guard asked the FILESYSTEM whether a session
    file existed, and seeded on EVERY turn against any harness that does not
    keep one -- which is every harness with an opaque native resume id
    (AC-44). Caught by the fake harness, whose held-child tests started
    seeing their second prompt wrapped in a prior-conversation block. So this
    asserts the property and never the mechanism.
    """
    store = chats.ChatStore(tmp_path / "chats")
    assert not store.needs_seed(), (
        "a conversation nobody has spoken into wants a seed, so the very "
        "first prompt of every conversation would carry an empty one")
    store.append(store.current()["id"], "user", "first question")
    store.append(store.current()["id"], "assistant", "first answer")
    store.session_ref(store.current(), "pi")
    assert not store.needs_seed(), (
        "an ordinary turn wants a seed, so every prompt after the first "
        "would re-send the whole conversation as prior context")
    store.append(store.current()["id"], "user", "second question")
    store.rewind(store.messages()[-1]["id"])
    assert store.needs_seed(), (
        "the turn after a rewind wants no seed, so the agent has forgotten "
        "the turns the owner deliberately KEPT")
    store.session_ref(store.current(), "pi")
    assert not store.needs_seed(), (
        "the seed did not stop once the child was given the reference")


def test_the_seed_is_labelled_capped_and_drops_the_OLDEST():
    """AC-193's rendering. Three claims, and the last is a choice rather than
    a detail: the turns nearest the rewind point are the ones the next
    question depends on, so the cap drops the oldest."""
    from daemon import dialogue

    assert dialogue.seed_note([]) == "", (
        "an empty transcript renders a block, so an ordinary turn's prompt "
        "would gain a section with nothing in it")
    note = dialogue.seed_note([{"role": "user", "content": "the question"},
                               {"role": "assistant", "content": "the answer"}])
    assert note.startswith(dialogue.SEED_HEADING), note[:120]
    assert "User: the question" in note, note
    assert "Assistant: the answer" in note, note

    long = ([{"role": "user", "content": "OLDEST " + "x" * 400}]
            + [{"role": "assistant", "content": "y" * 400} for _ in range(80)]
            + [{"role": "user", "content": "NEWEST " + "z" * 400}])
    cut = dialogue.seed_note(long)
    assert len(cut) <= dialogue.SEED_MAX_CHARS + len(dialogue.SEED_HEADING) + 8, (
        f"the seed is not capped: {len(cut)} chars")
    assert "NEWEST" in cut, "the cap dropped the turns nearest the rewind"
    assert "OLDEST" not in cut, "the cap dropped nothing at all"


# --------------------------------------------------------------------------- #
# a spoken STATUS is heard and not kept (`ports.Say.keep`)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_working_on_it_is_HEARD_and_is_in_nothing_else(tmp_path,
                                                             fake_openai):
    """The pre-spawn line is a status, and it was being recorded like an
    answer.

    Every harness reply opened with "Working on it." in the page's transcript,
    in the rolling history the next turn's router reads, and in Butler's own
    chat file -- so a conversation reopened next month read as Butler having
    said it twice. It still has to be SPOKEN, because the agent's thinking
    time is silence and silence reads as a dead microphone (D-10).

    FOUR assertions and the first one is what keeps the other three honest: a
    fix that simply stopped saying it would satisfy all of them.
    """
    hub, _, store = _hub(tmp_path, fake_openai)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await _final(hub, s, 1, "what does the endpointing module do")

    from daemon.adapters.brain_harness import HarnessConverse
    line = HarnessConverse.NARRATION
    assert line in hub.tts.said, (
        f"the pre-spawn line was not spoken at all, so the room now hears "
        f"silence while the agent thinks (D-10): {hub.tts.said}")
    assert line not in _said(s), (
        f"it is still a `reply` frame, so it is still the first sentence of "
        f"the answer in the transcript: {_said(s)}")
    assert not any(line in said for _role, said in hub.history), (
        f"it is still in the rolling transcript the next turn's router "
        f"reads: {list(hub.history)}")
    assert not any(line in m["content"] for m in store.messages()), (
        f"it is still in Butler's own record: "
        f"{[m['content'] for m in store.messages()]}")


@pytest.mark.asyncio
async def test_the_ANSWER_is_still_kept(tmp_path, fake_openai):
    """The other half, because a `keep` that leaked would be invisible.

    A flag that accidentally applied to the answer as well would empty the
    transcript and the record, and every assertion above would still pass.
    """
    hub, _, store = _hub(tmp_path, fake_openai)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await _final(hub, s, 1, "what does the endpointing module do")

    answers = [m["content"] for m in store.messages()
               if m["role"] == "assistant"]
    assert answers and any(a.strip() for a in answers), (
        f"the ANSWER is not in the record either, so `keep` is leaking onto "
        f"everything: {[m['content'] for m in store.messages()]}")
    assert [t for t in _said(s) if t.strip()], (
        f"no reply frame carried the answer: {_said(s)}")


def test_a_rewound_copy_belongs_to_the_same_project(tmp_path):
    """The snapshot is the one record built BY HAND, and it missed the field.

    `_read`'s setdefault, `listing`, `search` and `find` all learned `project`
    in the commit that added it; `session_rewind` constructs a record from
    `_blank()` and copies six fields by name, so a rewound conversation
    reported no workspace at all while the original kept one. Found by review
    2026-09-11.
    """
    store = chats.ChatStore(tmp_path / "chats")
    # Born in the project (AC-287): the one moment a record's project is set.
    store.rotate(str(tmp_path))
    store.append(store.current()["id"], "user", "first thing")
    store.append(store.current()["id"], "assistant", "an answer")
    store.append(store.current()["id"], "user", "second thing")
    target = store.current()["messages"][0]["id"]

    done = store.rewind(target)

    # `rewind` answers `{prompt, snapshot, id}` -- the SNAPSHOT's id is under
    # "snapshot" and "id" is the live conversation. Reading `done["id"]` for
    # the copy asserts the live record twice and the first version of this
    # test did exactly that, which is why it survived its own mutation.
    assert done is not None
    rows = {row["id"]: row for row in store.listing()}
    assert done["snapshot"] in rows, f"the copy is not in the sidebar: {rows}"
    assert rows[done["snapshot"]]["project"] == str(tmp_path), (
        "the rewound copy lost the workspace the conversation belonged to")
    assert rows[done["id"]]["project"] == str(tmp_path), (
        "the live conversation lost its workspace to the rewind")


# --------------------------------------------------------------------------- #
# NO UNPROJECTED CONVERSATIONS (R5, docs/filing-and-pacing.md; the owner's
# ruling of 2026-09-16, overturning `docs/workspaces-ui.md` D1/A)
# --------------------------------------------------------------------------- #
def test_migrate_unfiled_files_an_old_conversation_under_the_root(tmp_path):
    """A record written before `project` existed -- or whose birth-time
    filing never landed -- is filed under the Butler root exactly once.

    This is RETROACTIVE BIRTH and not a re-file: nothing can re-file a
    record (`docs/filing-and-pacing.md` 8, R-A), and this touches only
    records whose `project` is empty.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    store.append(store.current()["id"], "user", "an old question")
    unheld_id = store.current()["id"]
    store.rotate()                            # unregisters that record
    store.append(store.current()["id"], "user", "the live one")
    app_dir = tmp_path / "app"

    touched = store.migrate_unfiled(str(app_dir))

    assert set(touched) == {unheld_id, store.current()["id"]}, (
        f"not every unfiled conversation was touched: {touched}")
    rows = {row["id"]: row for row in store.listing()}
    assert rows[unheld_id]["project"] == str(app_dir), (
        "the unheld conversation on disk was not filed")
    assert rows[store.current()["id"]]["project"] == str(app_dir), (
        "the HELD (current) conversation was not filed")

    # ONCE: a second run touches nothing, because every record already has a
    # non-empty project -- the trigger is a fact about the record, not a
    # value that stops existing after one read.
    assert store.migrate_unfiled(str(app_dir)) == []


def test_migrate_unfiled_does_not_touch_an_already_filed_conversation(
        tmp_path):
    """A conversation that was properly born with a project keeps it -- this
    migration is for records that were NEVER told, not a re-file mechanism."""
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    store.rotate(str(tmp_path / "already-filed"))      # born filed

    touched = store.migrate_unfiled(str(tmp_path / "app"))

    assert touched == [], (
        "an already-filed conversation was touched by the migration")
    assert store.current()["project"] == str(tmp_path / "already-filed"), (
        "the migration overwrote a conversation's real project")


def test_migrate_unfiled_survives_an_append_to_the_held_conversation(
        tmp_path):
    """The migration must mutate the HELD dict, or the next append() silently
    reverts the filing (the held-dict-wins rule the class docstring states).

    The two tests above both assert via `store.listing()` -- a fresh DISK
    read -- immediately after `migrate_unfiled`, and neither calls `append()`
    afterward. Replacing `self._open.get(chat["id"], chat)` with plain `chat`
    (writing a fresh disk-read copy instead of the held one) passes both of
    them, because `_write` puts the right bytes on disk once; it is the NEXT
    `append()`, writing through the still-stale held dict, that silently
    undoes it. This test appends to the HELD conversation after migrating it
    and reads disk again, which is the only way to see that regression.
    """
    root = tmp_path / "chats"
    store = chats.ChatStore(root)
    store.append(store.current()["id"], "user", "an old question")
    held_id = store.current()["id"]
    app_dir = tmp_path / "app"

    store.migrate_unfiled(str(app_dir))
    store.append(held_id, "assistant", "a later reply")

    rows = {row["id"]: row for row in store.listing()}
    assert rows[held_id]["project"] == str(app_dir), (
        "append() into the held conversation reverted migrate_unfiled's "
        "filing -- the migration must mutate the HELD dict, not write a "
        "fresh disk-read copy that the held dict then overwrites")


# --------------------------------------------------------------------------- #
# the workspace scope (AC-229, AC-218, AC-225, docs/parallel-agents.md phase 1)
#
# `?project=` is `?q=`'s ruling in a second place: the SCOPING IS THE DAEMON'S,
# so the number of rows the page draws is the number the daemon served for that
# scope. A page-side filter would hide rows the daemon would have shown, and a
# row that is not drawn looks exactly like a conversation that does not exist.
#
# The other half is that a conversation's row is DERIVED from the path it
# already carries and is stored NOWHERE -- which is what makes AC-218 true by
# construction rather than by a migration: there is no second copy of the fact
# to go stale when the owner edits a row's path.
# --------------------------------------------------------------------------- #
def _scoped(tmp_path):
    """Two project rows, and four conversations: two, one, and one unfiled.

    The unfiled one is not decoration -- it is half the owner's real corpus
    [MEASURED 2026-09-11, `docs/workspaces-ui.md` 0.2] and it is the row that
    tells a scope that quietly returned everything from one that filtered.
    The stamps put the SECOND alpha conversation below a beta one in the pile,
    so "newest first survived the scope" is a claim rather than a coincidence
    of the walk order.
    """
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    a = store.upsert(sid="", path=str(alpha), label="Alpha")
    b = store.upsert(sid="", path=str(beta), label="Beta")
    root = tmp_path / "chats"
    _on_disk(root, "chat-4000000000000-aaaa01", 400,
             [_msg("user", "the newest alpha question")], project=str(alpha))
    _on_disk(root, "chat-3000000000000-bbbb01", 300,
             [_msg("user", "a beta question")], project=str(beta))
    _on_disk(root, "chat-2000000000000-aaaa02", 200,
             [_msg("user", "where is the barometer")], project=str(alpha))
    _on_disk(root, "chat-1000000000000-none01", 100,
             [_msg("user", "a question about nothing in particular")])
    return store, a, b


@pytest.mark.asyncio
async def test_a_row_id_scopes_the_listing_and_the_DAEMON_is_what_filtered(
        tmp_path, fake_openai, monkeypatch):
    """AC-229. `?project=<row id>` serves that workspace's conversations only.

    Three assertions and they are different claims. The ROWS are the two filed
    under alpha, newest first -- a scope that returned everything, and one that
    returned the wrong workspace, both fail here. The `projectId` on each row
    is the id the page would scope BY, so a rail can mark the open workspace
    without matching directory strings for itself. And `projectLabel` is what
    the OWNER called the row rather than the path component the page used to
    cut for itself.
    """
    store, a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)

    async with _http(hub, monkeypatch) as client:
        answer = await client.get(f"/api/chats?project={a.id}")
        doc = await answer.json()
        every = await (await client.get("/api/chats")).json()

    assert answer.status == 200, doc
    assert [c["id"] for c in doc["chats"]] == ["chat-4000000000000-aaaa01",
                                              "chat-2000000000000-aaaa02"], (
        f"the scope did not serve alpha's conversations newest first: "
        f"{[c['id'] for c in doc['chats']]}")
    assert {c["projectId"] for c in doc["chats"]} == {a.id}, (
        f"a scoped row does not say which project row it is in: {doc['chats']}")
    assert {c["projectLabel"] for c in doc["chats"]} == {"Alpha"}, (
        f"the row label is not the one the owner gave the project: "
        f"{doc['chats']}")
    assert len(every["chats"]) == 4, (
        f"the unscoped listing changed: {every['chats']}")


@pytest.mark.asyncio
async def test_the_EMPTY_scope_is_the_conversations_filed_nowhere(
        tmp_path, fake_openai, monkeypatch):
    """AC-229, and it is a real value rather than a spelling of "everything".

    Half of the owner's own records carry no project and `chats.py` refuses in
    writing to invent one for them -- so "no project" is a group that never
    empties and has to be reachable. A daemon that read a blank `?project=` as
    absent would serve all four here and nothing would look broken.
    """
    store, _a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats?project=")
        doc = await answer.json()

    assert answer.status == 200, doc
    assert [c["id"] for c in doc["chats"]] == ["chat-1000000000000-none01"], (
        f"the empty scope is not the unfiled conversations: {doc['chats']}")
    assert doc["chats"][0]["projectId"] == "", doc["chats"]
    assert doc["chats"][0]["projectLabel"] == "", (
        f"an unfiled conversation was given a workspace name: {doc['chats']}")


@pytest.mark.asyncio
async def test_a_project_no_row_has_is_a_400_THAT_NAMES_IT(tmp_path,
                                                           fake_openai,
                                                           monkeypatch):
    """`POST /api/project`'s rule and its wording, met again.

    400 rather than 404 because the page LISTED these rows: an id it does not
    recognise means the two disagree about what exists, and the owner's next
    action is a reload. And a REFUSAL rather than a quiet fall back to the
    whole list -- the house rule, on the one surface whose entire job is to
    leave conversations out, where "the filter stopped filtering" looks
    identical to "this project has everything in it".
    """
    store, _a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats?project=s-nobody-has-this")
        doc = await answer.json()

    assert answer.status == 400, doc
    assert "s-nobody-has-this" in str(doc), (
        f"the refusal does not name the id it refused: {doc}")
    assert "chats" not in doc, (
        f"a refused scope served conversations anyway: {doc}")


@pytest.mark.asyncio
async def test_a_scope_and_a_search_are_BOTH_in_force(tmp_path, fake_openai,
                                                      monkeypatch):
    """Matched AND scoped, never either (AC-185 composed with AC-229).

    The second half is the one with teeth: the same query scoped to the OTHER
    workspace must be empty. A daemon that applied only the query passes the
    first assertion, since the match happens to live in alpha.
    """
    store, a, b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)

    async with _http(hub, monkeypatch) as client:
        mine = await (await client.get(
            f"/api/chats?project={a.id}&q=barometer")).json()
        theirs = await (await client.get(
            f"/api/chats?project={b.id}&q=barometer")).json()
        loose = await (await client.get("/api/chats?q=barometer")).json()

    assert [c["id"] for c in mine["chats"]] == [
        "chat-2000000000000-aaaa02"], mine
    assert mine["q"] == "barometer", (
        f"the scoped search dropped the echo the page reads: {mine}")
    assert theirs["chats"] == [], (
        f"the query ignored the scope it was given: {theirs}")
    assert len(loose["chats"]) == 1, loose


# --------------------------------------------------------------------------- #
# docs/triage-list.md phase 1 -- status rides on the listing (AC-263)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_each_listed_row_carries_the_status_hub_status_for_answers(
        tmp_path, fake_openai, monkeypatch):
    """AC-263. A page must not be forced into N per-row requests to learn
    what is running -- `status` (and `elapsed_ms`, which phase 2 needs to
    show elapsed time without a second route) rides on the SAME listing,
    derived from `hub.status_for`, never a second source of truth.

    Three conversations, three different derivations: one untouched (idle),
    one that ran a real turn to completion (done, AC-59 holds its child),
    and one marked running BY HAND -- the idiom `test_worker_status.py`
    already uses for this exact derivation, because a real generation gives
    no stable window to catch between "running" and "done" without racing
    the fake's own event loop.
    """
    root = tmp_path / "chats"
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    s = attach_session(hub)
    await _final(hub, s, 1, "finish this one")
    done_id = store.current()["id"]

    idle_id = "chat-1000000000000-idle01"
    running_id = "chat-2000000000000-running01"
    _on_disk(root, idle_id, 100, [_msg("user", "an old, quiet question")])
    _on_disk(root, running_id, 200, [_msg("user", "go")])
    s.turn_by_chat[running_id] = 4242

    async with _http(hub, monkeypatch) as client:
        answer = await client.get("/api/chats")
        doc = await answer.json()

    assert answer.status == 200, doc
    by_id = {c["id"]: c for c in doc["chats"]}
    assert by_id[idle_id]["status"] == "idle", by_id
    assert by_id[running_id]["status"] == "running", by_id
    assert by_id[done_id]["status"] == hub.status_for(done_id) == "done", by_id
    assert all("elapsed_ms" in c for c in doc["chats"]), (
        f"phase 2 needs elapsed time on the SAME listing or it is forced "
        f"back to a per-row request: {doc['chats']}")


@pytest.mark.asyncio
async def test_status_rides_on_a_SCOPED_and_a_SEARCHED_listing_too(
        tmp_path, fake_openai, monkeypatch):
    """AC-263's other half. `?project=` and `?q=` are a DIFFERENT branch of
    `get_chats` than the plain listing -- `_chat_scope`/`search` rather than
    `session_list` -- and `_filed` is the one place both branches meet, so
    this is what proves the field is not a quirk of the plain branch alone.
    """
    store, a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)

    async with _http(hub, monkeypatch) as client:
        scoped = await (await client.get(f"/api/chats?project={a.id}")).json()
        searched = await (await client.get("/api/chats?q=barometer")).json()

    assert scoped["chats"] and all("status" in c for c in scoped["chats"]), (
        scoped)
    assert searched["chats"] and all("status" in c for c in searched["chats"]), (
        searched)


@pytest.mark.asyncio
@pytest.mark.parametrize("respell",
                         ["separator", "case", "both", "slashes", "all three"])
async def test_RESPELLING_a_rows_path_does_not_orphan_its_conversations(
        tmp_path, fake_openai, monkeypatch, respell):
    r"""AC-218, and it holds BY CONSTRUCTION rather than by a migration.

    The pane puts an `edit` button on every row, so the owner may add a
    trailing separator or change a drive letter's case at any time -- and the
    owner already has one path with a trailing backslash [MEASURED 2026-09-11].
    Nothing stores a conversation's row, so there is no second copy of the fact
    to go stale: the row is derived from the path the record already carries,
    through `shortcuts.same_dir`, which reads `E:\X`, `E:\X\`, `e:\x` and
    `E:/X` as one folder and four strings.

    **THE FORWARD-SLASH ROWS ARRIVED 2026-09-11**, after a review measured that
    spelling ORPHANING every conversation outright while the other three filed
    correctly -- `POST /api/shortcuts` accepts it and it is how this
    repository's own documents write paths. Its twin lives in
    `test_project_switch.py`, which asks the same four spellings of the SPAWN
    GATE: the defect was that the two sides answered differently.

    What this does NOT claim, and the distinction is the whole of the rule:
    moving a project to a DIFFERENT directory correctly leaves the old
    conversations behind, because the work happened there.
    """
    store, a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)
    spelled = {"separator": a.path + "\\",
               "case": a.path.upper(),
               "both": a.path.upper() + "\\",
               "slashes": a.path.replace("\\", "/"),
               "all three": a.path.upper().replace("\\", "/") + "/"}[respell]
    store.upsert(sid=a.id, path=spelled, label="Alpha")

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get(f"/api/chats?project={a.id}")).json()
        every = await (await client.get("/api/chats")).json()

    assert [c["id"] for c in doc["chats"]] == ["chat-4000000000000-aaaa01",
                                              "chat-2000000000000-aaaa02"], (
        f"re-spelling the row's path as {spelled!r} orphaned its "
        f"conversations: {doc['chats']}")
    filed = {c["id"]: c["projectId"] for c in every["chats"]}
    assert filed["chat-4000000000000-aaaa01"] == a.id, (
        f"the unscoped listing no longer files it under the row: {filed}")


@pytest.mark.asyncio
@pytest.mark.parametrize("respell", [None, "separator", "case", "slashes"])
async def test_the_LIVE_conversation_says_which_ROW_it_is_in(
        tmp_path, fake_openai, monkeypatch, respell):
    r"""`GET /api/session/current` carries `projectId`, like every listed row.

    Found by review 2026-09-11. The page held the raw `project` path from this
    route and compared it with `row.path` as a STRING at both of its "you are
    here" marks -- the lit tile on the rail and the popover's "working here" --
    so one legal path edit made the daemon and the page disagree about the live
    conversation's workspace: two conversations still filed under the row, and
    both marks silently out.

    The annotation is `_filed`'s, the same one the listing uses, because a
    second derivation is the drift AC-218 exists to prevent. `project` is
    UNCHANGED on the wire, so nothing reading it breaks.
    """
    store, a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)
    if respell is not None:
        store.upsert(sid=a.id, label="Alpha",
                     path={"separator": a.path + "\\",
                           "case": a.path.upper(),
                           "slashes": a.path.replace("\\", "/")}[respell])

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get("/api/session/current")).json()

    assert doc["id"] == "chat-4000000000000-aaaa01", doc
    assert doc["project"] == str(tmp_path / "alpha"), (
        f"`project` is the path the record carries and must not have been "
        f"rewritten by the annotation: {doc['project']!r}")
    assert doc["projectId"] == a.id, (
        f"the live conversation does not say which ROW it is in, so the page "
        f"has nothing to compare but directory strings (respelt {respell}): "
        f"{doc.get('projectId')!r}")
    assert doc["projectLabel"] == "Alpha", doc


@pytest.mark.asyncio
async def test_a_conversation_whose_ROW_WAS_REMOVED_is_still_listed(
        tmp_path, fake_openai, monkeypatch):
    """AC-225. Deleting a project must not delete a conversation.

    It keeps the path it was written with, matches no row, and is served in the
    unscoped list with an empty `projectId` and the DIRECTORY's own name as the
    label -- so the sidebar still says where the work happened. It is
    deliberately NOT in the unfiled scope: `project` is not blank, and
    pretending it is would be the record stating a fact nobody established. The
    unscoped list is where it lives, which is why that list stays the default.
    """
    store, a, _b = _scoped(tmp_path)
    hub, _, _ = _hub(tmp_path, fake_openai,
                     store=chats.ChatStore(tmp_path / "chats"),
                     shortcuts=store)
    store.remove(a.id)

    async with _http(hub, monkeypatch) as client:
        every = await (await client.get("/api/chats")).json()
        unfiled = await (await client.get("/api/chats?project=")).json()
        gone = await client.get(f"/api/chats?project={a.id}")
        refused = await gone.json()
        opened = await client.get("/api/chats/chat-4000000000000-aaaa01")
        transcript = await opened.json()

    orphan = next(c for c in every["chats"]
                  if c["id"] == "chat-4000000000000-aaaa01")
    assert len(every["chats"]) == 4, (
        f"removing a project row took its conversations with it: "
        f"{every['chats']}")
    assert orphan["projectId"] == "", orphan
    assert orphan["projectLabel"] == "alpha", (
        f"the orphan lost the only readable trace of where it ran: {orphan}")
    assert [c["id"] for c in unfiled["chats"]] == [
        "chat-1000000000000-none01"], (
        f"an orphan was quietly re-filed as unfiled: {unfiled['chats']}")
    assert gone.status == 400, refused
    assert opened.status == 200, transcript
    assert transcript["id"] == "chat-4000000000000-aaaa01", (
        f"the conversation is listed and cannot be opened: {transcript}")


@pytest.mark.asyncio
async def test_NEW_CHAT_and_a_REWIND_both_stay_in_the_workspace_scope(
        tmp_path, fake_openai, monkeypatch):
    """`rotate()` and `rewind()` carry the project, seen from the SCOPE.

    Both already copy the field and both were caught missing it by review on
    2026-09-11. What is asserted here is the consequence the owner would
    actually notice: a conversation that lost its project is not merely
    mislabelled, it VANISHES from the workspace it was had in, and a rewind's
    copy is exactly the row they would go looking for.
    """
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    a = store.upsert(sid="", path=str(alpha), label="Alpha")
    # Its own empty directory: `_scoped` leaves four records on disk and a
    # store attaches to the NEWEST of them, so the appends below would land in
    # a fixture conversation and the rewind would aim at the wrong message.
    live = chats.ChatStore(tmp_path / "live-chats")
    live.rotate(a.path)                         # born in Alpha (AC-287)
    ids = _spoken(live, [("user", "first question"),
                         ("assistant", "first answer"),
                         ("user", "second question")])
    hub, _, _ = _hub(tmp_path, fake_openai, store=live, shortcuts=store)

    done = live.rewind(ids[2])
    rotated = live.rotate()["id"]

    async with _http(hub, monkeypatch) as client:
        doc = await (await client.get(f"/api/chats?project={a.id}")).json()

    scoped = [c["id"] for c in doc["chats"]]
    assert done is not None and done["snapshot"] in scoped, (
        f"the rewind's copy left the workspace: {scoped}")
    assert rotated in scoped, (
        f"New chat left the workspace the owner was working in: {scoped}")


# --------------------------------------------------------------------------- #
# THE REGISTRY: the chat you click is the chat you talk to
# (AC-239, AC-240, docs/parallel-agents.md phase 4)
#
# `ChatStore` held ONE conversation, and the page had to LEAVE a past one
# before it could send anything -- so sending abandoned the chat that had just
# been clicked and put the message in the live one, which is the owner's own
# rule inverted. What replaces the slot is a registry of HELD conversations
# plus a pointer at the one a turn goes to, and `POST /api/session/open` moves
# that pointer.
#
# Two properties carry the section and they fail in opposite directions.
# AC-240 is that a send reaches the conversation the owner opened; AC-239 is
# that OPENING one reaches nothing at all -- no child, no session file, no
# record touched -- because browsing history must never start a
# full-capability coding agent.
# --------------------------------------------------------------------------- #
async def _open(client, chat_id: str):
    """`POST /api/session/open`, answering `(status, body)`.

    The body is read inside the caller's `_http` block for that helper's
    reason: reading `.json()` after the client closes raises
    `ClientConnectionError`, which reads like a daemon fault.
    """
    answer = await client.post("/api/session/open", json={"id": chat_id})
    return answer.status, await answer.json()


def _two_on_disk(root: Path) -> tuple[str, str]:
    """A past conversation and a newer one, the newer being what a boot holds.

    The PAST one is the interesting record: `_attach` picks the newest
    `updatedAt`, so anything that reaches the older one had to be opened.
    """
    past = _on_disk(root, "chat-1000000000000-pppppp", 100,
                    [_msg("user", "the older conversation", 100)])
    newest = _on_disk(root, "chat-2000000000000-nnnnnn", 200,
                      [_msg("user", "the one the daemon booted into", 200)])
    return past, newest


@pytest.mark.asyncio
async def test_opening_a_past_conversation_is_where_the_next_turn_GOES(
        tmp_path, fake_openai, monkeypatch):
    """AC-240, end to end: the chat you clicked is the chat you talk to.

    The assertion with teeth is the SECOND one. A daemon that opened the
    conversation for display and then appended into the live one satisfies
    every status check and every transcript the route answers -- and the owner
    finds out weeks later, reading a conversation with somebody else's
    question in it. So this asserts BOTH records: the opened one gained the
    message, and the one the daemon booted into is byte-for-byte what it was.
    """
    root = tmp_path / "chats"
    past, newest = _two_on_disk(root)
    before = (root / f"{newest}.json").read_text(encoding="utf-8")
    hub, child, store = _hub(tmp_path, fake_openai,
                             store=chats.ChatStore(root))
    assert store.current()["id"] == newest, "the fixture did not boot as set up"

    async with _http(hub, monkeypatch) as client:
        status, doc = await _open(client, past)
        assert status == 200, doc
        assert doc["id"] == past, doc
        assert [m["content"] for m in doc["messages"]] == [
            "the older conversation"], doc

        s = attach_session(hub)
        fake_openai.verdict(route="converse")
        await _final(hub, s, 1, "carry on from here")

    reopened = store.find(past)
    assert [m["content"] for m in reopened["messages"]][:2] == [
        "the older conversation", "carry on from here"], (
        f"the message did not land in the conversation that was opened: "
        f"{reopened['messages']}")
    assert (root / f"{newest}.json").read_text(encoding="utf-8") == before, (
        "the conversation the daemon was holding was written to by a turn "
        "that belonged to another one (AC-240)")
    assert store.current()["id"] == past, (
        "the opened conversation stopped being the one a turn goes to")


@pytest.mark.asyncio
async def test_opening_a_conversation_SPAWNS_NOTHING(tmp_path, fake_openai,
                                                     monkeypatch):
    """AC-239. Opening any conversation starts no child; the SEND does.

    Asserted as a spawn COUNT off the fake harness and not as a status, for
    `test_terminal_route.py`'s reason: a route that answered 200 and started a
    coding agent anyway passes every other check in this file, and what it
    started can already have written to disk by the time anybody notices.

    The second half is what makes the first mean anything: after a send the
    count is 1. A test that only proved "nothing spawned" would pass against a
    daemon that never spawns at all.
    """
    root = tmp_path / "chats"
    past, _ = _two_on_disk(root)
    hub, child, store = _hub(tmp_path, fake_openai,
                             store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        status, doc = await _open(client, past)
        assert status == 200, doc
        assert child.specs == [], (
            f"opening a conversation started a coding agent: {child.specs}")
        assert not list(root.glob("*.session.json")), (
            "opening a conversation minted an agent session file")

        s = attach_session(hub)
        fake_openai.verdict(route="converse")
        await _final(hub, s, 1, "now do something")

    assert len(child.specs) == 1, (
        f"the send did not start the child, so the count above proves "
        f"nothing: {child.specs}")


@pytest.mark.asyncio
async def test_TWO_conversations_are_held_and_neither_interleaves(
        tmp_path, fake_openai, monkeypatch):
    """Two conversations open, a turn in each, and back to the first.

    The interleaving this catches is the one the registry exists for: a store
    that re-read a record on every open would hand back a SECOND dict for a
    file it was already holding, and the next append would write one of them
    over the other -- so the third turn below would land in a record that had
    forgotten the first.
    """
    root = tmp_path / "chats"
    first = _on_disk(root, "chat-1000000000000-first1", 100,
                     [_msg("user", "about the first thing", 100)])
    second = _on_disk(root, "chat-2000000000000-second", 200,
                      [_msg("user", "about the second thing", 200)])
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    s = attach_session(hub)

    async with _http(hub, monkeypatch) as client:
        assert (await _open(client, first))[0] == 200
        fake_openai.verdict(route="converse")
        await _final(hub, s, 1, "more about the first")
        assert (await _open(client, second))[0] == 200
        fake_openai.verdict(route="converse")
        await _final(hub, s, 2, "more about the second")
        assert (await _open(client, first))[0] == 200
        fake_openai.verdict(route="converse")
        await _final(hub, s, 3, "still the first")

    said = {cid: [m["content"] for m in store.find(cid)["messages"]
                  if m["role"] == "user"]
            for cid in (first, second)}
    assert said[first] == ["about the first thing", "more about the first",
                           "still the first"], said
    assert said[second] == ["about the second thing",
                            "more about the second"], said


def test_a_session_file_is_minted_into_the_conversation_it_is_FOR(tmp_path):
    """The hinge (phase 4 step 2): `session_ref` takes the chat.

    It used to mint into the store's one live slot while being called per
    spawn from a resolver closure that names no conversation. Asserted from
    the RESOLVER, because that is the caller the criterion is about: naming
    the other conversation must write the reference into the other record and
    leave the current one with none.
    """
    root = tmp_path / "chats"
    past, newest = _two_on_disk(root)
    store = chats.ChatStore(root)
    prefs = _prefs(tmp_path)
    resolve = butlerd.harness_spec_resolver(prefs, store)

    other = store.open(past)
    store.open(newest)                   # and the CURRENT one is the newer

    spec = resolve(None, other)

    def ref_on_disk(cid):
        return json.loads((root / f"{cid}.json").read_text(
            encoding="utf-8"))["sessionRef"]

    assert past in spec.session_file, (
        f"the child for {past} was pointed at {spec.session_file}")
    assert str(root / ref_on_disk(past)) == spec.session_file, (
        "the reference reached the spec and not the record it belongs to")
    assert ref_on_disk(newest) == "", (
        "one conversation's spawn minted its session file into ANOTHER "
        "conversation's record, which is the crossed wire this test exists "
        "for")


def test_a_reference_cannot_be_minted_into_a_record_the_store_is_not_HOLDING(
        tmp_path):
    """A caller-side assertion, because the callee's own tests were green
    (D-18).

    A dict off `_records()` is a fresh read of a file. Minting into one writes
    a session reference that the next append -- from the dict the store
    actually holds -- silently overwrites, so the agent's memory and the
    transcript would disagree with nothing on any wire saying so. It is
    refused by name instead.
    """
    root = tmp_path / "chats"
    past, _ = _two_on_disk(root)
    store = chats.ChatStore(root)
    loose = dict(store.find(past))
    loose["sessionRef"] = ""
    loose["harness"] = ""

    with pytest.raises(ValueError) as caught:
        store.session_ref(loose, "pi")
    assert past in str(caught.value), str(caught.value)


@pytest.mark.asyncio
async def test_deleting_a_HELD_conversation_that_is_not_current_does_not_rotate(
        tmp_path, fake_openai, monkeypatch):
    """The plural half of "deleting the live conversation rotates first".

    While the store had one slot, "held in memory" and "the one a turn goes
    to" were the same conversation and one identity test answered both. They
    are now different: a conversation the owner opened and then went back from
    is HELD without being current, and unlinking one of those leaves a dict
    that recreates its file on the next append -- the same silent undo, one
    conversation over. So the test is `is_open`, and deleting a held-but-not-
    current record must NOT rotate: the owner deleted some other row and the
    conversation they are in is not supposed to end.
    """
    root = tmp_path / "chats"
    doomed, newest = _two_on_disk(root)
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        assert (await _open(client, doomed))[0] == 200
        assert (await _open(client, newest))[0] == 200
        assert store.is_open(doomed), "the fixture did not hold both"
        answer = await client.delete("/api/chats/" + doomed)
        doc = await answer.json()

    assert answer.status == 200, doc
    assert store.current()["id"] == newest, (
        "deleting a conversation the owner was not in ENDED the one they "
        "were in")
    assert not store.is_open(doomed), (
        "the deleted conversation is still held, so the next append recreates "
        "the file that was just removed")
    store.append(store.current()["id"], "user", "a word after the delete")
    assert not (root / f"{doomed}.json").exists(), (
        "the deleted conversation's file came BACK on the next append")


def test_a_restart_HOLDS_ONE_conversation_and_the_rest_are_openable(tmp_path):
    """The plural half of `_attach` (AC-35, phase 4).

    Nothing on disk records which conversations were open, so a boot comes
    back holding exactly ONE -- the newest `updatedAt`, which is what
    `test_a_restart_reattaches_to_the_newest_updated_at` proves and which is
    unchanged. What this adds is the other side: every other record is reached
    by OPENING it, and a failed open moves nothing.
    """
    root = tmp_path / "chats"
    past, newest = _two_on_disk(root)
    store = chats.ChatStore(root)

    assert store.is_open(newest) and not store.is_open(past), (
        "a boot came back holding a conversation nobody was having")
    assert store.open("chat-0000000000000-nobody") is None, (
        "the store opened a conversation it has no record of")
    assert store.current()["id"] == newest, (
        "a failed open moved the conversation a turn goes to")
    assert store.open(past)["id"] == past
    assert store.current()["id"] == past and store.is_open(newest), (
        "opening one conversation dropped the other")


def test_a_new_chat_after_an_OPEN_stays_in_the_opened_workspace(tmp_path):
    """`rotate()` mints and REGISTERS, and it carries the project forward
    from the conversation that is actually current.

    The trap the registry adds: a rotate that still read the boot-time slot
    would carry the WRONG workspace forward, and the owner would press New
    chat in a project and find the record filed in another one.
    """
    root = tmp_path / "chats"
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    filed = _on_disk(root, "chat-1000000000000-alpha1", 100,
                     [_msg("user", "an alpha question", 100)],
                     project=str(alpha))
    _on_disk(root, "chat-2000000000000-nowhere", 200,
             [_msg("user", "filed nowhere", 200)])
    store = chats.ChatStore(root)

    store.open(filed)
    fresh = store.rotate()

    assert fresh["project"] == str(alpha), (
        f"New chat left the workspace the owner had open: {fresh['project']!r}")
    assert store.is_open(fresh["id"]) and not store.is_open(filed), (
        "the rotation did not register the new conversation, or left the old "
        "one held -- a record nothing may write to again")
    assert store.current()["id"] == fresh["id"]


@pytest.mark.asyncio
async def test_opening_a_conversation_takes_the_ROUTERS_short_memory_with_it(
        tmp_path, fake_openai, monkeypatch):
    """`hub.history` belongs to the conversation (AC-64's argument).

    It is the rolling transcript handed to every brain as
    `TurnContext.history`, and it is read by the one model that runs on every
    single utterance. Left in place, the first turn of the conversation the
    owner just opened would be prompted with the last turns of the one they
    left -- a crossed wire on the model that decides how everything is routed,
    and invisible from the room.
    """
    root = tmp_path / "chats"
    past, _ = _two_on_disk(root)
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    s = attach_session(hub)
    fake_openai.verdict(route="converse")
    await _final(hub, s, 1, "something in the conversation being left")
    assert hub.history, "the fixture recorded no history to lose"

    async with _http(hub, monkeypatch) as client:
        assert (await _open(client, past))[0] == 200

    assert not hub.history, (
        "the router's short memory followed the owner into the conversation "
        "they opened")


@pytest.mark.asyncio
async def test_opening_an_unknown_conversation_is_a_404_that_NAMES_it(
        tmp_path, fake_openai, monkeypatch):
    """`get_chat`'s refusal, for `get_chat`'s reason, plus the empty body.

    A 200 for an id nobody has would tell the page it is now talking to a
    conversation that does not exist, and the next thing said would go
    somewhere else entirely. The traversal row is `find`'s rule seen from this
    route: the id is a KEY of what this store listed, so `..\\..\\secret` is a
    conversation nobody has rather than a path.
    """
    root = tmp_path / "chats"
    _two_on_disk(root)
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))
    was = store.current()["id"]

    async with _http(hub, monkeypatch) as client:
        status, doc = await _open(client, "chat-0000000000000-nobody")
        assert status == 404, doc
        assert "chat-0000000000000-nobody" in doc["error"], doc
        blank = await client.post("/api/session/open", json={})
        assert blank.status == 400, await blank.text()
        traversal = await _open(client, "..\\..\\secret")
        assert traversal[0] == 404, traversal

    assert store.current()["id"] == was, (
        "a refused open still moved the conversation a turn goes to")


@pytest.mark.asyncio
async def test_the_THREE_conversation_routes_answer_ONE_shape(tmp_path,
                                                              fake_openai,
                                                              monkeypatch):
    """"The same shape as `GET /api/session/current`" is a claim THREE routes
    make about themselves, and it has now broken twice in two commits that
    each said they had fixed it.

    `GET /api/session/current`, `GET /api/chats/{id}` and
    `POST /api/session/open` each promise it in their own first line, and each
    time a field was added it reached two of the three: `projectId` the first
    time, `questions` the second. Measured -- `POST /api/session/open`
    answered no `questions` at all, so a device that connects after the
    broadcast and then clicks the conversation with the open card draws no
    card and has no way to learn there is one.

    Asserted as the KEY SETS being equal rather than against a list written
    down here, because a written list is a fourth place for the same names to
    drift (`test_protocol_doc.py`'s rule). The route that answers a DIFFERENT
    conversation is deliberately the one with the same keys: the shape is the
    claim, not the contents.
    """
    root = tmp_path / "chats"
    past, newest = _two_on_disk(root)
    hub, _, store = _hub(tmp_path, fake_openai, store=chats.ChatStore(root))

    async with _http(hub, monkeypatch) as client:
        live = await (await client.get("/api/session/current")).json()
        peeked = await (await client.get(f"/api/chats/{past}")).json()
        status, opened = await _open(client, past)

    assert status == 200, opened
    assert set(live) == set(peeked) == set(opened), (
        f"the three routes that each claim `GET /api/session/current`'s exact "
        f"shape answer three different shapes.\n"
        f"  /api/session/current: {sorted(live)}\n"
        f"  /api/chats/{{id}}:      {sorted(peeked)}\n"
        f"  /api/session/open:    {sorted(opened)}")
    assert "questions" in opened, (
        "a page repainting from `POST /api/session/open` alone draws a "
        "conversation with no question card on it (AC-249)")
    assert opened["id"] == past and live["id"] == newest, (opened, live)


@pytest.mark.asyncio
async def test_opening_is_404_without_a_harness(tmp_path, monkeypatch):
    """Endpoint mode holds no conversation, so there is none to open --
    `GET /api/session/current`'s 404 and its wording, because a daemon that
    keeps no record is a different fact from an id nobody has."""
    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"), tts=QuietTts(),
                      brain=ports.build(ports.BRAINS, "fake"),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=_prefs(tmp_path))

    async with _http(hub, monkeypatch) as client:
        answer = await client.post("/api/session/open",
                                   json={"id": "chat-1000000000000-aaaaaa"})
        doc = await answer.json()

    assert answer.status == 404, doc
    assert doc["error"] == butlerd.NO_CONVERSATION, doc
