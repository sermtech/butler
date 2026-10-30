// Butler satellite page -- protocol v1 client (plan.md 4.2, 4.3, 4.7).
//
// The page is a MICROPHONE AND A SPEAKER with a local wake detector. It owns
// no policy. Specifically, and unlike the spike this was forked from:
//
//   * It does NOT decide when a turn ends. There is no energy gate, no
//     ON/OFF/HANG_MS, no EOU_* constants, no check_turn, no finalize. The
//     daemon runs all of that (plan.md 4.2), because a native mic source has
//     no page to author it (AC-24). The only end-of-turn signal the page may
//     send is `capture_end_hint`, and only when the user explicitly taps stop.
//   * It does NOT mint turn ids. The daemon mints one and announces it in
//     `capture_start`; anything carrying a different turn is discarded (D-6).
//   * It does NOT fetch speech over HTTP. Speech arrives as binary 24 kHz
//     mono int16 frames on the same socket, between speak_start and speak_end,
//     and is played through ONE AudioWorklet fed by a sample queue (4.7).
//   * It uses NO browser storage of any kind. The permission mode is
//     server-side and arrives in `ready` and in `mode` broadcasts (AC-8, D-2).
//
// The invariant that outranks everything else: EVERY terminal path returns to
// wake-listening (D-1, AC-2). Normal reply, empty final, protocol error,
// speech failure, socket drop, watchdog -- all of them land in goListen(), and
// none of them stop the microphone.

import { pcm16ToFloat32, resampleLinear } from "./pcm.js";
import { mountSettings } from "./settings.js";
import { mountFiles } from "./files.js";
import { renderChatArtifact } from "./artifacts.js";
import { mountLibrary } from "./library.js";
import { mountAutomations } from "./automations.js";
import { mountTerminals } from "./terminal.js";

const $ = (id) => document.getElementById(id);
// THE FILE EXPLORER (docs/file-explorer.md, AC-303..AC-315), mounted FIRST so
// every function below may call it. Mounting wires listeners and calls none
// of these: each is read at CALL time, after this module has finished
// evaluating, so the later `const`s they name are initialised by then.
const files = mountFiles({
  projectId: () => currentProjectId(),
  projectRow: (id) => projectRow(id),
  projectName: (row) => projectName(row),
  // The project a conversation belongs to: the daemon's answer for the one on
  // screen, else its row in the last `GET /api/chats` (AC-315).
  chatProject: (id) => (id === liveChatId ? chatProjectId
    : ((chats.find((c) => c.id === id) || {}).projectId || "")),
  rightPanel: () => rightPanel,
  setRight: (next, viaKey) => setRight(next, viaKey),
  isPhone: () => DRAWER_Q.matches,
  closeDrawer: () => closeDrawer(),
  closePops: () => closeShellPops(),
  closeOtherSheet: () => library.closeSheet(),
  painted: () => paintPanelBtn(),
  icon: (size, width, shapes) => icon(size, width, shapes),
  // The chat's own markdown renderer, for a previewed `.md` file drawn by
  // artifacts.js (docs/rich-chat.md RC-12): one parser, one element set.
  markdown: (text, into) => mdRender(text, into),
  toast: (text) => toast(text),
});
// THE LIBRARY (docs/automations.md phase 2): recipes, automations and tasks
// of the project on screen plus Global. Mounted here, BEFORE the composer's
// own listeners below, so its slash picker sees Enter first (AC-450).
// IN-PAGE TERMINALS (docs/chats-and-terminals.md CT-4, CT-5): listed beside a
// project's chats, one view at a time over the conversation's body. Dictation
// reaches it through `terminals.dictate` (AC-765); its output is never spoken.
const terminals = mountTerminals({
  projectRow: (id) => projectRow(id),
  projectName: (row) => projectName(row),
  composer: () => $("input"),
  closeDrawer: () => closeDrawer(),
  repaint: () => paintChatList(),
  showError: (text) => showError(text),
  toast: (text) => toast(text),
});
const library = mountLibrary({
  projectId: () => currentProjectId(),
  projectRow: (id) => projectRow(id),
  projectName: (row) => projectName(row),
  rightPanel: () => rightPanel,
  setRight: (next, viaKey) => setRight(next, viaKey),
  isPhone: () => DRAWER_Q.matches,
  closeDrawer: () => closeDrawer(),
  closePops: () => closeShellPops(),
  closeOtherSheet: () => files.closeSheet(),
  painted: () => paintPanelBtn(),
  icon: (size, width, shapes) => icon(size, width, shapes),
  composer: () => $("input"),
  insertText: (text) => insertComposerText(text),
  runInNewChat: (projectId, text, btn) => runInNewChat(projectId, text, btn),
  showError: (text) => showError(text),
  // The automation editor and the full page (phases 5-6), read at call time.
  automations: () => automations,
  openChat: (id) => openChat(id),
  // How a library dialog closes on Escape and on its backdrop (AC-706).
  modal: (dlg, close) => modal(dlg, close),
  toast: (text) => toast(text),
});
// THE AUTOMATION EDITOR AND THE FULL TASKS & AUTOMATIONS PAGE
// (docs/automations.md phases 5-6). Every rule is the daemon's; the page
// only draws its answers. A change re-reads the library panel too.
const automations = mountAutomations({
  projects: () => (shortcuts && shortcuts.rows) || [],
  projectRow: (id) => projectRow(id),
  projectName: (row) => projectName(row),
  rootId: () => (shortcuts && shortcuts.root_id) || "",
  isPhone: () => DRAWER_Q.matches,
  icon: (size, width, shapes) => icon(size, width, shapes),
  openChat: (id) => openChat(id),
  runInNewChat: (projectId, text, btn) => runInNewChat(projectId, text, btn),
  showError: (text) => showError(text),
  libraryChanged: () => library.changed({ project: "" }),
  modal: (dlg, close) => modal(dlg, close),
  // Where the page's one new-task box files a task (the project on screen).
  projectId: () => currentProjectId(),
  toast: (text) => toast(text),
});
// WHICH ONE PANEL THE DESK'S RIGHT COLUMN SHOWS: "", "files" or "library"
// (docs/automations.md AU-10, AC-466). ONE value for both panels, so "both
// open" is not a state this page can hold -- two booleans would make it one.
// It is `right_panel` in the daemon's shortcuts store (AC-8: no browser
// storage), written ONLY here and read back in `paintShortcuts`; a phone's
// sheets are gestures and never reach this (D-2).
let rightPanel = "";
// The tab the header's panel toggle opens when the panel is shut: the one last
// shown, this session only (a gesture, never written -- `right_panel` is the
// stored fact and "" means shut).
let lastPanel = "files";
function setRight(next, viaKey) {
  if (next === rightPanel) return;
  rightPanel = next;
  files.rightChanged(viaKey);
  library.rightChanged(viaKey);
  saveShortcut({ right_panel: next });
}

// --- audio geometry --------------------------------------------------------- //
const MIC_RATE = 16000;                 // mic-worklet.js emits exactly this
const MIC_FRAME_SAMPLES = 512;          // ...in 512-sample frames
const MIC_FRAME_MS = MIC_FRAME_SAMPLES / MIC_RATE * 1000;   // 32 ms
const MIC_BYTES_PER_MS = MIC_RATE * 2 / 1000;               // 32
const TTS_RATE = 24000;                 // daemon sends 24 kHz mono int16

// --- pre-roll ring (AC-1a) --------------------------------------------------- //
// Nothing streams while resting and PCM is illegal before capture_start, so the
// arbitration window (~300 ms) plus the round trip would eat the first
// syllables of every command. The cure is a ring that is ALWAYS running: at the
// instant the local detector fires we already hold the audio that came before
// it. 13 frames x 32 ms = 416 ms of lookback, which still reaches >= 300 ms
// before the fire after the detector's own 80 ms chunk latency and the async
// hop out of the inference promise.
const PREROLL_RING_FRAMES = 13;
const PREROLL_LOOKBACK_MS = PREROLL_RING_FRAMES * MIC_FRAME_MS;   // 416 ms
// Belt and braces: if capture_start never comes the buffer must not grow
// without bound. Abort loudly rather than truncating -- a silently clipped
// pre-roll is the exact bug this whole mechanism exists to prevent.
const PREROLL_MAX_MS = 4000;
const WAKE_DECISION_TIMEOUT_MS = 2500;  // no capture_start/wake_reject -> abort

// --- wake detector ----------------------------------------------------------- //
const WAKE_REFRACTORY_MS = 2000, WAKE_CHUNK = 1280;
// After a reply we must NOT reset the detector: reset() clears its 16x80 ms
// embedding context and the next wake is deaf for about 1.3 s. Instead the
// detector keeps being fed continuously and only FIRING is suppressed for a
// short cooldown, so the context stays warm and the tail of our own speech
// cannot re-trigger it (D-14).
//
// The cooldown is the daemon's `wake_cooldown_ms` (docs/wake-words.md
// AC-805), whose default of 1300 ms is DERIVED, not chosen by feel. The wake head scores the
// last 16 embeddings and each embedding covers one 80 ms chunk (wake.js
// WAKE_CONTEXT = 16, app.js WAKE_CHUNK = 1280 samples = 80 ms), so the
// detector's context window spans 16 x 80 = 1280 ms. This constant was
// 700 ms, which re-enabled firing while nearly half of that context was
// still filled with the tail of our own reply -- the self-trigger D-14
// exists to prevent. 1300 ms is the smallest round value that guarantees the
// context has been refilled entirely with audio from after the speaker went
// quiet. If the detector's shape changes, that default moves with it.
//
// WHICH words are on, their phrases and model URLs, the threshold and the
// cooldown are ALL the daemon's (AC-805): `wakeCfg` is the last `ready.wake`
// or `wake` frame, and the page has no default for any of them. Arming WAITS
// for the first one (`wakeCfgKnown`, awaited in `arm()`) rather than failing:
// the page arms itself at boot from the settings answer, which can race the
// `ready` that carries the words, and a failure there would latch
// `micRefused` and never retry. Waiting is visible -- the HUD says what it
// waits for -- and a guessed word would not be.
let wakeCfg = null;               // { words: [{id, phrase, url}], threshold, cooldown_ms }
let wakeCfgResolve = null;
const wakeCfgKnown = new Promise((resolve) => { wakeCfgResolve = resolve; });
let wakeKey = "";                 // the word list `wake` was built from
let wakeGen = 0;                  // bumped by every load and by stop()

function wordsKey(words) {
  return words.map((w) => w.id + "=" + w.url).join(",");
}

// The words that are on, as the page says them (AC-809): `"hey butler"`, or
// `"hey butler" or "hey jarvis"`. Shown to the user, never matched against
// anything. Before the daemon has said, "the wake word": a guessed phrase
// would be a promise the detector may not keep.
function wakePhrases(quote) {
  if (!wakeCfg) return "the wake word";
  const q = wakeCfg.words.map((w) => quote ? '"' + w.phrase + '"' : w.phrase);
  return q.length < 3 ? q.join(" or ")
    : q.slice(0, -1).join(", ") + " or " + q[q.length - 1];
}

// --- liveness watchdogs (anti-latch, NOT endpointing) ------------------------ //
// These never send anything and never end a turn on the daemon; they only stop
// the PAGE from sitting in a state where the microphone is useless if the
// daemon goes quiet. Every budget must be longer than the longest the daemon
// may legitimately stay in that state, or the net pre-empts the policy instead
// of catching a hang.
//
// `capture` was a flat 30000, and it was briefly 300000 on 2026-08-19. That
// blind raise was REVERTED and the reason is worth more than the change was
// (D-32, AC-92): 30000 is a tenth of `END_WORD_MAX_MS = 300_000` in
// daemon/endpointing.py, the ceiling `EndWordPolicy.max_turn_ms` holds, and
// in endword mode the daemon does NOT end a turn on silence at all -- so a
// flat 300000 made "hey jarvis" answer once and then do nothing for five
// minutes in every OTHER mode too. MEASURED, both ways, same fixture: at
// 30000 the page re-armed at 35.9 s and woke again at 40.1 s; at 300000 it was
// still sitting in `capturing` at 55 s with one wake in the daemon's log. The
// theoretical defect (aborting a legitimate long pause) was traded for a real
// one (a five-minute dead page in every other mode) -- neither number is
// right for every mode, because the modes do not share a ceiling.
//
// THE DURABLE FIX, AC-92's other half: the daemon TELLS the page its own live
// ceiling (`ready.capture_ceiling_ms`, and again on every `turn_mode` and
// `settings` broadcast -- `hub._capture_ceiling_ms()`, the same
// `max(absolute_max_turn_ms, policy.max_turn_ms)` arithmetic
// `pipeline.TurnPipeline` itself runs). `captureCeilingMs` below is what
// `armWatchdog` actually reads for CAPTURE; `WATCHDOG_MS.capture` survives
// only as the FALLBACK for a fresh connection that has not yet heard `ready`,
// or an older daemon that never sends the field -- the flat number this
// comment used to defend on its own.
const WATCHDOG_MS = { capture: 30000, thinking: 180000, speak: 120000 };
// The gap kept between the daemon's OWN reported ceiling and the moment this
// page gives up on its side. The daemon's hard cap fires first and forces a
// finalize, which is a real ending with its own round trip (a `final`, then
// whatever answers it) -- setting this page's budget to the SAME number would
// race that round trip and could announce "giving up" a moment before the
// daemon's own answer was already on the wire.
const CAPTURE_CEILING_MARGIN_MS = 5000;
// Overwritten by `applyTurnMode` the moment the daemon says anything
// (`ready`, a spoken mode change, or a settings broadcast). Never written
// anywhere else, so it is always the LAST ceiling the daemon actually
// reported, whichever of those three frames it arrived on.
let captureCeilingMs = WATCHDOG_MS.capture;
const RECONNECT_MS = 1500;

// --- what a coding agent is blocked on (AC-234, AC-249) ---------------------- //
// `qid` -> the daemon's own `question` frame. Declared HERE rather than beside
// the functions that build the card, because `paint()` and `armWatchdog()`
// both read it and both run while this module is still starting up -- a
// `const` further down would be in its temporal dead zone at that moment.
// A plain variable and not browser storage (AC-8) and not a setting (AC-41):
// which questions are open is the DAEMON's fact, re-read from
// `GET /api/session/current` on every `ready`, so a reload and a second device
// agree without this page keeping anything.
const openQuestions = new Map();
// The conversation on screen's agent, from its record (`harness`): the working
// row's and the question card's name for it (`screenAgent`).
let chatHarness = "";

// --- the drain gate (AC-5a, D-14) -------------------------------------------- //
// `speak_end` means "no more audio is coming", NOT "playback finished". The
// daemon writes at socket speed and this page plays at 24 kHz, so at speak_end
// almost the whole reply is still queued. Re-arming there puts the detector
// back to work while our own speaker is finishing the answer, and the device
// hears itself -- Home Assistant's open Voice PE bug. Echo cancellation is no
// safety net: Chrome has historically excluded AudioContext output from the
// canceller's reference signal (.references/audio-internals.md).
//
// So the page waits for the worklet to report a real drain, tells the daemon
// with `playback_done`, and only then re-arms. The wait is bounded by the same
// arithmetic the daemon uses, because an unbounded wait is the microphone-off
// failure this project exists to avoid (D-1).
const TTS_BYTES_PER_MS = TTS_RATE * 2 / 1000;   // 48
const DRAIN_GRACE_MS = 2000;        // mirrors hub.PLAYBACK_GRACE_S
const DRAIN_MAX_MS = 120000;        // hard cap; the mic is never held longer

// --- identity ----------------------------------------------------------------- //
// Minted per page load, in memory only. A stable id would need browser
// storage, and client-side storage is exactly what AC-8 forbids.
const DEVICE_ID = (self.crypto && crypto.randomUUID)
  ? crypto.randomUUID()
  : "dev-" + Math.random().toString(36).slice(2, 12);
const DEVICE_NAME =
  new URLSearchParams(location.search).get("name") || "browser";

// --------------------------------------------------------------------------- //
// State
// --------------------------------------------------------------------------- //
const OFF = "off", WAKE = "wake", CAPTURE = "capture",
      THINKING = "thinking", SPEAK = "speak";

let state = OFF;
// TWO flags, because `running` was one flag doing two jobs and the second job
// was invisible (AC-178, docs/mic-optional.md). `micLive` is "the microphone
// is capturing"; `linkWanted` is "this page wants a socket". They were the
// same boolean for as long as `connect()` was the last line of `start()`,
// which is exactly why the app could not be used with the microphone off: no
// microphone meant no socket, and a typed message has nowhere to go without
// one. Three sites already meant the second thing and said so in their own
// comment (`ws.onclose`); the rest mean the first.
let micLive = false;
let linkWanted = false;
// The browser said no, or there is no device. Remembered so the page does not
// re-prompt on every settings answer (AC-179); cleared only when the owner
// presses a listening control, which is the one event meaning "try again".
let micRefused = false;
// A DICTATION is in flight (AC-181). It taps the same frames the wake detector
// does, and it is deliberately independent of `micLive`: pressing the wave
// button opens the graph if nothing else has.
let dictating = false;
let dictateFrames = [];
let dictateBytes = 0;
let ws = null, reconnectTimer = null;
let micCtx = null, micNode = null, micStream = null;
let playCtx = null, ttsNode = null, ttsPlaying = false;
let wake = null;
let viz = null;                   // visualiser.js, display only
// ONE microphone control now, in the composer. `mic_style` shipped five
// treatments because the mic was the centrepiece of a hero page; beside a text
// field four of them have nowhere to be, and keeping them would mean five
// composer layouts (docs/familiar-ui.md 6.4). The setting is retired in the
// DAEMON; this is the page half of the same commit.
const MIC_BUTTON = $("micBtn");
// The header pill, which is a BUTTON since 2026-09-10 (AC-180, owner's ruling
// in `docs/mic-optional.md` R2). It reports the state and now also controls
// the one thing it was already reporting. Since lantern-ui CU-10 it is the
// BODY of the split voice pill; the chevron beside it opens the voice popover.
const STATE_CHIP = $("stateChip");

// The permission mode and the turn mode are NOT kept here. They arrive in
// `ready` and in broadcasts, and are displayed by the HUD and the settings
// panel; holding a second copy in this file is how two views of one
// server-side value start disagreeing (D-2, AC-8).
let currentTurn = null;           // the turn the DAEMON minted for us
let speakingTurn = null;          // set between speak_start and the drain
let audioBytes = 0;               // TTS bytes received for the current reply

// --- TURN STATE IS PER CONVERSATION (e2e audit 2026-09-26: S4c, S10d, S16,
// S18; `docs/parallel-agents.md` phase 6, page half) ------------------------ //
// `state`, `currentTurn`, `speakingTurn`, the clock and `openItem` describe
// the conversation ON SCREEN and nothing else. They used to describe "the"
// turn, so after the owner opened another conversation mid-turn the idle one
// on screen said "thinking" with a Stop button, the running one's reply
// streamed into it, and Stop killed the agent in the conversation they had
// left. Every turn frame carries `chat` (`send_for_turn`), so it is routed by
// that, never by which turn happens to be newest.
//
// `chatTurns` is every turn THIS SOCKET was told about and has not seen end,
// keyed by chat id: `{turn, pieces}`, where `pieces` is the reply text so
// far, so a conversation opened again mid-turn shows what it already said.
// It dies with the socket (a turn outlives it only as the daemon's, below).
// `watchedChat` is the conversation on screen running a turn this socket
// does NOT drive -- after a reload, or started on another device -- known
// only from `GET /api/chats/{id}/status`, which is polled while it runs
// because no frame of that turn will ever reach this socket.
const chatTurns = new Map();
let watchedChat = "";
let watchTimer = null;
const WATCH_POLL_MS = 3000;

// --- feedback while a turn is running, as DATA the daemon sent (AC-233,
// AC-235, AC-236, `docs/parallel-agents.md` phase 7) --------------------- //
// `turnStartedAt` is a local epoch-ms origin, CALIBRATED from the daemon's
// own `elapsed_ms` rather than stamped when this page happens to notice a
// turn -- a live `worker_event` sets it on arrival, and a reload recovers it
// from `GET /api/chats/{id}/status` (`recoverTurnClock`, below). null means
// "no running turn this page knows of", which `paint()` reads as the signal
// to show it at all, independently of `state`: a background/recovered turn
// has no `state === THINKING` to key off, the same reason the waiting-card
// overlay a few lines below does not key off `state` either.
let turnStartedAt = null;
// The harness's own last tool report (AC-235), never invented: a blank
// `detail` (the `started` kind, sent before the first tool) leaves this
// alone, so a toolless turn shows its age instead of a fabricated sentence.
let lastAction = "";
let elapsedTicker = null;         // setInterval handle; D-1, one writer below
// THE HEARTBEAT'S EVIDENCE (AC-389..AC-393, docs/turn-feedback.md). The last
// `progress` frame -- or the status route's body for a watched turn -- for
// the turn on screen, and when it arrived. `lastWordAt` is the last time
// ANYTHING about that turn reached this page: past `STALL_MS` the working
// line SAYS it may be stuck and acts on nothing (the daemon beats every 5 s,
// so 15 s is three missed beats, never one late one). `QUIET_MS` is the
// other fact the frame carries: the daemon is alive and the AGENT has
// produced nothing for that long. All three are cleared by `setTurnClock`.
let turnProgress = null;
let progressAt = 0;
let lastWordAt = 0;
const STALL_MS = 15000;
const QUIET_MS = 30000;

let ring = [];                    // rolling lookback, ArrayBuffers
let ringBytes = 0;
const RING_MAX_BYTES = PREROLL_RING_FRAMES * MIC_FRAME_SAMPLES * 2;

// --- ambient ring (AC-68, AC-72) --------------------------------------------- //
// A SECOND ring beside the pre-roll one, and the two must never be merged.
// The pre-roll is part of the UTTERANCE: it is flushed into the live ASR
// stream and its byte count has to stay equal to its declared header by
// construction. This one is CONTEXT: it is POSTed on its own, decoded on its
// own, and never reaches the streaming path. Growing the pre-roll to 30 s
// instead would hand that path thirty seconds in one utterance, which is the
// measured 15-seconds-and-zero-characters failure (docs/ambient.md 1 and 3).
//
// It is fed the SAME frames the pre-roll ring is, which is to say the
// CUE-SUPPRESSED ones: onFrame zeroes a frame in place before routeAudio sees
// either ring, so Butler's own earcons cannot end up inside audio that is
// later decoded as something the room said. Taking the raw frames instead
// would put a beep in the context of every single turn.
//
// The window is the DAEMON's (AC-68), it starts at zero, and zero means no
// ring is kept AT ALL rather than kept-and-unsent (AC-72). A daemon that does
// not serve the setting therefore leaves this page behaving exactly as it did
// before ambient existed. 30 s of 16 kHz mono int16 is 960 KB.
let ambientSeconds = 0;
let ambientRing = [];
let ambientBytes = 0;

let pendingWake = false;          // fired locally, waiting for the daemon
let prerollFrames = [];           // ring snapshot + everything since the fire
let prerollBytes = 0;
let lookbackBytes = 0;            // of those, captured BEFORE the fire (AC-800)
let wakeTimer = null;

let wakeSuppressUntil = 0;
let watchdog = null, drainTimer = null;
// One outstanding drain request at a time, matched by token so an answer for
// an utterance we have already walked away from cannot re-arm anything (D-6).
let drainSeq = 0;
let pendingDrain = null;          // { token, turn } while waiting for the queue

// --------------------------------------------------------------------------- //
// UI
// --------------------------------------------------------------------------- //
const STATE_LABEL = {
  [OFF]: "off",
  [WAKE]: "wake-listening",
  [CAPTURE]: "capturing",
  [THINKING]: "thinking",
  [SPEAK]: "speaking",
};

// What the room is told, per state: [kicker, headline, sentence]. The page's
// whole job in a house is to be readable from across it, so the hue, the
// headline and the mic label all move together and say the same thing.
//
// `closing` is not one of the engine's states. It is a SKIN over CAPTURE,
// applied while the daemon's `closing` warning stands, because the user needs
// to see "about to submit -- keep talking" without the page pretending the
// turn has ended. Mixing it into `state` would put a fake state in front of
// the watchdog and the re-arm path.
//
// `reconnecting` and `nolink` are the same trick for the SOCKET (AC-91). With
// the daemon stopped under an armed page this surface read `standing by` /
// `Listening` / "Armed and quiet" for as long as the outage lasted, and the
// only signals were a `link` readout inside a drawer that is collapsed by
// default and a stack of `websocket error` banners. They are skins and not
// states for the same reason `closing` is: the microphone, the wake detector
// and the pre-roll ring all stay live behind them, so nothing here may reach
// the watchdog or the re-arm path (D-1, AC-2).
//
// The off state names the wake phrase (AC-105). It used to say "listening for
// the wake word" without ever saying which word, so the one screen a first-time
// user sees was the one screen that could not tell them how to start.
const STATE_COPY = {
  // THE THIRD FIELD IS THE STATE, NOT THE INSTRUCTION (owner, 2026-09-10).
  // It used to be both, and `micAction()` below says the instruction anyway,
  // so three of the five rows told the reader the same thing twice on two
  // stacked lines: OFF said "arm the microphone" beside "tap to arm the mic",
  // WAKE said "say the wake phrase" beside 'say "hey jarvis"', and SPEAK said
  // "say the wake phrase to interrupt" beside "hey jarvis to interrupt".
  // Split cleanly now: this field names WHERE BUTLER IS, `micAction` names
  // WHAT THE BUTTON DOES, and the two render on one line separated by a dot.
  // AC-168 is unaffected and is why these stayed sentences rather than being
  // cut altogether -- the state has to be readable in words, and after the
  // light theme collapses `thinking` and `speaking` into one hue this is one
  // of the four always-on slots that still tells them apart.
  // The KICKER is the voice pill's word, in the export's words (lantern-ui
  // section 4): Off, Listening, Hearing you, Thinking, Speaking, Reconnecting.
  [OFF]:      ["Off", "Butler", "Butler is not listening."],
  [WAKE]:     ["Listening", "Listening", "Armed and quiet."],
  [CAPTURE]:  ["Hearing you", "Go ahead", "Butler is transcribing as you speak."],
  [THINKING]: ["Thinking", "One moment", "Working out the answer."],
  [SPEAK]:    ["Speaking", "Butler", "Butler is speaking."],
  // There is deliberately NO `muted` row here, and it was written and then
  // deleted on measurement (AC-110). The idea was sound -- a muted Butler and
  // a broken Butler must not look alike -- but a muted reply has no DURATION:
  // with no audio to play, `speak_start` and `speak_end` arrive together, so
  // SPEAK is entered and left inside a single frame. Driven across five muted
  // turns sampled every 350 ms, the headline never once appeared. A row that
  // can only flash is not communication, and shipping styling the page cannot
  // usefully enter is the defect D-30 names. The voice popover's "Read replies
  // aloud" switch carries this instead, the whole time rather than for one
  // frame.
  closing:    ["Closing", "Got it", "Submitting shortly. Keep speaking to stay in the turn."],
  reconnecting: ["Reconnecting", "Reconnecting",
                 "Butler stopped answering. This microphone is still live and "
                 + "still hears the wake phrase, but nothing reaches Butler "
                 + "until the link is back."],
  nolink:     ["Not connected", "Not connected",
               "This page has not reached Butler yet. The microphone is live "
               + "here, and nothing said to it goes anywhere until the link "
               + "comes up."],
};

// Which [data-state] row a skin dresses the page in. `closing` names its own;
// the two link skins share `error`, which the stylesheet and visualiser.js
// both carry a whole branch for and which nothing could reach before (AC-93):
// `data-state` was only ever assigned from `skin || view.attr`, and `view.attr`
// ranges over off / idle / capturing / thinking / speaking.
//
// `wake`'s own [data-state="wake"] row is GONE (ruling R5, CLAUDE.md
// 2026-09-16 backlog, D-30, AC-93): it could never fire, because
// STATE_VIEW[WAKE].attr is "idle" on purpose, and the armed-and-quiet page
// draws the idle hue rather than a cyan nothing could ever show. Left
// unchanged is why: changing which hue the armed page wears is still a
// design decision the owner has not made, not a wiring defect this reversed.
const SKIN_ATTR = { closing: "closing", reconnecting: "error", nolink: "error" };

// What the mic button IS, per state. It is one control doing the job the old
// page spread over three buttons, so what it does has to be legible before it
// is pressed -- hence a label under it in every state, including the states
// where the honest answer is that it does nothing.
// AS OF THE CHAT-FIRST COMPOSER (mocks/q-familiar.html item 2), this function
// answers for the HEADER PILL ALONE. The composer's own microphone used to
// share this act -- that was AC-180's whole point, "one act, two controls" --
// and now dictates instead (toggleDictation, below), because the owner asked
// for the two adjacent audio glyphs to merge into one and for arming to live
// in exactly one place. So every label here says "the header" rather than
// "tap": the composer's mic button no longer does any of this.
function micAction() {
  // AC-105 reads `stateDesc + orbLabel` CONCATENATED and requires the wake
  // phrase while the mic is off. It used to ride on the desc; the desc is the
  // state now, so it rides here, and the two halves had to move together or
  // the criterion would have gone red in between.
  if (!micLive) {
    // A REFUSED microphone is a dead end otherwise (review item 4,
    // 2026-09-26): the ordinary label promised the wake phrase would work
    // once tapped, which is not true of a browser that has already said no,
    // and the only way out of the loop was a sentence buried in `#errors`.
    // The act stays `listenOn` -- it still clears `micRefused` and retries
    // (AC-179) -- so pressing here IS "then tap here to retry".
    if (micRefused) {
      return { label: "microphone blocked - allow it in the browser's site "
                       + "settings, then tap here to retry",
               aria: "Retry microphone permission", act: listenOn };
    }
    return { label: "tap the header to start listening, then say "
                     + wakePhrases(true),
             aria: "Start listening", act: listenOn };
  }
  // The link is down, so the wake phrase is dropped on the floor by
  // onWakeFire ("fired while offline"). Saying "say hey jarvis" under a red
  // page would be the AC-7b defect from the other side -- a control promising
  // a capability that is not there. Standing down still works, so that stays
  // the action (AC-91).
  if (linkSkin) {
    return { label: "no link to Butler, or tap the header to stand down",
             aria: "Stop listening", act: listenOff };
  }
  if (state === CAPTURE) {
    // In a mode that ends on a phrase, name ONE of the daemon's own phrases:
    // the old label read "tap to submit" as if tapping were the only way out
    // of a turn (AC-105). Nothing is invented here -- an empty list or any
    // other turn mode falls straight through to the label that always held.
    const end = endPhrase();
    if (end) {
      return { label: 'listening, say "' + end + '" or tap the header to submit',
               aria: "Submit now", act: endHint };
    }
    return { label: "listening - tap the header to submit",
             aria: "Submit now", act: endHint };
  }
  if (state === THINKING || state === SPEAK) {
    // The one state where this control used to do NOTHING (`act: null`), and
    // therefore the one state a stop belongs in (AC-111, AC-112). The header
    // pill already changes job per state, and this is the job it was
    // missing. The wake phrase still interrupts, so the label names both --
    // the spoken way and the pressed one. (The composer's send button also
    // reaches `stopTurn` while a draft is empty, and Escape reaches it too --
    // this is one of three callers, not the only one, since the mic button
    // stopped being a second implementation of the same act.)
    return { label: wakePhrases(true) + " to interrupt, or tap the header to stop",
             aria: "Stop Butler", act: stopTurn };
  }
  return { label: "say " + wakePhrases(true) + ", or tap the header to stand down",
           aria: "Stop listening", act: listenOff };
}

let skin = null;                  // transient state skin, e.g. "closing"
// The SOCKET's skin, kept apart from `skin` on purpose (AC-91): `setState`
// clears `skin` on every transition, and the link being down survives every
// transition the page can make while it is down. Null, "reconnecting" (the
// socket was open at some point in this page's life) or "nolink" (it never
// has been), which is the distinction the owner reads as "it went away" versus
// "it was never there".
let linkSkin = null;
let everReady = false;            // a `ready` has arrived on some socket

// The daemon's own turn mode and end phrases, mirrored here for the mic label
// (AC-105). Read from the settings answer and from the broadcasts, never
// written down in this directory: which phrases end a turn is the daemon's to
// say, and a page that hardcoded one would name a setting's value (AC-41).
let turnMode = "";
let endPhrases = [];

// Whether the daemon is speaking its replies at all (AC-110, `speak`). Held
// here for the same reason as the two above -- it arrives in the settings
// answer and in every broadcast, so a phone turning speech off is reflected on
// the desk without either page storing anything (D-2, AC-8). It changes what
// the page SAYS and never what it does: a muted Butler still runs the whole
// turn, and the microphone, the detector and the socket are untouched.
let speakMuted = false;

// One phrase, or "" when this mode does not end on one. The FIRST of the list
// rather than all four: the label is read across a room and a room-length list
// of synonyms is not a label.
function endPhrase() {
  return turnMode === "endword" && endPhrases.length ? endPhrases[0] : "";
}

// The wave's palette, READ from the style (docs/lantern-ui.md CU-4). Its
// canvas cannot read a CSS custom property, so this reads them for it: the
// `[data-state]` rows in index.html and this palette both come from the
// style's own `--blue`, `--amber` and `--red`, so they cannot disagree and a
// retuned style moves the wave with it. This used to be two rgba tables kept
// in step with the stylesheet by hand ("one decision written twice").
//
// The mesh keys hold the LINK colour, three stops of one hue: cold `meshIn`
// while the room is heard, amber `meshOut` while Butler thinks and speaks,
// red `meshErr`, and `meshIdle` a blue sunk toward the ground. The node keys
// hold the points, which sit near the TEXT colour so the hue budget is spent
// in one place (docs/lattice-wave.md section 3): near-white on a dark page,
// near-black on a light one, because on a pale ground brightness is ink
// density. visualiser.js reads which theme it is from that node colour.
function styleRgb(cs, name) {
  const v = cs.getPropertyValue(name).trim();
  const m = /^#([0-9a-f]{6})$/i.exec(v);
  if (!m) throw new Error(name + " is " + JSON.stringify(v) + ", not a #rrggbb colour");
  const n = parseInt(m[1], 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
}
function mixRgb(a, b, t) { return a.map((c, i) => Math.round(c + (b[i] - c) * t)); }
function rgbaOf(c, alpha) { return "rgba(" + c.join(", ") + ", " + alpha + ")"; }
function meshFromStyle() {
  const cs = getComputedStyle(document.documentElement);
  const [blue, amber, red, fg, bg] = ["--blue", "--amber", "--red", "--fg", "--bg"]
    .map((name) => styleRgb(cs, name));
  const idle = mixRgb(blue, bg, 0.45);
  const stops = (c) => [rgbaOf(c, 1), rgbaOf(mixRgb(c, fg, 0.3), 1),
                        rgbaOf(mixRgb(c, bg, 0.25), 1)];
  const node = (c) => rgbaOf(mixRgb(fg, c, 0.12), 1);
  return {
    meshIdle: stops(idle), meshIn: stops(blue), meshOut: stops(amber), meshErr: stops(red),
    nodeIdle: node(idle), nodeIn: node(blue), nodeOut: node(amber), nodeErr: node(red),
    trace: rgbaOf(mixRgb(fg, blue, 0.35), 0.9),
    threshold: rgbaOf(blue, 0.85),
  };
}
// Called after every write of the style or the theme, and when the device's
// own light/dark preference changes, which is what `theme: auto` follows
// (CU-6): the stylesheet re-resolves by itself, the canvas needs telling.
// Cosmetic, so a failure is shown and never fatal (the visualiser's rule).
function applyMesh() {
  if (!viz) return;
  try {
    Object.assign(viz.colors, meshFromStyle());
  } catch (e) {
    showError("wave colours: " + (e.message || e));
  }
}
matchMedia("(prefers-color-scheme: light)").addEventListener("change", applyMesh);

// WHERE THE MOVED ELEMENTS GO (AC-689, AC-691, AC-681, CU-6): the ONE
// `#liveLine` and the ONE `#wave` canvas, moved and never copied. This is the
// single place that decides either parent, so a style switch, an effect
// switch and a placement switch cannot leave one behind (AC-260's spirit).
// - `#liveLine`: the slot the STYLE names in `--live-slot` (Lantern: inside
//   the box; Caption: the caption above it).
// - `#wave`: under `lattice` the `viz_placement` slot (`VIZ_SLOT`); under a
//   treatment the slot the stylesheet names in `--wave-slot` for the current
//   `data-voicefx` (the composer's fx layer, or Caption's seam); under `off`
//   it stays where it is, hidden.
// Read off the stylesheet the way `meshFromStyle` reads the palette, so this
// file names no style and no treatment (AC-41). Cosmetic, so a slot the
// stylesheet does not name is shown and never fatal.
function placeByStyle() {
  const cs = getComputedStyle(document.documentElement);
  const slot = (prop) => {
    const id = cs.getPropertyValue(prop).trim();
    const home = id ? document.getElementById(id) : null;
    if (!home) {
      showError("the style names no place for " + prop + " ("
                + JSON.stringify(id) + ")");
    }
    return home;
  };
  const live = slot("--live-slot");
  if (live && $("liveLine").parentNode !== live) live.prepend($("liveLine"));
  let wave = null;
  if (voiceEffect === "lattice") {
    wave = VIZ_SLOT[vizPlacement] ? $(VIZ_SLOT[vizPlacement]) : null;
  } else if (voiceEffect !== "off") {
    wave = slot("--wave-slot");
  }
  if (wave && $("wave").parentNode !== wave) wave.append($("wave"));
}

// The closing warning, drawn as a bar that runs down.
//
// It is a WARNING, not a countdown, and the difference matters: the daemon
// does not tell the page how long the pause will be, and it must not -- the
// smart policy asks a model again in 450 ms and may well decide you are still
// talking. So the bar animates over the longest wait any mode allows and is
// cancelled the moment another partial arrives. It shows "something is about
// to happen, speak if you are not done", which is exactly what the message
// means, rather than a deadline the daemon never promised.
const CLOSING_BAR_MS = 6000;
function showClosing() {
  setSkin("closing");
  const bar = $("closingFill");
  $("closingBar").hidden = false;
  bar.style.transition = "none";
  bar.style.width = "100%";
  requestAnimationFrame(() => {
    const gentle = matchMedia("(prefers-reduced-motion: reduce)").matches;
    bar.style.transition = "width " + CLOSING_BAR_MS + "ms linear";
    bar.style.width = gentle ? "40%" : "0%";
  });
}
function hideClosing() {
  $("closingBar").hidden = true;
  if (skin === "closing") { skin = null; paint(); }
}

// The rest of what a state looks like, beside the words in STATE_COPY: the
// attribute the whole stylesheet keys its hue off, the word on the capsule
// mic, and which way the wave is flowing. Written as a table for the same
// reason STATE_COPY is -- three inline ternary chains over the same five
// states is three places to forget a state, and one of them was a five-branch
// nested ternary.
const STATE_VIEW = {
  [OFF]:      { attr: "off",       wave: "idle" },
  [WAKE]:     { attr: "idle",      wave: "idle" },
  [CAPTURE]:  { attr: "capturing", wave: "input" },
  [THINKING]: { attr: "thinking",  wave: "idle" },
  [SPEAK]:    { attr: "speaking",  wave: "output" },
};

// The state as ONE WORD, which is the channel AC-168 exists for: in the light
// theme gold reads as brown and `thinking` and `speaking` are the same hue, so
// a page whose only signal is that something turned gold cannot say which of
// the two it is in. Three places carry it and all three are always visible --
// the header chip, the sidebar foot, and the wave band's own label. The
// KICKER of STATE_COPY is that word; nothing new is invented here.
function paint() {
  // The link outranks the turn: a `closing` warning drawn over a dead socket
  // would be the page describing a submission that cannot happen (AC-91).
  const dress = linkSkin || skin;
  // "SPEAKING" IS DRAWN ONLY WHILE SOMETHING IS AUDIBLE (review 2026-09-26).
  // `state` is SPEAK from `speak_start` to the drain, and the daemon sends
  // `speak_start` once per TURN (hub `_begin_speaking`) -- so in harness mode,
  // where "Working on it." is spoken first, the header said "speaking" for the
  // whole silent agent run behind it. `state` itself stays SPEAK, because the
  // wake cooldown and the drain depend on it; only what is DRAWN reads the
  // worklet's `playing` hint, and a gap between sentences reads "thinking",
  // which is what it is.
  const shown = state === SPEAK && !ttsPlaying ? THINKING : state;
  const key = dress || shown;
  const [kicker, title, desc] = STATE_COPY[key] || STATE_COPY[OFF];
  const view = STATE_VIEW[shown] || STATE_VIEW[OFF];
  // A skin names its own attribute through SKIN_ATTR (there is a
  // [data-state="closing"] row in the stylesheet, and an [data-state="error"]
  // one the link skins now reach); everything else reads off the real state,
  // because the skin changes how the turn LOOKS and not what it is.
  document.documentElement.dataset.state =
    dress ? (SKIN_ATTR[dress] || dress) : view.attr;
  $("stateKicker").textContent = kicker;
  $("stateTitle").textContent = title;
  $("stateDesc").textContent = desc;
  // THE STATE INSIDE THE BOX (lantern-ui section 4): "Hearing you",
  // "Thinking", "Speaking" -- the pill's own word, where the eyes are. Not
  // over a dead link: the error line says that.
  const boxWord = !linkSkin
    && (shown === CAPTURE || shown === THINKING || shown === SPEAK);
  $("inState").hidden = !boxWord;
  $("inStateWord").textContent = boxWord ? kicker : "";

  const mic = micAction();
  $("orbLabel").textContent = mic.label;
  // AC-180: the header pill is the ONLY control `micAction()` drives now
  // (mocks/q-familiar.html item 2). `MIC_BUTTON` -- the composer's
  // microphone -- used to read the same act here, which is what AC-180's
  // "one function feeds both" comment was about; it now dictates instead, and
  // `paintDictate()` is its own paint function, called below, the same way
  // `paintSend()` is `sendbtn`'s.
  STATE_CHIP.setAttribute("aria-label", mic.aria);
  STATE_CHIP.disabled = !mic.act;
  // THE ARMED LINE SAYS WHAT IS ACTUALLY HAPPENING (AC-234). A coding agent
  // blocked on a question is not "standing by" and not "thinking", and the
  // two slots under the composer are the one place this page describes the
  // situation in words. Written HERE, after the ordinary writes and never in
  // `paintAsk`, because `paint()` is the single writer of both slots -- a
  // second writer would be overwritten on the next transition (web/AGENTS.md).
  // THE WORKING ROW (AC-694, CU-12): "pi is working in butler, 1:42,
  // Running test/test_turn.py" as the last item in the conversation, from the
  // daemon's own evidence and nothing else (AC-392). `#stateDesc` is its
  // words and this function is their one writer.
  const where = $("chatProject").textContent
    ? " in " + $("chatProject").textContent : "";
  let stalled = false;
  if (openQuestions.size) {
    $("stateDesc").textContent = askingAgent() + " is waiting for your answer"
      + where + (turnStartedAt != null
                 ? ", " + elapsedLabel(Date.now() - turnStartedAt) : "") + ".";
    $("orbLabel").textContent = "answering or stopping both free its slot";
  } else if (turnStartedAt != null && !dress) {
    // A CODING AGENT'S OWN PROGRESS, NEVER INVENTED (AC-233, AC-235, AC-236).
    // `!dress`: a link skin already says the honest thing (the error line),
    // and a ticking number under it would be the reassuring-number-while-
    // the-page-cannot-tell failure AC-7b forbids.
    const age = Date.now() - turnStartedAt;
    const silent = Date.now() - lastWordAt;
    const lead = (screenAgent() || "Butler") + " is working" + where + ", "
      + elapsedLabel(age);
    const what = turnProgress ? workingWhat(turnProgress)
      : (lastAction ? "Running " + lastAction : "");
    // SAID, NEVER ACTED ON (AC-393): three missed beats. Audio still playing
    // IS word from Butler: after the last beat the daemon waits out the
    // page's own playback, which can outlast 15 s.
    if (silent >= STALL_MS && !ttsPlaying) {
      stalled = true;
      $("stateDesc").textContent = lead + (what ? ", " + what : "")
        + ", quiet for " + elapsedLabel(silent) + ", it may be stuck";
    } else {
      const quiet = quietMs();
      $("stateDesc").textContent = lead + (what ? ", " + what : "")
        + (turnProgress && quiet >= QUIET_MS && turnProgress.phase !== "asking"
           ? ", quiet for " + elapsedLabel(quiet) : "");
    }
  }
  const row = $("workRow");
  row.hidden = !(openQuestions.size || (turnStartedAt != null && !dress));
  row.dataset.stalled = stalled ? "true" : "false";
  paintSend();
  paintDictate();
  paintSend();
  paintDictate();

  paintHint();
  paintAmbient();
  if (viz) {
    viz.setMode(view.wave);
    // The same string the stylesheet keys its hue off. The canvas had three
    // palettes against the stylesheet's eight rows, so `error` drew a blue
    // wave inside a red page and read as "waiting". Passing the state costs a
    // coupling -- the canvas now knows this page's vocabulary -- and buys the
    // only thing that can express `error` and `closing` at all.
    viz.setState(dress ? (SKIN_ATTR[dress] || dress) : view.attr);
  }
}

// One rule, one place. paint() and renderLive() both decide whether the hint
// is showing, and deriving it twice meant the TEXT only followed the state on
// a paint -- so during a turn it could still read "awaiting wake phrase".
// It speaks ONLY while capturing. This line sits on the live-partial row, and
// its job is holding that row open until a partial lands -- so "listening..."
// earns its place and the other two readings did not: "awaiting wake phrase"
// and "microphone is off" were the fifth and fourth ways the page said a thing
// the header chip, the band label, the sidebar foot and the state line under
// the composer were all already saying (owner, 2026-09-10). `.live` keeps its
// `min-height`, so an empty hint holds the row and nothing shifts.
function paintHint() {
  const hint = $("transcriptHint");
  hint.textContent = state === CAPTURE ? "listening..." : "";
  hint.hidden = !hint.textContent
    || !!($("committed").textContent || $("tentative").textContent);
}

// The ring is running, and the page says so in words that cannot be read as
// anything else (AC-72). It is up whenever the ring is up -- which is whenever
// the microphone is live and the daemon has given a window -- and not merely
// during a turn, because the retroactive half is exactly what a turn-scoped
// indicator would hide. The owner chose the name `ambient`, that name says
// nothing about the last half-minute being kept
// (.references/ambient-mode.md 7), so this sentence has to.
//
// Painted from paint(), which is reached by every path that can change either
// half: setState (start, stop, every transition) and applyPageValues (the
// settings answer and every broadcast).
// THE VOICE POPOVER'S THREE SWITCHES (AC-686), painted from what the daemon
// and the microphone SAY, never from the press. Read replies aloud is the
// EXISTING `speak` setting (AC-182), so every device goes quiet at once
// (D-2, AC-8); it replaced the header's `#speakBtn` and `#muteChip` and kept
// the button's id.
function paintSwitch(btn, on) {
  btn.setAttribute("aria-checked", on ? "true" : "false");
  btn.querySelector(".swword").textContent = on ? "On" : "Off";
}
function paintSpeakButton() {
  paintSwitch($("speakBtn"), !speakMuted);
}

// Room memory: the ring is running, and the page says so in words that cannot
// be read as anything else (AC-72, AC-687). Up whenever the ring is up --
// whenever the microphone is live and the daemon has given a window -- and not
// merely during a turn, because the retroactive half is exactly what a
// turn-scoped indicator would hide. The header tag says it on every width;
// the popover's switch and its warning line say it where it is changed.
// Painted from paint(), which every path that changes either half reaches.
function paintAmbient() {
  const on = micLive && ambientSeconds > 0;
  const tag = $("ambientChip");
  tag.hidden = !on;
  if (on) {
    tag.setAttribute("aria-label", "Recording the room: the last "
                     + Math.round(ambientSeconds) + " seconds are kept");
  }
  paintSwitch($("wakeSw"), micLive);
  paintSwitch($("roomSw"), ambientSeconds > 0);
  $("roomWarn").hidden = !on;
  $("roomHelp").textContent = "Keeps the last "
    + Math.round(ambientSeconds || roomSecondsOn()) + " seconds of the room";
}

// The composer's send control, which is ALSO the stop control (AC-111,
// AC-112). It changes SHAPE and not only colour -- the arrow becomes a square
// -- because gold and amber collapse into one hue in the light theme and a
// control that says "stop" by turning a shade warmer says nothing there.
//
// `setAttribute`, NOT `.hidden`. The `hidden` IDL property is defined on
// HTMLElement and an <svg> is an SVGElement, so `svg.hidden = false` sets a
// plain expando and leaves the attribute in place: the stop square would never
// appear and the arrow would never come back, while the accessible name said
// "Stop". Nothing about the page would look broken.
function paintSend() {
  // A DRAFT OUTRANKS THE STOP, and the label is how the control says so.
  // This used to be `state === THINKING || state === SPEAK` alone, so the
  // button read "Stop Butler" while there was a message waiting in the box --
  // and pressing it stopped him, left the text stranded, and said nothing.
  // Measured 2026-09-11 by `test/manual/e2e_scenarios.py` F5: the page holds
  // its speaking state for a second or two past `speak_end` while the audio
  // queue drains, so that window is open on EVERY turn, not just a long one.
  //
  // The rule is now: if you have typed something you meant to send it. Stop
  // is what the button does when there is nothing else it could mean.
  // Whoever wants to stop with a draft in the box still has Escape, the
  // microphone control and the wake phrase (D-18 -- all four reach `stopTurn`).
  //
  // CAPTURE is in `busy` too (the owner's wedged endword turn, cause 1): a
  // capture that will not end needed a Stop the same as a reply that will
  // not finish, and this button was DISABLED the whole time -- `stopping`
  // below was false, so `btn.disabled = !stopping && draft === ""` left a
  // control drawn but unpressable for the one state the owner was stuck in.
  const busy = state === THINKING || state === SPEAK || state === CAPTURE;
  const draft = $("input").value.trim();
  const stopping = busy && !draft;
  const btn = $("sendBtn");
  btn.classList.toggle("stop", stopping);
  if (stopping) {
    $("sendIcon").setAttribute("hidden", "");
    $("stopIcon").removeAttribute("hidden");
    $("stopWord").removeAttribute("hidden");
  } else {
    $("sendIcon").removeAttribute("hidden");
    $("stopIcon").setAttribute("hidden", "");
    $("stopWord").setAttribute("hidden", "");
  }
  btn.setAttribute("aria-label", stopping ? "Stop Butler" : "Send");
  btn.disabled = !stopping && draft === "";
}

function setState(s) {
  state = s;
  skin = null;
  paint();
  armWatchdog();
}

function setSkin(name) { skin = name; paint(); }

// The link skin, raised and cleared from the socket's own callbacks (AC-91).
// It changes NOTHING but what the page says: no state moves, no timer is
// touched, neither flag is read and the microphone is never stopped -- every
// one of those would be the latch D-1 forbids.
function setLinkSkin(name) {
  if (linkSkin === name) return;
  linkSkin = name;
  errDismissed = false;
  paintErrLine();
  paint();
}

function setLink(text, ok) {
  hud("hLink", text);
  const el = $("hLink");
  if (el) el.style.color = ok ? "" : "var(--warn)";
  // THE SAME FACT, ON THE ACCOUNT ROW (AC-686): `#footLink` mirrors what
  // `#hLink` in the Diagnostics sheet says, from the one place that decides
  // it, and is drawn only while the link is NOT up (`data-ok`).
  const footWord = $("footLink");
  if (footWord) {
    footWord.textContent = text.charAt(0).toUpperCase() + text.slice(1);
    footWord.dataset.ok = ok ? "true" : "false";
  }
}

function renderLive(committed, tentative) {
  // textContent, never innerHTML: the transcript is whatever the recognizer
  // heard, and it reaches this page from a microphone in a room.
  $("committed").textContent = committed || "";
  $("tentative").textContent = tentative ? " " + tentative : "";
  paintHint();
}

// A HUD id that no longer exists must not take the page down with it. An ES
// module that throws stops executing, and the symptom is controls quietly
// missing rather than an error -- which reads as "that was never built" and
// costs the next hour (D-22). Warned once so a typo is still discoverable.
const missingHud = new Set();
function hud(id, value) {
  const el = document.getElementById(id);
  if (!el) {
    if (!missingHud.has(id)) {
      missingHud.add(id);
      try { console.warn("[butler] no HUD element", id); } catch { /* */ }
    }
    return;
  }
  el.textContent = value;
}

// Errors are VISIBLE, never console-only: the device that fails is usually a
// phone with no console (D-4 -- fail loud).
//
// THE ERROR LINE (AC-696, CU-13): one quiet line directly above the
// composer. `#errors` in the Diagnostics sheet still holds every line --
// nothing here is deleted -- but what a stranger SEES is this one line, and a
// second failure raises a COUNT on it rather than stacking a second bar.
// `paintErrLine` is its one painter, in a fixed order: a lost link (the draft
// stays in the box, `sendTyped`), then a refused microphone (said here
// because nothing else on screen says it since `#micLine` went), then the
// logged problems. Dismiss hides it until the next fact arrives.
let errStripCount = 0;
let errLast = "";
let errDismissed = false;
function paintErrLine() {
  let head = "", text = "";
  if (linkSkin) {
    head = "Not connected.";
    text = "Anything you type stays in the box until Butler is back.";
  } else if (micRefused) {
    head = "Microphone blocked.";
    text = "Allow it in the browser's site settings, then press the voice "
      + "pill to try again.";
  } else if (errStripCount) {
    head = errStripCount > 1 ? errStripCount + " problems." : "";
    text = errStripCount > 1 ? "Most recent: " + errLast : errLast;
  }
  $("errStripHead").textContent = head;
  $("errStripText").textContent = text;
  $("errStrip").hidden = errDismissed || !(head || text);
}
// A new fact: shown again even after a Dismiss.
function raiseErrLine() {
  errDismissed = false;
  paintErrLine();
}
// The LOG half alone: a line in `#errors` and the console, no strip. For a
// fact something else on screen already says -- `reportMicFailure` below.
function logError(text) {
  const box = $("errors");
  box.hidden = false;
  const line = document.createElement("div");
  line.className = "err";
  line.textContent = new Date().toLocaleTimeString() + "  " + text;
  box.prepend(line);
  while (box.children.length > 6) box.removeChild(box.lastChild);
  try { console.error("[butler]", text); } catch { /* no console */ }
}
function showError(text) {
  logError(text);
  errStripCount += 1;
  errLast = text;
  errDismissed = false;
  paintErrLine();
}
function clearErrors() {
  $("errors").hidden = true; $("errors").textContent = "";
  errStripCount = 0;
  errLast = "";
  paintErrLine();
}

// The transcript history. One item per turn: what was said, what came back,
// which tier answered. Built as elements rather than markup because every
// string in it is spoken input.
//
// 50, not 8 (AC-94). Eight turns is under an hour of use and the route already
// serves the newest 100 messages, so the page was throwing away most of what
// the daemon had already sent it.
const HISTORY_MAX = 50;
let openItem = null;

// THE EMPTY STATE (AC-170, AC-692, CU-14): the greeting, the composer
// CENTRED under it (`.col.empty`) and the suggestions under that. ONE fact --
// "nothing has been said in this conversation yet" -- drawn in three places,
// so this is the one writer of all three; a caller that set one and forgot
// another would leave a stray chip row under a real transcript. The greeting
// is the local clock's.
function setEmptyState(shown) {
  $("histEmpty").hidden = !shown;
  $("emptyChips").hidden = !shown;
  $("scroller").parentNode.classList.toggle("empty", shown);
  if (shown) {
    const h = new Date().getHours();
    $("greeting").textContent = h < 12 ? "Good morning."
      : h < 18 ? "Good afternoon." : "Good evening.";
  }
}

// --- assistant markdown (AC-94) ---------------------------------------------- //
// Harness mode's whole output is markdown and this page rendered none of it: a
// real answer read on screen as `I'm **pi** -- a minimal terminal coding agent
// harness built by Mario Zechner (badlogic). *`, asterisks and all.
//
// This is the one change here that can be wrong in a way nobody sees, so the
// rules are absolute and they are structural rather than a promise:
//
//   * There is no innerHTML, no insertAdjacentHTML and no markdown library
//     anywhere below. Every string reaches the DOM through textContent or
//     through append(string), which builds a text node -- neither parses
//     markup. The worst a hostile reply can do is look wrong.
//   * The element set is FIXED and closed: strong, em, code, pre > code, ul,
//     ol, li, and since 2026-09-10 also h1-h3, p, br, blockquote, hr and the
//     table family. Nothing else is created, and there is still deliberately
//     NO IMG HERE -- an image in markdown would be an outbound request
//     authored by whatever a coding agent read on disk. Images, audio and
//     video the agent SHOWS come from `web/artifacts.js` instead, drawn in a
//     container after `.answered`, with a `src` the page builds from the
//     record's ids on `/api/artifacts/` (docs/rich-chat.md RC-8, AC-632);
//     `![x](url)` in a reply is still text. `A` IS NOW CREATED (review item 20a,
//     2026-09-26), restricted to bare `http(s)` URLs and markdown
//     `[text](http...)` links: still built with `createElement`+`textContent`
//     only, still no `innerHTML`, the scheme is checked with `new URL()`
//     before an anchor is made, and every anchor carries
//     `target="_blank" rel="noopener noreferrer"` and the muted `--faint`
//     colour so it does not read as an accent. `probe_ux.py`'s AC-94 check
//     ("no links or images are ever produced") is stale on the link half and
//     needs updating by whoever owns that probe; the image half still holds.
//   * It runs OVER THE WHOLE ANSWER, and used to run per piece. A reply
//     arrives one sentence per `reply` message; each piece used to be parsed
//     alone into its own <span>, so no block could span a sentence boundary.
//     MEASURED on a real agent reply: a three-item list became THREE <ul> of
//     one <li>, one fenced block became two <pre> with one empty, and a table
//     fell inside the wrong fence. `logReply` now accumulates the raw markdown
//     on the turn and re-parses all of it each time a piece lands; the cost and
//     the two consequences are written down there.
//
// Known limit, stated rather than papered over: emphasis does not nest, so
// `**bold with *inner* em**` shows the inner asterisks as text. Fixing it means
// a recursive parser, and a bigger parser over untrusted input is the wrong
// trade for a case a spoken conversation almost never produces.
const MD_FENCE = /^\s*```/;
const MD_BULLET = /^(\s*)[-*+]\s+(.*)$/;
const MD_NUMBER = /^(\s*)\d+[.)]\s+(.*)$/;
const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const MD_HR = /^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
const MD_QUOTE = /^\s{0,3}>\s?(.*)$/;
const MD_ROW = /^\s*\|.*\|\s*$/;
const MD_SEP = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;
// Blockquotes and lists recurse. A coding agent can emit a pathological
// document and this runs on the page that owns the microphone, so the depth is
// bounded rather than trusted.
const MD_MAX_DEPTH = 4;

const MD_INLINE =
  /`([^`]+)`|\*\*([\s\S]+?)\*\*|__([\s\S]+?)__|\*([^*\n]+)\*|_([^_\n]+)_|\[([^\]\n]+)\]\((https?:\/\/[^\s()]+)\)|(https?:\/\/[^\s<>"'`]+)/g;

// http(s) ONLY (review item 20a): `new URL` accepts `javascript:`, `data:`
// and worse, and the regex above already anchors on a literal `http`, but
// this is the one place refusal is enforced rather than assumed, the same
// discipline AC-31's spawn gate follows for a path.
function isHttpUrl(u) {
  try {
    const p = new URL(u);
    return p.protocol === "http:" || p.protocol === "https:";
  } catch { return false; }
}

// Trailing sentence punctuation is not part of a URL ("see http://x.com."),
// so it is trimmed off the END rather than excluded from the character
// class, which would also cut a URL that legitimately contains it mid-string
// (a query string comma, a path dot). A trailing `)` is trimmed only when it
// is NOT balanced by an earlier `(` in the same match, so
// `.../Foo_(disambiguation)` survives whole.
function trimUrlTrailing(url) {
  let out = url;
  for (;;) {
    const last = out.charAt(out.length - 1);
    if (last === ")") {
      const opens = (out.match(/\(/g) || []).length;
      const closes = (out.match(/\)/g) || []).length;
      if (closes > opens) { out = out.slice(0, -1); continue; }
      break;
    }
    if (".,;:!?'\"".indexOf(last) >= 0) { out = out.slice(0, -1); continue; }
    break;
  }
  return out;
}

// `target=_blank` and `rel="noopener noreferrer"` on every one (a background
// page that could reach back into this one, or send a referrer naming a
// private tailnet host, is not a link a spoken conversation should be making
// on the owner's behalf). `--muted` is the style's dim TEXT colour, never a
// state colour (AC-680), so a link never reads as the state accent; the
// style's `--faint` is for icons and tentative words, too dim for text.
function mdLink(href, label) {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = label;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.style.color = "var(--muted)";
  return a;
}

function mdInline(text, into) {
  let last = 0, m;
  MD_INLINE.lastIndex = 0;
  while ((m = MD_INLINE.exec(text))) {
    if (m.index > last) into.append(text.slice(last, m.index));
    // An underscore INSIDE a word is not emphasis, and this matters here more
    // than it would elsewhere: a coding agent emits `some_snake_case` and
    // `https://host/a_b_c` all day, and both came out with their middles
    // italicised and their underscores eaten. Checked against the preceding
    // character rather than with a lookbehind, which older iOS Safari cannot
    // parse -- and a SyntaxError in a module takes the whole page with it.
    if ((m[3] != null || m[5] != null) && /\w/.test(text.charAt(m.index - 1))) {
      into.append(m[0]);
      last = m.index + m[0].length;
      continue;
    }
    // `![alt](url)` is markdown IMAGE syntax, one character to the left of
    // this same bracket-paren shape, and AC-94 still forbids an `img` -- so a
    // leading `!` means this stays TEXT rather than becoming a link to the
    // alt words.
    if (m[6] != null && text.charAt(m.index - 1) === "!") {
      into.append(m[0]);
      last = m.index + m[0].length;
      continue;
    }
    if (m[6] != null && m[7] != null) {
      if (isHttpUrl(m[7])) { into.append(mdLink(m[7], m[6])); }
      else { into.append(m[0]); }
      last = m.index + m[0].length;
      continue;
    }
    if (m[8] != null) {
      const url = trimUrlTrailing(m[8]);
      if (url && isHttpUrl(url)) {
        into.append(mdLink(url, url));
        last = m.index + url.length;         // the trimmed tail is plain text
      } else {
        into.append(m[0]);
        last = m.index + m[0].length;
      }
      continue;
    }
    let tag = "code", body = m[1];
    if (m[2] != null) { tag = "strong"; body = m[2]; }
    else if (m[3] != null) { tag = "strong"; body = m[3]; }
    else if (m[4] != null) { tag = "em"; body = m[4]; }
    else if (m[5] != null) { tag = "em"; body = m[5]; }
    const el = document.createElement(tag);
    el.textContent = body;
    into.append(el);
    last = m.index + m[0].length;
  }
  if (last < text.length) into.append(text.slice(last));
}

// ---- block level ---------------------------------------------------------- //
// The closed element set, extended 2026-09-10 from
// `strong em code pre ul ol li` to add `h1 h2 h3 p blockquote hr br` and the
// table family. The RULE is unchanged and is the part that matters (AC-94):
// every element is built by hand, every string goes in through `textContent`
// or `append`, there is no `innerHTML` anywhere and no markdown library --
// and there is still deliberately NO `img` (artifacts.js draws shown images,
// outside the answer, from Butler's own route: docs/rich-chat.md RC-8). `mdInline` now makes an `a` for a
// bare `http(s)` URL or a markdown `[text](http...)` link (review item 20a,
// 2026-09-26); see the comment above it for the constraints that make that
// safe. `probe_ux.py` measured 0 anchors and 0 images and needs its anchor
// half updated; the image count still holds.

function mdCells(line) {
  let s = line.trim();
  if (s.charAt(0) === "|") s = s.slice(1);
  if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

// A table is a pipe row FOLLOWED BY a separator row, never a pipe row alone.
// Prose about "a | b" is not a table, and a bare `---` is a horizontal rule;
// requiring the pair is what tells the three apart without guessing.
function mdIsTable(lines, i) {
  return MD_ROW.test(lines[i] || "") && MD_SEP.test(lines[i + 1] || "")
    && (lines[i + 1] || "").indexOf("-") >= 0;
}

function mdTable(lines, i, into) {
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  for (const c of mdCells(lines[i])) {
    const th = document.createElement("th");
    mdInline(c, th);
    head.append(th);
  }
  thead.append(head);
  table.append(thead);
  i += 2;                                   // the header and its separator
  const body = document.createElement("tbody");
  while (i < lines.length && MD_ROW.test(lines[i]) && !MD_SEP.test(lines[i])) {
    const tr = document.createElement("tr");
    for (const c of mdCells(lines[i])) {
      const td = document.createElement("td");
      mdInline(c, td);
      tr.append(td);
    }
    body.append(tr);
    i += 1;
  }
  if (body.childNodes.length) table.append(body);
  into.append(table);
  return i;
}

// Nesting is by INDENT, and a deeper run is appended to the `li` above it so
// the nested list is inside its parent item rather than a sibling of the whole
// list. A run that changes between bullets and numbers ENDS the list, because
// the two are different lists and merging them renumbers the second.
function mdList(lines, i, into, depth) {
  const first = MD_BULLET.exec(lines[i]) || MD_NUMBER.exec(lines[i]);
  const ordered = !MD_BULLET.test(lines[i]);
  const base = first[1].length;
  const list = document.createElement(ordered ? "ol" : "ul");
  while (i < lines.length) {
    const m = MD_BULLET.exec(lines[i]) || MD_NUMBER.exec(lines[i]);
    if (!m) break;
    const indent = m[1].length;
    if (indent < base) break;
    if (indent > base) {
      if (depth >= MD_MAX_DEPTH) { i += 1; continue; }
      let host = list.lastElementChild;
      if (!host) { host = document.createElement("li"); list.append(host); }
      const before = i;
      i = mdList(lines, i, host, depth + 1);
      if (i === before) i += 1;
      continue;
    }
    if ((!MD_BULLET.test(lines[i])) !== ordered) break;
    const li = document.createElement("li");
    mdInline(m[2], li);
    list.append(li);
    i += 1;
  }
  into.append(list);
  return i;
}

function mdQuote(lines, i, into, depth) {
  const body = [];
  while (i < lines.length && MD_QUOTE.test(lines[i])) {
    body.push(MD_QUOTE.exec(lines[i])[1]);
    i += 1;
  }
  const el = document.createElement("blockquote");
  if (depth < MD_MAX_DEPTH) mdBlocks(body.join("\n"), el, depth + 1);
  else el.append(body.join("\n"));
  into.append(el);
  return i;
}

// A PARAGRAPH ends at a blank line or at the start of any other block. A single
// newline inside one becomes a `br` rather than a space: CommonMark calls that
// a soft break and renders it as a space, and a model that put a line break
// there usually meant it -- an address, a list it forgot to bullet, a pair of
// short statements. Losing those is a visible regression from the `pre-wrap`
// this replaced, and keeping them costs one element.
function mdParagraph(lines, i, into) {
  const p = document.createElement("p");
  let first = true;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) break;
    if (MD_FENCE.test(line) || MD_HEADING.test(line) || MD_HR.test(line)
        || MD_QUOTE.test(line) || MD_BULLET.test(line) || MD_NUMBER.test(line)
        || mdIsTable(lines, i)) break;
    if (!first) p.append(document.createElement("br"));
    mdInline(line.trim(), p);
    first = false;
    i += 1;
  }
  if (p.childNodes.length) into.append(p);
  return i;
}

function mdFence(lines, i, into) {
  i += 1;
  const body = [];
  while (i < lines.length && !MD_FENCE.test(lines[i])) { body.push(lines[i]); i += 1; }
  if (i < lines.length) i += 1;              // consume the closing fence
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = body.join("\n");
  pre.append(code);
  into.append(pre);
  return i;
}

// Does this piece BEGIN a block? The daemon hands the page one SENTENCE at a
// time, and a piece that opens a list, a heading, a fence, a quote or a table
// has to land at the start of a LINE or the paragraph before it swallows it.
// MEASURED on real captured frames: joining every piece with a space cost the
// FIRST BULLET of the list, because "...exercise. - A bullet about" is prose,
// and a list of three rendered with two items.
function mdStartsBlock(piece) {
  const first = String(piece == null ? "" : piece)
    .replace(/^\s+/, "").split("\n")[0];
  return MD_FENCE.test(first) || MD_HEADING.test(first) || MD_HR.test(first)
    || MD_QUOTE.test(first) || MD_BULLET.test(first) || MD_NUMBER.test(first)
    || MD_ROW.test(first);
}

function mdRender(text, into) {
  mdBlocks(text, into, 0);
}

function mdBlocks(text, into, depth) {
  const lines = String(text == null ? "" : text)
    .replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const before = i;
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (MD_FENCE.test(line)) { i = mdFence(lines, i, into); }
    else if (MD_HEADING.test(line)) {
      const h = MD_HEADING.exec(line);
      // h4-h6 fold into h3. A chat bubble has three useful heading sizes and a
      // sixth-level heading rendered smaller than the body text is noise.
      const el = document.createElement("h" + Math.min(h[1].length, 3));
      mdInline(h[2], el);
      into.append(el);
      i += 1;
    } else if (mdIsTable(lines, i)) { i = mdTable(lines, i, into); }
    else if (MD_HR.test(line)) {
      into.append(document.createElement("hr"));
      i += 1;
    } else if (MD_QUOTE.test(line)) { i = mdQuote(lines, i, into, depth); }
    else if (MD_BULLET.test(line) || MD_NUMBER.test(line)) {
      i = mdList(lines, i, into, depth);
    } else { i = mdParagraph(lines, i, into); }
    // Never spin. Every branch above is supposed to consume at least one line;
    // this is the net that means a regex change cannot hang the page that owns
    // the microphone.
    if (i === before) i += 1;
  }
}

// `atMs` is when the turn HAPPENED, in epoch milliseconds. A live turn passes
// Date.now(); a restored one passes the daemon's own `timestamp` off the chat
// file, because stamping a restored turn with the moment the page loaded is
// inventing a fact (AC-7b). A message carrying no timestamp gets NO <time>
// rather than a plausible one.
// --- icons, built as NODES ---------------------------------------------- //
// createElementNS and setAttribute, never innerHTML. The rule that keeps the
// transcript a security boundary (AC-94) is that no string in this file is
// ever parsed as markup, and an icon helper reaching for innerHTML would be a
// second door into exactly the DOM the tokenizer is careful about.
const SVG_NS = "http://www.w3.org/2000/svg";
function icon(size, width, shapes) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", String(width));
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const shape of shapes) {
    const el = document.createElementNS(SVG_NS, shape[0]);
    for (const k of Object.keys(shape[1])) el.setAttribute(k, shape[1][k]);
    svg.append(el);
  }
  return svg;
}
const COPY_ICON = () => icon(16, 1.5, [
  ["rect", { x: 9, y: 9, width: 11, height: 11, rx: 2.4 }],
  ["path", { d: "M15 5.6A2.6 2.6 0 0 0 12.4 3H6.6A2.6 2.6 0 0 0 4 5.6v5.8A2.6 2.6 0 0 0 6.6 14" }],
]);
const MARK_ICON = () => icon(13, 1.6, [["path", { d: "M12 4l7 8-7 8-7-8z" }]]);
const REWIND_ICON = () => icon(16, 1.6, [
  ["path", { d: "M9 7H15.2a4.8 4.8 0 0 1 0 9.6H8" }],
  ["path", { d: "M11.6 4.2L8.6 7l3 2.8" }],
]);
const RETRY_ICON = () => icon(16, 1.6, [
  ["path", { d: "M20 12a8 8 0 1 1-2.5-5.8" }],
  ["path", { d: "M20 6v5h-5" }],
]);
// A stroked outline, the same construction as every icon above -- no fill,
// round caps and joins from `icon()` itself (review item 15, 2026-09-26).
// Replaces a literal "x" glyph that read as a stray character rather than a
// control.
const DELETE_ICON = () => icon(16, 1.6, [
  ["path", { d: "M4.6 7h14.8" }],
  ["path", { d: "M9.4 7V4.8A1.3 1.3 0 0 1 10.7 3.5h2.6A1.3 1.3 0 0 1 14.6 4.8V7" }],
  ["path", { d: "M6.4 7l0.9 12.2a2 2 0 0 0 2 1.8h5.4a2 2 0 0 0 2-1.8L17.6 7" }],
  ["path", { d: "M10.2 10.8v7" }],
  ["path", { d: "M13.8 10.8v7" }],
]);
// The tree's caret, pointing RIGHT when shut; CSS turns it down when open.
const CARET_ICON = () => icon(12, 2, [["path", { d: "M9 5.5l6.5 6.5L9 18.5" }]]);
// The mark of a chat a SCHEDULED RUN opened (docs/automations.md AU-8, the
// approved mock's clock glyph).
const PHONE_ICON = () => icon(14, 1.6, [
  ["rect", { x: 7, y: 3.5, width: 10, height: 17, rx: 2.4 }], ["path", { d: "M11 17.5h2" }]]);
const LOCK_ICON = () => icon(16, 1.6, [
  ["rect", { x: 5.5, y: 10.5, width: 13, height: 9.5, rx: 2 }],
  ["path", { d: "M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" }]]);
const CLOCK_ICON = () => icon(14, 1.6, [["circle", { cx: 12, cy: 12, r: 8 }],
                                        ["path", { d: "M12 8v4.3l2.8 1.7" }]]);
const EDIT_ICON = () => icon(20, 1.8, [["path", { d: "M12 20h9" }],
  ["path", { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" }]]);
const TERMINAL_ICON = () => icon(20, 1.8, [["path", { d: "m5 7 5 5-5 5M12 17h7" }]]);
// A menu row's words: a span, so a transient word ("opening...") replaces only
// the text and never the icon beside it.
function menuWord(text) {
  const w = document.createElement("span");
  w.textContent = text;
  return w;
}
const PLUS_ICON = () => icon(14, 1.8, [["path", { d: "M12 5v14" }],
                                       ["path", { d: "M5 12h14" }]]);

// --------------------------------------------------------------------------- //
// REWIND (AC-194..AC-196, docs/rewind.md)
//
// Go back to something you said, drop it and everything after it, and get the
// text back in the composer to say differently. It OVERTURNS AC-97's "a
// correction is a new turn, never an edit" -- the owner's ruling -- and what
// survives of that is the part that mattered: the whole original is COPIED
// into an ordinary conversation in the sidebar before anything is truncated,
// so nothing the owner said is destroyed and undo is opening the copy.
//
// The button is on the OWNER's messages only: an assistant message is not a
// point a conversation can be continued FROM. It carries `data-msg` -- the
// id out of Butler's own record -- and CSS hides it while it has none and
// while a turn is in flight, so neither rule needs repainting.
// --------------------------------------------------------------------------- //
function rewindButton() {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copybtn rewindbtn";
  btn.setAttribute("aria-label", "Rewind to this message");
  btn.dataset.help = "Rewind";
  btn.append(REWIND_ICON());
  btn.onclick = () => { if (btn.dataset.msg) rewindTo(btn.dataset.msg); };
  return btn;
}

// The ids come off the DAEMON's record and are paired onto the rendered turns
// from the END of both lists (AC-196). The page renders a live turn as it
// happens, before the daemon has appended it -- `brain_harness` records the
// utterance inside `turn()`, after the `final` frame has already gone out --
// so a just-spoken turn has no id until the next refresh, and carries no
// control rather than a control that guesses.
//
// From the END because both lists are bounded at different places: the record
// at `MESSAGES_SERVED` and the transcript at `HISTORY_MAX`. Their recent tails
// agree; their heads need not.
function attachMessageIds(messages) {
  const said = (messages || []).filter((m) => m && m.role === "user"
                                               && typeof m.id === "string");
  const items = [...histItems()];
  for (let i = items.length - 1, j = said.length - 1; i >= 0 && j >= 0; i--, j--) {
    const btn = items[i].querySelector(".rewindbtn");
    if (btn) btn.dataset.msg = said[j].id;
  }
}

let idRefreshInFlight = false;

// Asked for at the END of a turn, which is the first moment the daemon's
// record holds it. One bounded GET, not awaited by anything, and never worth
// an error banner: a missing id costs one turn's rewind control (D-1).
async function refreshLiveIds() {
  if (idRefreshInFlight) return;
  idRefreshInFlight = true;
  try {
    const r = await fetch("/api/session/current");
    if (!r.ok) return;
    const doc = await r.json();
    liveMessages = doc.messages || [];
    attachMessageIds(liveMessages);
  } catch { /* the transcript is still on screen; only the control is lost */ }
  finally { idRefreshInFlight = false; }
}

async function rewindTo(messageId) {
  clearErrors();
  try {
    const r = await fetch("/api/session/rewind", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: messageId }),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) {
      showError(doc.error || ("that rewind was refused (HTTP " + r.status + ")"));
      return;
    }
    // REPAINTED FROM THE ANSWER and never spliced here (AC-195): the route
    // returns the truncated transcript AND the new chat list, which is
    // `delete_chat`'s shape for `delete_chat`'s reason -- a page that edits
    // its own copy can disagree with disk and a page that repaints cannot.
    liveMessages = (doc.session && doc.session.messages) || [];
    clearHistory();
    openItem = null;
    $("histCount").textContent = "0 turns";
    renderMessages(liveMessages);
    // The answer carries the whole listing, which is what the tree draws.
    chats = Array.isArray(doc.chats) ? doc.chats : chats;
    paintChatList();
    // The prompt goes to the COMPOSER and is never sent (R3): the point of a
    // rewind is almost always to say it differently.
    $("input").value = doc.prompt || "";
    growInput();
    $("input").focus();
    paintSend();
    logNote("rewound -- edit and send, or open the copy in the sidebar to "
            + "get the dropped turns back");
  } catch (e) {
    showError("that rewind did not happen: " + (e.message || e));
  }
}

// A message can be copied, and the control is KEYBOARD REACHABLE (AC-169;
// icon-only since CU-8, named by `aria-label` and `data-help`).
// Not hover-only: on a tablet a hover-only control is no control, so it rests
// at low opacity, comes up on hover AND on focus-within, and is an ordinary
// button in the tab order.
//
// It says what happened on its own face rather than reporting a success it did
// not have (AC-7b). `navigator.clipboard` is undefined on plain http:// over a
// tailnet -- which is the normal case for a satellite reached by IP -- so the
// honest answer there is "no clipboard", not silence.
function copyButton(node, what) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copybtn";
  btn.setAttribute("aria-label", "Copy " + what);
  btn.dataset.help = "Copy";
  btn.append(COPY_ICON());
  btn.onclick = () => {
    const text = node.innerText || node.textContent || "";
    if (!text.trim()) { toast("Nothing to copy"); return; }
    if (!navigator.clipboard) { toast("No clipboard here"); return; }
    navigator.clipboard.writeText(text).then(() => toast("Copied"),
                                             () => toast("No clipboard here"));
  };
  return btn;
}

// --------------------------------------------------------------------------- //
// TRY AGAIN (mocks/q-familiar.html item 4, review P0)
//
// plan.md records retry as "re-ask", which means retyping the question by
// hand; both reference chat products have had one-click regenerate for
// years. This is NOT a new mechanism: it re-sends the PAIRED user message's
// own text through the EXISTING typed-turn path (`{"type":"typed", text}`,
// AC-162/AC-163) -- the same `sendTyped` a real Enter press reaches, so the
// daemon cannot tell this apart from the owner retyping the question. `said`
// is the same element `historyItem` built the question into, closed over
// rather than looked up, so a retry always sends the text that was actually
// paired with THIS answer even after the turn scrolls out of view.
//
// Mirrors the composer's own submit handler (AC-7b: a control that offers
// itself and then does the wrong thing while Butler is speaking is worse
// than one that is disabled): a typed message is a barge-in, so a retry
// pressed mid-reply drops the local playback queue first, the same way
// pressing Enter on a fresh draft would.
// --------------------------------------------------------------------------- //
function retryButton(said) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copybtn retrybtn";
  btn.setAttribute("aria-label", "Try again");
  btn.dataset.help = "Try again";
  btn.append(RETRY_ICON());
  btn.onclick = () => {
    const text = (said.textContent || "").trim();
    if (!text) return;
    if (state === THINKING || state === SPEAK) {
      stopPlayback();
      currentTurn = null;
      speakingTurn = null;
    }
    sendTyped(text);
  };
  return btn;
}

const SPEAK_ICON = () => icon(16, 1.5, [
  ["path", { d: "M4 9.4h3.2L12 5.4v13.2l-4.8-4H4z" }],
  ["path", { d: "M15.4 9.6a3.4 3.4 0 0 1 0 4.8" }],
  ["path", { d: "M17.8 7a6.8 6.8 0 0 1 0 10" }],
]);
const HALT_ICON = () => icon(16, 1.6, [
  ["rect", { x: 6.5, y: 6.5, width: 11, height: 11, rx: 2.2 }],
]);

// --------------------------------------------------------------------------- //
// Play this reply again (AC-183)
//
// The page keeps NO audio: `playChunk` posts samples into a worklet and never
// retains them. So this is RE-SYNTHESIS through `POST /api/tts/say`, which is
// the voice-preview route with the text supplied instead of a voice name.
//
// ONE AT A TIME, on the whole page: starting a second replay stops the first,
// exactly as the settings surface allows one audition. It works while `speak`
// is off -- that is the point of it -- and it changes no setting.
// --------------------------------------------------------------------------- //
let replay = null;          // { audio, url, btn } while one is playing

function stopReplay() {
  if (!replay) return;
  const was = replay;
  replay = null;
  try { was.audio.pause(); } catch { /* already stopped */ }
  URL.revokeObjectURL(was.url);
  was.btn.classList.remove("on");
  was.btn.replaceChildren(SPEAK_ICON());
  was.btn.setAttribute("aria-label", "Read this answer aloud");
  was.btn.dataset.help = "Read aloud";
}

function sayButton(node) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copybtn saybtn";
  btn.setAttribute("aria-label", "Read this answer aloud");
  btn.dataset.help = "Read aloud";
  btn.append(SPEAK_ICON());
  const say = (words) => toast(words);
  btn.onclick = async () => {
    if (replay && replay.btn === btn) { stopReplay(); return; }
    stopReplay();
    const text = (node.innerText || node.textContent || "").trim();
    if (!text) { say("Nothing to read aloud"); return; }
    btn.classList.add("on");
    btn.replaceChildren(HALT_ICON());
    btn.setAttribute("aria-label", "Stop reading this answer");
    btn.dataset.help = "Stop reading";
    // A token, so a replay stopped while its request is still in flight does
    // not start playing a moment later (D-6's rule, one layer down).
    const mine = {};
    replay = { audio: null, url: "", btn, token: mine };
    try {
      // Fetched rather than handed to the element as a `src`, for the voice
      // preview's reason: this route answers a JSON `error` like the rest of
      // the API, and an <audio> pointed at a 413 raises a MediaError carrying
      // no message at all (AC-7b).
      const r = await fetch("/api/tts/say", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        throw new Error((body && body.error) || ("HTTP " + r.status));
      }
      const blob = await r.blob();
      if (!replay || replay.token !== mine) return;   // stopped mid-flight
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      replay.audio = audio; replay.url = url;
      audio.onended = () => stopReplay();
      audio.onerror = () => { stopReplay(); say("Could not play that"); };
      // This is Butler's own voice out of the speaker while the microphone may
      // be LIVE, so it gets the earcon treatment: the mic is muted for its
      // length plus a tail, zeroed rather than dropped (D-15, AC-5b). Same
      // argument as the settings surface's voice audition, and a REPLY is
      // longer than a preview, so the wake detector is the thing at risk.
      const arm = () => suppressMic(
        (Number.isFinite(audio.duration) ? audio.duration * 1000 : 4000) + 320,
        "message replay");
      if (Number.isFinite(audio.duration)) arm();
      else audio.onloadedmetadata = arm;
      await audio.play();
    } catch (e) {
      if (replay && replay.token !== mine) return;
      stopReplay();
      say((e.message || String(e)).slice(0, 60));
    }
  };
  return btn;
}

// The conversation SCROLLS DOWN now, because that is what every assistant a
// new user has met does (AC-161). Turns are APPENDED and the trim drops the
// oldest from the FRONT -- the reverse of the hero page, where the newest turn
// sat at the top of a fold. Sticky rather than forced: a reader who has
// scrolled up to re-read an answer is not yanked back by the next sentence of
// the one arriving.
// The turns on screen, oldest first: `#history`'s children minus the
// working row, which is always the last child (AC-694).
function histItems() {
  return $("history").querySelectorAll(":scope > .histitem");
}
// Empty the conversation and keep the working row.
function clearHistory() {
  $("history").replaceChildren($("workRow"));
}

function nearBottom() {
  const el = $("scroller");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
}
function toBottom(smooth) {
  const el = $("scroller");
  el.scrollTo({ top: el.scrollHeight,
                behavior: (smooth && !matchMedia("(prefers-reduced-motion: reduce)").matches)
                  ? "smooth" : "auto" });
}
function stickBottom(wasNear) { if (wasNear) toBottom(true); }

function historyItem(atMs) {
  const stick = nearBottom();
  const item = document.createElement("div");
  item.className = "histitem pending";   // in flight until something answers

  // The question.
  const uturn = document.createElement("article");
  uturn.className = "turn user";
  const umeta = document.createElement("div"); umeta.className = "meta";
  const uwho = document.createElement("span");
  uwho.className = "turnwho"; uwho.textContent = "you";
  umeta.append(uwho);
  let when = null;
  if (typeof atMs === "number" && isFinite(atMs)) {
    const at = new Date(atMs);
    when = document.createElement("time");
    when.dateTime = at.toISOString();
    when.textContent = at.toLocaleTimeString();
    umeta.append(when);
  }
  const said = document.createElement("div"); said.className = "said";
  // The owner's messages only (AC-194). It is built ALWAYS and hidden by CSS
  // until `attachMessageIds` gives it a `data-msg`, so nothing has to repaint
  // when the ids arrive a turn later.
  // UNDER the bubble, not beside the timestamp in `.meta` (q-familiar's own
  // answer): labelled pills plus who/time in one `nowrap` row overflowed a
  // 390px turn. `.turn.user`'s own `align-items: flex-end` right-aligns this
  // row exactly the way it already right-aligns `.said`.
  const uacts = document.createElement("div"); uacts.className = "acts";
  uacts.append(rewindButton(), copyButton(said, "what you said"));
  uturn.append(umeta, said, uacts);

  // The answer. Butler's turns carry a MARK -- a shape, present on every one
  // of his and none of yours -- so the two roles are told apart without
  // reading and without hue.
  const bturn = document.createElement("article");
  bturn.className = "turn bot";
  const meta = document.createElement("div"); meta.className = "meta";
  const mark = document.createElement("span");
  mark.className = "mark";
  mark.setAttribute("aria-hidden", "true");
  mark.append(MARK_ICON());
  const bwho = document.createElement("span");
  bwho.className = "turnwho"; bwho.textContent = "butler";
  // Two SLOTS filled later by logReply. Slots and not a rebuild: logReply used
  // to replaceChildren() the whole meta row, which would now destroy the mark,
  // the role and the copy button along with the timestamp it was careful to
  // put back.
  const tier = document.createElement("span"); tier.className = "tiertag";
  const timing = document.createElement("span"); timing.className = "timing";
  const answered = document.createElement("div"); answered.className = "answered";
  // BUTLER'S TURNS ONLY (AC-183, owner's ruling): replaying your own question
  // is not a thing anyone wants, and it halves the surface. Retry is the
  // third action, between copy and speak (mocks/q-familiar.html item 4): it
  // needs `said` from the SAME turn, which is why it is built here rather
  // than passed in later.
  meta.append(mark, bwho, tier, timing);
  // UNDER the answer, for the same overflow reason `uacts` is (see above).
  const acts = document.createElement("div"); acts.className = "acts";
  acts.append(copyButton(answered, "Butler's answer"), retryButton(said),
              sayButton(answered));
  bturn.append(meta, answered, acts);

  item.append(uturn, bturn);
  // ABOVE the working row, which stays the conversation's last item (AC-694).
  $("workRow").before(item);
  while (histItems().length > HISTORY_MAX) histItems()[0].remove();
  setEmptyState(false);
  $("histCount").textContent = histItems().length + " turns";
  stickBottom(stick);
  return { item, said, answered, meta, when, tier, timing };
}

// A turn stops being in flight. Called from the one exit every terminal path
// lands in (goListen) and from the start of the next turn, so an item cannot
// keep the `pending` look after the turn behind it is over.
//
// An item that never received a word gets a plain sentence saying so (AC-94):
// one restored turn showed a question with a completely empty answer and
// nothing saying why, which is indistinguishable from a turn still running.
// It says only what is known -- that nothing came back -- and never guesses a
// REASON, because the page was not told one.
// A turn the owner STOPPED says so under what it had shown (AC-394). The
// note sits AFTER `.answered`, never inside it: `paintAnswer` rebuilds that
// element whole, and an `unspoken` frame landing after a stop does exactly
// that.
function markStopped(it) {
  if (!it || it.item.classList.contains("stopped")) return;
  it.item.classList.add("stopped");
  const note = document.createElement("div");
  note.className = "stopnote";
  note.textContent = "Stopped.";
  it.answered.after(note);
}

// A FINISHED AGENT SAYS SO (AC-695, CU-12): "pi finished in butler, 2:31.
// You chose: Only test_turn.py", on the turn's own item between the question
// and the reply. The time is the DAEMON's (`agent_elapsed_ms` live,
// `agentElapsedMs` from the record), never this page's clock; "You chose" is
// the option this page sent (`chooseAnswer`), so a reload omits it. Built
// once per item and rewritten in place: every later beat repeats it.
const CHECK_ICON = () => icon(16, 2, [["path", { d: "m5 12.5 4.5 4.5L19 7.5" }]]);
function paintFinished(it, agent, ms) {
  if (!it || !it.item) return;
  let row = it.item.querySelector(":scope > .finrow");
  if (!row) {
    row = document.createElement("div");
    row.className = "finrow";
    it.item.insertBefore(row, it.item.querySelector(":scope > .turn.bot"));
  }
  const proj = $("chatProject").textContent;
  const txt = document.createElement("span");
  txt.className = "txt";
  txt.textContent = agent + (proj ? " finished in " + proj : " finished") + ", "
    + elapsedLabel(Number(ms) || 0) + ".";
  if (it.chose) {
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = " You chose: " + it.chose;
    txt.append(sub);
  }
  row.replaceChildren(CHECK_ICON(), txt);
}

// WHAT A TURN SHOWED (docs/rich-chat.md RC-8, AC-633, AC-634): files and
// layouts drawn by `web/artifacts.js` in a `.arts` box placed AFTER
// `.answered` -- `markStopped`'s rule, because `paintAnswer` rebuilds that
// element whole on every streamed piece and anything inside it would be
// erased. The owner's own attachments go under their message, after `.said`
// (O-2). Found by the DOM rather than a field on the item, so a late frame
// can reach the newest drawn item however it was built.
function artifactBox(it, role) {
  const anchor = role === "user" ? it.said : it.answered;
  let box = anchor.parentNode.querySelector(":scope > .arts");
  if (!box) {
    box = document.createElement("div");
    box.className = "arts";
    anchor.after(box);
  }
  return box;
}

function drawArtifacts(it, role, list, chat) {
  if (!it || !Array.isArray(list) || !list.length || !chat) return;
  const stick = nearBottom();
  const box = artifactBox(it, role);
  for (const entry of list) {
    if (!entry || typeof entry !== "object") continue;
    box.append(renderChatArtifact(chat, entry, {
      markdown: (text, into) => mdRender(text, into),
      grew: () => stickBottom(stick),
    }));
  }
  stickBottom(stick);
}

// THE `artifact` FRAME (docs/rich-chat.md section 11): broadcast, naming a
// chat and a role but no turn, so it is routed here rather than through
// `trackTurnFrame`/`stale()`. Off screen it is ignored -- the record holds it
// and `loadSession` draws it. On screen it joins the item in flight; one that
// lands before that item exists (the owner's attachment can precede the
// `final` that draws their message) is HELD and drawn when the item appears;
// one that lands after its turn closed belongs to the newest item drawn.
let heldArtifacts = [];

function screenTurnInFlight() {
  return currentTurn != null || !!watchedChat || typedPending > 0
    || state === CAPTURE || state === THINKING || state === SPEAK;
}

function newestItem() {
  const all = histItems();
  const el = all[all.length - 1];
  if (!el) return null;
  const said = el.querySelector(".said"), answered = el.querySelector(".answered");
  return said && answered ? { item: el, said, answered } : null;
}

function applyArtifactFrame(m) {
  if (!m.chat || m.chat !== liveChatId || !m.artifact) return;
  const role = m.role === "user" ? "user" : "assistant";
  if (openItem) { drawArtifacts(openItem, role, [m.artifact], m.chat); return; }
  if (screenTurnInFlight()) { heldArtifacts.push(m); return; }
  drawArtifacts(newestItem(), role, [m.artifact], m.chat);
}

function drawHeldArtifacts() {
  if (!openItem || !heldArtifacts.length) return;
  const held = heldArtifacts;
  heldArtifacts = [];
  for (const h of held) {
    if (h.chat !== liveChatId) continue;
    drawArtifacts(openItem, h.role === "user" ? "user" : "assistant",
                  [h.artifact], h.chat);
  }
}

function closeItem() {
  if (!openItem) return;
  openItem.item.classList.remove("pending");
  // A turn that only SHOWED something did answer (RC-5: text never depends
  // on an artifact, but an artifact alone is still a reply).
  if (!openItem.answered.textContent.trim()
      && !openItem.answered.parentNode.querySelector(":scope > .arts")
      && !openItem.item.classList.contains("stopped")) {
    openItem.answered.append("no reply came back for this turn.");
  }
  openItem = null;
}

// Where a turn came from, when it was not this room: a phone glyph and a
// small word in the actions row under the owner's bubble (AC-519, the
// export's place). Information, not a control, so it never rests dimmed
// with the buttons beside it (CU-17). Text only, through textContent.
// A PHONE CHANNEL's turn (docs/messaging.md MS-16, 8.1): the daemon's
// `hub.PHONE_REASONS`, each with the words its transcript mark shows.
const PHONE_VIA = {whatsapp: "via WhatsApp", telegram: "via Telegram"};

function markVia(it, words) {
  if (!it) return;
  const acts = it.said.parentNode.querySelector(".acts");
  if (!acts) return;
  const via = document.createElement("span");
  via.className = "via";
  via.textContent = words;
  via.prepend(PHONE_ICON());
  acts.append(via);
}

function logUser(text, atMs) {
  closeItem();
  openItem = historyItem(typeof atMs === "number" ? atMs : Date.now());
  openItem.said.textContent = text;
}
// A REPLY IS RE-RENDERED WHOLE ON EVERY PIECE, and that is the fix for the
// thing the owner called "just a blob of text" (2026-09-10).
//
// The daemon emits one `reply` per SENTENCE so the first can start playing
// while the rest is still being generated, and that streaming is not
// negotiable -- it took first audio from 3925 ms to 673 ms. What was wrong is
// that each piece used to be rendered INDEPENDENTLY into its own `span`, so no
// block could ever span a sentence boundary. MEASURED on one real agent reply:
// a three-item bulleted list came out as THREE `ul` elements of one `li` each,
// one fenced code block came out as two `pre` (one of them empty), and the
// markdown table fell inside the wrong fence.
//
// So the raw markdown accumulates on the turn and the whole answer is parsed
// again each time a piece lands. That is what ChatGPT and Claude do, at token
// granularity; at SENTENCE granularity it is a few dozen re-parses of a few KB
// per reply, which is nothing.
//
// The cost, stated rather than discovered: re-rendering DESTROYS a text
// selection made inside the answer while it is still streaming. The
// alternative is an incremental block parser holding state across pieces,
// which is a great deal more code to get wrong for a window of a few seconds.
//
// Pieces are joined with a SPACE only when neither side already brings
// whitespace. The daemon's splitter carries structural newlines on the piece,
// so inserting a space in front of one would put a stray space at the start of
// a heading or a list item.
function logReply(text, tier, spoken) {
  const stick = nearBottom();
  if (!openItem) openItem = historyItem(Date.now());
  if (typeof openItem.raw !== "string") openItem.raw = "";
  // Where the SHOWN-NOT-SPOKEN tail begins, as an offset into `raw`, or -1
  // (AC-40). It used to be a class on the piece's own span; with one buffer it
  // has to be a position, because the spans are rebuilt on every render.
  if (typeof openItem.unspokenFrom !== "number") openItem.unspokenFrom = -1;
  // Where each reply FRAME's text starts in `raw`, one entry per call, empty
  // pieces included -- the daemon counts its `reply` frames per turn the same
  // way, so `{type: "unspoken", from: n}` names `starts[n]` (applyUnspoken).
  if (!Array.isArray(openItem.starts)) openItem.starts = [];

  const piece = String(text == null ? "" : text);
  if (!piece) openItem.starts.push(openItem.raw.length);
  if (piece) {
    if (openItem.raw && mdStartsBlock(piece)
        && !/\n\s*$/.test(openItem.raw)) {
      // A block must open on its own line, whatever the previous piece ended
      // with. This is the join that a space gets wrong.
      openItem.raw += "\n\n";
    } else if (openItem.raw && !/\s$/.test(openItem.raw)
               && !/^\s/.test(piece)) {
      openItem.raw += " ";
    }
    if (spoken === false && openItem.unspokenFrom < 0) {
      openItem.unspokenFrom = openItem.raw.length;
    }
    openItem.starts.push(openItem.raw.length);
    openItem.raw += piece;
  }

  paintAnswer(openItem);

  // Something answered, so the turn is no longer in flight (AC-94).
  if (openItem.raw) openItem.item.classList.remove("pending");
  if (tier) {
    // Into the SLOTS the row was built with. This used to replaceChildren()
    // the whole meta element and put the <time> back by hand; the row now also
    // carries Butler's mark, his role and a copy button, and rebuilding would
    // silently take all three out on the first reply of every turn.
    openItem.tier.textContent = tier;
    openItem.timing.textContent = lastTimings;
    $("tierPill").hidden = false;
    $("tierLabel").textContent = tier;
  }
  // The live region gets the RAW markdown rather than the rendered text: a
  // screen reader should hear "minus sign" no more than it should hear the
  // pipes of a table, and `innerText` of a rendered table is worse than the
  // source. It is the same string the room heard.
  $("reply").hidden = !openItem.raw;
  $("reply").textContent = openItem.raw;
  stickBottom(stick);
}

// Draw an item's answer: the heard part plainly, the shown-not-heard tail
// (from `unspokenFrom`) inside `.unspoken`. Its own function because the tail
// can now move AFTER the text was drawn: text is shown the moment the model
// produces it and audio follows, so a stop or a barge-in can leave sentences
// on screen that were never spoken, and the daemon then says from which frame
// (docs/filing-and-pacing.md item 2, AC-286).
function paintAnswer(it) {
  it.answered.replaceChildren();
  if (it.unspokenFrom >= 0) {
    mdRender(it.raw.slice(0, it.unspokenFrom), it.answered);
    // Sent to be READ, not heard (AC-40): the tail of a reply cut off at the
    // token cap, muted by a look-away, or stopped before its audio played.
    // Speaking half a sentence sounds broken and dropping it would hide what
    // the model produced, so it is shown and marked.
    const tail = document.createElement("span");
    tail.className = "unspoken";
    mdRender(it.raw.slice(it.unspokenFrom), tail);
    it.answered.append(tail);
  } else {
    mdRender(it.raw, it.answered);
  }
}

// THE DAEMON'S CORRECTION: from reply frame `from` on, this turn's text was
// shown and never heard (`{type: "unspoken", turn, chat, from}`). It names a
// turn that may already be over on this page -- a stop closes the item before
// the frame arrives -- so it is applied by TURN, to the item that turn drew,
// and never through `stale()`. The parked copy of an off-screen turn is
// marked too, so opening that conversation draws the same line.
const turnItems = new Map();
const TURN_ITEMS_KEPT = 16;
function rememberTurnItem(turn, it) {
  if (turn == null || !it) return;
  turnItems.delete(turn);
  turnItems.set(turn, it);
  while (turnItems.size > TURN_ITEMS_KEPT) {
    turnItems.delete(turnItems.keys().next().value);
  }
}
function applyUnspoken(m) {
  const from = Number(m.from);
  if (!Number.isInteger(from) || from < 0) return;
  const t = m.chat ? chatTurns.get(m.chat) : null;
  if (t && t.turn === m.turn) {
    for (let i = from; i < t.pieces.length; i++) t.pieces[i][2] = false;
  }
  const it = turnItems.get(m.turn);
  if (!it || !Array.isArray(it.starts) || from >= it.starts.length) return;
  const at = it.starts[from];
  if (it.unspokenFrom < 0 || at < it.unspokenFrom) {
    it.unspokenFrom = at;
    paintAnswer(it);
  }
}

// The harness CONVERSATION this page joined (AC-35, rewritten 2026-08-18).
// The record is now ONE flat chat file: `ready` carries `{session, turns}` and
// `/api/session/current` carries `{id, harness, messages}`.
//
// The line that used to sit above the transcript -- new / resumed / the
// agent's own context is gone -- is DELETED with the store that answered it.
// The page can no longer say whether the coding agent still remembers this
// conversation, and that is a real loss recorded in docs/harness-mode.md 10.5
// rather than replaced by a guess: a line claiming "resumed" that the daemon
// cannot back would be worse than no line at all.
async function loadSession(summary) {
  if (!summary) { $("sessionLine").hidden = true; return; }
  const moved = String(summary.session || "") !== liveChatId;
  setLiveChat(summary.session);
  const line = $("sessionLine");
  line.hidden = false;
  line.textContent =
    `session ${String(summary.session).slice(0, 22)} -- ` +
    `${summary.turns} turn${summary.turns === 1 ? "" : "s"}`;
  let doc;
  try {
    const r = await fetch("/api/session/current");
    if (!r.ok) {                       // endpoint mode: no conversation at all
      paintChatAgent(null);
      return;
    }
    doc = await r.json();
  } catch { return; }                  // a transcript is never worth an error
  // This runs on EVERY `ready`, and the socket reconnects by itself, so it
  // runs again after every drop: appending would show the whole conversation
  // twice, then three times. The record is the daemon's and includes the
  // turns this page logged live, so replacing is safe -- and it is done only
  // once the fetch has actually answered, because clearing a history nothing
  // is going to replace loses turns the owner can still see.
  clearHistory();
  openItem = null;
  heldArtifacts = [];                  // the record below holds them
  setEmptyState(true);
  $("histCount").textContent = "0 turns";
  // THE CONVERSATION'S OWN NAME, from the daemon and by the SAME rule the list
  // row uses (`ChatStore.title`). This line used to be the placeholder
  // unconditionally, so a reload put a real transcript on screen under the
  // words "current conversation" while the row beside it named it (measured
  // on the scratch surface, 2026-09-15). Empty means nobody has spoken yet,
  // and the placeholder is then the honest thing to draw.
  $("chatTitle").textContent = doc.title || "current conversation";
  // Which workspace this conversation belongs to (the owner's backlog item 4).
  // The DAEMON's answer, read off the record rather than remembered from the
  // last click, so a switch made on the phone shows here too (D-2).
  // THE ID, BECAUSE A PATH IS A SPELLING. The daemon annotates every
  // conversation it serves -- live or past -- with the ROW its directory
  // belongs to, resolved through `shortcuts.same_dir`. Comparing the path
  // instead meant that re-spelling a row with a trailing separator, a
  // different case or the other separator left the daemon still filing
  // conversations under it while this page said the owner was somewhere else:
  // the tile lost its "you are here" mark (review, 2026-09-11).
  chatProjectId = String(doc.projectId || "");
  chatHarness = String(doc.harness || "");
  paintChatProject(doc);
  paintChatAgent(doc);
  paintGuestLock(doc);
  // A conversation that just came on screen is never hidden inside a shut
  // project of its own sidebar (D-D2). Only when it CHANGED: this runs on
  // every reconnect, and re-opening a project the owner had just shut, on a
  // socket blip, would be the page overruling them.
  if (moved) ensureExpanded(chatProjectId);
  // WHAT THE CODING AGENT IS BLOCKED ON, re-read rather than remembered
  // (AC-249). This runs on every `ready`, so it is also what happens after a
  // reload and after every socket drop: the question is DAEMON STATE, and a
  // page that kept its own copy across a reconnect would draw a card for a
  // child that died while it was away.
  applyQuestions(doc.questions);
  if (shortcuts) paintShortcuts(shortcuts);
  paintChatList();
  // Oldest first and each one APPENDED, so the newest sits at the BOTTOM where
  // a live turn arrives -- the reverse of the hero page, whose transcript grew
  // upward inside a fold. `historyItem`'s trim keeps the newest HISTORY_MAX of
  // them: it drops `firstChild`, which is now the oldest end of the list.
  // `liveMessages` is KEPT because the rewind and the delete repaint from it
  // without a second round trip. It used to also be what `backToLive()`
  // restored synchronously when a wake word fired over an old conversation;
  // nothing restores anything now, because the conversation on screen is the
  // one the daemon is holding (AC-240).
  liveMessages = doc.messages || [];
  renderMessages(liveMessages);
  loadChats();                         // the list's turn counts moved with it
}

// `timestamp` is the daemon's own, minted when the message was appended
// (daemon/chats.py). Passed through so a restored turn is stamped with when it
// HAPPENED and not with when this page reconnected (AC-94).
// THE REWIND CONTROLS GO ON EVERYTHING THIS DRAWS. It took a `withIds` flag
// until 2026-09-12 and a PAST conversation was drawn without them, because
// only the live one could be rewound; since the registry landed, every
// conversation this page draws is one the daemon is holding -- opening one
// makes it the conversation a turn goes to (AC-240) -- so all four callers
// passed true and the argument only made it look as though one of them might
// not.
function renderMessages(messages) {
  for (const m of messages) {
    if (m.role === "user") {
      logUser(m.content || "", m.timestamp);
      // O-2: the owner's own attachments, under their message (AC-634).
      drawArtifacts(openItem, "user", m.artifacts, liveChatId);
    } else if (m.role === "assistant" && !m.content
               && Array.isArray(m.artifacts) && m.artifacts.length) {
      // A turn that showed something and said nothing.
      if (!openItem) openItem = historyItem(m.timestamp);
      drawArtifacts(openItem, "assistant", m.artifacts, liveChatId);
      if (m.stopped) markStopped(openItem);
    } else if (m.role === "assistant" && !m.content && m.stopped) {
      markStopped(openItem);          // stopped before it showed anything
    } else if (m.role === "assistant" && m.content) {
      // `unspokenFrom`: where the heard part of this answer ended, written
      // by the daemon when a stop or a barge-in left shown text unspoken, so
      // a reload draws the same `.unspoken` tail the live page did
      // (docs/filing-and-pacing.md item 2).
      const cut = m.unspokenFrom;
      if (Number.isInteger(cut) && cut >= 0 && cut < m.content.length) {
        if (cut > 0) logReply(m.content.slice(0, cut), "", true);
        logReply(m.content.slice(cut), "", false);
      } else {
        logReply(m.content, "", true);
      }
      // What this answer SHOWED, after `.answered` (AC-633, AC-634).
      drawArtifacts(openItem, "assistant", m.artifacts, liveChatId);
      if (m.stopped) markStopped(openItem);
      // WHICH AGENT FINISHED IT, from the record (AC-695, Step 1): the
      // daemon's own clock, frozen at the agent's end.
      if (m.agent && Number.isFinite(m.agentElapsedMs)) {
        paintFinished(openItem, String(m.agent), m.agentElapsedMs);
      }
    }
  }
  // The LAST restored turn is closed only if this conversation has nothing
  // in flight: a question whose answer is still being worked on is pending,
  // not "no reply came back" (S16). `settleScreenTurn` decides, per chat.
  settleScreenTurn();
  attachMessageIds(messages);
  setEmptyState(histItems().length === 0);
  toBottom(false);
}

// THE CHAT YOU CLICKED IS THE CHAT YOU TALK TO, so there is nothing to go
// "back" from (AC-240, docs/parallel-agents.md phase 4). `backToLive()` stood
// here until 2026-09-12: opening a conversation only changed what this page
// DREW, so every path that started a turn had to restore the live record
// first, and sending abandoned the conversation the owner had just clicked.
// `openChat` now POSTs `/api/session/open` and the daemon moves the
// conversation a turn goes to, which is why `viewingChat` is gone with it --
// there is one conversation on screen and it is the live one.
let liveMessages = [];

// --------------------------------------------------------------------------- //
// PAST CONVERSATIONS (AC-164, docs/familiar-ui.md step 1)
//
// `daemon/chats.py` has written one flat JSON file per conversation since
// AC-35, and until `GET /api/chats` existed `GET /api/session/current` was the
// only route to any of them -- so every conversation but the live one was
// unreachable from the page that wrote it. The sidebar lists what is on disk,
// newest first, and opening one shows it.
//
// The list is the DAEMON's: id, title, turn count and `updatedAt`, and the
// title is the first thing the owner said, truncated by the daemon. Nothing
// here invents a name for a conversation.
// --------------------------------------------------------------------------- //
let chats = [];
// WHICH PROJECTS THE TREE SHOWS OPEN, by row id (D-D2, AC-291). The DAEMON's
// -- `expanded_projects` in the shortcuts store, beside `sidebar_collapsed` --
// because AC-8 bans browser storage and a phone and a desk must agree about
// one tree. Replaced only from `paintShortcuts`, i.e. from the daemon's own
// answer; `toggleProject` paints ahead of that answer and is then corrected
// by it.
let expandedProjects = new Set();

function chatAgo(atMs) {
  const s = Math.max(0, (Date.now() - atMs) / 1000);
  if (s < 90) return "now";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 604800) return Math.round(s / 86400) + "d ago";
  return Math.round(s / 604800) + "w ago";
}

// WHEN A ROW LAST MOVED, the export's way: the clock time today, "Yesterday",
// the weekday this week, else the date. The browser's own locale formats it.
function chatWhen(atMs) {
  const d = new Date(atMs);
  const now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(now) - day(d)) / 86400000);
  if (days <= 0) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function paintChatList() {
  // A chat status change is a burger-dot change too (AC-296): this runs on
  // every rebuild -- a delete, a new conversation, a fresh listing.
  paintBurger();
  paintAskWho();
  const list = $("chatList");
  list.replaceChildren();
  // THE TREE, OR A SEARCH'S RESULTS (docs/filing-and-pacing.md 8, R-D). A
  // query is GLOBAL over every project and draws a flat list whose rows name
  // their project, because there the project IS the information; no query
  // draws the tree. Painted from HERE on every rebuild -- a delete, a new
  // conversation, a fresh listing, a shortcuts answer -- so the "nothing
  // matched" line can never survive a rebuild that made it false (AC-188).
  $("chatsHead").textContent = chatQuery ? "Results" : "Projects";
  $("chatsEmpty").hidden = !(chatQuery && !chats.length);
  $("chatsEmpty").textContent = "nothing matched";
  if (chatQuery) {
    for (const c of chats) list.append(buildChatRow(c, false));
    return;
  }
  // GROUPING, NEVER FILTERING (D-D1). Every served row is drawn under exactly
  // one heading: its project's row, or -- for a conversation whose project
  // row the owner REMOVED, which the daemon serves with an empty `projectId`
  // (AC-225) -- its directory's own name, struck through, after every live
  // project. A row that is not drawn looks exactly like a conversation that
  // does not exist, which is AC-229's argument and still binds.
  const inProject = new Map();
  const orphans = new Map();
  // A projectId NO ROW HAS is an orphan too (e2e audit S9c): the listing
  // was served before a project was removed, and grouping it under a row
  // that is not drawn made its conversations vanish until a reload.
  const rowIds = new Set((shortcuts.rows || []).map((r) => r.id));
  for (const c of chats) {
    if (c.projectId && rowIds.has(c.projectId)) {
      if (!inProject.has(c.projectId)) inProject.set(c.projectId, []);
      inProject.get(c.projectId).push(c);
    } else {
      const key = c.project || "";
      if (!orphans.has(key)) orphans.set(key, []);
      orphans.get(key).push(c);
    }
  }
  for (const row of (shortcuts.rows || [])) {
    list.append(projectNode(row, inProject.get(row.id) || []));
  }
  for (const [path, rows] of orphans) list.append(orphanNode(path, rows));
}

// The loudest thing any conversation in a project is doing, for the dot a
// project line and a strip chip carry (the owner's "rolled up to its project
// row"). `row.busy` is the daemon's own flag for a busy child holding the
// project's slot (`_annotate_busy`); the conversations' `status` words are
// the daemon's too (AC-269), so nothing here invents a state.
function projectStatus(row, rows) {
  if (rows.some((c) => c.status === "waiting" || c.status === "error")) {
    return rows.some((c) => c.status === "waiting") ? "waiting" : "error";
  }
  if ((row && row.busy) || rows.some((c) => c.status === "running")) {
    return "running";
  }
  return "";
}

// ONE PROJECT: a quiet line with a caret, and -- while it is open -- its
// conversations and its own "+ New chat". Clicking the LINE only opens or
// shuts it (R1 amended: "browsing moves nothing"); the "..." beside it holds
// everything a project could ever do, unchanged from the rail it replaced.
function projectNode(row, rows) {
  const open = expandedProjects.has(row.id);
  const node = document.createElement("div");
  node.className = "pnode";
  node.dataset.projectId = row.id;
  const head = document.createElement("div");
  head.className = "phead";
  if (chatProjectId && row.id === chatProjectId) head.dataset.here = "true";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "ptoggle";
  toggle.setAttribute("aria-expanded", String(open));
  toggle.append(CARET_ICON());
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = projectName(row);
  toggle.append(nm);
  const status = projectStatus(row, rows);
  if (status) {
    const dot = document.createElement("span");
    dot.className = "pdot";
    dot.dataset.status = status;
    dot.setAttribute("aria-hidden", "true");
    toggle.append(dot);
  }
  // A scheduled run's chat nobody has opened yet (AC-469), rolled up so a
  // SHUT project still says so. Named in the line's label below.
  const unread = rows.filter((c) => c.unread).length;
  if (unread) {
    const u = document.createElement("span");
    u.className = "udot";
    u.setAttribute("aria-hidden", "true");
    toggle.append(u);
  }
  // The count only while SHUT: open, the rows are their own count.
  if (!open && rows.length) {
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = String(rows.length);
    toggle.append(n);
  }
  toggle.setAttribute("aria-label", projectName(row)
    + (status ? ", an agent is " + status : "")
    + (unread ? ", " + unread + " unread automation run"
                + (unread === 1 ? "" : "s") : "")
    + ", " + rows.length + " conversation" + (rows.length === 1 ? "" : "s"));
  toggle.onclick = () => toggleProject(row.id);
  head.append(toggle, projectMore(row));
  node.append(head);
  if (open) {
    const kids = document.createElement("div");
    kids.className = "pkids";
    kids.setAttribute("role", "group");
    kids.setAttribute("aria-label", projectName(row) + " conversations");
    for (const c of rows) kids.append(buildChatRow(c, true));
    kids.append(newChatIn(row));
    kids.append(...terminals.rowsFor(row));
    node.append(kids);
  }
  return node;
}

// A REMOVED project's conversations (AC-225): still listed and openable,
// under the directory's own name struck through, with no "..." and no
// "+ New chat" -- there is no row left to launch in or to be born in.
function orphanNode(path, rows) {
  const node = document.createElement("div");
  node.className = "pnode gone";
  const head = document.createElement("div");
  head.className = "phead";
  const label = document.createElement("div");
  label.className = "ptoggle";
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = path ? projectName({ path }) : "No project";
  label.append(nm);
  if (path) {
    label.dataset.help = path + " -- this project was removed. Its "
      + "conversations are still here and still open.";
  }
  head.append(label);
  const kids = document.createElement("div");
  kids.className = "pkids";
  for (const c of rows) kids.append(buildChatRow(c, true));
  node.append(head, kids);
  return node;
}

// "+ New chat" INSIDE a project: born there, in the ONE call that creates it
// (AC-287), behind the busy-project confirm (AC-274) exactly as before.
function newChatIn(row) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pnew";
  btn.append(PLUS_ICON());
  const label = document.createElement("span");
  label.textContent = "New chat";
  btn.append(label);
  btn.setAttribute("aria-label",
                   "Start a new conversation in " + projectName(row));
  btn.onclick = gatedNewChat(() => row.id,
                             (id) => newChatFrom(btn, label, id));
  // The chevron beside it (option C): the same birth on a chosen agent.
  const wrap = document.createElement("div");
  wrap.className = "pnewsplit";
  const chev = agentChevron("Start a new conversation in " + projectName(row)
                            + " on a chosen agent");
  wireStartMenu(chev, () => row.id, btn, label);
  wrap.append(btn, chev);
  return wrap;
}

// The project's "..." -- the rail tile's own control, moved onto the tree
// line with the popover it opens unchanged (claude / pi / opencode, edit,
// remove, the chord slots).
function projectMore(row) {
  const more = document.createElement("button");
  more.type = "button";
  more.className = "tilemore";
  more.setAttribute("aria-haspopup", "true");
  more.setAttribute("aria-expanded", String(projPopFor === row.id));
  more.setAttribute("aria-controls", "projPop");
  more.setAttribute("aria-label", projectName(row) + ", more");
  more.setAttribute("data-help", "More options");
  // Three dots drawn as zero-length ROUND-CAPPED strokes rather than as
  // circles: `icon` sets `fill: none`, so a `circle` would come out as three
  // rings at this size.
  more.append(icon(14, 2.6, [["path", { d: "M6 12h0.01" }],
                             ["path", { d: "M12 12h0.01" }],
                             ["path", { d: "M18 12h0.01" }]]));
  more.onclick = () => openProjPop(row.id, more);
  return more;
}

// ONE ROW BUILDER, for the tree AND the search's results (D-18): two
// renderers for one row shape is how a third one drifts. Returns the
// `.chatline` -- the open button plus its sibling delete control (AC-172, a
// button may not nest inside a button).
//
// IN THE TREE A ROW IS ONE LINE (the export's `.chat`): the title, and at the
// right ONE quiet fact -- when it last moved ("14:59", "Yesterday"), a clock
// for an automation run, or, for a LOUD row (the daemon says running, waiting
// or error), its state: a spinner and the agent's clock, a "Needs you" chip,
// an "Error" chip. A loud row also keeps its last tool action under it.
// Search results keep the project and the age under the title, because
// across projects those ARE the information.
function buildChatRow(c, inTree) {
    const at = Number(c.updatedAt) || 0;
    // A LINE, not a row. `.chatrow` is a <button> and a button may not nest
    // inside one, so the delete control is a SIBLING and `.chatrow` keeps
    // every selector it already had -- which is what lets probe_page.py go on
    // reading `#chatList .chatrow` unchanged (AC-172).
    const line = document.createElement("div");
    line.className = "chatline";
    const row = document.createElement("button");
    row.type = "button";
    row.className = "chatrow";
    if (!inTree) row.classList.add("hit");
    // The open conversation is marked with `aria-current` and a tinted fill,
    // never with colour alone. ONE mark, because there is one conversation on
    // screen and it is the one the daemon is holding (AC-240).
    const isOpen = c.id === liveChatId;
    if (isOpen) row.setAttribute("aria-current", "true");
    const t = document.createElement("span");
    t.className = "t";
    // The daemon sends "" for a conversation nobody has spoken into and says
    // in `chats._title` exactly why: "New conversation" is UI COPY and the
    // page owns its own words. The one you are IN reads as new. Any OTHER
    // titleless row is an ASSISTANT-ONLY record (Butler spoke, the owner
    // never did), which is why it says who DID speak.
    t.textContent = c.title
      || (isOpen ? "New conversation" : "(Butler only)");
    // A SCHEDULED RUN'S CHAT (AC-469, AU-8): a clock glyph at the right, and
    // until it is opened on ANY device a dot before the title and the title
    // in bold -- both named in the accessible label, never colour alone
    // (AC-174). The daemon says which (`origin`, `unread`); nothing here is
    // remembered in the browser (AC-8).
    const isRun = !!(c.origin && c.origin.kind === "automation");
    if (isRun) {
      const tl = document.createElement("span");
      tl.className = "tline";
      if (c.unread) {
        row.classList.add("unread");
        const u = document.createElement("span");
        u.className = "udot";
        u.setAttribute("aria-hidden", "true");
        tl.append(u);
      }
      tl.append(t);
      row.append(tl);
    } else {
      row.append(t);
    }
    row.dataset.chatId = c.id;
    // THE STATE SEGMENT (D2/A, AC-263..AC-265), a SHAPE per state rather than
    // colour alone (AC-264): a spinning ring and the elapsed clock while an
    // agent runs (`elapsedLabel`, the SAME formatter the open turn's own clock
    // uses, D-18), a diamond and "Needs you" while it waits (the export's
    // words for the daemon's `waiting`), a triangle and "Error". The daemon's
    // own word is in the row's label (AC-269).
    const loud = c.status === "running" || c.status === "waiting"
               || c.status === "error";
    const meta = document.createElement("span");
    meta.className = "meta";
    if (!inTree && c.project) {
      const p = document.createElement("span");
      p.className = "proj";
      // NO ROW MATCHES THIS DIRECTORY ANY MORE -- the owner removed the
      // project. Struck through rather than dropped (AC-225).
      if (!c.projectId) {
        p.classList.add("gone");
        p.dataset.help = c.project + " -- this project was removed from "
          + "the pane. The conversation is still here and still opens.";
      }
      p.textContent = projectName({ label: c.projectLabel, path: c.project });
      meta.append(p);
    }
    if (!inTree) {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = chatAgo(at) + " " + c.turns
                    + " turn" + (c.turns === 1 ? "" : "s");
      if (!c.project) w.style.marginRight = "auto";
      meta.append(w);
    }
    if (loud) {
      const st = document.createElement("span");
      st.className = "rowstatus";
      st.dataset.status = c.status;
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.setAttribute("aria-hidden", "true");
      st.append(dot);
      if (c.status === "running") {
        const word = document.createElement("span");
        word.className = "sronly";
        word.textContent = "running ";
        // "pi 1:42": the agent is the record's (`harness`, the listing's),
        // never a guess here; a chat with none shows the clock alone.
        row.dataset.agent = c.harness || "";
        st.append(word, document.createTextNode(
          (c.harness ? c.harness + " " : "") + elapsedLabel(c.elapsed_ms || 0)));
      } else {
        st.append(document.createTextNode(
          c.status === "waiting" ? "Needs you" : "Error"));
      }
      meta.append(st);
    } else if (inTree && isRun) {
      const clock = CLOCK_ICON();
      clock.classList.add("clock");
      meta.append(clock);
    } else if (inTree && at) {
      const w = document.createElement("span");
      w.className = "when";
      w.textContent = chatWhen(at);
      meta.append(w);
    }
    if (meta.childNodes.length) row.append(meta);
    // WHY this row matched, when it was not the title (AC-187). textContent,
    // never innerHTML: this is the owner's own words and a coding agent's.
    if (c.snippet) {
      const s = document.createElement("span");
      s.className = "s";
      s.textContent = c.snippet;
      row.append(s);
    }
    // THE LAST TOOL ACTION, gated on `loud` too, on purpose: `c.action` is
    // cleared the instant a turn ends, and a row must not draw an action
    // line for a state it is not also announcing (D2/A).
    if (loud && c.action) {
      const act = document.createElement("span");
      act.className = "rowaction";
      act.textContent = c.action;
      row.append(act);
    }
    // The dot and the clock are NAMED here, in the one label (AC-469); so is
    // the daemon's own state word (AC-269).
    const runWords = isRun
      ? (c.unread ? ", unread automation run" : ", automation run") : "";
    const stateWords = loud ? ", " + c.status : "";
    row.setAttribute("aria-label",
                     "Open the conversation " + (c.title || "with no title")
                     + runWords + stateWords);
    if (isOpen) row.setAttribute("aria-label", "The conversation Butler is "
                                 + "holding: " + (c.title || "nothing said yet")
                                 + runWords + stateWords);
    row.onclick = () => openChat(c.id);
    line.append(row, chatDropButton(c));
    return line;
}

// How long an armed delete stays armed. Long enough to be a deliberate second
// press, short enough that a row left alone is not a trap waiting for a stray
// click on a phone in a pocket.
const DROP_ARM_MS = 4000;
// The shortest gap a SECOND press may be. A dblclick's two clicks, two taps
// on a phone, and the repeated clicks a held Enter fires (CDP measured 35 ms
// apart) all land well inside this -- none of them is a second deliberate
// press, and the two-step arm exists specifically so an accidental repeat
// gesture cannot commit an irreversible delete (docs/e2e-journeys.md S6c,
// [MEASURED] a double-click, two taps 120 ms apart, and a held Enter each
// deleted with the old code, which had no minimum gap at all).
const DROP_MIN_GAP_MS = 500;
let armedDrop = null;
let armedTimer = 0;
let armedAt = 0;

// DELETE, and the two rules it INHERITS rather than invents.
//
// AC-169's `.copybtn` treatment: present at rest, full on hover AND on
// `:focus-within`, never hover-only -- on a tablet a hover-only control is no
// control, and this project has already paid for a quiet affordance nobody
// could find (the shortcut row's name-click, docs/shortcuts.md).
//
// And a TWO-STEP arm, because unlike the shortcut row's `remove` this one is
// irreversible: a project path is retyped in ten seconds, a conversation is
// gone. The glyph offers the action and the WORD confirms it, so the
// destructive press is never on an unlabelled icon.
function chatDropButton(chat) {
  // ICON-ONLY, matching the rail's "..." menu icon construction (`icon()`,
  // review item 15, 2026-09-26): a literal "x" glyph read as a stray
  // character rather than a control, at 22x28px and 0.42 opacity outside the
  // row's own hover highlight. It still gets a tooltip -- said on the control
  // rather than only in the armed label, because the armed label is the
  // CONFIRMATION and this is what the button is for.
  const drop = document.createElement("button");
  drop.type = "button";
  drop.className = "chatdrop";
  drop.append(DELETE_ICON());
  const label = "Delete the conversation " + (chat.title || "with no title");
  drop.setAttribute("aria-label", label);
  drop.setAttribute("data-help", "Delete conversation");
  // A held Enter/Space fires one "click" per repeated keydown (measured: 35
  // ms apart). Suppressed on the KEYDOWN, before the browser turns it into a
  // click at all -- `onclick`'s own guards below still catch it if a browser
  // ever dispatches the click anyway, but this is the honest place to stop a
  // key that never came up.
  drop.addEventListener("keydown", (e) => { if (e.repeat) e.preventDefault(); });
  drop.onclick = (e) => {
    // A dblclick's SECOND click carries `detail === 2` -- ignored outright,
    // whether it would have armed or committed, because a double-click is one
    // gesture and never two deliberate presses.
    if (e.detail > 1) return;
    if (drop.dataset.armed) {
      // The gap since ARMING, not since any previous click: two taps 120 ms
      // apart, and a dblclick's clicks, both land inside DROP_MIN_GAP_MS, and
      // neither commits (docs/e2e-journeys.md S6c).
      if (Date.now() - armedAt < DROP_MIN_GAP_MS) return;
      commitChatDelete(chat, drop);
      return;
    }
    disarmChatDrop();
    armedDrop = drop;
    armedAt = Date.now();
    drop.dataset.armed = "1";
    const word = document.createElement("span");
    word.textContent = "Delete?";
    drop.replaceChildren(DELETE_ICON(), word);
    drop.setAttribute("aria-label",
                      "Press again to delete this conversation for good");
    // It disarms on its OWN. Deliberately not on Escape: that key has four
    // cases (AC-112) and the last of them stops a reply, which must not
    // become conditional on whether a sidebar button happens to be armed --
    // the same argument that kept the composer from taking a fifth case.
    clearTimeout(armedTimer);
    armedTimer = setTimeout(disarmChatDrop, DROP_ARM_MS);
    drop._label = label;
  };
  return drop;
}

function disarmChatDrop() {
  clearTimeout(armedTimer);
  if (!armedDrop) return;
  delete armedDrop.dataset.armed;
  armedDrop.textContent = "";
  armedDrop.append(DELETE_ICON());
  if (armedDrop._label) armedDrop.setAttribute("aria-label", armedDrop._label);
  armedDrop = null;
}

// One conversation, thrown away for good.
//
// The answer carries the NEW LIST and the live id, so this repaints from the
// DAEMON rather than splicing the local array -- a page that splices can
// disagree with disk and there is no way to notice.
// Where focus goes once the row it was on is gone: the next row in the same
// list, else the previous one, else the project's own "+ New chat" -- never
// left to fall to <body>, invisible and untabbable-out-of
// (docs/e2e-journeys.md S14, item 9e). Read off the OLD tree before the
// delete, because a repaint replaces `line` and everything in it.
function neighborChatId(line) {
  let sib = line && line.nextElementSibling;
  while (sib && !sib.classList.contains("chatline")) sib = sib.nextElementSibling;
  if (!sib) {
    sib = line && line.previousElementSibling;
    while (sib && !sib.classList.contains("chatline")) {
      sib = sib.previousElementSibling;
    }
  }
  const row = sib && sib.querySelector(".chatrow");
  return row ? row.dataset.chatId : null;
}

function focusChatRowOrNewChat(chatId, projectId) {
  if (chatId) {
    const row = document.querySelector(
      '.chatrow[data-chat-id="' + CSS.escape(chatId) + '"]');
    if (row) { row.focus(); return; }
  }
  if (projectId) {
    const btn = document.querySelector(
      '#chatList .pnode[data-project-id="' + CSS.escape(projectId) + '"] .pnew');
    if (btn) btn.focus();
  }
}

async function commitChatDelete(chat, drop) {
  disarmChatDrop();
  const nextId = neighborChatId(drop.closest(".chatline"));
  const projectId = chat.projectId || null;
  drop.disabled = true;
  drop.textContent = "...";
  try {
    const r = await fetch("/api/chats/" + encodeURIComponent(chat.id),
                          { method: "DELETE" });
    if (r.status === 404) {
      // Already gone: another device, or a click that raced this one. The
      // daemon answers 404 rather than a 200 it cannot back, so the honest
      // move here is to refresh and say so quietly -- not an error banner.
      toast("That chat was already gone");
      await loadChats();
      focusChatRowOrNewChat(nextId, projectId);
      return;
    }
    if (!r.ok) throw new Error("the daemon answered " + r.status);
    const doc = await r.json();
    // The answer carries the whole listing, which is what the tree draws.
    chats = Array.isArray(doc.chats) ? doc.chats : [];
    $("chatsNote").textContent = chats.length ? "" : "none yet";
    // Deleting the LIVE conversation makes the daemon ROTATE before it
    // unlinks, so a delete can change which conversation Butler is holding.
    // Read off the answer rather than assumed, or the marked row and the
    // restored transcript would both be about a conversation that is gone.
    // Deleting the conversation ON SCREEN is now exactly the rotation case:
    // it IS the live one (AC-240), the daemon rotated, and the empty
    // transcript below is the fresh record it answered with. There is no
    // separate "go back to live" branch left, because there is nowhere to go
    // back from.
    const rotated = !!doc.session && doc.session !== liveChatId;
    if (rotated) {
      setLiveChat(doc.session);
      paintGuestLock(null);           // a fresh record is nobody's guest chat
      liveMessages = [];
      $("chatTitle").textContent = "current conversation";
      chatHarness = "";
      paintChatAgent(null);
      clearHistory();
      openItem = null;
      $("histCount").textContent = "0 turns";
      renderMessages(liveMessages);
    }
    paintChatList();
    // The project's count moved with it. NOT awaited (unchanged), but
    // focus has to come AFTER whatever it repaints -- `loadShortcuts` ->
    // `paintShortcuts` rebuilds `#chatList` too, and focusing a row or a
    // "+ New chat" button before that second rebuild lands the focus on a
    // node this same delete is about to throw away, so it silently falls
    // back to <body> a moment later [MEASURED live: the .pnew focused here
    // synchronously, then lost it within one network round trip].
    loadShortcuts().then(() => focusChatRowOrNewChat(nextId, projectId));
    toast("Chat deleted");
  } catch (e) {
    // Loud, because this one had a consequence: a delete that silently did
    // nothing is worse than one that failed (D-4).
    showError("could not delete that conversation: " + (e.message || e));
    await loadChats();
  }
}

// --------------------------------------------------------------------------- //
// SEARCH over past conversations (AC-185..AC-188, docs/chat-search.md)
//
// The MATCHING is the daemon's, and there is deliberately no filter here: this
// page holds titles and the daemon holds the transcripts, so a page-side rule
// would hide rows the daemon would have shown -- invisibly, because a row that
// is not drawn looks exactly like a conversation that does not exist.
//
// DEBOUNCED rather than fired per keystroke: the route reads and parses every
// conversation on disk, which is why it is the one listing branch the daemon
// runs off its event loop (D-25).
// --------------------------------------------------------------------------- //
const CHAT_SEARCH_MS = 220;
let chatQuery = "";
let chatSearchTimer = 0;
// The query whose answer is allowed to land. Two searches in flight can come
// back out of order, and the slow one would then paint over the fast one
// (D-6's rule: an answer for something we have walked away from changes
// nothing).
let chatSearchSeq = 0;

function onChatSearch(value) {
  chatQuery = (value || "").trim();
  if (chatSearchTimer) clearTimeout(chatSearchTimer);
  chatSearchTimer = setTimeout(() => { chatSearchTimer = 0; loadChats(); },
                               CHAT_SEARCH_MS);
}

// The query string the daemon is asked for. The tree asks for EVERY
// conversation and groups them itself -- grouping, never filtering (D-D1):
// every served row is drawn under exactly one project -- so `?project=` is no
// longer sent. `?q=` is the one filter, and it is the daemon's (AC-188).
function chatQueryString() {
  return chatQuery ? "?q=" + encodeURIComponent(chatQuery) : "";
}

async function loadChats() {
  const mine = ++chatSearchSeq;
  try {
    const r = await fetch("/api/chats" + chatQueryString());
    if (mine !== chatSearchSeq) return;
    if (!r.ok) {
      // Endpoint mode holds no conversations at all, which is a different
      // fact from an empty list -- so the heading says which (AC-7b).
      chats = [];
      $("chatsNote").textContent = r.status === 404 ? "none here" : "unavailable";
      paintChatList();
      return;
    }
    const doc = await r.json();
    if (mine !== chatSearchSeq) return;
    chats = Array.isArray(doc.chats) ? doc.chats : [];
    // The header names the conversation on screen by the row's own title
    // once it has one (S3c): after its first turn it stopped being "current
    // conversation", and this fetch is the first place the page hears that.
    const here = chats.find((c) => c.id === liveChatId);
    if (here && here.title) $("chatTitle").textContent = here.title;
    if (here) paintChatAgent(here);
    $("chatsNote").textContent = chatQuery
      ? (chats.length ? String(chats.length) + " found" : "no match")
      : (chats.length ? "" : "none yet");
    paintChatList();
  } catch {
    // A list of past conversations is never worth an error banner: the page
    // is a microphone first.
    $("chatsNote").textContent = "unavailable";
  }
}

// THE HEADER NAMES THE PROJECT (AC-292): the daemon's `projectLabel` for the
// conversation on screen, never a memory of a click -- so a birth or a
// "work in X" made on the phone names the right project on the desk (D-2).
let agentInfo = { agents: [], agent: "", label: "", dflt: "", fixed: true,
                  model: "", modelDefault: "" };
// THE CHAT'S AGENT, in the daemon's words (CT-8, AC-688): `agent_label` rides
// every chat row and open-chat document, so this page keeps no name table and
// names no setting. textContent only. Hidden when the payload has none.
function paintChatAgent(doc) {
  const name = String((doc && doc.agent_label) || "");
  $("chatAgent").textContent = name;
  $("chatAgent").hidden = !name;
  // The picker's whole input is the same payload (AC-776, AP-3, CM-8): the
  // choices in the daemon's order, the default, whether the agent is fixed,
  // the chat's own model and the resolved default's name. `null` (no
  // conversation, endpoint mode) clears it.
  // A listing row carries the label but not the choices: it leaves the picker
  // alone.
  if (doc && !Array.isArray(doc.agents)) return;
  agentInfo = doc
    ? { agents: doc.agents.map((a) => ({ name: String(a.name), label: String(a.label) })),
        agent: String(doc.agent || ""), label: name,
        dflt: String(doc.agent_default || ""),
        fixed: doc.agent_fixed !== false, model: String(doc.model || ""),
        modelDefault: String(doc.model_default || "") }
    : { agents: [], agent: "", label: "", dflt: "", fixed: true, model: "", modelDefault: "" };
  paintAgentPicker();
}

function paintChatProject(doc) {
  const name = String((doc && doc.projectLabel) || "");
  $("chatProject").textContent = name;
  $("chatProject").hidden = !name;
  // The explorer shows THIS conversation's project, re-rooted here because
  // every path that changes it -- open, birth, `opened` -- paints here. The
  // library follows the same conversation for the same reason (AC-467).
  files.follow();
  library.follow();
}

// OPEN OR SHUT ONE PROJECT IN THE TREE (AC-291). A VIEW change and nothing
// else: it writes the expanded set and nothing more -- no conversation is
// opened, nothing is filed, nothing is started (R1, upheld: "browsing moves
// nothing"). Painted at once, then persisted; the daemon's answer repaints.
function toggleProject(id) {
  if (expandedProjects.has(id)) expandedProjects.delete(id);
  else expandedProjects.add(id);
  paintChatList();
  saveShortcut({ expanded_projects: [...expandedProjects] });
}

function ensureExpanded(id) {
  if (!id || expandedProjects.has(id)) return;
  expandedProjects.add(id);
  paintChatList();
  saveShortcut({ expanded_projects: [...expandedProjects] });
}

// OPEN one conversation: it becomes the one Butler is holding, so the next
// thing said or typed continues THAT conversation (AC-240).
//
// It POSTs `/api/session/open` rather than GETting `/api/chats/{id}`, and the
// verb is the feature. Until 2026-09-12 this was a read that changed only what
// the page DREW, and every path that started a turn called `backToLive()`
// first -- so clicking a conversation and sending abandoned it and put the
// message in the live one, which is the owner's own rule inverted. The GET
// survives as the read that changes nothing; nothing on this page uses it.
//
// It STARTS NOTHING (AC-239): the daemon moves a pointer, and the first send
// is what spawns a coding agent, in the workspace this conversation records.
//
// Everything the answer carries is the daemon's -- the transcript, the project
// row, the message ids the rewind controls need -- because this is now the
// same record `/api/session/current` serves, in the same shape.
async function openChat(id) {
  if (!id) return;
  closeDrawer();
  terminals.hide();
  // The Tasks and automations page sits over the conversation column with
  // the sidebar live (CU-16): a chat opened from beside it takes the column.
  automations.closePage(false);
  try {
    const r = await fetch("/api/session/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (!r.ok) {
      if (r.status === 404) {
        // Gone -- deleted on another device, most likely. The raw id in the
        // daemon's own message ("no conversation 'chat-...'") is nobody's
        // business but a log's, and the dead row staying on screen is the
        // other half of the same bug: it looks like it can still be opened
        // (docs/e2e-journeys.md S6d).
        toast("That chat no longer exists");
        await loadChats();
        return;
      }
      const doc = await r.json().catch(() => ({}));
      showError(doc.error || ("could not open that conversation (HTTP "
                              + r.status + ")"));
      return;
    }
    const doc = await r.json();
    clearHistory();
    openItem = null;
    chatHarness = String(doc.harness || "");
    $("histCount").textContent = "0 turns";
    setLiveChat(doc.id || id);
    liveMessages = doc.messages || [];
    // THE ID AND NEVER THE PATH, `loadSession`'s rule for `loadSession`'s
    // reason: the tile's "you are here" mark is the daemon's own answer about
    // which row this conversation's directory is, and comparing spellings here
    // is what made the mark go out on a re-spelled row (review 2026-09-11).
    chatProjectId = String(doc.projectId || "");
    paintChatProject(doc);
    paintChatAgent(doc);
    paintGuestLock(doc);
    ensureExpanded(chatProjectId);
    renderMessages(liveMessages);
    // The DAEMON's answer, not a lookup of the row this page happens to be
    // holding: one rule for what a conversation is called (2026-09-15), and it
    // is the same field the asking list was built from.
    $("chatTitle").textContent = doc.title || "current conversation";
    if (shortcuts) paintShortcuts(shortcuts);
    // RE-FETCHED, not repainted from the cached rows: opening another chat is
    // how a New chat nobody spoke in is LEFT, and the daemon stops listing it
    // then (it was never on disk -- `ChatStore._write`). Repainting the old
    // array kept it as a "(nothing was said)" ghost until the next reload;
    // the page holds no copy of that rule, the daemon's list decides.
    loadChats();
    logNote("");
    // Focus follows the ACT: opening a chat means the next thing is almost
    // always typing into it, and focus otherwise falls to <body>, invisible
    // and un-tabbable-out-of (docs/e2e-journeys.md S14, item 9e).
    $("input").focus();
  } catch (e) {
    showError("could not open that conversation: " + (e.message || e));
  }
}

let liveChatId = "";

// THE DEVICE TELLS THE DAEMON WHAT IT IS SHOWING, AND THIS IS THE ONLY WRITER
// OF `liveChatId` (AC-238, AC-247, `docs/parallel-agents.md` phase 6).
//
// The daemon had no way to know. Under the owner's ruling a device SPEAKS the
// conversation it is showing, so the conversation on screen decides whether a
// reply is read aloud here and which conversation an attached file belongs to
// -- and both of those are the daemon's to act on, so both need this fact.
//
// One writer rather than three assignment sites, because a fourth site that
// assigned and forgot to declare would leave the daemon speaking the wrong
// conversation with nothing on screen saying so (D-18). Not browser storage
// and not a setting (AC-8, AC-41): it is per socket, the daemon forgets it on
// a drop, and the `ready` handler re-declares by calling `loadSession` again.
function setLiveChat(id) {
  const next = String(id || "");
  // THE SCREEN CHANGES CONVERSATION, SO ITS TURN STATE DOES TOO. Parked
  // locally and never stopped: the turn keeps running and is still in
  // `chatTurns`, so opening it again adopts it (`settleScreenTurn`). A
  // CAPTURE is left alone -- the owner is talking to the conversation they
  // started talking to, and its `final` names that chat and parks it then.
  if (next !== liveChatId && state !== CAPTURE) parkScreenTurn();
  liveChatId = next;
  // `null` and not "" when there is nothing: the daemon reads a named
  // conversation as "mute anything else" and an absent one as "this device
  // hears everything", and those must not be the same value on the wire.
  send({ type: "showing", chat: liveChatId || null });
  // The burger's busy roll-up excludes whichever chat is now on screen
  // (AC-296), so the id changing is a repaint too.
  paintBurger();
}

// A note is a STATUS that lasts as long as what it describes (dictating,
// transcribing, arming the microphone): the next thing that happens replaces
// it, and its end is ALWAYS a later `logNote("")` of its own -- a timer would
// clear "dictating" while still dictating. Anything worth keeping is in the
// history or the errors.
function logNote(text) {
  $("notice").hidden = !text;
  $("notice").textContent = text || "";
}

// THE ONE TOAST (docs/lantern-ui.md 5.2, Step 6c): a one-shot announcement
// with nothing left to replace it -- "Copied", "Saved", "Restarting Butler",
// "Chat deleted". Such a word used to linger in the chat area for minutes
// (docs/e2e-journeys.md, findings/UI, item 9b), or was a per-button CSS
// bubble (`data-said`), a second mechanism that drifted. ONE element,
// `#toast` (`role="status"`, always in the tree so a screen reader hears the
// change), ONE function; the box is the overlay family's (`--pop`, the
// shared shadow) and only fades under reduced motion. A new toast replaces
// the one showing.
const TOAST_MS = 2400;
let toastTimer = 0;
function toast(text) {
  const t = $("toast");
  clearTimeout(toastTimer);
  t.textContent = text;
  t.classList.add("on");
  toastTimer = setTimeout(() => {
    t.classList.remove("on");
    toastTimer = setTimeout(() => { t.textContent = ""; }, 400);
  }, TOAST_MS);
}
let lastTimings = "";

// --------------------------------------------------------------------------- //
// A CODING AGENT'S QUESTION (AC-234, AC-249, AC-250, AC-49 REVERSED)
//
// Until 2026-09-12 the daemon answered every dialog a coding agent raised and
// told it the user had dismissed it, so a blocked agent looked exactly like
// one that had decided on its own. It now asks, and this is what draws the
// asking.
//
// VIEW STATE IN A VARIABLE, never browser storage (AC-8) and never a setting
// (AC-41). It is also not a preference: which questions are open is a fact
// about the daemon, and it is re-read from `GET /api/session/current` on every
// `ready`, so a reload and a second device agree without anything being
// stored here.
// --------------------------------------------------------------------------- //
// `openQuestions` is declared at the top of this file with the other page
// state, because `paint()` and `armWatchdog()` both read it
// and both run during the module's own start-up -- a `const` down here would
// be in its temporal dead zone at that moment and take the whole page down.

// WHICH AGENT the conversation on screen runs, in the daemon's words: the
// listing row's `harness` (re-read at every turn's end), else the record's
// own, read when the conversation came on screen. "" when it has none yet.
function screenAgent() {
  const row = chats.find((c) => c.id === liveChatId);
  return (row && row.harness) || chatHarness;
}
function askingAgent() { return screenAgent() || "the coding agent"; }

function applyQuestions(list) {
  openQuestions.clear();
  for (const q of (Array.isArray(list) ? list : [])) {
    if (q && q.qid != null) openQuestions.set(String(q.qid), q);
  }
  paintAsk();
}

// One place the card is built, reached by the frame, by the close and by the
// session load. `paint()` and `armWatchdog()` are called from here rather than
// from each of those, so a card appearing and the two things that depend on
// one being up can never disagree (D-18).
function paintAsk() {
  const card = $("askCard");
  card.replaceChildren();
  const asks = [...openQuestions.values()];
  card.hidden = asks.length === 0;
  asks.forEach((q, i) => card.append(askBlock(q, i === 0)));
  paint();
  armWatchdog();
  paintComposerPlaceholder();
}

// The choices a question offers, as [label, answer body] pairs: its own
// options, or Yes / No for a confirm (`{confirmed}` is what the harness waits
// for, and a typed "yes" is not that). One list read by the card's buttons
// and by the number keys, so the two can never disagree.
function askChoices(q) {
  const options = Array.isArray(q.options) ? q.options : [];
  if (options.length) return options.map((label) => [String(label), { value: label }]);
  if (q.method === "confirm") return [["Yes", { confirmed: true }], ["No", { confirmed: false }]];
  return [];
}

// An option chosen on this page. Its label is kept on the turn's item, for
// the finished row's "You chose" (AC-695) -- the page's own fact.
function chooseAnswer(q, [label, body]) {
  if (openItem) openItem.chose = label;
  sendAnswer(q.qid, body);
}

function askBlock(q, numbered) {
  const box = document.createElement("div");
  box.className = "ask";

  const who = document.createElement("p");
  who.className = "who";
  who.textContent = askingAgent() + " is asking";
  box.append(who);

  if (q.title) {
    const title = document.createElement("div");
    title.className = "qtitle";
    title.textContent = q.title;
    box.append(title);
  }
  const text = document.createElement("p");
  // The AGENT'S OWN WORDS, verbatim and as TEXT. Never markdown: this string
  // comes out of a coding agent's dialog and is what the owner is being asked
  // to agree to, so rendering it would let it style itself.
  text.textContent = q.message || q.title || ("a " + (q.method || "") +
                                              " question");
  box.append(text);

  // NUMBERED on the oldest question, the one the box and the keys answer
  // (AC-693): 1, 2, 3 choose while the box is empty (`askKey`).
  const choices = askChoices(q);
  if (choices.length) {
    const opts = document.createElement("div");
    opts.className = "opts";
    choices.forEach((choice, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "opt";
      if (numbered && i < 9) {
        const n = document.createElement("span");
        n.className = "n";
        n.setAttribute("aria-hidden", "true");
        n.textContent = String(i + 1);
        b.append(n);
      }
      b.append(choice[0]);
      b.addEventListener("click", () => chooseAnswer(q, choice));
      opts.append(b);
    });
    box.append(opts);
  }

  // The agent's own time limit, when it set one: nothing else says it.
  if (q.deadline_ms) {
    const orelse = document.createElement("p");
    orelse.className = "orelse";
    orelse.textContent = askingAgent() + " set its own time limit of "
      + Math.round(q.deadline_ms / 1000) + "s and will decide for itself "
      + "when that runs out.";
    box.append(orelse);
  }
  return box;
}

// 1..9 choose the OLDEST question's options while the box is empty (AC-693).
// Scoped to the dock -- the box and the card -- never the document, so
// Escape's order (AC-112) gains no case and no other control loses a key.
function askKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
  if (!/^[1-9]$/.test(e.key) || composerInput.value !== "") return;
  if (e.target !== composerInput && !$("askCard").contains(e.target)) return;
  const [q] = openQuestions.values();
  const choice = q && askChoices(q)[Number(e.key) - 1];
  if (!choice) return;
  e.preventDefault();
  chooseAnswer(q, choice);
}

async function sendAnswer(qid, body) {
  clearErrors();
  try {
    const r = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qid, ...body }),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) {
      // 410 AND THE REASON (AC-250). The card is dropped here rather than
      // waiting for a broadcast, because a 410 means the daemon no longer
      // holds this question at all -- there is nothing left to close and
      // leaving the buttons up would offer an act that cannot happen.
      showError(doc.error
        || ("that answer was refused (HTTP " + r.status + ")"));
      openQuestions.delete(String(qid));
      paintAsk();
      return;
    }
    // NOT removed optimistically. The daemon broadcasts `question_closed` and
    // that is what takes the card down, on every device at the same moment:
    // a page that cleared its own card first would be claiming an answer
    // landed before anything confirmed it (AC-7b).
    logNote("answer sent to " + askingAgent());
  } catch (e) {
    showError("could not send that answer: " + (e.message || e));
  }
}

// The agent's NAME on an open card follows the listing: a first turn's card
// can arrive before the listing knows which agent the chat runs, so every
// listing repaint re-says it (`paintChatList`) without rebuilding the card.
function paintAskWho() {
  for (const who of $("askCard").querySelectorAll(".who")) {
    who.textContent = askingAgent() + " is asking";
  }
  if (openQuestions.size) paint();         // the working row says it too
  paintComposerPlaceholder();
}

function paintComposerPlaceholder() {
  const box = $("input");
  // The export's words (lantern-ui section 4). The slash picker is said in
  // the Library panel ("Type / in the message box to use one.").
  box.placeholder = openQuestions.size
    ? ("Or type your answer for " + askingAgent())
    : "Message Butler";
}

// --------------------------------------------------------------------------- //
// Watchdog -- the page's own anti-latch net. Never talks to the daemon.
// --------------------------------------------------------------------------- //
function armWatchdog() {
  if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  // SUSPENDED WHILE A CODING AGENT IS WAITING ON YOU (AC-234). `thinking` is
  // 180 s and a waiting child emits nothing by definition, so this net -- the
  // one that exists to catch a turn nothing is driving -- would give up on
  // the turn and take the question with it at three minutes. The daemon
  // suspends its own `HARNESS_IDLE_S` for the same reason and in the same
  // commit; a page that gave up on its side alone would leave the owner
  // looking at a card whose turn this page had already abandoned.
  if (openQuestions.size) return;
  // Nor for a WATCHED turn (`watchChat`): this page is not driving it, the
  // daemon's status poll is its heartbeat, and giving up "from our own side"
  // would send a stop for an agent that is working.
  if (watchedChat) return;
  // CAPTURE reads the LIVE ceiling (AC-92's other half, above); THINKING and
  // SPEAK still read the flat constants -- this criterion is about the
  // capture watchdog specifically, and the daemon has no equivalent live
  // number for those two to report.
  const budget = state === CAPTURE ? captureCeilingMs : WATCHDOG_MS[state];
  // The budget itself is the guard now (AC-178). It reads `!running` before,
  // which meant a TYPED turn on a page with the microphone off got no
  // anti-latch net at all -- and a typed turn is exactly the turn that can now
  // happen without one. Every state carrying a budget IS a turn in flight,
  // whichever door it came in by; every state without one is not.
  if (!budget) return;
  const stuckIn = state;
  watchdog = setTimeout(() => {
    if (state !== stuckIn) return;
    // Says what THIS PAGE did, not what the daemon failed to do (AC-92).
    // Silence is normal in a long-form turn, so "no daemon traffic" read as an
    // accusation against a daemon that was behaving exactly as configured.
    // The second clause is CONDITIONAL for the same reason the first one is
    // worded the way it is: claiming the microphone is live on a page whose
    // microphone is off is a small lie in the one message a stuck turn leaves
    // behind (AC-7b).
    showError(`this page waited ${budget / 1000}s in ${STATE_LABEL[stuckIn]} `
              + "and is giving up on the turn from its own side"
              + (micLive
                 ? "; the microphone stays live and is listening for the "
                   + "wake phrase again"
                 : "; you can type another message"));
    // THE DAEMON MUST BE TOLD, NOT JUST THE PAGE (the second cause behind the
    // owner's wedged endword turn). This used to call `goListen()` alone,
    // which re-armed the PAGE and left the daemon still holding the capture
    // AND THE RECOGNIZER -- MEASURED against the owner's own log: their
    // numpad dictation was refused with AsrBusy ("a turn is using the
    // recognizer") ten seconds BEFORE the page said it had given up.
    // `stopTurn()` is the one path that does both (D-18): it is reachable in
    // exactly the three states this watchdog carries a budget for (CAPTURE,
    // THINKING, SPEAK), so `state === stuckIn` above already guarantees it
    // will not bail out early.
    stopTurn();
  }, budget);
}

// The running-turn clock has ONE writer (D-1, D-18), so it can never be
// armed without being disarmed by the same hand: `goListen()` -- the one
// exit every terminal path reaches -- calls this with null, a live
// `worker_event` and a recovered reload both call it with a calibrated
// start time. The interval only ever repaints; it touches no other state,
// the same rule `setLinkSkin`'s own comment states for itself, so a link
// skin over a running turn suppresses what it DRAWS (paint() below) without
// this needing to know the socket exists.
function setTurnClock(startedAt) {
  turnStartedAt = startedAt;
  lastWordAt = Date.now();
  if (startedAt == null) { turnProgress = null; progressAt = 0; }
  if (elapsedTicker) { clearInterval(elapsedTicker); elapsedTicker = null; }
  if (turnStartedAt != null) elapsedTicker = setInterval(paint, 1000);
}

// "0:07", "12:04" -- read across a room, so seconds alone would be a wall of
// digits and an hour count is a turn nobody should still be waiting on.
function elapsedLabel(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60), s = total % 60;
  return m + ":" + String(s).padStart(2, "0");
}

// WHAT THE TURN IS DOING, in words, from the daemon's own evidence and
// nothing else (AC-392). `p` is a `progress` frame or a status body. The
// working row puts the agent, the project and the clock in front of it.
function workingWhat(p) {
  const n = (x) => String(x || 0);
  switch (p && p.phase) {
    case "tool": return "Running " + (p.detail || "a tool");
    case "tool_input": return "writing a tool call, " + n(p.thought_chars) + " chars";
    case "thinking":
      return "thinking" + (p.thought_chars ? ", " + n(p.thought_chars) + " chars" : "");
    case "writing": return "writing, " + n(p.chars) + " chars";
    case "asking": return "waiting for your answer";
    case "speaking": return "speaking";
    default: return "";
  }
}

// How long the AGENT has produced nothing, as of now: the frame's own
// `last_event_ms` plus the time since the frame arrived. Zero without one.
function quietMs() {
  if (!turnProgress || turnProgress.last_event_ms == null) return 0;
  return turnProgress.last_event_ms + (Date.now() - progressAt);
}

// A background conversation's tree row follows its own frames (AC-392):
// "pi 0:42" (lantern-ui section 4), the agent from the listing's `harness`. Only a row the listing already drew as running is
// touched, so nothing here can claim a state the daemon did not.
function paintRowProgress(m) {
  const row = document.querySelector(
    '#chatList [data-chat-id="' + CSS.escape(String(m.chat)) + '"]');
  const st = row && row.querySelector('.rowstatus[data-status="running"]');
  if (!st || !st.lastChild) return;
  st.lastChild.textContent = (row.dataset.agent ? row.dataset.agent + " " : "")
    + elapsedLabel(m.elapsed_ms || 0);
}

// The DAEMON's word for what one conversation is doing (`hub.status_for`),
// or null when it could not be asked at all. The one reader of the status route.
async function chatStatus(chatId) {
  try {
    const r = await fetch(`/api/chats/${encodeURIComponent(chatId)}/status`);
    // An ANSWER that is not a status (a 404 for a deleted chat) is still an
    // answer: not in flight. Only NO answer at all is null, which a watched
    // turn reads as silence rather than as an ending (AC-393).
    return r.ok ? await r.json() : { status: "unknown", http: r.status };
  } catch { return null; }
}

// Evidence from a frame or a status body, when it carries any.
function takeProgress(p) {
  if (!p || !p.phase) return;
  turnProgress = p;
  progressAt = Date.now();
}

// "A turn is in flight" in the daemon's words: `waiting` is a running turn
// blocked on a question, and Stop frees it too (AC-234).
function inFlight(body) {
  return !!body && (body.status === "running" || body.status === "waiting");
}

// AC-236: recover the clock across a switch or a reload rather than showing
// zero. A failed fetch costs nothing but the static sentence `paint()`
// already had; a turn that ended meanwhile has no clock to show.
async function recoverTurnClock(chatId) {
  const body = await chatStatus(chatId);
  if (chatId !== liveChatId || !inFlight(body)) return;
  setTurnClock(Date.now() - (body.elapsed_ms || 0));
  takeProgress(body);
  paint();
}

// THE SCREEN LEAVES A CONVERSATION: forget its turn LOCALLY and tell the
// daemon nothing. Not `goListen()`, which closes the transcript item with
// "no reply came back" and refreshes as if a turn had ended -- this one has
// not, it is merely off screen, and its frames now route to `chatTurns`.
function parkScreenTurn() {
  if (currentTurn == null && !watchedChat
      && state !== THINKING && state !== SPEAK) return;
  stopWatching();
  cancelDrain();
  stopPlayback();
  currentTurn = null;
  speakingTurn = null;
  setTurnClock(null);
  lastAction = "";
  openItem = null;
  renderLive("", "");
  hideClosing();
  hud("hTurn", "-");
  setState(micLive ? WAKE : OFF);
}

// A CONVERSATION CAME ON SCREEN: take up whatever turn it has in flight.
// Called once the transcript is drawn (`renderMessages`), because the last
// restored question is the item a running turn's answer belongs in.
// Three cases, all from something the daemon said: a turn this socket drives
// (`chatTurns`) is adopted with its text so far; otherwise the status route
// says whether one runs elsewhere (`watchChat`); otherwise the item closes.
function settleScreenTurn() {
  const chat = liveChatId;
  const t = chat ? chatTurns.get(chat) : null;
  if (t) {
    if (state !== CAPTURE) {
      currentTurn = t.turn;
      hud("hTurn", String(t.turn));
      for (const [text, tier, spoken] of t.pieces) logReply(text, tier, spoken);
      if (t.pieces.length) rememberTurnItem(t.turn, openItem);
      if (state !== SPEAK) setState(THINKING);
    }
    recoverTurnClock(chat);
    return;
  }
  if (!chat) { closeItem(); return; }
  const item = openItem;
  void (async () => {
    const body = await chatStatus(chat);
    // Moved on while asking: another conversation, or a turn of this one.
    if (chat !== liveChatId || openItem !== item || chatTurns.has(chat)) return;
    if (inFlight(body) && state !== CAPTURE) watchChat(chat, body);
    else closeItem();
  })();
}

// The conversation on screen runs a turn this socket cannot drive. Stop is
// offered (it names the chat, the daemon finds the turn), the clock is the
// daemon's, and the status route is the heartbeat -- no frame of that turn
// reaches this socket, so without it the page could only guess the end.
// Exempt from the page's own watchdog (`armWatchdog`): giving up on a turn
// this page is not driving would send a stop for a working agent.
function watchChat(chat, body) {
  stopWatching();
  watchedChat = chat;
  setState(THINKING);
  setTurnClock(Date.now() - (body.elapsed_ms || 0));
  takeProgress(body);
  paint();
  watchTimer = setInterval(async () => {
    const now = await chatStatus(chat);
    if (watchedChat !== chat) return;
    // A poll that could not be answered is SILENCE, not an ending: it used
    // to read as "the turn ended" and re-read the record under a running
    // agent. The stall line says it after 15 s instead (AC-393).
    if (now === null) return;
    if (inFlight(now)) {
      lastWordAt = Date.now();
      takeProgress(now);
      return;
    }
    // It ended. The record now holds the answer, so it is re-read rather
    // than the item closed empty; `goListen` is the one exit either way.
    openItem = null;
    goListen();
    if (chat === liveChatId) {
      const row = chats.find((c) => c.id === chat);
      loadSession({ session: chat, turns: row ? row.turns : 0 });
    }
  }, WATCH_POLL_MS);
}

// THE TREE WHEN A TURN ENDS OFF SCREEN OR IS STOPPED. The daemon's status
// flips only at `_rearm`, which comes after `speak_end` and the playback
// gate, and after a `stop` it is acted on off the path of any listing -- so a
// listing read at the frame can predate the end and leave the row saying
// "running" (measured on the scratch daemon, both cases). The status route
// is asked until it says the turn is over -- a bounded few times, then the
// listing is read whatever it says, because it is the truth.
async function refreshWhenSettled(chat) {
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (!inFlight(await chatStatus(chat))) break;
  }
  loadChats();
  loadShortcuts();
}

function stopWatching() {
  watchedChat = "";
  if (watchTimer) { clearInterval(watchTimer); watchTimer = null; }
}

// Does a turn frame end its turn? The daemon's terminal frames for a turn:
// `speak_end` on every reply exit, `error` naming it, an empty `final`.
function endsTurn(m) {
  return m.type === "speak_end" || m.type === "error"
    || (m.type === "final" && !String(m.text || "").trim());
}

// BOOKKEEPING FOR EVERY TURN FRAME THAT NAMES A CHAT, on screen or not.
// Answers false for a frame from an OLDER turn of that chat, which a newer
// one has superseded (D-6; turn ids are the daemon's and only grow).
function trackTurnFrame(m) {
  let t = chatTurns.get(m.chat);
  if (t && m.turn < t.turn) return false;
  // A heartbeat names a turn a `final` already announced. One for a turn
  // this socket does not know is LATE -- it crossed a stop on the wire --
  // and taking it would park a ghost turn the next open would adopt.
  if (m.type === "progress" && (!t || t.turn !== m.turn)) return false;
  if (!t || t.turn !== m.turn) {
    if (endsTurn(m)) return true;
    t = { turn: m.turn, pieces: [] };
    chatTurns.set(m.chat, t);
  }
  if (m.type === "reply") t.pieces.push([m.text || "", m.tier, m.spoken]);
  // TAKEN: the tree's running mark is the daemon's own word, read now
  // (S3b) rather than only at the end of a turn.
  if (m.type === "final" && String(m.text || "").trim()) loadChats();
  if (endsTurn(m)) {
    chatTurns.delete(m.chat);
    files.turnEnded(m.chat);                 // AC-315
  }
  return true;
}

// --------------------------------------------------------------------------- //
// The single exit. EVERY terminal path lands here (D-1, AC-2).
// --------------------------------------------------------------------------- //
function goListen() {
  // Whatever turn was open is over, however it ended (AC-94). This is the one
  // exit every terminal path reaches, so it is the only place that can tell a
  // finished turn from one still running -- and a turn that ends here with no
  // reply text says so on its own face instead of rendering blank.
  terminals.refresh();
  closeItem();
  // A held artifact whose item never appeared (an empty final) is in the
  // record; nothing on screen is its place.
  heldArtifacts = [];
  if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; }
  cancelDrain();
  discardPreroll();
  // The on-screen turn is over, however it ended -- a stop sends no terminal
  // frame back, so its registry entry goes here, and a watched turn's poll
  // with it.
  for (const [chat, t] of chatTurns) {
    if (t.turn === currentTurn) chatTurns.delete(chat);
  }
  stopWatching();
  currentTurn = null;
  speakingTurn = null;
  // The running-turn clock is over too, however this turn ended -- the same
  // "every terminal path lands here" rule one field further along (D-1).
  setTurnClock(null);
  lastAction = "";
  stopPlayback();
  renderLive("", "");
  hideClosing();
  hud("hTurn", "-");
  // Cooldown, NOT reset(): reset() would throw away the detector's embedding
  // context and deafen the next wake for ~1.3 s. With no config yet there is
  // no detector either (`arm()` waits for it), so nothing to suppress.
  if (wakeCfg) wakeSuppressUntil = performance.now() + wakeCfg.cooldown_ms;
  setState(micLive ? WAKE : OFF);
  // The end of a turn is the first moment Butler's record holds it, so it is
  // where the rewind controls learn their message ids (AC-196). Fired and not
  // awaited: this is the one exit every terminal path lands in, and an await
  // here would put a round trip between a failed turn and the re-arm (D-1).
  refreshLiveIds();
  // The same moment is when the sidebar row has a title and a turn count and
  // the project tiles have their busy marks back, and nothing else re-read
  // them: a typed turn left its row saying "New conversation / 0 turns"
  // while `GET /api/chats` already served the real one (review 2026-09-26).
  // Here and not per terminal path, for the reason `refreshLiveIds` is here.
  loadChats();
  loadShortcuts();
  // A stop sends no terminal frame, so the explorer hears it here (AC-315).
  files.turnEnded(liveChatId);
}

// --------------------------------------------------------------------------- //
// Pre-roll ring
// --------------------------------------------------------------------------- //
function ringPush(buf) {
  ring.push(buf);
  ringBytes += buf.byteLength;
  while (ringBytes > RING_MAX_BYTES && ring.length > 1) {
    ringBytes -= ring.shift().byteLength;
  }
}

function beginPreroll() {
  // Snapshot the lookback FIRST -- this is the audio from before the fire.
  prerollFrames = ring.slice();
  prerollBytes = prerollFrames.reduce((n, b) => n + b.byteLength, 0);
  // Frozen here: the fire point is where the turn starts (AC-800). The
  // daemon may not hear voicing before it, or the wake phrase arms the
  // silence clock and the wait for capture_start ends the turn.
  lookbackBytes = prerollBytes;
  pendingWake = true;
  hud("hPreroll", Math.round(prerollBytes / MIC_BYTES_PER_MS) + " ms");
}

function discardPreroll() {
  pendingWake = false;
  prerollFrames = [];
  prerollBytes = 0;
  lookbackBytes = 0;
  hud("hPreroll", "-");
}

// Flush the buffered PCM as the FIRST bytes of the turn, then live frames
// continue from routeAudio(). The header declares the length so the daemon can
// tell back-fill from live audio, and `lookback`, how much of it came before
// the fire, so it can tell the wake phrase from the turn (plan.md 4.3, AC-802).
function flushPreroll(turn) {
  const frames = prerollFrames, bytes = prerollBytes, lookback = lookbackBytes;
  const ms = Math.round(bytes / MIC_BYTES_PER_MS);
  discardPreroll();
  send({ type: "preroll", turn, bytes, ms, lookback });
  for (const f of frames) sendPcm(f);
  hud("hPreroll", `${ms} ms / ${bytes} B flushed`);
  return ms;
}

// --------------------------------------------------------------------------- //
// Ambient ring -- the last N seconds of the room, held here and nowhere else
// (AC-68, AC-72)
// --------------------------------------------------------------------------- //
function ambientPush(buf) {
  // OFF means nothing is retained, not retained-and-withheld (AC-72). This one
  // line is the whole of that guarantee: with no window there is no push, so
  // there is no ring for anything else to leak.
  if (!ambientSeconds) return;
  ambientRing.push(buf);
  ambientBytes += buf.byteLength;
  ambientTrim();
}

// Bounded by WALL-CLOCK seconds (AC-72), which at a fixed 16 kHz mono int16 is
// a byte count: 32 B per ms, so N seconds is N * 32000 bytes.
function ambientTrim() {
  const max = ambientSeconds * MIC_RATE * 2;
  while (ambientBytes > max && ambientRing.length) {
    ambientBytes -= ambientRing.shift().byteLength;
  }
}

// Never to browser storage, ever: this directory may not use any, and there is
// a grep gate that says so (AC-8, AC-72). Dropping the references is the whole
// of the discard -- the frames are plain ArrayBuffers that nothing else holds
// once the ring lets go.
function discardAmbient() {
  ambientRing = [];
  ambientBytes = 0;
}

// The window arrived, or changed. Shrinking it drops what it no longer covers
// at once rather than at the next frame, and turning it off drops everything:
// a page still holding half a minute of a room after the owner turned the
// feature off is precisely what AC-72 is about.
// What the voice popover's Room memory switch turns ON to (AC-686): the last
// window this page saw in force, else ROOM_MEMORY_DEFAULT_S. `ambient_seconds`
// has no "on" value of its own -- 0 is off and is the daemon's default -- so a
// switch needs one; 30 is the window the spec's own scenario uses and the
// setting's help measures ("about 2 seconds at 30"). The daemon clamps it.
const ROOM_MEMORY_DEFAULT_S = 30;
let ambientLastOn = 0;
function roomSecondsOn() { return ambientLastOn || ROOM_MEMORY_DEFAULT_S; }

function setAmbientWindow(seconds) {
  const s = seconds > 0 ? seconds : 0;
  if (s) ambientLastOn = s;
  if (s === ambientSeconds) return;
  ambientSeconds = s;
  if (!s) discardAmbient();
  else ambientTrim();
}

// Hand the ring over, then forget it (AC-72).
//
// HTTP and NOT the socket, and that is the load-bearing half. Binary frames on
// the socket are fed straight into the live turn's pipeline, so ambient audio
// arriving there would become part of the UTTERANCE -- what the owner is
// treated as having said (AC-70). A megabyte queued on that socket would also
// sit in front of the live turn's own PCM and delay the very thing AC-69
// exists to protect, where a separate request cannot interleave with the
// stream and is concurrent by construction.
//
// Fire and forget: nothing is awaited and nothing is blocked on it. The daemon
// answers 202 and drops the clip if the turn has already moved on, and a
// failure is a line in the page's error region and never anything worse -- the
// microphone, the turn and the re-arm path are untouched (D-1).
function sendAmbient(turn) {
  if (!ambientRing.length) return;
  const frames = ambientRing;
  const body = new Uint8Array(ambientBytes);
  // Discarded HERE, at the moment it is handed over and before the request is
  // even built, so the page's copy is gone whatever the daemon does with it.
  discardAmbient();
  let at = 0;
  for (const f of frames) { body.set(new Uint8Array(f), at); at += f.byteLength; }
  fetch("/api/ambient?turn=" + encodeURIComponent(turn), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body,
  }).then((r) => {
    if (!r.ok) {
      showError("Butler refused the ambient buffer (HTTP " + r.status
                + "); this turn has no back-fill");
    }
  }).catch((e) => {
    showError("could not send the ambient buffer: " + (e.message || e));
  });
}

// --------------------------------------------------------------------------- //
// Audible cues -- synthesized, no assets, no network, and no AudioContext of
// their own.
//
// Three sounds, deliberately understated because this thing lives in a house:
//
//   listening   the LOCAL detector fired. Played instantly, before the
//               wake_candidate has left the socket -- the owner's call is
//               that immediate feedback beats the rare case where two
//               devices in earshot both beep and only one wins arbitration.
//   closing     the daemon is about to finalize the turn. It means "I am
//               about to submit -- keep talking if you are not done", so it
//               is the quietest and shortest of the three: it is the only
//               cue that can land on top of a talking user.
//   submitted   the utterance was taken. Two ascending notes, so it can
//               never be mistaken for either single-note cue.
//
// They share the TTS playback context. iOS caps concurrent AudioContexts at
// about four and the mic and the playback worklet already hold two, so a
// context per cue -- or even one more shared cue context -- is budget spent
// for nothing: an OscillatorNode mixes into playCtx.destination alongside
// the worklet perfectly well, and clearing the worklet's queue on barge-in
// does not touch a scheduled oscillator.
//
// ENVELOPE, not a gate. The reference implementation
// (.references/prototype-wake-ux.md section 1) uses a bare
// setValueAtTime(0.15) with no ramp, so its sine starts and stops on a
// discontinuity. That clicks, and a click is BROADBAND energy -- the last
// thing to put next to a wake detector and an energy gate. Every cue here
// ramps up linearly and decays exponentially to -80 dBFS before its
// oscillator stops.
// --------------------------------------------------------------------------- //
const CUE_LEAD_MS = 5;      // schedule just ahead of the audio clock
const CUE_EPS = 0.0001;     // -80 dBFS; exponentialRamp cannot reach zero
// The daemon promises at most one `closing` per pause. This is the client
// half of that promise, and it protects the TRANSCRIPT rather than the ears:
// every cue punches a hole in the captured audio (see onFrame), so a daemon
// that repeated it would chew through the user's sentence.
const CLOSING_CUE_MIN_GAP_MS = 1000;

// TAIL: how long the microphone stays muted AFTER the tone has stopped,
// because the room keeps handing it back. Derived, not felt:
//   * A domestic room's RT60 is around 0.4 s -- 60 dB in 400 ms, so 15 dB in
//     100 ms. A cue peaking at 0.13 only has to lose about 15 dB for its
//     reverberant residual to sit under the daemon's energy gate, so ~100 ms
//     of decay is what has to be covered.
//   * mic-worklet.js posts 512-sample frames, so suppression is applied in
//     32 ms steps and the last affected frame can straddle the boundary: +32.
//   * The rest is margin.
// Hence 150 / 130 / 90 ms, scaled with each cue's own level: the closing cue
// is about 9 dB quieter than the listening cue and needs proportionally less
// time to fall under the gate. Output latency (speaker) and input latency
// (mic) are added separately at play time, read off the AudioContexts
// themselves rather than buried in these numbers.
const CUES = {
  listening: {
    label: "listening", peak: 0.13, attackMs: 8, releaseMs: 30, tailMs: 150,
    tones: [{ hz: 880, atMs: 0, msLen: 120 }],
  },
  closing: {
    label: "closing", peak: 0.045, attackMs: 6, releaseMs: 24, tailMs: 90,
    tones: [{ hz: 520, atMs: 0, msLen: 60 }],
  },
  submitted: {
    label: "submitted", peak: 0.09, attackMs: 6, releaseMs: 26, tailMs: 130,
    tones: [{ hz: 660, atMs: 0, msLen: 70 }, { hz: 988, atMs: 80, msLen: 70 }],
  },
};

// Both of these are SERVER-SIDE settings (daemon/settings.py: cues,
// cue_volume) mirrored into memory here. Nothing is stored in the browser --
// the values arrive from GET /api/settings and from the `settings` broadcast,
// which is what stops the phone and the desk disagreeing about whether this
// house beeps (D-2, AC-8).
let cuesEnabled = true;
let cueVolume = 0.13;             // the level the tails below were derived at
const CUE_REF_VOLUME = 0.13;      // CUES[*].peak are calibrated to this
let cueFailed = false;
let micMuteUntil = 0;             // performance.now() deadline
let micMutedFrames = 0;
let lastClosingCueAt = 0;

function cueDurationMs(cue) {
  let end = 0;
  for (const t of cue.tones) end = Math.max(end, t.atMs + t.msLen);
  return end;
}

// Mute the microphone for `ms` from now. Windows never shorten: two cues in
// quick succession keep the later deadline.
function suppressMic(ms, why) {
  const until = performance.now() + ms;
  if (until > micMuteUntil) micMuteUntil = until;
  hud("hCue", `${why}: mic muted ${Math.round(ms)} ms`);
}

function playCue(name) {
  const cue = CUES[name];
  if (!cue) { showError("unknown cue: " + name); return; }
  if (!cuesEnabled) return;
  // No playback context means Start has not run or has failed: there is no
  // speaker to beep out of, and therefore nothing to suppress either.
  if (!playCtx) return;

  // cue_volume scales the calibrated peaks above. Zero means silent, and a
  // silent cue needs no microphone suppression: there is no tone to hide, and
  // an exponential ramp cannot start from zero in any case.
  const scale = cueVolume / CUE_REF_VOLUME;
  const peak = cue.peak * scale;
  if (!(peak > CUE_EPS)) { hud("hCue", "silent (cue_volume is 0)"); return; }

  const durMs = cueDurationMs(cue);
  // Latency the audio clock does not show. currentTime is the moment the
  // context RENDERS a sample, not the moment it leaves the speaker, and the
  // room's answer takes the mic's own input latency to reach us.
  const outMs = (playCtx.outputLatency || playCtx.baseLatency || 0) * 1000;
  const inMs = (micCtx && micCtx.baseLatency ? micCtx.baseLatency : 0) * 1000;
  // The tails above are derived for CUE_REF_VOLUME: the window has to last
  // until the cue's reverberant residual sits under the daemon's energy gate.
  // A LOUDER cue has further to fall, so the window grows with it -- 60 dB in
  // a domestic room's ~0.4 s RT60 is 6.7 ms per dB. A quieter one never
  // shortens it: the derived tail is already safe, and trading a measured
  // margin for a few milliseconds is how the cue ends up in the transcript.
  const tailMs = cue.tailMs + (scale > 1 ? 20 * Math.log10(scale) * (400 / 60) : 0);
  // Mute FIRST, and unconditionally. If scheduling throws below we have spent
  // a few silent frames, which is cheap; the other order can leak the tone.
  suppressMic(CUE_LEAD_MS + outMs + inMs + durMs + tailMs, cue.label);

  try {
    // iOS parks a context in the non-standard "interrupted" state as well as
    // "suspended"; resume anything that is not running.
    if (playCtx.state !== "running") playCtx.resume();
    const t0 = playCtx.currentTime + CUE_LEAD_MS / 1000;
    const attack = cue.attackMs / 1000, release = cue.releaseMs / 1000;
    for (const tone of cue.tones) {
      const start = t0 + tone.atMs / 1000;
      const end = start + tone.msLen / 1000;
      const hold = Math.max(start + attack, end - release);
      const osc = playCtx.createOscillator();
      const gain = playCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(tone.hz, start);
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(peak, start + attack);
      gain.gain.setValueAtTime(peak, hold);
      gain.gain.exponentialRampToValueAtTime(CUE_EPS, end);
      osc.connect(gain).connect(playCtx.destination);
      osc.onended = () => {
        try { osc.disconnect(); gain.disconnect(); } catch { /* already gone */ }
      };
      osc.start(start);
      osc.stop(end + 0.005);
    }
  } catch (e) {
    // Fail LOUD and once, then stop pretending. The reference swallows this
    // on purpose, so on a device where the context will not start the user
    // simply gets no feedback and no signal anywhere that it is missing.
    // A LOCAL latch: cues are off on this device until the page is reloaded,
    // and the daemon's `cues` setting is deliberately left alone. The failure
    // belongs to this browser's audio stack, and silencing every satellite in
    // the house from here would be one device's fault making a global
    // decision. The settings panel keeps showing what the daemon holds, so
    // say plainly that this page has stopped honouring it.
    cuesEnabled = false;
    if (!cueFailed) {
      cueFailed = true;
      showError("audible cue failed, cues are off ON THIS DEVICE for the rest "
                + "of the session (the daemon's setting is unchanged): "
                + (e.message || e));
    }
    hud("hCue", "failed");
  }
}

// --------------------------------------------------------------------------- //
// Microphone frames. Routing is SYNCHRONOUS so live audio can never be
// reordered behind an await; wake inference is pumped separately.
// --------------------------------------------------------------------------- //
function onFrame(pcmBuf, rms) {
  // CUE SUPPRESSION -- the thing the reference implementation does not do.
  // The microphone is live and the pre-roll ring has been running the whole
  // time, so capture has effectively already begun when a cue plays. Left
  // alone the tone reaches BOTH consumers: the recognizer, and the daemon's
  // energy gate, which reads its onset as speech and mis-endpoints the turn.
  // echoCancellation is not a defence we are entitled to assume (plan.md 4.2,
  // .references/audio-internals.md), and it is exactly what the reference
  // leaned on.
  //
  // Frames inside the window are ZEROED IN PLACE, never dropped, and the
  // difference is load-bearing:
  //   * The `preroll` header declares a byte count the daemon reads as
  //     milliseconds (16 kHz int16 = 32 B/ms). Zeroing keeps the flushed
  //     bytes literally the same frames, only quieter, so prerollBytes and
  //     what follows it on the wire cannot disagree. Dropping would need the
  //     count recomputed after the fact, in two places, forever.
  //   * The daemon's silence accounting and the endpointer's 8 s window are
  //     both byte-derived. A dropped frame shortens the timeline and every
  //     later timing decision is made against a clock that lost 32 ms.
  //   * The wake detector's 16 x 80 ms context assumes continuous input;
  //     splicing frames out of it lies to the detector about elapsed time.
  //   * A zeroed frame has a true RMS of 0, well under the gate's OFF
  //     threshold, so the gate reads the cue window as silence -- which is
  //     what it acoustically should have been.
  //
  // The cost, stated plainly: speech overlapping the window is destroyed, not
  // delayed. That is why the closing cue is the shortest and quietest one.
  if (performance.now() < micMuteUntil) {
    new Int16Array(pcmBuf).fill(0);
    rms = 0;
    micMutedFrames += 1;
    hud("hCue", `muted ${micMutedFrames} frames / `
                + Math.round(micMutedFrames * MIC_FRAME_MS) + " ms total");
  }
  // A DICTATION taps the frames here, after cue suppression and before the
  // detector (AC-181). The order matters twice: what is dictated is the same
  // audio the recognizer would have heard, minus Butler's own earcons; and
  // `pumpWake` is SKIPPED for the duration, which is R3 -- a wake phrase
  // landing inside a dictation would start a turn out of the words being
  // dictated, and the daemon would then have two claims on one recognizer,
  // which returns empty transcripts on both sides 9 times out of 9.
  if (dictating) pushDictation(pcmBuf);
  routeAudio(pcmBuf);
  if (!dictating) pumpWake(pcmBuf);
  hud("hRms", rms.toFixed(3));
}

function routeAudio(buf) {
  ringPush(buf);
  // The same frame the pre-roll ring gets, and deliberately so: this is AFTER
  // onFrame's cue suppression, so a zeroed earcon window is what both rings
  // hold (AC-68). It is pushed before the pendingWake return below, because
  // the room does not stop being worth holding while a wake is in flight.
  ambientPush(buf);

  if (pendingWake) {
    prerollFrames.push(buf);
    prerollBytes += buf.byteLength;
    hud("hPreroll", Math.round(prerollBytes / MIC_BYTES_PER_MS) + " ms");
    if (prerollBytes > PREROLL_MAX_MS * MIC_BYTES_PER_MS) {
      showError("pre-roll exceeded " + PREROLL_MAX_MS
                + " ms with no capture_start; abandoning this wake");
      goListen();
    }
    return;   // still buffering: the daemon has not opened the turn yet
  }

  if (state === CAPTURE) sendPcm(buf);
}

// --------------------------------------------------------------------------- //
// ATTACHMENTS -- the paperclip and the camera (AC-205, docs/attachments.md)
//
// `POST /api/attach?device=<id>&name=<filename>` with the FILE as the body.
// NOT the socket, and that is the same argument the ambient ring is built on:
// binary frames there are fed straight into the live turn's ASR pipeline, so
// an image arriving that way would be handed to the recognizer as audio.
//
// The daemon hangs it on THIS DEVICE's session and the next turn takes it,
// spoken or typed -- so nothing here sends an attachment with a message. The
// chips are what the owner has put up; sending is a separate act.
// --------------------------------------------------------------------------- //
let attached = [];

function paintAttached() {
  const box = $("attached");
  box.replaceChildren();
  box.hidden = !attached.length;
  for (const att of attached) {
    const chip = document.createElement("div");
    chip.className = "attchip";
    const name = document.createElement("span");
    name.className = "n";
    // textContent, never innerHTML: this is a filename off the owner's disk
    // and the daemon has already flattened it, but the page does not rely on
    // the daemon having done so.
    name.textContent = att.name;
    const kind = document.createElement("span");
    kind.className = "k";
    // What Butler made of it, not what the file claims: the daemon sniffed
    // the bytes, and a document it could read says how much it read (AC-198).
    kind.textContent = att.kind === "image" ? "image"
      : (att.chars ? att.chars + " chars" : "document");
    const drop = document.createElement("button");
    drop.type = "button";
    drop.setAttribute("aria-label", "Remove " + att.name);
    drop.textContent = "x";
    drop.onclick = () => {
      // Removed from the PAGE only. The daemon is not told, because there is
      // no route that could be: the file is already pending on the session
      // and the next turn takes whatever is there. Stated rather than hidden
      // -- see docs/attachments.md.
      attached = attached.filter((a) => a.id !== att.id);
      paintAttached();
      logNote("removed from this message; Butler still has the file");
    };
    chip.append(name, kind, drop);
    box.append(chip);
  }
}

async function sendAttachment(file, btn) {
  if (!file) return;
  clearErrors();
  if (btn) btn.classList.add("busy");
  try {
    const r = await fetch("/api/attach?device=" + encodeURIComponent(DEVICE_ID)
                          + "&name=" + encodeURIComponent(file.name || "file"),
                          { method: "POST", body: file });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) {
      // The daemon's own words. It refuses by SIZE with the number and by
      // TYPE with what it does keep, and both are sentences the owner can act
      // on -- "that did not work" is the report that helps nobody (AC-7b).
      showError(doc.error || ("that file was not accepted (HTTP "
                              + r.status + ")"));
      return;
    }
    attached.push(doc);
    paintAttached();
  } catch (e) {
    showError("that file did not reach Butler: " + (e.message || e));
  } finally {
    if (btn) btn.classList.remove("busy");
  }
}

// THE CAMERA IS OPENED ON THE PRESS AND CLOSED ON THE SHOT (AC-205). One
// frame, drawn into a canvas, encoded as a PNG and posted like any other
// file -- so there is one upload path and the daemon cannot tell a photograph
// from a file. The stream is stopped in a `finally`: a camera left running is
// the one failure this control has, and it is invisible on a tablet.
async function takePhoto() {
  // The camera no longer owns its own composer slot -- it is a menu item
  // behind the paperclip now (mocks/q-familiar.html item 1) -- so the busy
  // treatment shows on the control that is still on screen after the menu
  // closes, which is `#attachBtn`.
  const btn = $("attachBtn");
  clearErrors();
  btn.classList.add("busy");
  let stream = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" }, audio: false });
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    // One frame is not ready the instant `play()` resolves on every browser;
    // waiting for the metadata is what makes the canvas the camera's real
    // size rather than 0 x 0.
    if (!video.videoWidth) {
      await new Promise((done) => { video.onloadedmetadata = done; });
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    video.pause();
    video.srcObject = null;
    const blob = await new Promise((done) => canvas.toBlob(done, "image/png"));
    if (!blob) throw new Error("the camera produced no image");
    blob.name = "photo.png";
    await sendAttachment(blob, null);
  } catch (e) {
    showError("no photo was taken: " + (e.message || e));
  } finally {
    try { if (stream) stream.getTracks().forEach((t) => t.stop()); }
    catch { /* already stopped */ }
    btn.classList.remove("busy");
  }
}

// --------------------------------------------------------------------------- //
// DICTATION -- the composer's one microphone (AC-181, docs/mic-optional.md,
// mocks/q-familiar.html item 2)
//
// The owner's original ask: "a wave button so user can press to start asr and
// press again to add to chat input, independently of mic button status". That
// shipped as a SEPARATE wave button beside the mic button, which is what the
// owner then named the worst control on the page (CLAUDE.md backlog,
// 2026-09-11 item 7): two adjacent audio glyphs, one tooltip apart. The
// mechanism did not change -- `POST /api/dictate`, which has existed since the
// desk hotkey shipped (AC-113, AC-115, AC-116): a raw 16 kHz mono int16 body
// in, `{"text": ...}` out, nothing spoken, nothing recorded, no brain
// involved -- only which BUTTON reaches it. `MIC_BUTTON` (`#micBtn`) is now
// that button, and arming the wake word moved to the header pill alone
// (`micAction()`, above) so the two controls stopped disagreeing about what a
// microphone glyph in the composer does.
//
// ONE ROUTE, TWO CALLERS, deliberately (D-18). A second transcribe-a-clip path
// would be a second implementation of one contract, and this one already
// carries the ceiling, the busy REFUSAL (409, rather than two empty decodes)
// and `paste_safe`.
//
// NOTHING IS SENT. The text goes into the composer at the caret and the owner
// decides what to do with it; that is the whole difference from pressing
// Enter, and it is the owner's words "add to chat input".
// --------------------------------------------------------------------------- //

// The daemon's own ceiling, learned from the settings answer. The default is
// the schema's, so a page that has not been told yet still stops somewhere
// rather than buffering the room until the tab dies. The daemon refuses a
// larger body with a 413; stopping here means the words already spoken are
// still transcribed instead of being thrown away with the request.
let dictateMaxSeconds = 120;

function dictateCeilingBytes() {
  return dictateMaxSeconds * 1000 * MIC_BYTES_PER_MS;
}

function pushDictation(buf) {
  dictateFrames.push(buf);
  dictateBytes += buf.byteLength;
  hud("hDictate", Math.round(dictateBytes / MIC_BYTES_PER_MS / 1000) + " s");
  if (dictateBytes >= dictateCeilingBytes()) {
    logNote("dictation reached this daemon's " + dictateMaxSeconds
            + "s ceiling; transcribing what was said");
    endDictation();
  }
}

function paintDictate() {
  const btn = MIC_BUTTON;
  if (!btn) return;
  btn.setAttribute("aria-pressed", dictating ? "true" : "false");
  btn.setAttribute("aria-label",
                   dictating ? "Stop dictating and put the text in the box"
                             : "Dictate into the message box");
  btn.classList.toggle("on", dictating);
  // Never while a turn is in flight. The daemon's 409 is the real net -- it
  // is the one thing that can see a recognizer already in use -- but a page
  // that offers the control and then reports a refusal every time is a
  // control that lies about what it can do (AC-7b).
  btn.disabled = !dictating
                 && (state === CAPTURE || state === THINKING || state === SPEAK);
}

async function toggleDictation() {
  if (dictating) { await endDictation(); return; }
  clearErrors();
  try {
    await openMicGraph();
  } catch (e) {
    micRefused = true;
    raiseErrLine();
    // "the wave button" was the composer control's name before the wave
    // button and the mic button merged into `#micBtn` (AC-181); fixed here
    // to match, and to the same plain remedy `arm()` shows (review item 4,
    // 2026-09-26).
    reportMicFailure(e);
    paint();
    return;
  }
  dictateFrames = []; dictateBytes = 0;
  dictating = true;
  paintDictate();
  logNote("dictating -- press the microphone button again to put the words "
          + "in the box");
}

async function endDictation() {
  if (!dictating) return;
  dictating = false;
  const frames = dictateFrames, bytes = dictateBytes;
  dictateFrames = []; dictateBytes = 0;
  paintDictate();
  // Whichever of listening and dictation finishes last closes the graph. A
  // dictation on an otherwise-silent page must not leave the microphone open,
  // and standing listening down mid-dictation must not cut this one short.
  if (!micLive) closeMicGraph();
  if (!bytes) {
    logNote("");
    showError("nothing was recorded: the microphone produced no audio");
    return;
  }
  const body = new Uint8Array(bytes);
  let at = 0;
  for (const f of frames) { body.set(new Uint8Array(f), at); at += f.byteLength; }
  logNote("transcribing " + (bytes / MIC_BYTES_PER_MS / 1000).toFixed(1) + "s...");
  try {
    // Fetched rather than fired and forgotten: this route answers the TEXT and
    // every failure carries a status the caller can tell apart -- 409 the
    // recognizer is busy, 413 too long, 422 nothing was said, 502 the
    // recognizer fell over. The daemon's own words are shown, because a
    // dictation that silently produced nothing is indistinguishable from a
    // microphone that is not working (AC-7b).
    const r = await fetch("/api/dictate", { method: "POST", body });
    const answer = await r.json().catch(() => null);
    if (!r.ok) {
      throw new Error((answer && answer.error) || ("HTTP " + r.status));
    }
    const text = (answer && answer.text) || "";
    if (!text) throw new Error("the daemon answered with no text");
    logNote("");
    // A focused terminal takes the words as typed text, no Enter (AC-765).
    if (!terminals.dictate(text)) insertIntoComposer(text);
  } catch (e) {
    logNote("");
    showError("that dictation did not reach the box: " + (e.message || e));
  }
}

// At the caret, never appended blindly: the owner may be dictating into the
// middle of something they typed. A joining space is added only when the text
// before the caret does not already end in whitespace, which is `logReply`'s
// own rule for the same reason.
function insertIntoComposer(text) {
  const box = $("input");
  const from = box.selectionStart == null ? box.value.length : box.selectionStart;
  const to = box.selectionEnd == null ? from : box.selectionEnd;
  const before = box.value.slice(0, from), after = box.value.slice(to);
  const glue = before && !/\s$/.test(before) ? " " : "";
  box.value = before + glue + text + after;
  const caret = (before + glue + text).length;
  box.focus();
  box.setSelectionRange(caret, caret);
  growInput();
}

// The detector wants Float32 at 16 kHz. Copy the samples out synchronously so
// the caller's buffer is never shared with the inference chain.
function pumpWake(buf) {
  if (!wake) return;
  const i16 = new Int16Array(buf);
  const f = new Float32Array(i16.length);
  for (let k = 0; k < i16.length; k++) f[k] = i16[k] / 32768;
  wake.feed(f).then((r) => {
    if (!r) return;
    if (r.score != null) {
      hud("hScore", r.score.toFixed(3));
      // The trace plots what the DETECTOR said, never a loudness estimate.
      if (viz) viz.pushScore(r.score);
    }
    if (!r.fired) return;
    if (performance.now() < wakeSuppressUntil) {
      hud("hWake", "suppressed (cooldown)");
      return;
    }
    onWakeFire(r.score, r.word);
  }).catch((e) => showError("wake detector failed: " + (e.message || e)));
}

function onWakeFire(score, word) {
  if (!micLive) return;
  if (pendingWake) return;                        // one wake in flight
  if (state === CAPTURE) return;                  // already capturing this turn
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    hud("hWake", "fired while offline (dropped)");
    return;
  }
  // Barge-in during thinking/speaking: stop talking immediately. The daemon
  // abandons the old turn when it sees the candidate; dropping the local
  // playback queue here is what makes the interruption feel instant.
  if (state === SPEAK || state === THINKING) {
    stopPlayback();
    currentTurn = null;
    speakingTurn = null;
  }
  // The cue plays HERE: the instant the local detector fired, before the
  // wake_candidate has even left the socket, and before the daemon has
  // arbitrated. Note the order -- playback is dropped FIRST, so on a barge-in
  // the cue never overlaps the reply it is interrupting. The reference does
  // the reverse and both land in the new turn's opening frames.
  playCue("listening");
  beginPreroll();
  hud("hWake", "fired " + word + " " + (score == null ? "?" : score.toFixed(3)));
  // `word` is the fired id (AC-807): the daemon refuses one that is off.
  send({ type: "wake_candidate", word, score: score == null ? 0 : score,
         ts_ms: Date.now() });
  if (wakeTimer) clearTimeout(wakeTimer);
  wakeTimer = setTimeout(() => {
    if (!pendingWake) return;
    showError("no capture_start or wake_reject within "
              + WAKE_DECISION_TIMEOUT_MS + " ms; discarding the pre-roll");
    goListen();
  }, WAKE_DECISION_TIMEOUT_MS);
}

// --------------------------------------------------------------------------- //
// TTS playback: ONE worklet, one sample queue, drained at hardware rate.
// Never per-chunk source.start(t) scheduling (4.7).
// --------------------------------------------------------------------------- //
// Opened at BOOT, not by `start()` (AC-177). It is structurally independent of
// the microphone -- it creates an output context and a worklet and touches
// nothing the capture path owns -- and putting it inside the `getUserMedia`
// try block meant a page with no microphone had no speaker either, so a typed
// turn's reply would have arrived silently with nothing reporting it (AC-7b).
//
// IDEMPOTENT, because `start()` still calls it and the page may already have
// one: a second context would leave the first connected to the destination and
// the two would play over each other.
//
// A context created outside a user gesture starts SUSPENDED. That is not an
// error and must not be treated as one; every path that actually makes sound
// resumes it first (`playChunk`, `playCue`, the `speak_start` handler).
async function openPlayback() {
  if (playCtx) return;
  playCtx = new AudioContext({ sampleRate: TTS_RATE });
  await playCtx.audioWorklet.addModule("tts-playback.worklet.js");
  ttsNode = new AudioWorkletNode(playCtx, "tts-playback-processor",
                                 { outputChannelCount: [1] });
  ttsNode.port.onmessage = (e) => {
    const m = e.data;
    if (!m) return;
    // `state` is a UI hint only, and `paint()` is what it hints: whether the
    // header says "speaking". It is NOT the drain signal: postMessage is
    // async, so `playing` can still read false for samples already posted.
    if (m.type === "state") { ttsPlaying = !!m.playing; paint(); return; }
    if (m.type === "drained") { onDrained(m.token); return; }
  };
  ttsNode.connect(playCtx.destination);
  if (playCtx.sampleRate !== TTS_RATE) {
    // Documented safety net, not a silent fudge: say so in the HUD.
    hud("hPlay", `ctx ${playCtx.sampleRate} Hz (resampling from ${TTS_RATE})`);
  } else {
    hud("hPlay", TTS_RATE + " Hz 1:1");
  }
}

function playChunk(arrayBuffer) {
  if (!ttsNode) return;
  // The context is opened at boot now, which means it is routinely created
  // WITHOUT a user gesture and therefore starts suspended. `playCue` has
  // resumed since it was written and the `speak_start` handler does too; this
  // one did not, because until now nothing could reach it before a click had
  // armed the microphone. A suspended context accepts every sample silently.
  if (playCtx.state !== "running") playCtx.resume();
  // pcm16ToFloat32 reads the socket buffer through a DataView and returns a
  // FRESH array; resampleLinear either returns that same fresh array (rates
  // match) or another fresh one. Either way the buffer we transfer has exactly
  // one consumer -- the socket's own buffer is never handed to the worklet.
  const f32 = resampleLinear(pcm16ToFloat32(arrayBuffer), TTS_RATE,
                             playCtx.sampleRate);
  ttsNode.port.postMessage({ type: "write", samples: f32.buffer },
                           [f32.buffer]);
}

function stopPlayback() {
  ttsPlaying = false;
  // Dropping the queue drops the drain with it, on both sides of the port:
  // those samples will never leave the speaker, so reporting them as played
  // would be a lie the daemon acts on.
  cancelDrain();
  if (ttsNode) ttsNode.port.postMessage({ type: "clear" });
}

function cancelDrain() {
  if (drainTimer) { clearTimeout(drainTimer); drainTimer = null; }
  pendingDrain = null;
}

// speak_end arrived: no more audio is coming, but the speaker is still working
// through the queue. Ask the worklet to tell us when the last queued sample has
// actually been played.
function awaitDrain(turn) {
  cancelDrain();
  if (!ttsNode) { onQuiet(turn, true); return; }   // no playback path at all

  drainSeq += 1;
  const token = drainSeq;
  pendingDrain = { token, turn };
  ttsNode.port.postMessage({ type: "drain", token });

  // The bound is the audio we actually received plus a grace -- the same
  // arithmetic hub.py's _await_playback uses, so the two sides give up at
  // roughly the same moment instead of one holding the other hostage.
  const budget = Math.min(DRAIN_MAX_MS,
                          audioBytes / TTS_BYTES_PER_MS + DRAIN_GRACE_MS);
  drainTimer = setTimeout(() => {
    drainTimer = null;
    if (!pendingDrain || pendingDrain.token !== token) return;
    pendingDrain = null;
    showError("playback did not drain within " + Math.round(budget) + " ms ("
              + (ttsPlaying ? "worklet still playing" : "worklet idle")
              + "); re-arming anyway");
    // Deliberately WITHOUT playback_done: we do not know the speaker went
    // quiet, and claiming it would release the daemon's turn on a guess. The
    // daemon has its own bound and re-arms by itself; we re-arm here (D-1).
    onQuiet(turn, false);
  }, budget);
}

function onDrained(token) {
  if (!pendingDrain || pendingDrain.token !== token) return;   // stale answer
  const turn = pendingDrain.turn;
  cancelDrain();
  onQuiet(turn, true);
}

// The one place the reply ends. `confirmed` says whether the speaker is known
// to be quiet, which is the only thing that may be reported to the daemon.
function onQuiet(turn, confirmed) {
  if (confirmed && turn != null && turn === currentTurn) {
    send({ type: "playback_done", turn });
    hud("hAudio", (audioBytes / 1024).toFixed(1) + " KiB played");
  }
  finishSpeak(turn);
}

function finishSpeak(turn) {
  cancelDrain();
  speakingTurn = null;
  // The `cooldown_ms` firing-suppression window lives in goListen() and
  // covers both the last few ms still inside the output device's own buffer
  // and the detector's 1280 ms of context refilling with post-reply audio.
  // `turn === currentTurn` too: a turn ADOPTED when its conversation came
  // back on screen (`settleScreenTurn`) had its `speak_start` routed to the
  // background, so it is in THINKING and its `speak_end` is still its end --
  // measured on the scratch daemon, the page sat on "thinking" with Stop and
  // a running clock after the daemon said done. A NEWER turn in THINKING has
  // a different id and is left alone, which is what the SPEAK test guarded.
  if (state === SPEAK || (turn != null && turn === currentTurn)) goListen();
}

// --------------------------------------------------------------------------- //
// WebSocket -- protocol v1
// --------------------------------------------------------------------------- //
function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  try { ws.send(JSON.stringify(obj)); return true; }
  catch (e) { showError("send failed: " + (e.message || e)); return false; }
}

function sendPcm(buf) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try { ws.send(buf); }
  catch (e) { showError("pcm send failed: " + (e.message || e)); }
}

// The socket is the PAGE's and not the microphone's (AC-175). It is opened at
// boot, so a page that never touches a microphone still receives `ready`,
// restores the conversation, learns `turn_mode` and `end_phrases`, fills the
// engine chips and clears `linkSkin` -- all of which used to be unreachable
// without a device and a click, which is why `probe_page.py` needed `--live`
// and a fake audio device to see ANY daemon-supplied value.
//
// IDEMPOTENT against a socket that is already up or on its way, because
// `start()` still calls it: a second WebSocket would give the daemon two
// sessions for one page, and the first one's `onclose` would then reconnect a
// third.
// WHAT THIS PAGE CAN DO NOW: wake-listening and a speaker. Read off the
// things themselves, and RE-SENT (`caps`) whenever either changes, because
// `hello` goes out once at boot -- before the microphone is armed and usually
// before the speaker exists. The daemon's announcer reads it: only a desk
// that is listening and can play hears a run's line (AC-470).
function pageCaps() {
  return { wake: !!(micLive && wake), tts: !!ttsNode };
}
function sendCaps() { send({ type: "caps", caps: pageCaps() }); }

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN
             || ws.readyState === WebSocket.CONNECTING)) return;
  linkWanted = true;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  setLink("connecting", false);
  ws = new WebSocket(`${scheme}//${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    setLink("connected", true);
    // `caps` is what this page can DO right now (`pageCaps`), read off the
    // things themselves rather than asserted. The daemon's announcer reads it
    // (AC-470), and the `caps` frame keeps it current after this.
    send({ type: "hello", proto: 1, device_id: DEVICE_ID, name: DEVICE_NAME,
           caps: pageCaps() });
  };

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) { onBinary(ev.data); return; }
    let m;
    try { m = JSON.parse(ev.data); }
    catch (e) { showError("unparseable frame from daemon: " + (e.message || e)); return; }
    onMessage(m);
  };

  // A failed reconnect while the hero is ALREADY saying the link is gone adds
  // nothing (AC-91). Measured before this: a 25 s outage stacked six identical
  // `websocket error` boxes, one per retry, capped only by the six-line trim
  // -- so the page said the same thing seven times and none of the seven said
  // why. The FIRST one still shows, because a socket that fails while the page
  // believes it is connected is news; the retries after it are not.
  ws.onerror = () => { if (!linkSkin) showError("websocket error"); };

  ws.onclose = () => {
    setLink("disconnected", false);
    // Say it in the HERO and not only in the collapsed diagnostics drawer
    // (AC-91). Raised here rather than at `connect()` so arming a page onto a
    // daemon that answers does not flash red for the length of a handshake:
    // a socket that fails to open lands here too, because the browser fires
    // close on a connection that was never established. `linkWanted` is the
    // flag that says this page still means to be connected; nothing on the
    // page closes the socket deliberately any more, so this reads false only
    // before the first `connect()`.
    if (linkWanted) setLinkSkin(everReady ? "reconnecting" : "nolink");
    // A dropped socket is a terminal path like any other: it must never leave
    // the page in a state that ignores the microphone (D-1).
    goListen();
    // No frame of any turn this socket drove will reach the next one, so
    // they are the daemon's to report now: `settleScreenTurn` asks it on the
    // `ready` that follows.
    chatTurns.clear();
    if (linkWanted && !reconnectTimer) {
      reconnectTimer = setTimeout(() => { reconnectTimer = null; if (linkWanted) connect(); },
                                  RECONNECT_MS);
    }
  };
}

function onBinary(buf) {
  // Binary down the socket is speech, and speech only exists between
  // speak_start and speak_end. The daemon already filters by turn; dropping
  // anything outside the window is the client half of the same rule.
  if (speakingTurn == null) return;
  playChunk(buf);
  audioBytes += buf.byteLength;
  hud("hAudio", (audioBytes / 1024).toFixed(1) + " KiB");
}

// Turn-scoped messages from a turn we no longer own are discarded (D-6).
function stale(m) {
  return m.turn != null && m.turn !== currentTurn;
}

// SCHEMA-DERIVED LABELS for the "settings changed" note (item 8, review
// 2026-09-26): this page names no setting of its own (AC-41), so the human
// words come from the same GET /api/settings the settings panel reads --
// fetched again here rather than reaching into `web/settings.js`'s private
// state, because that module keeps its parsed rows to itself. Fired once at
// load; a `settings` frame that lands before it resolves falls back to the
// raw key for that one note only, and every note after it is labelled.
let settingsLabels = {};
fetch("/api/settings").then((r) => (r.ok ? r.json() : null)).then((doc) => {
  for (const s of ((doc && doc.settings) || [])) settingsLabels[s.key] = s;
}).catch(() => { /* the raw key is still a truthful note */ });

// "Agent model: big-pickle", not "harness_model: big-pickle" -- `spec.label`
// is the schema's own name for the setting, and `spec.labels` is the same
// schema's display string for a `choice` value, keyed by the raw value it
// replaces (settings.js does the same lookup for its own rows).
function settingsChangedLine(changed) {
  return Object.keys(changed).map((key) => {
    const spec = settingsLabels[key];
    const label = (spec && spec.label) || key;
    const value = changed[key];
    // A MULTILINE value (SOUL.md, USER.md) is a document, not a value: the
    // note printed the owner's whole soul into the chat area (2026-09-27).
    // Say WHAT changed; the text is on the settings page. Keyed on the
    // schema's `kind` and on a line break, never on a setting's name (AC-41).
    if ((spec && spec.kind === "multiline")
        || (typeof value === "string" && value.includes("\n"))) {
      return label + ": changed";
    }
    const shown = spec && spec.labels
      && Object.prototype.hasOwnProperty.call(spec.labels, value)
      ? spec.labels[value] : value;
    return label + ": " + shown;
  }).join(", ");
}

function onMessage(m) {
  // A TYPED turn's id arrives HERE and nowhere else (AC-163, D-6). The spoken
  // path adopts it from `capture_start`; a typed turn sends none, because
  // nothing is being captured, so the `final` frame is the first the page sees
  // and is what names the turn. This runs BEFORE the switch, because
  // `stale(m)` inside the `final` case would otherwise discard a frame whose
  // turn this page does not yet know it owns -- `currentTurn` is still null,
  // or still the capture the typed message just cancelled (R7).
  //
  // Guarded by `typedPending` rather than by `reason` alone: the page adopts a
  // turn only when it asked for one. A frame nobody typed for is left to the
  // ordinary staleness rule.
  if (m.type === "final" && m.reason === "typed" && typedPending > 0) {
    typedPending -= 1;
    currentTurn = m.turn;
    hud("hTurn", String(m.turn));
    // R7: a typed message CANCELS a capture in flight, on the daemon through
    // `_barge_in`. This is the page's half of the same teardown -- the buffer
    // for a wake that is now over, the closing warning it may have raised, and
    // the partial text under the composer.
    discardPreroll();
    hideClosing();
    renderLive("", "");
  }
  // A RECIPE RUN BY VOICE (docs/automations.md 8, AC-451): the daemon opened
  // a chat for this device (`opened`, which re-declares `liveChatId` at once)
  // and then began a turn of THIS device in it, whose `final` carries reason
  // "recipe". Nobody typed it, so the rule above does not adopt it; it is
  // adopted here only for the conversation on screen, and never over a
  // capture the owner is in the middle of.
  if (m.type === "final" && m.reason === "recipe" && m.chat
      && m.chat === liveChatId && state !== CAPTURE) {
    currentTurn = m.turn;
    hud("hTurn", String(m.turn));
  }
  // A PHONE MESSAGE (docs/messaging.md MS-16, AC-519, AC-533): the owner
  // wrote from WhatsApp or Telegram and the daemon runs it as a turn of its
  // own, nothing spoken here, whose frames every page is shown. Its `final`
  // carries the channel as its reason (PHONE_VIA); it is adopted, like a
  // recipe run, only for the conversation on screen and never over a capture.
  if (m.type === "final" && PHONE_VIA[m.reason] && m.chat
      && m.chat === liveChatId && state !== CAPTURE) {
    currentTurn = m.turn;
    hud("hTurn", String(m.turn));
  }
  // AN ANNOUNCEMENT (plan.md AC-14, docs/automations.md AU-18, AC-470): the
  // daemon MINTED a turn on this desk to say one line nobody asked for, and
  // this frame names it so the `speak_start` / audio / `speak_end` after it
  // are not dropped as stale. The daemon only sends it to an idle desk; the
  // page adopts it only when it truly is idle, and never over a capture.
  if (m.type === "announce") {
    if (currentTurn == null && state === WAKE) {
      currentTurn = m.turn;
      hud("hTurn", String(m.turn));
    }
    return;
  }
  // A refusal for a typed message (empty, or past the daemon's ceiling) is an
  // `error` frame with no turn on it, and it means no `final` is coming. Left
  // uncounted, the next spoken turn's final would be adopted as this one's.
  if (m.type === "error" && m.turn == null && typedPending > 0) typedPending -= 1;

  // A correction about a turn's PAST text, on screen or not, current or
  // already closed -- so it bypasses the routing and staleness below, which
  // would drop it or park a ghost turn for it (applyUnspoken).
  if (m.type === "unspoken") { applyUnspoken(m); return; }

  // Something a turn SHOWED (docs/rich-chat.md section 11): broadcast with
  // a chat and no turn, so it is routed by chat alone (applyArtifactFrame).
  if (m.type === "artifact") { applyArtifactFrame(m); return; }

  // ROUTED BY CONVERSATION (e2e audit S10d): a turn frame for a chat that is
  // not on screen updates that chat's bookkeeping and touches nothing drawn
  // -- not the transcript, not the state, not the watchdog. A frame naming
  // the on-screen turn but another chat is a turn that started here and has
  // since gone off screen (a capture, a typed turn that raced a click), so
  // it is parked first. Frames with no chat (endpoint mode) are unchanged.
  if (m.turn != null && typeof m.chat === "string" && m.chat) {
    if (!trackTurnFrame(m)) return;
    if (m.chat !== liveChatId) {
      if (m.turn === currentTurn) parkScreenTurn();
      if (m.type === "progress") paintRowProgress(m);
      if (m.type === "error") showError("daemon: " + (m.message || "(no message)"));
      // The tree says what that conversation is doing now (S3b).
      if (endsTurn(m)) refreshWhenSettled(m.chat);
      return;
    }
  }
  // ANY frame of the turn on screen is word from Butler (AC-393), and it
  // re-arms the page's own give-up net -- which, with a beat every 5 s, now
  // fires only when the DAEMON has gone silent (docs/turn-feedback.md 3).
  if (m.turn != null && !stale(m)) lastWordAt = Date.now();
  armWatchdog();

  switch (m.type) {

    case "ready":
      // proto, mode, device_id, stt, tts, brain -- all server-authored, none
      // persisted. The page names no engine of its own and asserts no key set:
      // each info dict is whatever its adapter reports, so a missing field
      // shows "?" rather than blanking the whole chip.
      if (m.proto !== 1) {
        showError(`daemon speaks protocol v${m.proto}, this page speaks v1`);
      }
      applyMode(m.mode);
      applyTurnMode(m.turn_mode, m.capture_ceiling_ms);
      // Before `settings.refresh()` below, whose answer is what arms the page.
      applyWakeConfig(m.wake);
      $("deviceLine").textContent =
        DEVICE_NAME + " - " + (m.device_id || DEVICE_ID).slice(0, 8);
      hud("hStt", m.stt ? `${m.stt.arch || "?"} / ${m.stt.backend || "?"}` : "-");
      // Which brain is answering matters most of the three: the fake matches
      // five substrings and says "Let me think about that" to everything else,
      // which from across the room looks exactly like a real model being
      // unhelpful. The chip is how you tell those apart (AC-42).
      hud("hTts", m.tts ? (m.tts.engine || "?") : "-");
      hud("hBrain", m.brain ? (m.brain.engine || "?") : "-");
      hud("hProto", "v" + m.proto);
      // Re-read the settings on every (re)connect. The socket reconnects by
      // itself, so this is also the moment after a daemon RESTART -- exactly
      // when the restart-only settings have finally taken effect and when the
      // schema itself may have gained or lost a field.
      settings.refresh();
      // The conversation, on every (re)connect and therefore after a daemon
      // restart: that is exactly the moment AC-35 is about, and the page must
      // say whether the agent came back knowing anything.
      // A TURN STILL RUNNING is recovered by the same call (AC-236, S16):
      // `loadSession` draws the record and `settleScreenTurn` asks the daemon
      // whether this conversation is in flight, then watches it with the
      // daemon's own clock. Both fetch, so both land after `goListen()` below
      // has cleared the old state -- the last writers, not a race with it.
      loadSession(m.session);
      clearErrors();
      // The link skin clears itself, with no user action, at the one message
      // that proves the daemon is actually talking to us (AC-91). Deliberately
      // NOT at `onopen`: a socket that opens and then says nothing is a daemon
      // this page still cannot reach, and claiming otherwise is the same lie
      // one layer down. `everReady` is what later tells "it went away" from
      // "it was never there".
      everReady = true;
      setLinkSkin(null);
      goListen();
      break;

    case "capture_start": {
      // A turn has started, so any voice preview stops (AC-109). It cannot
      // stop itself: an audition is deliberately not a turn and settings.js
      // has no handle on the turn state, which is why the mount hands this
      // out. Done at capture_start rather than at speak_start so the preview
      // is gone before the room is being transcribed, not merely before
      // Butler answers into it.
      settings.stopAudition();
      // Nothing is restored here any more, and that is the phase-4 change: a
      // turn belongs to the record Butler is holding, and the conversation on
      // screen IS that record because opening one moved the daemon's own
      // pointer (AC-240). `backToLive()` stood here and had to run
      // synchronously, from the copy the last `ready` left behind, because the
      // wake word has already fired by now and a fetch would race the owner's
      // first sentence -- the whole problem is gone rather than solved faster.
      // The DAEMON mints the id; the page adopts it and never invents one.
      currentTurn = m.turn;
      hud("hTurn", String(m.turn));
      if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; }
      // A new turn buries the old reply: whatever we were still waiting to
      // hear finish belongs to a turn nobody owns now, so never report it.
      cancelDrain();
      lastClosingCueAt = 0;     // the rate limit is per turn, not per page
      const ms = pendingWake ? flushPreroll(m.turn) : 0;
      if (!ms) hud("hPreroll", "0 ms (no buffer)");
      // The ambient ring goes as its OWN request, HERE and not in onWakeFire
      // (AC-68): the DAEMON mints the turn id and this page never invents one
      // (D-6), so capture_start is the first moment there is one to name. Not
      // awaited, so the live utterance's own PCM is never behind it (AC-69).
      sendAmbient(m.turn);
      renderLive("", "");
      $("reply").hidden = true;
      $("reply").textContent = "";
      $("tierPill").hidden = true;
      logNote("");
      setState(CAPTURE);
      break;
    }

    case "wake":
      // A word turned on or off, or the threshold or cooldown changed, on
      // any device (AC-805).
      applyWakeConfig(m);
      break;

    case "wake_reject":
      hud("hWake", "rejected: " + (m.reason || "?"));
      logNote("wake rejected (" + (m.reason || "?") + ")");
      goListen();
      break;

    case "partial":
      if (stale(m)) return;
      // Still talking, so the warning is void. The daemon is entitled to a
      // fresh one at the next pause (AC-6c).
      hideClosing();
      renderLive(m.committed || "", m.tentative || "");
      break;

    case "closing":
      // "I am about to submit -- keep talking if you are not done." The
      // daemon sends this at most once per pause. It is handled DEFENSIVELY:
      // an older daemon never sends it at all and nothing here depends on it
      // arriving, so its absence costs one cue and no behaviour.
      if (stale(m)) return;
      if (state !== CAPTURE) return;   // no turn to keep talking into
      if (performance.now() - lastClosingCueAt < CLOSING_CUE_MIN_GAP_MS) return;
      lastClosingCueAt = performance.now();
      playCue("closing");
      showClosing();
      break;

    case "final": {
      if (stale(m)) return;
      const t = (m.timings || {});
      hud("hFirst", t.first_partial_ms != null ? t.first_partial_ms + " ms" : "-");
      hud("hFinal", t.finalize_ms != null ? t.finalize_ms + " ms" : "-");
      hud("hReason", m.reason || "-");
      lastTimings = [t.first_partial_ms != null ? "first " + t.first_partial_ms + "ms" : "",
                     t.finalize_ms != null ? "final " + t.finalize_ms + "ms" : "",
                     m.reason || ""].filter(Boolean).join(" - ");
      renderLive("", "");
      hideClosing();
      const text = (m.text || "").trim();
      if (!text) {
        // AC-6/D-7: the final IS the turn end. The daemon has re-armed; so do we.
        logNote("heard nothing (" + (m.reason || "empty") + ")");
        goListen();
      } else {
        agentSpent();                  // a taken turn fixes the chat's agent
        // Confirm the utterance was TAKEN. An empty final took nothing, so
        // that branch gets the note above and no cue -- a confirmation for
        // something that was not submitted is a lie the user acts on.
        // A phone's message was not said in this room: no cue for it.
        const phone = PHONE_VIA[m.reason];
        if (!phone) playCue("submitted");
        logUser(text);
        if (phone) markVia(openItem, phone);
        drawHeldArtifacts();
        // The turn has been TAKEN, so the daemon has consumed whatever was
        // pending on this session (AC-199). Cleared here rather than when the
        // message was sent, because an empty final takes nothing and the
        // attachment is still waiting for the next thing the owner says.
        // A phone's turn took nothing pending on THIS page.
        if (!phone && attached.length) { attached = []; paintAttached(); }
        setState(THINKING);
      }
      break;
    }

    case "reply":
      if (stale(m)) return;
      logReply(m.text || "", m.tier, m.spoken);
      rememberTurnItem(m.turn, openItem);
      drawHeldArtifacts();
      break;

    case "library":
      // Something in the library changed, on any device or by voice
      // (docs/automations.md 5, AC-467): a hint to re-read, never the data.
      library.changed(m);
      automations.changed();
      // A run started, ended or was READ somewhere (AC-469): the tree's
      // clock glyphs and unread dots are the daemon's, so re-read them.
      if (m.what === "automations" || m.what === "runs") loadChats();
      break;

    case "opened":
      // THE DAEMON MOVED THIS DEVICE to another conversation -- "work in X"
      // navigates and "new conversation" births one (docs/filing-and-
      // pacing.md 8, D-C2, AC-290). Addressed to this socket alone rather
      // than to a turn, so there is no staleness check: `loadSession`
      // re-reads the record the daemon now holds and re-declares `showing`,
      // which the daemon has already set to the same id.
      loadSession({ session: m.chat, turns: 0 });
      break;

    case "speak_start":
      if (stale(m)) return;
      speakingTurn = m.turn != null ? m.turn : currentTurn;
      hud("hVoice", m.voice || "-");
      audioBytes = 0;
      hud("hAudio", "0.0 KiB");
      stopPlayback();                       // fresh queue for a fresh utterance
      if (playCtx && playCtx.state === "suspended") playCtx.resume();
      setState(SPEAK);
      break;

    case "speak_end":
      if (stale(m)) return;
      // The reply stopped at the owner's token cap rather than finishing
      // (AC-40). Not an error -- a cap doing its job is a bound they set --
      // so it is a note, and the unspoken tail is already in the history.
      if (m.truncated) logNote("reply cut short by the token cap");
      // NOT the end of the reply: it only means the daemon has stopped
      // sending. Do not re-arm here -- wait for the speaker to go quiet.
      awaitDrain(m.turn != null ? m.turn : speakingTurn);
      break;

    case "mode":
      applyMode(m.mode);
      logNote("mode is now " + m.mode);
      break;

    case "settings":
      // Somebody changed a preference -- possibly on another device, possibly
      // on this one. Update the controls in place, without a reload: two
      // satellites showing different values for one server-side setting is
      // the exact defect that put settings on the daemon (D-2, AC-8). The
      // panel leaves alone whatever control the user currently has focus in.
      settings.applyChanged(m.changed || {}, m.pending);
      if (m.turn_mode) applyTurnMode(m.turn_mode, m.capture_ceiling_ms);
      logNote("settings changed: " + settingsChangedLine(m.changed || {}));
      break;

    case "turn_mode":
      // How the daemon decides a turn is over: smart / patient / endword.
      // Server-authored and broadcast, like the permission mode, so two
      // devices cannot disagree about it (D-2). Worth showing rather than
      // hiding: in endword mode nothing submits until the phrase is said,
      // and a user who does not know which mode they are in has no way to
      // tell "still listening" from "stuck".
      applyTurnMode(m.turn_mode, m.capture_ceiling_ms);
      logNote("turn mode is now " + m.turn_mode);
      break;

    case "question":
      // A CODING AGENT IS BLOCKED ON A HUMAN (AC-234, AC-249). It is
      // BROADCAST, so this page draws it whether or not it owns the turn --
      // the device that answers is usually not the device that asked, and a
      // phone reading "Thinking" while the desk sits blocked is the recorded
      // failure this exists to prevent. Deliberately NOT behind `stale(m)`:
      // the frame carries a chat and no turn, on purpose.
      openQuestions.set(String(m.qid), m);
      paintAsk();
      toBottom(true);
      // WHO is asking (lantern-ui Step 7): a device that did not start the
      // turn last read the listing before the agent was spawned, and the
      // turn cannot end while the question blocks it, so nothing else would
      // re-read it. The listing's repaint re-says the name (`paintAskWho`).
      loadChats();
      break;

    case "question_closed":
      // What removes the card, on every device at once, for all four reasons
      // (answered, cancelled, expired, agent_gone). Without it the owner is
      // looking at a question that can no longer be answered and has no way
      // to learn that -- which is the Codex complaint verbatim.
      openQuestions.delete(String(m.qid));
      paintAsk();
      break;

    case "worker_event":
      // A coding agent's own progress, as DATA rather than a guess
      // (AC-233, AC-235, AC-236). `elapsed_ms` is the daemon's own
      // measurement, so every event recalibrates the clock instead of
      // letting drift accumulate across a turn -- one writer, `setTurnClock`
      // (D-1, D-18). A blank `detail` (the `started` kind, sent before any
      // tool fires) leaves `lastAction` alone: there is not one yet.
      if (stale(m)) return;
      setTurnClock(Date.now() - (m.elapsed_ms || 0));
      if (m.detail) lastAction = m.detail;
      paint();
      break;

    case "progress":
      // THE HEARTBEAT (AC-389..AC-392): the daemon's clock and its evidence.
      // Recalibrated from `elapsed_ms` on every beat, like `worker_event`.
      if (stale(m)) return;
      // THE AGENT FINISHED (AC-695, Step 1): one beat names it and its time,
      // and every later beat repeats it frozen. The working row is over; the
      // finished row goes on this turn's item, above the reply.
      if (m.agent && m.agent_elapsed_ms != null) {
        paintFinished(turnItems.get(m.turn) || openItem, String(m.agent),
                      m.agent_elapsed_ms);
        setTurnClock(null);
        paint();
        break;
      }
      setTurnClock(Date.now() - (m.elapsed_ms || 0));
      takeProgress(m);
      if (m.chat) paintRowProgress(m);   // its own tree row, not a snapshot
      paint();
      break;

    case "error":
      showError("daemon: " + (m.message || "(no message)"));
      // An error that names our turn ended that turn on the daemon (the reply
      // path re-arms in its finally block). Follow it back to wake-listening;
      // a protocol-level error with no turn changes no state.
      if (m.turn != null && m.turn === currentTurn) goListen();
      break;

    case "ping":
      // The daemon relies on WebSocket-level heartbeats today and never sends
      // this, but the handler exists on both sides, so answer it if it comes.
      send({ type: "pong", ts_ms: Date.now() });
      break;

    default:
      // Phase 9's `announce` is not sent by the daemon yet -- `worker_event`
      // is handled above, since 2026-09-16 (D-12: this list used to name it
      // too, wrongly). Surface anything unknown instead of swallowing it.
      logNote("unhandled message type: " + m.type);
      break;
  }
}

// --------------------------------------------------------------------------- //
// Mode: server-side, displayed here, requested from here. Nothing is cached.
// --------------------------------------------------------------------------- //
function applyMode(mode) {
  if (!mode) return;
  // showMode owns the header chip too, through the onMode callback below.
  // Writing it here as well was a second source for one value.
  settings.showMode(mode);
}

// Turn mode is a PREFERENCE, not a permission boundary, so unlike applyMode
// there is no escalation ceremony around it -- how long Butler waits before
// submitting is nobody's security problem (daemon/settings.py).
function applyTurnMode(mode, ceilingMs) {
  if (!mode) return;
  hud("hTurnMode", mode);
  // A spoken mode change ("long form") broadcasts `turn_mode` and nothing
  // else, so the settings panel would sit on the old value if it only
  // listened to `settings`. Feed it here too, and the control tracks the
  // user's own voice.
  settings.applyChanged({ turn_mode: mode });
  // AC-92's other half: the daemon's own live ceiling for one capture,
  // arriving beside `turn_mode` on all three frames that carry it (`ready`,
  // `turn_mode`, `settings`). A margin is added ON TOP of the daemon's own
  // number (CAPTURE_CEILING_MARGIN_MS) rather than used bare, so the
  // daemon's own hard cap gets to finalize first. Absent or non-positive
  // (an older daemon, a frame that only ever carried `turn_mode`) leaves
  // whatever this page already had -- never a lie about what this
  // connection was actually told.
  if (typeof ceilingMs === "number" && ceilingMs > 0) {
    captureCeilingMs = ceilingMs + CAPTURE_CEILING_MARGIN_MS;
  }
}

// The settings the PAGE has to honour itself. Everything else in the schema is
// read by the daemon; these two are about the noises this browser makes, so
// they arrive over HTTP like all the rest and are mirrored into memory here.
// Nothing is stored (AC-8).
function applyPageValues(values) {
  if (!values) return;
  // Look, from the server. The page holds no copy: this function is the only
  // place the style, the theme and the reading face are applied, and it runs
  // when the daemon says so -- on load, on a write from this device, and on a
  // broadcast caused by another one.
  // The pill that used to carry this word now opens the whole appearance
  // group, and the value is drawn by the control inside it -- written by
  // settings.js from this same answer, so there is one place saying what the
  // theme is rather than two that can disagree (D-18).
  // The STYLE (docs/lantern-ui.md CU-2, AC-678) and the THEME are both
  // attributes on <html> that the stylesheet decorates; the daemon names the
  // values and this file holds no list of its own (AC-41). `auto` is
  // resolved by the stylesheet against the device (CU-6). The wave's palette
  // is read back from whatever they resolve to, so it is re-read after
  // either changes -- a broadcast carries only the keys that changed.
  let looked = false;
  if (typeof values.ui_style === "string") {
    document.documentElement.dataset.style = values.ui_style;
    looked = true;
  }
  if (typeof values.theme === "string") {
    document.documentElement.dataset.theme = values.theme;
    looked = true;
  }
  if (looked) { applyMesh(); placeByStyle(); }
  // The READING FACE (AC-173, CU-5) is an override of the face Butler's
  // replies are set in. The VALUE is the CSS family name, written straight
  // into `--read`; no table in this directory maps a key to a family (AC-41,
  // AC-42). "" is "Style default": the override is REMOVED and the style's
  // own `--read` stands. `system-ui` is the tail of the stack so a face whose
  // woff2 has not been fetched still renders text.
  if (typeof values.reading_face === "string") {
    if (values.reading_face) {
      document.documentElement.style.setProperty(
        "--read", '"' + values.reading_face + '", system-ui, sans-serif');
    } else {
      document.documentElement.style.removeProperty("--read");
    }
  }
  // TEXT SIZE (R7, CLAUDE.md rulings of 2026-09-16). Same shape as the face
  // above: the daemon names the value, this file writes it into the one
  // custom property every font-size in index.html multiplies, and holds no
  // table of its own (AC-41). The value is already the bare number
  // (`"1.0"` / `"1.08"` / `"1.15"`), so it is written as-is rather than
  // parsed -- a CSS custom property is a token stream, not a typed value.
  if (typeof values.text_scale === "string" && values.text_scale) {
    document.documentElement.style.setProperty("--scale", values.text_scale);
  }
  // WHAT IS DRAWN, THEN WHERE (STEP 2, docs/voice-effects.md, AC-262).
  // `setVoiceEffect` MUST run before `setVizPlacement` on every read of this
  // object: `viz_placement`'s own branch only touches the DOM while the
  // effect is `lattice` (setVizPlacement/applyVizPlacement above), so
  // reversing the order would move the canvas into #waveMic and
  // then have voice_effect immediately move it right back out for any
  // treatment -- one extra DOM write on every settings read for nothing. In
  // this order, a `viz_placement` broadcast that arrives while a treatment is
  // showing is merely recorded and takes effect once `voice_effect` returns
  // to `lattice`.
  if (typeof values.voice_effect === "string") {
    setVoiceEffect(values.voice_effect);
  }
  // Where the wave is drawn (AC-167). `off` stops the DRAWING and never the
  // calls -- all eight `viz.*` call sites stay exactly where they were.
  if (typeof values.viz_placement === "string") {
    setVizPlacement(values.viz_placement);
  }
  // How many seconds of the room this page holds, and zero for off (AC-68).
  // The daemon owns it like every other setting, so it arrives on the full
  // read AND on a broadcast carrying only the keys that changed -- hence the
  // `typeof` guard every value in this function uses. Turning it off on the
  // phone therefore empties the desk's ring too, on the broadcast, without
  // either page storing anything (D-2, AC-8). The paint() below shows or hides
  // the indicator.
  if (typeof values.ambient_seconds === "number") {
    setAmbientWindow(values.ambient_seconds);
    paintAmbient();
  }
  // Speech on or off (AC-110). Two things say it, because they answer
  // different questions: the chip is always there and answers "will Butler
  // talk if I ask it something", and the headline answers "why is this reply
  // silent" at the moment it is silent. A repaint, because the value arrives
  // on the broadcast path too and the page may be mid-reply when it does.
  if (typeof values.speak === "boolean") {
    const wasMuted = speakMuted;
    speakMuted = !values.speak;
    paintSpeakButton();
    if (wasMuted !== speakMuted) paint();
  }
  // Whether this satellite listens at all (AC-179). The page acts on what the
  // daemon says rather than on what was pressed, so the boot arm and a
  // broadcast from another device are ONE path -- and the setting is what the
  // owner's "persistence in between sessions" actually means.
  if (typeof values.stt_enabled === "boolean") applySttEnabled(values.stt_enabled);
  // The wave button's own ceiling is the DAEMON's, not a second number here
  // (AC-181). The daemon refuses a longer body with a 413; stopping at the
  // same length means the words already spoken are still transcribed rather
  // than thrown away with the request.
  if (typeof values.dictate_max_seconds === "number"
      && values.dictate_max_seconds > 0) {
    dictateMaxSeconds = values.dictate_max_seconds;
  }
  // What ends a turn, so the mic control can name it (AC-105). Both arrive
  // here on the full read AND on a broadcast, which carries only the keys that
  // changed -- hence the same typeof / Array.isArray guards every value in
  // this function uses, and hence the repaint: `applyTurnMode` routes the
  // socket's turn_mode through settings.applyChanged, which lands right here.
  if (typeof values.turn_mode === "string") turnMode = values.turn_mode;
  if (Array.isArray(values.end_phrases)) {
    endPhrases = values.end_phrases.filter((p) => typeof p === "string" && p);
  }
  paint();
  if (typeof values.cue_volume === "number") cueVolume = values.cue_volume;
  if (typeof values.cues === "boolean" && !cueFailed) {
    const was = cuesEnabled;
    cuesEnabled = values.cues;
    if (was !== cuesEnabled) {
      // Turning cues off turns the mic suppression off with them: there is no
      // tone to hide. Only touch the HUD when it actually changed, so a
      // settings refresh cannot wipe the muted-frames readout.
      if (!cuesEnabled) micMuteUntil = 0;
      hud("hCue", cuesEnabled ? "on" : "off");
    }
  }
}

// --------------------------------------------------------------------------- //
// Lifecycle
// --------------------------------------------------------------------------- //

// The microphone GRAPH, opened once and shared. Extracted out of `start()`
// because the wave button dictates through the same frames without arming the
// wake word (AC-181), and two graphs would mean two `getUserMedia` prompts,
// two contexts and two sets of frames arriving at one `onFrame`.
//
// IDEMPOTENT: `micNode` being non-null is what says the graph is up. The
// visualiser is attached here, so a dictation on an otherwise-silent page
// draws the room exactly as an armed one does.
// MEMOISED ON THE PROMISE, not on `micNode`. A null check alone is not a
// guard across an `await`: MEASURED 2026-09-10, pressing the header pill calls
// this and the settings broadcast it causes calls it again a moment later,
// both before `micNode` is assigned -- so two AudioContexts were built, the
// module-level handle pointed at the second, and the first call then did
// `new AudioWorkletNode(ctx2)` before ctx2's `addModule` had resolved:
// "the node name 'downsample-processor' is not defined in
// AudioWorkletGlobalScope". The page said `standing by` with a red error under
// it and no microphone behind either.
//
// Cleared in `finally`, so a REFUSAL can be retried; a success is caught by
// the `micNode` check above it and never builds a second graph.
let micGraphPromise = null;

function openMicGraph() {
  if (micNode) return Promise.resolve();
  if (!micGraphPromise) {
    micGraphPromise = buildMicGraph()
      .finally(() => { micGraphPromise = null; });
  }
  return micGraphPromise;
}

async function buildMicGraph() {
  // Built into LOCALS and published at the end, so a failure half way through
  // does not leave the module holding a stream nothing can reach (D-17).
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext();
  try {
    await ctx.audioWorklet.addModule("mic-worklet.js");
    const node = new AudioWorkletNode(ctx, "downsample-processor");
    node.port.onmessage = (e) => onFrame(e.data.pcm, e.data.rms);
    const micSource = ctx.createMediaStreamSource(stream);
    micSource.connect(node);
    // Deliberately NOT connected to destination: no echo of our own mic.
    // `openPlayback()` used to live in `start()`, and that was the second half
    // of the mic coupling (AC-177): a page with no microphone had no speaker
    // either, so a typed turn's reply would have arrived silently with nothing
    // reporting it. It is opened at boot now; this is the idempotent re-open.
    await openPlayback();
    micStream = stream; micCtx = ctx; micNode = node;
    // Display only. An AnalyserNode is a pass-through tap, so neither of these
    // changes a sample that reaches the recognizer, the detector or the
    // speaker. The input tap sits BEFORE the cue suppression in onFrame, so a
    // cue is visible in the wave while still being zeroed out of everything
    // that matters (AC-5b) -- seeing Butler's own beep in the room's waveform
    // is honest, and reaching for the suppressed copy would mean rebuilding
    // the spectrum by hand for no gain.
    if (viz) { viz.attachInput(micSource); viz.attachOutput(ttsNode); }
  } catch (e) {
    try { stream.getTracks().forEach((t) => t.stop()); } catch { /* */ }
    try { ctx.close(); } catch { /* */ }
    throw e;
  }
}

// The other half, and it has TWO callers that must not fight: standing
// listening down, and a dictation finishing. Whichever goes last closes it,
// which is why both ask first.
function closeMicGraph() {
  // FIRST, while the context is still open. The visualiser holds an
  // AnalyserNode in it and nothing used to tell it the context was about to
  // be closed, so it went on reading a dead tap for the life of the page. It
  // keeps drawing -- the page is still here and still says `off`.
  if (viz) viz.detach();
  try { micNode && micNode.disconnect(); } catch { /* already gone */ }
  try { micStream && micStream.getTracks().forEach((t) => t.stop()); } catch { /* */ }
  try { micCtx && micCtx.close(); } catch { /* */ }
  micNode = micStream = micCtx = null;
}

// `starting` is a flag set before an `await`, so it is cleared in a `finally`
// and never at the return points (D-17): this function has three exits and a
// latched flag here means the microphone can never be armed again.
let starting = false;

async function start() {
  if (micLive || starting) return;
  starting = true;
  try {
    await arm();
  } finally {
    starting = false;
  }
}

// ONE readable line for a `getUserMedia` refusal, said once here rather than
// three times (`arm`, `toggleDictation`) as the raw `DOMException` message
// (review item 4, 2026-09-26). `NotAllowedError`/`SecurityError` is the
// browser permanently declining until the OWNER changes it, which
// `err.message` never says -- it reads "Permission denied" and stops there,
// with no path back for a stranger who has never opened a browser's site
// settings before.
// SAID ONCE (2026-09-26). A REFUSAL is painted from `micRefused` by
// `paintErrLine` ("Microphone blocked.") for as long as it is true, and the
// voice pill is the retry; counting it as a logged problem too would say it
// twice, so a refusal goes to the log only. Anything else (no device, a
// failed graph) has no persistent home, so it is counted on the line.
function reportMicFailure(e) {
  const name = e && e.name;
  const text = micRefusalMessage(e);
  if (name === "NotAllowedError" || name === "SecurityError") logError(text);
  else showError(text);
}
function micRefusalMessage(e) {
  const name = e && e.name;
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone blocked -- allow it in the browser's site settings, "
      + "then tap here to retry.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "No microphone found on this device. Typing still works.";
  }
  return "audio setup failed: " + (e && (e.message || e));
}

// The daemon's wake config, adopted (AC-805). From `ready.wake` and from the
// `wake` broadcast. A threshold change reaches the live detector with no
// reload; a different word list rebuilds it (`loadWake`). Checked here, at
// the boundary, so everything after trusts the shape.
function applyWakeConfig(w) {
  const ok = w && Array.isArray(w.words) && w.words.length > 0
    && w.words.every((x) => x && typeof x.id === "string"
                     && typeof x.phrase === "string" && typeof x.url === "string")
    && typeof w.threshold === "number" && typeof w.cooldown_ms === "number";
  if (!ok) {
    showError("Butler sent no usable wake words: " + JSON.stringify(w));
    return;
  }
  wakeCfg = { words: w.words, threshold: w.threshold, cooldown_ms: w.cooldown_ms };
  window.butlerWake = wakeCfg;            // diag.js reads the model list here
  wakeCfgResolve();
  if (wake) {
    wake.setThreshold(wakeCfg.threshold);
    if (wordsKey(wakeCfg.words) !== wakeKey) reloadWake();
  }
  paintWakeWords();
}

// Every place that names the wake words, from `wakeCfg` (AC-809). The pill's
// label is `micAction()`'s, reached through `paint()`.
function paintWakeWords() {
  const b = document.createElement("b");
  b.textContent = wakePhrases(false);
  $("emptyHint").replaceChildren("Say ", b, ", or type.");
  $("wakeHelp").textContent = "Say " + wakePhrases(true) + " to start a turn";
  $("wakeThreshold").textContent = wakeCfg
    ? "threshold " + wakeCfg.threshold.toFixed(2) : "threshold -";
  // The trace's line and the text above, from one value (D-28).
  if (viz && wakeCfg) viz.setThreshold(wakeCfg.threshold);
  paint();
}

function dropWake() {
  if (!wake) return;
  const old = wake;
  wake = null;
  wakeKey = "";
  old.close().catch((e) => showError("wake detector release failed: "
                                     + (e.message || e)));
}

// Builds the detector for the words that are on NOW. The old one is released
// FIRST: two full sets of sessions alive at once is the iOS memory failure the
// sequential load exists to avoid, so a word switch costs a second or two of
// deafness instead. Loops if the list changed while it loaded; resolves false
// when a newer load or `stop()` superseded it.
async function loadWake() {
  const gen = ++wakeGen;
  dropWake();
  for (;;) {
    const key = wordsKey(wakeCfg.words);
    hud("hWake", "loading wake models");
    const det = await window.WakeWord.loadWakeWordDetector(
      window.WakeWord.WAKE_WORD_MODEL_URLS, wakeCfg.words,
      { threshold: wakeCfg.threshold, refractoryMs: WAKE_REFRACTORY_MS,
        chunkSamples: WAKE_CHUNK });
    if (gen !== wakeGen) { await det.close(); return false; }
    if (key === wordsKey(wakeCfg.words)) {
      det.setThreshold(wakeCfg.threshold);
      wake = det;
      wakeKey = key;
      hud("hWake", "armed");
      return true;
    }
    await det.close();
  }
}

// A word list change while armed. A failed load stands the microphone down
// with the same verdict as `arm()`: a satellite with no wake word is not one.
function reloadWake() {
  loadWake().catch((e) => {
    if (!micLive) return;
    stop();
    micRefused = true;
    raiseErrLine();
    showError("wake word unavailable: " + (e.message || e));
  });
}

async function arm() {
  clearErrors();
  logNote("arming the microphone");

  try {
    hud("hWake", "loading onnxruntime");
    await window.ortReady;
    // Waits, never defaults (see `wakeCfg`).
    if (!wakeCfg) hud("hWake", "waiting for Butler to name the wake words");
    await wakeCfgKnown;
    if (!(await loadWake())) return;      // stood down while it loaded
  } catch (e) {
    // Remembered for `applySttEnabled`'s reason: a model that failed to load
    // will fail again on the next settings answer, and re-running it on every
    // broadcast turns one failure into a loop nobody asked for.
    micRefused = true;
    raiseErrLine();
    showError("wake word unavailable: " + (e.message || e));
    logNote("");
    return;                 // fail fast: a satellite with no wake word is not
                            // a satellite, and pretending otherwise hides it
  }

  try {
    await openMicGraph();
  } catch (e) {
    // A refusal is REMEMBERED, or the page retries on every settings answer
    // and re-prompts a browser that has already said no (AC-179). It is
    // cleared only by the owner pressing a listening control, which is the
    // one event that means "try again".
    micRefused = true;
    raiseErrLine();
    reportMicFailure(e);
    logNote("");
    paint();
    return;
  }

  micLive = true;
  logNote("");
  setState(WAKE);
  sendCaps();
  // `connect()` used to be the last line of this function, and that ONE line
  // was the whole of "with the microphone off the app cannot be used at all":
  // both `return`s above it leave without a socket, and a typed message has
  // nowhere to go without one. The socket is opened at boot now. This call
  // remains for the page that armed before the first connection attempt
  // finished, and `connect()` is idempotent against an open socket.
  connect();
}

function stop() {
  micLive = false;
  if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  discardPreroll();
  stopPlayback();
  // The PLAYBACK context and the SOCKET both survive this, and that is the
  // point of the whole change: standing the microphone down must not take
  // away the ability to type a question and hear the answer (AC-176, AC-177).
  // So no `playCtx.close()` and no `ws.close()` here, and `typedPending` is
  // left alone -- it used to be cleared because the socket was about to go,
  // and clearing it now would orphan a typed turn whose `final` has not
  // arrived (AC-163).
  //
  // The mic GRAPH survives a dictation in flight, for the same reason: the
  // wave button is independent of this control (AC-181, R3), so whichever of
  // the two finishes last is the one that closes the graph.
  if (!dictating) closeMicGraph();
  wakeGen++;                // a load still in flight closes itself
  dropWake();
  sendCaps();
  ring = []; ringBytes = 0;
  // The microphone has stood down, so the room is not being held any more
  // (AC-72). The window itself is the daemon's and is left alone: arming again
  // starts a fresh ring, and forgetting the setting here would make the
  // indicator lie until the next settings answer.
  discardAmbient();
  // A mute deadline must not survive into the next session: the frames it
  // would zero belong to audio nobody has heard yet.
  micMuteUntil = 0; micMutedFrames = 0; lastClosingCueAt = 0;
  hud("hCue", "-");
  setState(OFF);
}

// --------------------------------------------------------------------------- //
// Listening is a SETTING, and the page acts on what it is told (AC-179, AC-180)
//
// The owner asked for "an additional button to turn stt on/off ... all settings
// with persistence in between sessions", so whether this satellite listens is
// `stt_enabled` on the daemon and not a flag in this file. That is the same
// rule every preference here follows (D-2, AC-8): turning listening off on the
// tablet means it everywhere, and nothing in `web/` may store it.
//
// THE PRESS IS ANSWERED AT ONCE AND PERSISTED AFTERWARDS, which is the pattern
// the sidebar collapse and the shortcuts fold already use: a control that waits
// for a round trip before moving reads as broken on a slow link, and a refusal
// is SAID rather than silently reverting the thing just pressed.
// --------------------------------------------------------------------------- //
function listenOn() {
  // The one event that means "try again" after a browser has refused.
  micRefused = false;
  raiseErrLine();
  setListening(true);
}

function listenOff() { setListening(false); }

function setListening(on) {
  if (on) start(); else stop();
  settings.set("stt_enabled", on).catch((e) => {
    showError("listening is " + (on ? "on" : "off") + " on this device, but "
              + "Butler did not record it: " + (e.message || e));
  });
}

// What the daemon says, applied. Reached from the full settings answer on
// `ready` -- which is what ARMS THE PAGE AT BOOT with no tap (R1) -- and from
// a `settings` broadcast, so turning listening off on the phone stands the
// desk's microphone down too.
//
// `micRefused` is the guard that keeps this from becoming a prompt loop: a
// browser that has denied the microphone would otherwise be asked again on
// every broadcast, and a permission dialog nobody opened is worse than a
// control that stays where it was put.
function applySttEnabled(on) {
  if (on && !micLive && !micRefused) { start(); return; }
  if (!on && micLive) stop();
}

// Stop a reply, OR A CAPTURE, that is already running (AC-111). The owner's
// ruling was that this stops the SPEECH AND THE TURN -- in harness mode the
// thing worth stopping is usually the coding agent, not the sentence
// (docs/voice-control.md D1). That makes it the only irreversible act on
// this page: the agent's partial answer is gone. Escape is scoped so it
// cannot fire while the settings surface is open.
//
// CAPTURE JOINED THINKING/SPEAK HERE (owner: set `turn_mode` to `endword`,
// spoke Portuguese, and had no way to cancel the turn that never ended). It
// was deliberately absent -- the comment above used to say "ONLY while
// Butler is thinking or speaking" -- but that made Stop and Escape both
// INERT during the one state most likely to wedge: endword mode waits for a
// spoken end phrase with NO silence timer at all. `abandon()` on the daemon
// side already tears a capture down correctly (cancels the pipeline task,
// closes it, frees the recognizer) -- `_stop` has accepted a stop mid-capture
// since AC-111 shipped; this page simply never sent one.
//
// ONE function, called by the key and by the button (D-18). Two paths to the
// same act is exactly how a contract drifts, and this repo has paid for that
// three times.
//
// The order is the one `onWakeFire` already uses for barge-in and it is not
// arbitrary: the local queue is dropped FIRST so the room goes quiet at the
// press rather than at the round trip, and only then is the daemon told.
function stopTurn() {
  if (state !== THINKING && state !== SPEAK && state !== CAPTURE) return;
  const turn = speakingTurn != null ? speakingTurn : currentTurn;
  stopPlayback();
  // THE STOP NAMES THE CONVERSATION ON SCREEN (docs/parallel-agents.md D5,
  // e2e audit S18), and the turn when this page drives it -- turn state is
  // per conversation now, so `turn` can only be the on-screen chat's. The
  // daemon ignores a turn that is not in that chat (D-6). A WATCHED turn
  // (a reload, another device) has no id here, so the chat alone names it
  // and the daemon finds the turn wherever it is owned. With neither, nothing
  // is sent: the page would be asking the daemon to guess.
  if (turn != null) send({ type: "stop", turn, chat: liveChatId || null });
  else if (watchedChat) send({ type: "stop", chat: watchedChat });
  if ((turn != null || watchedChat) && liveChatId) refreshWhenSettled(liveChatId);
  if (turn != null || watchedChat) markStopped(openItem);
  logNote("stopped");
  // The page re-arms itself rather than waiting to be told, exactly as
  // barge-in does. Every terminal path lands here (D-1, AC-2).
  goListen();
}

// The ONE end-of-turn signal the page is allowed to send, and only because the
// user explicitly asked for it (plan.md 4.3). It is a HINT: the daemon still
// owns the decision and still emits the final.
function endHint() {
  if (state !== CAPTURE) return;
  send({ type: "capture_end_hint" });
  logNote("sent capture_end_hint");
}

// THE COMPOSER'S MICROPHONE DICTATES (mocks/q-familiar.html item 2). It used
// to change job with the state, sharing `micAction()` with the header pill;
// the owner's own backlog item 7 asked for the two adjacent audio glyphs to
// stop reading as one control with only a tooltip between them, so this one
// now does exactly one thing, always. Feedback still fires on pointer DOWN
// rather than on click: the Apple fluid-interface rule the design language
// here follows, and on a phone the difference between the two is the whole
// feel of the control.
MIC_BUTTON.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  toggleDictation();
});

// THE HEADER PILL ARMS THE WAKE WORD, ALONE (AC-180, mocks/q-familiar.html
// item 2). It is the one control `micAction()` still drives -- submitting
// early during a capture and stopping a reply are still reachable here, as
// they were before, because the mic button stopped being a second
// implementation of the same act rather than the pill losing one. `click`
// rather than `pointerdown`: the pill is not the instrument-feel control the
// microphone is, and a keyboard press has to reach it.
STATE_CHIP.addEventListener("click", () => {
  const act = micAction().act;
  if (act) act();
});

// --------------------------------------------------------------------------- //
// THE ATTACH MENU (AC-205, mocks/q-familiar.html item 1). The camera folds
// into the paperclip's menu instead of owning its own composer slot -- the
// camera's OWN code (`takePhoto()`, above) is unchanged, only what opens it
// moved. `position: fixed`, computed against the button's own rect and
// clamped to the viewport, the same technique `placeProjPop` already uses for
// the project tile's popover -- so no `overflow: hidden` ancestor can clip it,
// though none sits between here and the composer today.
// --------------------------------------------------------------------------- //
function attachMenuOpen() { return !$("attachMenu").hidden; }
function placeAttachMenu() {
  const menu = $("attachMenu");
  if (menu.hidden) return;
  const a = $("attachBtn").getBoundingClientRect();
  const b = menu.getBoundingClientRect();
  let x = a.left;
  x = Math.max(8, Math.min(x, window.innerWidth - b.width - 8));
  // Above the button: the composer sits at the BOTTOM of the page, the same
  // reason `.modelpop` used to open upward before it moved to the header.
  let y = a.top - b.height - 8;
  y = Math.max(8, Math.min(y, window.innerHeight - b.height - 8));
  menu.style.left = Math.round(x) + "px";
  menu.style.top = Math.round(y) + "px";
}
function setAttachMenuOpen(open) {
  $("attachMenu").hidden = !open;
  $("attachBtn").setAttribute("aria-expanded", String(open));
  if (open) placeAttachMenu();
}
$("attachBtn").addEventListener("click", () => setAttachMenuOpen(!attachMenuOpen()));
document.addEventListener("pointerdown", (e) => {
  if (!attachMenuOpen()) return;
  if ($("attachMenu").contains(e.target)) return;
  if (e.target.closest && e.target.closest("#attachBtn")) return;
  setAttachMenuOpen(false);
});
// ON THE WRAPPER, not the menu alone -- the exact defect this page has
// already paid for on the appearance pill and the model picker (review
// 2026-09-11): opening by click leaves focus on `#attachBtn`, which is
// OUTSIDE `#attachMenu`, so a listener on the menu never sees the key and
// Escape falls through to the global handler, which clears the composer
// (measured here: the menu stayed open through Escape until this moved to
// `.attachwrap`, the ancestor of both). It never becomes a sixth case in the
// global handler -- that handler's last cases stop a reply and clear the
// composer (AC-112), and neither may become conditional on a menu.
$("attachBtn").parentElement.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !attachMenuOpen()) return;
  setAttachMenuOpen(false);
  $("attachBtn").focus();
  if (!escapeWouldStop()) e.stopPropagation();
}, true);
window.addEventListener("resize", placeAttachMenu);

$("attachUpload").addEventListener("click", () => {
  setAttachMenuOpen(false);
  $("attachInput").click();
});
$("attachPhoto").addEventListener("click", () => {
  setAttachMenuOpen(false);
  takePhoto();
});

// AC-205. The paperclip's hidden file input, and its value is cleared
// afterwards so attaching the SAME file twice still fires `change`.
$("attachInput").addEventListener("change", async (e) => {
  const files = [...(e.target.files || [])];
  e.target.value = "";
  for (const file of files) await sendAttachment(file, $("attachBtn"));
});

// DRAG TO ATTACH (docs/rich-chat.md RC-13): a file dropped anywhere on the
// conversation column -- transcript or composer -- goes through the SAME
// `sendAttachment` the paperclip uses (`POST /api/attach`, AC-197), one call
// per file, so there is still one upload path. A drag that carries no files
// (selected text, a link) is left to the browser. The counter exists because
// `dragleave` fires on every child the pointer crosses.
const dropZone = document.querySelector(".col");
let dragDepth = 0;
function dragHasFiles(e) {
  return !!(e.dataTransfer && [...e.dataTransfer.types].includes("Files"));
}
function setDropping(on) { dropZone.classList.toggle("dropping", on); }
dropZone.addEventListener("dragenter", (e) => {
  if (!dragHasFiles(e)) return;
  e.preventDefault();
  dragDepth += 1;
  setDropping(true);
});
dropZone.addEventListener("dragover", (e) => {
  if (!dragHasFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = guestChat ? "none" : "copy";
});
dropZone.addEventListener("dragleave", (e) => {
  if (!dragHasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) setDropping(false);
});
dropZone.addEventListener("drop", async (e) => {
  if (!dragHasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  setDropping(false);
  // A guest chat's composer is inert (AC-607); so is its drop.
  if (guestChat) { showError("nothing was attached: " + GUEST_NOTE); return; }
  const dropped = [...(e.dataTransfer.files || [])];
  for (const file of dropped) await sendAttachment(file, $("attachBtn"));
});
// A file dropped anywhere ELSE (the header, the sidebar) would make the
// browser navigate to it, and leaving this page kills the microphone, the
// socket and playback. Such a drop does nothing.
for (const type of ["dragover", "drop"]) {
  window.addEventListener(type, (e) => { if (dragHasFiles(e)) e.preventDefault(); });
}

// AC-188. Searching past conversations. `input` and not `change`, so the list
// answers while the owner types; Escape clears the box and puts the full list
// back, and it does NOT propagate, because the global Escape has four cases
// and the last of them stops a reply (AC-112) -- a search box that silently
// disabled that would be the drift that rule exists to prevent.
// THE SEARCH ROW BECOMES THE FIELD (lantern-ui section 4): pressing "Search"
// swaps the row for `#chatSearch`, focused; the field goes back to a row when
// it loses focus EMPTY. A query typed in it stays on screen with its results.
function setSearchOpen(open) {
  $("searchBox").hidden = !open;
  $("searchRow").hidden = open;
  if (open) $("chatSearch").focus();
}
$("searchRow").onclick = () => setSearchOpen(true);
$("chatSearch").addEventListener("blur", () => {
  if (!$("chatSearch").value) setSearchOpen(false);
});
$("chatSearch").addEventListener("input", (e) => onChatSearch(e.target.value));
$("chatSearch").addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !$("chatSearch").value) return;
  e.stopPropagation();
  $("chatSearch").value = "";
  onChatSearch("");
});

// THE VOICE POPOVER'S SWITCHES (AC-686), each through the path that already
// wrote its setting. Answered by the daemon's own broadcast rather than
// painted here: every satellite hears it, and a page that painted itself
// first would disagree with the phone in the other room for a round trip.
// Read replies aloud: the existing `speak` setting (AC-182).
$("speakBtn").addEventListener("click", () => {
  const wanted = speakMuted;            // muted now, so the press means ON
  settings.set("speak", wanted).catch((e) => {
    showError("speech could not be turned " + (wanted ? "on" : "off") + ": "
              + (e.message || e));
  });
});
// Wake word: listening itself, `stt_enabled`, through `setListening` -- the
// same act the pill's body takes when it arms or stands down (AC-179, AC-180).
$("wakeSw").addEventListener("click", () => {
  if (micLive) listenOff(); else listenOn();
});
// Room memory: `ambient_seconds`, 0 for off (AC-68).
$("roomSw").addEventListener("click", () => {
  const wanted = ambientSeconds > 0 ? 0 : roomSecondsOn();
  settings.set("ambient_seconds", wanted).catch((e) => {
    showError("room memory could not be turned " + (wanted ? "on" : "off")
              + ": " + (e.message || e));
  });
});

// --------------------------------------------------------------------------- //
// WHERE THE WAVE IS DRAWN (AC-167)
//
// Four placements, ONE canvas. `#wave` is MOVED between two slots rather than
// duplicated: `visualiser.js` holds a reference to that element and reads its
// bounding rect every frame to place the lattice's text falloff, so a second
// canvas would mean a second `create()` and a second render loop.
//
// `off` stops the DRAWING and never the CALLS. Every `viz.*` call site stays
// exactly where it was -- `setMode`, `setState` and `pushScore` all still run
// on a page with the wave switched off, and `test_visualiser_contract.py`
// scrapes this file for them. What actually stops is cheap and automatic: the
// visualiser's own IntersectionObserver watches `#wave`, and a hidden canvas
// is not intersecting, so its frame loop idles.
//
// The DAEMON names the four values (AC-41); this reads whatever arrives and
// falls through to hiding both canvases for anything it does not recognise,
// which is the honest rendering of a value this page cannot draw.
// --------------------------------------------------------------------------- //
// `band` retired with the wave band (docs/lantern-ui.md CU-3); `ambient`'s
// canvas keeps the ring on the mic while `#field` fills the page.
const VIZ_SLOT = { mic: "waveMic", ambient: "waveMic" };
let vizPlacement = "";
// `viz_placement` only means something while `voice_effect` is `lattice`
// (STEP 2, docs/voice-effects.md, section 1 Option A) -- so the DOM move
// below is gated on that, and `vizPlacement` itself is still recorded
// unconditionally. A placement broadcast that arrives while a treatment is
// showing is not lost, it is applied the moment setVoiceEffect returns to
// `lattice` (applyVizPlacement's other caller, below).
let voiceEffect = "lattice";
function applyVizPlacement() {
  document.documentElement.dataset.viz = vizPlacement;
  const slot = VIZ_SLOT[vizPlacement];
  placeByStyle();
  $("waveMic").hidden = !slot;
  // The full-page lattice, and only in `ambient`. It is `position: fixed` at
  // inset 0 behind everything, which is a whole stacking layer -- so it is off
  // in the other three rather than drawn at zero opacity.
  $("field").hidden = vizPlacement !== "ambient";
}
function setVizPlacement(where) {
  if (where === vizPlacement) return;
  vizPlacement = where;
  if (voiceEffect === "lattice") applyVizPlacement();
}

// --------------------------------------------------------------------------- //
// WHICH VOICE EFFECT DRAWS (STEP 2, docs/voice-effects.md, PROPOSAL
// AC-252..AC-262)
//
// `voice_effect` is section 1's Option A: ONE setting answers "what do I
// see" and `viz_placement` survives only as "where the lattice goes",
// consulted only while this value is `lattice`. This file names just the two
// values it has code to run for, `off` and `lattice` (AC-41) -- every
// treatment name (`mist`, `bloom`, and whatever the catalogue grows to)
// passes straight through to `viz.setEffect()`, and index.html's
// `[data-voicefx=...]` selectors are what turn a name into drawing (or, for
// one this step has not built yet, into nothing). `data-voicefx` on <html>
// is the same precedent `data-theme` and `data-style` already are.
// --------------------------------------------------------------------------- //
function setVoiceEffect(name) {
  voiceEffect = name;
  document.documentElement.dataset.voicefx = name;
  if (viz) viz.setEffect(name);
  if (name === "off") {
    // Hide every wave surface -- exactly what applyVizPlacement("off") would
    // do, because there is nothing for `off` to draw either way.
    //
    // `#waveFx` IS ONE OF THOSE SURFACES, and forgetting it was a real defect
    // rather than an oversight worth one line. `off` makes `treatment` false
    // in visualiser.js, which puts the ring/wave block back in play -- and if
    // the canvas is still parented into the composer's slot, that block draws
    // a wave INSIDE THE COMPOSER. Measured while building the creative five:
    // 32,304 stray pixels going from a canvas treatment straight to `off`.
    // Hiding the slot is the whole fix, because the visualiser's own
    // IntersectionObserver idles the loop for a canvas that is not
    // intersecting -- the same mechanism this file already documents above.
    $("waveFx").hidden = true;
    $("waveMic").hidden = true;
    $("field").hidden = true;
  } else if (name === "lattice") {
    // Exactly today's behaviour: the canvas returns to wherever the
    // `viz_placement` setting says, including a change that arrived while a
    // treatment was showing and was recorded but not applied above.
    // `applyVizPlacement` MOVES `#wave` back out of the composer's slot, so
    // the empty slot is hidden here for the same reason as in `off`.
    $("waveFx").hidden = true;
    applyVizPlacement();
  } else {
    // A treatment. The one shared canvas moves into the slot the stylesheet
    // names for it (`--wave-slot`: the composer's `#waveFx`, or the seam for
    // the style's own voice) -- the same function that places it for
    // `lattice`, never a second `create()` -- and every other wave surface
    // goes dark. `mist` and `bloom` are CSS layers that never touch the
    // canvas, but `ember`, `ribbon`, `sonar`, `filaments`, `comet`, `dust`
    // and `aurora` draw into it, so the move is load-bearing.
    placeByStyle();
    $("waveFx").hidden = false;
    $("waveMic").hidden = true;
    $("field").hidden = true;
  }
}

// --------------------------------------------------------------------------- //
// THE COMPOSER (AC-162, AC-163)
//
// Typing is a PEER of speaking and not a fallback: Enter sends
// `{"type": "typed", "text": ...}` and everything past that is the path a
// spoken utterance already takes -- the same router, the same brain, the same
// record, the same re-arm.
//
// THE PAGE MINTS NO TURN ID, and this is the subtle half. On the spoken path
// the daemon announces one in `capture_start` and the page adopts it there.
// A typed turn sends NO `capture_start`, because nothing is being captured:
// the `final` frame is the first the page sees and is what carries the id
// (`hub.Hub._typed`). So the adoption moved to `onMessage`, ahead of the
// `stale()` check that would otherwise discard a frame for a turn this page
// does not yet know it owns.
//
// The microphone does not stop being live while this has focus, and nothing
// here touches the detector, the pre-roll ring or the socket.
// --------------------------------------------------------------------------- //
const composerForm = $("composer");
const composerInput = $("input");
// How many typed messages are in flight with no `final` back yet. It is a
// COUNT and not a boolean so a second Enter before the first answer cannot
// leave the page adopting one turn twice, and it is what stops a `final` this
// page did not ask for being adopted at all.
let typedPending = 0;

function growInput() {
  composerInput.style.height = "auto";
  composerInput.style.height = Math.min(176, composerInput.scrollHeight) + "px";
  paintSend();
}

// A GUEST'S CHAT HAS NO COMPOSER (RV-21, AC-607, docs/messaging.md 6.5). Its
// lines are what someone else wrote on WhatsApp; a typed turn here would be
// the owner's turn, with tools, reading a stranger's words. The daemon says
// which chat is one (`origin` on every conversation shape); the box is made
// inert, not hidden, and one line says where to answer instead.
const GUEST_ORIGIN_KIND = "whatsapp_guest";
const GUEST_NOTE = "Butler answers here as a guest assistant; reply from your phone.";
// The export's locked composer: a lock and the one line, in the box's place.
// The form itself is inert, disabled AND not drawn while locked.
const guestNote = document.createElement("span");
const guestLock = document.createElement("div");
guestLock.id = "guestLock";
guestLock.setAttribute("role", "status");
guestLock.hidden = true;
guestLock.append(LOCK_ICON(), guestNote);
composerForm.before(guestLock);
let guestChat = false;

function paintGuestLock(doc) {
  guestChat = !!(doc && doc.origin && doc.origin.kind === GUEST_ORIGIN_KIND);
  composerForm.inert = guestChat;
  composerInput.disabled = guestChat;
  composerForm.hidden = guestChat;
  guestNote.textContent = guestChat ? GUEST_NOTE : "";
  guestLock.hidden = !guestChat;
}

function sendTyped(text) {
  text = (text || "").trim();
  if (!text) return;
  if (guestChat) { showError("nothing was sent: " + GUEST_NOTE); return; }
  // WITH A QUESTION OPEN, THE BOX ANSWERS IT (AC-234). The placeholder says
  // "Answer <agent>..." and the card says "anything not on this list goes in
  // the box below", so a composer that sent a new turn instead would be the
  // control-that-lies failure with a text field in front of it. It is also
  // the right act mechanically: a typed message IS a barge-in
  // (`hub._typed` -> `_barge_in`), so sending one here would kill the very
  // child that is waiting for the answer.
  // The OLDEST question, because that is the one at the top of the card.
  if (openQuestions.size) {
    const [qid] = openQuestions.keys();
    composerInput.value = "";
    growInput();
    sendAnswer(qid, { value: text });
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    // Said rather than swallowed. A composer that accepts a message and drops
    // it on a dead socket is the control-that-lies failure with a text field
    // in front of it (AC-7b).
    showError("nothing was sent: this page has not reached Butler");
    return;
  }
  // No `backToLive()` here since 2026-09-12: the conversation on screen is the
  // one Butler is holding, so this message continues the chat the owner
  // clicked instead of abandoning it (AC-240).
  typedPending += 1;
  if (!send({ type: "typed", text })) { typedPending -= 1; return; }
  composerInput.value = "";
  growInput();
  logNote("");
  toBottom(true);
}

composerInput.addEventListener("input", growInput);
composerInput.addEventListener("keydown", (e) => {
  // Enter sends, Shift+Enter newlines, because that is what the familiar apps
  // do and familiarity is the whole point (docs/familiar-ui.md 6.1).
  // `isComposing` is checked because an IME's Enter commits a candidate and
  // must not send half a word.
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    composerForm.requestSubmit();
  }
});
composerForm.addEventListener("submit", (e) => {
  e.preventDefault();
  // ONE control, two jobs, and THE DRAFT DECIDES WHICH -- not the state alone.
  //
  // This used to read `if (state === THINKING || state === SPEAK) stopTurn()`
  // before looking at the box, so typing a follow-up while Butler was still
  // talking stopped him and threw the message away silently. That is the
  // single most common thing a person does with this app, and the window is
  // open on every turn because the page stays in its speaking state until its
  // own audio queue drains.
  //
  // A TYPED MESSAGE IS ALREADY DEFINED AS A BARGE-IN, which is what makes the
  // new rule safe rather than a second interruption mechanism: `Hub._typed`
  // calls `_barge_in()` itself (familiar-ui R7), so the daemon abandons the
  // running turn, aborts its own synthesis and mints a new turn from the one
  // message. The page's only remaining job is the half `onWakeFire` already
  // does for the spoken barge-in -- drop the LOCAL playback queue, so the room
  // goes quiet at the keystroke rather than at the round trip.
  //
  // Deliberately NOT `stopTurn()` followed by `sendTyped()`. That would send a
  // `stop` frame and then a `typed` one, which is two interruptions for one
  // act (D-18) -- and it is the exact sequence that wedges a turn today
  // (`e2e_scenarios.py` F2), so the tidy-looking version is also the broken one.
  const draft = composerInput.value.trim();
  if (draft) {
    if (state === THINKING || state === SPEAK) {
      stopPlayback();
      currentTurn = null;
      speakingTurn = null;
    }
    sendTyped(draft);
    return;
  }
  // Nothing to send, so the button means what it says: stop. Through the same
  // `stopTurn` the Escape key and the microphone control reach -- two paths to
  // one act is how a contract drifts, and this repository has paid for that
  // three times (D-18). CAPTURE is included (cause 1): `paintSend()`'s
  // `busy` already draws this button as Stop in that state, and a button
  // that draws as Stop and does nothing is the AC-7b failure by itself.
  if (state === THINKING || state === SPEAK || state === CAPTURE) stopTurn();
});

// THE WORKING ROW'S STOP (AC-694): the one stop, `stopTurn` (D-18).
$("workStop").addEventListener("click", () => stopTurn());
// 1, 2, 3 answer the docked question while the box is empty (AC-693).
$("dock").addEventListener("keydown", askKey);

// The empty state's example prompts send a REAL typed turn (AC-170). Nothing
// on this page is decorative: a chip that filled the box and stopped would be
// a smaller version of the failure R2 forbids.
for (const chip of document.querySelectorAll(".exchip")) {
  chip.addEventListener("click", () => sendTyped(chip.textContent));
}

// --------------------------------------------------------------------------- //
// THE SIDEBAR (AC-166)
//
// Two independent pieces of view state, because they answer different
// questions. `data-sidebar` is collapsed-or-open and is the DAEMON's, so it
// survives a reload and a second device agrees -- it rides on the shortcuts
// store beside the pane's own fold, for the reason that fold does: AC-8 bans
// browser storage outright, and nothing in this directory may name a setting
// (AC-41). `data-drawer` is the phone overlay's open-or-shut, which is a
// gesture rather than a preference and dies with the page.
// --------------------------------------------------------------------------- //
function setSidebar(collapsed) {
  document.documentElement.dataset.sidebar = collapsed ? "collapsed" : "open";
  // COLLAPSING IS HIDING THE LIST (AC-230). The strip that used to stay
  // behind at a fixed width is gone (docs/sidebar-collapse.md, SC-2); the
  // way back is `#burger`, in the header at every width, which repaints its
  // own label and busy dot here.
  paintBurger();
}
// HOW WIDE THE SIDEBAR IS (the owner's second-batch item 2, 2026-09-11).
//
// The number lives in the DAEMON like the collapsed flag beside it -- AC-8
// bans browser storage, and a per-device copy is how one laptop ended up with
// four disagreeing copies of a preference (D-2). So a drag paints locally for
// the length of the gesture and COMMITS once, on release: a POST per pointer
// move would be a hundred writes across one drag, and the daemon's answer
// would arrive behind the pointer and fight it.
//
// The bounds are the DAEMON's and are not written here. The store clamps and
// answers what it actually stored, so the page corrects itself from the reply
// rather than keeping a second copy of two numbers that would drift (AC-41).
function paintSidebarWidth(px) {
  document.documentElement.style.setProperty("--sb-w", px + "px");
  $("sbResize").setAttribute("aria-valuenow", String(px));
}

(function wireSidebarResize() {
  const handle = $("sbResize");
  let dragging = false;
  // The last width this page PAINTED, which is what gets committed. Measuring
  // the sidebar on release reads the wrong number: `#sidebar` carries a 300ms
  // width transition, so a `getBoundingClientRect` taken the instant the
  // pointer comes up is somewhere mid-animation -- measured, a drag from 264
  // to 400 committed 264 and the sidebar snapped back when the daemon's answer
  // arrived.
  let wanted = null;

  // THE LIST'S width, which is now the SAME as the sidebar's own
  // (docs/sidebar-collapse.md deleted the strip that used to sit beside it
  // and add to `#sidebar`'s total -- see e2e M2/M3's history, measured
  // 2026-09-26 against the strip: a 500px drag painted 556px when this
  // measured the whole box instead).
  const list = () => document.querySelector(".sb-main");
  function widthNow() {
    return wanted != null ? wanted
      : Math.round(list().getBoundingClientRect().width);
  }
  function paint(px) { wanted = px; paintSidebarWidth(px); }
  function commit(px) {
    // The daemon answers with what it STORED, which is the clamped value, so
    // a drag past a bound snaps to the bound rather than to wherever the
    // pointer stopped.
    // `saveShortcut` repaints from the daemon's ANSWER, which is the clamped
    // number, so a drag past a bound snaps back to the bound on its own and
    // this page holds no copy of the bounds.
    saveShortcut({ sidebar_width: px });
  }

  handle.addEventListener("pointerdown", (e) => {
    // Left button only: a right-click on the edge must not start a gesture
    // that only a pointerup can end.
    if (e.button !== 0) return;
    dragging = true;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.documentElement.dataset.sbdrag = "1";
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    // From the sidebar's LEFT edge to the pointer, not a delta accumulated
    // across moves: a delta drifts away from the cursor over a long drag and
    // never comes back.
    paint(Math.round(e.clientX - list().getBoundingClientRect().left));
  });
  function end(e) {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    delete document.documentElement.dataset.sbdrag;
    if (e && e.pointerId != null && handle.hasPointerCapture(e.pointerId)) {
      handle.releasePointerCapture(e.pointerId);
    }
    commit(widthNow());
  }
  handle.addEventListener("pointerup", end);
  // A drag the browser took away -- a window switch, a dropped pointer -- must
  // not leave the page in a state where every mouse move resizes it.
  handle.addEventListener("pointercancel", end);
  handle.addEventListener("lostpointercapture", end);

  // KEYBOARD. A control that can only be dragged is unreachable to anyone not
  // using a mouse, and `role="separator"` with a value is exactly the shape
  // that says arrow keys move it.
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 48 : 16;
    let px = null;
    if (e.key === "ArrowRight") px = widthNow() + step;
    else if (e.key === "ArrowLeft") px = widthNow() - step;
    else return;
    e.preventDefault();
    paint(px);
    commit(px);
  });
})();

function openDrawer() {
  document.documentElement.dataset.drawer = "open";
  paintDrawerInert();
  paintBurger();
}
function closeDrawer() {
  document.documentElement.dataset.drawer = "closed";
  paintDrawerInert();
  paintBurger();
}
function drawerOpen() { return document.documentElement.dataset.drawer === "open"; }
// A SHUT PHONE DRAWER IS OFF-CANVAS BY `transform`, which leaves every control
// in it in the Tab order (review 2026-09-26, the settings surface's defect in
// a second place). Only below the stylesheet's drawer breakpoint: on a desk
// `data-drawer="closed"` is the ordinary state and the list is on screen.
const DRAWER_Q = matchMedia("(max-width: 900px)");
function paintDrawerInert() {
  document.querySelector(".sb-main").inert = DRAWER_Q.matches && !drawerOpen();
}
DRAWER_Q.addEventListener("change", paintDrawerInert);
// Crossing 900px changes which mechanism `listOnScreen` reads, so the toggle
// (docs/sidebar-collapse.md, AC-296) has to repaint the same moment.
DRAWER_Q.addEventListener("change", paintBurger);
paintDrawerInert();

// Toggled here and then persisted: the control answers the press at once and
// the daemon's copy catches up. A refusal reaches the project popover's note in
// the daemon's own words rather than silently reverting the thing just clicked.
function toggleSidebar() {
  const collapsed = document.documentElement.dataset.sidebar !== "collapsed";
  // AC-299: collapsing must not strand focus inside a list that just left
  // the Tab order. `display: none` removes it silently, so without this a
  // Shift+Tab (or a screen reader's own recovery) starts from wherever the
  // browser happens to land rather than from the control that did it.
  if (collapsed && document.activeElement
      && document.querySelector(".sb-main").contains(document.activeElement)) {
    $("burger").focus();
  }
  setSidebar(collapsed);
  saveShortcut({ sidebar_collapsed: collapsed });
}
// WHICHEVER MECHANISM THE CURRENT WIDTH USES (docs/sidebar-collapse.md,
// AC-294..AC-296): collapsed-or-open on a desk, the drawer on a phone. Never
// written back on a phone (D-2): a phone crossing 900 px would otherwise
// overwrite the desk's stored preference.
function listOnScreen() {
  return DRAWER_Q.matches ? drawerOpen()
    : document.documentElement.dataset.sidebar !== "collapsed";
}
// THE BUSY DOT (AC-296): the loudest thing any conversation OTHER than the
// one on screen is doing, in the same waiting-over-error-over-running order
// `projectStatus` already uses for a project line's own dot -- and only
// while the list itself is not showing it, or the two would say the same
// thing twice.
function burgerBusyStatus() {
  if (listOnScreen()) return "";
  let waiting = false, error = false, running = false;
  for (const c of chats) {
    if (c.id === liveChatId) continue;
    if (c.status === "waiting") waiting = true;
    else if (c.status === "error") error = true;
    else if (c.status === "running") running = true;
  }
  if (waiting) return "waiting";
  if (error) return "error";
  return running ? "running" : "";
}
// ONE CONTROL, REPAINTED FROM EVERYWHERE ITS FACTS CHANGE: `setSidebar`,
// `openDrawer`/`closeDrawer`, the breakpoint crossing, and every chat-list
// repaint (AC-294..AC-296). The dot is never the only channel (AC-174) -- the
// words carry the same fact for a screen reader and for the tooltip.
function paintBurger() {
  const shown = listOnScreen();
  const act = shown ? "Hide" : "Show";
  const status = burgerBusyStatus();
  const btn = $("burger");
  btn.setAttribute("aria-expanded", String(shown));
  if (status) {
    btn.dataset.status = status;
    $("burgerDot").hidden = false;
    btn.setAttribute("aria-label",
      act + " the conversation list -- an agent elsewhere is " + status);
    btn.setAttribute("data-help", act + " list -- " + status + " elsewhere");
  } else {
    delete btn.dataset.status;
    $("burgerDot").hidden = true;
    btn.setAttribute("aria-label", act + " the conversation list");
    btn.setAttribute("data-help", act + " list");
  }
}
// ONE HANDLER FOR THE TOGGLE AT EVERY WIDTH (phase 1): `DRAWER_Q` decides
// collapse-the-desk-list versus open-the-phone-drawer, which used to be
// inline in the Ctrl+B branch alone -- now Ctrl+B and this press both reach
// it, so the two could not drift apart.
function toggleList() {
  if (DRAWER_Q.matches) { if (drawerOpen()) closeDrawer(); else openDrawer(); }
  else toggleSidebar();
}
$("burger").onclick = toggleList;
// Crossing 900px swaps the desk panel for the phone sheet, so what is shown
// is re-decided the same moment.
DRAWER_Q.addEventListener("change", () => { files.paint(); library.paint(); });
$("sbScrim").onclick = closeDrawer;

// --------------------------------------------------------------------------- //
// THE ONE RIGHT PANEL (AC-699, lantern-ui section 2)
//
// Files and Library are two TABS of one panel. Each module still owns its own
// panel, its desk state through `setRight` (`right_panel` in the store, one
// value, so "both open" cannot be held) and its phone sheet; this only says
// which tab a press means. The header's toggle opens the tab last shown and
// shuts whichever is up; the sidebar's Library row opens the Library tab;
// Ctrl+Shift+E stays the files tab's chord (FE-5).
// --------------------------------------------------------------------------- //
function panelShown() {
  if (files.shown()) return "files";
  return library.shown() ? "library" : "";
}
function openPanelTab(which, viaKey) {
  const mod = which === "library" ? library : files;
  if (!mod.shown()) mod.toggle(viaKey);
}
function togglePanel() {
  const now = panelShown();
  if (now === "files") files.toggle(false);
  else if (now === "library") library.toggle(false);
  else openPanelTab(lastPanel, false);
}
// ONE painter for every control that says whether the panel is up: the
// header's toggle, the sidebar's Library row and the tabs. Both modules call
// it from their own `paint` (`env.painted`), so it can never lag a change.
function paintPanelBtn() {
  const now = panelShown();
  if (now) lastPanel = now;
  const btn = $("panelBtn");
  btn.setAttribute("aria-expanded", String(!!now));
  btn.setAttribute("aria-label", (now ? "Hide" : "Show") + " files and library");
  $("libraryBtn").setAttribute("aria-expanded", String(now === "library"));
  for (const tab of document.querySelectorAll(".ptab")) {
    tab.setAttribute("aria-pressed", String(tab.dataset.panel === now));
  }
}
$("panelBtn").onclick = togglePanel;
$("libraryBtn").onclick = () => { closeDrawer(); openPanelTab("library", false); };
for (const tab of document.querySelectorAll(".ptab")) {
  tab.onclick = () => openPanelTab(tab.dataset.panel, true);
}

// The settings sheet (docs/lantern-ui.md 5.1, AC-700). Modal over a page that
// KEEPS LISTENING -- the microphone, the socket and the playback worklet are
// all still running behind it, which is why this is a sheet and not a second
// document (AC-83's reason, kept). Nothing here is stored: `open` is view
// state that dies with the page, and every VALUE inside it belongs to the
// daemon. `key`, when a picker's "More in settings" passes one, opens the
// sheet on that setting's page (`settings.reveal`).
function openPanel(open, key) {
  $("panel").classList.toggle("open", open);
  $("scrim").classList.toggle("open", open);
  // OUT OF THE TAB ORDER WHEN SHUT, AND THE PAGE BEHIND IT WHEN OPEN (review
  // 2026-09-26). Shut, the surface is only `opacity: 0`, so Tab walked into
  // an invisible "reset all"; open, it covers the whole screen and blocks the
  // pointer, and Shift+Tab walked out onto the covered microphone. Set BEFORE
  // the focus() below, because focus() into an inert subtree is refused.
  // EVERY other child of <body>, not `.app` alone: the skip link sits outside
  // it and Shift+Tab reached it, and whatever is added at the root next would
  // be the same hole.
  $("panel").inert = !open;
  // The scrim is NOT made inert: the sheet no longer covers the screen, the
  // dimmed page around it is the scrim, and a press there closes the sheet
  // (AC-706). It holds nothing focusable, so Tab still cannot leave the sheet.
  for (const el of document.body.children) {
    if (el !== $("panel") && el !== $("scrim")) el.inert = open;
  }
  $("settingsBtn").setAttribute("aria-expanded", String(open));
  // AC-101, the focus half. Opening the surface used to leave focus on
  // #settingsBtn, outside the panel, and one Tab landed on `mic-arc` -- a
  // control the full-screen surface covers and blocks the pointer from. So
  // focus moves to the first control INSIDE the panel on the way in and back
  // to the button that opened it on the way out.
  //
  // THIS WAS "deliberately NOT a focus trap, aria-modal false" until
  // 2026-09-26, on the argument that the page behind is still listening. It
  // still is -- listening is the microphone and the socket, which `inert`
  // does not touch -- but tabbing out reached controls the surface covers and
  // the pointer cannot, which is focus on something nobody can see. So the
  // page behind is inert while this is open and `aria-modal` is "true".
  // The Settings control lives in the sidebar, which on a phone is an overlay
  // -- so opening the surface from it leaves a drawer behind the thing that
  // covers the whole screen, and Escape would then take two presses to get
  // back to the conversation.
  if (open) { closeShellPops(); closeDrawer(); settings.reveal(key || null); }
  // Back to the gear that opened it -- or, on a phone, where the gear sits in
  // the drawer this just shut, to the panel icon that opens that drawer. On
  // the way in, the sheet's close button -- or, on a phone page where that
  // button gives way to Back, Back.
  const shut = $("closeSettings");
  const to = open ? (shut.offsetParent !== null ? shut : $("setBack"))
    : (DRAWER_Q.matches ? $("burger") : $("settingsBtn"));
  if (to) to.focus();
}
function panelOpen() { return $("panel").classList.contains("open"); }
$("settingsBtn").onclick = () => openPanel(!panelOpen());
$("closeSettings").onclick = () => openPanel(false);
$("scrim").onclick = () => openPanel(false);
window.addEventListener("keydown", (e) => {
  // Escape is SCOPED, and the scoping is what makes AC-111 safe to have on a
  // key at all (AC-112). Stopping ends the turn AND the coding agent behind
  // it, which is the only irreversible act on this page, so it must never be
  // one stray keystroke away from an idle screen or from the dialog-dismiss
  // reflex.
  //
  // THE CONTRACT IS "DISMISS THE TOPMOST SURFACE, ELSE STOP", and it always
  // was: settings-open already outranked stopping before the sidebar existed.
  // The phone drawer is a surface by that reading, so it takes its place in
  // the same order and the rule does not change -- which is the reasoning
  // docs/familiar-ui.md 6.2 recommends over "Escape always stops". The cost is
  // stated rather than hidden: an open drawer now makes the stop key
  // conditional on what else is open. What buys it back is that an overlay
  // with no keyboard dismissal is a trap on a phone with a keyboard attached,
  // and that the microphone control and the composer's own stop button both
  // reach `stopTurn` with no key at all.
  //
  // The order below is the STACKING order and is checked against it: the
  // settings surface is z-index 20 over a scrim at 19, and the drawer is 12
  // over a scrim at 11, the phone's files sheet 14 over 13. SIX cases, in
  // this order and no other:
  //   settings open       -> close it, and stop NOTHING
  //   drawer open         -> close it, and stop NOTHING
  //   files sheet open    -> close it, and stop NOTHING (phone only)
  //   both shut, busy     -> stop
  //   idle with a draft   -> clear the composer
  //   anything else       -> do nothing at all
  //
  // THE FIFTH CASE IS NEW AND IT REVERSES A DECISION (the owner's ask of
  // 2026-09-11: "ESC should function just like stop and cancel message
  // input, or output if agent is answering already"). The argument that lost
  // is kept rather than deleted, because half of it survives and decides the
  // ORDER: a user who has learned that Escape stops Butler must not find that
  // a text field silently disabled it (6.2). That is exactly why clearing
  // comes LAST -- with a turn in flight Escape still stops, whatever is in
  // the box, so the thing that key is trusted for is untouched. What lost is
  // only the claim that the two can never be told apart; the owner's own
  // phrasing is the rule that tells them apart.
  // Ctrl+B hides and shows the conversation list (AC-230), which is what every
  // familiar assistant binds it to. `toggleList` is the SAME function the
  // header's own toggle presses (docs/sidebar-collapse.md phase 1), so the
  // two can never decide differently. Never while the settings surface is
  // open: that surface covers the sidebar, so the keystroke would move
  // something nobody can see.
  if ((e.key === "b" || e.key === "B") && e.ctrlKey && !e.shiftKey
      && !e.altKey && !e.metaKey && !panelOpen()) {
    e.preventDefault();
    toggleList();
    return;
  }
  // Ctrl+Shift+E shows and hides the project's files, Orca's own chord (VS
  // Code muscle memory, docs/file-explorer.md FE-5) -- the SAME function the
  // panel's Files tab reaches (`openPanelTab`). `code`, not `key`: Shift
  // turns the key into "E" and a layout can move it.
  if (e.code === "KeyE" && e.ctrlKey && e.shiftKey && !e.altKey
      && !e.metaKey && !panelOpen()) {
    e.preventDefault();
    files.toggle(true);
    return;
  }
  if (e.key === "Escape") {
    // The library's dialogs take no case here: each closes in its own
    // capture-phase listener (`modal`) and takes the key whole (CU-15). The
    // Tasks and automations page keeps its first place (it sits over the
    // conversation column; its J / K / X / R are its own listener's).
    if (automations.isOpen()) automations.closePage();
    else if (panelOpen()) openPanel(false);
    else if (drawerOpen()) closeDrawer();
    // The PHONE sheets only: they cover the page like the drawer does. The
    // desk panels are not surfaces and take no case here (AC-112).
    else if (files.sheetOpen()) files.closeSheet();
    else if (library.sheetOpen()) library.closeSheet();
    else if (state === THINKING || state === SPEAK || state === CAPTURE) stopTurn();
    // The slash picker is a popover over the composer: only a stop outranks
    // it, the look popover's rule, and it is dismissed before the draft is.
    else if (library.slashOpen()) library.closeSlash();
    else if (composerInput.value !== "") {
      composerInput.value = "";
      growInput();
    }
    return;
  }
  // `/` jumps to the search box, the one shortcut a settings page is expected
  // to have. Only while the surface is open, and never while something is
  // already taking typing -- a slash in a model id must reach the field.
  if (e.key === "/" && panelOpen() && !e.metaKey && !e.ctrlKey) {
    const at = document.activeElement;
    const tag = at ? at.tagName : "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    settings.focusSearch();
  }
});

// --------------------------------------------------------------------------- //
// THE SHEETS: Diagnostics, Keyboard shortcuts, Restart Butler (AC-706..AC-708)
//
// Native modal <dialog>s. Like the settings surface they are SURFACES: Escape
// closes the one open and stops nothing, through a capture-phase listener on
// the dialog itself -- never a new case in the global handler (CU-15). The
// page behind keeps its microphone and socket; `showModal` makes only the
// controls inert. On close, focus goes back to the control that opened it,
// or to the account row when that control sat in the now-shut menu.
// --------------------------------------------------------------------------- //
let sheetOpener = null;
function openSheet(dlg, opener) {
  sheetOpener = opener || document.activeElement;
  closeShellPops();
  closeDrawer();
  if (!dlg.open) dlg.showModal();
}
for (const dlg of document.querySelectorAll("dialog.sheet")) {
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    dlg.close();
  }, true);
  // A press on the backdrop (the dialog box itself, outside its content).
  dlg.addEventListener("pointerdown", (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener("close", () => {
    const usable = (el) => el && el.isConnected && el.offsetParent !== null
      && !el.closest("[inert]");
    const back = usable(sheetOpener) ? sheetOpener
      : (DRAWER_Q.matches ? $("burger") : $("acctBtn"));
    sheetOpener = null;
    if (back) back.focus();
  });
}

// THE LIBRARY'S DIALOGS (recipe editor, run with values, automation editor;
// lantern-ui AC-706, CU-15). Modal like the sheets, but a dialog is a TASK
// over the conversation, not a surface: Escape closes it in a capture-phase
// listener on the dialog itself and, while a reply or a capture is live,
// goes on to the global handler's stop -- the popovers' rule, so a dialog
// never makes the stop conditional. A press on the backdrop (the dialog box
// itself, outside its form) closes it too.
function modal(dlg, close) {
  // A MODAL dialog takes Escape whole: it closes and the key goes no further,
  // even while Butler is busy. Letting it reach the global handler as well
  // would make one press both dismiss an editor and STOP the turn -- the one
  // irreversible control on the page (AC-111) -- which the old order never
  // did: dialogs sat above the stop (AC-112, docs/familiar-ui.md 6.2). Only
  // POPOVERS yield to the stop (CU-15); a modal is not a popover.
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    close();
  }, true);
  dlg.addEventListener("pointerdown", (e) => { if (e.target === dlg) close(); });
}

// DIAGNOSTICS (AC-707): `#diagPanel` and every `hud()` slot inside it.
function openDiag(opener) {
  $("diagPanel").hidden = false;
  openSheet($("diagSheet"), opener);
}
$("diagSheet").addEventListener("close", () => { $("diagPanel").hidden = true; });
$("diagClose").onclick = () => $("diagSheet").close();
$("diagBtn").onclick = () => openDiag($("acctBtn"));

// THE ERROR LINE'S "Details" opens the same sheet at `#errors`, which still
// holds every line the line summarised (CU-13).
$("errStripDetails").onclick = () => {
  openDiag($("errStripDetails"));
  $("errors").hidden = false;
  $("errors").scrollIntoView({ block: "nearest" });
};

// KEYBOARD SHORTCUTS (AC-708). The six keys this page binds are in the
// markup; every project chord is the DAEMON's answer, read at open -- the page
// holds no chord table.
function chordKeys(chord) {
  const dt = document.createElement("dt");
  for (const part of String(chord).split("+")) {
    const k = document.createElement("kbd");
    k.textContent = part;
    dt.append(k);
  }
  return dt;
}
function keyLine(chord, words) {
  const line = document.createElement("div");
  const dd = document.createElement("dd");
  dd.textContent = words;
  line.append(chordKeys(chord), dd);
  return line;
}
async function paintKeys() {
  const host = $("keysProjects");
  let doc;
  try { doc = await shortcutsFetch("GET"); }
  catch (e) {
    const p = document.createElement("p");
    p.className = "diagline";
    p.textContent = "Butler did not answer with the project keys ("
      + (e.message || e) + ").";
    host.replaceChildren(p);
    return;
  }
  const list = document.createElement("dl");
  list.className = "keylist";
  if (doc.dictate) list.append(keyLine(doc.dictate, "Dictate into any app"));
  for (const row of doc.rows || []) {
    for (const agent of doc.agents || []) {
      const chord = (row.chords || {})[agent];
      if (chord) list.append(keyLine(chord, "Open " + agent + " in "
                                     + projectName(row)));
    }
  }
  if (!list.childElementCount) {
    const p = document.createElement("p");
    p.className = "diagline";
    p.textContent = "No project keys are set. A project's menu, Set keys, "
      + "binds them.";
    host.replaceChildren(p);
    return;
  }
  host.replaceChildren(list);
}
$("keysBtn").onclick = () => {
  openSheet($("keysSheet"), $("acctBtn"));
  void paintKeys();
};
$("keysClose").onclick = () => $("keysSheet").close();

// RESTART BUTLER (account menu): confirmed, then the settings surface's own
// `restartDaemon`.
$("restartBtn").onclick = () => openSheet($("restartDlg"), $("acctBtn"));
$("restartCancel").onclick = () => $("restartDlg").close();
$("restartGo").onclick = () => {
  void restartDaemon($("restartGo"));
  setTimeout(() => $("restartDlg").close(), 600);
};

$("errStripDismiss").onclick = () => { errDismissed = true; paintErrLine(); };

// --------------------------------------------------------------------------- //
// THE SHELL'S POPOVERS: the voice popover and the account menu (AC-686)
//
// Popovers, not surfaces (CU-15): no scrim, no focus trap, and NO case in the
// global Escape handler -- that handler's last cases STOP a reply (AC-112)
// and clear the composer, and neither may become conditional on a menu. Each
// closes on a `pointerdown` anywhere outside it and on Escape through a
// capture-phase listener on its WRAPPER (so the key is seen while focus is
// still on the button that opened it), swallowing the key only when it would
// otherwise clear the composer. One open at a time.
// --------------------------------------------------------------------------- //
function popover(btn, pop, wrap) {
  const isOpen = () => !pop.hidden;
  function set(open) {
    pop.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  }
  btn.addEventListener("click", () => {
    const open = !isOpen();
    closeShellPops();
    set(open);
  });
  document.addEventListener("pointerdown", (e) => {
    if (isOpen() && !wrap.contains(e.target)) set(false);
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !isOpen()) return;
    set(false);
    btn.focus();
    if (!escapeWouldStop()) e.stopPropagation();
  }, true);
  return { close: () => set(false), isOpen };
}
const voicePop = popover($("voiceChev"), $("voicePop"),
                         $("voiceChev").closest(".voicewrap"));
const acctPop = popover($("acctBtn"), $("acctPop"), $("acctBtn").closest(".acct-row"));
// Every shell popover shut, from one place: opening another, a sheet, a
// phone panel sheet, the settings surface.
function closeShellPops() {
  voicePop.close();
  acctPop.close();
  setModelOpen(false);
  closeAgentMenu(false);
  closeModelSheet(false);
}
$("acctSettings").onclick = () => openPanel(true);
$("tasksPageBtn").onclick = () => { closeShellPops(); closeDrawer(); automations.openPage(); };

// --------------------------------------------------------------------------- //
// HOVER HELP, ONLY WHERE A WORD IS MISSING (u-railB1 item 2, owner's ruling
// of 2026-09-16: "the helper text above elements is too obnoxious and
// long... not everything needs help")
//
// SUPERSEDES "help is on hover any button" (D8, docs/parallel-agents.md 2):
// a control with a visible word gets no tooltip at all -- the label IS the
// help -- and an icon-only control gets one to three words plus its
// shortcut, never a repeated sentence. `web/AGENTS.md` carries the rule.
//
// The mechanism is unchanged and is still ONE: a `data-help` attribute, ONE
// delegated listener, ONE positioned element. A per-control tooltip
// implementation is the second copy that drifts, and the drifted copy is the
// one that forgets to hide itself.
//
// Deliberately NOT the browser's own `title`, which is why every `title` in
// this directory became a `data-help` in the same commit: `title`'s delay is
// about a second and is not ours, it cannot be styled, and it NEVER appears
// for a keyboard user -- which is most of what "help on hover" has to mean for
// anyone not holding a mouse. Hence 350 ms on hover and IMMEDIATE on focus.
//
// It takes NO case in the global Escape handler. That handler's last cases
// stop a reply (AC-112) and clear the composer, and neither may become
// conditional on whether a hover happens to be showing. What hides it is a
// pointer leaving, a focus moving, a pointerdown, or a scroll.
// --------------------------------------------------------------------------- //
const TIP_DELAY_MS = 350;
let tipTimer = 0;
let tipFor = null;

function hideTip() {
  if (tipTimer) { clearTimeout(tipTimer); tipTimer = 0; }
  tipFor = null;
  tipWatch.disconnect();
  $("tip").hidden = true;
}

// A SHOWN TOOLTIP FOLLOWS ITS CONTROL'S `data-help`. `showTip` copies the text
// once, and a model pick is focused (so its tip shows) BEFORE the chosen value
// is written onto it -- so the tip went on naming the model the pick no longer
// had, over the popover (review 2026-09-26). Re-shown on a change, hidden when
// the help is removed.
const tipWatch = new MutationObserver(() => {
  const node = tipFor;
  if (!node) return;
  if (node.getAttribute("data-help")) showTip(node);
  else hideTip();
});

function showTip(node) {
  const text = node.getAttribute("data-help");
  if (!text) return;
  // A control does not explain itself while its OWN popover is open: the
  // tooltip would land on top of the surface the press just opened. Read off
  // `aria-haspopup` plus `aria-expanded`, which is what says "this button owns
  // a menu and the menu is up" -- the sidebar's collapse control also carries
  // `aria-expanded`, for a thing that is not a popover at all, so the pair is
  // what separates them.
  if (node.getAttribute("aria-haspopup") === "true"
      && node.getAttribute("aria-expanded") === "true") return;
  const tip = $("tip");
  tip.textContent = text;
  // THE SHORTCUT RENDERS AS A CHIP, NOT AS MORE PROSE (u-railB1 item 2's
  // `#tip .k` rule). Only icon-only controls carry `data-key`, and only a
  // real, already-bound chord -- nothing here invents one.
  const key = node.getAttribute("data-key");
  if (key) {
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = key;
    tip.append(k);
  }
  tip.hidden = false;
  tipFor = node;
  tipWatch.disconnect();
  tipWatch.observe(node, { attributes: true, attributeFilter: ["data-help"] });
  const b = node.getBoundingClientRect();
  const r = tip.getBoundingClientRect();
  let x = b.left + b.width / 2 - r.width / 2;
  x = Math.max(8, Math.min(x, window.innerWidth - r.width - 8));
  let y = b.top - r.height - 8;
  if (y < 8) y = b.bottom + 8;
  y = Math.max(8, Math.min(y, window.innerHeight - r.height - 8));
  tip.style.left = Math.round(x) + "px";
  tip.style.top = Math.round(y) + "px";
}

document.addEventListener("pointerover", (e) => {
  const n = e.target.closest && e.target.closest("[data-help]");
  if (!n || n === tipFor) return;
  if (tipTimer) clearTimeout(tipTimer);
  tipTimer = setTimeout(() => { tipTimer = 0; showTip(n); }, TIP_DELAY_MS);
});
document.addEventListener("pointerout", (e) => {
  const n = e.target.closest && e.target.closest("[data-help]");
  if (n && n === tipFor) hideTip();
  else if (n && tipTimer) { clearTimeout(tipTimer); tipTimer = 0; }
});
// IMMEDIATE on focus, which is the half `title` cannot do at all.
document.addEventListener("focusin", (e) => {
  const n = e.target.closest && e.target.closest("[data-help]");
  // KEYBOARD focus only (`:focus-visible`). A pick hands focus back to its
  // button after a CLICK, and the tip that raised landed over the popover
  // the owner was still looking at (review 2026-09-26); a pointer user gets
  // the hover path above instead.
  if (n && e.target.matches(":focus-visible")) showTip(n); else hideTip();
});
document.addEventListener("focusout", hideTip);
document.addEventListener("pointerdown", hideTip);
window.addEventListener("scroll", hideTip, true);
// A KEYSTROKE HIDES IT, and the ORDER is what makes that safe: `keydown`
// fires BEFORE the `focusin` a Tab causes, so tabbing still shows the next
// control's help, while typing into the composer -- whose own help would
// otherwise sit over the words being typed -- takes it away on the first
// character.
document.addEventListener("keydown", hideTip, true);

// --------------------------------------------------------------------------- //
// WHAT ANSWERS, IN THE COMPOSER'S ROW (AC-688)
//
// Two chips, `#agentBtn` (what answers) and `#modelBtn` (its model), over ONE
// popover. Everything inside is built by `settings.js` from the daemon's
// per-setting `quick` flag, so this file names no setting and no model
// (AC-41) and only owns the open-and-shut. Same rules as the shell's other
// popovers: no scrim, no focus trap, NO case in the global Escape handler.
// --------------------------------------------------------------------------- //
const MODEL_CHIPS = ["agentBtn", "modelBtn"];
function modelPopOpen() { return !$("modelPop").hidden; }
function setModelOpen(open) {
  $("modelPop").hidden = !open;
  for (const id of MODEL_CHIPS) $(id).setAttribute("aria-expanded", String(open));
}
for (const id of MODEL_CHIPS) {
  $(id).onclick = () => {
    const open = !modelPopOpen();
    voicePop.close();
    acctPop.close();
    closeModelSheet(false);
    setModelOpen(open);
  };
}
document.addEventListener("pointerdown", (e) => {
  if (!modelPopOpen()) return;
  if ($("modelBtn").parentElement.contains(e.target)) return;
  setModelOpen(false);
});
// ON THE WRAPPER, so it sees the key while focus is still on a CHIP. Opening
// by click leaves focus there, so a listener on the popover alone never saw
// Escape and it fell through to the global handler, which CLEARS THE
// COMPOSER -- measured by review 2026-09-11. Swallowed only when the key
// would otherwise clear a draft: with a reply in flight Escape means STOP.
$("modelBtn").parentElement.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !modelPopOpen()) return;
  setModelOpen(false);
  $("modelBtn").focus();
  if (!escapeWouldStop()) e.stopPropagation();
}, true);

// --------------------------------------------------------------------------- //
// THE CHAT'S AGENT PICKER (docs/chat-agent-picker.md option C, AP-1..AP-4)
//
// Everything comes from the daemon's chat payload (`agents`, `agent_default`,
// `agent_fixed`, `model`, `model_default`; see `paintChatAgent`): this file
// names no agent, no model and no setting (AC-777, AC-785). The door onto
// `#agentMenu` is a chevron beside each New chat button: the birth naming the
// agent. The composer's ONE chip (agent and model together) opens its own
// sheet, below.
// A popover like the others: no scrim, no focus trap, NO case in the global
// Escape handler (a capture-phase listener that yields while Escape would stop
// a turn, AC-112).
// --------------------------------------------------------------------------- //
let agentMenuAnchor = null;

function hasAgentChoice() { return agentInfo.agents.length > 1; }

// The chip is the chat's agent and model in the daemon's words (CM-6): the
// model is the chat's own, else "Default" and the resolved default's name in a
// span a phone drops. It is there for the whole life of a chat, because the
// model can change at any time.
function paintAgentPicker() {
  const chip = $("chatAgentChip");
  chip.hidden = !agentInfo.agents.length || !liveChatId;
  $("chatAgentChipName").textContent = agentInfo.label;
  $("chatModelName").textContent = agentInfo.model || "Default";
  $("chatModelPar").textContent =
    !agentInfo.model && agentInfo.modelDefault ? " (" + agentInfo.modelDefault + ")" : "";
  for (const c of document.querySelectorAll(".agentchev")) {
    c.hidden = !hasAgentChoice();
  }
  if (chip.hidden || (modelSheetOpen() && sheetChat !== liveChatId)) closeModelSheet(false);
  else if (modelSheetOpen()) renderModelSheet();
}

// The first taken turn fixes the agent (the daemon says 409 from then on); the
// next doc re-read confirms it.
function agentSpent() {
  if (agentInfo.fixed) return;
  agentInfo.fixed = true;
  paintAgentPicker();
}

function agentChevron(label) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "agentchev";
  b.hidden = !hasAgentChoice();
  b.setAttribute("aria-haspopup", "true");
  b.setAttribute("aria-expanded", "false");
  b.setAttribute("aria-controls", "agentMenu");
  b.setAttribute("aria-label", label);
  b.setAttribute("data-help", "Choose the agent");
  b.append(icon(14, 2, [["path", { d: "m6 9 6 6 6-6" }]]));
  return b;
}

function closeAgentMenu(refocus) {
  const anchor = agentMenuAnchor;
  agentMenuAnchor = null;
  $("agentMenu").hidden = true;
  if (!anchor) return;
  anchor.setAttribute("aria-expanded", "false");
  if (refocus && anchor.isConnected) anchor.focus();
}

function openAgentMenu(anchor, title, onPick) {
  if (agentMenuAnchor === anchor) { closeAgentMenu(false); return; }
  closeShellPops();
  closeAgentMenu(false);
  const menu = $("agentMenu");
  menu.textContent = "";
  const head = document.createElement("p");
  head.className = "pop-title";
  head.textContent = title;
  menu.append(head);
  for (const a of agentInfo.agents) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mi";
    b.setAttribute("role", "menuitem");
    const ck = document.createElement("span");
    ck.className = "agck";
    const nm = document.createElement("span");
    nm.textContent = a.label;
    b.append(ck, nm);
    if (a.name === agentInfo.dflt) {
      const d = document.createElement("span");
      d.className = "agdflt";
      d.textContent = "default";
      b.append(d);
    }
    b.onclick = () => { closeAgentMenu(false); onPick(a.name); };
    menu.append(b);
  }
  agentMenuAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  menu.hidden = false;
  const ar = (anchor.parentElement || anchor).getBoundingClientRect();
  const mr = menu.getBoundingClientRect();
  const left = Math.max(8, Math.min(ar.left, window.innerWidth - mr.width - 8));
  let top = ar.bottom + 4;
  if (top + mr.height > window.innerHeight - 8) top = ar.top - mr.height - 8;
  top = Math.max(8, top);
  menu.style.left = Math.round(left) + "px";
  menu.style.top = Math.round(top) + "px";
  menu.querySelector(".mi").focus();
}

// A New chat button's chevron: the birth is the main button's own (the busy
// gate, the draft clearing), only naming the agent.
function wireStartMenu(chev, idFor, mainBtn, mainLabel) {
  chev.onclick = () => openAgentMenu(chev, "Start the chat on",
    (name) => gatedNewChat(idFor, (id) => newChatFrom(mainBtn, mainLabel, id, name))());
}

async function setChatAgent(name) {
  const cid = liveChatId;
  if (!cid) return;
  try {
    const r = await fetch("/api/chats/" + encodeURIComponent(cid) + "/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent: name }),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) {
      // 409: the chat started while the menu was open. Say so and drop the chip.
      if (r.status === 409) agentSpent();
      showError(doc.error || ("could not change the agent (HTTP " + r.status + ")"));
      return;
    }
    if (cid === liveChatId) paintChatAgent(doc);
    loadChats();
  } catch (e) {
    showError("could not change the agent (" + (e.message || e) + ")");
  }
}

document.addEventListener("pointerdown", (e) => {
  if (!agentMenuAnchor) return;
  if ($("agentMenu").contains(e.target) || agentMenuAnchor.contains(e.target)) return;
  closeAgentMenu(false);
});
$("agentMenu").addEventListener("focusout", (e) => {
  const to = e.relatedTarget;
  if (to && !$("agentMenu").contains(to) && to !== agentMenuAnchor) closeAgentMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (!agentMenuAnchor) return;
  const menu = $("agentMenu");
  const at = document.activeElement;
  if (!menu.contains(at) && at !== agentMenuAnchor) return;
  if (e.key === "Escape") {
    closeAgentMenu(true);
    if (!escapeWouldStop()) e.stopPropagation();
    return;
  }
  const items = [...menu.querySelectorAll(".mi")];
  const i = items.indexOf(at);
  let to = null;
  if (e.key === "ArrowDown") to = items[(i + 1) % items.length];
  else if (e.key === "ArrowUp") to = items[(i <= 0 ? items.length : i) - 1];
  else if (e.key === "Home") to = items[0];
  else if (e.key === "End") to = items[items.length - 1];
  if (to) { e.preventDefault(); to.focus(); }
}, true);

// --------------------------------------------------------------------------- //
// THE COMPOSER'S ONE CHIP: AGENT AND MODEL TOGETHER (docs/chat-model.md CM-5..
// CM-9, AC-785, AC-786)
//
// `#chatAgentChip` opens `#modelSheet`, a popover on a desk and a bottom sheet
// at 600 px and below. Two sections: the agent (a radio while the chat has not
// started, else one read-only line) and the model (Default, the chat's own
// pick, Favourites, Recent, then every model folded, with a search box pinned
// above the list). The agent's models come from GET /api/agents/{agent}/models
// (fetched when the sheet opens, kept for the page's life per agent, refetched
// after a pick so Recent is current). Favourites are the daemon's shared list
// as served; the star lives in the settings sheet. A model the chat holds that
// the catalogue does not list stays, said "(not listed)" (CM-5). Same popover
// rules as the others: outside press closes, Escape yields to the stop.
// --------------------------------------------------------------------------- //
const modelCatalog = new Map();      // agent -> { state: loading|ok|error, data, error }
const modelFetching = new Set();
let sheetChat = "";
let sheetAllOpen = false;
const SHEET_FLAT_MAX = 8;        // a catalogue this short is never folded
const SHEET_MATCH_CAP = 200;
const SHEET_NARROW = "(max-width: 600px)";

function modelSheetOpen() { return !$("modelSheet").hidden; }

function loadAgentModels(agent) {
  if (modelFetching.has(agent)) return;
  modelFetching.add(agent);
  if (!modelCatalog.has(agent)) modelCatalog.set(agent, { state: "loading" });
  const fail = (error) => {
    const prev = modelCatalog.get(agent);
    if (!prev || prev.state !== "ok") modelCatalog.set(agent, { state: "error", error });
  };
  fetch("/api/agents/" + encodeURIComponent(agent) + "/models")
    .then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (r.ok) modelCatalog.set(agent, { state: "ok", data: d });
      else fail(d.error || ("could not list models (HTTP " + r.status + ")"));
    })
    .catch((e) => fail("could not list models (" + (e.message || e) + ")"))
    .then(() => {
      modelFetching.delete(agent);
      if (modelSheetOpen()) renderModelSheet();
    });
}

function sheetNote(text) {
  const p = document.createElement("p");
  p.className = "msnote";
  p.textContent = text;
  return p;
}

function sheetTitle(text) {
  const p = document.createElement("p");
  p.className = "pop-title";
  p.textContent = text;
  return p;
}

// One row. `key` identifies it across a re-render so focus can be put back.
function sheetRow(key, text, checked, onClick, role) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "mi";
  b.dataset.k = key;
  if (role) { b.setAttribute("role", role); b.setAttribute("aria-checked", String(checked)); }
  else b.setAttribute("aria-pressed", String(checked));
  const ck = document.createElement("span");
  ck.className = "agck";
  if (checked) ck.append(icon(14, 2.2, [["path", { d: "m5 12 5 5 9-10" }]]));
  const nm = document.createElement("span");
  nm.className = "msname";
  nm.textContent = text;
  b.append(ck, nm);
  b.onclick = onClick;
  return b;
}

function renderModelSheet() {
  const at = document.activeElement;
  const keep = at && at.dataset ? at.dataset.k : "";
  // Agent section.
  const ag = $("msAgent");
  ag.textContent = "";
  ag.append(sheetTitle("Agent"));
  if (agentInfo.fixed) {
    ag.append(sheetNote(agentInfo.label + " -- this chat stays on " + agentInfo.label));
  } else {
    const grp = document.createElement("div");
    grp.setAttribute("role", "radiogroup");
    grp.setAttribute("aria-label", "Agent");
    for (const a of agentInfo.agents) {
      grp.append(sheetRow("a:" + a.name, a.label, a.name === agentInfo.agent,
                          () => { if (a.name !== agentInfo.agent) setChatAgent(a.name); },
                          "radio"));
    }
    ag.append(grp);
  }
  // Model section.
  const list = $("msList");
  list.textContent = "";
  const entry = modelCatalog.get(agentInfo.agent);
  if (!entry) loadAgentModels(agentInfo.agent);
  const data = entry && entry.state === "ok" ? entry.data : null;
  const q = $("msSearch").value.trim().toLowerCase();
  const own = agentInfo.model;
  const pick = (name) => () => { closeModelSheet(true); setChatModel(name); };
  const hit = (m) => !q || m.toLowerCase().includes(q);
  if (!q) {
    list.append(sheetRow("m:", agentInfo.modelDefault
      ? "Default (" + agentInfo.modelDefault + ")" : "Default", !own, pick("")));
  }
  if (own && hit(own)) {
    const gone = !!data && !data.models.includes(own);
    list.append(sheetRow("m:" + own, gone ? own + " (not listed)" : own, true, pick(own)));
  }
  const rows = (names) => names.filter((m) => m !== own && hit(m))
    .map((m) => sheetRow("m:" + m, m, false, pick(m)));
  if (!entry || entry.state === "loading") list.append(sheetNote("Loading models"));
  else if (entry.state === "error") list.append(sheetNote(entry.error));
  else if (q) {
    const all = [...new Set([...data.models, ...data.favourites, ...data.recent])];
    const found = rows(all);
    list.append(sheetTitle(found.length ? "Matches" : "No model matches"));
    list.append(...found.slice(0, SHEET_MATCH_CAP));
    if (found.length > SHEET_MATCH_CAP) {
      list.append(sheetNote("Showing the first " + SHEET_MATCH_CAP + " of " + found.length
                            + ". Keep typing to narrow it."));
    }
  } else if (data.models.length <= SHEET_FLAT_MAX) {
    // A short catalogue (Claude Code's three) is shown whole: a fold, or
    // favourites and recents above it, would only hide what fits anyway.
    list.append(...rows(data.models));
  } else {
    for (const [title, names] of [["Favourites", data.favourites], ["Recent", data.recent]]) {
      const r = rows(names);
      if (r.length) list.append(sheetTitle(title), ...r);
    }
    const fold = document.createElement("button");
    fold.type = "button";
    fold.className = "mi msfold";
    fold.dataset.k = "fold";
    fold.setAttribute("aria-expanded", String(sheetAllOpen));
    const nm = document.createElement("span");
    nm.className = "msname";
    nm.textContent = "All models (" + data.models.length + ")";
    fold.append(nm, icon(14, 2, [["path", { d: "m6 9 6 6 6-6" }]]));
    fold.onclick = () => { sheetAllOpen = !sheetAllOpen; renderModelSheet(); };
    list.append(fold);
    if (sheetAllOpen) list.append(...rows(data.models));
  }
  if (keep) {
    const back = [...$("modelSheet").querySelectorAll(".mi")].find((n) => n.dataset.k === keep);
    if (back && at !== back) back.focus();
  }
}

function placeModelSheet() {
  const sheet = $("modelSheet");
  if (window.matchMedia(SHEET_NARROW).matches) {
    sheet.style.left = "";
    sheet.style.bottom = "";
    sheet.style.maxHeight = "";
    return;
  }
  const ar = $("chatAgentChip").getBoundingClientRect();
  sheet.style.maxHeight = Math.max(160, Math.round(ar.top - 16)) + "px";   // never above the page
  const w = sheet.getBoundingClientRect().width;
  sheet.style.left = Math.round(Math.max(8, Math.min(ar.left, window.innerWidth - w - 8))) + "px";
  sheet.style.bottom = Math.round(window.innerHeight - ar.top + 8) + "px";
}

function closeModelSheet(refocus) {
  if (!modelSheetOpen()) return;
  $("modelSheet").hidden = true;
  $("chatAgentChip").setAttribute("aria-expanded", "false");
  if (refocus) $("chatAgentChip").focus();
}

function openModelSheet() {
  if (!liveChatId || !agentInfo.agents.length) return;
  closeShellPops();
  sheetChat = liveChatId;
  sheetAllOpen = false;
  $("msSearch").value = "";
  const c = modelCatalog.get(agentInfo.agent);
  if (c && c.state === "error") modelCatalog.delete(agentInfo.agent);
  if (c && c.state === "ok") loadAgentModels(agentInfo.agent);   // Recent may have moved
  $("modelSheet").hidden = false;
  $("chatAgentChip").setAttribute("aria-expanded", "true");
  renderModelSheet();
  placeModelSheet();
  const first = $("modelSheet").querySelector('[aria-pressed="true"], [aria-checked="true"], .mi');
  if (first) first.focus();
}

async function setChatModel(name) {
  const cid = liveChatId;
  if (!cid) return;
  try {
    const r = await fetch("/api/chats/" + encodeURIComponent(cid) + "/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: name }),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) {
      showError(doc.error || ("could not change the model (HTTP " + r.status + ")"));
      return;
    }
    if (cid === liveChatId) paintChatAgent(doc);
    loadAgentModels(String(doc.agent || ""));                    // Recent after a pick
  } catch (e) {
    showError("could not change the model (" + (e.message || e) + ")");
  }
}

$("chatAgentChip").onclick = () => (modelSheetOpen() ? closeModelSheet(true) : openModelSheet());
$("msSearch").addEventListener("input", renderModelSheet);
window.addEventListener("resize", () => { if (modelSheetOpen()) placeModelSheet(); });
document.addEventListener("pointerdown", (e) => {
  if (!modelSheetOpen()) return;
  if ($("modelSheet").contains(e.target) || $("chatAgentChip").contains(e.target)) return;
  closeModelSheet(false);
});
$("modelSheet").addEventListener("focusout", (e) => {
  const to = e.relatedTarget;
  if (to && !$("modelSheet").contains(to) && to !== $("chatAgentChip")) closeModelSheet(false);
});
document.addEventListener("keydown", (e) => {
  if (!modelSheetOpen()) return;
  const sheet = $("modelSheet");
  const at = document.activeElement;
  if (!sheet.contains(at) && at !== $("chatAgentChip")) return;
  if (e.key === "Escape") {
    closeModelSheet(true);
    if (!escapeWouldStop()) e.stopPropagation();
    return;
  }
  const typing = at === $("msSearch");
  const items = [...sheet.querySelectorAll(".mi, #msSearch")];
  const i = items.indexOf(at);
  let to = null;
  if (e.key === "ArrowDown") to = items[(i + 1) % items.length];
  else if (e.key === "ArrowUp") to = items[(i <= 0 ? items.length : i) - 1];
  else if (!typing && e.key === "Home") to = items[0];
  else if (!typing && e.key === "End") to = items[items.length - 1];
  if (to) { e.preventDefault(); to.focus(); }
}, true);

$("resetAll").onclick = (e) => settings.resetAll(e);

// Restart the daemon so restart-flagged settings take effect without a manual
// stop/start. Hand-built like the reset and mode controls (web/AGENTS.md
// allows those); it names no setting and is not schema-driven. It POSTs
// /api/restart, says "Restarting" in the toast and on the button, and then does NOTHING to bring the daemon back -- the socket drops
// when the process re-execs and ws.onclose already reconnects on its own
// (RECONNECT_MS), re-running `ready` -> settings.refresh(), which is exactly
// the moment the restart-only settings have finally taken effect.
//
// The account menu's Restart Butler posts it (after its confirm). The
// settings sheet's restart bar has no button since AC-715: a change that
// needs a restart is applied by the daemon itself once nothing is running.
async function restartDaemon(btn) {
  if (btn.disabled) return;
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "restarting...";
  toast("Restarting Butler. This page reconnects by itself.");
  try {
    // The daemon acks 200 BEFORE it execs (it flushes the response first),
    // so this normally resolves; the socket drops a moment later.
    const r = await fetch("/api/restart", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    // The restart bar clears itself: the daemon that comes back serves an
    // empty `pending` on the `ready` re-read (AC-716).
  } catch (e) {
    // A network error here is not necessarily a failure: the process may have
    // execed as the response was in flight. Say so quietly and let reconnect
    // decide -- if it never comes back, the link chip shows "disconnected".
    logNote("restart request did not get a clean answer; waiting for the "
            + "daemon to come back (" + (e.message || e) + ")");
  } finally {
    // Restore the label after the daemon has had time to come back and the
    // socket to reconnect; a fresh `ready` does not touch this button.
    setTimeout(() => { btn.disabled = false; btn.textContent = prev; }, 8000);
  }
}

// Start a new conversation BORN IN A PROJECT (AC-63, AC-287): the click
// equivalent of saying "new conversation". ONE call names the project -- the
// old two-step birth (rotate, then `postProject`) is gone with the move it
// used (docs/filing-and-pacing.md 8, R-A), so a chat is never briefly
// unprojected or filed wherever the previous one lived. `projectId` is a ROW
// id the daemon listed, never a path; absent, the daemon births in the root.
// Hand-built beside `resetAll` and `restartDaemon`, and wired here rather than
// in settings.js for the same reason they are -- it names no setting.
// `label` is the element carrying the transient word.
async function newConversation(btn, label, projectId, agent) {
  if (btn.disabled) return false;
  automations.closePage(false);            // the new chat takes the column (CU-16)
  terminals.hide();
  const prev = label.textContent;
  btn.disabled = true;
  label.textContent = "starting...";
  try {
    const r = await fetch("/api/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(projectId ? { project: projectId } : {}),
                             ...(agent ? { agent } : {}) }),
    });
    if (r.status === 404) {
      // Endpoint mode: this daemon holds no conversation, so there is nothing
      // to rotate. Say that on the button's own face instead of reporting a
      // fresh start the daemon never made (AC-7b).
      label.textContent = "no conversation";
      logNote("this daemon is not running a harness, so it holds no "
              + "conversation to restart");
      return false;
    }
    const doc = await r.json().catch(() => ({}));
    // The daemon's own sentence, which names the id or the directory it
    // refused -- a birth refused is a birth that did not happen (D-A2).
    if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    chatProjectId = String(doc.projectId || "");
    ensureExpanded(chatProjectId);
    // The turns on screen belong to the conversation that just ended, so the
    // transcript is stale the moment the POST answers. loadSession REPLACES
    // the history from the daemon's own record -- which is now empty.
    await loadSession({ session: doc.id, turns: 0 });
    // The project's count moved with the birth (review 2026-09-26: New chat
    // left its project's count at the old number). Not awaited; a stale count
    // is cosmetic and the birth itself already happened.
    loadShortcuts();
    toast("New chat started");
    return true;
  } catch (e) {
    showError("could not start a new conversation (" + (e.message || e) + ")");
    return false;
  } finally {
    // Long enough to read the transient label, short enough that the button is
    // ready again before the next turn. A daemon can come back in the other
    // mode after a restart, so even the 404 state is restored rather than
    // latched: the next click asks again instead of guessing (D-1).
    setTimeout(() => { btn.disabled = false; label.textContent = prev; }, 2500);
  }
}
// THREE callers, one function (D-18): the strip's New chat, each project's
// "+ New chat" and the settings surface's "new conversation" are the same
// act, and differ only in which project they name. The two chat buttons also
// share the pre-work -- closing the drawer, clearing the draft that New chat
// is one of the three ways to clear (docs/familiar-ui.md 6.2).
function newChatFrom(btn, label, projectId, agent) {
  closeDrawer();
  composerInput.value = "";
  growInput();
  newConversation(btn, label, projectId, agent);
  composerInput.focus();
}

// A RECIPE OR A TASK, RUN IN A NEW CHAT (docs/automations.md AU-4, AC-449).
// No new daemon path: the SAME gate every New chat passes (a busy project
// asks first), the SAME birth (`POST /api/session/new {project}`), and then
// the text as an ORDINARY typed message -- so the run is this device's own
// turn and its reply streams here like any other. Answers whether the text
// was sent. `btn` carries the transient "starting..." word; an icon-only
// button lends a detached label instead, so its glyph is never overwritten.
async function runInNewChat(projectId, text, btn) {
  if (!(await gateNewChat(projectId))) return false;
  const label = btn.querySelector("svg") ? document.createElement("span") : btn;
  if (!(await newConversation(btn, label, projectId))) return false;
  sendTyped(text);
  return true;
}

// The slash picker's choice goes INTO the box and is never sent (AC-450):
// the owner edits it and presses Enter themselves.
function insertComposerText(text) {
  composerInput.value = text;
  growInput();
  composerInput.focus();
  composerInput.setSelectionRange(text.length, text.length);
}

// --------------------------------------------------------------------------
// THE CONFIRM-TO-STOP GATE (docs/filing-and-pacing.md 1.7, phase 5).
//
// The owner's ruling, verbatim: "Starting a new chat on a project where
// there's already an active agent, should show a message to the user to
// confirm... if i decline, the whole gesture is cancelled. no new chat
// created." So declining is the safe default and skips EVERYTHING below
// it, including `/api/session/new` -- which is why this sits in FRONT of
// `newConversation`/`newChatFrom` rather than inside either: a function
// that already rotated the daemon's held conversation could not un-rotate
// it if the owner then said no.
//
// A UI-LOCAL confirmation and NOT #askCard's daemon-tracked question
// mechanism (the spec's own words for this): the daemon never learns this
// dialog exists until "yes" POSTs `/api/project/{id}/stop`, the ONE route
// that may ever call `hub.stop_conversation` from a collision
// (`daemon/butlerd.py`'s own docstring on it). An ordinary turn colliding
// with a busy project still refuses with `CapReached`, exactly as before
// this existed -- nothing here touches that path.
//
// THE ONE OVERLAY SHAPE (AC-706, Step 6c): a `dialog.sheet.confirm` like
// Restart, so the sheets' own listeners close it on Escape (taken WHOLE: a
// modal never also stops Butler, CU-15) and on its backdrop, and focus goes
// back to the control that asked. Every way it closes without "Stop" is a
// "no", resolved by the dialog's `close` event.
let stopConfirmResolve = null;

function closeStopConfirm(result) {
  const resolve = stopConfirmResolve;
  stopConfirmResolve = null;
  if ($("stopConfirm").open) $("stopConfirm").close();
  if (resolve) resolve(result);
}

// The agent that would be stopped, from the listing the sidebar draws (its
// `harness` and title) -- said only when the listing has it, never guessed,
// and with no clock: the listing's elapsed time is as old as the listing.
function busyAgentLine(row) {
  const c = chats.find((x) => x.projectId === row.id
                        && (x.status === "running" || x.status === "waiting"));
  if (!c) return "";
  return (c.harness || "The agent") + " in " + projectName(row)
    + (c.title ? ": " + c.title : "");
}

// Resolves true or false, never rejects. Cancel, the backdrop and Escape
// all resolve false -- the SAFE choice is the one reachable with no
// deliberate aim, which is also why focus lands on Cancel rather than on
// the destructive button when this opens. The drawer is left as it was: a
// modal dialog sits in the top layer over it.
function askStopConfirm(row) {
  return new Promise((resolve) => {
    stopConfirmResolve = resolve;
    $("stopConfirmBody").textContent = projectName(row) + " already has an "
      + "agent running. A new chat there stops it first; what it already "
      + "wrote stays.";
    const line = busyAgentLine(row);
    $("stopConfirmAgent").textContent = line;
    $("stopConfirmAgent").hidden = !line;
    sheetOpener = document.activeElement;
    closeShellPops();
    $("stopConfirm").showModal();
    $("stopConfirmCancel").focus();
  });
}

$("stopConfirmCancel").onclick = () => closeStopConfirm(false);
$("stopConfirmGo").onclick = () => closeStopConfirm(true);
$("stopConfirm").addEventListener("close", () => closeStopConfirm(false));

// Which project the STRIP's New chat and the settings surface's "new
// conversation" are born in: the project of the conversation on screen, else
// the root (D-A1) -- `shortcuts.root_id` is the daemon's own answer to which
// row that is (AC-41). The tree's "+ New chat" names its own project instead.
function currentProjectId() {
  return chatProjectId || (shortcuts && shortcuts.root_id) || null;
}

function projectRow(id) {
  return ((shortcuts && shortcuts.rows) || []).find((r) => r.id === id)
    || null;
}

// Resolves true when it is fine to proceed -- no collision, or the owner
// confirmed and the stop route answered -- and false when the owner
// declined, or the confirm's own network call failed. NEVER creates a chat:
// that stays `newConversation`'s job, run by the caller only when this
// resolves true. Asks about the EXACT project the chat would be born in.
async function gateNewChat(id) {
  if (!id) return true;
  // THE DAEMON'S ANSWER AT CLICK TIME, never the page's cached rows (e2e
  // audit S10a): those were refreshed only at the end of a turn, so the
  // confirm was skipped right after a turn started and asked about an agent
  // that had already finished. `busy` is `_annotate_busy`, computed per
  // answer from the same state the stop route acts on (D-18). A daemon that
  // cannot answer is a gesture that does not happen -- it is not a "no".
  let fresh;
  try {
    fresh = await shortcutsFetch("GET");
  } catch (e) {
    showError("could not ask whether this project is busy ("
              + (e.message || e) + ")");
    return false;
  }
  paintShortcuts(fresh);
  const row = (fresh.rows || []).find((r) => r.id === id) || null;
  if (!row || !row.busy) return true;
  const proceed = await askStopConfirm(row);
  if (!proceed) return false;
  try {
    const r = await fetch(
      "/api/project/" + encodeURIComponent(id) + "/stop", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
  } catch (e) {
    showError("could not stop the running agent (" + (e.message || e) + ")");
    return false;
  }
  return true;
}

// `idFor` is read at CLICK time, so the strip's button asks about the
// conversation on screen then, and not when the page was built.
function gatedNewChat(idFor, fn) {
  return () => {
    void (async () => {
      const id = idFor();
      if (await gateNewChat(id)) fn(id);
    })();
  };
}

$("newChat").onclick = gatedNewChat(currentProjectId, (id) => newChatFrom(
  $("newChat"), $("newChat").querySelector(".lbl"), id));
wireStartMenu($("newChatChev"), currentProjectId, $("newChat"),
              $("newChat").querySelector(".lbl"));

// --------------------------------------------------------------------------
// THE PROJECTS, AND A PROJECT'S OWN CONTROLS BEHIND ITS "..."
// (docs/shortcuts.md 4.8, AC-133..AC-137; docs/filing-and-pacing.md 8, R-D)
//
// Until 2026-09-11 this was `#shortPanel`, a fold BELOW the chat list; then a
// rail of TILES that filtered one flat list; since 2026-09-26 each project is
// a node of the sidebar's TREE (`projectNode`), with the strip's chips for
// when the tree is not on screen. Everything the pane could do is still one
// press behind the project's "..." rather than deleted.
//
// Wired HERE beside restartDaemon and the new-conversation button, for the
// reason those two are: settings.js owns only the schema-driven surface, and
// this names no setting -- the agent names and the key list come from the
// daemon's own answer, so nothing in this file writes down a closed daemon
// table (AC-41).
//
// `app.js` has no fetch helper, so these call fetch directly like every other
// hand-built control here.
// --------------------------------------------------------------------------
let shortcuts = { open: false, show_chords: false, sidebar_collapsed: false,
                  expanded_projects: [], root_id: "",
                  dictate: "", rows: [], agents: [], keys: [], bare_keys: [] };
// The row whose path and label are being edited, by id, or null. One at a time:
// two open editors would each hold a stale copy of the other's row, and an
// upsert replaces a row WHOLE.
let editing = null;
// The ROW the LIVE conversation is working in, as the daemon last said --
// read off `/api/session/current`, never remembered from a click, so a switch
// made on the phone marks the right row on the desk (D-2, the owner's backlog
// item 4). "" means no row matches: either the conversation belongs to no
// project, which is every conversation written before workspaces existed, or
// its project was removed from the pane. The LIST tells those two apart,
// because each row carries its own path beside its id; nothing up here needs
// to. The path itself is deliberately NOT held here any more -- comparing it
// against a pane row is comparing a spelling with an identity, and that is the
// defect this replaced (review, 2026-09-11).
let chatProjectId = "";
// How long an agent button stays disabled after a launch (AC-147). A control
// that starts a full-capability coding agent must not be double-clickable into
// two of them, and an impatient second click on a slow spawn is the normal case
// rather than the perverse one -- `wt.exe` was measured taking up to a second to
// hand off. This is the page-side twin of the latch that stopped one held chord
// becoming 138 of them (`desk/AGENTS.md`).
const LAUNCH_COOLDOWN_MS = 1200;
// The chord slot currently listening for a keystroke, or null. One at a time:
// two armed slots would both take the same press and the second write would
// silently lose to a collision refusal.
let capturing = null;
// Which project's controls are open, by row id -- or ADD_POP for the form that
// adds one, or null for none. A GESTURE, so it dies with the page and is never
// written to the daemon: the pane's old `open` flag was a preference because
// the pane was a fold in the scroll, and a popover is not.
const ADD_POP = "\u0000add";
let projPopFor = null;
// The note element inside the open popover. Rebuilt with it, so this holds
// whichever one is on screen and `shortNote` never writes into a detached node.
let noteEl = null;

function shortNote(message) {
  if (!noteEl) return;
  noteEl.textContent = message || "";
  noteEl.hidden = !message;
}

async function shortcutsFetch(method, body, path) {
  const r = await fetch(path || "/api/shortcuts",
                        body === undefined
                          ? { method }
                          : { method, headers: { "Content-Type": "application/json" },
                              body: JSON.stringify(body) });
  const doc = await r.json().catch(() => ({}));
  // The daemon's own sentence, which names the key and what already holds it.
  // Thrown rather than returned, so every caller shows it in the same place.
  if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
  return doc;
}

// The chord a keystroke describes, in the shape the DAEMON maps. `code` and
// never `key`: `key` is layout-dependent, so binding on it means one physical
// key produces a different binding on a Russian or AZERTY layout. There is
// deliberately no key table in this file (docs/shortcuts.md D3).
function chordFromEvent(e) {
  return { code: e.code, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey,
           meta: e.metaKey };
}

// A chord slot: the agent's name, the chord under it, and a click that arms a
// capture. `onChord` is given the captured chord, or null when the owner
// cancelled, or "" to clear the binding.
function chordSlot(name, chord, onChord) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chordbtn";
  const who = document.createElement("span");
  who.textContent = name;
  const shown = document.createElement("span");
  shown.className = "chord";
  btn.append(who, shown);
  const paint = () => {
    // "bind" rather than "unbound", because with the launch button above it this
    // slot is now a call to action rather than a status: the row already works
    // without a chord, and the slot is how you add one.
    shown.textContent = chord || "bind";
    btn.setAttribute(
      "aria-label",
      chord ? name + " chord, currently " + chord + ". Press to rebind."
            : name + " chord, not set. Press to bind one.");
    // NO TOOLTIP (u-railB1 item 2): the button already shows the agent's
    // name and its chord (or "bind"), so the label is the help.
  };
  paint();

  const disarm = () => {
    capturing = null;
    btn.removeAttribute("data-capturing");
    shown.textContent = chord || "bind";
    window.removeEventListener("keydown", onKey, true);
  };

  function onKey(e) {
    // BOTH, and both are load-bearing (docs/shortcuts.md 4.8). The single
    // global keydown listener in this file consumes Escape unconditionally and
    // `/` while the settings surface is open, so without stopPropagation
    // binding Escape would close the surface instead; and without
    // preventDefault the key also types, or triggers the browser's own action.
    e.stopPropagation();
    e.preventDefault();
    if (e.key === "Escape") { disarm(); return; }
    // A modifier on its own is not a chord and must not end the capture -- the
    // owner is on their way to pressing Ctrl+something.
    if (["Control", "Alt", "Shift", "Meta"].indexOf(e.key) >= 0) return;
    if (e.key === "Backspace" || e.key === "Delete") {
      // Clearing a slot is an ordinary edit, and Backspace is where a person
      // reaches for it. Sent as "" rather than as a chord, which the daemon
      // reads as unbound.
      disarm();
      onChord("");
      return;
    }
    const captured = chordFromEvent(e);
    disarm();
    onChord(captured);
  }

  btn.onclick = () => {
    if (capturing === btn) { disarm(); return; }
    if (capturing) capturing.click();          // disarm the other slot first
    capturing = btn;
    btn.setAttribute("data-capturing", "true");
    shown.textContent = "press keys";
    shortNote("Press the keys for this shortcut. Escape cancels, Backspace "
              + "clears it. A key with no modifier has to be one almost "
              + "nothing else uses: " + shortcuts.bare_keys.join(", ") + ".");
    // CAPTURE phase, so this runs before the page's own global handler rather
    // than merely alongside it.
    window.addEventListener("keydown", onKey, true);
  };
  return btn;
}

// A row's agent button: it OPENS a terminal, and that is the only thing it does
// (AC-146, AC-149). The chord slot beneath it is a different control, and the
// disclosure that reveals it never changes what this button means -- so the
// page's most consequential click is never ambiguous.
function agentButton(row, agent) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "agentbtn";
  const word = menuWord("Open " + agent + " here");
  btn.append(TERMINAL_ICON(), word);
  // AC-100's rule: a button reading "pi" computes an accessible name that says
  // nothing about what it opens or where.
  btn.setAttribute("aria-label",
                   "Open " + agent + " in " + (row.label || row.path));
  btn.onclick = async () => {
    if (btn.disabled) return;
    // An armed chord capture is swallowing keystrokes; launching means the owner
    // moved on, so it is disarmed rather than left listening.
    if (capturing) capturing.click();
    const prev = word.textContent;
    btn.disabled = true;
    word.textContent = "opening...";
    shortNote("");
    try {
      const r = await fetch("/api/terminal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd: row.path, agent }),
      });
      const doc = await r.json().catch(() => ({}));
      // A 400 for a directory that is not there is the COMMON failure here: a
      // row's path is something the owner typed and may since have renamed. A
      // click that did nothing and said nothing is indistinguishable from a
      // dead button (AC-148, AC-7b).
      if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    } catch (e) {
      shortNote(e.message || String(e));
    } finally {
      // No success state is latched. The daemon cannot see whether the terminal
      // became VISIBLE -- `wt.exe` onto a locked screen or a dropped remote
      // session opens a window nobody can look at and the route still answers
      // 200 -- so the WINDOW is the confirmation, not this button
      // (`docs/shortcuts.md` 9.7).
      setTimeout(() => {
        btn.disabled = false;
        word.textContent = prev;
      }, LAUNCH_COOLDOWN_MS);
    }
  };
  return btn;
}

// Clicking a row's name opens this: the path and the label, editable in place
// (AC-134's modify half). It saves through the SAME upsert the add box uses.
function rowEditor(row) {
  const box = document.createElement("div");
  box.className = "shortedit";
  const path = document.createElement("input");
  path.type = "text";
  path.value = row.path;
  path.spellcheck = false;
  path.setAttribute("aria-label", "Project directory");
  const label = document.createElement("input");
  label.type = "text";
  label.value = row.label;
  label.spellcheck = false;
  label.placeholder = "name (optional)";
  label.setAttribute("aria-label", "Project name");
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "save";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "cancel";
  cancel.onclick = () => { editing = null; paintShortcuts(shortcuts); };
  save.onclick = async () => {
    editing = null;
    // `chords: row.chords` is LOAD-BEARING. An upsert replaces the row whole,
    // so a save that sent only the path and label would silently unbind every
    // chord on it -- and the owner would find out the next time they pressed
    // one, with nothing on screen connecting the two.
    await saveShortcut({ row: { id: row.id, path: path.value.trim(),
                               label: label.value.trim(),
                               chords: row.chords } });
  };
  box.append(path, label, save, cancel);
  return box;
}

// ONE project's controls, the whole of what `#shortPanel`'s row used to hold.
// Same class names, same order, same rules -- it is the HOST that moved.
function projectControls(row) {
  const el = document.createElement("div");
  el.className = "shortrow";
  if (editing === row.id) {
    el.append(rowEditor(row));
    return el;
  }
  const who = document.createElement("button");
  who.type = "button";
  who.className = "who";
  who.setAttribute("aria-label",
                   "Edit " + (row.label || row.path) + " and its directory");
  const name = document.createElement("b");
  name.textContent = projectName(row);
  const where = document.createElement("span");
  where.textContent = row.path;
  who.append(name, where);
  who.onclick = () => { editing = row.id; paintShortcuts(shortcuts); };
  el.append(who);
  // One column per agent the DAEMON says it can start, IN THE ORDER IT SERVES
  // THEM -- this file sorts nothing and names none of them (AC-41, AC-151).
  // The launch button is always there; the chord slot only when the owner has
  // asked to see chords (D8).
  const agents = document.createElement("div");
  agents.className = "agentrow";
  for (const agent of shortcuts.agents) {
    const col = document.createElement("div");
    col.className = "agentcol";
    col.append(agentButton(row, agent));
    if (shortcuts.show_chords) {
      col.append(chordSlot(agent, row.chords[agent] || "", async (chord) => {
        const chords = Object.assign({}, row.chords);
        chords[agent] = chord;
        await saveShortcut({ row: { id: row.id, path: row.path,
                                   label: row.label, chords } });
      }));
    }
    agents.append(col);
  }
  el.append(agents);
  const gap = document.createElement("div");
  gap.className = "gapline";
  el.append(gap);
  const tools = document.createElement("div");
  tools.className = "agentrow";
  // An EXPLICIT edit button beside remove. Clicking the row's NAME does the
  // same thing and did from the start, but that affordance was styled as
  // plain text -- deliberately quiet, so as not to compete with the three
  // controls that start coding agents -- and quiet turned out to mean
  // invisible: the owner asked for a feature that was already there. A
  // control nobody can find is not a control (AC-134).
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "edit";
  edit.append(EDIT_ICON(), menuWord("Edit project"));
  edit.setAttribute("aria-label",
                    "Edit " + (row.label || row.path) + " and its directory");
  edit.onclick = () => { editing = row.id; paintShortcuts(shortcuts); };
  tools.append(edit);
  // Never offered on the ROOT row: the daemon refuses the DELETE anyway
  // (`ShortcutStore.remove`, R10), but offering a control that always fails
  // is its own defect -- the owner clicked it and was told no
  // (docs/e2e-journeys.md S9d). `shortcuts.root_id` is the daemon's own
  // answer to which row that is; this names no path of its own (AC-41).
  if (row.id !== shortcuts.root_id) {
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "drop";
    drop.append(DELETE_ICON(), menuWord("Remove project"));
    drop.setAttribute("aria-label", "Remove " + (row.label || row.path));
    drop.onclick = async () => {
      try {
        const answer = await shortcutsFetch("DELETE", undefined,
                                            "/api/shortcuts/" + row.id);
        closeProjPop();
        paintShortcuts(answer);
      } catch (e) { shortNote(e.message || String(e)); }
    };
    tools.append(drop);
  }
  el.append(tools);
  // "SET KEYS", the chord disclosure (lantern-ui CU-16). Its state is the
  // DAEMON's, like every other view flag in that store, so this waits to be
  // told rather than holding a copy -- and the launch buttons mean the same
  // thing whichever way it is set, which is the point of having it (D8).
  const chords = document.createElement("button");
  chords.type = "button";
  chords.className = "showchords";
  chords.id = "showChords";
  chords.setAttribute("aria-expanded", String(!!shortcuts.show_chords));
  chords.append(CARET_ICON(), menuWord("Set keys"));
  chords.onclick = async () => {
    await saveShortcut({ show_chords: !shortcuts.show_chords });
  };
  // Before `remove`, which stays last: the one control here that loses data.
  tools.insertBefore(chords, tools.children[1] || null);
  return el;
}

// The form that ADDS a project, plus the dictation chord -- the two things in
// the old pane that belonged to no single row. Behind the rail's `+`.
function projectAdder() {
  const box = document.createElement("div");
  box.className = "shortrow";
  const head = document.createElement("div");
  head.className = "sb-head";
  const word = document.createElement("span");
  word.textContent = "Dictation chord";
  const slot = document.createElement("span");
  slot.style.marginLeft = "auto";
  // The dictation chord is a row in the same table as the terminals, which is
  // what "one binding table" means (AC-137).
  const dictate = chordSlot("dictate", shortcuts.dictate, async (chord) => {
    if (chord === "") {
      // Refused by the daemon on purpose: dictation with no chord is the
      // feature switched off by a control that does not say so. Said here
      // rather than making the round trip for it.
      shortNote("Dictation cannot be unbound. Turn hotkeys off in settings "
                + "instead.");
      return;
    }
    await saveShortcut({ dictate: chord });
  });
  dictate.id = "dictateChord";
  slot.append(dictate);
  head.append(word, slot);
  box.append(head);

  const addHead = document.createElement("div");
  addHead.className = "sb-head";
  addHead.append("Add a project");
  box.append(addHead);

  const add = document.createElement("div");
  add.className = "shortadd";
  const path = document.createElement("input");
  path.type = "text";
  path.id = "shortPath";
  path.spellcheck = false;
  path.placeholder = "E:\\PROJECTS\\something";
  path.setAttribute("aria-label", "Project directory");
  const label = document.createElement("input");
  label.type = "text";
  label.id = "shortLabel";
  label.spellcheck = false;
  label.placeholder = "name (optional)";
  label.setAttribute("aria-label", "Project name");
  const browse = document.createElement("button");
  browse.type = "button";
  browse.id = "shortBrowse";
  browse.textContent = "browse...";
  browse.onclick = () => browseFor(browse, path);
  const commit = document.createElement("button");
  commit.type = "button";
  commit.id = "shortAdd";
  commit.textContent = "add";
  commit.onclick = async () => {
    const value = path.value.trim();
    if (!value) { shortNote("Name a directory first."); return; }
    await saveShortcut({ row: { path: value, label: label.value.trim() } });
  };
  add.append(path, label, browse, commit);
  box.append(add);
  return box;
}

async function browseFor(btn, field) {
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "choosing...";
  try {
    const r = await fetch("/api/browse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: field.value.trim() }),
    });
    // 204 is a cancel and is not an error: nothing is drawn for it, which is
    // why the daemon does not answer 200 with an empty path.
    if (r.status === 204) return;
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    field.value = doc.path || "";
  } catch (e) {
    shortNote("the folder chooser did not answer (" + (e.message || e) + ")");
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

// WHAT A PROJECT IS CALLED, in one place. The daemon's label when there is one,
// otherwise the path's last component -- never the whole path, which in a
// 64 px rail or a 264 px column is an ellipsis with no information in it.
//
// BOTH separators in the class. `[\/]` is just `/` -- the backslash is a
// redundant escape inside a character class -- so an earlier version of this
// never split a Windows path at all and `.pop()` handed back the whole thing,
// which looked exactly like the bug it was meant to fix (review 2026-09-11).
function projectName(row) {
  return row.label
    || row.path.split(/[\\/]/).filter(Boolean).pop() || row.path;
}

// --------------------------------------------------------------------------
// THE POPOVER
//
// NOT a surface: no scrim, no focus trap, and NO case in the global Escape
// handler -- that handler's last case STOPS a reply (AC-112) and must not
// become conditional on whether a menu is open. What closes it is a
// `pointerdown` anywhere else, plus a capture-phase listener of its own that
// swallows Escape only while the focus is inside it. That is the appearance
// pill's arrangement and the composer pickers', for the same reason.
// --------------------------------------------------------------------------
function projPopOpen() { return !$("projPop").hidden; }

function closeProjPop() {
  if (capturing) capturing.click();
  projPopFor = null;
  editing = null;
  noteEl = null;
  const pop = $("projPop");
  pop.hidden = true;
  pop.textContent = "";
  for (const b of document.querySelectorAll("#sidebar [aria-haspopup]")) {
    b.setAttribute("aria-expanded", "false");
  }
}

function renderProjPop() {
  const pop = $("projPop");
  pop.textContent = "";
  if (projPopFor === null) { pop.hidden = true; noteEl = null; return; }
  const wrap = document.createElement("div");
  wrap.className = "shortrows";
  if (projPopFor === ADD_POP) {
    wrap.append(projectAdder());
    pop.setAttribute("aria-label", "Add a project");
  } else {
    const row = shortcuts.rows.find((r) => r.id === projPopFor);
    if (!row) { closeProjPop(); return; }
    wrap.append(projectControls(row));
    pop.setAttribute("aria-label", projectName(row));
  }
  pop.append(wrap);
  noteEl = document.createElement("p");
  noteEl.className = "shortnote";
  noteEl.id = "shortNote";
  noteEl.setAttribute("role", "status");
  noteEl.hidden = true;
  pop.append(noteEl);
  pop.hidden = false;
  placeProjPop();
}

// Positioned against the control that opened it, and CLAMPED to the viewport.
// `position: fixed`, because the rail is 64 px wide with `overflow: hidden`
// and an absolutely positioned child of it would be clipped -- which is the
// trap the sidebar resize handle already paid for once.
let projPopAnchor = null;
function placeProjPop() {
  const pop = $("projPop");
  if (pop.hidden || !projPopAnchor || !projPopAnchor.isConnected) return;
  const a = projPopAnchor.getBoundingClientRect();
  const b = pop.getBoundingClientRect();
  // UNDER the "..." and flush with its right edge (the export's project
  // menu); above it when there is no room below.
  let x = a.right - b.width;
  x = Math.max(8, Math.min(x, window.innerWidth - b.width - 8));
  let y = a.bottom + 6;
  if (y + b.height > window.innerHeight - 8) y = a.top - 6 - b.height;
  y = Math.max(8, Math.min(y, window.innerHeight - b.height - 8));
  pop.style.left = Math.round(x) + "px";
  pop.style.top = Math.round(y) + "px";
}

function openProjPop(which, anchor) {
  const again = projPopFor === which;
  closeProjPop();
  if (again) return;
  projPopFor = which;
  projPopAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  renderProjPop();
}

document.addEventListener("pointerdown", (e) => {
  if (!projPopOpen()) return;
  if ($("projPop").contains(e.target)) return;
  if (e.target.closest && e.target.closest("#sidebar [aria-haspopup]")) return;
  closeProjPop();
});
// CAPTURE phase on the popover, so Escape closes it without ever reaching the
// page's global handler -- but only while the focus is inside it, and only
// SWALLOWED when the key would otherwise clear the composer. With Butler
// answering, one press closes this AND stops him (AC-112).
$("projPop").addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !projPopOpen()) return;
  const anchor = projPopAnchor;
  closeProjPop();
  if (anchor && anchor.isConnected) anchor.focus();
  if (!escapeWouldStop()) e.stopPropagation();
}, true);
window.addEventListener("resize", placeProjPop);

// --------------------------------------------------------------------------
// NEW CHAT'S OWN LABEL (docs/sidebar-collapse.md; was `paintProjRail`, which
// also built one tile per project -- the strip those tiles lived on is
// deleted, and this is the one piece of that function with nowhere else to
// go, since it does not depend on the tree being drawn).
// --------------------------------------------------------------------------
function paintNewChatLabel() {
  // New Chat says WHERE it will be born, because that depends on the
  // conversation on screen and a button whose destination is a guess is the
  // thing R-A exists to remove. `aria-label` only, never `data-help`: the
  // sidebar row's `.lbl` is a visible word, and D8 bans a tooltip on a
  // control that already carries one.
  const here = projectRow(currentProjectId());
  $("newChat").setAttribute("aria-label", "Start a new conversation"
                            + (here ? " in " + projectName(here) : ""));
  if (projPopOpen()) placeProjPop();
}

function paintShortcuts(doc) {
  // CARRY THE COUNTS FORWARD (review, 2026-09-11). `GET /api/shortcuts` counts
  // the conversations in each row; `POST /api/shortcuts` deliberately does NOT,
  // because it is the route a sidebar DRAG posts to on every release and a
  // count nobody asked for is not worth reading every chat on disk for. But
  // this function replaces the rows wholesale, so a drag, a collapse, a chord
  // bind, a rename or an add silently deleted the count from every tile until
  // the next reload. Absent means UNANSWERED here, never zero.
  if (doc && Array.isArray(doc.rows) && Array.isArray(shortcuts.rows)) {
    const had = new Map(shortcuts.rows.map((r) => [r.id, r.chats]));
    for (const row of doc.rows) {
      if (typeof row.chats !== "number" && typeof had.get(row.id) === "number") {
        row.chats = had.get(row.id);
      }
    }
  }
  shortcuts = doc;
  // The SIDEBAR's own collapsed state rides on this same answer (AC-166,
  // docs/familiar-ui.md 6.3). It is view state, AC-8 bans browser storage
  // outright, and this store already carries the chord disclosure -- so a
  // third boolean here costs nothing and buys a sidebar that survives a reload
  // and agrees across two devices (D-2).
  // The width the DAEMON holds, applied on every answer -- so it is right at
  // boot, and a drag on the tablet moves this window's sidebar too (D-2).
  // Skipped mid-drag: the pointer is the authority for the length of the
  // gesture, and writing the daemon's older number under it would fight it.
  if (typeof doc.sidebar_width === "number"
      && !document.documentElement.dataset.sbdrag) {
    paintSidebarWidth(doc.sidebar_width);
  }
  if (typeof doc.sidebar_collapsed === "boolean") {
    setSidebar(doc.sidebar_collapsed);
  }
  // WHICH PANEL THE RIGHT COLUMN SHOWS rides on the same answer (AC-312,
  // AC-466), and the ROWS both panels name their project by do too -- so
  // they re-root here as well: the root row is only known once this lands.
  if (typeof doc.right_panel === "string" && doc.right_panel !== rightPanel) {
    rightPanel = doc.right_panel;
    files.rightChanged(false);
    library.rightChanged(false);
  }
  files.follow();
  library.follow();
  // WHICH PROJECTS THE TREE SHOWS OPEN (D-D2), a further independent fact on
  // the same answer. The daemon drops ids no row has, so a removed project's
  // open state is never inherited by anything.
  if (Array.isArray(doc.expanded_projects)) {
    expandedProjects = new Set(doc.expanded_projects);
  }
  paintNewChatLabel();
  paintChatList();
  // Kept open across a save, because every write here repaints: an edit that
  // closed the thing being edited would need re-opening for the next one.
  if (projPopFor !== null) renderProjPop();
}

async function saveShortcut(body) {
  try {
    shortNote("");
    paintShortcuts(await shortcutsFetch("POST", body));
  } catch (e) {
    // The daemon's own sentence, which names the key and what already holds it.
    // Shown rather than swallowed, because the refusals here are the ones the
    // owner has to read: a bare letter would suppress their keyboard.
    shortNote(e.message || String(e));
  }
}

async function loadShortcuts() {
  try {
    paintShortcuts(await shortcutsFetch("GET"));
  } catch (e) {
    // A daemon with no store answers 404, and New Chat's label simply stays
    // as it is. Not an error banner: this pane is not what the page is for.
    paintNewChatLabel();
  }
}

$("projAdd").onclick = () => openProjPop(ADD_POP, $("projAdd"));

// The settings panel. It draws itself from GET /api/settings -- schema and
// values in one answer -- so this file names no setting and cannot drift from
// the daemon. It owns the permission mode buttons too, which post to
// /api/mode: the one client path that is allowed to RAISE the mode, as
// against the socket's mode_request, which the daemon refuses to escalate.
// Whether Escape is currently spoken for by something more important than
// closing a menu. The global handler's order is panel, drawer, STOP, then
// clear the composer; only the stop outranks a popover. CAPTURE joined
// THINKING/SPEAK with `stopTurn()` itself (cause 1): a popover open over a
// wedged capture must still let Escape reach the stop, exactly as it does
// over a reply that will not finish.
function escapeWouldStop() {
  return state === THINKING || state === SPEAK || state === CAPTURE;
}

const settings = mountSettings({
  root: $("settingsRoot"),
  // The category list, the sheet (its `searching` class and its phone back
  // stack, `data-phone`), and the phone page's Back.
  navRoot: $("navRoot"),
  sheet: $("panel"),
  back: $("setBack"),
  // The box a category page scrolls INSIDE, put back to its top on every
  // page change.
  scroller: $("setScroll"),
  modeRoot: $("modeRoot"),
  connRoot: $("connRoot"),
  search: $("setSearch"),
  searchClear: $("setSearchClear"),
  commitRoot: $("commitBar"),
  showError,
  onValues: applyPageValues,
  // The Diagnostics sheet shows the permission mode with the settings shut,
  // filled from the panel's own boot read rather than sitting on "-" until
  // `ready`.
  onMode: (mode) => hud("modeLabel", mode),
  // A voice preview is Butler's own speech out of the speaker while this
  // page's microphone is live, so it gets the same treatment as an earcon:
  // the mic is muted for its length plus a reverberation tail, and the frames
  // are ZEROED rather than dropped so the pre-roll byte count still matches
  // its declared header (AC-109, D-15, AC-5b). settings.js cannot do this --
  // the mic graph lives here -- and the daemon cannot either, because a
  // preview deliberately never goes near a turn.
  onAudition: (ms) => suppressMic(ms, "voice preview"),
  // The account menu's Appearance row (lantern-ui section 2, CU-6): the
  // theme's own control, built from the daemon's choices and labels, so the
  // page lists no value. `theme` is named here as `applyPageValues` already
  // names it to write `data-theme`; everything else that was in the header's
  // appearance popover lives in Settings, Appearance.
  menuControls: [{ key: "theme", host: $("acctAppear") }],
  // WHAT ANSWERS, in the composer's row (AC-688). The popover is the only
  // element this file hands over; WHICH settings go in it, and the word each
  // one carries, are the daemon's per-setting `quick` flag -- so nothing here
  // names a setting or a model (AC-41).
  pickRoot: $("modelPop"),
  pickTitle: "Model for this chat",
  // The two chips: the short word of the setting the daemon says is
  // ANSWERING (`answers`), and its value. Both the daemon's (AC-41).
  pickWho: $("agentBtnName"),
  pickLabel: $("modelBtnName"),
  // The door at the bottom of a picker. A control offering three favourites
  // has to lead somewhere when the model you want is not one of them, or it is
  // a dead end wearing a list.
  onOpenSettings: (key) => openPanel(true, key),
  escapeStops: escapeWouldStop,
});

// The wave. Created before the microphone exists so the page has something
// alive to show while it is off, and fed real nodes by start().
//
// No `threshold` here: it is the daemon's (AC-805) and arrives after this
// runs; `paintWakeWords` hands it to `viz.setThreshold` with the label (D-28).
try {
  viz = window.ButlerVisualiser.create($("wave"), $("trace"),
                                       { field: $("field"),
                                         // The voice-effect slot (STEP 2,
                                         // docs/voice-effects.md, AC-257):
                                         // handed over exactly like `field`
                                         // above, so visualiser.js never
                                         // looks up an id or a class itself.
                                         fx: $("composerFx") });
} catch (e) {
  // Cosmetic, so it must never be fatal: a page that refuses to run because a
  // canvas failed is a satellite that cannot hear you either.
  showError("visualiser unavailable: " + (e.message || e));
}
// The wave's colours come from the style the page is in (CU-4); until the
// daemon answers, that is the default style written on <html>.
applyMesh();

// Where the wave lives, before the daemon has said. `band` is the setting's
// own default and the closest thing to what the page shipped with, so an
// unreachable daemon leaves a recognisable page rather than a blank one. The
// setting replaces it on the first schema answer (AC-167).
setVizPlacement("band");

// The pane and the conversation list, at load. Over HTTP and not over the
// socket, so both are drawn before the microphone is armed -- the fold's own
// state and the sidebar's are in the first answer, and a fold that snapped
// open a second after the page appeared would read as a glitch.
loadShortcuts();
loadChats();
terminals.refresh();

hud("hLookback", Math.round(PREROLL_LOOKBACK_MS) + " ms");
hud("hStt", "-");
$("deviceLine").textContent = DEVICE_NAME + " " + DEVICE_ID.slice(0, 8);
growInput();
setState(OFF);
setLink("idle", false);

// The socket and the speaker, at load, before anything has touched a
// microphone (AC-175, AC-177). This is the whole of "Butler can be used with
// the microphone off": `connect()` used to be the last line of `start()`, so
// no device meant no `ready`, no restored conversation, no `end_phrases` and
// no way to send a typed message at all.
//
// Neither is awaited and neither may be fatal. `openPlayback` builds an
// AudioContext, which some browsers refuse outside a user gesture and others
// hand back suspended; a page that stopped here would be a page with no
// microphone AND no composer, which is worse than the thing being fixed
// (D-1). Every path that makes a sound resumes the context itself.
openPlayback().then(sendCaps).catch((e) => {
  showError("no speaker on this page yet: " + (e.message || e)
            + " -- replies will still arrive as text");
});
connect();
