"""Butler answers other people and groups (docs/messaging.md 6.1, phase M9).

AC-535 (who is answered: the classifier and the guest door are pinned by the
sidecar's `guest.test.mjs`; here the daemon side and the settings default),
AC-536 (a guest turn has nothing to give away: the request carries no tools,
no USER.md, no memory, no other chat, and a SOURCE test holds the guest path
apart from the owner's), AC-537 ("Butler: ", the guest's own standing chat,
20 an hour), AC-538 (the user's own message silences Butler there for 30
minutes), AC-563 (a guest's media is refused and never stored or shown to a
model). MS-21: guests never get questions; nothing about them is spoken.

Driven at the daemon with a fake WhatsApp door, an injected clock, and a
FakeOpenAI where the request on the wire is the assertion; the stream test
uses the fake sidecar process. Nothing here touches WhatsApp.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from assets import wa_archive                                  # noqa: E402
from conftest import attach_session                            # noqa: E402
from test_library_voice import _harness_hub                    # noqa: E402
from test_whatsapp_events import _push, _requests, _until, fake_argv  # noqa: E402,F401
from daemon import butlerd                                     # noqa: E402
from daemon import persona as persona_mod                      # noqa: E402
from daemon import settings as settings_mod                    # noqa: E402
from daemon import whatsapp as wa_mod                          # noqa: E402
from daemon.adapters import brain_endpoint                     # noqa: E402
from daemon.connections import Connection                      # noqa: E402

ALICE = "15551230000@s.whatsapp.net"
T0 = 1_800_000_000.0


class GuestWa:
    """The daemon's WhatsApp owner as `Guests` sees it. The owner's doors
    raise: a guest answer through them would be the owner's path."""

    def __init__(self, name="Sam"):
        self.replies: list[tuple[str, str, str | None]] = []
        self.fetched: list[str] = []
        self.name = name

    async def guest_reply(self, jid, text, quote=None):
        self.replies.append((jid, text, quote))
        return f"G{len(self.replies)}"

    async def own_name(self):
        return self.name

    async def media(self, media_id):
        self.fetched.append(media_id)
        return b"never"

    async def reply(self, *a, **k):
        raise AssertionError("a guest answer went through the OWNER's door")

    async def presence(self, *a, **k):
        raise AssertionError("typing was shown for a guest")


class Ask:
    def __init__(self, answer="She is out until five."):
        self.calls = []
        self.answer = answer

    async def __call__(self, system, history, text):
        self.calls.append((system, history, text))
        return self.answer


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _guest(mid, text="is she around?", jid=ALICE, **extra):
    return {"type": "message", "tag": "guest", "id": mid, "jid": jid,
            "sender": extra.pop("sender", jid), "pushName": "Alice",
            "isGroup": jid.endswith("@g.us"), "text": text, "ts": 1790950000,
            "quoted": None, **extra}


async def _drain(guests, timeout=10.0):
    deadline = time.monotonic() + timeout
    while guests.tasks:
        assert time.monotonic() < deadline, "guest work never finished"
        await asyncio.wait(list(guests.tasks), timeout=0.05)


