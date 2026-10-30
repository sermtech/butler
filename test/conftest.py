"""Shared pytest fixtures for Butler.

The spine of the Phase 1 test harness (plan.md section 5, Phase 1 step 2).
Everything here is deliberately protocol-level: it drives butlerd the way a
satellite does, over a real WebSocket, and asserts on the wire.

Two decisions are baked in here, and both came out of writing the tests first:

1. The protocol layer must be constructible WITHOUT the STT engine. Loading
   parakeet takes seconds and wants the GPU; a test that asserts "the daemon
   mints the turn id" should not pay for that or contend with whatever else
   is on the card. So the daemon honours BUTLER_TEST_STUB_STT=1 and swaps in
   a stub engine that emits scripted partials/finals. If that knob does not
   exist yet, these tests are red -- which is the point.

2. Tests never touch port 8140. The owner runs a real daemon there all day
   and a test suite that fights it is a test suite that lies (D-13).
"""
import asyncio
import json
import os
import shutil
import socket
import tempfile
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio
import aiohttp
from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
DAEMON = REPO / "daemon" / "butlerd.py"
BOOT_TIMEOUT_S = 30

# Hermetic against the owner's real `config/models.json`. The daemon fixtures
# inherit `os.environ`, so pointing the loader at a path that does not exist
# makes every in-process AND subprocess boot take the all-fakes default,
# whatever config the owner keeps on disk (test/AGENTS.md, the BUTLER_STATE
# rule one file over). A test that wants a real config sets BUTLER_CONFIG
# itself with monkeypatch.
os.environ["BUTLER_CONFIG"] = str(REPO / "test" / "assets" / "_no_config.json")
# TLS is the daemon's default when a cert exists, and this repo has one in
# `ssl/` -- so every fixture would boot https and the http test client would
# fail. Force plain HTTP for the suite, the BUTLER_CONFIG lesson one line up.
os.environ["BUTLER_TLS"] = "0"
# Hermetic against the owner's real `state/` too, and for a reason that only
# became visible on 2026-09-10: `build_hub` now reads the Recognition
# settings, so a suite sharing the owner's store BOOTS THEIR RECOGNIZER.
# Measured -- their `asr_model` is nemotron, and picking it up took the full
# suite from 4:04 to 17:54 and failed three real-recognizer tests on timeouts
# that had nothing to do with the code under test.
#
# Those settings were inert until that day (D-36), which is why the fixture
# below could say "this suite shares the owner's state directory" and be
# merely untidy rather than wrong. Wiring them made it wrong. The same
# module-scope trick as BUTLER_CONFIG and BUTLER_TLS above, for the same
# reason: the daemon fixtures inherit `os.environ`, so setting it here covers
# every in-process AND subprocess boot. A test that wants the real thing sets
# it itself with monkeypatch.
os.environ.setdefault("BUTLER_STATE",
                      tempfile.mkdtemp(prefix="butler-test-state-"))
# And never the owner's DATA ROOT (docs/data-root.md): a shell that exported
# BUTLER_DATA would otherwise make every fixture's root project the owner's
# home folder and put its attachments there. A test that wants one sets it.
os.environ.pop("BUTLER_DATA", None)
# Nor a live daemon's own address (docs/identity-memory.md 12.6): `main`
# writes BUTLER_SELF_URL for its children, and a shell that inherited it would
# give every fixture's harness child the memory extension pointing at that
# daemon. A test that wants one sets it.
os.environ.pop("BUTLER_SELF_URL", None)

sys.path.insert(0, str(REPO))

from daemon import adapters, ports            # noqa: E402,F401 (registration)
from daemon.connections import ResolvedTarget  # noqa: E402
from daemon.hub import Hub, SatelliteSession  # noqa: E402
from daemon.modes import ModeStore            # noqa: E402


