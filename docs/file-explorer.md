# A file explorer per project (BUILT, 2026-09-26)

**OVERTURNED IN PART 2026-10-08 (`docs/rich-chat.md` RC-12, RC-13).** The owner
ruled that the explorer previews EVERY kind (images, audio, video, CSV as a
table) and takes a dropped file as an upload. FE-4 below no longer holds,
and the explorer is no longer read-only: `GET /api/project/{id}/raw` serves
bytes and `POST /api/project/{id}/upload` adds a new file. Everything else
here stands.

**BUILT 2026-09-26, both halves.** The daemon half is `daemon/files.py` and
the two routes; the page half is `web/files.js`, `#filesBtn` and
`#filesPanel` (`web/AGENTS.md` carries the mechanism). Phase 0's mock was
skipped: the owner delegated the design, and the build was judged from
screenshots of the real page instead (`TMP/explorer-out/`). Phase 6 was
proved with a file created BESIDE a real turn rather than by the agent, so
that no model was asked to write into a real repository. The criteria stay
here until the owner accepts them. The text below is the spec as written.

Written against HEAD `90ebca5`. Nothing here is an
accepted criterion: AC-303..AC-322 are RESERVED for this spec and stay here
until the owner accepts them; `plan.md` is not touched. It assumes
`docs/sidebar-collapse.md` (AC-293..AC-302) lands first, because both change
the header, and says where it depends on it.

Provenance tags: [READ] read from source, [MEASURED] observed in a run,
[DECIDED] a choice this spec makes, [OPINION] a judgement, "from memory" where
a claim was not checked here.

## 0. The owner's words

CLAUDE.md, backlog 2026-09-26 item 2:

> "I would like to have a minimizable explorer tree to see the files per
> project more easily on the interface itself, just like Orca."

The owner delegated the design ("make sensible choices"); every decision below
carries its reason and the losing option. **Editing is out of scope** -- the
explorer reads, and nothing in it writes, renames, moves or deletes.

## 1. What Butler has today [READ]

- A project is a `ShortcutStore` row (`daemon/shortcuts.py:201`, fields `id`,
  `path`, `label`); `ShortcutStore.paths()` (`:469`) is BOTH of AC-31's gates,
  `get(sid)` (`:523`) raises `KeyError` naming the known ids, `row_for(path)`
  maps a directory back to its row, and `root_id()` names Butler's own row.
- A chat BELONGS to a project, set at birth (AC-287..AC-292,
  `docs/filing-and-pacing.md` 8); the page's `currentProjectId()`
  (`web/app.js:5838`) answers which project the conversation on screen is in,
  and the header names it (`#chatProject`, AC-292).
- There is a per-project route precedent, singular: `POST /api/project/{id}/stop`
  (`daemon/butlerd.py:2715`). The new routes follow it rather than inventing a
  plural `/api/projects/`.
- File-type sniffing from magic bytes already exists: `attachments.sniff(head,
  name)` (`daemon/attachments.py:128`). The explorer reuses it to NAME a
  binary file's type; it does not reuse attachment storage.
- Anything that walks a disk goes through `asyncio.to_thread` (D-25), as the
  chat-search walk does.
- View state that must survive a reload and agree across devices lives in
  `ShortcutStore` beside `sidebar_collapsed` and `expanded_projects` (AC-8 bans
  browser storage; AC-41 bans the page naming a setting).
- Nothing in Butler lists or reads a project's files for the page today.

## 2. Prior art: Orca's explorer [READ, `E:\PROJECTS\other-project` at `da6d483a`]

- **Where it lives.** A tab of the RIGHT sidebar, lazily mounted:
  `src/renderer/src/components/right-sidebar/right-sidebar-panel-content.tsx:
  6,27`. The right sidebar's open state, tab and width are persisted
  (`src/renderer/src/store/slices/persisted-ui-write-baseline.ts:13-16,42-45`);
  the chord is Mod+Shift+E ("keep explorer on Mod+Shift+E (VS Code muscle
  memory)", `src/shared/keybindings/definitions-core-1.ts:198-203` and
  `definitions-core-2.ts:105`).
