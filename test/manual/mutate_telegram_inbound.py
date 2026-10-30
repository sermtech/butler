"""D-13 mutation harness for two-way Telegram, phases T1..T3
(docs/messaging.md section 8).

Each entry breaks ONE rule, then asks two questions by pytest EXIT CODE only:

  A: do the NAMED tests fail?           expected exit 1
  B: does the rest of the SCOPE pass?   expected exit 0

The rules, and the failure each one prevents:
  * owner only: a message from someone else in the owner's chat is used
    (AC-530) -- a stranger driving an agent that may hold full tools;
  * the read position persists (AC-531) -- a restart loses what arrived
    while Butler was down;
  * a 409 backs off (AC-532) -- a tight loop fighting the other reader;
  * a tap answers ITS question (AC-528) -- a button that loses its token
    becomes an unquoted "1";
  * an unquoted bare answer needs exactly one open question (AC-527, the
    shared rule Telegram inherits) -- "1" confirming the wrong agent.

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_telegram_inbound.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
TG = "test/test_telegram_inbound.py"
WAQ = "test/test_whatsapp_questions.py"
SCOPE = [TG, "test/test_notify.py", WAQ, "test/test_whatsapp_inbound.py",
         "test/test_whatsapp_events.py"]
RUN_TIMEOUT_S = 600
ADAPTER = "daemon/adapters/notify_telegram.py"

MUTATIONS = [
    (
        "a non-owner sender in the owner's chat is accepted",
        ADAPTER,
        "        return bool(owner) and str(chat_id) == owner and "
        "str(from_id) == owner\n",
        "        return bool(owner) and str(chat_id) == owner  # MUTATED\n",
        [f"{TG}::test_ONLY_THE_OWNERS_MESSAGES_are_used_and_nothing_else_is_"
         f"logged",
         f"{TG}::test_a_strangers_tap_is_dropped"],
    ),
    (
        "the offset is not persisted",
        ADAPTER,
        "                await asyncio.to_thread(self._write_offset, name, "
        "bot, offset)\n                try:\n",
        "                pass  # MUTATED: not persisted\n                try:\n",
        [f"{TG}::test_A_RESTART_NEITHER_REPLAYS_NOR_LOSES_an_update"],
    ),
    (
        "a 409 is retried at once (tight loop)",
        ADAPTER,
        "            table = self.conflict_backoff_s\n"
        "            return table[min(conflicts, len(table) - 1)]\n",
        "            return 0.0  # MUTATED: no backoff\n",
        [f"{TG}::test_a_409_is_the_channels_problem_and_polling_BACKS_OFF"],
    ),
    (
        "a tap loses its question and becomes an unquoted answer",
        ADAPTER,
        "                said = await self.questions(ports.Incoming(number, "
        "token))\n",
        "                said = await self.questions(ports.Incoming(number, "
        "\"\"))  # MUTATED\n",
        [f"{TG}::test_with_TWO_open_a_tap_answers_ITS_question_and_a_bare_1_"
         f"neither",
         f"{TG}::test_a_LATE_TAP_gets_the_closed_reason_and_never_reaches_"
         f"the_child"],
    ),
    (
        "an unquoted bare answer takes the first open question",
        "daemon/notify.py",
        "            if len(open_) != 1:\n                return None\n"
        "            [sent] = open_.values()\n",
        "            if not open_:\n                return None\n"
        "            sent = next(iter(open_.values()))  # MUTATED\n",
        [f"{TG}::test_with_TWO_open_a_tap_answers_ITS_question_and_a_bare_1_"
         f"neither",
         f"{WAQ}::test_with_TWO_questions_open_an_unquoted_1_is_a_normal_"
         f"message"],
    ),
]


def run(args: list[str]) -> int:
    try:
        return subprocess.run(
            [str(PY), "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider",
             *args], cwd=str(REPO),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        print(f"       (did not finish inside {RUN_TIMEOUT_S}s)")
        return 2


def clear_caches() -> None:
    for root in (REPO / "daemon", REPO / "test"):
        for cache in root.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def main() -> int:
    bad = 0
    for name, rel, before, after, tests in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(before) != 1:
            print(f"[SKIP] {name}: {text.count(before)} matches in {rel}")
            bad += 1
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(before, after))
        assert path.read_bytes() != original, "the mutation did not apply"
        clear_caches()
        try:
            named = [run([t, "-q"]) for t in tests]
            rest = run([*SCOPE, "-q", "-m", "not slow",
                        *sum([["--deselect", t] for t in tests], [])])
        finally:
            path.write_bytes(original)
            clear_caches()
        ok = all(n == 1 for n in named) and rest == 0
        bad += not ok
        print(f"[{'OK ' if ok else 'BAD'}] {name}: named exits {named}, "
              f"rest exit {rest}", flush=True)
    assert all((REPO / rel).read_bytes().decode("utf-8").count("MUTATED") == 0
               for _, rel, *_ in MUTATIONS), "a mutation was left in place"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
