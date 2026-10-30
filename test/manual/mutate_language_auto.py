"""D-13 instrument for automatic language selection (AC-207..AC-215).

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

Two rows run against a DIFFERENT suite than the others, because the rule they
break lives at a seam two files assert. Each row names its own suite.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_language_auto.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB = REPO / "daemon" / "hub.py"
PORTS = REPO / "daemon" / "ports.py"
DIALOGUE = REPO / "daemon" / "dialogue.py"
BUTLERD = REPO / "daemon" / "butlerd.py"
SETTINGS = REPO / "daemon" / "settings.py"

AUTO = "test/test_language_auto.py"
SEAMS = "test/test_seams.py"
SAY = "test/test_tts_say.py"
SETTINGS_API = "test/test_settings_api.py"

# (label, suite, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    # The LATCH. The one rule whose absence is invisible in any single-turn
    # test: the detector is right about every long utterance, so a daemon
    # with no latch answers every normal turn correctly and flips the voice
    # only on "sim".
    ("the latch holds under an abstention", AUTO, HUB,
     "        if detected:\n"
     "            s.language = detected\n"
     "        elif s.language is None:\n"
     "            s.language = self._default_language()",
     "        s.language = detected or self._default_language()",
     ["test_a_one_word_answer_keeps_the_language_it_was_asked_in"],
     ""),

    # The abstention FLOOR. Dropping it to zero makes the detector commit to
    # its 0.514 guess on "sim", which is the measured wrong answer.
    ("the confidence floor abstains", AUTO, DIALOGUE,
     "    if confidence < DETECT_MIN_CONFIDENCE:\n"
     "        return None",
     "    if False:\n"
     "        return None",
     ["test_a_one_word_answer_keeps_the_language_it_was_asked_in"],
     "one honest witness: every other case in the file is a long utterance "
     "the detector names at >= 0.999, so no floor can change them"),

    # THE LIVE DEFECT THIS WORK FIXED. `_speak` handed the engine the raw
    # setting while `speak_start` announced a resolved voice. Putting it back
    # must redden the wire-versus-room assertions and nothing else.
    ("the engine gets the RESOLVED voice", AUTO, HUB,
     "                async for chunk in self.tts.synthesize(segment, voice,",
     "                async for chunk in self.tts.synthesize(segment, self._voice(),",
     ["test_the_turn_is_answered_in_the_language_it_was_asked_in",
      "test_the_voice_follows_the_language_ACROSS_turns",
      "test_under_auto_a_stored_voice_does_NOT_win"],
     "three honest witnesses and the breadth IS the evidence: every claim "
     "about which voice the ROOM heard goes through this one line, and that "
     "is exactly why the defect survived a green suite for a day"),

    # The same defect asserted from the settings side, which is where the
    # corrected caller-assertion lives (D-18).
    ("the engine gets the RESOLVED voice (settings side)", SETTINGS_API, HUB,
     "                async for chunk in self.tts.synthesize(segment, voice,",
     "                async for chunk in self.tts.synthesize(segment, self._voice(),",
     ["test_the_engine_is_asked_with_the_RESOLVED_voice"],
     "the row above proves the same rule in the other file; both are kept "
     "because the two suites reach it by different paths"),

    # `auto` must never reach a port. The engine rejects the literal string
    # outright, and pinning anything at all is measurably worse than pinning
    # nothing (test/manual/spike_asr_unpinned.py).
    ("auto never pins the recognizer", AUTO, HUB,
     "        if want == AUTO_LANGUAGE:\n"
     "            want = \"\"",
     "        if False:\n"
     "            want = \"\"",
     ["test_auto_never_pins_the_recognizer"],
     ""),

    # A stored voice must stop deciding under `auto`, or the feature is
    # decoration on the owner's own daemon.
    ("a stored voice stops deciding under auto", AUTO, HUB,
     "        chosen = \"\" if self._language() == AUTO_LANGUAGE else self._voice()",
     "        chosen = self._voice()",
     ["test_under_auto_a_stored_voice_does_NOT_win"],
     ""),

    # And the page has to SAY so. A control that is overruled and still drawn
    # as live is AC-7b, which is the half a behaviour test cannot see.
    ("the dimming says the voice stopped deciding", AUTO, SETTINGS,
     "        if str(self._values.get(\"language\", \"\")) == AUTO_LANGUAGE:",
     "        if False:",
     ["test_under_auto_a_stored_voice_does_NOT_win",
      "test_off_auto_the_voice_is_live_again"],
     "two honest witnesses, and they are different claims: one is that the "
     "control is dimmed while auto is on, the other that it comes back"),

    # A pinned locale is not second-guessed. The obvious implementation --
    # always detect -- passes every other row here.
    ("a pinned language is believed", AUTO, HUB,
     "        setting = self._language()\n"
     "        if setting and setting != AUTO_LANGUAGE:\n"
     "            s.language = setting\n"
     "            return s.language",
     "        setting = self._language()\n"
     "        if False:\n"
     "            s.language = setting\n"
     "            return s.language",
     ["test_a_pinned_language_is_not_second_guessed"],
     ""),

    # The BCP-47 primary-subtag match. Without it `voice_ids(info, "pt")`
    # answers nothing and the detector has no voice to pick, so the whole
    # feature silently falls through to the engine's default.
    ("a bare subtag matches a regioned voice", SEAMS, PORTS,
     "        if have == want or (broad and _primary_subtag(have) == want):",
     "        if have == want:",
     ["test_a_language_with_no_region_matches_the_primary_subtag"],
     ""),

    # The candidate set is the ENGINE's and regions collapse into it.
    ("the candidate set collapses regions", SEAMS, PORTS,
     "    subtags = {_primary_subtag(str(v.get(\"language\") or \"\"))",
     "    subtags = {str(v.get(\"language\") or \"\").lower()",
     ["test_the_detector_is_offered_only_languages_a_voice_speaks"],
     ""),

    # A fixed line must READ the turn's language rather than being a constant.
    # The hub half.
    ("a fixed line reads the turn's language (hub)", AUTO, HUB,
     "                              dialogue.line(self.ASR_FAILED, s.language),",
     "                              self.ASR_FAILED,",
     ["test_a_fixed_line_is_spoken_in_the_turns_language"],
     ""),

    # And the adapter half, which no hub test can see.
    ("a fixed line reads the turn's language (adapter)", AUTO,
     REPO / "daemon" / "adapters" / "brain_endpoint.py",
     "                yield Say(dialogue.line(self.OUT_OF_ROOM, ctx.language),\n"
     "                          tier=\"conversation\")",
     "                yield Say(self.OUT_OF_ROOM, tier=\"conversation\")",
     ["test_the_adapters_fixed_lines_read_the_turns_language"],
     ""),

    # A replay detects on its OWN text. Falling back to the conversation's
    # language is the obvious implementation and is wrong for a message an
    # hour old.
    ("a replay detects on the message", SAY, BUTLERD,
     "    language = dialogue.detect_language(\n"
     "        text, ports.voice_languages(hub.tts.info)) or hub._default_language()",
     "    language = hub._default_language()",
     ["test_a_replay_is_read_in_the_MESSAGES_own_language"],
     ""),
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
    for suite in sorted({m[1] for m in MUTATIONS}):
        baseline = pytest(suite)
        print(f"baseline {suite}: exit {baseline} (want 0)")
        if baseline != 0:
            print("a suite is not green before any mutation; nothing below "
                  "would mean anything")
            return 1

    caught = 0
    for label, suite, path, anchor, replacement, aimed, note in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        if original.count(anchor) != 1:
            print(f"[ERROR] {label}: the anchor appears "
                  f"{original.count(anchor)} times in {path.name}; a "
                  f"mutation that lands nowhere reads as a survivor")
            continue
        path.write_text(original.replace(anchor, replacement),
                        encoding="utf-8")
        try:
            a = pytest(*[f"{suite}::{name}" for name in aimed])
            deselect = []
            for name in aimed:
                deselect += ["--deselect", f"{suite}::{name}"]
            b = pytest(suite, *deselect)
        finally:
            path.write_text(original, encoding="utf-8")
        restored = pytest(suite)
        ok = a == 1 and restored == 0
        caught += ok
        print(f"[{'CAUGHT' if ok else 'SURVIVED'}] {label}  ({suite})\n"
              f"    aimed  exit {a} (want 1) -- {', '.join(aimed)}\n"
              f"    rest   exit {b} (want 0)\n"
              f"    restore exit {restored} (want 0)"
              + (f"\n    note: {note}" if note else ""))
    print(f"\n{caught}/{len(MUTATIONS)} caught")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
