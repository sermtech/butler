# The one-page settings surface

**SUPERSEDED IN PART, 2026-10-27.** The one-page surface was replaced by the
categorised settings sheet of `docs/lantern-ui.md` 5.1 (Step 5, AC-700..AC-705,
owner rulings R2 and CU-11). AC-83 (one scroll) is superseded by AC-700 (one
category page at a time), AC-84 (the scroll-spy rail) by AC-701 (the
signal-path strip) and AC-88 (the phone strip) by AC-705 (list, page, back).
AC-85 and AC-86 hold, across categories and in the docked restart bar (AC-702,
AC-703); AC-87, AC-89 and AC-90 hold as written. Sections 3, 4 and 8 describe
the replaced layout and are history; `web/AGENTS.md` "Settings sheet" is the
current contract.

A SPEC, not the contract. **BUILT 2026-08-19**, and its criteria
(AC-83..AC-90) are MET in `plan.md`; on any other point of conflict `plan.md`
wins. The three criteria section 7 asked to retire are SUPERSEDED in `plan.md`
(AC-55, AC-57, AC-58) and the fourth is AMENDED there (AC-56).

Written 2026-08-19. The design was chosen from `web/mocks/settings.html` and
locked on the same day; this is the spec for putting it in the real page.

---

## 1. The assumption this rests on, stated before anything is built

**The settings surface stays IN the page. It does not become a second
document.** `web/settings.html` as a separate URL is the obvious reading of
"one page" and it is wrong here: this page IS the satellite. It holds the
microphone, the wake detector, the socket and the playback worklet, and
navigating away tears all four down. Butler would stop listening while you
changed a setting, and come back through `getUserMedia` and a fresh
handshake when you left.

So "one page" means one FULL-SCREEN SURFACE over the live page, replacing a
760px drawer that showed one section at a time. The microphone stays live
behind it. If that reading is wrong, almost everything below changes, which
is why it is the first thing in the file.

---

## 2. What is being replaced, and what is deliberately not

`web/settings.js` is 1519 lines and most of it is not the problem. It builds
every control from the daemon's schema -- choice, segmented choice, bool,
number, text, list, the two-part model pair, the discovery picker with its
favourites star -- and each of those carries defects it was written against:
a bare `.changed` rule that collapsed a row into a six-pixel dot, a
connection change that never reached the model pickers, a favourites star
that committed a model id as a voice. **None of it changes.**

What changes is the SHELL around it:

| replaced | by |
|---|---|
| a 760px drawer inset top-right | a full-screen surface |
| a chain of boxes, one row of six | a vertical rail, the signal path drawn once |
| one section visible, eight hidden | every section on one scroll |
| nothing | a search box over all 38 settings |
| "after restart" on each row, into the void | a commit bar that counts them |
| nothing | one sentence under each heading, from the daemon |

`buildChain` goes. `buildGroup`, `buildRow`, `buildControl` and the eight
kind-specific factories stay, restyled from the outside.

---

## 3. The design

Taken from `web/mocks/settings.html`, which is the reference implementation
and should be read before building.

**The rail.** The signal path, vertical, always visible: one stop per
pipeline stage in the daemon's own order, each showing its label and the
current value of its `headline` setting. Below a divider, the groups that are
not stages -- permission mode, connections, Appearance. Clicking a stop
scrolls to its section; scrolling moves the marker. A stop whose section has
an edited setting carries a dot.

**The content.** Every section on one scroll, in rail order. Each has a
heading, one sentence under it from the daemon, and its rows. Row layout is
label and help on the left at real reading contrast, control on the right;
the setting KEY and its default are secondary and appear on hover or focus.
Each section keeps its own advanced disclosure -- a per-setting flag from the
schema, never inferred.

**Search.** Types filters rows by label, help text and key across every
section at once, hides sections with no match, and says when nothing
matched. `/` focuses it. This is the feature that makes 38 settings
navigable and it is why the one-section-at-a-time model had to go: a search
that can only see the open section is not a search.

