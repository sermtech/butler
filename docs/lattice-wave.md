# The lattice wave

A SPEC, not the contract. **BUILT 2026-08-19**, and its criteria
(AC-74..AC-82) are MET in `plan.md`; on any other point of conflict `plan.md`
wins. Section 9 is the part to read first: of the four things it warned it
might have wrong, ONE came true -- gold does not survive the light theme, and
the fix it names has NOT been taken because the ramp is the owner's ruling.

Written 2026-08-19, after the owner compared nine wave mocks in
`web/mocks/` and chose `g4-lattice`, with one change: the two speakers are to
be told apart by TEMPERATURE, and Butler's end of it is to be gold rather
than pink.

---

## 1. What this replaces, and why it is a replacement rather than an edit

`web/visualiser.js` came from a Claude Design comp and was kept nearly
verbatim so a re-import would stay diffable (`web/AGENTS.md`). That reason
expires here: nothing is being re-imported, four of its five render paths
are documented dead weight, and its analysis chain is wrong in ways the mocks
already fixed. The file is rewritten.

Three of its diagnostics report things that are not true. They are
diagnostics only -- the detector, the recognizer and the turn are unaffected
-- and they are numbered because a replacement that reproduced them would be
shipping the same lie in new code.

- **D-27. The wake score latches and can never fall.** `app.js` constructs
  with `externalScore: true`, so the only writer of `score` is `pushScore`,
  which is `score = Math.max(score, v)` (`visualiser.js:603`). The decay path
  at `:121` sits behind BOTH `if (!inAn)` and `if (!externalScore)`, so once a
  microphone is attached it cannot run. The readout and the trace become a
  running maximum for the life of the page: they climb to the loudest thing
  that ever happened and stay there.
- **D-28. The trace's threshold line is hardcoded to mid-height** (`:503`)
  while its label is generated from `WAKE_THRESHOLD` (`app.js:1471`). Change
  the constant and the text moves while the line does not.
- **D-29. The trace is pre-filled with 220 samples of `Math.random()`**
  (`:592`), so an instrument shows invented data for its first nine to eleven
  seconds. In a project whose rule is that an untagged number is a guess
  wearing a uniform, a drawn one is worse.

Two more faults are design rather than truth, and the mocks carry the fix:
the level chain smooths the rise and the fall by the same factor, so a
syllable arrives as slowly as it leaves; and every smoothing coefficient is
a fixed lerp factor, so the same code responds twice as fast on a 120 Hz
tablet as on a 60 Hz laptop.

---

## 2. What the lattice is

The full argument and a working implementation are in
`web/mocks/g4-lattice.html`. In one paragraph: a live oscilloscope trace of
the real waveform sits in the band, with a phosphor ring of its recent past
behind it; over the whole page, sparse particles drift and link to their near
neighbours with hairlines. LINK DISTANCE is the volume lever -- silence is
scattered dust, and a loud syllable snaps a constellation into being. Nodes
are near-white and carry the brightness; links carry the hue.

Two rules from the mocks are load-bearing and must survive.

- **A line may trace the waveform; a particle may not.** A path is rebuilt
  from scratch every frame. A particle reaches its target on a spring of tens
  of milliseconds, and a voice at 118 Hz has a pitch period of 8.5 ms, so a
  swarm asked to trace the raw waveform low-passes it into a smudge. The
  trace takes the waveform; the lattice takes the spectrum.
- **The particle field is TILTED and says so.** Density that follows the
  measured spectrum piles onto the left of the width, because speech puts its
  energy in the low bands. A gain rising with frequency fixes it,
  multiplicative so a silent high band stays silent. The tilted read is for
  display; the untilted one remains the measurement.

---

## 3. The colour ramp (the owner's ruling, 2026-08-19)

Hue carries session state, and because the state IS who is talking, hue also
carries who is talking. The ramp runs COLD while the room is heard and WARM
while Butler answers, and it passes through a dim warm pivot while he is
thinking, so a turn reads as one temperature arc rather than as eight
unrelated colours.

