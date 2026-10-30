"""Stop and Escape reach a CAPTURE, as a grep gate over web/app.js.

Same shape and same reason as `test_visualiser_contract.py`: `web/app.js` is
a plain module with no build step and nothing in this suite loads or executes
it, so a regression here is invisible to every daemon-side test.

The owner set `turn_mode` to `endword`, spoke Portuguese, and the turn never
ended -- and there was no way to cancel it. Cause 1 was that `stopTurn()` and
every one of its call sites (the send/stop button, Escape, and the popovers
that defer to Escape) excluded CAPTURE by name, so pressing either did
nothing while a capture was in flight. The daemon side of this needed NO
fix: `_stop`/`abandon()` already tear a capture down correctly (D-13'd
independently via `test_stop.py::test_the_daemon_accepts_stop_and_keeps_
answering`, which sends `stop` mid-capture and asserts no error and no
final). This file pins the page half only.

Cause 2 -- the capture watchdog giving up locally without telling the
daemon -- is pinned here too: `armWatchdog`'s timeout used to call
`goListen()` alone, so the daemon went on holding the capture and the
recognizer for up to `END_WORD_MAX_MS` after the page had already announced
it was giving up (MEASURED against the owner's own log: an AsrBusy refusal
ten seconds before the page's own giving-up message). The fix reuses
`stopTurn()` -- the same terminal path the button and Escape reach -- rather
than a second one (D-18).
"""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "web" / "app.js"


@pytest.fixture(scope="module")
def source():
    return APP.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """The text of one `function NAME() { ... }` declaration, brace-matched.

    A regex slice rather than a parser: good enough for a top-level function
    declaration in a file with no build step, and it is what
    `test_visualiser_contract.py` already does for the same reason.
    """
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert m, f"no `function {name}(...)` declaration found in app.js"
    start = m.end() - 1
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unbalanced braces reading function {name}")


def test_stop_turn_is_reachable_during_a_capture(source):
    """`stopTurn()`'s own guard must not exclude CAPTURE (cause 1's root)."""
    body = _fn_body(source, "stopTurn")
    guard = re.search(r"if\s*\(([^)]*)\)\s*return;", body)
    assert guard, "stopTurn() has no early-return guard to check"
    cond = guard.group(1)
    assert "CAPTURE" in cond, (
        f"stopTurn()'s guard does not mention CAPTURE, so a stuck capture "
        f"cannot be stopped from this function at all: {cond!r}")
    # And it must not have simply dropped the other two: a fix that swapped
    # the guard instead of widening it would break AC-111 to fix this.
    assert "THINKING" in cond and "SPEAK" in cond, (
        f"stopTurn()'s guard lost THINKING or SPEAK while gaining CAPTURE: "
        f"{cond!r}")


def test_the_escape_handler_stops_a_stuck_capture(source):
    """The global Escape handler's stop case must include CAPTURE.

    This is the control the owner actually reached for. Matched narrowly --
    the line that calls `stopTurn()` inside the `Escape` case, not any
    `state === CAPTURE` elsewhere in the file (there are several, for
    unrelated reasons like the dictate button and the mic pill's own label).
    """
    m = re.search(r"else if \(([^)]*stopTurn\(\)[^)]*)\);", source)
    # The condition and the call are two halves split by ` stopTurn();` in
    # the source (`else if (COND) stopTurn();`); the pattern above over-
    # matches into the call, so re-derive the condition on its own line.
    line = re.search(r"else if \(([^)]*)\) stopTurn\(\);", source)
    assert line, "no `else if (...) stopTurn();` line found (the Escape case)"
    assert "CAPTURE" in line.group(1), (
        f"the Escape handler's stop case does not mention CAPTURE: "
        f"{line.group(1)!r}")


def test_the_composer_send_button_offers_stop_during_a_capture(source):
    """`paintSend()`'s `busy` flag decides whether the send button is even
    drawn as Stop -- CAPTURE missing here means the button stayed disabled
    even after `stopTurn()` itself learned to accept the state, which is
    the AC-7b shape of the same defect (a control that never appears)."""
    body = _fn_body(source, "paintSend")
    m = re.search(r"const busy = ([^;]*);", body)
    assert m, "paintSend() has no `const busy = ...` line"
    assert "CAPTURE" in m.group(1), (
        f"paintSend()'s busy flag does not include CAPTURE, so the send "
        f"button never draws as Stop during a capture: {m.group(1)!r}")

    # And the submit handler's own "nothing to send" branch has to agree,
    # or the button LOOKS like Stop and does nothing when pressed.
    submit = re.search(
        r"if \(state === THINKING \|\| state === SPEAK[^)]*\) stopTurn\(\);",
        source)
    assert submit, "the composer's own stop-on-submit line was not found"
    assert "CAPTURE" in submit.group(0), (
        f"the composer submit handler's stop branch does not include "
        f"CAPTURE: {submit.group(0)!r}")


def test_popovers_let_escape_stop_a_stuck_capture(source):
    """`escapeWouldStop()` decides whether a popover (attach menu, look,
    model, header-more, project) swallows Escape for itself or lets it fall
    through to the global stop -- CAPTURE has to be in it or Escape closes
    the popover and leaves the capture running underneath."""
    body = _fn_body(source, "escapeWouldStop")
    assert "CAPTURE" in body, (
        f"escapeWouldStop() does not mention CAPTURE: {body!r}")


def test_the_watchdog_tells_the_daemon_when_it_gives_up(source):
    """Cause 2: the timeout callback must reach the daemon through
    `stopTurn()`, not merely re-arm the page with `goListen()` -- the daemon
    would otherwise go on holding the capture and the recognizer for up to
    `END_WORD_MAX_MS` after the page has already told the owner it gave up.
    """
    body = _fn_body(source, "armWatchdog")
    timeout = re.search(r"watchdog = setTimeout\(\(\) => \{(.*)\}, budget\);",
                        body, re.S)
    assert timeout, "armWatchdog()'s setTimeout callback was not found"
    callback = timeout.group(1)
    assert "stopTurn()" in callback, (
        "the capture/thinking/speak watchdog no longer calls stopTurn(), so "
        "it re-arms the PAGE alone and never tells the daemon to drop the "
        "turn (D-1, cause 2)")


def test_the_capture_budget_is_the_daemons_live_ceiling_not_a_flat_number(
        source):
    """Cause 3: `armWatchdog` must read a LIVE, daemon-reported ceiling for
    CAPTURE rather than the flat `WATCHDOG_MS.capture` constant every other
    state still uses."""
    body = _fn_body(source, "armWatchdog")
    m = re.search(r"const budget = ([^;]*);", body)
    assert m, "armWatchdog() has no `const budget = ...` line"
    assert "captureCeilingMs" in m.group(1), (
        f"the CAPTURE budget is not read from captureCeilingMs: "
        f"{m.group(1)!r}")


def test_apply_turn_mode_learns_the_ceiling_from_the_daemon(source):
    """`applyTurnMode` is the one function all three frames that carry
    `turn_mode` (`ready`, `turn_mode`, `settings`) already funnel through;
    the live ceiling has to ride the same path rather than a fourth
    mechanism (D-18)."""
    body = _fn_body(source, "applyTurnMode")
    assert "captureCeilingMs" in body, (
        "applyTurnMode() does not write captureCeilingMs, so the daemon's "
        "reported ceiling never reaches the watchdog")
    for frame_field in ("m.capture_ceiling_ms",):
        assert source.count(frame_field) >= 3, (
            f"{frame_field} is read fewer than 3 times (ready, turn_mode, "
            f"settings) -- one of the three frames may have been missed")
