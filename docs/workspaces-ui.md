# Workspaces you can see, and a chat list that belongs to one

The owner's ask of 2026-09-11, verbatim: "we need better ui and ux for the
projects and chat list history should be PER project right?" And how they
asked for it to be done: "test with playwright cli, spec and plan with
subagent and fix it properly, as per other sota projects (research as
needed)."

This is a PROPOSAL. Its criteria are AC-216..AC-227 and they stay here until
the owner accepts them; `plan.md` is still the contract and was not touched.
Nothing in this document has been built.

Every number below is MEASURED in a real browser against a scratch daemon
(patchright, Chrome, 1440x900 and 390x844, own `BUTLER_STATE`,
`hotkeys_enabled: false`, killed by the PID listening on its own port) unless
it says otherwise. Where a number is COMPUTED from two measured numbers it
says so. Where a claim comes from reading code rather than running it, it says
READ.

---

## 0. What is already true, and what the problem actually is

**Half of this ask shipped on 2026-09-11, hours before it was made.** Nothing
below should be rebuilt:

* The shortcuts pane's ROWS are the one project list (`ShortcutStore.paths()`),
  read by both of AC-31's gates. The `projects` setting is retired.
* A chat record CARRIES a `project`, written only by `_Host.work_in`
  (`brain_harness.py:433`), so the field cannot claim a directory the child is
  not in. It survives a restart, a New chat (`rotate` carries it forward) and a
  rewind (`snapshot["project"]`).
* `POST /api/project {id}` moves the live conversation's coding agent, resolved
  by enumerating rows, taking no path from the caller.
* Every pane row has claude / pi / opencode launchers, an optional chord each,
  edit, remove, and "work here".
* `GET /api/chats` and `GET /api/chats/{id}` both serve `project`, and the
  sidebar row prints its last path component in `.proj`.

So the plumbing for "a chat belongs to a project" is DONE. What does not exist
is any way to USE it: no grouping, no filtering, no per-project view, no count.
The list is one flat pile, newest first.

### 0.1 The measurement that reframes the job

The owner's complaint names two things and they are one thing. Driven in a
browser on 2026-09-11 with the owner's OWN five project rows (read from
`state\shortcuts.json`) and 24 seeded conversations, at 1440x900:

| what | measured |
|---|---|
| sidebar scroll viewport | 755 px tall |
| whole scroll content, pane open | 2704 px |
| where the `projects` fold button starts in that content | **1691 px down** |
| the pane itself, 5 rows, chords hidden | **901 px** |
| one project row | **133 px** (177 px with chords shown) |
| pane chrome above and below the rows | **212 px**, whatever the row count |
| chat list, 24 conversations | 1595 px, 68 px a row |
| chat list visible once the project rows are | **0 px** |

Three things fall out of that table and each decides part of the design.

**The pane does not fit the container it lives in.** At the owner's real five
rows it is 901 px in a 755 px viewport -- 1.19x -- before a sixth project
exists. Eight rows measured 1318 px. The brief's "seven projects make a
1,263 px pane" is the same finding counted slightly differently; my own
per-row number, 133 px, sits inside the brief's 132-147 px range.

**The chat list and the project list can never be seen together.** This is the
owner's "chat list pushed entirely out of view", and the mechanism is the
opposite of the obvious reading: the pane is BELOW the chats in the DOM
(`web\index.html:1801` chats, `:1823` the projects fold), so what the owner
scrolls past to reach their projects is 1691 px of conversation history.
Scrolled far enough to use a project row, `chatVisiblePx` is exactly 0.

**A third of the compacted pane is a form.** 212 px of chrome -- the dictation
chord line, the "show chords" checkbox, two text inputs, Browse, Add, and a
hidden note -- is present whether there is one project or twenty. Injecting a
one-line-per-row stylesheet in the page (a PRICE, not a proposal) took the row
from 133 px to 34 px and the pane from 901 px to 406 px. 212 px of that 406 is
still the form.

### 0.2 Two facts about the corpus that bound how much this is worth

**The owner has 2 chat records and 5 project rows**, read from
`state\chats\` and `state\shortcuts.json` on 2026-09-11. One of the two chats
carries a project; the other carries `""`. A design that solves navigating
hundreds of conversations is solving a problem this owner does not have TODAY
-- which is an argument for shipping the density fix first and the scoping
second, not an argument against scoping.

**In endpoint mode there are no conversations at all.** MEASURED against a
scratch daemon with `brain_mode: endpoint`: `GET /api/chats` and
`GET /api/session/current` both answer 404 `"this daemon is not running a
harness, so it holds no conversation"`, the page draws 0 chat rows and
`#chatsNote` reads `none here`, while the project rows are still served (the
shortcuts store does not go through the brain) and `POST /api/project` answers
400 `"this brain has no coding agent, so it has no working directory to
change"`. So a workspace surface whose organising principle is "the chats in
this project" is EMPTY in one of the three brain modes, and the project rows
are still there being terminal launchers. Whatever is built has to be legible
in that mode rather than merely not crash.

### 0.3 A live defect this investigation walked into

**The sidebar's collapse does not collapse it.** MEASURED: clicking
`#sbCollapse` set `document.documentElement.dataset.sidebar` to `collapsed`
and the sidebar stayed **264 px wide**, with project rows at 243 px and their
names fully readable. The cause is a cascade collision in the sidebar-resize
work that landed as commit `1f6bdab` the same day: `paintSidebarWidth` writes
`--sb-w` as an INLINE custom property on `<html>` (`web\app.js:3664`, called
from `paintShortcuts` at `:4304`), and an inline declaration beats
`[data-sidebar="collapsed"] { --sb-w: 64px; }` at `web\index.html:257`.

MEASURED TWICE: once against the working tree while that commit was still
uncommitted, and again against HEAD after it landed. Identical both times, so
this is a defect in shipped code and not a half-finished edit. Everything else
`[data-sidebar="collapsed"]` governs still works -- the labels hide, the
resize handle goes, the New chat button narrows to 44 px -- which is why the
toggle LOOKS like it did something.

Reported rather than fixed, because this spec writes no code -- but it bounds
one of section 2's options: **any placement that leans on the 64 px rail is
leaning on something that does not currently work.**

### 0.4 The tree moves while you are reading it, and two things follow

