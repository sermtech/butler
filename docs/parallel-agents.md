# Ten agents on one rail -- building the o2 shape into Butler

The owner's ask of 2026-09-11: "spec and plan actual implementation of this
mock into our butler app". The mock is `web/mocks/o2-filter.html`, chosen from
three variations on the rail layout ("this one is easier to see at a glance"),
and extended the same day so the conversation list HIDES while the rail always
stays.

This is a PROPOSAL. Its criteria are AC-228..AC-250 and they stay here until
the owner accepts them; `plan.md` is still the contract and was not touched.
**PHASES 1 THROUGH 5 ARE BUILT, AND PHASE 6 IS COMPLETE ON THE DAEMON SIDE**
(phases 1-2 on 2026-09-11, phases 3-6a on 2026-09-12, phase 6b on
2026-09-16); only the page half of phase 6 and phases 7-9 remain, and
section 5's status block says why. **The page half of phase 6 was BUILT
2026-09-26** after the e2e audit (`docs/e2e-journeys.md`) measured it
missing: turn state keyed by chat, frames routed by `chat`, `stop` naming
the chat (`web/AGENTS.md`); the "STILL OPEN" paragraph below is the state
before that.

**ALL SEVEN DECISIONS IN SECTION 2 ARE RULED, 2026-09-11**, in the owner's own
words, and each one carries them. Two rulings changed criteria rather than
confirming them -- the voice follows what you are LOOKING at (D4, AC-238) and
there is ONE stop control rather than a per-row one (D5, AC-243) -- and one
ruling was a QUESTION the spec had no answer to: "I don't know how to KILL/STOP
an agent so there are more slots available?" That gap is now AC-245, and it was
a real hole: the cap refused and named no way out.

**REVIEWED 2026-09-11, before any code**, and the review moved things. The
worst finding is that D4's ruling had NO MECHANISM under it and no phase built
one: the daemon cannot learn what a device is showing, and a background
conversation's reply is not "written, not spoken" today -- it is DROPPED at both
ends, by `SatelliteSession.owns(turn)` [`hub.py:234-262`] and by the page
discarding any frame whose turn is not `currentTurn` [`app.js:2789-2791`].
`docs/workspaces-ui.md:999-1002` named that as its blocker 4 and this spec had
dropped it. It is phase 6 now, and section 5 says what it contains.
Three more corrections came with it and each is recorded where it belongs:
going quiet is a MUTE and not AC-5's abort (which disposes the child you just
looked away from), a `waiting` conversation must suspend two 180-second timers
or the bucket empties itself, and "stop what you are looking at" requires the
PAGE's state machine to follow the viewed conversation rather than the device's
own turn.

