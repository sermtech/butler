"""The desk agent: a process the daemon owns, and that dies with it.

`docs/desk-hotkeys.md` D1 and AC-121, AC-122. The agent holds the keyboard
hook, the microphone, the clipboard and `SendInput`; it holds no model and
reads its settings over HTTP. This file is step 2 -- the process and its
lifetime -- and asserts nothing about hooks or audio, which do not exist yet.

Two claims.

**AC-121 -- OFF is the default, and off means not spawned.** A global hotkey
suppresses a key system-wide. A fresh clone must not do that to anyone's
machine, and the daemon must not start the process that would.

**AC-122 -- it dies with the daemon.** This is the worst orphan in the
repository: a leaked kokoro sidecar costs 46-100 MB, and a leaked desk agent
EATS KEYSTROKES on a machine whose owner has no idea it is running. The
mechanism is the same Job Object the sidecar taught (AC-36, D-24), so the
assertion is the same one: by PID against the process tree, after a hard kill.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_child_lifetime import (MARKER, _start_host, _survivors,  # noqa: E402
                                 _taskkill, _tree)
from daemon import desk as desk_mod                                # noqa: E402
from daemon.settings import BY_KEY, Settings                       # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
KILL_WINDOW_S = 15.0
POLL_S = 0.25

pytestmark = pytest.mark.skipif(os.name != "nt",
                                reason="Win32 job objects; this daemon is "
                                       "Windows-only (plan.md 4.1)")

# A daemon that is not there. The agent must keep running and keep retrying:
# it is spawned at boot and the daemon it polls can restart underneath it, so
# an agent that exited on the first refused connection would be gone for good
# after the first `POST /api/restart` (D-1).
NO_DAEMON = "http://127.0.0.1:9"

DESK_HOST = f'''
import sys, time
sys.path.insert(0, r"{REPO_ROOT}")
from daemon import desk

assert desk.start({NO_DAEMON!r}), "the desk agent did not start"
print("{MARKER}", desk.pid(), flush=True)
time.sleep(600)
'''

# A string nothing else on the machine can be carrying, put in the terminal's
# own command line with `title` so the host can find the process Windows
# Terminal started. Matching a UNIQUE marker is not matching an image name --
# `cmd.exe` names every console the operator has open, and killing by that is
# how a subagent took out the owner's live daemon on 2026-08-19.
TERMINAL_MARK = "BUTLER_AC119_TERMINAL"


def _terminal_host(control: Path) -> str:
    """The AC-119 host: one child inside the job, one deliberately outside.

    It calls the REAL `butlerd.spawn_terminal` -- the same `wt.exe` hand-off
    and the same absence of `childproc.assign` -- and swaps only WHAT is run.
    Starting a real pi agent would leave a coding agent sitting in this
    repository at the end of a test; what is under test is the LIFETIME, and
    the lifetime does not depend on the argv.

    The CONTROL child is the half that makes this an instrument. It is spawned
    the ordinary way and assigned, so the job object really exists in this
    process and really kills what it holds. Without it a `childproc` that had
    silently stopped working would make this test pass while proving nothing:
    every child would survive, including the one that is supposed to.
    """
    return f'''
import subprocess, sys, time
sys.path.insert(0, r"{REPO_ROOT}")
from daemon import butlerd, childproc

# NOT `cmd /k pi`: the lifetime does not depend on the argv, and starting a
# real coding agent would leave one sitting in this repository at the end of a
# test. `title` puts a UNIQUE marker in the command line, which is how the
# terminal is found -- `spawn_terminal` returns nothing, because `wt.exe` is a
# launcher whose own pid is not the terminal's.
butlerd.TERMINAL_ARGV = ("wt.exe", "-d", "{{cwd}}", "cmd", "/k",
                         "title {TERMINAL_MARK} & ping -n 600 127.0.0.1")
inside = subprocess.Popen(["cmd", "/c", "ping -n 600 127.0.0.1 > nul"])
childproc.assign(inside)
open(r"{control}", "w").write(str(inside.pid))
butlerd.spawn_terminal(r"{REPO_ROOT}")

# Poll for the terminal Windows Terminal started for us, by the marker.
found = 0
for _ in range(60):
    time.sleep(0.5)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \\"Name='cmd.exe'\\" | "
         "ForEach-Object {{ '{{0}}|{{1}}' -f $_.ProcessId, $_.CommandLine }}"],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        bits = line.split("|", 1)
        if len(bits) == 2 and "{TERMINAL_MARK}" in bits[1] and bits[0].isdigit():
            found = int(bits[0])
            break
    if found:
        break
print("{MARKER}", found, flush=True)
time.sleep(600)
'''


# --------------------------------------------------------------------------- #
# AC-121 -- off is the default
# --------------------------------------------------------------------------- #
def test_hotkeys_ship_off():
    """A fresh clone suppresses no key on anybody's machine (AC-121)."""
    spec = BY_KEY.get("hotkeys_enabled")
    assert spec is not None, (
        "without this setting the daemon has no way to be told not to install "
        "a global keyboard hook (AC-121)")
    assert spec.kind == "bool"
    assert spec.default is False, (
        f"a global hotkey suppresses a key system-wide; shipping it on means "
        f"a fresh clone changes what the owner's keyboard does before they "
        f"have asked for anything: {spec.default!r}")


