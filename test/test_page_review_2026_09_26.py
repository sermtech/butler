"""Four defects a playwright-cli review of the page found on 2026-09-26.

Mostly GREP GATES over `web/`, in the shape of `test_stop_capture_page.py`
and for its reason: nothing in this suite loads or executes `web/app.js`, so a
regression in the page is invisible to every daemon-side test. The one daemon
half (which setting answers in the current brain mode) is a behaviour test on
`Settings.describe()`. The browser evidence for each is in the session report,
driven against a scratch daemon, because a grep proves the wiring and not the
pixels.

1. KEYBOARD (AC-101). The closed settings surface is `opacity: 0` and was
   neither `inert` nor hidden, so Tab walked into an invisible "reset all";
   open, Shift+Tab walked out of it onto the covered page's microphone. The
   phone drawer had the same shape: translated off-screen, still tabbable.
2. STALE LIST. After a typed turn the sidebar row kept "New conversation /
   0 turns" while `GET /api/chats` already served the real title, and a
   project kept its old count after New chat was born in it: nothing re-read
   either at the end of a turn or after a birth.
3. PLACEHOLDER. "Try again" (and copy, and read aloud) rendered under the
   "working on it..." placeholder, which is CSS on an EMPTY pending answer --
   retrying a turn that is still running, with nothing to copy.
   And the header read "speaking" for a whole silent harness run: harness
   mode speaks the "Working on it." status first, `speak_start` is sent once
   per TURN (hub `_begin_speaking`), so SPEAK is the state from that status
   until the answer's last sample -- including the minutes of silence between.
4. WHAT ANSWERS. The header model button printed "Brain <model>" in
   `harness_only`, where the conversation model decides nothing, because the
   page took the FIRST quick setting served. And a focus-shown tooltip kept
   the OLD model id after a pick changed it.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from daemon.settings import BY_KEY, Settings                    # noqa: E402

APP = ROOT / "web" / "app.js"
SETTINGS_JS = ROOT / "web" / "settings.js"
INDEX = ROOT / "web" / "index.html"


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """One `function NAME(...) { ... }` declaration, brace-matched."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert m, f"no `function {name}(...)` declaration found"
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


# -- 1. the settings surface and the drawer leave the Tab order when shut --- #

def test_the_settings_surface_is_inert_before_it_is_ever_opened(index):
    """The page loads with the surface shut, and nothing runs `openPanel`
    before the first Tab -- so the markup itself has to say `inert`."""
    tag = re.search(r"<aside id=\"panel\"[^>]*>", index)
    assert tag, "no <aside id=\"panel\"> in index.html"
    assert re.search(r"\binert\b", tag.group(0)), (
        f"#panel is not inert at load, so Tab reaches its invisible controls: "
        f"{tag.group(0)!r}")


def test_opening_settings_inerts_the_page_behind_and_closing_reverses_it(app):
    """Both halves, in `openPanel`, the one function every open and close
    reaches (the button, the X, the scrim, Escape, `onOpenSettings`)."""
    body = _fn_body(app, "openPanel")
    assert re.search(r'\$\("panel"\)\.inert\s*=\s*!open', body), (
        "openPanel does not make the surface inert when it closes")
    # Every OTHER child of <body>, not `.app` alone: the skip link sits at the
    # root and Shift+Tab reached it in the browser run of this fix. The one
    # exception is the scrim (docs/lantern-ui.md AC-700, AC-706): the sheet no
    # longer covers the screen, so a press on the dimmed page around it must
    # reach the scrim and close the sheet, and the scrim holds nothing
    # focusable.
    assert re.search(r'for \(const el of document\.body\.children\)\s*\{?\s*'
                     r'if \(el !== \$\("panel"\) && el !== \$\("scrim"\)\) '
                     r'el\.inert = open;', body), (
        "openPanel does not make the covered page inert while it is open, so "
        "Shift+Tab walks out onto controls nobody can see")


def test_the_settings_surface_itself_can_never_be_scrolled(index):
    """`overflow: hidden` still makes a scroll container, so the rail's
    `scrollIntoView` scrolled #panel by its header's height and took "back"
    off screen (2026-09-27, measured y=11 -> y=-72). Only `clip` rules that
    out; `.body` must stay the one scroller."""
    rule = re.search(r"#panel\s*\{[^}]*\}", index).group(0)
    assert re.search(r"overflow\s*:\s*clip\s*;", rule), rule
    assert not re.search(r"overflow\s*:\s*(hidden|auto|scroll)", rule), rule


def test_the_settings_surface_says_it_is_modal(index):
    """The page behind it is inert now, so `aria-modal="false"` would be a
    screen reader being told it can reach something it cannot."""
    tag = re.search(r"<aside id=\"panel\"[^>]*>", index).group(0)
    assert 'aria-modal="true"' in tag, tag


def test_a_shut_phone_drawer_is_inert_and_only_on_a_phone(app):
    """Off-canvas by `transform` is still in the Tab order. On a desk the same
    `data-drawer="closed"` is the ordinary state and the list must stay live,
    so the rule is keyed on the drawer's own breakpoint."""
    body = _fn_body(app, "paintDrawerInert")
    assert '".sb-main"' in body and ".inert" in body, body
    assert "DRAWER_Q.matches" in body and "drawerOpen()" in body, body
    for fn in ("openDrawer", "closeDrawer"):
        assert "paintDrawerInert()" in _fn_body(app, fn), (
            f"{fn} does not repaint the drawer's inert state")
    assert re.search(r'DRAWER_Q\s*=\s*matchMedia\("\(max-width: 900px\)"\)',
                     app), "the drawer query is not the stylesheet's 900px"
    assert re.search(r'DRAWER_Q\.addEventListener\("change",\s*paintDrawerInert\)',
                     app), "crossing the breakpoint leaves a stale inert flag"


