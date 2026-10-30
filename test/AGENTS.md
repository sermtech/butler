# Tests

## Purpose

- The executable half of `plan.md`. Each acceptance criterion (AC-n) and defect
  rule (D-n) that can be checked has a test that names it.
- Protocol tests drive the daemon over a real WebSocket and assert on the wire;
  conformance suites hold every adapter to the same port contract.

## Ownership

- Governs: `test/` including `conformance/`, `assets/` and `manual/`.
- Parent owns: what the contracts ARE. This subtree only proves them.

## Local Contracts

### What a test is

- Name the AC or D a test encodes and the failure it would catch.
- Assert on observable behaviour, not internals. Protocol tests do not import
  daemon modules to poke at objects.
- A contract needs an assertion on the CALLER, not only the callee (D-18).
  Conformance proves an adapter CAN do something; only a hub test proves the
  daemon does it.
- Confirm a refusal by its CONSEQUENCE, never by status alone: assert the
  spawn count (`test_terminal_route.py`), the answers reaching the child
  (`test_harness_question.py`), the record, the decode count
  (`test_dictate.py`, AC-116), the ask count (`test_attachments.py`). A route
  that answered 400 and acted anyway passes every status check.
- Prove a SEPARATION with negative assertions, and build fixtures that make
  them possible: the two inputs share no words, and the double keeps the whole
  context (`test_ambient.py`, AC-70). Prove a registry on BOTH records, byte
  for byte on the one that must not change (`test_chats.py`, AC-240).
- Prove a SAMENESS by running two hubs and diffing the wire
  (`test_typed_turn.py`, AC-162/AC-163), on the real `tts_fake`, not `QuietTts`.
- A fixture that rules out only ONE wrong answer is not an instrument: put the
  wanted item in the MIDDLE of three, and give a detector a case it gets WRONG,
  not merely one it finds short.
- Never compare against a list written in the test when the code already holds
  it: read frame names off `plan.md` 4.3 and `hub._handlers`
  (`test_protocol_doc.py`), `viz.` methods off `app.js`
  (`test_visualiser_contract.py`), route shapes against EACH OTHER.
- Never weaken a test to make it pass. A real engine that cannot meet a
  conformance property gets `xfail(strict=True)` with the reason and the plan
  step that removes it; delete the mark when the step lands.
- Add a check when a review finds something; do not expect the suite to find
  layout, legibility or interaction defects first.

### Mutation discipline (D-13)

- For anything load-bearing: break the code, confirm the mutation applied,
  watch the RIGHT test fail, restore, re-run green.
- Ask TWO questions per mutation, by EXIT CODE only: does the aimed test fail
  (1), and does everything else still pass with it deselected (0). Never match
  "FAILED" in pytest's output text.
- A mutation must produce the DEFECT, not an impossible state; a row may need
  several edits.
- A rule with several honest witnesses names ALL of them rather than pretending
  to one; breadth is evidence, not a fault in the aim.
- Assert the mutation anchor is UNIQUE before writing; an anchor that matches
  twice or not at all is an error, not a survivor.
- Delete `__pycache__` between mutation runs, scoped to `daemon/` and `test/`
  (never `rglob` the repo -- it reaches `.venv`). Same-length edits inside one
  mtime second are otherwise served from the stale `.pyc`.
- Give every harness a run deadline (`RUN_TIMEOUT_S`, answer 2 on timeout) and
  a `BASELINE_RED` list for tests known red before any mutation;
  `test/manual/mutate_project_switch.py` is the template.
- A mutation harness that WRITES daemon files must not run while another
  session edits those files. Restore every file and re-run the restore.
- A SKIP proves nothing: a gate that can skip carries its own witness that the
  detector catches a hand-built bad case (`test_the_state_colour_gate_would_catch_one`).

### Environment and isolation

- Run with `.venv\Scripts\python.exe`, never bare `python` (a conda env with
  different pytest-asyncio and Python versions that disagree on
  ordering-sensitive tests).
- Keep the suite hermetic. `conftest.py` sets at import: `BUTLER_CONFIG` at a
  nonexistent path (all-fakes default; guarded by `test_config.py`),
  `BUTLER_TLS=0`, `BUTLER_STATE` to a temp dir, and POPS `BUTLER_DATA` and
  `BUTLER_SELF_URL`. `BUTLER_STATE` is `setdefault`: never run the suite from
  a shell that exports it. A test wanting another value sets it with
  `monkeypatch`.
- Never touch port 8140 -- the owner's daemon is there on the desk AND on the
  laptop. `conftest.py` takes a free port from the OS.