**The commit bar.** Restart-flagged changes collect at the bottom and say so
once -- "2 settings need a restart" plus the button -- instead of every row
saying "after restart" to nobody.

**Hue still carries state.** The surface sits over a page that is still
listening, so it takes `--sa` like everything else: the rail's marker and the
commit bar tint with the session state. Settings open is not a state, and
must not look like one.

---

## 4. Mobile

The satellite runs on a phone and a tablet. This is a requirement, not a
polish pass.

- The rail becomes a horizontally scrolling strip pinned under the top bar,
  same stops, same values, same current marker.
- Rows stack: label and help above, control below, full width.
- Every control keeps a 44px minimum touch target -- the existing controls
  already do; the new ones must.
- `env(safe-area-inset-*)` on the surface's padding, and the commit bar sits
  above the bottom inset.
- **No horizontal overflow at 390px.** Two things caused it in the mock and
  both are in the reference: a flex text input needs `min-width: 0` or it
  refuses to shrink below its content, and a grid column needs
  `minmax(0, 1fr)` rather than `1fr` or a horizontally scrolling child
  stretches it.
- The search box stays reachable without scrolling; on a phone it may
  collapse to an icon that expands.

---

## 5. The daemon change

One new field. `Group` gains `blurb: str = ""` and `describe()` serves it.

The page may not write those sentences. A page that describes a seam is
naming one, which is what AC-41's grep gate forbids, and the mock prints
them from a field that does not exist yet -- that invention is the whole
reason this spec has a daemon half. `_validated_groups` should not require
it: a group with no blurb prints no sentence, exactly as a group with no
headline shows no value.

The page must tolerate its absence, so the web half can ship against a
daemon that does not have it yet.

---

## 6. Acceptance criteria

- **AC-83 (one surface, one scroll).** SUPERSEDED by AC-700
  (`docs/lantern-ui.md`). The settings surface fills the
  viewport over the live page, and every schema group plus the two
  hand-built blocks is reachable by scrolling. No section is hidden behind a
  tab. The microphone stays live and the socket stays open while it is open.
- **AC-84 (the rail is the signal path, and it tracks).** SUPERSEDED by
  AC-701: the strip on every Voice page. One stop per
  pipeline stage in the daemon's order, each showing its group's headline
  value; the non-stage groups sit below a divider. Clicking scrolls;
  scrolling moves the marker. The page still names no group, stage or
  setting.
- **AC-85 (search reaches every setting).** Holds, across categories
  (AC-702). Typing filters rows by label,
  help and key across all sections, hides sections with nothing left, and
  says so when nothing matches. Advanced disclosures open while a search is
  active, or a matching advanced setting would be found and then hidden.
- **AC-86 (restart-flagged changes are counted once).** Holds, in the bar
  docked at the sheet's foot (AC-703), amended by AC-715/AC-716 (`plan.md`):
  the list is the daemon's `pending`, and the bar has no button because the
  daemon restarts itself once idle. As first written: changing a setting
  whose `restart` flag is set raises a commit bar naming how many are
  waiting. Pressing its button restarts the daemon; the socket drops and the
  page reconnects on its own, which is exactly when those settings take
  effect.
- **AC-87 (a section says what it is, and the daemon says it).** Each
  heading carries one sentence served as the group's `blurb`. A group without
  one prints no sentence rather than an invented one.
- **AC-88 (it fits a phone).** SUPERSEDED by AC-705: the list, a page, Back. At 390px there is no horizontal overflow, the
  rail scrolls horizontally with the same stops, rows stack, every control
  keeps a 44px touch target, and the safe-area insets are honoured.
- **AC-89 (nothing in the shell names a setting).** The rebuild keeps AC-41
  intact: the rail, the search index and the commit bar are all built from
  the schema payload. The permission-mode and connections blocks remain the
  only carve-outs and may name only their own fixed fields.
