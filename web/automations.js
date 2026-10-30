// AUTOMATIONS ON THE PAGE: the automation editor and the full TASKS &
// AUTOMATIONS page (docs/automations.md phases 5 and 6, AC-468; the editor's
// approved look is web/mocks/w1-calm-library.html, lantern-ui Step 6a).
//
// EVERY RULE IS THE DAEMON'S. A schedule is never parsed here: the preset
// picker sends `{preset, time, ...}` or `{cron}` to POST /api/schedule/
// preview and draws what comes back -- its `nextWords` sentence, the cron it
// is "the same as", or the daemon's refusal naming the field (AC-455). A
// run's outcome is its served `statusWord` and `group` (`mark`). Saving is
// POST /api/automations, whose refusals are drawn as they are. The page
// names no setting (AC-41) and keeps nothing in browser storage (AC-8).
//
// NO DATE IS WORDED HERE (lantern-ui section 8, Step 6b): a run's time and
// duration, a fold's last time and a task's added and done times are the
// daemon's `whenWords`, `tookWords`, `lastOccurrenceWords`, `addedWords` and
// `doneWords` (`test_library_lantern_page.py` gates it, no exceptions).
//
// NAMES, PROMPTS, TASK TEXT, RUN SUMMARIES AND REASONS ARE UNTRUSTED (a task
// or a summary may be a model's). Every string reaches the DOM through
// `textContent` and every element through `createElement`; there is no
// markup sink in this file.
//
// NOTIFY (phase 7b, AC-472): each run's result is sent to the automation's
// ticked channels, and what happened is on the run (`notify`), one entry per
// event -- drawn in the history as "Telegram sent to Owner's phone" or
// "Telegram to Owner's phone failed: 401 Unauthorized", plus what the desk
// said. The words for a channel are the daemon's (`label`, `service`).

