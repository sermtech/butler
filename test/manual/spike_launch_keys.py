"""GO/NO-GO: does a keyboard's MAIL button become a keystroke a hook can take?

    ANSWER ON THE OWNER'S KEYBOARD: **NO.** [MEASURED 2026-09-09]
    Three runs, 100 s of watching, with the control: the hook saw 55 ordinary
    key-downs -- so it was alive and receiving -- and ZERO keys in 0xA6-0xB7.
    The mail button never becomes a keyboard event, so Butler CANNOT bind it and
    no amount of hook work will change that. The remaining route is the
    `AppKey` registry redirect; see the bottom of this docstring.

    There is also no in-process way round it. The key arrives as
    `WM_APPCOMMAND`, which is delivered to the FOREGROUND window and the shell;
    a process can only receive one for its own windows, and the desk agent has
    none. `WH_GETMESSAGE` would need a DLL injected into every process.


`docs/shortcuts.md` section 10. The owner's keyboard has a physical mail button.
Pressing it opens a Chrome tab, because this machine's `mailto:` handler is
`ChromeHTML` [READ from the registry 2026-09-09] and the key resolves through
that association. They want it to open Butler instead.

TWO QUESTIONS, AND NEITHER CAN BE ANSWERED BY READING
-----------------------------------------------------
1. **Does the key become a keystroke at all?** Some keyboards send these as HID
   consumer-control usages that Windows turns into `WM_APPCOMMAND` without ever
   producing a keyboard event, and a `WH_KEYBOARD_LL` hook sees NOTHING. Others
   send an extended scancode and the hook sees vk 0xB4. It depends on the
   hardware and there is no way to know but to press it.
2. **If the hook suppresses it, does the mail launch stop?** If the keystroke and
   the APPCOMMAND are independent paths, suppressing one leaves the other -- and
   Butler would open its page while Chrome still opened its tab. Worse than
   doing nothing.

Phase 1 answers the first with a PASS-THROUGH hook, so nothing changes while it
runs. Phase 2 answers the second by suppressing whatever phase 1 saw.

WHAT IT RECORDS, AND WHAT IT REFUSES TO
---------------------------------------
**No letter, digit or punctuation key is ever recorded.** This installs a hook
that sees every keystroke on the machine, so it filters to the ranges that could
possibly be a launch key -- 0xA6-0xB7 (browser, volume, media, launch),
0x88-0x8F (unassigned) and 0xE8/0xFB-0xFE (OEM specific) -- and discards
everything else before it is even counted. A diagnostic that logged the
operator's typing would be a keylogger, and this one is going to be run against
a live desktop.

THE ROUTE THAT IS LEFT, ALSO READ FROM THIS MACHINE
---------------------------------------------------
`HKCU\\...\\CurrentVersion\\Explorer\\AppKey` is ABSENT, and the machine-wide
`HKLM\\...\\Explorer\\AppKey` has subkeys `7, 15, 16, 17, 18` -- browser-home,
mail, media-select, app1, app2 -- and **all five are EMPTY** [READ 2026-09-09].
Those numbers are the APPCOMMAND values. Nothing overrides the mail key today,
which is why it falls through to the `mailto:` association. A `ShellExecute`
string value under `HKCU\\...\\AppKey\\15` is the per-user, no-admin,
delete-one-key-to-undo way to point it somewhere else. **That claim is from
memory of Windows behaviour and is NOT measured here**; the test is to set it and
press the button.

WHAT THE FIRST VERSION OF THIS SPIKE GOT WRONG
----------------------------------------------
It had no control, so its first two runs reported "NO" when the honest answer was
"this measures nothing". A run seeing no launch key is ambiguous between "the
keyboard has none" and "the hook is receiving nothing at all" -- and the second
is a live possibility, because a hook on a session that does not own the input
desktop SUCCEEDS and then receives nothing forever (`desk/AGENTS.md`). Counting
ordinary key-downs settles it, and the instrument was ALSO mis-validated: an
injected vk proved the recorder worked, which does not prove the hook sees a real
finger. "One wrong answer ruled out is not an instrument" (test/AGENTS.md).

Run:  .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_launch_keys.py
      ...\\spike_launch_keys.py --phase 2 --vk 0xB4     (only if phase 1 is GO)
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from desk import hotkeys                                    # noqa: E402

WATCH_S = 12.0
# The only vks this spike will look at. Everything else is dropped before it is
# recorded -- see the module docstring.
INTERESTING = (set(range(0xA6, 0xB8)) | set(range(0x88, 0x90))
               | {0xE8} | set(range(0xFB, 0xFF)))
# What Windows calls the app-key ids, so the report names the button rather than
# a number. These are the APPCOMMAND values, which are also the `AppKey` registry
# subkey names -- this machine has 7, 15, 16, 17 and 18 registered and all five
# EMPTY [READ 2026-09-09].
KNOWN = {
    0xA6: "browser_back", 0xA7: "browser_forward", 0xA8: "browser_refresh",
    0xA9: "browser_stop", 0xAA: "browser_search", 0xAB: "browser_favorites",
    0xAC: "browser_home (AppKey 7)", 0xAD: "volume_mute",
    0xAE: "volume_down", 0xAF: "volume_up", 0xB0: "media_next",
    0xB1: "media_prev", 0xB2: "media_stop", 0xB3: "media_play_pause",
    0xB4: "LAUNCH_MAIL (AppKey 15)", 0xB5: "launch_media_select (AppKey 16)",
    0xB6: "launch_app1 (AppKey 17)", 0xB7: "launch_app2 (AppKey 18)",
}


class Watcher:
    """A hook that records the interesting vks and optionally swallows one.

    Written here rather than reusing `hotkeys.Hook` on purpose: that class takes
    a `Table`, and a Table cannot hold a key `desk/actions.KEYS` does not name --
    which is exactly the key under test. The machinery below is the same three
    ctypes calls, with the traps `desk/hotkeys.py` already paid for.
    """

    def __init__(self, suppress_vk: int | None = None):
        self.suppress_vk = suppress_vk
        self.seen: list[tuple[int, int, int, bool]] = []
        # THE CONTROL, and the spike was WRONG without it. A run reporting no
        # launch key is ambiguous between "this keyboard never produces one" and
        # "the hook is receiving nothing at all" -- and the second is a live
        # possibility here, because a hook on a session that does not own the
        # input desktop SUCCEEDS and then receives nothing, forever, with no
        # error (`desk/AGENTS.md`). A COUNT of everything else settles it: any
        # ordinary typing at all proves the hook works, so zero launch keys
        # beside a non-zero count is a fact about the KEY.
        #
        # A count and never a key. This callback sees every keystroke on the
        # machine and must not be able to reconstruct what was typed.
        self.other_downs = 0
        self._u32 = None
        self._proc = None
        self._handle = None

    def _callback(self, code, wparam, lparam):
        if code != 0:
            return self._u32.CallNextHookEx(None, code, wparam, lparam)
        kb = ctypes.cast(lparam,
                         ctypes.POINTER(hotkeys.KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        down = wparam in (hotkeys.WM_KEYDOWN, hotkeys.WM_SYSKEYDOWN)
        if vk in INTERESTING:
            self.seen.append((vk, kb.scanCode, wparam,
                              bool(kb.flags & hotkeys.LLKHF_INJECTED)))
            if self.suppress_vk is not None and vk == self.suppress_vk:
                return 1
        elif down:
            self.other_downs += 1
        return self._u32.CallNextHookEx(None, code, wparam, lparam)

    def __enter__(self):
        self._u32 = hotkeys._user32()
        self._proc = hotkeys._HOOKPROC(self._callback)
        self._handle = self._u32.SetWindowsHookExW(
            hotkeys.WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._handle:
            raise SystemExit(f"SetWindowsHookExW failed, error "
                             f"{ctypes.get_last_error()}")
        return self

    def pump(self, seconds: float, stop_on_hit: bool = False) -> None:
        """Pump messages on THIS thread for `seconds`.

        A low-level hook whose thread stops pumping past `LowLevelHooksTimeout`
        is silently REMOVED by Windows, so the countdown and the pump have to be
        the same loop.

        `stop_on_hit` ends the wait as soon as a key-DOWN is recorded, so the
        operator does not sit through a window that has already answered. It is
        off for the suppressing phase, where a SECOND press is worth seeing.
        """
        import ctypes.wintypes as w

        msg = w.MSG()
        deadline = time.monotonic() + seconds
        last = -1
        while time.monotonic() < deadline:
            while self._u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self._u32.TranslateMessage(ctypes.byref(msg))
                self._u32.DispatchMessageW(ctypes.byref(msg))
            if stop_on_hit and any(
                    wp in (hotkeys.WM_KEYDOWN, hotkeys.WM_SYSKEYDOWN)
                    for _v, _s, wp, _i in self.seen):
                # A moment more, so the key-UP of the press that just landed is
                # recorded too rather than arriving after the hook is gone.
                time.sleep(0.25)
                break
            left = int(deadline - time.monotonic()) + 1
            if left != last:
                print(f"    ...{left:2d}s", end="\r", flush=True)
                last = left
            time.sleep(0.01)
        print("            ", end="\r")

    def __exit__(self, *_exc):
        if self._handle:
            self._u32.UnhookWindowsHookEx(ctypes.c_void_p(self._handle))
        self._handle = None


def chrome_pids() -> set[int]:
    """Corroboration only. The OPERATOR's eyes are the verdict in phase 2.

    A new tab spawns a renderer process, so the count usually moves -- but Chrome
    starts and reaps renderers on its own, so this is evidence and not proof, and
    the report says which.
    """
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe",
                          "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        bits = [b.strip('"') for b in line.split('","')]
        if len(bits) > 1 and bits[1].isdigit():
            pids.add(int(bits[1]))
    return pids


def report(seen) -> dict:
    downs = {}
    for vk, scan, wparam, injected in seen:
        if wparam in (hotkeys.WM_KEYDOWN, hotkeys.WM_SYSKEYDOWN):
            downs.setdefault(vk, (scan, injected))
    for vk, (scan, injected) in sorted(downs.items()):
        print(f"    vk 0x{vk:02X}  scan 0x{scan:02X}  "
              f"{'INJECTED' if injected else 'real finger'}  "
              f"{KNOWN.get(vk, 'unknown to this spike')}")
    return downs


def phase1(seconds: float) -> int:
    reachable, why = hotkeys.input_desktop_reachable()
    if not reachable:
        # Refused rather than reported as a NO, the way `Agent._start` does. A
        # hook here would install cleanly and receive nothing forever, and this
        # spike would blame the keyboard for it (AC-7b).
        print(f"REFUSING TO MEASURE: {why}")
        return 3
    print("-- PHASE 1: does the key become a keystroke? NOTHING IS SUPPRESSED,")
    print("   so your button behaves exactly as it does today.")
    print(f"   PRESS YOUR MAIL BUTTON, then TYPE A FEW LETTERS anywhere.")
    print("   The letters are the CONTROL -- they are counted and never")
    print(f"   recorded, and they prove the hook is alive. {seconds:.0f} s.")
    with Watcher() as w:
        w.pump(seconds)
    print(f"   ordinary key-downs seen (count only): {w.other_downs}")
    print("   launch/media keys seen:")
    downs = report(w.seen)
    if not downs and not w.other_downs:
        print("\nVERDICT: NOTHING AT ALL, so this measures nothing. The hook saw")
        print("no launch key AND no typing, which means it is not receiving")
        print("events -- not that your keyboard lacks the key. Most likely the")
        print("press and the typing happened outside the window, or this process")
        print("is not on the desktop you are typing on. Re-run and type while it")
        print("counts down.")
        return 3
    if not downs:
        print(f"\nVERDICT question 1: NO, and the control makes it a real NO --")
        print(f"the hook saw {w.other_downs} ordinary key-downs, so it is alive,")
        print("and it still saw no launch or media key. This keyboard delivers")
        print("that button as a HID consumer usage or through a vendor driver,")
        print("so a low-level hook can NEVER take it: Butler cannot bind this")
        print("key and the AppKey registry route is the only way.")
        return 2
    mail = 0xB4 if 0xB4 in downs else sorted(downs)[0]
    print(f"\nVERDICT question 1: YES -- vk 0x{mail:02X} reaches a low-level "
          f"hook.")
    if 0xB4 not in downs:
        print(f"   NOTE: 0xB4 (mail) was NOT among them, so phase 2 should be "
              f"pointed at the right key explicitly: --vk 0x{mail:02X}")
    print(f"   next:  --phase 2 --vk 0x{mail:02X}")
    return 0


def phase2(seconds: float, vk: int) -> int:
    print(f"-- PHASE 2: does SUPPRESSING vk 0x{vk:02X} stop the mail launch?")
    print("   PRESS THE SAME BUTTON. This time the hook swallows it.")
    print("   WATCH YOUR SCREEN -- whether a Chrome tab appears is the verdict,")
    print(f"   and no script here can read it for you. {seconds:.0f} s.")
    before = chrome_pids()
    with Watcher(suppress_vk=vk) as w:
        w.pump(seconds)
    after = chrome_pids()
    caught = report(w.seen)
    print(f"\n   the hook saw the key: {vk in caught}")
    print(f"   ordinary key-downs seen (count only): {w.other_downs}")
    print(f"   chrome.exe processes: {len(before)} -> {len(after)} "
          f"(evidence only -- Chrome churns renderers by itself)")
    print("\nVERDICT question 2 is YOURS TO READ:")
    print("   NO new tab appeared  -> suppression works. Butler can own this")
    print("                          key. Both answers are GO.")
    print("   a tab DID appear     -> the keystroke and the APPCOMMAND are")
    print("                          separate paths. Butler would open its page")
    print("                          AND Chrome would open a tab, which is")
    print("                          worse than doing nothing. NO-GO for the")
    print("                          hook; use the AppKey registry route.")
    return 0 if vk in caught else 2


def main() -> int:
    import argparse

    if os.name != "nt":
        raise SystemExit("Windows only")
    ap = argparse.ArgumentParser(prog="spike_launch_keys")
    ap.add_argument("--phase", type=int, choices=(1, 2), default=1,
                    help="1 observes and suppresses nothing; 2 suppresses --vk")
    ap.add_argument("--vk", default="0xB4",
                    help="the key phase 2 swallows; 0xB4 is LAUNCH_MAIL")
    ap.add_argument("--seconds", type=float, default=WATCH_S)
    args = ap.parse_args()

    print("SPIKE: can Butler take the mail button?\n")
    print("Butler's own desk agent may be running with its own hook. That is")
    print("fine: its table does not name this key, so it passes it through.\n")
    if args.phase == 1:
        return phase1(args.seconds)
    return phase2(args.seconds, int(args.vk, 0))


if __name__ == "__main__":
    sys.exit(main())