def free_port() -> int:
    """Ask the OS for a port nobody is using, rather than hoping 8199 is free."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def silence_pcm(ms: int) -> bytes:
    """int16 mono 16 kHz silence. Enough to exercise framing and endpointing
    without needing a speech fixture."""
    return b"\x00\x00" * (16 * ms)


def tone_pcm(ms: int, amplitude: int = 6000) -> bytes:
    """A 220 Hz square-ish tone. Not speech -- STT will not transcribe it --
    but it is non-silent, so the energy gate and the utterance buffer see
    something. Use where the test cares about FRAMING, not words."""
    import array
    samples = array.array("h")
    period = 16000 // 220
    for i in range(16 * ms):
        samples.append(amplitude if (i % period) < period // 2 else -amplitude)
    return samples.tobytes()


class Satellite:
    """A scripted satellite: one WebSocket, the protocol v1 message set, and
    helpers that wait for a specific message instead of sleeping."""

    def __init__(self, ws):
        self.ws = ws
        self.seen = []          # every text message received, in order
        self.audio_chunks = []  # every binary frame received (TTS downstream)

    async def send(self, **payload):
        await self.ws.send_str(json.dumps(payload))

    async def send_pcm(self, pcm: bytes, frame_bytes: int = 1024):
        for i in range(0, len(pcm), frame_bytes):
            await self.ws.send_bytes(pcm[i:i + frame_bytes])

    async def next(self, kind: str, timeout: float = 10.0):
        """Wait for the next TEXT message of `kind`. Binary frames are
        collected, not skipped, so a test can assert on audio ordering."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {kind!r}; saw: "
                    f"{[m.get('type') for m in self.seen]}")
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                # wait_for fires before the deadline check above, so without
                # this the failure surfaces as a bare TimeoutError and the
                # test tells you nothing about what it DID receive. A harness
                # that hides the evidence is the D-13 failure mode.
                raise AssertionError(
                    f"timed out waiting for {kind!r} after {timeout}s; saw: "
                    f"{[m.get('type') for m in self.seen]}") from None
            if msg.type == aiohttp.WSMsgType.BINARY:
                self.audio_chunks.append(msg.data)
                continue
            if msg.type is not aiohttp.WSMsgType.TEXT:
                raise AssertionError(f"socket closed while waiting for {kind!r}")
            m = json.loads(msg.data)
            self.seen.append(m)
            if m.get("type") == kind:
                return m

    def types(self):
        return [m.get("type") for m in self.seen]


# --------------------------------------------------------------------------- #
# The hub-level harness.
#
# A few behaviours are invisible from one satellite's socket -- what a wake on
# the phone does to the desk, whether the abort reaches the speech engine, what
# the daemon records as SPOKEN. Those tests drive Hub objects directly instead
# of going over the wire, and these three pieces are what they all need. They
# live here rather than in each file because two copies of a stub socket drift,
# and a stub that drifts is a test that passes for the wrong reason.
# --------------------------------------------------------------------------- #
class StubWs:
    """A WebSocketResponse stand-in that just records what was written."""

    def __init__(self):
        self.closed = False
        self.sent = []          # text frames as str, audio frames as bytes

    async def send_str(self, s):
        self.sent.append(s)

    async def send_bytes(self, b):
        self.sent.append(b)

    async def close(self, code=None, message=b""):
        # The daemon's on_shutdown closes every page socket (DA-11).
        self.closed = True

    def text_types(self):
        """The `type` of every TEXT frame sent, in order."""
        return [json.loads(m)["type"] for m in self.sent if isinstance(m, str)]


def make_hub(tmp_path, **kw):
    """A Hub on all three fakes, with its mode file inside the test's tmp_path."""
    return Hub(asr=ports.build(ports.ASRS, "fake"),
               tts=ports.build(ports.TTSS, "fake"),
               brain=ports.build(ports.BRAINS, "fake"),
               modes=ModeStore(tmp_path / "mode.json"),
               asr_config=ports.AsrConfig(model="test"), **kw)


def attach_session(hub, name="desk"):
    """A registered session on a stub socket -- what the WS handler would build."""
    s = SatelliteSession(hub, StubWs())
    s.device_id = name
    hub.sessions.add(s)
    return s


def _drain(proc) -> None:
    """Read a booted daemon's piped output for the rest of its life.

    Nothing else reads it after boot, and an undrained pipe FILLS: the
    daemon's next `print` then blocks its event loop and a later test times
    out waiting for a frame (measured 2026-10-29: 16 more bytes per wake log
    line froze the session daemon at `test_correction.py`). Started only
    after the port opens, so the boot-failure paths still read it whole."""
    threading.Thread(target=lambda: proc.stdout.read(), daemon=True).start()


@pytest.fixture(scope="session")
def daemon_port():
    return free_port()


