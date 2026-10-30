"""D-13 instrument for the dictation gate (AC-116).

Four rules keep a whole-clip decode off the recognizer while something else is
using it. Two live in `Hub` and two in `TurnPipeline`, and the pipeline pair is
the interesting half: they encode the fact that `asyncio.to_thread` cannot be
cancelled, so the mark saying "the engine is busy" may only be cleared by the
worker thread that actually holds it.

Each rule is broken on its own and asks TWO questions, with exit codes only:

  A. do the AIMED tests fail?            (want 1)
  B. does the rest of the file pass?     (want 0)

One question is not enough. A mutation that reddens the whole file proves the
instrument is COUPLED, not that it is aimed (test/AGENTS.md). A rule with two
honest witnesses NAMES both rather than pretending it has one -- rule 2 has
two, because removing the guard is visible to a live capture and to an
abandoned finalize alike.

Read pytest's EXIT CODE and never its output: pytest colours the word
"FAILED", and an earlier session matched that word in the text and reported
every mutation as surviving.

`__pycache__` is deleted around every write. A mutation and its restore can
land inside one filesystem timestamp second, and Python's bytecode cache
validates on source mtime and SIZE -- so the mutated `.pyc` gets served for
the restored file, and a whole suite runs green against code the reader can
see is correct. Measured on 2026-08-18 against `chats.py`.

Run:  python -X utf8 test/manual/mutate_dictate.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "daemon" / "hub.py"
PIPELINE = REPO / "daemon" / "pipeline.py"
SUITE = "test/test_dictate.py"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

# Only the trees this harness rewrites. `REPO.rglob` reaches into `.venv`, so
# clearing repo-wide deletes the bytecode cache of every installed package and
# every pytest run afterwards recompiles the world -- which turned a four-run
# harness into one that had to be killed.
_CACHE_ROOTS = ("daemon", "test")

# (name, file, aimed `-k` expression, anchor, replacement)
#
# Every anchor is asserted UNIQUE before anything is written, so a mutation
# that lands nowhere reports as an error rather than as a survivor.
RULES = [
    ("a second dictation is refused while the first decodes", HUB,
     "test_a_second_dictation_is_refused_while_the_first_is_decoding",
     '        if self._decoding.locked():\n'
     '            raise AsrBusy("another dictation is still decoding")\n',
     '        # MUTATED: the second decode is allowed to start\n'),

    ("a dictation is refused while the recognizer is in use", HUB,
     "real_capture or abandoned_finalize",
     '        if self._recognizer_in_use():\n'
     '            raise AsrBusy("a turn is using the recognizer")\n',
     '        # MUTATED: nothing asks whether the engine is busy\n'),

    ("close() may not free the engine mid-finalize", PIPELINE,
     "abandoned_finalize",
     '        if not self._finalizing:\n'
     '            self._asr_in_use = False\n'
     '        self._session.close()\n',
     '        self._asr_in_use = False  # MUTATED: cleared even mid-finalize\n'
     '        self._session.close()\n'),

    ("the mark is cleared by the THREAD, not by the coroutine", PIPELINE,
     "abandoned_finalize",
     '        self._finalizing = True\n'
     '        await asyncio.to_thread(self._finalize_blocking)\n',
     '        self._finalizing = True\n'
     '        try:\n'
     '            await asyncio.to_thread(self._finalize_blocking)\n'
     '        finally:\n'
     '            self._asr_in_use = False  # MUTATED: the naive fix\n'),
]


def _clear_pycache() -> None:
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
                 for path in (HUB, PIPELINE)}
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
