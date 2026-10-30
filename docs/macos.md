# Butler on macOS

Status: PORTED 2026-10-26 for Ana's Mac mini (M4, macOS 15.6). Install
results are in section 4.

## 1. What differs from Windows

The daemon, the page, speech recognition (transcribe-cpp, CPU), end of turn
(onnxruntime, CPU) and speech (Kokoro on node, CPU) are the same code. The
microphone and speaker are the BROWSER's, so nothing audio-specific changed.

| Seam | Windows | macOS |
|------|---------|-------|
| Tree kill (`childproc.kill_tree`) | `taskkill /F /T` | `pgrep -P` walk, then SIGKILL |
| pi launcher (`harness_pi.PI`) | `cmd /c pi` | `pi` |
| Terminal action (`butlerd.TERMINAL_ARGV`) | `wt.exe -d <cwd> cmd /k <agent>` | `osascript`: Terminal.app, cwd and agent as quoted arguments |
| Launcher (`scripts/launch.py`) | registry, `netstat`, minimised console | `~/Butler-Data` default, `lsof`, `start_new_session` |
| Start at login | the desktop app / `start.bat` | launchd agent `scripts/macos/com.butler.daemon.plist` |
| In-page Restart (`do_restart`, `os.execv`) | spawn-then-exit: a NEW pid on the same port | in place: the SAME pid re-binds its port over its own TIME_WAIT, which needs `SO_REUSEADDR` (`listen_socket`, POSIX only; fixed 2026-10-27 after the Mac stayed down) |

Not available on macOS, by decision rather than oversight:

- Child-lifetime job (AC-36): `childproc.assign` returns False. A HARD kill of
  the daemon can orphan the kokoro sidecar or a pi child; `launch.py` clears
  the ports (and their trees) on the next start.
- Desk hotkey agent (`desk/`, Win32 hooks): keep `hotkeys_enabled` off.
- Prechecks (`automations._Job` is a Win32 job object).
- The Tauri desktop app (`app/`, WebView2): use Safari or Chrome instead.
- Process QoS (`engines/winqos.py`): a no-op off Windows.
- Tailscale CLI: the app keeps it inside the bundle, off PATH;
  `phonelink.tailscale_path` falls back to `phonelink.MAC_APP_CLI` (phone link
  and the gate's owner lookup, `docs/remote-access.md` RA-6, both need it).
  OPEN: from the launchd-started daemon that CLI fails ("The Tailscale GUI
  failed to start", CLIError 3) while the same binary works over SSH. So the
  Mac sets `tailscale_owners` explicitly and `tailscale serve` was run by hand.
- In-page terminals (`docs/chats-and-terminals.md`): npm 11 SKIPS node-pty's
  install scripts ("install-scripts" approval), so its post-install never marks
  `node_modules/node-pty/prebuilds/darwin-*/spawn-helper` executable and every
  shell fails to start. After each `npm install` on the Mac: `chmod +x` that
  file (done 2026-10-29). Windows is unaffected.
- Supervision: launchd runs `launch.py` ONCE at login and that exits, so a
  daemon that dies later stays down until `launchctl kickstart -k
  gui/$(id -u)/com.butler.daemon` (open in `docs/backlog.md`).
- Four launcher tests in `test/test_listeners.py` are Windows-shaped
  (backslash paths, the registry, `C:\Saved`) and fail on macOS.

## 2. Install (remoteshell, `--via lan`)

The repo has no hosted remote. Code reaches the Mac the way it reaches the
laptop: a push from the desk into a checkout with
`receive.denyCurrentBranch updateInstead`.

1. On the Mac: `git init ~/PROJECTS/butler`, `git config
   receive.denyCurrentBranch updateInstead`.
2. On the desk: `git remote add mac anauser@10.0.0.77:PROJECTS/butler`,
   push with `GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519"`.
3. Copy `models/` (1.4 GB, not in git) with `scp` using the same key.
4. On the Mac, in the repo: `uv venv --python 3.13 .venv`,
   `uv pip install --python .venv/bin/python -r requirements.txt`,
   `npm install`, `brew install ffmpeg`.
5. Data root: `.venv/bin/python -X utf8 scripts/migrate-data-root.py --data
   ~/Butler-Data`. On a FRESH install there is nothing to migrate, so it
   writes `AGENTS.md` and `attachments/` but no records folder, and the
   launcher refuses. Create `~/Butler-Data/.butler/logs` and a
   `.butler/settings.json` holding `{"hotkeys_enabled": false}` by hand.
   Also copy the git-ignored assets: `config/models.json` (the folder does not
   exist in a clone), `web/ort18/*`, `web/fonts/*.woff2`.
6. launchd: replace `@REPO@`/`@HOME@` in the plist, copy it to
   `~/Library/LaunchAgents/`, `launchctl bootstrap gui/$(id -u) <plist>`.
7. Open `http://127.0.0.1:8141` in a browser ON the Mac (localhost is a secure
   context, so the microphone works with no certificate).

Persona: `SOUL.md` and `USER.md` are created from template at first boot. They
describe Ana, never the owner: never copy the desk's.

## 3. Verification

On the Mac, in the repo: `.venv/bin/python -X utf8 -m pytest
test/test_macos_port.py -q` runs the platform seams for real (`pgrep`, SIGKILL).
The rest of the suite is Windows-calibrated and is not claimed on macOS.

## 4. Install results (Ana's Mac mini, 2026-10-26) [MEASURED]

- Repo `~/PROJECTS/butler`, data root `~/Butler-Data`, launchd label
  `com.butler.daemon`, desk remote `mac`. uv venv on CPython 3.13.12; npm
  skipped four install scripts (npm 11 asks for approval) and none is needed:
  onnxruntime-node ships its darwin-arm64 binary in the package.
- `test/test_macos_port.py` on the Mac: 13 passed. `test_tts_kokoro.py` plus
  the TTS and ASR conformance suites: 68 passed, then the process ABORTED at
  exit (code 134): `GGML_ASSERT([rsets->data count] == 0)` in transcribe-cpp's
  Metal teardown. Every test had already finished; it is the native library's
  exit path, not Butler's. Expect it on a clean daemon exit too.
- First boot through launchd: launcher exit 0, one daemon on 8140 (TLS, the
  LibreSSL 3.3.6 certificate), 8141 and 8142. Parakeet ran on the Metal
  backend even with `asr_engine.backend` `cpu`; Kokoro reported 31 voices;
  Smart Turn warm in 917 ms. HTTP 200 on 8141 and 8140 on the Mac, and on
  8140 from the desk over both the LAN and Tailscale (`100.64.0.3`).
- Connections were imported from Ana's own `~/.pi/agent/models.json` (six
  providers). Brain (owner, 2026-10-26): `router_model` and `endpoint_model`
  are `zen_bridge` / `acct/opencode-go/deepseek-v4.1-flash`; `zen_bridge`
  is the desk's hub (`100.64.0.1:8765`), so her Butler needs the desk up.
  A typed turn over `/ws` answered in 9.3 s.
