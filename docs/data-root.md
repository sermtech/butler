# Data root -- code in one folder, the owner's home and Butler's records in another

RULED and BUILT 2026-09-26, written at HEAD `38baff1`. Criteria AC-410..AC-419.
They stay in this spec until the owner accepts them; `plan.md` is not touched.
**The live migration has NOT been run**: `E:\Butler-Data` does not exist yet,
and `start.bat` refuses to start until it does (section 5).

AC numbers start at 410 because the highest in `docs/` and `plan.md` was 396
(`docs/turn-feedback.md`) when this was written.

## 0. The owner's words

"we need to separate out server directory and data from user working data to
be safer." Then, choosing from options: "i dont want 3 separate folders to
manage. just two would be good enough like projects an earlier prototype." Recorded in
`CLAUDE.md`, backlog 2026-09-26 (second batch) item 1, RULED paragraph.

## 1. The ruling

- `E:\PROJECTS\butler\` is CODE only.
- `E:\Butler-Data\` is the DATA ROOT and the ROOT PROJECT'S working directory:
  the owner's files, `attachments\`, and an `AGENTS.md` that is the soul/core
  for everyday chats (so AC-386's 62 KB of developer contract is gone from
  them).
- Butler's own records are in ONE hidden folder, `E:\Butler-Data\.butler\`,
  which the file explorer skips.
- The repo becomes an ordinary row, "butler (code)". Chats filed under the
  repo path STAY there -- that is where they ran.
- Stated limit: a full-tools agent can still reach any folder. This prevents
  accidents, not a determined agent.

Declined: prototype-exact visible record folders beside the owner's files
(the explorer and agents would see them); moving only the data while the root
stays in the repo (keeps the 62 KB and an agent in Butler's source).

## 2. Paths, before and after

| What | Before (legacy) | After (`BUTLER_DATA=E:\Butler-Data`) |
|---|---|---|
| settings, shortcuts, mode, connections | `<repo>\state\*.json` | `<data>\.butler\*.json` |
| chats (+ agent `*.session.json`) | `<repo>\state\chats\` | `<data>\.butler\chats\` |
| memory notes | `<repo>\state\memory\` | `<data>\.butler\memory\` |
| rendered prompt files | `<repo>\state\prompt\` | `<data>\.butler\prompt\` |
| TLS cert and key | `<repo>\ssl\` | `<data>\.butler\ssl\` |
| `voice-trace.log` | `<repo>\state\` | `<data>\.butler\logs\` |
| `butler.log` (start.bat's redirect; APPENDED since 2026-09-27, one dated `=====` marker per start -- an overwrite used to wipe the previous run's only record) | `<repo>\` | `<data>\.butler\logs\` |
| attachments | `<repo>\state\attachments\` | `<data>\attachments\` |
| root project's cwd | `<repo>` | `<data>` |
| `SOUL.md`, `USER.md` (`docs/identity-memory.md` 11) | `<repo>\state\` | `<data>\` |

## 3. Resolution -- `daemon/paths.py`, the one place

`--data` flag > `BUTLER_DATA` > the legacy layout. Nothing in code names
`E:\Butler-Data`: `start.bat`, the owner's launcher, sets it. With nothing
set, every path is what it was before this module (an earlier prototype's "with
nothing set the two roots are equal").

- An explicitly-set data root must already exist and must not be inside the
  code tree -- a fail-fast `DataRootError`, never a silently empty Butler.
- `BUTLER_STATE` keeps its one old meaning, "Butler's records live here", so
  conftest and every scratch-daemon recipe work unchanged. Neither the cert
  nor the root project moves with it.
- The working directory is never consulted (an earlier prototype: "cwd is never the
  data root").
- `start.bat` asks `python -m daemon.paths records|logs|ssl` rather than
  spelling the layout a second time.

## 4. Criteria

- **AC-410** With no `BUTLER_DATA`, every path equals the legacy layout.
- **AC-411** With `BUTLER_DATA`, records are `<data>\.butler`, the cert
  `.butler\ssl`, logs `.butler\logs`, attachments `<data>\attachments`.
- **AC-412** Flag beats env; a set root that is missing, or inside the code
  tree, refuses to boot and names the migration command.
- **AC-413** `BUTLER_STATE` moves the records only.
- **AC-414** The explorer never lists `.butler`.
- **AC-415** The root row is minted from the DATA root, ENSURED on every boot
  (`ShortcutStore.migrate_projects`), so an already-migrated store gains it;
  the repo's row becomes ordinary and removable.
- **AC-416** `build_hub` reads every record, the attachments folder and the
  root from the resolver (D-18: asserted on the caller).
- **AC-417** The migration COPIES `state\`, `ssl\` and `butler.log*` into the
  new layout, moves nothing, and rewrites the old attachments path inside
  Butler's chat records only (an agent's session file is its memory).
- **AC-418** After migration every chat's project path still names a row:
  "butler (code)" holds the old root chats, "Butler" is the data root.
- **AC-419** The migration never overwrites, is idempotent, verifies by hash
  that nothing is left to copy, and refuses while the port listens.

```gherkin
Feature: Two folders

  Scenario: an unmigrated install is unchanged (AC-410)
    Given BUTLER_DATA is not set
    Then the daemon reads <repo>\state and the root project is the repo

  Scenario: a typo never boots an empty Butler (AC-412)
    Given BUTLER_DATA names a folder that does not exist
    When the daemon or start.bat starts
    Then it refuses and prints the migration command

  Scenario: migrate, then boot (AC-415, AC-417, AC-418)
    Given the legacy layout with four chats filed under the repo
    When migrate-data-root.py runs with --data <data>
    And the daemon boots with BUTLER_DATA=<data>
    Then the root project "Butler" is <data> and lists no chats
    And "butler (code)" lists the four chats
    And a typed turn in "Butler" runs with cwd <data>

  Scenario: the live daemon is protected (AC-419)
    Given something listens on the daemon's port
    When the migration runs without --dry-run
    Then it exits 3 and writes nothing

  Scenario: a rerun is a no-op (AC-419)
    Given a completed migration
    When it runs again
    Then every file in <data> is byte-identical
