# Todo list: one main list, tagged by project (BUILT, 2026-10-16)

Owner request, 2026-10-16: Butler keeps its OWN todo list beside its own
memory, so it moves with the data root to any machine, using the decisions of
an earlier prototype's catch-all `MEMORY/TODO.md` (one item per todo, a project route, a
reason, who and when, never twice). The todo runs in the MAIN project's
context and knows every other project, so it can read context there or change
files there.

## 1. Decisions

- **TD-1. The list is the existing task store.** `<records>/library/tasks.json`
  (`docs/automations.md` AU-1), beside `<records>/memory`. No `TODO.md`: a
  second home is the drift AU-1 already rejected. Portable because nothing
  names a path; `BUTLER_DATA` moves it.
- **TD-2. An agent's task goes to the main list.** Whatever conversation calls
  `task_add`, the task is the ROOT row's. Its project is a TAG, `about` (a row
  id): the model names a project from the Projects block, else the
  conversation's project is used (none in the main project). Where it is
  STORED is still never a model argument (AU-9).
- **TD-3. A reason.** Optional `why`, one clause, at most 120 characters,
  scanned like the text.
- **TD-4. Never twice.** The same words (any case or spacing) already OPEN on
  the main list are answered with the open task; nothing is written and the
  per-reply cap is not spent. A done task does not block.
- **TD-5. Butler knows every project.** A Projects block (each row's name and
  folder, the main one marked) goes right after the Task rules, wherever the
  tools are. A harness agent may read or change files in those folders;
  SOUL.md's ask-first still governs changes. The endpoint brain has no file
  tools, so for it the block only names projects.
- **TD-6. The owner's own tasks are unchanged.** The page and the voice
  ("add a task ...") still file under the project the owner is in, untagged.

Who and when are the existing `source` and `createdAt` fields.

## 2. Acceptance criteria

- **AC-671.** An agent task is stored on the main list, tagged with its
  conversation's project (`test_library.py`, `test_library_voice.py` route
  test).
- **AC-672.** `about` names a project by its listed name, without case; an
  unknown name is refused and writes nothing.
- **AC-673.** `why` is kept, capped at 120 and scanned.
- **AC-674.** An open duplicate is answered with the existing task and not
  written.
- **AC-675.** The Projects block lists every row's name and folder, marks the
  main one, sits right after `TASK_RULES`, and only where the tools are;
  `build_hub` delivers it (`test_identity.py`).
- **AC-676.** `TASK_RULES` names the main list, `about`, `why` and "already
  open", within its 500-character cap.
- **AC-677.** pi's extension declares and POSTS `about` and `why`
  (`test_library_voice.py`).

## 3. Not done

- The claude and codex harnesses still get no task tool.
- No live turn has exercised it; tests run on fake engines.
