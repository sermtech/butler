"""A coding agent's question, asked and answered over WhatsApp (M8).

docs/messaging.md section 7: MS-20 (the INBOUND capability, AC-525), MS-21
(which questions go out, AC-526), MS-22 (the message and what a reply
means, AC-527), MS-24 (one answer door, late answers, AC-529).

The assertion with teeth is the ANSWER COUNT ON THE CHILD
(`test_harness_question.py`'s rule): a reply the daemon reads as an answer
reaches a blocked coding agent and unblocks it, and with two questions open
an unquoted "1" could confirm the wrong agent's action -- which cannot be
undone. So every test that says "this is not an answer" also says the child
was handed nothing.

Driven through the FAKE sidecar process (`test/assets/fake_wa_sidecar.py`):
owner messages are pushed into its real `/events` stream, and what Butler
said in the chat is read back from its recorded `/reply` requests (the
fake's message ids are R1, R2, ... in request order). The desk test uses a
recording door instead, because it asserts that nothing is sent at all.
Nothing here touches WhatsApp.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_automations import _finish, _project, _with_clock   # noqa: E402
from test_harness_question import _hub as _desk_hub            # noqa: E402
from test_harness_question import frames, running_turn, until  # noqa: E402
from test_library_voice import _harness_hub                    # noqa: E402
from test_schedule import EASTERN                              # noqa: E402,F401
from test_whatsapp_events import (OWN, _push, _until,          # noqa: E402
                                  fake_argv)                   # noqa: F401
from daemon import notify, ports                               # noqa: E402
from daemon import whatsapp as whatsapp_mod                    # noqa: E402
from daemon.adapters import notify_whatsapp                    # noqa: E402
from datetime import datetime                                  # noqa: E402

CONFIRM = ports.Ask(qid="q-confirm", method="confirm",
                    message="Delete 40 old drafts?")
SELECT = ports.Ask(qid="q-select", method="select", title="Backlog",
                   message="How should I proceed?",
                   options=("Run it all", "Skip it", "Just report"))
HEAD = "Butler needs you"


def et(*args):
    return datetime(*args, tzinfo=EASTERN)


# --------------------------------------------------------------------------- #
# what a reply means (MS-22), as pure rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,confirmed", [
    ("1", True), ("yes", True), ("YES", True), ("Sim.", True),
    ("2", False), ("no", False), ("nao", False), ("Não", False),
    (" No! ", False)])
def test_a_confirm_reads_numbers_and_yes_no_in_both_languages(text,
                                                              confirmed):
    assert notify.answer_for("confirm", (), text) == ports.Answer(
        confirmed=confirmed)


def test_a_confirm_never_guesses():
    for text in ("3", "maybe", "yes please", ""):
        assert notify.answer_for("confirm", (), text) == notify.CONFIRM_HINT


def test_a_select_takes_the_number_or_the_exact_option_text():
    opts = SELECT.options
    assert notify.answer_for("select", opts, "2") == ports.Answer(
        value="Skip it")
    assert notify.answer_for("select", opts, "just REPORT") == ports.Answer(
        value="Just report")
    for text in ("0", "4", "skip", "yes"):
        assert notify.answer_for("select", opts, text) == (
            "Reply with a number from 1 to 3.")


def test_an_input_takes_the_reply_text_as_the_value():
    assert notify.answer_for("input", (), "  feature/phone  ") == (
        ports.Answer(value="feature/phone"))


def test_the_question_message_is_numbered_text_MS_22():
    assert notify_whatsapp.question_text(CONFIRM, "Tidy - Mon") == (
        "Butler needs you (Tidy - Mon): Delete 40 old drafts?\n"
        "1) Yes\n2) No\n"
        "Reply with the number, or reply to this message.")
    assert notify_whatsapp.question_text(SELECT, "") == (
        "Butler needs you: Backlog\nHow should I proceed?\n"
        "1) Run it all\n2) Skip it\n3) Just report\n"
        "Reply with the number, or reply to this message.")


def test_whatsapp_declares_the_inbound_capability_and_ports_stay_six():
    """AC-525: a capability on a notifier class, not a seventh port."""
    assert ports.inbound(ports.NOTIFIERS["whatsapp"])
    assert ports.inbound(ports.NOTIFIERS["telegram"])     # T1
    registries = [n for n in ("SOURCES", "ASRS", "TTSS", "BRAINS",
                              "HARNESSES", "NOTIFIERS", "INBOUNDS")
                  if isinstance(getattr(ports, n, None), dict)]
    assert registries == ["SOURCES", "ASRS", "TTSS", "BRAINS", "HARNESSES",
                          "NOTIFIERS"]


# --------------------------------------------------------------------------- #
# the live path: fake sidecar, real WhatsApp owner, real hub
# --------------------------------------------------------------------------- #
def _replies(records: Path) -> list[dict]:
    """Every /reply Butler made, oldest first, with the id the fake gave
    it (R<n> in request order)."""
    f = records / "requests.jsonl"
    rows = ([json.loads(x) for x in f.read_text("utf-8").splitlines()]
            if f.exists() else [])
    out = [r["body"] for r in rows if r["path"] == "/reply"]
    return [{**b, "ref": f"R{i}"} for i, b in enumerate(out, 1)]


def _questions(records: Path) -> list[dict]:
    return [r for r in _replies(records) if r["text"].startswith(HEAD)]


class Children:
    """`harness_for`: the n-th child asks `asks[n]` (or nothing)."""

    def __init__(self, *asks):
        self.asks = asks
        self.made = []

    def __call__(self, _chat=None):
        ask = self.asks[len(self.made)] if len(self.made) < len(
            self.asks) else ()
        child = ports.build(ports.HARNESSES, "fake", ask=ask)
        self.made.append(child)
        return child

    def answers(self):
        return [a for c in self.made for a in c.answers]


async def _live(tmp_path, monkeypatch, children, **settings):
    monkeypatch.setenv("FAKE_WA_LINKED", "1")
    hub = _harness_hub(tmp_path, monkeypatch)
    hub.settings.set("speak", False)
    hub.settings.set("phone_reply", "text")
    hub.settings.set("whatsapp_enabled", True)
    for key, value in settings.items():
        hub.settings.set(key, value)
    hub.brain.host._harness_for = children
    wa = whatsapp_mod.WhatsApp(hub.settings, tmp_path / "wa")
    wa.inbound = whatsapp_mod.Inbound(hub, wa, busy_poll_s=0.01,
                                      queue_folder=tmp_path / "phone",
                                      typing_every_s=60.0)
    whatsapp_mod.serve(wa)
    await wa.start()
    await _until(lambda: wa.state == "running", "the sidecar running")
    return hub, wa, tmp_path / "wa"


async def _close(hub, wa):
    try:
        for task in list(wa.inbound._workers.values()):
            if not task.done():
                task.cancel()
        await hub.phone_questions.stop()
    finally:
        whatsapp_mod.withdraw(wa)
        await wa.stop()


def _owner(mid, text, quoted=None):
    return {"type": "message", "tag": "owner", "id": mid, "jid": OWN,
            "text": text, "ts": 1790950000, "quoted": quoted}


async def _run(hub, recipe_name, project, clock):
    recipe = hub.library.recipes.save(None, recipe_name, project,
                                      f"{recipe_name} now.")
    a = hub.automations.save({"recipe": recipe.id, "schedule": {
        "preset": "daily", "time": "09:00"}}, clock())
    run = await hub.scheduler.run_now(a["id"])
    await hub.scheduler.dispatch(run)
    return a


@pytest.mark.asyncio
async def test_a_phone_turns_question_goes_to_the_chat_and_a_quoted_reply_answers_it(  # noqa: E501
        tmp_path, monkeypatch, fake_argv):
    """AC-526 and AC-527: the question raised during a WhatsApp turn is
    sent there; a reply QUOTING it answers it, once, and the turn finishes
    with its ordinary answer quoted to the original message."""
    kids = Children(CONFIRM)
    hub, wa, records = await _live(tmp_path, monkeypatch, kids)
    try:
        await _push(wa, _owner("M1", "tidy the drafts"))
        await _until(lambda: _questions(records), "the question in the chat")
        [q] = _questions(records)
        assert q["text"].startswith(
            "Butler needs you (tidy the drafts): Delete 40 old drafts?\n"
            "1) Yes\n2) No\n"), q["text"]
        assert "quote" not in q
        await _push(wa, _owner("M2", "1", quoted=q["ref"]))
        await _until(lambda: kids.answers(), "the child answered")
        assert kids.answers() == [("q-confirm",
                                   ports.Answer(confirmed=True))]
        await _until(lambda: any(r.get("quote") == "M1"
                                 for r in _replies(records)),
                     "the turn's own answer")
        said = [(r["text"], r.get("quote")) for r in _replies(records)]
        assert ("Answered: yes", "M2") in said
        assert hub.open_question("q-confirm") is None
    finally:
        await _close(hub, wa)


@pytest.mark.asyncio
async def test_phone_questions_off_sends_nothing(tmp_path, monkeypatch,
                                                 fake_argv):
    kids = Children(CONFIRM)
    hub, wa, records = await _live(tmp_path, monkeypatch, kids,
                                   phone_questions=False)
    try:
        await _push(wa, _owner("M1", "tidy the drafts"))
        await _until(lambda: hub.open_question("q-confirm"), "the question")
        await asyncio.sleep(0.3)
        assert _questions(records) == []
        await hub.answer_question("q-confirm", ports.Answer(confirmed=False))
    finally:
        await _close(hub, wa)


@pytest.mark.asyncio
async def test_a_runs_question_goes_out_and_a_whatsapp_answer_completes_the_run(  # noqa: E501
        tmp_path, monkeypatch, fake_argv):
    """AC-526 for a scheduled run (modelled on test_automations.py's
    needs_you test): unquoted "no" with exactly one question open answers
    it, and the run completes."""
    kids = Children(CONFIRM)
    hub, wa, records = await _live(tmp_path, monkeypatch, kids)
    try:
        clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
        a = await _run(hub, "Tidy", _project(hub, tmp_path), clock)
        await _until(lambda: _questions(records), "the run's question")
        [q] = _questions(records)
        assert q["text"].startswith("Butler needs you (Tidy - "), q["text"]
        assert hub.automations.runs(a["id"])[0]["status"] == "needs_you"
        await _push(wa, _owner("M1", "No"))
        await _until(lambda: kids.answers(), "the child answered")
        assert kids.answers() == [("q-confirm",
                                   ports.Answer(confirmed=False))]
        await _finish(hub)
        done = hub.automations.runs(a["id"])[0]
        assert done["status"] == "completed" and done["neededYou"] is True
        assert ("Answered: no", "M1") in [(r["text"], r.get("quote"))
                                          for r in _replies(records)]
    finally:
        await _close(hub, wa)


@pytest.mark.asyncio
async def test_with_TWO_questions_open_an_unquoted_1_is_a_normal_message(
        tmp_path, monkeypatch, fake_argv):
    """AC-527, the one that cannot be undone: "1" could mean either agent,
    so it answers neither and becomes an ordinary owner turn. A QUOTED
    reply then answers exactly the question it quoted."""
    kids = Children(
        ports.Ask(qid="q-a", method="confirm", message="Delete A?"),
        ports.Ask(qid="q-b", method="confirm", message="Delete B?"))
    hub, wa, records = await _live(tmp_path, monkeypatch, kids)
    try:
        clock = _with_clock(hub, et(2026, 9, 28, 8, 0))
        a = await _run(hub, "Tidy A", _project(hub, tmp_path, "a", "A"),
                       clock)
        await _until(lambda: len(_questions(records)) == 1, "question A")
        b = await _run(hub, "Tidy B", _project(hub, tmp_path, "b", "B"),
                       clock)
        await _until(lambda: len(_questions(records)) == 2, "question B")
        qa, qb = _questions(records)
        assert "Delete A?" in qa["text"] and "Delete B?" in qb["text"]

        await _push(wa, _owner("M1", "1"))
        # A normal message: a phone turn, answered by a third child that
        # asks nothing, quoted to M1.
        await _until(lambda: any(r.get("quote") == "M1"
                                 for r in _replies(records)),
                     "the ordinary answer to M1")
        assert kids.answers() == [], (
            f"an unquoted '1' with two questions open reached a child: "
            f"{kids.answers()}")
        assert not any(r["text"].startswith("Answered")
                       for r in _replies(records))

        await _push(wa, _owner("M2", "yes", quoted=qb["ref"]))
        await _until(lambda: kids.answers(), "the quoted answer")
        assert kids.answers() == [("q-b", ports.Answer(confirmed=True))]
        assert hub.open_question("q-a") is not None
        await hub.answer_question("q-a", ports.Answer(confirmed=False))
        await _finish(hub)
        assert [hub.automations.runs(x["id"])[0]["status"]
                for x in (a, b)] == ["completed", "completed"]
    finally:
        await _close(hub, wa)


@pytest.mark.asyncio
async def test_a_late_quoted_answer_is_told_why_and_never_reaches_the_child(
        tmp_path, monkeypatch, fake_argv):
    """AC-529: answered on the page first, then quoted on the phone -- the
    phone is told the question was already closed and why, and the child
    holds exactly ONE answer."""
    kids = Children(CONFIRM)
    hub, wa, records = await _live(tmp_path, monkeypatch, kids)
    try:
        await _push(wa, _owner("M1", "tidy the drafts"))
        await _until(lambda: _questions(records), "the question")
        [q] = _questions(records)
        got = await hub.answer_question("q-confirm",
                                        ports.Answer(confirmed=False))
        assert got["ok"]
        await _push(wa, _owner("M2", "1", quoted=q["ref"]))
        await _until(lambda: any(r.get("quote") == "M2"
                                 for r in _replies(records)), "the refusal")
        [late] = [r["text"] for r in _replies(records)
                  if r.get("quote") == "M2"]
        assert late == ("That question was already closed: that question "
                        "was already answered")
        assert kids.answers() == [("q-confirm",
                                   ports.Answer(confirmed=False))]
    finally:
        await _close(hub, wa)


@pytest.mark.asyncio
async def test_an_answer_the_child_no_longer_holds_is_reported_not_taken(
        tmp_path, monkeypatch, fake_argv):
    """AC-529 through the door's own refusal: the question is still open in
    the hub but the child let go of it, so `answer_question` refuses, and
    the chat says so instead of "Answered"."""
    kids = Children(CONFIRM)
    hub, wa, records = await _live(tmp_path, monkeypatch, kids)
    try:
        await _push(wa, _owner("M1", "tidy the drafts"))
        await _until(lambda: _questions(records), "the question")
        [q] = _questions(records)
        child = kids.made[0]
        future = child._blocked.pop("q-confirm")     # it let go on its own
        await _push(wa, _owner("M2", "yes", quoted=q["ref"]))
        await _until(lambda: any(r.get("quote") == "M2"
                                 for r in _replies(records)), "the refusal")
        [said] = [r["text"] for r in _replies(records)
                  if r.get("quote") == "M2"]
        assert said.startswith("That question was already closed: the "
                               "coding agent that asked is gone"), said
        assert kids.answers() == []
        future.set_result(False)
    finally:
        await _close(hub, wa)


# --------------------------------------------------------------------------- #
# a desk turn's question stays at the desk (AC-526)
# --------------------------------------------------------------------------- #
class RecordingDoor:
    """The WhatsApp owner as the notifier reaches it: records every reply."""

    def __init__(self):
        self.questions = None
        self.said: list[str] = []

    async def own_jid(self):
        return OWN

    async def reply(self, jid, *, text=None, quote=None, voice=None):
        self.said.append(text)
        return f"R{len(self.said)}"


@pytest.mark.asyncio
async def test_a_desk_turns_question_is_NOT_sent_to_the_phone(tmp_path,
                                                             monkeypatch):
    hub, s, harness, _chats = _desk_hub(tmp_path, ask=(CONFIRM,))
    hub.settings.set("whatsapp_enabled", True)
    assert hub.settings.get("phone_questions") is True
    door = RecordingDoor()
    monkeypatch.setattr(whatsapp_mod, "_door", door)
    async with running_turn(hub, s):
        await until(lambda: frames(s, "question"), "the question frame")
        await asyncio.sleep(0.2)
        assert not hub.phone_questions._tasks
        assert door.said == [], f"a desk question went to the phone: {door.said}"
        await hub.answer_question("q-confirm", ports.Answer(confirmed=True))
    assert harness.answers == [("q-confirm", ports.Answer(confirmed=True))]
