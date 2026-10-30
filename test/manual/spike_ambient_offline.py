"""Ambient: what does N seconds cost through the OFFLINE decode path?

Sibling of `spike_ambient.py`, which measured the STREAMING path and produced
`docs/ambient.md` table A. This one measures `Session.run()` -- the same
model, the same audio, the same machine -- and the difference between the two
is the whole of `.references/ambient-2026.md` R1.

Why it exists: `daemon/engines/stt_engine.py` only ever calls
`Session.stream()`, and `catalog.json` maps `parakeet-unified-en-0.6b` to
`stream_kind: "buffered"`, whose sliding `[left|chunk|right]` window re-runs
the encoder over each second of audio many times. `Session.run()` is on the
same loaded session, takes the whole clip once, and had never been called
anywhere in this repository.

TWO TRAPS, both hit while writing this, both worth more than the numbers:

  1. `run()` takes FLOAT32 PCM, not the int16 bytes the rest of the audio
     path carries (`Session.run` docstring: "Transcribe 16 kHz mono float32
     PCM"). Fed int16 it returns `text=''` -- fast, confident and empty.
  2. `Result` is a dataclass whose `repr()` is ~214 characters of timings
     regardless of what was transcribed. Measuring `len(str(result))` gives a
     flat "213 chars" at every length and reads exactly like a working
     decoder. Assert on `result.text`.

Together those two produced a 16x speedup on an empty transcript, which is
this project's recurring failure -- a number measuring the wrong quantity --
in a new place. The `chars` column is in the output so the reader can see the
transcript growing with the audio; if it is flat, the instrument is lying.

NOT collected by pytest (`manual/` is excluded in pytest.ini). It loads the
real engine on the CPU backend and takes about half a minute.

Run:  .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_ambient_offline.py

[EXECUTED 2026-08-19 -- results in .references/ambient-2026.md section 9 R1.]
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import transcribe_cpp as tc                       # noqa: E402
from daemon.engines import stt_engine as se       # noqa: E402

SR = 16000
LENGTHS_S = (1, 5, 10, 20, 30, 60)
# docs/ambient.md table A, the streaming path on this machine, so the two
# columns can be read side by side rather than from two documents.
STREAMING_S = {1: 0.09, 5: 1.69, 10: 5.58, 20: 13.64, 30: 15.01}


def _speech() -> np.ndarray:
    """Every fixture concatenated, as float32 in [-1, 1)."""
    raw = bytearray()
    for name in ("long_utterance.wav", "two_sentences.wav",
                 "wake_then_command.wav", "short_question.wav"):
        with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
            if w.getframerate() != SR or w.getnchannels() != 1:
                raise SystemExit(f"{name} is not 16 kHz mono")
            raw += w.readframes(w.getnframes())
    return np.frombuffer(bytes(raw), dtype=np.int16).astype(np.float32) / 32768.0


def main() -> int:
    pool = _speech()
    print(f"fixture pool: {len(pool) / SR:.1f} s of real speech (float32)")

    path = se.gguf_path("parakeet-unified-en-0.6b")
    session = tc.Model(str(path), backend="cpu").session()
    session.run(pool[:SR], language="en")          # warm; excluded from timings

    tiled = np.tile(pool, 12)
    print(f"\n{'audio':>6} {'run()':>8} {'xRT':>7} {'chars':>6}   "
          f"streaming (docs/ambient.md table A)")
    for n in LENGTHS_S:
        pcm = tiled[:n * SR]
        t0 = time.perf_counter()
        result = session.run(pcm, language="en")
        dt = time.perf_counter() - t0
        was = STREAMING_S.get(n)
        note = (f"{was:>6.2f}s -> {was / dt:>5.1f}x cheaper" if was
                else "  (not in table A)")
        print(f"{n:>5}s {dt:>7.2f}s {n / dt:>6.1f}x "
              f"{len(result.text):>6}   {note}")

    print("\n30 s transcript, so the reader can see it is real:")
    print(repr(session.run(tiled[:30 * SR], language="en").text[:200]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
