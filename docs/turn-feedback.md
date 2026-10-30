# Turn feedback: working or stuck, and timeouts sized for a local model

CLAUDE.md backlog "2026-09-16 FOUR ITEMS", item 4, plus
three defects on the same path. Criteria AC-387..AC-396. BUILT 2026-09-26; the
criteria stay here until the owner accepts them, and `plan.md` was touched only
for the protocol frame list (4.3), which is a factual correction (D-12).

## 0. The ask, and how it was decided

The owner's words (2026-09-16): "we need to also improve the feedback so user
knows when agent is still working, especially on longer tasks. local models
can be slow, so timeouts must be generous (check E:\PROJECTS\prototype for
clues on this). and better ux overall."

**NO INTERVIEW WAS HELD, on the owner's instruction.** The backlog entry says
"do not start this without an interview"; on 2026-09-26 the owner delegated the
design instead ("make sensible choices"). Every decision below is therefore a
session's, recorded so the owner can overturn any one of them cheaply.

The lesson the entry carries, and the rule this spec is built on: the session
that recorded it polled a restarting daemon at 12 s, saw nothing, and told the
owner Butler was down. It was loading. **Silence was read as death.** So:
a wait with no upper bound needs a heartbeat, the heartbeat carries evidence,
and only a heartbeat that STOPS is allowed to suggest something died.

## 1. What was there before [READ, HEAD 90ebca5 + the uncommitted tree]

- The page already had a clock: `paint()` writes "`<tool> - m:ss`" or
  "Working on it - m:ss" once a `worker_event` has arrived (AC-233..AC-236).
- The daemon sent a `worker_event` only at child-up, tool start and tool end.
  **Between those it sent nothing**, so a two-minute silent generation and a
  wedged daemon still looked identical on the wire.
- `harness_pi._delta` DROPS `thinking_delta` and `toolcall_delta`. A reasoning
  model thinking for four minutes, or a model streaming a large `write` call's
  arguments, emits NO port event, so `HARNESS_IDLE_S` (180 s) fires on a child
  that is visibly producing tokens. That is a timeout firing on evidence of
  progress, which this spec forbids.
- The page's own watchdog is re-armed by every frame of the turn on screen. In
  harness mode "Working on it." is spoken first and the page sits in SPEAK for
  the whole run, so `WATCHDOG_MS.speak` (120 s) was the budget for a silent
  agent: a local generation quiet for two minutes was given up FROM THE PAGE,
  which sends `stop` and kills the agent [READ from the code; not reproduced
  before the fix].

## 2. Decisions

- **D1. Generous, idle-based, evidence-re-armed.** No total budget is added
  anywhere. Model-facing IDLE budgets go to 600 s. The owner's reference sizes
  a model call at 6x an ordinary HTTP call (`judge-runner.ts` 90 000 ms vs
  `places.js` 15 000 ms [READ 2026-09-16]); the measured healthy maximum gap on
  a CLOUD model was 78.6 s (`brain_harness.py`), and a local non-streaming
  model's silence is the whole generation, which scales with answer length.
  600 s is ~7.6x the measured cloud maximum. A longer wait costs the owner
  nothing now, because the page shows how long it has been quiet and offers
  Stop (D4): the owner, not a constant, decides when a quiet agent is dead.
- **D2. Evidence is the harness's own events, never invented.** Reasoning and
  tool-argument streaming become one new port event, `Activity(kind, chars)`,
  which re-arms the idle deadline and is counted, never spoken or recorded.
- **D3. One heartbeat per turn, from `_reply`.** `{type:"progress"}` goes out
  at once and every `PROGRESS_EVERY_S` (5 s) until the turn ends, on every exit
  (D-1). It carries the phase, the detail, what has been produced so far, and
  how long since the last piece of evidence. The same builder answers
  `GET /api/chats/{id}/status`, so a reloaded or second page sees the same
  evidence (D-18).
- **D4. The page says it; it does not act on it.** Silence past `STALL_MS`
  (15 s, 3x the heartbeat) is SAID: "No word from Butler for 0:25 -- it may be
  stuck. Stop ends it." Nothing is stopped automatically by that line. The
  existing page watchdog keeps its values and its auto-stop, but with a 5 s
  heartbeat it can now fire only when the DAEMON is silent, which is the hang
  it was written for.
- **D5. A stop keeps what was shown** (defect 1). The partial answer is
  recorded with `stopped: true`, and the unspoken mark of
  `docs/filing-and-pacing.md` 2.5 still applies to it.
- **D6. The rewind seed follows AC-64** (defect 2): the text before
  `unspokenFrom`, which is only ever set when speech was on and a tail went
  unheard; everything otherwise.
- **D7. A rewind refuses while the agent runs** (defect 3, AC-248): 409, in
  words, and nothing is copied or truncated.

## 3. The timeout audit (AC-387)

Every timeout on the turn path, before -> after. "Idle" means re-armed by each
event; "total" means a wall-clock bound.

