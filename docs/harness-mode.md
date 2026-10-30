# Harness mode -- spec and build order for phase 7

**[HOW TO READ THIS DOCUMENT, 2026-08-18 -- SECTIONS 1-9 ARE HISTORY.]**
Phase 7 landed complete in the morning of 2026-08-18 and the owner replaced
three of its seven steps the same afternoon. Sections 1-9 are the SPEC AS
WRITTEN and are kept unrewritten on purpose: they are the argument, and
section 9 in particular is this plan's own record of what it got wrong,
which is worth more intact than corrected. Sections whose claims the rulings
overtook carry a one-line note at their head saying which ruling did it.
Section 10 is the four rulings, and it is no longer a proposal -- it is what
shipped.

**The four rulings, and where each one landed** (10.4 has the amended
criteria, 10.5 the costs):

| ruling | what it did | criterion |
|---|---|---|
| 1 | the spoken write grant is DELETED whole; the child runs at full capability, `bash` included, in every mode | AC-29 RETIRED |
| 2 | a child is spawned PER MESSAGE and disposed at the turn's end | AC-46 REPLACED |
| 3 | sessions are ONE flat JSON file per conversation (`daemon/chats.py`) | AC-35 REWRITTEN |
| 4 | every criterion the first three contradict is amended with the losing argument recorded | AC-31, AC-44, AC-49 amended |

**PHASE 7 DONE 2026-08-18.** All seven steps landed, in order, in twelve
commits (`4635347` through `c9f892d`), and the four rulings landed in five
more (`653a44d` through `cc483d1`). Suite 504 passed in 251 s at the end of
the build; 465 passed in 301 s after the rulings [MEASURED 2026-08-18, three
full runs, the two earlier ones 464 passed with one LIVE pi row skipped]. AC-46 to AC-49 have moved into `plan.md`
section 2 beside AC-29, AC-31, AC-35, AC-36 and AC-44, and two of the nine
are honestly PARTLY met -- AC-44 because `caps.native_resume` is read by
NOTHING until a second real harness exists, AC-49 because it is proven on
the pi transport and nowhere on the caller. `plan.md` now carries the
contract; what survives here is the argument and section 9.

Written 2026-08-17 as a SPEC. `plan.md` remains the build contract; where
this document and `plan.md` disagree, the plan wins.

Scope: the fifth port (`Harness`), the first real adapter (pi), talking to a
coding agent instead of an endpoint (`brain_harness`), the permission grant
that lets it write, and the rule that no child outlives the daemon. It covers
plan.md section 5 step 7 pieces 6 and 7, and `docs/architecture-seams.md`
phase 7.

Six owner decisions were taken on 2026-08-17 and are folded in rather than
re-opened: the phase runs all the way to a usable harness mode; a write is
confirmed ONCE, up front, for the conversation rather than per turn; the child
is held open between turns; pi goes first; the model comes from the harness's
own registry; and the orphan fix covers the Kokoro sidecar in the same
mechanism.

---

## 1. What was MEASURED before any of this was designed

[Overtaken in one place by RULING 2 (section 10): the 1.5-2.3 s spawn
measured here was the reason to hold a child open, and it is now paid on
EVERY question by design. The number is unchanged and is what AC-46's
accepted cost is quoted from.]

Every number here was taken on this machine on 2026-08-17 with
`pi` 0.65.0. Nothing in section 3 rests on a number that was not measured.

**pi has an RPC mode built for exactly this** [READ, `docs/rpc.md` in the
installed package]. `pi --mode rpc` speaks LF-delimited JSONL over stdin and
stdout: commands in (`prompt`, `steer`, `follow_up`, `abort`, `new_session`,
`switch_session`, `fork`, `compact`, `bash`), responses out with the
correlating `id`, and an event stream. Its own documentation warns that a
generic line reader is NOT protocol-compliant because it splits on U+2028 and
U+2029, which are legal inside JSON strings. Python's `readline` splits on
`\n` alone, so Butler is compliant by construction -- and the rule goes in the
adapter's docstring so nobody "improves" it later.

**pi restricts tools with an ALLOW-LIST, at spawn** [MEASURED, `pi --help`].
`--tools read,bash,edit,write,grep,find,ls` names what is enabled; `--no-tools`
disables everything. This is the AC-29 mechanism, and it is an allow-list by
construction rather than by discipline. It cannot be changed after spawn.

