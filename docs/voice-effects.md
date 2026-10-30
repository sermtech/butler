# Voice effects: what the message box does while Butler hears and speaks

**SUPERSEDED IN PART, 2026-10-27 (docs/lantern-ui.md CU-3, AC-690):**
`voice_effect` gained `lantern` -- the composer's own border lit from
`--fx-level`, CSS only -- and it is now the DEFAULT; the wave band and
`viz_placement: band` retired. Where this file says `lattice` is the default
or names the band, CU-3 wins; the rest stands.

**Status: PROPOSAL, updated 2026-09-16 against HEAD. Steps 1, 2, 3, 4, 5 and 6
of section 5 are BUILT and step 6 is now AUDITED** (`906d31a`, `7e7d479`, and
a same-day fix pass); step 7 is documentation-only and closed; step 8 is
still partly open. `voice_effect` defaults to `lattice`, so the page draws
exactly what it drew before this work until the owner changes the setting.
`aurora` was CUT the same day -- the catalogue in section 2 is now EIGHT
treatments, and AC-261's per-treatment list (the catalogue plus the lattice,
since step 6 now covers its own frame cost too) is nine rather than ten. See
section 2 and section 7. Criteria are numbered AC-252..AC-262 and they stay
in this file until the owner accepts them; `plan.md` is still the contract.

The owner's ask, in their words across two days:

> "my current ui has some dynamic elements that react to microphone ... i'd
> like to change it to a more modern option (still user toggleable) maybe like
> energy / mist coming out of the input area of chat could be a subtle, but
> still dynamic reaction to both voice input and voice output (different
> styles or colors for each?)"

> "the effects could come FROM the input box, as if steaming from it to the
> outside, while leaving the inside clean for input."

> "add all of these effects. and add other more creative ones as well."

The shapes are mocked in `web/mocks/r-plume.html`, which the owner approved on
sight ("the effects are what i had in mind"). This file is how they get built.

---

## 0. What already exists, measured before anything was designed

Read off HEAD on 2026-09-16 with `zg` and `rg`. **This section is the reason
the plan is small**, and getting it wrong would have doubled the work.

**THE LEVEL PIPELINE IS ALREADY BUILT AND IT IS GOOD.** `web/visualiser.js`
holds `makeChain` (line 78) and `makeTap` (line 116): an asymmetric envelope
follower, the room's own noise floor tracked and SUBTRACTED, a slow loud
reference that is FROZEN during silence so the wave does not pump between
words, amplitude under a power curve rather than a dB axis, a separate
`transient()` for onsets, and `NB` per-band envelopes with a measured 2.6
spectral tilt. There is one tap per graph with SEPARATE auto-ranging state,
because "a far-field microphone and a synthesised reply do not live at the
same amplitude".

The mock's `Math.sqrt(rms) * 7` is a toy beside this. **No new level code is
written. Every treatment consumes the existing chain.**

**BOTH AUDIO GRAPHS ARE ALREADY HANDED TO IT.** `web/app.js:3705`:

```js
if (viz) { viz.attachInput(micSource); viz.attachOutput(ttsNode); }
```

and `viz.detach()` at `:3721`. So "react to voice input AND voice output" needs
no new plumbing at all -- the two sources the owner asked for are already
flowing into the file that would draw the effect.

**THE STATE IS ALREADY ROUTED.** `app.js:473-477` maps each turn state to a
wave mode, and `paint()` pushes it at `:530` and `:536`:

```
[OFF]      attr: "off"        wave: "idle"
[WAKE]     attr: "idle"       wave: "idle"
[CAPTURE]  attr: "capturing"  wave: "input"
[THINKING] attr: "thinking"   wave: "idle"
[SPEAK]    attr: "speaking"   wave: "output"
```

`input` and `output` are exactly the two directions the mock distinguishes.
Nothing new decides which one is live.

**THERE IS ONE CANVAS AND IT MOVES.** `web/index.html:804` states the rule:
"ONE canvas, MOVED between two slots rather than duplicated", because
`visualiser.js` asks the canvas for its rectangle to place the lattice's text
falloff and a second canvas would be a second everything. The slots today are
`#waveTop`, `#waveMic` and the full-viewport `#field` (`index.html:1993`).

**THE PUBLIC SURFACE IS SEVEN NAMES**, pinned by a test that reads them off the
CALLER. `visualiser.js:1389-1420` exports `attachInput`, `attachOutput`,
`setMode`, `setState`, `pushScore`, `detach`, `colors`.

