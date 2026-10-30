# Port adapters

## Purpose

- The only place a concrete engine is named outside `config/`. Everything here
  implements a port from `daemon/ports.py`.
- Each port ships with a real adapter AND a fake, because an interface with one
  implementation is a guess about the future rather than a seam.

## Ownership

- Governs: `daemon/adapters/*.py` and `daemon/adapters/pi_memory.ts`.
- Parent owns: the port definitions themselves and their count (six -- plan.md 4.10).

## Local Contracts

### Registration and selection

- Register every adapter unconditionally, by name, at import (`__init__.py`
  imports them all). Raise on an unknown name; never fall back to a default or stub.
- Pass engine-specific options as CONSTRUCTOR kwargs, never port fields
  (AC-41): `config/models.json` `asr` holds what crosses the port, `asr_engine`
  one adapter's vocabulary. An unaccepted option is a TypeError at boot.
  Conformance cannot prove a kwarg reaches the engine; that needs a per-engine test.
- Give `notify_telegram` NO registered fake: a `NOTIFIERS` kind is selectable
  by a channel FILE. Fake it at the HTTP layer (patch module-level `API` or
  pass `api=`). Its failure text is HTTP status plus Telegram's `description`,
  never an exception string or the URL (the URL carries the token).

### Fakes

- Fakes are not null objects; they reproduce what the pipeline depends on:
  `asr_fake` emits partials on the real 560 ms cadence and exactly one `Final`,
  empty for silence (D-7), and never transcribes silence in `transcribe`;
  `tts_fake` yields multiple chunks at the measured realtime factor and honours
  `abort` by handle; `brain_fake` emits `Dispatch` freely.
- `harness_fake` keeps: a FIRST turn with a failing tool, a no-delta mode,
  `fail_on` (yields `Failed`, never raises), `fail_once`, `fail_after` (fails
  part way through, so the 4.4c replay condition is testable), scripted
  `list_models`, and a record of every `HarnessSpec` it was started with.
- Every terminal event must be producible by a fake; every harness adapter
  owes `test_harness_conformance.py` a `FAIL_ON` row (AC-48).

### Harness child (`harness_pi`)

- Decide everything about the child in the argv (AC-31); the port has no
  setter, so a different `HarnessSpec` means dispose-plus-start.
