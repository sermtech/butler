"""Headless end-to-end check of the spike server (no mic/browser).

Streams a 16 kHz mono WAV through /ws exactly like the browser would, then
finalizes and asserts we get streaming partials, a final transcript, and a
spoken reply. Proves server.py + stt_engine.py end to end.

Usage: python test_ws_loop.py [model_slug] [wav]
"""
import asyncio, json, sys, wave, array, time
import aiohttp

MODEL = sys.argv[1] if len(sys.argv) > 1 else "parakeet-unified-en-0.6b"
WAV = sys.argv[2] if len(sys.argv) > 2 else \
    r"C:\PROJECTS\other-project\transcribe.cpp\samples\jfk.wav"
PORT = 8140


def load(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


async def main():
    pcm = load(WAV)
    frame = 512 * 2  # 512 samples * int16
    partials = 0
    final = reply = None
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{PORT}/ws") as ws:
            await ws.send_str(json.dumps({"type": "config", "model": MODEL,
                                          "backend": "auto", "latency": "low"}))
            # wait for ready
            while True:
                m = json.loads((await ws.receive()).data)
                if m["type"] == "ready":
                    print("ready:", m["arch"], m.get("variant"), "on", m["backend"]); break
                if m["type"] == "error":
                    print("ERROR:", m["message"]); return

            async def feeder():
                for i in range(0, len(pcm), frame):
                    await ws.send_bytes(pcm[i:i + frame])
                    await asyncio.sleep(0.032)  # realtime pace
                await ws.send_str(json.dumps({"type": "finalize"}))
            asyncio.ensure_future(feeder())

            deadline = time.time() + 40
            while time.time() < deadline:
                m = json.loads((await ws.receive()).data)
                if m["type"] == "partial":
                    partials += 1
                    print(f"\r  partial[{partials}]: {m['committed']}\x1b[2m{m['tentative']}\x1b[0m\x1b[K", end="")
                elif m["type"] == "final":
                    final = m; print(f"\n  FINAL: {m['text']}")
                    print(f"  first_partial={m['first_partial_ms']}ms finalize={m['finalize_ms']}ms turn={m['turn_ms']}ms")
                elif m["type"] == "reply":
                    reply = m; print(f"  REPLY({m['reply_ms']}ms): {m['text']}"); break

    ok = partials >= 3 and final and final["text"] and reply
    print("\nRESULT:", "PASS" if ok else "FAIL",
          f"(partials={partials}, final={'y' if final else 'n'}, reply={'y' if reply else 'n'})")
    sys.exit(0 if ok else 1)


asyncio.run(main())
