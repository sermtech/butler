"""Retry, and correct a misheard turn. AC-97, and D-1, D-6, D-18, D-20.

Two halves of one criterion, and only ONE of them is code.

**RETRY IS NOT A MESSAGE.** Re-asking is the same words typed again, which
protocol v1 has answered since AC-162: `_typed` reads `m["text"]`, mints its
own id and reaches `_on_final`, and nothing in that path can tell a re-ask
from a first ask -- nor should it, because a re-ask IS a new question about
the same thing. So the first test here builds nothing and PINS that: a second
turn with identical text is an ordinary turn with its own id, and both are in
the record. A protocol message that duplicates one that exists is worse than
no feature, and this is the test that keeps one from being added.

**CORRECT IS ONE OPTIONAL FIELD**, `corrects`, naming the turn the new one
supersedes:

    satellite -> daemon   {"type": "typed", "text": "...", "corrects": 7}

It changes NOTHING about how the turn runs. That is the criterion rather
than an economy: the original stays in the rolling transcript and in
`chats.py` exactly as it was heard, because a correction that edited the
record would make Butler's record of what was SAID a thing that can be
rewritten (D-20). The field buys two behaviours and both are asserted here --
a reference this session was not given is REFUSED out loud and starts no turn
(D-6), and an accepted one rides back on the `final` frame so the page can
attach the pair without guessing which final belongs to its own send.

Asserted on the CALLER wherever there is a choice (D-18): every refusal is
checked by its CONSEQUENCES -- no turn minted, no brain woken, no capture
lost -- and never by the error frame alone, because a daemon that complained
and started the turn anyway satisfies every status-shaped assertion.

The helpers come from `test_typed_turn.py` rather than being copied: two
copies of an instrument drift, and a drifted instrument is a test passing for
the wrong reason (test/AGENTS.md).
"""
import asyncio
import json

import pytest

from conftest import QuietTts, attach_session, make_hub
from daemon.hub import CAPTURING, IDLE
from test_typed_turn import (frames, frames_of, speak_at, spoken_turn,
                             type_at)

# Neither string matches a rule in `brain_fake`, so both get the same generic
# two-sentence answer -- which is what makes the pair comparable. They differ
# by one word, the way a mishearing does.
MISHEARD = "open the ledger for butter"
CORRECTED = "open the ledger for butler"
LATER = "and read me the last line"


