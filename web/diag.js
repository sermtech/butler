// On-device diagnostics for the voice loop. Written because the failing device
// is an iPad (no easy console): everything prints into the page so it can be
// read and copied. Probes, in order: environment, raw WebAssembly memory
// reservation limits (the documented iOS OOM trigger), the onnxruntime module,
// model reachability, and finally each ONNX session INDIVIDUALLY so we learn
// exactly which model/stage dies instead of one opaque "no available backend".
(function () {
  const out = () => document.getElementById("diag");

  function log(line) {
    const el = out();
    if (el) el.textContent += line + "\n";
    try { console.log("[diag]", line); } catch { /* */ }
  }
  function err(e) {
    const name = (e && e.name) || "Error";
    const msg = (e && e.message) || String(e);
    return `${name}: ${msg}`;
  }

  /** Try to reserve a WebAssembly.Memory of `mb` megabytes. */
  function probeMemory(mb, shared) {
    const pages = Math.round((mb * 1024 * 1024) / 65536);
    try {
      const desc = { initial: 1, maximum: pages };
      if (shared) desc.shared = true;
      const m = new WebAssembly.Memory(desc);
      // Touch it so a lazy reservation actually commits the initial page.
      const ok = m.buffer.byteLength >= 65536;
      return ok ? "ok" : "odd(buffer empty)";
    } catch (e) {
      return "FAIL " + err(e);
    }
  }

  async function head(url) {
    try {
      const r = await fetch(url, { method: "GET" });
      const buf = await r.arrayBuffer();
      return `${r.status} ${r.headers.get("content-type") || "?"} ${buf.byteLength} bytes`;
    } catch (e) {
      return "FETCH FAIL " + err(e);
    }
  }

  async function run() {
    const el = out();
    if (el) el.textContent = "";
    log("=== ENVIRONMENT ===");
    log("time        " + new Date().toISOString());
    log("url         " + location.href);
    log("ua          " + navigator.userAgent);
    log("platform    " + (navigator.platform || "?"));
    log("cores       " + (navigator.hardwareConcurrency || "?"));
    log("deviceMemory " + (navigator.deviceMemory != null ? navigator.deviceMemory + " GB" : "n/a (not exposed on iOS)"));
    log("crossOriginIsolated " + (typeof crossOriginIsolated !== "undefined" ? crossOriginIsolated : "?"));
    log("SharedArrayBuffer   " + (typeof SharedArrayBuffer !== "undefined"));
    log("WebAssembly         " + (typeof WebAssembly !== "undefined"));
    log("navigator.gpu (WebGPU) " + (navigator.gpu ? "present" : "absent"));
    log("secureContext " + window.isSecureContext);

    log("");
    log("=== WASM MEMORY RESERVATION PROBE (non-shared) ===");
    log("(this is the documented iOS OOM trigger: a large *maximum* reservation)");
    for (const mb of [16, 64, 128, 256, 512, 1024, 2048, 4096]) {
      log(String(mb).padStart(5) + " MB  " + probeMemory(mb, false));
    }
    log("");
    log("=== WASM MEMORY RESERVATION PROBE (shared / threaded) ===");
    for (const mb of [16, 256, 1024, 2048]) {
      log(String(mb).padStart(5) + " MB  " + probeMemory(mb, true));
    }

    log("");
    log("=== ONNXRUNTIME MODULE ===");
    let ort = null;
    try {
      ort = await window.ortReady;
      log("module import  ok");
      log("version        " + ((ort.env && ort.env.versions && ort.env.versions.common) || "?"));
      log("wasmPaths      " + (ort.env.wasm.wasmPaths || "(default)"));
      log("numThreads     " + ort.env.wasm.numThreads);
      log("simd           " + ort.env.wasm.simd);
      log("proxy          " + ort.env.wasm.proxy);
    } catch (e) {
      log("module import  FAIL " + err(e));
      log("(cannot continue without onnxruntime)");
      return;
    }

    // The wake models are the shared two plus one per word that is ON, as the
    // daemon last said (`window.butlerWake`, written by app.js; AC-805).
    const cfg = window.butlerWake;
    if (!cfg) {
      log("");
      log("Butler has not named the wake words yet (no `ready` on this page).");
      log("(cannot continue without the word list)");
      return;
    }
    const shared = window.WakeWord.WAKE_WORD_MODEL_URLS;
    const models = [["melspectrogram", shared.mel], ["embedding_model", shared.embedding]]
      .concat(cfg.words.map((w) => [w.id, w.url]));

    log("");
    log("=== FILE REACHABILITY (under the live wasmPaths) ===");
    const base = ort.env.wasm.wasmPaths || new URL("./", location.href).href;
    for (const f of ["ort-wasm-simd.wasm", "ort-wasm.wasm", "ort-wasm-simd-threaded.wasm"]) {
      log(f.padEnd(30) + " " + await head(base + f));
    }
    for (const [, url] of models) {
      log(url.padEnd(30) + " " + await head(url));
    }

    log("");
    log("=== ONNX SESSIONS, ONE AT A TIME (the real test) ===");
    const sessions = [];
    for (const [name, url] of models) {
      const t0 = performance.now();
      try {
        const s = await ort.InferenceSession.create(url, { executionProviders: ["wasm"] });
        sessions.push(s);
        log(name.padEnd(18) + " ok   " + Math.round(performance.now() - t0) + " ms  in=" +
            JSON.stringify(s.inputNames) + " out=" + JSON.stringify(s.outputNames));
      } catch (e) {
        log(name.padEnd(18) + " FAIL " + Math.round(performance.now() - t0) + " ms  " + err(e));
        log("  ^ this is the failing stage. Sessions loaded before it: " + sessions.length);
        break;
      }
    }
    log("sessions alive: " + sessions.length + " / " + models.length);

    if (sessions.length === models.length) {
      log("");
      log("ALL " + models.length + " WAKE MODELS LOADED -- wake should work on this device.");
    }

    log("");
    log("=== done ===");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("diagRun");
    if (b) b.onclick = () => { b.disabled = true; run().catch((e) => log("diag crashed: " + err(e))).finally(() => { b.disabled = false; }); };
    const c = document.getElementById("diagCopy");
    if (c) c.onclick = async () => {
      try { await navigator.clipboard.writeText(out().textContent); c.textContent = "Copied"; setTimeout(() => (c.textContent = "Copy"), 1500); }
      catch { c.textContent = "select manually"; }
    };
  });
})();
