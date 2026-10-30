"""The daemon's owner against the REAL `wa-sidecar.mjs` (AC-500, phase M2).

`test_whatsapp_owner.py` proves the lifecycle against a fake; this proves the
fake's contract is the real one: node boots, its FIRST stdout line is the
port the owner reads, the owner's token is the one the sidecar demands, the
config push is accepted, and the pairing view parses into the page document.

No network: the records folder is a fresh temp directory with no session/,
so the sidecar waits for `POST /pairing`, which this test never sends.
"""
import asyncio
import shutil
import subprocess
import time

import aiohttp
import pytest

from daemon import whatsapp as whatsapp_mod
from daemon.settings import Settings


@pytest.mark.asyncio
async def test_the_real_sidecar_boots_takes_the_token_and_reports_idle(
        tmp_path):
    assert shutil.which("node"), "node is not on PATH"
    prefs = Settings(tmp_path / "settings.json")
    prefs.set("whatsapp_enabled", True)
    records = tmp_path / "whatsapp"
    wa = whatsapp_mod.WhatsApp(prefs, records)
    await wa.start()
    try:
        deadline = time.monotonic() + 30
        while wa.state != "running" and time.monotonic() < deadline:
            assert wa.state != "failed", wa.reason
            await asyncio.sleep(0.1)
        assert wa.state == "running", f"never ran: {wa.state} {wa.reason}"
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{wa.port}/status") as r:
                assert r.status == 401, "the real sidecar took no token"
            async with s.get(f"http://127.0.0.1:{wa.port}/status",
                             headers={"X-Butler-Token": "wrong"}) as r:
                assert r.status == 401
        status, view = await wa.request("GET", "/pairing")
        assert status == 200
        doc = whatsapp_mod.page_doc(view)
        assert doc["status"] == "idle"
        assert [a["id"] for a in doc["actions"]] == ["link"]
        assert doc["headline"], "the sidecar's words did not come through"
        # AC-501: its records are where the owner said.
        assert (records / "whatsapp.db").exists()
        pid = wa.pid()
    finally:
        await wa.stop()
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    assert str(pid) not in out, "the real sidecar outlived stop()"
