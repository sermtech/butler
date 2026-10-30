"""The page's half of two wake words (docs/wake-words.md AC-805..AC-809).

Source pins over `web/`, comments stripped first (test/AGENTS.md "Grep gates
over web/"). The detector's own behaviour is `test_wake_detector.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_library_page import _fn_body, _strip_comments

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture(scope="module")
def app() -> str:
    return _strip_comments((WEB / "app.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def markup() -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


@pytest.fixture(scope="module")
def wake() -> str:
    return _strip_comments((WEB / "wake.js").read_text(encoding="utf-8"))


def test_the_page_names_no_wake_phrase_of_its_own(app, markup):
    # AC-809: the words that are on come from the daemon; a phrase written
    # here would be shown whether or not that word is on.
    for src in (app, markup):
        assert not re.search(r"(?i)hey[ _]?(jarvis|butler)", src)


def test_the_page_names_no_wake_setting(app):
    # AC-41: the daemon sends the word list; the page never names the keys.
    assert "wake_hey_" not in app
    for gone in ("WAKE_THRESHOLD", "WAKE_COOLDOWN_MS", "WAKE_PHRASE"):
        assert gone not in app, gone


def test_the_candidate_names_the_fired_word(app):
    body = _fn_body(app, "onWakeFire")
    assert re.search(r'send\(\{\s*type:\s*"wake_candidate",\s*word,', body)
    assert "onWakeFire(r.score, r.word)" in _fn_body(app, "pumpWake")


def test_the_config_comes_from_ready_and_the_broadcast(app):
    assert "applyWakeConfig(m.wake)" in app
    assert re.search(r'case "wake":\s*applyWakeConfig\(m\);', app)
    assert "wakeCfg.cooldown_ms" in _fn_body(app, "goListen")
    assert "await wakeCfgKnown" in _fn_body(app, "arm"), "arming waits"


def test_a_threshold_change_does_not_reload(app):
    body = _fn_body(app, "applyWakeConfig")
    assert "wake.setThreshold(wakeCfg.threshold)" in body
    assert "wordsKey(wakeCfg.words) !== wakeKey" in body


def test_the_hints_are_written_from_the_words(app, markup):
    body = _fn_body(app, "paintWakeWords")
    for el in ("emptyHint", "wakeHelp", "wakeThreshold"):
        assert f'$("{el}")' in body, el
    assert 'id="wakeHelp"' in markup
    assert "Say <b>the wake word</b>, or type." in markup


def test_sessions_are_created_one_at_a_time(wake):
    # web/AGENTS.md: sequential session creation is load-bearing on iOS.
    assert "Promise.all" not in wake
    body = _fn_body(wake, "loadWakeWordDetector")
    assert body.count("await ort.InferenceSession.create(") == 3
    assert "for (const w of words)" in body


def test_diag_lists_the_daemons_words(app):
    diag = _strip_comments((WEB / "diag.js").read_text(encoding="utf-8"))
    assert not re.search(r"(?i)hey_?jarvis|hey_?butler", diag)
    assert "window.butlerWake" in diag
    assert "window.butlerWake = wakeCfg" in app
