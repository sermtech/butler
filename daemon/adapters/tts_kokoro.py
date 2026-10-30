"""The real Tts: the Kokoro node sidecar behind the port.

The daemon owns the sidecar as a child process: this adapter spawns
`engines/tts/tts-sidecar.mjs`, health-checks it, drains its output so a node
stack trace is never lost, and shuts it down. The sidecar and the four modules
under it are NOT touched -- they are the working, measured engine.

ONE CHUNK PER SENTENCE, AND WHY THAT TOOK A TRANSPORT REWRITE
-------------------------------------------------------------
`tts-service.js` has always produced one PCM16 chunk per sentence
(`synthesizeStream`), which is exactly the granularity the port wants, and it
has always cancelled between sentences when its consumer stops reading. The
sidecar threw both away: it drained the generator into a single WAV and
answered one HTTP request with it. MEASURED 2026-08-16: 3925 ms to first byte
over HTTP versus 673 ms direct on a 3-sentence reply. MEASURED 2026-08-17: a
reply issued after an abort waited 7887 ms, because the abandoned synthesis
still owned the single kokoro worker.

Both are gone. The sidecar now streams framed PCM and takes an abort by job
id (plan.md section 5 step 8), so this adapter yields once per SENTENCE, its
`abort` reaches the ENGINE rather than only the daemon's own wait, and `voice`
is per call rather than bound once from KOKORO_VOICE at boot.

THE WIRE FORMAT, WHICH THIS FILE IS ONE OF TWO ENDS OF
------------------------------------------------------
16-byte header then payload: magic "BTL1", kind (0 audio, 1 error, 2 end),
sample rate, payload length -- all little-endian uint32. Framed rather than a
bare chunked body because no HTTP client preserves chunk boundaries, because a
failure after the 200 must still arrive as an error instead of a reply that
just stops, and because an explicit end frame is what makes a TRUNCATED stream
detectable. A silently short reply is the worst of the three: it sounds like a
complete answer.

TIMEOUTS ARE SPLIT (D-5, plan.md section 6)
-------------------------------------------
Three budgets, never one: BOOT (node is listening), COLD START (the first
synthesis, which is what actually loads kokoro -- the sidecar's model load is
lazy, so its "ready" line means the HTTP server is up and nothing more), and
the short watchdog once warm. One shared watchdog SIGKILLed a worker that was
merely still loading and the retry landed on a replacement that was also still
loading: cascade to silence.

The warm budget is now per FRAME rather than per request (`sock_read`), which
is the only honest way to bound a stream: a total timeout on a long reply
either cuts off a healthy synthesis or is too loose to catch a wedged one.
What we are actually watching for is the engine going quiet, and that is a
gap between frames.
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
from pathlib import Path
from typing import AsyncIterator



import aiohttp

from .. import childproc, ports

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[2]
SIDECAR = _HERE.parents[1] / "engines" / "tts" / "tts-sidecar.mjs"
_ENGINES = _HERE.parents[1] / "engines"


def _winqos():
    """engines/winqos.py (a standalone module, like the other engines)."""
    if str(_ENGINES) not in sys.path:
        sys.path.insert(0, str(_ENGINES))
    import winqos  # noqa: PLC0415
    return winqos


DEFAULT_VOICE = "af_heart"
# There was an ENGLISH_PREFIXES tuple here until 2026-09-10 (AC-155). It read
# `("af_", "am_", "bf_", "bm_")` and filtered the offered voices down to the
# English packs, with the reason that "the recognizer is configured for
# English, so a picker offering voices Butler cannot be SPOKEN TO in is a
# control that half works". The reasoning was right and is kept; the
# MECHANISM was the problem twice over. It hardcoded one answer to a question
# that is now the owner's (`language`), and it inferred a language from a
# NAME -- so the daemon held one engine's naming convention, which is what
# AC-41's gate forbids on the other side of this seam.
#
# What replaced it: the pool reports `{id, language}` per voice and this
# adapter passes that through. WHICH of them to offer is the daemon's
# decision, made against the one language setting, and it is not made here --
# an adapter that filtered by the owner's preference would be reading policy
# it cannot see (4.10: no adapter reads the settings store).
# There is deliberately NO voice list in this file. It held eleven names until
# AC-108 while the pool reported 28 [MEASURED 2026-08-19] -- the picker was
# honest about what the daemon declared and the daemon under-declared by a
# factor of two and a half, which is AC-42's complaint exactly: a list written
# into the daemon is wrong the day the engine changes, and wrong invisibly.
# `warm()` asks the sidecar and `info` serves what it was told.
# Something short and real: the sidecar rejects empty text with 400, and a
# silent warm-up would not exercise the ONNX graph anyway (D-10).
WARM_TEXT = "Ready."

BOOT_TIMEOUT_S = 60.0          # node process up and the HTTP port listening
COLD_START_TIMEOUT_S = 180.0   # first frame: kokoro model load (measured ~1.3 s, budgeted wide)
SYNTH_TIMEOUT_S = 30.0         # gap between frames once warm
ABORT_TIMEOUT_S = 2.0          # telling the sidecar to stop must never hang barge-in

# The frame header the sidecar writes. Kept beside the reader that parses it;
# the other end is documented in tts-sidecar.mjs.
FRAME_MAGIC = b"BTL1"
FRAME_HEADER_BYTES = 16
KIND_AUDIO = 0
KIND_ERROR = 1
KIND_END = 2


class SidecarError(RuntimeError):
    """The sidecar would not start, or answered with an error.

    Raised loudly on purpose: a TTS engine that cannot speak must never
    degrade to silence, because silence is indistinguishable from Butler
    ignoring the user.
    """


def _free_port() -> int:
    """Ask the OS for a port. The owner runs a real sidecar on 8141 all day
    and a test suite that fights it is a test suite that lies (D-13)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Sidecar:
    """One `node tts-sidecar.mjs` child process, shared by every adapter
    instance built for the same voice.

    Shared on purpose: kokoro is one worker pool holding an ~80 MB ONNX model,
    and the daemon owns ONE of them. Per-instance processes would also make
    the conformance suite spawn a fresh node + model load per test.
    """

    def __init__(self, voice: str, node: str, port: int | None):
        self.voice = voice
        self.node = node
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.model_loaded = False      # flips after the first successful synthesis
        # What the POOL said it can speak, filled by `KokoroTts._learn_voices`
        # after the first synthesis and served by `info` (AC-108). It lives on
        # the sidecar rather than the adapter because it is a fact about the
        # ENGINE: two adapters sharing this pool share its voices, and a
        # restart of the child does not change them, which is why `stop()`
        # does not clear it.
        self.voices: list[dict] = []      # [{id, language}] (AC-155)
        self._log = collections.deque(maxlen=200)
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self, boot_timeout_s: float) -> None:
        """Spawn and health-check. Blocking; call it from a worker thread."""
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            if not SIDECAR.exists():
                raise SidecarError(f"sidecar missing: {SIDECAR}")
            port = self.port or _free_port()
            # BUTLER_PYTHON: the sidecar's kokoro workers are forked by node, so
            # it shells out to engines/winqos.py with THIS interpreter to opt
            # each one out of power throttling (not inherited: measured).
            env = dict(os.environ, SIDECAR_PORT=str(port),
                       KOKORO_VOICE=self.voice, BUTLER_PYTHON=sys.executable)
            self.proc = subprocess.Popen(
                [self.node, str(SIDECAR)],
                cwd=str(REPO_ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            # Before anything else, because the sidecar forks its kokoro
            # worker within milliseconds and a grandchild born before the
            # assignment is outside the job (AC-36, D-24). `_stop_all_sidecars`
            # below is the clean-exit path; nothing runs atexit under a
            # TerminateProcess, which is how the test harness stops every
            # daemon it boots -- and how seven of these leaked in one day.
            childproc.assign(self.proc)
            _winqos().apply("kokoro sidecar", self.proc.pid)
            self.port = port
            self._ready.clear()
            threading.Thread(target=self._drain, daemon=True,
                             name="kokoro-sidecar-log").start()
            if not self._ready.wait(boot_timeout_s):
                tail = self.tail()
                self._stop_locked()
                raise SidecarError(
                    f"sidecar did not report ready in {boot_timeout_s}s"
                    f"{tail}")
            # The ready line is printed from the listen() callback, so the
            # socket is up by then; connect anyway rather than trust a print.
            with socket.socket() as s:
                s.settimeout(5.0)
                if s.connect_ex(("127.0.0.1", self.port)) != 0:
                    tail = self.tail()
                    self._stop_locked()
                    raise SidecarError(
                        f"sidecar printed ready but port {self.port} refuses "
                        f"connections{tail}")

    def _drain(self) -> None:
        """Echo the child's output to stderr and keep the tail.

        Without this the pipe fills and node blocks mid-synthesis, and a
        kokoro stack trace -- the thing that tells you WHY there is no audio --
        is thrown away.
        """
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.rstrip()
            self._log.append(line)
            if "TTS ready on http" in line:
                self._ready.set()
            print(f"[kokoro] {line}", file=sys.stderr, flush=True)
        # The child exited: unblock anyone waiting on boot so they fail with
        # the log instead of sitting out the full timeout.
        self._ready.set()

    def tail(self) -> str:
        if not self._log:
            return " (no output)"
        return ":\n  " + "\n  ".join(self._log)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self.proc = self.proc, None
        self.model_loaded = False
        self._ready.clear()
        if proc is None or proc.poll() is not None:
            return
        # Kill the TREE, not the sidecar. tts-service.js forks a kokoro
        # worker child holding the ~80 MB model and its ONNX threads; an
        # orphan of that survives its parent and shows up as unexplained
        # RSS in the overnight soak (AC-7).
        childproc.kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def check_alive(self) -> None:
        if not self.alive():
            raise SidecarError(f"sidecar is not running{self.tail()}")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/tts"

    @property
    def abort_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/abort"

    @property
    def voices_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/voices"


# Keyed by (node, requested port): one kokoro pool per distinct endpoint. The
# voice is NOT part of the key -- it is chosen per request now, so two adapters
# with different default voices share one ~80 MB model instead of loading two.
_SIDECARS: dict[tuple, _Sidecar] = {}
_SIDECARS_LOCK = threading.Lock()


def _sidecar_for(voice: str, node: str, port: int | None) -> _Sidecar:
    key = (node, port)
    with _SIDECARS_LOCK:
        sc = _SIDECARS.get(key)
        if sc is None:
            sc = _Sidecar(voice, node, port)
            _SIDECARS[key] = sc
        return sc


@atexit.register
def _stop_all_sidecars() -> None:
    with _SIDECARS_LOCK:
        sidecars = list(_SIDECARS.values())
        _SIDECARS.clear()
    for sc in sidecars:
        try:
            sc.stop()
        except Exception as e:  # noqa: BLE001 -- shutdown must not mask the real exit
            print(f"[kokoro] shutdown: {e}", file=sys.stderr, flush=True)


def parse_frame_header(header: bytes) -> tuple[int, int, int]:
    """(kind, sample_rate, payload_length) from a 16-byte frame header.

    Checked rather than assumed, because every failure this catches is one
    that would otherwise be found BY EAR. A frame boundary read at the wrong
    offset turns the rest of the reply into noise; a rate that quietly became
    22 050 makes Butler sound slightly wrong and nothing else.
    """
    if len(header) != FRAME_HEADER_BYTES or header[:4] != FRAME_MAGIC:
        raise SidecarError(
            f"lost frame sync: expected {FRAME_MAGIC!r}, got {header[:8]!r}")
    kind = int.from_bytes(header[4:8], "little")
    rate = int.from_bytes(header[8:12], "little")
    length = int.from_bytes(header[12:16], "little")
    if kind == KIND_AUDIO:
        if rate != ports.SAMPLE_RATE_OUT:
            raise SidecarError(
                f"sidecar sent audio at {rate} Hz; the port is "
                f"{ports.SAMPLE_RATE_OUT} Hz mono int16")
        if length % 2:
            raise SidecarError(
                f"audio frame is {length} bytes, not int16 aligned")
    return kind, rate, length


def _checked_speed(speed) -> float:
    """Refuse a speaking speed the engine cannot honour, LOUDLY (AC-107).

    kokoro takes this number as a float32 tensor and validates nothing, so a
    zero, a negative or a NaN is not an error the engine will report -- it is
    audio that comes out wrong, or does not come out. Clamping silently would
    be the worse failure of the two: the owner sets a speed, hears no change,
    and learns that the panel is decoration (AC-7b).

    No upper bound is invented here. Which range stays intelligible is
    unmeasured, and `docs/voice-control.md` section 6 deliberately leaves it
    open, because the same number moves `speech.py`'s word caps -- those bound
    MILLISECONDS for AC-4 and AC-5, and speed is exactly the constant that
    breaks a words-to-seconds proxy. A cap picked here would be a guess
    wearing a uniform.
    """
    try:
        value = float(speed)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"kokoro: speed must be a number, got {speed!r}") from e
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"kokoro: refusing to synthesize at speed {speed!r}; the engine "
            f"takes a positive, finite multiple of its own rate")
    return value


