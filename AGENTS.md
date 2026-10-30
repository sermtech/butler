# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why


## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

### Owner's standing rulings

- **No desktop UI.** Anything beside the daemon draws nothing; options belong
  on the settings page, which is generated from the daemon's schema (AC-41).
  Two exceptions: `desk/browse.py`, a short-lived child of the daemon run
  only when the owner clicks Browse, and the Tauri desktop app (`app/`,
  owner 2026-10-07), which shows the SAME page in a window plus a tray menu
  and its own startup, setup and phone-link pages -- never an option.
- **Key bindings are the owner's and live in one table** (`docs/shortcuts.md`,
  `daemon/shortcuts.py`): a click on a project row opens claude / pi / opencode
  there, a chord is optional, and claude always runs with
  `--dangerously-skip-permissions` from the daemon's closed table (D9).
  `POST /api/terminal` takes a caller path (D4); the `cmd` metacharacter
  refusal (AC-120), `is_dir` and the closed agent table are the gates.
- **The listening-buffer feature is called AMBIENT** (`docs/ambient.md`).
- **The owner's own messages are answered at any hour** (owner, 2026-10-08):
  a WhatsApp or Telegram message in their own chat is a turn and is never held by
  quiet hours, which hold only speech nobody asked for. Pinned by
  `test_whatsapp_inbound.py::test_the_owners_message_is_answered_inside_quiet_hours`.
