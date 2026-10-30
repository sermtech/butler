"""The desk microphone, with an optional lookback ring in front of it.

`docs/desk-hotkeys.md` section 6, `docs/desk-audio.md`.

WHY A RING, AND WHY IT IS THE ONLY REASON TO HOLD THE DEVICE
------------------------------------------------------------
Handy opens the device when the key goes down and discards every frame that
arrived before (`audio_toolkit/audio/recorder.rs:573-575`), and its own
tracker measures what that costs: time to first captured sample degrading
25-76 ms, then 380-416, then 488-606 across three releases, 1.4 s on Windows
11, with 6 of about 20 recordings capturing nothing at all
(`.references/push-to-talk-prior-art.md` section 1).

MEASURED here on 2026-09-09: opening the stream returned in 27 ms, and one
second of wall clock after that yielded 0.74 s of audio. About 260 ms of speech
goes missing at the start of an open-on-keydown capture on THIS machine. The
first syllable of "delete the old logs" is 260 ms.

So `preroll_ms` above 0 holds the stream open from `open()` to `close()`, a
bounded ring keeps the last `preroll_ms`, and key-down is an INDEX into audio
that already exists. `preroll_ms` 0 holds NOTHING: `open()` only checks that an
input device exists, `begin()` opens the stream and `end()` closes it, and the
first ~260 ms of the first word may clip (AC-819).

HOLDING THE DEVICE IS NOT FREE (AC-819, AC-820)
-----------------------------------------------
MEASURED 2026-10-25 and 2026-10-29: when the only microphone is the Remote
Desktop "Remote Audio" endpoint, its input channel sometimes stalls and
recovers ONLY when no process holds the device. A permanently open stream pins
it dead for every application, which is why 0 is the default and the ring is
opt-in. `holds_open` is the one place that decides the stream lifetime.

THE PRIVACY COST OF THE RING IS REAL AND IS STATED RATHER THAN HIDDEN
---------------------------------------------------------------------
With a ring, the last `preroll_ms` of the room is in this process memory at
all times. It never reaches disk, it is overwritten continuously, and none of
it leaves the machine unless a key is held. With 0 nothing is heard between
presses. `hotkeys_enabled` is False by default, and with it off this module is
never constructed.
"""
from __future__ import annotations

import collections
import threading
import time

SAMPLE_RATE = 16000          # ports.SAMPLE_RATE_IN; the daemon takes nothing else
BLOCK = 512                  # 32 ms at 16 kHz -- the ring's granularity
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000


class MicrophoneError(RuntimeError):
    """The device could not be opened or died. Loud; the caller decides."""


