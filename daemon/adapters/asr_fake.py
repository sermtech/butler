"""A scripted Asr. The second implementation of the port (AC-7a).

Boots instantly and needs no GPU, so protocol tests do not pay for a
parakeet load or contend with whatever else is on the card. Enabled by
BUTLER_TEST_STUB_STT=1.

It is deliberately NOT a null object. It emits partials on a realistic
cadence and always emits exactly one Final per finalize(), including the
empty one for silence, because those are the behaviours the protocol layer
is being tested against (D-7, AC-6). A fake that skips the empty Final
would let the bug it is meant to catch through.
"""
from __future__ import annotations

import threading

from .. import ports
from ..ports import AsrConfig, Final, Partial

# Chunk cadence the real engine runs at: (70,7,7) = 560 ms.
CHUNK_MS = 560
BYTES_PER_MS = 32          # 16 kHz * 2 bytes / 1000


class FakeAsrSession:
    def __init__(self, script: list[str], on_partial, on_final, lang=None):
        self._script = script
        # What this "recognizer" decoded in (AC-160). The fake honours the
        # config it was opened with rather than reporting nothing, because a
        # stamp only one adapter carries is a stamp no test can hold the
        # daemon to -- the same reason the fake implements every other half
        # of the port.
        self._lang = lang
        self._on_partial = on_partial
        self._on_final = on_final
        self._fed = 0
        self._emitted = 0
        self._committed = ""
        self._done = False
        self._lock = threading.Lock()

    def feed(self, pcm: bytes) -> None:
        with self._lock:
            if self._done:
                return
            # Only VOICED audio advances the script. This is the one way the
            # fake has to behave like a real recognizer: silence must produce
            # no words, or the empty-turn contract (D-7, AC-6) would be
            # untestable through the daemon -- a stub that transcribes silence
            # makes the empty final impossible to reach.
            if pcm.count(0) == len(pcm):
                return
            self._fed += len(pcm)
            # One partial per chunk of audio, in order, until the script runs out.
            while (self._emitted < len(self._script)
                   and self._fed >= (self._emitted + 1) * CHUNK_MS * BYTES_PER_MS):
                word = self._script[self._emitted]
                self._emitted += 1
                tentative = word
                self._on_partial(Partial(committed=self._committed,
                                         tentative=tentative))
                self._committed = (self._committed + " " + word).strip()

    def finalize(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            # Exactly one Final, ALWAYS -- empty text is a legal turn end.
            self._on_final(Final(text=self._committed,
                                 first_partial_ms=198 if self._committed else None,
                                 finalize_ms=150,
                                 lang=self._lang))

    def reset_turn(self) -> None:
        with self._lock:
            self._fed = 0
            self._emitted = 0
            self._committed = ""
            self._done = False

    def close(self) -> None:
        self._done = True


@ports.register(ports.ASRS, "fake")
class FakeAsr:
    """Transcribes whatever it was told to, paced by how much audio arrives.

    `script` is the word sequence to emit, and the default is DEFAULT_SCRIPT
    so the daemon's reply path is reachable under BUTLER_TEST_STUB_STT. Pass
    `[]` for a recognizer that never produces words: partials never fire and
    finalize() still produces the empty Final (D-7).
    """

    DEFAULT_SCRIPT = ["what", "time", "is", "it"]

    def __init__(self, script: list[str] | None = None, model: str = "",
                 backend: str = "", latency: str = ""):
        # The three engine options the daemon now hands EVERY recognizer from
        # the Recognition settings (D-36). Accepted and ignored rather than
        # rejected: a fake that raised on them would make `build_hub`
        # unusable in the whole suite, and `plan.md` 4.6a's rule that an
        # unaccepted option is a loud TypeError is about an option nobody
        # implements, not about a real setting reaching a stand-in. Recorded
        # so a test can assert the daemon passed what the owner chose.
        self.engine_options = {"model": model, "backend": backend,
                               "latency": latency}
        # None means "behave like a recognizer with something to say", so the
        # daemon's reply path is reachable under BUTLER_TEST_STUB_STT. Pass
        # [] explicitly for an adapter that never produces words.
        self._script = list(self.DEFAULT_SCRIPT if script is None else script)

    def open(self, cfg: AsrConfig, on_partial, on_final) -> FakeAsrSession:
        return FakeAsrSession(self._script, on_partial, on_final,
                              lang=cfg.language)

    async def transcribe(self, pcm: bytes, cfg: AsrConfig) -> str:
        """The whole script for a voiced clip, nothing for a quiet one.

        Same rule as `feed`, and for the same reason: a fake that transcribes
        silence makes the empty-clip contract untestable, and the ambient
        buffer's normal state is a quiet room. Length does NOT pace this one
        -- the clip is already over, so there is no cadence to reproduce, and
        the fake's job here is to make the contract reachable without a GPU.
        """
        if pcm.count(0) == len(pcm):
            # Covers both quiet clips at once: an empty buffer has zero zero
            # bytes out of zero, and an all-zero one has all of them.
            return ""
        return " ".join(self._script)

    async def warm(self) -> None:
        """Nothing to load, but the port requires the method to exist -- and
        the fake must exercise it, or the conformance suite would not notice
        a real adapter that forgot it."""
        sess = self.open(AsrConfig(model="warm"), lambda _p: None,
                         lambda _f: None)
        sess.close()

    @property
    def info(self) -> dict:
        return {"arch": "fake", "backend": "none", "variant": "stub",
                "sample_rate": ports.SAMPLE_RATE_IN}
