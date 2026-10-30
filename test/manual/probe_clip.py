"""Can the clipboard be written and read back from ctypes alone?

The other half of "is step 3 self-testable". SAFE: whatever text is on the
clipboard now is saved first and put back at the end.
"""
import ctypes, ctypes.wintypes as w, sys

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
for f, r in ((u32.GetClipboardData, ctypes.c_void_p),
             (u32.SetClipboardData, ctypes.c_void_p),
             (k32.GlobalAlloc, ctypes.c_void_p),
             (k32.GlobalLock, ctypes.c_void_p)):
    f.restype = r
u32.SetClipboardData.argtypes = [w.UINT, ctypes.c_void_p]
u32.GetClipboardData.argtypes = [w.UINT]
# argtypes matter as much as restype here: without them ctypes marshals the
# 64-bit HGLOBAL that GlobalAlloc returned as a C int and overflows.
k32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
k32.GlobalSize.argtypes = [ctypes.c_void_p]
k32.GlobalSize.restype = ctypes.c_size_t

def _open():
    for _ in range(50):
        if u32.OpenClipboard(None):
            return True
        ctypes.windll.kernel32.Sleep(20)
    return False

def get_text():
    if not _open():
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.c_wchar_p(p).value or ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()

def set_text(text):
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _open():
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = k32.GlobalLock(h)
        ctypes.memmove(p, ctypes.byref(data), size)
        k32.GlobalUnlock(h)
        return bool(u32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        u32.CloseClipboard()

before = get_text()
print("saved existing clipboard:", "yes" if before is not None else "COULD NOT READ",
      f"({len(before)} chars)" if before else "")
probe = "butler dictation probe 2026-09-09"
ok_set = set_text(probe)
back = get_text()
print("set ok:", ok_set, "| round trip:", back == probe)
restored = set_text(before) if before else None
print("clipboard restored:", restored if before else "nothing to restore")
sys.exit(0 if (ok_set and back == probe) else 1)