def _hub(tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    return hub


async def _rows(hub):
    root = hub.shortcuts.root_id()
    return await asyncio.to_thread(hub.brain.session_list, None,
                                   hub.project_filter(root))


async def _guest_chats(hub, jid=ALICE):
    return [r for r in await _rows(hub)
            if wa_mod.is_guest_chat(r) and r["origin"]["jid"] == jid]


async def _lines(hub, chat):
    record = await asyncio.to_thread(hub.brain.session_chat, chat)
    return [(m["role"], m["content"]) for m in record["messages"]]


# --------------------------------------------------------------------------- #
# AC-536: a guest turn has nothing to give away
# --------------------------------------------------------------------------- #
SOUL_MARK = "Your name is Jeeves. SOUL-MARK-FIG"
USER_SECRET = "USER-SECRET-PLUM"
MEMORY_SECRET = "MEMORY-SECRET-QUINCE"
CHAT_SECRET = "OTHER-CHAT-SECRET-PEAR"


@pytest.mark.asyncio
async def test_AC536_THE_GUEST_REQUEST_CARRIES_NO_TOOLS_NO_USER_NO_MEMORY_NO_OTHER_CHAT(
        tmp_path, monkeypatch, fake_openai):
    hub = _hub(tmp_path, monkeypatch)
    (tmp_path / "SOUL.md").write_text(SOUL_MARK, encoding="utf-8")
    (tmp_path / "USER.md").write_text(f"My bank PIN is {USER_SECRET}.",
                                      encoding="utf-8")
    hub.memory.write(MEMORY_SECRET, "Sam's sister is called Rosa.")
    desk = attach_session(hub, "desk")
    other = (await hub.new_conversation(None))["id"]
    desk.showing = other
    await hub._typed(desk, {"type": "typed", "text": CHAT_SECRET})
    await _until(lambda: not hub._turn_in_flight(other), "the desk turn")
    # The instrument: each secret IS where the owner's path reads it.
    assert USER_SECRET in persona_mod.files_for(tmp_path)["user"].effective()
    assert MEMORY_SECRET in hub.memory.index()
    assert ("user", CHAT_SECRET) in await _lines(hub, other)
    hub.connections.add_manual(Connection(id="fakeoa", label="Fake",
                                          base_url=fake_openai.base_url))
    hub.settings.set("endpoint_model", {"conn": "fakeoa",
                                        "model": "fake-reply"})
    fake_openai.answer("She is out until five.")
    wa = GuestWa()
    # The production wiring: `make_app` builds exactly this.
    guests = wa_mod.Inbound(hub, wa,
                            queue_folder=tmp_path / "phone").guests
    await guests.message(_guest("G1", "is Sam free today?"))
    await _drain(guests)

    body = fake_openai.last_body()
    assert "tools" not in body and "tool_choice" not in body, body.keys()
    wire = json.dumps(body)
    for secret in (USER_SECRET, MEMORY_SECRET, CHAT_SECRET):
        assert secret not in wire, f"{secret} reached a guest's model"
    assert body["messages"][0] == {"role": "system", "content":
                                   persona_mod.dialogue.soul_note(SOUL_MARK)}
    assert body["messages"][1] == {"role": "system", "content":
                                   wa_mod.GUEST_RULE.format(name="Alice",
                                                            user="Sam")}
    assert body["messages"][-1] == {"role": "user",
                                    "content": "is Sam free today?"}
    assert len(body["messages"]) == 3, body["messages"]
    assert wa.replies == [(ALICE, "Butler: She is out until five.", None)]


@pytest.mark.asyncio
async def test_on_the_shipped_defaults_a_guest_gets_the_fixed_line_and_the_reason_is_logged(
        tmp_path, monkeypatch, capfd):
    """`endpoint_model` ships blank: the guest path has no model it may ask
    without tools, so it says so in one fixed line (AC-536)."""
    hub = _hub(tmp_path, monkeypatch)
    assert settings_mod.BY_KEY["endpoint_model"].default == {"conn": "",
                                                             "model": ""}
    wa = GuestWa()
    guests = wa_mod.Inbound(hub, wa,
                            queue_folder=tmp_path / "phone").guests
    await guests.message(_guest("G1"))
    await _drain(guests)
    assert wa.replies == [(ALICE, "Butler: " + wa_mod.GUEST_CANNOT_REPLY,
                           None)]
    assert "endpoint_model names no connection" in capfd.readouterr().err


# The owner path's names. The guest path names none of them: the persona's
# USER/memory blocks, every tool list and tool runner, the turn machinery,
# sessions and speech, and the harness spawn.
FORBIDDEN_NAMES = {
    "blocks", "append_file", "files_for", "user_note", "memory_note",
    "persona_for", "MemoryStore", "memory", "TOOLS", "offered", "AgentTools",
    "agent_send", "library_tool", "_typed", "PhoneSession", "RunSession",
    "Pending", "sessions", "announce", "turn", "converse", "proceed",
    "harness_for", "spec_for", "stream", "tools", "questions", "attachments",
    "transcribe_clip", "tts"}
#: Attribute-only: the owner's reply door, typing, and fetching a file.
FORBIDDEN_ATTRS = {"reply", "presence", "media"}


def _used(node) -> tuple[set, set]:
    names, attrs = set(), set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            attrs.add(n.attr)
    return names, attrs


def _class(module, name):
    tree = ast.parse(Path(inspect.getsourcefile(module)).read_text("utf-8"))
    return next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == name)


