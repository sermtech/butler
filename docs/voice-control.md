# Control over Butler's voice, and a way to stop it

A SPEC, not the contract. Its criteria (AC-107..AC-112) move into `plan.md`
only as the owner accepts them.

**ALL SIX ARE BUILT, 2026-08-19** -- AC-107, AC-108 and AC-110 in `1bc9db8`,
AC-109, AC-111 and AC-112 in `848cec1` -- and the criteria are now in
`plan.md`. Section 3's rulings are the owner's and are what the build
followed. Sections 1 and 2 are the BEFORE picture and are kept in the tense
they were written in: the eleven-name `VOICES` tuple 2.1 measures is DELETED
and the list is the engine's (AC-108), and 2.4's "the page cannot stop
Butler" stopped being true with AC-111.

Written 2026-08-19, from the owner's own request: speed, other voices, a way
to turn speech off, and a stop control on Escape or a button. Every number
below was measured that day against the running daemon and the installed
`kokoro-js`, unless it says otherwise.

It **supersedes AC-96** in `docs/satellite-ux.md` ("stop, from the page"),
which was one line in a list. This is that criterion done properly, plus the
three the owner asked for beside it.

---

## 0. The lesson this spec is written under

AC-92 shipped and was reverted within the hour on the day this was written.
`docs/satellite-ux.md` section 11.1 has the whole account; the part that binds
this file is the rule that came out of it:

> A guard's value depends on which direction it fails. "30 s versus 300 s" is
> a real comparison that says nothing about which number is right, because a
> false abort costs one turn the owner can repeat and a missing abort costs
> every turn until the timer expires.

So section 7 below asks, for each criterion, **which way does it fail** -- and
where the answer is "unmeasured", the criterion says so rather than borrowing
confidence from the ones beside it. AC-92's own spec contained the sentence
"the mechanism was measured; the human was not" and it was promoted to Tier 1
anyway.

---

## 1. What already works, so nothing here rebuilds it

**Picking a voice already works.** `butlerd.LOCAL_TARGETS["tts_voices"]`
serves `hub.tts.info["voices"]`, `GET /api/models?target=tts_voices` answers
it, and the `voice` setting carries `choices_from="tts_voices"`, so the
settings surface renders a discovered picker with a live datalist. Measured
against the running daemon:

```
{"target": "tts_voices",
 "models": ["af_heart","af_bella","af_nicole","af_sarah","af_sky",
            "am_adam","am_michael","bf_emma","bf_isabella",
            "bm_george","bm_lewis"], "favourites": []}
```

That is the whole mechanism and it is sound. Two things about it are not.

**The favourites star is deliberately OFF for `voice`** (`spec.favourites`
false), and it must stay off. The star writes one shared `model_favourites`
list which `paintFavs` paints under every discovered control, so a starred
voice would appear as a clickable button under a MODEL picker and commit a
voice id as a model -- refused several layers from the click. That is a rule
`web/AGENTS.md` already records; this spec does not touch it.

---

## 2. The four gaps, with evidence

### 2.1 Seventeen voices the engine has are unreachable

MEASURED. `daemon/adapters/tts_kokoro.py` has:

```python
VOICES = ("af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
          "am_adam", "am_michael", "bf_emma", "bf_isabella",
          "bm_george", "bm_lewis")
```

Eleven names, hardcoded, and `info` returns `list(VOICES)`. Meanwhile the
sidecar's own boot line in `butler.log` reads:

```
[kokoro] [TTS] Pool ready: 28 voices, default=af_heart
```

and `node_modules/kokoro-js/voices/` holds **54** `.bin` packs -- `af_alloy`,
`af_aoede`, `af_jessica`, `af_kore`, `af_nova`, `af_river`, `am_echo`,
`am_eric`, `am_fenrir`, `am_liam`, `am_onyx`, `am_puck`, `bf_alice`,
`bf_lily`, `bm_daniel`, `bm_fable` and more, plus non-English packs.

So the picker is honest about what the ADAPTER declares and the adapter
under-declares by a factor of two and a half. This is the exact shape of
AC-42's complaint -- a list written into the daemon is wrong the day the
engine changes, and wrong invisibly.

The fix is not a longer tuple. It is asking the sidecar, which already knows.

