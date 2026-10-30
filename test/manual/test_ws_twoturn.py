"""Reproduce the multi-turn bug: feed a turn, finalize, get reply, then feed a
SECOND turn and check a second final arrives."""
import asyncio, json, sys, wave, time
import aiohttp

MODEL = "parakeet-unified-en-0.6b"
WAV = r"C:\PROJECTS\other-project\transcribe.cpp\samples\jfk.wav"
PORT = 8140


def load(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


async def run_turn(ws, pcm, n):
    frame = 512 * 2
    partials = 0
    final = None
    reply = None

    async def feeder():
        for i in range(0, len(pcm), frame):
            await ws.send_bytes(pcm[i:i + frame])
            await asyncio.sleep(0.032)
        await ws.send_str(json.dumps({"type": "finalize"}))
    task = asyncio.ensure_future(feeder())
    deadline = time.time() + 40
    while time.time() < deadline:
        m = json.loads((await ws.receive()).data)
        if m["type"] == "partial":
            partials += 1
        elif m["type"] == "final":
            final = m
        elif m["type"] == "reply":
            reply = m
            break
    await task
    print(f"TURN {n}: partials={partials} final={final['text'][:40] if final else None!r} reply={'y' if reply else 'n'}")
    return partials, final, reply


async def main():
    pcm = load(WAV)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as s:
        async with s.ws_connect(f"https://127.0.0.1:{PORT}/ws") as ws:
            await ws.send_str(json.dumps({"type": "config", "model": MODEL,
                                          "backend": "auto", "latency": "low"}))
            while True:
                m = json.loads((await ws.receive()).data)
                if m["type"] == "ready":
                    break
            r1 = await run_turn(ws, pcm, 1)
            await asyncio.sleep(0.5)
            r2 = await run_turn(ws, pcm, 2)
    ok = r1[0] >= 3 and r2[0] >= 3 and r2[1] and r2[1]["text"]
    print("RESULT:", "PASS (multi-turn works)" if ok else "FAIL (second turn dead)")
    sys.exit(0 if ok else 1)


asyncio.run(main())
