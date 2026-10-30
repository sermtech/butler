"""D-13 mutation harness for step 7.7, the spoken project switch (AC-31).

Each entry breaks ONE rule, then asks two questions with pytest EXIT CODES and
nothing else -- a previous session matched the word "FAILED" in pytest's
output, pytest colours that word, and every mutation was reported as
surviving:

  A: does the NAMED test fail?          expected exit 1
  B: does everything else still pass?   expected exit 0

The four rules, and the failure each one prevents. A near miss must be
REFUSED rather than falling back to the default working directory -- the
fail-open AC-31 is written against. Matching must be EXACT, because a
substring or a prefix is a guess and the cost of a guess is a tool-enabled
agent in the wrong repository. The phrase must be recognised only at the
START of an utterance, or describing the feature moves the agent. And the
resolver must re-check the directory, because that gate is the only thing
between an adapter holding a string and a child running in it.

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_project_switch.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
SWITCH = "test/test_project_switch.py"

# Tests that are RED before any mutation is applied, and are therefore noise in
# question B ("does everything else still pass"). One red test anywhere in the
# tier makes every row report `rest exit 1`, so the harness answers BAD for a
# mutation it caught perfectly -- which is how an instrument stops being read.
#
# The entry below is not flaky and not ours: it fails whenever the process
# running pytest cannot reach the input desktop, which is what an agent shell or
# a dropped remote session is [measured 2026-09-11, test/AGENTS.md]. Deselected
# rather than tolerated, so a SECOND unrelated failure still shows up as BAD.
# Empty this list once that test names its own precondition and skips.
BASELINE_RED = [
    "test/test_desk_dictation.py::test_the_install_line_is_generated_from_the_TABLE",
]

MUTATIONS = [
    (
        "a near miss falls back to the default",
        "daemon/hub.py",
        # `if` became `elif` 2026-09-11, when the ambiguous-name branch landed
        # above it (the owner's backlog item 4).
        "        elif found is None:\n"
        "            print(f\"[butler] turn {turn}: refusing to go to "
        "{heard!r}: it \"",
        "        elif False:  # MUTATED: an unknown name uses harness_cwd\n"
        "            print(f\"[butler] turn {turn}: refusing to go to "
        "{heard!r}: it \"",
        # THREE honest witnesses, named rather than pretending it has two: the
        # refusal is also what keeps an explicit unknown name from reaching the
        # agent as work [measured 2026-09-11].
        ["test_a_near_miss_is_refused_aloud_and_nothing_changes",
         "test_an_empty_projects_list_refuses_every_switch",
         "test_only_an_explicit_switch_refuses_a_name_it_does_not_know"],
    ),
    (
        "matching is exact",
        "daemon/settings.py",
        # A comprehension since 2026-09-11: the matcher collects EVERY hit so
        # it can tell one from two (`AmbiguousProject`). Since 2026-09-26 a
        # row answers to its label as well as its folder name (`_row_names`),
        # so the anchor moved from `project_name(path) == wanted` to a
        # membership test over both.
        "            if wanted in _row_names(row)}",
        "            if any(wanted in n for n in _row_names(row))}"
        "  # MUTATED: fuzzy",
        ["test_matching_is_exact_on_the_spoken_form"],
    ),
    (
        "the phrase is only recognised at the start",
        "daemon/settings.py",
        # The THIRD line is what makes this unique, and it also decides which
        # of the two loops is the honest target. They are byte-identical for
        # two lines, so the old anchor matched TWICE and this row had been
        # SKIPPING silently -- proving nothing for as long as nobody read the
        # output [found 2026-09-11].
        #
        # It aims at the BARE loop (`False`). Aimed at the explicit one it
        # SURVIVES, measured: every explicit prefix contains the word "project",
        # so no case in the parametrized test can tell `startswith` from `in`
        # there. The case with teeth is "I would work in butler if I could",
        # which only the bare prefixes reach.
        "        if cleaned.startswith(prefix):\n"
        "            name = cleaned[len(prefix):].strip()\n"
        "            return ProjectRequest(name, False) if name else None",
        "        if prefix in cleaned:  # MUTATED: anywhere in the utterance\n"
        "            name = cleaned.split(prefix, 1)[1].strip()\n"
        "            return ProjectRequest(name, False) if name else None",
        ["test_the_phrase_is_recognised_only_at_the_start"],
    ),
    (
        # The anchor moved 2026-09-11 with the list itself: the gate reads the
        # shortcuts pane's ROWS now, not the retired `projects` setting (the
        # owner's backlog item 4). Same rule, same aim, one source.
        "the resolver re-checks the directory",
        "daemon/butlerd.py",
        "        if cwd is not None and cwd not in allowed:",
        "        if False:  # MUTATED: the adapter's string is trusted",
        ["test_the_resolver_refuses_a_directory_nobody_configured"],
    ),
    (
        # AC-31's FIRST gate, and its source is the thing that changed. A hub
        # reading an empty list would refuse everything and look safe; a hub
        # reading the wrong list is what shipped for two days, so the mutation
        # is the drift rather than a deletion.
        #
        # The anchor moved again 2026-09-26: the matcher now reads
        # `self.shortcuts.all()` directly (rows, so a LABEL can match too)
        # rather than `project_paths()`, so mutating `project_paths()` no
        # longer touches this path at all -- that would have been the exact
        # silent drift D-13 exists to catch.
        "the spoken switch reads the shortcut rows",
        "daemon/hub.py",
        "                    found = settings_mod.match_project(\n"
        "                        heard.name, self.shortcuts.all())",
        "                    found = settings_mod.match_project(\n"
        "                        heard.name, ())  # MUTATED: no row is "
        "reachable by voice",
        # TWO honest witnesses [measured 2026-09-11]: the move itself, and the
        # turn AFTER it, which is the one that proves the agent is really there
        # rather than merely told to be.
        ["test_a_known_project_takes_the_next_turn_there",
         "test_work_after_going_there_runs_in_that_repository"],
    ),
    (
        # A chat is BORN in the project it names, in one call
        # (docs/filing-and-pacing.md 8, R-A; the MOVE this entry used to break
        # is deleted). Dropping the argument births the chat wherever the
        # previous one lived -- which no status check would notice.
        "a birth records the project it names",
        "daemon/adapters/brain_harness.py",
        "        return self._chats.rotate(project)[\"id\"]",
        "        return self._chats.rotate(None)[\"id\"]  # MUTATED",
        ["test_clicking_new_chat_on_a_project_births_it_there"],
    ),
]


# A mutated tier can HANG rather than fail, and this harness had no way to
# notice. Measured 2026-09-11: with the spawn gate mutated out, one tier run sat
# for 29 MINUTES against a 90-second baseline and had to be killed by PID from
# outside -- so the row reported nothing at all, and the operator's only signal
# was a source file still carrying a MUTATED comment. pytest-timeout is not
# installed here (test/AGENTS.md), so the deadline lives in the harness. 8x the
# baseline: long enough that CPU contention from another agent cannot trip it,
# short enough to fail the same afternoon.
RUN_TIMEOUT_S = 720


def run(args: list[str]) -> int:
    """pytest's EXIT CODE, never its output text -- and never a wait forever.

    A timeout answers 2, which is neither of the codes either question wants,
    so a hung run reads as BAD rather than as a pass. That is the honest
    reading: a mutation whose tier does not terminate has not been shown to be
    caught OR to be coupled.
    """
    try:
        return subprocess.run(
            [str(PY), "-X", "utf8", "-m", "pytest", *args], cwd=str(REPO),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        print(f"       (that run did not finish inside {RUN_TIMEOUT_S}s -- a "
              f"mutated suite can HANG rather than fail)")
        return 2


def nodeid(test: str) -> str:
    """`file.py::name`, or a bare name meaning this suite's own."""
    return f"test/{test}" if "::" in test else f"{SWITCH}::{test}"


def main() -> int:
    bad = 0
    for name, rel, before, after, tests in MUTATIONS:
        path = REPO / rel
        original = path.read_text(encoding="utf-8")
        if original.count(before) != 1:
            print(f"[SKIP] {name}: the mutation site is not unique in {rel} "
                  f"({original.count(before)} matches) -- the harness is "
                  f"wrong, not the code")
            bad += 1
            continue
        path.write_text(original.replace(before, after), encoding="utf-8")
        try:
            named = run([*(nodeid(t) for t in tests), "-q", "-m", "not slow"])
            rest = run(["test/", "-q", "-m", "not slow and not needs_gpu",
                        *sum([["--deselect", nodeid(t)] for t in tests], []),
                        *sum([["--deselect", t] for t in BASELINE_RED], [])])
        finally:
            path.write_text(original, encoding="utf-8")
        ok = named == 1 and rest == 0
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else 'BAD'}] {name}: named exit {named} "
              f"(want 1), rest exit {rest} (want 0)")
    print(f"all {len(MUTATIONS)} mutations were caught" if not bad
          else f"{bad} mutation(s) did not behave")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
