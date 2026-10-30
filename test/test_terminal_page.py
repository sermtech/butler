"""The PAGE half of in-page terminals (docs/chats-and-terminals.md CT-4, CT-5,
AC-765..AC-767). GREP GATES, in the shape of `test_file_explorer_page.py`:
nothing in this suite executes `web/terminal.js`. The daemon half (routes, the
owner-only gate, the host) is proved in its own tests; the pixels need a real
browser against a scratch daemon.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TERM = ROOT / "web" / "terminal.js"
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"
FETCH = ROOT / "scripts" / "fetch-xterm.py"


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
def term():
    assert TERM.exists(), "web/terminal.js does not exist"
    return TERM.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(term):
    return _strip_comments(term)


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


# -- AC-767: xterm from web/vendor only ------------------------------------- #

def test_xterm_is_loaded_only_from_the_vendor_folder(code, index):
    assert 'const VENDOR = "vendor/xterm/"' in code
    assert not re.search(r"https?://", code), "terminal.js names a URL"
    assert not re.search(r"""(?:src|href)=["']https?://[^"']*xterm""", index)
    srcs = re.findall(r"\.(?:src|href)\s*=\s*([^;]+);", code)
    assert srcs and all(s.strip().startswith("VENDOR") for s in srcs), srcs


def test_the_fetch_script_pins_and_verifies(code):
    src = FETCH.read_text(encoding="utf-8")
    assert "registry.npmjs.org" in src
    assert "integrity" in src and "shasum" in src
    assert "web" in src and "vendor" in src and "xterm" in src
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"(?m)^web/vendor/", gi), "web/vendor/ is not gitignored"


# -- the markup sinks and ASCII --------------------------------------------- #

def test_nothing_in_the_terminal_module_parses_markup(code):
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "DOMParser", "createContextualFragment"):
        assert sink not in code, f"terminal.js uses {sink}"


def test_no_browser_storage(code):
    assert "localStorage" not in code and "sessionStorage" not in code


def test_the_module_and_fetch_script_are_ascii(term):
    for text in (term, FETCH.read_text(encoding="utf-8")):
        text.encode("ascii")
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text)


# -- AC-765: dictation goes to the terminal, with no Enter ------------------- #

def test_dictation_sends_typed_text_with_no_newline(code):
    body = _fn_body(code, "dictate")
    assert '"input"' in body, "dictation does not send an input frame"
    assert re.search(r"replace\(/\[\\r\\n\]\+/g", body), (
        "line breaks inside the words are not removed")
    assert "\\r" not in re.sub(r"replace\(/\[\\r\\n\]\+/g", "", body), (
        "dictate appends a carriage return")
    assert '+ "\\n"' not in body and '+ "\\r"' not in body
    assert "targeted" in body, "dictate ignores which surface has focus"


def test_the_composer_takes_dictation_when_no_terminal_is_focused(app):
    body = _fn_body(app, "endDictation")
    assert re.search(r"if \(!terminals\.dictate\(text\)\) insertIntoComposer\(text\);",
                     body), "endDictation does not route through terminals.dictate"


def test_focusing_the_composer_clears_the_terminal_target(code):
    assert re.search(r'env\.composer\(\)\.addEventListener\("focus",\s*\(\)\s*=>\s*'
                     r'\{\s*targeted = false', code)


# -- AC-766: nothing a terminal prints is spoken ----------------------------- #

def test_terminal_output_never_reaches_speech(code):
    for word in ("speak", "tts", "Tts", "playChunk", "audio", "Audio", "say("):
        assert word not in code, f"terminal.js touches speech: {word}"


# -- the phone key row ------------------------------------------------------- #

def test_the_phone_key_row_exists(code, index):
    assert 'id="termKeys"' in index
    for key in ("Esc", "Tab", "Ctrl", "Left", "Down", "Up", "Right", "Enter"):
        assert f'"{key}"' in code, f"the key row has no {key}"
    assert '"aria-pressed"' in code or "aria-pressed" in code, "Ctrl is not sticky"
    css = re.sub(r"/\*.*?\*/", "", index, flags=re.S)
    assert re.search(r"@media \(max-width: 900px\)\s*\{\s*\.termkeys\s*\{\s*display:\s*flex",
                     css), "the key row is not drawn at phone width"


def test_keys_in_the_view_never_reach_the_global_escape_stop(code):
    assert re.search(r'termView"\)\.addEventListener\("keydown",\s*\(e\)\s*=>\s*'
                     r'e\.stopPropagation\(\)', code)


# -- the wiring -------------------------------------------------------------- #

def test_the_view_the_sidebar_and_the_list_are_wired(app, index, code):
    for ident in ("termView", "termHost", "termEnd", "termClose", "termTitle"):
        assert f'id="{ident}"' in index, f"#{ident} is missing"
    assert "terminals.rowsFor(row)" in _fn_body(app, "projectNode")
    assert "terminals.hide()" in _fn_body(app, "openChat")
    assert "terminals.hide()" in _fn_body(app, "newConversation")
    assert "terminals.refresh()" in _fn_body(app, "goListen")
    assert '"/api/terminals"' in code and "/ws/terminal/" in code
    assert 'api("DELETE"' in code


def test_the_socket_frames_are_the_daemons(code):
    for frame in ('"replay"', '"output"', '"exit"', '"input"', '"resize"'):
        assert frame in code, f"terminal.js never handles {frame}"


# -- SW-7, SW-8, AC-797: a terminal is a shell, opened on one tap -------------- #

def test_open_terminal_is_one_tap_with_no_agent_picker(code, index):
    assert "pickFor" not in code and "agents" not in code
    assert "pterm-pick" not in code and "pterm-pick" not in index
    body = _fn_body(code, "launch")
    post = re.search(r'api\("POST", "/api/terminals",\s*(\{[^}]*\})', body)
    assert post, "launch does not POST /api/terminals"
    assert "agent" not in post.group(1), post.group(1)
    assert "launch(row, open)" in _fn_body(code, "rowsFor")


def test_the_feature_hides_when_the_list_is_403_or_404(code):
    body = _fn_body(code, "refresh")
    assert re.search(r"res\.status === 403 \|\| res\.status === 404", body)
    assert "hidden = true" in body
    assert "if (hidden) return [];" in _fn_body(code, "rowsFor")
