"""Working or stuck: the heartbeat, its evidence, and the timeouts behind it.

`docs/turn-feedback.md`, AC-387..AC-396 -- CLAUDE.md backlog 2026-09-16
item 4 plus three defects on the same path. Each test names the failure it
would catch; the lesson behind the whole file is the backlog entry's own: a
session polled a loading daemon too tightly and read SILENCE AS DEATH.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_harness_idle import build, drive                     # noqa: E402
from test_harness_question import _hub, _http, frames, until   # noqa: E402
from test_harness_question import running_turn                  # noqa: E402
from daemon import dialogue, hub as hub_mod, llm                # noqa: E402
from daemon.adapters import brain_harness as bh, harness_pi     # noqa: E402
from daemon.ports import (Activity, AgentEnd, TextDelta,        # noqa: E402
                          ToolEnd, ToolStart, WorkerEvent)

IDLE_S = 0.4
BEAT_S = 0.05
EVIDENCE = {"elapsed_ms", "phase", "detail", "chars", "thought_chars",
            "last_event_ms"}


class _Caps:
    native_resume = False


class Gapped:
    """A child that emits `(delay, event)` pairs. `None` STALLS: nothing more
    until the child is disposed -- or, with `deaf=True`, ever, which is the
    child a stop can only end by cancelling the reader."""

    caps = _Caps()

    def __init__(self, script, deaf: bool = False):
        self.script = list(script)
        self.deaf = deaf
        self.disposals = 0
        self._gone = asyncio.Event()

    async def start(self, spec) -> None:
        self._gone = asyncio.Event()

    async def prompt(self, text: str) -> None:
        return None

    def subscribe(self):
        script, gone, deaf = list(self.script), self._gone, self.deaf

        async def _stream():
            for delay, event in script:
                await asyncio.sleep(delay)
                if event is None:
                    if deaf:
                        await asyncio.sleep(3600)
                    await gone.wait()
                    return
                yield event
        return _stream()

    async def abort(self) -> None:
        return None

    async def dispose(self) -> None:
        self.disposals += 1
        self._gone.set()

    @property
    def info(self) -> dict:
        return {"engine": "gapped"}


def gapped_hub(tmp_path, script, deaf=False):
    harness = Gapped(script, deaf=deaf)
    hub, s, _fake, chats = _hub(tmp_path, harness_for=lambda _chat=None: harness)
    return hub, s, harness, chats


# --------------------------------------------------------------------------- #
# AC-387 -- the budgets are sized for a local model
# --------------------------------------------------------------------------- #
def test_model_facing_idle_budgets_are_generous_minutes():
    """A local model's silence is its whole generation; 180 s was measured on
    a cloud model (78.6 s max gap) and cut a slow local one off mid-work."""
    assert bh.HARNESS_IDLE_S >= 600, bh.HARNESS_IDLE_S
    assert llm.READ_TIMEOUT_S >= 600, llm.READ_TIMEOUT_S


# --------------------------------------------------------------------------- #
# AC-388 -- reasoning and tool arguments are EVIDENCE
# --------------------------------------------------------------------------- #
def test_pi_reasoning_and_tool_arguments_reach_the_port_as_activity():
    """`thinking_delta` and `toolcall_delta` were dropped, so a reasoning
    model looked exactly like a silent child to the idle deadline."""
    mapper = harness_pi._Mapper()
    mapper.prompt_sent("p1")
    mapper.feed({"type": "response", "id": "p1", "command": "prompt",
                 "success": True})

    def update(kind, delta):
        return mapper.feed({"type": "message_update", "assistantMessageEvent":
                            {"type": kind, "delta": delta, "contentIndex": 0}})

    assert update("thinking_delta", "let me see") == [
        Activity(kind="thinking", chars=10)]
    assert update("toolcall_delta", '{"path": "a"}') == [
        Activity(kind="tool_input", chars=13)]
    assert update("text_delta", "Hi") == [TextDelta(text="Hi")], (
        "the answer's own deltas must be unchanged")


@pytest.mark.asyncio
async def test_a_reasoning_child_is_never_cut_off(tmp_path, monkeypatch):
    """Three budgets of reasoning with no answer text: before AC-388 the idle
    deadline fired on a child that was producing tokens the whole time."""
    monkeypatch.setattr(bh, "HARNESS_IDLE_S", IDLE_S, raising=False)
    step = IDLE_S * 0.6
    script = [(step, Activity(kind="thinking", chars=5)) for _ in range(6)]
    script += [(0.0, TextDelta("Done thinking. ")), (0.0, AgentEnd())]
    brain = build(tmp_path, Gapped(script))

    seen, took = await drive(brain)

    assert took > IDLE_S * 3, f"the fixture did not outrun the budget: {took}"
    assert "Done" in seen and "Say" in seen, (
        f"a child streaming reasoning was cut off: {seen}")


@pytest.mark.asyncio
async def test_activity_is_counted_and_never_spoken_or_recorded(tmp_path):
    """Reasoning is the model's scratch work: it re-arms and it is counted,
    and none of it reaches the room, the page or the record."""
    script = [(0.0, Activity(kind="thinking", chars=7)),
              (0.0, TextDelta("The answer. ")), (0.0, AgentEnd())]
    brain = build(tmp_path, Gapped(script))
    from daemon.ports import TurnContext
    ctx = TurnContext(turn_id=1, mode="secretary", history=())
    intents = []
    async for intent in brain.turn("hello", ctx):
        intents.append(intent)
        if type(intent).__name__ == "Done":
            break
    work = [i for i in intents if isinstance(i, WorkerEvent)
            and i.kind == "thinking"]
    assert work and work[0].chars == 7, intents
    said = [i.text for i in intents if type(i).__name__ == "Say"]
    assert all("7" not in t for t in said), said
    record = brain.host._chats.messages()
    assert record[-1]["content"] == "The answer.", record


# --------------------------------------------------------------------------- #
# AC-389 / AC-390 -- the heartbeat, and what it carries
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_heartbeat_carries_the_evidence_the_turn_saw(
        tmp_path, monkeypatch):
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    script = [(0.0, ToolStart(tool_id="t1", name="read")),
              (0.3, ToolEnd(tool_id="t1", name="read", is_error=False)),
              (0.0, Activity(kind="thinking", chars=40)),
              (0.3, TextDelta("Here it is. ")),
              (0.3, AgentEnd())]
    hub, s, _h, chats = gapped_hub(tmp_path, script)
    chat_id = chats.current()["id"]
    from test_conversation import hub_turn
    await hub_turn(hub, s, turn=950, text="look")

    beats = frames(s, "progress")
    assert beats, "no progress frame reached the wire at all (AC-389)"
    assert all(EVIDENCE <= set(b) for b in beats), beats[0]
    assert all(b["chat"] == chat_id and b["turn"] == 950 for b in beats)
    assert beats[0]["phase"] == "starting", (
        f"the first beat must go out at once, before any evidence: {beats[0]}")
    tool = [b for b in beats if b["phase"] == "tool"]
    assert tool and tool[0]["detail"] == "read", beats
    assert any(b["last_event_ms"] >= 150 for b in tool), (
        f"a quiet tool run must show how long it has been quiet: {tool}")
    think = [b for b in beats if b["phase"] == "thinking"]
    assert think and think[-1]["thought_chars"] == 40, beats
    wrote = [b for b in beats if b["phase"] == "writing"]
    assert wrote and wrote[-1]["chars"] == len("Here it is. "), beats


@pytest.mark.asyncio
async def test_answer_text_held_by_the_splitter_is_still_evidence(
        tmp_path, monkeypatch):
    """MEASURED in the browser, 2026-09-26: a real pi answer streamed a
    bullet list the sentence splitter held back for 1:47, and the heartbeat
    said "Writing, 848 chars - quiet for 1:47" while text arrived the whole
    time. Streamed answer text is evidence whether or not a sentence has
    been released yet."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    script = [(0.0, TextDelta("- `A_S = 1.0` first")),
              (0.2, TextDelta(" and `B_S = 2.0` second")),
              (0.3, TextDelta(" item\n\n")), (0.0, AgentEnd())]
    hub, s, _h, chats = gapped_hub(tmp_path, script)
    from test_conversation import hub_turn
    await hub_turn(hub, s, turn=956, text="list")

    writing = [b for b in frames(s, "progress") if b["phase"] == "writing"]
    assert writing, frames(s, "progress")
    assert max(b["chars"] for b in writing) >= len("- `A_S = 1.0` first"), (
        writing)
    assert min(b["elapsed_ms"] for b in writing) < 150, (
        f"text was arriving from the start and no beat said so until a "
        f"sentence was released: {writing}")
    assert all(b["last_event_ms"] < 450 for b in writing), (
        f"text was arriving and the beat called it quiet: {writing}")


