// THE FILE EXPLORER (docs/file-explorer.md 3.3, AC-303..AC-315), plus the
// rich preview and the drop-to-upload of docs/rich-chat.md RC-12/RC-13.
//
// A right-hand panel on a desk and a sheet on a phone, showing the files of
// the project the conversation ON SCREEN belongs to -- `env.projectId()` is
// app.js's `currentProjectId()`, so there is no second way to name a
// project here (AC-311). Every rule about what may be listed or read is the
// DAEMON's (`daemon/files.py`); this file draws its answers and adds none.
//
// WHAT THIS FILE MAY DO ON THE NETWORK, and a grep gate holds it to it
// (test/test_file_explorer_page.py, AC-310): the two GETs, through the ONE
// helper `get`; the raw-bytes URL `rawUrl` builds for artifacts.js to use as
// an element source (an image, a player, a download link -- never fetched
// here); ONE write, the upload POST in `upload` (RC-13: a dropped file is
// ADDED to the folder it was dropped on, never over an existing one -- the
// daemon answers 409); and `env.setRight("files" | "")` -- the desk's open-or-shut
// is `right_panel` in the shortcuts store (docs/automations.md AU-10,
// AC-466), ONE value app.js holds for the right column, so the library and
// this panel can never both be drawn there. It replaced the bool
// `files_open` (AC-312). AC-8 bans browser storage, AC-41 bans naming a
// setting. A PHONE never writes it (D-2): the sheet is a gesture and dies
// with the page, as the drawer does.
//
// FILE NAMES AND CONTENTS ARE UNTRUSTED. Every string reaches the DOM through
// `textContent` and every element through `createElement` -- a project file
// holding `<img onerror>` must render as characters (AC-314), because a
// script in this origin can drive every route Butler has.
//
// The expanded folders are PAGE memory, per project, and die with the page
// (the spec's choice, Orca's own). No watcher: open folders are re-listed
// when the panel comes on screen, on the refresh button, and when a turn ends
// in a conversation of the shown project (AC-315) -- the moment a coding
// agent is most likely to have written.

import { renderArtifact } from "./artifacts.js";

// What artifacts.js draws in the preview (Step 6c adds `code`, drawn as the
// chat's code card). Plain `text` keeps the reading view below (`#filesText`:
// the reading face, scrolling sideways), which the chat does not need.
const ART_KINDS = new Set(["image", "audio", "video", "csv", "markdown", "code",
                           "file"]);

