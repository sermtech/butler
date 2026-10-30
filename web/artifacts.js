// THINGS SHOWN IN THE CHAT AND IN THE EXPLORER (docs/rich-chat.md, RC-7..RC-10,
// AC-632..AC-639). One renderer, two callers: app.js draws the artifacts a
// turn showed (`show_file`, `show_ui`, the owner's own attachments), and
// files.js draws a previewed project file with it.
//
// WHERE AN ELEMENT SOURCE MAY COME FROM (RC-8, AC-632). This module is the
// ONLY place in `web/` that creates `img`, `audio` or `video`, and every
// `src`/`href` it sets is one of the URLs its CALLER passed in `opts`
// (`url`, `previewUrl`, `downloadUrl`). Those are built from ids and paths
// the page already holds -- `artifactUrls` below for `/api/artifacts/`,
// files.js for `/api/project/<id>/raw` -- and never from an entry's name, caption,
// title or any text a model wrote. A URL in model text never becomes a
// request this page makes. Pinned by test/test_rich_chat_page.py.
//
// EVERY STRING IS TEXT. Names, captions, CSV cells, chart labels and `ui`
// block fields reach the DOM through `textContent`; elements are built with
// `createElement`/`createElementNS`. No markup sink exists here (AC-94's rule,
// carried over). Markdown goes through the chat's OWN renderer, passed in as
// `opts.markdown`, so there is one parser and its closed element set.
//
// SVG and HTML are never rendered (RC-7, AC-636): the daemon classifies them
// as `code`, and an `image` entry whose media type says svg is drawn as code
// here too.
//
// THE CARDS (docs/lantern-ui.md Step 6c, the export's look): each artifact is
// a card on `--side` with a HEAD line -- its name and what it is -- and, for
// an image, links to the full size and the download (the caller's URLs, as
// above). Audio is a STYLED player (CU-16 supersedes RC-7's native controls):
// a play button, a seek slider and the time, the duration read from the
// element's own metadata; no waveform, because nothing here has the samples.
// Video keeps the browser's controls. The explorer passes `head: false`: its
// preview already names the file.

const SVG_NS = "http://www.w3.org/2000/svg";
const CSV_ROWS = 200;                 // RC-7: header plus the first 200 rows
const UI_MAX_DEPTH = 3;               // RC-9: Card nesting
const TONES = 6;                      // series tone classes s0..s5 in index.html
const SHOWN = new Set(["image", "audio", "video", "markdown", "text", "code",
                       "csv", "ui"]);
const TEXT_KINDS = new Set(["markdown", "text", "code", "csv"]);

// The chat's URLs for one artifact, from the RECORD's ids (RC-8).
export function artifactUrls(chat, id) {
  const url = "/api/artifacts/" + encodeURIComponent(chat) + "/"
    + encodeURIComponent(id);
  return { url, previewUrl: url + "?preview=1", downloadUrl: url + "?download=1" };
}

// The chat's entry point. The URLs are applied LAST so nothing in `opts`
// can replace them.
export function renderChatArtifact(chat, entry, opts) {
  return renderArtifact(entry, { ...(opts || {}), ...artifactUrls(chat, entry.id) });
}

export function humanSize(n) {
  if (typeof n !== "number" || !isFinite(n)) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = String(text);
  return e;
}

function svgEl(tag, attrs, cls) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const k of Object.keys(attrs || {})) e.setAttribute(k, String(attrs[k]));
  if (cls) e.setAttribute("class", cls);
  return e;
}

function noteEl(text) { return el("p", "artnote", text); }

