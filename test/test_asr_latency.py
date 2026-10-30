"""The streaming-latency picker and the engine's own catalogue (AC-7b, AC-42).

The defect these encode, measured 2026-08-19: `asr_latency` offered `low`,
`balanced` and `high`, and `daemon/engines/catalog.json` has never had a
preset called `high`. `stt_engine._family_ext` looked it up with a default of
`balanced`, so choosing `high` saved, reported success and changed nothing --
while `accurate`, a preset that does exist and is both cheaper per second of
audio and more accurate, could not be reached from the settings page at all.

That is the AC-7b failure by the front door, and the AC-42 lesson a second
time: a list of the ENGINE's options written into the daemon is wrong the day
the engine changes, and wrong invisibly. So the picker is filled from the
catalogue, and this file is what reddens if either side drifts back.

The catalogue is read here by PATH rather than through
`stt_engine.catalog_public()`, and that is a deliberate cost. Importing the
engine pulls in numpy and transcribe_cpp; on this machine, with the pagefile
commit limit near exhaustion, OpenBLAS aborts the interpreter during that
import -- one run in four on 2026-08-19, killing pytest before it printed a
single line. A fast-tier test that dies with the machine is an instrument that
lies, so the one check that genuinely needs the module runs in a CHILD
process, where a native abort is an exit code this file can read instead of
its own death.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from daemon.settings import BY_KEY, DEFAULTS, Settings          # noqa: E402

ENGINES = ROOT / "daemon" / "engines"
CATALOG = json.loads(
    (ENGINES / "catalog.json").read_text(encoding="utf-8"))
# `_note` is documentation for whoever opens the file, not a preset. The
# engine's own `catalog_public()` drops it the same way.
PRESETS = {k: v for k, v in CATALOG["latency_presets"].items() if k != "_note"}

PARAKEET = "parakeet-unified-en-0.6b"           # stream_kind: buffered
NEMOTRON = "nemotron-3.5-asr-streaming-0.6b"    # stream_kind: cache_aware


def test_every_latency_the_page_offers_exists_in_the_engine_catalogue():
    """Both directions, because each one hides a different lie.

    A choice with no preset behind it is a control that saves and does
    nothing (`high`). A preset with no choice in front of it is a capability
    the owner already has and cannot select (`accurate`). Only one of the two
    is visible from the settings page, and it is the harmless one.
    """
    offered = set(BY_KEY["asr_latency"].choices)

    assert offered <= set(PRESETS), (
        f"the settings page offers {sorted(offered - set(PRESETS))}, which "
        f"the engine catalogue has no preset for; choosing one saves, "
        f"reports success and silently runs something else (AC-7b)")
    assert set(PRESETS) <= offered, (
        f"the engine ships {sorted(set(PRESETS) - offered)}, which cannot be "
        f"reached from the settings page at all (AC-42)")


def test_the_latency_choices_are_in_the_catalogue_s_own_order():
    """Fastest partials first, cheapest last -- the order the engine lists
    them in, so a preset added to the catalogue lands in the right place on
    the page without anyone editing the daemon."""
    assert list(BY_KEY["asr_latency"].choices) == list(PRESETS), (
        f"the picker's order {list(BY_KEY['asr_latency'].choices)} no longer "
        f"follows the catalogue's {list(PRESETS)}")


def test_the_shipped_default_is_the_cheapest_and_most_accurate_preset():
    """Owner's ruling 2026-08-19, and the arithmetic behind it.

    A preset slides a window over the audio, so each second is re-encoded
    (left + chunk + right) / chunk times: 37.0 for `low`, 12.0 for `balanced`,
    7.4 for `accurate`. The default is therefore the cheapest of the three AND
    the one the engine names best-accuracy. What it costs is lookahead --
    2080 ms against 1120 ms -- so partials appear about a second later. That
    trade is the whole decision, which is why it is asserted here and spelled
    out in the help text rather than left in a JSON file the owner never
    opens.
    """
    assert DEFAULTS["asr_latency"] == "accurate", (
        f"the shipped latency default is {DEFAULTS['asr_latency']!r}")

    accurate, balanced = PRESETS["accurate"], PRESETS["balanced"]

    def passes_per_second(p):
        return (p["left_ms"] + p["chunk_ms"] + p["right_ms"]) / p["chunk_ms"]

    assert passes_per_second(accurate) < passes_per_second(balanced), (
        "the default is no longer the cheaper of the two, so the help text's "
        "reason for choosing it is now false (D-12)")
    assert accurate["lookahead_ms"] > balanced["lookahead_ms"], (
        "the default no longer costs lookahead, so the help text's warning "
        "about later partials is now false (D-12)")


def test_the_help_text_names_every_preset_it_offers():
    """D-12: stale prose is the recurring hazard here. The help text compares
    the presets to one another by name, so a preset added to the catalogue
    reaches the picker on its own and leaves the sentence beside it
    incomplete -- an option the owner is offered and told nothing about."""
    spec = BY_KEY["asr_latency"]
    # By the LABEL the picker shows (AC-811), which is derived from the
    # catalogue, so a new preset still leaves this sentence incomplete.
    for choice in spec.choices:
        assert spec.labels[choice] in spec.help, (
            f"{choice!r} ({spec.labels[choice]!r}) is offered and the help "
            f"text does not mention it")
    # Advanced, and NOT restart-flagged since 2026-10-27 (AC-713): a new
    # adapter over the cached GGUF is warmed and swapped in for the next
    # thing said (`test_settings_apply.py`).
    assert spec.advanced and not spec.restart, (
        "asr_latency is an advanced engine setting swapped live (AC-713)")


def test_a_stored_latency_the_engine_dropped_is_refused_loudly(tmp_path,
                                                               capsys):
    """What an existing install gets. `high` is in the owner's settings.json
    if they ever chose it, and it names nothing now.

    Loud and not fatal, like every other bad preference (D-1): the value is
    refused by name on the console and the setting falls back to the shipped
    default, rather than the daemon refusing to boot over a preference.
    """
    (tmp_path / "s.json").write_text(
        '{"asr_latency":"high","turn_mode":"endword"}', encoding="utf-8")

    prefs = Settings(tmp_path / "s.json")

    assert prefs["asr_latency"] == DEFAULTS["asr_latency"]
    assert prefs.turn_mode == "endword", "one bad field discarded a good one"
    assert "asr_latency" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the engine's own half, in a child process
# --------------------------------------------------------------------------- #
_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import stt_engine
out = {"rungs": sorted(stt_engine._CACHE_AWARE_RIGHT)}
try:
    stt_engine._family_ext(sys.argv[2], "high")
    out["refused"] = ""
except KeyError as e:
    out["refused"] = str(e)
out["chunk_ms"] = stt_engine._family_ext(sys.argv[2], "accurate").chunk_ms
out["right"] = stt_engine._family_ext(sys.argv[3], "accurate").att_context_right
print("RESULT " + json.dumps(out))
"""


