"""Sentence splitting for the speech path. plan.md 4.7.

The first segment of a reply is the whole of AC-4's speech budget, and the
numbers are not close. Kokoro runs CPU-only at a measured realtime factor
of 3.5-4.6x, so:

    ~8 words  (~2 s of speech)  ->  ~450 ms   [MEASURED p50 448 ms]
    19 words                    ->  1784 ms   [MEASURED]
    38 words                    ->  3710 ms   [MEASURED]

One long opening sentence therefore blows the 1500 ms budget on its own,
while later sentences are free because they synthesize while the first is
playing. So the first segment is CUT at a word cap regardless of where the
sentence actually ends, preferring a clause boundary if one is near.

This is enforced here with a test rather than requested of the model in a
prompt, because prompts asking for short first sentences are exactly the
kind of instruction models ignore (D-11).

WHITESPACE: COLLAPSED ACROSS A LINE, KEPT BETWEEN LINES
-------------------------------------------------------
These pieces have a SECOND consumer. Each one is sent to the page as a
`reply` frame, and the page concatenates them and re-renders the whole answer
as markdown (`web/app.js`, `logReply`). So whitespace here is two different
things and only one of them may be thrown away:

  * runs of spaces and tabs WITHIN a line are speech, and collapse to one
    space -- nobody says a tab, and a double space is not structure;
  * NEWLINES are structure, and survive. A single newline stays single (it
    ends a heading, a list item, a table row); a run of two or more is a
    block break and is normalised to exactly two; and the spaces or tabs
    directly after a newline are INDENTATION -- a nested list item, the body
    of a fenced code block -- so they are kept verbatim.

MEASURED on the real wire 2026-09-10, before this rule existed:

    reply[2] '## A quick formatted reply Two paragraphs to keep it short.'

`" ".join(text.split())` welded a level-2 heading onto the sentence under it,
so the page could never draw a heading, and two paragraphs arrived with the
blank line between them gone. Lists, fences and tables survived only by
accident -- they hold no sentence terminator, so they fell out of
`drain_sentences` as the leftover buffer and never went through the splitter
at all.

WHERE THE SEPARATOR GOES: on the FOLLOWING piece. When a sentence boundary is
consumed and the whitespace at it held a newline, the piece after the break
carries it, so `"".join(pieces)` gives the document back. The page depends on
that direction: it inserts a joining space only when neither side already
brings whitespace, and a space in front of a heading or a bullet would break
both.

Known and bounded: inside a fenced code block, a run of spaces between two
tokens on one line still collapses and three blank lines still become two.
Code is not spoken here, the segment caps below are in WORDS, and a splitter
that understood fences would be a markdown parser.
"""
from __future__ import annotations

import re

FIRST_SEGMENT_MAX_WORDS = 8

# Later segments are capped too, for a different reason. The first cap buys
# time-to-first-audio (AC-4); this one bounds how much synthesis a barge-in
# throws away (AC-5).
#
# The cap is smaller than it needs to be now that the sidecar has a cancel
# route (step 8, 2026-08-17): an abandoned reply no longer runs to completion,
# so the orphan cost is the sentence already inside a native ONNX generate()
# rather than every sentence after it. It is deliberately NOT being raised in
# the same change that made that true. Raising it is a latency trade with its
# own measurement -- longer segments mean fewer round trips but a larger
# stranded sentence -- and doing it here would mean shipping two changes under
# one set of numbers.
LATER_SEGMENT_MAX_WORDS = 16
# Where we would rather cut when the cap lands mid-sentence. A cut at a comma
# sounds deliberate; a cut at "the" does not.
#
# A MARK ONLY COUNTS AT THE END OF A WORD, and that is the whole of the rule.
# The earlier version searched the head for a bare "," ";" or ":" with
# `str.rfind`, which has no notion of a word at all, so it matched happily
# inside `1,000`, `12:30`, `3:1`, `https://` and `C:/Users` -- and the head it
# then built was a CHARACTER slice while the remainder was a WORD index, so the
# token the slice landed inside counted as consumed and its second half was
# DELETED rather than carried to the next phrase. MEASURED 2026-09-10: a fuzz
# over 4000 generated sentences lost 3810 characters and duplicated none.
# Working in word indices end to end is what makes that class of defect
# unrepresentable rather than merely fixed -- including the `idx > best`
# off-by-one that compared a raw offset against an already-adjusted one.
#
# Trailing closing quotes and brackets ride along, so `said,"` still ends a
# clause. A bare dash is a whole word of its own; there is no other way to
# write one that this splitter would see, because `split()` is what defines a
# word here.
_CLAUSE_TAIL = re.compile(r"[,;:][\"')\]]*$")
_CLAUSE_DASHES = ("--", "-")

