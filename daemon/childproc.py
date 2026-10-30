"""One kill-on-close Job Object, so no child outlives the daemon (AC-36, D-24).

WHY A JOB OBJECT AND NOT THE GUARD THAT ALREADY EXISTS
------------------------------------------------------
`endpointer_smartturn._exit_when_the_parent_does` solves this from INSIDE the
child: a thread on `multiprocessing.parent_process().join()` that calls
`os._exit(0)` when the parent's handle signals. It works, it stays, and it
does not transplant. It needs `multiprocessing`, which the kokoro sidecar
(node) and pi (not Butler's code) are not, and Butler cannot add a watch
thread to somebody else's program.

The mechanisms Butler DOES have from outside the child are each half a
guard. `atexit` is what the kokoro sidecar has had, and nothing on Windows
runs `atexit` for a `TerminateProcess` -- which is what `subprocess.terminate()`
does and how `test/conftest.py` stops every daemon it boots. Seven orphaned
sidecars, each still holding a `tts-worker.js` child, were found on this
machine on 2026-08-17 [MEASURED]. Closing the child's stdin works for pi,
which exits 0 on EOF, and says nothing about a child that is not reading its
stdin. Neither covers GRANDCHILDREN, and both of Butler's children have one:
the sidecar forks a kokoro worker, and `cmd /c pi` is a wrapper whose real
work is a node process underneath it.

A kill-on-close job object is the one mechanism that covers all of it from
the parent alone. Every process Butler spawns is assigned to a single job,
descendants join it automatically, and the kernel kills the whole set the
moment the last handle to the job closes -- which happens when this process
dies, FOR ANY REASON, `TerminateProcess` included, because handle teardown is
the kernel's job and not the dying program's.

LOUD, NEVER FATAL (D-1)
-----------------------
Every failure here is reported and swallowed. A host where the job cannot be
created or assigned -- already inside a job with conflicting limits, a
permission the daemon does not have -- costs an orphan after a hard kill.
Refusing to boot over it would cost the microphone, and Butler with no
microphone is worse than Butler with a leaked node process. The existing
`atexit` path stays for clean exits; this is what covers the hard kill.

ctypes on purpose: no new dependency for four Win32 calls.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading

# Win32, from winnt.h / jobapi2.h.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_lock = threading.Lock()
_job = None                 # the handle, held for the life of the process
_job_failed = False         # warned once; do not shout per child


def _warn(what: str) -> None:
    print(f"[butler] WARNING: {what}; a hard kill of the daemon will leave "
          f"this child running (AC-36, D-24)", file=sys.stderr, flush=True)


def _create_job():
    """The process-wide job. Returns a handle, or None having warned.

    The handle is deliberately never closed: closing it is what kills the
    children, so it lives exactly as long as this process does. It is also
    NOT inheritable -- a child holding a handle to the job would keep the job
    alive after the daemon died, which is the whole mechanism inverted.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateJobObjectW(None, None)
    if not handle:
        _warn(f"the child-lifetime job could not be created "
              f"(CreateJobObject failed, error {ctypes.get_last_error()})")
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
            handle, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info)):
        # A job without the kill-on-close limit is worse than no job: it
        # would look assigned and kill nothing.
        err = ctypes.get_last_error()
        k32.CloseHandle(handle)
        _warn(f"the child-lifetime job would not take the kill-on-close "
              f"limit (SetInformationJobObject failed, error {err}); the "
              f"daemon may already be inside a job with conflicting limits")
        return None
    return handle


def job_handle():
    """The one job, created on first use. None once creation has failed."""
    global _job, _job_failed
    with _lock:
        if _job is None and not _job_failed:
            _job = _create_job()
            _job_failed = _job is None
        return _job


def assign(proc) -> bool:
    """Put `proc` -- and every process it later spawns -- in the daemon's job.

    Takes anything with a `.pid`: `subprocess.Popen` and asyncio's process
    transport both qualify, and the PID is what both have in common.

    Call it IMMEDIATELY after the spawn. Descendants inherit job membership
    at creation, so a grandchild created before the assignment lands is
    outside the job: for `cmd /c pi` that window is the microseconds between
    `CreateProcess` returning and this call, and it is the reason the cmd
    wrapper is assigned rather than the node process that appears under it a
    moment later.

    Returns whether the child is covered. Never raises: lifetime housekeeping
    must not cost the microphone (D-1).
    """
    if os.name != "nt":
        return False
    handle = job_handle()
    if handle is None:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # OpenProcess rather than the Popen handle, because asyncio's child does
    # not expose one. A child that has already exited fails here loudly
    # instead of silently assigning whatever reused its PID.
    child = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                            False, int(proc.pid))
    if not child:
        _warn(f"child {proc.pid} could not be opened for job assignment "
              f"(error {ctypes.get_last_error()})")
        return False
    try:
        if not k32.AssignProcessToJobObject(handle, child):
            _warn(f"child {proc.pid} could not be assigned to the "
                  f"child-lifetime job (error {ctypes.get_last_error()})")
            return False
        return True
    finally:
        k32.CloseHandle(child)


def kill_tree(pid: int) -> None:
    """Kill `pid` AND every descendant: the ONE tree kill (docs/macos.md).

    The tree, not the process: the kokoro sidecar forks a worker holding the
    model, pi runs under `cmd /c` on Windows, so ending only the PID Butler
    spawned leaves the work running (AC-36). Windows: `taskkill /F /T`, with
    CREATE_NO_WINDOW because a console program on a console-less daemon gets
    a console ALLOCATED and the owner sees a black window flash. Elsewhere:
    the descendants are listed with `pgrep -P` BEFORE anything dies (an
    orphan is re-parented to launchd and can no longer be found from here),
    then all of them get SIGKILL, the parent first so it spawns no more.

    A process already gone is not an error.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        return
    for doomed in [pid, *_descendants(pid, _children)]:
        try:
            os.kill(doomed, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True,
                         text=True, timeout=10).stdout
    return [int(word) for word in out.split()]


def _descendants(pid: int, children_of) -> list[int]:
    found, stack = [], [pid]
    while stack:
        for child in children_of(stack.pop()):
            found.append(child)
            stack.append(child)
    return found
