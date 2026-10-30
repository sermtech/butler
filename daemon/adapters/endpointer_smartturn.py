"""Smart Turn v3.2 behind the endpointer callable. plan.md 4.2, D-5, D-10.

This is NOT a port. plan.md 4.10 caps the seams at five -- Source, Asr, Tts,
Brain, Harness -- and an end-of-turn model is not a seam anything else plugs
into:
it is one async callable `endpointer(pcm) -> P(turn complete)` that the Hub
holds and hands to `endpointing.SmartTurnEnd`. It lives in adapters/ because
it is the only place a concrete engine is named outside config/, and it is
adapter-shaped (construct, `warm()` at boot, fail loudly) so it reads like its
neighbours -- but it is deliberately absent from ports.py's registries.

Three properties are the whole reason this file exists rather than a lambda:

1. **It never runs on the event loop.** The work is numpy feature extraction
   plus ONNX inference; the daemon is single-threaded asyncio, so doing it
   inline is not one satellite waiting, it is every satellite, the heartbeat
   and the worker announcements all stopped. That exact defect was found in
   the recognizer's finalize() and fixed with `asyncio.to_thread`; the same
   rule applies here, except a THREAD is not enough. `turn_engine.get_pool()`
   documents why: in-process, feature extraction degrades from ~30 ms to
   ~2200 ms because the GIL is shared with transcribe.cpp's worker, while a
   BLAS-capped child stays at ~33 ms [MEASURED-SPIKE, plan.md Appendix A].
   So every score goes through `loop.run_in_executor(pool, ...)`.

2. **Model load has its own budget, separate from the per-inference
   timeout** (D-5). `warm()` gets LOAD_TIMEOUT_S; a single inference gets
   `endpointing.ENDPOINT_TIMEOUT_S` (2 s), enforced by the policy that calls
   it. One shared watchdog killing a worker that was merely still loading is
   the recorded cascade-to-silence defect.

3. **It fails loudly.** A missing ONNX file or a child that will not start is
   reported at boot, by butlerd, before any turn happens. Never a silent
   degrade to patient silence while the setting still says "smart".

`daemon/engines/turn_engine.py` is the forked, working spike wrapper and is
NOT modified here: this file only wraps it.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

from .. import endpointing

# engines/ is a directory of standalone modules, not a package (no
# __init__.py) -- same sys.path dance as the transcribe.cpp adapter, and for
# the same reason: the engine files stay directly runnable by the scripts in
# test/manual/.
_ENGINES = Path(__file__).resolve().parents[1] / "engines"

# Cold start budget, SEPARATE from the per-inference timeout (D-5). Generous:
# it covers spawning a Windows child process, importing numpy and onnxruntime
# there, and loading the 8 MB int8 graph. Measured cold start on this machine
# is well under a second of actual model load; the rest is process start.
LOAD_TIMEOUT_S = 60.0

# 16 kHz mono int16, fixed across every port (ports.SAMPLE_RATE_IN).
BYTES_PER_MS = 32


def _exit_when_the_parent_does() -> str:
    """Runs IN THE CHILD, once, at warm time. Module level so it is picklable.

    MEASURED 2026-08-16, and the reason this function exists: killing the
    daemon does NOT kill the scoring child. `ProcessPoolExecutor` shuts its
    workers down through an atexit hook, and nothing on Windows runs atexit
    for a TerminateProcess -- which is what `subprocess.terminate()` does, and
    that is how the test harness stops every daemon it boots. The child then
    blocks forever reading a call queue it also holds the write end of, so it
    never sees EOF either. Seven orphans, 46-100 MB each with onnxruntime
    loaded, accumulated in one afternoon of test runs.

    `parent_process().join()` waits on the parent's process handle, so this
    costs one sleeping thread and no polling, and it cannot fire early.
    """
    import multiprocessing
    import os
    import threading

    parent = multiprocessing.parent_process()
    if parent is None:
        return "no parent to watch"

    def _watch():
        parent.join()
        os._exit(0)

    threading.Thread(target=_watch, daemon=True,
                     name="butler-parent-watch").start()
    return f"watching parent {parent.ident}"


def _turn_engine():
    """Import the engine lazily.

    Importing it costs numpy at parse time and (in the CHILD) onnxruntime;
    registration-by-import in adapters/__init__.py must stay cheap, and a
    daemon that never uses the smart mode should never pay for it.
    """
    if str(_ENGINES) not in sys.path:
        sys.path.insert(0, str(_ENGINES))
    import turn_engine  # noqa: PLC0415  (deliberately lazy, see docstring)
    return turn_engine


class SmartTurnEndpointer:
    """P(the speaker has finished) for the utterance audio handed to it.

    Callable, because that is the entire contract `SmartTurnEnd` needs:

        endpointer = SmartTurnEndpointer()
        await endpointer.warm()             # at boot, once (D-10)
        p = await endpointer(pcm)           # 16 kHz mono int16 bytes

    The caller decides WHICH bytes: this scores exactly what it is given.
    Anchoring the window at speech onset is the policy's job, and it matters
    -- see `endpointing.PRE_SPEECH_LEAD_MS`.
    """

    name = "smartturn"

    def __init__(self) -> None:
        self._warm = False
        # One inference at a time. The pool has exactly one worker, so a
        # second concurrent call would queue INSIDE the child and the policy's
        # 2 s timeout would then be measured against a queue rather than
        # against the model. Serializing here means a call that gives up gives
        # up before it is submitted, and the child never accumulates a backlog
        # of verdicts for a turn that has already ended.
        self._lock = asyncio.Lock()
        # Last measurement, for the boot log and for tests. Not a metric
        # system: three numbers that make a slow turn diagnosable.
        self.last: dict | None = None

    @property
    def model_path(self) -> Path:
        return _turn_engine().MODEL_PATH

    @property
    def warmed(self) -> bool:
        return self._warm

    async def warm(self) -> float:
        """Start the child and load the model NOW. Returns milliseconds.

        D-10: the spike scored a zero buffer at boot for exactly this reason.
        Without it the first end-of-turn check of the first conversation pays
        a process spawn plus a model load inside the policy's 2 s timeout,
        blows it, and the turn ends on the silence cap instead -- smart mode
        that behaves like patient mode on the first thing you ever say.

        Raises rather than returning a flag. butlerd turns that into either a
        refusal to boot (the setting says smart) or a loud warning (it does
        not); neither is a silent fallback.
        """
        te = _turn_engine()
        # The window length the policy anchors against is a claim about THIS
        # engine, so it is checked rather than commented (D-11). If the engine
        # ever moves to a different smart-turn revision with a different
        # window, the anchoring arithmetic in endpointing.py is wrong and this
        # is where that gets noticed.
        if te.CHUNK_SECONDS * 1000 != endpointing.SMART_WINDOW_MS:
            raise ValueError(
                f"turn_engine takes a {te.CHUNK_SECONDS}s window but "
                f"endpointing.SMART_WINDOW_MS says "
                f"{endpointing.SMART_WINDOW_MS} ms")
        # Checked HERE, in the parent, so a missing model is a clear sentence
        # instead of a BrokenProcessPool from a child that died on import.
        if not te.MODEL_PATH.exists():
            raise FileNotFoundError(
                f"smart turn model missing: {te.MODEL_PATH} -- the 'smart' "
                f"turn mode cannot run without it (see README, Appendix B)")

        t0 = time.perf_counter()
        # Building the pool and submitting the first item both do real work on
        # the calling thread (Windows spawn pickles the parent's state and
        # calls CreateProcess), so even the boot path stays off the loop.
        pool = await asyncio.to_thread(te.get_pool)
        await self._watch_parent(pool)
        fut = await asyncio.to_thread(
            pool.submit, te._score_worker,
            np.zeros(te.SAMPLE_RATE, dtype=np.float32))
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=LOAD_TIMEOUT_S)
        self._warm = True
        return (time.perf_counter() - t0) * 1000

    async def _watch_parent(self, pool) -> None:
        """Ask the child to die when this process does. Never fatal.

        Housekeeping, not endpointing: if it fails, the daemon still scores
        turns correctly and the only cost is an orphan after a hard kill. So
        this one warns rather than refusing to boot -- but it warns, because
        the symptom otherwise is a machine that quietly fills up with 100 MB
        onnxruntime processes.
        """
        try:
            fut = await asyncio.to_thread(pool.submit, _exit_when_the_parent_does)
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=LOAD_TIMEOUT_S)
        except Exception as e:                        # noqa: BLE001
            print(f"[butler] WARNING: the smart-turn child will not shut down "
                  f"with the daemon ({e!r}); a hard kill will leave it running")

    async def score(self, pcm: bytes) -> float:
        """P(turn complete) in [0, 1] for `pcm` (16 kHz mono int16).

        The model reads the LAST 8 s of what it is given and zero-pads at the
        FRONT, so a longer buffer is trimmed for us -- but the trim is blind,
        which is why the caller anchors the window instead of relying on it.
        """
        if not pcm:
            # No audio is not a verdict. Returning 0.0 says "no evidence the
            # turn is over", which is the truthful answer and the safe
            # direction: the silence caps in pipeline.py still end the turn.
            return 0.0
        te = _turn_engine()
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        async with self._lock:
            p, feature_ms, inference_ms = await loop.run_in_executor(
                te.get_pool(), te._score_worker, audio)
        self.last = {
            "p": round(float(p), 4),
            "audio_ms": round(len(pcm) / BYTES_PER_MS),
            "feature_ms": round(feature_ms, 1),
            "inference_ms": round(inference_ms, 1),
            "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        return float(p)

    async def __call__(self, pcm: bytes) -> float:
        return await self.score(pcm)
