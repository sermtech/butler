"""D-13 instrument for the splitter's clause cut and the two segment caps.

Break ONE rule at a time, then ask TWO questions with EXIT CODES ONLY:

  A. does the test the rule is named after FAIL (exit 1)?
  B. does the rest of the file still PASS with that test deselected (exit 0)?

One question is not enough: a mutation that reddens the whole file proves the
instrument is coupled rather than aimed. Where a rule honestly has more than
one witness, the row names all of them instead of pretending it has one.

Read pytest's exit code and never its output -- an earlier session in this
repository matched the word "FAILED" in the text, pytest colours that word,
and every mutation was reported as surviving (test/AGENTS.md).

The row worth running before touching `_cut_at_cap` is the FIRST one. It puts
the original defect back exactly: a clause mark matched anywhere in the head
rather than only at the end of a word. That version deleted the second half of
every token carrying an internal `,` `;` or `:` -- `1,000`, `12:30`, `3:1`, a
URL -- and it passed the whole of this file for weeks, because the one fixture
`test_nothing_is_lost_for_any_input` used had every mark sitting word-final.

`__pycache__` is deleted between runs, scoped to `daemon/` and `test/`: a
mutation the same LENGTH as the original, written and reverted inside one
filesystem timestamp second, is served from a stale `.pyc` and reads as a
survivor followed by a restore that will not go green.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_speech_caps.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEECH = REPO / "daemon" / "speech.py"
HUB = REPO / "daemon" / "hub.py"
SUITE = "test/test_speech_splitter.py"

# (label, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    ("a clause mark counts only at the END of a word", SPEECH,
     r'''_CLAUSE_TAIL = re.compile(r"[,;:][\"')\]]*$")''',
     r'''_CLAUSE_TAIL = re.compile(r"[,;:]")''',
     ["test_an_internal_mark_is_not_even_a_PREFERRED_boundary"],
     "ONE witness, and it had NONE until this harness ran. The obvious two "
     "-- the internal-mark table and the fuzz -- both stayed GREEN under "
     "this mutation, because un-anchoring the pattern deletes nothing: the "
     "head and the remainder are both word slices either way. The anchor "
     "decides WHERE the cut lands, the word unit decides WHETHER anything "
     "is lost, and only the second was asserted"),
    ("the head and the remainder are measured in the SAME unit", SPEECH,
     '    return " ".join(words[:cut]), " ".join(words[cut:])',
     '    head = " ".join(words[:cut]).rstrip(",;:-")\n'
     '    return head, " ".join(words[len(head.split()):])',
     ["test_nothing_is_lost_for_any_input",
      "test_cut_prefers_a_clause_boundary",
      "test_no_cut_ever_lands_inside_a_word"],
     "the ORIGINAL defect in miniature -- a head trimmed by characters and a "
     "remainder resumed by word index. THREE honest witnesses, because a "
     "trailing mark stripped off the head is itself a character deleted "
     "from the reply, which is what the no-loss property forbids"),
    ("a clause cut is not taken too early", SPEECH,
     "    cut = at + 1 if at >= 0 and at + 1 >= max(3, max_words // 2) "
     "else max_words",
     "    cut = at + 1 if at >= 0 else max_words",
     ["test_a_cut_at_a_clause_boundary_is_not_taken_too_early"],
     ""),
    ("only the OPENING of a reply gets the tight cap", HUB,
     "        opening = not s.speaking_started",
     "        opening = True",
     ["test_only_the_opening_of_a_reply_gets_the_tight_cap"],
     "this is the shipped defect, not a hypothetical: every sentence of "
     "every reply was cut at eight words"),
    ("the loose cap is the one later pieces get", SPEECH,
     "LATER_SEGMENT_MAX_WORDS = 16",
     "LATER_SEGMENT_MAX_WORDS = 8",
     ["test_later_segments_get_the_looser_cap",
      "test_only_the_opening_of_a_reply_gets_the_tight_cap"],
     "two honest witnesses: the module rule and the caller that picks "
     "between the two caps"),
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