- Never read the owner's real model registry (`~/.pi/agent/models.json`, it
  holds keys) or `config/models.json`; use a fixture registry with a fake key
  (AC-34, AC-37).
- Never write the owner's records (`state/` on the legacy layout,
  `E:\Butler-Data\.butler` after migration). `test_migrate_data_root.py`
  migrates a fake tree under `tmp_path`; persona and memory tests build files
  under `tmp_path`. To test the new layout, set `BUTLER_DATA` to a `tmp_path`
  and `delenv` `BUTLER_STATE`.
- Never reach the network for notifications: an autouse fixture in EACH file
  points `notify_telegram.API` at a dead local port, and `FakeBotApi` stands in
  (`test_notify.py`, `test_automations_results.py`, `test_telegram_inbound.py`).
  Give every `PhoneTurns` its own `queue_folder` (the queue persists); channel fakes need a
  `questions` attribute. `FakeBotApi` also fakes long-poll `getUpdates`, `sendVoice`, `getFile` and file
  download, `answerCallbackQuery` and `editMessageText` (`push` and `calls` helpers), and the
  file sends `sendPhoto`/`sendVideo`/`sendAudio`/`sendDocument` (rich-chat O-1).
- `PI_PROVIDER`/`PI_MODEL` live once in `conftest.py`; import them, never copy.
- Claude Code and opencode are tested over RECORDED output (`test/assets/harness/`, replayed by
  `test/harness_replay.py`: the `*-replay` conformance rows and `test_harness_claude.py`,
  `test_harness_opencode.py`). Their live conformance rows spend quota and run only with
  `BUTLER_LIVE_AGENTS=1`; otherwise they SKIP.
- Every request passes the gate (`daemon/access.py`). A test that means "PROXIED" or "another
  address" sends `await three.who(kind)` (`test_listeners._Three`): the owner's Tailscale login
  on PROXIED, a paired device on a LAN-bound MAIN. Never patch `Gate.decide` or `_is_local`;
  a gate test proves a refusal by `X-Butler-Gate: refused` AND a handler that never ran
  (`test_access_gate._recorders`).

### Scratch daemons and live instruments

- Give a scratch daemon its own `BUTLER_STATE` on a free port, with
  `hotkeys_enabled: false` in its `settings.json` BEFORE boot, or its desk
  agent takes the owner's hotkeys system-wide.
- Kill a scratch daemon by the PID that started it (or found LISTENING on its
  own port). Never kill by image name.
- Never point a setting-writing instrument at 8140 on either machine:
  `probe_page.py --live`, `--turn`, `--write`, `--typed`;
  `probe_reduced_motion.py` (writes `voice_effect`); `e2e_scenarios.py` owns
  its own daemon (section N writes `ui_style` and `voice_effect` there). Check what a tool DOES before trusting a list of flags.
- `probe_ux.py` is READ-ONLY and asserts it (snapshot and diff of settings,
  mode, shortcuts). `--plant=<scratch BUTLER_STATE>` refuses the repo's own
  `state/`.
- Browser instruments need `https` (the daemon is TLS by default).
- Before believing a browser-instrument failure, say whether it is this
  change's or pre-existing (use a throwaway worktree, never `git stash`).

### Async, timing and lifetimes

- No unbounded await: pytest-timeout and pytest-randomly are NOT installed.
  Every `anext` on an adapter stream goes through an `asyncio.wait_for`
  helper (`until()`) whose message names what did not arrive; prove the
  deadline by mutation (remove end-of-stream: FAIL, not hang).
- pytest runs strict asyncio with `--strict-markers`: every async test carries
  `@pytest.mark.asyncio`.
- Terminal-event counts read the TAIL, drained BEFORE dispose, on a window.
- Assert a harness lifetime claim AT `Done` with the generator suspended, and
  drive `converse.turn`, not `brain.turn`, for a close-time claim (AC-46,
  AC-59); otherwise the assertion measures the garbage collector.
- A stop-test brain double must ignore `abort` (`HangingBrain(deaf=True)`); a
  double whose abort ends the stream at once hides the race.
- A concurrency test driven by a stateless stub proves nothing about
  concurrency: drive a REAL `HarnessBrain`, and remember the fake's
  synchronous `dispose()` has no `await` window.
- Mint a fake harness PER SPAWN whenever more than one child may exist
  (`test_parallel_agents.py`, `_Agents`); the fake refuses `start()` on a live
  child.
