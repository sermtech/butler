"""Smart Turn v3 end-of-turn detection, server-side.

Given the raw PCM of the utterance so far, returns P(turn complete) in [0, 1].
This replaces the browser-side ONNX port: the server already receives the audio
(it is streaming to transcribe.cpp), native numpy+onnxruntime is ~5x faster than
the hand-rolled JS DFT, and it works identically on every client device --
including iPads, whose WebKit WASM limits make browser inference fragile.

The feature pipeline is a numpy port of Pipecat's vendored Whisper feature
extractor (`_whisper_features.py`) plus `local_smart_turn_v3.py`'s
truncate/pad helper -- the same math as the app's golden-tested smartTurn.ts.
Verified against the app's golden fixtures (see test_turn_engine.py).

Model I/O (smart-turn-v3.2-cpu.onnx):
  input  "input_features" float32 [1, 80, 800]  (Whisper log-mel)
  output "logits"         float32 [1, 1]        ALREADY a sigmoid probability.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
N_FFT = 400
HOP_LENGTH = 160
N_MELS = 80
MEL_FLOOR = 1e-10
NORM_VARIANCE_EPS = 1e-7
CHUNK_SECONDS = 8
TARGET_SAMPLES = SAMPLE_RATE * CHUNK_SECONDS  # 128000
NUM_FREQ_BINS = N_FFT // 2 + 1                # 201
OUTPUT_FRAMES = 800

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "smart-turn-v3.2-cpu.onnx"


# --------------------------------------------------------------------------- #
# Slaney mel scale (matches transformers.audio_utils)
# --------------------------------------------------------------------------- #
def _hertz_to_mel_slaney(f: np.ndarray | float) -> np.ndarray:
    f = np.asarray(f, dtype=np.float64)
    min_log_hertz, min_log_mel = 1000.0, 15.0
    logstep = 27.0 / np.log(6.4)
    return np.where(f >= min_log_hertz,
                    min_log_mel + np.log(np.maximum(f, 1e-30) / min_log_hertz) * logstep,
                    (3.0 * f) / 200.0)


def _mel_to_hertz_slaney(mel: np.ndarray) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    min_log_hertz, min_log_mel = 1000.0, 15.0
    logstep = np.log(6.4) / 27.0
    return np.where(mel >= min_log_mel,
                    min_log_hertz * np.exp(logstep * (mel - min_log_mel)),
                    (200.0 * mel) / 3.0)


def _build_mel_filterbank() -> np.ndarray:
    """Slaney area-normalized triangular filterbank, shape (NUM_FREQ_BINS, N_MELS)."""
    num_points = N_MELS + 2
    mel_min = _hertz_to_mel_slaney(0.0)
    mel_max = _hertz_to_mel_slaney(SAMPLE_RATE / 2.0)
    mel_freqs = mel_min + (mel_max - mel_min) * np.arange(num_points) / (num_points - 1)
    filter_freqs = _mel_to_hertz_slaney(mel_freqs)
    fft_freqs = (SAMPLE_RATE / 2) * (np.arange(NUM_FREQ_BINS) / (NUM_FREQ_BINS - 1))

    filter_diff = np.diff(filter_freqs)                         # (81,)
    enorm = 2.0 / (filter_freqs[2:] - filter_freqs[:-2])        # (80,)

    # slopes[j, m]
    slope_low = filter_freqs[None, :N_MELS] - fft_freqs[:, None]
    slope_high = filter_freqs[None, 2:] - fft_freqs[:, None]
    down = -slope_low / filter_diff[None, :N_MELS]
    up = slope_high / filter_diff[None, 1:]
    filters = np.maximum(0.0, np.minimum(down, up)) * enorm[None, :]
    return filters


def _build_hann() -> np.ndarray:
    """Periodic Hann (matches np.hanning(N+1)[:-1] / torch.hann_window)."""
    n = np.arange(N_FFT)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * n / N_FFT)


_MEL_FILTERS = _build_mel_filterbank()
_HANN = _build_hann()


def _truncate_or_pad(audio: np.ndarray) -> np.ndarray:
    """Keep the LAST 8s, or zero-pad AT THE START (speech sits at the tail)."""
    n = audio.shape[0]
    if n > TARGET_SAMPLES:
        return audio[n - TARGET_SAMPLES:].astype(np.float64)
    out = np.zeros(TARGET_SAMPLES, dtype=np.float64)
    if n:
        out[TARGET_SAMPLES - n:] = audio
    return out


def compute_whisper_log_mel(audio: np.ndarray) -> np.ndarray:
    """Raw 16 kHz mono float PCM -> float32 log-mel features, shape (80, 800)."""
    if audio.size == 0:
        raise ValueError("compute_whisper_log_mel: audio must be non-empty")

    x = _truncate_or_pad(np.asarray(audio, dtype=np.float64))

    mean = x.mean()
    var = ((x - mean) ** 2).mean()
    x = (x - mean) / np.sqrt(var + NORM_VARIANCE_EPS)

    padded = np.pad(x, N_FFT // 2, mode="reflect")
    num_frames = (padded.shape[0] - N_FFT) // HOP_LENGTH + 1          # 801
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::HOP_LENGTH]
    frames = frames[:num_frames] * _HANN[None, :]
    power = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1)) ** 2          # (801, 201)

    mel = _MEL_FILTERS.T @ power.T                                     # (80, 801)
    mel = np.maximum(MEL_FLOOR, mel)

    log_spec = np.log10(mel[:, :OUTPUT_FRAMES])                        # drop trailing frame
    floor = log_spec.max() - 8.0
    return ((np.maximum(log_spec, floor) + 4.0) / 4.0).astype(np.float32)


_DETECTOR = None
_POOL = None


def _init_worker() -> None:
    global _DETECTOR
    # Power-throttling opt-out for this pool child (engines/winqos.py): loud on
    # failure, never fatal. Spawned children inherit sys.path, so the sibling
    # module resolves.
    import winqos
    winqos.apply("smart-turn pool child")
    _DETECTOR = TurnDetector()


def _score_worker(audio: np.ndarray):
    """Runs IN THE CHILD PROCESS. Module-level so it is picklable."""
    return _DETECTOR.score_timed(audio)


def get_pool():
    """One dedicated process for end-of-turn inference.

    Measured on this machine: in-process (thread executor) the feature
    extraction degrades from ~30 ms to ~2200 ms while ONNX inference only goes
    ~80 ms -> ~370 ms. That asymmetry is the GIL -- onnxruntime releases it for
    the whole Run(), but the numpy feature pipeline re-acquires it constantly and
    loses to the STT worker thread. A separate process removes the shared GIL
    entirely; threads cannot fix this.
    """
    global _POOL
    if _POOL is None:
        import os
        from concurrent.futures import ProcessPoolExecutor
        # The child inherits this env and reads it when IT imports numpy, so its
        # BLAS stays single-threaded. Without this, numpy's threaded matmul
        # oversubscribes the cores transcribe.cpp's ggml workers already hold.
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS"):
            os.environ.setdefault(var, "1")
        _POOL = ProcessPoolExecutor(max_workers=1, initializer=_init_worker)
    return _POOL


class TurnDetector:
    """Loads the ONNX model once. `score()` is CPU-bound -- call it off the loop."""

    def __init__(self, model_path: Path | str = MODEL_PATH):
        import onnxruntime as ort  # imported here so the module is importable without it

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"smart turn model not found: {path}")
        # Thread budget. 1 thread measures ~110 ms in isolation but degrades to
        # 1.1-1.9 s inside the server process, where transcribe.cpp's ggml worker
        # threads keep the CPU busy -- a single ONNX thread simply loses the
        # scheduler fight. Tune with SMART_TURN_THREADS.
        import os
        threads = int(os.environ.get("SMART_TURN_THREADS", "4"))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self._session = ort.InferenceSession(str(path), sess_options=opts,
                                             providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def score(self, audio: np.ndarray) -> float:
        """P(turn complete) in [0, 1] for the utterance audio (16 kHz mono float)."""
        return self.score_timed(audio)[0]

    def score_timed(self, audio: np.ndarray) -> tuple[float, float, float]:
        """(probability, feature_ms, inference_ms) -- the split matters: under
        server load these two degrade for different reasons."""
        import time as _t
        t0 = _t.perf_counter()
        feats = compute_whisper_log_mel(audio)[None, :, :]
        t1 = _t.perf_counter()
        out = self._session.run(None, {self._input: feats})
        t2 = _t.perf_counter()
        raw = float(np.asarray(out[0]).reshape(-1)[0])
        if not np.isfinite(raw):
            raise ValueError(f"smart turn: non-finite score ({raw})")
        return min(1.0, max(0.0, raw)), (t1 - t0) * 1000, (t2 - t1) * 1000
