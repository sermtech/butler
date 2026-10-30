# The chat list learns what the rail already knows

The owner's ask, verbatim, 2026-09-11 second batch item 3: "we need better ui
and ux for the projects and chat list history should be PER project right?"
And their instruction for how: "test with playwright cli, spec and plan with
subagent and fix it properly, as per other sota projects (research as
needed)."

This started as a PROPOSAL with no product code. **ALL THREE PHASES ARE NOW
BUILT** (phases 1 and 2 on 2026-09-16, phase 3 the same day in a second
pass) -- see each phase's own section below for what shipped and what it
cost. Its criteria are AC-263 onward and they stay here until the owner
accepts them; `plan.md` is still the contract and was not touched. No
`AGENTS.md` was edited this pass -- that DOX pass is left for the owner's
own review, per this session's instructions.

Every number below is MEASURED against a SCRATCH daemon (port 8971, its own
`BUTLER_STATE` at `E:\scratch\triage-list\state`, `hotkeys_enabled: false`,
`BUTLER_CONFIG` pointed at a nonexistent file so fake adapters loaded, plain
HTTP, killed by the PID confirmed listening on 8971 via `netstat`/
`Get-Process` before `Stop-Process`) unless it says otherwise. The owner's own
daemon on port 8140 was never sent a request. Where a claim comes from reading
code rather than running it, it says READ. Where it is arithmetic on two
measured numbers, it says COMPUTED.

---

## 0. This continues two documents rather than starting over

`docs/workspaces-ui.md` did the first prior-art pass and measured the original
complaint. `docs/parallel-agents.md` built almost all of the engine that
complaint turns out to need, and named "Phase 8 -- the triage list, and stop"
as the one piece left, in a page and a half of sketch rather than a dedicated
spec. **This file is that dedicated spec**, written now because two things
changed since parallel-agents.md's sketch was drafted: phase 7 shipped
2026-09-16 (the status data phase 8 needed exists), and a same-day rail
rebuild (`d2b2f28`) independently fixed the geometry complaint phase 1 was
chasing. Neither of those was true when phase 8 was sketched, and both change
what this spec has to do.