// A 24-unit icon in the page's own shape (app.js `icon`).
function glyph(size, shapes, solid) {
  const svg = svgEl("svg", { viewBox: "0 0 24 24", width: size, height: size,
                             "aria-hidden": "true",
                             fill: solid ? "currentColor" : "none",
                             stroke: solid ? "none" : "currentColor",
                             "stroke-width": 1.6, "stroke-linecap": "round",
                             "stroke-linejoin": "round" });
  for (const [tag, attrs] of shapes) svg.append(svgEl(tag, attrs));
  return svg;
}
const PLAY = () => glyph(18, [["path", { d: "M8 5.5v13l10.5-6.5z" }]], true);
const PAUSE = () => glyph(18, [["rect", { x: 6.5, y: 5, width: 4, height: 14, rx: 1 }],
                               ["rect", { x: 13.5, y: 5, width: 4, height: 14, rx: 1 }]], true);
const EXPAND = () => glyph(16, [["path", { d: "M14 4.5h5.5V10M10 19.5H4.5V14M19.5 4.5 13 11M4.5 19.5 11 13" }]]);
const DOWNLOAD = () => glyph(16, [["path", { d: "M12 4.5V15M7.5 10.5 12 15l4.5-4.5M5 19.5h14" }]]);

// An icon-only LINK in a card head: named for a screen reader, its one or
// two words through the page's one tooltip (`data-help`). The CALLER sets
// its href from `opts` (the AC-632 pin reads every href assignment).
function headLink(label, help, icon) {
  const a = el("a", "ib");
  a.rel = "noopener noreferrer";
  a.setAttribute("aria-label", label);
  a.setAttribute("data-help", help);
  a.append(icon);
  return a;
}

// The card's head: the name in `--fg`, then what it is ("PNG, 1.2 MB"), then
// any links. `meta` is a span the caller may rewrite once it knows more (an
// image's size in pixels).
function cardHead(entry, meta, links) {
  const head = el("div", "ah");
  head.append(el("b", "", String(entry.name || "file")), meta, el("span", "grow"));
  for (const a of links || []) head.append(a);
  return head;
}

function metaWords(entry) {
  return [typeLabel(entry), humanSize(entry.size)].filter(Boolean).join(", ");
}

function cannotShow(what) {
  return noteEl("Cannot show this" + (what ? " (" + what + ")" : "") + ".");
}

function typeLabel(entry) {
  const m = /\.([A-Za-z0-9]{1,8})$/.exec(String(entry.name || ""));
  if (m) return m[1].toUpperCase();
  return String(entry.media || "file");
}

function kindOf(entry) {
  const kind = SHOWN.has(entry.kind) ? entry.kind : "file";
  if (kind === "image" && /svg/i.test(String(entry.media || ""))) return "code";
  return kind;
}

// "name, 1.2 MB, PDF" as a download link (AC-637). The ONE href is the
// caller's download URL.
function fileLink(entry, opts, words) {
  const a = el("a", "artfile");
  a.href = opts.downloadUrl;
  a.setAttribute("download", String(entry.name || ""));
  a.rel = "noopener noreferrer";
  a.append(el("span", "n", words || entry.name || "file"));
  const meta = [humanSize(entry.size), typeLabel(entry)].filter(Boolean).join(", ");
  if (meta) a.append(el("span", "k", meta));
  return a;
}

// The whole of a text kind's body, from the caller's text (the explorer
// already holds it) or the route's `?preview=1` answer. A preview cut by
// the daemon says so with `X-Butler-Truncated: 1` (RC-7).
function withText(body, entry, opts, draw) {
  const finish = (text, cut) => {
    draw(text, cut);
    if (cut) body.append(fileLink(entry, opts, "download full"));
    if (opts.grew) opts.grew();
  };
  if (typeof opts.text === "string") { finish(opts.text, !!opts.truncated); return; }
  const wait = noteEl("Loading...");
  body.append(wait);
  fetch(opts.previewUrl).then(async (r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return [await r.text(), r.headers.get("X-Butler-Truncated") === "1"];
  }).then(([text, cut]) => { wait.remove(); finish(text, cut); },
          (e) => { wait.textContent = "Could not load this: " + e.message; });
}

// ---- image (AC-633) -------------------------------------------------------- //