**THE SETTING IT SITS BESIDE.** `daemon/settings.py:319-328` declares
`viz_placement` in the `look` group with choices `mic`, `band`, `ambient`,
`off`, and `test_settings_api.py:370` pins that there are exactly four and one
is `off`.

---

## 1. THE ONE DECISION THE OWNER MUST MAKE FIRST

The owner has already ruled that **all the effects are offered and the
placement is not**. That collides with `viz_placement`, which is a placement
control, and the collision has to be settled before any code is written
because it decides whether one setting or two reach the page.

There is ONE canvas. Two settings that both want to draw into it is a state
the page can reach and no test would catch.

**Option A -- ONE setting replaces both (recommended).** `voice_effect` in the
`look` group, values `off`, `lattice`, and one per treatment. `viz_placement`
survives ONLY as "where the lattice goes" and is consulted only when
`voice_effect` is `lattice`. One control answers "what do I see", the old one
degrades to a sub-question of a single value.
Cost, stated: a settings page that shows a control which is inert for eight of
ten values of another control. That is the thing AC-7b exists to prevent, so
the row has to hide or say so.

**Option B -- `voice_effect` wins whenever it is not `off`.** Two independent
settings and a documented precedence. Cheapest to build and the worst to live
with: two controls, one outcome, and the loser is silently ignored.

**Option C -- the treatments become values of `viz_placement`.** Rejected here
rather than offered: it would put "where" and "what" in one list, and the
owner's ruling is that "where" is not a question the user is asked.

**Both rulings are made. Option A shipped, and the lattice survives.**
Owner's ruling, 2026-09-16: **the lattice SURVIVES, as a value of
`voice_effect`.** One control answers "what is drawn"; `lattice` is one of
`voice_effect`'s choices and stayed the DEFAULT (SUPERSEDED 2026-10-27 by
`docs/lantern-ui.md` CU-3: `lantern` is the default); `viz_placement`
survives only as "where the lattice goes" and does nothing for any other
treatment. AC-74..AC-82 stay MET -- nothing accepted is deleted. Declined:
retiring the lattice entirely (it deletes a month-old accepted feature and is
not recoverable by a later fix) and keeping both settings independent (two
controls could then want the one canvas at `index.html:804`, which needs an
arbiter nobody has designed).
This is ALREADY MET at HEAD, not merely decided: `daemon/settings.py:347`
declares `voice_effect` with eleven choices and default `lattice`;
`daemon/settings.py:319-328` (`viz_placement`)'s help text reads "This only
applies when voice_effect below is set to lattice"; and
`test/test_settings_api.py:416`
(`test_the_viz_placement_help_says_it_only_applies_to_lattice`) enforces that
the help text says so. Nothing further is needed in the schema for this
ruling; what remains is step 7 below.

---

## 2. The catalogue

Four are mocked and approved. Five were built as the "more creative" half of
the ask; `aurora` was CUT 2026-09-16 (section 7), leaving four. Every one is
the SAME contract: born at the field's perimeter, drawn outward, the inside
untouched.

| # | Name | What it is | Data it uses | Cost |
|---|------|------------|--------------|------|
| 1 | `mist` | A soft cloud gathered around the field, breathing | level | CSS, 3 blurred layers |
| 2 | `ember` | Particles off the edge, out when speaking, in when listening | level | canvas, grows with level |
| 3 | `bloom` | One blurred shape the size of the field, swelling past its edge | level | CSS, one gradient |
| 4 | `ribbon` | A contour traced round the field, displaced by the level | level | canvas, one path |
| 5 | `sonar` | A ring leaves the edge on each ONSET and expands away | **transient()** | canvas, few paths |
| 6 | `filaments` | Tendrils growing outward, one per frequency band | **per-band envelopes** | canvas, NB paths |
| 7 | `comet` | A bright point orbiting the perimeter, tail length from level | level | canvas, cheapest moving |
| 8 | `dust` | Fine motes hovering and pushed outward, subtler ember | level | canvas, fixed count |

**`aurora` (vertical curtains of light drifting along the edge, level +
transient) is CUT, not in this table.** It rendered as faint triangular
smudges at every level 0.17-0.99 across two tuning passes -- a shape defect,
not a brightness one. See section 7.

