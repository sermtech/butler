"""Guest review fixes, wave 2 (docs/messaging.md 6.5, RV-21..RV-23).

AC-607/AC-608 (RV-21, F-4): a guest's standing chat holds a STRANGER's words,
recorded as Butler's notes. They never reach an owner turn with tools: the
memory index never holds them (and an index built before this drops them at
its next open), a turn in that chat is never seeded with them, and the page's
composer is inert there. AC-609 (RV-22, F-10): `phone_chat: current` never
picks a guest chat. AC-610 (RV-23, F-11), daemon half: a reply still being
made when the owner takes over is not sent (the sidecar half, per-message
takeover labelling, is `inbound.test.mjs` / `delivery.test.mjs`).

Every guest line below carries GUEST-WORD words the owner's lines never use,
so "the guest's text reached it" is a substring test with no false match.
Nothing here touches WhatsApp, the owner's records or a live Butler.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from conftest import StubResolver                              # noqa: E402
from test_whatsapp_guests import (ALICE, Ask, Clock, GuestWa,  # noqa: E402
                                  _drain, _guest, _guest_chats, _hub)
from daemon import butlerd                                     # noqa: E402
from daemon import memory_index as index_mod                  # noqa: E402
from daemon import whatsapp as wa_mod                          # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain         # noqa: E402
from daemon.adapters.harness_fake import FakeHarness           # noqa: E402
from daemon.chats import GUEST_ORIGIN, ChatStore               # noqa: E402
from daemon.memory import MemoryStore                          # noqa: E402
from daemon.ports import TurnContext                           # noqa: E402
from daemon.settings import Settings                           # noqa: E402

GUEST_TEXT = "zanzibar pineapple voucher"
OWNER_TEXT = "what is on my calendar"
GUEST = {"kind": GUEST_ORIGIN, "jid": ALICE, "title": "WhatsApp: Alice"}
TURN_BUDGET_S = 15.0


def _chat_file(root: Path, chat_id: str, lines, origin=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    record = {"id": chat_id, "createdAt": now, "updatedAt": now,
              "harness": "pi", "sessionRef": None, "project": "",
              "messages": [{"id": f"m{i}", "role": r, "content": c,
                            "timestamp": now}
                           for i, (r, c) in enumerate(lines)]}
    if origin is not None:
        record["origin"] = origin
    path = root / f"{chat_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _store(tmp_path) -> MemoryStore:
    index = index_mod.MemoryIndex(tmp_path / "index" / "memory.sqlite",
                                  tmp_path / "memory", tmp_path / "chats")
    index.open()
    return MemoryStore(tmp_path / "memory", index=index)


# --------------------------------------------------------------------------- #
# AC-607: memory_search never returns a guest's words
# --------------------------------------------------------------------------- #
def test_a_guest_chat_is_never_indexed_but_an_owner_chat_beside_it_is(
        tmp_path):
    chats = tmp_path / "chats"
    _chat_file(chats, "chat-1-aaaaaa",
               [("assistant", f"Alice wrote: {GUEST_TEXT}")], origin=GUEST)
    _chat_file(chats, "chat-2-bbbbbb",
               [("user", f"{OWNER_TEXT} pineapple")])
    store = _store(tmp_path)
    hits = store.search("pineapple", 10)
    assert [h["id"] for h in hits] == ["chat-2-bbbbbb"], (
        f"memory_search returned a guest's chat: {hits!r}")
    assert store.search("zanzibar voucher", 10) == []


def test_an_index_built_before_the_fix_drops_old_guest_rows_at_open(
        tmp_path):
    """An index that ALREADY holds guest text (schema 2, indexed by the old
    code) loses it at the next open, even though the file never changed --
    the stat sync alone would have kept the old row forever."""
    chats = tmp_path / "chats"
    path = _chat_file(chats, "chat-1-aaaaaa",
                      [("assistant", f"Alice wrote: {GUEST_TEXT}")],
                      origin=GUEST)
    store = _store(tmp_path)
    store.index_db.close()
    db = tmp_path / "index" / "memory.sqlite"
    con = sqlite3.connect(db)
    st = path.stat()
    con.execute("DELETE FROM docs")
    con.execute("DELETE FROM fts")
    cur = con.execute(
        "INSERT INTO docs (path, kind, id, mtime, size, title, date, project,"
        " status, private, supersedes, text) VALUES (?, 'chat', ?, ?, ?,"
        " 'WhatsApp: Alice', '', '', '', 0, '', '')",
        (str(path), "chat-1-aaaaaa", st.st_mtime_ns, st.st_size))
    con.execute("INSERT INTO fts (rowid, title, body) VALUES (?, ?, ?)",
                (cur.lastrowid, "WhatsApp: Alice",
                 f"assistant: Alice wrote: {GUEST_TEXT}"))
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    again = index_mod.MemoryIndex(db, tmp_path / "memory", chats)
    assert again.open() is not None, "the old index was reused as it was"
    assert again.search("zanzibar voucher", 10) == []
    again.close()


# --------------------------------------------------------------------------- #
# AC-608: no guest line is ever seeded into an owner turn
# --------------------------------------------------------------------------- #
def _harness_brain(tmp_path, server):
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("harness_cwd", str(tmp_path))
    chats = ChatStore(tmp_path / "chats")
    child = FakeHarness()
    brain = HarnessBrain(
        resolver=StubResolver(server.base_url), harness_for=lambda _chat=None: child,
        spec_for=butlerd.harness_spec_resolver(prefs, chats), chats=chats,
        cap_for=lambda: prefs["harness_agents_max"])
    return brain, child, chats


async def _turn(brain, text):
    ctx = TurnContext(turn_id=1, mode="secretary", history=(), overheard="")

    async def collect():
        return [i async for i in brain.turn(text, ctx)]
    return await asyncio.wait_for(collect(), TURN_BUDGET_S)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,seeded", [(GUEST, False), (None, True)])
async def test_an_owner_turn_in_a_guest_chat_is_not_seeded_with_its_lines(
        tmp_path, fake_openai, origin, seeded):
    """The CALLER (D-18): the prompt that reaches the child. The witness row
    is the same record without the guest mark, which IS seeded -- so the
    guest row's silence is the rule, not a seed that never fires."""
    brain, child, chats = _harness_brain(tmp_path, fake_openai)
    chat = chats.rotate(None, focus=True, origin=origin)["id"]
    chats.append(chat, "assistant", f"Alice wrote: {GUEST_TEXT}")
    await _turn(brain, OWNER_TEXT)
    assert len(child.prompts) == 1, child.prompts
    assert (GUEST_TEXT in child.prompts[0]) is seeded, child.prompts[0]


