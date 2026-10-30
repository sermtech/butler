"""No child outlives a hard-killed parent (AC-36, D-24).

docs/harness-mode.md 7.3. Both of Butler's real child processes are asserted
here, by PID against the process tree, because that is AC-36's own wording:
"neither process is alive a second later, asserted by PID". The absence of an
error proves nothing -- an orphan produces no error at all, which is exactly
why seven Kokoro sidecars accumulated unnoticed on 2026-08-17.

The shape of each test, and why it has to be this shape:

  a scratch HOST process spawns the child and prints its PID; the test records
  the whole tree under that PID (the sidecar forks a kokoro worker, `cmd /c`
  spawns node), then kills the host with `proc.kill()` -- TerminateProcess on
  Windows, the same hard kill `conftest`'s daemon fixtures use, and the one
  thing `atexit` never survives. Then it polls the process list until the tree
  is gone or the window expires.

The host must be a separate process for the kill to mean anything: killing
pytest would take the test with it.

A process is identified by its PID AND its creation time, never by the PID
alone. Windows recycles PIDs and never clears a parent-PID field, so an
orphan holding a recycled parent PID joins a tree it was never in -- which
made this file intermittently red against a job object that was working
(`_tree`, and `test_a_recycled_parent_pid_is_not_read_as_a_descendant`).

Both are marked `slow` (a node boot plus a kokoro model load; a pi spawn), and
each skips -- never fails -- when the engine it needs is not installed. The
`finally` tree-kills whatever survived, because a red run that leaves an
orphan per attempt pollutes the machine it is measuring.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import PI_MODEL, PI_PROVIDER  # noqa: E402
from daemon.adapters import tts_kokoro  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# How long the host gets to print its child's PID. A kokoro warm is a node
# boot plus an ~80 MB ONNX load; a pi child answers `get_state` after 1.5-2.3 s
# [MEASURED 2026-08-17]. Generous, but bounded: a test that waits forever
# reports nothing at all (test/AGENTS.md).
SPAWN_TIMEOUT_S = 180.0
# How long the kernel gets to tear the tree down after the host dies. The job
# object does it as the last handle closes, so this is slack for the polling
# loop rather than a measured budget.
KILL_WINDOW_S = 15.0
POLL_S = 0.25

pytestmark = pytest.mark.skipif(os.name != "nt",
                                reason="Win32 job objects; this daemon is "
                                       "Windows-only (plan.md 4.1)")


# --------------------------------------------------------------------------- #
# Reading the process tree
# --------------------------------------------------------------------------- #
# Toolhelp rather than `tasklist`, for one reason that matters and one that
# does not: a snapshot gives parent PIDs, so the GRANDCHILDREN this step
# exists to kill (the kokoro worker, the node under `cmd /c`) can be named --
# and it costs a millisecond instead of a process spawn per poll.
#
# A PID IS NOT AN IDENTITY, and reading it as one made this file intermittently
# red [MEASURED 2026-08-18]. Windows never clears a process's parent-PID field
# and recycles PIDs freely, so a long-dead parent's PID is eventually handed to
# somebody else and every orphan that recorded it becomes that stranger's
# "child". This machine carries 147 detached `git fsmonitor--daemon` processes,
# all of them orphans by design, and 8 of those 147 named a LIVE process as
# their parent at the moment this was measured -- one a cmd.exe, one a node.exe,
# which are exactly the two images in the pi tree. So a process counts here
# only if it also STARTED AFTER the parent it claims, and a tree entry stays
# "the same process" only while its PID and creation time both match.
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# Enough to ask when a process started, and granted for processes this one did
# not spawn -- which is the point: an unreadable creation time means the tree
# walk cannot vouch for the relationship.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_char * 260),
    ]


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


class _Proc(NamedTuple):
    """One process, identified by PID AND when it started.

    `created` is 0 when the creation time could not be read. That is not a
    detail to paper over: it means this test cannot tell whether the process
    is the one it saw a moment ago, and everything Butler spawns runs as this
    user and answers the question.
    """
    pid: int
    ppid: int
    image: str
    created: int


def _created(pid: int) -> int:
    """When `pid` started, as a raw FILETIME, or 0 if it cannot be read."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    handle = ctypes.c_void_p(handle)
    try:
        start, exited = _FILETIME(), _FILETIME()
        kernel, user = _FILETIME(), _FILETIME()
        if not k32.GetProcessTimes(handle, ctypes.byref(start),
                                   ctypes.byref(exited), ctypes.byref(kernel),
                                   ctypes.byref(user)):
            return 0
        return (start.high << 32) | start.low
    finally:
        k32.CloseHandle(handle)