@pytest.mark.asyncio
async def test_the_heartbeat_stops_when_the_turn_is_stopped(
        tmp_path, monkeypatch):
    """D-1's every-path rule for a task: a heartbeat that outlives its turn
    tells the page a dead turn is alive."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    hub, s, _h, chats = gapped_hub(tmp_path, [(0.0, None)])
    chat_id = chats.current()["id"]
    async with running_turn(hub, s, turn=951):
        await until(lambda: len(frames(s, "progress")) >= 3, "three beats")
        await hub._stop(s, {"turn": 951, "chat": chat_id})
        await asyncio.sleep(BEAT_S * 3)
        count = len(frames(s, "progress"))
        await asyncio.sleep(BEAT_S * 6)
        assert len(frames(s, "progress")) == count, (
            "progress frames kept coming after the turn was stopped")
        # The WIRE cannot see a leaked beat -- `send_for_turn` drops frames
        # for a turn the session no longer owns -- so the task itself is
        # asserted: a heartbeat that outlives its turn is a task per turn
        # leaked for the life of the daemon (D-13 found the wire check alone
        # survives removing both of the heartbeat's own stops).
        beating = [task for task in asyncio.all_tasks()
                   if getattr(task.get_coro(), "__qualname__", "")
                   == "Hub._heartbeat"]
        assert not beating, f"a heartbeat outlived its turn: {beating}"


@pytest.mark.asyncio
async def test_the_status_route_carries_the_same_evidence(
        tmp_path, monkeypatch):
    """AC-391. A reloaded or second page gets no frames; the route is its
    only view, so it must say what the frame says (one builder, D-18)."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    script = [(0.0, ToolStart(tool_id="t1", name="grep")), (0.0, None)]
    hub, s, _h, chats = gapped_hub(tmp_path, script)
    chat_id = chats.current()["id"]
    async with _http(hub, monkeypatch) as client:
        async with running_turn(hub, s, turn=952):
            await until(lambda: any(b["phase"] == "tool" for b in
                                    frames(s, "progress")), "a tool beat")
            answer = await client.get(f"/api/chats/{chat_id}/status")
            body = await answer.json()
            await hub._stop(s, {"turn": 952, "chat": chat_id})
    assert body["status"] == "running", body
    assert EVIDENCE <= set(body), body
    assert body["phase"] == "tool" and body["detail"] == "grep", body


