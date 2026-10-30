# Going back to an earlier point in a conversation

Owner's ask, 2026-09-10:

> "return to a previous point in a chat and re-send or continue from there,
> like the chat feature in `E:\PROJECTS\prototype`. investigate and apply same
> feature here please."

Criteria AC-189..AC-196. They stay here until the owner accepts them;
`plan.md` is still the contract.

**BUILT 2026-09-10.** What the build found that this spec did not predict is
in section 4a, and the first of the three is a design error this spec would
have shipped.

---

## 0. THE RULING THIS OVERTURNS, AND THE ARGUMENT THAT LOST

`daemon/hub.py` says, of AC-97:

> **A CORRECTION IS A NEW TURN, NEVER AN EDIT.** ... The original stays in the
> record, unedited: nothing here deletes, amends or annotates the turn being
> corrected, and there is no route in this daemon that could, because a record
> of what was SAID that can be rewritten is not a record (D-20).

That argument is not wrong and it is kept whole here, because half of it still
binds. What the owner has ruled is that a conversation is not only a RECORD; it
is also the agent's working context, and there is no way to say "forget the
last three exchanges" to a model except by taking them out of the transcript it
is resumed from. A record you cannot back out of is a conversation you have to
abandon and start again -- which is what the owner has been doing.

**What survives of D-20 is the part that mattered: nothing is silently
rewritten.** A rewind COPIES the whole original into a new conversation before
it truncates anything, and that copy is an ordinary row in the sidebar with a
title saying what it is. So the dropped turns are still on disk, still
readable, and reachable in one click. What is lost is the claim that a given
conversation FILE is append-only; what is kept is the claim that Butler never
destroys something the owner said without leaving it somewhere they can find.

`daemon/chats.py`'s module docstring says the schema "is not to be extended"
and names `parentChatId` and `parentMessageId` among the fields deliberately
not copied from the reference, because "Butler has one current conversation"
and no forking. That sentence is now false and those two fields arrive. The
reason it was written still holds for the other five (`title`, `titleSource`,
`pinned`, `archived`, `channelId`), and they stay out.

---

## 1. The reference, read rather than remembered

`E:\PROJECTS\prototype`, READ 2026-09-10. Its design is simpler than the word
"branching" suggests and should be copied nearly whole.

- **There is no message tree.** A conversation stays a flat array. The only
  lineage is two chat-level back-pointers on the copy.
- **Rewind = copy, then truncate.** `chat-store.js:216-257`: find the target by
  id, refuse a non-user target, `kept = messages[0..i-1]`, copy the FULL
  original into a new chat carrying `parentChatId` and `parentMessageId`, then
  overwrite the current chat with `kept` and null the session id. The target
  message itself is NOT kept -- it goes back to the composer.
- **The agent's memory is destroyed and re-seeded, never partially resumed**,
  because `--resume` is all-or-nothing. `claude-seed.js` renders the kept
  transcript into a system prompt capped at 12,000 characters, newest-first so
  the trim drops the oldest, and `claude-backend.js:159` seeds it only while
  there is no session id -- a guard that is self-limiting AND doubles as
  recovery when a stale resume is refused.
- **The client does not auto-send and does not mutate optimistically.** The
  prompt is loaded back into the composer and focused; the server broadcasts
  the new history and the client replaces its state wholesale.
- **Undo is opening the copy**, offered for 8 seconds. The truncated chat is
  not restored.
- **Their branch-switching UI was never built.** `forkFromMessage` exists end
  to end on the wire and no component calls it; the only navigation between
  branches is the ordinary history list plus a one-hop "open parent" link.

Their own decision log admits the thing that matters most here:

> rewind reverses the CONVERSATION, not the filesystem

-- found live, when an agent that had written a fact through a memory tool
could still recall it after the turn that wrote it was dropped. For Butler that
is sharper, not softer: the agent behind harness mode edits files and runs
commands.

---

## 2. Where Butler differs, and it decides the design

