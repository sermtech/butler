# Forked engines

## Purpose

- The working speech engines inherited from the unified-stt spike: the
  transcribe.cpp streaming wrapper, the Smart Turn child-process wrapper, the
  model catalogue, and the Kokoro node sidecar.
- Kept as a unit so the boundary between "what we inherited" and "what Butler
  wrote" stays visible.

## Ownership

- Governs: `daemon/engines/` including `tts/` and `whatsapp/`.
- Parent owns: the ports these are wrapped by, and everything in `adapters/`.

## Local Contracts

### Inherited boundary

- Treat this subtree as inherited, working code. Adapt around it in
  `daemon/adapters/`; do not rewrite it.
- Keep `stt_engine.py` and `turn_engine.py` standalone modules (no
  `__init__.py`; callers insert this directory on `sys.path`), so the scripts
  in `test/manual/` still run.
- Read model capabilities from the loaded GGUF via `catalog.json`, never a
  hard-coded per-slug table.
- Run Smart Turn, and anything numpy/onnx-heavy per turn, in its own child
  process with `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`
  set before numpy is imported (measured 1960 ms threaded vs 33 ms capped child).

### Kokoro sidecar (`tts/`)

- `tts-sidecar.mjs` is the one rewritten file (the transport): framed PCM per
  sentence plus `POST /abort`. Its wire format is in its header and parsed by
  `daemon/adapters/tts_kokoro.py`; change both together.
- The voice is per REQUEST; `KOKORO_VOICE` only selects the voice pre-warmed at boot.
- Keep `tts-worker.js` handling `{type:'abort', id}` OUTSIDE its FIFO queue,
  and never drain `synthesizeStream` in the sidecar -- that is what makes abort reachable.
- An abort still strands the sentence inside `generate()` (ONNX is not
  interruptible). Fixing that needs a public abort-by-id on `TTSService`, a
  change to inherited code: name it, do not do it quietly.

### Power-throttling opt-out (`winqos.py`)

- Windows 11 demotes a background process's ONNX threads to the E-cores
  (EXECUTION_SPEED throttling); kokoro synthesis then runs ~0.3-0.9x realtime
  instead of ~4x and speech gaps between sentences. `winqos.py` is the ONE
  definition (SetProcessInformation ProcessPowerThrottling, ControlMask=1,
  StateMask=0); the setting is per process and NOT inherited by children.
- Every CPU-heavy voice process applies it: `butlerd.main` (STT runs
  in-process), `turn_engine._init_worker` (Smart Turn pool child, on itself),
  `tts_kokoro._Sidecar.start` (the sidecar), `tts_pocket._Server._start`
  (each pocket-tts server), and every kokoro worker via the
  `TTSService.onWorkerSpawn` hook in `_spawnWorker` (first start AND respawn),
  wired in `tts-sidecar.mjs` to `tts-qos.js`, which runs `winqos.py <pid>` with
  the interpreter in `BUTLER_PYTHON` (set by the daemon). Node has no FFI, so
  the sidecar shells out rather than the daemon polling for its grandchildren.
- Loud, never fatal: `winqos.apply()` and the hook log a WARNING to the
  daemon log and carry on. A new CPU-heavy voice child needs the same call.
- Gated by `test/test_winqos.py` (reads the state back with
  GetProcessInformation, covers start and respawn).

### espeak-ng phonemes (D-37)

- Pass espeak's `-b` as a SEPARATE argv pair, `'-b', '1'`. Never `-b=1`
  (silently wrong: every accent becomes mojibake).
- Normalise text to NFC before phonemizing; NFD loses accents silently.
- Test input for a language must carry that language's own marks.
  Gated by `test_tts_kokoro.py::test_an_accent_does_not_multiply_the_phonemes`
  and its two companions.
- Check `tokenizer.json`'s 115-symbol vocabulary before adding a language:
  out-of-vocabulary phonemes are deleted silently.
