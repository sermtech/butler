"""Butler on macOS (docs/macos.md): the platform seams the port added.

Each test names the Windows-only line it replaced and the failure it catches.
The platform choice is a module constant chosen at import (`launch.WINDOWS`,
`harness_pi.PI`, `butlerd.TERMINAL_ARGV`), so the POSIX branch is driven here
by patching that constant, and the whole file also runs for real on the Mac
(docs/macos.md, Verification), where the kill-tree test exercises `pgrep`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from daemon import butlerd, childproc, shortcuts as shortcuts_mod
from daemon.adapters import harness_pi

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import launch  # noqa: E402


# --------------------------------------------------------------------------- #
# childproc.kill_tree: the ONE tree kill (was four `taskkill` copies, two of
# them unguarded, so a disposed pi child crashed on macOS)
# --------------------------------------------------------------------------- #
def test_descendants_are_walked_to_every_depth():
    """A fixture with a grandchild in the MIDDLE branch, so a walk that stops
    at the first level, or follows only the first child, misses it."""
    tree = {1: [2, 3, 4], 3: [5], 5: [6]}
    found = childproc._descendants(1, lambda pid: tree.get(pid, []))
    assert sorted(found) == [2, 3, 4, 5, 6]


_GRANDCHILD = """
import sys, time
path = sys.argv[1]
while True:
    with open(path, "w") as f:
        f.write(str(time.time()))
    time.sleep(0.05)
"""

_CHILD = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
time.sleep(600)
"""


def _beating(path: Path, within: float = 1.0) -> bool:
    """True if the heartbeat file changes within `within` seconds."""
    if not path.exists():
        return False
    before = path.read_text()
    time.sleep(within)
    return path.read_text() != before


def test_kill_tree_ends_the_grandchild_too(tmp_path):
    """The kokoro sidecar forks a worker and pi runs under a launcher, so
    killing only the PID Butler spawned leaves the work running (AC-36).
    Witness: a grandchild's heartbeat file stops changing."""
    beat = tmp_path / "beat.txt"
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, _GRANDCHILD,
                             str(beat)])
    try:
        deadline = time.monotonic() + 15
        while not beat.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _beating(beat), "the grandchild never started beating"
        childproc.kill_tree(proc.pid)
        proc.wait(timeout=10)
        time.sleep(0.3)
        assert not _beating(beat), "the grandchild outlived kill_tree"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kill_tree_of_a_process_already_gone_does_not_raise():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    childproc.kill_tree(proc.pid)


def test_no_module_spells_taskkill_but_childproc():
    """One tree kill, so a fifth copy cannot drift Windows-only again."""
    callers = [REPO / "daemon" / "adapters" / "harness_pi.py",
               REPO / "daemon" / "adapters" / "tts_kokoro.py",
               REPO / "daemon" / "desk.py", REPO / "daemon" / "whatsapp.py"]
    for path in callers:
        code = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.strip().startswith("#")]
        assert not any('"taskkill"' in ln for ln in code), path.name


# --------------------------------------------------------------------------- #
# pi's launcher: `cmd /c pi` only where pi is pi.cmd
# --------------------------------------------------------------------------- #
def test_pi_is_launched_through_cmd_only_on_windows():
    assert harness_pi.pi_command("nt") == ("cmd", "/c", "pi")
    assert harness_pi.pi_command("posix") == ("pi",)
    assert harness_pi.PI == harness_pi.pi_command(os.name)


def test_the_child_argv_starts_with_the_platform_launcher(monkeypatch,
                                                           tmp_path):
    """The CALLER uses `PI` (D-18): on macOS the argv begins `pi --mode`."""
    monkeypatch.setattr(harness_pi, "PI", ("pi",))
    harness = harness_pi.PiHarness()
    spec = harness_pi.HarnessSpec(model=None, cwd=str(tmp_path),
                                  session_file=str(tmp_path / "s.jsonl"))
    argv = harness._argv(spec)
    assert argv[:3] == ["pi", "--mode", "rpc"], argv


