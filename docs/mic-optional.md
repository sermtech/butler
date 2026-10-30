# The microphone stops being the power switch

Owner's ask, 2026-09-10, verbatim:

> "with microphone off, the app cannot be used at all. no texted messages to
> be sent at all. we need a better schema and an additional button to turn stt
> on/off. speaker to turn tts on and off, speaker icon so that user is able to
> play/stop manually per message; All settings with persistence in between
> sessions."

and, when asked how the controls should read (2026-09-10, answering three
questions about this spec):

> "i need a better solution - mic button and header pill should both work as
> buttons for agent status. I need a wave button so user can press to start
> asr and press again to add to chat input, independently of mic button
> status. and another button for tts overall on and off"

Criteria AC-175..AC-184. They stay here until the owner accepts them;
`plan.md` is still the contract.

**BUILT 2026-09-10, all four steps of section 6.** What the build found that
this spec did not predict is in section 6a; read that before changing anything
here, because one of the three is a defect this page can grow again.

---

## 0. The finding that sizes the job

**The whole coupling is one line.** `web/app.js:2535` calls `connect()` as the
last statement of `start()`, after `getUserMedia` has resolved. Both of
`start()`'s failure paths return before it -- a wake model that will not load
(`:2502`) and a microphone that is absent or denied (`:2529`) -- so a page with
no microphone has no WebSocket, and `sendTyped` (`:2697`) hits its own honest
refusal on every message: *"nothing was sent: this page has not reached
Butler"*.

**The daemon needs no change for that half.** A session that never captures is
already first-class and has been since arbitration was written:

- arbitration is opt-in -- `hub.py:875-896` only arbitrates between sessions
  that ask;
- a binary frame arriving outside a capture is explicitly NOT an error
  (`hub.py:633-637`);
- broadcasts go to every session regardless of what it has ever sent;
- `hello.caps` is never read anywhere in the daemon;
- `_await_playback` returns at once for a device that was sent no audio.

So a socket-only satellite is a satellite that happens never to speak, and
every daemon-side rule already holds for it.

**What the page loses today without the mic is not only typing.** No `ready`
means: no engine chips, no `turn_mode`, no `end_phrases`, no restored
conversation, no cleared `linkSkin`. `test/manual/probe_page.py` needs
`--live` and a fake audio device to see ANY daemon-supplied value, and
`probe_ux.py:467-478` carries the bug in a comment: *"Arming the microphone is
what opens the socket... so with no click there is no `ready` frame and
`loadSession` never runs."*

---

## 1. What the owner actually asked for, split into four controls

The second answer replaces this spec's first draft, which proposed one
speaker toggle and nothing else. Four controls, and each answers a different
question:

| Control | Question it answers | Where |
|---|---|---|
| Mic button | is Butler listening for the wake phrase? | composer |
| Header state pill | (the same question, from the other end of the page) | header |
| **Wave button** | transcribe what I am about to say, into the box | composer |
| **Speaker button** | will Butler read replies aloud? | composer |

Plus a fifth, per message: **play this reply again**.

**The wave button is the interesting one and it needs almost no new code.**
"Press to start ASR, press again to add to chat input" is DICTATION -- the
thing `POST /api/dictate` has done since `docs/desk-hotkeys.md` shipped
(AC-113, AC-115, AC-116). The route takes a raw 16 kHz mono int16 body, the
page's `mic-worklet.js` already produces exactly those frames (it is what
`flushPreroll` and `POST /api/ambient` send), and the route already carries
the ceiling, the busy refusal and `paste_safe`. The page buffers, POSTs, and
inserts the answer at the caret. **Nothing is sent.** That is the whole
difference from the composer's Enter, and it is the owner's word "add to chat
input".

It is DELIBERATELY the same route the desk agent's hotkey uses. A second
transcribe-a-clip path would be D-18 in its third shape, and this one already
refuses a busy recognizer (409) rather than returning two empty transcripts,
which is the measured failure two `Session`s on one model produce.

---

## 2. The rulings

**R1. The page arms the microphone by itself when it was left on.** Owner's
ruling, 2026-09-10, chosen over "remember the state, still need one tap". So
`stt_enabled` is a persisted daemon setting and the page acts on it at boot.
The cost is stated rather than buried: a reloaded tab on a phone opens its
microphone with no tap. What bounds it is that the browser still decides -- a
revoked permission, or a context that needs a gesture, is a refusal the page
must SAY rather than retry silently (AC-7b).

