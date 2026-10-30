# Lantern: a simpler page, in two styles

Status: PROPOSAL, 2026-10-26. Criteria AC-678..AC-712. Decisions CU-1..CU-15.

**Source of truth:** `Butler web UI.html` (repo root), the Claude Design
export of 2026-10-26. It holds a clickable prototype and boards for three
directions, desktop and phone 390. Direction **A, Lantern** is the default
style; direction **B, Caption** is the second style; **C, Orb** is not built
(section 7, CU-2). Read the boards before building: every state in section 4
has a named board.

Surfaces the export does NOT draw, mocked in the same tokens:
`web/mocks/w1-calm-settings.html` (settings, shortcuts, diagnostics,
confirms) and `web/mocks/w1-calm-library.html` (recipe and automation
editors, the Tasks and automations page, run history, chat delete, artifact
cards, slash picker). Both await the owner's sign-off (Step 0).

History: `web/mocks/w1-calm.html` is the mock the owner's household picked
first; Lantern is the same choice drawn more completely. `w2-bench.html` is
superseded by Caption.

This spec replaces the page's LOOK and ARRANGEMENT. It changes no protocol,
no turn logic, no audio path, and adds only the data in section 8.

## 0. The owner's rulings, 2026-10-26

- **R1. The shell is the Calm look.** "wife liked
  `w1-calm.html#state=chat&side=1` so spec and plan for it."
- **R2. Everything else is revamped to match.** "a matching revamp with focus
  on sota ux ui for menu and settings and everything else as well." This
  OVERRIDES `web/AGENTS.md`'s one-page settings lock.
- **R3. Two looks, the chosen one the default.** "can we have both schemas
  with default to what she chose? ... design ui colors, fonts, etc so A and B
  as options."
- **R4. Follow the design past old rules.** "go with your recommendation, more
  similar to new mocks, even if it breaks old rules." Every reversal is listed
  in section 7 with its cost.
- **R5. Claude Design's file is the design.** "it is a more complete design we
  should use instead `Butler web UI.html`." Lantern carries R1; Caption is R3's
  second option.

## 1. User story

As the owner (and anyone else at the desk), I want Butler's page to look and
feel like the calm desktop chat apps I already know, with the voice shown in
the message box I am looking at and every rarely used control one menu away,
so that the page is easy to live with all day and easy to use cold.

Done when: at 1440 and 390 px, in both styles and both themes, every board in
`Butler web UI.html` (Lantern and Caption) is reproduced by the live page on a
scratch daemon, every control the page has today is reachable (section 2),
settings is the categorised sheet, and the probes prove it.

## 2. What moved where

| Today | Lantern (default) | Caption |
|---|---|---|
| `#burger` | Sidebar-panel icon at the header's far left; dot when another chat runs or waits | same |
| `#newChat` | Sidebar row "New chat" | same |
| Wordmark | Gone. The header shows the chat title and its project ("butler") | same |
| `#modelBtn` / `#modelPop` | Composer row: agent chip ("pi", popover "Agent for this chat") and model chip (popover "Model for this chat") | same |
| `#stateChip` | Voice pill with chevron, top right: Listening, Hearing you, Thinking, Speaking, Reconnecting, Off | same |
| `#ambientChip` | Warn "Recording room" tag beside the pill; voice popover row "Room memory" with "Recording the room now" | same |
| `#muteChip`, `#speakBtn` | Voice popover rows "Wake word", "Read replies aloud" (switches) | same |
| `#lookBtn` / `#lookPop` | Account menu "Appearance: Dark / Light / Auto"; Style in settings | same |
| `#filesBtn`, `#libraryBtn` | One panel toggle at the header's far right; ONE right panel with tabs Files / Library | same |
| `#tasksPageBtn` | Account menu "Tasks and automations" | same |
| `#settingsBtn` | Gear on the Account row (one press); account menu "Settings" | same |
| `#headerMore` | Deleted | same |
| `#chatSearch` | Sidebar row "Search" | same |
| Wave band (`#waveTop`, `#bandWord`) | Deleted. The composer's own border lights: blue hearing, amber thinking and speaking | Live words as a caption above the box; a waveform seam on the box's top edge |
| `#liveLine` | Inside the box, under a "Hearing you" label; tentative words dim | Caption above the box |
| `#errStrip` | One line above the composer: "Not connected. Anything you type is kept and sent when Butler is back. Details"; pill "Reconnecting" | same |
| `#micLine` / `#stateDesc` | Working row in the transcript: "pi is working in butler, 1:42, Running test/test_turn.py" with Stop and an Esc hint; stalled: "..., quiet for 2:20, it may be stuck"; finished: "pi finished in butler, 2:31. You chose: Only test_turn.py" | same |
| `#askCard` | Docked on top of the composer: "pi is asking", the question, numbered options 1/2/3, "Or type your answer for pi" | same |
| Sidebar foot, `#diagBtn`, `#diagPanel` | Account row (chevron opens the account menu); Diagnostics sheet from the menu | same |
| Per-message actions with words | Icons only, under each message | same |
| Settings one-page scroll | Categorised sheet (section 5.1) | same |

## 3. What already exists, and is reused

Measured off HEAD 2026-10-26 (impact map over `web/` and `test/`).

- **The level is already a CSS variable.** `visualiser.js:1979` writes
  `--fx-level` (0..1) and `--fx-dir` every frame onto `#composerFx`, behind
  the composer; `voice_effect` treatments `bloom` and `mist` are CSS-only on
  it, `.fxstill` is the reduced-motion version. Lantern's lit border is one
  more CSS-only treatment on the same variables.
- **The one canvas already moves between slots** (`#wave`, `VIZ_SLOT`), and
  `visualiser.js` decides band vs ring from the canvas ASPECT. Caption's seam
  is a thin wide slot on the box's top edge, so the existing band rendering
  draws it.
- **The voice popover's three rows are settings already:** `stt_enabled`,
  `speak`, `ambient_seconds`.
- **The agent and model chips are the `quick` pickers** `settings.js:buildPicks`
  builds today (Brain, Eyes, Agent, Tools); moving them is re-parenting.
- **Live words** have one writer, `renderLive` (`app.js:880`). Working words
  come from `workingWords` / `setTurnClock` over `progress` frames; the
  question from `paintAsk`; the answer the owner chose is sent by this page.
- **A look setting becomes an attribute on `<html>`** that CSS decorates
  (`data-theme`, `data-palette`, `data-voicefx`, `app.js:4870-4897`).
- **Popovers already close on Escape** through a capture-phase handler on
  their wrapper that yields while `escapeWouldStop()`.
