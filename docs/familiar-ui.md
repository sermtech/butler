# A page a new user already knows how to use

A SPEC, not the contract. Its criteria (AC-161..AC-174) move into `plan.md`
only as the owner accepts them.

Written 2026-09-10, from the owner's ask: make the web UI more like ChatGPT
or claude.ai, so it is familiar and less intimidating, **keeping every
feature**. Everything below was read from the shipped page on that date; the
four rulings in section 2 are the owner's, made before this file was written.

---

## 0. The finding that reframes the whole job

**Butler already has a ChatGPT-shaped conversation. It is two clicks down,
behind a fold labelled `diagnostics`.**

`#history` (`web/index.html:1158`) is a real transcript: one article per
turn, the question in `.said`, the answer in `.answered`, rendered markdown,
a `<time>` per turn, the tier, the timings, an italic marker for a reply
that was shown but not spoken, and an italic pending row for a turn still
being answered. It restores across reload from the daemon's own chat file.
Fifty turns are kept.

It sits inside a collapsed disclosure whose button says `diagnostics`, below
eighteen HUD readouts and two labelled stubs.

So the work is NOT "build a chat UI". It is **promote the one that exists to
the surface, and demote the instrument panel to where a curious owner can
still find it.** That reframing is what makes this a layout change with two
new daemon routes rather than a rewrite.

Two more things are already built and would be reinvented by a naive
redesign: stop (AC-111/AC-112 -- Escape, and the mic control changing job),
and a New Chat button (`#newSession`). **AC-96 is not an open criterion**; it
was superseded and shipped.

---

## 1. What is actually missing

Five criteria in `docs/satellite-ux.md` are recorded NOT BUILT, and **all
five need a daemon or protocol change** -- which is exactly why the ten that
did ship were `web/`-only. Three of them are what a new user reaches for
first:

| | | |
|---|---|---|
| **AC-95** | typing is a peer of speaking | no `<textarea>` exists anywhere |
| **AC-98** | past conversations are reachable | 3 chats on disk, 1 route, 2 unreachable |
| **AC-97** | retry, and correct a misheard turn | depends on AC-95's inbound message |
| AC-99 | a turn says what it cost | needs timings from `llm.py` |
| AC-92 | the page's watchdog respects daemon policy | BUILT AND REVERTED in an hour |

The other gaps are smaller and are `web/`-only: no per-message copy button,
no scroll-to-bottom, no in-message streaming indicator, and no CSS at all for
`.saved` or `.busy` -- so **a successful settings save is currently
invisible** (`settings.js:912`, no matching rule in `index.html`).

---

## 2. The owner's rulings, 2026-09-10

**R1. Full ChatGPT shape, with a HIDEABLE sidebar**, keeping some of
Butler's dynamic character. Not a reskin and not a rebuild-from-zero: the
sidebar, the centre column and the bottom composer, with the wave surviving
in a form the owner chooses.

**R2. Typed input is REAL** (AC-95). Not mocked, not disabled. The owner
chose this knowing it needs a protocol addition, and the reason is AC-7b: a
composer that is drawn and does nothing is the exact control-that-lies
failure this project keeps paying for. **If the daemon half is not built,
the composer is not drawn.**

**R3. The visualiser's placement is the OWNER's choice, not this spec's.**
Four placements are built and offered as a setting: in the mic button, a
band under the header, full-bleed behind the chat, and off. The mock ships
all four with a live switcher so the decision is made by looking rather than
by reading.

**R4. Conversation history is REAL** (AC-98). `daemon/chats.py` already
writes one flat JSON file per conversation; the routes to list and read them
are new, the store is not.

**R5. The typeface is a SETTING, and IBM Plex Sans stays the default.**
Owner, 2026-09-10, on the mock: "this, but user gets to choose others on
settings page with persistence." So the six trial faces all SHIP and the
owner picks on the settings page like any other preference. This reverses
the delete-the-losers half of AC-173 and the criterion is rewritten below.
The 8% size increase is BAKED IN rather than offered, because that one was
settled by looking.

**R6. A typed turn obeys the EXISTING `speak` setting.** Owner's ruling: no
new rule. A typed question is answered out loud exactly when a spoken one
would be. The cost is stated rather than hidden -- to type quietly you must
also turn speech off, which is two actions for one intent -- and it was
chosen over a typed-is-always-silent rule because the alternative invents a
second speech policy that the page then has to explain.

**R7. A typed turn CANCELS a capture in flight.** Owner's ruling: pressing
Enter is a deliberate act with a clear end; a live capture is provisional,
and the newer explicit intent wins. The capture is abandoned the way a
barge-in abandons one -- through the existing terminal path, so it re-arms
(D-1) -- and never by a second mechanism.

---

## 3. The shape

