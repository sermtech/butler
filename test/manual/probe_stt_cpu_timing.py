"""MEASUREMENT ONLY. Loads TWO real 0.6B GGUF recognizers on the CPU backend
and decodes real audio through the REAL Asr adapter
(daemon/adapters/asr_transcribecpp.py). CPU-HEAVY: this pins a CPU core for
tens of seconds per run and should not be run alongside other CPU-heavy work.

Answers one question: on THIS machine, CPU only, with the owner's own
settings (asr_model=nemotron-3.5-asr-streaming-0.6b, asr_backend=cpu,
asr_latency=accurate), how long from "the last audio frame fed" to a `Final`
being delivered? Also measures parakeet-unified-en-0.6b/cpu as the comparison
point named in the task, and the one-time cold GGUF load for each.

It drives `TranscribeCppAsr.open()` / `.feed()` / `.finalize()` directly --
the same three calls `daemon/butlerd.py` makes -- with NO daemon, NO socket,
NO event loop, and NOTHING sent to the owner's live daemon on port 8140. It
never touches `state/`.

Audio is fed PACED to real time (a sleep between chunks matching the chunk's
own duration), because the adapter's `feed()` only ENQUEUES onto a background
worker thread and returns immediately -- feeding a whole clip in a tight loop
would tell you nothing about whether the decoder can keep up with a live
microphone, only how fast the queue accepts bytes. The reported number is the
wall-clock time from calling `finalize()` (issued immediately after the last
paced chunk, simulating "the speaker just stopped") to `finalize()` RETURNING,
which by the port's own contract does not happen until the `Final` has been
delivered.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_stt_cpu_timing.py
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from daemon.adapters.asr_transcribecpp import TranscribeCppAsr  # noqa: E402
from daemon.ports import AsrConfig  # noqa: E402

ASSETS = REPO / "test" / "assets"
CHUNK_MS = 100          # feed pacing: a live capture delivers small blocks
BYTES_PER_MS = 32       # 16 kHz mono int16


def load_pcm(name: str) -> bytes:
    with wave.open(str(ASSETS / name), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 \
            and w.getsampwidth() == 2, name
        return w.readframes(w.getnframes())


def duplicate_to(pcm: bytes, target_s: float) -> bytes:
    """Synthesize a longer clip by repeating real speech back to back.

    No fixture here reaches 10 s (the longest is long_utterance.wav at
    5.19 s), so this concatenates it with itself until the target duration is
    reached. This is REAL speech, repeated -- not a substitute for a genuine
    10 s continuous utterance, and the report says so."""
    step = len(pcm)
    target_bytes = int(target_s * 16000 * 2)
    out = bytearray()
    while len(out) < target_bytes:
        out += pcm
    return bytes(out[:target_bytes])


def one_run(adapter: TranscribeCppAsr, cfg: AsrConfig, pcm: bytes) -> dict:
    """Feed `pcm` paced to real time, then finalize. Returns timing facts."""
    result = {}
    done = time.__class__  # placeholder, replaced below
    import threading
    final_event = threading.Event()
    state = {"final": None, "chars": 0}

    def on_partial(_p):
        pass

    def on_final(f):
        state["final"] = f
        final_event.set()

    sess = adapter.open(cfg, on_partial, on_final)
    chunk_bytes = CHUNK_MS * BYTES_PER_MS
    audio_s = len(pcm) / 32000.0
    t_feed_start = time.perf_counter()
    for i in range(0, len(pcm), chunk_bytes):
        chunk = pcm[i:i + chunk_bytes]
        t0 = time.perf_counter()
        sess.feed(chunk)
        # Pace to real time: a live capture delivers this chunk's worth of
        # audio over CHUNK_MS wall-clock milliseconds, not instantly.
        elapsed = time.perf_counter() - t0
        sleep_s = max(0.0, CHUNK_MS / 1000.0 - elapsed)
        time.sleep(sleep_s)
    t_feed_done = time.perf_counter()
    t_finalize_start = time.perf_counter()
    sess.finalize()   # blocks until the Final has been delivered (port contract)
    t_finalize_end = time.perf_counter()
    if not final_event.wait(1.0):
        raise SystemExit("finalize() returned but on_final never fired -- "
                          "the port contract is broken, not the timing")
    sess.close()
    f = state["final"]
    result.update(
        audio_s=audio_s,
        feed_wall_s=t_feed_done - t_feed_start,
        tail_after_last_frame_s=t_finalize_end - t_finalize_start,
        chars=len(f.text or ""),
        failed=f.failed,
        text=f.text,
    )
    return result


def bench_model(slug: str, latency: str, short_pcm: bytes, long_pcm: bytes,
                 n_short: int, n_long: int) -> None:
    print(f"\n=== {slug}  backend=cpu  latency={latency} ===")
    adapter = TranscribeCppAsr(model=slug, backend="cpu", latency=latency)
    cfg = AsrConfig(model=slug, language=None)

    print("-- cold load (first open(), includes the GGUF load) --")
    t0 = time.perf_counter()
    r = one_run(adapter, cfg, short_pcm)
    cold_s = time.perf_counter() - t0
    print(f"cold: total_wall={cold_s:.2f}s "
          f"(includes feed+finalize of a {r['audio_s']:.2f}s clip) "
          f"chars={r['chars']} failed={r['failed']}")

    print(f"-- warm, short clip ({short_pcm.__len__()/32000.0:.2f}s), "
          f"{n_short} runs --")
    for i in range(n_short):
        r = one_run(adapter, cfg, short_pcm)
        rtf = r["feed_wall_s"] and (r["tail_after_last_frame_s"])
        print(f"  run {i+1}: audio={r['audio_s']:.2f}s "
              f"tail_after_last_frame={r['tail_after_last_frame_s']*1000:.0f}ms "
              f"chars={r['chars']} failed={r['failed']}")

    if n_long:
        print(f"-- warm, long clip ({long_pcm.__len__()/32000.0:.2f}s), "
              f"{n_long} runs --")
        for i in range(n_long):
            r = one_run(adapter, cfg, long_pcm)
            print(f"  run {i+1}: audio={r['audio_s']:.2f}s "
                  f"tail_after_last_frame={r['tail_after_last_frame_s']*1000:.0f}ms "
                  f"chars={r['chars']} failed={r['failed']}")


def main() -> int:
    short_pcm = load_pcm("long_utterance.wav")          # 5.19 s, real speech
    long_pcm = duplicate_to(short_pcm, 10.4)             # synthesized, see docstring
    print(f"short fixture: long_utterance.wav, {len(short_pcm)/32000.0:.2f}s")
    print(f"long fixture: long_utterance.wav DUPLICATED to "
          f"{len(long_pcm)/32000.0:.2f}s (not a genuine 10s+ recording -- "
          f"none exists in test/assets)")

    # The owner's exact configuration first.
    bench_model("nemotron-3.5-asr-streaming-0.6b", "accurate",
                short_pcm, long_pcm, n_short=3, n_long=3)

    # Comparison point named in the task: the other model choice, same
    # latency preset (asr_latency is ONE setting, not per-model).
    bench_model("parakeet-unified-en-0.6b", "accurate",
                short_pcm, long_pcm, n_short=3, n_long=3)

    return 0


if __name__ == "__main__":
    sys.exit(main())
