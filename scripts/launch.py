"""Start the Butler daemon: the ONE launcher (docs/desktop-app.md DA-9, AC-542).

`start.bat` and the desktop app both run this, so the two cannot drift. It
does what `start.bat` did, in order:

1. THE DATA ROOT (docs/data-root.md): `BUTLER_DATA`, else (Windows only)
   the value saved for the user in the registry (a session opened before
   `setx` does not have it; 2026-09-29 the laptop's double-click fell back to
   E:, which that machine does not have), else `E:\\Butler-Data` on Windows
   and `~/Butler-Data` on macOS. Paths are ASKED of `daemon/paths.py`, never
   spelled here.
2. Refuse a data root that is missing or not migrated (no records folder),
   with the exact migrate commands: starting anyway looks exactly like losing
   every conversation.
3. Clear the ports: anything listening on 8140/8141/8142 is a previous Butler
   (half dead, or its console closed without its children); take them back.
4. Generate the certificate on first run (`scripts/make-cert.py`).
5. APPEND a banner to `butler.log`, never overwrite (2026-09-26: a restart
   wiped the only record of the run before it).
6. Start `daemon/butlerd.py --tls` with the LOCAL and PROXIED listeners
   (DA-5), unbuffered (`-u`), its output appended to the same log.

Foreground (the default, `start.bat`): the daemon gets its own MINIMISED
console, as `start "Butler daemon" /min` gave it; closing that window stops
Butler, as before. `--detached` (the desktop app, DA-8): no console at all,
DETACHED_PROCESS ALONE -- combined with CREATE_NEW_CONSOLE it is
`WinError 87` -- and the app owns the process from there.

macOS (docs/macos.md): ports are found with `lsof` and cleared with
`childproc.kill_tree`, and the daemon always gets a session of its own, so
neither a closed shell nor launchd reaping this launcher takes it down.

Exit 1 when it refuses or the daemon dies at once; 0 otherwise.
"""
from __future__ import annotations

import argparse
import datetime
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from daemon import childproc, paths  # noqa: E402
from daemon import settings as settings_mod  # noqa: E402

WINDOWS = os.name == "nt"
DEFAULT_DATA = r"E:\Butler-Data"
POSIX_DEFAULT_DATA = Path.home() / "Butler-Data"
# MAIN (TLS, every device, today's), LOCAL (the desktop app), PROXIED
# (`tailscale serve`) -- docs/desktop-app.md DA-5.
PORT, LOCAL_PORT, PROXY_PORT = 8140, 8141, 8142
SW_SHOWMINNOACTIVE = 7          # what `start /min` shows a new console as


def _registry_data() -> str | None:
    """BUTLER_DATA as saved for this user (HKCU\\Environment), or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "BUTLER_DATA")
    except FileNotFoundError:
        return None
    return str(value) or None


def data_root(env) -> str:
    if not WINDOWS:
        return env.get("BUTLER_DATA") or str(POSIX_DEFAULT_DATA)
    return env.get("BUTLER_DATA") or _registry_data() or DEFAULT_DATA


def _refuse(data: str) -> int:
    py = (".venv\\Scripts\\python.exe -X utf8 scripts\\migrate-data-root.py"
          if WINDOWS else
          ".venv/bin/python -X utf8 scripts/migrate-data-root.py")
    print()
    print(f'  Butler\'s data root "{data}" is missing or not migrated yet.')
    print(f'  Preview:  {py} --data "{data}" --dry-run')
    print(f'  Migrate:  {py} --data "{data}"')
    print()
    return 1


def lan_on(settings_path: Path) -> bool:
    """`lan_access`, which decides MAIN's bind exactly as `butlerd.bind_host`
    does (RA-7)."""
    return bool(settings_mod.Settings(settings_path).get("lan_access"))


def bind_label(settings_path: Path) -> str:
    """MAIN's real bind address."""
    if lan_on(settings_path):
        return "0.0.0.0 (lan_access on)"
    return "127.0.0.1 (lan_access off)"


def daemon_argv() -> list[str]:
    return [sys.executable, "-u", "-X", "utf8",
            str(REPO / "daemon" / "butlerd.py"), "--tls",
            "--local-port", str(LOCAL_PORT), "--proxy-port", str(PROXY_PORT)]


