"""D-13 instrument for the chat LIST and the past-conversation route (AC-164).

`daemon/chats.py` has written one flat JSON file per conversation since AC-35,
and until step 1 of `docs/familiar-ui.md` only the current one was reachable.
Eight rules make the rest reachable without making the filesystem reachable
with them, and each is broken on its own here.

Two questions per mutation, with exit codes only:

  A. do the AIMED tests fail?            (want 1)
  B. does the rest of the file pass?     (want 0)

One question is not enough. A mutation that reddens the whole file proves the
instrument is COUPLED, not that it is aimed (test/AGENTS.md). A rule with more
than one honest witness NAMES them all rather than pretending it has one --
"newest first" has two, because the order decides both what the top row is and
which end of the list survives the bound.

The row that matters most is the PATH one. `ChatStore.find` matches an id
against the ids it read off disk, so a traversal is unrepresentable rather
than refused; the mutation puts the obvious implementation back -- join the id
onto the store's root -- and the test must catch a real chat record being read
from one directory ABOVE `state/chats/`.

Read pytest's EXIT CODE and never its output: pytest colours the word
"FAILED", and an earlier session matched that word in the text and reported
every mutation as surviving.

`__pycache__` is deleted around every write. A mutation and its restore can
land inside one filesystem timestamp second, and Python's bytecode cache
validates on source mtime and SIZE -- so the mutated `.pyc` gets served for
the restored file, and a whole suite runs green against code the reader can
see is correct. Measured on 2026-08-18 against this very file's subject.

Run:  python -X utf8 test/manual/mutate_chats.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHATS = REPO / "daemon" / "chats.py"
BUTLERD = REPO / "daemon" / "butlerd.py"
SUITE = "test/test_chats.py"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

# Only the trees this harness rewrites. `REPO.rglob` reaches into `.venv`, so
# clearing repo-wide deletes the bytecode cache of every installed package.
_CACHE_ROOTS = ("daemon", "test")

# (name, file, aimed `-k` expression, anchor, replacement)
#
# Every anchor is asserted UNIQUE before anything is written, so a mutation
# that lands nowhere reports as an error rather than as a survivor.
RULES = [
    ("the list is NEWEST first", CHATS,
     "test_the_chat_list_is_newest_first or test_the_chat_list_is_bounded",
     '        return [{"id": c["id"], "title": _title(c), "turns": _turns(c),\n',
     '        oldest = sorted(self._records(), key=lambda c: c["updatedAt"])\n'
     '        self._records = lambda: oldest  # MUTATED: oldest first\n'
     '        return [{"id": c["id"], "title": _title(c), "turns": _turns(c),\n'),

    ("a title is TRUNCATED", CHATS,
     "test_a_title_is_the_first_thing_the_owner_said_truncated",
     '            return (text if len(text) <= TITLE_CHARS\n'
     '                    else text[:TITLE_CHARS].rstrip() + "...")\n',
     '            return text  # MUTATED: served whole\n'),

    ("a title is the first thing the OWNER said", CHATS,
     "test_a_title_is_the_first_thing_the_owner_said_truncated",
     '        if m.get("role") != "user":\n'
     '            continue\n',
     '        pass  # MUTATED: the first message, whoever said it\n',),

    ("an id is a KEY and never a PATH", CHATS,
     "test_a_chat_id_cannot_reach_a_file_outside_the_chats_directory",
     '        for chat in self._records():\n'
     '            if chat["id"] == chat_id:\n'
     '                return {"id": chat["id"], "harness": chat["harness"],\n'
     '                        "messages": _window(chat["messages"], limit)}\n'
     '        return None\n',
     '        chat = self._read(self.root / f"{chat_id}.json")  # MUTATED\n'
     '        if chat is not None:\n'
     '            return {"id": chat["id"], "harness": chat["harness"],\n'
     '                    "messages": _window(chat["messages"], limit)}\n'
     '        return None\n'),

    ("a corrupt record is SKIPPED, not carried", CHATS,
     "test_one_corrupt_record_does_not_hide_the_others or "
     "test_a_corrupt_chat_record_is_loud_and_not_fatal",
     '            chat = self._read(path)\n'
     '            if chat is not None:\n'
     '                found.append(chat)\n',
     '            found.append(self._read(path))  # MUTATED: kept as None\n'),

    ("the LIST is bounded", CHATS,
     "test_the_chat_list_is_bounded",
     '                for c in (self._records()[:limit] if limit\n'
     '                          else self._records())]\n',
     '                for c in self._records()]  # MUTATED: every chat\n'),

    ("a past conversation's TRANSCRIPT is bounded", CHATS,
     "test_a_past_conversation_is_bounded_like_the_current_one",
     '                return {"id": chat["id"], "harness": chat["harness"],\n'
     '                        "messages": _window(chat["messages"], limit)}\n',
     '                return {"id": chat["id"], "harness": chat["harness"],\n'
     '                        "messages": list(chat["messages"])}  # MUTATED\n'),

    ("an unknown id is a 404 that NAMES it", BUTLERD,
     # THREE honest witnesses: the id nobody has, the id that names a file,
     # and the id of a record nothing can read. Each is a different way for
     # the route to answer 200 over a conversation that is not there.
     "test_an_unknown_chat_id_is_a_404_that_names_it or "
     "test_a_chat_id_cannot_reach_a_file_outside_the_chats_directory or "
     "test_one_corrupt_record_does_not_hide_the_others",
     '        if chat is None:\n'
     '            return web.json_response(\n'
     '                {"error": f"no conversation with id {chat_id!r}"}, '
     'status=404)\n',
     '        chat = chat or {"id": chat_id, "harness": "", "messages": []}\n'
     '        # MUTATED: an empty 200 for a conversation nobody has\n'),
]


def _clear_pycache() -> None:
    for root in _CACHE_ROOTS:
        for cache in (REPO / root).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def _pytest(select: str) -> int:
    """Run one selection and answer its EXIT CODE. Output is discarded.

    `not slow`, because the bottom of this suite starts a REAL pi child and
    takes minutes; what is under test here is a route over a JSON file.
    """
    args = [PY, "-X", "utf8", "-m", "pytest", SUITE, "-q", "-m", "not slow"]
    if select:
        args += ["-k", select]
    return subprocess.run(
        args, cwd=str(REPO),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode


def main() -> int:
    originals = {path: path.read_text(encoding="utf-8")
                 for path in (CHATS, BUTLERD)}
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