**Butler's `sessionRef` is a PATH BUTLER MINTS, not an opaque id.**
`ChatStore.session_ref` derives `<chat-id>-<harness>.session.json` from the
chat id, and `clear_session_ref`'s own docstring already records the
consequence: clearing it hands back a byte-identical path on the very next
call. So nulling the reference the way the reference implementation does buys
NOTHING here -- the next child would resume the same file, still holding every
dropped turn.

**So the rewind moves the session FILE onto the snapshot.** The dropped turns'
agent memory goes with the copy of the dropped turns, which is where it
belongs; the truncated conversation is left with no session file at its own
path, so the next child starts fresh. `ChatStore.delete` already reaps
`-<harness>.session.json` sidecars by glob off the record's filename, so
deleting the snapshot later takes the agent's memory of it too.

**And that makes a SEED necessary, not optional.** With the file gone the agent
has forgotten the KEPT turns as well, so the first prompt after a rewind
carries the kept transcript. The guard is the reference's, translated: seed
when the record has messages and the harness has no session file yet. Same
self-limiting property, same double duty as recovery.

---

## 3. The rulings

**R1. The live conversation only.** `POST /api/session/rewind` rewinds the
conversation Butler is holding. A past conversation is read-only (AC-164) and
staying that way: rewinding one would mean truncating a record the daemon is
not holding, and then either adopting it or leaving the owner looking at a
record nothing can continue.

**R2. User messages only, refused server-side.** The reference's rule and its
reason: an assistant message is not a point you can continue FROM, because the
thing that follows it is the owner's next instruction and there is nothing to
put in the composer.

**R3. The prompt goes to the COMPOSER and is never auto-sent.** The point of a
rewind is usually to say it differently.

**R4. The copy is an ordinary conversation.** No branch switcher, no tree view.
It appears in the sidebar with `parentChatId` set, and "undo" is opening it.
The reference never built the switcher either, and said so.

---

## 4. Acceptance criteria

**AC-189 `POST /api/session/rewind {"message": "msg-..."}` truncates the live
conversation at that message.** The target and everything after it leave the
record; the target's text comes back as `prompt`. 404 in endpoint mode on
`NO_CONVERSATION`, like the session routes beside it. FAILS BY: a record
truncated with nothing to show for it.

**AC-190 NOTHING IS DESTROYED: the whole original is copied first.** The copy
carries every message the conversation had, `parentChatId` and
`parentMessageId`, and it is an ordinary row in the sidebar. The copy is made
BEFORE the truncation, so a failure between the two leaves the conversation
whole rather than gone. FAILS BY: a rewind that loses the dropped turns --
which is the one irreversible failure in this spec and the whole of what
survives D-20.

**AC-191 A non-user target is REFUSED, and so is an id nobody has.** 400 naming
what was asked for, and the record is unchanged -- asserted by its CONSEQUENCE,
because a route that complained and truncated anyway satisfies every
status-shaped check. FAILS BY: an assistant message being rewindable, which
truncates a conversation to a point it cannot be continued from.

**AC-192 THE AGENT'S MEMORY GOES WITH THE DROPPED TURNS.** The harness session
file is MOVED to the snapshot's name and the snapshot's `sessionRef` points at
it; the truncated conversation is left with none. FAILS BY: a next turn that
still remembers what was dropped -- which is the failure the reference shipped
and fixed, and their own test header records it.

**AC-193 The first prompt after a rewind carries the KEPT transcript.** Seeded
when the record has messages and the harness has no session file, capped in
characters with the OLDEST dropped, and labelled as prior conversation rather
than as the request. FAILS BY: an agent that has forgotten the turns the owner
deliberately kept -- silently, because it answers anyway.

**AC-194 The page offers rewind on the owner's own messages and nowhere else**,
and not while Butler is thinking or speaking. Pressing it puts the text in the
composer, focused, and does NOT send. FAILS BY: a rewind that fires mid-stream
and tears down the child answering.

**AC-195 The page repaints from the daemon's answer.** The route returns the
truncated transcript, the new chat list and the session id, and nothing on the
page splices its own arrays -- `delete_chat`'s shape and `delete_chat`'s
reason: a page that edits its own copy can disagree with disk, a page that
repaints from the answer cannot.

