"""The router: one JSON verdict per utterance. plan.md 4.4, step 7.4.

What these encode, in one line each. The verdict is ENFORCED by a schema at
sampling time and never requested in a prompt (D-11). The schema is built per
TURN, because offering `steer` with nothing running measured a point of
routing accuracy and every failure was a spurious steer. A verdict that comes
back unusable loses the ROUTING and never the turn (plan.md 3.8, AC-39). And
the token cap is on the wire or it is nowhere (AC-38).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver                          # noqa: E402
from daemon import dialogue                                # noqa: E402
from daemon.ports import (Action, AskConfirm, Clarify, Done,  # noqa: E402
                          RunningWorker, Say, Steer, TurnContext)


class RecordingConverse:
    """The substantive brain behind the router, minus a model.

    Isolating the router from the thing it routes TO is the whole point: a
    test that used the real endpoint brain could not tell "the router chose
    converse" from "the conversation model happened to answer".
    """

    def __init__(self):
        self.asked: list[tuple[str, int]] = []
        self.aborted: list[int] = []

    async def turn(self, text, ctx):
        self.asked.append((text, ctx.turn_id))
        yield Say("The conversation model answered.", tier="conversation")
        yield Done("conversation")

    async def abort(self, turn_id):
        self.aborted.append(turn_id)

    async def warm(self):
        return None

    @property
    def info(self):
        return {"engine": "recording"}


def _ctx(turn_id=1, workers=()):
    return TurnContext(turn_id=turn_id, mode="secretary", workers=workers)


def _brain(server, caps=None, converse=None):
    resolver = StubResolver(server.base_url, caps=caps)
    return dialogue.Dialogue(
        router=dialogue.Router(resolver),
        converse=converse if converse is not None else RecordingConverse())


async def _intents(brain, text="what time is it", ctx=None):
    return [i async for i in brain.turn(text, ctx or _ctx())]


WORKER = RunningWorker(worker_id="w1", harness="claude", cwd=".",
                       task="fix the tests")


# --------------------------------------------------------------------------- #
# the schema, which is the enforcement
# --------------------------------------------------------------------------- #
def test_the_verdict_declares_route_first():
    """R9. A field declared first is emitted first, and is readable off the
    stream at 126 ms against 375 ms for the whole object [MEASURED, 4.4].

    Butler does not read the stream early yet, and this still matters: the
    ordering is what leaves that option open, and it costs nothing to keep.
    """
    schema = dialogue.verdict_schema(workers=())
    assert list(schema["properties"])[0] == "route", (
        f"route must be declared first, got {list(schema['properties'])}")


def test_steer_is_not_offered_when_nothing_is_running():
    """MEASURED 2026-08-16: with `steer` in the enum the router scored 7/10 and
    EVERY failure was a spurious steer, even though the prompt said no workers
    were running. Removing it took the same model to 8/10.

    A route the model cannot emit is a route it cannot get wrong -- which is
    why this is in the schema and not in the prompt (D-11).
    """
    routes = dialogue.verdict_schema(workers=())["properties"]["route"]["enum"]
    assert "steer" not in routes, f"steer offered with no worker: {routes}"
    assert "steer" in dialogue.verdict_schema(workers=(WORKER,))[
        "properties"]["route"]["enum"]


def test_the_schema_uses_only_what_llamacpp_actually_enforces():
    """D-11, R9's negative test. llama.cpp silently DROPS `not` and `if/then`:
    a probe emitted a forbidden value in 2 of 5 runs and answered HTTP 200.

    So a schema that expressed "answer is required when route is answer" would
    read as enforcement and be a suggestion. Butler expresses only what is
    enforced -- types, an enum, a property list -- and checks the rest in code
    (`Router.verdict` treats a route with no payload as unusable).
    """
    banned = {"not", "if", "then", "else", "oneOf", "anyOf", "allOf",
              "dependentSchemas", "patternProperties"}

    def walk(node, path="schema"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, (
                    f"{path}.{key} uses {key!r}, which llama.cpp drops "
                    f"silently -- the constraint would not be enforced")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(dialogue.verdict_schema(workers=(WORKER,)))


@pytest.mark.asyncio
async def test_the_verdict_is_constrained_on_the_wire_not_asked_for(fake_openai):
    """D-11 again, on the REQUEST this time. `response_format` carries the
    schema; a prompt that merely asks for JSON is the thing this rule exists
    to forbid, and the two are indistinguishable from the reply alone."""
    fake_openai.verdict(route="converse")
    await _intents(_brain(fake_openai))

    body = fake_openai.last_body()
    fmt = body.get("response_format") or {}
    assert fmt.get("type") == "json_schema", (
        f"the verdict was not schema-constrained: {body!r}")
    assert fmt["json_schema"]["schema"]["properties"]["route"]["enum"], \
        "the constraint carries no route enum"
    assert body.get("stream") is not True, (
        "the verdict is read whole; a streamed verdict would need a partial "
        "JSON parser this does not have")


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_answer_is_spoken_by_the_router_itself(fake_openai):
    converse = RecordingConverse()
    fake_openai.verdict(route="answer", answer="It is twenty past four.")
    intents = await _intents(_brain(fake_openai, converse=converse))

    assert [type(i) for i in intents] == [Say, Done]
    assert intents[0].text == "It is twenty past four."
    assert intents[0].tier == "reflex"
    assert converse.asked == [], "the conversation model was asked anyway"


@pytest.mark.asyncio
async def test_converse_hands_the_turn_to_the_substantive_brain(fake_openai):
    converse = RecordingConverse()
    fake_openai.verdict(route="converse")
    intents = await _intents(_brain(fake_openai, converse=converse),
                             text="explain the GIL")

    assert converse.asked == [("explain the GIL", 1)]
    assert any(isinstance(i, Say) and i.tier == "conversation" for i in intents)


@pytest.mark.asyncio
async def test_clarify_asks_one_question(fake_openai):
    fake_openai.verdict(route="clarify", question="Which project?")
    intents = await _intents(_brain(fake_openai))
    assert [type(i) for i in intents] == [Clarify, Done]
    assert intents[0].question == "Which project?"


@pytest.mark.asyncio
async def test_dispatch_is_a_proposal_and_never_an_action(fake_openai):
    """AC-9, the load-bearing boundary: the Brain proposes, butlerd disposes.

    A router that returned Dispatch directly would hand a model the decision
    the Mode Store is supposed to make.
    """
    fake_openai.verdict(route="dispatch",
                        action={"summary": "fix the failing test",
                                "harness": "claude", "cwd": ".",
                                "writes": True})
    intents = await _intents(_brain(fake_openai))
    assert [type(i) for i in intents] == [AskConfirm, Done]
    assert isinstance(intents[0].action, Action)
    assert intents[0].action.writes is True


@pytest.mark.asyncio
async def test_steer_reaches_a_worker_that_is_actually_running(fake_openai):
    fake_openai.verdict(route="steer",
                        steer={"worker": "w1", "message": "also the README"})
    intents = await _intents(_brain(fake_openai),
                             ctx=_ctx(workers=(WORKER,)))
    assert [type(i) for i in intents] == [Steer, Done]
    assert intents[0].worker_id == "w1"


@pytest.mark.asyncio
async def test_steer_at_a_worker_that_is_not_running_becomes_a_question(
        fake_openai):
    """A steering line delivered to the wrong worker is a write to the wrong
    repository (4.4). The id is checked against the live list, not trusted."""
    fake_openai.verdict(route="steer",
                        steer={"worker": "ghost", "message": "carry on"})
    intents = await _intents(_brain(fake_openai), ctx=_ctx(workers=(WORKER,)))
    assert [type(i) for i in intents] == [Clarify, Done]


# --------------------------------------------------------------------------- #
# what happens when the verdict is not usable (plan.md 3.8, AC-39)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unparseable_verdict_loses_the_routing_not_the_turn(
        fake_openai, capsys):
    """plan.md 3.8's stated exception to fail-fast: the verdict is MODEL
    output, not our input, and losing the user's turn is the worse failure."""
    converse = RecordingConverse()
    fake_openai.answer("I think you want me to look at the tests?")
    intents = await _intents(_brain(fake_openai, converse=converse))

    assert converse.asked, "an unparseable verdict lost the turn"
    assert any(isinstance(i, Say) for i in intents)
    out = capsys.readouterr().out
    assert "verdict" in out.lower(), f"the parse failure was silent: {out!r}"


