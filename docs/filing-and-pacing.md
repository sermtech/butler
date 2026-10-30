# Filing is not needed, the project is the unit, and the text should not wait for the voice

Two items from the owner, 2026-09-16, quoted verbatim below. Item 1 went
through FIVE rounds of owner clarification in one session -- a narrow
reading, a fuller vision, a full set of rulings resolving that vision's
own open questions, a round settling three more, and a final round
establishing how a restored agent's memory actually works. This document
specs the FIFTH, final round; the earlier four are kept as history because
the reversal pattern they repeat (`295bf85`/`8720f02`) is exactly the kind
of thing this project keeps paying to relearn, and a spec that deletes its
own working is a spec inviting a sixth round. Item 2 is unchanged
throughout.

This is a PROPOSAL: its criteria are AC-271 onward and they stay here until
the owner accepts them; `plan.md` is still the contract and was not touched,
and no product code was written for this file.

**AC-271 is the next free number.** The highest AC actually promoted into
`plan.md` is AC-247. The highest anywhere in `docs/` is AC-270
(`docs/triage-list.md`). [READ, `grep -rhoE "AC-[0-9]+" plan.md docs/*.md |
sort -n | uniq`, 2026-09-16.]

Every code citation below is [READ] -- taken from the file at HEAD on
2026-09-16, not run. Nothing here touched the owner's daemon on port 8140;
no scratch daemon was needed either, because this is a specification with
no build step of its own.

**SECTION 8 (2026-09-26) SUPERSEDES PARTS OF THIS DOCUMENT**: a chat belongs
to its project from birth, "work in X" navigates, and the sidebar is a project
tree. R1 and R4 below are amended or overturned there, and D3 is answered B.

**BUILD STATUS, added 2026-09-17.** Item 1 phases 1 through 5 are BUILT:
phase 1 navigation/root-guard/birth/migration (`e537485`), the tile-press
fix upholding `8720f02` (`13ddc9e`), phase 3 project-as-unit-of-concurrency
with the settings, lazy reaper and TOCTOU-fixed slot reservation
(`eb0753e`), phase 4's real-pi memory-preservation measurement (`6fcf3ab`,
confirming section 1.6 risk #1), and phase 5's confirm-to-stop mechanism in
two halves -- the daemon capability (`0a9fd9a`) and the page's dialog plus
`busy` on the wire (`5305a6d`) -- plus a same-day fix, found by the phase-5
review, for a defect that predates this document
(`HarnessConverse`'s shared-scalar reentrancy guard disposing the wrong
conversation's child, `d008cf1`). `7d51d11` is unrelated to filing but
landed in the same session (a stale test fixture, not a filing defect) and
is what made the phase-3/4/5 suite runs trustworthy. Item 1 phase 6 (this
document's own DOX pass) is this pass. **Item 2 ("chat text should not wait
for TTS") is BUILT and RULED, 2026-09-26** -- phase 1 (`359cb96`, a
lookahead of one) and the remainder (a per-turn audio queue, D7 = A, D8 = A);
section 2.5 records the ruling, the measurement and what shipped.

---

## Item 1: navigation is project, then chat -- and one agent per project

### The five rounds, in the owner's own words

**Round 1:** *"the 'file here' function shouldn't exist. it should be very
simple: Project butler is ALWAYS existing/ available. user clicks on
project, starts a chat. chat is always filed there. there's no file here
need. Project butler is the original."*

**Round 2:** *"my vision is this: user always starts by clicking on a
project, then a chat already existing on that project, or a new chat,
which will be filed on the project selected. Always project->chat.
Clicking on a new project, will change the view to the latest chat that
had been clicked on that project. the whole point of parallel agents is
this. click on a project, chat, start an action, while that agent works, I
can click on another project and the view will change to the latest chat
on that project, or to a new chat on that project while the old chat/
previous project continues to run. Only one agent/ active chat per project
at once, but up to the max allowed agents in settings which would be on
different projects."*

**Round 3, which GOVERNS round 2's own internal tension:** *"clicking a
project, will list its previous chats, if any, or give the user a chance
to start a new chat. only starting a new chat, or clicking an existing
chat will change the view to that chat. this way user can view/ search
other projects list of chats before actually changing to that chat.
Starting a new chat on a project where there's already an active agent,
should show a message to the user to confirm: 'Starting a new chat on
this project will STOP the current running agent. Are you sure you want
to continue?' or something like that. Per project last opened is global.
Unscoped all chats view can survive just as a list of all chats, but their
location will still be their original projects where they were created.
Chats created on ALL CHATS will be placed on Butler project. no
unprojected conversations. Chat cannot move between projects after. In the
future, it is possible that i will allow two or more agents on the same
project, but for now, and for other users, it is safer to be ready for it,
but only allow 1 per project for now."*

**Round 4:** *"project held but idle child occupy its single slot for x
time (settings adjustable) default to 60 minutes. if i decline, the whole
gesture is cancelled. no new chat created. Butler root project cannot be
deleted or renamed."*

**Round 5, the final round:** *"the fresh agent, as well as clicking on an
existing chat, will load the saved conversation up to that point."*

**THE DISCREPANCY BETWEEN ROUND 2 AND ROUND 3, NAMED RATHER THAN SILENTLY
RESOLVED.** Round 2 said clicking a project tile "will change the view to
the latest chat that had been clicked on that project" -- a NAVIGATION
act. Round 3 says clicking a project only LISTS its chats, and "only
starting a new chat, or clicking an existing chat will change the view."
**Round 3 governs, explicitly, on the owner's own later instruction.** What
survives of round 2's sentence is demoted from a navigation mechanism to,
at most, a HIGHLIGHT: the project's last-opened chat may be marked in its
own list without the view changing to it. Section 3, D2 asks whether
building even that highlight is worth it.

### 1.1 What exists today, exactly

The control the owner first named is `web/app.js:2252`, inside
`paintFilterHead()` (`web/app.js:2231-2277`) [READ]:

```js
file.textContent = here ? "filed here" : "file here";
```

It sits in the filter head -- the row that appears under the rail once a
project tile has scoped the chat list -- beside the project's name and a
"clear" button. Pressing it calls `postProject(row.id)`
(`web/app.js:2180-2189`), which is `POST /api/project`, which is
`hub.switch_project` -> `_Host.work_in(cwd)` (`daemon/adapters/brain_harness.py:830-864`)
[READ]. `work_in` re-checks the directory against `_spec_for` (AC-31's
second gate), requires `Path(cwd).is_dir()`, and -- only after both pass --
calls `self._chats.set_project(cwd)` (`daemon/chats.py:525-536`) [READ],
which is the ONLY writer of a chat record's `project` field anywhere in
the codebase, and whose docstring says "Called from `work_in` and nowhere
else, which is the whole of the contract."

`newConversation()` (`web/app.js:5230-5250`) already files a NEW chat into
whatever project is scoped at the moment it is minted, by calling
`postProject(chatScope)` right after rotation. **This piece of every round
of the owner's ask is already built and needs no change.**

### 1.2 The reversal history, and round 3's correction to how it should be read

**Both commits were read in full** (`git show 295bf85`, `git show
8720f02`), not summarized from memory.

**295bf85, "a tile press files the conversation" (2026-09-15 morning,
AC-251 v1).** MEASURED on the owner's own shape (6 rail rows, 3
conversations): four of six tiles led to "none yet" because filing was
reachable only from the settings surface; New chat under a scope produced
a conversation filed under the PREVIOUS scope; the pane's header read
"current conversation" over a real transcript. The fix: **a tile press
MOVES the live conversation into that project**, scopes the list, and
leaves the moved conversation on screen. Owner's ruling that day: "B --
clicking a project row changes the working directory of the chat."

**The argument that lost:** a tile is a FILTER control -- pressing it is
how you look at a project's history -- and this design made looking also
mean moving. "pressing along the rail drags the conversation through
every project passed."

**8720f02, "browsing moves nothing, filing is its own act" (2026-09-15
afternoon, AC-251 reversed).** Living with the first version for a few
hours produced: "a few chats are always showing on all projects" (pressing
six tiles in turn drew the SAME conversation under all six) and "I still
don't see a clear easy button to start a NEW chat." The fix: tile press
reverted to browse-only (scope plus open-newest, files nothing); filing
got its own control (the "file here" button round 1 asked to delete);
`#newChatList` was added for the second complaint.

**The argument that lost this time:** AC-251 v1's own argument -- "clicking
a project row changes the working directory of the chat" -- which the
owner had ruled ONLY hours earlier and then falsified by living with it.

**ROUND 3'S CORRECTION, RECORDED PLAINLY BECAUSE AN EARLIER DRAFT OF THIS
DOCUMENT GOT IT BACKWARDS: round 3 UPHOLDS `8720f02`, it does not overturn
it.** "Browsing moves nothing" survives completely -- round 3's own words,
"this way user can view/search other projects list of chats before
actually changing to that chat," is `8720f02`'s exact principle restated.
**AC-229 and AC-240 are UPHELD, not amended**: the scoping is still the
daemon's (AC-229), and opening a past conversation still does not abandon
what was live (AC-240) -- nothing about either changes. What disappears is
narrower than either commit's own subject: the EXPLICIT "file here"
control, because a chat is now filed once, at birth, and never afterward
(section 1.5) -- so there is nothing left that a "file here" button COULD
ever legitimately do.

### 1.3 Navigation, exactly as round 3 specifies it

