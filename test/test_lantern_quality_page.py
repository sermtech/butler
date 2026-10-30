"""Step 7, the quality floor (docs/lantern-ui.md AC-710, section 11): focus,
reduced motion, reduced transparency, more contrast, phone safe areas, the
skip link, the dimmed rows of inactive settings, and the question card's
agent name on a device that did not start the turn.

GREP GATES over `web/`, in the shape of `test_lantern_extras_page.py`:
nothing here runs the page. Each pin is the static half of a rendered check
run against a scratch daemon (`.scratch-ui/step7/`), where the numbers in the
comments were measured.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _code(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//[^\n\"']*$", "", source)


@pytest.fixture(scope="module")
def app():
    return _code((WEB / "app.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def css():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    sheet = index[index.index("<style>"):index.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)


def _media_blocks(css: str, query: str) -> list[str]:
    """Every `@media <query> { ... }` body, braces balanced."""
    out = []
    for m in re.finditer(r"@media\s*" + re.escape(query) + r"\s*\{", css):
        depth, start = 0, m.end() - 1
        for i in range(start, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[start + 1:i])
                    break
    assert out, f"no @media {query} block"
    return out


def _rule(css: str, selector: str) -> str:
    """The declarations of the LAST top-level-looking rule whose selector list
    is exactly `selector`."""
    hits = re.findall(r"(?:^|[}\s])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert hits, f"no rule for {selector!r}"
    return hits[-1]


def test_a_question_rereads_the_listing_so_every_device_names_the_agent(app):
    # MEASURED 2026-10-27 (.scratch-ui/step7/ask_two.py): on a fresh chat a
    # second device watching the turn drew "the coding agent is asking" while
    # the device that typed drew "pi is asking". The typed device's listing was
    # re-read on its own `final`; nothing re-read the watcher's, and the turn
    # cannot end while the question blocks it. The question frame is the moment
    # the daemon is sure to know the harness (the child was spawned to ask).
    body = re.search(r'case "question":(.*?)\n\s*case "', app, flags=re.S)
    assert body, 'no `case "question":` in the frame handler'
    assert "loadChats()" in body.group(1)


def test_the_composer_focus_ring_is_a_hairline_that_more_contrast_thickens(css):
    # MEASURED (.scratch-ui/step7/focus.py): the page-wide ring in `--sa` was
    # 2.48-2.99:1 on Caption's light ground; `--fg` is the export's ring.
    assert re.search(r"(?:^|\})\s*:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--fg\)", css)
    ring = _rule(css, ".composer:focus-within")
    width = re.search(r"outline:\s*([\d.]+)px", ring)
    assert width and float(width.group(1)) <= 1, ring
    more = "".join(_media_blocks(css, "(prefers-contrast: more)"))
    assert re.search(r"\.composer:focus-within\s*\{[^}]*outline-width:\s*2px", more)
    assert re.search(r":focus-visible\s*\{[^}]*outline-width:\s*3px", more)


def test_more_contrast_raises_the_lines_the_page_draws_with(css):
    more = "".join(_media_blocks(css, "(prefers-contrast: more)"))
    for token in ("--line", "--hairline", "--muted"):
        assert re.search(re.escape(token) + r"\s*:", more), token


def test_reduced_motion_leaves_only_colour_to_change(css):
    rm = "".join(_media_blocks(css, "(prefers-reduced-motion: reduce)"))
    prop = re.search(r"transition-property:\s*([^;!]+)", rm)
    assert prop, "reduced motion must name what may still transition"
    names = {p.strip() for p in prop.group(1).split(",")}
    assert "color" in names and "background-color" in names
    assert not names & {"all", "transform", "top", "left", "width", "height",
                        "translate", "inset"}, names
    assert re.search(r"scroll-behavior:\s*auto", rm)


def test_reduced_transparency_drops_every_blur(css):
    rt = "".join(_media_blocks(css, "(prefers-reduced-transparency: reduce)"))
    blurred = set()
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if re.search(r"backdrop-filter:\s*blur", m.group(2)):
            blurred.update(s.strip() for s in m.group(1).split(","))
    assert blurred
    for sel in blurred:
        assert re.search(re.escape(sel) + r"[^{]*\{[^}]*backdrop-filter:\s*none", rt), sel


def test_the_skip_link_casts_no_shadow_while_it_waits_off_screen(css):
    # MEASURED (Step 5 screenshots, light theme): the off-screen link's large
    # shadow bled onto the page's top-left corner.
    rule = _rule(css, ".skiplink")
    assert "box-shadow" not in rule, rule


def test_the_phone_settings_sheet_clears_the_home_indicator(css):
    # MEASURED (.scratch-ui/step7/safearea.py, insets 47 / 34 emulated):
    # "Reset all settings" sat at 786-830 of 844, inside the 34 px band.
    phone = "".join(_media_blocks(css, "(max-width: 900px)"))
    rule = re.search(r"#panel\s*\{([^}]*)\}", phone)
    assert rule and "safe-area-inset-bottom" in rule.group(1), rule


def test_dimmed_inactive_controls_stay_readable(css):
    # AC-710's exception for AC-66: a control dimmed ON PURPOSE may sit at
    # APCA Lc 45, not 60. MEASURED at opacity .55: Lc 31.
    rule = _rule(css, "#panel .set.inactive .set-ctl > :not(.fb)")
    o = re.search(r"opacity:\s*([\d.]+)", rule)
    assert o and float(o.group(1)) >= 0.7, rule
