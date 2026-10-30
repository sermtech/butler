"""D-13 instrument for the configurable binding table.

`docs/shortcuts.md` 7 names three rules that must be validated by mutation, and
they are the three whose failure is either invisible or unrecoverable:

  1. **the exact-modifier match.** A subset match makes a BARE binding fire
     under every modifier, so `numpad *` would dictate in the middle of every
     Ctrl chord -- and fires `ctrl+numpad *` under Ctrl+Shift, which may be
     somebody else's shortcut.
  2. **the bare-key restriction.** Suppression stops a key producing its
     character SYSTEM-WIDE. A bare letter getting through is the one refusal in
     this feature the owner cannot recover from on the page, because with `e`
     suppressed they cannot type into the pane that would undo it.
  3. **the UP replays the DOWN.** Deleting the `_held.pop` leaves the entry
     behind, so the next press reads as auto-repeat and the hotkey is dead
     until hotkeys are toggled off and on.

Each rule is broken on its own and asks TWO questions, with exit codes only:

  A. do the AIMED tests fail?            (want 1)
  B. does the rest of the file pass?     (want 0)

One question is not enough. A mutation that reddens the whole file proves the
instrument is COUPLED, not that it is aimed (test/AGENTS.md).

Read pytest's EXIT CODE and never its output: pytest colours the word "FAILED",
and an earlier session matched that word in the text and reported every mutation
as surviving.

`__pycache__` is deleted around every write. A mutation and its restore can land
inside one filesystem timestamp second, and Python's bytecode cache validates on
source mtime and SIZE -- so the mutated `.pyc` gets served for the restored file
and a whole suite runs green against code the reader can see is correct.
Measured on 2026-08-18 against `chats.py`.

Run:  python -X utf8 test/manual/mutate_bindings.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACTIONS = REPO / "desk" / "actions.py"
HOTKEYS = REPO / "desk" / "hotkeys.py"
SUITE = "test/test_desk_hotkeys.py"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

_CACHE_ROOTS = ("daemon", "desk", "test")

# (name, file, aimed `-k` expression, anchor, replacement)
#
# Every anchor is asserted UNIQUE before anything is written, so a mutation that
# lands nowhere reports as an error rather than as a survivor.
RULES = [
    # 1. The exact match becomes a SUBSET match: a binding fires whenever its
    #    own modifiers are held, whatever else is. That is the plausible wrong
    #    implementation, which is why it is the one worth mutating to.
    # SIX honest witnesses, and the harness names every one rather than
    # pretending it has one (test/AGENTS.md). They are not coupling: each is a
    # different consequence of a subset match -- the wrong binding fires, one
    # key's two chords collapse into one, an unbound combination is swallowed,
    # and the callback suppresses a keystroke it should have passed on.
    ("the modifier match is EXACT, never a subset", ACTIONS,
     "exact_chord or DIFFERENT_bindings or NOT_bound or callback_returns_1",
     "        return self._by_chord.get((vk, mods))\n",
     "        # MUTATED: a SUBSET match\n"
     "        for (key_vk, key_mods), binding in self._by_chord.items():\n"
     "            if key_vk == vk and (mods & key_mods) == key_mods:\n"
     "                return binding\n"
     "        return None\n"),

    # 2. The bare-safe check goes away entirely.
    ("a bare key that is not bare-safe is refused", ACTIONS,
     "bare_key_that_is_not_bare_safe",
     '    if not mods and not key.bare_safe:\n',
     '    if False:  # MUTATED: any key may be bound bare\n'),

    # 3. THE ONE THE PLAN NAMES: delete the `_held.pop`. The release still
    #    replays -- it reads the entry -- but never clears it, so the next press
    #    of that key reads as auto-repeat.
    # THREE witnesses, and the third one is the reason this rule matters at all:
    # an entry left behind makes the NEXT press of that key read as auto-repeat,
    # so the hotkey is dead until hotkeys are toggled off and on.
    ("the UP POPS the entry the DOWN made", HOTKEYS,
     "UP_replays_the_action or MID_HOLD or two_keys_held",
     "    binding = held.pop(vk, None)\n",
     "    binding = held.get(vk)  # MUTATED: the entry is never cleared\n"),
]


def _clear_pycache() -> None:
    # Scoped to the trees this harness rewrites. `REPO.rglob` reaches into
    # `.venv`, deletes the bytecode cache of every installed package, and turns
    # a three-run harness into one that has to be killed.
    for root in _CACHE_ROOTS:
        for cache in (REPO / root).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def _pytest(select: str) -> int:
    """Run one selection and answer its EXIT CODE. Output is discarded."""
    args = [PY, "-X", "utf8", "-m", "pytest", SUITE, "-q"]
    if select:
        args += ["-k", select]
    return subprocess.run(
        args, cwd=str(REPO),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode


def main() -> int:
    originals = {path: path.read_text(encoding="utf-8")
                 for path in (ACTIONS, HOTKEYS)}
    failures = 0

    _clear_pycache()
    if _pytest("") != 0:
        print("ABORT: the suite is not green before any mutation")
        return 2
    print(f"baseline {SUITE}: exit 0\n")

    for name, path, aimed, anchor, replacement in RULES:
        source = originals[path]
        if source.count(anchor) != 1:
            print(f"ERROR  {name}: anchor appears {source.count(anchor)} "
                  f"times in {path.name}, not once")
            failures += 1
            continue
        try:
            path.write_text(source.replace(anchor, replacement),
                            encoding="utf-8")
            _clear_pycache()
            a = _pytest(aimed)
            b = _pytest(f"not ({aimed})")
        finally:
            path.write_text(source, encoding="utf-8")
            _clear_pycache()

        restored = _pytest("")
        ok = (a == 1 and b == 0 and restored == 0)
        failures += 0 if ok else 1
        print(f"{'PASS ' if ok else 'FAIL '} {name}  [{path.name}]\n"
              f"        aimed tests       exit {a} (want 1)\n"
              f"        rest of the file  exit {b} (want 0)\n"
              f"        after restore     exit {restored} (want 0)")

    print(f"\n{len(RULES) - failures}/{len(RULES)} rules have a test that "
          f"catches them")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
