"""Every Brain adapter must pass this. See plan.md 4.10 and AC-7a.

THREE implementations since 2026-08-18: `brain_fake`, `brain_endpoint` (the
router-plus-endpoint composite, plan.md section 5 step 7 piece 5) and
`brain_harness` (the same router in front of a coding agent, plan.md section 5
step 7 piece 6, built by docs/harness-mode.md step 7.4). Two documents number
these differently and the endpoint came FIRST: "7.5" is a piece of plan step
7, while "7.4" is a step of the harness-mode build order. This suite was
written while the fake was the only Brain, which is the point of writing it
early -- a suite written after `brain_endpoint` landed would describe
`brain_endpoint` rather than the port. The harness row is where AC-27's
both-modes-one-suite claim starts being true.

The endpoint parameter runs against the fake OpenAI server in conftest, not
a model: what a conformance suite can prove is that an adapter HOLDS THE
CONTRACT, and every property here is about shape. What it cannot prove is
that an abort reaches the socket or that a cap is on the wire -- those need a
server that records what it was sent, and they live in
`test/test_brain_endpoint.py` (D-18: conformance proves the callee).

Turn-shape properties -- exactly one `Done` per turn, intents belonging to
`ctx.turn_id` -- are still enforced in the hub rather than declared on the
port. Both implementations happen to honour them; pinning them here would be
generalising from two adapters that share a mapping, which is how a suite
ends up describing an accident.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver  # noqa: E402
from daemon import adapters  # noqa: F401,E402  (registers the adapters)
from daemon import butlerd, ports  # noqa: E402
from daemon.chats import ChatStore  # noqa: E402
from daemon.settings import Settings  # noqa: E402


def _ctx(turn_id: int) -> ports.TurnContext:
    return ports.TurnContext(turn_id=turn_id, mode="secretary")

ADAPTERS = ["fake", "endpoint", "harness"]


@pytest.fixture(params=ADAPTERS)
def brain(request, fake_openai, tmp_path):
    """The real adapters get a resolver aimed at the fake endpoint.

    The server is here for `endpoint` and `harness` and harmless for `fake`; a
    fixture that switched on the parameter would be one more branch to keep
    right, and the thread costs a millisecond.

    `harness` is the composite over the FAKE harness (AC-44: the row is what
    makes "both modes, one suite" a claim rather than an intention). Its spec
    resolver is the daemon's own, over a real Settings store pointed at
    tmp_path, because those settings are what decide the child's spec and a
    stub would let a typo in a key name pass.
    """
    if request.param == "endpoint":
        return ports.build(ports.BRAINS, "endpoint",
                           resolver=StubResolver(fake_openai.base_url))
    if request.param == "harness":
        prefs = Settings(tmp_path / "settings.json")
        prefs.set("harness_cwd", str(tmp_path))
        chats = ChatStore(tmp_path / "chats")
        return ports.build(ports.BRAINS, "harness",
                           resolver=StubResolver(fake_openai.base_url),
                           harness_for=lambda _chat=None: ports.build(
                               ports.HARNESSES, "fake"),
                           spec_for=butlerd.harness_spec_resolver(
                               prefs, chats),
                           chats=chats,
                           # The agent cap, read off the same real store as
                           # the spec (AC-237): a stub number here would let
                           # this row pass with the setting misspelled.
                           cap_for=lambda: prefs["harness_agents_max"])
    return ports.build(ports.BRAINS, request.param)


def test_satisfies_the_protocol(brain):
    assert isinstance(brain, ports.Brain)


def test_info_reports_what_the_ui_needs(brain):
    """The owner must be able to see which brain is answering.

    This matters more here than for the speech engines: `brain_fake` answers
    five substrings and says "Let me think about that" to everything else,
    and a daemon quietly running it looks exactly like a daemon whose model
    is being unhelpful. Naming the engine in hello is what tells those two
    apart from across the room (AC-42).
    """
    info = brain.info
    assert isinstance(info, dict)
    assert "engine" in info, f"info missing 'engine': {info}"
    assert isinstance(info["engine"], str) and info["engine"], \
        f"engine must be a non-empty string, got {info['engine']!r}"


def test_info_is_cheap_enough_for_the_handshake(brain):
    """Read during the handshake, so it must not load or connect.

    A Brain adapter that opened an HTTP session here would freeze every
    satellite each time one connected -- and this is the port most likely to
    be tempted, since a real brain lives behind a network call. Timed, not
    compared: see the Tts suite's copy for why equality proves nothing.
    """
    started = time.perf_counter()
    info = brain.info
    elapsed = time.perf_counter() - started
    assert isinstance(info, dict)
    assert elapsed < 0.25, (
        f"reading info took {elapsed * 1000:.0f} ms on the event loop")


@pytest.mark.asyncio
async def test_abort_is_callable_and_does_not_raise(brain):
    """Barge-in must be able to reach the brain (AC-43, D-4).

    Like the Tts suite's version, this cannot prove the adapter actually
    stops generating -- that is asserted per-adapter. What it catches is an
    adapter with no abort at all, which makes the daemon's call a crash
    instead of a barge-in.
    """
    await brain.abort(1)


@pytest.mark.asyncio
async def test_abort_of_an_unknown_turn_is_a_no_op(brain):
    """Barge-in races completion by nature.

    The turn may have finished microseconds before the wake arrived, and the
    daemon cannot know. An adapter that raised on an unfamiliar turn id would
    turn that ordinary race into a failed abandon -- tasks left running, the
    session never back to IDLE, the microphone held (D-1).
    """
    await brain.abort(999_999)
    await brain.abort(999_999)          # twice: still inert


@pytest.mark.asyncio
async def test_aborting_one_turn_does_not_disturb_a_LIVE_one(brain):
    """AC-43's second clause. An abort keyed by SESSION rather than by turn
    would cancel the reply to the utterance that CAUSED the barge-in -- the
    one turn the user is actually waiting for.

    Turn 2 is left suspended mid-iteration while turn 1 is aborted, and that
    overlap is the whole test. A first version ran the two in sequence --
    abort(1) to completion, then turn 2 from scratch -- which a session-keyed
    abort passes unchanged, because at the moment abort ran there was no
    other turn alive to damage (D-13: the test has to be able to fail).
    """
    # Honest limit: `FakeBrain.abort` only records, so this cannot fail
    # against the only adapter that exists -- it pins the property for the
    # SECOND implementation, which is what a conformance suite is for. The
    # assertion that bites today is at hub level, where the coupling is real
    # (test_hub_bargein.py).
    live = brain.turn("what time is it", _ctx(turn_id=2))
    first = await anext(live)               # turn 2 is now IN FLIGHT
    await brain.abort(1)                    # abort a DIFFERENT, older turn
    rest = [i async for i in live]

    assert first is not None
    assert rest, ("aborting turn 1 killed turn 2 while it was in flight -- "
                  "this abort is keyed by session, not by turn")


@pytest.mark.asyncio
async def test_warm_completes(brain):
    """D-10 in its third home. The router is a model on a shared GPU whose
    cold load is measured at 15.85 s for the SMALLEST model on this machine
    (plan.md 4.4); paying that on the first thing the owner says is the
    defect this method exists to prevent. The fake has nothing to warm and
    must still answer, because boot calls this unconditionally.
    """
    await brain.warm()


@pytest.mark.asyncio
async def test_warm_is_safe_to_call_twice(brain):
    """Boot warms once, but a supervised restart warms again (the failure
    policy in docs/reflex-serving.md section 6 re-runs the boot check after
    a successful respawn). A warm that only works the first time would turn
    recovery into a second outage.
    """
    await brain.warm()
    await brain.warm()