# --------------------------------------------------------------------------- #
# AC-394 -- a stop keeps what was shown
# --------------------------------------------------------------------------- #
async def _stop_after_text(tmp_path, deaf):
    script = [(0.0, TextDelta("First sentence. Second sentence. ")),
              (0.0, None)]
    hub, s, _h, chats = gapped_hub(tmp_path, script, deaf=deaf)
    chat_id = chats.current()["id"]
    async with running_turn(hub, s, turn=953):
        await until(lambda: len(frames(s, "reply")) >= 2, "two sentences")
        await hub._stop(s, {"turn": 953, "chat": chat_id})
        await asyncio.sleep(0.2)
    return chats.find(chat_id)["messages"]


@pytest.mark.parametrize("deaf", [False, True],
                         ids=["stream-ends-on-dispose", "reader-cancelled"])
@pytest.mark.asyncio
async def test_a_stopped_answer_keeps_what_was_shown(tmp_path, deaf):
    """Before: the adapter recorded only at the terminal event, so a reload
    showed the question and nothing else. Both ways a stop can end the read
    -- the stream ending, and the reader being cancelled -- must record."""
    messages = await _stop_after_text(tmp_path, deaf)
    last = messages[-1]
    assert last["role"] == "assistant", messages
    assert last["content"] == "First sentence. Second sentence.", last
    assert last.get("stopped") is True, last


