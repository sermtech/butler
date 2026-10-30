"""The first-segment cap, which is AC-4's speech budget, and the STRUCTURAL
whitespace the same splitter has to carry through it. plan.md 4.7.

Kokoro on CPU runs at a measured 3.5-4.6x realtime, so the length of the
FIRST segment decides whether a reply starts speaking inside the 1500 ms
budget or three seconds late. These tests exist because that rule is easy
to quietly lose in a refactor and impossible to notice by ear until the
reply that breaks it happens to be long.

The second half of the file is about the OTHER consumer of the same pieces.
Every piece is sent to the page as a `reply` frame and the page concatenates
them and re-renders the whole answer as markdown, so a newline the splitter
eats is a heading, a list or a table the page can never draw. MEASURED on the
real wire 2026-09-10, a typed turn to a coding agent:

    reply[2] '## A quick formatted reply Two paragraphs to keep it short.'

-- the level-2 heading and the sentence after it welded into one line, because
`split_sentences` collapsed all whitespace with `" ".join(text.split())`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon.speech import (FIRST_SEGMENT_MAX_WORDS,  # noqa: E402
                           LATER_SEGMENT_MAX_WORDS, drain_sentences, segments,
                           split_sentences)

# Measured on this machine, 2026-08-16 (docs/bench-tts.md). Used here only to
# document why the cap is 8 words; the tests assert the cap, not the timing.
MEASURED_MS_BY_WORDS = {5: 448, 19: 1784, 38: 3710}


def test_splits_on_sentence_boundaries():
    assert split_sentences("The tests are passing. I pushed the branch.") == [
        "The tests are passing.", "I pushed the branch."]


def test_does_not_split_common_abbreviations():
    """Catches a splitter that turns "approx. 40 files" into two segments and
    makes Butler pause mid-phrase."""
    out = split_sentences("It touched approx. 40 files. Nothing else changed.")
    assert out == ["It touched approx. 40 files.", "Nothing else changed."]


def test_short_first_sentence_is_left_alone():
    """A reply already inside the budget must not be chopped."""
    assert segments("It is twenty past four.") == ["It is twenty past four."]


def test_long_first_sentence_is_cut_at_the_cap():
    """The measured failure: a 38-word opener costs 3710 ms, which is the
    whole of AC-4 plus double. The cut is what keeps first audio near 450 ms."""
    long_open = ("I looked through the authentication module and the session "
                 "handling code and the token refresh path and found three "
                 "separate places where the expiry is compared against the "
                 "wrong clock which is almost certainly your bug.")
    out = segments(long_open)
    assert len(out[0].split()) <= FIRST_SEGMENT_MAX_WORDS, (
        f"first segment is {len(out[0].split())} words: {out[0]!r}")
    assert len(out) > 1, "the remainder must survive, not be discarded"
    # Nothing may be lost: the user must hear the whole answer.
    assert " ".join(out).split() == long_open.split()


def test_later_segments_get_the_looser_cap():
    """Both caps apply, at different sizes and for different reasons.

    An earlier version capped only the first segment. That served AC-4 but
    left AC-5 unbounded: barge-in strands whatever is in flight, Kokoro has
    no cancel route, and at ~95 ms per word an uncapped 38-word segment
    holds the next reply hostage for 3710 ms.
    """
    text = ("Done. I changed the retry logic in the fetch helper so it now "
            "backs off exponentially and gives up after five attempts "
            "instead of hanging forever on a dead endpoint.")
    out = segments(text)
    assert out[0] == "Done."
    assert all(len(s.split()) <= LATER_SEGMENT_MAX_WORDS for s in out[1:]), (
        f"a later segment exceeded the barge-in cap: "
        f"{[s for s in out[1:] if len(s.split()) > LATER_SEGMENT_MAX_WORDS]}")
    assert any(len(s.split()) > FIRST_SEGMENT_MAX_WORDS for s in out[1:]), (
        "later segments should use the LOOSER cap, not the first-audio one")


def test_cut_prefers_a_clause_boundary():
    """A cut at a comma sounds deliberate; a cut mid-phrase sounds broken.

    The comma STAYS on the head. An earlier version stripped it, which is a
    character deleted from the reply for no audible gain -- a trailing comma
    is a pause hint the phonemizer already handles, and keeping it is what
    makes "nothing is lost" a property with no exceptions carved into it.
    """
    text = ("We ran the tests again, and everything came back clean this "
            "time around.")
    out = segments(text)
    assert out[0] == "We ran the tests again,"
    assert len(out[0].split()) <= FIRST_SEGMENT_MAX_WORDS
    assert " ".join(out).split() == text.split()


def test_a_cut_at_a_clause_boundary_is_not_taken_too_early():
    """A two-word opener is worse than a slightly long one, so a mark in the
    first few words is ignored and the hard word cut is taken instead."""
    text = ("Yes, and the second thing you asked about is already handled "
            "by the existing retry path.")
    out = segments(text)
    assert out[0] == "Yes, and the second thing you asked about"
    assert " ".join(out).split() == text.split()


def test_empty_and_whitespace_produce_no_segments():
    """An empty reply must yield nothing to speak, not one empty segment that
    sends a speak_start with no audio behind it."""
    assert segments("") == []
    assert segments("   \n  ") == []


@pytest.mark.parametrize("words", [1, 7, 8, 9, 20, 60])
def test_first_segment_never_exceeds_the_cap(words):
    """Property check across lengths. Catches an off-by-one at the boundary,
    which is exactly where a hand-written cap tends to break."""
    text = " ".join(["word"] * words) + "."
    out = segments(text)
    if out:
        assert len(out[0].split()) <= FIRST_SEGMENT_MAX_WORDS


# Tokens that carry a clause mark INSIDE them. Every one of these was a word
# the splitter deleted: `_cut_at_cap` searched for a bare "," ";" or ":"
# anywhere in the head, sliced the head by CHARACTER and then resumed the
# remainder by WORD index -- so the token the slice landed inside counted as
# consumed and its second half was dropped rather than spoken.
INTERNAL_MARK_TOKENS = (
    "1,000",            # a thousand separator
    "1,234,567",        # several of them
    "12:30",            # a clock time
    "3:1",              # a ratio
    "https://example.com/guide",   # a URL's scheme colon
    "C:/Users/user/notes",          # a Windows drive letter
    "Smith,Jones",      # a list with no space after the comma
    "foo;bar",          # a shell fragment
)


@pytest.mark.parametrize("token", INTERNAL_MARK_TOKENS)
def test_a_clause_mark_inside_a_word_is_not_a_boundary(token):
    """The measured defect, one token at a time.

    MEASURED 2026-09-10 against the version before this rule: '...we sold
    1,000 units...' came back as ['...we sold 1', 'units and then...'] -- the
    "000" is not carried to the next phrase, it is GONE. A fuzz over 4000
    generated sentences lost 3810 characters and duplicated none.
    """
    text = (f"Right we sold {token} units last quarter and then the whole "
            f"market turned around on us again.")
    out = segments(text)
    assert token in " ".join(out).split(), (
        f"{token!r} was destroyed by the cut: {out!r}")
    assert " ".join(out).split() == text.split()


def test_an_internal_mark_is_not_even_a_PREFERRED_boundary():
    """The anchor decides WHERE the cut lands; the word unit decides WHETHER
    anything is lost. They are separate rules and this is the anchor's witness.

    Found by mutation, 2026-09-10: un-anchoring the pattern so a mark matches
    anywhere in a word deletes nothing at all, because the head and the
    remainder are both word slices -- it only moves the cut to just after
    `1,000`, which is not a clause boundary and does not sound like one. So
    the anchor survived its first mutation with the whole file green, and
    this test is what makes it a rule rather than a comment.
    """
    text = ("We shipped about 1,000 units before the deadline last week and "
            "nobody noticed.")
    out = segments(text)
    assert out[0] == "We shipped about 1,000 units before the deadline", (
        f"the cut treated an internal comma as a clause boundary: {out[0]!r}")


def test_nothing_is_lost_for_any_input():
    """The cap reorganises a reply; it must never delete part of it.

    Table-driven rather than one hand-written string. The single fixture this
    replaced had every clause mark sitting word-FINAL, which is the one
    arrangement the character/word unit mismatch could not corrupt -- so the
    test carried this exact name for weeks while asserting nothing about it.
    """
    texts = [
        "First, I need to say something reasonably long here to force a "
        "cut. Second sentence. Third one is here too.",
        "We shipped 1,000 of them; the next batch is due at 12:30 on "
        "Friday, assuming the supplier does not slip again this month.",
        "Read https://example.com/a/b before you touch the resolver, "
        "because the ratio there is 3:1 and nothing in the tests says so.",
        "It is in C:/Users/user/notes -- the part about retries, not the "
        "part about caching, which is already correct and well covered.",
        "One, two, three, four, five, six, seven, eight, nine, ten, "
        "eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen.",
    ]
    for text in texts:
        assert " ".join(segments(text)).split() == text.split(), text


def test_no_cut_ever_lands_inside_a_word():
    """Fuzz, seeded so a failure is reproducible.

    Sentences are built from a pool mixing ordinary words with the tokens
    above, at every length either cap can land on. The property is the whole
    contract of `segments`: it REORGANISES a reply and never edits it.
    """
    import random

    rng = random.Random(20260910)
    pool = ["the", "resolver", "is", "already", "handled", "by", "retry",
            "path", "and", "nothing", "else", "changed", "here", "again,",
            "so;", "then:", "--", "-"] + list(INTERNAL_MARK_TOKENS)
    for _ in range(4000):
        words = [rng.choice(pool) for _ in range(rng.randint(1, 40))]
        text = " ".join(words) + "."
        out = segments(text)
        assert " ".join(out).split() == text.split(), text


# --- structural whitespace ------------------------------------------------
#
# A realistic mixed document: every markdown block the measured reply used,
# in the order it used them. Written as explicit "\n" rather than a triple
# quoted string so the blank lines and the code block's indentation are
# visible in the source and cannot be reflowed by an editor.
MIXED = (
    "## A quick formatted reply\n"
    "Two paragraphs to keep it short.\n"
    "\n"
    "The first one just confirms that everything you asked for is here.\n"
    "\n"
    "The second one says that no file was touched.\n"
    "\n"
    "- A bullet about the heading above\n"
    "- A bullet about the list itself\n"
    "\n"
    "```python\n"
    "def hello(name):\n"
    "\n"
    "    print(name)\n"
    "```\n"
    "\n"
    "| Column A | Column B |\n"
    "|---|---|\n"
    "| first row | value |"
)


def test_a_heading_keeps_the_newline_that_ends_it():
    """The measured defect, reduced to one line.

    A level-2 heading ends at its newline. Collapse that newline and the
    heading is no longer a heading in any markdown renderer -- it becomes the
    first words of the paragraph under it, hash marks and all, which is what
    reply[2] showed on the wire.
    """
    pieces = split_sentences("## A quick formatted reply\nTwo paragraphs.")
    assert pieces == ["## A quick formatted reply\nTwo paragraphs."]


def test_two_paragraphs_keep_the_blank_line_between_them():
    """The separator belongs to the FOLLOWING piece.

    The page joins pieces with nothing when either side already brings
    whitespace, so the blank line has to travel on one of them or the two
    paragraphs render as one.
    """
    pieces = split_sentences("First paragraph.\n\nSecond paragraph.")
    assert pieces == ["First paragraph.", "\n\nSecond paragraph."]
    assert "".join(pieces) == "First paragraph.\n\nSecond paragraph."


def test_three_blank_lines_become_one_blank_line():
    """Normalised, not preserved verbatim: any run of two or more newlines is
    the same block break in markdown, and a splitter that passed them through
    would make the piece boundary visible in the rendered answer."""
    assert split_sentences("One.\n\n\n\nTwo.") == ["One.", "\n\nTwo."]


def test_a_bullet_list_is_not_mangled():
    """Each item is its own line. Welded together they are one paragraph
    beginning with a hyphen, which is the shape the owner called "just a blob
    of text"."""
    text = "- first item\n- second item\n- third item"
    assert split_sentences(text) == [text]