```
  +----------------+----------------------------------------------+
  | [+] New chat   |  Butler            [state chip]  [theme] [=]  |
  |            [<] +----------------------------------------------+
  |                |                                              |
  | TODAY          |    +--- optional wave band (placement: top) --+
  |  Portuguese... |                                              |
  |  Fix the tests |         You                          09:14   |
  |                |         what time is it                      |
  | YESTERDAY      |                                              |
  |  Site slides |         Butler        [reflex]       09:14   |
  |                |         It is twenty past four.              |
  | EARLIER        |                                              |
  |  Bar chart     |         You                          09:15   |
  |                |         run the tests in butler              |
  |----------------|                                              |
  | PROJECTS    [v]|         Butler        [harness]      09:16   |
  |  butler        |         791 passed, 4 failed.                |
  |   [claude][pi] |         ```                                  |
  |  SITE        |         test/test_seams.py ...               |
  |   [claude][pi] |         ```                                  |
  |----------------|                                              |
  | [=] Settings   |    +--------------------------------------+  |
  | idle           |    | Message Butler...        ( (o) ) [>] |  |
  +----------------+    +--------------------------------------+  |
                        Enter to send - hey jarvis - Esc to stop   |
                        +----------------------------------------+
```

Three columns collapse to one on a phone: the sidebar becomes an overlay
behind a hamburger, the composer stays pinned, the conversation fills the
rest.

**What moves where**

| Today | Becomes |
|---|---|
| `#history`, inside the `diagnostics` fold | THE PAGE. The centre column. |
| The hero: kicker, gradient headline, big transcript | A state chip in the header plus the live partial in the composer |
| The mic: 5 treatments, one shown | One treatment, in the composer. `mic_style` is retired -- see 6.4 |
| `#wave`, a 150-212px band always present | One of four placements, the owner's choice (R3) |
| `#field`, full-page canvas at z-index 0 | The `ambient` placement, unchanged |
| The shortcuts pane, a fold in `main` | A collapsible section in the sidebar |
| 18 HUD readouts + wake card + stubs | A `diagnostics` fold, still there, now honestly named and out of the way |
| Settings, a full-screen surface | Unchanged. It works and it is specced (AC-83..AC-90) |

---

## 4. What must not break

These are not style preferences. Each is enforced, and the enforcement is
named so a redesign can be checked against it rather than argued about.

**Gated by a test that will fail CI**

- **No `localStorage` or `sessionStorage`, anywhere, even in a comment**
  (AC-8, D-2). `test/test_http_surface.py:86` greps `web/*.js` and
  `web/*.html` for the plain substring. There is currently zero browser
  storage in `web/`, and every piece of view state that looks like it wants
  storage -- the shortcuts fold, the `show_chords` disclosure -- lives in the
  DAEMON's store instead. **A sidebar's collapsed state is view state and
  therefore belongs in the daemon too.**
- **`app.js` may not be renamed.** `test/test_http_surface.py:44`
  parametrizes over `["/", "/app.js", "/api/settings"]`.
- **The seven `viz.*` call sites must survive.**
  `test/test_visualiser_contract.py:100` scrapes `app.js` for every
  `viz.<method>` and requires all of `attachInput attachOutput setMode
  pushScore setState detach colors`. **Removing the wave from the layout
  without removing the calls is fine; removing the calls is not.** The `off`
  placement therefore stops DRAWING, it does not stop CALLING.
- **`visualiser.js` stays pure ASCII, opens no AudioContext, and has no
  top-level import/export** (`test_visualiser_contract.py:49,70,106`).
- **A settings group carries exactly `{key,label,stage,headline,blurb,
  inactive}`** (`test_settings_api.py:325`) and the group order is pinned
  (`test_seams.py`). A new section is added in the DAEMON, never in `web/`.

**Enforced by review, and equally binding**

- **AC-41: nothing in `web/` names a setting, a group, a stage, an engine or
  a model.** The rail, the search index, the agent buttons and the section
  blurbs are all built from the daemon's payload. The carve-outs are
  documented and closed; this spec adds none.
- **The transcript renderer is a security boundary** (AC-94). A small
  explicit tokenizer, never `innerHTML`, never a library, emitting a closed
  set: `strong em code pre>code ul ol li`. **No `<a>` and no `<img>`, ever**
  -- the text comes from a microphone in a room or from a full-capability
  coding agent, and an always-on page must not turn that into something
  clickable. Measured after AC-94: 0 anchors, 0 images.
- **No second URL and no navigation away.** This page IS the satellite: it
  holds the microphone, the wake detector, the socket and the playback
  worklet. Every "conversation" and "new chat" affordance is in-page.
- **No fourth Escape case** (AC-112). Settings open closes settings;
  thinking or speaking stops; everything else does nothing. **The composer
  makes this harder and section 6.2 rules on it.**
- **Hue is session state and nothing else.** Settings being open is not a
  state. No component may introduce a colour of its own.
- **Every terminal path re-arms** (D-1). No new latch on the microphone.
- **Not-yet-built things are labelled dashed stubs**, deleted in the commit
  that ships the replacement.
- **No build step, no bundler, no npm, no CDN, no fetched fonts.** Fonts are
  vendored so the satellite renders with no internet at all.
