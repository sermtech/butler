"""The global keyboard hook, and nothing else.

`docs/desk-hotkeys.md` D2. A `WH_KEYBOARD_LL` hook rather than
`RegisterHotKey`, for the reason Handy chose the same: `RegisterHotKey` gives
no key-UP, and push-to-talk is defined by the release.

ctypes on purpose -- `daemon/childproc.py`'s precedent, "no new dependency for
four Win32 calls" -- and the three traps below each cost a run while proving
this was possible at all (`test/manual/probe_hook.py`, MEASURED 2026-09-09):

  1. Without `restype`/`argtypes`, ctypes truncates a 64-bit handle and
     `SetWindowsHookExW` fails with error 126, which reads like a missing DLL.
     hMod NULL is legal for a low-level hook whose proc is in this process.
  2. `dwExtraInfo` is a `ULONG_PTR` VALUE. Passing a pointer to one hands
     Windows an address, and the marker comes back different every event --
     indistinguishable from Windows discarding it.
  3. `INPUT` must keep its unused union arms or it is 32 bytes instead of 40,
     and `SendInput` validates `cbSize` and silently presses NOTHING.

FIVE RULES THIS FILE EXISTS TO KEEP
-----------------------------------
**The callback does no work.** It classifies, suppresses, and puts an event on
a queue. Anything slower runs on the coordinator thread, because a low-level
hook that blocks stalls every keystroke on the machine -- and Windows silently
REMOVES a hook whose thread stops pumping past `LowLevelHooksTimeout`, which
is the failure where a dictation app appears to work and simply stops
answering its own key.

**NOTHING HERE MAY PRINT.** The agent inherits the daemon's stdout
(`daemon/desk.py`), and a Windows console write BLOCKS INDEFINITELY while the
operator has text selected in quick-edit mode -- so one log line on this
thread silently uninstalls the hook. Every diagnostic belongs on the
coordinator thread, which is why `classify` answers rather than reports.

**Our own injected keys are passed through.** Every `SendInput` this process
makes is stamped with `MARK`, and the hook ignores anything carrying it. Not
theoretical: the paste chord is `Ctrl` plus `V`, and a hook that treated its
own injection as user input would be reading the keystrokes it just sent.

**The table is published by a SINGLE ATTRIBUTE STORE and read ONCE into a
local. No lock** (`docs/shortcuts.md` 4.4). The coordinator would hold that
lock while it parses and formats a new table, so a lock here converts a
settings-poll hiccup into a removed hook -- the failure the paragraph above is
about. An atomic reference swap costs nothing and cannot stall.

**A SUPPRESSED KEY CANNOT BE POLLED, so nothing here may ask whether one is
still down.** MEASURED 2026-09-09, one variable changed, the same injected key
through the same callback: with the hook SUPPRESSING it,
`GetAsyncKeyState(vk) & 0x8000` reads False 5 times out of 5 while the key is
physically held; with the hook passing it through, True 5 of 5. Returning
non-zero from a low-level hook stops Windows updating the async key-state table
for that key -- which is what suppression IS -- so the physical state of every
key in the table is unreadable BY CONSTRUCTION.
That is not a curiosity. A `key_down()` helper shipped here for one commit and
broke dictation completely and silently: the coordinator's 10 ms tick asked
whether the capturing key was still down, was told no, and ended every capture
inside 200 ms -- so every clip was the lookback ring and the daemon answered
"nothing was said in that clip", which is a statement about SPEECH. The
instrument is `test/manual/probe_asyncstate.py` and it exists so nobody adds
the poll back. What replaced it is a TIMEOUT (`deskd.Agent.check_stuck`), which
needs no key state at all.

**The DOWN resolves and the UP REPLAYS it.** `Hook._held` maps a vk to the
binding its key-down matched, so a release is never re-resolved from whatever
modifiers happen to be held at that instant. Two things fall out and both are
the point: suppression is SYMMETRIC, because a DOWN that was passed through
leaves no entry and its UP passes through too; and the whole class of
mislabelled-edge defects this project paid for twice -- a release the hook
called `dictate` because Ctrl was let go first, and the mirror image that lost
a whole dictation -- stops being produced at all.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import queue
import threading

from . import actions as actions_mod

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
LLKHF_INJECTED = 0x10

# The modifier VKs, re-exported from the ONE key table rather than declared
# again here (`desk/actions.py`). Two copies of a VK constant drift, and the
# drifted copy is the one the hook polls.
VK_CONTROL = actions_mod.VK_CONTROL
VK_SHIFT = actions_mod.VK_SHIFT
VK_MENU = actions_mod.VK_MENU              # Alt
VK_LWIN, VK_RWIN = actions_mod.VK_LWIN, actions_mod.VK_RWIN

# Stamped into every keystroke this PROCESS injects, so the hook can tell its
# own paste chord from the owner's fingers. An arbitrary constant; its only
# requirement is that nothing else uses it.
MARK = 0xB1717

DOWN, UP = "down", "up"


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD),
                ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_ulonglong)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, w.WPARAM, w.LPARAM)


def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.SetWindowsHookExW.restype = ctypes.c_void_p
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, ctypes.c_void_p,
                                    w.DWORD]
    u.CallNextHookEx.restype = ctypes.c_long
    u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, w.WPARAM,
                                 w.LPARAM]
    u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    u.GetAsyncKeyState.restype = ctypes.c_short
    return u


def modifiers_down(u32=None) -> list[str]:
    """Which modifier keys are PHYSICALLY held right now.

    Read before injecting the paste chord. Handy does not check, and their
    #2051 is the result: the transcript lands while the owner still holds the
    hotkey's own modifier, so the target sees `Ctrl+Alt+V` instead of
    `Ctrl+V`. Their only defence is a 50 ms grace plus a 60 ms delay, which is
    a race rather than a rule.
    """
    u = u32 or _user32()
    names = {VK_CONTROL: "ctrl", VK_SHIFT: "shift", VK_MENU: "alt",
             VK_LWIN: "win", VK_RWIN: "win"}
    # The HIGH bit is "down now"; the low bit is "was pressed since last
    # asked" and is a different question that would report a key the owner
    # released a moment ago.
    return sorted({name for vk, name in names.items()
                   if u.GetAsyncKeyState(vk) & 0x8000})


def input_desktop_reachable() -> tuple[bool, str]:
    """Can this process see the desktop the owner is typing on?

    MEASURED 2026-09-09, and it is the reason this function exists: a probe
    that had installed a hook and driven an injected keystroke through it an
    hour earlier suddenly saw nothing at all, and `SendInput` began returning
    0 with ERROR_ACCESS_DENIED. Nothing about the code had changed. This
    process was in session 2, the active console session was 1, and
    `OpenInputDesktop` was denied -- a disconnected or non-input session,
    which is what a machine reached over RDP becomes when the connection
    drops or the screen locks.

    A hook installed there SUCCEEDS and then receives nothing, forever, with
    no error anywhere. That is the shape of Handy's largest bug cluster
    (`.references/push-to-talk-prior-art.md` section 2), and the difference
    between a hotkey that is broken and one that is merely on the wrong
    desktop is a sentence the owner can act on (AC-7b).

    Answers `(reachable, why_not)`. Never raises.
    """
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.OpenInputDesktop.restype = ctypes.c_void_p
        handle = u.OpenInputDesktop(0, False, 0x0100)   # DESKTOP_READOBJECTS
        if handle:
            u.CloseDesktop(ctypes.c_void_p(handle))
            return True, ""
        err = ctypes.get_last_error()
        ours = w.DWORD()
        k.ProcessIdToSessionId(k.GetCurrentProcessId(), ctypes.byref(ours))
        console = k.WTSGetActiveConsoleSessionId()
        return False, (
            f"this process cannot reach the input desktop (error {err}); it "
            f"is in session {ours.value} and the console session is "
            f"{console}. A hotkey installed here would never fire. The usual "
            f"cause is a locked screen or a disconnected remote session")
    except Exception as e:                                # noqa: BLE001
        return True, f"the input-desktop check itself failed ({e!r})"


def mods_now(u32) -> int:
    """The modifier bitmask, PHYSICALLY, right now.

    Five `GetAsyncKeyState` calls, and they run only for a key the table
    already claims -- the frozenset test in `classify` comes first. The HIGH
    bit is "down now"; the low bit is "pressed since last asked" and would
    report a modifier the owner released a moment ago.
    """
    mods = 0
    for vk, bit in actions_mod.MOD_VKS:
        if u32.GetAsyncKeyState(vk) & 0x8000:
            mods |= bit
    return mods


def classify(table, held: dict, vk: int, wparam: int, mods: int):
    """One keyboard edge, decided. Answers `(event, suppress)`.

    THE SEAM. Everything about which key does what lives here and nothing
    here touches Win32, which is what finally makes the hook testable in
    pytest (`docs/shortcuts.md` 4.4). It is pure of WINDOWS, not of state: it
    updates `held`, which belongs to the caller and is owned by the hook
    thread.

    `event` is `(binding, edge)` for the coordinator's queue, or None.
    `suppress` is whether the keystroke must be swallowed instead of reaching
    the application under the caret.

    Five cases, and the ordering of the first is the hot path: `vk not in
    table.keys` is ONE frozenset test and it runs for every keystroke on the
    machine.
    """
    if vk not in table.keys and vk not in held:
        # THE HOT PATH, and the second half of it is load-bearing rather than
        # belt and braces. A key REBOUND AWAY while it is physically held still
        # owes its key-up: without the `held` test the release is passed through,
        # the entry is never popped, and the capture runs on with the microphone
        # open and a stage the agent can never leave (D-1). Both tests are O(1)
        # and `held` is empty almost all the time.
        return None, False
    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        edge = DOWN
    elif wparam in (WM_KEYUP, WM_SYSKEYUP):
        edge = UP
    else:
        return None, False
    if edge is DOWN:
        already = held.get(vk)
        if already is not None:
            # AUTO-REPEAT: a low-level hook sees a held key as a stream of
            # key-downs with no key-up between them. Replayed rather than
            # re-resolved, so a modifier touched mid-hold cannot relabel the
            # edge -- and still suppressed, or the character the owner is
            # dictating into appears twenty times.
            return (already, DOWN), True
        binding = table.match(vk, mods)
        if binding is None:
            # A bound key under the WRONG modifiers. Passed through untouched
            # and NOT recorded, which is what makes suppression symmetric:
            # its key-up finds no entry and passes through as well.
            return None, False
        held[vk] = binding
        return (binding, DOWN), True
    binding = held.pop(vk, None)
    if binding is None:
        return None, False
    return (binding, UP), True


class Hook:
    """The hook, its thread, and the queue it feeds.

    The table decides everything; this class owns the Windows machinery and
    the message pump. The queue carries `(binding, edge)`.
    """

    def __init__(self, table=None):
        # A table is required in practice and defaults to an EMPTY one rather
        # than to a shipped binding: defaulting would bind a key nobody asked
        # for on a machine whose owner had configured none (AC-140).
        self._table = table if table is not None else actions_mod.Table()
        # Owned by the hook thread and by nothing else. `set_table` never
        # touches it, which is why swapping the table mid-hold still lets the
        # release end what the press started.
        self._held: dict = {}
        self.events: queue.Queue = queue.Queue()
        self.installed = threading.Event()
        self.error: str = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._u32 = None
        self._handle = None
        # Held on the instance so the trampoline is never garbage collected
        # while Windows still holds a pointer to it -- a collected callback is
        # a crash inside the hook chain, not an exception here.
        self._proc = None

    # -- publishing a new table --------------------------------------------- #
    def set_table(self, table) -> None:
        """Swap the table. ONE assignment, no lock (see the module docstring).

        Safe because a `Table` is immutable and `_callback` reads this
        attribute ONCE into a local: a callback that started under the old
        table finishes under it, and the next keystroke sees the new one.
        """
        self._table = table

    @property
    def table(self):
        return self._table

    # -- the callback ------------------------------------------------------- #
    def _callback(self, code, wparam, lparam):
        if code != 0:
            return self._u32.CallNextHookEx(None, code, wparam, lparam)
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if int(kb.dwExtraInfo) == MARK:
            # Ours, and injected. The paste chord is Ctrl plus V, and a hook
            # that read its own injection would be reading the keystrokes it
            # just sent.
            return self._u32.CallNextHookEx(None, code, wparam, lparam)
        # ONCE into a local, so a table published mid-callback cannot change
        # the answer half way through this decision.
        table = self._table
        vk = kb.vkCode
        if vk not in table.keys and vk not in self._held:
            # THE HOT PATH: two O(1) membership tests, and it runs for every
            # keystroke on this machine. Nothing above it may be added. The
            # `_held` half is what still delivers the release of a key that was
            # rebound away while the owner was holding it -- see `classify`.
            return self._u32.CallNextHookEx(None, code, wparam, lparam)
        mods = mods_now(self._u32)
        event, suppress = classify(table, self._held, vk, wparam, mods)
        if event is not None:
            self.events.put(event)
        if suppress:
            # Without this the held key auto-repeats into whatever the owner
            # was about to dictate into, which is the first thing they would
            # see (`docs/desk-hotkeys.md` D2).
            return 1
        return self._u32.CallNextHookEx(None, code, wparam, lparam)

    # -- lifetime ----------------------------------------------------------- #
    def _run(self) -> None:
        self._u32 = _user32()
        self._proc = _HOOKPROC(self._callback)
        self._handle = self._u32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._handle:
            self.error = (f"SetWindowsHookExW failed, error "
                          f"{ctypes.get_last_error()}")
            self.installed.set()
            return
        self.installed.set()
        msg = w.MSG()
        # PeekMessage in a loop rather than GetMessage, because GetMessage
        # blocks until a message arrives and a low-level hook's thread may
        # receive none for hours -- there would be nothing to notice `_stop`
        # with, and the thread could not be joined.
        while not self._stop.is_set():
            while self._u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self._u32.TranslateMessage(ctypes.byref(msg))
                self._u32.DispatchMessageW(ctypes.byref(msg))
            self._stop.wait(0.005)
        self._u32.UnhookWindowsHookEx(ctypes.c_void_p(self._handle))
        self._handle = None

    def start(self, timeout: float = 5.0) -> bool:
        """Install the hook on its own thread. Answers whether it took."""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="desk-hook")
        self._thread.start()
        self.installed.wait(timeout)
        return bool(self._handle)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
