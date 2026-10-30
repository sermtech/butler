"""The Lantern shell: header, sidebar rows, account menu, voice popover, the
composer's chips, the one right panel, the sheets (docs/lantern-ui.md Step
3b, AC-683..AC-688, AC-699, AC-706..AC-708, CU-9..CU-15).

GREP GATES over `web/`, in the shape of `test_sidebar_collapse_page.py`:
nothing in this suite runs `app.js`, so these pin the structure the probes
(`probe_page.py`, `probe_ux.py`, `e2e_scenarios.py` K12..K15) exercise in a
browser. Section 2's table of "what moved where" is the list read here.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"
FILES = ROOT / "web" / "files.js"
LIBRARY = ROOT / "web" / "library.js"


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markup(index):
    """The body, comments stripped: a comment naming an old id is history,
    not an element."""
    body = index[index.index("<body>"):]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


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


def _element(markup: str, element_id: str) -> str:
    """The element with `id=element_id`, open tag to its own close tag."""
    m = re.search(r'<(\w+)\b[^>]*\bid="' + re.escape(element_id) + r'"', markup)
    assert m, f"no element with id={element_id!r}"
    tag = m.group(1)
    depth, pos = 0, m.start()
    for t in re.finditer(r"<(/?)" + tag + r"\b[^>]*>", markup[pos:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return markup[pos:pos + t.end()]
    raise AssertionError(f"unbalanced <{tag}> reading id={element_id!r}")


def _ids(fragment: str) -> set:
    return set(re.findall(r'\bid="([^"]+)"', fragment))


# -- AC-683: the header holds four things ---------------------------------- #

def test_the_header_holds_the_panel_icon_the_pill_and_the_panel_toggle(markup):
    header = re.search(r"<header>.*?</header>", markup, re.S).group(0)
    outside_pop = header.replace(_element(header, "voicePop"), "")
    controls = re.findall(r'<button\b[^>]*\bid="([^"]+)"', outside_pop)
    assert controls == ["burger", "stateChip", "voiceChev", "panelBtn"], (
        f"the header's controls are {controls}")
    tag = re.search(r'<span\b[^>]*id="ambientChip"[^>]*>', header)
    assert tag and 'role="status"' in tag.group(0) and "hidden" in tag.group(0), (
        "the Recording room tag is not a hidden status span in the header")


# -- AC-684: nothing reachable became unreachable (section 2's table) ------- #

GONE = ("lookBtn", "lookPop", "muteChip", "headerMore", "headerMorePop",
        "filesBtn", "sbClose", "footDot", "moreWrap")

HOMES = {
    "sidebar": ("newChat", "searchRow", "chatSearch", "libraryBtn",
                "projAdd", "acctBtn", "settingsBtn"),
    "acctPop": ("acctSettings", "acctAppear", "tasksPageBtn", "keysBtn",
                "diagBtn", "restartBtn"),
    "voicePop": ("wakeSw", "speakBtn", "roomSw", "roomWarn"),
    "composer": ("attachBtn", "agentBtn", "modelBtn", "modelPop", "micBtn",
                 "sendBtn"),
    "diagSheet": ("diagPanel", "errors", "modeLabel", "trace"),
}


def test_every_deleted_control_is_gone(markup, app):
    present = _ids(markup)
    survived = [i for i in GONE if i in present]
    assert not survived, f"deleted controls survived in the markup: {survived}"
    named = [i for i in GONE if f'$("{i}")' in app]
    assert not named, f"app.js still looks up deleted controls: {named}"
    assert 'class="wordmark"' not in markup


@pytest.mark.parametrize("home", sorted(HOMES))
def test_every_control_has_its_new_home(markup, home):
    inside = _ids(_element(markup, home))
    missing = [i for i in HOMES[home] if i not in inside]
    assert not missing, f"#{home} is missing {missing}"


# -- AC-686, CU-10: the pill keeps its job; the switches write through ------- #

def test_the_pill_body_is_still_driven_by_mic_action(app):
    body = _fn_body(app, "paint")
    assert "STATE_CHIP.setAttribute(\"aria-label\", mic.aria)" in body
    assert '$("stateKicker").textContent = kicker' in body


def test_the_voice_switches_write_their_existing_settings(app):
    wake = re.search(r'\$\("wakeSw"\)\.addEventListener\("click",(.*?)\}\);', app, re.S)
    assert wake and "listenOff()" in wake.group(1) and "listenOn()" in wake.group(1)
    room = re.search(r'\$\("roomSw"\)\.addEventListener\("click",(.*?)\}\);\n\}\);',
                     app, re.S)
    assert room and 'settings.set("ambient_seconds"' in room.group(1)
    speak = re.search(r'\$\("speakBtn"\)\.addEventListener\("click",(.*?)\}\);\n\}\);',
                      app, re.S)
    assert speak and 'settings.set("speak"' in speak.group(1)


def test_the_switches_paint_from_state_never_from_the_press(app):
    """Each switch is painted from what the daemon and the microphone say."""
    body = _fn_body(app, "paintAmbient")
    assert 'paintSwitch($("wakeSw"), micLive)' in body
    assert 'paintSwitch($("roomSw"), ambientSeconds > 0)' in body
    assert "paintSwitch($(\"speakBtn\"), !speakMuted)" in _fn_body(
        app, "paintSpeakButton")


# -- AC-687: room memory is never silent ------------------------------------ #

def test_the_room_tag_follows_the_ring(app):
    body = _fn_body(app, "paintAmbient")
    assert "const on = micLive && ambientSeconds > 0;" in body
    assert "tag.hidden = !on;" in body
    assert '$("roomWarn").hidden = !on;' in body
    assert "paintAmbient();" in _fn_body(app, "paint")


# -- AC-688: the agent and model chips live in the composer ----------------- #

def test_the_chips_are_the_quick_pickers_in_the_composer(markup, app):
    tools = _element(markup, "composer")
    assert 'class="modelwrap"' in tools
    for key, el in (("pickRoot", "modelPop"), ("pickWho", "agentBtnName"),
                    ("pickLabel", "modelBtnName")):
        assert re.search(key + r':\s*\$\("' + el + r'"\)', app), (
            f"settings.js is not handed {key} = #{el}")


# -- AC-699: one panel, two tabs --------------------------------------------- #

def test_both_panels_carry_the_two_tabs_and_report_to_one_painter(markup):
    for panel in ("filesPanel", "libraryPanel"):
        body = _element(markup, panel)
        tabs = re.findall(r'class="ptab" data-panel="(\w+)"', body)
        assert tabs == ["files", "library"], f"#{panel} tabs: {tabs}"
    for src in (FILES, LIBRARY):
        assert "env.painted();" in src.read_text(encoding="utf-8"), (
            f"{src.name} paints without telling the panel toggle")


def test_right_panel_has_one_writer(app):
    writes = re.findall(r"^\s*rightPanel\s*=", app, re.M)
    assert len(writes) == 2, (
        f"rightPanel is assigned {len(writes)} times; setRight and the "
        f"store's answer in paintShortcuts are the only two")


# -- AC-706, CU-15: popovers and sheets add no global Escape case ----------- #

def test_the_global_escape_handler_gained_no_case(app):
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m, "no global Escape block"
    block = m.group(1)
    for name in ("voicePop", "acctPop", "diagSheet", "keysSheet",
                 "restartDlg", "closeShellPops"):
        assert name not in block, f"{name} became a global Escape case"


def test_the_shell_popovers_yield_escape_to_a_stop(app):
    body = _fn_body(app, "popover")
    assert "if (!escapeWouldStop()) e.stopPropagation();" in body
    assert '}, true);' in body, "the Escape listener is not capture-phase"


def test_the_sheets_close_on_escape_in_their_own_capture_phase(app):
    m = re.search(r'for \(const dlg of document\.querySelectorAll\("dialog\.sheet"\)\)'
                  r' \{(.*?)\n\}', app, re.S)
    assert m and "dlg.close();" in m.group(1) and "}, true);" in m.group(1)


# -- AC-707: diagnostics keeps every readout --------------------------------- #

def test_every_hud_slot_is_inside_the_diagnostics_sheet(markup, app):
    ids = set(re.findall(r'\bhud\(\s*"([^"]+)"', app))
    assert ids, "no hud() call sites found"
    sheet = _ids(_element(markup, "diagSheet"))
    missing = sorted(ids - sheet)
    assert not missing, f"hud() slots outside the Diagnostics sheet: {missing}"


def test_details_opens_the_diagnostics_sheet(app):
    m = re.search(r'\$\("errStripDetails"\)\.onclick = \(\) => \{(.*?)\};', app, re.S)
    assert m and "openDiag(" in m.group(1)


# -- AC-708: the shortcuts sheet reads the project chords from the daemon ---- #

def test_the_shortcuts_sheet_takes_project_chords_from_the_daemon(app, markup):
    body = _fn_body(app, "paintKeys")
    assert 'shortcutsFetch("GET")' in body
    assert "doc.rows" in body and "doc.agents" in body and "doc.dictate" in body
    sheet = _element(markup, "keysSheet")
    assert 'id="keysProjects"' in sheet


# -- lantern-ui 11: the export's button reset lives in a cascade layer -------- #

def test_the_button_reset_is_layered(index):
    css = index[index.index("<style>"):index.index("</style>")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(r"@layer reset \{(.*?)\n  \}", css, re.S)
    assert m and "background: none" in m.group(1), "no layered button reset"
    outside = css.replace(m.group(0), "")
    assert not re.search(r"(^|[,\s])(header|#sidebar|\.pop) button\s*[,{]",
                         outside), "a bare shell button reset sits outside @layer"
