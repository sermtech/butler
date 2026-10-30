# Butler as a desktop app (Tauri) and a phone app (PROPOSAL)

Written 2026-10-07 against HEAD `e475f76`. Nothing here is accepted:
AC-540..AC-559 are RESERVED for this spec. Spec-local decision ids are
DA-n. Provenance tags as `docs/proactive.md`.

## 0. The owner's decisions [OWNER]

- 2026-10-06: "spec electron or other better option so app can run on the
  web, mobile, but ALSO as a local desktop so the user can start/close it
  more easily. the multiple steps of finding a tab, allowing https source
  is too much for a few users."
- 2026-10-07, after three options were compared (a tray plus an Edge app
  window, Tauri, Electron): **"i want tauri please."**
- This REVERSES the standing ruling "No desktop UI" (root `AGENTS.md`) for
  one thing only: a native window that shows the SAME page, plus a tray
  menu and three local status pages (section 4.3). Options still live only
  on the settings page (AC-41); the root `AGENTS.md` ruling is reworded in
  the commit that builds D1.

## 1. What a user goes through today [READ]

- `start.bat` resolves the data root, generates a SELF-SIGNED certificate,
  starts `daemon\butlerd.py --tls` in a minimised console on
  `0.0.0.0:8140`, and prints three addresses with "Accept the certificate
  warning once on each device". `stop.bat` kills whatever listens on 8140.
- So a user must: run a batch file, find or open a browser tab, type an
  address, click through a certificate warning on EVERY device, and allow
  the microphone. Closing the console window kills Butler.
- The daemon binds ONE listener (`web.run_app`, `butlerd.py:3508`).
- Two things trust the PEER ADDRESS: the desk is "the session whose peer is
  loopback" (`hub.py:411`, `LOOPBACK` at `hub.py:63`), and the agent routes
  `/api/memory/*` and `/api/tasks/agent` refuse anything not loopback
  (`butlerd.py:3279`, `3322`), as will WhatsApp's send door
  (`docs/messaging.md` MS-10).

## 2. The shape

```
 DESKTOP (this PC)                          PHONE / TABLET (tailnet)
 Butler.exe (Tauri 2.12+)                   https://<pc>.<tailnet>.ts.net
   window -> http://127.0.0.1:8141            (real Let's Encrypt cert,
   tray: Open / status / Start with           Add to Home Screen)
         Windows / Phone link / Restart /           |
         Quit                                 tailscale serve --bg
   starts/stops butlerd                       -> http://127.0.0.1:8142
             \                                     /
              +------------- butlerd --------------+
                 8141 LOCAL   (127.0.0.1, HTTP)  trusted as this machine
                 8142 PROXIED (127.0.0.1, HTTP)  NEVER trusted as local
                 8140 TLS     (0.0.0.0, today)   unchanged
```

- **DA-1 Desktop: no certificate at all.** Browsers treat
  `http://127.0.0.1` as a secure context, so the microphone works over
  plain HTTP on loopback. The app window loads the existing page from a new
  loopback-only listener. Nothing to accept, nothing to type.
- **DA-2 Phones: a real certificate.** `tailscale serve` gives the PC a
  trusted HTTPS name, `https://<pc>.<tailnet>.ts.net`, with a Let's Encrypt
  certificate that Tailscale renews
  ([Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates)).
  No warning on any device, and a valid certificate is what lets the phone
  add the page to its home screen as an app (a self-signed certificate
  usually prevents that). The page already has a web-app manifest
  (`web/index.html:42`).
- **DA-3 No native phone app.** The phone still has to reach the PC over the
  tailnet, and the home-screen web app over a real certificate gives the
  same result. Tauri can build for Android and iOS, but iOS needs a Mac and
  an Apple developer account (from memory); revisit only if the home-screen
  app falls short (for example, listening with the screen locked).
- **DA-4 8140 stays** exactly as today for existing setups and for devices
  without Tailscale. Nothing is removed in this spec.

## 3. Trust comes from the listener, not the peer address (the trap)

