"""Pocket TTS behind the Tts port: kyutai's pinned Rust binary as a loopback
HTTP server, one per language model.

Ported from an earlier prototype's pocket engine (`scripts/dashboard/server/lib/tts/
engines/pocket/`, default there since 2026-10-30). The release, voices and
languages are in `pocket_pin.py`; `scripts/fetch-pocket-tts.py` installs the
binary. Nothing here falls back to Kokoro: a missing binary is one error that
names the fetch command.

THE SEAMS, EACH SWAPPABLE ALONE
-------------------------------
- `_Server`: one `pocket-tts.exe serve --language <model>` child (spawn,
  health check, tree kill). The adapter holds them in `_servers` by model and
  needs only `ensure() -> base url`, `alive()` and `stop()` from each, which
  is the stand-in `test/test_tts_pocket.py` puts there.
- `WavReader`: the server streams a RIFF/WAVE whose sizes are unknown while it
  generates; this turns arbitrary network pieces into PCM and refuses any
  format the port cannot carry (24 kHz mono int16, measured 2026-10-30 for
  English and Portuguese).
- `EdgeTrimmer`: every piece carries ~200 ms lead and ~350 ms tail silence
  [MEASURED-an earlier prototype]; Butler speaks a reply as 8-16 word segments, so
  untrimmed that is half a second of dead air between phrases.

MEASURED FACTS THIS RELIES ON (an earlier prototype phase 0, rechecked here 2026-10-30)
------------------------------------------------------------------------------
- Dropping the HTTP request stops generation, so barge-in is closing the
  response; there is no abort route and no kill.
- The server is SERIAL, which is why an abort must close the response rather
  than merely stop reading it.
- First audio 39 ms (English) and 104 ms (Portuguese) after the request, and
  a server is ready 0.5-1.1 s after spawn with the weights cached.
- No speed control: it speaks at about Kokoro's 1.3x by nature.
- `voice_url` must be a built-in name or a URL; a local file is refused (400).
"""
from __future__ import annotations

import asyncio
import atexit
import collections
import itertools
import math
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import AsyncIterator

import aiohttp
import numpy as np

from .. import childproc, ports
from . import pocket_pin

RATE = ports.SAMPLE_RATE_OUT
WARM_TEXT = "Ready."

BOOT_TIMEOUT_S = 180.0    # spawn to answering HTTP; a missing weight downloads here
SYNTH_TIMEOUT_S = 30.0    # gap between pieces of one response once it started

# Edge trim, an earlier prototype's measured defaults (policies/edge-silence.js): the
# threshold sits 20x above the noise floor and ~100x below speech onset, so it
# can only ever name silence; the pads keep a breath of lead and a natural
# pause between segments.
TRIM_THRESHOLD = 0.002
KEEP_LEAD_MS = 30
KEEP_TRAIL_MS = 150

_ENGINES = Path(__file__).resolve().parents[1] / "engines"


class PocketError(RuntimeError):
    """The binary is missing, the server would not start, or it failed a
    synthesis. Loud on purpose: silence reads as Butler ignoring the user."""


def _winqos():
    """engines/winqos.py (a standalone module, like the other engines)."""
    if str(_ENGINES) not in sys.path:
        sys.path.insert(0, str(_ENGINES))
    import winqos  # noqa: PLC0415
    return winqos


