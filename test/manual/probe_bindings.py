"""Suppression is SELECTIVE, and this is the only thing that can prove it.

`docs/shortcuts.md` 7, AC-138, AC-139. A suppressed key is INVISIBLE to pytest:
the only observable difference between "the hook swallowed it" and "the hook
never fired" is whether the character reached an application, and nothing in
`test/` can look at an application. `test_desk_hotkeys.py` proves `_callback`
RETURNS 1, which is as far as a test can go; this proves Windows acts on it, and
that it acts on the RIGHT keystroke.

WHAT IT DOES
------------
Binds `Ctrl + numpad *` to the terminal action, installs the real hook, then
injects two things as a FINGER:

  * `Ctrl + numpad *` -- the bound chord. It must produce NO character in the
    scratch file and must reach the agent's queue.
  * a BARE `numpad *` -- the same physical key with no modifier, bound to
    nothing in this table. It must produce a literal `*` in the scratch file
    and must NOT reach the queue.

The second half is the whole point. A hook that suppressed every press of a
watched key would pass every test in `test_desk_hotkeys.py` and would stop the
owner typing `*` while hotkeys were on -- which is exactly what makes an
exact-modifier match a property of the shipped code rather than of `classify`.

THE WINDOW TITLE IS CHECKED BEFORE A SINGLE KEY IS INJECTED
-----------------------------------------------------------
`test/AGENTS.md` records what it cost when a probe assumed a process it launched
was a window it owned: `probe_dictation.py --paste` opened a bare `notepad.exe`,
Windows 11 Notepad RESTORED the operator's previous unsaved tab, and 95
characters landed in the middle of a document they were writing -- which Notepad
autosaved before the probe killed it. So this opens a NAMED scratch file and
refuses loudly unless the foreground window's title contains that name.

It types into Notepad, so it also SAVES with Ctrl+S and reads the file back
rather than trying to read the control's text.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_bindings.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from desk import actions, hotkeys, paste as paste_mod       # noqa: E402

MULTIPLY = actions.KEYS["numpad_multiply"].vk
VK_S = 0x53
SETTLE_S = 0.35
# Unique, and it is what the foreground check matches on. Long enough that no
# other window on the machine can carry it by accident.
SCRATCH_NAME = f"butler-probe-bindings-{os.getpid()}.txt"

TABLE = actions.Table([
    {"chord": "ctrl+numpad_multiply", "action": "terminal",
     "cwd": str(REPO), "agent": "pi", "label": "butler"},
])


def foreground_title() -> str:
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = ctypes.c_void_p
    handle = u.GetForegroundWindow()
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(ctypes.c_void_p(handle), buf, 512)
    return buf.value or ""


def require_scratch_window() -> None:
    """Refuse loudly unless the scratch file's own window has the focus.

    Not a nicety. `test/AGENTS.md`: "A process you launched is not a window you
    own." Anything injected while somebody else's window is focused is typed
    into somebody else's document.
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        title = foreground_title()
        if SCRATCH_NAME in title:
            print(f"foreground window is the scratch file ({title!r})")
            return
        time.sleep(0.25)
    raise SystemExit(
        f"REFUSING to inject anything: the foreground window is "
        f"{foreground_title()!r} and not the scratch file {SCRATCH_NAME}. "
        f"Anything typed now would land in somebody else's document.")


def tap(vk: int, ctrl: bool = False) -> None:
    """One press and release, as a FINGER.

    `mark=0`: every keystroke the agent itself sends carries `hotkeys.MARK` and
    its own hook passes those through, so a probe using the default would press
    a key the hook is built to ignore. That is not a hypothetical -- the first
    end-to-end probe in this repository did exactly that and read as a broken
    hook for a minute.
    """
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    if ctrl:
        paste_mod._send(u32, (actions.VK_CONTROL, False), mark=0)
    paste_mod._send(u32, (vk, False), mark=0)
    time.sleep(0.05)
    paste_mod._send(u32, (vk, True), mark=0)
    if ctrl:
        paste_mod._send(u32, (actions.VK_CONTROL, True), mark=0)


def release_control() -> None:
    """On every exit. An injected Ctrl-down nobody released leaves the operator's
    keyboard stuck in a modifier they never pressed."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    paste_mod._send(u32, (actions.VK_CONTROL, True), mark=0)


def drain(hook) -> list:
    out = []
    while not hook.events.empty():
        binding, edge = hook.events.get_nowait()
        out.append((binding.chord, binding.action, edge))
    return out


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Windows only")

    scratch_dir = REPO / "TMP"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch = scratch_dir / SCRATCH_NAME
    scratch.write_text("", encoding="utf-8")

    hook = hotkeys.Hook(TABLE)
    if not hook.start():
        raise SystemExit(f"the hook would not install: {hook.error}")
    print(f"hook installed; table: {TABLE.describe()}")

    editor = subprocess.Popen(["notepad.exe", str(scratch)])
    ok = True
    try:
        time.sleep(1.2)
        require_scratch_window()

        print("\n-- the BOUND chord: Ctrl plus numpad * --")
        tap(MULTIPLY, ctrl=True)
        time.sleep(SETTLE_S)
        seen = drain(hook)
        bound_reached = [e for e in seen if e[1] == "terminal"]
        print(f"  the agent's queue: {seen}")
        chord_ok = len(bound_reached) == 2          # down and up
        print(f"VERDICT the bound chord reached the agent: {chord_ok}")
        ok = ok and chord_ok

        print("\n-- the BARE key: numpad * on its own, bound to nothing --")
        tap(MULTIPLY)
        time.sleep(SETTLE_S)
        bare_seen = drain(hook)
        print(f"  the agent's queue: {bare_seen}")
        bare_quiet = bare_seen == []
        print(f"VERDICT an unbound chord did NOT reach the agent: {bare_quiet}")
        ok = ok and bare_quiet

        # Ctrl+S, then read the FILE. Reading the edit control's text would need
        # a second pile of Win32 and would prove less: what matters is the
        # character Windows actually delivered to the application.
        tap(VK_S, ctrl=True)
        time.sleep(0.8)
        typed = scratch.read_text(encoding="utf-8", errors="replace")
        print(f"\nthe scratch file holds {typed!r}")

        selective = typed.count("*") == 1
        print(f"VERDICT suppression is SELECTIVE -- exactly one '*' reached the "
              f"window, from the BARE press, and none from the bound chord: "
              f"{selective}")
        if not selective and typed.count("*") == 0:
            print("  0 asterisks means the hook suppressed the UNBOUND press "
                  "too, so the owner cannot type '*' while hotkeys are on")
        if typed.count("*") > 1:
            print("  more than one means the bound chord was passed through, "
                  "so the owner gets the character AND the action")
        ok = ok and selective
        return 0 if ok else 1
    finally:
        hook.stop()
        release_control()
        # Killed by the PID this probe RECORDED, never by an image name: the
        # operator may well have their own Notepad open (root AGENTS.md).
        if editor.poll() is None:
            editor.kill()
            editor.wait(timeout=10)
        try:
            scratch.unlink()
        except OSError:
            pass
        print("the hook is uninstalled, Ctrl is released, the scratch file and "
              "its editor are gone")


if __name__ == "__main__":
    sys.exit(main())