Read `docs/parallel-agents.md` phase 8 (AC-228 completed, AC-238, AC-242,
AC-243, AC-248, AC-232's badge) before this section 4's decisions -- those six
criteria are **INHERITED, NOT RESTATED**, and section 8 below says exactly
what carries over and what this file adds.

---

## 1. What is already built -- verify before proposing, per the brief

Six claims were handed to this spec to check. All six HELD, and a seventh,
larger fact fell out of checking them: most of what would have been "new work"
for this item shipped in the last five days without this document's name on
it.

| claim | verdict | evidence |
|---|---|---|
| a chat record carries `project` | **TRUE** | `daemon/chats.py:308`, `chat.setdefault("project", "")` |
| `ChatStore.listing()` serves it, `belongs` applied before the limit | **TRUE** | `daemon/chats.py:407-442`; the filter runs inside the list comprehension at `:438-441`, the slice `rows[:limit]` is `:442` |
| `GET /api/chats?project=` filters | **TRUE** | `daemon/butlerd.py:2267-2333`, `_chat_scope` resolves the three legal values (a row id, `""`, absent) and a 400 names an id no row has |
| per-project counts computed server-side, shown on the rail | **TRUE** | `daemon/butlerd.py:744-757`, one walk of `state/chats/` per `GET /api/shortcuts`, off the event loop |
| the project rail was rebuilt 2026-09-16 and is DONE | **TRUE, and undersold** | `d2b2f28`. Not a cosmetic pass over the same structure -- see 1.1 |
| `GET /api/chats?q=` searches title and content | **TRUE** | `daemon/butlerd.py:2289-2299`, AC-185..188 |

**NEW SINCE THE BRIEF WAS WRITTEN, and it is what unblocks this item exactly
as `docs/parallel-agents.md`'s own status block said it would:** phase 7
shipped 2026-09-16 (`329c965`, `d742f15`). `Hub.status_for(chat_id)`
(`daemon/hub.py:1621-1656`) derives one of `idle`/`running`/`waiting`/`done`/
`error` from state already held -- `turn_by_chat`, the harness host's open
questions, `_Host.last_outcome` -- and `GET /api/chats/{cid}/status`
(`daemon/butlerd.py:2373-2466`) serves `{id, status, elapsed_ms}`,
READ-ONLY. **So the per-conversation state to sort by now exists.** That was
phase 8's own stated blocker in `docs/parallel-agents.md` section 3
("AC-233 PARTLY MET -- ... nothing on the page shows them per row yet"), and
it is gone.

A second, larger thing is also true and was not in the brief: **phases 3
through 6b of `docs/parallel-agents.md` are built.** `_Host` is a registry
keyed by chat id (AC-241, `daemon/AGENTS.md`), a coding agent may ask a
question and wait (AC-234, phase 3), a turn is owned per conversation on the
wire (phase 6b, `f76686e`), and the cap is enforced and refuses by name
(AC-237, AC-245). **Ten conversations really can hold a live coding agent at
once today.** The page gives zero visibility into that fact beyond the one
conversation on screen -- confirmed by reading the row renderer itself,
`web/app.js:1807-1811`: "The state dot, the elapsed time and the last action
are NOT drawn here -- there is no data behind any of them until phase 7."
That comment is now STALE. The data exists; the comment predates phase 7 by
four days and nobody has gone back to update it, which is exactly the D-12
hazard this project keeps naming.

### 1.1 What the 2026-09-16 rail rebuild actually changed

It is not the same fold, redrawn. The whole shape changed. **The accordion
that pushed the chat list off screen is gone.** `#projRail` is now a FIXED
COLUMN beside the chat list (`web/index.html:519`,
`#sidebar` is `--projrail-w` (176px) PLUS `--sb-w`, `:482`), not a fold that
sits below it and pushes it out of the viewport when opened. Every project row
carries a colour chip, the full name and a live count
(`web/app.js:5782-5810`, `paintProjRail`), and clicking a tile scopes the list
without moving anything (AC-251, reversed and re-shipped 2026-09-11, still
true today: `tile.onclick = () => setChatScope(row.id, true)`,
`web/app.js:5838`).

**This independently resolves most of `docs/workspaces-ui.md`'s original
measured complaint, on desktop, without this document's help.** See section
2.

---

## 2. The measured complaint, re-measured, and it is smaller than it was

`docs/workspaces-ui.md` measured a 901px pane in a 755px viewport, 0px of
chat list visible once a project control is usable [MEASURED-2026-09-11].
`docs/parallel-agents.md` re-measured the desktop half as MET on 2026-09-15,
before the rail's own visual rebuild. **This spec measured it again, live,
against the code at HEAD, after that rebuild**, with the owner's own project
count (seven rows, `E:\scratch\triage-list\state\shortcuts.json`) and a
twenty-four-conversation corpus spread across all seven plus one unfiled
[MEASURED, scratch daemon, patchright + real Chrome, 1440x900 and 390x844]:

| what | 1440x900 | 390x844 |
|---|---|---|
| `#projRail` (rail) width, height | 176 x 900 | 56 x 900 (icon mode, forced by the existing narrow breakpoint) |
| rail's own content (`#railTiles`, 8 rows: ALL + 7 projects) | 160 x 366 | 40 x 366 |
| `.sb-main` (chat list column) width, height | 264 x 900 | 300 x 844, but translated to `left: -256` (off-screen, drawer closed) |
| `.sb-main` `scrollHeight` vs `clientHeight` | 900 == 900 | 844 == 844 |
| `#chatList` scroll content height (24 rows) | 1364 | 1364 (irrelevant while off-screen) |
| `#chatList` height AFTER scoping to one project (3 of 24 rows) | 198 | -- |

**Read that table plainly: at 1440x900 the whole sidebar -- both the rail and
the visible slice of the chat list -- fits inside the viewport with nothing
to scroll to reach it.** `sbScrollHeight == sbClientHeight == 900` means the
outer container does not overflow at all; whatever needs scrolling scrolls
INSIDE it. Eight rail rows cost 366px of a 900px column -- COMPUTED, that is
room for roughly nineteen project rows before the rail itself needs to
scroll, not the eight `docs/parallel-agents.md` section 7 finding 5 warned a
64px rail might not hold. And the 198px figure for a scoped list matches
`docs/parallel-agents.md`'s own 2026-09-15 remeasurement exactly, which is
independent confirmation rather than a coincidence -- the same layout,
measured twice, five days apart, by two different sessions.

**So AC-231's desktop half stays MET, and this spec is not the thing that
fixes a geometry problem, because there mostly isn't one left to fix on
desktop.** What is still true, unchanged, and NOT this spec's to solve
because `docs/workspaces-ui.md` already flagged it as a question for the
owner: at 390x844 the chat list is 0px visible until the drawer opens --
`.sb-main` sits at `left: -256`. That is AC-230's design working as specified
(hide the list, keep the rail), and `docs/parallel-agents.md:663-671` already
recorded that AC-231's second clause is UNSATISFIABLE as written at phone
width. This spec inherits that finding rather than re-discovering it.

**What the pane-overflow fix did NOT touch, and what this spec is actually
for:** the chat list itself is still one flat pile, newest-first, inside day
buckets, with **no state visible per row** even though ten conversations can
now genuinely be running work at once. That is the gap phase 8 named and
this file specs.

### 2.1 The corpus, re-counted

`docs/workspaces-ui.md` measured 2 chat records and 5 project rows on
2026-09-11. Re-counted READ-ONLY against the owner's real `state/chats/` on
2026-09-16, excluding harness session sidecars (`*-pi.session.json`, which
are not conversation records): **4 real conversation records, all 4 filed
under one project, against 6 shortcut rows.** Still a small pile. This bounds
section 7's first risk exactly as it bounded `docs/workspaces-ui.md`'s.

---

## 3. Prior art

### 3.1 What already applies to the LIST, not just the rail

`docs/parallel-agents.md` section 9.5 already read the closest reference --
Claude Code's own terminal multi-agent view -- and it decides more here than
it decided for the rail. It groups by STATE with `Pinned`, `Ready for
review`, `Needs input`, `Working`, `Completed`, a one-line auto-generated
activity summary refreshed at most every 15s, and an age; `Ctrl+S` toggles
grouping between STATE and DIRECTORY; attention is drawn in four redundant
channels and never by colour alone; overflow folds ("... N more") and
failures never fold; `Space` peeks at a blocking question without switching.
`docs/workspaces-ui.md` section 8's finding about Claude Code's OWN desktop
app session picker is the second half of the same reference: choosing a
session from an unrelated project does not resume it, it copies a `cd` plus
resume command to the clipboard -- a cross-project jump is refused and handed
back rather than performed, which is AC-31's instinct arrived at
independently and a reason this spec never proposes letting a click cross a
project silently.

`docs/workspaces-ui.md` section 1.2's Claude.ai finding -- the project is a
PAGE, not a branch, so a rail never pays an expand/collapse or indentation
cost -- is exactly the shape the 2026-09-16 rail rebuild already took. That
finding is spent; it decided the rail, and the rail is built.

### 3.2 New research for this document: does grouping survive a filter?

The brief's specific question -- when a list is filtered to one project, does
grouping still earn its place, or is grouping only for the unscoped view --
was not answered by either existing spec, because neither had a working
filter to test it against. Two products that solve exactly "many items,
several states, several projects, one list" were read for this file
[MEASURED-READ, 2026-09-16]:

**Linear separates filtering from grouping as two different controls, and
they compose freely.** Filtering narrows WHICH issues appear (a project is
one of the filterable fields, filters stack, and a filtered view is
shareable by URL); grouping is a separate "Display options" control that
organises HOW the remaining issues are shown, by status, assignee, project,
priority, cycle, label, or no grouping. Linear ships SUB-grouping on top of
that -- group a filtered view by one property and sub-group by a second, e.g.
a cycle filtered down and then grouped by assignee, sub-grouped by status.
[Linear Docs, "Display options" and "Filters"; Linear changelog
2021-02-12 "Issue grouping"; Linear on X, sub-grouping announcement]

**GitHub Projects draws the identical line.** Filtering narrows a view
(`label:bug status:"In progress"`, ANDed, with an OR operator added later for
exactly the case "items in the current sprint OR items with no sprint");
grouping is a separate View menu action, and the board layout's column axis
is itself a grouping choice. Real project setups documented in GitHub's own
material stack a filter (`Critical Path=Yes AND Status!=Done`) with a group-by
(`Phase` or `Level/Status`) in the same saved view. [GitHub Docs, "Filtering
projects", "Changing the layout of a view"; GitHub Projects feature page]

**What this decides:** filtering and grouping are ORTHOGONAL AXES in both
references, and a filtered view keeps its grouping control rather than losing
it. But the two axes are not symmetric here the way they are in Linear or
GitHub, because Butler's rail is ALREADY a project filter with per-project
counts on permanent display. **Grouping the chat list BY PROJECT once the
rail already filters by project is the redundant axis** -- every row in a
scoped view shares one project value, and in the unscoped view the rail's own
tiles already show the per-project counts a grouped heading would repeat.
**Grouping BY STATE is the axis that survives scoping**, because a single
project can hold several conversations in different states at once now that
phase 5 shipped -- exactly Linear's own "group by status inside a project
filter" case. That is what section 4's D1 below turns into a decision rather
than a default.

### 3.3 What is rejected, and why, carried forward rather than re-argued

`docs/workspaces-ui.md` section 1.3 and 1.5 already rule out a two-level
expandable tree (Codex's own bug reports: a lazily-loaded group silently lies
about what is in it) and path-keyed project identity (Cursor's own orphaned-
history bug). Both conclusions still hold and neither needed re-checking:
Butler's project identity is DERIVED (`shortcuts.same_dir`, never stored
twice, `docs/parallel-agents.md` phase 1 item 2), which already avoids the
Cursor failure, and the rail is one level, never a tree, which already avoids
the Codex one.

---

## 4. The decisions that are the owner's -- RULED 2026-09-16

Four, each with options and what each costs, because a spec that refuses an
opinion makes the owner do the work twice. All four are now RULED by the
owner; the options and their costs are kept below rather than deleted,
because the cost of the CHOSEN option is still the cost and is not softened
by having been picked.

### D1. Does grouping ship at all, and if so, by what?

**RULED: C (both axes).** The owner chose OFFERING BY-PROJECT GROUPING TOO,
over this spec's own recommendation of B (state-only). **Option C's stated
cost stands, unsoftened, because it was chosen over the recommendation
rather than because it was argued down**: a by-project grouping control
duplicates the question the rail's own filter already answers, and two
controls that can disagree about the same fact is the exact drift
`docs/shortcuts.md` D4 already paid to unify in a different shape (the two
project lists that diverged before that fix). Phase 3 (grouping) was NOT
built the day this ruling was recorded -- see the build-scope note below,
kept for the record -- and shipped 2026-09-16 in the same pass as phases 1
and 2, with C's cost paid rather than deferred: see phase 3's own section.

```
  A  NO GROUPING             B  GROUP BY STATE ONLY      C  GROUP BY STATE,
     day buckets stay,          a toggle beside the          TOGGLEABLE, with
     rows gain a status         search box; ON groups        BY PROJECT also
     dot/word/elapsed/          into four buckets over        offered even
     last-action, sorted        the day buckets; OFF          though the rail
     newest-first as today      is today's flat list          already filters
```

* **A. No grouping, ever.** The corpus argument from section 2.1 supports
  this: 4 real conversations, 6 project rows. A flat list with a status dot
  per row already answers "is anything running that I am not looking at" --
  the thing that cannot be answered today at all. Cost: at ten live
  conversations the four-bucket triage the owner described in the mocks
  never exists, and finding the one that is `waiting` means reading every
  row.
* **B. Group by state, one toggle, no project grouping.** Matches section
  3.2's finding exactly -- project grouping is redundant with the rail, state
  grouping is the axis that survives a filter. Cost: one more control, and it
  needs the owner's "keep it visually easy" held against it directly (D3
  covers how that is checked).