### 2.2 There is no speed control at any layer

MEASURED, three layers deep, and every one of them drops it:

| layer | today | supports speed? |
|---|---|---|
| `ports.Tts.synthesize(text, voice, handle)` | no speed parameter | -- |
| sidecar `POST /tts` | body is `{text, voice, id}` | -- |
| `svc.synthesizeStream(text, voice ? {voice} : {})` | passes voice only | -- |
| `kokoro-js` | `stream(text, {voice, speed, split_pattern})` | **YES** |

`node_modules/kokoro-js/types/kokoro.d.ts` line 4: `@property {number}
[speed=1] The speaking speed`. So the engine has had it all along and three
layers of Butler throw it away.

**One non-obvious consequence, and it is the reason this is not a one-line
change.** `daemon/speech.py` caps synthesis segments in WORDS --
`FIRST_SEGMENT_MAX_WORDS = 8`, `LATER_SEGMENT_MAX_WORDS = 16` -- and those
caps exist to bound things measured in MILLISECONDS: time to first audio
(AC-4) and how long an interrupted segment keeps playing (AC-5). Words per
segment is a proxy for seconds per segment, and speed is exactly the constant
that breaks the proxy. At 0.7x an 8-word opener takes ~43% longer to speak, so
AC-5's recovery budget stretches without anything saying so.

### 2.3 There is no way to tell Butler not to speak

MEASURED: the `tts` group holds `voice`, `cues` and `cue_volume`. `cues` is
the earcons only -- the heard-you and done tones -- and turning it off leaves
the spoken reply untouched. Nothing anywhere silences the answer.

**This is the one gap with a real architectural question in it, and section 3
puts it to the owner rather than deciding.** Settings live on the daemon
precisely so two devices cannot diverge (D-2, AC-8) -- one laptop once held
four disagreeing copies of a voice setting. But "do not speak" is the most
plausibly per-device preference in the whole system: the desk should talk, the
phone on the bedside table should not.

### 2.4 The page cannot stop Butler

MEASURED. The page can send exactly six things: `hello`, `preroll`,
`wake_candidate`, `playback_done`, `pong`, `capture_end_hint`. None of them
stops anything. The only interruption is the wake word, handled locally in
`onWakeFire`, which drops playback and lets the daemon abandon the turn.

The pieces all exist and are not connected: `Tts.abort(handle)` reaches the
ENGINE (the kokoro sidecar has `POST /abort {id}`), `Brain.abort(turn_id)`
closes the in-flight completion socket, and barge-in already runs
`abort()` then `dispose()` in that order for the harness child. What is
missing is a way for the page to say so. That is review R8 (`speak_cancel`),
Tier 2 in `.references/README.md` since 2026-08-16.

**Escape is currently taken**: `web/app.js` binds it to closing the settings
surface, and that is its only binding.

---

## 3. The decisions, with options

**RULED by the owner, 2026-08-19: D1 = B, D2 = C, D3 = A, D4 = B.** All four
recommendations were taken. The options are kept whole rather than trimmed to
the winner, because each one records what was given up:

- **D1 = B.** Stop stops the speech AND the turn. The owner accepts that this
  is the only irreversible act on the page and that a mis-hit destroys a
  coding agent's work in progress. D2's scoping is what bounds it.
- **D2 = C.** Escape and the mic control, sharing ONE handler. Escape is
  scoped so it can only stop while Butler is actually thinking or speaking,
  which is what keeps D1's irreversibility from being one stray keystroke away
  at any moment.
- **D3 = A.** A daemon setting for every device. Per-device mute (C) is the
  acknowledged right end state and waits for a real annoyance rather than a
  prediction.
- **D4 = B.** The engine's full list plus audition. Non-English packs stay
  out: the recognizer is configured for English and a picker offering voices
  for a language Butler cannot hear is a control that half works.

The options below are the argument those rulings were made against.

### D1. What does "stop" stop?