# --------------------------------------------------------------------------- #
# retry: no new message, and this test is the whole of the decision
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_retry_is_an_ordinary_typed_turn_and_needs_no_new_message(
        tmp_path):
    """AC-97's first half, built by building NOTHING.

    Re-asking is the previous text sent again. What must be true for that to
    be honest, and what this asserts: the second turn gets its OWN id (the
    daemon mints it, so a page cannot re-use the first -- D-6, AC-163), the
    brain is asked again rather than answered from anything cached, and BOTH
    asks are in the rolling transcript -- a retry that replaced the first
    would be the record editing itself, which is the failure the correction
    half of this criterion exists to prevent (D-20).

    THE TEETH ARE THE TWO IDS AND THE TWO RECORD ENTRIES. A daemon that
    silently de-duplicated identical consecutive utterances would answer the
    retry from nowhere and pass every content assertion.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await type_at(hub, s, MISHEARD)
    first = hub.brain.seen[-1][1]
    await type_at(hub, s, MISHEARD)
    again = hub.brain.seen[-1][1]

    assert hub.brain.seen == [(MISHEARD, first), (MISHEARD, again)], (
        f"a re-asked question did not reach the brain twice as two turns: "
        f"{hub.brain.seen}")
    assert again != first, (
        f"the retry re-used the first turn's id ({first}), so the daemon is "
        f"no longer the only thing minting them (D-6, AC-163)")
    assert [t for r, t in hub.history if r == "user"] == [MISHEARD, MISHEARD], (
        f"a retry did not enter the record as a second question: "
        f"{list(hub.history)} (AC-64, D-20)")
    assert s.state == IDLE and s.turn is None, (
        f"the retry did not re-arm: state={s.state!r} turn={s.turn!r} "
        f"(D-1, AC-2)")


# --------------------------------------------------------------------------- #
# correct: a NEW turn that names an old one, never an edit
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_correction_is_a_new_turn_and_the_original_stays(tmp_path):
    """AC-97's whole second half, and every clause of it is an assertion.

    "The corrected text is what enters the record and what the router sees":
    the brain is asked the CORRECTED words, and they are in the transcript.
    "The original stays visible so the correction is not a rewrite of
    history": the misheard turn is still the first thing in that transcript,
    unedited and unannotated.

    On the real `tts_fake` rather than `QuietTts`, for `test_typed_turn.py`'s
    reason: the assistant half of the record is `s.spoken`, which only audio
    that actually reached the satellite fills, so a silent double records the
    questions, records no answers, and lets a comparison pass proving nothing
    (D-13).
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)

    await speak_at(hub, s, MISHEARD)
    misheard_turn = hub.brain.seen[-1][1]

    await type_at(hub, s, CORRECTED, corrects=misheard_turn)
    correction_turn = hub.brain.seen[-1][1]

    assert correction_turn != misheard_turn, (
        f"the correction ran as turn {correction_turn}, the same turn it "
        f"claims to correct -- a correction is a NEW turn (AC-97, D-6)")
    assert hub.brain.seen == [(MISHEARD, misheard_turn),
                              (CORRECTED, correction_turn)], (
        f"the router was not handed the corrected text, unchanged and with "
        f"nothing about the reference added to it: {hub.brain.seen} (AC-97)")

    spoken_halves = [t for r, t in hub.history if r == "user"]
    assert spoken_halves == [MISHEARD, CORRECTED], (
        f"the record does not hold the mishearing AND the correction, in "
        f"that order: {list(hub.history)}. A correction that edited, "
        f"replaced or hid the original would make Butler's record of what "
        f"was said rewritable (AC-97, D-20)")
    assert hub.history[0] == ("user", MISHEARD), (
        f"the original utterance was altered by the correction: "
        f"{hub.history[0]!r} (D-20)")
    assert len([r for r, _ in hub.history if r == "assistant"]) == 2, (
        f"both turns must have answered, or the record comparison above is "
        f"comparing two half-turns: {list(hub.history)}")

    corrected_final = [m for m in frames_of(s, "final")
                       if m["turn"] == correction_turn]
    assert corrected_final and corrected_final[0].get("corrects") == \
        misheard_turn, (
        f"the correction's `final` does not name the turn it supersedes, so "
        f"the page has to GUESS which final is its own: {corrected_final} "
        f"(AC-97)")
    assert s.state == IDLE and s.turn is None, (
        f"the correction did not re-arm: state={s.state!r} turn={s.turn!r} "
        f"(D-1, AC-2)")


@pytest.mark.asyncio
async def test_an_ordinary_turn_says_it_corrects_nothing(tmp_path):
    """ONE `final` shape on the wire, whatever door the turn came in by.

    `corrects` is on every final and is null on the ones that correct
    nothing, because a key that appears only sometimes cannot be told from a
    daemon that forgot to send it -- and `test_typed_turn.py` diffs a typed
    turn's whole wire against a spoken one's, so a field on only one of them
    reddens a criterion that has nothing to do with this one.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await type_at(hub, s, CORRECTED)
    await speak_at(hub, s, LATER)

    finals = frames_of(s, "final")
    assert len(finals) == 2, f"expected one final per turn: {finals}"
    assert all("corrects" in m for m in finals), (
        f"a final left `corrects` off entirely, so the page cannot tell "
        f"'this corrects nothing' from 'this daemon does not know about "
        f"corrections': {finals}")
    assert [m["corrects"] for m in finals] == [None, None], (
        f"a turn that corrects nothing claimed to correct something: "
        f"{finals}")


@pytest.mark.asyncio
async def test_a_correction_can_be_corrected_and_the_chain_stays_in_the_record(
        tmp_path):
    """Getting it wrong twice is an ordinary Tuesday, and it must not latch.

    A correction's own turn is a turn this session was told about, so it can
    be corrected in turn -- and every one of the three utterances is still in
    the record afterwards. This is also the D-1 case for the feature: three
    terminal paths in a row, with the session live at the end.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    await speak_at(hub, s, MISHEARD)
    first = hub.brain.seen[-1][1]
    await type_at(hub, s, CORRECTED, corrects=first)
    second = hub.brain.seen[-1][1]
    await type_at(hub, s, LATER, corrects=second)
    third = hub.brain.seen[-1][1]

    assert [t for t, _ in hub.brain.seen] == [MISHEARD, CORRECTED, LATER], (
        f"the chain did not reach the brain as three separate questions: "
        f"{hub.brain.seen}")
    assert [t for r, t in hub.history if r == "user"] == [
        MISHEARD, CORRECTED, LATER], (
        f"correcting a correction dropped something from the record: "
        f"{list(hub.history)} (AC-97, D-20)")
    named = {m["turn"]: m.get("corrects") for m in frames_of(s, "final")}
    assert named == {first: None, second: first, third: second}, (
        f"the chain is not legible from the wire: {named} (AC-97)")
    assert s.state == IDLE and s.turn is None, (
        f"three corrections in a row left the session holding a turn: "
        f"state={s.state!r} turn={s.turn!r} (D-1, AC-2)")


