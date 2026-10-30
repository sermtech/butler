"""The three turn-ending policies. plan.md 4.2.

Each mode answers "when should it stop listening" differently, and the
whole reason there are three is that the wrong answer is unpleasant in a
different way each time: too eager cuts you off, too patient makes every
quick question slow, and a timer of any kind makes composing a long
instruction impossible.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon.endpointing import (PATIENCE, EndWordEnd, SilenceEnd,  # noqa: E402
                                SmartTurnEnd, TurnState, build)


def state(**kw) -> TurnState:
    base = dict(silence_ms=0.0, speech_ms=1000.0, elapsed_ms=2000.0,
                voiced=True, transcript="", utterance=b"")
    base.update(kw)
    return TurnState(**base)


# --------------------------------------------------------------------------- #
# patient
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_silence_policy_waits_its_preset():
    p = SilenceEnd("balanced")
    assert await p.should_finalize(state(silence_ms=500)) is None
    assert await p.should_finalize(state(silence_ms=650)) == "silence"


@pytest.mark.asyncio
async def test_silence_policy_never_ends_a_turn_that_never_started():
    """Not voiced means nobody has spoken; ending here is the no-speech
    timeout's job, and it lives in the pipeline so every mode gets it."""
    p = SilenceEnd("quick")
    assert await p.should_finalize(state(voiced=False, silence_ms=9999)) is None


@pytest.mark.parametrize("name", sorted(PATIENCE))
def test_every_patience_preset_is_usable(name):
    p = SilenceEnd(name)
    assert p.silence_ms > 0 and p.max_wait_ms > p.silence_ms


def test_an_unknown_patience_is_refused_loudly():
    """A typo in a setting must not silently select a default -- the user
    would conclude the setting does nothing."""
    with pytest.raises(ValueError):
        SilenceEnd("leisurely")


# --------------------------------------------------------------------------- #
# smart
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_smart_policy_does_not_ask_before_the_first_pause():
    """Asking a model on every frame wastes a child process round trip; the
    pause is the cue to ask."""
    asked = []

    async def endpointer(pcm):
        asked.append(pcm)
        return 0.9

    p = SmartTurnEnd(endpointer, first_check_ms=280)
    assert await p.should_finalize(state(silence_ms=100)) is None
    assert asked == []


@pytest.mark.asyncio
async def test_smart_policy_ends_when_the_model_is_confident():
    async def endpointer(pcm):
        return 0.87

    p = SmartTurnEnd(endpointer, threshold=0.6)
    assert await p.should_finalize(state(silence_ms=300)) == "endpoint"


@pytest.mark.asyncio
async def test_smart_policy_keeps_listening_when_the_model_says_keep_going():
    """The whole point: a thinking pause is not the end of a turn."""
    async def endpointer(pcm):
        return 0.12

    p = SmartTurnEnd(endpointer, threshold=0.6)
    assert await p.should_finalize(state(silence_ms=300)) is None
    assert await p.should_finalize(state(silence_ms=900)) is None


@pytest.mark.asyncio
async def test_smart_policy_fails_open_at_its_cap():
    """A model that never says done cannot hold the turn forever."""
    async def never(pcm):
        return 0.0

    p = SmartTurnEnd(never, max_wait_ms=6000)
    assert await p.should_finalize(state(silence_ms=6100)) == "hard_cap"


@pytest.mark.asyncio
async def test_smart_policy_throttles_its_rechecks():
    """Each check is a child-process round trip; asking every poll would
    swamp it."""
    calls = []

    async def endpointer(pcm):
        calls.append(1)
        return 0.1

    ticks = iter([0.0, 0.1, 0.2, 5.0])
    p = SmartTurnEnd(endpointer, recheck_ms=450, clock=lambda: next(ticks))
    for _ in range(3):
        await p.should_finalize(state(silence_ms=400))
    assert len(calls) == 2, f"expected 2 checks with throttling, got {len(calls)}"


# --------------------------------------------------------------------------- #
# endword
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_end_word_mode_ignores_silence_entirely():
    """The point of the mode. Think for as long as you like."""
    p = EndWordEnd()
    assert await p.should_finalize(
        state(silence_ms=60_000, transcript="i am still thinking")) is None


@pytest.mark.asyncio
async def test_end_word_submits_after_the_phrase_and_a_pause():
    p = EndWordEnd(pause_ms=600)
    said = state(transcript="add a retry to the fetch helper send it",
                 silence_ms=100)
    assert await p.should_finalize(said) is None, "submitted before the pause"

    said = state(transcript="add a retry to the fetch helper send it",
                 silence_ms=700)
    assert await p.should_finalize(said) == "end_word"


@pytest.mark.asyncio
async def test_the_phrase_mid_sentence_does_not_submit():
    """"send it back to the old version" must keep listening.

    This is the failure that makes a naive keyword trigger unusable: the
    phrase only counts at the END of what has been said so far.
    """
    p = EndWordEnd()
    mid = state(transcript="send it back to the previous version",
                silence_ms=5000)
    assert await p.should_finalize(mid) is None


@pytest.mark.asyncio
async def test_trailing_punctuation_does_not_hide_the_phrase():
    p = EndWordEnd()
    assert await p.should_finalize(
        state(transcript="run the tests. send it.", silence_ms=800)
    ) == "end_word"