**5 and 6 are the interesting additions and they are why the existing level
chain matters.** `sonar` fires on onsets, so a ring per syllable rather than a
wobble per volume -- it draws the STRUCTURE of speech, which no amplitude-only
effect can. `filaments` reads the `NB` per-band envelopes that already exist
with their measured 2.6 tilt, so it is a real spectrum wrapped around the box
rather than a decoration. Neither needs new DSP, but `sonar` does need an EDGE
derived from `transient()`, which is an envelope rather than an event -- see
section 7.

`comet` and `dust` are cheap and are there because the owner asked for range.

---

## 3. Where the code goes, and why not elsewhere

**The treatments live INSIDE `visualiser.js`, as a table of draw functions
keyed by mode.** Three reasons, in order of force:

1. The level chain, both taps, and the `input`/`output`/`idle` mode are all
   already there. A new module would have to be handed the same nodes, and
   `test_visualiser_contract.py` forbids it opening an `AudioContext` of its
   own -- so it would need a second tap on nodes it does not own.
2. There is one canvas by rule. A second drawing surface is a second
   everything, and `index.html:804` already says so.
3. `app.js` names no setting (AC-41). It passes the daemon's value to
   `setMode` and holds no list. That stays true only if the value is a MODE.

**The composer gets a fourth SLOT, and the existing canvas moves into it.**
A `.fx` wrapper around the composer, inset negatively on all four sides,
`z-index` below the composer, `pointer-events: none`. The mock proves the
mechanism: the composer's own opaque background occludes the middle, so the
inside stays clean by construction rather than by care (`r-plume.html`,
verified `fx z:0 composer z:1`).

**`mist` and `bloom` are CSS and do not use the canvas at all.** They are two
elements in the same wrapper whose opacity and transform are written from the
same level. That is a genuine split in the design and it must be stated: the
slot holds a canvas AND two CSS layers, and the mode decides which is live.

**Growth is the real cost.** `visualiser.js` is 1,426 lines before this and
nine treatments will not fit in it politely. The plan splits the FILE only if
step 3 measures it as unreadable, and never splits the CONTRACT: one script,
one canvas, one publish, because the grep gate reads one file as raw bytes.

---

## 4. Contracts this must not break

Each names the test that catches it, because several of these are invisible to
everything else in the suite.

- **`test_visualiser_contract.py`** -- no `AudioContext` constructed, no
  browser storage, ASCII only, publishes synchronously, and **every method
  `app.js` calls exists**, read off `app.js` rather than written twice. Adding
  a `viz.setEffect()` call site with no method behind it reddens this without
  anyone remembering. If the seven-name surface grows, `REQUIRED` grows with it.
- **AC-41** -- `web/` may name no setting value. The treatment names are the
  DAEMON's, arriving through `setMode`. `test_seams.py`'s grep gate is the
  enforcement.
- **AC-174** -- a colour scheme repaints surfaces and never the state accent.
  `--in` and `--out` are declared once, outside every palette. Verified in the
  mock across all five schemes; `test_seams.py` reads the shipped stylesheet
  for this rule already.
- **AC-8** -- no browser storage. The chosen effect is a daemon setting.
- **`test_settings_api.py:370`** pins `viz_placement` at four choices, one of
  them `off`. Option A in section 1 changes that test.
- **AC-7b** -- no control that lies. If `viz_placement` becomes inert for most
  values of `voice_effect`, the settings page must say so or hide it.
- **The seven `viz.*` call sites** in `app.js` are asserted. `off` must stop
  the DRAWING and never the calls (`test_settings_api.py:375`).

**Two rules the mock established and the build inherits:**

- **No meaning by colour alone.** `docs/lattice-wave.md` 9 records `thinking`
  and `speaking` sitting 1.18:1 apart in the LIGHT theme in all five schemes,
  so hue cannot be the only carrier. Listening draws energy INWARD, speaking
  pushes it OUTWARD, in every treatment. That survives greyscale.
- **Nothing moves while you read.** Every treatment paints in an absolutely
  positioned layer and changes only transform, opacity and canvas pixels.
  Measured in the mock: composer height 56px at silence and 56px at full level.

---

## 5. Build order

Each step ends with something the owner can look at, and each says what proves
it. **Nothing starts until section 1 is ruled.**