@pytest.mark.asyncio
async def test_a_turn_stopped_before_any_answer_is_still_marked(tmp_path):
    hub, s, _h, chats = gapped_hub(tmp_path, [(0.0, None)])
    chat_id = chats.current()["id"]
    async with running_turn(hub, s, turn=954):
        await until(lambda: hub.status_for(chat_id) == "running", "running")
        await asyncio.sleep(0.05)
        await hub._stop(s, {"turn": 954, "chat": chat_id})
        await asyncio.sleep(0.2)
    last = chats.find(chat_id)["messages"][-1]
    assert last["role"] == "assistant" and last.get("stopped") is True, last
    assert last["content"] == "", last


# --------------------------------------------------------------------------- #
# AC-395 -- the rewind seed is what was HEARD
# --------------------------------------------------------------------------- #
def test_the_seed_drops_an_unheard_tail():
    messages = [
        {"role": "user", "content": "explain it"},
        {"role": "assistant", "content": "Heard part. Never heard tail.",
         "unspokenFrom": len("Heard part. ")},
        {"role": "user", "content": "and then"},
        {"role": "assistant", "content": "Speech was off, all of it counts."},
    ]
    seed = dialogue.seed_note(messages)
    assert "Heard part." in seed and "Never heard tail" not in seed, seed
    assert "Speech was off, all of it counts." in seed, seed


# --------------------------------------------------------------------------- #
# AC-396 (AC-248) -- no rewind under a running agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_rewind_is_refused_while_the_agent_runs(tmp_path, monkeypatch):
    hub, s, _h, chats = gapped_hub(tmp_path, [(0.0, None)])
    chat_id = chats.current()["id"]
    async with _http(hub, monkeypatch) as client:
        async with running_turn(hub, s, turn=955):
            await until(lambda: chats.find(chat_id)
                        and chats.find(chat_id)["messages"], "the question")
            before = json.dumps(chats.find(chat_id)["messages"])
            listed = len(chats.listing())
            msg = chats.find(chat_id)["messages"][0]["id"]
            answer = await client.post("/api/session/rewind",
                                       json={"message": msg})
            body = await answer.json()
            assert answer.status == 409, (answer.status, body)
            assert "running" in body["error"], body
            assert json.dumps(chats.find(chat_id)["messages"]) == before
            assert len(chats.listing()) == listed, "a snapshot was made anyway"
            await hub._stop(s, {"turn": 955, "chat": chat_id})


# --------------------------------------------------------------------------- #
# AC-695 -- a finished agent says so: "pi finished in butler, 2:31"
# (docs/lantern-ui.md 8, "Finished row"). The time is the DAEMON's turn
# clock read at the agent's end, never the page's own, so a reloaded tab
# draws the same number from the record.
# --------------------------------------------------------------------------- #
FINISHED = [(0.0, ToolStart(tool_id="t1", name="read")),
            (0.2, ToolEnd(tool_id="t1", name="read", is_error=False)),
            (0.1, TextDelta("Only test_turn.py ran. ")),
            (0.1, AgentEnd())]


def _sent(s):
    return [json.loads(m) for m in s.ws.sent if isinstance(m, str)]


def _stamped(frame):
    return "agent" in frame or "agent_elapsed_ms" in frame


