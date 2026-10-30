"""The PAGE half of the library (docs/automations.md phase 2): AC-449 (Run and
Start are the device's own turn), AC-450 (the slash picker inserts and never
sends), AC-452's untrusted text, AC-466 (one right panel) and AC-467 (it
follows the conversation and re-reads on the `library` frame).

GREP GATES, `test_file_explorer_page.py`'s shape and reason: nothing in this
suite executes `web/app.js` or `web/library.js`, so a regression here is
invisible to every daemon-side test. The pixels were checked with
`playwright-cli` on a scratch daemon against `web/mocks/t-library.html`.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
LIB = ROOT / "web" / "library.js"
INDEX = ROOT / "web" / "index.html"


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", js)


def _fn_body(source: str, name: str) -> str:
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{",
                  source)
    assert m, f"no `function {name}(...)` declaration found"
    depth = 0
    for i in range(m.end() - 1, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[m.end() - 1:i + 1]
    raise AssertionError(f"unbalanced braces reading function {name}")


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lib():
    return LIB.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


# -- AC-466: one right panel ------------------------------------------------- #

def test_right_panel_has_ONE_writer_and_repaints_both_panels(app, lib):
    code = _strip_comments(app)
    assert code.count("right_panel:") == 1, (
        "right_panel must be written in exactly one place")
    body = _fn_body(app, "setRight")
    assert "files.rightChanged(" in body and "library.rightChanged(" in body
    assert "saveShortcut({ right_panel: next })" in body
    assert "env.save(" not in _strip_comments(lib)
    writes = re.findall(r"env\.setRight\((.*?), viaKey\)", _strip_comments(lib))
    assert writes == ['deskOpen() ? "" : "library"'], writes


def test_the_store_answer_moves_both_panels(app):
    body = _fn_body(app, "paintShortcuts")
    assert "doc.right_panel" in body
    assert "library.rightChanged(" in body and "library.follow()" in body


def test_a_phone_never_writes_the_right_panel(lib):
    body = _fn_body(lib, "toggle")
    phone, _, _desk = body.partition("env.setRight(")
    assert "env.isPhone()" in phone and "return" in phone


def test_opening_one_sheet_shuts_the_other(app):
    assert "closeOtherSheet: () => library.closeSheet()" in app
    assert "closeOtherSheet: () => files.closeSheet()" in app


def test_no_browser_storage(lib):
    code = _strip_comments(lib)
    for banned in ("localStorage", "sessionStorage", "indexedDB",
                   "document.cookie"):
        assert banned not in code, banned


# -- AC-467: follows the conversation, re-reads on the frame ----------------- #

def test_the_library_follows_the_conversation_on_screen(app, lib):
    assert "library.follow()" in _fn_body(app, "paintChatProject")
    assert "projectId: () => currentProjectId()" in app
    assert re.search(r'case "library":\s*\n(\s*//.*\n)*\s*library\.changed\(m\)',
                     app), "the library frame is not handled"


# -- AC-449: Run and Start are the device's own turn ------------------------- #

def test_run_births_through_the_existing_gate_and_sends_a_typed_message(app):
    body = _fn_body(app, "runInNewChat")
    order = [body.index(s) for s in ("gateNewChat(", "newConversation(",
                                     "sendTyped(")]
    assert order == sorted(order), order
    assert "fetch(" not in body, "Run grew a daemon path of its own"


def test_starting_a_task_does_not_mark_it_done(lib):
    m = re.search(r"start\.onclick = \(\) => (.*?);\n", lib)
    assert m and "runText(t.project, t.text" in m.group(1), m
    assert "done" not in m.group(1)


def test_every_filled_text_comes_from_the_daemon(lib):
    """AC-448 on the page: Run, the preview and the slash insert all ask the
    expand route; nothing here substitutes a placeholder."""
    code = _strip_comments(lib)
    assert '"/expand"' in code
    assert ".replace(" not in code, "library.js rewrites text itself"


# -- AC-450: the slash picker inserts and never sends ----------------------- #

def test_the_slash_picker_sees_enter_before_the_composer_sends(app, lib):
    assert app.index("mountLibrary({") < app.index(
        'composerInput.addEventListener("keydown"'), (
        "the library must be mounted before the composer's Enter listener")
    assert "e.stopImmediatePropagation()" in _strip_comments(lib)


def test_choosing_a_recipe_inserts_and_sends_nothing(app, lib):
    body = _fn_body(lib, "choose")
    assert "env.insertText(" in body and "runInNewChat" not in body
    insert = _fn_body(app, "insertComposerText")
    assert "send(" not in insert and "sendTyped" not in insert


def test_escape_order_with_the_library(app):
    """The library's DIALOGS left this order in lantern-ui Step 6a (AC-706,
    CU-15, AC-709): each closes in its own capture-phase listener and yields
    the key to a stop (`test_library_lantern_page.py`)."""
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m
    block = m.group(1)
    order = [block.index(s) for s in (
        "panelOpen()", "drawerOpen()",
        "files.sheetOpen()", "library.sheetOpen()", "stopTurn()",
        "library.slashOpen()", 'composerInput.value !== ""')]
    assert order == sorted(order), order


# -- untrusted text, and the markup ------------------------------------------ #

def test_nothing_in_the_library_parses_markup(lib):
    code = _strip_comments(lib)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "DOMParser", "createContextualFragment"):
        assert sink not in code, f"library.js uses {sink} on untrusted text"


def test_one_door_to_the_network(lib):
    assert _strip_comments(lib).count("fetch(") == 1


def test_the_markup_is_there(index):
    for needle in ('id="libraryBtn"', 'id="libraryPanel"', 'id="slashPop"',
                   'role="tablist"', 'id="recipeDlg"', 'id="recipeRunDlg"',
                   'aria-controls="slashPop"'):
        assert needle in index, needle


def test_the_library_row_opens_the_panel_on_its_library_tab(app, index):
    """AC-699: the sidebar's Library row is `#libraryBtn`, and it opens the
    one right panel on the Library tab."""
    side = re.search(r'<aside id="sidebar".*?</aside>', index, re.S).group(0)
    assert 'id="libraryBtn"' in side, "the Library row is not in the sidebar"
    assert re.search(r'\$\("libraryBtn"\)\.onclick\s*=.{0,80}openPanelTab\("library"',
                     app, re.S), "the Library row does not open the library tab"


def test_a_voice_run_is_adopted_in_the_chat_on_screen(app):
    """AC-451's page half: the recipe's turn is this device's, begun by the
    daemon in the chat it just opened, so its `final` is adopted there."""
    m = re.search(r'if \(m\.type === "final" && m\.reason === "recipe"(.*?)\{'
                  r'\s*currentTurn = m\.turn;', app, re.S)
    assert m, "the page does not adopt a recipe's turn"
    assert "m.chat === liveChatId" in m.group(1)
    assert "state !== CAPTURE" in m.group(1)


# -- docs/proactive.md PR-17..PR-20: "New reminder" ------------------------- #

def test_new_reminder_saves_through_the_automations_route(lib):
    """The mock's "New reminder" (proactive-p0.html 2): in both sections of
    the Automations tab, one editor, and a save that sends `kind: reminder`
    with a `runAt` or a preset schedule to the ONE automations route -- the
    daemon judges the time; the page parses no schedule."""
    code = _strip_comments(lib)
    # Step 6a (AC-709): the add rows carry an icon, so the word lost its "+".
    assert code.count('addButton("New reminder"') == 2
    save = _fn_body(code, "saveRem")
    assert 'kind: "reminder"' in save
    assert 'api("POST", "/api/automations", body)' in save
    assert "body.runAt" in save and "body.schedule" in save
    assert "Date.parse" not in save and "cron" not in save
    row = _fn_body(code, "automationRow")
    assert 'a.kind === "reminder"' in row and "openRem(a" in row
