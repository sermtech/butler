"""Step 4 end to end: a real chord, a real route, a real terminal that lives.

`docs/desk-hotkeys.md` section 7, AC-118, AC-119, AC-120. Everything between
the injected chord and the console window is the shipped path -- the
`WH_KEYBOARD_LL` hook, the coordinator's stage machine, `POST /api/terminal`
against a real daemon, the metacharacter refusal, and a `wt.exe` hand-off
deliberately left out of the Job Object.

WHY THIS IS MANUAL AND NOT PYTEST
---------------------------------
It presses a real key on the operator's keyboard and it opens a real coding
agent in a real repository. Two things bound that and both are load-bearing:
the hook SUPPRESSES the key it injects, so no other application sees the
keystroke; and every process this probe starts is killed by the PID it
RECORDED, never by matching an image name -- which is how a subagent took out
the owner's live daemon on 2026-08-19.

WHAT IT PROVES, AND WHAT IT CANNOT
----------------------------------
It proves the chord reaches the route, the route spawns, a terminal carrying
this probe's unique marker really appears, and it OUTLIVES a hard-killed
daemon -- which is AC-119 against a real kill rather than against a fake. The
terminal is FOUND rather than reported: `spawn_terminal` returns nothing,
because `wt.exe` hands off to Windows Terminal and its own pid is a launcher's.

**IT PROVED THE OPPOSITE OF ONE VERDICT UNTIL 2026-09-09**, and the reversal is
recorded rather than quietly rewritten. It used to prove the route IGNORED a
directory a caller named, which was the owner's ruling of that morning. The
owner then asked for a pane of project rows with a chord each, which a route
that cannot be told which row was pressed cannot serve, so the body is now
HONOURED (`docs/shortcuts.md` D4) and this probe asserts that instead. Every
other gate is still asserted, and one of them matters more than it did: AC-120's
refusal now guards a path off the WIRE rather than one out of a setting.

It does NOT read the terminal's working directory. A process's current
directory lives in its PEB and cannot be read from outside without walking it,
so what stands in for that is the route's own directory check: name a directory
that does not exist and it answers 400 without spawning. That check had to be
ADDED when the spawn became `wt.exe` -- the old `Popen(cwd=...)` raised and gave
a 502 for free, while `wt -d missing` opens a window and complains inside it.
Weaker than reading the PEB, and the difference is stated rather than hidden.

SCRATCH DAEMON RULES (root AGENTS.md)
-------------------------------------
Its own `BUTLER_STATE`, a free port, never 8140, killed by the recorded PID. It
no longer writes any setting -- the directory rides in the request now -- but it
still OPENS TERMINALS, so it still belongs on a scratch daemon.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_terminal.py
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from desk import actions, deskd, hotkeys, paste as paste_mod  # noqa: E402

BOOT_TIMEOUT_S = 120
SETTLE_S = 0.4
SURVIVE_S = 3.0
# What the terminal runs during the probe. `pi` is what SHIPS; a probe that
# started it would leave a coding agent sitting in this repository at the end
# of a run, waiting for input nobody is going to give it. The lifetime -- the
# thing under test -- does not depend on the argv, and this is said out loud
# in the verdict rather than left for a reader to notice.
PROBE_MARK = "BUTLER_TERMINAL_PROBE"
PROBE_ARGV = ("wt.exe", "-d", "{cwd}", "cmd", "/k",
              f"title {PROBE_MARK} & ping -n 600 127.0.0.1")
# Read from the ONE key table rather than written down again (`desk/actions.py`).
MULTIPLY = actions.KEYS["numpad_multiply"].vk
# The table this probe installs. The shipped SHAPE rather than the shipped
# table: the scratch daemon has no shortcut rows, so the chord is built here and
# handed to the hook exactly the way `Agent._apply_bindings` hands it one.
PROBE_TABLE = actions.Table([
    {"chord": "ctrl+numpad_multiply", "action": "terminal",
     "cwd": str(REPO), "agent": "pi", "label": "butler"},
])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def terminals() -> list[int]:
    """Every cmd whose command line carries this probe's UNIQUE marker.

    `spawn_terminal` returns nothing -- `wt.exe` hands off to Windows
    Terminal, which owns the shell -- so the terminal is found rather than
    reported. Matching a unique marker is not matching an image name:
    `cmd.exe` names every console the operator has open.
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
         "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }"],
        capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        bits = line.split("|", 1)
        if len(bits) == 2 and PROBE_MARK in bits[1] and bits[0].isdigit():
            found.append(int(bits[0]))
    return found