@pytest.mark.asyncio
async def test_a_route_outside_the_enum_is_not_believed(fake_openai):
    """The enum is enforcement only while the server honours it. A server that
    does not -- llama.cpp does drop parts of a schema -- must not be able to
    put Butler into a route it has no code for."""
    converse = RecordingConverse()
    fake_openai.verdict(route="rm -rf", answer="done")
    await _intents(_brain(fake_openai, converse=converse))
    assert converse.asked, "an unknown route was acted on rather than refused"


@pytest.mark.asyncio
async def test_a_route_with_no_payload_falls_through_to_the_conversation(
        fake_openai):
    """The schema cannot say "answer is required when route is answer" (the
    llama.cpp limitation above), so the code says it. An empty answer spoken
    aloud is silence with a tier attached."""
    converse = RecordingConverse()
    fake_openai.verdict(route="answer", answer="   ")
    await _intents(_brain(fake_openai, converse=converse))
    assert converse.asked, "an empty answer was treated as an answer"


@pytest.mark.asyncio
async def test_an_empty_capped_verdict_says_so_in_the_log(fake_openai, capsys):
    """AC-39. MEASURED 2026-08-17: a reasoning model under a 200-token cap
    spent the whole cap thinking and returned EMPTY content, 10 times out of
    10. The turn must still be answered, and the log has to make "capped"
    distinguishable from "garbage" -- the behaviour is identical, so only the
    log can tell them apart.
    """
    converse = RecordingConverse()
    fake_openai.answer("", finish_reason="length",
                       reasoning="Let me think about which route this is...")
    brain = _brain(fake_openai, caps={"router": 200}, converse=converse)
    await _intents(brain)

    assert converse.asked, "a capped verdict lost the turn"
    out = capsys.readouterr().out
    assert "finish_reason=length" in out, f"log did not name why: {out!r}"
    assert "cap=200" in out, f"log did not name the cap: {out!r}"