Pink and violet leave. Butler's end is gold.

Dark theme, `web/index.html`:

| state | `--sa` | `--g1` | `--g2` |
|---|---|---|---|
| off | `oklch(0.62 0.02 275)` | `oklch(0.66 0.02 275)` | `oklch(0.78 0.015 275)` |
| idle | `oklch(0.70 0.06 235)` | `oklch(0.74 0.05 232)` | `oklch(0.88 0.03 226)` |
| wake | `oklch(0.86 0.16 202)` | `oklch(0.92 0.14 196)` | `oklch(0.68 0.16 240)` |
| capturing | `oklch(0.80 0.15 208)` | `oklch(0.88 0.14 198)` | `oklch(0.66 0.16 246)` |
| closing | `oklch(0.76 0.13 220)` | `oklch(0.82 0.13 206)` | `oklch(0.63 0.15 250)` |
| thinking | `oklch(0.72 0.09 96)` | `oklch(0.78 0.10 100)` | `oklch(0.68 0.11 72)` |
| speaking | `oklch(0.87 0.17 94)` | `oklch(0.93 0.16 100)` | `oklch(0.79 0.18 62)` |
| error | `var(--warn)` | `oklch(0.68 0.19 22)` | `oklch(0.78 0.16 12)` |

`--sa-dim` keeps its existing alpha per row.

Error moves toward pure red (hue 12-24) and away from the orange it had
(hue 40), because gold now occupies hue 62-100 and the two must not be
confusable at a glance. Geometry backs it up: the lattice tears and goes
static in the error state, which no other state does.

Light theme overrides. **Yellow on a pale ground is the trap in this ramp**:
at the lightness that makes gold vibrant on black it is nearly invisible on
white, so the warm end becomes amber and ochre at roughly half lightness,
not a tint of the dark values.

| state | `--sa` | `--g1` | `--g2` |
|---|---|---|---|
| idle | `oklch(0.50 0.07 238)` | `oklch(0.46 0.07 236)` | `oklch(0.58 0.05 228)` |
| wake, capturing, closing | `oklch(0.52 0.16 226)` | `oklch(0.56 0.16 208)` | `oklch(0.42 0.18 252)` |
| thinking | `oklch(0.48 0.10 78)` | `oklch(0.50 0.11 84)` | `oklch(0.44 0.12 58)` |
| speaking | `oklch(0.52 0.15 72)` | `oklch(0.55 0.15 78)` | `oklch(0.45 0.16 50)` |

The canvas cannot read a custom property, so `app.js` keeps its own tables in
step (`MESH_DARK` / `MESH_LIGHT`, assigned into `viz.colors`). Those gain a
warm `meshOut`, a cooler `meshIn` with the violet end removed, an error
triple, and the node tints:

```
meshIn   dark   rgba(34,211,238)  rgba(56,148,246)  rgba(70,106,232)
meshOut  dark   rgba(255,214,92)  rgba(255,176,48)  rgba(255,236,168)
meshErr  dark   rgba(240,96,72)   rgba(226,62,58)   rgba(198,44,62)
nodeIn   dark   rgba(236,248,255)      nodeOut dark  rgba(255,248,226)
meshIn   light  rgba(14,120,170)  rgba(28,84,190)   rgba(44,62,180)
meshOut  light  rgba(176,116,8)   rgba(198,92,12)   rgba(150,96,20)
meshErr  light  rgba(170,40,30)   rgba(186,58,34)   rgba(150,32,44)
nodeIn   light  rgba(18,32,52)         nodeOut light rgba(52,38,10)
```

**Glow is budgeted, not sprinkled.** Vibrance comes from three cheap places:
additive blending on the field, one cached radial wash tinted per state, and
a pre-rendered node sprite scaled per particle. `shadowBlur` is allowed on at
most two strokes per frame, both on the wave trace. A per-particle shadow is
a blur pass per particle and is what drops a tablet to single-figure frame
rates.