def test_a_fenced_code_block_survives_whole():
    """Fences, internal blank lines and INDENTATION.

    Indentation is the one kind of space run that is structural: it opens a
    nested list item and it is the body of every indented line of code. So a
    run of spaces that follows a newline is kept, while a run between two
    words on the same line still collapses.
    """
    text = ("```python\n"
            "def hello(name):\n"
            "\n"
            "    print(name)\n"
            "```")
    assert split_sentences(text) == [text]


def test_a_table_survives():
    """A markdown table is three lines and stops being a table at two."""
    text = "| Column A | Column B |\n|---|---|\n| first row | value |"
    assert split_sentences(text) == [text]


def test_runs_of_spaces_and_tabs_still_collapse():
    """The half of the old behaviour that was right. Nobody speaks a tab, and
    a double space inside a sentence is not structure."""
    assert split_sentences("Two  spaces\tand a tab.") == [
        "Two spaces and a tab."]


def test_leading_and_trailing_whitespace_is_still_trimmed():
    """A reply does not start with a blank line. The structural rule is about
    what sits BETWEEN pieces; the ends of the whole input are still trimmed,
    which is also what keeps `segments("  \\n ")` empty."""
    assert split_sentences("\n\n  Hello there.  \n\n") == ["Hello there."]


def test_joining_the_pieces_reconstructs_the_document():
    """The whole contract in one assertion.

    The page accumulates the raw markdown across pieces and re-parses it, so
    the only thing that matters is that concatenation gives the document back.
    """
    assert "".join(split_sentences(MIXED)) == MIXED