# --------------------------------------------------------------------------- #
# the cap on the wire (AC-38, router half)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_router_cap_is_sent_when_set(fake_openai):
    await _intents(_brain(fake_openai, caps={"router": 512}))
    assert fake_openai.last_body().get("max_tokens") == 512


@pytest.mark.asyncio
async def test_zero_sends_no_cap_at_all(fake_openai):
    """0 means ABSENT, not `max_tokens: 0`. A server that took a literal zero
    would answer nothing at all, which is the failure the setting exists to
    prevent rather than cause."""
    await _intents(_brain(fake_openai, caps={"router": None}))
    assert "max_tokens" not in fake_openai.last_body()


# --------------------------------------------------------------------------- #
# reasoning off on the wire (router half). Same shape as the cap: the control
# is on the wire or it is nowhere, and OFF means an added kwarg while ON means
# a byte-identical request (AC-38's rule, 2026-08-18 reasoning measurement).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_router_reasoning_off_sends_the_no_think_kwarg(fake_openai):
    resolver = StubResolver(fake_openai.base_url, reasoning={"router": False})
    brain = dialogue.Dialogue(router=dialogue.Router(resolver),
                              converse=RecordingConverse())
    [i async for i in brain.turn("hello", _ctx())]
    assert (fake_openai.last_body().get("chat_template_kwargs")
            == {"enable_thinking": False})


