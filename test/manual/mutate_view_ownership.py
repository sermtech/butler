"""D-13 instrument for ownership (AC-238's mechanism, AC-246, AC-247).

`docs/parallel-agents.md` phase 6. Break ONE rule at a time, then ask TWO
questions with EXIT CODES ONLY:

  A. does the test the rule is named after FAIL (exit 1)?
  B. does the rest of the file still PASS with that test deselected (exit 0)?

One question is not enough: a mutation that reddens the whole file proves the
instrument is coupled rather than aimed.

Read pytest's exit code and never its output -- an earlier session in this
repository matched the word "FAILED" in the text, pytest colours that word,
and every mutation was reported as surviving (test/AGENTS.md).

**THE ROW TO RUN FIRST IS THE MUTE ONE.** It makes the daemon go quiet by
ABANDONING the turn, which is what the spec's own first draft proposed and
what the review overturned: it produces silence, an empty engine queue and a
re-armed session, so every assertion about the ROOM being quiet passes. Only
the assertion on the child catches it, and what it costs in the field is the
coding agent the owner looked away from, killed mid-edit.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_view_ownership.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "daemon" / "hub.py"
HOST = REPO / "daemon" / "adapters" / "brain_harness.py"
SUITE = "test/test_view_ownership.py"

# (label, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    ("AC-238: going quiet ABANDONS the turn instead of muting it", HUB,
     "            spoken = False\n        if not spoken:",
     "            await s.abandon()\n            return False\n"
     "        if not spoken:",
     ["test_looking_away_mid_reply_MUTES_the_rest_and_leaves_the_child_UP"],
     "THE ROW TO RUN FIRST. The room still goes quiet, so only the "
     "assertion on `harness.disposals` sees it"),
    ("AC-238: a device hears everything whatever it says it is showing", HUB,
     "        if not chat or self.showing is None:\n            return True\n"
     "        return self.showing == chat",
     "        return True",
     ["test_looking_away_mid_reply_MUTES_the_rest_and_leaves_the_child_UP"],
     "the mechanism deleted, leaving the ruling with nothing behind it"),
    ("AC-238: a device that never declared is muted too", HUB,
     "        if not chat or self.showing is None:\n            return True\n"
     "        return self.showing == chat",
     "        return self.showing == chat",
     ["test_a_device_that_never_said_what_it_shows_HEARS_EVERYTHING"],
     "the asymmetry removed: this is the hands-free path going silent, "
     "which is the failure the absence branch exists to prevent"),
    ("AC-246: a turn is resolved per call instead of fixed at its start", HUB,
     "        s.turn_chat = self.chat_for(s)",
     "        s.turn_chat = \"\"",
     ["test_a_device_showing_NOTHING_lands_in_the_conversation_the_daemon_HOLDS",
      "test_a_file_attached_in_A_is_still_As_after_the_device_speaks_in_B",
      "test_looking_away_mid_reply_MUTES_the_rest_and_leaves_the_child_UP",
      "test_a_SPOKEN_turn_names_its_conversation"],
     "FOUR honest witnesses and the breadth IS the evidence: a turn that "
     "cannot name its conversation cannot put it on `capture_start`, cannot "
     "take the attachment that names one, and cannot be muted against a "
     "view either"),
    ("AC-246: `ready` names no conversation", HUB,
     "                     turn_mode=self._turn_mode(), chat=self.chat_for(s),",
     "                     turn_mode=self._turn_mode(),",
     ["test_ready_tells_a_fresh_device_WHICH_conversation_it_is_on"],
     "the field-level gap `test_protocol_doc.py` cannot see: it compares "
     "frame NAMES only"),
    ("AC-246: `capture_start` names no conversation", HUB,
     "        await s.send(type=\"capture_start\", turn=turn, chat=s.turn_chat)",
     "        await s.send(type=\"capture_start\", turn=turn)",
     ["test_a_SPOKEN_turn_names_its_conversation"],
     ""),
    ("AC-247: a turn takes every pending attachment", HUB,
     "        held = tuple(p.att for p in self.attachments if p.chat == chat)\n"
     "        self.attachments = [p for p in self.attachments "
     "if p.chat != chat]",
     "        held = tuple(p.att for p in self.attachments)\n"
     "        self.attachments = []",
     ["test_a_file_attached_in_A_is_still_As_after_the_device_speaks_in_B"],
     "the behaviour before this phase, put back: the photo attached to the "
     "site chat arrives in the trading one"),
    ("AC-247: the file pends against the DEVICE and not the view", HUB,
     "        chat = self.chat_for(s)\n        s.attachments.append("
     "Pending(chat, att))",
     "        chat = self.chat_for(s)\n        s.attachments.append("
     "Pending(\"\", att))",
     ["test_a_file_attached_in_A_is_still_As_after_the_device_speaks_in_B"],
     "the route still answers 200 and the file is still on disk; only the "
     "conversation it belongs to is wrong"),
    ("AC-238's mechanism: the daemon ignores what a device says it shows", HUB,
     "        s.showing = chat or None",
     "        pass",
     ["test_a_device_says_what_it_is_SHOWING",
      "test_a_showing_frame_that_is_not_an_id_is_refused_and_changes_NOTHING",
      "test_a_device_that_SAYS_what_it_shows_overrules_the_pointer",
      "test_looking_away_mid_reply_MUTES_the_rest_and_leaves_the_child_UP",
      "test_a_file_attached_in_A_is_still_As_after_the_device_speaks_in_B"],
     "FIVE honest witnesses and the breadth IS the evidence: a frame the "
     "daemon accepts and discards leaves every consequence of the view "
     "unreachable at once"),
    ("AC-241: the record is written to whatever is CURRENT at write time",
     HOST,
     "        self._chats.append(chat_id, role, content)",
     "        self._chats.append(self._chats.current()[\"id\"], role, content)",
     ["test_a_turn_records_into_the_conversation_it_STARTED_in",
      "test_a_turn_RUNS_in_the_conversation_the_device_is_showing"],
     "the defect as it shipped: `turn()` threads a chat id through `plan`, "
     "`ensure` and `dispose` and the RECORD ignored it, so a turn answered "
     "into whichever conversation the pointer named when the answer "
     "finished. TWO honest witnesses -- the pointer can move mid-turn (the "
     "owner clicks a row) or be somewhere else the whole time (another "
     "device is showing this one)"),
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
            a = pytest(*[f"{SUITE}::{name}" for name in aimed])
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
