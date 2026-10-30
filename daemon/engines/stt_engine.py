"""
Streaming STT engine for the spike -- a thin wrapper over transcribe.cpp
(handy-computer) that copies Handy's two structural clues:

  1. ONE engine, MANY models by GGUF path (catalog.json is the registry).
  2. Capabilities are canonical IN the loaded model -- we probe them at load
     time (model.capabilities) instead of hard-coding per-model behavior.

The heavy transcribe_cpp stream is single-threaded and blocking, so each
StreamingSession owns a worker thread fed by a queue; partials are pushed back
out through a caller-supplied thread-safe emit() callback. This mirrors the
process-isolation spirit of the app's existing stt-service.js without changing
that layer -- the spike is standalone.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import transcribe_cpp as tc

HERE = Path(__file__).resolve().parent
MODELS_DIR = HERE.parent.parent / "models"
CATALOG = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))

_MODEL_BY_SLUG = {m["slug"]: m for m in CATALOG["models"]}
_LATENCY = CATALOG["latency_presets"]


def catalog_public():
    """Registry payload the browser needs to render the model/latency pickers."""
    return {
        "models": [
            {k: m[k] for k in ("slug", "name", "family", "languages",
                               "capabilities", "license", "default_quant", "stream_kind")}
            for m in CATALOG["models"]
        ],
        "latency_presets": {k: v for k, v in _LATENCY.items() if k != "_note"},
    }


def gguf_path(slug: str, quant: str | None = None) -> Path:
    m = _MODEL_BY_SLUG[slug]
    quant = quant or m["default_quant"]
    return MODELS_DIR / f"{slug}-{quant}.gguf"


# Loaded models are cached by (path, backend). transcribe_cpp allows only one
# stream in-flight per Model, which is fine for a single-user spike.
_model_cache: dict[tuple[str, str], tc.Model] = {}
_cache_lock = threading.Lock()


def _get_model(path: Path, backend: str) -> tc.Model:
    key = (str(path), backend)
    with _cache_lock:
        m = _model_cache.get(key)
        if m is None:
            if not path.exists():
                raise FileNotFoundError(f"GGUF not found: {path} (download it into models/)")
            m = tc.Model(str(path), backend=backend)
            _model_cache[key] = m
        return m


# nemotron cache-aware encoders only accept att_context_right from this trained
# menu (encoder frames): 0/3/6/13 = 0/240/480/1040 ms lookahead. Map each
# latency preset onto the nearest legal rung -- an off-menu value is rejected by
# transcribe_stream_begin (InvalidArgument). Every preset in catalog.json needs
# a rung here; test/test_asr_latency.py holds the two lists together, because
# this one cannot be derived from the catalogue.
_CACHE_AWARE_RIGHT = {"low": 3, "balanced": 6, "accurate": 13}


def _family_ext(slug: str, latency: str):
    """Map a latency preset onto the correct family stream extension.

    parakeet-unified -> buffered streaming (L,C,R). nemotron -> cache-aware
    (att_context_right, fixed menu). Copied from the two ParakeetStream option
    classes in the transcribe_cpp binding.

    An unknown preset RAISES rather than falling back to balanced. The
    fallback is what let the settings page offer a preset this catalogue does
    not have: it saved, reported success and quietly ran something else, and
    silence was the only way the two could disagree (AC-7b, 2026-08-19)."""
    m = _MODEL_BY_SLUG[slug]
    p = _LATENCY.get(latency)
    if not isinstance(p, dict):
        raise KeyError(f"unknown latency preset {latency!r}; catalog.json has "
                       f"{sorted(k for k in _LATENCY if k != '_note')}")
    if m["stream_kind"] == "buffered":
        return tc.ParakeetBufferedStreamOptions(
            left_ms=p["left_ms"], chunk_ms=p["chunk_ms"], right_ms=p["right_ms"])
    return tc.ParakeetStreamOptions(
        att_context_right=_CACHE_AWARE_RIGHT[latency])


class StreamingSession:
    """One live microphone turn-machine. feed() PCM as it arrives; finalize()
    ends the turn. Emits partial/final dicts through emit(kind, payload)."""

    def __init__(self, slug, quant, backend, latency, language, emit):
        self.slug = slug
        self.emit = emit  # thread-safe callable(kind:str, payload:dict)
        path = gguf_path(slug, quant)
        # backend "auto" lets transcribe.cpp pick the best device (Vulkan here).
        self.model = _get_model(path, backend)
        caps = self.model.capabilities            # <-- capabilities from the MODEL
        if not caps.supports_streaming:
            raise RuntimeError(f"{slug} GGUF does not advertise streaming")
        self.info = {
            "slug": slug,
            "arch": self.model.arch,
            "variant": getattr(self.model, "variant", ""),
            "backend": str(self.model.backend),
            "streaming": True,
            "languages": _MODEL_BY_SLUG[slug]["languages"],
        }
        self._session = self.model.session()
        self._family = _family_ext(slug, latency)
        # The locale this stream decodes in, published in `info` so the
        # ADAPTER can stamp it on every Final without reaching into a private
        # attribute (AC-160). None means UNPINNED and the model decides.
        #
        # **NO MODEL HERE REQUIRES A LOCALE, and the guard that said one did
        # is GONE** (AC-215, 2026-09-11). Until then this raised ValueError for
        # a blank language on any `cache_aware` model, on the belief that
        # nemotron "rejects a missing locale outright" -- which made
        # `hub._speakable_language` substitute the model's FIRST locale, and
        # that is `en-US`.
        #
        # MEASURED against the real engine (`test/manual/spike_asr_unpinned.py`,
        # nemotron-3.5-asr-streaming-0.6b, cpu, accurate):
        #
        #   audio       language=None                 language='en-US'    language='pt-BR'
        #   English     full sentence, correct        identical           A SENTENCE LOST
        #   Portuguese  best of the three             one word mangled    one word mangled
        #
        # So unpinned is never worse than a pin and is sometimes much better,
        # and the raise was costing accuracy to prevent a failure the engine
        # does not have. What the engine DOES reject is the literal string
        # `auto` (`UnsupportedRequest: unsupported language (status 10)`), and
        # that string never reaches here -- `language` is a locale or nothing.
        #
        # The defect the raise was written for does NOT come back with it: the
        # version before it read `language or ("en-US" if requires_lang else
        # None)` and SILENTLY decoded English. None is not English; it is the
        # model choosing per utterance, which is the measurement above.
        #
        # `requires_language` went with the raise. It had exactly one reader
        # (`hub._speakable_language`), it was derived from `stream_kind` rather
        # than from anything measured, and a key that is False for every model
        # this daemon ships is a claim rather than a capability.
        self._lang = language or None
        self.info["language"] = self._lang
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._turn_lock = threading.Lock()
        self._t0 = None            # first audio of the current turn
        self._first_partial = None
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # -- called from the asyncio side -------------------------------------
    def feed(self, pcm_int16: bytes):
        self._q.put(("audio", pcm_int16))

    def finalize(self):
        self._q.put(("finalize", None))

    def reset_turn(self):
        self._q.put(("reset", None))

    def close(self):
        self._stop.set()
        self._q.put(("stop", None))

    # -- worker thread -----------------------------------------------------
    #
    # A transcribe.cpp Stream is ONE utterance: after finalize()/reset() it
    # cannot be fed again ("stream has been reset"). So we begin a FRESH stream
    # per turn (lazily on the turn's first audio) and end it on finalize or a
    # barge-in reset. One turn's error must never kill the worker, or every
    # later phrase would go dark.
    def _run(self):
        self._stream = None
        try:
            while not self._stop.is_set():
                kind, data = self._q.get()
                if kind == "stop":
                    break
                try:
                    if kind == "audio":
                        self._on_audio(data)
                    elif kind == "finalize":
                        self._on_finalize()
                    elif kind == "reset":
                        self._end_turn()
                except Exception as e:  # noqa: BLE001 -- recover, don't die
                    self.emit("error", {"message": f"stt: {e}"})
                    self._end_turn()
        finally:
            self._end_turn()

    def _begin_turn(self):
        self._stream = self._session.stream(language=self._lang, family=self._family)
        self._t0 = None
        self._first_partial = None

    def _end_turn(self):
        if self._stream is not None:
            try:
                self._stream.reset()
            except Exception:
                pass
            self._stream = None
        self._t0 = self._first_partial = None

    def _on_audio(self, pcm_int16: bytes):
        if self._stream is None:
            self._begin_turn()          # first audio of a new turn
        if self._t0 is None:
            self._t0 = time.perf_counter()
        pcm = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size == 0:
            return
        update = self._stream.feed(pcm)
        if update.committed_changed or update.tentative_changed:
            txt = self._stream.text()
            if self._first_partial is None and txt.committed.strip():
                self._first_partial = (time.perf_counter() - self._t0) * 1000
            self.emit("partial", {
                "committed": txt.committed,
                "tentative": txt.tentative,
            })

    def _on_finalize(self):
        if self._stream is None:
            return                      # finalize with nothing spoken -> ignore
        t_req = time.perf_counter()
        update = self._stream.finalize()
        final_txt = self._stream.text().committed.strip()
        now = time.perf_counter()
        payload = {
            "text": final_txt,
            "first_partial_ms": round(self._first_partial) if self._first_partial else None,
            "finalize_ms": round((now - t_req) * 1000),
            "turn_ms": round((now - self._t0) * 1000) if self._t0 else None,
            "audio_ms": round(getattr(update, "audio_committed_ms", 0)),
        }
        self._end_turn()
        self.emit("final", payload)