**Clicking a project tile lists that project's chats and changes nothing
else.** This is `docs/chat-search.md`/`docs/parallel-agents.md` AC-229's
existing `?project=` scoping mechanism, completely unchanged --
`setChatScope(id, false)` rather than today's `setChatScope(id, true)`
(the `true` is what currently also opens the newest conversation; round 3
retires that second argument's effect entirely).

**Only two acts change what is displayed:** clicking an existing chat row
(`openChat`, AC-240's existing route, unchanged) and starting a new chat
while a project is scoped (`newConversation`, unchanged, still files at
birth via `postProject(chatScope)`). Browsing a project's list, however
long that browsing lasts, moves and starts nothing.

**The per-project last-opened pointer becomes a HIGHLIGHT, not a route.**
A new `ShortcutStore` field (`_last_chat_for`, following `_chat_grouping`'s
exact persisted-dict shape) can mark the matching row in a project's list.
**Per round 3, this pointer is GLOBAL** ("Per project last opened is
global"), settled directly in `ShortcutStore`'s own existing
global-view-state precedent (`_chat_grouping`, `_sidebar_collapsed`), with
no new per-device identity mechanism required.

### 1.4 "All chats" survives, chats keep their birth project, and there are no unprojected conversations

**Three rulings, and they compose into one data-model invariant.**

1. **"Unscoped all chats view can survive just as a list of all chats, but
   their location will still be their original projects where they were
   created."** The existing unscoped `GET /api/chats` (no `?project=`)
   stays exactly as it is -- a flat, newest-first list across every
   project, read-only for browsing in precisely the way section 1.3
   describes for a single project's list.
2. **"Chats created on ALL CHATS will be placed on Butler project."**
   `newConversation()`'s existing filing-on-mint behaviour
   (`postProject(chatScope)`) needs exactly one addition: when
   `chatScope === null` (the unscoped view), it files into the root
   ("butler") project's row id instead of skipping the call.
3. **"no unprojected conversations."** This is the sentence that turns
   (1) and (2) from a UI convenience into a hard data-model invariant:
   `project` is NEVER the empty string, for any chat, at any point after
   this ships. **This is a DELIBERATE OVERTURN of a RULED decision**
   (`docs/workspaces-ui.md` D1/A, 2026-09-11: "'no project' is a real,
   permanently-present group") **and it directly contradicts
   `daemon/chats.py`'s own standing comment**, kept here rather than
   deleted, the way this project's convention keeps every losing argument
   (AC-97's docstring, D-20's own overturned half):

   > "They are deliberately NOT attributed to the root project -- they
   > were not all about Butler, and inventing that would be the record
   > stating a fact nobody established."

   **The owner's 2026-09-16 ruling overturns this in plain terms: every
   chat DOES belong somewhere, and where a chat with no explicit scope
   belongs is Butler's own project, by construction, not by inference.**

**The migration this requires, specified with the same care
`ShortcutStore.migrate_projects` got** (`daemon/shortcuts.py:752-811`)
[READ]. `docs/triage-list.md` section 2.1 counted the current corpus
precisely: **4 real conversation records, all 4 already filed under one
project** [MEASURED 2026-09-16] -- so on the owner's own disk there may be
ZERO records to migrate today, but the mechanism has to be correct for any
other install.

- **What it does:** one pass over `ChatStore._records()`, and for every
  chat whose `project` field is the empty string, `set_project(app_dir)`
  is called on it.
- **When it runs:** at boot, alongside `ShortcutStore.migrate_projects`
  (which must run FIRST in the same boot, since this migration needs
  `app_dir`'s path to exist as a real, matchable directory first).
- **That it runs once, and WHY it does not strictly need a persisted flag
  the way `migrate_projects` does:** `migrate_projects`'s flag exists
  because it reads a RETIRED settings key that is deleted in the same
  commit. This migration's own trigger condition (`project == ""`) is a
  fact about the RECORD itself, so re-running it on a fully-migrated store
  is a genuine no-op. A flag is still worth adding for auditability.

### 1.5 A chat's project is fixed at birth, and this reopens a real conflict with existing code

**"Chat cannot move between projects after."** Read plainly, this
retires the file-here button (already gone) AND retires any future UI
gesture shaped like it. But it collides with something that ALREADY
SHIPS and is not about a button at all: `_Host.work_in(cwd)`
(`daemon/adapters/brain_harness.py:830-864`) calls
`self._chats.set_project(cwd)` on EVERY successful spoken "work in
project X," for the CURRENT (already-existing) conversation, since
backlog item 4 shipped on 2026-09-11.

**This is a genuine, unresolved tension, and this document does not pick
a side.** Two readings: **A**, the ruling is about UI controls, not the
spoken switch, and `work_in` keeps calling `set_project` exactly as
today; **B**, the ruling is literal and total, and `work_in` must stop
calling `set_project` for an already-born chat, splitting "where the
agent currently runs" from "which project the chat belongs to" for the
first time. Section 3, D3 surfaces this explicitly.

### 1.6 One active agent per project, designed for N -- the cap's counting rule settled in three stages, and what a restored agent actually remembers

**Today, `_Host._children` is keyed by CHAT ID, with no project dimension
at all** (`daemon/adapters/brain_harness.py:219`) [READ]:

```python
self._children: dict[str, _Child] = {}
```

The owner's own instruction -- "design for N agents per project; enforce
1 for now... raising it later should be a NUMBER, not a redesign" -- means
naming precisely which of the four methods that drive this dict would
have to be REDESIGNED (not merely re-tuned) if "1" were baked in as a
boolean rather than carried as a comparison against a limit:

- **`live()`** (`:284-286`): `return len(self._children)`. **Must not be
  redesigned**: once the exclusivity check below holds, this arithmetic
  is already correct for "how many projects hold a child" regardless of
  the per-project limit.
- **`plan(chat)`** (`:392-430`): `held = self._children.get(chat["id"])`.
  **The correct shape is a COUNT-versus-LIMIT comparison from the
  start**: count how many OTHER chat ids' held children share this
  chat's project (via `shortcuts.same_dir` on `HarnessSpec.cwd`, never
  `ShortcutStore.row_for` alone), and compare against a new limit
  (`project_agent_limit`, default 1) rather than a hardcoded `1`.
- **`ensure(chat, spec)`** (`:432-476`): spawns keyed by `chat_id`. **Must
  not be redesigned**: one child per chat id is right whether the
  per-project limit is 1 or 10.
- **`cap_refusal()`** (`:503-534`): its own example text is `"4 in
  butler, 3 in SITE"` -- impossible once the limit is enforced at 1.
  **Needs a wording change, not a structural one**: state project NAMES
  at limit=1, and let count-based wording return automatically if the
  limit is raised.

**The global cap (`harness_agents_max`) and the new per-project limit are
TWO SEPARATE NUMBERS.** "up to the max allowed agents in settings which
would be on different projects" (round 2) is the EXISTING
`harness_agents_max`. "only one active chat per project at once" is a
NEW, independent limit, scoped to one project.

**THE CAP'S COUNTING RULE, SETTLED IN THREE STAGES, RECORDED IN ORDER SO A
LATER READER SEES A PROGRESSION RATHER THAN A CONTRADICTION.**

```
  STAGE 1 (today, shipped)        STAGE 2 (CLAUDE.md R4,           STAGE 3 (round 4,
  live() counts every HELD        2026-09-16 morning, NOT          2026-09-16, THIS
  child, AC-59's own model:       BUILT): count only BUSY          DOCUMENT): busy, PLUS
  "ten finished conversations     children. An idle-but-held       a BOUNDED IDLE GRACE.
  fill a cap of ten with          child stops occupying a          A child keeps its
  nothing running."               slot the moment it goes          project's slot while
                                  idle.                            busy AND for a
                                                                   configured period after
                                                                   -- default 60 minutes --
                                                                   then the slot releases
                                                                   automatically.
```

**Stage 3 governs.** It is stricter than stage 2 and looser than stage 1.
`CLAUDE.md`'s R4 is superseded by stage 3 rather than silently dropped;
the DOX pass in section 4 updates R4's own entry to say so.

**THE NEW SETTING.** `project_idle_grace_minutes`, in the `"harness"`
group beside `harness_agents_max` (`daemon/settings.py:639-659`, the
exact precedent shape -- `_s(key, default, group, label, kind="number",
minimum=..., maximum=..., help=...)`), `kind="number"`, default `60`.
**Not restart-flagged**: `daemon/settings.py`'s restart flag
(`restart=True`, seen only on `brain_mode`, `:454`) marks settings baked
into a spawned child at construction; this one is read live at the
moment a collision or a cap check happens, so "takes effect on the next
thing you say" is accurate for it exactly as it already is for
`harness_agents_max`. The companion `project_agent_limit` setting
(section 3, D5) belongs in the same group.

**THE REAPER, AND WHY IT IS SAFE UNDER D-1.** No background timer, no
task to leak or cancel. `_Host` records when a child WENT idle
(`_idle_since: dict[str, float]`, set at the same two places
`_last_outcome` already is -- `HarnessConverse._answer` and `._give_up`,
the only places a turn actually ENDS -- and cleared the moment that
chat's child is reused for a new turn). The grace period is checked
LAZILY, at the exact moment `plan()` already runs its project-collision
count: a held child whose `_idle_since` is older than
`project_idle_grace_minutes` is treated as already gone for the purpose
of that count, and is disposed at that point. This mirrors the standing
guidance behind it: **a lazy check at cap-time is cheaper and safer than
a timer** -- a timer is a task this process must remember to cancel on
every teardown path (D-1's own argument against anything that can
outlive its purpose), while a lazy check has no state that can leak.

**THE DISCONNECT-SURVIVAL INTERACTION** (`017f8a1`, 2026-09-16): a turn
that survives its OWN socket disconnecting is still BUSY. So
`_idle_since` must be set only when the turn actually ENDS, never when a
socket merely drops.

**THE MEMORY QUESTION, ANSWERED FROM THE CODE RATHER THAN ASSUMED, WITH
FILE:LINE, AND CORRECTED ONCE ALREADY IN THIS DOCUMENT'S OWN DRAFTING.**
Releasing an idle project's slot means calling `_Host.dispose(chat_id)`
(`daemon/adapters/brain_harness.py:884-903`) [READ], which drops the held
`_Child` and calls `held.harness.dispose()`.

**`caps.native_resume` being "declared, frozen by conformance and
unread" (`daemon/AGENTS.md`) is a claim about a FLAG, not about a
MECHANISM, and it must not be read as "pi cannot resume."** What is
unread is whether any CALLER branches on `native_resume`'s boolean value
-- nobody does, because nobody needs to. pi's resume mechanism is WIRED,
by construction, on every single spawn, because it does not work through
that flag at all:

`harness_pi.HarnessCaps` (`daemon/adapters/harness_pi.py:564-568`) [READ]:
```python
self._caps = HarnessCaps(
    # pi resumes by being pointed at its own session FILE, not by an
    # opaque id handed back to the caller.
    native_resume=False,
    ...
```

`harness_pi._argv(spec)` (`daemon/adapters/harness_pi.py:624-626`) [READ]:
```python
session = reject_shell_metacharacters("spec.session_file", spec.session_file)
argv = ["cmd", "/c", "pi", "--mode", "rpc", "--session", session]
```

Every spawn -- the FIRST one for a conversation and every one after a
dispose -- passes `--session <spec.session_file>`, and `session_ref(chat,
harness)` "derives the path from the chat id and hands back a
byte-identical one on the next call" [MEASURED 2026-08-18,
`daemon/AGENTS.md`].

**Does disposal touch the session FILE itself? Read, with file:line, and
the answer is NO.** `HarnessPi.dispose()` and `_close_child()`
(`daemon/adapters/harness_pi.py:642-688`) [READ in full] only ever act on
the PROCESS: close stdin, wait for exit, and `taskkill /F /T` the tree if
it does not exit on its own. Neither method contains an `unlink`, an
`os.remove`, or any other reference to the session file path at all --
the file lives entirely outside this method's reach, because it is PI'S
OWN persistence, written by the pi process while it runs, not Butler's.
Separately, `_Host.dispose(chat_id)` (`brain_harness.py:884-903`) never
touches `ChatStore` or any session reference either -- it only pops
`_children`/`_last_action` and calls the adapter's `dispose()`. **The
only thing in this codebase that removes a session reference is a
rewind's `_move_session`** (`daemon/chats.py:836`, renaming the file away
from the conversation it belonged to, AC-192) **or the harness's own
narrow report of a missing session** (`_forget_a_dead_session`, one
regex match on pi's own words). Neither the idle-grace reaper nor the
confirm-to-stop dispose is either of those. **So the session file
survives BOTH disposal paths this document specs, and the next spawn for
that conversation resumes from it exactly as before.**

**TWO DIFFERENT RESTORES EXIST, AND THIS DOCUMENT NAMES WHICH ONE HAPPENS
AT EVERY RESTORE POINT (round 5).**

```
  SESSION-FILE RESUME                        TRANSCRIPT SEED
  (the harness's OWN memory,                 (Butler's fallback,
  including tool history --                  when the harness's own
  what it read, what it edited)              memory is gone)

  Triggered by: `HarnessSpec.session_file`   Triggered by:
  pointing at a file that already exists     `ChatStore.needs_seed()` --
  and has content. This is what happens      "a record with messages and
  after: an idle-grace reap (just shown,     no `sessionRef`" -- which is
  file survives); a confirm-to-stop          true ONLY after a rewind's
  dispose (same code path, same              `_move_session` renames the
  guarantee); clicking an EXISTING chat      file away (AC-192), or after
  and then sending (AC-239: opening          the harness's own report of a
  spawns nothing, the FIRST send after       DEAD session
  is what calls `ensure()`, and              (`_forget_a_dead_session`).
  `sessionRef` was never touched by          `_Host.seed_note()`
  merely opening it).                        (`brain_harness.py:692-709`)
                                             [READ] returns "" in EVERY
                                             ORDINARY TURN -- self-
                                             limiting, because `ensure()`
                                             sets the reference on the
                                             very turn it is read for.
```

**THE RULE, STATED ONCE: resume the session file if it exists; seed the
transcript if it does not.** They restore DIFFERENT things and the first
is strictly better where available. Neither the idle-grace reaper nor the
confirm-to-stop mechanism this document specs ever clears a session
reference, so **both of them land in the LEFT column, not the right one**
-- a reaped or stopped project's next turn RESUMES via the session file,
carrying the harness's own memory of what it did, not merely what was
said. The right column (seed) is exclusively `docs/rewind.md`'s territory
and this document adds nothing to it.

**THREE CONSEQUENCES, STATED PLAINLY RATHER THAN LEFT TO BE DISCOVERED.**

1. **`seed_note` trims NEWEST-FIRST, so on a long conversation the FIRST
   thing discarded is the ORIGINAL INSTRUCTION** -- often the one thing
   the agent most needs (`dialogue.py:107` `SEED_MAX_CHARS = 12000`,
   `daemon/dialogue.py:115-133` `seed_note()`, the reference
   implementation's own cap, kept rather than re-derived). This is the
   sharp edge of the FALLBACK path only -- it never applies to a
   session-file resume, which is unbounded by this cap because it is not
   a rendered transcript at all, it is the harness's own file. Section 3,
   D6 asks whether anything should change about this trimming rule, as a
   decision this document does not make.
2. **A transcript-restored agent knows what was DISCUSSED but not what it
   DID, so it may redo or contradict its own work.** `docs/rewind.md`
   already records the mirror of this -- a rewound conversation talking
   to an agent whose edits are still on disk -- and this document cites
   that finding rather than re-arguing it. **This bites only on the seed
   path, never on session resume**, which is the reaper's own path and
   therefore not a new risk this document introduces.
3. **Respawn costs pi's measured startup, 1.5-2.3 s** (`docs/harness-
   mode.md` section 1, 21 runs, [MEASURED 2026-08-17]), so returning to a
   project after the idle grace is a PAUSE, not a stall.
   `STARTUP_TIMEOUT_S = 90` is generous against it deliberately.

**What this document could NOT independently verify, stated plainly
rather than assumed away:** the mechanism (same file, same `--session`
flag on every spawn) is unchanged from the design `plan.md:467-471`
measured under AC-46 ("pi DOES resume from a session file across
separate children... an unbroken `parentId` chain... It lost time and
reliability, not memory"), and that measurement is real evidence the
underlying pi behaviour works as described -- but it was taken under a
per-MESSAGE dispose trigger in 2026-08, not under an IDLE-TIMEOUT trigger,
and this document did not re-run that measurement against the new
trigger. Section 7 lists this as a risk.

**THE REAPER NEEDS NO ABORT.** A child eligible for reaping is, by
definition, IDLE -- no turn is in flight for it, so there is nothing to
gracefully interrupt. `_Host.dispose(chat_id)` alone is the whole
mechanism.

### 1.7 The confirm-to-stop mechanism, for a BUSY collision only, and the daemon capability it requires that does not exist today

**Round 3's answer to "what happens on collision with a BUSY agent" is
REPLACE, WITH CONSENT**: *"Starting a new chat on this project will STOP
the current running agent. Are you sure you want to continue?"* **Round 4
settles what a decline does: "if i decline, the whole gesture is
cancelled. no new chat created."** So the confirm gates the ENTIRE act --
there is no partial outcome.

This mechanism is needed only when the colliding child is actually BUSY
(a turn is in flight); section 1.6 already established the idle case
needs no abort at all. Section 3, D1 covers the remaining edge -- a
collision with a child that is idle but still inside its grace window.

**Today's stop mechanism only reaches "the turn this device owns."**
`docs/parallel-agents.md` phase 6b and `daemon/AGENTS.md`'s own words:
*"stop only reaches the turn in flight... so a background conversation's
idle child cannot be stopped until phase 8 makes stop act on the
conversation on screen."* Phases 7, 8 and 9 of `docs/parallel-agents.md`
were explicitly DEFERRED on 2026-09-12. **The confirm-to-stop gesture
needs exactly the piece that was deferred: a way to stop a NAMED
conversation's in-flight turn regardless of which device is driving it.**

`_Host.dispose(chat_id)` already exists but its only existing caller
(`session_delete`) never disposes a BUSY child. Calling `dispose()`
directly on a chat whose `_reply` coroutine is ACTIVELY awaiting harness
events skips the documented graceful sequence -- `abort()` THEN
`dispose()`, which is what lets pi settle in 1.51 s and still emit its
own ending -- and leaves whichever session's `_reply` loop was iterating
that stream to hit a dead subscription with no coordinated teardown.

**So this feature needs new daemon work.** At minimum: a way to find
whether chat X currently has a turn in flight (today only discoverable
per-SESSION via `turn_by_chat`/`chat_by_turn`, never globally); a way to
signal THAT turn's owning session to abort gracefully (reusing
`abandon()`'s abort-then-dispose sequence, scoped to one conversation);
and a way for the confirm's caller to know the stop completed before
proceeding to spawn the new chat's own child. Section 4 gives this its
own build phase.

**This is also a UI-local confirmation, not `AC-234`'s question card.**
A plain client-side confirmation gate in front of the "start new chat
here" route is the right shape.

### 1.8 The root project cannot be deleted or renamed, and this needs a real, daemon-side enforcement

**"Butler root project cannot be deleted or renamed."** Everything else
in this document now depends on the root project existing -- it is where
a chat created from "All chats" is born and where every migrated
unprojected conversation lands.

**Where it belongs.** `POST /api/shortcuts` is the one write surface, and
`ShortcutStore` is the one store -- so the refusal belongs in
`ShortcutStore` itself, checked before either `remove(sid)` or the
path-edit branch commits anything, exactly the way `_collide()` already
refuses a duplicate chord (`daemon/shortcuts.py:691-699`).
`ShortcutStore.remove(sid)` (`daemon/shortcuts.py:675-679`) [READ] has no
such check today:

```python
def remove(self, sid: str) -> None:
    if sid not in self._rows:
        raise KeyError(f"no shortcut with id {sid!r}")
    del self._rows[sid]
    self.save()
```

**What the refusal looks like.** A new check, comparing the row's `path`
against `app_dir` via `shortcuts.same_dir`: if the row being deleted or
re-pathed IS the root row, raise a `ShortcutError` naming why -- the same
translated-at-the-boundary exception `_chord()` already raises, which
`POST /api/shortcuts` already turns into a 400 with the reason in the
body. A caller sees a 400 and a sentence, exactly the shape every other
refused shortcut edit already produces.

**Renaming is the harder half, and it is refused at the same check.** The
pane's existing "edit" control can change a row's `path`; the SAME check
must also refuse a path-edit that would move the root row's `path` away
from `app_dir`.

**Stated plainly: this is a CORRECTNESS guard, not a security boundary.**
`CLAUDE.md`'s R2 already records that `POST /api/shortcuts`'s
unauthenticated tailnet write surface is ACCEPTED, not fixed -- "the
tailnet is the boundary." This refusal does not change that posture: an
unauthenticated caller can still add, remove and re-path every OTHER row.
What this refusal protects against is Butler's OWN feature set breaking
itself the moment the one row several features now assume always exists
is removed or silently re-pathed.

### 1.9 What round 5 leaves open

Six decisions, in section 3 as D1 through D6. None is decided here.

---

## Item 2: "chat streaming should not wait for TTS"

The owner's words, verbatim: *"For some reason, chat streaming/ appearing on
chat lane is waiting/ pausing to accompany the tts. that is undesirable. chat
should appear when is available. tts answer can keep normal pacing as it is
now."* Unchanged from the first draft of this document.

### 2.1 The mechanism, traced exactly

**The hypothesis in the brief held, in full, with one refinement that makes
the fix's design harder rather than easier.**

`Hub._reply` (`daemon/hub.py:2466-2565`) [READ] drives one turn:

```python
stream = self.brain.turn(text, ctx)
while stream is not None:
    ...
    async for intent in stream:
        if not s.owns(turn):
            return
        if isinstance(intent, Say):
            if not await self._speak(s, turn, intent.text, intent.tier,
                                      spoken=intent.spoken, keep=intent.keep):
                break
        ...
```

`self.brain.turn(text, ctx)` is an async generator. The conversation tier
(`brain_endpoint.py:104-247`, and `brain_harness.py`'s equivalent for a
harness-mode turn) yields **one `Say` per sentence** as it streams the
model's output and finds a complete sentence boundary
(`speech.drain_sentences`, `brain_endpoint.py:180-188`) [READ]. The critical
fact about a Python async generator: it is **suspended between yields**. It
does not keep pulling tokens off the LLM's HTTP stream while `_reply`'s
`async for` loop is busy doing something else with the sentence it already
has -- it only resumes, and only then reads more of the stream, when
`_reply` calls `anext()` again. And `_reply` does not call `anext()` again
until `await self._speak(...)` for the CURRENT sentence returns.

`Hub._speak` (`daemon/hub.py:2742-2880`) [READ] is what `_reply` awaits per
sentence. Inside it, for a normal (audible, kept) `Say`:

```python
s.state = SPEAKING
if keep:
    await s.send_for_turn(turn, type="reply", text=text, tier=tier, spoken=True)
...
for segment in speech.segments(text, first_max_words=first_cap):
    ...
    async for chunk in self.tts.synthesize(segment, voice, speed=self._speed(), handle=turn):
        ...
        await s.send_audio(turn, chunk)
    ...
```

**The `reply` text frame for ONE `Say` is sent BEFORE that `Say`'s own audio
synthesis starts** (`hub.py:2803-2804`, ahead of the `for segment in
speech.segments(...)` loop at `:2831`). So within a single sentence, text
genuinely does lead audio -- the ordering the brief guessed is right for the
INSIDE of one `Say`.

**What is NOT decoupled is ACROSS sentences.** `_speak` does not return to
`_reply` until the entire `for segment in speech.segments(text, ...)` loop
has finished -- which means every chunk of TTS audio for sentence N has been
generated and streamed to the socket -- because that `for` loop and the
`async for chunk in self.tts.synthesize(...)` inside it are BOTH inside the
one `await self._speak(...)` call. Only once that whole call returns does
`_reply`'s `async for intent in stream:` ask the brain generator for its
next item, which is what lets `brain_endpoint.turn` resume reading the LLM's
stream and eventually yield sentence N+1's `Say`. **So sentence N+1's `reply`
frame cannot reach the page until sentence N's audio has been fully
synthesized and sent**, even though the LLM may have already produced
sentence N+1's text and it may be sitting, unread, in the HTTP client's own
receive buffer the whole time `_speak` is busy with sentence N's audio.

**The refinement the brief's hypothesis did not have:** kokoro's TTS engine
runs a pool of **exactly one persistent worker** by explicit design choice
(`daemon/engines/tts/tts-service.js:132-149`) [READ]:

> "Default to ONE persistent warm worker. On a CPU box shared with the LLM
> stream + STT + embedding workers, a pool of 2 only doubles onnxruntime
> thread pressure and buys nothing (kokoro serializes per worker anyway)."

CPU, fp32 dtype are confirmed the same way (`tts_kokoro.py:377`, `:623`)
[READ]; the exact thread count per worker was not independently pinned down
this session and is not load-bearing to the finding -- what matters is that
there is exactly ONE worker, so synthesis for sentence N+1 cannot even
START concurrently with sentence N's synthesis today, regardless of what the
Hub does with `await`. A design that only reorders when the `reply` TEXT
frame is sent, without also letting synthesis for two sentences overlap,
still serializes AUDIO exactly as it does now -- which is fine, because the
owner explicitly asked for that ("tts answer can keep normal pacing as it is
now"), but it means the fix is a change to WHEN TEXT IS SENT, and nothing
about the audio pipeline, pool size, or synthesis order needs to move.

**`_await_playback` is confirmed NOT in this loop.** It is called only at
turn-end sites (`hub.py:1927, 2047, 2364, 2439, 2464, 2585`) [READ,
grep-confirmed]. So today's defect is precisely as diagnosed: the text lane
is gated on synthesis completing and being SENT to the socket, not on the
room having finished HEARING it. The visible symptom -- text pacing with the
voice -- comes from the SEND-side gate, not a playback-side one.

### 2.2 The tension a decoupling fix must resolve, precisely

**Two different records exist, and they are gated differently today, which
changes what "text runs ahead" would break.**

**(a) `hub.history` (AC-64), the router's own short memory.** Built by
`Hub._remember` (`hub.py:2593-2638`) [READ], called UNCONDITIONALLY in
`_reply`'s `finally` (`hub.py:2582`) regardless of brain mode. It is
built from `s.spoken` -- the list `_speak` only appends a segment to AFTER
that segment's audio has actually been streamed to the socket
(`hub.py:2852, 2871`) -- and specifically NOT for a segment cut off mid-
stream by barge-in (`hub.py:2861-2867`, `s.interrupted = True; return
False`, no append). The docstring calls this out directly: telling the next
turn's router that Butler said a sentence the room never heard is "the
context-desync defect `ports.TurnContext.history` was written to prevent...
the most-corroborated finding in `.references/`" (D-20). **This record is
gated on AUDIO, not on TEXT, and it is used by every turn's router
regardless of brain mode**, because `_remember` has one call site with no
mode branch.

**(b) `chats.py`'s persisted record (`state/chats/*.json`), harness mode
only.** Written by `_Host.record(chat_id, "assistant", ...)`
(`daemon/adapters/brain_harness.py:632`, called at `:1203, 1449, 1503, 1659`)
[READ]. Critically, this call happens **inside the brain adapter itself**,
at the END of the harness's own `_answer` generator (`brain_harness.py:1449`,
`self._host.record(chat_id, "assistant", " ".join(answered).strip())`,
immediately before `yield Done(...)`), using `answered` -- the TEXT the
harness generated -- with **no reference to `s.spoken`, to `_speak`, or to
whether any of it was actually played over the speaker.** This record is
ALREADY gated on text generation, not on audio, for harness-mode turns; the
only way it is NOT written at all is if `_reply`'s `async for` loop returns
early (`s.owns(turn)` false) and the generator's own `finally`/`_let_go`
tears it down before this line is ever reached -- a whole-or-nothing outcome
today, never a partial one.

**So the AC-64/D-20 danger the brief named is real for (a) and already does
not exist for (b).** Decoupling text-send from audio-send does not put
`chats.py`'s persisted record at any NEW risk in harness mode, because that
record was never gated on `s.spoken` in the first place. What it DOES put at
risk, and this is the finding the brief's framing did not have: **the PAGE's
own transcript**, and separately, **`hub.history`'s router context for every
mode**.

**The page's own transcript risk.** `_speak` sends the `reply` frame with
`spoken=True` (`hub.py:2803-2804`) BEFORE synthesis for that sentence has
even started, let alone finished. If a caller decouples further -- sending
several sentences' text ahead of their audio -- and a barge-in lands after
sentence N+2's TEXT has already gone out marked `spoken=True` but before its
audio has started synthesizing, the PAGE has already rendered that sentence
as a normal, fully-spoken line (no `.unspoken` marking -- see below), while
the room never heard it and `s.spoken` correctly excludes it from
`hub.history`. **This is a display-side lie, not a model-context corruption
-- but it is exactly the kind of thing `docs/parallel-agents.md` AC-238 had
to build a real mechanism for** (`.unspoken`/shown-not-heard marking) rather
than assume away.

The marker already exists and is the right building block. `web/app.js`'s
`logReply(text, tier, spoken)` (`web/app.js:1516-1552`) [READ] tracks
`openItem.unspokenFrom`, the offset in the transcript's raw text where a
`spoken=false` reply frame started, and renders everything after it inside a
`<span class="unspoken">` with the comment "Sent to be READ, not heard
(AC-40)." Today this only fires for two cases: the truncated tail of a
token-capped reply (`brain_endpoint.py:228`, `Say(tail, ..., spoken=False)`),
and AC-238's mute-mid-turn for a conversation the device is no longer
showing. **Reusing this exact mechanism is the natural way to make a
decoupled design honest**: if text is sent ahead of its own confirmed
playback, and a barge-in lands before that playback happens, the page would
need a way to reclassify already-shown text as unspoken retroactively --
which is a NEW capability this marker does not have today (it is written
once, forward, as frames arrive; nothing currently walks it backward to
relabel a span after the fact).

**The router-context risk (`hub.history`).** This is the one place a
decoupled design has no cheap answer, because `hub.history`'s whole
contract is "what was SAID is what the room HEARD" (D-20), and if text
generation and audio playback are decoupled, `_remember` still has to wait
for the SAME thing it waits for today -- actual audio completion -- to know
what to append. Decoupling the PAGE's view does not, and should not, change
when `_remember` runs; it already runs once per turn at the end, independent
of per-sentence pacing. So this risk is smaller than it first appears: **the
router's memory is already turn-granular, not sentence-granular**, and a
fix that only changes when TEXT FRAMES are sent (not when `_remember` reads
`s.spoken`) leaves AC-64/D-20 exactly as safe as today.

### 2.3 What a decoupled design must preserve, named directly

1. **`_await_playback` and turn-ending timing are untouched.** Nothing about
   when a turn is considered OVER (and therefore when `_rearm`, the next
   capture, etc. happen) should move; only the `reply` TEXT frame's send
   time moves earlier.
2. **`hub.history`/AC-64 keeps being built from `s.spoken`, once, at turn
   end**, exactly as `_remember` does today -- a decoupled design does not
   change WHAT is remembered, only when the page LEARNS the text.
3. **Barge-in (`abandon()`/`s.owns(turn)`) must still be able to reach
   audio that has not started yet.** `_speak`'s `handle=turn` on
   `self.tts.synthesize(...)` (`hub.py:2857-2860`) is what lets `abort()`
   cancel synthesis in flight for the CURRENT sentence; a design that queues
   several sentences' TEXT ahead of their audio needs the same handle to be
   able to cancel synthesis for a sentence whose text has ALREADY been sent
   but whose audio has not started -- otherwise a barge-in stops the audio
   the room can hear but leaves several sentences of un-audited text sitting
   in the transcript with no way to know whether they were ever going to be
   spoken.
4. **The mute path (AC-238, `spoken=False` for the remainder of a reply
   when the device looks away) and the decoupling must not fight.** AC-238's
   check happens once per `_speak` call, before synthesis; if text for
   several sentences is queued ahead of audio, a look-away needs to mute
   ALL of the not-yet-played queued sentences' audio, not just the one
   `_speak` currently has open.

### 2.4 What this reveals about the fix's shape, without choosing it

The narrowest fix that satisfies "text should not wait for TTS" while
changing nothing else is: **split `_speak` into two independent legs per
`Say`** -- one that sends the `reply` text frame and returns immediately
(or nearly so), and one that performs synthesis+send exactly as today,
queued so that only ONE synthesis runs at a time (matching the pool's own
size-1 reality) -- with the router's `hub.history`/`chats.py` writes
UNCHANGED in what they read from (`s.spoken`, populated the same way, on the
same schedule, by the audio leg). Section 3 turns the open parts of that
shape (marking, barge-in reach, and the `spoken=True`-then-retract case)
into owner decisions, because they are product choices about what the
transcript is allowed to claim, not implementation details.

### 2.5 RULED AND BUILT, 2026-09-26

**The ruling (the owner delegated it: "make sensible choices").**
(a) The page shows reply text the moment the brain produces it, NEVER paced
by synthesis; the voice keeps its own pacing. (b) The history handed to the
model still records only what was actually SPOKEN when speech is on -- AC-64
and D-20 are unchanged, because telling the model it said text the room
never heard is the context-desync defect. (c) On a stop, a barge-in, a dead
segment or a look-away, text already shown and not yet spoken STAYS on
screen and is marked with the page's existing shown-not-heard treatment
(`.unspoken`, AC-40); the chat record keeps the full shown text with the
boundary, so a reload draws the same line. With speech OFF everything shown
is the answer and is recorded whole, which `_speak_audio`'s muted branch
already did. So D7 = A (a retractable marker) and D8 = A (no lookahead
bound). The losing options, in one sentence: D7 = C accepted a transcript
that claims sentences were said when they were not, D7 = B only narrowed
that window, and D8's lookahead of one (phase 1) still paced every sentence
past the second behind the voice -- measured below.

**Why the lookahead of one was not enough [MEASURED 2026-09-26].** Two
scratch daemons with the REAL kokoro fp32 pool of one, the same typed prompt
(five short sentences), under the same machine load: at HEAD (`90ebca5`,
phase 1) the five `reply` frames arrived at 20681, 20683, 23856, 28307 and
34025 ms -- the fifth sentence's text 13.3 s after the first, paced by
synthesis at 3-6 s a sentence. With the audio queue all five arrived within
212 ms (9850 -> 10062 ms) while the audio ran on to 33.5 s. An earlier pair
of runs disagreed on synthesis speed (0.6 s against 5 s a sentence); the
status line "Working on it." -- synthesized identically by both versions --
was slow by the same factor, so that was machine load, not the change.

**What shipped.**
* `hub._TurnVoice` is one turn's AUDIO LEG, a task beside `_reply`'s text
  loop. Jobs play strictly in order, one at a time (AC-285); `_speak_audio`
  is still the only writer of `s.spoken`. A sentence counts as HEARD only
  if `s.spoken` grew -- an engine abort ends a synthesis quietly.
* `abandon()` HALTS the turn's voice before it aborts the engine. Otherwise
  the aborted synthesis ends quietly and the queue starts the next
  sentence's synthesis in the gap (D-4); a test double whose abort ended the
  brain at once hid exactly that window, and mutation M8 found it.
* The look-away (AC-238) is re-checked when each queued sentence's audio
  would start, so sentences already shown as spoken are muted too (2.3
  item 4).
* A dead segment ends the VOICE for the rest of the turn -- nothing later
  is attempted, even a sentence the model produces afterwards -- and the
  error frame says so (plan.md 3.8: abandoned loudly, never skipped). The
  TEXT leg is not stopped, so a TTS crash no longer walks away from a coding
  agent mid-prompt; its child completes and is held (AC-59).
  `test_conversation.py`'s `..._still_disposes_the_child` became
  `..._ends_the_voice_and_not_the_agent` for that reason.
* `{type:"unspoken", turn, chat, from}` (plan.md 4.3) names the first
  unheard `reply` frame of the turn; `_send_reply` is the one sender of
  `reply` and keeps the per-turn count both sides index by. The page applies
  it by TURN (`applyUnspoken`, `rememberTurnItem`), bypassing `stale()`,
  because a stop closes the item before the frame lands.
* The record: `ChatStore.mark_unspoken` sets `unspokenFrom` on the turn's
  assistant message, refusing loudly unless that message was written during
  this turn and ends with the unheard tail. `renderMessages` draws it.

**Still open, stated rather than left to be found.** A stop that lands
before a harness agent has FINISHED still leaves no assistant message on
record at all -- the adapter records at its terminal event, and the
generator is closed first -- so a reload shows the question alone while the
live page showed the partial answer. That predates this work and is
unchanged. A sentence cut off PART-WAY is marked unheard whole, although
its first segment reached the room and `s.spoken` holds that segment.
`seed_note` (the rewind's transcript for a fresh child) reads `content`
whole, unheard tail included, which matches the agent's own memory rather
than the room's. Endpoint mode keeps no chat record, so only the live page
is marked there.

---

## 3. Decisions for the owner

Eight decisions: D1-D6 for item 1 (all that remain open after round 5),
D7-D8 for item 2 (unchanged from the earlier draft).

### D1 (item 1). Does starting a new chat during a project's IDLE GRACE WINDOW show the confirm, or dispose silently?

Section 1.6 settled that an idle-but-held child occupies its project's
slot for up to `project_idle_grace_minutes` (default 60) before
auto-releasing, and that BOTH the reap and the confirm-to-stop path
preserve the harness's own memory via the session file. This decision is
the one case round 3's confirm wording does not disambiguate: a
collision during that grace window, where the existing child is NOT busy
but is still occupying the slot.

* **A. No confirm; dispose the idle child and proceed.** Matches the
  confirm's literal wording ("STOP the current RUNNING agent") -- nothing
  is running. Section 1.6's own finding softens this option's cost
  considerably: the discarded child's memory is NOT lost (it resumes via
  the session file the next time that conversation is asked something),
  it only costs a 1.5-2.3 s respawn later.
* **B. Show the confirm anyway, reworded for the idle case.** Cost: a
  second wording to maintain, and a dialog for a case that may feel
  unnecessary.

**RECOMMENDED: A**, and more strongly than in an earlier draft of this
document, now that section 1.6 has established the "cost" of disposing an
idle child is a respawn delay rather than a memory loss.

### D2 (item 1). Is the per-project last-opened HIGHLIGHT worth building at all?

Section 1.3 already demoted this from a navigation mechanism (round 2) to
a decorative highlight (round 3).

* **A. Build it.** A row in a project's list is visually marked as "last
  opened." Cost: new daemon state (`_last_chat_for`) for a purely
  cosmetic effect.
* **B. Drop it.** Nothing marks a project's last-opened chat. Cost: none
  stated.

**RECOMMENDED: not chosen here.** A small, taste-driven call the owner
should make by looking at a screenshot of both versions.

### D3 (item 1). Does the spoken "work in project X" switch still re-file an EXISTING chat, or does "chat cannot move between projects" retire that too?

* **A. The spoken switch is the one standing exception.** `work_in` keeps
  calling `set_project` exactly as today.
* **B. The ruling is total.** `work_in` stops calling `set_project` for an
  already-born chat, splitting "where the agent runs" from "which project
  the chat belongs to" for the first time.

**RECOMMENDED: A.** Splitting those two facts (B's consequence) is a
materially bigger change than anything else in this document, invented to
satisfy a sentence that reads naturally as being about UI filing controls
in context.

### D4 (item 1). Does the confirm-to-stop mechanism (BUSY collisions only) get the GENERAL "stop any named conversation" capability, or a narrow one-off?

* **A. Build the general capability now.** Cost: real daemon work, but it
  also unblocks part of `docs/parallel-agents.md`'s own deferred phase 8.
* **B. Build a narrow, special-cased version** for just this gesture.

**RECOMMENDED: A**, on the grounds that B's "narrow" version still has to
solve the hard part to work at all.

### D5 (item 1). Is the per-project agent limit (and the idle-grace minutes) a single global number, or configurable per project?

* **A. One global number for each** (`project_agent_limit`,
  `project_idle_grace_minutes`). Simpler; matches `harness_agents_max`.
* **B. Per-project**, new fields on `ShortcutStore`'s `Shortcut`
  dataclass. Cost: real new surface for a capability the owner has
  explicitly said is a FUTURE possibility.

**RECOMMENDED: A**, on the owner's own words describing ONE number
changing, not a per-row table.

### D6 (item 1). Should `seed_note`'s newest-first trimming change, given it discards the ORIGINAL INSTRUCTION first?

Section 1.6's third consequence: on the fallback (seed) path only --
never on session-file resume -- `SEED_MAX_CHARS = 12000`'s newest-first
trim means a long rewound conversation's kept transcript loses its
OPENING instruction before it loses anything else.

* **A. Leave it.** This is the reference implementation's own rule
  (`claude-seed.js`), kept rather than re-derived, and the argument for
  it stands on its own terms: "the turns nearest the rewind point are the
  ones the next question depends on" (`dialogue.py`'s own comment).
* **B. Always keep the FIRST user turn, regardless of the trim,** on the
  theory that an original instruction is categorically more valuable than
  a middle turn, even at the cost of dropping something more recent
  instead.
* **C. Something else** -- e.g. keep both ends and trim the middle.

**RECOMMENDED: not chosen here.** This is `docs/rewind.md` AC-193's own
territory, not something this document should re-litigate as a side
effect of specifying the idle-grace reaper; it is included here only
because R11 raised the question and the owner should see it named rather
than silently left as-is.

### D7 (item 2). How does the page mark text that outran its own audio?

**RULED 2026-09-26: A**, and built -- section 2.5.

This only matters once the two send-legs in section 2.4 are decoupled
enough that a barge-in can land after text is shown but before (or while)
its audio plays.

* **A. Extend the existing `.unspoken` marker to be RETRACTABLE.** A new
  frame kind (or a field on `question_closed`/a new `retract` frame) tells
  the page "the reply frame you already rendered for segment N was never
  actually spoken; mark it `.unspoken` after the fact." Cost: a genuinely
  new mechanism -- today `.unspoken` is written forward once, never
  revised.
* **B. Never send `spoken=True` ahead of confirmed synthesis START** (a
  half-measure): text still queues ahead of a FULLY-SPOKEN commitment, but
  the `reply` frame's `spoken` field is only set true once that sentence's
  synthesis has begun, which narrows but does not close the race.
* **C. Accept the small window.** A barge-in has to land in the narrow gap
  between "text sent" and "that sentence's audio finishes," across
  potentially several queued sentences instead of within one. Cost: an
  honesty regression whose size is bounded by how many sentences are
  allowed to queue ahead (see D8).

**RECOMMENDED: not chosen here.** A is the only option that keeps the
transcript perfectly honest and it is real, new work; C is nearly free and
may be an acceptable trade for "text appears immediately," especially since
today's code ALREADY tolerates the single-sentence version of this same gap
(a mid-synthesis barge-in already can leave a sentence marked spoken=True
that was cut off, per `hub.py:2861-2867`'s comment about the case it
explicitly does NOT retroactively unmark).

### D8 (item 2). How many sentences may queue ahead of their own audio?

**RULED 2026-09-26: A**, and built -- section 2.5. The recommendation below
(B, a lookahead of one) shipped first as phase 1 and was measured still
pacing every sentence past the second.

* **A. Unbounded -- send text as fast as the LLM produces it.** Maximizes
  the "text appears immediately" feel; maximizes D7's exposure the most.
* **B. A small, fixed lookahead** (e.g. 1-2 sentences ahead of the one
  currently synthesizing).
* **C. No lookahead at all beyond what already exists inside one `Say`.**

**RECOMMENDED: B, with a lookahead of 1** (the next sentence, sent as soon
as it is available, synthesized only after the current one's audio has been
sent). This directly removes the paced-typewriter effect the owner
described without introducing an unbounded honesty gap.

---

## 4. Phased build order

Each phase ends in something that can be looked at or measured, names the
files it touches, and states what proves it. Item 1 and item 2 are
independent workstreams and may be built in either order or in parallel;
item 1's own phases are ordered surface-before-engine, following
`docs/parallel-agents.md` D1's own already-ratified pattern.

### Item 1, Phase 1 -- Navigation: list-only browsing, the root guard, the highlight

**Files:** `web/app.js` (`paintFilterHead`, `paintProjRail`),
`daemon/shortcuts.py` (`ShortcutStore.remove`, the path-edit path),
`daemon/butlerd.py` (no change expected -- the existing `ShortcutError`
-to-400 translation already covers the new refusal), `test/test_shortcuts.py`,
`test/manual/probe_page.py`.

Depends on D2 (build the highlight or not).

1. Remove the `file` button and its click handler from `paintFilterHead()`
   (`web/app.js:2231-2277`), leaving `name` and `clear`.
2. `paintProjRail`'s `tile.onclick` changes from `setChatScope(row.id,
   true)` to `setChatScope(row.id, false)` -- scope only, opens nothing.
3. `ShortcutStore` gains the root-row guard (section 1.8): `remove()` and
   the path-edit branch both refuse a request naming/re-pathing the row
   matching `app_dir`, via `same_dir`, raising a `ShortcutError`.
4. If D2 rules "build it": `ShortcutStore` gains `_last_chat_for: dict[str,
   str]` (project row id -> chat id), following `_chat_grouping`'s exact
   persisted-dict shape, GLOBAL per round 3; written whenever `openChat` or
   `newConversation` runs while a project scope is active; read back in
   `GET /api/shortcuts`; the chat list's row renderer marks the matching
   row.

*Proves it:* a scratch-daemon browser check -- scope to project X, open an
old chat, scope to project Y, confirm the LIVE conversation and the page's
current view are both untouched by the scope changes alone. A check that
deleting or re-pathing the root row's shortcut is refused with a stated
reason, and a mutation test (D-13) confirming the guard actually fires
(remove the `same_dir` check, watch the refusal test fail, restore).
`probe_page.py`'s existing filing check (added in 8720f02 for "file here")
is retired or rewritten to assert the button no longer exists AND that a
bare tile click opens nothing.

### Item 1, Phase 2 -- No unprojected conversations

**Files:** `daemon/chats.py` (a new migration method, following
`migrate_projects`'s shape), `daemon/butlerd.py` or wherever boot-time
migrations are already invoked, `web/app.js` (`newConversation`),
`test/test_chats.py`.

Depends on D3's answer only insofar as it decides whether `work_in` needs
a matching change in the SAME phase (if D3=B) or not at all (if D3=A).

1. `newConversation()` files into the root project's row id when
   `chatScope === null` (chats created from "All chats"), rather than
   skipping the filing call.
2. A one-time migration pass, run at boot after `ShortcutStore.
   migrate_projects`, sets `project = app_dir` on every existing chat
   record whose `project` is the empty string, with a persisted flag for
   auditability.
3. If D3=B: `work_in` stops calling `set_project` for a chat that already
   has a non-empty `project`; if D3=A (recommended): no change to
   `work_in` at all.

*Proves it:* a scratch-daemon check with a hand-seeded chat record
carrying `project: ""`: after boot, its `project` reads `app_dir`'s path.
A fresh chat started from the unscoped view is asserted filed under the
root row the moment it is created, never transiently empty.

### Item 1, Phase 3 -- One live child per project, designed for N, with the idle-grace reaper

**Files:** `daemon/settings.py` (two new entries in the `"harness"`
group), `daemon/adapters/brain_harness.py` (`_Host._idle_since`,
`plan()`, `cap_refusal()`), `test/test_parallel_agents.py` or a new
`test/test_project_exclusivity.py`.

**This is the engine half.** Depends on D5 (global vs. per-project
numbers).

1. Two new settings: `project_agent_limit` (default 1, minimum 1) and
   `project_idle_grace_minutes` (default 60, minimum 0 -- 0 meaning
   "release the instant a turn ends," stage 2's behaviour, folded into
   stage 3 as its own zero case). Neither restart-flagged.
2. `_Host` gains `_idle_since: dict[str, float]`, set at
   `HarnessConverse._answer`/`._give_up` (the two places a turn ends),
   cleared when that chat's child is reused for a new turn.
3. `_Host.plan(chat)` gains a count-versus-limit check, run before
   today's cap check: count entries in `self._children` whose
   `held.spec.cwd` is `same_dir`-equal to `self.desired_for(chat).cwd`,
   EXCLUDING any whose `_idle_since` is older than
   `project_idle_grace_minutes` (disposing those via `_Host.dispose()` --
   no abort needed -- as part of this same lazy check), and whose key is
   not `chat["id"]`. Compare the remaining count against
   `project_agent_limit()`. At the limit with a BUSY match, this is where
   the CONFIRM (Phase 4) is consulted; at the limit with only an
   idle-but-not-yet-expired match, D1's ruling applies.
4. `cap_refusal()`'s per-project accounting states project NAMES when the
   limit is 1.
5. Mutation-validated tests (D-13): two DIFFERENT chat ids, same project
   directory, second attempted while the first still holds a live child
   -- confirm the collision is DETECTED; a second test ages an entry's
   `_idle_since` past the grace period and confirms it is excluded from
   the count and disposed; a THIRD test (this document's own risk #2,
   section 7) confirms `_idle_since` is set ONLY at turn end, never on a
   socket disconnect that a survivable turn rides out (`017f8a1`).

*Proves it:* against the fake harness -- two conversations, same `cwd`,
the project-collision count reads 1 the moment the second is attempted; a
test simulates the grace period elapsing (by directly setting
`_idle_since` into the past, not by sleeping the real duration) and
confirms the slot is free again with no owner action.

### Item 1, Phase 4 -- Confirm both memory guarantees BEFORE building the stop mechanism (a verification-only micro-phase)

**Files:** a new small test module, e.g.
`test/test_project_reap_preserves_memory.py`; no product code.

**This phase exists because section 1.6's memory claim is the single
most consequential finding in this document and has not been freshly
measured under the NEW trigger (idle timeout), only reasoned from a 2026-
08-19 measurement taken under a different one (per-message dispose).**
Before building the confirm-to-stop mechanism on the assumption that
disposal is safe, confirm it against a REAL pi child (not the fake
harness, which cannot prove anything about pi's own file format):

1. Spawn a real pi child for a throwaway conversation, in `harness_only`
   mode against a scratch daemon, in a scratch directory.
2. Send it a message establishing a fact (e.g., "remember the number
   47"). Confirm the reply.
3. Call `_Host.dispose(chat_id)` directly (simulating what the idle-grace
   reaper and the confirm-to-stop mechanism both do).
4. Confirm the session file on disk is unchanged in size/mtime-relevant
   content by the dispose call itself (it should be untouched, per
   section 1.6's reading of `_close_child`).
5. Spawn a FRESH pi child pointed at the SAME session file. Ask it "what
   number did I ask you to remember?" Confirm the answer is 47.

*Proves it:* step 5's answer is correct, which is the freshly-measured
confirmation section 1.6's risk #1 (section 7) says is still owed. If
this fails, EVERYTHING in sections 1.6-1.8 about the idle-grace reaper
being "free" (a pause, not a loss) is wrong and must be revisited before
Phase 3 ships to real users, not after.

### Item 1, Phase 5 -- The confirm-to-stop mechanism, for busy collisions

**Files:** `daemon/hub.py` (a new named-conversation-abort capability),
`daemon/adapters/brain_harness.py` (a caller for it), `web/index.html`/
`web/app.js` (the confirm dialog), `test/test_hub_bargein.py` or a new
`test/test_project_stop.py`.

**Depends on Phase 3 and Phase 4, and on D1, D4.** This is the largest
single piece of new work in item 1, per section 1.7's finding that no
general "stop a named conversation" capability exists today.

1. Per D4: either the general `Hub`-level named-conversation stop (D4=A,
   recommended) or the narrow special-case (D4=B) is built. Either way:
   find whether the colliding project's chat has a turn in flight; if so,
   reach its owning session and run the SAME abort-then-dispose sequence
   `abandon()` already uses, scoped to that one conversation. If the
   collision is with an idle-but-not-yet-expired child, per D1's ruling
   either skip the confirm and dispose directly (D1=A, recommended) or
   still confirm with reworded text (D1=B).
2. The page's "start a new chat here" action, when Phase 3's collision
   check reports a BUSY hit, shows the confirm dialog with the owner's own
   suggested wording before calling the route at all -- this is a
   CLIENT-SIDE gate, not `#askCard`'s daemon-tracked question mechanism.
3. **Declining CANCELS THE WHOLE GESTURE, per round 4 -- no branch, no
   option.** No route is called; no chat is created; nothing is stopped.
4. Confirming waits for the stop to complete before the new chat's own
   `ensure()` runs, so the two children are never briefly both alive.

*Proves it:* a scratch-daemon browser check -- project X has chat A mid-turn
(a slow fake harness call that stays open); start a new chat in project X;
confirm the dialog appears with the owner's own wording; confirming stops
chat A's turn cleanly (its own terminal event still fires, matching
`abort()`'s documented 1.51 s settle) and only then spawns chat B's
agent; declining leaves chat A completely undisturbed AND creates no chat
B at all, asserted as a NEGATIVE (no new record on disk, no route called).

### Item 1, Phase 6 -- DOX pass

**Files:** `daemon/AGENTS.md`, `web/AGENTS.md`, `daemon/chats.py` (the
comment this overturns), root `AGENTS.md`, `CLAUDE.md`.

Update `daemon/AGENTS.md`'s `_Host`/cap bullets to describe the
per-project invariant, the three-stage cap-counting progression, the
idle-grace reaper, the resume-vs-seed distinction, and the
confirm-to-stop mechanism; update `web/AGENTS.md`'s filter-head bullet to
remove the "file here" description and add list-only navigation and (if
built) the highlight; update `daemon/chats.py`'s own "deliberately NOT
attributed" comment to record that this is OVERTURNED (AC-97's docstring
pattern) rather than deleting the argument; update the root `AGENTS.md`
spec index with this file's line; update `CLAUDE.md`'s R4 entry to
record that it is superseded by this document's stage 3.

### Item 2, Phase 1 -- Split `_speak`'s two legs

**Files:** `daemon/hub.py` (`_speak`, `_reply`), `web/app.js`
(`logReply`'s `.unspoken` handling if D7=A), `test/test_conversation.py`
or a new `test/test_speak_pacing.py`.

**Ships:** text frames arrive without waiting for the PRIOR sentence's audio
to finish sending, at a lookahead of 1 (D8/B), with the honesty gap this
opens handled per D7's ruling.

1. `_speak` (or a new sibling method) is split into a TEXT leg (sends the
   `reply` frame, appends nothing to `s.spoken` yet) and an AUDIO leg
   (synthesizes, streams, and is what actually appends to `s.spoken` on
   success). `_reply`'s loop drives the text leg for `Say` N+1 as soon as
   it is available, while the audio leg for `Say` N is still running,
   subject to the pool's own single-worker serialization -- so audio
   ordering and pacing are UNCHANGED, only the text leg's timing moves
   earlier.
2. A one-slot queue (D8/B) holds at most one "text sent, audio not yet
   started" `Say` at a time; `_reply` blocks requesting `Say` N+2's text
   until `Say` N's audio leg has at least STARTED, preventing an unbounded
   race for a very long reply.
3. `handle=turn` on `self.tts.synthesize(...)` is confirmed to still let
   `abort()` cancel a QUEUED (not yet started) audio leg, not only one
   already streaming -- this is new coverage the current single-leg design
   never needed, because today nothing is ever queued.
4. D7's ruling is implemented: either the retract mechanism (D7/A) or the
   accepted-gap statement in the docs and in `.unspoken`'s own comment
   (D7/C), whichever the owner chose.

*Proves it:* a scratch-daemon browser check with a fake TTS engine slowed
down artificially (or the existing `harness_fake`/`brain_fake` pattern
extended) that measures TIME BETWEEN a sentence's text arriving on the
wire and the PRIOR sentence's audio finishing -- today that gap is ~0
(text can only arrive after prior audio is fully sent); after this phase,
text for sentence N+1 should arrive as soon as the model produced it,
decoupled from audio completion, MEASURED with real timestamps rather
than asserted. A barge-in test that lands between a queued sentence's
text-send and its audio-start, confirming (per D7's ruling) either a
retraction frame arrives or the accepted gap is what the transcript
shows -- named explicitly so the test proves the CHOSEN behaviour, not an
assumption about it.

### Item 2, Phase 2 -- DOX pass

**Files:** `daemon/AGENTS.md`, `web/AGENTS.md`.

Update `daemon/AGENTS.md`'s `_speak` bullet and `web/AGENTS.md`'s
`.unspoken`/AC-238 bullets to describe the two-leg design.

---

## 5. Proposed criteria

AC-271 onward, item 1 first (AC-271..283), item 2 second (AC-284..286).
Each states how it fails and whether that failure is recoverable.

- **AC-271 (a chat's project is fixed at birth, per the D3 ruling).**
  Whichever of D3/A or D3/B the owner rules, no UI control ever changes an
  existing chat's `project` field, and the resolved behaviour of the
  spoken "work in project X" switch matches D3's answer exactly.
  *Plain:* filing is a birth event, and whether the one grandfathered
  voice mechanism is an exception is written down rather than assumed.
  *Fails as:* a stray "file here"-shaped control reappearing under a new
  name -- OR `work_in` quietly keeps re-filing an existing chat while the
  docs claim it does not. **Recoverable** -- a UI deletion and/or a
  one-branch code change.
- **AC-272 (clicking a project lists its chats and changes nothing
  else).** Scoping the chat list to a project spawns no process, opens no
  conversation, and moves no filing -- only clicking an existing chat row
  or starting a new one changes what is displayed (round 3, upholding
  AC-229/AC-240 rather than amending them).
  *Plain:* browsing is free; only committing to a chat is not.
  *Fails as:* `295bf85`'s exact failure returning under a new trigger.
  **Recoverable.**
- **AC-273 (the project's last-opened chat is a highlight only, global,
  never a navigation event, if built at all per D2).**
  *Plain:* if a "last opened" mark exists, it decorates a row; it never
  drives.
  *Fails as:* round 2's own now-superseded behaviour silently returning
  through the highlight mechanism. **Recoverable.**
- **AC-274 (starting a new chat where a project already holds a BUSY
  child raises the confirm, and declining CANCELS THE WHOLE GESTURE with
  no exception).**
  *Plain:* stopping another agent to start a new chat is always something
  the owner explicitly agreed to in full, never a silent or partial side
  effect.
  *Fails as:* a coding agent mid-edit disposed with no warning, or a new
  chat created anyway after the owner said no. **NOT recoverable** if it
  disposes a child that was mid-write.
- **AC-275 (an idle-but-held child occupies its project's slot for a
  bounded, settings-adjustable grace period, then releases
  automatically, via a LAZY check rather than a background timer).**
  *Plain:* an idle agent's memory is kept for a while in case the owner
  comes back, then let go on its own, with no task the daemon has to
  remember to cancel.
  *Fails as:* a slot held forever if the release never fires, or a
  background timer that leaks and violates D-1. **Recoverable** for the
  slot mechanism itself.
- **AC-276 (releasing an idle child's slot, or stopping a busy one via
  confirm, NEVER removes or invalidates its session file, so the next
  spawn for that conversation RESUMES rather than starts fresh).**
  `HarnessPi.dispose()`/`_close_child()` act only on the process; the
  session file is untouched by either disposal path this document specs.
  *Plain:* the agent forgets nothing permanently from a reap or a
  confirmed stop; it just has to reload, once, the next time it is asked
  to do something.
  *Fails as:* the owner discovering an agent genuinely forgot prior
  context after a reap or a confirmed stop, which would mean the
  `plan.md:467-471` measurement (taken under a different trigger) does
  not transfer to these new triggers as reasoned. **NOT recoverable** if
  actual context loss occurs and the owner has already acted on an
  answer that no longer reflects what the agent remembers.
- **AC-277 (a session-file resume and a transcript seed are never
  confused, and the codebase's own criteria say which applies at every
  restore point).** A conversation whose session reference still exists
  (the idle-grace reap and the busy-confirm stop both preserve it) always
  resumes via the file; a conversation whose reference was explicitly
  cleared (a rewind, or the harness's own dead-session report) is the
  ONLY case that falls back to `seed_note`.
  *Plain:* the better restore is used whenever it is available, and the
  weaker one only when it has to be.
  *Fails as:* a session-file resume silently downgraded to a transcript
  seed because something in this document's new code accidentally clears
  a reference it should not touch -- the seed path's own sharp edges
  (AC-278, `docs/rewind.md`'s mirror finding) becoming the NORMAL case
  instead of the rare one. **NOT recoverable** if it happens silently
  and the owner never learns the agent's memory degraded.
- **AC-278 (the seed path's newest-first trimming behaviour is stated
  plainly, and D6's ruling on whether to change it is followed exactly).**
  *Plain:* what a rewound conversation's agent remembers, and what it is
  most likely to have forgotten, is documented rather than discovered by
  the owner the hard way.
  *Fails as:* an owner surprised that the agent forgot the ORIGINAL
  instruction of a long-rewound conversation, with nothing in the docs
  saying that was always going to be the first thing trimmed.
  **Recoverable** -- it is a trimming rule, changeable per D6.
- **AC-279 (the root ("butler") project cannot be deleted or re-pathed,
  enforced in `ShortcutStore` itself).**
  *Plain:* "Project butler" is always there, enforced by the daemon that
  owns the data, not merely intended by convention.
  *Fails as:* deleting or silently re-pathing the one guaranteed fallback
  destination for "no unprojected conversations" and for chats born on
  "All chats," breaking both at once. **Recoverable** as a data matter
  for deletion; **NOT recoverable** for anything keyed to the old id, or
  for a silent re-path that goes unnoticed for a while.
- **AC-280 ("All chats" survives as a genuine, unaffected, read-only-for-
  browsing flat list).**
  *Plain:* nothing about "always project->chat" removes the ability to see
  everything at once.
  *Fails as:* the owner's explicit "unscoped all chats view can survive"
  being quietly retired. **Recoverable.**
- **AC-281 (no conversation is ever left unprojected, including the
  migration of existing ones).**
  *Plain:* every conversation belongs somewhere, always.
  *Fails as:* a transiently-empty `project` field observed by any reader
  between creation and filing, or an existing unprojected chat left
  unmigrated after boot. **NOT recoverable** for the "invented fact" risk
  this overturns if the attribution is later found wrong; the migration
  itself is idempotent and safe to re-run.
- **AC-282 (at most `project_agent_limit` live-or-within-grace children
  exist per project, and raising that number later needs no
  redesign).**
  *Plain:* "one for now" is a number, not an architecture.
  *Fails as:* the owner raising the limit to 2 later and discovering it
  requires re-keying `_Host._children` or rewriting `plan()`'s structure.
  **Recoverable**, but expensive to discover late.
- **AC-283 (a colliding project's count-versus-limit check runs BEFORE
  the numeric cap check, and the two refusals are never confused).**
  *Plain:* the reason Butler gives for refusing (or confirming) matches
  the actual reason.
  *Fails as:* an owner told "10 of 10 agents are running" when the real
  issue is a project already running its one allowed agent at a global
  count of 2. **Recoverable.**
- **AC-284 (a reply's text frame is not gated on the PRIOR sentence's
  audio).**
  *Plain:* the transcript keeps up with the model, not with the voice.
  *Fails as:* the owner's literal complaint, unfixed. **Recoverable.**
- **AC-285 (audio pacing and ordering are byte-identical to today).**
  *Plain:* the voice sounds exactly as it does now; only the page's text
  arrives sooner.
  *Fails as:* a regression reopening D-20. **NOT recoverable** in the
  sense that a corrupted context can produce a wrong answer that is then
  acted on.
- **AC-286 (a barge-in during the queued gap resolves exactly as D7
  ruled).**
  *Plain:* what the transcript is allowed to claim was said is a decision,
  not an accident of scheduling.
  *Fails as:* an untested assumption about what the queue does under
  barge-in. **Recoverable**, but only if it is decided before Item 2
  Phase 1 ships rather than discovered after.

---

## 6. Gherkin, for the new criteria

```gherkin
Scenario: AC-271 a chat's project is fixed at birth
  Given a conversation filed in project A at creation
  When any UI control on the page is used
  Then no control changes that conversation's recorded project
  And the spoken "work in project X" switch behaves exactly as D3 ruled

Scenario: AC-272 browsing a project changes nothing but the list
  Given the owner is looking at project A's live, running chat
  When the owner clicks project B's tile
  Then project B's chats are listed
  And project A's chat is still the one displayed and still running

Scenario: AC-274 starting a new chat where an agent is already running
  Given chat A in project X holds a live, busy coding-agent child
  When the owner starts a new chat while scoped to project X
  Then a confirm is shown naming the running agent
  When the owner confirms
  Then chat A's turn is stopped gracefully and chat A's slot is freed
  And only then does the new chat's own agent start

Scenario: AC-274 declining the confirm cancels the whole gesture
  Given the same setup
  When the owner declines the confirm
  Then chat A's agent is completely undisturbed
  And no new chat record exists anywhere on disk
  And no route was called to start one

Scenario: AC-275 an idle child releases its slot after the grace period
  Given chat A's child in project X went idle 61 minutes ago
  And the grace period is set to 60 minutes
  When any turn anywhere triggers a plan() check for project X
  Then chat A's child is disposed
  And project X's slot is free

Scenario: AC-276 a reaped conversation's session file survives disposal
  Given chat A's child is disposed, either by the idle-grace reaper or
      by a confirmed stop
  When the session file on disk is inspected immediately afterward
  Then it exists and is unchanged from before the disposal

Scenario: AC-277 a reaped conversation resumes rather than seeds
  Given chat A's child was reaped after its idle grace expired
  When the owner asks chat A something new
  Then a fresh child is spawned pointed at chat A's own session file
  And ChatStore.needs_seed() answers false for chat A
  And no seed_note transcript is injected into the prompt

Scenario: AC-279 the root project cannot be deleted or renamed
  Given the migrated root row for Butler's own app directory
  When a delete or a re-path request names that row
  Then the request is refused with a stated reason
  And the row's path is unchanged afterward

Scenario: AC-280 All chats survives unaffected
  Given conversations across five different projects
  When the owner opens the unscoped All Chats view
  Then every conversation is listed regardless of its project
  And opening one from there behaves exactly as opening it from its own
      project's scoped list would

Scenario: AC-281 no conversation is ever left unprojected
  Given a fresh boot with a legacy chat record carrying an empty project field
  When the boot migration runs
  Then that record's project is the root project's path
  And a brand-new chat started from the unscoped view is filed under the
      root project the moment it is created

Scenario: AC-282 the per-project limit is a number, not a redesign
  Given the per-project limit is raised from 1 to 2 in settings
  When two different chats in the same project both hold live children
  Then neither is refused or disposed
  And no code path required a structural change to allow it

Scenario: AC-284 text is not gated on the prior sentence's audio
  Given a reply of three sentences
  When the second sentence's audio is still being synthesized
  Then the third sentence's text has already arrived on the wire

Scenario: AC-285 audio pacing is unchanged
  Given a reply of three sentences
  When it is played back
  Then the three segments are synthesized and sent in order, one at a time
  And the router's rolling history is populated only from segments whose
      audio was actually sent

Scenario: AC-286 barge-in during the queued gap
  Given sentence two's text has been sent and its audio has not yet started
  When the owner barges in
  Then the transcript reflects the ruled behaviour for that gap (D7)
  And the router's history does not include sentence two
```

---

## 7. What this spec could have wrong

Fourteen, worst first. The first eight are new to this fifth round; the
rest carry over from earlier drafts.

**1. The memory-preservation claim (AC-276, AC-277) rests on a
MEASUREMENT taken under a different trigger than the one this document
specs, and Item 1 Phase 4 exists specifically to close this gap before
Phase 5 is built on top of it.** `plan.md:467-471`'s "pi DOES resume from
a session file across separate children" was measured under AC-46's
per-MESSAGE dispose-then-respawn, not an idle-timeout dispose. The
underlying mechanism is unchanged (same file, same `--session` flag), so
the reasoning transfers -- but Phase 4 is where this document insists on
a fresh measurement before trusting it for real.

**2. The idle-grace reaper's lazy-check design means a project's slot can
sit occupied by an EXPIRED-but-not-yet-checked idle child indefinitely if
nothing anywhere ever calls `plan()` again for any project.** This is not
a correctness bug, but it means the numeric cap's live count can be
STALE at any given instant -- `docs/triage-list.md`'s own status-reporting
work should be checked against this before it ships anything that reads
cap occupancy directly rather than through `plan()`.

**3. D1's recommendation (no confirm during the idle grace window) leans
harder on the memory-preservation finding than an earlier draft did, and
if that finding turns out wrong under Phase 4's fresh measurement, D1's
whole argument weakens with it** -- the two are coupled in a way this
document states explicitly rather than treating as independent.

**4. Section 1.7's confirm-to-stop mechanism (D4) is the single riskiest
piece of NEW (not merely re-verified) work in this document, and it was
reasoned from reading the deferred `docs/parallel-agents.md` phase 8's
own account of what it would take, not from prototyping the cross-session
abort path against a real fake harness.**

**5. D3 (does `work_in` still re-file an existing chat) may be the single
most consequential open question in this document, and it was resolved
here by INFERENCE from sentence order and context, not by asking the
owner directly.**

**6. The root-row enforcement (section 1.8, AC-279) was specified as
living in `ShortcutStore` and reusing `_chord()`'s translated-exception
pattern, but the PATH-EDIT route this must also guard was located by
name (`daemon/butlerd.py`) rather than read line by line the way
`remove()` was.**

**7. R7/R8's "design for N" instruction was applied to
`plan()`/`live()`/`ensure()`/`cap_refusal()`, but `HarnessSpec` and the
session-file-per-chat model (`session_ref`) were NOT checked against what
N>1 in one project would require** -- two chats sharing one project would
need two DIFFERENT session files in the SAME directory, which is likely
fine but was not traced against `session_ref`'s per-harness naming scheme
to confirm no assumption there also bakes in "1".

**8. D6 (seed_note's trimming rule) was raised as a decision but the
document did not check whether the owner's round 5 sentence ("load the
saved conversation up to that point") implies an opinion about it --
"up to that point" could be read as wanting the FULL kept transcript
regardless of the 12000-character cap, which this document did not treat
as a live possibility.**

**9. The migration in section 1.4/Phase 2 assumes `ChatStore._records()`
is safe to run a full pass over at boot, appending a write to every
matching record.** This was reasoned from `migrate_projects`'s own
precedent rather than measured against a large `state/chats/` directory.

**10. This document assumes the confirm dialog is a plain client-side gate
and explicitly rejects reusing `#askCard`'s protocol -- but it did not
check whether the EXISTING `#askCard` CSS/interaction pattern should be
visually reused even if the wire mechanism is not.**

**11. Item 1's remaining D2/D5 recommendations may not be what the owner
meant, and this document explicitly refuses to guess past the
recommended (lowest-risk) reading on the ones that are genuinely
ambiguous.**

**12. The `chats.py` persistence finding (section 2.2b) was read, not
executed.** The claim that `_Host.record()` is unconditionally independent
of `s.spoken` is based on reading `brain_harness.py:1203, 1449, 1503, 1659`
and tracing the call graph by eye, not run against a fake harness and a
fake TTS engine that deliberately fails mid-synthesis.

**13. The kokoro thread-count claim in the original brief ("4 threads")
was not independently confirmed and this document deliberately does not
repeat it as fact.** The pool-size-1 finding is solid ([READ], quoted
verbatim); the per-worker thread count was not chased down.

**14. D8's recommended lookahead of 1 is a guess, not a measurement, and
this document did not check whether `speech.drain_sentences`'s buffering
already introduces its own latency independent of `_speak`.** Separately,
item 2's fix was not checked against every other `_speak` call site
(reflex lines, `AskConfirm`'s spoken refusal, the harness's fixed
pre-spawn narration, `WorkerEvent` handling) one by one; those are
believed unaffected but this was reasoned, not driven and confirmed.

---

## 8. Addendum, 2026-09-26: a chat BELONGS to a project, and the sidebar is a project tree

The owner reviewed the reference implementation Orca
(`github.com/stablyai/orca`, cloned at `E:\PROJECTS\other-project`) and ruled four
things on 2026-09-26. In Orca an agent session carries an immutable
`worktreeId` set in the one call that creates it, its working directory is
derived from that at spawn, no code path re-parents a session, clicking a
project only expands it, and the "project" palette command navigates and never
mutates. The rulings copy that model.

**BUILD STATUS: BUILT 2026-09-26, uncommitted at the time of writing.**
Evidence is in `test/AGENTS.md` (`test_chat_belongs_to_project.py`, its
mutation runs, and the three browser instruments); the criteria below stay
here until the owner accepts them.

**R-A. A chat belongs to a project; the link is set ONCE, in the call that
creates the chat, and nothing can change it afterwards.** A move is made
UNREPRESENTABLE rather than guarded: `POST /api/session/new` takes the
project, and `POST /api/project`, `Hub.switch_project`, `_Host.work_in` and
`ChatStore.set_project` are DELETED. The page's two-step birth
(`newConversation` then `postProject`) becomes one call.

**R-B. The coding agent's working directory is DERIVED from the chat's
project at spawn.** `_Host._cwd`, the in-memory "where the spoken switch put
this session", is deleted; `_session_cwd(chat)` reads the record's `project`
and nothing else. AC-31's gates stay: a directory must be a configured row,
resolved by enumerating rows, never taken from a caller. The per-project
concurrency rules (`project_agent_limit`, the idle grace, the busy-project
confirm on New chat) are unchanged.

**R-C. "work in X" becomes NAVIGATION.** It opens X's most recent chat, or
starts a new chat born in X when it has none. It never moves a chat and never
stops a running agent. It reuses the page's own open and new code paths
(D-18), and the device that spoke ends up SHOWING that chat. Its refusals
(unknown name, ambiguous name) are unchanged.

**R-D. The sidebar becomes a PROJECT TREE**, replacing the project rail's
tiles, the filtered flat list, the filter head and "All chats". Clicking a
project row ONLY expands or collapses it; clicking a chat opens it; each
project has "+ New chat" (born there, behind the existing busy-project
confirm); a busy dot on a running chat rolls up to its project row; the
project's controls stay behind the row's "..." menu; search stays global and
its results name their project; the expanded set persists in the DAEMON's
store; the header names the current chat's project. "Keep it visually easy":
quiet rows, no heavy boxes.

**WHAT THIS OVERTURNS, recorded so it is not re-litigated.** Round 3's R4
("All chats survives as a flat list", AC-280) is OVERTURNED: the tree shows
every chat under its project, and a flat view would be a second list of the
same rows. Round 3's R1 ("clicking a project LISTS its chats", AC-272) is
AMENDED rather than reversed: a click still changes nothing but the view, and
the view is now the project expanding IN PLACE instead of a filter over one
list. D3 (section 3) is ANSWERED B, against this document's own
recommendation of A: the spoken switch no longer re-files anything, and
"where the agent runs" is derived from "which project the chat belongs to"
instead of being a second fact that could disagree with it. `chats.py`'s
module note that `project` "is written by `work_in` and nowhere else" becomes
"written at birth and nowhere else".

### 8.1 Decisions made in this build (the owner should confirm)

- **D-A1, a birth that names no project goes to the ROOT row.**
  `POST /api/session/new` with no body births in Butler, because R4 already
  ruled that a chat created with no project context belongs to Butler, and
  the root row is the one project guaranteed to exist (AC-279). An unknown
  row id is a 400 naming it. The SPOKEN "new conversation" is the one caller
  that knows a context: it is born in the project of the conversation that
  device is on, falling back to the root when that project's row was removed.
- **D-A2, a birth runs AC-31's gate.** Birth checks the directory against the
  spawn resolver and `is_dir()` BEFORE minting -- the check `work_in` used to
  run at the move -- so a chat can never be born where no agent can start.
- **D-C1, "go to X" joins "work in X".** "go to project X" is explicit; bare
  "go to X" navigates only when X names a configured project exactly and is
  otherwise an ordinary utterance, the bare rule "work in X" already has.
- **D-C2, the daemon TELLS the speaking device** with one new outbound frame,
  `{"type": "opened", "chat": "chat-..."}`, and sets that socket's `showing`
  itself. Without it the page kept showing the old conversation and, because
  `chat_for` prefers what a device says it is showing, the NEXT spoken turn
  went back into the old chat. The spoken "new conversation" had that same
  latent defect and now sends the same frame. Other devices are not told,
  which is the staleness `ChatStore.summary()` already documents.
- **D-D1, the tree is GROUPING, not filtering.** The page asks for the whole
  listing once and draws every served row under exactly one project; a
  collapsed project hides its rows and keeps their count on its heading. A
  conversation whose project row was removed is drawn under its directory's
  name, struck through, at the end of the tree (AC-225). The daemon's
  `?project=` scope still exists; the page no longer sends it.
- **D-D2, the expanded set is `expanded_projects` in `ShortcutStore`**, beside
  `sidebar_collapsed`. Never written, it answers `[root_id]`, so a fresh
  install opens on Butler's own chats. Opening or starting a chat inside a
  collapsed project expands it, so the conversation on screen is never hidden
  in its own sidebar.
- **D-D3, the narrow rail survives as a 56 px strip** holding New chat (born
  in the current chat's project), the list toggle and Settings. With the list
  COLLAPSED on a desk, and always on a phone, it also shows each project's
  two-letter chip with the rolled-up busy dot; pressing one shows the list
  (the drawer, on a phone) with that project expanded -- a view change, never
  a navigation. With the list open on a desk the chips are hidden, because
  the tree already names every project. `rail_collapsed` and `chat_grouping`
  are RETIRED on the page and kept as inert store keys (`open`'s precedent).
- **D-D4, a tree chat row is ONE line** (the title), plus a status line only
  while the daemon reports that conversation running, waiting or errored.
  Search results keep the two-line row with the project name and age,
  because there the project IS the information.

### 8.2 Criteria (AC-287 onward; they stay here until the owner accepts them)

- **AC-287 (birth names the project, in one call).** `POST /api/session/new`
  with `{"project": <row id>}` mints a record whose `project` is that row's
  directory before the response is written; absent births in the root row; an
  unknown id is a 400 and mints nothing. *Fails as:* a chat briefly
  unprojected, or born in the previous chat's project. **Recoverable.**
- **AC-288 (a move is unrepresentable).** No route, Hub method, brain method
  or `ChatStore` method rewrites an existing record's `project`; the only
  writers are the birth (`rotate`), the rewind's copy (the birth of a copy)
  and the one-time filing of blank legacy records. `POST /api/project` is
  gone. *Fails as:* a chat silently re-filed. **Recoverable** as data, NOT for
  an agent that already ran in the wrong repository.
- **AC-289 (cwd is derived at spawn).** A spawned child's `cwd` is the
  directory of the project the TURN's conversation records, including after
  switching between conversations of different projects in either order.
  *Fails as:* an agent answering from the previous conversation's repository.
  **NOT recoverable** once it has written there.
- **AC-290 ("work in X" / "go to X" navigates).** It opens X's most recent
  conversation, or births one in X when it has none; it changes no record's
  `project` and stops nothing; the speaking device receives `opened` and
  shows that conversation. Unknown and ambiguous names are refused exactly as
  before. **Recoverable.**
- **AC-291 (the tree).** Every listed conversation is drawn under exactly one
  project node; clicking a project node toggles it and writes only the
  expanded set; "+ New chat" births in that project behind the busy confirm;
  the expanded set survives a reload; no "All chats" control exists.
  **Recoverable.**
- **AC-292 (the header names the project).** The header shows the current
  conversation's project, read from the daemon's answer and never from a
  click. **Recoverable.**

```gherkin
Scenario: AC-288 no path re-files a chat
  Given a chat born in project A
  When any route, spoken phrase or click is used
  Then that chat's recorded project is still A

Scenario: AC-289 the agent follows the conversation, not the last move
  Given chat 1 in project A and chat 2 in project B
  When the owner opens chat 2 and sends, then opens chat 1 and sends
  Then the first spawn ran in B and the second in A

Scenario: AC-290 "work in B" navigates
  Given project B holds chats, the newest being chat 2
  When the owner says "work in B"
  Then chat 2 is the conversation shown on the speaking device
  And no conversation's project changed
```
