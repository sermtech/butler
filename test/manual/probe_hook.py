"""Can a low-level keyboard hook be installed AND driven from a script?

The instrument step 3 is built on, proved before step 3 exists. It answers the
only question that decides whether the hotkey is self-testable: does an
injected keystroke reach a `WH_KEYBOARD_LL` hook, can the hook suppress it,
and does a marker survive `dwExtraInfo` -- which is how an agent tells its own
injected paste from the owner's real finger.

SAFE BY CONSTRUCTION. The hook returns 1 for the probe key, so the injected
keystroke is swallowed and no application receives it. Nothing else is sent.
That matters: an earlier prototype deliberately never automated a Ctrl+V test because "a
test that pressed Ctrl+V would paste into whatever window the person running
the suite had focused". Suppressing our own injection is what makes this one
safe to run on a live desktop.

THREE CTYPES TRAPS, each of which cost a run [MEASURED 2026-09-09]:

  1. Without `restype`/`argtypes`, ctypes truncates the 64-bit HMODULE and
     `SetWindowsHookExW` fails with error 126 (ERROR_MOD_NOT_FOUND), which
     reads like a missing DLL rather than a marshalling bug. hMod NULL is
     legal for a low-level hook whose proc is in this process.
  2. `dwExtraInfo` is a `ULONG_PTR` VALUE, not a pointer to one. Passing
     `ctypes.pointer(ULONG(MARK))` hands Windows an address, and the marker
     comes back as a different number on every event -- which looks exactly
     like Windows discarding it.
  3. `INPUT` must keep its unused MOUSEINPUT/HARDWAREINPUT union arms.
     Keyboard-only it is 32 bytes instead of 40, and `SendInput` validates
     `cbSize` and silently presses NOTHING.

NOT collected by pytest (`manual/` is excluded in pytest.ini).

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_hook.py

[EXECUTED 2026-09-09: hook installed, injected down+up seen, injected flag
set, extra-info marker intact.]
"""
import ctypes, ctypes.wintypes as w, sys, time

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP = 13, 0x0100, 0x0101
VK_MULTIPLY, LLKHF_INJECTED = 0x6A, 0x10
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
MARK = 0xB1717

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_ulonglong)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_ulonglong)]
class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
class INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", _U)]

seen = []
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, w.WPARAM, w.LPARAM)

def _proc(code, wparam, lparam):
    if code == 0:
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.vkCode == VK_MULTIPLY:
            seen.append((("down" if wparam == WM_KEYDOWN else "up"),
                         bool(kb.flags & LLKHF_INJECTED),
                         int(kb.dwExtraInfo)))
            return 1          # SUPPRESS: nothing on the desktop receives it
    return u32.CallNextHookEx(None, code, wparam, lparam)

cb = HOOKPROC(_proc)
u32.SetWindowsHookExW.restype = ctypes.c_void_p
u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, w.DWORD]
u32.CallNextHookEx.restype = ctypes.c_long
u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, w.WPARAM, w.LPARAM]
# hMod NULL is legal for a low-level hook whose proc lives in this process,
# and it sidesteps ctypes truncating a 64-bit HMODULE into a c_int -- which
# is what produced error 126 (ERROR_MOD_NOT_FOUND) on the first attempt.
hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, None, 0)
if not hook:
    print("FAIL SetWindowsHookExW error", ctypes.get_last_error()); sys.exit(1)
print("hook installed")

def send(up):
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_U(ki=KEYBDINPUT(wVk=VK_MULTIPLY, wScan=0,
                                   dwFlags=KEYEVENTF_KEYUP if up else 0,
                                   time=0, dwExtraInfo=MARK)))
    n = u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        print("FAIL SendInput error", ctypes.get_last_error())

def pump(seconds):
    msg = w.MSG(); end = time.time() + seconds
    while time.time() < end:
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)

send(False); pump(0.3)
send(True);  pump(0.3)
u32.UnhookWindowsHookEx(ctypes.c_void_p(hook))

print("events seen:", seen)
ok = [e[0] for e in seen] == ["down", "up"]
print("VERDICT hook sees injected keydown+keyup:", ok)
print("VERDICT injected flag set:", all(e[1] for e in seen) if seen else None)
print("VERDICT extra-info marker survives:",
      all(e[2] == MARK for e in seen) if seen else None)
sys.exit(0 if ok else 1)
