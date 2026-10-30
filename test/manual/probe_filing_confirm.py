"""Throwaway scratch daemon that seeds ONE project with a genuinely BUSY
child, for the phase-5 confirm-to-stop dialog's screenshot
(docs/filing-and-pacing.md 1.7).

NOT a pytest file and NOT part of the suite -- a one-shot process, run as a
background subprocess and killed by the PID that launched it, the same shape
as `seed_triage_corpus.py`. That script fakes "running"/"waiting" by poking
`Hub._orphaned`/`_turn_started` directly, which never touches `_Host.
_project_claims` -- the dict `_Host.busy_children_for_project` (and so
`GET /api/shortcuts`'s `busy` flag) actually reads. So this script spawns a
REAL child through `host.plan()`/`host.ensure()`, exactly the state a child
is in the instant after a turn starts and before it has finished -- which is
what makes the project genuinely read `busy: true` over the wire.

Usage: `python test/manual/probe_filing_confirm.py <port>`
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test"))

os.environ.setdefault("BUTLER_CONFIG", str(REPO / "does-not-exist.json"))

from aiohttp import web                                          # noqa: E402

from conftest import StubResolver                                 # noqa: E402
from daemon import butlerd, hub as hub_mod, ports                  # noqa: E402
from daemon.adapters.brain_harness import HarnessBrain            # noqa: E402
from daemon.adapters.harness_fake import FakeHarness                # noqa: E402
from daemon.chats import ChatStore                                  # noqa: E402
from daemon.modes import ModeStore                                  # noqa: E402
from daemon.settings import Settings                                # noqa: E402
from daemon.shortcuts import ShortcutStore                          # noqa: E402

STATE = REPO / "test" / "manual" / "_filing_confirm_state"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8975
PROJ_DIR = STATE / "proj"


def main():
    if STATE.exists():
        import shutil
        shutil.rmtree(STATE)
    PROJ_DIR.mkdir(parents=True)

    prefs = Settings(STATE / "settings.json")
    prefs.set("harness_cwd", str(PROJ_DIR))
    prefs.set("project_agent_limit", 1)
    store = ShortcutStore(STATE / "shortcuts.json")
    row = store.upsert(sid="", path=str(PROJ_DIR), label="Proj")
    chats = ChatStore(STATE / "chats")

    # No real model calls happen (the busy child is spawned directly below,
    # never through `brain.turn()`), so a `StubResolver` pointed at nothing
    # reachable is fine -- it is never asked to resolve.
    brain = HarnessBrain(
        resolver=StubResolver("http://127.0.0.1:1/"),
        harness_for=lambda _chat=None: FakeHarness(),
        spec_for=butlerd.harness_spec_resolver(prefs, chats, store.paths),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])

    hub = hub_mod.Hub(asr=ports.build(ports.ASRS, "fake"),
                      tts=ports.build(ports.TTSS, "fake"), brain=brain,
                      modes=ModeStore(STATE / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=prefs, shortcuts=store)
    brain.host.set_stop_for(hub.stop_conversation)

    async def _make_busy():
        chat = chats.current()
        await brain.host.ensure(chat, await brain.host.plan(chat))
        print(f"[probe] project {row.id!r} ({row.path}) now holds a BUSY "
              f"child for chat {chat['id']!r}", flush=True)

    asyncio.get_event_loop().run_until_complete(_make_busy())

    butlerd.build_hub = lambda cfg: hub
    app = butlerd.make_app({})
    print(f"[probe] serving on 127.0.0.1:{PORT}, root_id={store.root_id()!r}, "
          f"project_row={row.id!r}", flush=True)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
