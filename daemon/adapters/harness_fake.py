"""A scripted Harness. First implementation of the port (AC-7a, AC-44).

Not a null object (adapters/AGENTS.md). It reproduces the five behaviours
the callers actually depend on, each one taken from something measured or
from a defect the predecessor shipped (plan.md 4.4c):

  - a MULTI-TURN answer with tool calls, so "one terminal event per prompt"
    is a claim with something to be wrong about (AC-48);
  - tool events carrying THIS adapter's own ids and a real outcome, with a
    failing tool in the default script, so an adapter that hardcodes success
    cannot pass the suite;
  - a prompt that FAILS on demand (`fail_on`), because `Failed` is otherwise
    a terminal event nothing in the repository can emit, and 7.4 has to
    speak it (AC-47, AC-48);
  - a no-delta mode, because a measured cloud provider emits zero text
    deltas and puts the whole answer in `turn_end` (AC-47);
  - a RECORD of every `HarnessSpec` it was started with, which is what the
    project-switch and respawn tests assert against: those steps prove
    themselves by what the child was spawned AS;
  - a QUESTION for a human that BLOCKS the stream until it is answered
    (AC-234, `ask=`), because AC-49's reversal is otherwise untestable
    without a real coding agent and a real extension -- and because a fake
    that asked and carried on would pass a daemon that never suspends the
    idle deadline.

Events are produced as the reader takes them rather than pushed from a task.
That is deliberate: an abort then lands between two events instead of racing
a producer, so the tests that matter are deterministic. The callers drain
this stream continuously, so nothing depends on the difference.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from .. import ports
from ..ports import (AgentEnd, Answer, Ask, Failed, HarnessCaps, HarnessEvent,
                     HarnessSpec, TextDelta, ToolEnd, ToolStart, TurnEnd)


@dataclass(frozen=True)
class ScriptedTurn:
    """One agent turn: some tools, then something said about them."""
    text: str
    tools: tuple[tuple[str, bool], ...] = ()   # (tool name, is_error)


# Two turns, three tools, one of which FAILS -- an agent that tries
# something, is refused, and answers another way. The failure is
# load-bearing: with an all-succeeding script, an adapter that hardcodes a
# successful outcome passes the conformance suite unchanged, which is the
# predecessor's defect surviving its own test (D-13, plan.md 4.4c). It sits
# in the FIRST turn so that a mutation to the terminal-event rule fails the
# test that owns that rule rather than dragging the tool test down with it.
DEFAULT_SCRIPT: tuple[ScriptedTurn, ...] = (
    ScriptedTurn("Let me look at the endpointing module.",
                 tools=(("read", False), ("grep", True))),
    ScriptedTurn("It holds three policies and one of them is not wired up.",
                 tools=(("read", False),)),
)

_END_OF_STREAM = object()

# Deliberately shaped like the real lister's answer -- `provider/model`, which
# is what pi's `--model` accepts -- and deliberately named so nobody mistakes
# one of these for something that exists.
FAKE_MODELS = ("fakeprovider/fake-small", "fakeprovider/fake-large")


@ports.register(ports.HARNESSES, "fake")
class FakeHarness:
    """A coding agent that never runs, with the timing taken out.

    `stream=False` is the measured non-streaming provider: no text deltas at
    all, the whole message in `turn_end`.

    `fail_on` is a substring of a prompt that FAILS, shaped like
    `tts_fake.fail_on` -- the difference is that this one is a value, not an
    exception: a failed prompt is spoken (AC-47), so it ends with `Failed`
    rather than raising. `fail_once` fails only the FIRST matching prompt,
    which is the only way to script a transient blip: Butler replays a failed
    turn once from the verbatim input (plan.md 4.4c), and a fake that cannot
    recover cannot show that replay working.

    `fail_after` is how far into the answer the failure lands. A prompt that
    fails with its answer SKIPPED can only ever test half of the replay rule:
    Butler replays a failed turn only while nothing has been said, and with no
    way to script "some text, then a failure" the other half of that condition
    was untestable and therefore untested -- removing it left the whole fast
    tier green. `fail_after=n` yields the first n events of the scripted answer
    and then `Failed`, so a turn can reach the room before it breaks.
    """

    def __init__(self, script: tuple[ScriptedTurn, ...] | None = None,
                 stream: bool = True, native_resume: bool = False,
                 fail_on: str | None = None, fail_once: bool = False,
                 fail_after: int | None = None,
                 ask: Ask | tuple[Ask, ...] = ()):
        self.script = tuple(script if script is not None else DEFAULT_SCRIPT)
        self.stream = stream
        # Without this switch `Failed` is unreachable in every adapter that
        # exists, so "exactly one terminal event" is only ever tested on the
        # half that succeeds -- and the predecessor's defect was precisely
        # the other half: its error event fired only on a JS exception, so a
        # provider failure ended nothing and held the turn lock forever
        # (AC-48, plan.md 4.4c).
        self.fail_on = fail_on
        self.fail_once = fail_once
        self.fail_after = fail_after
        # What this child ASKS before it answers (AC-234). A tuple, because
        # `select`, `confirm` and `input` are three different shapes and the
        # daemon has to be drivable against each without a real coding agent.
        # It BLOCKS, which is the property that matters: the stream produces
        # nothing at all until `answer()` is called, so a test can see the
        # idle deadline being suspended rather than merely see a frame go out.
        self.asks = (ask,) if isinstance(ask, Ask) else tuple(ask)
        # Every answer this fake was handed, oldest first: (qid, Answer). The
        # record is what `test_harness_question.py` asserts on -- AC-250 is a
        # claim about what did NOT reach the child, and a fake that discarded
        # the call could not tell that from an answer that arrived.
        self.answers: list[tuple[str, Answer]] = []
        self._blocked: dict[str, asyncio.Future] = {}
        self.failures = 0
        # Every spec this fake was ever started with, oldest first. The
        # respawn steps (a changed setting, the spoken project switch) have
        # nothing else to assert on: what a child IS is decided at spawn.
        self.specs: list[HarnessSpec] = []
        self.prompts: list[str] = []
        self.aborts = 0
        self.disposals = 0
        self._caps = HarnessCaps(
            native_resume=native_resume,
            # No `steer` method exists at this port yet, so claiming the
            # capability would let a caller read True and find nothing to
            # call. False is the honest answer for the fake; pi's adapter
            # answers for pi.
            steerable=False,
            images=False,
        )
        self._live = False
        self._inbox: asyncio.Queue | None = None
        self._aborting = False
        self._prompting = False
        self._tools_seen = 0

    # -- lifecycle --------------------------------------------------------- #
    async def start(self, spec: HarnessSpec) -> None:
        if self._live:
            raise RuntimeError(
                "start() on a live child. Changing what a child IS means "
                "dispose() first, because the spec is fixed at spawn (AC-31)")
        self.specs.append(spec)
        self._inbox = asyncio.Queue()
        self._aborting = False
        self._live = True

    async def dispose(self) -> None:
        """Idempotent, and it ENDS the subscribed stream.

        A reader task that never sees its iterator close is a task leaked
        per respawn, and a project switch respawns mid-conversation (AC-31).
        """
        self.disposals += 1
        if not self._live:
            return
        self._live = False
        self._inbox.put_nowait(_END_OF_STREAM)
        self._inbox = None
        # A child that is disposed while it is blocked on a question has to
        # let its own reader go, or the stream never ends and the task that
        # drains it is leaked per respawn -- the rule `dispose` already keeps
        # for the queue, applied to the second thing that can park it.
        blocked, self._blocked = self._blocked, {}
        for future in blocked.values():
            if not future.done():
                future.set_result(False)

    # -- the stream -------------------------------------------------------- #
    def subscribe(self) -> AsyncIterator[HarnessEvent]:
        if not self._live:
            raise RuntimeError("subscribe() before start(): no child to read")
        return self._drain(self._inbox)

    async def _drain(self, inbox: asyncio.Queue) -> AsyncIterator[HarnessEvent]:
        while True:
            item = await inbox.get()
            if item is _END_OF_STREAM or inbox is not self._inbox:
                return                          # disposed, or a stale stream
            self._prompting = True
            failing = (self.fail_on is not None and self.fail_on in item
                       and not (self.fail_once and self.failures))
            if failing:
                self.failures += 1
            # A failing prompt yields nothing at all unless `fail_after` says
            # how much of the answer got out first.
            budget = (self.fail_after if failing else None)
            if not failing or budget:
                # THE QUESTIONS COME FIRST AND THEY BLOCK (AC-234). A real
                # child raises its dialog from inside a tool call and stops
                # there, so this stream must stop too: a fake that asked and
                # then carried on would let a daemon which never suspends the
                # idle deadline pass the test for that rule.
                stop = False
                for ask in self.asks:
                    if inbox is not self._inbox:
                        return
                    yield ask
                    if not await self._blocked_on(ask.qid):
                        stop = True
                        break
                    if inbox is not self._inbox:
                        return
                if stop:
                    return
                spoken = 0
                for event in self._answer(item):
                    if inbox is not self._inbox:
                        return  # disposed MID-answer: the reader ends too
                    if self._aborting:
                        break                   # barge-in: drop the rest
                    if budget is not None and spoken >= budget:
                        break                   # the failure lands here
                    spoken += 1
                    yield event
            # Exactly one terminal event per prompt, aborted or not (AC-48),
            # and `Failed` REPLACES `AgentEnd` rather than joining it: a
            # caller that saw both would announce a failure and then an
            # answer it never got.
            self._aborting = False
            self._prompting = False
            if failing:
                yield Failed(message=f"fake harness refused the prompt: "
                                     f"{item!r}")
            else:
                yield AgentEnd()

    async def _blocked_on(self, qid: str) -> bool:
        """Park until somebody answers `qid`. False means the child was let go."""
        future = asyncio.get_running_loop().create_future()
        self._blocked[qid] = future
        try:
            return await future
        finally:
            self._blocked.pop(qid, None)

    def _answer(self, text: str) -> Iterator[HarnessEvent]:
        for turn in self.script:
            for name, is_error in turn.tools:
                self._tools_seen += 1
                # This adapter's OWN id, unique per call. A shared "current
                # step" slot is what let parallel calls overwrite each other
                # in the predecessor (plan.md 4.4c).
                tool_id = f"fake-tool-{self._tools_seen}"
                yield ToolStart(tool_id=tool_id, name=name)
                yield ToolEnd(tool_id=tool_id, name=name, is_error=is_error)
            if self.stream and turn.text:
                # A word at a time, separated the way ports.TextDelta says:
                # a space inside a turn, and a paragraph break ENDING it, as
                # `harness_pi` emits at pi's `text_end`. A trailing space per
                # word used to separate the turns, which hid the glue a real
                # pi child produces (docs/e2e-journeys.md).
                for index, word in enumerate(turn.text.split(" ")):
                    yield TextDelta(text=(" " if index else "") + word)
                yield TextDelta(text="\n\n")
            # Carried whether or not anything streamed: on the measured
            # non-streaming provider this is the only text there will be.
            yield TurnEnd(text=turn.text)

    # -- driving it -------------------------------------------------------- #
    async def prompt(self, text: str) -> None:
        if not self._live:
            raise RuntimeError(
                "prompt() before start(): there is no child. Failing here "
                "rather than queueing, because a prompt that goes nowhere is "
                "a turn that never ends")
        self.prompts.append(text)
        self._inbox.put_nowait(text)

    async def answer(self, qid: str, answer: Answer) -> bool:
        """Unblock a question, and say whether this child still held it.

        The False branch is the one with teeth (AC-250): an id nobody raised,
        one already answered, and one whose child is gone all return False
        and nothing is recorded, so a test can assert that a late answer
        NEVER REACHED THE CHILD rather than only that a route said 410.
        """
        future = self._blocked.get(qid)
        if future is None or future.done():
            return False
        self.answers.append((qid, answer))
        future.set_result(True)
        return True

    async def abort(self) -> None:
        """Records the call and truncates the answer.

        Recorded like `tts_fake` records handles: a fake that discarded this
        would let a daemon which never calls abort pass its own test, which
        is not hypothetical -- it happened on the TTS side for a day (D-18).
        Inert when nothing is running, because barge-in races completion by
        nature and raising there would turn a routine race into a held
        microphone (D-1).
        """
        self.aborts += 1
        if self._live and (self._prompting or not self._inbox.empty()):
            self._aborting = True
        # A prompt parked on a question is still a prompt, and AC-48 says it
        # ends with exactly one terminal event however it was stopped. So the
        # block is released rather than abandoned: the answer loop below sees
        # `_aborting`, drops the rest, and still yields `AgentEnd`.
        for future in list(self._blocked.values()):
            if not future.done():
                future.set_result(True)

    # -- what it is -------------------------------------------------------- #
    @classmethod
    async def list_models(cls) -> list[str]:
        """What this harness could be pointed at (`harness_model`, 7.4).

        A CLASSMETHOD, like the real one, because the picker must answer
        before any child has ever been started -- the owner configures the
        harness and then selects it. Scripted here so the wiring from the
        settings page to a harness's own registry is testable without
        installing a coding agent; the real list is the harness's own and
        Butler never learns its vocabulary (AC-41).
        """
        return list(FAKE_MODELS)

    @property
    def caps(self) -> HarnessCaps:
        return self._caps

    @property
    def info(self) -> dict:
        """Says plainly that this is the fake, for the reason `brain_fake`
        does: a harness that answers from a script looks exactly like a real
        one being unhelpful, and the owner should be able to tell which they
        have (AC-42)."""
        return {"engine": "fake", "turns": len(self.script),
                "streams": self.stream}