| option | what happens | cost | fails how |
|---|---|---|---|
| **A. Speech only** | Playback flushes, `Tts.abort` reaches the sidecar. The brain or coding agent keeps working and finishes into the record. | small | You silence a wrong answer and the agent still spends the tokens. Nothing is lost. |
| **B. Speech and the turn** | As A, plus `Brain.abort(turn)` -- for harness mode that is `abort()` then `dispose()`, the same path barge-in already takes. | small-medium | A stop meant as "be quiet" also kills work you wanted. Unrecoverable: the agent's partial answer is gone. |
| **C. Two gestures** | One press stops speaking; a second press within a few seconds stops the turn. | medium | A second control to learn, and the window is a guess. |

**Recommended: B.** The owner's words were "stop agent", and in `harness_only`
mode -- which is what this daemon runs -- the thing worth stopping is usually
the agent, not the sentence. A stop that leaves a coding agent running in a
repository is the surprising one.

### D2. Where does the stop control live?

| option | cost | fails how |
|---|---|---|
| **A. Escape only** | trivial | Useless on a phone and a tablet, which are two of the three satellites. |
| **B. A button only** | small | Needs a pointer or a tab-stop; nothing for the hands-on-keyboard case. |
| **C. Escape AND a button** | small | Two paths to keep on one handler, which is the D-18 trap if they drift. |

**Recommended: C, with one handler.** Escape must be scoped: it closes the
settings surface today, so the rule is *if the surface is open, Escape closes
it; otherwise, if Butler is thinking or speaking, Escape stops.* Never both.

The button is the microphone control itself changing job -- it already does
this, and `micAction()` is the table that decides. Today it returns `act:
null` while thinking or speaking, which is precisely the state a stop belongs
in. No new control on the page.

### D3. What does "do not speak" mean?

| option | scope | cost | fails how |
|---|---|---|---|
| **A. A daemon setting** | every device at once | small | Muting the bedside phone mutes the desk. Consistent with D-2, wrong for a satellite fleet. |
| **B. Page-local, dies on reload** | this device, this page load | trivial | A phone that reloads comes back talking. No storage is allowed, so it cannot persist. |
| **C. A daemon setting, per device** | the device you set it on, remembered | medium | New per-device state in the daemon, which nothing else has. |
| **D. A setting, plus the text is still shown** | every device | small | Same as A, but the answer stays readable rather than lost. |

**Recommended: A now, C later if the phone actually annoys you.** A is one
schema line and honest. C is the right end state and it is the first
per-device state in this daemon, so it should be paid for by a real
annoyance rather than by a prediction. D is not an alternative -- the
transcript already shows the answer, so A gives you D for free.

### D4. How far does the voice picker go?

| option | cost | fails how |
|---|---|---|
| **A. Ask the sidecar for its list** | small | 11 becomes 28. You still cannot hear one without setting it and starting a turn. |
| **B. A, plus audition** | medium | A preview route the daemon must serve; see below. |

**Recommended: B, and the reason is that A alone makes the problem worse.**
Going from 11 unheard names to 28 unheard names is more choice and no more
information. `bm_fable` and `bm_daniel` are not distinguishable by reading
them.

Audition has one architectural constraint worth stating: **`daemon/AGENTS.md`
forbids speaking outside a turn** -- "Nothing is SPOKEN outside a turn, and
NOTHING is sent between turns either", because the page discards frames for a
turn it does not own (D-6). So a preview must NOT go through the socket. It is
a plain HTTP route returning WAV bytes that the page plays itself, entirely
outside the turn machinery. That keeps the rule intact instead of carving an
exception in it.

---

## 4. Acceptance criteria

- **AC-107 (speech speed is a setting and it reaches the ENGINE).** A
  `tts_speed` number setting, default 1.0, carried through `Tts.synthesize`,
  the sidecar's `/tts` body and `kokoro-js`'s `stream({voice, speed})`.
  Asserted on the WIRE -- the body the sidecar actually received -- and not
  merely that the value is stored, which is the AC-38 rule: "the cap is
  stored" and "the cap is on the wire" are different claims. A speed the
  engine cannot honour is refused loudly rather than silently ignored.