- **Scope.** The root is the ACTIVE worktree's path
  (`src/renderer/src/components/right-sidebar/FileExplorer.tsx:37,49`), and it
  loads only while the panel is visible
  (`src/renderer/src/components/right-sidebar/file-explorer-reset.ts:3-22`).
- **Lazy, one level per expand.** Main-process `fs:readDir` does one
  `readdir(dirPath, { withFileTypes: true })` and maps each entry to
  `{ name, isDirectory, isSymlink }`
  (`src/main/ipc/filesystem/filesystem-read-handlers.ts:44-54`). Directories
  first, then a natural locale compare (`src/shared/file-name-sort.ts`). No
  entry-count cap; rows are virtualised (`FileExplorerVirtualRows.tsx:2`).
- **Ignore rules.** `.git` and `node_modules` are HIDDEN outright:
  `entry.name !== '.git' && entry.name !== 'node_modules'`
  (`src/renderer/src/components/right-sidebar/file-explorer-entries.ts:3-5`).
  Git-ignored files are SHOWN, dimmed and italic, computed by asking git
  (`use-file-explorer-ignored-paths.ts:40-114`, `FileExplorerRow.tsx:225-235`).
  Dotfiles have a per-worktree show/hide toggle.
- **Containment.** Every path is realpath'd and checked against the allowed
  roots (`src/main/ipc/filesystem-auth.ts:85-139`); the core check rejects a
  relative result of `..`, `../...` or an absolute path, with the Windows
  trap written down: "on Windows relative() returns absolute across drives,
  which would bypass drive-traversal checks" and "Windows path.relative()
  ignores drive/root casing" (`src/main/ipc/filesystem-path-containment.ts:
  12-28`).
- **Reading.** A null byte in the first 8192 bytes means binary
  (`src/main/ipc/filesystem/filesystem-file-content-inspection.ts:7,55-63`);
  text up to 50 MB goes to Monaco; images, SVG and PDF are returned base64
  (`:6-20`). A click opens a preview TAB in the editor
  (`useFileExplorerHandlers.ts:57-157`).
- **Refresh.** A `@parcel/watcher` per worktree pushes change events
  (`src/main/ipc/filesystem-watcher-handlers.ts:30-103`), plus a manual
  refresh button.
- **Persistence of the tree.** The expanded-folder set is in memory only,
  keyed by worktree, and NOT persisted
  (`src/renderer/src/store/slices/editor/actions/explorer-dir-state.ts`).
- **Accessibility.** Rows are plain buttons with a rotating chevron; no
  `role="tree"`, `treeitem` or `aria-expanded` was found in
  `FileExplorerRow.tsx` / `FileExplorerVirtualRows.tsx`. Butler does better
  here on purpose.
- **Writes.** Rename, delete, new file/folder, duplicate, drag-drop and
  undo exist (`useFileExplorerDragDrop.ts`, `file-explorer-batch-deletion.ts`,
  ...). None is copied.

What Butler copies: right-hand placement, the chord, active-project scope,
lazy one-level listing, dirs-first sort, the hidden `.git`/`node_modules`,
realpath-then-contain, the 8 KiB null-byte probe, and an in-memory expanded
set. What it does not: the editor, image/PDF bytes, the watcher, git
decorations, git-ignore dimming, and every write.

## 3. The design [DECIDED]

### 3.1 Two daemon routes, one new module

`daemon/files.py` holds pure functions (no `hub`, no aiohttp) so the rules
are tested without a socket; `butlerd.py` adds two thin routes that call them
through `asyncio.to_thread` (D-25).

**`GET /api/project/{id}/files?path=<relative>`** lists ONE directory level.

- `{id}` must be a current `ShortcutStore` row: an unknown id is a 404 naming
  it (the `get()` message). The ROOT is that row's `path`, resolved with
  `strict=True`; a row whose directory is gone is a 409 naming the path. A
  caller can never supply the root.
- `path` is optional (`""` is the root), POSIX or Windows separators, and is
  refused with a 400 naming the rule BEFORE touching the disk when it:
  is absolute (`/x`, `\x`), carries a drive (`C:`) or UNC prefix, contains a
  `..` component, contains `:` anywhere (NTFS alternate data streams,
  `file.txt:secret`), contains a NUL, or has a component that is a Windows
  reserved device name (`CON`, `NUL`, `AUX`, `PRN`, `COM1-9`, `LPT1-9`, with
  or without an extension).
- It is then joined to the root, resolved with `strict=True`, and must satisfy
  `resolved.is_relative_to(root_resolved)` -- both sides resolved, so case,
  8.3 short names, trailing dots and links are canonical on both. A symlink or
  JUNCTION that resolves outside the root is a 403. `Path.resolve` following
  junctions on Windows is from memory (it uses the final-path handle since
  Python 3.8); phase 1 proves it with a real `mklink /J` rather than trusting
  that.
- A path with a component in the SKIP set (3.2) is a 404 naming the rule, so
  "not listed" and "not reachable" are one rule.
- The answer: `{"project": id, "path": "<relative, forward slashes>",
  "entries": [...], "truncated": bool, "total": n, "skipped": [names]}`.
  Each entry is `{"name", "kind": "dir"|"file"|"link", "size" (files only),
  "mtime", "outside": bool (links that resolve outside the root), "locked":
  bool (3.4)}`. Directories first, then `str.casefold` name order. At most
  **1000 entries** per level after sorting; beyond that `truncated: true` and
  `total` says how many there were.

**`GET /api/project/{id}/file?path=<relative>`** previews one file, read-only.

- The same id, path and containment rules; the target must be a regular file
  (a directory is a 400).
- Reads at most **256 KiB**. A NUL byte in the first **8192** bytes (Orca's
  probe) means binary: the answer carries `binary: true`, the size and, when
  `attachments.sniff` recognises the bytes, the type it names -- and NO
  content. Otherwise the bytes are decoded as UTF-8 with `errors="replace"`
  and the answer says `lossy: true` when any replacement happened, so a
  mis-decoded file is labelled rather than silently shown as if clean.
- The answer is JSON (`{"path", "size", "text", "truncated", "binary",
  "lossy", "type"}`), never the file's bytes under the file's own media type.
  Serving raw bytes would let an `.html` or `.svg` in a project run script in
  Butler's origin.
- Each preview is one line in the daemon log (`file preview <id> <path>
  <bytes>`), because it is a read with no chat record to show for it.

### 3.2 What is skipped [DECIDED]

A fixed SKIP set of names is never listed and never served: `.git`,
`node_modules`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`,
`.mypy_cache`, `.ruff_cache`. The listing names what it skipped at that level
(`skipped`), and the page draws one quiet line ("2 folders hidden: .git,
node_modules"), because a folder that is not drawn looks exactly like one that
does not exist -- AC-229's argument.
*Losing option:* listing them dimmed and expandable -- one `node_modules` level
can be tens of thousands of entries, Orca hides the same two outright
(`file-explorer-entries.ts:3-5`), and `.git/config` can carry a credential in
a remote URL.
*Also declined for v1:* honouring `.gitignore`. Orca asks git for it
(`use-file-explorer-ignored-paths.ts`); that is a subprocess per listing and a
matcher to get right, and the SKIP set covers the heavy cases. Dotfiles are
shown.

### 3.3 The panel [DECIDED]

- **Desk: a right-hand panel beside the conversation**, 300 px, like Orca's
  right sidebar. A header button, `#filesBtn` (a folder glyph, label "Files",
  `aria-expanded`, `aria-controls`), toggles it; Ctrl+Shift+E toggles it too,
  Orca's own chord. The page adds a `data-key` for it because it binds it
  (the tooltip rule in `web/AGENTS.md`). That no browser reserves
  Ctrl+Shift+E on Windows is from memory; phase 4 checks Chrome and Edge.
  *Losing option:* a fold inside the left project tree under each project --
  it puts two trees in one 264 px column, and the owner asked for "just like
  Orca".
- **Scope: the project of the conversation on screen**, `currentProjectId()`,
  re-rooted whenever that changes (opening a chat in another project, a birth,
  the daemon's `opened` frame). The panel's head names the project and its
  directory. A project whose row was removed shows a line saying so, and no
  tree.
  *Losing option:* a project picker inside the panel -- a second way to name a
  project, and browsing another project's files is one press away through the
  tree anyway (opening its newest chat).
- **Minimizable and remembered.** Open-or-shut is `files_open` in
  `ShortcutStore`, beside `sidebar_collapsed`, written by
  `POST /api/shortcuts {"files_open": bool}` and refused by name when not a
  bool. Default shut. The expanded folders are PAGE memory, per project, and
  die with the page (Orca's own choice, `explorer-dir-state.ts`).
  *Losing option:* persisting the expanded folders -- an unbounded list of
  path strings in the store for a convenience nobody asked for.
- **Rows.** One quiet line each: caret for folders, name, and for files a
  muted size at the right. No icons per file type, no boxes, the only fill is
  the focused row -- the tree's own visual rules ("keep it visually easy").
  A `link` that is `outside` is drawn struck and cannot be opened; a `locked`
  file carries a small lock and its preview explains why.
- **Preview.** SUPERSEDED 2026-10-27 by `docs/lantern-ui.md` CU-16 (Step 6c):
  the preview now sits UNDER the tree, which stays on screen with its one
  tab stop; the previewed row is `aria-selected`; the head holds the path,
  the size, Copy path and Close (icon buttons); code, markdown, CSV and
  media draw through `artifacts.js`. The original text, kept for history:
  Pressing a file replaces the tree, inside the panel, with the
  preview: a back control, the relative path, the size, a "Copy path" button
  (the ABSOLUTE path, so it can be pasted into a prompt), then the text in a
  `pre` set with `textContent` -- never `innerHTML` -- in the reading face,
  no wrapping, scrolling both ways. Truncated, binary and lossy previews say
  so in one line above the text. Back returns to the tree with its expanded
  set and scroll position intact.
  *Losing option:* a wide reading overlay over the conversation -- it hides
  the chat the owner is working in, which is the thing the panel sits beside.
- **Refresh.** No watcher. Open folders are re-listed when the panel opens,
  when the refresh control in its head is pressed, and when a turn ENDS in any
  conversation of the project it shows, off the turn-end frame the page
  already receives -- the moment a coding agent is most likely to have
  written.
  *Losing option:* Orca's watcher -- a watcher lifecycle per project in the
  daemon plus a new outbound frame, for a read-only panel.
- **Keyboard.** `role="tree"` with `treeitem`s carrying `aria-expanded`,
  `aria-level` and `aria-setsize`/`aria-posinset`, one roving `tabindex`:
  Up/Down move, Right opens or enters, Left closes or goes to the parent,
  Enter and Space open a folder or preview a file, Home/End jump. In a
  preview, Backspace and the close button shut it (since CU-16). The desk panel
  is NOT modal and takes no case in the global Escape handler (the look
  popover's precedent -- that handler's last case stops a reply, AC-112).
- **Phone (900 px and below).** `#filesBtn` is one of the controls
  `layoutHeaderControls()` sheds into `#headerMorePop` (the 390 px header has
  no room for it -- `docs/sidebar-collapse.md` 8). The panel opens as a
  full-width sheet from the right over its own scrim, under the settings
  surface in the z-index ladder, with a close button; Escape closes it, as a
  new case AFTER the drawer's (panel, drawer, files sheet, STOP, clear). A
  phone never writes `files_open` (D-2, as the drawer never writes
  `sidebar_collapsed`).

### 3.4 Security: what this adds, stated plainly

**Ruling R2 (CLAUDE.md, 2026-09-16) accepted that the tailnet is the
boundary:** two unauthenticated JSON posts can already start a
full-capability coding agent in any directory a row names, and a row can name
any directory. That agent can read every file there. The explorer does not
change who can reach Butler, but it ADDS A DIRECT READ: **any tailnet caller
can list and read any file under any project row with one unauthenticated
GET**, with no agent, no model call and no chat record. Because
`POST /api/shortcuts` can add a row naming any directory, "under a project
row" bounds nothing against a caller who writes a row first -- that is R2's
accepted posture reaching a new verb, and it is the owner's to know rather
than a defect this spec can fix.

What it bounds, all enforced in the daemon and none in the page:

- **Roots are rows only.** The caller names a row id, never a directory; the
  root is resolved from the store on every request.
- **Containment after resolution**, links and junctions included (3.1).
- **No writes, no deletes, no execution, no raw bytes.** Two GET routes that
  answer JSON; nothing is served under a file's own media type.
- **Caps**: 1000 entries per level, 256 KiB per preview.
- **The SKIP set** keeps `.git` (remote URLs can hold tokens) and dependency
  trees out.
- **A small SECRETS set is listed but never previewed:** `.env`, `.env.*`,
  `*.pem`, `*.key`, `*.pfx`, `*.p12`, `id_rsa*`, `id_ed25519*`,
  `credentials.json`. The entry carries `locked: true` and the preview route
  answers 403 naming the rule. This is a denylist and is incomplete by
  construction [OPINION]; it exists because the explorer turns reading a
  secret into one GET that leaves no transcript, where an agent reading it at
  least leaves a chat record.
  *Losing option:* previewing everything, since R2 concedes more already --
  rejected for the no-trace reason just given.
- **Every preview is logged** (3.1).

## 4. Decisions [DECIDED; the owner may overrule any one line]

- **FE-1. Routes are `/api/project/{id}/files` and `/api/project/{id}/file`**,
  singular, following `POST /api/project/{id}/stop`. The backlog brief wrote
  `/api/projects/{row_id}/files`; one spelling per resource beats matching the
  brief.
- **FE-2. Lazy, one level per request, capped at 1000.** Orca's shape;
  losing option: a recursive listing -- unbounded on a real repository.
- **FE-3. The SKIP set is hidden and named; `.gitignore` is not honoured in
  v1** (3.2).
- **FE-4. [OVERTURNED 2026-10-08, rich-chat RC-12] Text preview only; binaries are named, not shown.** Losing option:
  Orca's base64 images -- SVG is a script-capable format and an image route is
  a second, riskier surface; it can come later behind its own spec.
- **FE-5. A right-hand panel scoped to the conversation on screen, toggled by
  a header button and Ctrl+Shift+E, open state in `ShortcutStore`** (3.3).
- **FE-6. No watcher; refresh on open, on demand, and at turn end** (3.3).
- **FE-7. A secrets denylist blocks preview, not listing** (3.4).

## 5. Criteria (AC-303..AC-322 reserved; AC-303..AC-315 used)

- **AC-303 (one level, sorted, capped).** `GET /api/project/{id}/files`
  returns exactly the immediate children of the requested directory,
  directories first then case-folded names, at most 1000 entries with
  `truncated` and `total` truthful. **Recoverable.**
- **AC-304 (the root is the row's).** An unknown row id is a 404 naming it; a
  row whose directory is missing is a 409 naming the path; no request
  parameter can change the root. **Recoverable.**
- **AC-305 (lexical refusals before the disk).** A `path` that is absolute,
  has a drive or UNC prefix, contains `..`, `:`, or NUL, or names a reserved
  device is a 400 naming the rule, on BOTH routes, and no filesystem call is
  made for it. *Fails as:* a read outside the project. **NOT recoverable**
  once a secret has been read.
- **AC-306 (containment after resolution).** A symlink or junction inside a
  project that resolves outside it is listed with `outside: true`, and both
  routes answer 403 for any path through it; one that resolves inside is
  followed. Proved with a real junction (`mklink /J`). **NOT recoverable**,
  same reason.
- **AC-307 (the SKIP set).** Names in the SKIP set are absent from every
  listing, named in `skipped`, and a path through one is a 404 naming the
  rule; the page draws the "N folders hidden" line. **Recoverable.**
- **AC-308 (preview is bounded and labelled).** At most 256 KiB is read;
  `truncated`, `binary` and `lossy` are truthful; a binary answer carries no
  content; the answer is `application/json`. **Recoverable.**
- **AC-309 (secrets are listed, never previewed).** A file matching the
  secrets set is listed with `locked: true` and its preview is a 403 naming
  the rule. **NOT recoverable** if it fails.
- **AC-310 (read-only).** The explorer adds no route other than the two GETs
  and the `files_open` key; no control in the panel issues a request other
  than those GETs and `POST /api/shortcuts {"files_open"}`. **Recoverable.**
- **AC-311 (the panel follows the conversation).** The panel's root is always
  the project of the conversation on screen, including after opening a chat
  in another project, a birth, and the daemon's `opened` frame.
  **Recoverable.**
- **AC-312 (minimizable, remembered on the desk, never on a phone).**
  Toggling on a desk writes `files_open` and a reload restores it; a phone
  toggling it writes nothing. No browser storage. **Recoverable.**
- **AC-313 (keyboard).** The tree is `role="tree"` with one tab stop, and
  every action in 3.3 is reachable by keyboard alone. **Recoverable.**
- **AC-314 (preview is inert text).** Preview content is set with
  `textContent`; a project file containing `<script>` or `<img onerror>`
  renders as characters. *Fails as:* script running in Butler's origin.
  **NOT recoverable** -- a script there can drive every route.
- **AC-315 (refresh at turn end).** When a turn ends in a conversation of the
  shown project, the open folders are re-listed and a file the agent created
  appears without a manual refresh. **Recoverable.**

## 6. Gherkin

```gherkin
Scenario: AC-303/AC-307 one level, directories first, heavy folders named
  Given a project row whose directory holds "src/", "README.md", "a.txt",
        ".git/" and "node_modules/"
  When the page lists the project's root
  Then the entries are "src", "a.txt", "README.md" in that order
  And ".git" and "node_modules" are absent and named as skipped

Scenario: AC-305 a traversal is refused before the disk
  When a caller asks for path "../outside"
  Then the answer is 400 naming the ".." rule
  And the same holds for "C:/Windows", "\\\\host\\share", "a.txt:stream"
      and "CON"

Scenario: AC-306 a junction out of the project is not followed
  Given a junction "escape" inside the project pointing at another directory
  When the page lists the project's root
  Then "escape" is listed as outside
  And listing or previewing any path under "escape" is 403

Scenario: AC-308/AC-309 previews are bounded, labelled, and secrets are locked
  Given a 1 MB text file, a PNG and a ".env" in the project
  When each is previewed
  Then the text answer carries 256 KiB and says truncated
  And the PNG answer says binary, names the type, and carries no content
  And the ".env" answer is 403 naming the secrets rule

Scenario: AC-311 the panel follows the conversation
  Given the files panel is open on project A
  When the owner opens a chat in project B
  Then the panel's head names project B and its tree is B's root

Scenario: AC-312 the panel is remembered on the desk
  Given the desk page with the files panel shut
  When the owner presses Ctrl+Shift+E
  Then the panel opens and files_open is written to the daemon
  When the page reloads
  Then the panel is open

Scenario: AC-314 a hostile file renders as text
  Given a project file containing "<img src=x onerror=alert(1)>"
  When the owner previews it
  Then the characters are shown and no script runs

Scenario: AC-315 a new file appears after a turn
  Given the files panel shows project A with its root open
  When a turn in a chat of project A ends after the agent created "notes.md"
  Then "notes.md" is in the tree without a manual refresh
```

## 7. Build plan

Red then green in every phase; each phase quotes its scoped run in the commit.
Browser checks use `playwright-cli` against a SCRATCH daemon (own
`BUTLER_STATE` with `hotkeys_enabled: false`, `BUTLER_ASR=fake`,
`BUTLER_TTS=fake`, a free port that is neither 8140 nor 8765, killed by its
launch PID), with a scratch project row pointing at a FIXTURE directory under
`TMP/`, never at a real repository.

**Phase 0 -- mock.** `web/mocks/t-files.html`: the desk panel beside the
conversation (tree and preview states) and the phone sheet, screenshotted at
1440 and 390 px for the owner BEFORE any code; "keep it visually easy" is
judged by looking. *Verify:* screenshots in `TMP/file-explorer-out/`.

**Phase 1 -- `daemon/files.py`, rules only.** `resolve_inside`, `list_level`,
`preview`, the SKIP and secrets sets. `test/test_files.py` drives them over a
`tmp_path` tree: sorting, the cap, every lexical refusal (AC-305), a real
junction made with `cmd /c mklink /J` (no admin needed) both inside and
outside (AC-306), a symlink case that SKIPS with the reason printed when the
process lacks the symlink privilege, `C:\Repo` vs `c:\repo` casing
(Orca's recorded trap), binary/lossy/truncated (AC-308), the secrets set
(AC-309). *Mutation:* delete the `is_relative_to` check and watch the junction
test fail; delete the `..` refusal and watch the lexical test fail (it must
fail on the refusal, not on containment -- assert the 400 message). Run:
`python -X utf8 -m pytest test/test_files.py`, exit code read.

**Phase 2 -- the two routes.** Thin handlers in `butlerd.py` through
`asyncio.to_thread`, status codes per 3.1, the preview log line.
`test/test_files_routes.py` with the existing in-process app pattern: 404
unknown id, 409 missing root, 400/403/404 bodies name their rule, content type
is JSON (AC-304, AC-308, AC-310). *Mutation:* make the route take the root
from a query parameter and watch AC-304's test fail. Run the two files plus
`test/test_http_surface.py`.

**Phase 3 -- `files_open` in the store.** `ShortcutStore` field, `public()`,
`set_files_open` refusing a non-bool by name, `POST /api/shortcuts`.
*Verify:* `test/test_shortcuts.py` gains the key; run it.

**Phase 4 -- the desk panel.** Markup, `#filesBtn`, Ctrl+Shift+E, the tree,
the preview, "Copy path", scope from `currentProjectId()`. A structural test
(`test/test_file_explorer_page.py`) pins that preview text is set with
`textContent` and that the panel's only requests are the two GETs and the
store write (AC-310, AC-314). `playwright-cli`: AC-311, AC-312, AC-313,
AC-314 with a hostile fixture file; check Ctrl+Shift+E reaches the page in
Chrome and Edge.

**Phase 5 -- the phone sheet.** Shedding into `#headerMorePop`, the sheet,
its Escape case, no store write. `playwright-cli` at 390 px with touch:
AC-312's phone half, AC-313 on the sheet; `e2e_scenarios.py`'s phone section
still passes.

**Phase 6 -- refresh at turn end.** *Verify:* `playwright-cli` against the
scratch daemon with a real pi brain in `harness_tools` full, a prompt that
creates a file in the fixture project, AC-315 read from the DOM and from the
listing route.

**Phase 7 -- DOX pass.** `daemon/AGENTS.md` (the routes, `files.py`, the
security statement in 3.4), `web/AGENTS.md` (the panel, the Escape order
change, the header control), `test/AGENTS.md` (the new files and the junction
fixture rule), `plan.md` 4.x route list as a factual correction only. Full
suite once, because `butlerd.py` and `index.html` are shared.

## 8. What this could have wrong

- **The read surface is wider than it looks.** 3.4 says it plainly: with R2 in
  force, a caller that can write a row can read any directory on the machine
  through this explorer. If the owner reads that sentence and changes their
  mind about R2, the fix is on `POST /api/shortcuts`, not here.
- **The secrets denylist will miss things** -- a token in `config.yaml`, a key
  under a name nobody predicted. It is a speed bump, not a boundary.
- **Junction resolution on Windows is from memory.** If `Path.resolve` does
  not follow a junction on this Python, containment is checked on the
  unresolved path and AC-306 is false; phase 1's real `mklink /J` test is
  there to find that before any route exists.
- **A 300 px panel may be too narrow to read code in.** Previews scroll
  sideways by design; if that is unusable, the next step is letting the panel
  widen while a preview is open, not an overlay over the chat.
- **Turn-end refresh misses changes made outside Butler** (the owner's own
  editor, a terminal opened from the project pane). The refresh control
  covers them; a watcher is the later answer if that is not enough.
- **The header is already full on a phone** (`docs/sidebar-collapse.md` 8).
  `#filesBtn` sheds into the "more" popover there, which makes the explorer a
  two-tap feature on a phone -- acceptable for a desk-first tool [OPINION].
