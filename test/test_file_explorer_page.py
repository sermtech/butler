"""The PAGE half of the file explorer (docs/file-explorer.md, AC-303..AC-315),
plus the two header fixes that landed with it: the list toggle that no longer
moves on a desk collapse (AC-295) and the microphone refusal said once.

GREP GATES, in the shape of `test_sidebar_collapse_page.py` and for the same
reason: nothing in this suite loads or executes `web/app.js` or
`web/files.js`, so a regression here is invisible to every daemon-side test.
The daemon half -- the two routes and `right_panel` (which replaced
`files_open`, docs/automations.md AU-10) -- is proved in
`test_files.py`, `test_files_routes.py` and `test_shortcuts.py`; the pixels
were proved with `playwright-cli` against a scratch daemon. This file pins
the structure that browser run depended on.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
FILES = ROOT / "web" / "files.js"
INDEX = ROOT / "web" / "index.html"


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", js)


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def files():
    assert FILES.exists(), "web/files.js does not exist"
    return FILES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
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


def _header(index: str) -> str:
    m = re.search(r"<header>.*?</header>", index, re.S)
    assert m, "no <header> found"
    return m.group(0)


# -- AC-295: the toggle does not move, because the header is not in `.col` -- #

def test_the_header_is_not_inside_the_shrinking_column(index):
    """AC-295. `.app` is a grid, and a header inside `.col` moved left by the
    sidebar's whole width on a desk collapse (MEASURED 276px -> 12px). The
    header is a row of `.app` itself now, spanning every column, so the
    sidebar's width cannot move it."""
    col = index.index('<div class="col')
    head = index.index("<header>")
    assert head < col, "the <header> is still inside .col"
    code = re.sub(r"/\*.*?\*/", "", index, flags=re.S)
    m = re.search(r"\.app\s*>\s*header\s*\{([^}]*)\}", code)
    assert m, "no `.app > header` placement rule"
    assert re.search(r"grid-column:\s*1\s*/\s*-1", m.group(1)), (
        f"the header does not span the grid: {m.group(1)!r}")


# -- FE-5 / AC-312 / AC-313: the control, the panel, the tree --------------- #

def test_the_panel_toggle_is_in_the_header_and_names_the_files_chord(index):
    """AC-699: ONE panel toggle in the header (the files button's home), and
    the files panel carries the Files | Library tabs."""
    header = _header(index)
    m = re.search(r'<button[^>]*id="panelBtn"[^>]*>', header, re.S)
    assert m, "#panelBtn is not inside <header>"
    tag = m.group(0)
    assert 'aria-expanded="false"' in tag
    # D8: the page binds the chord itself, so the tooltip names it.
    assert 'data-key="Ctrl+Shift+E"' in tag
    panel = re.search(r'<aside id="filesPanel".*?</aside>', index, re.S)
    assert panel, "no #filesPanel aside"
    for tab in ('data-panel="files"', 'data-panel="library"'):
        assert tab in panel.group(0), f"the files panel has no {tab} tab"


def test_the_tree_is_an_aria_tree(index):
    assert re.search(r'id="filesTree"[^>]*role="tree"|role="tree"[^>]*id="filesTree"',
                     index), "#filesTree is not role=tree"
    assert re.search(r'<aside[^>]*id="filesPanel"', index), "no #filesPanel aside"


def test_treeitems_carry_the_aria_the_spec_names(files):
    for attr in ("aria-expanded", "aria-level", "aria-setsize",
                 "aria-posinset"):
        assert attr in files, f"treeitems never set {attr}"
    assert '"treeitem"' in files and '"group"' in files
    # ONE tab stop: a roving tabindex, not every row focusable.
    assert re.search(r"tabIndex\s*=\s*-1", files)
    assert re.search(r"tabIndex\s*=\s*0", files)


def test_every_key_the_spec_names_is_handled(files):
    for key in ("ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home",
                "End", "Enter", '" "', "Backspace"):
        assert key in files, f"the tree does not handle {key}"


# -- AC-310 as widened by docs/rich-chat.md RC-12/RC-13: the two GETs, one
# raw-bytes URL builder, ONE write (the upload POST), one store write ------- #

def test_the_panel_issues_only_the_two_gets_and_the_one_upload(files):
    """AC-310, widened by the owner's 2026-10-08 ruling (RC-13): the explorer
    stays a reader except for ONE write, a dropped file POSTed to
    `/api/project/{id}/upload`. Catches a second network door, a GET route
    beyond `files`/`file`, any other method, or the upload going elsewhere."""
    code = _strip_comments(files)
    assert code.count("fetch(") == 2, (
        "files.js must reach the network through the GET helper and the "
        "upload helper only")
    assert re.search(r'"/api/project/"\s*\+\s*encodeURIComponent\(', code)
    routes = set(re.findall(r'\bget\(\s*"(\w+)"', code))
    assert routes == {"files", "file"}, f"unexpected routes: {routes}"
    assert re.findall(r'method:\s*"(\w+)"', code) == ["POST"], (
        "files.js sends a method other than the one upload POST")
    upload = _fn_body(code, "upload")
    assert "fetch(" in upload and 'method: "POST"' in upload, (
        "the POST is not inside `upload`")
    assert '"/upload?path="' in upload, "the upload POSTs somewhere else"
    assert "fetch(" in _fn_body(code, "get")


