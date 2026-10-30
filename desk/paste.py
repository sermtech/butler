"""The clipboard, and getting text into the window the owner clicked.

`docs/desk-hotkeys.md` section 6. Four rules here were paid for by somebody
else, and each is cited where it lands.

**The chord is RAW VK CODES, never characters** (Handy `input.rs:33`). A
character-derived scancode breaks paste on Russian, AZERTY and Dvorak; VK_V is
VK_V on every layout.

**The owner's own modifiers are waited for.** Handy does not check and their
#2051 is the result -- the transcript lands while the hotkey's own modifier is
still physically down and the target sees `Ctrl+Alt+V`. A push-to-talk key
that contains a modifier makes this the common case rather than the rare one.

**There is no paste-delay SETTING.** Handy has one and their #502 -- open, 82
comments, marked critical -- is the wrong clipboard being pasted under load,
where a reporter walked the delay through ten values from 50 to 190 ms and
reproduced at every one. One conservative constant lives here; if it proves
insufficient the answer is a mechanism, not a knob the owner has to tune.

**The clipboard is NOT restored.** The owner asked for the transcript to stay
on it. Stated here because the opposite is the courtesy in this class of tool
-- Handy restores text, and an image only when there was no text, destroying
every other format on the way (`clipboard.rs:79-96`) -- so this is a decision
rather than an omission.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import time

from .hotkeys import MARK, modifiers_down

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_V, VK_INSERT = 0x56, 0x2D
VK_CONTROL, VK_SHIFT = 0x11, 0x10

# How long the modifier is held down around the keypress. Handy uses 100 ms
# hardcoded in three places with no name; this is the same value with one.
CHORD_HOLD_S = 0.100
# Between writing the clipboard and pressing the chord. The target has to be
# able to READ the clipboard by the time the keystroke lands.
CLIPBOARD_SETTLE_S = 0.120
# How long the owner's own modifiers are waited for before giving up and
# sending explicit key-ups. Bounded: a stuck modifier must not mean the
# transcript is never pasted.
MODIFIER_WAIT_S = 1.0


class _U(ctypes.Union):
    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                    ("time", w.DWORD), ("dwExtraInfo", ctypes.c_ulonglong)]
    # The unused arms are LOAD-BEARING. `SendInput` validates `cbSize` against
    # `sizeof(INPUT)`; keyboard-only this struct is 32 bytes instead of 40 and
    # the call silently presses nothing at all.
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", _U)]


def _dlls():
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    for fn, rt in ((u.GetClipboardData, ctypes.c_void_p),
                   (u.SetClipboardData, ctypes.c_void_p),
                   (k.GlobalAlloc, ctypes.c_void_p),
                   (k.GlobalLock, ctypes.c_void_p)):
        fn.restype = rt
    u.SetClipboardData.argtypes = [w.UINT, ctypes.c_void_p]
    u.GetClipboardData.argtypes = [w.UINT]
    # argtypes matter as much as restype: without them ctypes marshals the
    # 64-bit HGLOBAL that GlobalAlloc returned as a C int and overflows.
    k.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    return u, k


class ClipboardError(RuntimeError):
    """The clipboard could not be opened or written. Loud, never silent."""


def _open(u32, tries: int = 50) -> bool:
    """The clipboard is a single system-wide resource another process may
    hold. Retried rather than failed on the first refusal."""
    for _ in range(tries):
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard() -> str | None:
    u32, k32 = _dlls()
    if not _open(u32):
        return None
    try:
        handle = u32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = k32.GlobalLock(handle)
        try:
            return ctypes.c_wchar_p(pointer).value or ""
        finally:
            k32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


def set_clipboard(text: str) -> None:
    u32, k32 = _dlls()
    buffer = ctypes.create_unicode_buffer(text)
    if not _open(u32):
        raise ClipboardError("another process is holding the clipboard")
    try:
        u32.EmptyClipboard()
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buffer))
        pointer = k32.GlobalLock(handle)
        ctypes.memmove(pointer, ctypes.byref(buffer), ctypes.sizeof(buffer))
        k32.GlobalUnlock(handle)
        if not u32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ClipboardError(
                f"SetClipboardData refused, error {ctypes.get_last_error()}")
    finally:
        u32.CloseClipboard()


def _send(u32, *keys, mark: int = MARK) -> int:
    """`keys` is a sequence of `(vk, is_up)`. Answers how many were accepted.

    Every event is stamped with `MARK` so this process's own hook passes it
    through rather than reading the keystrokes it just sent.

    `mark=0` is how a test injects a keystroke the hook should treat as a
    FINGER. It exists because the first end-to-end probe used this function
    to press the hotkey, the mark did its job, and the hook correctly ignored
    the press -- which read as a broken hook for a minute.
    """
    events = (INPUT * len(keys))()
    for i, (vk, up) in enumerate(keys):
        events[i].type = INPUT_KEYBOARD
        events[i].u.ki.wVk = vk
        events[i].u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        events[i].u.ki.dwExtraInfo = mark
    return u32.SendInput(len(keys), ctypes.byref(events),
                         ctypes.sizeof(INPUT))


def wait_for_modifiers(budget_s: float = MODIFIER_WAIT_S,
                       ignore=()) -> list[str]:
    """Block until no modifier is physically down, or the budget expires.

    Answers whatever is STILL down, so the caller can say so. Bounded because
    a modifier that never comes up -- a stuck key, a remote session that ate
    the key-up -- must not mean the transcript is never delivered.

    `ignore` names the modifiers the CAPTURING BINDING contains, and it is a
    contract change made deliberately (`docs/shortcuts.md` 4.6, D-12). Until
    bindings were configurable the only chord with a modifier was the terminal
    one, which pastes nothing, so this function could treat every held
    modifier as an accident and inject a key-up for it. A HOLD binding on
    `ctrl+f13` makes that reachable: the owner is still pressing Ctrl at the
    moment the transcript lands, and injecting Ctrl-up for a key they are
    physically holding leaves Windows and the application disagreeing about it
    until they press and release it again.
    """
    skip = set(ignore or ())
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        still = [m for m in modifiers_down() if m not in skip]
        if not still:
            return []
        time.sleep(0.02)
    return [m for m in modifiers_down() if m not in skip]


def paste(text: str, method: str = "ctrl_v", ignore_mods=()) -> str:
    """Put `text` on the clipboard and deliver it. Answers what it did.

    `clipboard_only` is not a degraded mode: it is the honest answer for an
    application that refuses both chords, and it leaves the owner one Ctrl+V
    away from their text rather than nothing.

    `ignore_mods` is the capturing binding's own modifiers; see
    `wait_for_modifiers`.
    """
    set_clipboard(text)
    if method == "clipboard_only":
        return "clipboard"

    u32, _k32 = _dlls()
    stuck = wait_for_modifiers(ignore=ignore_mods)
    if stuck:
        # Send explicit key-ups rather than pressing anyway. A chord composed
        # with a modifier the owner is still holding is a DIFFERENT chord, and
        # in an editor `Ctrl+Shift+V` is often "paste without formatting" --
        # or nothing at all.
        _send(u32, (VK_CONTROL, True), (VK_SHIFT, True))
    time.sleep(CLIPBOARD_SETTLE_S)

    if method == "shift_insert":
        modifier, key = VK_SHIFT, VK_INSERT
    else:
        modifier, key = VK_CONTROL, VK_V
    sent = _send(u32, (modifier, False), (key, False))
    time.sleep(CHORD_HOLD_S)
    sent += _send(u32, (key, True), (modifier, True))
    if sent != 4:
        # UIPI: a medium-integrity process cannot send input to an elevated
        # window, and `SendInput` reports it by accepting fewer events rather
        # than by failing. The text is still on the clipboard, so this is a
        # degraded delivery rather than a lost one -- and saying which is the
        # difference between a bug report and a shrug (AC-115).
        return f"blocked ({sent}/4 events accepted; elevated window?)"
    return "pasted" if not stuck else f"pasted (after clearing {'+'.join(stuck)})"
