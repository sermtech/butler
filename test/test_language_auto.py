"""Butler picks the voice to match the language being spoken.

AC-207..AC-215, `docs/language-auto.md`. What this file encodes, and what
each claim would catch:

* the turn's language comes from the owner's WORDS, not from a setting
* it LATCHES, so a one-word answer cannot flip the voice mid-conversation
* a voice the owner picked by hand wins, except under `auto`, where the
  control is DIMMED rather than silently overruled
* `auto` never pins the recognizer
* Butler's own eight fixed lines are said in the turn's language

The assertions are on what the ENGINE was asked for and what crossed the
WIRE, never on `s.language` alone: a hub that decided the right language and
then synthesized with the wrong voice satisfies every field-level check, and
it is the exact defect AC-212 is named after.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub                   # noqa: E402
from test_typed_turn import speak_at                            # noqa: E402
from daemon import dialogue, ports                              # noqa: E402
from daemon.settings import AUTO_LANGUAGE, Settings             # noqa: E402

# Long enough for the detector to commit, and in the two languages the fake
# speech engine has voices for. Short forms live in their own cases: what
# separates them is the whole design, so they are never mixed into a fixture.
ENGLISH = "Run the tests again and tell me which ones failed."
PORTUGUESE = "Voce pode rodar os testes e me dizer quantos falharam?"


def auto_hub(tmp_path):
    prefs = Settings(tmp_path / "s.json")
    prefs.set("language", AUTO_LANGUAGE)
    return make_hub(tmp_path, settings=prefs)


def voices_of(language: str, hub) -> set[str]:
    return set(ports.voice_ids(hub.tts.info, language))


def speak_starts(s) -> list[dict]:
    return [json.loads(m) for m in s.ws.sent
            if isinstance(m, str) and json.loads(m)["type"] == "speak_start"]


# --------------------------------------------------------------------------- #
# AC-209: the words decide, and the engine is asked accordingly
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("said,want", [(ENGLISH, "en"), (PORTUGUESE, "pt")])
async def test_the_turn_is_answered_in_the_language_it_was_asked_in(
        tmp_path, said, want):
    """AC-209 and AC-212, asserted at the ENGINE and on the WIRE.

    Both halves, because either alone passes against the defect this feature
    fixes: a daemon that announced the right voice and synthesized with a
    blank one satisfies the wire check, and a daemon that never sent
    `speak_start` satisfies the engine check.
    """
    hub = auto_hub(tmp_path)
    s = attach_session(hub)
    await speak_at(hub, s, said)

    wanted = voices_of(want, hub)
    assert wanted, f"the fake engine has no {want!r} voice to choose"
    assert hub.tts.voices and set(hub.tts.voices) <= wanted, (
        f"{said!r} was synthesized with {hub.tts.voices!r}, which is not one "
        f"of the {want!r} voices {sorted(wanted)}")
    starts = speak_starts(s)
    assert starts, "no speak_start was sent"
    assert starts[0]["voice"] in wanted, (
        f"speak_start announced {starts[0]['voice']!r} for a {want!r} turn")
    assert set(hub.tts.voices) == {starts[0]["voice"]}, (
        f"the wire said {starts[0]['voice']!r} and the room heard "
        f"{hub.tts.voices!r}")


@pytest.mark.asyncio
async def test_the_voice_follows_the_language_ACROSS_turns(tmp_path):
    """The decision is per TURN and it is not sticky when the words change.

    One session, two languages, in order. A hub that decided once and cached
    passes every single-turn assertion in this file, which is why this runs
    two turns -- `test/AGENTS.md`'s "a conversation is not a turn".
    """
    hub = auto_hub(tmp_path)
    s = attach_session(hub)

    await speak_at(hub, s, ENGLISH)
    first = list(hub.tts.voices)
    hub.tts.voices.clear()
    await speak_at(hub, s, PORTUGUESE)
    second = list(hub.tts.voices)

    assert set(first) <= voices_of("en", hub), first
    assert set(second) <= voices_of("pt", hub), second
    assert set(first).isdisjoint(second), (
        f"the same voice answered both languages: {first!r} then {second!r}")


# --------------------------------------------------------------------------- #
# AC-210: the LATCH
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_one_word_answer_keeps_the_language_it_was_asked_in(tmp_path):
    """The measurement that made the latch necessary, as a test.

    MEASURED 2026-09-11: "sim" classifies as `en` at 0.514 confidence, below
    `DETECT_MIN_CONFIDENCE`. So a daemon that trusted every verdict would flip
    a Portuguese conversation into an English voice on the word "yes" in
    Portuguese -- which is the failure `docs/language.md` 2 declined automatic
    detection over, and the latch is the answer to it.

    The fixture word is deliberately one the detector gets WRONG rather than
    one it merely finds short: a latch tested with a word it would have got
    right anyway is a test that cannot fail.
    """
    assert dialogue.detect_language("sim", ["en", "pt"]) is None, (
        "the detector now commits on 'sim'; this test's premise is gone and "
        "the fixture needs re-measuring, not the assertion relaxing")

    hub = auto_hub(tmp_path)
    s = attach_session(hub)
    await speak_at(hub, s, PORTUGUESE)
    hub.tts.voices.clear()

    await speak_at(hub, s, "sim")
    assert hub.tts.voices and set(hub.tts.voices) <= voices_of("pt", hub), (
        f"a one-word answer moved a Portuguese conversation to "
        f"{hub.tts.voices!r}")
    assert s.language == "pt"


@pytest.mark.asyncio
async def test_the_FIRST_turn_of_a_conversation_has_a_language(tmp_path):
    """An abstention with nothing latched still has to answer something.

    The engine's own default voice decides it, so the fixed lines match the
    room rather than a constant. None would mean English, which is right by
    accident here and wrong the day the engine's default is not English.
    """
    hub = auto_hub(tmp_path)
    s = attach_session(hub)
    assert s.language is None

    await speak_at(hub, s, "sim")
    default = str(hub.tts.info["voice"])
    assert s.language == hub._voice_language(default), (
        f"the first turn latched {s.language!r}, which is not what the "
        f"engine's own default voice {default!r} speaks")


# --------------------------------------------------------------------------- #
# AC-213: a voice the owner named, and the control that says so
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_under_auto_a_stored_voice_does_NOT_win(tmp_path):
    """The setting is overruled, and the page is told so in the same breath.

    This is the pair that has to move together. The owner's live daemon has a
    Portuguese voice stored; under `auto` it must stop deciding, or turning
    the feature on answers every English turn in a Portuguese accent. And a
    setting that is silently overruled is AC-7b, so the dimming is asserted
    HERE, beside the overruling, rather than in the settings file -- if they
    were separated, one could ship without the other and the failure would be
    a control that looks live and is not.
    """
    prefs = Settings(tmp_path / "s.json")
    pt_voice = ports.voice_ids(
        ports.build(ports.TTSS, "fake").info, "pt")[0]
    prefs.set("voice", pt_voice)
    prefs.set("language", AUTO_LANGUAGE)
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)

    await speak_at(hub, s, ENGLISH)
    assert hub.tts.voices and set(hub.tts.voices) <= voices_of("en", hub), (
        f"a stored {pt_voice!r} answered an English turn: {hub.tts.voices!r}")

    inactive = {row["key"]: row["inactive"]
                for row in prefs.describe()["settings"]}
    assert inactive["voice"], (
        "`voice` stopped deciding and the page still draws it as live")
    assert prefs["voice"] == pt_voice, (
        "the stored voice was destroyed rather than dimmed; turning auto off "
        "must give the owner their choice back")


def test_off_auto_the_voice_is_live_again(tmp_path):
    """The dimming is conditional, asserted both ways.

    A rule that marks a control inert unconditionally passes the test above
    and is the same lie pointing the other way (AC-66).
    """
    prefs = Settings(tmp_path / "s.json")
    assert not {r["key"]: r["inactive"]
                for r in prefs.describe()["settings"]}["voice"]
    prefs.set("language", AUTO_LANGUAGE)
    assert {r["key"]: r["inactive"]
            for r in prefs.describe()["settings"]}["voice"]
    prefs.set("language", "pt-BR")
    assert not {r["key"]: r["inactive"]
                for r in prefs.describe()["settings"]}["voice"]


@pytest.mark.asyncio
async def test_a_pinned_language_is_not_second_guessed(tmp_path):
    """AC-209's other half: with a locale set, no detector runs.

    Said in Portuguese, with the setting pinned to English: the setting wins.
    A classifier overruling an explicit choice is the control-that-lies
    failure aimed at its own control.
    """
    prefs = Settings(tmp_path / "s.json")
    prefs.set("language", "en-US")
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)

    await speak_at(hub, s, PORTUGUESE)
    assert s.language == "en-US"
    assert hub.tts.voices and set(hub.tts.voices) <= voices_of("en", hub), (
        f"a pinned language was overruled by the detector: {hub.tts.voices!r}")


# --------------------------------------------------------------------------- #
# AC-215: the recognizer is left alone
# --------------------------------------------------------------------------- #
def test_auto_never_pins_the_recognizer(tmp_path):
    """AC-215, and the measurement behind it.

    `auto` is not a locale and the engine REJECTS the literal string
    (`unsupported language (status 10)`, measured). More than that, pinning
    anything is worse: unpinned decoded English perfectly and Portuguese
    better than either pin, while the wrong pin LOST A SENTENCE
    (`test/manual/spike_asr_unpinned.py`, 2026-09-11).

    Asserted on `_asr_config()` -- the one place a preference reaches the
    recognizer -- because that is what every caller goes through: a live
    turn, a dictation and the ambient back-fill.
    """
    hub = auto_hub(tmp_path)

    # THE CASE WITH TEETH, and the harness is why it is first: an adapter that
    # reports NO languages is deliberately never second-guessed
    # (`_speakable_language`), so it is the one shape where a missing `auto`
    # check lets the literal string through to the port. Against an adapter
    # that DOES list its locales, `auto` is not in the list and the existing
    # unsupported-language branch answers None by accident -- so a test
    # written only that way passes against the check being deleted. Found by
    # `test/manual/mutate_language_auto.py` on 2026-09-11.
    hub.asr = type("Silent", (), {"info": {}})()
    assert hub._asr_config().language is None, (
        f"auto reached the recognizer as {hub._asr_config().language!r}; it "
        f"is not a locale and the engine refuses it outright")

    hub.asr = type("Multi", (), {
        "info": {"languages": ["en-US", "pt-BR"]}})()
    assert hub._asr_config().language is None, (
        f"auto reached a multilingual recognizer as "
        f"{hub._asr_config().language!r}")

    # And a model that names its languages must not have its FIRST one
    # substituted for a blank, which is what used to happen and is how `auto`
    # would have pinned a Portuguese speaker to English.
    hub.settings.set("language", "")
    assert hub._asr_config().language is None, (
        "a blank language is being substituted with the model's first "
        "locale again; that guard was deleted on measured evidence")


# --------------------------------------------------------------------------- #
# AC-211: Butler's own lines
# --------------------------------------------------------------------------- #
def test_every_translated_line_is_still_a_line_something_says():
    """The gate that keeps a reworded line from orphaning its translation.

    `dialogue.LINES` is keyed by the ENGLISH LINE, which is what makes English
    the fallback by construction -- and the cost of that choice is exactly
    this: change the wording at a call site and the translation silently stops
    matching, so every Portuguese conversation quietly reverts to English with
    nothing failing. This is the thing that fails.
    """
    from daemon import hub as hub_mod
    from daemon.adapters.brain_endpoint import EndpointConverse
    from daemon.adapters.brain_harness import HarnessConverse

    live = {
        hub_mod.Hub.ASR_FAILED, hub_mod.Hub.NEW_CONVERSATION,
        hub_mod.Hub.WORKING_IN,
        HarnessConverse.NARRATION, HarnessConverse.FAILED,
        HarnessConverse.NOTHING_CAME_BACK,
        EndpointConverse.OUT_OF_ROOM, EndpointConverse.NOTHING_CAME_BACK,
    }
    orphans = sorted(set(dialogue.LINES) - live)
    assert orphans == [], (
        f"{orphans} are translated and nothing says them any more; a reworded "
        f"line takes its translation with it")
    assert len(dialogue.LINES) == 8, (
        f"the eight fixed lines are {len(dialogue.LINES)}; a new one needs a "
        f"translation or a written reason for staying English")


def test_a_translation_keeps_the_fields_its_english_has():
    """`.format` happens AFTER translation, so a dropped `{name}` raises.

    One line carries a placeholder today. A translation that lost it would
    fail inside a turn, at the moment the owner switched projects -- which is
    a spoken failure for a missing brace.
    """
    import string

    for english, translations in dialogue.LINES.items():
        want = {f for _, f, _, _ in string.Formatter().parse(english) if f}
        for lang, text in translations.items():
            have = {f for _, f, _, _ in string.Formatter().parse(text) if f}
            assert have == want, (
                f"the {lang!r} translation of {english!r} has fields {have} "
                f"and the English has {want}")


def test_an_untranslated_language_gets_the_english_line():
    """English is the fallback, said out loud rather than discovered.

    Three shapes that must all answer English: no language at all, a language
    with no table, and a line with no entry. A `KeyError` on any of them would
    be a failed reply for a missing string (D-1).
    """
    line = "Working on it."
    assert dialogue.line(line, None) == line
    assert dialogue.line(line, "de-DE") == line
    assert dialogue.line("something nobody translated", "pt") == (
        "something nobody translated")
    # A locale and a bare subtag are the same answer: what differs between
    # pt-BR and pt-PT is a voice, not a sentence.
    assert dialogue.line(line, "pt") == dialogue.line(line, "pt-BR") != line


@pytest.mark.asyncio
async def test_a_fixed_line_is_spoken_in_the_turns_language(tmp_path):
    """AC-211 on the CALLER (D-18), and through a real turn.

    `dialogue.line` being correct proves nothing about whether the hub calls
    it. The recognizer failure is the one fixed line a hub test can reach
    without a harness, and it is also the one that most needs to be
    understood -- it is what the owner hears when Butler could not hear them.
    """
    hub = auto_hub(tmp_path)
    s = attach_session(hub)
    await speak_at(hub, s, PORTUGUESE)

    turn = hub._begin_turn(s)
    await hub._on_final(s, turn, ports.Final(text="", failed=True), "silence")

    replies = [json.loads(m) for m in s.ws.sent
               if isinstance(m, str) and json.loads(m)["type"] == "reply"]
    said = [r["text"] for r in replies]
    want = dialogue.line(hub.ASR_FAILED, "pt")
    assert want != hub.ASR_FAILED, "the Portuguese line is missing"
    assert want in said, (
        f"the recognizer failure was announced as {said!r} in a Portuguese "
        f"conversation")


@pytest.mark.asyncio
async def test_the_adapters_fixed_lines_read_the_turns_language(fake_openai):
    """The other five lines live in adapters and read `ctx.language`.

    Driven through the real adapter against the recording server, because
    that is where the field arrives: a brain that ignored `ctx.language` is
    invisible to every hub test, since the hub's own three lines would still
    be right.

    Both endpoint lines, in one turn each, because they are reached by
    different branches -- an empty answer under a cap and an empty answer
    without one -- and an adapter can easily translate at one of them.
    """
    from conftest import StubResolver
    from daemon.adapters.brain_endpoint import EndpointConverse

    ctx = ports.TurnContext(turn_id=1, mode="secretary", language="pt-BR")

    fake_openai.answer("", finish_reason="length", reasoning="thinking...")
    brain = EndpointConverse(StubResolver(fake_openai.base_url,
                                          caps={"reply": 200}))
    said = [i.text async for i in brain.turn("oi", ctx)
            if isinstance(i, ports.Say)]
    want = dialogue.line(EndpointConverse.OUT_OF_ROOM, "pt")
    assert want != EndpointConverse.OUT_OF_ROOM, "the Portuguese line is missing"
    assert said == [want], f"the endpoint brain said {said!r} out of room"

    fake_openai.answer("", finish_reason="stop")
    brain = EndpointConverse(StubResolver(fake_openai.base_url))
    said = [i.text async for i in brain.turn("oi", ctx)
            if isinstance(i, ports.Say)]
    want = dialogue.line(EndpointConverse.NOTHING_CAME_BACK, "pt")
    assert want != EndpointConverse.NOTHING_CAME_BACK
    assert said == [want], f"the endpoint brain said {said!r} on an empty reply"
