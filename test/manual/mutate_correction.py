"""D-13 instrument for retry and correct (AC-97).

Break ONE rule at a time, then ask TWO questions with EXIT CODES ONLY:

  A. does the test the rule is named after FAIL (exit 1)?
  B. does the rest of the file still PASS with that test deselected (exit 0)?

One question is not enough: a mutation that reddens the whole file proves the
instrument is coupled rather than aimed. Where a rule honestly has more than
one witness, the row names all of them instead of pretending it has one.

Read pytest's exit code and never its output -- an earlier session in this
repository matched the word "FAILED" in the text, pytest colours that word,
and every mutation was reported as surviving (test/AGENTS.md).

`__pycache__` is deleted between runs, scoped to `daemon/` and `test/`: a
mutation the same LENGTH as the original, written and reverted inside one
filesystem timestamp second, is served from a stale `.pyc` and reads as a
survivor followed by a restore that will not go green.

THE ROW WORTH RUNNING BEFORE TOUCHING ANY OF THIS is the second one. It puts
the OBVIOUS implementation of the reference check back -- "is this id within
the range the daemon has minted" -- which refuses every value in the
parametrized case and is still wrong, because a turn the DAEMON minted is not
a turn THIS DEVICE owns (D-6).

There is deliberately NO row for retry: retry is the absence of a mechanism,
so there is nothing to break. Its test is a pin against one being added.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_correction.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "daemon" / "hub.py"
SUITE = "test/test_correction.py"

REFUSED = "test_a_correction_of_a_turn_this_device_was_not_given_is_refused"

# (label, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    ("AC-97: `true` is not turn 1", HUB,
     "            if isinstance(corrects, bool) or not isinstance(corrects, int):",
     "            if not isinstance(corrects, int):",
     [f"{REFUSED}[True]"],
     "one witness on purpose: `bool` subclasses `int`, so this is the only "
     "value in the parametrized set the type check alone lets through"),
    ("D-6: a turn the DAEMON minted is not a turn THIS DEVICE owns", HUB,
     "            if corrects not in s.seen_turns:",
     "            if not (0 < corrects <= self._turn_seq):",
     ["test_a_correction_of_another_devices_turn_is_refused"],
     "the obvious wrong implementation, and it passes all eight rows of the "
     "parametrized case -- which is exactly why the phone is a test of its "
     "own"),
    ("AC-97: a reference is checked at all", HUB,
     "            if corrects not in s.seen_turns:",
     "            if False:",
     [f"{REFUSED}[9999]", f"{REFUSED}[0]", f"{REFUSED}[-1]",
      f"{REFUSED}[True]",
      "test_a_correction_of_another_devices_turn_is_refused",
      "test_a_refused_correction_costs_no_capture",
      "test_correcting_a_spoken_turn_over_the_wire"],
     "SEVEN honest witnesses and they are different claims: four unminted "
     "values, another device's turn, the capture a refusal must not cost, "
     "and the wire. The three non-int rows are NOT here -- the type check "
     "above still catches those. The capture one was found BY THIS HARNESS: "
     "it was left out of the first list and question B came back 1"),
    ("AC-97: the reference rides back on the final", HUB,
     "            corrects=corrects,\n",
     "",
     ["test_a_correction_is_a_new_turn_and_the_original_stays",
      "test_an_ordinary_turn_says_it_corrects_nothing",
      "test_a_correction_can_be_corrected_and_the_chain_stays_in_the_record",
      "test_correcting_a_spoken_turn_over_the_wire"],
     "four witnesses, and the ordinary-turn one is the interesting one: an "
     "optional field silently dropped is what a stale daemon looks like"),
    ("AC-97: a turn is remembered as correctable", HUB,
     "        s.seen_turns.append(turn)",
     "        pass",
     ["test_a_correction_is_a_new_turn_and_the_original_stays",
      "test_a_correction_can_be_corrected_and_the_chain_stays_in_the_record",
      "test_correcting_a_spoken_turn_over_the_wire"],
     "three witnesses: with nothing remembered every correction is refused, "
     "so this is the mutation that turns the feature off rather than "
     "breaking it"),
    ("the refusal is checked BEFORE the barge-in", HUB,
     "        corrects = m.get(\"corrects\")\n"
     "        if corrects is not None:",
     "        await self._barge_in()\n"
     "        corrects = m.get(\"corrects\")\n"
     "        if corrects is not None:",
     ["test_a_refused_correction_costs_no_capture"],
     "one witness, and it is the only one that can exist: every other case "
     "here has no capture in flight to lose"),
]


def clear_pycache() -> None:
    for root in ("daemon", "test"):
        for cache in (REPO / root).rglob("__pycache__"):
            for pyc in cache.glob("*.pyc"):
                pyc.unlink()


def pytest(*args: str) -> int:
    clear_pycache()
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", *args, "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL).returncode


def main() -> int:
    baseline = pytest(SUITE)
    print(f"baseline: exit {baseline} (want 0)")
    if baseline != 0:
        print("the suite is not green before any mutation; nothing below "
              "would mean anything")
        return 1

    caught = 0
    for label, path, anchor, replacement, aimed, note in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        if original.count(anchor) != 1:
            print(f"[ERROR] {label}: the anchor appears "
                  f"{original.count(anchor)} times in {path.name}; a "
                  f"mutation that lands nowhere reads as a survivor")
            continue
        path.write_text(original.replace(anchor, replacement),
                        encoding="utf-8")
        try:
            aimed_args = [f"{SUITE}::{name}" for name in aimed]
            a = pytest(*aimed_args)
            deselect = []
            for name in aimed:
                deselect += ["--deselect", f"{SUITE}::{name}"]
            b = pytest(SUITE, *deselect)
        finally:
            path.write_text(original, encoding="utf-8")
        restored = pytest(SUITE)
        ok = a == 1 and b == 0 and restored == 0
        caught += ok
        print(f"[{'CAUGHT' if ok else 'SURVIVED'}] {label}\n"
              f"    aimed  exit {a} (want 1) -- {', '.join(aimed)}\n"
              f"    rest   exit {b} (want 0)\n"
              f"    restore exit {restored} (want 0)"
              + (f"\n    note: {note}" if note else ""))
    print(f"\n{caught}/{len(MUTATIONS)} caught")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