# --------------------------------------------------------------------------- #
# fail fast: a reference the daemon cannot honour is refused, never guessed at
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    9999,                  # a turn nobody has minted
    0,                     # the daemon's ids start at 1
    -1,
    "3",                   # the right number, the wrong type
    True,                  # `bool` is an `int`; unguarded, this names turn 1
    1.0,
    [1],
    {"turn": 1},
])
@pytest.mark.asyncio
async def test_a_correction_of_a_turn_this_device_was_not_given_is_refused(
        tmp_path, bad):
    """Refused LOUDLY, never silently treated as a fresh turn (D-6).

    The daemon mints turn ids, so a page may REFERENCE one it was told about
    and may never name one it was not. Guessing here has a real cost: a
    number that happened to hit a live turn would attach the owner's
    correction to somebody else's question, and one that hit nothing would
    quietly become an ordinary turn -- the page then draws a correction that
    the daemon never agreed to.

    `True` is in the list because `bool` subclasses `int` in Python: without
    an explicit guard `{"corrects": true}` names turn 1, which is a JSON
    encoder away from correcting a turn nobody pointed at.

    Asserted by CONSEQUENCE and not by the error frame (test_shortcuts.py's
    rule): no id burned, no brain woken, nothing in the record, session
    untouched.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    # One real turn first, so the session HAS a correctable id and the
    # refusal below is about this reference rather than about an empty
    # memory -- otherwise every value in the list is refused for the same
    # uninteresting reason and the case proves nothing.
    await type_at(hub, s, MISHEARD)
    minted = hub._turn_seq
    before = list(hub.history)

    await hub._on_text(s, json.dumps({"type": "typed", "text": CORRECTED,
                                      "corrects": bad}))
    await asyncio.sleep(0)

    assert hub._turn_seq == minted, (
        f"a correction naming {bad!r} minted turn {hub._turn_seq}")
    assert [t for t, _ in hub.brain.seen] == [MISHEARD], (
        f"a correction naming {bad!r} woke the brain: {hub.brain.seen}")
    assert list(hub.history) == before, (
        f"a refused correction reached the record: {list(hub.history)}")
    assert s.turn is None and s.state == IDLE, (
        f"a refused correction left the session holding a turn: "
        f"turn={s.turn!r} state={s.state!r} (D-1)")
    assert [m["type"] for m in frames_of(s, "error")] == ["error"], (
        f"a correction naming {bad!r} was not refused out loud: {frames(s)}")


@pytest.mark.asyncio
async def test_a_correction_of_another_devices_turn_is_refused(tmp_path):
    """A turn the DAEMON minted is not a turn THIS device owns (D-6).

    THE TEETH ARE THAT THE ID IS REAL. A check written as "is this within the
    daemon's sequence" passes every case in the parametrized test above and
    fails only here, which is why this is a test of its own: the phone would
    be able to correct what the desk said, and the correction's `final` -- the
    one frame that names the pair -- goes to the phone, so the desk's page
    would never learn its turn had been superseded.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    desk = attach_session(hub, name="desk")
    phone = attach_session(hub, name="phone")
    # Past the single digits, so "the refusal names the turn" is not
    # satisfied by the string "1" appearing somewhere in an English sentence.
    hub._turn_seq = 40

    await type_at(hub, desk, MISHEARD)
    desk_turn = hub.brain.seen[-1][1]
    minted = hub._turn_seq

    await hub._on_text(phone, json.dumps({"type": "typed", "text": CORRECTED,
                                          "corrects": desk_turn}))
    await asyncio.sleep(0)

    assert hub._turn_seq == minted, (
        f"the phone corrected a turn it was never told about: the daemon "
        f"minted turn {hub._turn_seq} for it (D-6)")
    assert [t for t, _ in hub.brain.seen] == [MISHEARD], (
        f"the phone's correction of the desk's turn reached the brain: "
        f"{hub.brain.seen}")
    assert [m["type"] for m in frames_of(phone, "error")] == ["error"], (
        f"the phone's correction was not refused out loud: {frames(phone)}")
    assert str(desk_turn) in frames_of(phone, "error")[0]["message"], (
        f"the refusal does not say which turn it would not correct: "
        f"{frames_of(phone, 'error')}")


