"""The PAGE half of automations (docs/automations.md phases 5-6): the
Library's Automations tab, the automation editor and the full Tasks &
Automations page (AC-468).

GREP GATES, `test_library_page.py`'s shape and reason: nothing in this suite
executes the page's JavaScript, so a regression here is invisible to every
daemon-side test. The pixels were checked with `playwright-cli` on a scratch
daemon against `web/mocks/t-library.html`.
"""
import re
from pathlib import Path

import pytest

from test_library_page import _fn_body, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
LIB = ROOT / "web" / "library.js"
AUTO = ROOT / "web" / "automations.js"
INDEX = ROOT / "web" / "index.html"


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lib():
    return LIB.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def auto():
    return AUTO.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return INDEX.read_text(encoding="utf-8")


def test_nothing_parses_markup(auto):
    """Names, prompts, task text, summaries and reasons are untrusted."""
    code = _strip_comments(auto)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "DOMParser", "createContextualFragment"):
        assert sink not in code, f"automations.js uses {sink}"


def test_one_door_to_the_network_and_no_browser_storage(auto):
    code = _strip_comments(auto)
    assert code.count("fetch(") == 1
    for store in ("localStorage", "sessionStorage", "indexedDB",
                  "document.cookie"):
        assert store not in code, store                      # AC-8


def test_the_page_names_no_setting(auto, lib):
    """AC-41: the default channel is the DAEMON's (notify_default, read at
    save) and nothing here reads or writes a setting."""
    for code in (_strip_comments(auto), _strip_comments(lib)):
        assert "/api/settings" not in code
        assert "notify_default" not in code


def test_every_schedule_answer_is_the_daemons(auto):
    """AC-455: the page never parses a schedule. The next runs and "same as
    the cron" come from POST /api/schedule/preview, and the only cron text
    the page holds is what the owner typed or what the daemon answered."""
    code = _strip_comments(auto)
    assert '"/api/schedule/preview"' in code
    body = _fn_body(auto, "preview")
    assert "got.next" in body and "got.cron" in body
    assert ".split(\" \")" not in code and ".split(/\\s+/)" not in code


def test_every_element_the_script_names_is_in_the_markup(auto, index):
    code = _strip_comments(auto)
    ids = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', code))
    assert ids, "no element lookups found"
    # ...or built by the script itself (the preset line's own inputs).
    made = set(re.findall(r'\.id = "([A-Za-z0-9_]+)"', code))
    made |= set(re.findall(r'select\("([A-Za-z0-9_]+)"', code))
    for i in sorted(ids - made):
        assert f'id="{i}"' in index, f"automations.js names #{i}, not in index.html"


def test_the_library_draws_real_automations(lib):
    body = _fn_body(lib, "drawAutomations")
    assert "doc.automations" in body and "not built yet" not in lib
    assert "openEditor(" in body
    assert "openPage(" in _strip_comments(lib)


def test_starting_a_task_from_the_page_does_not_mark_it_done(auto):
    body = _fn_body(auto, "taskRow")
    start = body[body.index("start.onclick"):]
    assert "runInNewChat" in start and "done" not in start.split("};")[0]


def test_escape_order_with_automations(app):
    m = re.search(r'if \(e\.key === "Escape"\) \{(.*?)\n  \}', app, re.S)
    assert m
    block = m.group(1)
    # The automation editor closes in its own capture phase since Step 6a
    # (AC-706, CU-15, AC-709); the full page keeps its place.
    order = [block.index(s) for s in (
        "automations.isOpen()", "panelOpen()", "stopTurn()")]
    assert order == sorted(order), order


