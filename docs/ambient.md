# Ambient -- spec and build order

**PROPOSAL, written 2026-08-19.** `plan.md` remains the build contract; where
this document and `plan.md` disagree, the plan wins. Its criteria (AC-68..)
move into `plan.md` section 2 only as the phase is accepted.

Scope: a listening mode in which Butler holds the last N seconds of the room
and, when the wake word fires, already knows what was being said -- so the
owner does not have to restate the context of a request they were in the
middle of thinking out loud.

The owner named it `ambient` and set 30 seconds as the target.

**TWO CONCLUSIONS IN THIS DOCUMENT WERE OVERTURNED BY MEASUREMENT, and both
corrections are BOXED where the prose they replace still stands.** The first
is at the end of section 1 (the streaming path, not the model, is what made
30 seconds unaffordable) and the second is inside section 3 (the concurrency
the design assumed CORRUPTS both transcripts, so the back-fill is QUEUED).
Read the boxes; the prose around them is kept because the argument is the
useful half, not because it is still true.

**BUILT 2026-08-19**, steps 1-5 of section 6: the satellite's ring and its
indicator, `Asr.transcribe`, `ambient_seconds`, `POST /api/ambient`, the turn
wiring, and -- as of the same day -- the brains reading the field. Its
criteria (AC-68..AC-73) stay HERE until the owner accepts them -- `plan.md`
was not touched, still says none of AC-68..AC-73 is built, and remains the
contract. AC-70 is now whole; what step 3 called the missing clause is
section 4's note under AC-70. Section 5's defect is FIXED (AC-73). **AC-71 is
the one criterion here that is NOT built** -- the finalize budget scales and
an expired one is spoken, and the chunking that would remove the cost is not
written.

**SECTION 1'S CONCLUSION WAS OVERTURNED ON 2026-08-19 AND SECTIONS 3, 4 AND 6
ARE REWRITTEN AROUND THE NEW MEASUREMENT.** Read section 1 first anyway: the
tables in it are correct and the reasoning built on them was not, which is the
more useful half. In short -- the cost that made 30 seconds impossible was one
code path (`Session.stream()` over a re-encoding sliding window), the offline
`Session.run()` on the same session does 30 s in 1.94 s, and the two shapes
this spec used to offer collapse into one that costs nothing while resting and
needs no amendment to AC-1a.

Read `.references/ambient-mode.md` first. It is the review that produced this
document and it carries the prior art, the litigation, and the argument for
the name.

---

## 1. What was MEASURED before any of this was designed

`docs/harness-mode.md` exists because an unmeasured 9-10 s figure drove a
whole design and was wrong. The same thing happened here, in this document's
own first draft, and it is recorded rather than quietly corrected.

`.references/ambient-mode.md` R2 proposed extending the pre-roll ring to
10-30 seconds of audio and decoding it at wake time, and COMPUTED the cost as
about 4.3 s from transcribe.cpp's published 7-9x real-time CPU figure for
`parakeet-unified-en-0.6b`. That estimate was wrong by roughly 3x and the
design it implied does not work.

Measured on this machine, CPU backend, the real adapter, real speech tiled
from the recorded fixtures, engine warmed first
(`test/manual/spike_ambient.py`) [MEASURED 2026-08-19]:

**A. One utterance of N seconds -- what a naive long pre-roll produces**

| audio | decode | xRT | chars returned |
|---|---|---|---|
| 1 s | 0.09 s | 10.8x | 15 |
| 5 s | 1.69 s | 3.0x | 60+ |
| 10 s | 5.58 s | 1.8x | 60+ |
| 20 s | 13.64 s | 1.5x | 60+ |
| 30 s | 15.01 s | 2.0x | **0** |

**B. The same 30 s decoded as a series of short utterances on ONE session**