# --------------------------------------------------------------------------- #
# AC-607: the page's composer is inert in a guest chat
# --------------------------------------------------------------------------- #
APP = (REPO / "web" / "app.js").read_text(encoding="utf-8")


def _body(name: str) -> str:
    m = re.search(r"\nfunction " + name + r"\(doc\) \{\n(.*?)\n\}\n", APP,
                  re.S)
    assert m, f"{name} is gone from app.js"
    return m.group(1)


def test_the_page_locks_the_composer_in_a_guest_chat_with_the_one_line():
    assert ('const GUEST_NOTE = "Butler answers here as a guest assistant; '
            'reply from your phone.";') in APP
    lock = _body("paintGuestLock")
    assert 'doc.origin.kind === GUEST_ORIGIN_KIND' in lock
    assert "composerForm.inert = guestChat" in lock
    assert "composerInput.disabled = guestChat" in lock
    assert "guestNote.textContent = " in lock and "innerHTML" not in lock
    assert f'GUEST_ORIGIN_KIND = "{GUEST_ORIGIN}"' in APP
    # Every path that puts a conversation on screen paints it: the reload
    # and `opened` path, a click, and the rotation after a delete.
    load = APP[APP.index("async function loadSession(summary)"):]
    load = load[:load.index("\n}\n")]
    assert "paintGuestLock(doc);" in load
    opened = APP[APP.index("async function openChat("):]
    opened = opened[:opened.index("\n}\n")]
    assert "paintGuestLock(doc);" in opened
    assert "paintGuestLock(null);" in APP
    # And the one door a typed turn leaves by refuses while locked.
    send = APP[APP.index("function sendTyped(text) {"):]
    send = send[:send.index("\n}\n")]
    assert "if (guestChat)" in send


