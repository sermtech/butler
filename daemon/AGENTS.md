# Butler daemon

## Purpose

- One asyncio process that owns all policy: satellites connect to it, it decides when a turn
  starts and ends, what gets said, and what Butler is allowed to do.
- Engines are held at arm's length behind ports so speech recognition, speech synthesis, the
  agentic middle and the coding agent behind it stay replaceable. Every port is built (see Local
  Contracts).

## Ownership

- Governs: `daemon/*.py` -- the entry point, protocol, turn lifecycle, the router and the
  completion wire, endpoint policy, mode, settings and connection stores, and the speech
  splitter.
- Children own: `adapters/` (concrete engines) and `engines/` (forked spike code).
- Parent owns: the project-wide rules and the defect catalogue in `plan.md`.

## Local Contracts

### Ports and seams

- `ports.py` is the contract: SIX ports (`Source`, `Asr`, `Tts`, `Brain`, `Harness`,
  `Notifier`). Change the count here, in `ports.py`'s docstring and in the code in ONE commit
  (D-12). A seventh port needs its argument in `plan.md` 4.10 first. The end-of-turn model is
  one async callable on the Hub, not a port.
- Adding a harness is an adapter file, a registry line and a conformance row. If it needs a
  call-site edit, the port is wrong. Never branch on a harness NAME; read `caps.native_resume`
  (AC-35, AC-44).
- Both harness callers end a prompt on `ports.TERMINAL_EVENTS`; never keep a per-caller copy of
  the terminal set (AC-48).
- Three harnesses (`docs/chats-and-terminals.md` CT-7): `pi` (`pi --mode rpc`), `claude`
  (Claude Code stream-json, one child per conversation) and `opencode` (`opencode run --format
  json`, one process per prompt). Claude Code and opencode keep their opaque session id INSIDE the
  spec's session file (`adapters/session_id_file.py`), so no caller handles them differently.
- The agent is PER CHAT (AC-771): `butlerd.chat_harness(settings, chat)` is the one answer -- the
  record's `harness`, written by `ChatStore.session_ref` at the chat's first spawn, else the
  `harness` setting. `harness_factory` (`harness_for(chat)`) and `harness_spec_resolver` both
  read it; `harness_model` reaches only a chat whose agent is the setting's.
- The Brain proposes, butlerd disposes: a `Brain` yields typed intents and never acts. Modes,
  arbitration, worker spawning and turn ids stay in the daemon, and no brain may raise the
  permission mode (AC-9, AC-10). A router `dispatch` becomes `AskConfirm`, never an action.
- `dialogue.Dialogue` is router-in-front, ONE substantive brain behind, for both
  `brain_endpoint` and `brain_harness`. Shared needs live in shared modules
  (`speech.drain_sentences`), never copied between brains.
- The verdict schema is built per turn and uses only what llama.cpp enforces (`enum`, `type`,
  `properties`); check everything else in code. A bad verdict from a healthy server falls
  through to the conversation model (plan.md 3.8); a dead or refusing server raises
  `llm.LlmError` and the turn ends loudly. `ports.Action.writes` is kept for Phase 2's worker
  dispatch; give it a reader there or remove it.
- `llm.py` is the only place the OpenAI wire format lives and the only holder of in-flight
  requests, registered by TURN so `abort(turn)` closes the socket (D-4). `llm.Aborted` is
  swallowed by the brain. A request offering no tools has no `tools` key.
- `connections.SettingsResolver` is the only route from a preference to a REQUEST
  (`resolve(role)` per turn; cap 0 becomes `None`, AC-38). A `*_reasoning` of False is the only
  value that changes the body (`chat_template_kwargs`).
- NO adapter reads the settings store. What a resolver cannot answer gets another resolver of
  the same shape: `harness_spec_resolver` (`spec_for`, the only route to a SPAWN) and
  `harness_factory` (`harness_for`). The chat store is the one narrow exception (a RECORD).
  `HarnessSpec` is built by the daemon, never by an adapter.
- butlerd injects by CAPABILITY flag (`NEEDS_TARGETS`, `NEEDS_HARNESS`, `NEEDS_PERSONA`,
  `NEEDS_TASKS`, `CAN_PROCEED`, `HAS_SESSIONS`), never by name. Boot builds no harness child.
- Connections are not a port. A model choice is a `{conn, model}` pair, never a bare id (plan.md
  4.4b).

### Harness children: lifetime and concurrency

- A child is held for ONE CONVERSATION; `HarnessSpec` equality is the ONLY invalidation rule
  (AC-59, AC-60). Do not add a list of invalidating events. Deleting a conversation is the one
  explicit dispose, because it has no next turn.
- A child is held only across turns that ENDED: barge-in, a caller that walked away and a failed
  turn all dispose it. Barge-in is `abort()` then `dispose()`, in that order, in one place.
- ONE fixed line ("Working on it.") is spoken before the spawn on every harness turn (D-10) with
  `Say.keep=False`: heard, never sent as `reply`, never in `s.spoken` or the record. The seed,
  the record and the spec decision run before it so a cap refusal can speak instead of it
  (AC-237).
- `_Host` is a registry keyed by CHAT ID (AC-241). Every path that reaches a child names the
  chat; `turn()` reads the conversation ONCE. `record(chat_id, ...)` and
  `ChatStore.append(chat_id, ...)` take the conversation explicitly.
- `HarnessConverse._turns` is one `_RunningTurn` per chat. Never reintroduce shared per-instance
  turn scalars; `_answer` takes its `_RunningTurn` as a parameter.
- Open questions live on the child (`_Child.questions`) and an answer goes to the child that
  asked.
- Caps: `harness_agents_max` (global, enforced only in `ensure()`, read per spawn through
  `cap_for`), and the project as the unit of concurrency: `project_agent_limit` and
  `project_idle_grace_minutes`, checked in `plan()` BEFORE the global cap, directories compared
  with `shortcuts.same_dir`. None is restart-flagged. A replacement of a conversation's own
  child is never refused.
- The idle reaper is LAZY, at plan time, never a timer (D-1). Idle is marked only where a turn
  ends (`_answer`, `_give_up`), never on a socket disconnect. A reap leaves the session file
  untouched and writes a note into the reaped conversation's own transcript.
- Project slot grants (`_project_claims`) are read and written in ONE region with no `await`;
  release the claim on every failure path (D-1).
- The cap refusal names where the slots are, read from each child's spec. Nothing is queued.
  Settings help text must state only what really frees a slot.
- The slot is the agent's PROMPT, never the voice: the child is idle at its terminal event,
  before audio finishes.
- `HarnessBrain.dispose()` disposes EVERY child (`dispose_all`); `_restart_cleanup` reaches it
  (AC-62).
- A page refresh does not kill a harness turn with a live child (`_turn_survivable`);
  `Hub._orphaned` holds the task reference. Never decide this from `SatelliteSession.state`.

### Turn ownership and addressing