// The picture, then its head: name, pixel size once it has loaded (read off
// the element, never a field a model wrote), full size and download.
function imageCard(box, body, entry, opts) {
  const a = el("a", "artimg");
  a.href = opts.url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  const img = document.createElement("img");
  img.alt = String(entry.caption || entry.name || "image");
  img.loading = "lazy";
  img.decoding = "async";
  const meta = el("span", "k", metaWords(entry));
  img.addEventListener("load", () => {
    if (img.naturalWidth) {
      meta.textContent = [img.naturalWidth + " x " + img.naturalHeight,
                          humanSize(entry.size)].filter(Boolean).join(", ");
    }
    if (opts.grew) opts.grew();
  });
  img.addEventListener("error", () => {
    a.replaceWith(noteEl("Could not load " + (entry.name || "this image") + "."));
  });
  img.src = opts.url;
  a.append(img);
  body.append(a);
  if (opts.head === false) return;
  const full = headLink("Open " + (entry.name || "the image") + " full size",
                        "Full size", EXPAND());
  full.href = opts.url;
  full.target = "_blank";
  const down = headLink("Download " + (entry.name || "the image"), "Download",
                        DOWNLOAD());
  down.href = opts.downloadUrl;
  down.setAttribute("download", String(entry.name || ""));
  box.append(cardHead(entry, meta, [full, down]));
}

// ---- audio: the styled player (CU-16) -------------------------------------- //

