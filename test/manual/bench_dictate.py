"""What does a dictation cost at the ceiling, through the PORT?

`docs/desk-hotkeys.md` section 6 shipped `dictate_max_seconds` at 120 with a
cost EXTRAPOLATED from a 60 s measurement, and its own build order says that
must be measured before the ceiling ships. This is that measurement.

WHY NOT `spike_ambient_offline.py`, WHICH ALREADY MEASURED `run()`
------------------------------------------------------------------
That one times `tc.Session.run()` on float32 audio, which is the ENGINE. The
route pays for the PORT: `Asr.transcribe` takes the same int16 bytes `feed`
does, converts to float32 inside the adapter, and hops to a worker thread
(`daemon/adapters/asr_transcribecpp.py`). Those three things are the
difference between what the engine can do and what a held key actually costs,
and only one of them is in the other spike.

It also answers a second question the other one cannot: whether the ceiling is
reachable at all. `Session.limits.effective_max_audio_ms` is 0 for parakeet
and the engine's truncation mode is an `OutputTruncated` EXCEPTION carrying
the partial rather than a short string (`daemon/AGENTS.md`), so a clip that is
too long fails loudly -- but nobody has run 120 s through this path.

THE TRAP THIS INHERITS
----------------------
`Session.run()` takes FLOAT32 and answers `text=''` for int16 -- silently,
fast, and confidently. The `chars` column below is the guard: if it is flat as
the audio grows, the instrument is lying rather than the decoder being quick.
Assert on the transcript, never on the timing alone.

NOT collected by pytest (`manual/` is excluded in pytest.ini). It loads the
real engine and takes a few minutes.

Run:  .venv\\Scripts\\python.exe -X utf8 test\\manual\\bench_dictate.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from daemon import ports, settings as settings_mod     # noqa: E402
from daemon.adapters import asr_transcribecpp          # noqa: E402

SR = ports.SAMPLE_RATE_IN
MODEL = "parakeet-unified-en-0.6b"
# The ceiling itself is read from the schema rather than written here, so this
# measures what the route will actually allow rather than what this file
# happened to be written with.
CEILING_S = int(settings_mod.BY_KEY["dictate_max_seconds"].maximum)
LENGTHS_S = (5, 30, 60, CEILING_S)
# `docs/ambient.md` / `.references/ambient-2026.md` R1, the ENGINE path on
# this machine, so the two columns can be read side by side.
ENGINE_RUN_S = {5: 0.28, 20: 1.27, 30: 1.94, 60: 3.93}


def _pool() -> bytes:
    """Every fixture concatenated, as the int16 bytes the PORT takes."""
    raw = bytearray()
    for name in ("long_utterance.wav", "two_sentences.wav",
                 "wake_then_command.wav", "short_question.wav"):
        with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
            if w.getframerate() != SR or w.getnchannels() != 1:
                raise SystemExit(f"{name} is not 16 kHz mono")
            raw += w.readframes(w.getnframes())
    return bytes(raw)


async def main() -> int:
    pool = _pool()
    seconds = len(pool) / (SR * 2)
    print(f"fixture pool: {seconds:.1f} s of real speech, int16 bytes")
    tiled = pool * (int(CEILING_S / seconds) + 2)

    asr = ports.build(ports.ASRS, "transcribecpp", backend="cpu")
    cfg = ports.AsrConfig(model=MODEL)
    print("warming the engine (excluded from the timings below)...")
    t0 = time.perf_counter()
    await asr.warm()
    print(f"warm: {time.perf_counter() - t0:.1f} s")

    print(f"\n{'audio':>7} {'transcribe()':>13} {'xRT':>7} {'chars':>7}   "
          f"engine run() [MEASURED 2026-08-19]")
    rows = []
    for n in LENGTHS_S:
        pcm = tiled[:n * SR * 2]
        t0 = time.perf_counter()
        try:
            text = await asr.transcribe(pcm, cfg)
        except Exception as e:                            # noqa: BLE001
            print(f"{n:>6}s  RAISED {e!r}")
            rows.append((n, None, 0))
            continue
        dt = time.perf_counter() - t0
        was = ENGINE_RUN_S.get(n)
        note = (f"{was:>6.2f}s  (+{dt - was:>5.2f}s of port)" if was
                else "   (not measured at this length)")
        print(f"{n:>6}s {dt:>12.2f}s {n / dt:>6.1f}x {len(text):>7}   {note}")
        rows.append((n, dt, len(text)))

    chars = [c for _n, dt, c in rows if dt is not None]
    if len(set(chars)) == 1 and len(chars) > 1:
        print("\nWARNING: the chars column is FLAT. The decoder is returning "
              "the same amount of text at every length, which is what an "
              "empty or truncated transcript looks like. Do not trust the "
              "timings above.")

    ceiling = next((dt for n, dt, _c in rows if n == CEILING_S), None)
    print(f"\nthe ceiling is {CEILING_S} s; a full-length dictation costs "
          f"{'RAISED' if ceiling is None else f'{ceiling:.2f} s'} "
          f"before the text appears.")
    print("docs/desk-hotkeys.md section 6 carries an EXTRAPOLATION of about "
          "8 s. Replace it with the number above, and say which it is.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