def wait_for_terminal(seconds: float = 20.0) -> list[int]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = terminals()
        if found:
            return found
        time.sleep(0.5)
    return []


def alive(pid: int) -> bool:
    """Is this PID a live process right now?

    `tasklist` filtered on the PID rather than on an image name, for this
    file's whole reason: an image name matches the operator's own windows.
    """
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


def boot_daemon(state: Path, projects: list[str], real_pi: bool) -> tuple:
    """A scratch daemon with its own state, on a free port, with a projects list.

    Every engine is a FAKE: this probe touches no recognizer and no model, so
    booting parakeet would cost a minute to prove nothing.
    """
    port = free_port()
    state.mkdir(parents=True, exist_ok=True)
    # Hotkeys OFF in the scratch daemon's own settings: this probe drives the
    # agent IN PROCESS, and a second agent spawned by the daemon would install
    # a second hook on the same key.
    #
    # `projects` is a RETIRED key since 2026-09-11 (the owner's backlog item 4)
    # and is written here anyway, deliberately: the project list is the
    # shortcuts pane's rows now, and `ShortcutStore.migrate_projects` reads this
    # FILE and turns each entry into one on first boot. So this still configures
    # exactly what it says it does, and it exercises the migration while doing
    # it. `Settings._load` drops the key itself with a loud line, which is the
    # expected noise in this probe's output rather than a fault.
    (state / "settings.json").write_text(
        json.dumps({"hotkeys_enabled": False, "projects": projects,
                    "harness_cwd": projects[0]}),
        encoding="utf-8")
    env = {**os.environ, "BUTLER_ASR": "fake", "BUTLER_TTS": "fake",
           "BUTLER_BRAIN": "fake", "BUTLER_PORT": str(port),
           "BUTLER_STATE": str(state), "BUTLER_TLS": "0",
           "BUTLER_ENDPOINTER": "none", "PYTHONUTF8": "1",
           # At a path that does not exist, which is conftest's own trick and
           # for conftest's own reason: the owner keeps a REAL
           # `config/models.json` naming transcribe.cpp and a CPU backend, and
           # a fake engine handed those keyword arguments dies at boot with a
           # TypeError that reads like a broken daemon.
           "BUTLER_CONFIG": str(state / "no-config-on-purpose.json")}
    argv = [sys.executable, "-X", "utf8"]
    if real_pi:
        argv += [str(REPO / "daemon" / "butlerd.py")]
    else:
        # The argv swap has to happen INSIDE the daemon, which is a separate
        # process, so it is done by importing the module and running its main
        # rather than by any production setting. Nothing in the shipped daemon
        # can be told what to run in a terminal, and that is deliberate: a
        # binding table that can name a command line is a different security
        # object from one that chooses between two written behaviours.
        argv += ["-c", f"import sys; sys.path.insert(0, r'{REPO}');"
                       f"from daemon import butlerd;"
                       f"butlerd.TERMINAL_ARGV = {PROBE_ARGV!r};"
                       f"sys.exit(butlerd.main())"]
    proc = subprocess.Popen(argv, cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + BOOT_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"the scratch daemon exited {proc.returncode}")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc, base
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the scratch daemon never opened {port}")


def ask_for(base: str, path: str, agent: str = "pi") -> tuple:
    """Ask for a terminal in `path`, the way the desk agent now does.

    No setting is written any more: `terminal_cwd` is retired (AC-144) and the
    directory rides in the request (`docs/shortcuts.md` D4). That takes this
    probe off the list of tools that may never be aimed at the owner's daemon
    for WRITING a setting; it stays on the list for opening terminals.
    """
    return Probe(base, {}).open_terminal(path, agent)


class Probe(deskd.Daemon):
    """The real `Daemon`, with settings this probe chooses."""

    def __init__(self, url, values):
        super().__init__(url)
        self._values = values


def chord(up: bool) -> None:
    """Ctrl plus numpad `*`, injected as a FINGER rather than as our own paste.

    `mark=0` matters: every keystroke the agent sends carries `hotkeys.MARK`
    and its own hook passes those through, so a probe using the default would
    press a key the hook is built to ignore.

    Ctrl is pressed FIRST and released LAST. It no longer HAS to be -- the hook
    resolves on the DOWN and replays that binding on the UP
    (`docs/shortcuts.md` 4.4), so a Ctrl released early can no longer relabel
    the release -- and it is kept because it is what a finger actually does.
    """
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    if up:
        paste_mod._send(u32, (MULTIPLY, True),
                        (hotkeys.VK_CONTROL, True), mark=0)
    else:
        paste_mod._send(u32, (hotkeys.VK_CONTROL, False),
                        (MULTIPLY, False), mark=0)