While this spec was being written, `daemon\butlerd.py`, `daemon\shortcuts.py`,
`web\app.js`, `web\index.html`, `test\manual\e2e_scenarios.py` and
`test\test_shortcuts.py` went from dirty to committed (`1f6bdab`, a draggable
sidebar width -- the owner's second-batch item 2) and a second commit
(`e98cbe5`) landed on top. Those are six of the eight files section 5 plans to
touch, and they were being edited by another session at the same time.

Two things follow and neither is optional. **Re-read those six files before
starting phase 4 or 5**, because every line number in this document was taken
from a tree that has since moved. And **every measurement above was re-taken
against HEAD** rather than left standing from the dirty tree; where a number
appears once it was stable across both.

The root `AGENTS.md` spec index also stops at `language-auto.md`:
`mic-optional.md`, `rewind.md`, `chat-search.md` and `attachments.md` are all
built and none of them is indexed. This file will be the fifth. That is a DOX
gap reported rather than fixed here, because this session was scoped to one
new file.

---

## 1. Prior art, read rather than remembered

Five products, fetched rather than recalled. What is taken and what is
rejected is stated with the reason, because a list of screenshots is not
research. Every OpenAI first-party page (help.openai.com, openai.com) answered
403, so the ChatGPT and Codex claims below rest on `learn.chatgpt.com`,
OpenAI's public issue tracker and forum, and one detailed third-party
walkthrough. None of the five was seen running.

### 1.1 ChatGPT Projects -- and the complaint thread that is the finding

The sidebar carries a flat `Recents` list PLUS a `Projects` section, and
OpenAI ships the filter-versus-group question as a user SETTING: "You can
organize Recents as one combined list, or group chats by project so related
work stays together". A chat moved into a project still appears in Recents,
and support confirms that is intentional -- Recents is activity-based, not
location-based.

Users hate it, in exactly the words that matter here: "This makes Projects
feel more like labels than real workspaces", and "When a user moves a chat
into a Project folder, the ordinary meaning of 'move' is that the chat has
been relocated."

TAKEN: the lesson, not the behaviour. Whatever Butler decides about whether a
scoped chat leaves the flat list, the LABEL has to mean it. REJECTED: a chat
appearing in both places. It is the single most complained-about behaviour
found in this search, and it makes "move" a lie.

There is also no "active project" in ChatGPT. `learn.chatgpt.com/docs/projects`
says "Start a chat without a project when the work is self-contained" -- a
project-less chat is a first-class thing, not a leftover.

### 1.2 Claude.ai Projects -- the opposite choice, made hard

The boundary is hard and stated: search covers "All chats outside of projects"
and "Individual project conversations (searches are limited to within each
specific project)", and each project has "its own separate memory space".
Projects live on a DEDICATED PAGE at `claude.ai/projects`, with tabs, a search
box and sort-by-activity -- not as an expanded tree in the rail. Starred
projects get promoted into the rail.

TAKEN: **the project is a page, not a branch.** A rail that stays one level
deep never pays the expand/collapse state, the indentation, or the accordion
cost. This is the single most useful idea found, because it is the one that
costs the 264 px sidebar nothing. Also TAKEN: promote a few, list the rest --
for a single user with five directories, that is the whole navigation problem.

REJECTED: hard search scoping. Butler's chat search is four days old
(`docs\chat-search.md`) and its value is finding a conversation you cannot
PLACE. Scoping it to the open project hides exactly those.

### 1.3 The Codex desktop app -- and the bug that argues against a tree

"Every top-level item is a project. Inside each project, you create threads."
A true two-level tree, with no project-less threads. The cautionary evidence
is `openai/codex#14751`: expanding a workspace shows only a recent subset,
because the sidebar is driven by a preloaded recent list --
`thread/list(limit=50, sortKey=updated_at)` with a cursor it never pages. Old
conversations vanish from the UI while remaining visible in the CLI, and
pinning is the only way to keep one.

REJECTED: the two-level expandable tree. It forces per-group pagination, and
a lazy list silently LIES about what is in a group -- which is worse than a
flat list, because a flat list never claimed to be complete. Butler's
`CHATS_LISTED` bound would do the same thing the moment a group is a page of
its own.

### 1.4 Claude Cowork -- the split this design borrows

Projects are "persistent, self-contained workspaces with their own files,
links, instructions, and memory", but the LOCAL FOLDER is chosen per session:
"Folder instructions add project-specific context to Cowork when you select a
local folder on desktop."

TAKEN, and it is the load-bearing borrowing: **the workspace owns the history;
the directory is a capability granted at task time.** Those are two axes and
Anthropic split them. Butler has the same two axes and currently fuses them --
`work_in` is the only writer of `project`, so the ONLY way a conversation gets
filed is by pointing a tool-enabled coding agent at a directory.

### 1.5 Cursor and VS Code -- what path-as-identity costs

The folder IS the identity, and history lives in
`workspaceStorage\<hash>\state.vscdb` keyed by path. "Move a project folder,
rename a directory, or open Cursor from a different path -- and your chat
history vanishes." An open feature request to sync chats across workspaces has
no staff reply; a commenter explains the hostname is baked into workspace
identity at both client and server. From search summaries rather than a
fetched page: Cursor 3.0 moved conversation content into a central
`globalStorage` index keyed by URI to fix orphaned chats.

REJECTED: path-keyed storage, and it is the finding that turns into decision
D2 below. **Butler is doing this today.** `work_in` stores `row.path`
verbatim and `hereButton` compares `row.path === chatProject` as a STRING
(`web\app.js:4198`). The repository already knows this is unsafe --
`shortcuts.same_dir` exists at `daemon\shortcuts.py:87` with the comment
"this is Windows: `E:\X`, `E:\X\` and `e:\x` are one folder and three
strings" -- and NOTHING that joins a chat to a row calls it (grep: two call
sites, both inside `migrate_projects`). The owner's own OWNER row carries a
trailing backslash today [READ].

### 1.6 Pattern writing

NN/g on accordions on desktop names the interaction cost as "scrolling the
page, scanning the headings, deciding which one to expand, targeting the
click, and waiting for the content to appear", and lists deep hierarchies as a
reason to avoid them. The article carries no study numbers, so it is an
argument and not a measurement. Notion puts the workspace switcher at the very
top of the rail, above everything, and switching repopulates the whole
sidebar.

REJECTED: a global header workspace switcher (Slack / Linear / Notion). It
implies a persistent "current project" MODE, and Butler already has one
switching path that is deliberately expensive -- AC-31's exact, twice-gated,
refused-aloud switch. A second, silent one in the header is the failure D-18
is named after.

---

## 2. The decisions that are the owner's

Five, each with the options and what each costs. The recommendation is stated
because a spec that refuses to have an opinion makes the owner do the work
twice; it is still a recommendation.

### D1. Does a chat belong to exactly one project, and what about the ones with none?

Today: exactly one, as a path string, or `""`. Half the owner's two records
carry `""`, and `chats.py:270-272` refuses to invent one in a comment written
on purpose: "They are deliberately NOT attributed to the root project -- they
were not all about Butler, and inventing that would be the record stating a
fact nobody established."

