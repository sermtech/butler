"""The Lantern and Caption token blocks (docs/lantern-ui.md section 6, Step 2).

AC-682: the token names and values of the approved export appear verbatim in
the page's style blocks, except the four dark-theme greys CU-17 lifts.
AC-680: each style declares --blue, --amber and --red in both themes, and each
meets WCAG 3:1 against that style-theme's --bg and --raised.

The EXPORT table below is the export's own text (`.bt`, `.dir-a`, `.dir-b`
and their `.dark` / `.light` blocks), written once here because the export is
a design file, not something the page ships. The page keys a style with
`[data-style="<name>"]` and a theme with `[data-theme="<theme>"]`; since Step
3a `app.js` writes `data-style` from the `ui_style` setting and the style
blocks are the page's only palette.

Step 3a adds the switches' page half: every style draws every theme the
daemon offers, `auto` included (AC-698); Butler's replies are set in the
style's reading face (AC-697); the wave's colours are read from the style
rather than restated (CU-4).
"""
import re
from pathlib import Path

import pytest

from daemon.settings import BY_KEY
from test_seams import _stylesheet

WEB = Path(__file__).resolve().parents[1] / "web"

STYLES = ("lantern", "caption")
THEMES = ("dark", "light")

# The export's shared motion and mono (`.bt`).
SHARED = {"--ease": "cubic-bezier(.2,.8,.2,1)", "--dur": "320ms",
          "--mono": "'JetBrains Mono',ui-monospace,Consolas,monospace"}

# Per style (`.dir-a` = Lantern, `.dir-b` = Caption).
TYPE = {
    "lantern": {"--ui": "'Figtree',system-ui,sans-serif",
                "--display": "'Iowan Old Style','Charter',Georgia,serif",
                "--read": "'Iowan Old Style','Charter',Georgia,serif",
                "--readsize": "17px", "--rd": "22px"},
    "caption": {"--ui": "'Inter',system-ui,sans-serif",
                "--display": "'Inter',system-ui,sans-serif",
                "--read": "'Inter',system-ui,sans-serif",
                "--readsize": "16px", "--rd": "14px"},
}

# Per style and theme (`.dir-a.dark` ...), the export's values as exported.
COLOUR = {
    ("lantern", "dark"): {"--bg": "#1c1b19", "--side": "#161513", "--raised": "#262522",
                          "--tone": "#32302c", "--pop": "#2b2a27", "--fg": "#eeebe5",
                          "--muted": "#aaa59b", "--faint": "#7a756c", "--blue": "#8cbdf2",
                          "--amber": "#e8b964", "--red": "#f2877c"},
    ("lantern", "light"): {"--bg": "#f8f6f1", "--side": "#eeeae2", "--raised": "#ffffff",
                           "--tone": "#e3ded4", "--pop": "#ffffff", "--fg": "#1f1d1a",
                           "--muted": "#5f5a51", "--faint": "#8a8479", "--blue": "#2c6ab3",
                           "--amber": "#93600c", "--red": "#b3372c"},
    ("caption", "dark"): {"--bg": "#0f1114", "--side": "#15181c", "--raised": "#1b1f24",
                          "--tone": "#262b32", "--pop": "#20252b", "--fg": "#e7eaee",
                          "--muted": "#98a0ab", "--faint": "#6a7381", "--blue": "#78abff",
                          "--amber": "#f0bb52", "--red": "#ff7b72"},
    ("caption", "light"): {"--bg": "#fbfcfd", "--side": "#f0f2f5", "--raised": "#ffffff",
                           "--tone": "#e2e6eb", "--pop": "#ffffff", "--fg": "#14171b",
                           "--muted": "#566070", "--faint": "#8b94a1", "--blue": "#1d5fcc",
                           "--amber": "#8c5d00", "--red": "#c2352b"},
}

# CU-17 (owner, 2026-10-26): the four dark greys the page lifts over the export.
CU17 = {("lantern", "dark"): {"--muted": "#cbc7bf", "--faint": "#9f9b93"},
        ("caption", "dark"): {"--muted": "#c1c6ce", "--faint": "#939aa5"}}

STATE_COLOURS = ("--blue", "--amber", "--red")


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _tokens(css: str, style: str, theme: str) -> dict:
    """What the style blocks declare for one style under one theme.

    A selector counts when it names `data-style="<style>"` and either names
    `data-theme="<theme>"` or no theme at all; a later rule wins, as in the
    cascade (every selector here is attributes only, so order is what decides
    between two that both match)."""
    found = {}
    for sels, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css):
        for sel in sels.split(","):
            if f'[data-style="{style}"]' not in sel:
                continue
            themed = re.search(r'\[data-theme="(\w+)"\]', sel)
            if themed and themed.group(1) != theme:
                continue
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
                found[name] = _norm(value)
    return found