- `spec.tools` non-empty emits `--tools <list>` (read_only); empty emits NO
  flag (full, pi's default set including `bash`). Never emit `--tools ""`.
  This is the `harness_tools` setting, not the retired AC-29 grant.
- Pass the persona to pi as a Butler-minted FILE PATH on
  `--append-system-prompt`, never as argv text (AC-373). Never add
  `--no-context-files` (AC-376: the project's own `AGENTS.md` is per-project identity).
- When `spec.memory_url` is set, add `--extension <this folder>/pi_memory.ts
  --butler-url <url>`, plus `--butler-chat <spec.chat_id>` (AC-440, AC-454);
  the resolver names every extension tool (`memory.TOOL_NAMES`,
  `library.TOOL_NAMES`, `artifacts.TOOL_NAMES`, `guide.TOOL_NAMES`, and the
  allowed WhatsApp tools) in read_only's `--tools` because pi hides unnamed
  extension tools.
- Keep `pi_memory.ts` logic-free: tools POST to loopback-only routes
  (`/api/memory/*`, `/api/tasks/agent`), refuse non-loopback URLs, and skip
  cert checks only for loopback. Its tools mirror `memory.TOOLS` (a test holds
  them together). Never install it into the owner's pi config.
- `pi_memory.ts` sends `X-Butler-Local: 1` on every call (RV-1); `whatsapp_send` always sends
  `chat` and never `confirm` -- a held send is confirmed by the OWNER through `Hub.ask_owner`, and
  `file_inside` refuses the records folder, `ssl` and `<data>/.butler` (RV-2, AC-587).
- WhatsApp tools in `pi_memory.ts` mirror `whatsapp.READ_TOOLS`/`SEND_TOOLS` and
  stay INACTIVE unless named in `--butler-url`'s `whatsapp=` query, which the spawn
  spec sets from the settings (so flipping a setting replaces the held child);
  under read_only the same names join `--tools`. The endpoint brain gets them
  through `NEEDS_WHATSAPP` (`AgentTools.offered`/`call`, settings read per request).
- `pi_memory.ts` mirrors ALL of `library.TOOLS`; `reminder_add`, `schedule_propose`,
  `recipe_find` and `recipe_propose` POST to the loopback `/api/library/agent`
  (`butlerd.LIBRARY_AGENT_TOOLS`, every other name refused; `test_recipe_tools.py` holds the
  declarations together); each prompt gets the clock line (`before_agent_start`). The
  endpoint's `tasks` callable is async `(name, args, chat, turn)`.
- `show_file` and `show_ui` in `pi_memory.ts` mirror `artifacts.TOOLS` (descriptions verbatim,
  `test_artifacts.py` holds them) and POST `{tool, args, chat}` to the loopback
  `/api/artifacts/show`; the PATH goes as the model wrote it and the daemon resolves and
  judges it (`docs/rich-chat.md` RC-2, RC-11). They join read_only's `--tools`. The endpoint
  brain is NOT offered them: it keeps no conversation record.
- `butler_help` in `pi_memory.ts` mirrors `guide.TOOLS` and POSTs `{topic}` to the loopback
  `/api/guide/agent`; the endpoint brain gets it through `NEEDS_GUIDE` (an async `(topic) -> text`)
  and routes it in `_answer_tool` (`docs/butler-help.md` BH-2).
- `GuestCall` (brain_endpoint) is `llm.Client.complete` only: never add tools, USER.md or memory
  to it (AC-536, source-tested).
- Telegram messages are persisted in the shared phone queue, resumed when polling starts.
- `notify_telegram` is INBOUND through one `Inbound` reader (one channel with `inbound` on): BOTH
  sender and chat must be the owner; the offset is saved before handling (a crash loses one
  update, never runs it twice); a 409 becomes the channel's live problem plus backoff; callback
  tokens live in memory only; the long poll runs on a daemon thread.
- `notify_whatsapp` is INBOUND (the optional capability, not a port: plan.md 4.10): `own()` is the
  own chat and `ask` uses the reply door.
- `notify_whatsapp` resolves `to` inside `problem` (the dict `Channels.save` then
  stores carries the jid; a resolve hook in `notify.py` would be cleaner) and sends
  through `whatsapp.door()` with `confirm: true`: saving the channel is the user's
  confirmation for that recipient.
- Spawn through `cmd /c` (pi is `pi.cmd`; plan.md 4.5). Because cmd re-parses
  the line, run EVERY argv value through `reject_shell_metacharacters`
  (refuses `" % & | < > ^`, loudly, by name). It is PUBLIC because
  `butlerd.post_terminal` also calls it (AC-120); keep ONE refusal list.
- Split JSONL on LF only, never U+2028/U+2029. Judge a failed start at the
  child's CLOSE with the stderr tail, never the first error line (4.4c).
- Turn pi's auto-retry off (4.4c). `stopReason: "error"` + `agent_end` is a
  failure; `"aborted"` is barge-in (D-4). Drop EVERY event once a prompt is
  over; a terminal event counts only for the prompt id `prompt()` minted (AC-48).
- End each text block with "\n\n" at `text_end` / `toolcall_start` /
  `message_end` / `turn_end` (`_Mapper._close_text`), at the END not the start
  of the next block (latency: the next block follows a 3-8 s tool run).
  A `Say` may lead with "\n\n". Conformance
  `test_the_deltas_keep_a_paragraph_break_between_turns` holds every adapter to it.
- Keep close handling in `_read_stdout`'s `finally` and map each record inside
  its own guard: a malformed record costs one event, never the turn's ending;
  a child dying mid-prompt ends the prompt (AC-48, D-1).
- Dialogs (AC-49, REVERSED per `docs/parallel-agents.md` D2): hand
  `select`/`confirm`/`input` to the human. Auto-cancel only `editor`
  (`AUTO_CANCELLED_METHODS`) and dialogs raised with no prompt in flight
  (`cancellation_for`). Never reply to fire-and-forget methods.
- List models through `Harness.list_models()`, a CLASSMETHOD reached via
  `butlerd.LOCAL_ASYNC_TARGETS` (async: it shells out, D-25). Read pi's table
  from STDERR; answer `provider/model`.

### Harness children (`harness_claude`, `harness_opencode`)

- Ported from an earlier prototype's claude backend (`harness_claude`) and built from opencode 1.19.3's
  recorded output (`harness_opencode`); each mapping table is its module docstring, its
  recordings are `test/assets/harness/`. Same layering as pi: a pure `_Mapper`, then the transport.
- Permissions are bypassed (OD-1, owner 2026-10-29): `--permission-mode bypassPermissions`,
  opencode `--auto`. Neither raises an `Ask`; `answer` returns False. read_only maps to the
  engine's OWN read set (Claude Code `--tools Read,Grep,Glob --strict-mcp-config`, opencode
  `--agent plan` without `--auto`), never pi's tool names.
- The opaque session id lives in `spec.session_file` (`session_id_file`); an unreadable file
  RAISES. On the CLI's literal missing-session words the adapter forgets the id itself.
- Claude Code: abort is a stdin `control_request` interrupt, its is_error `result` maps to
  AgentEnd, and the tree is killed after `INTERRUPT_GRACE_S` if the turn does not end. claude is
  `claude.exe`, spawned without `cmd /c`.
- opencode: one `opencode run` per prompt, the prompt on STDIN; the process EXIT is the terminal
  event; spawned through `cmd /c`, so argv values go through `reject_shell_metacharacters`.
- Neither gets Butler's tools (`memory_url`, `chat_id` unused) nor, for opencode, the persona.

### Harness brain (`brain_harness`)

- Register TWO brains sharing one `_Host` (AC-66, AC-67): `HarnessBrain`
  (router in front of `HarnessConverse`) and `HarnessOnlyBrain`
  (`brain_mode=harness_only`, no `NEEDS_TARGETS`, no `CAN_PROCEED`). Never
  register a converse half on its own -- it would drop the routing silently.
- Hold a child per CONVERSATION (`_Host._children`, keyed by chat id). Each
  turn compares `spec_for(...)` with the running child's spec: EQUAL reuses,
  DIFFERENT disposes and spawns from `harness_for(chat)` (AC-59, AC-60, AC-771). The spec
  comparison is the whole invalidation rule; add no event list beside it.
- Derive the child's cwd from the chat record at every spawn (`_session_cwd`,
  AC-289), through `desired_for(chat)` with the record the turn read (AC-241).
