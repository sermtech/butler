# Project roots: what a project may be (BUILT)

Written 2026-10-29 against HEAD `f0c0a51`; approved ("ok great. do
everything") and built the same day: `shortcuts.whole_disk`,
`access.OWNER_ONLY`, `test/test_shortcuts.py`, `test/test_access_gate.py`.
AC-750..AC-754 are promoted to `plan.md`; AC-755..AC-759 stay reserved. Spec-local decision ids are
PJ-n. Provenance tags as `docs/proactive.md`.

## 0. The owner's decision [OWNER]

- 2026-10-29, on the release review's finding that `POST /api/shortcuts`
  accepts `C:\` as a project: "sure, do your spec recommendation."

## 1. What is true today [READ 2026-10-29]

- A project row's `path` is checked only for being Butler's own root
  directory twice (`daemon/shortcuts.py` `upsert`, ~line 820). `C:\`, `E:\`,
  `/` and the user's home are accepted.
- The file explorer contains every read and upload INSIDE the row's path
  (`daemon/files.py` `resolve_inside`, junction-safe). So a row at `C:\`
  makes the whole disk readable and writable through `/api/project/{id}/raw`
  and `/upload` [REVIEW 2026-10-29, finding 3].
- Since `docs/remote-access.md` only the owner and paired devices reach
  that route. A paired device is the remaining remote path (H2 made the
  standing-channel routes owner-only; project rows were left out).
- A coding agent started in a project runs as the user with tools
  (`--dangerously-skip-permissions`): it can already read the whole disk
  whatever the project is. This spec does not and cannot change that.
- Only `POST /api/shortcuts` creates or moves a row (`butlerd.py:953`). The
  desk's six rows are all ordinary folders [MEASURED 2026-10-29], so the
  rule below breaks none of them.

## 2. The rules

- **PJ-1 Owner-only.** `POST /api/shortcuts` and `DELETE
  /api/shortcuts/{sid}` join `access.OWNER_ONLY`: a paired device cannot add
  a project, move one, or change key bindings. The owner (this machine, the
  owner's Tailscale login) can, from any page.
- **PJ-2 Not a whole disk.** A row's path, after `Path.resolve()`, may not
  be a filesystem root (its `parent` is itself: `C:\`, `\\server\share\`,
  `/`) nor the user's home directory itself (`Path.home()`). `upsert`
  raises `ShortcutError` naming the path and why, so the page shows it
  (the existing 400 path). A folder INSIDE home is fine.
- **PJ-3 Existing rows are not rewritten.** The rule applies when a row is
  created or its path changed. A row already at a root keeps working and is
  named once at boot on stderr, so the owner can move it.
- **PJ-4 A real folder here** (added 2026-10-29). Through the route, a new
  or moved row's path must be absolute and an existing folder on THIS
  machine (`shortcuts.not_a_folder`). Found on the Mac mini: a row typed
  as `\VolumesTBowner\PROJECTS\ANA` was stored, and on macOS a
  backslash is a plain character, so the project opened nothing. Internal
  callers skip it (`must_exist=False`): a row may outlive its folder.

What is deliberately NOT here: an allowlist of parent folders (a setting
to maintain, for little gain over PJ-1 + PJ-2), and any limit on agents.

## 3. Criteria (AC-750..AC-759 reserved)

- **AC-750 (owner-only).** A paired device gets the gate's 403 on POST and
  DELETE `/api/shortcuts`; the owner reaches both (enumerated with the
  `test_access_gate` harness, handler never runs for the device).
- **AC-751 (no whole disk).** `upsert` refuses a drive root, `/` and
  `Path.home()`, each with a message naming the path; it accepts a folder
  inside home and an ordinary folder. Through the route: 400 and no row
  written.
- **AC-752 (old rows survive).** A `shortcuts.json` already holding a row
  at a root loads, the row is usable, and boot prints one line naming it.
- **AC-753 (guide).** `daemon/guide_features.py` says a project cannot be a
  whole drive or the home folder.
- **AC-754 (real folder).** The route answers 400 naming the path, and
  writes no row, for a relative path, a Windows spelling on macOS, or a
  folder that does not exist; renaming a row whose folder is gone still
  works.

## 4. Build plan

1. Red tests for AC-750..AC-752 (`test/test_access_gate.py`,
   `test/test_shortcuts*.py`), then `access.OWNER_ONLY` and
   `shortcuts.upsert`. Mutation-check each rule. [opus: security]
2. Guide row, `daemon/AGENTS.md`, `plan.md`, this spec's status.

## 5. Review of the remaining surfaces, 2026-10-29 [REVIEW]

An opus review of attachments, artifacts, the file explorer, `web/` and the
Tauri app (the parts the remote-access reviews had not covered) found no
Critical or High issue. Dispositions:

- M1 FIXED: an upload named `.env ` (trailing space) passed the SECRETS and
  SKIP checks and Windows wrote it as `.env` (verified). `files.clean_relative`
  now refuses any name ending in a dot or a space, for every explorer path.
- M2 FIXED by PJ-1: a paired device could add a project row anywhere and
  upload into it. Uploading into an EXISTING project stays open to devices:
  a file is content, not a standing channel (H2's line).
- L3 FIXED: `credentials`, `.npmrc`, `.netrc`, `.git-credentials`,
  `.pypirc` joined the SECRETS denylist (still a denylist, 3.4).
- L4 ACCEPTED: `POST /api/artifacts/show` trusts the `chat` id a local agent
  names, so one agent can post an artifact into another chat. It can only
  show what it could already read.
- L5 ACCEPTED: check-then-open race between `containment.file_inside` and
  the snapshot; an agent able to win it already has a shell.
- Held: served headers (`nosniff`, `CSP: sandbox`, polyglots served inert),
  explorer containment (`.butler` spellings, 8.3 names, junctions), no
  markup sink in `web/*.js`, links http(s) only with `noopener`, the Tauri
  capability set (`core:default`, no remote IPC, microphone for Butler's
  own origin only).
