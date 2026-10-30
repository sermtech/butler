# The collapsed sidebar: the strip and its project chips go (PROPOSAL, 2026-09-26)

A spec, not a build. Written against HEAD `90ebca5`. Nothing here is an
accepted criterion: AC-293..AC-302 are RESERVED for this spec and stay here
until the owner accepts them; `plan.md` is not touched.

Provenance tags as elsewhere: [READ] read from source, [MEASURED] observed in a
browser or a run, [DECIDED] a choice this spec makes, [OPINION] a judgement.

## 0. The owner's words

CLAUDE.md, backlog 2026-09-26 item 1:

> "I believe the circles/ icons on minimized are not needed anymore. i dont
> think orca uses them."

The owner delegated the design: "make sensible choices". Every decision below
is made, with its reason and the losing option in one sentence, so the owner
can overrule one line rather than re-derive the whole.

## 1. What exists today, exactly [READ, and screenshots MEASURED by earlier sessions]

The sidebar is two columns (`web/AGENTS.md`, "THE SIDEBAR IS TWO COLUMNS" and
"THE STRIP AND ITS CHIPS"; `web/index.html:2568-2800`):

- `#projRail`, a 56 px strip (`--projrail-w`, `web/index.html:322`) that NEVER
  hides. It holds:
  - `#railTiles`: one `.tile` chip per project (two initials, a ring on the
    project of the conversation on screen, the rolled-up status dot). Shown
    only while the tree is NOT on screen: desk with the list collapsed
    (`web/index.html:545`), and always on a phone (`web/index.html:862`).
    A press is `revealProject` (`web/app.js:2465`): the tree comes on screen
    with that project open -- a view change, never a navigation.
  - `.railfoot`, sticky (AC-230): `#newChat` (born in `currentProjectId()`),
    `#sbCollapse` (desk only; hidden under 900 px, `web/index.html:880`) and
    `#settingsBtn`.
- `.sb-main`, the tree column: search, the project tree, and `.sb-foot` with
  `#footDot`/`#footLink`, `#modeLabel` and `#diagBtn` (Diagnostics).