- **Pure ASCII in `index.html` and `settings.js`**, checked by
  `LC_ALL=C grep -c "[^ -~]"`.

**The instruments**

`test/manual/probe_page.py` and `probe_ux.py` are the ONLY acceptance
instruments for AC-91..AC-106 and for the whole settings surface, and they
hard-code the current DOM: `#errors #notice #reply #history .histitem
.answered .meta time #micWrap button[data-mic] #panel #settingsBtn
label.switch` and twenty more selectors. **A redesign that moves this markup
must update both probes in the same commit**, or the acceptance evidence for
sixteen criteria silently stops being evidence. That is the single largest
piece of work this spec does not otherwise account for.

---

## 5. Acceptance criteria

Each says which way it fails and whether that is recoverable.

- **AC-161 (the conversation is the page).** The turn-by-turn transcript is
  the centre of the main view, visible with no click, showing question,
  answer, time and tier. The instrument panel survives behind a fold that is
  honestly named. Fails as: a redesign that keeps the transcript behind a
  disclosure, or one that drops a HUD readout without saying so.
  Recoverable.
- **AC-162 (typing is a peer of speaking).** A typed message starts the same
  turn a spoken one does: same router, same brain, same record. Protocol v1
  gains ONE inbound message. The microphone does not stop being live while
  the composer has focus. Fails as: a composer that sends nothing, which is
  AC-7b, or one that mutes the room to be typed into. Recoverable.
  **NOT recoverable if the daemon half is skipped and the field is drawn
  anyway** -- then the page has a control that lies, and R2 forbids it.
- **AC-163 (the daemon mints turn ids, still).** A typed turn carries no id
  from the page (D-6). Fails as: a satellite naming a turn it does not own.
  Recoverable, and the reason AC-95 was flagged under-specified.
- **AC-164 (past conversations are reachable).** The sidebar lists the
  conversations already on disk, newest first, and opening one shows it
  read-only. Fails as: a sidebar that lists only the live session, which is
  half a feature wearing the whole feature's shape. Recoverable.
- **AC-165 (a new chat is one click and says what it did).** Fails as: a
  button that clears the view without rotating the daemon's conversation --
  the two must not diverge.
- **AC-166 (the sidebar hides, and stays where it was put).** Its state
  survives a reload and a second device agrees, because it lives in the
  daemon's store and not in the browser (AC-8). Fails as: a sidebar that
  reopens on every load, or one that reaches for `localStorage` and reddens
  `test_http_surface.py`. Recoverable.
- **AC-167 (the visualiser's placement is the owner's).** Four placements --
  in the mic control, a band under the header, full-bleed behind the
  conversation, and off -- selectable as a setting. `off` stops the DRAWING
  and not the `viz.*` calls. Fails as: a fifth hardcoded choice in `web/`,
  or a contract test reddened by a deleted call site. Recoverable.
- **AC-168 (state is legible without colour).** Every state is named in
  words somewhere always-visible. This is the standing answer to the light
  theme collapsing `thinking` and `speaking` into one hue, and it is why the
  state chip is not decoration. Fails as: a redesign where the only signal
  that Butler is speaking is that something turned gold. **Not recoverable
  by a later CSS change** -- if the words are not in the layout, they have
  nowhere to go.
- **AC-169 (a message can be copied).** Per-message copy, keyboard
  reachable. Fails as: a hover-only control, which on a tablet is no
  control.
- **AC-170 (the empty state teaches the wake phrase).** A new chat shows
  what Butler is and three or four example prompts, and says "hey jarvis"
  where a new user will read it (extends AC-105). Fails as: an empty page
  that gives a first-time user nothing to do.
- **AC-171 (a save is visible).** A setting that saves says so. `.saved` and
  `.busy` are emitted by `settings.js` today and have no CSS at all, so a
  successful non-restart save currently produces no feedback whatsoever.
  Fails as: it already does. Recoverable and cheap.