def test_the_raw_url_is_an_element_source_never_fetched(files):
    """RC-12: `/api/project/{id}/raw` is built by ONE helper and handed to
    artifacts.js as `url`/`downloadUrl`; this file never fetches it.
    Catches a third network door hiding behind the URL builder."""
    code = _strip_comments(files)
    assert code.count('"/raw?path="') == 1, "the raw URL is built twice"
    assert '"/raw?path="' in _fn_body(code, "rawUrl")
    assert not re.search(r"fetch\(\s*rawUrl", code), "files.js fetches raw bytes"
    assert re.search(r"url:\s*rawUrl\(path, false\)", code)
    assert re.search(r"downloadUrl:\s*rawUrl\(path, true\)", code)


def test_the_only_write_is_the_right_panel(files):
    """AC-312 as amended by AC-466: the desk's open-or-shut is `right_panel`,
    written through app.js's one `setRight`, and only ever "files" or ""."""
    code = _strip_comments(files)
    assert "env.save(" not in code, "files.js writes the store directly"
    writes = re.findall(r"env\.setRight\((.*?), viaKey\)", code)
    assert writes == ['deskOpen() ? "" : "files"'], writes


def test_a_phone_never_writes_the_right_panel(files):
    """AC-312's phone half: the sheet is a gesture (D-2)."""
    body = _fn_body(files, "toggle")
    phone, _, desk = body.partition("env.setRight(")
    assert "env.isPhone()" in phone, "toggle does not branch on the phone"
    assert "return" in phone, "the phone branch falls through to the write"


# -- AC-314: preview is inert text ------------------------------------------ #

def test_nothing_in_the_explorer_parses_markup(files):
    code = _strip_comments(files)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "DOMParser", "createContextualFragment"):
        assert sink not in code, f"files.js uses {sink} on untrusted names"


def test_the_preview_text_is_set_with_textcontent(files):
    assert re.search(r'\$\("filesText"\)\.textContent\s*=', files), (
        "the preview's text is not written through textContent")


# -- AC-311 / AC-315 / wiring in app.js ------------------------------------- #

def test_the_panel_follows_the_conversation_on_screen(app):
    assert "files.follow()" in _fn_body(app, "paintChatProject")
    assert "files.follow()" in _fn_body(app, "paintShortcuts"), (
        "the root row arrives with the shortcuts answer and nothing re-roots")


def test_the_store_answer_opens_and_shuts_the_desk_panel(app):
    body = _fn_body(app, "paintShortcuts")
    assert "doc.right_panel" in body and "files.rightChanged(" in body


def test_a_turn_end_reaches_the_explorer(app):
    assert "files.turnEnded(" in _fn_body(app, "trackTurnFrame")
    assert "files.turnEnded(" in _fn_body(app, "goListen")


def test_ctrl_shift_e_and_the_files_tab_reach_the_same_function(app):
    """The Files tab and the panel toggle go through `openPanelTab`, which
    calls the module's own `toggle`; Ctrl+Shift+E calls `files.toggle`."""
    assert "mod.toggle(" in _fn_body(app, "openPanelTab")
    assert "files.toggle(" in _fn_body(app, "togglePanel")
    assert re.search(r'tab\.onclick\s*=\s*\(\)\s*=>\s*openPanelTab\(', app), (
        "the panel's tabs do not call openPanelTab")
    m = re.search(r'e\.code === "KeyE".{0,300}?files\.toggle\(', app, re.S)
    assert m, "Ctrl+Shift+E does not call files.toggle"


def test_escape_closes_the_sheet_after_the_drawer_and_before_stop(app):
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m, "no global Escape block"
    block = m.group(1)
    order = [block.index(s) for s in
             ("panelOpen()", "drawerOpen()", "files.sheetOpen()", "stopTurn()")]
    assert order == sorted(order), f"Escape order is wrong: {order}"


def test_the_header_overflow_is_deleted(app, index):
    """lantern-ui section 2: `#headerMore` and `layoutHeaderControls` are
    deleted; nothing is reparented into an overflow any more."""
    code = re.sub(r"<!--.*?-->", "", index, flags=re.S)
    for gone in ('id="headerMore"', 'id="headerMorePop"', 'id="filesBtn"'):
        assert gone not in code, f"{gone} survived"
    assert "layoutHeaderControls" not in app and "headerMore" not in app


# -- the microphone refusal is said ONCE, on the persistent line ------------ #

def test_a_microphone_refusal_does_not_raise_the_banner(app):
    """`micAction` already paints the refusal and its remedy on `#micLine`,
    which stays up; the strip saying it again was the duplicate."""
    for fn in ("arm", "toggleDictation"):
        body = _fn_body(app, fn)
        assert "showError(micRefusalMessage(e))" not in body, (
            f"{fn} still raises the banner for a refusal")
        assert "reportMicFailure(e)" in body, f"{fn} lost its mic report"
    report = _fn_body(app, "reportMicFailure")
    assert re.search(r'if \(name === "NotAllowedError" \|\| name === '
                     r'"SecurityError"\) logError\(text\);\s*else showError\(text\);',
                     report), (
        "a refusal must reach #errors only, and every other failure the strip")


def test_the_error_strip_reserves_space_below_itself(index):
    code = re.sub(r"/\*.*?\*/", "", index, flags=re.S)
    m = re.search(r"\.errstrip\s*\{([^}]*)\}", code)
    assert m
    margin = re.search(r"margin:\s*([^;]+);", m.group(1)).group(1).split()
    assert len(margin) >= 3 and margin[2] not in ("0", "0px"), (
        f"the strip's bottom margin is zero, so the transcript's clip edge "
        f"sits flush under it: {margin}")
