"""Step 6a of docs/lantern-ui.md: the Library tab of the one right panel and
its dialogs (AC-706, AC-709; section 8's served words; CU-15).

The daemon serves every word these surfaces print about time and outcome
(lantern-ui section 8, commit 57a81b3): an automation's `nextWords`, a run's
`statusWord` and `group`, a heartbeat's `awakeWords`, a recipe's `usedBy`
and `placeholders`, a task's `agent`. These gates fail when the page starts
wording any of it itself.

GREP GATES, `test_library_page.py`'s shape and reason: nothing in this suite
executes the page's JavaScript. The pixels were checked on a scratch daemon
against `web/mocks/w1-calm-library.html` (`.scratch-ui/w1l/`).
"""
import re
from pathlib import Path

import pytest

from test_library_page import _fn_body, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
LIB = ROOT / "web" / "library.js"
AUTO = ROOT / "web" / "automations.js"
INDEX = ROOT / "web" / "index.html"

DATE_WORDING = re.compile(r"\bnew Date\b|\bDate\.\w|toLocale|\bIntl\."
                          r"|\.get(Time|Hours|Minutes|Date|Day|FullYear|Month)\(")


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lib():
    return LIB.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def auto():
    return AUTO.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


def _css(index: str) -> str:
    css = index[index.index("<style>"):index.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css: str, selector: str) -> str:
    """Every declaration block whose selector list names `selector`."""
    found = [body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if selector in sel]
    assert found, f"no CSS rule for {selector}"
    return "\n".join(found)


# -- section 8: the schedule and outcome words are the daemon's -------------- #

def test_the_library_and_the_tasks_page_word_no_date(lib, auto):
    """No toLocale*, Intl or Date arithmetic anywhere in library.js or
    automations.js, NO exceptions (Step 6b paid 6a's listed debt): a run's
    and a task's times are served words, and the reminder editor's input
    values are served machine values (`runAtInput`, `remindInput`)."""
    for name, code in (("library.js", lib), ("automations.js", auto)):
        found = DATE_WORDING.search(_strip_comments(code))
        assert not found, f"{name} words a date: {found.group(0)}"


def test_an_automation_card_prints_the_served_next_words(lib):
    row = _strip_comments(_fn_body(lib, "automationRow"))
    assert "a.nextWords" in row
    assert "a.schedule.label" not in row and "nextRunAt" not in row


def test_the_heartbeat_says_when_from_the_daemon(lib):
    card = _strip_comments(_fn_body(lib, "beatRow"))
    assert "hb.awakeWords" in card and "hb.schedule.label" in card
    assert "quiet hours" not in card, "the page words the quiet-hours rule"
    editor = _strip_comments(_fn_body(lib, "drawBeat"))
    assert "hb.nextWords" in editor and "hb.awakeWords" in editor


def test_the_editor_preview_prints_the_served_next_words(auto):
    body = _strip_comments(_fn_body(auto, "preview"))
    assert "got.nextWords" in body and "got.cron" in body
    assert "got.next.slice" not in body, "the page words the next run times"


def test_a_run_mark_prints_the_served_word_with_a_shape_per_group(auto, index):
    """`statusWord` verbatim, and a SHAPE per served `group` (AC-168: never
    colour alone): diamond needs, square failed, dot ok, ring skipped, a
    spinning ring pending. The page holds no status table."""
    code = _strip_comments(auto)
    assert "STATUS = {" not in code, "the page keeps its own status words"
    mark = _strip_comments(_fn_body(auto, "mark"))
    assert "run.statusWord" in mark and "run.group" in mark
    assert "run.status " not in mark and "run.status]" not in mark
    css = _css(index)
    needs = _rules(css, ".mk.needs > i")
    assert "rotate(45deg)" in needs, "needs is not a diamond"
    failed = _rules(css, ".mk.failed > i")
    assert "border-radius: 2px" in failed and "50%" not in failed
    ok = _rules(css, ".mk.ok > i")
    assert "border-radius: 50%" in ok and "background:" in ok
    skipped = _rules(css, ".mk.skipped > i")
    assert "inset" in skipped and "border-radius: 50%" in skipped
    assert "background: transparent" in skipped, "skipped is not a ring"
    pending = _rules(css, ".mk.pending > i")
    assert "animation" in pending


