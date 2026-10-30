// IN-PAGE TERMINALS (docs/chats-and-terminals.md CT-4, CT-5, AC-765..AC-767).
//
// A project's terminals are listed beside its chats; one opens as a view that
// takes the conversation column's body (the composer stays, so the dictation
// button stays). xterm.js is VENDORED under web/vendor/xterm/ by
// scripts/fetch-xterm.py and loaded from there only -- never a CDN.
//
// Everything about a terminal is the daemon's: the list and the refusals. A terminal is always the plain shell
// (AC-797): the page names no agent and no command line.
// Terminal output is NEVER spoken: nothing here reaches a speech path
// (owner decision 3, AC-766). Dictation reaches a terminal as typed text with
// no Enter (AC-765): `dictate()` is the one door app.js calls.
//
// Every string that came from the daemon is set with textContent.

const VENDOR = "vendor/xterm/";
const RECONNECT_MS = 1000;
const END_ARM_MS = 4000;
const FONT_PX = 14;

export function mountTerminals(env) {
  const $ = (id) => document.getElementById(id);
  let list = [];
  let hidden = false;          // 403 or 404: the daemon offers this device none
  let view = null;             // { id, term, fit, ws, ro, retry, exited }
  let targeted = false;        // the terminal, not the composer, takes dictation
  let ctrl = false;            // the key row's sticky Ctrl
  let vendor = null;           // promise: xterm.js is on the page
  let endArmed = 0;

  async function api(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  }

  function words(res) {
    if (res.status === 403) {
      return "Terminals are owner-only: this device cannot open or use one.";
    }
    return (res.data && res.data.error) || ("HTTP " + res.status);
  }

  // -- the list ------------------------------------------------------------ //

  async function refresh() {
    let res;
    try { res = await api("GET", "/api/terminals"); } catch (e) { return; }
    if (res.status === 403 || res.status === 404) {
      if (!hidden || list.length) { hidden = true; list = []; env.repaint(); }
      return;
    }
    if (!res.ok || !res.data) return;
    const next = res.data.terminals || [];
    const changed = JSON.stringify(next) !== JSON.stringify(list) || hidden;
    hidden = false;
    list = next;
    if (view && !list.some((t) => t.id === view.id)) close();
    if (changed) env.repaint();
  }

  function byId(id) { return list.find((t) => t.id === id) || null; }

  function label(t) {
    const row = env.projectRow(t.project);
    return (t.title || "terminal") + (row ? " - " + env.projectName(row) : "");
  }

  // -- the sidebar --------------------------------------------------------- //

  // The project's open terminals, one line each, then the "Open terminal"
  // action: one tap opens a shell.
  function rowsFor(row) {
    if (hidden) return [];
    const out = [];
    for (const t of list.filter((x) => x.project === row.id)) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pterm termrow";
      b.dataset.termId = t.id;
      if (view && view.id === t.id) b.setAttribute("aria-current", "true");
      const name = document.createElement("span");
      name.className = "tn";
      name.textContent = t.title || "terminal";
      const st = document.createElement("span");
      st.className = "ts";
      st.textContent = t.alive ? "running" : "ended";
      b.append(name, st);
      b.setAttribute("aria-label", (t.title || "shell") + " terminal, "
                     + (t.alive ? "running" : "ended"));
      b.onclick = () => show(t.id);
      out.push(b);
    }
    const open = document.createElement("button");
    open.type = "button";
    open.className = "pterm";
    const word = document.createElement("span");
    word.textContent = "Open terminal";
    open.append(word);
    open.onclick = () => launch(row, open);
    out.push(open);
    return out;
  }

  async function launch(row, btn) {
    btn.disabled = true;
    try {
      const res = await api("POST", "/api/terminals",
        { project: row.id, cols: 80, rows: 24 });
      if (!res.ok) { env.showError(words(res)); return; }
      const t = res.data && res.data.terminal;
      if (!t) { env.showError("the daemon answered with no terminal"); return; }
      list.push(t);
      env.repaint();
      show(t.id);
    } catch (e) {
      env.showError("could not open a terminal: " + (e.message || e));
    } finally {
      btn.disabled = false;
    }
  }

  // -- xterm.js, from /vendor only ------------------------------------------ //

  function loadScript(name) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = VENDOR + name;
      s.onload = resolve;
      s.onerror = () => reject(new Error(name + " did not load; run "
        + "scripts/fetch-xterm.py"));
      document.head.append(s);
    });
  }

  function loadVendor() {
    if (!vendor) {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = VENDOR + "xterm.css";
      document.head.append(css);
      vendor = loadScript("xterm.js").then(() => loadScript("addon-fit.js"))
        .catch((e) => { vendor = null; css.remove(); throw e; });
    }
    return vendor;
  }

  // -- the view -------------------------------------------------------------- //

  function send(frame) {
    if (!view || !view.ws || view.ws.readyState !== WebSocket.OPEN) return false;
    view.ws.send(JSON.stringify(frame));
    return true;
  }

  function paintBar() {
    const t = view && byId(view.id);
    $("termTitle").textContent = t ? label(t) : "";
    $("termCtrl").setAttribute("aria-pressed", String(ctrl));
  }

  function connect() {
    const v = view;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(scheme + "//" + location.host + "/ws/terminal/"
                             + encodeURIComponent(v.id));
    v.ws = ws;
    ws.onopen = () => send({ type: "resize", cols: v.term.cols, rows: v.term.rows });
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) {
        env.showError("terminal sent a frame that is not JSON");
        return;
      }
      if (m.type === "replay") { v.term.reset(); v.term.write(m.data || ""); }
      else if (m.type === "output") v.term.write(m.data || "");
      else if (m.type === "exit") {
        v.exited = true;
        v.term.write("\r\n[process exited, code " + m.code + "]\r\n");
        refresh();
      }
    };
    ws.onclose = () => {
      if (view !== v || v.exited || v.ws !== ws) return;
      v.retry = setTimeout(() => { if (view === v) connect(); }, RECONNECT_MS);
    };
  }

  function ctrlify(d) {
    if (!ctrl) return d;
    ctrl = false;
    paintBar();
    if (d.length !== 1) return d;
    const code = d.toUpperCase().charCodeAt(0);
    if (d === " ") return "\0";
    return code >= 64 && code <= 95 ? String.fromCharCode(code - 64) : d;
  }

  async function show(id) {
    if (!byId(id)) return;
    if (view && view.id === id) { focusTerm(); return; }
    close();
    try { await loadVendor(); } catch (e) { env.showError(e.message); return; }
    env.closeDrawer();
    const host = $("termHost");
    host.replaceChildren();
    const cs = getComputedStyle(host);
    const term = new window.Terminal({
      cursorBlink: true,
      fontSize: FONT_PX,
      fontFamily: getComputedStyle(document.documentElement)
        .getPropertyValue("--mono") || "monospace",
      scrollback: 5000,
      theme: { background: cs.backgroundColor, foreground: cs.color },
    });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    $("termView").hidden = false;
    document.querySelector(".col").classList.add("termopen");
    term.open(host);
    view = { id, term, fit, ws: null, ro: null, retry: 0, exited: false };
    term.onData((d) => send({ type: "input", data: ctrlify(d) }));
    term.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
    view.ro = new ResizeObserver(() => { try { fit.fit(); } catch (e) { /* hidden */ } });
    view.ro.observe(host);
    try { fit.fit(); } catch (e) { /* no size yet; the observer refits */ }
    targeted = true;
    paintBar();
    connect();
    env.repaint();
    focusTerm();
  }

  function focusTerm() { if (view) { targeted = true; view.term.focus(); } }

  // Leave the view; the terminal itself keeps running.
  function close() {
    if (!view) return;
    const v = view;
    view = null;
    targeted = false;
    ctrl = false;
    clearTimeout(v.retry);
    if (v.ro) v.ro.disconnect();
    if (v.ws) v.ws.close();
    v.term.dispose();
    $("termHost").replaceChildren();
    $("termView").hidden = true;
    document.querySelector(".col").classList.remove("termopen");
    endArmed = 0;
    $("termEnd").textContent = "End terminal";
    env.repaint();
  }

  // Two presses: the first arms, the second ends the process.
  async function endTerminal() {
    if (!view) return;
    const now = Date.now();
    if (!endArmed || now - endArmed > END_ARM_MS) {
      endArmed = now;
      $("termEnd").textContent = "End it?";
      setTimeout(() => {
        if (endArmed === now) { endArmed = 0; $("termEnd").textContent = "End terminal"; }
      }, END_ARM_MS);
      return;
    }
    const id = view.id;
    const res = await api("DELETE", "/api/terminals/" + encodeURIComponent(id));
    if (!res.ok && res.status !== 404) { env.showError(words(res)); return; }
    list = list.filter((t) => t.id !== id);
    close();
    refresh();
  }

  // -- the key row (phone): what a soft keyboard lacks ----------------------- //

  const KEYS = {
    Esc: "\x1b", Tab: "\t", Up: "\x1b[A", Down: "\x1b[B",
    Right: "\x1b[C", Left: "\x1b[D", Enter: "\r",
  };

  function buildKeyRow() {
    const row = $("termKeys");
    for (const name of ["Esc", "Tab", "Ctrl", "Left", "Down", "Up", "Right", "Enter"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = name;
      b.dataset.termKey = name;
      // Pressing a key must not steal focus from the terminal, or the soft
      // keyboard closes on every tap.
      b.addEventListener("pointerdown", (e) => e.preventDefault());
      if (name === "Ctrl") {
        b.id = "termCtrl";
        b.setAttribute("aria-pressed", "false");
        b.onclick = () => { ctrl = !ctrl; paintBar(); };
      } else {
        b.onclick = () => { send({ type: "input", data: ctrlify(KEYS[name]) }); };
      }
      row.append(b);
    }
  }

  // -- dictation (CT-5, AC-765) ---------------------------------------------- //

  // True when the words went to the terminal (or were refused loudly); false
  // when the composer should take them as it always did. Typed text only: no
  // Enter, so line breaks inside the words become spaces.
  function dictate(text) {
    if (!view || $("termView").hidden || !targeted) return false;
    const typed = text.replace(/[\r\n]+/g, " ").trim();
    if (!send({ type: "input", data: typed })) {
      env.showError("the terminal is reconnecting; this dictation did not "
                    + "reach it: " + typed);
      return true;
    }
    env.toast("Typed into the terminal");
    return true;
  }

  // -- wiring ---------------------------------------------------------------- //

  buildKeyRow();
  $("termClose").onclick = () => close();
  $("termEnd").onclick = () => endTerminal();
  $("termView").addEventListener("focusin", () => { targeted = true; });
  env.composer().addEventListener("focus", () => { targeted = false; });
  // Keys typed in a terminal are the terminal's: Escape must never reach the
  // page's global handler, where it would stop Butler's turn (AC-111, AC-112).
  $("termView").addEventListener("keydown", (e) => e.stopPropagation());

  return { refresh, rowsFor, dictate, hide: close, active: () => (view ? view.id : "") };
}