def test_the_page_is_reachable_from_the_footer_and_the_account_menu(app, index):
    """AC-468's two doors after lantern-ui section 2: the library's footer,
    and the account menu's "Tasks and automations"."""
    assert 'id="libraryFull"' in index
    menu = re.search(r'<div[^>]*id="acctPop".*?id="restartBtn"', index, re.S)
    assert menu and 'id="tasksPageBtn"' in menu.group(0), (
        "Tasks and automations is not in the account menu")
    assert re.search(r'\$\("tasksPageBtn"\)\.onclick\s*=.{0,120}'
                     r'automations\.openPage\(\)', app, re.S)


def test_the_page_re_reads_on_the_library_frame(app):
    m = re.search(r'case "library":(.*?)break;', app, re.S)
    assert m and "automations.changed()" in m.group(1)


# --------------------------------------------------------------------------- #
# phase 7b: the unread dot, the clock glyph, the desk's line, the history
# --------------------------------------------------------------------------- #
def test_a_run_chat_row_draws_the_daemons_unread_and_origin(app):
    """AC-469: the row's dot and clock come from `unread` and `origin`,
    both named in the accessible label; the project line rolls the dot up;
    nothing is kept in the browser."""
    row = _strip_comments(_fn_body(app, "buildChatRow"))
    assert 'c.origin.kind === "automation"' in row
    assert "c.unread" in row and '"udot"' in row and "CLOCK_ICON()" in row
    assert '", unread automation run"' in row and '", automation run"' in row
    assert "+ runWords" in row
    node = _strip_comments(_fn_body(app, "projectNode"))
    assert "c.unread" in node and "unread automation run" in node


def test_a_library_frame_about_runs_re_reads_the_tree(app):
    code = _strip_comments(app)
    assert re.search(r'm\.what === "automations" \|\| m\.what === "runs"\)'
                     r'\s*loadChats\(\)', code)


def test_the_desk_line_is_adopted_only_when_idle_and_caps_stay_honest(app):
    """AC-470's page half: the `announce` frame names a turn the daemon
    minted, adopted only over nothing; `caps` is re-sent when the microphone
    or the speaker changes, because `hello` goes out once at boot."""
    code = _strip_comments(app)
    assert re.search(r'm\.type === "announce"\) \{\s*if \(currentTurn == null '
                     r'&& state === WAKE\)', code)
    assert "wake: !!(micLive && wake), tts: !!ttsNode" in code
    assert code.count("sendCaps();") >= 2          # arm and stop
    assert "openPlayback().then(sendCaps)" in code


def test_the_history_says_what_each_channel_did(auto):
    body = _strip_comments(_fn_body(auto, "noticeWords"))
    assert '" sent to "' in body and '" failed: "' in body
    assert "c.service" in body and "c.label" in body
    assert "Nothing is sent yet" not in auto


# --------------------------------------------------------------------------- #
# docs/proactive.md P1: silence (PR-3, PR-4) and failure streaks (PR-7)
# --------------------------------------------------------------------------- #
def test_the_history_says_silent_quiet_and_turned_off(auto):
    """AC-475's "completed, nothing new" and AC-480's "turned off after N
    failures", drawn from the daemon's own record: `run.silent`, the notify
    entry's `spoken` ("silent", "quiet"), `streak` and `turnedOff`."""
    mark = _strip_comments(_fn_body(auto, "mark"))
    # The word before ", nothing new" is the served `statusWord` (Step 6a).
    assert "run.silent" in mark and '", nothing new"' in mark
    words = _strip_comments(_fn_body(auto, "noticeWords"))
    assert 'n.spoken === "silent"' in words
    assert 'n.spoken === "quiet"' in words and "n.streak" in words
    assert "n.turnedOff" in words and '" failures"' in words
    # Neither marker may fall through to the generic "desk line ..." words:
    # both RETURN before that line is reached.
    assert re.search(r'n\.spoken === "silent"\) return ', words)
    assert 'return ["not sent: failed "' in words
    assert words.index('n.spoken === "quiet"') < words.index('"desk line "')