# --------------------------------------------------------------------------- #
# The terminal action on macOS: Terminal.app through osascript
# --------------------------------------------------------------------------- #
def test_the_mac_terminal_passes_cwd_and_agent_as_arguments(monkeypatch,
                                                             tmp_path):
    """The directory and the agent words are osascript ARGUMENTS, quoted by
    AppleScript's `quoted form of`, never spliced into the script text: a
    path with a quote in it must not become AppleScript or shell."""
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = list(argv)

    monkeypatch.setattr(butlerd, "TERMINAL_ARGV", butlerd.MAC_TERMINAL_ARGV)
    monkeypatch.setattr(butlerd.subprocess, "Popen", FakePopen)
    butlerd.spawn_terminal(str(tmp_path), "claude")
    argv = seen["argv"]
    assert argv[0] == "osascript"
    script = argv[2]
    assert str(tmp_path) not in script and "claude" not in script
    assert "quoted form of" in script
    assert argv[3:] == [str(tmp_path),
                        *shortcuts_mod.TERMINAL_AGENTS["claude"]]


def test_windows_keeps_windows_terminal():
    assert butlerd.WINDOWS_TERMINAL_ARGV[0] == "wt.exe"
    if os.name == "nt":
        assert butlerd.TERMINAL_ARGV == butlerd.WINDOWS_TERMINAL_ARGV
    else:
        assert butlerd.TERMINAL_ARGV == butlerd.MAC_TERMINAL_ARGV


# --------------------------------------------------------------------------- #
# The launcher (AC-542) off Windows
# --------------------------------------------------------------------------- #
def test_off_windows_the_registry_is_never_read(monkeypatch):
    monkeypatch.setattr(launch, "WINDOWS", False)
    monkeypatch.setattr(launch, "_registry_data",
                        lambda: pytest.fail("winreg read off Windows"))
    assert launch.data_root({"BUTLER_DATA": "/x"}) == "/x"
    assert launch.data_root({}) == str(Path.home() / "Butler-Data")


def test_lsof_output_gives_the_listening_pids():
    assert launch.lsof_pids("123\n456\n\n123\n") == {123, 456}
    assert launch.lsof_pids("") == set()


def test_off_windows_ports_are_found_with_lsof_and_killed_as_trees(
        monkeypatch):
    monkeypatch.setattr(launch, "WINDOWS", False)
    asked, killed = [], []

    def fake_run(argv, **kw):
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="77\n", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    monkeypatch.setattr(launch.childproc, "kill_tree", killed.append)
    monkeypatch.setattr(launch.time, "sleep", lambda s: None)
    launch.clean_ports((8140, 8141))
    assert asked == [["lsof", "-nP", "-t", "-iTCP:8140", "-sTCP:LISTEN"],
                     ["lsof", "-nP", "-t", "-iTCP:8141", "-sTCP:LISTEN"]]
    assert killed == [77]


def test_off_windows_the_daemon_gets_its_own_session(monkeypatch):
    """No console to minimise: a new session so closing the shell (or
    launchd reaping the launcher) does not take the daemon with it."""
    monkeypatch.setattr(launch, "WINDOWS", False)
    for detached in (False, True):
        assert launch._spawn_options(detached) == {"start_new_session": True}


def test_off_windows_the_refusal_names_the_posix_venv(monkeypatch, capsys):
    monkeypatch.setattr(launch, "WINDOWS", False)
    launch._refuse("/d")
    out = capsys.readouterr().out
    assert ".venv/bin/python -X utf8 scripts/migrate-data-root.py" in out


def test_a_mac_hostname_already_ending_in_local_is_not_doubled():
    """macOS's gethostname() is `Example-Mac-mini.local`; the first boot
    printed `https://Example-Mac-mini.local.local:8140`."""
    assert launch.mdns_name("Example-Mac-mini.local") == \
        "Example-Mac-mini.local"
    assert launch.mdns_name("DESKTOP-7JR0N55") == "DESKTOP-7JR0N55.local"