- **AC-108 (the voice list is the ENGINE's, not a tuple in the daemon).**
  `Tts.info["voices"]` comes from the sidecar, which already knows -- 11
  becomes what the pool reports. The hardcoded `VOICES` tuple is deleted, not
  extended (AC-42): a list written into the daemon is wrong the day the engine
  changes and wrong invisibly. A sidecar that cannot be asked is a LOUD
  failure and never an empty picker, the same rule
  `LocalListUnavailable` already holds.
- **AC-109 (a voice can be heard before it is chosen).** One line of speech in
  a named voice, on demand, from the settings surface. It is an HTTP route
  returning audio the page plays itself -- **never the socket**, because
  nothing is spoken outside a turn (`daemon/AGENTS.md`, D-6) -- and it changes
  no setting: auditioning a voice must not become choosing it.
- **AC-110 (Butler can be told not to speak, and the page says so).** A
  setting that suppresses the spoken reply. The reply still arrives as TEXT
  and still enters the transcript and the record: muting output is not
  dropping the answer. The page shows the state where it is read from across a
  room, because a silent Butler and a broken Butler must not look the same
  (AC-7b). Cues are already separate (`cues`) and stay separate.
- **AC-111 (stop, from the page, reaches the engine).** A page-initiated stop
  flushes local playback, tells the daemon, and lands on the same terminal
  path as every other ending, so the page re-arms exactly once (D-1, AC-2).
  It reaches the ENGINE -- `Tts.abort` to the sidecar, and per D1 the brain's
  abort as well -- rather than only stopping the daemon's own wait, which is
  the D-4 defect by another name. Protocol v1 gains the messages R8 named.
  The labelled `simulator` stub that says this cannot be done is deleted in
  the same commit that makes it possible.
- **AC-112 (Escape stops, and does not fight the settings surface).** With the
  settings surface open, Escape closes it, unchanged. With it shut and Butler
  thinking or speaking, Escape stops. In every other state Escape does
  nothing. The key and the button share ONE handler (D-18): a contract needs
  an assertion on the caller, and two paths to the same act is exactly how
  they drift.

---

## 5. Build order

Each phase is separately shippable and separately verifiable.

| phase | criteria | touches | why here |
|---|---|---|---|
| 1 | AC-108 | `tts_kokoro.py`, sidecar | Pure win, no protocol, no page change. The picker gets 28 voices the day it lands. |
| 2 | AC-110 | `settings.py`, `hub.py`, `web/` | One schema line and a branch. The cheapest thing the owner asked for. |
| 3 | AC-107 | `ports.py`, `tts_kokoro.py`, sidecar, `settings.py` | Four layers but each is one parameter. Section 6 covers the `speech.py` word-cap coupling. |
| 4 | AC-111, AC-112 | protocol v1, `hub.py`, `web/app.js` | The only protocol change here. Both criteria at once, because the key and the button must share a handler. |
| 5 | AC-109 | `butlerd.py` route, `web/settings.js` | Last because it is the only one that is comfort rather than capability. |

Phases 1 and 2 are an afternoon and need no protocol change at all.

---

## 6. The coupling phase 3 must not break

`speech.py`'s word caps bound milliseconds, and speed changes the conversion.
Three options, and this spec does not pick one because it has no measurement
to pick with:

1. **Scale the caps by speed** -- `FIRST_SEGMENT_MAX_WORDS / speed`. Keeps the
   time budget, changes where sentences are cut.
2. **Leave them and restate the budget** -- AC-4 and AC-5 become "at speed
   1.0", and the owner accepts that a slow voice has a slower barge-in.
3. **Bound the speed range** so the drift cannot matter -- e.g. 0.8 to 1.3,
   at which the worst case is 25%.

**Measure before choosing.** `test/bench_tts.mjs` already exists and
`docs/bench-tts.md` is its log; the number needed is seconds-per-segment at
each end of the range, which that bench can produce.

---

## 7. Which way each criterion fails

The AC-92 discipline, applied before anything is built rather than after.

| criterion | if it is wrong | recoverable? | evidence today |
|---|---|---|---|
| AC-107 speed | speech is too fast or slow | yes -- change the setting | engine support MEASURED; the `speech.py` coupling is REASONED and unmeasured |
| AC-108 voices | a name the pool does not have reaches the engine | yes -- refused at synthesis, loudly | the 11-versus-28 gap is MEASURED from the adapter and `butler.log` |
| AC-109 audition | a preview plays over a live turn | yes, and it is the risk to design against | not measured at all -- no route exists |
| AC-110 mute | Butler is silent when you wanted speech | yes -- and this is why the page MUST show it | the gap is MEASURED; whether per-device matters is UNMEASURED and is D3 |
| AC-111 stop | a stop kills work you wanted (D1 option B) | **NO** -- the agent's partial answer is gone | the missing capability is MEASURED; how often the owner would regret it is not |
| AC-112 Escape | Escape stops when you meant to close something | yes | the current binding is MEASURED (one, the settings surface) |

**AC-111 is the only irreversible one**, and that is the whole reason D1 is a
question rather than an assumption. Under D1 option B a mis-hit Escape
destroys a coding agent's work in progress. Two mitigations exist and neither
is free: require the stop to be deliberate (a button, not a key, or a key only
while speaking), or make it option A and accept that "stop" does not stop the
agent.

---

## 8. Verification

```
node --check web/app.js web/settings.js
LC_ALL=C grep -c '[^ -~]' web/app.js web/settings.js web/index.html
.venv\Scripts\python.exe -X utf8 -m pytest test\ -q
python -X utf8 test\manual\probe_ux.py https://127.0.0.1:<scratch>/
```

Per criterion, driven rather than read, against a SCRATCH daemon:

- **AC-107.** Set the speed, run a turn, and assert on the body the SIDECAR
  received -- a recording stand-in like `conftest.FakeOpenAI` is for the
  completion wire. Storing it proves nothing. Then listen to both ends of the
  range and confirm the words are still intelligible.
- **AC-108.** `GET /api/models?target=tts_voices` returns what the pool
  reports. Before: 11. Kill the sidecar and confirm the picker says so LOUDLY
  rather than rendering empty.
- **AC-109.** Audition a voice, then read `/api/settings` and confirm `voice`
  is unchanged. Audition one DURING a live reply and confirm the reply is not
  corrupted -- that is the failure this route can cause.
- **AC-110.** Mute, run a turn, and confirm three things at once: no audio,
  the answer still in the transcript, and the page saying it is muted.
- **AC-111.** Stop mid-reply and confirm the sidecar's `/abort` was hit (not
  just that the daemon stopped waiting), that the page re-armed EXACTLY once,
  and -- across four consecutive turns, never one -- that the wake word still
  works afterwards. One turn proves a terminal path works once; that is the
  oldest lesson in `test/AGENTS.md`.