@pytest.mark.parametrize("module,cls", [(wa_mod, "Guests"),
                                        (brain_endpoint, "GuestCall")])
def test_AC536_SOURCE_the_guest_path_never_names_the_owner_path(module, cls):
    names, attrs = _used(_class(module, cls))
    every = names | attrs
    assert not every & FORBIDDEN_NAMES, sorted(every & FORBIDDEN_NAMES)
    assert not attrs & FORBIDDEN_ATTRS, sorted(attrs & FORBIDDEN_ATTRS)


def test_AC536_SOURCE_the_one_model_call_is_complete_which_takes_no_tools():
    names, attrs = _used(_class(brain_endpoint, "GuestCall"))
    assert "complete" in attrs
    assert "tools" not in inspect.signature(
        brain_endpoint.llm.Client.complete).parameters, (
        "complete() grew a tools parameter: the guest call is no longer "
        "tool-less by construction")


def test_AC536_SOURCE_soul_file_builds_no_handle_on_USER_md():
    tree = ast.parse(textwrap.dedent(inspect.getsource(persona_mod.soul_file)))
    names, attrs = _used(tree)
    assert "files_for" not in names | attrs
    assert not any("user" in str(c.value).lower() for c in ast.walk(tree)
                   if isinstance(c, ast.Constant) and isinstance(c.value, str)
                   and not c.value.startswith("SOUL.md"))
    assert persona_mod.soul_file(Path("x")).key == "soul"