**pi has no permission prompts at all** [READ, README "Philosophy": "No
permission popups. Run in a container, or build your own confirmation flow
with extensions."]. So a per-write interception simply does not exist for this
harness. The owner's up-front-confirmation decision was the only shape pi
could have supported.

**Timings, from a Python client driving a real child.** The turn column is
from the first spike [MEASURED, three runs]. The startup column is a
RE-MEASUREMENT taken after step 7.2's review, because the spike's 9-10 s could
not be reproduced at any timing point [MEASURED 2026-08-17, three runs per
row].

| | to prompt accepted | to `agent_end` |
|---|---|---|
| `llama-swap` / Qwen3-4B-Instruct-2507 (local) | 1.72 s | 35.9 s and 37.6 s |
| `claudebridge-local` / claude-haiku-4-5 | 1.53 s | 10.6 s (1.55 s of turn) |
| `codexbridge-local` / gpt-5.4-mini | 1.54 s | 8.2 s (three turns, tools) |

Two things follow, and they are the load-bearing findings of this section.

1. **Startup is 1.5-2.3 s, and the 9-10 s this section used to claim is not
   reproducible** [MEASURED 2026-08-17, 21 runs: twelve varying the working
   directory, nine varying the provider and model]. Spawn to the child
   ACCEPTING a `prompt` command was 1.51-2.28 s in every one of them, and the
   two boundaries are a millisecond apart -- a child that answers `get_state`
   takes a prompt in the same breath, so there is no later point where the
   older number could have been hiding. Three explanations were tested and
   none of them holds:
   - the WORKING DIRECTORY, on the theory that pi discovers extensions,
     skills, prompt templates and context files from it: the owner's home
     `C:\Users\user`, which carries a 2.4 GB `.claude` tree beside a `.pi`
     one, measured 1.87 s mean; this repository 1.98 s; an empty temporary
     directory 1.78 s; four runs each. The spread between directories is
     smaller than the spread within any one of them. `harness_cwd` is
     owner-configurable, so this was the answer worth having, and it is no.
   - the PROVIDER and MODEL, since the old table's two rows differ in both
     and both claimed 9-10 s: the same two pairs now measure 1.72 s and
     1.53 s, three runs each, against a machine where one of those bridges
     was not even answering.
   - a cold first spawn: the slowest run of all (2.28 s) was a first run, but
     a first run elsewhere was the fastest of its four, so warming accounts
     for a few hundred milliseconds and not for eight seconds.

   What produced 9-10 s is unknown, and the number is replaced rather than
   explained -- an unreproducible measurement is not evidence. The decision it
   was used to justify does not change: 1.5-2.3 s of silence after a question
   reads as a dead microphone, so the child is still spawned and warmed at
   BOOT (D-10, exactly as the ASR and TTS are). What changes is that the
   budget is not a reason to refuse anything else, and that AC-46 needed a
   restatement (section 3) -- a criterion phrased against a number that turned
   out to be wrong could no longer fail.
2. **The model choice dominates the turn, by 20x.** The local 4B spends about
   23 s evaluating pi's system prompt before it emits a word -- confirmed twice
   with the model already resident, so it is prompt evaluation and not a load.
   The same turn on a fast bridged model is 1.55 s. Butler does not warn about
   this (4.4b: the owner chooses and Butler does not second-guess), and it is
   recorded here so the choice is informed.

**Some providers do not stream** [MEASURED]. The `claudebridge-local` turn
produced ZERO `text_delta` events: `message_start`, `message_end`, then
`turn_end` carrying the whole message. An adapter that maps only `text_delta`
would have spoken nothing at all on that provider, which is the
silence-reads-as-ignoring failure. The text must be taken from
`turn_end.message` when no deltas arrived.

**Aborting works and leaves the child usable** [MEASURED]: an `abort` during a
long generation settled in 1.51 s and the next prompt on the same child
answered normally. **The child exits 0 on stdin EOF** -- which is exactly what
the predecessor relied on, and D-24 says that is not enough (a hard kill of
butlerd never closes stdin).

**The event stream, as it actually arrives** [MEASURED]:

```
extension_ui_request        (x3, unprompted, before the first response)
response {command: prompt, success: true}
agent_start
turn_start
message_start / message_end
message_update  -> assistantMessageEvent.type in {text_start, text_delta, text_end}
message_end
turn_end        {message, toolResults}
agent_end
```

Tool calls add `tool_execution_start` / `_update` / `_end`, each carrying
`toolCallId` and the end carrying `isError` [READ, docs/rpc.md]. Both matter:
the predecessor self-minted step ids and kept ONE current slot, so parallel
tool calls overwrote each other (8 of 19 starts never closed), and it
hardcoded `outcome: 'ok'` so an error outcome was unreachable. pi gives Butler
both for free; Butler uses them.

**`extension_ui_request` arrives unprompted.** Three landed at startup before
anything was asked. The dialog-shaped ones (`confirm`, `select`, `input`,
`editor`) expect a reply on stdin and would block the turn forever if ignored.
The adapter answers every request it does not implement with the documented
cancellation response.

---

## 2. User story

[Overtaken by RULING 1 (section 10): the story's "let it change something
after I say so" was the spoken write grant. There is no consent step. The
agent changes things because the router routed the utterance to it.]

> As the owner, I want to TALK to a coding agent hands-free -- ask it about a
> repository, have it read code and answer, and let it change something after
> I have said yes once -- so that the work I would otherwise type at a
> terminal happens while my hands are somewhere else.

The thing being built is not "run a coding agent". It is the seam that makes
which agent replaceable, plus the permission boundary that makes a
tool-enabled brain safe to point at a microphone with a 0.56-1.5 per hour
wake-word false-accept rate and no speaker verification until Phase 4.

---

## 3. Acceptance criteria

[Overtaken by RULINGS 1, 2 and 3 (section 10). AC-29 is RETIRED, AC-46 is
REPLACED by its own inverse, AC-35 is REWRITTEN, and AC-31, AC-44 and AC-49
are amended. The criteria BELOW are the versions that lost; the ones in
force are 10.4's, and they live in plan.md section 2. AC-36, AC-47 and
AC-48 are untouched by any ruling.]

AC-29, AC-31, AC-35, AC-36 and AC-44 already exist in `plan.md` and are
carried unchanged except where noted. AC-46 to AC-49 are new and come from
what the measurements and the owner's decisions forced.

- **AC-44 (a harness is an adapter behind one contract).** Unchanged from
  plan.md 4.10. `brain_harness` and, later, the Worker Manager consume only
  the `Harness` protocol; resume is decided by `caps.native_resume`, never by
  name; adding a harness is a new adapter file, a registry line and a
  conformance row. Demonstrated by the fake and pi passing the same suite.

- **AC-29 (a harness may only do what the mode allows, and the mode is
  frozen), REFINED.** The refinement is the owner's: a write is confirmed once
  for the CONVERSATION, not once per turn, because a confirmation on every
  turn is an interruption nobody would keep. Concretely:
  - `paranoid`: the child is spawned read-only and no grant is possible. A
    write attempt fails at the tool layer, which the owner hears.
  - `secretary` (the default): the child is spawned read-only. The first
    utterance that needs to write produces a spoken repeat-back; an
    affirmative GRANTS write tools for the rest of this session, and Butler
    disposes the read-only child and respawns it write-capable on the same
    session file. A refusal, silence past the timeout, or an unparseable
    answer leaves the read-only child exactly as it was.
  - `trusted`: spawned with the full tool set, no confirmation.
  - The tool set is derived once, at spawn, and frozen for the child's life.
    Raising the permission mode mid-conversation does not widen a running
    child; the grant is the only thing that widens it, and it is spoken.
  - LOWERING the mode revokes immediately: the write-capable child is disposed
    and respawned read-only, mid-conversation, without being asked twice. It
    also cancels a repeat-back that has not been answered -- that question was
    asked under a mode the owner has left.
  - The grant is scoped to the WORKING DIRECTORY it was given in as well as to
    the conversation. Moving `harness_cwd` revokes it and the next write asks
    again, for AC-31's reason: the cost of guessing is a tool-enabled agent
    writing in the wrong repository.
  - ONE authority decides which mode a write is judged against, and it is the
    mode the running child was DERIVED from rather than the mode store. The
    two disagree after a UI raise -- the store ratchets both ways, the child's
    mode only down -- and reading one for the ceremony and the other for the
    tool set skips the repeat-back for a write the child cannot perform.

- **AC-31 (the working directory is spoken, bounded, and never guessed).**
  Unchanged. "Work in project X" changes the harness working directory when X
  matches the configured list, and is REFUSED ALOUD when it does not. The test
  includes a near-miss name. A directory change disposes the child and
  respawns it there, because pi's cwd is fixed at spawn.

- **AC-35 (a harness conversation survives a daemon restart).** Unchanged in
  substance, narrowed in mechanism by what pi is: the session lives in a file
  named by `--session <path>`, Butler owns the path, and a restart re-attaches
  to the same file. The page shows the transcript from Butler's own
  `events.jsonl`, which is the record; pi's session file is the harness's
  context, which is losable. `native_resume` is FALSE for pi in the sense the
  capability means (handing back an opaque id): pi resumes by being pointed at
  its own session file. That is a capability difference the caller must read
  from `caps`, never from the name.

- **AC-36 (a harness may not outlive the daemon).** Unchanged, and extended to
  every child Butler spawns. Kill butlerd with `TerminateProcess` while a
  harness session is open and a Kokoro sidecar is running, and neither
  survives -- asserted by PID against the process tree, not by the absence of
  an error. The Kokoro sidecar is IN this criterion because it is already
  failing it: seven orphans were found on 2026-08-17, each holding a worker
  child, because `atexit` does not run under a hard kill.

- **AC-46 (a routine harness turn never pays for starting the harness).** The
  child is spawned and warmed at boot, beside the ASR, TTS and Brain warmups,
  and is ready before the first word. The criterion forbids a spawn on the
  ROUTINE path -- a turn against a healthy child whose spec still matches --
  and PERMITS one on the two recovery paths, which is a correction of the
  earlier absolute wording rather than a loophole in it. The exceptions,
  and why each is the lesser silence:
  - the child is GONE or WEDGED (it died between turns, it will not take the
    prompt, or it never ended an interrupted one). The alternative to
    respawning is a harness that stays dead until someone restarts the
    daemon, so the choice is 1.5-2.3 s of silence once against every turn
    from now on being refused.
  - the owner CHANGED what the child must be (`harness_model`, `harness_cwd`,
    and from 7.5 the spoken grant). Everything about a child is fixed at
    spawn, so the alternative is a control that saves, reports success and
    changes nothing (AC-7b).

  What stays absolute is the routine path, and that is what is asserted:
  (a) a turn against a healthy child records NO `start()`, read off the fake's
  record of every spec it was started with -- it fails however fast a spawn
  is; (b) live, the first turn after warm is
  within 1.5 s of a LATER turn on the same child, asked the same fixed short
  question, where 1.5 s is the measured spawn cost (section 1) and a turn that
  secretly spawned could not come in under it. The first phrasing here --
  "the first turn must not carry the measured 9-10 s" -- is deleted rather
  than adjusted: it was written against a number that re-measurement put at
  1.5-2.3 s, so a daemon spawning a child inside every single turn would have
  passed it. A criterion that cannot fail is not one. A warm failure is loud
  and never fatal (D-1), and harness mode then refuses out loud rather than
  spawning inside a turn: a child that has NEVER started is a configuration
  problem the owner has to be told about, not something to recover from
  silently on every utterance until they notice.

- **AC-47 (a harness turn is never silent).** Every turn ends by speaking
  something: the answer when there is one, a fixed line when the harness
  produced no text, and the failure when it failed. Asserted against a
  provider that emits NO `text_delta` events -- measured, `claudebridge-local`
  does exactly that -- where the text must be recovered from `turn_end`. A
  turn that produces sound only when the model happens to stream is the
  silence failure with a race attached.

- **AC-48 (one terminal event per prompt, and one announcement).** Exactly one
  of `agent_end` or `error` reaches the caller per prompt, asserted in
  conformance AND on the caller (D-18), because the predecessor's backend
  emitted `error` only on a JS exception and a provider failure held its turn
  lock forever. `agent_end` fires once when the whole loop settles, never per
  `turn_end`: for a voice assistant that is the difference between announcing
  once and announcing five times.

- **AC-49 (an unanswered harness dialog cannot wedge a turn).** Any
  `extension_ui_request` Butler does not implement is answered with the
  documented cancellation response rather than ignored. Asserted by injecting
  a `confirm` request mid-turn and watching the turn still finish. Measured
  motivation: three such requests arrive unprompted before the first response
  even in the simplest configuration.

---

## 4. Gherkin

[Overtaken by RULINGS 1 and 2 (section 10). Every scenario with a spoken
grant, a repeat-back or a parked confirmation describes a ceremony that no
longer exists, and the scenarios that assume a child already running now
start one. 10.2 has the turn as it actually runs.]

```gherkin
Scenario: Talking to the harness (AC-44, AC-46, AC-47)
  Given brain_mode is harness and the pi child was warmed at boot
  When the owner asks "what does the endpointing module do"
  Then the router routes it to the harness rather than the endpoint
  And the answer is spoken
  And the first turn does not pay the harness's startup cost

Scenario: The provider does not stream (AC-47)
  Given a harness model that emits no text_delta events
  When a turn completes with its text only in turn_end
  Then that text is spoken
  And the session re-arms

Scenario: One yes covers the conversation (AC-29)
  Given the permission mode is secretary and the child is read-only
  When the owner says something that needs a write
  Then Butler speaks the repeat-back and nothing is written
  When the owner says yes
  Then the read-only child is disposed and a write-capable one is spawned
  And the same session file carries the conversation across the respawn
  And the NEXT write in this conversation is not asked about again

Scenario: A refusal changes nothing (AC-29)
  Given the repeat-back has been spoken
  When the owner says no, or answers with anything that is not plainly a yes
  Then no child is respawned, no tool set widens, and the refusal is SPOKEN
  # Spoken because it happens inside the turn that answered. A refusal
  # anywhere in the utterance outranks an affirmative anywhere in it: "yes,
  # cancel that" is somebody changing their mind mid-breath, not consent.

Scenario: The offer expires (AC-29)
  Given the repeat-back has been spoken
  When the owner says nothing until the timeout
  Then no child is respawned and no tool set widens
  And the expiry notice is SHOWN, not spoken
  # This one lands BETWEEN turns, and every audible path in the daemon is
  # turn-scoped: the page clears its turn when one ends and drops frames for
  # a turn it does not own (D-6), so audio to an idle satellite is discarded
  # at the client. Making it audible needs Phase 2's announcer (AC-14) and a
  # frame that is not turn-scoped -- the named gap, not a workaround.

Scenario: Barge-in during the repeat-back (AC-29, D-1)
  Given Butler is part way through speaking "you want me to X, yes or no"
  When a wake candidate abandons that turn
  Then nothing is parked, however long the engine aborts take
  And the interrupting utterance is answered as itself, never as consent

Scenario: Lowering the mode revokes without being asked (AC-29)
  Given a write-capable child is running
  When the permission mode is lowered to paranoid
  Then the child is disposed and respawned read-only
  And a repeat-back still awaiting an answer is cancelled with it
  And the owner is told, once -- SHOWN rather than spoken, the same gap

Scenario: Raising the mode does not widen a running child (AC-29)
  Given a read-only child is running in secretary
  When the mode is raised to trusted in the web UI
  Then the running child stays read-only until it is respawned
  And a write is still repeated back, because the mode a decision is judged
    against is the one the running child was derived from
  And no write happens without the spoken grant

Scenario: Working somewhere else (AC-31)
  Given the projects list holds "butler" and "smart crawler"
  When the owner says "work in smart crawler"
  Then the child is respawned with that working directory
  And a write grant given in the previous directory does not come with it
  When the owner says "work in smartcrawlr"
  Then it is refused ALOUD and the working directory does not change

Scenario: The conversation survives a restart (AC-35)
  Given a harness conversation with three turns
  When butlerd is restarted
  Then the transcript is redisplayed from Butler's own events.jsonl
  And the next utterance continues the same pi session file
  And the page says which of new, resumed or lost the context is -- never
    "replayed", because Butler feeds nothing back into a harness

Scenario: Nothing outlives the daemon (AC-36)
  Given a harness child and a Kokoro sidecar are running
  When butlerd is killed with TerminateProcess
  Then neither process is alive a second later, asserted by PID

Scenario: Barge-in during a harness turn (AC-48, D-4)
  Given the harness is mid-turn
  When a wake candidate arrives
  Then harness.abort() is called before the reader task is cancelled
  And exactly one terminal event is delivered for that prompt
  And nothing from the abandoned turn is spoken

Scenario: A dialog nobody implemented (AC-49)
  Given the harness sends an extension_ui_request of type confirm
  When Butler does not implement it
  Then it answers with the cancellation response
  And the turn still reaches its terminal event
```

---

## 5. The port

[Overtaken by RULINGS 1 and 2 (section 10). `HarnessSpec.tools` and
`HarnessCaps.tool_allowlist` are DELETED -- nothing reads a tool set once
tools are unrestricted, which is section 9's own rule about capabilities
with no method -- and the lifecycle sentence is now "started and disposed
per prompt". `daemon/ports.py` is the contract as it stands.]

```python
@dataclass(frozen=True)
class HarnessCaps:
    """What a harness can do, tested by CAPABILITY and never by name (AC-35).

    `native_resume` says the harness hands back an opaque session id to
    resume by; pi resumes by being pointed at its own session FILE, and
    claude by `--resume <id>`, so the two need different callers -- which is
    exactly what a name test would get wrong on the third harness.
    """
    native_resume: bool
    steerable: bool          # accepts a message mid-turn (pi: `steer`)
    tool_allowlist: bool     # tools are named at spawn (pi: `--tools`)
    images: bool


@runtime_checkable
class Harness(Protocol):
    async def start(self, spec: HarnessSpec) -> None: ...
    def subscribe(self) -> AsyncIterator[HarnessEvent]: ...
    async def prompt(self, text: str) -> None: ...
    async def abort(self) -> None: ...
    async def dispose(self) -> None: ...
    @property
    def caps(self) -> HarnessCaps: ...
    @property
    def info(self) -> dict: ...
```

`HarnessSpec` is the frozen record of what this child IS: model, cwd, the
tool allow-list derived from the mode, and the session file. It is built by
the daemon, never by the adapter, and a new one means a new child.

`HarnessEvent` is the normalized set, transposed from an earlier prototype's contract
(plan.md 4.4c) and confirmed against pi's real stream: `text_delta`,
`tool_start`, `tool_end`, `turn_end`, `agent_end`, `error`. Tool events carry
the HARNESS's own id and `tool_end` carries a real outcome, because both were
defects in the predecessor.

`HARNESSES: dict[str, type]` joins `SOURCES/ASRS/TTSS/BRAINS` in ports.py, and
the module docstring says FIVE seams in the same commit as the code (D-12).

---

## 6. Build order, tests first

[Overtaken by RULINGS 1, 2 and 3 (section 10) in three of its seven steps.
Step 7.5 (the grant) is DELETED whole, 7.4's held-open child became one per
MESSAGE, and 7.6's session directory became one flat chat file. Steps 7.1,
7.2, 7.3 and 7.7 survive as built. 10.6 is the order the rulings shipped
in.]

Each step lands green before the next starts, red test first. For every step
this section names the red test and WHY it is red, the files, what must keep
passing, what could break, the D-13 mutation check, the verification command,
and the one condition that would stop the step rather than push it through.
Suite baseline going in: 304 passed, 0 xfailed [MEASURED 2026-08-17, plan.md
section 5].

Two rules apply to every step and are stated once. First, the mutation
harness reads pytest EXIT CODES, never its output: a previous session's
harness matched `line.startswith("FAILED")`, pytest colours that word, and
every mutation was reported as surviving -- the instrument lied exactly the
way D-13 warns. Second, every step ends with a DOX pass: the owning
AGENTS.md is updated in the same commit as the behaviour it describes
(D-12), and the specific documents each step touches are listed with its
files.

### 7.1 The port, the fake, the conformance suite

No consumer yet, on purpose: the suite is written against the fake FIRST,
exactly as the Asr suite predated its second implementation, so the contract
is pinned before any code exists that could bend it toward one engine.

**The red test.** `test/conformance/test_harness_conformance.py`, the whole
file, written before any code. It goes red at IMPORT: `from daemon.ports
import HARNESSES, Harness, HarnessCaps, HarnessSpec` raises ImportError.
That is "fails because the feature is absent", and it is the right reason:
the daemon still boots untouched, because nothing consumes the port until
7.4 -- which is the ordering `docs/architecture-seams.md` phase 7 chose so
that "could break: nothing" is literally true here. The tests that matter:

- `test_satisfies_the_protocol` -- `isinstance(h, ports.Harness)`.
- `test_caps_is_a_frozen_capability_record` -- the four booleans of section
  5, frozen.
- `test_exactly_one_terminal_event_per_prompt` (AC-48) -- the fake scripts a
  two-turn, two-tool answer; exactly one of `agent_end` / `error` arrives,
  after the last `turn_end`, never per turn.
- `test_abort_ends_the_prompt_once_and_the_child_answers_again` -- abort
  mid-prompt is not a second terminal event, and the next prompt works
  (the measured behaviour of section 1, made a contract).
- `test_dispose_then_start_is_a_fresh_child` -- pinned HERE because 7.5's
  grant respawn is dispose-plus-start on the same adapter instance; without
  this property the respawn path would rest on an accident.
- `test_tool_events_carry_the_harness_own_id_and_a_real_outcome` -- the two
  predecessor defects from plan.md 4.4c, as suite rows.
- `test_turn_end_carries_the_message_even_when_nothing_streamed` -- the
  shape AC-47 rides on; the fake has a scripted no-delta mode because the
  real provider that behaves this way is a cloud one [MEASURED, section 1].
- `test_info_is_cheap_enough_for_the_handshake` -- the D-25 clock, same
  form as the other three suites.

The fake is not a null object (adapters/AGENTS.md): scripted multi-turn
answers with tool events, the no-delta mode, and a RECORD of every
`HarnessSpec` it was started with -- that recording is what half the caller
tests in 7.4 through 7.7 assert against.

**Files.** `daemon/ports.py` (the section 5 protocol, `HarnessCaps`,
`HarnessSpec`, `HarnessEvent`, `HARNESSES`; the module docstring says FIVE
seams in this same commit, as it promises); `daemon/adapters/harness_fake.py`;
`daemon/adapters/__init__.py`; `test/conformance/test_harness_conformance.py`.
DOX: `daemon/AGENTS.md` still says four ports exist in code and flips to five
here; `daemon/adapters/AGENTS.md` already counts five and needs no change.

**Keeps passing.** The whole suite, most pointedly
`test/test_seams.py::test_the_contract_file_explains_no_engine_internals`:
`ports.py` grows text and must gain no engine deployment vocabulary. Naming
pi and claude in the `HarnessCaps` docstring passes that gate -- it is the
Kokoro-provenance precedent, a contract citing where a fact came from -- but
nothing beyond names goes in.

**Could break.** Nothing at runtime; there is no consumer. The realistic
break is import weight: `adapters/__init__.py` imports `harness_fake` at
registration, and anything heavy dragged in there slows every test and every
boot. Noticed by the suite clock and the D-25 info tests.

**Mutation (D-13).** Change the fake to emit `agent_end` after every
`turn_end` (per-turn, the predecessor's announce-five-times shape).
`test_exactly_one_terminal_event_per_prompt` must fail, alone. If it stays
green, the suite has pinned nothing about AC-48 and the step is not done.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\conformance\test_harness_conformance.py test\test_seams.py -q`
then the full `.venv\Scripts\python.exe -X utf8 -m pytest test\ -q`.

**Stop if** the six-event vocabulary cannot express something the measured
stream in section 1 actually contains -- amend section 5 first rather than
teaching the fake a shape pi does not have.

### 7.2 `harness_pi`

Two layers in one file, so the mapping is testable without a child: a pure
mapper (one of pi's JSONL lines in, zero or more `HarnessEvent`s out) and the
transport (spawn `cmd /c pi --mode rpc ...` -- the PATHEXT gotcha plan.md 4.5
already records -- LF-only framing per the docstring rule from section 1, one
reader task draining stdout eagerly, D-16). The one-terminal-event guard
lives in the ADAPTER: exactly one of `agent_end` / `error` per prompt,
including when the child dies mid-turn, with the verdict taken at the child's
close and not at the first stderr line (4.4c). `caps` for pi:
`native_resume=False, steerable=True, tool_allowlist=True, images=True`
[READ, rpc.md].

**The red test.**
`test/test_harness_pi.py::test_the_measured_stream_maps_to_the_normalized_events`
feeds the recorded lines of section 1's measured stream (string constants in
the test, captured 2026-08-17) through the mapper and asserts the normalized
sequence: text events, `turn_end`, exactly one `agent_end`. Red at import of
`daemon.adapters.harness_pi` -- absent feature; the daemon boots. The rest:

- `test_a_no_stream_provider_recovers_text_from_turn_end` (AC-47's mapping
  half) -- recorded lines shaped like the claudebridge run: `message_start`,
  `message_end`, `turn_end` carrying the message, ZERO `text_delta`
  [MEASURED, section 1].
- `test_framing_splits_on_lf_only` -- a JSON string whose value contains
  U+2028 (built in the test from the ASCII escape, never the raw
  character) arrives as ONE event, not two fragments.
- `test_an_unimplemented_dialog_is_answered_with_the_cancellation_response`
  (AC-49) -- inject an `extension_ui_request` with `method: "confirm"`
  against a scripted transport; the adapter writes
  `{"type": "extension_ui_response", "id": ..., "cancelled": true}` to the
  child's stdin. Fire-and-forget methods get no reply.
- `test_tool_events_keep_pis_toolCallId_and_isError`.
- `test_a_real_child_prompts_aborts_and_prompts_again`, marked `slow` -- a
  real child with the read-only tool set and a temp session file: prompt to
  terminal event, abort a second prompt mid-flight, third prompt answers.
  Spawn gets its own budget, generous against the measured 1.5-2.3 s startup
  [MEASURED, section 1] and separate from the per-turn watchdog (D-5). Disposes in a
  `finally`, because until 7.3 lands a red run would otherwise add to the
  orphan pile.
- The conformance row: `ADAPTERS` in
  `test/conformance/test_harness_conformance.py` gains pi, marked `slow`.
  Per adapters/AGENTS.md the adapter is not done until it passes the same
  suite as the fake (AC-7a, AC-44).

**Files.** `daemon/adapters/harness_pi.py`; `daemon/adapters/__init__.py`;
`test/test_harness_pi.py`; `test/conformance/test_harness_conformance.py`
(the row). DOX: none beyond the adapters/AGENTS.md workflow already followed.

**Keeps passing.** The fake's conformance row and the whole fast tier,
untouched.

**Could break.** Suite runtime: the pi conformance rows cost a child start
each at 1.5-2.3 s [MEASURED], plus the model's own time per prompt, which is
the larger half. Mitigate with one module-scoped child where the
suite's isolation allows it; the `slow` mark keeps it out of the fast tier
either way. Noticed by the suite clock.

**Mutation (D-13).** Hardcode `is_error=False` in the `tool_end` mapping --
the predecessor's literally-unreachable error outcome (4.4c), reintroduced.
`test_tool_events_keep_pis_toolCallId_and_isError` must fail.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_harness_pi.py test\conformance\test_harness_conformance.py -q`
-- no `-m` filter, so the live test RUNS; this step is the live proof.

**Stop if** the real child's stream shows events the recorded fixtures
cannot place (re-record from the live child rather than patching the mapper
toward a guess), or if the `cmd /c` indirection breaks stdin flushing so
commands sit in a buffer -- that is transport, and everything above it would
be built on sand.

### 7.3 The child-lifetime rule, both children at once (AC-36)

One honest correction to the sketch this section previously carried: the
endpointer's in-child watchdog does NOT transplant. It rides
`multiprocessing.parent_process().join()`, which exists only for
multiprocessing children, and pi's code is not Butler's to add a watch thread
to. The ONE mechanism that covers both children from the parent alone --
and their grandchildren: the `cmd /c` wrapper, the kokoro worker the sidecar
forks -- is a Win32 kill-on-close Job Object. The daemon creates one job,
assigns every child it spawns, and when the daemon dies, TerminateProcess
included (which is how the test harness stops every daemon it boots, and
exactly what `atexit` never survives, D-24), the kernel closes the handle
and kills the whole tree. ctypes, no new dependency; nested jobs are
supported on this OS generation. The smart-turn child keeps its existing
in-child watchdog -- it is not the failing case, and removing a working
guard buys nothing.

**The red test.**
`test/test_child_lifetime.py::test_a_hard_killed_host_leaves_no_kokoro_sidecar`
-- a scratch Python host process builds the kokoro adapter, prints the
sidecar PID, and is killed with `proc.kill()` (TerminateProcess on Windows);
the test polls `tasklist` and asserts the PID is GONE within a bounded
window. Asserted by PID against the process tree, not by the absence of an
error -- AC-36's own words. Red because the feature is absent: the sidecar
demonstrably survives today, which is the seven-orphans finding [MEASURED
2026-08-17, section 1]. The test's `finally` taskkills the leaked PID,
because a red test that leaves an orphan per run is polluting the machine it
measures. `test_a_hard_killed_host_leaves_no_pi_child` is the same shape for
the pi child; both are marked `slow` (a node boot, a pi spawn).

**Files.** `daemon/childproc.py` (new: the job, `assign(proc)`);
`daemon/adapters/tts_kokoro.py` (`_Sidecar.start` assigns; the atexit path
stays for clean exits); `daemon/adapters/harness_pi.py` (assign at spawn);
`test/test_child_lifetime.py`. DOX: the child-process bullet in
`daemon/adapters/AGENTS.md` is rewritten to name the helper and the job
mechanism, replacing the watchdog-only prose that is now half the story.

**Keeps passing.** `test/test_tts_kokoro.py`, the Tts conformance kokoro
rows, the endpointer tests, the full suite. A side effect to expect and
welcome: the conftest daemon fixtures stop leaking sidecars when they
terminate their daemons.

**Could break.** Job assignment can fail on a host where the daemon already
runs inside a kill-on-close job with conflicting limits. The helper WARNS
loudly and never refuses to boot -- the same policy as the endpointer's
`_watch_parent`, because lifetime housekeeping must not cost the microphone
(D-1). Noticed by the warning line, and by this step's tests failing on such
a host.

**Mutation (D-13).** No-op the `AssignProcessToJobObject` call in
`childproc.py`. `test_a_hard_killed_host_leaves_no_kokoro_sidecar` must fail
-- it is the named instrument because it runs on any machine with node,
where the pi twin needs pi.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_child_lifetime.py -q`
then the full suite.

**Stop if** job objects kill children spuriously here (a child dying the
moment it is assigned): AC-36 half-working is worse than absent, because it
reads as done, so the mechanism goes back to the owner rather than shipping
flaky.

This step lands as ONE commit covering both children. A commit that fixes
only Kokoro leaves AC-36 half-claimed, and half-claimed is the failure mode
this repository documents most.

**What the build actually found [MEASURED 2026-08-17].** Only ONE of the two
tests was ever red. The Kokoro sidecar and its worker survived a hard-killed
host every time, exactly as predicted; the pi child did NOT -- it exits on
stdin EOF the moment the host's pipe closes, and `cmd /c` follows it out. So
`test_a_hard_killed_host_leaves_no_pi_child` was green before the job object
existed, which makes it a regression guard rather than a red-to-green, and
the job is what covers a child that has stopped reading its stdin. Job
membership for the pi tree was therefore verified DIRECTLY with
`IsProcessInJob`: both the `cmd.exe` wrapper and the `node.exe` beneath it
report true, which is the only evidence that the mechanism -- rather than
pi's courtesy -- is what the criterion now rests on.

**What went red afterwards, and why it was not the job [MEASURED 2026-08-18].**
`test_a_hard_killed_host_leaves_no_pi_child` failed in a full run naming a
surviving `git.exe`, and passed alone. The job object was not at fault and
neither was the assignment window. Three measurements settle it: an idle pi
child spawns no `git` at all over 20 s, so the test's own child never had a
git descendant to leak; a `git fsmonitor--daemon` -- git's DETACHED,
by-design-immortal watcher -- spawned inside the job DIES when the job closes,
so it does not break away; and this machine carries 147 such daemons whose
parents are long gone, 8 of which named a LIVE process as their parent at the
moment it was measured, one a cmd.exe and one a node.exe. The instrument was
reading a recycled parent PID as a family tie (D-13). `_tree` now requires a
descendant to have STARTED AFTER the parent it claims, and treats a PID plus
its creation time as the identity. The new
`test_a_recycled_parent_pid_is_not_read_as_a_descendant` pins that on an
injected snapshot, because the race cannot be scheduled and a test that waits
for it proves nothing.

### 7.4 `brain_harness`, and the settings that spawn it

`daemon/adapters/brain_harness.py` holds `HarnessConverse` (the substantive
half) and `HarnessBrain(dialogue.Dialogue)` registered as
`BRAINS["harness"]`, so the router still runs in front and the route-to-
intent mapping is not copied (4.4). It declares `NEEDS_TARGETS = True` for
the router's resolver and a new `NEEDS_HARNESS = True`; `build_hub` injects
the built `Harness` adapter by capability, never by name (daemon/AGENTS.md),
together with `spec_for(tools)` -- `butlerd.harness_spec_resolver(settings,
session_dir)`, the `TargetResolver` idea one seam out. The adapter therefore
reads no setting and knows no path: a brain that needs configuration a
connection cannot serve gets a SECOND resolver, never the settings store,
because a store can be read for anything by whoever edits that file next.

A small host inside the adapter owns the child: it asks the resolver for the
DESIRED `HarnessSpec` per turn -- passing the tool set, which is the adapter's
own decision -- and respawns when the running child's spec differs. That one
compare-and-respawn is deliberately the mechanism 7.5 (grant, revoke) and
7.7 (cwd) reuse, with one rule enforced in the derivation itself: a WIDER
tool set can enter the desired spec only through the grant. In this step the
tool set is ALWAYS the read-only list, whatever the mode -- the mode-to-tools
table is 7.5's, and a fail-closed constant is the only honest interim
(AC-29). Selection is `BUTLER_BRAIN=harness` only; `brain_mode` does not
exist yet (see 7.5 for the reconciliation). The session file is real and
Butler-owned, under `state/harness/`, so 7.5's respawn has something to
carry across; 7.6 promotes it into the session store.

`HarnessBrain.warm` is `router.warm` plus spawn-and-ready, slotting into
`butlerd.warmup`'s existing loop unchanged (AC-46). A warm failure is loud
and never fatal, and `HarnessConverse` then refuses out loud per turn
instead of spawning inside one. Narration: one reflex `Say` when the first
`tool_start` of the turn arrives, then silence until the answer. Text deltas
are sentence-split exactly as endpoint mode's are -- `_drain` moves from
`brain_endpoint.py` into `daemon/speech.py` in this commit, because two
users is what moves a helper to the shared module. AC-47 end-to-end: the
streamed sentences, else the `turn_end` text, else a fixed line; an `error`
event is spoken as the failure; every path yields `Done` (D-1).

**Retry lands here, and it is a rule 4.4c has been carrying alone.** Butler
owns the retry policy -- that is exactly why `harness_pi` turns pi's own
auto-retry off at start, since each attempt emitted a whole `agent_end`
(AC-48) -- but 4.4c's other half, "replay a failed turn ONCE, from the
verbatim serialized input", was implemented by no step at all. Between 7.2
and this one a transient blip is therefore a SPOKEN failure with nothing
behind it, which is worse than what pi did unaided. `HarnessConverse` owns
it because it owns the turn: on a `Failed` event, prompt once more with the
input string it was handed -- verbatim, never rebuilt from a text history,
which is what silently drops images -- and speak the failure only if the
replay fails too. One replay, never a loop, and the narration says nothing
about it: a retry the owner can hear is an apology, and a voice assistant
that apologises for recovering is worse than one that just recovers. It is
safe at THIS step because the tool set is always the read-only list, so a
replayed turn cannot repeat a write; when 7.5's grant can widen it, a
failure that arrives after a write has already happened is a different
question and goes to the owner rather than into a replay.

Settings, in the same commit as their readers (the AC-7b rule): `harness`
(choices discovered from a local target listing `HARNESSES`),
`harness_model` (text, `choices_from="harness_models"`, blank meaning the
harness's own default, favourites on -- 533 registry rows [MEASURED
2026-08-17] is a list nobody reads), `harness_cwd` (text). A new `harness`
GROUP joins between brain and speech per plan.md 4.6a, and
`test/test_seams.py`'s `STAGE_ORDER` is updated in the same commit -- the
list lives in the test precisely so this reorder is a deliberate act.
`GET /api/models?target=harness_models` runs the named adapter's OWN lister
(`pi --offline --list-models`) off the loop with a cache: a `LOCAL_TARGETS`
lambda is synchronous on the loop, and a subprocess there is D-25's freeze.
The daemon never learns the lister's vocabulary (AC-41). The spec's earlier
draft of this step also listed `projects`; it lands in 7.7 instead, with the
code that reads it -- a setting without its reader is the AC-7b failure this
project has already shipped once.

**The red test.**
`test/test_brain_harness.py::test_a_no_stream_turn_is_still_spoken`
(AC-47's caller half) -- `HarnessBrain` over the fake harness in no-delta
mode, the router scripted `route=converse` on `fake_openai`; asserts one
`Say` carrying the `turn_end` text and one `Done`. Red at import of
`daemon.adapters.brain_harness` -- absent feature; the daemon boots. Then:

- `test_the_router_still_runs_in_front` -- a `route=answer` turn never
  prompts the harness (the fake records prompts; the count is zero).
- `test_narration_is_one_line_then_silence` -- a two-tool scripted turn
  yields exactly one narration `Say` before the answer.
- `test_a_multi_turn_answer_ends_the_turn_once` (AC-48's caller half) --
  two `turn_end`s, one `agent_end`: exactly one `Done`, one announcement.
- `test_an_error_event_is_spoken_and_the_turn_ends` (AC-47, AC-48) -- with
  the replay below already spent, since a failure is only spoken once it
  has failed twice.
- `test_a_failed_turn_is_replayed_once_from_the_same_input` and
  `test_a_replay_that_fails_too_is_spoken_once` (4.4c) -- the fake records
  every prompt, so both are read off that list: two identical prompt
  strings and no `Say` of the failure in the first, exactly two prompts and
  ONE spoken failure in the second. The fake needs a fail-the-FIRST-prompt
  mode to reach them; `fail_on` currently fails every matching prompt
  forever, and a fake that cannot recover cannot show a replay working
  (adapters/AGENTS.md).
- `test_abort_reaches_the_harness_child` (D-18) -- `brain.abort(turn)`
  forwards to `harness.abort()`; the hub's abort-before-cancel ordering is
  already pinned generically by `test/test_hub_bargein.py`.
- `test_a_turn_against_a_healthy_child_starts_nothing` (AC-46's caller
  half) -- after `warm`, a turn records no `start()`. Scoped to a healthy
  child, because the criterion permits a recovery or reconciliation spawn
  and a test forbidding what another test requires is a suite that
  contradicts itself. That is the assertion with teeth,
  because it fails however fast a spawn is. Plus, marked `slow`:
  `test_the_first_turn_costs_no_more_than_a_later_one` -- a warmed real
  child, the same fixed short question twice, the first turn within 1.5 s of
  the second (the measured spawn cost, section 1). Time-to-ANSWER is the
  model's and is deliberately not asserted on its own (section 8); the
  COMPARISON is what removes the model from the measurement.
- `test_a_failed_warm_refuses_out_loud` (AC-46's failure clause).
- `test/conformance/test_brain_conformance.py` `ADAPTERS` gains
  `"harness"` (fake harness plus `StubResolver`) -- AC-27's
  both-modes-one-suite claim starts being true.
- `test/test_conversation.py` gains a four-turn harness case mirroring the
  endpoint one: per-turn state now includes a child and a reader task, and
  that leak class is invisible one turn deep (test/AGENTS.md).

**Files.** `daemon/adapters/brain_harness.py`; `daemon/speech.py` and
`daemon/adapters/brain_endpoint.py` (the `_drain` move);
`daemon/adapters/harness_fake.py` (the fail-once mode the replay tests need);
`daemon/settings.py`; `daemon/butlerd.py` (`NEEDS_HARNESS` injection, the
`harness_models` target, the `harnesses` local target);
`test/test_brain_harness.py`; `test/test_seams.py`;
`test/conformance/test_brain_conformance.py`; `test/test_conversation.py`.
No web change: the panel renders from the schema, so the new group appears
by itself -- which is the point of AC-45's machinery. DOX: `daemon/AGENTS.md`
(the harness-has-no-code contract text is now stale and goes),
`daemon/adapters/AGENTS.md` if the brain-adapter bullets change.

**Keeps passing.** The whole fast tier, most pointedly
`test/test_brain_endpoint.py` (the `_drain` move),
`test/test_seams.py::test_settings_groups_follow_the_pipeline`,
`test/test_settings_api.py`, `test/test_conversation.py`.

**Could break.** The group reorder (renders as a misordered page; caught by
the STAGE_ORDER test); the `_drain` move (caught by
`test_a_reply_arrives_as_one_intent_per_sentence`); warmup spawning a child
on daemons that did not ask for one (only `BUTLER_BRAIN=harness` boots do,
and the conftest daemons do not set it).

**Mutation (D-13).** Delete the `turn_end`-text fallback in
`HarnessConverse` so only deltas are spoken.
`test_a_no_stream_turn_is_still_spoken` must fail -- and only it. The
streamed-path tests staying green is itself evidence: the fallback is a
separate path, not an accident of the happy one.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_brain_harness.py test\conformance\ test\test_seams.py test\test_settings_api.py -q`
then the full suite.

**Stop if** holding one child open across turns fights the
one-prompt-per-turn model -- pi queue semantics leaking between Butler turns
in ways the fake cannot script. That goes back to 4.4c rather than teaching
the brain a queue.

### 7.5 The grant (AC-29)

The boundary lives in the DAEMON, not the brain: the Brain proposes
(`AskConfirm`), butlerd disposes (plan.md 4.10). In harness mode under
`secretary`, a verdict whose action carries `writes` becomes `AskConfirm` as
it already does; the hub speaks the repeat-back and parks the session in a
pending-confirmation state instead of Phase 1's "I cannot do that yet" line.
The NEXT final is parsed as affirmative, refusal, or neither. Affirmative:
the host's grant flag is set for the session, the desired spec widens,
dispose-plus-start carries the SAME session file, and the confirmed
utterance proceeds. Refusal, an unparseable answer, or the fail-closed
timeout: nothing changes, and the refusal is spoken. `paranoid` never offers
the repeat-back at all; `trusted` spawns with the full set and no ceremony.
Raising the mode never widens the running child -- the widening rule 7.4 put
in the derivation. Lowering it revokes NOW, not at the next turn: the two
mode-change call sites (`hub._mode_request`, voice-down, and
`butlerd.post_mode`, UI) both notify the host, which disposes, respawns
read-only mid-conversation, and says so once. This is AC-9's machinery
arriving early; Phase 2's dispatch flow reuses it rather than growing a
second.

`brain_mode` lands HERE, with both options, in the same commit as the
selection code that reads it. That is the reconciliation of this spec's two
sentences (7.4's earlier draft said the switch lands there; the closing rule
says harness mode is not selectable until 7.5): a switch whose unsafe option
exists before its gate is exactly the wake-word-to-tools exposure the
closing rule forbids, and a one-option switch is an AC-7b control. It is
restart-flagged -- the brain is constructed at boot, and a restart flag the
UI shows honestly beats a hot-swap nobody asked for. The confirmation
timeout is a fail-closed constant; nothing in the criteria needs it to be a
setting.

**The red test.**
`test/test_harness_grant.py::test_one_yes_widens_the_session_and_is_not_asked_again`
-- hub-level, fake harness, scripted verdicts. A writes-turn produces the
spoken repeat-back and NO respawn; "yes" produces dispose-plus-start with
the write set and the same session file; a second writes-turn in the same
session is not asked again. Red on the first assertion because the hub
still answers with Phase 1's defined "I cannot do that yet" behaviour --
absent feature; the daemon boots. Then:

- `test_a_refusal_changes_nothing`,
  `test_silence_past_the_timeout_fails_closed`,
  `test_an_unparseable_answer_is_a_refusal` -- the three fail-closed exits.
- `test_paranoid_never_offers_a_grant`.
- `test_raising_the_mode_does_not_widen_the_running_child` -- the freeze,
  asserted on the caller (D-18).
- `test_lowering_the_mode_revokes_immediately_and_says_so_once` -- driven
  through BOTH call sites, voice-down over the socket and the HTTP route,
  because a revoke wired to one of two callers is D-18's exact shape.
- `test_a_parked_confirmation_rearms_on_barge_in_and_disconnect` -- the
  pending state is new per-session state held ACROSS turns, precisely the
  class test/AGENTS.md says one-turn tests cannot see; parked-and-latched
  is D-1 with a new name.
- `test_a_real_grant_carries_the_conversation_across_the_respawn`, marked
  `slow` -- section 8's named risk made falsifiable: three real turns, the
  grant, then a question that depends on the pre-grant turns, answered by
  the NEW child.

**Files.** `daemon/hub.py` (the pending-confirmation branch in `_on_final`,
re-arming on every exit); `daemon/adapters/brain_harness.py` (grant state,
the mode-to-tools table, the revoke entry point); `daemon/settings.py`
(`brain_mode`); `daemon/butlerd.py` (selection reads `brain_mode`;
`BUTLER_BRAIN` stays as the test override, as it is for the other ports);
`test/test_harness_grant.py`. DOX: `daemon/AGENTS.md` gains the grant
boundary as a Local Contract -- it is a permission rule, and those live in
the daemon's own doc.

**Keeps passing.** `test/test_protocol_v1.py`, `test/test_turn_modes.py`,
`test/test_hub_bargein.py`, `test/test_conversation.py` -- `_on_final` is
the most-trodden path in the daemon and those four files are the regression
net around it.

**Could break.** The parked state: held across turns, it can latch the
session if any exit forgets it (D-1, D-17's flag-across-await shape). The
re-arm test above and the conversation suite are the watch on it. Also the
spoken-mode-change path in `_on_final`, which the new branch must not
shadow -- `test_turn_modes.py` notices.

**Mutation (D-13).** Delete the revoke-on-lower call at the hub's
mode-change site.
`test_lowering_the_mode_revokes_immediately_and_says_so_once` must fail.
Chosen over mutating the grant itself because revoke is the half nobody
exercises by hand -- this section has always called it the easy-to-forget
half, so it gets the instrument.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_harness_grant.py test\test_protocol_v1.py test\test_hub_bargein.py test\test_conversation.py -q`
then the full suite.

**Stop if** yes/no parsing of real dictation proves too brittle and the fix
on the table becomes "let a model judge the affirmative" -- a model deciding
a permission grant moves the security boundary into a model, and that is the
owner's call, not a plan detail.

**What the build actually decided [2026-08-18].** Three questions this
section left open, answered in the code and recorded here rather than in a
commit message nobody re-reads.

1. **Only `secretary` parks.** `paranoid` and `trusted` both PROCEED with the
   utterance instead of refusing it: in paranoid the read-only child attempts
   the write and fails at the tool layer, which is what AC-29 says the owner
   hears, and offering a repeat-back that could never be honoured would be
   worse. That also settles the awkward state a mode RAISE leaves behind -- a
   trusted mode with a read-only child running -- without inventing a second
   way to widen one.
2. **The granted set is not the full set.** `secretary` plus a yes gets
   `read, grep, find, ls, edit, write`; `bash` belongs to `trusted` alone,
   because "yes, rename that module" is not consent to run arbitrary
   commands, and the owner who wants that says so in the web UI.
3. **A barge-in mid-repeat-back parks NOTHING, and it took two fixes.**
   `abandon()` clears the pending first and nulls the turn last, and between
   them it awaits the engine aborts -- 1.51 s against a real pi child
   (section 1). The repeat-back finished inside that window, still owned its
   turn, and parked BEHIND the barge-in meant to cancel it, so the
   interrupting utterance was ruled on as consent. The park is now guarded on
   still owning the turn AND `abandon()` re-clears after the turn is gone,
   which is D-18 in its usual shape: the callee's own test was green.
4. **The revoke notice and the expiry notice are SHOWN, not spoken.** Every
   audible path in the daemon is turn-scoped, and both of these happen
   between turns: the page clears its turn when one ends and discards frames
   for a turn it does not own (D-6), so audio sent to an idle satellite is
   dropped at the client. They go out as `spoken=False` text, which is the
   frame a reply that is shown rather than read out already uses (AC-40).
   Making them audible needs Phase 2's announcer (AC-14) and a protocol frame
   that is not turn-scoped; that is a build, not a workaround, and it is not
   this step's. The refusal and the unparseable answer ARE spoken, because
   those happen inside the turn that answered.

### 7.6 Sessions (AC-35)

`daemon/sessions.py`: `state/sessions/<id>/{meta.json, events.jsonl}`,
mirroring 4.5's `workers/<id>/` on purpose -- same names, same append-only
rule. The host writes the record as events arrive: `user_message`,
`assistant_message`, and tool start/end WITH the tool name and outcome,
because a role-and-content-only transcript is the 4.4c replay defect.
Rotation on a spoken "new conversation" (a whole-utterance match beside
`spoken_mode_request`, daemon-level). Re-attach on boot by explicit
last-active timestamp, never the head of a sorted list (4.4c). The wire says
which of THREE things happened to the context -- `new`, `resumed`, or `lost`
-- beside a separate `resume` field derived from `caps.native_resume`; for pi
that is re-attach by session file [MEASURED mechanism, section 1]. The third
word is `lost` and not "replayed": Butler replays nothing into a harness (see
what the build decided, below). The page renders the line, filling its existing
history from a new `GET /api/session/current` on connect. A real transcript
view remains 4.4's separate debt; this step ships the record and the honest
label, not a new UI.

**The red test.**
`test/test_harness_sessions.py::test_a_restarted_host_reattaches_the_same_session_file`
-- host plus store in a tmp state dir, scripted turns, tear down, build
fresh (the restart), assert the new child's recorded spec names the SAME
session file and the store's events survived. Red at import of
`daemon.sessions` -- absent feature; the daemon boots. Then:

- `test_events_record_tools_with_name_and_outcome`.
- `test_new_conversation_rotates_to_a_fresh_session`.
- `test_reattach_picks_the_last_active_not_the_first_sorted` -- two
  sessions where the older one sorts first, so the 4.4c rule can actually
  fail.
- `test_the_wire_says_whether_the_agent_still_remembers` -- protocol-level,
  and it must say WHICH of the three (AC-35's clause, its honest wording).
- `test_resume_is_chosen_by_caps_not_by_name` -- the fake with
  `native_resume` flipped both ways; this is AC-44's resume clause proven
  on the CALLER, which conformance cannot do.
- `test_a_corrupt_session_record_is_loud_and_not_fatal` -- boot now reads
  state, and a store that refuses boot is D-1.
- `test_a_real_session_survives_a_real_restart`, marked `slow` -- real pi:
  a turn, host rebuilt on the same store, a follow-up that depends on turn
  one.

**Files.** `daemon/sessions.py`; `daemon/adapters/brain_harness.py` (the
host writes through the store; the 7.4 session path moves in);
`daemon/hub.py` or `daemon/settings.py` (the rotation phrase);
`daemon/butlerd.py` (`/api/session/current`); `web/app.js` (render the
new/resumed/lost line and the restored history);
`test/test_harness_sessions.py`. DOX: `daemon/AGENTS.md` (the store and its
append-only contract), `web/AGENTS.md` (the new frame the page consumes).

**Keeps passing.** 7.5's grant suite (the respawn now rides the store's
path), the protocol suite, and `test/test_connections.py::
test_nothing_under_state_holds_a_key_value` -- `state/` grows a sibling and
the grep over all of it is the proof session records never hold a key.

**Could break.** Boot: re-attach is new boot-time state reading, and a
corrupt file must be loud and non-fatal (the `settings._load` posture).
Noticed by the corrupt-record test and by the daemon fixtures failing to
boot if it is wrong.

**Mutation (D-13).** Stop appending to `events.jsonl` in the host's event
pump. `test_events_record_tools_with_name_and_outcome` must fail -- the
re-attach test would stay green, because the FILE still re-attaches, which
is exactly why the record needs its own instrument.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_harness_sessions.py test\test_harness_grant.py test\test_connections.py -q`
then the full suite.

**What the build actually decided [2026-08-18].** Three things this section
assumed turned out to need an answer rather than an implementation.

1. **"Resumed or replayed" is not the choice, and pi is neither.** This
   section and AC-35 both ASSUMED `native_resume=False` means Butler replays
   a transcript, and both are corrected above and in plan.md section 2 --
   prose that outlives the behaviour it describes is the recurring hazard
   here (D-12). Butler replays nothing into pi and does not need to: pi is
   pointed at its own session FILE, which is Butler's to name, and it reads
   its conversation back out of it -- proved live, and proved by mutation
   (point the second child at a different file and the same test fails with
   "I don't have any number to recall from this chat"). So the wire carries
   THREE values: `new`, `resumed` (the agent's own context came back), and
   `lost` (Butler's record survived and the agent's did not). Calling the
   third "replayed" would claim Butler had fed the conversation back, which
   is the one thing the label exists to be honest about. `resume` is a
   separate field -- `native` or `session_file` -- and IT is what comes off
   `caps.native_resume` (AC-44). plan.md 4.4c said pi replays a transcript;
   that sentence was measured wrong and was corrected in the same commit,
   and AC-35's own wording followed it on 2026-08-18 (D-12).
2. **The grant is scoped by ONE predicate, not by three clearings.**
   `_grant_holds` already compared the working directory (7.5); it now
   compares the session file too, and `_forget_a_stale_grant` is the only
   thing that acts on it, called from the reconcile, the rotation and a
   discarded reference. The first draft cleared the flag inline in each
   place, and the mutation harness caught that immediately: with three
   redundant clearings, removing any one of them changed nothing observable,
   so none of them was proven. A rule nothing can reach is a rule nothing
   proves (D-13).
3. **A discarded reference takes the grant with it.** The owner said yes
   about a conversation; once the harness says its own context for that
   conversation is gone, the agent on the other end of the next prompt has no
   part in it. Fail closed, on the same predicate as a rotation.

**Stop if** the live test shows pi's auto-compaction rewriting the session
file so re-attach loses more than compaction admits. Then AC-35's "continues
the same conversation" needs a measurement, possibly a compaction field on
`caps`, and the owner -- not a quiet weakening of the test.

### 7.7 The spoken project switch (AC-31)

`projects` is a list of absolute directory paths (`lowercase=False`,
`allow_empty=True` -- empty is the shipped state). The spoken name of a
project is its final path component, normalized: lowercased, `-` and `_`
read as spaces. Matching is EXACT on the normalized name -- no fuzzy, no
prefix -- because the cost of a guess is a tool-enabled agent in the wrong
repository (AC-31). The phrase is handled in the daemon beside
`spoken_mode_request`, a deterministic instruction and not something to
route through a model, and only in harness mode. A match confirms aloud and
changes the desired spec's cwd; 7.4's compare-and-respawn does the rest, on
the same session. No match is refused ALOUD, naming what was heard, and
changes nothing -- a directory not on the list is unreachable by voice even
if it exists on disk.

**The red test.**
`test/test_project_switch.py::test_a_near_miss_is_refused_aloud_and_nothing_changes`
-- projects hold the butler and smart-crawler paths; "work in smartcrawlr"
must produce a spoken refusal, no respawn (the fake's spec record is
unchanged), and an untouched `harness_cwd`. Red because today the phrase
falls through to the brain and is answered as conversation -- no refusal is
ever spoken, so the assertion fails: absent feature, the daemon boots. Then:

- `test_a_known_project_respawns_the_child_there` -- the Gherkin's happy
  half, spec recorded with the matched cwd.
- `test_the_switch_is_inert_outside_harness_mode`.
- `test_an_empty_projects_list_refuses_every_switch`.
- `test_a_mode_phrase_still_reaches_the_mode_store` -- the two
  whole-utterance matchers are disjoint literal sets and must stay that
  way.

**Files.** `daemon/settings.py` (`projects`, same commit as its reader --
the rule); the matcher beside `spoken_mode_request`; `daemon/hub.py` (the
harness-mode branch in `_on_final`); `test/test_project_switch.py`. DOX:
none beyond the settings row unless the hub contract text changes.

**Keeps passing.** `test/test_turn_modes.py` and the spoken-mode tests, the
grant and session suites, the full tier.

**Could break.** Phrase shadowing between the two matchers -- covered by the
disjointness test above -- and the refusal path forgetting to re-arm, which
the conversation suite's shape catches.

**Mutation (D-13).** Make the matcher fall back to the default
`harness_cwd` on a failed match -- the guessing fail-open AC-31 forbids.
`test_a_near_miss_is_refused_aloud_and_nothing_changes` must fail.

**Verify.**
`.venv\Scripts\python.exe -X utf8 -m pytest test\test_project_switch.py test\test_turn_modes.py -q`
then the full suite:
`.venv\Scripts\python.exe -X utf8 -m pytest test\ -q`.

**What the build actually decided [2026-08-18].** Two questions this section
left open.

1. **The switch is scoped to the SESSION and never rewrites `harness_cwd`.**
   This section says "changes the desired spec's cwd", which reads either
   way. The host holds the project in memory and the resolver takes it as an
   argument; the setting is the DEFAULT the daemon comes back to. Writing it
   back would mean a spoken command silently editing the owner's default, so
   the next restart would come up somewhere they never chose -- and the
   spoken path has no repeat-back to catch that. The cost is real and named:
   a daemon restarted mid-project re-attaches to the same conversation in the
   DEFAULT directory, and the owner has to say it again.
2. **There are TWO gates, not one.** `hub._work_in_project` matches the
   spoken name, and `harness_spec_resolver` checks the answer again before
   building a spec. The second is what a future caller also passes through --
   the Worker Manager is the next one -- and it is the only thing between an
   adapter holding a directory string and a child running in it. It has its
   own test for the reason 7.6 learned the hard way: a redundant guard with
   no witness is a guard nothing proves.

**Stop if** real dictation of project names misses so often that exact
matching is unusable. The options then -- spelled-out names, a numbered
list -- go to the owner; fuzziness is not quietly added to a security
boundary.

### Commits, and what may not be split

One commit per step, each green, in order. Within that, the pairings that
MUST NOT be split, because each half without the other is a recorded failure
mode here:

- A setting and its reader: `harness`, `harness_model`, `harness_cwd` land
  inside 7.4; `brain_mode` inside 7.5; `projects` inside 7.7. A control that
  saves and changes nothing is the AC-7b failure this project has already
  shipped once -- it is why `projects` moved out of 7.4's earlier draft.
- The `ports.py` five-seam docstring, the `daemon/AGENTS.md` count, and the
  port code: one commit, 7.1 (D-12; both files say so themselves).
- A real adapter and its conformance row: 7.2 (adapters/AGENTS.md).
- The settings GROUPS change and the `STAGE_ORDER` test edit: 7.4.
- Both children under the job object: 7.3 is one commit or AC-36 is
  half-claimed.

### Order, dependencies, and the gate

7.1 -> 7.2 -> 7.3 -> 7.4 -> 7.5 -> 7.6 -> 7.7, strictly. 7.2 needs the
port; 7.3 needs 7.2's spawn path (its Kokoro half is order-free but lands
with the pi half regardless); 7.4 needs the fake for its tests and 7.2 plus
7.3 before its live warm spawns children worth trusting; 7.5 needs 7.4's
host and derivation; 7.6 needs 7.5's respawn to have something to carry;
7.7 needs the compare-and-respawn from 7.4 and reads a setting of its own.

**7.5 is the gate.** `brain_mode` with the `harness` option exists only from
7.5, so harness mode first becomes selectable there -- with the grant, the
freeze, and the revoke landing in the same commit. Until then the mode is
reachable only through `BUTLER_BRAIN=harness` in a test environment, and
`brain_mode` stays `endpoint` by default throughout and after: harness mode
is a deliberate choice, not a default, because a tool-enabled brain with no
grant model is one wake-word false accept away from acting.

### Where each criterion is actually proven

Conformance proves an adapter HOLDS the contract; only a caller-side or
system-level test proves the daemon USES it (D-18 -- three green-but-broken
contracts in this repository earned that sentence). Every criterion below
names both, or says why one level cannot exist.

| Criterion | Callee (conformance) | Caller / system | Step |
|---|---|---|---|
| AC-44 | fake and pi pass one suite | brain conformance `harness` row; resume chosen by caps, name never tested -- but the caps value only picks a LABEL until a second real harness exists | 7.1, 7.2 / 7.4, 7.6 |
| AC-29 | none possible -- it is daemon policy, not adapter behaviour | the grant suite: grant, refusal, timeout, freeze-on-raise, revoke-on-lower, paranoid | 7.5 |
| AC-31 | none possible | near-miss refusal, known-project respawn | 7.7 |
| AC-35 | dispose-then-start contract | re-attach, rotation, last-active, new-vs-resumed-vs-lost on the wire, live pi restart | 7.1 / 7.6 |
| AC-36 | none possible -- process-tree property | PID-level hard-kill tests against a Toolhelp process snapshot, both children | 7.3 |
| AC-46 | none possible -- boot policy | no `start()` inside a turn; live prompt-accepted timing against the measured startup | 7.4 |
| AC-47 | `turn_end` carries the message | pi mapping recovers no-stream text; brain speaks it, or the fixed line, or the failure | 7.1, 7.2 / 7.4 |
| AC-48 | exactly one terminal event, both adapters | exactly one `Done`, one announcement, error path included | 7.1, 7.2 / 7.4 |
| AC-49 | none -- the fake never asks | cancellation response asserted on the pi TRANSPORT, mid-prompt included; every live test rides past the three unprompted startup requests [MEASURED]. NO caller-level test: nothing asserts a turn still finishes when a dialog arrives mid-answer, because the fake cannot raise one through the port | 7.2 |

DONE 2026-08-18: these criteria are now in `plan.md` section 2, and this
document is what `docs/architecture-seams.md` is for its finished phases --
the argument, kept, while the plan carries the contract. Section 9 records
what the plan got wrong.

---

## 7. Deliberately NOT in this phase

- **The Worker Manager.** The `Harness` port's second consumer is Phase 2
  (plan.md 4.5). Building it here would design the dispatch half against a
  talk-mode adapter.
- **claude as a second harness.** It is the obvious next adapter and it is
  what turns `native_resume` from a field into a proven capability, but AC-44
  asks for the fake plus ONE real adapter, and pi is the one measured working
  here.
- **A generic CLI base class.** Every candidate harness is a child process, so
  the temptation is real. The dialects are not compatible -- pi speaks JSONL
  rpc with `steer` and `tool_execution_*`; claude speaks stream-json and
  resumes by id -- and a shared base would encode pi's protocol as the
  universal one, which is how the third harness ends up rewriting it. What IS
  shared is three helpers: spawn-plus-watchdog, the LF-only JSONL reader, and
  the one-terminal-event guard.
- **Per-write permission prompts.** pi has none by design, and building
  Butler's own through a pi extension would put Butler's security boundary
  inside the process it is trying to bound.
- **Speaking tool progress.** One line when the first tool fires, then
  silence, by decision. Narrating every tool is Phase 2's worker behaviour,
  where the owner is not waiting for the answer.

---

## 8. The risks, named

[One risk here is now REALISED rather than accepted: 10.5 records that a
wake-word false accept reaches a full-capability agent, because ruling 1
deleted the ceremony that stood in the way. The model-choice risk below is
unchanged and is sharper, since the spawn is now paid per question too.]

**The model choice decides whether this is usable, and Butler does not warn.**
1.55 s on a bridged cloud model against about 27 s on a local 4B, measured. A
voice assistant at 27 s per turn is not one. The mitigation is the measurement
being in this document and in the settings help text, not a warning dialog --
the owner chooses (4.4b), and this is the sharpest example of that decision's
cost so far.

**A respawn loses in-flight context that the session file does not carry.**
FALSIFIED, in the good direction [MEASURED 2026-08-18, real `pi` child,
`codexbridge-local/gpt-5.4-mini`]. Three turns, a real grant, then a question
whose answer exists only in the turns before it: the child spawned by the
grant answered it. The instrument was validated by mutation the same way --
point the respawned child at a different session file and the same test fails
with "I don't have a remembered number from this chat", so the assertion is
about the session file and not about the model being agreeable.
`test_a_real_grant_carries_the_conversation_across_the_respawn` is the guard.

**The conformance suite is fake-proven until 7.2.** The same condition D-18
and D-13 describe, and the same mitigation: the caller-side assertions and the
mutation checks are written with the fake, and the real adapter lands in the
next step rather than eventually.

---

## 9. What this plan got wrong

[NOT overtaken and deliberately not rewritten: this is the record. Read it
with 10.1's correction, which found one more thing this section missed --
`proceed()` was described as existing only so a confirmed utterance could
skip the router, and it is in fact the ONLY path from a router `dispatch`
verdict to the coding agent, so it survived the grant's deletion.]

Eight things, in the order they hurt. Each is something the plan could not
have known and a later reader would otherwise have to rediscover; none of
them is a restatement of what the plan already said.

**The orphan mechanism did not transplant.** Section 7.3's first sketch said
the endpointer's in-child watchdog would cover the harness. It cannot: that
watchdog rides `multiprocessing.parent_process().join()`, and neither node
nor pi is a multiprocessing child. The mechanism that actually works is a
Win32 kill-on-close Job Object created through ctypes, assigned in the same
function that spawns, which descendants join at creation. And only ONE of
the two tests was ever red -- the pi child exits on stdin EOF the moment the
host's pipe closes, so its test is a regression guard, and job membership had
to be verified with `IsProcessInJob` to show the criterion rests on the job
rather than on pi's courtesy.

**A measured number was wrong, and a criterion had been written against it.**
Section 1 claimed 9-10 s of startup [MEASURED]. Twenty-one runs put
spawn-to-prompt-accepted at 1.51-2.28 s and could not reproduce the old
figure on any axis tested -- working directory, provider, model, or cold
first spawn. What produced it is still unknown, and the number was replaced
rather than explained. The decision it justified survived; AC-46 did not,
because "the first turn must not carry 9-10 s" cannot fail against a 1.5 s
spawn. An unreproducible measurement is not evidence, and a criterion phrased
against one inherits its unfalsifiability.

**A capability with no method behind it is a claim, not a contract.**
`HarnessCaps.steerable` is True for pi and there is no `steer` on the port.
The field keeps its place because the Worker Manager (4.5) will read it, but
the disclosure moved out of a comment in the fake and into the port's own
docstring, where an adapter author will actually see it. AC-44 now carries
the same admission one level up about `native_resume`, which selects a
display string and nothing else until a second real harness lands.

**The conformance suite HUNG instead of failing.** Every read of an adapter's
stream was a bare `anext`, so a harness that stops producing without a
terminal event -- the exact predecessor defect the file exists to catch --
made pytest print nothing, return no exit code and get killed from outside.
Proved by deleting the fake's end-of-stream: exit 124 at 90 s. Every read now
goes through one helper with a deadline whose message names what did not
arrive, and the same mutation fails in 5.49 s. A suite that cannot REPORT the
failure it was written for is worse than no suite, because it reads as slow
rather than as broken (test/AGENTS.md carries the rule now).

**Counting terminal events needs the tail, drained BEFORE dispose.** Reading
stops at the first terminal event, so a fake yielding `Failed` and then
`AgentEnd` passed the count unchanged. Draining after dispose put the check
straight back to sleep for pi, whose `_drain` returns the moment the handle
is nulled. Both versions were green and neither proved anything.

**pi's auto-retry emits a whole terminal event per attempt** [MEASURED
2026-08-17] -- up to three `agent_end`s for one prompt, which is AC-48's
announce-five-times failure arriving from the engine instead of from Butler.
pi also has no error EVENT at all: a broken provider is a turn whose message
carries `stopReason: "error"` followed by an ordinary `agent_end`. Auto-retry
is turned off at start because Butler owns retry policy, and EVERY event is
dropped once a prompt is over -- not only the terminal ones, because 7.4
sentence-splits `text_delta` and SPEAKS it, so a retry's text would be a
second answer to a turn already announced.

**Some providers stream nothing**, and the plan only guessed they might.
`claudebridge-local` produced zero `text_delta` events and put the whole
answer in `turn_end`, so an adapter mapping only deltas speaks nothing at
all. The recovery has to be guarded on nothing having been said, because
`turn_end` carries the message on streaming providers too and an unguarded
fallback reads the answer out twice. pi still has NO no-delta conformance
row: the one provider measured behaving this way was not answering when the
row was needed, and the test file says so plainly rather than pretending.

**The settings seam had to be narrowed back to a callable.** 7.4 shipped the
adapter a settings store and a state directory. Review reversed it: butlerd
builds `harness_spec_resolver(settings, sessions)` and injects
`spec_for(tools)`, mirroring `TargetResolver.resolve(role)`, so the adapter
reads no store and knows no path while per-turn reads survive and the grant
still owns the tool set by passing it in. A store handed to an adapter can be
read for anything, per turn, by whoever edits that file next -- and the
AGENTS.md sentence permitting it was deleted rather than qualified.

**And two security holes were found by REVIEW, in steps that had already been
called done.** A barge-in during the repeat-back parked the confirmation
BEHIND the interruption, so the interrupting utterance was ruled on as
consent; and the working directory was resolved at yes-time rather than
question-time, so moving `harness_cwd` between the two honoured consent for
one repository in another. Both are described in full under AC-29. What
belongs here is the pattern: each was a boundary proved on one side of a call
(D-18), each had a green callee test, and each was fail-OPEN in the single
direction the whole ceremony exists to prevent. The grant is the one place in
this phase where a defect is not a bad answer but an unasked-for action, so
"the step is green" is not the same sentence as "the step is done".

---

## 10. The simplification -- four rulings, and what they cost (SHIPPED 2026-08-18)

**SHIPPED 2026-08-18.** This section was written as a proposal and is now
what the code does. All four rulings landed the same day, in five commits
(`653a44d`, `7e25f16`, `ecc2b86`, `5e8916a`, `fc082df`) plus a review fix
(`cc483d1`) and this documentation sweep. The suite went from 504 collected
to 465, and stands at 465 passed in 301 s [MEASURED 2026-08-18].
Where the implementation DIVERGED from what this section proposed, 10.8
records it rather than quietly editing the proposal to match.

Phase 7 works: 504 tests pass [MEASURED 2026-08-18]. The owner has read it
and ruled it overly complex. Four decisions were taken on 2026-08-18 and are
folded in here rather than re-opened; this section works out what each one
deletes, what replaces it, and what it costs.

1. **The spoken write grant is DELETED ENTIRELY.** The harness always runs at
   full capacity, `bash` included. No repeat-back, no yes/no parsing, no
   parked confirmation, no respawn-on-grant, no revoke-on-mode-change, no
   scoping to a conversation or a repository. The owner's words: "grant tools
   at all should cover bash as well. keep it simple please" and "i just want
   full agent capability".
2. **A harness is spawned FRESH FOR EACH MESSAGE**, never held across turns,
   resuming by the harness's own session reference. This matches the
   reference implementation's CLI-based backend
   (`E:\PROJECTS\prototype\scripts\dashboard\server\lib\backends\claude-backend.js`),
   which builds its argv per prompt, passes `--resume <id>` captured from the
   previous child's `init` line, and kills the child when the turn ends. The
   cost is stated in 10.6: pi takes 1.5-2.3 s to start [MEASURED 2026-08-17,
   21 runs], and that lands as dead air after the owner stops speaking, on
   EVERY question.
3. **Sessions use exactly the reference's model**: one flat JSON file per
   chat, rewritten whole. `daemon/sessions.py` -- `meta.json` plus
   append-only `events.jsonl`, rotation, stale-reference clearing,
   replay-once wiring, and the `new`/`resumed`/`lost` wire vocabulary -- is
   replaced by it.
4. **The acceptance criteria this contradicts are AMENDED, with the losing
   argument recorded** (10.5). This repository keeps rejected arguments
   rather than deleting them, exactly as plan.md 4.10 keeps the struck
   sentence about `Harness` not being a port.

One door is shut before anyone proposes it: for pi the reference SPAWNS
NOTHING -- its `pi-backend.js` wraps an in-process `ChatSessionManager` from
pi's Node SDK [READ 2026-08-18]. Butler is Python and must run pi as a
program, so Butler's pi path is the shape of the reference's CLAUDE backend
(fresh process per message, resume by reference), not its pi one. Do not
re-propose the SDK; it is not reachable from here.

### 10.1 What is deleted

All counts below were read from the repository at commit `efcadb6`
[READ 2026-08-18].

**Whole files -- 2,230 lines, 41 tests:**

| file | lines | tests |
|---|---|---|
| `daemon/sessions.py` | 345 | -- |
| `test/test_harness_grant.py` | 961 | 22 |
| `test/test_harness_sessions.py` | 652 | 19 |
| `test/manual/mutate_grant_review.py` | 120 | (D-13 instrument) |
| `test/manual/mutate_sessions.py` | 152 | (D-13 instrument) |

**The grant, by name.** In `daemon/adapters/brain_harness.py`: the
mode-to-tools table (`READ_ONLY_TOOLS`, `WRITE_TOOLS`, `FULL_TOOLS`,
`MODE_TOOLS`, lines 73-107) and the whole grant block of `_Host` -- `tools`,
`grant_scope`, `_grant_holds`, `_forget_a_stale_grant`, `writes_now`,
`grant`, `permission_mode` (lines 182-364, 183 lines) -- plus
`effective_mode`, the `CAN_GRANT` flag, and `proceed()`, which existed only
so a confirmed utterance could skip the router. In `daemon/hub.py`:
`PendingConfirm` and the pending/`clear_pending` state (including the two
deliberate clearings in `abandon()`), `apply_permission_mode` (lines
570-608), `_resolve_confirmation` (806-869), the grant half of
`_on_ask_confirm` (962-1025), `_expire_confirmation` (1027-1043),
`_granting_brain`, and the `CONFIRM_*` strings -- 184 lines across the four
method spans alone. In `daemon/modes.py`: `spoken_confirmation` with its
`AFFIRMATIVE`/`REFUSAL` grammar, which has no caller once the grant goes.
In `daemon/butlerd.py`: the `permission_mode=store.mode` injection under
`NEEDS_HARNESS`, and the `apply_permission_mode` call in `post_mode`.

**Full capacity means NO tool restriction at all.** `HarnessSpec.tools` is
deleted from `daemon/ports.py`, the `--tools`/`--no-tools` branch is deleted
from `harness_pi._argv`, and `HarnessCaps.tool_allowlist` goes with them --
a capability nothing reads is a claim, not a contract (section 9). pi spawns
with its own default tool set, which is everything.

**The held-open child, by name.** In `brain_harness.py`: `_settle` and
`SETTLE_TIMEOUT_S` (a fresh child per message has no interrupted prompt to
read past -- the whole abandoned-terminal-event problem, `_outstanding`
included, exists only because a stream outlived a turn), the
compare-and-respawn (`refresh`, `desired`, the spec comparison), the
dead-child recovery branches (`restart` on a wedged or dead child; a child
now dies at the end of every turn on purpose), and boot-time
`warm`/`start()` for the harness. In `butlerd.py`: the harness half of
warmup. In `daemon/settings.py`: the `restart` flag on the `harness`
setting (line 206) -- the adapter is built per message from the current
setting, so the flag would describe a restart nobody needs; `brain_mode`
keeps its flag because the Brain composite is still built at boot.

**The session scheme, by name.** `daemon/sessions.py` whole: `SessionStore`,
rotation-as-a-fresh-directory, `clear_native` and the numbered
`harness-N.json` scheme, `names_a_missing_session` and
`MISSING_SESSION_PHRASES`, last-active re-attach, the `new`/`resumed`/`lost`
vocabulary and the `resume: native|session_file` field. In `butlerd.py`:
`SESSION_EVENTS_SERVED` and the events half of `GET /api/session/current`.
In `harness_fake.py`: `reject_session` and the leave-a-context-file-behind
behaviour, which exist only so AC-35's clear-and-replay clause had something
to produce it. In `web/app.js`: the `SESSION_LINES` new/resumed/lost text
and the by-session-file suffix.

**What stops being needed as a consequence, rather than by instruction:**

- The settle timeout, the wedged-child replacement, and the one-deadline-
  for-the-whole-loop rule in `brain_harness` (the rule survives in the
  adapter's own read helpers; the caller-side loop it guarded is gone).
- The desired-vs-running spec comparison and everything that hung off it:
  four `_forget_a_stale_grant` call sites, the reconcile-turn-after-the-move
  test shape `test/AGENTS.md` documents, `mutate_project_switch.py`'s grant
  mutations (the file survives for the matcher, thinned from 111 lines).
- `NEEDS_HARNESS`'s extra injections: the built child and the permission
  mode. What remains injected is a `harness_for()` callable (a fresh adapter
  from the current `harness` setting), `spec_for`, and the chat store.
- The permission mode's ONLY enforcement point. `modes.py`, the store, the
  UI and the asymmetry stay (AC-8 is met and Phase 2's workers need them),
  but until the Worker Manager lands, `paranoid`/`secretary`/`trusted`
  gate NOTHING. That is stated in 10.6 as a cost, not buried here.
- Rail prose: `daemon/AGENTS.md` loses the write-grant contract, the
  `_grant_holds` bullet, the sessions bullet, the spawned-at-boot bullet and
  the new/resumed/lost bullet; `daemon/adapters/AGENTS.md` loses the
  mode-to-tools and fake-session-features text; `test/AGENTS.md` loses the
  two dead mutation instruments and the guard-before-respawn rule;
  `web/AGENTS.md` loses the agent-memory-line contract. Each edit lands in
  the same commit as the code it describes (D-12).

**Rewritten rather than deleted:** `test/test_brain_harness.py` (23 tests --
the AC-46 assertions invert, the settle/wedged/recovery tests go, the
never-silent and one-Done tests stay), `test/test_project_switch.py`
(12 tests, minus the grant-revocation assertions),
`test/conformance/test_harness_conformance.py` (12 tests -- specs lose
`tools`, `dispose`-then-`start` stops being a special property and becomes
the routine path), `test/test_harness_pi.py` (27 tests -- the mapper,
framing, metacharacter and dialog tests survive untouched; the argv tests
lose their tools rows), the 7 harness cases spread through
`test/test_conversation.py` and the hub stubs in `test/conftest.py`.

### 10.2 What replaces it

**A turn, end to end.** `_on_final` -> router (unchanged, `dialogue.py`) ->
`HarnessConverse.turn`:

1. Build the spec: `spec_for(cwd=self._cwd)` -- model and default cwd from
   settings per turn as today, session reference from the chat store. No
   tools field.
2. Build a fresh adapter from `harness_for()` and `start(spec)`: for pi that
   is `cmd /c pi --mode rpc --session <file> [--model m]` -- no `--tools`
   flag -- then the `get_state` ready gate and `set_auto_retry off`, both
   unchanged.
3. `prompt(text)`, stream events exactly as today: sentence-split `Say`s,
   one narration line on the first tool, `turn_end` text recovery when
   nothing streamed, exactly one `Done`.
4. On the terminal event: append the user and assistant messages to the chat
   file, `dispose()` the child (stdin EOF, `taskkill /F /T` fallback, job
   object behind both). The child is GONE between turns, by design.

Barge-in mid-turn: `abort()` (pi settles in 1.51 s and still emits its
terminal event [MEASURED 2026-08-17]), then dispose. This is a deliberate
divergence from the reference, which tree-kills on abort: claude's
transcript lives in claude's own store and survives a kill, while pi is
mid-write to a session FILE Butler wants intact next turn, so pi gets the
graceful exit it measurably honours. The next turn needs no settle read:
no stream survives a turn.

A `Failed` before anything was spoken still gets ONE replay -- dispose,
fresh child on the same reference, the verbatim prompt again (4.4c). That is
the reference's own dead-resume replay (`resumeRecoveryUsed`, one per
backend) generalised by one step, and without it a transient provider blip
is a spoken failure with nothing behind it.

**Where the session reference is captured and stored.** For pi, Butler names
the file, exactly as today -- the only change is where the name lives (the
chat record, below) and that the path moves into the chat's own terms. For a
future claude adapter, the id arrives on the child's `init` line and is
handed back after the turn; the port gains whatever narrow member that needs
WHEN that adapter lands, not now -- pi does not need it, and a capability
with no method behind it is section 9's own warning.

**The chat record.** `daemon/chats.py`, storing one flat JSON file per chat
under `state/chats/chat-<ms>-<rand>.json`, rewritten whole on every append
(atomic replace kept -- it is ten lines and a torn record on the worst boot
is the alternative). The fields, decided against the reference's thirteen:

```json
{"id": "chat-1776568428712-0kze41",
 "createdAt": 1776568428712,
 "updatedAt": 1776610601555,
 "harness": "pi",
 "sessionRef": "E:\\...\\state\\chats\\chat-...-pi.session.json",
 "messages": [{"id": "msg-...", "role": "user", "content": "...",
               "timestamp": 1776568436645}]}
```

Butler takes `id`, `createdAt`, `updatedAt`, `messages` and their
`{id, role, content, timestamp}` shape verbatim from the reference [READ
2026-08-18, three pi files and one claude file in
`E:\PROJECTS\prototype\CHAT-SESSIONS\`]. It does NOT copy `title`,
`titleSource`, `pinned`, `archived`, `parentChatId`, `parentMessageId` or
`channelId` -- those are the reference's chat-list, forking and
multi-channel features, and Butler has one current conversation and no chat
list. The reference's per-backend pair (`piSdkSessionId`,
`claudeSessionId`) collapses to `harness` plus one `sessionRef`, because a
Butler chat runs one harness; "exactly the same system" is read as the
SHAPE and the simplicity, not as cargo-culting `pinned`. The store is under
150 lines [TARGET].

- Boot re-attaches to the chat with the newest `updatedAt` -- which IS the
  explicit last-active stamp 4.4c requires, now one field instead of a
  ceremony.
- The spoken "new conversation" (`spoken_new_conversation`, kept) mints a
  new chat file. No rotation machinery: a new chat is a new file.
- A dead reference is cleared on the harness's literal words for a missing
  session, one narrow regex, exactly where the reference does it -- and for
  pi that path is inert, because pi creates a missing session file rather
  than refusing it, so the machinery waits for the claude adapter instead
  of existing speculatively here.

**How the page gets its transcript.** `GET /api/session/current` serves
`{id, harness, messages}` from the chat file, bounded to the newest
`MESSAGES_SERVED`; `ready` carries `{session: id, turns: N}`. `loadSession`
renders `messages` as turns, replacing not appending, as `web/AGENTS.md`
already requires. The new/resumed/lost line is gone (10.6 names what that
loses).

### 10.3 What is KEPT, and why

Deleting something that earned its place is as bad as keeping something
that did not. Each of these exists because of a measured defect, and each
survives the rulings:

- **The `Harness` port, `HARNESSES`, the fake, and one conformance suite**
  (AC-44). Two callers are still real -- `brain_harness` now, the Worker
  Manager in Phase 2 -- and per-message spawning changes how the port is
  USED, not what it promises. `dispose`-then-`start`-is-a-fresh-child stops
  being an edge property and becomes the routine path, which the suite
  already proves.
- **The six-event mapping and the exported `TERMINAL_EVENTS` pair** (AC-48,
  4.4c). The predecessor's provider failure held a turn lock forever; a
  per-message child makes the ending MORE load-bearing, because a prompt
  with no terminal event now also leaks a process per question.
- **The mapper's two gates in `harness_pi`**: nothing mapped outside a
  pending prompt, and a terminal event only for the prompt pi accepted.
  Fresh children shrink the late-`agent_end` window but pi still talks at
  startup and its retry behaviour is still real; the gates are cheap and
  the failure they stop is announce-five-times.
- **LF-only framing** (`_Framer`): U+2028 is legal inside a JSON string and
  pi's own rpc.md warns about it.
- **`cmd /c` plus the metacharacter refusal**: a model value ran a second
  command on this machine [MEASURED 2026-08-17], and `harness_model`,
  `harness_cwd` and the spoken project name still reach this argv.
- **The startup verdict at the child's CLOSE with the stderr tail** (4.4c),
  and the startup/command timeout split (D-5). Now exercised once per
  message instead of once per boot, so they matter more.
- **`turn_end` text recovery, guarded on silence** (AC-47): the no-delta
  provider was measured, and the guard is what stops streaming providers
  being read out twice.
- **Dialog cancellation** (AC-49): three unprompted `extension_ui_request`s
  arrive before the first response [MEASURED], now at the top of EVERY
  message's child.
- **`childproc.py` and `test/test_child_lifetime.py`, unchanged** (AC-36,
  D-24): per-message spawning multiplies child creation by the number of
  questions asked, so the job object is the difference between one orphan
  per crash and one orphan per question. All three tests stay.
- **The project matcher** (AC-31): exact, twice-gated, refused aloud, the
  explicit/bare grammar split -- minus its grant-revocation clauses. The
  session-scoped `_cwd` that never rewrites `harness_cwd` stays.
- **`spoken_new_conversation` and the daemon-answered utterances** in
  `_on_final`, unchanged.
- **Never-silent, one `Done`, every path re-arms** (AC-47, AC-48, D-1), and
  abort matched on the TURN (AC-43).
- **The router in front** (`dialogue.Dialogue`): both rulings are about the
  back half; the routing is untouched -- and it is now most of what stands
  between a false accept and the harness (10.6).

### 10.4 The amended criteria, ready to paste into plan.md

- **AC-29 -- RETIRED 2026-08-18, by the owner.** Replacement text:

  > **AC-29 (RETIRED 2026-08-18).** This criterion required a harness child
  > to hold only the tools its permission mode allowed, with a spoken
  > once-per-conversation grant gating writes and `bash` reserved for
  > `trusted`. The owner retired it: the harness always runs at full
  > capability, `bash` included, in every mode -- "i just want full agent
  > capability". The argument that lost, recorded as this plan records
  > them: a wake-word false accept (0.56-1.5 per hour on `hey_jarvis`,
  > section 7 and Appendix A, with no speaker verification until Phase 4)
  > now reaches a
  > tool-enabled agent with `bash` in the configured project, and the two
  > consent holes the grant's review found (section 6 of
  > docs/harness-mode.md section 9) show how hard that boundary was to
  > hold. What remains between a false accept and a write: the router must
  > route the utterance to the harness at all, the project list bounds
  > where the agent runs, and the owner's own room. The permission modes
  > (AC-8, AC-10) remain for Phase 2's worker dispatch, where AC-9's
  > repeat-back still applies; until then they gate nothing.

- **AC-31 -- AMENDED.** The criterion survives; strike the two grant
  sentences ("Moving revokes the write grant, because consent for one
  repository is not consent for another (AC-29)" and the equivalent clause
  in the daemon rail). One recording sentence is added:

  > (The clause revoking a write grant on a move was deleted with AC-29 on
  > 2026-08-18: there is no grant to revoke. The old version claimed a move
  > must re-ask consent; the owner ruled there is no consent ceremony.)

- **AC-35 -- REWRITTEN.** Replacement text:

  > **AC-35 (a harness conversation survives a daemon restart).** Talk to a
  > harness, restart butlerd, and the next thing said continues the same
  > conversation. The record is ONE flat JSON file per chat in
  > `state/chats/` -- `id`, `createdAt`, `updatedAt`, `harness`,
  > `sessionRef`, and `messages` of `{id, role, content, timestamp}` --
  > rewritten whole, matching the reference implementation's CHAT-SESSIONS
  > model by the owner's ruling of 2026-08-18. The transcript the page
  > shows is the `messages` array; the agent's own context comes back
  > through `sessionRef` -- a session file pi is pointed at, an opaque id a
  > claude adapter passes to `--resume` -- read off `caps.native_resume`,
  > never off the name. Boot re-attaches to the newest `updatedAt`. A
  > reference the harness refuses with its literal missing-session words is
  > cleared, once, and the turn replayed once (the predecessor's eight dead
  > chats). The old version kept a per-session directory with `meta.json`
  > and an append-only `events.jsonl` recording every tool call, rotated
  > rather than replaced, and told the page which of `new`/`resumed`/`lost`
  > the context was; the owner overruled it for the reference's flat-file
  > shape -- the argument that lost was that the tool-level record and the
  > agent-memory honesty line were worth their machinery.

- **AC-44 -- AMENDED, still PARTLY MET.** Two edits: `HarnessCaps` loses
  `tool_allowlist` (nothing reads it once tools are unrestricted -- the
  section 9 rule about capabilities with no method), and the contract's
  lifecycle sentence changes from "held open across turns" to "started and
  disposed per prompt". `native_resume` stays, label-only as before, and
  becomes real with the claude adapter. Recording sentence:

  > (Amended 2026-08-18: `tool_allowlist` was deleted with AC-29, and the
  > per-message lifecycle replaced the held-open child by the owner's
  > ruling. The old version froze a tool set at spawn; there is no tool set
  > to freeze.)

- **AC-46 -- REPLACED.** The old criterion is inverted by ruling 2.
  Replacement text:

  > **AC-46 (one child per message, and none in between).** Every harness
  > turn spawns a fresh child and disposes it at the turn's end; no child
  > is held between turns, asserted on the fake's record of every
  > `start()` (exactly one per prompt) and by PID absence between turns.
  > The cost is accepted and stated: pi's spawn-to-prompt-accepted is
  > 1.5-2.3 s [MEASURED 2026-08-17, 21 runs], paid as dead air on every
  > question. The old version required the opposite -- a child spawned at
  > boot and never on the routine path, precisely to avoid that dead air,
  > which reads as a dead microphone (D-10) -- and the owner overruled it
  > on 2026-08-18 to match the reference implementation's per-prompt CLI
  > lifecycle: the argument that lost is the 2 seconds, and it lost to
  > simplicity, not to a measurement.

- **AC-49 -- AMENDED in one sentence.** The criterion and its
  transport-level proof are unchanged; the exposure changes:

  > (Amended 2026-08-18: with a child per message, the three unprompted
  > startup dialogs arrive at the top of EVERY turn rather than once at
  > boot, so the cancellation path is now on the routine path. The
  > caller-level gap recorded here is unchanged.)

AC-36, AC-47 and AC-48 are untouched. AC-27's harness half now reads "a
fresh `pi` session per message" rather than "a persistent session"; 4.4's
"held open as a PERSISTENT multi-turn session" sentence and 4.4c's
directory diagram are rewritten in the same commits (D-12).

### 10.5 What this costs, plainly

- **About two seconds of dead air per question, every question.** Spawn to
  prompt-accepted is 1.5-2.3 s [MEASURED 2026-08-17, 21 runs], on top of
  the model's own time, and no model choice reduces it -- it is pi coming
  up, not a model loading. The old design existed to hide exactly this
  (D-10: silence reads as a dead microphone). Mitigation belongs to 10.8.
- **A false accept now reaches a full-capability agent.** The measured
  false-accept rate is 0.56-1.5 per hour (plan.md section 7 and Appendix A,
  `hey_jarvis`),
  there is no speaker verification until Phase 4, and the grant ceremony
  that stood between a misheard utterance and a write -- `bash` included --
  is gone. The honest framing: Butler now trusts the router's verdict and
  the owner's own room, not a ceremony. The two consent holes review found
  in the grant (section 9) stop existing because the ceremony stops
  existing -- they are deleted, not fixed.
- **The permission modes gate nothing until Phase 2.** The store, the UI
  and the asymmetry survive for AC-8/AC-10, but between this refactor and
  the Worker Manager, `paranoid` and `trusted` are words on a settings
  page.
- **The page loses two honest things**: the line saying whether the agent
  still remembers the conversation (`new`/`resumed`/`lost`), and the
  tool-level transcript (`events.jsonl` recorded every tool with name and
  outcome; `messages` records roles and text). Both are display-only losses
  -- nothing replays a transcript into a harness -- but a resumed page can
  no longer show what the agent DID, only what it said.
- **The chat file is rewritten whole per message**, so its size is
  re-paid on every append. At voice-conversation lengths that is noise; it
  is noted because "rewritten whole" is the reference's simplicity being
  bought with the reference's write pattern.

### 10.6 Refactor order, tests named, deletion by commit

Each commit lands green, carries its own rail (AGENTS.md) and plan.md edits
(D-12), and the full suite is the proof nothing else broke:
`.venv\Scripts\python.exe -X utf8 -m pytest test\ -q` from 504 collected
[MEASURED 2026-08-18] down to the counts each step leaves.

**Commit 1 -- delete the grant (ruling 1, AC-29 retired).** Remove the
grant block from `brain_harness.py`, the four confirmation methods and
pending state from `hub.py`, `spoken_confirmation` from `modes.py`, the
`permission_mode` injection from `butlerd.py`, `HarnessSpec.tools`, the
`--tools` branch in `harness_pi._argv`, and `tool_allowlist`. Delete
`test/test_harness_grant.py` (22 tests) and
`test/manual/mutate_grant_review.py`. Rewrite: the grant-revocation
assertions out of `test/test_project_switch.py`, the tools rows out of
`test/test_harness_pi.py` and the conformance suite, the mode-derived
spec assertions out of `test/test_brain_harness.py`. plan.md: AC-29
retirement text, AC-31 and AC-44 amendments. Must keep passing:
`test_protocol_v1.py`, `test_turn_modes.py`, `test_hub_bargein.py`,
`test_conversation.py` -- the regression net around `_on_final`, which
loses a branch and must not change the others.

**Commit 2 -- fresh child per message (ruling 2, AC-46 replaced).**
Rewrite `_Host` to spawn-per-prompt through `harness_for()`; delete
`_settle`, `SETTLE_TIMEOUT_S`, `refresh`, the boot warm, the dead-child
recovery; drop the `restart` flag on the `harness` setting. Rewrite
`test/test_brain_harness.py`: the healthy-child-records-no-start and
refuses-with-no-child tests invert to exactly-one-start-per-prompt and
no-child-between-turns; the settle and wedged tests are deleted; the
never-silent, one-Done, narration, router-in-front, abort and replay tests
are re-pointed and stay. plan.md: AC-46 replacement, 4.4 lifecycle
sentence. Must keep passing: the whole conformance suite unchanged, and
`test_child_lifetime.py` unchanged -- the job object is what makes
per-message children safe to multiply.

**Commit 3 -- the flat chat file (ruling 3, AC-35 rewritten).** Add
`daemon/chats.py`; delete `daemon/sessions.py` (345 lines),
`test/test_harness_sessions.py` (19 tests), `test/manual/mutate_sessions.py`;
strip `reject_session` and the context-file behaviour from
`harness_fake.py`; rewire `butlerd.py`'s route and `NEEDS_HARNESS`
injection; replace `web/app.js`'s session line with the messages render.
New `test/test_chats.py`: restart re-attach by newest `updatedAt`, the
spoken rotation mints a new file, a corrupt file is loud and not fatal, the
live restart test re-pointed at `sessionRef` (the D-13 mutation stays:
point the second child at a different reference and the recall test must
fail). Must keep passing: `test_http_surface.py`,
`test_connections.py::test_nothing_under_state_holds_a_key_value` --
`state/chats/` is new state and the no-keys grep must cover it.

**Commit 4 -- the DOX and prose sweep.** Anything the first three commits'
rail edits missed: 4.4c's directory diagram, `daemon/AGENTS.md`'s counts,
this document's section 1-9 claims that are now historical (marked, not
rewritten -- they are the argument, and section 9 stays as the record).
`handoff.md` rewritten.

### 10.7 The 10% to add back

Musk's rule: if nothing wants adding back, the cut was too timid. Two
candidates, in order:

1. **A reflex acknowledgment covering the spawn gap.** One fixed line
   ("Working on it") spoken the moment the router routes to the harness,
   BEFORE the 1.5-2.3 s spawn, so the dead air ruling 2 buys is filled by
   the same mechanism the tool narration already uses. About five lines in
   `HarnessConverse.turn`, and it converts the refactor's sharpest cost
   into a UX tic. This one should land with commit 2.
2. **The missing-session clearing, when claude lands.** The narrow-regex
   clear plus one replay is kept in principle (10.2) but is inert for pi.
   The claude adapter re-earns it on day one -- eight dead chats is the
   reference's own receipt -- and that is the moment `sessionRef` clearing
   gets its test back.

Honestly, nothing else. The grant, the settle, the events log and the
new/resumed/lost line were all built to a standard the owner has ruled this
phase does not need; if the false-accept exposure in 10.5 proves real in
use, the right thing back is not the grant ceremony but a confirmation
UPSTREAM of the router (a wake-word double-check, Phase 4's speaker
verification) -- the lesson of the two consent holes is that this boundary
is cheapest to hold before the agent, not around it.

### 10.8 Where the implementation diverged from this section

Recorded rather than edited into the proposal above, for the reason section 9
exists: the gap between what a plan expected and what building it found is
the only part a later reader cannot reconstruct.

1. **`proceed()` was KEPT, and 10.1 was wrong about why it existed.** 10.1
   listed it for deletion, describing it as existing "only so a confirmed
   utterance could skip the router". It is in fact the ONLY path by which a
   router `dispatch` verdict reaches the coding agent: the router turns
   `dispatch` into `AskConfirm` and never into an action, and
   `hub._on_ask_confirm` hands that straight back to the brain. Deleting it
   would have made every dispatched instruction unanswerable in harness
   mode. The capability flag that gates it was renamed `CAN_GRANT` ->
   `CAN_PROCEED` in the same commit, because the name was the thing that
   made the mistake readable.

2. **The test arithmetic in 10.1 and 10.6 was wrong in both directions.**
   The deleted files collected MORE cases than the table claimed --
   `test_harness_grant.py` collected 37 against "22", `test_harness_sessions.py`
   26 against "19", both because of parametrization the line counts could not
   see -- and the replacements ADDED tests for behaviour the rulings kept
   (`test_brain_harness.py` 23 -> 29, `test_conversation.py` 7 -> 13,
   `test_chats.py` 17, of which 8 are parametrized cases for
   `spoken_new_conversation`, which survives). Net: 504 -> 465 collected,
   not the ~40-fewer-in-two-files the tables implied. [MEASURED 2026-08-18
   by `--collect-only` at `0dcb44f` and at the head of the sweep.]

3. **Disposal at the terminal event was not enough on its own.** 10.2 step 4
   said the child goes at the terminal event. It does -- but `hub._reply`
   BREAKS out of the brain's turn generator on four routes and resumes none
   of them, and three of the four never reached the adapter's own backstop,
   which is guarded on still owning the turn. The review after the commit
   found a leaked full-capability `pi` tree on the `Done`, the synthesis
   failure and the swallowed exception. The fix is a caller-side `_let_go`
   plus a `start()` slot guard, both bounded by `BRAIN_LETGO_S = 3.0` --
   D-18 again, and the proposal did not see it coming.

4. **A clear-and-replay on a reference Butler MINTS is a no-op.** 10.2 kept
   the missing-session clearing "in principle", inert for pi. It is inert in
   a second way the section did not state: `ChatStore.session_ref` derives
   the path from the chat id, so clearing it and asking again returns a
   byte-identical path [MEASURED 2026-08-18]. The clause needs a harness
   that hands back an opaque id, which is 10.7 item 2 either way.

5. **`caps.native_resume` lost its last reader.** 10.3 kept it, correctly,
   as the rule a caller reads instead of a harness name. What went with
   `daemon/sessions.py` was the display string it fed, so nothing in the
   daemon now branches on it at all -- it is declared, frozen by conformance
   and unread, on the same terms as `Action.writes`. plan.md's AC-44 says so.