# -- the served placeholders, never parsed ----------------------------------- #

def test_placeholders_are_found_from_the_served_list_never_parsed(lib):
    """The recipe row, the editor's highlight and the values dialog all read
    the recipe's served `placeholders`; library.js holds no brace grammar."""
    code = _strip_comments(lib)
    assert "new RegExp" not in code
    assert not re.search(r"/[^/\n]*\\\{", code), "a regex reads braces"
    body = _strip_comments(_fn_body(lib, "tokens"))
    assert "indexOf(" in body and "placeholders" in body
    assert "createTextNode(" in body, "the text between tokens is not text"
    assert "tokens(" in _strip_comments(_fn_body(lib, "recipeRow"))
    mirror = _strip_comments(_fn_body(lib, "paintMirror"))
    assert "tokens(" in mirror and "served" in mirror
    assert "r.placeholders" in _strip_comments(_fn_body(lib, "openValues"))


def test_a_recipe_says_what_uses_it_from_the_served_field(lib):
    row = _strip_comments(_fn_body(lib, "recipeRow"))
    assert "r.usedBy.length" in row
    assert '"/" + r.name' in row, "the row does not show / and the NAME"
    assert "usedBy.length" in _strip_comments(_fn_body(lib, "paintPlaceholders"))


def test_a_task_names_the_agent_the_daemon_recorded(lib):
    row = _strip_comments(_fn_body(lib, "taskRow"))
    assert '"Added by " + t.agent' in row, "the recorded agent is not printed"
    assert "t.why" in row


def test_every_tab_has_an_empty_state_that_invites_a_first_step(lib):
    empty = _strip_comments(_fn_body(lib, "lempty"))
    assert 'el("h3"' in empty and "primary" in empty
    assert empty.count("return") == 1 and "return box;" in empty, (
        "the empty state returns before it is drawn")
    for draw in ("drawRecipes", "drawAutomations", "drawTasks"):
        assert "lempty(" in _strip_comments(_fn_body(lib, draw)), draw


# -- AC-706: one overlay shape, Escape and outside press ---------------------- #

def test_the_library_dialogs_share_one_overlay_shape(index):
    for dlg in ("recipeDlg", "recipeRunDlg", "autoDlg"):
        m = re.search(r'<dialog class="([^"]*)" id="' + dlg + '"', index)
        assert m and "lbdlg" in m.group(1).split(), f"#{dlg} is not an .lbdlg"
    shape = _rules(_css(index), "dialog.lbdlg")
    for token in ("var(--pop)", "var(--rd)", "var(--shadow)", "var(--dur)",
                  "var(--ease)"):
        assert token in shape, f"dialog.lbdlg does not use {token}"


def test_the_dialogs_close_in_their_own_capture_phase_and_take_escape_whole(
        app, lib, auto):
    """CU-15: no global Escape case. Each dialog closes on Escape through a
    capture-phase listener on ITSELF and on a press outside its box (the
    backdrop). A MODAL takes the key whole -- it never lets the same press
    also stop Butler (AC-111, the irreversible control; dialogs sat above the
    stop in AC-112's order). Only popovers yield to a stop."""
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m and "dialogOpen" not in m.group(1), (
        "a library dialog is still a global Escape case")
    body = _strip_comments(_fn_body(app, "modal"))
    assert "escapeWouldStop" not in body, (
        "a modal dialog lets Escape through to the stop while busy")
    assert "e.stopPropagation();" in body
    assert "}, true);" in body and "e.target === dlg" in body
    assert "e.preventDefault()" in body
    assert "modal: (dlg, close) => modal(dlg, close)" in app
    assert app.count("modal: (dlg, close) => modal(dlg, close)") == 2
    code = _strip_comments(lib)
    assert 'env.modal($("recipeDlg"), ' in code
    assert 'env.modal($("recipeRunDlg"), ' in code
    assert 'env.modal($("autoDlg"), ' in _strip_comments(auto)