| chunk | decode | xRT | chars |
|---|---|---|---|
| 10 s | 17.90 s | 1.7x | 504 |
| 5 s | 11.08 s | 2.7x | 491 |
| 3 s | 8.86 s | 3.4x | 477 |

Four findings, and each one moves a design decision.

1. **This machine decodes at 1.5-3x real time, not 7-9x.** No published
   benchmark applies here. The vendor number is presumably a different thread
   count on a different part; ours also runs the CPU backend only because the
   GPU is contended with llama-swap.
2. **Cost is SUPERLINEAR in utterance length.** 1 s decodes at 10.8x and 20 s
   at 1.5x -- the same audio is seven times cheaper per second when it arrives
   in short pieces. Chunking is not an optimisation here, it is the
   difference between working and not.
3. **A single 30 s decode returns NOTHING.** As measured it exceeded the
   adapter's then-fixed 15 s finalize timeout and the adapter ended the turn
   with an empty final so the claim releases. That is a hard ceiling, and it
   failed in the worst available shape: silently, with a plausible empty
   answer. Partly fixed on 2026-08-19 (see the note under this list); the
   MEASUREMENTS above are unchanged, because the decode cost is the same.
4. **Even chunked, 30 s costs 8.9-11 s to decode.** As a burst at the moment
   the owner has just finished speaking and is waiting, that is unusable. A
   full daemon turn measures 1.61 s today.

**Finding 3 is also a LIVE DEFECT, and it is not about ambient at all.**
`TurnPipeline` finalizes once, at the end of the turn, so a turn's whole
audio is one utterance. `turn_mode=endword` -- what the owner runs -- allows
`END_WORD_MAX_MS = 300_000`, five minutes. Any long-form dictation past
roughly 22-25 seconds therefore returns an empty final, silently. Confirmed
with realistic audio: a 30 s turn that was HALF SILENCE still returned 0
characters, so the cost tracks audio duration, not speech duration. Recorded
as D-26; AC-71 below is the fix.

**PARTLY FIXED 2026-08-19, and AC-71 is still the fix.** The adapter's
finalize budget now scales with the audio fed to that utterance
(`FINALIZE_FLOOR_S` 15 s, `FINALIZE_PER_AUDIO_S` 2.0 s per second of audio,
`FINALIZE_MAX_S` 60 s), and an expired budget delivers `Final(failed=True)`,
which `hub` SPEAKS -- so the silent shape is gone and the ceiling moved from
roughly 22-25 s of audio to roughly 54 s. What did not change is anything in
the tables above: the decode still costs what it costs, a minute of waiting
is still a bad answer, and `endword`'s 300 s is still far above one decode.
Segmenting mid-turn remains the only thing that removes the cost.

> **THIS CONCLUSION IS FALSE, and the measurement that killed it is below.**
> It was drawn from table A without noticing that table A measures ONE code
> path -- `Session.stream()` -- which is the only one `stt_engine.py` has ever
> called. `catalog.json` maps this model to `stream_kind: "buffered"`, a
> sliding `[left|chunk|right]` window that re-encodes every second of audio
> many times. `Session.run()` sits on the same loaded session, takes the clip
> once, and had never been called anywhere in this repository.
>
> Same model, same fixtures, same machine
> (`test/manual/spike_ambient_offline.py`) [MEASURED 2026-08-19]:
>
> | audio | `stream()` (table A) | `run()` | chars returned |
> |---|---|---|---|
> | 1 s | 0.09 s | 0.08 s | 23 |
> | 5 s | 1.69 s | 0.28 s | 95 |
> | 10 s | 5.58 s | 0.58 s | 164 |
> | 20 s | 13.64 s | 1.27 s | 324 |
> | **30 s** | **15.01 s, 0 chars** | **1.94 s** | **507** |
> | 60 s | -- | 3.93 s | 980 |
>
> Two things follow and both invert what section 1 concluded. The cost is
> FLAT at 15-18x real time, so the superlinearity was never the model -- it
> was the window. And 30 seconds decodes in under two seconds and returns a
> real transcript, where the streaming path spent 15 seconds to return
> nothing.
>
> So a 30-second ambient CAN be produced at wake time, it costs about 2 s, and
> nothing has to stream while resting -- AC-1a survives untouched.
> `.references/ambient-2026.md` is the research this came from and section 9
> R3 is the design that replaces 3a and 3b.
>
> **Two traps were hit getting this number and they are recorded in the
> spike's own docstring**: `run()` takes float32 and returns `text=''` for the
> int16 the rest of the audio path carries, and `Result.__repr__` is ~214
> characters of timings whatever was transcribed -- so measuring
> `len(str(result))` gives a flat "213 chars" at every length and reads
> exactly like success. The first draft of this measurement reported a 16x
> speedup on an empty transcript.