def test_the_daemon_does_not_spawn_the_agent_while_hotkeys_are_off(tmp_path):
    """Off means NOT SPAWNED, not spawned-and-idle.

    An agent running with its hook uninstalled is one code path away from
    installing it, and it is invisible in Task Manager to anyone who did not
    put it there. The assertion is therefore on whether a process exists.
    """
    settings = Settings(tmp_path / "settings.json")
    assert settings.get("hotkeys_enabled") is False

    started = desk_mod.start_if_enabled(settings, NO_DAEMON)

    assert started is False, "the agent was started with hotkeys off (AC-121)"
    assert desk_mod.pid() is None, (
        f"a desk agent process exists with hotkeys off: pid {desk_mod.pid()}")


def test_turning_hotkeys_on_is_what_starts_it(tmp_path):
    """The other half: the setting is what decides, and it really spawns."""
    settings = Settings(tmp_path / "settings.json")
    settings.set("hotkeys_enabled", True)
    try:
        assert desk_mod.start_if_enabled(settings, NO_DAEMON) is True
        pid = desk_mod.pid()
        assert pid is not None and desk_mod.running(), (
            "start_if_enabled reported success and no process is running")
    finally:
        desk_mod.stop()
    assert not desk_mod.running(), "stop() left the agent running"
    assert desk_mod.pid() is None


def test_starting_twice_does_not_leave_two(tmp_path):
    """Boot and a re-arm must not accumulate agents.

    Two live hooks on one key is not twice as good: both fire, and the second
    one's capture races the first for the microphone.
    """
    settings = Settings(tmp_path / "settings.json")
    settings.set("hotkeys_enabled", True)
    try:
        assert desk_mod.start_if_enabled(settings, NO_DAEMON) is True
        first = desk_mod.pid()
        assert desk_mod.start_if_enabled(settings, NO_DAEMON) is True
        assert desk_mod.pid() == first, (
            f"a second start replaced the running agent ({first} -> "
            f"{desk_mod.pid()}) and left the first one holding the hook")
    finally:
        desk_mod.stop()


# --------------------------------------------------------------------------- #
# AC-122 -- it dies with the daemon
# --------------------------------------------------------------------------- #
def test_a_hard_killed_host_leaves_no_desk_agent(tmp_path):
    """AC-122, by PID against the process tree (AC-36, D-24).

    `proc.kill()` is TerminateProcess: no `atexit`, no `finally`, no chance
    for any in-process cleanup. It is how `conftest` stops every daemon it
    boots, and it is the case the Job Object exists for. The absence of an
    error proves nothing here -- an orphan produces no error at all, which is
    how seven kokoro sidecars accumulated unnoticed.

    The tree is TWO deep even though `deskd.py` spawns nothing, and the depth
    is not Butler's doing: `.venv/Scripts/python.exe` is a 238 KB uv
    TRAMPOLINE that re-spawns the real interpreter
    (`sys.base_prefix` is under `AppData/Roaming/uv/python/...`), so anything
    launched with `sys.executable` from this venv is a launcher with a python
    underneath it. MEASURED 2026-09-09. Two consequences worth carrying: the
    grandchild is the process actually holding the hook, so `desk.pid()` names
    the launcher and not the agent; and this test is a real grandchild test
    rather than the easy half, which is what the kokoro and pi cases go out of
    their way to guarantee.

    The count is deliberately NOT pinned. Pinning it asserts a property of the
    interpreter rather than of Butler, and it would go red on a system python.
    What is asserted is that nothing in the tree survives, whatever its shape.
    """
    host, agent_pid = _start_host(tmp_path, "desk_host.py", DESK_HOST)
    tree = _tree(agent_pid)
    try:
        assert tree, (
            f"no process was found under the PID the agent announced "
            f"({agent_pid}); this test cannot prove anything about a tree it "
            f"never saw")
        host.kill()
        host.wait(timeout=10)
        deadline = time.monotonic() + KILL_WINDOW_S
        while time.monotonic() < deadline:
            if not _survivors(tree):
                return
            time.sleep(POLL_S)
        pytest.fail(
            f"the desk agent outlived a hard-killed daemon (pid {agent_pid}). "
            f"It holds a global keyboard hook, so an orphan here eats the "
            f"owner's keystrokes with nothing on screen to say why (AC-122).")
    finally:
        for proc in _survivors(tree):
            _taskkill(proc.pid)
        if host.poll() is None:
            host.kill()