class Microphone:
    """A 16 kHz mono int16 stream, with an optional lookback ring.

    `preroll_ms` decides the stream lifetime (`holds_open`): above 0 it is
    open from `open()` to `close()`; at 0 it is open only from `begin()` to
    `end()`, so the device is free between dictations (AC-819).

    `sounddevice` is asked for 16 kHz DIRECTLY rather than for the device's
    native rate plus a resampler, which is what Handy does. It was accepted
    without complaint on this machine [MEASURED 2026-09-09]; if a device ever
    refuses, that is a loud failure here rather than a silent resample nobody
    reviewed.
    """

    def __init__(self, preroll_ms: int = 400, max_seconds: int = 120,
                 device=None):
        self.preroll_ms = max(0, int(preroll_ms))
        self.max_bytes = int(max_seconds) * SAMPLE_RATE * 2
        self.device = device
        self._stream = None
        self._lock = threading.Lock()
        # Bounded by FRAMES, computed from the window: the callback must not
        # do arithmetic per block, and a deque with maxlen drops the oldest
        # for free. `maxlen=0` is legal and is what 0 ms MUST mean -- an
        # earlier `max(1, ...)` here kept one 32 ms block for a lookback the
        # owner had switched off, which made the setting's help text false.
        frames = (self.preroll_ms * BYTES_PER_MS) // (BLOCK * 2)
        self._ring: collections.deque = collections.deque(maxlen=frames)
        self._capture: list[bytes] = []
        self._capturing = False
        self._overflow = False
        self._dropped = 0
        # How many blocks arrived DURING the current capture, and when the
        # last block arrived at all. Both exist because of a measured failure
        # (docs/desk-audio.md 1): a stream can open cleanly and then receive
        # nothing, with no exception, no `PaErrorCode` and no status flag, and
        # the only evidence anywhere is the absence of callbacks.
        self._live_blocks = 0
        self._last_block = 0.0
        self.info = "not open"

    # -- lifetime ----------------------------------------------------------- #
    @property
    def holds_open(self) -> bool:
        """The ring is the ONLY reason to hold the device (AC-819)."""
        return self.preroll_ms > 0

    def open(self) -> None:
        """Arm. With a ring, start the stream; per press, only find and describe
        the input device. Raises rather than degrading (fail fast)."""
        if self.holds_open:
            self._start()
            return
        import sounddevice as sd

        try:
            info = sd.query_devices(self.device, kind="input")
            api = sd.query_hostapis(info["hostapi"])["name"]
        except Exception as e:                            # noqa: BLE001
            raise MicrophoneError(f"no desk microphone to open: {e}") from e
        self.info = f"{info['name']!r} via {api}, opened per press"

    def _start(self) -> None:
        import sounddevice as sd

        if self._stream is not None:
            return
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=BLOCK, device=self.device, callback=self._on_block)
            self._stream.start()
        except Exception as e:                            # noqa: BLE001
            # A stream constructed but not started still HOLDS the device
            # (AC-820); leaking one per failed press would pin it for good.
            self.close()
            raise MicrophoneError(
                f"the desk microphone would not open at {SAMPLE_RATE} Hz "
                f"mono int16: {e}") from e
        # The clock starts at OPEN, not at the first block, so a stream that
        # never delivers one is silent for a growing interval rather than for
        # ever-zero seconds.
        self._last_block = time.monotonic()
        if self.holds_open:
            # Per press, `info` keeps the arming description: re-describing
            # on every capture would flip the status line twice a dictation.
            self.info = self._describe(sd)

    def _describe(self, sd) -> str:
        """Device, host API and rate, for the log line (AC-128).

        "Which microphone is Butler using" was unanswerable from the log until
        this existed, on a machine whose DEFAULT input turned out to be an RDP
        redirection endpoint rather than the microphone on the desk.
        """
        try:
            dev = self._stream.device
            info = sd.query_devices(dev)
            api = sd.query_hostapis(info["hostapi"])["name"]
            return (f"{info['name']!r} via {api} at "
                    f"{int(self._stream.samplerate)} Hz")
        except Exception as e:                            # noqa: BLE001
            return f"device {self.device!r} (could not be described: {e!r})"

    def reopen(self) -> None:
        """Close and open again, keeping the ring's shape (AC-127).

        A stream that has gone deaf never recovers on its own -- MEASURED
        2026-09-09: the desk agent's stream died, `apply_settings` only acts on
        a CHANGE so nothing re-examined it, and dictation stayed broken until
        the daemon was restarted while a FRESH stream opened and delivered
        immediately. Handy has no equivalent (`docs/desk-audio.md` 2) and would
        stay deaf too.

        The ring is dropped with the stream: what is in it came from a device
        that has stopped, so seeding a capture from it is how a dead microphone
        produced a plausible 0.4 s clip that the recognizer then blamed on the
        owner not speaking.
        """
        self.close()
        with self._lock:
            self._ring.clear()
            self._capture = []
            self._capturing = False
        self.open()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        # close() even when stop() fails (AC-820): a lost device raises on
        # stop, and the unclosed stream would keep holding it.
        try:
            stream.stop()
        except Exception:                                 # noqa: BLE001
            pass
        try:
            stream.close()
        except Exception:                                 # noqa: BLE001
            pass

    def release_if_full(self) -> None:
        """Per press, free the device once the ceiling has stopped the capture.

        The key may still be down, but nothing is being recorded, so holding
        the device until the key-up would pin it for nothing (AC-820). The
        captured audio stays; `end()` still returns it. A no-op with a ring.
        """
        if not self.holds_open and self._overflow and self._stream is not None:
            self.close()

    @property
    def live(self) -> bool:
        return self._stream is not None

    # -- the callback ------------------------------------------------------- #
    def _on_block(self, indata, _frames, _time, status) -> None:
        """PortAudio's thread. Does the least possible and never raises.

        An exception here kills the stream and the microphone with it, so a
        status flag is COUNTED rather than logged: printing from an audio
        callback is a blocking write on a thread that must not block.
        """
        if status:
            self._dropped += 1
        block = bytes(indata)
        # Written on the callback thread and read on the coordinator's; a
        # float assignment is atomic under the GIL and this must not take the
        # lock for a timestamp.
        self._last_block = time.monotonic()
        with self._lock:
            self._ring.append(block)
            if self._capturing:
                self._live_blocks += 1
                self._capture.append(block)
                if sum(map(len, self._capture)) > self.max_bytes:
                    # A key held down and never released stops HERE rather
                    # than growing without bound. Handy has no cap anywhere
                    # and an unbounded Vec behind a physical key is a
                    # stuck-key memory leak.
                    self._capturing = False
                    self._overflow = True

    # -- one capture -------------------------------------------------------- #
    def begin(self) -> None:
        """Start accumulating, SEEDED with the ring.

        The seed is the whole feature: the audio for the first ~400 ms of a
        dictation was captured before the key went down. Per press the ring is
        empty and the stream opens HERE, after the state is set so its first
        block is captured (AC-819). A failed open leaves nothing capturing
        (AC-821).
        """
        with self._lock:
            self._capture = list(self._ring)
            self._capturing = True
            self._overflow = False
            self._live_blocks = 0
        if self.holds_open:
            return
        try:
            self._start()
        except MicrophoneError:
            with self._lock:
                self._capturing = False
            raise

    def end(self) -> tuple[bytes, bool]:
        """Stop, and answer `(pcm, hit_the_ceiling)`.

        The flag is returned rather than logged because the CALLER decides
        what a truncated dictation means; this module does not know whether
        there is a person waiting for it.

        Per press this releases the device too (AC-819), OUTSIDE the lock:
        stopping a stream waits for the PortAudio callback, which takes it.
        """
        with self._lock:
            self._capturing = False
            pcm = b"".join(self._capture)
            self._capture = []
            overflow = self._overflow
        if not self.holds_open:
            self.close()
        return pcm, overflow

    @property
    def live_blocks(self) -> int:
        """Blocks that arrived DURING the last capture (AC-126).

        Zero after a key was held long enough to expect some is the whole
        signal that the device delivered nothing -- as opposed to the room
        being quiet, which still produces blocks, because silence is data.
        The caller must weigh it against how long the key was down: a 10 ms
        brush legitimately produces none, since blocks arrive every 32 ms.
        """
        return self._live_blocks

    def silent_for(self) -> float:
        """Seconds since the last block, or since `open()` if there was none.

        A live stream delivers continuously whether or not anyone is speaking,
        so this crossing a second means the stream is dead rather than the
        room being quiet. Answers 0.0 when nothing is open, because a closed
        microphone is not a deaf one and must not be reported as one.
        """
        if self._stream is None:
            return 0.0
        return max(0.0, time.monotonic() - self._last_block)

    @property
    def dropped_blocks(self) -> int:
        """How many callbacks reported an overflow or underflow.

        Read by the agent's log line rather than asserted on. A device that
        starts dropping is the shape of the Bluetooth and unplug failures in
        Handy's tracker, and the only symptom otherwise is a transcript that
        quietly gets worse.
        """
        return self._dropped
