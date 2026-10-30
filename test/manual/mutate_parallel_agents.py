"""D-13 instrument for phase 5 (AC-241, AC-237; docs/parallel-agents.md).

Break ONE rule at a time, then ask TWO questions with EXIT CODES ONLY:

  A. does the test the rule is named after FAIL (exit 1)?
  B. does the rest of the file still PASS with that test deselected (exit 0)?

One question is not enough: a mutation that reddens the whole file proves the
instrument is coupled rather than aimed.

Read pytest's exit code and never its output -- an earlier session in this
repository matched the word "FAILED" in the text, pytest colours that word,
and every mutation was reported as surviving (test/AGENTS.md).

The first two rows are the two the spec names, and the first is the one that
matters most: a child lookup that ignores the chat id answers the owner from
another project's repository with a real agent on a real session file, and
nothing else in this suite can see it -- every transcript, every spoken line
and every count in the file is correct while it happens.

The third arrived with the 2026-09-12 review and is the one place AC-60
cannot reach: a DELETED conversation has no next turn, so the spec comparison
that invalidates every other child never runs for it.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_parallel_agents.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOST = REPO / "daemon" / "adapters" / "brain_harness.py"
SUITE = "test/test_parallel_agents.py"

# (label, file, [(anchor, replacement), ...], [aimed tests], note)
#
# A row may carry MORE THAN ONE edit, and the first row is why. Mutating the
# lookup in `plan()` alone left the whole suite green in the aimed tests and
# red everywhere else: `ensure()` still disposed by chat id, so the host
# reused the wrong child's SPEC and then spawned a second one anyway -- a
# state neither the old design nor the new one can reach. A mutation has to
# produce the DEFECT, which here is the single-child host as it was before
# this phase, and that takes both lookups.
MUTATIONS = [
    ("AC-241: the child registry is keyed by NOTHING (the old one-slot host)",
     HOST,
     [("        spec = self.desired_for(chat)\n"
       "        held = self._children.get(chat[\"id\"])",
       "        spec = self.desired_for(chat)\n"
       "        held = next(iter(self._children.values()), None)"),
      ("        chat_id = chat[\"id\"]\n"
       "        if chat_id in self._children:",
       "        chat_id = chat[\"id\"]\n"
       "        if self._children:"),
      ("        held = self._children.pop(chat_id, None)",
       "        held = (self._children.pop(chat_id, None) or\n"
       "                (self._children.pop(next(iter(self._children)))\n"
       "                 if self._children else None))")],
     ["test_two_conversations_hold_two_children",
      "test_going_back_to_a_conversation_reuses_ITS_child",
      "test_the_daemons_teardown_takes_every_child",
      "test_the_cap_refuses_the_eleventh_and_names_where_the_slots_are",
      "test_nothing_is_queued_when_a_slot_frees",
      "test_the_cap_is_a_SETTING_and_takes_effect_on_the_next_turn",
      "test_a_replacement_at_the_cap_is_not_an_eleventh_child",
      "test_a_failed_turns_ONE_replay_still_works_at_the_cap",
      "test_the_cap_cannot_be_bypassed_by_a_spoken_project_switch",
      "test_a_waiting_agent_is_named_in_the_refusal",
      "test_deleting_a_conversation_DISPOSES_its_child_and_frees_the_slot"],
     "one child, whoever asks. TEN witnesses, and the breadth is not a fault "
     "in the aim: this file's subject IS the registry, and with one child the "
     "cap can never be reached either, so every cap test goes with it. The "
     "aim was measured OUTSIDE the file instead, which is the question worth "
     "asking -- test_brain_harness, test_chats, test_harness_question and "
     "test_conversation together lose exactly ONE test, "
     "test_a_new_conversation_replaces_the_held_child, and that one is a "
     "witness of this same rule rather than a bystander [MEASURED "
     "2026-09-12: 1 failed, 159 passed]. ELEVEN since the delete row "
     "joined it: a registry with one slot cannot hold the second child a "
     "delete is supposed to leave alone"),
    ("AC-237: the cap is raised by one inside ensure()", HOST,
     [("        if held is None and self.live() >= self.cap():",
       "        if held is None and self.live() >= self.cap() + 1:")],
     ["test_the_cap_refuses_the_eleventh_and_names_where_the_slots_are",
      "test_the_cap_is_a_SETTING_and_takes_effect_on_the_next_turn",
      "test_the_cap_cannot_be_bypassed_by_a_spoken_project_switch",
      "test_a_waiting_agent_is_named_in_the_refusal",
      "test_nothing_is_queued_when_a_slot_frees",
      "test_deleting_a_conversation_DISPOSES_its_child_and_frees_the_slot"],
     "the mutation the spec asks for by name. FIVE honest witnesses: every "
     "test that reaches the cap stops reaching it, and the queue one is the "
     "widest -- a message that was refused is sent instead. SIX with the "
     "delete row, which spawns its third child instead of being refused"),
    ("AC-241/AC-245: deleting a conversation leaves its child running", HOST,
     [("        await self.dispose(chat_id)\n"
       "        return self._chats.delete(chat_id)",
       "        return self._chats.delete(chat_id)")],
     ["test_deleting_a_conversation_DISPOSES_its_child_and_frees_the_slot"],
     "the defect as it shipped, and it is the one place AC-60 cannot reach: "
     "the spec comparison invalidates a child on the NEXT turn and a deleted "
     "conversation has no next turn, so the agent stayed up holding a slot "
     "with nothing in any UI able to open or stop it"),
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
         "--no-header", "-m", "not slow", "-p", "no:cacheprovider"],
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
    for label, path, edits, aimed, note in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        mutated = original
        missed = [a for a, _ in edits if original.count(a) != 1]
        if missed:
            print(f"[ERROR] {label}: {len(missed)} anchor(s) do not appear "
                  f"exactly once in {path.name}; a mutation that lands "
                  f"nowhere reads as a survivor")
            continue
        for anchor, replacement in edits:
            mutated = mutated.replace(anchor, replacement)
        path.write_text(mutated, encoding="utf-8")
        try:
            a = pytest(*[f"{SUITE}::{name}" for name in aimed])
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