- The daemon mints turn ids. Every outbound frame goes through `send_for_turn`/`owns()`, which
  stamps `chat` (D-6, AC-246).
- `SatelliteSession` owns turns per conversation (`turn_by_chat`, `chat_by_turn`);
  `abandon()`/`_rearm()` release only their own turn.
- `Hub.chat_for(s)` is the ONE resolver of which conversation a device means: what it says it is
  `showing`, else the conversation the daemon holds (D-18). `turn_chat` is fixed in
  `_begin_turn` and never re-read; it reaches the brain as `TurnContext.chat`, and
  `ChatStore.for_turn` never moves the pointer.
- `{"type":"showing"}` is socket state; a reconnect forgets it. Muting a conversation not on
  screen is `Say(spoken=False)`, NEVER `abandon()`.
- `_barge_in` ends a turn only if the session `hears()` it.
- A page stop is `abandon()`, never a copy (AC-111): with `turn`, only a turn that session owns
  in that `chat`; with `chat` alone, `stop_conversation`, and only a turn in flight.
- `Hub.stop_conversation(chat_id)` distinguishes: owned turn (`s.abandon(turn)`), orphaned turn
  (abort the brain), held-idle child (dispose, never abort), none (`LookupError`). An ordinary
  turn colliding with a busy project still raises `CapReached`; only `POST
  /api/project/{id}/stop` (the confirm's yes) stops. `_Host.busy_children_for_project` reads the
  same dicts the collision decides from.
- `Hub.abandon()` asserts an explicit `turn` is one the session tracks (D-18).
- Every terminal path reaches `_rearm(turn)` and names the turn; the ownership check lives
  inside `_rearm` (D-1, AC-2, D-39).
- `_reply` closes the brain's generator on EVERY exit (`_let_go`, D-18). `_let_go` and the brain
  abort in `abandon()` share `BRAIN_LETGO_S`; expiry is loud, never fatal.
- An empty final and a FAILED final differ (D-26): check `Final.failed` first, speak
  `Hub.ASR_FAILED`, re-arm. Never put Butler's words in `Final.text`. Rebuild a Final with
  `dataclasses.replace`.
- `Hub.history` is the router's bounded short memory of what was SPOKEN (AC-64, AC-65, D-20),
  recorded on every exit from `_reply`, cleared by any new or opened conversation.
  `brain_harness` does not read it.
- Nothing is spoken outside a turn. Unsolicited speech has ONE door, `Hub.announce`, which mints
  a turn and speaks only on `desk_session()` (`is_local` socket, AC-541), only with `speak`
  on, waits while the desk is busy and is dropped after `ANNOUNCE_WAIT_S` (AC-14, AC-470).

### Agent questions (AC-49 reversed: AC-234, AC-249, AC-250)

- A question from a `PhoneSession` turn or a `RunSession` goes to the owner's phone
  (`notify.Questions`, setting `phone_questions`); a desk or page turn's does not. Phone answers go
  only through `answer_question`; the sent-message map is in memory only (64 entries). An UNQUOTED
  number or yes/no answers only while exactly ONE phone question is open (AC-527).
- `Hub.ask_owner` questions (`owner-` qid prefix) resolve a DAEMON waiter, never a child; a tool's
  WhatsApp send that the guard holds for confirmation asks this way (RV-2, AC-588).
- `ports.Ask` carries the HARNESS's own `qid`; replies go through `Harness.answer(qid,
  ports.Answer)`, typed (AC-41).
- Questions are BROADCAST, never `send_for_turn`. Nothing about a pending question goes on disk;
  a closed one leaves a transcript line.
- `POST /api/answer`, never a socket frame. An unknown `qid` is 410 naming the reason and is
  never forwarded. Closing reasons go through ONE bounded writer, `_remember_closed`.
- Butler never auto-answers; it passes the harness deadline through and closes as `expired`
  sending nothing. Only `editor` dialogs and dialogs raised with no prompt in flight are
  auto-cancelled.

### Timeouts and progress

- The harness answer stream has an IDLE deadline, never a total one (`HARNESS_IDLE_S` = 600 s,
  D-34); reasoning and tool-argument streaming (`ports.Activity`) re-arm it. It is suspended
  while a question is open. It exits through `_give_up`. `llm.READ_TIMEOUT_S` is 600 s (AC-387,
  AC-388).
- Inside `harness_pi`, one deadline per whole read, never per event (D-1).
- A running turn sends `progress` every `PROGRESS_EVERY_S` through `send_for_turn`; the
  heartbeat is cancelled first in `_reply`'s `finally`. `last` moves only on evidence. No frame
  per token (`thinking`, `tool_input`, `text`). `progress_for` feeds `GET
  /api/chats/{id}/status` (one builder).
- `status_for` derives idle/running/waiting/done/error from held state; only the terminal
  outcome is new state. `ToolStart`/`ToolEnd` become `worker_event` frames keyed by `chat`; a
  toolless turn still gets `started`.
- A stopped turn keeps what it showed (`stopped: true`, AC-394), written before any await.
- A finished agent (AC-695): at `Done`, on a turn where a `WorkerEvent` arrived, `_reply`
  stamps the record FIRST (`ChatStore.mark_finished`: `agent`, `agentElapsedMs` on this turn's
  answer; never a stopped or older one) and then sends one immediate `progress` beat carrying
  `agent` and `agent_elapsed_ms`, which every later beat repeats frozen. The time is the
  heartbeat's own `elapsed_ms` at that moment. Never `speak_end`: it waits for the playout.

### Coding-agent tools and dispatch

- Tools are the `harness_tools` setting, not a grant (AC-29 retired): read_only maps to
  `("read","grep","find","ls")`, full to no `--tools` flag. `--tools` bounds pi's BUILT-IN tools
  only, not extensions.
- `hub._on_ask_confirm` hands a dispatch to `brain.proceed()` with TWO strings: `text`
  (recorded) and `instruction` = `action.summary` (AC-73, AC-28). A blank summary passes None.

### The conversation record (`chats.py`)

- One flat JSON file per conversation, rewritten whole with an atomic replace (AC-35). Fields:
  `id, createdAt, updatedAt, harness, sessionRef, messages, parentChatId, parentMessageId,
  project` (plus `sessionGen`, `origin`, `seenAt`). Do not add `title`, `pinned`, `archived` or
  `channelId`. `harness` is the chat's agent, fixed at its first spawn (AC-771).
- `ChatStore` is a registry (`_open` plus `_current`). A held dict wins over the file. `append`
  takes the chat id; an unknown id is a loud drop, never a write elsewhere. Opening spawns
  nothing.
- `session_ref` takes the chat; minting into an unheld record is refused (D-18).
- `sessionRef` holds a FILE NAME, never a directory; it is joined onto the records root at use.
  Clear a reference only on the harness's literal missing-session words
  (`names_a_missing_session`).
- A conversation with no messages is NEVER on disk; `_write` is the one place that rule lives.
  `_purge_empty` removes legacy empties at boot.
- Persistence and read failures are loud and never fatal (D-1). A corrupt record is a loud skip
  inside `_records`, the one walk of the chats folder.
- `ChatStore.find` matches ids against ids read off disk and never joins a caller string onto a
  path: traversal is unrepresentable.
- Delete: the store UNHOLDS before it unlinks; the minted session sidecar and the running child
  go too; a second DELETE is 404. `_reap_files` treats a missing file as success.
- A move between projects is UNREPRESENTABLE (AC-287, AC-288). `project` is written only by
  `rotate` (birth), `rewind` (a copy's birth) and `migrate_unfiled`;
  `test_chat_belongs_to_project.py` enforces it. Never add `POST /api/project`,
  `switch_project`, `work_in` or `set_project` back.
- The row a conversation is in is DERIVED per request (`ShortcutStore.row_for`, `same_dir`),
  never stored (AC-218). `chats.py` takes a predicate, never a row.

### Conversation routes

- `GET /api/session/current`, `GET /api/chats/{id}` and `POST /api/session/open` answer ONE
  shape, bounded at `MESSAGES_SERVED`; `test_chats.py` compares their key sets. `title` comes
  from `ChatStore.title`, the first user utterance; never invent one.
- `POST /api/session/open` moves the held conversation, spawns and aborts nothing. `POST
  /api/session/new {project}` is the ONE birth (with the spoken "new conversation" and "go to
  X"), clears `hub.history`, and a scheduled run uses `focus=False` (AC-461). Endpoint mode
  answers 404 `NO_CONVERSATION`.
- `GET /api/chats` is newest first, bounded at `CHATS_LISTED` AFTER matching.
  Each row carries the record's `harness` (the running row's "pi 1:42",
  `docs/lantern-ui.md` section 4); search rows too. `?q=` and
  `?project=` are the daemon's (AC-185..AC-187, AC-229) and compose; an unknown project id is
  400, never "show everything". Search and scoped listings go through `asyncio.to_thread`
  (D-25). `_filed` annotates every chat list on the wire.
- DELETE answers the new list; nothing is broadcast. Rename does not exist.
- `ready` carries one session id; do not send the registry without a `plan.md` 4.3 change.

### Rewind (overturns AC-97/D-20, `docs/rewind.md`)

- Copy FIRST, then truncate (AC-189..AC-193). User messages only (400 otherwise).
- The session file moves onto the snapshot and `sessionGen` is bumped so the minted name and the
  spec change and AC-60 replaces the child (AC-192). Generation 0 renders as no suffix.
- `needs_seed` (messages and no `sessionRef`) triggers `seed_note`, read before the question is
  recorded; the seed is cut at `unspokenFrom` (AC-395).
- `POST /api/session/rewind` answers 409 while the conversation is running or waiting (AC-248,
  AC-396).

### Projects and the spawn gate (AC-31)

- The project list is the shortcuts pane's ROWS (`ShortcutStore`). Both gates read it: the
  spoken matcher and `harness_spec_resolver`'s re-check at birth and at every spawn. Neither may
  be softened.
- `settings.match_project` matches rows exactly on label or final folder component (lower case,
  `-`/`_` as spaces); no fuzz, no prefix. Ambiguity raises `AmbiguousProject` and is refused
  aloud. "work in PROJECT x" refuses an unknown name aloud; bare "work in x" is intercepted only
  on an exact match and otherwise is work.
- "work in X" / "go to X" NAVIGATE (`Hub.go_to_project`) through the same open and birth the
  page uses; nothing is moved, spawned, disposed or stopped (AC-290). The speaking device gets
  `{type:"opened", chat}` (`_tell_device`).
- The gate compares directories with `shortcuts.same_dir`, never strings.
- A blank `harness_cwd` defaults to the `TMP` scratch dir, never a guessed project. The agent's
  cwd is derived from the chat's `project` at spawn (`_session_cwd`).
- The root row (the data root) cannot be removed or re-pathed; refused in the store, keyed on
  directory. It is ensured on every boot. Unprojected records are filed under it once at boot
  (`migrate_unfiled`).

### Daemon-answered utterances

- Six utterance kinds are answered in `_on_final` without a model: mode request, new
  conversation, project switch, chat model, "remember that ...", "forget that", plus the library commands in
  `_library_turn`. They are disjoint BY CONSTRUCTION, never by branch order. `spoken_remind`
  (starts "remind me") is a library command; `spoken_confirm` is a command ONLY while a schedule
  proposal waits in that chat.
- The current chat's model (`spoken_model_request`, `Hub._model_turn`, `docs/chat-model.md`
  CM-10): "use X" / "switch to X" / "change the model to X" / "back to the default model".
  Favourites, then the chat's agent's catalogue (`match_models`); one match is set via
  `set_model`, none or several are answered and nothing changes. A bare form naming no model is
  work; a bare name that is a configured project stays navigation. butlerd's rules arrive as
  `hub.chat_models` (`ChatModels`, wired in `build_hub`); no frame is sent.

### Identity and memory (`docs/identity-memory.md`)

- `SOUL.md` and `USER.md` live in `paths.Paths.persona`, created from template only if absent,
  comments stripped, over cap (4,000 / 2,000) REFUSED and the last good content kept. Never
  stored in `settings.json` (`FILE_KEYS`).
- They reach the endpoint brain as labelled system messages and pi as a content-addressed FILE
  PATH on `--append-system-prompt`; text never touches argv (AC-373). The path is a spec field,
  so AC-60 handles freshness (AC-374).
- The router is never shown persona or memory. Never pass `--no-context-files` (AC-376). Labels
  and templates name no language (`language.md` D2).
- `MemoryStore.write` is the ONE write path (D-18): loopback-only routes (403 otherwise),
  endpoint tool calls, and spoken "remember that" from the request text only (AC-380). Every
  write is scanned (AC-436); a same-title note supersedes; `memory/provenance.jsonl` is
  append-only source of truth.
- The FTS5 index is a rebuildable cache (`porter unicode61 remove_diacritics 2`); boot exits
  without FTS5. Private notes never enter the always-on index. All memory I/O goes through
  `asyncio.to_thread`.
- `MEMORY_RULES` is sent only where the tools exist; for a harness brain only with a loopback
  `BUTLER_SELF_URL`.

### Speech output

- Muting (`speak`) removes AUDIO only: `reply`, `speak_start`/`speak_end`, `s.spoken` and the
  record all still happen (AC-110). It is global.
- `stt_enabled` is a page-read setting, not restart-flagged (AC-179).
- `tts_speed` reaches `synthesize(speed=)`; keep its 0.8-1.3 bound unless the segment caps are
  re-sized (AC-107).
- `speech.py` collapses spaces but keeps newlines and post-newline indentation; the separator
  rides on the FOLLOWING piece so `"".join(pieces)` is the document. Brains end with
  `buffer.rstrip()`.
- Segment cuts are measured in WORDS end to end: `" ".join(segments(t)).split() == t.split()`
  for every `t` (D-38).
- The tight first cap is the REPLY's: `hub._speak` passes `first_max_words` from
  `s.speaking_started` (D-18). A `segment` trace line precedes mute and synthesis.
- Text never waits for synthesis: `_speak_text` sends at once, `_TurnVoice` plays in order.
  `_speak_audio` is the ONLY writer of `s.spoken`; `abandon()` halts the voice before aborting
  the engine; a dead segment ends the voice for the turn. Unheard text is told once via
  `{type:"unspoken", from}` and `unspokenFrom` (`ChatStore.mark_unspoken` refuses unless it
  matches).
- Voice and latency lists are the ENGINE's; an empty voice list is a failure
  (`LocalListUnavailable`), and unknown latency presets raise (AC-108, AC-42).
- `_voice_for` is the ONE voice resolver (AC-212).
- Voice preview (`GET /api/tts/preview`) and replay (`POST /api/tts/say`) are HTTP, never the
  socket; distinct tuple handles (`PREVIEW_HANDLE`, `SAY_HANDLE`); preview changes no setting;
  replay ignores `speak`; over-length is refused (`SAY_MAX_CHARS` = `TYPED_MAX_CHARS`), never
  truncated; failures are 400/502, never an empty 200.

### Typed turns and corrections

- `{"type":"typed"}` builds a `ports.Final` and calls `_on_final`; nothing branches on how a
  turn arrived after that (AC-162, AC-163). `_begin_turn` is the one place a turn is minted. No
  `capture_start`. The `speak` setting governs it.
- A typed message abandons a capture through `_barge_in()`; refusals (empty, over
  `TYPED_MAX_CHARS`, bad `corrects`) are checked BEFORE the barge-in.
- Retry is an ordinary typed message. `corrects` names a turn from this session's bounded
  `seen_turns` or is refused; it is echoed on every `final` (null when absent). The original is
  never edited.

### Recognition, ambient and dictation

- `Asr` has two shapes: streaming `open/feed/finalize` and `transcribe(pcm) -> str` for a
  finished clip, taking int16 bytes (AC-68).
- NEVER run two decodes on one `transcribe_cpp.Model` concurrently: both return EMPTY silently.
  The ambient back-fill runs after the final, before the brain; `Hub._decoding` gates every
  whole-clip decode; dictation is REFUSED (409), never queued, while `_recognizer_in_use()`
  (asked of the pipelines, never session state) is true (AC-116). Only the worker thread clears
  `_asr_in_use`.
- Ambient: `POST /api/ambient?turn=` is fire-and-forget 202; size is the one refusal. Overheard
  text rides in `TurnContext.overheard`, never in the utterance, `hub.history` or `chats.py`
  (AC-70); brains get it labelled via `dialogue.overheard_note`; the router never sees it.
- Dictation (`POST /api/dictate`): never an empty 200, never an apology in `text`; 409 busy, 422
  nothing spoken, 502 recognizer failure (AC-115).
- `butlerd.paste_safe` is a fail-CLOSED security filter applied at the route before the
  emptiness check (AC-123): whitespace controls become a space, other C0 and DEL are deleted.
  Never wrap it in a fail-open fallback. Any new paste route calls it.
- Language: one setting reaching every open and decode through `hub._asr_config()` (AC-152), not
  restart-flagged. `auto` is decided once per turn from TEXT (`Hub._turn_language`), latched
  below `DETECT_MIN_CONFIDENCE`, candidates from `ports.voice_languages`; a pinned locale wins;
  under `auto` the `voice` setting is dimmed and ignored (AC-213). `TurnContext.language` is
  never a model instruction (D2). `asr_model`, `asr_backend` and `asr_latency` are wired from
  settings; a value left in `asr_engine` for them is a loud refusal (D-36). Wire any new control
  in that group with a caller-side test. `endpointer` is read from the setting
  (`endpointer_choice`: `BUTLER_ENDPOINTER`, else the setting; a config key is named and loses,
  AC-714).
- Renamed keys go through `settings.RENAMES` above the unknown-key drop.

### Attachments (AC-197..AC-206)

- `POST /api/attach` with the file as body, never the socket and never multipart; owner's size
  limit enforced while reading.
- Attachments pend on the SESSION, keyed to the conversation via `chat_for` (`hub.Pending`),
  cleared on read (AC-199, AC-247). No session is a 400.
- `attachments.py` sniffs magic bytes and refuses unknown types; the stored name is minted; SVG
  is not kept (AC-198).
- Documents are read locally first per `document_reading` (AC-200); extraction failure is loud
  and never fatal. The path travels labelled (AC-201).
- No separate vision model (AC-815): an image goes to the `reply` role; the resolver has
  only `router` and `reply`. `vision_model` is RETIRED (a stored one is dropped by `_load`'s
  unknown-key line). `models.sees_images` is three-valued; refuse only what the connection asserted; no table of
  model names (AC-204).

### Desk-facing routes and stores

- `POST /api/terminal` takes `{cwd, agent}` (AC-136, AC-141); `agent` is a key into the closed
  `TERMINAL_AGENTS` table whose argv tuple is SPLICED, never a command. claude always carries
  `--dangerously-skip-permissions` (D9). It runs `wt.exe -d <cwd> cmd /k <agent>` (macOS:
  `MAC_TERMINAL_ARGV`, osascript with cwd and agent as quoted ARGUMENTS), answers no
  PID, checks `is_dir` itself and applies the `cmd` metacharacter refusal (AC-120). 400 for bad
  agent/cwd, 502 if wt fails. It is the ONE spawn NOT assigned to the Job Object.
- `shortcuts.py` is the ONE binding table and the home of view state (`show_chords`, sidebar
  collapsed/width, `expanded_projects`, `right_panel`), never a setting (AC-8, AC-41). Bad rows
  are logged and skipped at load. Keep retired keys (`open`, `rail_collapsed`, `chat_grouping`)
  readable so `save()` does not drop them. `desk/actions.py` is the key-name table, imported not
  copied.
- `GET /api/settings` adds `bindings` composed by the route; no store means no key (absent is
  not empty, AC-140).
- `POST /api/shortcuts` carries exactly one field (400 otherwise); validation and collisions are
  checked there. Sidebar width is clamped and answered back; a non-number is 400 and leaves it
  alone. `right_panel` is a closed set refused by name; `files_open` is a 400. `GET` adds
  per-row `chats` (one walk, off the loop); `POST` omits that walk by contract. `busy` rides
  both.
- `POST /api/browse` runs `desk/browse.py` as a job-assigned child via `to_thread`: 200 path,
  204 cancel, 502 failure (AC-143).
- `desk.py` owns the desk agent process (AC-121, AC-122): module singleton, started only when
  `hotkeys_enabled`, one at a time, `childproc.assign` after spawn, `desk.stop()` in
  `_restart_cleanup`. `make_app` with an empty `local_url` starts nothing.

### Settings schema

- `modes.py` (what Butler may DO: asymmetric, fail closed) and `settings.py` (preferences:
  symmetric, loud but never fatal) stay separate.
- `brain_mode` is `endpoint`, `harness` or `harness_only` (AC-66, AC-67), stored values frozen,
  `labels` for display, restart-flagged (applied by the automatic restart, AC-715), default
  `endpoint`. Every value must be constructible
  by `build_hub`. `HarnessOnlyBrain` declares no `NEEDS_TARGETS`.
- `Settings.INERT` (and `INERT_UNDER_AUTO_LANGUAGE`) marks inert settings; they stay stored and
  editable. `Settings.ANSWERS` names which model answers per mode.
- `settings.GROUPS` is data served whole (`Group(label, stage, headline, blurb, quick)`, AC-45).
  `_validated_groups` and `_validated_settings` fail at IMPORT on a bad headline, non-contiguous
  stages, or a restart-flagged setting in a `quick` group or with a `quick` string.
- `Setting.quick` is a short display word (two settings: Brain, Tools); `web/` names no setting (AC-41).
- Every visible choice is fully `labels`ed and a group headline reads as words (AC-810, AC-811);
  no label, help, blurb or spoken line shows "harness", a raw value or a key (AC-813,
  `test_settings_wording.py`). Turn modes are Smart / Pause / Long form everywhere, Pause
  length Short / Medium / Long; `Hub.TURN_MODE_SAID` is the spoken copy. Stored values never change.
- Appearance (`look`, quick): `ui_style` first (`lantern`/`caption`, default `lantern`,
  `preview`, AC-678), `theme` (`dark`/`light`/`auto`), `viz_placement`
  (`mic`/`ambient`/`off`, default `mic`; `band` retired by CU-3, a stored one
  falls back with one log line), `voice_effect` (default `lantern`, CU-3),
  `reading_face` (`""` "Style default" first and default, every value labelled),
  `text_scale`; none restart-flagged. `colour_scheme` is RETIRED (`docs/lantern-ui.md` CU-4; a
  stored value is dropped by `_load`'s unknown-key line). `Setting.preview` is legal only on a
  fully labelled choice (`_validated_settings`). Every `ui_style` and `theme` value needs a palette
  in `web/index.html` (`test_lantern_tokens.py`); `reading_face` values other than `""` must
  exist in `web/fonts.css`. `viz_placement` acts only
  when `voice_effect` is `lattice` (AC-262).
- Wake words (`docs/wake-words.md`, AC-803..AC-808): `settings.WAKE_WORDS` is the ONE
  catalogue (id, phrase, page-relative model url); each word's role is `wake_<id>`
  (`off`/`screen`/`new`). `Settings.update` refuses a save leaving every word off (a
  hand-edited file can still load that way). `Hub.wake_frame` is the one builder of
  `ready.wake` and the `wake` broadcast. `_wake_candidate` checks the word BEFORE
  `_barge_in`; `new` births through `_birth_here` (shared with the spoken "new
  conversation") before the turn is minted, and a failed birth is `wake_reject
  new_failed`, never a turn in the old chat.

### Records, secrets and transport

- `paths.py` is the ONE place a record path is spelled (AC-410..AC-416): `--data`, then
  `BUTLER_DATA`, then the legacy layout; a missing or in-repo data root is `DataRootError`.
  Resolve at call time; tests use `monkeypatch.setenv`. Never write a `REPO / "state"` literal.
- `BUTLER_STATE` moves the records alone; `BUTLER_PI_REGISTRY` and `BUTLER_CONFIG` relocate pi's
  registry and `config/models.json`. Tests must set them so the suite never touches the owner's
  records, keys or engines.
- `connections.py` is the ONLY module that reads a secret (`ConnectionStore.resolve`). Serialise
  `Connection.public()`, never the dataclass. No key is written to Butler's disk or served
  (AC-34, AC-37). A key is resolved in memory at call time; pi's registry is read at boot and on
  explicit reload only.
- The harness model picker comes from pi's declared `models[]` (`pi --offline --list-models`);
  brain pickers come live from `/v1/models`.
- TLS is the default when `<ssl>/cert.pem` exists; `--tls` demands it and fails fast;
  `--no-tls`/`BUTLER_TLS=0` force HTTP.
- THREE listeners (`docs/desktop-app.md` DA-5, AC-540): MAIN (`--host`/`--port`, TLS as above),
  LOCAL (`--local-port`) and PROXIED (`--proxy-port`), both plain HTTP on 127.0.0.1 and OFF at
  0, the bare default, so tests and scratch daemons never race the live one; `scripts/launch.py`
  passes 8141/8142. `tailscale serve` targets PROXIED only.
- THE GATE (`access.py`, `docs/remote-access.md` RA-1..RA-11, AC-720..AC-731) runs before
  every route, static file and `/ws`: OWNER on LOCAL, on MAIN from loopback, on PROXIED with a
  `Tailscale-User-Login` in `tailscale_owners` (blank = this PC's login, cached 60 s; tagged or
  unreadable = nobody, 503); DEVICE with a paired `butler_device` cookie; else 401. Before
  that, on every listener: proxy headers refused on MAIN/LOCAL, `Origin` by `ws_origin_ok`,
  `Sec-Fetch-Site` only same-origin/none, plain-HTTP Host only IP or localhost. A new route is
  gated by default; the exempt list (`access.EXEMPT`) changes only with the spec. A DEVICE is
  refused `OWNER_ONLY` routes and `OWNER_KEYS` settings. Gate refusals carry `X-Butler-Gate:
  refused`; `/api/devices` (owner) and `/api/pair` (device) are its routes; `devices.json` holds
  hashes only. Tests reach a route past the gate with `test_listeners._Three.who(kind)`.
  `OWNER_ONLY` also holds the standing-channel routes (H2) and `/api/shortcuts` (project
  rows, `docs/project-roots.md` PJ-1); `shortcuts.upsert` refuses a whole drive or the home
  folder (`whole_disk`, PJ-2), and the route refuses a path that is not an existing folder
  (`must_exist`, PJ-4); `files.clean_relative` refuses a name ending in a dot or
  space (Windows drops it).
- EVERY "is this caller on this machine?" decision goes through `hub.is_local(request)` /
  `hub._is_local(listener, peer)` (AC-541): LOCAL yes, MAIN only from a loopback peer, PROXIED
  never. The listener comes from the accepting socket's port, never a header. `hub.LOOPBACK` is
  read only there and by `build_hub`'s BUTLER_SELF_URL check (`test/test_listeners.py` pins it).
  A new loopback-only route calls `is_local`; it never reads `request.remote`. `is_local` ALSO
  requires `X-Butler-Local: 1` and, when an `Origin` is present, one of `hub.own_origins`
  (messaging.md RV-1, AC-586): a web page in the owner's browser cannot reach a local route.
  `test_local_gate.py` lists every gated handler and fails on a new one it does not know.
- `/ws` cannot carry `X-Butler-Local`: `Hub.handle` calls `hub.ws_origin_ok` BEFORE the upgrade
  (messaging.md RV-25, AC-612): no Origin (never a browser), or `own_origins`, or -- MAIN and
  PROXIED only -- scheme+Host same-origin, over https or with an IP-literal Host (a hostname over
  plain http is what DNS rebinding looks like). `test/test_ws_origin.py`.
- `POST /api/shutdown` is local-only: `_restart_cleanup`, close the pages, exit 0 (AC-543).
- `phonelink.py` (DA-15, DA-16): GET `/api/phonelink` is open past the gate (a settings read) and offers the
  setup action only to a local caller; POST is `is_local` only and runs `tailscale serve --bg
  --https=<phone_link_port>` to the daemon's ACTUAL PROXIED port (off when that listener is 0).
  It never replaces an existing forward on that HTTPS port (the desk's 443 already forwards to
  8787, 8443 to an earlier prototype's 3001) and never calls `funnel` (source-tested). QR PNGs come from the
  node `qrcode` package in a one-shot, no Python dependency.
- `server.py` is dead spike code. Do not extend it.

### Process lifetime and the event loop

- Nothing blocking runs on the event loop. Wrap blocking calls in `asyncio.to_thread`; run
  CPU-bound inference in a child process (D-1, D-25).
- Every child process calls `childproc.assign` immediately after spawn (AC-36, D-24), except
  `spawn_terminal`; failure is loud, never fatal. `assign` is a no-op off Windows.
- Every tree kill goes through `childproc.kill_tree` (taskkill, or a `pgrep` walk plus
  SIGKILL); never spell `taskkill` in a module (`test_macos_port.py`). pi launches through
  `harness_pi.PI`, never a literal `cmd /c pi`.
- `POST /api/restart` acks 200, then `do_restart` runs `_restart_cleanup` BEFORE `os.execv` with
  the original argv. The new pid is not the contract; the port and the settings re-read are.
- Settings apply with no owner restart (`apply_settings.Applier`, AC-713..AC-716), built in
  `build_hub`, started in `_on_start` after the warm-up. LIVE (`asr_latency`, `endpointer`, not
  restart-flagged): build from the current value, `warm()`, THEN assign the hub's reference;
  model and backend stay what the process booted with; a failed warm keeps the old engine,
  loudly. Every `restart=True` setting is applied by `do_restart` once `Settings.pending()` has
  sat unchanged for `SETTLE_S` and `Hub.busy()` is None (turns in any session or orphaned, the
  recognizer, `_Host.busy`: a child mid-prompt; an idle-held child does not count). A new
  construction-time setting needs a swap entry or `restart=True`, never neither (D-36).
  `pending` rides `GET`/`POST /api/settings` and the `settings` frame.
- `pipeline.py` owns the safety nets (no-speech timeout, absolute ceiling); policies cannot hold
  the microphone. Caps key off SILENCE, never elapsed turn time.
- `EndWordEnd` matches the end phrase at the END only, against committed plus tentative text
  (D-41), using the curated `END_PHRASE_ALIASES` (D-40), never fuzzy matching; the phrase is
  stripped before the brain. A turn ending with the phrase unspent is logged.
- The voice trace (`trace.py`) is always on (`BUTLER_TRACE=0` to disable), edges only, never per
  frame, writes to a FILE never the console, rotates at `MAX_BYTES`, and can never cost a turn
  (D-1).

### WhatsApp sidecar (`docs/messaging.md` M2)

- `whatsapp.py` owns the node sidecar (`daemon/engines/whatsapp/`): it runs only while
  `whatsapp_enabled` is on, starting and stopping on change; one random token per boot; every call
  goes through `_call`. A crash restarts with backoff; after `MAX_RESTARTS` the state is `failed`.
  The answer settings (`whatsapp_answer_chats`, `whatsapp_answer_all`, `whatsapp_group_trigger`) are
  pushed to the sidecar's `/config`; `_restart_cleanup` and app cleanup stop it.
- `/api/whatsapp/pairing` (GET/POST/DELETE): unlink needs `confirm` (409 otherwise) and is open to
  the tailnet like every settings route (R2; an unlink costs a re-scan). SENDING is the risky door
  and stays loopback-only (`is_local`, M5). A 503 says why and whether to poll.
- Reads (M4): `Archive` opens `whatsapp.db` READ-ONLY and never guesses a name (zero or several
  matches give candidates, no jid). Sends (M5): `WhatsApp.send` is the ONE door (loopback route,
  agent tools, notify channel); the send switch is the setting, read per send, and a caller's
  `send_enabled` is dropped. A tool's file must pass `containment.file_inside` (project folder
  or data root, real paths, no secret names); the door never opens a caller-named path. Every `/api/whatsapp/{read/*,send,status}`
  route calls `is_local`. Refusals the daemon makes itself (`not_local`, `file_outside_roots`) are
  not in `sent.jsonl`; the sidecar is that file's only writer.
- Inbound (M6/M7): `Inbound` routes owner items from `/events` into `phone_chat`'s chat as
  `PhoneSession` turns (headless, reason `whatsapp`, frames shown on every page; nothing spoken at
  the desk); one turn per chat, 10 waiting; `last_owner_chat` is set in `_begin_turn`; voice-note
  clips decode ONLY through `Hub.transcribe_clip` (30 s windows, waits for a live turn, takes the
  whole-clip lock); replies use `WhatsApp.reply`, never `send`. A phone message is always work:
  only `/new` is a command there.
- Guests (M9, AC-535..538, 563): `whatsapp.Guests` is the ONLY guest path. It reaches a model only
  via `brain_endpoint.GuestCall` (`endpoint_model`, `complete`, no tools), never a session,
  `_typed`, persona blocks beyond SOUL.md, or memory; with `endpoint_model` blank every guest gets
  the fixed "cannot reply" line. Its chat is `origin.kind whatsapp_guest`, notes only;
  `Inbound._target` skips it. Rate limit (20/h) and takeover (30 min) are in memory.
  `chats.is_guest_chat` marks the chat: never indexed for memory search (index `guest` rows, schema
  3), never seeded into a harness child (`needs_seed`), never `phone_chat: current`'s target; the
  page's composer is inert there; quiet is re-checked right before `guest_reply` (RV-21..RV-23).
  Open: a SPOKEN owner turn while a guest chat is on screen still runs in that chat (unseeded).

### File explorer (AC-303..AC-310; rich-chat RC-12, RC-13)

- `files.py` is pure. List one level and preview as JSON (256 KiB, plus `kind`/`media` from
  `artifacts.classify`) through `to_thread`, rooted only at a row. `raw` serves bytes under the
  preview's rules (through `serve_shown`); `save_upload` is the ONE write: a new file, one
  name component, never SKIP or SECRETS, never over an existing file (`open("xb")`, 409), the
  body read in chunks and aborted past `MAX_UPLOAD` (413). Open to the tailnet (R2). Refuse the path lexically first, then resolve both sides and require
  `is_relative_to` (403). Apply SKIP and the secrets denylist to RESOLVED components too. Any
  tailnet caller can read any file under any row (R2 accepted posture, stated in the spec 3.4).

### Rich chat: what an agent SHOWS (`docs/rich-chat.md`, AC-613..AC-640)

- `containment.py` is the ONE guard for a path a model names: `file_inside` (roots, the
  records/ssl/`.butler` folders, and `files.SECRETS` names, on REAL paths). The WhatsApp send
  and `show_file` both call it (RC-11, AC-615); never copy it.
- `artifacts.py` is pure: `classify` (kind from BYTES, RC-6), `ArtifactStore` (snapshots at
  `Paths.artifacts/<chat>/<id>.data` + `<id>.json`, lookups matched against LISTED names),
  `validate_ui` (the closed `show_ui` set, refusals name the path), `TOOLS`.
- `POST /api/artifacts/show` is `is_local` (pi's extension); refusals are 200 text a model
  reads. Only brains with `HAS_SESSIONS` (a record to show into); never the endpoint brain
  or a guest. `GET /api/artifacts/{chat}/{id}` is open (R2). Both artifact and explorer bytes
  go through `butlerd.serve_shown`: nosniff, `CSP: sandbox`, text kinds as text/plain,
  script-capable and `file` kinds as attachments, Range via `FileResponse`.
- `Hub.show_artifact` is the ONE door: `ChatStore.show` (the entry waits for that chat's
  next message of its role) plus a BROADCAST `artifact` frame. The owner's attachments are
  shown the same way as `user` (`_show_attachments`, O-2). `ChatStore(artifacts=)` copies a
  chat's folder on rewind and removes it on delete. `artifacts` is written on a message only
  when non-empty (AC-623).
- O-1: a phone turn's `_Watch` collects its chat's `assistant` artifact frames and
  `PhoneTurns._send_shown` sends each file AFTER the text through `channel.reply(file=,
  caption=)` (size from disk first; `ui` sends nothing; every failure SAID). The WhatsApp
  sidecar's own-chat `/reply` takes one non-ptt file via `buildOutboundContent`; Telegram picks
  sendPhoto/Video/Audio/Document from the BYTES (`artifacts.classify`).

### Butler knows Butler (`docs/butler-help.md`, AC-641..AC-656)

- `guide_features.py` is the ONE feature table; `guide.py` renders the static `block()` (policy plus
  one line per feature, capped at `BLOCK_CAP`, sent by `Persona.blocks` after the reminder rules)
  and `help_text(topic, state)`. The block holds NO state (AC-643): state changes would respawn
  pi (AC-60) and cool the prefix cache.
- `butlerd.guide_state(hub)` reads what is on per call (labels, names, on/off; never a token,
  AC-651). `butler_help` reaches it through `NEEDS_GUIDE` (endpoint) or the `is_local`
  `POST /api/guide/agent` (pi). Every settings group and every offered tool must be covered by
  a row (`test_butler_help.py`).

### Library, schedules, automations, notify (`docs/automations.md`)

- `library.py`: recipes and tasks under `Paths.library`, writes via `memory._atomic`.
  `library.expand` is the ONE expansion grammar (AC-448); every door calls it. Each change
  broadcasts one `{type:"library"}` frame. A bare "run X" is a command only on an exact recipe
  match. `task_add` is capped at 3 per reply and scanned; where it is stored is never a model argument:
  always the main list, tagged by `about` (`docs/todo-list.md`). The Projects block follows
  `TASK_RULES` wherever the tools are.
- Recipe tools (`docs/recipe-tools.md`, AC-657..AC-670): `recipe_find` is `Library.find`
  (read-only, off the loop): this project's and Global's recipes only, every prompt through
  `expand`, cut at 400. `recipe_propose` is the SAME proposal as `schedule_propose`, with
  `kind` "recipe" (`hub.RECIPE_KIND`): one proposal per conversation of either kind, checked
  by `Library.check_proposal` (the recipe half `automations.check_proposal` also calls); yes
  saves the recipe alone, no automation, no run.
- `schedule.py` is PURE: every "now" and zone is an argument. Pass `local_zone()`, never
  `datetime.now().astimezone()` or `zoneinfo`; compare instants in UTC.
- `automations.py`: decide (off the loop, under the lock, no await) then dispatch. Runs birth
  chats with `focus=False` and enter text through `Hub._typed` on a headless `RunSession`
  (`headless` is read in exactly four places). NOTHING in this module stops, abandons or
  disposes (AC-460). A refused run returns to `waiting_busy`, never `completed`. Results are
  sent after the status is on disk; nothing is retried or rerouted.
- Served words (`docs/lantern-ui.md` section 8): the page words no date, status or setting
  itself. `Recipe.public(used_by)` takes `AutomationStore.used_by()` as a REQUIRED argument
  (`usedBy`); a served run goes through `automations.served_run` (`statusWord`, `group`), and
  every status in `STATUSES` has one `WORDS` and one `GROUP` entry; a row's `nextWords` is
  `AutomationStore.next_said` with the scheduler's clock; a heartbeat's `awakeWords` reads the
  store's `quiet` callable. A task's `agent` is the conversation RECORD's `harness`, set only
  by `POST /api/tasks/agent`, never from the body.
- Served TIMES (Step 6b): `schedule.when_words` is the ONE wording of an instant ("3:06 PM
  today", "9:12 AM yesterday", "9:00 AM tomorrow", "7:00 AM on Tue 27 Oct") and
  `schedule.took_words` the one duration ("2:31", "1:02:06"). `served_run(run, now, tz)` adds
  `whenWords` (`scheduledFor`), `tookWords` (started AND ended, else "") and
  `lastOccurrenceWords` (a fold's last, else ""); `library.served_task(task, now, tz)` adds
  `addedWords` and, when done, `doneWords` (else ""), and raises on a time with no offset.
  Routes read them at `butlerd._word_clock(hub)` -- the scheduler's clock and the store's
  zone -- so a test's injected clock is the one the words follow (D-18: pinned on the routes).
  The reminder editor's input values are served too: `runAtInput` on a one-shot reminder row,
  `remindInput` (an hour ahead) on the panel, both `schedule.input_values` in the store's zone.
  Words and values are served only, never stored.
- `notify.py`: one file per channel under `Paths.notify`, re-read on use; `kind` resolves only
  through `ports.NOTIFIERS`. Serialise `Channel.public`, never quote a judged value, scrub
  secrets from every failure detail, never build failure text from an exception or URL. No
  fallback. HTTP via `to_thread`, 15 s. `POST /api/notify` is an accepted unauthenticated write
  (R2, spec section 12).
- Silence and streaks (`docs/proactive.md` PR-1..PR-7, AC-475..AC-480): `SILENCE_FOOTER` is
  ONE constant appended in ONE place (`dispatch`, after `library.expand`; source-tested).
  Only a `completed` run is checked by `is_silent`; a silent run gets `silent`, `seenAt =
  endedAt`, a `{spoken:"silent"}` note and no `_result`. Every failure is counted in
  `_deliver` via `store.failure` (boot `recover()` never reaches it); auto-disable is
  written there, never via `save()`. A new stored automation key must be added to
  `save()`'s `saved` dict or the next save drops it.
- Heartbeat and quiet hours (`docs/proactive.md` PR-8..PR-16, AC-481..AC-487): one
  `kind:"heartbeat"` row per project, no recipe; its checklist lives in `Paths.heartbeat` and its
  prompt is `HEARTBEAT_PREAMBLE` plus the checklist, never put through `expand`. An empty
  checklist settles `skipped_empty` with no chat; none of its skips notify; it never runs inside
  quiet hours; its runs reuse `a["chat"]`. Inside quiet hours `Scheduler._say` holds lines in
  `HeldSpeech` (`Paths.held_speech`); the morning line goes from the tick through `Hub.announce`,
  and the file is cleared only when the line was said.
- Prechecks (`docs/proactive.md` PR-23..PR-25, AC-493..AC-495): `precheck` is a NAME resolved only
  through `PRECHECK_RUNNERS` (.py/.ps1/.cmd) to a regular file directly in `Paths.prechecks`
  (realpath); no route writes that folder (`GET /api/prechecks` lists it). `dispatch` runs it
  before any chat birth via `run_precheck` in `to_thread`: `childproc.assign` plus a nested job
  ended at exit or timeout, so nothing it starts survives. Its output block joins the ONE footer
  site; `precheckDigest` is written only by `succeeded()`. Recipe automations only.
- Reminders (PR-17..PR-20): `kind:"reminder"` rows have no recipe or agent and fire through
  `Scheduler._remind`, never held in quiet hours; a one-shot is done (`doneAt`) when decided.
  `Hub.library_tool` is the ONE dispatcher for `library.TOOLS`. `schedule_propose` (PR-21) and
  `recipe_propose` write nothing before yes (proposals live in hub memory, 10 min, each with a
  `kind`); a schedule's first run is `trial`. Neither
  brain has a clock in its rules: the clock is a separate line per request, so the rules stay
  static and pi's prompt cache survives.
- `PhoneTurns.message` offers every owner message -- a voice note by its transcript -- to the
  question door before queueing; `say` is the ONE reply path (AC-605). The phone queue is
  persisted per channel in `<records>/phone/<kind>.json` (message text included, so records only):
  `waiting` messages re-run in order after a restart; a `started` one is never re-run and gets the
  "I restarted while working on your message" line; answered ones are deleted (RV-13). When
  WhatsApp stops (440, 401, 403 forbidden, sidecar given up) `notify.tell_owner` says so ONCE per
  stop through `notify_default` and the desk (RV-14). Audio and video sound over 10 minutes are
  refused in the chat (RV-16).
- Every owner phone message is logged by id and kind on arrival, and every reply, voice reply,
  refusal or failure by id (`phone.log`); never content. A lost message must be visible in
  butler.log (messaging.md 6.4: live voice notes were filed as "missed" across reconnects).
- `phone.PhoneTurns` is the ONE owner-turn path for every chat app (WhatsApp, Telegram): a channel
  supplies reply/presence/media and raises `phone.ChannelError`; the channel name is the typed
  reason and the question origin (`hub.PHONE_REASONS`, mirrored by the page's `PHONE_VIA`).
- `Channels.live_problems` shows a running channel's problem without unconfiguring it; a `FIELDS`
  entry may carry `default`; `Questions._channel` calls `own()` on every question.
- Triggers (`triggers.py`, PR-26..PR-30, AC-496..AC-499): one file per source under
  `Paths.triggers`, kinds only through `KINDS` (imap, ics, webhook); the password, calendar link
  and webhook token are secrets like a bot token (AU-19 extended): never served, every failure
  text scrubbed (`_scrub`); cursors in `Paths.trigger_cursors`; the first IMAP poll only records
  the highest UID. Trigger text reaches a prompt ONLY through `library.expand`'s `{event}`
  (labelled as data, capped). A chat born from an event gets read-only tools whatever Tools says
  (`automations.from_event` in `harness_spec_resolver`), and stays read-only. `/api/hooks/{name}`
  is token-guarded (constant-time) on every listener.

### Terminals (`docs/chats-and-terminals.md`, AC-760..AC-764)

- `terminals.py` and `terminal_host/` own in-page terminals. The host is a DETACHED plain-`node`
  process (ported from an earlier prototype's terminal host) that owns the PTYs, one `@xterm/headless` screen
  each, so a daemon restart leaves every shell alive (AC-763). `terminals.py` is its only client:
  spawn-if-absent, the HMAC hello, request/reply, `seq`-numbered events, and the routes.
- One host per `<records>/terminals` folder. Pipe (Windows) or Unix socket (macOS, in that folder)
  is keyed by a hash of the folder, so a test's temp records never reach a live host. The list
  persists in `terminals/terminals.json`. Daemon start ADOPTS a running host and never starts one;
  a host is spawned only when a terminal is requested. No host at adopt means every record is ended.
- A terminal is the plain SHELL (`docs/agent-and-terminal-settings.md` SW-8, AC-797): a request
  names a project ROW only; any `agent` but `shell` is a 400. `launch_argv()` is the only place an
  argv is made. Validate before touching the host so a refusal spawns nothing (AC-760).
- `terminals_enabled` (default off, owner-only key, SW-6/SW-7): while off every route and the
  socket answer 404 and nothing reaches the host; switching it off ends every terminal
  (`settings.on_change` -> `Terminals.end_all`), and a boot with it off ends what a host still holds.
- AC-764: the token (`host-v2.token`) never crosses the pipe. The client checks the HOST's proof
  first and sends a squatter nothing more. A wire change bumps `PROTOCOL_VERSION` in both
  `protocol.js` and `terminals.py`.
- Routes are owner-only (AC-761), enforced by `access.py`, not here. Terminal output is never spoken.
- Host code changes do not reach a running host (it keeps its shells); there is no restart route yet.
- Tests: `test/test_terminals.py` (real host) and `node --test test/terminal_host/*.test.mjs`.

## Work Guidance

- Read `plan.md` sections 4.2 (audio path), 4.3 (protocol v1), 4.6 (modes and settings), 4.10
  (ports) before changing anything here.
- New behaviour starts as a failing test (`test/AGENTS.md`), then the code.
- Cite the AC or D number in a comment when a line exists because of one.
- A concurrency rule needs a test that drives a real `HarnessBrain` or real harness; the fake's
  synchronous `dispose()` cannot exhibit await-window races.
- A protocol frame, route field or response shape change updates `plan.md` 4.3 and every route
  sharing that shape in the same commit.

## Verification

```
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q
```

## Child DOX Index

| Child | Scope |
|-------|-------|
| `adapters/AGENTS.md` | Port implementations: real engines and their fakes |
| `engines/AGENTS.md` | Forked spike engines, kept as inherited |