def test_the_editor_has_the_silence_switch_and_sends_it(auto, index):
    """PR-3: one switch per automation, in the editor's More options since
    Step 6a (markup, not built by the script; AC-709); it opens with the
    automation's own value (a new one ON) and Save sends it."""
    code = _strip_comments(auto)
    m = re.search(r'<button[^>]*id="autoSilence"[^>]*>', index)
    assert m and 'role="switch"' in m.group(0)
    assert 'aria-labelledby="autoSilenceLab"' in m.group(0)
    assert re.search(r'id="autoSilenceLab">Nothing new', index)
    assert 'const silenceSw = $("autoSilence")' in code
    opened = _strip_comments(_fn_body(auto, "openEditor"))
    assert "auto.silence" in opened
    saved = _strip_comments(_fn_body(auto, "save"))
    assert re.search(r"silence: [^,\n]*aria-checked", saved)


# --------------------------------------------------------------------------- #
# docs/proactive.md P2: the pinned heartbeat (PR-8..PR-11) and quiet hours
# --------------------------------------------------------------------------- #
def test_the_heartbeat_is_pinned_and_its_rules_are_the_daemons(lib):
    """PR-9: the pinned row comes first in the tab and is drawn from the
    panel answer; its one write is POST /api/heartbeat. The presets and the
    checklist count are SERVED (`heartbeatMinutes`, `checklistItems`): the
    page holds no interval table and judges no line empty itself."""
    code = _strip_comments(lib)
    draw = _strip_comments(_fn_body(lib, "drawAutomations"))
    assert draw.index("beatRow(autos.heartbeat") < draw.index("automationRow")
    assert '"/api/heartbeat"' in code
    assert "autos.heartbeatMinutes" in code and "heartbeatDefault" in code
    assert "hb.checklistItems" in code
    for table in ("240", "[15, 30", "120"):
        assert table not in code, f"library.js holds the preset table: {table}"


def test_an_open_heartbeat_editor_is_not_rebuilt_under_typing(lib):
    body = _strip_comments(_fn_body(lib, "render"))
    guard = body.index("body.contains(beat.node)")
    assert guard < body.index("body.replaceChildren()")


def test_the_history_names_the_new_outcomes(auto):
    # A run's own word ("skipped (empty checklist)") is the daemon's served
    # `statusWord` since Step 6a; the page holds no status table
    # (test_library_lantern_page.py, test_automations_served.py).
    assert "run.statusWord" in _strip_comments(_fn_body(auto, "mark"))
    words = _strip_comments(_fn_body(auto, "noticeWords"))
    assert 'n.spoken === "held: quiet hours"' in words
    # A heartbeat, and a reminder (docs/proactive.md PR-17), have no recipe
    # editor to open on the full page.
    history = _strip_comments(_fn_body(auto, "renderHistory"))
    assert ('const own = a.kind === "heartbeat" || a.kind === "reminder";'
            in history and "if (!own) {" in history)


# --------------------------------------------------------------------------- #
# docs/proactive.md P4: the precheck choice (PR-23, AC-493)
# --------------------------------------------------------------------------- #
def test_the_editor_picks_a_listed_precheck_and_never_writes_one(auto, index):
    """The choice is the daemon's list (GET /api/prechecks) plus "none";
    Save sends only the NAME; the page has no way to create a script. The
    select is markup in More options since Step 6a (AC-709)."""
    code = _strip_comments(auto)
    assert re.search(r'<select[^>]*id="autoPrecheck"', index)
    assert 'const precheckPick = $("autoPrecheck")' in code
    drawn = _strip_comments(_fn_body(auto, "drawPrechecks"))
    assert 'api("GET", "/api/prechecks")' in drawn
    assert '"none"' in drawn and "got.folder" in drawn
    assert "auto.precheck" in _strip_comments(_fn_body(auto, "openEditor"))
    assert "precheck: precheckPick.value" in _strip_comments(
        _fn_body(auto, "save"))
    assert not re.search(r'api\("(POST|PUT|DELETE)", "/api/prechecks', code)