* **C. Both axes offered.** Cost: a control offering an axis (project) that
  duplicates a control that already exists (the rail), which is the kind of
  redundant surface `docs/shortcuts.md` D4 warned against in a different
  shape -- two ways to ask the same question that can drift.

RECOMMENDED (not chosen): **B**, on the grounds section 3.2 lays out from
two shipped products rather than from taste, and because C repeats a
control the rail already is. The owner ruled **C** instead (above).

### D2. What does a row show now that status exists, and when?

**RULED: A.** A quiet row (idle/done) draws no more than it does today;
only a row that is running, waiting or in error gains a state word, an
elapsed time, and the last tool action when one exists, falling back to age
when there is none. This is the recommendation, and the owner accepted it
unchanged.

```
  quiet row (idle/done)              running row                waiting row
  "the sidebar quota fix"            "why is the scheduler.."   "sunday order.."
  butler . 2h ago                    o running . 4m 12s .       o waiting . 26m .
                                        editing web/index.html     needs an answer
```

* **A. Status only when non-idle/non-done.** A quiet row looks exactly as it
  does today (project + age); a row currently doing something gains a state
  word, elapsed, and the last tool action when one exists (AC-235's own
  fallback: age, when there is no tool event). Cost: none stated; this is
  the shape `docs/parallel-agents.md` AC-233/235/236 already describe for a
  status line, applied to every row instead of only the open one.
