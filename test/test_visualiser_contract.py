"""The visualiser's contract, as a grep gate rather than a promise.

Same shape and same reason as the localStorage gate in `test_http_surface.py`:
`web/visualiser.js` is a CLASSIC script loaded by a plain `<script src>` before
the app.js module, in a directory served as-is with no build step. Nothing
compiles it, nothing lints it, and no other test in this suite loads it -- so
the four rules it must not break are invisible to every check the daemon has,
and the symptom of breaking one is a page that renders as if the visualiser had
never been built (D-22).

The rules, each with the failure it exists to catch:

  * No AudioContext. Butler owns both audio graphs and this file may only TAP
    them. A visualiser that opens a context of its own on a device whose
    microphone is live can make sound, and its predecessor's `beginOutput()`
    did exactly that -- an oscillator, so the design comp could demo without a
    backend (web/AGENTS.md).
  * No browser storage. Settings live on the daemon so two devices cannot
    diverge; one laptop once held four disagreeing localStorage copies of a
    voice setting (D-2, AC-8).
  * ASCII only, so nothing here can break a Windows console or a diff.
  * Every method app.js calls exists. This is the D-18 half -- a contract needs
    an assertion on the CALLER, not just the callee -- so the list is READ OFF
    app.js rather than written down twice. A new `viz.something()` call site
    with no method behind it reddens this without anyone remembering to add it.
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
VIZ = WEB / "visualiser.js"
APP = WEB / "app.js"

# What the spec (docs/lattice-wave.md section 4) says the caller imposes.
# Named here as well as derived from app.js, because a regression that deleted
# BOTH a call site and its method would satisfy a purely derived check while
# silently dropping a piece of the contract.
REQUIRED = ("attachInput", "attachOutput", "setMode", "pushScore",
            "setState", "detach", "colors", "setEffect")


@pytest.fixture(scope="module")
def source():
    return VIZ.read_text(encoding="utf-8")


def test_the_visualiser_opens_no_audio_context(source):
    """It may tap Butler's graphs and may not own one of its own.

    The CONSTRUCTION is what is banned, not the word: the file's own header
    comment says it opens no AudioContext, and a check that could not tell the
    rule from a statement of the rule would have to be worked around by
    deleting the explanation.
    """
    built = re.findall(r"new\s+(?:webkit|Offline)?AudioContext", source)
    assert built == [], (
        f"visualiser.js constructs {built}; it may only take nodes handed to "
        f"it by app.js (web/AGENTS.md)")


def test_the_visualiser_uses_no_browser_storage(source):
    """AC-8's gate, applied to the one file the other gate does not cover in
    spirit: this one is loaded as a classic script and is easy to forget."""
    for banned in ("localStorage", "sessionStorage"):
        assert banned not in source, f"client storage is banned; found {banned}"


def test_the_visualiser_is_ascii_only():
    """No unicode in code, so a Windows console and a diff both survive it.

    Read as BYTES: a decode with errors='replace' would turn the offending
    byte into a character this test then failed to notice.
    """
    raw = VIZ.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    assert bad == [], f"non-ASCII bytes in visualiser.js at {bad[:5]}"


def test_the_visualiser_defines_every_method_app_js_calls():
    """The D-18 half: assert on the CALLER, not only on the callee.

    Three green-but-broken contracts in this project came from testing one end
    of a pair, so the list of names comes out of app.js by reading its `viz.`
    call sites, and every one of them must be a key of the object create()
    returns.
    """
    app = APP.read_text(encoding="utf-8")
    called = sorted(set(re.findall(r"\bviz\.([A-Za-z_]\w*)", app)))
    assert called, "no viz.* call sites found in app.js -- is the wave wired up?"

    viz = VIZ.read_text(encoding="utf-8")
    missing = [n for n in called if not re.search(r"^\s*" + n + r":", viz,
                                                  re.MULTILINE)]
    assert missing == [], (
        f"app.js calls viz.{{{','.join(missing)}}} and visualiser.js does not "
        f"define {'it' if len(missing) == 1 else 'them'}")

    absent = [n for n in REQUIRED if n not in called]
    assert absent == [], (
        f"app.js no longer uses {absent}; the spec's interface "
        f"(docs/lattice-wave.md section 4) says it should")


def test_the_visualiser_publishes_itself_synchronously(source):
    """index.html loads this with a plain <script src> BEFORE the app.js
    module, and app.js reaches for window.ButlerVisualiser.create at module
    evaluation. An `import`, an `export` or a deferred publication turns that
    into a TypeError that takes the whole page down with it."""
    assert "global.ButlerVisualiser = " in source
    assert not re.search(r"^\s*(import|export)\s", source, re.MULTILINE), (
        "visualiser.js is a classic script; there is no bundler here")