---

## 4. The contract `app.js` already imposes

Read `web/app.js` before changing any of this; these are facts about the
caller, not preferences.

- A CLASSIC script (`index.html:809`, loaded before the `app.js` module) that
  publishes `window.ButlerVisualiser.create(mainCanvas, traceCanvas, opts)`
  synchronously. No `import`, no `export`, no bundler, no CDN.
- `create` is called at module evaluation, BEFORE any microphone exists, and
  is wrapped in `try/catch` (`app.js:1459`). It must not need audio to start,
  and `app.js` supports `viz` being null.
- `attachInput(node)` takes a `MediaStreamAudioSourceNode` in the mic context;
  `attachOutput(node)` takes the TTS `AudioWorkletNode` in a 24 kHz context.
  Both arrive once, inside `start()` (`app.js:1274`). **The output tap is live
  for the whole session** -- the worklet emits silence between replies -- so
  "speaking" must never be inferred from the presence of a signal.
- `setMode("idle" | "input" | "output")` on every paint (`app.js:280`).
- `pushScore(v)`, `v` in 0..1, about every 80 ms (`app.js:772`).
- `colors` is a mutable object that `app.js` `Object.assign`s into on every
  theme change (`app.js:1214`). Cache keys must be the colour STRING, so a
  new palette naturally produces new cache entries.
- Analyser taps only. Connect nothing to any destination, open no
  `AudioContext`, close none, produce no sound. A visualiser that makes its
  own noise on a device whose microphone is live is not a visualiser.

Four extensions, each small and each closing a defect or a gap:

- `opts.field` -- the full-page canvas the lattice lives on. New element.
- `opts.threshold` -- the wake threshold the trace draws its line at. Closes
  D-28.
- `setState(s)` -- one of `off, idle, wake, capturing, closing, thinking,
  speaking, error`. The canvas has three palettes today and the stylesheet has
  eight rows, which is why `error` currently draws a blue wave inside a red
  page. `app.js` already computes this string in `paint()`.
- `detach()` -- called from `app.js`'s `stop()`, which today closes both
  AudioContexts without telling the visualiser and never calls `destroy()`.

---

## 5. Files that change

- `web/visualiser.js` -- rewritten.
- `web/index.html` -- the eight state rows and the four light overrides; one
  new `<canvas id="field" aria-hidden="true">` fixed behind the content, with
  `pointer-events: none`, and the stacking context that puts `header` and
  `main` above it.
- `web/app.js` -- `MESH_DARK`/`MESH_LIGHT` gain the new keys; `create` is
  passed `field` and `threshold`; `paint()` calls `setState`; `stop()` calls
  `detach()`.
- `test/test_visualiser_contract.py` -- new grep gate, in the shape of the
  existing `localStorage` one.

---

## 6. Acceptance criteria

- **AC-74 (the wave is the room, and the lattice is its spectrum).** The band
  shows a triggered trace of the real time-domain waveform with a phosphor
  history behind it (THE BAND CLAUSE IS SUPERSEDED, 2026-10-27,
  `docs/lantern-ui.md` CU-3: the band retired; the lattice draws on the mic
  ring and the full-page field); the field shows particles whose density follows the
  tilted spectrum and which link to near neighbours. No shape on screen is
  generated by a timer alone while audio is present.
- **AC-75 (link distance is the volume lever).** Link distance rises with
  level. At silence the field draws an order of magnitude fewer links than at
  a loud syllable, measured in a real browser, not asserted.
- **AC-76 (the ramp is a temperature arc).** The eight states run cold
  through the room's half of the turn, a dim warm pivot at `thinking`, and
  vibrant gold at `speaking`. No pink or violet remains in the wave path.
  Error is unmistakable against gold in both themes.
- **AC-77 (the canvas and the stylesheet agree).** Every state that has a
  stylesheet row has a canvas palette. `error` never draws a cool wave.