**AC-196 A message is rewindable once Butler's record names it.** Message ids
already cross the wire; the page attaches them to the rendered turns from the
END of both lists at the end of every turn. A turn with no id yet carries no
control rather than a control that guesses. FAILS BY: a rewind aimed at the
wrong message, which truncates the wrong part of a conversation.

---

## 4a. What the build found that this spec did not predict

**4a.1 THE SEED GUARD IN SECTION 2 WAS WRONG, and the fake harness caught
it.** "Seed when the record has messages and the harness has no session FILE
yet" is the reference's guard translated too literally. It asks a
`Path.exists()` of a port that promises no such thing -- `caps.native_resume`
exists precisely because a harness may hand back an opaque id (AC-44) -- so
against any harness that keeps no file it seeded on EVERY turn. Measured: the
second prompt of every held-child test arrived wrapped in a
prior-conversation block, and the whole conversation would have been re-sent
as prior context forever, growing until the cap started cutting the oldest
turns off.
The guard is `messages and not sessionRef`. `sessionRef` is Butler's OWN mark
that a conversation has been handed to a child, set by `session_ref` and
cleared by `rewind` and by `clear_session_ref` -- so it answers the question
for a file-based harness and an opaque-id one alike, needs no disk, and is
self-limiting because `ensure()` sets it on the same turn the seed is read
for. `test_the_next_turn_after_a_rewind_is_seeded_and_the_one_after_is_not`
asserts the property and never the mechanism.

**4a.2 The seed has to be read BEFORE the utterance is recorded.**
`brain_harness.turn()` calls `record("user", text)` early on purpose -- so a
spawn that fails still leaves the question in the record -- which means by the
time the prompt is built the live question is already in the transcript the
seed renders. Recording first would put the question being asked into its own
prior-conversation block, where a model reads it as something it already
answered.

**4a.3 `probe_ux.py`'s AC-169 check reported a failure for a page that had
gained two features.** `.saybtn` (AC-183) and `.rewindbtn` reuse `.copybtn`'s
treatment deliberately, so a bare `#history .copybtn` matched four controls
per turn against a criterion counting two. Both probes negate now, and
`probe_ux.py` counts the two new controls as checks of their own, including
that each is one-sided by ruling. A selector that OVER-matches is the same
defect as the dead `#micWrap` selector, from the other side.

**4a.4 What was measured.** A scratch daemon on a free port with its own
`BUTLER_STATE`, a planted six-message conversation and a planted pi session
file. Before: three turns, all three of the owner's messages carrying a
`data-msg` off the daemon's record, one conversation in the sidebar. After
pressing rewind on the second question: the transcript truncated to one turn,
"wire up the third one please" in the composer and NOT sent, two conversations
in the sidebar, the record on disk holding two messages, the copy holding all
six with `parentChatId` and `parentMessageId` set, and the pi session file
renamed onto the copy with nothing left at the truncated conversation's own
path. Zero uncaught page errors. `probe_ux.py` 43/43 exit 0, `probe_page.py`
exit 0, pytest 920 passed.

---

## 5. Limits, stated up front

**5.1 TOOL SIDE EFFECTS ARE NOT REVERTED.** Rewind reverses the conversation,
not the filesystem. The reference found this live -- an agent recalled a fact
it had written through a memory tool after the turn that wrote it was dropped
-- and for Butler it is sharper, because the agent behind harness mode edits
files and runs commands in a real repository. A rewound conversation may be
talking to an agent whose work is still on disk.

**5.2 The context replay is lossy above the cap.** A long kept transcript loses
its oldest turns, silently, exactly as the reference does.

**5.3 There is no branch switcher.** The copy is a sibling row in the sidebar.
Undo is opening it, and the truncated conversation stays truncated.

**5.4 A just-spoken turn may not be rewindable yet.** The daemon appends the
user message inside `brain_harness.turn()`, after the `final` frame has already
gone out, so the page cannot learn the id from that frame without reordering
the turn. It learns it at the end of the turn instead. The reference's own main
chat has this exact gap and ships with it.

**5.5 This is the first route in this daemon that removes something the owner
said from the conversation they are in.** The copy is what makes that
acceptable, and the copy is the part of this that must never be allowed to
become conditional.
