"""A silent Tts that keeps real timing. Second implementation of the port.

Yields multiple chunks at a realistic cadence rather than one blob, because
the behaviours worth testing are all about ordering and interruption:
sentence-granular abort, the announcer's turn-boundary queue, backpressure.
A fake that returned everything instantly would make every one of those
tests pass for the wrong reason.

Timing is derived from the measured realtime factor of the real engine
(3.5-4.6x on CPU), so a test that depends on synthesis being slower than
playback still behaves.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .. import ports

RTF = 4.0                   # measured 3.5-4.6x; pick the middle
CHUNK_MS = 120              # audio per yielded chunk
WORDS_PER_SECOND = 2.6      # ~155 wpm speech
# Deliberately not Kokoro's names. The fake accepts any voice string, so the
# only job these do is prove that a caller reading `voices` got them from THIS
# adapter -- borrowing the real engine's names would let a mix-up look correct.
# `{id, language}` since AC-155. TWO languages on purpose: with one, every
# test of "offer the voices for the chosen language" passes whether the
# filter works or not, which is the toothless-instrument failure (D-13). The
# locales are real ones so a caller comparing against a recognizer's list
# matches, and the NAMES are still deliberately not Kokoro's.
FAKE_VOICES = ({"id": "fake_low", "language": "en-US"},
               {"id": "fake_high", "language": "pt-BR"})


def _speech_ms(text: str) -> int:
    words = max(1, len(text.split()))
    return int(words / WORDS_PER_SECOND * 1000)


@ports.register(ports.TTSS, "fake")
class FakeTts:
    """Silent 24 kHz PCM, chunked and paced.

    `speed` multiplies the delays: tests that only care about ordering set
    speed=0 and get instant chunks in the right sequence; tests about
    interruption leave it at 1.
    """

    def __init__(self, speed: float = 0.0, fail_on: str | None = None):
        self.speed = speed
        self.fail_on = fail_on          # substring that makes synthesis raise
        self.aborted: list = []
        self.spoken: list[str] = []
        # Recorded because `voice` is a real part of the port and a fake that
        # discards it lets a caller which never passes one pass its own test.
        # That is not hypothetical: the Kokoro adapter dropped the voice for a
        # day while every test stayed green (AC-7b, D-18).
        self.voices: list[str] = []
        # And the same for the speaking rate (AC-107). Recorded, not acted on:
        # a silent fake has no rate to change, so the only thing it can prove
        # is that the caller passed one -- which is the assertion with teeth,
        # because the daemon reads the setting and the adapter is where it
        # gets dropped. Named `speeds` beside the constructor's `speed`, which
        # is this fake's own timing multiplier and a different thing entirely.
        self.speeds: list[float] = []
        self._cancelled: set = set()
        self._abort_all = False

    async def synthesize(self, text: str, voice: str, *,
                         speed: float = 1.0,
                         handle=None) -> AsyncIterator[bytes]:
        if self.fail_on and self.fail_on in text:
            # Loud, not silent: the caller must surface this, never skip the
            # sentence quietly (D-4).
            raise RuntimeError(f"fake tts refused to synthesize: {text!r}")
        self.spoken.append(text)
        self.voices.append(voice)
        self.speeds.append(speed)
        self._cancelled.discard(handle)
        total_ms = _speech_ms(text)
        emitted = 0
        while emitted < total_ms:
            # Checked per chunk, so an abort mid-reply actually stops the
            # stream. A fake that ignores the handle would let a broken
            # daemon-side abort pass its own test -- which is exactly how a
            # handle-less abort survived review once already.
            if self._abort_all or handle in self._cancelled:
                return
            step = min(CHUNK_MS, total_ms - emitted)
            if self.speed:
                await asyncio.sleep(step / 1000.0 / RTF / self.speed)
            samples = int(ports.SAMPLE_RATE_OUT * step / 1000)
            yield b"\x00\x00" * samples
            emitted += step

    async def abort(self, handle) -> None:
        self.aborted.append(handle)
        if handle is None:
            self._abort_all = True
        else:
            self._cancelled.add(handle)

    async def warm(self) -> None:
        async for _ in self.synthesize("warm", FAKE_VOICES[0]["id"],
                                   handle=("warm",)):
            pass
        self.spoken.clear()
        self.voices.clear()
        self.speeds.clear()

    @property
    def info(self) -> dict:
        """Static, like a real adapter's must be -- read during hello.

        The names are deliberately NOT Kokoro's. A fake that reported real
        voice names would let a caller which reads `voices` from the wrong
        adapter still look correct, and the whole point of a second
        implementation is to make that kind of mix-up fail (AC-7a).
        """
        return {"engine": "fake", "voice": FAKE_VOICES[0]["id"],
                "voices": [dict(v) for v in FAKE_VOICES],
                "sample_rate": ports.SAMPLE_RATE_OUT}