The conclusion that followed at the time, superseded above:
a 30-second ambient cannot be produced at wake time. It can only exist if the
audio is transcribed AS IT ACCUMULATES. Decoding in 3 s chunks costs 8.86 s
per 30 s of audio -- about 30% of one decode stream running continuously,
before any VAD gating. That is affordable; a burst is not.

What IS affordable at wake time: about 5 seconds (1.69 s), and less than that
if it is decoded concurrently with the live utterance rather than in front of
it.

---

## 2. User story

As Butler's owner, I want Butler to have heard the last half-minute of what I
said, so that when I call it mid-thought I can say "send that" instead of
repeating the thought.

## 3. ONE shape: the buffer is audio, and it is decoded at wake

**This section replaced two.** It used to offer `ambient: short` (a ~5 second
ring decoded at wake) and `ambient: full` (continuous transcription into a
rolling text buffer), because the measurement said 30 seconds could not be
decoded at wake. That measurement was of the streaming path, the offline path
does 30 s in 1.94 s, and the choice those two shapes existed to force
disappears with it.

**The satellite keeps the last N seconds of PCM in a ring. On wake it sends
that ring. The daemon decodes it once, offline, while the owner is still
speaking. The text reaches the brain as context.**

What that buys, and it is everything the owner asked for:

- **The whole 30 seconds**, not five. 60 s costs 3.93 s if it is ever wanted.
- **Zero continuous cost.** Nothing is transcribed while resting, so the CPU
  is exactly as free as it is today.
- **AC-1a is untouched.** "Nothing streams while resting" survives literally:
  the ring lives on the SATELLITE and crosses the socket only when the wake
  word fires. The amendment 3b said would have to be argued for is not needed,
  which is the single biggest thing this measurement changed.
- **More accurate than the streaming path**, because an offline decode sees
  the whole clip with full context rather than a sliding window.

What it costs:

- **About 2 s of decode at wake**, paid CONCURRENTLY with the owner still
  talking. Section 3a budgeted 1.69 s for exactly this trade and accepted it;
  this is the same trade for six times the window.
- **Memory on the satellite.** 30 s of 16 kHz mono int16 is 960 KB. It is
  discarded when it is sent.
- **The four-minute case is out of reach.** 240 s would cost about 16 s
  [COMPUTED from the flat 15x]. That is a real limit and AC-68 puts it in the
  setting's help text rather than leaving it to be discovered.

### THE CONCURRENCY THIS DESIGN ASSUMED DOES NOT WORK [MEASURED 2026-08-19]

`test/manual/spike_ambient_concurrent.py`, cpu backend, 9 samples per
condition, overlap 60-100% on every concurrent row:

| condition | live -> final | live chars | back-fill | chars |
|---|---|---|---|---|
| live turn alone (5 s) | 1.71 s | 94 | -- | -- |
| back-fill alone (30 s) | -- | -- | 2.11 s | 507 |
| both, ONE shared `Model` | 2.87 s | **0** | 2.69 s | **0** |
| both, TWO `Model`s | 3.66 s | 94 | 3.04 s | 507 |

