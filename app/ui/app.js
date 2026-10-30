// The local pages' only script: fill in what the app knows (page_info) and
// send the one action (restart_butler, which is also "Try again").
// window.__TAURI__ exists here because these pages are the app's own;
// Butler's page on 8141 gets no app command (capabilities/default.json).
(function () {
  "use strict";
  var invoke = window.__TAURI__.core.invoke;

  function fill(id, text) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    var wrap = document.getElementById(id + "-wrap");
    if (wrap) wrap.hidden = !text;
  }

  invoke("page_info").then(function (info) {
    fill("repo", info.repo);
    fill("not-set-up", info.not_set_up);
    fill("launcher-output", info.launcher_output);
    fill("log-path", info.log_path);
    fill("log-tail", info.log_tail);
    fill("note", info.note);
  });

  var btn = document.getElementById("restart");
  if (btn) {
    btn.addEventListener("click", function () {
      btn.disabled = true;
      invoke("restart_butler").catch(function (e) {
        btn.disabled = false;
        fill("note", String(e));
      });
    });
  }
})();