# -- 2. the list and the project counts are re-read, not remembered -------- #

def test_every_turn_end_rereads_the_list_and_the_project_counts(app):
    """`goListen` is the ONE exit every terminal path lands in (D-1), so it is
    the one place a refresh reaches every way a turn can end."""
    body = _fn_body(app, "goListen")
    assert "loadChats()" in body, "a finished turn leaves the list stale"
    assert "loadShortcuts()" in body, (
        "a finished turn leaves the project tiles' counts and busy marks stale")


def test_a_birth_rereads_the_project_counts(app):
    """`newConversation` is the one place a conversation is born into a
    project (AC-287; `postProject`, the move, is deleted), so the project
    counts are re-read there rather than by each of its callers (D-18)."""
    body = _fn_body(app, "newConversation")
    assert "loadShortcuts()" in body, (
        "a birth leaves its project showing its old count")


# -- 3. no actions under a placeholder, and "speaking" means audible -------- #

def test_no_answer_actions_render_under_the_placeholder(index):
    """The placeholder is `.histitem.pending .answered:empty::after`; the
    actions sit right after `.answered`, so the SAME condition hides them.
    A turn that closed empty ("no reply came back") keeps its retry."""
    assert re.search(
        r"\.histitem\.pending\s+\.answered:empty\s*\+\s*\.acts\s*\{\s*display:\s*none",
        index), "copy / Try again / Read aloud render under 'working on it...'"


def test_the_state_shown_is_speaking_only_while_audio_plays(app):
    """SPEAK is held from `speak_start` to the drain, once per turn, and in
    harness mode that spans the silent agent run after "Working on it.".
    `state` stays SPEAK (the wake suppression depends on it); what is DRAWN
    reads the worklet's own `playing` hint."""
    body = _fn_body(app, "paint")
    assert re.search(r"state === SPEAK\s*&&\s*!ttsPlaying\s*\?\s*THINKING", body), (
        "paint() draws 'speaking' whether or not anything is audible")
    handler = re.search(r'if \(m\.type === "state"\)\s*\{([^}]*)\}', app)
    assert handler and "paint()" in handler.group(1), (
        "a playing/quiet transition does not repaint the state")


# -- 4. the header names what actually answers ------------------------------ #

@pytest.mark.parametrize("mode, key", [
    ("endpoint", "endpoint_model"),
    ("harness", None),
    ("harness_only", None),
])
def test_the_daemon_says_which_setting_answers_in_each_mode(tmp_path, mode, key):
    """The page may not name a setting (AC-41), so which model is answering
    is the daemon's to state -- beside `inactive`, from the same mode. In the
    harness modes NO setting answers (docs/chat-model.md CM-6): each chat's
    own agent and model do, on the chat chip, so the daemon says None."""
    s = Settings(tmp_path / "s.json")
    s.set("brain_mode", mode)
    doc = s.describe()
    assert doc.get("answers") == key, (
        f"in {mode!r} the daemon says {doc.get('answers')!r} answers")
    assert "answers" in doc
    if key is None:
        return
    assert BY_KEY[key].quick, f"{key} is not a quick setting, so no pill shows it"
    served = {d["key"]: d for d in doc["settings"]}
    assert served[key]["inactive"] == "", (
        f"{key} is named as answering in {mode!r} and dimmed as inert there")


def test_the_header_pill_reads_the_answering_setting():
    src = SETTINGS_JS.read_text(encoding="utf-8")
    assert re.search(r"quickRows\.get\(\s*next\.answers\s*\)", src), (
        "the header pill still prints the first quick setting served, which is "
        "the conversation model even where it decides nothing")
    # docs/chat-model.md CM-6: `answers: null` names NO setting.
    assert re.search(r"next\.answers\s*===\s*null\s*\?\s*null", src), (
        "a daemon saying no setting answers must leave the pill naming none")


def test_a_shown_tooltip_follows_its_controls_help(app):
    """`#tip` copied `data-help` once; a pick focused and then written kept
    showing the model it no longer had."""
    assert re.search(r"attributeFilter:\s*\[\s*\"data-help\"\s*\]", app), (
        "nothing re-reads data-help while its tooltip is showing")


def test_a_focus_tooltip_is_for_keyboard_focus_only(app):
    """A pick returns focus to its button after a CLICK, and a tip raised by
    that focus covered the popover still open under the pointer."""
    handler = re.search(
        r'addEventListener\("focusin",\s*\(e\)\s*=>\s*\{(.*?)\n\}\);', app, re.S)
    assert handler, "no focusin tooltip handler found"
    assert ':focus-visible' in handler.group(1), (
        "a mouse-driven focus raises the tooltip over an open popover")


def test_a_changed_document_setting_is_named_not_printed(app):
    """SOUL.md / USER.md are multiline settings; the "settings changed" note
    printed the whole soul into the chat area (2026-09-27). The note must say
    the setting changed, keyed on the schema's kind or a line break -- never
    print the value, and never name a setting (AC-41)."""
    body = _fn_body(app, "settingsChangedLine")
    assert 'spec.kind === "multiline"' in body, body
    assert r'includes("\n")' in body, body
    assert '": changed"' in body, body
