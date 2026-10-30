"""End-to-end check of SERVER-SIDE Smart Turn over the real WebSocket.

Feeds real speech, then asks the server for an end-of-turn verdict exactly the
way the browser does ({"type":"check_turn"}), and asserts a usable answer comes
back. JFK's line is a COMPLETE sentence, so a working detector should report a
high P(turn complete).
"""
import asyncio
import json
import sys
import time
import wave

import aiohttp

MODEL = "parakeet-unified-en-0.6b"
WAV = r"C:\PROJECTS\other-project\transcribe.cpp\samples\jfk.wav"
PORT = 8140
URL = f"https://127.0.0.1:{PORT}/ws"


def load(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


async def main():
    pcm = load(WAV)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.ws_connect(URL) as ws:
            await ws.send_str(json.dumps({"type": "config", "model": MODEL,
                                          "backend": "auto", "latency": "low",
                                          "chat": "stub"}))
            while True:
                m = json.loads((await ws.receive()).data)
                if m["type"] == "ready":
                    break
            print("server ready:", m.get("backend"))

            # Stream the utterance (no finalize -- we are mid-turn, at a pause).
            frame = 512 * 2
            for i in range(0, len(pcm), frame):
                await ws.send_bytes(pcm[i:i + frame])
                await asyncio.sleep(0.032)   # REALTIME pace, as a browser sends
            await asyncio.sleep(0.4)

            # Ask for the end-of-turn verdict, twice, to get a warm timing too.
            results = []
            for n in (1, 2):
                t0 = time.perf_counter()
                await ws.send_str(json.dumps({"type": "check_turn", "id": n}))
                deadline = time.time() + 30
                while time.time() < deadline:
                    m = json.loads((await ws.receive()).data)
                    if m.get("type") == "turn":
                        rtt = (time.perf_counter() - t0) * 1000
                        print(f"check {n}: id={m['id']} complete={m['complete']} "
                              f"p={m['p']} server_ms={m['ms']} (feat={m.get('feat_ms')} infer={m.get('infer_ms')}) rtt_ms={rtt:.0f}"
                              + (f" error={m['error']}" if m.get("error") else ""))
                        results.append(m)
                        break
                else:
                    print(f"check {n}: TIMEOUT")

            ok = (len(results) == 2
                  and all(r.get("error") is None for r in results)
                  and all(r["id"] == i + 1 for i, r in enumerate(results))
                  and results[0]["p"] >= 0.6
                  and results[0]["complete"] is True)
            print("RESULT:", "PASS (server-side smart turn works)" if ok else "FAIL")
            return 0 if ok else 1


sys.exit(asyncio.run(main()))
