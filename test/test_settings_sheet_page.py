"""The settings SHEET (docs/lantern-ui.md 5.1, CU-11, AC-700..AC-705).

GREP GATES, `test_file_explorer_page.py`'s shape and reason: nothing in this
suite executes the page's JavaScript. What they pin is the part a browser run
cannot see by looking -- that the sheet is DRAWN FROM THE PAYLOAD (the nav from
the served `sections`, the strip from the served stages, the style cards from
the served `preview` flag) and that `settings.js` writes down no group or
setting of its own (AC-41). The pixels are proved by `probe_ux.py`,
`probe_page.py` and screenshots against a scratch daemon.

Supersedes the one-page surface's criteria (docs/settings-page.md): AC-83 (one
scroll) by AC-700 (one category page at a time), AC-84 (the scroll-spy rail)
by AC-701 (the signal-path strip), AC-88 (the phone strip) by AC-705 (list,
page, back).
"""
import re
from pathlib import Path

import pytest

from daemon.settings import GROUPS, SCHEMA
from test_file_explorer_page import _fn_body, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "web" / "settings.js"
APP = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"

#: The ONE setting key settings.js still spells, and why. Favourites are one
#: shared list that every model picker reads and the star writes; the schema
#: says only WHETHER a picker has the star (`favourites: true`), not which
#: setting holds the list. Naming it here keeps the debt visible: the fix is
#: the daemon serving the list's key on the spec, and then this goes.
KNOWN_LITERALS = {"model_favourites"}


@pytest.fixture(scope="module")
def src():
    return SETTINGS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(src):
    return _strip_comments(src)


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


# -- AC-41: the page names no group and no setting --------------------------- #

def test_settings_js_spells_no_group_or_setting_key(code):
    """AC-41 / AC-89. The nav, the strip, the cards and the search are all
    built from the payload, so no group key and no setting key may appear as
    a string literal in settings.js. A new category or a renamed setting then
    needs no edit in `web/`."""
    literals = set(re.findall(r'"([A-Za-z0-9_]+)"', code))
    literals |= set(re.findall(r"'([A-Za-z0-9_]+)'", code))
    named = sorted((literals & (set(GROUPS) | {s.key for s in SCHEMA}))
                   - KNOWN_LITERALS)
    assert named == [], f"settings.js names {named} (AC-41)"


def test_the_literal_gate_would_catch_one():
    """D-13: the gate above is an instrument; it must see a planted name."""
    planted = _strip_comments('const x = "brain"; // "voice" in a comment\n')
    literals = set(re.findall(r'"([A-Za-z0-9_]+)"', planted))
    assert literals & set(GROUPS) == {"brain"}


# -- AC-700: the nav is the served sections, the groups in served order ----- #

def test_the_nav_is_built_from_the_served_sections(code):
    """AC-700. One heading per served section, in served order, and every
    group filed under the section it names. The page invents no category."""
    body = _strip_comments(_fn_body(code, "buildNav"))
    assert "next.sections" in code, "the nav does not read the served sections"
    assert re.search(r"for \(const sec of sections\)", body), (
        "the nav is not one pass over the served sections")
    assert re.search(r"\.section === sec\.key", body), (
        "groups are not filed by their served `section`")


def test_the_carve_outs_say_their_own_section(index, code):
    """AC-41's carve-outs: the permission mode and connections blocks are the
    only things the page names, so THEIR markup says which served section they
    sit in, and settings.js reads it rather than naming a section."""
    assert re.search(r'id="modeRoot"[^>]*data-section="safety"', index)
    assert re.search(r'id="connRoot"[^>]*data-section="connected"', index)
    assert "dataset.section" in code
    for word in ("safety", "connected", "voice"):
        assert '"' + word + '"' not in code, f"settings.js names section {word}"


