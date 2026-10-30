"""D-13 instrument for the typed turn (AC-162, AC-163, R6, R7).

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

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_typed_turn.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "daemon" / "hub.py"
SUITE = "test/test_typed_turn.py"

# (label, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    ("AC-163: the daemon mints the id", HUB,
     "        turn = self._begin_turn(s)\n"
     "        # THINKING from the moment the turn exists",
     "        turn = self._begin_turn(s)\n"
     "        turn = int(m.get('turn') or turn)\n"
     "        s.turn = turn\n"
     "        # THINKING from the moment the turn exists",
     ["test_the_daemon_mints_the_turn_id_and_ignores_the_pages"],
     ""),
    ("empty text is refused", HUB,
     "        text = text.strip()\n"
     "        if not text:",
     "        text = text.strip()\n"
     "        if False:",
     ["test_a_typed_message_with_no_text_is_refused",
      "test_a_refused_typed_message_costs_no_capture"],
     "two honest witnesses: the refusal itself, and the capture it must "
     "not cost"),
    # The MISSING CHECK, not a changed VALUE. Multiplying
    # `TYPED_CHARS_PER_SECOND` by a thousand was tried first and SURVIVED
    # (2026-09-10): the ceiling is derived and the test reads the derivation
    # rather than a second copy of the number, so the expectation moves with
    # it. That trade is the point of the derivation and the test's docstring
    # says so; what a ceiling has to be able to fail at is being absent.
    ("the ceiling bounds the text", HUB,
     "        if len(text) > TYPED_MAX_CHARS:",
     "        if False:",
     ["test_the_ceiling_refuses_and_the_character_below_it_does_not"],
     ""),
    ("R7: a typed message cancels a capture", HUB,
     "        await self._barge_in()\n"
     "        turn = self._begin_turn(s)",
     "        turn = self._begin_turn(s)",
     ["test_a_typed_message_abandons_a_capture_and_the_session_rearms",
      "test_typing_over_the_wire_cancels_a_capture_and_leaves_it_working"],
     "two honest witnesses, and the first one was TOOTHLESS until this "
     "harness ran: it asserted `s.pipeline is None`, which the typed turn's "
     "own re-arm sets anyway, so only the wire case reddened"),
    ("AC-162: everything downstream is _on_final's", HUB,
     "        s.task = asyncio.create_task(\n"
     "            self._on_final(s, turn, ports.Final(text=text), \"typed\"))",
     "        s.reply_task = asyncio.create_task(self._reply(s, turn, text))",
     ["test_a_typed_turn_and_a_spoken_one_produce_the_same_record",
      "test_a_typed_turn_gets_no_capture_start"],
     "two honest witnesses: the record, and the frame that names the turn. "
     "Question B is unanswerable for this one and the breadth IS the "
     "evidence -- skipping `_on_final` removes the `final` frame every "
     "other case in the file reads its turn id from"),
    ("R6: the speak setting governs a typed turn", HUB,
     "        return bool(self.settings.get(\"speak\", True))",
     "        return False",
     ["test_the_speak_setting_governs_a_typed_turn"],
     "COUPLED and said so: this is AC-110's own rule, so it also reddens "
     "test_conversation.py's muted case. The aimed question is still "
     "honest -- a typed turn that never speaks must not pass here"),
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
        ok = a == 1 and restored == 0
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