@pytest.fixture(scope="session")
def daemon(daemon_port):
    """Boot one butlerd for the whole session with a stubbed STT engine.

    Session-scoped on purpose: per-test daemon boots would dominate the run
    time and encourage writing fewer tests, which is the wrong incentive.
    There is no per-test daemon fixture. A test that must not see the
    session daemon's state should restore what it changed, the way
    test_models_api and the mode test do -- this suite shares the owner's
    state directory, so leaving a setting pointed somewhere dead breaks the
    next thing they say rather than the next test.
    """
    env = {
        **os.environ,
        "BUTLER_TEST_STUB_STT": "1",
        "BUTLER_PORT": str(daemon_port),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(DAEMON)],
        cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"daemon exited during boot (rc={proc.returncode}):\n"
                f"{proc.stdout.read() if proc.stdout else ''}")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", daemon_port)) == 0:
                break
        time.sleep(0.1)
    else:
        proc.kill()
        raise AssertionError(f"daemon did not open port {daemon_port} in {BOOT_TIMEOUT_S}s")
    _drain(proc)

    yield f"http://127.0.0.1:{daemon_port}"

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# --------------------------------------------------------------------------- #
# The live pi child (docs/harness-mode.md 7.2)
# --------------------------------------------------------------------------- #
# Which model a real `pi --mode rpc` child talks to. NOT a property of pi, and
# not a property of Butler either: the owner's registry decides what exists.
# claudebridge-local answered a turn in 1.55 s when section 1 was measured and
# its bridge was not listening when 7.2 was built, so the default is the fast
# bridged model that WAS up. One place, because two copies of a constant drift
# and the pi conformance row and the pi live test must talk to the same child.
# gpt-5.4-mini is STILL in the owner's ~/.pi/agent/models.json catalogue --
# this is not a missing model id -- but the live Codex API answers every call
# to it with 400 "The 'gpt-5.4-mini' model is not supported when using Codex
# with a ChatGPT account" [MEASURED 2026-09-17], which is a plan restriction
# on the account rather than a Butler defect. gpt-5.6-sol on the same
# provider and account was measured live and answering the same day.
PI_PROVIDER = os.environ.get("BUTLER_PI_TEST_PROVIDER", "codexbridge-local")
PI_MODEL = os.environ.get("BUTLER_PI_TEST_MODEL", "gpt-5.6-sol")

# pi's own vocabulary, passed as constructor kwargs because engine options
# never cross the port (AC-41). The system prompt is what makes a LIVE child
# reach the properties the conformance suite asserts: a scripted fake can be
# told to answer in several turns with a failing tool call, and a real agent
# has to be ASKED. Without it the suite would be measuring whether the model
# happened to feel chatty. No shell metacharacters: this goes through
# `cmd /c`.
PI_CONFORMANCE_PROMPT = (
    "You are terse. Before every tool call, first say one short sentence "
    "about what you are about to do. For any question you MUST first call "
    "the read tool on missing-on-purpose.txt, then answer in one sentence.")


def pi_kwargs(**overrides) -> dict:
    kwargs = {"provider": PI_PROVIDER, "model": PI_MODEL,
              "append_system_prompt": PI_CONFORMANCE_PROMPT}
    kwargs.update(overrides)
    return kwargs


def skip_without_pi() -> None:
    """Skip -- never fail -- when the real thing is not on this machine.

    Same posture as `real_stt_daemon`: an engine that has not been installed
    is not a failing contract. A provider that is installed and refusing is a
    different thing and is left to fail loudly.
    """
    if shutil.which("pi") is None:
        pytest.skip("pi is not installed on this machine (needs `pi --mode "
                    "rpc`, docs/harness-mode.md 7.2)")


def _adapter_registered(registry_name: str, adapter: str) -> bool:
    """Is an adapter importable and registered? Used to skip rather than fail
    when a real engine has not been written yet."""
    import importlib
    try:
        ports = importlib.import_module("daemon.ports")
        importlib.import_module("daemon.adapters")
        return adapter in getattr(ports, registry_name)
    except Exception:                                  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def real_stt_daemon(daemon_port):
    """A daemon running the REAL recognizer, with fake speech and brain.

    Separate from `daemon` on purpose. Almost every protocol assertion should
    NOT pay for a model load, but the handful that are about audio actually
    reaching the recognizer cannot be satisfied by a scripted fake -- it would
    return its script whether or not the bytes arrived, and the test would
    pass while the bug lived.
    """
    if not _adapter_registered("ASRS", "transcribecpp"):
        pytest.skip("the transcribecpp Asr adapter is not written yet")

    port = free_port()
    env = {
        **os.environ,
        "BUTLER_ASR": "transcribecpp",
        "BUTLER_TTS": "fake",
        "BUTLER_BRAIN": "fake",
        "BUTLER_PORT": str(port),
        "PYTHONUTF8": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(DAEMON)],
        cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # Model load gets its own budget, separate from the per-operation
    # watchdog. One shared timeout killed a worker that was merely still
    # loading, and the retry landed on a replacement that was also loading
    # -- cascade to silence (D-5).
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"real-stt daemon exited (rc={proc.returncode}):\n"
                f"{proc.stdout.read() if proc.stdout else ''}")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.kill()
        raise AssertionError("real-stt daemon did not open its port in 180s")
    _drain(proc)

    yield f"http://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest_asyncio.fixture
