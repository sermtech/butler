"""The PAGE half of linking a device by QR code (docs/messaging.md MS-5,
AC-503): `buildPairing` in `web/settings.js`.

GREP GATES, `test_file_explorer_page.py`'s shape and reason: nothing in this
suite executes the page's JavaScript. The daemon half -- the route and the
document it answers -- is proved in `test_whatsapp_owner.py`; the pixels by
a scratch daemon run (phase M2's manual check).
"""
import re
from pathlib import Path

import pytest

from test_file_explorer_page import _fn_body, _strip_comments

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "web" / "settings.js"


@pytest.fixture(scope="module")
def src():
    return SETTINGS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def body(src):
    return _fn_body(src, "buildPairing")


def test_a_group_that_declares_pairing_gets_the_block(src):
    group = _strip_comments(_fn_body(src, "buildGroup"))
    assert "if (g.pairing) section.append(buildPairing(g.pairing))" in group


def test_the_page_names_no_service_state_or_setting(src):
    """AC-41: the route, the states, the words and the buttons are the
    daemon's. The page may not name WhatsApp, a pairing state, or the route."""
    code = _strip_comments(src)
    assert not re.search(r"whatsapp", code, re.I)
    for state in ("awaiting_scan", "logged_out", "replaced", "paired",
                  "connecting", "/pairing", "unlink"):
        assert state not in code, f"settings.js names {state!r}"


def test_untrusted_text_goes_in_as_text(body):
    code = _strip_comments(body)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert sink not in code
    assert "status.textContent" in code and "detail.textContent" in code


def test_the_code_is_a_png_data_url_on_a_forced_white_plate(body):
    code = _strip_comments(body)
    assert 'startsWith("data:image/png;base64,")' in code, (
        "the QR src must be checked to be a PNG data URL, never any URL")
    assert 'plate.style.background = "#fff"' in code, (
        "an inverted code does not scan: the plate is white in every theme")


def test_it_polls_only_while_the_daemon_says_so(src, body):
    code = _strip_comments(body)
    assert "const PAIRING_POLL_MS = 2000;" in src
    assert re.search(r"if \(doc\.poll && box\.isConnected\) timer = "
                     r"setTimeout\(\(\) => read\(\), PAIRING_POLL_MS\)", code)
    assert "setInterval" not in code
    # A rebuild drops the old blocks' re-readers.
    assert "pairings.clear();" in _strip_comments(_fn_body(src, "build"))


def test_buttons_come_from_the_document_and_a_confirm_takes_two_presses(body):
    code = _strip_comments(body)
    assert "for (const action of doc.actions || [])" in code
    assert "action.method" in code and "action.query" in code
    assert "JSON.stringify({ confirm: true })" in code
    assert "armed === action.id" in code and "RESET_ARM_MS" in code


def test_a_settings_change_rereads_every_block(src):
    for fn in ("apply", "applyChanged"):
        assert "for (const reread of pairings) reread();" in _strip_comments(
            _fn_body(src, fn)), fn
