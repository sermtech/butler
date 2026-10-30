// THE LIBRARY: recipes, automations and tasks, per project (docs/
// automations.md phase 2, AC-449, AC-450, AC-452, AC-466, AC-467). The
// approved look is web/mocks/w1-calm-library.html (docs/lantern-ui.md 5.2,
// Step 6a): the Library tab of the one right panel and its dialogs.
//
// A right-hand panel on a desk -- sharing `.app`'s third column with the
// file explorer, ONE AT A TIME (AU-10): which one is `right_panel` in the
// shortcuts store, held by app.js and read here through `env.rightPanel()`
// -- and a sheet on a phone, a gesture that never writes (D-2). It shows the
// project of the conversation ON SCREEN plus the Global section, and follows
// it: `env.projectId()` is app.js's `currentProjectId()`, so there is no
// second way to name a project (AC-311's rule, AC-467).
//
// EVERY RULE IS THE DAEMON'S. This file draws `GET /api/library` and writes
// through the recipe and task routes; it never fills a placeholder -- Run,
// Start and the slash picker all ask `POST /api/recipes/{rid}/expand`, THE
// ONE EXPANSION (AU-3, AC-448, D-18) -- and it never births a chat itself:
// `env.runInNewChat` is app.js's own New chat plus an ordinary `typed`
// frame (AU-4, AC-449), so a run is the device's own turn.
//
// EVERY WORD ABOUT TIME AND OUTCOME IS SERVED (docs/lantern-ui.md section 8):
// an automation's `nextWords`, a run's `statusWord` and `group` (drawn by
// automations.js `mark`), the heartbeat's `awakeWords`, a recipe's `usedBy`
// and `placeholders`, a task's `agent`. This file words no date and parses
// no prompt: a placeholder is found by looking up the SERVED token text
// (`tokens`), never by reading braces itself.
//
// RECIPE TEXT, TASK TEXT AND NAMES ARE UNTRUSTED (a task may be a model's,
// AU-9). Every string reaches the DOM through `textContent` and every element
// through `createElement`; there is no markup sink in this file.
//
// AUTOMATIONS (phase 5) are drawn here from the same answer; the editor and
// the full Tasks & Automations page are `automations.js`, reached through
// `env.automations()`.
//
// THE HEARTBEAT (docs/proactive.md PR-8, PR-9) is the Automations tab's
// pinned first card and its own inline editor: interval, switch, checklist.
// One write, POST /api/heartbeat, which creates the project's one heartbeat
// on first save. The presets and the checklist's item count are the daemon's
// (`heartbeatMinutes`, `checklistItems`); the page counts nothing itself.

