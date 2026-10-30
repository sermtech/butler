"""D-13 mutation harness for AC-64/AC-65, the rolling transcript.

Each entry breaks ONE rule, then asks two questions with pytest EXIT CODES and
nothing else -- a previous session matched the word "FAILED" in pytest's
output, pytest colours that word, and every mutation was reported as
surviving:

  A: do the NAMED tests fail?           expected exit 1
  B: does everything else still pass?   expected exit 0

Three rules, and the failure each one prevents.

The ROUTER must be given the conversation. Without it every utterance is
classified as though it were the first thing said, and the two routes the
router answers ITSELF -- `answer` and `clarify` -- never reach the substantive
brain, so a follow-up is answered from nothing. This is the defect the owner
reported as having to explain themselves at every prompt.

The transcript must hold what was SPOKEN, never what was generated (D-20).
`SatelliteSession.spoken` is the only thing that knows the difference -- a
segment cut off mid-stream by barge-in is deliberately not appended there --
and the mutation swaps it for the user's own text, which is the shape of any
"just record the reply" mistake. This rule has THREE honest witnesses and all
of them are named rather than one being pretended away: the router test
asserts the exact assistant text in the router's request body, and the
late-turn test asserts the ordinary case still records what was spoken, so
recording the wrong thing reddens both as well as the test aimed at the
recording.

A new conversation must CLEAR it. A history that survived the rotation makes
"new conversation" a lie told by the one model that runs on every single
utterance.

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_history.py

`__pycache__` is deleted around every run, and this is not tidiness: a
same-length mutation written and reverted inside one filesystem timestamp
second is served from the bytecode cache, which is how a whole suite once ran
green against source the reader could see was correct (test/AGENTS.md). The
anchor count is checked BEFORE anything is written, so a mutation that lands
nowhere reports as an error rather than as a survivor, and every restore is
re-run and must come back exit 0.

[EXECUTED 2026-08-19: all three OK -- aimed=1, rest=0, restored=0.]
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"

# (file, anchor, replacement, *tests that must go red)
MUTATIONS = [
    (
        "daemon/dialogue.py",
        '        messages += [{"role": role, "content": said} for role, said in\n'
        '                     ctx.history]\n',
        "",
        "test/test_conversation.py::test_the_router_sees_the_conversation",
    ),
    (
        "daemon/hub.py",
        '        heard = " ".join(seg for seg in s.spoken if seg.strip()).strip()',
        '        heard = " ".join(seg for seg in [text] if seg.strip()).strip()',
        "test/test_conversation.py::"
        "test_history_holds_what_was_SPOKEN_and_not_what_was_generated",
        "test/test_conversation.py::test_the_router_sees_the_conversation",
        "test/test_conversation.py::"
        "test_a_turn_that_finishes_late_records_no_answer",
    ),
    (
        "daemon/hub.py",
        "        self.history.clear()\n",
        "",
        "test/test_conversation.py::test_a_new_conversation_clears_the_history",
    ),
]


def clear_pycache() -> None:
    for d in REPO.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run(args: list[str]) -> int:
    clear_pycache()
    return subprocess.run([str(PY), "-X", "utf8", "-m", "pytest", *args],
                          cwd=REPO, capture_output=True).returncode


def main() -> int:
    failures = 0
    for path, anchor, replacement, *targets in MUTATIONS:
        p = REPO / path
        original = p.read_text(encoding="utf-8")
        found = original.count(anchor)
        if found != 1:
            print(f"ERROR anchor appears {found} times in {path}; a mutation "
                  f"that lands nowhere would report as a survivor")
            failures += 1
            continue
        p.write_text(original.replace(anchor, replacement), encoding="utf-8")
        try:
            aimed = run([*targets, "-q"])
            deselect = [a for t in targets for a in ("--deselect", t)]
            rest = run(["test/", "-q", "-m", "not slow and not needs_gpu",
                        *deselect])
        finally:
            p.write_text(original, encoding="utf-8")
        restored = run([*targets, "-q"])
        ok = (aimed == 1 and rest == 0 and restored == 0)
        failures += 0 if ok else 1
        print(f"{'OK  ' if ok else 'BAD '} {path}: aimed={aimed} (want 1) "
              f"rest={rest} (want 0) restored={restored} (want 0)  <- "
              f"{', '.join(targets)}")
    clear_pycache()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
