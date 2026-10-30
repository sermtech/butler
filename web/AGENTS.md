# Satellite page

## Purpose

- The browser page is simultaneously the SATELLITE (microphone, local wake
  detector, speaker) and the WEB UI (transcript, settings, permission mode).
- It is a microphone and a speaker with a wake detector. It owns no policy.

## Ownership

- Governs: `web/` -- the page, its worklets, the wake detector, the settings UI.
- Parent owns: the protocol definition and every decision about when a turn ends.

## Local Contracts

### Build, assets, storage

- No build step: no bundler, no npm, no CDN, no fetched fonts or assets. Plain
  JavaScript modules, served as-is by the daemon.
- Fonts are vendored: `fonts.css` plus `web/fonts/*.woff2`, fetched once by
  `scripts/fetch-fonts.py` and gitignored. Never link a font CDN.
- `manifest.json` and `icon.svg` are local (AC-106). No service worker and no
  offline shell: a cached page with no daemon would claim to be listening (AC-91).
- No browser storage of any kind: no `localStorage`, no `sessionStorage`
  (AC-8, D-2; grep-gated). Per-device UI state that must persist (sidebar
  collapse and width, expanded projects, right panel, chord disclosure) lives in
  the DAEMON's shortcuts store, never in a setting.
- ASCII only, control bytes included (`test_the_shipped_client_is_ascii`).
- `onnxruntime-web` stays at 1.18.0 with sequential sessions in `wake.js`
  (load-bearing on iOS); never upgrade without a device test.
- `mock.html` and `mocks/` are design references (URL `/mocks/index.html`);
  shipped code never imports from them. A mock says on its face what the daemon
  does not serve and embeds CAPTURED payloads; `makeVoiceSynth` never ships.

### The page names no setting (AC-41)

- The settings sheet renders from the daemon's schema. Nothing in `web/` may
  name a setting, group, stage, section, choice label or value table. AC-41 is
  enforced by review plus `test_settings_api.py`/`test_seams.py`, and
  `test_settings_sheet_page.py` fails on any group or setting key spelled as a
  string literal in `settings.js` (one listed debt: the shared favourites
  list's key, until the daemon serves it on the spec).
- Hand-built carve-outs name NO setting: the permission mode and connections
  blocks (markup in `index.html`, built by `settings.js`) and reset all. The
  restart bar has no button (AC-715). The two blocks' markup says which
  served section lists them (`data-section`); that is the only section word in
  `web/`.
- Connections panel (plan.md 4.4b) may name only label, base URL and env-var
  NAME. Never a model, never a setting, never a key value (not typed, sent or shown).
- A group's `collection` (AC-471, `docs/automations.md` 7) renders via
  `buildCollection(url)` from `{noun, items, kinds}`; the page names no item, kind,
  field or route. A `secret` field is a password input that starts empty and is
  never filled from daemon data; replacing a token is Add with the same name.
- Quick surfaces are built from the daemon's per-setting `quick` flags: the
  model popover `#modelPop` in the composer's row, opened by two chips
  (AC-688). A new quick setting appears with no edit in `web/`. The group-level
  `quick` on `look` has no reader since the look popover was deleted (Step 3b).
- The chips follow the daemon's `answers` pointer (the setting whose model
  decides in the current `brain_mode`, else the first quick setting served):
  `#agentBtnName` prints its short `quick` word, `#modelBtnName` its value. The
  chat's own agent and model are a different chip (`#chatAgentChip`, below).
- `menuControls` (settings.js) draws ONE named setting's control outside the
  surface from its served spec: the account menu's Appearance row is `theme`
  (Dark / Light / Auto from its labels). The key is app.js's, as
  `applyPageValues` already names it; values and words are the daemon's.
