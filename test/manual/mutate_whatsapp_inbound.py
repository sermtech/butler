"""D-13 mutation harness for WhatsApp phases M6/M7 (docs/messaging.md).

Each entry breaks ONE rule, then asks two questions by pytest EXIT CODE only:

  A: does the NAMED test fail?          expected exit 1
  B: does the rest of the SCOPE pass?   expected exit 0

The scope for B is the WhatsApp inbound files plus the suites that cover the
code these mutations touch (typed turns, attachments, language-auto,
automations' RunSession) -- NOT the full suite, by the owner's test-scope
rule; a coupling outside that scope would not show here.

The rules, and the failure each one prevents:
  * a phone-origin turn is never spoken at the desk (AC-520);
  * `phone_chat: current` answers in the chat the owner last used (AC-519);
  * a voice reply speaks the language RECEIVED (AC-539), and that language
    comes from the owner's words, not the English warning prefix (AC-522);
  * an image from the chat reaches the turn's attachments (AC-560).

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_whatsapp_inbound.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
INBOUND = "test/test_whatsapp_inbound.py"
EVENTS = "test/test_whatsapp_events.py"
SCOPE = [INBOUND, EVENTS, "test/test_typed_turn.py",
         "test/test_attachments.py", "test/test_language_auto.py",
         "test/test_automations.py"]
RUN_TIMEOUT_S = 600

MUTATIONS = [
    (
        "a phone-origin turn is spoken at the desk",
        "daemon/hub.py",
        # In `_speaking` and nowhere else: flipping `headless` on the class
        # changes four behaviours at once, which is not this defect.
        "        if s is not None and s.headless:\n            return False\n",
        "        if s is not None and s.headless and s.typed_reason != "
        "\"whatsapp\":\n            return False  # MUTATED\n",
        # TWO honest witnesses: the second reads every synthesis, so the
        # desk's reading of the raw answer (links, code) shows up there too.
        [f"{INBOUND}::test_NOTHING_IS_SPOKEN_AT_THE_DESK_for_a_phone_turn",
         f"{INBOUND}::test_code_and_links_are_not_spoken_and_a_long_answer_"
         f"is_capped"],
    ),
    (
        "phone_chat current ignores the owner's last chat",
        "daemon/phone.py",
        "        chat = self.hub.last_owner_chat\n",
        "        chat = \"\"  # MUTATED: the held chat instead\n",
        [f"{INBOUND}::test_CURRENT_mode_continues_the_chat_the_owner_last_used",
         f"{INBOUND}::test_new_in_current_mode_moves_the_phone_to_the_fresh_"
         f"chat"],
    ),
    (
        "a voice reply in the wrong language",
        "daemon/phone.py",
        "    found = ports.voice_ids(hub.tts.info, language)\n",
        "    found = ports.voice_ids(hub.tts.info, \"\")  # MUTATED\n",
        [f"{INBOUND}::test_voice_text_sends_a_voice_note_IN_THE_LANGUAGE_"
         f"RECEIVED_then_the_text"],
    ),
    (
        "the language is decided from the prefixed transcript",
        "daemon/hub.py",
        "            s, text if s.language_words is None else s.language_words)",
        "            s, text)  # MUTATED: the prefix decides",
        [f"{INBOUND}::test_a_voice_note_is_transcribed_prefixed_and_its_"
         f"language_comes_from_the_words"],
    ),
    (
        "an image does not reach the turn's attachments",
        "daemon/phone.py",
        "            session.attachments.append(Pending(chat, att))\n",
        "            pass  # MUTATED: stored, never attached\n",
        [f"{INBOUND}::test_an_image_is_stored_and_ATTACHED_to_the_owner_turn",
         f"{INBOUND}::test_an_image_caption_is_the_question",
         f"{INBOUND}::test_a_document_follows_document_reading_like_a_page_"
         f"upload",
         f"{EVENTS}::test_end_to_end_a_message_is_answered_quoted_through_the_"
         f"sidecar"],
    ),
]


def run(args: list[str]) -> int:
    try:
        return subprocess.run(
            [str(PY), "-X", "utf8", "-m", "pytest", *args], cwd=str(REPO),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        print(f"       (did not finish inside {RUN_TIMEOUT_S}s)")
        return 2


def clear_caches() -> None:
    for root in (REPO / "daemon", REPO / "test"):
        for cache in root.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def main() -> int:
    bad = 0
    for name, rel, before, after, tests in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(before) != 1:
            print(f"[SKIP] {name}: {text.count(before)} matches in {rel}")
            bad += 1
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(before, after))
        assert path.read_bytes() != original, "the mutation did not apply"
        clear_caches()
        try:
            named = run([*tests, "-q"])
            rest = run([*SCOPE, "-q", "-m", "not slow",
                        *sum([["--deselect", t] for t in tests], [])])
        finally:
            path.write_bytes(original)
            clear_caches()
        ok = named == 1 and rest == 0
        bad += not ok
        print(f"[{'OK ' if ok else 'BAD'}] {name}: named exit {named}, "
              f"rest exit {rest}")
    assert all((REPO / rel).read_bytes().decode("utf-8").count("MUTATED") == 0
               for _, rel, *_ in MUTATIONS), "a mutation was left in place"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
