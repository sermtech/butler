"""Step 6b of docs/lantern-ui.md: the Tasks and automations page (AC-468,
AC-706, AC-709; CU-15, CU-16; section 8's served words).

The page now sits in the CONVERSATION COLUMN with the sidebar live (CU-16),
its keys are its own (one listener on the page element, no new global Escape
case, CU-15), its run-history drawer is the library's one overlay shape
(AC-706), and every time and duration it prints is a word the DAEMON served
(`whenWords`, `tookWords`, `lastOccurrenceWords`, `addedWords`, `doneWords`;
the routes are pinned in `test_automations_served.py`, D-18).

GREP GATES, `test_library_page.py`'s shape and reason: nothing in this suite
executes the page's JavaScript. The pixels were checked on a scratch daemon
against `web/mocks/w1-calm-library.html` view=tasks.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from test_library_page import _fn_body, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
LIB = ROOT / "web" / "library.js"
AUTO = ROOT / "web" / "automations.js"
INDEX = ROOT / "web" / "index.html"

#: The global Escape handler's cases (web/AGENTS.md "Escape order", AC-112):
#: the page, settings, drawer, files sheet, library sheet, STOP, slash
#: picker, clear the draft. Step 6b adds none.
ESCAPE_CASES = 8


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
    """Every declaration block whose selector list names `selector` exactly
    (one comma-separated selector equal to it)."""
    found = [body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if selector in [s.strip() for s in sel.split(",")]]
    assert found, f"no CSS rule for {selector}"
    return "\n".join(found)


class _Parent(HTMLParser):
    """The open-element stack at the tag carrying `id="<want>"`."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "source", "track", "wbr", "path", "circle",
            "rect"}

    def __init__(self, want):
        super().__init__()
        self.want, self.stack, self.found = want, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id") == self.want and self.found is None:
            self.found = list(self.stack)
        if tag not in self.VOID:
            self.stack.append((tag, a.get("id", ""), a.get("class", "")))

    def handle_startendtag(self, tag, attrs):
        if dict(attrs).get("id") == self.want and self.found is None:
            self.found = list(self.stack)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def _parents(index: str, want: str):
    body = index[index.index("<body"):]
    p = _Parent(want)
    p.feed(body)
    assert p.found is not None, f"no #{want} in the markup"
    return p.found


# -- CU-16: in the conversation column, the sidebar live ---------------------- #

def test_the_page_sits_in_the_conversation_column(index):
    """A child of `.app` (beside `.col`, never a body-level surface over
    everything), in `.col`'s grid cell, which it hides while open; fixed to
    the whole screen only on a phone."""
    tag, _id, cls = _parents(index, "tasksPage")[-1]
    assert (tag, cls) == ("div", "app"), (
        f"#tasksPage is inside <{tag} class={cls!r}>, not .app")
    css = _css(index)
    page = _rules(css, "#tasksPage")
    assert "grid-column: 2" in page and "grid-row: 2" in page
    assert "z-index: 1" in page, "the page must sit under the header (z 2)"
    desk = css[:css.index("@media (max-width: 900px) {\n    #tasksPage {")]
    assert "position: fixed" not in _rules(desk, "#tasksPage"), (
        "the page covers the sidebar on a desk")
    assert re.search(r"\.app:has\(> #tasksPage:not\(\[hidden\]\)\) > \.col"
                     r" \{ visibility: hidden; \}", css), (
        "the conversation column is not hidden under the page")


def test_a_chat_opened_or_started_beside_the_page_takes_the_column(app):
    """With the sidebar live, a chat row, New chat or a library Run must not
    land in a column the page covers."""
    for fn in ("openChat", "newConversation"):
        assert "automations.closePage(false)" in _strip_comments(
            _fn_body(app, fn)), f"{fn} leaves the page over the chat"


# -- CU-15: the page's keys are its own; no new global Escape case ------------- #