**Step 1 -- the slot and one treatment, end to end. BUILT, `906d31a`.**
The step named only `bloom` as the cheapest thing that could be wrong, but
what shipped is the whole `voice_effect` schema setting (all eleven choices --
`off`, `lattice`, and the nine treatments), `#composerFx` inset negatively on
all four sides so the composer occludes it by construction, and the settings
round trip confirmed in `test_settings_api.py`.
*Verified:* `test_the_voice_effect_offers_eleven_and_the_default_is_the_shipped_lattice`
and `test_the_voice_effect_setting_round_trips_through_the_api`. No browser
screenshot at two levels was found in this pass.

**Step 2 -- the level seam. BUILT, `906d31a`.**
`setEffect()` and `frame()` write `--fx-level` and `--fx-dir` onto `opts.fx`
from the existing chain -- no new `AudioContext`, no second tap. `setEffect`
is in `test_visualiser_contract.py`'s `REQUIRED` caller-contract list.

**Step 3 -- the two approved canvas treatments. BUILT, `906d31a`.**
`ember` and `ribbon` draw into `mainCanvas` via a shared perimeter walker.
*Verify still open:* no iPad frame-cost measurement was found; section 7's
"the iPad ... still unmeasured" remains true at HEAD.

**Step 4 -- `mist`, and the CSS/canvas split made explicit. BUILT, `906d31a`.**
`fxHasCanvas` in `visualiser.js` names exactly the seven canvas treatments;
`mist` and `bloom` stay CSS-only and never touch `mainCanvas`.

**Step 5 -- the creative five, then four. BUILT, `906d31a`, fixed `7e7d479`,
`aurora` CUT 2026-09-16.** `sonar`, `filaments`, `comet`, `dust` and `aurora`
were all implemented. `7e7d479` fixed two live defects found by using them:
no treatment drew anything below the room's noise floor (an armed, silent
microphone looked unarmed), and `dust` inverted that failure by drawing a
fixed mote count with the microphone off. `aurora` was then CUT rather than
re-tuned a third time -- section 7 has the reason. *Verify still open:* no
recorded-utterance test for `sonar`'s ring-count-matches-syllable-count claim
was found.

**Step 6 -- reduced motion and accessibility, across all nine values. BUILT
AND AUDITED, `906d31a`, extended 2026-09-16.** `armed && !calm() &&
fxHasCanvas` gates the canvas treatments uniformly (`visualiser.js` ~line
1946); reduced motion takes the same branch as an unbuilt effect name and
falls back to `.fxstill`, which carries `--fx-level` as intensity.
`mist`/`bloom` have their own `prefers-reduced-motion` block in
`web/index.html` (~line 1520). **The 2026-09-16 pass found `lattice` was
NOT covered** -- it is a tenth `voice_effect` value and draws through its own
code, not `fxHasCanvas`. `breathNow()` now freezes at 0.5 under `calm()`,
`motion` is `calm() ? 0 : 1`, and `sweepP`'s update and position integration
are separately gated. MEASURED: 6 samples 1.2s apart, byte-identical, 2728
nonzero-alpha pixels every sample (was 4,272 -> 22,068 over ~7s). The still
halo was also raised for reduced motion: floor-vs-off delta 9-12 before, 30
after, against a visibility bar of 8. `web/AGENTS.md`'s composer entry
carries the full numbers.

**Step 7 -- the retirement. RULED, AND MOSTLY MOOT.** Section 1's second
ruling closes as "the lattice survives" rather than "the lattice retires", so
there is nothing to delete: `voice_effect` defaults to `lattice`,
`viz_placement` is consulted only when `voice_effect` is `lattice`
(`daemon/settings.py:319-328`, enforced by `test_settings_api.py:416`), and
`visualiser.js`'s lattice code stays exactly where it is. What is left of
this step is DOCUMENTATION rather than code: no criterion is retired in
`plan.md` because none needs to be -- AC-74..AC-82 stay met -- and this file's
own top-of-page status line and section 1 are the record that the question is
closed.

**Step 8 -- the instruments. STILL PARTLY BUILT.** `test_settings_api.py`
covers the setting's choice list and round trip; `test_visualiser_contract.py`'s
`REQUIRED` list asserts `setEffect` is called; `manual/probe_reduced_motion.py`
(2026-09-16, `test/AGENTS.md`) is a real-browser audit of all ten values under
`prefers-reduced-motion`, plus a `--normal` screenshot mode and a
`--visibility` floor measurement -- it is a manual instrument, not a pytest
gate. STILL MISSING: no per-treatment screenshot check IN PYTEST (nothing
runs `--normal` on CI or on a commit), and no iPad frame-budget measurement
for AC-261. **This is the step the 2026-09-11 lesson is about**, and that
half of the lesson has not yet been applied here: the rejected composer row
shipped because no instrument had an opinion about weight or order, and a
screenshot was the only thing that could have caught it.