`tailscale serve` forwards phone traffic from 127.0.0.1. Under today's
peer-address checks, EVERY phone would become "the desk" (it would get the
desk's speech and hotkeys) and could call the loopback-only agent routes,
including the WhatsApp send door. So:

- **DA-5 Three listeners, one app.** `butlerd` moves from `web.run_app` to
  `AppRunner` with three sites: 8140 TLS on the configured host (today),
  8141 plain HTTP bound to 127.0.0.1 (LOCAL), 8142 plain HTTP bound to
  127.0.0.1 (PROXIED). The ports are command-line arguments like today's
  `--port` (`--local-port 8141`, `--proxy-port 8142`; `0` turns that
  listener off), passed by `scripts\launch.py`.
- **DA-6 One trust function.** `is_local(request)` answers true ONLY for a
  request that arrived on the LOCAL listener, or on 8140 from a loopback
  peer (today's behaviour). A request on the PROXIED listener is never
  local, whatever its peer. Every `req.remote in LOOPBACK` and
  `peer in LOOPBACK` check is replaced by it, including `is_desk`
  (`hub.py:411`); a source test finds no other use of `LOOPBACK`.
- Each request's listener is read from the socket it arrived on (aiohttp's
  `request.transport.get_extra_info("sockname")`), never from a header: a
  header can be forged by whoever sends the request.
- Since 2026-10-29 "is_local" answers only "is this caller ON this machine";
  whether a caller may use Butler AT ALL is the gate in
  `docs/remote-access.md` (a phone on PROXIED needs the owner's Tailscale
  login and a `.ts.net` Host, or a paired device). The section 9 line "the
  same exposure as 8140 today (R2)" predates it.

## 4. The desktop app

### 4.1 What the user does

1. Runs `Butler-Setup.exe` once. Per-user install, no administrator prompt
   (NSIS `installMode: currentUser`). The installer is NOT code-signed
   [OWNER 2026-10-07], so Windows shows SmartScreen once ("Windows protected
   your PC": "More info", then "Run anyway"); the install instructions say
   so with a screenshot.
2. First launch only: a folder picker asks "Where is Butler?" if the app
   cannot find a checkout (it tries the folder it was built from, then
   `%USERPROFILE%\Butler`, `C:\PROJECTS\butler`, `E:\PROJECTS\butler`). It
   checks for `daemon\butlerd.py` and remembers the answer in
   `%APPDATA%\Butler\app.json`.
3. From then on: Butler in the Start menu, a tray icon, and one window.
   Microphone access is granted by the app itself; Windows' privacy switch
   "Let desktop apps access your microphone" must be on, and the app says
   so if the microphone fails for that reason.

### 4.2 How it behaves

- **DA-7 One instance** (`tauri-plugin-single-instance` 2.4.x). Launching it
  again focuses the open window.
- **DA-8 Starting Butler.** On launch the app asks `GET
  http://127.0.0.1:8141/api/health`. Healthy: it opens the window on it.
  Not answering: it starts the daemon through `scripts\launch.py` (DA-9),
  hidden (no console window), assigned to a Windows job object so the
  daemon cannot outlive a crashed app in a half state, then waits up to
  60 s for health, showing "Starting Butler..." meanwhile.
- **DA-9 One launcher, not two.** `start.bat`'s logic (data-root lookup
  including the registry read, the not-migrated refusal, certificate
  generation, appending to `butler.log`, clearing a stale port) moves into
  `scripts\launch.py`. `start.bat` becomes a two-line call to it, so the
  batch file and the app cannot drift apart.
- **DA-10 Closing the window does not stop Butler.** It hides to the tray;
  Butler keeps listening. Only "Quit Butler" stops it.
- **DA-11 Quit.** The app calls a new `POST /api/shutdown`, accepted only
  when `is_local` (DA-6), which stops the daemon cleanly (sidecars and
  children through their existing teardown). If the daemon has not exited
  within 15 s, the app kills the process tree it started and says so in a
  notification. A daemon started outside the app (by `start.bat`) is
  stopped the same way: there is one Butler.
- **DA-12 Start with Windows** (`tauri-plugin-autostart` 2.5.x, launched
  with `--autostart`): at login the app starts Butler and stays in the tray
  with no window.
- **DA-13 Microphone, and only the microphone.** A Rust permission handler
  (Tauri 2.12's permission hook, or WebView2's `PermissionRequested`
  directly) ALLOWS the microphone for exactly the origin
  `http://127.0.0.1:8141` and DENIES every other permission and origin.
  A blanket grant is the defect other Tauri apps shipped
  (any script in the window gets the microphone).
- **DA-14 Links leave the app.** A link to anything but 8141 opens in the
  user's default browser, never inside the Butler window.

### 4.3 The app's own pages and tray

The only things the app draws besides the Butler page:

- Tray menu: "Open Butler", a status line (Running, Starting, Stopped),
  "Start with Windows" (checkbox), "Phone link", "Restart Butler", "Quit
  Butler".
- Three local pages bundled in the app, shown in the same window: Starting
  ("Starting Butler..."), Not set up (the data root is missing or not
  migrated, with the exact command `start.bat` prints today), and Stopped
  (Butler exited: the last 20 lines of `butler.log` and a Restart button).
- Phone link (section 5).

## 5. Phone link: from QR code to home-screen app

- **DA-15 The phone address as a QR code.** "Phone link" (tray) and a row in
  the settings page's Devices group show `https://<pc>.<tailnet>.ts.net`
  as a QR code (`qrcode` is already a WhatsApp dependency) with three
  steps: scan, allow the microphone, "Add to Home Screen".
- **DA-16 Setting it up with one button.** When the ts.net name does not
  answer yet, the same place shows "Set up phone access". It needs
  Tailscale installed and logged in (checked with `tailscale status
  --json`), then runs `tailscale serve --bg --https=443
  http://127.0.0.1:8142`. A port forward like this needs no Administrator
  console on Windows
  ([Tailscale Serve examples](https://tailscale.com/docs/reference/examples/serve)).
  If the tailnet has not enabled HTTPS certificates, Tailscale's own error
  is shown verbatim with the one admin-console step (DNS, enable HTTPS
  Certificates). Never `tailscale funnel`: that would put Butler on the
  public internet.
- The page served on the phone is the same page; the phone is a satellite
  exactly as today.

## 6. Build and distribution

- **DA-17 Where it lives.** `app/` at the repo root: `app/src-tauri/`
  (Rust: shell, tray, launcher, permission handler) and `app/ui/` (the
  three local pages). It gets its own `AGENTS.md` and a row in the root
  Child DOX Index in the D1 commit. Tauri 2.12.0 or later (the startup and
  permission fixes landed there); Rust is installed on the desk
  (`~/.cargo/bin/rustc`).
- **DA-18 Installer.** NSIS, `installMode: currentUser`,
  `webviewInstallMode: downloadBootstrapper` (Windows 11 ships WebView2;
  the bootstrapper covers a machine where it was removed). The installer
  is a build artifact, never committed.
- **DA-19 No auto-update in v1.** The app changes rarely; Butler itself
  keeps updating through git as today. The laptop gets a new installer
  copied over the `remoteshell` route when the app changes.

## 7. Criteria (AC-540..AC-559 reserved)

- **AC-540 (listeners).** By default the daemon answers on 8140 (as today),
  8141 and 8142; 8141 and 8142 bind 127.0.0.1 only; a port argument of 0
  removes that listener. **Recoverable.**
- **AC-541 (trust by listener).** A request on 8142 from 127.0.0.1 is not
  local: it is never the desk and every loopback-only route answers 403;
  the same request on 8141 is local; a source test finds no `LOOPBACK`
  check outside `is_local`. *Fails as:* every phone hearing the desk's
  speech and reaching the agent and WhatsApp-send routes. **NOT
  recoverable.**
- **AC-542 (one launcher).** `start.bat` and the app both start the daemon
  through `scripts\launch.py`; its refusals and log appending match what
  `start.bat` does today. **Recoverable.**
- **AC-543 (shutdown).** `POST /api/shutdown` stops the daemon and its
  children when local and answers 403 on 8142 and from any other peer.
  **Recoverable.**
- **AC-544 (start or attach).** Launching the app with Butler running opens
  the window with no second daemon; with Butler stopped it starts one and
  shows Starting until healthy, or Stopped with the log tail after 60 s.
  **Recoverable.**
- **AC-545 (window close vs quit).** Closing the window leaves Butler
  answering; Quit stops it, killing the tree after 15 s if it hangs, and
  says which happened. **Recoverable.**
- **AC-546 (one instance).** A second launch focuses the first window.
  **Recoverable.**
- **AC-547 (start with Windows).** With the checkbox on, logging in starts
  Butler and a tray icon with no window. **Recoverable.**
- **AC-548 (microphone, only microphone).** In the app window the page's
  microphone starts with no prompt and no certificate step; any other
  permission, or the microphone for any other origin, is denied.
  *Fails as:* a page inside the window listening without the user's
  knowledge. **NOT recoverable** for what it heard.
- **AC-549 (links leave).** A link to another origin opens in the default
  browser. **Recoverable.**
- **AC-550 (status pages).** Not set up, Starting and Stopped each appear in
  their condition with the text and actions of section 4.3.
  **Recoverable.**
- **AC-551 (finding Butler).** First launch finds a checkout in the listed
  places or asks once; a folder without `daemon\butlerd.py` is refused
  naming it. **Recoverable.**
- **AC-552 (installer).** A per-user install with no administrator prompt
  puts Butler in the Start menu; uninstalling removes the app and leaves
  the data root and the checkout untouched. [MEASURED at build on the
  laptop] **Recoverable.**
- **AC-553 (phone link).** The tray and the settings page show the ts.net
  address as a QR code with the three steps. **Recoverable.**
- **AC-554 (phone setup).** "Set up phone access" checks Tailscale, runs the
  port-forward `serve` without elevation, and shows Tailscale's error
  verbatim when HTTPS certificates are off; nothing ever calls `funnel`.
  **Recoverable.**
- **AC-555 (phone end to end).** A phone opening the ts.net address sees no
  certificate warning, can add it to the home screen, and talks to Butler
  as a satellite (not as the desk). [MEASURED at build: Sam's phone]
  **Recoverable.**
- AC-556..AC-559 are left unassigned for what the build finds; AC-560..AC-563 belong to `docs/messaging.md`.

## 8. Build plan

Red-green per phase: the daemon side in pytest (the listener and trust
tests need real sockets on 127.0.0.1:0, never 8140); the Rust side with
`cargo test` for the launcher and permission decision logic, run from one
pytest wrapper so the gate sees it by exit code. Scratch daemons only
(own `BUTLER_STATE`, `hotkeys_enabled: false`), never the live desk or
laptop Butler on 8140.

| phase | builds | criteria | verify |
|---|---|---|---|
| D0 | mock of the tray menu, the three local pages and the Phone link QR panel | -- | owner's look approval |
| D1 | daemon: three listeners, `is_local`, `/api/shutdown`, `scripts\launch.py` with `start.bat` calling it | AC-540..543 | scoped pytest; then the FULL suite (this touches `hub.py` session identity) |
| D2 | Tauri shell: start or attach, window, close vs quit, single instance, status pages, finding Butler | AC-544..546, 550, 551 | `cargo test`; launch on the desk against a scratch daemon on non-default ports |
| D3 | microphone handler, link handling, autostart | AC-547..549 | speak to Butler in the app window on the desk with no prompt; log out and in |
| D4 | installer; install on the laptop | AC-552 | Sam installs it; Start menu entry; uninstall test on the desk |
| D5 | phone link and setup | AC-553..555 | Sam's phone: scan, add to home screen, talk |

The desktop work does not depend on `docs/proactive.md` or
`docs/messaging.md`, but D1 must land BEFORE WhatsApp's send door ships
(messaging M5), because that door's loopback-only rule relies on DA-6
once `tailscale serve` exists.

## 9. Security, stated plainly

- The trust change (section 3) is the one that matters: without it, adding
  `tailscale serve` silently turns every phone into the desk.
- The app grants the microphone without a prompt, so the permission handler
  is the only gate: one origin, one permission.
- The phone address is reachable by every device on the user's tailnet,
  the same exposure as 8140 today (R2). Funnel is never used.
- Tailscale's certificates are recorded in public Certificate Transparency
  logs, so the PC's name and tailnet name become public. Rename a machine
  whose name says too much before enabling HTTPS (O-3).
- An unsigned installer trains users to click through a SmartScreen
  warning; accepted by the owner (no certificate).

## 10. Open for the owner

- **O-1. RULED 2026-10-07: no code-signing certificate.** Users see
  SmartScreen once at install.
- **O-2.** Once every device uses the app or the ts.net address, retire the
  self-signed 8140 listener and `make-cert.py`? Not in this spec.
- **O-3.** Accept that machine names appear in public certificate logs, or
  rename machines first.