def _snapshot() -> list[_Proc]:
    """Every live process, with the creation time that makes a PID an identity."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        out = []
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            out.append(_Proc(pid=pid,
                             ppid=int(entry.th32ParentProcessID),
                             image=entry.szExeFile.decode("mbcs", "replace"),
                             created=_created(pid)))
            ok = k32.Process32Next(snap, ctypes.byref(entry))
        return out
    finally:
        k32.CloseHandle(snap)


def _survivors(tree: list[_Proc]) -> list[_Proc]:
    """Which of `tree` are still THE SAME process, on one snapshot.

    Matched on PID and creation time together. A PID alone would report a
    survivor the moment Windows handed that number to something else, which
    for a suite that spawns node, cmd and pi is an ordinary event rather than
    an exotic one.
    """
    now = {(p.pid, p.created) for p in _snapshot()}
    return [p for p in tree if (p.pid, p.created) in now]


def _tree(root: int, procs: list[_Proc] | None = None) -> list[_Proc]:
    """`root` and every descendant alive right now, deepest last.

    A candidate is a descendant only if it also STARTED AFTER the parent it
    names. Without that clause an orphan holding a recycled parent PID joins
    the tree, is never killed by the job it was never in, and fails an
    assertion about Butler's own children -- which is how this file went red
    in a full run while passing alone.
    """
    procs = _snapshot() if procs is None else procs
    by_pid = {p.pid: p for p in procs}
    if root not in by_pid:
        return []
    found = [by_pid[root]]
    frontier = [by_pid[root]]
    while frontier:
        parents = {p.pid: p for p in frontier}
        frontier = []
        for proc in procs:
            parent = parents.get(proc.ppid)
            if parent is None or any(f.pid == proc.pid for f in found):
                continue
            if not proc.created or not parent.created:
                continue          # cannot vouch for it, so it is not ours
            if proc.created < parent.created:
                continue          # a recycled PID, not a child
            found.append(proc)
            frontier.append(proc)
    return found


def _describe(tree: list[_Proc]) -> str:
    live = _survivors(tree)
    return ", ".join(f"{p.pid} ({p.image})" for p in live) or "none"


def _taskkill(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True)


# --------------------------------------------------------------------------- #
# The scratch host
# --------------------------------------------------------------------------- #
MARKER = "BUTLER_CHILD_PID"


def _read_pid(proc: subprocess.Popen, timeout_s: float) -> int:
    """The PID the host announced, or fail with everything it said instead.

    Drained on a thread for two reasons. `readline` cannot be given a
    deadline, so a host that dies during boot would hang the suite rather
    than report its traceback (test/AGENTS.md); and the child's own output
    shares this pipe -- the kokoro sidecar echoes its ready line here -- so
    the PID is found by MARKER rather than by being first.
    """
    lines: list[str] = []
    found = threading.Event()

    def _drain() -> None:
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.startswith(MARKER):
                found.set()
        found.set()             # the host exited: stop waiting on it

    threading.Thread(target=_drain, daemon=True).start()
    found.wait(timeout_s)
    for line in lines:
        if line.startswith(MARKER):
            return int(line.split()[1])
    proc.kill()
    raise AssertionError(
        f"the scratch host announced no child PID within {timeout_s}s; it "
        f"said:\n" + "\n".join(lines[-40:]))


def _start_host(tmp_path: Path, name: str, source: str) -> tuple[subprocess.Popen, int]:
    """Run `source` as a separate process and return it with the PID it printed."""
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(script)],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1)
    try:
        return proc, _read_pid(proc, SPAWN_TIMEOUT_S)
    except Exception:
        proc.kill()
        raise


KOKORO_HOST = f'''
import asyncio, sys
sys.path.insert(0, r"{REPO_ROOT}")
from daemon.adapters.tts_kokoro import KokoroTts

async def main():
    tts = KokoroTts()
    # warm() is a real synthesis, so the sidecar has spawned its kokoro worker
    # by the time the PID is printed -- the grandchild is half of what leaks.
    await tts.warm()
    print("{MARKER}", tts._sidecar.proc.pid, flush=True)
    await asyncio.sleep(600)

asyncio.run(main())
'''

PI_HOST = f'''
import asyncio, sys
sys.path.insert(0, r"{REPO_ROOT}")
from daemon.adapters.harness_pi import PiHarness
from daemon.ports import HarnessSpec

async def main():
    harness = PiHarness(provider={PI_PROVIDER!r}, model={PI_MODEL!r})
    await harness.start(HarnessSpec(model=None, cwd=r"{REPO_ROOT}",
                                    session_file=sys.argv[0] + ".session"))
    # proc.pid is cmd.exe: `cmd /c pi` is the wrapper, and node appears
    # underneath it a moment later [MEASURED 2026-08-17, 7.2 review].
    print("{MARKER}", harness._child.proc.pid, flush=True)
    await asyncio.sleep(600)

asyncio.run(main())
'''


def test_a_recycled_parent_pid_is_not_read_as_a_descendant():
    """The instrument, tested (D-13). This is why the file was flaky.

    Windows never clears a process's parent-PID field and hands PIDs out
    again, so an orphan that recorded PID 100 years ago becomes a "child" of
    whatever holds 100 today. This machine carries 147 detached
    `git fsmonitor--daemon` orphans, and a full run failed here naming a
    git.exe that Butler never spawned: the job object had killed the real
    tree correctly, and a stranger was standing in it.

    A snapshot is injected rather than measured, because the race cannot be
    scheduled and a test that waits for it proves nothing.
    """
    procs = [
        _Proc(pid=100, ppid=4, image="cmd.exe", created=5_000),
        _Proc(pid=101, ppid=100, image="node.exe", created=5_001),
        # An orphan from last week whose recorded parent PID is now ours.
        _Proc(pid=102, ppid=100, image="git.exe", created=10),
        _Proc(pid=103, ppid=101, image="git.exe", created=5_002),
        # Real, but its creation time cannot be read, so nothing here can say
        # whose it is.
        _Proc(pid=104, ppid=101, image="git.exe", created=0),
    ]

    found = [p.pid for p in _tree(100, procs)]

    assert found == [100, 101, 103], (
        f"the tree walk took a recycled parent PID at face value: {found}")


def _assert_tree_dies(host: subprocess.Popen, root: int, what: str) -> None:
    """Kill the host with TerminateProcess; the whole tree must go with it."""
    tree = _tree(root)
    # Both trees are two deep, and the SECOND process is the one that gets
    # missed: the kokoro worker holding the model, the node doing the work
    # under `cmd /c`. A run that found only the root would be measuring the
    # easy half, so it fails instead of passing quietly (D-13).
    assert len(tree) >= 2, (
        f"expected a {what} tree of at least two processes under PID {root}, "
        f"found {_describe(tree)} -- this test cannot prove anything about "
        f"grandchildren it never saw")
    try:
        # proc.kill() is TerminateProcess on Windows: no atexit, no finally,
        # no chance for any in-process cleanup to run. It is how conftest
        # stops every daemon it boots, which is why atexit was never the
        # guard it looked like (D-24).
        host.kill()
        host.wait(timeout=10)
        deadline = time.monotonic() + KILL_WINDOW_S
        while time.monotonic() < deadline:
            if not _survivors(tree):
                return
            time.sleep(POLL_S)
        pytest.fail(
            f"the {what} tree outlived a hard-killed parent: "
            f"{_describe(tree)} (spawned under PID {root}). AC-36 says "
            f"nothing Butler spawns may survive the daemon.")
    finally:
        for proc in _survivors(tree):
            _taskkill(proc.pid)


@pytest.mark.slow
def test_a_hard_killed_host_leaves_no_kokoro_sidecar(tmp_path):
    """The seven-orphans finding, as a test (AC-36, D-24).

    `atexit` is the sidecar's only cleanup and nothing runs it under
    TerminateProcess, so every terminated test daemon left a node sidecar and
    the tts-worker beneath it -- 46-100 MB apiece, found seven deep on
    2026-08-17 [MEASURED].
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the kokoro sidecar cannot run")
    if not tts_kokoro.SIDECAR.exists():
        pytest.skip(f"sidecar missing: {tts_kokoro.SIDECAR}")

    host, sidecar_pid = _start_host(tmp_path, "kokoro_host.py", KOKORO_HOST)
    try:
        _assert_tree_dies(host, sidecar_pid, "kokoro sidecar")
    finally:
        if host.poll() is None:
            host.kill()


@pytest.mark.slow
def test_a_hard_killed_host_leaves_no_pi_child(tmp_path):
    """The same rule for the harness child (AC-36).

    The tree here is `cmd.exe` -- what `proc.pid` names, because pi is pi.cmd
    -- with node beneath it, so a mechanism that only covered the process
    Butler spawned would leave the agent itself running.
    """
    if shutil.which("pi") is None:
        pytest.skip("pi is not installed on this machine (docs/harness-mode.md 7.2)")

    host, cmd_pid = _start_host(tmp_path, "pi_host.py", PI_HOST)
    try:
        _assert_tree_dies(host, cmd_pid, "pi child")
    finally:
        if host.poll() is None:
            host.kill()