# The separator is CAPTURED, because whether it held a newline decides
# whether the piece after it starts with one.
_SENTENCE_END = re.compile(r"(?<=[.!?])(\s+)")
_WHITESPACE_RUN = re.compile(r"\s+")
# Abbreviations that must not end a sentence. Small and deliberately
# incomplete: this is a speech splitter, not a linguist. Used by
# `split_sentences` and by `drain_sentences` below, which asks the same
# question one chunk earlier.
ABBREV = ("mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "e.g.",
          "i.e.", "etc.", "no.", "fig.", "approx.")

# Where a sentence ends MID-STREAM: a terminator followed by whitespace, so
# "Mr." and a decimal point do not each end one while text is still arriving.
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s")


def _block_break(run: str) -> str:
    """One whitespace run, normalised to what it MEANS.

    A run with no newline in it is word spacing and is worth one space. A run
    with newlines is a line break (one) or a block break (two or more), plus
    whatever indentation follows the last newline -- that indentation is the
    nesting of a list item and the body of a fenced code block, so it is kept
    exactly as it arrived.
    """
    newlines = run.count("\n")
    if not newlines:
        return " "
    return ("\n" if newlines == 1 else "\n\n") + run.rsplit("\n", 1)[1]


def _lead(text: str) -> tuple[str, str]:
    """Take the structural part of `text`'s leading whitespace off the front.

    Answers (lead, rest). A leading run with no newline carries no structure
    -- it is the space after a full stop -- so it is dropped, exactly as
    `split_sentences` would drop it. A run WITH a newline is the separator
    that belongs to whatever comes next, and it is handed back so a caller
    can put it there.
    """
    match = _WHITESPACE_RUN.match(text)
    if not match:
        return "", text
    run, rest = match.group(0), text[match.end():]
    return (_block_break(run) if "\n" in run else ""), rest