| Timeout | Where | Before | After | Why |
|---|---|---|---|---|
| `HARNESS_IDLE_S` | brain_harness | 180 s idle | **600 s idle** | measured on a cloud model; now also re-armed by `Activity` (D1, D2) |
| `READ_TIMEOUT_S` | llm.py (endpoint brain) | 180 s idle | **600 s idle** | a local server loads the model and prefills after connect, before the first byte |
| `CONNECT_TIMEOUT_S` | llm.py | 10 s | 10 s | TCP connect only; load happens after it |
| `STARTUP_TIMEOUT_S` | harness_pi | 90 s | 90 s | child start, measured 1.5-2.3 s |
| `COMMAND_TIMEOUT_S` | harness_pi | 15 s | 15 s | an RPC acknowledgement (prompt accepted, abort), not a model call [READ] |
| `DISPOSE_TIMEOUT_S` | harness_pi | 5 s | 5 s | teardown |
| `BRAIN_LETGO_S`, `ABANDON_TASK_S` | hub | 3 s | 3 s | bounds on a STOP, so the microphone is never held (D-1) |
| playback gate | hub | audio + 2 s | same | the room hearing the answer |
| `SYNTH_TIMEOUT_S` / cold / boot | tts_kokoro | 30 / 180 / 60 s | same | local CPU engine, already sized locally |
| `FINALIZE_*` | asr_transcribecpp | 15..60 s | same | local recognizer, measured |
| capture ceilings | pipeline, endpointing | 120 s / 300 s / 6 s | same | the owner's own speech, not a model |
| `WATCHDOG_MS.thinking` / `.speak` | web/app.js | 180 / 120 s since last frame | same values, **re-armed by the heartbeat** | now fires only on a silent DAEMON (D4) |
| `WATCHDOG_MS.capture`, `DRAIN_MAX_MS`, wake | web/app.js | 30 s / 120 s / 2.5 s | same | capture and audio, not a model |
| `STALL_MS` (new) | web/app.js | -- | 15 s | SAYS "may be stuck"; acts on nothing |

Not on the turn path and untouched: model listing (`models.py` 10 s,
`MODELS_TIMEOUT_S` 60 s), the folder chooser, the desk agent.

## 4. The frame

```
{type:"progress", turn, chat, elapsed_ms, phase, detail, chars,
 thought_chars, last_event_ms}
```