- `ui_style` is written as `data-style` and `theme` as `data-theme` on `<html>`
  (`applyPageValues`); `<html>` carries the default style from the first byte.
  `theme: auto` is resolved by the STYLESHEET under `prefers-color-scheme`
  (`docs/lantern-ui.md` CU-6). `reading_face` IS the CSS family written into
  `--read` (Butler's replies), and its "" ("Style default") REMOVES the
  override (CU-5); `text_scale` is written into `--scale` (capped at 1.15, R7).
  No map from key to value in `web/`. Every `font-size` is
  `calc(var(--scale) * Npx)` or `calc(var(--scale) * var(--readsize))`
  (gated by `test_seams.py`).
- `settings.js` owns its generated class names; every rule for them is scoped
  to `#panel` (the sheet), never a bare class rule -- the shell has its own
  `.field`, `.swword` and `.btn`.
- Control ids are prefixed per surface (`set-`, `look-`, `pick-`): labels point at ids.
- `settings.js` emits `label.set-name` and `h2.label`; the element types are
  load-bearing for accessible names and headings (AC-100, AC-101). Never tidy
  them back to `span`/`div`.
- Under `language: auto` the `voice` row draws DIMMED by the daemon's
  `inactive`; never un-dim it in CSS (AC-207..AC-215).

### Settings sheet (`docs/lantern-ui.md` 5.1, AC-700..AC-705; AC-85..AC-87, AC-89, AC-90 hold)

- Settings are ONE modal sheet over the live page (`#panel`), never a separate
  URL: navigating away would kill mic, socket and playback. `inert` on
  everything else while open (except `#scrim`, the dimmed page around it,
  whose press closes it), on itself while shut, `aria-modal="true"`;
  Escape keeps settings' place in the global order. Desk: about 1080 x 760,
  centred, radius from the style's scale; `#panel.open` turns `visibility`
  on with no transition (a transition holds `hidden` for a frame and refuses
  the opener's `focus()`).
- `#panel` is `overflow: clip`, never `hidden`; `.pscroll` (`#setScroll`) is
  the page's only scroller, `.nlist` the list's.
- Left (AC-700): search, then the nav `buildNav` draws from the served
  `sections` (one heading each, served order) with each group under its
  `section` in served order and the carve-outs first in theirs; a stage number
  where the daemon gave one. Right: ONE category page (`showPage` hides the
  rest; nav `aria-current="page"`). No scroll-spy: AC-83 and AC-84 are
  superseded.
- The signal-path strip (AC-701) is the staged groups with their headlines,
  built once, MOVED onto whichever staged page is shown, repainted from every
  value path (AC-56); a stop opens its page. On a phone it wraps into rows of
  three, never a sideways scroll (owner 2026-10-29: six stops ran off a 390 px
  screen with no cue).
- Search (AC-702) filters by a schema-built `data-hay` across every page:
  results are the matching pages in nav order, each under an "Open" line, only
  matching rows, folds opened (and put back after), the nav counting matches,
  "Nothing matched" said; `/` focuses it while open.
- The restart bar (`#commitBar`, AC-703) is docked in the page pane and
  counts the daemon's `pending` (AC-716: from every full read and every
  `settings` frame, `applyChanged(changed, pending)`), naming each by its
  label. It says "Applying N change(s): Butler restarts when idle" and has NO
  button: the daemon restarts itself once nothing is running (AC-715). The
  page keeps no baseline of its own. It is `inert` while hidden.
- Rows: label and help left, control right, in `.cards`; prose, lists, model
  pairs, discovered pickers, preview cards and long button rows stack under
  their words (`stacks`, by kind and length, never by name). Help is two lines
  then "More" where the rendered box is cut (`fitHelp`). The key is not
  printed (it is still searched). The served `default` is said in WORDS,
  never a mark (owner 2026-10-29): a select option reads "<label> (default)"
  (not twice when the label already says default), a segmented button
  carries a small `.segdef` "default" UNDER its label (no wider than the
  label) and an `aria-label` of "<label> (default)", a preview card "Default". A save is visible:
  `.set.busy`/`.set.saved` paint `.fb` ("Saving", "Saved", AC-171).
- Blurbs come only from the daemon's `Group.blurb` (AC-87), as the page's lede;
  a group without one prints nothing. `advanced` is a schema flag, never
  inferred.
- A choice the daemon flags `preview` is drawn as preview cards (AC-704): each
  miniature carries its value as `data-style` and the page's `data-theme`, so
  the style blocks paint it; the served default is marked "Default".
- Phone, 900 px and below (AC-705, supersedes AC-88): full screen, a back
  stack in `data-phone` ("list" | "page", ONE writer, `setPhone`), `#setBack`
  on a page, a typed query showing its results under the search box; 44 px
  targets; grid columns `minmax(0, 1fr)`, never `1fr`; `min-width: 0` on flex
  text inputs and selects only.
- `app.js` opens it through `openPanel(open, key)`, which calls
  `settings.reveal(key)`: a picker's "More in settings" lands on that
  setting's page; a plain open keeps the page last read (the list on a phone).
- Inactive settings are DIMMED, never disabled, and stay editable (AC-66); the
  reason sentence is shown undimmed (no opacity on the row). A setting's
  `problem` shows as `.set-problem` under its control and does NOT dim the row.
- `labels` on a choice renders segmented buttons with `aria-pressed`, up to
  `BUTTONS_MAX` (4) values; a longer labelled choice is a select whose options
  read the labels (`settings.js`, pinned in `test_seams.py`). A value outside
  the choices is shown and said, never rendered as nothing pressed.
- A model setting is a connection select plus a model list from THAT
  connection (AC-30); `connectionsChanged` repopulates every selector.
  Favourites (`favouritesFor`) are opt-in per setting (`spec.favourites`); a
  broadcast updates `latest` and repaints every favourites row (D-2).
- A `multiline` setting is a textarea that commits on `change`, shows
  `length / max`, and never uses `maxlength` (the daemon refuses over-long loudly).
- "Settings changed:" notes print `spec.label` / `spec.labels[value]`; a
  multiline value or one holding a line break is NAMED ("<label>: changed"),
  never printed. Keyed on the schema's `kind`, never a setting name.
- Reset all (`#resetAll`, the nav's foot) is two presses with `RESET_ARM_MS`
  (4000 ms), a `RESET_MIN_GAP_MS` (500 ms) dwell, and the same dblclick/key-repeat
  guards as the chat delete. Restart is the account menu's; New chat is the
  sidebar's (the old top bar is gone).
- The permission-mode block gates nothing since AC-29's retirement and its copy
  must keep saying so BEFORE it is opened: its markup's `data-tag` ("Not active
  yet") is drawn as the nav row's `.ntag` (any carve-out may carry one; on a
  phone it stands in for the row's current fact), and the admission is the
  page's lede (the first `.mode-note`). A raise asks first (`#modeConfirm`). Labelled stubs
  (repeat-back AC-9, wake simulator) say they are stubs; a stub that comes true
  is deleted in the same commit (D-12).

### Socket, microphone, playback

- The socket and playback context open at BOOT, not on arming (AC-175..AC-178).
  `micLive` (capturing) and `linkWanted` (wants a socket) are separate.
  `stop()` never closes the socket, playback context or `typedPending`.
  `openPlayback()` and `connect()` are idempotent.
- A boot-created playback context starts suspended; `playChunk` and the cue and
  `speak_start` paths resume it. Never await or fail on it at boot (D-1).
- `caps` is `pageCaps()` (`wake` = mic armed, `tts` = playback exists); re-sent
  from `arm()`, `stop()` and when playback resolves, because the announcer reads
  it (AC-470). An `announce` frame is adopted only when no turn is current and
  the state is WAKE.
- Listening is the `stt_enabled` setting; `applySttEnabled` is the one place the
  mic starts or stops. The page arms itself at boot; `micRefused` prevents a
  permission-prompt loop and only a listening-control press clears it.
- A mic refusal is said ONCE, on the error line, painted from `micRefused`
  ("Microphone blocked.", `paintErrLine`) for as long as it is true; the
  voice pill retries. `reportMicFailure` logs it to `#errors` only, never as
  a counted problem (`micRefusalMessage` keys the log line on `e.name`).
- The mic graph is memoised on its PROMISE, never a null check; `start()`'s
  `starting` flag is cleared in a `finally` (D-17); `buildMicGraph` publishes
  handles only at the end and tears down on failure.
- Playback is ONE AudioWorklet fed by a sample queue, self-healing on underrun.
  Never per-chunk `source.start(t)` (D-3). Copy a PCM buffer per consumer (D-9).
- `speak_end` means "no more audio is coming". Wait for the worklet drain, send
  `playback_done`, then re-arm.
- A cue never enters captured audio: mic frames are ZEROED (never dropped) for
  the cue plus a tail. Browser echo cancellation is not a substitute.
- The wake words, their model URLs, the threshold and the cooldown are the
  daemon's (`docs/wake-words.md` AC-805): `ready.wake` and the `wake` broadcast
  fill `wakeCfg` through `applyWakeConfig`, its one writer; the page has no
  default for any of them and names no wake setting (AC-41) or phrase (AC-809,
  `test_wake_words_page.py`). `arm()` WAITS for the first config
  (`wakeCfgKnown`), never guesses. A threshold change is `setThreshold` on the
  live detector; a different word list rebuilds it in `loadWake`, which
  releases the old sessions FIRST (iOS memory). `window.butlerWake` is the copy
  `diag.js` reads. `paintWakeWords` hands the threshold to
  `viz.setThreshold` with the label, so line and text agree (D-28).
- `wake.js` loads mel and embedding, then one session per word, all
  sequentially; every chunk scores every word on the same embeddings, only the
  highest at or over the threshold fires and is named, and ONE refractory
  follows any fire (AC-806, `test_wake_detector.py`). `wake_candidate`
  carries the fired `word` (AC-807).
- Post-reply wake cooldown is the daemon's `cooldown_ms` (default 1300 ms,
  derived from 16 x 80 ms of detector context, D-14).
  The pre-roll ring runs continuously and flushes on `capture_start` (AC-1a);
  the header's `lookback` is the ring snapshot frozen at the fire (AC-800).
- The AMBIENT ring is a SECOND ring (AC-68, AC-72): fed cue-suppressed frames,
  POSTed over HTTP to `/api/ambient?turn=<id>` at `capture_start`, unawaited,
  never over the socket. The window is the daemon's `ambient_seconds`.
- `#ambientChip`, the header's "Recording room" tag (`role="status"`, the
  seconds in its `aria-label`, a bar under the header on a phone), is up
  whenever the ring is up, not only during a turn (AC-72, AC-687);
  `paintAmbient` also paints the voice popover's switches and `#roomWarn`.

### Turns, stop, watchdog