def release_control() -> None:
    """Always, on every exit. An injected Ctrl-down nobody released leaves the
    operator's keyboard stuck in a modifier they never pressed."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    paste_mod._send(u32, (hotkeys.VK_CONTROL, True), mark=0)


def run_chord(base: str) -> list[int]:
    """Drive one real chord. Answers what the route said."""
    daemon = Probe(base, {"hotkeys_enabled": True, "cues": False})
    agent = deskd.Agent(daemon)
    answered: list = []
    real_open = agent.daemon.open_terminal

    def watched(cwd="", agent_name=""):
        status, payload = real_open(cwd, agent_name)
        print(f"  POST /api/terminal cwd={cwd!r} agent={agent_name!r} "
              f"-> {status} {payload}")
        answered.append((status, payload))
        return status, payload

    agent.daemon.open_terminal = watched
    agent._table = PROBE_TABLE
    agent.hook = hotkeys.Hook(PROBE_TABLE)
    if not agent.hook.start():
        raise SystemExit(f"the hook would not install: {agent.hook.error}")
    agent._enabled = True
    try:
        chord(up=False)
        time.sleep(SETTLE_S)
        chord(up=True)
        time.sleep(SETTLE_S)
        agent.drain()              # this does the pick and the whole request
    finally:
        agent.hook.stop()
        release_control()
    return answered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-pi", action="store_true",
                    help="run the shipped `cmd /k pi` instead of the probe's "
                         "own harmless command; leaves a coding agent open "
                         "until this probe kills it")
    args = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows only")

    project = str(REPO)
    state = REPO / "TMP" / f"probe-terminal-{os.getpid()}"
    proc, base = boot_daemon(state, [project], args.real_pi)
    print(f"scratch daemon on {base}, pid {proc.pid}")
    print(f"terminal argv: "
          f"{'cmd /k pi (the shipped one)' if args.real_pi else PROBE_ARGV}")
    opened: list[int] = []
    ok = True
    try:
        print("\n-- the chord --")
        answered = run_chord(base)
        opened = wait_for_terminal()
        chord_ok = (len(answered) == 1 and answered[0][0] == 200
                    and len(opened) == 1)
        print(f"VERDICT the chord opened exactly one live terminal: "
              f"{chord_ok} (pids {opened})")
        ok = ok and chord_ok

        print("\n-- the caller CHOOSES the directory now (the reversal) --")
        elsewhere = str(REPO / "TMP")
        forced = ask_for(base, elsewhere)
        honoured = forced[0] == 200 and forced[1].get("cwd") == elsewhere
        print(f"  asked for {elsewhere} -> {forced}")
        print(f"VERDICT the body's directory is honoured: {honoured}")
        opened = terminals()
        ok = ok and honoured

        print("\n-- AC-141, the agent is a KEY and never a command --")
        shell = Probe(base, {})._post(
            "/api/terminal",
            json.dumps({"cwd": project,
                        "agent": "cmd /c echo PWNED"}).encode(),
            "application/json", 10.0)
        shell_ok = shell[0] == 400
        print(f"  -> {shell}")
        print(f"VERDICT an agent outside the closed table is refused: "
              f"{shell_ok}")
        ok = ok and shell_ok

        print("\n-- AC-120, a path cmd would re-parse --")
        bad = ask_for(base, project + " & echo PWNED")
        bad_ok = bad[0] == 400
        print(f"  -> {bad}")
        print(f"VERDICT refused by name, nothing spawned: {bad_ok}")
        ok = ok and bad_ok

        print("\n-- the negative control: the cwd really reaches CreateProcess --")
        gone = ask_for(base, str(REPO / "TMP" / "no-such-directory-anywhere"))
        gone_ok = gone[0] == 400
        print(f"  -> {gone}")
        print(f"VERDICT a directory that is not there is a 400: {gone_ok}")
        ok = ok and gone_ok

        print("\n-- AC-119, it outlives the daemon --")
        proc.kill()
        proc.wait(timeout=15)
        time.sleep(SURVIVE_S)
        lived = bool(opened) and alive(opened[0])
        print(f"VERDICT the terminal survived a hard-killed daemon: {lived}")
        ok = ok and lived
        return 0 if ok else 1
    finally:
        for pid in opened:
            # By the PID this probe RECORDED. Never by image name: `cmd.exe`
            # matches every console the operator has open.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        if proc.poll() is None:
            proc.kill()
        release_control()
        print("every process this probe started has been killed by its pid")


if __name__ == "__main__":
    sys.exit(main())
