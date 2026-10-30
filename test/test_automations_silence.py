"""Proactive P1: silence for unattended runs and failure streaks.
docs/proactive.md sections 2-3 (PR-1..PR-7), AC-475..AC-480.

* SILENCE (AC-475..AC-477): with `silence` on, a completed run whose whole
  answer, first line or last line is the sentinel sends nothing, says
  nothing and leaves no unread dot; its row reads completed with
  `silent: true`. The token mid-sentence is content. A failed, needs-you or
  stopped run is never silenced, whatever its text (AC-476) -- the failure
  this would hide is a broken job that looks quiet. The footer that tells
  the agent about the sentinel is ONE constant appended in ONE place after
  `library.expand` (AC-448 stays a single substitution); an automation saved
  before this feature upgrades with silence OFF.
* STREAKS (AC-478..AC-480): `failStreak` and `lastFailAlertAt` live on the
  automation row and survive an unrelated save and a restart. Only real
  failures count (never a boot recovery, a stop, a skip or needs-you); any
  completed run and re-enabling reset it. Failure 1 delivers, 2..N-1 deliver
  only a reminder per 24 h, failure N turns the automation off (stopping no
  chat) and says so naming the last error.

Real runs go through the fake coding agent and the real Telegram adapter
pointed at `FakeBotApi`; nothing reaches api.telegram.org.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_automations import _auto, _finish, _store, et        # noqa: E402
from test_automations_results import (_device, _run, _setup,   # noqa: E402
                                      _types)
from test_notify import FakeBotApi, _write                    # noqa: E402
from daemon import automations as au                          # noqa: E402
from daemon import library as lib                             # noqa: E402
from daemon import ports, settings                            # noqa: E402
from daemon.adapters import brain_harness, notify_telegram    # noqa: E402
from daemon.adapters.harness_fake import ScriptedTurn         # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """A dead local port as the API base, so nothing here reaches Telegram."""
    monkeypatch.setattr(notify_telegram, "API", "http://127.0.0.1:9")


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBotApi()
    monkeypatch.setattr(notify_telegram, "API", fake.url)
    try:
        yield fake
    finally:
        fake.close()


def _agent(hub, *texts, **kw):
    """The fake coding agent answers `texts`, one agent turn each."""
    hub.brain.host._harness_for = lambda _chat=None: ports.build(
        ports.HARNESSES, "fake",
        script=tuple(ScriptedTurn(t) for t in texts), **kw)


# --------------------------------------------------------------------------- #
# PR-1: the sentinel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("parts", [
    ["[SILENT]"], ["  [silent]  "], ["SILENT"], ["no_reply"], ["No Reply"],
    ["[SILENT]\nI looked at the inbox and nothing came in."],
    ["I looked at the inbox and nothing came in.", "NO_REPLY"],
    ["I looked.\n\n[SILENT]\n"],
])
def test_the_sentinel_on_the_whole_answer_or_its_first_or_last_line(parts):
    assert au.is_silent(parts) is True, parts


@pytest.mark.parametrize("parts", [
    [], [""], ["Nothing new, so I would normally say [SILENT] here."],
    ["The SILENT auction moved to Friday."], ["[SILENT]."],
    ["One.\n[SILENT]\nThree."],
])
def test_the_token_mid_sentence_is_content(parts):
    assert au.is_silent(parts) is False, parts


# --------------------------------------------------------------------------- #
# PR-2, PR-3: one footer, opt-in, upgrade OFF (AC-477)
# --------------------------------------------------------------------------- #
def test_the_footer_is_one_constant_appended_in_one_place():
    """AC-477 and AC-448: the footer has no placeholder, so appending it is
    not a second substitution, and there is exactly one site that appends
    it -- after the one expansion, in `dispatch`."""
    assert isinstance(au.SILENCE_FOOTER, str)
    assert "{" not in au.SILENCE_FOOTER and "}" not in au.SILENCE_FOOTER
    assert "[SILENT]" in au.SILENCE_FOOTER
    code = Path(au.__file__).read_text(encoding="utf-8")
    uses = [m.start() for m in re.finditer(r"\bSILENCE_FOOTER\b", code)]
    assert len(uses) == 2, "the footer is defined once and appended once"
    dispatch = code[code.index("    async def dispatch("):
                    code.index("    async def _mark(")]
    assert dispatch.count("SILENCE_FOOTER") == 1
    # The footer is KNOWN before the expansion (docs/proactive.md PR-29:
    # `{event}` is cut to the room it leaves) and APPENDED once, after it.
    assert dispatch.count("text = text + footer") == 1
    assert dispatch.index("library_mod.expand") < \
        dispatch.index("text = text + footer")
    for other in (ROOT / "daemon").rglob("*.py"):
        if other.name != "automations.py":
            assert "SILENCE_FOOTER" not in other.read_text(
                encoding="utf-8"), other


def test_new_automations_are_silent_and_old_ones_upgrade_with_silence_off(
        tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 10, 0))
    assert a["silence"] is True                          # PR-3: new is ON
    off = _auto(store, recipe, et(2026, 9, 26, 10, 0), silence=False)
    assert off["silence"] is False
    # An automation written before this feature has no `silence` key.
    rows = json.loads((tmp_path / "library" / "automations.json")
                      .read_text(encoding="utf-8"))
    for row in rows:
        row.pop("silence")
    (tmp_path / "library" / "automations.json").write_text(
        json.dumps(rows), encoding="utf-8")
    old = store.save({"id": a["id"], "graceMinutes": 30},
                     et(2026, 9, 26, 11, 0))
    assert old["silence"] is False, "an existing automation went silent"
    with pytest.raises(lib.LibraryRefused, match="silence"):
        store.save({"id": a["id"], "silence": "yes"}, et(2026, 9, 26, 11))


@pytest.mark.asyncio
async def test_the_agent_is_told_about_the_sentinel_only_when_silence_is_on(
        tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    fake = ports.build(ports.HARNESSES, "fake")
    hub.brain.host._harness_for = lambda _chat=None: fake
    await _run(hub, a)
    assert fake.prompts[-1].endswith("\n\n" + au.SILENCE_FOOTER)
    assert fake.prompts[-1].startswith("Review.")
    hub.automations.save({"id": a["id"], "silence": False},
                         hub.scheduler.clock())
    await _run(hub, a)
    assert au.SILENCE_FOOTER not in fake.prompts[-1]


# --------------------------------------------------------------------------- #
# PR-4: the outcome (AC-475)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [("[SILENT]",),
                                   ("I checked the inbox.", "NO_REPLY")])
async def test_a_silent_run_sends_nothing_says_nothing_and_is_not_unread(
        tmp_path, monkeypatch, bot, texts):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    desk = _device(hub, "127.0.0.1", "desk")
    _agent(hub, *texts)
    done = await _run(hub, a)
    assert done["status"] == "completed", done
    assert done["silent"] is True
    assert done["seenAt"] and done["seenAt"] == done["endedAt"]
    assert done["chat"] and done["chat"] not in hub.automations.unseen_chats()
    [entry] = done["notify"]
    assert entry["event"] == "completed"
    assert entry["spoken"] == "silent" and entry["channels"] == {}
    assert bot.requests == [], "a silent run messaged the owner"
    assert "announce" not in _types(desk), "a silent run spoke at the desk"


@pytest.mark.asyncio
async def test_the_token_mid_sentence_delivers_normally(
        tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    _agent(hub, "Two invoices came in; I would only say SILENT if none did.")
    done = await _run(hub, a)
    assert done["status"] == "completed" and not done.get("silent")
    assert not done["seenAt"]
    assert len(bot.requests) == 1


@pytest.mark.asyncio
async def test_with_silence_off_the_sentinel_is_delivered(
        tmp_path, monkeypatch, bot):
    """The upgrade promise: an existing automation behaves as it did."""
    hub, _row, a = _setup(tmp_path, monkeypatch)
    hub.automations.save({"id": a["id"], "silence": False},
                         hub.scheduler.clock())
    _write(hub.channels.folder, "owner")
    _agent(hub, "[SILENT]")
    done = await _run(hub, a)
    assert not done.get("silent") and not done["seenAt"]
    assert len(bot.requests) == 1


# --------------------------------------------------------------------------- #
# AC-476: never silence a problem
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_failed_run_saying_the_sentinel_is_delivered(
        tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    # A failing harness's partial text never reaches the record (the turn
    # ends on Butler's own failure line), so THAT line is made the sentinel:
    # the failed run's whole answer is then exactly "[SILENT]".
    monkeypatch.setattr(brain_harness.HarnessConverse, "FAILED", "[SILENT]")
    _agent(hub, "Never said.", fail_on="Review")
    done = await _run(hub, a)
    assert done["status"] == "failed", done
    assert "[SILENT]" in done["summary"], \
        "the instrument: the failed run must actually have said the sentinel"
    assert not done.get("silent") and not done["seenAt"]
    assert len(bot.requests) == 1
    assert ": failed" in bot.requests[0]["body"]["text"]


@pytest.mark.asyncio
async def test_needs_you_is_delivered_even_when_the_end_is_silent(
        tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    _write(hub.channels.folder, "owner")
    _agent(hub, "[SILENT]", ask=ports.Ask(qid="q-s", method="confirm",
                                          message="Archive 40 mails?"))
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if len(bot.requests) == 1:
            break
        await asyncio.sleep(0.01)
    assert "needs you" in bot.requests[0]["body"]["text"]
    await hub.answer_question("q-s", ports.Answer(confirmed=True))
    await _finish(hub)
    done = hub.automations.runs(a["id"])[0]
    assert done["status"] == "completed" and done["silent"] is True
    assert [e["event"] for e in done["notify"]] == ["needs_you", "completed"]
    assert len(bot.requests) == 1


@pytest.mark.asyncio
async def test_a_stopped_run_is_never_marked_silent(tmp_path, monkeypatch):
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    _agent(hub, "[SILENT]", ask=ports.Ask(qid="q-t", method="confirm",
                                          message="Go on?"))
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if hub.automations.runs(a["id"])[0]["status"] == "needs_you":
            break
        await asyncio.sleep(0.01)
    chat = hub.automations.runs(a["id"])[0]["chat"]
    assert await hub.stop_conversation(chat)
    await _finish(hub)
    done = hub.automations.runs(a["id"])[0]
    assert done["status"] == "stopped", done
    assert not done.get("silent") and not done["seenAt"]


# --------------------------------------------------------------------------- #
# PR-5: the streak is stored (AC-478)
# --------------------------------------------------------------------------- #
def test_the_streak_survives_an_unrelated_save_and_a_restart(tmp_path):
    store, library, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    assert a["failStreak"] == 0 and a["lastFailAlertAt"] == ""
    store.failure(a["id"], et(2026, 9, 26, 9, 0), 5)
    store.failure(a["id"], et(2026, 9, 26, 10, 0), 5)
    saved = store.save({"id": a["id"], "graceMinutes": 30},
                       et(2026, 9, 26, 11, 0))
    assert saved["failStreak"] == 2
    assert au.parse_iso(saved["lastFailAlertAt"]) == et(2026, 9, 26, 9, 0)
    again = au.AutomationStore(tmp_path / "library", library, store.tz)
    assert again.get(a["id"])["failStreak"] == 2
    assert again.get(a["id"])["lastFailAlertAt"] == saved["lastFailAlertAt"]


# --------------------------------------------------------------------------- #
# PR-6: what counts (AC-479)
# --------------------------------------------------------------------------- #
def test_a_success_and_re_enabling_reset_the_streak(tmp_path):
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    store.failure(a["id"], et(2026, 9, 26, 9, 0), 5)
    store.failure(a["id"], et(2026, 9, 26, 10, 0), 5)
    store.succeeded(a["id"])
    assert store.get(a["id"])["failStreak"] == 0
    assert store.failure(a["id"], et(2026, 9, 26, 11, 0), 5) == ("first", 1)
    store.set_enabled([a["id"]], False, et(2026, 9, 26, 12, 0))
    assert store.get(a["id"])["failStreak"] == 1     # off is not a reset
    store.set_enabled([a["id"]], True, et(2026, 9, 26, 12, 0))
    assert store.get(a["id"])["failStreak"] == 0


def test_a_boot_recovery_does_not_count(tmp_path):
    """The Gherkin's "a restart is not a failure streak": recover() marks
    the in-flight run failed and leaves the streak alone."""
    store, _lib, recipe, _ = _store(tmp_path)
    a = _auto(store, recipe, et(2026, 9, 26, 8, 0))
    store.failure(a["id"], et(2026, 9, 26, 8, 30), 5)
    run = store.run_now(a["id"], et(2026, 9, 26, 9, 0))
    store.update_run(run["id"], status="running")
    [changed] = store.recover(et(2026, 9, 26, 9, 5))
    assert changed["status"] == "failed"
    assert store.get(a["id"])["failStreak"] == 1


@pytest.mark.asyncio
async def test_stops_skips_and_needs_you_neither_count_nor_reset(
        tmp_path, monkeypatch):
    """Through the scheduler: a failing run counts, a stop, a skip and a
    question in between change nothing, and a completed run resets."""
    hub, _row, a = _setup(tmp_path, monkeypatch, channels=())
    _agent(hub, "No.", fail_on="Review")
    await _run(hub, a)
    assert hub.automations.get(a["id"])["failStreak"] == 1
    # A stop while it asks: needs_you, then stopped.
    _agent(hub, "Fine.", ask=ports.Ask(qid="q-u", method="confirm",
                                       message="Go on?"))
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    for _ in range(300):
        if hub.automations.runs(a["id"])[0]["status"] == "needs_you":
            break
        await asyncio.sleep(0.01)
    assert await hub.stop_conversation(hub.automations.runs(a["id"])[0]["chat"])
    await _finish(hub)
    assert hub.automations.runs(a["id"])[0]["status"] == "stopped"
    assert hub.automations.get(a["id"])["failStreak"] == 1
    # A skip: the scheduled occurrence missed for days.
    hub.scheduler.clock.at = et(2026, 10, 2, 12, 0)
    await hub.scheduler.tick()
    await _finish(hub)
    assert hub.automations.runs(a["id"])[0]["status"] == "skipped_missed"
    assert hub.automations.get(a["id"])["failStreak"] == 1
    # A completed run resets.
    _agent(hub, "All good.")
    await _run(hub, a)
    assert hub.automations.get(a["id"])["failStreak"] == 0


# --------------------------------------------------------------------------- #
# PR-7: who hears about it (AC-480)
# --------------------------------------------------------------------------- #
def test_the_setting_is_in_the_notify_group():
    spec = settings.BY_KEY["fail_disable_after"]
    assert spec.group == "notify" and spec.kind == "number"
    assert spec.default == 5 and spec.minimum == 0 and spec.maximum == 20


@pytest.mark.asyncio
async def test_a_broken_automation_stops_pestering_and_turns_itself_off(
        tmp_path, monkeypatch, bot):
    """The Gherkin's "A broken automation stops pestering": failure 1
    delivers; 2..4 deliver nothing except ONE reminder after 24 h; failure 5
    turns it off, naming the last error -- and stops no chat."""
    hub, _row, a = _setup(tmp_path, monkeypatch)
    # 02:00 is inside the default quiet hours, which would HOLD the desk
    # lines (docs/proactive.md PR-16, test_quiet_hours.py); this test counts
    # the streak's deliveries, so the window is off.
    hub.settings.set("quiet_hours", False)
    _write(hub.channels.folder, "owner")
    desk = _device(hub, "127.0.0.1", "desk")
    stopped = []
    monkeypatch.setattr(hub, "stop_conversation",
                        lambda chat: stopped.append(chat))
    _agent(hub, "No.", fail_on="Review")
    clock = hub.scheduler.clock
    t0 = et(2026, 9, 28, 2, 0)
    sent = []
    for hours in (0, 1, 25, 26, 27):
        clock.at = t0 + timedelta(hours=hours)
        await _run(hub, a)
        sent.append(len(bot.requests))
    assert sent == [1, 1, 2, 2, 3], sent
    texts = [r["body"]["text"] for r in bot.requests]
    assert "has failed 3 times in a row" in texts[1]
    assert "failed 5 times in a row and was turned off" in texts[2]
    assert "Last error: " in texts[2] and "failed" in texts[2]
    now = hub.automations.get(a["id"])
    assert now["enabled"] is False and now["failStreak"] == 5
    assert stopped == [], "turning it off stopped a chat"
    runs = hub.automations.runs(a["id"])           # newest first
    assert [r["status"] for r in runs] == ["failed"] * 5
    quiet = [r for r in runs if r["notify"][0].get("spoken") == "quiet"]
    assert len(quiet) == 2 and all(not r["seenAt"] for r in quiet), \
        "a quiet failure must keep its unread dot"
    assert all(r["notify"][0]["channels"] == {} for r in quiet)
    assert runs[0]["notify"][0]["turnedOff"] is True
    assert [r["notify"][0]["streak"] for r in runs] == [5, 4, 3, 2, 1]
    lines = [json.loads(m)["text"] for m in desk.ws.sent if isinstance(m, str)
             and json.loads(m)["type"] == "announce"]
    assert len(lines) == 3 and "turned off" in lines[-1], lines


@pytest.mark.asyncio
async def test_zero_never_turns_it_off(tmp_path, monkeypatch, bot):
    hub, _row, a = _setup(tmp_path, monkeypatch)
    hub.settings.set("fail_disable_after", 0)
    _write(hub.channels.folder, "owner")
    _agent(hub, "No.", fail_on="Review")
    clock = hub.scheduler.clock
    for minutes in range(6):
        clock.at = et(2026, 9, 28, 2, minutes)
        await _run(hub, a)
    now = hub.automations.get(a["id"])
    assert now["enabled"] is True and now["failStreak"] == 6
    assert len(bot.requests) == 1                  # failure 1 only, same day
