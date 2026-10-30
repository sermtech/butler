# Worker mode -- spec and build order for Phase 2

**PROPOSAL, written 2026-08-18.** `plan.md` remains the build contract; where
this document and `plan.md` disagree, the plan wins. This document follows
the path `docs/harness-mode.md` walked: its criteria (AC-50..AC-54) move into
`plan.md` section 2 only as the phase is accepted, and until then they are an
argument, not a contract. Section 12 is this document's equivalent of
harness-mode.md's section 9 -- empty now, written by whoever builds this.

Scope: `plan.md` section 5 Phase 2 -- the confirmation ceremony (AC-9) and
the spoken mode changes (AC-10), the Worker Manager (AC-11, AC-12, AC-13),
the steer route, and the announcer (AC-14, AC-14a). AC-8 is already MET.

Written the day the phase-7 simplification shipped (harness-mode.md section
10), which is why it exists at all: four of that refactor's consequences land
on Phase 2 and the plan's Phase 2 text predates all of them. This document
owns those collisions instead of leaving them to be rediscovered mid-build.
Section 11 lists the `plan.md` statements the refactor and this spec's own
measurements show to be false or in tension, for the owner to rule on; this
document edits none of them.

---

## 1. What was MEASURED before any of this was designed

harness-mode.md section 1 exists because an unmeasured 9-10 s figure drove a
whole design and was wrong. The same discipline here: everything below was
run on this machine on 2026-08-18 against `claude` 2.1.224
(`C:\Users\user\.local\bin\claude.exe`), from a driver script, with tiny
`--model haiku` prompts. Total spike cost about $0.08. Where a number rests
on ONE run it says so; a single run pins a mechanism, not a distribution.
The plan's own demand (section 5, Phase 2 step 3) was to pin three things
with the first red test; all three are pinned here, plus four findings the
plan did not ask for, one of them alarming.

**1. The stdin user-message schema** [MEASURED 2026-08-18]. One LF-delimited
JSON line:

```json
{"type": "user", "message": {"role": "user",
 "content": [{"type": "text", "text": "..."}]}}
```

Accepted, answered, and -- with `--replay-user-messages` -- echoed back on
stdout as a `user` event before the assistant's reply, which is the
acknowledgment AC-11's steering wants: the echo is proof the line was TAKEN,
the same identity gate `harness_pi` builds from pi's command ids.

**2. A positional prompt under `--input-format stream-json` is silently
ignored** [MEASURED 2026-08-18]. `claude -p "say OK" --input-format
stream-json ...` with stdin closed immediately produced NO assistant turn,
NO `result`, and **exit 0**. The session started, hooks fired, and EOF ended
it before the positional prompt was ever processed. So the prompt MUST be
the first stdin JSON line -- and the failure mode of getting this wrong is
the worst available shape: a worker that does nothing and reports success.
The adapter treats "exit 0 with zero `result` events" as `Failed`, never as
a clean end. Two more argv facts from the same runs: `--output-format
stream-json` without `--verbose` exits 1 immediately with one stderr line
(the child never comes up), and `claude --help` confirms `--input-format` /
`--output-format` only work with `--print` [MEASURED via --help, matching
what plan.md 4.5 recorded on 2026-08-16].

**3. `--settings <file>` installs Butler's hooks in an arbitrary working
directory** [MEASURED 2026-08-18]. A Stop hook declared in a settings file
passed by absolute path fired from an empty temp directory with no `.claude`
anywhere near it, and its stdin payload carried `session_id`,
`transcript_path`, `cwd`, `prompt_id`, `stop_hook_active` and
`last_assistant_message`. Two Windows traps measured on the way, recorded
for whoever ever revisits hooks: hook commands are executed through
`/usr/bin/bash` even on Windows, so a backslashed path in the command string
is eaten (`C:UsersadmAppData...: command not found`) -- hook command paths
must use forward slashes -- and the hook is registered as an async hook with
a 600 s default timeout. **This finding is preserved but the design below
does not use hooks at all** (section 5.3): the reason to want them died when
AC-36 put every child in the kill-on-close Job Object.

**4. With stdin held open, `result` is per RESPONSE and the process idles
between them** [MEASURED 2026-08-18, one full run]. Message 1 in; `init` at
4.5 s; answer; `result` at 9.8 s; the process stayed alive through 3 s of
deliberate idle; message 2 in; a second `init` 20 ms later; second answer;
second `result`; stdin closed; **exit 0 about 2 s later**. This is exactly
the Stop-fires-per-response shape 4.5 warned about, arriving on stdout
instead of through a hook -- and it confirms the 4.5 completion rule (idle
grace, close stdin, exit is the end) works with no hook anywhere. It also
means `result` maps to `TurnEnd`, never to `AgentEnd`: an adapter that maps
`result` to a terminal event announces a steered worker's completion once
per steering round, which is AC-48's announce-five-times failure in new
clothes.

**5. An unrestricted claude child inherits the owner's account-level MCP,
measured, in the bad direction** [MEASURED 2026-08-18]. The `init` line of a
child spawned in an EMPTY temp directory listed 101 tools, 70 of them
`mcp__*`, including **connected** claude.ai Gmail, Google Calendar, Google
Drive and Canva servers, plus the owner's local MCP servers loading. plan.md
4.4c called this exact question unmeasured; it is now measured, and the
answer is that a voice-dispatched background worker would hold the owner's
mailbox unless something strips it. Section 5.5 rules on it; the dormant
allow-list rule 4.4c kept is what wakes up. The owner's user-level plugins
also load: the superpowers SessionStart hook fired in every single run and
injected 4101 chars of context, and its `hook_started` / `hook_response`
events appear in the stdout stream, so the adapter must drop `system/hook_*`
events or narrate the owner's plugin chatter.

**6. claude streams NO text deltas by default** [MEASURED 2026-08-18].
Assistant messages arrive whole; token streaming needs
`--include-partial-messages`, which is deliberately not used (section 9).
So the claude adapter is a no-delta provider by construction -- the exact
shape AC-47 was built for, and the guard `brain_harness` already carries
(text recovered at `TurnEnd`, guarded on nothing having been said) is what
makes a future claude CONVERSATION adapter work unchanged.

**7. What this run cost and how long it took** [MEASURED 2026-08-18, n=1
each]: spawn to `init` 4.5 s, send to `result` 9.8 s on haiku with thinking
enabled, about $0.025 per trivial turn. The spawn is roughly twice pi's
measured 1.5-2.3 s; a single run, so treat it as "seconds, not hundreds of
milliseconds" rather than as a distribution. For a BACKGROUND worker this is
not dead air: the ceremony's own spoken confirmation covers it, and the
announcement arrives whenever it arrives.

**What was NOT measured, named as such:**

- **Mid-run steering delivery** [ASSUMED]. The spike proves a second message
  is taken BETWEEN responses in the same process. It does not prove a
  message written while tools are running is folded into the run in
  progress. AC-11's literal words ("without restarting the process") are met
  either way; whether a steer lands mid-response or at the next response
  boundary is pinned by step 2.2's live test, and the design does not depend
  on which.
- **The 30 s slow-stdout exit stall** stays [READ, the project brief]. Not
  reproduced here and not worth reproducing: the eager drain (D-16) is built
  regardless, and AC-13's 5 s bound is the test.