# --------------------------------------------------------------------------- #
# AC-119 -- the terminal does NOT die with the daemon
# --------------------------------------------------------------------------- #
def test_the_pi_terminal_outlives_a_hard_killed_daemon(tmp_path):
    """AC-119, and it is every other lifetime test in this repository inverted.

    `POST /api/restart` lands between turns. If the terminal the owner is
    typing in were in the Job Object, a restart would close their session
    mid-command and take whatever they had not saved with it -- so this one
    child is deliberately outside the job, and `spawn_terminal` carries the
    comment saying so, because the next reader's correct instinct is to add
    the `childproc.assign` that every other spawn here has.

    Asserted against a CONTROL child in the same host, spawned the ordinary
    way and assigned: the control must DIE and the terminal must LIVE. One
    without the other proves nothing -- a `childproc` that had quietly stopped
    working would leave both alive and pass.

    **What this test no longer witnesses, stated rather than implied.** While
    the spawn was `cmd` in a `CREATE_NEW_CONSOLE`, the terminal was this
    daemon's own child and adding `childproc.assign` killed it -- D-13 on
    2026-09-09 reddened exactly this test. Since the spawn became `wt.exe`,
    Windows Terminal owns the shell and it is not a descendant of anything
    Butler holds, so the SAME mutation now leaves this test GREEN (measured:
    aimed exit 0). The lifetime is structural rather than guarded here. This
    is still a real end-to-end assertion -- it fails if the terminal dies for
    any reason, if `wt.exe` is absent, or if nothing matching the marker ever
    appears -- but the only witness left for the "never assign" RULE is
    `test_terminal_route.py::test_the_terminal_is_deliberately_NOT_put_in_the_job_object`,
    which the same mutation still reddens at exit 1.

    This test's hazard is the opposite of its neighbour's. Every other lifetime
    test here fails by leaking a process; this one fails by leaking one on
    purpose, so the `finally` kills the terminal by the PID it recorded and
    never by an image name -- the rule a subagent bought on 2026-08-19 by
    matching `butlerd.py` in `wmic` output and taking the owner's live daemon
    with it. It also flashes a console window for a second or two, which is
    what `CREATE_NEW_CONSOLE` is.
    """
    control_file = tmp_path / "in_job.txt"
    host, terminal_pid = _start_host(tmp_path, "terminal_host.py",
                                     _terminal_host(control_file))
    terminal = _tree(terminal_pid)
    control = _tree(int(control_file.read_text()))
    try:
        assert terminal and control, (
            f"the host announced a terminal ({terminal_pid}) and a control "
            f"child, and one of the two was not on the process table; this "
            f"test cannot prove anything about a tree it never saw")
        host.kill()
        host.wait(timeout=10)

        deadline = time.monotonic() + KILL_WINDOW_S
        while time.monotonic() < deadline and _survivors(control):
            time.sleep(POLL_S)
        assert not _survivors(control), (
            "the CONTROL child survived the job object, so this run proves "
            "nothing about the terminal: every child outlived the daemon")
        assert _survivors(terminal), (
            f"the pi terminal (pid {terminal_pid}) died with the daemon. A "
            f"restart between turns just closed the window the owner was "
            f"typing in, mid-command (AC-119).")
    finally:
        for proc in _survivors(_tree(terminal_pid) + terminal):
            _taskkill(proc.pid)
        for proc in _survivors(control):
            _taskkill(proc.pid)
        if host.poll() is None:
            host.kill()


def test_restart_cleanup_stops_the_desk_agent(monkeypatch):
    """D-18: the CALLER is what needs asserting.

    `os.execv` replaces the image but keeps the process handles, so the Job
    Object survives into the new image and the agent with it -- and the new
    image then spawns a SECOND one. That is one orphaned keyboard hook per
    restart, and every one of them is still suppressing the key. The stop has
    to be explicit, and it has to happen in `_restart_cleanup`.
    """
    import asyncio

    from daemon import butlerd

    stopped: list[bool] = []
    monkeypatch.setattr(desk_mod, "stop", lambda: stopped.append(True))

    class _NoBrainNoTts:
        brain = object()
        tts = object()

    asyncio.run(butlerd._restart_cleanup(_NoBrainNoTts()))

    assert stopped == [True], (
        "_restart_cleanup did not stop the desk agent, so a restart carries "
        "the old one into the new image and spawns another (AC-122, AC-36)")


def test_the_agent_script_exists_where_the_supervisor_looks():
    """A missing script is a boot-time failure, not a silent no-op.

    The supervisor spawns a path; if that path is wrong the owner turns
    hotkeys on, nothing happens, and nothing says why (AC-7b).
    """
    assert desk_mod.DESKD.exists(), (
        f"the desk agent script is missing: {desk_mod.DESKD}")
    out = subprocess.run([sys.executable, "-X", "utf8", str(desk_mod.DESKD),
                          "--check"], capture_output=True, text=True,
                         cwd=str(REPO_ROOT), timeout=60)
    assert out.returncode == 0, (
        f"the desk agent will not even import: {out.stdout}\n{out.stderr}")