function clock(sec) {
  if (typeof sec !== "number" || !isFinite(sec) || sec < 0) return "-:--";
  const s = Math.floor(sec);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// A hidden <audio> (no `controls`, so it draws nothing) driven by one button,
// one slider and the time. Every word and state is painted from the
// element's own events, so the button never says "Pause" over silence.
function audioPlayer(entry, opts) {
  const name = String(entry.name || "audio");
  const wrap = el("div", "player");
  const media = document.createElement("audio");
  media.preload = "metadata";
  const play = el("button", "play");
  play.type = "button";
  const meta = el("div", "pmeta");
  const seek = document.createElement("input");
  seek.type = "range";
  seek.className = "seek";
  seek.min = "0";
  seek.max = "0";
  seek.step = "0.1";
  seek.value = "0";
  seek.disabled = true;
  seek.setAttribute("aria-label", "Position in " + name);
  seek.setAttribute("data-help", "Position");
  meta.append(el("b", "", name), seek);
  const time = el("span", "pt", "0:00 / -:--");
  const paintPlay = () => {
    const on = !media.paused && !media.ended;
    play.replaceChildren(on ? PAUSE() : PLAY());
    play.setAttribute("aria-label", (on ? "Pause " : "Play ") + name);
    play.setAttribute("data-help", on ? "Pause" : "Play");
  };
  const paintTime = () => {
    time.textContent = clock(media.currentTime || 0) + " / " + clock(media.duration);
    seek.value = String(media.currentTime || 0);
    seek.setAttribute("aria-valuetext", clock(media.currentTime || 0));
  };
  media.addEventListener("loadedmetadata", () => {
    const known = isFinite(media.duration) && media.duration > 0;
    seek.max = known ? String(media.duration) : "0";
    seek.disabled = !known;
    paintTime();
    if (opts.grew) opts.grew();
  });
  media.addEventListener("timeupdate", paintTime);
  for (const ev of ["play", "pause", "ended"]) media.addEventListener(ev, paintPlay);
  media.addEventListener("error", () => {
    wrap.replaceWith(noteEl("Could not load " + name + "."));
  });
  play.addEventListener("click", () => {
    if (media.paused) media.play().catch(() => paintPlay());
    else media.pause();
  });
  seek.addEventListener("input", () => { media.currentTime = Number(seek.value); });
  paintPlay();
  media.src = opts.url;
  wrap.append(play, meta, time, media);
  return wrap;
}

// ---- CSV (AC-635) --------------------------------------------------------- //

// RFC 4180 as far as a preview needs: quoted fields, doubled quotes inside
// them, separators and line breaks inside quotes, CRLF.
export function parseCsv(text, sep) {
  const rows = [];
  let row = [], cell = "", quoted = false, started = false;
  let src = String(text);
  if (src.charCodeAt(0) === 0xFEFF) src = src.slice(1);     // a byte-order mark
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (quoted) {
      if (c === "\"") {
        if (src[i + 1] === "\"") { cell += "\""; i += 1; } else quoted = false;
      } else cell += c;
      continue;
    }
    if (c === "\"" && cell === "") { quoted = true; started = true; continue; }
    if (c === sep) { row.push(cell); cell = ""; started = true; continue; }
    if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i += 1;
      row.push(cell); rows.push(row);
      row = []; cell = ""; started = false;
      continue;
    }
    cell += c;
    started = true;
  }
  if (started || cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

function tableEl(head, rows) {
  const table = el("table", "arttable");
  const thead = el("thead");
  const tr = el("tr");
  for (const h of head) tr.append(el("th", "", h == null ? "" : h));
  thead.append(tr);
  table.append(thead);
  const tbody = el("tbody");
  for (const r of rows) {
    const row = el("tr");
    for (let i = 0; i < Math.max(head.length, r.length); i++) {
      row.append(el("td", "", r[i] == null ? "" : r[i]));
    }
    tbody.append(row);
  }
  if (rows.length) table.append(tbody);
  return table;
}

function drawCsv(body, entry, opts, text, cut) {
  const tsv = /tab-separated/i.test(String(entry.media || ""))
    || /\.tsv$/i.test(String(entry.name || ""));
  const rows = parseCsv(text, tsv ? "\t" : ",");
  if (!rows.length) { body.append(noteEl("Empty.")); return; }
  const wrap = el("div", "arttablewrap");
  wrap.append(tableEl(rows[0], rows.slice(1, 1 + CSV_ROWS)));
  body.append(wrap);
  const more = rows.length - 1 - CSV_ROWS;
  if (more > 0) {
    body.append(noteEl(more.toLocaleString("en-US") + (cut ? "+" : "")
                       + " more rows"));
  } else if (!cut) {
    const n = rows.length - 1;
    body.append(noteEl(n + (n === 1 ? " row" : " rows") + ", all shown."));
  }
  // AC-635: a CSV always carries its download link; a cut one already has
  // "download full" from `withText`.
  if (!cut) body.append(fileLink(entry, opts, "download"));
}

// ---- show_ui (RC-9, AC-638, AC-639) --------------------------------------- //

function fmtNum(v) {
  if (typeof v !== "number" || !isFinite(v)) return String(v);
  return String(Number(v.toPrecision(4)));
}

function chartEl(b, line) {
  const fig = el("figure", "uichart");
  if (b.title) fig.append(el("figcaption", "uititle", b.title));
  const labels = (b.labels || []).map((l) => String(l));
  const series = Array.isArray(b.series) ? b.series : [];
  const n = labels.length;
  // Drawn at about the width it will be SHOWN, because SVG text scales with
  // the viewBox: a 640-wide drawing squeezed into a 360 px phone column
  // printed 11 px labels at about 6 px (measured 2026-10-08, rich-chat).
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  const W = narrow ? 360 : 640, H = narrow ? 230 : 280;
  const L = narrow ? 40 : 52, R = 10, T = 12, B = 36;
  const pw = W - L - R, ph = H - T - B;
  let lo = 0, hi = 0;
  for (const s of series) {
    for (const v of (s.values || [])) {
      if (typeof v === "number" && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
  }
  if (hi === lo) hi = lo + 1;
  const y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph;
  const band = n ? pw / n : pw;
  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
                             preserveAspectRatio: "xMidYMid meet" });
  svg.setAttribute("aria-label", String(b.title || (line ? "line chart" : "bar chart")));
  const TICKS = 4;
  for (let k = 0; k <= TICKS; k++) {
    const v = lo + ((hi - lo) * k) / TICKS;
    svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }, "grid"));
    const t = svgEl("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" }, "ax");
    t.textContent = fmtNum(v);
    svg.append(t);
  }
  if (lo < 0) svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(0), y2: y(0) }, "zero"));
  const step = Math.max(1, Math.ceil(n / (narrow ? 6 : 12)));
  labels.forEach((lab, i) => {
    if (i % step) return;
    const t = svgEl("text", { x: L + band * (i + 0.5), y: H - B + 18,
                              "text-anchor": "middle" }, "ax");
    t.textContent = lab.length > 14 ? lab.slice(0, 13) + "..." : lab;
    svg.append(t);
  });
  series.forEach((s, si) => {
    const tone = "s" + (si % TONES);
    const name = String(s.name == null ? "" : s.name);
    const values = Array.isArray(s.values) ? s.values : [];
    if (line) {
      let run = [];
      const flush = () => {
        if (run.length > 1) svg.append(svgEl("polyline", { points: run.join(" ") }, "ln " + tone));
        run = [];
      };
      values.forEach((v, i) => {
        if (typeof v !== "number" || !isFinite(v)) { flush(); return; }
        const cx = L + band * (i + 0.5), cy = y(v);
        run.push(cx.toFixed(1) + "," + cy.toFixed(1));
        if (n <= 40) {
          const dot = svgEl("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 3 }, "pt " + tone);
          const tip = svgEl("title");
          tip.textContent = name + " " + (labels[i] || "") + ": " + fmtNum(v);
          dot.append(tip);
          svg.append(dot);
        }
      });
      flush();
    } else {
      const k = Math.max(1, series.length);
      const bw = (band * 0.78) / k;
      values.forEach((v, i) => {
        if (typeof v !== "number" || !isFinite(v)) return;
        const x = L + band * i + band * 0.11 + bw * si;
        const top = y(Math.max(v, 0)), bot = y(Math.min(v, 0));
        const r = svgEl("rect", { x: x.toFixed(1), y: top.toFixed(1),
                                  width: Math.max(1, bw - 1).toFixed(1),
                                  height: Math.max(0.5, bot - top).toFixed(1),
                                  rx: 2 }, "bar " + tone);
        const tip = svgEl("title");
        tip.textContent = name + " " + (labels[i] || "") + ": " + fmtNum(v);
        r.append(tip);
        svg.append(r);
      });
    }
  });
  fig.append(svg);
  // The legend names every series in words, so tone is never the only cue.
  if (series.length > 1 || (series[0] && series[0].name)) {
    const legend = el("div", "uilegend");
    series.forEach((s, si) => {
      const item = el("span", "li");
      item.append(el("span", "sw s" + (si % TONES)), String(s.name == null ? "" : s.name));
      legend.append(item);
    });
    fig.append(legend);
  }
  return fig;
}