- Collapsed on a desk (`[data-sidebar="collapsed"]`, `sidebar_collapsed` in
  the daemon's `ShortcutStore`) = `#sidebar` shrinks to the strip and
  `.sb-main` is `display: none` (`web/index.html:336`).
- On a phone (max-width 900 px) the strip stays, `.sb-main` is a fixed
  overlay drawer opened by `#burger` in the header (`web/index.html:861-886`),
  and `#burger` is hidden above 901 px (`web/index.html:887`).
- Ctrl+B toggles `sidebar_collapsed` on a desk and the drawer on a phone
  (`web/app.js:5353-5360`).

Screenshots of the current states (from `TMP/tree-out/`, taken by the
2026-09-26 tree session): `desk-1440-dark-collapsed.png` shows six coloured
chips (BU, CH, ED, SE, PR, CL) above a three-icon foot (pencil, panel,
sliders); `phone-390-dark.png` shows the same six chips permanently beside a
334 px conversation, with a two-icon foot (pencil, sliders);
`phone-390-dark-drawer.png` shows the drawer opening BESIDE the strip, so the
projects are named twice on screen -- the chips and the tree lines.

### 1.1 Everything the strip does, which the replacement must keep reachable

| What | Where today | Other routes today |
|---|---|---|
| New chat in the current project | `#newChat`, strip foot | the tree's "+ New chat" per project (list open only); settings surface "new conversation"; spoken "new conversation" |
| Show / hide the list (desk) | `#sbCollapse`, strip foot | Ctrl+B |
| Open the list (phone) | `#burger`, header | Ctrl+B |
| Settings | `#settingsBtn`, strip foot | NONE -- the only on-screen door |
| Jump to one project's chats | chip press (`revealProject`) | open the list, press the project line |
| Which project is on screen | chip ring | header `#chatProject` (AC-292) |
| "An agent in another project is running / waiting / errored" while the list is hidden | chip status dot | NONE while the list is hidden |
| Diagnostics, link state, mode label | `.sb-foot` (list only) | already hidden when collapsed -- accepted in `web/AGENTS.md` ("collapsing is the owner choosing the conversation") |

Two rows have no other route: **Settings** and **the background-activity
signal**. Those are what a deletion must re-home; everything else survives on
routes that already exist.

## 2. Prior art: Orca [READ, `E:\PROJECTS\other-project` at `da6d483a`]

The owner's belief is CORRECT: Orca has no collapsed icon strip and no project
chips.

- **Collapsed means gone.** The left column wrapper becomes
  `relative w-0 overflow-visible` when `sidebarOpen` is false, with the
  comment "when the sidebar is collapsed, take this titlebar-height header out
  of flex layout so the terminal/editor reclaim the left edge"
  (`src/renderer/src/app-shell/AppWorkspaceShell.tsx:128-131`). The whole
  sidebar body -- nav, header, worktree list, toolbar, resize handle -- is
  rendered only under `{sidebarOpen && (...)}`
  (`src/renderer/src/components/sidebar/index.tsx:156-236`).
- **One small floating cluster survives**, top-left:
  `titlebar-left-floating absolute top-0 left-0 z-10 w-max`
  (`AppWorkspaceShell.tsx:132-147`), holding the app menu, the sidebar toggle
  (a `PanelLeft` icon, `src/renderer/src/app-shell/TitlebarLeftControls.tsx:
  1,96-100`) and back/forward.
- **The chord is Mod+B** (`src/shared/keybindings/definitions-core-1.ts:
  181-187`, id `sidebar.left.toggle`) -- the same Ctrl+B Butler already binds.
- **Width** 220-500, default 280, drag handle, persisted on mouseup
  (`src/renderer/src/components/sidebar/index.tsx:34-35`). Butler's 200-560
  clamp and paint-then-commit are already the same shape.
- **Open/closed is NOT persisted** in Orca: `sidebarOpen` defaults to true
  (`src/renderer/src/store/slices/ui/ui-slice-agent-actions.ts:22-47`) and is
  absent from the persisted baseline
  (`src/renderer/src/store/slices/persisted-ui-write-baseline.ts:40-63`).
  Butler does NOT copy this: `sidebar_collapsed` already lives in the daemon
  and a second device agreeing is a feature here (AC-8, D-2).
- **While collapsed, Orca's New worktree and Settings are reachable only
  through the app menu and the command palette.** Butler has neither, which
  is why this spec cannot copy Orca one-for-one: the header must carry what
  Orca hides in a menu bar.
- **No narrow-window drawer.** Orca is a desktop app; nothing converts its
  sidebar into an overlay (no `matchMedia` in `src/renderer/src/app-shell/`).
  The phone design below is Butler's own, from the drawer it already has.

## 3. The design [DECIDED]

**The strip is deleted. A collapsed desk sidebar is 0 px wide. The header
carries the three controls that must never scroll or hide: the list toggle,
New chat and Settings. The phone keeps only the drawer.**

### 3.1 Desk (above 900 px)

- `#projRail`, `#railTiles`, `.railfoot`, `--projrail-w` and `revealProject`
  are deleted. `#sidebar`'s width is `var(--sb-w)` open and `0` collapsed
  (the width rule stays a direct `width` on `[data-sidebar="collapsed"]
  #sidebar`, for the inline-`--sb-w` reason `web/index.html:324-333`
  records). A collapsed `.sb-main` is `inert` as well as hidden, so Tab never
  lands in it.
- **The header's leading cluster, always present, in this order:**
  `#burger` (the list toggle -- one control at every width, the `PanelLeft`
  glyph that `#sbCollapse` draws today), then `#newChat`. Neither moves when
  the sidebar opens or closes.
  *Losing option:* Orca's and claude.ai's pattern of moving the toggle INTO
  the sidebar's top while open -- rejected because `web/AGENTS.md` already
  paid for "a control that only appears once the list is gone is one nobody
  knows is there", and a control that changes place with the state it
  toggles is the same defect.
- **Settings moves to the header's trailing end**, after `.lookwrap` /
  `#moreWrap`, as a 44 px icon button keeping its id `#settingsBtn`, its
  handler and its `aria-expanded`. It never sheds into `#headerMorePop`: it is
  the only door to the settings surface, and a door behind a "more" menu is a
  door a stranger does not find.
  *Losing option:* Settings in the list's own foot beside Diagnostics -- it
  would vanish exactly when the list is collapsed, which is the state this
  spec exists for.
- **The background-activity signal moves onto `#burger`.** When the list is
  NOT on screen (collapsed desk, shut phone drawer) and any conversation OTHER
  than the one on screen is `waiting`, `error` or `running` (the daemon's own
  status, the one the chips roll up today), the toggle carries the same
  status dot the chips carried, most urgent first (`waiting` over `error` over
  `running`), and its `aria-label` says it in words ("Show the conversation
  list -- 1 agent needs you"). The dot is never the only channel (AC-174,
  `lattice-wave.md` 9): the words carry it for a screen reader and the tooltip
  for a mouse.
  *Losing option:* dropping the signal because the header's state chip
  already exists -- rejected because that chip reports the conversation on
  SCREEN, and a background agent asking a question (AC-49's reversal) would
  then wait unseen behind a collapsed list.
- **A one-press jump to a named project is given up.** Opening the list and
  pressing the project line is two presses where the chip was one; the tree
  keeps its expanded set (`expanded_projects`), so the second press is often
  unnecessary. Stated as the cost of the deletion, not hidden.
- Ctrl+B is unchanged (AC-230). `sidebar_collapsed` and `sidebar_width` are
  unchanged and mean what they meant; `sidebar_width` is now the whole
  sidebar again, because nothing adds to it.

### 3.2 Phone (900 px and below)

- The strip is gone, so the conversation gets its 56 px back (334 px to
  390 px at the owner's phone width).
- The drawer is the whole sidebar: `.sb-main` slides from the LEFT EDGE
  (`left: 0`, width `min(320px, calc(100vw - 48px))`), over `#sbScrim`, with
  `#sbClose` unchanged. The `backdrop-filter` / containing-block rule and the
  z-index ladder (12 over 11, under the settings surface's 20 over 19) are
  unchanged.
- The header's leading cluster is the same `#burger` + `#newChat` as the
  desk; `#settingsBtn` is the same trailing button. `layoutHeaderControls()`
  still sheds `.modelwrap`, `#speakBtn` and `.lookwrap` into
  `#headerMorePop`; it never sheds `#burger`, `#newChat`, `#stateChip` or
  `#settingsBtn`.
- The wordmark yields first when the header is tight: `#chatTitle` truncates,
  then the word "Butler" hides visually at the narrowest widths (kept in the
  accessible tree), and `#chatProject` stays (AC-292 made it the one piece of
  context a header must keep).
  *Losing option:* shedding Settings into `#headerMorePop` on a phone --
  rejected for the "only door" reason in 3.1; the wordmark is decoration by
  comparison.

### 3.3 What does not change

The tree, its expanded set, "+ New chat" per project, the busy-project
confirm, search, the resize handle and its clamp, the drawer's inert rule
(`paintDrawerInert`), the error strip's "Details" (`setSidebar(false)` /
`openDrawer()`), the Escape order (panel, drawer, STOP, clear), and every
store key. `rail_collapsed` stays an inert key; nothing new is stored.
AC-8 and AC-41 hold by construction: the page names no setting and stores
nothing in the browser.

## 4. Decisions [DECIDED; the owner may overrule any one line]

- **SC-1. Delete the chips and the strip outright, at every width.** Orca has
  neither (section 2), and on a phone they named every project twice beside
  the open drawer. Losing option: keep the strip without chips as a 56 px
  icon column (VS Code's activity bar) -- three icons do not earn a permanent
  column on a 390 px phone.
- **SC-2. Collapsed desk = 0 px, toggle in the header.** Orca's own behaviour
  (`AppWorkspaceShell.tsx:130`). Losing option: a slimmer strip -- same
  objection as SC-1.
- **SC-3. New chat and Settings live in the header in BOTH states.** SUPERSEDED
  2026-10-27 by `docs/lantern-ui.md` CU-9 (Step 3b): New chat is the sidebar's
  first row and Settings the gear on its Account row; with the list shut both
  are one press behind `#burger`. Original text kept for the record: Nothing
  moves when the list toggles. Losing option: show them in the header only
  while collapsed -- a control that appears and disappears with a state is the
  defect `web/AGENTS.md` records for the rail foot.
- **SC-4. The busy dot moves onto the list toggle.** It is the only signal the
  chips carried that nothing else carries. Losing option: a separate header
  chip -- one more pill in a header the owner has twice called too busy.
- **SC-5. No store change and no new setting.** Losing option: a
  "remember collapsed per device" flag -- D-2 is the argument against
  per-device copies of one preference, and the phone never writes the desk's
  flag already.

## 5. Criteria (AC-293..AC-302 reserved; AC-293..AC-299 used)

- **AC-293 (no strip).** At 1440, 1024 and 390 px, in both sidebar states, no
  `#projRail`, `#railTiles` or `.tile` exists in the DOM, and a collapsed
  desk sidebar's bounding width is 0. *Fails as:* chips back beside the tree.
  **Recoverable.**
- **AC-294 (nothing reachable becomes unreachable).** SUPERSEDED 2026-10-27 by
  `docs/lantern-ui.md` AC-683/AC-684 and CU-9 (Step 3b): `#burger` stays in
  the header at the export's 36 px; `#newChat` and `#settingsBtn` moved into
  the sidebar, so a collapsed desk reaches them in two presses
  (`probe_page.py --write` asserts both). Original text: In every state (desk
  open, desk collapsed, phone drawer shut, phone drawer open) `#burger`,
  `#newChat` and `#settingsBtn` are visible, at least 44 x 44 px, not
  covered (`elementFromPoint` at the centre returns the control or a
  descendant), and each press does what it did from the strip. *Fails as:* a
  state with no way to Settings. **Recoverable**, but every day it is broken
  the owner cannot change a setting.
- **AC-295 (the toggle does not move).** `#burger`'s bounding box is
  identical with the sidebar open and collapsed at 1440 px (within 1 px).
  **Recoverable.**
- **AC-296 (background activity survives the collapse).** With the list not
  on screen and another conversation reported `waiting` by
  `GET /api/chats`, `#burger` shows the status dot and its `aria-label`
  names the count and the state in words; with nothing running elsewhere it
  shows no dot. *Fails as:* an agent waiting on a question nobody sees.
  **Recoverable** per occurrence, but the cost is the agent's idle time.
- **AC-297 (the preference is still the daemon's).** Collapsing on the desk
  writes `sidebar_collapsed` through `POST /api/shortcuts` and a reload
  restores it; a phone opening and shutting its drawer writes nothing; no
  `localStorage`/`sessionStorage` call exists in `web/` (the existing AC-8
  gate). **Recoverable.**
- **AC-298 (phone gets the width back).** At 390 px with the drawer shut,
  the conversation column starts at x = 0 and is 390 px wide; the open drawer
  starts at x = 0. **Recoverable.**
- **AC-299 (a hidden list is not in the Tab order).** With the desk list
  collapsed, or the phone drawer shut, no element inside `.sb-main` receives
  focus from Tab (`.sb-main.inert` true). Collapsing while focus is inside
  the list moves focus to `#burger`. **Recoverable.**

## 6. Gherkin

```gherkin
Scenario: AC-293/AC-294 a collapsed desk shows no strip and loses nothing
  Given the desk page at 1440 px with the list open
  When the owner presses Ctrl+B
  Then the sidebar is 0 px wide and no project chip exists
  And the list toggle, New chat and Settings are visible in the header
  And pressing Settings opens the settings surface

Scenario: AC-295 the toggle stays where it was
  Given the desk page at 1440 px
  When the owner collapses and reopens the list with the header toggle
  Then the toggle's position is the same in both states

Scenario: AC-296 a waiting agent shows through a collapsed list
  Given the list is collapsed
  And a conversation in another project is waiting on a question
  Then the list toggle shows the waiting dot
  And its accessible name says one agent needs you
  When that conversation's question is answered and nothing else runs
  Then the toggle shows no dot

Scenario: AC-297 the collapse survives a reload and the phone writes nothing
  Given the desk list is collapsed
  When the page reloads
  Then the list is still collapsed
  Given a phone at 390 px
  When the owner opens and shuts the drawer
  Then no POST to /api/shortcuts was made

Scenario: AC-298 the phone conversation is full width
  Given a phone at 390 px with the drawer shut
  Then the conversation column is 390 px wide
  When the owner taps the list toggle
  Then the drawer opens from the left edge over a scrim

Scenario: AC-299 a hidden list takes no focus
  Given the desk list is open and focus is on a chat row
  When the owner presses Ctrl+B
  Then focus is on the list toggle
  And pressing Tab never reaches a control inside the list
```

## 7. Build plan

Each phase is red then green (TDD), each ends with a scoped run whose command
and result are quoted in the commit, and the browser half runs against a
SCRATCH daemon: its own `BUTLER_STATE` copied from `state/` with
`hotkeys_enabled: false`, `BUTLER_ASR=fake`, `BUTLER_TTS=fake`, a free port
(not 8140 on either machine, not 8765), killed by the PID that launched it
(root `AGENTS.md`, user preferences).

**Phase 0 -- mock, before code.** A static mock at
`web/mocks/s-collapse.html` of the header at 1440 and 390 px in both states,
screenshotted with `playwright-cli`. The owner looks before anything ships:
the composer row was rejected on sight on 2026-09-11 and no instrument caught
it. *Verify:* screenshots in `TMP/sidebar-collapse-out/`; the header at 390 px
does not wrap past two rows and every control is 44 px.

**Phase 1 -- header controls (additive).** Move `#newChat` and
`#settingsBtn` into the header, show `#burger` at every width and point it at
the existing Ctrl+B branch (one handler, `DRAWER_Q` decides). The strip still
exists. *Verify:* a new `test/test_sidebar_collapse_page.py` (structural,
like `test_turn_per_chat_page.py`) asserts the three ids sit inside
`<header>` and that `#burger`'s handler and Ctrl+B call the same function;
mutation: point `#burger` at `openDrawer` only, watch the test fail, restore.
`playwright-cli`: AC-294 and AC-295 at 1440/1024/390.

**Phase 2 -- delete the strip.** Remove `#projRail`, `#railTiles`,
`.railfoot`, `--projrail-w`, `revealProject`, the `.tile .chip` rules and the
phone rules that exist only for the strip; `#sidebar` becomes `var(--sb-w)`
/ 0; the drawer anchors at `left: 0`; `wireSidebarResize` measures
`#sidebar` again. Update the three browser instruments IN THE SAME COMMIT
(AC-172's lesson): `probe_page.py:702-735` and `:1009-1055` (the rail and
foot checks), `probe_ux.py:1012`, and `e2e_scenarios.py:1691-1797` (the
collapse and rail sections). *Verify:* the structural test asserts no
`projRail`/`railTiles` in `index.html` or `app.js`; `playwright-cli` AC-293,
AC-298, AC-299; `e2e_scenarios.py --only=<the collapse and phone sections>`
against the scratch daemon, exit code read, not output text.

**Phase 3 -- the dot on the toggle.** Roll up `GET /api/chats` statuses
excluding the conversation on screen, paint `#burger`'s dot and label only
while the list is not on screen. *Verify:* a unit-level assertion in the
structural test that the roll-up excludes `liveChatId`;
`playwright-cli` against the scratch daemon, with `GET /api/chats` answered
by a routed fixture (HTTP routing works under playwright; only the SOCKET
cannot be mocked, `test/AGENTS.md`) that reports one other conversation
`waiting`, AC-296 both ways -- a real `waiting` needs a live harness
question, which is `probe_filing_confirm.py`'s territory and too slow for
this check; mutation: drop the exclusion and watch the "nothing elsewhere,
no dot" case fail.

**Phase 4 -- DOX pass.** Rewrite `web/AGENTS.md`'s "TWO COLUMNS", "THE STRIP
AND ITS CHIPS" and "A STRIP CHIP ALWAYS GETS ITS TOOLTIP" entries (delete what
is stale rather than annotating it), `docs/filing-and-pacing.md` D-D3 gets a
one-line pointer here, `test/AGENTS.md` names the new test file. Full suite
once, because `index.html` is shared by every page test.

## 8. What this could have wrong

- **The 390 px header may not fit.** Four fixed controls (`#burger`,
  `#newChat`, `#stateChip`, `#settingsBtn`) plus `#headerMore` and the
  wordmark is roughly 5 x 44 px plus gaps [OPINION, not measured]. Phase 0
  exists to measure it before code; if it does not fit, what yields is the
  wordmark, and the owner sees that in the mock.
- **The busy dot on the toggle may be too quiet.** A 6 px dot on a 44 px
  icon is smaller than a dot on a coloured chip. If a waiting agent is missed
  in use, the next step is a worded chip in the header, which this spec
  declined (SC-4).
- **Two presses to a project may be felt.** The owner used the chips; if the
  second press is annoying, the answer is the tree opening on the project of
  the conversation on screen, which `ensureExpanded` already does -- not the
  chips back.
- **The deletion touches three browser instruments that are the only
  acceptance evidence for older criteria** (`probe_page.py`, `probe_ux.py`,
  `e2e_scenarios.py`). Updating them in the same commit is the rule, and a
  rewritten check is where a criterion quietly stops being checked.
