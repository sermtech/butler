"""D-13 instrument for attachments (AC-197..AC-204, docs/attachments.md).

Break ONE rule at a time, then ask TWO questions with EXIT CODES ONLY:

  A. does the test the rule is named after FAIL (exit 1)?
  B. does the rest of the file still PASS with that test deselected (exit 0)?

One question is not enough: a mutation that reddens the whole file proves the
instrument is coupled rather than aimed.

Read pytest's exit code and never its output -- an earlier session in this
repository matched the word "FAILED" in the text, pytest colours that word,
and every mutation was reported as surviving (test/AGENTS.md).

The row worth running before touching anything here is the LAST one. A brain
that speaks "I cannot see that" and sends the image anyway satisfies every
content assertion in the suite, and what comes back is a confident answer
about an image the model never received -- which is the only failure in this
feature that is worse than an error.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_attachments.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ATTACH = REPO / "daemon" / "attachments.py"
HUB = REPO / "daemon" / "hub.py"
ENDPOINT = REPO / "daemon" / "adapters" / "brain_endpoint.py"
BUTLERD = REPO / "daemon" / "butlerd.py"
SUITE = "test/test_attachments.py"

# (label, file, anchor, replacement, [aimed tests], coupling note)
MUTATIONS = [
    ("AC-198: the BYTES decide, not the filename", ATTACH,
     "    for magic, ext, media, kind in _MAGIC:\n"
     "        if head.startswith(magic):\n"
     "            return ext, media, kind",
     "    ext = (name.rsplit('.', 1)[-1] or '').lower() if '.' in name else ''\n"
     "    if ext in ('png', 'jpg', 'jpeg', 'gif'):\n"
     "        return ext, f'image/{ext}', 'image'\n"
     "    for magic, ext, media, kind in _MAGIC:\n"
     "        if head.startswith(magic):\n"
     "            return ext, media, kind",
     ["test_the_BYTES_decide_and_not_the_filename"],
     "the obvious wrong implementation: trust the extension first"),
    ("AC-199: an attachment is taken ONCE", HUB,
     "        held = tuple(p.att for p in self.attachments if p.chat == chat)\n"
     "        self.attachments = [p for p in self.attachments "
     "if p.chat != chat]",
     "        held = tuple(p.att for p in self.attachments if p.chat == chat)",
     ["test_an_attachment_is_taken_ONCE",
      "test_the_next_turn_gets_it_and_the_utterance_is_UNTOUCHED"],
     "two honest witnesses: the store-level read and the turn that must "
     "leave nothing behind for the next question"),
    ("AC-197: the OWNER's ceiling and not just the schema's", BUTLERD,
     "        ceiling = min(ceiling,\n"
     "                      int(hub.settings.get(\"attachment_max_mb\",\n"
     "                                           ATTACHMENT_DEFAULT_MB)) * 1_000_000)",
     "        pass",
     ["test_a_body_past_the_ceiling_is_a_413_naming_it"],
     "`dictate_max_seconds`'s own defect put back: reading only .maximum "
     "makes the control inert"),
    ("AC-200: `vision` reads NOTHING", ATTACH,
     '    if mode == "vision":\n        return ""',
     "    if False:\n        return \"\"",
     ["test_vision_mode_reads_NOTHING_and_that_is_the_setting_working"],
     ""),
    ("AC-200: a document that will not parse still attaches", ATTACH,
     "    except Exception as e:                                # noqa: BLE001\n"
     "        print(f\"[butler] could not read any text out of {path.name} ({e!r}). \"\n"
     "              f\"The file is still attached; only its text is missing.\")\n"
     "    return \"\"",
     "    except Exception:\n        raise\n    return \"\"",
     ["test_a_document_that_will_not_parse_still_ATTACHES"],
     ""),
    ("AC-815: an image goes to the model that ANSWERS", ENDPOINT,
     "== \"image\"]\n"
     "        target = self._resolver.resolve(\"reply\")",
     "== \"image\"]\n"
     "        target = self._resolver.resolve(\"router\" if pictures else \"reply\")",
     ["test_an_image_goes_to_the_model_that_ANSWERS",
      "test_a_model_the_CONNECTION_says_is_blind_is_refused_out_loud"],
     "TWO honest witnesses: the blind check reads the ANSWERING model's "
     "entry in the listing, so a picture routed elsewhere is not refused"),
    ("AC-204: a blind model is refused BEFORE the request", ENDPOINT,
     "                yield Say(CANNOT_SEE, tier=\"conversation\")\n"
     "                yield Done(\"conversation\")\n"
     "                return",
     "                yield Say(CANNOT_SEE, tier=\"conversation\")",
     ["test_a_model_the_CONNECTION_says_is_blind_is_refused_out_loud"],
     "THE ROW TO RUN FIRST. It still SAYS the refusal, so every content "
     "assertion passes; only the ask count catches it"),
    ("AC-204: silence from the connection is TRIED", ENDPOINT,
     "        return models.sees_images(entry or {}) is False",
     "        return models.sees_images(entry or {}) is not True",
     ["test_a_connection_that_says_NOTHING_is_tried",
      "test_an_image_rides_on_the_USER_message_as_a_content_part",
      "test_an_endpoint_that_refuses_the_image_is_reported_in_ITS_words",
      "test_an_image_goes_to_the_model_that_ANSWERS"],
     "flattening None to False, which refuses every model on every server "
     "that does not report modalities -- which is most of them. FOUR honest "
     "witnesses and the breadth IS the evidence: every picture test in the "
     "file stops sending a picture, because that is what the mutation does"),
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