**Two `Session`s on one `transcribe_cpp.Model` return EMPTY transcripts on
both sides, 9 times out of 9, with no exception, no `Final.failed` and
nothing on stderr.** It is not sticky -- the next decode after the overlap is
correct -- so a shared `Model` is poisoned only while they overlap. That is
the worst available shape: a turn that answers nothing, intermittently, only
when ambient is on.

Two `Model`s fix correctness completely and cost **+1060 MB working set,
+1232 MB private commit** on top of the first.

**AC-69 IS NOT ACHIEVABLE ON THIS MACHINE EITHER WAY.** The live turn was
never as fast as its ambient-off baseline: 1.55-1.86x shared, 1.65-2.42x with
two `Model`s. Both decodes want the same CPU cores. There is real parallelism
-- 2.87 s against the 3.82 s full serialisation would cost -- but the final
still lands about 1.2 s late shared and 2.0 s late with two.

So "the back-fill is decoded concurrently and the turn's final arrives no
later than with ambient off" is false as written, and the design has a fork
the owner has to rule on. It is NOT closed and nothing is wired.

The one caveat that softens the timing half only: the spike pushes 5 s of live
audio into the engine instantly rather than at real-time pace, so the
streaming decode runs as a burst at maximum contention. A real turn spreads
that work over 5 wall-clock seconds and would overlap less densely, so the
latency penalty is an UPPER bound. **The corruption finding is unaffected by
this.**

> **THE OWNER RULED: QUEUE IT. [2026-08-19, and it is BUILT.]** The back-fill
> is decoded after the live turn's final -- when the live decode is finished
> and nothing is contending for the engine -- and before the brain is called.
> AC-69 is rewritten to that in section 4 and the losing argument is kept
> there.
>
> Three things the ruling DELETES, and none of them is to be built later as
> an improvement:
>
> * **No concurrency.** No `asyncio.gather` of the two decodes, and no second
>   `Model`. The corruption above is silent, and +1232 MB of private commit
>   is a strange price for a feature that ships off.
> * **No "drop it if the brain is called first".** With a queue the back-fill
>   is always ready before the brain, so that branch is dead the day it
>   ships.
> * **No claim that the final arrives no later than with ambient off.** It
>   does not, on this machine, either way. The cost is a stated budget --
>   about 2 s at 30 s -- and the default is off.
>
> What is built: `POST /api/ambient?turn=<id>` stores the clip against the
> turn a session owns, `Hub._overheard` decodes it between the final and the
> brain, and `TurnContext.overheard` carries it. Where the premise "paid
> while the owner is still speaking" appears above, it is the design this
> ruling overtook -- the trade it describes is real and was measured
> unavailable.

### What must NOT be confused with the pre-roll

The pre-roll (AC-1a, 416 ms) and the ambient ring are different things that
both carry pre-wake audio, and merging them would break the older one.

The pre-roll is part of the UTTERANCE: it is flushed into the live ASR stream
so the first syllables of a command are not eaten by the wake round trip, and
its byte count and declared header must stay equal by construction. The
ambient ring is CONTEXT: it is never fed to the live stream, never becomes
part of what the owner is treated as having said, and is decoded separately.

So the ring is sent as its own message and the pre-roll path is not touched.
Growing the pre-roll to 30 s instead would hand the streaming path 30 seconds
in one utterance, which is precisely the 15.01-seconds-and-zero-characters
failure in section 1.

---

## 4. Acceptance criteria (proposed, not yet in plan.md)

- **AC-68 (ambient is off by default, and its window is a number of
  seconds).** A `listen` group setting: off, or a window in seconds with 30 as
  the offered default. `off` is the shipped default and is today's behaviour
  exactly. The help text carries the MEASURED cost at the window chosen --
  about 2 s at 30 s, about 4 s at 60 s -- and says that beyond roughly a
  minute the wait stops being payable while the owner is still speaking. The
  three-valued `off`/`short`/`full` version of this criterion is retired with
  the two shapes it named.