@pytest.mark.asyncio
async def test_router_reasoning_on_sends_no_kwarg(fake_openai):
    """The default. A reasoning control that shipped On would put a
    llama.cpp-only kwarg on every request, which a cloud connection never
    asked for -- so On must be indistinguishable from the request before this
    setting existed."""
    await _intents(_brain(fake_openai))
    assert "chat_template_kwargs" not in fake_openai.last_body()


@pytest.mark.asyncio
async def test_the_model_and_the_key_come_from_the_resolver(fake_openai):
    """AC-34/AC-37's other half: a key is read at call time and used for ONE
    request. The router never holds one and never chooses a model."""
    resolver = StubResolver(fake_openai.base_url, api_key="SECRET-FOR-ONE-CALL",
                            models={"router": "chosen-by-the-owner"})
    brain = dialogue.Dialogue(router=dialogue.Router(resolver),
                              converse=RecordingConverse())
    [i async for i in brain.turn("hello", _ctx())]

    assert fake_openai.last_body()["model"] == "chosen-by-the-owner"
    assert fake_openai.asks[-1].authorization == "Bearer SECRET-FOR-ONE-CALL"


# --------------------------------------------------------------------------- #
# barge-in (AC-43) against a second implementation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_abort_reaches_both_halves_of_the_brain(fake_openai):
    """AC-43, D-18. The router holds a request for the first fraction of a
    second and the substantive brain holds one for the rest of the turn, so a
    barge-in that reached only the first would leave the model that is
    actually generating still generating.

    That the abort reaches the SOCKET rather than only the caller is the other
    half, and it is asserted where it can bite: against a streaming reply, in
    test_brain_endpoint.py.
    """
    converse = RecordingConverse()
    brain = _brain(fake_openai, converse=converse)
    await brain.abort(7)
    assert converse.aborted == [7], "the substantive brain was never told"


@pytest.mark.asyncio
async def test_a_dead_router_is_not_quietly_answered_by_the_conversation_model(
        fake_openai):
    """docs/reflex-serving.md section 6, the mistake it exists to prevent.

    plan.md 3.8's exception to fail-fast is about bad MODEL OUTPUT from a
    healthy server. It does not extend to a server that is absent, dead or
    refusing: that would send every utterance -- including ones the router
    would have refused -- to the substantive model with no verdict at all,
    which is both a cost leak and, from Phase 2 on, a safety hole.
    """
    from daemon import llm

    converse = RecordingConverse()
    fake_openai.answer(status=502, body="upstream is down")
    with pytest.raises(llm.LlmError):
        await _intents(_brain(fake_openai, converse=converse))
    assert converse.asked == [], (
        "a dead router silently became a conversation call")


# --------------------------------------------------------------------------- #
# the boot check (D-10)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_boot_check_asks_for_a_real_verdict(fake_openai):
    """Not a ping. `/health` answering ok proves the server is up; it does not
    prove the grammar path works or the template rendered, and those are what
    every turn depends on (docs/reflex-serving.md section 5)."""
    await _brain(fake_openai).warm()
    assert (fake_openai.asks[0].body.get("response_format") or {}), \
        "the boot check was not a schema-constrained verdict"


@pytest.mark.asyncio
async def test_the_boot_check_fails_loudly_on_an_unusable_verdict(fake_openai):
    """Found by a live probe, not by reasoning: with the router capped at 16
    tokens the daemon warmed "successfully" while every verdict it could ever
    produce was already unusable. At boot nobody is waiting and there is no
    turn to lose, so this is the one place a bad verdict should raise."""
    fake_openai.answer("sure, here is what I think you meant")
    with pytest.raises(dialogue.VerdictUnusable):
        await _brain(fake_openai).warm()


@pytest.mark.asyncio
async def test_aborting_an_unknown_turn_is_inert(fake_openai):
    brain = _brain(fake_openai)
    await brain.abort(999_999)
    await brain.abort(999_999)
