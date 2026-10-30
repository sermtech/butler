"""The PAGE half of the per-chat model (docs/chat-model.md CM-5..CM-9, AC-785,
AC-786). GREP GATES, in the shape of `test_chat_agent_page.py`: nothing here
executes `web/app.js`. The daemon half is `test_chat_model.py`; the pixels were
checked in a real browser against a scratch daemon.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", js)


def _fn_body(source: str, name: str) -> str:
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{",
                  source)
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


@pytest.fixture(scope="module")
def code():
    return _strip_comments(APP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chip(code):
    """The chip's own code: from the sheet's banner state to the reset button,
    plus the two functions that read the payload and paint the chip."""
    start = code.index("const modelCatalog")
    end = code.index('$("resetAll").onclick')
    return (code[start:end] + _fn_body(code, "paintChatAgent")
            + _fn_body(code, "paintAgentPicker"))


# -- AC-785: the page names no model, agent or setting ------------------------ #

def test_chip_code_names_no_agent_model_or_setting(chip):
    for name in ("claude", "opencode", "pi", "codex", "fake", "Claude Code",
                 "sonnet", "opus", "haiku", "harness", "harness_model",
                 "model_favourites", "brain_mode"):
        assert not re.search(r"""["'`]%s["'`]""" % re.escape(name), chip), (
            f"the chip spells {name!r} as a literal")


def test_chip_reads_the_payload(code):
    body = _fn_body(code, "paintChatAgent")
    for field in ("doc.model", "doc.model_default", "doc.agents", "doc.agent_fixed"):
        assert field in body, f"paintChatAgent does not read `{field}`"


def test_chip_label_comes_from_the_payload(code):
    body = _fn_body(code, "paintAgentPicker")
    assert "agentInfo.label" in body and "agentInfo.model" in body
    assert "agentInfo.modelDefault" in body
    assert '"Default"' in body and '" ("' in body


def test_chip_text_is_textcontent_only(chip):
    for sink in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"):
        assert sink not in chip
    assert "textContent" in chip


def test_no_browser_storage_in_chip(chip):
    assert "localStorage" not in chip and "sessionStorage" not in chip


# -- the routes it uses ------------------------------------------------------- #

def test_sheet_fetches_the_agents_model_list(code):
    body = _fn_body(code, "loadAgentModels")
    assert '"/api/agents/"' in body and '"/models"' in body
    assert "encodeURIComponent(agent)" in body


def test_models_are_cached_per_agent_and_refetched_after_a_pick(code):
    assert "const modelCatalog = new Map()" in code
    pick = _fn_body(code, "setChatModel")
    assert "loadAgentModels(" in pick, "Recent must be refetched after a pick"
    opener = _fn_body(code, "openModelSheet")
    assert "modelCatalog.get(agentInfo.agent)" in opener


def test_pick_posts_the_chat_model_route(code):
    body = _fn_body(code, "setChatModel")
    assert '"/api/chats/"' in body and '"/model"' in body
    assert 'method: "POST"' in body
    assert "JSON.stringify({ model: name })" in body
    assert "paintChatAgent(doc)" in body, "the chip must repaint from the answer"


def test_default_row_clears_the_model(code):
    body = _fn_body(code, "renderModelSheet")
    assert 'pick("")' in body, "the Default row must POST an empty model"
    assert '"Default ("' in body


def test_agent_radio_posts_the_agent_route(code):
    body = _fn_body(code, "renderModelSheet")
    assert "setChatAgent(a.name)" in body
    assert '"radio"' in body
    assert " -- this chat stays on " in body, "a fixed chat says so, read only"


# -- sections, search, "(not listed)" ----------------------------------------- #

def test_sheet_has_favourites_recent_and_all_models(code):
    body = _fn_body(code, "renderModelSheet")
    for word in ("Favourites", "Recent", "All models", "data.favourites",
                 "data.recent", "data.models"):
        assert word in body, word
    assert "sheetAllOpen" in body, "All models is a fold"


def test_search_filters_by_case_insensitive_substring(code):
    body = _fn_body(code, "renderModelSheet")
    assert "toLowerCase()" in body and ".includes(q)" in body
    assert '$("msSearch").value' in body
    assert 'addEventListener("input", renderModelSheet)' in code


def test_a_model_missing_from_the_catalogue_is_not_listed_and_kept(code):
    body = _fn_body(code, "renderModelSheet")
    assert '" (not listed)"' in body
    assert "!data.models.includes(own)" in body
    # kept: the row still POSTs the model it shows (it is the checked one)
    assert "pick(own)" in body