- The page does not decide when a turn ends. No energy gate, no EOU constants.
  The only end signals it sends are `capture_end_hint` and `stop` (AC-111).
- The page never mints turn ids (D-6). A typed turn sends no `capture_start` and
  adopts its id from the `final`, in `onMessage` ahead of `stale()`, guarded by
  the `typedPending` count (AC-163). Frames for a turn this page does not own are discarded.
- Every terminal path re-arms through `goListen()` (D-1, AC-2); `goListen()` also
  re-reads `loadChats()` and `loadShortcuts()`, unawaited.
- `stopTurn()` is the one stop (D-18): drop local playback FIRST, send
  `{type:"stop", turn, chat}` (or `{type:"stop", chat}` for a watched turn), then
  `goListen()`. Callers: Escape, the voice pill's body (`#stateChip`, via
  `micAction()`), the working row's `#workStop`, and the send button while
  busy AND the box is empty. A draft outranks the stop: typing during a reply
  sends one `typed` frame (a barge-in), never a stop then a send.
- The send button says STOP by SHAPE and WORD (the arrow becomes a filled
  square beside `#stopWord`, "Stop"). Toggle SVG and word visibility with
  `setAttribute("hidden")`, never `.hidden`.
- Escape order (AC-112), one press = one case: Tasks page, settings, drawer,
  files sheet, library sheet, STOP (while capturing, thinking or speaking),
  slash picker, clear the draft. The library's dialogs (`dialog.lbdlg`: recipe
  editor, run with values, automation editor) take NO case: `modal()` in
  app.js closes each in a capture-phase listener on the dialog, and a press on
  its backdrop, and takes the key WHOLE: it never also stops Butler, even
  while busy (AC-111 is the irreversible control; a modal is not a popover).
  The slash picker is a popover with the export's shape (title "Recipes",
  "/" + name with the typed part underlined, the served placeholders as
  chips, the scope, a key footer); Enter and Tab both fill it in.
  Popovers
  (voice, account menu, model chips, project "...", attach menu, search, chord
  capture) take NO case, and neither do the question card's number keys
  (`askKey`, a keydown listener on `#dock`, never the document); each closes on outside `pointerdown` and swallows
  Escape in a capture-phase listener on its WRAP only while focus is inside and
  only when the key would not otherwise stop (`popover()` in app.js for the
  shell's two). The sheets (`dialog.sheet`: Diagnostics, Keyboard shortcuts,
  Restart, and the stop-the-agent confirm `#stopConfirm`) are modal like
  settings: their own capture-phase listener closes them and stops nothing.
  The two confirms are `.sheet.confirm`, the library dialogs' overlay shape
  (AC-706): display-face question, quiet Cancel, filled act; a confirm closed
  any way but its act resolves "no" (the dialog's `close` event). A popover never makes the stop conditional.
  The Tasks and automations page keeps its first place; its own keys (J / K
  move, X select, R run now) are ONE keydown listener on `#tasksPage`, never
  the document, ignored while typing or with a modifier, and none is Escape
  (`test_tasks_page.py` counts the global cases).
- Watchdog: `WATCHDOG_MS` = capture 30 s, thinking 180 s, speak 120 s. The
  capture budget tracks the daemon's reported ceiling plus
  `CAPTURE_CEILING_MARGIN_MS` (AC-92, D-32) -- in endword mode it is the user's
  only way back to wake-listening, so never raise it blind. The watchdog follows
  the TURN, not the microphone, and is suspended while a question card is open
  or a turn is watched.
- The WORKING ROW (`#workRow`, AC-694, CU-12) is `#history`'s last child,
  shown while an agent clock runs or a question is open: "<agent> is working
  in <project>, m:ss, <what>" from the `progress` heartbeat (AC-392, AC-393,
  `workingWhat`); `setTurnClock` is the clock's one writer; ", quiet for m:ss"
  after `QUIET_MS`; past `STALL_MS` it SAYS "it may be stuck" (pause glyph,
  `data-stalled`) and does nothing else. No ticker in
  `reconnecting`/`nolink`. A late beat never creates a turn. The agent is the
  daemon's (`screenAgent`: the listing row's `harness`, else the record's),
  never a guess.
- The CHAT'S AGENT chip (`#chatAgent`, beside the project tag in the header) is
  painted by `paintChatAgent` from the payload's `agent_label` (CT-8); no name
  table or setting is in the page, and it is `textContent` only.
- The FINISHED ROW (`.finrow`, AC-695) goes on the turn's own item, above the
  reply: "<agent> finished in <project>, m:ss." from the beat that carries
  `agent`/`agent_elapsed_ms` (which ends the working row) or, on reload, the
  record's `agent`/`agentElapsedMs`; never this page's clock. "You chose:" is
  the option this page sent (`chooseAnswer`), so a reload omits it.
- "speaking" is drawn only while audio plays (`ttsPlaying`). A stop is drawn as
  `.stopnote` after `.answered` (AC-394).

### Per-conversation state and the daemon's view

- Turn state (`state`, `currentTurn`, clock, `openItem`) describes the
  conversation ON SCREEN. `onMessage` routes every frame carrying `chat` via
  `trackTurnFrame`/`chatTurns` first; off-screen frames touch nothing drawn.
- `setLiveChat` is the ONLY writer of `liveChatId` (grep-gated by
  `test_view_ownership.py`) and sends `{type:"showing", chat}`; `null`, never
  `""`, when nothing is on screen. Re-declared on every `ready` via `loadSession`.
- Switching chats PARKS the screen's turn, never `goListen()`. `renderMessages`
  ends in `settleScreenTurn`: adopt from `chatTurns`, else ask
  `GET /api/chats/{id}/status` and WATCH a turn this socket cannot drive.
- Answer `{type:"opened", chat}` with `loadSession`.
- `question` is broadcast, NOT behind `stale()`; `#askCard` is re-read on every
  `ready`, DOCKED on the composer's top edge (AC-693), its options numbered
  buttons (`askChoices` feeds the buttons and the 1-9 keys alike), the
  composer answers the oldest, and the card goes only on `question_closed` or
  a 410. Agent text is TEXT, never markdown (AC-249).
- `#stateDesc` (inside `#workRow`) and `#orbLabel` (the voice pill's
  `aria-describedby`, `sronly`) have one writer, `paint()` (AC-105 reads
  both). `paint()` also writes the box's state word (`#inState`).
- A dead socket is a SKIN (`linkSkin`: `reconnecting`/`nolink`, AC-91, AC-93):
  raised in `ws.onclose` only while running, cleared on `ready` (not `onopen`),
  latches nothing; repeated `websocket error` banners are suppressed.

### Transcript and composer

- The conversation is the page (AC-161): transcript grows DOWNWARD, each
  `.histitem` placed above `#workRow` (`$("workRow").before(item)`), trimmed
  oldest first; read turns through `histItems()` and empty the transcript with
  `clearHistory()`, never `#history`'s raw children. Sticky scroll via
  `nearBottom`. `logReply` fills slots and never rebuilds `.meta`, which is
  clipped for screen readers only (no visible meta row, lantern-ui section 4).
  The owner's message is the export's bubble (`.said` on `--raised`).
- `loadSession` REPLACES the history after its fetch answers; it never appends.
  It renders Butler's record (AC-35): never claim the agent "resumed" or show
  tool transcripts. Chat titles and listings are the daemon's (AC-164).
