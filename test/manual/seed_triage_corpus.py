"""Throwaway scratch daemon that seeds a corpus for triage-list screenshots.

NOT a pytest file and NOT part of the suite -- a one-shot process, run as a
background subprocess and killed by the PID that launched it. It builds its
Hub directly (harness_only on a FakeHarness), seeds 7 projects and 24
conversations across mixed states, and pokes a handful of chats into
running/waiting/error BY HAND -- the same idiom
`test_status_for_derives_the_five_words` uses -- because those three words
are DERIVED from live process state that a persisted chat file cannot carry.

Moved here from `test/manual/appearance/_seed_triage23.py` (defect 3 of the
triage-list review, 2026-09-16): that directory holds SCREENSHOTS, not
scripts. Kept rather than deleted because it is the reusable half of the
pair -- the corpus this seeds is what every triage-list screenshot in
`test/manual/appearance/` was taken against, and any future one needs the
same shapes again.

Usage: `python test/manual/seed_triage_corpus.py <port>`
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test"))

os.environ.setdefault("BUTLER_CONFIG", str(REPO / "does-not-exist.json"))

from aiohttp import web                                          # noqa: E402

from conftest import make_hub                                     # noqa: E402
from daemon import butlerd, chats as chats_mod, ports              # noqa: E402
from daemon.adapters.brain_harness import _Child                   # noqa: E402
from daemon.adapters.harness_fake import FakeHarness                # noqa: E402
from daemon.settings import Settings                                # noqa: E402
from daemon.shortcuts import ShortcutStore                          # noqa: E402

STATE = REPO / "test" / "manual" / "_triage_corpus_state"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8974

PROJECTS = ["alpha", "beta", "site", "docs-site", "ember", "finance",
            "garden"]


def _msg(role, content, ts=1):
    return {"id": f"msg-{ts}-{role}", "role": role, "content": content,
            "timestamp": ts}


def _write_chat(root: Path, cid: str, updated_at: int, project: str,
                title: str, n_turns: int = 1):
    root.mkdir(parents=True, exist_ok=True)
    msgs = []
    for i in range(n_turns):
        msgs.append(_msg("user", f"{title} ({i + 1})", i * 2 + 1))
        msgs.append(_msg("assistant", "on it", i * 2 + 2))
    (root / f"{cid}.json").write_text(json.dumps({
        "id": cid, "createdAt": updated_at, "updatedAt": updated_at,
        "harness": "fake", "sessionRef": "", "project": project,
        "messages": msgs,
    }), encoding="utf-8")


def main():
    if STATE.exists():
        import shutil
        shutil.rmtree(STATE)
    STATE.mkdir(parents=True)
    chats_root = STATE / "chats"
    now = int(time.time() * 1000)

    prefs = Settings(STATE / "settings.json")
    prefs.set("harness_cwd", str(REPO))
    store = chats_mod.ChatStore(chats_root)

    shortcuts = ShortcutStore(STATE / "shortcuts.json")
    for i, name in enumerate(PROJECTS):
        shortcuts.upsert(sid="", path=str(REPO / "test" / "manual" / name),
                         label=name.capitalize(), chords={})

    # 24 conversations, spread across the 7 projects plus a couple unfiled,
    # ages spanning today/yesterday/older so the day buckets (ungrouped view)
    # have more than one heading too.
    ids = []
    for i in range(24):
        proj = str(REPO / "test" / "manual" / PROJECTS[i % len(PROJECTS)]) \
            if i % 6 else ""
        cid = f"chat-{now - i * 3_600_000}-{i:04d}"
        _write_chat(chats_root, cid, now - i * 3_600_000, proj,
                   f"seeded conversation {i}", n_turns=(i % 3) + 1)
        ids.append(cid)

    from daemon.butlerd import harness_spec_resolver
    harness = ports.build(ports.HARNESSES, "fake")
    hub = make_hub(STATE, settings=prefs, shortcuts=shortcuts)
    hub.brain = ports.build(
        ports.BRAINS, "harness_only",
        harness_for=lambda _chat=None: harness,
        spec_for=harness_spec_resolver(prefs, store),
        chats=store,
        cap_for=lambda: prefs["harness_agents_max"])

    host = hub.brain.host

    # RUNNING: a background turn with no live session (survived a socket
    # close, D-1's `_orphaned` path) -- status_for checks this dict first.
    running_id = ids[1]
    hub._orphaned[running_id] = object()
    hub._turn_started[running_id] = time.time() - 300  # 5:00 elapsed
    host.note_action(running_id, "editing web/index.html")

    # WAITING: a child holding one open question.
    waiting_id = ids[3]
    wait_spec = ports.HarnessSpec(model=None, cwd=str(REPO),
                                 session_file=str(STATE / "wait.session"))
    wait_child = _Child(harness=FakeHarness(), spec=wait_spec, stream=None)
    wait_child.questions.add("fake-qid-1")
    host._children[waiting_id] = wait_child
    hub._turn_started[waiting_id] = time.time() - 48
    host.note_action(waiting_id, "confirm: delete build/ and rebuild")

    # ERROR: a turn that failed, no child left.
    error_id = ids[5]
    host.mark_outcome(error_id, "error")
    hub._turn_started[error_id] = time.time() - 48

    # DONE: a held child, nothing running or waiting (AC-59).
    done_id = ids[7]
    done_spec = ports.HarnessSpec(model=None, cwd=str(REPO),
                                 session_file=str(STATE / "done.session"))
    host._children[done_id] = _Child(harness=FakeHarness(), spec=done_spec,
                                     stream=None)

    print(f"[seed] running={running_id} waiting={waiting_id} "
          f"error={error_id} done={done_id}", flush=True)

    butlerd.build_hub = lambda cfg: hub
    app = butlerd.make_app({})
    print(f"[seed] serving on 127.0.0.1:{PORT}", flush=True)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