def test_the_page_keys_are_scoped_to_the_page(auto):
    """J / K move, X selects, R runs: one keydown listener on the page
    element, never the document; never while typing or with a modifier;
    Escape is not one of them."""
    code = _strip_comments(auto)
    assert "document.addEventListener" not in code
    assert "window.addEventListener" not in code
    m = re.search(r'\$\("tasksPage"\)\.addEventListener\("keydown", \(e\) => '
                  r'\{(.*?)\n  \}\);', code, re.S)
    assert m, "the page has no keydown listener of its own"
    keys = m.group(1)
    for k in ('"j"', '"k"', '"x"', '"r"'):
        assert k in keys, f"the page does not handle {k}"
    assert "Escape" not in keys, "the page takes an Escape case"
    assert "e.ctrlKey || e.metaKey || e.altKey" in keys
    assert 'closest("input, select, textarea, [contenteditable]")' in keys
    assert "toggleChecked(" in keys and "runNow(" in keys


def test_no_new_global_escape_case(app):
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m
    block = _strip_comments(m.group(1))
    assert len(re.findall(r"\bif \(", block)) == ESCAPE_CASES, (
        "the global Escape order gained or lost a case (AC-112)")
    assert block.index("automations.isOpen()") < block.index("panelOpen()")


# -- section 8: every time on the page is the daemon's word ------------------- #

def test_a_run_prints_the_served_words_verbatim(auto):
    row = _strip_comments(_fn_body(auto, "runRow"))
    for field in ("r.whenWords", "r.tookWords", "r.lastOccurrenceWords"):
        assert field in row, f"the run row does not print {field}"
    assert "mark(r)" in row, "the run's served word and shape are not drawn"
    for raw in ("scheduledFor", "startedAt", "endedAt", "lastOccurrenceAt"):
        assert raw not in row, f"the run row reads the raw {raw}"


def test_a_task_prints_the_served_words_verbatim(auto, lib):
    for code in (_fn_body(auto, "taskRow"), _fn_body(lib, "taskRow")):
        row = _strip_comments(code)
        assert "t.addedWords" in row and "t.doneWords" in row
        assert "createdAt" not in row and "t.doneAt" not in row


def test_the_reminder_editor_starts_from_served_values(lib):
    body = _strip_comments(_fn_body(lib, "openRem"))
    assert "a.runAtInput" in body and "remindInput" in body


def test_the_result_filter_reads_the_served_group(auto, index):
    body = _strip_comments(_fn_body(auto, "visibleAutos"))
    assert "a.last.group" in body and ".status" not in body
    m = re.search(r'<select id="tpResult".*?</select>', index, re.S)
    assert m
    assert set(re.findall(r'value="([a-z]*)"', m.group(0))) == {
        "", "ok", "needs", "failed", "skipped", "pending", "none"}


def test_added_by_reads_the_served_agent(auto, index):
    body = _strip_comments(_fn_body(auto, "visibleTasks"))
    assert "!!t.agent" in body
    m = re.search(r'id="tpTaskSrc">.*?</span>', index, re.S)
    assert m
    assert re.findall(r'data-v="([a-z]*)"', m.group(0)) == ["", "owner", "agent"]


# -- AC-706: the drawer is the one overlay shape ------------------------------ #

def test_the_run_history_drawer_is_the_one_overlay_shape(index):
    tag, _id, cls = _parents(index, "tpHist")[-1]
    assert cls == "pgbody", "the drawer is not beside the list"
    drawer = _rules(_css(index), "#tasksPage .hist")
    for token in ("var(--pop)", "var(--rd)", "var(--shadow)", "var(--dur)",
                  "var(--ease)", "var(--hairline)"):
        assert token in drawer, f"the drawer does not use {token}"


def test_the_bulk_bar_keeps_its_four_actions(index, auto):
    m = re.search(r'<div class="bulk" id="tpBulk".*?</div>', index, re.S)
    assert m
    for i in ("tpBulkOn", "tpBulkOff", "tpBulkRun", "tpBulkClear"):
        assert f'id="{i}"' in m.group(0)
        assert f'$("{i}").onclick' in _strip_comments(auto)
