"""The PAGE half of the chat agent picker (docs/chat-agent-picker.md option C,
AC-776, AC-777). GREP GATES, in the shape of `test_terminal_page.py`: nothing
here executes `web/app.js`. The daemon half is `test_chat_agent.py`; the
pixels were checked in a real browser against a scratch daemon.
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


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(app):
    return _strip_comments(app)


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def picker(code):
    """The picker's own code: from its banner's first function to the reset
    button, plus the two functions that read the payload and start a chat."""
    start = code.index("let agentMenuAnchor")
    end = code.index('$("resetAll").onclick')
    return (code[start:end] + _fn_body(code, "paintChatAgent")
            + _fn_body(code, "newConversation") + _fn_body(code, "newChatFrom")
            + _fn_body(code, "newChatIn"))


# -- AC-777: the page names no agent and no setting --------------------------- #

def test_picker_code_names_no_agent(picker):
    for name in ("claude", "opencode", "pi", "codex", "fake", "Claude Code"):
        assert not re.search(r"""["'`]%s["'`]""" % re.escape(name), picker), (
            f"the picker spells the agent {name!r} as a literal")


def test_picker_code_names_no_setting(picker):
    for key in ("harness", "harness_model", "brain_mode", "agent_default_setting"):
        assert not re.search(r"""["'`]%s["'`]""" % key, picker), key


def test_picker_reads_everything_from_the_payload(code):
    body = _fn_body(code, "paintChatAgent")
    for field in ("agents", "agent_default", "agent_fixed", "model",
                  "model_default", "agent_label"):
        assert field in body, f"paintChatAgent does not read `{field}`"


def test_picker_text_is_textcontent_only(picker):
    assert "innerHTML" not in picker
    assert "insertAdjacentHTML" not in picker
    assert "outerHTML" not in picker
    assert "textContent" in picker


def test_no_browser_storage_in_picker(picker):
    assert "localStorage" not in picker and "sessionStorage" not in picker


# -- the routes it uses ------------------------------------------------------- #

def test_chip_posts_the_chat_agent_route(code):
    body = _fn_body(code, "setChatAgent")
    assert '"/api/chats/"' in body and '"/agent"' in body
    assert 'method: "POST"' in body
    assert "paintChatAgent(doc)" in body, "the chip must repaint from the answer"
    assert "409" in body, "a started chat (409) must drop the chip"


def test_birth_sends_the_agent_only_when_chosen(code):
    body = _fn_body(code, "newConversation")
    assert "/api/session/new" in body
    assert "...(agent ? { agent } : {})" in body, (
        "the main button must keep sending no agent (the daemon's default)")


def test_main_new_chat_buttons_still_pass_no_agent(code):
    # The one-click path is unchanged: no third argument after the id.
    assert re.search(r"newChatFrom\(\s*\$\(\"newChat\"\),\s*\$\(\"newChat\"\)"
                     r"\.querySelector\(\"\.lbl\"\),\s*id\)", code)
    assert "newChatFrom(btn, label, id));" in _fn_body(code, "newChatIn")


# -- the chip: hidden once the agent is fixed --------------------------------- #

def test_chevrons_hide_on_a_single_choice_and_the_chip_stays(code):
    # The New chat chevrons need a choice of agent; the composer's chip does
    # not (the model can change in a fixed chat), so it hides only with no
    # payload or no conversation (docs/chat-model.md CM-6).
    body = _fn_body(code, "paintAgentPicker")
    assert re.search(r"chip\.hidden\s*=\s*!agentInfo\.agents\.length\s*\|\|\s*!liveChatId",
                     body)
    assert "c.hidden = !hasAgentChoice()" in body
    assert "agents.length > 1" in code


def test_a_taken_turn_fixes_the_agent(code):
    # the definition, the 409 path and the `final` that took a turn
    assert code.count("agentSpent()") >= 3



def test_a_listing_row_does_not_reset_the_picker(code):
    body = _fn_body(code, "paintChatAgent")
    assert "!Array.isArray(doc.agents)" in body and "return;" in body


def test_endpoint_mode_clears_the_picker(code):
    body = _fn_body(code, "loadSession")
    assert "paintChatAgent(null)" in body


# -- the menu is a popover, with the popovers' Escape rule -------------------- #

def test_escape_yields_to_the_stop(picker):
    m = re.search(r'document\.addEventListener\("keydown".*?\}, true\);', picker, re.S)
    assert m, "the menu's keydown listener must be capture-phase"
    assert "escapeWouldStop()" in m.group(0)
    assert 'e.key === "Escape"' in m.group(0)


def test_menu_closes_on_outside_press(picker):
    assert re.search(r'document\.addEventListener\("pointerdown"', picker)


def test_menu_is_keyboard_reachable(picker):
    for key in ("ArrowDown", "ArrowUp", "Home", "End"):
        assert key in picker
    assert ".focus()" in picker


def test_global_escape_handler_has_no_menu_case(code):
    # The popovers' rule (web/AGENTS.md): no case in the global handler.
    for m in re.finditer(r'addEventListener\("keydown"', code):
        window = code[m.start():m.start() + 600]
        if "agentMenu" in window:
            assert "escapeWouldStop" in window


# -- markup ------------------------------------------------------------------- #

def test_markup_has_the_three_doors(index):
    for ident in ("chatAgentChip", "chatAgentChipName", "newChatChev", "agentMenu"):
        assert f'id="{ident}"' in index, ident
    assert re.search(r'id="chatAgentChip"[^>]*\bhidden\b', index), (
        "the chip ships hidden until the payload says so")
    assert re.search(r'id="newChatChev"[^>]*\bhidden\b', index)


def test_chip_sits_left_of_the_model_chips(index):
    assert index.index('id="chatAgentChip"') < index.index('class="modelwrap"')


def test_menu_is_a_sibling_after_the_project_popover(index):
    # Like `#projPop`: `#sidebar`'s backdrop-filter traps fixed children
    # (web/AGENTS.md), so the menu is not inside it.
    assert index.index('id="agentMenu"') > index.index('id="projPop"')
    sidebar_open = index.index('id="sidebar"')
    assert 'id="newChatChev"' in index[sidebar_open:index.index('id="projPop"')]
