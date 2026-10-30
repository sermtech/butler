"""Routing accuracy before and after AC-375's sentence. NOT collected by pytest.

docs/identity-memory.md 4.4 and phase 3: `ROUTER_SYSTEM` gains one sentence
sending questions about the owner, about Butler, or about remembered notes to
`converse`, because the router answers short questions ITSELF and is shown
neither the identity nor the memory. A schema change here once cost a point
of routing accuracy (dialogue.py docstring), so the sentence is kept only if
the measurement says it does not hurt.

THE SET. The ten hand-labelled utterances behind docs/reflex-serving.md 4 were
never committed -- only four of them are quoted there -- so there is no
"existing set" to re-run verbatim. This one keeps the quoted ones whose labels
are not disputed, adds unambiguous items for every route so a REGRESSION on
ordinary routing is visible, and adds the class the sentence is for. The
split is reported separately, because "the sentence helped personal questions"
and "the sentence broke ordinary routing" are different findings.

ROUND-ROBIN, both arms in the same minutes: a provider's behaviour drifts by
the hour, so two arms run an hour apart measure the hour (CLAUDE.md, the Zen
entry). Each repetition asks every item under BOTH prompts back to back.

    .venv/Scripts/python.exe -X utf8 test/manual/measure_router_personal.py \
        --settings TMP/identity-state/settings.json --reps 2

It reads the settings file it is given and the real pi registry, writes
nothing, and prints no key.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from daemon import connections as conn_mod, dialogue  # noqa: E402
from daemon.ports import TurnContext                   # noqa: E402
from daemon.settings import Settings                   # noqa: E402

# (utterance, expected route, class)
ORDINARY = [
    ("what time is it", "answer"),
    ("what is the capital of france", "answer"),
    ("how many days are in a leap year", "answer"),
    ("talk me through whether butler should use a message bus", "converse"),
    ("explain how garbage collection works in python and when it matters",
     "converse"),
    ("fix the failing test in the endpointing module", "dispatch"),
    ("refactor the settings page code to remove the duplication", "dispatch"),
    ("can you do the thing", "clarify"),
    ("change it", "clarify"),
]
PERSONAL = [
    ("what is my name", "converse"),
    ("what's your name", "converse"),
    ("who are you", "converse"),
    ("what do you remember about me", "converse"),
    ("what is the gate code I asked you to remember", "converse"),
    ("how should you address me", "converse"),
    ("when is my daughter's birthday", "converse"),
    ("what are my current priorities", "converse"),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--before", required=True,
                    help="a file holding the HEAD ROUTER_SYSTEM text")
    args = ap.parse_args()

    before = Path(args.before).read_text(encoding="utf-8")
    after = dialogue.ROUTER_SYSTEM
    if before == after:
        print("the two prompts are identical; nothing to compare")
        return 2
    prefs = Settings(Path(args.settings))
    conns = conn_mod.ConnectionStore(Path(args.settings).parent
                                     / "connections.json")
    router = dialogue.Router(conn_mod.SettingsResolver(conns, prefs))
    target = conn_mod.SettingsResolver(conns, prefs).resolve("router")
    print(f"router: {target.model} (reasoning off: "
          f"{prefs.get('router_reasoning') is False})")

    arms = {"before": before, "after": after}
    score = {a: {"ordinary": [0, 0], "personal": [0, 0]} for a in arms}
    misses = {a: [] for a in arms}
    turn = 0
    for rep in range(args.reps):
        for cls, items in (("ordinary", ORDINARY), ("personal", PERSONAL)):
            for text, want in items:
                for arm, prompt in arms.items():
                    dialogue.ROUTER_SYSTEM = prompt
                    turn += 1
                    t0 = time.monotonic()
                    v = await router.verdict(
                        text, TurnContext(turn_id=turn, mode="secretary"))
                    got = v.route if v else "(unusable)"
                    print(f"  rep {rep} {arm:6s} {got:10s} want {want:9s} "
                          f"{time.monotonic() - t0:5.1f}s  {text}", flush=True)
                    score[arm][cls][1] += 1
                    if got == want:
                        score[arm][cls][0] += 1
                    else:
                        misses[arm].append(f"{text!r} -> {got} "
                                           f"(want {want})")
    dialogue.ROUTER_SYSTEM = after
    for arm in arms:
        o, p = score[arm]["ordinary"], score[arm]["personal"]
        print(f"{arm:7s} ordinary {o[0]}/{o[1]}  personal {p[0]}/{p[1]}  "
              f"total {o[0] + p[0]}/{o[1] + p[1]}")
        for m in misses[arm]:
            print(f"          miss: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