- **AC-69 (the back-fill is decoded AFTER the final and BEFORE the brain, and
  the turn PAYS for it).** REWRITTEN 2026-08-19 by the owner's ruling; the
  version it replaces is kept below, because the argument that lost is the
  half worth reading. The clip is decoded when the live decode has finished
  and nothing is contending for the engine, and before `Brain.turn` is called.
  It costs about **2 s on a 30 s window and about 4 s on 60 s** [MEASURED
  2026-08-19, `test/manual/spike_ambient_offline.py`], and that cost lands
  between the owner finishing and Butler answering, on EVERY ambient turn.
  Confirmed through the turn path itself rather than inferred from the spike
  [MEASURED 2026-08-19, `Hub._reply` on the real cpu adapter, 3 rounds
  alternating]: final to brain is **0.0-0.1 ms with ambient off and
  1892-1988 ms with a 30 s clip stored** (507 characters of context). Off is
  not merely cheap, it is nothing: with no window nothing is stored, so there
  is no clip, no call and no thread hop.
  That is a BUDGET, stated rather than promised away: `ambient_seconds` puts
  the same two numbers in its help text so the owner chooses with them in
  front of them, and the shipped default of off pays none of it.

  **The losing argument, recorded the way `plan.md` AC-46/AC-59 record
  theirs.** This criterion used to read "the back-fill never delays the live
  utterance": decoded CONCURRENTLY, the turn's `final` arriving no later than
  with ambient off, and a back-fill that had not finished by the time the
  brain was called DROPPED rather than waited for. Every clause of it is
  retired and each one for its own reason.

  * *Concurrent* is not available. Two `Session`s on one
    `transcribe_cpp.Model` return EMPTY transcripts on BOTH sides, 9 times out
    of 9, with no exception, no `Final.failed` and nothing on stderr (section
    3). Two `Model`s fix correctness and cost +1232 MB private commit for a
    feature that is off by default.
  * *No later than with ambient off* was measured false either way -- 1.55x to
    2.42x the ambient-off baseline -- because both decodes want the same CPU
    cores. It was written before anything had been run, and it is the second
    time in this document that an unmeasured number drove a design.
  * *Drop it if the brain got there first* is DELETED rather than kept as a
    safety net. With a queue the back-fill is finished before the brain is
    called, always, so that branch could never execute -- and a branch that
    cannot run is not a rule, it is untested code sitting on the path of the
    one property the feature is gated on (AC-70).

  What the ruling costs is the sentence this design opened with: the 2 s is no
  longer free, because it no longer overlaps speech that is still happening.
  What it buys is that a turn with ambient on answers with the room's context
  or fails loudly, and never answers with an empty transcript nobody can
  explain.

- **AC-70 (what was OVERHEARD is not what was SAID TO Butler).** The ambient
  transcript is a separate field on `TurnContext`, never merged into
  `history` and never into the utterance. The sharper half of D-20: a remark
  made to another person in the room must never reach a coding agent as an
  instruction. The brain is told which is which.

  **WHOLE as of 2026-08-19**, and the last clause landed in three files.
  `dialogue.OVERHEARD_HEADING` is the one wording -- overheard, not addressed
  to Butler, not a request, only the request is the request -- and
  `dialogue.overheard_note(ctx)` the one builder, because a second copy of a
  safety label drifts and the drifted copy is the one a coding agent reads
  (D-12). `brain_endpoint` sends it as its own SYSTEM message after the
  history, so the user message stays byte-for-byte the utterance;
  `brain_harness` puts it in FRONT of the request in the one string the child
  gets, built once so the replay is still verbatim (4.4c). `HarnessOnlyBrain`
  gets it through the same converse half.
  An EMPTY note adds nothing at either call site. Ambient ships off (AC-68),
  so the normal turn's request has to be identical to the one built before
  this existed: `test_ambient_prompt.py` asserts that as the exact message
  list and the exact prompt string, and 19 tests in the fast tier redden if
  the harness prompt is wrapped unconditionally.

  **The ROUTER is deliberately NEVER shown it, and that is a ruling rather
  than an omission.** A `dispatch` verdict's `action.summary` becomes the
  prompt handed to a full-capability coding agent (AC-73), and that summary
  carries no label -- so a router shown the room can compose an instruction
  OUT of a remark made to somebody else, and by the time it arrives nothing
  can tell it from what the owner said. The argument on the other side is
  real and it loses: "send that" is unclassifiable without the referent. It
  is refused because resolving WHICH slice of the buffer a request means is
  out of scope in section 7 and nothing in the field ships an answer to it
  (`.references/ambient-mode.md` 5), so a router that had the room would be
  guessing -- and the one thing that must never be guessed is whether a
  sentence was an instruction. The classifier gets the utterance; the
  substantive brain gets the referent, labelled.