- `phase`: `starting` (nothing back yet), `thinking` (reasoning streaming, or
  a tool just returned), `tool` (a tool is running; `detail` is its name),
  `tool_input` (the model is writing a tool call's arguments), `writing`
  (answer text is arriving), `asking` (blocked on the owner), `speaking` (all
  text is in, the voice is finishing).
- `chars`: answer characters so far -- as they ARRIVE from a harness (a
  `WorkerEvent(kind="text")` per delta, before the sentence splitter), as they
  are SHOWN from the endpoint brain. `thought_chars`: reasoning plus
  tool-argument characters streamed. Neither is estimated.
- `last_event_ms`: since the last piece of evidence. The heartbeat itself is
  not evidence: a daemon that is alive and an agent that is quiet are two
  different facts, and the frame carries both.

## 5. Criteria

- **AC-387 (the timeouts are an audited set).** Section 3 lists every
  timeout on the turn path; no model-facing idle budget is under 600 s.
  *Fails as:* a slow local model cut off mid-work. Recoverable (re-ask).
- **AC-388 (reasoning is evidence).** `thinking_delta` and `toolcall_delta`
  reach the port as `Activity` and re-arm the idle deadline; nothing of them
  is spoken, shown or recorded. *Fails as:* a reasoning model killed while
  producing tokens.
- **AC-389 (a heartbeat while a turn runs).** A `progress` frame goes out at
  turn start and every 5 s, and stops on every exit -- answer, failure, stop,
  barge-in (D-1). *Fails as:* silence read as death, or a heartbeat that
  outlives its turn.
- **AC-390 (the heartbeat carries evidence).** `phase`, `detail`, `chars`,
  `thought_chars`, `last_event_ms` follow the events the turn actually saw.
- **AC-391 (one builder).** `GET /api/chats/{id}/status` carries the same
  evidence fields while a turn runs.
- **AC-392 (the page says it plainly).** The working line reads e.g.
  "Running tool: read - 0:42" or "Writing - 1:05, 820 chars", adds
  "quiet for m:ss" when the agent has produced nothing for 30 s, and a
  background conversation's tree row shows the same line from its frames.
- **AC-393 (stuck is said, never assumed).** No frame for 15 s on the turn on
  screen reads "No word from Butler for m:ss -- it may be stuck. Stop ends
  it."; while frames arrive the page never claims failure.
- **AC-394 (a stop keeps the partial answer).** Stopping a harness turn
  records what was shown, marked `stopped: true` (an empty `stopped` marker if
  nothing was shown), and a reload draws it with a "stopped" note.
  *Fails as:* a reload showing only the question. Not recoverable.
- **AC-395 (the seed is what was heard).** The rewind seed truncates an
  answer at `unspokenFrom`. *Fails as:* a fresh agent told it said what the
  owner never heard (D-20).
- **AC-396 (AC-248 met).** `POST /api/session/rewind` answers 409 naming the
  reason while the live conversation's agent is running or waiting, and the
  record and its session file are untouched.

## 6. Gherkin

```gherkin
Scenario: AC-388 a thinking model is not cut off
  Given the harness idle budget is B
  And a child streams reasoning for 3 x B with no answer text
  Then the turn is not timed out and ends with the answer

Scenario: AC-389 the heartbeat starts and stops
  When a turn starts
  Then a progress frame is sent at once and again every 5 s
  When the turn is stopped
  Then no progress frame for it is sent afterwards

Scenario: AC-390 evidence follows the events
  Given a child that runs the tool "read" and then answers
  Then a progress frame shows phase "tool" with detail "read"
  And a later one shows phase "writing" with chars > 0

Scenario: AC-393 a silent daemon is said to be maybe stuck
  Given a turn on screen
  When no frame arrives for 15 s
  Then the working line says "No word from Butler for 0:15 -- it may be stuck"
  And nothing is stopped by the page for saying so

Scenario: AC-394 stop keeps the partial answer
  Given an agent has shown two sentences
  When the owner presses Stop
  Then the record holds those sentences with stopped true
  And a reload shows them and a "stopped" note

Scenario: AC-395 the seed holds only what was heard
  Given an answer whose unspokenFrom is 20
  When the conversation is rewound and asked again
  Then the seed carries the first 20 characters of that answer only

Scenario: AC-396 no rewind under a running agent
  Given the live conversation's agent is running
  When the owner rewinds
  Then the answer is 409 saying the agent is still running
  And the record is unchanged
```

## 7. Phases (all built 2026-09-26)

1. Port `Activity`; `harness_pi` maps the two deltas; `brain_harness` passes
   them up as `WorkerEvent(kind, chars)`; budgets raised (AC-387, AC-388).
2. `Hub._reply` heartbeat and `_TurnProgress`; status route (AC-389..AC-391).
3. Page: working line, quiet suffix, stall line, tree row (AC-392, AC-393).
4. Defects: stopped partial answer, heard-only seed, rewind refusal
   (AC-394..AC-396).

## 8. What the build found, and the browser evidence

Driven with `playwright-cli` against a scratch daemon (own `BUTLER_STATE`,
fake ASR/TTS, 127.0.0.1:8810, hotkeys off, a real pi child with read-only
tools), five turns; screenshots in `TMP/feedback-out/`.

- The working line followed real evidence: "Working on it - 0:05",
  "Thinking - 0:18, 150 chars", "Running tool: grep - 0:23", "Writing a tool
  call - 0:39, 888 chars", "Writing - 4:25, 4533 chars" [MEASURED].
- **The first version called a working turn quiet.** A bullet-list answer was
  held by `speech.drain_sentences` for 1:47 while text arrived the whole time,
  and the line read "Writing - 2:57, 848 chars - quiet for 1:47"
  (`03-before-fix-false-quiet.png`). Raw answer deltas are now evidence
  (`WorkerEvent(kind="text")`), with a test that fails without it. The HOLD
  itself -- the page not showing a list until it ends -- is
  `docs/filing-and-pacing.md` item 2's ground and was not changed here.
- Suspending the pi child by PID: beats continued and the line gained
  "quiet for 0:33 ... 1:28" (`05-agent-quiet.png`). Suspending the DAEMON:
  "No word from Butler for 0:17 -- it may be stuck. Stop ends it." at 15 s
  (`06-daemon-silent-stuck.png`); on resume the line returned to evidence and
  nothing had been stopped [MEASURED].
- Stop mid-write: the record kept 2310 characters with `stopped: true`, and a
  reload drew them with "Stopped." (`09`, `10`). A rewind posted while an
  agent ran answered 409 with the reason and the record kept 7 messages.
  A background conversation's row read "running - 1:10 - writing a tool call"
  from its own beats (`11-background-row.png`).
- **A stop that lands after the agent FINISHED** (the voice still playing
  out) stops only the speech: the record keeps the whole answer, unmarked,
  while the live page says "Stopped." Seen once in the browser (8692
  characters recorded whole). Stated rather than fixed: the answer on record
  is complete, which is the fact that matters on reload.

## 9. What this could have wrong

- **600 s is chosen, not measured on a local model.** Nobody has timed the
  owner's slowest local model's longest silent generation. The quiet suffix
  makes a too-long budget visible rather than harmful; a too-short one is the
  failure this spec exists to stop, so it errs long.
- **The endpoint brain's reasoning stream is not evidence yet.** `llm.py`
  re-arms on any byte, so it is never cut off, but the page shows "Working on
  it" rather than "Thinking" for an endpoint model that reasons; only harness
  mode maps reasoning into `Activity`.
- **A background row on a turn another device drives is a snapshot**, as it
  was: only the socket that drives a turn gets its frames. The status route
  carries the evidence for the conversation on screen.