@pytest.mark.asyncio
async def test_the_page_is_told_which_agent_finished_and_after_how_long(
        tmp_path, monkeypatch):
    """The CALLER's wire (D-18): a `progress` frame carries `agent` and
    `agent_elapsed_ms` once the agent has ended, on the SAME clock as every
    beat's `elapsed_ms`, and BEFORE `speak_end` -- the board draws the row
    while the answer is still being spoken, and `speak_end` waits for the
    whole playout."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    hub, s, _h, chats = gapped_hub(tmp_path, FINISHED)
    chat_id = chats.current()["id"]
    from test_conversation import hub_turn
    await hub_turn(hub, s, turn=960, text="run the tests")

    sent = _sent(s)
    done = [i for i, f in enumerate(sent)
            if f.get("type") == "progress" and _stamped(f)]
    assert done, f"no frame said the agent finished: {sent}"
    first = sent[done[0]]
    assert first["chat"] == chat_id and first["turn"] == 960, first
    assert first["agent"] == "pi", first
    assert 350 <= first["agent_elapsed_ms"] <= first["elapsed_ms"], (
        f"the agent ran ~400 ms on the turn clock: {first}")
    assert {sent[i]["agent_elapsed_ms"] for i in done} == {
        first["agent_elapsed_ms"]}, "the finished time must not keep running"
    before = [f for f in sent[:done[0]] if f.get("type") == "progress"]
    assert before and not any(_stamped(f) for f in before), before
    ends = [i for i, f in enumerate(sent) if f.get("type") == "speak_end"]
    assert ends and done[0] < ends[0], (
        f"the finished row must be known before speak_end: {sent}")


@pytest.mark.asyncio
async def test_a_reloaded_page_reads_the_same_finished_row_from_the_record(
        tmp_path, monkeypatch):
    """What `GET /api/chats/{id}` serves a reloaded tab: the turn's answer
    carries `agent` and `agentElapsedMs`, equal to what the live frame said.
    The owner's own message carries neither."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    hub, s, _h, chats = gapped_hub(tmp_path, FINISHED)
    chat_id = chats.current()["id"]
    from test_conversation import hub_turn
    await hub_turn(hub, s, turn=961, text="run the tests")
    live = next(f for f in _sent(s)
                if f.get("type") == "progress" and _stamped(f))
    async with _http(hub, monkeypatch) as client:
        answer = await client.get(f"/api/chats/{chat_id}")
        body = await answer.json()
    assert answer.status == 200, body
    question, reply = body["messages"][-2:]
    assert reply["role"] == "assistant", body["messages"]
    assert reply["agent"] == live["agent"], reply
    assert reply["agentElapsedMs"] == live["agent_elapsed_ms"], reply
    assert "agent" not in question and "agentElapsedMs" not in question
    # ON DISK, not only in the held dict the route answered from: a
    # restarted daemon reads the file (D-13 found the route alone survives
    # a stamp that is never written).
    from daemon.chats import ChatStore
    stored = ChatStore(tmp_path / "chats").find(chat_id)["messages"][-1]
    assert stored["agentElapsedMs"] == live["agent_elapsed_ms"], stored