* **A. Exactly one, and "no project" is a REAL, permanently-present group.**
  Cost: a group that never empties and, on today's corpus, holds half of
  everything. Benefit: nothing is ever unreachable, and the existing refusal
  to invent an attribution survives untouched.
* **B. Exactly one, and an unfiled chat is attributed to the root (butler)
  project on first read.** Cost: the record states a fact nobody established,
  which is the thing `chats.py` already refuses in writing. Also irreversible
  once written.
* **C. Exactly one, and unfiled chats are reachable only from an "All chats"
  view.** Cost: from inside a project there is no route to them, which is the
  Codex lazy-list problem arriving by a different door.

RECOMMENDED: **A**. It is the only one that does not require overturning a
decision the code already carries an argument for.

### D2. What identifies the project on a chat record -- the PATH, or the ROW ID?

Today: the path, verbatim, compared as a string. Section 1.5 is the whole
argument.

* **A. Keep the path.** Cost: editing a row's path -- which the pane invites,
  with an `edit` button on every row -- silently orphans every conversation
  filed under the old spelling. Nothing warns, nothing migrates, and the group
  simply empties. A trailing backslash alone is enough (`E:\X` versus `E:\X\`),
  and the owner has one. Benefit: zero work.
* **B. Store the row id (`s-<ms>-<hex>`), migrating existing records by
  `same_dir`.** Cost: a migration, plus a rule for a chat whose row was
  REMOVED (see D5's orphan case), plus the record no longer says in plain text
  where the agent was, which is a real loss when reading a chat file by hand.
* **C. Store BOTH -- id for identity, path as a human-readable snapshot.**
  Cost: two fields that can disagree, which is D-18's shape; benefit: a
  hand-read record still says where the work happened, and a renamed row
  re-points every conversation without losing the history of where it used
  to be.

RECOMMENDED: **C**, on the grounds that the second field is written once and
never compared -- it is a label, not a key -- so the two cannot drift in any
way that changes behaviour. If the owner wants the smaller change, B.

### D3. Is the chat list FILTERED to one project, or GROUPED with headers?

MEASURED at 24 conversations in a 755 px viewport; the grouped and filtered
rows are COMPUTED from the measured row heights (68 px with a project tag,
52 px without, 34 px a heading):

| shape | height |
|---|---|
| flat, every row tagged (today) | 1632 px |
| grouped by project, six headings, tags dropped | 1452 px |
| filtered to the biggest project (3 chats) | 156 px |

* **A. GROUPED, one list, headings per project.** Everything stays reachable
  and the empty-state trap is impossible. Cost: it is barely shorter than
  today -- 1452 px against 1632, still 1.9x the viewport -- so it does not
  solve the measured problem. And it REPLACES the today / yesterday / earlier
  buckets, which are the `#chatList .sb-head` elements `probe_page.py` reads
  by name.
* **B. FILTERED to the open workspace, with "All chats" as a first-class
  choice.** The only shape measured to FIT. Cost: filter times search is the
  documented empty-list trap, so AC-222 exists to make the page say WHICH one
  emptied the list; and `probe_page.py` asserts `len(drawn) == len(served)`,
  which is why the filtering must be the DAEMON's (`?project=`) and never a
  page-side rule -- exactly `docs\chat-search.md`'s ruling, for exactly its
  reason: a page-side filter hides rows the daemon would have shown, and a row
  that is not drawn looks like a conversation that does not exist.
* **C. Neither. Leave the sidebar flat and newest-first, and put the
  per-project list on a workspace PAGE.** Closest to Claude.ai. Cost: the
  owner's sentence "chat list history should be PER project" is answered only
  on a surface they have to navigate to.

RECOMMENDED: **B**, done in the daemon, with the day headings KEPT inside the
filtered list. That is the only combination where the owner's words come true
and the measured problem goes away.

### D4. Is there a "current project" at all, or only the live chat's?

Today there is only the live chat's: `chatProject` is read off
`GET /api/session/current` and never remembered from the click, so a switch
made on the phone marks the right row on the desk.

* **A. No view scope. The chat list follows the LIVE chat's project
  automatically.** Cost: the scope moves under the owner without being
  touched -- saying "work in site" repopulates the sidebar. And browsing an
  old project's history is impossible without first moving a tool-enabled
  coding agent into it, which is the one action AC-31 makes expensive on
  purpose.
* **B. A VIEW scope, separate from the agent's working directory, stored in
  `ShortcutStore` beside `sidebar_collapsed`.** This is Cowork's split (1.4).
  Cost: two notions of "which project" that can disagree, and the surface has
  to SAY which is which. Benefit: looking is free and moving still is not;
  AC-31 is untouched because nothing in the view path calls `work_in`.
* **C. A view scope that also moves the agent.** Cost: it makes browsing into
  a spawn. Rejected outright here rather than offered as a real option; it is
  listed so the owner can see it was considered.

RECOMMENDED: **B**, and the single most important consequence is stated in
AC-220: **changing what you are LOOKING at must never change where a coding
agent runs.**

### D5. Where does the project pane live once it is more than a fold?

Measured constraints: the pane is 901 px in a 755 px viewport at five rows;
212 px of that is a form; one line per row costs 406 px total; the 64 px
collapsed rail does not currently work (0.3).

* **A. Stays a fold in the sidebar, rows compacted to one line, the add form
  behind an "add" button.** ~236 px at five rows [COMPUTED: 406 measured,
  minus ~170 px of form]. Cheapest, and the sidebar keeps every launcher one
  click away. Cost: it is still a fold below a chat list, so the two still
  cannot be read together at long chat counts.
* **B. A one-line workspace SWITCHER above the chat list, and the row editing
  moves to a dedicated surface** (Claude.ai's shape, 1.2). The sidebar's
  project cost drops to one row, permanently, at any project count. Cost:
  **the three launch buttons become two clicks instead of one, and the owner
  uses them daily.** That is the real price and it is the owner's to accept
  or refuse.
* **C. B, but the switcher's popover carries the three launchers**, so a
  launch stays one click from the sidebar and only editing moves. Cost: a
  popover with three full-capability coding-agent buttons in it, which is a
  denser control than the appearance pill that set the popover precedent.

RECOMMENDED: **C**. It is the only option that takes the 901 px down to one
row without charging the owner a click on the thing they do every day. The
precedent it leans on is real and recent: the model-picker row
(`web\AGENTS.md`, the owner's second-batch item 5) was rejected on sight --
"the model chooser is too big, ugly, and on top of the chat input instead of
underneath" -- and was redrawn from five 44 px bordered boxes, 94 px tall,
into a line of TEXT that behaves like a button, 30 px. **That is the density
this pane has to reach, and it is the owner's own standard.**

---

## 3. Acceptance criteria

AC-216..AC-227. `plan.md`'s highest in use is AC-215 (`language-auto.md`).
Each says which way it FAILS and whether the failure is recoverable, under
`docs\voice-control.md`'s rule and for its reason: AC-92 was built, shipped
and reverted because nobody had written down what it would look like to be
wrong.

- **AC-216 (the project list fits the container it is in).** With the owner's
  five rows, the projects surface occupies no more than the sidebar's scroll
  viewport at 1440x900 and at 390x844 -- MEASURED, not estimated, at both
  widths. Today it is 901 px in a 755 px viewport.
  Fails as: the same scroll the owner complained about, with a different
  layout. **Recoverable** -- it is CSS and a render function.
- **AC-217 (the conversations and the projects are visible at once).** At the
  owner's real row and chat counts, some part of the chat list and some part
  of the project surface are simultaneously within the sidebar's scroll
  viewport, at every scroll position where a project control is usable.
  Today that number is 0 px.
  Fails as: the owner's literal complaint, unfixed. **Recoverable.**
- **AC-218 (a conversation keeps its project when the row is edited).**
  Editing a project row's path or label -- including adding or removing a
  trailing separator, or changing its case -- leaves every conversation filed
  under it still filed under it. Asserted on the LISTING, not on the record.
  Fails as: a workspace that silently empties, with no error and nothing in
  the log. **NOT recoverable** -- once the daemon has written records under
  two spellings there is no way to tell a genuine move from a rename.
- **AC-219 (the scoping is the DAEMON's).** The chat list is scoped by a
  daemon query (`GET /api/chats?project=...`), the page applies no filter of
  its own, and the count the page shows is the count the daemon served.
  `docs\chat-search.md`'s rule, for its reason.
  Fails as: rows the daemon would have shown, hidden invisibly. And it breaks
  `probe_page.py`'s `len(drawn) == len(served)` check, which is the instrument
  that would catch it. **Recoverable**, and cheaply, because the probe fails
  loudly.
- **AC-220 (looking is not moving).** Changing which workspace the chat list
  shows spawns no process, calls no `work_in`, changes no coding agent's
  working directory, and leaves `GET /api/session/current`'s `project`
  untouched. Asserted by a NEGATIVE: the spawn count and the session project
  are both unchanged across a scope change.
  Fails as: browsing history starts a full-capability coding agent in a
  directory the owner was only reading about. **NOT recoverable** -- a spawned
  agent may already have written.
- **AC-221 (an unfiled conversation is never silently filed).** A record whose
  `project` is `""` stays `""`, is reachable from the surface, and is labelled
  as belonging to no workspace rather than to the root one.
  Fails as: the record stating a fact nobody established, which `chats.py`
  already refuses in writing. **NOT recoverable** once written.
- **AC-222 (a scope and a search compose, and an empty result says which
  emptied it).** With both a workspace scope and a `?q=` in force, an empty
  list says whether the search matched nothing anywhere or nothing IN THIS
  WORKSPACE, and offers the wider one.
  Fails as: the documented filter-times-search trap -- the owner searches for
  a conversation they remember, gets nothing, and concludes it was deleted.
  **Recoverable**, but it costs trust in the search each time.
- **AC-223 (the view scope is the daemon's, and nothing in `web\` names it).**
  The scope survives a reload, is identical on two devices, lives in
  `ShortcutStore` beside `open`, `show_chords` and `sidebar_collapsed`, and is
  NOT a setting -- so `web\` still names none (AC-41, AC-8).
  Fails as: two devices disagreeing, which is D-2 exactly. **Recoverable.**
- **AC-224 (endpoint mode is legible, not merely non-fatal).** With
  `brain_mode: endpoint` the workspace surface says there are no conversations
  on this daemon, and every control that cannot work -- "work here" first --
  is DIMMED with the reason readable, not enabled-and-failing. The three
  launchers stay live, because a terminal needs no brain.
  Today "work here" is enabled in endpoint mode, and clicking it puts the
  daemon's own sentence in `#shortNote` [MEASURED]. That is honest but it is
  not dimmed, and the model-picker row already set the standard: dim on
  `inactive`, stay editable, never disable (`web\AGENTS.md`).
  Fails as: a control that is always there and never works. **Recoverable.**
- **AC-225 (removing a project does not remove its conversations).** Deleting
  a project row leaves every conversation filed under it present, listed and
  openable. The daemon says where they went.
  Fails as: the owner tidies their project list and loses history. **NOT
  recoverable** if anything actually deletes; recoverable if they merely
  become unreachable.
- **AC-226 (the probes still prove what they proved).** `probe_page.py` and
  `probe_ux.py` pass against the new markup, updated in the SAME commit that
  moves it, with the before numbers unchanged or better. AC-172's rule applied
  to this change, and the specific collisions are named in section 6.
  Fails as: sixteen earlier criteria quietly losing their evidence.
  **NOT recoverable later** -- once the probes are stale nobody can tell which
  regression was introduced when.
- **AC-227 (nothing in `web\` names a project, a path or a setting).** The
  workspace names, their paths, their counts, the agent names and the scope
  vocabulary all arrive from the daemon. AC-41 applied to this surface, and it
  is enforced by review plus the existing grep gate -- so the cheap way to
  keep it true is to leave the page nothing to name.
  Fails as: a hardcoded list that drifts, and the drifted copy is the one
  missing the project added last. **Recoverable**, loudly, the first time it
  drifts.

---

## 4. Gherkin

```gherkin
Scenario: AC-216 the pane fits
  Given five project rows and a 1440x900 window
  When the workspace surface is open
  Then its height is at most the sidebar scroll viewport's height
  And the same holds at 390x844 with the drawer open

Scenario: AC-216 edge -- twenty projects
  Given twenty project rows
  When the workspace surface is open
  Then it still occupies at most the scroll viewport
  And the list inside it scrolls on its own rather than growing the sidebar

Scenario: AC-217 both lists are readable at once
  Given five project rows and twenty-four conversations
  When a project control is under the pointer and usable
  Then some part of the chat list is inside the scroll viewport

Scenario: AC-218 a row is renamed and keeps its chats
  Given three conversations filed in "E:\PROJECTS\butler"
  When the owner edits that row's path to "E:\PROJECTS\butler\"
  Then that workspace still lists three conversations

Scenario: AC-218 edge -- case only
  Given conversations filed in "E:\PROJECTS\butler"
  When the owner edits that row's path to "e:\projects\butler"
  Then that workspace still lists the same conversations

Scenario: AC-218 error -- the directory is gone
  Given a project row whose folder has been deleted on disk
  When the owner opens that workspace
  Then its conversations are listed
  And the surface says the directory is missing
  And no coding agent is started

Scenario: AC-219 the daemon does the scoping
  Given a workspace is chosen
  When the chat list repaints
  Then the page issued GET /api/chats with a project parameter
  And the number of rows drawn equals the number the daemon served

Scenario: AC-220 looking is not moving
  Given the live conversation works in project A
  When the owner scopes the chat list to project B
  Then no process was spawned
  And GET /api/session/current still reports project A
  And the coding agent's working directory is unchanged

Scenario: AC-220 and the spoken switch still costs what it cost
  Given the chat list is scoped to project B
  When the owner says "work in project B"
  Then the move goes through work_in and both of AC-31's gates
  And an unrecognised name is still refused aloud and changes nothing

Scenario: AC-221 an unfiled conversation
  Given a conversation written before workspaces existed
  When the chat list is shown
  Then that conversation appears under no workspace
  And its record's project field is still ""

Scenario: AC-222 a search inside a workspace finds nothing
  Given the chat list is scoped to project A
  When the owner searches for a phrase that appears only in project B
  Then the list says nothing matched in this workspace
  And offers to search every conversation
  And searching every conversation finds it

Scenario: AC-223 the scope survives and agrees
  Given the chat list is scoped to project A on the desk
  When the page is reloaded
  Then it is still scoped to project A
  And a phone opening the same daemon is scoped to project A
  And no setting key anywhere names the scope

Scenario: AC-224 endpoint mode
  Given brain_mode is endpoint
  When the workspace surface is opened
  Then it says this daemon holds no conversations
  And "work here" is dimmed with the reason readable
  And the claude, pi and opencode buttons still launch

Scenario: AC-225 a project is removed
  Given three conversations filed in project A
  When the owner removes project A's row
  Then those three conversations are still listed and openable
  And the surface says they are no longer in any workspace

Scenario: AC-225 edge -- the live conversation's project is removed
  Given the live conversation works in project A
  When the owner removes project A's row
  Then the coding agent keeps running where it is
  And the next turn is not silently moved to the default directory

Scenario: AC-226 the probes
  When probe_page.py and probe_ux.py are run against the new markup
  Then both pass
  And every number they printed before is unchanged or better

Scenario: AC-227 nothing is named in the page
  When web/ is grepped for a project path, a workspace name or a setting key
  Then nothing is found
  And every name on the surface came from GET /api/shortcuts or GET /api/chats
```

---

## 5. The build order

Seven phases. Each is independently shippable -- the daemon phases change no
pixel and the page phases need no new route -- and each is independently
verifiable. **Nothing starts until the uncommitted sidebar-resize work in
0.4 lands**, because six of the eight files below are dirty.

### Phase 1 -- Project identity stops being a spelling (AC-218)

`daemon\chats.py`, `daemon\shortcuts.py`, `daemon\adapters\brain_harness.py`,
`test\test_chats.py`, `test\test_shortcuts.py`.

`work_in` writes the row's ID (and, under D2/C, the path beside it as a
label). A one-time migration keyed on `shortcuts.same_dir` re-points existing
records, on its own flag, `migrate_projects`'s shape and for its reason -- a
second flag, because a reused one makes whichever migration runs second a
silent no-op. `_read` keeps its `setdefault`, so a record that predates this
is readable rather than broken.

Verify: pytest only. No UI change, so this can ship alone and be lived with.

### Phase 2 -- The daemon can serve one workspace's conversations (AC-219, AC-221, AC-225)

`daemon\chats.py`, `daemon\adapters\brain_harness.py`, `daemon\butlerd.py`,
`test\test_chats.py`, `test\test_http_surface.py`.

`ChatStore.listing()` and `search()` take a project, `GET /api/chats` gains
`?project=`, and `GET /api/shortcuts` gains a per-row COUNT so a switcher can
say "3" without fetching three lists. The count walk is the one
`ChatStore.listing()` already does, so it goes through `asyncio.to_thread`
exactly as `?q=` does (D-25).

Three values are distinct and all three are real: a row id, the empty string
(unfiled), and absent (every conversation). A project id no row has is a 400
naming it, `POST /api/project`'s rule.

Verify: pytest only. No UI change.

### Phase 3 -- The view scope is stored (AC-223)

`daemon\shortcuts.py`, `daemon\butlerd.py`, `test\test_shortcuts.py`.

`ShortcutStore` gains `view_project`, beside `open`, `show_chords` and
`sidebar_collapsed`, written through the existing `POST /api/shortcuts` named
-key branch. A scope naming a row that no longer exists reads back as "every
conversation" rather than as an error -- that is AC-225's edge case landing in
the store, where it can be made unrepresentable instead of handled.

Verify: pytest only.

### Phase 4 -- The project row becomes one line (AC-216)

`web\index.html`, `web\app.js`, `test\manual\probe_ux.py`.

The measured 133 px row becomes a line of text that behaves like a button --
the model-picker row's own redraw, at the owner's own instruction. The add
form goes behind an "add" control, taking 212 px of permanent chrome down to
a button. Chord slots stay behind their existing disclosure.

This phase alone takes the pane from 901 px to roughly 236 px at five rows
[COMPUTED from the 406 px measured with a one-line stylesheet injected, less
about 170 px of form] and can ship before any of the scoping exists.

Verify: `probe_ux.py` measures the row height, the surface height against the
scroll viewport, and the 44 px touch targets. This is the first phase a
browser must see.

### Phase 5 -- The switcher, and the chat list obeys it (AC-217, AC-219, AC-220, AC-222)

`web\index.html`, `web\app.js`.

One line above the chat list: the workspace name, its count, and a popover
carrying every workspace plus "All chats" plus "No workspace" -- and, under
D5/C, the three launchers for the chosen row. It hangs downward, takes NO case
in the global Escape handler, and closes on a press anywhere else; Escape
closes it only while focus is inside it, through a capture-phase listener of
its own. That is the appearance pill's arrangement and the picker row's
correction, both of which this project has already paid for twice: a listener
on the popover never sees the key when the popover was opened by CLICK, and
swallowing Escape unconditionally costs a STOP (AC-112).

Nothing here filters. The page sends the scope and repaints what the daemon
answers.

Verify: `e2e_scenarios.py`, section L extended.

### Phase 6 -- The workspace surface (AC-224, AC-225, AC-227)

`web\index.html`, `web\app.js`.

Row editing, paths, labels, chords, remove, Browse and the per-workspace chat
list move to a surface reached from the switcher. `openPanel` is hard-wired to
`#panel` and is NOT reusable, which `web\AGENTS.md` already records -- so this
either reuses the settings surface's shell properly or is a second one, and
that choice belongs in the build rather than here.

Endpoint mode is this phase's real work: the surface has to say there are no
conversations on this daemon, and dim "work here" with the reason, while
leaving the launchers live.

Verify: `e2e_scenarios.py` section M, plus the endpoint-mode branch.

### Phase 7 -- The probes and the DOX (AC-226)

`test\manual\probe_page.py`, `test\manual\probe_ux.py`,
`test\manual\e2e_scenarios.py`, `test\test_seams.py`, `web\AGENTS.md`,
`test\AGENTS.md`, `daemon\AGENTS.md`, `AGENTS.md`.

AC-172's rule says the same COMMIT, not the same phase -- so in practice each
of phases 4, 5 and 6 carries its own probe edit, and this phase is the DOX
pass plus the root index gap in 0.4.

---

## 6. The verification plan

### pytest

* `test\test_chats.py` -- phases 1 and 2. The identity migration including the
  trailing-separator and case-only cases; `listing(project=)` and
  `search(project=)`; the three distinct values (a row id, `""`, absent); the
  orphan after a row is removed; that `rotate()` and `rewind()` still carry
  the project forward now that it is an id.
* `test\test_shortcuts.py` -- phase 3. `view_project` round-trips, survives a
  reload, refuses nothing, and reads back as "every conversation" when it
  names a row that is gone.
* `test\test_http_surface.py` -- `?project=` shapes, the 400 for an id no row
  has, and the 404 in endpoint mode.
* `test\test_project_switch.py` -- **AC-220's negative.** A scope change must
  call no `work_in` and spawn nothing. This is the file that already asserts
  AC-31's gates, so the assertion belongs beside them rather than in a new
  file: a second place that knows what "moving a project" means is how the two
  drift (D-18).
* `test\test_seams.py` -- AC-227's grep gate gains the workspace vocabulary.

Validate the instrument on AC-218 and AC-220 specifically (D-13): break the
migration, confirm the mutation applied, watch the RIGHT test fail, restore.
A test that survives the defect it is named after is not a test.

### `test\manual\e2e_scenarios.py`

* **Section L, extended.** L already covers the owner's backlog item 4 -- the
  pane grew a control that is not a launcher, the record followed the agent,
  the sidebar says where each conversation belongs. The scoping stories are
  the same story continued, so they go there: scope the list, count the rows,
  assert the live conversation's project is unchanged, scope to a workspace
  with nothing in it, scope plus search.
* **Section M, new.** The workspace surface: open it, edit a row's path, come
  back and find the conversations still there; remove a row and find them
  still listed; the endpoint-mode branch. M rather than a sub-case of L
  because it needs its own daemon configuration and `main()`'s plan list is
  how a section is skipped.
* J stays LAST, because it kills the daemon.

Both need a real scratch daemon: patchright strips the init-script mechanism
`route_web_socket` depends on, so the socket cannot be mocked
(`test\AGENTS.md`), and `hotkeys_enabled: false` is not optional -- a scratch
daemon with it true installs a system-wide hook and takes the owner's numpad.

### `probe_ux.py` and `probe_page.py`

`probe_ux.py` owns the numbers, because they are the criteria:

* the project row height, against the 133 px before-number;
* the workspace surface height against the sidebar scroll viewport, at
  1440x900 and 390x844 (AC-216);
* simultaneous visibility of the two lists (AC-217), against the 0 px
  before-number;
* the switcher's touch targets -- and stated plainly, the model-picker row
  shipped at 30 px against the 44 px guideline on the owner's instruction, so
  this measures and REPORTS rather than asserting a number the owner has
  already overruled once.

`probe_page.py` carries the selector collisions, and there are three. It reads
`#chatList .sb-head` as the DAY buckets -- if D3/A (grouping) is ever chosen
those stop being days, and the probe silently starts measuring something else.
It clicks `#chatList .chatline:last-of-type .chatrow` to open the oldest
conversation, which is only "the oldest" in a flat newest-first list. And it
asserts `len(drawn) == len(served)`, which is exactly the assertion that
catches a page-side filter -- so it must NOT be weakened when the scope
lands; it is the instrument for AC-219.

### What nothing here verifies

No screen reader, no real voice, no tablet. And nothing measures whether the
owner LIKES it, which is the criterion the model-picker row failed.

---

## 7. What this spec could have wrong

Seven, led by the one that would waste the most work.

**1. The premise may be wrong, and phases 1-3 and 6 are what it costs.** The
owner has TWO chat records and FIVE project rows [MEASURED, 2026-09-11]. "Chat
list history should be PER project" solves navigating a pile, and there is no
pile. If what actually bothers them is the 901 px pane -- which is the half of
the complaint that IS reproducible on their own machine today -- then phase 4
alone is the whole fix and everything else is built for a future that may not
arrive. **This is the first thing to put to the owner**, and it is why the
build order is arranged so phase 4 can ship on its own.

**2. D4's split may be the wrong model, and it is the owner's own words that
say so.** Backlog item 4 says "clicking a project changes the working
directory of the CHAT", and `docs\shortcuts.md`'s whole argument is that a
second list beside the rows is the drift `connections.py` exists to avoid. A
VIEW scope beside the agent's directory is a second notion of "which project"
-- the same shape. If the owner wants one notion, phase 3 is deleted and the
list simply follows `chatProject`, at the cost named in D4/A.

**3. The identity migration may be solving a problem that never fires.** AC-218
is reasoned from `same_dir`'s existence and from the owner's trailing
backslash, not from an observed failure: nobody has yet renamed a row and lost
a workspace, because workspaces are one day old and hold two conversations.
The counter-argument is that it is the one defect on this list that is NOT
recoverable, and that it costs a field.

**4. Filtering may hide the thing the owner was looking for.** D3/B is chosen
on a measurement, and AC-222 is a mitigation rather than a fix. Claude.ai's
hard scope is the shape being copied and its own search has this problem; the
difference is that Claude tells you which pool you searched and Butler would
have to learn to.

**5. The 64 px rail is broken, so one option is priced wrong.** 0.3 measured
the collapse failing to collapse, twice, the second time against HEAD. If that
one-line cascade defect is fixed, a collapsed-rail placement becomes available
and D5 should be re-asked with it on the table -- a 64 px rail carrying five
workspace initials is a genuinely different answer to AC-216 and this spec
priced it out on a broken measurement. If it is not fixed, nothing may lean on
it.

**6. Two clicks for a launcher may be refused.** D5/C exists to avoid it and
puts three full-capability coding-agent buttons in a popover instead, which is
denser than any popover this page has shipped. The owner may look at it and
say what they said about the model picker. There is no measurement that
predicts this; the only instrument is showing them.

**7. AC-217 may be unsatisfiable in a 264 px column.** "Both lists visible at
once" is a geometry claim about a container the owner can now RESIZE (the
uncommitted work), and nothing here has measured it at the minimum width the
store clamps to. A narrow enough sidebar makes the criterion false by
arithmetic, which means AC-217 needs a stated width below which it does not
apply -- and this spec does not state one.


---

## 8. ADDENDUM, 2026-09-11 -- what a second, deeper prior-art pass found

Written after the owner asked to "research claude cowork and gpt cowok for
clues". Two findings contradict this spec's own leanings and one corrects its
premise, so they are recorded here rather than folded silently into section 1.

**THE PREMISE CORRECTION.** There is no OpenAI product called "cowork". The
real comparison -- the only OpenAI surface with BOTH a working directory and
its own chat list -- is the Codex mode of the merged ChatGPT desktop app
(the standalone Codex app was folded into it on 2026-07-09). ChatGPT Projects
on the web hold chats, instructions and files but no folder at all.
And Cowork is not a collaboration surface: it is Anthropic's agentic workspace
inside Claude Desktop, "the same agentic architecture that powers Claude Code,
accessible within Claude Desktop without opening the terminal". A Cowork
PROJECT is local folders plus standing instructions plus a memory store --
which is almost exactly the owner's second-batch item 4, so that item and this
one are one design and should be planned together.

**FINDING 1: COWORK DOES NOT NEST CHATS UNDER PROJECTS.** Its rail carries
Projects and Recents as two INDEPENDENT flat lists; a session never indents
under the project it belongs to. Selecting a project "starts a new Cowork
session with that project's folders mounted and instructions applied" -- so
clicking a project means START A CHAT HERE, not OPEN A PROJECT. That sidesteps
D4 entirely: there is no question about what happens to the conversation you
were in, because you get a new one.
[MEDIUM confidence -- docs plus one bug report whose "expected behaviour"
spells the two-list structure out; no screenshot found.]

**FINDING 2: CODEX DOES NEST, AND IT IS THE SHAPE WITH BUG REPORTS.** Projects
are the top level and threads sit inside them. Its own users have filed: the
folder is not shown on a sidebar row, so with several projects active you
cannot tell which one a conversation belongs to without opening it
(openai/codex#26386); threads hidden at scale after a restart (#23900); a
thread reachable from settings but missing from the sidebar (#25119).
So the nesting this spec's option B leans toward is the one arrangement with
recorded field failures, and Cowork -- which shipped later -- declined it.

**WHAT CLAUDE CODE'S OWN DESKTOP APP DOES, and it is the cheapest answer for a
264 px rail:** ONE session list, with controls at the top that "filter sessions
by status, project, or environment, and GROUP SESSIONS BY PROJECT". Not two
lists. At the owner's five projects and two conversations, two sections means
five rows of chrome above two rows of content.
Its `/resume` picker is worth reading for a second rule: choosing a session
from an UNRELATED project does not resume it -- Claude Code copies a `cd` plus
resume command to the clipboard instead. A cross-project jump is refused and
handed back, which is AC-31's instinct arrived at independently, and it means
this project can keep AC-31 unsoftened while still letting an old conversation
be READ.

**WHAT THIS CHANGES.** `web/mocks/k-workspaces.html` gained a fourth option,
**D -- one list, grouped by project**, which is what the research recommends and
which none of A/B/C was. Every option in that mock prints the project ON the
chat row, which is finding 2's lesson applied.

**WHAT TO REJECT, with the reason.** Nesting chats under projects in the rail
(finding 2). Pin/sort/customise controls on the project list -- seen only in an
unshipped A/B test, and at five projects a sort control has nothing to do.
Copying either company's mode-switcher PLACEMENT: Anthropic put it in the
composer in July and is already testing moving it, OpenAI put it top-left, so
it is a moving target at both and should be decided here on this page's own
grounds. And the IDE furniture -- file tree, terminal pane, diff view -- which
is Codex's answer to reviewing code, where this project's answer is a coding
agent in a terminal window of its own.

**Confidence, stated because these are recent products and the search results
were thin:** HIGH for everything taken from Anthropic's and OpenAI's own docs
(Cowork's project contents and folder mounting, Claude Code's session storage
path, picker fields and clipboard behaviour, the desktop app's group-by-project
control, the ChatGPT desktop modes). MEDIUM for the two sidebar STRUCTURES
above. LOW for every click count -- all derived from documented mechanics, with
neither product run. No annotated screenshots or design write-ups were found
for either product, and two source pages refused the fetcher.

---

## 9. TEN PARALLEL AGENTS -- the owner's vision of 2026-09-11, and what it costs

The owner changed the shape of the ask after reading section 8: "i want to
allow up 10 parallel agents on different projects to work side by side.
clicking on chat loads history of latest chat, user can start a new chat.
sending a new message is what activates the agent, this way we can look before
starting it. so we need to be able to navigate/ switch projects, see chats from
that project, but also see any chat/ active agent so we can navigate through
those quickly."

Six layouts were drawn for it -- `web/mocks/l-rails.html` (rail, tabs),
`m-activity.html` (dispatch panel, board) and `n-triage.html` (triage, side by
side). What follows is the AUDIT that must be read beside them, because three
of its findings change what any of those layouts would cost.

### 9.1 The good news, MEASURED

**"Sending activates the agent" is ALREADY how Butler works.** `_Host.__init__`
sets `_harness = None`, `warm()` deliberately spawns nothing with the argument
written out, and `ensure()` is reached from exactly two places, `turn()` and
`restart()`. A child is spawned lazily at the first turn. This half of the
vision is FREE.

**Ten children is not the problem.** EXECUTED on this machine: one idle pi
child is 162.3 MB and ready in 1.72 s; ten are 1326 MB total, 132.6 MB each,
each ready in 1.53-1.86 s. That is about 2% of RAM. The 1.5-2.3 s spawn figure
in `daemon/AGENTS.md` reproduced exactly.

**The job object already holds them.** EXECUTED: ten pi children are twenty
processes (ten `cmd.exe`, ten `node.exe`), all assigned with no warnings, and
the whole tree is gone within 0.5 s of `TerminateProcess` on the host. No
per-process limit was hit.

**`HarnessSpec` equality already gives per-conversation child identity** (its
`cwd` plus `session_file`), so ten conversations already produce ten distinct
specs with no change to AC-60's one-comparison rule. `harness_for()` already
builds a fresh adapter per call, and ten coexisted in one process.

### 9.2 The blockers, worst first

**1. A SECOND CONVERSATION DISPOSES THE FIRST ONE'S CHILD.** EXECUTED against
the fake harness: entering `turn()` for a second conversation logs "turn 1
never released the harness child before turn 2 asked for one. Disposing it"
and both conversations then append into ONE record, producing an interleaved
transcript. `_Host` holds single slots (`_harness`, `_spec`, `_stream`,
`_cwd`) and exactly one is constructed. This is the subsystem-sized change.

**2. TODAY OPENING IS FREE AND SENDING ABANDONS WHAT YOU OPENED -- the owner's
rule INVERTED.** `ChatStore` has one `_chat`, assigned in only two places
(construction and `rotate`), so there is no third writer and a past
conversation cannot be made live. The page proves it: `openChat` only sets
`viewingChat`, and everything that starts a turn calls `backToLive()` first.
Twelve methods read or write `self._chat`; `listing`, `search`, `find` and
`_records` already read the DIRECTORY and need nothing.

**3. SAYING THE WAKE WORD WOULD KILL EVERY RUNNING AGENT.** `_barge_in` loops
every session and abandons any that is not idle, and `abandon()` reaches
`brain.abort(turn)`, which matches only the single `_turn_id` -- so four of
five children would be orphaned by a cancel that never reached them. This is a
RULING, not a refactor: AC-5 is why it is global.

**4. A device owns exactly ONE turn.** `SatelliteSession.turn` is a single
slot and `owns()`, `send_for_turn()`, `_rearm()` and `_begin_turn()` all key
off it. Ten conversations in one browser tab is not expressible without a map
of owned turns, and that is where the protocol change lands.

**5. The project switch moves the WHOLE daemon**, because `_Host._cwd` is one
slot and `harness_spec_resolver` has no conversation parameter -- it closes
over one chat store and calls `session_ref` as a side effect.

**6. `hub.history` is one deque for the daemon**, with a comment saying why:
"Butler holds ONE conversation (AC-35)".

**7. The protocol carries `turn` and never a conversation**, and
`test_protocol_doc.py` will NOT catch the gap: its regex captures the frame
NAME only and all five assertions compare name sets. Adding a conversation id
to every frame is cheap and nothing goes red if one is missed.

### 9.3 The decisions a ten-agent Butler forces, which a one-agent Butler never had

**Which conversation owns the VOICE.** There is one speaker and one kokoro
worker; an abandoned reply once held it for 7887 ms while the next answer
queued. `_voice_for` and `s.language` are per-DEVICE, not per-conversation.
The mechanism to decide with already exists: `Say(spoken=False)` (AC-40) means
"seen, not heard", so a background conversation can be written and not spoken
with NO new frame.

**What a background agent does when it finishes and nobody is looking.**
Already specified and not built: AC-14's three states and AC-14a's
hold-and-speak-once with a one-hour staleness drop. `plan.md` 4.3 already
reserves `announce` under the "NOT implemented today" marker, and
`test_protocol_doc.py` currently asserts it is never sent -- so shipping it is
moving that line, which is the intended path.

**What STOP means.** A fourth decision, and it bites before the wake word:
AC-111/AC-112 make `stop` end "the turn this session owns", and with ten
running there is no "the turn". Stop is the only irreversible act the page has.

### 9.4 A phased order, each phase shippable alone

1. **Make a conversation openable.** `ChatStore` gains a keyed registry and an
   `open(id)`; one route; the page opens instead of calling `backToLive()`.
   Still one live conversation -- and it ships as the bug fix that sending no
   longer abandons the chat you just clicked.
2. **A child per conversation.** `_Host` into a registry keyed by chat id, and
   `spec_for(chat, cwd)` instead of the closed-over store. Still one turn at a
   time. Verifiable entirely against the FAKE harness.
3. **A turn per conversation on the wire**, `_barge_in` scoped, and its own
   assertion because the protocol gate checks names only.
4. **The voice arbiter** -- one conversation audible, the rest on the existing
   `spoken=False`.
5. **The announcer** (AC-14/AC-14a).
6. **The side-by-side UI**, last, because until 3 and 4 exist there is nothing
   coherent to draw.

**Phases 1 and 2 alone deliver "open ten, each keeps its own agent and its own
project", with sending still serialised** -- which may be most of what the
owner wants, and is a fraction of the work.

### 9.5 What the prior art says about the SHAPE

`claude agents`, Claude Code's own terminal multi-agent view, is the closest
shipped thing to this ask and the reference design. It groups rather than
sorts: `Pinned`, `Ready for review`, `Needs input`, `Working`, `Completed` --
"so the ones that need input are at the top". A row is name, a one-line
auto-generated activity summary refreshed at most every 15 s, and an age.
`Ctrl+S` toggles grouping between STATE and DIRECTORY, which answers "navigate
projects AND see any active agent" with ONE list and two organisations rather
than two competing lists. Attention is drawn in four redundant channels and
never by colour alone; overflow FOLDS ("... N more") and failures never fold.
`Space` peeks at the blocking question without switching.

**Two products name the owner's own rule.** Vibe Kanban ships "Create Task"
beside "Create and Start" -- preparing is free and the act that starts work is
a different button. And Claude Code refuses a cross-project resume rather than
performing it, copying a `cd` command to the clipboard instead, which is AC-31
arrived at independently.

**Caps are almost never by count.** Jules is the exception and the only one
with a documented cap UX: the new-task button is DISABLED and a tooltip says
why, with existing tasks still reviewable. Everyone else governs by quota, so
the user meets the limit as a mid-run failure rather than as a disabled
control.

**Reject:** a kanban board (no first-party product ships one), a collapsed
group with no aggregated indicator (a Codex bug -- the sidebar lies while
collapsed), a badge with no in-app destination (also Codex), a single global
thread (Cowork Dispatch, which already has a feature request against it), and
silent timeouts on human gates. **HALF OF THAT IS WITHDRAWN, 2026-09-12**:
"Dispatch auto-denies a permission after ten minutes" is UNSOURCED and the
likeliest origin is a third-party push service describing its own ten-minute
window, not Anthropic's behaviour -- Anthropic's own help text says the
opposite ("work on complex tasks for extended periods without conversation
timeouts"), and `anthropics/claude-code#37913` is an OPEN request for exactly
the permission timeout that does not exist. What survives is Jules, which
auto-approves a plan on a timer it has never published, and which shipped a
second reviewing agent in January 2026 to compensate -- and it remains the one
thing to reject: a timeout on a human gate turns "waiting for a human" into a
decision nobody made. The wider finding is stronger than the original claim:
across Claude Code, Codex, Devin and OpenHands, NOBODY auto-denies on a
clock.
