"""Measurement instrument, NOT part of pytest. Spawns a REAL pi coding-agent
child (`pi --mode rpc`) and costs a real API call against whatever
provider/model it is pointed at (see PROVIDER/MODEL below).

docs/filing-and-pacing.md item 1 phase 4: the whole idle-grace-reaper design
(and the confirm-to-stop mechanism beside it) rests on the claim that
disposing a held pi child costs TIME, not MEMORY, because pi resumes from its
own session file on the next spawn. daemon/AGENTS.md's section 1.6 reasons
this from a 2026-08-19 measurement taken under a DIFFERENT trigger
(per-message dispose), not a fresh one under the current trigger (idle
timeout / confirm-to-stop). This script is that fresh measurement.

It drives PiHarness directly rather than standing up a full daemon:
`_Host.dispose(chat_id)` (daemon/adapters/brain_harness.py:884-903) only ever
pops its own bookkeeping dict and then calls `held.harness.dispose()` -- so
calling `PiHarness.dispose()` on a real child IS the exact call under test,
with the daemon-level wrapper (which is plain dict bookkeeping, already
covered by test_parallel_agents.py) stripped away.

Run manually:

    python test/manual/probe_session_resume.py

Exit code: 0 only if a FRESH child pointed at the same session file answers
correctly what an earlier, disposed child was told. Non-zero if the fact was
not remembered, or if the measurement itself could not be taken (pi missing,
provider unreachable, a spawn or a reply timed out) -- in every non-zero
case the idle-grace reaper's "disposal is free" claim has NOT been confirmed
and must not be reported as proven.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from daemon.adapters.harness_pi import PiHarness  # noqa: E402
from daemon.ports import (Failed, HarnessSpec, TERMINAL_EVENTS,  # noqa: E402
                          TextDelta, TurnEnd)

# MEASURED live 2026-09-17: a plain chat completion against
# codexbridge-local/gpt-5.6-sol answered "Hi" in about a second (curl'd
# directly). conftest.py's own default model for a live pi child
# (gpt-5.4-mini) is the model test/AGENTS.md already records as rejected by
# a live Codex -- and reading ~/.pi/agent/models.json confirms why: that
# model id is GONE from the registry, replaced by the gpt-5.6-* family. This
# script does not reuse conftest's stale default; it names the model it
# actually used so a result here is not mistaken for validating that one.
PROVIDER = "codexbridge-local"
MODEL = "gpt-5.6-sol"

PROMPT_DEADLINE_S = 180.0     # the whole prompt, generous (local/bridge model)
EVENT_TIMEOUT_S = 60.0        # one event within the prompt
KILL_WAIT_S = 15.0            # how long dispose() gets to actually kill the tree


async def _run_prompt(harness: PiHarness, text: str) -> str:
    """Send one prompt, return the assistant's whole reply text.

    Raises on a `Failed` terminal event or a stream that goes silent too
    long -- both are the MEASUREMENT failing, not "the fact was not
    remembered", and main() reports them as exit code 2 to keep that
    distinction visible.
    """
    stream = harness.subscribe()
    await harness.prompt(text)
    # TextDelta is NOT guaranteed (ports.py: "a measured provider emitted
    # none at all and put the whole message in TurnEnd"), but a provider that
    # DOES stream deltas also sends the whole text again in TurnEnd -- so
    # TurnEnd is authoritative when it arrives, and the joined deltas are
    # only a fallback for a provider that never sends one.
    deltas: list[str] = []
    turn_end_text: str | None = None
    deadline = time.monotonic() + PROMPT_DEADLINE_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"no terminal event within {PROMPT_DEADLINE_S:.0f}s of the "
                f"whole prompt")
        event = await asyncio.wait_for(anext(stream),
                                       min(EVENT_TIMEOUT_S, remaining))
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        elif isinstance(event, TurnEnd):
            turn_end_text = event.text
        elif isinstance(event, Failed):
            raise RuntimeError(f"pi reported Failed: {event.message}")
        elif isinstance(event, TERMINAL_EVENTS):
            break
    return turn_end_text if turn_end_text is not None else "".join(deltas)


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True, text=True)
    return str(pid) in out.stdout


def _taskkill(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True)


async def _wait_gone(pid: int, label: str) -> bool:
    deadline = time.monotonic() + KILL_WAIT_S
    while time.monotonic() < deadline and _pid_alive(pid):
        await asyncio.sleep(0.25)
    gone = not _pid_alive(pid)
    print(f"{label} pid {pid} gone after dispose: {gone}")
    return gone


async def main() -> int:
    if shutil.which("pi") is None:
        print("FAIL: pi is not installed on this machine "
              "(needs `pi --mode rpc`); the measurement could not be taken")
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="butler_pi_resume_probe_"))
    session_file = scratch / "probe.session.json"
    print(f"scratch directory: {scratch}")
    print(f"session file:      {session_file}")
    print(f"provider/model:    {PROVIDER}/{MODEL}")

    live_pids: list[int] = []       # anything spawned that still needs a kill
    verdict = 2                      # default: measurement not completed

    try:
        # -- 1: spawn a real pi child, establish a fact -------------------- #
        h1 = PiHarness(provider=PROVIDER, model=MODEL)
        t0 = time.monotonic()
        await h1.start(HarnessSpec(model=None, cwd=str(scratch),
                                    session_file=str(session_file)))
        print(f"first spawn took {time.monotonic() - t0:.2f}s")
        pid1 = h1._child.proc.pid          # noqa: SLF001 -- private read, stated
        live_pids.append(pid1)
        print(f"first child pid (cmd.exe wrapper): {pid1}")

        reply1 = await _run_prompt(
            h1, "Remember the number 47. Reply with one short sentence "
                "acknowledging it and nothing else.")
        print(f"reply 1: {reply1!r}")
        if not reply1.strip():
            print("FAIL: the first reply was empty; nothing to build the "
                  "measurement on")
            return 2

        if not session_file.exists():
            print(f"FAIL: pi never wrote a session file at {session_file}")
            return 2
        before = session_file.stat()
        print(f"session file BEFORE dispose: size={before.st_size} "
              f"mtime={before.st_mtime}")

        # -- 2: dispose. This is the exact call _Host.dispose(chat_id) makes #
        await h1.dispose()
        print("h1.dispose() returned")

        if not session_file.exists():
            print("FAIL: dispose() DELETED the session file -- the "
                  "idle-grace-reaper-is-free claim is WRONG")
            return 1
        after = session_file.stat()
        print(f"session file AFTER dispose:  size={after.st_size} "
              f"mtime={after.st_mtime}")
        untouched = (before.st_size == after.st_size
                     and before.st_mtime == after.st_mtime)
        print(f"dispose left the file byte-for-byte untouched: {untouched}")
        if not untouched:
            print("WARNING: the file changed across dispose() -- section "
                  "1.6's reading of _close_child would be wrong")

        if await _wait_gone(pid1, "first child"):
            live_pids.remove(pid1)

        # -- 3: fresh child, same session file, ask it back ---------------- #
        h2 = PiHarness(provider=PROVIDER, model=MODEL)
        t1 = time.monotonic()
        await h2.start(HarnessSpec(model=None, cwd=str(scratch),
                                    session_file=str(session_file)))
        print(f"respawn took {time.monotonic() - t1:.2f}s")
        pid2 = h2._child.proc.pid          # noqa: SLF001
        live_pids.append(pid2)
        print(f"second child pid: {pid2}")

        reply2 = await _run_prompt(
            h2, "What number did I ask you to remember earlier in this "
                "conversation? Answer with just the number, digits only.")
        print(f"reply 2: {reply2!r}")

        await h2.dispose()
        if await _wait_gone(pid2, "second child"):
            live_pids.remove(pid2)

        remembered = "47" in reply2
        verdict = 0 if remembered else 1

        print("")
        print("=" * 70)
        print(f"VERDICT: the fresh child {'DID' if remembered else 'did NOT'}"
              f" remember the number from the disposed child's conversation.")
        print("=" * 70)
        return verdict
    finally:
        # Safety net (test/AGENTS.md, root AGENTS.md): a red run must not
        # leak a coding agent onto the machine it is measuring.
        for pid in live_pids:
            if _pid_alive(pid):
                print(f"cleanup: killing leaked pid {pid}")
                _taskkill(pid)
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:                                    # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
