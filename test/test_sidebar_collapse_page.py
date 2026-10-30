"""The strip is deleted; the header carries the list toggle, New chat and
Settings at every width (docs/sidebar-collapse.md, AC-293..AC-299).

Mostly GREP GATES over `web/`, in the shape of `test_stop_capture_page.py`
and `test_turn_per_chat_page.py` and for the same reason: nothing in this
suite loads or executes `web/app.js`, so a regression here is invisible to
every daemon-side test. The pixels -- AC-294..AC-299 as drawn -- were proved
against a scratch daemon with `playwright-cli`; this file pins the structure
that browser run depended on.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """The text of one `function NAME() { ... }` declaration, brace-matched.

    Same helper as `test_stop_capture_page.py`'s -- a regex slice rather than
    a parser, good enough for a top-level function declaration in a file
    with no build step.
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


# -- AC-293: the strip is gone, at the source level ------------------------ #

def test_the_strip_and_its_markup_are_deleted(index):
    for token in ("id=\"projRail\"", "id=\"railTiles\"", "class=\"railfoot\"",
                  "id=\"sbCollapse\""):
        assert token not in index, f"the strip's own markup survived: {token}"


def test_the_strips_css_is_deleted(index):
    """Comments are allowed to keep saying what was removed and why (both
    HTML and CSS comments mention the old names in prose); what must be gone
    is the variable and the rule sets actually USED to draw the strip, so
    both comment forms are stripped before looking."""
    code = re.sub(r"<!--.*?-->", "", index, flags=re.S)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    assert re.search(r"--projrail-w\s*:", code) is None, (
        "the strip's own width variable is still declared")
    assert re.search(r"var\(--projrail-w\)", code) is None, (
        "something still reads the strip's width variable")
    # A RULE, not a mention: the selector immediately followed by `{` (or a
    # comma joining it to another selector).
    for selector in ("#projRail", "#railTiles", "\\.railfoot", "\\.tile"):
        assert re.search(selector + r"\s*[{,\[]", code) is None, (
            f"a live CSS rule for the strip survived: {selector}")


def test_the_strips_js_is_deleted(app):
    for name in ("revealProject", "paintProjRail", "projectHue", "tintFor",
                 "projectShort"):
        assert re.search(r"function\s+" + name + r"\s*\(", app) is None, (
            f"dead code from the deleted strip survived: {name}")
    assert "railTiles" not in app and "projRail" not in app


def test_a_collapsed_sidebar_is_zero_width(index):
    """AC-293: nothing is left behind at a fixed strip width."""
    m = re.search(r'\[data-sidebar="collapsed"\]\s*#sidebar\s*\{([^}]*)\}',
                  index)
    assert m, "no collapsed-sidebar width rule"
    assert re.search(r"width:\s*0\s*;", m.group(1)), (
        f"collapsed sidebar is not 0px: {m.group(1)!r}")


# -- AC-294 as reversed by lantern-ui CU-9: the panel icon in the header, ---- #
# New chat and Settings in the sidebar (one press behind the icon when shut) #

def test_the_panel_icon_is_in_the_header_and_new_chat_and_settings_in_the_sidebar(index):
    header = re.search(r"<header>.*?</header>", index, re.S)
    assert header, "no <header> found"
    assert 'id="burger"' in header.group(0), "#burger is not inside <header>"
    side = re.search(r'<aside id="sidebar".*?</aside>', index, re.S)
    assert side, "no #sidebar"
    top = re.search(r'<div class="side-top">.*?</div>\s*\n\s*<div class="sb-scroll">',
                    side.group(0), re.S)
    assert top and 'id="newChat"' in top.group(0), "New chat is not a sidebar row"
    foot = re.search(r'<div class="acct-row">.*', side.group(0), re.S)
    assert foot and 'id="settingsBtn"' in foot.group(0), (
        "Settings is not the gear on the account row")


# -- AC-295: one handler, so the two presses can never disagree ------------- #

def test_ctrl_b_and_the_burger_press_reach_the_same_function(app):
    body = _fn_body(app, "toggleList")
    assert "DRAWER_Q.matches" in body
    assert "toggleSidebar()" in body
    assert re.search(r'\$\("burger"\)\.onclick\s*=\s*toggleList\s*;', app), (
        "the burger button's own handler is not toggleList")
    # The Ctrl+B keydown branch calls the SAME function the button does.
    m = re.search(r'e\.key === "b".{0,400}?toggleList\(\);', app, re.S)
    assert m, "Ctrl+B does not call toggleList()"


# -- AC-296: the busy dot, excluding the conversation on screen ------------- #

def test_the_busy_rollup_excludes_the_chat_on_screen(app):
    body = _fn_body(app, "burgerBusyStatus")
    assert "c.id === liveChatId" in body, (
        "the roll-up does not exclude the conversation already on screen")
    assert "listOnScreen()" in body, (
        "the roll-up does not check whether the list is already showing it")


def test_the_dot_is_never_the_only_channel(app):
    """AC-174: a status change must move the words too, not only a class."""
    body = _fn_body(app, "paintBurger")
    assert 'setAttribute("aria-label"' in body
    assert 'setAttribute("data-help"' in body
    assert '$("burgerDot").hidden' in body


def test_every_place_the_facts_change_repaints_the_burger(app):
    for fn in ("setSidebar", "openDrawer", "closeDrawer", "setLiveChat",
               "paintChatList"):
        assert "paintBurger()" in _fn_body(app, fn), (
            f"{fn} changes a fact paintBurger depends on but never repaints it")


# -- AC-299: collapsing never strands focus inside the hidden list ---------- #

def test_collapsing_while_focus_is_inside_the_list_moves_it_to_the_toggle(app):
    body = _fn_body(app, "toggleSidebar")
    assert re.search(r"\.sb-main.*\.contains\(document\.activeElement\)", body), (
        "toggleSidebar does not check whether focus is inside the list")
    assert '$("burger").focus()' in body, (
        "toggleSidebar does not move focus to the toggle on collapse")


# -- New chat must still reach its own label after the move ----------------- #

def _element_markup(index: str, element_id: str) -> str:
    """The markup of the element with `id=element_id`, open tag to its own
    close tag, depth-matched on that tag name."""
    m = re.search(r'<(\w+)\b[^>]*\bid="' + re.escape(element_id) + r'"', index)
    assert m, f"no element with id={element_id!r} in index.html"
    tag = m.group(1)
    depth, pos = 0, m.start()
    for t in re.finditer(r"<(/?)" + tag + r"\b[^>]*>", index[pos:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return index[pos:pos + t.end()]
    raise AssertionError(f"unbalanced <{tag}> reading id={element_id!r}")


def test_every_id_scoped_class_lookup_finds_its_element(app, index):
    """`newConversation` reads `label.textContent` before it POSTs anything,
    so a label lookup that answers null throws there and New chat does
    NOTHING, with no error on screen. That shipped: the header move renamed
    the button's `.sb-label` to `.lbl` and `app.js` kept asking for
    `.sb-label` (found by `e2e_scenarios.py` E1, 2026-09-26). Every
    `$("id").querySelector(".cls")` must name a class the element carries.
    """
    pairs = re.findall(
        r'\$\("([\w-]+)"\)\.querySelector\("\.([\w-]+)"\)', app)
    assert pairs, "the lookup pattern this gate reads no longer occurs"
    missing = [f'#{eid} .{cls}' for eid, cls in pairs
               if not re.search(r'class="[^"]*\b' + re.escape(cls) + r'\b',
                                _element_markup(index, eid))]
    assert not missing, (
        f"app.js looks these up and index.html has no such element, so each "
        f"returns null: {missing}")