@pytest.mark.asyncio
async def test_a_refused_correction_costs_no_capture(tmp_path):
    """The reference is checked BEFORE the barge-in, like the other refusals.

    A correction Butler will not act on must not take the sentence the owner
    is half way through saying. This is `test_typed_turn.py`'s own case for
    the empty message, aimed at the new refusal -- and it is a separate test
    because the check was ADDED after the other two and could easily have
    landed on the wrong side of `_barge_in()`.
    """
    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    s = attach_session(hub)

    captured = hub._begin_turn(s)
    s.state = CAPTURING

    await hub._on_text(s, json.dumps({"type": "typed", "text": CORRECTED,
                                      "corrects": captured + 500}))
    await asyncio.sleep(0)

    assert s.turn == captured and s.state == CAPTURING, (
        f"a refused correction ended the capture in flight: turn={s.turn!r} "
        f"state={s.state!r}")


# --------------------------------------------------------------------------- #
# over the wire, where the message shape is the contract
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_correcting_a_spoken_turn_over_the_wire(satellite):
    """The field is in protocol v1, and the daemon still hears afterwards.

    Over a real socket because the SHAPE is the contract, and because the
    failure worth catching here is the quiet one: an OPTIONAL field on an
    existing message is exactly what an older daemon IGNORES. It would answer
    a perfectly good `final` with no `corrects` on it, the page would draw
    nothing, and no status anywhere would say why.

    The bogus reference is asserted by the id the NEXT turn gets: if the
    refusal had burned an id, the spoken turn after it would not be the
    correction's plus one. That is the same "assert by consequence" rule the
    hub-level cases use, over a daemon whose sequence this test cannot read.
    """
    misheard = await spoken_turn(satellite)

    await satellite.send(type="typed", text=CORRECTED, corrects=misheard)
    final = await satellite.next("final", timeout=15.0)
    assert final.get("corrects") == misheard, (
        f"the correction's final does not name the turn it supersedes -- an "
        f"ignored optional field looks exactly like this: {final}")
    assert final.get("text") == CORRECTED and final.get("reason") == "typed", (
        f"the corrected text is not what the daemon acted on: {final}")
    correction = final["turn"]
    assert correction != misheard, f"the correction re-used {misheard}"
    end = await satellite.next("speak_end", timeout=20.0)
    assert end.get("turn") == correction
    await satellite.send(type="playback_done", turn=correction)

    await satellite.send(type="typed", text=LATER, corrects=correction + 500)
    error = await satellite.next("error", timeout=10.0)
    assert str(correction + 500) in error["message"], (
        f"the refusal does not name the turn it would not correct: {error}")

    after = await spoken_turn(satellite)
    assert after == correction + 1, (
        f"the refused correction burned turn id {correction + 1}: the next "
        f"turn is {after}, so a reference the daemon would not honour still "
        f"cost the owner a turn (D-6)")
    assert len([m for m in satellite.seen if m.get("type") == "error"]) == 1, (
        f"the daemon refused something it should not have: "
        f"{[m for m in satellite.seen if m.get('type') == 'error']}")
