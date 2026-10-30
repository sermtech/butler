"""AC-534: the Node tests of the WhatsApp sidecar run inside the suite.

`daemon/engines/whatsapp/` is a PORT of an earlier prototype's WhatsApp code (docs/messaging.md
section 12, phase M1) and its ported tests are `node:test` files. The gate reads
pytest, so one wrapper runs them under `node --test` and asserts the EXIT CODE:
a failing Node test fails this test, and nothing here reads the output text.

Nothing in those tests opens a real WhatsApp connection or reaches the internet:
Baileys is injected as a fake at its three seams. Failure mode this catches: the
socket options of AC-502 (a link that archives 0 contacts) or the send-guard
order of AC-512 regressing with no Python test noticing.

The tests are named by a GLOB, not by the directory: on Node 22 `node --test
<dir>` tries to run the directory as a module and dies with MODULE_NOT_FOUND.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "daemon" / "engines" / "whatsapp" / "test"


def test_the_ported_whatsapp_node_tests_pass():
    node = shutil.which("node")
    assert node, "node is not on PATH: the WhatsApp sidecar tests cannot run"
    assert list(TESTS.glob("*.test.mjs")), f"no node tests found in {TESTS}"

    result = subprocess.run(
        [node, "--test", "daemon/engines/whatsapp/test/*.test.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    # Only the exit code decides. The failing tests' own lines ride along
    # FIRST: a bare tail cut the name of a load-only failure on 2026-10-08.
    out = result.stdout + result.stderr
    failing = [ln for ln in out.splitlines() if "not ok" in ln]
    assert result.returncode == 0, ("\n".join(failing) + "\n...\n" + out[-3000:])
