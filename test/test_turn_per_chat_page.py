"""Turn state is PER CONVERSATION on the page, as a grep gate over web/app.js.

The 2026-09-26 e2e audit (`docs/e2e-journeys.md`) measured five defects with
one root: `state`, `currentTurn` and the clock described "the" turn, not the
conversation on screen. Opening another chat mid-turn left the idle one
saying "thinking" with a Stop button (S4c), the running one's reply streamed
into it (S10d), Stop killed the agent in the chat the owner had left (S18), a
reload drew "no reply came back" over a turn still running (S16), and the
busy confirm read a flag cached at the end of the last turn (S10a).

Nothing in this suite executes app.js (`test_stop_capture_page.py` says why),
so the structure is pinned here and the behaviour was proved in a real browser
against a scratch daemon (see `web/AGENTS.md`). The daemon half of Stop is
`test_stop.py`.
"""
import re
from pathlib import Path

import pytest

from test_stop_capture_page import _fn_body

APP = Path(__file__).resolve().parents[1] / "web" / "app.js"


@pytest.fixture(scope="module")
def source():
    return APP.read_text(encoding="utf-8")


def test_the_stop_frame_names_the_conversation_on_screen(source):
    """S18: a stop naming only a turn let a page-global turn id kill a
    background agent. Both sends in `stopTurn` carry `chat`."""
    body = _fn_body(source, "stopTurn")
    sends = re.findall(r"send\(\{[^}]*type:\s*\"stop\"[^}]*\}\)", body)
    assert len(sends) == 2, f"expected the turn and the watched send: {sends}"
    for s in sends:
        assert "chat:" in s, f"a stop frame that names no conversation: {s}"


def test_a_frame_for_another_conversation_is_routed_before_anything_is_drawn(
        source):
    """S10d: the chat check must come BEFORE the watchdog and the switch that
    renders, or a background reply still lands in the transcript on screen."""
    body = _fn_body(source, "onMessage")
    route = body.find("m.chat !== liveChatId")
    assert route != -1, "onMessage no longer routes turn frames by chat"
    assert route < body.find("armWatchdog()"), (
        "a background frame re-arms the on-screen watchdog")
    assert route < body.find("switch (m.type)"), (
        "the chat routing runs after the frame has been drawn")
    assert "trackTurnFrame(m)" in body[:route], (
        "the per-chat registry is not fed before the routing decision")


def test_changing_the_conversation_on_screen_parks_its_turn(source):
    """S4c: the one writer of `liveChatId` is where the screen's turn state
    changes conversation too, so no path that opens a chat can forget it."""
    body = _fn_body(source, "setLiveChat")
    assert "parkScreenTurn()" in body, "setLiveChat does not park the turn"
    assert body.find("parkScreenTurn()") < body.find("liveChatId = next"), (
        "the turn is parked after the id moved, under the new chat's name")


def test_a_drawn_transcript_does_not_close_its_last_turn_unasked(source):
    """S16: `renderMessages` closed the last item unconditionally, which is
    what wrote "no reply came back" over a turn still running."""
    body = _fn_body(source, "renderMessages")
    assert "closeItem()" not in body, (
        "renderMessages closes the last turn without asking whether it runs")
    assert "settleScreenTurn()" in body


def test_a_watched_turn_is_exempt_from_the_pages_own_watchdog(source):
    """A turn this page does not drive has no frames to re-arm the watchdog,
    so the 180 s budget would send a stop for a working agent."""
    body = _fn_body(source, "armWatchdog")
    assert re.search(r"if\s*\(\s*watchedChat\s*\)\s*return;", body), (
        "armWatchdog gives up on a watched turn it is not driving")


def test_the_busy_confirm_asks_the_daemon_at_click_time(source):
    """S10a: the cached `row.busy` was a snapshot from the last turn end."""
    body = _fn_body(source, "gateNewChat")
    assert 'shortcutsFetch("GET")' in body, (
        "gateNewChat decides from the page's cached rows")
    assert "projectRow(" not in body


def test_a_chat_filed_under_a_removed_project_is_still_drawn(source):
    """S9c: grouping never filters. A projectId no row has is an orphan."""
    body = _fn_body(source, "paintChatList")
    assert re.search(r"c\.projectId\s*&&\s*rowIds\.has\(c\.projectId\)",
                     body), "a removed project's chats vanish from the tree"


def test_an_adopted_turn_ends_on_its_own_speak_end(source):
    """Found in the browser, not by the audit: a turn adopted when its chat
    came back on screen is in THINKING (its `speak_start` was routed off
    screen), and `finishSpeak` re-armed only from SPEAK -- so the page sat on
    "thinking" with Stop and a running clock after the daemon said done."""
    body = re.sub(r"//[^\n]*", "", _fn_body(source, "finishSpeak"))
    assert re.search(r"turn\s*===\s*currentTurn", body), (
        "finishSpeak ends only a SPEAKING turn, stranding an adopted one")
