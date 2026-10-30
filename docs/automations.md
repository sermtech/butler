# Recipes, automations and tasks, organised by project (BUILT, 2026-09-26)

Written against HEAD `d6695ea`. **ALL PHASES 0-8 ARE BUILT (2026-09-26)**;
section 11 records each phase's build, what differed and what was not done.
Nothing here is an accepted criterion: AC-447..AC-474 are RESERVED for this
spec and stay here until the owner accepts them; `plan.md` was touched only
for 4.3's frame list, a factual correction (`caps` in, `announce` out). It builds on `docs/file-explorer.md`
(the right-hand column), `docs/filing-and-pacing.md` 8 (a chat is born in its
project), `docs/turn-feedback.md` (the heartbeat) and `docs/identity-memory.md`
12 (the memory tools and their rules block).

Provenance tags: [READ] read from source, [MEASURED] observed in a run,
[DECIDED] a choice this spec makes, [OWNER] the owner's ruling, "from memory"
where a claim was not checked here.

## 0. The owner's decisions (2026-09-26) [OWNER]

In substance, as given:

- **A RECIPE** is a name, a prompt with optional `{placeholders}`, and a scope
  (one project, or global). It runs from the Library panel, by typing `/` in
  the composer, or by voice ("run <recipe>"). The PROMPT is the reusable unit;
  an automation REFERENCES a recipe.
- **AN AUTOMATION** is a recipe plus a schedule plus a project, an enabled
  flag and a run history. Each run opens its OWN new chat in that project,
  titled with the recipe and the date. A missed run (PC off) runs once at
  startup if within a grace window, else it is a "skipped: missed" row. If the
  project's agent is busy, the run waits within the grace window, then
  "skipped: busy" -- **it never stops the owner's chat**. Repeated identical
  skips fold into one row. A run follows the Tools setting (`harness_tools`)
  and SOUL.md's ask-first rule: a run that needs approval stops and asks in
  its chat.
- **A TASK** is a to-do per project (text, done flag, created and done dates,
  source owner or Butler), checked off by hand or by voice, with a Start
  button that opens a chat in that project with the task as the prompt.
  Whether Butler may ADD one is left to this spec (section 3, AU-9).
- **PLACEMENT.** A right-hand LIBRARY panel with tabs Recipes | Automations |
  Tasks, scoped to the chat's project plus a Global section; a sheet on the
  phone. Plus a full TASKS & AUTOMATIONS page "for better control": every
  project, grouped, with run history, filters, bulk enable/disable, rerun.
- **RESULTS of a run, all three:** the run's chat appears in the tree with an
  unread dot; a spoken one-liner if a device is listening; a Telegram message
  with the first lines of the result, its token held by the DAEMON.

### 0.1 The owner's rulings on section 14 (2026-09-26, second sitting) [OWNER]

- **R-A. Telegram is ON by default for every new automation.** And, in the
  owner's words: "keep it modular so i can copy telegram skill and add a
  different bot to different pc/ users in the future." So notification is a
  small pluggable CHANNEL behind a port, and each bot is one self-contained
  file (AU-19, section 7).
- **R-B. The spoken one-line result speaks on the DESK ONLY**, never on the
  last-used device. If the desk is not connected and listening, Butler stays
  silent (AU-18).
- **R-C. Unattended authority stays as already ruled: a run follows the Tools
  setting.** Not re-opened. The warning stays: with Tools at `full`, a
  schedule writes and runs commands with nobody watching (AU-17, section 12).
- **R-D. Build order: phase 0 (the mock) first; the owner approves the look;
  then phases 1-8** (section 11). No daemon code before the look is approved.

## 1. What Butler has today [READ]

- **Projects** are `ShortcutStore` rows (`id`, `path`, `label`);
  `root_id()` names Butler's own row, which always exists (R5).