- Shorten a timing constant in a test only beside a separate assertion on the
  shipped value (`HARNESS_IDLE_S` = 600 s, `PROGRESS_EVERY_S` = 5 s).
- Inject the clock: no test sleeps to a wall-clock minute
  (`test_automations.py`); assert schedule instants in UTC
  (`test_schedule.py` carries its own Eastern tzinfo).
- Per-turn state is tested over SEVERAL turns (`test_conversation.py`,
  `test_stop.py`: the terminal path on turn 2, turns 3-4 prove re-arm, D-1),
  and an OVERLAP needs two turns alive at once (D-39).
- A pipeline fixture must clear `min_speech_ms` (more than six 32 ms frames),
  or the turn runs to the absolute cap and costs tens of seconds while passing.
- Three wall-clock tests fail TOWARD their own symptom under load
  (`test_a_working_harness_is_never_cut_off`,
  `test_a_silence_timeout_is_not_replayed`,
  `test_a_brushed_key_is_not_reported_as_a_broken_microphone`); say whether a
  failure is load before fixing.
- `test_desk_dictation.py::test_the_install_line_is_generated_from_the_TABLE`
  fails when the process cannot reach the input desktop (remote session). Fix
  with a skip that names the desktop, never a weaker assertion.

### Process lifetime by PID

- Assert lifetime by PID against the process tree (`test_child_lifetime.py`,
  AC-36): a scratch HOST process, killed with `proc.kill()`; read the tree
  before the kill and require at least two levels.
- A PID is not an identity: pair it with CREATION TIME, and require a
  descendant to have started after its parent.
- Reuse `test_child_lifetime.py`'s `_tree`, `_survivors`, `_start_host`,
  `_taskkill`, `MARKER`; never copy them. Do not pin tree depth (the venv
  python is a uv trampoline).
- Every lifetime test's `finally` tree-kills survivors by recorded PID or a
  unique command-line marker, never by image name.
- An inverted lifetime test (AC-119, the terminal must SURVIVE) needs a
  CONTROL child that must die. The "never assign the terminal" rule's only
  witness is
  `test_terminal_route.py::test_the_terminal_is_deliberately_NOT_put_in_the_job_object`.

### Shared harness

- `conftest.py` owns one copy of: daemon fixtures, `Satellite`, `StubTts`,
  `QuietTts`, `FakeOpenAI` (records bodies and `Authorization`),
  `StubResolver`, hub-level stubs, and the pi config (`pi_kwargs`,
  `skip_without_pi`). Subclass `StubTts`; override only what a test varies.
  Import helpers across test files rather than copying them.
- Assert a model's request against `FakeOpenAI`, which records it: "stored"
  and "on the wire" are different claims (AC-38).
- A real-child conformance row (pi, `slow`) configures the child with
  `--append-system-prompt`. It SKIPS when pi is absent or the model does not
  comply, and FAILS when the provider refuses.
- `conformance/` holds what every adapter shares; `test_<port>_<engine>.py`
  holds what only one engine can be asked.
- A whole-clip decode needs a LONG case (30 s) and a SHORT one (5 s).
- Fixtures are generated: `assets/` WAVs from `scripts/make-fixtures.mjs`;
  assert against `manifest.json`'s observed text (`asr_observed_parakeet`),
  normalised, never the ground truth. Post HEADERLESS PCM (strip with
  `wave`).
- Test fixture binaries are GENERATED by the libraries that read them
  (`test_attachments.py`), including one whose extension lies.

### Known fixture traps

- A test daemon's stdout is a PIPE: `conftest._drain(proc)` reads it once the port is open.
  Never start one without it; a full pipe blocks the daemon's next `print` and a
  wire test times out far from the cause.
- Read a response BODY before the client closes (`test_dictate.py`'s
  `post_dictate` answers `(status, payload)`).
- A hub turn with `speak` on sits out the playback gate unless the socket
  answers `playback_done` on `speak_end`; set `speak` False or answer it.
  `s.ws.sent` is cumulative and `hub.history` is a deque.
- Scope a `Path.resolve` monkeypatch with `monkeypatch.context()` around the
  call alone; a 405 check also asserts the GET is 200; write fixture text with
  `newline=""`. Containment tests use a real junction (`mklink /J`).
- Age idle timestamps into the past (`_idle_since`); never sleep for real.

### Grep gates over `web/`