- **AC-174 (the surface is the owner's, and it never eats the state).**
  Three colour schemes -- `instrument`, `sand`, `plain` -- each working in
  both light and dark, stored on the daemon beside `theme` and the face.
  A scheme repaints surfaces, lines and text ONLY: `--sa`, `--sa-dim`,
  `--g1`, `--g2`, `--warn` and the eight `[data-state]` rows are identical
  in all three. Verified rather than asserted -- a test reads the stylesheet
  and fails if a palette block declares a state token. Fails as: a theme
  that repaints the accent, which does not change how the page looks so much
  as delete the one channel that says what Butler is doing (AC-76).
  **Not recoverable by a later fix** -- once a scheme owns the accent, every
  later state colour is a negotiation with six surfaces.
- **AC-173 (the reading face is the owner's, and it persists).** The type
  is 8% larger than it was, baked in. The FACE is a setting with six
  choices, defaulting to what ships today, and it survives a reload and a
  second device because it lives in the daemon's store like `theme` -- never
  in browser storage, which AC-8 bans outright. The daemon names the faces;
  nothing in `web/` does (AC-41). Fails as: a font picker in `localStorage`,
  which reddens `test_http_surface.py`; or a page that downloads all six
  families instead of the one it sets. Recoverable.
  **This REVERSES the delete-the-losers half of the same criterion**, ruled
  by the owner on 2026-09-10 after seeing the mock (R5). The cost, stated:
  six vendored families is about 190 KB on disk against 46 KB, and
  `fonts.css` declares five faces the page will usually not draw. A browser
  fetches a family only when something is set in it, so the runtime cost is
  one face -- but the repository now carries all six and `fetch-fonts.py`
  keeps `--candidates` permanently.
- **AC-172 (the probes still prove what they proved).** `probe_page.py` and
  `probe_ux.py` pass against the new markup, updated in the same commit that
  moves it, with the BEFORE numbers for AC-91..AC-106 unchanged or better.
  Fails as: sixteen criteria quietly losing their evidence. **Not
  recoverable later** -- once the probes are stale nobody can tell which
  regression was introduced when.

---

## 6. Decisions

### 6.1 The composer sends on Enter, and that costs a rule

Enter sends and Shift+Enter newlines, because that is what the familiar
apps do and familiarity is the whole point. The cost is that the page now
has a text field that swallows keys, so `/` (focus search) and `space` must
not fire while it has focus. Both are already scoped; this adds one
condition rather than a new mechanism.

### 6.2 Escape keeps THREE cases, and the composer does not get a fourth

AC-112 is a hard contract: settings open closes settings; thinking or
speaking stops; otherwise nothing. A composer wants a fourth -- "clear the
draft" -- and it does not get one. **Escape while thinking or speaking stops
the turn even if the composer has focus**, because AC-111 is the only
irreversible control on the page: a mis-hit Escape destroys a coding agent's
work in progress, and a user who has learned that Escape stops Butler must
not find that the text field silently disabled it.

The draft is cleared by the send, by the New Chat button, or by selecting
the text. Not by Escape.

> **PARTLY OVERTURNED 2026-09-11 by the owner's own ask** -- "ESC should
> function just like stop and cancel message input, or output if agent is
> answering already". Escape has a fifth case now and it CLEARS the draft.
>
> What SURVIVES is the whole of the paragraph above, and it is what decides
> the new case's position: **Escape while thinking or speaking still stops the
> turn even if the composer has focus and even if there is a draft in it.**
> Clearing is checked LAST, after the stop, so the key keeps the meaning a
> user has learned. `e2e_scenarios.py` F3b is the check with teeth -- it types
> a draft DURING a turn, presses Escape, and asserts Butler stopped and the
> text stayed.
>
> What LOST is only the claim that the two cases cannot be told apart. The
> owner's phrasing is itself the rule that tells them apart: stop when there
> is something to stop, clear when there is not.
>
> **A SECOND paragraph of this section is also overturned, and it was not
> about Escape.** 6.1's send button was "one control, two jobs, and the job is
> the state's": it became the stop whenever Butler was thinking or speaking.
> That made typing a follow-up while he was still talking STOP him and discard
> the message silently -- measured, and the window is open on every turn,
> because the page holds its speaking state until its own audio queue drains.
> A draft now outranks the stop. See `web/AGENTS.md`.

**OPEN, AND THE OWNER'S TO RULE ON.** The mock disagrees with the paragraph
above, deliberately and in writing: on a phone the sidebar is an overlay,
and its own cost list says "Escape closes the drawer FIRST and stops the
reply second, which is a decision, not an accident." That IS a fourth case.

Both readings are defensible and they cannot both be right:

- **Three cases (this spec).** Escape always stops a running turn. A user
  who has learned the one irreversible control must never find a drawer
  disabled it. Cost: an open overlay has no keyboard dismissal, which on a
  phone with a keyboard attached is a trap.
- **Four cases (the mock).** An open overlay is modal and Escape dismisses
  the topmost thing, which is what every familiar app does -- and is already
  how settings behave, since settings-open outranks stopping. Cost: AC-112's
  contract gains a case, and the one control that can destroy a coding
  agent's work becomes conditional on what else is open.

The second argument is stronger than it first looks, because the settings
surface ALREADY takes precedence over stopping -- so the contract is not
"Escape always stops", it is "Escape dismisses the topmost surface, else
stops". A sidebar overlay is a surface by that reading and the rule does not
change at all. **Recommended: four cases, on that reasoning** -- but AC-111
is flagged in `voice-control.md` as the only irreversible criterion on the
page, so this is not a call to make on tidiness.

### 6.3 The sidebar's collapsed state lives in the DAEMON

Not because it is important, but because AC-8 bans browser storage outright
and the shortcuts pane already set this precedent for exactly this kind of
view state. It rides on the same store.

### 6.4 `mic_style` is RETIRED, and that is a real loss

Five microphone treatments -- `orb ring glyph capsule arc` -- exist because
the mic was the centrepiece of the page. In the composer it is a 40px button
beside a text field and four of the five have nowhere to be. Keeping all
five would mean five composer layouts.

The cost, stated rather than buried: the owner picked `arc` and that choice
is being taken away. The setting is migrated out and its key deleted the way
`asr_language` was (`settings.RENAMES`). **`probe_page.py --live` clicks the
VISIBLE mic control precisely because five are in the DOM** -- that comment
becomes wrong in the same commit.

### 6.5 The hero is not preserved, and the wave is

The gradient headline, the kicker and the 30px live transcript are the
page's character and they are also why it does not look like anything a new
user has used. They go. What replaces them: the state chip carries the
words, the composer carries the live partial, and the wave carries the
motion in whichever placement the owner picks. **R3 exists because the wave
is the part worth arguing about and the owner should decide by looking.**

### 6.7 The typeface changes, and it is one line

Owner's ruling, 2026-09-10, on seeing the mock: the layout is approved and
**the fonts should be friendlier and easier to read while staying modern.**

IBM Plex Sans is a corporate-neutral grotesque. It is not unfriendly, but it
is the face of an instrument panel, which is exactly what this redesign is
moving away from. The mono is doing more work than the sans here -- every
label, chip, tag, timestamp and readout is set in it -- so both are on the
table.

**The swap is genuinely one line.** `scripts/fetch-fonts.py` holds a single
Google Fonts CSS2 URL, fetches the latin subset, de-duplicates by digest and
regenerates `web/fonts.css`. Change the family, re-run, done. `web/fonts/`
is gitignored, so trialling costs nothing in the repository.

Six faces are vendored behind a `--candidates` flag and switchable live in
the mock, because a typeface is decided by looking at real labels and real
numerals, not by reading names. Measured, latin subset:

| face | bytes | why it is here |
|---|---:|---|
| IBM Plex Sans | 45.7 KB | the control, what ships today |
| Figtree | 20.2 KB | friendly geometric, and the smallest of the modern ones |
| Plus Jakarta Sans | 27.3 KB | modern and distinctive without being quirky |
| Inter | 48.3 KB | the neutral workhorse every app uses; safest, least characterful |
| Nunito Sans | 31.1 KB | the roundest and warmest |
| Atkinson Hyperlegible | 34.7 KB | drawn by the Braille Institute for legibility |
| JetBrains Mono | 31.4 KB | the mono candidate, against IBM Plex Mono's 29.6 KB |

**Atkinson ships 400 and 700 ONLY.** The design uses 300, 400, 500 and 600,
so 500 and 600 would be synthesised or rounded. That is a real constraint on
the face whose entire argument is legibility, and the mock says so on its
face rather than letting it quietly look worse than it is.

**The type is 8% larger overall** (owner, 2026-09-10). Every font-size in
the product part of the mock is `calc(<designed px> * var(--ts))`, so one
number moves the page and a 10 px mono label and a 58 px headline grow by
the same proportion rather than converging. The mock bar is deliberately not
scaled -- scaffolding that grew with the page would make the comparison
drift. Measured at 1.08: body 15.5 -> 16.7 px, chat titles 13.5 -> 14.6 px,
mono labels and timestamps 10 -> 10.8 px, with zero horizontal overflow at
390 px and 1440 px even at the stepper's 1.30 ceiling. The winning number is
written into the real page as a plain multiplier on the designed sizes, NOT
shipped as a variable -- a scale knob in production is a second way to say
the same thing and the two drift.

Not a free change: the type scale is tuned to Plex's metrics. Every face
above has a different x-height and different advance widths, so the `ch`
caps (`30ch`, `42ch`, `62ch`), the mono tracking (`0.10-0.16em`) and the
44 px touch targets all want re-checking. The sidebar rows and the composer
microcopy are where a wider face will show first.

---

### 6.8 Colour schemes, and the one thing they may not touch

Owner's ask, 2026-09-10: offer other colour schemes as options, in the
spirit of chatgpt.com and claude.ai without copying either.

**The constraint that shapes this: HUE IS SESSION STATE AND NOTHING ELSE.**
That is AC-76 and `web/AGENTS.md` states it as a design rule -- the
temperature arc from cold `capturing` through the dim warm pivot at
`thinking` to vibrant gold at `speaking` is how the page says whose voice it
is drawing, readable across a room. A colour scheme that repaints the accent
does not change the theme; it deletes the state channel.

So a scheme is a SURFACE palette and nothing more:

| may change | may NOT change |
|---|---|
| `--bg`, `--bg-deep`, `--panel`, `--panel-2` | `--sa`, `--sa-dim` |
| `--line`, `--line-soft` | `--g1`, `--g2` |
| `--fg`, `--fg-2`, `--fg-3` | the eight `[data-state]` rows |
| `--shadow`, the body radial ground | `--warn` |

Three palettes, each with the existing light and dark:

- **`instrument`** -- what ships today. Cool blue-violet neutrals at hue
  275. The default, and the one the state ramp was tuned against.
- **`sand`** -- warm. Paper-cream in light, warm charcoal in dark, neutrals
  around hue 70-90. The nearest relative of claude.ai's surface, arrived at
  from Butler's own tokens rather than sampled from theirs.
- **`plain`** -- near-achromatic. Chroma at or below 0.004, so the surface
  says nothing and the state accent is the only colour on the page. The
  nearest relative of chatgpt.com, and the strongest possible version of
  "hue is state" -- there is no competing hue anywhere.

**`plain` is the interesting one and may be the best of the three**, because
the rule this project already committed to is exactly what that surface is
for. It is listed third because it is the biggest change from today, not
because it is the weakest.

**Every palette must be CHECKED against the known defect, not assumed.**
Gold at half lightness already reads as brown on the light `instrument`
surface, and `thinking` and `speaking` collapse into one state there
(measured: light `thinking` (130,75,0) against light `speaking` (160,96,0)).
A warm surface can only make that worse -- gold on cream has less separation
than gold on white. So each palette ships only once its light variant has
been looked at in both those states, and AC-168's requirement that state is
named in WORDS somewhere always-visible is what stops any of them being
unusable.

**The cost nobody sees until it bites:** the canvas cannot read a custom
property, so `app.js` holds `MESH_DARK` and `MESH_LIGHT` as hand-written
rgba and `visualiser.js` infers which theme it is in from the LUMINANCE of
`colors.nodeIdle`. Three palettes times two themes is six surfaces for a
mechanism built for two, and D-30 already names this shape -- the stylesheet
and the canvas are one decision written twice and must change in one commit.
Luminance inference still works (a palette changes tint, not lightness
band), but it is now carrying more than it was designed for, and that should
be said before it is discovered.

### 6.6 What this spec does NOT do

- It does not touch the settings surface. AC-83..AC-90 are met and the
  surface is reached the same way.
- It does not reopen the colour ramp. The light-theme gold collapse is the
  owner's recorded ruling; AC-168 works around it by never relying on hue
  alone, which is the cheaper half of the fix the ramp's own spec named.
- It does not build AC-99 (per-turn cost) or AC-97 (retry). AC-97 becomes
  cheap once AC-162 exists and should be the next thing after it.
- It does not add avatars, message reactions, model pickers in the composer,
  or file upload. Butler is one assistant on one machine.

---

## 7. The implementation plan

Written 2026-09-10 after the owner approved the mock. Seven steps. Each ends
at something observable, each names its verification, and the order is
chosen so that **nothing user-visible ships until the thing behind it
works** -- which is the whole of R2.

**Steps 1, 2 and 7 are daemon work and ship alone.** Steps 3-6 are the page
and land together, because a half-moved layout has neither the old
acceptance evidence nor the new.

### Step 1 -- Past conversations become reachable (AC-164)

`daemon/chats.py` already writes one flat JSON file per conversation into
`state/chats/`. Three exist; `GET /api/session/current` is the only route to
any of them, so two are unreachable from the page that wrote them.

- `GET /api/chats` -- id, title, turn count, last-modified, newest first.
  The title is the first user utterance, truncated; a conversation has no
  name and inventing a naming ceremony is scope.
- `GET /api/chats/{id}` -- the messages, bounded the way `MESSAGES_SERVED`
  already bounds the current one.
- No `DELETE` and no rename in this step. Read-only is AC-164's own wording.

VERIFY: all three conversations on disk are readable over HTTP; a bad id is
a 404 naming the id, not a 500; the bound is asserted with a chat longer
than it. `test/test_chats.py` is the home.
SHIPS ALONE -- useful from `curl` before the page has a sidebar.

### Step 2 -- A typed turn (AC-162, AC-163) -- BUILT 2026-09-10

**Interviewed, then built.** `docs/satellite-ux.md` 11 named three open
questions. AC-163 answered one (the page mints no turn id, D-6) and the
owner ruled on the other two before the build, as R6 and R7 in section 2: a
typed question is spoken exactly when a spoken one would be, and a typed
message cancels a capture in flight.

What shipped is ONE inbound message and nothing else:

```
satellite -> daemon   {"type": "typed", "text": "<what was typed>"}
```

`Hub._typed` builds a `ports.Final` and hands it to `_on_final`, so
everything downstream is the code that was already there -- the three
daemon-answered utterances, the router, the brain, the record, the re-arm.
It sends no `capture_start`, because nothing is being captured: the `final`
carrying `reason:"typed"` is the first frame of the turn and is what names
it. `_barge_in()` is the wake word's own teardown, now named because it has
two callers (D-18). Empty text is refused and text past `TYPED_MAX_CHARS`
is refused rather than truncated, both BEFORE the barge-in so a message
Butler will not act on cannot cost a capture; the ceiling is derived from
`dictate_max_seconds`'s own maximum rather than invented.

VERIFIED: `test/test_typed_turn.py` (17 cases) diffs a typed turn's whole
wire against a spoken one's, both on real hubs; `manual/mutate_typed_turn.py`
is the D-13 harness, 6/6 caught. `plan.md` 4.3 gained the message -- and
`stop`, which AC-111 shipped and never wrote down (D-12).
SHIPPED ALONE -- drive it from a socket client with no UI at all.

### Step 3 -- The mock becomes the page (AC-161, AC-165, AC-166, AC-169, AC-170)

`web/mocks/j-chat.html` is the approved design. Port it into
`web/index.html` and `web/app.js`, keeping every rule in section 4.

Order within the step, because the page must never be half-moved in a
commit: markup and CSS first with the old JS still driving it, then the
transcript moves out of the `diagnostics` fold, then the sidebar, then the
composer wired to step 2's message.

- The sidebar's collapsed state goes in the DAEMON's store (6.3), riding on
  the shortcuts pane's existing mechanism. **Not `localStorage`** -- that
  reddens `test_http_surface.py:86`.
- The `diagnostics` fold keeps all eighteen HUD readouts. Every `hud(id)`
  needs a slot or the diagnostic is silently discarded.
- Per-message copy must be keyboard-reachable, not hover-only (AC-169).

VERIFY: by eye at 390 px and 1440 px, in both themes; `node --check` on both
modules; the ASCII gate on `index.html`.

### Step 4 -- The visualiser placement setting (AC-167)

One new setting with four values. The DAEMON names them -- nothing in
`web/` may (AC-41).

**`off` stops the DRAWING and never the calls.** All seven `viz.*` call
sites stay in `app.js` or `test_visualiser_contract.py:100` goes red.

The risk is named in 8.3 and this is where it lands: `visualiser.js` reads
`#wave`'s bounding rect every frame to place its text falloff. In three of
the four placements `#wave` is not where it was. Expect to re-tune, and
expect it to look wrong before it looks right.

VERIFY: each placement in both themes, and the contract test green.

### Step 5 -- The probes (AC-172) -- BUILT 2026-09-10

**It was an UPDATE, not a rewrite** -- the answer 8.5 asks for. Every check
either probe made before the reshape it still makes, with its own BEFORE
number: `probe_ux.py` went 23 checks to 41 by ADDING AC-168, AC-169, AC-170
and AC-171, and dropped nothing. So the evidence for AC-91..AC-106 is
PRESERVED rather than re-created. Green against a scratch daemon holding
eleven conversations: `probe_ux.py` 41/41 exit 0; `probe_page.py --live` and
`--live --write` exit 0, no MISSING element, no page error, no failed
request.

Two findings the plan did not predict, both now in `web/AGENTS.md` and
`test/AGENTS.md`. **The stale probes did not FAIL, they SKIPPED.**
`#micWrap button[data-mic]` matched nothing, so `--live`'s click never
happened, no socket opened, nothing was restored -- and AC-94 printed
"SKIP ... this daemon restored no conversation" inside a run that exited 0.
A criterion can leave the room without anybody's build going red.
**AC-104 was passing on two identical colours.** Chrome serialises a declared
`oklch(...)` as oklch and an interpolated one as oklab, and a CSS transition
never FINISHES in headless Chrome -- measured, playState "running" with the
start value still in the computed style 3.5 s into a 300 ms transition -- so
the OFF read was the ON colour wearing a different spelling. It now finishes
the animations and compares RGBA bytes off a 1x1 canvas.

**Same commit as steps 3 and 4, or the acceptance evidence for sixteen
criteria silently stops being evidence.**

`probe_page.py` and `probe_ux.py` pin about thirty selectors across markup
that steps 3 and 4 move wholesale. Update them, and re-run with the BEFORE
numbers visible -- the probes print them, which is what makes a regression
read as "was 42, want 0, got 7".

If this turns into a rewrite rather than an update, **say so out loud**: the
evidence for AC-91..AC-106 is then being re-created rather than preserved,
and that is a different claim (8.5).

### Step 6 -- Appearance: face, surface, and a save that is visible (AC-173, AC-174, AC-171)

- **The face is a SETTING** (R5), not a single choice: all six ship,
  `IBM Plex Sans` is the default, the DAEMON names them (AC-41) and the
  value lives beside `theme` in its store. `scripts/fetch-fonts.py` keeps
  `--candidates` permanently and the flag's comment stops saying the losers
  come out.
- **Three colour schemes** (AC-174, 6.8): `instrument`, `sand`, `plain`,
  each in light and dark. A palette block may declare surface, line and
  text tokens ONLY -- add the test that reads the stylesheet and fails if
  one declares `--sa`, `--g1`, `--g2` or a `[data-state]` row.
- Look at the light variant of every palette in `thinking` and `speaking`
  before shipping it. Gold on cream separates less than gold on white, and
  gold on white already barely separates.
- Re-check the `ch` caps, the mono tracking and the 44 px targets against
  the new metrics (6.7).
- Add CSS for `.saved` and `.busy`. `settings.js:912` has emitted both since
  it was written and neither has ever had a rule, so a successful
  non-restart save produces no feedback at all today.

VERIFY: `fonts.css` declares only what the page sets; the specimen strip's
mono labels and numerals still read at 10 px; saving a setting visibly says
so.

### Step 7 -- Retire `mic_style`, then DOX (6.4)

Migrate the key out with `settings.RENAMES`, the way `asr_language` went.
Delete the four unused mic treatments from the markup and the comment in
`probe_page.py` that explains why it clicks the visible one.

Then the DOX pass, and AC-161..AC-173 into `plan.md` on the owner's
acceptance.

### What is deliberately NOT in this plan

AC-97 (retry and correct) becomes cheap once step 2 exists and should be the
next thing after it, not part of this. AC-99 (per-turn cost) needs timings
`llm.py` and the harness adapters do not currently report. Neither is
blocking.

---

## 7a. The same thing in one view

Each step ends at something observable, and steps 1-2 are the daemon work
that step 3 refuses to be drawn without.

```
  step 1 --- GET /api/chats, GET /api/chats/{id}   (AC-164)
     |       VERIFY: three conversations on disk, all three readable.
     |       Bounded like MESSAGES_SERVED already bounds the current one.
     v
  step 2 --- {"type":"typed", text}  BUILT 2026-09-10  (AC-162, AC-163)
     |       VERIFIED: a typed turn and a spoken turn diff to the SAME
     |       record and the same wire. R6 and R7 answered the two open
     |       questions satellite-ux.md 11 left; AC-163 answered the third.
     v
  step 3 --- the mock, approved, becomes the page   (AC-161, AC-165..AC-171)
     |       VERIFY: by eye at 390px and 1440px, in both themes.
     v
  step 4 --- probe_page.py and probe_ux.py updated  (AC-172)
     |       VERIFY: the BEFORE numbers for AC-91..AC-106 unchanged or better.
     |       SAME COMMIT as step 3, or the evidence is stale.
     v
  step 5 --- mic_style retired and migrated (6.4), DOX, criteria to plan.md
```

Steps 1 and 2 ship alone and are useful alone: the routes make three
conversations reachable by any client, and typed input works from `curl`
before the page has a box to type in.

---

## 8. What this spec could have wrong

**8.1 The composer may make the microphone feel optional, and that would be
a regression in what Butler IS.** Every familiar app is text-first with
voice bolted on; Butler is the reverse. If the page teaches "type here" and
the wake word withers, the thing that made it worth building is gone. AC-170
is the mitigation and it is weak -- an empty state teaches the first user
once. The honest test is whether the owner still talks to it a week later.

**8.2 Retiring `mic_style` removes a control the owner uses.** 6.4 states
the cost. If they object, the fallback is to keep two treatments rather than
five, which is one composer layout with a modifier class.

**8.3 ANSWERED, 2026-09-10, and the prediction was WRONG -- but it found
something adjacent that is real.** This section warned that
`visualiser.js` reads `#wave`'s bounding rect to place its particle text
falloff, and that in three of four placements `#wave` would not be where it
was. Measured: it does not bite. `#field` is hidden in exactly the three
placements where `#wave` moved or vanished, so the rect is only ever read
in `ambient` -- and there `#wave` is where it always was. The floors
absorbed the band shrinking too: `max(80, 72*0.52)` is the same 80 the old
150 px band gave.

What IS real is a consequence of the rule rather than a defect in it. In
`ambient` the lattice lands entirely in the TOP FIFTH of the page --
ten horizontal bands measured 25831 / 15607 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0
lit pixels. The daemon's own help text for that choice says it "fills the
space behind what you are reading"; it is a 150 px strip under the header.
Closing that means letting `ambient` scale its falloff to the VIEWPORT,
which `web/AGENTS.md` explicitly forbids ("never of the viewport's") --
so it is **a ruling for the owner, not a fix**, and it was left alone.

**8.3a The wave placement may still have no good answer.** `ambient` is the only
one that keeps the shipped drama, and it is also the one that most fights a
scrolling conversation -- a full-bleed canvas behind reading text is exactly
what the text-falloff tuning in `visualiser.js` exists to stop, and that
tuning reads `#wave`'s bounding rect. If `#wave` is gone in three of the
four placements, that falloff is measuring something that no longer exists.
**This is the most likely thing in this spec to be wrong in a way that only
shows up on a real screen.**

**8.4 Nothing here has been tested with a screen reader, on a phone, or on
the iPad**, and neither was the page it replaces (`satellite-ux.md` 11). The
sidebar is a new landmark and an overlay on narrow screens, which is the
most accessibility-sensitive thing this project has built.

**8.5 `probe_ux.py` may be harder to update than to rewrite.** It pins
thirty selectors across a layout that this spec moves wholesale. Step 4 is
written as an update; if it turns into a rewrite, the acceptance evidence
for AC-91..AC-106 is being re-created rather than preserved, and that should
be said out loud rather than discovered in a diff.

**8.6 Two conversations on disk are currently unreachable, and nobody
noticed.** That is the measured state today. It is worth asking why before
building the route: if the owner has never wanted an old conversation, AC-164
is a sidebar full of something nobody opens, and the sidebar's real job is
just New Chat and the projects list.