- **AC-71 (a single decode may never exceed the finalize ceiling).** No path
  hands the ASR more audio in one utterance than it can finalize inside its
  budget. Measured, 30 s returned an EMPTY final, which is the worst shape a
  failure can take. The chunk size is derived from the measurement and the
  derivation is written at the constant. Two thirds of the way there since
  2026-08-19: the budget scales with the audio and an expired one is spoken
  rather than silent (D-26), so what is left is the chunking itself -- the
  only half that removes the COST rather than bounding the wait.

- **AC-72 (the buffer is memory-only, capped, and VISIBLY on).** The ring
  never reaches disk, is bounded by wall-clock seconds, is discarded when it
  is sent or when the microphone stands down, and the page shows an indicator
  that cannot be mistaken for anything else while it is running. One action
  turns it off. This is what makes Butler a recorder; see
  `.references/ambient-mode.md` section 6 for what that category has cost
  other people, and section 7 of the same file for why the indicator carries
  more weight now than it did: the owner named this feature `ambient`, and
  that name says nothing about the retroactive part, so the page has to.
  Two things this criterion got EASIER when the design collapsed to one
  shape, and they are worth stating because they are the privacy argument:
  the buffer is AUDIO rather than text, so nothing is ever transcribed that
  the owner did not summon; and it lives on the satellite, so a room with no
  wake word in it never sends anything anywhere.

- **AC-73 (the router cleans the instruction before the harness sees it).**
  See section 5 -- this is a separate defect that ambient makes urgent.

## 5. The router's cleaned instruction was computed and thrown away

**FIXED 2026-08-19 (AC-73).** `hub._on_ask_confirm` now hands
`brain.proceed()` both strings -- the owner's words for the record and
`action.summary` as the instruction -- and `brain_harness.turn()` prompts the
child with the instruction when there is one. A BLANK summary passes None and
the prompt is the utterance, which is the router declining to rewrite. The
account below is why, and it is the piece that makes ambient worth having in
`brain + harness` mode.

The router already produces a cleaned-up instruction: a `dispatch` verdict
carries `action.summary`, and the system prompt tells the model the utterance
was DICTATED and may carry false starts and mis-hearings -- "judge what was
meant, not how it was transcribed" (`dialogue.py`).

That summary reaches exactly one place: `hub.py:916`, the line spoken when
the brain CANNOT do the work ("I would need to X, but I cannot do that yet").
When the brain CAN, `hub.py:919` hands `brain.proceed()` the RAW transcript,
and `brain_harness._answer` prompts the child with it VERBATIM
(`brain_harness.py:437`). The `converse` route is the same: raw text.

So the router's understanding is discarded precisely when it would be useful.
`plan.md` AC-28 already describes the fix -- a `router_role: assist` in which
the router's contribution is observable as "a cleaned-up instruction that
differs from the raw transcript", with a test asserting the cleaned
instruction reaching the harness. `settings.py` records that `router_role` is
deliberately ABSENT because shipping a switch before the thing it switches is
the AC-7b failure. The thing it switches now has a reason to exist.