- Hold a child only across turns that COMPLETED: barge-in, a caller that
  walked away (`hub._let_go`), and a failed turn all dispose it. A turn whose
  predecessor never released is dropped LOUDLY, never put on a busy child.
- `HarnessConverse.warm` stays EMPTY (AC-59); `HarnessBrain.dispose()` is
  what `butlerd._restart_cleanup` reaches (AC-62).
- Every turn makes a sound: one fixed line BEFORE the spawn on every turn
  (D-10), then streamed sentences, else `turn_end` text (only if nothing was
  said), else a fixed line, else the failure (AC-47). A failed SPAWN is a
  spoken failure and one `Done`, never a refusal.
- Replay a failed turn ONCE, silently, only while nothing has been said, from
  the verbatim prompt (4.4c). Clean ONCE (the router's `instruction`, AC-73),
  then replay verbatim. Record the owner's own words, never the cleaned prompt (D-20).
- Wrap `dialogue.overheard_note(ctx)` in FRONT of the request, built once; an
  empty note wraps nothing (AC-68, AC-70). `brain_endpoint` carries it as its
  own SYSTEM message.
- Every answering path yields exactly one `Done` (AC-48, D-1); barge-in yields
  ZERO. Match `abort` on the TURN; never forward every abort (AC-43).
- Record roles and text once per turn (a failed turn records the line the
  room heard). Decide no tool posture here: it rides in on the `HarnessSpec`.

### Brain capabilities

- A brain gets what it needs by declaring a CAPABILITY, never by butlerd
  testing its name, and is handed CALLABLES, never a store. NO adapter reads
  the settings store (4.10).
- `NEEDS_TARGETS` gets the `TargetResolver` (`resolve(role)` per turn).
  `NEEDS_HARNESS` gets `harness_for(chat)` (`butlerd.harness_factory`, called only
  at spawn) and `spec_for` (`butlerd.harness_spec_resolver`, called per turn;
  a blank cwd defaults to scratch `TMP`, AC-31). `NEEDS_MEMORY` gets the `MemoryStore`.
- `EndpointConverse` with a store sends `memory.TOOLS`, runs calls through
  `MemoryStore.call_tool` in `asyncio.to_thread`, at most `MAX_TOOL_ROUNDS`
  (4), the last with `tool_choice: "none"` (AC-439). Bad arguments and
  refused writes are ANSWERED to the model, never raised. Without a store it
  sends no `tools` key (AC-444). The router is shown no tools.
- A capped reply is shown, not spoken (AC-40): fragments go out as
  `Say(..., spoken=False)` and the turn ends `Done(truncated=True)`; if
  nothing complete arrived, speak a fixed line.

### Speech engines

- `Tts.synthesize` takes a `handle` and `abort` matches on it, freeing the
  ENGINE, not just the wait (`test_abort_frees_the_engine_not_just_the_caller`, D-13).
- `voice` must reach the engine; conformance takes it from the adapter's own
  `info["voice"]`, and `test/test_tts_kokoro.py` compares two voices (AC-7b).
- Every `Asr` adapter owes a whole-clip `transcribe` as well as a session; the
  real one converts to float32 before `Session.run()` (raw bytes return "").
- Never run two decodes on one `transcribe_cpp.Model` at once: both return
  EMPTY transcripts, silently (`docs/ambient.md`, AC-116).
- `Asr.finalize` blocks: run it off the event loop. Load models in `warm()` (D-10).
- Scale the finalize budget with the utterance's audio
  (`FINALIZE_FLOOR_S`/`FINALIZE_PER_AUDIO_S`/`FINALIZE_MAX_S`, D-26), reset
  per utterance. Every SYNTHESIZED empty Final carries `Final.failed`.
  Model-load and per-operation timeouts are SEPARATE budgets (D-5).
- Serve `info` from a static table or cache; it is read on the event loop in
  `hub._hello`. If not known yet, say so (`"unknown"`, `warmed: False`),
  never go and find out (D-25). Conformance asserts this with a clock.
- `tts_kokoro` learns voices ONCE in `warm()` (`_learn_voices`, AC-108); a
  `voices` entry is `{"id", "language"}`, passed through unfiltered -- WHICH
  to offer is the daemon's decision (AC-155). Read it via `ports.voice_ids`.
- Non-English phonemes come from `daemon/engines/tts/tts-phonemes.js`; adding
  a `LANGUAGES` row means listening to it.
- `tts_pocket` (the default on all three machines, owner 2026-10-30) runs the
  pinned Rust binary from `pocket_pin.py` (`vendor/pocket-tts/`, installed by
  `scripts/fetch-pocket-tts.py`); a missing binary is a `PocketError` naming
  that command, never a fallback to Kokoro. One asset per platform
  (`pocket_pin.ASSETS`: Windows x64, Apple Silicon); any other machine is refused.
- Pocket loads one model per LANGUAGE, so a voice id is `<subtag>_<name>`
  (`pt_rafael`), each language's native voice first; locales are the
  recognizer catalogue's (`pt-BR`), so a pinned locale finds a voice.
- Pocket refuses any speed but 1.0 (no rate control); conformance xfails that
  one case strictly. Abort closes the HTTP response, which stops generation.
- One `_Server` per language, kept resident (~310 MB each, [MEASURED] English
  int8 2026-10-30); `HF_HUB_CACHE`
  is always passed, because cmd.exe has no HOME and the exe would miss its cache.

### Boot and child lifetime

- Boot calls every engine port's `warm()`: loud on failure, never fatal (D-1).
- Call `daemon/childproc.assign(proc)` in the SAME function that spawns,
  immediately after (AC-36, D-24). Call sites: `tts_kokoro._Sidecar.start`,
  `tts_pocket._Server._start`, `harness_pi._spawn`, `harness_claude._spawn`, `harness_opencode._spawn`. It warns and
  continues on failure.
- Keep the other mechanisms; none replaces the job object: `atexit`
  (`_stop_all_sidecars`, `_stop_all_servers`), `harness_pi.dispose` (close stdin, then
  `taskkill /F /T`), `endpointer_smartturn`'s in-child watchdog. Tests that
  start a real child dispose it in a `finally`.

## Work Guidance

- Adding an adapter: implement the port, register it, add it to the matching
  conformance suite in `test/conformance/`, and add it to the daemon's selection
  table. It is not done until it passes the same suite the fake passes (AC-7a).
- If a conformance test cannot hold for an engine, mark that parameter
  `xfail(strict=True)` with the reason and fixing plan step. Never weaken it.

## Verification

```
.venv\Scripts\python.exe -X utf8 -m pytest test\conformance\ -q
```

## Child DOX Index

No child AGENTS.md yet.