async def real_satellite(real_stt_daemon):
    """A connected satellite on the real-recognizer daemon."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{real_stt_daemon}/ws",
                                      max_msg_size=16 * 1024 * 1024) as ws:
            sat = Satellite(ws)
            await sat.send(type="hello", proto=1, device_id="test-real",
                           name="real", caps={"wake": True, "tts": True})
            await sat.next("ready", timeout=180)
            yield sat


@pytest_asyncio.fixture
async def satellite(daemon):
    """One connected satellite that has completed the hello/ready handshake."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{daemon}/ws", max_msg_size=16 * 1024 * 1024) as ws:
            sat = Satellite(ws)
            await sat.send(type="hello", proto=1, device_id="test-desk",
                           name="desk", caps={"wake": True, "tts": True})
            await sat.next("ready")
            yield sat


@pytest_asyncio.fixture
async def two_satellites(daemon):
    """Desk and phone, both connected and ready -- the arbitration fixture."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{daemon}/ws") as ws_a, \
                   session.ws_connect(f"{daemon}/ws") as ws_b:
            desk, phone = Satellite(ws_a), Satellite(ws_b)
            for sat, name in ((desk, "desk"), (phone, "phone")):
                await sat.send(type="hello", proto=1, device_id=f"test-{name}",
                               name=name, caps={"wake": True, "tts": True})
                await sat.next("ready")
            yield desk, phone


class StubTts:
    """Base for the single-behaviour Tts doubles the hub tests use.

    Lives here rather than being copied into each test file for the reason
    test/AGENTS.md gives about the stub socket: two copies drift, and a stub
    that drifts is a test passing for the wrong reason. It earned its keep
    immediately -- when `Tts.info` joined the port (AC-42) four doubles in
    two files broke at once, all with the same one-line gap.

    Subclasses override `synthesize` and nothing else. Everything the doubles
    were doing IDENTICALLY lives here: recording aborts so a test can assert
    barge-in reached the engine, a warm that answers, and the engine
    description. Voices are deliberately not Kokoro's, so a caller that reads
    them from the wrong adapter cannot look correct.

    Subclasses MUST call `super().__init__()`; forgetting it fails loudly on
    the first abort rather than silently recording nothing.
    """

    # `{id, language}` since AC-155, and two languages for the reason
    # `tts_fake` gives: one language cannot tell a working filter from a
    # missing one.
    VOICES = ({"id": "stub_a", "language": "en-US"},
              {"id": "stub_b", "language": "pt-BR"})

    def __init__(self):
        self.aborted = []

    async def abort(self, handle) -> None:
        self.aborted.append(handle)

    async def warm(self) -> None:
        return None

    @property
    def info(self) -> dict:
        return {"engine": "stub", "voice": self.VOICES[0]["id"],
                "voices": [dict(v) for v in self.VOICES],
                "sample_rate": ports.SAMPLE_RATE_OUT}


class QuietTts(StubTts):
    """Says the words and produces no audio.

    For a test whose assertions are all on the `reply` frames -- what Butler
    SAID -- where real chunks would make every turn wait out the playback gate
    (`audio_ms_sent` plus two seconds) for nothing. Here rather than in one
    test file because three suites need it -- chats, the project switch
    and the conversation -- and two copies of a stub drift (test/AGENTS.md).
    """

    def __init__(self):
        super().__init__()
        self.said: list[str] = []

    # `speed` is accepted and ignored: a double with no rate control
    # honestly honours 1.0 and nothing else (AC-107, ports.Tts).
    async def synthesize(self, text, voice, *, speed=1.0, handle=None):
        self.said.append(text)
        if False:                                  # pragma: no cover
            yield b""


# --------------------------------------------------------------------------- #
# A fake OpenAI-compatible server, and a resolver pointed at it.
#
# AC-38 is "the cap is on the WIRE", and the only instrument that can tell that
# apart from "the cap is in the settings file" is a server that records the
# body it was sent. Everything the router and endpoint mode do lands here.
# --------------------------------------------------------------------------- #
class Ask:
    """One request this server received, as it arrived."""

    def __init__(self, path: str, body: dict, authorization: str | None):
        self.path = path
        self.body = body
        self.authorization = authorization

    def __repr__(self):                                   # pragma: no cover
        return f"Ask(path={self.path!r}, body={self.body!r})"


class FakeOpenAI:
    """An OpenAI-compatible endpoint that answers a script and records asks.

    Runs in its own thread with its own event loop, so a synchronous fixture
    (the conformance suite's) and an async test can both use it, and neither
    has to share the loop under test. Nothing here is a Butler component --
    it is the far side of the wire.
    """

    def __init__(self):
        self.port = free_port()
        self.asks: list[Ask] = []
        self.script: list[dict] = []
        self.chunk_delay_s = 0.0     # per streamed chunk, for barge-in tests
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    # -- what a test says it will answer ----------------------------------- #
    def answer(self, content: str = "", finish_reason: str = "stop",
               reasoning: str = "", status: int = 200, body: str = "",
               tool_calls: list | None = None):
        """Queue one answer. Answers are consumed in order.

        `tool_calls` is `[{id, name, arguments}]`; streamed, each arrives in
        FRAGMENTS (id and name first, the arguments split in two) and the
        finish is `tool_calls`, the OpenAI shape `llm._read_sse` assembles."""
        self.script.append({"content": content,
                            "finish_reason": ("tool_calls" if tool_calls
                                              else finish_reason),
                            "reasoning": reasoning, "status": status,
                            "body": body, "tool_calls": tool_calls or []})

    def verdict(self, **fields):
        """Queue one router verdict, as a schema-constrained server would."""
        self.answer(json.dumps(fields))

    def _default(self, body: dict) -> dict:
        """What an unscripted call gets.

        A server constrained by a json_schema answers JSON, so the default
        follows the shape it was ASKED for rather than a fixed string -- a
        default that answered prose to a schema-constrained request would
        make every unscripted router call look like a parse failure.
        """
        if (body.get("response_format") or {}).get("type") == "json_schema":
            return {"content": json.dumps({"route": "converse"}),
                    "finish_reason": "stop", "reasoning": "", "status": 200,
                    "body": ""}
        return {"content": "This is the default answer.",
                "finish_reason": "stop", "reasoning": "", "status": 200,
                "body": ""}

    # -- the wire ----------------------------------------------------------- #
    @property
    def base_url(self) -> str:
        """WITH the version, which is what pi's registry stores (models.py)."""
        return f"http://127.0.0.1:{self.port}/v1"

    def last_body(self) -> dict:
        assert self.asks, "the server was never asked for anything"
        return self.asks[-1].body

    async def _chat(self, request):
        body = await request.json()
        self.asks.append(Ask(str(request.rel_url), body,
                             request.headers.get("Authorization")))
        answer = self.script.pop(0) if self.script else self._default(body)
        if answer["status"] != 200:
            return web.Response(status=answer["status"],
                                text=answer["body"] or "refused")
        if body.get("stream"):
            return await self._stream(request, answer)
        message = {"role": "assistant", "content": answer["content"]}
        if answer["reasoning"]:
            message["reasoning_content"] = answer["reasoning"]
        return web.json_response(
            {"choices": [{"index": 0, "message": message,
                          "finish_reason": answer["finish_reason"]}]})

    async def _stream(self, request, answer):
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)

        async def frame(delta: dict, finish=None):
            payload = {"choices": [{"index": 0, "delta": delta,
                                    "finish_reason": finish}]}
            await response.write(f"data: {json.dumps(payload)}\n\n".encode())

        if answer["reasoning"]:
            await frame({"reasoning_content": answer["reasoning"]})
        # Word by word, because a reply assembled from one chunk would never
        # exercise the sentence buffer that decides when Butler starts
        # speaking.
        pieces = [w for w in (answer["content"] or "").split(" ") if w]
        for i, piece in enumerate(pieces):
            await frame({"content": piece + (" " if i < len(pieces) - 1 else "")})
            if self.chunk_delay_s:
                await asyncio.sleep(self.chunk_delay_s)
        for n, call in enumerate(answer.get("tool_calls") or []):
            args = call["arguments"]
            half = len(args) // 2
            await frame({"tool_calls": [{"index": n, "id": call["id"],
                                         "type": "function",
                                         "function": {"name": call["name"],
                                                      "arguments": args[:half]}}]})
            await frame({"tool_calls": [{"index": n, "function":
                                         {"arguments": args[half:]}}]})
        await frame({}, finish=answer["finish_reason"])
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def _models(self, request):
        self.asks.append(Ask(str(request.rel_url), {},
                             request.headers.get("Authorization")))
        return web.json_response(
            {"data": [{"id": "fake-router"}, {"id": "fake-reply"}]})

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="fake-openai")
        self._thread.start()
        assert self._ready.wait(10), "fake OpenAI server did not start"
        return self

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._chat)
        app.router.add_get("/v1/models", self._models)
        runner = web.AppRunner(app)
        self._loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", self.port)
        self._loop.run_until_complete(site.start())
        self._ready.set()
        self._loop.run_forever()
        self._loop.run_until_complete(runner.cleanup())
        self._loop.close()

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)