def test_one_category_page_at_a_time(code):
    """AC-700 supersedes AC-83: every page but the one shown is hidden, and
    the nav marks it with aria-current="page"."""
    body = _strip_comments(_fn_body(code, "showPage"))
    assert "hidden = " in body
    assert 'setAttribute("aria-current"' in body and '"page"' in body


# -- AC-701: the signal-path strip replaces the scroll-spy rail -------------- #

def test_the_strip_is_the_staged_groups_and_navigates(code):
    """AC-701 supersedes AC-84: no scroll-spy, a strip of the groups the
    daemon gave a `stage`, each pressing to its page."""
    assert "IntersectionObserver" not in code, "the scroll-spy rail is back"
    body = _strip_comments(_fn_body(code, "buildStrip"))
    assert re.search(r"stage != null", body), "the strip is not the staged groups"
    assert "showPage(" in body, "a strip stop does not open its page"
    assert '"step"' in _strip_comments(_fn_body(code, "showPage")), (
        "the strip does not mark the page shown")


# -- AC-702: search across categories ---------------------------------------- #

def test_search_reaches_every_category_and_says_nothing_matched(code):
    """AC-702 (AC-85's rules across pages): rows match the schema-built hay,
    folds open while querying, matches counted per category, and a miss is
    said."""
    body = _strip_comments(_fn_body(code, "applySearch"))
    assert "dataset.hay" in body
    assert "fold.open = true" in body
    assert "noMatch.hidden" in body
    assert "count" in body


# -- AC-703: the restart bar is docked in the sheet --------------------------- #

def test_the_restart_bar_is_docked_inside_the_sheet(index):
    """AC-703: the bar sits in the sheet's page pane, not over the page.
    AC-715: it asks nobody to press anything -- the daemon restarts by
    itself once idle -- so it carries no button."""
    panel = index[index.index('<aside id="panel"'):]
    panel = panel[:panel.index("</aside>")]
    assert 'id="commitBar"' in panel
    bar = panel[panel.index('id="commitBar"'):]
    bar = bar[:bar.index("</div>")]
    assert "<button" not in bar, "the restart bar asks the owner to restart"
    assert "restarts when idle" in bar


def test_the_restart_bar_counts_what_the_DAEMON_says_is_pending(code, app):
    """AC-716. The bar's list is the daemon's `pending`, from the full read
    and from every `settings` frame; a page-side baseline missed any change
    made before the page loaded, and never cleared after a restart it did
    not request."""
    assert "baseline" not in code, "the page keeps its own restart baseline"
    assert "pending = next.pending" in _strip_comments(_fn_body(code, "apply"))
    changed = _strip_comments(_fn_body(code, "applyChanged"))
    assert "pending = servedPending" in changed
    assert "for (const key of pending)" in _strip_comments(
        _fn_body(code, "pendingRestart"))
    assert "settings.applyChanged(m.changed || {}, m.pending)" in app


# -- AC-704: style cards keyed on the served flag ----------------------------- #

def test_style_cards_are_drawn_from_the_preview_flag(code):
    """AC-704. Preview cards are the `preview` flag's, never a setting name;
    the miniature wears the choice's own value as its `data-style`."""
    body = _strip_comments(_fn_body(code, "buildChoice"))
    assert re.search(r"if \(spec\.preview\)\s*return buildPreviewCards\(", body)
    cards = _strip_comments(_fn_body(code, "buildPreviewCards"))
    assert "dataset.style = " in cards
    for word in ("lantern", "caption"):
        assert word not in code.lower(), f"settings.js names the style {word}"


# -- AC-705: a phone is a list, a page and back ------------------------------- #

def test_the_phone_back_stack_is_written_by_one_function(code, index):
    """AC-705: `data-phone` is "list" or "page", written in one place."""
    writes = re.findall(r"dataset\.phone\s*=", code)
    assert len(writes) == 1, f"data-phone is written {len(writes)} times"
    assert 'id="setBack"' in index


