"""
Standalone spike server: streaming STT (transcribe.cpp) + full voice loop.

  browser mic --16k PCM--> /ws --> transcribe_cpp stream --> partial/final
                                        |                        |
                                        |                     reply()  (chat lane)
                                        v                        v
                              committed/tentative text      spoken in the browser

Isolated from the dashboard on purpose. TTS in the page uses the browser's
speechSynthesis as a STAND-IN for the app's Kokoro (left as-is per the spike
scope); the chat "reply" defaults to a fast local stub, with an optional
`pi -p` passthrough (BUTLER_CHAT=pi) to feel a real turn.

Run:  .venv/Scripts/python.exe server.py   then open http://localhost:8140
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import web, WSMsgType

# Layout: <root>/daemon/server.py, engines beside it, the web page and the
# model files one level up. Engines are imported by name (not as a package)
# so they stay directly runnable for the standalone test scripts in test/.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "engines"))

import stt_engine  # noqa: E402  (needs the sys.path line above)
import turn_engine  # noqa: E402

# WebAssembly must be served as application/wasm or the browser refuses to
# instantiate it (Windows Python often lacks this mapping) -- needed for the
# pinned onnxruntime-web 1.18 wasm under web/ort18/.
mimetypes.add_type("application/wasm", ".wasm")

STATIC = ROOT / "web"
CHAT_MODE = os.environ.get("BUTLER_CHAT", "stub")  # "stub" | "pi"

# Smart Turn (end-of-turn) detection runs HERE, not in the browser: the server
# already has the audio, and it then works on every client (iPad WebKit cannot
# host it reliably). It runs in a DEDICATED CHILD PROCESS -- see
# turn_engine.get_pool(): sharing this process's GIL with the STT worker thread
# inflated feature extraction from ~30 ms to ~2200 ms. Fail loud at startup if
# the model is missing rather than silently degrading endpointing.
if not turn_engine.MODEL_PATH.exists():
    raise SystemExit(f"[butler] smart turn model missing: {turn_engine.MODEL_PATH}")

# Rolling utterance buffer cap: the model reads the last 8 s; keep 9 s of int16.
UTT_MAX_BYTES = 16000 * 9 * 2


# --------------------------------------------------------------------------- #
# Chat lane (deliberately minimal -- the spike proves STT, not the LLM).
# --------------------------------------------------------------------------- #
def reply_stub(text: str) -> str:
    t = text.strip().rstrip(".!?").lower()
    if not t:
        return "I did not catch that."
    if t.startswith(("what", "who", "when", "where", "why", "how",
                     "is ", "are ", "can ", "do ")):
        return f"Good question. You asked: {text.strip()}"
    return f"Got it. You said: {text.strip()}"


def reply_pi(text: str) -> str:
    # `pi` is pi.cmd on Windows; subprocess(["pi", ...]) fails with WinError 2
    # because it does not resolve PATHEXT. `cmd /c pi` does. Shape the prompt so
    # the coding agent answers like a brief spoken assistant. Fail LOUD (a clear
    # bracketed error), never a stub masquerading as a real reply.
    prompt = ("You are a friendly voice assistant. Reply in one or two short "
              "spoken sentences, plain text (no markdown). User said: " + text)
    try:
        out = subprocess.run(
            ["cmd", "/c", "pi", "-p", prompt],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        r = (out.stdout or "").strip()
        if r:
            return r
        err = (out.stderr or "").strip()[:160]
        return "[pi returned no text" + (": " + err if err else "") + "]"
    except Exception as e:
        return f"[pi error: {e}]"


def make_reply(text: str, mode: str) -> str:
    return reply_pi(text) if mode == "pi" else reply_stub(text)


# --------------------------------------------------------------------------- #
# WebSocket: one StreamingSession per connection. The engine emits partial/final
# from its worker thread; a "final" additionally kicks off the chat reply on its
# own thread so the event loop never blocks on the LLM.
# --------------------------------------------------------------------------- #
async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    session: stt_engine.StreamingSession | None = None
    chat_mode = CHAT_MODE  # per-connection; the client picks pi|stub in `config`
    # Client-stamped turn id, echoed back on final + reply. The client bumps it
    # whenever it abandons a turn (barge-in during thinking) and discards any
    # reply whose turn is stale -- pi runs as an unkillable subprocess, so its
    # late answer still arrives and must not be spoken.
    pending_turn = {"v": 0}

    async def send(obj):
        await ws.send_str(json.dumps(obj))

    def emit(kind: str, payload: dict):
        # invoked from the engine worker thread -> hop back onto the loop
        msg = {"type": kind, **payload}
        if kind == "final":
            msg["turn"] = pending_turn["v"]
        asyncio.run_coroutine_threadsafe(send(msg), loop)
        if kind == "final" and payload.get("text"):
            turn = pending_turn["v"]
            def _reply():
                t0 = time.perf_counter()
                r = make_reply(payload["text"], chat_mode)
                asyncio.run_coroutine_threadsafe(send({
                    "type": "reply", "text": r,
                    "reply_ms": round((time.perf_counter() - t0) * 1000),
                    "turn": turn,
                }), loop)
            threading.Thread(target=_reply, daemon=True).start()

    # Raw int16 PCM of the CURRENT utterance, for Smart Turn. The client only
    # streams audio while capturing, so this buffer is exactly the utterance;
    # it is cleared whenever a turn starts (reset) or ends (finalize).
    utt = bytearray()

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if session:
                    session.feed(msg.data)
                utt.extend(msg.data)
                if len(utt) > UTT_MAX_BYTES:
                    del utt[:len(utt) - UTT_MAX_BYTES]
            elif msg.type == WSMsgType.TEXT:
                m = json.loads(msg.data)
                mt = m.get("type")
                if mt == "config":
                    chat_mode = m.get("chat") or CHAT_MODE
                    if session:
                        session.close()
                    try:
                        session = stt_engine.StreamingSession(
                            slug=m.get("model", "parakeet-unified-en-0.6b"),
                            quant=m.get("quant"),
                            backend=m.get("backend", "auto"),
                            latency=m.get("latency", "balanced"),
                            language=m.get("language"),
                            emit=emit,
                        )
                        await send({"type": "ready", **session.info})
                    except Exception as e:
                        await send({"type": "error", "message": str(e)})
                elif mt == "finalize" and session:
                    pending_turn["v"] = int(m.get("turn") or 0)
                    utt.clear()
                    session.finalize()
                elif mt == "reset":
                    utt.clear()
                    if session:
                        session.reset_turn()
                elif mt == "check_turn":
                    # End-of-turn decision for the audio captured so far. Run off
                    # the event loop (CPU-bound, ~110 ms) so streaming STT and the
                    # WebSocket keep flowing while it thinks.
                    req_id = m.get("id")
                    audio = (np.frombuffer(bytes(utt), dtype="<i2")
                             .astype(np.float32) / 32768.0)
                    if audio.size == 0:
                        await send({"type": "turn", "id": req_id, "complete": False,
                                    "p": 0.0, "ms": 0})
                    else:
                        t0 = time.perf_counter()
                        try:
                            p, feat_ms, inf_ms = await loop.run_in_executor(
                                turn_engine.get_pool(), turn_engine._score_worker, audio)
                            await send({"type": "turn", "id": req_id, "complete": p >= 0.6,
                                        "p": round(p, 4),
                                        "ms": round((time.perf_counter() - t0) * 1000),
                                        "feat_ms": round(feat_ms), "infer_ms": round(inf_ms)})
                        except Exception as e:
                            # Visible, not silent: the client's max-wait cap still
                            # ends the turn, so a bad inference cannot hang the loop.
                            print(f"[butler] smart-turn failed: {e}")
                            await send({"type": "turn", "id": req_id, "complete": False,
                                        "p": 0.0, "ms": 0, "error": str(e)})
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        if session:
            session.close()
    return ws


async def catalog_handler(_request):
    return web.json_response(stt_engine.catalog_public())


# Same-origin proxy to the kokoro sidecar so the page's "kokoro" TTS works over
# LAN/HTTPS. Over TLS the browser cannot fetch http://localhost:8141 (mixed
# content + on a remote device "localhost" is the wrong machine); this forwards
# server-side to the local sidecar and returns its WAV.
async def tts_proxy(request):
    body = await request.read()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("http://127.0.0.1:8141/tts", data=body,
                              headers={"Content-Type": "application/json"}) as r:
                data = await r.read()
                headers = {}
                if r.headers.get("X-Synth-Ms"):
                    headers["X-Synth-Ms"] = r.headers["X-Synth-Ms"]
                return web.Response(body=data, status=r.status,
                                    content_type=r.headers.get("Content-Type", "audio/wav"),
                                    headers=headers)
    except Exception as e:
        return web.Response(status=502, text=f"kokoro sidecar unreachable: {e}")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/catalog", catalog_handler)
    app.router.add_post("/tts", tts_proxy)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/", STATIC, show_index=True)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("BUTLER_PORT", "8140"))
    # A satellite is a browser, and a browser will not open a microphone
    # without HTTPS (localhost excepted). So: with a cert we bind 0.0.0.0 and
    # phones on the LAN or tailnet can join; without one we stay on localhost,
    # desk-only. ssl/ is gitignored -- see README for the one-line copy of an
    # existing mkcert pair, or scripts/make-cert.ps1 to mint a fresh one.
    cert = os.environ.get("BUTLER_CERT", str(ROOT / "ssl" / "server.crt"))
    key = os.environ.get("BUTLER_KEY", str(ROOT / "ssl" / "server.key"))
    ssl_ctx = None
    if os.path.exists(cert) and os.path.exists(key):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert, key)
    host = os.environ.get("BUTLER_HOST", "0.0.0.0" if ssl_ctx else "127.0.0.1")
    scheme = "https" if ssl_ctx else "http"

    # Start the smart-turn child and load its model NOW, so the first real
    # end-of-turn check does not pay the cold start mid-conversation.
    _t0 = time.perf_counter()
    turn_engine.get_pool().submit(turn_engine._score_worker,
                                  np.zeros(16000, dtype=np.float32)).result()
    print(f"[butler] smart-turn worker warm in {round((time.perf_counter() - _t0) * 1000)} ms")

    print(f"[butler] chat={CHAT_MODE} ssl={'on' if ssl_ctx else 'off'}  ->  {scheme}://{host}:{port}")
    web.run_app(make_app(), host=host, port=port, ssl_context=ssl_ctx)