export function mountAutomations(env) {
  const $ = (id) => document.getElementById(id);

  // ---- the network: ONE helper ------------------------------------------ //
  async function api(method, path, body) {
    const init = { method };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    const r = await fetch(path, init);
    const answer = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(answer.error || ("HTTP " + r.status));
    return answer;
  }

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
  function projectLabel(id) {
    const row = id ? env.projectRow(id) : null;
    return row ? env.projectName(row) : "Project removed";
  }

  // ---- shared drawing: a status mark, a switch -------------------------- //
  // THE DAEMON'S WORD for a run (`statusWord`) and a SHAPE per its served
  // `group` (docs/lantern-ui.md section 8; AC-168, never colour alone): the
  // stylesheet draws `.mk.ok` a dot, `.needs` a diamond, `.failed` a square,
  // `.skipped` a ring, `.pending` a turning ring. `lead` reads "Last run ".
  function mark(run, lead = "", none = "never run") {
    const span = el("span", "mk");
    if (!run) { span.textContent = none; span.classList.add("none"); return span; }
    span.classList.add(run.group);
    // A silent run (docs/proactive.md PR-4) is still completed; it says so.
    span.append(el("i"), document.createTextNode(lead + run.statusWord
      + (run.silent ? ", nothing new" : "")
      + (run.occurrenceCount > 1 ? " x" + run.occurrenceCount : "")));
    return span;
  }
  function sw(on, label, onchange) {
    const b = button("ausw", undefined, label);
    b.setAttribute("role", "switch");
    b.setAttribute("aria-checked", String(!!on));
    b.onclick = (e) => { e.stopPropagation(); onchange(!on); };
    return b;
  }
  // What one result event did (a `notify` entry), in the mock's words.
  function noticeWords(n) {
    const out = [];
    // The daemon's own markers (docs/proactive.md PR-4, PR-7): a silent run
    // and a failure kept quiet inside a streak were sent to nobody.
    if (n.spoken === "silent") return ["nothing new, nothing sent"];
    if (n.spoken === "quiet") {
      return ["not sent: failed " + n.streak + " times in a row"];
    }
    if (n.turnedOff) out.push("turned off after " + n.streak + " failures");
    const tag = n.event === "needs_you" ? " (needs you)" : "";
    for (const [name, c] of Object.entries(n.channels || {})) {
      const who = (c.service || "Channel") + (c.ok ? " sent to " : " to ")
        + (c.label || name);
      out.push(c.ok ? who + tag : who + " failed: " + (c.detail || "unknown"));
    }
    if (n.spoken === "said") out.push("desk said it" + tag);
    // Quiet hours (docs/proactive.md PR-16): the line waits for the morning.
    else if (n.spoken === "held: quiet hours") out.push("desk line held for the morning");
    else if (n.spoken && n.spoken !== "no desk" && n.spoken !== "off") {
      out.push("desk line " + n.spoken);
    }
    return out;
  }

  // The switch, the Run now and the bulk actions all go through here, then
  // re-read (the daemon's `library` frame re-reads every other surface).
  async function setEnabled(ids, enabled) {
    await api("POST", "/api/automations/bulk", { ids, enabled });
  }
  async function runNow(id) {
    const run = await api("POST", "/api/automations/" + encodeURIComponent(id) + "/run");
    if (run.status && run.status.startsWith("skipped")) {
      env.showError("Not run: " + (run.reason || run.status));
    }
    return run;
  }

  // ===================================================================== //
  // THE EDITOR (docs/automations.md 6)
  // ===================================================================== //
  const ed = { auto: null, project: "", recipes: [], preset: "daily",
               previewSeq: 0, ok: false, channels: null };
  const DAYS = [["MON", "Monday"], ["TUE", "Tuesday"], ["WED", "Wednesday"],
                ["THU", "Thursday"], ["FRI", "Friday"], ["SAT", "Saturday"],
                ["SUN", "Sunday"]];
  const NTH = [[1, "1st"], [2, "2nd"], [3, "3rd"], [4, "4th"], [5, "5th"]];
  let previewTimer = 0;

  function select(id, options, value, label) {
    const s = el("select", "inp");
    s.id = id;
    if (label) s.setAttribute("aria-label", label);
    for (const [v, t] of options) {
      const o = el("option", "", t);
      o.value = String(v);
      s.append(o);
    }
    if (value !== undefined) s.value = String(value);
    s.addEventListener("change", previewSoon);
    return s;
  }
  function timeInput(value) {
    const t = el("input", "inp time");
    t.type = "time";
    t.id = "autoTime";
    t.value = value || "09:00";
    t.setAttribute("aria-label", "Time");
    t.addEventListener("input", previewSoon);
    return t;
  }

  async function openEditor(auto, projectId) {
    ed.auto = auto || null;
    ed.project = auto ? auto.project : (projectId || env.rootId());
    $("autoErr").hidden = true;
    $("autoDelete").hidden = !auto;
    $("autoDlgTitle").textContent = auto ? (auto.recipeName || "Automation")
      : "New automation";
    let recipes = [];
    try { recipes = (await api("GET", "/api/recipes")).recipes || []; }
    catch (e) { env.showError(e.message); return; }
    ed.recipes = recipes.filter((r) => !r.problem
      && (r.project === "global" || r.project === ed.project
          || (auto && r.id === auto.recipe)));
    const rs = $("autoRecipe");
    rs.replaceChildren();
    for (const r of ed.recipes) {
      const o = el("option", "", "/" + r.name + (r.project === "global"
        ? ", every project" : ""));
      o.value = r.id;
      rs.append(o);
    }
    $("autoNoRecipe").hidden = ed.recipes.length > 0;
    if (auto) rs.value = auto.recipe;
    const sch = auto ? auto.schedule : null;
    const p = sch && sch.preset;
    ed.preset = p ? (p.preset === "weekly" && p.days.length !== 1 ? "cron" : p.preset)
      : (sch ? "cron" : "daily");
    ed.presetValues = p || { time: "09:00" };
    ed.cron = sch ? sch.cron : "0 9 * * *";
    $("autoGrace").value = String(auto ? auto.graceMinutes : 60);
    $("autoEnabled").setAttribute("aria-checked", String(auto ? auto.enabled : true));
    silenceSw.setAttribute("aria-checked", String(auto ? !!auto.silence : true));
    paintMore();
    drawRecipeFields();
    drawPreset();
    await drawChannels();
    await drawPrechecks(auto ? auto.precheck || "" : "");
    await drawSources(auto ? auto.source || "" : "");
    // More options opens by itself when it holds a choice that changes how
    // the automation starts, so a hidden Repeat is never a mystery.
    setMore(!!(auto && (auto.source || auto.precheck)));
    $("autoDlg").showModal();
    rs.focus();
    preview();
  }

  function chosenRecipe() {
    return ed.recipes.find((r) => r.id === $("autoRecipe").value) || null;
  }

  // The project line and a field per placeholder of the chosen recipe. A
  // global recipe runs where the owner picks; a project recipe runs in its
  // own project and says so.
  function drawRecipeFields() {
    const r = chosenRecipe();
    const where = $("autoProject");
    where.replaceChildren();
    if (r && r.project === "global") {
      const rows = env.projects();
      const s = el("select", "inp");
      s.id = "autoProjectPick";
      s.setAttribute("aria-label", "Project");
      for (const row of rows) {
        const o = el("option", "", env.projectName(row));
        o.value = row.id;
        s.append(o);
      }
      s.value = rows.some((row) => row.id === ed.project) ? ed.project : env.rootId();
      s.onchange = () => { ed.project = s.value; };
      ed.project = s.value;
      where.append(s);
    } else if (r) {
      ed.project = r.project;
      where.append(el("span", "val", projectLabel(r.project)),
                   el("span", "help", "the recipe's own project"));
    }
    const fields = $("autoValues");
    fields.replaceChildren();
    const given = (ed.auto && ed.auto.values) || {};
    for (const [i, ph] of ((r && r.placeholders) || []).entries()) {
      const f = el("div", "fld");
      const label = el("label", "", ph.name);
      const input = el("input", "inp vin");
      input.id = "autoVal" + i;
      label.htmlFor = input.id;
      input.dataset.name = ph.name;
      input.value = given[ph.name] || "";
      const line = el("div", "inrow");
      line.append(input, el("span", "help", ph.default == null
        ? "no default, so fill it in" : "the recipe's own default is " + ph.default));
      f.append(label, line);
      fields.append(f);
    }
  }

  function drawPreset() {
    for (const b of $("autoPresets").querySelectorAll("button")) {
      b.setAttribute("aria-pressed", String(b.dataset.p === ed.preset));
    }
    const line = $("autoLine");
    line.replaceChildren();
    const pv = ed.presetValues || {};
    const w = (t) => el("span", "", t);
    if (ed.preset === "daily") {
      line.append(w("every day at"), timeInput(pv.time));
    } else if (ed.preset === "weekdays") {
      line.append(w("Monday to Friday at"), timeInput(pv.time));
    } else if (ed.preset === "weekly") {
      line.append(w("every"), select("autoDay", DAYS, (pv.days || ["MON"])[0], "Day"),
                  w("at"), timeInput(pv.time));
    } else if (ed.preset === "monthly") {
      line.append(w("on the"), select("autoNth", NTH, pv.nth || 1, "Which week"),
                  select("autoDay", DAYS, pv.day || "MON", "Day"), w("at"),
                  timeInput(pv.time));
    } else {
      const c = el("input", "inp code");
      c.id = "autoCron";
      c.value = ed.cron;
      c.spellcheck = false;
      c.setAttribute("aria-label", "Cron expression");
      c.setAttribute("aria-describedby", "autoCronHelp");
      c.addEventListener("input", previewSoon);
      const help = el("span", "help", "minute hour day month weekday; TUE#1 = the first Tuesday");
      help.id = "autoCronHelp";
      line.append(c, help);
    }
  }

  function scheduleBody() {
    if (ed.preset === "cron") return { cron: $("autoCron").value.trim() };
    const body = { preset: ed.preset, time: $("autoTime").value };
    if (ed.preset === "weekly") body.days = [$("autoDay").value];
    if (ed.preset === "monthly") {
      body.nth = Number($("autoNth").value);
      body.day = $("autoDay").value;
    }
    return body;
  }

  function previewSoon() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(preview, 200);
  }

  // THE DAEMON'S ANSWER, never a local parse (AC-455): its `nextWords`
  // sentence (lantern-ui section 8: the page words no date), and for a preset
  // the cron it compiles to -- "same as". A refusal is the daemon's sentence.
  async function preview() {
    const mine = ++ed.previewSeq;
    const box = $("autoNext");
    let got;
    try { got = await api("POST", "/api/schedule/preview", scheduleBody()); }
    catch (e) {
      if (mine !== ed.previewSeq) return;
      box.className = "next bad";
      box.replaceChildren(el("span", "n1", e.message), el("span", "n2",
        "Save stays off while Butler refuses the schedule."));
      ed.ok = false;
      paintSave();
      return;
    }
    if (mine !== ed.previewSeq) return;
    ed.ok = true;
    ed.cron = got.cron;
    box.className = "next";
    box.replaceChildren(el("span", "n1", got.nextWords));
    if (ed.preset !== "cron") {
      const same = el("span", "n2", "Same as ");
      same.append(el("code", "", got.cron),
                  document.createTextNode(" -- Advanced edits it directly"));
      box.append(same);
    }
    paintSave();
  }

  function paintSave() {
    $("autoSave").disabled = !(ed.ok || startPick.value) || !chosenRecipe();
  }

  // The channel ticks: `GET /api/notify`'s `items` (never a token).
  async function drawChannels() {
    const box = $("autoChannels");
    box.replaceChildren();
    let channels = null;
    try { channels = (await api("GET", "/api/notify")).items || []; }
    catch (_e) { channels = null; }
    ed.channels = channels;
    const ticked = new Set(ed.auto ? ed.auto.channels
      : (channels || []).filter((c) => c.default).map((c) => c.name));
    for (const c of channels || []) {
      const lab = el("label", "auchk");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.dataset.name = c.name;
      box2.checked = ticked.has(c.name);
      lab.append(box2, document.createTextNode((c.label || c.name)
        + (c.kind ? " (" + c.kind + ")" : "")));
      if (c.default) lab.append(el("small", "", "default"));
      box.append(lab);
    }
    // Names the automation already carries that the daemon does not list
    // stay ticked and visible, never silently dropped.
    for (const name of ticked) {
      if ((channels || []).some((c) => c.name === name)) continue;
      const lab = el("label", "auchk");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.dataset.name = name;
      box2.checked = true;
      lab.append(box2, document.createTextNode(name + " (not found)"));
      box.append(lab);
    }
    box.append(el("p", "help", (channels && channels.length) || ticked.size
      ? "Each run's result -- finished, failed, needs you or skipped -- is "
        + "sent to the ticked channels."
      : "No notification channel yet -- add one in Settings."));
  }

  // The precheck choice (docs/proactive.md PR-23): only the names the
  // daemon lists. Scripts are added by hand in its folder, never here.
  async function drawPrechecks(current) {
    precheckPick.replaceChildren();
    let got = { names: [], folder: "" };
    try { got = await api("GET", "/api/prechecks"); }
    catch (e) { precheckHelp.textContent = "Prechecks unavailable: " + e.message; }
    const none = el("option", "", "none");
    none.value = "";
    precheckPick.append(none);
    const names = [...got.names];
    // A stored name the folder no longer has stays visible, never dropped.
    if (current && !names.includes(current)) names.push(current);
    for (const name of names) {
      const o = el("option", "", name + (got.names.includes(name) ? "" : " (not found)"));
      o.value = name;
      precheckPick.append(o);
    }
    precheckPick.value = current;
    if (got.folder) {
      precheckHelp.textContent = "A precheck runs first; the agent is woken only "
        + "when its output is new. Add one by placing a .py, .ps1 or .cmd file in "
        + got.folder;
    }
  }

  // What starts it (docs/proactive.md PR-28): its schedule, or a trigger
  // source from `GET /api/triggers` (never a password, link or token). A
  // source hides the schedule, the grace and the precheck: the daemon
  // ignores them for an event automation.
  async function drawSources(current) {
    startPick.replaceChildren();
    const onSchedule = el("option", "", "on its schedule");
    onSchedule.value = "";
    startPick.append(onSchedule);
    let items = [];
    try { items = (await api("GET", "/api/triggers")).items || []; }
    catch (e) { startHelp.textContent = "Triggers unavailable: " + e.message; }
    for (const t of items) {
      const o = el("option", "", "when " + (t.label || t.name) + " fires ("
        + t.kind + (t.configured ? "" : ", not usable") + ")");
      o.value = t.name;
      startPick.append(o);
    }
    // A stored source the daemon no longer lists stays visible, never dropped.
    if (current && !items.some((t) => t.name === current)) {
      const o = el("option", "", "when " + current + " fires (not found)");
      o.value = current;
      startPick.append(o);
    }
    startPick.value = current;
    paintStart();
  }

  function paintStart() {
    const event = !!startPick.value;
    for (const row of [repeatRow, graceRow, precheckRow]) row.hidden = event;
    paintMore();
    startHelp.textContent = event
      ? "Runs each time this source fires, with read-only tools: what "
        + "arrived was written by someone else. Put {event} in the recipe "
        + "to hand it what arrived."
      : "";
    paintSave();
  }

  function values() {
    const out = {};
    for (const input of $("autoValues").querySelectorAll("input")) {
      if (input.value.trim()) out[input.dataset.name] = input.value;
    }
    return out;
  }

  async function save() {
    const body = {
      recipe: $("autoRecipe").value, project: ed.project, values: values(),
      schedule: scheduleBody(),
      graceMinutes: Number($("autoGrace").value),
      enabled: $("autoEnabled").getAttribute("aria-checked") === "true",
      silence: silenceSw.getAttribute("aria-checked") === "true",
      precheck: precheckPick.value,
      channels: [...$("autoChannels").querySelectorAll("input[type=checkbox]")]
        .filter((c) => c.checked).map((c) => c.dataset.name),
    };
    body.source = startPick.value;
    if (body.source) {
      delete body.schedule;
      delete body.graceMinutes;
      delete body.precheck;
    }
    if (ed.auto) body.id = ed.auto.id;
    try { await api("POST", "/api/automations", body); }
    catch (e) {
      $("autoErr").textContent = e.message;
      $("autoErr").hidden = false;
      return;
    }
    closeEditor();
    env.toast("Saved");
    changed();
  }

  function closeEditor() {
    ed.auto = null;
    if ($("autoDlg").open) $("autoDlg").close();
  }

  for (const b of $("autoPresets").querySelectorAll("button")) {
    b.onclick = () => {
      // Moving between presets keeps what can be kept; Advanced starts from
      // the cron the current choice compiles to, so it edits, not retypes.
      if (ed.preset !== "cron" && $("autoTime")) {
        ed.presetValues = { ...(ed.presetValues || {}), time: $("autoTime").value };
      }
      ed.preset = b.dataset.p;
      drawPreset();
      preview();
    };
  }
  $("autoRecipe").onchange = () => { drawRecipeFields(); paintSave(); };
  $("autoEnabled").onclick = () => {
    const on = $("autoEnabled").getAttribute("aria-checked") === "true";
    $("autoEnabled").setAttribute("aria-checked", String(!on));
  };
  // MORE OPTIONS (w1-calm-library.html): what starts it, the precheck and
  // the "nothing new" switch, folded, with a one-line summary of the choices.
  // The switch is the automation's own `silence` (docs/proactive.md PR-3); a
  // new automation starts ON.
  const silenceSw = $("autoSilence");
  silenceSw.onclick = () => {
    const on = silenceSw.getAttribute("aria-checked") === "true";
    silenceSw.setAttribute("aria-checked", String(!on));
    paintMore();
  };
  const precheckPick = $("autoPrecheck");
  const precheckHelp = $("autoPrecheckHelp");
  const precheckRow = $("autoPrecheckRow");
  precheckPick.onchange = () => paintMore();
  const repeatRow = $("autoRepeat");
  const graceRow = $("autoMissed");
  const startPick = $("autoStart");
  startPick.onchange = paintStart;
  const startHelp = $("autoStartHelp");
  function setMore(open) {
    $("autoMoreBody").hidden = !open;
    $("autoMoreBtn").setAttribute("aria-expanded", String(open));
  }
  function paintMore() {
    const start = startPick.selectedOptions[0];
    $("autoMoreSum").textContent = [start ? start.textContent : "",
      startPick.value ? "" : precheckPick.value ? "precheck " + precheckPick.value
        : "no precheck",
      silenceSw.getAttribute("aria-checked") === "true"
        ? "quiet when nothing is new" : "always reports"]
      .filter(Boolean).join(", ");
  }
  $("autoMoreBtn").onclick = () => setMore($("autoMoreBody").hidden);
  $("autoCancel").onclick = () => closeEditor();
  $("autoClose").onclick = () => closeEditor();
  $("autoDlg").addEventListener("cancel", (e) => { e.preventDefault(); closeEditor(); });
  env.modal($("autoDlg"), () => closeEditor());
  $("autoDlg").addEventListener("submit", (e) => { e.preventDefault(); save(); });
  $("autoDelete").onclick = async () => {
    if (!ed.auto) return;
    try { await api("DELETE", "/api/automations/" + encodeURIComponent(ed.auto.id)); }
    catch (e) {
      $("autoErr").textContent = e.message;
      $("autoErr").hidden = false;
      return;
    }
    if (page.selected === ed.auto.id) page.selected = "";
    closeEditor();
    changed();
  };

  // ===================================================================== //
  // THE FULL PAGE (AC-468; docs/lantern-ui.md 5.2, CU-16, Step 6b): every
  // project, grouped; filters; bulk; one automation's run history in a
  // drawer. It sits in the CONVERSATION COLUMN with the sidebar live, and
  // every time, duration and outcome on it is a word the daemon served
  // (`whenWords`, `tookWords`, `lastOccurrenceWords`, `addedWords`,
  // `doneWords`, `nextWords`, `statusWord`): the page words no date.
  // ===================================================================== //
  const page = { open: false, tab: "automations", autos: [], tasks: [],
                 selected: "", hist: false, checked: new Set(), runs: [],
                 error: "", q: "", proj: "", onoff: "", result: "",
                 tq: "", tproj: "", tstate: "open", tsrc: "",
                 seq: 0, opener: null };
  const TICK = [["path", { d: "M5 12.5l4.5 4.5L19 7" }]];
  const PLAY = [["path", { d: "M7 5.5v13l11-6.5z" }]];
  const CROSS = [["path", { d: "M6.5 6.5l11 11" }], ["path", { d: "M17.5 6.5l-11 11" }]];

  function isOpen() { return page.open; }

  // `select`: an automation id whose history to show (the Library card's
  // "Past runs" / "Past checks").
  function openPage(tab, select) {
    page.opener = document.activeElement;
    page.open = true;
    if (tab) page.tab = tab;
    if (select) { page.selected = select; page.hist = true; }
    $("tasksPage").hidden = false;
    paintFilters();
    render();                       // the tabs are marked before focus lands
    load();
    const on = $("tasksPage").querySelector('[role="tab"][aria-selected="true"]');
    (on || $("tasksPageClose")).focus();
  }

  // `back` returns focus to the control that opened the page; a press that
  // opens or starts a chat passes false, so focus stays where it went.
  function closePage(back = true) {
    if (!page.open) return;
    page.open = false;
    page.hist = false;
    $("tasksPage").hidden = true;
    const to = page.opener;
    page.opener = null;
    if (back && to && to.isConnected && to.offsetParent !== null) to.focus();
  }

  async function runsOf(id) {
    return (await api("GET", "/api/automations/" + encodeURIComponent(id)
                      + "/runs")).runs || [];
  }

  async function load() {
    if (!page.open) return;
    const mine = ++page.seq;
    try {
      const [a, t] = await Promise.all([api("GET", "/api/automations"),
                                        api("GET", "/api/tasks")]);
      if (mine !== page.seq) return;
      page.autos = a.automations || [];
      page.tasks = t.tasks || [];
      page.error = "";
      if (page.selected && !page.autos.some((x) => x.id === page.selected)) {
        page.selected = "";
        page.hist = false;
      }
      page.runs = page.hist ? await runsOf(page.selected) : [];
      if (mine !== page.seq) return;
    } catch (e) {
      if (mine !== page.seq) return;
      page.error = e.message;
    }
    render();
  }

  // Projects in the pane's own order, then any a removed row left behind.
  function groups(items, projectOf) {
    const order = env.projects().map((r) => r.id);
    const found = new Map();
    for (const item of items) {
      const p = projectOf(item);
      if (!found.has(p)) found.set(p, []);
      found.get(p).push(item);
    }
    return [...found.keys()].sort((x, y) => {
      const a = order.indexOf(x), b = order.indexOf(y);
      return (a < 0 ? 1e9 : a) - (b < 0 ? 1e9 : b);
    }).map((p) => [p, found.get(p)]);
  }

  // The last result filter reads the run's SERVED `group` (lantern-ui
  // section 8); "none" is an automation that never ran.
  function visibleAutos() {
    const q = page.q.trim().toLowerCase();
    return page.autos.filter((a) =>
      (!q || (a.recipeName || "").toLowerCase().includes(q)
            || projectLabel(a.project).toLowerCase().includes(q))
      && (!page.proj || a.project === page.proj)
      && (!page.onoff || (page.onoff === "on") === !!a.enabled)
      && (!page.result || (a.last ? a.last.group : "none") === page.result));
  }

  function paintFilters() {
    for (const id of ["tpProj", "tpTaskProj"]) {
      const s = $(id);
      const keep = s.value;
      s.replaceChildren();
      const all = el("option", "", "All projects");
      all.value = "";
      s.append(all);
      for (const row of env.projects()) {
        const o = el("option", "", env.projectName(row));
        o.value = row.id;
        s.append(o);
      }
      s.value = keep;
    }
  }

  function render() {
    for (const b of $("tasksPage").querySelectorAll('[role="tab"]')) {
      const on = b.dataset.ptab === page.tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    $("tpAutoCount").textContent = String(page.autos.length);
    $("tpTaskCount").textContent = String(page.tasks.filter((t) => !t.done).length);
    $("tpAutos").hidden = page.tab !== "automations";
    $("tpTasks").hidden = page.tab !== "tasks";
    if (page.tab === "automations") renderAutos(); else renderTasks();
  }

  // Rebuild a list in place: its scroll kept, and focus put back on the
  // same control of the same row (`data-id`, `data-k`), so J / K, X and a
  // re-read never drop the owner's place.
  function redraw(list, draw) {
    const at = document.activeElement;
    const row = at && list.contains(at) ? at.closest("[data-id]") : null;
    const id = row ? row.dataset.id : "";
    const k = at && at.dataset ? at.dataset.k || "" : "";
    const top = list.scrollTop;
    list.replaceChildren();
    draw(list);
    list.scrollTop = top;
    if (!id) return;
    const again = [...list.querySelectorAll("[data-id]")].find((r) => r.dataset.id === id);
    const to = again && ((k && again.querySelector('[data-k="' + k + '"]')) || jkTarget(again));
    if (to) to.focus();
  }
  function jkTarget(row) {
    return row.querySelector('[data-k="main"]') || row.querySelector("[data-k]");
  }

  function renderAutos() {
    const n = [...page.checked].filter((id) => page.autos.some((a) => a.id === id)).length;
    $("tpBulk").hidden = n === 0;
    $("tpBulkCount").textContent = n + " selected";
    redraw($("tpList"), (list) => {
      if (page.error) { list.append(el("p", "lbnote lberr", page.error)); return; }
      const shown = visibleAutos();
      const head = el("div", "colh");
      head.setAttribute("aria-hidden", "true");
      head.append(el("span"), el("span"), el("span", "", "Name"),
                  el("span", "nx", "Next"), el("span", "", "Last result"), el("span"));
      list.append(head);
      if (!page.autos.length) {
        list.append(el("p", "lbnote", "No automations yet. Make one from a "
          + "recipe with New automation."));
      } else if (!shown.length) {
        list.append(el("p", "lbnote", "Nothing matches these filters."));
      }
      for (const [pid, autos] of groups(shown, (a) => a.project)) {
        const g = el("div", "pgh", projectLabel(pid));
        g.append(el("span", "c", String(autos.length)));
        list.append(g);
        for (const a of autos) list.append(autoRow(a));
      }
    });
    renderHistory();
  }

  function autoRow(a) {
    const name = a.recipeName || "(recipe deleted)";
    const on = page.checked.has(a.id);
    const row = el("div", "ar" + (a.enabled ? "" : " off") + (on ? " sel" : ""));
    row.dataset.id = a.id;
    if (page.hist && a.id === page.selected) row.setAttribute("aria-current", "true");
    const box = button("cbx", undefined, "Select " + name);
    box.setAttribute("role", "checkbox");
    box.setAttribute("aria-checked", String(on));
    box.dataset.k = "sel";
    box.append(env.icon(14, 2.6, TICK));
    box.onclick = () => toggleChecked(a.id);
    const toggle = sw(a.enabled, name + " on", (v) => act(() => setEnabled([a.id], v)));
    toggle.dataset.k = "sw";
    const main = button("main2", undefined, "Past runs of " + name);
    main.dataset.k = "main";
    main.append(el("b", "", name), a.problem ? el("small", "pr", a.problem)
      : el("small", "", a.schedule.label));
    main.onclick = () => showHistory(a.id);
    const last = el("span", "lr");
    last.append(mark(a.last));
    const run = button("rb", undefined, "Run " + name + " now");
    run.dataset.k = "run";
    run.append(env.icon(14, 1.6, PLAY), document.createTextNode("Run"));
    run.onclick = () => act(() => runNow(a.id));
    row.append(box, toggle, main, el("span", "c2 nx", a.nextWords || ""), last, run);
    return row;
  }

  function toggleChecked(id) {
    if (page.checked.has(id)) page.checked.delete(id); else page.checked.add(id);
    renderAutos();
  }

  async function showHistory(id) {
    page.selected = id;
    page.hist = true;
    page.runs = [];
    renderAutos();
    try { page.runs = await runsOf(id); } catch (e) { env.showError(e.message); }
    if (page.selected !== id || !page.hist) return;
    renderHistory();
    // A phone's drawer is a sheet over the list: focus goes into it.
    if (env.isPhone()) $("tpHist").querySelector(".hx").focus();
  }

  function closeHistory() {
    const id = page.selected;
    page.hist = false;
    renderAutos();
    const row = [...$("tpList").querySelectorAll("[data-id]")].find((r) => r.dataset.id === id);
    if (row) jkTarget(row).focus();
  }

  function renderHistory() {
    const box = $("tpHist");
    const a = page.hist ? page.autos.find((x) => x.id === page.selected) : null;
    box.hidden = !a;
    $("tpScrim").hidden = !a;
    box.replaceChildren();
    if (!a) return;
    const name = a.recipeName || "(recipe deleted)";
    const hh = el("div", "hh");
    const t = el("div", "t");
    const close = button("hx", undefined, "Close run history");
    close.dataset.help = "Close";
    close.append(env.icon(16, 1.7, CROSS));
    close.onclick = () => closeHistory();
    t.append(el("h3", "", name), close);
    hh.append(t, el("span", "s", [projectLabel(a.project), a.schedule.label, a.nextWords,
      a.channels.length ? "Notifies " + a.channels.join(", ") : "Notifies nobody"]
      .filter(Boolean).join(". ") + "."));
    const acts = el("div", "b");
    const run = button("hbtn", undefined, "Run " + name + " now");
    run.append(env.icon(14, 1.6, PLAY), document.createTextNode("Run now"));
    run.onclick = () => act(() => runNow(a.id));
    acts.append(run);
    // A heartbeat has no recipe and is edited where its checklist is: the
    // Library's Automations tab (docs/proactive.md PR-9). So is a reminder,
    // in its own editor there (PR-17).
    const own = a.kind === "heartbeat" || a.kind === "reminder";
    if (!own) {
      const edit = button("hbtn", "Edit", "Edit " + name);
      edit.onclick = () => openEditor(a);
      acts.append(edit);
    }
    hh.append(acts);
    if (a.kind === "heartbeat") {
      hh.append(el("p", "lbhelp", "Edit its checklist in the Library's "
        + "Automations tab."));
    } else if (a.kind === "reminder") {
      hh.append(el("p", "lbhelp", "Edit it in the Library's Automations "
        + "tab. It runs no model: Butler says it and sends it."));
    }
    const runs = el("div", "runs");
    if (!page.runs.length) runs.append(el("p", "lbnote", "No runs yet."));
    for (const r of page.runs) runs.append(runRow(r));
    runs.append(el("p", "lbhelp tpfoot", "Runs are kept up to 50; identical "
      + "skips in a row fold into one line with a count."));
    box.append(hh, runs);
  }

  // One run: the served word with its shape, its number, and WHEN in the
  // daemon's words (`whenWords`, `tookWords`); then why, what it said, and
  // what each channel did.
  function runRow(r) {
    const row = el("div", "run" + (r.occurrenceCount > 1 ? " fold" : ""));
    const h1 = el("div", "h1");
    h1.append(mark(r), el("span", "n", "#" + r.n),
              el("span", "d", [r.whenWords, r.tookWords].filter(Boolean).join(", ")));
    row.append(h1);
    const why = [];
    if (r.trigger === "manual") why.push("run now");
    if (r.neededYou && r.status !== "needs_you") why.push("needed you on the way");
    if (r.occurrenceCount > 1 && r.lastOccurrenceWords) {
      why.push(r.occurrenceCount + " times, last " + r.lastOccurrenceWords);
    }
    if (r.reason) why.push(r.reason);
    if (why.length) row.append(el("div", "nt", sentence(why.join(". "))));
    if (r.summary) row.append(el("div", "sum", r.summary));
    for (const n of r.notify || []) {
      const bad = Object.values(n.channels || {}).some((c) => !c.ok);
      row.append(el("div", "nt" + (bad ? " bad" : ""), sentence(noticeWords(n).join(", "))));
    }
    if (r.chat) {
      const open = button("linkbtn", "Open chat", "Open the chat of run " + r.n);
      open.onclick = () => { closePage(false); env.openChat(r.chat); };
      row.append(open);
    }
    return row;
  }

  function sentence(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

  // -- tasks across projects -- //
  // "An agent" is a task the daemon recorded an `agent` for; "You" is the
  // owner's own (`source`).
  function visibleTasks() {
    const q = page.tq.trim().toLowerCase();
    return page.tasks.filter((t) =>
      (!q || t.text.toLowerCase().includes(q))
      && (!page.tproj || t.project === page.tproj)
      && (!page.tsrc || (page.tsrc === "agent" ? !!t.agent : t.source === page.tsrc)));
  }

  function renderTasks() {
    for (const [id, v] of [["tpTaskState", page.tstate], ["tpTaskSrc", page.tsrc]]) {
      for (const b of $(id).querySelectorAll("button")) {
        b.setAttribute("aria-pressed", String(b.dataset.v === v));
      }
    }
    redraw($("tpTaskList"), (list) => {
      if (page.error) { list.append(el("p", "lbnote lberr", page.error)); return; }
      if (page.tstate !== "done") list.append(taskAdd());
      const shown = visibleTasks();
      const open = groups(shown.filter((t) => !t.done), (t) => t.project)
        .flatMap(([, tasks]) => tasks);
      const done = shown.filter((t) => t.done)
        .sort((a, b) => String(b.doneAt).localeCompare(String(a.doneAt)));
      const rows = page.tstate === "open" ? open : page.tstate === "done" ? done
        : [...open, ...done];
      if (!rows.length) {
        list.append(el("p", "lbnote", page.tasks.length
          ? "Nothing matches these filters." : "No tasks yet."));
      }
      for (const t of rows) list.append(taskRow(t));
    });
  }

  function taskRow(t) {
    const row = el("div", "tk" + (t.done ? " done" : ""));
    row.dataset.id = t.id;
    const chk = button("ck" + (t.done ? " on" : ""), undefined,
                       (t.done ? "Mark not done: " : "Mark done: ") + t.text);
    chk.setAttribute("aria-pressed", String(!!t.done));
    chk.dataset.help = t.done ? "Not done" : "Done";
    chk.dataset.k = "main";
    chk.append(env.icon(14, 2.6, TICK));
    chk.onclick = () => act(() => api("POST", "/api/tasks", { id: t.id, done: !t.done }));
    const about = t.about ? env.projectRow(t.about) : null;
    const sub = [about ? "For " + env.projectName(about) : "",
                 t.why ? "Why: " + t.why : ""].filter(Boolean).join(". ");
    const text = el("span", "tx");
    text.append(el("span", "t1", t.text), el("span", "t2", sub || (t.source === "owner"
      ? "Yours" : "Butler's")));
    // Who added it and WHEN, in the daemon's words (`addedWords`,
    // `doneWords`); the agent is the served `agent` (the chat's harness).
    const who = t.agent || (t.source === "butler" ? "Butler" : "you");
    const src = el("span", "src");
    const av = el("span", "av", who.charAt(0));
    av.setAttribute("aria-hidden", "true");
    src.append(av, document.createTextNode(who + ", "
      + (t.done ? "done " + t.doneWords : t.addedWords)));
    row.append(chk, text, el("span", "tag", projectLabel(t.project)), src);
    if (!t.done && env.projectRow(t.project)) {
      const start = button("st", "Start", "Start a chat on: " + t.text);
      start.dataset.k = "start";
      // Starting a task does NOT mark it done (AC-449).
      start.onclick = async () => {
        if (await env.runInNewChat(t.project, t.text, start)) closePage(false);
      };
      row.append(start);
    } else row.append(el("span"));
    return row;
  }

  // The one new-task box: filed under the Project filter's project, else
  // the project on screen, else the root row.
  function addTarget() {
    for (const id of [page.tproj, env.projectId()]) {
      if (id && env.projectRow(id)) return id;
    }
    return env.rootId();
  }

  function taskAdd() {
    const pid = addTarget();
    const wrap = el("label", "newtask");
    wrap.append(env.icon(16, 1.8, [["path", { d: "M12 5v14M5 12h14" }]]));
    const input = el("input");
    input.maxLength = 200;
    input.placeholder = "New task, filed under " + projectLabel(pid);
    input.setAttribute("aria-label", "New task in " + projectLabel(pid));
    const err = el("p", "lbnote lberr");
    err.hidden = true;
    const commit = async () => {
      if (!input.value.trim()) return;
      try {
        await api("POST", "/api/tasks", { project: pid, text: input.value });
        input.value = "";
        await load();
      } catch (e) { err.textContent = e.message; err.hidden = false; }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
    });
    wrap.append(input);
    const box = el("div", "newwrap");
    box.append(wrap, err);
    return box;
  }

  async function act(fn) {
    try { await fn(); } catch (e) { env.showError(e.message); }
    changed();
  }

  // Something changed here or on another device (the `library` frame).
  function changed() {
    env.libraryChanged();
    if (page.open) load();
  }

  // ---- the page's own controls ------------------------------------------ //
  for (const b of $("tasksPage").querySelectorAll('[role="tab"]')) {
    b.onclick = () => { page.tab = b.dataset.ptab; render(); };
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      page.tab = page.tab === "automations" ? "tasks" : "automations";
      render();
      $("tasksPage").querySelector('[role="tab"][aria-selected="true"]').focus();
      e.preventDefault();
    });
  }
  $("tasksPageClose").onclick = () => closePage();
  $("tpNew").onclick = () => openEditor(null, page.proj || env.projectId());
  $("tpScrim").onclick = () => closeHistory();
  $("tpSearch").addEventListener("input", () => { page.q = $("tpSearch").value; renderAutos(); });
  $("tpProj").onchange = () => { page.proj = $("tpProj").value; renderAutos(); };
  $("tpOnOff").onchange = () => { page.onoff = $("tpOnOff").value; renderAutos(); };
  $("tpResult").onchange = () => { page.result = $("tpResult").value; renderAutos(); };
  $("tpTaskSearch").addEventListener("input", () => { page.tq = $("tpTaskSearch").value; renderTasks(); });
  $("tpTaskProj").onchange = () => { page.tproj = $("tpTaskProj").value; renderTasks(); };
  for (const [id, key] of [["tpTaskState", "tstate"], ["tpTaskSrc", "tsrc"]]) {
    for (const b of $(id).querySelectorAll("button")) {
      b.onclick = () => { page[key] = b.dataset.v; renderTasks(); };
    }
  }
  const chosen = () => [...page.checked].filter((id) => page.autos.some((a) => a.id === id));
  $("tpBulkOn").onclick = () => act(() => setEnabled(chosen(), true));
  $("tpBulkOff").onclick = () => act(() => setEnabled(chosen(), false));
  $("tpBulkRun").onclick = () => act(async () => { for (const id of chosen()) await runNow(id); });
  $("tpBulkClear").onclick = () => { page.checked.clear(); renderAutos(); };

  // THE PAGE'S OWN KEYS (the mock's legend): J / K move between rows, X
  // selects the automation in focus, R runs it now. ONE listener on the page
  // element, never the document, so they exist only while focus is on the
  // page; never while typing, with a modifier or on a repeat. Escape is not
  // one of them: the page keeps its one place in the global order (AC-112).
  $("tasksPage").addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const key = e.key.toLowerCase();
    if (key !== "j" && key !== "k" && key !== "x" && key !== "r") return;
    if (e.target.closest("input, select, textarea, [contenteditable]")) return;
    const list = page.tab === "automations" ? $("tpList") : $("tpTaskList");
    const rows = [...list.querySelectorAll("[data-id]")];
    if (!rows.length) return;
    const here = list.contains(e.target) ? e.target.closest("[data-id]") : null;
    e.preventDefault();
    if (key === "j" || key === "k") {
      const step = key === "j" ? 1 : -1;
      const i = here ? rows.indexOf(here) + step : (step > 0 ? 0 : rows.length - 1);
      const to = rows[Math.max(0, Math.min(rows.length - 1, i))];
      jkTarget(to).focus();
      to.scrollIntoView({ block: "nearest" });
      return;
    }
    if (page.tab !== "automations" || !here) return;
    if (key === "x") toggleChecked(here.dataset.id);
    else act(() => runNow(here.dataset.id));
  });

  return { openEditor, openPage, closePage, isOpen,
           changed: () => { if (page.open) load(); },
           setEnabled, runNow, mark, sw, projectLabel };
}
