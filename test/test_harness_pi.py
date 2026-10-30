"""What only the pi harness can be asked. docs/harness-mode.md step 7.2.

`test/conformance/test_harness_conformance.py` holds what every Harness owes
the port. This file holds the half that is pi's alone: its JSONL framing, its
event vocabulary, its unprompted dialogs, and the failures a scripted fake
can never produce -- a provider that breaks mid-turn, a child that dies with
a prompt in flight, and pi's own automatic retry, which emits a WHOLE
`agent_end` per attempt and would otherwise announce one answer three times
(AC-48).

The bulk is OFFLINE and drives a scripted transport, so the suite stays fast
and deterministic. Every fixture below is a RECORDED line from a real
`pi --mode rpc` child on this machine on 2026-08-17: the bulky `message` and
`partial` mirrors are trimmed so the fixture is readable, and every field the
mapper actually reads is verbatim. Fixtures were re-recorded rather than
transcribed from the spec, which is what docs/harness-mode.md 7.2 asks for
when the live stream and a guess disagree.

The one live test is marked `slow` and skips -- never fails -- when pi is not
installed, in the shape `conftest.real_stt_daemon` uses for the recognizer.
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import PI_MODEL, PI_PROVIDER  # noqa: E402
from daemon import ports  # noqa: E402
from daemon.adapters import harness_pi  # noqa: E402
from daemon.ports import (AgentEnd, Failed, HarnessSpec,  # noqa: E402
                          TERMINAL_EVENTS, TextDelta, ToolEnd, ToolStart,
                          TurnEnd)

# Nothing in this file waits forever (test/AGENTS.md): the defect this port is
# most likely to have is a terminal event that never arrives, and a suite that
# HANGS on it reports nothing at all.
EVENT_TIMEOUT = 5.0

# The live child, READ FROM conftest RATHER THAN RESTATED. This file used to
# carry its own copy of the same two environment reads, and conftest's own
# comment already said why that is wrong -- "two copies of a constant drift
# and the pi conformance row and the pi live test must talk to the same
# child". They drifted exactly as predicted: conftest moved off gpt-5.4-mini
# on 2026-09-17 when the live Codex API began answering it with 400 "not
# supported when using Codex with a ChatGPT account", and this copy did not,
# so it was still aimed at a model the account may not call.
LIVE_PROVIDER = PI_PROVIDER
LIVE_MODEL = PI_MODEL


# --------------------------------------------------------------------------- #
# Recorded lines
# --------------------------------------------------------------------------- #
# One prompt, three turns, four tool calls, TWO OF THEM IN PARALLEL and three
# of them failing -- recorded, not designed. The parallel pair is the exact
# shape the predecessor got wrong: it kept one current-step slot, so 8 of 19
# tool starts never closed (plan.md 4.4c).
STARTUP_LINES = [
    '{"type":"extension_ui_request","id":"63c6584b","method":"setStatus",'
    '"statusKey":"mcp","statusText":""}',
    '{"type":"extension_ui_request","id":"85701f2e","method":"setStatus",'
    '"statusKey":"plan-mode"}',
    '{"type":"extension_ui_request","id":"bc81d1ed","method":"setWidget",'
    '"widgetKey":"plan-todos"}',
]

MEASURED_ANSWER = [
    '{"id":"p1","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_start",'
    '"contentIndex":0}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":0,"delta":"I\\u2019ll inspect"}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":0,"delta":" the directory."}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_end",'
    '"contentIndex":0,"content":"I\\u2019ll inspect the directory."}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"toolcall_end",'
    '"contentIndex":1,"toolCall":{"type":"toolCall","id":"call_XZeo",'
    '"name":"read","arguments":{"path":"missing-on-purpose.txt"}}}}',
    '{"type":"tool_execution_start","toolCallId":"call_XZeo",'
    '"toolName":"read","args":{"path":"missing-on-purpose.txt"}}',
    '{"type":"tool_execution_end","toolCallId":"call_XZeo","toolName":"read",'
    '"isError":true}',
    '{"type":"turn_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"I\\u2019ll inspect the directory."},{"type":"toolCall",'
    '"id":"call_XZeo","name":"read","arguments":{}}],"stopReason":"toolUse"},'
    '"toolResults":[{"role":"toolResult","toolCallId":"call_XZeo",'
    '"toolName":"read"}]}',
    '{"type":"turn_start"}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":0,"delta":"I\\u2019ll list the files."}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"toolcall_end",'
    '"contentIndex":1,"toolCall":{"type":"toolCall","id":"call_DR9Z",'
    '"name":"read","arguments":{}}}}',
    '{"type":"message_update","assistantMessageEvent":{"type":"toolcall_end",'
    '"contentIndex":2,"toolCall":{"type":"toolCall","id":"call_TmFf",'
    '"name":"grep","arguments":{}}}}',
    '{"type":"tool_execution_start","toolCallId":"call_DR9Z",'
    '"toolName":"read","args":{}}',
    '{"type":"tool_execution_start","toolCallId":"call_TmFf",'
    '"toolName":"grep","args":{}}',
    '{"type":"tool_execution_end","toolCallId":"call_DR9Z","toolName":"read",'
    '"isError":true}',
    '{"type":"tool_execution_end","toolCallId":"call_TmFf","toolName":"grep",'
    '"isError":false}',
    '{"type":"turn_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"I\\u2019ll list the files."}],"stopReason":"toolUse"},'
    '"toolResults":[]}',
    '{"type":"turn_start"}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":0,"delta":"I can\\u2019t enumerate this directory."}}',
    '{"type":"turn_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"I can\\u2019t enumerate this directory."}],'
    '"stopReason":"stop"},"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
]

# What arrives AFTER the terminal event. Reading stops at the first terminal,
# so a stray one behind it is invisible unless something looks past the ending
# (test/AGENTS.md, and the 7.1 reviewer's finding).
TAIL_AFTER_THE_ANSWER = [
    '{"id":"p2","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
]

# The measured provider that does not stream: message_start, message_end,
# turn_end carrying the WHOLE answer, and ZERO text deltas
# (docs/harness-mode.md section 1, AC-47). Recorded from the claudebridge-local
# child, whose message text is restored from that measurement because the
# bridge was not listening when these fixtures were re-recorded.
NO_STREAM_ANSWER = [
    '{"id":"p1","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"message_start","message":{"role":"assistant","content":[]}}',
    '{"type":"message_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"It holds three endpointing policies."}],'
    '"stopReason":"stop"}}',
    '{"type":"turn_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"It holds three endpointing policies."}],'
    '"stopReason":"stop"},"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
]

# A provider that broke mid-turn: recorded verbatim from a child whose bridge
# was not listening. The failure is in the MESSAGE, not in any error event --
# pi has no top-level error event at all, which is precisely how an adapter
# ends up with an unreachable failure branch (plan.md 4.4c).
BROKEN_PROVIDER = [
    '{"id":"p1","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"turn_end","message":{"role":"assistant","content":[],'
    '"stopReason":"error","errorMessage":"Connection error."},'
    '"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
]

# pi retries a transient failure by itself, and every attempt emits its own
# agent_end [MEASURED 2026-08-17]. Three attempts would be three terminal
# events for ONE prompt, which is the announce-it-five-times failure AC-48 is
# named after. The adapter turns auto-retry OFF at start and ALSO refuses the
# extra terminals, because a child that ignored the command must not be able
# to make Butler speak twice.
RETRYING_CHILD = [
    '{"id":"p1","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"turn_end","message":{"role":"assistant","content":[],'
    '"stopReason":"error","errorMessage":"Connection error."},'
    '"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
    '{"type":"auto_retry_start","attempt":1,"maxAttempts":3,"delayMs":2000,'
    '"errorMessage":"Connection error."}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"turn_end","message":{"role":"assistant","content":[],'
    '"stopReason":"error","errorMessage":"Connection error."},'
    '"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
]

# An abort, recorded: the turn ends with stopReason "aborted" and pi still
# emits its agent_end. Barge-in is routine and NOT a failure (D-4), so this
# ends the prompt with AgentEnd and never with Failed.
ABORTED_TURN = [
    '{"id":"p1","type":"response","command":"prompt","success":true}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta",'
    '"contentIndex":0,"delta":"The history of the typewriter"}}',
    '{"type":"turn_end","message":{"role":"assistant","content":[{"type":'
    '"text","text":"The history of the typewriter"}],'
    '"stopReason":"aborted","errorMessage":"Request was aborted."},'
    '"toolResults":[]}',
    '{"type":"agent_end","messages":[]}',
    '{"type":"response","command":"abort","success":true}',
]


def _map(lines, *, prompt=True, prompt_id="p1"):
    """Every event a mapper produces for `lines`, prompt in flight or not.

    The id is the one every recorded fixture above carries, because a prompt
    is identified by it: a terminal event is accepted only after pi has
    ACCEPTED that id, so a mapper told about a different prompt would refuse
    the whole recording.
    """
    mapper = harness_pi._Mapper()
    if prompt:
        mapper.prompt_sent(prompt_id)
    events = []
    for line in lines:
        events.extend(mapper.feed(json.loads(line)))
    return events


def _terminals(events):
    return [e for e in events if isinstance(e, TERMINAL_EVENTS)]


# --------------------------------------------------------------------------- #
# The mapper
# --------------------------------------------------------------------------- #
def test_the_measured_stream_maps_to_the_normalized_events():
    """The recorded three-turn answer, as the six-event vocabulary sees it.

    The tail is fed as well: reading normally stops at the first terminal
    event, so an adapter that emitted a second one would pass the obvious
    version of this test (D-13, test/AGENTS.md).
    """
    events = _map(STARTUP_LINES + MEASURED_ANSWER + TAIL_AFTER_THE_ANSWER)

    assert [e.text for e in events if isinstance(e, TextDelta)] == [
        "I\u2019ll inspect", " the directory.", "\n\n", "I\u2019ll list the files.",
        "\n\n", "I can\u2019t enumerate this directory.", "\n\n"]
    assert [e.text for e in events if isinstance(e, TurnEnd)] == [
        "I\u2019ll inspect the directory.", "I\u2019ll list the files.",
        "I can\u2019t enumerate this directory."]
    terminals = _terminals(events)
    assert len(terminals) == 1, (
        f"one prompt produced {len(terminals)} terminal events: {terminals}")
    assert isinstance(terminals[0], AgentEnd)
    assert events[-1] is terminals[0], (
        "something arrived after the prompt was declared over")


def test_a_no_stream_provider_recovers_text_from_turn_end():
    """AC-47's mapping half.

    The measured cloud provider emitted no `text_delta` at all and put the
    whole answer in `turn_end`. An adapter that mapped only deltas would have
    spoken NOTHING there, and silence reads as being ignored.
    """
    events = _map(NO_STREAM_ANSWER)

    assert [e for e in events if isinstance(e, TextDelta)] == [], (
        "this recording has no text deltas in it; deltas here would be "
        "something the adapter invented")
    turn_ends = [e for e in events if isinstance(e, TurnEnd)]
    assert [e.text for e in turn_ends] == ["It holds three endpointing "
                                           "policies."]
    assert len(_terminals(events)) == 1


def test_a_streamed_answer_is_not_also_delivered_as_turn_end_deltas():
    """The other half of AC-47, which is the one that gets it said TWICE.

    Recovering the text from `turn_end` is the fix for a provider that does
    not stream; synthesizing deltas from it would make every streaming
    provider say its answer once as deltas and again as a turn.
    """
    events = _map(MEASURED_ANSWER)
    deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert deltas.count("I\u2019ll inspect the directory.") == 1
    assert [e for e in events if isinstance(e, TurnEnd)][0].text == (
        "I\u2019ll inspect the directory.")


def test_text_on_either_side_of_a_tool_call_keeps_a_paragraph_break():
    """Separate text blocks stay separate once they are one delta stream.

    pi streams each assistant MESSAGE (one per agent turn, so one per tool
    round) and each text BLOCK inside it from its own first character, with
    no separator: the recording above carries "...the directory." and then
    "I'll list the files." as adjacent deltas. A caller that concatenates
    deltas -- `brain_harness` does, and must -- glued them into
    "directory.I'll", and the glue reached the room, the page and the chat
    record (docs/e2e-journeys.md). The mapper is where blocks become one
    stream, so it is where the break is put.
    """
    events = _map(MEASURED_ANSWER)
    joined = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert joined == ("I’ll inspect the directory.\n\n"
                      "I’ll list the files.\n\n"
                      "I can’t enumerate this directory.\n\n"), joined


def test_the_sentence_before_a_tool_call_is_released_before_the_tool_runs():
    """The break ENDS a block, at `text_end`, and that is latency.

    MEASURED on a live child 2026-09-26: pi sends `text_end` as soon as a
    block is done, then runs the tool, then thinks again -- the next block's
    first delta came 3-8 s later. `speech.drain_sentences` releases a
    sentence only once whitespace follows its full stop, so a break put at
    the START of the next block would hold "I'll inspect the directory."
    until after the tool had run.
    """
    from daemon import speech

    events = _map(MEASURED_ANSWER)
    first_tool = next(i for i, e in enumerate(events)
                      if isinstance(e, ToolStart))
    before = "".join(e.text for e in events[:first_tool]
                     if isinstance(e, TextDelta))
    sentences, _ = speech.drain_sentences(before)
    assert sentences == ["I’ll inspect the directory."], (
        f"the text streamed before the first tool call was {before!r}, and "
        f"the room could hear none of it until the tool had run")


def test_two_text_blocks_in_one_message_keep_a_paragraph_break():
    """The same rule inside ONE message: text, a tool call, more text.

    Shaped on the recorded `contentIndex` events above. A thinking block
    first is the other half: it is never mapped, so the first TEXT block
    must not open with a separator it has nothing to separate from.
    """
    lines = [
        '{"id":"p1","type":"response","command":"prompt","success":true}',
        '{"type":"turn_start"}',
        '{"type":"message_update","assistantMessageEvent":{"type":'
        '"thinking_delta","contentIndex":0,"delta":"hmm"}}',
        '{"type":"message_update","assistantMessageEvent":{"type":'
        '"text_delta","contentIndex":1,"delta":"Checking"}}',
        '{"type":"message_update","assistantMessageEvent":{"type":'
        '"text_delta","contentIndex":1,"delta":" now"}}',
        '{"type":"message_update","assistantMessageEvent":{"type":'
        '"toolcall_end","contentIndex":2,"toolCall":{"type":"toolCall",'
        '"id":"c1","name":"read","arguments":{}}}}',
        '{"type":"message_update","assistantMessageEvent":{"type":'
        '"text_delta","contentIndex":3,"delta":"Done."}}',
        '{"type":"agent_end","messages":[]}',
    ]
    joined = "".join(e.text for e in _map(lines) if isinstance(e, TextDelta))
    assert joined == "Checking now\n\nDone.", repr(joined)


def test_a_turn_end_with_two_text_blocks_keeps_a_paragraph_break():
    """The non-streaming half: `turn_end` is the WHOLE answer there (AC-47),
    so its blocks must be joined the way the deltas are."""
    message = {"content": [{"type": "text", "text": "Checking now"},
                           {"type": "toolCall", "id": "c1"},
                           {"type": "text", "text": "Done."}]}
    assert harness_pi._text_of(message) == "Checking now\n\nDone."


def test_tool_events_keep_pis_toolCallId_and_isError():
    """Both halves are transposed predecessor defects (plan.md 4.4c).

    The recording contains two tool calls running in PARALLEL -- two starts
    before either end -- which is the case a single current-step slot gets
    wrong, and three of the four calls FAIL, which is the case a hardcoded
    outcome gets wrong.
    """
    events = _map(MEASURED_ANSWER)
    starts = [e for e in events if isinstance(e, ToolStart)]
    ends = [e for e in events if isinstance(e, ToolEnd)]

    assert [(e.tool_id, e.name) for e in starts] == [
        ("call_XZeo", "read"), ("call_DR9Z", "read"), ("call_TmFf", "grep")]
    assert [(e.tool_id, e.name, e.is_error) for e in ends] == [
        ("call_XZeo", "read", True), ("call_DR9Z", "read", True),
        ("call_TmFf", "grep", False)]


def test_a_broken_provider_ends_the_prompt_as_failed():
    """AC-47, AC-48. pi has no error EVENT: the failure is a stop reason.

    An adapter watching for an `error` event would see this prompt end with
    `agent_end` and announce an answer it never got -- which is the
    predecessor's defect wearing pi's vocabulary.
    """
    events = _map(BROKEN_PROVIDER)
    terminals = _terminals(events)
    assert len(terminals) == 1
    assert isinstance(terminals[0], Failed)
    assert "Connection error." in terminals[0].message


def test_an_aborted_turn_is_not_a_failure():
    """Barge-in is routine (D-4). `stopReason: "aborted"` is Butler's own
    doing, and reporting it as `Failed` would make Butler apologise every
    time it was interrupted."""
    events = _map(ABORTED_TURN)
    terminals = _terminals(events)
    assert len(terminals) == 1
    assert isinstance(terminals[0], AgentEnd), (
        f"an aborted prompt ended with {terminals[0]!r}")


def test_pis_own_retry_cannot_end_one_prompt_twice():
    """AC-48, measured: pi emits a whole `agent_end` per retry attempt.

    Three attempts is three terminal events for one prompt, and the caller
    announces once per terminal event. The adapter disables auto-retry at
    start; this proves the guard holds even if the child ignores that.
    """
    events = _map(RETRYING_CHILD)
    terminals = _terminals(events)
    assert len(terminals) == 1, (
        f"pi retried and the adapter passed on {len(terminals)} terminal "
        f"events: {terminals}")
    assert isinstance(terminals[0], Failed)


def test_no_event_at_all_escapes_a_prompt_that_has_ended():
    """Nothing reaches the caller once the prompt is over -- not just the
    terminal event.

    The child talks at startup, after an abort settles, and once more per
    retry attempt it makes on its own. Dropping only the extra TERMINALS was
    the 7.2 shape and it is not enough: 7.4 sentence-splits `text_delta` and
    SPEAKS it, so a retry's text and its `turn_end` would have Butler
    delivering a second answer to a turn it had already finished announcing
    (AC-48).
    """
    events = _map(MEASURED_ANSWER, prompt=False)
    assert events == [], (
        f"the child spoke for a prompt nobody sent: {events}")
    # The same lines WITH a prompt in flight, so this test cannot be passed
    # by an adapter that drops everything always (D-13).
    assert _map(MEASURED_ANSWER), (
        "the same recording produced nothing with a prompt in flight either, "
        "so the assertion above proves nothing")


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #
def test_framing_splits_on_lf_only():
    """pi's own docs warn about this and name the exact hazard.

    U+2028 and U+2029 are legal inside a JSON string, and a reader that
    treats them as newlines (Node's `readline` does) chops one event into two
    unparseable fragments. The separator is built from its escape here so
    this file stays ASCII (project rule).
    """
    framer = harness_pi._Framer()
    line = json.dumps({"type": "message_update", "delta": "a\u2028b\u2029c"},
                      ensure_ascii=False)
    assert "\u2028" in line, "the fixture lost its separator before the test"

    events = framer.feed(line.encode("utf-8") + b"\n")

    assert len(events) == 1, f"one JSONL record arrived as {len(events)}"
    assert json.loads(events[0])["delta"] == "a\u2028b\u2029c"


def test_framing_reassembles_a_record_split_across_reads():
    """A pipe read boundary lands anywhere. Recorded lines run to tens of
    kilobytes -- a whole tool result -- so this is the ordinary case, not the
    edge one."""
    framer = harness_pi._Framer()
    raw = (MEASURED_ANSWER[0] + "\n" + MEASURED_ANSWER[1] + "\n").encode()
    first = framer.feed(raw[:20])
    rest = framer.feed(raw[20:])
    assert first == []
    assert rest == [MEASURED_ANSWER[0], MEASURED_ANSWER[1]]


def test_framing_strips_the_carriage_return_pi_documents():
    """rpc.md: accept optional `\\r\\n` by stripping a trailing `\\r`."""
    framer = harness_pi._Framer()
    assert framer.feed(b'{"type":"agent_end"}\r\n') == ['{"type":"agent_end"}']


# --------------------------------------------------------------------------- #
# The dialog. AC-49 REVERSED, 2026-09-12 (AC-234, `docs/parallel-agents.md` D2)
#
# This section is INVERTED rather than deleted, so the reversal is legible.
# It used to prove that a dialog was ANSWERED BY THE ADAPTER with
# `cancelled: true` -- "the extension is told the user dismissed the dialog,
# which is exactly what happened". The owner ruled on 2026-09-11 that a coding
# agent may ask a human and wait, so the same function now answers None for a
# dialog a human can be shown, and the two stated exceptions are what is left.
# The behaviour end to end is `test_harness_question.py`.
# --------------------------------------------------------------------------- #
def test_a_dialog_raised_DURING_a_prompt_is_left_for_a_human():
    """AC-234. What this function used to answer, it now declines to.

    `prompt_pending` is the gate and it is an ARGUMENT rather than something
    read here: the decision belongs to the mapper, which knows whether a
    prompt pi accepted is still in flight, and a second copy of that fact
    would be the one that goes stale.
    """
    assert harness_pi.cancellation_for(
        {"type": "extension_ui_request", "id": "uuid-2", "method": "confirm",
         "title": "Clear session?"}, True) is None, (
        "a confirm raised during a prompt is still being answered by the "
        "adapter; that is AC-49 and it is reversed (AC-234)")


def test_the_two_exceptions_are_editor_and_an_unprompted_dialog():
    """The exceptions are STATED, with reasons, and there are exactly two.

    `editor` asks the client to open a text editor on a buffer: no Butler
    surface can render it and no voice can dictate it, and it is the one
    method with no agent-side timeout path at all. An UNPROMPTED dialog --
    three arrive before the first response even in the simplest
    configuration [MEASURED] -- has no turn to attach to at all.
    """
    editor = harness_pi.cancellation_for(
        {"type": "extension_ui_request", "id": "uuid-3", "method": "editor"},
        True)
    assert editor == {"type": "extension_ui_response", "id": "uuid-3",
                      "cancelled": True}
    unprompted = harness_pi.cancellation_for(
        {"type": "extension_ui_request", "id": "uuid-4", "method": "confirm"},
        False)
    assert unprompted == {"type": "extension_ui_response", "id": "uuid-4",
                          "cancelled": True}


@pytest.mark.parametrize("line", STARTUP_LINES)
@pytest.mark.parametrize("pending", [True, False])
def test_a_fire_and_forget_request_is_not_answered(line, pending):
    """The three recorded startup requests are `setStatus` and `setWidget`.

    Those expect no response at all, and answering them would put lines on
    the child's stdin that its protocol never asked for. Asked BOTH ways
    round the prompt gate, because the reversal added a second branch and a
    non-dialog method must fall out of the first one whichever way it is set.
    """
    assert harness_pi.cancellation_for(json.loads(line), pending) is None


# --------------------------------------------------------------------------- #
# The transport, against a scripted child
# --------------------------------------------------------------------------- #
class _ScriptedPi:
    """A pi child that never runs: it answers commands and emits recorded
    lines on demand.

    Not a null object (adapters/AGENTS.md): it reproduces the two behaviours
    the transport actually depends on -- every command gets a response
    carrying the id it was sent with, and stdin EOF ends the child -- so the
    adapter's start, prompt and dispose paths are exercised for real.
    """

    def __init__(self, respond=True):
        self.respond = respond
        self.written: list[dict] = []
        self.stdin = self
        self.stdout = self
        self.stderr = _ScriptedStderr()
        self.returncode = None
        self.pid = -1
        self._out: asyncio.Queue = asyncio.Queue()
        self._buf = b""
        self._closed = False

    # -- what the adapter writes ------------------------------------------- #
    def write(self, raw: bytes) -> None:
        for line in raw.decode("utf-8").split("\n"):
            if not line.strip():
                continue
            command = json.loads(line)
            self.written.append(command)
            if self.respond and command.get("type") != "extension_ui_response":
                self.emit(json.dumps(
                    {"id": command.get("id"), "type": "response",
                     "command": command["type"], "success": True}))

    async def drain(self) -> None:
        return None

    def last_prompt_id(self) -> str:
        """The id the adapter minted for its most recent prompt.

        Since the 7.2 review a terminal event is refused until pi has
        ACCEPTED that id, so a test that made up an id would drive a prompt
        the adapter has never heard of and prove nothing about the real one.
        """
        prompts = [c for c in self.written if c.get("type") == "prompt"]
        assert prompts, "no prompt has been written to this child"
        return str(prompts[-1]["id"])

    def close(self) -> None:
        self._closed = True
        self.returncode = 0
        self._out.put_nowait(b"")

    # -- what the adapter reads -------------------------------------------- #
    def emit(self, line: str) -> None:
        self._out.put_nowait((line + "\n").encode("utf-8"))

    def die(self) -> None:
        """EOF with no exit of our own: the child was killed under us."""
        self.returncode = 3
        self._out.put_nowait(b"")

    def explode(self, error: Exception) -> None:
        """The PIPE fails, not a record: the next read raises."""
        self.returncode = 7
        self._out.put_nowait(error)

    async def read(self, _n: int) -> bytes:
        item = await self._out.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _ScriptedStderr:
    async def readline(self) -> bytes:
        await asyncio.sleep(3600)     # never speaks; the test's deadline wins
        return b""


@pytest_asyncio.fixture
async def scripted(monkeypatch, tmp_path):
    """A started adapter over a scripted child, disposed however a test ends."""
    child = _ScriptedPi()

    async def _spawn(argv, cwd, env=None):
        _spawn.argv, _spawn.cwd = argv, cwd
        return child

    monkeypatch.setattr(harness_pi, "_spawn", _spawn)
    harness = ports.build(ports.HARNESSES, "pi")
    await harness.start(HarnessSpec(model=None, cwd=str(tmp_path),
                                    session_file=str(tmp_path / "s.jsonl")))
    try:
        yield harness, child, _spawn
    finally:
        await harness.dispose()


async def _next(stream, waiting_for):
    try:
        return await asyncio.wait_for(anext(stream), EVENT_TIMEOUT)
    except asyncio.TimeoutError:
        pytest.fail(f"waited {EVENT_TIMEOUT:.0f}s for {waiting_for}")


async def _drain_to_terminal(stream, seen):
    while True:
        event = await _next(stream, "the terminal event that ends a prompt")
        seen.append(event)
        if isinstance(event, TERMINAL_EVENTS):
            return seen


@pytest.mark.asyncio
async def test_the_spawn_line_restricts_no_tools_at_all(scripted):
    """AC-29 RETIRED 2026-08-18: the child holds pi's own default set.

    The tool restriction used to be the ONE place a permission could be got
    wrong, and it is now deliberately absent: neither `--tools` nor
    `--no-tools` is built, so pi spawns with everything including `bash`.
    Asserting the absence rather than deleting the test, because a `--tools`
    argument creeping back in would silently narrow the agent the owner asked
    to have at full capability, and nothing else in the suite would see it.
    """
    _harness, _child, spawn = scripted
    assert spawn.argv[:len(harness_pi.PI) + 2] == [*harness_pi.PI, "--mode",
                                                   "rpc"], (
        "pi is pi.cmd on Windows and does not resolve through PATHEXT "
        "(plan.md 4.5)")
    assert "--tools" not in spawn.argv, spawn.argv
    assert "--no-tools" not in spawn.argv, spawn.argv


@pytest.mark.asyncio
async def test_read_only_tools_become_a_tools_allow_list(tmp_path):
    """The `harness_tools` read-only posture, on the argv (AC-29 note).

    A spec carrying a read allow-list emits `--tools read,grep,find,ls` -- pi's
    own documented read-only mode (`pi --help`). This is the branch 653a44d
    deleted, re-added and driven by a plain SETTING rather than the retired
    spoken grant. Emitted as ONE comma-joined value, not one flag per tool.
    """
    harness = ports.build(ports.HARNESSES, "pi")
    spec = HarnessSpec(model=None, cwd=str(tmp_path),
                       session_file=str(tmp_path / "s.jsonl"),
                       tools=("read", "grep", "find", "ls"))
    argv = harness._argv(spec)
    assert "--tools" in argv, argv
    assert argv[argv.index("--tools") + 1] == "read,grep,find,ls", argv


@pytest.mark.asyncio
async def test_full_tools_emit_no_tools_flag_at_all(tmp_path):
    """An empty allow-list means NO `--tools`, not an empty one (AC-29).

    `--tools ""` and no flag are DIFFERENT instructions: the first is an empty
    tool set pi would read as a restriction, the second leaves pi at its own
    default (read, bash, edit, write). full must leave pi unrestricted, so the
    argv must carry no `--tools` and no empty string standing in for one.
    """
    harness = ports.build(ports.HARNESSES, "pi")
    spec = HarnessSpec(model=None, cwd=str(tmp_path),
                       session_file=str(tmp_path / "s.jsonl"), tools=())
    argv = harness._argv(spec)
    assert "--tools" not in argv, argv
    assert "" not in argv, argv


@pytest.mark.asyncio
async def test_start_turns_off_the_retry_that_would_end_a_prompt_twice(
        scripted):
    """Measured: each retry attempt emits its own `agent_end` (AC-48).

    Butler owns the retry policy -- 4.4c says a failed turn is replayed once,
    by the daemon -- and a child retrying underneath it announces the same
    turn up to three times.
    """
    _harness, child, _spawn = scripted
    commands = [c["type"] for c in child.written]
    assert "set_auto_retry" in commands, (
        f"start() sent {commands}; nothing turned pi's own retry off")
    sent = [c for c in child.written if c["type"] == "set_auto_retry"][0]
    assert sent["enabled"] is False


@pytest.mark.asyncio
async def test_an_unimplemented_dialog_is_answered_on_the_childs_stdin(
        scripted):
    """The UNPROMPTED exception, on the transport rather than on the pure
    function (AC-234, AC-49 reversed).

    The fixture has no prompt in flight, which is exactly the case that still
    auto-cancels: pi raises dialogs at startup and they have no turn to be
    drawn against. The mapping being right is worth nothing if the reply
    never reaches the child, which is the caller-side half of D-18.
    """
    _harness, child, _spawn = scripted
    child.emit('{"type":"extension_ui_request","id":"uuid-9",'
               '"method":"confirm","title":"Allow?"}')

    for _ in range(200):
        replies = [c for c in child.written
                   if c.get("type") == "extension_ui_response"]
        if replies:
            break
        await asyncio.sleep(0.01)
    assert replies == [{"type": "extension_ui_response", "id": "uuid-9",
                        "cancelled": True}]


@pytest.mark.asyncio
async def test_a_child_that_dies_mid_prompt_still_ends_the_prompt(scripted):
    """AC-48's hardest case, and the one no fake can script.

    A killed child stops producing events; if that silence is the end of the
    story, the caller waits forever for an answer, which is a held
    microphone (D-1). The verdict is taken at the child's CLOSE and not at
    the first stderr line, because stdout and stderr are different pipes and
    an early verdict is a race (plan.md 4.4c).
    """
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    await harness.prompt("read the whole repository")
    child.emit('{"id":"p1","type":"response","command":"prompt",'
               '"success":true}')
    child.emit('{"type":"agent_start"}')
    child.die()

    seen = await _drain_to_terminal(stream, [])
    assert isinstance(seen[-1], Failed), (
        f"the child died mid-prompt and the prompt ended with {seen[-1]!r}")
    assert seen[-1].message.strip(), (
        "Failed carried no message; the caller SPEAKS this (AC-47)")
    with pytest.raises(StopAsyncIteration):
        await _next(stream, "the stream to END after the child died")


@pytest.mark.asyncio
async def test_the_stream_ends_when_the_child_does(scripted):
    """A reader task that never sees its iterator close is leaked per
    respawn, and a spoken project switch respawns mid-conversation."""
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    child.die()
    with pytest.raises(StopAsyncIteration):
        await _next(stream, "the stream to END after the child died")


@pytest.mark.asyncio
async def test_a_prompt_rejected_by_the_child_is_a_terminal_event(scripted):
    """rpc.md's Error Handling, which pi uses for a prompt it will not take.

    `success: false` is the whole answer -- no agent_start, no turn, no
    `agent_end` will ever follow -- so an adapter that only watched the event
    stream would wait out the turn on a prompt the child had already refused.
    """
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    child.respond = False          # this child answers the prompt itself
    await harness.prompt("hello")
    child.emit(json.dumps({"id": child.last_prompt_id(), "type": "response",
                           "command": "prompt", "success": False,
                           "error": "Agent is already processing."}))

    seen = await _drain_to_terminal(stream, [])
    assert isinstance(seen[-1], Failed)
    assert "already processing" in seen[-1].message


@pytest.mark.asyncio
async def test_a_malformed_record_still_lets_the_prompt_end(scripted):
    """AC-48, D-1, and the reason the reader has a `finally`.

    Both lines below are VALID JSON that pi's own vocabulary says nothing
    about: a string where the mapper expects an object. Without a guard the
    reader task dies on the first one, `_on_close` never runs, and NO
    terminal event ever arrives -- the predecessor's held-turn-lock defect
    (plan.md 4.4c) reached through a typo rather than a provider outage. One
    unreadable record costs one event; it may never cost the turn its ending.
    """
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    await harness.prompt("what does this module do")
    child.emit('{"type":"message_update","assistantMessageEvent":"oops"}')
    child.emit('{"type":"turn_end","message":"oops"}')
    child.emit('{"type":"message_update","assistantMessageEvent":{"type":'
               '"text_delta","contentIndex":0,"delta":"still here"}}')
    child.emit('{"type":"agent_end","messages":[]}')

    seen = await _drain_to_terminal(stream, [])
    assert len(_terminals(seen)) == 1, (
        f"a malformed record cost the prompt its ending: {seen}")
    assert isinstance(seen[-1], AgentEnd)
    assert any(isinstance(e, TextDelta) and e.text == "still here"
               for e in seen), (
        "the reader stopped reading after the malformed records, so the "
        "prompt ended only because the child happened to close")


@pytest.mark.asyncio
async def test_a_reader_that_dies_on_the_pipe_still_ends_the_prompt(scripted):
    """The other half of the same rule, and why the close is in a `finally`.

    A record this adapter cannot map is one failure; the PIPE failing under
    the reader is another, and it reaches the same place. However this task
    dies, the prompt gets its one terminal event and the stream closes --
    otherwise the caller waits on an answer that cannot arrive, which is
    indistinguishable from a dead microphone (D-1, AC-48).
    """
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    await harness.prompt("what does this module do")
    child.explode(OSError("the pipe went away"))

    seen = await _drain_to_terminal(stream, [])
    assert isinstance(seen[-1], Failed), (
        f"the reader died and the prompt ended with {seen[-1]!r}")
    assert seen[-1].message.strip()
    with pytest.raises(StopAsyncIteration):
        await _next(stream, "the stream to END after the reader died")


@pytest.mark.asyncio
async def test_a_late_terminal_from_the_previous_prompt_ends_nothing(scripted):
    """A prompt is identified by the id pi ACCEPTED, not by being next.

    pi answers `abort` asynchronously and retries on its own, so an
    `agent_end` belonging to the prompt just finished can land after the next
    one is sent. Without identity that stale event ends prompt N+1 instantly:
    Butler announces silence, and the real answer arrives afterwards to a
    turn that is already over.
    """
    harness, child, _spawn = scripted
    stream = harness.subscribe()
    await harness.prompt("first question")
    child.emit('{"type":"agent_end","messages":[]}')
    first = await _drain_to_terminal(stream, [])
    assert isinstance(first[-1], AgentEnd)

    child.respond = False           # the acceptance is emitted by hand below
    await harness.prompt("second question")
    child.emit('{"type":"agent_end","messages":[]}')          # prompt one's
    child.emit(json.dumps({"id": child.last_prompt_id(), "type": "response",
                           "command": "prompt", "success": True}))
    child.emit('{"type":"turn_end","message":{"role":"assistant","content":'
               '[{"type":"text","text":"the real answer"}],'
               '"stopReason":"stop"},"toolResults":[]}')
    child.emit('{"type":"agent_end","messages":[]}')

    second = await _drain_to_terminal(stream, [])
    assert len(_terminals(second)) == 1
    assert any(isinstance(e, TurnEnd) and e.text == "the real answer"
               for e in second), (
        f"the second prompt ended before its own answer arrived: {second}")


SHELL_METACHARACTERS = [
    pytest.param('claude" & echo PWNED & rem ', id="a quote closes the argument"),
    pytest.param("%USERPROFILE%", id="a percent expands a variable"),
    pytest.param("a|b", id="a pipe"),
    pytest.param("a<b", id="a redirect in"),
    pytest.param("a>b", id="a redirect out"),
    pytest.param("a^b", id="a caret escapes"),
]


@pytest.mark.parametrize("value", SHELL_METACHARACTERS)
def test_a_shell_metacharacter_in_a_constructor_option_is_refused(value):
    """The `cmd /c` spawn (plan.md 4.5) is a command LINE, not an argv.

    `cmd` re-parses what it is handed, so a double quote inside an argument
    closes the quoting and everything after `&` runs as a second command --
    demonstrated: this exact model value printed PWNED. `harness_model` and
    `harness_cwd` become owner-typed settings in 7.4, which is when a
    mistyped setting stops being a typo and becomes an execution path.
    """
    with pytest.raises(ValueError, match="cmd"):
        ports.build(ports.HARNESSES, "pi", model=value)


@pytest.mark.parametrize("value", SHELL_METACHARACTERS)
@pytest.mark.asyncio
async def test_a_shell_metacharacter_in_the_spec_is_refused(monkeypatch,
                                                            tmp_path, value):
    """The same rule for what the DAEMON builds, not just what config does.

    The spec's model and session file both reach the command line, and 7.7
    puts a spoken project name behind the working directory. A value that
    reaches `cmd` unchecked is the injection above with a microphone in front
    of it.
    """
    async def _spawn(argv, cwd, env=None):
        raise AssertionError(f"a child was spawned with {argv!r}")

    monkeypatch.setattr(harness_pi, "_spawn", _spawn)
    harness = ports.build(ports.HARNESSES, "pi")
    spec = HarnessSpec(model=value, cwd=str(tmp_path),
                       session_file=str(tmp_path / "s.jsonl"))
    with pytest.raises(ValueError, match="cmd"):
        await harness.start(spec)


# --------------------------------------------------------------------------- #
# The live child
# --------------------------------------------------------------------------- #
def _pi_missing() -> str | None:
    """The `real_stt_daemon` shape: skip when the real thing is not here."""
    if shutil.which("pi") is None:
        return "pi is not installed on this machine (pi --mode rpc, 7.2)"
    return None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_real_child_prompts_aborts_and_prompts_again(tmp_path):
    """The live proof, and the only test that runs the transport for real.

    Three properties in one child because a child costs a spawn: a prompt
    reaches its terminal event, an abort mid-flight ends that prompt exactly
    once and leaves the child usable, and a third prompt answers. The spawn
    gets its own budget, separate from anything per-prompt (D-5).

    It rides past the three unprompted `extension_ui_request`s every real
    child sends before its first response, which is AC-49's live half.
    """
    missing = _pi_missing()
    if missing:
        pytest.skip(missing)

    harness = ports.build(ports.HARNESSES, "pi", provider=LIVE_PROVIDER,
                          model=LIVE_MODEL)
    spec = HarnessSpec(model=None, cwd=str(tmp_path),
                       session_file=str(tmp_path / "live.jsonl"))
    started_at = time.perf_counter()
    await harness.start(spec)
    startup_ms = (time.perf_counter() - started_at) * 1000
    # Disposed in a `finally` even though `childproc`'s job object now kills
    # the tree when pytest exits: the job covers the machine, not the run, and
    # a child held for the rest of the suite is still a leak (AC-36).
    try:
        stream = harness.subscribe()
        first = await _live_prompt(harness, stream, "say the word ok")
        if isinstance(first[-1], Failed):
            pytest.skip(
                f"the configured pi provider ({LIVE_PROVIDER}/{LIVE_MODEL}) "
                f"is not answering: {first[-1].message[:200]}. The transport "
                f"still delivered exactly one terminal event.")
        assert len(_terminals(first)) == 1
        assert any(isinstance(e, TurnEnd) and e.text.strip() for e in first), (
            f"the child answered nothing: {first}")

        await harness.prompt("write a 2000 word essay about the typewriter")
        await asyncio.sleep(2.0)
        await harness.abort()
        aborted = await _live_drain(stream, [], 60.0)
        assert len(_terminals(aborted)) == 1, (
            f"an aborted prompt produced {len(_terminals(aborted))} terminal "
            f"events; AC-48 says exactly one, aborted or not")

        again = await _live_prompt(harness, stream, "say the word ok")
        assert len(_terminals(again)) == 1, (
            "the abort took the child down with the prompt")
        assert any(isinstance(e, TurnEnd) for e in again)
    finally:
        await harness.dispose()

    print(f"\n[live pi] start {startup_ms:.0f} ms, "
          f"{LIVE_PROVIDER}/{LIVE_MODEL}")


async def _live_prompt(harness, stream, text, budget=120.0):
    await harness.prompt(text)
    return await _live_drain(stream, [], budget)


async def _live_drain(stream, seen, budget):
    """Read to the terminal event on a wall-clock budget of its own.

    Separate from the spawn budget (D-5): one shared watchdog killed a worker
    that was merely still loading, and a model that thinks for 30 s is not a
    child that failed to start.
    """
    deadline = time.perf_counter() + budget
    while True:
        left = deadline - time.perf_counter()
        if left <= 0:
            pytest.fail(f"no terminal event in {budget:.0f}s: {seen}")
        try:
            event = await asyncio.wait_for(anext(stream), left)
        except asyncio.TimeoutError:
            pytest.fail(f"no terminal event in {budget:.0f}s: {seen}")
        except StopAsyncIteration:
            return seen
        seen.append(event)
        if isinstance(event, TERMINAL_EVENTS):
            return seen


@pytest.mark.slow
@pytest.mark.asyncio
async def test_the_model_lister_reads_pis_own_registry(monkeypatch):
    """`harness_model`'s list, from the harness rather than a connection (7.4).

    The one place this can be wrong quietly: pi prints its table on STDERR
    [MEASURED 2026-08-18], so an adapter reading stdout alone reports that pi
    knows no models and the picker draws empty. 532 pairs here; the assertion
    is on the SHAPE, because the owner's registry is theirs to change.

    The cache is defeated first, or a lister that never ran at all would pass
    on an entry some earlier test left behind (D-13).
    """
    missing = _pi_missing()
    if missing:
        pytest.skip(missing)
    monkeypatch.setattr(harness_pi, "_model_cache", [])

    found = await asyncio.wait_for(harness_pi.PiHarness.list_models(), 120)

    assert len(found) > 10, f"pi listed almost nothing: {found}"
    assert all("/" in m for m in found), (
        f"the lister must answer provider/model, which is what --model takes: "
        f"{found[:5]}")
    assert len(set(found)) == len(found), "the lister repeated a pair"
    assert not any(m.startswith("[") or m.startswith("provider/")
                   for m in found), (
        f"a banner or the table header was read as a model: {found[:5]}")
    print(f"\n[live pi] {len(found)} provider/model pairs")
