"""Opt a process out of Windows 11 EXECUTION_SPEED power throttling.

Why: Windows 11 demotes a background process's threads onto the E-cores after a
few seconds of sustained work (measured 2026-10-09 on the 12700K: kokoro's ONNX
threads on CPU16-19 at 100% while the P-cores idled, synthesis ~4.3x -> 0.8x
realtime, dips to 0.36x; Butler's own sidecar measured rtf 0.31-0.87). Setting
ProcessPowerThrottling with ControlMask=EXECUTION_SPEED and StateMask=0 tells
the scheduler "never throttle this process".

LOUD, NEVER FATAL (D-1): this is a performance setting. `apply()` prints a
warning to stderr (which butler.log captures) on failure and returns False; it
never raises. `set_high_qos()` is the raising primitive, for tests and the CLI.
Off Windows both are no-ops.

Standalone module (no package imports) so it can sit beside the other engines:
the daemon, Smart Turn's pool child and the kokoro sidecar's CLI call all load
this one file.

    python -X utf8 winqos.py <pid>     exit 0 applied, 3 not Windows, 1 failed
"""
from __future__ import annotations

import sys

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_POWER_THROTTLING = 4
EXECUTION_SPEED = 0x1


def _api():
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD]
    k32.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD]
    return ctypes, k32, State


def _open(ctypes, k32, pid):
    if pid is None:
        return k32.GetCurrentProcess(), False
    h = k32.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
                        False, pid)
    if not h:
        raise OSError(ctypes.get_last_error(), f"OpenProcess(pid={pid}) failed")
    return h, True


def set_high_qos(pid: int | None = None) -> bool:
    """Disable EXECUTION_SPEED throttling for `pid` (None = this process).

    True on success, False off Windows. Raises OSError on failure.
    """
    if sys.platform != "win32":
        return False
    ctypes, k32, State = _api()
    h, owned = _open(ctypes, k32, pid)
    try:
        st = State(1, EXECUTION_SPEED, 0)
        if not k32.SetProcessInformation(h, PROCESS_POWER_THROTTLING,
                                         ctypes.byref(st), ctypes.sizeof(st)):
            raise OSError(ctypes.get_last_error(),
                          f"SetProcessInformation(pid={pid}) failed")
        return True
    finally:
        if owned:
            k32.CloseHandle(h)


def query_qos(pid: int | None = None):
    """Read back (ControlMask, StateMask), or None off Windows.

    A process never opted in reads (0, 0).
    """
    if sys.platform != "win32":
        return None
    ctypes, k32, State = _api()
    h, owned = _open(ctypes, k32, pid)
    try:
        st = State(1, 0, 0)
        if not k32.GetProcessInformation(h, PROCESS_POWER_THROTTLING,
                                         ctypes.byref(st), ctypes.sizeof(st)):
            raise OSError(ctypes.get_last_error(),
                          f"GetProcessInformation(pid={pid}) failed")
        return (st.ControlMask, st.StateMask)
    finally:
        if owned:
            k32.CloseHandle(h)


def is_high_qos(pid: int | None = None) -> bool:
    """True when the process has opted out (ControlMask has the bit, StateMask clear)."""
    q = query_qos(pid)
    return q is not None and bool(q[0] & EXECUTION_SPEED) and not (q[1] & EXECUTION_SPEED)


def apply(label: str, pid: int | None = None) -> bool:
    """Loud-never-fatal wrapper: the one call every Butler call site uses."""
    try:
        return set_high_qos(pid)
    except Exception as e:  # noqa: BLE001 - a performance setting must not cost the microphone
        print(f"[butler] WARNING: could not opt {label} (pid {pid or 'self'}) out of "
              f"power throttling: {e}; its speech work may be demoted to the E-cores "
              f"and run several times slower", file=sys.stderr, flush=True)
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        sys.stderr.write("usage: winqos.py <pid>\n")
        return 2
    try:
        if not set_high_qos(int(argv[1])):
            sys.stderr.write("winqos: not Windows, nothing applied\n")
            return 3
    except OSError as e:
        sys.stderr.write(f"winqos: {e} (winerror {e.errno})\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