- **The one birth** is `Hub.new_conversation(project_id)` (`daemon/hub.py:2318`)
  -> `brain.new_conversation(path)` -> `ChatStore.rotate(project)`
  (`daemon/chats.py:890`). **Two of its side effects are wrong for an
  unattended run, and this is the spec's main finding:** `rotate` UNHOLDS the
  outgoing current conversation ("'new conversation' ends the one the owner
  was having", `chats.py:915-919`), and `Hub.new_conversation` clears the
  router's short memory (`hub.py:2353-2358`). A scheduled run through that
  birth unchanged would end the owner's live conversation at 09:00 and wipe
  its router history -- while every test of the birth stays green.
- **The one turn path** is `Hub._typed(s, m)` (`hub.py:1604`) ->
  `_begin_turn(s)` -> `_on_final` -> `_reply`. Every door needs a
  `SatelliteSession`. A turn can already OUTLIVE its socket (`017f8a1`, the
  orphaned harness turn), and `SatelliteSession.send` returns quietly when the
  socket is closed (`hub.py:430-437`).
- **The per-project limit** (`_Host._enforce_project_limit`,
  `daemon/adapters/brain_harness.py:820`): a BUSY child in the same project
  raises `CapReached`; an IDLE, unexpired child is DISPOSED with no confirm
  (D1=A; the session file survives, a 1.5-2.3 s respawn). So a run that
  arrives while the owner's chat is idle in that project costs the owner's
  next turn a respawn, not memory. Stated, not hidden.
- **Unsolicited speech does not exist.** "Unsolicited speech is the
  announcer's job and the announcer is Phase 2 (AC-14)" (`hub.py:2552-2559`).
  AC-14 and AC-14a in `plan.md` already define the queueing rules a spoken
  run result needs; nothing implements them.
- **Secrets** are POINTERS in `daemon/connections.py` (`auth: {"from": "env" |
  "pi"}`); no secret value is ever typed into the page today.
- **Spoken commands** are deterministic branches in `_on_final`
  (`hub.py:2880-2930`), parsed by `settings.spoken_*` helpers and matched by
  `settings.match_project` (exact, normalized by `_spoken_form`, ambiguity
  refused aloud, a bare unknown name falls through to work).
- **Memory tools** reach the endpoint brain as tool calls and pi through the
  loopback routes `POST /api/memory/search|write` (403 off-loopback,
  `butlerd.py:2898`) and `daemon/adapters/pi_memory.ts`; the rules block is
  `dialogue.MEMORY_RULES` (`dialogue.py:170`), with the "Noted: <fact>."
  read-back.
- **Four atomic-write helpers already exist** (`memory._atomic`,
  `persona._atomic_write`, and inline in `chats.py:1230`, `modes.py:90`,
  `settings.py:1354`). This work reuses `memory._atomic` and adds no fifth.
- **The right-hand column** is `#filesPanel`, `.app`'s third grid column,
  open state `files_open` in `ShortcutStore` (`web/AGENTS.md:1001-1013`).
- **The full-screen surface** precedent is the settings surface
  (`web/settings.js`), over the page, with its own z-index slot.

## 2. Prior art: Orca [READ, `E:\PROJECTS\other-project` at `da6d483a`]

- **The record.** `Automation` (`src/shared/automations-types.ts:96-131`):
  `name`, `prompt`, `precheck`, `rrule`, `timezone`, `enabled`, `nextRunAt`,
  `lastRunAt`, `missedRunPolicy: 'run_once_within_grace'`,
  `missedRunGraceMinutes`, `workspaceMode: 'existing' | 'new_per_run'`,
  `reuseSession`. **Orca embeds the prompt in the automation**; Butler's
  automation references a recipe instead (the owner's ruling), so one edited
  prompt reaches every schedule that uses it.
- **Runs** (`:9-19`, `:134-172`): statuses `pending`, `dispatching`,
  `dispatched`, `completed`, `skipped_precheck`, `skipped_missed`,
  `skipped_unavailable`, `skipped_needs_interactive_auth`, `dispatch_failed`;
  `trigger: 'scheduled' | 'manual'`; only FINAL statuses may be evicted from
  history (`:22-31`). Repeated refusals fold into one record
  (`occurrenceCount`, `lastOccurrenceAt`, `:165-171`), because "a */5
  automation would otherwise write ~288 identical rows a day -- past
  retention, which would evict the automation's real history"
  (`src/main/automations/service.ts:280-282`).
- **The scheduler.** A 60 s `setInterval` (`service.ts:40`, `:117-119`) that
  walks every automation SEQUENTIALLY, isolates each record's failure
  (`:245-258`), and advances `nextRunAt` after each decision (`:263-296`).
  Its own recorded gap: an evaluation pass holds the re-entrancy guard across
  its dispatches, so a slow dispatch drops later ticks and makes the next
  automation look "missed" (`dispatch-refusal.ts:140-146`). **Butler
  decouples the decision from the run for exactly this reason** (AU-6).
- **Missed-run grace** (`dispatch-refusal.ts:147-157`): missed when
  `now - scheduledFor > grace + 2 * tick` -- one tick for the pass that should
  have caught it, one for jitter.
- **Schedules** are hand-parsed, no library: cron parsing and occurrence math
  are `automation-cron-occurrence.ts` (49 lines),
  `automation-schedule-parsing.ts` (247) and
  `automation-schedule-occurrences.ts` (157), with presets hourly, daily,
  weekdays, weekly, custom (`automations-types.ts:34`) and a minute-by-minute
  cron scan bounded at 9 years for Feb 29 (`automation-schedule-occurrences.ts:
  8-10`, `:85-111`).
- **Templates** (`src/renderer/src/components/automations/automation-templates.ts:
  20-126`): four starters (weekday repo audit, weekly release prep, daily
  review, hourly maintenance), each a name, a prompt, a preset, a time and a
  grace (720 minutes for the audit).
- **Grouping by project** (`automation-project-groups.ts:11-27`): one group
  per project identity, several sources folded under it.
- **UI**: an AutomationsPage with a list, an editor dialog
  (`AutomationEditorDialog.tsx`, a prompt editor plus a settings sidebar and a
  custom cron panel `AutomationCustomCronPanel.tsx`), and a detail view with
  run history and "Run Now" (`AutomationDetail.tsx:175`).
- **Orca has NO to-do store of its own.** Its tasks are external trackers:
  `TaskProviderIdentity` is GitHub, GitLab, Jira or Linear
  (`src/shared/task-source-context.ts:9-25`); a search for a local task store
  found none. **Butler deliberately adds one**, because the owner's tasks are
  personal commitments as well as code work, and a voice assistant has to be
  able to hear "add task" with no tracker configured.

Copied: the record's shape, folding, the grace formula, final-only eviction,
starter templates, manual rerun, a hand-written parser. Declined: `precheck`
(a shell command per automation is a new execution surface on an
unauthenticated tailnet), `workspaceMode`/`reuseSession` (the owner ruled
one new chat per run), SSH hosts and usage accounting.

## 3. Decisions [DECIDED unless marked OWNER; the owner may overrule any line]

- **AU-1. Recipes are one Markdown file each; automations, runs and tasks are
  one JSON file each**, all under `<records>/library/` (a new
  `paths.Paths.library`). A recipe is prose the owner may want to edit in an
  editor, which is memory v2's one-note-per-file precedent; the other three
  are structured and only ever edited through the page. Every write is
  `memory._atomic`; a hand edit to a recipe file is picked up by stat, as
  notes are. *Losing option:* one `recipes.json` -- a multi-line prompt inside
  a JSON string is unreadable by hand.
- **AU-2. A scope is a ROW ID or `global`, never a path.** A removed row
  leaves its items addressable and says so ("project removed"), and its
  automations skip as unavailable rather than guessing a new home. The
  "Global" section of every list is global recipes plus the ROOT row's tasks
  and automations: Butler's own project is where things with no project live,
  R4's rule. *Losing option:* a separate global bucket for tasks -- a second
  home for the same thing the root row already is.
- **AU-3. One expansion.** `library.expand(recipe, values)` in the daemon is
  the ONLY place a placeholder is filled (D-18): the panel, the slash picker,
  voice, a task and a scheduled run all call it. Syntax `{name}` or
  `{name=default}`, names `[a-z0-9_]{1,32}`, `{{` and `}}` for literal braces.
  An unfilled placeholder without a default is REFUSED naming it; nothing is
  half-expanded. The result is capped at `TYPED_MAX_CHARS` and refused past
  it, as a typed message is.
- **AU-4. Interactive doors run on the DEVICE's own turn; only the scheduler
  runs headless.** Panel Run and task Start: the page births a chat
  (`POST /api/session/new {project}`), opens it, and sends the expanded text
  as an ordinary `typed` frame -- no new daemon path. Slash: the expansion is
  INSERTED into the composer and not sent, so it can be edited (the Claude
  Code custom-command shape). Voice: "run X" births a chat, tells the device
  (`opened`, AC-290's frame) and starts the expanded text as that device's
  turn in it. *Losing option:* one daemon-side "run recipe" for every door --
  the device that pressed Run would not own the turn and would see only the
  status poll, not the streamed reply.
- **AU-5. A scheduled run is a typed turn in a new chat through a HEADLESS
  session.** `RunSession` is a `SatelliteSession` with no socket (a null
  socket that is always closed), `device_id = "automation:<id>"`, `showing`
  set to its own chat, TTS capability off, and a pre-set `playback_done`.
  It enters through `_typed`, so the router, the brain, the record, the
  heartbeat and `_rearm` are the ones every turn uses. *Losing option:* a
  direct call into the brain -- a second agent pathway, the thing D-18 names.
- **AU-6. The scheduler DECIDES; a separate runner DISPATCHES.** A 60 s tick
  (`asyncio` task started by `build_hub`, cancelled at shutdown) reads the
  store, decides every due automation with NO `await` inside the decision (the
  TOCTOU lesson of `_enforce_project_limit`), writes the run rows and advances
  `nextRunAt`, then hands `pending` runs to a queue. The runner births and
  starts each run and returns when the turn has STARTED, never when it ends.
  A slow birth can delay the next dispatch but never the next decision, which
  is Orca's recorded stall avoided by construction. All disk work goes through
  `asyncio.to_thread` (D-1, D-25). The tick compares WALL clock time, so a
  machine that slept wakes into the missed-run rule rather than a burst.
- **AU-7. A run's birth does not take focus.** The one birth gains
  `focus: bool = True`: `Hub.new_conversation(project_id, focus=False)` ->
  `ChatStore.rotate(project, focus=False)` registers and holds the new record
  WITHOUT unholding or replacing the current one, and skips the router-history
  clear. Still one birth, one gate (AC-31 plus `is_dir`), one place a
  project is set. *Losing option:* a second `mint` method -- a second
  definition of a birth, which is how the page's New chat once missed a line.
- **AU-8. A run chat is titled from its ORIGIN, visibly.** The first user
  message carries `origin: {kind: "automation", automation, run, recipe}`
  and `_title` answers `"<recipe name> - <Mon 28 Sep 09:00>"` for such a
  record; the listing carries `origin` so the page marks the row with a clock
  glyph. `chats.py`'s objection to a daemon-invented title ("indistinguishable
  on the wire from one the owner said", `chats.py:203-206`) is answered by
  that mark rather than overridden silently.
- **AU-9. Butler MAY add a task: a `task_add` tool beside the memory tools.**
  (Amended by `docs/todo-list.md` TD-2: an agent's task now goes to the main
  list, tagged with its project; the rules wording below is superseded.)
  Offered exactly where `memory_search`/`memory_write` are (the endpoint
  brain's tools, pi's loopback extension through `POST /api/tasks/agent`,
  403 off-loopback), with a `TASK_RULES` block beside `MEMORY_RULES`: add a
  task only when the user commits to doing something later or asks to be
  reminded, one task per commitment, under 200 characters, never for work
  done in this reply; end the reply with "Added task: <text>.". The project
  is the TURN's chat's project, never an argument the model supplies. At most
  3 per turn. The text goes through the memory write scan (AC-436's
  patterns). *Losing option:* no tool -- the owner then has to say "add task"
  for every commitment Butler already heard.
- **AU-10. The right column shows ONE panel at a time.** `ShortcutStore`'s
  bool `files_open` is replaced by `right_panel: "" | "files" | "library"`,
  migrated once (`true` -> `"files"`); `#filesBtn` and a new `#libraryBtn`
  each toggle their own value. Two bools would make "both open" a
  representable state nobody can draw at 300 px. *Losing option:* one panel
  with four tabs (Files, Recipes, Automations, Tasks) -- it hides the
  explorer behind a Library it has nothing to do with.
- **AU-11. Schedules: presets plus a 5-field cron, parsed by Butler, no
  dependency.** Presets: daily at, weekdays at, weekly on day at, monthly on
  the nth weekday at. Every preset compiles to cron; the engine knows only
  cron. The parser takes numbers, `*`, ranges, lists, steps, `MON-SUN` and
  `JAN-DEC` names and one extension, `dow#n` (n = 1-5, "the nth weekday"),
  because standard cron ORs day-of-month with day-of-week and so cannot say
  "first Monday". Next occurrence walks DAYS (at most 5 x 366), then the
  hour and minute lists -- milliseconds, no minute scan. An expression with no
  occurrence in 5 years is refused at save. Orca hand-rolls the same thing in
  453 lines. *Losing options:* `croniter` (no `#n` in the owner's presets
  without its extension syntax, and its maintenance status changed in 2024,
  from memory); `python-dateutil`'s rrule (handles nth weekday well, but it is
  not a declared dependency and RRULE is not the "advanced cron field" asked
  for).
- **AU-12. Time is the machine's local zone.** A nonexistent local time
  (spring forward) runs at the first valid minute after it; an ambiguous one
  (fall back) runs once, at the first. `nextRunAt` is stored as ISO 8601 with
  its UTC offset, so the file is readable and a zone change is visible.
- **AU-13. Busy means wait, then skip; the owner's chat is never stopped.**
  `CapReached` from the per-project or the global cap puts the run in
  `waiting_busy`; each tick retries it until `scheduledFor + grace`, then it is
  `skipped_busy` naming which limit. `Hub.stop_conversation` is never called by
  this feature. An IDLE held child in the project is disposed by the existing
  D1=A rule (a respawn, not a loss) -- inherited, stated in 1, not changed.
- **AU-14. Missed-run grace defaults to 60 minutes** (0-1440 per automation);
  missed means `now - scheduledFor > grace + 2 ticks` (Orca's formula). At
  boot, every missed occurrence of one automation within grace becomes ONE run
  (the latest); those beyond grace become ONE folded `skipped_missed` row
  carrying the count.
- **AU-15. History is capped at 50 rows per automation**, final statuses
  only are evicted, identical consecutive skips fold (Orca). Done tasks are
  kept; the panel shows the last 10 under a fold.
- **AU-16. Approval needed = the existing question path.** A harness child
  that raises a dialog in a run chat is `waiting` in `status_for`; the run
  row becomes `needs_you`, the `question` broadcast reaches every page as
  today, and the run's channels say "needs you". Answering continues the run; an
  expired question ends it as the agent decides. Nothing new asks.
- **AU-17. Runs follow the Tools setting (`harness_tools`), read at dispatch
  [OWNER, R-C].** It is one global setting today, not per project, so "the
  project's Tools setting" is that setting. Stated, not softened: **with
  Tools at `full`, a schedule starts a coding agent that writes and runs
  commands while nobody watches** (section 12). The owner ruled this and it
  is not re-opened here.
- **AU-18. The spoken result is AC-14's announcer, finally built, for the
  DESK ONLY [OWNER, R-B].** The desk is a satellite session whose socket PEER
  is loopback (`butlerd.LOOPBACK`), recorded by the daemon when it accepts
  the socket -- never the page's own `device_id`, which is a caller-supplied
  string. It speaks only if that session's socket is open, it has TTS, `speak`
  is on and it is wake-listening; AC-14's states decide WHEN (queued behind a
  turn in flight on the desk, never preempting). **If no desk session is
  connected and listening, nothing is spoken and nothing is held**: AC-14a's
  hold-and-replay does NOT apply to run results (the unread dot and the
  channel message already carry them). The line is fixed and short: "Your
  <recipe> run in <project> finished." / "... needs you." / "... failed.",
  never the result itself. *Losing options:* the device that last took a
  turn (it may be a phone in a pocket -- the owner's reason); every
  connected device (two devices in one room say it twice).
- **AU-19. Notification is a pluggable CHANNEL, one file per bot [OWNER,
  R-A].** A `Notifier` port, a Telegram adapter, and a directory of channel
  files; an automation names its channels and a new one starts with the
  machine's default channel ticked. Section 7.
- **AU-20. The unread dot is GLOBAL, not per device** (R3's precedent): a
  run's chat is unread until it is opened on any device
  (`POST /api/session/open` records `seenAt` on the run). *Losing option:* per
  device -- the phone would nag about a result already read at the desk.

## 4. Data model [DECIDED]

`<records>/library/recipes/rcp-<8 hex>.md` -- the file name is the id, never
the name, so a rename is not a file move:

```
---
id: rcp-7f3a19c2
name: Weekly review
project: sc-4d2e      # a ShortcutStore row id, or "global"
created: 2026-09-26T10:14:00-04:00
updated: 2026-09-26T10:14:00-04:00
---
Review what changed in this project since {since=last Monday}. List risks,
failing tests and the next three actions.
```

Name: 1-60 characters, unique per scope after `_spoken_form` normalization
(so voice can reach it). Prompt: 1..`TYPED_MAX_CHARS`.

`<records>/library/automations.json` -- a list of:

| field | meaning |
|---|---|
| `id` | `aut-<8 hex>` |
| `recipe` | recipe id; a project recipe must be this automation's project |
| `project` | row id (required -- a schedule always runs somewhere) |
| `values` | `{placeholder: value}`; every placeholder without a default must be here |
| `schedule` | `{preset, cron, label}`; `cron` is the truth, `preset` only redraws the picker |
| `enabled` | bool |
| `graceMinutes` | 0-1440, default 60 |
| `channels` | list of channel names (section 7); a NEW automation starts as `[notify_default]` when that setting names a channel, else `[]` and the editor says why. `[]` = no message |
| `silence` | bool, editable (`docs/proactive.md` PR-3): the run is told to answer `[SILENT]` when nothing is new, and such a completed run tells nobody. `true` for a new automation; a row saved before the field existed upgrades as `false` |
| `failStreak` | int, stored, not editable (PR-5): consecutive FAILED runs; a completed run (silent or not) or turning it back on resets it to 0; boot `recover()`, stops, skips and needs_you never touch it |
| `lastFailAlertAt` | ISO or "" (PR-5): when the streak last reached the owner; a reminder needs 24 h since it (PR-7) |
| `kind` | absent for a recipe automation; `"heartbeat"` for a project's ONE heartbeat (`docs/proactive.md` PR-8). A heartbeat has no `recipe` or `values`, `graceMinutes` is always 0 (PR-11: never caught up), its `schedule.preset` is `{preset: "every", minutes}` with minutes 15, 30, 60 (default), 120 or 240, `silence` starts `true`, and a save naming no id updates the project's existing heartbeat. Its checklist is `<records>/heartbeat/<row id>.md` (`Paths.heartbeat`), not a field. The kind of a row never changes |
| `precheck` | recipe automations only (`docs/proactive.md` PR-23): "" or the NAME of a script in `<records>/prechecks/` (`Paths.prechecks`): `[a-z0-9-]{1,32}` plus `.py` (Butler's interpreter, `-X utf8`), `.ps1` (`powershell -NoProfile -ExecutionPolicy Bypass -File`) or `.cmd` (`cmd /c`), resolving to a regular file directly in that folder. Checked when given; re-resolved before every run |
| `precheckDigest` | stored, not editable (PR-25): SHA-256 of the check output the last COMPLETED run was given; "" when the check changes |
| `source` | recipe automations only (`docs/proactive.md` PR-28): the NAME of a trigger source in `<records>/triggers/` instead of a cron. Then `schedule` is `{preset: null, cron: "", label: "When <source> fires"}`, `nextRunAt` "", `graceMinutes` 60 and `precheck` "". Given, it must exist; a `schedule` given without it removes it; removing it with no schedule is refused. Absent on a scheduled automation |
| `chat` | heartbeat only (PR-13): the standing "Heartbeat" chat every run goes into, born on the first run and born again if it was deleted |
| `kind: "reminder"` | `docs/proactive.md` PR-17: no `recipe`, `values` or agent; fields `text` (the owner's words, 1-200), `runAt` (ISO with offset, a one-shot) with `schedule.cron` "" and `schedule.label` "Once, Wed 7 Oct 17:00" -- OR a `schedule` (repeating) with `runAt` "" -- and `doneAt` (ISO or ""). `project` is where it is listed (default: the root row) and never makes it unavailable or busy. `graceMinutes` is always 720 (PR-19). A one-shot is disabled with `doneAt` the moment its occurrence is decided; re-arming one needs a `runAt` still ahead. Saving takes `runAt` (naive = the machine's zone) or `schedule`, never both. Done one-shots older than 7 days leave `GET /api/library` and `GET /api/automations` |
| `nextRunAt`, `lastRunAt` | ISO 8601 with offset |
| `createdAt`, `updatedAt` | ISO 8601 with offset |

`<records>/library/runs.json` -- a list of:

| field | meaning |
|---|---|
| `id`, `automation`, `n` | run id, owner, run number (never reused after pruning) |
| `trigger` | `scheduled`, `manual` or `event` (`docs/proactive.md` PR-28) |
| `eventKey`, `event` | event runs only: the dedup key (`<uidvalidity>:<uid>`, `<UID>` then a bar then `<start>`, `hook-<hex>`) and `{source, text}`, the payload capped at 4,096 characters. It reaches the prompt only through `{event}` (PR-29); the chat it births gets read-only tools (O-2) |
| `trial` | `true` only on a proposed schedule's first run (`docs/proactive.md` PR-22): never silenced, and if it fails the automation is turned off (written directly, stopping no chat) and the result says "failed on its first run, so its schedule was turned off"; absent otherwise |
| `scheduledFor` | the occurrence (manual: the press) |
| `status` | `pending`, `waiting_busy`, `running`, `needs_you`, `completed`, `failed`, `stopped`, `skipped_missed`, `skipped_busy`, `skipped_unavailable`, `skipped_empty` (a heartbeat whose checklist says nothing: no chat, no model call, PR-10), `skipped_nothing_new` (the precheck printed nothing or what the last completed run saw: no chat, folded, told to nobody, PR-25) |
| `reason` | one line for a skip or a failure, naming the rule |
| `chat` | the run's chat id, or "" |
| `startedAt`, `endedAt`, `seenAt` | ISO or "" |
| `summary` | the first 300 characters of the final answer |
| `occurrenceCount`, `lastOccurrenceAt` | folding (absent = 1) |
| `silent` | `true` on a completed run whose answer was the sentinel with `silence` on (PR-4); it also gets `seenAt = endedAt` (no unread dot); absent otherwise |
| `notify` | AS BUILT (7b): a LIST, one entry per result event, `{event, at, channels: {<name>: {ok, detail, label, service}}, spoken}`; `spoken` is "said", "no desk", "off", "dropped: ..." or "not said: ..." (see 11, phase 7b), or "silent" (a silent run, `channels: {}`) or "quiet" (a failure inside a streak delivered to nobody, `channels: {}`). A `failed` entry also carries `streak` (the count at this failure) and, on the failure that turned the automation off, `turnedOff: true` (`docs/proactive.md` PR-7) |

Final statuses: `completed`, `failed`, `stopped`, and the five skips. A
heartbeat's skips are never delivered (PR-10, PR-12); a heartbeat never
waits (`waiting_busy` is a folded `skipped_busy` at once), a missed check
leaves no row, and none is decided inside quiet hours (PR-11).

A reminder's run (PR-18) is `completed` with `chat` "", `seenAt` set and
`summary` the line it said ("Reminder: <text>", or "Late reminder, set for
HH:MM: <text>" when more than two ticks late). Its `notify` entry records
the channels and the desk; quiet hours never hold it. Its only other
delivery is a `skipped_missed` row (more than 12 h late): one channel
message "Missed reminder, set for <Day HH:MM>: <text>", nothing spoken.

`notify[].spoken` gains "held: quiet hours" (PR-16): the line went to
`<records>/held-speech.json` (`Paths.held_speech`, `{held: [{id, at, text}],
dropped}`, at most 50, older than 24 h expire) and is said in the one
morning line after `quiet_end`.

`<records>/library/tasks.json` -- a list of `{id: "tsk-<8 hex>", project,
text (1-200), done, createdAt, doneAt, source: "owner" | "butler", chat,
agent}`, where `chat` is the conversation Butler added it from (source butler
only) and `agent` the harness that conversation runs as when a coding agent
added it through `POST /api/tasks/agent` ("pi"), read from the conversation's
record, never from the caller; "" for the owner's and the endpoint brain's
tasks (`docs/lantern-ui.md` section 8). Rows written before it lack it.

## 5. Routes and protocol

REST, following the existing plural-collection convention
(`/api/chats/{cid}`, `/api/connections/{cid}`); every body refusal is a 400
naming the rule, an unknown id a 404 naming it, the `KeyError` shape
`ShortcutStore.get` uses.

| route | does |
|---|---|
| `GET /api/library?project=<id>` | the panel's one read: recipes, automations (with next run and last result), open and recent-done tasks, for that project plus Global |
| `GET/POST /api/recipes`, `DELETE /api/recipes/{rid}` | list, create or update, delete (409 naming the automations that use it). Every served recipe row (these and `GET /api/library`) carries `usedBy`: the ids of the automations that run it, read fresh per request (`AutomationStore.used_by`) |
| `POST /api/recipes/{rid}/expand` | `{values}` -> `{text}` or 400 naming the missing placeholder (AU-3) |
| `GET/POST /api/tasks`, `DELETE /api/tasks/{tid}` | list (`?project=`, `?all=1`), add, edit, done/undone, delete. Every served task (this list and `GET /api/library`'s) carries `addedWords` and `doneWords` ("" while open): `schedule.when_words` at the scheduler's clock, e.g. "3:06 PM today", "9:12 AM yesterday", "10:00 AM on Sun 18 Oct" (`docs/lantern-ui.md` Step 6b). Served only, never stored |
| `POST /api/tasks/agent` | loopback only; `task_add` from pi (AU-9) |
| `POST /api/library/agent` | loopback only; `{tool, args, chat}` for `reminder_add` and `schedule_propose` from pi (`docs/proactive.md` PR-20, PR-21). The project is the chat's; the work is `Hub.library_tool`, the endpoint brain's dispatcher too. A refusal is 200 text for the model; 400 for another tool or no chat, 404 for an unknown chat, 403 off loopback |
| `GET/POST /api/automations`, `DELETE /api/automations/{aid}` | list with next run and last result, create or update, delete (history goes with it). Every served row (these, `GET /api/library` and `POST /api/heartbeat`) carries `nextWords`, words the page prints verbatim: "Next: 7:00 AM tomorrow, then weekdays at 07:00" (`when_words` then the label; a one-shot has no "then"; a custom cron says "then on a custom schedule"), "When <source> fires", "Turned off", "Done" (a finished one-shot), or "" with no next run. A heartbeat's next check skips quiet hours |
| `POST /api/automations/bulk` | `{ids, enabled}`; turning one on resets its `failStreak` |
| `POST /api/automations/{aid}/run` | manual rerun, `trigger: manual`, allowed while disabled |
| `GET /api/automations/{aid}/runs` | history, newest first. Each run, and every row's `last`, carries `statusWord` (`automations.WORDS`, e.g. "skipped (busy)") and `group`: `ok` (completed), `failed` (failed, stopped), `needs` (needs_you), `skipped` (the five skips), `pending` (pending, waiting_busy, running); `whenWords` (its `scheduledFor`, e.g. "7:00 AM today"), `tookWords` ("2:31", "" unless it both started and ended) and `lastOccurrenceWords` (a fold's last time, else ""). Served only, never stored |
| `POST /api/schedule/preview` | `{cron}` or `{preset, ...}` -> `{cron, label, next: [5 times], nextWords}` or 400 naming the field |
| `POST /api/heartbeat` | `{project, checklist?, minutes?, enabled?, channels?, silence?}` -> the project's heartbeat as the panel reads it (`docs/proactive.md` PR-8, PR-9); created on first save, updated after; everything is checked before the checklist file is replaced atomically; 400 naming the rule. `GET /api/library`'s `automations` carries `heartbeat` (that row plus `checklist`, `checklistItems` and `awakeWords`: "while you are awake" while quiet hours are on, else "", so the page never names the setting; or null), `heartbeatMinutes`, `heartbeatDefault` and `remindInput` (`{date, time}`, an hour from now in the daemon's zone: a new reminder editor's input values), and its three lists leave heartbeats out. A one-shot reminder row carries `runAtInput` (its `runAt` as `{date, time}`; null for a repeat) |
| `GET/POST /api/triggers`, `DELETE /api/triggers/{name}`, `POST /api/triggers/{name}/test` | trigger sources (`docs/proactive.md` PR-26): `{noun, items, kinds}`, rows `{name, kind, label, configured, problem}` (+ `hook` for a webhook), never a password, link or token. POST writes `<records>/triggers/<name>.json`; a webhook's create answer carries `created: {name, hook, header, token}`, the only time the token is shown. `test` logs in, reads the calendar or reports the hook |
| `POST /api/hooks/{name}` | a webhook source's event: header `X-Butler-Token`, JSON body up to 16 KB. 202 `{ok, coalesced, runs}`; 401 for any wrong, missing or unknown token or name (nothing recorded); 413 over 16 KB; 400 not JSON; 503 while `triggers_enabled` is off. On every listener |
| `GET /api/prechecks` | `{folder, names}`: the precheck scripts an automation may name (`docs/proactive.md` PR-23). Read-only: no route creates, edits or uploads a script |
| `POST /api/answer` with a `prop-` qid | the page's card for a schedule proposal (`docs/proactive.md` PR-21): `{qid, confirmed}` -> `{ok, qid, chat, text}`; 410 when the proposal is gone (answered, replaced or 10 minutes old) and nothing is created |
| `GET /api/notify` | the channel list: `[{name, kind, label, configured, problem, default}]` -- NEVER a token or a chat id. A running inbound Telegram channel's own finding (409: another reader; two inbound channels) is shown as `problem` with `configured` still true (`docs/messaging.md` 8.1) |
| `POST /api/notify` | `{name, kind, label, bot_token, chat_id, inbound?}` -> writes `<records>/notify/<name>.json` (`inbound` stored as a bool, false when absent); same name replaces it; the answer carries no token |
| `POST /api/notify/{name}/test` | sends one test message through that channel; answers the service's own error text |
| `DELETE /api/notify/{name}` | removes the file and its `<name>.offset`; automations naming it then record `failed: channel <name> not found` |

`GET /api/chats` rows gain `origin` (AU-8) and `unread` (AU-20).

Protocol v1 additions for `plan.md` 4.3, daemon -> satellite only; nothing
new flows upstream (the doors use REST plus the existing `typed`):

```
{type:"library", what, project}   # BROADCAST: recipes|tasks|automations|runs
                                  # changed in `project` ("" = global); an
                                  # open panel or page re-reads. Like
                                  # `settings`, a hint, never the data.
{type:"announce", announce_id, text, chat}
                                  # to the DESK only (AU-18), then the existing
                                  # speak_start{announce_id} / PCM /
                                  # speak_end{announce_id} brackets
```

## 6. The UI (wireframes; "keep it visually easy" is judged in phase 0)

Desk, Library open in the right column (300 px), Tasks tab:

```
+--------+-------------------------------------+----------------------+
| tree   |  conversation                       | Library      [^] [x] |
|        |                                     | Recipes Autom. Tasks |
| butler |                                     |  -----        =====  |
|  chat  |                                     | SITE               |
|  chat* |  <- * unread run chat, clock glyph  | [ ] draft bulletin > |
| site |                                     | [ ] email the board >|
|  ...   |                                     | + Add task           |
|        |                                     | BUTLER (global)      |
|        |                                     | [ ] renew passport  >|
|        |                                     | > Done (3)           |
|        | [ type a message...   / recipes ]   | Open full view       |
+--------+-------------------------------------+----------------------+
  ">" is Start (opens a chat with the task as the prompt); [^] refresh
```

Recipes tab and Automations tab, same column:

```
| Recipes             |      | Automations              |
| SITE              |      | SITE                   |
| Weekly review   Run |      | (*) Weekly review        |
| Bulletin draft  Run |      |     Mon 09:00 - next Mon |
| BUTLER (global)     |      |     last: completed  v   |
| Morning brief   Run |      | ( ) Bulletin draft       |
| + New recipe        |      |     skipped: busy x3  v  |
                              | + New automation         |
  (*) enabled toggle; "v" opens the last run's chat
```

Phone (390 px): the same content as a full-width sheet from the right, over
its own scrim, reached from `#headerMorePop`; tabs across the top; never
writes `right_panel` (D-2).

Recipe editor (a dialog over the panel, desk; a full sheet on the phone):

```
+ Recipe ------------------------------------------+
| Name     [ Weekly review                       ] |
| Scope    (o) This project: SITE  ( ) Global    |
| Prompt                                           |
| [ Review what changed since {since=last Monday}] |
| [ ...                                          ] |
| Placeholders: since (default "last Monday")      |
|                        [Delete]  [Cancel] [Save] |
+--------------------------------------------------+
```

Automation editor with the schedule picker:

```
+ Automation --------------------------------------+
| Recipe   [ Weekly review            v]           |
| Project  SITE (from the recipe; global: pick)  |
| since    [ last Monday ]                         |
| Repeat   [ Weekly on   v] [Mon v] at [09:00]     |
|          Daily at / Weekdays at / Weekly on /    |
|          Monthly on the [1st v][Mon v] / Custom  |
| Custom   [ 0 9 * * MON#1     ]  (cron)           |
| Next     Mon 28 Sep 09:00, Mon 5 Oct 09:00, ...  |
| If missed, still run within [60] minutes         |
| Notify   [x] Telegram - Owner's phone (default) |
|          [ ] Telegram - Sam                  |
| [x] Enabled                                      |
|                        [Delete]  [Cancel] [Save] |
+--------------------------------------------------+
  "Next" comes from POST /api/schedule/preview; an invalid cron shows the
  daemon's message under the field and Save stays off.
```

Slash picker (composer starts with `/`):

```
| /wee                          |
| Weekly review      SITE     |  <- Enter inserts the expansion
| Weekly budget      global     |     (placeholders inline first)
[ /wee                        ] [send]
```

The full TASKS & AUTOMATIONS page (a surface like settings):

```
+ Tasks & Automations -------------------------------------- [x] +
| Automations | Tasks        Filter: [All v] [Any result v]       |
|--------------------------------------------------------------- |
| [ ] Enable  [ ] Disable  (2 selected)                           |
| SITE                                                          |
| [x] (*) Weekly review    Mon 09:00   next Mon 28 Sep  completed |
| [x] ( ) Bulletin draft   Fri 16:00   --               busy x3   |
| BUTLER                                                          |
| [ ] (*) Morning brief    Weekdays 07:30  next Mon    failed     |
|--------------------------------------------------------------- |
| Weekly review - history                            [Run now]    |
|  #12 Mon 21 Sep 09:00  completed  4m 10s   open chat           |
|  #11 Mon 14 Sep 09:00  skipped: missed (PC off)                 |
|  #10 Mon  7 Sep 09:02  needs you -> completed   open chat       |
|        owner: failed: 401 Unauthorized                        |
+-----------------------------------------------------------------+
```

The tree's unread dot: a run chat is drawn with a filled dot before its
title and a clock glyph after, both named in `aria-label` ("unread
automation run"); the project line's rolled-up dot counts it; opening the
chat clears it everywhere (AC-174: never colour alone).

## 7. Notification channels; Telegram the first [OWNER R-A, DECIDED detail]

The owner's constraint decides the shape: "keep it modular so i can copy
telegram skill and add a different bot to different pc/ users in the
future." So a bot is a FILE, a kind of service is an ADAPTER, and nothing
about either is in the code that runs automations.

- **The port.** `ports.Notifier` with one method,
  `async send(title: str, text: str) -> NotifyResult`, where `NotifyResult`
  is `{ok: bool, detail: str}` (`detail` is the service's own error text on
  failure, "" on success). It never raises for a delivery failure; it raises
  only for a programming error. The runner calls it and knows no service.
- **The channel file.** One JSON file per bot at
  `<records>/notify/<name>.json`; the file stem IS the channel name,
  `[a-z0-9-]{1,32}`:

  ```
  {"kind": "telegram", "label": "Owner's phone",
   "bot_token": "<digits>:<35+ chars>", "chat_id": "123456789",
   "inbound": false}
  ```

  `inbound` (two-way Telegram, `docs/messaging.md` section 8) is optional
  and not secret; `true` makes `chat_id` the owner and Butler reads the bot.
  Its read position lives beside the file as `<name>.offset`.

  `bot_token` is the same key the telegram skill's `config.json` uses, so a
  bot is moved by copying one value or one file. **Adding a second bot for
  another user, or taking this bot to another PC, is dropping a file in
  that folder** (or the settings page's Add, which writes the same file) --
  no code change and no restart: the registry re-reads the folder by stat,
  as notes are. Written with `memory._atomic`.
- **The registry.** `daemon/notify.py` lists the folder and builds each
  channel through a CLOSED table `{"telegram": TelegramNotifier}` -- the
  terminal agents' precedent: a file cannot name code. A new service (email,
  ntfy) is one adapter plus one table line. A malformed file is NOT dropped
  silently: it is listed `configured: false` with `problem` naming the rule
  ("bot_token is not <digits>:<35+ chars>", "unknown kind 'slack'"), and a
  send through it records that problem on the run.
- **The default.** `notify_default`, a text setting in a new `notify` group,
  naming one channel. Adding the FIRST channel while it is blank sets it (an
  explicit write, visible on the settings page). A new automation starts
  with `channels = [notify_default]` -- the owner's "on by default" -- and
  the editor shows it ticked; with no channel configured the editor says
  "No notification channel yet -- add one in Settings" and stores `[]`.
  **No fallback anywhere**: a channel named by an automation that no longer
  exists records `failed: channel <name> not found`; it is never rerouted to
  the default. *Losing option:* a `default: true` flag inside each file -- a
  copied file would carry it onto a machine that already has a default, and
  "exactly one" would be an invariant across files.
- **What the page learns.** `GET /api/notify` answers name, kind, label,
  `configured`, `problem` and `default` -- never `bot_token`, never
  `chat_id` (a chat id plus a leaked token is the whole bot). The settings
  page renders ONE simple block in the `notify` group, the connections
  precedent: a row per channel (label, kind, "Send test", "Remove") and "Add
  channel" (kind, name, label, token as a password field, chat id). Replacing
  a token is Add with the same name. There is no Edit that shows a token.
- **How the owner enters the first one.** Paste the token from @BotFather
  (or from the telegram skill's `config.json`) and the chat id, press "Send
  test", read Telegram's own answer. Butler does NOT read the skill's file: a
  second source is a fallback, and a fallback is how a stale token keeps
  "working" in one place and failing in another.
- **The Telegram adapter.** The skill's approach (`tg.py:38`, `:96-141`):
  stdlib `urllib.request` POST to
  `https://api.telegram.org/bot<token>/sendMessage`, inside
  `asyncio.to_thread` with a 15 s timeout. Plain text, no `parse_mode` (a
  model's Markdown is not Telegram's and a malformed entity fails the whole
  send). Body: `title` = `"<recipe> in <project>: <status>"`, then the first
  600 characters of the answer, cut at a line end. The URL contains the
  token, so the adapter's error text is built from the HTTP status and
  Telegram's `description` field, never from the exception string.
- **Failure.** Each channel is sent independently and recorded on the run as
  `notify.channels[<name>] = "sent" | "failed: <reason>"`, shown in history,
  printed once to the log without the token. It NEVER changes the run's
  status and is never retried in a loop; the next run tries again. Sent on:
  completed, failed, needs_you, and the FIRST row of a folded skip.
- *Losing options:* a write-only `secret` SETTING kind (the previous draft) --
  one token per machine, so a second bot for a second user is a code change,
  which is what the owner ruled out; a pointer (`{"from": "env"}`) like
  `connections.py` -- the owner edits an environment variable, which the
  "options belong on the settings page" ruling rejects.

## 8. Voice [DECIDED]

New `settings.spoken_*` parsers, matched in `_on_final` BEFORE the brain,
beside the project and memory branches, and never routed through a model.
The project is the one of the conversation the device is showing
(`chat_for(s)`).

- **"run recipe X" / "run X".** Name matched exactly after `_spoken_form`
  against this project's and global recipes. The explicit form refuses aloud
  when nothing matches; the BARE form falls through to work unless it names a
  recipe exactly, so "run the tests" still reaches the agent. Two matches are
  refused aloud naming the count (the `AmbiguousProject` precedent). A
  placeholder without a default is refused aloud naming it ("That recipe needs
  a value for since. Run it from the Library.").
- **"add task X"** adds to this project with source owner; read back "Added
  task: X."
- **"what are my tasks"** reads this project's open tasks, oldest first,
  numbered, at most five, then "and N more".
- **"mark task N done" / "mark X done"** -- N is the number just read back, X
  an exact normalized text match; read back "Marked done: X." so a mistake is
  heard at once, and undoable in the panel. No match or two matches: refused
  aloud, nothing changes.

Read-backs stay English (`language.md` D5).

## 9. Criteria (AC-447..AC-474 reserved)

- **AC-447 (recipe store).** A recipe is one `.md` file under
  `<records>/library/recipes/`, written atomically; a hand edit is served on
  the next read; names are unique per scope after normalization; every refusal
  names its rule. **Recoverable.**
- **AC-448 (one expansion).** Every door fills placeholders through
  `library.expand`; a missing value without a default is refused naming it and
  nothing is sent; `{{`/`}}` are literal. A source-level test finds no second
  substitution. **Recoverable.**
- **AC-449 (panel Run and task Start).** Each births a chat in the item's
  project (a global recipe: the project on screen) through
  `POST /api/session/new` and sends the text as an ordinary `typed` frame; a
  task is NOT marked done by starting it. **Recoverable.**
- **AC-450 (slash).** `/` at the start of the composer opens a picker of
  in-scope recipes; choosing one inserts the expansion and sends nothing.
  **Recoverable.**
- **AC-451 (voice run).** As section 8: exact, bare-falls-through,
  explicit-refuses, ambiguity and missing placeholder refused aloud; a match
  births a chat, sends `opened`, and runs the text as that device's turn.
  *Fails as:* a coding agent started on a guess. **NOT recoverable** once it
  has written.
- **AC-452 (tasks).** Add, edit, done, undone and delete per project row;
  the root row's tasks are the Global section; a removed row's tasks stay
  listed under "project removed". **Recoverable.**
- **AC-453 (voice tasks).** "add task", "what are my tasks", "mark N / X
  done" behave as section 8, each with a read-back; no match or two matches
  change nothing. **Recoverable.**
- **AC-454 (task_add).** Offered exactly where the memory tools are; the
  project is the turn's chat's, never a model argument; at most 3 per turn;
  the memory write scan refuses what it refuses for notes; the task carries
  `source: butler` and `chat`; the reply ends "Added task: ...".
  **Recoverable.**
- **AC-455 (schedule parser).** Presets compile to cron; the parser accepts
  AU-11's grammar and refuses anything else naming the field; an
  expression with no occurrence in 5 years is refused; `preview` returns the
  next five times. **Recoverable.**
- **AC-456 (local time and DST).** Spring-forward runs at the first valid
  minute after; fall-back runs once. Proved with a fixed zone in pytest.
  **Recoverable.**
- **AC-457 (survives restart).** `nextRunAt` is persisted after every
  decision; a restart neither repeats a decided occurrence nor loses one
  still within grace. **Recoverable.**
- **AC-458 (decide, never block).** The tick's decision has no `await` in it;
  it never awaits a run's end; a dispatch that hangs does not delay another
  automation's decision; every disk touch is off the loop (D-1). **NOT
  recoverable** in the moment -- a frozen loop is a dead microphone.
- **AC-459 (missed).** Within grace at boot: exactly one run, the latest
  occurrence. Beyond: one `skipped_missed` row whose count is the number of
  missed occurrences. **Recoverable.**
- **AC-460 (busy never stops the owner).** A busy project (either cap) puts
  the run in `waiting_busy`; it starts if the slot frees within grace, else
  `skipped_busy` naming the limit; no code path in this feature calls
  `stop_conversation` or `abandon`. *Fails as:* the owner's work killed by a
  schedule. **NOT recoverable.**
- **AC-461 (the birth keeps focus).** After a run's birth, `current_chat_id()`
  is unchanged, the previous conversation is still held, the router history is
  unchanged, and a hands-free turn lands in the owner's conversation.
  *Fails as:* the owner's conversation silently replaced at 09:00.
  **Recoverable** but invisible, so it is pinned by test.
- **AC-462 (one turn path, silent).** A run enters through `_typed` on a
  `RunSession`; with `speak` ON it synthesizes zero audio and never waits on
  playback; its chat is titled "<recipe> - <date>" and listed with `origin`.
  **Recoverable.**
- **AC-463 (approval).** A question raised in a run chat makes the run
  `needs_you`, reaches every page through the existing `question` broadcast,
  and an answer lets the run continue to `completed`. **Recoverable.**
- **AC-464 (folding and cap).** Identical consecutive skips fold
  (`occurrenceCount`, `lastOccurrenceAt`); history holds at most 50 rows per
  automation and never evicts a non-final row. **Recoverable.**
- **AC-465 (manual rerun and unavailable).** "Run now" works while disabled
  and records `trigger: manual`; a removed project or recipe makes each due
  occurrence a folded `skipped_unavailable` naming which, and the automation
  stays. **Recoverable.**
- **AC-466 (one right panel).** `right_panel` replaces `files_open` (migrated
  once); Files and Library never draw at once; a phone writes nothing. No
  browser storage (AC-8). **Recoverable.**
- **AC-467 (Library panel scope).** The panel shows the project on screen
  plus Global and follows it as AC-311 does; every list re-reads on the
  `library` frame. **Recoverable.**
- **AC-468 (full page).** Every project grouped; filters by enabled and last
  result; bulk enable/disable; Run now; history with open-chat links; the page
  names no setting (AC-41). **Recoverable.**
- **AC-469 (unread).** A run chat is unread until opened on any device;
  drawn with a dot plus an accessible name; opening clears it everywhere.
  **Recoverable.**
- **AC-470 (spoken, desk only, never preempting).** A run's line is spoken
  only on a session whose socket peer is loopback, open, TTS-capable,
  wake-listening and with `speak` on; queued by AC-14's states on that
  session; with no such session nothing is spoken, nothing is held, and the
  run records `notify.spoken = "no desk"`. A phone that took the last turn
  never speaks it. *Fails as:* a result read aloud from a pocket, or a
  device_id string claiming to be the desk. **Recoverable.**
- **AC-471 (a token never leaves its channel file).** A bot token is written
  only to `<records>/notify/<name>.json`; `GET /api/notify`, the `POST`
  answer, every frame, log line, trace, error text and chat carry neither the
  token nor the chat id. *Fails as:* a bot token on the tailnet. **NOT
  recoverable** without revoking it at @BotFather.
- **AC-472 (delivery).** Each named channel is sent independently, off the
  loop, 15 s timeout, plain text, at most 600 characters of answer; each
  result is recorded per channel on the run; a failure changes nothing else
  and is not retried in a loop; a missing or malformed channel records a
  failure naming it and is never rerouted to another channel. **Recoverable.**
- **AC-473 (channels are files, kinds are a closed table).** A channel file
  dropped into `<records>/notify/` (a copy from another PC, or a second bot)
  is listed and usable on the next read with no restart and no code change;
  `kind` resolves only through the closed adapter table; a malformed file is
  listed with `configured: false` and a `problem` naming the rule, never
  hidden. **Recoverable.**
- **AC-474 (on by default).** A new automation starts with
  `channels = [notify_default]` when that setting names a configured
  channel; adding the first channel while `notify_default` is blank sets it;
  with none configured the editor says so and stores `[]`. Unticking is
  per automation and persists. **Recoverable.**

## 10. Gherkin (the flows that can go wrong)

```gherkin
Scenario: AC-457/AC-462 a scheduled run opens its own chat
  Given an enabled automation "Weekly review" in SITE due Mon 09:00
  And the owner's current conversation is in BUTLER
  When the clock reaches Mon 09:00
  Then a new chat titled "Weekly review - Mon 28 Sep 09:00" exists in SITE
  And the run is "running" and then "completed"
  And the owner's current conversation and router history are unchanged

Scenario: AC-459 a run missed while the PC was off, within grace
  Given an automation due at 09:00 with a 60 minute grace
  When the daemon boots at 09:40
  Then exactly one run starts for the 09:00 occurrence

Scenario: AC-459 missed beyond grace, several times
  Given a daily automation and the PC off for three days
  When the daemon boots
  Then one "skipped: missed" row carries a count of 3 and no run starts

Scenario: AC-460 the project is busy, then frees
  Given the owner's chat in SITE is answering
  When the SITE automation comes due
  Then the run is "waiting_busy" and the owner's turn keeps running
  When the owner's turn ends 10 minutes later
  Then the run starts on the next tick

Scenario: AC-460/AC-464 the project stays busy past grace, every day
  Given the SITE agent is busy through each day's grace window
  When the automation comes due on three days
  Then one "skipped: busy" row carries a count of 3
  And the owner's turn was never stopped

Scenario: AC-463 a run needs approval
  Given SOUL.md asks first before deleting files
  When a run's agent asks "Delete 40 old drafts?"
  Then the run is "needs_you" and the question is shown in its chat
  And the automation's channels say the run needs you
  When the owner answers "No"
  Then the run continues and ends "completed"

Scenario: AC-472 Telegram is down
  Given an automation notifying channel "owner"
  And Telegram answering 502
  When a run completes
  Then the run is "completed" with notify.channels.owner "failed: 502 Bad Gateway"
  And the next run tries again

Scenario: AC-472 one of two channels fails
  Given an automation notifying "owner" and "sam"
  And the "sam" file was deleted
  When a run completes
  Then "owner" is "sent"
  And "sam" is "failed: channel sam not found"
  And nothing is sent to any other channel instead

Scenario: AC-473 a bot copied from another PC
  Given the file "sam.json" is copied into <records>/notify/
  When the settings page lists channels
  Then "sam" is listed as configured, with its label and no token
  And it can be ticked on an automation without a restart

Scenario: AC-474 a new automation notifies by default
  Given notify_default is "owner"
  When the owner creates an automation
  Then its channels are ["owner"] and the editor shows it ticked

Scenario: AC-470 the desk is not listening
  Given the only connected device is the phone
  When a run completes
  Then nothing is spoken on any device
  And the run records notify.spoken "no desk"

Scenario: AC-471 the token never leaves the daemon
  Given the owner added a Telegram channel with a token and a chat id
  When any page reads /api/notify or receives any frame
  Then it sees the channel's name, label and configured state
  And never the token or the chat id

Scenario: AC-451 a bare "run" that is not a recipe
  Given no recipe named "the tests"
  When the owner says "run the tests"
  Then the words reach the agent as ordinary work

Scenario: AC-451 an explicit run that matches nothing
  When the owner says "run recipe weekly revue"
  Then Butler says it has no recipe called that and nothing starts

Scenario: AC-453 marking a task by number
  Given Butler just read "1, draft bulletin. 2, email the board."
  When the owner says "mark task 2 done"
  Then Butler says "Marked done: email the board."
```

## 11. Build plan

Red then green in every phase; each commit quotes its scoped pytest run
(`python -X utf8 -m pytest <files>`, exit code read). Browser checks use
`playwright-cli` against a SCRATCH daemon: `BUTLER_DATA` set to a fresh
folder OUTSIDE the repo (`paths.resolve` refuses one inside the code tree),
`hotkeys_enabled: false`, `BUTLER_ASR=fake`, `BUTLER_TTS=fake`, a free port
that is neither 8140 nor 8765, killed by its launch PID; a scratch project
row points at a fixture folder, never a real repository. Scheduling tests
inject a clock into the scheduler; no test sleeps to a wall-clock minute.

**THE ORDER IS THE OWNER'S (R-D): phase 0 first, the owner approves the
look, and only then phases 1-8.** No daemon or page code is written while
the mock is unapproved; a change the owner asks for on the mock is made on
the mock.

**Phase 0 -- mock.** `web/mocks/t-library.html`, linked from
`web/mocks/index.html`: the Library panel's three tabs, both editors, the
run-with-values prompt, the slash picker, the channel picker, the full page
and the phone sheet, in dark and light, screenshotted at 1440x900, 1024x768
and 390x844 plus each editor, the full page and the sheet. *Verify:*
screenshots in `TMP/library-mock/`; the owner's look. **Gate: the owner's
approval.**

**Phase 1 -- the store.** `daemon/library.py`: recipes, tasks, `expand`,
`paths.Paths.library`; the recipe and task routes plus `expand`.
`test/test_library.py` (AC-447, AC-448, AC-452) and a routes file.
*Mutation:* substitute placeholders in a route instead of `expand` and watch
AC-448's test fail.
**BUILT 2026-09-26, uncommitted.** `daemon/library.py` (`Library`,
`RecipeStore`, `TaskStore`, `expand`, `placeholders`, `TOOLS`),
`Paths.library` (a property: always `<records>/library`), eight routes in
`butlerd.py`. Three things the plan did not say. A hand-edited recipe whose
braces will not parse is KEPT and served with a `problem` (Run refused) rather
than hidden. `memory.scan` was split so `scan_content` is the one copy of
AC-436's patterns that a task and a note both pass. And `TYPED_MAX_CHARS`
moved into `settings.py` (the hub re-exports it), because `expand` caps at it
and cannot import the hub. The 409 for a recipe an automation uses waits for
phase 5 -- no automation exists before it. Tests: `test_library.py`,
`test_library_routes.py`; D-13 8/8 caught (the named mutation included).

**Phase 2 -- the panel, Recipes and Tasks.** `right_panel` plus its
migration (AC-466), `web/library.js`, `#libraryBtn`, panel Run and task Start
(AC-449), the slash picker (AC-450), the `library` frame (AC-467), the phone
sheet. *Verify:* `test_shortcuts.py`, a structural page test, and
`playwright-cli`: run a recipe and start a task in the fixture project, both
arriving as ordinary turns; the files panel still works (its tests rerun).
**BUILT 2026-09-26, uncommitted**, and compared with the approved mock at
1440 dark/light, 1024 and 390. `right_panel` is ONE value app.js holds
(`setRight`), so both panels repaint from it; `files.js` lost its own bool.
Run and Start reuse the New chat gate and birth, then `sendTyped`. What is
NOT the mock, on purpose: the "Open Tasks & Automations" footer (phase 6 has
no page to open), the recipe editor's in-prompt placeholder chips and the
preview's bold values (both need a second placeholder parser on the page,
which AC-448 forbids -- the daemon's placeholder list is shown under the
field instead), the editor's "Used by" row, and the Automations tab, which
says in words that automations are not built yet. Tests:
`test_library_page.py`, the amended `test_file_explorer_page.py` and
`test_shortcuts.py`.

**Phase 3 -- voice and `task_add`.** The parsers and `_on_final` branches
(AC-451, AC-453), `TASK_RULES`, the endpoint tool, `POST /api/tasks/agent`
and the pi extension's `task_add` (AC-454). *Verify:* parser tests and hub
tests on fake engines; one live `task_add` on the scratch daemon with a real
brain. *Mutation:* let the bare form refuse and watch "run the tests" fail.
**BUILT 2026-09-26, uncommitted**, except the ONE LIVE `task_add` with a real
brain, which was not run (no provider on the scratch daemon). A spoken run
with conversations ends the command turn and begins a NEW turn of the same
device in the born chat with `final.reason = "recipe"`; that reason skips
every spoken-command branch (a recipe whose prompt says "run ..." is work,
not a second run), and the page adopts it for the chat on screen. pi learns
its conversation from `--butler-chat` (`HarnessSpec.chat_id`), so the route
files the task under that chat's project; the endpoint brain holds no chats
and files under the root. `TASK_RULES` is its own block right after
`MEMORY_RULES`. Tests: `test_library_voice.py`; D-13 10/10 caught.

**Phase 4 -- the schedule engine, pure.** `daemon/schedule.py`: cron parse,
presets, next occurrence, latest occurrence, DST (AC-455, AC-456).
`test/test_schedule.py` with a fixed zone (`America/New_York`, whose 2026
transitions are known) and a table of expressions from Orca's own tests.
**BUILT 2026-09-26, uncommitted**: 125 tests, 7 mutations each caught. Two
things this plan did not know. `zoneinfo` has NO data on this Python
(`tzdata` is not installed), so the test zone is hand-built and the daemon
gets `schedule.local_zone()`, an OS-backed PEP 495 zone. And "missed" stays
phase 5's: the engine offers `between` (the count) and `latest_at_or_before`
(the one run); the grace formula is not in it.

**Phase 5 -- automations, the scheduler and the runner.** The automation and
run stores, `focus=False` through the one birth (AC-461), `RunSession`
(AC-462), the tick and the queue (AC-457, AC-458), missed, busy, folding,
unavailable, manual (AC-459, AC-460, AC-464, AC-465), approval (AC-463), the
Automations tab and both editors. *Verify:* pytest with the injected clock
and the fake harness for the logic; then a REAL pi child on the scratch
daemon for the parts a stub cannot prove (`test/AGENTS.md`: a concurrency
test driven by a stub proves nothing about concurrency) -- a run while the
owner's scratch chat in the same project is busy, and a run that asks a
question. *Mutation:* call the existing birth without `focus=False` and watch
AC-461 fail; add an `await` of the run into the tick and watch AC-458 fail.
**BUILT 2026-09-26, uncommitted.** `daemon/automations.py`
(`AutomationStore`, `Scheduler`), `RunSession` and the `headless` rule in
`hub.py`, `focus`/`origin` through `Hub.new_conversation` ->
`_Host.new_conversation` -> `ChatStore.rotate`, `_Host.refusal_for`, seven
routes in `butlerd.py`, the 409 on a used recipe, the Automations tab and the
editor (`web/automations.js`). What the plan did not say. **`_typed` had a
barge-in in it** that would have cut the owner's reply off at 09:00, and the
room's barge-in would have killed a run on the next wake word -- so
`headless` is read in four places (`_typed`, `_barge_in`, `_speaking`,
`_reply`/`_remember`), which is section 13's "RunSession may not be as clean
as a null socket" come true. **The router's short memory is global**, not per
chat, so a run turn neither reads nor writes it. **A busy project is judged
BEFORE the birth** (`refusal_for`, a read-only copy of `plan()` pinned by a
parity test), because `CapReached` inside a run's turn would be recorded as
its answer; the sub-second race left between that check and the agent's own
claim is stated in `daemon/AGENTS.md`. **`origin` is on the chat RECORD**, not
the first message, and carries the finished title. **A restart re-dispatches
a `pending` row within grace** rather than deciding its occurrence again.
**Channels are stored, not sent**: a new automation takes `notify_default`
when that setting names one; the editor says nothing is sent yet (7b).
**Live on a scratch daemon with a real pi** (`codexbridge-local/gpt-5.6-sol`,
fixture project): a daily schedule two minutes ahead ran at 22:19:31 and
completed at 22:19:43 in a new chat "List and summarise - Sat 26 Sep 22:19"
while the current chat stayed where it was; the first attempt, on a model the
provider refuses, recorded `failed`; a Run now pressed while the owner's own
FIXTURE turn was working went `waiting_busy` ("the project's agent limit is
full; nothing was stopped"), the owner's turn finished whole, and the run
started on the next tick after it (22:22:31 to 22:22:45). Starting it
disposed the owner's now-IDLE child by D1=A and said so in their chat, as
section 1 predicted. Not run live: a run that asks a question (pytest, fake
agent) and skipped_busy/missed (pytest, injected clock). Tests:
`test_automations.py` (37), `test_automations_page.py`; D-13 17/17 caught.

**Phase 6 -- the full page** (AC-468). *Verify:* `playwright-cli` on the
scratch daemon with seeded stores: filters, bulk, rerun, history links.
**BUILT 2026-09-26, uncommitted**, in `web/automations.js` beside the editor
and compared with the approved mock at 1440 dark/light, 1024 and 390: the
Automations tab (search, project, on/off and last-result filters, a bulk bar
for Turn on / Turn off / Run now, rows grouped by project, the selected
automation's history with Run now, Edit and Open chat, under the list below
1180px) and the Tasks tab (every project, Open/Done/both, added-by, add,
Start). Doors: the library footer and, on a phone, the header overflow.
Not the mock, on purpose: "Next" reads the browser's own date format, the
weekly picker offers one day (a several-day schedule opens as Advanced), the
monthly picker has no "last" (the engine has no `L`), and the run chat's
clock glyph and unread dot are phase 7's.

**Phase 7 -- results.** The unread dot (AC-469); the AC-14 announcer and the
spoken line (AC-470), which also closes AC-14/AC-14a for their original
worker case; the `Notifier` port, `daemon/notify.py`'s registry and closed
kind table, the Telegram adapter, the `notify` group and its channel block,
and the channel routes (AC-471..AC-474), against a local fake Bot API whose
base URL is passed to the adapter's constructor. *Mutation:* put the token
into `GET /api/notify`'s row and watch AC-471's test fail; reroute a missing
channel to the default and watch AC-472's test fail. *Verify:* pytest;
`playwright-cli` for the dot; ONE real Telegram message to the owner's chat,
only with the owner's go-ahead.
**7a, NOTIFICATION CHANNELS ONLY, BUILT 2026-09-26, uncommitted.** Split off
so it could be built beside phases 5-6; runs are NOT wired to it (that is
7b, with the dot and the announcer). `ports.Notifier` + `NotifyResult` +
`NOTIFIERS` (the sixth port, argued in `plan.md` 4.10);
`daemon/adapters/notify_telegram.py`; `daemon/notify.py` (`Channels`: the
folder re-read by stat, `save`, `remove`, `send`, `clip`, and the four
routes); `paths.Paths.notify`; the `notify` group with `notify_default`
and `Group.collection = "/api/notify"`, which `web/settings.js`
(`buildCollection`) renders generically. Three details differ from the text
above: `GET /api/notify` answers `{noun, items, kinds}` (the page needs the
kinds' fields to build Add without naming one); the test double sits at the
module-level `notify_telegram.API` (a constructor `api=` also exists) and
there is no `fake` KIND, because a registered fake would be a kind a
channel file could name; and a channel file with an unknown field is
listed with that problem rather than ignored. The 600 cap marks a cut with
a trailing `...` inside the 600, and the title is one line of at most 200.
`test/test_notify.py`: 37 tests; D-13 EXECUTED, exit codes only: 7 mutations,
each caught by every test aimed at it, 10/10 runs (token on the row, no scrub, no cap, reroute a missing
channel, send on the loop, no stat reload, `parse_mode` added).
`playwright-cli` on a scratch daemon: Add wrote the file, filled
`notify_default` through the settings broadcast, cleared both password
inputs, and neither `GET /api/notify`, `GET /api/settings` nor the page's
HTML carried the token; Remove emptied the list. The real Telegram send is
NOT done -- it waits for the owner.
**7b, RESULTS, BUILT 2026-09-26, uncommitted.** A run's end is now sent,
spoken and marked. **WHICH EVENTS NOTIFY** (amended by `docs/proactive.md`
P1, built 2026-10-07: a completed run that answers the silence sentinel
notifies nobody, and failures follow the streak policy PR-7 -- the first
delivers, the rest are quiet except one reminder a day, and the Nth turns the
automation off with one message): completed, failed, the FIRST
question of a run (needs you; a second question in the same run is the same
"needs you"), and a NEW skip row -- a fold into an existing skip does not,
so a PC off for a week or a project busy every morning is one message per
streak (the text above's "FIRST row of a folded skip", kept). `stopped` is
the owner's own act and notifies nobody. The desk speaks only completed,
failed and needs you, never a skip. `Scheduler._result` starts ONE task per
event AFTER the status is on disk (`automations.py`); the channels and the
desk run concurrently inside it, and each records its own half on the run.
`notify` is a LIST, one entry per event, not the single object section 4
drew, because a run that needed you and then completed has two results and
the second must not erase the first. Each channel entry carries the
channel's `label` and the adapter's `service` word ("Telegram") at send
time, which is what the history draws: "Telegram sent to Owner's phone",
"Telegram to Owner's phone failed: 401 Unauthorized". **THE REGISTRY** is
built once in `build_hub` (`hub.channels`) and handed to the `Scheduler`
and to `notify.add_routes` alike; `make_app` no longer builds its own.
**THE DESK** is a session whose socket PEER (`request.remote`, recorded by
`Hub.handle`) is in `hub.LOOPBACK` -- ASSUMED to be how the owner's desk
browser connects; section 13's warning stands and was NOT measured against
the owner's real desk, because that daemon is on 8140 and this session was
told never to touch it. "Listening" needed a new inbound frame, `caps`
(`plan.md` 4.3): `hello.caps` goes once at boot, before the page arms its
microphone and usually before its speaker exists, so it could not say it.
`Hub.announce` is the minimal AC-14 announcer: it waits while the desk is in
any state but idle (AC-14 a/b) and speaks at the boundary, speaks at once on
an idle wake-listening desk (c), and is DROPPED after `ANNOUNCE_WAIT_S`
(120 s) rather than held -- a deviation from AC-14, which queues without a
bound. It mints a turn on the desk, sends `{type:"announce", turn, text,
chat}` so the page adopts it only when idle, then the ordinary
`speak_start`/PCM/`speak_end` and `_rearm`; `keep=False`, so it is heard and
in no transcript; the wake word still barges in on it. NOT built: AC-14a's
hold-and-replay (ruled out for runs, AU-18), AC-14's seam cues, and the
WORKER case -- there are no background workers to announce. **UNREAD:**
`POST /api/session/open` calls `Hub.chat_seen`, which stamps `seenAt` on the
chat's runs and broadcasts `{type:"library", what:"runs"}`; `GET /api/chats`
rows gain `unread`; the tree draws a dot, a bold title and the clock glyph,
all named in the row's label, and rolls the dot up to the project line.
**THE RACE PHASES 5-6 LEFT, FIXED:** a turn the owner starts between the
runner's busy check and the run's own slot claim makes the agent limit
refuse the RUN. `HarnessConverse._refuse` now notes it
(`_Host.note_refused`), the watcher takes it and puts the run back to
`waiting_busy` -- never `completed` with the refusal as its summary -- and
the next tick retries it in the SAME chat, where the refusal stays on the
record (or skips it past grace, the busy rule). **One defect from 7a fixed
on the way:** the editor read `GET /api/notify`'s `.channels`, which does not
exist (the route answers `items`), so it always said "No notification
channel yet". Tests: `test_automations_results.py` (15),
`test_automations_page.py` (+4), `test_protocol_doc.py` (the guard now
allows an empty unbuilt list once its marker is gone); D-13 EXECUTED, exit
codes only, 11 mutations each caught (one re-aimed: awaiting the send AFTER
the record is not a defect, sending BEFORE it is). **Live on a scratch
daemon** (fake harness, `BUTLER_ASR/TTS=fake`, 127.0.0.1:8819, a local fake
Bot API): Run now completed, the fake API received ONE `sendMessage`
("List and summarise in FIXTURE: completed" plus the answer), the loopback
socket with `caps` wake+tts received `announce`, `speak_start`, 147,648
bytes of audio and `speak_end`, the second socket (device_id "desk", not
listening) received none of them, and `playwright-cli` showed the dot, the
bold title and the clock, then the dot cleared on the opening tab AND on a
second tab with no reload.

**Phase 8 -- DOX pass.** `daemon/AGENTS.md` (stores, scheduler, the
`focus` birth, `RunSession`, the desk-only announcer, the `Notifier` port
and channel files, the security statement), `web/AGENTS.md` (`right_panel`, panel, page, slash, dot),
`test/AGENTS.md` (clock injection, the fake Bot API), `plan.md` 4.3 frames as
a factual correction. Full suite once, since the birth, `ShortcutStore` and
`settings.py` are shared.
**DONE 2026-09-26 with 7b**: the four AGENTS.md files, `CLAUDE.md`'s backlog
entry and `plan.md` 4.3 (`caps` inbound; `announce` moved from the unbuilt
list, which it was the last of, to the built one). The FAST tier was run,
not the slow/GPU tiers. Still open after this spec: the ONE real Telegram
message to the owner's chat (their go-ahead), reading the peer address of the
owner's real desk page (section 13), promoting AC-447..AC-474, and the
worker half of AC-14/AC-14a, which waits for workers to exist.

## 12. Security, stated plainly

- **Automations act unattended.** With `harness_tools` at `full`, a schedule
  starts a coding agent that writes and runs commands while nobody watches.
  Under R2 (the tailnet is the boundary), any tailnet caller can now CREATE
  an automation with two unauthenticated posts and have it recur -- a
  persistent version of what R2 already accepted for a one-off start. This
  spec does not change R2; it widens what R2 reaches, and says so.
- **Prompt injection.** The recipe text is the owner's; what a run reads is
  not (web pages, files, issue text). An injected instruction can steer an
  unattended agent, and a channel gives its output a path OFF the machine.
  Bounds: 600 characters, per-automation channel ticks, SOUL.md's
  ask-first rule reaching the run through the question path, and
  `read_only` as the default Tools value. None is a boundary.
- **`task_add` from fetched content** can plant a to-do. Bounds: the write
  scan, 3 per turn, `source: butler` shown on the row, and a task is text
  that starts nothing until the owner presses Start.
- **Unattended authority is the owner's ruling, not an oversight (R-C).**
  Runs follow the Tools setting; nothing here caps them.
- **A bot token** is stored in plain text in its channel file in the
  records folder -- the same exposure as the skill's own `config.json` --
  and never leaves the daemon otherwise (AC-471). A run's first lines go to
  Telegram's servers. Copying a channel file to another PC copies the token
  with it, which is the point and is also the exposure.
- **`POST /api/notify` is a new unauthenticated WRITE, and it is an
  exfiltration path.** Under R2 a tailnet caller can add a channel pointing
  at THEIR bot and tick it on an automation, and every run's first 600
  characters then leave for a chat the owner never sees. Not new in kind (R2
  already lets that caller start an agent that could send anything), but it
  is persistent and quiet. Bound: every channel is listed on the settings
  page by label, and the automation editor shows each ticked channel.

## 13. What this could have wrong

- **`RunSession` may not be as clean as a null socket.** `_speak`,
  `_await_playback`, `_rearm` and the heartbeat were written for a device;
  phase 5 measures a run with `speak` on before trusting AC-462.
- **`focus=False` may not be the only hidden "current" assumption.** Two were
  found by reading (the unhold and the history clear); an attachment or the
  hands-free resolver may hold a third. AC-461's hands-free check is there to
  find it.
- **"The desk" as a loopback socket peer may be wrong for this machine.**
  If the owner's desk browser reaches Butler by its tailnet or LAN address
  rather than `127.0.0.1`, no session is the desk and nothing is ever spoken.
  Phase 7 reads the peer address of the owner's real desk page before
  trusting AU-18; if it is not loopback, the owner rules how a desk is named.
- **A 60-minute grace is a guess**; Orca's own audit template uses 720.
- **The bare "run X" can still capture work** if a recipe is named like an
  instruction ("tests"); the explicit form is the safe one to teach.
- **"Channel" may be the wrong unit for a second USER.** A channel is a
  destination; nothing here says which user an automation belongs to. For
  one owner with two bots that is enough; a truly second user on one daemon
  would want their own automations, which is a different spec.


## 14. Open for the owner

Nothing. The three questions this section held were ruled on 2026-09-26
(section 0.1): Telegram on by default through pluggable channels (R-A), the
desk only speaks (R-B), runs follow the Tools setting (R-C). Section 13
names what could still prove any of them wrong in the build.