def test_the_abbreviation_cases_still_work():
    """Regression guard on the half of the function that was already right: a
    splitter that pauses after "approx." makes Butler stutter mid-phrase."""
    assert split_sentences("It touched approx. 40 files. Nothing else.") == [
        "It touched approx. 40 files.", "Nothing else."]
    assert split_sentences("See Dr. Smith today.") == ["See Dr. Smith today."]


def test_an_abbreviation_across_a_line_break_keeps_the_break():
    """The put-back must re-use the separator it consumed. Joining with a
    hardcoded space instead would silently collapse a line break whenever the
    line happened to end in an abbreviation."""
    assert split_sentences("Ask Dr.\nSmith about it.") == [
        "Ask Dr.\nSmith about it."]


def test_drain_returns_complete_sentences_and_a_leftover():
    """`drain_sentences`'s contract, unchanged: never a partial sentence.

    At the moment "I pushed the bra" arrives there is no way to know what the
    rest of it is, and speaking a fragment is worse than waiting one chunk.
    """
    pieces, rest = drain_sentences("The tests pass. I pushed the bra")
    assert pieces == ["The tests pass."]
    assert rest == "I pushed the bra"


def test_drain_never_ends_a_sentence_on_an_abbreviation():
    """"See Dr. " is a title. It goes back into the buffer whole."""
    assert drain_sentences("See Dr. ") == ([], "See Dr. ")