**R2. The state pill becomes a button, and that OVERTURNS a rule.**
`web/AGENTS.md` says of `#muteChip`, and by the same argument of the state
chip: *"It is a `<span>` and not a button on purpose -- the setting lives in
the panel, and a header control that silently muted the whole house would be a
consequence hiding in a status."* The owner has ruled the other way for the
STATE pill. The losing argument is kept because half of it survives: the pill
now controls the one thing it was already reporting, which is not a hidden
consequence, and `#muteChip` -- which reports something it would NOT control
-- stays a span.

**R3. The wave button is independent of the mic button.** Dictation works
with listening off, and with listening on it SUSPENDS the wake detector for
its duration. Two reasons, and the second is not optional: a wake phrase
landing inside a dictation would start a turn out of the words being dictated,
and the daemon would then have two claims on one recognizer, which returns
empty transcripts on both sides 9 times out of 9 (measured, `docs/ambient.md`).

**R4. Per-message replay asks the DAEMON to say it again.** The page keeps no
audio -- `playChunk` posts samples into a worklet and never retains them -- so
replay is re-synthesis. `POST /api/tts/say` is `tts_preview`'s shape with the
text supplied instead of a voice, and it changes no setting, exactly as the
preview does not.

**R5. Butler's replies only.** Owner's ruling. The owner's own messages carry
the copy button they already have.

---

## 3. What must not break

Read before touching anything:

- **`running` is ONE flag doing TWO jobs** and every site is listed in section
  5. Three of them (`app.js:2104`, `:2108`, `:2109`) already mean "this page
  wants a socket" and say so in their own comment; the rest mean "the
  microphone is capturing".
- **`goListen()` ends `setState(running ? WAKE : OFF)`** (`:1521`) and is
  reached from `ws.onclose`, from `ready`, from the watchdog and from
  `stopTurn`. On a socket-only page every one of those lands in OFF -- including
  the end of a successful typed turn.
- **`armWatchdog()` returns early when `!running`** (`:1485`), so a typed turn
  on a mic-off page gets NO anti-latch net, and its own message
  ("the microphone stays live") would be false.
- **`hello.caps` claims `{wake, tts}`** (`:2076`). `tts: true` is a lie
  without a playback context. Nothing reads it, which is exactly why it is
  worth keeping honest rather than worth ignoring.
- **`openPlayback()` is inside the microphone try-block** (`:2517`), and
  `playChunk` dereferences `playCtx` unguarded (`:1976`) -- safe today only
  because the two are created and nulled together.
- **An AudioContext created outside a user gesture starts SUSPENDED.**
  `playCue` already resumes (`:1775`) and so does the `speak_start` handler
  (`:2306`); `playChunk` does not.
- **`stop()` closes the socket** (`:2553`) and nulls `playCtx` (`:2554`) and
  clears `typedPending` (`:2568`), which would orphan an in-flight typed turn.
- **AC-105 reads `#stateDesc` + `#orbLabel` CONCATENATED** and requires the
  wake phrase to be named with the mic off (`probe_ux.py:373-376`). Any change
  to `micAction()`'s `!running` label moves that check.
- **Both probes hard-code the mic control as `#micBtn[data-mic]`** and must be
  updated in the SAME commit (AC-172). `probe_ux.py:467-478` and
  `probe_page.py:213-239` both click it to open the socket; after this that
  click stops being how the socket opens, and the probes must prove that.
- **No browser storage** (AC-8). Every new on/off state is a daemon setting or
  it does not persist at all.
- **Nothing in `web/` may name a setting's choices** (AC-41) -- but naming a
  setting KEY to write it is what the theme button already does
  (`app.js:2868`).

---

## 4. Acceptance criteria

**AC-175 The socket is the PAGE's, not the microphone's.** `connect()` runs at
boot. A page that never touches a microphone receives `ready`, restores the
conversation, renders the engine chips, holds `turn_mode` and `end_phrases`,
and clears `linkSkin`. FAILS BY: `probe_page.py` with no `--live` still
showing markup placeholders. Recoverable.

**AC-176 A typed turn works with the microphone off, end to end.** The
composer sends, the daemon answers, the reply renders, and it is spoken if
`speak` is on. FAILS BY: the "this page has not reached Butler" error.
Recoverable.

