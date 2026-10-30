# Butler -- project plan

An always-on, hands-free voice assistant daemon for Windows. It owns the
user's microphone and speakers, holds a conversation through three model
tiers, and dispatches real coding-agent harnesses (claude, pi, codex) as
background workers that act on the user's behalf and report back by speech.

Forked from the proven spike at `E:\PROJECTS\prototype\spikes\unified-stt`
(1,464 lines: server.py, stt_engine.py, turn_engine.py, static/app.js,
static/wake.js, plus catalog.json and the mic worklet). Butler is deliberately
INDEPENDENT of an earlier prototype: no imports from `E:\PROJECTS\prototype`, no shared
services, no runtime dependency. Model files and vendored runtimes are copied
into this repo, never referenced across.

Provenance of every number in this document:

- **[MEASURED-SPIKE]** -- measured on this machine (i7-12700K, GTX 1080 Ti),
  recorded in the spike README or spike source.
- **[MEASURED-PROTOTYPE]** -- measured on this machine inside an earlier prototype, recorded
  in `E:\PROJECTS\prototype\CLAUDE.md`.
- **[TARGET]** -- a goal we set, not a result. Must be verified by a test.
- **[READ]** -- from published sources, not verified here.
- **[REVIEW <file>]** -- from the prior-art review in `.references\`
  (section 9): someone else's published measurement, cited but not rerun.
  Distinct from [READ] because the named file carries the source and the
  caveats; distinct from [MEASURED-*] because we did not run it.
- **[DECIDED]** -- a design decision already made by the owner; not re-opened.

---

## 1. User story

As the owner of this machine and its satellite devices, I want a voice
assistant that is always listening for its wake word on every device I carry,
answers me in under a breath for small things, thinks with me for big things,
and quietly sends real coding agents off to do work I ask for -- confirming
anything destructive out loud before it happens, telling me when work
finishes in a voice I can identify by ear, and putting anything longer than
three sentences on a screen instead of reading it at me -- so that I can run
my projects and my day hands-free, from my desk, my couch, or my phone,
without a browser tab being the fragile thing that owns my ears.

---

## 2. Acceptance criteria

Every criterion is observable: a person or a test can watch it happen. Each
becomes a FAILING test before its implementation (red-green). Criteria are
grouped by the phase that delivers them (section 5).

### Phase 1 -- the closed loop on one device

- **AC-1 (loop closes).** With the daemon running and one browser satellite
  connected, saying the wake word, then a question, produces an audible
  spoken reply through Kokoro, and the satellite page shows the live partial
  transcript while I speak. Verifiable headlessly: a scripted WS client that
  sends a wake candidate, streams a known WAV as PCM, and receives
  `partial`, `final`, `speak_start`, binary TTS audio, `speak_end` in that
  order.
- **AC-1a (no clipped onset).** The satellite buffers microphone audio from
  the moment its local wake detector fires and flushes that buffer to the
  daemon as the first bytes after `capture_start`, so the arbitration
  window and its round trip cost latency but never audio. Test: a recorded
  WAV whose first command word begins 150 ms after the wake phrase ends
  transcribes with that first word present. Without the flush this test
  fails by construction -- nothing streams while resting (4.2) and PCM is
  illegal before `capture_start` (4.3).
- **AC-2 (always re-arms).** After ANY terminal path -- normal reply, empty
  utterance, chat-tier timeout, TTS synth failure, malformed Reflex output --
  the session returns to wake-listening and a subsequent wake + utterance
  works with no page refresh and no daemon restart. Test: drive each failure
  path over WS, then prove the next turn still completes.
- **AC-3 (tier routing observed).** A trivial utterance ("what time is it")
  is answered by the Reflex tier with no Conversation-tier call made; a
  substantive utterance is answered by the Conversation tier. Verifiable
  because the daemon logs and the `final`/`reply` messages carry
  `tier: "reflex" | "conversation"`, and the test asserts the conversation
  endpoint (a local mock in tests) received zero / one request respectively.
- **AC-3a (clarify is exercised).** An utterance the Reflex tier cannot act
  on without more information ("run the tests", no project named) produces
  route `clarify` and exactly ONE spoken question, after which capture
  reopens WITHOUT requiring the wake word again; answering completes the
  original intent in the same turn. Two consecutive clarifies are not
  allowed -- the second falls through to the Conversation tier. If the user
  says nothing, the no-speech timeout re-arms to wake-listening (D-1 holds:
  clarify is a state the microphone stays open in, never a latch).
- **AC-4 (reflex latency).** Reflex-tier round trip, wake-word finalize to
  first TTS audio byte reaching the satellite, under 1500 ms on this machine
  with STT and Kokoro warm. [TARGET. Every COMPONENT is now measured:
  198 ms first STT partial and ~150 ms finalize [MEASURED-SPIKE], 31 ms
  smart-turn inference in the wired daemon, 140 ms Reflex verdict, 448 ms
  Kokoro first audio for a short sentence [all MEASURED 2026-08-16]. The
  criterion is still unproven, because nothing has measured them TOGETHER
  end to end -- the step 10 bench has never been run, and it cannot be run
  honestly until steps 6 and 7 give it a real Reflex call to time.]
- **AC-5 (interruption).** Saying the wake word while a reply is being
  spoken stops the audio within one sentence boundary and opens a new
  capture. The abandoned reply is never resumed and a late-arriving reply
  for the abandoned turn is never spoken (turn-id discard).
- **AC-5a (the reply never wakes Butler).** A TTS reply whose text CONTAINS
  the wake phrase, played through the satellite's normal output path with
  the microphone live, produces zero `wake_candidate` messages from that
  device. AC-5 proves barge-in fires when the user speaks; this proves it
  does not fire on our own audio. Test asserts the count, not the absence
  of a state change, because a self-trigger that loses arbitration would
  otherwise pass silently.
- **AC-5b (Butler's own cues never reach the recognizer).** With the audible
  cues on, a turn in which all three tones play produces a transcript with
  no artefact from any of them, and none of them starts or stops the energy
  gate. The satellite ZEROES the microphone frames covered by each cue's
  suppression window, in place, before the pre-roll ring, the wake detector
  or the socket see them -- so the declared `preroll` byte count still
  matches what is sent and the detector's 16 x 80 ms context stays
  continuous. Zeroing rather than dropping is the load-bearing choice: a
  dropped frame desynchronises the daemon's byte-derived silence clock,
  which counts 32 bytes per millisecond and has no other clock. Status:
  NOT covered by an automated test -- the suppression lives in `web/app.js`
  and this repo has no JS test runner. Today it is checked by ear and by
  `test/manual/probe_page.py`; naming it here is what stops that being
  mistaken for coverage (D-13).
- **AC-6 (empty turn ends cleanly).** A wake followed by silence emits a
  `final` with empty text within the no-speech timeout, releases the turn,
  and returns to wake-listening. (The final is the turn's END, not its text
  -- inherited defect rule D-7, section 6.)
- **AC-6a (the turn ends the way the setting says).** The three policies in
  `daemon/endpointing.py` are distinguishable from the wire alone, with the
  same audio driving all three. In `smart`, the turn ends when the model
  crosses its threshold, and with the endpointer absent the daemon SAYS it
  is falling back rather than running patient silence under the smart name.
  In `patient`, it ends on accumulated silence at the selected preset
  (quick 400 ms, balanced 600 ms, patient 800 ms) and never earlier. In
  `endword`, no amount of silence ends anything: the turn is still open
  after a pause longer than every other mode's cap, and only the end phrase
  plus a 600 ms pause finalizes it. Saying a mode phrase ("switch to long
  form") as the WHOLE utterance changes the mode and is confirmed aloud;
  saying it inside a sentence does not, because a preference that fires on
  a mention of itself is unusable. The failure this catches is the one that
  reads as working: a mode that is selected, reported everywhere, and
  behaves identically to the one before it.
- **AC-6b (the end phrase is control, not content).** In `endword` mode the
  phrase that ended the turn is absent from the `final` text, from the
  transcript the page displays, and from what the Brain is given.
  "...and run the tests. send it." reaches the brain as "...and run the
  tests". Without the strip the model reads an instruction with a stray
  imperative on the end, which at best wastes tokens and at worst changes
  what was asked for -- and the symptom is a wrong answer, never an error.
- **AC-6c (one warning before the turn ends).** While a turn is closing,
  the daemon emits exactly ONE `closing` message per pause, and continuing
  to speak cancels it: the next pause is entitled to a fresh one. Test
  asserts the COUNT across a pause long enough to poll several times, not
  merely that one was sent. Emitting it every poll is the defect -- the
  satellite plays a tone on it, so a repeated warning is a stutter in the
  room and, because every cue punches a hole in the captured audio
  (AC-5b), it chews through the sentence it was warning about.
- **AC-7 (survives the night).** The daemon runs 24 hours with a satellite
  connected and idle, then still completes a turn. RSS is logged hourly for
  EVERY process in the tree, not just the daemon: butlerd, the Kokoro node
  sidecar, and the Smart Turn child. The test asserts the last reading is
  within 1.5x the first FOR EACH. The daemon is the least likely leaker of
  the three -- transformers.js in the node sidecar and the numpy/onnx child
  are the ones with a history -- so bounding only the parent would measure
  the wrong process. [TARGET]
- **AC-7a (the ports are real, not decorative).** Each of the five ports in
  4.10 has AT LEAST TWO implementations passing the same conformance suite
  in `test\conformance\`: the production adapter and a test fake. Swapping
  the adapter named in `config\models.json` changes no code outside
  `daemon\adapters\`, proven by running the full protocol suite against the
  fake `Asr` and the fake `Tts` and getting the same passes. This is the
  criterion that stops "modular" from meaning "we wrote an interface once
  and never implemented it twice" -- an unimplemented interface is a guess
  about the future, and guesses are what section 4.10 caps at five.

  Status, stated plainly because this criterion is easy to believe already
  met: FOUR of the five ports satisfy it -- `Asr`, `Tts`, `Brain` (closed
  2026-08-17 by `brain_endpoint`) and `Harness` (closed 2026-08-18 by
  `harness_pi`), each a real adapter plus a fake through `test\conformance\`.
  `Source` does NOT. There is no `source_ws.py`: the WebSocket source is
  inlined in `hub.py`, so that port has exactly one implementation and no
  conformance file. The unmet half is the half whose real adapter has never
  been written.
- **AC-7b (every knob is reachable, and honest about itself).** Nothing that
  changes Butler's behaviour is editable only by hand. `GET /api/settings`
  returns the SCHEMA and the values together; the page's controls are
  generated from that response and name no setting of their own, so adding
  one to `daemon/settings.py` makes it appear with no page change and
  removing one makes it vanish. A rejected write returns 400 naming what was
  allowed and changes NOTHING -- a form with one bad field does not
  half-apply, which is asserted by mutating the call site to validate and
  set in one pass and watching that test fail (D-18). Every change is
  broadcast to all satellites, and a grep gate over the shipped client
  files fails the build on any browser-storage API, so two devices cannot
  diverge (D-2). Settings that are read once at construction rather than per
  turn carry a `restart` flag, the flag is asserted in BOTH directions
  against which module actually reads the value, and the UI shows it: a
  control that saves, reports success and changes nothing until a restart
  nobody mentioned teaches the user that the whole panel is decoration.

The six below are Phase 1 too, despite the numbers: they were added on
2026-08-17 when the brain was respecified (4.4), and renumbering the criteria
every phase cites would break more than it tidied.

- **AC-27 (both brain modes are real, and swappable).** With `brain_mode` set
  to `endpoint`, an utterance the router sends onward is answered by the
  configured OpenAI-compatible endpoint and nothing spawns a process. With it
  set to `harness`, the same utterance is answered by a fresh `pi` session
  per message, and one fixed progress line is spoken before that session is
  even started (AC-46). Both satisfy the same `Brain` conformance suite.
  Swapping is a settings write and changes no code, which is what stops "two
  modes" from meaning "one mode and a branch".
- **AC-28 (the router's authority is the owner's choice).** In `router_role:
  route` the router picks what handles the turn and the log names its verdict.
  In `assist` it NEVER picks: the mode is whatever the owner set, and the
  router's contribution is observable instead as a trivia answer, a single
  clarifying question, or a cleaned-up instruction that differs from the raw
  transcript. Test asserts the cleaned instruction reaching the harness, not
  merely that the setting was read -- a role that is selected, reported, and
  behaves identically to the other one is the AC-6a failure again.
- **AC-29 (RETIRED 2026-08-18).** This criterion required a harness child
  to hold only the tools its permission mode allowed, with a spoken
  once-per-conversation grant gating writes and `bash` reserved for
  `trusted`. The owner retired it: the harness always runs at full
  capability, `bash` included, in every mode -- "i just want full agent
  capability". The argument that lost, recorded as this plan records
  them: a wake-word false accept (0.56-1.5 per hour on `hey_jarvis`,
  section 7 and Appendix A, with no speaker verification until Phase 4)
  now reaches a
  tool-enabled agent with `bash` in the configured project, and the two
  consent holes the grant's review found (section 6 of
  docs/harness-mode.md section 9) show how hard that boundary was to
  hold. What remains between a false accept and a write: the router must
  route the utterance to the harness at all, the project list bounds
  where the agent runs, and the owner's own room. The permission modes
  (AC-8, AC-10) remain for Phase 2's worker dispatch, where AC-9's
  repeat-back still applies; until then they gate nothing.

  Amended 2026-08-18: full capability is the DEFAULT posture, now SELECTABLE
  down to read-only by the persistent `harness_tools` SETTING (read_only /
  full, default read_only so a wake-word false accept cannot make a built-in
  write until the owner flips it). This re-adds only the `--tools` plumbing
  653a44d removed -- read_only maps to the read allow-list `read,grep,find,ls`
  and full to no flag -- NOT the grant: no spoken ceremony, no permission-mode
  coupling, no repeat-back. It is a plain knob. Measured caveat: `--tools`
  gates pi's BUILT-IN tools only, so a read_only pi child on this machine could
  still write via the `background-process` EXTENSION's shell [MEASURED
  2026-08-18]. pi has no `--strict-mcp-config`, and `--no-extensions` closes
  the hole but disables the provider bridge, so there is no clean lever to gate
  extensions today; read_only bounds pi's built-in tools, not its extensions.
- **AC-30 (models are discovered per connection, chosen as a pair, and survive a
  restart).** MET 2026-08-17. `GET /api/models?connection=` returns what THAT connection
  actually serves -- 130 entries from llama-swap today. The settings page
  renders the list and names no model of its own, a favourite survives a daemon
  restart, and selecting a model the connection does not serve is refused with a
  400 naming what was available. Every choice is a `{conn, model}` pair
  (`router_model`, `endpoint_model`, one per harness), and the pair survives the
  restart intact: a saved choice that kept the model but forgot the connection
  would resolve against whichever connection happened to be first, which is the
  wrong model answering under the right name.
- **AC-31 (the working directory is spoken, bounded, and never guessed).**
  MET 2026-08-18 for the EXPLICIT form. "Work in PROJECT x" matches the final
  path component of a CONFIGURED PROJECT, normalized -- lower case,
  `-` and `_` read as spaces -- with no fuzz, no prefix and no nearest
  neighbour, because the cost of a guess is a tool-enabled agent writing in
  the wrong checkout. An unrecognised name is REFUSED ALOUD, names what was
  heard so a misrecognition is diagnosable from the room, and changes
  NOTHING; an empty list refuses every switch.
  **AMENDED 2026-09-11 (the owner's backlog item 4): the configured list is
  the shortcuts pane's ROWS (`ShortcutStore.paths()`), and the `projects`
  SETTING is retired.** It was a second list beside those rows and the two had
  diverged completely on the owner's machine -- the setting named one
  directory that was not a row, and all three rows were unreachable by voice.
  Both gates read the rows now. The cost is a WIDER surface, accepted by the
  owner in the same ruling: every row added for a terminal is reachable by
  voice. One tightening came with it -- two rows can share a final path
  component, and an ambiguous name is refused aloud with its own sentence
  rather than resolved to either, because picking one is the guess this
  criterion exists to prevent. A blank `harness_cwd` DEFAULTS to a scratch dir (`TMP` inside
  Butler's own folder), created on demand, rather than raising -- the owner
  ruled on 2026-08-18 that a child should always have somewhere harmless to
  run. The default is the scratch dir and NEVER a named guess: an unknown
  spoken project is still refused aloud, which is the whole point of the two
  gates. A match moves the SESSION, and the next turn's child is
  spawned there -- nothing is respawned, because AC-46 leaves no child
  running between turns. The move is
  session-scoped and never rewrites `harness_cwd` -- a spoken command
  editing the owner's default would bring the next restart up somewhere they
  never chose. (The clause revoking a write grant on a move was deleted with
  AC-29 on 2026-08-18: there is no grant to revoke. The old version claimed a
  move must re-ask consent; the owner ruled there is no consent ceremony.)
  TWO gates, not one:
  `hub._work_in_project` matches the spoken name and
  `butlerd.harness_spec_resolver` checks the answer again before it builds a
  spec, which is the only thing between an adapter holding a directory
  string and a child running in it. `test/test_project_switch.py` carries
  the near-miss; `test/manual/mutate_project_switch.py` is the D-13
  instrument.

  The limit, stated because the criterion reads wider than the build. The
  BARE forms -- "work in x", "work on x", "switch to x" -- are intercepted
  only on an EXACT match against a configured project and otherwise fall
  through to the agent untouched. Intercepting every such phrase swallowed
  ordinary work: "work on the endpointing module" and "work in butler and fix
  the failing tests" were answered with "I do not have a project called ..."
  and never reached the agent, which made the feature a liability. A REFUSAL
  is for navigation, never for work, so the spoken refusal belongs to the
  explicit grammar alone.
- **AC-32 (a long answer is not read at you).** A reply past about three
  sentences is spoken as one line that points at the screen, and the full text
  is on the web UI. Asserted on the wire: the spoken segments are a strict
  subset of the reply, and the `reply` message carries the whole of it.

- **AC-33 (keep-warm is the owner's switch, and says what it is doing).**
  With `keep_warm` off, butlerd sends no unprompted traffic to the endpoint at
  all -- asserted by counting requests across an interval, because a keep-warm
  that ignores its own toggle is indistinguishable from one that works until
  the owner needs the machine back. With it on and a model named, the endpoint
  sees one ping per interval. With it on and no model named, the ping is
  SKIPPED and a line says why; with it on and the router and conversation models
  differing ON THE SAME CONNECTION, boot warns that the ping will evict the
  model being talked to. The same two models on different connections raise
  nothing, which is the assertion that stops the warning becoming noise (4.4a).
- **AC-34 (the picker is per connection, grouped, live, and leaks no keys).**
  Every model offered comes from a connection's own `/v1/models`, fetched when
  the picker opens; pi's declared `models[]` array is never the source, asserted
  by putting an id in the fixture registry that the fake endpoint does not serve
  and requiring it NOT to appear. Connections are grouped, never flattened, and
  one that does not answer says so rather than contributing nothing -- so a dead
  bridge port is visibly dead instead of looking like a provider with no models.
  The load-bearing negative: no response from `/api/models` or
  `/api/connections` ever contains an `apiKey`, asserted against a fixture
  registry that contains one, because the real file holds the owner's keys in
  plaintext and this page is served over a tailnet.
- **AC-41 (no engine vocabulary outside its adapter).** MET 2026-08-17, both
  halves. The VOICE half: `daemon/hub.py` and `daemon/settings.py` name no voice; the
  list is served from `Tts.info["voices"]` through `GET /api/models?target=
  tts_voices`, and the conformance suite takes its voice from `info["voice"]`
  instead of a shared Kokoro constant that no second engine could ever have
  passed. Enforced by a grep gate over the core files (`test/test_seams.py`),
  which reads the list from the adapter rather than restating it. The ASR half
  -- `AsrConfig.backend` and `.latency`, which were transcribe.cpp vocabulary
  in the shared contract file -- closed on the same day. `AsrConfig` is now
  `model` and `language`: the two facts every recognizer understands. A
  compute backend and a chunking preset are constructor kwargs on the adapter,
  named in `config/models.json` under `asr_engine` so the seam is visible
  where someone edits it, and an option the adapter does not accept is a loud
  TypeError at boot rather than a setting that silently does nothing. Both
  halves are enforced by grep gates in `test/test_seams.py`, and the ASR half
  additionally by `test/test_asr_transcribecpp.py`, which builds with
  `backend="cpu"` and asserts the ENGINE reports CPU -- conformance cannot
  catch a dropped constructor kwarg, because it only ever builds with
  defaults and the engine's own defaults are the same values. Mutation-checked:
  hardcoding `backend="auto"` in the adapter fails that test alone and leaves
  every conformance case green.
  Still deliberately deferred: the settings schema's engines group still names
  parakeet models and chunking presets (`asr_model`, `asr_backend`,
  `asr_latency`). Those are construction inputs (the first two restart-flagged,
  the third swapped live, AC-713, AC-715) whose drift is
  visible in one file, and fixing them properly needs adapter-contributed
  schema fragments -- a bigger mechanism than the problem until a second real
  Asr adapter lands (docs/architecture-seams.md section 7).
- **AC-45 (settings are organised by seam, in pipeline order).** MET
  2026-08-17. One group per stage of the audio path, ordered as sound travels
  it, so every swappable engine has exactly one configuration home. Asserted
  three ways because each catches a different way to get it wrong: the group
  ORDER matches a sequence declared in the test rather than only in the schema,
  every declared group has at least one setting (an emptied group renders as a
  bare heading), and no group name encodes a tier -- `advanced` is a per-setting
  flag whose controls render inside their own seam's disclosure. Verified in
  real Chrome, since neither order nor the split is visible in markup: six
  sections in pipeline order, everyday and advanced counts per section (D-22).
- **AC-55 (the settings drawer opens on a chain the daemon draws).**
  SUPERSEDED by AC-84 on 2026-08-19 (`docs/settings-page.md` section 7): the
  chain of boxes became the vertical rail and the drawer became a full-screen
  surface. What it was protecting -- the daemon supplies the order and the page
  invents none of it -- survives there. It was MET 2026-08-18 and the account
  below is kept whole. The drawer's top row is one box per pipeline stage, in the order
  sound travels it, and a second un-chained row carries the rest -- permission
  mode, connections and Appearance. The page names none of it: `GET
  /api/settings` serves each group a `stage` (an integer for a pipeline stage,
  null otherwise) and a `headline` (the key of the setting its box shows, or
  null), and `buildChain` derives keys, order, labels and shown values entirely
  from that payload (AC-41, AC-45). The old number prefixes on the labels went
  with it: `stage` now carries the order the string was faking, and a headline
  naming a missing setting or a non-contiguous stage raises at import
  (`daemon/settings.py`), never a blank box. A seventh seam added in the daemon
  gets a box with zero changes under `web/` -- the same property that let the
  harness section arrive with no page edit. Verified in real Chrome (six chained
  boxes, three un-chained), since none of it is visible in markup.
- **AC-56 (a box shows what its stage is running, and repaints without a
  rebuild).** MET 2026-08-18, and AMENDED 2026-08-19 by
  `docs/settings-page.md` section 7: the BOX is gone with the chain, and the
  rule is carried unchanged by the rail's stops (AC-84), for the same reason --
  a rebuild mid-edit destroys the control under the cursor. Each box shows its
  label and the current value of
  its `headline` setting -- Recognition / parakeet, Brain / harness, Harness /
  pi; a group with a null headline shows its label alone rather than inventing a
  value. When that value changes the box text repaints, but the settings ROWS do
  NOT rebuild: the `sig` guard keys on `key:kind:group` and every group's
  `key:stage:headline` and deliberately excludes every value, because a rebuild
  on a value change is what once left the router's model dropdown missing a
  newly added connection until a page reload (D-12 one layer out). Proved in
  Chrome by writing a setting from outside the page and asserting the box text
  changed while the rows were not rebuilt.
- **AC-57 (one section at a time, and nothing unreachable).** SUPERSEDED by
  AC-83 and AC-85 on 2026-08-19 (`docs/settings-page.md` section 7), and
  directly contradicted: every section is now on one scroll. This is the
  criterion search could not live with -- a search that can only see the open
  section is not a search. It was MET 2026-08-18.
  Clicking a box shows that one section beneath the chain and hides the other
  eight, the two hand-built blocks (permission mode, connections) included; the
  open section is a module variable that survives closing and reopening the
  drawer within one page load and resets on reload -- never `localStorage`, which
  is banned and grep-gated. Boxes are real tab-role buttons with roving
  tabindex, `aria-selected` and matching tabpanels, operable by arrow/Home/End
  without a mouse. Proved in Chrome by clicking all nine boxes and asserting the
  matching section is in the DOM and the other eight are not.
- **AC-58 (the chain fits a phone).** SUPERSEDED by AC-88 on 2026-08-19
  (`docs/settings-page.md` section 7): the measured 760px drawer width and the
  wrap-and-drop-the-connector rule go with the chain. It was MET 2026-08-18.
  The drawer widens to
  `min(760px, 100% - 28px)` so the six pipeline boxes sit one row on the desktop
  -- the number is measured (624px of boxes plus the drawer's 36px padding plus
  one long model id of headroom), not chosen. Below that the row wraps to two or
  three lines and the first box on each new line drops its left connector,
  because a connector into nothing is the same lie as an arrow between two things
  sound never travels between. Which boxes those are is measured off the laid-out
  page by a `ResizeObserver`, never switched at a stylesheet width, because what
  wraps depends on the label and value lengths the daemon sends. Proved at 390px
  in Chrome: chain `scrollWidth == clientWidth`, no box clipped, no horizontal
  scroll.

- **AC-59 (one child per CONVERSATION, held across turns that completed).**
  MET 2026-08-19. The first turn that needs a coding agent starts one; every
  later turn prompts that same child, so only the first question of a
  conversation pays pi's 1.5-2.3 s spawn [MEASURED 2026-08-17, 21 runs]
  instead of every question paying it. Asserted on the fake's record of every
  `start()` -- one spec across two turns, and across four turns of a real hub
  conversation (D-18: the adapter-level witness and the caller-level one are
  different claims) -- and by PID against a live pi child, where the second
  turn must reach the same process the first one started.

  **Held only across turns that ENDED.** A turn marks itself complete at the
  prompt's terminal event; anything else disposes -- barge-in (`abort`), a
  caller that walked away (`hub._let_go` closing the generator on a dead
  speech segment or a swallowed exception), and a failed turn (`_give_up`).
  That clause is not tidiness. An unfinished prompt still owes a terminal
  event, and a held-open stream carrying the PREVIOUS prompt's ending is what
  answered the next thing the owner said with silence and a `Done` in the
  design before AC-46. Disposing makes those leftovers unreachable without a
  caller-side settle loop, which could not run anyway: `_answer` is suspended
  on that same stream and a second reader on one async generator raises. The
  cost is one respawn after an interruption.

  This REVERSES AC-46, and the losing argument is kept rather than deleted.
  AC-46 (2026-08-18) required the opposite -- a fresh child per message,
  disposed at the turn's end, chosen for the simplicity of having nothing to
  keep alive, reconcile or recover, and matching the reference
  implementation's CLAUDE backend. What overturned it was use: the owner
  reported having to re-explain themselves every prompt, and the measured
  conversation at `state/chats/chat-1787089456529-c51265*` shows two of five
  turns failing at `"The harness could not finish that one."` with no user
  message recorded -- the signature of a spawn that failed. So the spawn was
  not only 1.5-2.3 s of dead air but a failure point multiplied by the number
  of questions asked. The reference's own pi lane holds ONE session for the
  process's lifetime and rebuilds it only on an explicit event
  (`agent-chat.js` `_forceNewSession`, [READ 2026-08-19]); AC-46 copied the
  claude lane's lifecycle onto the pi one.

  What AC-46 was right about is recorded too: pi DOES resume from a session
  file across separate children. The chat above holds one `session` header and
  three user messages spanning six minutes with an unbroken `parentId` chain
  [MEASURED 2026-08-19], so the per-message design did not lose the agent's
  context. It lost time and reliability, not memory.

- **AC-60 (`HarnessSpec` equality IS the invalidation rule).** MET
  2026-08-19. `_Host.ensure()` resolves the spec every turn and compares it
  with what the running child was started as: equal reuses, different disposes
  and spawns. There is deliberately no list of invalidating events beside it.
  `HarnessSpec` is frozen, so a new conversation shows up as a different
  `session_file`, a spoken project switch as a different `cwd`, and a
  `harness` / `harness_model` / `harness_tools` write as a different `model`
  or `tools` -- every one of the owner's four ways to end a conversation is
  the same one check, and a fifth field gets the behaviour without anyone
  remembering to add it. A list would be a list that goes stale, and the
  failure of forgetting an entry is silent: an agent still answering from the
  repository the owner just left. Tested in both directions, because the
  first direction alone is trivially satisfiable: each of the four replaces
  the child, and a turn that changes a setting NO spec carries does not --
  the resolver hands back a fresh object every turn, so an `ensure()`
  comparing identity rather than equality would respawn on every question
  while passing all four (D-13).

- **AC-61 (WITHDRAWN before it was built).** Proposed on 2026-08-19 as
  "barge-in aborts, it does not dispose", on the measurement that pi settles
  an abort in 1.51 s and stays usable [MEASURED 2026-08-17]. Withdrawn the
  same day, during AC-59's implementation: keeping the child means keeping
  the stream, and the aborted prompt's terminal event is then queued where
  the next turn reads it. Draining it needs a caller-side settle loop -- the
  machinery `docs/harness-mode.md` 10.1 deleted -- and that loop cannot run
  where it would have to, because `_answer` is already suspended on the same
  async generator. The number is left here so nobody re-proposes it from the
  1.51 s alone: the settle is cheap, the second reader is the wall.

- **AC-62 (a held child never outlives the daemon).** MET 2026-08-19.
  `HarnessBrain.dispose()` exists and drops the held child;
  `butlerd._restart_cleanup` already called `brain.dispose()` by name and had
  nothing to call while a child lived for one message. It matters more now
  than it read then: `POST /api/restart` lands BETWEEN turns, which is exactly
  when a child is held, and `os.execv` replaces the image while keeping this
  process's handles -- so a coding agent left running would survive into the
  new image with nobody holding it, the same leak the kokoro sidecar taught
  (AC-36, D-24). Two witnesses (D-18): the adapter-level one asserts the
  child is disposed, and `test_restart.py` asserts the daemon calls it before
  `execv`. A hard kill is still `childproc.py`'s job and always was.

- **AC-63 (the page can start a new conversation).** MET 2026-08-19.
  `POST /api/session/new` rotates the chat record and answers `{id}`, 404 in
  endpoint mode like `GET /api/session/current` -- a daemon holding no
  conversation cannot start a second one, and that is a different fact from a
  rotation that failed. It rotates the RECORD and disposes nothing: the new
  `sessionRef` makes the next turn's spec differ, and AC-60 replaces the
  child. A hand-built button in the settings panel header POSTs it and then
  calls `loadSession`, which REPLACES the rendered transcript from the
  daemon's now-empty record. This exists because AC-59 makes a conversation
  last until something ends it, and until now the only way to end one was
  saying so.
- **AC-64 (the hub fills `ctx.history` with what was SPOKEN).** MET
  2026-08-19. `Hub.history` is a bounded deque of `(role, text)` recorded at
  the end of every turn that reached `_reply`, and handed to the brain as
  `TurnContext.history`. It holds the user's utterance and ONE assistant entry
  built from `SatelliteSession.spoken` -- the segments whose audio actually
  reached the satellite. That field has been right since it was written and
  nothing read it: a segment cut off mid-stream by barge-in is deliberately
  not appended, so an interrupted turn cannot tell the next one it said a
  sentence the owner never heard. That is the context-desync defect
  `ports.TurnContext.history` was written against and the most-corroborated
  finding in `.references/` (D-20).

  Bounded at `HISTORY_ENTRIES = 6` -- three exchanges [DECIDED 2026-08-19, not
  measured]. The ROUTER reads this on every single utterance and its whole
  value is being fast, so an unbounded transcript would grow the one prompt
  that runs before Butler can answer anything. A conversation longer than
  three exchanges is what the substantive model's own memory is for: pi
  resumes its entire session file.

  Recorded on EVERY exit from `_reply` and before the turn-ownership check,
  because a turn a barge-in interrupted still happened -- the owner said it
  and heard part of an answer. Cleared by a new conversation, spoken or
  clicked. One assistant entry per turn rather than one per spoken segment: a
  transcript showing six consecutive assistant messages would be describing
  Butler's synthesis pipeline rather than the conversation. A turn that said
  nothing adds only the user's half, because inventing an empty assistant
  entry would tell the model it answered with silence.

  Two things are deliberately NOT done. The invariant's second clause -- "the
  entry is marked interrupted" -- is not implemented: `(role, text)` has no
  room for a marker and adding one is a `ports` change nothing yet needs, so
  an interrupted turn is recorded truncated and unlabelled. And the reflex
  line ("Working on it.") rides along into history, because the owner HEARD
  it; the invariant is what was spoken, not what was worth speaking.

  The three rules have a D-13 instrument, `test/manual/mutate_history.py`,
  EXECUTED 2026-08-19: all three mutations aimed (exit 1 on the named tests,
  0 on the rest, 0 after restore). The "what was spoken" rule has TWO honest
  witnesses and the harness names both rather than pretending it has one.

- **AC-65 (the router sees the conversation).** MET 2026-08-19.
  `Router.verdict` puts `ctx.history` between the system prompt and the
  utterance. It used to build `[system, user]` -- one sentence, no context --
  and this is the defect the owner reported as having to explain themselves
  at every prompt. It is worse than a merely worse verdict: `answer` and
  `clarify` are answered by the router ITSELF and never reach the substantive
  brain, so a follow-up like "how many people live there" was answered by a
  small model that had never seen what it was following.

  What this does to routing ACCURACY is not measured. The router was tuned on
  single utterances [MEASURED 2026-08-16] and every prompt-shape change in
  that work moved the number, so a before/after run on the same utterances is
  the honest way to change `HISTORY_ENTRIES` or to claim an improvement. What
  is asserted is the mechanism: the first turn's router body carries no
  history, and the second's carries the first turn's user text and spoken
  answer.

- **AC-66 (three brain modes, one setting, one labelled control).** MET
  2026-08-19. `brain_mode` offers `endpoint`, `harness` and `harness_only`,
  shown as "Chat only", "Chat + coding agent" and "Coding agent only"
  (renamed from "brain only", "brain + harness", "harness only" by AC-813,
  2026-10-29). The stored
  VALUES are frozen and only the labels are new: an existing `settings.json`
  holds `endpoint` or `harness`, and renaming a stored value so a control
  reads better boots that install into a mode nobody chose. `Setting.labels`
  is the schema field that carries the display text, and it does a second job
  -- a spec that carries labels is one the daemon is saying should be laid out
  as a row of named buttons rather than a dropdown, which is how the page gets
  a segmented control without naming a setting (AC-41). It is opt-in and
  empty everywhere else: `reading_face` as a six-button row would be a worse
  dropdown. `test_every_label_names_a_choice_that_exists` checks the two
  tables both ways, because a label for a renamed value is a button the daemon
  rejects and a missing one is a raw identifier sitting beside two English
  phrases.

  **Every value must be one `build_hub` can construct.** A mode the page
  offers and the daemon cannot build is AC-7b with a restart in the middle of
  it: the owner picks it, restarts, and the daemon does not come back. The
  test drives `butlerd.BRAIN_MODES` itself rather than a literal list, so a
  fourth entry with no build path fails there.

  **Adding the third mode created three dead controls, and the schema says
  so.** With no router, `router_model`, `router_reasoning` and
  `router_max_tokens` decide nothing -- exactly the control that saves,
  reports success and changes nothing that AC-7b exists to prevent. So
  `describe()` now serves `inactive` per setting (the empty string, or the
  reason it does nothing in this mode) and per group (a boolean, true only
  when the whole group is inert). This was already true and unsaid twice
  over: `endpoint` has always left the entire harness seam inert, and
  `harness` has always left the conversation model and its cap inert. An
  inert setting is still stored, validated and editable, because the mode it
  belongs to is one restart away and a value set for it must survive the
  trip. Asserted in BOTH directions -- marking everything inert would pass a
  one-sided check while dimming controls that work.

- **AC-67 (harness-only mode has no router at all).** MET 2026-08-19.
  `HarnessOnlyBrain` is `_Host` plus `HarnessConverse` with nothing in front:
  every utterance goes to the coding agent. It declares no `NEEDS_TARGETS`,
  so `build_hub` hands it no `TargetResolver` -- the absence is the
  implementation, not a branch -- and the test asserts the model server is
  asked NOTHING during a turn, on an utterance the router demonstrably takes
  for itself in `harness` mode.

  It is registered as a Brain while `HarnessConverse` alone is not, and the
  difference is the point: the converse halves are unregistered because a
  Brain that answers everything it is given would SILENTLY drop the routing.
  Here dropping the routing is the declared behaviour of a mode the owner
  picked off a labelled control, which is the opposite of silent.

  It ignores `ctx.history` (AC-64) deliberately: pi resumes its whole session
  file, so handing it Butler's three-exchange window would repeat the last
  three turns to a model that already has all of them. It keeps `HAS_SESSIONS`
  and `CAN_SWITCH_PROJECT`, and both are exercised rather than read off the
  class -- a capability declared with no method behind it is one the hub calls
  into nothing. The three daemon-answered utterances (a turn mode, a new
  conversation, a project switch) still work, because `hub._on_final` matches
  them before any brain is called; that is what stops "new conversation" from
  being sent to pi as a question.

  **The cost is stated rather than hidden:** "what time is it" is now a full
  agent turn. Holding a child (AC-59) removes the spawn from that, but not the
  harness's own model -- measured at 1.55 s for a bridged cloud model and
  about 27 s for a local 4B on the same question [MEASURED,
  docs/harness-mode.md]. This mode is only pleasant with a fast
  `harness_model`, and the help text says so.

- **AC-43 (barge-in mid-thought reaches the brain, and names the turn).**
  MET 2026-08-17. `SatelliteSession.abandon` calls `brain.abort(turn)` when the
  abandoned state is THINKING or SPEAKING, BEFORE either task is cancelled, and
  a raise from it cannot break barge-in. THINKING is the window that mattered:
  it is where the brain is doing the expensive thing, and cancelling the reply
  task only stops US reading while the model goes on generating into a socket
  nobody drains -- D-4 one port over from where it was learned. SPEAKING counts
  too, because `turn()` yields intents as they are decided, so generation can
  still be running while the first sentences are in the room. Asserted on the
  CALLER (D-18) and mutation-checked four ways (D-13): restricting it to
  SPEAKING, moving it after cancellation, dropping the try/except, and deleting
  it entirely each fail exactly the test named for that property.
- **AC-42 (every engine port warms and identifies itself).** MET 2026-08-17.
  `warm()` and `info` exist on `Asr`, `Tts` and `Brain` and are conformance-
  tested on each; boot warms all three; the `ready` frame carries all three
  info dicts and the page shows them. Three assertions carry it, and they are
  deliberately at three different levels because each catches what the others
  cannot: conformance proves an adapter HAS the method, a protocol test proves
  the daemon SENDS all three on the wire, and a caller-side test proves boot
  CALLS `brain.warm` (D-18 -- the port having a method is not the daemon using
  it, which is the gap that has bitten this project three times). A warm that
  raises is logged and never fatal, because a slow first turn is survivable and
  a daemon that refuses to boot leaves the owner with no microphone (D-1).
  Every AC promoted from `docs/architecture-seams.md` is now on this list:
  AC-38 through AC-43 and AC-45 are closed, and AC-44 -- the `Harness` port
  -- is built and PARTLY met, with its own entry below saying which half.
- **AC-38 (the token caps are the owner's, persistent, and honoured on the
  wire).** MET 2026-08-17. `router_max_tokens` and `reply_max_tokens`, both
  default 0, both advanced settings inside the Brain section. With a cap N the
  next request body carries `max_tokens: N`; with 0 the body carries no
  `max_tokens` key at all -- asserted against the RECEIVED body on a fake
  OpenAI server in `test/conftest.py`, never against the setting being stored,
  because "the cap is in the file" and "the cap is on the wire" are different
  claims and only the second is the feature. Zero stops being a number in
  `connections.SettingsResolver`, so nothing downstream carries the special
  case. Read per turn, so a change takes effect on the next utterance without a
  restart. Mutation-checked both ways: sending `max_tokens: 0` and passing 0
  through the resolver each fail exactly one named test.
- **Per-role reasoning, on the same wire contract as the cap.** MET
  2026-08-18, the sibling of AC-38 and built on it. `router_reasoning` and
  `reply_reasoning`, both advanced Brain settings defaulting True, travel the
  same `SettingsResolver.resolve(role)` seam and are read per turn. False --
  the only value that acts -- becomes `chat_template_kwargs: {enable_thinking:
  false}` in `llm._body`; True leaves the body byte-identical, because the
  kwarg is a Qwen-family signal and a cloud connection that would reject it
  must be untouched until the owner who runs a local reasoning model turns it
  off. Per role because a reasoning model is right for one role and wrong for
  the other, exactly as the caps are. Asserted against the RECEIVED body on
  the fake OpenAI server (on the wire or nowhere) and mutation-checked by
  inverting the condition. The measurement that earned it: Ornith-1.0-9B with
  reasoning on takes 30-68 s to speak its first word and returns an EMPTY
  router verdict on a free GPU; off, the reply starts in 0.2 s, the router
  answers valid JSON in 0.5 s, and a full daemon turn measured 1.61 s
  end to end [MEASURED 2026-08-18]. Reasoning is a per-MODEL property expressed
  per role here; a future multi-family setup is where pi's `thinkingFormat`
  registry would earn its place (docs note in `daemon/AGENTS.md`).
- **AC-39 (an empty capped verdict loses the routing, never the turn).** MET
  2026-08-17. An unusable verdict -- unparseable, a route outside the enum, a
  route whose payload is missing, or empty because a cap was spent thinking --
  routes to the conversation model and logs `finish_reason`, the cap, whether
  a reasoning block came back, and the raw content. The behaviour is identical
  to a parse failure by design (3.8), so the log line is the only thing that
  tells "capped" from "garbage". Proved against the real model, not only the
  fake: with `router_max_tokens` 16, Qwen3-4B-Instruct-2507 returned
  `finish_reason=length` with the verdict cut mid-string and the turn was
  answered by the conversation path [MEASURED 2026-08-17]. A verdict from a
  DEAD server does NOT take this path -- it raises, and that distinction has
  its own test, because reusing the parse-failure branch for an unreachable
  server is the mistake `docs/reflex-serving.md` section 6 was written to
  prevent.
- **AC-40 (a capped reply is visible, never silent, never half-spoken).** MET
  2026-08-17. Whole sentences are spoken as they stream; a trailing fragment
  cut mid-sentence is sent to the page and NOT spoken; a reply capped to
  nothing speaks a fixed line, because silence is indistinguishable from
  Butler ignoring you. `Say` gained `spoken` and `Done` gained `truncated` for
  this, the `reply` frame carries `spoken` and `speak_end` carries
  `truncated`, and the unspoken text never enters the spoken history (D-20).
  Asserted on the CALLER as well as the adapter (D-18): only a hub test proves
  the daemon does not read the fragment aloud. Verified in a real browser
  against the real model with `reply_max_tokens` 12: one sentence spoken,
  "You'll need to" rendered dimmed and unspoken, `speak_end truncated=true`
  [MEASURED 2026-08-17].
- **AC-37 (a connection you added yourself works, and Butler stores no
  secret).** MET 2026-08-17.
  Adding a connection through the settings form -- label, base URL, environment
  variable NAME -- makes its models pickable and its completions routable. Two
  assertions carry the rule. Nothing under `state/` ever contains the key value,
  checked by writing a real key into the environment and grepping the whole
  directory after a save. And a connection whose named variable is unset fails
  LOUDLY at use, naming the connection and the variable, rather than issuing an
  unauthenticated request whose 401 arrives from somewhere else entirely. A
  reload of pi's registry leaves `origin: "manual"` entries untouched (4.4b).
- **AC-35 (a harness conversation survives a daemon restart).** REWRITTEN
  2026-08-18 by the owner (docs/harness-mode.md 10.4). MET, and PROVED LIVE
  rather than asserted: a real `pi` child was told a number, the daemon was
  torn down and rebuilt on the same store, and the second child answered the
  question from before the restart. The instrument is validated the same way
  (D-13) -- point the second child at a DIFFERENT reference and the same test
  fails with "I don't have any number to recall from this chat".
  `daemon/chats.py` and `test/test_chats.py` carry it.

  Talk to a
  harness, restart butlerd, and the next thing said continues the same
  conversation. The record is ONE flat JSON file per chat in
  `state/chats/` -- `id`, `createdAt`, `updatedAt`, `harness`,
  `sessionRef`, and `messages` of `{id, role, content, timestamp}` --
  rewritten whole, matching the reference implementation's CHAT-SESSIONS
  model by the owner's ruling of 2026-08-18. The transcript the page
  shows is the `messages` array; the agent's own context comes back
  through `sessionRef` -- a session file pi is pointed at, an opaque id a
  claude adapter passes to `--resume` -- read off `caps.native_resume`,
  never off the name. Boot re-attaches to the newest `updatedAt`. A
  reference the harness refuses with its literal missing-session words is
  cleared, once, and the turn replayed once (the predecessor's eight dead
  chats). The old version kept a per-session directory with `meta.json`
  and an append-only `events.jsonl` recording every tool call, rotated
  rather than replaced, and told the page which of `new`/`resumed`/`lost`
  the context was; the owner overruled it for the reference's flat-file
  shape -- the argument that lost was that the tool-level record and the
  agent-memory honesty line were worth their machinery.
- **AC-36 (a harness may not outlive the daemon).** MET 2026-08-17. Kill
  butlerd while a harness session is open and no orphan survives -- asserted
  by PID, on the process tree, not by the absence of an error. D-24 in its
  second home: the predecessor left this to stdin EOF and never registered
  the children at all. It covers EVERY child Butler spawns, not only the
  harness: the Kokoro sidecar was failing the same criterion, seven orphans
  deep, when the criterion was still worded as a harness rule. The mechanism
  is `daemon/childproc.py` -- one Win32 kill-on-close Job Object per daemon
  process, created through ctypes with no new dependency, which descendants
  join at creation, so the kernel kills the whole tree however the daemon
  died. `test/test_child_lifetime.py` is the proof and no-oping
  `AssignProcessToJobObject` is the D-13 mutation.

  Two honest notes. Only the Kokoro half was ever RED: the pi child exits on
  stdin EOF the moment the host's pipe closes, so its test is a regression
  guard rather than a red-to-green, and job membership for its `cmd /c` tree
  was verified directly with `IsProcessInJob` -- both the `cmd.exe` wrapper
  and the `node.exe` beneath it report true, which is the only evidence that
  the job rather than pi's courtesy is what the criterion rests on. And the
  instrument itself was wrong for a day: a PID is not an identity, Windows
  never clears a parent-PID field and recycles PIDs, so a descendant must
  also have STARTED AFTER the parent it claims (D-13, `test/AGENTS.md`).

- **AC-44 (a harness is an adapter behind one contract).** PARTLY MET
  2026-08-18. Promoted from `docs/architecture-seams.md` phase 7 and built
  as `docs/harness-mode.md` steps 7.1 to 7.7. What IS proven: `Harness` is
  the fifth port in `daemon/ports.py` with `HARNESSES`, `HarnessCaps`,
  `HarnessSpec` and six normalized events; TWO adapters, `harness_fake` and
  `harness_pi`, pass ONE suite
  (`test/conformance/test_harness_conformance.py`); no call site branches on
  a harness NAME; and both callers end a prompt on the exported
  `ports.TERMINAL_EVENTS` rather than on a copy each, because a copy per
  reader drifts the day a third ending lands and leaves the reader that was
  not updated waiting forever.

  What is NOT proven: `caps.native_resume` is read by NOTHING. It used to
  select a display string -- `_Host.session_state` answered `native` or
  `session_file` on the wire -- and that vocabulary went with
  `daemon/sessions.py` on 2026-08-18, so the capability is now declared,
  frozen by conformance, and branched on by no caller at all. There is no
  id-based resume path anywhere in the daemon, because pi is the only real
  harness and it resumes by a session FILE Butler names. So "resume is
  decided by a capability, never by a name" is a rule the code cannot
  currently break rather than a claim two implementations have tested, and
  the capability is kept on the same terms as `Action.writes` (4.4): its
  reader is the claude adapter, and it either gets one or it comes out.

  (Amended 2026-08-18: `HarnessCaps.tool_allowlist` was deleted with AC-29,
  because nothing reads it once tools are unrestricted -- a capability with
  no method behind it is a claim, not a contract. The old version froze a
  tool set at spawn; there is no tool set to freeze. The same ruling replaced
  the held-open child with a per-message lifecycle, and that half landed with
  AC-46 below on the same day: the contract's lifecycle sentence now reads
  "started and disposed per prompt", in `ports.Harness` and here. A
  conversation therefore lives in the SPEC rather than in the adapter object,
  which is what makes `native_resume` the whole of how context comes back.)

- **AC-46 (one child per message, and none in between).** SUPERSEDED by AC-59
  on 2026-08-19, after 25 hours as the contract. It required every harness
  turn to spawn a fresh child and dispose it at the turn's end. Kept here
  whole rather than deleted, because this criterion is now the record of a
  decision taken twice and reversed twice, and the second reversal is only
  legible against it.

  It replaced a child held open from boot -- which existed precisely to avoid
  the dead air a spawn buys (D-10) -- and the owner overruled that on
  2026-08-18 to match the reference implementation's per-prompt CLI
  lifecycle: the argument that lost was the 2 seconds, and it lost to
  simplicity, not to a measurement. AC-59 is not that first design coming
  back. The child is created by the first TURN rather than by boot, and any
  turn that ends badly still drops it, so the two properties AC-46 was bought
  for -- nothing to recover from, nothing to reconcile -- are kept where they
  were actually load-bearing. What AC-59 rejects is only the claim that a
  measurement of 1.5-2.3 s per question was worth that simplicity, and it
  rejects it on use rather than on taste: see AC-59 for the failed spawns.

  What that dead air is filled with instead is ONE fixed line
  (`HarnessConverse.NARRATION`), spoken the moment a turn reaches the
  harness and BEFORE the spawn (docs/harness-mode.md 10.7). It is the same
  sentence that used to be spoken on the turn's first tool, and that
  first-tool narration was DELETED rather than kept: two identical lines in
  one turn is a defect, and of the two only the earlier one covers real
  silence. `test_no_line_is_ever_spoken_twice_in_one_turn` is the guard.

  Asserted in `test/test_brain_harness.py`: two turns record exactly two
  `start()` calls on the same session reference, the host holds nothing at
  the moment `Done` is delivered (which is where `hub._reply` stops reading,
  so a disposal left to the generator's own `finally` would be scheduled by
  the garbage collector), and live -- marked `slow` -- a real pi tree is
  caught two deep while the turn runs and is gone once it ends, identified
  by PID AND creation time. A spawn that fails is a spoken FAILURE and one
  `Done` (AC-47, AC-48): "no child" stopped being a category, because a turn
  no longer finds one, it starts one. Measured on the day this landed: two
  consecutive live pi turns at 3.33 s and 2.96 s, each including its own
  spawn [MEASURED 2026-08-18].

  The first wording -- "the first turn must not carry the measured 9-10 s"
  -- was DELETED rather than adjusted. Spawn to prompt-accepted measures
  1.51-2.28 s over 21 runs [MEASURED 2026-08-17], and the old figure could
  not be reproduced on any axis tested, so a daemon spawning a child inside
  every single turn would have passed that phrasing. A criterion that cannot
  fail is not one (D-13).

- **AC-47 (a harness turn is never silent).** MET 2026-08-18. Every turn
  makes a sound: the streamed sentences, else the `turn_end` text a
  non-streaming provider only sends there, else a fixed line, else the
  failure. The motivation is measured, not imagined -- `claudebridge-local`
  produced ZERO `text_delta` events and put the whole answer in `turn_end`
  [MEASURED 2026-08-17] -- so an adapter mapping only deltas would have
  spoken nothing at all on that provider, which is the
  silence-reads-as-a-dead-microphone failure. Proven at three levels because
  each catches what the others cannot: the fake's no-delta mode in
  conformance, the pi mapper against lines recorded from a real child
  (`test/test_harness_pi.py`), and the brain speaking it
  (`test/test_brain_harness.py`). The recovery is GUARDED on nothing having
  been said, and both directions have a test: `turn_end` carries the message
  on streaming providers too, so an unguarded fallback reads the whole
  answer out twice.

- **AC-48 (one terminal event per prompt, and one announcement).** MET
  2026-08-18. Exactly one of `AgentEnd` or `Failed` reaches the caller per
  prompt -- aborted, failed, or the child dying mid-turn -- and exactly one
  `Done` reaches the hub per turn, whatever the harness did. Asserted in
  conformance for both adapters AND on the caller (D-18), because the
  predecessor's backend emitted `error` only on a JS exception and a
  provider failure held its turn lock forever (4.4c). Two things the build
  learned about MEASURING it. Counting terminals needs the TAIL, drained
  BEFORE dispose: reading stops at the first terminal event, so a fake
  yielding `Failed` and then `AgentEnd` passed the count unchanged, and
  draining after dispose put the check straight back to sleep because
  `harness_pi._drain` returns the moment the handle is nulled. And pi's own
  auto-retry emits a WHOLE `agent_end` per attempt [MEASURED 2026-08-17],
  which is this criterion's announce-five-times failure arriving from the
  engine rather than from Butler; the adapter turns auto-retry off at start,
  since Butler owns retry policy.

- **AC-49 (an unanswered harness dialog cannot wedge a turn).** PARTLY MET
  2026-08-18. Proven on the pi TRANSPORT: an `extension_ui_request` whose
  method is dialog-shaped (`confirm`, `select`, `input`, `editor`) is
  answered with the documented `{"cancelled": true}` -- including one
  injected mid-prompt -- while the fire-and-forget methods get no reply at
  all, because a line the protocol never asked for is its own defect
  (`test/test_harness_pi.py`). Every live pi test rides past the three
  unprompted requests that arrive before the first response [MEASURED
  2026-08-17], which is corroboration rather than proof.

  What is missing is the CALLER level, which is exactly D-18's gap: no test
  puts a dialog into the middle of an answer and asserts that the TURN still
  finishes. `harness_fake` cannot ask one -- the request is pi's vocabulary
  and not the port's -- so the fake would need a way to raise a dialog
  through the contract before a hub-level test could exist. Until it does,
  this criterion rests on one adapter's own suite.

  (Amended 2026-08-18: with a child per message (AC-46) the three unprompted
  startup dialogs arrive at the top of EVERY turn rather than once at boot,
  so the cancellation path moved off the boot path and onto the routine one
  -- it now runs on every question. The caller-level gap recorded above is
  unchanged.)

  **REVERSED 2026-09-12 by the owner's ruling of 2026-09-11 (AC-234,
  `docs/parallel-agents.md` D2, option A).** A coding agent may now ask a
  human a question and WAIT for the answer. `harness_pi.cancellation_for`
  answers None for a `select`, `confirm` or `input` raised while a prompt is
  in flight; the dialog becomes `ports.Ask`, a member of the CLOSED
  `HarnessEvent` union carrying the harness's own id, and the answer goes
  back out of band through `Harness.answer` and `POST /api/answer`.
  The argument that LOST is kept rather than deleted: this criterion was
  written because a dialog with nobody to answer it hangs the turn forever,
  and it was TRUE -- what overturns it is that a human is now watching a
  surface whose whole top bucket exists to show the question, and that both
  180-second timers are suspended while one is open so the turn cannot be
  cut out from under it.
  **WHAT IT COSTS, accepted by the owner in the same breath:** an unattended
  run that would have carried on now STOPS and waits. A long agent turn left
  alone overnight can stall where it used to finish, and Butler never
  auto-answers -- a Butler-invented default is byte-identical to the owner
  having said no. The prior art says that is the norm rather than a defect:
  of eleven shipped products, every one with a real permission gate waits,
  and nobody auto-denies on a clock (`docs/parallel-agents.md` 9.2).
  TWO EXCEPTIONS SURVIVE and each is stated with its reason rather than
  being a fallback. `editor` is still auto-cancelled -- no Butler surface can
  render or dictate it and it has no agent-side timeout at all -- and so is
  any dialog raised while NO prompt is pending, because pi raises unprompted
  requests at startup and they have no turn to attach to.
  The caller-level gap recorded above is CLOSED: `harness_fake` can raise a
  dialog through the contract and blocks on it, and
  `test/test_harness_question.py` drives a real hub through the whole path.

- **AC-68 (ambient is off by default, and its window is a number of
  seconds).** MET 2026-08-19, `docs/ambient.md`. `listen` ships `off`;
  `test_ambient.py`, `test_ambient_prompt.py` assert off costs zero decodes.
- **AC-69 (the back-fill is QUEUED after the final and before the brain, and
  the turn pays for it).** MET, as REWRITTEN (the concurrent-decode version
  is the losing argument, kept in the spec). Two `Model`s returned empty
  transcripts 9/9 [MEASURED]; `test_ambient.py`, `spike_ambient_concurrent.py`.
- **AC-70 (what was OVERHEARD is not what was SAID TO Butler).** MET, whole
  as of 2026-08-19. `dialogue.overheard_note`, both brains labelled, the
  router deliberately blind; `test_ambient_prompt.py`, `test_conversation.py`.
- **AC-71 (a single decode may never exceed the finalize ceiling).** PARTLY
  MET. The budget scales with the audio and an expired one is spoken rather
  than silent (D-26); the chunking half that removes the cost rather than
  bounding the wait is still open (`test_asr_transcribecpp.py`).
- **AC-72 (the buffer is memory-only, capped, and VISIBLY on).** MET. Ring
  never reaches disk (`hub.py`), page shows `#ambientChip` while recording
  (`web/app.js:679-683`).