function statEl(b) {
  const box = el("div", "uistat");
  box.append(el("span", "l", b.label == null ? "" : b.label));
  const v = el("span", "v", typeof b.value === "number" ? fmtNum(b.value) : String(b.value == null ? "" : b.value));
  if (b.unit != null && b.unit !== "") v.append(el("span", "u", " " + b.unit));
  box.append(v);
  if (typeof b.delta === "number" && isFinite(b.delta)) {
    // The sign is a WORD-level cue, not a colour (AC-168).
    box.append(el("span", "d", (b.delta > 0 ? "+" : "") + fmtNum(b.delta)));
  }
  return box;
}

function blockEl(b, opts, depth) {
  const type = b && typeof b === "object" ? String(b.type) : "";
  switch (type) {
    case "Card": {
      const card = el("section", "uicard");
      if (b.title) card.append(el("h3", "uititle", b.title));
      if (depth >= UI_MAX_DEPTH) card.append(cannotShow("nested too deep"));
      else drawBlocks(card, b.blocks, opts, depth + 1);
      return card;
    }
    case "Text": {
      const md = el("div", "artmd");
      opts.markdown(String(b.markdown == null ? "" : b.markdown), md);
      return md;
    }
    case "Stat": return statEl(b);
    case "Table": {
      const wrap = el("div", "arttablewrap");
      wrap.append(tableEl((b.columns || []).map((c) => String(c)),
                          (b.rows || []).map((r) => (Array.isArray(r) ? r : []))));
      return wrap;
    }
    case "List": {
      const ul = el("ul", "uilist");
      for (const it of (b.items || [])) ul.append(el("li", "", it));
      return ul;
    }
    case "BarChart": return chartEl(b, false);
    case "LineChart": return chartEl(b, true);
    default: return cannotShow(type || "no type");
  }
}