class StubResolver:
    """A `TargetResolver` (daemon/connections.py) over one fake endpoint.

    Roles are the ones the real resolver serves -- "router" and "reply"; an
    image goes to "reply" (AC-815) -- and an unknown role raises KeyError
    here for the same reason it does there: a typo must not resolve to
    something plausible.
    """

    def __init__(self, base_url: str, models: dict | None = None,
                 api_key: str | None = None, caps: dict | None = None,
                 reasoning: dict | None = None):
        self.base_url = base_url
        self.models = {"router": "fake-router", "reply": "fake-reply",
                       **(models or {})}
        self.api_key = api_key
        self.caps = {"router": None, "reply": None, **(caps or {})}
        self.reasoning = {"router": True, "reply": True, **(reasoning or {})}

    def resolve(self, role: str) -> ResolvedTarget:
        if role not in self.models:
            raise KeyError(f"unknown role {role!r}")
        return ResolvedTarget(base_url=self.base_url, model=self.models[role],
                              api_key=self.api_key,
                              max_tokens=self.caps.get(role),
                              reasoning=self.reasoning.get(role, True))


@pytest.fixture
def fake_openai():
    """A fresh server per test: the ask list IS the assertion in most of them."""
    server = FakeOpenAI().start()
    try:
        yield server
    finally:
        server.stop()


