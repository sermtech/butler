"""The Lantern composer and conversation (docs/lantern-ui.md Step 3c,
AC-689..AC-696, CU-3, CU-6, CU-8, CU-12..CU-15).

GREP GATES over `web/`, in the shape of `test_lantern_shell_page.py`:
nothing here runs `app.js`, so these pin the structure the probes
(`probe_page.py`, `probe_ux.py`, `e2e_scenarios.py`, `probe_reduced_motion.py`)
exercise in a browser.
"""
import re
from pathlib import Path

import pytest

from test_lantern_shell_page import _element, _fn_body, _ids

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"


def _code(source: str) -> str:
    """JavaScript with its comments stripped, so a pin cannot match its own
    explanation."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//[^\n\"']*$", "", source)


@pytest.fixture(scope="module")
def app():
    return _code(APP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css(index):
    sheet = index[index.index("<style>"):index.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)


@pytest.fixture(scope="module")
def markup(index):
    body = index[index.index("<body>"):]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|[}\s,])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no CSS rule for {selector!r}"
    return m.group(1)


# -- 1. the composer ---------------------------------------------------------- #

def test_the_composer_row_is_the_exports(markup):
    composer = _element(markup, "composer")
    order = re.findall(r'\bid="(input|attachBtn|agentBtn|modelBtn|micBtn|sendBtn)"',
                       composer)
    assert order == ["input", "attachBtn", "agentBtn", "modelBtn", "micBtn",
                     "sendBtn"], order
    assert re.search(r'id="input"[^>]*placeholder="Message Butler"', composer)


def test_the_stop_says_its_word_while_busy(markup, app):
    send = _element(markup, "sendBtn")
    assert re.search(r'id="stopWord"[^>]*hidden[^>]*>Stop<', send), (
        "the stop square needs the word Stop beside it (lantern-ui section 4)")
    body = _fn_body(app, "paintSend")
    assert re.search(r'\$\("stopWord"\)\.removeAttribute\("hidden"\)', body)
    assert re.search(r'\$\("stopWord"\)\.setAttribute\("hidden", ""\)', body)
    # A draft still outranks the stop (web/AGENTS.md).
    assert "const stopping = busy && !draft;" in body


def test_the_box_is_the_exports_shape(css):
    rule = _rule(css, ".composer")
    assert "var(--rd)" in rule and "var(--raised)" in rule, rule


# -- 2. the lit border (AC-690, CU-3) ----------------------------------------- #

def test_the_band_is_gone_and_the_one_canvas_stays(markup, app):
    ids = _ids(markup)
    for gone in ("waveTop", "bandWord", "micLine"):
        assert gone not in ids, f"#{gone} survived Step 3c"
        assert f'"{gone}"' not in app, f"app.js still names #{gone}"
    assert markup.count('id="wave"') == 1
    m = re.search(r"const VIZ_SLOT = \{([^}]*)\};", app)
    assert m and "band" not in m.group(1), m and m.group(1)


def test_the_lit_border_is_css_on_the_level(css, markup, app):
    assert "lantern" not in app, (
        "app.js names a voice_effect value; the page draws it from CSS (AC-41)")
    fx = _element(markup, "composerFx")
    assert 'class="fxlantern"' in fx
    lit = re.findall(r'\[data-voicefx="lantern"\][^{]*\{[^}]*\}', css)
    assert lit, "no [data-voicefx=\"lantern\"] rule"
    joined = "\n".join(lit)
    assert "--fx-level" in joined, "the glow does not follow the level"
    for state, colour in (("capturing", "--blue"), ("thinking", "--amber"),
                          ("speaking", "--amber")):
        assert re.search(r'\[data-state="' + state + r'"\][^{]*\{[^}]*--lit:\s*var\('
                         + colour + r'\)', css), f"{state} is not {colour}"


def test_reduced_motion_keeps_the_colour_and_drops_the_motion(css):
    blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n  \}",
                        css, re.S)
    lantern = [b for b in blocks if "fxlantern" in b]
    assert lantern, "no reduced-motion rule for the lit border"
    rule = re.search(r"\.fxlantern[^{]*\{([^}]*)\}", lantern[0]).group(1)
    assert "--fx-level" not in rule and "var(--lit" in rule, rule


# -- 3. live words (AC-689, CU-6) --------------------------------------------- #

def test_the_live_words_live_in_the_box_and_move_in_one_place(markup, app):
    assert markup.count('id="liveLine"') == 1
    assert 'id="liveLine"' in _element(markup, "composer")
    moves = [m.start() for m in re.finditer(
        r'\.(?:append|prepend|before|after|appendChild)\(\$\("liveLine"\)\)', app)]
    body = _fn_body(app, "placeByStyle")
    assert len(moves) == 1 and '$("liveLine")' in body, (
        "placeByStyle must be the one place that re-parents #liveLine")
    assert "--live-slot" in body, "the slot is the style's to name, not app.js's"


def test_render_live_is_the_only_writer_of_the_words(app):
    writes = re.findall(r'\$\("(committed|tentative)"\)\.textContent =', app)
    body = _fn_body(app, "renderLive")
    assert len(writes) == 2 and len(re.findall(r"\.textContent =", body)) >= 2


# -- 4. the error line (AC-696, CU-13) ---------------------------------------- #

def test_the_error_line_sits_directly_above_the_composer(markup):
    dock = _element(markup, "dock")
    assert 'id="errStrip"' in dock and 'role="alert"' in _element(dock, "errStrip")
    order = re.findall(r'\bid="(errStrip|askCard|composer)"', dock)
    assert order == ["errStrip", "askCard", "composer"], order
    assert 'id="errStripDetails"' in _element(dock, "errStrip")


def test_a_lost_link_says_so_on_the_line_and_keeps_the_draft(app):
    body = _fn_body(app, "paintErrLine")
    assert "linkSkin" in body and "Not connected." in body
    for writer in ("setLinkSkin", "showError", "clearErrors"):
        assert "paintErrLine()" in _fn_body(app, writer), writer
    send = _fn_body(app, "sendTyped")
    dead = send.index("ws.readyState !== WebSocket.OPEN")
    assert "composerInput.value = \"\"" not in send[dead:send.index("return;", dead)]


# -- 5. the empty state (AC-692, CU-14) --------------------------------------- #

def test_the_empty_state_centres_the_composer(app, css, markup):
    body = _fn_body(app, "setEmptyState")
    assert 'classList.toggle("empty", shown)' in body
    assert "getHours()" in body, "the greeting is the local clock's"
    for el in ("histEmpty", "emptyChips"):
        writes = re.findall(r'\$\("' + el + r'"\)\.hidden =', app)
        assert len(writes) == 1 and f'$("{el}").hidden =' in body, el
    assert "justify-content: center" in _rule(css, ".col.empty")
    chips = _element(markup, "emptyChips")
    assert len(re.findall(r'class="exchip"', chips)) == 3
    # AC-105, AC-809: the line names the words that are on, written by app.js
    # from the daemon's list; until then the markup says "the wake word".
    assert re.search(r"Say <b>the wake word</b>, or type\.", chips), "AC-105"
    assert '$("emptyHint").replaceChildren("Say ", b, ", or type.")' in app


# -- 6. the question card (AC-693) -------------------------------------------- #

def test_the_question_is_docked_with_numbered_options(app):
    block = _fn_body(app, "askBlock")
    assert 'className = "n"' in block, "options carry their number"
    assert "Or type your answer for " in _fn_body(app, "paintComposerPlaceholder")


def test_number_keys_are_scoped_and_escape_gains_no_case(app):
    m = re.search(r'\$\("dock"\)\.addEventListener\("keydown", askKey\)', app)
    assert m, "the 1-3 keys must listen on the dock, never the document"
    body = _fn_body(app, "askKey")
    assert "composerInput.value" in body and "chooseAnswer(" in body
    assert "sendAnswer(" in _fn_body(app, "chooseAnswer")
    assert "Escape" not in body


# -- 7. working and finished rows (AC-694, AC-695, CU-12) --------------------- #

def test_the_working_row_is_the_last_item_and_holds_the_state_words(markup, app):
    history = _element(markup, "history")
    assert 'id="workRow"' in history and 'id="stateDesc"' in _element(history, "workRow")
    stop = re.search(r'\$\("workStop"\)\.addEventListener\("click", \(\) => stopTurn\(\)\)',
                     app)
    assert stop, "the row's Stop is the one stop (D-18)"
    item = _fn_body(app, "historyItem")
    assert '$("workRow").before(item)' in item, "a turn lands above the row"
    for w in re.finditer(r'\$\("stateDesc"\)\.textContent =', app):
        assert w.start() > app.index("function paint()") and \
            w.start() < app.index("function paintHint()"), "paint() is the one writer"


def test_the_pill_is_described_by_the_mic_action(markup):
    chip = re.search(r'<button\b[^>]*id="stateChip"[^>]*>', markup).group(0)
    assert 'aria-describedby="orbLabel"' in chip
    assert re.search(r'<span class="sronly" id="orbLabel">', markup)


def test_a_finished_agent_says_so_live_and_after_a_reload(app):
    fin = _fn_body(app, "paintFinished")
    assert "finished in" in fin and "You chose: " in fin
    beat = app[app.index('case "progress":'):]
    beat = beat[:beat.index("break;")]
    assert "m.agent" in beat and "paintFinished(" in beat
    render = _fn_body(app, "renderMessages")
    assert "m.agentElapsedMs" in render and "paintFinished(" in render


# -- 8. the transcript (CU-8, CU-7) ------------------------------------------- #

def test_message_actions_are_icons_with_tooltips(app, css):
    assert "actLabel" not in app, "a message action still carries a word"
    for fn, words in (("copyButton", "Copy"), ("rewindButton", "Rewind"),
                      ("retryButton", "Try again"), ("sayButton", "Read aloud")):
        assert f'dataset.help = "{words}"' in _fn_body(app, fn), fn
    rule = _rule(css, ".copybtn")
    assert "36px" in rule
    assert re.search(r"\.turn:hover \.copybtn,\s*\.turn:focus-within \.copybtn", css)


def test_no_visible_meta_header_and_the_bubble(css):
    meta = re.search(r"\.turn \.meta:not\(:has\(\.via\)\)[^{]*\{([^}]*)\}", css)
    assert meta and "clip" in meta.group(1), "the meta row is still drawn"
    said = _rule(css, ".said")
    assert "var(--raised)" in said and "18px" in said


# -- 9. the sidebar's running row --------------------------------------------- #

def test_a_running_row_names_its_agent_from_the_daemon(app):
    row = _fn_body(app, "buildChatRow")
    assert "c.harness" in row
