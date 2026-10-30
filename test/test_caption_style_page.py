"""Caption, the second style of the same DOM (docs/lantern-ui.md Step 4,
AC-679, AC-681, AC-689 for Caption, AC-691, CU-1, CU-3).

GREP GATES over `web/`, in the shape of `test_lantern_composer_page.py`:
nothing here runs `app.js` or `visualiser.js`. The browser half (the id set
and Tab order under both styles, the live words in the caption during a real
capture, the band drawn in the seam) is `e2e_scenarios.py` section N and
`probe_page.py`'s style-aware home check.
"""
import re
from pathlib import Path

import pytest

from test_lantern_composer_page import _code, _rule
from test_lantern_shell_page import _element, _fn_body, _ids

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"
VIZ = ROOT / "web" / "visualiser.js"

FOCUSABLE = re.compile(r"<(button|a|input|textarea|select)\b|\btabindex=")
MOVES = r"\.(?:append|prepend|before|after|appendChild|insertBefore|replaceWith)\("


@pytest.fixture(scope="module")
def app():
    return _code(APP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def viz():
    return _code(VIZ.read_text(encoding="utf-8"))


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


def _rules(css: str):
    """(selector, body) for every plain rule, media blocks flattened."""
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


# -- AC-679: a style moves no behaviour -------------------------------------- #

def test_one_dom_every_id_once(markup):
    ids = re.findall(r'\bid="([^"]+)"', markup)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"an element is drawn twice (one per style?): {dup}"


def test_app_js_names_no_style_and_branches_on_none(app):
    for word in ('"caption"', "'caption'", '"lantern"', "'lantern'"):
        assert word not in app, f"app.js names a style value {word} (AC-41, CU-1)"
    reads = re.findall(r"dataset\.style\b[^=\n]*", app)
    writes = re.findall(r"dataset\.style = values\.ui_style;", app)
    assert len(writes) == 1 and len(reads) == 1, (
        "app.js may WRITE data-style once and never read it to branch: "
        f"{reads}")


def test_what_a_style_hides_is_never_focusable(css, markup):
    """CSS hides, never duplicates -- and a hidden element that held a control
    would change the Tab order between the styles (AC-679)."""
    # Any id whose display a style decides, hiding OR showing: `#captionSlot`
    # is hidden by its own rule and shown only by Caption's.
    hidden = set()
    for selector, body in _rules(css):
        if "[data-style=" in selector and re.search(r"\bdisplay:", body):
            hidden |= set(re.findall(r"#([A-Za-z][\w-]*)", selector))
    assert {"captionSlot", "waveSeam", "inState"} <= hidden, (
        f"the selector scan missed a style-drawn element: {sorted(hidden)}")
    for el in sorted(hidden):
        if f'id="{el}"' in markup:
            assert not FOCUSABLE.search(_element(markup, el)), (
                f"#{el} is hidden by one style and holds a control")


# -- AC-681: a moved element is moved ---------------------------------------- #

@pytest.mark.parametrize("el", ["liveLine", "wave"])
def test_the_moved_elements_exist_once_and_move_in_one_place(app, markup, el):
    assert markup.count(f'id="{el}"') == 1
    moves = [m.start() for m in re.finditer(MOVES + r'\$\("' + el + r'"\)\)', app)]
    body = _fn_body(app, "placeByStyle")
    start = app.index(body)
    assert len(moves) == 1 and start <= moves[0] < start + len(body), (
        f"placeByStyle must be the one place that re-parents #{el} "
        f"({len(moves)} move(s))")


def test_the_slots_are_the_stylesheets_to_name(app, css):
    body = _fn_body(app, "placeByStyle")
    assert "--live-slot" in body and "--wave-slot" in body, body
    assert "VIZ_SLOT[vizPlacement]" in body, "the placement setting is one input"
    for caller in ("applyPageValues", "applyVizPlacement", "setVoiceEffect"):
        assert "placeByStyle()" in _fn_body(app, caller), caller
    assert re.search(r'\[data-style="caption"\]\s*\{[^}]*--live-slot:\s*captionSlot', css)
    assert re.search(r'\[data-voicefx="lantern"\]\s*\{[^}]*--wave-slot:\s*waveSeam', css)
    assert re.search(r'\[data-voicefx\]\s*\{[^}]*--wave-slot:\s*waveFx', css), (
        "every other treatment keeps the canvas in #waveFx")


# -- AC-689 (Caption): the live words above the box --------------------------- #

def test_the_caption_slot_sits_above_the_box(markup):
    dock = _element(markup, "dock")
    order = re.findall(r'\bid="(captionSlot|errStrip|askCard|composer)"', dock)
    assert order == ["captionSlot", "errStrip", "askCard", "composer"], order
    cap = _element(markup, "captionSlot")
    assert re.search(r">\s*Hearing you\s*<", cap), "the board's label"
    assert not FOCUSABLE.search(cap)


def test_the_caption_is_large_centred_and_legible(css):
    rule = _rule(css, "#captionSlot #liveLine")
    assert "calc(var(--scale) * 25px)" in rule and "center" in rule, rule
    assert re.search(r"#liveLine \.tentative\s*\{[^}]*var\(--muted\)", css), (
        "tentative stays --muted (Lc >= 60), never --faint")
    shown = re.search(r'\[data-style="caption"\]:is\(\[data-state="capturing"\],\s*'
                      r'\[data-state="closing"\]\)\s*#captionSlot\s*\{([^}]*)\}', css)
    assert shown and "flex" in shown.group(1), "the caption shows only while hearing"
    assert re.search(r"#captionSlot\s*\{[^}]*display:\s*none", css), (
        "Lantern (and every other state) never shows it")


def test_caption_keeps_no_word_and_no_glow_in_the_box(css):
    assert re.search(r'\[data-style="caption"\] #inState\s*\{[^}]*display:\s*none', css)
    assert re.search(r'\[data-style="caption"\]\[data-voicefx="lantern"\] '
                     r'#composerFx \.fxlantern\s*\{[^}]*display:\s*none', css)
    # The placeholder hides only when the live words share its cell.
    assert re.search(r"#liveSlot:has\(> #liveLine\) > textarea::placeholder", css)


# -- AC-691: the seam is the voice ------------------------------------------- #

def test_the_seam_is_the_boxs_top_edge(markup, css):
    composer = _element(markup, "composer")
    first = re.search(r"<form\b[^>]*>\s*<(\w+)\b[^>]*\bid=\"([^\"]+)\"", composer)
    assert first and first.group(2) == "waveSeam", "the seam is the box's first child"
    assert 'aria-hidden="true"' in _element(markup, "waveSeam")
    assert re.search(r"#waveSeam\s*\{[^}]*display:\s*none", css)
    shown = re.search(r'\[data-style="caption"\]\[data-voicefx="lantern"\]:is\('
                      r'\[data-state="capturing"\],\s*\[data-state="closing"\],\s*'
                      r'\[data-state="speaking"\]\)\s*#waveSeam\s*\{([^}]*)\}', css)
    assert shown and "block" in shown.group(1), "shown while hearing or speaking"
    seam = _rule(css, "#waveSeam")
    w = re.search(r"width:\s*[^;]*?(\d+)px", seam)
    h = re.search(r"height:\s*(\d+)px", seam)
    assert w and h and int(w.group(1)) >= 2 * int(h.group(1)), (
        "the seam must be WIDE: visualiser.js draws a band only when w >= 2h")


def test_the_visualiser_draws_its_shape_for_the_style_voice(viz):
    assert re.search(r"var fxShape = effect === 'lantern';", viz)
    assert "if (!treatment || fxShape)" in viz, (
        "lantern's canvas draws the band (or ring) the slot's shape asks for")
    assert re.search(r"io\.observe\(fxEl\)", viz), (
        "a canvas hidden by the style must not freeze the level the border reads")


def test_reduced_motion_freezes_the_seam_into_still_bars(css):
    blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n  \}",
                        css, re.S)
    seam = [b for b in blocks if "#waveSeam" in b]
    assert seam, "no reduced-motion rule for the seam"
    assert re.search(r"#waveSeam canvas\s*\{[^}]*display:\s*none", seam[0])
    bars = re.search(r"#waveSeam::before\s*\{([^}]*)\}", seam[0])
    assert bars, "the seam must keep still bars, not vanish (AC-259)"
    assert "var(--lit)" in bars.group(1) and "mask" in bars.group(1), bars.group(1)
    assert "animation" not in bars.group(1)
