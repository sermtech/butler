# The satellite as a product

A SPEC, not the contract. Its criteria (AC-91..AC-106) are IN `plan.md` since
2026-08-19, each with its real status; on any point of conflict `plan.md`
wins.

**Ten of the sixteen are BUILT, 2026-08-19** -- phases 1, 2, 3 and 5 of
section 8: AC-91, AC-93, AC-94, AC-100, AC-101, AC-102, AC-103, AC-104,
AC-105, AC-106. AC-93 is PARTLY MET and `plan.md` records it that way:
`error` is reachable and drives the hero, `wake` is still not, and that half
is a design decision waiting on the owner rather than an oversight.
**AC-92 was built and REVERTED the same day** -- section 11.1
records why, and it is the most useful thing in this file. **AC-96 was
SUPERSEDED by AC-111 and AC-112** (`docs/voice-control.md`), which built the
stop it asked for; the four that were never started are the four that need a
protocol or daemon change, and they are named in section 8. Section 5 is kept
in the PAST
TENSE it was written in: every number there is the measurement that justified
the criterion, not a description of the page today. Section 9 carries the
after-numbers.

Written 2026-08-19, after driving the shipped page in a real Chrome against a
scratch daemon rather than reading it. Every number below was measured that
day unless it is marked otherwise, and section 11 lists what was NOT measured.

---

## 1. What this is

The page is beautiful and it is honest about the seams it knows about. What it
is not yet is a product someone lives with. This spec is the gap between
"a satellite that works" and "a voice assistant in 2026", found by using it.