def drain_sentences(buffer: str) -> tuple[list[str], str]:
    """Complete sentences the buffer now holds, and what is left over.

    Waiting for the whitespace AFTER a terminator is the whole trick: at the
    moment "3." arrives there is no way to know whether the reply is about a
    number or has ended, and speaking a fragment is worse than waiting one
    chunk.

    It lives here rather than in one adapter because two brains stream text a
    fragment at a time now -- an endpoint's completion (4.4 mode 2) and a
    harness's `text_delta` (mode 3) -- and two copies of a sentence boundary
    drift the first time one of them is fixed (docs/harness-mode.md 7.4).

    The leftover buffer carries its own leading separator between calls, and
    that separator is put back onto the first piece it is finally the
    separator FOR. Nothing else can hold it: the piece it belongs to has not
    arrived yet, and a separator left on the head is trailing whitespace,
    which `split_sentences` trims.
    """
    lead, body = _lead(buffer)
    last = None
    for match in _SENTENCE_BREAK.finditer(body):
        last = match
    if last is None:
        return [], lead + body
    # Cut BEFORE the break's whitespace rather than after it. `_SENTENCE_BREAK`
    # matches exactly one whitespace character, so slicing at `last.end()` put
    # the first newline of a blank line on the head (where it was trimmed) and
    # the second on the leftover (where the next call trimmed it) -- a blank
    # line disappeared whenever a chunk boundary landed inside it.
    head = body[:last.end() - 1]
    sep, rest = _lead(body[last.end() - 1:])
    pieces = split_sentences(head)
    if pieces and pieces[-1].lower().endswith(ABBREV):
        # "See Dr. " is a title, not the end of a sentence. Put it back --
        # with the separator it was followed by, not a fresh space, or a line
        # break is lost every time a line happens to end in an abbreviation --
        # and wait for the rest of the line.
        rest, sep = f"{pieces.pop()}{sep or ' '}{rest}", ""
    if not pieces:
        return [], f"{lead}{sep}{rest}"
    pieces[0] = lead + pieces[0]
    return pieces, sep + rest


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, without breaking common abbreviations.

    Whitespace is normalised rather than collapsed: see the module docstring
    for which runs survive and why. The ends of the WHOLE input are still
    trimmed -- a reply does not begin with a blank line -- so a piece starts
    with a newline only when this call is the one that consumed the boundary.
    """
    text = _WHITESPACE_RUN.sub(lambda m: _block_break(m.group(0)),
                               text or "").strip()
    if not text:
        return []
    # `[part, separator, part, separator, ...]`, because the separator is a
    # capturing group.
    parts = _SENTENCE_END.split(text)
    out: list[str] = [parts[0]]
    for index in range(1, len(parts), 2):
        sep, part = parts[index], parts[index + 1]
        if out[-1].lower().endswith(ABBREV):
            out[-1] = f"{out[-1]}{sep}{part}"
        elif "\n" in sep:
            out.append(sep + part)
        else:
            out.append(part)
    return [p for p in out if p.strip()]


def _clause_end(words: list[str]) -> int:
    """Index of the LAST word in `words` that ends a clause, or -1.

    A WORD index and never a character offset: that is what keeps the head and
    the remainder measured in the same unit, which is the defect this function
    was extracted to make impossible.
    """
    for index in range(len(words) - 1, -1, -1):
        if _CLAUSE_TAIL.search(words[index]) or words[index] in _CLAUSE_DASHES:
            return index
    return -1


def _cut_at_cap(sentence: str, max_words: int) -> tuple[str, str]:
    """Cut `sentence` so the head is at most `max_words` words.

    Prefers the last clause boundary at or before the cap; falls back to a
    hard word cut. Returns (head, remainder); remainder is "" if no cut was
    needed.

    NOTHING IS EVER DELETED. The mark stays on the head it ends -- a trailing
    comma is a pause the phonemizer already reads, and stripping it was a
    character removed from the reply for no audible gain. It is also what
    makes the no-loss property assertable with no exception carved into it:
    `" ".join(segments(t)).split() == t.split()` for every t.
    """
    words = sentence.split()
    if len(words) <= max_words:
        return sentence, ""

    # Look for a clause boundary inside the head, but not so early that the
    # first segment becomes a stub -- a two-word opener is worse than a
    # slightly long one.
    at = _clause_end(words[:max_words])
    cut = at + 1 if at >= 0 and at + 1 >= max(3, max_words // 2) else max_words
    return " ".join(words[:cut]), " ".join(words[cut:])


def segments(text: str, first_max_words: int = FIRST_SEGMENT_MAX_WORDS,
             later_max_words: int = LATER_SEGMENT_MAX_WORDS) -> list[str]:
    """Split a reply into synthesis segments, both caps applied.

    The two caps exist for different reasons and are deliberately different
    sizes: the first bounds time to first audio (AC-4), every later one
    bounds how much synthesis a barge-in strands (AC-5).
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    out: list[str] = []
    for index, sentence in enumerate(sentences):
        cap = first_max_words if index == 0 else later_max_words
        remainder = sentence
        while remainder:
            head, remainder = _cut_at_cap(remainder, cap)
            out.append(head)
            # Only the very first piece gets the tight cap; the rest of a
            # long opening sentence is just another later segment.
            cap = later_max_words
    return out
