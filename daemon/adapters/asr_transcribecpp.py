"""The real Asr: transcribe.cpp (parakeet/nemotron GGUF) behind the port.

This is a thin translator, not a second engine. `engines/stt_engine.py` is the
working spike wrapper and stays exactly as it is; everything here converts its
two shapes -- the `emit(kind, payload)` dict callback fired from a worker
thread, and its lazy per-turn stream -- into `Partial`/`Final` dataclasses.

`transcribe()` is the exception and reaches past that wrapper on purpose: it
is the port's WHOLE-CLIP half and `stt_engine` has no offline path at all, so
it takes the engine's model cache and calls `Session.run()` directly. Its own
docstring carries the measurement that made that worth doing.

Two behaviours are the whole reason this file is not a five-line wrapper:

1. **The engine emits NOTHING when a turn had no audio.** MEASURED 2026-08-16
   on parakeet-unified-en-0.6b: feeding 1 s of silence and finalizing DOES
   produce `final {text: ''}`, but calling finalize() with no audio at all
   produces nothing, because `_on_finalize()` returns early while `_stream is
   None`. That is the D-7 / AC-6 defect exactly: the turn never ends, the claim
   never releases, the next wake is refused as busy. So this adapter
   synthesizes the empty Final itself for that case, and de-duplicates so the
   engine's own Final can never make it two.

2. **finalize() blocks until the Final has been delivered.** The port says
   finalize "MUST cause exactly one Final"; the conformance suite asserts the
   callback has already fired when finalize() returns, and it is right to --
   a caller that has to guess when the turn ended cannot release a claim.
   The engine finalize costs 70-100 ms MEASURED, so this is a short wait, but
   it is a wait: call it off the event loop (`asyncio.to_thread`) from butlerd.

Timeouts are SPLIT (D-5, plan.md section 6). Model load happens inside
`open()` and is deliberately NOT under a watchdog -- killing a worker that is
merely still loading is the cascade-to-silence defect. `finalize()` gets its
own per-operation budget, SCALED to the audio this utterance was fed because
decode cost here is superlinear in utterance length (D-26; the derivation is
at FINALIZE_PER_AUDIO_S). Blowing it emits an empty Final marked `failed`
(the turn MUST end, and a turn nobody heard is not a quiet room) plus a loud
line on stderr (the failure MUST be visible).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from .. import ports
from ..ports import AsrConfig, Final, Partial

# engines/ is a directory of standalone modules, not a package (no __init__.py):
# stt_engine.py has to stay directly runnable by test/bench.py and server.py,
# which both do this same sys.path insert.
_ENGINES = Path(__file__).resolve().parents[1] / "engines"

DEFAULT_MODEL = "parakeet-unified-en-0.6b"

# Per-operation watchdog. NOT the model-load budget -- see the module docstring.
#
# It SCALES with the audio this utterance was fed (D-26). A fixed budget sat
# under ceilings that permit far more input than it covers --
# `absolute_max_turn_ms` is 120 s and `endpointing.END_WORD_MAX_MS` is 300 s --
# and nothing in the system compared the two numbers, so it was not a safety
# net but a silent data-loss threshold: past roughly 22-25 s of audio the turn
# completed normally having heard nothing.
#
# [MEASURED 2026-08-19] real adapter, CPU backend, one utterance per row
# (test/manual/spike_ambient.py, table A in docs/ambient.md):
#
#     audio    decode    decode per second of audio
#      1 s     0.09 s      0.09
#      5 s     1.69 s      0.34
#     10 s     5.58 s      0.56
#     20 s    13.64 s      0.68
#     30 s      >15 s      blew the old fixed 15 s and returned 0 characters
#
# Cost is SUPERLINEAR, so the per-second figure is not a constant to multiply
# by. Fitting a power law to the 5 s and 20 s rows gives
# decode ~= 0.15 * audio^1.5, which predicts 4.7 s for the 10 s row against
# 5.58 s measured -- about 15% optimistic in the middle of the range.
#
# FINALIZE_PER_AUDIO_S is therefore 2.0 s of budget per second of audio, not
# the 0.68 the 20 s row alone would suggest: a linear coefficient fitted to a
# superlinear cost under-provisions the moment the input grows, and 2.0 stays
# ahead of that fit out to about 178 s of audio. Below that it deliberately
# OVER-provisions -- a 20 s utterance gets 40 s for a 13.6 s decode -- because
# the two errors are not symmetric. Over-provisioning costs waiting on an
# engine that is already broken; under-provisioning costs the utterance, and
# costs it silently.
#
# FINALIZE_MAX_S is the hard ceiling, and it exists because an unbounded wait
# on a wedged engine is D-1: finalize() blocks the turn, the claim never
# releases and the microphone never comes back. 60 s is reached at 30 s of
# audio and covers about 54 s of audio by the fit above. Longer than that
# still blows the budget -- but it now ends the turn with a FAILED final,
# which is SPOKEN rather than silent. The actual fix is never handing the
# engine one long utterance at all (AC-71, docs/ambient.md), and it is unbuilt.
#
# The floor keeps every short turn exactly as it was: it is the fixed 15 s
# this constant used to be, and the scaled term only overtakes it past 7.5 s
# of audio.
FINALIZE_FLOOR_S = 15.0
FINALIZE_PER_AUDIO_S = 2.0
FINALIZE_MAX_S = 60.0

_BYTES_PER_S = ports.SAMPLE_RATE_IN * 2      # 16 kHz mono int16


def finalize_budget_s(audio_s: float, floor_s: float) -> float:
    """How long finalize() may wait for an utterance of `audio_s` seconds."""
    return min(FINALIZE_MAX_S, max(floor_s, audio_s * FINALIZE_PER_AUDIO_S))


def _stt_engine():
    """Import the engine lazily.

    Importing `transcribe_cpp` enumerates Vulkan devices and loads native
    backends (~0.5 s plus four lines of driver noise). Registration below is
    unconditional, as daemon/adapters/__init__.py requires, but the COST is
    paid by whoever actually opens a session -- which is the entire point of
    shipping a fake alongside.
    """
    if str(_ENGINES) not in sys.path:
        sys.path.insert(0, str(_ENGINES))
    import stt_engine  # noqa: PLC0415  (deliberately lazy, see docstring)
    return stt_engine


class TranscribeCppSession:
    """One utterance machine, wrapping one `stt_engine.StreamingSession`.

    Thread model: `feed`/`finalize`/`reset_turn`/`close` are called by the
    daemon; `_emit` is called by the engine's worker thread. `_lock` guards the
    turn flags only -- the `on_partial`/`on_final` callbacks are invoked
    OUTSIDE it so a caller that re-enters the session cannot deadlock the
    engine worker.
    """

    def __init__(self, on_partial, on_final, finalize_floor_s: float,
                 **engine_kwargs):
        self._on_partial = on_partial
        self._on_final = on_final
        self._finalize_floor_s = finalize_floor_s
        self._lock = threading.Lock()
        self._final_done = threading.Event()
        self._delivered = False     # a Final has been handed to the caller
        self._finalizing = False    # finalize() called, Final not yet delivered
        self._fed = False           # any audio reached the engine this turn
        self._fed_bytes = 0         # THIS utterance's audio, for the budget
        self._closed = False
        self._engine = _stt_engine().StreamingSession(emit=self._emit,
                                                      **engine_kwargs)
        self.engine_info = dict(self._engine.info)

    # -- engine worker thread ------------------------------------------------
    def _emit(self, kind: str, payload: dict) -> None:
        if kind == "partial":
            with self._lock:
                if self._closed or self._delivered:
                    return          # stale: this turn already ended
            self._on_partial(Partial(committed=payload.get("committed", "") or "",
                                     tentative=payload.get("tentative", "") or ""))
            return
        if kind == "final":
            with self._lock:
                stale = not self._finalizing
            if stale:
                # The engine only emits `final` from _on_finalize(), so this
                # can only be a straggler for a turn that was abandoned.
                # Delivering it would speak a reply to an interrupted
                # utterance (the reset-is-not-finalize rule).
                return
            self._deliver(Final(
                text=(payload.get("text") or "").strip(),
                first_partial_ms=payload.get("first_partial_ms"),
                finalize_ms=payload.get("finalize_ms"),
                # The locale the engine was ASKED for, echoed back
                # (AC-160). Not a resolved value -- nothing reports one; see
                # `ports.Final`. `.get` rather than `[]` because a stand-in
                # engine reports whatever it likes (the suite has one whose
                # `info` is two keys) and a stamp is not worth an
                # AttributeError on the audio path.
                lang=self.engine_info.get("language"),
            ))
            return
        if kind == "error":
            # Loud, always -- the engine swallows the exception to keep its
            # worker alive, so stderr is the only place this surfaces.
            print(f"[butler][asr] engine error: {payload.get('message')}",
                  file=sys.stderr, flush=True)
            with self._lock:
                pending = self._finalizing and not self._delivered
            if pending:
                # The engine already tore the turn down and will never send a
                # Final. The turn still has to END (D-7), and it ends as a
                # FAILURE: what the owner said is gone, which is not the same
                # thing as a quiet room, and only this flag can tell the
                # daemon which of the two to speak about (D-26).
                self._deliver(Final(text="", failed=True,
                                lang=self.engine_info.get("language")))

    def _deliver(self, final: Final) -> None:
        with self._lock:
            if self._delivered:
                return              # exactly one Final per turn, ever
            self._delivered = True
        self._on_final(final)
        # Set AFTER the callback: finalize() returning must mean the caller
        # has already seen the Final, not that one is on its way.
        self._final_done.set()

    # -- daemon side ---------------------------------------------------------
    def feed(self, pcm: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            if self._delivered:
                # The previous turn ended and audio is arriving again: this is
                # the next utterance. The engine does the same thing -- it
                # begins a fresh stream on the first audio after a turn ends.
                self._start_turn_locked()
            elif self._finalizing:
                # Only reachable from a second thread, because finalize()
                # blocks. The engine's stream for this turn is already closed.
                return
            self._fed = True
            # Counted per UTTERANCE, not per session: the budget is for what
            # this finalize has to decode, and `_start_turn_locked` above (and
            # `reset_turn`) clears it (D-26).
            self._fed_bytes += len(pcm)
        self._engine.feed(pcm)

    def finalize(self) -> None:
        with self._lock:
            if self._delivered or self._finalizing:
                return              # idempotent: a double finalize is one end
            self._finalizing = True
            fed = self._fed
            audio_s = self._fed_bytes / _BYTES_PER_S
        if not fed:
            # Nothing was ever fed, so the engine has no stream and its
            # _on_finalize() returns silently. Synthesize the empty Final:
            # the Final is the turn's END, not its text (D-7 / AC-6).
            self._deliver(Final(text="", finalize_ms=0,
                            lang=self.engine_info.get("language")))
            return
        budget = finalize_budget_s(audio_s, self._finalize_floor_s)
        self._engine.finalize()
        if not self._final_done.wait(budget):
            # Loud, and the turn ends as a FAILURE rather than as silence
            # (D-26). The claim still has to release (D-7, D-1) -- what
            # changed is that the daemon can now tell the room the difference
            # between "you said nothing" and "I could not finish listening".
            print(f"[butler][asr] engine did not finalize {audio_s:.1f}s of "
                  f"audio in {budget:.1f}s; ending the turn with a FAILED "
                  f"empty final so the claim releases",
                  file=sys.stderr, flush=True)
            self._deliver(Final(text="", failed=True,
                                lang=self.engine_info.get("language")))

    def reset_turn(self) -> None:
        """Barge-in: abandon the utterance WITHOUT a Final."""
        with self._lock:
            if self._closed:
                return
            self._start_turn_locked()
        self._engine.reset_turn()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._engine.close()

    # -- internals -----------------------------------------------------------
    def _start_turn_locked(self) -> None:
        self._delivered = False
        self._finalizing = False
        self._fed = False
        self._fed_bytes = 0
        self._final_done.clear()


@ports.register(ports.ASRS, "transcribecpp")
class TranscribeCppAsr:
    """The transcribe.cpp engine behind the Asr port.

    Constructor arguments PIN a field; anything left None is taken from the
    per-session `AsrConfig`. The precedence is that way round because the
    constructor is `config/models.json` speaking (plan.md 4.10: adapters are
    the only place an engine is named outside config/), while AsrConfig is one
    turn asking. It matters most for `model`: transcribe.cpp binds a GGUF at
    load time and caches it per (path, backend), so switching model per turn
    would drop a 600 MB load onto the audio thread. Use a different adapter
    instance instead -- `ports.build(ports.ASRS, "transcribecpp", model=...)`.

    `AsrConfig.model` is therefore not used to pick the GGUF. A config that
    names a DIFFERENT catalogue slug is a real conflict and raises; anything
    that is not a catalogue slug at all (the conformance suite passes the
    placeholder "test") is not a model request and is ignored.
    """

    def __init__(self, model: str = DEFAULT_MODEL, quant: str | None = None,
                 backend: str = "auto", latency: str = "balanced",
                 language: str | None = None,
                 finalize_floor_s: float = FINALIZE_FLOOR_S):
        self._model = model
        self._quant = quant
        self._backend = backend
        self._latency = latency
        self._language = language
        self._finalize_floor_s = finalize_floor_s
        self._info: dict | None = None
        self._info_lock = threading.Lock()

    def open(self, cfg: AsrConfig, on_partial, on_final) -> TranscribeCppSession:
        self._check_model(cfg)
        sess = TranscribeCppSession(
            on_partial, on_final, self._finalize_floor_s,
            slug=self._model,
            quant=self._quant,
            # Straight from this adapter's own construction: these are
            # transcribe.cpp's vocabulary and no longer cross the port
            # (AC-41). "auto" picks the device; "balanced" is the measured
            # (70,7,7) 560 ms chunking the spike settled on.
            backend=self._backend,
            latency=self._latency,
            language=self._language or cfg.language,
        )
        with self._info_lock:
            self._info = dict(sess.engine_info,
                              sample_rate=ports.SAMPLE_RATE_IN)
        return sess

    async def transcribe(self, pcm: bytes, cfg: AsrConfig) -> str:
        """The port's whole-clip half, on transcribe.cpp's OFFLINE entry point.

        `Session.run()` and `Session.stream()` sit on the same loaded model
        and cost wildly different amounts, and until 2026-08-19 nothing in
        this repository had ever called the first one. `catalog.json` maps
        parakeet-unified-en-0.6b to `stream_kind: "buffered"`, a sliding
        [left|chunk|right] window that re-encodes each second of audio about
        twelve times; `run()` takes the clip once with full context.

        [MEASURED 2026-08-19, cpu backend, real fixtures, same machine
        (test/manual/spike_ambient_offline.py, docs/ambient.md section 1)]:

            audio    stream()          run()      chars
              5 s     1.69 s          0.28 s         95
             20 s    13.64 s          1.27 s        324
             30 s    15.01 s, 0 chars 1.94 s        507
             60 s    --               3.93 s        980

        So 30 s of back-fill costs about 2 s here, where the streaming path
        spent 15 s to return nothing at all. That number is the whole reason
        this method exists.

        NO CEILING was found, and this is the place to say so rather than
        discover it in a turn. `Session.limits.effective_max_audio_ms` is 0
        for this model, which the binding documents as unbounded, and the
        decode was measured out to 240 s with the transcript growing linearly
        (60 s -> 980 chars, 120 s -> 1965, 240 s -> 3922; cost drifts from
        19.6x to 9.4x real time over that range). The engine's truncation
        failure mode is an `OutputTruncated` EXCEPTION carrying the partial
        transcript, not a quietly short string, so nothing is caught here: a
        clip too long to decode has to be loud.

        Two traps are designed out rather than documented around, because
        both produce an empty transcript that reads exactly like a quiet
        room. `run()` wants FLOAT32, and handed the raw int16 BYTES this port
        carries it accepts them and returns text='' -- measured 2026-08-19 on
        long_utterance.wav: 0 characters against 94 for the same audio
        converted. A numpy int16 array it refuses loudly (InvalidArgument),
        so the one shape that fails silently is exactly the one an adapter
        would pass by doing nothing. And `Result.__repr__` is ~214 characters
        of timings whatever was decoded, so `.text` is the only thing read
        here and the only thing the tests assert on.
        """
        import asyncio

        self._check_model(cfg)
        if not pcm:
            # No audio at all: the ambient buffer before anything was
            # captured. Never handed to the engine, which validates its
            # input and would raise on an empty array (the port says a quiet
            # clip returns "" and does not raise).
            return ""
        return await asyncio.to_thread(self._decode_clip, pcm, cfg)

    def _decode_clip(self, pcm: bytes, cfg: AsrConfig) -> str:
        """BLOCKING. Called on a worker thread by `transcribe` only.

        It reaches `stt_engine._get_model` rather than building a
        `StreamingSession`: that is the engine's model CACHE, keyed by
        (path, backend), so the whole-clip path shares the GGUF the live
        turns already loaded instead of paying a second 600 MB load. A
        `StreamingSession` would also start a worker thread and a per-turn
        stream, which is precisely the machinery a finished clip does not
        need.

        The session is per call and closed after it. transcribe.cpp allows
        one stream in flight per Model, so a long-lived one held here would
        be a second thing contending for that with the live turn.
        """
        import numpy as np

        stt = _stt_engine()
        model = stt._get_model(stt.gguf_path(self._model, self._quant),
                               self._backend)
        session = model.session()
        try:
            audio = (np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                     / 32768.0)
            result = session.run(audio,
                                 language=self._language or cfg.language)
            return result.text.strip()
        finally:
            session.close()

    async def warm(self) -> None:
        """Load the GGUF now, in a thread, so no turn ever pays for it.

        Both halves matter. Loading it at all at boot is D-10. Loading it in
        a THREAD is because the load is a blocking native call and butlerd is
        single-threaded asyncio -- doing it inline at startup would merely
        move the freeze rather than remove it, and this same call is what
        `info` triggers lazily during a satellite's handshake.
        """
        import asyncio

        def _load():
            # A model that DEMANDS a locale gets one, or the warm-up is the
            # only thing that fails and the cold load lands on the owner's
            # first sentence instead (D-10). `warm()` has no turn behind it
            # and so no `language` setting to read -- the Hub resolves that
            # per turn -- so this asks the CATALOGUE for one the model
            # accepts. Any of them loads the same weights; which one is
            # used for real is decided per turn by `Hub._speakable_language`.
            #
            # Found by restarting the owner's daemon on 2026-09-10: their
            # `asr_model` had just become live and nemotron refused to warm,
            # so a 15-30 s load was queued behind their next utterance.
            spoken = next(
                (m["languages"] for m in _stt_engine().CATALOG["models"]
                 if m["slug"] == self._model), [])
            probe = self.open(
                AsrConfig(model=self._model,
                          language=self._language or (spoken[0] if spoken
                                                      else None)),
                lambda _p: None, lambda _f: None)
            probe.finalize()      # exercises the finalize path too
            probe.close()

        await asyncio.to_thread(_load)

    @property
    def info(self) -> dict:
        """Capabilities read off the LOADED model, never a per-slug table.

        What is reported is what transcribe.cpp actually resolved -- backend
        "Vulkan0" when "auto" picked the 1080 Ti -- rather than what the
        catalogue hoped for. `warm()` fills it at boot, off the event loop.

        Until it is filled this SAYS SO rather than finding out. It used to
        open a throwaway session here, which is a blocking GGUF and Vulkan
        load: measured at 744 ms with a warm page cache, and the smallest
        model on this machine cold-loads in 15.85 s [MEASURED 2026-08-17].
        `hub._hello` reads this synchronously on the event loop every time a
        device connects, and `butlerd.warmup` swallows a warm failure on
        purpose (loud, never fatal -- D-1), so a single failed warm left that
        load reachable for the life of the process, freezing every satellite
        on every connect.

        Reporting "unknown" is not a silent fallback. Warmup has already
        printed a warning, and a HUD reading unknown/unknown is the visible
        symptom that it did -- which is strictly more honest than a value
        obtained by stalling the daemon to get it.
        """
        with self._info_lock:
            cached = self._info
        if cached is None:
            return {"arch": "unknown", "backend": "unknown",
                    "model": self._model, "warmed": False}
        return dict(cached, warmed=True)

    # -- internals -----------------------------------------------------------
    def _check_model(self, cfg: AsrConfig) -> None:
        known = {m["slug"] for m in _stt_engine().CATALOG["models"]}
        if cfg.model in known and cfg.model != self._model:
            raise ValueError(
                f"AsrConfig asks for model {cfg.model!r} but this adapter is "
                f"pinned to {self._model!r}; build a second adapter rather "
                f"than reloading a GGUF mid-session")
        if self._model not in known:
            raise KeyError(
                f"no model {self._model!r} in engines/catalog.json; "
                f"known: {sorted(known)}")