- **AC-73 (the router cleans the instruction before the harness sees it).**
  MET, fixed live defect. `daemon/brain_harness.py`, `test_router_summary.py`,
  `test_project_switch.py`.

- **AC-74 (the wave is the room, and the lattice is its spectrum).** BAND CLAUSE
  SUPERSEDED 2026-10-27 by `docs/lantern-ui.md` CU-3 (the band is deleted; the
  lattice stays a `voice_effect` choice). Otherwise MET
  2026-08-19, `docs/lattice-wave.md`. The band shows a triggered trace of the
  real time-domain waveform with a phosphor history behind it; the full-page
  field shows particles whose density follows the tilted spectrum and which
  link to their near neighbours. No shape on screen is generated by a timer
  alone while audio is present. A line may trace the waveform and a particle
  may not: a swarm on a spring of tens of milliseconds low-passes an 8.5 ms
  pitch period into a smudge, so the trace takes the waveform and the lattice
  takes the spectrum.
- **AC-75 (link distance is the volume lever).** MET 2026-08-19. Link distance
  rises with level, so silence is scattered dust and a loud syllable snaps a
  constellation into being -- an order of magnitude fewer links at silence than
  at a syllable, measured in a real browser rather than asserted.
- **AC-76 (the ramp is a temperature arc).** MET 2026-08-19. The eight states
  run cold through the room's half of the turn, a dim warm pivot at `thinking`,
  vibrant gold at `speaking`; no pink or violet remains in the wave path, and
  error moved to hue 12-24 so it cannot be confused with gold at 62-100.
  KNOWN DIVERGENCE, recorded rather than fixed: in the LIGHT theme gold reads
  as brown and `thinking` and `speaking` are one state to the eye, and
  `wake`/`capturing`/`closing` share one rule -- six distinct `--sa` values
  against the dark theme's eight (`docs/lattice-wave.md` section 9,
  `docs/satellite-ux.md` 5.8). The ramp is the owner's ruling and the fix the
  spec names -- carry the temperature in chroma rather than forcing the hue --
  has not been taken.
- **AC-77 (the canvas and the stylesheet agree).** MET 2026-08-19. Every state
  with a stylesheet row has a canvas palette, and `error` never draws a cool
  wave. `setState` hands the canvas the page's state vocabulary, which the old
  three-mode interface avoided; it is a trade rather than a free win, because
  `setMode` alone cannot express `error` or `closing`.
- **AC-78 (the score is what the detector said).** MET 2026-08-19. `pushScore`
  sets the current value and it decays; the trace plots that against a
  threshold line taken from the caller (`opts.threshold`), and it starts EMPTY
  and shows only samples that arrived. Closes D-27, D-28, D-29.
- **AC-79 (the level chain has the shape a voice needs).** MET 2026-08-19.
  Asymmetric follower, the room's own floor tracked and subtracted, a slow loud
  reference frozen while nothing is said, and height as amplitude under a power
  curve. Every smoothing coefficient is `1 - exp(-dt / tau)`, so the same code
  no longer responds twice as fast on a 120 Hz tablet as on a 60 Hz laptop.
- **AC-80 (it costs what a tablet can pay).** MET 2026-08-19 on the DESKTOP,
  and unmeasured where it matters. Particle count scales by the square root of
  viewport area, the pair loop is a uniform grid rather than all-pairs, no
  per-particle or per-link `shadowBlur`, device pixel ratio capped at 2, and no
  work while the document is hidden or the canvas is off-screen. Every cost
  figure behind it comes from a desktop with a discrete GPU; the iPad is
  fill-rate limited in a way that machine is not and has never been tried
  (`docs/lattice-wave.md` section 9).
- **AC-81 (the three media queries are honoured on the canvas, not just in
  CSS).** MET 2026-08-19. Reduced motion draws fewer particles and gentler
  drift but never a still image; reduced transparency drops additive blending
  and the wash; increased contrast raises minimum alphas and thickens links.
  `prefers-contrast` and `prefers-reduced-transparency` reach the canvas for
  the first time, and each query is re-read on change rather than captured once
  at construction.
- **AC-82 (the page survives the visualiser).** MET 2026-08-19. A throw inside
  `create` still leaves a usable page, `detach` is safe to call twice, a null
  or zero-sized trace canvas is not an error, and the field canvas is optional.

- **AC-83 (one surface, one scroll).** SUPERSEDED 2026-10-27 by AC-700
  (`docs/lantern-ui.md` CU-11, the categorised sheet). Was MET 2026-08-19,
  `docs/settings-page.md`. The settings surface fills the viewport over the
  live page and every schema group plus the two hand-built blocks is reachable
  by scrolling; no section is hidden behind a tab. The microphone, the socket
  and the playback worklet all stay live behind it, which is why this is a
  SURFACE and not a second document: navigating to a separate URL would tear
  down all four and Butler would stop listening while a setting was changed.
  Supersedes AC-57.
- **AC-84 (the rail is the signal path, and it tracks).** SUPERSEDED 2026-10-27
  by AC-701 (the signal-path strip). Was MET 2026-08-19. One
  stop per pipeline stage in the daemon's order, each showing its group's
  headline value, with the non-stage groups below a divider; clicking a stop
  scrolls to its section and scrolling moves the marker. The page still names
  no group, stage or setting (AC-41). Supersedes AC-55 and carries AC-56's
  repaint-without-rebuild rule.
- **AC-85 (search reaches every setting).** MET 2026-08-19. Typing filters rows
  by label, help and key across all sections at once, hides sections with
  nothing left, and says so when nothing matches; advanced disclosures open
  while a search is active, or a matching advanced setting would be found and
  then hidden. `/` focuses the box, and never while something else is taking
  typing.
- **AC-86 (restart-flagged changes are counted once).** MET 2026-08-19,
  amended 2026-10-27 by AC-715/AC-716. Changing a setting whose `restart` flag
  is set raises a bar naming how many are waiting -- once, instead of every row
  saying "after restart" to nobody. The bar has no button any more: the daemon
  restarts by itself once idle (AC-715), and the page reconnects on its own.
  The old KNOWN LIMIT (a write from another device went uncounted) is closed:
  the list is the daemon's `pending` (AC-716).
- **AC-87 (a section says what it is, and the daemon says it).** MET
  2026-08-19. `Group.blurb` is a new schema field that `describe()` serves, and
  each heading prints it. A group without one prints no sentence rather than an
  invented one -- the page writing those sentences would be a page describing a
  seam, which is what AC-41's grep gate forbids, and the mock printed them from
  a field that did not exist yet.
- **AC-88 (it fits a phone).** SUPERSEDED 2026-10-27 by AC-705. Was MET
  2026-08-19. At 390px there is no horizontal
  overflow, the rail scrolls horizontally with the same stops and values, rows
  stack, every control keeps a 44px touch target, and `env(safe-area-inset-*)`
  is honoured on the surface's padding and the commit bar. The two traps that
  caused overflow in the mock are both closed: a flex text input needs
  `min-width: 0`, and a grid column needs `minmax(0, 1fr)`. Supersedes AC-58.
- **AC-89 (nothing in the shell names a setting).** MET 2026-08-19. The rail,
  the search index and the commit bar are all built from the schema payload, so
  AC-41 survives the rebuild. The permission-mode and connections blocks remain
  the only carve-outs and name only their own fixed fields.
- **AC-90 (the controls did not change).** MET 2026-08-19. Every control kind
  still renders and writes as it did -- the segmented choice from `labels`, the
  dimmed but editable `inactive` row with its sentence undimmed, the two-part
  model pair, the favourites star only where the schema opts in, and the
  single-key write with its error landing on the offending row. `buildChain`
  went; `buildGroup`, `buildRow`, `buildControl` and the eight kind-specific
  factories were restyled from the outside and not touched.

- **AC-91 (the link state reaches the hero).** MET 2026-08-19,
  `docs/satellite-ux.md`. When the socket is not open the page says so where
  the state is read from across the room -- kicker, headline and sentence --
  rather than only in a collapsed drawer; it distinguishes "reconnecting" from
  "gone" and returns to the true state by itself when `ready` arrives.
  MEASURED before and after: `standing by / Listening / "Armed and quiet."` for
  a whole 52 s outage, against `link lost / Reconnecting / "Butler stopped
  answering. This microphone is still live..."` within 16 s and back to
  `standing by / Listening` 2 s after the daemon returns; and 6 identical
  `websocket error` banners per outage against 0, the first still shown if the
  link was believed good. The microphone and the wake detector STAY LIVE
  throughout: this is a statement about what is said, never a new latch (D-1).
  It is also the answer to the AC-7b complaint that a page reporting listening
  while it hears nothing is that criterion's failure in a different organ.
- **AC-92 (the page's safety net respects the daemon's policy).** NOT MET.
  Built and REVERTED on 2026-08-19 within the hour, and the criterion was
  WRONG rather than unfinished (`docs/satellite-ux.md` 11.1, which is the
  account worth reading before re-proposing it). It required the page's
  abandonment budget for a state never to be shorter than the longest the
  daemon may legitimately stay in it, and offered two ways to get there: the
  daemon tells the page the live policy ceiling, or the page raises its own
  budget to the largest any mode allows. The cheap one shipped --
  `WATCHDOG_MS.capture` from 30 s to `END_WORD_MAX_MS`'s 300 s -- and the owner
  reported "hey jarvis doesn't seem to wake it up anymore" inside the hour.
  `onWakeFire` drops every wake while the page is in CAPTURE, and in `endword`
  mode the daemon never ends a turn on silence, so that timer is not a
  hang-catcher: it is the ONLY thing that returns an utterance the owner walked
  away from to wake-listening. MEASURED both ways on one fixture: at 30 s the
  page re-armed at 35.9 s and woke again at 40.1 s, three candidates in the
  daemon's log; at 300 s it was still `capturing` at 55 s, one candidate.
  Only the protocol option is left -- the live ceiling on the wire, most
  naturally in `capture_start` beside the turn id -- and it is unbuilt. The
  evidence that produced this criterion was one-sided and its own spec said so
  ("the mechanism was measured; the human was not"); the lessons are D-32.
- **AC-93 (every styled state is reachable, or is deleted).** MET
  2026-09-26 (ruling R5, CLAUDE.md 2026-09-16 backlog). `error` is wired:
  `SKIN_ATTR` maps the reconnecting and no-link skins onto
  `[data-state="error"]`, so AC-91 is what drives it and the visualiser's red
  branch draws for the first time. `wake` was STILL NOT reachable --
  `STATE_VIEW[WAKE].attr` is deliberately `"idle"` -- so its stylesheet row
  and its palette were DELETED rather than left as live code with no caller:
  the owner ruled the armed-and-quiet state does not get its own bright cyan.
  D-30 is CLOSED for the same reason.
- **AC-94 (the conversation is a surface, not a diagnostic).** MET 2026-08-19.
  The transcript is reachable without opening diagnostics, `HISTORY_MAX` is 50
  rather than 8, every turn carries a timestamp (a restored one uses the
  daemon's own from the chat record rather than inventing `Date.now()`, and a
  message with no timestamp gets no `<time>` at all), assistant markdown
  renders as marked-up text, and a failed turn is distinguishable from an
  interrupted one and from one still in flight. MEASURED: 0 rows containing raw
  `**` against `I'm **pi** -- a minimal...` before; 5 `strong`, 1 `em`, 1
  `code`; 3 of 3 turns stamped and 0 blank answers. The renderer is the one
  place a security defect could enter here, because every string in it comes
  from a microphone in a room or from a coding agent: it is a small explicit
  tokenizer emitting `strong`/`em`/`code`/`pre>code`/`ul`/`ol`/`li` and nothing
  else, no library and no `innerHTML`, and deliberately **no `a` and no `img`**
  so an agent that emits a URL cannot make something clickable on an always-on
  page. 0 `a` or `img` elements were produced in the after-measurement. The
  closed element set is what an adversarial read should check first.
- **AC-95 (typing is a peer of speaking).** NOT BUILT, and UNDER-SPECIFIED --
  it should be interviewed before it is planned rather than planned from
  `docs/satellite-ux.md`. A satellite would send a typed instruction and the
  daemon would treat it as a turn: same router, same brain, same record, same
  spoken answer unless muted, through one new inbound protocol message and the
  `_on_final` path the three daemon-answered utterances already share. Three
  questions it does not answer (its section 11.2): whether a typed turn
  suppresses the spoken answer, how it arbitrates against a spoken turn
  arriving at the same moment, and whether it carries a turn id the page mints
  -- which D-6 forbids.
- **AC-96 (stop, from the page).** SUPERSEDED by AC-111 and AC-112 on
  2026-08-19, before it was built. It was one line in a list asking for a
  control that stops Butler mid-reply on `speak_cancel` (review R8);
  `docs/voice-control.md` is that criterion done properly, with the owner's
  ruling on what stop STOPS (the speech and the turn) and Escape's scoping
  beside it.
- **AC-97 (retry, and correct).** **MET** -- built in `b252c42`, and this entry
  said NOT BUILT for weeks after it shipped (D-12). A turn can be re-asked, and
  a misheard turn corrected and re-sent as text without being dictated again;
  the corrected text is what enters the record and what the router sees, and
  the original stays visible so the correction is not a rewrite of history.
  **Its "never an edit" clause was OVERTURNED on 2026-09-10** by the owner's
  rewind ruling (AC-189..AC-196, `docs/rewind.md`): there is now a route that
  truncates the live conversation at a message. Nothing about a CORRECTION
  changed -- it is still a new turn that mints its own id and edits nothing --
  and what survives of D-20 is that nothing is silently rewritten, because a
  rewind copies the whole original into an ordinary conversation first. The
  losing argument is kept whole in `hub._typed`'s docstring.
- **AC-98 (past conversations are reachable).** NOT BUILT. The conversations
  already on disk are listable and readable from the page, newest first, with a
  way to find a turn by its words. Needs a list route over `state/chats/`,
  bounded as `MESSAGES_SERVED` already bounds the current one. Read-only is
  enough: whether the AGENT can resume one is `caps.native_resume` and is not
  the page's to guess (AC-35, AC-44).
- **AC-99 (a turn says what it cost).** NOT BUILT. Every turn carries
  end-to-end latency and, where the seam knows it, the tokens and the model
  that answered. Today's `first Nms / final Nms` is the recognizer only and is
  silent about the model, which is the slow half. Needs the timings `llm.py`
  and the harness adapters already know and do not report; adopt the field's
  names (review R10): `end_of_utterance_delay`, `ttft`, `ttfb`, `e2e_latency`.
- **AC-100 (every control has a name).** MET 2026-08-19. Every input, select
  and checkbox the settings surface renders is programmatically associated with
  its label: MEASURED 42 of 61 controls with no computed accessible name
  before, 0 of 61 after. The association is built from the schema payload like
  everything else, so the page still names no setting (AC-41).
- **AC-101 (the surface has structure, and takes focus).** MET 2026-08-19 in
  two clauses of three. Section titles are real headings -- MEASURED 0 before,
  9 after -- and opening the surface moves focus to `#closeSettings` inside it
  while Escape returns focus to `#settingsBtn`. The third clause is DECLINED
  rather than missed: content behind the scrim is NOT removed from the tab
  order and `aria-modal` stays `"false"`, because the page behind the surface
  is still listening and the owner is still allowed to tab back out to it. What
  was fixed is where the first Tab lands (it used to reach `mic-arc`, a control
  the surface covers and blocks the pointer from); this is deliberately not a
  focus trap.
- **AC-102 (a failure and an answer are both announced).** MET 2026-08-19.
  `#errors` is `role="alert"`, `#notice` is `role="status"`, and the reply is a
  polite live region, so a device whose speaker is muted still tells its user
  what happened.
- **AC-103 (the microphone is on screen at every size).** MET 2026-08-19.
  MEASURED at 844x390: the mic control's bottom edge was 409px in a 390px
  viewport and is now 368px with no scroll.
- **AC-104 (a switch shows that it is on).** MET 2026-08-19. The boolean
  control's on state is visible from its styling -- MEASURED as
  `oklch(0.89 0.012 265)` border and a transparent background either way
  before, against `--sa` and `--sa-dim` when checked after. Closes D-31. The
  instrument was itself wrong first and was corrected (D-13): reading the
  computed style immediately after the change event caught the 300 ms
  transition's START value re-serialised into `oklab`, reported "they differ",
  and would have passed against a page where nothing worked at all.
- **AC-105 (the page teaches itself).** MET 2026-08-19, with one clause of it
  found false and recorded rather than met. Before the microphone is armed the
  page names the wake phrase, and in a turn mode that ends on a phrase it names
  one of the daemon's own `end_phrases`. The spec said both come from settings
  the daemon already serves and that neither is written down in `web/`; that is
  true of the end phrase and FALSE of the wake phrase, because there is no
  `wake_phrase` setting -- the detector is whichever ONNX file `web/models`
  holds and the phrase is [DECIDED] in section 7. So `WAKE_PHRASE` is a
  constant in `web/app.js`, shown and never matched against anything, and it
  moves when the model does.
- **AC-106 (a satellite installs).** MET 2026-08-19. A web app manifest and the
  iOS meta tags, so the phone and the tablet run full-screen from a home-screen
  icon -- MEASURED as manifest and icon both served 200 and 3 Apple tags. An
  offline shell is explicitly OUT of scope: a satellite with no daemon has
  nothing to do, and a cached page that comes up saying "Listening" is AC-91
  from a new direction. UNPROVEN on a device: every viewport was emulated in
  desktop Chrome and nothing has actually been installed
  (`docs/satellite-ux.md` 11.2).

- **AC-107 (speech speed is a setting and it reaches the ENGINE).** MET
  2026-08-19, `docs/voice-control.md`. A `tts_speed` number setting, default
  1.0, read per turn by `Hub._speed()` and carried through `Tts.synthesize`,
  the sidecar's `/tts` body and `kokoro-js`'s `stream({voice, speed})`.
  Asserted on the WIRE -- the body the sidecar actually received -- and not
  merely that the value is stored, which is the AC-38 rule: "the cap is stored"
  and "the cap is on the wire" are different claims. Read per turn rather than
  restart-flagged, like `voice`. The coupling the spec's section 6 warned about
  is NOT closed: `speech.py`'s word caps (`FIRST_SEGMENT_MAX_WORDS` 8,
  `LATER_SEGMENT_MAX_WORDS` 16) bound things measured in milliseconds (AC-4,
  AC-5) and speed is the constant that breaks the words-to-seconds proxy. That
  is arithmetic, not an observation -- nobody has measured seconds per segment
  at either end of the range on this machine, and `test/bench_tts.mjs` is the
  instrument that would.