---

## 6. Proposed criteria

Not promoted. They move into `plan.md` on the owner's acceptance.

- **AC-252** The effect is a DAEMON setting in the `look` group. `web/` names
  no treatment; the page renders whatever value arrives.
- **AC-253** Every treatment is born at the message box's perimeter and drawn
  outward. Nothing paints inside the field, and that is enforced by stacking
  order rather than by each treatment behaving.
- **AC-254** Listening draws energy inward and speaking pushes it outward, in
  every treatment, so the two are distinguishable with colour removed.
- **AC-255** `--in` and `--out` are identical in all five colour schemes.
- **AC-256** No treatment changes the composer's height or position at any
  level, in any state.
- **AC-257** The level comes from the existing chain in `visualiser.js`. No
  second envelope follower, no second tap, no `AudioContext` constructed.
- **AC-258** `off` stops the drawing and never the `viz.*` call sites.
- **AC-259** Every treatment has a `prefers-reduced-motion` alternative that
  still conveys the level. **Now covers the LATTICE too** (2026-09-16): it is
  a `voice_effect` value like any treatment and its own animation was found
  ungated (section 5 step 6).
- **AC-260** Switching treatments leaves no residue of the previous one.
- **AC-261** Every treatment holds its frame budget on the iPad, or is capped,
  or is not offered. Which, per treatment, is recorded with a measurement.
  The list is the section 2 catalogue (eight, `aurora` cut) plus the lattice,
  since step 6 now covers its frame cost too -- nine, not ten.
- **AC-262** One control answers "what is drawn". A control that cannot act on
  the current value says so or is not shown.

---

## 7. What this could have wrong

Led by the one that decides the outcome.

**The iPad.** Eight treatments plus the lattice, most of them per-frame canvas
work, on a device whose particle budget this project has never measured.
`lattice-wave.md` 9 flagged it as unmeasured in August and it is still
unmeasured. If the tablet cannot carry `ember` and `filaments`, the catalogue
shrinks further.

**Nine values is still a lot of surface for one person.** The owner asked for
range and this answers the ask, but every treatment is code that can break and
no test will have an opinion about whether it looks right. A catalogue with
two loved and the rest never chosen is the likely outcome, and trimming after
a week of living with them is cheaper than trimming now.

**`aurora` was NOT a distinct idea, and it is CUT rather than kept as an open
question.** It was tuned twice against the same benchmark used for the other
four (MEASURED: 5x-20x fainter than ember's baseline, then re-tuned to
"small dim amber lozenges... hard-clipped at the canvas edge") and it still
read as faint triangular smudges at every level from 0.17 to 0.99 -- a shape
defect a brightness pass cannot fix. `voice_effect` is ten choices now, not
eleven (`daemon/settings.py`).

**The CSS/canvas split is a seam inside a seam.** Two treatments that do not
touch the canvas sit in a file whose whole subject is the canvas. It is right
because a blurred gradient in canvas is slower and worse-looking than the
browser's own blur, but it means the mode table has two kinds of entry.

**`transient()` is already tuned for a FLASH, not for a ring.** It is consumed
at `visualiser.js:1292`, where it decays with a 150 ms constant because that is
"about how long a person reads a flash as belonging to the sound that caused
it". `sonar` wants a discrete EVENT -- one ring per onset -- and what exists is
a continuous envelope that peaks on onsets. Step 5 has to derive an edge from
it (a threshold with a refractory period) rather than read it directly, and
that threshold is the thing most likely to fire twice on one syllable or miss
a soft one. An earlier draft of this file claimed nothing consumed
`transient()`; that was wrong, and the correction makes `sonar` harder rather
than easier.

**The lattice question is RULED, 2026-09-16: it survives.** Most of a
1,426-line file is lattice, and AC-74..AC-82 are met criteria in `plan.md`;
the owner declined to delete accepted work and kept `lattice` as
`voice_effect`'s default value instead (section 1). What was a risk in this
file's first draft is now a closed decision, recorded where it is enforced:
`daemon/settings.py:319-328`/`347` and `test/test_settings_api.py:416`.