# -- the old top bar's buttons have homes ------------------------------------- #

def test_the_old_top_bar_is_gone_and_its_jobs_have_homes(index, app):
    """Restart is the account menu's and the restart bar's; New chat is the
    sidebar's; reset all sits at the nav's foot."""
    for gone in ('id="restartDaemon"', 'id="newSession"'):
        assert gone not in index, gone
    for gone in ('$("restartDaemon")', '$("newSession")'):
        assert gone not in app, gone
    nav = index[index.index('class="snav"'):]
    nav = nav[:nav.index('class="spane"')]
    assert 'id="resetAll"' in nav and 'id="setSearch"' in nav
    assert 'id="restartBtn"' in index and 'id="newChat"' in index


# -- the served default is said in words, never a bare mark ----------------- #

def test_a_select_option_says_default_in_words(code):
    """Owner 2026-10-29: `patient *` read as nothing. The option that is the
    schema's `default` reads "<label> (default)", keyed on `spec.default`,
    never on a setting's name (AC-41)."""
    body = _strip_comments(_fn_body(code, "buildChoice"))
    assert '" *"' not in body, "the bare star marker is back"
    assert "same(c, spec.default)" in body
    assert '" (default)"' in body, "a select option does not say (default)"


def test_a_segmented_default_carries_a_visible_word(code, index):
    """The trailing dot on a segmented button meant nothing to anyone. The
    default button carries a small, quieter "default" caption instead -- a
    VISIBLE word, not only a screen-reader one -- styled under `#panel`."""
    body = _strip_comments(_fn_body(code, "buildChoiceButtons"))
    assert "defdot" not in body, "the default dot is back"
    assert re.search(r'el\("span",\s*"segdef",\s*"default"\)', body), (
        "the default button carries no visible word")
    css = _strip_comments(index)
    assert ".defdot" not in css
    assert re.search(r"#panel \.segdef\s*\{", css), "the caption is unstyled"


# -- a carve-out that gates nothing says so before it is opened -------------- #

def test_a_carve_out_tag_comes_from_its_own_markup(code, index):
    """The permission mode sits under a safety heading but gates nothing yet.
    Its nav row says so from the block's OWN markup (`data-tag`), the way it
    says its section, so settings.js holds no words of its own for it."""
    m = re.search(r'<div[^>]*id="modeRoot"[^>]*>', index)
    assert m and 'data-tag="Not active yet"' in m.group(0)
    assert "dataset.tag" in code
    assert "Not active yet" not in code, "settings.js spells the tag itself"
    nav = _strip_comments(_fn_body(code, "navItem"))
    assert '"ntag"' in nav, "the nav row does not draw the tag"
    assert re.search(r"#panel \.ntag\s*\{", _strip_comments(index))


def test_the_permission_page_leads_with_gating_nothing(index):
    """Its page's lede (the first `.mode-note`) is the admission, so nobody
    reads the controls as live before reading that they are not."""
    block = index[index.index('id="modeRoot"'):]
    block = re.sub(r"<!--.*?-->", "", block[:block.index('id="modeSeg"')], flags=re.S)
    first = re.search(r'<p class="mode-note">(.*?)</p>', block, flags=re.S)
    assert first and "gate nothing" in first.group(1), "the lede is not the admission"


# -- the phone strip keeps every stage on screen ----------------------------- #

def test_the_phone_strip_wraps_instead_of_running_off_the_edge(index):
    """At 390 px six stops ran off the right edge with no cue. Under the
    phone breakpoint the stops wrap into rows of three, `minmax(0, 1fr)`,
    and nothing scrolls sideways."""
    css = _strip_comments(index)
    rules = re.findall(r"#panel \.stops\s*\{([^}]*)\}", css)
    assert any("repeat(3, minmax(0, 1fr))" in r for r in rules), rules
    assert not any("overflow-x" in r for r in rules), "the strip still scrolls sideways"
