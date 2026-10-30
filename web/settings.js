// Butler satellite -- the settings panel (plan.md 4.6, AC-8).
//
// Every control on this page is drawn from GET /api/settings. Nothing about a
// setting is written down here: not its label, not its range, not even that it
// exists. The daemon's schema is the only list, so adding a setting to
// daemon/settings.py makes it appear here and removing it makes it vanish. A
// form that keeps its own copy of the field list drifts from the daemon the
// first time someone adds one, and the drift is invisible -- the new setting
// simply does nothing, and nothing says so.
//
// The other rule this file exists to keep: NO BROWSER STORAGE OF ANY KIND.
// Values live on the daemon and arrive over HTTP and the WebSocket. An earlier prototype
// ended up with one laptop holding four diverging per-origin copies of a voice
// setting because the app answered on four origins (D-2). A test greps every
// shipped client file for the storage APIs and fails the build, so the ban is
// enforced rather than promised -- which is also why this comment does not
// spell the names out.

const SETTINGS_URL = "/api/settings";
const RESET_URL = "/api/settings/reset";
const MODE_URL = "/api/mode";
const MODELS_URL = "/api/models";

// AC-109. Which DISCOVERED lists can be auditioned, and the route that plays
// one item of each.
//
// The key is the daemon's own `choices_from` target -- never a setting key,
// never a group, never a label. That is the one thing in the payload that says
// what a control's values ARE rather than which setting is holding them, and
// audition is a property of the list, not of the setting: a second setting
// pointed at the same target gets the button with no change here, and a
// setting renamed, moved between groups or relabelled changes nothing.
//
// It is opt-in for the same reason the favourites star is (web/AGENTS.md): a
// preview button rendered under every `choices_from` control would offer to
// "listen to" a router model, and the request would come back 400 from a
// control that had no business showing it.
//
// The honest cost, stated rather than hidden: `tts_voices` is a daemon-owned
// identifier written into this directory, so renaming that target on both
// sides leaves the picker working and the button silently gone. The right fix
// is the daemon flagging an auditionable control the way it flags `favourites`
// and `labels`; it does not serve such a field today, and this file may not
// invent one (AC-41).
// Reverberation tail after a preview, before the microphone is trusted
// again (AC-109). Same reason `playCue` has one: the room keeps the sound
// for a while after the speaker stops.
const AUDITION_TAIL_MS = 320;

const AUDITION = new Map([
  ["tts_voices", "/api/tts/preview?voice="],
]);

// Permission modes are NOT part of the settings schema and must not be: they
// gate what Butler is allowed to DO, they are asymmetric, and they fail closed
// (daemon/modes.py). The order here mirrors daemon/modes.py ORDER -- least to
// most permissive -- because the index IS the comparison that decides whether
// a click is a lowering or a raise.
const MODE_ORDER = ["paranoid", "secretary", "trusted"];
// How often a pairing block re-reads while its document says `poll` (a code
// on screen, or a service starting). docs/messaging.md MS-5: every 2 s, and
// only then.
const PAIRING_POLL_MS = 2000;
// The most values a LABELLED choice lays out as a row of buttons. Past it a
// row is a worse dropdown, so a longer labelled choice is a select that shows
// the labels (seven reading faces, "Style default" first, would otherwise be
// seven buttons). A count, never a setting name (AC-41); pinned by
// test_seams.py beside the reading-face gate.
const BUTTONS_MAX = 4;
// A row of labelled buttons sits BESIDE its row's words while its labels are
// short; past this many characters together it moves UNDER them, where it has
// the card's full width (docs/lantern-ui.md 5.1). A count, never a name.
const SEG_INLINE_MAX = 24;

// --------------------------------------------------------------------------- //
// Small helpers
// --------------------------------------------------------------------------- //
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function ghostButton(text, title) {
  const b = el("button", "ghost", text);
  b.type = "button";
  if (title) b.setAttribute("data-help", title);
  return b;
}

function same(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }
  return a === b;
}

// A deep compare, for the one question `same` cannot answer: has this setting
// moved since the page first read it? That has to hold for the {conn, model}
// pair as well as for scalars and lists, and `same` is shallow on purpose --
// it is the control-level comparison and every control it serves holds a
// scalar or an array.
function sameJson(a, b) {
  return JSON.stringify(a === undefined ? null : a)
      === JSON.stringify(b === undefined ? null : b);
}

// How a value reads in prose, for the "default ..." line under each control.
function human(v) {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(empty)";
  if (v && typeof v === "object" && "model" in v) {
    // A {conn, model} pair (AC-30). Empty means the engine's own default --
    // the same words the control's placeholder uses -- and `String(pair)` is
    // the "default [object Object]" a UI review caught under the two model
    // controls, which is exactly the tell this page must not ship.
    if (!v.conn && !v.model) return "(engine default)";
    return (v.model || "(default model)") + (v.conn ? " on " + v.conn : "");
  }
  if (typeof v === "boolean") return v ? "on" : "off";
  // "(blank)" read as a broken sidebar row ("Speech (blank)") rather than as
  // a value (item 8b, UI review 2026-09-26).
  if (v === "") return "none";
  return String(v);
}

function decimals(n) {
  if (!Number.isFinite(n)) return 0;
  const s = String(n);
  const dot = s.indexOf(".");
  return dot < 0 ? 0 : s.length - dot - 1;
}

// Slider granularity, derived from the schema rather than chosen per setting,
// so a new number appears with a usable slider and no page change.
//
// The one hard requirement is that the DEFAULT stays reachable by dragging:
// a slider that cannot return to the value the daemon shipped is a trap. Every
// step below divides the distance from `min` to the default of every number in
// the current schema exactly.
function stepFor(s) {
  const whole = Number.isInteger(s.default) && Number.isInteger(s.min)
    && Number.isInteger(s.max);
  if (whole) {
    const range = s.max - s.min;
    if (range > 20000) return 100;
    if (range > 2000) return 50;
    if (range > 200) return 10;
    return 1;
  }
  const d = Math.max(decimals(s.default), decimals(s.min), decimals(s.max), 2);
  return Math.pow(10, -Math.min(d, 3));
}

function roundToStep(v, step) {
  return Number(v.toFixed(Math.min(decimals(step), 6)));
}

// Units are the only thing inferred from a key name, and only to LABEL a
// number the user is already being shown. Guessing wrong costs a wrong suffix,
// never a wrong value.
function unitFor(key) {
  return /_ms$/.test(key) ? "ms" : "";
}

// An icon, drawn the way the shell draws its own (`svg.i`, a 24-unit box,
// strokes in currentColor). Decorative: the control beside it has the words.
const SVG_NS = "http://www.w3.org/2000/svg";
function icon(d, small) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", small ? "i s" : "i");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", d);
  svg.append(path);
  return svg;
}
const ICON = {
  chevDown: "m6 9 6 6 6-6",
  chevRight: "m9 6 6 6-6 6",
  check: "M5 12.5 10 17 19 7",
  power: "M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
  star: "m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9Z",
};

