"""A native folder chooser, in a process that exists for one dialog.

`docs/shortcuts.md` D6, AC-143. Run as a SCRIPT by the daemon, never imported
by the desk agent:

    python -X utf8 desk\\browse.py [--start <dir>]

It prints the chosen directory on stdout and nothing else, and exits 0 with no
output when the owner cancels. Every diagnostic goes to stderr, because stdout
IS the answer.

WHY THIS IS NOT THE PICKER THE OWNER OVERRULED
----------------------------------------------
A tkinter directory picker shipped inside the desk agent for exactly one commit
and was deleted (`desk/AGENTS.md`, owner's ruling 2026-09-09). What made it bad
is mechanical rather than aesthetic and both halves are fixed by being a
separate short-lived process:

* It BLOCKED the agent's single decision thread, so the settings poll stopped
  and every dictation was refused until somebody dismissed the window. Nothing
  in this process serves a hotkey or a turn, and the daemon waits for it off
  the event loop.
* It took the FOREGROUND from the window the owner had clicked, which is
  exactly what AC-114's second half forbids. Here the owner clicked "Browse..."
  in the browser a moment ago, so taking the foreground is the thing they
  asked for.

**THE DESK AGENT STILL DRAWS NOTHING.** That rule is unchanged and this file
does not weaken it: `desk/deskd.py` neither imports nor spawns this. It lives
in `desk/` because everything that talks to the Windows desktop lives in
`desk/`, and the daemon spawns it the way it spawns every other child.

It is also the first thing to delete if the owner dislikes it. The path field
beside the Browse button in the pane is a plain text input with the `projects`
list behind it, and works with no dialog at all.
"""
from __future__ import annotations

import argparse
import sys


def choose(start: str = "") -> str:
    """Show the dialog and answer the chosen directory, or "" on cancel.

    tkinter is in the standard library, so this adds no dependency -- the same
    reasoning `daemon/childproc.py` uses for ctypes. `withdraw()` before
    `askdirectory` so the empty root window never appears; without it Windows
    paints a bare grey frame behind the dialog and leaves it on the taskbar.
    """
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    try:
        chosen = filedialog.askdirectory(
            title="Butler: choose a project directory",
            initialdir=start or None, mustexist=True)
    finally:
        root.destroy()
    return str(chosen or "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="browse")
    ap.add_argument("--start", default="",
                    help="the directory to open the dialog in")
    args = ap.parse_args(argv)
    try:
        chosen = choose(args.start)
    except Exception as e:                                # noqa: BLE001
        # Loud on STDERR and a non-zero exit, so the route can answer 502 with
        # a reason rather than reporting a cancel the owner did not make. A
        # machine with no display, or a python built without tkinter, lands
        # here.
        print(f"the folder chooser would not open: {e!r}", file=sys.stderr)
        return 1
    if chosen:
        # Backslashes as Windows writes them. `askdirectory` answers forward
        # slashes, and a path with the wrong separator still WORKS everywhere
        # here -- but it is about to be shown to the owner in a pane and stored
        # in a file they can read, and two spellings of one directory look like
        # two directories.
        print(chosen.replace("/", "\\"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
