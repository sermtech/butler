"""The daemon's half of the desk agent: spawn it, own it, stop it.

`docs/desk-hotkeys.md` D1, AC-121, AC-122. The agent itself is `desk/deskd.py`
and runs as its own process; this module is the supervisor and holds no
policy beyond "the `hotkeys_enabled` setting decides".

The pattern is the kokoro sidecar's, one seam over: a child spawned by this
daemon, assigned to the Job Object IMMEDIATELY, and stopped explicitly in
`butlerd._restart_cleanup` before `os.execv`. The arrow is reversed -- the
sidecar is called BY the daemon and this one CALLS the daemon -- but the
lifetime rules are identical and were paid for in the same place (AC-36,
D-24).

WHY A MODULE SINGLETON RATHER THAN SOMETHING ON THE HUB
-------------------------------------------------------
The agent is not a port, not an engine and not a store: nothing in a turn
touches it, and the Hub would carry it only so `_restart_cleanup` could find
it. `childproc.py` holds the job handle exactly this way and for the same
reason -- there is one per process, its lifetime is the process's, and giving
it an owner would mean threading it through constructors that have no other
use for it.

LOUD, NEVER FATAL (D-1)
-----------------------
Every failure here is reported and swallowed. A desk agent that will not
start costs the owner their hotkeys; a daemon that refuses to boot over it
costs them the microphone, and Butler with no microphone is worse than Butler
with no hotkeys.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path



from . import childproc

REPO = Path(__file__).resolve().parent.parent
DESKD = REPO / "desk" / "deskd.py"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def pid() -> int | None:
    """The running agent's PID, or None. Never a stale one."""
    with _lock:
        return None if _proc is None or _proc.poll() is not None else _proc.pid


def running() -> bool:
    return pid() is not None


def start(url: str) -> bool:
    """Spawn the agent against `url`, unless one is already running.

    Answers whether an agent is running when this returns -- so a second call
    while one is up is success, not a second process. Two live hooks on one
    key is not twice as good: both fire, and the second one's capture races
    the first for the microphone.
    """
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return True
        if not DESKD.exists():
            print(f"[butler] WARNING: the desk agent script is missing "
                  f"({DESKD}); hotkeys are on and nothing will listen for "
                  f"them", flush=True)
            return False
        try:
            # INHERITS this daemon's stdout and stderr rather than discarding
            # them. The agent's own lines -- "hotkeys ON", the input-desktop
            # refusal, every "no text (409)" -- are the only evidence there is
            # when the owner says the hotkey does nothing, and a feature whose
            # commonest failure is silence must not have its diagnostics sent
            # to DEVNULL, which is what this did until 2026-09-09.
            _proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(DESKD), "--url", url],
                cwd=str(REPO),
            )
        except OSError as e:
            print(f"[butler] WARNING: the desk agent would not start: {e!r}",
                  flush=True)
            _proc = None
            return False
        # Before anything else. Descendants inherit job membership at
        # creation, so anything the agent spawns in the microseconds before
        # this lands is outside the job (childproc.assign's own warning).
        childproc.assign(_proc)
        print(f"[butler] desk agent up, pid {_proc.pid}, pointed at {url}",
              flush=True)
        return True


def start_if_enabled(settings, url: str) -> bool:
    """Start the agent only if the owner has asked for hotkeys (AC-121).

    OFF means NOT SPAWNED rather than spawned-and-idle. An agent running with
    its hook uninstalled is one code path away from installing it, and it is
    invisible to anyone who did not put it there.
    """
    if settings is None or not settings.get("hotkeys_enabled", False):
        return False
    return start(url)


def stop() -> None:
    """Kill the agent and anything under it. Idempotent, never raises.

    `childproc.kill_tree` rather than `terminate()`, the sidecar's own reasoning:
    the tree is what has to go, and nothing in the child runs on the way out
    anyway. Called from `butlerd._restart_cleanup` BEFORE `os.execv`, because
    `execv` keeps this process's handles -- so the Job Object survives into
    the new image and carries the old agent with it, and the new image then
    spawns a second one. Every orphan in that pile is still suppressing the
    owner's key.
    """
    global _proc
    with _lock:
        proc, _proc = _proc, None
    if proc is None or proc.poll() is not None:
        return
    try:
        childproc.kill_tree(proc.pid)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[butler] WARNING: the desk agent would not stop: {e!r}",
              flush=True)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
