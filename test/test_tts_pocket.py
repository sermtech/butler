"""Properties of the Pocket TTS adapter that the port cannot state.

`test/conformance/test_tts_conformance.py` holds what every Tts must do; the
real binary runs there under `slow`. This file holds what is particular to
Pocket, answered in the FAST tier by a stand-in for `pocket-tts.exe serve`
that records what it was sent, as `test_tts_kokoro.py` does for the sidecar:

- a voice id names its language, and the request reaches THAT language's
  server with the bare voice name;
- a speed other than 1.0, and a voice Pocket does not have, are refused
  before anything reaches the engine (no silent default, no clamp);
- the WAV the server streams is read correctly at every split point, and a
  format other than 24 kHz mono int16 is refused by name;
- each piece's lead and tail silence is trimmed, identically however the
  audio arrives;
- a missing or wrong binary is one error naming the fetch command.
"""
import asyncio
import os
import struct
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import ports                                    # noqa: E402
from daemon.adapters import pocket_pin, tts_pocket          # noqa: E402

SHORT = "It is twenty past four."
RATE = ports.SAMPLE_RATE_OUT


def _wav(pcm: bytes, rate: int = RATE, channels: int = 1, bits: int = 16,
         data_size: int = 0xFFFFFFFF) -> bytes:
    """A streamed WAV as pocket-tts writes one: sizes unknown while it
    generates (0xFFFFFFFF), then the samples. Built from literals here, not
    from the reader under test, so it is an independent second end."""
    fmt = struct.pack("<HHIIHH", 1, channels, rate,
                      rate * channels * bits // 8, channels * bits // 8, bits)
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", data_size) + pcm)


def _speech(lead_ms=200, loud_ms=300, tail_ms=350) -> bytes:
    """Silence, a loud tone, silence: the shape of one pocket piece."""
    n = lambda ms: RATE * ms // 1000                          # noqa: E731
    t = np.arange(n(loud_ms))
    tone = (np.sin(t / 5.0) * 8000).astype("<i2")
    return (np.zeros(n(lead_ms), "<i2").tobytes() + tone.tobytes()
            + np.zeros(n(tail_ms), "<i2").tobytes())


class _StandIn:
    """`pocket-tts.exe serve` for one language model, recording the forms
    it was sent. Satisfies the adapter's server seam: `ensure`, `alive`,
    `stop`."""

    def __init__(self, model: str, status: int = 200, slow: bool = False):
        self.model = model
        self.status = status
        self.slow = slow
        self.forms: list[dict] = []
        self.port = 0
        self._runner = None

    async def start(self):
        app = web.Application()
        app.router.add_post("/tts", self._tts)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def close(self):
        await self._runner.cleanup()

    async def ensure(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    async def _tts(self, req):
        self.forms.append(dict(await req.post()))
        if self.status != 200:
            return web.json_response({"detail": "nope"}, status=self.status)
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "audio/wav"
        await resp.prepare(req)
        body = _wav(_speech() * (4 if self.slow else 1))
        # Odd-sized pieces, so a header and a sample are split mid-way.
        step = 4001 if self.slow else 777
        for i in range(0, len(body), step):
            await resp.write(body[i:i + step])
            if self.slow:
                await asyncio.sleep(0.01)
        await resp.write_eof()
        return resp


@asynccontextmanager
async def stand_ins(*models, **kw):
    servers = {m: _StandIn(m, **kw) for m in models}
    for s in servers.values():
        await s.start()
    tts = tts_pocket.PocketTts()
    tts._servers.update(servers)
    try:
        yield tts, servers
    finally:
        for s in servers.values():
            await s.close()


# --------------------------------------------------------------------------- #
# Voices and languages
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_voice_reaches_the_server_for_its_language():
    """`pt_alba` is the voice `alba` spoken by the PORTUGUESE model: the
    language lives in the id, because the port carries only a voice."""
    async with stand_ins("english", "portuguese") as (tts, servers):
        chunks = [c async for c in tts.synthesize("Ola, tudo bem?", "pt_alba")]
    assert chunks, "the stand-in produced no audio; the test is broken"
    assert servers["english"].forms == []
    assert servers["portuguese"].forms == [
        {"text": "Ola, tudo bem?", "voice_url": "alba"}]


@pytest.mark.asyncio
async def test_a_blank_voice_is_the_default_voice():
    async with stand_ins("english") as (tts, servers):
        _ = [c async for c in tts.synthesize(SHORT, "")]
    assert servers["english"].forms[0]["voice_url"] == "alba"


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", ["af_heart", "pt_nobody", "xx_alba", "alba"])
async def test_a_voice_pocket_does_not_have_is_refused_before_the_engine(voice):
    """A Kokoro name left in the owner's settings must fail loudly, not come
    out in some other voice (AC-7b)."""
    async with stand_ins("english", "portuguese") as (tts, servers):
        with pytest.raises(ValueError, match="voice"):
            _ = [c async for c in tts.synthesize(SHORT, voice)]
    assert all(s.forms == [] for s in servers.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("speed", [0.8, 1.3, 0.0, float("nan")])
async def test_a_speed_other_than_one_is_refused_before_the_engine(speed):
    """Pocket has no rate control. Accepting 1.3 and speaking at 1.0 would be
    a setting that saves and changes nothing (AC-107, AC-7b)."""
    async with stand_ins("english") as (tts, servers):
        with pytest.raises(ValueError, match="speed"):
            _ = [c async for c in tts.synthesize(SHORT, "en_alba",
                                                 speed=speed)]
    assert servers["english"].forms == []


@pytest.mark.asyncio
async def test_an_error_from_the_server_is_loud():
    async with stand_ins("english", status=400) as (tts, _):
        with pytest.raises(tts_pocket.PocketError, match="400"):
            _ = [c async for c in tts.synthesize(SHORT, "en_alba")]


def test_info_declares_every_voice_in_every_language_with_its_locale():
    """The locales are the recognizer catalogue's, so both an automatic `pt`
    and a pinned `pt-BR` find a voice (ports.voice_ids)."""
    info = tts_pocket.PocketTts().info
    assert info["voice"] == "en_alba"
    assert info["sample_rate"] == RATE
    ids = ports.voice_ids(info)
    assert len(ids) == len(pocket_pin.VOICES) * len(pocket_pin.LANGUAGES)
    assert ports.voice_ids(info, "pt-BR")[0] == "pt_rafael"
    assert ports.voice_ids(info, "en-US")[0] == "en_alba"
    assert ports.voice_languages(info) == sorted(pocket_pin.LANGUAGES)


@pytest.mark.parametrize("sub,native", [("en", "alba"), ("pt", "rafael"),
                                        ("fr", "estelle"), ("es", "lola"),
                                        ("de", "juergen"), ("it", "giovanni"),
                                        ("nl", "alba")])
def test_a_language_is_answered_first_in_its_own_native_voice(sub, native):
    """`hub._voice_for` takes the first voice of a language. That must be the
    voice the binary itself picks for that model (`generate --help`, 3.3.0),
    not the English `alba` reading Portuguese."""
    info = tts_pocket.PocketTts().info
    assert ports.voice_ids(info, sub)[0] == f"{sub}_{native}"


@pytest.mark.asyncio
async def test_abort_by_handle_stops_the_stream():
    async with stand_ins("english", slow=True) as (tts, _):
        full = [c async for c in tts.synthesize(SHORT, "en_alba",
                                                handle=("turn", 2))]
        got = []
        async for c in tts.synthesize(SHORT, "en_alba", handle=("turn", 1)):
            got.append(c)
            if len(got) == 1:
                await tts.abort(("turn", 1))
        after = [c async for c in tts.synthesize(SHORT, "en_alba",
                                                 handle=("turn", 1))]
    assert len(got) < len(full), "abort did not shorten the stream"
    assert after, "an abort silenced the next reply on the same handle"


# --------------------------------------------------------------------------- #
# The WAV stream
# --------------------------------------------------------------------------- #
def test_the_wav_stream_reads_the_same_at_every_split_point():
    pcm = _speech(10, 20, 10)
    body = _wav(pcm)
    for cut in range(1, 80):
        reader = tts_pocket.WavReader()
        out = reader.push(body[:cut]) + reader.push(body[cut:])
        reader.end()
        assert out == pcm, f"split at byte {cut} changed the audio"
    reader = tts_pocket.WavReader()
    out = b"".join(reader.push(body[i:i + 1]) for i in range(len(body)))
    assert out == pcm, "one byte at a time changed the audio"


@pytest.mark.parametrize("kw,named", [({"rate": 22050}, "22050"),
                                      ({"channels": 2}, "channel"),
                                      ({"bits": 8}, "bits")])
def test_a_wav_the_port_cannot_carry_is_refused_by_name(kw, named):
    reader = tts_pocket.WavReader()
    with pytest.raises(tts_pocket.PocketError, match=named):
        reader.push(_wav(b"\x00\x00" * 10, **kw))


def test_a_stream_that_ends_inside_its_header_is_an_error():
    reader = tts_pocket.WavReader()
    reader.push(_wav(b"")[:20])
    with pytest.raises(tts_pocket.PocketError, match="header"):
        reader.end()


# --------------------------------------------------------------------------- #
# Edge silence
# --------------------------------------------------------------------------- #
def test_edge_silence_is_trimmed_to_the_pads():
    pcm = _speech(lead_ms=200, loud_ms=300, tail_ms=350)
    out = tts_pocket.trim_edges(pcm, RATE)
    lead, trail = tts_pocket.KEEP_LEAD_MS, tts_pocket.KEEP_TRAIL_MS
    expect_ms = lead + 300 + trail
    # The tone's first and last samples are near zero, so allow a few ms.
    assert abs(len(out) // 2 * 1000 // RATE - expect_ms) <= 5, (
        f"{len(out) // 2 * 1000 // RATE} ms kept, expected about {expect_ms}")


def test_the_streaming_trim_equals_the_whole_trim_at_every_split():
    pcm = _speech(lead_ms=40, loud_ms=60, tail_ms=200) + _speech(30, 50, 300)
    whole = tts_pocket.trim_edges(pcm, RATE)
    for cut in list(range(0, len(pcm), 997)) + [len(pcm) // 2 + 1]:
        cut -= cut % 2
        trimmer = tts_pocket.EdgeTrimmer(RATE)
        out = trimmer.push(pcm[:cut]) + trimmer.push(pcm[cut:]) + trimmer.end()
        assert out == whole, f"split at byte {cut} changed the trimmed audio"


def test_silence_inside_a_piece_is_kept():
    """A pause between two sentences of one piece is speech, not an edge."""
    pcm = _speech(0, 100, 250) + _speech(0, 100, 0)
    out = tts_pocket.trim_edges(pcm, RATE)
    assert len(out) >= len(_speech(0, 100, 250)) + len(_speech(0, 100, 0)) - 4


# --------------------------------------------------------------------------- #
# The binary and its server
# --------------------------------------------------------------------------- #
def test_a_missing_binary_names_the_fetch_command(tmp_path):
    with pytest.raises(tts_pocket.PocketError) as e:
        tts_pocket.check_binary(tmp_path / "pocket-tts.exe")
    assert pocket_pin.FETCH_COMMAND in str(e.value)


@pytest.mark.asyncio
async def test_a_missing_binary_fails_the_synthesis_loudly(tmp_path):
    tts = tts_pocket.PocketTts(exe=str(tmp_path / "pocket-tts.exe"))
    with pytest.raises(tts_pocket.PocketError, match="fetch-pocket-tts"):
        _ = [c async for c in tts.synthesize(SHORT, "en_alba")]


def test_the_server_is_loopback_one_language_quantized():
    argv = tts_pocket.server_argv("x.exe", "portuguese", 5123)
    assert argv == ["x.exe", "serve", "--host", "127.0.0.1", "--port", "5123",
                    "--language", "portuguese", "--quantize"]


@pytest.mark.parametrize("env,expect", [
    ({"HF_HUB_CACHE": "C:/c"}, "C:/c"),
    ({"HF_HOME": "C:/h"}, str(Path("C:/h") / "hub")),
    ({}, str(Path("C:/home") / ".cache" / "huggingface" / "hub")),
])
def test_the_server_is_told_where_the_weight_cache_is(env, expect):
    """Left alone the exe finds the cache through HOME, which Git Bash sets and
    cmd.exe (start.bat) does not: it then misses every cached file and reaches
    for the network (an earlier prototype, 2026-10-30)."""
    assert pocket_pin.hf_hub_cache(env, Path("C:/home")) == expect
    assert tts_pocket.server_env(env, Path("C:/home"))["HF_HUB_CACHE"] == expect


@pytest.mark.asyncio
async def test_a_started_but_unready_server_is_not_handed_out(monkeypatch):
    """A second caller during a slow boot must wait for the health check, not
    get the address of a port nobody listens on yet (review, 2026-10-30)."""
    server = tts_pocket._Server(Path("x.exe"), "english", 1.0)

    class _Running:
        def poll(self):
            return None
    server.proc = _Running()                 # alive, health check not passed
    started = []
    monkeypatch.setattr(server, "_start", lambda: started.append(True))
    await server.ensure()
    assert started, "ensure() trusted a live process that never became ready"


def test_a_binary_that_cannot_run_is_not_installed(tmp_path):
    """A truncated exe raises OSError on --version; that must read as 'not
    installed, run the fetch command', not crash."""
    bad = tmp_path / "pocket-tts.exe"
    bad.write_bytes(b"MZ truncated")
    with pytest.raises(tts_pocket.PocketError, match="fetch-pocket-tts"):
        tts_pocket.check_binary(bad)


# --------------------------------------------------------------------------- #
# One pin, one asset per platform (Windows desk and laptop, Apple Silicon Mac)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,suffix,sha", [
    (("win32", "AMD64"), ".zip", "98753041f2e6"),
    (("darwin", "arm64"), "aarch64-apple-darwin.tar.gz", "ee9ca6c7c000"),
])
def test_each_supported_platform_has_its_own_pinned_asset(key, suffix, sha):
    asset = pocket_pin.asset_for(key)
    assert asset["name"].endswith(suffix) and asset["sha256"].startswith(sha)
    assert pocket_pin.RELEASE["version"] in asset["name"]


def test_an_unsupported_platform_is_refused_by_name():
    with pytest.raises(RuntimeError, match="linux"):
        pocket_pin.asset_for(("linux", "x86_64"))


def _fetch_script():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "fetch-pocket-tts.py"
    spec = importlib.util.spec_from_file_location("fetch_pocket_tts", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_the_fetch_script_unpacks_either_archive(tmp_path, kind):
    import tarfile
    import zipfile
    exe = "pocket-tts.exe" if kind == "zip" else "pocket-tts"
    archive = tmp_path / f"a.{kind}"
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(f"top/{exe}", b"BIN")
            z.writestr("top/LICENSE", b"MIT")
    else:
        src = tmp_path / "top"
        src.mkdir()
        (src / exe).write_bytes(b"BIN")
        (src / "LICENSE").write_bytes(b"MIT")
        with tarfile.open(archive, "w:gz") as t:
            t.add(src, arcname="top")
    dest = tmp_path / "out"
    _fetch_script().unpack(archive, exe, dest)
    assert (dest / exe).read_bytes() == b"BIN"
    assert (dest / "LICENSE").read_bytes() == b"MIT"
    if os.name != "nt":
        assert os.access(dest / exe, os.X_OK), "the binary was not made executable"