- **AC-78 (the score is what the detector said).** `pushScore` sets the
  current value and it decays; the trace plots that, against a threshold line
  taken from the caller. The trace starts EMPTY and shows only samples that
  arrived. Closes D-27, D-28, D-29.
- **AC-79 (the level chain has the shape a voice needs).** Asymmetric
  follower, the room's own floor tracked and subtracted, a slow loud
  reference frozen while nothing is said, and height as amplitude under a
  power curve. Every smoothing coefficient is `1 - exp(-dt / tau)`.
- **AC-80 (it costs what a tablet can pay).** Particle count scales by the
  square root of viewport area; the pair loop is a uniform grid, not
  all-pairs; no per-particle or per-link `shadowBlur`; device pixel ratio
  capped at 2; no work while the document is hidden or the canvas is
  off-screen.
- **AC-81 (the three media queries are honoured on the canvas, not just in
  CSS).** Reduced motion: fewer particles, gentler drift, never a still
  image. Reduced transparency: no additive blending and no wash. Increased
  contrast: higher minimum alphas and thicker links. `prefers-contrast` and
  `prefers-reduced-transparency` reach the canvas for the first time; the
  media query must be re-read on change rather than captured once at
  construction.
- **AC-82 (the page survives the visualiser).** A throw inside `create` still
  leaves a usable page; `detach` is safe to call twice; a null or zero-sized
  trace canvas is not an error; the field canvas is optional.

---

## 7. Build order

Each step is separately verifiable, and the order is chosen so a failure is
attributable.

1. **The ramp alone.** `index.html` state rows plus the `app.js` colour
   tables, with the OLD visualiser still in place. The page should read as a
   temperature arc across a scripted turn. This de-risks the palette
   independently of any renderer work, and it is the step most likely to need
   the owner's eye.
2. **The visualiser, contract-first.** New `web/visualiser.js` with the
   analysis chain, the trace and the wave band. Wire `opts.field`,
   `opts.threshold`, `setState` and `detach` in `app.js` and add the field
   canvas to `index.html`. At the end of this step the page runs with the new
   wave and no lattice.
3. **The lattice.** Particles, the grid pair loop, the link rendering, the
   glow budget, the text falloff.
4. **The three media queries and the grep gate.**

---

## 8. Verification

```
node --check web/visualiser.js
node --check web/app.js
.venv\Scripts\python.exe -X utf8 -m pytest test\test_http_surface.py test\test_visualiser_contract.py -q
```

Plus a real browser, against a SCRATCH daemon and never the owner's on port
8140: give it its own `BUTLER_STATE` and a free port, then
`python -X utf8 test\manual\probe_page.py https://127.0.0.1:<port>/ --live`.
Reading the markup is not evidence -- a classic script that throws leaves the
page looking like it was never built.

---

## 9. What this plan may already have wrong

Recorded now so the next reader can check it rather than rediscover it.

- **Gold may not survive the light theme.** The ramp assumes amber at half
  lightness reads as the same idea as gold at full lightness. If it reads as
  brown, the honest fix is to let the light theme carry the temperature in
  chroma and saturation instead, not to force the hue.
- **The full-page canvas is a new stacking context on a page that had none.**
  The settings drawer, the scrim and the mock-free real page have their own
  z-index expectations, and a fixed canvas behind everything is exactly the
  kind of change that reveals one of them.
- **`setState` may be the wrong seam.** It hands the canvas the page's state
  vocabulary, which is a coupling the old three-mode interface avoided. The
  alternative -- more palettes keyed off `setMode` alone -- cannot express
  `error` or `closing`, so this is a trade rather than a free win.
- **The particle count is a guess until it is measured on the iPad.** Every
  cost figure behind AC-80 comes from a desktop with a discrete GPU. The
  tablet is fill-rate limited in a way that machine is not, and the honest
  test is the device.