# Deliberately conspicuous. If either ever appears in a response body or
# under state/, the assertion quotes it and the reason is obvious at a glance.
PI_KEY = "PI-FIXTURE-KEY-MUST-NEVER-LEAK"
ENV_KEY = "ENV-FIXTURE-KEY-MUST-NEVER-LEAK"
ENV_VAR = "BUTLER_TEST_CONN_KEY"


@pytest.fixture(scope="module")
def isolated_daemon(tmp_path_factory):
    """A daemon with its OWN state directory and a FIXTURE pi registry.

    The deliberate exception to "there is no per-test daemon" above. The
    session daemon runs against the owner's real `state/` and their real pi
    registry; a test that writes a connection there has done damage a cleanup
    cannot undo, and one that reads the real registry puts real cloud keys in
    a test process. Module-scoped, so a file that needs it pays one boot.
    """
    state = tmp_path_factory.mktemp("state")
    registry = tmp_path_factory.mktemp("pi") / "models.json"
    registry.write_text(json.dumps({"providers": {
        "llama-swap": {"api": "openai", "baseUrl": "http://127.0.0.1:9/v1",
                       "apiKey": PI_KEY,
                       "models": [{"id": "declared-but-never-served"}]},
        "kimi": {"api": "openai", "baseUrl": "https://api.kimi.example/v1",
                 "apiKey": PI_KEY, "models": []},
    }}), encoding="utf-8")

    port = free_port()
    env = {**os.environ,
           "BUTLER_TEST_STUB_STT": "1",
           "BUTLER_PORT": str(port),
           "BUTLER_STATE": str(state),
           "BUTLER_PI_REGISTRY": str(registry),
           "BUTLER_ENDPOINTER": "none",
           ENV_VAR: ENV_KEY,
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-X", "utf8", str(DAEMON)],
                            cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    import urllib.error
    import urllib.request
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"daemon exited {proc.returncode}: {proc.stdout.read()[:2000]}")
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        proc.kill()
        raise AssertionError("isolated daemon never became healthy")
    _drain(proc)
    yield base, state, registry
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