@pytest.mark.asyncio
async def test_a_trailing_comma_does_not_hide_the_phrase():
    """The pattern stripped `.!?` and NOTHING else (2026-09-15).

    A recogniser punctuates with whatever fits the sentence it is building,
    so a phrase at the end of a spoken clause arrives as `pode ir,` at least
    as often as `pode ir.` -- and the comma made the end phrase unmatchable,
    which is a turn that runs to the five-minute ceiling.
    """
    p = EndWordEnd(phrases=("pode ir",))
    for ending in ("pronto pode ir,", "pronto pode ir;", "pronto pode ir:",
                   "pronto pode ir...", 'pronto "pode ir"', "pronto pode ir\u201d"):
        assert await p.should_finalize(
            state(transcript=ending, silence_ms=800)) == "end_word", ending


@pytest.mark.asyncio
async def test_a_misheard_end_phrase_still_ends_the_turn():
    """Every string here is VERBATIM from the laptop's own voice trace.

    2026-09-15. Five long-form turns on that machine have no `turn_end` at
    all, and these are the committed tails that were live when they should
    have closed: the recogniser, decoding Portuguese, spelled the end phrase
    phonetically (`send it` -> `Sandit`, `Sanded`, `Sended`) and the literal
    match could never fire. The same speaker got the literal spelling on
    other turns, which is what made this look random.
    """
    p = EndWordEnd()
    committed = ("eu forma o portugues Sandit",
                 "salva em PDF no meu desktop Sended",
                 "faz o flyer para mim sand it",
                 "manda uma ideia aqui Sanded")
    for tail in committed:
        assert await p.should_finalize(
            state(transcript=tail, silence_ms=800)) == "end_word", tail


def test_a_misheard_phrase_is_stripped_from_what_the_brain_sees():
    """The phrase is control in every spelling. Left on the instruction, a
    coding agent is handed a word nobody wrote."""
    p = EndWordEnd()
    assert p.strip("faz o flyer para mim Sandit") == "faz o flyer para mim"
    assert p.strip("salva em PDF no meu desktop Sended") == \
        "salva em PDF no meu desktop"


@pytest.mark.asyncio
async def test_a_misheard_phrase_mid_sentence_does_not_submit():
    """The mishearing changes the SPELLING, never the rule: it still only
    counts at the end. `Sandit uma ideia aqui entao` must keep listening --
    that tail is also verbatim from the trace, and it did keep listening."""
    p = EndWordEnd()
    assert await p.should_finalize(state(
        transcript="Sandit uma ideia aqui entao", silence_ms=5000)) is None


def test_the_end_phrase_is_stripped_from_what_the_brain_sees():
    """It was addressed to Butler, not part of the instruction. Leaving it
    in means the brain reads "...and run the tests over"."""
    p = EndWordEnd()
    assert p.strip("add a retry and run the tests. send it.") == \
        "add a retry and run the tests."
    assert p.strip("do the thing over") == "do the thing"


def test_stripping_leaves_something_sane_for_a_bare_end_phrase():
    """Saying only the end word is an empty instruction, not a crash."""
    p = EndWordEnd()
    assert p.strip("send it") == ""


def test_the_end_phrase_is_stripped_WHATEVER_ITS_CASE():
    """Found in the owner's own chat records, 2026-08-20.

    `_heard_end_phrase` lowercases the transcript before matching and `strip`
    did not, and the pattern carried no IGNORECASE -- so a real transcript
    ENDED the turn and kept its control phrase. A recogniser capitalises after
    sentence punctuation, so "Send it" rather than "send it" is the normal
    case and every test above used the abnormal one.

    What it costs: the phrase reaches the brain, and in harness mode reaches a
    full-capability coding agent, as part of the instruction. Real examples,
    stored verbatim in `state/chats/`: "Can you hear me now? Send it" and
    "Who are you? Send it".
    """
    p = EndWordEnd()
    assert p.strip("Can you hear me now? Send it") == "Can you hear me now?"
    assert p.strip("Who are you? Send it") == "Who are you?"
    assert p.strip("Add a retry and run the tests. SEND IT.") == \
        "Add a retry and run the tests."
    assert p.strip("Ship the branch Over To You") == "Ship the branch"


def test_the_longest_matching_phrase_wins():
    """"over to you" must not leave a dangling "to you"."""
    p = EndWordEnd()
    assert p.strip("ship the branch over to you") == "ship the branch"


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
def test_build_selects_by_name():
    assert build("patient").name == "patient"
    assert build("endword").name == "endword"


def test_smart_without_an_endpointer_falls_back_loudly(capsys):
    """Silently running a different policy than the setting names is how a
    user concludes the setting does nothing."""
    policy = build("smart", endpointer=None)
    assert policy.name == "patient"
    assert "no endpointer" in capsys.readouterr().out


def test_an_unknown_mode_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        build("whenever")


# --------------------------------------------------------------------------- #
# the long-form ceiling
# --------------------------------------------------------------------------- #
def test_end_word_mode_raises_its_own_ceiling():
    """Long-form turns must not be cut at the conversational ceiling.

    The mode exists so someone can stop, think and resume. END_WORD_MAX_MS
    was defined and read by nothing for a while, so a long-form turn hit the
    120 s safety net instead -- the mode quietly failing at the one thing it
    is for, and failing in a way no test noticed.
    """
    from daemon.endpointing import END_WORD_MAX_MS

    assert EndWordEnd().max_turn_ms == END_WORD_MAX_MS
    assert END_WORD_MAX_MS > 120_000, (
        "the long-form ceiling must exceed the conversational one")


def test_the_timed_policies_do_not_raise_the_ceiling():
    """Only long-form gets the longer rope. A smart or patient turn running
    for minutes is a stuck turn, not a considered one."""
    assert getattr(SilenceEnd("balanced"), "max_turn_ms", 0) == 0
    assert getattr(SmartTurnEnd(lambda _p: None), "max_turn_ms", 0) == 0
