"""One live child per project, designed for N, with the idle-grace reaper.

`docs/filing-and-pacing.md` item 1, section 1.6, phase 3 -- the engine half
only. The owner's ruling: "only one agent/active chat per project at once,
but up to the max allowed agents in settings which would be on different
projects... for now, and for other users, it is safer to be ready for it,
but only allow 1 per project for now." D5 settles both new numbers
(`project_agent_limit`, `project_idle_grace_minutes`) as ONE global value
each, matching `harness_agents_max`'s own precedent -- never a per-project
field on `ShortcutStore.Shortcut`.

Phase 5's confirm-to-stop mechanism (for a BUSY collision) is NOT built here.
`_Host._enforce_project_limit` resolves the two cases Phase 3 alone can
answer: a BUSY collision is refused, exactly like the global cap, because
nothing here can stop one gracefully yet; an IDLE collision -- whether still
within its grace window or already past it -- is disposed and the turn
proceeds with no confirm at all (D1's ruling, recommended option A).

**A second conversation's turn is deliberately never run CONCURRENTLY with
the first's here.** `HarnessConverse.turn()`'s own reentrancy guard --
`self._turn_id`/`self._chat_id` are scalars on that ONE instance, shared by
every conversation -- disposes whichever turn "never released the harness
child" the moment a second one starts while the first's generator is still
open, which is a real, PRE-EXISTING limitation (its own docstring: "in
production this is unreachable... `hub._reply` closes the generator through
`_let_go` on every exit"). That is orthogonal to this feature and not
something these tests route around: "BUSY" is produced here the same way a
real child looks busy between two turns -- spawned via `_Host.ensure()`
directly, with `_idle_since` never touched, exactly the state a child is in
the instant after `ensure()` and before its own `_answer`/`_give_up` ever
runs.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver                                # noqa: E402
from daemon import butlerd                                       # noqa: E402
from daemon.adapters.brain_harness import CapReached, HarnessBrain  # noqa: E402
from daemon.adapters.harness_fake import FakeHarness              # noqa: E402
from daemon.chats import ChatStore                                # noqa: E402
from daemon.ports import Say                                    # noqa: E402

from test_parallel_agents import _brain, _prefs, _turn           # noqa: E402


def _said(intents) -> list[str]:
    return [i.text for i in intents if isinstance(i, Say) and i.spoken]


# --------------------------------------------------------------------------- #
# A BUSY collision is refused; an IDLE one is reaped instead (D1=A)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_busy_project_refuses_a_second_chat_and_an_idle_one_is_reaped(
        tmp_path, fake_openai):
    """The two branches `_enforce_project_limit` can resolve without phase
    5's confirm mechanism, in the order the owner would actually hit them:
    first while the colliding child is genuinely working (no `_idle_since`
    at all), then again once it has finished and gone idle.
    """
    prefs = _prefs(tmp_path, project_agent_limit=1)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host

    first = chats.current()
    await host.ensure(first, await host.plan(first))
    assert host.live() == 1
    # `first` has never finished a turn through `HarnessConverse` -- exactly
    # the shape of a child genuinely BUSY doing real work (no `_idle_since`
    # entry at all, which is the ONLY thing `_enforce_project_limit` reads
    # to tell busy from idle).

    await brain.new_conversation()
    said = _said(await _turn(brain, "second project, first try", turn_id=2))
    assert len(agents.all) == 1, (
        f"a second child was spawned into a project already BUSY, with no "
        f"confirm mechanism to have allowed it: {agents.all}")
    assert any("agent" in s.lower() for s in said), (
        f"the refusal did not name the collision: {said}")

    # The first child "finishes": simulated directly, mirroring
    # `HarnessConverse._answer`'s own one line rather than driving a whole
    # turn through the single-flight generator above.
    host.note_idle(first["id"])

    # Second attempt: the same project, now IDLE and well within its
    # (default 60-minute) grace window. D1's ruling (option A) says this is
    # disposed and the turn proceeds with NO confirm -- the owner's own
    # words say the cost is a respawn, never a lost question.
    said2 = _said(await _turn(brain, "second project, second try",
                              turn_id=3))
    assert len(agents.all) == 2, (
        f"the idle collision was refused forever instead of being reaped: "
        f"{agents.all}")
    assert agents.all[0].disposals == 1, (
        f"the first (idle) child was not disposed to make room for the "
        f"second: {agents.all[0].disposals} disposal(s)")
    assert said2, "the second conversation's turn produced no answer at all"


# --------------------------------------------------------------------------- #
# The idle grace, simulated rather than slept (test/AGENTS.md)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_idle_grace_elapsing_frees_the_slot_with_no_owner_action(
        tmp_path, fake_openai):
    """`_idle_since` is aged into the past directly -- never by sleeping the
    real 30-minute duration. This is the EXPIRED branch of `_enforce_
    project_limit`, distinct from the D1 branch tested above: the stale
    child is reaped OUTRIGHT, as a lazy hygiene action, whether or not the
    count would otherwise have been at the limit at all.

    `project_agent_limit=2` on purpose: with ONE colliding child, a count
    of 1 is already under a limit of 2, so the D1 "pick the oldest idle
    collision and sacrifice it" branch would never fire here -- if this
    test passed at limit=1 it could not tell "reaped because expired" from
    "disposed because the count needed it", since both branches would
    dispose the SAME one collision either way.
    """
    prefs = _prefs(tmp_path, project_agent_limit=2,
                  project_idle_grace_minutes=30)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host

    first = chats.current()
    await host.ensure(first, await host.plan(first))
    assert host.live() == 1

    # The turn "ending": mark it idle, then age that mark past the
    # 30-minute grace.
    host.note_idle(first["id"])
    host._idle_since[first["id"]] -= 31 * 60

    await brain.new_conversation()
    second = chats.current()
    await host.ensure(second, await host.plan(second))

    assert host.live() == 1, (
        f"the expired idle child was not reaped by the lazy check, so the "
        f"project now holds two children: {host.live()}")
    assert host.child(first["id"]) is None, (
        "the expired child is still registered under its old chat id")
    assert host.child(second["id"]) is not None, (
        "the new chat's own child was never spawned once the slot freed")
    assert agents.all[0].disposals == 1, (
        f"the expired child was not actually disposed, only forgotten: "
        f"{agents.all[0].disposals} disposal(s)")


# --------------------------------------------------------------------------- #
# Different projects never collide (the owner's own "on different projects")
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_DIFFERENT_projects_both_hold_a_child_at_once(
        tmp_path, fake_openai):
    """The limit is PER PROJECT, never global -- the owner's own words:
    "up to the max allowed agents in settings which would be on different
    projects." `_enforce_project_limit` must never compare across
    directories, or a limit of 1 would silently become "one agent, ever."
    """
    other = tmp_path / "other-project"
    other.mkdir()
    prefs = _prefs(tmp_path, projects=(str(tmp_path), str(other)),
                  project_agent_limit=1)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host

    first = chats.current()
    await host.ensure(first, await host.plan(first))

    await brain.new_conversation(str(other))
    second = chats.current()
    await host.ensure(second, await host.plan(second))

    assert host.live() == 2, (
        f"two DIFFERENT projects collided under a limit meant to bound ONE "
        f"project at a time: {host.live()} live")
    assert agents.all[0].disposals == 0 and agents.all[1].disposals == 0


# --------------------------------------------------------------------------- #
# THE CONFIRMED RACE: a reap disposing an idle child races that same
# conversation's own turn starting concurrently (D-12 correction, 2026-09-17)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_reap_racing_a_self_reuse_never_exceeds_the_project_limit(
        tmp_path, fake_openai):
    """`_enforce_project_limit` used to decide "this project has room" by
    reading `self._children` -- which `dispose()` had already POPPED the
    outgoing occupant from, synchronously, before ever awaiting that
    occupant's real teardown. Racing that eviction against the SAME chat's
    own new turn starting concurrently made both sides see the project as
    empty: chat A idle in project P; a new turn for chat B in P begins
    disposing A's child; while that dispose is suspended in teardown, A's
    own new turn runs `plan(A)`, sees no held child (already popped) and no
    collision (B has not spawned yet), and spawns; B's dispose then
    completes and B spawns too. MEASURED (pre-fix, this test): `host.live()`
    reads 2 against `project_agent_limit=1`.

    The stock `FakeHarness.dispose()` has no `await` in it at all, which is
    this suite's own diagnosis of why nothing here could ever see the
    window (test/AGENTS.md). This test gives chat A's child a `dispose()`
    that genuinely suspends, gated on an `asyncio.Event`, and races a real
    `plan()` call for A against `_enforce_project_limit`'s eviction of A on
    B's behalf. It drives `_Host` directly rather than through
    `HarnessConverse.turn()`, exactly like every other test in this file
    and for the reason stated at the top of it: `turn()`'s own reentrancy
    guard is a different mechanism from the one this race is about.

    RED without the fix (D-13): `host.live()` reads 2. GREEN with it: A's
    own concurrent `plan()` call correctly raises `CapReached` -- refused
    rather than silently duplicated -- and exactly 1 child survives.
    """
    entered_dispose = asyncio.Event()
    gate = asyncio.Event()

    class _SlowDispose(FakeHarness):
        """A's real harness: `dispose()` announces that it has genuinely
        started tearing down, then waits, so the race window this test
        targets exists at all to be raced against."""
        async def dispose(self):
            entered_dispose.set()
            await gate.wait()
            await super().dispose()

    prefs = _prefs(tmp_path, project_agent_limit=1)
    chats = ChatStore(tmp_path / "chats")
    agents: list[FakeHarness] = []

    def harness_for(_chat=None):
        h = _SlowDispose()
        agents.append(h)
        return h

    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=harness_for,
        spec_for=butlerd.harness_spec_resolver(prefs, chats,
                                               prefs.project_paths),
        chats=chats, cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])
    host = brain.host

    a = chats.current()
    chats.append(a["id"], "user", "remember 47")
    await host.ensure(a, await host.plan(a))
    assert host.live() == 1
    host.note_idle(a["id"])   # A is idle, not expired -- D1=A's own target.

    await brain.new_conversation()
    b = chats.current()

    async def b_spawns():
        spec = await host.plan(b)     # decides to evict A, then awaits it
        await host.ensure(b, spec)

    task_b = asyncio.create_task(b_spawns())
    # Deterministic, not timing-guessed (test/AGENTS.md's own rule against
    # unbounded waits, honoured here by bounding this one): wait until B's
    # eviction of A has genuinely entered A's dispose() and is suspended
    # mid-teardown, which is the exact window the bug lived in.
    await asyncio.wait_for(entered_dispose.wait(), 5.0)

    # A's OWN new turn, concurrently, while A's old child is being torn
    # down on B's behalf -- as if a second device still had A open and the
    # owner asked it something (a real `POST /api/session/open` would do
    # this before any such turn; `new_conversation()` above evicted A from
    # the store's registry, and a session reference may only be minted into
    # a conversation the store is holding). Written to be robust to either
    # outcome: the pre-fix code lets this spawn a SECOND child; the fix
    # refuses it.
    chats.open(a["id"])
    try:
        spec_a2 = await host.plan(a)
    except CapReached:
        pass
    else:
        await host.ensure(a, spec_a2)

    gate.set()
    await asyncio.wait_for(task_b, 5.0)

    assert host.live() <= 1, (
        f"the project's own limit of 1 was violated by a reap racing a "
        f"self-reuse: {host.live()} live children ({list(host._children)})")


# --------------------------------------------------------------------------- #
# THE IDLE DISPOSE IS NO LONGER SILENT (defect 2)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_reaped_conversation_gets_a_note_it_can_read_later(
        tmp_path, fake_openai):
    """The D1=A eviction used to leave NO signal at all -- no log line, no
    wire frame -- so a device reopening the reaped conversation paid an
    unexplained 1.5-2.3s respawn on its next message with nothing saying
    why. `_note_reaped` writes a line into that conversation's OWN
    transcript, `record_note`'s own shape (AC-234), which is the one
    channel that survives a reload and needs no live listener.
    """
    prefs = _prefs(tmp_path, project_agent_limit=1)
    brain, agents, chats, _ = _brain(tmp_path, fake_openai, prefs=prefs)
    host = brain.host

    first = chats.current()
    chats.append(first["id"], "user", "remember 47")
    await host.ensure(first, await host.plan(first))
    host.note_idle(first["id"])

    await brain.new_conversation()
    second = chats.current()
    await host.ensure(second, await host.plan(second))

    assert host.live() == 1, "the idle collision should have been reaped"
    record = chats.find(first["id"])
    notes = [m["content"] for m in record["messages"] if m["role"] == "assistant"]
    assert any("paused this agent" in n for n in notes), (
        f"the reaped conversation's own transcript carries no explanation "
        f"of what happened to its agent: {notes}")