def test_drain_carries_a_blank_line_across_the_chunk_that_split_it():
    """The streaming half of the defect, and the reason the cut moved.

    A model emits "...short.\\n" in one chunk and "\\nThe first..." in the
    next. Cutting the buffer AFTER the first newline left it as trailing
    whitespace on the head, where it was trimmed, and the second newline
    became leading whitespace on the next head, where it was trimmed too -- so
    a blank line vanished whenever a chunk boundary landed inside it.
    """
    first, buffer = drain_sentences("It is short.\n")
    assert first == ["It is short."]
    pieces, buffer = drain_sentences(buffer + "\nThe next paragraph. ")
    assert pieces == ["\n\nThe next paragraph."]
    assert "".join(first + pieces) == "It is short.\n\nThe next paragraph."


# --- the CALLER, which is the half that decides which cap applies ---------
#
# The two caps are a property of `hub._speak`, not of this module: `segments`
# is told which cap the FIRST piece gets and has no idea whether it is being
# asked about the opening of a reply or its fourth sentence. So the rule needs
# an assertion on the caller, which is test/AGENTS.md's D-18 rule and the one
# this project has been burned by three times.
@pytest.mark.asyncio
async def test_only_the_opening_of_a_reply_gets_the_tight_cap(tmp_path):
    """The 8-word cap buys time to FIRST audio (AC-4), and there is one first.

    A reply is several `Say` intents -- the conversation tier yields one per
    sentence -- and each is its own `_speak` call, so `segments` restarted at
    index 0 every time and cut EVERY sentence of every answer at 8 words. The
    16-word cap only ever reached the tail of a single long sentence.

    The teeth are the third entry. A daemon with the defect back cuts the
    second `Say` at 8 words too, and every other assertion here still passes.
    """
    from conftest import QuietTts, attach_session, make_hub
    from daemon.adapters.brain_fake import FakeBrain
    from daemon.ports import Done, Say

    opening = ("The resolver already handles every retry path in the fetch "
               "helper today.")
    follow_up = ("Nothing else in the module changed and the tests are still "
                 "green.")
    assert len(opening.split()) == len(follow_up.split()) == 12, (
        "both sentences must be longer than the tight cap and shorter than "
        "the loose one, or this test cannot tell the two apart")

    hub = make_hub(tmp_path)
    hub.tts = QuietTts()
    hub.brain = FakeBrain(rules=[
        ("cap probe", lambda t, c: [Say(opening, tier="conversation"),
                                    Say(follow_up, tier="conversation"),
                                    Done("conversation")])])
    s = attach_session(hub)

    from test_typed_turn import type_at
    await type_at(hub, s, "cap probe")

    assert hub.tts.said == [
        "The resolver already handles every retry path in",
        "the fetch helper today.",
        follow_up,
    ], f"the caps were applied per SAY rather than per REPLY: {hub.tts.said!r}"


@pytest.mark.parametrize("chunk", [1, 3, 7, 40, 4096])
def test_streaming_the_document_reconstructs_it_at_every_chunk_size(chunk):
    """What the two brains actually do, at chunk sizes nobody controls.

    `brain_endpoint` and `brain_harness` both append a delta to a buffer and
    call `drain_sentences`. The provider decides where the chunks fall, so the
    structure has to survive a break landing anywhere -- including inside a
    blank line, inside a code fence and between a terminator and its space.
    """
    pieces: list[str] = []
    buffer = ""
    for i in range(0, len(MIXED), chunk):
        buffer += MIXED[i:i + chunk]
        drained, buffer = drain_sentences(buffer)
        pieces += drained
    assert "".join(pieces) + buffer == MIXED