- **AC-108 (the voice list is the ENGINE's, not a tuple in the daemon).** MET
  2026-08-19. `Tts.info["voices"]` is learned from the sidecar pool, which
  already knew; the hardcoded 11-name `VOICES` tuple is DELETED rather than
  extended (AC-42), because a list written into the daemon is wrong the day the
  engine changes and wrong invisibly -- the pool reported 28 while the tuple
  offered 11 and `kokoro-js` ships 54 packs. A sidecar that cannot be asked is
  a LOUD failure and never an empty picker, the same rule `LocalListUnavailable`
  already holds.
- **AC-109 (a voice can be heard before it is chosen).** MET 2026-08-19. One
  fixed line of speech in a named voice, on demand from the settings surface,
  over a plain HTTP route returning audio the page plays itself -- **never the
  socket**, because nothing is spoken outside a turn (`daemon/AGENTS.md`, D-6)
  and the page discards frames for a turn it does not own. It registers under
  its own `PREVIEW_HANDLE` so a live turn's abort cannot cancel it and it
  cannot cancel a live turn, it is spoken at the owner's `tts_speed` so the
  audition is what they will actually hear, and it changes no setting:
  auditioning a voice must not become choosing it.
- **AC-110 (Butler can be told not to speak, and the page says so).** MET
  2026-08-19. The `speak` setting suppresses the spoken reply while the reply
  still arrives as TEXT and still enters the transcript and the record --
  muting output is not dropping the answer. The page carries a `role="status"`
  chip, because a silent Butler and a broken Butler must not look the same
  (AC-7b). A DAEMON setting, so every device goes quiet at once (D-2, AC-8);
  per-device mute is the acknowledged right end state and waits for a real
  annoyance rather than a prediction (`docs/voice-control.md` D3).
- **AC-111 (stop, from the page, reaches the engine).** MET 2026-08-19, with
  one clause outstanding. `{"type": "stop", "turn": n}` is the new inbound
  message; the page drops local playback FIRST so the room goes quiet at the
  press rather than at the round trip, then names the turn, then re-arms itself
  (D-1, AC-2). The daemon ignores a `stop` that does not name the turn the
  session is on (D-6) -- a stale press must not take the microphone away from
  the turn the owner is now in -- and otherwise calls `abandon()`, the path
  barge-in already proves, rather than a second copy of it (D-18). So the abort
  reaches the ENGINE (the kokoro sidecar's own `/abort`) and, per the owner's
  D1 ruling, the brain's abort as well, which in harness mode is `abort()` then
  `dispose()`. **This is the only irreversible act on the page**: a mis-hit
  destroys a coding agent's work in progress, and AC-112's scoping is what
  bounds it. OUTSTANDING: the criterion required the labelled `simulator` stub
  to be deleted in the same commit, and it is still in `web/index.html` saying
  "nothing in protocol v1 lets this page stop the daemon mid-reply", which is
  now false (D-12).
- **AC-112 (Escape stops, and does not fight the settings surface).** MET
  2026-08-19. Three cases in this order and no other: the surface is open and
  Escape closes it and stops nothing; the surface is shut and Butler is
  thinking or speaking, and Escape stops; anything else and Escape does
  nothing. The key and the microphone control share ONE handler (`stopTurn`),
  because two paths to the same act is exactly how a contract drifts (D-18),
  and the microphone control needed no new button -- `micAction()` returned
  `act: null` while thinking or speaking, which is precisely the state a stop
  belongs in.

### Promoted 2026-09-26 -- built specs, owner's ruling R3 (CLAUDE.md,
"the rulings of 2026-09-16")

Each criterion below carries the status found in the code and tests at HEAD
(90ebca5), not the status claimed in the spec's own prose -- several specs
still call themselves PROPOSAL after shipping. MET / PARTLY MET / NOT MET /
SUPERSEDED, never a blanket MET for a whole spec.

**mic-optional -- AC-175..AC-184** (`docs/mic-optional.md`, built
2026-09-10).
- AC-175 MET -- socket connects at boot, mic untouched. `web/app.js`
  `connect()`; `probe_page.py`.
- AC-176 MET -- typed turn round-trips with mic off. `test_typed_turn.py`.
- AC-177 MET -- playback independent of capture. `web/app.js`
  `openPlayback()`.
- AC-178 MET -- `micLive`/`linkWanted` split, watchdog follows the turn.
  `web/app.js`.
- AC-179 MET -- `stt_enabled` setting, page arms itself at boot.
  `test_settings_api.py`.
- AC-180 MET -- pill and mic button are one control (`micAction()`).
- AC-181 MET -- wave button dictates into composer via `/api/dictate`,
  never sends. `test_dictate.py`.
- AC-182 MET -- speaker button toggles `speak` through `settings.set`.
- AC-183 MET -- per-message play/stop via `/api/tts/say`. `test_tts_say.py`.
- AC-184 MET -- `.micbtn` has no `border` rule left in `web/index.html`.

**chat-search -- AC-185..AC-188** (`docs/chat-search.md`, built
2026-09-10).
- AC-185 MET -- `GET /api/chats?q=` same shape, filtered, blank q
  unfiltered. `daemon/butlerd.py`.
- AC-186 MET -- matches title and message content, case-insensitive.
- AC-187 MET -- content match carries a bounded snippet.
- AC-188 MET -- sidebar search survives `paintChatList`'s `replaceChildren`
  rebuild.

**rewind -- AC-189..AC-196** (`docs/rewind.md`, built 2026-09-10; overturns
AC-97's "never an edit" clause, recorded there).
- AC-189 MET -- `POST /api/session/rewind {message}` truncates at that
  message. `daemon/butlerd.py:post_session_rewind`.
- AC-190 MET -- whole original copied before truncation.
- AC-191 MET -- non-user or unknown target refused 400, record unchanged.
- AC-192 MET -- harness session file moved to the snapshot's name.
- AC-193 MET -- kept transcript seeded into the next prompt (`seed_note`).
- AC-194 MET -- rewind control on owner's own messages only, puts text in
  composer unsent.
- AC-195 MET -- page repaints from the route's answer, splices nothing.
- AC-196 MET -- rewindable once the daemon's record names the message id.
  Spec's own build note: 920 passed, `probe_ux.py` 43/43, scratch-daemon
  measurement in `docs/rewind.md` 4a.4.

**attachments -- AC-197..AC-206** (`docs/attachments.md`, built
2026-09-11).
- AC-197 MET -- `POST /api/attach`, size-ceiling refusal with 413.
- AC-198 MET -- type sniffed from bytes, unrecognised type refused by name.
- AC-199 MET -- pending-on-session, cleared in `_begin_turn` after read.
- AC-200 MET -- PDFs/`.docx` extracted via `pypdf`/`python-docx` before any
  model sees them.
- AC-201 MET -- file path reaches the harness as a labelled block.
- AC-202 MET -- `TurnContext.attachments` is its own field, never merged
  into the utterance.
- AC-203 REPLACED by AC-815 (2026-10-29) -- was: image goes to
  `vision_model` when named, else the conversation model.
- AC-204 MET -- a model that cannot see is reported out loud, before or
  after.
- AC-205 MET -- page attaches a file or a camera photo, both removable.
- AC-206 MET -- nothing added to the WebSocket frame set;
  `test_protocol_doc.py` unchanged.

**desk-hotkeys -- AC-113..AC-123** (`docs/desk-hotkeys.md`, built
2026-09-09).
- AC-113 MET -- dictation round trip. `test_desk_dictation.py`.
- AC-114 MET -- key suppressed, focus unmoved. `desk/deskd.py`.
- AC-115 MET -- a failed dictation writes nothing to the clipboard.
- AC-116 MET -- dictation refused 409 while a turn holds the recognizer;
  mutate-and-restore instrument. `test_dictate.py`.
- AC-117 MET -- clipboard holds exactly the pasted text.
- AC-118 MET -- Ctrl+dictate opens pi in the configured `terminal_cwd`.
  `test_terminal_route.py`.
- AC-119 MET -- terminal outlives a daemon restart/kill.
- AC-120 MET -- a `cmd`-metacharacter path refused, nothing spawned.
- AC-121 MET -- hotkeys are settings, default off.
- AC-122 MET -- desk agent dies with the daemon, via the Job Object.
- AC-123 MET -- pasted text carries no CR/LF/control byte.
  `desk/paste.py`.

**shortcuts -- AC-133..AC-151** (`docs/shortcuts.md`, built 2026-09-09,
extended same day in section 9).
- AC-133 MET -- pane state lives in `ShortcutStore`, not a page setting.
- AC-134 MET -- rows added/edited/removed, upsert replaces a row whole.
- AC-135 MET -- chord capture consumes the keystroke, nothing else fires.
- AC-136 MET -- a chord opens one terminal in that row's directory/agent.
- AC-137 MET -- dictation chord is a row in the same table, rebindable.
- AC-138 MET -- bare-unsafe keys refused by name at bind time.
- AC-139 MET -- a modifier change mid-hold still replays the down's
  resolution on the up.
- AC-140 MET -- a bad table change leaves the running table untouched.
- AC-141 MET -- `POST /api/terminal` resolves `agent` through a closed
  `{pi, claude, opencode}` table, never interpolates a command.
- AC-142 MET -- the `hotkeys ON` log line names every bound chord.
- AC-143 MET -- Browse runs in a child process, blocks nothing.
- AC-144 MET -- `terminal_cwd` migrated once into a row, then retired from
  the schema.
- AC-145 MET, REWRITTEN 2026-09-09 -- a lost key-up recovers by TIMEOUT
  (`dictate_max_seconds` + slack), not by polling `GetAsyncKeyState`
  (measured impossible for a suppressed key, `probe_asyncstate.py`).
- AC-146 MET -- clicking an agent row opens a terminal with no chord set.
- AC-147 MET -- six rapid clicks recorded exactly one spawn.
- AC-148 MET -- a launch refusal reaches the pane in the daemon's words.
- AC-149 MET -- launch and bind are never the same activation.
- AC-150 MET -- claude always carries `--dangerously-skip-permissions`
  from the daemon's closed table.
- AC-151 MET -- buttons render in the daemon's own order.
  Spec's own build note (9.7): 734 passed, real-browser drive, zero page
  errors.

**language -- AC-152..AC-160** (`docs/language.md`, built 2026-09-10).
- AC-152 MET -- `language` is the one setting; no second locale field.
- AC-153 MET -- language list discovered from `Asr.info`/`Tts.info`, not
  hardcoded.
- AC-154 MET -- an unspeakable language is never offered (gate test).
- AC-155 MET -- `Tts.info["voices"]` carries each voice's language.
- AC-156 MET -- a blank voice resolves inside the current language.
- AC-157 MET -- a voice that disagrees with the language is refused, not
  clamped.
- AC-158 MET -- pt-BR utterances transcribe as Portuguese (nemotron,
  measured).
- AC-159 MET -- Portuguese replies synthesize with a Portuguese voice and
  phonemes.
- AC-160 MET in full at step 3 -- `speak_start` and `Final` both carry
  `lang`. Was HALF MET after step 1 (spec's own account); closed by step 3.
  Evidence: `test_seams.py`, `test_settings_api.py`, `test_tts_kokoro.py`,
  `test/conformance/test_tts_conformance.py`.

**language-auto -- AC-207..AC-215** (`docs/language-auto.md`, built
2026-09-11).
- AC-207 MET -- `language` gains `auto`, candidates from the engine
  catalogue.
- AC-208 MET -- a region-less language matches a voice's primary subtag.
- AC-209 MET -- one language per turn, decided once, before speaking.
- AC-210 MET -- an abstention keeps the conversation's latched language.
- AC-211 MET -- the eight fixed lines translate, English is the fallback.
- AC-212 MET -- fixed live defect: `Hub._voice_for` is the one resolver,
  engine and `speak_start` agree.
- AC-213 MET -- under `auto` the `voice` control is dimmed and says so.
- AC-214 MET -- a `/api/tts/say` replay detects on the message's own text.
- AC-215 MET -- `auto` never pins the recognizer; the `requires_language`
  guard is deleted.
  Evidence: `test_language_auto.py`, `test_seams.py`, `test_settings_api.py`,
  `test_tts_say.py`.

**familiar-ui -- AC-161..AC-174** (`docs/familiar-ui.md`; the spec's own
header still reads PROPOSAL, which is STALE -- the protocol change, the
appearance schema and the transcript-as-main-view all shipped 2026-09-10).
- AC-161 MET -- transcript is the main view, HUD behind an honest fold.
- AC-162 MET -- `{"type":"typed"}` inbound frame, same router/brain/record
  as speech.
- AC-163 MET -- daemon mints the turn id, page names none.
- AC-164 MET -- sidebar lists chats from disk, newest first, read-only.
- AC-165 MET -- new chat is one click (`$("newChat")`, `newChatFrom`); the
  ORIGINAL mechanism is SUPERSEDED by `docs/filing-and-pacing.md` section 8
  (project-scoped birth, AC-287), same user-visible behaviour.
- AC-166 MET -- sidebar collapse state in the daemon's store, not
  `localStorage`.
- AC-167 MET -- `voice_effect`/`viz_placement` setting, `off` stops drawing
  only.
- AC-168 MET -- state named in words (`#stateDesc`) at every state.
- AC-169 SUPERSEDED 2026-10-27 in part by `docs/lantern-ui.md` CU-8: the actions
  are icons with tooltips, no visible word; copy stays keyboard reachable. Was
  MET -- per-message copy control, keyboard reachable.
- AC-170 MET -- empty state names the wake phrase and example prompts.
- AC-171 MET -- `.saved`/`.busy` now have CSS, a save is visible.
- AC-172 MET -- `probe_page.py`/`probe_ux.py` updated in the same commits
  that moved the markup.
- AC-173 MET -- `reading_face` setting, six choices, stored on the daemon.
- AC-174 SUPERSEDED 2026-10-27 by AC-680 (`docs/lantern-ui.md` CU-4: colour
  schemes retired; each style owns its state colours). Was MET -- three colour
  schemes repaint surfaces only; `test_seams.py`
  reads the shipped stylesheet and fails if a scheme block declares a state
  token.

**parallel-agents -- AC-228..AC-251** (`docs/parallel-agents.md`; phases
1-5 and 7 built, phase 6 daemon-side built 2026-09-16, phase 6's PAGE half
plus phases 8 and 9 remain proposed -- `docs/triage-list.md` built three
further phases on top but explicitly leaves AC-242/AC-243/AC-248
untouched).
- AC-228 MET -- rail filters one list, ALL clears it.
- AC-229 MET -- scoping done daemon-side, page draws what is served.
- AC-230 MET -- hiding the list leaves the rail with the slot count and New
  chat.
- AC-231 PARTLY MET -- desktop re-measured MET 2026-09-15 (328 px sidebar,
  chat list fully in viewport); the phone size is UNSATISFIABLE as written
  (a rail, a 264 px list and a transcript do not fit 390 px) and the spec
  itself leaves this for the owner to restate. Sidebar was rebuilt again
  2026-09-26 (project tree) and not re-measured against this criterion.
- AC-232 MET -- project hue never drawn from a state token, `test_seams.py`
  gate extended.
- AC-233 MET -- `hub.status_for` derives idle/running/waiting/done/error,
  served by `GET /api/chats/{id}/status`, page infers nothing
  (`web/app.js` `recoverTurnClock`).
- AC-234 MET -- `waiting` only when a real question reached a human;
  Butler never invents or auto-resolves a deadline; harness idle timers
  suspended while waiting.
- AC-235 MET -- last action from real tool events, age shown when there is
  none.
- AC-236 MET -- elapsed measured from a daemon-recorded start, survives
  reload.
- AC-237 MET -- cap enforced at `_Host.ensure()`, refusal names what would
  free a slot.
- AC-238 MET -- a device speaks the conversation it is showing; looking
  away mutes (Say(spoken=False)), never disposes. `test_view_ownership.py`.
- AC-239 MET -- opening a conversation spawns nothing; sending does.
- AC-240 MET -- clicking a past conversation then sending continues THAT
  conversation, `backToLive()`'s forced detour removed.
- AC-241 MET -- one child per conversation, `HarnessSpec` equality (AC-60),
  no cross-conversation disposal.
- AC-242 MET -- wake word interrupts only the audible session's turn,
  `test_hub_bargein.py`.
- AC-243 PARTLY MET -- `Hub.stop_conversation` exists and stops a named
  conversation wherever it is owned, but per `docs/triage-list.md` section
  8 it is wired only to the confirm dialog's "yes" button; ordinary
  Escape/Stop still key off the DEVICE's `data-state`
  (`web/AGENTS.md:528-535`), not the conversation on screen. The page
  rewrite this needs is unbuilt.
- AC-244 MET -- enforced by the existing AC-41 grep gate, extended to this
  surface (project names, hues, cap, vocabulary all daemon-served).
- AC-245 MET -- a slot is a child process, freed on finish/fail/stop;
  refusal at cap names which project holds which slot.
- AC-246 MET -- a spoken turn always names its conversation; `ready`
  carries `chat`.
- AC-247 MET -- an attachment pends against the conversation on screen when
  attached, not the device's next turn.
- AC-248 NOT MET -- `post_session_rewind` still operates only on
  `hub.session_brain()` (the live single conversation) with no check for a
  running background conversation's agent; `docs/triage-list.md` section 8
  confirms this is untouched ("this spec adds no new rewind surface").
- AC-249 MET -- a question broadcasts to every connected device, survives a
  reload.
- AC-250 MET -- a late answer naming an unknown `qid` is refused, never
  forwarded.
- AC-251 SUPERSEDED by AC-287/AC-288/AC-291 (`docs/filing-and-pacing.md`
  section 8, 2026-09-26). Built as "a tile press files the conversation"
  (`295bf85`), reversed the same evening to "browsing moves nothing, filing
  is its own act" (`8720f02`), then the whole filing mechanism it depended
  on was retired: a chat's project is now fixed at birth and a move is
  unrepresentable, so there is no "file here" control left to be right or
  wrong about.

**voice-effects -- AC-252..AC-262** (`docs/voice-effects.md`, steps 1-6 of
section 5 built 2026-09-16, `906d31a`/`7e7d479`; step 7 docs-only, step 8
not built).
- AC-252 MET -- `voice_effect` is a daemon setting in `look`; `web/` names
  no treatment.
- AC-253 MET, with a recorded defect -- nothing paints inside the
  composer (occlusion by construction), but three treatments (`sonar`,
  `ember`, `dust`) overdraw past their perimeter onto surrounding text;
  only `filaments` was fixed (`web/AGENTS.md`).
- AC-254 MET -- listening draws inward, speaking pushes outward, in every
  treatment.
- AC-255 SUPERSEDED 2026-10-27 by AC-680 (CU-4: `--in`/`--out` follow each
  style's --blue/--amber). Was MET -- `--in`/`--out` identical across colour schemes
  (`test_seams.py`).
- AC-256 MET -- no treatment changes the composer's height/position.
- AC-257 MET -- reuses `visualiser.js`'s existing chain; no second tap or
  `AudioContext`.
- AC-258 MET -- `off` stops the drawing, `viz.*` call sites untouched.
- AC-259 MET -- every treatment (nine, including the lattice) gated under
  `prefers-reduced-motion`; the lattice's own gap was found and fixed the
  same pass.
- AC-260 MET -- switching treatments leaves no residue of the previous
  one.
- AC-261 NOT MET -- iPad frame-budget measurement is manual only
  (`test/manual/probe_reduced_motion.py`), nothing runs it in CI, no
  per-treatment screenshot gate exists (step 8, explicitly left open).
- AC-262 MET -- `voice_effect` is the one control that answers "what is
  drawn".

**filing-and-pacing, item 1 -- AC-271..AC-283, superseded in part by
section 8** (`docs/filing-and-pacing.md`, phases 1-5 built; section 8's
2026-09-26 rewrite -- `80effe1`, `90ebca5` -- replaces the navigation
mechanism these describe while keeping their invariants).
- AC-271 SUPERSEDED by AC-288 -- phases 1-5 guarded against a project
  change; section 8 makes a move UNREPRESENTABLE instead (`POST
  /api/project` deleted), which is the same intent built stronger, not a
  reversal.
- AC-272 SUPERSEDED (AMENDED, not reversed) by AC-291/D-D1 -- "browsing
  moves nothing" still holds; the mechanism changed from a filtered flat
  list to a project tree that expands/collapses in place.
- AC-273 SUPERSEDED -- the per-project last-opened HIGHLIGHT this
  describes was never needed once section 8's tree shipped;
  `expanded_projects` (D-D2) tracks which projects are open, not which chat
  was last opened.
- AC-274 MET -- starting a chat where a project holds a busy child raises
  the confirm; declining cancels the whole gesture. `test_project_stop.py`.
- AC-275 MET -- an idle held child releases its project's slot on a lazy
  check past `project_idle_grace_minutes`, no background timer.
  `test_harness_project_limit.py`.
- AC-276 MET -- a reap or confirmed stop disposes the process only; the
  session file is untouched.
- AC-277 MET -- a session-file resume and a `seed_note` transcript seed are
  distinguished by whether `sessionRef` was explicitly cleared.
- AC-278 MET -- the seed path's newest-first trimming is documented, per
  D6.
- AC-279 MET -- the root ("butler") project's row cannot be removed or
  re-pathed (`ShortcutStore`'s `same_dir` guard).
- AC-280 SUPERSEDED, EXPLICITLY OVERTURNED by section 8 R-D (2026-09-26):
  "All chats" is deleted; the tree shows every chat under its project.
- AC-281 MET -- no chat is ever left unprojected; a one-time boot migration
  files every blank-project record into the root row.
- AC-282 MET -- `project_agent_limit` (default 1) is a number, not a
  redesign, per project. `test_harness_project_limit.py`.
- AC-283 MET -- the per-project collision check runs before the global cap
  check; the two refusals are never confused.

**filing-and-pacing, item 2 -- AC-284..AC-286** (`docs/filing-and-pacing.md`,
phase 1 built `359cb96`; phase 2, a DOX-only pass, not yet done).
- AC-284 MET -- a reply's text frame no longer waits on the PRIOR
  sentence's audio. `test_speak_pacing.py`.
- AC-285 MET -- audio pacing and ordering are byte-identical to before.
- AC-286 MET -- a barge-in during the queued gap resolves per D7's ruling
  (2026-09-26).

**filing-and-pacing, section 8 addendum -- AC-287..AC-292** (2026-09-26,
after the owner reviewed Orca; built same day, `80effe1`/`90ebca5`).
- AC-287 MET -- `POST /api/session/new {project}` sets the project in the
  one call that mints the chat; absent births in the root row; an unknown
  id is a 400.
- AC-288 MET -- no route/method rewrites an existing record's `project`;
  `POST /api/project`, `Hub.switch_project`, `_Host.work_in`,
  `ChatStore.set_project` are deleted.
- AC-289 MET -- a spawned child's `cwd` is derived from the TURN's
  conversation's project (`_session_cwd`), not a stored "last switch".
- AC-290 MET -- "work in X"/"go to X" navigates to X's newest chat or
  births one there; never moves or stops anything; sends `{"type":
  "opened"}` to the speaking device.
- AC-291 MET -- the sidebar is a project tree; a project row only
  expands/collapses (`expanded_projects` in `ShortcutStore`); no "All
  chats" control.
- AC-292 MET -- the header names the current chat's project from the
  daemon's answer.
  Evidence: `test_chat_belongs_to_project.py` and its mutation runs,
  `test/AGENTS.md`'s browser-instrument record.

**lantern-ui -- AC-678..AC-712** (`docs/lantern-ui.md`, built 2026-10-27 on
master in Steps 1-7; owner rulings R1-R5 in its section 0). Status from the
commits and their checks, not the spec's prose.
- AC-678 MET -- `ui_style` lantern | caption, default lantern, written as
  `data-style` live (Step 3a, `test_settings_api.py`).
- AC-679 MET -- ids and Tab order identical across a live style switch
  (Step 4, e2e section N, `test_caption_style_page.py`).
- AC-680 MET -- only the style blocks declare the state colours, 3:1 on
  `--bg` / `--raised` (Steps 2-3a, `test_lantern_tokens.py`, `test_seams.py`).
- AC-681 MET -- `#liveLine` and `#wave` exist once; `placeByStyle()` is the
  only mover (Step 4).
- AC-682 MET -- the export's tokens verbatim except CU-17's four dark greys
  (Step 2, `test_lantern_tokens.py`).
- AC-683 MET -- header: panel icon, voice pill (two parts), panel toggle,
  "Recording room" only while recording (Step 3b).
- AC-684 MET -- every old control has a home; Settings in at most two
  presses (Step 3b, `probe_page.py` shell block).
- AC-685 MET -- `#burger` keeps one box open and collapsed (Step 3b).
- AC-686 MET -- the split pill and the voice popover's three switches
  (Step 3b, e2e K12-K16).
- AC-687 MET -- "Recording room" in both styles and widths (Step 3b).
- AC-688 PARTLY MET -- agent and model chips in the composer; the agent chip
  names what answers, not the harness ("pi"), which no quick setting serves.
- AC-689 MET -- live words in the box (Lantern, 3c) and the caption
  (Caption, Step 4), tentative dimmer, Lc >= 60.
- AC-690 MET -- `voice_effect: lantern`, the lit border from `--fx-level`
  (Step 3c, `probe_reduced_motion.py`).
- AC-691 MET -- Caption's seam is the one canvas in a seam slot (Step 4); it
  draws a line, not the board's bars.
- AC-692 MET -- the empty composer centred (2.9 % off at 1440), three
  suggestions, the wake phrase named (Step 3c).
- AC-693 MET -- question docked with numbered options, keys scoped to the
  dock (Step 3c).
- AC-694 MET -- the working row is the last item, carries `#stateDesc`, its
  Stop calls `stopTurn` (Step 3c).
- AC-695 MET -- the finished row from `progress` `agent` /
  `agent_elapsed_ms` and the record's `agent` / `agentElapsedMs` (Steps 1,
  3c).
- AC-696 PARTLY MET -- one line above the composer, Details opens
  Diagnostics, the draft survives; the copy says the draft STAYS in the box
  (nothing re-sends it), not the board's "sent when Butler is back".
- AC-697 MET -- replies in `--read`; `reading_face` overrides (Step 3a).
- AC-698 MET -- `theme: auto` follows the OS, `color-scheme` agrees (3a).
- AC-699 MET -- one right panel, Files | Library tabs, `right_panel` stored
  (Step 3b).
- AC-700..AC-705 MET -- the categorised sheet, signal-path strip, search
  across categories, docked restart bar, style preview cards, phone back
  stack (Step 5, `test_settings_sheet_page.py`). Supersede AC-83, AC-84,
  AC-88.
- AC-706 MET -- one overlay shape; popovers yield to a stop, modals take
  Escape whole (Steps 6a-6c; the 6a review fix).
- AC-707 MET -- every `hud()` slot inside the Diagnostics sheet (Step 3b).
- AC-708 MET -- the shortcuts sheet reads chords from `GET /api/shortcuts`
  (Step 3b).
- AC-709 MET -- library, tasks, files and rich-chat page tests pass with the
  served words (`nextWords`, `statusWord`, `whenWords`...) (Steps 6a-6c).
- AC-710 MET -- rendered APCA sweep, both styles and themes, 1440 and 390:
  text >= Lc 60 (sidebar names >= 75); inactive rows by design >= 45 (the
  stated exception); focus rings >= 3.71:1; reduced motion / transparency /
  more contrast handled (Step 7, `test_lantern_quality_page.py`).
- AC-711 MET -- the probes changed in the same commits as the markup, and
  exit 0 in both styles at every step.
- AC-712 MET -- `daemon/guide_features.py` rows for style, account menu,
  voice options, side panel, settings sheet, tasks page.

**Settings apply with no owner restart -- AC-713..AC-716** (owner,
2026-10-27: "option 3, hybrid"; built 2026-10-27, `daemon/apply_settings.py`,
`test/test_settings_apply.py`, D-13: `test/manual/mutate_settings_apply.py`,
13 of 13 caught). Route per setting by MEASURED cost (`docs/lessons/daemon.md`).
- AC-713 MET -- `asr_latency` and `endpointer` are swapped LIVE: the new engine
  is built from the current value, warmed, then assigned to the hub; the next
  capture opens it, a capture already open keeps its own, nothing restarts. A
  warm-up that fails is said and the running engine stays (D-1). Neither is
  restart-flagged.
- AC-714 MET -- the `endpointer` setting is READ (it never was: D-36's shape).
  `BUTLER_ENDPOINTER`, else the setting; a config `adapters.endpointer` is
  named loudly and loses.
- AC-715 MET -- a change to a restart-flagged setting (`asr_model`,
  `asr_backend`, `brain_mode`) restarts the daemon BY ITSELF once
  `Settings.pending()` has sat unchanged for `SETTLE_S` (5 s) and `Hub.busy()`
  is None: no turn in any session or orphaned, the recognizer not decoding, no
  coding agent mid-prompt (`_Host.busy`; a child held idle between turns does
  not count). A value put back owes nothing. Live: a capture held 7.5 s kept
  the pid; the restart followed the turn's end.
- AC-716 MET -- the daemon serves `pending` (restart-flagged keys whose value
  differs from what this process booted with) on `GET`/`POST /api/settings`
  and on the `settings` frame. The restart bar counts that list and says
  "Applying N change(s): Butler restarts when idle"; it has no button.
  OPEN: a turn that starts during the restart's own cleanup (about a second)
  is cut off; nothing refuses new turns once the restart has begun.

**Who may call Butler -- AC-720..AC-732** (owner, 2026-10-29: "option 2 and
3", "Both, LAN opt-in per machine"; `docs/remote-access.md`; built 2026-10-29,
`daemon/access.py`, `test/test_access_gate.py`, `test/test_lan_access.py`;
D-13: 29 mutations, each caught by its aimed test). Rolled out on the
desk, laptop and Mac mini 2026-10-29 (spec section 8).
- AC-720 MET -- one gate before every route, static file and `/ws`: a LAN
  peer with no device cookie reaches no handler but `access.EXEMPT`
  (enumerated from `app.router`).
- AC-721 MET -- LOCAL, and MAIN from loopback, reach every handler.
- AC-722 MET -- `/ws` from a LAN peer with no Origin is refused before
  `Hub.handle`.
- AC-723 MET -- foreign Origin, cross- or same-site fetch metadata, a
  hostname Host on plain HTTP, and a non-`.ts.net` Host on PROXIED are refused.
- AC-724 MET -- proxy headers on MAIN/LOCAL are refused.
- AC-725 MET -- on PROXIED only a `tailscale_owners` login is the owner.
- AC-726 MET -- blank `tailscale_owners` is this PC's login (cached 60 s); a
  tagged or unreadable status is nobody (503), never everybody.
- AC-727 MET -- an owner-minted 6-digit code pairs once, for 5 minutes,
  burning after 5 wrong guesses per peer or 20 in total.
- AC-728 MET -- `butler_device` is HttpOnly, Secure, SameSite=Strict;
  `devices.json` holds hashes only.
- AC-729 MET -- a DEVICE is refused `/api/devices`, settings reset and the
  owner keys, and the routes that make a standing channel (H2, owner
  2026-10-29); removing a device refuses it and closes its sockets.
- AC-730 MET -- `lan_access` decides MAIN's bind before binding (default
  127.0.0.1); a LAN bind without TLS refuses to boot; the settings write
  refuses `lan_access: true` with no certificate (AC-715 would restart into
  that refusal).
- AC-731 MET -- the exempt list is exactly RA-5; hooks keep their token.
- AC-732 MET -- Settings, Access holds both settings and the device block;
  `daemon/guide_features.py` names it.

**A project is never a whole disk -- AC-750..AC-753** (owner, 2026-10-29;
`docs/project-roots.md`; built 2026-10-29, mutations caught).
- AC-750 MET -- POST and DELETE `/api/shortcuts` are owner-only (PJ-1).
- AC-751 MET -- `upsert` refuses a drive root, a bare drive, a UNC share
  root, `/` and the home folder itself; a folder inside home is fine; the
  route answers 400 and writes no row.
- AC-752 MET -- a row already at a root loads, works, and is named once.
- AC-753 MET -- the chats guide row says what a project cannot be.
- AC-754 MET -- `POST /api/shortcuts` refuses a new or moved row whose path
  is not an absolute, existing folder on that machine (PJ-4, 2026-10-29: a
  `\Volumes\...` spelling was stored on the Mac mini); a rename is not checked.

**Chats and terminals on Claude Code and opencode -- AC-760..AC-773** (owner,
2026-10-29; `docs/chats-and-terminals.md`; terminal host ported from
an earlier prototype; mutation checks on the closed table, token hello, gate, the
one-terminal-event guard and the per-chat choice).
- AC-760 MET -- a terminal runs only a `TERMINAL_AGENTS` entry or a shell in
  the project row's folder; a refused request spawns nothing.
- AC-761 MET -- terminal routes and `/ws/terminal/{tid}` are owner-only.
- AC-762 MET -- output carries `seq`; a reattach replays with no gap.
- AC-763 MET -- a daemon restart keeps the host and shell PIDs; the page
  reattaches with its screen (real-Chrome scratch run).
- AC-764 MET -- the host's token never crosses the pipe (HMAC hello).
- AC-765 MET -- dictation into a focused terminal sends no newline.
- AC-766 MET -- terminal output never reaches speech.
- AC-767 MET -- xterm.js from `web/vendor/` only; the phone key row exists.
- AC-770 MET -- `harness_claude` and `harness_opencode` pass the harness
  conformance suite on recorded streams (live rows behind
  `BUTLER_LIVE_AGENTS=1`).
- AC-771 MET -- a chat keeps the agent it was born with; the page chip names
  it (`agent_label`, also AC-688).
- AC-772 MOOT -- OD-1: chats bypass permissions, so nothing asks.
- AC-773 MET -- the guide names terminals and the per-chat agent.

**Choosing a chat's agent from the page -- AC-774..AC-779** (owner,
2026-10-29: option C; `docs/chat-agent-picker.md`; mutations caught on the
409, the unknown-agent refusal, the birth record and the fixed flag; a
real-Chrome scratch run of 23 checks).
- AC-774 MET -- `POST /api/session/new` with `agent` births a chat on it and
  the first turn spawns it; an unknown agent is a 400, nothing minted.
- AC-775 MET -- an empty chat changes agent; after the first message, 409.
- AC-776 MET -- documents carry `agents`, `agent_default`, `agent_fixed`,
  `model` and `model_default` (`model_applies` was removed 2026-10-29: nothing
  read it); endpoint mode answers 404, so no picker.
- AC-777 MET -- the chip and the New chat chevrons render from the payload
  only (grep gate: no agent or setting name in the picker code).
- AC-778 MET -- both model chips hide on a chat the model setting does not
  govern.
- AC-779 MET -- the guide says how to start a chat on an agent.

**A model per chat -- AC-780..AC-788** (owner, 2026-10-29; `docs/chat-model.md`;
UX from a Fable consult and an earlier prototype; mutations caught on own-model-first,
the untouched setting, the agent-switch clear, the name check, the recents
order, the write, the voice prefix guard and the several-match refusal;
real-Chrome scratch run at desk and phone widths).
- AC-780 MET -- a chat's own model reaches its next spawn on any agent; ""
  clears it.
- AC-781 MET -- `harness_model` and other chats are untouched.
- AC-782 MET -- the model changes any time; the next turn's spec differs, so
  the held child is replaced on the same session (AC-60).
- AC-783 MET -- an empty chat's agent switch clears its model.
- AC-784 MET -- `GET /api/agents/{agent}/models` serves default, favourites,
  recent and the catalogue; picks land in recents (5, daemon-wide).
- AC-785 MET -- one composer chip `agent . model` opens one sheet (bottom
  sheet on a phone) with search; the old "who answers" pill hides in the
  harness modes (`answers: null`).
- AC-786 MET -- a model the catalogue lacks shows "(not listed)", kept.
- AC-787 MET -- "use <model>" / "back to the default model" by voice.
- AC-788 MET -- the guide explains the per-chat model.

**Chat agents and terminals are switched on in Settings -- AC-790..AC-798**
(owner, 2026-10-29; `docs/agent-and-terminal-settings.md`; mutations caught on
the offer filter, the route refusal, the always-allowed default, the
terminals gate, the switch-off, the owner key and shell-only).
- AC-790 MET -- `chat_agents` defaults to pi: one agent, no choice drawn.
- AC-791 MET -- a switched-off agent is a 400 at both routes; nothing minted.
- AC-792 MET -- the default agent is always allowed (by construction).
- AC-793 MET -- a chat on a switched-off agent keeps it.
- AC-794 MET -- `terminals_enabled` defaults off; routes and socket 404, no
  host started.
- AC-795 MET -- switching terminals off ends every open shell.
- AC-796 MET -- `terminals_enabled` is owner-only.
- AC-797 MET -- a terminal is the shell; "Open terminal" is one tap.
- AC-798 MET -- the guide names both switches.

**A turn starts where the wake word fired -- AC-800..AC-802**
(2026-10-29; `docs/wake-words.md` section 1; the Mac mini's iPhone trace:
every wake ended 156-361 ms in, `reason=silence`, empty. Mutations caught on
the voicing latch, the no-speech timer and the hub's boundary.)
- AC-800 MET -- voicing and the onset start only after the `lookback` bytes;
  the wake phrase and the wait for `capture_start` never end a turn.
- AC-801 MET -- a voiced turn under `min_speech_ms` still ends by
  `no_speech`, never at the absolute cap.
- AC-802 MET -- a `preroll` header without an integer `lookback` inside
  `bytes` is refused.

**Two wake words, one setting each -- AC-803..AC-809**
(2026-10-29; `docs/wake-words.md` section 2; daemon half in
`test/test_wake_words.py`; mutations caught on the off-word refusal and the
`new` birth.)
- AC-803 MET -- `wake_hey_butler` (default `screen`) and `wake_hey_jarvis`
  (default `off`) in Listening: `off`, `screen`, `new`; the words come from
  one catalogue, `settings.WAKE_WORDS`.
- AC-804 MET -- a save leaving every word off is refused (400 with the
  reason); the stored values are unchanged.
- AC-805 MET -- `ready.wake` and the `wake` broadcast (one builder,
  `Hub.wake_frame`) carry the words that are on, `wake_threshold` and
  `wake_cooldown_ms`; any of the four changing is broadcast.
- AC-806 MET -- the detector runs every loaded word; only the highest score
  over the threshold fires and is named; one shared refractory
  (`test/test_wake_detector.py`, node; mutations caught on max-wins and the
  shared refractory).
- AC-807 MET -- `wake_candidate` needs a known `word`; unknown or missing is
  `unknown_word`, off is `word_off`, both before any barge-in or
  arbitration. The winner's `new` births a conversation in the shown one's
  project and tells the device before the turn is minted, so
  `capture_start.chat` is the new id; a refused birth is `new_failed` and
  mints no turn.
- AC-808 MET -- in Chat only (`endpoint`), `new` acts as `screen` and its row is
  `inactive` with the reason, only while its value is `new`.
- AC-809 MET -- the hints, the wake switch's help and the threshold label are
  written from `ready.wake`; `web/` holds no phrase
  (`test/test_wake_words_page.py`). Not yet seen on a real iPhone.

**The settings sheet's words, daemon side -- AC-810..AC-814**
(owner-approved 2026-10-29; `test/test_settings_wording.py`; mutations caught
on the turn headline and on an unlabelled choice. Stored values unchanged.)
- AC-810 MET -- the `turn` group's headline is `turn_mode`, so the strip and
  the phone list show the mode that decides turns, not the advanced
  End-of-turn model.
- AC-811 MET -- every choice with declared values labels every value:
  `asr_model` "Parakeet (English, 0.6B)" / "Nemotron (multilingual, 0.6B)",
  `language` "" "Model default" and `auto` "Automatic" (locale codes label
  themselves, derived), `asr_latency` from the catalogue's own labels,
  `asr_backend`, `endpointer`, `voice_effect`, `turn_mode`, `patience`.
- AC-812 MET -- one turn-mode vocabulary: `turn_mode` Pause (`patient`),
  Smart, Long form; `patience` is "Pause length", Short / Medium / Long; the
  help, the guide and `Hub.TURN_MODE_SAID` use those words; "pause mode" was
  added to `SPOKEN_MODES` and every older phrase still works.
- AC-813 MET -- no label, help, blurb, inactive reason or turn-mode
  confirmation says "harness", a raw turn-mode or endpointer value, or a
  snake_case key; `brain_mode` reads Chat only / Chat + coding agent /
  Coding agent only and the `harness` group is "Coding agent". The guide
  table uses the same words.
- AC-814 MET -- an everyday setting's help opens with what it does for the
  owner; no measurement, date or criterion number in its first sentence.

**No separate vision model -- AC-815..AC-818**
(owner decision 2026-10-29: the answering model itself has eyes;
`docs/attachments.md` section 3; `test/test_attachments.py`,
`test/test_settings_api.py`; `mutate_attachments.py` row AC-815 caught.)
- AC-815 MET -- the `vision_model` setting (quick word "Eyes") and the
  resolver's `vision` role and `FALLS_BACK_TO` are removed; an image goes to
  the `reply` role in `brain_endpoint`. A stored `vision_model` is dropped at
  load by `_load`'s unknown-key line, said once; boot continues. Harness
  modes unchanged (the agent gets the attachment itself). Replaces AC-203.
- AC-816 MET -- AC-204 stays; `CANNOT_SEE` reads "The model this
  conversation uses cannot look at images. Choose one that can, under
  Brain."
- AC-817 MET -- `document_reading` keeps `text`, `text_then_vision`,
  `vision`; its help names the model and says looking needs a model that
  can see.
- AC-818 MET -- `guide_features.py`, `docs/attachments.md` and
  `docs/messaging.md` describe no vision model.

**The desk microphone per press -- AC-819..AC-823**
(owner decision 2026-10-29; `docs/desk-audio.md` D3 and section 5;
`test/test_desk_dictation.py`. Why: a microphone held permanently over Remote
Desktop stalls the RDP audio channel for every app; `docs/lessons/desk.md`.)
- AC-819 MET -- with `dictate_preroll_ms` 0, arming hotkeys opens no stream; a
  press opens it and the capture's end closes it. Above 0 unchanged.
  `audio.Microphone.holds_open` decides.
- AC-820 MET -- every ending path releases the device: key-up, stuck-key
  recovery, the ceiling (released when reached, while the key is still held),
  hotkeys off, a failed open, a `stop()` that raises.
- AC-821 MET -- an open that fails at the press is logged, cues failure,
  returns to idle, posts and pastes nothing, and the next press retries.
- AC-822 MET -- the default is 0 and the setting's help names both costs
  (clipped first word; Remote Desktop audio broken for every app).
- AC-823 MET -- per press, a capture with no live audio still says NO AUDIO and
  cues failure, but reopens nothing (the next press opens fresh).


### Phase 2 -- modes and workers

- **AC-8 (mode store is server-side and survives).** The current permission
  mode is readable at `GET /api/mode` and identical from two different
  origins/devices; it survives daemon restart; nothing about modes is stored
  in browser localStorage (grep gate on the client bundle). Status: MET
  early, ahead of its phase. `daemon/modes.py` stores the mode in
  `state/mode.json` with atomic replace-on-write, `GET /api/mode` serves it,
  and the grep gate over the shipped client files is a passing test. The
  preference store built alongside it (4.6a) is held to the same rule.
- **AC-9 (write confirmation).** In the default mode, an utterance that the
  Reflex tier classifies as a write/dispatch produces a spoken repeat-back
  ("You want me to X -- yes or no?") and NO worker process exists until an
  affirmative is heard. A "no", silence past the confirmation timeout, or an
  unparseable answer cancels: observable because no process spawned, and the
  cancellation is spoken. This is about Phase 2's WORKER dispatch and about
  nothing else. Harness mode built a version of it and the owner retired it
  on 2026-08-18 (AC-29), so today nothing anywhere speaks a repeat-back --
  the machinery, including the yes/no grammar, was deleted rather than left
  waiting for the Worker Manager to re-use.
- **AC-10 (escalation is asymmetric).** A spoken request to move to a MORE
  permissive mode is refused with a spoken explanation and the mode file is
  unchanged. A spoken request to move to a MORE restrictive mode takes
  effect immediately (mode file changes, confirmation spoken). Escalation
  succeeds only through the web UI click (Phase 2) or a
  speaker-verified spoken confirmation (Phase 4). Status: the UI half is
  DONE and tested -- `POST /api/mode` raises, `mode_request` over the
  WebSocket is refused. What is NOT done is the SPOKEN half: no utterance
  reaches the mode store at all, because the routing that would classify
  "switch to trusted mode" needs the Brain of step 7. The one spoken
  setting Butler honours today is the TURN mode, which is a preference and
  deliberately not part of this criterion (4.6a).
- **AC-11 (worker lifecycle end to end).** "Butler, have claude do X in
  project Y" (confirmed) spawns exactly one `claude` process with
  `--input-format stream-json --output-format stream-json --verbose
  --replay-user-messages`; tool-use events from its stdout appear as short
  spoken progress lines ("reading auth.ts"); a spoken steering instruction
  mid-run is written as one JSON user-message line to the worker's stdin
  without restarting the process; on completion -- as DEFINED in 4.5
  (stdin closed and the process exited), not the first Stop hook, which
  fires once per assistant response -- the daemon speaks a one-line summary
  that names the worker. The full transcript is on the web UI, not read
  aloud.
- **AC-12 (worker events are durable).** Kill the daemon while a worker
  runs; restart it; the worker's completion still becomes a spoken
  announcement, because hook events append to the worker's `events.jsonl`
  on disk (primary record) and the daemon reconciles from disk on boot; the
  POST is only the push notification.
- **AC-13 (narration never blocks the worker).** With TTS artificially
  stalled (test hook), the worker process still exits within 5 s of its
  natural end -- daemon drains worker stdout eagerly into its own buffer, so
  the known Claude Code behavior of waiting up to 30 s for a slow stdout
  consumer on exit [READ -- stated in the project brief] cannot trigger.
- **AC-14 (announcement policy).** A worker completing while the user is
  mid-conversation does NOT preempt. Three session states, three defined
  behaviors, all assertable from the ordered wire events: (a) SPEAKING --
  the announcement is queued and emitted after that turn's `speak_end`;
  (b) CAPTURING or THINKING -- queued and emitted after the resulting
  reply's `speak_end`; (c) WAKE-LISTENING with a satellite connected --
  emitted immediately. The load-bearing assertion is the ORDERING. Seam
  cues (a short tone before and after) apply to case (c) only and are
  cosmetic; "idle-but-live dialogue" was not a testable boundary and is
  replaced by this state list.
- **AC-14a (completion with nobody listening).** A worker that finishes
  while no satellite is connected does not lose its announcement: the
  terminal event is on disk, the announcement is held, and it is spoken
  ONCE -- not once per device -- when the next satellite reaches
  wake-listening. An announcement older than a stated age (default 1 hour)
  is dropped to the web UI with no speech, because a stale "done" spoken
  into an empty room the next morning is noise, not information.

### Phase 3 -- satellites

- **AC-15 (arbitration).** Two satellites report a wake candidate for the
  same utterance within the arbitration window; exactly ONE receives
  `capture_start`, the other receives `wake_reject`; the winner is the
  higher score, not the earlier report (test: lower score arrives first).
  The reply's TTS audio goes only to the winning device.
- **AC-16 (turn binding).** The device that wins the wake is the device the
  reply comes out of, even if another satellite connects mid-turn.
- **AC-17 (satellite drop mid-utterance).** Cutting a satellite's WS during
  capture ends that turn server-side within the liveness window, frees the
  capture claim, and a wake from another satellite works immediately. No
  daemon restart, no stuck "capturing" state.
- **AC-18 (remote TLS).** A phone on the tailnet loads the satellite page
  over HTTPS, gets mic permission, and completes a full turn. The wake
  model loads on iOS (onnxruntime-web pinned 1.18.0, `.wasm` served as
  `application/wasm` -- both load-bearing [MEASURED-SPIKE]).
  The SERVING half is built ahead of its phase (2026-08-18): `butlerd` runs
  https/wss on the same port from a self-signed cert whose SANs cover the LAN
  and tailnet addresses (`scripts/make-cert.py`), TLS is the default when a
  cert exists, and `start.bat`/`stop.bat` run it. The page already picks
  `wss:` from `https:`. What is UNPROVEN is the device half -- iOS mic
  permission on a self-signed cert, the wake model over the tailnet, a full
  turn -- which still needs a real phone, and the cert being self-signed means
  each device accepts a warning once (or trusts the cert) before the mic works.
- **AC-19 (iOS suspend recovery).** After the phone backgrounds and returns,
  the satellite recovers the mic without a page reload: AudioContext resume
  on `statechange` AND on `visibilitychange` (the iOS `interrupted` state
  never recovers without this -- ported from an earlier prototype's `useAudioSession.ts`).
- **AC-20 (the iOS lock-screen experiment ran).** The decisive test in
  section 8.1 has been executed on the owner's actual phone and its result
  -- either way -- is recorded in this repo. The criterion is that the
  answer is KNOWN, not that it is favorable; the architecture does not
  depend on it.

### Phase 4 -- speaker verification and voice escalation

- **AC-21 (enrollment).** The web UI records N enrollment utterances,
  produces a stored ECAPA-TDNN 192-dim voiceprint, and shows the
  owner-vs-impostor score separation on a held-out enrollment clip.
- **AC-22 (TV rejection).** Wake-word audio + command audio that fails
  verification is refused for anything above the current mode's floor: the
  turn is answered read-only or refused with a spoken "I don't recognize
  your voice", and no write/dispatch/escalation occurs. Test uses recorded
  non-owner audio. Verification always scores wake word PLUS following
  command audio -- 0.6 s of wake word alone is below the reliable floor
  [DECIDED].
- **AC-23 (voice escalation).** With verification passing, the spoken
  escalation flow (request, spoken challenge, verified confirmation) moves
  the mode up; the same flow with failing verification refuses and the mode
  file is unchanged.

### Phase 5 -- native presence

- **AC-24 (native mic source).** The daemon captures the desk microphone
  natively (no browser page open anywhere) and a full turn completes.
  Proves the source abstraction: the native source and the WS source drive
  the identical downstream pipeline (same tests pass against both).
- **AC-25 (autostart).** After a reboot with nobody logged into a browser,
  the wake word works on the desk mic. Task Scheduler / startup entry
  observable; the acceptance test is a post-reboot manual check recorded in
  the repo.
- **AC-26 (push-to-talk).** A global hotkey opens a capture without the
  wake word, from any foreground app.

---

## 3. Gherkin scenarios

### 3.1 Wake and reply on one device (AC-1, AC-3)

```gherkin
Feature: Hands-free turn on a single device
  Scenario: Trivial question answered by the Reflex tier
    Given the daemon is running with STT, smart-turn, and Kokoro warm
    And one satellite is connected and in wake-listening
    When the satellite reports a wake candidate above threshold
    And the user says "what time is it" followed by silence
    Then the satellite receives streaming partials while the user speaks
    And smart-turn ends the turn without a fixed silence timeout
    And the reply is produced by the Reflex tier (no Conversation call)
    And Kokoro audio for the reply plays on that satellite
    And the session returns to wake-listening

  Scenario: Substantive question escalates to the Conversation tier
    Given the same setup
    When the user asks "talk me through whether Butler should use
         a message bus or direct calls"
    Then the Reflex tier classifies the utterance as "converse"
    And the Conversation tier streams the reply
    And sentences are spoken as they complete, through the same TTS path,
        with no audible seam between tiers
```

### 3.2 Two satellites hear the same wake word (AC-15, AC-16)

```gherkin
Feature: Device arbitration
  Scenario: Highest score wins, not first to report
    Given satellites "desk" and "phone" are both connected
    When "phone" reports a wake candidate with score 0.61 at t=0ms
    And "desk" reports a wake candidate with score 0.88 at t=180ms
    Then after the arbitration window closes, "desk" receives capture_start
    And "phone" receives wake_reject and stays in wake-listening
    And the eventual reply audio is sent only to "desk"

  Scenario: Single candidate proceeds without waiting the full window
    Given only "desk" is connected
    When "desk" reports a wake candidate
    Then capture_start is sent after the window elapses with no rival
    And the added latency is bounded by the window length

  Scenario: The window costs latency but never audio
    Given "desk" reports a wake candidate at t=0
    And the user begins speaking the command at t=150ms
    When capture_start arrives at t=340ms
    Then "desk" flushes its pre-roll buffer before any live frame
    And the final transcript contains the first word of the command
    # Without the flush this word is unrecoverable: nothing streams while
    # resting, and PCM is illegal before capture_start (AC-1a, 4.2).
```

### 3.3 A write is refused until confirmed (AC-9)

Phase 2's WORKER dispatch, and nothing that runs today: the harness reaches a
coding agent with no ceremony at all since the owner retired AC-29 on
2026-08-18, so nothing in Butler currently speaks a repeat-back and the
machinery below has to be built rather than re-enabled.

```gherkin
Feature: Confirm-all-writes in the default mode
  Scenario: Spoken repeat-back gates the action
    Given the mode is "secretary" (default)
    When the user says "delete the old logs folder in project butler"
    Then Butler speaks a repeat-back naming the exact action and target
    And no process has been spawned and no file has changed
    When the user says "yes"
    Then the action proceeds and its start is spoken

  Scenario: Silence cancels, fail closed
    Given a repeat-back has been spoken
    When the confirmation window passes with no affirmative
    Then Butler speaks that the action was cancelled
    And no process has been spawned and no file has changed
    And the session returns to wake-listening

  Scenario: An ambiguous answer does not count as yes
    Given a repeat-back has been spoken
    When the user says "hmm, maybe the other folder"
    Then the action is cancelled and Butler asks one clarifying question
```

### 3.4 Mode escalation by voice is refused (AC-10)

```gherkin
Feature: Asymmetric mode changes
  Scenario: More permissive by voice is refused
    Given the mode is "secretary"
    When the user says "switch to trusted mode"
    Then Butler refuses aloud, names the web UI as the way to escalate
    And GET /api/mode still returns "secretary"

  Scenario: More restrictive by voice is instant
    Given the mode is "trusted"
    When the user says "lock down" from ANY connected satellite
    Then the mode becomes "paranoid" immediately and is confirmed aloud
    And GET /api/mode returns "paranoid" from every origin

  Scenario: A podcast says the dangerous sentence
    Given the mode is "secretary" and the room TV is playing speech
    When the TV audio contains "switch to trusted mode"
    Then the mode is unchanged
    # Phase 2: refused because voice escalation is refused for everyone.
    # Phase 4: additionally refused because speaker verification fails.
```

### 3.5 Worker dispatched, narrated, steered, completed (AC-11, AC-13, AC-14)

```gherkin
Feature: Coding-agent worker lifecycle
  Scenario: Dispatch through completion
    Given the mode permits dispatch after confirmation
    When the user says "have claude add a retry to the fetch in butler
         and run the tests" and confirms the repeat-back
    Then one claude process starts with stream-json stdin/stdout
    And tool-use events surface as short spoken lines ("reading fetch.py",
        "running tests"), keyed on tool events, never token deltas
    When the user says "Butler, tell it to also update the README"
    Then the Reflex tier routes this as "steer" naming the running worker
    And one JSON user-message line is written to the worker's stdin
    And the worker continues without restarting
    When the worker's Stop hook fires
    Then the hook appends to the worker's events.jsonl and POSTs the daemon
    And nothing is announced yet -- Stop is a response boundary, not the end
    When the idle grace expires with no steering message pending
    Then the daemon closes the worker's stdin and the process exits
    And Butler speaks one completion line naming the worker
    And the full result is on the web UI with a spoken pointer to it

  Scenario: Completion while the user is talking waits its turn
    Given a worker finishes while a conversation reply is being spoken
    Then the announcement is queued
    And it plays after that turn's speak_end, with no seam cues
    # Seam cues belong to the wake-listening case only (AC-14 state (c)).

  Scenario: A stalled TTS queue cannot hold the worker hostage
    Given TTS playback is stalled by the test hook
    When the worker reaches its natural end
    Then the worker process exits within 5 seconds
    Because the daemon drains worker stdout into its own buffer eagerly
```

### 3.6 Satellite drops mid-utterance (AC-17)

```gherkin
Feature: Capture liveness
  Scenario: The capturing device disappears
    Given "phone" won the wake and is streaming capture audio
    When the phone's WebSocket drops mid-sentence
    Then the daemon ends that turn within the liveness window
    And the partial transcript is discarded, not acted upon
    And the capture claim is released
    When "desk" reports a wake candidate
    Then a new turn proceeds normally on "desk"
```

### 3.7 A television wakes Butler and is rejected (AC-22)

```gherkin
Feature: Speaker verification gates non-owner speech
  Scenario: TV fires the wake word
    Given speaker verification is enrolled and enabled (Phase 4)
    And the mode is "secretary"
    When wake fires from audio whose ECAPA score is below the owner threshold
    And the following command audio also scores below threshold
    Then any write, dispatch, or mode change is refused
    And Butler either answers read-only or says it does not recognize
        the voice (per mode policy)
    And the refusal and both scores are visible in the daemon log
```

### 3.8 Edge and error cases

```gherkin
  Scenario: Stale reply is never spoken (AC-5)
    Given the user barged in during "thinking" and a new turn started
    When the abandoned turn's reply arrives from the Conversation tier
    Then it is discarded by turn id and nothing is spoken

  Scenario: Reflex output fails to parse (AC-2)
    Given the Reflex model returns text that is not the required JSON verdict
    Then the turn routes to the Conversation tier by defined policy
    And the parse failure is logged loudly with the raw output
    # Deliberate, stated exception to fail-fast: the verdict is MODEL output,
    # not our input; losing the user's turn is the worse failure. Same
    # reasoning an earlier prototype recorded for goal-mode handoffs (2026-07-11).

  Scenario: TTS worker dies mid-reply (AC-2)
    Given Kokoro fails on a sentence twice (once on a fresh worker)
    Then a spoken/audible error marker is emitted, the remainder of that
         reply is abandoned loudly (never silently skipped)
    And the next turn's TTS works (worker respawned)

  Scenario: Empty capture (AC-6)
    Given wake fired and the user says nothing
    Then a final with empty text is emitted at the no-speech timeout
    And the session re-arms; a wake in the following second is honored

  Scenario: Daemon restart with a live worker (AC-12)
    Given a worker is running and the daemon is killed
    When the daemon restarts
    Then it reconciles the worker registry from disk
    And the worker's later completion is announced normally
```

---

## 4. Architecture

### 4.1 Components

```
                                  E:\projects\butler
+---------------------------------------------------------------------+
|  butlerd  (Python 3.11+, asyncio, aiohttp; ONE process owns policy) |
|                                                                     |
|  +--------------+   +-----------+   +-----------------------------+ |
|  | Source Hub   |   | Turn      |   | Dialogue Manager            | |
|  |  WS sources  |-->| Pipeline  |-->|  Router      (local LLM)    | |
|  |  native mic  |   |  STT      |   |  endpoint OR harness (4.4)  | |
|  |  (Phase 5)   |   |  SmartTurn|   |  Dispatch -> Worker Manager | |
|  +--------------+   +-----------+   +-----------------------------+ |
|        |                  |                        |                | |
|  +-----v------+   +-------v-------+   +------------v-------------+  |
|  | Wake       |   | Speech Output |   | Worker Manager           |  |
|  | Arbiter    |   |  TTS queue    |   |  spawn/steer stream-json |  |
|  | (300ms win)|   |  announcer    |   |  registry on disk        |  |
|  +------------+   +---------------+   |  hook receiver (HTTP)    |  |
|                                       +--------------------------+  |
|  Mode Store (server-side file)   Speaker Verify (Phase 4, child)   |
+---------------------------------------------------------------------+
   |                |                 |                    |
   | child process  | child process   | sidecar process    | child processes
   v                v                 v                    v
 SmartTurn        SpeakerVerify     Kokoro TTS         claude / pi / codex
 (onnx, BLAS=1)   (ECAPA, BLAS=1)   (node, kokoro-js)  (harness CLIs)

 Satellites (browser page over WSS): desk Chrome, phone Brave/Safari, iPad.
 Each runs: mic worklet + local wake detector (ort 1.18.0 wasm) + TTS
 playback worklet + HUD. The page IS the web UI and the satellite.
```

The boxes above are wired together through four of the five named ports --
`Source`, `Asr`, `Tts`, `Brain` -- defined in 4.10. The fifth, `Harness`, is
not in this picture: it is consumed by a brain adapter (4.4c) and by the
Worker Manager (4.5), never by the hub. Read that section before writing
any of these modules: it is what keeps the speech engines and the agentic
middle replaceable, and it constrains what the Dialogue Manager is allowed
to do on its own.

Process layout is a hard rule, measured not aesthetic: Smart Turn in a
thread pool took ~1960 ms per check; in a child process 292 ms; child
process + single-threaded BLAS 33 ms [MEASURED-SPIKE]. The GIL is the first
cliff (numpy feature code re-acquires it constantly and loses to the STT
worker thread), threaded BLAS oversubscribing ggml's workers is the second.
Anything numpy/onnx-heavy that runs per-turn goes in its own child process
with `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`
set before numpy import. Speaker verification inherits this rule by design
[TARGET -- its own numbers must be measured when built].

STT stays in-daemon on a worker thread exactly as the spike has it
(transcribe.cpp releases the GIL for its heavy work; the spike proved 198 ms
first partial with this layout [MEASURED-SPIKE]).

### 4.2 Audio path

One turn, end to end:

```
satellite mic (48k) -> AudioWorklet downsample -> 16k int16 PCM frames
  -> [wake-listening] frames feed the LOCAL wake detector in the page;
     nothing is streamed to the daemon while resting  (bandwidth + privacy)
  -> wake fires locally -> the satellite immediately STARTS BUFFERING mic
     frames into a pre-roll ring (reaching ~300 ms BEFORE the fire, so the
     tail of the wake phrase is retained for Phase 4 verification) and
     sends {wake_candidate, score}
  -> daemon Wake Arbiter opens a ~300 ms window [DECIDED], collects rival
     candidates, picks the HIGHEST SCORE (not first-to-report -- the
     Home Assistant failure mode), sends capture_start to the winner
  -> winner FLUSHES its pre-roll buffer as the first bytes of the stream,
     then continues with live binary PCM frames over the same WS. Losers
     discard their buffer on wake_reject. This flush is not an optimisation:
     nothing streams while resting and PCM is illegal before capture_start,
     so without it the arbitration window plus the round trip would eat the
     first syllables of every command -- nobody pauses after a wake word
     (AC-1a). The spike never hit this because wake and capture lived in
     the same page with no round trip between them.
  -> daemon: frames tee into (a) transcribe.cpp streaming session
     (partials out) and (b) the rolling utterance buffer for Smart Turn
     (last 9 s of int16, exactly the spike's UTT_MAX_BYTES shape)
  -> endpointing: policy AND checks both live in the daemon. DONE: the
     energy gate is daemon\pipeline.py and the three end-of-turn policies
     are daemon\endpointing.py, chosen per turn by the `turn_mode` setting
     (see the list below the diagram). In smart mode the constants are the
     spike's -- first check after 280 ms of silence, recheck every 450 ms,
     finalize at p >= 0.6, silence caps so a turn ALWAYS ends
     [MEASURED-SPIKE tuning]. The spike authored all of this in the page
     (web/app.js EOU_* and the ON/OFF/HANG_MS energy gate); a native mic
     source (Phase 5) has no page to author them, and AC-24 promises that
     both sources drive the identical pipeline -- leaving the policy in the
     client would have made AC-24 a rewrite discovered four phases late.
     Price paid for the move, as predicted: the daemon runs the energy VAD
     on incoming frames itself
  -> while the turn is closing the daemon sends ONE `closing` message per
     pause (AC-6c); continuing to speak cancels it and earns a fresh one
  -> final text, with any end phrase stripped off it (AC-6b)
  -> Brain -> reply text (streamed). TODAY this is the scripted FakeBrain:
     the Dialogue Manager of step 7 is not written, so every reply below
     this line comes from five substring rules and one fallback
  -> sentence splitter -> Kokoro sidecar -> 24 kHz PCM chunks
  -> binary TTS frames to the BOUND device only -> satellite playback
     worklet (queue-fed, self-healing on underrun) -> speak_end
  -> re-arm: every terminal path returns to wake-listening (invariant)
```

**There are THREE end-of-turn policies, not one, because "when should it
stop listening" is not one question with one right answer.** They live in
`daemon\endpointing.py` behind one `EndPolicy` protocol, and the
server-side `turn_mode` setting picks which one a turn gets. A fresh
instance is built for every turn: `SmartTurnEnd` remembers when it last
asked the model and what it heard, and `EndWordEnd` matches a transcript
that only exists inside one turn, so reusing one instance would leak the
previous turn's answer into this one's first decision.

- **`smart`** -- the model decides, with silence only as the cue to ask it.
  Trained to tell "trailing off with an um, still thinking" from "finished
  a sentence", which is the actual fix for being cut off. A longer timer is
  the crude version of the same goal and makes every quick turn slow too.
- **`patient`** -- plain silence, with presets named for how they feel
  rather than what they measure: quick (400 ms silence / 4 s cap), balanced
  (600 / 8000), patient (800 / 15000). The fallback when the model is wrong
  or switched off. Naming them by feel rather than by number is what made
  them usable in the owner's previous project.
- **`endword`** -- no timer at all. Talk, pause, think, resume; nothing is
  submitted until an end phrase is said and a 600 ms pause follows. Default
  phrases are deliberately awkward -- "over to you", "send it", "go ahead",
  "over" -- because a natural one ("done", "okay") submits a half-finished
  thought the first time it is used in passing. The phrase must sit at the
  END of what has been said, so "send it back to the old version" keeps
  listening, and it is STRIPPED before the text goes anywhere (AC-6b): it
  was addressed to Butler, not part of the instruction. This is the mode
  for composing a real instruction for a coding agent, which is the job
  Butler exists to do.

The policies do NOT own the safety nets. The no-speech timeout and the
absolute ceiling stay in `pipeline.py` and apply to every mode, so a policy
bug cannot hold the microphone open (D-1). Selecting a mode by voice is
handled by the DAEMON and never reaches the Brain: it is a preference, not
something to reason about, and routing it to a model would make a
deterministic setting depend on a model's mood. The match is against the
WHOLE utterance, so "we should switch to long form later" is a sentence
about the mode rather than a command to change it.

**The endpoint policy in full, with the corrections the prior-art review
forced.** The safety nets are `daemon\pipeline.py` and the smart-mode
constants are `daemon\endpointing.py`; every clause below was wrong in the
first port of it:

- **Both caps are measured against accumulated SILENCE, never against
  elapsed turn time.** The 6 s hard cap is "we have waited 6 s for a verdict
  since you stopped", not "you have been talking for 6 s". The spike had
  this right (`static/app.js:198`, `silenceMs >= EOU_MAX_WAIT_MS`) and the
  first Butler port capped elapsed time instead, which force-finalized every
  utterance past 6 s of speech, mid-sentence. Three of the six review
  documents flagged it independently; every comparable system caps trailing
  silence (pipecat `stop_secs`, LiveKit `max_endpointing_delay`, AssemblyAI
  `max_turn_silence`) [REVIEW audio-internals.md]. Someone who is still
  talking is never interrupted by a timer.
- **A separate 120 s absolute ceiling exists as a safety net, not a policy.**
  Nothing should reach it. It is there so "a turn ALWAYS ends" survives even
  if the energy gate latches voiced and the silence cap can therefore never
  fire -- the failure it insures against is a leaked capture claim that
  refuses every later wake as busy. Generous on purpose: cutting someone off
  is the worse failure of the two. KNOWN GAP, stated rather than left to be
  discovered: it applies to `endword` as well, where nothing else ends a
  turn, so a long-form dictation is cut off at 120 s by a net meant for a
  latched gate. `endpointing.END_WORD_MAX_MS` (300 s) is defined and never
  read by anything. Either wire it for that mode or delete it -- a constant
  that documents a behaviour the code does not have is D-12.
- **The endpointer call is bounded by a 2 s timeout.** It is a child process
  doing numpy and ONNX work under GPU contention, which makes it the most
  likely thing in this loop to wedge, and awaiting it unbounded blocked the
  poll loop so that neither cap was ever re-evaluated -- the invariant was
  simply false while the child was stuck. A timeout degrades a wedged child
  to "no verdict", and silence still ends the turn.
- **The energy-gate thresholds are FLOORS that adapt upward to the room.**
  The satellite's getUserMedia constraints set `echoCancellation` and
  `noiseSuppression` and say nothing about `autoGainControl`, which leaves it
  ON by default, so a noisy room's amplified noise floor can sit permanently
  above an absolute RMS_OFF. Then silence never accumulates, no endpoint
  check ever runs, and -- once the cap was correctly keyed to silence -- the
  turn survives to the 120 s ceiling with the microphone open. The
  thresholds therefore track the quietest thing recently heard and rise with
  the room. They only ever rise above the tuned constants: dropping below
  them in a silent room would make the gate fire on the noise of the
  microphone itself.
- **With no endpointer wired the silence floor is 700 ms, not 280 ms.**
  280 ms is the cadence at which a MODEL gets asked; it is not evidence on
  its own. With nothing to ask, ending at 280 ms of silence cuts people off
  mid-thought, and the field's floor for pure-silence endpointing is
  500-900 ms [REVIEW audio-internals.md]. Since 2026-08-16 butlerd wires the
  real endpointer and warms it at boot, so this branch is what a pipeline
  built without one runs -- the tests, and a daemon started with the
  endpointer named `none`.
- **The smart-turn window is anchored at speech onset minus 500 ms, not at
  the start of the buffer.** The buffer starts with the pre-roll, which
  carries the tail of the wake phrase, and a completed phrase sitting in
  front of the command biases the model toward "they are done" -- the
  direction that cuts the user off. Upstream anchors at
  `speech onset - pre_speech_ms` with `pre_speech_ms = 500` since pipecat
  0.0.99, and smart-turn issue #12 measured identical speech swinging from
  p=0.012 to over 0.99 purely on what preceded it. The pipeline records where
  voicing began (skipping the `lookback` bytes the `preroll` header declares, AC-800) and
  the policy slices from there; a source with no pre-roll, and any caller
  that reports no onset, keeps the whole buffer.
- **The endpointer is wired, and it is a child process on purpose.**
  `daemon\adapters\endpointer_smartturn.py` wraps the forked
  `engines\turn_engine.py` and is NOT a port at all -- it is one async
  callable `endpointer(pcm) -> P(complete)` that the Hub holds. Every score
  goes through the BLAS-capped child pool, never a thread and never the
  loop, because in-process feature extraction degrades from ~30 ms to
  ~2200 ms against transcribe.cpp's GIL. One inference at a time behind a
  lock, so the policy's 2 s timeout is measured against the model rather
  than against a queue. Measured on this machine 2026-08-16 against the
  real fixtures: p50 31 ms / p95 35 ms wall (6 ms features, 25 ms ONNX),
  boot warm 498 ms, and 434 ms from end of speech to `final` over a real
  WebSocket. Failure is loud in both directions: if `turn_mode` is `smart`
  and the model will not load the daemon REFUSES TO BOOT, because booting
  into patient silence while every UI still reports "smart" is the hardest
  version of this to diagnose; if the setting is anything else, a broken
  endpointer warns and is dropped, so switching to smart later still
  refuses out loud instead of silently degrading.

STT streaming configuration: parakeet-unified buffered streaming at
(70,7,7) = 560 ms chunks. The (70,2,2) 160 ms configuration was ~37x
realtime overhead and fell behind under GPU contention [MEASURED-PROTOTYPE].
`parakeet-unified-en-0.6b` for English; `nemotron-3.5-asr-streaming-0.6b`
for other languages, which REQUIRES a concrete locale -- `auto` is rejected
on the streaming path [MEASURED-SPIKE]. Capabilities are read from the
loaded GGUF (`model.capabilities`), never hard-coded per model (Handy's
pattern, already in stt_engine.py).

The models are pre-warmed at daemon start: the smart-turn child scores a
zero buffer at boot (spike does this); Butler additionally feeds one dummy
streaming turn through transcribe.cpp and synthesizes one silent sentence
through Kokoro at boot, because an earlier prototype observed a cold start measured in
minutes when the streaming graph warms up mid-conversation
(owner-reported ~2 min, cause hypothesized as first GGUF load + stream
graph warmup, never fully profiled [MEASURED-PROTOTYPE, partially]).

Echo: satellites request `echoCancellation: true, noiseSuppression: true`
on getUserMedia (the spike's choice for a conversational mic; note this is
the OPPOSITE of an earlier prototype's room-capture recorder, which wants EC off --
different job, different setting). During SPEAK, only the wake word may
interrupt; energy barge-in is not used in wake mode, so the reply's own
LOUDNESS cannot self-trigger. Its WORDS still can, and echo cancellation is
not a defence we are entitled to assume: the spec only obliges
`echoCancellation` to match `remote-only`, Chrome has historically excluded
`AudioContext` output from the canceller's reference signal, and the
cross-device case (the phone hearing the desk speaker) has no reference
signal anywhere and is uncancellable by any AEC [REVIEW audio-internals.md].
What actually holds the line today is the playback gate in 4.3 -- the
detector does not come back until the speaker is quiet -- plus AC-5a's
negative test. Making the raised bar server-side policy for ALL sessions
while any session is SPEAKING is R6, and is not done.

The other half of that boundary is the wake cooldown, and it was WRONG:
700 ms, which is this plan's own recorded number (D-14, ~1.28 s of context
refill) contradicting the code. The detector scores 16 embeddings of 80 ms,
so its context spans 1280 ms; re-enabling firing at 700 ms left nearly half
that window still holding the tail of Butler's own reply. It is now
1300 ms, derived rather than felt: the smallest round value that guarantees
the context has refilled entirely with audio from after the speaker went
quiet. The detector is still never RESET -- resetting clears the context
and the next wake is deaf -- only its firing is suppressed.

### 4.3 Daemon-satellite protocol

One WSS connection per satellite. Text frames are JSON; binary frames are
raw 16 kHz mono int16 PCM (mic upstream) or 24 kHz mono int16 PCM (TTS
downstream, direction disambiguates). Versioned from day one.

```
satellite -> daemon
  {type:"hello", proto:1, device_id, name, caps:{wake:bool, tts:bool}}
  {type:"wake_candidate", score, ts_ms, word}    # local detector fired; `word`
                                                 # is a `ready.wake` id, required:
                                                 # unknown or missing is refused
                                                 # `unknown_word`, an off word
                                                 # `word_off`, before any barge-in
                                                 # (AC-807)
  {type:"preroll", turn, bytes, ms, lookback}    # header for the back-fill flush;
                                                 # lookback = bytes captured BEFORE the
                                                 # wake fired, required (AC-802)
  {type:"capture_end_hint"}                       # optional: user tapped stop
  {type:"playback_done", turn}                    # MY SPEAKER IS QUIET NOW
  {type:"mode_request", to}                       # subject to asymmetry rule
  {type:"stop", turn?, chat}                      # end the turn of the chat ON
                                                  # SCREEN: with `turn`, only if
                                                  # it is owned here AND in
                                                  # `chat`; with `chat` alone
                                                  # (a reloaded page), the turn
                                                  # in flight there, wherever
                                                  # owned (e2e audit 2026-09-26)
  {type:"typed", text, corrects?}                 # the owner TYPED a question;
                                                  # `corrects` names a turn THIS
                                                  # socket was told about (AC-97)
  {type:"showing", chat}                          # which conversation is on
                                                  # THIS device's screen, or
                                                  # null for none (AC-238,
                                                  # AC-247). Per socket, never
                                                  # persisted, re-sent on every
                                                  # reconnect; it is what makes
                                                  # "a device speaks the
                                                  # conversation it is showing"
                                                  # a mechanism rather than a
                                                  # ruling
  {type:"caps", caps:{wake:bool, tts:bool}}       # what the page can do NOW,
                                                  # re-sent when the mic arms
                                                  # or stands down and when
                                                  # the speaker opens: `hello`
                                                  # goes once at boot, before
                                                  # either. The announcer reads
                                                  # it (docs/automations.md
                                                  # AC-470)
  {type:"pong", ts_ms}
  binary: mic PCM -- the pre-roll flush first (length declared by the
          preroll header), then live frames; both only between
          capture_start and final/reset

  There is no check_turn: endpointing is server-side (4.2). The spike's
  client-authored version is deliberately not carried over.

daemon -> satellite
  {type:"ready", stt:{...}, tts:{...}, brain:{...}, session, mode, proto:1,
                 device_id, turn_mode, capture_ceiling_ms,
                 wake:{words:[{id, phrase, url}],
                       threshold, cooldown_ms},
                 chat}                            # `chat` is the conversation
                                                  # this device's next word
                                                  # would go to (AC-246), "" if
                                                  # this daemon holds none.
                                                  # `capture_ceiling_ms` is the
                                                  # live safety-net ceiling one
                                                  # capture may run to under
                                                  # the CURRENT turn_mode
                                                  # (AC-92's other half); the
                                                  # page's own capture
                                                  # watchdog tracks this number
                                                  # instead of a flat guess.
                                                  # `wake` is what the detector
                                                  # loads: the words not off,
                                                  # in catalogue order, `url`
                                                  # page-relative (AC-805)
  {type:"capture_start", turn, chat}              # you won arbitration; `chat`
                                                  # names the conversation this
                                                  # spoken turn is in (AC-246)
  {type:"wake_reject", reason}                   # lost_arbitration|unknown_word|
                                                  # word_off|new_failed (a `new`
                                                  # word whose birth was refused;
                                                  # no turn minted, AC-807)
  {type:"partial", committed, tentative, turn}
  {type:"closing", turn}                          # ONCE per pause (AC-6c)
  {type:"final", text, turn, reason, lang, corrects, timings:{first_partial_ms, finalize_ms}}
  {type:"reply", text, turn, tier, spoken}        # for transcript display
  {type:"unspoken", turn, chat, from}             # from reply frame `from` on
                                                  # (0-based, this turn) the
                                                  # text was SHOWN, NEVER HEARD
                                                  # (a stop, barge-in, dead
                                                  # segment, look-away); sent
                                                  # once at turn end. Text is
                                                  # not gated on synthesis
                                                  # (filing-and-pacing item 2)
  {type:"speak_start", turn|announce_id, voice, lang}  # brackets the TURN
  binary: TTS PCM chunks
  {type:"speak_end", turn|announce_id, truncated} # NO MORE AUDIO IS COMING
  {type:"mode", mode}                             # broadcast on every change
  {type:"wake", words, threshold, cooldown_ms}   # broadcast when a wake word,
                                                  # wake_threshold or
                                                  # wake_cooldown_ms changes;
                                                  # the shape of `ready.wake`
                                                  # (AC-805)
  {type:"turn_mode", turn_mode, capture_ceiling_ms}   # broadcast on every
                                                  # change; ceiling as above
  {type:"settings", changed:{...}, turn_mode,
                    capture_ceiling_ms, pending}  # broadcast on every change;
                                                  # pending: restart-flagged
                                                  # keys not applied yet
                                                  # (AC-716)
  {type:"question", chat, qid, method, title, message, options, placeholder,
                    prefill, deadline_ms}         # BROADCAST: a coding agent
                                                  # is blocked on a human
                                                  # (AC-234). `qid` is the
                                                  # harness's own id; the
                                                  # answer goes back through
                                                  # POST /api/answer
  {type:"question_closed", chat, qid, how}        # answered|cancelled|
                                                  # expired|agent_gone
  {type:"opened", chat}                           # to the SPEAKING device
                                                  # only: the daemon moved it
                                                  # to `chat` ("work in X",
                                                  # "new conversation"); the
                                                  # page re-reads
                                                  # /api/session/current
                                                  # (docs/filing-and-pacing.md
                                                  # 8, D-C2, AC-290); also
                                                  # before a spoken "run X",
                                                  # whose turn's `final`
                                                  # carries reason "recipe"
                                                  # (docs/automations.md 8)
  {type:"library", what, project}                 # BROADCAST: recipes|tasks|
                                                  # automations (a row or its
                                                  # history, phase 5)
                                                  # changed in `project` (""
                                                  # = the Global section); a
                                                  # hint to re-read, never the
                                                  # data (docs/automations.md
                                                  # 5, a factual correction,
                                                  # not an accepted criterion)
  {type:"worker_event", chat, kind, detail,
                        elapsed_ms}               # a coding agent's own
                                                  # activity, between the
                                                  # narration line and the
                                                  # answer (docs/parallel-
                                                  # agents.md phase 7,
                                                  # AC-233, AC-235). `kind` is
                                                  # "started" (once, right
                                                  # after the child is up) or
                                                  # "tool_start"/"tool_end";
                                                  # `detail` is the tool name,
                                                  # blank for "started".
                                                  # `elapsed_ms` is stamped by
                                                  # the hub from the turn's
                                                  # own start, never guessed
                                                  # by a page (AC-236). Routed
                                                  # through send_for_turn, not
                                                  # broadcast -- see `question`
                                                  # above for the difference
  {type:"progress", turn, chat, elapsed_ms, phase,
   detail, chars, thought_chars, last_event_ms,
   agent?, agent_elapsed_ms?}                     # the turn's HEARTBEAT
                                                  # (docs/turn-feedback.md,
                                                  # AC-389..AC-391): at turn
                                                  # start, then every 5 s,
                                                  # until the turn ends on
                                                  # any path. `phase` is
                                                  # starting/thinking/tool/
                                                  # tool_input/writing/
                                                  # asking/speaking; `chars`
                                                  # is answer text shown,
                                                  # `thought_chars` reasoning
                                                  # plus tool arguments;
                                                  # `last_event_ms` is since
                                                  # the last EVIDENCE (a beat
                                                  # is not evidence). The
                                                  # status route carries the
                                                  # same fields. Routed
                                                  # through send_for_turn.
                                                  # When a harness agent's
                                                  # run ends, one extra beat
                                                  # adds `agent` and
                                                  # `agent_elapsed_ms` (the
                                                  # frozen `elapsed_ms`); the
                                                  # record keeps `agent` and
                                                  # `agentElapsedMs` on that
                                                  # turn's assistant message
                                                  # (docs/lantern-ui.md AC-695)
  {type:"artifact", chat, role, artifact}         # BROADCAST: something
                                                  # SHOWN in `chat`
                                                  # (docs/rich-chat.md RC-4,
                                                  # AC-624): `role` is
                                                  # "assistant" (show_file,
                                                  # show_ui) or "user" (the
                                                  # owner's attachment, O-2);
                                                  # `artifact` is the entry
                                                  # the record's message
                                                  # carries. Drawn only for
                                                  # the conversation on
                                                  # screen; a reload draws it
                                                  # from the record
  {type:"error", message}                         # loud, never swallowed
  {type:"announce", turn, text, chat}             # a line nobody asked for,
                                                  # to the DESK only (socket
                                                  # peer loopback, listening,
                                                  # a speaker), queued until
                                                  # it is idle: the daemon
                                                  # MINTED `turn` and the page
                                                  # adopts it only when idle,
                                                  # then the ordinary
                                                  # speak_start / PCM /
                                                  # speak_end follow. Today a
                                                  # scheduled run's result
                                                  # (docs/automations.md AU-18,
                                                  # AC-470); a worker's
                                                  # announcement (AC-14) would
                                                  # use the same door
```

Corrections to earlier drafts of this block, made by reading the running
code rather than the document. The list is deliberately not numbered in the
prose: "three corrections" above four bullets is the same defect as "39
settings" above 46, and a count nobody executes is a count that drifts.

- **There is no `{type:"turn", id, complete, p, ms}` verdict message.** The
  spike sent one because the PAGE owned the endpointing decision and needed
  the answer; the daemon owns it now and nothing on the satellite acts on a
  per-check probability. `closing` is the user-visible signal and it is a
  warning, not a verdict.
- **`final` carries `reason`, not `tier`.** The reason is why the turn
  ended (`silence`, `endpoint`, `hard_cap`, `end_word`, `no_speech`,
  `absolute_cap`, `source_lost`, `client_hint`, `error`, `typed`), which is
  what makes an unexpected cut-off diagnosable after the fact. AC-3's tier
  assertion rides on `reply`, which does carry it.
- **`typed` is a turn with no capture behind it** (AC-162, AC-163,
  `docs/familiar-ui.md` step 2). The satellite sends the TEXT and nothing
  else: the daemon mints the id as it always has, and any id in the body is
  ignored rather than honoured (D-6). It gets no `capture_start` -- that
  frame means "you won arbitration, start streaming the microphone" and
  nothing is being captured -- so the `final` carrying `reason:"typed"` is
  the first frame of the turn and is what names it. Everything after that
  frame is byte-identical to a spoken turn, including whether the answer is
  read aloud, which is the existing `speak` setting and not a second rule.
  A typed message ABANDONS a capture in flight, through the same path a
  wake word does, so the abandoned turn re-arms (D-1).
- **`reason:"whatsapp"` is a phone-origin typed turn** (`docs/messaging.md`
  MS-16/17, M6). It runs on a headless `PhoneSession` in the `phone_chat`
  target; its frames are shown on EVERY page that has that chat on screen,
  never over a capture and with no cue, and nothing of it is spoken at the
  desk: the answer goes back to WhatsApp.
- **The heartbeat is aiohttp's WebSocket-level `heartbeat=20`, not an
  application `ping`.** The daemon accepts a `pong` frame and does nothing
  with it. AC-17's liveness comes from the socket closing, which ends the
  turn through `alive()` in the pipeline; the N-missed-pongs rule below is
  not implemented and does not need to be while the transport does it.
- **The block above is now EXECUTED, and the fields were wrong when it
  was** (`test/test_protocol_doc.py`, D-12). The test parses this fence and
  compares it with `hub._handlers` and with every `type="..."` the daemon
  sends, both directions inbound and both directions outbound -- the Phase 2
  entries being carried by the "NOT implemented today" marker, which the
  test reads rather than hardcoding, and asserts those frames are NOT sent.
  The frame NAMES were already right when it first ran; the fields were not,
  and were corrected by reading the sends: `ready` also carries `tts`,
  `brain` and `session`, `reply` carries `spoken`, `speak_start` carries
  `lang` (AC-160) and `speak_end` carries `truncated` (AC-40). `wake_reject`
  lost `verify_failed`, which no code has ever sent -- `lost_arbitration` is
  the only reason that exists. Nothing checks the FIELDS, so that half is
  still prose and still able to drift; only the frame names are enforced.

Rules carried over from measured failures and from the prior-art review
(section 9):

- **`speak_end` means "no more audio is coming". It does NOT mean "playback
  finished", and the two are different moments.** The daemon writes into the
  socket at socket speed; the satellite plays out at 24 kHz. A four-second
  reply is fully sent in milliseconds and still has four seconds of speaker
  left. Conflating them re-arms the wake detector while the device's own
  speaker is finishing the sentence, and the device transcribes its own
  reply -- Home Assistant's open Voice PE bug #537 [REVIEW
  home-assistant-satellites.md]. Echo cancellation cannot be relied on to
  cover it: Chrome has historically excluded `AudioContext` output from the
  canceller's reference signal, and a phone hearing the desk speaker has no
  reference signal anywhere [REVIEW audio-internals.md]. So the satellite
  sends `playback_done` when its playback worklet reports a real drain (the
  worklet's `playing` flag is racy -- postMessage is async -- so it is a
  drain/drained token round trip answered only when the queue is empty at
  the end of a process block), and the daemon holds the turn open until it
  arrives. The wait is BOUNDED by the audio actually sent plus a 2 s grace,
  and expiring is not an error: a satellite that never reports back must
  still get its microphone returned (D-1). A `playback_done` naming a turn
  the session has moved past is ignored, or a late confirmation for an
  abandoned reply would release the turn the user is now in.
- **`speak_start`/`speak_end` bracket the TURN, not each `Say` intent.** One
  reply is several Say intents -- the Conversation tier streams sentence by
  sentence -- and bracketing each one tells the satellite the reply ended
  several times. The page re-arms on that boundary, so it comes back
  mid-reply with the speaker still going: the same defect as the rule above,
  one level up.
- The daemon MUST check WS backpressure (`bufferedAmount` equivalent:
  aiohttp's write buffer) before sending TTS binary; a stalled client gets
  its stream aborted at a byte ceiling rather than growing daemon memory
  without bound (an earlier prototype hit exactly this: the send loop honored only a
  client signal, never the socket buffer, and a stalled client could OOM
  the server [MEASURED-PROTOTYPE]). Ceiling value: start at 4 MB, the
  constant an earlier prototype chose; a choice, not a measurement.
  **NOT IMPLEMENTED.** `hub.send_audio` writes unconditionally today. This
  is R7 in the review and it is the one rule in this section written as a
  contract that the code does not yet honour -- said here rather than left
  for a reader to discover, because a rule that looks satisfied is worse
  than one that is obviously missing (D-12).
- Liveness is the socket, not an application heartbeat: aiohttp pings every
  20 s, and when the connection drops the session is marked dead, the
  pipeline's `alive()` check fails on its next poll, and the turn ends with
  the partial transcript DISCARDED rather than acted on (AC-17). The
  50 ms poll makes the effective window far tighter than the 5 s that was
  budgeted here.
- The DAEMON mints every turn id, at capture_start. DONE. The spike had the
  client mint them (`web/app.js:30` incremented a local counter and
  `daemon/server.py:113` accepted whatever number the client sent on
  finalize), which is fine for one page and indefensible for N satellites:
  a device can name a turn it does not own. It was inverted first, before
  there was more code to touch, because every discard rule below only
  becomes enforceable once the server owns the id.
- Every message that belongs to a turn carries the turn id; the client and
  server both discard stale-turn messages (the spike already does this for
  `final` and `reply`; Butler extends it to speak/announce).
- One satellite = one WS = one possible capture stream. The daemon-side
  session owns the id of the turn it minted; a second device's audio can
  never finalize into the first device's turn (an earlier prototype's owned-turns
  defect class, D-6/D-7 below).

### 4.4 The brain: one router, two modes

[DECIDED 2026-08-17. This replaces an earlier Reflex / Conversation / Worker
tier split. What changed is not the routing idea but what sits on the far side
of it: the substantive tier is no longer "an API endpoint", it is either an
OpenAI-compatible endpoint OR a full coding-agent harness, chosen by the owner
and each carrying its own model. The old split assumed a harness was only ever
something you DISPATCH work to in the background (4.5); this makes it something
you can also TALK to.]

What follows is Butler's own `Brain` implementations. An external agent
framework would still be another adapter emitting the same intents; nothing
below is part of the port.

**A note on the older names, because they are all over this document.** What
earlier sections call the "Reflex tier" is the ROUTER below. What they call
the "Conversation tier" is whatever answers substantively -- which is now
endpoint mode OR harness mode, chosen by the owner. The routing behaviour
those sections describe (AC-3, AC-3a, the Gherkin in 3.1) is unchanged; only
what sits on the far side of the router is.

**Three pieces, and only the first is always in the path.**

**1. The router.** A small always-warm local model, one job: a strict-JSON
verdict per utterance, sub-second. It never writes code and never sees tools.

```json
{"route": "answer" | "clarify" | "converse" | "dispatch" | "steer",
 "answer": "one short sentence, only when route=answer",
 "question": "one clarifying question, only when route=clarify",
 "action":  {"summary": "...", "harness": "...", "cwd": "...",
             "writes": true|false},  // only when route=dispatch
 "steer":   {"worker": "<worker id>", "message": "..."}  // only route=steer
}
```

`steer` exists because AC-11 requires "Butler, tell it to also update the
README" to reach a specific running process, which is neither an answer nor a
new dispatch. It is offered ONLY when at least one worker is running, and the
prompt is given the live worker list. With exactly one worker running an
unaddressed steering utterance binds to it; with more than one and no clear
address the route degrades to `clarify`, because a steering line delivered to
the wrong worker is a write to the wrong repository.

**How much authority the router has is a SETTING, not a constant** (`router_role`):

- `route` -- the router classifies and chooses what handles the turn.
- `assist` -- the OWNER chooses the mode and what a harness may do. The router
  is still in the path and still earns its keep: it answers trivia, asks the
  one clarifying question, and rewrites a spoken utterance into a clean
  instruction before a harness ever sees it. Dictated speech is full of false
  starts, and handing that to a tool-enabled agent verbatim is how a stray
  clause becomes an action.

Both roles matter for different work. `route` is the hands-free case; `assist`
is composing a real instruction and knowing exactly where it will land.

Model choice is the OWNER's (4.4b); what follows is measurement, not a
prescription. Full evidence in `docs/reflex-serving.md`; the numbers that
characterise the local candidates:

| | verdict p50 | verdict p95 | routing accuracy | VRAM |
|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 Q6_K | 140 ms | 168 ms | 8/10 | 3959 MiB |
| LFM2-2.6B Q8_0 | -- | -- | 7/10 | 2897 MiB |
| LFM2.5-1.2B Q8_0 | -- | -- | 3/10 | 1451 MiB |

[MEASURED 2026-08-16, 20/20 valid JSON, on an IDLE GPU.] 140 ms is roughly a
tenth of AC-4's budget, so the router is not the latency problem this plan once
feared. The same model re-measured on 2026-08-17 with 5564 MiB already resident
came out at p50 181 ms / p95 514 ms (4.4); both are real, and the difference
between them is what contention costs. Two findings ride with the table. **"Move the router to CPU" is dead**: Qwen3-4B on CPU
measured p95 1493 ms, the whole AC-4 budget. And **the verdict schema is built
per turn, not once**: offering `steer` when no worker is running cost a point
of accuracy and every failure was a spurious `steer` [MEASURED]. The constraint
is enforced in the grammar, not requested in the prompt (D-11).

Use llama.cpp GBNF / JSON-schema constrained decoding so the verdict is
structurally guaranteed. Enforcement is TESTED rather than assumed: llama.cpp
silently drops `not` and `if-then`, and a probe emitted a forbidden value in
2 of 5 runs with HTTP 200 [REVIEW realtime-and-brain.md]. Declare `route`
FIRST, because a field declared first is emitted first and is readable off the
stream at 126 ms against 375 ms for the complete object (R9).

**2. Endpoint mode** (`brain_endpoint`). Any OpenAI-compatible
`/v1/chat/completions`, reached through a CONNECTION (4.4b). Streams; the daemon
sentence-splits into the same TTS queue the router's answers use, so the seam
between them is inaudible.

**Butler ships no default model.** `router_model` and `endpoint_model` both
start empty and the owner picks from what a connection actually serves. A
plan-declared default is a name that goes wrong on the first machine that does
not have it, and it silently outranks a choice the owner has already made.

**The keep-warm plan this section carried was wrong, and here is what
replaced it** [MEASURED 2026-08-17 against the running llama-swap, build 197].
An earlier draft said butlerd would send a periodic tiny completion so the
900 s TTL never fired on Butler's model. The ping works; the design around it
does not.

- `globalTTL: 900` is confirmed twice: the config at
  `E:\PROJECTS\other-project\cuda124-b10361\llama-swap-config-b10361-q8cache.yaml`
  and `GET /running`, which also names the resident model and its state.
- `GET /v1/models` loads nothing and resets no timer (16.2 / 39.0 / 1.0 ms,
  and `/running` stayed empty across all three). Butler's model list is free.
- A cold load of the SMALLEST model is 15.85 s; a warm one-token completion is
  15-49 ms. `GET /upstream/<model>/health` also resets the idle clock at
  3-8 ms without touching the KV cache -- a code read at the running commit,
  not a measurement, because proving it needs a 15-minute idle window.
- **The killer: every model is in ONE exclusive swap group.** The config has no
  `groups:` key, so all 127 land in `(default)` with `Swap: true,
  Exclusive: true`, and requesting a second model STOPS the first -- observed
  in the log, not inferred. Two models cannot be resident at once.

So two DIFFERENT models on that one connection cannot both stay warm: the first
conversation turn evicts the router, the keep-warm ping reloads the router, that
evicts the conversation model, and 16-26 s loads land on alternating turns. This
is a property of one connection, not of Butler. Putting the two brains on
different connections (4.4b) is what makes two warm models possible at all,
subject to the card. Butler warns once at boot and does not refuse (4.4a): which
models to run, and on which connection, is the owner's call.

**What the two candidate models actually cost** [MEASURED 2026-08-17, dedicated
llama-server b10361, `--ctx-size 4096 --n-gpu-layers 99 --flash-attn on`,
schema-constrained verdicts at real output length]:

| | verdict p50 | verdict p95 | VRAM | cold load | reply TTFT |
|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 Q6_K | 181 ms | 514 ms | 4077 MiB | 24.6 s | 71-86 ms |
| Ornith-1.0-9B Q5_K_L, `--reasoning off` | 1519 ms | 2762 ms | 6490 MiB | 108.5 s | not measured |

n=20 and n=10. The 4B was measured under load, with 5564 MiB already resident
(desktop, butlerd, and another model on llama-swap); the 9B on a near-empty GPU
at 1692 MiB. The harder conditions belonged to the faster model.

**Ornith-1.0-9B is a reasoning model, and that is a fact about a token budget,
not about speed.** Left as shipped it returns its thinking in
`reasoning_content` and took 9.8 s over 316 tokens to answer a one-sentence
question. Under a 200-token cap it spends the whole cap thinking and returns
EMPTY `content`: 10 of 10 schema-constrained verdicts came back blank rather
than late. pi's registry does carry a `reasoning` flag that would have named
this model, and the owner has chosen not to surface it as a warning (4.4b). What
no registry carries is the consequence measured here -- that a capped verdict
comes back empty, which reads as a broken router rather than a slow one. That
distinction belongs in the router's own failure handling (section 6, D-11), not
in a picker warning.

One older number is superseded rather than confirmed: the 15-49 ms warm
completion in the list above is a 10-token prompt with `max_tokens: 1`, and
proves only that the server is not paging. The 181 ms / 514 ms row is the real
verdict budget and replaces it.

**3. Harness mode** (`brain_harness`). A coding-agent CLI talked to rather
than dispatched to -- one child per MESSAGE, resuming through the harness's
own session reference and disposed at the end of every turn (AC-46, replaced
by the owner on 2026-08-18; the argument for holding it open is recorded
there). First
cut: `claude` and `pi`, the two this machine has measured completing prompts
(2.1.x and 0.65.0). `codex`, `opencode`, `crush` and `gemini` are installed and
report versions but have never been observed finishing a turn here; each one is
a named verification task, not an assumption.

**Transposed, not invented.** `E:\PROJECTS\prototype\scripts\core\agent-backend.js`
is a 151-line contract for exactly this -- `prompt` / `subscribe` / `abort` /
`setModel` / `getMessageHistory` / `resume` / `dispose`, with normalized events
`text_delta | tool_start | tool_end | turn_end | agent_end | error`, a
capabilities record, and a fake implementation as the executable reference. It
has 134-line and 439-line implementations for pi and claude. Butler transposes
the SHAPE (no imports across repos, per this plan's opening rule) and maps the
events onto its own intents: `tool_start` is RECORDED and nothing else --
the narration it used to ride on was deleted on 2026-08-18, because AC-46's
per-message child moved the one fixed line to BEFORE the spawn, where the
silence actually is, and two identical sentences in one turn is a defect --
and `agent_end` fires ONCE after the final `turn_end` so an N-tool answer
announces once rather than per turn. That is the same lesson 4.5 records about
the Stop hook, learned independently on the other side.

**A harness runs at FULL capability, in every mode** (AC-29, retired
2026-08-18 by the owner). The mode-to-tools table that used to sit here --
`paranoid` read-only, `secretary` read-only until a spoken repeat-back was
confirmed, `trusted` unrestricted -- is gone with the ceremony that enforced
it. "The Brain proposes, butlerd disposes" still holds in the sense that
matters for AC-9 and AC-10: the Brain cannot change the mode STORE and voice
still cannot escalate. What it no longer means is that the permission mode
bounds the agent, and the cost is stated rather than buried: a wake-word
false accept -- 0.56-1.5 per hour on `hey_jarvis`, and no speaker
verification until Phase 4 -- reaches an agent with `bash` in the configured
project. The router's verdict and the configured project
list (AC-31) are what is left -- and since 2026-09-11 that list is the
shortcuts pane's rows, which is a wider set than the setting it replaced.

**Where a harness runs.** `harness_cwd` holds a default project directory, and
a spoken "work in project X" changes it for the session against the configured
project list (the shortcuts pane's rows since 2026-09-11; the `projects`
setting it used to read is retired). A spoken name that matches nothing is refused out loud rather
than guessed at: the cost of guessing is an agent with tools in the wrong
repository. Built in step 7.7 of docs/harness-mode.md. Matching is EXACT on
the project's spoken form -- its final path component, lower-cased, with `-`
and `_` read as spaces -- and the switch is scoped to the SESSION: it never
rewrites `harness_cwd`, because a spoken command that edits the owner's
default means the next restart comes back somewhere they did not choose.

**Models are discovered, per connection, and persistent.**
`GET /api/models?connection=` proxies that connection's own `/v1/models`, or a
harness's own list, so the settings page renders what is really available and
still names no model of its own. One persisted `{conn, model}` pair per role --
router, endpoint, and one per harness -- plus a favourites list, because a
picker that opens on 130 entries is a picker nobody uses. The full design is
4.4b.

**Long replies go to the screen.** Anything past about three sentences is
spoken as a one-line pointer and rendered in full on the web UI [DECIDED, the
governing UX rule]. A 9B model asked a real question answers at length, and
reading an essay at someone is the failure mode this rule exists for. Note what
it costs: the page needs a real transcript view, which today is an eight-item
history.

Tier boundary rule, unchanged and now more important: a `dispatch` verdict is a
PROPOSAL. The Mode Store rules on it, the Dialogue Manager speaks the
repeat-back, and only an affirmative reaches the Worker Manager.

**Pieces 1 and 2 are running code since 2026-08-17.** `daemon/dialogue.py`
holds the router -- the per-turn schema, the route-to-intent mapping, and the
3.8 fall-through -- and `daemon/adapters/brain_endpoint.py` holds endpoint
mode, streamed sentence by sentence. `Dialogue` is the `Brain` the hub sees:
the router in front, one substantive brain behind, so harness mode (piece 3)
is a second implementation of that back half rather than a second copy of the
routing. Selected as `BUTLER_BRAIN=endpoint`; `brain_fake` is still the
default and still what every protocol test runs against.

Measured end to end on this machine, Qwen3-4B-Instruct-2507 Q6_K over
llama-swap, with another workload on the GPU [MEASURED 2026-08-17]: 1.51 s
from final to first reply for a route=answer turn the router answered itself,
1.09-1.20 s to the first sentence when it routed to the conversation model,
and 26.63 s on the first turn after a cold load. Those are turn-level numbers
from `test/manual/probe_brain.py`, not verdict latency -- 4.4's 181 ms p50 is
the verdict alone, on a dedicated server.

What is NOT built here: `router_role` (the route/assist split). It is a
setting with nowhere to land yet, and a control that saves and changes nothing
is the AC-7b failure this project has already paid for once. `brain_mode` DOES
exist, since step 7.5 of docs/harness-mode.md: it selects endpoint or harness
mode, defaults to endpoint, and is restart-flagged because the brain is built
at boot (the daemon restarts itself to apply it once idle, AC-715). It landed in the same commit as the spoken grant that was meant to
gate what a tool-enabled child may do; the grant was retired on 2026-08-18
(AC-29) and the default is now the only thing making harness mode a
deliberate choice.

### 4.4a Keeping a model warm, and whether to bother

[MEASURED 2026-08-17. The facts are in 4.4; this is what Butler does about them.]

**It is a setting, because it is not always the neighbourly thing to do.**
`keep_warm` (default on) and `keep_warm_interval_s` (default 300, a third of
the 900 s TTL, so two missed ticks still leave 300 s of margin). Default ON
because a 16-26 s load landing on the first thing the owner says after a quiet
spell is D-10 exactly -- the cold start paid mid-conversation. Toggleable
because llama-swap is shared: while another project is working, Butler pinning
a model is Butler taking the machine.

**The ping is an ordinary one-token completion, not llama-swap's
`/upstream/<model>/health`.** The health route is measured cheaper (3-8 ms
against 15-49 ms) and does not disturb the KV cache, and it is still the wrong
choice: this port promises ANY OpenAI-compatible endpoint, and a route only
llama-swap has would make the keep-warm silently do nothing everywhere else.
Thirty milliseconds every five minutes does not buy a second code path and a
vendor coupling.

**Two things it refuses to do rather than do badly.** With no model named
(`router_model` blank means "the connection's own default"), there is nothing to
keep warm and the ping is skipped with a line saying so -- pinging a default
that may change is a ritual, not a mechanism. And when `keep_warm` is on while
`router_model` and `endpoint_model` name different models ON THE SAME
CONNECTION, the daemon WARNS at boot, because on a swapping connection that
configuration makes things worse rather than better: the ping reloads the
router, which evicts the model mid-conversation (4.4). The same two models on
different connections raise nothing, because nothing evicts anything. Butler
cannot know whether a given connection swaps, so this is a loud warning and
never a refusal -- consistent with 4.4b, where the owner chooses and Butler does
not second-guess the choice.

### 4.4b Connections: where models come from, and where keys do not

[DECIDED 2026-08-17 by the owner. This replaces a design in which Butler had a
single `endpoint_url` and pi's registry was a metadata overlay on it. The owner
chooses every model, from every reachable source, and accepts the latency
consequences. Butler's job is to make that choice possible, keep it current,
and keep the keys out of the browser.]

**A connection is the primitive, not a URL.** `state/connections.json` holds one
entry per place Butler may ask for a completion:

    "llama-swap": {"label":    "llama-swap (local)",
                   "base_url": "http://localhost:8077/v1",
                   "auth":     {"from": "pi", "provider": "llama-swap"},
                   "origin":   "pi"},
    "my-box":     {"label":    "My server",
                   "base_url": "http://10.0.0.5:8080/v1",
                   "auth":     {"from": "env", "var": "BUTLER_KEY_MYBOX"},
                   "origin":   "manual"}

**A model choice is a PAIR, `{conn, model}`.** `router_model` and
`endpoint_model` keep their names and each gains a connection, so the two brains
may sit on different servers. A bare id is ambiguous the moment two providers
serve the same name, and useless for any provider that needs a key -- which is
why the single `endpoint_url` this replaces could only ever reach
unauthenticated local servers.

**pi's registry supplies CONNECTIONS and KEYS. It does not supply the model
list.** `C:\Users\user\.pi\agent\models.json` holds 8 providers, each with `api`,
`baseUrl`, `apiKey` and a declared `models[]` array. Butler takes two things
from it and ignores the third: the providers become connections with
`origin: "pi"`, their keys stay readable BY REFERENCE, and `models[]` is never
read.

**The list is live, because a declaration goes stale silently.** Every model
list comes from the connection's own `/v1/models` when a picker opens,
concurrently across connections, behind the 60 s catalogue `daemon/models.py`
already keeps. That is the rule that module was written for: a list recorded
somewhere else is wrong the first time a config changes elsewhere, and wrong
INVISIBLY -- the picker still shows the name, the daemon still saves it, and the
endpoint answers 404 on the next thing the owner says. pi's `models[]` is
exactly such a recorded list, which is why it is not used.

**Connections are grouped in the picker and probed for reachability**, never
flattened into one list. 56 of pi's 189 entries are localhost-addressed but
cloud-backed -- `codexbridge-local` is GPT-5.x, `claudebridge-local` is Claude,
`zen_bridge` an aggregator, `qwen-local` Qwen cloud -- answering on five
distinct ports, two of which were not listening when measured, and 6 more are
directly remote behind a key. A connection that does not answer SAYS SO rather
than contributing silence.

**Butler stores no secret, ever.** `auth` is a pointer, never a value.
`{"from": "pi", "provider": X}` reads that provider's key out of pi's registry
in memory at call time; `{"from": "env", "var": X}` reads an environment
variable. A missing or unreadable key fails LOUDLY when it is needed, naming the
connection and what it wanted, rather than falling back to an unauthenticated
request that returns 401 from somewhere further away. Butler never writes a key
to its own disk, and no response from `/api/models` or `/api/connections` ever
contains one (AC-34, AC-37).

**Reload is a button, not a watcher.** pi's registry and
`state/connections.json` are read at boot and when the owner presses Reload.
Nothing watches the filesystem. A provider that has vanished from pi's file
since boot is marked missing rather than deleted, and a model setting still
pointing at a missing connection is a loud failure at use, never a silent
substitution onto some other connection. Entries with `origin: "manual"` are
never touched by a reload.

**Adding a connection is a form in the settings page**: label, base URL, and the
NAME of an environment variable holding the key, plus a Test button that probes
`/v1/models` and reports what came back, and a remove for the owner's own
entries -- a panel that adds and never removes leaves hand-editing a file
nobody was told about as the only way out. Removal is refused on a
pi-imported entry, because the import would put it straight back. It is the first hand-built panel in a
page that otherwise renders entirely from the settings schema, and it is
hand-built deliberately: a connection is not a preference. It has its own shape,
its own validation, and its own probe. An id already used by a pi provider is
refused at save rather than silently shadowing it.

**Butler does not warn about the model the owner chose** [DECIDED by the owner
2026-08-17]. No reasoning-model flag, no latency probe on save, no annotation in
the picker. The owner selects from what is served and owns the result. What is
MEASURED about the models on this machine is recorded in 4.4, so the choice is
informed rather than prevented.

### 4.4c Harness mode: sessions and persistence

Transposed from an earlier prototype, whose `AgentBackend` contract and two implementations
have already paid for the defects below. Butler copies the SHAPE and not the
code (no imports across repos), and deliberately declines several things.

**What persists, and where.** The app owns the transcript; the harness owns the
context. They are separate and the second is losable.

```
state/chats/
  chat-<ms>-<rand>.json          Butler's RECORD, rewritten whole per message
  chat-<ms>-<rand>-<harness>.session.json
                                 the AGENT's own context, where it keeps one
```

```json
{"id": "chat-1776568428712-0kze41", "createdAt": 1776568428712,
 "updatedAt": 1776610601555, "harness": "pi", "sessionRef": "...json",
 "messages": [{"id": "msg-...", "role": "user", "content": "...",
               "timestamp": 1776568436645}]}
```

Rewritten WHOLE on every append, which is the reference implementation's own
write pattern and the price of its simplicity. `id`, `createdAt`,
`updatedAt`, `messages` and the message shape are taken verbatim from it
[READ 2026-08-18]; `title`, `titleSource`, `pinned`, `archived`,
`parentChatId`, `parentMessageId` and `channelId` are deliberately NOT copied,
because they serve a chat list, forking and multiple channels and Butler has
one current conversation and none of the three. The reference's per-backend
pair (`piSdkSessionId`, `claudeSessionId`) collapses to `harness` plus one
`sessionRef`, because a Butler chat runs one harness. That list is the whole
schema and it is not to be extended.

This NO LONGER mirrors `workers/<id>/` in 4.5, and the divergence is
deliberate: a dispatched worker's tool-by-tool record is what its announcer
reports on, while a talked-to conversation is turns. Built as
`daemon/chats.py`, replacing the per-session directory with `meta.json` and
an append-only `events.jsonl` that step 7.6 shipped and the owner replaced on
2026-08-18 (docs/harness-mode.md 10.2). What went with it: rotation as a
fresh directory, the numbered `harness-N.json` scheme, the tool-level
transcript, and the `new`/`resumed`/`lost` wire vocabulary.

**Resume is a CAPABILITY, not a branch on harness name.** `native_resume` says
whether a harness hands back an opaque session id to resume by. claude does
(`--resume <id>`, capturing the id from the `init` line on first output); pi
does NOT -- it is pointed at its own session FILE, which Butler owns and names
[MEASURED 2026-08-18: a real child answered a question from before a daemon
restart, and pointing the second child at a different file made the same test
fail with "I don't have any number to recall from this chat"]. An earlier
draft of this paragraph said pi resumes by replaying a transcript; nothing
replays a transcript into pi and nothing has to. A caller must never test the
harness's name to decide, or the third harness rewrites every call site.

The page used to be told one of THREE things about a conversation it joins --
`new`, `resumed`, or `lost` -- and that vocabulary was DELETED with the store
that answered it on 2026-08-18. `ready` now carries `{session, turns}` and
`GET /api/session/current` carries `{id, harness, messages}` (and, with
`GET /api/chats`, `GET /api/chats/{id}` and `POST /api/session/open`, `agent`
and `agent_label`: the chat's `chat_harness` and its owner-facing name, CT-8), and nothing
says whether the AGENT still remembers any of it. That is a real loss and it
is recorded here rather than replaced by a guess: a line claiming "resumed"
that the daemon cannot back would be the honesty defect the three words
existed to prevent, wearing the opposite costume (docs/harness-mode.md 10.5).
`native_resume` survives on `HarnessCaps` and is still what a caller reads
instead of a name; it becomes load-bearing the day the claude adapter lands.

**Rules taken directly, each one paid for by a defect recorded in an earlier prototype:**

- A captured session id needs a matching WAY TO CLEAR IT. A stored id that
  outlived its transcript left 8 of 26 chats permanently dead, one of them
  answering nothing for two weeks. Butler persists the reference and clears it
  on the one signal that means it is gone. Two honest limits on that clause as
  it stands: the signal is INERT for pi, which creates a missing session file
  rather than refusing it, and clearing a reference Butler MINTS buys nothing,
  because `ChatStore.session_ref` derives the path from the chat id and hands
  back a byte-identical one on the next call [MEASURED 2026-08-18]. The clause
  becomes real for a harness that returns an opaque id Butler could not have
  guessed -- `caps.native_resume`, a claude adapter's `--resume` -- which is
  the same day it gets a test (docs/harness-mode.md 10.7 item 2).
- Discard an id only on the CLI's literal words for a missing session. Any
  broader rule throws away context because of an unrelated failure.
- Rule on a startup failure at the child's `close`, not at the first error
  line: stdout and stderr are different pipes and a verdict taken early is a
  race. Butler's asyncio subprocess handling has exactly this hazard.
- Replay a failed turn ONCE, from the verbatim serialized input. Rebuilding it
  from a text history silently drops images.
- Per-child buffers, and every handler bails when the child is not the current
  one. On Windows a killed child keeps emitting, and a fragment from a dead
  one prefixes the successor's next line, the parse fails, and the turn never
  ends. Null the handle BEFORE killing so a synchronous exit takes the
  superseded path.
- Re-check identity after every await in the turn path. A turn parked on an
  await can otherwise wake up and drive a backend that was disposed while it
  slept.
- Exactly ONE terminal event per prompt, and `agent_end` fires once when the
  whole loop settles -- never per `turn_end`. For a voice assistant this is
  the difference between announcing once and announcing five times.
- Fail fast at construction on contradictory options rather than at first use.

**The one thing Butler used to refuse and now DOES, by DEFAULT.** An earlier prototype runs
claude with `--permission-mode bypassPermissions`; Butler's harness child at
the `full` posture is functionally the same thing. `HarnessSpec` carries a
plain `tools` allow-list re-added on 2026-08-18 (the minimal slice of the
plumbing 653a44d deleted): `harness_pi._argv` emits `--tools read,grep,find,ls`
for read_only and NO flag for full, driven by the persistent `harness_tools`
SETTING. This is a knob, NOT the spoken grant AC-29 retired -- no ceremony, no
permission-mode coupling, no repeat-back. The default is read_only, so a
false accept cannot make a built-in write until the owner flips it; full is
pi's own default set including `bash`. The claude adapter
(docs/harness-mode.md 10.2) spells the same two postures in claude's own
vocabulary.

The `--tools` allow-list is verified to gate pi's BUILT-IN tools, but a
MEASUREMENT on 2026-08-18 shows what it does NOT gate: a read_only pi child
wrote a file through the `background-process` EXTENSION's shell -- exactly an earlier prototype's shape, a restricted child still holding a tool the allow-list never
covered. pi has NO `--strict-mcp-config`, and `--no-extensions` would close the
hole but also disables the provider bridge (measured: "No API provider
registered"), so there is no clean lever to gate pi's extensions today.
Restriction on the day it must be airtight has to be an ALLOW-LIST plus an
extension/MCP gate, never a deny-list that fails open on the next built-in;
that gate stays UNSOLVED for pi and is recorded here rather than faked with a
flag pi ignores. read_only bounds pi's built-in tools, not its extensions.

**What Butler does NOT copy, and why each is deliberate:**

- **Its children do not reliably die with it.** The agent manager is never
  disposed on shutdown and its children are never registered, so cleanup rests
  on stdin EOF. D-24 says the opposite: every child Butler spawns watches its
  parent and is tree-killed on the way out.
- **Self-minted tool step ids.** pi's backend discards the SDK's own
  `toolCallId` and keeps a single current-step slot, so parallel tool calls
  overwrite each other -- measured at 8 of 19 tool starts never closing, and
  an error outcome that is literally unreachable. A voice assistant that
  speaks tool progress would announce eight things it never finished. Use the
  harness's own id and a dict of open calls.
- **A turn that can end with no terminal event.** pi's backend emits `error`
  only on a JS exception, so a provider failure leaves the turn lock held
  forever. Butler requires exactly one of `turn_end` or `error` per prompt and
  asserts it ON THE CALLER (D-18).
- ~~**A transcript that is only `{role, content}` text.**~~ SUPERSEDED
  2026-08-18: Butler now records exactly that. The owner ruled for the
  reference's flat chat file, and `messages` holds roles and text, so tool
  names and outcomes are dropped here too and a resumed page shows what the
  agent SAID and no longer what it DID. Kept struck rather than deleted,
  because it is the position that lost and the loss is real (10.5): the
  original objection was that a replayed session is materially less informed
  than the one it replaces. Butler pays LESS for it than the predecessor did
  -- nothing replays a transcript into a harness here, the agent's own
  context comes back through `sessionRef`, so the cost is a display loss on
  the page rather than a worse prompt.
- **Silent truncation.** A 12k-char oldest-first cut with nothing telling the
  model or the owner that the head of the conversation is gone.
- **Resuming whatever sorts first.** Butler resumes by an explicit last-active
  timestamp, not by the head of a list sorted pinned-first and unfiltered for
  archived.
- **Unvalidated model specs.** A typo becomes a child that dies at startup.
- **Transcript seed files left in %TEMP% forever**, and disk-persistence
  failures swallowed by a bare catch. A failure to persist a session id is a
  failure to be resumable, and this project fails loudly.

### 4.5 Worker lifecycle

```
dispatch(action)                                  [mode-gated, confirmed]
  -> worker_id minted; dir workers/<id>/ created:
       meta.json     (harness, cwd, prompt, voice, mode at dispatch)
       events.jsonl  (append-only; THE durable record)
  -> spawn:  claude -p <prompt> --input-format stream-json
             --output-format stream-json --verbose --replay-user-messages
             (cwd = action.cwd; permission flags derived from the MODE at
              dispatch time, frozen into meta.json)
  -> a per-worker reader task drains stdout EAGERLY into events.jsonl and
     an in-memory queue (never let narration backpressure the pipe: the
     30 s exit-drain gotcha, AC-13)
  -> narration: keyed on tool-use events only ("reading auth.ts",
     "running tests"); token deltas are never spoken; narration lines are
     rate-limited and go through the announcer at LOW priority
  -> steering: a spoken instruction addressed to a running worker becomes
     ONE JSON user-message line on that worker's stdin. Exact line schema
     is verified against the real CLI in Phase 2's first red test -- the
     flags above are given; the message shape is not asserted here to
     avoid inventing it.
  -> events: the Claude Code Stop hook (and Notification/idle_prompt hooks
     for attention requests) run a small script that (1) appends to
     events.jsonl, (2) POSTs http://127.0.0.1:<port>/api/workers/<id>/event.
     The file is the record; the POST is the doorbell. Daemon reconciles
     from files on boot (AC-12). Never poll.
  -> COMPLETION, defined precisely, because the obvious reading is wrong:
     Stop fires once per ASSISTANT RESPONSE, not once per worker. With
     stdin held open for steering the process stays alive and Stop fires
     again after every steering round -- so treating the first Stop as
     "worker done" would announce completion mid-run, repeatedly. Butler's
     definition: a Stop event marks the worker IDLE and starts a grace
     timer (default 10 s); if no steering message is pending when it
     expires, the daemon CLOSES the worker's stdin; the process then exits
     and that exit is the single terminal event written to events.jsonl and
     announced. Steering during the grace window cancels the timer.
  -> HOOK INSTALLATION: the worker runs in an arbitrary action.cwd, so its
     hooks cannot come from that project's .claude/settings.json -- Butler
     would otherwise be silent in every repo it has not pre-configured, and
     editing user repos to install hooks is not acceptable. Butler passes
     its own settings file explicitly on the command line. The exact flag
     and file shape are pinned by Phase 2's first red test against the real
     CLI, alongside the stdin message schema (8.7).
  -> FLAGS, partially verified: `claude --help` on this machine states that
     --input-format and --output-format only work with --print, so -p is
     required, not merely compatible [MEASURED -- claude --help, 2026-08-16].
     What is NOT settled is whether the initial prompt is a positional
     argument or the first stdin JSON line when --input-format=stream-json
     is set; those two are plausibly mutually exclusive. Same red test.
  -> announcement: one line that NAMES the worker in words ("the butler
     retry job is done"), queued by the AC-14 state rules. The per-worker
     distinct-Kokoro-voice rotation is deferred out of Phase 2: it only
     pays off when several workers run at once, which is not the Phase 2
     experience, and section 7 already concedes that naming the worker is
     an acceptable substitute when voices run out. Revisit in Phase 3+ if
     concurrency becomes routine. Anything longer than about three
     sentences goes to the web UI with a one-line spoken pointer
     [DECIDED -- the governing UX rule].
```

pi and codex in Phase 2 run as plain `-p` one-shot processes with
completion detected by process exit writing a final event (no steering, no
hooks) -- stated limitation, upgraded only if their stream protocols are
verified later. `pi` on Windows must be spawned as `cmd /c pi` (PATHEXT
resolution failure otherwise -- spike-verified).

### 4.6 Permission modes and the mode store

Named, persistent, user-toggleable modes [DECIDED]. Proposed set (names
final at Phase 2 review):

| mode        | reads | conversation | dispatch (read-only work) | writes/dispatch with writes | mode raise |
|-------------|-------|--------------|---------------------------|-----------------------------|------------|
| `paranoid`  | yes   | yes          | no                        | no                          | UI only    |
| `secretary` (default) | yes | yes  | confirm by repeat-back    | confirm by repeat-back      | UI / verified voice (P4) |
| `trusted`   | yes   | yes          | yes                       | confirm by repeat-back      | UI / verified voice (P4) |

This table is about Phase 2's WORKER dispatch and is where AC-9's repeat-back
lives. It does NOT describe the harness: the owner retired AC-29 on
2026-08-18 and a coding agent now runs at full capability in every mode, so
between here and the Worker Manager these three modes gate nothing at all.

Load-bearing rules [DECIDED, restated here because they bind every phase]:

1. Escalation is asymmetric. DOWN (more restrictive) works by voice
   instantly from any device, unverified -- refusing a lockdown is never
   correct. UP (more permissive) requires a web UI click or, from Phase 4,
   a spoken confirmation gated on speaker verification. "Switch to trusted
   mode" from a podcast must be inert. The authorized path is
   `POST /api/mode`, and it exists: `mode_request` over the WebSocket calls
   the store with `authorized=False` and is refused, the HTTP route calls
   it with `authorized=True`. Until that route landed there was no client
   path at all, so the mode could only ever go down and the UI half of
   AC-10 was unimplemented rather than merely untested.
2. The store is server-side: `state/mode.json`, atomic replace-on-write,
   broadcast to all satellites on change. NOTHING mode-related in
   localStorage -- an earlier prototype recorded one laptop holding four diverging
   localStorage copies of a voice setting because the app answered on four
   origins [MEASURED-PROTOTYPE]. AC-8's grep gate enforces it.
3. The mode at DISPATCH time is frozen into the worker's meta.json and
   translated into harness permission flags once; a later mode change does
   not retroactively loosen a running worker.

### 4.6a Preferences: the settings schema

Kept apart from the mode store on purpose. Permission modes gate what
Butler is allowed to DO, so they are asymmetric, refuse escalation by
voice, and fail closed. Everything here is a preference: how long to wait
before submitting is nobody's security problem. What they share is where
they live -- on the DAEMON, in `state/settings.json`, atomic
replace-on-write, never in the browser (D-2, AC-7b).

**The schema IS the contract.** `daemon/settings.py` holds 46 settings in 7
groups (and grows again as the rest of 4.4 lands), each carrying its kind, range or choices, default, group, help text
and a restart flag. `GET /api/settings` serves the schema alongside the
values (and `pending`, AC-716) and `web/settings.js` generates every control from that response,
naming no setting of its own. A form with a hardcoded field list drifts
from the daemon the first time someone adds a setting, and the drift is
invisible: the new one simply does nothing, and nothing says so.

**One group per stage of the audio path, in the order sound travels it**
[DECIDED 2026-08-17 by the owner]. The page reads as the pipeline -- hear it,
recognise it, decide you have finished, think, speak -- so each swappable
engine has exactly one home and "where do I configure speech recognition" is a
question with an answer. It replaced a grouping by how FIDDLY a setting felt,
which put speech recognition in "Engines (need a restart)" beside the
end-of-turn model, and put the turn thresholds in a different part of the page
from the turn mode they modify. Group ORDER is load-bearing and asserted
(AC-45); `advanced` is a per-SETTING flag rendered as a disclosure INSIDE its
own section, because a tier is orthogonal to a seam and only one of the two is
an organising idea.

**A group is DATA, not a label with a number in front of it**
[DECIDED 2026-08-18 by the owner]. Each one carries a `label`, a `stage` --
its position in the audio path, or none for a group that is not a stage of
anything -- and a `headline`, the KEY of the setting whose current value a
summary box for that group displays. The order used to be faked into the label
text ("1. Listening", "5. Harness"), so the only thing that knew the pipeline
was a string being printed, and the page cannot recover either fact for
itself: naming a setting or a seam in `web/` is what AC-41's grep gate forbids.
A headline naming a setting that is not in the schema raises where `GROUPS` is
defined, and so does a stage that is not contiguous from 1 in key order --
a blank box reads as "not set" rather than as a rename that was left half
done (AC-7b).

| stage | group | headline | everyday | advanced (same section, one disclosure down) |
|---|---|---|---|---|
| 1 | Listening | -- | wake_hey_butler, wake_hey_jarvis (AC-803), wake_threshold | rms_on, rms_off, min_speech_ms, arbitration_window_ms, wake_cooldown_ms |
| 2 | Recognition | asr_model | asr_model, language | asr_backend, asr_latency |
| 3 | Turn taking | endpointer | turn_mode, patience, end_phrases | endpointer, smart_threshold, eou_first_check_ms, eou_recheck_ms, eou_max_wait_ms, no_endpointer_silence_ms, no_speech_ms, absolute_max_turn_ms |
| 4 | Brain | brain_mode | router_model, endpoint_model -- each a `{conn, model}` PAIR (4.4b) | router_max_tokens, reply_max_tokens, model_favourites |
| 5 | Harness | harness | harness, harness_model (discovered from the harness's own registry, 4.10), harness_tools (read_only/full, default read_only), harness_cwd, projects | -- |
| 6 | Speech | voice | voice (discovered from the engine, 4.10), cues, cue_volume | -- |
| -- | Appearance | theme | theme, viz_placement, reading_face, colour_scheme | -- |

Two headlines name the closest honest setting rather than the engine behind
the seam, and the schema says so where they are declared: Speech shows the
VOICE and Recognition shows `asr_model`, because which TTS and ASR adapters
are loaded is named in `config/models.json` (4.9) and is not a setting at all.
Listening has no headline for the opposite reason -- no engine sits there, and
a wake threshold is not a fact about a seam.

`endpoint_url` was retired on 2026-08-17: a connection carries the base URL,
and two ways to name a server is the drift connections replaced. An older
on-disk string value is MIGRATED to `{conn: "", model: <it>}` rather than
dropped or rejected -- rejecting means a daemon that will not boot after an
update, dropping means losing a choice the owner made.

`mic_style` was retired from Appearance on 2026-09-10 (docs/familiar-ui.md
6.4): five microphone treatments existed because the mic was the page's
centrepiece, and beside a composer it is a 40 px button where four of them
have nowhere to be. Unlike `endpoint_url` and `asr_language` it carries NO
migration, because a retirement only needs one when there is a successor key
to carry the value TO -- the unknown-key drop in `Settings._load` is the whole
of it, and it announces the key it ignored so the owner's `arc` disappears
loudly. The three settings that replaced it in that group are described by
AC-167, AC-173 and AC-174 in `docs/familiar-ui.md`, which are not yet accepted
into section 2; the table above is a description of the schema as it stands,
not an acceptance.

The two token caps are advanced rather than everyday [DECIDED 2026-08-17, a
departure from docs/architecture-seams.md 4.2, which wrote them as everyday
rows]. A cap is exactly what the per-seam disclosure is for: it defaults to 0,
most owners never touch it, and its consequences are a runtime log line rather
than a daily preference. The disclosure is inside the Brain section, so it is
still one click from the model that it bounds.

Not settings, and deliberately outside this schema: CONNECTIONS (4.4b) live in
`state/connections.json` with their own panel, because a connection has a
shape, a reachability probe and a secret reference the schema does not model.
The `Harness` group joined the sequence between 4 and 5 on 2026-08-18, with
the code that reads its three settings (docs/harness-mode.md 7.4); `projects`
joined it in 7.7 with the spoken switch and was RETIRED from the schema on
2026-09-11, when the shortcuts pane's rows became the one project list. Still to come:
router_role in group 4 (4.4) -- with the code that reads it, because a control
that saves and changes nothing is the AC-7b failure this project has already
shipped once.

Everything except the restart-flagged settings is read PER TURN -- the pipeline builds
its energy gate from the settings, the hub builds the end-of-turn policy
and reads the Kokoro voice per reply, the arbiter takes its window from
them -- so a change takes effect on the next thing the owner says. The
engine settings bound when an adapter is CONSTRUCTED either swap live
(`asr_latency`, `endpointer`, AC-713) or carry `restart: True` and are
applied by an automatic restart once idle (`asr_model`, `asr_backend`,
`brain_mode`, AC-715); the flag is asserted in both directions against
which module actually reads the value, and the page shows it. A control
that saves, reports success and changes nothing until a restart nobody
mentioned is worse than one that is not editable at all: the user
concludes the whole panel is fake.

Writes are all-or-nothing (`Settings.update` validates the entire body
before touching a value), rejected with a 400 naming what was allowed, and
broadcast to every satellite as `{type:"settings", changed, turn_mode,
capture_ceiling_ms, pending}` so
a second device is never left showing a stale value.
`POST /api/settings/reset?group=` returns one group or all of them to
defaults. An unreadable settings file is loud and NOT fatal -- unlike a
permission mode, a preference cannot make Butler unsafe, so refusing to
boot over one would be the worse failure -- and unknown keys are dropped
with a printed line rather than kept, because a file that still lists a
setting Butler no longer honours looks like it still does something.

### 4.7 Speech output: TTS queue, announcer, voices

Kokoro runs as a node sidecar owned by the daemon (butler's own
`package.json`, kokoro-js; forked from the spike's tts-sidecar.mjs, not
shared with an earlier prototype). Everything below is a direct port of lessons an earlier prototype
paid for [MEASURED-PROTOTYPE throughout]:

- **The FIRST sentence is capped, and this is enforced.** Kokoro's realtime
  factor is 3.5-4.6x on CPU at every length [MEASURED 2026-08-16], so the
  opening sentence is the whole of AC-4's TTS budget: about 8 words (~2 s of
  speech) costs ~450 ms, 19 words costs 1784 ms, 38 words costs 3710 ms. The
  splitter therefore CUTS the first segment at a word cap regardless of
  where the sentence ends, at a clause boundary if one is available, and the
  remainder becomes the next segment. Later sentences synthesize while the
  first is playing, so only the first one is latency-critical. Enforced in
  the splitter with a test, not requested of the model in a prompt (D-11).
- **EVERY segment is capped, and the two caps exist for different reasons.**
  The first at 8 words serves AC-4 (time to first audio); every later one at
  16 words serves AC-5 (how much synthesis a barge-in strands). Capping only
  the first left AC-5's recovery time unbounded: barge-in orphans whatever
  segment is in flight, and before step 8 the abandoned segment ran to
  completion with the next reply queued behind it. At the measured ~95 ms per
  word an uncapped 38-word segment strands 3710 ms. 16 words bounds that near
  1500 ms. Since step 8 the sidecar can be cancelled, so what an orphan costs
  is now the sentence inside `generate()` rather than every sentence after it
  -- which means the later cap COULD rise. It deliberately has not: that is a
  latency trade with its own measurement (fewer round trips against a larger
  stranded sentence), and making it in the same change that enabled it would
  put two decisions behind one set of numbers.
- **Abort reaches the engine, and strands about two sentences [MEASURED
  2026-08-17].** The sidecar takes `POST /abort` on its own connection, which
  runs the service generator's `finally`, which sends the worker an abort the
  worker handles outside its FIFO queue (D-4). Measured on a ten-sentence
  call aborted a quarter of the way in: the next reply's first audio arrives
  in 3736-4392 ms, against 7887 ms when the engine could not be told at all.
  What is stranded is the sentence already inside kokoro's native
  `generate()`, which has no cooperative cancellation, plus usually the one
  after it, because `tts-service.js` only sends its abort when the generator
  is resumed and the generator is resumed by the next chunk. Closing that
  second gap means giving `TTSService` a public abort-by-id rather than
  relying on generator finalisation; it is named here and not done.
  An earlier draft of this plan predicted the orphan cost would go "to about
  zero" once cancel landed. It did not, and the prediction is corrected here
  rather than quietly dropped. In practice a real barge-in strands far less
  than the measurement above: `speech.segments` caps a segment at 16 words,
  so the daemon never issues a ten-sentence call.
- **Sentence-level synthesis and abort.** A reply is synthesized sentence
  by sentence; interrupting abandons at most ONE in-flight sentence. The
  abort message is dispatched OUTSIDE any serializing command queue in the
  sidecar (an abort behind the synthesis it cancels is a fake fix -- found
  the hard way). Evidence this design works: interrupting a 12-sentence
  answer let the next answer's first audio arrive ~1.7-3.0 s later instead
  of ~15 s of orphan synthesis starving it.
- **Fail loud on a dead segment.** A sentence that fails twice (second try
  on a fresh sidecar) emits an audible/visible error and abandons the rest
  of that reply -- never a silent skip, never a lying "done".
- **Separate cold-start budget.** Model load gets its own long timeout;
  the per-sentence watchdog is short. One shared 30 s watchdog SIGKILLed a
  worker that was merely still loading, and the "fresh" retry landed on a
  replacement still loading too -- cascade to silence.
- **Client playback is one worklet fed by a queue.** The satellite plays
  TTS through a single AudioWorklet draining a sample queue at hardware
  rate; underrun is self-healing silence. Never per-chunk
  `source.start(t)` scheduling -- it drops late chunks and resets the
  clock (the original non-determinism).
- **One consumer per buffer.** A PCM ArrayBuffer is copied per consumer,
  never transferred to two listeners (the DataCloneError class).

The **announcer** is the single gate to the speaker: priorities are
(1) direct reply to the bound device, (2) worker announcements queued to
turn boundaries, (3) narration lines, dropped first under pressure.
Suggestive, low-complexity phrasing [DECIDED]. **It does not exist yet**
and does not need to until there is a second thing that wants the speaker:
today `hub._speak` is the only path to a satellite's audio, and Phase 2's
worker announcements are what force the queue to be real (AC-14). Voice
assignment: the reply voice is the server-side `voice` setting (4.6a), read
per reply so a change on the settings page takes effect on the next thing
Butler says. Phase 2 uses that same voice for worker announcements, which
identify the worker by NAME. A distinct-voice-per-worker rotation
(recorded in meta.json) stays in the design but ships no earlier than
Phase 3 -- see 4.5.

**Three audible cues, and the reason they are in this section is that they
are output that lands back in the microphone.** The page synthesizes them
into the existing playback AudioContext -- iOS caps concurrent contexts at
about four and the mic and the playback worklet already hold two, so a
context per cue is budget spent for nothing.

| cue | when | tone | why it sounds like that |
|---|---|---|---|
| `listening` | the LOCAL detector fired, before `wake_candidate` leaves the socket | 880 Hz, 120 ms | immediate feedback beats the rare case where two devices in earshot both beep and only one wins arbitration [DECIDED] |
| `closing` | on the daemon's `closing` message | 520 Hz, 60 ms | quietest and shortest: the only one that can land on top of a talking user |
| `submitted` | on a non-empty `final` | 660 then 988 Hz, 150 ms | two ascending notes, so it can never be mistaken for either single-note cue |

Every cue ramps up and decays exponentially to -80 dBFS rather than
starting and stopping on a discontinuity. The reference implementation
uses a bare `setValueAtTime`, which clicks -- and a click is BROADBAND
energy, the last thing to put next to a wake detector and an energy gate.

**The suppression is the load-bearing part** (AC-5b). The reference feeds
its own cues straight into its own recognizer; Butler does not. `onFrame`
ZEROES the microphone samples in place for the cue's window -- scheduling
lead, plus both AudioContexts' latencies, plus the tone, plus a tail sized
per cue (150 / 90 / 130 ms) -- before the pre-roll ring, the wake detector
or the socket see them. The tail is derived, not felt: a domestic room's
RT60 is around 0.4 s, so 15 dB of decay takes about 100 ms, which is what
the loudest cue needs to fall under the daemon's energy gate; the mic
worklet's 512-sample frames add 32 ms of straddle and the rest is margin.
Zeroed rather than DROPPED, because dropping frames would desynchronise
the `preroll` byte count from the header that declares it and the daemon's
byte-derived silence clock from the 32 bytes per millisecond it assumes.
The cost is that speech overlapping a cue is destroyed rather than
delayed, which is the right trade for a 120 ms window and is why the
closing cue is the quiet one.

### 4.8 Speaker verification (Phase 4)

SpeechBrain ECAPA-TDNN, 192-dim embeddings, cosine similarity against
enrolled voiceprints, threshold tuned on the owner's own enrollment
recordings [DECIDED]. Scored on wake word PLUS the following command audio
-- the wake word alone (~0.6 s) is below the reliable floor [DECIDED].
Runs in a dedicated child process with single-threaded BLAS (the
turn-engine rule, 4.1). What verification gates is mode-dependent:
in `secretary`, failed verification still allows read-only conversation
but refuses writes/dispatch/escalation; the refusal is spoken and both
scores logged. FAR/FRR on this hardware and this room are UNMEASURED until
enrollment exists -- the threshold is tuned then, not now (section 8.4).

### 4.9 Repository layout (proposed)

```
E:\projects\butler\
  plan.md                    # this file
  daemon\
    butlerd.py               # entry: asyncio app, boot warmup, TLS
    ports.py                 # THE CONTRACT (4.10): Source/Asr/Tts/Brain/Harness + Intent types
    adapters\                # one file per implementation; the ONLY place a
                             # concrete engine is named outside config\
      asr_transcribecpp.py   # wraps engines\stt_engine.py
      asr_fake.py            # scripted partials/finals; the port's 2nd impl (AC-7a)
      tts_kokoro.py          # wraps engines\tts\ sidecar
      tts_fake.py            # silent PCM at real timing; 2nd impl (AC-7a)
      brain_fake.py          # scripted intents; the ONLY Brain today
      endpointer_smartturn.py  # NOT a port (4.10 caps them at five): the one
                             # async callable the Hub holds
      source_ws.py           # NOT WRITTEN. The WS source is inlined in hub.py
      source_native.py       # NOT WRITTEN (P5)
      brain_tiers.py         # NOT WRITTEN. Reflex + Conversation; without it
                             # every reply Butler gives is scripted
    hub.py                   # satellite registry, WS handler, protocol v1
    arbiter.py               # wake candidate window, scoring, binding
    pipeline.py              # per-turn: Asr session + utterance buffer + VAD + caps
    endpointing.py           # the three end-of-turn policies (4.2)
    settings.py              # the preference schema, store and API model (4.6a)
    speech.py                # sentence splitter (the TTS queue and announcer
                             # are not written; hub._speak is the only path)
    modes.py                 # mode store, asymmetry rules
    server.py                # DEAD: the spike's server, superseded by butlerd
    dialogue.py              # NOT WRITTEN. Reflex verdict, Conversation stream
    tiers.py                 # NOT WRITTEN. model registry, openai/anthropic
    workers.py               # NOT WRITTEN (P2)
    verify.py                # NOT WRITTEN (P4)
    engines\                 # forked from the spike, kept together as a unit
      stt_engine.py          # transcribe.cpp wrapper
      turn_engine.py         # Smart Turn child process
      catalog.json
      tts\                   # kokoro-js sidecar plus its 4 support modules
        tts-sidecar.mjs   tts-service.js   tts-worker.js
        tts-model-loader.js   tts-sentence-stream.js
  web\                       # satellite page = web UI  (deliberately not static\)
    index.html  app.js  wake.js  mic-worklet.js  tts-playback.worklet.js
    pcm.js  diag.js
    settings.js              # the panel, generated from the daemon's schema
    ort18\                   # onnxruntime-web 1.18.0 (gitignored)
    models\                  # wake ONNX trio (gitignored)
  models\                    # GGUFs + smart-turn onnx (gitignored)
  config\
    models.json              # NOT WRITTEN. Its absence is load-bearing: see
                             # "Where Phase 1 actually stands" in section 5
  state\                     # mode.json, settings.json (gitignored)
  workers\                   # per-worker dirs (gitignored)
  hooks\
    worker-settings.json     # the settings file handed to every claude worker
    claude-stop-hook.py      # append jsonl + POST
  ssl\                       # butler's own cert pair (gitignored)
  test\                      # pytest; test_ws_loop.py pattern from spike
    conformance\             # one file per port; every adapter runs the same
                             # suite -- this is what makes 4.10 real (AC-7a)
    assets\                  # recorded/synthesised WAVs (the spike's tests
                             # pointed at a jfk.wav that is NOT on this machine)
  scripts\
    make-fixtures.mjs        # exists: generates the recorded speech fixtures
    make-cert.ps1            # Phase 3; server.py already references it
  package.json               # top level: kokoro-js for the sidecar
  run_butler.bat             # port 8140 -- NOT written yet; the daemon is
                             # started by hand (Appendix C)
```

Python env: `uv venv` + `uv pip install`; run with `python -X utf8`.
No unicode in code, paths, or console output.

**The layout questions are now SETTLED, in favour of the disk over the
document wherever the disk was already reasonable.** An earlier draft of this
section proposed renames that bought nothing; churn in a scaffold is not
progress. The decisions:

- **`web\` stays `web\`.** It is simultaneously the satellite page and the
  web UI; `static\` describes neither.
- **Port stays 8140** (the spike's). 8300 was arbitrary and is now dropped
  from this document -- see Appendix C.
- **`daemon\engines\` stays.** The earlier flat list called for
  `sidecar-tts\` holding one `.mjs`; the TTS path is five modules, so the
  flat target actively misdescribed it. Keeping the spike's forked engines
  as one subtree also keeps the "what we inherited" boundary visible.
- **`server.py` becomes `butlerd.py` inside the Phase 1 commit that rewires
  the entry point**, not as a standalone rename. A rename commit that
  changes no behavior only breaks everyone's muscle memory and `git blame`.
- **No `scripts\fetch-assets.py`.** The GGUFs, the smart-turn ONNX, the wake
  trio and ort18 are already on disk, this is a single-machine project, and
  README already carries the fetch commands. Writing a downloader to
  re-fetch files that are sitting there is ceremony that blocks the first
  red test.
- **`config\`, `state\`, `workers\`, `hooks\` are created by the phase that
  first needs them** (config in Phase 1, the rest in Phase 2), not up front.

One live defect found while checking this: `daemon\server.py:254` references
`scripts\make-cert.ps1`, and `scripts\` holds only `make-fixtures.mjs` --
there is no cert script. That is a D-12 violation already in the tree.
Either write the script in Phase 3 with TLS, or delete the comment now; do
not leave it pointing at nothing.

### 4.10 Ports and adapters -- the six seams

[The count went from five to SIX on 2026-09-26: `Notifier`
(`docs/automations.md` 7, AU-19). The argument, written here as the cap
demands: the owner ruled (R-A) that a finished automation reaches them by
Telegram by default AND that another bot, for another PC or another user,
must be addable with no code change. That is a swappable engine with real
candidates (Telegram today; ntfy and email named), selected per channel
FILE, where a sub-seam inside the runner would put a service's name in the
code that runs automations. `send(title, text) -> NotifyResult` never raises
for a delivery failure, because a failed notification must change nothing
else about a run (AC-472). It is the one port without a registered fake:
the double is a local fake Bot API, since a `fake` kind would be a kind a
channel file could name.]

[Inbound messaging did NOT make a seventh port (2026-10-07, `docs/messaging.md`
MS-20, AC-525). The argument: a channel that can also hear the owner reply
is the SAME engine choice as the channel that sends (one WhatsApp link, one
Telegram bot), selected the same way, so a separate port would be a second
registry naming the same services and a place for the two to disagree.
Nothing swaps independently of `Notifier`. So it is an optional CAPABILITY on
it: a class declares `INBOUND = True` and adds `own(settings)`,
`start(on_message)`, `stop()` and `ask(question, where)`; callers test
`ports.inbound()`, never a kind name. What a reply MEANS (quoted, unquoted,
how many questions are open) is decided once in `notify.Questions`, not per
channel, and every answer goes through `Hub.answer_question`. Revisit only if
an inbound source appears that cannot also send.]

[The count went from four to FIVE on 2026-08-17, by the owner, and it is a
deliberate plan change rather than drift. `Harness` is promoted from the
sub-seam `docs/architecture-seams.md` 3.5 proposed. The argument for keeping it
below the line was that the four ports are what the HUB consumes, while a
harness is consumed by a brain adapter and by the Worker Manager. The argument
that won is simpler: a harness is a swappable engine with several real
candidates on this machine, "switch harnesses without breaking" is a stated
requirement, and a seam that exists in fact but not in `ports.py` is the
condition that produced `Action.harness: str | None`. The cap still means
something -- a sixth port needs an argument written down here, exactly as this
one did.]

Butler has to survive having its speech engines and its agentic middle
replaced. This is not hypothetical on any count: Kokoro may lose on latency
(section 7 names it as the AC-4 risk), parakeet will be beaten by something next
year, the Reflex/Conversation pair may eventually be better served by an
external agent framework, and of the six coding-agent CLIs installed here only
two have ever been observed finishing a turn (4.4). So exactly FIVE interfaces
are formalised -- chosen because they are the things genuinely likely to change,
and capped because every additional seam is a guess about the future paid for
with indirection today.

| port | swaps out | today's adapter | plausible replacement |
|---|---|---|---|
| `Source` | where audio comes from | WebSocket satellite | native mic (P5), a file (tests) |
| `Asr` | speech to text | transcribe.cpp / parakeet | whisper.cpp, Deepgram, Soniox |
| `Tts` | text to speech | Kokoro sidecar (CPU) | Piper, ElevenLabs, Orpheus |
| `Brain` | the agentic middle | router + endpoint or harness (4.4) | an external agent framework |
| `Harness` | which coding agent runs the work | `harness_pi` plus the fake (AC-44) | claude, codex, opencode, crush, gemini |

`Harness` is consumed by TWO callers, which is why it is a port and not a
parameter: `brain_harness` talks to one as a conversation (4.4c), and the Worker
Manager dispatches to one as a background job (4.5). Resume is decided by a
capability record, never by harness name -- `caps.native_resume`, because a name
test is how the predecessor stranded eight dead chats (AC-35).

The whole contract is one file, `daemon/ports.py`, using `typing.Protocol`
so adapters need no base class and no registration ceremony:

```python
class Source(Protocol):          # audio in
    device_id: str
    def frames(self) -> AsyncIterator[bytes]: ...   # 16 kHz mono int16
    def alive(self) -> bool: ...

class Asr(Protocol):             # stateful and streaming, NOT a function
    def open(self, cfg: AsrConfig) -> AsrSession: ...
class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None: ...
    def finalize(self) -> None: ...                 # emits Final even if empty (D-7)
    def close(self) -> None: ...

class Tts(Protocol):             # ALWAYS an async iterator of PCM chunks
    def synthesize(self, text: str, voice: str,
                   handle=None) -> AsyncIterator[bytes]: ...
    async def abort(self, handle) -> None: ...      # dispatched outside the queue (D-4)
    async def warm(self) -> None: ...               # silent sentence at boot (D-10)

class Brain(Protocol):           # an utterance in, typed INTENTS out
    def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]: ...

# Intent is a closed set. The Brain proposes; it never acts.
Say(text) | Clarify(question) | AskConfirm(action) | Dispatch(action)
         | Steer(worker_id, message) | Done(tier)
```

Four shape decisions carry the whole design, and each one is the difference
between a seam and a rewrite:

1. **`Tts` yields chunks, and this is the port decision that paid off most.**
   The service layer always emitted one chunk per sentence, but
   `tts-sidecar.mjs` drained that generator into a single WAV over HTTP, so
   time-to-first-byte equalled time-to-last-byte and a 3-sentence reply paid
   +3252 ms for the collapse (3925 ms over HTTP vs 673 ms direct)
   [MEASURED 2026-08-16]. The engine was never the problem and the output was
   always 24 kHz mono PCM16 -- the TRANSPORT was, and step 8 replaced it
   (1242 ms over HTTP vs 1351 ms direct, same run [MEASURED 2026-08-17]).
   Defining the port as "returns audio" would have baked that 3252 ms in
   permanently and there would have been nowhere to put the fix; defining it
   as a stream cost one `yield` for adapters that cannot stream, and the
   adapter that could stream needed no port change at all to start doing it.
   4.7's sentence-level abort rides on the same iterator.
2. **`Brain` emits intents, not text.** The tempting seam -- "abstract the
   LLM client" -- is the wrong one. A port that returns a reply string
   throws away tool use the moment an agent framework is swapped in, because
   those frameworks emit their own tool calls and streaming. An intent
   stream survives the swap.
3. **`Asr` is a session, not a call.** It is stateful, it emits partials
   mid-utterance, and its `finalize()` must produce a Final even for silence
   (D-7). Anything shaped as `transcribe(bytes) -> str` cannot express
   streaming partials at all.
4. **`synthesize` takes a `handle`, and the handle is part of the CONTRACT,
   not an adapter extension.** `abort(handle)` can only cancel synthesis it
   can name. It was briefly optional, callers omitted it, and the result was
   an abort that matched nothing: the daemon called `abort(turn)` while
   synthesis had registered anonymously, so the code read as correct, the
   fake's assertion passed, and no real synthesis was ever cancelled. A
   handle-less abort is not an abort -- so the parameter lives in the port
   where every adapter and every caller is bound by it, and the conformance
   suite asserts on the CALLER as well as the callee (D-18).

**The Brain never gets policy, and that is a security property, not tidiness.**
Modes, arbitration, worker spawning, turn-id ownership, and Phase 2's spoken
repeat-back over a WORKER dispatch (AC-9) all stay in butlerd. A `Dispatch`
intent is a PROPOSAL that the Mode Store rules on (the tier-boundary rule
already stated in 4.4). If the Brain could spawn workers directly, then
dropping in an external agent framework would silently bypass the permission
gate that AC-9 and AC-10 exist to enforce -- a swapped brain must not be able
to widen what Butler is allowed to do. It is no longer a claim about what a
CODING AGENT may do: AC-29 was retired on 2026-08-18 and the harness runs
unrestricted, so this boundary is about the mode STORE and about worker
dispatch, and nothing here bounds the agent a `dispatch` verdict reaches
through `AskConfirm`. Every intent also carries the turn id, so a slow external
brain's late answer is still discarded by the existing stale-turn rule (D-6).

**In-process, not IPC.** These are Python protocols and direct calls. No
message bus, no HTTP between ports, no plugin loader.

An earlier draft justified this on latency -- "a bus would spend
milliseconds per hop" -- and that is simply wrong on the facts. Pipecat's
in-process pipeline is not a bus; it costs roughly one `await` per hop,
measured in microseconds [REVIEW pipecat.md]. The draft argued against a
strawman, and arguing against a strawman is how you talk yourself out of
things the strawman was not responsible for.

The honest reason is SIZE, not speed. Pipecat's frame vocabulary is 2302
lines of `frames.py` before a word is spoken, and the endpointing logic
Butler expresses in the 380 lines of `pipeline.py` plus the 367 of
`endpointing.py` -- three policies, not one -- is spread there across a
29-file package of 4,378 lines plus a VAD processor, a controller and an
analyzer hierarchy [REVIEW pipecat.md]. For one user, five ports and a
single reply path, that is not a good trade, and a typed-intent seam does
what the `Brain` port needs at about one percent of the size.

State what the decision costs, so it is a trade and not a slogan. A frame
pipeline gives four things Butler now has to build by hand: a universal
interruption broadcast with a declared UNINTERRUPTIBLE opt-out set (today
`abandon()` cancels wholesale, which is fine only because nothing
side-effecting is in flight -- a spawned worker, a steering line already on
stdin and a `mode.json` write are all Phase 2 things that must survive a
barge-in, and R15 is the recommendation to build that set); an observer
seam for instrumentation (without it AC-4's bench is timing statements
sprinkled through three files, the instrument-you-cannot-trust problem
D-13 warns about); insertability; and in-band ordering between `_speak()`
and the announcer, which AC-14 will have to write as an explicit queue.

The child-process rule stays exactly where it was MEASURED to be necessary
-- Smart Turn and speaker verification, for GIL and BLAS reasons (4.1) --
and is not extended to the ports for symmetry.

**A conformance suite is what makes a port real.** One parameterized test
file per port, in `test/conformance/`, that every adapter must pass. An
interface with one implementation is a guess, so each port ships with two
from day one: the real adapter and the test fake (the stubbed STT that
`test/conftest.py` boots with `BUTLER_TEST_STUB_STT=1` is not a testing
convenience, it is the `Asr` port's second implementation and the proof the
seam exists). AC-7a is the criterion; AC-24 in Phase 5 is the same idea
applied to `Source`, and is now a special case of this rule rather than a
one-off.

**Deliberately NOT abstracted:** the wake detector (it lives in the browser,
openWakeWord is fine, and a port across a network boundary buys nothing),
the satellite protocol itself, and the Mode Store. The worker harnesses are
already adapter-shaped through `config/models.json` and need no port. THAT
  SENTENCE IS SUPERSEDED: `Harness` was promoted to a port on 2026-08-17, with
  the argument recorded at the top of this section. It is kept here, struck
  rather than deleted, because it is the position that lost -- a harness was
  something you only ever DISPATCHED to until 4.4c made it something you also
  talk to, and that is precisely what changed the answer.

---

## 5. Build order

Each phase ends at something the owner actually uses. Every AC in a phase
becomes a failing test before its implementation.

### Phase 1 -- the closed loop (AC-1..AC-7)

The smallest end-to-end thing: daemon + one browser satellite + two tiers +
Kokoro + Smart Turn + wake, always re-arming.

Build steps, in order:

0. **Measure before promising. DONE 2026-08-16.** Both numbers that gated
   AC-4 are now measured and AC-4's 1500 ms survives: Kokoro CPU first
   audio p50 448 ms for a short sentence (`docs/bench-tts.md`), Reflex
   verdict p50 140 ms (`docs/reflex-serving.md`). Two consequences carried
   into the steps below: the splitter must cap the first sentence (4.7),
   and the TTS transport must be replaced rather than ported (step 8).
   Still unmeasured and therefore still able to break AC-4: wake finalize,
   ASR, and the satellite hop, measured together in step 10.
1. **Turn ids move to the daemon. DONE.** Server mints at capture_start,
   client echoes. Small, invasive, and everything downstream discards on it
   -- done before there was more code to touch (4.3).
2. **Red tests first. DONE.** `test_ws_loop.py` became the pytest harness
   spine -- a scripted WS client with recorded WAVs -- plus the protocol v1
   message assertions. No fetch-assets step: the assets were already on
   disk (4.9).
3. **The ports (4.10) and their conformance suites. FOUR OF FIVE DONE.**
   `ports.py` exists and the pipeline consumes only the protocols. `Asr`,
   `Tts`, `Brain` and, since 2026-08-18, `Harness` each have two
   implementations through `test\conformance\` -- `harness_pi` and
   `harness_fake` for the last. `Source` still has no adapter file at all:
   the WebSocket source is inlined in `hub.py`, so it is the one port with
   a single implementation and no conformance file. See AC-7a, which states
   this plainly rather than letting "we wrote the interface" read as done.
   Since 2026-08-17 the engine surface is uniform: `Tts` gained `info`,
   `Brain` gained `warm` and `info`, boot warms all three, and the `ready`
   frame names all three engines (AC-42). `test\conformance\
   test_brain_conformance.py` exists and is fake-only on purpose -- pinning
   the contract before the second implementation is what stops the suite
   describing `brain_endpoint` instead of the port.
4. **Endpointing moves server-side. DONE, and then some.** The energy VAD
   and the EOU policy left `web/app.js` for `pipeline.py`, and the policy
   itself then split into the three modes of `endpointing.py` (4.2).
5. **Wake arbitration in degenerate single-device form. DONE.** The
   candidate window exists and logs every candidate with both clocks (8.3),
   one candidate wins trivially, and the pre-roll ring flushes on
   capture_start -- 13 frames of 32 ms, 416 ms of lookback. Not deferred to
   Phase 3 with the rest of arbitration, because the single-device path
   pays the same round trip and loses the same first syllable (AC-1a).
6. **Stand up the router, and the models API. HALF DONE 2026-08-17.**
   Choosing a model (8.2) was not the work and is finished. The models API
   IS: `daemon/models.py` reads any OpenAI-compatible `/v1/models`,
   `GET /api/models` serves it behind a 60 s catalogue, and the `brain`
   settings group holds a model per role and a favourites list. Verified
   against the real llama-swap: 130 models, and an unknown lookup refused by
   name. Two failures are deliberately kept apart -- an endpoint that is down
   answers 502, never a 200 with an empty list, because an empty picker says
   "this endpoint has no models" and that is a different sentence from "nobody
   answered" (mutation-checked). The single `endpoint_url` this shipped with is
   superseded by connections (4.4b); the catalogue and the two-failures rule
   survive that change unaltered, which is why it is a rewrite of the caller
   and not of `models.py`.
   The picker now renders the discovered list: `web/settings.js` reads
   `choices_from` and every model setting is a `{conn, model}` pair control
   (AC-30, MET 2026-08-17). STILL TO DO in this step: the router's own health
   check at boot, and the keep-warm ping against the 900 s TTL.
   `docs/reflex-serving.md` is a probe log, not a service.
7. **The brain: router, endpoint mode, harness mode. PIECES 1, 3, 4, 5, 6
   AND 7 DONE; PIECE 2 REMAINS** -- `docs/harness-mode.md` phase 7 landed
   COMPLETE on 2026-08-18 in twelve commits, so `brain_harness.py` answers
   real turns against a real `pi` child, the conversation survives a restart,
   and the working directory can be spoken. The spoken write grant landed
   with them and was deleted on the same day by the owner's ruling (AC-29).
   Still open in this step: keep-warm (piece 2) and the two roles piece 4
   deferred (`router_role`). `brain_fake` is still the default, so a plain
   boot is still all-fakes, and harness mode is a deliberate `brain_mode`
   choice rather than a default. Seven pieces, in this order, each landing
   green before the next starts:

   1. **Connections** (4.4b, AC-30, AC-34, AC-37). Moved ahead of keep-warm
      on 2026-08-17: everything downstream must resolve a `{conn, model}`
      pair and find a key before it can call anything, keep-warm included,
      which cannot ping "the endpoint" once there is no longer one endpoint.
      `state/connections.json`, the boot-and-Reload import of pi's providers,
      reference-only auth, the per-connection live model list, and the
      Add-connection form with a per-connection Test button, and a DELETE
      route so the form is not one-way. Two negative tests are written FIRST,
      because that registry holds the owner's real keys: no `apiKey` in any
      response, and nothing under `state/` holding a key value after a save.
   2. **Keep-warm** (4.4a, AC-33). Two settings, one interval task, two
      refusals -- now aimed at the router's CONNECTION rather than at a
      single endpoint URL. Early, so every later measurement runs against a
      warm model instead of being contaminated by a 16-26 s load nobody
      accounted for.
   3. **Measure the router budget. DONE 2026-08-17**, out of order, because
      the model question could not be settled without it. A real verdict
      prompt at a real output length on a dedicated llama-server:
      Qwen3-4B-Instruct-2507 Q6_K at p50 181 ms, p95 514 ms, 20/20 valid
      JSON, measured with the GPU already carrying 5564 MiB. The full table
      and the Ornith-9B result are in 4.4. This supersedes the 15-49 ms
      figure, which was a 10-token prompt with `max_tokens: 1` and proved
      only that the server was not paging.
   4. **The router. DONE 2026-08-17**, except the two roles. Grammar-enforced
      verdict with `route` declared first, the schema built per turn so
      `steer` is absent when nothing runs, and R9's negative test in the form
      that can actually fail: the schema is walked and any `not`, `if/then`,
      `oneOf` or `anyOf` fails the test, because llama.cpp drops those
      silently and a constraint it drops is a suggestion (D-11). What the
      schema cannot say -- "answer is required when route is answer" -- is
      checked in code, and a route with a missing payload joins the 3.8
      fall-through. `router_role` is NOT built; see 4.4 for why it waits for
      a harness.
   5. **Endpoint mode. DONE 2026-08-17**, and the `Brain` conformance suite
      has its second implementation, so AC-7a's Brain half is closed. The
      reply streams and is yielded one sentence at a time, the two token caps
      land with it (AC-38/39/40), and barge-in now closes the HTTP request
      rather than only stopping the reader -- the first time AC-43 is proved
      against something that can actually keep generating.
   6. **Harness mode** (4.4c). **DONE 2026-08-18.** The session store, the
      resume capability split, and the transposed defect rules -- including
      the ones an earlier prototype got wrong, listed there as things NOT to copy. Built
      to `docs/harness-mode.md`'s own step numbering, which is NOT this
      list's: 7.1 the port and its fake, 7.2 the pi adapter, 7.3 the
      child-lifetime rule, 7.4 the brain that talks to a harness, 7.5 the
      grant, 7.6 sessions, 7.7 the spoken project switch. This list's piece
      5 (endpoint mode) came before all of them. What that spec got wrong is
      recorded in its own closing section rather than restated here.

      Three of those seven did not survive the day they landed. The owner
      read the finished phase on 2026-08-18, ruled it overly complex, and
      took four rulings (harness-mode.md section 10, which is no longer a
      proposal): step 7.5's grant was DELETED whole and AC-29 retired; 7.4's
      held-open child became one child per MESSAGE and AC-46 was replaced;
      7.6's session directory became one flat chat file per conversation and
      AC-35 was rewritten. 7.1, 7.2, 7.3 and 7.7 survive untouched. The suite
      went 504 to 465 collected across the five commits that did it.
   7. **The orphan rule (AC-36). DONE 2026-08-18**, as harness-mode step
      7.3. The mode-to-tools freeze that used to share this line was
      harness-mode step 7.5 and was RETIRED on the same day (AC-29):
      `brain_mode` still defaults to `endpoint`, but it is now a choice
      about which brain answers rather than a gate, and nothing bounds what
      the agent may do once it is chosen.

   The sentence splitter, bundled into this step originally, IS done and
   tested (`speech.py`). Route `dispatch` still has only its Phase 1 answer
   -- `Dispatch` and `Steer` are spoken as "I cannot run workers yet.", a
   defined behaviour rather than an unhandled branch, and nothing routes to
   it from a model. `AskConfirm` is the one that grew up: in harness mode it
   is the ONLY route from a routed instruction to the coding agent --
   `hub._on_ask_confirm` hands it straight to `brain.proceed()` -- and it
   refuses aloud, naming the work ("I would need to X, but I cannot do that
   yet."), only when no brain can carry it out itself (`CAN_PROCEED`), which
   is every endpoint boot and all of Phase 2's dispatch until the Worker
   Manager exists. It used to speak a repeat-back and park there; that
   ceremony went with AC-29 on 2026-08-18. Both refusals are asserted in
   `test/test_conversation.py`, on the WORDS and over two turns -- until
   2026-08-18 this paragraph claimed a test that did not exist, which reads
   as coverage to the next person deciding what is safe to change (D-12).
8. **Streaming TTS transport with a cancel route. DONE 2026-08-17.**
   `tts-sidecar.mjs` writes one framed PCM chunk per sentence and takes
   `POST /abort`; `tts_kokoro.py` reads the frames and tells the sidecar to
   stop. The two xfails are gone -- not deleted, turned green. Measured on
   the day, all comparisons within one run because the machine was running
   at about half its 2026-08-16 throughput for unrelated reasons
   (`docs/bench-tts.md` section 3a):

   - Three-sentence reply, time to first audio over HTTP: 1242 ms against
     1351 ms for the in-process generator. The +3252 ms transport penalty is
     GONE rather than reduced -- the two paths now agree within noise.
   - Next reply's first audio after a barge-in, on a ten-sentence call:
     7887 ms before, 3736-4392 ms after. **This is NOT the "about zero" this
     plan predicted**, and the reason is in 4.7.
   - No audio is lost in the framing: response size is the same PCM plus
     16 bytes per frame and one end frame, checked on all four bench cases.

   Two things fell out of the same rewrite because they could not be fixed
   without it. `voice` is now per REQUEST, which turned a live AC-7b
   violation green (the sidecar bound one voice at boot, so the settings
   page reported success, the wire said `bm_george`, and the room heard
   `af_heart`). And a failure that happens after the 200 now travels as an
   error frame instead of a reply that silently stops early.
9. **Satellite page. DONE, and past its brief.** app.js/wake.js forked, the
   spike's speak path replaced with the worklet, partial/final/speak wired,
   state pill. Since added: the drain gate, the three audible cues with
   microphone suppression (4.7), the turn-mode display -- without it, in
   endword mode there is no way to tell "still listening" from "stuck" --
   and the whole settings panel (`web/settings.js`, 4.6a).
10. **Boot warmup DONE; the AC-4 bench NEVER RUN.** Warmup pays the ASR,
    Kokoro and smart-turn cold starts at boot (D-10), and warming the
    RECOGNIZER was itself a defect fix: the first version warmed speech
    only, while its own docstring cited the multi-minute STT cold start as
    the reason it existed. The bench that records wake-to-first-audio
    end to end, and the concurrent STT-plus-Reflex measurement 8.2 asks
    for, do not exist. AC-4 is therefore still un-falsified rather than
    proven, and it cannot be proven at all until step 6 and step 7 give it
    a real Reflex call to time.
11. **The re-arm gauntlet: PARTLY DONE. The soak: NEVER RUN.** AC-2's
    terminal paths and AC-5a's playback gate have tests; two AC-2 paths are
    deliberately still uncovered and named as such in
    `test/test_protocol_v1.py`. The overnight soak with per-process RSS
    (AC-7) has not been run once, and the one leak found so far -- the
    smart-turn child outliving a hard-killed parent -- was found by
    noticing orphans on the machine, not by a soak.

Definition of usable: the owner talks to Butler at the desk all day.

Deliberately NOT in Phase 1: workers, permission modes beyond a hardcoded
`secretary`-shaped read-only posture (no dispatch exists yet, so nothing
to gate), multi-satellite arbitration under contention, speaker
verification, native mic, autostart, TLS for remote devices (desktop
localhost is a secure context; getUserMedia works), tray/hotkey/HUD,
conversation memory.

### Where Phase 1 actually stands

Stated bluntly, because the step list above is easy to read as further
along than it is. Suite: **465 passed in 301 s** [MEASURED 2026-08-18, after
the owner's four rulings deleted 39 of the 504 this phase peaked at]. Two
earlier runs the same day were 464 passed with 1 skipped, and the difference
is entirely the two LIVE pi rows: they skip when the configured provider is
not answering, or when the model answers without complying with what the row
asks of it, and both are facts about the day rather than gaps.
Nothing in the suite is waiting on unwritten work: the last two
strict xfails were the Kokoro transport and step 8 turned them green.

**What Butler is now.** A router in front of either an ENDPOINT or a CODING
AGENT, and since 2026-08-18 nothing in between: the agent runs at full
capability (AC-29, retired). Start the daemon, open the
page, say "hey jarvis": the pre-roll flushes so the first syllable survives
the round trip, partials stream while you talk, the turn ends by whichever of
the three policies `turn_mode` names, you are warned once before it does, the
reply is spoken, the daemon holds the turn open until your speaker is
actually quiet, and it re-arms. With `brain_mode` set to `harness` the same
router hands a substantive utterance to a real `pi` child instead of an
endpoint: one fixed line is spoken BEFORE the spawn, covering the 1.5-2.3 s a
fresh child costs (AC-46), and then the answer streams. It writes, and runs
`bash`, in every permission mode, with nothing between a routed utterance and
the agent but the router's verdict and the configured project list (AC-29,
retired 2026-08-18). "Work in project butler" moves the
child, and a name that is not on the list is refused aloud. The conversation
survives a daemon restart; the child does not survive the TURN, let alone the
daemon. Every knob is on a settings page served from the daemon's own schema:
**46 settings in 7 groups, 5 of them restart-only** [READ FROM CODE
2026-08-18].

**What does not work.** In order of what it costs the owner:

- **`config/models.json` is now a LOCAL file the owner keeps** (transcribe.cpp,
  kokoro, a CPU ASR backend), gitignored like `state/` and `models/`. A fresh
  clone with no such file still boots the ALL-FAKES default -- fake recognizer,
  fake speech, fake brain answering "Let me think about that" -- and the tests
  stay hermetic against a present one through `BUTLER_CONFIG`, which conftest
  forces at a nonexistent path (2026-08-18). The brain is still the
  `brain_mode` setting, never named here (AC-7b). What remains open is the
  DAEMON-side gap this exposed: the `asr_backend`/`asr_latency` SETTINGS do not
  reach the adapter -- only `config/models.json`'s `asr_engine` does -- so
  choosing a CPU ASR backend needs the file, not the settings page.
- **`router_role` (route/assist) is NOT built.** Its real job is rewriting an
  utterance before a HARNESS sees it, and there is now a harness to rewrite
  for, so the reason it waited has expired (AC-28).
- **Nothing can be DISPATCHED.** There is no Worker Manager, so the `Harness`
  port has one caller rather than the two that justify it, and all of Phase 2
  -- modes beyond the read-only posture, workers, narration, the announcer --
  is unbuilt. Nothing anywhere speaks OUTSIDE a turn, and nothing is shown
  outside one either: `hub.notify`, the shown-not-spoken line, went with the
  grant on 2026-08-18 and had no other caller, so a thing that wants to be
  heard without being asked has to build the announcer (AC-14) rather than
  work around this.
- **AC-4 has never been timed end to end.** The bench does not exist. The
  24-hour soak (AC-7) has never run either.
- **`Source` has no adapter file**, so it is the one port of five with a
  single implementation (AC-7a).
- **Backpressure (D-8, R7) is a contract in 4.3 the code ignores.**

**What changed on 2026-08-18.** `docs/harness-mode.md` phase 7 landed
complete in the morning: the fifth port and its fake, `harness_pi`, the Win32
job object that kills every child with the daemon, `brain_harness`, the
spoken write grant, the session store, and the spoken project switch. Two
reviews after those steps had been called done found two consent holes in the
grant.

Then the owner read the whole of it, ruled it overly complex, and took FOUR
rulings, which section 10 of that document works out and which shipped the
same day:

1. The spoken write grant is DELETED, not fixed. AC-29 is RETIRED, both
   consent holes stop existing with the ceremony that had them, and the
   coding agent runs at full capability with `bash` in every permission
   mode.
2. A child is spawned PER MESSAGE and disposed at the end of the turn.
   AC-46 is REPLACED -- it used to require the opposite. The 1.5-2.3 s
   spawn is now paid on every question and covered by one fixed line
   spoken BEFORE it.
3. Sessions become ONE flat JSON file per conversation, rewritten whole
   (`daemon/chats.py`, matching the reference implementation).
   `daemon/sessions.py` -- `meta.json`, `events.jsonl`, rotation, the
   `new`/`resumed`/`lost` vocabulary -- is deleted and AC-35 is REWRITTEN.
4. Every criterion this contradicts is amended with the losing argument
   recorded, which is what AC-29, AC-31, AC-35, AC-44, AC-46 and AC-49
   above now do.

What that cost is in 10.5 and is not softened here: about two seconds of
dead air per question, and a wake-word false accept now reaching a
full-capability agent with nothing between it and a write but the router's
verdict, the project list and the owner's own room. Each of the five commits
was REVIEWED before the next one started, and every review found something
the implementer had missed -- three of four breaks out of the turn generator
leaking a live `pi` tree, and a re-attach test that survived the defect it
was named after.

### Phase 2 -- modes and workers (AC-8..AC-14)

1. Mode store + `/api/mode` + broadcast + localStorage grep gate (AC-8).
2. Confirmation flow: repeat-back synthesis, affirmative parsing,
   fail-closed timeout (AC-9). Asymmetric mode changes, voice-down /
   UI-up only (AC-10).
3. Worker manager for `claude`: spawn with the stream-json flags, eager
   stdout drain (AC-13), events.jsonl, tool-event narration, steering
   line, Stop/Notification hooks + receiver + boot reconcile
   (AC-11, AC-12). The FIRST red test runs the real CLI once and pins
   three things at once, none of which are assumed here (4.5, 8.7): the
   stdin user-message schema, whether the initial prompt is positional or
   the first stdin line, and the flag that installs Butler's own settings
   file so hooks fire in an arbitrary `cwd`. Then implement the completion
   rule from 4.5 -- idle grace, close stdin, exit is terminal -- because
   the first Stop hook is a response boundary, not the end of the worker.
4. The `steer` route: verdict schema extension, live-worker list in the
   Reflex prompt, single-worker binding, ambiguous-address fallback to
   clarify (4.4).
5. Announcer priorities + the AC-14 state rules + turn-boundary queue +
   held announcements for a disconnected user (AC-14a). One voice; the
   announcement names the worker.
6. pi and codex as one-shot `-p` workers (exit = completion event).

Usable: dispatch real coding work by voice, steer it, hear it finish.

NOT in Phase 2: voice escalation (UI click only), speaker verification,
satellites beyond the desk, worker-to-worker anything, cost tracking.

### Phase 3 -- satellites (AC-15..AC-20)

1. TLS: butler's own cert (mkcert or openssl against the local mkcert CA)
   AND document the `tailscale serve` route -- an earlier prototype verified tailscale
   serve proxies WebSocket upgrades with a real Let's Encrypt cert, which
   avoids installing any CA on the phone [MEASURED-PROTOTYPE].
2. Real arbitration: window, score compare, reject losers, bind turn to
   winner (AC-15, AC-16). Note honestly: cross-device score comparability
   is imperfect (different mics, gains); it is still strictly better than
   first-to-report. Open question 8.3 tracks normalization.
3. Satellite liveness: heartbeat, capture-drop turn teardown (AC-17).
4. iOS: verify wake loads (ort 1.18 pin, wasm mimetype, sequential
   session creation -- all already in the forked code); port the
   `statechange` + `visibilitychange` resume pattern from
   useAudioSession.ts (AC-19); run the lock-screen experiment and record
   the answer (AC-20, section 8.1).

Usable: Butler in a pocket and on every room's spare screen.

NOT in Phase 3: speaker verification (a TV can still wake it -- but modes
already stop it doing damage), native mic, autostart.

### Phase 4 -- speaker verification (AC-21..AC-23)

Enrollment UI, ECAPA child process, wake+command scoring, gates per 4.8,
voice escalation flow. Threshold tuning against the owner's enrollment and
recorded impostor audio (the TV test is a literal recorded-TV test).

NOT in Phase 4: multi-user profiles (single owner only).

### Phase 5 -- native presence (AC-24..AC-26)

Native mic source (Python capture via a maintained lib -- choice recorded
in 8.5), native local playback for the desk speakers, autostart (Task
Scheduler), global push-to-talk hotkey, optional tray icon; HUD pill and
any native shell (Tauri, NOT Electron [DECIDED]) only if still wanted.

---

## 6. Defect catalogue -> binding design rules

An earlier prototype's CLAUDE.md (2026-07-23 onward) is a paid-for defect catalogue for
exactly this subsystem, and D-1 through D-16 come from it. D-17 through D-24
are Butler's own, paid for on 2026-08-16. Each entry below is a rule in
Butler with a test.

- **D-1 Every terminal path re-arms.** The dashboard's Phoenix died on a
  sticky error string that switched the wake agent off. The spike fixed
  this by construction (goListen() from every exit); Butler keeps the
  invariant and AC-2 is its gauntlet. Never latch an error into a state
  that disables the microphone.
- **D-2 Server-side settings.** localStorage is per-origin; four origins,
  four diverging copies. Mode store and all voice tuning live in
  `state/` on the daemon (AC-8).
- **D-3 One playback worklet, queue-fed.** Per-chunk scheduled sources
  drop late chunks and gap; the worklet self-heals (4.7).
- **D-4 Abort outside the queue; sentence granularity; fail loud twice.**
  The TTS cancellation trilogy (4.7).
- **D-5 Split cold-start timeout from synth watchdog.** (4.7).
- **D-6 Stream ownership by id.** An earlier prototype's `stream_reset` ignored the
  session id on the wire and destroyed whichever stream was live; any
  reset/finalize in Butler's protocol names its turn and is ignored if
  stale (the spike's turn-id discard, extended to every message).
- **D-7 The final is the turn's END, not its text.** An empty turn must
  still emit `final`, or claims/timers leak and the next wake is refused
  as busy (AC-6).
- **D-8 Backpressure is the server's job.** Check the socket buffer, not
  a client-reported signal (4.3).
- **D-9 Copy buffers per consumer.** Never transfer one ArrayBuffer to
  two listeners (4.7).
- **D-10 Warm everything at boot.** The 2-minute cold start was paid
  mid-conversation (4.2).
- **D-11 Prefer counted enforcement over prose.** An earlier prototype's loops ignored
  ALL-CAPS notes in prompts; where Butler must constrain a model (Reflex
  verdict schema), the constraint is parsed and enforced, not requested.
- **D-12 Stale prose is the recurring hazard.** Comments and docs that
  contradict code cost an earlier prototype wrong designs repeatedly. Butler's rule:
  when behavior changes, the same commit updates the comment/doc or
  deletes it.
- **D-13 Validate the instrument.** At least eight recorded instances of
  a test/harness accusing the app falsely (dialog-vs-modal, fake-audio
  flags, stale dist, regex-escaped placeholders...). Butler's discipline:
  every new guard is mutation-checked -- break the code, confirm the
  mutation applied, watch the RIGHT test fail, restore to green.
- **D-14 Wake re-arm needs detector context.** Resetting the wake
  detector clears its 16x80 ms embedding context; after a reply, feed it
  through a short cooldown with firing suppressed instead of resetting
  cold, or the next wake is deaf for ~1.3 s [MEASURED-PROTOTYPE].
- **D-15 Barge-in gets extra defenses.** When wake-during-speak is
  enabled, use a raised score bar, a consecutive-frame requirement, and
  an onset grace window; an earlier prototype shipped +0.15 bar / 2 frames / 700 ms as
  reasoned starting values (guesses, not measurements -- tune by ear).
- **D-16 A slow consumer must never own a producer's exit.** The claude
  30 s stdout-drain gotcha generalizes: every pipe from a child is
  drained eagerly into Butler's own buffer (AC-13).

D-17 onward are NOT inherited. They were found in Butler's own code on
2026-08-16 by comparing it against the prior-art review (section 9) --
eleven defects in total, all in code written that morning, none visible to
the tests that existed at the time. That is the meta-lesson and it is worth
more than the list: the tests could not see them because the tests were
written by the same reasoning that wrote the code.

- **D-17 A flag set before an `await` needs a `try/finally`, or it latches.**
  `WakeArbiter._resolving` was set, then the window was awaited, then the
  flag was cleared. The resolving caller is a satellite's own handler task,
  and that task is cancelled when the satellite disconnects -- a normal
  event, not an exception. So the flag stayed True forever, every later
  candidate returned an empty ranking, and NO device on ANY satellite could
  capture again until the daemon restarted. Reachable by closing a browser
  tab at the wrong moment. The rule: any flag that gates a SHARED resource
  and is held across an await is released in a `finally`, and the shared
  state it guards is cleared on every exit including cancellation. This is
  D-1's microphone-off latch arriving through a door D-1 did not name.
- **D-18 A contract needs an assertion on the CALLER, not just the callee.**
  Three times in one session a fix read as done and was not, because the
  test drove the helper directly and never checked that production code
  reached it: the anonymous TTS handle (`abort` matched nothing because
  `synthesize` was called without one), the playback gate (deleting it from
  `_reply` left every test in its own file green), and per-session barge-in
  (abandoning only the session that heard the wake passed AC-5 identically,
  while the desk talked over the phone's answer). The rule: every contract
  gets one test that mutates the CALL SITE, not only the implementation. A
  suite that exercises the helper proves the helper works; it says nothing
  about whether anything uses it.
- **D-19 "I finished sending" is not "you finished playing".** Anywhere a
  producer's completion is used as a stand-in for a consumer's completion,
  the gap is the buffer -- and here the buffer is seconds of audio. Named
  separately from D-18 because it is a modelling error rather than a testing
  one: two genuinely different events had one name. `speak_end` versus
  `playback_done` (4.3) is the instance; the rule is that the daemon never
  infers a remote state it can be told (bounded, with a timeout, so being
  told is never load-bearing for re-arming).
- **D-20 Record what was SPOKEN, not what was generated.** An assistant
  history entry holds only the segments whose audio actually reached the
  satellite; a segment cut off part-way through does not count, and the
  entry is marked interrupted. Otherwise the model believes it said
  sentences the user never heard, and the next turn re-answers a question
  already answered or refers back to advice nobody received. The most
  corroborated finding of the review -- LiveKit truncates to the
  synchronized transcript, Pipecat changed to the same behaviour in 1.5.0,
  and OpenAI's Realtime API exposes `conversation.item.truncate` for it
  [REVIEW livekit-agents.md, pipecat.md, realtime-and-brain.md]. Written
  into `ports.TurnContext.history` while the history is still empty, so
  Phase 1 step 7 cannot quietly build the bug.
- **D-21 Absolute audio thresholds break under automatic gain control.**
  `getUserMedia` leaves `autoGainControl` on, so the room's amplified noise
  floor is not a constant and can sit above any fixed RMS_OFF. A fixed
  threshold then never registers silence at all, which is a deaf gate, not a
  strict one. Every energy threshold in Butler is a FLOOR that adapts upward
  to the quietest thing recently heard and never downward past its tuned
  constant (4.2). Two sub-rules learned the hard way: compute the thresholds
  BEFORE updating the estimate, and update the estimate only from frames
  already judged quiet -- seeding it from a speech frame lifts the gate above
  the speech that produced it, and the microphone goes deaf.
- **D-22 A stale browser cache makes a shipped feature look unbuilt.** The
  settings panel was reported MISSING. It was not: it rendered 53 controls
  with no console errors and no failed requests in real Chrome. What was
  missing was a `Cache-Control` header. Without one a browser applies
  HEURISTIC caching -- typically a fraction of the time since Last-Modified
  -- and reuses `app.js` from memory without asking, so the open tab runs an
  older build than the daemon serves. The symptom is not an error; it is
  controls quietly absent, which reads as "that was never built" rather than
  "this tab is stale", and the next hour is spent looking for a bug in
  finished code. The rule: everything the daemon serves carries
  `Cache-Control: no-cache` -- revalidate-before-use, NOT no-store, so the
  ETag still answers 304 and the cost is one conditional request. Its
  corollary is D-13 in a new place: reading the markup is not evidence
  either, because an ES module that throws takes the whole page with it, so
  a broken import looks exactly like "fewer controls than before". That is
  what `test/manual/probe_page.py` exists for -- it loads the page in the
  owner's actual Chrome and reports console messages, page errors, failed
  requests and what is really in the DOM.
- **D-23 Block-buffered stdout hides the log a decision depends on.** Python
  block-buffers stdout whenever it is not a terminal, so running the daemon
  under a pipe, a log file or a service manager swallowed everything it
  printed until the buffer filled or the process exited -- the boot line,
  the smart-turn warm confirmation, and every wake-candidate score. That
  last one is the entire evidence base for open question 8.3, being
  collected into a buffer nobody would ever read. What made it hard to see
  is that the log was not EMPTY: the node sidecar is a subprocess writing to
  the same pipe, so its lines appeared while the daemon's own did not, which
  reads as "nothing went wrong". The rule: any process expected to run
  unattended line-buffers its own output at entry, and a log that is missing
  only SOME of its writers is treated as a buffering bug until proven
  otherwise.
- **D-24 A child process must be told to die with its parent.**
  `ProcessPoolExecutor` shuts its workers down through an `atexit` hook, and
  nothing on Windows runs `atexit` for a `TerminateProcess` -- which is what
  `subprocess.terminate()` does, and that is how the test harness stops
  every daemon it boots. The smart-turn child then blocked forever on a call
  queue it also holds the write end of, so it never saw EOF either: seven
  orphans at 46-100 MB each accumulated in one afternoon, one per terminated
  test daemon. That is an AC-7 soak failure that would have taken days to
  surface and would have been blamed on the wrong process. It has a second
  home: the Kokoro sidecar had `atexit` and nothing else, and seven of those
  were found on 2026-08-17, each still holding a `tts-worker.js` child.
  The rule, in two halves. A multiprocessing child watches its parent and
  exits when the parent does (`multiprocessing.parent_process().join()`, one
  sleeping thread, no polling) -- `endpointer_smartturn` does this and keeps
  doing it. EVERY child, including the ones whose code Butler does not own,
  is also assigned to a kill-on-close Job Object at spawn
  (`daemon/childproc.assign`, AC-36): it is the only mechanism that reaches
  grandchildren and the only one that survives `TerminateProcess`, because
  the kernel closes the handle whatever the dying program did or did not run.
  And AC-7 measures RSS for every process in the tree rather than the daemon
  alone -- because the daemon is the least likely leaker of the set.
- **D-25 The cheap read must not be the thing that loads the model.**
  `asr_transcribecpp.info` opened a throwaway session when its cache was
  empty, to report what the engine had actually resolved rather than what the
  catalogue promised. Honest, and it made `info` a blocking GGUF and Vulkan
  load: MEASURED at 744 ms with a warm page cache on 2026-08-17, against a
  15.85 s cold load for the smallest model here. `hub._hello` reads it
  synchronously on the event loop every time a device connects, so a single
  failed warm -- which `butlerd.warmup` swallows on purpose, loud but never
  fatal (D-1) -- left that load reachable for the life of the process,
  freezing every satellite, the heartbeat and worker announcements on every
  connect. Two lessons, and the second is the one that generalises. A
  property that is cheap in the warm case is not cheap; it is BIMODAL, and
  the cold branch is the one that runs on the worst day. And the guard test
  written for exactly this property was itself broken -- it read `info`
  twice and compared, which an adapter reloading on every call also passes,
  and it built its own fake while accepting the parametrized fixture, so the
  real adapter's case ran green without ever calling the real adapter. The
  rule: a "must be cheap" contract is asserted with a CLOCK against every
  adapter, never with an equality check against a convenient one (D-13).

- **D-26 A turn longer than the recognizer can finalize returns SILENCE.**
  FOUND 2026-08-19 while speccing `docs/ambient.md`, and it is live in the
  owner's own configuration rather than hypothetical. `TurnPipeline`
  finalizes ONCE, at the end of the turn (`pipeline._end_turn`), so a turn's
  entire audio reaches the recognizer as ONE utterance. Decode cost on this
  machine is SUPERLINEAR in utterance length -- MEASURED with the real
  adapter on the CPU backend: 1 s costs 0.09 s, 5 s costs 1.69 s, 20 s costs
  13.64 s, and 30 s exceeded the adapter's then-fixed 15 s finalize timeout,
  at which point the adapter ended the turn with an EMPTY final so the claim
  released. The practical ceiling was somewhere near 22-25 seconds of AUDIO,
  and it is audio duration rather than speech duration: a 30 s turn that was
  half silence returned 0 characters on the same measurement.

  It matters because the ceilings above it are far higher. `turn_mode=endword`
  -- what the owner runs -- carries `END_WORD_MAX_MS = 300_000`, five
  minutes of "keep talking until you say send it", and
  `absolute_max_turn_ms` defaults to 120 s. So the two settings that exist to
  let someone dictate at length are the two that produce silence, and the
  failure takes the worst available shape: not an error, not a truncated
  answer, but a turn that completes normally having heard nothing.

  The lesson generalises past ASR. A timeout on a component whose cost grows
  with input, sitting under a ceiling that permits large input, is not a
  safety net -- it is a silent data-loss threshold, and nothing in the system
  compares the two numbers. `test/manual/spike_ambient.py` is the instrument.

  PARTLY FIXED 2026-08-19, and the two halves are different claims. The
  SILENCE is gone: `ports.Final` carries `failed`, every empty Final the
  adapter synthesizes for itself (the expired budget and the engine-error
  path) sets it, and `hub._on_final` speaks `Hub.ASR_FAILED` before any other
  branch, so a lost utterance no longer sounds like a quiet room. And the
  budget now SCALES with the audio fed to that utterance --
  `asr_transcribecpp.FINALIZE_FLOOR_S` 15 s, `FINALIZE_PER_AUDIO_S` 2.0 s per
  second of audio, `FINALIZE_MAX_S` 60 s, derived at the constant from the
  measurements above -- which moves the ceiling from roughly 22-25 s of audio
  to roughly 54 s and leaves short turns byte-identical.

  STILL OPEN: the cost itself. A 60 s hard ceiling is kept because an
  unbounded wait on a wedged engine is D-1, so audio past about 54 s still
  ends the turn without a transcript -- audibly now, but still without one --
  and `endword`'s 300 s remains far above anything one decode can cover. A
  minute of waiting before that spoken failure is also a poor answer in its
  own right. `docs/ambient.md` AC-71 is the actual fix (never hand the
  recognizer more in one utterance than it can finalize) and it is unbuilt;
  it needs mid-turn segmentation, which touches the adapter's worker thread
  and the event loop and was deliberately left out of this change.

D-27 through D-32 are the page's own, found on 2026-08-19 by rewriting the
visualiser (`docs/lattice-wave.md` section 1) and by driving the shipped page
in a real browser (`docs/satellite-ux.md` section 10). Five are fixed, one is
half fixed, and one was withdrawn after acting on it made things worse.

- **D-27 An instrument that can only rise is a running maximum, not a
  reading.** `app.js` constructs the visualiser with `externalScore: true`, so
  the only writer of the wake score was `pushScore`, which was
  `score = Math.max(score, v)`; the decay path sat behind both `if (!inAn)` and
  `if (!externalScore)` and could not run once a microphone was attached. The
  readout and the trace climbed to the loudest thing that ever happened and
  stayed there for the life of the page. FIXED by AC-78: `pushScore` sets the
  current value and it decays.
- **D-28 A constant and the thing that draws it are one number.** The trace's
  threshold LINE was hardcoded to mid-height while its LABEL was generated from
  `WAKE_THRESHOLD`, so changing the constant moved the text and not the line.
  FIXED by AC-78: the caller passes `opts.threshold` and the line is drawn from
  it. D-12 in pixels rather than in prose.
- **D-29 An instrument shows what arrived, or it shows nothing.** The trace was
  pre-filled with 220 samples of `Math.random()`, so it displayed invented data
  for its first nine to eleven seconds. In a project whose rule is that an
  untagged number is a guess wearing a uniform, a drawn one is worse. FIXED by
  AC-78: the trace starts empty.
- **D-30 A styled state the state machine cannot enter is one decision written
  twice.** `[data-state="wake"]` and `[data-state="error"]` had stylesheet rows
  AND canvas palettes -- `error` had a whole branch in `visualiser.js`
  (`meshErr`, `nodeErr`, `errKick`, its own motion model) -- while `paint()`
  could assign neither, so the page drew a blue wave inside a red page and the
  cyan row was never seen at all. HALF CLOSED 2026-08-19: `error` is reachable
  and its branch draws (AC-91, AC-93). CLOSED 2026-09-26 (ruling R5, CLAUDE.md
  2026-09-16 backlog): the owner ruled armed-and-quiet does not get its own
  bright cyan, so `[data-state="wake"]` and its light-theme override were
  DELETED from `web/index.html` rather than left as a row nothing could enter
  -- `STATE_VIEW[WAKE].attr` stays `"idle"` and the armed page draws the idle
  hue. The rule: the stylesheet and the state machine are checked in one
  commit, exactly as the canvas and the stylesheet already are.
- **D-31 A CSS rule keyed on an attribute nothing sets never fires.**
  `index.html` styled `.switch[aria-checked="true"]` and no code ever set
  `aria-checked`, so toggling a boolean setting left border and background
  byte-identical: the designed "on" appearance shipped and could never appear.
  Nothing errored, which is why it survived. FIXED 2026-08-19 by AC-104, keyed
  on `:has(input:checked)` -- the only thing that is actually true.
- **D-32 WITHDRAWN as stated; what survives is a rule rather than a defect.**
  It was written as "a client-side abandonment budget shorter than the
  server-side policy it is guarding", found at the page's 30 s against
  `END_WORD_MAX_MS`'s 300 s. Acting on it made things strictly worse: raised to
  300 s, the page sat in CAPTURE, `onWakeFire` dropped every wake, and "hey
  jarvis" answered once and then nothing for five minutes -- reverted within
  the hour (AC-92, `docs/satellite-ux.md` 11.1). The rule that survives: in a
  turn mode with no silence ending, the page's abandonment budget is the user's
  ONLY way out of an utterance they walked away from, so it is not free to be
  as long as the policy's ceiling. Two lessons generalise past it and are worth
  more than the finding was. A guard's value depends on WHICH DIRECTION it
  fails: a false abort costs one turn the owner can repeat, a missing abort
  costs every turn until the timer expires, so "30 versus 300" is a real
  comparison that says nothing about which number is right. And when a
  criterion offers a cheap variant, check whether it is the same criterion --
  the daemon telling the page the LIVE ceiling tracks the mode, and a constant
  raised to the maximum any mode allows does not.

- **D-33 One matcher that lowercases and one that does not is two matchers.**
  `EndWordEnd` compiled its end-phrase pattern from lowercased phrases with no
  `re.IGNORECASE`. `_heard_end_phrase` lowercased the transcript before
  searching, so DETECTION was correct and every turn ended when it should.
  `strip()` ran the same pattern over the ORIGINAL text, where a recogniser has
  capitalised after sentence punctuation -- so it matched nothing and silently
  returned the transcript unchanged. Every endword turn therefore reached the
  brain, and in harness mode a full-capability coding agent, with its control
  phrase still attached: "Can you hear me now? Send it", stored verbatim in
  `state/chats/`. Fixed 2026-08-20 by one flag on the compile.
  Two things earned it a number rather than a quiet patch. **It was invisible
  to five green tests** in `test_endpointing_policies.py`, all of which fed
  lowercase input -- the one case a real transcript almost never is, because
  the fixtures were written by hand and the transcripts are written by an ASR.
  And it is D-18 wearing new clothes: the detector and the stripper are one
  contract expressed twice, and only the half with a test was right. The test
  that catches it asserts on CAPITALISED input and was confirmed red before the
  fix (D-13).
  Found by reading the owner's own conversation records while investigating
  whispered dictation, which is the second defect that file has surfaced by
  looking at production data rather than at code
  (`.references/whisper-input.md` 1.3).
- **D-34 A refusing backend fails fast; a SILENT one hung the turn forever.**
  `brain_harness._answer` read the harness event stream with `async for` and
  no deadline. A child that REFUSES a prompt answers `stopReason: error`, the
  adapter turns it into `Failed`, and the turn has always ended -- which is
  why this hid: every failure anyone had seen was the fast, visible kind. A
  child that ACCEPTS the prompt and then emits nothing had no bound at all.
  The room heard the one fixed line spoken before the spawn (D-10) and then
  silence, and the microphone stayed held (D-1).
  In `butler.log` on 2026-08-08 that is turn 1: a wake at score 0.871, a user
  message in `state/chats/`, and then 158 seconds of nothing -- no reply, no
  error line, no terminal event. The two turns before it failed in 1.4 s each
  with `Connection error.`, which is the refusing case and is what made the
  hang look like the same fault. It was not.
  The owner's diagnosis of the CAUSE was right and is recorded because it
  changed the fix: the backend behind `zen_bridge` was down. With it up, the
  same configuration answers in 3.05 s [MEASURED 2026-09-09]. So this defect
  is not why Butler was unusable that day; it is what turned one dead
  provider into a turn that never ended.
  Fixed 2026-09-09 by `HARNESS_IDLE_S` = **180 s**, a deadline on SILENCE
  rather than on duration. That distinction is the whole design: a coding agent legitimately
  thinks for minutes and emits deltas and tool events throughout, so a total
  budget would cut off real work, which is the more expensive failure. Only a
  stream producing nothing at all trips it, and it exits through the existing
  `_give_up` path so nothing new was invented.
  MEASURED before the fix: past 25 s with one `Say` and no terminal event.
  After: the turn ends, the room is told, and the next thing said is
  answered. `test/test_harness_idle.py` holds both halves, and the second one
  -- a harness that keeps talking past the budget in TOTAL is never cut off --
  is the one that matters more, because a total-duration bound would pass the
  first test and destroy real work.
  **The number is measured, and the first guess was wrong.** Six real pi turns
  on the owner's own model, 146 gaps [MEASURED 2026-09-09]: p90 3.25 s, p95
  7.27 s, p99 40.2 s, max **78.6 s**. The constant was first written as 120 s
  on the reasoning that it was "well past any gap seen in a healthy turn", and
  the measurement says it would have KILLED one -- a turn that finished
  correctly after a 78.6 s silence while a parallel tool batch ran. That gap
  scales with the DIRECTORY a tool searches rather than with the model, so a
  larger project pushes past it with nothing wrong; 180 s is about 2.3x the
  observed maximum for that reason. The pre-first-event wait is not the long
  pole (7.4 s at most), so one uniform deadline armed at the prompt and
  re-armed on every event is enough.
  **A silence timeout is NOT replayed.** The replay-once rule exists for a
  transient blip, and a child that accepted the prompt and then said nothing
  for a whole budget is not evidence of one -- while replaying it costs a
  SECOND full budget before the room hears anything, six minutes at 180 s.
  That is worse than the wrong answer the replay branch exists to avoid.

- **D-37 espeak READ UTF-8 AS LATIN-1 AND SPOKE THE CHARACTER NAMES.**
  Found 2026-09-10 by the owner, who reported that Portuguese "doesn't read
  correctly when accents are present". `tts-phonemes.phonemize` passed `-b=1`
  to espeak-ng. That flag names the input encoding and 1 means UTF-8, so the
  intent was right -- **the `=` is the entire defect**: getopt hands the short
  option an optarg of `"=1"`, `atoi` returns 0, and espeak falls back to
  SINGLE-BYTE input. The UTF-8 bytes Emscripten put into argv were then read
  as Latin-1, every accented letter became mojibake, and espeak pronounced the
  NAME of each character it thought it had found: an a-tilde as "a til", a
  cedilla-plus-tilde pair as "libra", "simbolo de copyright" and "ordinal
  feminino".
  MEASURED across the forms, phonemizing c-cedilla-a-tilde: `-b=1` and `-b=2`
  are both broken and identical, because the `=` makes the value irrelevant;
  `-b 1` and `-b1` are CORRECT; `-b 2` and `-b2` are broken and correctly so,
  since 2 means 8-bit; and omitting it works by auto-detection. Fixed as an
  explicit `'-b', '1'` pair rather than by deleting the flag, because
  auto-detection is a heuristic on the text while this is a fact about the
  caller: everything reaching the phonemizer is a JS string and is always
  valid UTF-8.
  MEASURED on the shipped build, "A manutencao do coracao e uma questao
  seria." with its accents, through the real engine: **9.53 s of audio before
  and 2.95 s after**, against 2.88 s for the same sentence with the accents
  stripped. At the phoneme level, 30 groups against 8. Butler was spending
  six and a half extra seconds per sentence reading out punctuation names,
  and every reply in Portuguese was affected.
  **Three things kept it hidden, and all three generalise.** ENGLISH IS
  BYTE-IDENTICAL with and without the flag, so it survived from before the
  Portuguese voice existed and no English test could ever have caught it.
  The broken and working forms differ by ONE CHARACTER, which is what made it
  invisible on review: `-b=1` looks like a deliberate, correct UTF-8 setting
  and is the opposite.
  And this project's own pure-ASCII rule meant every fixture on this path was
  written in unaccented ASCII, so the whole test suite exercised exactly the
  case that works. A rule about SOURCE had quietly become a rule about TEST
  DATA.
  Gated by `test/test_tts_kokoro.py::test_an_accent_does_not_multiply_the
  _phonemes`, which asserts a COUNT rather than a phoneme string -- pinning
  IPA would break on any espeak upgrade, and cannot be written in this
  repository anyway. Two tests beside it stop the count being satisfied the
  wrong way: one proves accented and unaccented text do NOT phonemize alike
  (a phonemizer that stripped accents would also pass a count), and one
  proves English did not move.

- **D-38 THE SPLITTER CUT A TOKEN IN HALF AND DELETED THE SECOND HALF.**
  Found 2026-09-10 from the owner's report that Butler "breaks a word and
  starts the second half of a word on the next spoken phrase". The second
  half never started anywhere: it was dropped.
  `speech._cut_at_cap` searched the head for a clause mark with
  `str.rfind(",")`, `rfind(";")` and `rfind(":")` -- three bare characters
  with no notion of a word, unlike the space-delimited `" -- "` and `" - "`
  sitting in the same tuple. So the mark matched INSIDE `1,000`, `12:30`,
  `3:1`, `https://` and `C:/Users`. It then built the head as a CHARACTER
  slice and the remainder as `words[len(head.split()):]`, a WORD index -- so
  when the slice landed inside token N the partial still counted as a word
  and `words[consumed:]` started at N+1. Token N was neither spoken nor
  carried.
  MEASURED against the unmodified module: `'...we sold 1,000 units...'` came
  back as `['...we sold 1', 'units and then...']`; a fuzz over 4000 generated
  sentences lost **3810 characters and duplicated none**.
  **THE FIX IS THE UNIT, NOT THE PATTERN**, and the mutation harness is what
  proved that. `_cut_at_cap` now answers a WORD index end to end, so a head
  and a remainder measured in different units is unrepresentable rather than
  corrected -- and the `idx > best` off-by-one beside it (a raw offset
  compared against an already-adjusted one) went with the arithmetic that
  needed it. Un-anchoring the mark pattern afterwards deletes NOTHING, which
  is why `test_an_internal_mark_is_not_even_a_PREFERRED_boundary` exists: the
  anchor decides where a cut lands and only the unit decides whether text
  survives, and the second rule had all the tests.
  **Nothing is stripped from the head any more.** A trailing comma is a pause
  the phonemizer already reads, and removing it was a character deleted from
  the reply for no audible gain -- keeping it is what lets the no-loss
  property be stated with no exception carved into it:
  `" ".join(segments(t)).split() == t.split()` for every `t`.
  **Why no test caught it**: `test_nothing_is_lost_for_any_input` claimed
  exactly this property against ONE hand-written fixture in which every `,`
  `;` and `:` was word-final -- the single arrangement the unit mismatch
  cannot corrupt. It is now table-driven plus a seeded fuzz.
  **A SECOND defect in the same area, and it is why the first fired so
  often.** `speech.segments` is called once per `Say` (`hub._speak`) and the
  conversation tier yields one `Say` per SENTENCE, so `index == 0` was true
  for every sentence of every reply and the 8-word FIRST cap was applied to
  all of them; `LATER_SEGMENT_MAX_WORDS = 16` only ever reached the tail of a
  single long sentence. The tight cap buys time to first audio (AC-4) and
  there is one first audio. `_speak` now reads `s.speaking_started` BEFORE
  `_begin_speaking` sets it and passes the matching cap. Asserted on the
  CALLER (D-18), because `segments` cannot know which it is being asked
  about.
  `test/manual/mutate_speech_caps.py` is the D-13 instrument, five rules,
  EXECUTED 2026-09-10 with exit codes only: **5/5 caught**, after row 1
  survived its first run and earned the test named above.

- **D-39 A STOPPED TURN RE-ARMED THE TURN THAT REPLACED IT, AND THE NEW TURN
  THEN NEVER ENDED.** Found 2026-09-11 by `test/manual/e2e_scenarios.py`
  section F, driving a real browser against a real daemon: a turn started
  about three seconds after stopping the previous one NEVER FINISHED. The
  `reply` frames arrived, `speak_end` never did, and the page sat in its
  speaking state with the Stop button showing until it was reloaded. Six- and
  eleven-second gaps were fine, 3 of 3.
  **IT IS A RACE BETWEEN TWO BUDGETS AND NEITHER IS THE ENGINE'S.** `stop`
  reaches `abandon()`, which cancels the reply task. A cancelled task's
  `finally` may still AWAIT -- only a second cancel interrupts it -- so
  `_reply`'s `finally` went on to sit out `_await_playback`, bounded at the
  audio already sent plus `PLAYBACK_GRACE_S` (2.0 s). `abandon()` waits
  `ABANDON_TASK_S` (3.0 s) and gives up. Whenever the stopped turn had sent
  more than a second of audio, the gate outlasted the give-up: barge-in
  returned, the owner's next question minted turn N+1, and THEN the stopped
  turn reached `s._rearm()` -- which took no argument and cleared
  `turn`/`state`/`pipeline` unconditionally. Turn N+1 owned nothing from that
  instant, so `_speak` bailed on `owns()` and `send_for_turn` dropped its
  `speak_end`. Everything after the steal is silent BY DESIGN, which is why
  the daemon log said nothing at all.
  MEASURED 2026-09-11 in the scratch daemon's own trace and stderr:
  `[butler] turn 1: no playback_done within 3.6s (1600ms of audio sent)`
  followed immediately by `[rearm] line 1660 clears turn 2` from a temporary
  print; the adapter-level debug showed turn 2's `synthesize` closing after
  ONE chunk with no error, which is `_speak` returning False on `owns()`.
  **THE FIX IS THAT THE EXIT NAMES ITS TURN.** `SatelliteSession._rearm` now
  takes the turn it is ENDING, required, and returns without touching
  anything when the session has moved on. The check is at the mutation rather
  than at the seven call sites, because a rule enforced at call sites is a
  rule the eighth call site does not have (D-18) -- and `send_for_turn` has
  had exactly this identity re-check since protocol v1 (D-6). D-1 is
  unweakened: skipping the re-arm is only reachable when another turn owns
  the session and has its own terminal path. `ABANDON_TASK_S` was given a
  name in the same change, because a literal that is one half of a race is
  not readable as one; it now decides latency and no longer decides
  correctness.
  **Why no test caught it**: every re-arm test in the suite was about a turn
  that ended while it still owned the session. `test_stop.py`'s four-turn
  case drives turns strictly in sequence, and the fast fakes send so little
  audio that `_await_playback` returns at once -- so the window could not
  open. The witness is
  `test_stop.py::test_a_stopped_turn_cannot_rearm_the_turn_that_replaced_it`,
  which needs a double that sends audio BEFORE it parks and both budgets
  shrunk. D-13 EXECUTED 2026-09-11, exit codes only: removing the ownership
  check reddened the aimed test (exit 1) with the rest of the file green
  (exit 0), and the restore came back exit 0; each of its two assertions was
  then confirmed to catch the mutation ALONE.

- **D-40 AN END PHRASE MISSES WHEN THE RECOGNISER SPELLS IT DIFFERENTLY, AND THE
  TURN RUNS TO ITS CEILING.** Found 2026-09-15 from the owner's report that the
  end word answers "only in English" and that a Portuguese turn "keeps
  capturing forever and never sends it". Diagnosed from the LAPTOP's own
  `state/voice-trace.log` -- no reproduction and no guess: FIVE of that file's
  nine long-form turns have no `turn_end` line at all, and every turn that DID
  close contains the literal string `send it` in its committed tail.
  **THE MECHANISM IS A LITERAL MATCH AGAINST A TRANSCRIPT NOBODY WROTE.**
  `EndWordEnd` compiles the configured phrases into one regex anchored at the
  END of the transcript, and the transcript is `partial.committed` -- the
  recogniser's own text. Decoding Portuguese, the same speaker saying the same
  English end word over one evening produced `send it` (which closed the turn),
  and `Sanded`, `Sended` and `Sandit` (which did not). Verbatim from the trace:
  `partial ... tail='no meu desktop Sended esta dando problema'` and
  `partial ... tail='eu forma o portugues Sandit uma ideia aqui entao'`. A turn
  whose committed text takes the phonetic spelling can never match, so it runs
  to `END_WORD_MAX_MS` (300 s), which is what "captures forever" means.
  Two aggravating facts, both measured. The pattern trimmed ONLY `.!?`, so a
  phrase the recogniser punctuated with a comma (`pode ir,`) did not match
  either. And `end_phrases` is a per-machine SETTING: the hub's held the four
  English defaults and nothing else, so on that machine a Portuguese phrase
  could not match whatever it was spelled as. Two machines, same commit,
  different behaviour -- which is why "the app is the same version there" was
  true and still misleading.
  **THE FIX IS A CURATED TABLE, NOT FUZZINESS.** `END_PHRASE_ALIASES` maps an
  observed spelling to the phrase it stands for, and the trailing class became
  `[^\w]*$`, so no punctuation a recogniser might emit can hide the phrase.
  Edit distance and phonetic matching were rejected: in this mode a false
  submit costs a half-finished instruction, and every entry in that table is a
  string that was really committed to a real transcript. `turn_open` now
  carries `phrases=`, because the first question about a turn that ran to its
  ceiling is whether the phrase the owner said was even configured, and
  answering that took an ssh session and a settings file.
  **Why no test caught it**: every end-phrase test in the suite used the
  literal spelling and `.!?` -- precisely the input this defect needs. The
  witnesses are `test_endpointing_policies.py::test_a_misheard_end_phrase_still
  _ends_the_turn` and `::test_a_trailing_comma_does_not_hide_the_phrase`, both
  fed with the strings above, plus a guard that a mishearing mid-sentence still
  does not submit. D-13 EXECUTED 2026-09-15, exit codes only: emptying the alias
  table reddened the two aimed tests with the rest of the file green, and
  restoring `.!?` reddened the comma test ALONE.
  **STILL OPEN, and deliberately not changed here**: whether the phrase should
  also be matched against `tentative` text (the policy sees `committed` only),
  and the content of `end_phrases` itself, which is the owner's to fill -- five
  minutes of open microphone is what a miss costs until it is filled.

- **D-41 THE ENDWORD READ HALF THE TRANSCRIPT: THE STABLE HALF.** Found
  2026-09-15 by measuring the owner's own laptop, after they reported that the
  end phrase "worked once, but it didn't work after that. it never sends the
  endword, even though it captures it".
  **THE MECHANISM, IN TWO TURNS THEY ENDED BY HAND.** `TurnPipeline` built the
  transcript the end-word policy matches from `partial.committed` ALONE, and the
  engine's own API says what that leaves out: `transcribe_cpp.StreamText`
  documents `committed` as "append-only and stable" and `tentative` as "the
  volatile suffix". So the last words of an utterance -- which is where an end
  phrase always is -- can live in `tentative` for the whole turn: visible to the
  owner, because the PAGE draws both halves (`renderLive(committed,
  tentative)`), and invisible to the policy. The two turns ran 33.8 s and 33.8 s
  and ended `reason=client_hint`, which is the page's stop button -- `app.js:3900`
  sends `capture_end_hint` only when the user taps it. Their transcripts stop
  growing at '...resultado da sua pesquisa anterior.' and repeat identically for
  the rest of the turn; the words they actually said -- 'O que aconteceu? Pode
  ir' -- appear ONLY in the turn's `final`, which is `committed` read AFTER
  `finalize()` flushed `tentative` into it. Across the whole file: 46 committed
  tails ended with a phrase, 7 turns closed by `end_word`, 5 never closed, 3
  were stopped by hand -- and EVERY turn stopped by hand had `phrase_at_end=no`
  at the moment of the tap.
  **THE FIX IS ONE JOIN**: `_watch` merges `committed` and `tentative`, the text
  the page already draws. The 600 ms pause and the END anchor are untouched, so
  this widens WHAT the policy can read, never WHERE a phrase may be.
  **Why no test caught it**: the fake recognizer ALSO splits -- `tentative` is
  the word just heard -- and no pipeline test ever drove the end-word policy
  through the fake, so the split was never exercised below the policy's own
  unit tests, which are handed a finished transcript. The witness is
  `test_pipeline_endpointing.py::test_the_end_phrase_counts_while_it_is_still
  _TENTATIVE`, with its guard `::test_a_phrase_that_is_NOT_last_does_not_submit
  _and_says_so`. D-13 executed 2026-09-15: reverting `_watch` to committed-only
  reddened the first (with `reason='absolute_cap'` in the message), deleting the
  new log line reddened the second, each with the other green.
  **AND A TURN THAT ENDS WITH THE PHRASE UNSPENT NOW SAYS SO** in the daemon
  log, naming the phrase and the reason -- this defect took an afternoon of
  forensics over a file that recorded the transcript and never the decision.
  MEASURED AFTER (`test/manual/spike_endword_stream.py`: the real nemotron,
  real synthesized speech, fed at WALL-CLOCK pacing): the phrase commits with
  `tentative` EMPTY for clean audio in both languages, and three repeat turns on
  one held session each end with the phrase. That is why the spike could not
  reproduce the owner's failure and the trace had to -- clean audio never
  leaves a suffix uncommitted.

---

## 7. Risks and accepted limits

- **GPU contention (narrower than an earlier draft claimed).** Correction
  to that draft: Kokoro is NOT on the card and never was.
  `daemon/engines/tts/tts-model-loader.js:60` hardcodes `device: 'cpu'`,
  and kokoro-js under Node has no CUDA path on this hardware. So the
  1080 Ti (11 GB) holds Parakeet (Vulkan) plus the Reflex model, and
  "move Kokoro to CPU" was listed as a mitigation for a configuration that
  does not exist. Budget as measured 2026-08-16: 1825 MiB desktop baseline
  + 3959 MiB Reflex + roughly 2000 MiB Parakeet (ESTIMATE) leaves about
  3480 MiB free on the 11264 MiB card. Remaining mitigations, in order:
  measure STT + Reflex together in Phase 1, then drop to LFM2-2.6B Q8_0
  (-1062 MiB, 7/10 routing) and then LFM2.5-1.2B Q8_0 (-2508 MiB, but 3/10
  routing, which is too low to ship). "Move Reflex to CPU" is REMOVED from
  the ladder: Qwen3-4B on CPU measured p95 1493 ms, the whole AC-4 budget.
  An earlier prototype measured STT falling ~30 s behind under GPU contention until the
  560 ms chunk config landed [MEASURED-PROTOTYPE]. Correction to an earlier
  draft: llama-swap IS running on port 8077, but Butler must not use it --
  its 900 s idle TTL and its shared tenancy with an earlier prototype's dashboard mean
  Butler's always-warm model can be evicted mid-conversation (18.1 s to
  reload). Butler owns a dedicated llama-server on 8901 (4.4).
- **Kokoro on CPU: measured, and the risk moved.** The feared number came
  back fine -- a 5-word first sentence reaches first audio in p50 448 ms
  (p95 468 ms), well under the 700 ms trigger, leaving roughly 1030 ms of
  the AC-4 budget for wake finalize, ASR, the Reflex verdict and the
  satellite hop. AC-4 is therefore un-falsified, NOT proven; those four
  terms are still unmeasured together. The real risk turned out to be
  LENGTH, not baseline latency: realtime factor is 3.5-4.6x at every size,
  so a 19-word first sentence costs 1784 ms and a 38-word one 3710 ms. One
  long opening sentence blows the budget on its own. The mitigation is a
  hard rule on the splitter (4.7), not a faster engine.
- **The iOS unknown (8.1).** Architecture does not depend on the answer;
  the pocket-satellite EXPERIENCE does.
- **Always-on microphone security.** The daemon is an always-listening
  mic reachable over the network. Accepted posture: bind TLS; tailnet or
  LAN only, never port-forwarded; audio is processed in memory and NOT
  recorded to disk by default (no capture archive in this plan); the
  satellite page carries a visible live state pill. NOT solved here:
  authentication of satellites (any device on the tailnet can connect in
  Phases 1-3; speaker verification in Phase 4 gates actions, not
  connections). Stated plainly: until Phase 4, a hostile device on the
  network can talk to Butler; modes bound the damage to read-only
  conversation.
- **Cost of the substantive tier.** Zero while it is endpoint mode against a
  local llama-swap, which is the default. The paragraph below applies to a
  CLOUD endpoint or a cloud-backed harness. Idle cost is zero by construction --
  wake and Reflex are local, and the cloud tier is called per-utterance
  only when Reflex routes to it. The risk is chattiness, not idleness. A
  daily spend note in the web UI is a Phase 2+ nicety; no hard budget
  enforcement in this plan (accepted limit). One data point from an earlier prototype on
  harness cost: a single `claude -p` probe cost $0.90 with cache creation
  [MEASURED-PROTOTYPE] -- worker dispatches are the expensive path and they
  are always deliberate.
- **Wake-word spoofing.** Before Phase 4, anything that sounds like the
  wake word wakes Butler (TV, podcast). Escalation-by-voice is still
  refused for everyone, so a spoof cannot RAISE the mode -- but since AC-29
  was retired on 2026-08-18 the modes no longer bound a coding agent, so a
  spoofed instruction the router treats as work reaches an agent with
  `bash` in the configured project. That is a breach, not a nuisance, and
  it is accepted until Phase 4. After Phase 4, spoofing
  additionally requires beating ECAPA verification on wake+command audio.
  Replay of a recording of the OWNER is out of scope and stated as such.
- **The wake word stays `hey_jarvis` [DECIDED 2026-08-16].** The prior-art
  review recommended changing it (R3). The number the decision was made
  against: on Voice PE's Dinner Party Corpus benchmark, `hey_jarvis` at its
  manifest default produces 0.563 false accepts per hour, rising to 1.502 at
  looser cutoffs, while `okay_nabu` at cutoff 0.85 produces 0.000 [REVIEW
  home-assistant-satellites.md]. For an always-on daemon that is on the
  order of 24 self-triggers a day. The owner has weighed that and kept
  `hey_jarvis`; the decision is not re-argued here. Two consequences follow
  and are carried in the plan rather than left implicit. First, the accepted
  false-accept rate is a daily-nuisance cost that modes bound but do not
  remove. Second, it RAISES the value of Phase 4 speaker verification: with
  ~24 spurious wakes a day, the thing that stops a non-owner voice from
  reaching anything above read-only is the only real filter, so Phase 4 is
  load-bearing rather than a refinement.
- **Single point of failure.** One daemon owns everything. Accepted for a
  single-user system; the worker registry's disk-first design (AC-12)
  and autostart (Phase 5) are the recovery story. No HA, no clustering.
- **Pinned dependencies.** onnxruntime-web 1.18.0 (iOS shared-memory
  refusal is structural in >= 1.19 [MEASURED-SPIKE]); `transcribe-cpp`
  0.1.x wheel (prebuilt; a future version changing the stream API is a
  named upgrade task, not an implicit bump). Both pins are written into
  the repo with the reason beside them (D-12).
- **Abstraction tax on the five ports (4.10).** Every seam is a bet that
  the thing behind it will change. Five bets are being placed and they are
  not equally safe: `Tts` is close to certain (the Kokoro latency risk above
  may force it in Phase 1), `Asr` and `Source` are likely, `Harness` has
  several real candidates installed on this machine, and `Brain` is
  the speculative one -- it exists because an external agent framework is a
  stated future intent, not a current need. If `Brain` never gets a second
  real implementation it will have cost an intent type and one indirection,
  which is a cheap losing bet; the fake adapters keep even the losing bets
  exercised. The tax is capped by refusing to abstract anything else, and
  the plan states plainly what is NOT a port.
- **Harness coverage.** Steering and hooks are built against claude only;
  pi/codex are one-shot. gemini/opencode/crush unverified end to end.
- **Score comparability across satellites** (8.3) makes arbitration
  approximate; accepted, still better than first-to-report.
- **Kokoro voice inventory** is assumed sufficient for a per-worker
  rotation (kokoro ships multiple voices); if concurrent workers exceed
  distinct-enough voices, workers share and the announcement names the
  worker in words.

---

## 8. Open questions

Each with the evidence that settles it. None block Phase 1 except 8.2.

### 8.1 Does iOS keep a web page's mic capturing while locked? (recorded, not resolved)

Published sources say WebKit suspends capture when the page is backgrounded
and that the Screen Wake Lock API is unsupported in Mobile Safari [READ].
The owner reports it working RIGHT NOW in Brave on iOS against an earlier prototype's dashboard. The owner's device is better evidence than an article, and the
answer is genuinely unknown.

**The decisive test (run in Phase 3, on the owner's phone):** start a
capture on the phone, lock the screen, wait 60 seconds, unlock, stop the
capture. Then check whether the capture's duration and transcript cover the
locked window, or only the seconds on either side of it. Duration and
transcript are the ground truth; the UI's appearance of recording is not.

**Why the architecture does not depend on the answer:** with a
source-agnostic pipeline the phone is just another WebSocket source. If
background capture works, the pocket satellite is free. If it does not,
the phone is a screen-on satellite -- and nothing in the core changes.

**Related iOS gotcha to verify in the same session:** once mic permission
is granted and capture starts, iOS can force audio OUTPUT from Bluetooth
headphones to the built-in speaker [READ + owner-adjacent report]. Record
observed routing in the same experiment note.

### 8.2 Reflex contention with Parakeet (mostly ANSWERED 2026-08-16)

Serving is settled and no model is prescribed: the owner picks (4.4b). What is
measured about the candidates on this machine is the table in 4.4 -- Qwen3-4B-
Instruct-2507-Q6_K at p50 181 ms, p95 514 ms, 4077 MiB, against Ornith-1.0-9B at
p50 1519 ms -- with `docs/reflex-serving.md` as the fuller probe log.

Half of (c) is now answered and half is not, and the difference matters. The
2026-08-17 verdict run happened with butlerd's process RESIDENT on the GPU and
another model loaded on llama-swap, 5564 MiB in use before the router loaded:
p95 514 ms, inside the 600 ms pass line section 7 set. That is the router half.
The TRANSCRIPTION half is untouched -- Parakeet was resident but not decoding,
so nothing yet says whether partial cadence holds while a verdict generates, and
that is the direction section 7 calls the worse one. Evidence still needed, in
Phase 1 step 10's bench: run a streaming transcription and a verdict
simultaneously and record whether partials slip past the 560 ms chunk cadence
and whether combined VRAM stays under the card. The fallback ladder is in
section 7.

### 8.3 Wake-score normalization across devices (collection HAS STARTED)

Different mics and gains mean a 0.7 on the phone and a 0.7 at the desk are
not the same evidence. Still open; what changed is that the evidence is now
being collected rather than merely promised.

`daemon\arbiter.py` logs EVERY candidate -- winner and loser alike -- as one
line carrying `device`, `score`, the client's `ts_ms` (when the DEVICE says
it fired) and the daemon's `recv_ms` (when the daemon actually received it).
Both clocks, because the field's lesson is that arrival order is not
distance order: WiFi power-save jitter swamps the ~30 ms of time-of-flight
between rooms, and Amazon's arbitration patents carry a per-device latency
correction for exactly that [REVIEW home-assistant-satellites.md]. The log
line sits BEFORE the early return that rivals take; the first version of it
sat after, which recorded only window-openers, and per-device distributions
cannot be compared from one side.

Evidence still needed: a week of real use, then decide between raw
comparison (shipping first), a per-device offset, or a per-device threshold.
The same log is the only thing that can tune the 300 ms window, which is
currently a guess costing 20 percent of the AC-4 budget and which the review
expects to come down to 150-200 ms.

### 8.4 Speaker-verification threshold

FAR/FRR on this hardware, this room, these mics is unknowable until
enrollment recordings exist. Evidence: enrollment set + a recorded-TV and
recorded-other-speaker impostor set, scored offline; pick the threshold
from the plotted separation, per mode strictness.

### 8.5 Native mic capture library (Phase 5)

sounddevice/PortAudio vs WASAPI-specific alternatives on Windows.
Evidence: a spike that runs 24 h at 16 k mono without drift, device-change
survival (unplug/replug the interface), and CPU cost. Decide then.

### 8.6 Conversation-tier memory

Whether Butler keeps long-term memory (facts, preferences) or stays
session-scoped. Deliberately out of this plan; the evidence that would
justify it is lived experience with session-scoped Butler feeling
forgetful. Revisit after Phase 2.

### 8.7 The steering message schema

The claude stream-json flags are given [DECIDED]; the exact JSON line
accepted on stdin for a mid-run user message is pinned by running the real
CLI in Phase 2's first red test, not asserted here (D-13: validate the
instrument before trusting it; do not invent the schema).

---

## 9. Prior art -- what is in `.references\`

On 2026-08-16, six parallel investigations read comparable systems against
this plan and wrote their findings mapped to its sections. They are in
`.references\` and they are the source of every [REVIEW] tag above.

| file | covers |
|---|---|
| `README.md` | the synthesis: sixteen ranked recommendations R1-R16, the defects found, and where Butler is ahead |
| `livekit-agents.md` | AgentSession lifecycle, end-of-utterance model, interruption and transcript truncation, metrics names |
| `pipecat.md` | frame pipeline, smart-turn v3.2 (we use their model), interruption strategies, TTS cancellation |
| `home-assistant-satellites.md` | Wyoming protocol, Assist pipeline, multi-satellite reality, wake-word false accepts, announcements |
| `realtime-and-brain.md` | OpenAI Realtime, speech-to-speech versus cascade, whether the four ports survive, constrained JSON |
| `audio-internals.md` | wake engines, VAD, endpointing, AEC and self-trigger, barge-in tuning, iOS |
| `failure-modes.md` | 33 sourced war stories across 11 classes, each checked against this design |

Status as of this revision: Tier 1 (R1 `playback_done`, R2 the 700 ms
silence floor, R4 the second segment cap, R5 the candidate log) is
implemented and is described above; R3 is [DECIDED] against in section 7.
Tier 2 (R6 self-trigger as daemon policy, R7 backpressure, R8
`speak_cancel`, R9 route-field-first, R10 the field's metric names) and
Tier 3 (R11-R16) are NOT done and are not yet folded into the acceptance
criteria -- read `README.md` before planning the phase each one touches.

Three things the review confirmed rather than changed, recorded so they are
not re-litigated: the pre-roll ring is a genuine lead (nobody in the Home
Assistant ecosystem ships one), the arbitration design matches what Amazon's
patents describe against HA's first-packet-wins lockout, and the `Brain`
port survives speech-to-speech because OpenAI's Realtime API still emits
tool calls the application executes.

---

## Appendix A -- measured numbers carried into this plan

| number | value | provenance |
|---|---|---|
| STT first partial, Vulkan | 198 ms | MEASURED-SPIKE (bench.py, JFK 11 s) |
| STT RTF, Vulkan / CPU | 0.151 / 0.362 | MEASURED-SPIKE |
| STT finalize | ~150 ms | MEASURED-SPIKE (headless realtime loop) |
| Smart Turn: thread pool / child / child+BLAS=1 | ~1960 / 292 / 33 ms | MEASURED-SPIKE |
| Streaming config | (70,7,7) = 560 ms chunks; (70,2,2) was ~37x RT overhead | MEASURED-PROTOTYPE |
| Endpoint policy | first check after 280 ms of SILENCE, recheck 450 ms, p >= 0.6, hard cap 6 s of SILENCE | MEASURED-SPIKE (tuned there); "of silence" restored 2026-08-16 |
| No-endpointer silence floor | 700 ms (the branch a pipeline with no endpointer runs) | REVIEW audio-internals.md |
| Smart-turn window anchor | speech onset - 500 ms (pipecat `pre_speech_ms`, default 500 since 0.0.99) | REVIEW pipecat.md |
| Smart-turn inference, wired daemon | 31 ms p50 / 35 ms p95 wall (6 ms features + 25 ms ONNX), child process, BLAS=1 | MEASURED 2026-08-16 (n=10 per fixture) |
| Smart-turn model load at boot | 498 ms (spawn + ONNX load + one zero-buffer score) | MEASURED 2026-08-16 |
| End of speech to `final`, smart mode | 434 ms over a real WebSocket (280 ms first-check silence + poll + inference) | MEASURED 2026-08-16 |
| Field floor for pure-silence endpointing | 500-900 ms (OpenAI Realtime `silence_duration_ms` 500, LiveKit `min_endpointing_delay` 0.5 s, AssemblyAI `max_turn_silence` 1000) | REVIEW audio-internals.md |
| Absolute turn ceiling | 120 s (safety net; nothing should reach it) | a choice, not a measurement (4.2) |
| Endpointer call timeout | 2 s, then "no verdict" and the silence cap decides | a choice, not a measurement (4.2) |
| Playback gate | audio actually sent + 2 s grace, then re-arm anyway | a choice, not a measurement (4.3) |
| Wake detector | threshold 0.5, refractory 2000 ms, 1280-sample chunks | spike constants (tuned, not derived) |
| Wake re-arm context refill | ~1.28 s (16 x 80 ms) | MEASURED-PROTOTYPE |
| Wake cooldown after a reply | 1300 ms (was 700, which re-armed with half the detector's context still holding our own voice) | DERIVED 2026-08-16 from the row above |
| Pre-roll ring | 13 frames x 32 ms = 416 ms of lookback | a choice, sized to still reach >= 300 ms before the fire (4.2) |
| Patience presets (silence / cap) | quick 400 / 4000 ms, balanced 600 / 8000 ms, patient 800 / 15000 ms | DECIDED (named by feel, not by number) |
| End-word pause after the phrase | 600 ms | a choice, not a measurement (4.2) |
| Audible cue tones | listening 880 Hz / 120 ms; closing 520 Hz / 60 ms; submitted 660 then 988 Hz / 150 ms | DECIDED (4.7) |
| Cue microphone suppression | 5 ms lead + output latency + input latency + tone + tail of 150 / 90 / 130 ms; frames ZEROED, never dropped | DERIVED 2026-08-16 (~0.4 s RT60 room, 32 ms mic frame) (4.7) |
| Settings surface | 46 settings, 7 groups, 5 of them restart-only (`brain_mode`, `asr_model`, `asr_backend`, `asr_latency`, `endpointer`) | READ FROM CODE 2026-09-10 |
| Test suite | 461 passed, 0 xfailed in 216 s | MEASURED 2026-08-18 |
| Harness startup, `pi --mode rpc` | spawn to prompt-accepted 1.51-2.28 s (21 runs; working directory, provider and model each varied and none explained a difference) | MEASURED 2026-08-17 (docs/harness-mode.md 1) |
| Harness turn, same prompt, two models | 1.55 s on a bridged cloud model vs about 27 s on a local 4B -- a 20x spread the owner chooses and Butler does not warn about | MEASURED 2026-08-17 |
| Harness abort | settles in 1.51 s and the same child answers the next prompt | MEASURED 2026-08-17 |
| pi model registry | 532 provider/model pairs, printed on STDERR | MEASURED 2026-08-18 |
| Orphaned `git fsmonitor--daemon` on this machine | 147, of which 8 named a LIVE process as their parent -- why a PID alone is not an identity (D-13) | MEASURED 2026-08-18 |
| Brain, warm 4B over llama-swap | 1.51 s final to first reply (router answered); 1.09-1.20 s to the first sentence when routed on; 26.63 s cold | MEASURED 2026-08-17, GPU shared with another workload (4.4) |
| Barge-in starting values | +0.15 bar, 2 frames, 700 ms grace | an earlier prototype reasoned guesses, NOT measured |
| TTS interrupt recovery | 1688-3038 ms to next answer's first audio (vs ~15 s pre-fix) | MEASURED-PROTOTYPE |
| ort pin | 1.18.0 (>= 1.19 refuses on iOS: shared 2 GB wasm memory) | MEASURED-SPIKE |
| Cold start observed | ~2 min first transcription (cause hypothesized, not fully profiled) | MEASURED-PROTOTYPE (owner report) |
| WS send ceiling | 4 MB | an earlier prototype constant -- a choice, not a measurement |
| claude one-shot probe cost | $0.90 (cache creation dominated) | MEASURED-PROTOTYPE |
| Arbitration window | ~300 ms | DECIDED (target; tune with 8.3 data) |
| Kokoro device | CPU (`tts-model-loader.js:60` hardcodes it) | READ FROM CODE 2026-08-16 |
| Reflex verdict latency, Qwen3-4B Q6_K on GPU | p50 140 ms, p95 168 ms, 20/20 valid JSON | MEASURED 2026-08-16 (docs/reflex-serving.md) |
| Reflex VRAM, Qwen3-4B Q6_K | 3959 MiB (card 11264, desktop baseline 1825) | MEASURED 2026-08-16 |
| Reflex on CPU, same model | p95 1493 ms -- the whole AC-4 budget | MEASURED 2026-08-16 |
| Reflex routing accuracy | 8/10 Qwen3-4B, 7/10 LFM2-2.6B, 3/10 LFM2.5-1.2B | MEASURED 2026-08-16 (n=10, small sample) |
| llama-swap | running on 8077, TTL 900 s, shared -- unusable for always_warm | MEASURED 2026-08-16 |
| Verdict latency under load, Qwen3-4B Q6_K | p50 181 ms, p95 514 ms, 20/20 valid JSON (5564 MiB already resident) | MEASURED 2026-08-17 |
| Same model, VRAM and cold load under load | 4077 MiB, 24.6 s | MEASURED 2026-08-17 |
| Ornith-1.0-9B Q5_K_L, `--reasoning off` | verdict p50 1519 ms / p95 2762 ms, 6490 MiB, 108.5 s load | MEASURED 2026-08-17 (near-empty GPU) |
| Ornith-1.0-9B as shipped | reasoning model: 9.8 s for a one-sentence answer; under a 200-token cap 10/10 verdicts return EMPTY content | MEASURED 2026-08-17 |
| Kokoro CPU first audio, 5-word sentence | p50 448 ms, p95 468 ms (n=20) | MEASURED 2026-08-16 (docs/bench-tts.md) |
| Kokoro CPU, 19-word sentence / 38-word sentence | 1784 ms / 3710 ms | MEASURED 2026-08-16 |
| Kokoro realtime factor | 3.5-4.6x at every length | MEASURED 2026-08-16 |
| Kokoro cost per word | ~95 ms | MEASURED 2026-08-16, derived (1784/19 = 94, 3710/38 = 98) |
| TTS segment caps | first 8 words (AC-4), later 16 words (AC-5) | DECIDED from the two rows above (4.7) |
| TTS transport, 3-sentence reply, first audio | 1242 ms over HTTP vs 1351 ms in-process, same run | MEASURED 2026-08-17 (docs/bench-tts.md 3a) |
| Barge-in: next reply's first audio, 10-sentence call | 7887 ms before the cancel route, 3736-4392 ms after | MEASURED 2026-08-17 |
| Machine throughput on 2026-08-17 | about half the 2026-08-16 figures (5-word case 912 vs 448 ms, RTF 2.0-2.2 vs 3.5-4.6), cause not investigated | MEASURED 2026-08-17 -- why the rows above are compared only within one run |
| Kokoro cold start | 1.2 s per restart (+ one-time 6.6 s / 325 MB download) | MEASURED 2026-08-16 |
| Kokoro CPU cores used | p50 2.32, max 3.72 of 20 logical (intraOpNumThreads=4) | MEASURED 2026-08-16 |
| Cost of the whole-WAV HTTP sidecar hop | +3252 ms on a 3-sentence reply (3925 vs 673 ms). REMOVED by step 8: 1242 vs 1351 ms, same run | MEASURED 2026-08-16, re-measured 2026-08-17 |
| claude --input-format | requires --print; prompt placement unresolved | MEASURED (claude --help, 2026-08-16) |
| openWakeWord false accepts, Dinner Party Corpus (~5.5 h far-field) | hey_jarvis 0.563 FA/h at its manifest default, 0.751-1.502 at looser cutoffs; okay_nabu 0.000 at cutoff 0.85 | REVIEW home-assistant-satellites.md (Voice PE firmware YAML) |
| Constrained JSON decoding cost | 372 ms unconstrained vs 376 ms with a full schema -- free | REVIEW realtime-and-brain.md |
| Route field readable first, streaming | 126 ms for `route` vs 375 ms for the complete verdict object | REVIEW realtime-and-brain.md |
| Reflex round trip | < 1500 ms | TARGET (two components unmeasured) |

## Appendix B -- assets (already on disk; no fetch script)

All of these were copied in with the scaffold commit and are present now.
They are gitignored, so this list is the record of what a fresh clone must
obtain; the README carries the actual commands. There is deliberately no
`fetch-assets.py` (4.9).

- `models\parakeet-unified-en-0.6b-Q8_0.gguf`,
  `models\nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf` (HuggingFace; the same
  files the spike used)
- `models\smart-turn-v3.2-cpu.onnx` (pipecat's published model)
- openWakeWord trio in `web\models\`: `melspectrogram.onnx`,
  `embedding_model.onnx`, `hey_jarvis_v0.1.onnx`. The shipped wake phrase is
  "hey jarvis" and STAYS -- see the [DECIDED] entry in section 7 for the
  false-accept number that decision was made against. A custom "hey butler"
  model remains a later, optional training task.
- onnxruntime-web 1.18.0 in `web\ort18\`: `ort.wasm.min.js`,
  `ort-wasm-simd.wasm`, `ort-wasm.wasm`
- kokoro model: pulled on first run by kokoro-js (top-level `package.json`)
- Python deps via uv: aiohttp, numpy, onnxruntime, transcribe-cpp (pinned)

## Appendix C -- verification commands (Phase 1)

```
cd E:\PROJECTS\butler
uv venv && uv pip install -r requirements.txt        # pinned
npm install                                          # kokoro-js for the sidecar
python -X utf8 -m pytest test\ -q                    # 461 passed
python -X utf8 daemon\butlerd.py                     # http://localhost:8140
python -X utf8 test\manual\test_ws_loop.py           # headless end-to-end
python -X utf8 test\manual\probe_page.py             # the page in real Chrome

# The line above boots the ALL-FAKES daemon on a fresh clone, where
# config\models.json does not exist (4.9). Write that file to select real
# engines and an ASR backend (it is gitignored, and BUTLER_CONFIG keeps the
# test suite hermetic against it); or select engines by environment:
set BUTLER_ASR=transcribecpp
set BUTLER_TTS=kokoro
```

The entry point is `daemon/butlerd.py` on port 8140. The rename landed with
the protocol v1 rewrite; `daemon\server.py` is the spike's server and is now
dead code that contradicts the running daemon (D-12) -- delete it once
nothing references it.