class _Job:
    """One in-flight synthesis: the id the sidecar knows it by, and the
    response to tear down when it is abandoned."""

    __slots__ = ("job_id", "resp")

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.resp = None


@ports.register(ports.TTSS, "kokoro")
class KokoroTts:
    """Kokoro (kokoro-js, fp32, CPU) behind the Tts port."""

    def __init__(self, voice: str = DEFAULT_VOICE, node: str = "node",
                 port: int | None = None,
                 boot_timeout_s: float = BOOT_TIMEOUT_S,
                 cold_start_timeout_s: float = COLD_START_TIMEOUT_S,
                 synth_timeout_s: float = SYNTH_TIMEOUT_S):
        # The voice a caller gets when it passes none, and the one the sidecar
        # pre-warms. Not a binding: every request names its own (AC-7b).
        self.voice = voice
        self._boot_timeout_s = boot_timeout_s
        self._cold_start_timeout_s = cold_start_timeout_s
        self._synth_timeout_s = synth_timeout_s
        self._sidecar = _sidecar_for(voice, node, port)
        # Abort bookkeeping. A plain dict plus a NON-async lock, held only for
        # the microseconds it takes to mutate it: abort() must never wait on
        # anything the synthesis it cancels is holding (D-4).
        self._inflight: dict[object, list[_Job]] = {}
        self._aborted: set[object] = set()
        self._reg_lock = threading.Lock()
        self._ids = itertools.count(1)

    # -- port ----------------------------------------------------------------
    async def synthesize(self, text: str, voice: str = "", *,
                         speed: float = 1.0,
                         handle=None) -> AsyncIterator[bytes]:
        """Yield 24 kHz mono int16 PCM, one chunk per sentence.

        `handle` names this synthesis so `abort` can find it, and it is part
        of the port's contract rather than an adapter extension: a handle-less
        abort matches nothing and reads as working. Callers that omit it get
        an anonymous handle, reachable only through `abort(None)`.

        `speed` is the speaking rate (AC-107). It goes on the `/tts` body and
        reaches `kokoro-js`'s own `stream({voice, speed})`, which has taken it
        all along; the default is sent explicitly rather than omitted, so the
        engine is never left to decide it.
        """
        if not text or not text.strip():
            # Loud, never a silent skip: an empty segment means the splitter
            # upstream is broken, and swallowing it drops half an answer.
            raise ValueError("kokoro: refusing to synthesize empty text")
        # BEFORE the sidecar is touched: a refused speed must not spawn a
        # child, and the test that proves it asserts the engine received
        # nothing at all.
        speed = _checked_speed(speed)
        await self._ensure_started()

        key = handle if handle is not None else ("anon", next(self._ids))
        job = _Job(f"{id(self):x}-{next(self._ids)}")
        self._register(key, job)
        try:
            async for chunk in self._stream(text, voice or self.voice,
                                            speed, job):
                # Checked between frames as well as by tearing the socket
                # down, so an abort lands even when the next sentence is
                # already on the wire.
                if self._is_aborted(key):
                    return
                yield chunk
        except (aiohttp.ClientError, asyncio.TimeoutError,
                asyncio.IncompleteReadError) as e:
            if self._is_aborted(key):
                return          # we tore the connection down on purpose
            self._sidecar.check_alive()
            raise SidecarError(f"kokoro synthesis failed: {e!r}"
                               f"{self._sidecar.tail()}") from e
        finally:
            self._unregister(key, job)
            self._clear_aborted(key)

    async def _stream(self, text: str, voice: str, speed: float,
                      job: "_Job") -> AsyncIterator[bytes]:
        """POST /tts and read framed PCM until the end frame."""
        # Cold start and the warm watchdog are DIFFERENT budgets (D-5): until
        # a synthesis has completed once, this request is what loads the
        # model. Both are applied to the gap between frames rather than to the
        # whole response, because a long reply is not a stuck one.
        gap = (self._synth_timeout_s if self._sidecar.model_loaded
               else self._cold_start_timeout_s)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15.0,
                                        sock_read=gap)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                    self._sidecar.url,
                    json={"text": text, "voice": voice, "speed": speed,
                          "id": job.job_id}
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:400]
                    raise SidecarError(
                        f"sidecar returned {resp.status}: {body}"
                        f"{self._sidecar.tail()}")
                job.resp = resp
                while True:
                    kind, _rate, length = parse_frame_header(
                        await resp.content.readexactly(FRAME_HEADER_BYTES))
                    payload = (await resp.content.readexactly(length)
                               if length else b"")
                    if kind == KIND_AUDIO:
                        self._sidecar.model_loaded = True
                        yield payload
                    elif kind == KIND_END:
                        return
                    elif kind == KIND_ERROR:
                        # The 200 is already sent by the time kokoro can fail,
                        # so the failure travels in the stream. Raising here is
                        # what stops it reading as a short answer.
                        raise SidecarError(
                            f"kokoro synthesis failed: "
                            f"{payload.decode('utf-8', 'replace')}")
                    else:
                        raise SidecarError(f"unknown frame kind {kind}")

    async def abort(self, handle) -> None:
        """Cancel in-flight synthesis for `handle` (None means all).

        Three things, in this order, and the order is the point:

        1. mark the handle, so `synthesize` ends quietly rather than raising
           on the socket we are about to tear down;
        2. tell the SIDECAR, on its own connection and its own route, so the
           kokoro worker stops between sentences. Without this step abort
           frees the caller and not the engine: the abandoned reply keeps the
           single worker, and the next answer queues behind it -- MEASURED at
           7887 ms before this route existed;
        3. close the response, so a generator parked on the next frame wakes
           immediately instead of waiting out the sentence in flight.

        Dispatched outside every queue (D-4): it holds no lock across an
        await, and step 2 opens its own connection rather than reusing the one
        the synthesis is streaming on, so it can never end up behind the work
        it cancels.
        """
        with self._reg_lock:
            keys = list(self._inflight) if handle is None else [handle]
            victims: list[_Job] = []
            for k in keys:
                if k in self._inflight:
                    self._aborted.add(k)
                    victims.extend(self._inflight[k])
        for job in victims:
            await self._tell_sidecar_to_stop(job)
            if job.resp is not None:
                job.resp.close()        # synchronous, immediate

    async def _tell_sidecar_to_stop(self, job: "_Job") -> None:
        """POST /abort for one job. Bounded and never fatal.

        Barge-in must not hang on a wedged sidecar: the audio has to stop
        whatever the engine does, and failing to cancel costs latency on the
        next reply rather than correctness of this one (D-1).
        """
        try:
            async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=ABORT_TIMEOUT_S)
            ) as session:
                async with session.post(self._sidecar.abort_url,
                                        json={"id": job.job_id}):
                    pass
        except Exception as e:                        # noqa: BLE001
            print(f"[kokoro] abort of job {job.job_id} did not reach the "
                  f"sidecar: {e!r}; the engine may finish the abandoned reply",
                  file=sys.stderr, flush=True)

    async def warm(self) -> None:
        """Pay the cold start at boot, not mid-conversation (D-10).

        Measured 2026-08-16: node boot to "ready" is fast because the sidecar
        loads kokoro LAZILY -- the first POST costs ~1.5 s of model load on top
        of synthesis. That is the cost being moved to boot here.

        It is also where the voice list is learned (AC-108). Boot calls this
        before the first satellite handshake, so by the time anything reads
        `info` the answer is already in memory.
        """
        async for _ in self.synthesize(WARM_TEXT, self.voice, handle=("warm",)):
            pass
        await self._learn_voices()

    async def _learn_voices(self) -> None:
        """Ask the ENGINE what it can speak, once, so `info` never has to.

        AC-108 and D-25 together are the whole shape of this method. `info` is
        a synchronous property read ON THE EVENT LOOP during every satellite
        handshake, so it may not go and look; the list therefore has to be
        fetched somewhere that can afford to wait and cached for it.

        Here rather than in `_Sidecar.start` because the pool reads the names
        off the LOADED MODEL: asking at start would pay the model load on the
        boot budget, and cold start and boot are separate budgets on purpose
        (D-5). By the time the warm synthesis above has returned, the model is
        up and this is an in-memory read at the far end.

        LOUD on failure, and never an empty list: a picker rendered empty as
        though all were well is how a setting silently stops working (the rule
        `butlerd.LocalListUnavailable` and `models.ModelsUnavailable` already
        hold). Raising here makes it `butlerd.warmup`'s printed WARNING, which
        is loud without being fatal -- a daemon that will not start leaves the
        owner with no microphone at all (D-1).
        """
        timeout = aiohttp.ClientTimeout(total=self._synth_timeout_s)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._sidecar.voices_url) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:400]
                        raise SidecarError(
                            f"sidecar would not list its voices: "
                            f"{resp.status}: {body}{self._sidecar.tail()}")
                    payload = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise SidecarError(
                f"could not ask the sidecar what it can speak: {e!r}"
                f"{self._sidecar.tail()}") from e
        # `{id, language}` per voice since AC-155, and the id is the only
        # part this adapter requires: an engine that cannot say what language
        # a voice speaks reports "" and is offered unfiltered, which is the
        # honest degradation. Anything without an id is not a voice.
        reported = [
            {"id": v["id"], "language": str(v.get("language") or "")}
            for v in (payload.get("voices") or [])
            if isinstance(v, dict) and isinstance(v.get("id"), str) and v["id"]
        ]
        if not reported:
            raise SidecarError(
                f"the sidecar listed no usable voice; it answered "
                f"{payload.get('voices')!r}, and a voice needs an id")
        self._sidecar.voices = reported

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        """Shut the child process down cleanly. Safe to call twice."""
        self._sidecar.stop()

    @property
    def info(self) -> dict:
        # `voice` is this adapter's DEFAULT; `voices` is everything it can
        # speak. Both are reported because they answer different questions --
        # "what will I get if I name none" and "what may I choose".
        #
        # `voices` is a CACHE READ and never a question (D-25): it is what the
        # pool told `_learn_voices` at warm, and it is empty until then. Empty
        # therefore means "this engine has not answered yet", which is a state
        # the daemon only reaches when warmup already printed why (AC-108).
        return {"engine": "kokoro-js", "voice": self.voice,
                "voices": list(self._sidecar.voices), "dtype": "fp32",
                "device": "cpu", "sample_rate": ports.SAMPLE_RATE_OUT,
                "port": self._sidecar.port, "running": self._sidecar.alive()}

    # -- internals -----------------------------------------------------------
    async def _ensure_started(self) -> None:
        if self._sidecar.alive():
            return
        # Blocking spawn + health check, off the event loop. Its budget is the
        # BOOT budget, which is not the synthesis budget (D-5).
        await asyncio.to_thread(self._sidecar.start, self._boot_timeout_s)

    def _register(self, key, job: "_Job") -> None:
        with self._reg_lock:
            self._inflight.setdefault(key, []).append(job)

    def _unregister(self, key, job: "_Job") -> None:
        with self._reg_lock:
            lst = self._inflight.get(key)
            if lst and job in lst:
                lst.remove(job)
            if lst is not None and not lst:
                del self._inflight[key]

    def _is_aborted(self, key) -> bool:
        with self._reg_lock:
            return key in self._aborted

    def _clear_aborted(self, key) -> None:
        """Forget that this handle was aborted.

        The CLEARING is the point, which is why this returns nothing: a mark
        left behind would silence the next reply on the same turn id, and
        aborting turn N must never silence turn N+1.
        """
        with self._reg_lock:
            self._aborted.discard(key)