// A stored record is drawn whatever it holds: a block that does not fit the
// closed set, or throws while being drawn, is one plain note and the blocks
// around it still draw (AC-639). Consecutive Stats share one row.
function drawBlocks(into, blocks, opts, depth) {
  if (!Array.isArray(blocks)) { into.append(cannotShow("no blocks")); return; }
  let stats = null;
  for (const b of blocks) {
    let node;
    try { node = blockEl(b, opts, depth); } catch (e) { node = cannotShow(e.message); }
    if (node.className === "uistat") {
      if (!stats) { stats = el("div", "uistats"); into.append(stats); }
      stats.append(node);
    } else {
      stats = null;
      into.append(node);
    }
  }
}

function drawUi(body, entry, opts) {
  const wait = noteEl("Loading...");
  body.append(wait);
  fetch(opts.url).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then((doc) => {
    wait.remove();
    const title = (doc && doc.title) || entry.title;
    if (title) body.append(el("h3", "uititle", title));
    drawBlocks(body, doc && doc.blocks, opts, 1);
    if (opts.grew) opts.grew();
  }).catch((e) => { wait.textContent = "Could not load this: " + e.message; });
}

// ---- the one entry point -------------------------------------------------- //

// `entry`: `{id, kind, media, name, size, caption?, title?}` (docs/rich-chat.md
// section 11). `opts`: `{url, previewUrl, downloadUrl, markdown(text, into),
// text?, truncated?, grew?()}` -- the caller's URLs are the ONLY element
// sources; `text` skips the preview fetch; `grew` is called when late content
// changed the node's height (sticky scroll).
export function renderArtifact(entry, opts) {
  const kind = kindOf(entry);
  const box = el("div", "art");
  box.dataset.kind = kind;
  const body = el("div", "artbody");
  box.append(body);
  // A text card's head goes ABOVE what is about to be read; an image's goes
  // under the picture (the export's order, `imageCard`).
  const head = () => {
    if (opts.head !== false) {
      box.insertBefore(cardHead(entry, el("span", "k", metaWords(entry))), body);
    }
  };
  try {
    if (kind === "image") {
      imageCard(box, body, entry, opts);
    } else if (kind === "audio") {
      body.append(audioPlayer(entry, opts));
    } else if (kind === "video") {
      head();
      const media = document.createElement("video");
      media.controls = true;
      media.preload = "metadata";
      media.playsInline = true;
      media.addEventListener("loadedmetadata", () => { if (opts.grew) opts.grew(); });
      media.setAttribute("aria-label", String(entry.name || kind));
      media.src = opts.url;
      body.append(media);
    } else if (kind === "ui") {
      drawUi(body, entry, opts);
    } else if (TEXT_KINDS.has(kind)) {
      head();
      withText(body, entry, opts, (text, cut) => {
        if (kind === "csv") { drawCsv(body, entry, opts, text, cut); return; }
        if (kind === "markdown") {
          const md = el("div", "artmd");
          opts.markdown(text, md);
          body.append(md);
          return;
        }
        const pre = el("pre", kind === "code" ? "artcode" : "arttext");
        if (kind === "code") pre.append(el("code", "", text));
        else pre.textContent = text;
        body.append(pre);
      });
    } else {
      body.append(fileLink(entry, opts));
    }
  } catch (e) {
    body.replaceChildren(cannotShow(e.message));
  }
  if (entry.caption) box.append(el("p", "artcap", entry.caption));
  return box;
}