export function mountFiles(env) {
  const $ = (id) => document.getElementById(id);

  let sheet = false;              // the phone's, a gesture
  let shown = false;              // what was last painted
  let project = null;             // the row id whose files are drawn
  let seq = 0;                    // bumped on re-root; late answers are dropped
  const listings = new Map();     // relative dir path -> the daemon's listing
  const openDirs = new Map();     // project id -> Set of open dir paths
  let rootError = "";             // the daemon's sentence for the root, if any
  let focusPath = null;           // the tree's one tab stop
  let previewing = null;          // the relative path being previewed, or null
  let refreshTimer = 0;
  let wantTreeFocus = false;      // opened by key before the listing landed

  // The desk's, from the one `right_panel` value app.js holds (AC-466).
  function deskOpen() { return env.rightPanel() === "files"; }
  function visible() { return env.isPhone() ? sheet : deskOpen(); }

  function opened() {
    if (!openDirs.has(project)) openDirs.set(project, new Set());
    return openDirs.get(project);
  }

  // THE ONE DOOR TO THE NETWORK. `route` is "files" or "file", and nothing
  // else ever reaches it (AC-310). The root is never sent: the daemon
  // resolves it from the row id on every request (AC-304).
  async function get(route, path) {
    const r = await fetch("/api/project/" + encodeURIComponent(project)
                          + "/" + route + "?path=" + encodeURIComponent(path));
    const doc = await r.json().catch(() => ({}));
    // The daemon's own sentence -- it names the rule that refused (AC-305,
    // AC-306, AC-309) -- rather than a status code nobody can act on.
    if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    return doc;
  }

  // The raw bytes of one project file, as an ELEMENT SOURCE for
  // artifacts.js (RC-12). Built from the row id and the relative path this
  // panel already holds; the daemon applies the explorer's own rules to it.
  function rawUrl(path, download) {
    return "/api/project/" + encodeURIComponent(project) + "/raw?path="
      + encodeURIComponent(path) + (download ? "&download=1" : "");
  }

  // THE ONE WRITE (RC-13). The file is the body; the daemon refuses a name
  // that exists (409), a size past its cap (413) and anything the explorer's
  // rules forbid, each with a sentence that is shown as written.
  async function upload(dir, file) {
    const r = await fetch("/api/project/" + encodeURIComponent(project)
                          + "/upload?path=" + encodeURIComponent(dir)
                          + "&name=" + encodeURIComponent(file.name || "file"),
                          { method: "POST",
                            headers: { "Content-Type": "application/octet-stream" },
                            body: file });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    return doc;
  }

  function join(base, name) { return base ? base + "/" + name : name; }

  // What a press on an entry does. A LINK carries no dir/file bit of its own
  // on the wire, but the daemon gives a size only to a file it can stat
  // inside the project, so an inside link with a size is a file and one
  // without is a directory. An `outside` link opens nothing (AC-306).
  function kindOf(e) {
    if (e.outside) return "outside";
    if (e.kind === "dir" || e.kind === "file") return e.kind;
    return typeof e.size === "number" ? "file" : "dir";
  }

  function fmtSize(n) {
    if (typeof n !== "number") return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  // ---- showing and hiding ------------------------------------------------ //

  function paint() {
    const show = visible();
    $("filesPanel").hidden = !show;
    document.documentElement.dataset.files = show ? "open" : "shut";
    // The header's panel toggle and the tabs are app.js's to paint, from
    // BOTH panels at once (docs/lantern-ui.md AC-699).
    env.painted();
    // Coming ON screen is one of the three refresh moments (FE-6).
    if (show && !shown) refreshSoon(0);
    shown = show;
  }

  // Where focus goes when the panel leaves: the header's panel toggle, which
  // is on screen at every width.
  function focusOpener() { $("panelBtn").focus(); }

  function afterShowChange(viaKey) {
    const inside = $("filesPanel").contains(document.activeElement);
    paint();
    if (!visible()) {
      if (inside) focusOpener();
      return;
    }
    // Into the panel when the KEYBOARD opened it, or when it is the phone's
    // sheet (which covers the page); a desk press on the button leaves focus
    // on the button, because the panel is not modal.
    if (viaKey || env.isPhone()) focusInside();
  }

  function focusInside() {
    const item = currentItem();
    if (item) item.focus();
    else {
      // Nothing to land on yet: hold the close button, and hand focus to the
      // first row when the listing arrives (`render`).
      $("filesClose").focus();
      wantTreeFocus = true;
    }
  }

  function toggle(viaKey) {
    if (env.isPhone()) {
      setSheet(!sheet, viaKey);
      return;
    }
    // app.js repaints BOTH right-column panels from the new value and
    // writes it (AC-466), so the library shuts in the same moment.
    env.setRight(deskOpen() ? "" : "files", viaKey);
  }

  function setSheet(open, viaKey) {
    sheet = open;
    if (open) { env.closeDrawer(); env.closePops(); env.closeOtherSheet(); }
    afterShowChange(viaKey);
  }

  function sheetOpen() { return env.isPhone() && sheet; }
  function closeSheet() { if (sheet) setSheet(false, false); }

  // `right_panel` changed -- a press here or on the library, or the daemon's
  // answer on a shortcuts read (AC-312, AC-466). Changes only the DESK's
  // state; a phone reads it and draws nothing from it.
  function rightChanged(viaKey) { afterShowChange(viaKey); }

  // ---- following the conversation (AC-311) -------------------------------- //

  function follow() {
    const id = env.projectId();
    if (id !== project) {
      project = id;
      seq += 1;
      listings.clear();
      rootError = "";
      focusPath = null;
      closePreview();
    }
    paintHead();
    // Redrawn on every call, not only on a re-root: the ROWS arrive with the
    // shortcuts answer, often after the conversation, and a project that was
    // "not in the list" a moment ago may be in it now.
    render();
    const row = project ? env.projectRow(project) : null;
    if (visible() && row && !listings.has("") && !rootError) refreshSoon(0);
    paint();
  }

  function paintHead() {
    const row = project ? env.projectRow(project) : null;
    $("filesProject").textContent = row ? env.projectName(row) : "Files";
  }

  function turnEnded(chatId) {
    if (!visible() || !project) return;
    if (env.chatProject(chatId) === project) refreshSoon(400);
  }

  // ---- listing ------------------------------------------------------------ //

  function refreshSoon(ms) {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { refreshTimer = 0; refresh(); }, ms);
  }

  async function refresh() {
    if (!visible()) return;
    if (!project || !env.projectRow(project)) { render(); return; }
    const mine = ++seq;
    const paths = ["", ...[...opened()].sort()];
    if (!listings.has("")) note("Loading...");
    const results = await Promise.all(paths.map((p) =>
      get("files", p).then((d) => [p, d], (e) => [p, e])));
    if (mine !== seq) return;
    listings.clear();
    rootError = "";
    for (const [p, d] of results) {
      if (d instanceof Error) {
        // A folder that has gone since it was opened simply closes; the ROOT
        // failing is the daemon's sentence, drawn where the tree would be.
        if (p === "") rootError = d.message;
        else opened().delete(p);
        continue;
      }
      listings.set(p, d);
    }
    render();
  }

  async function expand(path) {
    opened().add(path);
    if (!listings.has(path)) {
      const mine = seq;
      try {
        const doc = await get("files", path);
        if (mine !== seq) return;
        listings.set(path, doc);
      } catch (e) {
        if (mine !== seq) return;
        opened().delete(path);
        note(e.message);
        return;
      }
    }
    render();
  }

  function collapse(path) {
    for (const p of [...opened()]) {
      if (p === path || p.startsWith(path + "/")) opened().delete(p);
    }
    render();
  }

  function note(text) {
    $("filesNote").textContent = text || "";
    $("filesNote").hidden = !text;
  }

  // ---- drawing ------------------------------------------------------------ //

  function lineItem(text) {
    const li = document.createElement("li");
    li.setAttribute("role", "none");
    li.className = "fpnote";
    li.textContent = text;
    return li;
  }

  function buildLevel(ul, doc, level) {
    const n = doc.entries.length;
    doc.entries.forEach((e, i) => {
      const path = join(doc.path, e.name);
      const kind = kindOf(e);
      const li = document.createElement("li");
      li.setAttribute("role", "treeitem");
      li.setAttribute("aria-level", String(level));
      li.setAttribute("aria-setsize", String(n));
      li.setAttribute("aria-posinset", String(i + 1));
      li.dataset.path = path;
      li.dataset.kind = kind;
      li.tabIndex = -1;
      const row = document.createElement("div");
      row.className = "fprow";
      row.style.paddingLeft = (6 + (level - 1) * 14) + "px";
      const caret = document.createElement("span");
      caret.className = "fpcaret";
      if (kind === "dir") caret.append(env.icon(12, 2, [["path", { d: "M9 5.5l6.5 6.5L9 18.5" }]]));
      const nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = e.name;
      // The export's glyph per row: a folder or a page (shape, not colour).
      const glyph = kind === "dir"
        ? env.icon(16, 1.6, [["path", { d: "M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" }]])
        : env.icon(16, 1.6, [["path", { d: "M7 3.5h6.5l4.5 4.5v10.5a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z" }],
                             ["path", { d: "M13.5 3.5V8H18" }]]);
      glyph.classList.add("fpglyph");
      row.append(caret, glyph, nm);
      let label = e.name;
      if (kind === "dir") {
        const open = opened().has(path) && listings.has(path);
        li.setAttribute("aria-expanded", String(open));
        label += ", folder";
      }
      if (e.locked) {
        row.append(env.icon(13, 1.6, [
          ["rect", { x: 5.5, y: 10.5, width: 13, height: 9.5, rx: 2 }],
          ["path", { d: "M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" }]]));
        label += ", secret, not previewed";
      }
      if (kind === "outside") {
        li.setAttribute("aria-disabled", "true");
        label += ", link outside the project";
      }
      if (kind === "file") li.setAttribute("aria-selected", String(path === previewing));
      if (kind === "file" && typeof e.size === "number") {
        const sz = document.createElement("span");
        sz.className = "sz";
        sz.textContent = fmtSize(e.size);
        row.append(sz);
        label += ", " + fmtSize(e.size);
      }
      li.setAttribute("aria-label", label);
      li.append(row);
      if (kind === "dir" && opened().has(path) && listings.has(path)) {
        const group = document.createElement("ul");
        group.setAttribute("role", "group");
        buildLevel(group, listings.get(path), level + 1);
        li.append(group);
      }
      ul.append(li);
    });
    // WHAT IS NOT DRAWN IS SAID (AC-307): a folder that is not listed looks
    // exactly like one that does not exist.
    if (doc.truncated) {
      ul.append(lineItem("Showing " + doc.entries.length + " of "
                         + doc.total + "."));
    }
    if (doc.skipped && doc.skipped.length) {
      const k = doc.skipped.length;
      ul.append(lineItem(k + (k === 1 ? " folder" : " folders")
                         + " hidden: " + doc.skipped.join(", ")));
    }
    if (!n && !(doc.skipped && doc.skipped.length)) ul.append(lineItem("Empty."));
  }

  function items() {
    return [...$("filesTree").querySelectorAll('[role="treeitem"]')];
  }

  function currentItem() {
    const all = items();
    return all.find((li) => li.dataset.path === focusPath) || all[0] || null;
  }

  function render() {
    const tree = $("filesTree");
    const hadFocus = tree.contains(document.activeElement)
      || (wantTreeFocus && document.activeElement === $("filesClose"));
    tree.replaceChildren();
    const row = project ? env.projectRow(project) : null;
    if (!project) note("No project.");
    else if (!row) note("This conversation's project was removed from the "
                        + "list, so there are no files to show.");
    else if (rootError) note(rootError);
    else if (listings.has("")) note("");
    const root = listings.get("");
    if (row && root && !rootError) buildLevel(tree, root, 1);
    // ONE TAB STOP (AC-313): the roving item is 0 and every other is -1.
    const cur = currentItem();
    if (cur) {
      focusPath = cur.dataset.path;
      cur.tabIndex = 0;
      if (hadFocus) cur.focus();
      wantTreeFocus = false;
    }
  }

  function focusItem(li) {
    if (!li) return;
    for (const other of items()) other.tabIndex = -1;
    li.tabIndex = 0;
    focusPath = li.dataset.path;
    li.focus();
  }

  function activate(li) {
    const kind = li.dataset.kind;
    const path = li.dataset.path;
    if (kind === "dir") {
      if (li.getAttribute("aria-expanded") === "true") collapse(path);
      else expand(path);
    } else if (kind === "file") {
      openPreview(path);
    }
  }

  $("filesTree").addEventListener("click", (e) => {
    const li = e.target.closest('[role="treeitem"]');
    if (!li) return;
    focusItem(li);
    activate(li);
  });

  $("filesTree").addEventListener("keydown", (e) => {
    const li = e.target.closest('[role="treeitem"]');
    if (!li || e.ctrlKey || e.altKey || e.metaKey) return;
    const all = items();
    const i = all.indexOf(li);
    const dir = li.dataset.kind === "dir";
    const expanded = li.getAttribute("aria-expanded") === "true";
    switch (e.key) {
      case "ArrowDown": focusItem(all[i + 1]); break;
      case "ArrowUp": focusItem(all[i - 1]); break;
      case "Home": focusItem(all[0]); break;
      case "End": focusItem(all[all.length - 1]); break;
      case "ArrowRight":
        if (dir && !expanded) expand(li.dataset.path);
        else if (dir) focusItem(li.querySelector('[role="treeitem"]'));
        break;
      case "ArrowLeft":
        if (dir && expanded) collapse(li.dataset.path);
        else focusItem(li.parentElement.closest('[role="treeitem"]'));
        break;
      case "Enter":
      case " ":
        activate(li);
        break;
      default:
        return;
    }
    e.preventDefault();
  });

  // ---- preview (AC-308, AC-309, AC-314) ---------------------------------- //
  // UNDER the tree, which stays on screen (docs/lantern-ui.md CU-16, which
  // supersedes file-explorer.md 3.4's swap): the tree keeps its one tab stop
  // and its keys, and the previewed row is marked `aria-selected`.

  function closePreview() {
    previewing = null;
    $("filesPreview").hidden = true;
    $("filesArt").replaceChildren();
    $("filesText").textContent = "";
  }

  async function openPreview(path) {
    const mine = seq;
    previewing = path;
    for (const li of items()) {
      if (li.dataset.kind === "file") {
        li.setAttribute("aria-selected", String(li.dataset.path === path));
      }
    }
    $("filesPreview").hidden = false;
    $("filesPath").textContent = path;
    $("filesSize").textContent = "";
    $("filesPrevNote").textContent = "Loading...";
    $("filesPrevNote").hidden = false;
    $("filesText").textContent = "";
    $("filesText").hidden = true;
    $("filesArt").replaceChildren();
    $("filesArt").hidden = true;
    $("filesPreview").scrollIntoView({ block: "nearest" });
    let doc;
    try {
      doc = await get("file", path);
    } catch (e) {
      if (mine !== seq || previewing !== path) return;
      // A secret, a vanished file, a refusal: the daemon's own sentence.
      $("filesPrevNote").textContent = e.message;
      return;
    }
    if (mine !== seq || previewing !== path) return;
    $("filesSize").textContent = fmtSize(doc.size);
    const art = ART_KINDS.has(doc.kind);
    const notes = [];
    if (doc.binary && !art) {
      notes.push("Binary file" + (doc.type ? " (" + doc.type + ")" : "")
                 + " -- not shown.");
    }
    if (doc.truncated && !doc.binary) {
      notes.push("Only the start of this " + fmtSize(doc.size)
                 + " file is shown.");
    }
    if (doc.lossy) {
      notes.push("Not valid UTF-8 -- undecodable bytes are shown as "
                 + "replacement characters.");
    }
    $("filesPrevNote").textContent = notes.join(" ");
    $("filesPrevNote").hidden = !notes.length;
    if (art) {
      // RC-12: the chat's renderer, fed the text already fetched (never a
      // second request for it) and this panel's own raw URLs for the rest.
      // `head: false` -- the preview's own head already names the file.
      $("filesArt").append(renderArtifact(
        { id: path, kind: doc.kind, media: doc.media,
          name: path.split("/").pop(), size: doc.size },
        { url: rawUrl(path, false), downloadUrl: rawUrl(path, true),
          previewUrl: null, markdown: env.markdown, head: false,
          text: doc.binary ? undefined : (doc.text || ""),
          truncated: !!doc.truncated }));
      $("filesArt").hidden = false;
      return;
    }
    // INERT TEXT (AC-314): textContent, never markup.
    $("filesText").textContent = doc.binary ? "" : (doc.text || "");
    $("filesText").hidden = !!doc.binary;
  }

  function back() {
    closePreview();
    render();
    const cur = currentItem();
    if (cur) cur.focus();
  }

  $("filesBack").onclick = back;
  $("filesPreview").addEventListener("keydown", (e) => {
    if (e.key !== "Backspace" || e.ctrlKey || e.altKey || e.metaKey) return;
    e.preventDefault();
    back();
  });

  // The ABSOLUTE path, so it can be pasted into a prompt. Joined with the
  // separator the row itself is spelled with; the row is the daemon's. The
  // outcome is said by the page's one toast.
  $("filesCopy").onclick = () => {
    const row = project ? env.projectRow(project) : null;
    if (!row || previewing === null) return;
    const sep = row.path.includes("\\") ? "\\" : "/";
    const abs = row.path.replace(/[\\/]+$/, "") + sep
      + previewing.split("/").join(sep);
    if (!navigator.clipboard) { env.toast("No clipboard here"); return; }
    navigator.clipboard.writeText(abs).then(
      () => env.toast("Copied"), () => env.toast("Copy failed"));
  };

  // ---- drop to upload (RC-13) ------------------------------------------- //
  // Onto a folder row: into that folder. Onto a file row: into the folder
  // holding it. Anywhere else in the panel's body: the project root. Only a
  // drag carrying FILES is taken; one call per file, then the folder is
  // opened and re-listed so the new file is on screen.
  function dropDir(target) {
    const li = target && target.closest ? target.closest('[role="treeitem"]') : null;
    if (!li) return "";
    if (li.dataset.kind === "dir") return li.dataset.path;
    const p = li.dataset.path || "";
    return p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
  }
  function dragHasFiles(e) {
    return !!(e.dataTransfer && [...e.dataTransfer.types].includes("Files"));
  }
  // THE VISIBLE DROP TARGET (Step 6c): `#filesDrop`, hidden at rest, drawn
  // while a drag carrying files is over the panel's body, and naming where
  // the file will land -- the project, or the folder row under the pointer.
  // A drag that ends anywhere (a drop, a leave, an Escape) hides it again.
  function markDrop(target) {
    for (const r of $("filesBody").querySelectorAll(".dropping")) r.classList.remove("dropping");
    $("filesBody").classList.remove("dropping");
    $("filesDrop").hidden = !target;
    if (!target) return;
    const li = target.closest ? target.closest('[role="treeitem"][data-kind="dir"]') : null;
    (li ? li.querySelector(".fprow") : $("filesBody")).classList.add("dropping");
    const row = project ? env.projectRow(project) : null;
    const where = (row ? env.projectName(row) : "this project")
      + (li ? "/" + li.dataset.path : "");
    $("filesDropWords").textContent = "Drop a file here to upload it to " + where;
  }
  $("filesBody").addEventListener("dragover", (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    markDrop(e.target);
  });
  $("filesBody").addEventListener("dragleave", (e) => {
    if (!$("filesBody").contains(e.relatedTarget)) markDrop(null);
  });
  // A drag cancelled with Escape or dropped outside the window fires no
  // `drop` here; `dragend` reaches the document only for drags that began in
  // the page, so the leave above is what clears a drag from the desktop.
  document.addEventListener("dragend", () => markDrop(null));
  $("filesBody").addEventListener("drop", async (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    markDrop(null);
    if (!project || !env.projectRow(project)) { note("No project to upload into."); return; }
    const dir = dropDir(e.target);
    const mine = seq;
    const failed = [];
    for (const file of [...(e.dataTransfer.files || [])]) {
      try { await upload(dir, file); }
      catch (err) { failed.push((file.name || "file") + ": " + err.message); }
    }
    if (mine !== seq) return;
    if (dir) opened().add(dir);
    await refresh();
    if (failed.length) note(failed.join(" "));
  });

  $("filesRefresh").onclick = () => refreshSoon(0);
  $("filesClose").onclick = () => {
    if (env.isPhone()) closeSheet();
    else toggle(false);
  };
  $("filesScrim").onclick = () => closeSheet();

  return { follow, rightChanged, toggle, turnEnded, paint, sheetOpen,
           closeSheet, shown: visible };
}
