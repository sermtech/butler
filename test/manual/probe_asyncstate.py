"""A SUPPRESSED key cannot be polled. This is the measurement that proves it.

`docs/shortcuts.md` AC-145. Kept in the tree for one reason: the obvious way to
recover a lost key-up is to ask whether the key is still physically down, that
way SHIPPED HERE FOR ONE COMMIT, and it broke dictation completely and silently.
This probe exists so the next session measures instead of reasoning.

WHAT WENT WRONG
---------------
`Agent.check_stuck` asked `GetAsyncKeyState(capturing_vk)` on the coordinator's
10 ms tick. It was told the key was up -- while the owner was holding it and
speaking -- so every capture ended inside 200 ms, every clip was the lookback
ring and nothing live, and `POST /api/dictate` answered "nothing was said in
that clip". That is a sentence about SPEECH for a failure that was nothing of
the kind, which is the same wrong-organ trap `docs/desk-audio.md` records.

WHAT THIS MEASURES
------------------
One variable: whether the hook SUPPRESSES the key. Same key, same callback,
same thread doing the reading.

    MEASURED 2026-09-09 on the owner's machine:
      hook suppresses  -> key_down False, False, False, False, False
      hook passes thru -> key_down True,  True,  True,  True,  True

Returning non-zero from a `WH_KEYBOARD_LL` callback is precisely Windows NOT
updating the async key-state table for that keystroke -- that is what
suppression IS. So the physical state of every key in the binding table is
unreadable by construction, because every bound key is a suppressed key.

What replaced the poll is a TIMEOUT (`deskd.Agent.check_stuck`), which needs no
key state at all and cannot fire on a capture that is still running.

WHY F13 AND NOT NUMPAD `*`
--------------------------
The owner's desk agent may be running with a real hook on its own bindings.
Injecting a key THEY have bound would start their dictation. F13 is bare-safe,
nothing on this machine produces it, and an agent whose table does not name it
passes it straight through to this probe's hook.

It suppresses what it injects, so nothing on the desktop receives the keystroke,
and it uninstalls its hook in a `finally`.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_asyncstate.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from desk import actions, hotkeys, paste as paste_mod       # noqa: E402

F13 = actions.KEYS["f13"].vk
SAMPLES = 5


def key_down(vk: int) -> bool:
    """What `Agent.check_stuck` used to ask.

    Deliberately NOT a function in `desk/hotkeys.py`. It lived there for one
    commit and it cannot answer honestly for any key this project binds, so it
    belongs in the probe that shows why rather than in the module a caller
    reaches for.
    """
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetAsyncKeyState.restype = ctypes.c_short
    return bool(u32.GetAsyncKeyState(vk) & 0x8000)


def measure(suppress: bool) -> dict:
    table = actions.Table([{"chord": "f13", "action": "dictate"}])
    hook = hotkeys.Hook(table)
    original = hotkeys.classify
    if not suppress:
        # The ONE variable. The callback still classifies and still enqueues; it
        # just does not swallow. Anything else changed here would make the two
        # rows measure two different things.
        hotkeys.classify = lambda t, h, vk, wp, m: (
            original(t, h, vk, wp, m)[0], False)
    try:
        if not hook.start():
            raise SystemExit(f"the hook would not install: {hook.error}")
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        # `mark=0`, so the hook treats this as a FINGER. Every keystroke this
        # process injects normally carries `hotkeys.MARK` and is passed through.
        paste_mod._send(u32, (F13, False), mark=0)
        time.sleep(0.15)
        seen = [key_down(F13) for _ in range(SAMPLES)]
        edges = []
        while not hook.events.empty():
            _binding, edge = hook.events.get_nowait()
            edges.append(edge)
        return {"key_down": seen, "edges": edges}
    finally:
        # The key-up ALWAYS goes out, even on the way out of a failure: an
        # injected key-down nobody released leaves the operator's keyboard stuck
        # in a key they never pressed.
        paste_mod._send(ctypes.WinDLL("user32", use_last_error=True),
                        (F13, True), mark=0)
        hook.stop()
        hotkeys.classify = original


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Windows only")

    suppressed = measure(True)
    time.sleep(0.4)
    passed = measure(False)

    print(f"hook SUPPRESSES  the key -> key_down {suppressed['key_down']}, "
          f"edges {suppressed['edges']}")
    print(f"hook PASSES THRU the key -> key_down {passed['key_down']}, "
          f"edges {passed['edges']}")

    # Both rows must have SEEN the key, or the probe measured nothing at all --
    # a hook that never fired would report False/False and look like a
    # confirmation. That is the "one wrong answer ruled out is not an
    # instrument" rule (test/AGENTS.md).
    fired = bool(suppressed["edges"]) and bool(passed["edges"])
    unreadable = not any(suppressed["key_down"])
    readable = all(passed["key_down"])
    print(f"\nVERDICT the hook saw the key in BOTH runs: {fired}")
    print(f"VERDICT a SUPPRESSED key reads as UP while held: {unreadable}")
    print(f"VERDICT a PASSED-THROUGH key reads as DOWN while held: {readable}")
    print("\nSo `check_stuck` may never ask for a key state: every key it "
          "waits on is a key this agent suppresses.")
    return 0 if (fired and unreadable and readable) else 1


if __name__ == "__main__":
    sys.exit(main())