- **AC-90 (the controls did not change).** Every control kind still renders
  and writes as it did: the segmented choice from `labels`, the dimmed but
  editable `inactive` row with its sentence undimmed, the two-part model
  pair, the favourites star only where the schema opts in, the single-key
  write with its error landing on the offending row.

---

## 7. What this retires, which is the owner's call

These are MET in `plan.md` and this design deletes what they describe. They
should be marked SUPERSEDED with a pointer here rather than quietly falsified.

- **AC-55 (the drawer opens on a chain the daemon draws).** The chain of
  boxes becomes the vertical rail. What the criterion was protecting -- that
  the daemon supplies the order and the page invents none of it -- survives
  in AC-84.
- **AC-57 (one section at a time).** Directly contradicted: every section is
  now visible on one scroll. This is the criterion search could not live
  with.
- **AC-58 (the chain fits a phone).** The measured 760px drawer width and the
  wrap-and-drop-the-connector rule go with the chain. Replaced by AC-88.

**AC-56 is AMENDED, not retired.** Its box goes, but its rule -- that a
headline VALUE changing must repaint without rebuilding the rows -- is
exactly as load-bearing on the rail, and for the same reason: a rebuild
mid-edit destroys the control under the cursor.

**AC-45 and AC-66 survive untouched.** Settings are still organised by seam
in pipeline order, and an inert setting is still dimmed, never disabled, with
its sentence at full contrast.

---

## 8. Build order

1. **The daemon's `blurb`.** Field, `describe()`, a test that every group
   serves the key. Independent of the web half and shippable alone.
2. **The shell.** `index.html`'s surface, rail, search and commit bar markup
   and CSS; `settings.js`'s `buildChain` replaced by `buildRail`, the
   one-section switching removed, search and the commit bar added. Every
   control factory untouched.
3. **Mobile.** The 390px pass, with the two overflow traps checked
   explicitly.
4. **DOX.** `web/AGENTS.md`'s account of the drawer is rewritten; it
   currently describes the chain, the one-open-section rule and the measured
   width in detail, and all three become false.

---

## 9. Verification

```
node --check web/settings.js
node --check web/app.js
.venv\Scripts\python.exe -X utf8 -m pytest test\test_settings_api.py test\test_seams.py test\test_http_surface.py -q
LC_ALL=C grep -c '[^ -~]' web/settings.js web/index.html   # both 0
```

And a real browser against a SCRATCH daemon, never port 8140:
`probe_page.py https://127.0.0.1:<port>/ --live`, then screenshots at 1440px
and at 390px. Reading the markup is not evidence: an ES module that throws
takes the whole page with it, so a broken import looks like missing controls
rather than an error.

**We will know this is done when:** the surface opens full-screen with the
microphone still live, all seven groups plus both hand-built blocks are on
one scroll with the rail tracking, typing in search filters across sections,
a restart-flagged change raises the commit bar, the daemon serves a `blurb`
the page prints, and there is no horizontal overflow at 390px.

---

## 10. What this plan may already have wrong

- **The full-screen surface may want to be a sheet on a phone.** Full screen
  is right on a desktop. On a 390px device the drawer already becomes a
  bottom sheet at 88dvh, and users expect to dismiss that by dragging. This
  spec keeps one geometry everywhere; if that reads badly on the phone, the
  fix is a sheet, not a smaller full screen.
- **Scroll-spy and click-to-scroll fight each other.** Clicking a stop
  scrolls, which moves the marker through every section on the way. The
  reference implementation uses an IntersectionObserver with a
  `-70%` bottom margin; if it flickers during a programmatic scroll, the
  answer is to suppress the observer until the scroll settles.
- **Search plus `inactive` plus advanced is three ways for a row to be
  hidden**, and they can disagree. A row that is filtered out, dimmed and
  behind a disclosure has three reasons not to be seen and the owner will
  only be told one.
- **The commit bar could lie by omission** until 2026-10-27: it counted only
  what THIS page changed. Closed by AC-716 (`plan.md`): the list is the
  daemon's `pending`, sent on every `settings` frame.
