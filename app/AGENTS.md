# Desktop app

## Purpose

- The Tauri 2.12 desktop shell (`docs/desktop-app.md` D2/D3): one window on
  `http://127.0.0.1:8141`, a tray menu, and three local pages (Starting, Not
  set up, Stopped). It starts, attaches to and stops Butler; it draws no
  options of its own (they live on the settings page, AC-41).

## Ownership

- Governs: `app/` (`src-tauri/` Rust, `ui/` the three local pages).
- Parent owns: the daemon, its listeners and trust rule (`daemon/AGENTS.md`),
  and `scripts/launch.py`, the one launcher.

## Local Contracts

- Butler is started ONLY through `scripts\launch.py --detached` (DA-9); its
  refusal text "is missing or not migrated" is a contract the Not set up page
  reads. Never re-implement launcher logic in Rust.
- Decisions live in `src/logic.rs` as pure functions; the I/O modules only
  carry them out. A new decision gets a `cargo test`.
- Microphone (DA-13, AC-548): `src/guard.rs` handles WebView2's
  `PermissionRequested` directly (webview2-com, the version wry uses), because
  Tauri 2.12.1's hook passes no origin. It allows ONLY the microphone, ONLY for
  the top document at the app's own origin; every iframe gets its own handler
  that denies everything (WebView2 reports a delegated iframe as the top
  origin).
- The 8141 page reaches no app command (no remote capability). Every daemon call the app makes
  sends `LOCAL_HEADER` (`X-Butler-Local: 1`, RV-1); an installed app older than that gets 403
  on Quit and Phone link, so rebuild and reinstall it with the daemon.
- Quit (DA-11): `POST /api/shutdown`, then end the app's job object after
  15 s, never a PID (`/api/restart` changes the daemon's PID).
- `BUTLER_APP_PORT` is TEST MODE: the app never runs `launch.py` (which clears
  8140-8142) and Restart only shows a note.

## Work Guidance

- Manual runs: a scratch daemon plus `BUTLER_APP_PORT`; check
  `netstat -ano | findstr ":8141 "` first, and never attach to or stop the
  live Butler.
- The tray's Phone link (enabled only while Running) opens `ui/phonelink.html`; Rust fetches
  `/api/phonelink` on LOCAL (the `phone_link` command). Four local pages in all.

## Verification

```
.venv\Scripts\python.exe -X utf8 -m pytest test\test_app_rust.py -q
```

That runs `cargo test` by exit code. This desk has no MSVC Build Tools (the
owner's choice, 2026-10-07): the Microsoft CRT and SDK live in `~/.xwin`,
named by the user variable `BUTLER_XWIN`, and the wrapper's `xwin_env` builds
with LLVM (`clang-cl`, `lld-link`, `llvm-lib`). A manual `cargo` or
`cargo tauri build` in a shell needs the same variables; copy them from
`xwin_env`. With `BUTLER_XWIN` unset, MSVC Build Tools are required.

The installer (D4): `cargo tauri build --bundles nsis` in `app/src-tauri`
with that env and `CARGO_BUILD_JOBS=4` (a heavier build coincided with the
live Butler dying, 2026-10-07). It writes
`target\release\bundle\nsis\Butler_<version>_x64-setup.exe`, a build
artifact never committed. The bundler fetches NSIS 3.11 from GitHub, which
this desk's per-program firewall blocks for it; NSIS was fetched with curl
into `%LOCALAPPDATA%\tauri\NSIS` and checked against the SHA-1 values pinned
in tauri-bundler (`NSIS_SHA1`, `NSIS_TAURI_UTILS_SHA1`). Per-user install
(`RequestExecutionLevel user`, `$LOCALAPPDATA\Butler`); the uninstaller
removes only the app, shortcuts and, if ticked, `%APPDATA%\com.butler.desktop`
-- never the checkout or the data root. `app/INSTALL.md` is the user note.

## Child DOX Index

No child AGENTS.md yet.