- Latent, deliberately unfixed: `tts-sidecar.mjs` builds the request body with
  `body += chunk`, which corrupts multi-byte UTF-8 split across chunks. Safe
  only while callers POST ASCII-escaped JSON (aiohttp `json=`); fix it before
  anything POSTs raw UTF-8 to `/tts`.

### WhatsApp sidecar (`whatsapp/`)

- A PORT of an earlier prototype's WhatsApp code (`docs/messaging.md` MS-1..MS-15), not inherited spike code: it
  is a transport only (socket, linking, archive, send guard, media, inbound
  tagging); chats, agents, speech and settings stay in the daemon.
- `wa-sidecar.mjs` binds 127.0.0.1:0, prints its port as the FIRST stdout
  line, and refuses any request without `X-Butler-Token` (env
  `BUTLER_WA_TOKEN`); records live in env `BUTLER_WA_DIR`. Routes are listed
  in its header; change them together with the daemon's client.
- Never drop `markOnlineOnConnect: false`, `fetchLatestBaileysVersion()`, or
  `shouldSyncHistoryMessage` beside `syncFullHistory: false`: each cost a
  real link to learn. `test/socket-config.test.mjs` pins them (AC-502).
  Baileys is pinned to exactly `7.0.0-rc.9` in `package.json`.
- `console` belongs to the libraries and prints TEXT only (`wa-console.js`): libsignal dumped
  whole SessionEntry objects (privKey, rootKey) through `console.info`, and the daemon copies
  this process's output into butler.log. The sidecar's own lines go straight to the streams
  (`say`/`warn` in `wa-sidecar.mjs`); never print an object, Buffer or key through `console`
  (`test/console-guard.test.mjs`, which starts the real sidecar).
- A known contact is a non-blank address-book `name` or a chat with a non-Butler `from_me`
  message (`isKnownContact`; its Python twin `Archive.is_known`, RV-20): a push `notify` name
  alone never counts. `ownChatType` labels the user's `fromMe` messages per message in every chat,
  so takeovers are never read from a batch label (RV-23).
- Baileys is loaded ONLY by `await import()` after `guardConsole()` (RV-10): a static import
  would load libsignal before the guard. Baileys' own logger is `makeTextLogger` (message and
  field names, digit runs masked), never objects.
- Every send carries a `BUTLER_ID_PREFIX` message id (WhatsApp Web shape, `3EB0...`); an
  own-chat message with that prefix is never routed, missed or a takeover (RV-12). Ids reach
  the daemon at most once (the archive's `routed` table, RV-4). The archive is WAL with a 5 s
  busy timeout (RV-6). A failed version fetch is an error state, never the bundled version;
  retries back off 3 s to 5 min; 401, 440 and 403 are terminal (RV-5). `/events` buffers 200
  items while no subscriber is attached and `ready.dropped` counts the rest (RV-11); `/reply`
  is capped at 30 a minute (429).
- Every outbound text ends with the marker `\u2063` (MS-15); inbound
  self-chat text ending with it is archived, never routed.
- `/reply` and `/presence` reach the user's OWN chat only; replies are audited `reply: true` and
  not charged to the rate limit. Live or missed in the OWN chat is decided per MESSAGE by
  `ownChatType` (written after sidecar start, delivered within `LIVE_LATE_S`), never by Baileys'
  batch `type` (which labels a whole buffered batch, and everything held across a reconnect,
  `append`); the sidecar's own sends are neither routed nor missed (`test/delivery.test.mjs`,
  messaging.md 6.4). Owner message items carry `quoted` (the quoted message id, MS-22).
- `/guest-reply` re-checks `isGuestChat` against the LIVE config, requires the "Butler: " prefix,
  and audits `guest: true`.

## Work Guidance

Changes here should be rare. When the port needs something this code does not
do, add it in `daemon/adapters/` rather than here -- that is what keeps the
inherited boundary visible and the upstream diff small.

## Verification

```
.venv\Scripts\python.exe -X utf8 -m pytest test\conformance\ -q
node test\bench_tts.mjs
.venv\Scripts\python.exe -X utf8 -m pytest test\test_whatsapp_node.py -q
```

## Child DOX Index

No child AGENTS.md yet.