**AC-177 Playback does not depend on capture.** `openPlayback()` is outside
the `getUserMedia` path; a context that starts suspended is resumed on first
use, in `playChunk` as well as in `playCue` and `speak_start`. FAILS BY: a
reply that arrives silently with no error anywhere -- which is AC-7b and is
the worst failure in this spec, because nothing reports it.

**AC-178 `running` is split, and the watchdog follows the TURN.** `micLive`
means the microphone is capturing; `linkWanted` means this page wants a
socket. `armWatchdog` arms whenever a turn is in flight, whichever door it
came in by, and its message says what is actually true. FAILS BY: a typed
turn that hangs forever on a mic-off page with nothing saying so.

**AC-179 Listening is a setting and it survives a reload** (`stt_enabled`,
default true). The page arms itself at boot when it is on (R1). A browser
that refuses is reported in words and the control stays usable. FAILS BY: a
page that comes up armed when the owner left it off -- which is a microphone
opening itself against an explicit instruction, and is the one irreversible
failure here.

**AC-180 The state pill and the mic button are ONE control** (R2, D-18). Both
toggle listening, both carry the same accessible name for the same state, and
both reach one function. FAILS BY: two implementations of one act, which is
what this project has paid for three times.

**AC-181 A WAVE button dictates into the composer.** Press to record, press
again to transcribe and INSERT at the caret; it never sends. It works with
listening off, and suspends the wake detector while it records (R3). It POSTs
`/api/dictate`, and every refusal -- busy, too long, nothing said -- is shown
in the DAEMON's own words. FAILS BY: text that lands in a turn instead of in
the box, or a dictation that steals the recognizer from a live turn.

**AC-182 A SPEAKER button toggles `speak`.** In the composer beside the
microphone, writing the existing setting through `settings.set`, so every
device goes quiet at once (D-2, AC-8) and the header chip keeps saying so.
FAILS BY: a per-device mute, which is the four-disagreeing-copies defect.

**AC-183 Butler's replies carry play/stop.** One control per assistant
message; pressing it plays that text again through `POST /api/tts/say`,
pressing it again stops, and starting one stops any other. It changes NO
setting and works while `speak` is off -- that is the point of it. FAILS BY:
a control that plays over a live reply, or two playing at once.