- **`--strict-mcp-config` actually stripping the 70 tools** [ASSUMED from
  the flag's documented semantics]. The spike measured the inheritance, not
  the cure. Step 2.2's red test asserts the cure on a live `init` line.
- **The wake false-accept rate** 0.56-1.5/hr is [REVIEW
  home-assistant-satellites.md], the idle grace 10 s and the announcement
  age 1 hour are [DECIDED, plan.md 4.5 and AC-14a], the announcer queue
  bound in section 6 is [TARGET]. Suite baseline going in: 465 passed
  [MEASURED 2026-08-18, handoff.md, three runs].
- **claude's own version drift** is a standing hazard, not a number: the CLI
  self-updates, and every mechanism above is pinned against 2.1.224. The
  live tests are the tripwire, not a guarantee.

---

## 2. User story

> As the owner, I want to DISPATCH real coding work by voice -- confirm it
> once out loud, hear short progress lines while it runs, steer it mid-run
> without restarting it, and hear one sentence when it is done, even if I
> left the room -- so that background work happens while I talk to Butler
> about something else.

The thing being built is not "run claude in the background". It is three
boundaries: the spoken ceremony that stands between a wake-word false accept
and a spawned agent (the ONLY such ceremony left anywhere in Butler, since
AC-29's retirement removed the talk path's), the queue that lets Butler
speak without being spoken to, and the port growth that keeps "which coding
agent" swappable while a second caller starts using it.

---

## 3. Acceptance criteria

AC-8..AC-14a already exist in `plan.md` section 2 and are NOT restated here;
they are cited throughout. AC-9's machinery was deleted on 2026-08-18 and is
rebuilt to the shape in section 7 -- the criterion is unchanged, the build is
from nothing. The criteria below are the NEW ones this phase turns out to
need, numbered from AC-50 as the next free number.

- **AC-50 (an announcement is a turn).** Unsolicited speech reaches the room
  as a TURN the daemon mints: one `announce` frame carrying a fresh turn id
  and the text, to exactly ONE satellite, followed by the existing
  `speak_start` / audio / `speak_end` / `playback_done` machinery, ending at
  `_rearm()` like every other turn (D-1). Asserted on the ordered wire
  events. A wake candidate during an announcement abandons it exactly as it
  abandons a reply -- and the abandoned announcement is DROPPED, never
  re-queued: the owner interrupted it, and repeating it is nagging. Nothing
  else in the protocol changes, which is the point: the page's turn
  discard (D-6), the playback gate (D-19, R1), the cue suppression and the
  wake cooldown (D-14) all apply to announcements because an announcement
  is not a new kind of thing.

- **AC-51 (the announcer queue is bounded and honest).** The announcer is
  the single coordination point for unsolicited speech: worker completions
  and failures at one priority, narration lines below them, emitted only
  when EVERY connected session is idle (AC-14's three states, evaluated
  across all sessions -- an announcement into a room where the owner is
  mid-sentence on ANOTHER device is the preemption AC-14 forbids). The
  queue is bounded; under pressure narration is dropped first, oldest
  first, and dropping is logged; completions are never dropped except by
  AC-14a's age rule. Emission is triggered from `_rearm()` -- the single
  exit every terminal path already reaches -- so there is no second
  "is the room quiet" predicate to drift (D-18).

- **AC-52 (steering is a port capability, refused honestly).** `Harness`
  gains `steer(text)`, gated by the existing `caps.steerable`; a steer
  routed to a worker whose harness answers False is refused ALOUD, naming
  the harness, and nothing is written to the child. Proven on the fake and
  on one real steerable adapter through the same conformance suite, and on
  the CALLER (D-18): the steer verdict reaches `WorkerManager.steer`, and
  a steer for a worker id that no longer runs is a spoken "that job already
  finished", never a silent drop.

- **AC-53 (a worker's fate is always announced, exactly once).** Every
  dispatched worker ends in exactly one announced terminal state:
  completed (AC-11's rule: idle grace expired, input closed, process
  exited), failed (the failure line, from the stderr tail at the child's
  close, 4.4c), or DIED WITH THE DAEMON -- discovered by the boot
  reconcile as a record with no terminal event, appended as such, and
  announced through the held-announcement path (AC-14a). One announcement
  per worker, whatever the path, asserted by counting announce frames
  across completion, failure, kill-and-restart and barge-in cases. This is
  AC-12's intent in the only form AC-36 leaves possible; section 11 item 1
  is the owner's call on the letter.

- **AC-54 (a worker holds no account surface the owner did not give it).**
  A dispatched claude child is spawned so that the owner's account-level
  MCP servers -- measured at 70 tools including a connected Gmail -- do NOT
  load: `--strict-mcp-config` with no `--mcp-config`, asserted on the live
  child's own `init` line (zero `mcp__*` tools), not on the argv. The
  argument that lost, recorded: the owner might WANT their MCP in a worker;
  when they do, that is an explicit opt-in setting with a reader, added
  then, not an inheritance nobody chose. This is 4.4c's dormant allow-list
  rule waking up for its named successor, in claude's own vocabulary.

---

## 4. Gherkin

3.3 (the ceremony), 3.4 (asymmetric modes), 3.5 (dispatch, narration,
steering, completion) and 3.8's daemon-restart case already cover the happy
paths and are not restated. What follows is what they do not cover, in
3.8's edge-case register.

```gherkin
Scenario: An announcement is a turn on one device (AC-50, AC-14 case c)
  Given two satellites are connected and both are wake-listening
  When a worker completes
  Then exactly one satellite receives an announce frame with a fresh turn id
  And the audio flows through the ordinary speak machinery to that device
  And the page re-arms through playback_done, never through speak_end (R1)
  And the other satellite sees the announcement text and hears nothing

Scenario: Barge-in mid-announcement (AC-50, D-4)
  Given an announcement is being spoken
  When a wake candidate arrives above the raised speaking bar (D-15)
  Then the announcement's turn is abandoned like any reply
  And it is NOT re-queued and NOT spoken again
  And its text remains on the web UI

Scenario: The room is busy on another device (AC-51)
  Given the owner is mid-capture on the phone
  When a worker completes
  Then no announce frame is sent to any device
  And the announcement is emitted after the phone's turn fully ends

Scenario: Narration is dropped before completions (AC-51)
  Given the announcer queue is at its bound
  When another narration line arrives
  Then the oldest narration line is dropped and the drop is logged
  And no completion announcement is ever displaced by narration

Scenario: Completion with nobody connected, then too late (AC-14a, AC-53)
  Given a worker completes while no satellite is connected
  Then the terminal event is on disk and the announcement is held
  When a satellite connects and reaches wake-listening within the hour
  Then the announcement is spoken once, on that device only
  When instead the first satellite arrives after the age limit
  Then nothing is spoken and the completion is visible on the web UI

Scenario: The daemon dies with a worker running (AC-53, AC-36)
  Given a worker is mid-run and butlerd is killed with TerminateProcess
  Then the worker process is dead within the Job Object's window (AC-36)
  When butlerd restarts
  Then the reconcile finds a record with no terminal event
  And appends a died event and announces the worker's death once
  And does not claim completion for work that did not complete

Scenario: Steering a worker that cannot be steered (AC-52)
  Given a running worker whose harness caps.steerable is False
  When the owner says "tell it to also update the readme"
  Then the refusal is spoken and names the harness
  And nothing is written to the worker's stdin

Scenario: Steering a worker that already finished (AC-52)
  Given the only worker completed two seconds ago
  When the owner addresses a steering instruction to it
  Then Butler says the job already finished, and nothing is dispatched

Scenario: The confirmation is frozen at question time (AC-9)
  Given the repeat-back for "delete the logs in project butler" was spoken
  When the owner changes harness_cwd or says "work in smart crawler"
  And then says yes
  Then the dispatch runs with the directory the QUESTION named
  # Consent hole 2 from harness-mode.md section 9, kept dead: consent for
  # one repository is never honoured in another.

Scenario: Barge-in during the repeat-back parks nothing (AC-9, D-17)
  Given Butler is mid-way through the repeat-back
  When a wake candidate abandons that turn
  Then no confirmation is pending, however long the engine aborts take
  And the interrupting utterance is answered as itself, never as consent
  # Consent hole 1, kept dead: the park is guarded on still owning the
  # turn AND abandon() re-clears after the turn is gone.

Scenario: The offer expires OUT LOUD this time (AC-9, AC-50)
  Given the repeat-back has been spoken and nothing was answered
  When the confirmation window passes
  Then no process has been spawned
  And the cancellation is spoken through the announcer when the room is idle
  # harness-mode.md 7.5 had to SHOW this notice because every audible path
  # was turn-scoped. The announcer is the named gap, built; the expiry is
  # its first non-worker client.

Scenario: A dispatch names a directory outside the projects list (AC-31)
  Given the projects list holds butler and smart crawler
  When a confirmed dispatch carries any other cwd
  Then the dispatch is refused aloud and no worker is spawned
  # The resolver's second gate (harness-mode.md 7.7) -- the Worker Manager
  # is the caller that gate was kept for.

Scenario: A worker child starts stripped (AC-54)
  Given a dispatch is confirmed
  When the claude child's init line arrives
  Then it lists no mcp__ tools
  And the worker still completes normally
```

---

## 5. The port decision, and the rulings that ride on it

### 5.1 The Worker Manager IS a `Harness` caller

Ruled yes, and the argument is shorter than the alternative's costs.

The port was promoted (plan.md 4.10) on the claim of two callers, and
`daemon/AGENTS.md` states that claim as the port's justification. If the
Worker Manager drives claude through a private driver instead, the claim
collapses to one caller and the port is a cap on nothing; worse, the second
claude integration (a future claude CONVERSATION adapter, 10.7) would then
exist beside a worker driver speaking the same wire protocol with different
code -- two copies of the stream-json mapper is two copies that drift. And
the measured shape fits: a worker is `start(spec)`, ONE `prompt`, zero or
more mid-run messages, one terminal event, `dispose` -- which is the port's
own lifecycle with two members added. The events the Worker Manager needs
for narration and for the record -- tool starts and ends with the harness's
own ids and real outcomes, per-response boundaries, one ending -- are the
six normalized events already frozen by conformance.

What it costs, stated: the port grows TOWARD long-lived-child semantics that
the conversation path never uses (section 5.2), the fake must script
multi-response steered runs, and the port becomes the thing both a talk
turn and a background job are described in, so a future harness that fits
only one of the two will stretch it. What would overturn it: if step 2.2's
live tests show stream-json cannot express mid-run steering or per-response
boundaries in a way `TurnEnd` carries -- then the Worker Manager gets its
own driver, the port stays as it stands, and this section's argument is the
one recorded as lost.

### 5.2 What the port gains: `steer` and `finish`, and nothing else

```python
async def steer(self, text: str) -> None:
    """One mid-prompt message to a running child (AC-52). Only meaningful
    when caps.steerable; calling it on a harness that answered False is a
    caller bug and raises. pi: the rpc `steer` command. claude: one
    user-message line on stdin [MEASURED 2026-08-18]."""

async def finish(self) -> None:
    """The caller promises no further input for this prompt. A harness
    whose prompts end by themselves treats this as a no-op (pi: agent_end
    arrives unaided); a harness that waits for more input ends its loop
    (claude: close stdin; the process exits and AgentEnd follows
    [MEASURED 2026-08-18: exit 0 about 2 s after close]). This is AC-11's
    completion definition -- stdin closed and the process exited -- spoken
    at the port, which is the only place both callers can share it."""
```

`finish` earns its place because without it the port cannot express AC-11's
completion rule at all: `result` is per response [MEASURED], so an adapter
has no way to know a prompt is OVER until the caller says no more input is
coming, and the alternative -- the manager disposing after its grace timer
and treating the last `TurnEnd` as the end -- silently abandons the
one-terminal-event contract (AC-48) that three defects paid for. The cost:
every adapter and the fake gain a method, and `brain_harness` gains one
line (`prompt` then `finish`, since a voice turn's input is complete by
definition). pi's no-op is semantically true rather than a stub -- its
prompts genuinely carry their own ending -- which is the test this rule
sets for any adapter: if `finish` would have to FAKE an ending, the harness
does not fit the port and this section's decision is being stretched.

`steer` finally gives `caps.steerable` its method -- section 9 of
harness-mode.md called a capability with no method a claim, and this is the
claim coming due. `caps.native_resume` gets its first reader in the same
phase: the claude adapter captures the session id from the `init` line
[MEASURED] and re-attaches by id, which is the mechanism AC-44 has been
waiting to see proven on a second real harness.

What the port does NOT gain, each refused with its reason:

- **A worker id.** A `Harness` is one child; identity, records and the
  registry are the Worker Manager's. A port that knows about jobs is a
  worker manager wearing a protocol's clothes.
- **Hook anything** (section 5.3).
- **The idle grace.** The 10 s window and its cancellation-by-steer are
  POLICY -- the manager watches `TurnEnd`, runs the timer, and calls
  `finish`. An adapter with a policy timer inside it would make the grace
  unobservable to the thing that owns completion.
- **A `list_models`-style status query.** `info` stays static (D-25).

### 5.3 Hooks are dropped from the design entirely

The plan (4.5, AC-11, AC-12, 8.7) builds Stop/Notification hooks, a POST
receiver, a Butler-authored settings file and a boot reconcile from
hook-written files. The whole apparatus existed for one property: events
that survive Butler dying, written by the worker itself, so a worker that
OUTLIVED a dead daemon could still be announced after the restart.

**No worker outlives the daemon any more.** AC-36 -- built, measured, and
paid for twice (D-24: seven orphans in one afternoon, twice) -- puts every
child Butler spawns in the kill-on-close Job Object, and its own criterion
text says "extended to every child Butler spawns". A hard-killed daemon
takes its workers with it, so there IS no worker still running after a
restart to hear about, and events written by the worker's own hooks stop at
the same TerminateProcess the daemon's drain stops at. The hook pathway
survives a failure that can no longer happen.

What replaces it is what AC-13 requires anyway: the daemon drains the
child's stdout EAGERLY (D-16) and appends every event to the worker's
`events.jsonl` as it arrives -- the drain is the record, one writer instead
of two. The spike settles the last thing hooks provided that stdout might
not have: the per-response boundary (`result` per response [MEASURED]) and
the final text (`result.result`, plus `last_assistant_message` if hooks are
ever revisited) both arrive on stdout.

The argument that lost, recorded: hook events on disk are written by the
WORKER's process and are therefore exactly as durable as the worker,
independent of any Butler code path -- a genuinely stronger property in a
world where workers survive daemons. That world was ruled out on 2026-08-18
by AC-36's extension, and this ruling follows it. If the owner ever exempts
workers from the Job Object (section 11 item 1), hooks are the mechanism to
resurrect, and section 1 finding 3 is the measured ground for doing it --
including the bash-runs-the-command and forward-slash traps that cost this
spike twenty minutes.

Consequence for `plan.md`: AC-11's hook clause, AC-12's stated mechanism,
4.5's hook-installation block and 8.7's settings-file question are all
affected; they are listed in section 11 rather than edited.

### 5.4 Worker records: `workers/<id>/` with an append-only `events.jsonl`

Kept as 4.5 specifies, deliberately UNLIKE `daemon/chats.py` -- and this is
confirming a divergence the plan already ruled deliberate (4.4c: "This NO
LONGER mirrors workers/<id>/ in 4.5, and the divergence is deliberate")
rather than resolving an accident. The reasons are the workloads:

- A conversation is TURNS, appended a few times a minute by a human; the
  flat file rewritten whole is the owner's ruled shape for it (AC-35).
- A worker record is a tool-by-tool LOG, hundreds of events per run,
  written from the eager drain loop that AC-13 forbids to stall: an append
  is O(1) and a whole-file rewrite per tool event is O(n^2) inside the one
  loop with a latency contract. The record is also what the boot reconcile
  reads to rule on a worker's fate (AC-53), where "the file ends
  mid-stream" is exactly the evidence -- an atomically-replaced whole file
  can never show where it stopped.

`workers/<id>/meta.json` holds `{harness, model, cwd, prompt, mode,
createdAt, name}` -- the mode AT DISPATCH recorded for the audit trail
(4.6 rule 3), even though since AC-29's retirement it derives no flags
(section 11 item 4). `events.jsonl` holds one JSON object per line: the
normalized port events plus `dispatched`, `steered`, `died`, each with a
timestamp. The spoken name in `meta.json` is minted at dispatch (project
plus two or three task words) because AC-11 announces the worker in WORDS
and 4.5 already deferred per-worker voices out of the phase.

`state/chats/` and `state/workers/` therefore coexist ON PURPOSE, and this
paragraph is the stated reason the repository carries two record shapes.

### 5.5 The claude adapter, and what a worker child may hold

`daemon/adapters/harness_claude.py`, behind the same port and conformance
suite as `harness_pi` and the fake (AC-44: adapter file, registry line,
conformance row). The mapping, each line measured in section 1:

- argv: `claude -p --input-format stream-json --output-format stream-json
  --verbose --replay-user-messages --strict-mcp-config
  [--model m] [--resume id]`, spawned with `childproc.assign` immediately
  (AC-36) and the same metacharacter refusal `harness_pi` enforces --
  `harness_model` and the spoken project name reach this argv too. claude
  resolves as `claude.exe`, so no `cmd /c` and no PATHEXT trap; the
  metacharacter rule stays anyway, because an argv builder that is safe
  only for the current spawn style is one refactor from not being.
- The prompt is written to stdin as the first JSON line, NEVER passed
  positionally -- finding 2, and the D-13 mutation for this adapter.
- `--verbose` is load-bearing (exit 1 without it), and exit 0 with zero
  `result` events maps to `Failed`, because that is the measured signature
  of input that never arrived.
- `system/init` -> capture `session_id`; `caps.native_resume = True`, and
  the id goes back to the caller's record for `--resume`. `assistant`
  text blocks -> whole-message text (no deltas by default, finding 6);
  `tool_use` blocks -> `ToolStart` with claude's own id; `tool_result` ->
  `ToolEnd` with the real `is_error` (the predecessor's unreachable error
  outcome, again). `result` -> `TurnEnd(text=result)`. Process exit after
  `finish()` -> `AgentEnd`; nonzero exit or a dead child mid-prompt ->
  `Failed` with the stderr tail, verdict at the child's CLOSE (4.4c).
  `system/hook_*`, `thinking_tokens`, `rate_limit_event` and the
  `--replay-user-messages` echo are consumed, not surfaced -- the echo is
  the prompt-acceptance gate, the rest is chatter.
- `--strict-mcp-config` with no `--mcp-config` is AC-54: the measured
  Gmail-in-a-worker inheritance, stripped at spawn and asserted on the
  live `init` line. The owner's plugins still load (finding 5's 4 KB
  SessionStart injection); accepted for this phase and named in section
  10, because the only measured lever that strips plugins (`--bare`)
  also restricts auth to `ANTHROPIC_API_KEY`, which is not how this
  machine authenticates.

### 5.6 `proceed()` dies, and dispatch means a worker

Today a router `dispatch` verdict becomes `AskConfirm`, and
`hub._on_ask_confirm` hands it straight to `brain.proceed()` -- the talk
child does the work inline, because until now there was nothing else to
hand it to. When the Worker Manager lands, that route changes meaning:
`dispatch` is the ceremony plus a BACKGROUND worker (AC-11), inline work is
`converse`, and `proceed()` / `CAN_PROCEED` lose their only caller and are
deleted in the same commit (step 2.5). "The Brain proposes, butlerd
disposes" becomes literally true again -- the daemon, not the brain,
carries a confirmed action out.

The cost, recorded because it is a behavior change the owner will hear: an
instruction the router calls `dispatch` today gets answered inline by the
talk agent in the same turn; after this phase the same sentence gets a
repeat-back and runs in the background. If lived use shows the router
calling `dispatch` on things the owner wanted inline, the fix is router
prompt tuning or the `router_role=assist` setting (4.4), not a second
inline path around the ceremony.

---

## 6. The announcer

The largest piece, because it is the one thing the daemon's architecture
currently forbids. `daemon/AGENTS.md` records the limit exactly: every
audible path is turn-scoped by design, the page drops frames for a turn it
does not own and clears its turn the moment one ends, `hub.notify` is gone,
and "anything that wants to be HEARD without being asked has to build it".

**The design refuses to build a second audible path. It makes the
announcement a turn instead.** The daemon already mints turn ids
(`hub.mint_turn`); an announcement is a turn the daemon mints WITHOUT a
capture: pick a device, mint an id, send one new frame, and drive the
existing speak machinery. Everything downstream -- `send_for_turn`'s stale
discard (D-6), `onBinary`'s speaking-window gate, the playback-done gate
(D-19, R1), `_await_playback`'s timeout, barge-in's raised bar (D-15), the
cue-bleed zeroing and the 1300 ms wake cooldown (D-14), every-path-re-arms
(D-1) -- applies unmodified, because it keys on "a turn", not on "a turn
the owner started". The alternative -- a parallel `announce_start` /
`announce_audio` / `announce_done` protocol keyed on its own id -- would
duplicate exactly the machinery whose lessons cost D-3, D-6, D-9, D-19 and
R1 to learn, and every one of those lessons would need re-learning on the
copy. That is the argument, and it is the whole of it.

**The protocol change** is ONE frame, daemon to page:

```
{"type": "announce", "turn": <id>, "text": "...", "spoken": true|false}
```

On `spoken: true` the page ADOPTS the turn (`currentTurn = m.turn`), logs
the text as an announcement (visually distinct from a reply), and the
`speak_start` that follows carries the audio through the existing path;
`playback_done` and `goListen()` end it. On `spoken: false` -- the AC-14a
age-out, and any satellite that is not the chosen speaking device -- the
page renders the text and adopts nothing, the same show-not-say shape
AC-40's `spoken` flag already taught it. `web/app.js` gains one `case`;
`web/AGENTS.md` gains the contract line.

**The client change is small on purpose** and one rule in it is
load-bearing: the page adopts an announce turn ONLY from the `announce`
frame, never from a bare `speak_start` with an unknown turn -- `stale()`
already drops those, and that drop is the D-6 guard doing its job. A page
that adopted any turn the daemon named would speak frames for turns another
device owns.

**Who owns a turn-less utterance: the daemon, which picks ONE device.** The
chosen device is the most recently ACTIVE satellite (the last wake winner)
if it is connected and idle, else any connected idle satellite, else nobody
and the item is held. One device because AC-14a's "spoken ONCE, not once
per device" is a rule about the daemon-global queue, and because two
devices in one room speaking the same sentence a network-jitter apart is
noise. The cost: the announcement can land on a device in the wrong room.
Accepted for a single-user desk-first system and revisited with Phase 3's
real multi-satellite life; the losing option (announce on all idle
devices) is recorded here for that day.

**The queue** (`daemon/announcer.py`, owned by the Hub):

- Items: `{priority, text, minted_at, worker_id}`. Priority 2 = worker
  completions, failures, deaths, and the ceremony's expiry notice;
  priority 3 = narration. Priority 1 (the direct reply) is deliberately
  NOT rerouted through the announcer: replies keep the `_reply` path, and
  the announcer coordinates AROUND turns rather than carrying them. 4.7's
  "single gate to the speaker" is realized as a single COORDINATION point;
  rerouting the most-trodden path in the daemon for zero observable change
  is risk with no payoff, and that losing reading of 4.7 is recorded here.
- Emission: only when every connected session is `IDLE` -- AC-14's three
  states collapse to this one predicate, evaluated over ALL sessions. The
  trigger is a ping from `_rearm()` plus one on a satellite reaching
  wake-listening after connect (the AC-14a wake-up). No timer loop: the
  announcer is event-driven off the single exit every terminal path
  already reaches, so "quiet" cannot mean two different things (D-18).
- Bound: `ANNOUNCE_QUEUE_MAX = 32` [TARGET] items; overflow drops the
  oldest NARRATION line first and logs it; completions are dropped only by
  the AC-14a age rule (older than 3600 s [DECIDED]: `spoken: false` to the
  page, nothing to the speaker). Narration is additionally rate-limited at
  the Worker Manager per 4.5, so the queue's bound is the second guard,
  not the first.
- Constants, not settings: the age and the bound follow the 7.5 precedent
  (the confirmation timeout) -- nothing in the criteria needs them
  configurable, and a setting without a demonstrated need is AC-7b bait.

**Interaction rules, each inherited rather than invented:** barge-in during
an announcement is `abandon()` doing what it does -- `brain.abort(turn)` on
a turn the brain never saw is a NO-OP by the port's own contract
(`ports.Brain.abort`: unknown turn id is a no-op, D-1), the audio stops,
the item is dropped (AC-50), and the interrupting utterance is a fresh
turn. The announcement's audio lands in the room like any reply, so the
raised wake bar during SPEAKING (D-15) and the client's cue handling are
already correct for it. A satellite that dies mid-announcement re-arms
server-side like any dropped turn; the item is NOT re-queued -- delivery
was attempted, the text is on the web UI, and at-most-once beats a retry
loop wired to a flaky socket.

---

## 7. The confirmation ceremony (AC-9), rebuilt from nothing

Deleted whole on 2026-08-18 (commit `653a44d`); nothing in the tree speaks
a repeat-back today. The rebuild is smaller than what was deleted, because
the deleted thing was a session GRANT and AC-9 needs only a per-action
confirmation. The two consent holes the grant's reviews found
(harness-mode.md section 9) are the negative spec: each has a named test
before the ceremony is called done.

**Trigger and mode policy.** A router `dispatch` verdict becomes
`AskConfirm(action)` as it does today. `hub._on_ask_confirm` applies the
4.6 table -- which makes the permission modes gate something again for the
first time since AC-29's retirement:

- `paranoid`: dispatch is refused aloud. No ceremony, no worker.
- `secretary` (default): every dispatch gets the repeat-back.
- `trusted`: a dispatch whose `action.writes` is False proceeds without
  ceremony; a writing one gets the repeat-back. **This is the reader
  `Action.writes` has been waiting for** -- `ports.py` promises the field
  goes if Phase 2 lands without reading it; it lands read.

**The parked question.** The hub speaks the repeat-back naming the action
and target ("You want me to X in project Y -- yes or no?") and parks ONE
pending confirmation carrying the FROZEN `Action` -- harness, cwd, prompt,
writes, resolved at QUESTION time. The cwd is validated against the
`projects` list when the question is built, and the frozen value is what a
yes dispatches: consent hole 2 (cwd re-resolved at yes-time) stays dead by
construction, with the Gherkin's freeze scenario as its witness.

**The answer.** The NEXT final on that session is parsed by
`modes.spoken_confirmation` -- rebuilt under its old name in `modes.py`,
because it is permission policy and that is where permission policy lives.
The grammar's one non-negotiable rule survives from the deleted version: a
refusal ANYWHERE in the utterance outranks an affirmative anywhere in it
("yes, cancel that" is someone changing their mind mid-breath, not
consent). Affirmative: the frozen action goes to the Worker Manager and
the dispatch is confirmed aloud. Refusal or an unparseable answer: the
cancellation is spoken and nothing was spawned -- spoken, because both
happen inside the turn that answered. Silence: the window (30 s, a
fail-closed constant as before) expires the park, nothing spawns, and the
expiry notice goes through the ANNOUNCER -- the notice 7.5 could only show
is now audible, because the named gap got built.

**The three rules that must hold, from the holes that were paid for:**

1. The park is guarded on still owning the turn, AND `abandon()` clears
   the pending confirmation FIRST and re-clears after the turn is nulled
   (consent hole 1: the repeat-back that finished inside the abort window
   parked BEHIND the barge-in and the interruption was ruled on as
   consent). D-17's flag-across-await shape; the barge-in Gherkin is the
   witness, and the mutation for it is deleting the re-clear.
2. One park at a time; a new `AskConfirm` cancels and replaces the old
   one aloud. A queue of pending consents is a queue of things the owner
   has forgotten asking about.
3. Nothing about a confirmation persists past its resolution. No grant, no
   scope record, no session flag: one yes authorizes one dispatch and is
   consumed by it. The deleted machinery that must NOT return, by name:
   `grant_scope`, `_grant_holds`, dispose-plus-respawn-on-grant, the
   mode-to-tools table, revoke-on-mode-change. They solved a problem --
   standing permission -- that no longer exists anywhere in Butler.

**The spoken mode change (AC-10's missing half)** lands beside the ceremony
as the FOURTH daemon-answered utterance in `_on_final`: a whole-utterance
mode phrase ("lock down", "switch to paranoid/secretary/trusted mode").
DOWN calls `modes.set(to, authorized=False)` and confirms aloud; UP is
refused aloud naming the web UI, and the store's own asymmetry (already
built and tested) is the enforcement -- the matcher adds no second
authority (D-18). Disjointness with the existing three matchers is by
construction, as `daemon/AGENTS.md` requires: the phrase set is a closed
literal list, and the near-collision to test is "switch to long form"
(turn mode) against "switch to trusted mode" (permission mode).

---

## 8. Build order, tests first

Six commits, each green before the next, red test first with its reason
named, each carrying its rail (`AGENTS.md`) and `plan.md` edits in the same
commit (D-12), each naming its D-13 mutation and the single test that must
catch it. Suite baseline going in: 465 passed [MEASURED 2026-08-18]. The
mutation harness reads pytest EXIT CODES, never output, and deletes
`__pycache__` between runs (test/AGENTS.md; both rules are paid for).

### 2.1 The port grows `steer` and `finish`; the fake learns to be steered

**Red test.** New rows in
`test/conformance/test_harness_conformance.py`, red at `AttributeError` on
the fake -- absent feature, the daemon boots:
`test_steer_reaches_a_running_prompt` (the fake records steers; a steer
mid-prompt appears in the record and the prompt still ends once),
`test_finish_ends_a_prompt_that_waits_for_input` (a fake scripted to hold
its ending until `finish`, mirroring claude's measured behavior, emits
exactly one `AgentEnd` after it), `test_steer_on_a_nonsteerable_harness
_raises` (caller bug, loud). `harness_fake` gains a `waits_for_finish`
script mode and a steer record; `harness_pi.finish` is the measured no-op
with the docstring saying why it is not a stub; `brain_harness` gains the
one-line `finish` after `prompt`.

**Files.** `daemon/ports.py` (two members, docstring),
`daemon/adapters/harness_fake.py`, `daemon/adapters/harness_pi.py`,
`daemon/adapters/brain_harness.py` (one line),
`test/conformance/test_harness_conformance.py`. DOX:
`daemon/AGENTS.md`'s port bullet and `daemon/adapters/AGENTS.md`'s fake
bullet, same commit.

**Keeps passing.** The whole existing conformance suite unchanged -- the
new members must not disturb the six events or the terminal contract --
and `test_brain_harness.py` end to end.

**Mutation (D-13).** Make the fake's `waits_for_finish` mode emit
`AgentEnd` immediately after `TurnEnd` (ignoring `finish`).
`test_finish_ends_a_prompt_that_waits_for_input` must fail, alone: if it
stays green the suite has pinned nothing about who ends a held-open
prompt.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\conformance\test_harness_conformance.py test\test_brain_harness.py -q`
then the full suite.

**Stop if** `finish` cannot be given honest semantics for pi without
faking an ending -- that is section 5.2's own test for port bloat, and it
goes back to the port decision rather than shipping a stub.

### 2.2 `harness_claude`, pinned live

**Red test.** `test/test_harness_claude.py`, red at import. The mapper
half runs on RECORDED lines (string constants captured from the
2026-08-18 spike, refreshed at build time):
`test_the_measured_stream_maps_to_the_normalized_events`,
`test_result_maps_to_turn_end_never_agent_end` (finding 4's
announce-per-round hazard),
`test_tool_results_keep_claudes_id_and_is_error`,
`test_hook_and_thinking_chatter_is_dropped`,
`test_exit_zero_with_no_result_is_failed` (finding 2's silent no-op).
The live half, marked `slow`, is the plan's own step-3 red test made
permanent: `test_a_real_child_takes_the_prompt_on_stdin_and_answers`
(schema + replay echo), `test_a_second_message_reaches_the_same_process`
(the steer mechanic), `test_finish_closes_stdin_and_the_child_exits`,
`test_the_init_line_lists_no_mcp_tools` (AC-54 -- asserted on the child,
not the argv), and the conformance row. `caps`: `native_resume=True,
steerable=True, images=True` [MEASURED / READ --help].

**Files.** `daemon/adapters/harness_claude.py`,
`daemon/adapters/__init__.py`, `test/test_harness_claude.py`,
`test/conformance/test_harness_conformance.py` (the row),
`test/conftest.py` (skip-without-claude, provider config, same shape as
the pi rows). DOX: `daemon/adapters/AGENTS.md` gains the claude bullet
(argv rules, the no-positional-prompt rule, `--verbose`, AC-54).

**Keeps passing.** Everything; no caller changes in this commit.

**Mutation (D-13).** Pass the prompt positionally instead of writing the
stdin line. `test_a_real_child_takes_the_prompt_on_stdin_and_answers`
must fail -- and per finding 2 it fails with a completed-but-empty run,
which is exactly why the assertion is on the replay echo and the answer,
never on the exit code.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\test_harness_claude.py test\conformance\ -q` with no `-m` filter --
this step is the live proof -- then the full suite.

**Stop if** the live child's stream shows shapes the recorded fixtures
cannot place (re-record from the live child, never patch the mapper
toward a guess), or the current CLI no longer matches the 2.1.224
mechanisms in section 1 -- that is the version-drift risk landing early,
and the fixtures get re-measured before any code bends.

### 2.3 The announcer, the `announce` frame, the page

**Red test.** `test/test_announcer.py`, red because the frame does not
exist -- protocol tests drive a real WebSocket (test/AGENTS.md) and the
`announce` frame never arrives:
`test_an_announcement_is_a_turn_on_one_idle_device` (AC-50: ordered wire
events, fresh turn id, one device speaks, the other sees `spoken: false`),
`test_a_busy_session_anywhere_defers_the_announcement` (AC-51 ordering:
queued behind the OTHER device's turn, emitted after its terminal),
`test_barge_in_kills_an_announcement_and_it_is_not_requeued`,
`test_narration_is_dropped_before_completions_at_the_bound`,
`test_a_held_announcement_is_spoken_once_on_next_connect` and
`test_a_stale_announcement_is_shown_not_spoken` (AC-14a),
`test_emission_hooks_rearm_not_a_second_predicate` -- the D-18 row:
mutate the `_rearm` ping away and this is the test that must fail.
Client: `web/app.js` gains the `announce` case and the
adopt-only-from-announce rule; `node --check` plus
`probe_page.py --live` for the render.

**Files.** `daemon/announcer.py`, `daemon/hub.py` (the mint-and-speak
path, the `_rearm` ping), `web/app.js`, `test/test_announcer.py`. DOX:
`daemon/AGENTS.md` REWRITES the nothing-is-spoken-outside-a-turn bullet
(the limit is now "unsolicited speech is an announcer turn"),
`web/AGENTS.md` gains the frame contract, `plan.md` 4.3's message table
gains the frame -- same commit (D-12).

**Keeps passing.** `test_protocol_v1.py`, `test_hub_bargein.py`,
`test_conversation.py` -- the announcer must not perturb owned turns --
and the page's five-turn re-arm proof.

**Mutation (D-13).** Emit held announcements once per connected device
instead of once globally.
`test_a_held_announcement_is_spoken_once_on_next_connect` must fail,
alone -- AC-14a's exact words.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\test_announcer.py test\test_protocol_v1.py test\test_hub_bargein.py
-q`, `node --check web\app.js`, then the full suite.

**Stop if** the announce-as-turn shape cannot coexist with the page's
turn adoption without weakening `stale()` -- weakening D-6's guard to
ship a feature is the one trade this design forbids, and the parallel-
protocol alternative comes back to the table with its costs.

### 2.4 The ceremony and the spoken mode change (AC-9, AC-10)

**Red test.** `test/test_dispatch_confirm.py`, hub-level over scripted
verdicts and the fake harness, red because `_on_ask_confirm` still routes
to `proceed()`:
`test_secretary_speaks_the_repeat_back_and_nothing_spawns`,
`test_yes_dispatches_the_frozen_action_once`,
`test_the_cwd_is_frozen_at_question_time` (consent hole 2),
`test_a_refusal_anywhere_outranks_an_affirmative_anywhere`,
`test_silence_expires_closed_and_the_notice_goes_to_the_announcer`,
`test_an_unparseable_answer_cancels_aloud`,
`test_barge_in_parks_nothing` (consent hole 1),
`test_paranoid_refuses_dispatch_aloud`,
`test_trusted_skips_the_ceremony_only_for_a_read_only_action`
(the `Action.writes` reader), plus in `test/test_turn_modes.py`'s
neighborhood: `test_lock_down_by_voice_is_instant` and
`test_escalation_by_voice_is_refused_aloud` (AC-10), and the
near-collision disjointness row ("switch to long form" vs "switch to
trusted mode"). Two-turn shapes throughout, because a parked state that
eats the next utterance is the class one-turn tests cannot see
(test/AGENTS.md).

**Files.** `daemon/hub.py` (the pending confirmation, `abandon()`'s
clear-first-and-re-clear, the mode-phrase matcher), `daemon/modes.py`
(`spoken_confirmation` rebuilt), `test/test_dispatch_confirm.py`,
`test/test_turn_modes.py`. The dispatch target in THIS commit is a seam
the manager fills in 2.5 -- the hub hands the frozen action to a
`dispatch(action)` callable injected at build time, and until 2.5 the
only implementation is the test's recorder; production wiring stays on
the spoken Phase 1 refusal, stated in the commit message, so no
placeholder ships as behavior. DOX: `daemon/AGENTS.md` gains the ceremony
as a Local Contract and the fourth daemon-answered utterance;
`web/AGENTS.md`'s labelled-stub bullet updates (the repeat-back stops
being a stub); `plan.md` 4.6's "gate nothing" sentences update -- same
commit.

**Keeps passing.** `test_protocol_v1.py`, `test_turn_modes.py`,
`test_hub_bargein.py`, `test_conversation.py` -- the regression net
around `_on_final`, again.

**Mutation (D-13).** Delete `abandon()`'s pending re-clear.
`test_barge_in_parks_nothing` must fail, alone. Chosen over mutating the
grammar because the barge-in interleaving is the hole a review found
AFTER the original step was called done -- it gets the instrument.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\test_dispatch_confirm.py test\test_turn_modes.py
test\test_hub_bargein.py test\test_conversation.py -q` then the full
suite.

**Stop if** yes/no parsing of real dictation proves too brittle and the
fix on the table becomes a model judging the affirmative -- the same
stop-line 7.5 drew: a model deciding a permission boundary is the owner's
call, never a plan detail.

### 2.5 The Worker Manager, the record, the reconcile

**Red test.** `test/test_workers.py`, red at import of `daemon/workers.py`:
`test_a_confirmed_dispatch_spawns_exactly_one_worker` (through the 2.4
seam, on the fake's spec record),
`test_the_drain_appends_every_event_before_narration` (D-16 / AC-13's
caller half: with the announcer artificially stalled, the fake's run
completes and `events.jsonl` holds every event -- the record must not
wait on the mouth),
`test_turn_end_starts_the_grace_and_finish_follows` (AC-11's completion,
against the `waits_for_finish` fake),
`test_a_steer_during_grace_cancels_the_timer`,
`test_a_dispatch_outside_the_projects_list_is_refused_aloud` (AC-31's
second gate, the caller 7.7 kept it for),
`test_boot_reconcile_announces_a_died_worker_once` (AC-53: a record with
no terminal event, daemon rebuilt, one announcement),
`test_completion_and_failure_each_announce_exactly_once`. Plus, marked
`slow`, `test_a_real_claude_worker_dispatch_to_completion` -- the AC-11
end-to-end on a temp repo. `proceed()` and `CAN_PROCEED` are deleted in
this commit (section 5.6), and `ports.RunningWorker` finally has a
producer.

**Files.** `daemon/workers.py`, `daemon/hub.py` (the 2.4 seam wired),
`daemon/butlerd.py` (build the manager, boot reconcile),
`daemon/adapters/brain_harness.py` (`proceed` removal),
`test/test_workers.py`. DOX: `daemon/AGENTS.md` (the manager's
ownership, the two-record-shapes paragraph from 5.4), `plan.md` 4.5's
hook block per the owner's section-11 ruling -- same commit.

**Keeps passing.** The brain conformance suite (a brain that no longer
proceeds must still refuse cleanly), `test_conversation.py`,
`test_child_lifetime.py` unchanged -- workers multiply children and the
Job Object is what makes that safe.

**Mutation (D-13).** Route the drain through the announcer queue (append
only after narration is accepted).
`test_the_drain_appends_every_event_before_narration` must fail -- the
D-16 defect reintroduced at the exact coupling AC-13 names.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\test_workers.py test\conformance\ test\test_conversation.py -q`
then the full suite.

**Stop if** the reconcile cannot tell "died mid-run" from "record still
being written by a live daemon" on this filesystem -- announcing a death
for a worker that is alive is worse than silence, and the record format
gets a liveness marker designed rather than guessed.

### 2.6 The steer route

**Red test.** `test/test_steer_route.py`, red because `ctx.workers` is
never populated: `test_the_schema_offers_steer_only_when_a_worker_runs`
(the measured accuracy rule, now with a real producer),
`test_a_single_worker_binds_an_unaddressed_steer`,
`test_two_workers_and_no_address_degrades_to_clarify`,
`test_a_steer_reaches_the_ports_steer` (caller half of AC-52),
`test_a_steer_to_a_finished_worker_is_spoken_not_dropped`,
`test_a_nonsteerable_workers_refusal_names_the_harness`. Live, `slow`:
one steered claude worker, the steer visible in the replay echo.

**Files.** `daemon/hub.py` / `daemon/butlerd.py` (workers into
`TurnContext`), `daemon/dialogue.py` (nothing -- the schema already
builds per turn; the test proves it), `daemon/workers.py` (`steer`),
`test/test_steer_route.py`. DOX: `daemon/AGENTS.md`'s dialogue bullet
(the `workers` tuple has a producer), `plan.md` section 5 Phase 2
checkboxes -- same commit.

**Keeps passing.** `test_router.py` (the schema without workers is
byte-identical), the announcer and worker suites.

**Mutation (D-13).** Populate `ctx.workers` unconditionally with the
last-known list even when nothing runs.
`test_the_schema_offers_steer_only_when_a_worker_runs` must fail -- the
measured point of routing accuracy is the thing being guarded.

**Verify.** `.venv\Scripts\python.exe -X utf8 -m pytest
test\test_steer_route.py test\test_router.py -q` then the full suite,
and the phase closes with three full-suite runs recorded in
`handoff.md`, counts and seconds, as phase 7 did.

**Order and the gate.** 2.1 -> 2.2 -> 2.3 -> 2.4 -> 2.5 -> 2.6, strictly:
the adapter needs the port, the ceremony's expiry needs the announcer, the
manager needs the ceremony's seam and announces through the announcer, the
steer route needs the manager. **2.4 is the gate**: no commit before it
makes a dispatch reach a worker, and 2.5 -- the commit that CAN spawn one
-- lands with the ceremony already in force, so at no commit in this
sequence does a voice-triggered worker exist without the repeat-back in
front of it. That ordering is this phase's version of 7.5's
brain-mode gate, and it may not be reshuffled for convenience.

pi as a worker rides the port: the manager is parametrized over
`HARNESSES` like every other caller, the fake and pi rows come free in
2.5's tests, and 4.5's "pi one-shot" downgrade is stale (section 11
item 5). codex stays unverified and out (section 9).

---

## 9. Deliberately NOT in this phase

- **Per-worker voice rotation.** Deferred by 4.5's own text; one voice,
  the announcement names the worker in words.
- **The two narration channels (R14).** Spoken-filler-vs-history so the
  conversation can answer "how is that job going" -- real, and wants
  designing against lived use of the single channel first. The worker
  RECORD already holds what the answer would need.
- **Hooks, the POST receiver, and the Butler settings file** (section
  5.3) -- unless the owner rules for exempting workers from the Job
  Object, which resurrects them.
- **`--include-partial-messages` streaming for claude.** The no-delta
  path is measured, guarded and sufficient for narration keyed on tool
  events; token streaming buys nothing a background worker's announcer
  can spend.
- **A claude CONVERSATION brain.** The adapter this phase builds makes it
  nearly free later (10.7 item 2 gets its missing-session test the same
  day), but AC-44 asks nothing of it and the phase is big enough.
- **Idempotency keys on dispatch (R16)** and any dispatch dedup. Named in
  section 10; the ceremony makes a duplicate dispatch a spoken, confirmed
  act rather than a silent retry, which bounds the risk this phase.
- **Backpressure (D-8, R7).** Still a contract in 4.3 the code ignores;
  this phase adds announcer traffic but does not fix the seam. Named in
  section 10 so the omission is a decision, not an oversight.
- **Cost tracking, voice escalation, speaker verification, satellites
  beyond the desk, worker-to-worker anything** -- the plan's own Phase 2
  exclusions, unchanged.

---

## 10. The risks, named

**A television two sentences from `bash`.** The honest chain, stated
without softening: the wake word false-accepts at 0.56-1.5 per hour
[REVIEW]; there is no speaker verification until Phase 4; and after
AC-29's retirement the CONVERSATION path to a full-capability agent has no
ceremony at all -- this phase's repeat-back guards WORKER dispatch and
nothing else. For the worker path, everything between a spoof and a write
in the owner's repository is: the router must call it `dispatch`, the
repeat-back must be answered with an affirmative the grammar accepts, and
the projects list bounds where it lands. Two spoken sentences. For the
talk path, it is one routed utterance. The ceremony narrows the worker
door; the talk door stands as the owner ruled it, and Phase 4 remains
load-bearing, not a refinement (plan.md section 7).

**The measured Gmail-in-a-worker inheritance, and an assumed cure.** The
inheritance is measured (finding 5); `--strict-mcp-config` stripping it is
[ASSUMED] until 2.2's live test pins it. If the flag does not strip
account-level servers, this phase STOPS at 2.2 until a mechanism is found,
because AC-54 is not optional: a voice-dispatched agent holding the
owner's mailbox is a different product. The owner's plugins still load
either way, injecting context and hook chatter into every worker; accepted
this phase, and it is the first thing to revisit if worker behavior reads
as haunted.

**claude self-updates under Butler.** Every mechanism in section 1 is
pinned against 2.1.224, and the CLI updates itself on a schedule Butler
does not control. The live tests are the tripwire; a version bump that
changes the stream shape reds them, and the answer is re-measuring, never
loosening the mapper. This risk is structural and permanent for any
harness Butler does not pin.

**Steering semantics are half-measured.** Between-response delivery is
proven; mid-response folding is not (section 1). If 2.2's live test shows
steers land only at response boundaries, AC-11's letter still holds but
the lived experience is "the steer took effect a minute later" -- worth a
sentence in the announcement ("told it; it will pick that up next step")
rather than a redesign.

**The 30 s stdout stall is still [READ].** The eager drain makes it moot
by construction and AC-13's stalled-TTS test bounds it, but nobody on this
machine has ever observed the stall itself. If the drain is ever weakened
in a refactor, nothing measured here says how long the grace really is.

**Backpressure (D-8, R7) remains ignored while this phase adds traffic.**
`send_audio` writes unconditionally; announcements add unsolicited audio
to sockets whose owners did not just speak. The predecessor OOMed on
exactly this seam. Not fixed here (section 9); whoever sees satellite
memory climb during long narration has found this paragraph.

**The announcement device choice is a heuristic.** Most-recently-active
lands the announcement where the owner last was, which is wrong exactly
when they moved rooms. Single-user, desk-first, accepted; Phase 3 revisits
with real satellite life.

**One duplicated dispatch is one duplicated write (R16).** The ceremony
makes dispatch deliberate, but nothing dedups two confirmed dispatches of
the same instruction ten seconds apart. LiveKit shipped duplicated real
orders on this shape. Accepted this phase, named here.

---

## 11. plan.md statements Phase 2 shows to be false or in tension -- FOR THE OWNER

This spec edits none of these. Each is listed with the evidence and this
document's proposed resolution; the ruling is the owner's.

1. **AC-12's letter is impossible under AC-36.** "Kill the daemon while a
   worker runs; restart it; the worker's completion still becomes a spoken
   announcement" -- but AC-36 (built, tested, D-24 twice paid) puts every
   child in the kill-on-close Job Object, so the worker dies WITH the
   daemon and there is no later completion to announce. The options: (a)
   AC-36 wins; AC-12 is restated as durable record plus announced FATE
   (this spec's AC-53) -- the work is lost, the loss is announced, nothing
   is orphaned; (b) workers are exempted from the Job Object -- completions
   survive daemon crashes at the price of a full-capability agent running
   unwatched in the owner's repository after every crash, D-24's exact
   shape. This spec is built on (a) and section 5.3's hook ruling follows
   from it; ruling (b) reverses both.
2. **AC-11's and 4.5's hook machinery, and 8.7's open question.** The hook
   apparatus exists for durability a Job-Object world does not need
   (section 5.3), and its event content is measured to arrive on stdout
   (`result` per response, finding 4). Proposed: the hook clause comes out
   of AC-11, 4.5's HOOK INSTALLATION block is marked superseded with the
   losing argument kept, and 8.7 is closed as measured (the flag is
   `--settings`, it works in an arbitrary cwd, and it is not needed).
   3.5's Gherkin lines "the hook appends... and POSTs the daemon" go the
   same way.
3. **AC-14's "emitted after that turn's speak_end" re-opens a fixed bug if
   read literally.** `speak_end` means "I finished sending"; the page
   re-arms on `playback_done` (R1, D-19, built). An announcement emitted
   at speak_end plays into the tail of the reply still leaving the
   speaker. Proposed: the criterion's ordering words become "after that
   turn fully ends (playback_done or its timeout)", which is what this
   spec's AC-50/AC-51 assert.
4. **4.6 rule 3 derives flags that no longer exist.** "The mode at
   DISPATCH time is... translated into harness permission flags once" --
   since AC-29's retirement there is no tool restriction to translate to
   (4.4c's own text says so). Proposed: the mode is still frozen into
   `meta.json` as the audit record; the translation clause is struck.
5. **4.5's "pi... as plain -p one-shot processes... (no steering)" is
   stale.** `harness_pi` exists, speaks RPC, and measured
   `caps.steerable=True` in phase 7; the one-shot downgrade predates the
   port. Proposed: pi workers ride the port like any adapter; codex keeps
   the unverified-and-out status.
6. **plan.md section 5 Phase 2 step 3's shape changes.** The step
   prescribes hooks and the settings-file pin; under ruling 1+2 its first
   red test becomes this spec's 2.2 live suite (already run once, section
   1). The three things it demanded pinned ARE pinned; two of them came
   out different from what 4.5 assumed (the prompt is stdin-only and
   silently so; the settings flag works but is no longer needed).

---

## 12. What this plan got wrong

Empty on purpose. This is the section whoever BUILDS Phase 2 writes, after
the fact, in the register of harness-mode.md section 9: what the plan could
not have known, what a later reader would otherwise rediscover the hard
way, and nothing that merely restates the plan. If this section is still
empty when the phase is accepted, either the plan was perfect or nobody
was honest; bet on the second.