- **AC-112.** With the surface open, Escape closes it and stops nothing. With
  it shut and Butler idle, Escape does nothing. With it shut and Butler
  speaking, Escape stops.

**We will know this is done when:** the voice picker offers what the engine
has, a voice can be heard before it is chosen, speech has a speed, Butler can
be told to stay quiet without going dark about it, and Escape or the mic
control stops a reply that is already running.

---

## 9. What this plan may already have wrong

- **The `speech.py` word-cap coupling is reasoned, not measured.** Section 2.2
  argues that speed breaks a words-to-milliseconds proxy. That is arithmetic,
  not an observation: nobody has measured seconds-per-segment at 0.7x on this
  machine. Section 6 says to measure before choosing, and this is the item
  most likely to make phase 3 bigger than its table says.
- **28 is the sidecar's number, not necessarily the picker's.** The pool
  reports 28 and the package ships 54 packs including Spanish, French, Hindi,
  Italian and Japanese. Whether a picker should offer voices whose language
  the recognizer is not configured for is a question this spec does not
  answer, and "ask the engine" answers it by accident rather than on purpose.
- **AC-111 under D1 option B is the only irreversible act on this page**, and
  the mitigation is not designed. "Escape stops the agent" one keystroke away
  from "Escape closes a dialog" is a real hazard on a keyboard.
- **Nobody has asked whether the owner wants speed per VOICE.** A rate that
  suits `bm_george` may not suit `af_heart`. This spec makes speed one global
  number because that is what was asked for, and if it turns out to be per
  voice the setting is the wrong shape.
- **D3 recommends A on a prediction.** The argument for deferring per-device
  mute is that no annoyance has been observed yet -- which is precisely the
  "reasoned, not executed" move that AC-92 punished. The difference, and it is
  why it is still the recommendation: A is one schema line and C is new
  per-device state in a daemon that has none, so the cost of being wrong about
  A is small and reversible.