# --------------------------------------------------------------------------- #
# The binary and its server
# --------------------------------------------------------------------------- #
def check_binary(exe: Path) -> None:
    """Refuse a missing binary or one that is not the pinned version."""
    fix = f"Run: {pocket_pin.FETCH_COMMAND}"
    if not Path(exe).is_file():
        raise PocketError(f"Pocket TTS is not installed ({exe} is missing). {fix}")
    try:
        found = subprocess.run([str(exe), "--version"], capture_output=True,
                               text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        # A truncated download is not an executable (WinError 216).
        raise PocketError(f"{exe} does not run ({e}). {fix}") from e
    want = f"pocket-tts {pocket_pin.RELEASE['version']}"
    if found != want:
        raise PocketError(f"{exe} reports {found!r}, but {want!r} is pinned. {fix}")


def server_argv(exe: str, model: str, port: int) -> list[str]:
    """IPv4 loopback, one language model, int8 (an earlier prototype's measured winner:
    5.7x realtime, 584 MB)."""
    return [str(exe), "serve", "--host", "127.0.0.1", "--port", str(port),
            "--language", model, "--quantize"]


def server_env(env: dict, home: Path) -> dict:
    return dict(env, HF_HUB_CACHE=pocket_pin.hf_hub_cache(env, home))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """One `pocket-tts.exe serve` child for one language model."""

    def __init__(self, exe: Path, model: str, boot_timeout_s: float):
        self.exe = Path(exe)
        self.model = model
        self.boot_timeout_s = boot_timeout_s
        self.proc: subprocess.Popen | None = None
        self.port = 0
        # Set only once the health check passes. A process that is alive but
        # not ready is never handed out: a second caller during a slow boot
        # would POST to a port nobody listens on yet (review, 2026-10-30).
        self.ready = False
        self._log = collections.deque(maxlen=100)
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def ensure(self) -> str:
        if not (self.ready and self.alive()):
            await asyncio.to_thread(self._start)
        return self.url

    def tail(self) -> str:
        return (":\n  " + "\n  ".join(self._log)) if self._log else " (no output)"

    def _start(self) -> None:
        with self._lock:
            if self.ready and self.alive():
                return
            self._stop_locked()                     # a half-started child
            check_binary(self.exe)
            self.port = _free_port()
            self._log.clear()
            self.proc = subprocess.Popen(
                server_argv(self.exe, self.model, self.port),
                env=server_env(dict(os.environ), Path.home()),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            childproc.assign(self.proc)              # AC-36, D-24: same function
            _winqos().apply(f"pocket-tts {self.model}", self.proc.pid)
            _LIVE.add(self)
            threading.Thread(target=self._drain, daemon=True,
                             name=f"pocket-{self.model}-log").start()
            deadline = time.monotonic() + self.boot_timeout_s
            while time.monotonic() < deadline:
                if not self.alive():
                    raise PocketError(f"pocket-tts {self.model} exited during "
                                      f"start{self.tail()}")
                try:
                    with urllib.request.urlopen(self.url + "/", timeout=1):
                        self.ready = True
                        return
                except OSError:
                    time.sleep(0.1)
            self._stop_locked()
            raise PocketError(f"pocket-tts {self.model} did not answer in "
                              f"{self.boot_timeout_s:.0f}s{self.tail()}")

    def _drain(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.rstrip()
            self._log.append(line)
            print(f"[pocket-{self.model}] {line}", file=sys.stderr, flush=True)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self.proc = self.proc, None
        self.ready = False
        _LIVE.discard(self)
        if proc is None or proc.poll() is not None:
            return
        childproc.kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


_LIVE: set[_Server] = set()


@atexit.register
def _stop_all_servers() -> None:
    for server in list(_LIVE):
        try:
            server.stop()
        except Exception as e:  # noqa: BLE001 -- shutdown must not mask the real exit
            print(f"[pocket] shutdown: {e}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# The WAV stream and its edges
# --------------------------------------------------------------------------- #
class WavReader:
    """Streamed RIFF/WAVE bytes in, PCM bytes out, at any split point."""

    def __init__(self):
        self._buf = bytearray()
        self._in_data = False

    def push(self, data: bytes) -> bytes:
        self._buf += data
        if not self._in_data:
            self._parse_header()
            if not self._in_data:
                return b""
        whole = len(self._buf) - len(self._buf) % 2
        out = bytes(self._buf[:whole])
        del self._buf[:whole]
        return out

    def end(self) -> None:
        if not self._in_data:
            raise PocketError("the pocket WAV stream ended inside its header")
        if self._buf:
            raise PocketError("the pocket WAV stream ended mid-sample")

    def _parse_header(self) -> None:
        buf = self._buf
        if len(buf) < 12:
            return
        if buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
            raise PocketError(f"pocket /tts did not answer a WAV stream: "
                              f"{bytes(buf[:16])!r}")
        pos, fmt_ok = 12, False
        while pos + 8 <= len(buf):
            chunk = bytes(buf[pos:pos + 4])
            size = int.from_bytes(buf[pos + 4:pos + 8], "little")
            if chunk == b"data":
                if not fmt_ok:
                    raise PocketError("pocket WAV stream: data before fmt")
                del buf[:pos + 8]
                self._in_data = True
                return
            if pos + 8 + size > len(buf):
                return                      # this chunk has not all arrived
            if chunk == b"fmt ":
                tag, channels, rate = (int.from_bytes(buf[pos + 8:pos + 10], "little"),
                                       int.from_bytes(buf[pos + 10:pos + 12], "little"),
                                       int.from_bytes(buf[pos + 12:pos + 16], "little"))
                bits = int.from_bytes(buf[pos + 22:pos + 24], "little")
                if (tag, channels, rate, bits) != (1, 1, RATE, 16):
                    raise PocketError(
                        f"pocket WAV stream is format {tag}, {rate} Hz, {bits} "
                        f"bits, {channels} channel(s); the port is {RATE} Hz "
                        f"mono 16-bit PCM")
                fmt_ok = True
            pos += 8 + size + size % 2      # RIFF chunks are word aligned


def _loud(samples: np.ndarray) -> np.ndarray:
    return np.flatnonzero(np.abs(samples.astype(np.int32)) / 32768.0
                          >= TRIM_THRESHOLD)


def _ms(ms: int, rate: int) -> int:
    return round(ms * rate / 1000)


def trim_edges(pcm: bytes, rate: int) -> bytes:
    """Cut the silence before the first and after the last loud sample, keeping
    the pads. Silence inside the piece is never touched."""
    a = np.frombuffer(pcm, "<i2")
    if not len(a):
        return b""
    loud = _loud(a)
    if not len(loud):
        return a[:max(1, min(len(a), _ms(KEEP_TRAIL_MS, rate)))].tobytes()
    lo = max(0, loud[0] - _ms(KEEP_LEAD_MS, rate))
    hi = min(len(a), loud[-1] + 1 + _ms(KEEP_TRAIL_MS, rate))
    return a[lo:hi].tobytes()


class EdgeTrimmer:
    """`trim_edges` for audio that arrives in pieces: the concatenated output
    equals `trim_edges` of the whole, at any split. Quiet samples after the
    latest loud one are HELD (a pause, kept, or the tail, cut) until the next
    loud sample or `end()`, so first audio waits only for the first loud one."""

    def __init__(self, rate: int):
        self._lead = _ms(KEEP_LEAD_MS, rate)
        self._trail = _ms(KEEP_TRAIL_MS, rate)
        self._started = False
        self._head = np.zeros(0, "<i2")    # all-silent case: the first `trail`
        self._pre = np.zeros(0, "<i2")     # the last `lead` before the first loud
        self._held = np.zeros(0, "<i2")

    def push(self, pcm: bytes) -> bytes:
        a = np.frombuffer(pcm, "<i2")
        if not len(a):
            return b""
        loud = _loud(a)
        if not self._started:
            if not len(loud):
                self._head = np.concatenate([self._head, a])[:self._trail]
                if self._lead:
                    self._pre = np.concatenate([self._pre, a])[-self._lead:]
                return b""
            self._started = True
            before = np.concatenate([self._pre, a[:loud[0]]])
            pad = before[-self._lead:] if self._lead else before[:0]
            self._held = a[loud[-1] + 1:]
            return pad.tobytes() + a[loud[0]:loud[-1] + 1].tobytes()
        if not len(loud):
            self._held = np.concatenate([self._held, a])
            return b""
        out = self._held.tobytes() + a[:loud[-1] + 1].tobytes()
        self._held = a[loud[-1] + 1:]
        return out

    def end(self) -> bytes:
        if not self._started:
            return self._head.tobytes()
        tail, self._held = self._held[:self._trail], self._held[:0]
        return tail.tobytes()


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #
def _checked_speed(speed) -> None:
    """Pocket has no rate control, so 1.0 is the only speed it can honour.
    Anything else is refused rather than spoken at 1.0: a setting that saves
    and changes nothing is AC-7b (AC-107)."""
    try:
        value = float(speed)
    except (TypeError, ValueError) as e:
        raise ValueError(f"pocket: speed must be a number, got {speed!r}") from e
    if not (math.isfinite(value) and value == 1.0):
        raise ValueError(
            f"pocket: refusing speed {speed!r}; Pocket TTS has no speed control "
            f"and already speaks at about Kokoro's 1.3x. Set Settings > "
            f"Speaking speed to 1.0")


@ports.register(ports.TTSS, "pocket")
class PocketTts:
    """Pocket TTS (kyutai, Rust, int8, CPU) behind the Tts port."""

    def __init__(self, voice: str = pocket_pin.DEFAULT_VOICE,
                 exe: str | None = None,
                 boot_timeout_s: float = BOOT_TIMEOUT_S,
                 synth_timeout_s: float = SYNTH_TIMEOUT_S):
        pocket_pin.split_voice(voice)       # a bad default is a boot error
        self.voice = voice
        self._exe = Path(exe) if exe else pocket_pin.EXE
        self._boot_timeout_s = boot_timeout_s
        self._synth_timeout_s = synth_timeout_s
        self._voices = pocket_pin.voice_entries()
        self._servers: dict[str, object] = {}
        # Abort bookkeeping, as tts_kokoro: a plain dict under a non-async
        # lock held for microseconds, so abort never waits on the synthesis
        # it cancels (D-4).
        self._inflight: dict[object, list] = {}
        self._aborted: set = set()
        self._reg_lock = threading.Lock()
        self._ids = itertools.count(1)

    def _server_for(self, model: str):
        """One server per language model, started on first use and kept: a
        conversation that moves between English and Portuguese does not pay
        a model load on every switch."""
        if model not in self._servers:
            self._servers[model] = _Server(self._exe, model, self._boot_timeout_s)
        return self._servers[model]

    async def synthesize(self, text: str, voice: str = "", *,
                         speed: float = 1.0,
                         handle=None) -> AsyncIterator[bytes]:
        """Yield 24 kHz mono int16 PCM as the server streams it."""
        if not text or not text.strip():
            raise ValueError("pocket: refusing to synthesize empty text")
        # Both refusals BEFORE a server is touched: nothing reaches the engine.
        _checked_speed(speed)
        model, name = pocket_pin.split_voice(voice or self.voice)
        base = await self._server_for(model).ensure()

        key = handle if handle is not None else ("anon", next(self._ids))
        job = {"resp": None}
        with self._reg_lock:
            self._inflight.setdefault(key, []).append(job)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15.0,
                                        sock_read=self._synth_timeout_s)
        form = aiohttp.FormData({"text": text, "voice_url": name})
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(base + "/tts", data=form) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:300]
                        raise PocketError(f"pocket /tts answered {resp.status} "
                                          f"for voice {name!r} ({model}): {body}")
                    job["resp"] = resp
                    wav, edges = WavReader(), EdgeTrimmer(RATE)
                    async for data in resp.content.iter_any():
                        if self._is_aborted(key):
                            return
                        out = edges.push(wav.push(data))
                        if out:
                            yield out
                    if self._is_aborted(key):
                        return
                    wav.end()
                    tail = edges.end()
                    if tail:
                        yield tail
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if self._is_aborted(key):
                return              # we closed the response on purpose
            raise PocketError(f"pocket synthesis failed ({model}): {e!r}") from e
        finally:
            with self._reg_lock:
                jobs = self._inflight.get(key, [])
                if job in jobs:
                    jobs.remove(job)
                if not jobs:
                    self._inflight.pop(key, None)
                    self._aborted.discard(key)

    async def abort(self, handle) -> None:
        """Cancel in-flight synthesis for `handle` (None means all) by closing
        its response: the server stops generating when the request drops."""
        with self._reg_lock:
            keys = list(self._inflight) if handle is None else [handle]
            victims = []
            for k in keys:
                if k in self._inflight:
                    self._aborted.add(k)
                    victims.extend(self._inflight[k])
        for job in victims:
            if job["resp"] is not None:
                job["resp"].close()

    def _is_aborted(self, key) -> bool:
        with self._reg_lock:
            return key in self._aborted

    async def warm(self) -> None:
        """Start the default voice's server and run one synthesis (D-10)."""
        async for _ in self.synthesize(WARM_TEXT, self.voice, handle=("warm",)):
            pass

    def close(self) -> None:
        for server in self._servers.values():
            server.stop()

    @property
    def info(self) -> dict:
        # Static: the voices are the pin's, so nothing is asked here (D-25).
        return {"engine": "pocket-tts", "voice": self.voice,
                "voices": [dict(v) for v in self._voices],
                "version": pocket_pin.RELEASE["version"], "device": "cpu",
                "sample_rate": RATE,
                "running": sorted(m for m, s in self._servers.items() if s.alive())}