export function mountLibrary(env) {
  const $ = (id) => document.getElementById(id);

  let sheet = false;              // the phone's, a gesture
  let shown = false;              // what was last painted
  let project = null;             // the row id whose library is drawn
  let doc = null;                 // the daemon's last answer for `project`
  let error = "";                 // the daemon's sentence, if the read failed
  let seq = 0;                    // late answers for another project dropped
  let tab = "tasks";
  let adding = null;              // the section key an add row is open in
  let editing = null;             // the task id being edited
  let quick = "";                 // the quick-add box's draft, kept over a redraw
  const doneOpen = new Set();     // section keys whose Done fold is open
  let recipe = null;              // the recipe the editor holds (null = new)
  let editScope = "";             // the editor's chosen scope
  let runWith = null;             // {recipe, mode, anchor, pid} for values
  let previewTimer = 0;
  let slashItems = [];
  let slashAt = 0;
  let beat = null;                // the heartbeat editor's draft, or null
  let rem = null;                 // the reminder editor's draft, or null

  // ---- the network: ONE helper ------------------------------------------ //
  async function api(method, path, body) {
    const init = { method };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    const r = await fetch(path, init);
    const answer = await r.json().catch(() => ({}));
    // The daemon's own sentence, which names the rule that refused.
    if (!r.ok) throw new Error(answer.error || ("HTTP " + r.status));
    return answer;
  }

  function expand(rid, values) {
    return api("POST", "/api/recipes/" + encodeURIComponent(rid) + "/expand",
               { values: values || {} });
  }

  // ---- small DOM helpers ------------------------------------------------- //
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function button(cls, text, label) {
    const b = el("button", cls, text);
    b.type = "button";
    if (label) b.setAttribute("aria-label", label);
    return b;
  }
  const ICON_CARET = [["path", { d: "M9 6l6 6-6 6" }]];
  const ICON_TICK = [["path", { d: "M5 12.5l4.5 4.5L19 7" }]];
  const ICON_PLUS = [["path", { d: "M12 5v14M5 12h14" }]];
  const ICON_PLAY = [["path", { d: "M7 5.5v13l11-6.5z" }]];
  const ICON_EDIT = [["path", { d: "M12 20h9" }],
                     ["path", { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" }]];
  const ICON_CLOCK = [["circle", { cx: "12", cy: "12", r: "9" }],
                      ["path", { d: "M12 7v5l3 2" }]];
  const ICON_BELL = [["path", { d: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" }],
                     ["path", { d: "M10 20.5a2 2 0 0 0 4 0" }]];
  const ICON_PULSE = [["path", { d: "M2 12h4.5l3-7.5 4.5 15 3-7.5h5" }]];
  const ICON_RECIPE = [["path", { d: "M6 3h9l3 3v15H6z" }],
                       ["path", { d: "M9 10h6M9 14h6M9 18h3" }]];
  const ICON_TASKS = [["path", { d: "M9 6h11M9 12h11M9 18h11" }],
                      ["path", { d: "M3.5 6l1 1 2-2M3.5 12l1 1 2-2M3.5 18l1 1 2-2" }]];

  function rowName(id) {
    const row = id ? env.projectRow(id) : null;
    return row ? env.projectName(row) : "";
  }
  function isRootProject() { return !!(doc && doc.isRoot); }
  function rootId() { return (doc && doc.root) || ""; }

  // ---- served placeholders, FOUND, never parsed (AU-3) -------------------- //
  // The daemon is the one parser of a prompt. A placeholder it serves is
  // `{name, default}`; its token text is looked up in `text` with `indexOf`
  // and handed to `draw`, the rest is plain text. A placeholder typed since
  // the last save is not in the served list, so it is not marked until then.
  function tokenText(p) {
    return "{" + p.name + (p.default == null ? "" : "=" + p.default) + "}";
  }
  function tokens(parent, text, placeholders, draw) {
    const wanted = placeholders.map((p) => [tokenText(p), p]);
    let at = 0;
    for (;;) {
      let found = -1;
      let hit = null;
      for (const [t, p] of wanted) {
        const i = text.indexOf(t, at);
        if (i >= 0 && (found < 0 || i < found)) { found = i; hit = [t, p]; }
      }
      if (found < 0) break;
      if (found > at) parent.append(document.createTextNode(text.slice(at, found)));
      parent.append(draw(hit[0], hit[1]));
      at = found + hit[0].length;
    }
    if (at < text.length) parent.append(document.createTextNode(text.slice(at)));
  }
  function chip(p) {
    return el("span", "ph", p.default == null ? p.name : p.name + " = " + p.default);
  }

  // ---- showing and hiding (AC-466) --------------------------------------- //
  function deskOpen() { return env.rightPanel() === "library"; }
  function visible() { return env.isPhone() ? sheet : deskOpen(); }

  function paint() {
    const show = visible();
    $("libraryPanel").hidden = !show;
    document.documentElement.dataset.library = show ? "open" : "shut";
    // The header's panel toggle, the sidebar's Library row and the tabs are
    // app.js's to paint, from BOTH panels at once (lantern-ui AC-699).
    env.painted();
    if (show && !shown) load();
    shown = show;
  }

  // The header's panel toggle, on screen at every width.
  function focusOpener() { $("panelBtn").focus(); }

  function afterShowChange(viaKey) {
    const inside = $("libraryPanel").contains(document.activeElement);
    paint();
    if (!visible()) {
      if (inside) focusOpener();
      return;
    }
    // Into the panel on the selected TAB (its one tab stop), when the
    // keyboard opened it or it is the phone's covering sheet. Not the close
    // button: its hover-help would sit over the tabs (MEASURED at 390).
    if (viaKey || env.isPhone()) {
      const on = $("libraryPanel").querySelector('[role="tab"][aria-selected="true"]');
      (on || $("libraryClose")).focus();
    }
  }

  // The desk writes `right_panel` through app.js, which repaints BOTH panels
  // from the one value; the phone's sheet is a gesture and writes nothing.
  function toggle(viaKey) {
    if (env.isPhone()) {
      setSheet(!sheet, viaKey);
      return;
    }
    env.setRight(deskOpen() ? "" : "library", viaKey);
  }

  function setSheet(open, viaKey) {
    sheet = open;
    if (open) { env.closeDrawer(); env.closePops(); env.closeOtherSheet(); }
    afterShowChange(viaKey);
  }
  function sheetOpen() { return env.isPhone() && sheet; }
  function closeSheet() { if (sheet) setSheet(false, false); }
  function rightChanged(viaKey) { afterShowChange(viaKey); }

  // ---- following the conversation (AC-467) ------------------------------- //
  function follow() {
    const id = env.projectId();
    if (id !== project) {
      project = id;
      seq += 1;
      doc = null;
      error = "";
      adding = null;
      editing = null;
      quick = "";
      beat = null;
      rem = null;
      if (visible()) load();
    }
    render();
    paint();
  }

  // The `library` frame: a hint that something in `m.project` ("" = the
  // Global section) changed. An open panel re-reads; a shut one reads when it
  // opens (AC-467).
  function changed(m) {
    if (!visible() && !slashOpen()) { doc = null; return; }
    if (m.project && m.project !== project) return;
    load();
  }

  async function load() {
    if (!project) { render(); return; }
    const mine = ++seq;
    try {
      const got = await api("GET", "/api/library?project="
                            + encodeURIComponent(project));
      if (mine !== seq) return;
      doc = got;
      error = "";
    } catch (e) {
      if (mine !== seq) return;
      doc = null;
      error = e.message;
    }
    render();
    if (slashOpen()) paintSlash();
  }

  // ---- drawing ------------------------------------------------------------ //
  const FULL_LEAD = { recipes: "Every project:", automations: "Every project:",
                      tasks: "Tasks in every project:" };

  function render() {
    for (const b of document.querySelectorAll("#libraryPanel [role='tab']")) {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    const open = doc ? doc.tasks.project.open.length
      + doc.tasks.global.open.length : 0;
    $("libTaskCount").textContent = open ? String(open) : "";
    $("libraryFullLead").textContent = FULL_LEAD[tab];
    const body = $("libraryBody");
    // An open heartbeat editor is never rebuilt under the owner's typing by
    // a `library` frame; it is drawn once, on open.
    if (tab === "automations" && beat && beat.node
        && body.contains(beat.node)) return;
    if (tab === "automations" && rem && rem.node
        && body.contains(rem.node)) return;
    const keep = body.scrollTop;
    const typing = document.activeElement && document.activeElement.id === "libQuickAdd";
    body.replaceChildren();
    if (error) { body.append(el("p", "lnote lberr", error)); return; }
    if (!doc) { body.append(el("p", "lnote", project ? "Loading..." : "No project.")); return; }
    if (tab === "recipes") drawRecipes(body);
    else if (tab === "automations") drawAutomations(body);
    else drawTasks(body);
    body.scrollTop = keep;
    if (typing && $("libQuickAdd")) $("libQuickAdd").focus();
  }

  function sectionTitle(body, title, what) {
    const h = el("div", "ptitle");
    h.append(el("b", "", title), el("span", "", what));
    body.append(h);
  }

  function projectTitle() { return rowName(project) || "This project"; }

  function addButton(text, onclick) {
    const b = button("lbadd2");
    b.append(env.icon(15, 2, ICON_PLUS), document.createTextNode(text));
    b.onclick = onclick;
    return b;
  }

  // A card's action: a word, with an icon when the export draws one.
  function solid(svg) {
    svg.classList.add("solid");
    return svg;
  }
  function actButton(text, shapes, label, cls) {
    const b = button("lbbtn2" + (cls ? " " + cls : ""), undefined, label);
    if (shapes) b.append(solid(env.icon(13, 2, shapes)));
    b.append(document.createTextNode(text));
    return b;
  }

  // An icon-only control: one or two words of hover help (the page's D8).
  function iconButton(shapes, label, help) {
    const b = button("ib", undefined, label);
    b.dataset.help = help;
    b.append(env.icon(16, 1.7, shapes));
    return b;
  }

  // EMPTY STATES invite a first step: an icon, one line in the display
  // face, what the thing is, one primary action and up to two suggestions.
  function lempty(shapes, title, text, action, onclick, sugs) {
    const box = el("div", "lempty");
    box.append(env.icon(40, 1.4, shapes), el("h3", "", title), el("p", "", text));
    if (action) {
      const b = button("lbbtn2 primary");
      b.append(env.icon(14, 2, ICON_PLUS), document.createTextNode(action));
      b.onclick = onclick;
      box.append(b);
    }
    if (sugs && sugs.length) {
      const list = el("div", "sugs");
      for (const [bold, rest, fn] of sugs) {
        const s = button("sugg");
        s.append(el("b", "", bold), document.createTextNode(rest));
        s.onclick = fn;
        list.append(s);
      }
      box.append(list);
    }
    return box;
  }

  // The full Tasks & Automations page, on one automation's history.
  function openFull(select) {
    if (env.isPhone()) closeSheet();
    env.automations().openPage("automations", select);
  }

  // -- recipes -- //
  function drawRecipes(body) {
    const mine = doc.recipes.project;
    if (mine.length || doc.recipes.global.length) {
      body.append(el("p", "lnote", "Type / in the message box to use one."));
    }
    if (mine.length) {
      sectionTitle(body, projectTitle(), "this project");
      for (const r of mine) body.append(recipeRow(r, project));
      body.append(addButton("New recipe", () => openEditor(null, project)));
    } else {
      body.append(lempty(ICON_RECIPE, "No recipes in " + projectTitle() + " yet",
        "A recipe is a prompt you reuse. Give it a name, then type / and the "
        + "name in any chat, or say \"run recipe\" and its name.",
        "New recipe", () => openEditor(null, project)));
    }
    sectionTitle(body, "Global", "every project");
    for (const r of doc.recipes.global) body.append(recipeRow(r, ""));
    if (!doc.recipes.global.length) {
      body.append(el("p", "lnote", "No recipe is offered in every project yet."));
    }
    body.append(addButton("New recipe", () => openEditor(null, "global")));
    if (doc.removed.recipes.length) {
      sectionTitle(body, "Project removed", "kept, not deleted");
      for (const r of doc.removed.recipes) body.append(recipeRow(r, null));
    }
  }

  // "/" + the recipe's NAME (lantern-ui section 8: no slug), the prompt's
  // first line with the served placeholders as chips, what uses it, then
  // Run and Edit.
  function recipeRow(r, runIn) {
    const slash = "/" + r.name;
    const row = el("div", "lbrecipe");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", slash);
    const tx = el("div", "tx");
    const line = el("div", "l1");
    const v = el("span", "v");
    if (r.problem) v.textContent = r.problem;
    else tokens(v, firstLine(r.prompt), r.placeholders, (_t, p) => chip(p));
    line.append(el("span", "k", slash), v);
    tx.append(line);
    const n = r.usedBy.length;
    if (n) tx.append(el("span", "used", "Used by " + n + " automation" + (n === 1 ? "" : "s")));
    row.append(tx);
    // A recipe of a REMOVED project has nowhere to run; it can be edited
    // (and re-scoped) but not run (AU-2).
    if (runIn !== null && !r.problem) {
      const run = iconButton(ICON_PLAY, "Run " + slash + " in a new chat", "Run");
      solid(run.firstChild);
      // A GLOBAL recipe runs in the project on screen (AC-449).
      run.onclick = () => startRun(r, runIn || project, run);
      row.append(run);
    }
    const edit = iconButton(ICON_EDIT, "Edit " + slash, "Edit");
    edit.onclick = () => openEditor(r, r.project);
    row.append(edit);
    return row;
  }

  function firstLine(text) {
    const line = String(text || "").split("\n").find((l) => l.trim()) || "";
    return line.length > 90 ? line.slice(0, 90) + "..." : line;
  }

  // -- automations (phase 5) -- //
  function drawAutomations(body) {
    const autos = doc.automations;
    if (!autos || !autos.built) {
      body.append(el("p", "lnote", "This daemon keeps no automations."));
      return;
    }
    if (rem) { drawRem(body); return; }
    // On Butler's own project its automations ARE the global ones.
    const here = isRootProject() ? autos.global : autos.project;
    const pid = isRootProject() ? rootId() : project;
    sectionTitle(body, projectTitle(), isRootProject() ? "this project, global"
                                                       : "this project");
    if (!autos.heartbeat && !beat && !here.length) {
      body.append(lempty(ICON_CLOCK, "Nothing in " + projectTitle() + " runs on its own",
        "An automation runs a recipe on a schedule, in a new chat of its own, "
        + "and tells you how it went.",
        "New automation", () => env.automations().openEditor(null, pid),
        [["Set up a heartbeat", ", a short checklist checked every "
          + autos.heartbeatDefault + " min", () => openBeat(null, autos)],
         ["New reminder", ", said and sent at a time you pick",
          () => openRem(null, pid)]]));
    } else {
      body.append(beatRow(autos.heartbeat, autos));
      for (const a of here) body.append(automationRow(a));
      body.append(addButton("New automation",
                            () => env.automations().openEditor(null, pid)));
      body.append(addButton("New reminder", () => openRem(null, pid)));
    }
    if (!isRootProject()) {
      sectionTitle(body, "Global", "Butler's own project");
      for (const a of autos.global) body.append(automationRow(a));
      if (!autos.global.length) {
        body.append(el("p", "lnote", "Nothing global runs on its own yet."));
      }
      body.append(addButton("New automation",
                            () => env.automations().openEditor(null, rootId())));
      body.append(addButton("New reminder", () => openRem(null, rootId())));
    }
    if (autos.removed.length) {
      sectionTitle(body, "Project removed", "kept, skipped as unavailable");
      for (const a of autos.removed) body.append(automationRow(a));
    }
  }

  // The last run, in the daemon's word and shape; a button that opens the
  // run's chat when it has one.
  function lastRun(run, lead, none, name) {
    const mark = env.automations().mark(run, lead, none);
    if (!run || !run.chat) {
      const span = el("span", "lblast");
      span.append(mark);
      return span;
    }
    const b = button("lblast", undefined, "Open the last run of " + name);
    b.append(mark);
    b.onclick = () => env.openChat(run.chat);
    return b;
  }

  // A card: the switch, the name, when it runs NEXT in the daemon's words
  // (`nextWords`), its last result, then Run now, Past runs and Edit.
  function automationRow(a) {
    const au = env.automations();
    const name = a.recipeName || "(recipe deleted)";
    // A reminder (docs/proactive.md PR-17) is the owner's own words and is
    // edited in its own editor below.
    const isRem = a.kind === "reminder";
    const card = el("div", "lbcard" + (a.enabled ? "" : " off"));
    const top = el("div", "top");
    top.append(env.icon(16, 1.6, isRem ? ICON_BELL : ICON_CLOCK), el("b", "", name),
               au.sw(a.enabled, name + " on", (on) => act(
                 () => au.setEnabled([a.id], on))));
    card.append(top, el("span", "sub", (isRem ? "Reminder. " : "") + a.nextWords));
    if (a.problem) card.append(el("span", "sub lberr", a.problem));
    card.append(lastRun(a.last, "Last run ", "Not run yet", name));
    const acts = el("div", "acts");
    const run = actButton("Run now", ICON_PLAY, "Run " + name + " now");
    run.onclick = () => act(() => au.runNow(a.id));
    const past = actButton("Past runs", null, "Past runs of " + name);
    past.onclick = () => openFull(a.id);
    const edit = actButton("Edit", null, (isRem ? "Edit reminder " : "Edit automation ")
                           + name, "q");
    edit.onclick = () => (isRem ? openRem(a, a.project) : au.openEditor(a));
    acts.append(run, past, edit);
    card.append(acts);
    return card;
  }

  // -- the heartbeat (docs/proactive.md PR-8..PR-13) -- //
  // Pinned first and always there: before its first save it is an offer to
  // set one up, with no switch, because nothing exists to switch. Its words
  // are the daemon's: the schedule's label, `awakeWords` (quiet hours,
  // which the page may not name) and the checklist's served count.
  function beatRow(hb, autos) {
    const au = env.automations();
    const card = el("div", "lbcard beat" + (hb && !hb.enabled ? " off" : ""));
    const top = el("div", "top");
    top.append(env.icon(16, 1.6, ICON_PULSE), el("b", "", "Heartbeat"));
    if (beat) {
      // The open editor's switch is its draft, saved by Save.
      const sw = au.sw(beat.enabled, "Heartbeat on", (v) => {
        beat.enabled = v;
        sw.setAttribute("aria-checked", String(v));
      });
      top.append(sw);
    } else if (hb) {
      top.append(au.sw(hb.enabled, "Heartbeat on", (on) => act(
        () => au.setEnabled([hb.id], on))));
    }
    card.append(top);
    if (!hb) {
      card.append(el("span", "sub", "Not set up yet: a short checklist of your "
        + "own, checked every " + autos.heartbeatDefault + " min."));
    } else {
      const n = hb.checklistItems;
      card.append(el("span", "sub", hb.schedule.label
        + (hb.awakeWords ? " " + hb.awakeWords : "") + ", "
        + (n ? n + " checklist line" + (n === 1 ? "" : "s")
             : "empty checklist, no model call")));
      if (hb.problem) card.append(el("span", "sub lberr", hb.problem));
      card.append(lastRun(hb.last, "Last check ", "Not checked yet", "the heartbeat"));
    }
    if (beat) {
      beat.node = card;
      drawBeat(card, autos);
      return card;
    }
    const acts = el("div", "acts");
    const open = actButton(hb ? "Edit checklist" : "Set up", null,
                           hb ? "Edit the heartbeat checklist" : "Set up a heartbeat");
    open.onclick = () => openBeat(hb, autos);
    acts.append(open);
    if (hb) {
      const past = actButton("Past checks", null, "Past heartbeat checks", "q");
      past.onclick = () => openFull(hb.id);
      acts.append(past);
    }
    card.append(acts);
    return card;
  }

  function openBeat(hb, autos) {
    const live = hb || autos.heartbeat;
    beat = { minutes: live ? live.schedule.preset.minutes : autos.heartbeatDefault,
             enabled: live ? live.enabled : true, text: live ? live.checklist : "",
             hb: live, node: null };
    tab = "automations";
    render();
    const box = $("beatChecklist");
    if (box) {
      box.focus();
      box.setSelectionRange(0, 0);
      box.scrollTop = 0;
    }
  }

  function closeBeat() {
    beat = null;
    render();
  }

  // Inside the card (the mock's place): interval presets (the daemon's list),
  // the checklist, when it checks next in the daemon's words, Cancel and Save.
  function minutesWord(m) {
    return m > 60 && m % 60 === 0 ? m / 60 + " h" : m + " min";
  }
  function drawBeat(card, autos) {
    const hb = beat.hb;
    const ed = el("div", "bed");
    const every = el("div", "");
    every.append(el("span", "lab", "Check every"));
    const seg = el("div", "lbseg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "Check every");
    for (const m of autos.heartbeatMinutes) {
      const b = button("", minutesWord(m), m + " minutes"
        + (m === autos.heartbeatDefault ? ", the default" : ""));
      b.setAttribute("aria-pressed", String(m === beat.minutes));
      b.onclick = () => {
        beat.minutes = m;
        for (const x of seg.children) {
          x.setAttribute("aria-pressed", String(x === b));
        }
      };
      seg.append(b);
    }
    every.append(seg);

    const list = el("div", "");
    const label = el("label", "lab", "Checklist");
    const box = el("textarea", "");
    box.id = "beatChecklist";
    label.htmlFor = box.id;
    box.rows = 6;
    box.spellcheck = false;
    box.value = beat.text;
    box.placeholder = "- check my inbox folder for new invoices";
    box.oninput = () => { beat.text = box.value; };
    list.append(label, box);

    const note = el("p", "lnote", (hb && hb.enabled ? hb.nextWords
      + (hb.awakeWords ? ", " + hb.awakeWords : "") + ". " : "")
      + "Your own list, one item per line. An empty checklist makes no model call.");
    const err = el("p", "lnote lberr");
    err.hidden = true;
    const row = el("div", "row2");
    const cancel = actButton("Cancel", null, "Close without saving", "q");
    cancel.onclick = () => closeBeat();
    const save = actButton("Save", null, "Save the heartbeat", "primary");
    save.onclick = () => saveBeat(err);
    row.append(cancel, save);
    ed.append(every, list, note, err, row);
    card.append(ed);
  }

  async function saveBeat(err) {
    try {
      await api("POST", "/api/heartbeat", { project, checklist: beat.text,
                                            minutes: beat.minutes,
                                            enabled: beat.enabled });
    } catch (e) {
      // The daemon's sentence, naming the rule; the draft stays as typed.
      err.textContent = e.message;
      err.hidden = false;
      return;
    }
    beat = null;
    env.toast("Saved");
    await load();
  }

  // -- reminders (docs/proactive.md PR-17..PR-20; mock: proactive-p0.html 2) -- //
  // Text, then ONE date and time or a repeat. Every rule is the daemon's:
  // the page sends `runAt` or a preset schedule to POST /api/automations
  // and draws the refusal it gets back. No model runs when one fires.
  const REM_DAYS = [["MON", "Mon"], ["TUE", "Tue"], ["WED", "Wed"],
                    ["THU", "Thu"], ["FRI", "Fri"], ["SAT", "Sat"],
                    ["SUN", "Sun"]];
  // The date and time INPUTS' starting values are the DAEMON's machine
  // values (Step 6b: this file reads no clock): a one-shot's own `runAtInput`,
  // else the panel's `remindInput` (an hour from now), both in the zone the
  // daemon reads `runAt` in.
  function openRem(a, pid) {
    const p = a && a.schedule ? a.schedule.preset : null;
    const when = (a && a.runAtInput) || doc.automations.remindInput;
    rem = { a, project: pid || rootId(), text: a ? a.text : "",
            once: !(a && a.schedule && a.schedule.cron),
            date: when.date,
            time: p && p.time ? p.time : when.time,
            every: p ? (p.preset === "weekly" || p.preset === "weekdays"
              ? "week" : p.preset === "monthly" ? "month" : "day") : "day",
            days: p && p.days ? [...p.days] : p && p.preset === "weekdays"
              ? ["MON", "TUE", "WED", "THU", "FRI"] : ["MON"],
            nth: p && p.nth ? p.nth : 1, day: p && p.day ? p.day : "MON",
            channels: a ? [...a.channels] : null, node: null };
    tab = "automations";
    render();
    const box = $("libraryBody").querySelector("#remText");
    if (box) box.focus();
  }

  function closeRem() {
    rem = null;
    render();
  }

  function seg(label, options, isOn, onpick) {
    const g = el("div", "lbseg");
    g.setAttribute("role", "group");
    g.setAttribute("aria-label", label);
    for (const [value, text] of options) {
      const b = button("", text);
      b.setAttribute("aria-pressed", String(isOn(value)));
      b.onclick = () => onpick(value);
      g.append(b);
    }
    return g;
  }

  function remField(label, control) {
    const f = el("div", "lbf");
    f.style.gridTemplateColumns = "minmax(0, 1fr)";
    f.append(el("span", "lab", label), control);
    return f;
  }

  async function drawRem(body) {
    const wrap = el("div", "lbcard lbbeat");
    rem.node = wrap;
    const err = el("p", "lberr");
    err.hidden = true;
    const head = el("div", "lbrow");
    const title = el("div", "lbamain");
    title.append(el("span", "nm", rem.a ? "Reminder" : "New reminder"),
                 el("span", "sub", rowName(rem.project) || projectTitle()));
    const save = button("lbtxt", "Set reminder", "Set the reminder");
    save.onclick = () => saveRem(err);
    const cancel = button("lbtxt", "Cancel", "Close without saving");
    cancel.onclick = () => closeRem();
    head.append(title, save, cancel);
    if (rem.a) {
      const del = button("lbtxt", "Delete", "Delete this reminder");
      del.onclick = async () => {
        try {
          await api("DELETE", "/api/automations/" + encodeURIComponent(rem.a.id));
        } catch (e) { err.textContent = e.message; err.hidden = false; return; }
        rem = null;
        await load();
      };
      head.append(del);
    }
    wrap.append(head);

    const help = el("p", "lbhelp");
    const paintHelp = () => {
      help.textContent = "Butler will say \"Reminder: "
        + (rem.text.trim() || "...") + "\" at " + (rem.time || "that time")
        + ", even in quiet hours, and send it to any channel you ticked. "
        + "No model runs.";
    };
    const text = el("input", "lbinp");
    text.id = "remText";
    text.maxLength = 200;
    text.value = rem.text;
    text.placeholder = "call the bank";
    text.oninput = () => { rem.text = text.value; paintHelp(); };
    const label = el("label", "lab", "Remind me");
    label.htmlFor = text.id;
    const f = el("div", "lbf");
    f.style.gridTemplateColumns = "minmax(0, 1fr)";
    f.append(label, text);
    wrap.append(f);

    // A choice that changes which fields exist redraws the editor from the
    // draft, so nothing typed is lost.
    const redraw = () => { rem.node = null; render(); };
    wrap.append(remField("When", seg("When", [[true, "Once"], [false, "Repeat"]],
      (v) => v === rem.once, (v) => { rem.once = v; redraw(); })));
    const time = el("input", "lbinp ausm");
    time.type = "time";
    time.value = rem.time;
    time.setAttribute("aria-label", "Time");
    time.oninput = () => { rem.time = time.value; paintHelp(); };
    if (rem.once) {
      const date = el("input", "lbinp ausm");
      date.type = "date";
      date.value = rem.date;
      date.setAttribute("aria-label", "Date");
      date.oninput = () => { rem.date = date.value; };
      const line = el("div", "lbrow");
      line.append(date, time);
      wrap.append(remField("Date and time", line));
    } else {
      wrap.append(remField("Every", seg("Every",
        [["day", "Day"], ["week", "Week"], ["month", "Month"]],
        (v) => v === rem.every, (v) => { rem.every = v; redraw(); })));
      if (rem.every === "week") {
        wrap.append(remField("On", seg("On", REM_DAYS,
          (v) => rem.days.includes(v), (v) => {
            rem.days = rem.days.includes(v) && rem.days.length > 1
              ? rem.days.filter((d) => d !== v) : [...new Set([...rem.days, v])];
            redraw();
          })));
      } else if (rem.every === "month") {
        const nth = el("select", "lbinp ausm");
        nth.setAttribute("aria-label", "Which week");
        for (const [v, t] of [[1, "1st"], [2, "2nd"], [3, "3rd"], [4, "4th"],
                              [5, "5th"]]) {
          const o = el("option", "", t);
          o.value = String(v);
          nth.append(o);
        }
        nth.value = String(rem.nth);
        nth.onchange = () => { rem.nth = Number(nth.value); };
        const day = el("select", "lbinp ausm");
        day.setAttribute("aria-label", "Day");
        for (const [v, t] of REM_DAYS) {
          const o = el("option", "", t);
          o.value = v;
          day.append(o);
        }
        day.value = rem.day;
        day.onchange = () => { rem.day = day.value; };
        const line = el("div", "lbrow");
        line.append(nth, day);
        wrap.append(remField("On the", line));
      }
      wrap.append(remField("At", time));
    }
    paintHelp();
    wrap.append(help);

    const chans = el("div", "");
    wrap.append(remField("Channels", chans), err);
    body.append(wrap);

    // The channel ticks: `GET /api/notify`'s items, never a token.
    let items = [];
    try { items = (await api("GET", "/api/notify")).items || []; }
    catch (_e) { items = []; }
    if (rem === null || rem.node !== wrap) return;
    if (rem.channels === null) {
      rem.channels = items.filter((c) => c.default).map((c) => c.name);
    }
    for (const c of items) {
      const lab = el("label", "auchk");
      const box = el("input");
      box.type = "checkbox";
      box.checked = rem.channels.includes(c.name);
      box.onchange = () => {
        rem.channels = box.checked ? [...rem.channels, c.name]
          : rem.channels.filter((n) => n !== c.name);
      };
      lab.append(box, document.createTextNode(" " + (c.label || c.name)));
      chans.append(lab);
    }
    if (!items.length) {
      chans.append(el("p", "lbhelp",
                      "No notification channel yet -- add one in Settings."));
    }
  }

  async function saveRem(err) {
    const body = { kind: "reminder", project: rem.project, text: rem.text,
                   channels: rem.channels || [] };
    if (rem.a) body.id = rem.a.id;
    if (rem.once) {
      body.runAt = rem.date + "T" + rem.time;
      if (rem.a) body.enabled = true;
    } else {
      body.schedule = rem.every === "week"
        ? { preset: "weekly", days: rem.days, time: rem.time }
        : rem.every === "month"
          ? { preset: "monthly", nth: rem.nth, day: rem.day, time: rem.time }
          : { preset: "daily", time: rem.time };
    }
    try { await api("POST", "/api/automations", body); }
    catch (e) {
      // The daemon's sentence, naming the rule; the draft stays as typed.
      err.textContent = e.message;
      err.hidden = false;
      return;
    }
    rem = null;
    await load();
  }

  // -- tasks -- //
  // The quick add at the top writes to the project on screen; the Global
  // section keeps its own add. Agents add theirs to the main list, tagged
  // with the project they are about (docs/todo-list.md TD-2).
  function quickAdd(pid) {
    const box = el("label", "lbqadd");
    const input = el("input");
    input.id = "libQuickAdd";
    input.maxLength = 200;
    input.value = quick;
    input.placeholder = "Add a task to " + projectTitle();
    input.setAttribute("aria-label", "Add a task to " + projectTitle());
    input.oninput = () => { quick = input.value; };
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || !input.value.trim()) return;
      e.preventDefault();
      const text = input.value;
      act(async () => {
        await api("POST", "/api/tasks", { project: pid, text });
        quick = "";
      });
    });
    box.append(env.icon(16, 2, ICON_PLUS), input);
    return box;
  }

  function drawTasks(body) {
    const pid = isRootProject() ? rootId() : project;
    const here = isRootProject() ? doc.tasks.global : doc.tasks.project;
    body.append(quickAdd(pid));
    sectionTitle(body, projectTitle(), isRootProject() ? "the main list" : "this project");
    if (!here.open.length && !here.doneCount) {
      body.append(lempty(ICON_TASKS, "Nothing to do in " + projectTitle(),
        "Type one above, or say \"add a task\". Agents add theirs to the main "
        + "list, tagged with the project."));
    } else {
      drawTaskSection(body, "here", here, pid, false);
    }
    if (!isRootProject()) {
      sectionTitle(body, "Global", "the main list");
      drawTaskSection(body, "global", doc.tasks.global, rootId(), true);
    }
    if (doc.removed.tasks.length) {
      sectionTitle(body, "Project removed", "kept, not deleted");
      for (const t of doc.removed.tasks) body.append(taskRow(t, false));
    }
  }

  function drawTaskSection(body, key, sec, pid, addable) {
    for (const t of sec.open) body.append(taskRow(t, true));
    if (adding === key) body.append(taskInput("", async (text) => {
      await api("POST", "/api/tasks", { project: pid, text });
      adding = null;
    }, () => { adding = null; render(); }));
    else if (addable) body.append(addButton("Add a task to Global", () => {
      adding = key; editing = null; render();
      const input = $("libraryBody").querySelector(".lbedit input");
      if (input) input.focus();
    }));
    if (sec.doneCount) {
      const fold = button("lbfold");
      fold.setAttribute("aria-expanded", String(doneOpen.has(key)));
      fold.append(env.icon(12, 2, ICON_CARET),
                  document.createTextNode("Done (" + sec.doneCount + ")"));
      fold.onclick = () => {
        if (doneOpen.has(key)) doneOpen.delete(key); else doneOpen.add(key);
        render();
      };
      body.append(fold);
      if (doneOpen.has(key)) for (const t of sec.done) body.append(taskRow(t, false));
    }
  }

  // The round check, the task (tagged with the project an agent's task is
  // about), who added it -- the coding agent the daemon RECORDED (`agent`),
  // else Butler or you -- and why, then Start.
  function taskRow(t, startable) {
    if (editing === t.id) {
      return taskInput(t.text, async (text) => {
        await api("POST", "/api/tasks", { id: t.id, text });
        editing = null;
      }, () => { editing = null; render(); }, t);
    }
    const row = el("div", "lbtask" + (t.done ? " done" : ""));
    const chk = button("lbck" + (t.done ? " on" : ""), undefined,
                       (t.done ? "Mark not done: " : "Mark done: ") + t.text);
    chk.setAttribute("aria-pressed", String(!!t.done));
    // Icon-only, so one or two words of hover help (the page's D8 rule).
    chk.dataset.help = t.done ? "Not done" : "Done";
    chk.append(env.icon(14, 2.6, ICON_TICK));
    chk.onclick = () => act(() => api("POST", "/api/tasks",
                                      { id: t.id, done: !t.done }));
    const main = button("tx", undefined, "Edit task: " + t.text);
    const line = el("span", "t");
    const about = t.about ? env.projectRow(t.about) : null;
    if (about) line.append(el("span", "tag", env.projectName(about)));
    line.append(document.createTextNode(t.text));
    // Who, then WHEN in the daemon's words (`addedWords`, Step 6b).
    const who = t.agent ? "Added by " + t.agent
      : t.source === "butler" ? "Butler added this" : "You";
    const sub = el("span", "s", who + ", " + (t.done ? "done " + t.doneWords
      : t.addedWords));
    if (t.why) {
      sub.append(document.createTextNode(". "), el("span", "why", "Why: " + t.why));
    }
    main.append(line, sub);
    main.onclick = () => { editing = t.id; adding = null; render();
      const input = $("libraryBody").querySelector(".lbedit input");
      if (input) input.focus(); };
    row.append(chk, main);
    if (startable && !t.done) {
      const start = button("lbbtn2", "Start", "Start a chat on: " + t.text);
      // Starting a task does NOT mark it done (AC-449): the owner checks it
      // off when it is done, which may be days later.
      start.onclick = () => runText(t.project, t.text, start);
      row.append(start);
    }
    return row;
  }

  // One line of text for a new or edited task. Enter saves, Escape cancels;
  // an edit also offers Delete.
  function taskInput(value, save, cancel, task) {
    const wrap = el("div", "lbedit");
    const input = el("input", "lbinp");
    input.value = value;
    input.maxLength = 200;
    input.setAttribute("aria-label", task ? "Edit the task" : "New task");
    const note = el("p", "lnote lberr");
    note.hidden = true;
    const commit = async () => {
      if (!input.value.trim()) { cancel(); return; }
      try { await save(input.value); await load(); }
      catch (e) { note.textContent = e.message; note.hidden = false; }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
    });
    const row = el("div", "lbeditrow");
    row.append(input);
    const ok = button("lbtxt", "Save");
    ok.onclick = commit;
    row.append(ok);
    if (task) {
      const del = button("lbtxt", "Delete", "Delete the task: " + task.text);
      del.onclick = () => act(async () => {
        await api("DELETE", "/api/tasks/" + encodeURIComponent(task.id));
        editing = null;
      });
      row.append(del);
    }
    wrap.append(row, note);
    return wrap;
  }

  async function act(fn) {
    try { await fn(); await load(); }
    catch (e) { env.showError(e.message); }
  }

  // ---- run: a recipe or a task in a NEW chat (AU-4, AC-449) --------------- //
  async function runText(pid, text, btn) {
    const ok = await env.runInNewChat(pid, text, btn);
    if (ok && env.isPhone()) closeSheet();
    return ok;
  }

  async function startRun(r, pid, btn) {
    if (r.placeholders.length) { openValues(r, "run", btn, pid); return; }
    try {
      const got = await expand(r.id, {});
      await runText(pid, got.text, btn);
    } catch (e) { env.showError(e.message); }
  }

  // ---- run with values, or the slash picker's insert -------------------- //
  // One field per SERVED placeholder, its default beside it; "Butler will
  // send" is the daemon's expansion of what is typed (AC-448).
  function openValues(r, mode, anchor, pid) {
    runWith = { recipe: r, mode, anchor, pid };
    $("recipeRunTitle").textContent = "/" + r.name;
    $("recipeRunSub").textContent = mode === "run"
      ? "Runs in a new chat in " + (rowName(pid) || "this project") + "."
      : "Goes into the message box so you can edit it first. Nothing is sent.";
    const fields = $("recipeRunFields");
    fields.replaceChildren();
    r.placeholders.forEach((p, i) => {
      const f = el("div", "fld");
      const label = el("label", "", p.name);
      const input = el("input", "inp vin");
      input.id = "recipeVal" + i;
      label.htmlFor = input.id;
      input.dataset.name = p.name;
      input.value = p.default == null ? "" : p.default;
      input.addEventListener("input", previewSoon);
      const line = el("div", "inrow");
      line.append(input, el("span", "help", p.default == null
        ? "asked each run, so fill it in" : "default " + p.default));
      f.append(label, line);
      fields.append(f);
    });
    $("recipeRunGo").textContent = mode === "run" ? "Run in a new chat"
      : "Put it in the box";
    $("recipeRunPreview").textContent = "";
    $("recipeRunDlg").showModal();
    const first = fields.querySelector("input");
    if (first) first.focus();
    preview();
  }

  function values() {
    const out = {};
    for (const input of $("recipeRunFields").querySelectorAll("input")) {
      out[input.dataset.name] = input.value;
    }
    return out;
  }

  function previewSoon() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(preview, 200);
  }

  // THE DAEMON'S TEXT, never a local substitution (AC-448): a missing value
  // shows the daemon's own refusal where the preview would be.
  async function preview() {
    if (!runWith) return;
    const r = runWith.recipe;
    const box = $("recipeRunPreview");
    try {
      const got = await expand(r.id, values());
      if (!runWith || runWith.recipe !== r) return;
      box.textContent = got.text;
      box.classList.remove("refused");
      $("recipeRunGo").disabled = false;
    } catch (e) {
      if (!runWith || runWith.recipe !== r) return;
      box.textContent = e.message;
      box.classList.add("refused");
      $("recipeRunGo").disabled = true;
    }
  }

  function closeValues() {
    runWith = null;
    if ($("recipeRunDlg").open) $("recipeRunDlg").close();
  }

  $("recipeRunCancel").onclick = () => closeValues();
  $("recipeRunClose").onclick = () => closeValues();
  $("recipeRunDlg").addEventListener("cancel", (e) => { e.preventDefault(); closeValues(); });
  env.modal($("recipeRunDlg"), () => closeValues());
  $("recipeRunDlg").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!runWith) return;
    const { recipe: r, mode, pid } = runWith;
    let text;
    try { text = (await expand(r.id, values())).text; }
    catch (err) { $("recipeRunPreview").textContent = err.message; return; }
    const btn = $("recipeRunGo");
    closeValues();
    if (mode === "run") await runText(pid, text, btn);
    else env.insertText(text);
  });

  // ---- the recipe editor ------------------------------------------------- //
  function openEditor(r, scope) {
    recipe = r;
    editScope = scope || project || "global";
    $("recipeDlgTitle").textContent = r ? "Edit recipe" : "New recipe";
    $("recipeName").value = r ? r.name : "";
    $("recipePrompt").value = r ? r.prompt : "";
    $("recipeErr").hidden = true;
    $("recipeDelete").hidden = !r;
    $("recipeScopeHere").textContent = rowName(editScope === "global" ? project : editScope)
      || rowName(project) || "This project";
    paintScope();
    paintMirror();
    paintPlaceholders();
    $("recipeDlg").showModal();
    $("recipeName").focus();
  }

  // The placeholders the daemon found in the SAVED prompt.
  function served() { return recipe ? recipe.placeholders : []; }

  // The mirror behind the prompt marks each served token where it stands in
  // the text as typed now (the trailing line break keeps the last line's
  // height when the text ends in one).
  function paintMirror() {
    const mirror = $("recipeMirror");
    mirror.replaceChildren();
    tokens(mirror, $("recipePrompt").value + "\n", served(),
           (t) => el("mark", "", t));
    mirror.scrollTop = $("recipePrompt").scrollTop;
  }

  function paintPlaceholders() {
    const box = $("recipePlaceholders");
    box.replaceChildren();
    if (recipe && recipe.problem) { box.append(el("span", "", recipe.problem)); return; }
    const phs = served();
    const edited = $("recipePrompt").value !== (recipe ? recipe.prompt : "");
    if (!recipe) {
      box.append(document.createTextNode("Placeholders are listed once the recipe is saved."));
    } else if (!phs.length) {
      box.append(document.createTextNode("No placeholders."));
    } else {
      box.append(document.createTextNode("Placeholders"));
      for (const p of phs) {
        const tag = el("span", "tag");
        tag.append(el("b", "", p.name), document.createTextNode(
          p.default == null ? "asked each run" : "default " + p.default));
        box.append(tag);
      }
    }
    if (recipe && edited) box.append(el("span", "", "As last saved; Save checks the new text."));
    if (recipe) {
      const n = recipe.usedBy.length;
      box.append(el("span", "use", n ? "Used by " + n + " automation"
        + (n === 1 ? "" : "s") + "." : "Used by no automation."));
    }
  }

  function paintScope() {
    const global = editScope === "global";
    $("recipeScopeHere").setAttribute("aria-pressed", String(!global));
    $("recipeScopeGlobal").setAttribute("aria-pressed", String(global));
  }

  $("recipeScopeHere").onclick = () => {
    // "This project" is the recipe's own project when it has one, else the
    // project on screen.
    editScope = recipe && recipe.project !== "global" ? recipe.project : project;
    paintScope();
  };
  $("recipeScopeGlobal").onclick = () => { editScope = "global"; paintScope(); };
  $("recipePrompt").addEventListener("input", () => { paintMirror(); paintPlaceholders(); });
  $("recipePrompt").addEventListener("scroll", () => {
    $("recipeMirror").scrollTop = $("recipePrompt").scrollTop;
  });

  function closeEditor() {
    recipe = null;
    if ($("recipeDlg").open) $("recipeDlg").close();
  }

  $("recipeCancel").onclick = () => closeEditor();
  $("recipeClose").onclick = () => closeEditor();
  $("recipeDlg").addEventListener("cancel", (e) => { e.preventDefault(); closeEditor(); });
  env.modal($("recipeDlg"), () => closeEditor());
  $("recipeDlg").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { name: $("recipeName").value, project: editScope,
                   prompt: $("recipePrompt").value };
    if (recipe) body.id = recipe.id;
    try {
      await api("POST", "/api/recipes", body);
    } catch (err) {
      $("recipeErr").textContent = err.message;
      $("recipeErr").hidden = false;
      return;
    }
    closeEditor();
    env.toast("Saved");
    tab = "recipes";
    load();
  });
  $("recipeDelete").onclick = async () => {
    if (!recipe) return;
    try {
      await api("DELETE", "/api/recipes/" + encodeURIComponent(recipe.id));
    } catch (err) {
      $("recipeErr").textContent = err.message;
      $("recipeErr").hidden = false;
      return;
    }
    closeEditor();
    load();
  };

  // ---- the slash picker (AC-450) ----------------------------------------- //
  // `/` at the START of the composer lists this project's and the global
  // recipes by name; choosing one PUTS the expansion in the box and sends
  // nothing, so it can be edited first (the Claude Code custom-command
  // shape). A recipe with a value to fill opens the values dialog first.
  function slashOpen() { return !$("slashPop").hidden; }

  function slashQuery() {
    const v = env.composer().value;
    if (!v.startsWith("/") || v.includes("\n")) return null;
    return v.slice(1).toLowerCase();
  }

  function paintSlash() {
    const q = slashQuery();
    const pop = $("slashPop");
    if (q === null) { closeSlash(); return; }
    if (!doc) {
      pop.replaceChildren(el("div", "slashhint", "Loading recipes..."));
      pop.hidden = false;
      if (!doc && project) load();
      return;
    }
    const all = [...doc.recipes.project.map((r) => [r, rowName(project)]),
                 ...doc.recipes.global.map((r) => [r, "Global"])];
    slashItems = all.filter(([r]) => !r.problem
      && r.name.toLowerCase().includes(q));
    if (slashAt >= slashItems.length) slashAt = 0;
    // The export's popover: a title, one row per recipe -- "/" and the name
    // with the typed part underlined, the prompt's first line with the
    // SERVED placeholders as chips (`tokens`, as the Library tab draws
    // them), the scope -- and a footer naming only what the keys do here.
    pop.replaceChildren(el("p", "pop-title", "Recipes"));
    slashItems.forEach(([r, scope], i) => {
      const opt = el("div", "slashopt");
      opt.id = "slashOpt" + i;
      opt.setAttribute("role", "option");
      opt.setAttribute("aria-selected", String(i === slashAt));
      const k = el("span", "k", "/");
      const at = q ? r.name.toLowerCase().indexOf(q) : -1;
      if (at >= 0) {
        k.append(r.name.slice(0, at), el("u", "", r.name.slice(at, at + q.length)),
                 r.name.slice(at + q.length));
      } else {
        k.append(r.name);
      }
      const v = el("span", "v");
      tokens(v, firstLine(r.prompt), r.placeholders, (_t, p) => chip(p));
      opt.append(k, v, el("span", "s", scope));
      opt.addEventListener("pointerdown", (e) => { e.preventDefault(); choose(i); });
      pop.append(opt);
    });
    if (!slashItems.length) {
      pop.append(el("div", "slashhint", "No recipe matches. Add recipes in the Library."));
    }
    const foot = el("div", "slfoot");
    const keys = (parts, words) => {
      const span = el("span");
      for (const p of parts) span.append(typeof p === "string" ? el("kbd", "", p) : p);
      span.append(words);
      return span;
    };
    const arrow = (d) => {
      const k = el("kbd");
      k.setAttribute("aria-label", d === "up" ? "Up" : "Down");
      k.append(env.icon(12, 2, [["path", { d: d === "up"
        ? "M12 19V5M6 11l6-6 6 6" : "M12 5v14M6 13l6 6 6-6" }]]));
      return k;
    };
    if (slashItems.length) {
      foot.append(keys([arrow("up"), arrow("down")], "move"),
                  keys(["Enter"], "fill in"));
    }
    foot.append(keys(["Esc"], "close"));
    pop.append(foot);
    pop.hidden = false;
    env.composer().setAttribute("aria-activedescendant",
                                slashItems.length ? "slashOpt" + slashAt : "");
  }

  function closeSlash() {
    $("slashPop").hidden = true;
    slashItems = [];
    slashAt = 0;
    env.composer().removeAttribute("aria-activedescendant");
  }

  async function choose(i) {
    const item = slashItems[i];
    if (!item) return;
    const r = item[0];
    closeSlash();
    const pid = r.project === "global" ? project : r.project;
    if (r.placeholders.some((p) => p.default == null)) {
      openValues(r, "insert", env.composer(), pid);
      return;
    }
    try { env.insertText((await expand(r.id, {})).text); }
    catch (e) { env.showError(e.message); }
  }

  env.composer().addEventListener("input", () => {
    if (slashQuery() === null) { if (slashOpen()) closeSlash(); return; }
    slashAt = 0;
    paintSlash();
  });
  // Registered BEFORE app.js's own Enter-sends listener (this module is
  // mounted first), so an Enter that chooses a recipe never also sends.
  env.composer().addEventListener("keydown", (e) => {
    if (!slashOpen()) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!slashItems.length) return;
      e.preventDefault();
      slashAt = (slashAt + (e.key === "ArrowDown" ? 1 : -1)
                 + slashItems.length) % slashItems.length;
      paintSlash();
    } else if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey
               && slashItems.length) {
      e.preventDefault();
      e.stopImmediatePropagation();
      choose(slashAt);
    }
  });
  env.composer().addEventListener("blur", () => { if (slashOpen()) closeSlash(); });

  // ---- tabs and chrome ---------------------------------------------------- //
  for (const b of document.querySelectorAll("#libraryPanel [role='tab']")) {
    b.onclick = () => { tab = b.dataset.tab; render(); };
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const tabs = [...document.querySelectorAll("#libraryPanel [role='tab']")];
      const i = tabs.indexOf(b) + (e.key === "ArrowRight" ? 1 : -1);
      const next = tabs[(i + tabs.length) % tabs.length];
      tab = next.dataset.tab;
      render();
      next.focus();
      e.preventDefault();
    });
  }
  $("libraryRefresh").onclick = () => load();
  $("libraryFull").onclick = () => {
    if (env.isPhone()) closeSheet();
    env.automations().openPage(tab === "tasks" ? "tasks" : "automations");
  };
  $("libraryClose").onclick = () => {
    if (env.isPhone()) closeSheet();
    else toggle(false);
  };
  $("libraryScrim").onclick = () => closeSheet();

  return { follow, rightChanged, toggle, paint, sheetOpen, closeSheet,
           changed, slashOpen, closeSlash, shown: visible };
}