- **Per-device view state** is the daemon's shortcuts store
  (`sidebar_collapsed`, `sidebar_width`, `expanded_projects`, `right_panel`).

## 4. The shell, board by board

Names in quotes are the board titles in `Butler web UI.html`.

- **Sidebar (Lantern `--side`).** Rows: New chat, Search, Library. "Projects"
  with `+`. Each project: chevron, name, `...` (menu: "Open claude here",
  "Open pi here", "Open opencode here", "Edit project"). Chat rows: title,
  right-aligned meta: time, "Yesterday", a spinner with "pi 1:42" when
  running, a clock for an automation run (bold title when unread), a "Needs
  you" chip. Foot: "Account" with a chevron (account menu: Settings,
  Appearance Dark / Light / Auto, Tasks and automations, Keyboard shortcuts,
  Diagnostics, Restart Butler) and a gear (Settings).
- **Header.** Sidebar-panel icon (busy dot), chat title, project name; right:
  "Recording room" tag when room memory records, the voice pill with chevron
  (voice popover), the panel toggle.
- **"Empty chat".** "Good afternoon." in the display face, the composer
  centred, three suggestions ("What changed in butler today?", "Read me the
  last three errors", "Run the tests in myproject"), "Say hey jarvis, or
  type."
- **"At rest, listening for the wake word".** Owner messages in a filled
  bubble, right; Butler's in the reading face at `--readsize`, no bubble; icon
  actions under each (copy and rewind on the owner's; copy, read aloud, try
  again on Butler's). Code in a `--raised` card with language label and copy.
- **Composer.** "Message Butler"; row: `+` (attach menu: "Upload a file",
  "Take a photo"), agent chip, model chip, spacer, dictate mic, send (a stop
  square with "Stop" while busy).
- **"Hearing you: live words in the box"** (Lantern). The box border turns
  `--blue` with a soft glow, a "Hearing you" label inside the box, the live
  words in the box (committed solid, tentative `--faint`); the pill reads
  "Hearing you".
- **"Hearing you: caption above, seam on the box"** (Caption). "Hearing you"
  over a large caption of the live words centred above the box; a waveform
  seam across the box's top edge; the box border `--blue`.
- **"Thinking".** Amber border, "Thinking" label in the box and above it,
  send becomes "Stop"; the pill reads "Thinking".
- **"Agent working, with a question card".** The working row (spinner, words,
  "Stop", "Esc") as the last transcript item; the question card docked on the
  composer with numbered options; placeholder "Or type your answer for pi".
- **"Agent quiet, it may be stuck".** The row with a pause glyph and the
  stall words.
- **"Speaking, with Stop".** Amber border, "Speaking" label, "Stop"; the
  finished row "pi finished in butler, 2:31. You chose: Only test_turn.py"
  above the reply.
- **"Link lost: one quiet line, draft kept".** The warn line above the box with
  "Details"; the draft stays in the box; pill "Reconnecting".
- **"Wake word off".** Pill "Off"; everything else unchanged; typed turns
  still answered.
- **"Voice popover, room memory on".** Three switch rows with one-line help
  ("Say 'hey jarvis' to start a turn", "Butler speaks each reply as it
  streams", "Keeps the last [N] seconds of the room"); a warn "Recording the
  room now" line; "Recording room" tag in the header.
- **"Model popover", "Agent picker", "Account menu", "Attach menu", "Project
  menu", "Files panel", "Library panel", "Light theme"** as drawn.
- **Files panel.** Tabs Files / Library, close; project name and "Read only";
  the tree; the preview of the selected file with copy; "Drop a file here to
  upload it to butler".
- **Library panel.** Sub-tabs Recipes / Automations / Tasks; "Type / in the
  message box to use one."; recipes as `/today What changed in butler today?`;
  "+ New recipe".
- **Phone 390.** Header: panel icon, title over project, pill, panel toggle.
  Sidebar as a drawer; the right panel and the voice popover as bottom sheets
  (the voice sheet carries "Recording room" as a warn bar at the top).

## 5. Settings and the surfaces the export does not draw

### 5.1 Settings: a categorised sheet (`w1-calm-settings.html`)

- Desktop: a modal sheet over the dimmed shell, centred; phone: full screen
  with a back stack (category list, then the page).
- Left: search (`/` focuses it), then the category nav from the schema,
  grouped by a new `Group.section` the daemon serves: Voice (the six staged
  groups, each with its stage number and headline), You (Identity,
  Appearance, Quiet hours), Connected (Notifications, Triggers, WhatsApp,
  Devices, Connections), Safety (Permission mode).
- Right: one category page; the blurb as the lede; rows of label and help
  with the control on the right; "Advanced, N more" fold; per-row busy then
  "Saved". The Voice section opens with the signal-path strip (Listening,
  Recognition, Turn taking, Brain, Harness, Speech, each with its headline),
  which navigates.
- The restart bar docks at the sheet's foot.
- Appearance's first row is Style: two preview cards, Lantern (Default) and
  Caption; then Theme Dark / Light / Auto, reading face, text size, voice
  effect.
- Kept: every control kind, AC-41 (schema only), dimmed-but-editable inactive
  rows, `problem` under the control, reset group, reset all with its arm, the
  permission-mode confirm, connections' three fields, secrets never filled,
  the pairing QR on a white plate.

### 5.2 Everything else (`w1-calm-library.html` and the settings mock)

Keyboard shortcuts sheet (chords from `GET /api/shortcuts`), Diagnostics
sheet (every `hud()` slot, the wake trace, the error log), Restart and
permission confirms, toasts; recipe editor, run-with-values, automation
editor, the Tasks and automations page with run history; chat delete with its
two-press arm and the stop-the-agent confirm; artifact cards, "via
WhatsApp", the guest-locked composer, "Stopped.", the `/` picker. Their
contracts (`web/AGENTS.md` "Right panel", `docs/automations.md`) are
unchanged: schedule sentences from `/api/schedule/preview`, filled recipes
from `/expand`, no markup sinks.

## 6. The style system

- **One DOM; a style is a token block plus named variants.** Token names and
  values are the export's, verbatim: motion `--ease`
  `cubic-bezier(.2,.8,.2,1)`, `--dur` 320ms, `--mono`; per style `--ui`,
  `--display`, `--read`, `--readsize`, `--rd`; per style and theme `--bg`,
  `--side`, `--raised`, `--tone`, `--pop`, `--fg`, `--muted`, `--faint`,
  `--blue`, `--amber`, `--red`.
- **Lantern:** Figtree UI; display and reading face `'Iowan Old Style',
  'Charter', Georgia, serif` at 17px; radius 22px; warm graphite (dark `--bg`
  #1c1b19) and warm paper (light `--bg` #f8f6f1).
- **Caption:** Inter throughout at 16px; radius 14px; cool graphite (dark
  `--bg` #0f1114) and near-white (light `--bg` #fbfcfd).
- **Caption's variants:** the caption above the box, the seam on the box.
  Nothing else differs from Lantern but tokens.
- **A style may change** tokens and its listed variants. **It may not
  change** element ids, the reading order, any behaviour or key, any word a
  probe reads, or the MEANING of a state colour (blue is listening and
  hearing, amber is thinking and speaking, red is error, lost link and room
  recording).
- **Dark greys are lifted over the export (CU-17).**
- **One element, moved, never copied.** The live words sit in the box
  (Lantern) or the caption (Caption); `placeByStyle()` re-parents the one
  `#liveLine`, as `#wave` is moved today.

## 7. Decisions, and the rules they reverse

- **CU-1. Two styles, one page.** Rejected: one page file per style.
- **CU-2. `ui_style` is a `look` setting**, choices `lantern`, `caption`,
  default `lantern`, written as `data-style` on `<html>`. Orb is not built: its
  orb button changes the composer's geometry, and R3 asked for two. Adding it
  later is one token block and one variant.
- **CU-3. The wave band retires; the lit border is the default.**
  `voice_effect` gains `lantern` (CSS-only, `--fx-level` drives the glow) as
  the default; Caption's seam is the existing canvas in a seam slot;
  `viz_placement` loses `band`. The `viz.*` call sites stay (AC-258). Reverses
  `voice-effects.md` section 1 ("lattice stays the DEFAULT", 2026-09-16) and
  AC-74's band. AC-76, AC-77, AC-82 survive.
- **CU-4. Each style owns its state colours; `colour_scheme` retires.** The
  export tunes `--blue`, `--amber`, `--red` per style and theme. Reverses
  AC-174's "region 1 may not be declared by any palette block" and AC-255
  (`--in`/`--out` identical in every scheme) in favour of AC-680 (a style keeps
  each colour's meaning and its contrast). The five colour schemes fold into
  the two styles; their stored values are ignored.
- **CU-5. `reading_face` becomes an override.** First choice "Style default"
  (`""`), new default; any other value replaces `--read`. Reverses "`reading_face`
  IS the CSS family written into `--sans`".
- **CU-6. Theme gains `auto`** (the account menu's Dark / Light / Auto);
  `viz.colors` and `color-scheme` follow the resolved theme.
- **CU-7. Type moves to the style.** Reading 17 / 16 px by style. Reverses
  "Body 16.5 px at 1.7". Every size still multiplies `--scale`.
- **CU-8. Message actions are icons only, with tooltips** (`data-help`, the one
  mechanism). Reverses AC-169's visible word.
- **CU-9. The always-reachable controls.** Desk: the panel icon keeps one box
  open or collapsed (AC-295 holds); New chat and Settings are in the sidebar,
  so a collapsed sidebar puts them one press away. Phone: header holds the
  panel icon, title, pill and panel toggle. Reverses AC-294 ("visible in every
  state"): New chat and Settings become two presses with the sidebar shut.
- **CU-10. The voice pill is a split control.** Body: `micAction()`'s job
  unchanged (arm, stand down, submit, stop); chevron: the voice popover.
- **CU-11. Settings is a sheet of categories.** Reverses AC-83 and AC-84; the
  signal-path strip replaces the rail. AC-85..AC-90 hold. `Group.section` from
  the daemon keeps AC-41.
- **CU-12. Work is a row in the transcript.** `#stateDesc` keeps its id and its
  one writer (`paint()`, AC-105) inside the working row; `#orbLabel` becomes
  the pill's accessible description. The finished row is new (section 8).
- **CU-13. The error strip is one line above the composer**, `role="alert"`
  kept, Details opens the Diagnostics sheet.
- **CU-14. The empty state centres the composer** via `.col.empty` from
  `setEmptyState` (its only writer); three suggestions, not four.
- **CU-15. Escape gets no new global case.** New popovers use the capture-phase
  wrapper pattern and yield while `escapeWouldStop()`. A MODAL dialog (library
  editors, the sheets) takes Escape whole and never also stops a turn
  (fixed 2026-10-27 in Step 6a review: one press must not both dismiss an
  editor and stop the agent).
- **CU-16. The library mock's reversals are taken.** The file preview sits
  under the tree instead of replacing it (`file-explorer.md` 3.4); audio is a
  styled player instead of native controls (`rich-chat.md` RC-7); the Tasks
  and automations page lives in the main column with the sidebar live instead
  of covering the page; an unbound chord reads "Set keys". NOT taken: the
  mock's CSS-only tooltips. Hover help stays the one `data-help` / `#tip`
  mechanism, restyled to the export's look.
- **CU-17. Readability lift over the export (owner, 2026-10-26, option 2).**
  The export's dark greys measured APCA Lc 52 (`--muted`, the whole sidebar)
  and Lc 28 (`--faint`, icons and dim words) where 16 px regular text wants
  about 75. Dark themes only, same hue, lighter: Lantern `--muted` #cbc7bf,
  `--faint` #9f9b93; Caption `--muted` #c1c6ce, `--faint` #939aa5 (each the
  first step of the hue toward `--fg` that holds Lc 70 / 45 on `--bg`,
  `--side` and `--raised`). Chat titles and project names draw in `--fg`;
  timestamps and labels stay `--muted`. The light themes already measure Lc
  71-84 / 50-65 and are unchanged. Text inside the message-actions row that
  is information, not a control ("via WhatsApp", a guest's name), is not
  dimmed with the row's resting opacity. Rejected: a lighter background
  (option 3), which leaves the export's chosen surfaces. MEASURED after, both
  mocks, both styles, dark: every visible text Lc >= 67.7, sidebar names 93-94.

## 8. New daemon data

| Need | Shape | Where |
|---|---|---|
| Style | `ui_style`: `lantern` / `caption`, default `lantern` | `look` group |
| Auto theme | `theme` gains `auto` | `look` group |
| Reading override | `reading_face` gains `""` first ("Style default"), the new default | `look` group |
| Lit border | `voice_effect` gains `lantern`, the new default; `viz_placement` drops `band` | `look` group |
| Retire schemes | `colour_scheme` removed (stored values ignored with a log line) | `look` group |
| Nav sections | `Group.section`: `voice` / `you` / `connected` / `safety`, plus the served section order and labels | `settings.py` GROUPS, `GET /api/settings` |
| Style cards | a `preview` flag on `ui_style` so `settings.js` draws its choices as preview cards without naming the setting | `settings.py` spec |
| Finished row | BUILT (Step 1): when an agent turn ends, one extra `progress` beat carries `agent` ("pi") and `agent_elapsed_ms` (the heartbeat's own `elapsed_ms`, frozen at the end, so it never reads below the working row); the turn's assistant message in the record keeps `agent` and `agentElapsedMs` for a reloaded tab. A run that started and then failed still gets one; a stopped run does not. The chosen answer is the page's own; the project is the chat's `projectLabel` | `progress`, `GET /api/chats/{id}` |

Checked 2026-10-27 against the code (read, not run), for the library mock:

| Mock shows | Served today | Decision |
|---|---|---|
| "/today" slash names | No: a recipe has an id and a `name` (any line up to 60 characters); the picker matches the name | The page shows `/` plus the recipe's NAME ("/Morning brief"); no slug is added. The mock's "/today" is wrong |
| Placeholders before running | Yes: `placeholders: [{name, default}]` on every recipe | Use it; the page never parses a prompt |
| "Added by pi" on a task | No: `source` is only `owner` or `butler`, plus `chat` | BUILT 2026-10-27: `agent` (the chat record's harness, e.g. "pi"; a caller-sent name is ignored; "" for owner and endpoint-brain tasks) |
| A task's "why" | Yes: `why`, at most 120 characters | Use it |
| "Used by 2 automations" | Derivable only | BUILT 2026-10-27: `usedBy` (automation ids) on every served recipe row |
| "Next: tomorrow 07:00, then every weekday" | No: `label` ("Weekdays at 07:00") and five ISO times | BUILT 2026-10-27: `nextWords` on every automation row and the preview answer (e.g. "Next: 7:00 AM tomorrow, then weekdays at 07:00"; the two clock styles come from two existing wordings); the page words no date |
| ok / failed / needs you / skipped | Raw `status` (eleven values) | BUILT 2026-10-27: `statusWord` and `group` (ok / failed / needs / skipped / pending) on every run and on a row's `last`; "stopped" groups as failed |
| Audio duration and waveform | Neither | A plain player; the duration is the `<audio>` element's own metadata; no waveform |
| Heartbeat "while you are awake" | No: it is the quiet-hours setting, which the page may not name | BUILT 2026-10-27: `awakeWords` on the heartbeat row ("while you are awake" when quiet hours are on, else "") |
| A run's "Today 02:00, 41m 06s"; a task's "today 15:06" | No: ISO times only, so 6a left the page wording them (listed debt) | BUILT 2026-10-27 (Step 6b): `whenWords`, `tookWords` ("2:31") and `lastOccurrenceWords` on every served run, `addedWords` / `doneWords` on every served task, all `schedule.when_words`, the one house wording ("3:06 PM today", "9:12 AM yesterday", "7:00 AM on Tue 27 Oct"); the reminder editor's inputs from `runAtInput` / `remindInput`. The page words no date anywhere |

Not needed after R5: the owner's name and the device name (the export greets
"Good afternoon." and labels the row "Account").

## 9. Acceptance criteria

"Probe" means `probe_page.py` / `probe_ux.py` / `e2e_scenarios.py` against a
scratch daemon; "static" means a pytest over the served files; "board" means
a screenshot of the live page beside the named board, judged by the owner.

**Styles**

- **AC-678 (one setting picks the style).** `ui_style` in `look`, choices
  `lantern`, `caption`, default `lantern`; a change writes `data-style` on
  `<html>` on every device without a reload (`test_settings_api.py`; probe).
- **AC-679 (a style moves no behaviour).** Element ids and Tab order of the
  shell are identical under both styles (probe).
- **AC-680 (a style keeps colour meaning).** Each style declares `--blue`,
  `--amber`, `--red` for both themes; each meets 3:1 against `--bg` and
  `--raised`; no other state colour is declared anywhere (static, replaces
  the AC-174 gate in `test_seams.py`).
- **AC-681 (a moved element is moved).** `#liveLine` exists once under both
  styles (static + probe).
- **AC-682 (tokens are the export's).** The token names and values in section
  6 appear verbatim in `index.html`, except the four dark-theme greys CU-17
  lifts (static, a table in the test).

**Shell**

- **AC-683 (the header holds four things).** At 1440 with the sidebar open the
  header's controls are the panel icon, the voice pill (two parts), the panel
  toggle, plus the "Recording room" tag only while recording (probe).
- **AC-684 (nothing reachable becomes unreachable).** Every control in section
  2's left column has its new home present and operable in every state (desk
  open, desk collapsed, phone drawer shut, phone drawer open); Settings in at
  most two presses (probe).
- **AC-685 (the toggle does not move).** The panel icon's box is identical
  open and collapsed at 1440, within 1 px (probe).
- **AC-686 (the voice pill keeps its job).** The pill's body does what
  `#stateChip` did; the chevron opens the voice popover whose three switches
  write `stt_enabled`, `speak`, `ambient_seconds` (`e2e_scenarios.py`).
- **AC-687 (room memory is never silent).** While the ambient ring is up, the
  warn "Recording room" tag is in the header in both styles and both widths
  (probe). Successor of AC-72's chip.
- **AC-688 (the agent and model live in the composer).** The chips sit in the
  composer row and their popovers list the schema's `quick` settings (K-series).
- **AC-689 (live words land where you look).** During capture `renderLive`
  writes inside the box (Lantern) or the caption (Caption), tentative in
  `--faint` (probe; "Hearing you" boards).
- **AC-690 (the border is the voice).** Lantern: the box border is `--blue`
  while hearing and `--amber` while thinking or speaking, its glow follows
  `--fx-level`, and at rest the border is the hairline; reduced motion keeps
  the colour and drops the motion (probe, `probe_reduced_motion.py`).
- **AC-691 (the seam is the voice).** Caption: the canvas sits in the seam slot
  while hearing or speaking and draws the band rendering (probe).
- **AC-692 (empty is centred).** On an empty chat the composer's centre is
  within 10 % of the column's; three suggestions; the wake phrase is named
  (AC-105); after the first message the composer is at the bottom (probe).
- **AC-693 (the question is at hand).** An open question is docked on the
  composer with numbered options; 1, 2, 3 on the keyboard choose while the box
  is empty; typing answers the oldest (probe; AC-249 kept).
- **AC-694 (work is in the conversation).** The working row is the last item
  in `#history`, carries `#stateDesc`'s words including the quiet and stall
  phrases, and its Stop calls `stopTurn` (static: one stop, D-18; probe).
- **AC-695 (a finished agent says so).** After an agent turn, a row reads
  "<agent> finished in <project>, m:ss" plus "You chose: <option>" when a
  question was answered in that turn (probe).
- **AC-696 (errors are one line).** A logged problem shows one line above the
  composer with Details, which opens the Diagnostics sheet; a draft in the box
  survives a lost link (`e2e_scenarios.py`).
- **AC-697 (the reading face is the style's).** `.answered` computes to
  `--read`; a set `reading_face` overrides it (probe).
- **AC-698 (auto theme follows the OS).** `theme: auto` with the emulated
  scheme dark then light paints each, and `color-scheme` agrees (probe).
- **AC-699 (one panel, two tabs).** The panel toggle opens one right panel with
  Files and Library tabs; `right_panel` stores which; on the phone it is a
  sheet (`test_file_explorer_page.py`, `test_library_page.py`, probe).

**Settings and surfaces**

- **AC-700 (settings is a sheet of categories).** Opening settings shows the
  nav from `Group.section` and groups in served order, and one page; the mic
  and socket stay live (probe). Supersedes AC-83, AC-84.
- **AC-701 (the signal path navigates).** The Voice strip shows the six staged
  groups with headlines; pressing one opens its page (probe).
- **AC-702 (search still reaches everything).** AC-85 across categories,
  grouped, folds opened, "nothing matched" said (probe).
- **AC-703 (restart is counted once, docked).** AC-86 in the docked bar (probe).
  Amended 2026-10-27 (`plan.md` AC-715, AC-716): no button; the daemon
  restarts itself once idle and the bar says so.
- **AC-704 (style is chosen by seeing it).** Appearance's first row is the two
  style cards, Lantern marked Default; choosing one applies it live (probe).
- **AC-705 (settings fits a phone).** At 390: list, page, back; no overflow;
  44 px targets (probe; successor of AC-88).
- **AC-706 (one shape for every overlay).** Menus, popovers, sheets and dialogs
  share `--pop`, `--rd`-derived radii, one shadow and `--ease`/`--dur`; outside
  press and Escape close them; Escape never fails to stop a turn (static: no
  global case added; probe).
- **AC-707 (diagnostics keeps every readout).** Every `hud()` id has an element
  in the Diagnostics sheet (`test_every_hud_readout_has_a_slot_to_land_in`).
- **AC-708 (shortcuts come from the source).** The sheet's project chords come
  from `GET /api/shortcuts`; the page holds no chord table (static).
- **AC-709 (library, tasks and files keep their contracts).**
  `test_library_page.py`, `test_automations_page.py`,
  `test_file_explorer_page.py`, `test_rich_chat_page.py` pass in substance
  (selectors may move).

**Quality floor**

- **AC-710 (accessible in both styles).** Body text >= 4.5:1, large text and
  control outlines >= 3:1, AND every visible text APCA Lc >= 60 (sidebar
  names >= 75), measured on the RENDERED colours -- composited through every
  overlay and opacity, not the computed `color` alone -- both styles, both
  themes; visible `:focus-visible`; "Skip to message box" first (probe).
  One exception (lead's ruling for the owner, 2026-10-27, settling AC-66
  against this criterion): a control dimmed ON PURPOSE because its setting
  is inactive may sit at Lc >= 45; the sentence saying why stays undimmed
  at >= 60.
- **AC-711 (the probes prove what they proved).** The four probes are updated
  in the SAME commits as the markup they read (AC-172) and exit 0 on a scratch
  daemon in both styles.
- **AC-712 (help names it).** `daemon/guide_features.py` rows for style, the
  account menu, the voice popover, the panel and the settings sheet (BH-3).

### 9.1 Scenarios

```gherkin
Scenario: The owner switches style (AC-678, AC-679)
  Given the page is open on the desk in Lantern
  When the owner chooses Caption on Settings > Appearance
  Then <html> carries data-style="caption" on the desk and on the phone
  And while Butler hears, the live words show as a caption above the box
  And the set of element ids on the page is unchanged

Scenario: Room memory is visible (AC-687)
  Given ambient_seconds is 30 and the ring is up
  When the page is 390 px wide in either style
  Then the header shows "Recording room" in the warn colour

Scenario: A question while an agent works (AC-693, AC-694, AC-695)
  Given pi is working in butler and asks "Run it, or only test_turn.py?"
  Then the working row is the last item in the conversation, with Stop
  And the question card is docked on the composer with options 1, 2, 3
  When the owner presses 1 with the box empty
  Then the question is answered with "Only test_turn.py"
  And when pi ends, a row reads "pi finished in butler, m:ss. You chose: Only test_turn.py"

Scenario: Escape still stops with a popover open (AC-706, AC-112)
  Given Butler is speaking and the voice popover is open
  When the owner presses Escape
  Then the turn stops

Scenario: Settings on a phone (AC-705)
  Given the page is 390 px wide
  When the owner opens Settings and chooses Brain
  Then the Brain page fills the screen with Back
  When the owner presses Back
  Then the category list is shown
```

## 10. Build plan

Every step is red first (a failing static test or probe assertion), then
green, then the step's checks from `web/AGENTS.md`. Board screenshots at 1440
and 390, both styles, both themes, before a step is called done; OWNER marks a
gate.

- **Step 0, OWNER.** Approve `w1-calm-settings.html` and
  `w1-calm-library.html` (the two surfaces the export does not draw). The
  export itself is approved (R5).
- **Step 1, daemon data that today's page ignores (section 8).** The desk
  and laptop Butlers run from this tree, so every Step 1 commit must leave
  today's page looking and behaving the same after a restart: ADD only.
  BUILT 2026-10-27: `Group.section` and the served `sections`, the
  finished-row fields, and the library mock's data checked (section 8).
  Checks: `test_settings_api.py`, `test_seams.py`, `test_http_surface.py`,
  the hub frame test.
- **The SWITCHES land with the page that draws them.** BUILT in Step 3a
  (2026-10-27): `ui_style` with `preview`, `reading_face`'s "" labelled
  "Style default" (a labelled choice past four values is a select), `theme:
  auto`, `colour_scheme` retired. BUILT in Step 3c (2026-10-27):
  `voice_effect: lantern` as the default and `viz_placement` without `band`
  (`mic`, `ambient`, `off`; default `mic`).
- **Step 2, tokens with no visible change.** Rename `index.html`'s surface
  tokens to the export's names with TODAY's values; before and after
  screenshots identical. Then the Lantern and Caption blocks. Checks:
  `test_seams.py` (AC-680, AC-682, sizes against `--scale`).
- **Step 3a, BUILT 2026-10-27: the switches and the Lantern skin on today's
  layout.** No markup change. `data-style` written from `ui_style` (`<html>`
  starts in `lantern`); the style blocks are the only palette (palettes and
  `data-palette` deleted); every state token derived from the style's
  `--blue`/`--amber`/`--red` (AC-680 replaces the AC-174 gate); `theme: auto`
  in CSS; Butler's replies in `--read` at `--readsize`; the canvas palette
  read from the style; dim text moved from `--faint` to `--muted` (AC-710).
  Left for 3b/3c: the shell below, the resting message actions (0.42 opacity,
  Lc 19-35 until CU-8's icons), the style cards (`preview` is served, drawn
  as buttons until Step 5).
- **Step 3b, BUILT 2026-10-27: the chrome around the conversation.** Header
  (panel icon, title over project, "Recording room" tag, split voice pill and
  voice popover, one panel toggle), sidebar rows (New chat, Search, Library),
  the tree restyled (one-line rows with time / spinner and clock / "Needs
  you"), the project menu, the Account row and menu, the Diagnostics,
  Keyboard shortcuts and Restart sheets, the composer's agent and model chips,
  one right panel with Files | Library tabs; `#headerMore`,
  `layoutHeaderControls`, the wordmark and the look popover deleted. Pinned by
  `test/test_lantern_shell_page.py`; the probes moved in the same change
  (e2e K12..K16 for AC-686). Left for 3c: the composer's look (beyond the
  control row), the voice border, the band, the transcript rows and message
  actions (still Lc 19-35), the empty state, the error line, live words,
  docked question, working and finished rows; the agent chip shows what
  answers ("Agent"/"Brain"), not the harness ("pi") -- that needs a quick
  flag on `harness` and a per-row agent on `GET /api/chats` ("pi 1:42").
- **Step 3c, BUILT 2026-10-27: the composer and the conversation.** The box
  (`--raised`, `--rd`, one hairline; `+`, chips, mic, send that becomes a
  filled square with "Stop"); the lit border (`voice_effect: lantern`, the
  default, CSS on `--fx-level`; AC-690) and the band deleted; the live words
  and the state word inside the box (`placeByStyle`, AC-689); the error line
  above the box (AC-696); the centred empty state (AC-692); the question card
  docked with numbered options and 1-9 keys (AC-693); the working row as the
  conversation's last item and the finished row (AC-694, AC-695); owner
  bubbles, no meta row, icon-only message actions (CU-8); the running row
  reads "pi 1:42" from `GET /api/chats`' new `harness`. Pinned by
  `test/test_lantern_composer_page.py`; the four probes moved in the same
  change. Left for Step 4: Caption's caption slot and seam (its
  `--live-slot`), and hiding the in-box state word under Caption.
- **Step 3, the shell (AC-683..AC-699).** Header, sidebar rows and account
  row, account menu, composer row with chips, split pill and voice popover,
  the error line, empty centring, live words, docked question, working and
  finished rows, one panel with tabs; delete `layoutHeaderControls`,
  `#headerMore`, the band; plus the Step 1 SWITCHES. Probes and static page
  tests change in the same commits. Built where a half-done shell can never
  reach a live Butler's restart (the owner chooses: a branch merged when
  green, or master with each commit whole). Checks: the full `web/AGENTS.md` pytest list, `probe_page.py`,
  `probe_ux.py`, `e2e_scenarios.py --only=I,K,L,M` on a scratch daemon, boards.
  **OWNER gate: Lantern live beside its boards.**
- **Step 4, BUILT 2026-10-27: Caption (AC-679, AC-681, AC-689, AC-691).**
  `#captionSlot` above the box ("Hearing you" over the live words, 25 px,
  centred) and `#waveSeam` on the box's top edge, both in the DOM under both
  styles and drawn only by Caption. `placeByStyle()` is the one function that
  re-parents `#liveLine` (`--live-slot`) and `#wave` (`VIZ_SLOT` under
  `lattice`, the stylesheet's `--wave-slot` under a treatment; `lantern`'s is
  the seam in both styles). `visualiser.js` draws the band for `lantern` in
  the wide seam, and keeps its loop alive on `#composerFx` so Lantern's glow
  still reads the level with its canvas hidden. Caption: no glow, no word in
  the box, the edge `--blue` / `--amber`; reduced motion freezes the seam into
  still bars. Pinned by `test/test_caption_style_page.py`; e2e section N
  (id set and Tab order equal across a live switch, the words and seam during
  a real capture in each style); the probes check the served style.
- **Step 5, BUILT 2026-10-27: the settings sheet (AC-700..AC-705).** A modal
  sheet (desk about 1080 x 760; phone full screen with a back stack, list then
  page then Back): search and the nav from the served `sections` on the left,
  ONE category page on the right; the signal-path strip on every staged page;
  search across categories with counts in the nav; the restart bar docked at
  the foot and naming what waits; `ui_style` drawn as preview cards from its
  `preview` flag; carve-outs placed by their markup's `data-section`; the old
  top bar's back, restart daemon and new conversation removed (Close and Back,
  the account menu's Restart, the sidebar's New chat), reset all at the nav's
  foot. Pinned by `test/test_settings_sheet_page.py`; `probe_page.py` and
  `probe_ux.py` settings halves rewritten for the sheet. Checks:
  `test_settings_api.py`, `test_pairing_page.py`, `probe_ux.py` settings half.
  **OWNER gate.**
- **Step 6, everything else (AC-706..AC-709).** Shortcuts, Diagnostics,
  confirms, toasts, library dialogs, Tasks and automations page, transcript
  extras. Checks: the four page tests above, probes.
  **6a BUILT 2026-10-27: the Library tab and its dialogs.** Recipes /
  Automations / Tasks as a segmented row, this project then Global, cards;
  the heartbeat pinned first with its editor inline; automation cards with
  Run now, Past runs (the full page on that automation), Edit, the switch,
  `nextWords` and the last run's `statusWord` with a shape per `group`;
  recipe rows "/" plus the name, served placeholders as chips, `usedBy`;
  tasks with a quick add, the project tag, "Added by <agent>" and `why`;
  empty states with one primary step. The recipe editor, run with values and
  automation editor are one `dialog.lbdlg` shape (AC-706) that closes on
  Escape and its backdrop through `modal()` and takes the key whole (CU-15;
  the two global dialog cases removed). Pinned by
  `test/test_library_lantern_page.py`; `test_library_page.py` and
  `test_automations_page.py` moved where selectors and words moved (AC-709).
  **6b BUILT 2026-10-27: the Tasks and automations page.** In the
  conversation column with the sidebar, header and right panel live (CU-16);
  a chat opened or started beside it takes the column. Automations: search,
  Project, On or off, Last result by the served `group`; grouped rows with
  select, switch, `nextWords`, the mark and Run; a floating bulk bar; the
  run-history drawer in the library's one overlay shape (AC-706), each run's
  `statusWord` with its shape, `whenWords`, `tookWords`. Tasks: search,
  Project, Open / Done / All, Added by Anyone / You / An agent (the served
  `agent`), one new-task box, `addedWords` / `doneWords`. J / K move, X
  selects, R runs now: one listener on the page, no new Escape case
  (CU-15). The 6a date debt is paid: the daemon serves every time (section
  8), and the static gate covers library.js and automations.js with no
  exception. Pinned by `test/test_tasks_page.py` and the served-route tests
  in `test_automations_served.py`.
  **6c BUILT 2026-10-27: Files, transcript extras, confirms, toast, tip.**
  Files: the project header with "Read only", the tree with a glyph per
  row and its keys unchanged, the preview UNDER it (CU-16; file-explorer
  3.3's swap superseded) with path, size, Copy path and Close, code /
  markdown / CSV / image / media drawn by `artifacts.js` from the panel's
  raw URL, and `#filesDrop`, hidden at rest and drawn while a file is
  dragged over the panel. Transcript: artifact cards with a head line
  (image: pixel size, Full size, Download), the styled audio player (CU-16;
  rich-chat RC-7 superseded), chart labels in `--muted`, "via WhatsApp" in
  the actions row at full strength, the guest-locked box in the composer's
  place, "Stopped." with its square, the "/" picker in the export's popover
  shape. Confirms: the stop-the-agent confirm is a `dialog.sheet.confirm`
  like Restart (AC-706, modal, Escape taken whole); chat delete arms with
  its glyph and "Delete?" in warn. One `toast()` and `#toast` for Copied,
  Saved, Restarting Butler and the one-shot notes; `#tip` restyled.
  Pinned by `test/test_lantern_extras_page.py`.
- **Step 7, quality floor (AC-710, AC-711).** Contrast per style and theme,
  focus, reduced motion and transparency, `prefers-contrast`, safe areas, the
  desktop app window (`app/`). Then the full pytest suite once (the shared CSS
  and the settings schema changed).
  **BUILT 2026-10-27.** The page-wide ring is the export's (`--fg`,
  2 px; `--sa` measured 2.48-2.99:1 on Caption light) and the composer's is
  a 1 px hairline; every Tab stop measured >= 3.71:1 in both styles and
  themes. `prefers-contrast: more` raises `--hairline` and thickens rings;
  reduced motion transitions colour and opacity only (no scroll glide);
  reduced transparency drops every blur; the phone settings sheet clears
  the home indicator (CDP-emulated insets 47 / 34, nine surfaces); the skip
  link is the export's, with no shadow; inactive rows at opacity .76 measure
  Lc >= 47.6. A question re-reads the listing so every device names the
  agent. `app/` names no page selector: nothing to change. Pinned by
  `test/test_lantern_quality_page.py`. Open items: section 11.
- **Step 8, DOX and help (AC-712).** Rewrite `web/AGENTS.md` "Visual
  language", "Settings surface" and the header and sidebar rules, deleting
  what they replace; mark superseded criteria in `plan.md` (AC-74 band, AC-83,
  AC-84, AC-88, AC-169, AC-174, AC-255, AC-294) and promote AC-678..AC-712;
  update the spec index, `docs/backlog.md`, `daemon/guide_features.py`,
  `handoff.md`. Sync the laptop and the Mac only after the final look.

## 11. What this could have wrong

- **The serif on Windows is Georgia.** The export's stack names Iowan Old
  Style and Charter, which Windows lacks: MEASURED 2026-10-26, the full stack
  and plain Georgia set the same line at the same width (390.8 px), so the
  desk draws Georgia while a Mac or iPhone draws Iowan. Vendor a face only if
  the owner wants phone and desk to match exactly.
- **Bold was capped at 600; FIXED 2026-10-26.** `fonts.css` declared Figtree
  and Inter `300 600` because `scripts/fetch-fonts.py` requested only those
  weights, though the vendored files carry 300-900 and 100-900. The export sets
  700 in twelve rules (unread titles, "Recording room", Stop, "pi is
  asking"...). The script now requests 700 for both and `fonts.css` says
  `300 700` (hand-written to the script's output; the script was not re-run).
  MEASURED after: 700 sets the test line at 353.4 / 370.6 px against 600's
  349.5 / 367.1, matching the export; before, both weights measured the same.
- **Two styles doubles every check.** Accepted by R3; AC-679 and AC-682 keep
  them one product.
- **Categories hide settings.** AC-83 existed because a setting behind a tab
  is a setting nobody finds; cross-category search (AC-702) and the strip are
  the answer, and the Step 5 gate is the owner finding three named settings
  cold.
- **The export's CSS has a specificity trap.** Its `.bt button {background:
  none; color: inherit}` outranks its own one-class button rules, so copied
  as-is the send button, `.btn`, `.ib` and `.pick` lose their fill. The
  settings mock puts that reset in a cascade layer; Step 2 must do the same.
- **The settings mock still draws a Colour scheme row.** CU-4 retires the
  setting; the row goes when Step 1 lands, and the mock is wrong there.
- **Three 3b differences from the boards (2026-10-27).** The agent chip
  reads what answers ("Agent" / "Brain"), not the harness name ("pi"): the
  daemon serves no quick setting for the harness, so naming it needs one.
  Room memory switched on from the voice popover starts at 30 seconds, a
  constant in the page, which is a policy value the daemon should serve.
  The header spans the full width so `#burger` keeps one box (AC-685), which
  puts New chat about 56 px lower than the board draws it.
- **Four 3c differences from the boards (2026-10-27).** Tentative live words
  are `--muted`, not `--faint`: rendered, `--faint` measured APCA Lc 45.5 in
  both dark styles, under AC-710's 60. The link-lost line says "Anything you
  type stays in the box until Butler is back": the page keeps the draft but
  does not queue it, so the export's "and sent when Butler is back" would be
  untrue. The empty state's suggestions are today's three real prompts, not
  the export's project-specific ones. The pending answer's "Thinking" row
  hides while the working row is up, so an agent turn shows one indicator.
- **Two 3c carry-overs (2026-10-27).** The question card said "the coding
  agent is asking" in one 3c screenshot where the board says "pi is asking".
  The card names the agent from the chat row's `harness` (`screenAgent()`,
  served since 3c) and falls back to "the coding agent" when that is empty;
  why it was empty in that run is NOT traced. Step 7 checks it on a fresh
  chat. TRACED 2026-10-27 (Step 7): the device that typed re-reads the
  listing on its own `final`; a SECOND device watching the same fresh chat
  never did, and the turn cannot end while the question blocks it, so it
  said "the coding agent" (reproduced, `.scratch-ui/step7/ask_two.py`).
  The `question` frame now re-reads the listing; both devices say "pi". The empty composer's keyboard-focus ring in light reads
  heavier than the board's hairline; Step 7 tunes focus rings.
- **Three Step 4 differences from the boards (2026-10-27).** "Speaking:
  caption follows the voice" (the reply's words as a caption with the spoken
  word marked) is NOT built: section 4 names no speaking caption and the
  page has no per-word timing; Caption shows the seam alone while speaking.
  The seam is the visualiser's band (a waveform line), not the export's
  bars; the bars are what reduced motion shows. Caption's rows are denser
  only through its tokens (Inter, 16 px, radius 14); no row spacing differs.
- **Step 5 differences from `w1-calm-settings.html` (2026-10-27).** What
  the mock draws and the daemon does not serve is not drawn: its Colour scheme
  row (retired, CU-4), the strip's live state tag ("Listening" with its shape;
  the session state stays the header pill's), a searchable model list with
  stars inside a popover (the model control is still a connection select, a
  typed box with the endpoint's list and favourite chips, AC-30), connection
  rows with key tags and an edit pencil (there is no edit route; a row shows
  its URL, key pointer and origin as text, with Test and Remove). The nav
  follows the served group order (Appearance before Identity), and an empty
  voice reads "none" (`human()`), not "Default". The restart bar has no
  button since AC-715 (the daemon restarts itself once idle); the account
  menu's Restart still asks first. A raise of the permission mode asks in an inline card, not a dialog.
  The desk nav shows no headlines (the strip carries them; the phone list
  shows them). The key and the default are no longer printed on a row.
  MEASURED: rendered APCA of every visible text in the sheet is Lc >= 64.1 in
  both styles, both themes, at 1440 and 390, EXCEPT the deliberately dimmed
  controls of inactive rows (AC-66, opacity .55): Lc 30-49. AC-66 and AC-710
  disagree there; Step 7 decided: AC-710's exception (Lc >= 45), opacity .76,
  MEASURED Lc >= 47.6 dimmed and >= 64.1 for every other text.
- **Step 7 left open (2026-10-27).** The empty chat's "starting..." text
  (Lc 45.4-57.6) was the disabled New chat row's `opacity: .6`; FIXED by the
  lead the same day: a busy row is drawn in `--muted`, measured Lc 70.8-73.8
  in both styles and themes. `#sbResize` shows its own handle line on
  focus, not a ring (by design, not measured). The safe-area instrument
  flagged rows scrolled out of a scroller as intruding; the scrollers' boxes
  sit inside the bands. Not re-shot against the boards: Caption-specific
  focus screenshots at 390.
- **Step 6a differences from `w1-calm-library.html` (2026-10-27).** Not
  drawn: the time in "Last check 14:30" (served since 6b as the last run's
  `whenWords`, not yet printed on the card; a task's time is, since 6b),
  "5 more in other projects" (the panel answer holds one project), the
  editor's list of next dates (`nextWords` only), the filled value marked
  inside "Butler will send" (`/expand` returns text only). Changed: the
  automation editor keeps a Starts select (the daemon's sources), not the
  mock's two-way switch; channels are native checkboxes; the weekly preset
  keeps its day select, not day chips; the quick add writes to the project on
  screen and Global keeps its own add; Run, Edit and Start keep today's
  routes. A placeholder typed in the editor is highlighted only after Save
  (the page never parses a prompt). MEASURED: rendered APCA of every visible
  text in the tab, the heartbeat editor and the three dialogs is Lc >= 64.5,
  both styles, both themes, 1440 and 390.
- **Step 6b differences from `w1-calm-library.html` view=tasks
  (2026-10-27).** The page sits UNDER the header, which stays (title, voice
  pill, panel toggle): AC-683's header never hides, and the voice pill is
  the stop. Times read in the daemon's one wording ("7:00 AM today, 2:31",
  "pi, 1:35 PM today"), not the mock's "Today 02:00, 41m 06s" / "pi, 2 h
  ago". The drawer is the overlay shape on `--pop` with a shadow, not the
  mock's flat `--side` column; the row's Next column is the served
  `nextWords` sentence (the mock's short "Tonight 02:00" is not served) and
  hides when the list is narrow; the schedule label is the row's small
  text, where the mock shows a recipe slash name an automation does not
  have. Keys built: J / K / X / R and the native Enter; NOT built: E, Space,
  N, S and the bulk bar's Esc (Escape keeps the page's one case: it closes
  the page). Kept from today: per-row select boxes on a phone (bulk works
  there), "All" beside Open / Done, a heartbeat or reminder's history saying
  where it is edited. Tasks are one list with a project tag, not grouped;
  the avatar is the first letter of who added it. MEASURED: rendered APCA of
  every visible text on the page (list, bulk bar, drawer scrolled, tasks)
  is Lc >= 64.5, both styles, both themes, 1440 and 390.
- **Step 6c differences from `w1-calm-library.html` (2026-10-27).** Not
  built: the slash picker's "Tab run in a new chat" (Tab still fills in,
  as Enter does; the footer says only what the keys do), the armed chat
  delete's sentence under the row ("Press again to delete it"; the
  button's accessible name says it), the drop target's large "Drop to add"
  panel with the dragged file's ghost (a dashed bar at the panel's foot
  names the project or the folder under the pointer instead), the audio
  waveform (no samples on the page; a seek slider), a highlighted maximum
  bar in charts, and the guest's name beside "via WhatsApp". The toast is
  ours (the export draws none): under the header, the overlay family's
  box. The confirm's agent line has no clock (the listing's elapsed time
  is as old as the listing). MEASURED: rendered APCA of every visible text
  in the Files tab, the drop target, the artifact cards, the slash picker,
  the guest box, "Stopped.", the armed delete and the stop confirm is Lc
  >= 64.5 (1158 texts), the toast and the tooltip >= 66.0, non-text (drop
  outline, play button, seek accent, head icons) >= 38.8; both styles,
  both themes, 1440 and 390.
- **The lost-link line does not promise a send.** The board says "sent when
  Butler is back"; nothing re-sends a kept draft, so the line says the draft
  stays in the box. Re-sending on reconnect would be new behaviour.
- **The finished row needs a number the daemon does not send yet.** Until Step
  1 lands it, the page must not compute it from its own clock (a reloaded tab
  would show a wrong time).
