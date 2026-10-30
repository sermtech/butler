"""The voice trace: one line per stage of a turn, into `voice-trace.log` in
the logs directory `paths.resolve()` names (`state/` on the legacy layout).

WHY THIS EXISTS. Until 2026-09-10 the turn path had no logging at all. The
daemon printed wake candidates and exceptions, and nothing else -- so "Butler
heard me and never answered" was indistinguishable from a dozen causes: an
end phrase the recogniser never produced, a pause that arrived before the
transcript caught up, a harness child that died, a recogniser still busy with
a turn nobody ended. Diagnosing one of those cost a browser harness sampling
the HUD once a second, and the HUD is live-only: it vanishes on reload and
cannot be copied off a phone.

The shape is taken from an earlier prototype's `voiceLog.ts`, which was written for this
exact problem and earned its keep: with it they could say "17 discarded
pauses; one turn in nine never closed; median 18.7 s of wasted wall-clock per
turn" about an intermittent bug. That sentence is a diagnosis. Without an
instrument there is no such sentence, only a report that something is wrong
sometimes.

FOUR PROPERTIES, and each one is a decision rather than a detail.

It is in the DAEMON and not the page. An earlier prototype's ring lives in the browser and
is POSTed back, because their capture path is the browser's. Butler's turn
decisions are all daemon-side (`web/AGENTS.md`: "The page does not decide when
a turn ends"), so tracing the page would trace the wrong half. A daemon file
also survives a reload and needs no console on the device that failed.

It carries `dt`, not just elapsed. `+123456ms` locates a line in the run;
`dt=+612ms` is what finds the GAP, which is the question every voice bug
asks. An earlier prototype's header says the same thing about its relative stamps.

It is ALWAYS ON. An intermittent fault you have to ask the owner to reproduce
with logging enabled is one you will not catch. The cost is a few dozen short
appends per turn; the file rotates at `MAX_BYTES` so it cannot grow without
limit. `BUTLER_TRACE=0` turns it off for anyone who wants that.

It CANNOT break a turn (D-1). Every call is wrapped: a full disk, a locked
file or a bad value costs a trace line and never the microphone. It writes to
a FILE and never to the console, because a Windows console write blocks
indefinitely while the operator has text selected, and this is called from the
audio path.
"""
import os
import threading
import time
from pathlib import Path

from daemon import paths

#: Rotate at 2 MB, keeping one previous file. A turn costs a few hundred
#: bytes, so this is thousands of turns -- long enough to still hold the
#: evening a fault was noticed, small enough to paste the tail of.
MAX_BYTES = 2_000_000

_lock = threading.Lock()
_fh = None
_origin = 0.0
_prev = 0.0
_opened_path: Path | None = None


def path() -> Path:
    """Where the trace goes. Resolved like every other record path
    (`paths.resolve`, docs/data-root.md), so a scratch daemon with its own
    `BUTLER_STATE` writes its own trace and cannot interleave lines into the
    owner's. Unchecked: this runs per line and must never raise (D-1)."""
    return paths.resolve(check=False).logs / "voice-trace.log"


def enabled() -> bool:
    return os.environ.get("BUTLER_TRACE", "1") not in ("0", "false", "off")


def _rotate_locked(target: Path) -> None:
    """Keep one previous file. Called with the lock held and never raising."""
    global _fh
    if _fh is not None:
        _fh.close()
        _fh = None
    try:
        target.replace(target.with_suffix(".log.1"))
    except OSError:
        pass


def _ensure_locked() -> None:
    """Open the handle if it is not open. Called with the lock held."""
    global _fh, _origin, _prev, _opened_path
    target = path()
    if _fh is not None and _opened_path == target:
        if target.exists() and target.stat().st_size > MAX_BYTES:
            _rotate_locked(target)
        else:
            return
    if _fh is not None:
        _fh.close()
        _fh = None
    target.parent.mkdir(parents=True, exist_ok=True)
    _fh = open(target, "a", encoding="utf-8", buffering=1)
    _opened_path = target
    if not _origin:
        _origin = _prev = time.monotonic()
    # A header per open, so the relative stamps below can be turned back into
    # wall-clock time and two runs in one file are told apart.
    _fh.write(f"\n--- voice trace opened {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"pid {os.getpid()} ---\n")


def trace(stage: str, **detail) -> None:
    """One line. Never raises, never blocks on a console (D-1).

    `detail` is rendered shallowly -- keep it to primitives, because this is
    called from the audio path and from a worker thread.
    """
    if not enabled():
        return
    try:
        global _prev
        with _lock:
            # The clock is read INSIDE the lock and AFTER the open, because
            # `_ensure_locked` is what sets the origin on the very first line:
            # reading it before produced a `now` fractionally EARLIER than the
            # origin, and the first line of every file said `+-0ms dt=+-0ms`.
            _ensure_locked()
            if _fh is None:
                return
            now = time.monotonic()
            since = (now - _origin) * 1000
            gap = (now - _prev) * 1000
            _prev = now
            parts = " ".join(f"{k}={v}" for k, v in detail.items() if v != "")
            _fh.write(f"+{since:.0f}ms dt=+{gap:.0f}ms {stage}"
                      f"{' ' + parts if parts else ''}\n")
    except Exception:                                   # noqa: BLE001
        # A trace line is never worth a turn. Not even a print: this runs on
        # the audio path, and a blocked console write is a dead microphone.
        pass


def tail(text: str, n: int = 60) -> str:
    """The LAST `n` characters of a transcript, whitespace collapsed.

    The tail and not the head, because that is where the decisions are made:
    `EndWordEnd` anchors its match to the end of the transcript, so "did the
    end phrase actually arrive" is a question only the tail can answer.

    Less than `state/chats/` already keeps -- the chat record holds every
    utterance in full -- so this adds no new class of stored content. It is
    still the owner's own words on the owner's own disk, and it is the one
    field here that could be dropped if that ever stops being true.
    """
    flat = " ".join(str(text or "").split())
    return repr(flat[-n:] if len(flat) > n else flat)


def close() -> None:
    """Release the handle, for a restart that re-execs this process."""
    global _fh, _opened_path
    try:
        with _lock:
            if _fh is not None:
                _fh.close()
            _fh = None
            _opened_path = None
    except Exception:                                   # noqa: BLE001
        pass