```

## 5. Migration and rollback

`scripts/migrate-data-root.py`, run ONCE by the owner with Butler stopped:

```
stop.bat
.venv\Scripts\python.exe -X utf8 scripts\migrate-data-root.py --data E:\Butler-Data --dry-run
.venv\Scripts\python.exe -X utf8 scripts\migrate-data-root.py --data E:\Butler-Data
start.bat
```

It creates `<data>`, copies with robocopy's never-overwrite rule
(/XC /XN /XO), hash-verifies that nothing is left to copy (robocopy /L's
question; exit 4 otherwise), labels the repo row "butler (code)", adds the
"Butler" row at `<data>`, writes a starter `AGENTS.md` only if absent (no
language instruction, `language.md` D2), and prints the rollback. It refuses
(exit 3) while anything listens on `--port` (default 8140) and kills nothing.

ROLLBACK: nothing in the repo is changed or deleted. Stop Butler, revert
`start.bat`'s `BUTLER_DATA` line, start again -- the daemon reads
`state\` as before. Anything written after the migration exists only under
`<data>\.butler`. Once the new layout has been lived with, move `state\` out
of the repo into a quarantine folder (an earlier prototype's pattern), then delete it.

The laptop Butler (`laptop-host`) is the same code: until it is migrated
too, its `start.bat` will refuse with the same message. Migrate it with its
own `--data`, or set `BUTLER_DATA` there before pulling this change.

## 6. Build record, 2026-09-26

Built: `daemon/paths.py`; `butlerd.build_hub`, `ssl_context`, `main --data`;
`trace.path`; `files.SKIP`; `ShortcutStore.migrate_projects` ensures the root;
`scripts/make-cert.py`; `start.bat`; `scripts/migrate-data-root.py`. Tests:
`test/test_paths.py`, `test/test_migrate_data_root.py`, one each in
`test_shortcuts.py` and `test_files.py`. Seven mutations each reddened their
test (flag precedence, fail-fast, `BUTLER_STATE`, no-overwrite, `.butler`
skip, root from the repo, attachments in records).

Verified on a SCRATCH copy of the owner's `state\` and `ssl\` (in `%TEMP%`,
port 8813, killed by its PID): the root "Butler" was the scratch data root
with 0 chats, "butler (code)" held the 4 old root chats, the explorer listed
`attachments` and `AGENTS.md` and skipped `.butler`, a typed turn in the root
answered with that cwd and the starter heading, an upload landed in
`<data>\attachments`, "remember that" wrote into `.butler\memory`, and an
identity edit reached the agent. The owner's `state\` hashed byte-identical
before and after.

Not done here: the live migration, the laptop, and quarantining `state\`.
`harness_cwd`'s blank default is still `<repo>\TMP` (a scratch folder in the
code tree); it is reached only by a caller with no project.
