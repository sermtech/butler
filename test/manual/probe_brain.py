"""Drive one real turn through the real brain. NOT collected by pytest.

The suite proves the router and endpoint mode against a fake OpenAI server,
which is the right instrument for behaviour and cannot tell you whether a
real llama.cpp honours the schema, what a verdict actually costs, or whether
the connection the owner picked answers at all. This does.

    python -X utf8 test\\manual\\probe_brain.py --model lmstudio/Qwen3-4B-Instruct-2507

It boots its OWN daemon with its OWN state directory, so the owner's
settings are untouched. It reads the real pi registry -- that is the point,
it is proving a real connection resolves -- and prints no key: connections
are shown through `Connection.public()` like every other reader.

What it reports, all measured in the run rather than declared:
  * which connections imported, and which one it used
  * the wall time of the verdict (final -> first reply frame) and of the
    whole turn, INCLUDING any model load, which is called out separately
  * every wire frame in order, so a truncated or unspoken reply is visible
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "daemon" / "butlerd.py"
sys.path.insert(0, str(REPO))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tone(ms: int) -> bytes:
    """Non-silent PCM: the fake recognizer only advances its script on voiced
    audio, so silence would produce the empty final instead of a turn."""
    import array
    samples = array.array("h")
    period = 16000 // 220
    for i in range(16 * ms):
        samples.append(6000 if (i % period) < period // 2 else -6000)
    return samples.tobytes()


async def run(args) -> int:
    state = Path(args.state or (REPO / "state" / "probe-brain"))
    state.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = {**os.environ,
           "BUTLER_TEST_STUB_STT": "1", "BUTLER_BRAIN": "endpoint",
           "BUTLER_TTS": "fake", "BUTLER_ENDPOINTER": "none",
           "BUTLER_PORT": str(port), "BUTLER_STATE": str(state),
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-X", "utf8", str(DAEMON)],
                            cwd=str(REPO), env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        return await drive(base, args)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


async def drive(base: str, args) -> int:
    async with aiohttp.ClientSession() as http:
        # The daemon warms the brain at boot, which pays the model load here
        # rather than in the turn -- exactly what D-10 asks for, and worth
        # separating in the report because it is the biggest number.
        boot_started = time.monotonic()
        for _ in range(600):
            try:
                async with http.get(base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:                             # noqa: BLE001
                await asyncio.sleep(0.25)
        else:
            print("daemon never became healthy")
            return 1
        print(f"daemon up in {time.monotonic() - boot_started:.1f}s "
              f"(includes the boot warm, so it includes any model load)")

        async with http.get(base + "/api/connections") as r:
            conns = (await r.json())["connections"]
        print(f"connections: {[c['id'] for c in conns]}")
        if not any(c["id"] == args.connection for c in conns):
            print(f"no connection named {args.connection!r}; pick one above")
            return 1

        pair = {"conn": args.connection, "model": args.model}
        body = {"router_model": pair, "endpoint_model": pair,
                "router_max_tokens": args.router_cap,
                "reply_max_tokens": args.reply_cap}
        async with http.post(base + "/api/settings", json=body) as r:
            if r.status != 200:
                print(f"settings refused: {r.status} {await r.text()}")
                return 1
        print(f"router and reply both -> {args.connection} / "
              f"{args.model or '(connection default)'}; "
              f"caps {args.router_cap}/{args.reply_cap}")

        async with http.ws_connect(f"{base}/ws",
                                   max_msg_size=16 * 1024 * 1024) as ws:
            await ws.send_str(json.dumps({
                "type": "hello", "proto": 1, "device_id": "probe",
                "name": "probe", "caps": {"wake": True, "tts": True}}))
            ready = await expect(ws, "ready")
            print(f"ready: stt={ready['stt'].get('arch')} "
                  f"tts={ready['tts'].get('engine')} "
                  f"brain={ready['brain'].get('engine')}")

            await ws.send_str(json.dumps({"type": "wake_candidate", "word": "hey_butler",
                                          "score": 0.9, "ts_ms": 0}))
            start = await expect(ws, "capture_start")
            turn = start["turn"]
            for i in range(0, len(tone(2400)), 1024):
                await ws.send_bytes(tone(2400)[i:i + 1024])
            await ws.send_str(json.dumps({"type": "capture_end_hint",
                                          "turn": turn}))

            final = await expect(ws, "final")
            said = time.monotonic()
            print(f"heard: {final['text']!r}")

            first_reply = None
            audio = 0
            frames = []
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=args.timeout)
                if msg.type is aiohttp.WSMsgType.BINARY:
                    audio += len(msg.data)
                    continue
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    print("socket closed early")
                    return 1
                m = json.loads(msg.data)
                frames.append(m)
                if m["type"] == "reply":
                    if first_reply is None:
                        first_reply = time.monotonic()
                        print(f"first reply after {first_reply - said:.2f}s "
                              f"(verdict plus first sentence)")
                    mark = "" if m.get("spoken", True) else "   [NOT SPOKEN]"
                    print(f"  reply[{m.get('tier')}]: {m['text']!r}{mark}")
                elif m["type"] == "speak_end":
                    print(f"speak_end truncated={m.get('truncated')}")
                    await ws.send_str(json.dumps({"type": "playback_done",
                                                  "turn": turn}))
                    break
                elif m["type"] == "error":
                    print(f"  ERROR: {m['message']}")
                    return 1

            print(f"turn took {time.monotonic() - said:.2f}s, "
                  f"{audio / 48000:.1f}s of audio, "
                  f"frames: {[f['type'] for f in frames]}")
    return 0


async def expect(ws, kind: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = await asyncio.wait_for(ws.receive(),
                                     timeout=max(1.0, deadline - time.monotonic()))
        if msg.type is aiohttp.WSMsgType.BINARY:
            continue
        if msg.type is not aiohttp.WSMsgType.TEXT:
            raise SystemExit(f"socket closed waiting for {kind}")
        m = json.loads(msg.data)
        if m.get("type") == kind:
            return m
        if m.get("type") == "error":
            raise SystemExit(f"daemon error waiting for {kind}: {m}")
    raise SystemExit(f"timed out waiting for {kind}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="probe_brain")
    ap.add_argument("--connection", default="llama-swap")
    ap.add_argument("--model", default="")
    ap.add_argument("--router-cap", type=int, default=0)
    ap.add_argument("--reply-cap", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--state", default="")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