**AC-184 The microphone button has ONE circle** (owner's item 2: "remove the
fixed white circle and leave only the dynamic circle that moves with audio").
`.micbtn`'s `border` is gone. An unarmed button still reads as a control,
because the ring draws nothing without audio. FAILS BY: a button that
disappears when listening is off. Recoverable.

---

## 5. Every `running` site, and which way it goes

Read off `web/app.js` on 2026-09-10.

| Line | Site | Becomes |
|---|---|---|
| 142 | declaration | two flags |
| 293 | `micAction()` | `micLive` |
| 514 | `paintAmbient()` | `micLive` |
| 1485 | `armWatchdog()` | neither -- a turn in flight |
| 1521 | `goListen()` | `micLive` |
| 1909 | `onWakeFire()` | `micLive` |
| 2104 | `ws.onclose` link skin | `linkWanted` |
| 2108 | `ws.onclose` reconnect | `linkWanted` |
| 2109 | reconnect callback | `linkWanted` |
| 2486 | `start()` guard | `micLive` |
| 2532 | `start()` sets | `micLive` |
| 2539 | `stop()` clears | `micLive` only -- `stop()` stops capturing and MUST NOT close the socket |

---

## 6. Build order

Each step ends at something observable and nothing user-visible ships before
the thing behind it works.

**Step 1 -- the daemon halves.** `stt_enabled` in the schema, and
`POST /api/tts/say`. Both ship alone and change nothing on the page.
Verified by pytest.

**Step 2 -- the decoupling** (AC-175, AC-176, AC-177, AC-178). `connect()` at
boot, `openPlayback()` out of the mic path, the `running` split, `stop()` no
longer closing the link, the lazy resume, the honest `caps`. No new markup.
Verified by `probe_page.py --typed` with NO `--live`, which is the whole
criterion in one run.

**Step 3 -- the controls** (AC-179, AC-180, AC-181, AC-182, AC-183, AC-184).
Markup, CSS and wiring. Verified in a real browser and by both probes.

**Step 4 -- the probes and DOX** (AC-172's standing rule).

---

## 6a. What the build found that this spec did not predict

**6a.1 A NULL CHECK IS NOT A GUARD ACROSS AN `await`, and this page had two
places that thought it was.** MEASURED in a real browser: pressing the header
pill calls `openMicGraph()`, and the `settings` broadcast that press causes
calls it again a moment later -- both before `micNode` is assigned. Two
AudioContexts were built, the module-level handle pointed at the second, and
the first call then did `new AudioWorkletNode(ctx2)` before ctx2's
`addModule` had resolved: *"the node name 'downsample-processor' is not
defined in AudioWorkletGlobalScope"*. The page said `standing by` with a red
error under it and no microphone behind either -- which is AC-7b arriving out
of the fix for AC-7b.
The graph is memoised on its PROMISE now, and `start()` carries a `starting`
flag cleared in a `finally` rather than at its three return points, which is
D-17 written down in this project's own catalogue and reintroduced anyway.
`buildMicGraph` also publishes into the module handles only at the END and
tears down its own stream and context on failure.

**6a.2 `--typed` implied `--live` in `probe_page.py`, so the first run that
"proved" AC-176 had a microphone.** The implication was written as a feature
("the socket does not open until the microphone does") and was the defect
stated as documentation. Caught by reading the probe's own argument parsing
after its output said `STANDING BY` on a page that should have been `off`.
The reversal is now the acceptance evidence: `--typed` alone is a whole turn
sent, answered and rendered on a page that has never touched a microphone.

**6a.3 AC-170 and AC-94 wanted opposite pages, and only the mic click was
keeping them apart.** "The empty state teaches the wake phrase" was read
BEFORE the click and "a restored conversation renders markdown" after it, so
"before the click" and "no conversation restored" were the same moment. With
the socket open at boot they are not, and on a daemon holding a conversation
the page is never empty at any instant a probe can look at. AC-170 SKIPS with
the reason there rather than failing or, worse, being quietly deleted.

**6a.4 What was measured, and on what.** A scratch daemon on a free port with
its own `BUTLER_STATE` and `hotkeys_enabled: false`, killed by the PID found
on its own port.
`stt_enabled: false` on disk gives `state: off`, the pill reading `Start
listening`, mic `border-top-width: 0px`; pressing the PILL arms the page and
writes `stt_enabled: true`; a reload then comes up `idle` / `standing by` with
no tap (AC-179, AC-180, AC-184). The speaker button wrote `speak: false` to
the daemon and flipped its own `aria-pressed` (AC-182). The wave button
recorded (`aria-pressed=true`, HUD `1 s`), and the second press put the
recognizer's text **in the box** with the page still `idle` and nothing sent
(AC-181). A reply's `.saybtn` played, reported `Stop reading this answer`, and
stopped on a second press (AC-183). Zero uncaught page errors in every run.
`probe_page.py` and `probe_page.py --typed` exit 0; `probe_ux.py` 32/32 exit 0
against a daemon holding no conversation and 57/57 against one that does.

---

## 7. What this spec could have wrong

**7.1 Auto-arm may be the wrong default on a phone.** R1 is the owner's
ruling and it is cheap to reverse -- one setting read -- but between shipping
it and reversing it, a tablet left on a shelf is listening because it was
reloaded. The mitigation is that `stt_enabled` is a DAEMON setting, so turning
it off anywhere turns it off everywhere.

**7.2 The wave button may fight the wake word in a way testing will not show.**
R3 suspends the detector while dictating, which is a page-side flag. What
neither probe can drive is a wake phrase spoken DURING a dictation on a real
microphone, and the failure mode -- two decodes on one recognizer -- is silent
and returns empty text on both sides. The 409 is the net, and it only fires if
the daemon is the one that notices.

**7.3 A composer with a dictate button may finish what 8.1 of
`docs/familiar-ui.md` started.** That spec warned a composer could make the
microphone feel optional; this one adds a way to talk to Butler WITHOUT
talking to Butler. If the wake phrase withers, the product changed and nobody
decided to change it.

**7.4 Replay re-synthesizes, so it is not what was heard.** A voice or a speed
changed since the reply was spoken gives a different rendering of the same
words. Cheaper than storing audio per message, and it is what "play this
again" means everywhere else, but it is not a recording.

**7.5 Nothing here is tested with a screen reader**, and this spec adds three
buttons to the busiest row on the page.

**7.6 `stop()` keeping the socket open changes what "stopped" means.** The
link readout said `stopped` because the page really had disconnected; now it
stays connected with the microphone off, and a reader of that word may
conclude Butler is unreachable when it is merely not listening.
