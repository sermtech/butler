"""One turn slot per SOCKET, and why a background conversation's reply never
reached the wire (`docs/parallel-agents.md` phase 6, item 1).

`SatelliteSession.owns(turn)` used to be `turn == self.turn`: one scalar, so
starting a SECOND turn on a session -- even for a totally different
conversation -- silently made the FIRST turn's `owns()` answer False from
that moment on. Nothing raised, nothing logged from the caller's side:
`send_for_turn`, `send_audio`, `_speak` and `_rearm` all bail out quietly on a
turn they no longer "own", which is precisely the mechanism stale-turn
discard (D-6, AC-5) is built on -- and it could not tell "this turn was
abandoned" from "this session has since addressed a DIFFERENT conversation".

This file drives `_begin_turn` directly for two chats on ONE session,
bypassing `_barge_in` on purpose: what is under test here is the OWNERSHIP
MECHANISM itself -- once two turns for different conversations are both live
on one session, does the daemon route each conversation's frames to the wire,
or does the newer one silently kill the older one's traffic -- in isolation
from whatever `_barge_in` decides to end. `docs/parallel-agents.md`'s own
build-status note named this exact gap ("STILL OPEN under phase 6").

**`_barge_in` is PARTIALLY scoped by conversation now** (phase 8, partial):
it ends a session's front-door turn only when that session would actually
SPEAK it (`SatelliteSession.hears`, AC-238), so a turn the device has looked
away from survives a wake word or typed message elsewhere -- see
`test_hub_bargein.py`'s background-conversation cases. It still only ever
acts on the FRONT DOOR (`abandon()`'s own scope, unchanged), so this file's
two-conversations-both-live setup is still reached only by driving
`_begin_turn` directly, or in production by a device that has looked away
from its own front-door turn -- not yet by two conversations a single device
is actively showing at once through the wake-word/typed front door.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub                 # noqa: E402


def frames(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent if isinstance(m, str)]


def replies(s) -> list[dict]:
    return [f for f in frames(s) if f.get("type") == "reply"]


@pytest.mark.asyncio
async def test_a_background_conversations_reply_is_not_dropped(tmp_path):
    """Two conversations, turns in flight on ONE socket: both frames deliver.

    `chat_for(s)` reads `s.showing` when it is set, so setting it directly
    between two `_begin_turn` calls stands in for two conversations without
    needing a `ChatStore` -- what is under test is `owns()`/`send_for_turn`,
    not conversation resolution (that is `test_view_ownership.py`'s file).

    On today's code (`self.turn` a single scalar) this reddens at the FIRST
    assertion: beginning turn B overwrites `s.turn`, so `s.owns(turn_a)`
    answers False even though nothing ever abandoned or superseded turn A --
    it is a live, different conversation's turn.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    s.showing = "chat-a"
    turn_a = hub._begin_turn(s)

    s.showing = "chat-b"
    turn_b = hub._begin_turn(s)

    assert s.owns(turn_a), (
        f"turn {turn_a} (chat-a) is no longer owned once a DIFFERENT "
        f"conversation's turn {turn_b} (chat-b) began on the same socket -- "
        f"a background conversation's reply would be dropped at the wire")
    assert s.owns(turn_b), f"the newer turn {turn_b} must still be owned too"

    s.ws.sent.clear()
    await s.send_for_turn(turn_a, type="reply", text="from A", tier="reflex")
    await s.send_for_turn(turn_b, type="reply", text="from B", tier="reflex")

    got = replies(s)
    texts = {f["text"] for f in got}
    assert texts == {"from A", "from B"}, (
        f"expected both conversations' replies on the wire, got {texts}")


@pytest.mark.asyncio
async def test_send_for_turn_stamps_the_conversation_the_turn_belongs_to(
        tmp_path):
    """Item 3: every frame `send_for_turn` sends carries `chat`, looked up
    from the TURN rather than from any current pointer -- so the page can key
    staleness per conversation instead of per session.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    s.showing = "chat-a"
    turn_a = hub._begin_turn(s)
    s.showing = "chat-b"
    turn_b = hub._begin_turn(s)

    s.ws.sent.clear()
    await s.send_for_turn(turn_a, type="reply", text="from A", tier="reflex")
    await s.send_for_turn(turn_b, type="reply", text="from B", tier="reflex")

    got = {f["text"]: f.get("chat") for f in replies(s)}
    assert got == {"from A": "chat-a", "from B": "chat-b"}, got


@pytest.mark.asyncio
async def test_a_turn_superseded_in_its_OWN_conversation_still_goes_stale(
        tmp_path):
    """D-6/AC-5, scoped to one conversation rather than lost. A correction or
    a retry in the SAME chat must still discard the turn it supersedes --
    widening `owns()` must not also widen what counts as "still mine" within
    one conversation.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    s.showing = "chat-a"
    first = hub._begin_turn(s)
    second = hub._begin_turn(s)          # same chat, supersedes `first`

    assert not s.owns(first), (
        f"turn {first} was superseded by turn {second} in the SAME "
        f"conversation and must have gone stale")
    assert s.owns(second)

    s.ws.sent.clear()
    await s.send_for_turn(first, type="reply", text="stale", tier="reflex")
    assert not replies(s), "a superseded same-chat turn must not reach the wire"


@pytest.mark.asyncio
async def test_abandon_only_clears_the_front_door_turn(tmp_path):
    """Item 5: `abandon()` must not turn into "abandon everything this
    session is tracking". It targets `self.turn` (the front door) exactly as
    before; a DIFFERENT conversation's still-live turn is left alone.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    s.showing = "chat-a"
    turn_a = hub._begin_turn(s)
    s.showing = "chat-b"
    turn_b = hub._begin_turn(s)          # front door is now chat-b's turn

    await s.abandon()

    assert not s.owns(turn_b), "abandon() must still end the front-door turn"
    assert s.owns(turn_a), (
        "abandon() must not sweep a DIFFERENT conversation's live turn -- "
        "that is turning barge-in into \"abandon everything\"")


@pytest.mark.asyncio
async def test_rearm_of_a_background_turn_leaves_the_front_door_alone(
        tmp_path):
    """A background turn reaching its own natural end must not re-arm the
    CURRENT (different) turn's state out from under it -- the exact shape of
    the `_rearm` race its own docstring records, generalised to a second
    conversation instead of a stale/cancelled one.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    s.showing = "chat-a"
    turn_a = hub._begin_turn(s)
    s.showing = "chat-b"
    turn_b = hub._begin_turn(s)
    assert s.turn == turn_b

    s._rearm(turn_a)                     # A's own turn finishing

    assert not s.owns(turn_a), "turn A should have ended"
    assert s.turn == turn_b, (
        f"turn A finishing re-armed the FRONT DOOR ({s.turn}) instead of "
        f"just its own conversation -- the microphone would be taken away "
        f"from the turn the owner is actually in (D-1)")
    assert s.owns(turn_b), "turn B must still be owned after A's own rearm"