def _probe() -> dict:
    """Ask a child process what the engine does with a preset.

    A child because importing the engine loads native backends, and this
    machine aborts that import outright when it is short of commit charge.
    An abort there is a fact about the machine, not about the rule, and the
    two must not be reported the same way -- so a child that never printed a
    RESULT skips, and the module's absence is checked first so the skip
    cannot swallow a deleted engine.
    """
    assert (ENGINES / "stt_engine.py").exists(), (
        "daemon/engines/stt_engine.py is gone; the skip below would have "
        "hidden that")
    # Twice before giving up, because the abort is a transient shortage of
    # commit charge and the skip is the dangerous branch here: it reported a
    # BROKEN `_family_ext` as a pass once on 2026-08-19, during the D-13 run
    # that was checking this very test. A skip that can mask the defect has
    # to be as hard to reach as it can honestly be made.
    for _ in range(2):
        done = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", _PROBE,
             str(ENGINES), PARAKEET, NEMOTRON],
            capture_output=True, text=True, timeout=120)
        for line in done.stdout.splitlines():
            if line.startswith("RESULT "):
                return json.loads(line[len("RESULT "):])
    pytest.skip(f"the engine could not be loaded here (exit {done.returncode}): "
                f"{done.stderr.strip()[-300:]!r}")


def test_an_unknown_preset_is_refused_by_the_engine_not_quietly_replaced():
    """The mechanism of the defect, one layer below the picker.

    `_family_ext` used to answer an unknown preset with `balanced`, so the
    only way the daemon and the engine could disagree was silence. It fails
    fast now: the two lists are held together above, and if they ever part
    company again the failure has to be audible.

    The same child answers the cache-aware rung table, which is a SECOND list
    that can drift and cannot come from the catalogue -- nemotron's
    `att_context_right` is a trained menu, so the preset-to-rung mapping lives
    in `stt_engine`. A preset added to the catalogue reaches the picker for
    free and reaches that dict not at all, and the first person to select it
    on nemotron would get a KeyError at the start of a turn instead of a red
    test here.
    """
    got = _probe()

    assert got["refused"], (
        "the engine accepted a preset the catalogue does not have and ran "
        "something else instead (AC-7b)")
    assert "high" in got["refused"], got["refused"]
    assert got["chunk_ms"] == PRESETS["accurate"]["chunk_ms"], (
        "the buffered preset stopped reaching the stream options")
    assert sorted(got["rungs"]) == sorted(PRESETS), (
        f"the cache-aware rung table and the catalogue disagree: "
        f"{sorted(set(got['rungs']) ^ set(PRESETS))}")
    assert got["right"] == 13, (
        "the cache-aware rung for `accurate` is no longer the 1040 ms one")