@pytest.mark.asyncio
async def test_every_conversation_shape_says_it_is_a_guests(tmp_path,
                                                            monkeypatch):
    """What the page reads: `origin` on the transcript of the chat on
    screen, from the held one and from a past one alike."""
    hub = _hub(tmp_path, monkeypatch)
    guests = wa_mod.Guests(hub, GuestWa(), ask=Ask(), soul=lambda: "",
                           clock=Clock())
    await guests.message(_guest("G1", GUEST_TEXT))
    await _drain(guests)
    chat = (await _guest_chats(hub))[0]["id"]
    past = hub.brain.session_chat(chat)
    assert past["origin"]["kind"] == GUEST_ORIGIN
    opened = hub.brain.session_open(chat)
    assert opened["origin"]["kind"] == GUEST_ORIGIN
    assert hub.brain.session_transcript()["origin"]["kind"] == GUEST_ORIGIN


# --------------------------------------------------------------------------- #
# AC-609: `phone_chat: current` never targets a guest chat
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["last_owner_chat", "held"])
async def test_current_mode_never_lands_in_a_guest_chat(tmp_path, monkeypatch,
                                                        how):
    hub = _hub(tmp_path, monkeypatch)
    hub.settings.set("phone_chat", "current")
    inbound = wa_mod.Inbound(hub, GuestWa(), queue_folder=tmp_path / "phone")
    inbound.guests = wa_mod.Guests(hub, inbound.wa, ask=Ask(),
                                   soul=lambda: "", clock=Clock())
    await inbound.guests.message(_guest("G1", GUEST_TEXT))
    await _drain(inbound.guests)
    guest_chat = (await _guest_chats(hub))[0]["id"]
    if how == "last_owner_chat":
        # The owner typed into the guest chat on the page.
        hub.last_owner_chat = guest_chat
    else:
        # The owner opened it; no owner turn since boot.
        hub.last_owner_chat = None
        assert hub.brain.session_open(guest_chat) is not None
        assert hub.current_chat_id() == guest_chat
    target = await inbound._target()
    assert target and target != guest_chat
    record = hub.brain.session_chat(target, 1)
    assert not wa_mod.is_guest_chat(record)


# --------------------------------------------------------------------------- #
# AC-610: a reply being made when the owner takes over is not sent
# --------------------------------------------------------------------------- #
class SlowAsk(Ask):
    """Holds the answer until the test lets it go."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, system, history, text):
        self.started.set()
        await self.release.wait()
        return await super().__call__(system, history, text)


@pytest.mark.asyncio
async def test_a_reply_in_flight_when_the_owner_takes_over_is_not_sent(
        tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    wa, ask = GuestWa(), SlowAsk()
    guests = wa_mod.Guests(hub, wa, ask=ask, soul=lambda: "", clock=Clock())
    await guests.message(_guest("G1", GUEST_TEXT))
    await asyncio.wait_for(ask.started.wait(), TURN_BUDGET_S)
    guests.takeover({"type": "takeover", "jid": ALICE, "id": "H1", "ts": 1})
    ask.release.set()
    await _drain(guests)
    assert len(ask.calls) == 1, "the model was never asked"
    assert wa.replies == [], "Butler answered over the human"
