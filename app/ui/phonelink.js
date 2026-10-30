// The Phone link page (docs/desktop-app.md DA-15, DA-16): renders the
// daemon's `/api/phonelink` pairing document -- the same one the settings
// page's Devices group shows -- and sends its one action. The words, the
// states and the buttons are all the daemon's; this names none of them.
// Rust asks the daemon (`phone_link`), so this page needs no network access.
(function () {
  "use strict";
  var invoke = window.__TAURI__.core.invoke;
  var $ = function (id) { return document.getElementById(id); };

  function paint(doc) {
    var failed = doc.ok === false;
    $("pl-status").textContent = failed ? "" : (doc.headline || "");
    $("pl-status").hidden = failed;
    $("pl-error").textContent = failed ? (doc.error || "") : "";
    $("pl-error").hidden = !failed;
    $("pl-detail").textContent = failed ? "" : (doc.detail || "");
    $("pl-detail").hidden = failed || !doc.detail;
    // Only a PNG data URL, on a white plate whatever the theme: an inverted
    // code does not scan.
    var qr = typeof doc.qr === "string" && doc.qr.indexOf("data:image/png;base64,") === 0 ? doc.qr : "";
    if (qr) $("pl-qr").src = qr; else $("pl-qr").removeAttribute("src");
    $("pl-plate").hidden = !qr;
    var row = $("pl-actions");
    row.textContent = "";
    (doc.actions || []).forEach(function (a) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn primary";
      b.textContent = a.label;
      b.addEventListener("click", function () { read(a.method === "POST"); });
      row.appendChild(b);
    });
  }

  function read(setup) {
    var buttons = $("pl-actions").querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
    if (setup) $("pl-status").textContent = "Setting up phone access...";
    invoke("phone_link", { setup: setup }).then(function (r) {
      var doc;
      try { doc = JSON.parse(r.body); } catch (e) { doc = null; }
      paint(doc && typeof doc === "object" ? doc
        : { ok: false, error: "Butler answered " + r.status + ": " + r.body.slice(0, 300), actions: [] });
    }, function (e) {
      paint({ ok: false, error: String(e), actions: [] });
    });
  }

  invoke("page_info").then(function (info) { $("pl-back").href = info.butler + "/"; });
  read(false);
})();
