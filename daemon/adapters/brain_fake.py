"""A scripted Brain. Second implementation of the port (AC-7a).

Maps an utterance to a fixed list of intents so protocol and policy tests
run with no model, no network, and no nondeterminism. This is also the
adapter that proves the Brain seam is real: if butlerd can run its whole
turn loop against scripted intents, then an external agent framework can be
dropped in behind the same port.

It exercises the rule that matters: this brain emits Dispatch freely, and
butlerd is still expected to refuse or gate it according to the mode. If a
test ever shows a worker spawned straight from a Dispatch here, the policy
boundary has leaked (AC-9).
"""
from __future__ import annotations

from typing import AsyncIterator, Callable

from .. import ports
from ..ports import (Action, AskConfirm, Clarify, Dispatch, Done, Intent,
                     Say, Steer, TurnContext)

# Default script: enough to drive the Phase 1 loop without a model.
DEFAULT_RULES: list[tuple[str, Callable[[str, TurnContext], list[Intent]]]] = [
    ("what time is it",
     lambda t, c: [Say("It is twenty past four.", tier="reflex"), Done("reflex")]),
    ("delete",
     lambda t, c: [AskConfirm(Action(summary=t, writes=True)), Done("reflex")]),
    ("have claude",
     lambda t, c: [AskConfirm(Action(summary=t, harness="claude",
                                     cwd=".", writes=True)), Done("reflex")]),
    ("run the tests",
     lambda t, c: [Clarify("Which project?"), Done("reflex")]),
    ("tell it",
     lambda t, c: ([Steer(c.workers[0].worker_id, t), Done("reflex")]
                   if c.workers
                   else [Clarify("Nothing is running. Start something?"),
                         Done("reflex")])),
]


@ports.register(ports.BRAINS, "fake")
class FakeBrain:
    """Rule-matched intents. Unmatched utterances go to the conversation
    tier and produce two sentences, so the sentence splitter and the
    first-sentence cap have something to chew on."""

    def __init__(self, rules=None):
        self.rules = list(rules if rules is not None else DEFAULT_RULES)
        self.seen: list[tuple[str, int]] = []
        self.aborted: list[int] = []

    async def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]:
        self.seen.append((text, ctx.turn_id))
        lowered = text.lower()
        for needle, make in self.rules:
            if needle in lowered:
                for intent in make(text, ctx):
                    yield intent
                return
        yield Say("Let me think about that.", tier="conversation")
        yield Say("Here is the longer answer to your question.",
                  tier="conversation")
        yield Done("conversation")

    async def abort(self, turn_id: int) -> None:
        """Records the call, like `tts_fake` records handles.

        A fake that discarded this would let a daemon which never calls
        abort pass its own test -- which is not hypothetical, it is what
        happened on the TTS side for a day (D-18).
        """
        self.aborted.append(turn_id)

    async def warm(self) -> None:
        """Nothing to warm, and it must still answer.

        Boot calls this unconditionally (D-10). A fake that omitted it would
        make `butlerd.warmup` raise on the all-fakes configuration, which is
        the configuration every protocol test runs.
        """
        return None

    @property
    def info(self) -> dict:
        """Says plainly that this is the fake.

        Load-bearing rather than decorative: this brain matches five
        substrings and says "Let me think about that" to everything else, so
        a daemon running it looks exactly like a daemon whose real model is
        being unhelpful. The owner should be able to tell from across the
        room which one they have (AC-42).
        """
        return {"engine": "fake", "rules": len(self.rules)}