* **B. Status always, including "idle" and "done" spelled out.** Cost: a row
  that will never move again (`done`, most of the owner's history) carries a
  word that adds nothing over the age it already showed, which is visual
  noise on every quiet row -- the opposite of "visually easy".

RECOMMENDED: **A.** It is also the design phase 7's own status line already
uses for the ONE open conversation (`web/AGENTS.md`, the elapsed-clock
bullet): "Working on it - m:ss" only appears while there is something to
report, and a quiet turn shows nothing extra. RULED as recommended.

### D3. How is "visually easy" checked, not merely asserted?

**RULED: A.** A screenshot at each phase boundary, shown to the owner
before the next phase starts. The cost stands as stated: it puts a person
in the loop between phases, which slows the build down on purpose -- the
owner accepted that cost rather than repeating `docs/parallel-agents.md`
D8's history.

The owner has said it twice, and the composer model-picker row was rejected
on sight for failing it with no instrument objecting -- "no instrument
objected, because both touch-target checks are scoped elsewhere and nothing
in the suite has an opinion about weight or order." That is not a decision
with options; it is a PROCESS gap this spec has to close, because AC-269
below cannot be satisfied by pytest.

* **A. A screenshot at each phase boundary, shown to the owner, before the
  next phase starts.** Cost: it puts a person in the loop between phases,
  which slows the build down on purpose.
* **B. Ship it and let the owner react**, as the model-picker row did the
  first time. Cost: this is the thing that already failed once, on this
  exact feature area (`docs/parallel-agents.md` D8's own history).

RECOMMENDED: **A.** It is the only option consistent with the lesson
`docs/parallel-agents.md` D8 already paid for.

### D4. Does "per project" get anything beyond what the rail already gives it?

* **A. Filter-only, which is what exists.** The rail scopes the list, shows a
  count, and the chat you click opens without moving anything. This is
  `docs/workspaces-ui.md` D3/B, already built. Cost: nothing new; the owner's
  "chat list history should be PER project" is answered by the rail alone,
  and this spec's remaining work (status, grouping) applies equally whether
  or not a project scope is on.
* **B. A per-project LANDING, e.g. the newest conversation opens
  automatically when a tile is pressed.** Already built too -- section 1
  confirms `docs/parallel-agents.md` AC-251 item 5 shipped this on
  2026-09-11 ("a tile filters AND opens that project's newest conversation").
* **C. Something stronger**, e.g. a per-project default model/tools posture,
  or a dedicated page per project (Claude.ai's shape) rather than a rail
  tile. Cost: this is new surface, not list UX, and belongs with the
  2026-09-16 backlog item 4 (system prompt / soul per project) rather than
  with a chat list -- they would be built twice if this spec also claimed it.

RECOMMENDED: **A**, already satisfied, with B already shipped as a bonus.
State this explicitly to the owner: **there is no more "make chat history
per-project" work left undone that belongs in this document.** What remains
is status visibility and, since D1 is C, grouping by both state and project
-- both apply identically scoped or unscoped. **RULED: A**, as recommended.

---

## 5. The phased build order

Each phase ends in something the owner can look at, per D3, and each names
the screenshot or check that proves it. **Nothing here duplicates
`docs/parallel-agents.md` phase 8's own AC-228/238/242/243/248**, which cover
the rail-and-filter half (built) and the stop/Escape-follows-the-view half
(not built, and named again in section 8 rather than respecified here).

### Phase 1 -- The daemon serves status per row, not just per conversation

**BUILT 2026-09-16.**

**Ships:** nothing visible. Prerequisite for every later phase.

`daemon/butlerd.py`, `test/test_chats.py`.

`GET /api/chats` (plain, `?q=`, and `?project=`, all three) gains a `status`
AND an `elapsed_ms` field per row (phase 2 needs elapsed time on the same
listing or it is forced back to a per-row request), read from
`hub.status_for(chat_id)`/`hub._elapsed_ms(chat_id)` -- the SAME functions
`GET /api/chats/{cid}/status` already calls, and it is cheap enough to call
per row: both are derived from in-memory dicts (`turn_by_chat`, the harness
host, `_orphaned`), never a disk read, so looping them over every row
`listing()` already walks costs no new I/O. A per-row HTTP round trip
(polling `GET /api/chats/{cid}/status` once per visible row) is explicitly
REJECTED here: it multiplies request count with list length and goes stale
between polls in a way one server-side loop does not.

**BUILT DIFFERENTLY FROM THIS SECTION'S OWN SKETCH, and stated why.** The
field is NOT added to `_filed` (`daemon/butlerd.py`'s shared per-row
annotator) -- `_filed` also annotates `GET /api/session/current`,
`GET /api/chats/{id}`, `DELETE /api/chats/{id}` and the rewind answer, and
each of those has its OWN "exact shape" test enumerating every key it may
carry (10.2's discipline). Growing `_filed` itself silently widened all four
responses at once and broke three of those locked-shape tests, for a
criterion (AC-263) that names only the LISTING route. `_with_status`, a
second function applied only inside `get_chats` after `_filed`, is the fix;
`daemon/chats.py` and `daemon/hub.py` needed no change at all, since
`status_for`/`_elapsed_ms` already existed from phase 7.

**Verify:** `test_chats.py::test_each_listed_row_carries_the_status_hub_
status_for_answers` -- idle, done (a real completed turn, AC-59 holds its
child) and running (marked by hand, `test_worker_status.py`'s own idiom for
this exact derivation) all checked against `status_for`'s own answer for the
same id, on the SAME listing (`elapsed_ms` asserted present too).
`test_chats.py::test_status_rides_on_a_SCOPED_and_a_SEARCHED_listing_too` --
the field survives `?project=` and `?q=`, the OTHER branch of `get_chats`.
`test_the_chat_list_is_newest_first`'s own exact-shape assertion was
UPDATED in the same commit to include the two new keys (D-12/AC-172: the
instrument moves with the behaviour). D-13 EXECUTED 2026-09-16, exit codes
only: mutating `_with_status`'s body to a no-op reddened exactly the three
aimed tests (the two new ones plus the updated shape test) with the rest of
`test_chats.py` (79 other tests) green; the restore came back exit 0.
Full scoped batch (`test_worker_status.py test_disconnect_survival.py
test_turn_ownership.py test_chats.py test_http_surface.py
test_visualiser_contract.py test_seams.py test_settings_api.py`): 208
passed, 1 known-pre-existing failure (live Codex API rejecting
`gpt-5.4-mini`, named in `test/AGENTS.md`).

### Phase 2 -- A row draws its own state

**BUILT 2026-09-16.**

**Ships:** the thing this whole document is for. A background conversation
stops being invisible.

`web/app.js`, `web/index.html`, `test/manual/probe_ux.py`.

`paintChatList`'s row builder (`web/app.js:1729-1872`) grows a status
segment per D2/A -- a dot with a SHAPE (AC-232's rule, not colour alone) and
a word plus elapsed time, appended after `.w`. The stale "no data until
phase 7" comment (both the JS one at the old `:1807-1811` and an IDENTICAL
one this pass found in `web/index.html`'s `.chatrow .meta` CSS rule) is
corrected in this same commit (D-12).

**NO LAST TOOL ACTION -- FOUND WHILE BUILDING, NOT A SHORTCUT.** The spec
said "elapsed, and the last tool action when one exists". Checking what the
daemon actually holds server-side (`daemon/AGENTS.md`'s phase 7 entry, and
`hub.py`'s own `_on_worker_event`) found that a tool's name is never
persisted per conversation -- `worker_event` reaches only the ONE session
driving that turn, through `send_for_turn` (D-6), and nothing stores "the
last tool" in `_Host` or `Hub` for a background conversation nobody is
looking at. So a row NEVER has a tool name to show, for any conversation
that is not the one on screen -- every loud row falls back to AC-235's own
"age alone" case, always. This is stated rather than silently narrowed:
showing the last tool action for a background row would need NEW server-side
state (`_Host` remembering one string per chat) that phase 1 did not add
and this document's own phase 1 section did not ask for.

`row.dataset.chatId` was added (new, not in the original sketch) so a row
can be found by its real id rather than by position -- day-bucket headers
interleave with rows, so "the Nth chatline" is not "the Nth chat". This is
what makes the `probe_ux.py` check below possible at all.

**Verify:** a screenshot at 1440x900 and 390x844, per D3, with a scratch
daemon holding at least one `running`, one `waiting` and one `error`
conversation beside twenty `done`/`idle` ones across seven projects --
LOOKED AT before phase 3 starts (D3/A). Both screenshots are in
`test/manual/appearance/triage-p2.png` and `triage-p2-phone.png`. What was
SEEN: a dot with a distinct SHAPE per state (filled circle for running,
rotated diamond for waiting, triangle for error) plus the daemon's own word
and elapsed time ("running - 5:00", "waiting - 0:48", "error - 0:48"),
appended after the existing "now 1 turn" age text; every quiet row (21 of
24 in the seeded corpus) unchanged from before this phase -- project and
age only, no status word; nothing pushed or wrapped at either width; the
phone view (drawer open) reads identically at the narrower column. `running`
draws in a neutral `--fg-2` (informational, nothing to act on); `waiting`
and `error` both draw in `--warn` (something needs attention) and are told
apart by SHAPE alone (diamond vs triangle), which is the "never colour
alone" rule holding even where two states share a colour on purpose.
`probe_ux.py` gained a READ-ONLY check (AC-263/AC-265) comparing `/api/chats`
against the DOM by `data-chat-id`: every running/waiting/error row's
`.rowstatus` text is non-empty, every idle/done row's is absent, with a SKIP
when the daemon it is pointed at has neither kind. Verified standalone
against the same scratch corpus (loud_ok=True over 3 rows, quiet_ok=True
over 22) after `test/manual/probe_ux.py`'s own full run against this
scratch daemon did not complete in the time available -- unrelated
earlier checks in that file assume features (a full wake-word/STT stack)
this `harness_only` scratch build does not carry, so the full file was not
exercised end to end this session; the new block's own logic was.

**`running` MOVED OFF `--fg-2` ONTO `--sa`, THE SAME DAY, ON THE OWNER'S
OWN RULING AFTER SEEING THIS SCREENSHOT.** The shipped colour above was
identical to a quiet row's plain text -- the row an owner most wants to
spot was the least visually distinct of the three. `web/index.html`'s
`.chatrow .rowstatus[data-status="running"]` now reads `color: var(--sa)`,
the SAME state-accent custom property the page's own `[data-state]` ramp
already defines and AC-174 already binds identical across all five colour
schemes -- no new token, so nothing here can repaint it per scheme. Shape
(circle vs diamond vs triangle) is unchanged.

**PHASE 2 CONTINUED, BUILT 2026-09-16, CLOSING THE GAP THIS SECTION NAMED
ABOVE.** The owner asked for the last-tool-action explicitly (see the
top-level session brief); the NEW server-side state this section said would
be needed is now `_Host.last_action` (`daemon/adapters/brain_harness.py`),
a `dict[str, str]` beside `_last_outcome`, written by `note_action()` on
every `ToolStart` and read by `Hub.action_for` (`daemon/hub.py`), which
`_with_status` (`daemon/butlerd.py`) rides into `GET /api/chats` as a third
field, `action`, beside `status` and `elapsed_ms`.
**CLEARED AT EVERY TERMINAL PATH, NAMED RATHER THAN ASSUMED**: `_Host.
dispose()` clears it for every path that drops a child (a failure, a
barge-in, `dispose_all()` at a restart, `session_delete`), and
`HarnessConverse.turn()`'s own `finally` clears it UNCONDITIONALLY beside
that, because a turn that completes cleanly holds its child for the whole
conversation (AC-59) and never reaches `dispose()` at all -- the one path
`dispose()`'s own clear cannot cover. D-13, EXECUTED 2026-09-16, exit codes
only, three aimed mutations each reddening only its own test: removing the
`turn()` finally's clear (`test_tool_start_notes_the_action_and_the_turns_
end_clears_it`), removing the ToolStart-branch `note_action` call (the same
test, its first assertion), and removing `dispose()`'s own clear in
isolation from any turn generator at all
(`test_dispose_clears_the_action_with_no_turn_generator_involved`, which
spawns a real held child, notes an action by hand, and calls `dispose_all()`
with no turn in flight to reach the other clear). `web/app.js`'s row
builder shows it as a third line under `.meta`, gated on the row already
being loud (a quiet row must never carry one, even if server state lagged).
Screenshot `test/manual/appearance/triage-p2b.png`: the running and waiting
rows each show their tool ("editing web/index.html", "confirm: delete
build/ and rebuild"), the error row shows none (never noted), matching D2/A.

### Phase 3 -- Grouping, BUILT 2026-09-16 (both axes, per D1/C)

**The owner asked for phases 1-3 in one pass** (the top-level session
brief), so this phase's own "explicitly out of scope" note below is
superseded -- kept for the record of what the original build order intended
rather than deleted, per D-12's rule about correcting rather than erasing.

`web/app.js` (`groupChatsByState`, `groupChatsByProject`, and
`paintChatList`'s new branch), `web/index.html` (`#groupBtn`, `.groupbtn`
CSS), `daemon/shortcuts.py` (`chat_grouping`, `CHAT_GROUPINGS`,
`set_chat_grouping`), `daemon/butlerd.py` (`POST /api/shortcuts`'s new
named key), `test/test_shortcuts.py`.

**ONE CONTROL, NOT TWO, CYCLING THREE VALUES.** `#groupBtn` beside the
search box re-labels itself ("Group" / "Group: state" / "Group: project")
rather than three separate toggles, so there is one place a device's
grouping choice lives rather than three that could disagree about it --
`docs/shortcuts.md` D4's own argument, applied to a new control instead of
to the project list it was written about.

**STORED ON THE DAEMON, NOT A SETTING, THE SAME SHAPE AS `sidebar_
collapsed`/`rail_collapsed` BESIDE IT.** AC-8 bans browser storage outright
and two devices looking at the same list must agree how it is arranged;
`ShortcutStore._chat_grouping` is a closed three-value string
(`CHAT_GROUPINGS = ("off", "state", "project")`), refused by
`ShortcutError` for anything else and left unchanged on refusal
(`set_sidebar_width`'s own rule for a bad value). `web/` names the three
values directly rather than reading them from a schema `choices` list --
stated rather than silently claimed as AC-41 compliant, because this is
VIEW STATE and not a setting, and `sidebar_collapsed`'s own boolean is
painted by fixed page code the same way today.

**STATE GROUPING is four buckets, always drawn in this order, even at
zero** (`docs/parallel-agents.md` AC-228's own "0 of N" rule, inherited):
Needs you (`waiting` or `error`), Working (`running`), Finished (`done`),
Quiet (everything else). The bucket NAMES are this page's own copy -- the
daemon's own vocabulary (AC-269) still decides which bucket a row falls
into, and each row still shows the daemon's own exact word inside it.

**PROJECT GROUPING is offered too, AS THE OWNER RULED (D1/C), over this
spec's own recommendation of state-only** -- the cost recorded in D1 is
unsoftened by shipping it: this duplicates the question the rail's own
filter already answers, and two controls that can disagree is the drift
`docs/shortcuts.md` D4 warned about in a second shape. Grouped by
`projectId || project` (so a REMOVED project, AC-225, still gets its own
heading rather than merging into "no project"), in the order each
project's newest conversation appears in the already-newest-first list --
a stable partition, not a second sort. Only a project with at least one row
in the CURRENTLY SERVED list gets a heading: an exhaustive zero-count list
of every project row would repeat what the rail already shows permanently,
which this document's own D1 already named as the redundant half of this
choice.

**GROUPING COMPOSES WITH THE RAIL'S FILTER AND ISSUES NO REQUEST** (AC-266's
argument): both grouping functions run over the SAME `chats` array
`paintChatList` already has, whatever the rail scoped it to -- toggling
grouping repaints, it does not refetch, and the rail's own per-project count
is untouched. Verified live: scoping to one project via the rail, then
switching grouping on, groups only the rows already scoped.
**A GROUP HEADER IS TEXT, NEVER A CLICKABLE CONTROL** (AC-229/AC-240's rule
in a new shape): a browsing gesture must not become a filing one, and only
a `.chatrow` button opens a conversation -- the heading carries a label and
a count, nothing else.

D-13, EXECUTED 2026-09-16, exit codes only: removing the `value not in
CHAT_GROUPINGS` guard from `set_chat_grouping` reddened both aimed tests
(`test_chat_grouping_refuses_a_value_off_the_closed_list`,
`test_the_page_is_told_the_chat_grouping`) with the rest of
`test_shortcuts.py` (47 others) green; both restores came back exit 0.

**Verify:** screenshots at 1440x900 and 390x844 (phone drawer open), per D3,
against the SAME seven-project/24-conversation scratch corpus phase 2 used,
with `chat_grouping` set to `state` before the shot.
`test/manual/appearance/triage-p3.png` and `triage-p3-phone.png`. What was
SEEN: `Group: state` reads with a filled/active background beside the
search box; four headings in order -- "Needs you 2", "Working 1", "Finished
1", "Quiet 21" -- each with its running/waiting/error row still carrying its
own dot, word, elapsed time and (for running/waiting) its last action;
nothing wrapped or clipped at either width. `project` grouping was checked
the same way against the same corpus (not one of the two required
screenshots, but run and read before this report was written): "No project
5" first, then one heading per project in newest-first order, each with its
own count, the running row's own status line intact inside its group.

### The original phase 3 sketch, kept for the record rather than deleted (D-12)

**Explicitly out of scope for the 2026-09-16 build.** D1 ruled C (both
axes), but this phase was not started -- only phases 1 and 2 were
authorized. Left here unchanged as the plan for whoever builds it next.

**Ships:** the four-bucket view, toggleable, composing with the rail's
existing filter per section 3.2.

`web/app.js`, `web/index.html`, `test/manual/e2e_scenarios.py`.

A toggle beside the search box, OFF by default (the corpus in 2.1 does not
need it turned on for the owner today). ON groups the CURRENTLY SERVED rows
-- scoped or not, exactly as they arrive from phase 1's `status` field --
into Needs you / Working / Finished / Quiet, each heading showing its count
even at zero (`docs/parallel-agents.md` AC-228's own rule, inherited: "every
group heading still draws... at 0 of N"). The day buckets are what state
grouping REPLACES when it is on; they return when it is off. Nothing here
proposes grouping by project, per D1's reasoning.

**Verify:** `e2e_scenarios.py`, a new case -- toggle grouping on with a
project scope active, confirm the four headings draw over the SCOPED rows
only, confirm the rail's own count is unchanged (grouping paints, it does not
re-filter). A screenshot per D3, shown before phase 4.

### Phase 4 -- Probes, and the DOX pass

**Ships:** nothing visible; closes the loop AC-172 always requires.

`test/manual/probe_page.py`, `test/manual/probe_ux.py`,
`test/manual/e2e_scenarios.py`, `web/AGENTS.md`, `daemon/AGENTS.md`,
`AGENTS.md` (the spec index).

`probe_page.py`'s no-match search assertion (`:564-566` per
`docs/parallel-agents.md` section 6) needs the SAME decision that document
already named: if grouping headings always draw at "0 of N", the existing
"headings go to zero on no match" assertion is rewritten here, in the commit
that makes it true, not left for a later session to discover broken.

**Verify:** every existing probe still exits 0 with every number it printed
before unchanged or better (AC-226's rule, inherited).

---

## 6. Proposed criteria

**AC-263 is the next free number.** The highest AC anywhere in this
repository's docs is AC-262 (`docs/voice-effects.md`, itself a PROPOSAL); the
highest actually promoted into `plan.md` is AC-247. AC-263..AC-270 follow, in
the `docs/voice-control.md` shape: what the criterion fails as, and whether
that failure is recoverable.

- **AC-263 (status is served per row, computed once, not polled per row).**
  `GET /api/chats` (plain, scoped, or searched) carries `status` for every
  row it serves, derived server-side from `Hub.status_for`, at no added disk
  I/O over what `listing()` already does.
  *Plain:* the daemon says what is running; the page never has to ask row by
  row.
  *Fails as:* a page that polls N small requests to paint one list, going
  stale between them -- the AC-7b failure in a new shape. **Recoverable.**
- **AC-264 (a row's state is a shape and a word, never colour alone).** Every
  drawn status carries the same non-colour channel AC-232 already requires
  of the rail badge, applied to list rows: a dot SHAPE plus a word, not a
  tinted background alone.
  *Plain:* a colour-blind reader loses nothing a sighted one has.
  *Fails as:* AC-174's rule broken on a second surface, the same way AC-232
  named it broken on the rail's first draft. **Recoverable**, and the
  existing `test_seams.py` gate extends to it.
- **AC-265 (a quiet row stays quiet).** `idle` and `done` conversations draw
  no status word beyond their existing age; only `running`, `waiting`, and
  `error` gain the extra line.
  *Plain:* the list does not get louder just because it can.
  *Fails as:* every one of the owner's four real conversations carrying a
  permanently visible "done" label that says nothing the age did not already
  say -- the opposite of "keep it visually easy". **Recoverable.**
- **AC-266 (grouping, if built, composes with the rail's filter and never
  replaces it).** Toggling grouping on repaints the SAME served rows a
  project scope already narrowed to; it issues no separate request and
  changes no count the rail shows.
  *Plain:* filtering says which rows; grouping only says how they are
  arranged.
  *Fails as:* a grouping control that quietly re-fetches unscoped data,
  which would put a conversation back in view that a project filter had
  deliberately hidden -- AC-229's failure in a new control. **Recoverable,
  loudly**, the same way AC-229 is.
- **AC-267 (grouping never offers a project axis).** The grouping control, if
  built, offers state only. No "group by project" option exists anywhere the
  rail already provides that function.
  *Plain:* one way to ask "which project", not two that can disagree.
  *Fails as:* a second project-grouping UI drifting from the rail's own
  counts the way the two former project lists drifted before
  `docs/shortcuts.md`'s D4 unified them. **Recoverable.**
- **AC-268 (a screenshot gates each phase before the next starts).** Phases
  2 and 3 of section 5 each produce a screenshot at 1440x900 and 390x844,
  reviewed before the next phase's work begins.
  *Plain:* someone looks at it before more is built on top.
  *Fails as:* the composer model-picker row's own history repeating itself --
  built, shipped, rejected on sight, redrawn the same day. **Recoverable**,
  but it costs a whole redraw, which is the cost this criterion exists to
  avoid paying twice.
- **AC-269 (nothing in `web/` names a state word or grouping label the
  daemon did not send).** AC-41 applied to this surface: "waiting", "needs
  you", and every state word painted come from the daemon's own vocabulary,
  never a string literal added in `web/app.js` for this feature alone.
  *Plain:* the page draws what it is told.
  *Fails as:* a label that drifts from the daemon's own word for the same
  state, which `test_seams.py`'s grep gate already watches for elsewhere on
  this page. **Recoverable, loudly.**
- **AC-270 (the probes and the DOX move in the same commit as the markup).**
  `probe_page.py`, `probe_ux.py`, and `e2e_scenarios.py` are updated in the
  SAME commit that ships each phase's markup, and `web/AGENTS.md` /
  `daemon/AGENTS.md` / the root spec index gain this file's line in the same
  commit it is accepted.
  *Plain:* the instruments and the docs never lag the code that made them
  stale.
  *Fails as:* the stale "no data until phase 7" comment this spec's own
  section 1 found and reported -- a true statement on the day it was written
  that nobody returned to fix. **Recoverable**, at the cost of exactly this
  kind of stale-comment audit recurring.

---

## 7. Gherkin, for the new criteria only

```gherkin
Scenario: AC-263 status rides on the listing, not a second request
  Given three conversations, one running, one waiting, one done
  When the page requests GET /api/chats
  Then each row carries its own status
  And no additional per-conversation request was made to get it

Scenario: AC-264 state is never colour alone
  Given a running conversation and a waiting conversation
  When their rows are painted
  Then each carries a distinct dot SHAPE as well as a distinct colour
  And each carries the state as a word

Scenario: AC-265 a quiet row adds nothing
  Given a conversation whose status is done
  When its row is painted
  Then it shows the same project-and-age line it showed before this spec
  And no status word is added

Scenario: AC-266 grouping composes with a project scope
  Given the list is scoped to one project with mixed states
  When grouping is switched on
  Then the four headings are built only from the rows already scoped
  And the rail's own count for that project is unchanged

Scenario: AC-267 no project axis in the grouping control
  When the grouping control's options are read
  Then "project" is not one of them

Scenario: AC-269 nothing in web/ invents a state word
  When web/ is grepped for a state label used in this feature
  Then every one traces to a string the daemon sent, not a literal in app.js
```

---

## 8. What this inherits from `docs/parallel-agents.md` phase 8

Not restated, not renumbered, and NOT this document's to redesign:

* **AC-228 (rail filters one list)** -- COMPLETE already, per section 1.1
  above; phase 8's own remaining piece of it ("0 of N" headings) becomes
  this spec's AC-266/AC-228 boundary: the day/state headings this spec adds
  follow the SAME rule.
* **AC-232's badge** -- the rail's own attention badge, not a chat-row
  concern; this spec's AC-264 is the same rule (shape, not colour alone)
  applied to a different element, and the two should be built against one
  shared token set rather than two.
* **AC-238 (a device speaks the conversation it is showing)** -- unrelated to
  list rendering; untouched here.
* **AC-242 (the wake word is not a kill switch)** and **AC-243 (stop acts on
  the conversation on screen)** -- these are what make a `waiting` row
  ACTIONABLE once you can see it, and they are real prerequisites for this
  spec to be worth using at scale, but they are page-state-machine work
  (`escapeStops()` keying off the viewed conversation rather than the
  device's own turn) with no chat-list markup in it. Left with
  `docs/parallel-agents.md`, where they are already fully specced.
* **AC-248 (a rewind refuses while that conversation's agent is running)** --
  untouched; this spec adds no new rewind surface.

---

## 9. What this spec could have wrong

Six, worst first.

**1. The corpus still does not justify grouping, and D1 says so on its own
evidence.** 4 real conversations, 6 project rows [MEASURED 2026-09-16,
section 2.1]. Phase 3 (grouping) may be work built for a future that has not
arrived, exactly as `docs/parallel-agents.md` section 7's own first risk
said about the triage list generally. Phase 2 alone -- a status dot per row
-- is useful at ANY corpus size, because it turns "is something running that
I am not looking at" from unanswerable into answerable with one glance, which
is why the build order puts it first and makes phase 3 conditional on D1.

**2. Section 3.2's Linear/GitHub research describes tools built for teams
triaging hundreds of items, and Butler is one person with four
conversations.** The orthogonal-axes finding may be right in the abstract and
still be the wrong amount of ceremony for this corpus -- a filter and a toggle
where a person with four rows would just read them. The recommendation (D1/B,
default OFF) is the hedge against that, not a resolution of it.

**3. AC-263's "no added disk I/O" claim is read, not measured under load.**
`status_for` is documented as cheap because it reads in-memory dicts rather
than the filesystem [READ, `daemon/hub.py:1621-1656`'s own docstring], but
this spec did not execute it against a listing of two hundred rows and time
it. If the owner's conversation count grows the way `docs/parallel-agents.md`
section 6's "known and bounded" note already flags for the registry itself,
this should be re-measured before phase 1 ships rather than trusted from the
comment.

**4. Phone geometry is inherited unsolved, and this spec did not attempt it.**
`docs/parallel-agents.md:663-671` already flagged AC-231's phone half as
UNSATISFIABLE as originally written and left it for the owner to restate.
This spec repeats that rather than proposing a fix, on the grounds that
status/grouping work applies equally to whatever the phone eventually shows
-- but if the owner's real complaint was partly about the phone, this
document does not answer it.

**5. D2's "quiet row stays quiet" may read as UNDER-informative once grouping
exists.** A `done` row inside a "Finished" heading carries no more
information than the heading already gave it, which might be exactly right
(the heading did the telling) or might leave a `done` row indistinguishable
from an `idle` one that never ran at all -- a real distinction Butler's own
`status_for` makes and D2/A throws away by design.

**6. AC-268's screenshot gate has no mechanism to enforce it beyond
instruction.** Nothing in pytest can require a human looked at an image
before the next phase starts; this is a process rule, not a test, and
`docs/parallel-agents.md` D8's own history is the only evidence it will be
followed rather than skipped under time pressure the way it appears to have
been skipped once already.