**BUILD STATUS, updated 2026-09-16 against HEAD.** The owner approved the work
and stepped out ("implement everything ... make sensible decisions"). **PHASES
1 AND 2 ARE BUILT, 2026-09-11** -- the rail, the daemon-side filter, the hide,
and D8's restyle, in `76c0877` (daemon) and `f75ced0` (page), with a review and
its fixes on top. **PHASES 3, 4, 5 AND ALL OF 6'S DAEMON SIDE ARE ALSO BUILT** -- `b49c10f`
(phase 3, the ask-a-question reversal of AC-49, 2026-09-12), `ef2e2d0` (phase
4, the conversation registry, 2026-09-12), `f0704a7` (phase 5, a child per
conversation with the cap, 2026-09-12), `0d8bb6e`/`ed5be6a` (phase 6a,
2026-09-12: the `showing` frame, `Hub.chat_for` as the one resolver, the
`Say(spoken=False)` mute that never disposes the child, and attachment
ownership per conversation -- AC-238's mechanism, AC-246, AC-247), and
`f76686e` (phase 6b, 2026-09-16: `SatelliteSession.turn_by_chat`/`chat_by_turn`
scope `owns()` per conversation instead of per socket, `send_for_turn` stamps
`chat` on every outbound frame, and `_barge_in` is scoped to what a session
`hears()` rather than ending every turn in flight). What is STILL OPEN under
phase 6, read off `web/app.js:3333`: `stale()` still compares a frame's turn
against one scalar `currentTurn` rather than keying staleness per
conversation -- the daemon-side blocker is gone since 6b, every outbound frame
carries `chat` now -- so what remains is on the PAGE alone: it does not show
two live conversations at once yet, and that piece needs phase 8's list to be
worth building. **PHASES 7, 8 AND 9 ARE DEFERRED**, deliberately and not for
lack of time, and the reason
is the spec's own words: phase 3's reversal of AC-49 already shipped with its
stated cost -- "an unattended agent that would have carried on now stops and
waits" -- and phases 7-9 rewrite the turn, the barge-in and the stop semantics
of the daemon the owner uses daily. Shipping them while nobody is at the desk
would be the owner meeting a behaviour change cold, with no way to say no.

It CONTINUES `docs/workspaces-ui.md` rather than replacing it. That spec did
the prior-art research, measured the sidebar, and audited what ten parallel
agents would cost; its AC-216..AC-227 stay its own and section 8 below says
which of them this shape keeps, changes or answers.

Every number is MEASURED at HEAD (`36aa76b`) unless tagged otherwise: [READ]
means read from code, [EXECUTED] means a program was run, [COMPUTED] means
arithmetic on two measured numbers. Browser numbers come from a scratch daemon
with its own `BUTLER_STATE`, `hotkeys_enabled: false`, on a free port, killed
by the PID found listening on it.

---

## 0. The shape, in pictures

### 0.1 What the sidebar is today, and what the mock makes it

```
  TODAY -- 264 px of sidebar            THE MOCK -- 56 px rail + 264 px list
  +--------------------------+         +--+--------------------------+
  | + New chat      [<] [x]  |         |AL| All conversations        |
  | CHATS              note  |         |==| 12 chats . 5 of 10 up    |
  | [ search              ]  |         |CH| + New chat    [STATE|proj]|
  | today                    |         |EX| NEEDS YOU             2  |
  |   a conversation   68px  |         |SE|   why is the scheduler.. |
  |   a conversation         |         |PR|   headed bridge drops..  |
  | yesterday                |         |BU| WORKING               4  |
  |   a conversation         |         |MO|   the composer is bro (( |
  |   ...                    |         |SM|   sunday service order   |
  |                          |         |OC| FINISHED              1  |
  | > projects (5)      <----+- 1835*   |+ |   zen session header     |
  |   [ 901 px of pane  ]    |   px    |  | > QUIET               5  |
  |   [ 5 rows x 133 px ]    |   down  |--|                          |
  |   [ 236 px of form  ]    |         |5/|  5 of 10 agents          |
  | > diagnostics            |         |10|  [=====-----]            |
  | settings          mode   |         |+B|  running waiting error   |
  +--------------------------+         |< |  (( voice                |
                                       +--+--------------------------+
   The pane is BELOW the chats, so       The rail FILTERS the one list.
   reaching a project scrolls the        Clicking BU shows butler's chats;
   chat list entirely off screen.        ALL clears it. Ctrl+B folds the
   MEASURED: 0 px of chat list           list away and the rail stays, with
   visible when a project row is         the slot count and New chat on it.
   usable.
```

\* 1835 px, where `docs/workspaces-ui.md:54` measured 1691. Both are right:
this measurement seeded a project line on all 24 conversations and that one did
not, which is 144 px of chat list. The numbers that decide anything -- 755, 901,
133, 68 and the 0 -- are identical in both.

### 0.2 What Butler holds today, and what the mock draws

```
  TODAY                                  WHAT THE MOCK DRAWS
  =====                                  ===================

   one page                               one page
      |                                      |
   one hub  ------ one turn               one hub ---- a turn per conversation
      |                                      |
   one brain                              one brain
      |                                      |
   one _Host  (4 scalar slots)            a registry: chat id -> host
      |                                    /     |      \
   one child                          child   child   child ...  (cap 10)
      |                                    \     |      /
   one ChatStore._chat                    a registry of live conversations
      |                                          |
   one record on disk                     one record EACH
      |                                          |
   one voice, always audible              you HEAR the one you are looking at,
                                          the rest are written, not spoken
```

The left column is not a simplification. `ChatStore` holds exactly one
`self._chat` [READ `daemon/chats.py:213`], assigned in exactly two places
(`__init__` and `rotate`, `:213` and `:541`); the brain is constructed once
[`daemon/butlerd.py:1383`] and `_Host` once [`brain_harness.py:1023, 1105`].
So a second live conversation is not merely unimplemented -- it is
unrepresentable, and the interleaved transcript the earlier audit blamed on
`_Host` is actually the single `ChatStore` slot that `record()` writes through
[`brain_harness.py:273`, `chats.py:696`].

### 0.3 The three products inside one mock

The mock looks like a layout change. It is three things, and only the first is
one:

```
  1. THE SURFACE        the rail, the filter, the one triaged list, the hide.
                        Pure web/ work. Solves a measured complaint today.

  2. THE STATUS MODEL   running / waiting / idle / done / error, per
                        conversation, plus elapsed, last action, and the cap
                        meter. NONE of it exists. This is daemon work, a port
                        change, new protocol frames -- and one reversal.

  3. THE ENGINE         ten conversations actually live at once: a registry, a
                        child each, a turn each on the wire, a scoped barge-in,
                        and a stop that names the one on screen.
```

Built in that order the surface ships first and lights up as the daemon learns
to answer it. Built in the other order nothing is visible for weeks. Section 5
takes the first order, and section 2's D1 is the owner's chance to stop after
product 1.

---

## 1. What each thing the mock draws would have to come from

Every row of this table was checked against HEAD. "EXISTS" means the page could
draw it today from data the daemon already serves.

| the mock draws | where it would come from | exists today? |
|---|---|---|
| a conversation's title, age, turn count | `GET /api/chats` | **YES** [MEASURED: `{id,title,turns,updatedAt,project}`] |
| which project a conversation is in | the same route's `project` | **YES**, as an absolute PATH, and only where the conversation ENDED [`brain_harness.py:433`] |
| `running` | nothing. `HarnessConverse._turn_id is not None` is the fact, private and singular [`brain_harness.py:505`] | **NO** |
| `waiting` (the agent asked you a question) | nothing, and it is SUPPRESSED: `harness_pi.cancellation_for()` auto-answers every dialog pi raises and tells it the user walked away [`harness_pi.py:186-199`, AC-49] | **NO, and negative** |
| `idle` | `_Host.harness is None`, on no route [`brain_harness.py:158`] | **NO** |
| `done` | `AgentEnd` is consumed and discarded [`brain_harness.py:744`] | **NO** (the assistant message and `updatedAt` are the nearest thing) |
| `error` | `Failed` becomes a spoken sentence recorded as an ordinary message [`ports.py:810`] | **NO** |
| "4m 12s" elapsed while working | nothing records a turn START anywhere | **NO** |
| "editing web/index.html" | `ToolStart`/`ToolEnd` ARE emitted by the pi adapter [`harness_pi.py:277,285`] and DROPPED by their only consumer -- the `isinstance` chain at `brain_harness.py:731-745` has no branch for them | **NO, but one `elif` away at the adapter boundary** |
| "5 of 10 agents" | no cap, no counter, no registry exists anywhere in `daemon/` | **NO** |
| the blue "(( voice" pill | `hub.sessions` is a set of SOCKETS, not conversations [`hub.py:801`]; per-socket state is never serialised onto any frame | **NO** |
| a per-project badge coloured by its worst state | the states above | **NO** |
| the armed line, "Enter starts pi in E:\PROJECTS\butler" | `ShortcutStore.paths()` plus the live chat's project | **YES** |
| "opening a chat starts nothing; sending does" | `ensure()` is reachable ONLY from `turn()` [`brain_harness.py:566`, `:270`; `warm()` has an empty body] | **YES, already** |

**Read the table once more before section 2.** Of the mock's per-row facts,
one exists (`project`) and one is derivable (age). The triage list -- the thing
the whole layout is arranged around -- is a new per-conversation status model,
not a view over data that exists.

### 1.1 The one piece of good news, measured

Ten children is not the hard part. [EXECUTED, recorded in
`docs/workspaces-ui.md` 9.1]: one idle pi child is 162.3 MB and ready in
1.72 s; ten are 1326 MB, about 2% of this machine's RAM, each ready in
1.53-1.86 s. Ten pi children are twenty processes, all assigned to the job
object, and the whole tree dies within 0.5 s of terminating the host. And
AC-59 already says "one child per CONVERSATION", so ten conversations meaning
ten children is what the contract literally says [`plan.md:430`].

---

## 2. The decisions that are the owner's

Seven. Each carries a picture, a plain-words reading, the options with what
each costs, and a recommendation -- because a spec that refuses to have an
opinion makes the owner do the work twice.

### D1. How far do we go? The surface, the status, or the engine?

```
  A  SURFACE ONLY          B  SURFACE + STATUS        C  ALL THREE
  +--+----------+          +--+----------+            +--+----------+
  |RA| triaged  |          |RA| NEEDS YOU|            |RA| NEEDS YOU|
  |IL| list,    |          |IL|  one row |            |IL|  3 rows  |
  |  | but only |          |  | WORKING  |            |  | WORKING  |
  |  | QUIET    |          |  |  one row |            |  |  5 rows  |
  |  | has rows |          |  | QUIET    |            |  | QUIET    |
  +--+----------+          +--+----------+            +--+----------+
  the layout, the          the live conversation      ten conversations
  filter, the hide.        shows a real state.        alive at once.
  One conversation         Still one at a time,       Every row can be
  can ever be live,        so at most ONE row is      any state.
  so the triage is         ever non-quiet.
  a heading over
  a flat list.
```

**In plain words:** A gives you the picture now and it is honest but mostly
empty. B makes one row light up. C is the thing you asked for, and it is a
subsystem.

* **A. Surface only.** Cost: the four triage headings sit over a list where
  everything is Quiet, which reads as broken even though it is truthful.
  Benefit: it ships in days and fixes the measured complaint -- 901 px of pane
  in a 755 px viewport, and 0 px of chat list visible when a project control is
  usable.
* **B. Surface plus the status model.** Cost: a port change, new frames, and
  the AC-49 reversal in D2. Benefit: every state the page draws is real, and
  the model is what the engine will need anyway.
* **C. All three.** Cost: it touches `chats.py`, `brain_harness.py`, `hub.py`,
  `ports.py`, the protocol, and four tests go red by construction. Benefit: the
  owner's actual vision.

RECOMMENDED: **C, built in the order A, then B, then the engine**, with A
shipped and lived with on its own first. The reason is not caution: A's value
does not depend on B or C, and the mock's own measured claim -- chrome from
320 px to 56 px -- is delivered entirely by A.

**RULED: C in that order** ("do your recommendation", 2026-09-11). So the
surface ships and is lived with before the status model exists, and section 5's
phase 1 is deliberately a layout over mostly-quiet rows.

### D2. "Waiting" -- do we let a coding agent ask you a question?

This is the largest decision in the document and it is not a UI one.

```
  TODAY                                 IF AC-49 IS REVERSED
  pi raises a dialog                    pi raises a dialog
      |                                     |
  harness_pi.cancellation_for()         the question reaches the hub
  answers it: "the user dismissed            |
  the dialog"                           the conversation goes WAITING
      |                                     |
  the agent carries on or gives up      the rail badge turns amber, the
  and nobody ever knew                  row sorts to the top of NEEDS YOU
```

**In plain words:** the top bucket of the mock's triage is "Needs you". Today
Butler answers the agent's questions itself and tells it you walked away, so
nothing can ever be in that bucket for the reason the bucket exists.

* **A. Reverse AC-49.** A dialog becomes a `HarnessEvent` member, the
  conversation reports `waiting`, and the answer goes back to the CHILD,
  out of band, keyed by the id pi raised the question with.
  **Corrected 2026-09-12**: this option first said "the answer goes back
  through a turn", and it cannot. The turn is already open and the child is
  blocked INSIDE a tool call, so routing the answer as a prompt would both fail
  to unblock the dialog and write a spurious user message into the transcript.
  Cost: a member added to a CLOSED union [`ports.py:824`] that every adapter
  ships in one commit; and a blocked agent now waits on a human instead of
  proceeding, so an unattended run can stall where today it finishes -- which
  the prior art says is the norm rather than a defect, since every product with
  a real permission gate waits and the one that refuses to (Jules) is the one
  with complaints about decisions nobody made.
* **B. Keep AC-49 and let "Needs you" mean ERROR only.** Cost: the triage's
  first bucket is a rename of "something broke", and the owner's own sentence
  -- "the ones that need input are at the top" -- is not answered.
* **C. Reverse it for `confirm` and `select` only**, keep auto-cancel for
  `input` and `editor`. Cost: a rule with exceptions is the kind of list that
  goes stale (AC-60's argument).

RECOMMENDED: **A**, and it is the one thing in this document that should be
built even if the rest is refused, because it is the difference between an
agent that gets stuck silently and one that asks.

**RULED: A** ("yes", 2026-09-11). AC-49 is REVERSED and the losing argument is
kept rather than deleted: it was written on 2026-08-18 because a dialog with
nobody to answer it hung the turn forever, and what overturns it is that a
human is now watching a list whose whole top bucket exists to show the
question. The cost the owner accepted with it: an unattended agent that would
have carried on now stops and waits, so a long run left alone can stall where
today it finishes.

### D3. The cap -- what happens at ten?

```
       9 of 10                          10 of 10
  +---------------------+          +---------------------+
  | Enter starts pi in  |          | Enter is HELD: 10 of|
  | E:\PROJECTS\butler  |          | 10 agents running.  |
  | 10 of 10 once it    |          | [send is dimmed]    |
  | does.               |          | -- or stop one now. |
  +---------------------+          +---------------------+
```

**In plain words:** ten agents is the owner's number. Something has to say no.

* **A. A hard cap in `_Host.ensure()`.** It is the ONLY construction site and
  is reachable only from `turn()` [`brain_harness.py:220-259`], so a cap there
  cannot be bypassed by voice, by a route or by the page. At the cap, a send is
  REFUSED with the reason said out loud and on screen.
* **B. A cap in the route or the page.** Cost: voice bypasses it. Not a real
  option; listed so it is visibly considered.
* **C. Queue at the cap** -- the message is held and starts when a slot frees.
  Cost: a queue is a promise, and Butler has no surface that shows one.

RECOMMENDED: **A, refuse and say so.** The only prior art with a documented cap
UX (Jules) disables the button and says why, and every other product lets you
meet the limit as a mid-run failure [`workspaces-ui.md:1076-1080`].

**RULED: A, with a question the spec could not answer** -- "refusing to create
more is fine, but I don't know how to KILL/STOP an agent so there are more
slots available?" (2026-09-11). That was a real hole: the refusal named a limit
and no way past it. The answer is now AC-245 and it has three parts.

**A SLOT IS A CHILD PROCESS.** Nothing else. That makes the accounting
mechanical rather than a rule someone has to remember:

```
  running   holds a slot   (a child is up and working)
  waiting   holds a slot   (a child is up, blocked on YOUR answer)
  done      free           (the child was disposed when the turn ended)
  error     free           (the child was disposed when it failed)
  idle      free           (there has never been a child, or it was stopped)
```

**THREE WAYS A SLOT COMES BACK, and two of them need nobody:**

```
  1. the agent finishes        -> slot returns by itself
  2. the agent fails           -> slot returns by itself
  3. you stop it               -> open that conversation, press stop or Escape
                                  (D5: stop acts on what you are LOOKING at)
```

So killing an agent is: click its row in the list, press stop. Two actions, no
new control, and it is the same stop the owner already knows. The refusal at
the cap SAYS this and names where the slots are, rather than stating a number:

```
  +-------------------------------------------------------------+
  | Enter is held: 10 of 10 agents are running.                  |
  | 4 in butler, 3 in Workspace-Data, 2 in SITE, 1 waiting on you|
  | Open one and press stop to free a slot.                      |
  +-------------------------------------------------------------+
```

**THE COST OF D2 LANDING ON D3, stated because it is now reachable:** a
`waiting` agent holds a slot while doing nothing at all. Answer it and the slot
goes on being used; stop it and the slot frees; ignore it and it holds one
forever. Before AC-49 was reversed this state did not exist, so the cap could
only fill with work.

### D4. The voice -- which of ten conversations is audible?

```
  one microphone, one speaker
             |
   +---------+---------+
   |         |         |
  chat A    chat B    chat C
  (( voice   silent    silent       <- the pill, and only one of them has it
```

**In plain words:** ten agents can run; exactly one can talk to you. The
mechanism is free -- `Say(spoken=False)` (AC-40) already ships text without
audio [`hub.py:1948-1951`] -- so this decision is only about WHO.

* **A. The conversation you are looking at.** Reading a row makes it audible.
  Cost: the voice moves as you browse, which is the thing AC-31 makes expensive
  on purpose in the other direction.
* **B. A conversation you give the voice to explicitly** (the mock's "bring the
  voice here" button in the band). Cost: one more control; benefit: the voice
  never moves by accident, and the pill always names it.
* **C. Whichever spoke last.** Cost: the microphone follows the machine rather
  than the person.

RECOMMENDED: **B**, which is what the mock already draws.

**RULED: A, and the recommendation is OVERTURNED** -- "conversation follows
chat being seeing and active. keep it simple. follows what users sees"
(2026-09-11). The voice is not handed over; it FOLLOWS THE VIEW:

```
  the device is looking at chat B
            |
   chat A   chat B   chat C          A and C reply in writing;
  (silent) (( voice (silent)         B is the one you hear.
            |
   look at C instead -> C becomes audible, B goes quiet at the next
   sentence boundary and the rest of its reply is shown, not spoken
```

Why this is simpler than the mock's own band button, and it is not only taste:
there is no arbiter, no hand-over control, no stored "who has the voice", and
no state that can disagree with what is on screen. The rule is one sentence --
**a device speaks the conversation that device is showing** -- and that also
answers two devices at once without a second rule, because each speaks what its
own owner is looking at.

Three consequences, stated rather than discovered:
* The band's "bring the voice here" control is DELETED from the design. Looking
  is bringing. The rail's blue VOICE RING goes with it, for the same reason the
  review gave: under this ruling the ring and the lit tile are the same tile on
  every device, by construction, so it drew one fact twice. Two marks on the
  rail, not three.
* Switching conversations mid-reply MUTES the one you left: the rest of its
  reply is delivered written and unspoken. The first draft said this was
  barge-in's existing behaviour (AC-5) and the review showed it is not --
  `abandon()` disposes the child in harness mode, so that path would kill the
  agent you looked away from. A mute-mid-turn does not exist yet; it is phase
  6's.
* Two devices showing two different conversations means two audible replies at
  once, serialised through the one kokoro worker -- whose own record says a
  queued synthesis once waited 7887 ms [`tts_kokoro.py:16-17`]. On one desk
  that never happens; with a phone in the same room it can.

### D5. Stop, with ten running

**In plain words:** today `stop` ends "the turn this device owns"
[`hub.py:1152-1181`], and with ten running, nine children have no legal stop
frame at all.

* **A. `stop` names a conversation.** Cost: it reopens D-6 (a satellite naming
  something it does not own) and needs the same ownership check the turn id
  gets today.
* **B. `stop` keeps meaning "mine", and each conversation's own stop control
  sends it.** Same thing said differently; the difference is whether the frame
  carries a conversation id or a turn id.

RECOMMENDED: **A**, with Escape UNCHANGED -- its last case must not become
conditional, which is a standing owner ruling [`web/AGENTS.md:528-547`].
Escape stops the conversation you are looking at; the per-row stop stops that
row.

**RULED: A, with the per-row stop DELETED** -- "stop/esc does the same thing.
it stops a conversation on the seen / active agent. other agents on background
keep running" (2026-09-11).

```
  looking at chat B, five agents running

   [stop]  or  [Esc]   ->   B stops.   A, C, D, E keep running.
```

One control, one meaning, on both surfaces. The FRAME still names a
conversation -- a device's turn and the conversation it is showing are not the
same thing once ten exist -- but no surface offers stopping something you are
not looking at, so D-6's worry (a satellite naming something it does not own)
never arises from the UI. Escape's five cases and their order are untouched;
its STOP case simply means "the conversation on screen" instead of "the turn
this device owns".

What this costs, and it is the pair to D3: freeing a slot is always
open-then-stop. There is no way to stop five agents from the list without
opening five conversations. At a cap of ten that is the worst case and the
owner accepted it in the same breath as asking the question.

### D6. Where does the rail live?

```
  the 64 px collapsed rail            a new 56 px rail
  ALREADY EXISTS and works            beside the sidebar
  +--+-------------------+            +--+--+----------------+
  |[<]|                  |            |RA|[<]|               |
  |[+]|   (blank: the    |            |IL|   |  the list     |
  |   |    scroll is     |            |  |   |               |
  |[=]|    hidden)       |            |  |   |               |
  +--+-------------------+            +--+--+----------------+
  264 -> 64 px MEASURED,              two collapse controls, two
  even after dragging the             mental models, 56 + 264 = 320
  handle to 400 px first
```

**In plain words:** Butler already has a narrow rail -- it is what the sidebar
becomes when you collapse it -- and it is currently an empty 64 px column with
three buttons in it. The mock's rail is the same column with the projects in
it.

This reverses `docs/workspaces-ui.md` 0.3, which measured the collapse as
BROKEN and priced the rail option out on that. It is fixed
[`web/index.html:266`], measured 264 -> 64 px at HEAD, and the hostile case
(drag to 400 px first, then collapse) also gives 64 px.

* **A. The collapsed sidebar BECOMES the project rail.** The tiles appear in
  the 64 px column; hiding the list is what the collapse control already does.
  Cost: 64 px not 56 px, and the collapsed rail currently blanks its whole
  scroll with `visibility: hidden` [`index.html:444`], which has to become
  selective.
* **B. A separate 56 px rail, always present, beside the sidebar.** Cost: two
  narrow columns when collapsed, or a second rule about what collapse means.

**RULED: A** ("do your recommendation", 2026-09-11).

RECOMMENDED: **A.** One column, one control, one stored state
(`sidebar_collapsed`, which already exists and is already not a setting). Note
the naming constraint either way: `.rail`, `#railRoot` and `--rail-w` are
already the SETTINGS page's section navigation [`index.html:255, 1439,
1502-1556`] and `probe_page.py` reads `#railRoot` -- the project rail needs
different names.

### D7. `probe_page.py` asserts every served conversation has a row

**In plain words:** the probe that proves the chat list is honest counts rows
against what the daemon served [`probe_page.py:477-478`]. A rail that FILTERS
makes that false on purpose.

* **A. The probe compares against the SCOPED serve.** The filter is the
  daemon's (`GET /api/chats?project=`), exactly `docs/chat-search.md`'s ruling,
  so "served" means "served for this scope" and the assertion survives intact.
* **B. Weaken the assertion.** It is the instrument for AC-164 and for AC-219;
  weakening it removes the only thing that catches a page-side filter.

**RULED: A** ("follow your recommendation", 2026-09-11).

RECOMMENDED: **A**, and it is why AC-229 below insists the filtering is the
daemon's. Two more probe assertions need the same treatment and they are named
in section 6.


### D8. What it LOOKS like -- RULED 2026-09-11, and this one arrived with a mock

"p1 quiet will do." The owner supplied two ChatGPT screenshots, asked for "all
features, but a more minimalistic overall look", said what they liked about them
("the font and font size, easy to read", "the overall minimalistic design",
"help is on hover any button"), and chose `web/mocks/p1-quiet.html` from the two
variations built against them.

```
  o2, THE CHOSEN SHAPE                p1, THE CHOSEN LANGUAGE
  +--+--------------------+           +--+--------------------+
  |AL| ALL CONVERSATIONS  |           |ALL Search chats       |
  |==| 12 chats . 5 of 10 |           |CH  + New chat         |
  |CH| NEEDS YOU       2  |           |EX  Needs you        2 |
  |  | [ why is the sch.. |           |    why is the schedu. |
  |  |  o waiting PROTO.. |           |     waiting  Proto-D  |
  |  | ]                  |           |    12m 40s  needs you |
  +--+--------------------+           +--+--------------------+
  boxed rows, mono meta,              no borders at all, one sans,
  uppercase tracked labels,           sentence-case labels, a tone
  bordered pills                      step instead of a line
```

**What the ruling settles, and every one of these is now binding on the page:**

* **No borders.** Separation is space plus a tone step -- hover at 7% of the
  foreground, the open row at 11%. The composer keeps a hairline, because a text
  field with no edge stops reading as a text field.
* **No monospace in the interface.** Labels, counts, paths, state words and
  elapsed times are all the one humanist sans. This is the single biggest visual
  change and it is what stopped the list reading as an instrument panel.
* **Bigger, calmer type.** Body at about 16.5px with line-height near 1.7,
  sidebar rows at 15px, section labels at 13px in SENTENCE CASE with no
  uppercase and no letter-spacing.
* **Help on hover, on every control**, through ONE mechanism: a `data-help`
  attribute, one delegated listener, one positioned element, about 350 ms on
  hover and IMMEDIATE on keyboard focus. Never the browser's own `title`, whose
  delay is not ours and which never shows on focus. It takes NO case in the
  Escape handler (AC-112).
* **The model pickers move INTO the composer** as plain text with a chevron.
  That deletes `#pickRow`, the row that was rejected on sight the day it
  shipped, and it is the reference's own idea.
* **A conversation row keeps its second line** -- state, elapsed, last action,
  project, in one quiet phrase. That is the whole of what p1 proved against p2,
  whose title-only row its own author measured as unreadable at the cap: nine
  identical dots and titles, and "which one is stuck" is nine hovers.

**Three divergences from the owner's own reference, each forced by a rule this
project already paid for**, and all three are in both mocks' headers:

1. The reference's one saturated colour is a blue SEND button. Here blue is the
   microphone and nothing else, so the send is a NEUTRAL filled circle.
2. The reference signals by colour and type alone. The five machine states keep
   five dot SHAPES, and the rail badge does too -- which fixes a defect o2
   shipped and AC-232 names.
3. The reference's greys sit below 4.5:1. Six tokens moved to clear it:
   `--fg-3` in all three palettes, the light theme's `--st-run` and `--st-wait`
   (4.02 and 3.41 as a state word), and the voice pill's fill (3.32 -- an accent
   on a 17% tint of itself). MEASURED by canvas read-back against the real
   ancestor stack.

**One thing the owner should look at rather than take on trust**, raised by the
mock's own author: butler's project hue is 212 and the voice accent is 235, so
filtering to butler paints the head, the edge bar and the lit tile blue on a
page whose one rule is that blue means the microphone. It does not break
AC-232's letter -- no project colour is drawn FROM `--sa` -- and it may break its
spirit.

---

## 3. Acceptance criteria

AC-228..AC-248. The highest in use anywhere is AC-227
(`docs/workspaces-ui.md`). Each criterion carries a plain-words line, how it
FAILS in the room, and whether the failure is recoverable -- `docs/voice-
control.md`'s rule, for its reason: AC-92 was built, shipped and reverted
because nobody wrote down what being wrong would look like.

### The surface (product 1)

- **AC-228 (the rail filters one list; nothing is ever in a view you are not
  looking at).** Clicking a project tile scopes the conversation list to that
  project and does not swap it for a different list. ALL clears it. Every group
  heading still draws while a filter is on, at "0 of N", naming where the
  others went.
  *Plain:* one list, always the same list, with a lens over it.
  *Fails as:* Codex's vanishing threads -- a conversation that exists in one
  view and nowhere else. **Recoverable.**
- **AC-229 (the scoping is the DAEMON's).** The page sends the scope and draws
  what comes back; it filters nothing itself, and the number of rows drawn
  equals the number served for that scope.
  *Plain:* if the daemon would show a conversation, the page does not hide it.
  *Fails as:* rows hidden invisibly, and the probe that would catch it broken
  in the same commit. **Recoverable, loudly.**
- **AC-230 (the list hides; the rail never does).** Hiding the conversation
  list leaves the rail, and the rail carries the slot count, New chat and the
  way back -- in BOTH states, not appearing on hide. Ctrl+B and a visible
  control both do it, and the control cannot scroll out of reach.
  *Plain:* you can always get the list back, from something you can see.
  *Fails as:* a folded sidebar with no way back on a short window. **Recoverable.**
- **AC-231 (the chrome fits, and both lists are visible at once).** At the
  owner's real row count the projects surface and some part of the chat list
  are within the sidebar's scroll viewport at the same time, at 1440x900 and at
  390x844. Today: the pane is 901 px in a 755 px viewport and the chat list
  visible when a project row is usable is 0 px [MEASURED at HEAD].
  *Plain:* you can see your projects and your chats together.
  *Fails as:* the owner's literal complaint, unfixed. **Recoverable.**
  **THE DESKTOP HALF IS MET (re-measured 2026-09-15, scratch daemon, owner's
  shape): 328 px sidebar, `scrollHeight == clientHeight == 900`, rail 64 px,
  and the chat list fully inside the viewport (198 px of its 198). The PHONE
  half measured 0 px: at 390x844 the sidebar is the 64 px rail alone and
  `#chatList` sits translated off-screen at x=-236 until the burger opens it.
  That is AC-230's design working, and it makes this criterion's second size
  UNSATISFIABLE as written -- a rail, a 264 px list and a transcript do not fit
  in 390 px, and the criterion does not say which gives way. Left standing and
  flagged rather than quietly reworded: it is the owner's to restate.**
- **AC-232 (a project hue never touches a state colour).** The per-project
  tints are their own scale; no project colour is drawn from `--sa`, `--g1`,
  `--g2` or `--warn`, and no state is signalled by colour alone -- every state
  is a dot SHAPE plus a word.
  The rail's own badge is the case to get right and the mock gets it wrong: it
  carries a COUNT and signals which state that count is about by background
  colour alone [`o2-filter.html:246-247`]. On the shipped page it carries the
  state's shape as well, or it says the word.
  *Plain:* a green project must never be read as a running agent, and the badge
  must not be the one place colour is the only channel.
  *Fails as:* AC-174's rule broken on a new surface, and a colour-blind reader
  losing the states. **Recoverable**, and the existing grep gate in
  `test_seams.py` is extended to catch it.

- **AC-251 (browsing moves nothing, filing is its own act, and the pane names
  what it shows).** Four rules. (1) Pressing a project tile SCOPES the list to
  that project and opens its newest conversation, and re-files NOTHING: no
  conversation changes project because someone looked at a list. (2) Filing is
  deliberate, and it has two doors -- New chat, with a scope on, files the
  conversation it just minted into that project, and the FILTER HEAD carries a
  "file here" control, which reads "filed here" and is disabled when the
  conversation already lives there. (3) A clear, LABELLED control to start a
  new conversation is visible in the list column, above the search. (4) The
  pane's header is the daemon's own word for the conversation -- the same
  `ChatStore.title` the list row is built from -- with the page's own
  placeholder only when the daemon says empty.
  *Plain:* looking at a project does not move your chat; putting your chat in a
  project is something you do on purpose; and starting a chat is obvious.
  *Fails as:* the owner's complaint of 2026-09-15, both halves at once -- "a
  few chats are always showing on all projects" and "I still don't see a clear
  easy button to start a NEW chat". MEASURED: pressing the owner's six tiles in
  turn drew the SAME conversation under all six (rule 1, violated by the first
  version of this criterion), and the only visible New chat affordance was an
  unlabelled 44x44 pencil at y=758 of a 64 px rail whose one `+`, at y=387,
  means ADD A PROJECT.
  **REVERSED THE SAME EVENING IT SHIPPED, and the losing argument is kept here
  because it was the owner's own ruling**: "clicking a project row changes the
  working directory of the chat" (2026-09-11, `docs/workspaces-ui.md`). The
  ruling was about INTENT -- a click is how you say where the chat lives -- and
  it did not consider that a click is also how you BROWSE. Filing moved to the
  filter head; the press went back to browsing. **Recoverable.**
  Rule 1's witness is `test/manual/probe_page.py --write`, which presses two
  tiles and compares EVERY conversation's project after EACH press: the first
  version of that check compared only the end state and came back GREEN against
  a deliberately restored drag, because the drag moved the chat into the first
  project and then into the second and the map ended where it began. Caught by
  mutation (D-13), and the check now names the conversation that moved.

### The status model (product 2)

- **AC-233 (every state the page draws is the daemon's).** `running`,
  `waiting`, `idle`, `done` and `error` arrive from the daemon per
  conversation. The page infers none of them -- not from a missing reply, not
  from a timer, not from the socket.
  *Plain:* the page never guesses whether an agent is working.
  *Fails as:* a row that says running with nothing running, which is AC-7b's
  failure in a new organ. **Recoverable.**
- **AC-234 (waiting means a human was actually asked).** A conversation reads
  `waiting` only when a coding agent raised a question that reached a human and
  is unanswered. Answering it from the page or by voice resumes that agent.
  **THE DEADLINE MAY NOT BE BUTLER'S, and that is new (research,
  2026-09-12).** pi's own RPC lets the extension attach a `timeout` to a
  dialog, and pi AUTO-RESOLVES it agent-side when that expires -- "the client
  does not need to track timeouts" [READ, `pi-coding-agent/docs/rpc.md`]. Its
  resolved default is byte-identical to the cancel Butler sends today, so
  nothing downstream can tell a timeout from a dismissal. So Butler PASSES THE
  DEADLINE THROUGH and shows it, and never invents one; `editor` carries no
  timeout path at all and blocks forever. The criterion is therefore that
  BUTLER never auto-answers -- not that the question waits for ever, which is
  not Butler's to promise.
  While a conversation is `waiting`, the harness idle deadline
  (`HARNESS_IDLE_S = 180.0`, `brain_harness.py:120`) and the page's thinking
  watchdog (`WATCHDOG_MS.thinking = 180000`, `app.js:106`) are SUSPENDED for
  it: a waiting child emits nothing by definition, so the two timers that exist
  to catch a silent child would fire at three minutes and take the question
  with them.
  *Plain:* "needs you" means it needs you, and it waits as long as you take.
  *Fails as:* a bucket that is never populated (if AC-49 stands), one that fills
  with things nobody was asked (if it is faked), or one that empties itself
  after three minutes (if the timers are left alone). **Recoverable**, but it
  costs trust in the triage every time.
- **AC-235 (the last action is reported, never invented).** The one-line
  activity a row shows comes from the harness's own tool events
  (`ToolStart`/`ToolEnd`, already emitted and currently dropped), and a
  conversation with no tool event shows its age instead of a sentence.
  *Plain:* "editing web/index.html" is what the agent did, not what we assume.
  *Fails as:* a plausible sentence describing work that did not happen.
  **NOT recoverable** in the moment -- the owner acts on it.
- **AC-236 (elapsed is measured from a recorded start).** "4m 12s" counts from
  a turn start the daemon wrote down, survives a page reload, and is absent
  rather than zero when there is no start.
  *Plain:* the clock is the daemon's, not the tab's.
  *Fails as:* a timer that resets on reload and lies about how long something
  has been stuck. **Recoverable.**
- **AC-237 (the cap is the daemon's and unbypassable).** The limit is enforced
  at `_Host.ensure()`, the only place a child is constructed and reachable only
  from `turn()`. At the cap a new send is REFUSED, out loud and on screen, and
  names what would free a slot. Nothing is queued silently.
  *Plain:* the eleventh agent does not start, and you are told why.
  *Fails as:* eleven children, or a message that looks sent and is not.
  **Recoverable**, but a silent queue is not.
- **AC-238 (a device speaks the conversation it is showing).** The audible
  conversation is the one on screen on that device. Every other conversation's
  reply is written and not spoken, through the existing `Say(spoken=False)`,
  and is marked as shown-not-heard. Switching what you are looking at moves the
  voice with it by MUTING the conversation you left -- the rest of that reply is
  delivered with `spoken=False` and shown -- and never by aborting it. This
  corrects the spec's first draft, which said "at the next sentence boundary,
  AC-5's own rule": AC-5's path is `abandon()`, and in harness mode that
  DISPOSES the child [`hub.py:1173-1180`], so going quiet that way would kill
  the agent you just looked away from -- exactly what AC-242 and AC-243 exist to
  prevent. A mute-mid-turn path does not exist today and is phase 6's work.
  There is no hand-over control and nothing stores who "has" the voice.
  *Plain:* you hear the chat you are looking at. Look somewhere else and you
  hear that one instead.
  *Fails as:* a background agent talking over the one you are reading, or a
  reply that was silent with nothing on screen saying so. **Recoverable.**

### The engine (product 3)

- **AC-239 (opening a conversation starts nothing; sending starts it).**
  Opening any conversation -- live or past -- spawns no child, and the first
  send is what spawns it. The armed line says what that send will start and
  where, before it is sent.
  *Plain:* you can look before you start anything.
  *Fails as:* browsing history spawning full-capability coding agents.
  **NOT recoverable** -- a spawned agent may already have written.
- **AC-240 (a past conversation can be made live without abandoning the one you
  opened).** Clicking a conversation and then sending continues THAT
  conversation. Today the page must call `backToLive()` first, so sending
  abandons the chat you just opened.
  *Plain:* the chat you clicked is the chat you talk to.
  *Fails as:* today's behaviour, which is the inversion of the owner's rule.
  **Recoverable.**
- **AC-241 (one child per conversation, and no conversation disposes
  another's).** Each live conversation holds its own child, identified by
  `HarnessSpec` equality exactly as AC-60 already requires. A turn in one
  conversation never disposes another's child and never writes into another's
  record.
  *Plain:* ten agents, ten transcripts, no crossed wires.
  *Fails as:* the interleaved transcript measured today -- two conversations
  appended into one record. **NOT recoverable** once written.
- **AC-242 (the wake word no longer kills every agent).** Saying the wake word
  interrupts the conversation that owns the voice and leaves every other
  running agent alone. AC-5's sentence is unchanged; its SCOPE narrows from
  every session to the audible one.
  *Plain:* talking to Butler does not kill nine background agents.
  *Fails as:* nine orphaned children and an abort that reached none of them.
  **NOT recoverable.**
- **AC-243 (one stop, and it stops what you are looking at).** The stop control
  and Escape do the same thing: they stop the conversation on screen. Every
  other running conversation is untouched -- asserted as a NEGATIVE, on the
  other children still being up. The frame names the conversation rather than
  the device's turn, because those stop being the same thing, and Escape's five
  cases and their order are UNCHANGED.
  This has a cost the ruling implies and the first draft did not state: the
  page's `escapeStops()` keys off the DEVICE's own `data-state`
  [`web/AGENTS.md:528-535`], so viewing a running conversation this device did
  not start, Escape today falls through to "clear the draft". Making stop mean
  the conversation ON SCREEN means the page's state machine follows the VIEW,
  which is a page rewrite and is phase 8's largest piece.
  *Plain:* stop stops the agent you are watching. The rest carry on.
  *Fails as:* a stop that kills nine background agents, or Escape's last case
  becoming conditional -- a ruling the owner has already made once.
  **NOT recoverable** in the first direction: the killed agents may have been
  mid-edit.
- **AC-244 (nothing in `web/` names a project, a path, a state or a setting).**
  Project names, paths, hues, state words, agent names, the cap and the
  vocabulary all arrive from the daemon. AC-41 applied to this surface.
  *Plain:* the page draws what it is told and invents no names.
  *Fails as:* a hardcoded list that drifts, and the drifted copy is the one
  missing what was added last. **Recoverable**, loudly.
- **AC-245 (a slot can always be freed, and the refusal says how).** A slot is
  a child process: `running` and `waiting` hold one, `done`, `error` and `idle`
  do not. A slot returns by itself when an agent finishes or fails, and
  deliberately when the owner opens that conversation and stops it -- which
  frees it within the same time a stop already takes. The refusal at the cap
  NAMES where the slots are ("4 in butler, 3 in Workspace-Data, 1 waiting on
  you") and says what to do, rather than stating a number.
  *Plain:* at ten agents you are told who is holding the slots and that opening
  one and pressing stop frees it.
  *Fails as:* the owner's own question with no answer -- a cap that refuses and
  names no way out, which is a dead end rather than a limit. **Recoverable**,
  but it makes the cap feel broken every time it is met.
- **AC-246 (a spoken turn always names its conversation).** An utterance
  arriving from a device goes to a conversation the daemon can name, the page
  shows which, and a fresh device is told which conversation it is on in the
  `ready` handshake. When Butler starts work somewhere the owner did not name,
  it says where before it starts.
  *Plain:* you talk to Butler from across the room and it is never a guess
  which agent heard you.
  *Fails as:* a spoken sentence starting a coding agent in the wrong repository
  -- AC-31's exact failure arriving by a path AC-31 does not guard. **NOT
  recoverable**: the agent may already have written.
- **AC-247 (an attachment belongs to the conversation it was attached in).** A
  file pends against the conversation that was on screen when it was attached,
  not against whatever the device's next turn happens to be, and the page shows
  it against that conversation only.
  *Plain:* the photo you attached to the site chat does not arrive in the
  trading one.
  *Fails as:* a file handed to the wrong coding agent, which then reads it from
  disk. **NOT recoverable.**
- **AC-248 (a rewind refuses while that conversation's agent is running).**
  `docs/rewind.md`'s control is hidden while the DEVICE is thinking or
  speaking; with ten conversations that test is the wrong one, so a rewind
  offered on a running background conversation is refused with the reason,
  because `post_session_rewind` does nothing to a held child
  [`butlerd.py:1871`] and the child's next `record()` would append to a record
  that was just truncated.
  *Plain:* you cannot rewind a conversation whose agent is still typing.
  *Fails as:* a truncated transcript that a running agent immediately writes
  into, so the record shows a conversation that never happened. **NOT
  recoverable.**

---

## 4. Gherkin

```gherkin
Scenario: AC-228 the rail filters
  Given twelve conversations across eight projects
  When the owner clicks the butler tile
  Then the list shows only butler's conversations
  And every heading still draws, saying how many are elsewhere
  And clicking ALL restores the full list

Scenario: AC-229 the daemon does the scoping
  Given the list is scoped to a project
  When it repaints
  Then the page issued GET /api/chats with a project parameter
  And the rows drawn equal the rows served

Scenario: AC-230 the way back survives
  Given the conversation list is hidden
  When the window is 600 px tall and there are eighteen projects
  Then the slot count, New chat and the control that restores the list are all
       on screen and clickable

Scenario: AC-231 both lists at once
  Given the owner's five projects and twenty-four conversations
  When a project control is usable
  Then some part of the conversation list is inside the scroll viewport

Scenario: AC-233 no invented states
  Given a conversation whose agent has stopped answering
  When the daemon reports no state change
  Then the row does not change state
  And nothing on the page starts a timer that would change it

Scenario: AC-234 an agent asks a question
  Given a coding agent running in project A raises a confirm dialog
  When the daemon receives it
  Then that conversation reads waiting
  And it sorts to the top of Needs you
  And answering from the page unblocks the same child

Scenario: AC-234 error case -- the question is never answered
  Given a conversation has been waiting for an hour
  When nobody answers
  Then the agent is still waiting and has not been auto-answered
  And the row still says waiting

Scenario: AC-235 the last action is the agent's own
  Given an agent that has run one tool
  Then the row shows that tool's own report
  And a conversation with no tool event shows its age instead

Scenario: AC-237 the cap refuses
  Given ten conversations hold a slot
  When the owner sends an eleventh message
  Then no child is started
  And the refusal names the cap and what would free a slot
  And the message is not silently queued

Scenario: AC-237 the cap cannot be bypassed
  Given ten conversations hold a slot
  When the owner says "work in project X" and then speaks a request
  Then no eleventh child is started

Scenario: AC-238 the voice follows the view
  Given three conversations are running and the owner is looking at the second
  When two of them produce replies at the same time
  Then only the conversation on screen is spoken
  And the other reply is written and marked as shown, not heard

Scenario: AC-238 looking away moves the voice
  Given the conversation on screen is being spoken
  When the owner opens a different conversation
  Then the speech stops at the next sentence boundary
  And the rest of that reply is shown, not spoken
  And the newly opened conversation is the one that is now audible

Scenario: AC-239 opening starts nothing
  Given a past conversation in project A
  When the owner opens it
  Then no process is spawned
  And the armed line says what Enter would start and where

Scenario: AC-240 the chat you clicked
  Given the owner opens a past conversation
  When they send a message
  Then that conversation receives it
  And the previously live conversation is untouched

Scenario: AC-241 no crossed wires
  Given two conversations are live
  When both take a turn
  Then each child answers its own conversation
  And each record contains only its own messages

Scenario: AC-242 the wake word is not a kill switch
  Given four agents are running and one holds the voice
  When the owner says the wake word
  Then the audible conversation is interrupted
  And the other three are still running

Scenario: AC-243 stopping the one you are watching
  Given five conversations are running
  When the owner opens the third and presses stop
  Then that conversation's child is stopped
  And the other four children are still up
  And Escape in the same position does exactly the same thing

Scenario: AC-245 freeing a slot at the cap
  Given ten conversations hold a slot
  When the owner reads the refusal
  Then it names which projects hold the slots and how many are waiting on them
  When the owner opens one of them and presses stop
  Then a slot is free
  And the next message starts an agent

Scenario: AC-245 a slot returns without being asked
  Given ten conversations hold a slot
  When one of them finishes
  Then a slot is free with nobody having stopped anything
```

---

## 5. The plan

Nine phases, **rebuilt after the review**. Each phase says what SHIPS, its
steps, and the check that proves each step -- a step with no check is a step
nobody can tell is done. The stories the review found are steps here rather
than warnings at the bottom.

```
  WHAT DEPENDS ON WHAT

  1 rail + filter        web + one route      ships alone, fixes the measured
       |                                      complaint
  2 hide the list        web + store          needs 1
       |
  3 an agent may ask     daemon only          needs NOTHING -- can go first,
       |                                      in parallel, or tomorrow
  4 registry             daemon only          needs nothing
       |
  5 a child each + cap   daemon               needs 4
       |
  6 OWNERSHIP            daemon + protocol    needs 5. Nothing above is
       |                                      visible to a second conversation
  7 status on the wire   daemon + protocol    needs 6
       |
  8 the triage + stop    web                  needs 7
       |
  9 the announcer        daemon               needs 6
```

Phases 1-2 and 3-4 are independent of each other. If only one thing is built,
build phase 1. If only two, build 1 and 3.

---

### Phase 1 -- The projects become a rail, and the rail filters

**Ships:** the owner's measured complaint, fixed, IN THE CHOSEN LANGUAGE.
901 px of pane in a 755 px viewport becomes a 64 px column, the chat list stops
being pushed off screen, and the page is restyled to D8 in the same work --
because the rail and the list are the same markup and restyling them twice is
how a surface ends up half in one language and half in the other.

What phase 1 may NOT draw: the four triage groups, the state dots, the cap
meter, the elapsed time and the last action. None of them has data behind it
until phase 7, and drawing one from the page's own guess breaks AC-233. The day
buckets stay; the row's second line carries what the daemon actually serves --
the project and the age.

**Criteria:** AC-228, AC-229, AC-231, AC-232, plus `workspaces-ui.md`'s AC-218.

1. **`GET /api/chats?project=<row id>` scopes in the DAEMON.** Reuse the `?q=`
   branch's shape exactly, including `asyncio.to_thread` (D-25), because it is
   the same directory walk. Three values are distinct and all three are real: a
   row id, the empty string (unfiled), and absent (everything). A row id no row
   has is a 400 naming it, `POST /api/project`'s rule.
   *Verify:* `test_chats.py` and `test_http_surface.py` -- the three values,
   the 400, and the 404 in endpoint mode.
2. **Which row a conversation is in is DERIVED, not stored.** BUILT this way
   and it is simpler than what this step first prescribed, which was
   `workspaces-ui.md` D2/C -- a row id written beside the path, behind its own
   migration flag. The losing argument is kept: a stored id survives MOVING a
   project to a genuinely different directory, and a derived match does not.
   What overturned it is that a derived match cannot drift, needs no migration,
   and makes AC-218 true by construction rather than by a one-time pass -- there
   is no second copy to go stale, because there is no second copy. A record
   keeps the path `work_in` wrote and belongs to the row whose path matches it
   under `shortcuts.same_dir`.
   *Verify:* add a trailing separator to a row's path, then change only its
   case, then write it with the other separator style; the same conversations
   stay filed under it. MUTATE the comparison to a raw string compare, confirm
   it applied, and watch those tests fail (D-13). **Measured on the build:** all
   three AC-218 rows went red quoting `orphaned its conversations: []`, and the
   per-row count test reddened with them, which is the same rule asserted from
   the other route.
3. **The project rows render into the collapsed sidebar's 64 px column.** That
   column exists and works (264 -> 64 px MEASURED, twice) and today is blank,
   because `[data-sidebar="collapsed"] .sb-scroll { visibility: hidden }`
   blanks the whole scroll -- that becomes selective. Names may NOT be `.rail`,
   `#railRoot` or `--rail-w`: those are the settings page's and `probe_page.py`
   reads them.
   *Verify:* `probe_ux.py` measures the column against the 901 px
   before-number, and both lists visible against the 0 px before-number, at
   1440x900 and 390x844. Plus a screenshot at both widths -- the model-picker
   row proved no instrument has an opinion about weight or order.
4. **A tile filters; ALL clears.** The page sends the scope and draws what
   comes back; it filters nothing itself (AC-229).
   *Verify:* `e2e_scenarios.py` section L extended -- scope, count the rows,
   scope to a project with nothing in it, scope plus search.
5. **Decide what a tile CLICK does besides filter, and write it down.** The
   owner's words are "clicking on chat loads history of latest chat"
   (`workspaces-ui.md:941`), so a tile filters AND opens that project's newest
   conversation -- and a project with NO conversations filters and leaves the
   conversation on screen alone rather than blanking it.
   *Verify:* the empty-project case in section L, asserting the conversation
   did not change.
6. **The restyle is D8, applied to the whole page and not only the sidebar.**
   Tokens, type scale, the hover-help mechanism on every control, the pickers
   folded into the composer, and `#pickRow` deleted. `web/mocks/p1-quiet.html`
   is the reference implementation and its header carries the three divergences.
   *Verify:* the hover-help check is the owner's own request turned into an
   assertion -- every interactive control has non-empty help, and no native
   `title` survives. Plus the contrast floor at 4.5:1, measured by canvas
   read-back and not by parsing `getComputedStyle` (Chrome serialises
   `oklab()`, which a naive reader scores as 1.0).
7. **The probes are updated in the SAME commit** (AC-172). `probe_page.py`'s
   `len(drawn) == len(served)` compares against the SCOPED serve (D7); its
   no-match search assertion stays true because the day buckets are untouched
   in this phase.
   *Verify:* `probe_page.py` and `probe_ux.py` both exit 0, with every number
   they printed before unchanged or better.

**Do not** rebuild the chat list here. The day buckets stay. There is nothing
to triage by until phase 7.

---

### Phase 2 -- Hide the list, keep the rail

**Ships:** Ctrl+B, and a phone layout the sidebar has never had.

**Criteria:** AC-230.

1. **Collapsing IS hiding.** D6/A means one stored fact, not two:
   `sidebar_collapsed` already exists, already lives in the daemon (AC-8), and
   is already not a setting (AC-41). Nothing new is stored.
   *Verify:* `test_shortcuts.py` -- the existing round-trip still passes with
   no new key.
2. **The rail's footer is sticky**, carrying the slot count, New chat and the
   control that brings the list back. MEASURED on the mock: the way back went
   unreachable at a 620 px viewport height and at 18 projects.
   *Verify:* a browser check at 1280x440 that all three are hit-testable by
   `elementFromPoint` -- the sidebar-resize work shipped a handle no mouse
   could reach and only that check caught it.
3. **Below the page's existing narrow breakpoint the list is a drawer**, with
   the rail still beside it. BUILT at **900 px**, not the 820 this step first
   named: the page already had a drawer with a burger and a scrim at 900, and
   inventing a second breakpoint 80 px away would have been two rules for one
   question. The crossing is PER DEVICE and is never written back to the store,
   or a phone crossing it overwrites the desk (D-2).
   *Verify:* `e2e_scenarios.py` section I extended at 390 px.
4. **Section M is UNTOUCHED and still green.** This step predicted a rewrite,
   on the assumption that a fixed rail replaces the draggable sidebar. It does
   not: the rail is a fixed column INSIDE the box the handle sizes, so the
   handle, its `role="separator"` and the 200-560 clamp all survive -- which is
   what the owner asked for when they asked for a resizable sidebar in the first
   place. Nothing was rewritten and nothing needed to be.

---

### Phase 3 -- A coding agent may ask you a question

**Ships:** the single most valuable change in this document, and it needs
nothing else in it. Today a blocked agent is told "the user dismissed the
dialog" and nobody ever learns it asked.

**Criteria:** AC-234. **Reverses AC-49.**

1. **A dialog becomes a member of the `HarnessEvent` union** (`ports.py:824`).
   It is a CLOSED union, so every adapter ships the new member in one commit --
   pi, fake, and whatever the seam test asserts about them.
   *Verify:* `test_seams.py`'s port conformance, and the fake grows the event
   so every later phase can drive it without a real child.
2. **`harness_pi.cancellation_for()` stops auto-answering** (`:186-199`). Its
   four methods are `select`, `confirm`, `input`, `editor` (`:154`) and they
   are not the same shape: a `select` carries OPTIONS and a composer cannot
   answer it.
   *Verify:* a fake dialog of each of the four reaches the hub unanswered.
3. **The conversation reports `waiting`, and the question is BROADCAST with
   its options** (section 9.4). `hub.broadcast`, never `send_for_turn`, or the
   device that answers -- usually not the device that asked -- never sees it.
   The answer comes back through `POST /api/answer` rather than a socket frame,
   because only a route can tell the answerer their answer was too late.
   *Verify:* `test_conversation.py` -- a dialog in, a question on the wire, an
   answer back in, the same child continues. AC-249 asserted with TWO sessions,
   the second NOT owning the turn. AC-250: an answer for an unknown `qid` is
   refused and never reaches the child.
4. **Suspend both 180-second timers while waiting.** `HARNESS_IDLE_S`
   (`brain_harness.py:120`) and the page's `WATCHDOG_MS.thinking`
   (`app.js:106`) both fire on a child that emits nothing, which is exactly
   what waiting IS.
   *Verify:* a fake dialog held for longer than 180 s of simulated time; the
   question survives and no respawn happens. This is the step to validate by
   mutation -- leave one timer alone and watch the test fail.
5. **Record what the reversal costs**, in `plan.md`'s AC-49 entry and in
   `daemon/AGENTS.md`: an unattended run that would have carried on now stops
   and waits for a human.

---

### Phase 4 -- A conversation registry

**Ships:** the bug fix that sending no longer abandons the conversation you
just clicked.

**Criteria:** AC-239, AC-240.

1. **`ChatStore` gains a registry and `open(id)`.** `rotate()` mints and
   registers instead of replacing the one slot; `delete()`'s "is this the live
   one" test becomes "is it in the registry"; `_attach()` stops meaning "the
   one live chat".
   *Verify:* `test_chats.py:180` and `:910` are rewritten here -- "the live
   conversation" becomes plural -- with the old assertions kept as what they
   proved.
2. **`session_ref()` takes the chat as an argument.** It mutates `self._chat`
   today and is called per spawn from a closure with no conversation parameter
   (`butlerd.py:1164`). This is the hinge of the whole registry.
   *Verify:* two conversations, two session files, neither minted into the
   other's record.
3. **The page opens instead of calling `backToLive()`.** `openChat` sets
   `viewingChat` and every send path calls `backToLive()` first (`app.js:1481`,
   `:2878`, `:3569`) -- that is the inversion of the owner's rule.
   *Verify:* `e2e_scenarios.py` -- open an old conversation, send, and the
   message lands in THAT conversation.

---

### Phase 5 -- A child per conversation, with the cap

**Ships:** ten agents that actually run at once, serialised only by the turn
machinery above them.

**Criteria:** AC-241, AC-237, AC-245.

1. **`_Host` becomes a registry keyed by chat id.** AC-60's one comparison does
   not change; its MEANING changes, from "the running child" to "the child for
   this chat".
   *Verify:* against the FAKE harness -- two conversations, two children,
   neither disposing the other. Then MUTATE the lookup to ignore the chat id
   and watch that test fail: getting this wrong answers from another project
   silently, which is the failure nothing else would catch.
2. **The cap lands in `ensure()`**, the only construction site and reachable
   only from `turn()`. A `restart()` replay reaches it too (`:261-270`), so a
   replacement must not count as an eleventh.
   *Verify:* eleven conversations, ten children, one refusal -- and a restart
   at the cap that still works.
3. **The refusal names where the slots are** and what frees one (AC-245).
   *Verify:* the refusal text contains the per-project counts, asserted on the
   daemon's own sentence rather than on the page's.
4. **A slot is a child**: `running` and `waiting` hold one, `done`, `error` and
   `idle` do not. Stopping frees one; finishing and failing free one without
   being asked.
   *Verify:* a fake conversation taken to each of the five states, counting
   live children after each.

---

### Phase 6 -- Ownership: which conversation is this frame about?

**Ships:** nothing visible. It is the phase without which phases 7 and 8 cannot
work, and it exists because the review found the ruling in D4 had no mechanism.

**Criteria:** AC-238's mechanism, AC-246, AC-247.

1. **A session owns a turn PER conversation.** `SatelliteSession.turn` is one
   slot and `owns()`, `send_for_turn()`, `_rearm()` and `_begin_turn()` all key
   off it (`hub.py:151`, `:234-262`), so a background conversation's reply is
   dropped before it reaches the socket.
   *Verify:* two conversations replying at once, both frames delivered.
2. **The page keys staleness per conversation.** It discards any frame whose
   turn is not `currentTurn` (`app.js:2789-2791`) -- the other half of the same
   drop.
   *Verify:* a browser check that a background reply appears in its own
   conversation while another is open.
3. **A device says what it is SHOWING.** The daemon has no way to know;
   `viewingChat` is page state (`app.js:1512`). One inbound frame, and
   `plan.md` 4.3 gains it.
   *Verify:* the field-level assertion this phase must write itself --
   `test_protocol_doc.py` compares frame NAMES only (`:67`), so a missed
   conversation id goes green.
4. **A MUTE path: `spoken=False` for the REMAINDER of a reply.** AC-238 needs
   it and it does not exist; the only way to go quiet today is `abandon()`,
   which disposes the child.
   *Verify:* look away mid-reply -- the speech stops, the text keeps arriving,
   and the child is still up. Asserted on the child, because that is the part
   that would be wrong.
5. **Decide, and write down, where a SPOKEN turn goes when no device is showing
   anything** -- the hands-free path, which is Butler's first sentence. And
   which conversation `ready` hands a fresh device (`hub.py:862-866` carries
   one `session` today).
   *Verify:* AC-246 -- a voice turn with the page closed lands in a
   conversation the daemon can name, and the answer says where when it is not
   the obvious one.
6. **A reply is spoken ONCE, not once per device**, when two devices show the
   same conversation. AC-14a's existing principle, arriving here because D4
   makes it reachable.
   *Verify:* two sessions showing one conversation; one speak bracket.
7. **An attachment pends against the conversation it was attached in**
   (AC-247). Files pend on the DEVICE's session today (`hub.py:221-232`).
   *Verify:* attach in conversation A, send in B, and the file is still A's.

---

### Phase 7 -- The status model on the wire

**BUILT 2026-09-16 (`329c965`, `d742f15`).** Ships: the five states, the
elapsed time and the last action -- as data, plus one place that draws it (the
open turn's own status line; the rail/list drawing all five per row is still
phase 8).

**Criteria:** AC-235 MET. AC-236 MOSTLY MET -- `GET /api/chats/{cid}/status`
serves a daemon-recorded start, so elapsed no longer resets on reload, but a
tab that reloads WHILE DRIVING its own turn does not yet re-attach to it
(`017f8a1` made the turn survive the disconnect; re-attaching the reloading
page to it was not built). AC-233 PARTLY MET -- the five states (`status_for`)
are derivable from the daemon for any conversation, but nothing on the page
shows them per row yet; only the single open turn gets a status line. The
frame's field is `chat`, not the `worker_id` this section originally reserved
-- there is no worker concept, a conversation is the unit, matching AC-246.

1. **A per-conversation status the DAEMON owns**, derived from what it already
   knows: a turn in flight, a dialog outstanding, a child up, a terminal event,
   a failure.
   *Verify:* the five states driven through the fake, read off the wire.
2. **A turn start is recorded** so elapsed is measured rather than guessed, and
   survives a page reload (AC-236).
3. **`ToolStart`/`ToolEnd` stop being dropped.** They are emitted
   (`harness_pi.py:277`, `:285`) and their only consumer has no branch for them
   (`brain_harness.py:731-745`).
   *Verify:* a tool event in, a last-action line out, and a conversation with
   no tool event showing its age instead of a sentence (AC-235).
4. **The frames go into `plan.md` 4.3.** `{type:"worker_event", worker_id,
   kind, detail}` is already reserved there and asserted as never-sent
   (`test_protocol_doc.py:234`) -- shipping it is moving that marker.
5. **Cheap, and it removes the worst moment in the design:** carry a
   FINISHED-since-you-last-looked marker, so a background completion is
   additive. Without it the rail's attention count going DOWN is the only
   signal that an agent finished, and that lasts until phase 9.

---

### Phase 8 -- The triage list, and stop

**Ships:** the mock.

**Criteria:** AC-228 completed, AC-238, AC-242, AC-243, AC-248, AC-232's badge.

1. **The list becomes four state groups with counts**, the Quiet disclosure,
   the "0 of N" rule, and the BY STATE / BY PROJECT toggle.
   *Verify:* `probe_page.py`'s no-match assertion is rewritten HERE -- it
   asserts headings go to zero, and a heading that always draws contradicts it
   directly.
2. **The cap meter and the per-project badges.** The badge carries the state's
   SHAPE or its word, not only a background colour -- the mock signals it by
   colour alone (`o2-filter.html:246-247`) and AC-232 forbids that.
   *Verify:* `test_seams.py`'s AC-174 gate extended to the new tokens.
3. **The page's state machine follows the VIEWED conversation.** This is the
   largest piece of work in the phase and it is what AC-243 costs:
   `escapeStops()` keys off the device's own `data-state`, so viewing a running
   conversation this device did not start, Escape clears the draft today.
   *Verify:* Escape and the stop control, driven in a browser, against a
   conversation this device did not start.
4. **`_barge_in` is scoped to the audible conversation.**
   `test_hub_bargein.py:194` goes red BY CONSTRUCTION -- it is AC-5's only
   cross-device witness. Rewrite it and keep the old assertion as the losing
   argument.
   *Verify:* the wake word with four agents running; one interrupted, three
   still up, asserted on the children.
5. **A rewind refuses while that conversation's agent is running** (AC-248).
   The control hides on the DEVICE being busy today (`docs/rewind.md` AC-194),
   which is the wrong test once ten exist.
6. **Two deletions, both from the review:** the rail's blue voice ring (under
   D4 it is the lit tile, by construction) and the per-row agent name
   (`harness` is ONE setting; ten rows would all claim it).
7. **Say what the composer's tools pill means with nine agents running.**
   `harness_tools` and `harness_model` are in `HarnessSpec` (AC-60), so flipping
   the pill changes every conversation's NEXT turn while the running nine keep
   the posture they started with. Either the pill says so or the setting becomes
   per-conversation; it may not silently mean both.

---

### Phase 9 -- The announcer

**Ships:** AC-14 and AC-14a, specified in August and never built. An agent that
finishes while nobody is looking says so, once, not once per device, with the
one-hour staleness drop already written into the criterion.

## 6. The verification plan

Section 5 carries the check on each STEP. This section is the instrument
inventory -- what already exists, what it currently proves, and where this
design collides with it. Read it before touching a probe, and keep the two in
agreement: a check that moves in one and not the other is the drift this
project keeps paying for.

### The four instrument conflicts, each needing a decision rather than an edit

1. `probe_page.py:477-478` asserts `len(drawn) == len(served)`. A filtering
   rail breaks it by design; D7 resolves it by comparing against the SCOPED
   serve.
2. `probe_page.py:564-566` asserts that on a no-match search the headings go to
   ZERO. The triage's "a heading always draws, at 0 of N" rule contradicts it
   directly.
3. `probe_ux.py:945-948` diffs `/api/shortcuts` before and after the run and
   asserts nothing moved. Any rail that persists a filter or a selection on
   first paint turns that probe from read-only into failing -- no selector
   involved.
4. `test_http_surface.py:170-201` is the only non-manual pytest a sidebar
   rewrite can redden: every `hud("x")` call site needs `id="x"` in the markup,
   and `#modeLabel` lives in `.sb-foot`.

### The tests that go red the moment a second conversation is live

`test_hub_bargein.py:194` (by construction), `test_chats.py:910` and `:180`
("the live conversation" becomes plural), `test_conversation.py:839`
(`hub.history.clear()` is global). `test_protocol_doc.py` will NOT go red for a
missing conversation id -- that is the gap, not the guard.

### e2e scenarios

Section L asserts exactly three `.agentbtn` per row and the literal text
"working here"; section M is the sidebar-resize story and does not survive a
fixed rail. Both are rewritten in the phase that moves them, not later
(AC-172). Section J stays last, because it kills the daemon.

### Instrument validation (D-13)

Break it, confirm the mutation applied, watch the RIGHT test fail, restore. The
three that matter most: AC-241 (make one conversation's turn write into
another's record), AC-237 (raise the cap by one and watch the refusal test
fail), AC-242 (leave `_barge_in` global and watch the new scope test fail).

### A safety rule this work found, and it belongs in the root `AGENTS.md`

The standing rule warns off `probe_page.py --turn` and `--write` against port
8140. **`--live` also writes**: it POSTs `theme` to `/api/settings`
[`probe_page.py:733-739`, gated by `LIVE`]. The rule names two flags and there
are three.

### What nothing here verifies

No screen reader, no real voice, no tablet. And nothing measures whether the
owner likes it, which is the criterion the model-picker row failed.

---

## 7. What this spec could have wrong

1. **The corpus may not justify the triage.** The owner has TWO chat records
   [MEASURED 2026-09-11]. A surface arranged around triaging ten parallel
   agents is built for a future that has not arrived, and phase 1 alone may be
   the whole fix. This is the first thing to put to the owner and it is why the
   build order front-loads it.
2. **AC-49's reversal may make unattended runs worse.** Today an agent that
   asks a question is told to carry on. After D2/A it waits for a human who may
   be asleep. The mock's triage assumes someone is watching.
3. **The cap may be the wrong unit.** Ten AGENTS is the owner's number, but a
   slot is held by a child that is up -- so a conversation waiting on a human
   holds a slot while doing nothing. That may be right or it may be how the cap
   fills with stalled work.
4. **The status model may be bigger than phase 5 makes it look.** Five states
   across an adapter boundary, a port union, a protocol frame and a page is the
   shape of work this project has previously found to be two commits and then
   five.
5. **The 64 px rail may not hold eight projects plus a footer.** The mock's own
   rail is 56 px and 685 px tall at eight projects, and it needed a sticky
   footer at 620 px of viewport height. At 64 px with Butler's existing three
   buttons in it, this is arithmetic nobody has drawn yet.
6. **One voice may be the wrong answer to ten agents.** The alternative nobody
   has priced is that background agents speak in turn through a queue, which
   the kokoro worker's own contention record argues against [`tts_kokoro.py:
   16-17`, a reply waiting 7887 ms] but which is what a person with ten
   employees actually experiences.
7. **The rail may not hold eight projects at a short viewport.** MEASURED on
   the mock at 1440x620: the rail's content is 689 px in 620, so it scrolls,
   and that is with the sticky footer the hide work added. At 64 px with
   Butler's three existing buttons in the column this has not been drawn.
8. **A background completion is announced by a number going DOWN.** Until phase
   9 the rail's attention badge counts running, waiting and error -- so an agent
   FINISHING makes the count smaller and nothing else happens. That is the
   weakest moment in the whole design and it lasts for eight phases.
9. **Answering a `select` needs a control the page does not have.** Phase 3
   says the options reach the page; what they are rendered AS is undecided, and
   the composer cannot do it.
10. **Two devices showing the SAME conversation both speak it** under D4, so one
   reply plays twice, offset. AC-14a's "spoken ONCE, not once per device" is the
   existing principle and this spec does not say how it is kept.

---

## 8. What this inherits from `docs/workspaces-ui.md`

That spec's AC-216..AC-227 are not restated here. What changes:

* **AC-216, AC-217 are ANSWERED by AC-231** (the same geometry criteria, now
  with the rail as the mechanism).
* **AC-218 (a conversation keeps its project when the row is edited) stands
  unchanged and is a prerequisite** -- a rail whose tiles are row ids filtering
  a list whose records hold paths is the drift that spec named.
* **AC-219 is restated as AC-229** with the same ruling and the same reason.
* **AC-220 (looking is not moving) stands** and is now sharper: a rail tile
  both filters and opens a conversation, so the click must not call `work_in`.
* **AC-221, AC-224, AC-225 stand unchanged** -- unfiled conversations, endpoint
  mode, and removing a project.
* **AC-222 (scope plus search) stands**, and the mock's "0 of N" heading rule
  is one way to satisfy it.
* **AC-223 stands** and phase 2 uses it for the hidden state.
* **AC-226, AC-227 stand**; AC-227 is restated for this surface as AC-244.
* **Section 0.3's defect is FIXED**, which reopens D5 of that spec and is why
  D6 here recommends the collapsed rail.
* **Its section 9.2 blocker list is confirmed TRUE at HEAD**, with two
  corrections: the interleaving is `ChatStore`'s single slot rather than
  `_Host`'s, and `_voice_for` is per-DAEMON rather than per-device.

---

## 9. The question protocol, read from the field (2026-09-12)

Phase 3 was specced from Butler's own code. This is what eleven shipped
products actually do, fetched rather than remembered, and it changed three
things. Read it before building phase 3.

### 9.1 The finding that changes the design: the clock may not be ours

pi's own RPC lets an extension attach a `timeout` to a dialog, and **pi
auto-resolves it AGENT-SIDE when it expires** -- "the client does not need to
track timeouts" [READ, `pi-coding-agent/docs/rpc.md`, and `dist/modes/rpc/
rpc-mode.js`]. Three consequences, all mechanical:

* The deadline is **passed through and drawn**, never invented. A question
  frame carries `deadline_ms` when pi gave one and nothing when it did not.
* A timed dialog's resolved default is **byte-identical to the cancel Butler
  sends today**, so nothing downstream can tell a timeout from a dismissal.
* `editor` has **no timeout path at all** and blocks forever. It is also the
  one dialog no Butler surface can render or dictate, which is the mechanical
  case for auto-cancelling that method alone.

`HARNESS_IDLE_S` must be suspended even for a TIMED dialog: a five-minute
`confirm` outlives Butler's three-minute silence deadline, and the turn would
die before the agent resolved it.

### 9.2 Nobody auto-denies on a clock

| policy | who | evidence |
|---|---|---|
| wait forever | Claude Code, Codex, Devin, OpenHands, Antigravity | Claude Code's docs, verbatim: "permission prompts don't time out"; Codex's source is a bare await with no timer |
| auto-DENY on a timer | **nobody** | `anthropics/claude-code#37913` is an OPEN request for the feature |
| auto-APPROVE on a timer | Jules, plans only | documented, number never published; a second reviewing agent shipped in Jan 2026 to compensate |
| auto-resolve to empty | Codex, non-blocking questions only, 120 s | complained about: "Codex later responds that the operation timed out, even though I eventually approved it" |
| deny-and-continue with no host | Claude Code headless | and passing a host WITHOUT the flag is what turns that into "block forever" |
| sleep to zero cost, wake on a message | Devin (30 min), Amp | both pay per-hour for a VM; Butler does not |

Measured, and the reason this matters: a census of 77 scheduled Claude Code
sessions found **8 of 23 dead (35%)** when spawned in the default permission
mode -- blocked on the first prompt with zero turns, one for 8h16m
(`claude-code#77817`). The block is real, not theoretical.

### 9.3 The recorded failure is VISIBILITY, not waiting

Every product that gates on a human waits. What their users complain about is
not the wait:

* Cursor -- an idle agent silently falls off the sidebar after a day or two and
  is reachable only by URL.
* Codex `#40310` -- the Android app cannot open a thread **precisely while it is
  waiting for approval**.
* Codex `#27006` -- mobile reads "Thinking" while the desktop sits blocked.
* Antigravity removed its Inbox in 2.0 and users are complaining on Google's
  own forum.

**The thing waiting for you becomes the thing you cannot see.** That is the
feature phase 3 is actually buying, and it is why the question must be STATE
that any device can draw rather than an event that can be missed.

### 9.4 The architecture that falls out

**Stored, in memory, in the daemon, and nowhere else.** One record per
outstanding question, held on the child and keyed by chat id:
`{qid, chat_id, method, title, message | options | placeholder | prefill,
asked_at_ms, deadline_ms | None}`. `qid` is **pi's own id, never one Butler
mints**.

**Nothing on disk, and the argument is `daemon/childproc.py`**: every child is
in a kill-on-close job object, so a daemon restart kills the agent that asked.
A persisted question is a question whose only possible answerer is already
dead, and the next reader would trust it. What DOES go on disk is a message in
the transcript when the question closes -- "Butler asked X, you answered Y" --
because that is what makes a reload and a rewind honest.

**On the wire: two frames out, one route in.**
* `{type:"question", chat, qid, method, title, message, options, placeholder,
  prefill, deadline_ms}` -- **BROADCAST** (`hub.broadcast`), never
  `send_for_turn`. `SatelliteSession.owns(turn)` drops anything for a turn the
  session does not own, and the device that answers is usually not the device
  that asked.
* `{type:"question_closed", chat, qid, how:"answered"|"cancelled"|"expired"|
  "agent_gone"}` -- what removes the card, and what prevents the Codex
  complaint above.
* **`POST /api/answer`**, not a socket frame -- `post_ambient`'s precedent. It
  is idempotent, it works from a device whose socket is reconnecting, and
  decisively it can RETURN A STATUS: a socket frame cannot tell the answerer
  their answer was too late.

**Recovery needs no protocol.** The daemon holds the question and serves it on
demand, so a reload and a second device both work through the same
`GET /api/session/current`, and a daemon restart has no agent left to answer.
This is MCP's own lesson arrived at the hard way: its 2026-07-28 revision
DELETED server-initiated requests and resumability precisely because
redelivery across a reconnect could not be made to work.

**A late or unknown answer is refused with `410 Gone` and the reason**, never
forwarded hopefully. pi drops an unknown id silently, so a forwarded stale
answer produces the Codex failure exactly: the human believes they approved and
the agent already took the default. The four reasons are all nameable -- the
agent resolved it on its own timeout, the conversation was stopped, the child
died, the daemon restarted.

**Timers:** at three minutes, nothing happens and two timers must be taught not
to fire (`HARNESS_IDLE_S`, and the page's `WATCHDOG_MS.thinking`). At about
thirty minutes, **say it out loud, once** -- Butler is the only product here
with a speaker already in the room, and 9.3 says invisibility is the recorded
failure. That is AC-14/AC-14a's first genuinely earned use. Overnight: no
expiry. An idle pi child is 162.3 MB and ten are about 2% of this machine's RAM
[MEASURED], and Butler pays no per-hour VM cost -- which is the only reason
Devin sleeps at thirty minutes. The cap refusal (AC-245) names the waiting
conversation and stop frees it.

### 9.5 Two criteria this adds

- **AC-249 (a question reaches every device, and survives a reload).** A
  question is broadcast to every connected satellite, not to the session that
  owns the turn, and a device that connects or reloads afterwards still sees it
  by asking. Asserted with TWO sessions, the second not owning the turn.
  *Fails as:* the phone never showing the question that the desk is blocked on
  -- the exact shape of Codex `#27006`. **Recoverable.**
- **AC-250 (a late answer is refused, never forwarded).** An answer naming a
  `qid` the daemon no longer holds is refused with the reason and never reaches
  the child.
  *Fails as:* the owner believing they approved while the agent already took
  the default -- Codex `#27458`, verbatim. **NOT recoverable.**

### 9.6 What the research could not find

No number for Jules's auto-approve timer. No documented maximum for Devin's
`waiting_for_user`. No first-party wire schema for Claude Code's
`--permission-prompt-tool` (the issue asking for one was closed without it). No
elicitation timeout in the MCP spec in ANY revision. The absences are findings:
nobody has published a deadline for a human gate, which is consistent with
9.2's result that nobody enforces one.