It proposes nothing about the wave, the colour ramp, the settings layout or
the mic treatments. Those are settled (`docs/lattice-wave.md`,
`docs/settings-page.md`, and the owner's rulings recorded in `web/AGENTS.md`).
Everything here is about what the page SAYS, what it REMEMBERS, and who can
USE it.

## 2. What was actually driven, and how

A scratch daemon on `127.0.0.1:8199` with its own `BUTLER_STATE` (a copy of
the owner's), `BUTLER_CONFIG` pointed at a nonexistent path so every engine
took its fake, and `BUTLER_BRAIN=fake` for the turn runs. Chrome launched
through patchright with a fake microphone, and for the turn runs with
`--use-file-for-fake-audio-capture` pointed at `test/assets/wake_then_command.wav`.
The owner's daemon on 8140 was never written to and is still up.

Five drivers, all in the job's temp directory rather than in this repository:
a cold-page and accessibility pass, a control-inventory pass, a 400 ms state
sampler across several real turns, a daemon kill-and-restart observer, and a
responsive sweep over four viewports in both themes.

`test/manual/probe_page.py --live` was run first and passed everything it
asserts. **That is the point worth carrying**: the probe is a good instrument
for the contracts it was written against, and it reports a healthy page. None
of what follows is visible to it, because none of it is a contract yet.

---

## 3. What is there today

Honest inventory, so the gaps below are read against it.

**The hero.** Five session states painted as eight CSS rows, a live
oscilloscope trace of the real waveform over a full-page particle lattice, a
kicker, a headline, one sentence, the live transcript with committed and
tentative halves, the reply, a closing-warning bar, one microphone control in
five treatments with a label that changes per state, and a transient notice
line.

**Diagnostics**, behind a collapsed disclosure: the wake-score trace with its
threshold, an 18-field HUD, the conversation (8 turns), and two labelled stubs
that say on their face what they cannot do.

**Settings**, a full-screen surface over the live page: a signal-path rail
with live headline values, 39 rows across seven schema groups plus the
permission-mode and connections blocks, one daemon-written sentence per
section, search across everything, per-section advanced disclosures, and a
commit bar counting restart-flagged changes. It is the strongest part of the
page and this spec proposes almost nothing about it.

**What the page can SAY to the daemon**, in full: `hello`, `preroll`,
`wake_candidate`, `playback_done`, `pong`, `capture_end_hint`. Six messages,
none of which carries text.

---

## 4. What a 2026 voice interface has that this does not

The list, before the argument. Each row is measured or read off the code.

| the field has | Butler has |
|---|---|
| a degraded state that says the assistant cannot hear you | "Listening", unchanged, through a whole outage |
| an error state on the surface | eight styled states, two of which the page can never enter |
| the conversation as the main surface | eight turns in a collapsed diagnostics card |
| rendered assistant output | raw markdown asterisks in restored turns |
| timestamps on every turn | none, anywhere |
| history across sessions, searchable | three conversations on disk, one reachable, no search |
| typed input as a peer of speech | no text field and no protocol message for one |
| stop / interrupt from the UI | the wake word only; `speak_cancel` is unbuilt (R8) |
| retry, regenerate, edit-and-resend | none |
| per-turn latency and token cost | first-partial and finalize milliseconds only |
| named form controls | 42 of 61 settings controls have no accessible name |
| heading structure | zero `h2`/`h3` in a 39-setting surface |
| focus management on a modal | focus stays outside; one Tab lands behind the scrim |
| an install target on a tablet | no manifest, no service worker, no iOS meta |
| onboarding | the wake phrase appears only after you arm the mic |

---

## 5. The findings, with their evidence

### 5.1 The page says it is listening while the daemon is gone

**Measured.** The daemon was stopped for 20 s under a page that had already
armed. For the whole outage plus the reconnect storm -- 52 s of samples -- the
kicker read `standing by`, the headline read `Listening`, the sentence read
"Armed and quiet. Say the wake phrase to start a turn.", the microphone label
read `say "hey jarvis", or tap to stand down`, and `data-state` stayed `idle`
with its cold blue hue.

The only two things that changed were a `link` readout inside a diagnostics
drawer that is COLLAPSED by default, and a stack of red banners reading
`websocket error` -- five of them in fifteen seconds, one per reconnect
attempt, capped at six, with no cause and no guidance.

This is the page's most serious defect and it is exactly the class AC-7b
exists to prevent: a surface reporting a capability it does not have. The
owner walks past a page that says it is listening and speaks to a daemon that
is not there.

Recovery itself is correct: `ws.onclose` reconnected on its own and
`clearErrors()` on `ready` wiped the banners.

### 5.2 The page's own safety net is shorter than the daemon's turn

**Measured.** With `turn_mode = endword` -- the owner's setting -- the page
entered CAPTURE, rendered partials for 1.6 s, and then sat in "Go ahead" until
its own watchdog fired at exactly 30 s and abandoned the turn with a red
banner. `WATCHDOG_MS.capture = 30000` in `web/app.js`;
`EndWordPolicy.max_turn_ms = END_WORD_MAX_MS = 300_000` in
`daemon/endpointing.py`, whose own comment says the mode "exists precisely so
someone can stop, think, and resume".

The watchdog re-arms on every inbound message, so this is not "the daemon went
quiet for no reason" -- it is "the daemon sends nothing during silence, and
long-form silence is the point of the mode". A ten-times mismatch between a
policy's ceiling and the page's abandonment budget is the shape of a defect
whichever way it is resolved.

**Reasoned, not executed:** that a human pausing 30 s to think in long-form
mode would trip it. The mechanism was measured; the human was not.

### 5.3 Two of the eight designed states can never be reached

**Measured.** `document.documentElement.dataset.state` is assigned in exactly
one place, `paint()`, from `skin || view.attr`. `view.attr` ranges over
`off`, `idle`, `capturing`, `thinking`, `speaking`; `skin` is only ever `null`
or `"closing"`.

So `[data-state="wake"]` -- the bright cyan row -- and `[data-state="error"]`
-- the red one the temperature arc was re-tuned around -- are unreachable.
`visualiser.js` carries a whole error branch behind them (`meshErr`,
`nodeErr`, `errKick`, a distinct motion model), and nothing the real page does
can drive it.

That is either dead code to delete or a state to wire up. It cannot stay
both.

### 5.4 The conversation is a diagnostic

**Measured.** The transcript lives inside the collapsed diagnostics grid,
capped at `HISTORY_MAX = 8` turns, with no timestamps, no copy control, no
scroll-back and no search. `logReply` writes `meta` only when a `tier` string
arrives, so restored turns carry no meta at all -- the three turns restored
from the owner's own chat file rendered with an empty meta row.

Restored assistant text renders as `textContent`, correctly, and so a real
harness answer read on screen as:

```
I'm **pi** -- a minimal terminal coding agent harness built by Mario Zechner (badlogic). *
```

Asterisks and all. Harness mode's whole output is markdown, and the page
renders none of it.

One restored turn showed the question and an EMPTY answer with nothing saying
why. A turn that failed and a turn that was interrupted look identical to a
turn that has not answered yet.

`state/chats/` held three conversations. `GET /api/session/current` is the
only route to any of them, and there is no list endpoint, so two of the three
are unreachable from the page that wrote them.

### 5.5 Speech is the only way in

**Measured.** No text input exists anywhere on the page, and protocol v1 has
no message that carries typed text from a satellite. There is therefore no way
to correct a misrecognition, no way to ask a long or precise question without
dictating it, no way to use Butler in a room where you cannot speak, and no
way for anyone who does not speak to use it at all.

For a daemon whose main job is dispatching coding agents at real
repositories, "the instruction was misheard and there is no way to fix it
except say it again" is a daily cost. `docs/ambient.md` section 5 already
records a related live defect: the router computes a cleaned-up instruction on
every dispatch and throws it away.

### 5.6 The page cannot stop Butler talking

**Read.** The only interruption is the wake word, handled locally. R8
(`speak_cancel`) is in `.references/README.md` Tier 2, unbuilt, and the
diagnostics carry a labelled stub saying so. On a phone in a quiet room, or
when Butler has started reading out something long and wrong, there is no
button.

### 5.7 Accessibility

**Measured, in Chrome, without a screen reader.**

- 42 of 61 settings controls compute no accessible name. Only the range
  sliders carry one; every `<select>`, every number box and every text input
  is announced as an unnamed control. `settings.js` builds `.set-name` as a
  `<span>` and never associates it.
- Zero `h1`/`h2`/`h3` inside `#panel`. Nine section titles, all `<span>` or
  `<div>`. A screen reader user cannot navigate 39 settings by heading.
- Opening the settings surface leaves focus on `#settingsBtn`, outside the
  panel. One Tab lands on a control BEHIND the full-screen surface. `#panel`
  declares `aria-modal="false"`, so this is deliberate rather than an
  oversight -- but the four elements behind it are visually covered and
  pointer-blocked while staying in the tab order, which is the worst of both.
- `#reply` -- Butler's answer -- has no `aria-live`. `#errors` has no
  `role="alert"`; `#notice` has no `role="status"`. The transcript and the
  headline are live; the answer and every failure are not.
- The boolean setting is a `<label class="switch">` around a native 13 px
  checkbox. `index.html` styles `.switch[aria-checked="true"]`, and nothing
  ever sets that attribute: toggling one leaves border and background
  byte-identical (`oklch(0.89 0.012 265)` and `rgba(0,0,0,0)` both ways). The
  designed "on" appearance ships and never appears.

### 5.8 The light theme collapses three states into one

**Measured.** `[data-theme="light"]` groups `wake`, `capturing` and `closing`
into one rule, so all three compute `--sa: oklch(0.52 0.16 226)`. Walking all
eight states in both themes: the dark theme produces **eight distinct `--sa`
values** and the light theme **six**, because that trio is one.

The perceptual count is lower still, but that half is CITED rather than
measured here: `web/AGENTS.md` records light `thinking` and light `speaking`
as one state to the eye -- `(130,75,0)` against `(160,96,0)`, same hue one
lightness step apart -- and this pass did not re-measure rendered pixels. So
the honest statement is six distinct values, of which two are already known to
read as one, against the dark theme's eight.

`closing` is the one that matters: it is the "about to submit, keep talking"
warning, and in the light theme it is the same colour as ordinary capture. The
bar still animates, so the signal is not lost -- only the hue channel is.

### 5.9 Landscape phone puts the microphone below the fold

**Measured** at 844x390: `scrollHeight` 521 against an `innerHeight` of 390,
and the visible mic control's bottom edge past the viewport. The
`@media (max-height: 760px)` and `(max-height: 600px)` blocks exist precisely
to keep the mic reachable and do not go far enough at 390 px. Every other
tested viewport (390x844, 820x1180, 1440x900, both themes) had no vertical or
horizontal overflow.

### 5.10 A raw Python exception is user-facing copy

**Measured.** A connection that refuses puts the daemon's own error string in
the settings control:

```
could not read llama-swap: http://localhost:8077/v1/models could not be read:
ClientConnectorError(ConnectionKey(host='localhost', port=8077, is_ssl=False,
ssl=True, proxy=None, proxy_auth=None, proxy_headers_hash=None,
server_hostname=None), ConnectionRefusedError(22, 'The remote computer refused
the network connection', None, 1225, None))
```

The behaviour is right -- a loud refusal beats an empty list rendered as if
all were well -- and the message is not.

### 5.11 There is no first run and no install

**Measured.** No `manifest.json`, no service worker, no `apple-mobile-web-app-*`
meta. The satellites are a phone and a tablet on a tailnet, and neither can
install this as an app: added to a home screen it comes back as a browser tab
with browser chrome, and there is no offline shell for the moment the tailnet
is down.

The wake phrase is never shown until the microphone is armed -- the `off`
state says "Arm the microphone to start listening for the wake word" without
saying what the word is. In `endword` turn mode the page never names an end
phrase either, though four are configured, and the mic label says
"tap to submit" as if tapping were the only way.

---

## 6. Acceptance criteria

Numbered from AC-91, ranked by tier. A tier is impact on the owner's day; the
cost estimate is in section 8.

### Tier 1 -- the page currently misleads, or drops something needed daily

- **AC-91 (the link state reaches the hero).** When the socket is not open,
  the page says so where the state is read from across the room -- kicker,
  headline and sentence -- and not only in a collapsed drawer. It distinguishes
  "reconnecting" from "gone", it never claims to be listening while it is
  neither, and it returns to the true state by itself when `ready` arrives.
  The microphone and the wake detector STAY LIVE throughout: this is a
  statement about what is said, not a new latch (D-1).
- **AC-92 (the page's safety net respects the daemon's policy).** The page's
  abandonment budget for a state is never shorter than the longest the daemon
  may legitimately stay in it. Either the daemon tells the page the current
  policy's ceiling, or the page's budget is raised to the largest any mode
  allows. The watchdog stays -- it is the D-1 anti-latch net -- and its
  message names what actually happened.
- **AC-93 (every styled state is reachable, or is deleted).** No
  `[data-state]` row and no visualiser palette exists that the running page
  cannot enter. `wake` and `error` are either wired to real conditions or
  removed from `index.html`, `app.js` and `visualiser.js` in one commit
  (D-12). If `error` is wired, it is what AC-91 uses.
- **AC-94 (the conversation is a surface, not a diagnostic).** The transcript
  is reachable without opening diagnostics, holds more than eight turns,
  timestamps every turn, renders assistant markdown (at minimum emphasis,
  code spans, fenced blocks and lists) as marked-up text rather than as
  literal characters, offers copy per turn, and distinguishes a turn that
  failed from one that was interrupted from one still in flight. Every string
  in it still reaches the DOM as text, never as HTML: it comes from a
  microphone in a room and from a coding agent, and neither is trusted input.
- **AC-95 (typing is a peer of speaking).** A satellite can send a typed
  instruction, and the daemon treats it as a turn: same router, same brain,
  same record, same spoken answer unless muted. Protocol v1 gains one message.
  The page gains one field, reachable by keyboard without a pointer, and the
  microphone does not stop being live while it is focused.

### Tier 2 -- the field has these and the owner will reach for them

- **AC-96 (stop, from the page).** SUPERSEDED 2026-08-19 by AC-111 and AC-112
  (`docs/voice-control.md`), which built it as an inbound
  `{"type":"stop","turn":n}` rather than the outbound `speak_cancel` below.
  A control that stops Butler mid-reply,
  built on `speak_cancel` (review R8). It flushes the playback worklet, tells
  the daemon, and lands on the same terminal path as every other ending, so
  the page re-arms exactly once (D-1). The existing labelled stub is deleted
  in the same commit as the control that replaces it.
- **AC-97 (retry, and correct).** A turn can be re-asked, and a misheard turn
  can be corrected and re-sent as text without being dictated again. The
  corrected text is what enters the record and what the router sees; the
  original stays visible so the correction is not a rewrite of history.
- **AC-98 (past conversations are reachable).** The conversations already on
  disk are listable and readable from the page, newest first, with a way to
  find a turn by its words. Read-only is enough; nothing here resumes an old
  conversation, because whether the AGENT can resume one is `caps.native_resume`
  and is not the page's to guess (AC-35, AC-44).
- **AC-99 (a turn says what it cost).** Every turn carries end-to-end latency
  and, where the seam knows it, the tokens and the model that answered.
  Today's `first Nms / final Nms` is the recognizer only and is silent about
  the model, which is the slow half. Adopt the field's names (review R10):
  `end_of_utterance_delay`, `ttft`, `ttfb`, `e2e_latency`.

### Tier 3 -- reach and correctness, all cheap

- **AC-100 (every control has a name).** Every input, select and checkbox the
  settings surface renders is programmatically associated with its label. The
  page still names no setting: the association is built from the schema
  payload like everything else (AC-41).
- **AC-101 (the surface has structure, and takes focus).** Section titles are
  real headings. Opening the surface moves focus into it and closing returns
  focus to the control that opened it. Content behind the scrim is either
  out of the tab order while the surface is open, or the surface stops
  covering it.
- **AC-102 (a failure and an answer are both announced).** `#errors` is an
  alert region, `#notice` a status region, and Butler's answer is in a polite
  live region. A device whose speaker is muted still tells its user what
  happened.
- **AC-103 (the microphone is on screen at every size).** At 844x390 and any
  viewport at least as large, the microphone control is fully within the
  viewport without scrolling.
- **AC-104 (a switch shows that it is on).** The boolean control's on state is
  visible from its styling, not only from a three-letter word and a 13 px
  native box. Whatever carries it, the CSS and the markup agree -- a rule
  keyed on an attribute nothing sets is the same defect as a stale comment
  (D-12).
- **AC-105 (the page teaches itself).** Before the microphone is armed, the
  page names the wake phrase. In a turn mode that ends on a phrase, it names a
  phrase that will end the turn. Both come from settings the daemon already
  serves; neither is written down in `web/` (AC-41).
- **AC-106 (a satellite installs).** A web app manifest and the iOS meta tags,
  so the phone and the tablet run full-screen from a home-screen icon. An
  offline shell is explicitly OUT of scope: a satellite with no daemon has
  nothing to do, and a cached page that comes up saying "Listening" is AC-91
  from a new direction.

---

## 7. The daemon half

Four of the sixteen need the daemon, and they are named here so the web half
is never tempted to invent them (AC-41).

1. **AC-92** wants the current policy's ceiling on the wire, most naturally in
   `capture_start` beside the turn id. Alternative with no protocol change:
   the page reads the largest ceiling any mode allows and uses that.
2. **AC-95** wants one new inbound message carrying text, and a `Hub` path
   that starts a turn from it. Everything downstream is unchanged -- the same
   `_on_final` the three daemon-answered utterances already share.
3. **AC-96** wants `speak_cancel` outbound and a satellite-initiated stop
   inbound (review R8).
4. **AC-98** wants a list route over `state/chats/`, bounded like
   `MESSAGES_SERVED` already bounds the current one.
5. **AC-99** wants the turn's timings to include the model's, which
   `llm.py` and the harness adapters know and do not currently report.

**AC-105 needs nothing.** `wake_phrase` and `end_phrases` are already in the
schema the page renders.

---

## 8. Ranked build order

Each phase is separately verifiable and separately shippable. Ranked so the
owner can stop after any of them.

| phase | criteria | cost | status | why here |
|---|---|---|---|---|
| 1 | AC-91, AC-93 | small | **BUILT** | The page stops misleading. Both web-only. |
| 1b | AC-92 | small | **REVERTED** | The no-protocol option (raise the page's budget to the largest ceiling any mode allows) was built and backed out within the hour: it made the wake word deaf for five minutes. Only the daemon-tells-the-page option is left, and it is a protocol change. See section 11. |
| 2 | AC-100, AC-102, AC-104, AC-103, AC-105 | small | **BUILT** | Five cheap correctness fixes, all in `web/`, none touching a contract. |
| 3 | AC-94 | medium | **BUILT** | The conversation becomes usable. Markdown rendering was the only real work and it is the one place to be careful about untrusted text. |
| 4 | AC-95 | medium | not started | One protocol message, one field, one hub path. The largest change in daily use per line of code. |
| 5 | AC-101, AC-106 | small | **BUILT** | Reach: keyboard, screen reader, home screen. |
| 6 | AC-96, AC-97 | medium | **AC-96 SUPERSEDED**, AC-97 not started | Control over a turn in flight. AC-96 was rebuilt as AC-111 and AC-112 in `docs/voice-control.md` and SHIPPED 2026-08-19: `{"type":"stop","turn":n}` inbound, no `speak_cancel` outbound. AC-97 depends on AC-95. |
| 7 | AC-99, AC-98 | medium | not started | Instrumentation and memory. Both are additive and neither blocks anything. |

Phases 1, 2, 3 and 5 shipped together on 2026-08-19, in `web/` only: no
protocol message, no daemon change, no test weakened. The four that remain are
4, 6 and 7, and every one of them needs the daemon.

**AC-95 is not merely unbuilt, it is under-specified**, and section 11 says
which three questions it does not answer. It should be interviewed before it
is planned, not planned from this file.

---

## 9. Verification

```
node --check web/app.js
node --check web/settings.js
node --check web/visualiser.js
LC_ALL=C grep -c '[^ -~]' web/settings.js web/index.html      # both 0
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q -m "not slow"
python -X utf8 test\manual\probe_page.py https://127.0.0.1:<scratch>/ --live
```

Per criterion, driven rather than read, against a SCRATCH daemon:

- **AC-91, AC-93.** Stop the daemon under an armed page and read the kicker,
  the headline, the sentence and `data-state`. The current answer is
  `standing by / Listening / "Armed and quiet." / idle`; anything that still
  says that is unfixed. Restart it and confirm the page returns to the true
  state by itself.
- **AC-92.** Set `turn_mode = endword`, start a turn, stay silent past 30 s,
  and confirm the page has not abandoned it.
- **AC-94.** Restore a conversation whose assistant text contains `**bold**`
  and a fenced block, and read what is on screen. Confirm a timestamp on every
  turn and more than eight turns retained.
- **AC-95.** Type a question with the microphone armed, and confirm a spoken
  answer, a history entry, and a `chats.py` record indistinguishable from a
  spoken turn's.
- **AC-100.** Count controls whose computed accessible name is empty. The
  current answer is 42 of 61; the target is 0.
- **AC-101.** Open the surface and press Tab once. Focus must be inside
  `#panel`. Press Escape and confirm focus returns to `#settingsBtn`.
- **AC-103.** Set the viewport to 844x390 and confirm the mic control's bottom
  edge is inside the viewport.
- **AC-104.** Toggle a boolean and diff the computed border and background.
  The current answer is that they are identical.

**We will know this is done when:** a page whose daemon has gone away says so
in the headline, the conversation is readable and rendered, a question can be
typed, and no control in the settings surface is nameless. Three of those four
are now true; the typed question is phase 4.

### What the eleven measured after they were built [MEASURED 2026-08-19]

Driven in the same Chrome, against a scratch daemon holding a copy of the
owner's state, with `probe_page.py --live` run alongside as a regression check.

| criterion | before | after |
|---|---|---|
| AC-91 | `standing by` / `Listening` / "Armed and quiet." for the whole outage | `link lost` / `Reconnecting` / "Butler stopped answering. This microphone is still live..." within 16 s, returning to `standing by` / `Listening` by itself 2 s after the daemon is back |
| AC-91 | 6 identical `websocket error` banners per outage | 0 -- the first is still shown if the link was believed good; the retries are not |
| AC-93 | `data-state` stuck at `idle` | reaches `error`; the visualiser's red branch draws |
| AC-92 | capture watchdog 30 s against a 300 s policy | **still 30 s -- reverted.** At 300 s: 1 wake candidate in the daemon's log and the page still `capturing` at 55 s. At 30 s: re-armed at 35.9 s, woke again at 40.1 s, 3 candidates. |
| AC-94 | `I'm **pi** -- a minimal...` rendered literally | 0 rows containing raw `**`; 5 `strong`, 1 `em`, 1 `code`; **0 `a` or `img` elements ever** |
| AC-94 | 0 timestamps, 1 blank answer | 3 of 3 turns stamped, 0 blank answers |
| AC-100 | 42 of 61 controls with no accessible name | 0 of 61 |
| AC-101 | 0 headings in `#panel`; focus outside on open | 9 headings; focus on `#closeSettings`, one Tab reaches `#setSearch`, Escape returns to `#settingsBtn` |
| AC-102 | no role, no live region | `alert`, `status`, `polite` |
| AC-103 | mic bottom 409 px in a 390 px viewport | 368 px, no scroll |
| AC-104 | border and background identical off vs on | `oklch(0.89 0.012 265)` / transparent, versus `--sa` / `--sa-dim` |
| AC-106 | no manifest, no Apple meta | manifest and icon both served 200, 3 Apple tags |

Gate at the same commit: `475 passed` in the fast tier, `node --check` clean on
all three JS files, the ASCII gate 0 on every file it covers and unchanged at 5
on `app.js` (pre-existing shipped copy), and `probe_page.py --live` exit 0 with
no page errors.

**One check in this instrument was wrong before it was right, and it is worth
recording (D-13).** AC-104's first version read the switch's computed style
immediately after the change event and compared strings. That caught the
300 ms transition's START value re-serialised into its interpolation space --
`oklch(0.89 0.012 265)` came back as `oklab(0.89 -0.00104587 -0.0119543)`, the
same colour -- so it reported "they differ" and would have passed against a
page where nothing worked at all. It now waits out the transition, asserts
`matches(':has(input:checked)')` directly, and checks both properties.

---

## 10. What this retires or contradicts

Nothing in `plan.md` is retired by this spec. Four things it raised, and where
each one landed:

- **AC-7b was not met by the offline path, and AC-91 is what met it**
  (2026-08-19). The criterion is about a control that reports success and does
  nothing; a page that reports listening and hears nothing is the same failure
  in a different organ. Its scope did not have to be narrowed.
- **The labelled stubs are load-bearing and should stay until replaced.** The
  repeat-back and simulator stubs are the right pattern and each one is
  deleted only in the commit that ships its replacement. The simulator stub's
  stop half came true with AC-111 and its wording lagged a whole session
  behind, which is the rule arriving as a defect (`web/AGENTS.md`, D-12).
- **Three defects are now IN `plan.md` section 6** as D-30, D-31 and D-32
  (D-27..D-29 came from `docs/lattice-wave.md`):
  - **D-30.** A state row styled in CSS and implemented in the canvas that the
    page's own state machine cannot enter. Found for `wake` and `error`; the
    general rule is that the stylesheet and the state machine are one decision
    written twice and must be checked in one commit, exactly as the canvas and
    the stylesheet already are. **`error` is now reachable and `wake` is
    STILL NOT** -- `STATE_VIEW[WAKE].attr` is deliberately `"idle"`, and
    whether the armed-and-quiet state should have its own bright cyan is a
    design decision, not a defect fix. The row and its palette are live code
    with no caller until that is ruled on.
  - **D-31.** A CSS rule keyed on an attribute nothing sets. Found on
    `.switch[aria-checked="true"]`. The control shipped, the styling shipped,
    and no state was ever visible. **FIXED** -- keyed on
    `:has(input:checked)`, which follows the only thing that is true.
  - **D-32 is WITHDRAWN as stated.** It was written as "a client-side
    abandonment budget shorter than the server-side policy it is guarding",
    found at 30 s against 300 s. Raising the client budget to match made
    things strictly worse (section 11), so the finding was half of one. The
    half that survives is a rule, not a defect: **in a turn mode with no
    silence ending, the page's abandonment budget is the user's only way out
    of an utterance they walked away from, so it is not free to be as long as
    the policy's ceiling.** Whether that earns a D-number is the owner's
    call.
- **Review R8 is CLOSED and R10 is still open.** R8 was answered on
  2026-08-19 by AC-111, with an inbound `{"type":"stop"}` rather than the
  outbound `speak_cancel` it named; R10 is what AC-99 still waits on. Both
  have been Tier 2 in `.references/README.md` since 2026-08-16.

---

## 11. What this plan may already have wrong

Said plainly, because half of what a spec gets wrong is what it did not look
at. 11.1 is not a possibility -- it already happened.

### 11.1 AC-92 was wrong, and it took four hours to find out

It shipped at 14:00 and the owner reported "hey jarvis doesn't seem to wake it
up anymore" before 15:00. Reproduced on the fixture inside ten minutes,
reverted, and re-measured.

The criterion said: the page's abandonment budget must never be shorter than
the longest the daemon may legitimately stay in that state. That reads as
obviously true. It is not, and the reason is that **a page-side timeout in a
turn mode with no silence ending is not a hang-catcher -- it is the user's
only way out.**

`onWakeFire` drops every wake while `state === CAPTURE`. In `endword` mode the
daemon never ends a turn on silence; it waits for a spoken phrase. So an
utterance the owner walked away from -- said something, did not say "send it",
wandered off -- leaves the page in CAPTURE, and the ONLY thing that returns it
to wake-listening is this timer. Raised from 30 s to 300 s, "hey jarvis"
answered once and then did nothing for five minutes. Measured both ways on one
fixture: at 30 s the page re-armed at 35.9 s and woke again at 40.1 s, three
candidates in the daemon's log; at 300 s it was still `capturing` at 55 s,
one candidate.

Three things generalise, and they are worth more than the criterion was.

- **The evidence for it was one-sided and the spec said so.** Section 5.2's
  own words: "Reasoned, not executed: that a human pausing 30 s to think in
  long-form mode would trip it. The mechanism was measured; the human was
  not." A finding carrying that sentence should not have produced a Tier 1
  criterion. The measured half was a mechanism; the unmeasured half was the
  entire justification.
- **A guard's value depends on which direction it fails.** The comparison
  "30 s versus 300 s" is real and says nothing about which number is right,
  because the two failure modes are not symmetric: a false abort costs one
  turn the owner can repeat, and a missing abort costs every turn until the
  timer expires.
- **The cheap option and the durable option were not the same fix.** AC-92
  offered two, and taking the no-protocol one looked like the same outcome for
  less work. It was a different outcome: the daemon telling the page the LIVE
  ceiling tracks the mode, and a constant raised to the maximum any mode
  allows does not. When a criterion has a cheap variant, check whether it is
  the same criterion.

The rule that survives is in `web/AGENTS.md` beside the constant, and AC-92 is
back to unbuilt with only the protocol option left.

### 11.2 Everything else

- **No screen reader was run.** Every accessibility finding is a DOM and
  computed-style measurement in Chrome. "42 controls compute no accessible
  name" is a fact; "a screen reader user cannot use this" is an inference from
  it. NVDA or VoiceOver may recover some names by proximity heuristics, and
  would certainly find problems this pass did not.
- **No real microphone and no real voice.** Every turn was Chrome's fake
  device playing a fixture, and every reply came from the fake brain. What a
  real answer looks like on screen -- length, pacing, whether the reply line
  overflows -- is untested, and the one piece of real evidence here is three
  restored turns from the owner's own chat file.
- **No iPad and no phone.** Every viewport was an emulated size in desktop
  Chrome. The landscape-phone finding is real geometry, but touch, the iOS
  keyboard's effect on a `100dvh` layout, and Safari's handling of the
  full-page canvas are all unknown. AC-106 in particular is proposed without
  ever having tried to install anything.
- **AC-95 may be a bigger change than it looks.** "One message, one field" is
  true of the happy path. Whether a typed turn should suppress the spoken
  answer, whether it should arbitrate against a spoken one arriving at the
  same moment, and whether it should carry a turn id the page mints -- which
  D-6 forbids -- are three questions this spec does not answer.
- **AC-94's markdown rendering is where a security defect would enter.** Every
  string in the transcript comes from a microphone or from a coding agent, and
  the page was safe precisely because it used `textContent` everywhere. A
  renderer that produces elements from that text is the one change here that
  can be wrong in a way the owner cannot see. It was built as specified -- a
  small explicit tokenizer emitting `strong`/`em`/`code`/`pre>code`/`ul`/`ol`/
  `li` and nothing else, no library, no `innerHTML`, and deliberately no `a`
  and no `img`, so an agent that emits a URL cannot make something clickable on
  an always-on page. It is the one thing shipped this round that would still
  repay an adversarial read, and the closed element set is what to check first.
  Its own test pass caught a real bug: `some_snake_case` and
  `https://example.com/a_b_c` were being italicised with the underscores eaten.
- **The ranking may be wrong about AC-95.** It is placed in phase 4 on the
  argument that the offline lie is more urgent than a missing feature. An
  owner who is misheard three times a day would reasonably put it first, and
  nothing measured here settles that.
- **The light theme's collapsed states are recorded, not proposed.** The ramp
  is the owner's ruling and this spec does not reopen it. Section 5.8 exists
  so the count -- six distinct `--sa` values against the dark theme's eight --
  is written down. Its first draft said five, by folding in a perceptual
  collapse it had not measured; a verification pass on 2026-08-19 caught it
  and it is corrected rather than quietly dropped, because a count is the one
  kind of claim in this file that is trivially checkable.
