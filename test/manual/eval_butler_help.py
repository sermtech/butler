"""Scenario check for docs/butler-help.md AC-656: does the agent suggest the
right Butler feature, none, or an invented one?

Drives a SCRATCH daemon (never 8140) over its own socket: each scenario in a
NEW conversation (so nothing carries over), one typed turn, then the
conversation's status is polled until done and the recorded answer is read.
Writes a JSON list of {id, ask, expect, answer} for a person to score.

    python -X utf8 test/manual/eval_butler_help.py https://127.0.0.1:<port> out.json

Uses a real model: it costs whatever the scratch daemon's harness costs.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import time

import aiohttp

SCENARIOS = [
    ("weekly", "I ask you for the same status summary every Monday morning.",
     "recipe or automation"),
    ("photo", "Can you look at a screenshot I have on my phone?",
     "attach (photo/upload) or phone"),
    ("dave", "I want to know whenever Dave emails me.",
     "trigger (mailbox) + notification"),
    ("away", "I'm leaving the desk. Can I keep talking to you from my phone?",
     "phone link / WhatsApp / Telegram"),
    ("redo", "My last question came out wrong, I phrased it badly.",
     "rewind or try again"),
    ("what", "What can you do?", "an overview, nothing invented"),
    ("wa", "Can you read my WhatsApp messages?",
     "says whether it is on; how to turn on"),
    ("files", "I'd like to see what files are in this project.",
     "file explorer (or just list them)"),
    ("night", "Please don't talk out loud late at night.",
     "quiet hours (or speech off)"),
    ("lights", "Can you turn off my smart lights?",
     "no such feature; must not invent"),
    ("capital", "What is the capital of France?", "no suggestion"),
    ("remind", "Remind me to call my mom tomorrow at 5pm.",
     "uses reminder_add; no extra suggestion needed"),
    # docs/recipe-tools.md section 4: run with RECIPES seeded first
    # (`--seed`), so a saved way exists for each of these.
    ("r_email", "Check my emails please.", "recipe_find -> Email check"),
    ("r_week", "What does my week look like?", "recipe_find -> My week"),
    ("r_status", "Can you do my Monday status summary?",
     "recipe_find -> Monday status"),
]

#: Global recipes `--seed` saves before the run (recipe-tools.md section 4).
RECIPES = [
    ("Email check", "Check my unread email in my Gmail and list the three "
                    "most important messages: who, what, and why it "
                    "matters. Read only."),
    ("My week", "Show my calendar for the next 7 days, day by day: time and "
                "what. Read only."),
    ("Monday status", "Write my Monday status summary: open tasks, this "
                      "week's calendar and unread important email, in five "
                      "short lines. Read only."),
]

TURN_DEADLINE_S = 420


async def run(base: str, out: str) -> int:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    results = []
    async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ctx)) as http:
        ws_url = base.replace("https://", "wss://").replace(
            "http://", "ws://") + "/ws"
        async with http.ws_connect(ws_url) as ws:
            await ws.send_json({"type": "hello", "proto": 1,
                                "device_id": "eval", "name": "eval",
                                "caps": {"wake": False, "tts": False}})
            # The daemon closes a socket whose pings go unanswered, and
            # aiohttp answers them only while something reads: drain it.
            async def drain():
                async for _ in ws:
                    pass
            reader = asyncio.create_task(drain())
            args = [x for x in sys.argv[3:] if x != "--seed"]
            only = set(args[0].split(",")) if args else None
            if "--seed" in sys.argv:
                for name, prompt in RECIPES:
                    async with http.post(base + "/api/recipes", json={
                            "name": name, "project": "global",
                            "prompt": prompt}) as r:
                        assert r.status == 200, await r.text()
            for sid, ask, expect in SCENARIOS:
                if only and sid not in only:
                    continue
                async with http.post(base + "/api/session/new",
                                     json={}) as r:
                    born = await r.json()
                chat = born["id"]
                await ws.send_json({"type": "showing", "chat": chat})
                await ws.send_json({"type": "typed", "text": ask})
                started = time.monotonic()
                status = ""
                while time.monotonic() - started < TURN_DEADLINE_S:
                    await asyncio.sleep(3)
                    async with http.get(f"{base}/api/chats/{chat}/status") \
                            as r:
                        if r.status == 200:
                            status = (await r.json()).get("status", "")
                    if status in ("done", "error"):
                        break
                async with http.get(f"{base}/api/chats/{chat}") as r:
                    record = await r.json() if r.status == 200 else {}
                answers = [m["content"] for m in record.get("messages", [])
                           if m.get("role") == "assistant"]
                results.append({"id": sid, "ask": ask, "expect": expect,
                                "status": status,
                                "seconds": round(time.monotonic() - started),
                                "answer": "\n".join(answers)})
                print(f"{sid}: {status} in {results[-1]['seconds']} s",
                      flush=True)
            reader.cancel()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1].rstrip("/"), sys.argv[2])))