- Nothing here loads or executes `web/app.js`; these files are the only checks
  that reach the page and are source pins, not behaviour: `test_http_surface.py`
  (no localStorage), `test_visualiser_contract.py`, the appearance half of
  `test_seams.py` (AC-173, AC-680), `test_page_review_2026_09_26.py`,
  `test_turn_per_chat_page.py`, `test_sidebar_collapse_page.py`,
  `test_lantern_shell_page.py`, `test_lantern_composer_page.py`,
  `test_caption_style_page.py`, `test_settings_sheet_page.py`,
  `test_pairing_page.py`, `test_file_explorer_page.py`, `test_rich_chat_page.py`, `test_library_page.py`,
  `test_library_lantern_page.py`, `test_tasks_page.py`, `test_lantern_extras_page.py`,
  `test_lantern_quality_page.py`,
  `test_automations_page.py`, and the grep rows in `test_view_ownership.py`,
  `test_harness_question.py`, `test_turn_feedback.py`.
- Strip comments before matching, so a pin cannot match its own explanation.
- Browser evidence for each pin is driven separately against a scratch daemon.
- `test_chat_belongs_to_project.py`'s AST sweep asserts the only writers of a
  chat's `["project"]` are `rotate`, `rewind`, `migrate_unfiled` (AC-287..AC-290).

### Browser instruments (`manual/`)

- patchright cannot mock the WebSocket: `add_init_script` does not run and
  breaks every socket, and `route_web_socket` never fires. Read the wire with
  `page.on("websocket")` / `framereceived`.
- `evaluate()` runs in an ISOLATED world; drive levels with real audio, not a
  script-set property. Chromium's echo cancellation flattens a steady tone.
- THE PAGE ARMS ITSELF (AC-179): read `#stateChip`'s label before clicking it;
  `#micBtn` dictates, it does not arm.
- CSS transitions never finish in headless Chrome: call
  `getAnimations().finish()` and compare RGBA painted into a 1x1 canvas, never
  computed-style strings.
- A `position: fixed` element always has `offsetParent === null`; read
  `.hidden`. `.innerText` is layout-based; read the visible summary node.
- Selectors reusing `.copybtn` must negate every sibling
  (`:not(.saybtn):not(.rewindbtn):not(.retrybtn)`); a dead selector makes a
  criterion SKIP quietly, so the exit code covers missing elements.
- Drive drag handles with a REAL pointer and set the viewport explicitly.
- Contrast is not measured by any instrument here; a reader must composite
  real ancestors in a canvas and skip `opacity: 0` surfaces.

### Desktop-touching probes (`manual/`)

- A process you launched is not a window you own: open a NAMED scratch file and
  assert the foreground window's TITLE contains it before injecting any key.
- Suppress your own injection, restore the operator's clipboard, release any
  injected modifier in a `finally`, and use keys the owner has not bound (F13).
- A probe asserts the hook FIRED before believing any reading.
- Build the probe's own `actions.Table`; never write down a VK.

### Notes on `manual/`

- `manual/` is not collected (`pytest.ini` `norecursedirs`). It holds spikes,
  probes, benches and `mutate_*.py` D-13 harnesses.
- Spikes against `transcribe_cpp`: `Session.run()` takes FLOAT32 (int16 bytes
  give `text=''`), and `len(str(result))` is never evidence of text.
- Benches print a `chars` column; a fast timing on an empty transcript is the
  recurring failure.

### Worktrees

- A git worktree lacks `ssl/`, `models/` and `node_modules/`. Pass
  `BUTLER_TLS_CERT`/`BUTLER_TLS_KEY` from the repo root; junction `models/` and
  `node_modules/` in, and remove the junctions (`rmdir`, no `/s`) BEFORE
  deleting the worktree.

## Work Guidance

- Red first: a new behaviour gets a failing test that fails for the RIGHT
  reason. "Feature absent" and "daemon would not boot" are different results;
  say which.
- Mark slow or hardware-bound tests `slow`, `needs_gpu`, `needs_audio` so the
  fast tier stays fast.
- There are no tolerated failures: never exclude a file for "known" failures.
- Scope runs to the change; run the full suite only at real gates.

## Verification

```
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q -m "not slow and not needs_gpu"
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q --collect-only
```

- The full suite runs to completion under `.venv` (about 5 minutes); it does
  not hang.
- Read pytest's EXIT CODE, never its output text.
- Known open flake:
  `test_typed_turn.py::test_typing_over_the_wire_cancels_a_capture_and_leaves_it_working`
  occasionally misses its third `capture_start` in the full tier, never alone.
  Do not wave it through (D-1); capture the daemon's stderr with the test's
  `saw:` list.

## Child DOX Index

No child AGENTS.md yet.