@pytest.mark.asyncio
async def test_a_stopped_agent_never_claims_it_finished(tmp_path, monkeypatch):
    """A stop is not a finish: the page draws "stopped" (AC-394), and a row
    saying the agent finished would be a lie on the wire and in the record."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    hub, s, _h, chats = gapped_hub(
        tmp_path, [(0.0, TextDelta("First sentence. ")), (0.0, None)])
    chat_id = chats.current()["id"]
    async with running_turn(hub, s, turn=962):
        await until(lambda: frames(s, "reply"), "a sentence")
        await hub._stop(s, {"turn": 962, "chat": chat_id})
        await asyncio.sleep(0.2)
    last = chats.find(chat_id)["messages"][-1]
    assert last.get("stopped") is True, last
    assert "agent" not in last and "agentElapsedMs" not in last, last
    assert not any(_stamped(f) for f in frames(s, "progress"))


class _WontStart(Gapped):
    async def start(self, spec) -> None:
        raise RuntimeError("no such model")


@pytest.mark.asyncio
async def test_a_turn_no_agent_ran_draws_no_finished_row(
        tmp_path, monkeypatch):
    """A spawn that failed is answered with a spoken apology and a `Done`,
    exactly like a finished agent -- but no agent ran, so nothing may say
    one finished. The witness for the "an agent reported in" gate."""
    monkeypatch.setattr(hub_mod, "PROGRESS_EVERY_S", BEAT_S, raising=False)
    harness = _WontStart([])
    hub, s, _fake, chats = _hub(tmp_path, harness_for=lambda _chat=None: harness)
    chat_id = chats.current()["id"]
    from test_conversation import hub_turn
    await hub_turn(hub, s, turn=963, text="run the tests")
    last = chats.find(chat_id)["messages"][-1]
    assert last["role"] == "assistant" and last["content"], last
    assert "agent" not in last and "agentElapsedMs" not in last, last
    assert not any(_stamped(f) for f in frames(s, "progress"))


def test_the_record_stamps_only_this_turns_finished_answer(tmp_path):
    """`ChatStore.mark_finished` refuses, loudly and with None, an answer
    older than this turn and a STOPPED one; it never guesses."""
    from daemon.chats import ChatStore
    chats = ChatStore(tmp_path / "chats")
    cid = chats.current()["id"]
    chats.append(cid, "user", "first")
    chats.append(cid, "assistant", "an old answer")
    old = json.dumps(chats.find(cid)["messages"])
    later = int(time.time() * 1000) + 60_000
    assert chats.mark_finished(cid, 1234, since_ms=later) is None
    assert json.dumps(chats.find(cid)["messages"]) == old
    chats.append(cid, "user", "second")
    chats.append(cid, "assistant", "cut off", stopped=True)
    assert chats.mark_finished(cid, 1234, since_ms=0) is None
    assert "agent" not in chats.find(cid)["messages"][-1]


# --------------------------------------------------------------------------- #
# AC-392 / AC-393 / AC-394 -- the page (structure; behaviour was driven in a
# browser, docs/turn-feedback.md and TMP/feedback-out/)
# --------------------------------------------------------------------------- #
APP = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
    encoding="utf-8")


def _const_ms(name):
    import re
    found = re.search(r"const " + name + r" = (\d+);", APP)
    assert found, f"web/app.js no longer declares {name}"
    return int(found.group(1))


def test_the_stall_window_is_several_beats_long():
    """One late beat must never read as death: the page may say "may be
    stuck" only after more than two of the daemon's own beats went missing."""
    beat_ms = hub_mod.PROGRESS_EVERY_S * 1000
    assert _const_ms("STALL_MS") > 2 * beat_ms, (
        _const_ms("STALL_MS"), beat_ms)


def test_the_page_reads_the_heartbeat_and_says_stuck_without_acting():
    assert 'case "progress":' in APP, "the page never reads the heartbeat"
    stuck = APP.index("it may be stuck")
    window = APP[stuck - 600:stuck + 200]
    assert "stopTurn" not in window and "goListen" not in window, (
        "the stuck line must SAY, never act (AC-393)")


def test_a_failed_watch_poll_is_silence_not_an_ending():
    """A network blip used to read as "the turn ended" and re-read the
    record under a running agent -- silence read as death, page side."""
    body = APP[APP.index("function watchChat"):]
    body = body[:body.index("\n}\n")]
    assert "if (now === null) return;" in body, body


def test_a_stop_is_drawn_live_and_after_a_reload():
    render = APP[APP.index("function renderMessages"):]
    render = render[:render.index("\n}\n")]
    stop = APP[APP.index("function stopTurn"):]
    stop = stop[:stop.index("\n}\n")]
    assert "markStopped" in render and "m.stopped" in render, render
    assert "markStopped(openItem)" in stop, stop