def _expected(style: str, theme: str) -> dict:
    want = {**SHARED, **TYPE[style], **COLOUR[(style, theme)],
            **CU17.get((style, theme), {})}
    return {k: _norm(v) for k, v in want.items()}


def _luminance(hex_colour: str) -> float:
    m = re.fullmatch(r"#([0-9a-f]{6})", hex_colour)
    assert m, f"{hex_colour!r} is not a #rrggbb colour; this test reads hex only"
    channels = [int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("theme", THEMES)
def test_each_style_carries_the_exports_tokens_verbatim(style, theme):
    """AC-682. Every export token, by name and value, in the page's block for
    this style and theme; the CU-17 greys are the only stated exception. A
    renamed token, a retyped value or a lost CU-17 lift fails here."""
    declared = _tokens(_stylesheet(), style, theme)
    want = _expected(style, theme)
    wrong = {k: (declared.get(k), v) for k, v in want.items() if declared.get(k) != v}
    assert not wrong, (
        f"{style}/{theme}: token -> (page, export) differs or is missing: {wrong}")


def test_the_cu17_greys_differ_from_the_export():
    """CU-17 is an exception only while it is a different value: if a lift
    were set back to the export's grey, the exception table would be hiding
    nothing and the readability ruling would be gone."""
    for key, lifted in CU17.items():
        for name, value in lifted.items():
            assert _norm(value) != _norm(COLOUR[key][name]), (key, name)


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("theme", THEMES)
def test_each_style_keeps_its_state_colours_legible(style, theme):
    """AC-680. The three state colours are declared per style and theme and
    each holds WCAG 3:1 against --bg and --raised, computed from what the PAGE
    declares, not from the table above."""
    declared = _tokens(_stylesheet(), style, theme)
    missing = [n for n in ("--bg", "--raised", *STATE_COLOURS) if n not in declared]
    assert not missing, f"{style}/{theme} declares no {missing}"
    weak = {(c, ground): round(_contrast(declared[c], declared[ground]), 2)
            for c in STATE_COLOURS for ground in ("--bg", "--raised")
            if _contrast(declared[c], declared[ground]) < 3.0}
    assert not weak, f"{style}/{theme}: below 3:1 -> {weak}"


def test_the_contrast_instrument_reads_known_pairs():
    """The instrument, checked against WCAG's own fixed points: black on white
    is 21:1, a colour on itself is 1:1, and #767676 on white is the textbook
    4.54:1 -- so a broken formula cannot pass every pair above by accident."""
    assert round(_contrast("#000000", "#ffffff"), 2) == 21.0
    assert _contrast("#8cbdf2", "#8cbdf2") == 1.0
    assert round(_contrast("#767676", "#ffffff"), 2) == 4.54


# --------------------------------------------------------------------------- #
# Step 3a: the switches (AC-678, AC-697, AC-698, CU-4, CU-5)
# --------------------------------------------------------------------------- #
def _split_media(css: str) -> tuple[str, list[tuple[str, str]]]:
    """The stylesheet outside every `@media` block, and each block's
    `(condition, inner css)`. Brace-balanced, because the inner rules have
    braces of their own."""
    outside, blocks, i = [], [], 0
    for m in re.finditer(r"@media([^{]*)\{", css):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        outside.append(css[i:m.start()])
        blocks.append((m.group(1).strip(), css[m.end():j - 1]))
        i = j
    outside.append(css[i:])
    return "".join(outside), blocks


def _auto_tokens(css: str, style: str, scheme: str) -> dict:
    """What `theme: auto` draws for one style while the device is in
    `scheme`: the rules outside any media query, then whatever a
    `prefers-color-scheme: <scheme>` block adds on top."""
    outside, blocks = _split_media(css)
    found = _tokens(outside, style, "auto")
    for cond, inner in blocks:
        if re.search(rf"prefers-color-scheme\s*:\s*{scheme}", cond):
            found.update(_tokens(inner, style, "auto"))
    return found


def test_the_page_starts_in_the_default_style():
    """AC-678. `<html>` carries the DEFAULT style from the first byte, so no
    frame is drawn in a style nobody chose before the daemon answers. The
    default is read off the schema, not restated. The retired schemes'
    attribute is gone with them (CU-4)."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    tag = re.search(r"<html\b[^>]*>", html).group(0)
    want = BY_KEY["ui_style"].default
    assert f'data-style="{want}"' in tag, tag
    assert "data-palette" not in tag + _stylesheet(), (
        "a retired scheme attribute survives")


@pytest.mark.parametrize("style", STYLES)
def test_every_style_draws_every_theme_the_daemon_offers(style):
    """AC-678, AC-698. Every style the daemon offers has a palette under
    every theme it offers, `auto` included: under `auto` with the device
    dark the page is EXACTLY the dark palette, and with the device light
    EXACTLY the light one. `auto` has to restate the light palette inside
    a media query (CSS cannot key a selector on the device's scheme), so
    this is the test that keeps that copy from drifting."""
    assert set(BY_KEY["ui_style"].choices) == set(STYLES)
    assert set(BY_KEY["theme"].choices) == {"dark", "light", "auto"}
    css = _stylesheet()
    outside, _ = _split_media(css)
    for scheme in ("dark", "light"):
        explicit = _tokens(outside, style, scheme)
        assert "--bg" in explicit, f"{style} has no --bg in the {scheme} theme"
        auto = _auto_tokens(css, style, scheme)
        assert auto == explicit, (
            f"{style}: theme auto on a {scheme} device differs from the "
            f"{scheme} theme: "
            f"{ {k: (auto.get(k), v) for k, v in explicit.items() if auto.get(k) != v} }")


def test_auto_tells_the_browser_which_way_it_is_lit():
    """CU-6, AC-698: `color-scheme` follows the RESOLVED theme, or a light
    device under `auto` keeps dark scrollbars and selects. Dark is the root
    default, so only the light half needs a rule."""
    _, blocks = _split_media(_stylesheet())
    lit = [inner for cond, inner in blocks
           if re.search(r"prefers-color-scheme\s*:\s*light", cond)]
    assert any(re.search(r'\[data-theme="auto"\][^{]*\{[^}]*color-scheme\s*:\s*light',
                         inner) for inner in lit), (
        "no prefers-color-scheme: light rule tells the browser an auto page "
        "is light")


def test_butlers_replies_are_set_in_the_styles_reading_face():
    """AC-697, CU-7. `.answered` is set in `--read` at `--readsize`, still
    multiplied by `--scale` (R7); the interface stays in `--ui`."""
    css = _stylesheet()
    body = "".join(b for sel, b in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
                   if re.fullmatch(r"\s*\.answered\s*", sel))
    assert re.search(r"font-family\s*:\s*var\(--read\)", body), body
    assert re.search(
        r"font-size\s*:\s*calc\(var\(--scale\)\s*\*\s*var\(--readsize\)\)",
        body), body
    assert re.search(r"(?m)^\s*body\s*\{[^}]*font-family\s*:\s*var\(--ui\)",
                     css), "the page body is not set in --ui"


def test_the_reading_face_overrides_the_replies_and_style_default_clears_it():
    """CU-5. The setting writes `--read` (Butler's replies), never `--ui`,
    and its "" ("Style default") REMOVES the override so the style's face
    stands. The page still names no face (AC-41)."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert re.search(r'setProperty\(\s*"--read"', app), "nothing writes --read"
    assert re.search(r'removeProperty\(\s*"--read"', app), (
        "Style default does not clear the override")
    assert not re.search(r'setProperty\(\s*"--ui"', app), (
        "the reading face still overwrites the interface face")


def test_the_wave_reads_its_colours_from_the_style():
    """CU-4. The `[data-state]` rows and the canvas palette were one decision
    written twice (a CSS ramp and an rgba table in `app.js`), kept in step by
    hand. Now `app.js` reads the style's own `--blue`, `--amber` and `--red`
    off the page and spells no colour of its own, so a retuned style cannot
    leave the wave behind."""
    app = re.sub(r"(?m)^\s*//.*$", "", (WEB / "app.js").read_text(encoding="utf-8"))
    literals = re.findall(r"rgba?\(\s*\d|#[0-9a-fA-F]{6}\b|okl(?:ch|ab)\(", app)
    assert literals == [], f"app.js spells colours: {literals[:5]}"
    for token in STATE_COLOURS:
        assert re.search(rf'getPropertyValue\(\s*"{token}"', app) or \
            f'"{token}"' in app, f"app.js never reads {token}"