- **The harness model picker shows pi's DECLARATION** (`pi --offline
  --list-models` over `~/.pi/agent/models.json`), which `pi update --models`
  does not refresh for a custom provider. Brain pickers come live from
  `/v1/models`.
- **A review subagent gets a shell**, reads pytest's EXIT CODE (never its
  text), and leaves the tree exactly as found.

### What Butler is

An always-on, hands-free voice assistant daemon for Windows. It owns the
microphone and speakers, holds a conversation through three model tiers, and
dispatches coding-agent harnesses (claude, pi, codex) that report back by
speech. Single user, one desk machine plus phone and tablet satellites on a
tailnet. NOT telephony, NOT multi-tenant, NOT a cloud service. The daemon
also runs on macOS (`docs/macos.md`), without the Windows-only desk agent,
prechecks, child-lifetime job or desktop app.

- `plan.md` is the build contract: acceptance criteria (AC-n), architecture,
  build order and the defect catalogue (D-n). Cite AC and D numbers in code
  comments and commits. A spec's criteria are a proposal until promoted there.
### Spec index (`docs/`)

Add the row in the same commit that adds the spec. Status is as of 2026-10-02;
`plan.md` section 2 holds each promoted criterion's real status.

| Spec | Status | ACs | Read first |
|------|--------|-----|-----------|
| `architecture-seams.md` | BUILT, all 7 phases | 38-45 | Before touching `daemon/ports.py`, an adapter, or config-to-engine flow |
| `harness-mode.md` | BUILT | 46-49 | Sections 1-9 are overtaken by AC-59/AC-60 (child held per conversation); section 9 is what the plan got wrong |
| `worker-mode.md` | PROPOSAL | 50-54 | Section 10 (simplification, shipped: AC-29 retired) and section 11 (tensions awaiting the owner) |
| `ambient.md` | BUILT (AC-71 partly) | 68-73 | Two boxed overturned conclusions; two decodes on one model return EMPTY |
| `lattice-wave.md` | BUILT | 74-82 | Section 9: gold does not survive the light theme |
| `settings-page.md` | BUILT; AC-83, AC-84, AC-88 superseded by `lantern-ui.md` AC-700..AC-705 | 83-90 | History of the one-page surface; the sheet's contract is `web/AGENTS.md` |
| `satellite-ux.md` | PART BUILT | 91-106 | Section 5 is past-tense measurement, not today's page |
| `voice-control.md` | BUILT (header label stale) | 107-112 | Supersedes AC-96 |
| `desk-hotkeys.md` | BUILT | 113-123 | Dictation is not push-to-talk; D3 reversed by `shortcuts.md` D4; section 7's box is itself stale: HEAD opens `wt.exe -d <cwd> cmd /k <agent>` (`daemon/butlerd.py` `TERMINAL_ARGV`) |
| `desk-audio.md` | PART BUILT; D3 ruled 2026-10-29, AC-819..AC-823 BUILT | 124-132, 819-823 | Section 1: the failure did not reproduce; fixed 16 kHz forces MME; D3: the mic is held per press at lookback 0 (default) |
| `shortcuts.md` | BUILT | 133-151 | AC-145 (suppressed keys cannot be polled); 4.3 bare-safe keys; section 10: media/mail keys cannot be hooked |
| `language.md` | BUILT | 152-160 | 6.1 and 6.4 (D-35, D-36); D2: the brain gets no language instruction |
| `familiar-ui.md` | BUILT (header label stale) | 161-174 | Section 4: what a redesign may not break; probes change in the same commit (AC-172) |
| `mic-optional.md` | BUILT | 175-184 | 7.3's open worry |
| `chat-search.md` | BUILT | 185-188 | Matching is the daemon's, never the page's |
| `rewind.md` | BUILT | 189-196 | Overturns AC-97; a rewind never reverses the filesystem |
| `attachments.md` | BUILT; AC-203 replaced by AC-815..AC-818 (no separate vision model, 2026-10-29) | 197-206 | Type sniffed from magic bytes; documents read locally first |
| `language-auto.md` | BUILT | 207-215 | Section 7 (the guard that had to go) and 4.2 (why a latch) |
| `workspaces-ui.md` | PROPOSAL, superseded | 216-227 | Its build order was replaced by `parallel-agents.md` |
| `parallel-agents.md` | MOSTLY BUILT (phases 8-9 proposed) | 228-251 | AC-49 reversed: an agent may ask and wait; the voice follows what the device shows |
| `voice-effects.md` | BUILT steps 1-6 | 252-262 | Section 0: the level pipeline already exists; lattice stays the default |
| `triage-list.md` | BUILT | -- | Chat-list triage; navigation later replaced by `filing-and-pacing.md` 8 |
| `filing-and-pacing.md` | BUILT | 271-292 | Section 8: a chat belongs to its project and never moves; the project is the unit of concurrency |
| `sidebar-collapse.md` | BUILT | 293-299 | 1.1: what each deleted strip control reached |
| `file-explorer.md` | BUILT | 303-315 | 3.4: the read surface it adds under R2 |
| `identity-memory.md` | BUILT phases 1-4, 11, 12 | 370-386, 420-446 | SOUL/USER files; memory v2 rules (12.5) |
| `turn-feedback.md` | BUILT | 387-396 | Section 3: every turn-path timeout |
| `data-root.md` | BUILT, migrated on both machines | 410-419 | Section 5: migration and rollback |
| `automations.md` | BUILT, phases 0-8 | 447-474 | Section 1: a scheduled run needs `focus=False` on the one chat birth |
| `proactive.md` | BUILT P1-P5 (real-world checks open, backlog) | 475-499 | Section 0: the owner's 2026-10-06 rulings (held morning speech, prechecks reopened); `.references/personal-agents-2026.md` is its evidence |
| `messaging.md` | BUILT M1-M9, T1-T3; review fixes 6.5 in progress | 500-539, 560-563, 586-612 | A PORT of an earlier prototype's WhatsApp (`.references/prototype-whatsapp.md`) plus guest replies with no tools (6.1); built between `proactive.md` P2 and P3 |
| `desktop-app.md` | BUILT D1-D5 (installs and AC-555 open) | 540-559 | Section 3: trust by LISTENER, not peer address, once tailscale serve exists; reverses No desktop UI for the Tauri shell only |
| `parallel-same-project.md` | PROPOSAL | 564-585 | Section 1: the limit is already a setting; what breaks above 1; shared folder by default, a worktree per new agent on request |
| `rich-chat.md` | BUILT (owner rulings in section 0) | 613-640 | Section 0: narrowly reverses AC-94's "NEVER img" (sources only from Butler's artifact route) and extends FE-4 for the chat only |
| `butler-help.md` | BUILT | 641-656 | BH-1: a short index always, details from `butler_help`; BH-3: a feature the table does not name is a test failure |
| `recipe-tools.md` | BUILT | 657-670 | RT-3: `recipe_propose` is the schedule proposal with a `kind`, one per conversation of either kind; skills untouched (owner) |
| `todo-list.md` | BUILT | 671-677 | TD-2: an agent's task goes to the main list, tagged by project; the owner's own tasks are unchanged |
| `macos.md` | BUILT, installed on Ana's Mac mini | -- | Section 1: what is Windows-only; section 2's fresh-install trap (no records folder) |
| `lantern-ui.md` | BUILT Steps 1-7 on master 2026-10-27 (look: `Butler web UI.html`; mocks approved 2026-10-27); laptop and Mac synced and restarted 2026-10-27 | 678-712 | Section 0: owner rulings R1-R5 (Lantern default, Caption as a style, settings revamp overrides the lock); section 7 lists every rule it reverses |
| `remote-access.md` | BUILT 2026-10-29; rolled out on desk, laptop and Mac | 720-732 (733-749 reserved) | Section 2: one gate by listener (RA-2); section 6: rollout order, `lan_access` added by hand with the daemon stopped; section 8: what the attack found |
| `project-roots.md` | BUILT 2026-10-29 | 750-753 (754-759 reserved) | Section 1: agents already read the whole disk; this limits the explorer and devices only |
| `chats-and-terminals.md` | BUILT 2026-10-29, rolled out on all three | 760-773 (774-799 reserved) | Section 0: owner's five answers; section 5: open decisions OD-1..OD-6 |
| `chat-agent-picker.md` | BUILT, option C (mock `web/mocks/chat-agent-picker.html`) | 774-779 | Section 2: AP-1..AP-6; section 6: open decisions AP-D2..AP-D4 |
| `chat-model.md` | BUILT 2026-10-29 | 780-788 (789 reserved) | Section 2: one rule (a chat has its own model or follows its agent's default) |
| `agent-and-terminal-settings.md` | BUILT 2026-10-29 | 790-798 (799 reserved) | Section 2: chat agents and terminals are switched on in Settings; section 5: SW-D1..SW-D3 |
| `wake-words.md` | BUILT 2026-10-29 (not yet on the Mac mini) | 800-809 | Section 1: the turn starts at the wake fire (`lookback`); section 2: one setting per wake word |
| `e2e-journeys.md` | AUDIT | -- | Evidence, not criteria |
| `reflex-serving.md`, `bench-tts.md` | PROBE LOGS | -- | Historical evidence, not rules |

### Development guidelines

- Windows system: adapt paths, slashes, commands accordingly
- Clean Architecture: no fallback code, no placeholder implementations
- A new or changed owner-facing feature updates its row in
  `daemon/guide_features.py` in the same commit (`docs/butler-help.md` BH-3):
  that table is all the agent knows about Butler. The test catches a new
  settings group or tool; a page-only feature it cannot see.
- WHENEVER CREATING A NEW FEATURE, ALWAYS review the app FIRST for existing code
  and for where the new thing connects: what already does this, what it would sit
  beside, and who else calls it. Reuse and extend rather than duplicate wherever
  possible, and keep the architecture clean
- TDD/BDD red-green methodology for all development
- Fail fast philosophy: errors surface immediately, no silent swallowing
- TRUTHFUL in all reports, evaluations, and generated content
- No unicode characters in code or documentation (Windows compatibility)
- Run code-simplifier after sessions or refactoring for simplification opportunities
- Interview user with scenarios/stories to evaluate feature completeness
- Use `python -X utf8` on Windows to avoid encoding issues
- Prefer `uv` over `pip` for package management
- Use ast-grep and ast-grep-py for deep AST code analysis, navigation, symbol
  search, refactoring

### Rules this project paid for

These are not general advice. Each one is a defect that shipped here or in its
predecessor, and each has a number in `plan.md` section 6.

- A contract needs an assertion on the CALLER, not just the callee (D-18). This
  bit three times in one day: an abort that named no handle, a playback gate the
  reply path never called, and per-session barge-in. Every time, the callee's own
  tests were green.
- Every terminal path re-arms (D-1). Never latch an error into a state that
  disables the microphone. Freezing counts: a blocking call on the event loop is
  indistinguishable from a dead microphone.
- Validate the instrument (D-13). Break the code, confirm the mutation applied,
  watch the RIGHT test fail, restore. A test that survives the defect it is named
  after is not a test.
- Stale prose is the recurring hazard (D-12). When behavior changes, the same
  commit updates the comment or deletes it.
- Measure, do not estimate. Numbers in docs carry a provenance tag
  ([MEASURED-SPIKE], [MEASURED-PROTOTYPE], [TARGET], [READ], [DECIDED], [REVIEW]).
  An untagged number is a guess wearing a uniform.

## Child DOX Index

| Child | Scope |
|-------|-------|
| `daemon/AGENTS.md` | The daemon: ports, protocol v1, turn lifecycle, policy |
| `desk/AGENTS.md` | The desk agent: the hotkey process, its microphone, clipboard and cues |
| `web/AGENTS.md` | The satellite page: microphone, wake word, playback, settings UI |
| (no AGENTS.md) `tools/` | Standalone CLIs for other harnesses: `wa.py` + `wa-SKILL.md` (`docs/messaging.md` MS-9); stdlib only, may import `daemon/paths.py` |
| `app/AGENTS.md` | The Tauri desktop app: window, tray, local pages, microphone gate |
| `test/AGENTS.md` | pytest suite, conformance suites, fixtures, mutation discipline |