The verbatim rule survives and is not in tension: it is about the REPLAY
(`brain_harness`, "never rebuilt from a text history, which is what silently
drops images"). Clean once, then replay the cleaned prompt verbatim.

With ambient this stops being a nicety. Thirty seconds of raw dictated
transcript, false starts and all, is not a prompt. Something has to turn it
into one, and the router is the piece that already exists, already runs on
every utterance, and already has the conversation (AC-64, AC-65).

---

## 6. Build order, tests first

Rewritten with section 3. Phase 4 used to be "`full`, and only as an explicit
AC-1a amendment"; there is no `full` and no amendment.

1. **AC-73 first, alone.** Independent of ambient, small, and worth having on
   its own. Red test: a `dispatch` turn whose cleaned summary differs from the
   transcript, asserting what reaches the harness. **DONE 2026-08-19**
   (`hub._on_ask_confirm` passes `action.summary` as `instruction`,
   `test/test_router_summary.py`).
2. **The offline decode, on the `Asr` port. DONE 2026-08-19.** A whole-clip
   transcribe, implemented with `Session.run()` and faked, with conformance
   properties for a long clip, a short one and a quiet one. This is the code
   path that had never been executed in this repository, so it was de-risked
   before it touched the audio path.
3. **AC-70 and the turn wiring. DONE 2026-08-19.** `TurnContext` gains a
   separate `overheard` field; `POST /api/ambient?turn=<id>` stores the clip
   against the turn a session owns and discards anything else (D-6);
   `Hub._overheard` decodes it after the final and before the brain (AC-69 as
   rewritten, NOT concurrently, and nothing is ever dropped for arriving
   late). `test/test_ambient.py`, the multi-turn case in
   `test/test_conversation.py`, and `test/manual/mutate_ambient.py` as the
   D-13 instrument.
   **The last clause landed the same day**: `dialogue.overheard_note` builds
   the labelled block, `brain_endpoint` sends it as its own system message
   and `brain_harness` puts it in front of the child's request, with the
   router deliberately blind to it. `test/test_ambient_prompt.py` asserts all
   of it ON THE WIRE, and `mutate_ambient.py` grew five rows for it.
4. **AC-68, the setting. DONE 2026-08-19.** `ambient_seconds` in the `listen`
   group: 0 (off) by default, 0-60 seconds, read per turn, with the measured
   2 s and 4 s in its help text. The key is the one the satellite already
   reads.
5. **The satellite's ring. DONE 2026-08-19 (commit 24869c8).** It grows to the
   configured window and is sent as its OWN request on wake. The pre-roll path
   is not touched -- see section 3.
6. **AC-72, the indicator.** Last only because it is the one part that cannot
   be tested by a machine: whether it reads as "this room is being held" to
   someone who did not build it.

**AC-71 is no longer on this list and that is a real change.** It said no path
may hand the ASR more audio in one utterance than it can finalize inside its
budget, and it was written when the ambient design was going to do exactly
that. This design never does: the back-fill goes to the offline path and the
live stream sees only what it always saw. AC-71 survives as a rule about the
STREAMING path -- `endword` mode still allows a 300 s turn against a finalize
ceiling around 54 s -- so it stays a live foot-gun worth closing, but it is no
longer ambient's problem and no longer blocks this phase.

## 7. Deliberately NOT in this phase

- Speaker diarization. Butler cannot tell the owner from the television, and
  `full` without it will overhear both.
- Deciding WHICH slice of the buffer a request means. Nothing ships an
  answer (`.references/ambient-mode.md` section 5). The fallback is the
  owner's own instinct: confirm before acting.
- Arbitrary wake words. That is a separate feature with a better solution
  than this one; see section 4 of the review.