// --------------------------------------------------------------------------- //
// Mount
// --------------------------------------------------------------------------- //
//   root        element the schema-driven category pages are rendered into
//   navRoot     the category list beside them; see buildNav
//   sheet       the sheet itself: carries `searching` while a query runs and
//               `data-phone` ("list" | "page", the phone's back stack, AC-705)
//   back        the phone's "Settings" back button on a category page
//   scroller    the element a category page scrolls INSIDE, put back to the
//               top whenever another page is shown
//   modeRoot    the (static) permission mode block; see index.html
//   connRoot    the (static) connections block; see index.html
//               Both carry `data-section`: the served section their markup
//               says they belong under. The page names its own carve-outs
//               and nothing else (AC-41), so the word is theirs, not here.
//   search      the search input; searchClear its clear button
//   commitRoot  the restart bar: what the daemon says it has not applied
//   showError   the page's visible error log -- a phone has no console (D-4)
//   onValues    called with the full value set whenever it is known, so the
//               page can honour the settings that are about the PAGE (the cues)
//   menuControls  [{key, host}]: ONE setting's control drawn outside this
//               surface (the account menu's Appearance row). A labelled choice
//               is drawn as plain named buttons; the key is the CALLER's, the
//               choices and words are the daemon's (AC-41)
//   escapeStops says whether Escape would STOP A REPLY right now. The page
//               owns that fact; a popover here closes on the key either way
//               and only swallows it when the answer is no (AC-112)
//   pickRoot    the popover in the composer's row that carries the settings
//               the daemon marked `quick` INDIVIDUALLY -- the models answering
//               and the agent's tool posture -- under `pickTitle`; pickWho and
//               pickLabel are the two chips that open it: the short word of the
//               setting the daemon says is ANSWERING (`answers`, else the first
//               served) and that setting's value (this file names none);
//               onOpenSettings raises the full surface from it, so "more" is a
//               real door rather than a dead word
export function mountSettings({ root, navRoot, sheet, back, scroller,
                               modeRoot, connRoot,
                               search, searchClear, commitRoot, showError,
                               onValues, onMode, onAudition,
                               menuControls,
                               pickRoot, pickTitle, pickWho, pickLabel,
                               onOpenSettings,
                               escapeStops }) {
  const rows = new Map();          // key -> row controller
  let layout = "";                 // structural fingerprint; changes -> rebuild
  const editing = new Set();       // keys whose control has focus right now
  const deferred = new Map();      // remote values held back while editing
  let serverMode = "";
  let pendingRaise = null;
  // The category pages (AC-700). `pages` maps a page key -- a group's key, or
  // a carve-out block's id -- to {key, label, stage, section, node, nav,
  // read}; `stops` holds the signal-path strip's stops (AC-701). Both are
  // rebuilt with the controls and never outlive them. `current` is the page
  // on screen -- view state, in a variable, never in browser storage and never
  // sent to the daemon: which page you were reading is not a preference (AC-8).
  const pages = new Map();
  const stops = new Map();
  let current = "";
  // ONE strip, MOVED onto whichever staged page is shown, never copied: the
  // same rule `#wave` lives under (web/AGENTS.md).
  let strip = null;
  // The search results' heading and the "nothing matched" line (AC-702).
  let results = null;
  let resultsLine = null;
  let noMatch = null;
  let query = "";
  // Each preview card's miniature wears the page's theme, mirrored from
  // `<html>` by one observer that a rebuild replaces.
  let themeWatch = null;
  // Which groups the daemon says do nothing in the current configuration.
  // Held here rather than read off the stops, because the stops are built once
  // and repainted from four paths (AC-56) while this fact only arrives with a
  // whole schema answer.
  let inertGroups = new Set();
  let connCount = null;
  // The last values the daemon sent. Read by the model pickers, which have
  // to show the favourites list -- a setting they do not own.
  let latest = {};
  // The controls drawn OUTSIDE this surface: the account menu's and the
  // composer's. Same specs, same single-key write; a second map because they
  // are a second element per setting and `rows` holds the panel's row
  // controller, which owns the busy, error and saved treatments a menu has no
  // room for. A refusal still lands on the panel row and in the page's error
  // log, so it is never silent.
  const quickRows = new Map();
  // Which picker popover under the composer is open, if any. One at a time,
  // and closed by a press anywhere else -- the same arrangement the shell's
  // popovers use, and for the same reason it takes no case in the page's global
  // Escape handler: that key's last case STOPS a reply (AC-112) and must not
  // become conditional on whether a popover happens to be open. Escape closes
  // one only while the focus is inside it, through a capture-phase listener
  // the picker installs on itself.
  let openPick = null;
  // The picker whose value is printed on the control that opens the popover:
  // the one the daemon says is ANSWERING, else the FIRST one it served
  // (`firstPick`, see `buildPicks`).
  let headlinePick = null;
  let firstPick = null;
  // The keys the RUNNING daemon has not applied yet, as the daemon says
  // (`pending`, AC-716): what the restart bar counts (AC-86, AC-703). The
  // daemon knows what it booted with; a baseline kept here missed every
  // change made before this page loaded. A value put back leaves the list.
  let pending = [];
  // The live pairing blocks' re-read functions (`buildPairing`). A setting
  // change can be the one that starts or stops what a block links, and the
  // page may not know which setting that is (AC-41), so every change re-reads
  // every block -- one GET each. Cleared with the controls on a rebuild.
  const pairings = new Set();

  const note = (msg) => { if (showError) showError(msg); };

  // Registered once, at mount, and not per build: a listener added beside each
  // control would accumulate one copy per rebuild, all of them pointing at
  // detached nodes. `pointerdown` and not `click`, so a control under an open
  // popover does not need two presses.
  document.addEventListener("pointerdown", (e) => {
    if (openPick && !openPick.wrap.contains(e.target)) openPick.close();
  });

  // -- talking to the daemon ------------------------------------------------ //
  async function call(url, body) {
    const init = { method: "POST", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) init.body = JSON.stringify(body);
    const r = await fetch(url, init);
    let parsed = null;
    try { parsed = await r.json(); } catch { parsed = null; }
    return { ok: r.ok, status: r.status, body: parsed };
  }

  async function refresh() {
    try {
      const r = await fetch(SETTINGS_URL);
      if (!r.ok) throw new Error("HTTP " + r.status);
      apply(await r.json());
    } catch (e) {
      note("settings unavailable: " + (e.message || e));
    }
  }

  // One control at a time, so a refusal names exactly one offender. The daemon
  // validates the whole request before writing anything, so a multi-field form
  // that fails tells you only that SOMETHING was wrong; here the 400 lands on
  // the control that caused it.
  async function commit(key, value) {
    const row = rows.get(key);
    if (!row) return;
    row.clearError();
    row.busy(true);
    try {
      const r = await call(SETTINGS_URL, { [key]: value });
      if (!r.ok) {
        const msg = (r.body && r.body.error) || ("daemon refused with HTTP " + r.status);
        row.setError(msg);
        note("settings: " + msg);
        // Nothing was applied -- the write is all-or-nothing -- so the daemon
        // still holds what we last saw. Put that back at once, then re-read to
        // be certain: the form must never sit showing a value the daemon
        // rejected, because the next person to look at it will believe it.
        row.write(row.spec.value);
        await refresh();
        return;
      }
      apply(r.body, key);
      if (row.spec.restart) {
        row.setNote("saved -- Butler restarts by itself to apply this as "
                    + "soon as nothing is running");
      } else {
        // Completion is one of the four kinds of feedback a UI owes the
        // user. A silent success is indistinguishable from a control that
        // did nothing, and this panel is full of settings whose effect you
        // cannot see until the next time you speak.
        row.flashSaved();
      }
    } catch (e) {
      row.setError("could not reach the daemon: " + (e.message || e));
      row.write(row.spec.value);
    } finally {
      row.busy(false);
    }
  }

  async function reset(group) {
    const url = group ? RESET_URL + "?group=" + encodeURIComponent(group) : RESET_URL;
    try {
      const r = await call(url);
      if (!r.ok) {
        const msg = (r.body && r.body.error) || ("HTTP " + r.status);
        note("reset refused: " + msg);
        return;
      }
      apply(r.body);
    } catch (e) {
      note("reset failed: " + (e.message || e));
    }
  }

  // -- applying what the daemon says ---------------------------------------- //
  function apply(next, forceKey) {
    if (!next || !Array.isArray(next.settings)) return;
    // Before the rows are written, not after: a row's write() may read this
    // (the model pickers paint the favourites from it), and painting from
    // the PREVIOUS answer is how a list stays one change behind forever.
    latest = {};
    for (const s of next.settings) latest[s.key] = s.value;
    // Before build() too, because buildNav paints as soon as it has made the
    // items and an item built bright and dimmed a frame later flickers.
    inertGroups = new Set((next.groups || [])
      .filter((g) => g.inactive).map((g) => g.key));
    pending = next.pending || [];
    // The fingerprint covers a group's stage, headline and section as well as
    // its key, because the nav's and the strip's SHAPE is built from those: a
    // group that gains a stage joins the strip, one that changes section moves
    // in the nav. What it deliberately does not cover is any VALUE --
    // rebuilding when a value changes is the defect the guard exists to stop,
    // and the nav's and strip's own text is repainted instead (AC-56).
    // `quick` is in the per-SETTING half as well as the per-group half: it
    // decides whether a control exists under the composer and what word it
    // carries, so a flag that moved without a rebuild would leave the row
    // showing yesterday's set.
    const sig = next.settings
      .map((s) => [s.key, s.kind, s.group, s.quick || ""].join(":"))
      .join("|") + "//" + (next.groups || [])
      .map((g) => [g.key, g.stage, g.headline, g.quick, g.section].join(":"))
      .join("|") + "//" + (next.sections || []).map((x) => x.key).join("|");
    if (sig !== layout) { build(next); layout = sig; }
    for (const spec of next.settings) {
      const row = rows.get(spec.key);
      if (!row) continue;
      row.spec = spec;
      row.showInactive();
      row.showProblem();
      if (spec.key === forceKey) {
        // The key the user just committed. Written unconditionally, even
        // though the control probably still has focus: this is their own
        // change coming back, and it is the daemon's normalized version of it
        // (a trimmed string, a lowercased phrase), which is the only value
        // this control is allowed to be showing afterwards.
        deferred.delete(spec.key);
        row.write(spec.value);
        row.mark(spec.changed);
        row.clearNote();
      } else {
        put(spec.key, spec.value, spec.changed);
      }
    }
    for (const row of rows.values()) row.repaint();
    // The pill's controls take the daemon's value unconditionally. They hold
    // no busy or deferred state of their own, and the one control the owner
    // has their hands on is a button they just pressed -- whose answer this
    // is.
    for (const [key, ctl] of quickRows) {
      if (key in latest) ctl.write(latest[key]);
    }
    // The pickers dim when the current brain mode leaves their setting
    // deciding nothing -- in `harness_only` the conversation model is inert,
    // and a live-looking control against the text box that changes nothing is
    // AC-7b at the closest possible range (AC-66). A menu control's
    // controls have no such state and do not carry this.
    for (const spec of next.settings) {
      const ctl = quickRows.get(spec.key);
      if (ctl && ctl.showInactive) ctl.showInactive(spec.inactive || "");
    }
    // WHAT IS ANSWERING is the daemon's to say, per brain mode (`answers`,
    // review 2026-09-26): in `harness_only` the first quick setting is the
    // conversation model, which decides nothing there. Re-read on EVERY full
    // read and not only at build, because the mode changes across a restart
    // that leaves the schema -- and so the build -- untouched. `null` means
    // NO setting answers (the harness modes: each chat's own chip does,
    // docs/chat-model.md CM-6), so the pill names none; an older daemon that
    // sends no `answers` at all keeps the first quick setting served.
    const answering = quickRows.get(next.answers);
    headlinePick = answering && answering.headline ? answering
      : (next.answers === null ? null : firstPick);
    paintPickLabel();
    paintNav();
    paintCommit();
    for (const reread of pairings) reread();
    if (onValues) {
      const values = latest;
      onValues(values);
    }
  }

  // A single value, from wherever it came. Never clobbers a control the user
  // has their hands on: that value is held until they let go, because
  // overwriting a half-typed phrase from a broadcast is how a settings page
  // earns a reputation for fighting back.
  function put(key, value, changed) {
    // Before every early return below. This is the path a BROADCAST takes, so
    // it is how the phone's appearance change reaches the desk's pill -- and
    // the guards under it are about a control the owner is typing into, which
    // the pill does not have.
    const quick = quickRows.get(key);
    if (quick) quick.write(value);
    const row = rows.get(key);
    if (!row) return;
    row.spec = Object.assign({}, row.spec, { value });
    const isChanged = changed == null ? !same(value, row.spec.default) : changed;
    // Already on screen. This is the ordinary case for a change made HERE: the
    // POST answers, the control is written, and the daemon's broadcast of the
    // same change arrives a moment later. Treating that as a remote edit would
    // put a "somebody else changed this" warning under every control the user
    // touches, which is worse than useless -- it is wrong.
    if (same(row.shown, value)) { row.mark(isChanged); deferred.delete(key); return; }
    if (editing.has(key)) {
      deferred.set(key, { value, changed: isChanged });
      row.setNote("changed on the daemon while you were editing; "
                   + "it will update when you finish");
      return;
    }
    row.write(value);
    row.mark(isChanged);
  }

  function flush(key) {
    const held = deferred.get(key);
    if (!held) return;
    deferred.delete(key);
    const row = rows.get(key);
    if (!row) return;
    row.write(held.value);
    row.mark(held.changed);
    row.setNote("updated from the daemon");
  }

  // The `settings` broadcast carries only what changed, and the `turn_mode`
  // broadcast carries one key. Both land here, so a change made on the phone
  // shows up on the tablet without a reload -- which is the entire reason the
  // values live on the daemon (D-2, AC-8).
  function applyChanged(changed, servedPending) {
    if (!changed) return;
    // The `settings` frame carries the daemon's `pending` beside what changed
    // (AC-716); a `turn_mode` change carries none and leaves it alone.
    if (Array.isArray(servedPending)) pending = servedPending;
    // `latest` FIRST, and it is not bookkeeping. Every model picker paints the
    // favourites out of it -- the panel's rows through `repaint`, the
    // composer's pickers when one is opened -- and this path used to leave it
    // holding whatever the last FULL read said. So starring a model on the
    // phone left the desk offering the old list until something else forced a
    // refresh: one change behind, on whichever device did not make the change,
    // which is the failure the server-side values exist to prevent (D-2).
    // Found by the composer picker, true of the panel since favourites shipped.
    Object.assign(latest, changed);
    for (const key of Object.keys(changed)) put(key, changed[key], null);
    for (const row of rows.values()) row.repaint();
    paintNav();
    paintCommit();
    for (const reread of pairings) reread();
    if (onValues) onValues(changed);
  }

  // -- building the controls ------------------------------------------------ //
  function build(next) {
    rows.clear();
    editing.clear();
    deferred.clear();
    // AC-109. The row about to be discarded may be auditioning, and its
    // `listen` button is the only thing that can stop it. Torn down here,
    // while its teardown is still reachable: an Audio element goes on playing
    // after its DOM row is gone, so a rebuild mid-preview would leave the room
    // hearing a voice with no control anywhere on the page able to stop it,
    // and its object URL never revoked. Not an overlap -- the next preview
    // would still stop it, because `audition` outlives the rebuild -- which is
    // exactly why it would be looked for in the wrong place.
    stopAudition();
    // The pair controls about to be discarded registered repopulate
    // callbacks; keeping them would leave this list growing and pointing at
    // detached nodes. Harmless today -- build() only re-runs when the schema
    // layout changes, and the schema is static per process -- but the cost
    // of the guard is one line.
    connectionConsumers.length = 0;
    pairings.clear();
    pages.clear();
    if (themeWatch) { themeWatch.disconnect(); themeWatch = null; }
    root.textContent = "";

    const groups = (next.groups || []).slice();
    const seen = new Set(groups.map((g) => g.key));
    for (const s of next.settings) {
      // A setting in a group the daemon did not describe still gets shown.
      if (!seen.has(s.group)) { seen.add(s.group); groups.push({ key: s.group, label: s.group }); }
    }

    // ONE PAGE PER GROUP (AC-700), each built once and shown one at a time.
    // Every engine seam is one page and its advanced options are INSIDE it
    // rather than in a drawer somewhere else -- splitting one seam across two
    // places is what AC-45 replaced. The page does not re-sort anything: the
    // nav files each page under the section the daemon names, in the order
    // the daemon serves both.
    const made = [];
    for (const g of groups) {
      const items = next.settings.filter((s) => s.group === g.key);
      if (!items.length && !g.pairing && !g.collection) continue;
      made.push({ key: g.key, label: g.label || g.key,
                  stage: g.stage == null ? null : g.stage,
                  section: g.section || "", node: buildGroup(g, items),
                  read: () => headline(g.headline) });
    }
    // The two blocks that have no schema group and never will: one gates what
    // Butler may DO and the other has a shape the schema cannot model
    // (daemon/modes.py, plan.md 4.4b). They are pages of this sheet like any
    // other -- MOVED here from where index.html parked them. Each one's
    // markup says which served section it belongs under (`data-section`),
    // its title is the heading the markup already holds, and its nav line is
    // the fact this page already has: the current mode, and how many
    // connections are configured. Neither is a setting, so neither can come
    // from a group's `headline`. A block whose markup carries a `data-tag`
    // (the mode block: it gates nothing yet) shows that tag on its nav row,
    // so the row says so before anyone opens it.
    if (modeRoot) {
      made.push({ key: modeRoot.id, label: blockTitle(modeRoot), stage: null,
                  section: modeRoot.dataset.section || "", node: modeRoot,
                  tag: modeRoot.dataset.tag || "", read: () => serverMode });
    }
    if (connRoot) {
      made.push({ key: connRoot.id, label: blockTitle(connRoot), stage: null,
                  section: connRoot.dataset.section || "", node: connRoot,
                  tag: connRoot.dataset.tag || "",
                  read: () => (connCount == null ? ""
                    : connCount + (connCount === 1 ? " connection" : " connections")) });
    }

    // The search results' own heading, then every page in NAV order: while a
    // query runs, the pages with matches are shown together and read top to
    // bottom in the order the nav lists them (AC-702).
    results = el("div", "sresults");
    results.hidden = true;
    resultsLine = el("p", "setgroup-blurb");
    results.append(el("h2", "label", "Results"), resultsLine);
    // Said once, where the results would be: a search that matches nothing
    // must say so rather than leave a blank page to be interpreted (AC-85).
    noMatch = el("p", "nomatch");
    noMatch.hidden = true;
    root.append(results, noMatch);
    for (const p of buildNav(next.sections || [], made)) {
      p.node.querySelectorAll(":scope > .rhead").forEach((n) => n.remove());
      p.node.prepend(resultHead(p));
      if (!p.node.id) p.node.id = "sec-" + p.key;
      p.node.dataset.page = p.key;
      pages.set(p.key, p);
      root.append(p.node);
    }
    buildStrip([...pages.values()]);
    paintNav();
    // The rows the query was filtering are gone, so the filter is re-run over
    // the ones that replaced them. Without this a schema change while a search
    // is open leaves every new row visible under a box that still reads as if
    // it were filtering.
    applySearch(query);
    showPage(pages.has(current) ? current : (pages.keys().next().value || ""));
    buildQuick(next);
  }

  // A carve-out block's title is the heading its markup already holds.
  function blockTitle(node) {
    const t = node.querySelector(".mode-title");
    return (t && t.textContent) || node.id;
  }

  // The line a page starts with in the search results: its stage number, its
  // name, and "Open", which leaves the search for that page.
  function resultHead(p) {
    const b = el("button", "rhead");
    b.type = "button";
    if (p.stage != null) b.append(el("span", "nnum", String(p.stage)));
    b.append(el("span", "rname", p.label), el("span", "go", "Open"));
    b.lastChild.append(icon(ICON.chevRight, true));
    b.onclick = () => openPage(p.key);
    return b;
  }

  /**
   * The settings reachable WITHOUT opening this surface: the account menu's
   * Appearance row (`menuControls`, docs/lantern-ui.md section 2) and the
   * composer's pickers (`buildPicks`). One map, one clear: they are the same
   * idea drawn twice. Rebuilt only with the panel, under the same fingerprint
   * guard, because a control rebuilt on a value change is destroyed under
   * the cursor that is pressing it (AC-56).
   */
  function buildQuick(next) {
    quickRows.clear();
    buildMenuControls(next);
    buildPicks(next);
  }

  // ONE setting per host, named by the CALLER, drawn from the daemon's spec.
  // A labelled choice is plain named buttons with no default mark -- a menu
  // row has no room for one -- and anything else is the panel's own control.
  // A key the daemon does not serve draws nothing rather than a guess.
  function buildMenuControls(next) {
    for (const { key, host } of menuControls || []) {
      if (!host) continue;
      host.textContent = "";
      const spec = next.settings.find((x) => x.key === key);
      if (!spec) continue;
      const labelled = spec.kind === "choice"
        && Object.keys(spec.labels || {}).length > 0;
      quickRows.set(key, labelled
        ? buildChoiceButtons(spec, host, spec.labels, false)
        : buildControl(spec, host, "menu-"));
    }
  }

  /**
   * The row under the chat input: what is answering, and what it may do.
   *
   * The owner's backlog item 5 (2026-09-11) -- the brain, the coding agent, the
   * model that looks at images, and read_only versus full -- chosen where the
   * work happens rather than three disclosures into the settings surface.
   *
   * Which settings those are is the DAEMON's answer, exactly as the appearance
   * pill's are: every setting carrying a non-empty `quick`, in the order it was
   * served, with the short word it carries printed on the control. A list of
   * keys here would be the second copy that drifts, and the drifted copy is the
   * one that silently omits the setting added last (AC-41).
   *
   * Same single-key write as everywhere else, so a refusal lands on the panel
   * row with the daemon's own message and in the page's error log.
   */
  function buildPicks(next) {
    if (!pickRoot) return;
    pickRoot.textContent = "";
    if (pickTitle) pickRoot.append(el("p", "pop-title", pickTitle));
    openPick = null;
    firstPick = null;
    let shown = 0;
    for (const spec of next.settings) {
      if (!spec.quick) continue;
      shown += 1;
      // `pick-` and not `set-` or `look-`: this setting has a control in the
      // panel too, and one id on two elements sends every panel label click to
      // whichever copy the browser finds first.
      const ctl = buildPicker(spec, pickRoot, "pick-");
      quickRows.set(spec.key, ctl);
      // THE WORD ON THE HEADER'S CONTROL is the setting the daemon says is
      // ANSWERING (`next.answers`, chosen where the values are written), and
      // the first quick setting served where it names none -- printed with
      // that setting's own short word and its own value. Which one, and what
      // it is called, are the daemon's (AC-41): this file neither picks a key
      // nor shortens a label.
      if (!firstPick && ctl.headline) firstPick = ctl;
    }
    headlinePick = firstPick;
    // The control that opens this is hidden outright on a daemon that marks
    // nothing quick, rather than opening onto an empty box.
    if (pickRoot.parentElement) pickRoot.parentElement.hidden = shown === 0;
    paintPickLabel();
  }

  // Repainted from every path a value can arrive on, which is the rule the
  // settings rail already lives under (AC-56): the pickers are built once and
  // written many times.
  function paintPickLabel() {
    if (pickWho) {
      pickWho.textContent = headlinePick ? headlinePick.who() : "";
      // Nothing answers by setting (`answers: null`, the harness modes): the
      // "who answers" chip has nothing to say, so it goes (docs/chat-model.md
      // CM-6); the chat chip says what answers this chat.
      const who = pickWho.closest("button");
      if (who) who.hidden = !headlinePick;
    }
    if (pickLabel) {
      pickLabel.textContent = headlinePick ? headlinePick.value() : "models";
    }
  }

  /**
   * ONE control in that row.
   *
   * Two shapes, and which one is the daemon's to say. A choice it gave LABELS
   * to is already a row of named buttons in the panel and that is the right
   * shape here too (AC-66) -- the tool posture is two words, not a dropdown of
   * identifiers. Everything else is a pill carrying the value, opening on the
   * favourites.
   *
   * FAVOURITES AND NOT THE WHOLE LIST, which is the owner's ruling of
   * 2026-09-11 and is also the only honest option: the panel's own picker is a
   * connection select plus a text box plus a datalist fetched from that
   * endpoint, and three of those stacked against the composer is the settings
   * page again. What this cannot express is a model that is not a favourite,
   * which is what the door at the bottom is for.
   */
  function buildPicker(spec, host, prefix) {
    if (spec.kind === "choice" && Object.keys(spec.labels || {}).length) {
      const seg = el("div", "pickseg");
      // The row has no <label> to point at this, so the group says what it is
      // on its own -- and it says the setting's FULL label, because "Tools"
      // alone is not something anyone can act on out of context (AC-100).
      seg.setAttribute("role", "group");
      seg.setAttribute("aria-label", spec.label || spec.key);
      host.append(seg);
      const inner = buildChoiceButtons(spec, seg, spec.labels, false);
      return {
        write: inner.write,
        // KEPT past the u-railB1 rule (item 2): the buttons in this group
        // are labelled and need no tooltip while they are LIVE, but a
        // dimmed group has no visible text saying WHY, and the quick row
        // this control lives in has no room for the settings surface's own
        // `set-inactive` sentence.
        showInactive(why) {
          seg.classList.toggle("inactive", !!why);
          if (why) seg.setAttribute("data-help", why);
          else seg.removeAttribute("data-help");
        },
      };
    }

    const wrap = el("div", "pick");
    const btn = el("button", "pickbtn");
    btn.type = "button";
    btn.id = prefix + spec.key;
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");
    btn.append(el("span", "pickname", spec.quick),
               el("span", "pickval"));
    const val = btn.lastChild;
    const pop = el("div", "pickpop");
    pop.hidden = true;
    wrap.append(btn, pop);
    host.append(wrap);

    let current = null;
    let inactive = "";

    // A `pair` is {conn, model} and the connection is not what is being chosen
    // here; a `text` setting is the model id itself. Blank is a REAL value
    // meaning "whatever this connection or harness defaults to", so it is
    // printed as such -- "nothing is set" and "set to the default" are
    // different statements and only one of them is true.
    function modelOf(v) {
      if (v && typeof v === "object") return v.model || "";
      return v == null ? "" : String(v);
    }
    function shownValue() {
      const model = modelOf(current);
      if (!model) return "default";
      const cut = model.lastIndexOf("/");
      return cut >= 0 ? model.slice(cut + 1) : model;
    }

    function setOpen(open) {
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      if (open) { fill(); openPick = { wrap, close: () => setOpen(false) }; }
      else if (openPick && openPick.wrap === wrap) openPick = null;
    }

    function choose(model) {
      setOpen(false);
      btn.focus();
      // A favourite is a bare model id, so a `pair` keeps the connection it is
      // already on. That is the panel's own rule for the same click: its
      // favourites row fills the model box and leaves the connection select
      // alone, because a favourite says which model and never which endpoint.
      commit(spec.key, (current && typeof current === "object")
        ? { conn: current.conn || "", model } : model);
    }

    function option(model, text) {
      const b = el("button", "pickopt", text);
      b.type = "button";
      // NO TOOLTIP (u-railB1 item 2): the popover has room the pill does not,
      // so an id that does not fit WRAPS here rather than being cut, and the
      // button's own text is already the full model id -- a tooltip would
      // say the identical word a second time.
      b.setAttribute("aria-pressed", String(model === modelOf(current)));
      b.onclick = () => choose(model);
      pop.append(b);
    }

    // Filled on OPEN rather than on build, so it needs no repaint of its own:
    // the favourites are another setting's value and it may have changed on
    // another device since this control was drawn (D-2).
    function fill() {
      pop.textContent = "";
      option("", "default");
      const favs = latest.model_favourites || [];
      for (const model of favs) option(model, model);
      if (!favs.length) {
        pop.append(el("p", "picknone",
          "star a model in settings to put it here"));
      }
      if (onOpenSettings) {
        const more = el("button", "pickmore", "More in settings");
        more.type = "button";
        more.onclick = () => { setOpen(false); onOpenSettings(spec.key); };
        pop.append(more);
      }
    }

    btn.onclick = () => {
      // Closing whatever else is open FIRST: two popovers over one row is two
      // lists of the same words with nothing saying which control either
      // belongs to.
      if (openPick && openPick.wrap !== wrap) openPick.close();
      setOpen(pop.hidden);
    };
    // ON THE WRAP, NOT ON THE POPOVER, and that is the whole of it. Opening by
    // CLICK leaves focus on the BUTTON, so a listener on `.pickpop` never sees
    // the key: Escape fell through to the page's global handler, whose cases
    // stop a reply (AC-112) and CLEAR THE COMPOSER -- so pressing Escape with
    // a picker open deleted the draft and left the picker open. Measured by
    // review 2026-09-11 against a real draft. The wrap contains the button as
    // well as the popover, so the capture-phase listener sees the key wherever
    // the focus is inside this control, and `stopPropagation` keeps the global
    // handler unconditional, which is the rule this must not break.
    wrap.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || pop.hidden) return;
      setOpen(false);
      btn.focus();
      // The popover always closes. It only SWALLOWS the key when the page
      // would otherwise have used it to clear the composer. With a reply in
      // flight Escape means STOP (AC-112), and that must not become
      // conditional on whether a menu happens to be open -- the first version
      // of this fix swallowed unconditionally and cost a stop, measured by
      // review 2026-09-11. So both things happen on one press when Butler is
      // talking, and a draft is still safe when he is not.
      if (!(escapeStops && escapeStops())) e.stopPropagation();
    }, true);

    // The accessible name carries the LABEL, the VALUE, and the reason the
    // control is inert when it is. An `aria-label` of "Conversation model"
    // alone OVERRIDES the button's own text, so a screen reader was told which
    // setting this is and never which model is answering -- the one fact the
    // row exists to show (review 2026-09-11). The dimming has the same
    // problem: opacity says nothing to a reader, and the explanation was in a
    // `title`, which is a mouse tooltip.
    function name() {
      const parts = [spec.label || spec.key, shownValue()];
      if (inactive) parts.push(inactive);
      return parts.join(", ");
    }

    return {
      headline() { return spec.quick + " " + shownValue(); },
      who() { return spec.quick; },
      value() { return shownValue(); },
      write(v) {
        current = v;
        val.textContent = shownValue();
        paintPickLabel();
        // KEPT past the u-railB1 rule (item 2), but only while a model IS
        // chosen: the pill's own word is already visible, but it is the
        // value CUT to a path's last segment, and the tooltip is the only
        // place the FULL model id survives. Two favourites that differ only
        // in their last few characters -- the quantised variants of one
        // model, which is the normal case here -- render as the same short
        // word otherwise, with no way to tell which one had taken. Blank
        // already shows the word "default" on the pill itself, so that case
        // gets no tooltip at all -- repeating the schema's own long `help`
        // sentence here was the exact obnoxious-and-long thing the owner
        // asked to have removed.
        const model = modelOf(current);
        if (model) btn.setAttribute("data-help", model);
        else btn.removeAttribute("data-help");
        btn.setAttribute("aria-label", name());
      },
      showInactive(why) {
        inactive = why || "";
        wrap.classList.toggle("inactive", !!inactive);
        btn.setAttribute("aria-label", name());
      },
    };
  }

  // -- the nav, the strip, one page at a time ------------------------------- //
  /**
   * The category list (AC-700): one heading per section the daemon SERVES, in
   * its order, and under each the pages the daemon filed there -- a group by
   * its `section`, a carve-out block by its own markup's `data-section` --
   * in served order, carve-outs first. Nothing here names a section, a group
   * or a setting (AC-41, AC-89); a fifth section or a new group appears with
   * no change in this directory. Answers the pages in NAV order, which is the
   * order the sheet keeps them in.
   *
   * A page whose section is not served (a setting in an undescribed group,
   * which the daemon refuses at import) is still listed, last, under no
   * heading: a page nobody can reach is the blank AC-7b forbids.
   */
  function buildNav(sections, made) {
    const ordered = [];
    navRoot.textContent = "";
    const list = (items) => {
      const grp = el("div", "ngrp");
      for (const p of items) {
        p.nav = navItem(p);
        grp.append(p.nav);
        ordered.push(p);
      }
      return grp;
    };
    for (const sec of sections) {
      const mine = made.filter((p) => p.section === sec.key);
      // Carve-outs first in their section, then the groups in served order.
      const items = mine.filter((p) => p.stage == null && !p.node.matches(".setgroup"))
        .concat(mine.filter((p) => p.node.matches(".setgroup")));
      if (!items.length) continue;
      navRoot.append(el("p", "nsec", sec.label || sec.key), list(items));
    }
    const rest = made.filter((p) => !ordered.includes(p));
    if (rest.length) navRoot.append(list(rest));
    return ordered;
  }

  function navItem(p) {
    const b = el("button", "nitem");
    b.type = "button";
    b.id = "nav-" + p.key;
    b.dataset.page = p.key;
    // The stage NUMBER the daemon gave it, never a name for it; a page with
    // no stage gets no number rather than an invented position.
    if (p.stage != null) b.append(el("span", "nnum", String(p.stage)));
    const text = el("span", "ntext");
    text.append(el("span", "nlbl", p.label), el("span", "nsub"));
    // A carve-out's own quiet tag, in its markup's words (`data-tag`).
    if (p.tag) text.append(el("span", "ntag", p.tag));
    const count = el("span", "ncount");
    count.hidden = true;
    const chev = icon(ICON.chevRight, true);
    chev.classList.add("nchev");
    b.append(text, count, chev);
    b.onclick = () => openPage(p.key);
    return b;
  }

  /**
   * The signal path (AC-701, supersedes AC-84's rail): one stop per group the
   * daemon gave a `stage`, in served order, each with its label and the
   * current value of its `headline`. Pressing one opens its page. Drawn on
   * every staged page, as ONE strip moved by `showPage`. A line joins the
   * stops because sound really does pass from each to the next; the groups
   * that are not stages are not on it.
   */
  function buildStrip(list) {
    stops.clear();
    strip = null;
    const staged = list.filter((p) => p.stage != null);
    if (!staged.length) return;
    strip = el("nav", "strip");
    strip.setAttribute("aria-label", "Signal path");
    strip.append(el("p", "striphd", "Signal path"));
    const row = el("div", "stops");
    for (const p of staged) {
      const b = el("button", "stop");
      b.type = "button";
      b.id = "stop-" + p.key;
      b.dataset.page = p.key;
      const sub = el("span", "ssub");
      b.append(el("span", "snode", String(p.stage)), el("span", "slbl", p.label), sub);
      b.onclick = () => showPage(p.key);
      // Every part of a stop is REPAINTED, never rebuilt (AC-56): the value
      // it shows and whether its stage is one the current configuration
      // gives nothing to do. A rebuild mid-edit destroys the control under
      // the cursor, and a headline value changes under a stop that has to
      // survive it.
      stops.set(p.key, {
        node: b,
        paint() {
          sub.textContent = p.read() || "";
          b.classList.toggle("inert", inertGroups.has(p.key));
        },
      });
      row.append(b);
    }
    strip.append(row);
  }

  /**
   * ONE category page on screen (AC-700): every other page hidden, the nav
   * and the strip marking this one, the strip moved onto it when it is a
   * stage, the scroll put back to its top. While a query runs the RESULTS
   * own the pages' visibility (`applySearch`); this only remembers the page
   * to come back to.
   */
  function showPage(key) {
    if (!pages.has(key)) return;
    current = key;
    const page = pages.get(key);
    if (!query) {
      for (const [k, p] of pages) p.node.hidden = k !== key;
    }
    for (const [k, p] of pages) {
      p.nav.setAttribute("aria-current", k === key ? "page" : "false");
    }
    for (const [k, s] of stops) {
      s.node.setAttribute("aria-current", k === key ? "step" : "false");
    }
    if (strip && !query) {
      if (page.stage != null) page.node.prepend(strip);
      else strip.remove();
    }
    if (scroller) scroller.scrollTop = 0;
    if (!query) fitHelp(page.node);
  }

  // The phone's back stack (AC-705): the category list, or one page. ONE
  // writer; at a desk width both halves show and the attribute changes
  // nothing that is drawn.
  function setPhone(view) {
    if (sheet) sheet.dataset.phone = view;
  }

  // A press on a nav item, a result's "Open", or the composer picker's door:
  // leave any search, show the page, and on a phone step into it.
  function openPage(key) {
    if (query) clearSearch();
    // The phone steps into the page FIRST, so the page has a box when
    // `showPage` measures its help.
    setPhone("page");
    showPage(key);
  }

  function clearSearch() {
    if (search) search.value = "";
    applySearch("");
  }

  if (back) {
    back.onclick = () => {
      setPhone("list");
      const item = pages.has(current) ? pages.get(current).nav : null;
      if (item) item.focus();
    };
  }

  /**
   * Opening the sheet. With a setting key (the composer picker's "More in
   * settings"), its page, with its row brought into view and its fold
   * opened; without one, the page last read -- and on a phone the list,
   * which is where a sheet of categories starts.
   */
  function reveal(key) {
    if (query) clearSearch();
    const row = key ? rows.get(key) : null;
    if (!row) {
      setPhone("list");
      if (pages.has(current)) fitHelp(pages.get(current).node);
      return;
    }
    openPage(row.spec.group);
    const fold = row.node.closest("details.adv");
    if (fold) fold.open = true;
    row.node.scrollIntoView({ block: "center" });
  }

  // HELP IS TWO LINES, THEN "More" (docs/lantern-ui.md 5.1). Whether a help
  // sentence is longer than two lines is a fact about the RENDERED box, so it
  // is measured on what is shown -- a hidden page has no box -- and "More"
  // appears only where something is actually cut. Search reads the whole
  // sentence either way: `data-hay` is built from the schema.
  function fitHelp(node) {
    for (const help of node.querySelectorAll(".set-help.clamp")) {
      const more = help.nextElementSibling;
      if (!more || !more.classList.contains("more")) continue;
      if (help.offsetParent === null) continue;
      more.hidden = help.scrollHeight <= help.clientHeight + 1;
    }
  }
  let fitTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(() => {
      if (pages.has(current)) fitHelp(query ? root : pages.get(current).node);
    }, 150);
  });

  // What a nav line or a strip stop shows: the CURRENT value of the setting
  // the daemon named as that group's headline, or nothing at all where it
  // named none -- a stop inventing a value for a seam that has no single fact
  // is worse than a stop showing only its name.
  //
  // Read from the ROW rather than from `latest`, and that is the whole of the
  // repaint problem: the `settings` broadcast lands in put() and never touches
  // `latest`, and rebuilding on a value change is the defect the sig guard
  // exists to stop (it left the router's model dropdown missing a connection
  // until a reload). So the nav and the strip are built once and repainted
  // from every path a value can arrive on.
  function headline(key) {
    if (!key) return "";
    const row = rows.get(key);
    if (!row) return "";
    // A labelled choice reads as its LABEL here too, not as its stored value.
    // The segmented control shows "harness only" while the stop beside it
    // showed the identifier the daemon stores, so the same setting had two
    // names on one screen and only one of them was English. Falls through to
    // `human()` for everything else, which is every other control: `labels`
    // is opt-in and empty unless the daemon says otherwise.
    const label = (row.spec.labels || {})[row.spec.value];
    return label || human(row.spec.value);
  }

  function paintNav() {
    for (const p of pages.values()) {
      if (!p.nav) continue;
      p.nav.querySelector(".nsub").textContent = p.read() || "";
      p.nav.classList.toggle("inert", inertGroups.has(p.key));
    }
    for (const s of stops.values()) s.paint();
  }

  // -- search --------------------------------------------------------------- //
  /**
   * One search across every category at once (AC-702, AC-85's rules).
   *
   * While a query runs the sheet shows RESULTS instead of one page: every
   * page with a match, in nav order, each under a line that opens it, with
   * only its matching rows -- full, editable rows, not a list of links. The
   * nav counts each category's matches. When nothing matches the sheet says
   * so rather than going blank.
   *
   * The advanced folds are OPENED while a query is running, or a matching
   * advanced setting would be found and then hidden behind a fold -- and
   * each one is put back the way it was found when the query goes, since the
   * fold is how the everyday settings stay readable.
   *
   * A row can be out of sight for three reasons at once -- filtered, folded,
   * or dimmed as inert -- and the owner is only told one of them. That is why
   * the fold OPENS rather than counting as a miss: two of the three are undone
   * by the act of searching, and the third is a dim row that is still on the
   * page.
   */
  function applySearch(q) {
    query = (q || "").trim().toLowerCase();
    if (sheet) sheet.classList.toggle("searching", !!query);
    let total = 0;
    let cats = 0;
    let deep = false;
    for (const p of pages.values()) {
      const rowNodes = p.node.querySelectorAll(".set");
      let count = 0;
      for (const node of rowNodes) {
        const hit = !query || (node.dataset.hay || "").indexOf(query) > -1;
        node.hidden = !hit;
        if (hit && query) {
          count++;
          if (node.closest("details.adv")) deep = true;
        }
      }
      // The two hand-built blocks have no schema rows, so they match on their
      // own text -- which is the only place their words exist.
      if (query && !rowNodes.length
          && p.node.textContent.toLowerCase().indexOf(query) > -1) count = 1;
      for (const fold of p.node.querySelectorAll("details.adv")) {
        if (query) {
          if (fold.dataset.wasOpen === undefined) {
            fold.dataset.wasOpen = fold.open ? "1" : "0";
          }
          fold.open = true;
          fold.hidden = !fold.querySelector(".set:not([hidden])");
        } else {
          fold.hidden = false;
          if (fold.dataset.wasOpen !== undefined) {
            fold.open = fold.dataset.wasOpen === "1";
            delete fold.dataset.wasOpen;
          }
        }
      }
      const badge = p.nav ? p.nav.querySelector(".ncount") : null;
      if (badge) {
        badge.textContent = String(count);
        badge.hidden = !query || !count;
        p.nav.classList.toggle("miss", !!query && !count);
      }
      if (query) {
        p.node.hidden = !count;
        if (count) { cats++; total += count; }
      }
    }
    if (results) {
      results.hidden = !query || !total;
      resultsLine.textContent = total + (total === 1 ? " match" : " matches")
        + " for \"" + query + "\" in " + cats
        + (cats === 1 ? " category" : " categories")
        + (deep ? ", advanced settings included." : ".");
    }
    if (noMatch) {
      noMatch.hidden = !query || total > 0;
      noMatch.textContent = "Nothing matched \"" + query + "\".";
    }
    if (searchClear) searchClear.hidden = !query;
    if (query) {
      if (strip) strip.remove();
      if (scroller) scroller.scrollTop = 0;
      fitHelp(root);
    } else if (pages.has(current)) {
      showPage(current);
    }
  }

  // -- the restart bar ------------------------------------------------------ //
  // Every change the running daemon has not applied yet, counted ONCE at the
  // foot of the sheet and named, instead of every row saying "after restart"
  // to nobody (AC-86, AC-703). The LIST is the daemon's (AC-716), so a change
  // from any device, or from before this page loaded, is counted, and the bar
  // clears by itself when the daemon comes back from its automatic restart
  // (AC-715): `ready` re-reads the settings. Nothing here asks the owner to
  // press Restart; the account menu's Restart still exists.
  function pendingRestart() {
    const names = [];
    for (const key of pending) {
      const row = rows.get(key);
      if (row) names.push(row.spec.label || key);
    }
    return names;
  }

  function paintCommit() {
    if (!commitRoot) return;
    const names = pendingRestart();
    const n = names.length;
    commitRoot.classList.toggle("show", n > 0);
    // Out of the Tab order while it is not shown.
    commitRoot.inert = n === 0;
    const count = commitRoot.querySelector("#commitCount");
    const word = commitRoot.querySelector("#commitWord");
    const list = commitRoot.querySelector("#commitNames");
    if (count) count.textContent = String(n);
    if (word) word.textContent = n === 1 ? "change" : "changes";
    if (list) list.textContent = names.join(", ");
  }

  function buildGroup(g, items) {
    const section = el("section", "setgroup");
    const head = el("div", "setgroup-head");
    // A real heading, not a styled span (AC-101): a screen reader navigates
    // the sheet by heading. The sheet's own title is the nav's h2; each page
    // is one h2 under it. The class is what index.html decorates, so it
    // stays.
    const title = el("h2", "label", g.label || g.key);
    title.id = "pg-" + g.key;
    section.setAttribute("aria-labelledby", title.id);
    head.append(title);
    const rst = el("button", "qbtn", "Reset group");
    rst.type = "button";
    rst.onclick = () => reset(g.key);
    head.append(rst);
    section.append(head);
    // One sentence under the heading, in the DAEMON's words (AC-87): the
    // page's lede. A group without one prints nothing rather than an invented
    // sentence: a page that describes a seam is naming one.
    if (g.blurb) section.append(el("p", "setgroup-blurb", g.blurb));
    // A group that links a device by QR code (the daemon serves the route;
    // the page names none, AC-41, AC-503).
    if (g.pairing) section.append(buildPairing(g.pairing));

    // Everyday first, in tone-stepped cards, then this seam's own fold. A
    // wall of thresholds is not what you want in front of you while standing
    // in a room talking, but nor is hunting for the one that belongs to the
    // thing you are reading about.
    const everyday = items.filter((s) => !s.advanced);
    const advanced = items.filter((s) => s.advanced);
    if (everyday.length) {
      const cards = el("div", "cards");
      for (const spec of everyday) cards.append(buildRow(spec));
      section.append(cards);
    }
    if (advanced.length) {
      const det = el("details", "adv");
      const sum = el("summary", "");
      sum.append(icon(ICON.chevRight, true), el("span", "", "Advanced"),
                 el("span", "fcount", advanced.length + " more"));
      det.append(sum);
      const cards = el("div", "cards");
      for (const spec of advanced) cards.append(buildRow(spec));
      det.append(cards);
      section.append(det);
    }
    // A group that also OWNS a list of items the schema cannot model (the
    // daemon serves the route; the page names none, AC-41).
    if (g.collection) section.append(buildCollection(g.collection));
    return section;
  }

  /**
   * A collection a settings group owns (`group.collection`, a route the
   * DAEMON names): notification channels today (docs/automations.md 7).
   * The route answers `{noun, items, kinds}` -- the rows, and for each kind
   * the fields an Add form needs -- so this names no item, kind or field.
   *
   * A `secret` field is a password input that starts EMPTY and is never
   * filled from anything the daemon sent, because the daemon never sends one
   * (AC-471). Replacing a secret is Add with the same name; there is no Edit
   * that could show one.
   */
  function buildCollection(url) {
    const box = el("div", "collection");
    const list = el("div", "cards");
    const form = el("div", "connform");
    const err = el("div", "set-err");
    err.hidden = true;
    box.append(list, form, err);
    let noun = "item";
    let built = false;

    function fail(message) { err.textContent = message; err.hidden = !message; }

    async function send(method, path, body) {
      fail("");
      const r = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const answer = await r.json().catch(() => null);
      if (!r.ok) throw new Error((answer && answer.error) || ("HTTP " + r.status));
      return answer;
    }

    function paint(doc) {
      noun = doc.noun || noun;
      list.textContent = "";
      if (!doc.items.length) list.append(el("div", "mode-note", "None yet."));
      for (const item of doc.items) {
        const row = el("div", "connrow");
        const it = el("div", "it");
        it.append(el("span", "cname", item.label || item.name));
        row.append(it);
        const bits = [item.name, item.kind];
        if (item.default) bits.push("default");
        if (!item.configured) bits.push("NOT USABLE: " + item.problem);
        // A usable item can still report a live problem (a second reader of
        // the same bot, docs/messaging.md 8.1): shown, not disabled.
        else if (item.problem) bits.push("PROBLEM: " + item.problem);
        const meta = el("span", "cmeta", bits.join("  .  "));
        it.append(meta);
        const test = ghostButton("Send test");
        test.disabled = !item.configured;
        test.onclick = () => {
          meta.textContent = "sending...";
          send("POST", url + "/" + encodeURIComponent(item.name) + "/test")
            .then((a) => { meta.textContent = a.ok ? "sent" : "failed: " + a.detail; })
            .catch((e) => { meta.textContent = "failed: " + (e.message || e); });
        };
        const drop = ghostButton("Remove");
        drop.onclick = () => send("DELETE", url + "/" + encodeURIComponent(item.name))
          .then(paint).catch((e) => fail((e.message || e) + ""));
        row.append(test, drop);
        list.append(row);
      }
      if (!built) { built = true; buildForm(doc.kinds || []); }
    }

    function buildForm(kinds) {
      if (!kinds.length) return;
      const kind = el("select");
      kind.setAttribute("aria-label", noun + " kind");
      for (const k of kinds) {
        const o = el("option", "", k.label || k.kind);
        o.value = k.kind;
        kind.append(o);
      }
      const name = el("input");
      name.type = "text";
      name.placeholder = "name (a-z, 0-9, -)";
      name.setAttribute("aria-label", noun + " name");
      const label = el("input");
      label.type = "text";
      label.placeholder = "label (optional)";
      label.setAttribute("aria-label", noun + " label");
      const fieldsHost = el("div", "connform");
      let inputs = [];
      function paintFields() {
        fieldsHost.textContent = "";
        const spec = kinds.find((k) => k.kind === kind.value) || kinds[0];
        inputs = spec.fields.map((f) => {
          const input = el("input");
          input.type = f.secret ? "password" : "text";
          input.autocomplete = "off";
          input.placeholder = f.label || f.key;
          // No `title`: the page bans the browser's own tooltip, and a field
          // whose placeholder names it gets no hover help (D8). The help still
          // reaches a screen reader.
          if (f.help) input.setAttribute("aria-description", f.help);
          input.setAttribute("aria-label", f.label || f.key);
          fieldsHost.append(input);
          return { key: f.key, input };
        });
      }
      kind.onchange = paintFields;
      paintFields();
      const add = el("button", "raise", "Add " + noun);
      add.type = "button";
      add.onclick = () => {
        const body = { name: name.value.trim(), kind: kind.value,
                       label: label.value.trim() };
        for (const { key, input } of inputs) body[key] = input.value.trim();
        send("POST", url, body).then((doc) => {
          name.value = label.value = "";
          for (const { input } of inputs) input.value = "";
          paint(doc);
        }).catch((e) => fail((e.message || e) + ""));
      };
      form.append(kind, name, label, fieldsHost, add);
    }

    send("GET", url).then(paint).catch((e) =>
      fail("could not read the list: " + (e.message || e)));
    return box;
  }

  /**
   * A group's pairing block (`group.pairing`, a route the DAEMON names;
   * docs/messaging.md MS-5, AC-503). Every answer is one document:
   * `{status, headline, detail, tone, qr, qr_count, poll, actions}`, or on a
   * 503 `{error, poll, actions: []}`. The words -- the phone steps, and the
   * passkey hint once the code has been reissued twice -- are the daemon's,
   * and so is which buttons apply, so this names no state, service or
   * setting. Each action is `{id, label, method, query, confirm}`; one with
   * `confirm` takes two presses and sends `{confirm: true}`.
   *
   * Untrusted text goes in by textContent only. The QR is taken only as a
   * PNG data URL and drawn on a forced WHITE plate whatever the theme: an
   * inverted code does not scan.
   */
  function buildPairing(url) {
    const box = el("div", "pairing");
    const status = el("p", "pair-status");
    status.setAttribute("role", "status");
    const plate = el("div", "qrplate");
    plate.style.background = "#fff";
    plate.style.padding = "14px";
    plate.style.borderRadius = "12px";
    plate.style.width = "max-content";
    plate.style.lineHeight = "0";
    const img = el("img");
    img.alt = "Code to scan with your phone";
    img.width = 176;
    img.height = 176;
    plate.append(img);
    plate.hidden = true;
    const detail = el("p", "set-help");
    const warn = el("p", "set-err");
    warn.hidden = true;
    const buttons = el("div", "btnrow");
    // Words and buttons on the left, the code on the right (lantern-ui 5.1).
    const words = el("div", "ptext");
    words.append(status, detail, warn, buttons);
    box.append(words, plate);
    let timer = 0;
    let seq = 0;
    let armed = "";
    let armedAt = 0;
    let armTimer = 0;

    function disarm() {
      armed = "";
      clearTimeout(armTimer);
      warn.textContent = "";
      warn.hidden = true;
    }

    function paint(doc) {
      const failed = doc.ok === false;
      status.textContent = failed ? (doc.error || "") : (doc.headline || "");
      status.dataset.tone = failed ? "error" : (doc.tone || "neutral");
      detail.textContent = failed ? "" : (doc.detail || "");
      detail.hidden = !detail.textContent;
      const qr = typeof doc.qr === "string" && doc.qr.startsWith("data:image/png;base64,") ? doc.qr : "";
      if (qr) img.src = qr; else img.removeAttribute("src");
      plate.hidden = !qr;
      disarm();
      buttons.textContent = "";
      for (const action of doc.actions || []) buttons.append(actionButton(action));
    }

    function actionButton(action) {
      const b = ghostButton(action.label);
      b.onclick = (e) => {
        if (!action.confirm) { read(action); return; }
        // Two presses, the reset-all guard's rules (dblclick, quick repeat).
        if (e && e.detail > 1) return;
        if (armed === action.id) {
          if (Date.now() - armedAt < RESET_MIN_GAP_MS) return;
          disarm();
          read(action);
          return;
        }
        armed = action.id;
        armedAt = Date.now();
        warn.textContent = action.confirm + " Press " + action.label + " again to confirm.";
        warn.hidden = false;
        clearTimeout(armTimer);
        armTimer = setTimeout(disarm, RESET_ARM_MS);
      };
      return b;
    }

    async function read(action) {
      clearTimeout(timer);
      timer = 0;
      const mine = ++seq;
      // X-Butler-Local (docs/messaging.md 6.5 RV-1, AC-586): a route the
      // daemon keeps to this machine (`/api/phonelink`'s set-up) also needs
      // this header, which a cross-site page cannot send. Same-origin, so no
      // preflight; on another device the LISTENER still says no.
      const init = { method: action ? action.method : "GET",
                     headers: { "X-Butler-Local": "1" } };
      if (action && action.confirm) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify({ confirm: true });
      }
      let doc;
      try {
        const r = await fetch(url + (action && action.query ? "?" + action.query : ""), init);
        doc = await r.json().catch(() => null);
        if (!doc) throw new Error("HTTP " + r.status);
      } catch (e) {
        doc = { ok: false, error: "could not reach the daemon: " + (e.message || e), poll: false, actions: [] };
      }
      // A later read owns the block; an earlier answer arriving late is stale.
      if (mine !== seq) return;
      paint(doc);
      if (doc.poll && box.isConnected) timer = setTimeout(() => read(), PAIRING_POLL_MS);
    }

    pairings.add(() => read());
    read();
    return box;
  }

  // Whether a row's control goes UNDER its words rather than beside them:
  // anything that needs the card's width -- prose, a list, a model pair, a
  // discovered picker, preview cards -- and a row of labelled buttons whose
  // labels run past SEG_INLINE_MAX characters together. Decided by kind and
  // by length, never by name (AC-41).
  function stacks(spec) {
    if (spec.kind === "multiline" || spec.kind === "list"
        || spec.kind === "pair") return true;
    if (spec.kind === "text") return !!spec.choices_from;
    if (spec.kind !== "choice") return false;
    if (spec.preview) return true;
    const labels = spec.labels || {};
    const choices = spec.choices || [];
    if (!Object.keys(labels).length || choices.length > BUTTONS_MAX) return false;
    return choices.map((c) => labels[c] || c).join("").length > SEG_INLINE_MAX;
  }

  function buildRow(spec) {
    const wrap = el("div", "set k-" + spec.kind);
    wrap.dataset.key = spec.key;
    // What search matches on: the label, the help and the key, all from the
    // schema (AC-85, AC-89). Built here rather than read back off the rendered
    // row, because a control's own text -- a model id in a datalist, the word
    // "on" in a switch -- is not what anybody is searching for. The key is
    // matched but no longer printed (lantern-ui 5.1).
    wrap.dataset.hay = ((spec.label || "") + " " + (spec.help || "") + " "
                        + spec.key).toLowerCase();
    if (spec.restart) wrap.classList.add("restart");
    if (stacks(spec)) wrap.classList.add("stk");

    const top = el("div", "set-top");
    // The row's name is a real <label>, so the control it names computes an
    // accessible name instead of being announced as an unnamed input (AC-100).
    // Measured before this: 42 of the 61 controls this file renders had none,
    // because the name sat in a <span> that was associated with nothing. The
    // `for` is wired below, after buildControl, since only then is it known
    // whether this kind of control has a primary input to point at. The word
    // itself is still the schema's (AC-41).
    const name = el("label", "set-name", spec.label || spec.key);
    top.append(name);
    if (spec.restart) {
      const tag = el("span", "tag restart");
      tag.append(icon(ICON.power, true), el("span", "", "Applies on restart"));
      // KEPT, deliberately, past the u-railB1 rule (item 2): the tag alone
      // does not say the value is already saved and that Butler restarts BY
      // ITSELF to apply it (AC-715), which is the one thing nobody would guess.
      tag.setAttribute("data-help", "Saved now; Butler restarts by itself to "
                       + "apply it as soon as nothing is running.");
      top.append(tag);
    }
    // Said only in the search results, where an advanced row stands outside
    // its fold (the `advanced` flag is the schema's, never inferred).
    if (spec.advanced) top.append(el("span", "tag advtag", "Advanced"));
    wrap.append(top);

    // Two lines of help, then "More" where the sentence is cut (`fitHelp`).
    if (spec.help) {
      const help = el("p", "set-help clamp", spec.help);
      const more = el("button", "more", "More");
      more.type = "button";
      more.hidden = true;
      more.setAttribute("aria-expanded", "false");
      // The help is read whole by a screen reader either way; the button is
      // for eyes, and says what it does.
      more.onclick = () => {
        const open = help.classList.toggle("clamp") === false;
        more.textContent = open ? "Less" : "More";
        more.setAttribute("aria-expanded", String(open));
      };
      wrap.append(help, more);
    }

    // Why this control decides nothing right now, in the daemon's own words.
    // This setting stays editable, since the configuration that reads it is
    // one restart away and a value set for it now has to survive that trip
    // (AC-7b, AC-66). Its sentence is the one part of a dim row NOT dimmed.
    const inertEl = el("div", "set-inactive");
    inertEl.hidden = true;
    const inertText = el("span", "");
    inertEl.append(icon(ICON.info, true), inertText);
    wrap.append(inertEl);

    const noteEl = el("div", "set-note");
    noteEl.hidden = true;
    wrap.append(noteEl);
    const err = el("div", "set-err");
    err.hidden = true;
    wrap.append(err);

    const ctl = el("div", "set-ctl");
    // The save, said beside the control it belongs to (AC-171): a spinner
    // while the write is in flight, "Saved" on the daemon's acknowledgement.
    // The words are the stylesheet's, keyed on the row's `busy` / `saved`.
    const fb = el("span", "fb");
    fb.setAttribute("aria-hidden", "true");
    ctl.append(fb);
    wrap.append(ctl);

    // Why the value on screen is NOT the one in use, in the daemon's own
    // words (`problem`, docs/identity-memory.md 11.2, AC-424): today a
    // hand-edited file over its cap. Under the control, which stays live and
    // undimmed -- it is where the owner fixes it -- so this is not `inactive`.
    const problemEl = el("div", "set-err set-problem");
    problemEl.hidden = true;
    wrap.append(problemEl);

    const control = buildControl(spec, ctl);

    // The control carrying this row's own id is the one the name belongs to
    // (AC-100). A kind that renders none of them -- a row of labelled buttons,
    // a list, a kind this page cannot edit -- leaves the label unassociated
    // rather than pointing the browser at an id that does not exist, and names
    // its own controls where they are built.
    const primary = ctl.querySelector("[id='set-" + spec.key + "']");
    if (primary) name.htmlFor = primary.id;

    // Focus tracking, so a remote change cannot yank the control out from
    // under a finger. focusout also fires when moving BETWEEN inputs inside one
    // row (a list item to its remove button), hence the containment re-check.
    wrap.addEventListener("focusin", (e) => {
      if (e.target && e.target.dataset && e.target.dataset.nolock) return;
      editing.add(spec.key);
    });
    wrap.addEventListener("focusout", () => {
      setTimeout(() => {
        if (wrap.contains(document.activeElement)) return;
        editing.delete(spec.key);
        flush(spec.key);
      }, 0);
    });

    const row = {
      spec,
      node: wrap,
      // `shown` is what the control is DISPLAYING, kept separate from
      // spec.value, which is what the daemon holds. They differ for exactly
      // as long as a write is in flight or a remote change is being held back,
      // and telling them apart is what makes both of those cases behave.
      shown: undefined,
      write(v) { row.shown = v; control.write(v); },
      busy(on) { wrap.classList.toggle("busy", !!on); },
      flashSaved() {
        // Restart the animation even on a repeat save: without removing the
        // class first the browser reuses the finished animation and the
        // second save looks like nothing happened.
        wrap.classList.remove("saved");
        void wrap.offsetWidth;
        wrap.classList.add("saved");
        clearTimeout(wrap._savedTimer);
        wrap._savedTimer = setTimeout(
          () => wrap.classList.remove("saved"), 1800);
      },
      // Some controls depend on a setting they do NOT own -- the model
      // pickers show the shared favourites list. Their own write() only
      // runs when their own value changes, so without this a favourite
      // added from one picker never appears in the other.
      repaint() { if (control.repaint) control.repaint(); },
      // Whether the value differs from the default. Kept on the row for the
      // instruments; the sheet no longer prints a "changed" tag or the
      // default (lantern-ui 5.1) -- "Reset group" is the way back.
      mark(changed) { wrap.classList.toggle("changed", !!changed); },
      setError(msg) { err.textContent = msg; err.hidden = false; },
      clearError() { err.hidden = true; err.textContent = ""; },
      setNote(msg) { noteEl.textContent = msg; noteEl.hidden = false; },
      clearNote() { noteEl.hidden = true; noteEl.textContent = ""; },
      showInactive() {
        const why = row.spec.inactive || "";
        inertText.textContent = why;
        inertEl.hidden = !why;
        wrap.classList.toggle("inactive", !!why);
      },
      showProblem() {
        const why = row.spec.problem || "";
        problemEl.textContent = why;
        problemEl.hidden = !why;
      },
    };
    rows.set(spec.key, row);
    row.showInactive();
    row.showProblem();
    row.write(spec.value);
    row.mark(spec.changed);
    return wrap;
  }

  // One branch per `kind`. Anything the daemon invents that is not handled here
  // is shown read-only and says so, rather than being dropped silently.
  //
  // `prefix` is what keeps element ids unique when the SAME setting is drawn
  // twice -- once in this surface and once in a menu (`menuControls`). Two nodes
  // with one id is not cosmetic: `buildRow` points its <label> at the control
  // by id, and the browser would hand every click on the panel label to
  // whichever copy it found first.
  function buildControl(spec, host, prefix) {
    prefix = prefix || "set-";
    switch (spec.kind) {
      case "choice": return buildChoice(spec, host, prefix);
      case "bool": return buildBool(spec, host, prefix);
      case "number": return buildNumber(spec, host, prefix);
      case "text": return buildText(spec, host, prefix);
      case "multiline": return buildMultiline(spec, host, prefix);
      case "list": return buildList(spec, host, prefix);
      case "pair": return buildPair(spec, host, prefix);
      default: {
        const ro = el("code", "set-unknown");
        host.append(ro, el("span", "sub",
          ` this page does not know how to edit a "${spec.kind}" setting`));
        return { write: (v) => { ro.textContent = JSON.stringify(v); } };
      }
    }
  }

  // A choice the daemon sent LABELS for is laid out as named buttons instead
  // of a dropdown. That is the daemon saying "these values have names a person
  // should read, and there are few enough to show at once": the stored value
  // is an identifier, and a select showing identifiers makes the owner
  // translate. Which choices those are is never decided here -- a spec with no
  // labels keeps the select, because a hundred voices in a row of buttons is
  // not a control (AC-41).
  function buildChoice(spec, host, prefix) {
    const labels = spec.labels || {};
    const named = Object.keys(labels).length > 0;
    // Drawn by SEEING it (AC-704): a choice the daemon flags `preview` is a
    // row of cards, each a miniature of the page in that value. The flag is
    // the daemon's; this file never decides it by the setting's name.
    if (spec.preview) return buildPreviewCards(spec, host, labels);
    if (named && (spec.choices || []).length <= BUTTONS_MAX) {
      const seg = el("div", "segset");
      // The row's <label> cannot name a group of buttons, so the group says
      // what it is on its own, in the schema's words (AC-100).
      seg.setAttribute("role", "group");
      seg.setAttribute("aria-label", spec.label || spec.key);
      host.append(seg);
      return buildChoiceButtons(spec, seg, labels);
    }
    const wrap = el("span", "selw");
    const sel = el("select");
    sel.id = prefix + spec.key;
    for (const c of spec.choices || []) {
      // An `<option>` is plain text, so the served default says so in words
      // (owner 2026-10-29: a bare star meant nothing). A label, when the
      // daemon sent one, is what the option reads; a label that already says
      // "default" is not told twice.
      const text = named ? labels[c] : c;
      const says = /default/i.test(text);
      const o = el("option", "", (text + (same(c, spec.default) && !says
                                          ? " (default)" : "")).trim());
      o.value = c;
      sel.append(o);
    }
    sel.onchange = () => commit(spec.key, sel.value);
    wrap.append(sel, icon(ICON.chevDown, true));
    host.append(wrap);
    return {
      write(v) {
        // A value outside the schema's own choices cannot happen through the
        // API, but showing the empty select would be a silent lie if it did.
        if (![...sel.options].some((o) => o.value === v)) {
          const o = el("option", "", v + "  (not in this schema)");
          o.value = v;
          sel.append(o);
        }
        sel.value = v;
      },
    };
  }

  /**
   * A choice drawn as PREVIEW CARDS (AC-704): one card per value, each a
   * miniature of the page -- sidebar, a greeting, a reply, the composer --
   * wearing that value's own tokens. The miniature carries the value as its
   * `data-style` and the page's current `data-theme`, so the stylesheet's own
   * token blocks paint it: this file names no style and holds no colour. The
   * value marked Default is the schema's `default`.
   */
  function buildPreviewCards(spec, host, labels) {
    const set = el("div", "previews");
    set.setAttribute("role", "group");
    set.setAttribute("aria-label", spec.label || spec.key);
    host.append(set);
    const cards = new Map();
    const minis = [];
    const theme = () => document.documentElement.dataset.theme || "";
    for (const c of spec.choices || []) {
      const b = el("button", "pv");
      b.type = "button";
      b.setAttribute("aria-pressed", "false");
      const mini = el("span", "mini");
      mini.setAttribute("aria-hidden", "true");
      mini.dataset.style = c;
      mini.dataset.theme = theme();
      minis.push(mini);
      const side = el("span", "m-side");
      side.append(el("i"), el("i"), el("i"));
      const main = el("span", "m-main");
      const comp = el("span", "m-comp");
      comp.append(el("i"));
      main.append(el("span", "m-hi", "Good evening"), el("span", "m-bub"),
                  el("span", "m-l"), el("span", "m-l s"), comp);
      mini.append(side, main);
      const cap = el("span", "pvcap");
      cap.append(el("b", "", labels[c] || c));
      if (same(c, spec.default)) cap.append(el("span", "pvdef", "Default"));
      const tick = icon(ICON.check, true);
      tick.classList.add("pvsel");
      cap.append(tick);
      b.append(mini, cap);
      // The same single-key write every control here uses; choosing applies
      // live because `applyPageValues` writes the value on <html>.
      b.onclick = () => commit(spec.key, c);
      cards.set(c, b);
      set.append(b);
    }
    // The miniatures follow the theme, which another control writes.
    themeWatch = new MutationObserver(() => {
      for (const mini of minis) mini.dataset.theme = theme();
    });
    themeWatch.observe(document.documentElement,
                       { attributes: true, attributeFilter: ["data-theme"] });
    return {
      write(v) {
        for (const [c, b] of cards) {
          b.setAttribute("aria-pressed", same(c, v) ? "true" : "false");
        }
      },
    };
  }

  // `marks` is whether a value that IS the default says so. True in the panel,
  // where "(default)" is information the owner is deciding against. False in
  // the composer row, where it doubled the width of the widest control to
  // restate the shipped value of a setting that is showing its own value
  // anyway -- and where every character is competing with the text box.
  function buildChoiceButtons(spec, host, labels, marks) {
    const mark = marks !== false;
    const seen = new Map();          // value -> the button that commits it
    // `isDefault` puts a small, quieter "default" UNDER the label (owner
    // 2026-10-29: the old dot meant nothing to anyone). Under, not beside:
    // "(default)" appended inline made the widest pill nearly double its
    // neighbours' and wrapped a four-button row into two rows of two (item
    // 16, UI review 2026-09-26). The word is narrower than any label.
    function add(value, text, isDefault) {
      const b = el("button", "", text);
      b.type = "button";
      b.setAttribute("aria-pressed", "false");
      if (isDefault) {
        b.classList.add("isdef");
        b.append(el("span", "segdef", "default"));
        // Spoken as one name, never the label and the word run together.
        b.setAttribute("aria-label", text + " (default)");
      }
      // The same single-key write the select's onchange does, so a refusal
      // still lands on this row with the daemon's own message.
      b.onclick = () => commit(spec.key, value);
      seen.set(value, b);
      host.append(b);
    }
    for (const c of spec.choices || []) {
      add(c, labels[c] || c, mark && same(c, spec.default));
    }
    return {
      write(v) {
        // Same honesty the select branch owes: a value outside the schema's
        // choices cannot arrive through the API, but a row of buttons with
        // none of them pressed reads as "nothing is set", which is a lie
        // about a control that is holding a value.
        if (!seen.has(v)) add(v, v + "  (not in this schema)");
        for (const [value, b] of seen) {
          b.setAttribute("aria-pressed", same(value, v) ? "true" : "false");
        }
      },
    };
  }

  // A switch: the word, then the track. The checkbox stays a real checkbox --
  // focusable, labelled by the row, read as checked -- and only its LOOK is
  // the track beside it; the pressed look keys off the box's own state
  // (AC-104, `:has(input:checked)`), never off an attribute set beside it.
  function buildBool(spec, host, prefix) {
    const label = el("label", "switch");
    const box = el("input");
    box.type = "checkbox";
    box.id = prefix + spec.key;
    const text = el("span", "swword", "Off");
    const track = el("span", "swtrack");
    track.setAttribute("aria-hidden", "true");
    box.onchange = () => { text.textContent = box.checked ? "On" : "Off";
                           commit(spec.key, box.checked); };
    label.append(text, box, track);
    host.append(label);
    return { write(v) { box.checked = !!v; text.textContent = v ? "On" : "Off"; } };
  }

  function buildNumber(spec, host, prefix) {
    const wrap = el("div", "num");
    const hasRange = Number.isFinite(spec.min) && Number.isFinite(spec.max);
    const step = hasRange ? stepFor(spec) : "any";
    let slider = null;

    const box = el("input");
    box.type = "number";
    box.id = prefix + spec.key;
    box.className = "numbox";
    // step="any" on the typed box on purpose: the slider's granularity is for
    // dragging, and it must never stop someone typing the exact number they
    // want -- or reject a value another device already set.
    box.step = "any";
    if (Number.isFinite(spec.min)) box.min = spec.min;
    if (Number.isFinite(spec.max)) box.max = spec.max;

    // How far along its range the slider sits, for the filled half of its
    // track. A CSS variable on the input, read by the stylesheet.
    const fill = () => {
      if (!slider) return;
      const at = (Number(slider.value) - spec.min) / ((spec.max - spec.min) || 1);
      slider.style.setProperty("--p", Math.max(0, Math.min(1, at)) * 100 + "%");
    };

    if (hasRange) {
      // Milliseconds and thresholds are far easier to feel with a slider than
      // to guess in a box, so they get both: drag for the shape of it, read the
      // exact number beside it, type it when you know what you want.
      const rng = el("div", "rng");
      slider = el("input");
      slider.type = "range";
      slider.min = spec.min;
      slider.max = spec.max;
      slider.step = step;
      slider.setAttribute("aria-label", spec.label || spec.key);
      slider.oninput = () => { box.value = slider.value; fill(); };   // readout only
      slider.onchange = () => commit(spec.key, roundToStep(Number(slider.value), step));
      const ends = el("div", "ends");
      ends.append(el("span", "", String(spec.min)), el("span", "", String(spec.max)));
      rng.append(slider, ends);
      wrap.append(rng);
    }

    box.onchange = () => {
      const v = Number(box.value);
      if (box.value.trim() === "" || !Number.isFinite(v)) {
        const row = rows.get(spec.key);
        if (row) { row.setError("that is not a number"); row.write(row.spec.value); }
        return;
      }
      commit(spec.key, v);
    };

    const side = el("div", "numside");
    side.append(box);
    const unit = unitFor(spec.key);
    if (unit) side.append(el("span", "unit", unit));
    wrap.append(side);
    host.append(wrap);

    return {
      write(v) {
        box.value = v;
        if (slider) { slider.value = v; fill(); }
      },
    };
  }

  function buildText(spec, host, prefix) {
    if (spec.choices_from) return buildDiscovered(spec, host, prefix);
    const input = el("input");
    input.type = "text";
    input.id = prefix + spec.key;
    input.className = "inp";
    input.onchange = () => commit(spec.key, input.value);
    host.append(input);
    return { write(v) { input.value = v == null ? "" : v; } };
  }

  // A block of prose (docs/identity-memory.md 4.1, AC-370). Committed on
  // change -- that is, when the field loses focus -- like `buildText`, so a
  // paragraph is one write rather than one per keystroke. `max` is the
  // daemon's length cap and is shown as a counter, NOT set as `maxlength`:
  // a browser silently truncates a paste to maxlength, and a cut persona is
  // the failure the daemon refuses loudly instead. Over the cap, the save is
  // refused with the daemon's own message on this row.
  function buildMultiline(spec, host, prefix) {
    const area = el("textarea");
    area.id = prefix + spec.key;
    area.rows = 10;
    area.spellcheck = true;
    const foot = el("div", "tafoot");
    const count = el("span", "range-hint", "");
    foot.append(count);
    const recount = () => {
      count.textContent = area.value.length
        + (spec.max != null ? " / " + spec.max : "");
    };
    area.oninput = recount;
    area.onchange = () => commit(spec.key, area.value);
    host.append(area, foot);
    return { write(v) { area.value = v == null ? "" : v; recount(); } };
  }

  // Model lists, per target, for the life of the page. The daemon caches these
  // too; this second one exists because several controls share one target and
  // would otherwise each ask on every rebuild of the panel.
  const discovered = new Map();
  function modelsFor(target) {
    if (!discovered.has(target)) {
      discovered.set(target, fetch(MODELS_URL + "?target=" + encodeURIComponent(target))
        .then(async (r) => {
          const body = await r.json().catch(() => null);
          if (!r.ok) throw new Error((body && body.error) || ("HTTP " + r.status));
          return body;
        }));
    }
    return discovered.get(target);
  }

  // Same memo, keyed by connection rather than by local target. A connection
  // that answers nothing is a LOUD note in the control, never an empty list
  // rendered as if all was well (daemon/models.py keeps those apart).
  function modelsForConnection(id) {
    const memoKey = "conn:" + id;
    if (!discovered.has(memoKey)) {
      discovered.set(memoKey, fetch(
        MODELS_URL + "?connection=" + encodeURIComponent(id))
        .then(async (r) => {
          const body = await r.json().catch(() => null);
          if (!r.ok) throw new Error((body && body.error) || ("HTTP " + r.status));
          return body;
        }));
    }
    return discovered.get(memoKey);
  }

  let connectionsPromise = null;
  // Every pair control populates its connection selector ONCE, when it is
  // built. A panel that adds or removes a connection therefore has to tell
  // them, or the picker keeps offering the set that existed at page load --
  // measured: adding a connection left the router's dropdown without it.
  // A list of repopulate callbacks rather than an event bus: there are two
  // of these and they live in the same closure.
  const connectionConsumers = [];
  function connectionsChanged() {
    connectionsPromise = null;
    discovered.clear();
    for (const repopulate of connectionConsumers) repopulate();
  }

  function allConnections() {
    if (!connectionsPromise) {
      connectionsPromise = fetch("/api/connections").then(async (r) => {
        const body = await r.json().catch(() => null);
        if (!r.ok) throw new Error((body && body.error) || ("HTTP " + r.status));
        return body.connections || [];
      });
    }
    return connectionsPromise;
  }

  /**
   * The favourites star and row, shared by the two model pickers. OPT-IN per
   * setting (`spec.favourites`), and that is load-bearing rather than
   * tidiness: the star writes ONE shared list and the row paints that list
   * under every picker that has it. Rendered unconditionally, the voice
   * picker would offer model ids as clickable chips, and pressing one would
   * commit a model id as the voice -- which the engine then refuses at
   * synthesis time, several layers away from the press that caused it.
   *
   * The star is pressed when the model in the box is a favourite; the chip
   * of the model in the box is pressed too. `pick` is the caller's commit.
   */
  function favouritesFor(spec, input, pick) {
    if (!spec.favourites) return null;
    const star = el("button", "qbtn star");
    star.type = "button";
    star.append(icon(ICON.star, true), el("span", "", "Favourite"));
    star.setAttribute("aria-pressed", "false");
    const row = el("div", "disc-favs");
    star.onclick = () => {
      const model = input.value.trim();
      if (!model) return;
      const now = (latest.model_favourites || []).slice();
      const at = now.indexOf(model);
      if (at >= 0) now.splice(at, 1); else now.push(model);
      commit("model_favourites", now);
    };
    function paint() {
      const model = input.value.trim();
      const favs = latest.model_favourites || [];
      star.setAttribute("aria-pressed", String(!!model && favs.includes(model)));
      row.textContent = "";
      if (!favs.length) return;
      const lead = el("span", "favlead");
      lead.append(icon(ICON.star, true), el("span", "", "Favourites"));
      row.append(lead);
      for (const m of favs) {
        const b = el("button", "favchip", m);
        b.type = "button";
        b.setAttribute("aria-pressed", String(m === model));
        b.onclick = () => { input.value = m; pick(m); };
        row.append(b);
      }
    }
    return { star, row, paint };
  }

  /**
   * A {conn, model} pair (AC-30).
   *
   * Two controls, because the value is two things: WHERE to ask and WHAT to
   * ask for. A bare model id was ambiguous the moment two connections served
   * one name, and unusable for any provider needing a key -- the connection
   * carries the base URL and the auth pointer, and the page never sees the
   * key itself (plan.md 4.4b).
   *
   * The model list is refetched when the connection changes, because it is
   * that connection's list and nothing else's.
   */
  function buildPair(spec, host, prefix) {
    prefix = prefix || "set-";
    const wrap = el("div", "disc");
    const row = el("div", "disc-row");
    const connWrap = el("span", "selw");
    const conn = el("select");
    conn.id = prefix + spec.key + "-conn";
    connWrap.append(conn, icon(ICON.chevDown, true));
    // The row's <label> can name only ONE control and it names the model box,
    // so the half beside it says what it is on its own (AC-100). Built from
    // the schema's label like everything else -- this file still writes down
    // no setting (AC-41).
    conn.setAttribute("aria-label", (spec.label || spec.key) + " connection");
    const input = el("input");
    input.type = "text";
    input.id = prefix + spec.key;
    input.className = "inp";
    input.placeholder = "blank = the connection's own default";
    const list = el("datalist");
    list.id = prefix + "models-" + spec.key;
    input.setAttribute("list", list.id);
    const note = el("div", "disc-note");

    let current = { conn: "", model: "" };

    function commitPair() {
      commit(spec.key, { conn: conn.value, model: input.value.trim() });
    }
    conn.onchange = () => {
      // Blank the model FIRST. Committing the text still sitting there would
      // persist a model from the connection you just left -- and for
      // router_model, which runs on every single thing you say, that arms a
      // name that 404s on the next utterance. Blank is legal and means "this
      // connection's own default", so the pair is always coherent.
      input.value = "";
      commitPair();
      fillModels();
    };
    input.onchange = commitPair;
    const fav = favouritesFor(spec, input, commitPair);

    row.append(connWrap, input);
    if (fav) row.append(fav.star);
    wrap.append(row);
    if (fav) wrap.append(fav.row);
    wrap.append(note);
    host.append(wrap);
    const paintFavs = () => { if (fav) fav.paint(); };

    function fillModels() {
      list.textContent = "";
      if (!conn.value) {
        note.textContent = "choose a connection first";
        note.className = "disc-note";
        return;
      }
      note.textContent = "reading " + conn.value + "...";
      note.className = "disc-note";
      modelsForConnection(conn.value).then((body) => {
        for (const model of body.models || []) {
          const o = el("option");
          o.value = model;
          list.append(o);
        }
        wrap.append(list);
        note.textContent = (body.models || []).length
          + " served by " + conn.value;
        note.className = "disc-note";
      }).catch((e) => {
        // Loud, and in the control: this is where the owner is looking when
        // a connection is misconfigured or its server is down.
        note.textContent = "could not read " + conn.value + ": "
          + (e.message || e);
        note.className = "disc-note bad";
      });
    }

    function populate() {
      return allConnections().then((conns) => {
      conn.textContent = "";
      const none = el("option");
      none.value = "";
      none.textContent = conns.length ? "-- choose --" : "-- none configured --";
      conn.append(none);
      for (const c of conns) {
        const o = el("option");
        o.value = c.id;
        // The owner needs to see that a connection is authenticated and
        // which pointer it uses. Never the key: the daemon does not send it.
        const auth = c.auth_from && c.auth_from !== "none"
          ? " [key: " + c.auth_from + "]" : "";
        o.textContent = (c.label || c.id) + auth + (c.missing ? " (missing)" : "");
        conn.append(o);
      }
      conn.value = current.conn || "";
      fillModels();
      }).catch((e) => {
        note.textContent = "could not read connections: " + (e.message || e);
        note.className = "disc-note bad";
      });
    }
    connectionConsumers.push(populate);
    populate();

    return {
      write(v) {
        const previous = current.conn;
        current = (v && typeof v === "object") ? v : { conn: "", model: "" };
        conn.value = current.conn || "";
        input.value = current.model || "";
        // A write can arrive from ANOTHER device (settings are server-side,
        // D-2). If it moved the connection, the datalist is still offering
        // the old one's models, so refresh it. Guarded on a real change:
        // every repaint calls write(), and refetching on each would ask the
        // endpoint far more often than the owner changes anything.
        if (conn.value && conn.value !== previous) fillModels();
        paintFavs();
      },
      repaint: paintFavs,
    };
  }

  // AC-109. ONE audition at a time on the whole surface. Pressing preview
  // twice, or previewing from two controls, must not layer two voices over
  // each other, so starting one stops whatever was already playing rather
  // than being ignored -- a control that does nothing when pressed is the
  // failure AC-7b exists to prevent.
  //
  // The page owns this audio outright: a plain Audio element, created and
  // torn down here. The microphone graph, the playback worklet and the socket
  // belong to the live turn and are not touched -- an audition is not a turn,
  // which is why it is an HTTP route and not a spoken reply.
  let audition = null;              // { stop() } of whatever is playing
  function stopAudition() {
    const playing = audition;
    audition = null;
    if (playing) playing.stop();
  }

  /**
   * A setting whose options are DISCOVERED rather than declared (AC-30).
   *
   * Three things at once, because the owner asked for all three and each one
   * fails without the others:
   *
   *   - the endpoint's own list, so a model that stops being served stops
   *     being offered and the page still names no model of its own;
   *   - free typing, because a model the endpoint has not loaded yet is still
   *     a legal thing to want, and a picker that only offers what it can see
   *     cannot express it;
   *   - favourites first, because llama-swap serves 130 and a list of 130 is
   *     a list nobody reads.
   *
   * A datalist gives the first two in one control: the browser filters as you
   * type and accepts anything you finish typing.
   *
   * When the endpoint cannot be reached this says so IN the control. Drawing
   * an empty picker would claim the endpoint serves nothing, which is a
   * different statement from "nobody answered" and the one that costs an hour.
   */
  function buildDiscovered(spec, host, prefix) {
    prefix = prefix || "set-";
    const wrap = el("div", "disc");
    const row = el("div", "disc-row");
    const input = el("input");
    input.type = "text";
    input.id = prefix + spec.key;
    // Deliberately not "the endpoint's own default": this control now also
    // fills the voice picker from the speech engine, and a placeholder naming
    // the wrong source is a small lie the owner has to decode.
    input.className = "inp";
    input.placeholder = "blank = the engine's own default";
    const list = el("datalist");
    list.id = prefix + "models-" + spec.key;
    input.setAttribute("list", list.id);
    // AC-109. Opt-in exactly like the star, and keyed off the daemon's own
    // target rather than off this setting (see AUDITION at the top).
    const previewUrl = AUDITION.get(spec.choices_from) || "";
    const listen = previewUrl ? ghostButton("Listen") : null;
    const note = el("div", "disc-note");

    input.onchange = () => commit(spec.key, input.value.trim());
    const fav = favouritesFor(spec, input, (model) => commit(spec.key, model));

    row.append(input);
    if (fav) row.append(fav.star);
    if (listen) row.append(listen);
    wrap.append(row);
    if (fav) wrap.append(fav.row);
    wrap.append(note);
    host.append(wrap);
    const paintFavs = () => { if (fav) fav.paint(); };

    // -- the note ----------------------------------------------------------- //
    // What this control says when nothing is being auditioned, remembered so
    // an audition can borrow the same line and give it back (AC-109). Without
    // this the count of what the engine offers is lost the first time anything
    // is previewed, and never comes back.
    let base = { text: "", bad: false };
    function showNote(text, bad) {
      note.textContent = text;
      note.className = bad ? "disc-note bad" : "disc-note";
    }
    function restoreNote() { showNote(base.text, base.bad); }

    modelsFor(spec.choices_from).then((body) => {
      for (const model of body.models) {
        const o = el("option");
        o.value = model;
        list.append(o);
      }
      wrap.append(list);
      base = { text: body.models.length + " offered by this engine",
               bad: false };
      restoreNote();
    }).catch((e) => {
      // Loud, and in the control itself: this is the one place the owner is
      // looking when the endpoint is misconfigured.
      base = { text: "could not read the model list: " + (e.message || e),
               bad: true };
      restoreNote();
    });

    // -- audition (AC-109) --------------------------------------------------- //
    // Hearing a voice must not CHOOSE it. Everything below reads the input and
    // plays audio: it never calls commit(), never assigns input.value, and
    // never touches the row's changed mark. The only write path in this file
    // is commit(), and no line here reaches it.
    let playing = false;
    let seq = 0;                    // bumped to abandon a request in flight

    function paintListen() {
      if (!listen) return;
      listen.textContent = playing ? "Stop" : "Listen";
      // The row's <label> names the input, so this button says what it is on
      // its own -- in the schema's words, never in words this page invents
      // about the setting (AC-100, AC-41).
      listen.setAttribute("aria-label",
        (playing ? "Stop the preview of " : "Preview ")
        + (spec.label || spec.key));
    }

    let player = null;              // { audio, url } while a preview plays

    // Teardown, from every path an audition can end on: it finished, it was
    // stopped, another control started one, the request failed.
    function halt() {
      if (player) {
        player.audio.pause();
        URL.revokeObjectURL(player.url);
        player = null;
      }
      seq++;
      playing = false;
      paintListen();
      restoreNote();
    }

    async function preview() {
      // What is TYPED, not what is saved: the whole point is to try a value
      // before committing to it.
      const name = input.value.trim();
      if (!name) {
        showNote("nothing to preview: the box is blank, which means the "
                 + "engine's own default and has no name to ask for", true);
        return;
      }
      stopAudition();
      const mine = ++seq;
      playing = true;
      paintListen();
      // Registered before the request, so a stop pressed while it is in
      // flight goes through the same teardown as a stop during playback.
      audition = { stop: halt };
      showNote("fetching " + name + "...", false);
      try {
        // Fetched rather than handed to the element as a src, because a
        // failure has to be READABLE: this route answers a JSON `error` like
        // the rest of the API, and an <audio> pointed at a 400 raises a
        // MediaError carrying no message at all (AC-7b).
        const r = await fetch(previewUrl + encodeURIComponent(name));
        if (!r.ok) {
          const body = await r.json().catch(() => null);
          throw new Error((body && body.error) || ("HTTP " + r.status));
        }
        const blob = await r.blob();
        if (mine !== seq) return;             // stopped while it was in flight
        const url = URL.createObjectURL(blob);
        player = { audio: new Audio(url), url };
        player.audio.onended = () => stopAudition();
        player.audio.onerror = () => {
          stopAudition();
          showNote("the browser could not play that audio", true);
        };
        showNote("playing " + name, false);
        // The microphone is LIVE while this plays, and this is Butler's own
        // voice coming out of the speaker -- so it must not enter the captured
        // audio, exactly as an earcon must not (web/AGENTS.md, D-15, AC-5b).
        // A cue is 100 ms of tone; a preview is a second or two of SPEECH, so
        // the wake detector is the thing at risk rather than the recognizer.
        // Neither this file nor the daemon can see that: it needs the page's
        // own mic graph, so `onAudition` is a callback the mount is given.
        // Duration is read after the metadata lands, because that is the first
        // moment it is known. Stopping early leaves the mute standing for the
        // remainder -- at most the length of one preview, and deliberately the
        // conservative direction: a beat of deafness costs a repeated wake
        // word, and the other way costs Butler answering its own sample.
        if (onAudition) {
          const arm = () => onAudition(
            (Number.isFinite(player.audio.duration)
             ? player.audio.duration * 1000 : 2000) + AUDITION_TAIL_MS);
          if (Number.isFinite(player.audio.duration)) arm();
          else player.audio.onloadedmetadata = arm;
        }
        await player.audio.play();
      } catch (e) {
        if (mine !== seq) return;
        stopAudition();
        showNote("could not preview " + name + ": " + (e.message || e), true);
      }
    }

    if (listen) {
      // Focus on this button must not hold back a value arriving from another
      // device: it writes nothing, so there is nothing to protect (AC-8).
      listen.dataset.nolock = "1";
      listen.onclick = () => {
        if (playing) { stopAudition(); return; }
        preview();
      };
      paintListen();
    }

    return {
      write(v) { input.value = v == null ? "" : v; paintFavs(); },
      repaint: paintFavs,
    };
  }

  function buildList(spec, host) {
    const wrap = el("div", "list");
    const items = el("div", "list-items");
    const add = el("div", "list-add");
    const addInput = el("input");
    addInput.type = "text";
    addInput.placeholder = "add a phrase";
    // A list has no single primary control for the row's <label> to point at,
    // so every input it renders carries its own name (AC-100): this one, and
    // each existing entry below, numbered by position because that is the only
    // thing that tells them apart. Both are built from the schema (AC-41).
    addInput.setAttribute("aria-label", "add to " + (spec.label || spec.key));
    // Exempt from the editing lock: typing a NEW entry must not hold back a
    // remote change to the existing ones, and the add box is never rewritten.
    addInput.dataset.nolock = "1";
    const addBtn = ghostButton("Add");
    let current = [];

    function doAdd() {
      const v = addInput.value.trim();
      if (!v) return;
      addInput.value = "";
      commit(spec.key, current.concat([v]));
    }
    addBtn.onclick = doAdd;
    addInput.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); doAdd(); }
    };
    add.append(addInput, addBtn);
    wrap.append(items, add);
    host.append(wrap);

    // A list whose entries can be PICKED: the schema names a `choices_from`
    // target and the daemon answers its choices, each a plain string or a
    // `{value, label}` pair (a chat id and the name it goes by). The add box
    // offers them and every entry shows its label beside the stored value.
    // Generic over targets: nothing here knows which setting or which list
    // (AC-41). A target that cannot answer says so in the control, loudly,
    // and typing a value still works.
    const labels = new Map();
    if (spec.choices_from) {
      const offer = el("datalist");
      offer.id = "list-choices-" + spec.key;
      addInput.setAttribute("list", offer.id);
      addInput.placeholder = "pick or type";
      const note = el("div", "disc-note");
      wrap.append(offer, note);
      modelsFor(spec.choices_from).then((body) => {
        for (const entry of body.models || []) {
          const value = typeof entry === "string" ? entry : entry.value;
          const label = typeof entry === "string" ? "" : (entry.label || "");
          if (label) labels.set(value, label);
          const o = el("option");
          o.value = value;
          if (label) o.label = label;
          offer.append(o);
        }
        note.textContent = (body.models || []).length + " to choose from";
        note.className = "disc-note";
        paint();
      }).catch((e) => {
        note.textContent = "could not read the choices: " + (e.message || e);
        note.className = "disc-note bad";
      });
    }

    function paint() {
      items.textContent = "";
      current.forEach((v, i) => {
        const line = el("div", "list-item");
        const inp = el("input");
        inp.type = "text";
        inp.value = v;
        inp.setAttribute("aria-label",                        // AC-100
                         (spec.label || spec.key) + " " + (i + 1));
        inp.onchange = () => {
          const next = current.slice();
          next[i] = inp.value;
          commit(spec.key, next);
        };
        const rm = ghostButton("Remove");
        rm.classList.add("tiny");
        rm.onclick = () => {
          const next = current.slice();
          next.splice(i, 1);
          commit(spec.key, next);   // the daemon refuses an empty list, loudly
        };
        line.append(inp);
        if (labels.has(v)) line.append(el("span", "unit", labels.get(v)));
        line.append(rm);
        items.append(line);
      });
    }

    return {
      write(values) {
        current = Array.isArray(values) ? values.slice() : [];
        paint();
      },
    };
  }

  // -- permission mode ------------------------------------------------------ //
  // Deliberately NOT rendered from the settings schema, and deliberately not
  // shaped like the rest of the panel. Everything above is a preference: how
  // long Butler waits before submitting is nobody's security problem. This one
  // decides what Butler is ALLOWED TO DO, so it is the only control here that
  // asks before it acts, and only in the direction that grants more.
  /**
   * Connections: where Butler may ask for a completion (plan.md 4.4b).
   *
   * Hand-built, like the mode block, and it is the carve-out web/AGENTS.md
   * describes: a connection is not a preference with a schema, it has a
   * fixed shape and its own probe. This panel may name its three fields --
   * label, base URL, environment variable NAME -- and nothing else. It never
   * names a model or a setting.
   *
   * A key VALUE is never typed here, never sent and never displayed. What
   * travels is the NAME of an environment variable; the daemon resolves it
   * at call time and drops it (AC-34, AC-37).
   */
  function buildConnections() {
    if (!connRoot) return;
    const list = connRoot.querySelector("#connList");
    const fId = connRoot.querySelector("#connId");
    const fLabel = connRoot.querySelector("#connLabel");
    const fUrl = connRoot.querySelector("#connUrl");
    const fVar = connRoot.querySelector("#connVar");
    const add = connRoot.querySelector("#connAdd");
    const reload = connRoot.querySelector("#connReload");
    const err = connRoot.querySelector("#connErr");

    function fail(message) {
      err.textContent = message;
      err.hidden = false;
    }
    function clearError() { err.textContent = ""; err.hidden = true; }

    function paint(conns) {
      // This block's nav line counts them, and this is the only place the
      // count is known.
      connCount = conns.length;
      paintNav();
      list.textContent = "";
      if (!conns.length) {
        const empty = el("div", "mode-note", "None yet.");
        list.append(empty);
        return;
      }
      for (const c of conns) {
        const row = el("div", "connrow");
        const it = el("div", "it");
        it.append(el("span", "cname", c.label || c.id));
        row.append(it);
        // Whether a key is involved and WHERE it comes from. Never what it
        // is -- the daemon does not send one, and this line is the whole
        // reason the owner can trust that.
        const bits = [c.base_url];
        if (c.auth_from && c.auth_from !== "none") bits.push("key: " + c.auth_from);
        if (c.origin === "pi") bits.push("from pi");
        if (c.missing) bits.push("MISSING");
        const meta = el("span", "cmeta", bits.join("  .  "));
        it.append(meta);

        // The probe plan.md 4.4b asks for. The model pickers already report
        // reachability, but only for a connection you have SELECTED there --
        // and the moment you most want to know is right after adding one.
        // Reuses GET /api/models, so this tests the path Butler actually
        // uses rather than a separate liveness check that could disagree.
        const test = ghostButton("Test");
        test.onclick = () => {
          meta.textContent = "asking " + c.id + "...";
          fetch(MODELS_URL + "?connection=" + encodeURIComponent(c.id))
            .then(async (r) => {
              const answer = await r.json().catch(() => null);
              if (!r.ok) throw new Error((answer && answer.error)
                                         || ("HTTP " + r.status));
              meta.textContent = (answer.models || []).length
                + " models served";
            })
            .catch((e) => {
              // In the row, not a dialog: this is where the owner is looking.
              meta.textContent = "no answer: " + (e.message || e);
            });
        };
        row.append(test);

        if (c.origin !== "pi") {
          const drop = ghostButton("Remove");
          drop.onclick = () => send("DELETE", "/api/connections/"
            + encodeURIComponent(c.id));
          row.append(drop);
        }
        list.append(row);
      }
    }

    function reloadList() {
      connectionsPromise = null;          // this panel just changed the list
      allConnections().then(paint).catch((e) =>
        fail("could not read connections: " + (e.message || e)));
    }

    async function send(method, url, body) {
      clearError();
      try {
        const r = await fetch(url, {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
        });
        const answer = await r.json().catch(() => null);
        if (!r.ok) throw new Error((answer && answer.error) || ("HTTP " + r.status));
        // The model pickers read per-connection lists; a changed connection
        // set makes every one of those memos stale.
        // The list here, and every model picker's connection selector --
        // `refresh()` alone does not rebuild rows when only values changed,
        // so the selectors would keep the set they were built with.
        connectionsChanged();
        reloadList();
      } catch (e) {
        fail((e.message || e) + "");
      }
    }

    add.onclick = () => {
      const id = fId.value.trim();
      const url = fUrl.value.trim();
      if (!id || !url) { fail("id and base URL are required"); return; }
      const varName = fVar.value.trim();
      send("POST", "/api/connections", {
        id,
        label: fLabel.value.trim() || id,
        base_url: url,
        // The NAME, never a value. There is no field on this form that could
        // carry a key, which is the point rather than an oversight.
        auth: varName ? { from: "env", var: varName } : { from: "none" },
      }).then(() => { fId.value = fLabel.value = fUrl.value = fVar.value = ""; });
    };
    reload.onclick = () => send("POST", "/api/connections/reload");

    reloadList();
  }

  function buildMode() {
    if (!modeRoot) return;
    const seg = modeRoot.querySelector("#modeSeg");
    const err = modeRoot.querySelector("#modeErr");
    const confirm = modeRoot.querySelector("#modeConfirm");
    const confirmText = modeRoot.querySelector("#modeConfirmText");
    const yes = modeRoot.querySelector("#modeYes");
    const no = modeRoot.querySelector("#modeNo");
    if (!seg) return;

    seg.querySelectorAll("button[data-mode]").forEach((b) => {
      b.onclick = () => {
        const to = b.dataset.mode;
        if (!to || to === serverMode) return;
        err.hidden = true;
        if (rank(to) > rank(serverMode)) {
          // A raise is the only click on this page that gives Butler more
          // room. It gets a second look; lowering never does, because
          // refusing a lockdown is never the safe answer.
          pendingRaise = to;
          confirmText.textContent =
            `Raise ${serverMode} to ${to}? This gives Butler more room to act `
            + "on its own, on every device at once.";
          confirm.hidden = false;
          return;
        }
        sendMode(to);
      };
    });

    if (yes) yes.onclick = () => {
      const to = pendingRaise;
      pendingRaise = null;
      confirm.hidden = true;
      if (to) sendMode(to);
    };
    if (no) no.onclick = () => { pendingRaise = null; confirm.hidden = true; showMode(serverMode); };

    async function sendMode(to) {
      try {
        const r = await call(MODE_URL, { mode: to });
        if (!r.ok) {
          const msg = (r.body && r.body.error) || ("daemon refused with HTTP " + r.status);
          err.textContent = msg;
          err.hidden = false;
          note("mode: " + msg);
          showMode(serverMode);        // never leave a refused mode on screen
          return;
        }
        // The daemon also broadcasts `mode` to every satellite; applying the
        // answer here too means the button is right even with the socket down.
        showMode((r.body && r.body.mode) || to);
      } catch (e) {
        err.textContent = "could not reach the daemon: " + (e.message || e);
        err.hidden = false;
        showMode(serverMode);
      }
    }
  }

  function rank(mode) {
    const i = MODE_ORDER.indexOf(mode);
    return i < 0 ? -1 : i;
  }

  function showMode(mode) {
    if (!mode || !modeRoot) return;
    serverMode = mode;
    // The header carries the mode too, and it is the only part of it
    // visible with the panel shut. Told from here rather than fetched
    // again, so the two can never disagree about which mode is current.
    if (onMode) onMode(mode);
    const here = rank(mode);
    modeRoot.querySelectorAll("button[data-mode]").forEach((b) => {
      const there = rank(b.dataset.mode);
      const is = b.dataset.mode === mode;
      b.setAttribute("aria-pressed", is ? "true" : "false");
      b.classList.toggle("up", there > here);
      const dir = b.querySelector("[data-dir]");
      // What pressing it would do: lowering applies at once, raising asks
      // first (the confirm below), and the current one is said as such.
      if (dir) dir.textContent = is ? "Current" : (there > here ? "Asks first" : "Applies at once");
    });
    paintNav();           // this block's nav line shows the mode it is in
  }

  // The permission mode arrives on the socket in `ready` -- but the socket is
  // only opened when Start is pressed, and the mode is the one thing worth
  // knowing BEFORE you say anything. So read it over HTTP at load too, and the
  // buttons are never blank on a page nobody has started yet.
  async function readMode() {
    try {
      const r = await fetch(MODE_URL);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const body = await r.json();
      if (body && body.mode) showMode(body.mode);
    } catch (e) {
      note("permission mode unavailable: " + (e.message || e));
    }
  }

  // The search box. Its index is rebuilt with the rows, so a query that is
  // still in the box when the schema changes is re-applied at the end of
  // build() rather than left pointing at rows that no longer exist.
  if (search) search.addEventListener("input", () => applySearch(search.value));
  if (searchClear) {
    searchClear.hidden = true;
    searchClear.onclick = () => {
      search.value = "";
      applySearch("");
      search.focus();
    };
  }

  buildMode();
  buildConnections();
  refresh();
  readMode();

  // `set` and `resetAll` exist for the two controls that live OUTSIDE this
  // panel -- the theme toggle in the header, and the panel's own reset button.
  // They go through `commit`, the same path a rendered control uses, so a
  // refusal lands on the right row with the daemon's own message and the
  // header cannot end up showing something the daemon rejected. A shortcut
  // that PUT the value directly would be a second write path with no error
  // surface, which is how a control starts lying about what it did.
  //
  // `focusSearch` is the same arrangement for the control the SHEET owns but
  // app.js drives: the `/` shortcut, which is a global key. `reveal` is
  // app.js opening the
  // sheet: on a setting's page when a picker's door names one, else where the
  // reader left it (on a phone, the category list).
  //
  // `stopAudition` is that arrangement for AC-109's one real hazard: an
  // audition is not a turn, so nothing here knows a turn has begun, and a
  // preview left running under Butler's own reply is two voices in the room.
  // app.js holds the turn state and calls this; the teardown itself stays
  // here, with the Audio element it created (docs/voice-control.md 7).

  // RESET ALL confirms with a SECOND PRESS, mirroring the sidebar's own
  // delete idiom (app.js `chatDropButton`, "delete?") rather than
  // `window.confirm` (item 17, UI review 2026-09-26). app.js's own onclick
  // calls `resetAll()` on every press unconditionally, so the two-press
  // state lives entirely inside the one function that press already reaches.
  // `#resetAll` sits at the foot of the nav, outside every root this module
  // is handed (`root` is `#settingsRoot`, `navRoot` the list above it) -- the
  // one place this file reaches for an element by id rather than by parameter.
  const RESET_ARM_MS = 4000;
  // The sidebar delete idiom's own guard, mirrored (app.js `chatDropButton`,
  // DROP_MIN_GAP_MS): a dblclick, two taps, or a held Enter's repeated clicks
  // must not commit what only the first press armed
  // (docs/e2e-journeys.md S6c, item 1).
  const RESET_MIN_GAP_MS = 500;
  let resetArmTimer = 0;
  let resetArmedAt = 0;
  const resetKeyGuard = document.getElementById("resetAll");
  // The button's resting word is the markup's, put back after an arm.
  const RESET_WORD = resetKeyGuard ? resetKeyGuard.textContent : "";
  if (resetKeyGuard) {
    // A held Enter/Space fires one "click" per repeated keydown. Suppressed
    // here, before the browser turns it into a click at all.
    resetKeyGuard.addEventListener(
      "keydown", (e) => { if (e.repeat) e.preventDefault(); });
  }
  function armedResetAll(e) {
    const btn = document.getElementById("resetAll");
    if (!btn) { reset(null); return; }
    // A dblclick's SECOND click carries `detail === 2` -- ignored outright,
    // the same rule as the sidebar's delete control.
    if (e && e.detail > 1) return;
    if (btn.dataset.armed) {
      if (Date.now() - resetArmedAt < RESET_MIN_GAP_MS) return;
      clearTimeout(resetArmTimer);
      delete btn.dataset.armed;
      btn.textContent = RESET_WORD;
      reset(null);
      return;
    }
    resetArmedAt = Date.now();
    btn.dataset.armed = "1";
    btn.textContent = "Reset all? Press again";
    clearTimeout(resetArmTimer);
    resetArmTimer = setTimeout(() => {
      delete btn.dataset.armed;
      btn.textContent = RESET_WORD;
    }, RESET_ARM_MS);
  }

  return {
    refresh, applyChanged, showMode, stopAudition,
    focusSearch: () => { if (search) { search.focus(); search.select(); } },
    set: (key, value) => commit(key, value),
    resetAll: armedResetAll,
    reveal,
  };
}