- Replies stream sentence by sentence into `openItem.raw`; the WHOLE answer is
  re-parsed per piece. A piece that opens a block is joined with a newline.
- Markdown is a CLOSED, hand-built element set via `createElement`/`textContent`
  (AC-94): `strong em code pre ul ol li h1-h3 p br blockquote hr table`-family and
  `a` (http/https only, checked with `new URL()`, `target=_blank
  rel="noopener noreferrer"`). Never `innerHTML`, never a markdown library; the
  markdown renderer still NEVER creates `img` (`![x](url)` stays text).
  Recursion bounded by `MD_MAX_DEPTH`. No regex lookbehind (older iOS).
- What a turn SHOWED (`docs/rich-chat.md`, AC-632..AC-639): `img`, `audio` and
  `video` in the transcript exist ONLY in `web/artifacts.js`, with a `src`/`href`
  the CALLER built -- `/api/artifacts/<chat>/<id>` from the record's ids, or the
  explorer's `/api/project/<id>/raw` -- never a URL from model text; every
  `src`/`href` assignment there reads `opts.url` or `opts.downloadUrl` (the
  pin greps them). Drawn in a
  `.arts` box placed with `.after()` on `.answered` (Butler) or `.said` (the
  owner's attachments), never inside `.answered`. Each artifact is a CARD on
  `--side` with a head line (`.ah`: name, type and size; an image adds its
  pixel size read off the element, Full size and Download icon links); the
  explorer passes `head: false`. Audio is the STYLED player (CU-16 supersedes
  rich-chat RC-7): a play button, a seek `range`, the time, an `audio` with
  no `controls`, the duration from its own `loadedmetadata`, no waveform;
  video keeps native controls. The `artifact` frame is
  broadcast with no turn: routed by chat before `trackTurnFrame`, ignored off
  screen, held until its item exists. Markdown kinds go through `mdRender`
  (passed in); SVG/HTML are code text; charts are `createElementNS` SVG in
  `--fg` tones. Pinned by `test_rich_chat_page.py`.
- A file dropped on the conversation column goes through `sendAttachment`, one
  call per file (RC-13); a file dropped elsewhere on the page is swallowed so
  the browser never navigates away.
- `reply.spoken === false` and `{type:"unspoken"}` mark shown-not-heard text by
  OFFSET (`unspokenFrom`), dimmed (AC-40); `paintAnswer` is the one renderer.
- `.answered` has no `pre-wrap` (`.said` keeps it); table cells use
  `overflow-wrap: normal`.
- Per-message actions (`.copybtn` family: copy, say, rewind, retry) are ICONS
  ONLY (CU-8 reverses AC-169's word), named by `aria-label` and `data-help`,
  36 px, rest at 0.66 opacity (MEASURED rendered APCA Lc >= 38 as non-text,
  bar 30), and come up on hover AND `:focus-within` -- never hover-only. They
  live in `.acts` under the message and are hidden under an empty pending
  answer, whose placeholder is "Thinking" with a spinner (hidden while the
  working row is up).
- Replay (`.saybtn`, AC-183) is re-synthesis via `POST /api/tts/say`, Butler's
  messages only, one at a time with a cancel token, and suppresses the mic for
  its duration plus a tail (D-15).
- The composer (AC-162): Enter sends `{type:"typed", text}`, Shift+Enter
  newlines; sending on a dead socket says so. A message still needs text.
- Composer (lantern-ui section 4): one box on `--raised` at `--rd` with ONE
  hairline; inside it Caption's seam (`#waveSeam`), the state word
  (`#inState`, while hearing, thinking or speaking; Lantern only), the live
  words (Lantern) and the text, then the control row (`.tools`):
  `+` (menu: Upload a file, Take a photo), the chat's agent-and-model chip and
  the quick chips, mic
  (DICTATES via `POST /api/dictate`, inserts at the caret, never sends;
  `pumpWake` skipped while recording; disabled during a turn), send.
  Placeholder "Message Butler", or "Or type your answer for <agent>" with a
  question open. Arming is the voice pill alone. Controls are 44 px (AC-103).
- LIVE WORDS (AC-689, AC-681): ONE `#liveLine`, `renderLive` its one writer
  (committed `--fg`, tentative `--muted`: `--faint` measured Lc 45.5
  rendered). `placeByStyle()` is the single place that decides its parent,
  from the style's `--live-slot`: Lantern `#liveSlot` in the box (with an
  empty draft it shares the textarea's grid cell and hides the placeholder);
  Caption `#captionSlot` above the box ("Hearing you" over the words at 25 px,
  21 px on a phone, centred; drawn only by Caption while capturing; the
  placeholder stays). Shown only while capturing. Style it by id.
- ONE DOM, TWO STYLES (AC-679, CU-1): both styles carry every element; a style
  only shows or hides (`[data-style]` rules), never adds, and nothing it hides
  may be focusable, so ids and Tab order are identical. `app.js` names no
  style and never reads `data-style` (it writes it once, `applyPageValues`).
  Pinned by `test_caption_style_page.py`; e2e section N compares the id set and
  Tab order across a live switch.
- Attachments go over `POST /api/attach`, never with a message (AC-205, AC-206).
  The camera stops in a `finally`; `capture` is absent from the file input.
  Chips clear on the `final` that took the turn.
- Rewind (AC-194..AC-196): owner's messages only, hidden by CSS while a turn is
  in flight or without `data-msg`; ids paired from the END of the record; the
  dropped prompt goes back into the composer, never sent.
- The empty state (AC-170, AC-692, CU-14) is `#histEmpty` (the time-of-day
  greeting in `--display`), the composer CENTRED by `.col.empty`, and
  `#emptyChips` (three suggestions and "Say <the words that are on>, or
  type.", AC-105, written by `paintWakeWords`)
  under it; `setEmptyState` is the only writer of both `hidden` flags, the
  class and the greeting. The column ships `empty` from the first byte.
  Suggestions send a real typed turn.
- Errors: `#errStrip` is ONE quiet line directly above the composer
  (`role="alert"`, AC-696, CU-13), painted only by `paintErrLine`, in order: a
  lost link ("Not connected. Anything you type stays in the box until Butler
  is back." -- the draft is never cleared on a dead socket), a refused
  microphone, then the logged problems with a count. Dismiss hides it until
  the next fact (`raiseErrLine`). `#errors` lives in the Diagnostics sheet,
  which "Details" opens. It never writes the stored sidebar preference.
- Every `hud(id)` call site needs a slot, inside `#diagSheet`
  (`test_every_hud_readout_has_a_slot_to_land_in`, AC-707 in
  `test_lantern_shell_page.py`); `hud()` returns quietly on a missing element
  by design (D-22). The permission mode's word (`#modeLabel`) is there too.

### Sidebar, projects, conversations

- The sidebar is a PROJECT TREE (`docs/filing-and-pacing.md` 8, AC-291), one
  column, no strip. A project line press only opens/shuts it and writes only
  `expanded_projects` -- it opens, files and starts nothing. Grouping, never
  filtering: every served row appears under exactly one heading; removed projects
  are drawn struck through. A query replaces the tree with a flat list.
- There is NO route that moves a chat (R-A); a birth names its project in one
  call, `POST /api/session/new {project}` (AC-287). No "work here" control.
- `gatedNewChat` reads the target at click time and asks `GET /api/shortcuts`
  THEN for `busy`; a confirm precedes `POST /api/project/{id}/stop`; declining
  does nothing.
- The header holds four things (AC-683): `#burger` (the sidebar-panel icon),
  the title over its project, the split voice pill (`#stateChip` body,
  `#voiceChev` chevron opening `#voicePop`), `#panelBtn`; plus `#ambientChip`
  while the room is recorded. Nothing is reparented into an overflow.
- Sidebar rows (lantern-ui section 4): `#newChat`, `#searchRow` (swaps itself
  for `#chatSearch` until the field blurs empty), `#libraryBtn` (opens the
  panel on Library); the tree; the Account row (`#acctBtn` opens `#acctPop`:
  Settings, Appearance, Tasks and automations, Keyboard shortcuts,
  Diagnostics, Restart Butler) and the gear `#settingsBtn` (one press).
  `#footLink` on the Account row is the link state, written only by `setLink`,
  drawn only while the link is down.
- Collapsing is 0 px (AC-293). `<header>` is a row of `.app` so `#burger`
  never moves (AC-295, AC-685); the header's part over the sidebar wears
  `--side` through `--lead`, never `--sb-w` (the drag writes that inline).
  With the list shut, New chat and Settings are one press behind `#burger`
  (CU-9 reverses AC-294). `toggleList` is the one toggle for the press and
  Ctrl+B. Collapsing never strands focus (AC-299). The busy dot rolls up onto
  `#burger` and is also in its `aria-label` (AC-296).
- Phone (900 px and below): the drawer is the whole sidebar at `left: 0`; the
  crossing is per device and never written back (D-2). `backdrop-filter` makes
  `#sidebar` the containing block for fixed children, and the scrim lives inside `.app`.
- Stacking: `#field` 0, `.app` 1, header 2, drawer scrim 11 / drawer 12, files
  sheet scrim 13 / sheet 14, settings scrim 19 / surface 20, shell popovers
  60; the sheets are native modal dialogs (top layer). The Tasks and
  automations page is inside `.app` at z 1 in `.col`'s grid cell (under the
  header, so its popovers drop over it) on a desk, and fixed at 21 on a phone.
- Sidebar width is the daemon's `sidebar_width` (`--sb-w`): `#sbResize` is a
  focusable `role="separator"` inside the box, commits the PAINTED width with
  ONE POST on release, and takes its clamp from the daemon's answer.
- `#projPop` is `position: fixed`, a sibling of the sidebar, placed under the
  "..." that opened it and clamped to the viewport; the export's project menu
  (Open <agent> here, Edit project, Set keys, Remove project). Agent buttons
  launch (`POST /api/terminal`), chord slots bind; never
  the same click (AC-146, AC-149). Launch is disabled for
  `LAUNCH_COOLDOWN_MS`, never latches success, and refusals show in the daemon's
  words (AC-147, AC-148). A row save sends `chords: row.chords`. Chord capture
  listens in the capture phase and calls `stopPropagation` and `preventDefault`.
  The page holds no key table and no agent names. No "remove" on the root row.
- Chat delete is two presses (the glyph and "Delete?" in `--warn`; the row's
  time steps aside), `DROP_MIN_GAP_MS` dwell,
  ignores `detail > 1` and repeat keydowns, never disarms on Escape, repaints from
  the route's answer, and refocuses a neighbour row or "+ New chat" after
  `loadShortcuts()` resolves.
- `openChat` POSTs `/api/session/open` (AC-240), re-fetches the listing, and
  treats a 404 as "already gone".
- Chat search (AC-188) is the daemon's `?q=`: no local filter, debounced at
  `CHAT_SEARCH_MS`, sequence-guarded; "nothing matched" painted from `paintChatList`.
- A tree row is ONE line: the title in `--fg` and at the right one quiet fact
  (`chatWhen`: time today, "Yesterday", weekday, date), or for a loud row its
  state -- a spinner and the elapsed clock, a "Needs you" chip (the export's
  words for `waiting`; the daemon's word stays in the `aria-label`, AC-269),
  an "Error" chip -- plus its last tool action under it. Search results keep
  the project label and age (`.hit`). Project marks are ids, never paths
  (AC-232); a project's hue is never a state colour.
- An automation run's row has a clock glyph at the right and, unread, a dot
  and a bold title, all named in its `aria-label` (AC-469).

### Phone-origin turns (`docs/messaging.md` M6)

- `paintGuestLock(doc)` makes the composer inert, disabled and not drawn in a guest chat
  (`origin.kind`); `#guestLock` (a lock and one `textContent` line, the export's locked box)
  stands in its place (AC-607).
- A `final` with reason `whatsapp` is adopted only for the chat on screen, never over a capture,
  with no cue; `markVia` writes the "via WhatsApp" mark (a phone glyph and `textContent`) into
  the `.acts` row under the owner's bubble, never at the buttons' resting opacity (CU-17).

### Pairing groups (`docs/messaging.md` MS-5)

- A settings group that declares `pairing` renders through `buildPairing(url)`: the words, buttons
  and polling all come from the daemon (the page counts nothing and names no service); text via
  `textContent` only; the QR is accepted only as a PNG data URL, on a forced white plate (an
  inverted QR does not scan). Pinned by `test_pairing_page.py`.

### Terminals (`docs/chats-and-terminals.md` CT-4, CT-5; AC-765..AC-767)

- `web/terminal.js` (`mountTerminals`): a project's open terminals and an "Open
  terminal" action sit under its chats in the tree (`terminals.rowsFor`). A
  terminal is always the plain shell and opens on one tap: `POST /api/terminals`
  sends `{project, cols, rows}`, no agent, no picker (SW-8, AC-797). A
  terminal opens as `#termView` over the conversation body (`.col.termopen`), the
  composer stays. A 403/404 on the list hides the feature (404 means
  Settings switched terminals off, SW-7). All daemon text via
  `textContent`; no markup sink.
- xterm.js and the fit addon are VENDORED in `web/vendor/xterm/` by
  `scripts/fetch-xterm.py` (npm tarball, integrity-checked, gitignored) and
  loaded lazily from there only; never a CDN. A monospace face is allowed in
  `#termHost` (a program's own output).
- Socket `/ws/terminal/{id}`: `replay` resets and writes, `output` writes, `exit`
  stops reconnecting; a dropped socket reconnects and replays. Keydown inside
  `#termView` stops propagating, so Escape never reaches the global stop (AC-111).
- The phone key row (`#termKeys`: Esc, Tab, sticky Ctrl, arrows, Enter) shows at
  900 px and below.
- DICTATION TARGET (AC-765): `endDictation` calls `terminals.dictate(text)`
  before `insertIntoComposer`; it sends `{type:"input"}` with NO newline when the
  terminal view is open and was focused after the composer, else returns false.
  Nothing a terminal prints reaches speech (AC-766).
- Pinned by `test_terminal_page.py`.

### Chat agent and model chip (`docs/chat-model.md` CM-5..CM-9, AC-785, AC-786; `docs/chat-agent-picker.md`, AC-774..AC-779)

- Everything is the chat payload's (`paintChatAgent`): `agents` (what to offer,
  in order), `agent_default`, `agent_fixed`, `agent_label`, `model` (the chat's
  own, "" = default), `model_default` (the resolved default's name, "" when
  unnameable). The page names no agent, no model and no setting; all text via
  `textContent`. A listing row (label only) never resets the chip;
  `paintChatAgent(null)` (endpoint mode, session routes 404) clears it. The old
  A catalogue of `SHEET_FLAT_MAX` (8) models or fewer is listed whole, never folded.
- ONE composer chip, `#chatAgentChip` (left of the quick chips), is the agent
  and the model together: `<agent_label> . <model | Default (<model_default>) |
  Default>`, painted by `paintAgentPicker`. The dot is a CSS escape (`\00B7`,
  the source stays ASCII); at 600 px and below the parenthesis (`#chatModelPar`)
  and the glyph go, and `#modelBtn` (the quick chips' value half) is hidden so
  the row fits. The chip shows for the whole life of a chat: the model changes
  at any time, from the next reply.
- It opens `#modelSheet` (a `.pop` sibling of `#projPop` outside `#sidebar`;
  a fixed popover above the chip on a desk, a bottom sheet at 600 px and
  below, `placeModelSheet`). Agent: a radio of `agents` while `agent_fixed` is
  false (POST `/api/chats/{id}/agent`, which clears the model; a 409 makes it
  read-only), else one line "<label> -- this chat stays on <label>". Model:
  the search box (`#msSearch`, pinned above the scrolling `#msList`), then
  Default (POST `{"model": ""}`), the chat's own pick (checked; "(not listed)"
  and kept when the catalogue lacks it, CM-5), Favourites, Recent, and "All
  models" folded. A query replaces the sections with Matches (substring, case
  insensitive, capped at `SHEET_MATCH_CAP`). A pick closes the sheet, POSTs
  `/api/chats/{id}/model`, repaints from the answer and refetches the agent's
  list (Recent).
- Lists come from `GET /api/agents/{agent}/models`, fetched when the sheet
  opens and kept per agent for the page's life (`modelCatalog`); an error is
  shown in the daemon's words and retried on the next open. Favourites are the
  daemon's shared list as served; the star stays in the settings sheet.
- Doors for the agent at BIRTH are unchanged: an `.agentchev` chevron beside the
  sidebar's New chat and each project's "+ New chat" opens `#agentMenu`
  (the same birth through `newChatFrom(..., agent)`; the main buttons stay
  agent-less). A taken turn (`final` with text) calls `agentSpent()`; the next doc
  re-read confirms it. The header label `#chatAgent` stays.
- A popover: closes on outside `pointerdown` and when focus leaves; Escape
  through a capture-phase listener that yields to the stop (`escapeWouldStop`);
  arrow keys (Home and End except in the search box); focus returns to the chip.
  Opening any other shell popover shuts it (`closeShellPops`). No case in the
  global Escape handler. Pinned by `test_chat_model_page.py` and
  `test_chat_agent_page.py`.

### Right panel: files and library

- ONE right panel with two tabs (AC-699): `#filesPanel` and `#libraryPanel`
  each carry the Files | Library tabs (`.ptab`); `rightPanel` ("" | "files" |
  "library", `right_panel` in the store) is written only by `setRight`.
  `#panelBtn` opens the tab last shown and shuts whichever is up; both modules
  call `env.painted()` from `paint`, and `paintPanelBtn` is the one painter of
  the toggle, the Library row and the tabs. On a phone each panel is a bottom
  sheet (a gesture, never written), and opening one shuts the other.
- `web/files.js` (AC-303..AC-315): two GETs through one helper, plus ONE write
  -- a dropped file POSTed raw to `/api/project/{id}/upload` in `upload`, the
  daemon's refusal (409/413/400/403) shown in its words (RC-13). The preview
  sits UNDER the tree, which stays drawn (CU-16, supersedes file-explorer
  3.3's swap): a card on `--bg` whose head is the path, the size, Copy path
  (the toast says the outcome) and Close; the previewed row is
  `aria-selected`. Image/audio/video/csv/markdown/code/file kinds draw through
  `artifacts.js` with `rawUrl` (`/api/project/{id}/raw`, an element source,
  never fetched), the text already fetched and `head: false`; plain text
  keeps `#filesText`. `#filesDrop` (hidden at rest) is drawn while a drag
  carrying files is over the body, sticky at its foot, naming where the file
  lands ("Drop a file here to upload it to <project>[/<folder>]"). Project from
  `currentProjectId()`, all names and contents via `createElement` +
  `textContent` (no markup sink), ARIA tree with one roving tab stop, hidden
  items said, refresh coalesced on turn end. Pinned by
  `test_file_explorer_page.py` and `test_rich_chat_page.py`.
- `web/library.js` (the Library tab, `docs/lantern-ui.md` 5.2, Step 6a):
  Recipes / Automations / Tasks sub-tabs, this project then Global, cards on
  `--bg`; an empty tab invites a first step (`lempty`). Every filled text comes
  from `POST /api/recipes/{rid}/expand`, never substituted locally (AC-448);
  Run and Start go through `runInNewChat`; one fetch helper, no markup sink.
  A recipe row is "/" plus its NAME (no slug) and says what uses it from the
  served `usedBy`. Placeholders are the SERVED `placeholders`, found in the
  text by their token (`tokens`, `indexOf`): the page holds no brace grammar,
  so a placeholder typed in the editor is marked once saved. A task says
  "Added by <agent>" from the served `agent`, else Butler or You, plus `why`.
  Pinned by `test_library_page.py` and `test_library_lantern_page.py`.
- The page words NO date, status or schedule (lantern-ui section 8): an
  automation card prints `nextWords`, a run its `statusWord` with a shape per
  `group` (`mark` in automations.js: dot ok, diamond needs, square failed,
  ring skipped, turning ring pending) and its `whenWords` / `tookWords` /
  `lastOccurrenceWords`, a task its `addedWords` / `doneWords`, the heartbeat
  `awakeWords`; the reminder editor's inputs start from the served
  `runAtInput` / `remindInput`. Static gate: no `toLocale*`, `Intl`,
  `new Date`, `Date.*` or `.getHours()`-style reads anywhere in library.js or
  automations.js, NO exceptions (`test_library_lantern_page.py`).
- `web/automations.js`: every schedule answer comes from
  `POST /api/schedule/preview` (its `nextWords` and cron) -- the page parses no
  cron. The editor's Starts, Precheck and "Nothing new" are markup in its More
  options fold. Same untrusted-text rules. Pinned by `test_automations_page.py`.
- The Tasks and automations page (`#tasksPage`, lantern-ui Step 6b, CU-16;
  `w1-calm-library.html` view=tasks) lives in the CONVERSATION COLUMN: a
  child of `.app` in `.col`'s grid cell, which `.app:has(...)` hides while it
  is open, so the sidebar, header and right panel stay live. `openChat` and
  `newConversation` close it (`closePage(false)`). Automations: search,
  Project, On or off, and Last result by the served `group`; rows grouped by
  project with select, switch, name (whole row opens Past runs), `nextWords`,
  the mark and Run; a floating bulk bar (Turn on, Turn off, Run now, Clear);
  the run-history drawer at the right, the library's one overlay shape
  (`--pop`, `--rd`, shadow, `--ease`/`--dur`), over the list when the page is
  narrow and a bottom sheet over `#tpScrim` on a phone. Tasks: search,
  Project, Open / Done / All, Added by Anyone / You (`source`) / An agent
  (the served `agent`); one new-task box filed under the Project filter's
  project, else the one on screen. A list rebuild keeps focus on the same
  row's same control (`redraw`). Pinned by `test_tasks_page.py`.
- The library's three dialogs are ONE overlay shape, `dialog.lbdlg` (`--pop`,
  `--rd`, the shared shadow, `--ease`/`--dur`; a bottom sheet at 900 px and
  below); a disabled button is a muted word on a tone fill, never faded.
- The heartbeat is the pinned first card of the Automations tab, its editor
  inline in the card, whose one write is `POST /api/heartbeat`; the presets
  and the item count are served by the daemon, and an open editor is never
  rebuilt by a `library` frame (`docs/proactive.md` 4.1). A card's Past runs
  opens the full page on that automation (`openPage(tab, select)`).

### Visual language (D8, owner rulings)

- "Keep it visually easy": no borders; separation is space plus a tone step
  (`--hover`, `--active`, `--hairline`, mixed against `--fg`, never from `--sa`).
  The composer keeps one hairline. The only fill in the tree is the
  conversation on screen.
- No monospace in the interface; `--mono` only under `.answered code/pre` and
  `.art code/pre` (shown content).
- The style names the faces (CU-7): the interface in `--ui`, Butler's replies
  (`.answered`) in `--read` at `--readsize`, the empty-state heading in
  `--display`. The owner's messages are 16.5 px; all prose at 1.7 (times
  `--scale`). Section labels sentence case, no uppercase.
- Hover help is ONE mechanism: `data-help`, one delegated listener, one `#tip`
  (`aria-hidden`), keyboard focus only via `:focus-visible`, hidden on keystroke
  and while the control's own popover is open. The browser `title` is banned,
  and so is a CSS-generated word (`content: attr(...)`). `#tip` wears the
  export's look: `--fg` ground, `--bg` words, no shadow (CU-16).
- ONE TOAST: `toast(text)` in app.js is the only writer of `#toast`
  (`role="status"`, always in the tree, empty and transparent at rest; the
  modules reach it as `env.toast`). It carries one-shot announcements
  ("Copied", "Saved", "Restarting Butler", "Chat deleted", "New chat
  started"); `logNote` keeps the statuses that last as long as what they
  describe (dictating, transcribing). No per-button word bubbles.
- Help rule: a control with a visible word gets NO tooltip; an icon-only control
  gets one to three words plus its shortcut (`data-key`) only if a binding exists.
  Kept exceptions: truncated model pick values, the `Applies on restart` tag, a
  `pickseg` inactive reason.
- Microcopy says each thing once, at full contrast; never fade text below
  legibility. Dim TEXT is `--muted`; the style's `--faint` is for icons only
  (it measures APCA Lc 45-50 as text, AC-710; the tentative live words moved
  off it in Step 3c).
  `STATE_COPY` names the state; `micAction()` owns the instruction.
- Hue carries session state and nothing else: blue listening and hearing,
  amber thinking and speaking, red error, lost link and recording room.
  AC-680 (replaces AC-174, CU-4): ONLY a `[data-style]` block declares the
  state colours `--blue`, `--amber`, `--red`; every state token (`--sa`,
  `--sa-dim`, `--g1`, `--g2`, `--warn`, `--run`, `--in`, `--out`) is derived
  from them by reference, never a literal (gated in `test_seams.py`). The
  style blocks are the page's only palette; tokens the export does not name
  (`--bg-deep`, `--line`, `--line-soft`, `--shadow`, `--hover`, `--active`,
  `--hairline`) are derived in `[data-style]` from the style's own palette.
  `--tone` is the style's; nothing else declares it.
- State is never conveyed by colour alone (AC-168): words in every slot, shape
  changes (stop square, mute cross), and `aria-label`s.
- Current values in pickers are a FILL with `--fg` text, never accent-coloured
  text. Dim with a colour, never a compounded opacity.
- `color-scheme` follows the RESOLVED theme (`dark` on `:root`, `light` under
  `[data-theme="light"]` and under `[data-theme="auto"]` in
  `prefers-color-scheme: light`), gated in `test_seams.py` and
  `test_lantern_tokens.py`. `auto` restates each light palette inside that
  media query; `test_lantern_tokens.py` holds the copy equal. `scrollbar-color` on
  `:root`, `scrollbar-width: thin` on `*`, never `--sa`, no `::-webkit-scrollbar`
  block; `main` has `scrollbar-gutter: stable`.
- `html, body { overflow-x: clip }`; the header is `flex-wrap: nowrap`. At
  900 px and below it is panel icon, title over project, pill, panel toggle.
- The shell's classes are the export's (`.srow`, `.pop`, `.mi`, `.sw-row`,
  `.tabseg`, `.vgroup`, `.pickchip`, ...), its rules last in the sheet. The
  export's button reset lives in `@layer reset`, scoped to the shell, so every
  unlayered component rule outranks it (lantern-ui 11; pinned).
- `.micbtn` has no border ring, a surface FILL at rest (AC-184).
- Speech off is the voice popover's "Read replies aloud" switch (`#speakBtn`,
  `role="switch"`, AC-110's successor); the pill's body is a button fed by
  `micAction()` (AC-180) and its word is `STATE_COPY`'s kicker (Off,
  Listening, Hearing you, Thinking, Speaking, Reconnecting). The voice
  switches paint from state, never from the press. Composer controls are
  44 px (AC-103). A `.skiplink` to `#input` is the first focusable element:
  the export's (`--fg` ground, `--bg` words, no shadow).
- Focus (lantern-ui Step 7): the page-wide `:focus-visible` ring is `--fg`,
  2 px, never the state hue; the composer's is a 1 px hairline.
  `prefers-contrast: more` raises `--line`, `--hairline`, `--muted`,
  `--faint` and thickens both rings; reduced motion transitions colour and
  opacity only; reduced transparency drops every `backdrop-filter`. A
  setting dimmed as inactive (AC-66) is the one text allowed down to APCA
  Lc 45 (AC-710's exception); its reason sentence is never dimmed.

### Visualiser and voice effects

- `visualiser.js` is a classic script exposing `window.ButlerVisualiser`
  synchronously, with no `import`/`export`, no AudioContext construction, no
  browser storage, no sound. It takes Butler's own nodes
  (`attachInput`/`attachOutput`). Gated by `test/test_visualiser_contract.py`,
  which also requires a method behind every `viz.` call site in `app.js`.
- `#wave` is MOVED between slots, never duplicated; `off` stops the drawing,
  never the calls. `placeByStyle()` is the ONE function that re-parents it
  (and `#liveLine`), called on every style, effect and placement write: under
  `lattice` the `viz_placement` slot (`VIZ_SLOT`: `#waveMic` for `mic` and
  `ambient`, whose `#field` also fills the page); under a treatment the
  stylesheet's `--wave-slot` (`#waveFx`; `lantern`'s is `#waveSeam`, the
  seam, under both styles). Band vs ring is decided by the canvas aspect, not
  by `app.js`; the seam is wide (200 x 22), so it gets the band. Under
  `calm()` the ring's nodes stand still (RULING 1).
- The frame loop runs while the canvas is on screen, or, under a treatment,
  while `#composerFx` is (both observed): Lantern keeps `lantern`'s canvas in
  the seam it never shows, and its glow still needs `--fx-level`. MEASURED: with
  only the canvas counted the level was never written (e2e N8 witnesses it).
- Lattice reach and text falloff are fractions of the band's height, never the
  viewport's. The field thins smoothly near text (fourth-power falloff, no hard mask).
- The level chain (asymmetric follower, subtracted floor, frozen loud
  reference, amplitude not dB) is reused, never rebuilt. Lines trace the
  waveform, particles the spectrum. Smoothing is `1 - exp(-dt / tau)`.
- The canvas palette is READ from the style (`meshFromStyle` in `app.js`:
  `--blue`, `--amber`, `--red`, `--fg`, `--bg` off `<html>`), so it and the
  `[data-state]` rows cannot disagree; `app.js` spells no colour
  (`test_lantern_tokens.py`). `applyMesh` re-reads it after every style or
  theme write and on a `prefers-color-scheme` change. The visualiser reads the
  theme from the palette's luminance, not from `data-theme`.
- Who is talking is carried by hue and glow (cold listening, gold speaking),
  never by geometry. The wave's motion is settled (`mocks/g4-lattice.html`).
- `voice_effect` treatments: `#composerFx` sits BEHIND the opaque composer,
  inset negatively, so the inside stays clean by OCCLUSION and the composer's
  geometry never changes (AC-256). Insets come from `--fx-t/r/b/l` (and
  `--fx-radius`), never repeated literals. The level comes only from the existing
  chain via `opts.fx` (AC-257); colours are `--in`/`--out`, the style's blue
  and amber (CU-4 replaces AC-255), re-read by `visualiser.js` whenever the
  palette changes. Switching treatments leaves no residue (AC-260);
  `off` and `lattice` hide `#waveFx`.
- `lantern` (the default, AC-690, CU-3) is CSS only: under
  `[data-voicefx="lantern"]` the box's own edge is `--lit` (`--blue` while
  capturing, `--amber` while thinking or speaking, set on `.field` by
  `[data-state]`, the hairline at rest), and `.fxlantern` in `#composerFx`
  draws a glow sized from `--fx-level`; reduced motion keeps the colour and a
  STILL glow. It shows no `.fxstill`. Its canvas draws the SHAPE figure
  (`fxShape` in visualiser.js) wherever the style shows it: nowhere under
  Lantern (the seam is hidden, the canvas measures 0 x 0), the band in
  Caption's seam while hearing or speaking (AC-691).
- Caption's `lantern` (AC-691, CU-3): the box's edge is `--lit` as under
  Lantern, but no glow (`.fxlantern` hidden) and no word in the box; the seam
  shows while capturing, closing or speaking. Reduced motion hides the seam's
  canvas and shows STILL bars (`#waveSeam::before`, a mask in `--lit`).
- `setVoiceEffect` names only `off` and `lattice` (`lantern` passes through as
  a treatment); `viz_placement` applies only under `lattice`. `fxHasCanvas`
  and the dispatch chain must agree (D-18).
- Reduced motion (AC-259): every treatment and the lattice freeze under
  `calm()`; the still halo (`.fxstill`) carries the level. Reduced motion means
  gentler feedback, not absent feedback.

## Work Guidance

- Read the surface's spec in `docs/` (familiar-ui, settings-page, lattice-wave,
  voice-effects, filing-and-pacing, sidebar-collapse, file-explorer, automations,
  rich-chat).
- Motion: feedback on pointer-DOWN, one critically damped curve at ~320 ms,
  interruptible transitions. Honour `prefers-reduced-motion`,
  `prefers-reduced-transparency` and `prefers-contrast`.
- Take a screenshot at 1440 and 390 px before shipping any visual change; no
  instrument judges weight, order or overdraw, and the owner rejects on sight.
  A pixel count and a screenshot are different instruments; use both.
- Under reduced motion, wait out the global 140 ms colour transition (or
  `getAnimations().finish()`) before reading computed styles; headless Chrome
  never finishes a transition on its own.
- Known open items: `sonar`, `ember` and `dust`
  overdraw nearby text at level 1.0; light-theme `thinking` and `speaking` are
  1.18:1 apart (`docs/lattice-wave.md` 9); the iPad frame budget is unmeasured
  (AC-261); the export's "sent when Butler is back" is not built (a draft
  typed while the link is down stays in the box and is NOT queued); typed
  replies in `endpoint` brain mode do not render; a reloaded
  driving tab does not re-attach to its own turn's clock; a native click on an
  `aria-haspopup` button can show its own tooltip over its popover.

