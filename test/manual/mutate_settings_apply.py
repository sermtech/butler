"""D-13 mutation harness for AC-713..AC-716: settings applied with no owner
restart (`daemon/apply_settings.py`).

Each entry breaks ONE rule, then asks two questions by pytest EXIT CODE only:

  A: does the NAMED test fail?                      expected exit 1
  B: does everything else in SCOPE still pass?      expected exit 0

Question B is scoped to the files that exercise this feature (`SCOPE`), not
the whole tier: the full fast tier is about five minutes per row, and every
other caller of the touched modules is in the list below. Say so when quoting
a result.

Files are mutated and restored as BYTES, so a CRLF file comes back
byte-identical, and `__pycache__` under `daemon/` and `test/` is removed
before every run (test/AGENTS.md: a same-length edit inside one mtime second
is otherwise served from the stale .pyc).

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_settings_apply.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
SUITE = "test/test_settings_apply.py"
SCOPE = [SUITE, "test/test_settings_api.py", "test/test_smart_endpointer.py",
         "test/test_restart.py", "test/test_settings_sheet_page.py",
         "test/test_parallel_agents.py", "test/test_harness_project_limit.py"]
BASELINE_RED: list[str] = []
RUN_TIMEOUT_S = 600

MUTATIONS = [
    ("a recognizer is swapped in before its warm-up",
     "daemon/butlerd.py",
     "        await fresh.warm()\n        hub.asr = fresh",
     "        hub.asr = fresh  # MUTATED\n        await fresh.warm()",
     ["test_the_new_recognizer_is_warmed_before_it_is_swapped_in"]),
    ("the swap keeps the booted latency",
     "daemon/butlerd.py",
     '"latency": prefs["asr_latency"]})',
     '"latency": asr_engine_opts["latency"]})  # MUTATED',
     # TWO honest witnesses: both assert the installed latency.
     ["test_a_latency_change_reaches_the_next_capture_with_no_restart",
      "test_the_new_recognizer_is_warmed_before_it_is_swapped_in"]),
    ("no live swaps are wired",
     "daemon/butlerd.py",
     '{"asr_latency": swap_asr, "endpointer": swap_endpointer}',
     '{}',
     ["test_a_latency_change_reaches_the_next_capture_with_no_restart",
      "test_the_new_recognizer_is_warmed_before_it_is_swapped_in",
      "test_an_endpointer_change_reaches_the_next_capture",
      "test_a_failed_warm_keeps_the_running_engine_and_says_so"]),
    ("the endpointer setting is inert again (config wins)",
     "daemon/butlerd.py",
     'return os.environ.get("BUTLER_ENDPOINTER") or str(prefs["endpointer"])',
     'return os.environ.get("BUTLER_ENDPOINTER") or names.get('
     '"endpointer", "smartturn")  # MUTATED',
     # FOUR honest witnesses: the two live-swap tests boot with the setting
     # at none, so a boot that ignores it breaks their first assertion.
     ["test_the_end_of_turn_setting_is_read_at_boot",
      "test_a_config_key_for_the_endpointer_is_named_and_loses",
      "test_an_endpointer_change_reaches_the_next_capture",
      "test_a_failed_warm_keeps_the_running_engine_and_says_so"]),
    ("an endpointer is wired without its warm-up",
     "daemon/butlerd.py",
     "        if fresh is not None:\n            await fresh.warm()\n"
     "        hub.endpointer = fresh",
     "        hub.endpointer = fresh  # MUTATED: never warmed",
     ["test_an_endpointer_change_reaches_the_next_capture",
      "test_a_failed_warm_keeps_the_running_engine_and_says_so"]),
    ("a turn in flight does not hold the restart",
     "daemon/hub.py",
     "        if self._orphaned or any(s.turn_by_chat for s in self.sessions):",
     "        if False:  # MUTATED",
     ["test_a_restart_setting_waits_for_the_turn_then_restarts",
      "test_a_whole_clip_decode_or_an_orphaned_turn_holds_the_restart"]),
    ("the restart ignores what is running",
     "daemon/apply_settings.py",
     "                if busy is None and settled:",
     "                if settled:  # MUTATED",
     ["test_a_restart_setting_waits_for_the_turn_then_restarts"]),
    ("an agent mid-prompt reads as idle",
     "daemon/adapters/brain_harness.py",
     "        return any(cid not in self._idle_since for cid in self._children)",
     "        return False  # MUTATED",
     ["test_a_coding_agent_mid_prompt_holds_the_restart"]),
    ("an agent held idle blocks the restart",
     "daemon/adapters/brain_harness.py",
     "        return any(cid not in self._idle_since for cid in self._children)",
     "        return bool(self._children)  # MUTATED",
     ["test_a_coding_agent_mid_prompt_holds_the_restart"]),
    ("a value put back still restarts",
     "daemon/apply_settings.py",
     "                pending = self._settings.pending()\n",
     "                pending = self._settings.pending() or ['x']  # MUTATED\n",
     ["test_putting_the_value_back_cancels_the_restart"]),
    ("the app never starts the applier",
     "daemon/butlerd.py",
     "            hub.applier.start()",
     "            pass  # MUTATED",
     ["test_the_running_app_restarts_itself_through_the_settings_route"]),
    ("the frame does not carry pending",
     "daemon/butlerd.py",
     "                            pending=hub.settings.pending())",
     "                            )  # MUTATED",
     ["test_the_route_and_the_frame_carry_what_is_pending"]),
    ("pending counts live settings too",
     "daemon/settings.py",
     "        self._booted = {s.key: self.get(s.key) for s in SCHEMA if s.restart}",
     "        self._booted = {s.key: self.get(s.key) for s in SCHEMA}  # MUTATED",
     ["test_the_store_says_what_it_has_not_applied"]),
]


def clear_pycache() -> None:
    for top in ("daemon", "test"):
        for cache in (REPO / top).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def run(args: list[str]) -> int:
    """pytest's EXIT CODE, never its text; a hung run answers 2."""
    clear_pycache()
    try:
        return subprocess.run(
            [str(PY), "-X", "utf8", "-m", "pytest", *args, "-p",
             "no:randomly"], cwd=str(REPO), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        print(f"       (did not finish inside {RUN_TIMEOUT_S}s)")
        return 2


def nodeid(test: str) -> str:
    return f"test/{test}" if "::" in test else f"{SUITE}::{test}"


def main() -> int:
    bad = 0
    for name, rel, before, after, tests in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        crlf = b"\r\n" in original
        b = before.replace("\n", "\r\n") if crlf else before
        a = after.replace("\n", "\r\n") if crlf else after
        hits = original.count(b.encode())
        if hits != 1:
            print(f"[BAD] {name}: the mutation site matched {hits} times in "
                  f"{rel} -- the harness is wrong, not the code")
            bad += 1
            continue
        path.write_bytes(original.replace(b.encode(), a.encode()))
        try:
            named = run([*(nodeid(t) for t in tests), "-q"])
            rest = run([*SCOPE, "-q", "-m", "not slow and not needs_gpu",
                        *sum([["--deselect", nodeid(t)] for t in tests], []),
                        *sum([["--deselect", t] for t in BASELINE_RED], [])])
        finally:
            path.write_bytes(original)
        assert path.read_bytes() == original, f"{rel} was not restored"
        ok = named == 1 and rest == 0
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else 'BAD'}] {name}: named exit {named} "
              f"(want 1), rest exit {rest} (want 0)", flush=True)
    clear_pycache()
    print(f"all {len(MUTATIONS)} mutations were caught" if not bad
          else f"{bad} mutation(s) did not behave")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