def listening_pids(netstat: str, ports) -> set[int]:
    """PIDs LISTENING on any of `ports` in `netstat -ano` output."""
    found = set()
    for line in netstat.splitlines():
        cols = line.split()
        if len(cols) == 5 and cols[0] == "TCP" and cols[3] == "LISTENING":
            if int(cols[1].rsplit(":", 1)[1]) in ports:
                found.add(int(cols[4]))
    return found


def lsof_pids(out: str) -> set[int]:
    """PIDs in `lsof -t` output: one per line."""
    return {int(word) for word in out.split()}


def clean_ports(ports) -> None:
    """Kill whatever listens on `ports` AND its children (the kokoro sidecar,
    the desk agent, any coding-agent child), as stop.bat does."""
    if WINDOWS:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, check=True).stdout
        pids = listening_pids(out, ports)
    else:
        # lsof exits 1 when nothing listens, so its code is not checked.
        pids = set()
        for port in ports:
            pids |= lsof_pids(subprocess.run(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True).stdout)
    for pid in pids:
        childproc.kill_tree(pid)
    if pids:
        print(f"Stopped the Butler that was already on port {ports[0]}.")
        # The socket needs a moment to leave TIME_WAIT, or the new daemon
        # binds nothing and dies.
        time.sleep(2)


def _spawn_options(detached: bool) -> dict:
    if not WINDOWS:
        return {"start_new_session": True}
    if detached:
        return {"creationflags": subprocess.DETACHED_PROCESS}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_SHOWMINNOACTIVE
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE,
            "startupinfo": info}


def mdns_name(host: str) -> str:
    """The `.local` name; macOS's hostname already carries the suffix."""
    return host if host.endswith(".local") else f"{host}.local"


def _announce(proc, log: Path, lan: bool) -> int:
    """start.bat's closing lines, after checking the daemon did not die."""
    time.sleep(1.5)
    if proc.poll() is not None:
        print(f"Butler exited at once (code {proc.returncode}); see {log}")
        return 1
    print()
    print("  Butler is reachable at:")
    print(f"    https://localhost:{PORT}      (this machine)")
    if lan:
        host = socket.gethostname()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        print(f"    https://{ip}:{PORT}      (LAN)")
        print(f"    https://{mdns_name(host)}:{PORT}")
    print(f"    http://127.0.0.1:{LOCAL_PORT}      (this machine, no "
          f"certificate)")
    print()
    print("  Accept the certificate warning once on each device; the "
          "microphone")
    stop = ("stop.bat" if WINDOWS else
            f"kill $(lsof -t -iTCP:{PORT} -sTCP:LISTEN)")
    print(f"  then works over TLS. Stop with {stop}.")
    return 0


def main(argv=None, env=None) -> int:
    ap = argparse.ArgumentParser(prog="launch")
    ap.add_argument("--detached", action="store_true",
                    help="no console window (the desktop app)")
    args = ap.parse_args(argv)
    env = dict(os.environ if env is None else env)
    data = data_root(env)
    env["BUTLER_DATA"] = data
    try:
        where = paths.resolve(env=env)
    except paths.DataRootError:
        return _refuse(data)
    if not where.records.is_dir():
        return _refuse(data)
    where.logs.mkdir(parents=True, exist_ok=True)

    clean_ports((PORT, LOCAL_PORT, PROXY_PORT))

    if not (where.ssl / "cert.pem").exists():
        print("No certificate yet; generating one for this machine's "
              "addresses...")
        subprocess.run([sys.executable, "-X", "utf8",
                        str(REPO / "scripts" / "make-cert.py")],
                       cwd=str(REPO), env=env, check=True)

    log = where.logs / "butler.log"
    settings_path = where.records / "settings.json"
    bind = bind_label(settings_path)
    print(f"Starting Butler over TLS on {bind}, port {PORT} (log: {log}) ...")
    stamp = datetime.datetime.now().strftime("%a %m/%d/%Y %H:%M:%S")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"===== Butler started {stamp} =====\n")
    with open(log, "ab") as out:
        proc = subprocess.Popen(daemon_argv(), cwd=str(REPO), env=env,
                                stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT,
                                **_spawn_options(args.detached))
    if args.detached:
        print(f"Butler daemon pid {proc.pid}")
        return 0
    return _announce(proc, log, lan_on(settings_path))


if __name__ == "__main__":
    sys.exit(main())
