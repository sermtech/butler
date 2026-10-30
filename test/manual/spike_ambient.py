"""Earshot: what does N seconds of back-fill audio cost to decode HERE?

The whole ambient design turns on one number that no published benchmark can
answer for this machine. transcribe.cpp advertises 7-9x real time for
`parakeet-unified-en-0.6b` on CPU but does not state its thread count, and
Butler runs the CPU backend precisely because the GPU is contended with
llama-swap. The measured answer on this box is 2-3x, not 7-9x, and it
DEGRADES with utterance length -- which is what killed the first design.

Feeds a real `AsrSession` exactly the way `TurnPipeline` does (32 ms frames,
then `finalize` off the event loop) and reports wall time. Speech comes from
the recorded fixtures tiled to length, so the decoder does real work rather
than skipping silence.

Two shapes, because the difference between them IS the design:
  A: one long utterance -- what a naive long pre-roll produces.
  B: the same audio as a series of short utterances on ONE session, which is
     what a continuously-decoded rolling buffer produces. `AsrSession`
     already supports this (`asr_transcribecpp.py` restarts the stream after
     a Final).

NOT collected by pytest (`manual/` is excluded in pytest.ini). It loads the
real engine and takes about a minute.

Run:  .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_ambient.py

[EXECUTED 2026-08-19 -- results in docs/ambient.md section 1.]
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BUTLER_TLS", "0")

from daemon import ports                                    # noqa: E402
from daemon.adapters import asr_transcribecpp               # noqa: E402,F401

FRAME_BYTES = 512 * 2          # the worklet's 32 ms frame at 16 kHz int16
BYTES_PER_S = 16000 * 2
SINGLE_LENGTHS_S = (1, 5, 10, 20, 30)
CHUNK_SIZES_S = (10, 5, 3)
TOTAL_S = 30


def _speech() -> bytes:
    """Every fixture concatenated: real speech, not a tone."""
    out = bytearray()
    for name in ("long_utterance.wav", "two_sentences.wav",
                 "wake_then_command.wav", "short_question.wav"):
        with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1:
                raise SystemExit(f"{name} is not 16 kHz mono")
            out += w.readframes(w.getnframes())
    return bytes(out)


def _tiled(seconds: float) -> bytes:
    want = int(seconds * BYTES_PER_S)
    base = _speech()
    return (base * ((want // len(base)) + 1))[:want]


async def _decode(session, pcm: bytes) -> None:
    for i in range(0, len(pcm), FRAME_BYTES):
        session.feed(pcm[i:i + FRAME_BYTES])
    # Off the loop, like `pipeline._end_turn`: `finalize` blocks.
    await asyncio.to_thread(session.finalize)


async def _run(asr, cfg, pcm: bytes, chunk_s: float | None) -> tuple:
    finals: list[str] = []
    session = asr.open(cfg, on_partial=lambda t: None,
                       on_final=lambda f: finals.append(f.text))
    try:
        started = time.perf_counter()
        if chunk_s is None:
            await _decode(session, pcm)
        else:
            step = int(chunk_s * BYTES_PER_S)
            for off in range(0, len(pcm), step):
                await _decode(session, pcm[off:off + step])
        elapsed = time.perf_counter() - started
    finally:
        session.close()
    return elapsed, len(" ".join(finals).strip())


async def main() -> int:
    asr = ports.build(ports.ASRS, "transcribecpp", backend="cpu")
    cfg = ports.AsrConfig(model="parakeet-unified-en-0.6b")
    started = time.perf_counter()
    await asr.warm()
    print(f"warm {time.perf_counter() - started:.2f} s; backend="
          f"{asr.info.get('backend')}  model={asr.info.get('slug')}\n")

    print("A) ONE utterance of N seconds -- a naive long pre-roll")
    print(f"{'audio_s':>8} {'decode_s':>9} {'xRT':>7} {'chars':>6}")
    print("-" * 34)
    for seconds in SINGLE_LENGTHS_S:
        elapsed, chars = await _run(asr, cfg, _tiled(seconds), None)
        print(f"{seconds:8.0f} {elapsed:9.2f} {seconds / elapsed:7.1f} "
              f"{chars:6d}")

    print(f"\nB) {TOTAL_S} s as short utterances -- a rolling decode")
    print(f"{'chunk_s':>8} {'decode_s':>9} {'xRT':>7} {'chars':>6}")
    print("-" * 34)
    pcm = _tiled(TOTAL_S)
    for chunk_s in CHUNK_SIZES_S:
        elapsed, chars = await _run(asr, cfg, pcm, chunk_s)
        print(f"{chunk_s:8.0f} {elapsed:9.2f} {TOTAL_S / elapsed:7.1f} "
              f"{chars:6d}")

    print("\nZERO chars means the adapter's finalize budget fired and the "
          "turn ended empty. That is a real ceiling, not a slow result.")
    print("The budget SCALES with the audio fed since D-26 was partly fixed "
          "(asr_transcribecpp.finalize_budget_s), so the length at which this "
          "happens moved -- re-read that constant before trusting an old "
          "number here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