## Verification

```
node --check web/app.js
node --check web/settings.js
node --check web/visualiser.js
node --check web/files.js
node --check web/artifacts.js
node --check web/library.js
node --check web/automations.js
node --check web/terminal.js
.venv\Scripts\python.exe -X utf8 -m pytest test\test_http_surface.py ^
    test\test_settings_api.py test\test_visualiser_contract.py ^
    test\test_seams.py test\test_file_explorer_page.py ^
    test\test_sidebar_collapse_page.py test\test_library_page.py ^
    test\test_automations_page.py test\test_turn_per_chat_page.py ^
    test\test_view_ownership.py test\test_page_review_2026_09_26.py ^
    test\test_rich_chat_page.py test\test_lantern_tokens.py ^
    test\test_lantern_shell_page.py test\test_lantern_composer_page.py ^
    test\test_caption_style_page.py test\test_settings_sheet_page.py ^
    test\test_pairing_page.py test\test_library_lantern_page.py ^
    test\test_tasks_page.py test\test_lantern_extras_page.py ^
    test\test_lantern_quality_page.py test\test_terminal_page.py -q
python -X utf8 test\manual\probe_page.py https://127.0.0.1:<scratch>/
python -X utf8 test\manual\probe_ux.py   https://127.0.0.1:<scratch>/
python -X utf8 test\manual\e2e_scenarios.py --only=F,I,K,L,M,N
python -X utf8 test\manual\probe_reduced_motion.py https://127.0.0.1:<scratch>/
```

- `grep` is blind to control bytes; the ASCII gate is the pytest above.
- Point probes at a SCRATCH daemon over `https` (self-signed accepted), never
  port 8140 on either machine. `--live`, `--turn`, `--write` and `--typed`
  write settings or send turns.
- Run `probe_page.py`, `probe_ux.py` and `probe_reduced_motion.py` in BOTH
  styles: set `ui_style` on the scratch daemon between runs; each checks the
  style it is served.
- Probe selectors are coupled to this markup: update `probe_page.py`,
  `probe_ux.py` and `e2e_scenarios.py` in the SAME change as the markup they
  measure (AC-172). `probe_page.py` exits 1 on a missing expected element; read
  exit codes, never output text.
- AC-94 and AC-169 need a daemon whose brain holds a conversation
  (`harness`/`harness_only`); otherwise they SKIP.
- No probe sees browser chrome (scrollbars, select popups): verify by looking.
  AC-91/AC-93 and chord hotkeys are driven by hand; kill a scratch daemon by PID.
  Reading markup is not evidence -- a throwing module looks like missing controls.

## Child DOX Index

No child AGENTS.md yet.