# --------------------------------------------------------------------------- #
# AC-537: "Butler: ", the guest's own standing chat, 20 an hour
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_AC537_EVERY_REPLY_STARTS_BUTLER_AND_LANDS_IN_THAT_GUESTS_STANDING_CHAT(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    wa, ask = GuestWa(), Ask()
    guests = wa_mod.Guests(hub, wa, ask=ask, soul=lambda: "", clock=Clock())
    for mid in ("G1", "G2"):
        await guests.message(_guest(mid, f"question {mid}"))
        await _drain(guests)
    assert [r[1] for r in wa.replies] == ["Butler: She is out until five."] * 2
    assert all(r[0] == ALICE for r in wa.replies)
    chats = await _guest_chats(hub)
    assert len(chats) == 1, "one standing chat per guest, reused"
    assert chats[0]["title"] == "WhatsApp: Alice"
    assert await _lines(hub, chats[0]["id"]) == [
        ("assistant", "Alice wrote: question G1"),
        ("assistant", "Butler: She is out until five."),
        ("assistant", "Alice wrote: question G2"),
        ("assistant", "Butler: She is out until five.")], (
        "nothing a guest wrote is recorded as the owner's words")
    # The second message saw the first exchange, from memory only.
    assert ask.calls[1][1] == [
        {"role": "user", "content": "question G1"},
        {"role": "assistant", "content": "She is out until five."}]


@pytest.mark.asyncio
async def test_AC537_AT_MOST_20_AN_HOUR_then_silence_recorded_once_until_the_hour_rolls(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    wa, clock = GuestWa(), Clock()
    guests = wa_mod.Guests(hub, wa, ask=Ask(), soul=lambda: "", clock=clock)
    assert wa_mod.GUEST_REPLIES_PER_HOUR == 20
    for i in range(23):
        await guests.message(_guest(f"M{i}", f"q{i}"))
        await _drain(guests)
        clock.t += 60
    assert len(wa.replies) == 20
    chat = (await _guest_chats(hub))[0]["id"]
    silent = [t for _, t in await _lines(hub, chat)
              if t.startswith("Butler answered")]
    assert len(silent) == 1, silent
    assert "20 times" in silent[0]
    clock.t = T0 + wa_mod.GUEST_HOUR_S + 1          # the first reply rolls out
    await guests.message(_guest("LATE", "still there?"))
    await _drain(guests)
    assert len(wa.replies) == 21


@pytest.mark.asyncio
async def test_a_group_guest_is_named_from_the_archive_quoted_and_the_speaker_is_named(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    archive = wa_archive.build(tmp_path / "wa" / "whatsapp.db", int(T0))
    wa, ask = GuestWa(), Ask("Lunch is at noon.")
    guests = wa_mod.Guests(hub, wa, ask=ask, soul=lambda: "",
                           archive=archive, clock=Clock())
    await guests.message(_guest("Q7", "when is lunch?", jid=wa_archive.FAMILY,
                                sender=wa_archive.BRUNO, pushName="Bruno"))
    await _drain(guests)
    system, _history, text = ask.calls[0]
    assert text == "Bruno: when is lunch?"
    assert wa_mod.GUEST_RULE.format(name="Family Group",
                                    user="Sam") in system
    assert wa.replies == [(wa_archive.FAMILY, "Butler: Lunch is at noon.",
                           "Q7")]
    assert (await _guest_chats(hub, wa_archive.FAMILY))[0]["title"] == (
        "WhatsApp: Family Group")


# --------------------------------------------------------------------------- #
# AC-538: the human takes over
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_AC538_THE_USERS_OWN_MESSAGE_SILENCES_BUTLER_THERE_FOR_30_MINUTES(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    wa, clock = GuestWa(), Clock()
    guests = wa_mod.Guests(hub, wa, ask=Ask(), soul=lambda: "", clock=clock)
    await guests.message(_guest("G1"))
    await _drain(guests)
    guests.takeover({"type": "takeover", "jid": ALICE, "ts": 1})
    await _drain(guests)
    clock.t += wa_mod.TAKEOVER_S - 1
    await guests.message(_guest("G2", "hello?"))
    await _drain(guests)
    assert len(wa.replies) == 1, "Butler answered over the human"
    other = "15550000099@s.whatsapp.net"
    await guests.message(_guest("O1", "hi", jid=other))
    await _drain(guests)
    assert wa.replies[-1][0] == other, "a takeover silences ONE chat only"
    clock.t += 2
    await guests.message(_guest("G3", "anyone?"))
    await _drain(guests)
    assert wa.replies[-1][0] == ALICE and len(wa.replies) == 3
    assert wa_mod.TAKEOVER_S == 30 * 60
    chat = (await _guest_chats(hub))[0]["id"]
    assert any(t.startswith("You wrote in this chat yourself")
               for _, t in await _lines(hub, chat))


# --------------------------------------------------------------------------- #
# AC-563: a guest's media
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "audio", "document", "video"])
async def test_AC563_GUEST_MEDIA_IS_REFUSED_NEVER_FETCHED_STORED_OR_SHOWN(
        tmp_path, monkeypatch, kind):
    hub = _hub(tmp_path, monkeypatch)
    wa, ask = GuestWa(), Ask()
    guests = wa_mod.Guests(hub, wa, ask=ask, soul=lambda: "", clock=Clock())
    await guests.message(_guest("F1", "look at this caption", media={
        "kind": kind, "id": "MEDIA1", "tooLarge": False}))
    await _drain(guests)
    assert wa.replies == [(ALICE, "Butler: I can only read text here.", None)]
    assert wa.fetched == [] and ask.calls == []
    stored = hub.attachments.root if hasattr(hub.attachments, "root") else None
    if stored is not None and Path(stored).exists():
        assert not any(Path(stored).iterdir()), "a guest's file was stored"


# --------------------------------------------------------------------------- #
# Separation from the owner's path (AC-519, MS-21)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_owners_root_chat_is_never_a_guests_chat(tmp_path,
                                                           monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    inbound = wa_mod.Inbound(hub, GuestWa(),
                                   queue_folder=tmp_path / "phone")
    inbound.guests = wa_mod.Guests(hub, inbound.wa, ask=Ask(),
                                   soul=lambda: "", clock=Clock())
    await inbound.guests.message(_guest("G1"))
    await _drain(inbound.guests)
    guest_chat = (await _guest_chats(hub))[0]["id"]
    assert (await _rows(hub))[0]["id"] == guest_chat, "the guest's is newest"
    assert await inbound._target() != guest_chat


@pytest.mark.asyncio
async def test_a_guest_message_is_never_offered_as_an_answer_and_runs_no_session(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    owner = wa_mod.WhatsApp(hub.settings, tmp_path / "wa")
    owner.inbound = wa_mod.Inbound(hub, GuestWa(),
                                   queue_folder=tmp_path / "phone")
    owner.inbound.guests = wa_mod.Guests(hub, owner.inbound.wa, ask=Ask(),
                                         soul=lambda: "", clock=Clock())
    offered = []

    async def hook(incoming):
        offered.append(incoming)
        return "Answered: 1"
    owner.questions = hook
    before = set(hub.sessions)
    await owner._dispatch(_guest("G1", "1"))
    await _drain(owner.inbound.guests)
    assert offered == [], "a guest's '1' was offered as an answer (MS-21)"
    assert set(hub.sessions) == before, "a guest message built a session"
    assert owner.inbound.wa.replies[0][1].startswith("Butler: ")


@pytest.mark.asyncio
async def test_end_to_end_through_the_sidecar_stream(tmp_path, monkeypatch,
                                                     fake_argv, capfd):
    hub = _hub(tmp_path, monkeypatch)
    hub.settings.set("whatsapp_enabled", True)
    records = tmp_path / "wa-records"
    owner = wa_mod.WhatsApp(hub.settings, records)
    owner.inbound = wa_mod.Inbound(hub, owner,
                                   queue_folder=tmp_path / "phone")
    guests = owner.inbound.guests = wa_mod.Guests(
        hub, owner, ask=Ask("Not today."), soul=lambda: "", clock=Clock())
    await owner.start()
    try:
        await _until(lambda: owner.state == "running", "running")
        await _push(owner, _guest("E1", "a private guest secret"))
        await _until(lambda: len(_requests(records, "/guest-reply")) == 1,
                     "the guest reply at the sidecar")
        assert _requests(records, "/guest-reply")[0]["body"] == {
            "jid": ALICE, "text": "Butler: Not today."}
        await _push(owner, {"type": "takeover", "jid": ALICE, "ts": 1})
        await _push(owner, _guest("E2", "hello again"))
        await _until(lambda: guests._quiet_until and not guests.tasks
                     and not guests._waiting.get(ALICE), "the second item")
        assert len(_requests(records, "/guest-reply")) == 1
        assert _requests(records, "/reply") == [], "the owner's door was used"
        assert "a private guest secret" not in capfd.readouterr().err
    finally:
        await owner.stop()


# --------------------------------------------------------------------------- #
# The picker (MS-14a): "Butler answers" from the archive's chats
# --------------------------------------------------------------------------- #
def test_the_answer_list_is_picked_from_a_generic_target():
    spec = settings_mod.BY_KEY["whatsapp_answer_chats"]
    assert spec.kind == "list" and spec.choices_from == "whatsapp_chats"
    assert "whatsapp_chats" in butlerd.LOCAL_ASYNC_TARGETS
    assert settings_mod.BY_KEY["whatsapp_answer_all"].default is False


@pytest.mark.asyncio
async def test_the_chat_choices_are_people_and_groups_with_names(
        tmp_path, monkeypatch):
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    target = butlerd.LOCAL_ASYNC_TARGETS["whatsapp_chats"]
    with pytest.raises(butlerd.LocalListUnavailable):
        await target(None)
    wa_archive.build(butlerd.paths_mod.resolve().whatsapp / "whatsapp.db",
                     int(T0))
    found = await target(None)
    by_value = {c["value"]: c["label"] for c in found}
    assert by_value[wa_archive.FAMILY] == "Family Group (group)"
    assert by_value[wa_archive.BRUNO] == "Bruno Lima"
    assert "status@broadcast" not in by_value


def test_the_page_offers_choices_on_a_list_without_naming_a_setting():
    """AC-41: the list builder reads `spec.choices_from` and asks the
    generic models route; the page names no WhatsApp setting or target."""
    src = (REPO / "web" / "settings.js").read_text("utf-8")
    code = "\n".join(line.split("//")[0] for line in src.splitlines())
    start = code.index("function buildList(")
    body = code[start:code.index("\n  }\n", start)]
    assert "spec.choices_from" in body and "modelsFor(" in body
    for named in ("whatsapp", "answer_chats", "whatsapp_chats"):
        assert named not in code, named