def test_model_chips_no_longer_follow_model_applies(code):
    assert "model_applies" not in code and "modelApplies" not in code


# -- the popover rules -------------------------------------------------------- #

def test_sheet_escape_yields_to_the_stop(code):
    m = re.search(r'document\.addEventListener\("keydown", \(e\) => \{\s*'
                  r'if \(!modelSheetOpen\(\)\) return;.*?\}, true\);', code, re.S)
    assert m, "the sheet's keydown listener must be capture-phase"
    block = m.group(0)
    assert 'e.key === "Escape"' in block and "escapeWouldStop()" in block
    for key in ("ArrowDown", "ArrowUp", "Home", "End"):
        assert key in block
    assert ".focus()" in block


def test_sheet_closes_on_outside_press_and_returns_focus(code):
    assert re.search(r'document\.addEventListener\("pointerdown", \(e\) => \{\s*'
                     r'if \(!modelSheetOpen\(\)\) return;', code)
    close = _fn_body(code, "closeModelSheet")
    assert 'if (refocus) $("chatAgentChip").focus()' in close


def test_other_popovers_shut_the_sheet(code):
    assert "closeModelSheet(false)" in _fn_body(code, "closeShellPops")
    assert "closeModelSheet(false)" in code[code.index("const MODEL_CHIPS"):
                                            code.index("function hasAgentChoice")]


def test_no_sheet_case_in_the_global_escape_handler(code):
    for m in re.finditer(r'addEventListener\("keydown"', code):
        window = code[m.start():m.start() + 600]
        if "modelSheet" in window:
            assert "escapeWouldStop" in window


# -- markup ------------------------------------------------------------------- #

def test_markup_has_one_chip_and_the_sheet(index):
    for ident in ("chatAgentChip", "chatAgentChipName", "chatModelName",
                  "chatModelPar", "modelSheet", "msAgent", "msSearch", "msList"):
        assert f'id="{ident}"' in index, ident
    assert re.search(r'id="chatAgentChip"[^>]*\bhidden\b', index)
    assert re.search(r'id="modelSheet"[^>]*\bhidden\b', index)
    assert index.count('id="chatAgentChip"') == 1


def test_dot_is_a_css_escape_and_the_source_stays_ascii(index):
    assert 'content: "\\00B7"' in index
    assert index.isascii()


def test_phone_drops_the_parenthesis_and_becomes_a_bottom_sheet(index):
    m = re.search(r"@media \(max-width: 600px\) \{\s*\.agentpick \.par, \.agentpick \.glyph \{ display: none; \}"
                  r".*?#modelSheet \{[^}]*bottom: 0", index, re.S)
    assert m, "the 600 px block must drop `.par` and pin the sheet to the bottom"


def test_search_is_pinned_above_a_scrolling_list(index):
    assert index.index('id="msSearch"') < index.index('id="msList"')
    assert re.search(r"\.mslist \{[^}]*overflow-y: auto", index)
    assert re.search(r"\.mssearch \{[^}]*flex: none", index)


def test_sheet_is_a_sibling_after_the_project_popover(index):
    # Like `#agentMenu`: `#sidebar`'s backdrop-filter traps fixed children.
    assert index.index('id="modelSheet"') > index.index('id="projPop"')


def test_the_who_answers_chip_goes_when_no_setting_answers():
    """docs/chat-model.md CM-6: in the harness modes `answers` is null, so
    the "who answers" chip hides (no bare arrow) and, on a phone, the
    popover's other chip stays reachable instead of both vanishing."""
    js = (ROOT / "web" / "settings.js").read_text(encoding="utf-8")
    assert re.search(r'who\.hidden\s*=\s*!headlinePick', js)
    html = INDEX.read_text(encoding="utf-8")
    assert "#agentBtn:not([hidden]) + #modelBtn { display: none; }" in html
    assert not re.search(r"^\s*#modelBtn \{ display: none; \}", html, re.M)


def test_a_short_catalogue_is_never_folded(code):
    """Review 2026-10-29: Claude Code's three models sat behind "All models
    (3)". A catalogue of SHEET_FLAT_MAX or fewer is listed whole."""
    assert re.search(r"const SHEET_FLAT_MAX = \d+;", code)
    body = _fn_body(code, "renderModelSheet")
    assert re.search(r"data\.models\.length <= SHEET_FLAT_MAX\)", body)
