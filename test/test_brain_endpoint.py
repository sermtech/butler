"""Endpoint mode: the conversation model, streamed. plan.md 4.4, step 7.5.

The acceptance criteria this file carries are AC-38 (the cap is on the wire),
AC-40 (a capped reply is visible, never silent, never half-spoken) and the
socket half of AC-43 (barge-in reaches the request, not just the reader).

The measured failure behind AC-40 is worth restating, because it is why the
caps have a FAILURE BEHAVIOUR and not just a number: Ornith-1.0-9B under a
200-token cap spent the entire cap in its thinking block and returned EMPTY
content, 10 times out of 10 [MEASURED 2026-08-17, plan.md 4.4]. Silence is
indistinguishable from Butler ignoring you.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import (StubResolver, attach_session,        # noqa: E402
                      make_hub)
from daemon import llm, ports                              # noqa: E402
from daemon.adapters.brain_endpoint import EndpointConverse  # noqa: E402
from daemon.ports import Done, Say, TurnContext            # noqa: E402


def _ctx(turn_id=1, history=()):
    return TurnContext(turn_id=turn_id, mode="secretary", history=history)


def _converse(server, cap=None, reasoning=None):
    return EndpointConverse(StubResolver(
        server.base_url, caps={"reply": cap},
        reasoning={"reply": reasoning} if reasoning is not None else None))


async def _intents(brain, text="explain the GIL", ctx=None):
    return [i async for i in brain.turn(text, ctx or _ctx())]


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_reply_arrives_as_one_intent_per_sentence(fake_openai):
    """Sentence by sentence, so the first one starts synthesizing while the
    rest is still being generated. That is the whole reason `Brain.turn`
    yields instead of returning."""
    fake_openai.answer("The lock is per interpreter. Threads take turns. "
                       "That is the short version.")
    intents = await _intents(_converse(fake_openai))

    said = [i.text for i in intents if isinstance(i, Say)]
    assert said == ["The lock is per interpreter.", "Threads take turns.",
                    "That is the short version."]
    assert isinstance(intents[-1], Done) and intents[-1].tier == "conversation"


@pytest.mark.asyncio
async def test_the_first_sentence_does_not_wait_for_the_last(fake_openai):
    """AC-4 lives on this. A reply held until the model stops generating pays
    the whole generation before the first syllable is spoken."""
    fake_openai.chunk_delay_s = 0.03
    fake_openai.answer("One two three four. " + "filler word here again. " * 8)

    started = time.monotonic()
    stream = _converse(fake_openai).turn("go", _ctx())
    first = await anext(stream)
    first_at = time.monotonic() - started
    rest = [i async for i in stream]
    whole_at = time.monotonic() - started

    assert isinstance(first, Say) and first.text == "One two three four."
    assert rest, "the rest of the reply never arrived"
    assert first_at < whole_at / 2, (
        f"first sentence took {first_at:.2f}s of a {whole_at:.2f}s reply -- "
        f"it was buffered rather than streamed")


@pytest.mark.asyncio
async def test_the_conversation_is_carried_when_there_is_one(fake_openai):
    """`TurnContext.history` holds what was SPOKEN (D-20). Nothing fills it
    yet -- conversation memory is open question 8.6 -- and the adapter honours
    it now so that wiring it later is one change in the hub, not two."""
    fake_openai.answer("Yes.")
    await _intents(_converse(fake_openai),
                   ctx=_ctx(history=(("user", "what is the GIL"),
                                     ("assistant", "A lock."))))

    roles = [m["role"] for m in fake_openai.last_body()["messages"]]
    assert roles == ["system", "user", "assistant", "user"], roles


# --------------------------------------------------------------------------- #
# the cap on the wire (AC-38, reply half)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_reply_cap_is_sent_when_set(fake_openai):
    fake_openai.answer("Fine.")
    await _intents(_converse(fake_openai, cap=512))
    assert fake_openai.last_body().get("max_tokens") == 512


@pytest.mark.asyncio
async def test_zero_sends_no_cap_at_all(fake_openai):
    fake_openai.answer("Fine.")
    await _intents(_converse(fake_openai, cap=None))
    assert "max_tokens" not in fake_openai.last_body()


# --------------------------------------------------------------------------- #
# reasoning off on the wire (reply half). The measured failure this exists for:
# Ornith-1.0-9B with reasoning on takes 30-68 s to say its first word; off, it
# starts in 0.2 s (2026-08-18). On the wire or nowhere (AC-38's shape).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reply_reasoning_off_sends_the_no_think_kwarg(fake_openai):
    fake_openai.answer("Fine.")
    await _intents(_converse(fake_openai, reasoning=False))
    assert (fake_openai.last_body().get("chat_template_kwargs")
            == {"enable_thinking": False})


@pytest.mark.asyncio
async def test_reply_reasoning_on_sends_no_kwarg(fake_openai):
    """The default is a byte-identical request, so a cloud connection is never
    handed a kwarg it did not ask for."""
    fake_openai.answer("Fine.")
    await _intents(_converse(fake_openai, reasoning=True))
    assert "chat_template_kwargs" not in fake_openai.last_body()


@pytest.mark.asyncio
async def test_the_reply_is_streamed_on_the_wire(fake_openai):
    fake_openai.answer("Fine.")
    await _intents(_converse(fake_openai))
    assert fake_openai.last_body().get("stream") is True


# --------------------------------------------------------------------------- #
# AC-40 -- a capped reply is visible, never silent, never half-spoken
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_truncated_reply_speaks_whole_sentences_and_not_the_fragment(
        fake_openai, capsys):
    """A half-sentence read aloud sounds like a broken Butler, and history
    records only what was spoken anyway (D-20). The fragment still goes to the
    screen: what the model produced is not a secret, it is just not speech."""
    fake_openai.answer("First sentence here. Second one too. And then it was c",
                       finish_reason="length")
    intents = await _intents(_converse(fake_openai, cap=64))

    says = [i for i in intents if isinstance(i, Say)]
    assert [s.text for s in says if s.spoken] == ["First sentence here.",
                                                  "Second one too."]
    unspoken = [s for s in says if not s.spoken]
    assert [s.text for s in unspoken] == ["And then it was c"], (
        "the trailing fragment was either spoken or thrown away")
    assert isinstance(intents[-1], Done) and intents[-1].truncated, (
        "the turn did not say it had been cut off")

    out = capsys.readouterr().out
    assert "finish_reason=length" in out and "cap=64" in out, out


@pytest.mark.asyncio
async def test_a_reply_capped_to_nothing_says_so_out_loud(fake_openai, capsys):
    """AC-40's second half. MEASURED: 10 of 10 verdicts came back empty under
    a cap the reasoning block consumed. Silence here is indistinguishable from
    Butler ignoring the user, which is the failure this line exists for."""
    fake_openai.answer("", finish_reason="length",
                       reasoning="Let me consider how to phrase this...")
    intents = await _intents(_converse(fake_openai, cap=200))

    says = [i for i in intents if isinstance(i, Say)]
    assert len(says) == 1 and says[0].spoken, f"nothing was said: {intents}"
    assert says[0].text == EndpointConverse.OUT_OF_ROOM
    assert isinstance(intents[-1], Done) and intents[-1].truncated

    out = capsys.readouterr().out
    assert "finish_reason=length" in out and "cap=200" in out, out


@pytest.mark.asyncio
async def test_an_empty_reply_that_was_not_capped_still_says_something(
        fake_openai, capsys):
    """Not AC-40 -- no cap was involved -- and the same principle: a turn that
    produces no sound at all is indistinguishable from a dead microphone."""
    fake_openai.answer("", finish_reason="stop")
    intents = await _intents(_converse(fake_openai))

    says = [i for i in intents if isinstance(i, Say)]
    assert len(says) == 1 and says[0].spoken
    assert not intents[-1].truncated, "an uncapped reply claimed a cap cut it"
    assert "empty" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_an_ordinary_reply_is_not_marked_truncated(fake_openai):
    """The negative half. A marker that is always on says nothing."""
    fake_openai.answer("All done.", finish_reason="stop")
    intents = await _intents(_converse(fake_openai))
    assert all(i.spoken for i in intents if isinstance(i, Say))
    assert not intents[-1].truncated


# --------------------------------------------------------------------------- #
# AC-40's other half, on the CALLER (D-18)
# --------------------------------------------------------------------------- #
class ScriptedBrain:
    """Yields a fixed list of intents. The hub is what is under test here."""

    def __init__(self, intents):
        self._intents = intents
        self.aborted = []

    async def turn(self, text, ctx):
        for intent in self._intents:
            yield intent

    async def abort(self, turn_id):
        self.aborted.append(turn_id)

    async def warm(self):
        return None

    @property
    def info(self):
        return {"engine": "scripted"}


async def _drive(hub, s, turn):
    """Run one reply to completion, playing the satellite's part."""
    async def confirm():
        for _ in range(400):
            if s.audio_ms_sent > 0:
                await hub._playback_done(s, {"turn": turn})
                return
            await asyncio.sleep(0.01)

    helper = asyncio.create_task(confirm())
    try:
        await asyncio.wait_for(hub._reply(s, turn, "anything"), timeout=15)
    finally:
        helper.cancel()


@pytest.mark.asyncio
async def test_an_unspoken_say_reaches_the_page_and_not_the_speaker(tmp_path):
    """The caller's half of AC-40 (D-18). Conformance can prove an adapter
    EMITS an unspoken Say; only a hub test proves the daemon does not read it
    aloud, and that is the assertion that would have caught a hub which
    ignored the flag."""
    hub = make_hub(tmp_path)
    hub.brain = ScriptedBrain([Say("Spoken part.", tier="conversation"),
                               Say("cut off frag", tier="conversation",
                                   spoken=False),
                               Done("conversation", truncated=True)])
    s = attach_session(hub)
    s.turn = 31
    s.state = "thinking"
    await _drive(hub, s, 31)

    frames = [json.loads(m) for m in s.ws.sent if isinstance(m, str)]
    replies = [f for f in frames if f["type"] == "reply"]
    assert [f["text"] for f in replies] == ["Spoken part.", "cut off frag"], (
        "the fragment never reached the page")
    assert replies[0].get("spoken") is not False
    assert replies[1]["spoken"] is False
    assert hub.tts.spoken == ["Spoken part."], (
        f"the engine was asked to speak {hub.tts.spoken}; a fragment cut "
        f"mid-sentence must not be read aloud")
    assert s.spoken == ["Spoken part."], (
        "the fragment was recorded as spoken; history holds what was SAID "
        "(D-20), and the user never heard this")
    ends = [f for f in frames if f["type"] == "speak_end"]
    assert ends and ends[-1]["truncated"] is True


# --------------------------------------------------------------------------- #
# barge-in, and the failures that are not the model's
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_barge_in_closes_the_request_not_just_the_reader(fake_openai):
    """AC-43, D-4, and the first time this project can assert it against a
    real one. Cancelling our reader leaves the server generating into a socket
    nobody drains -- 7887 ms of it, measured on the TTS side of the same
    shape -- and the next turn queues behind the abandoned one."""
    fake_openai.chunk_delay_s = 0.25
    fake_openai.answer(" ".join(f"word{i}" for i in range(40)))
    brain = _converse(fake_openai)
    stream = brain.turn("something long", _ctx(turn_id=9))

    started = time.monotonic()

    async def drain():
        try:
            async for _ in stream:
                pass
        except llm.Aborted:
            return "aborted"
        return "finished"

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.4)
    await brain.abort(9)
    outcome = await asyncio.wait_for(task, timeout=3)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, (
        f"the stream ran for {elapsed:.1f}s after the abort; the whole reply "
        f"is 10s of chunks, so the request was not closed")
    assert outcome in ("aborted", "finished")


@pytest.mark.asyncio
async def test_a_dead_endpoint_raises_rather_than_answering_nothing(
        fake_openai):
    """Infrastructure, not model output: it fails fast and loudly, and the
    hub's terminal path re-arms (D-1)."""
    fake_openai.answer(status=500, body="model failed to load")
    with pytest.raises(llm.LlmError) as e:
        await _intents(_converse(fake_openai))
    assert "500" in str(e.value)


@pytest.mark.asyncio
async def test_the_model_and_the_key_come_from_the_resolver(fake_openai):
    fake_openai.answer("Fine.")
    brain = EndpointConverse(StubResolver(
        fake_openai.base_url, api_key="SECRET-FOR-ONE-CALL",
        models={"reply": "the-owners-choice"}))
    await _intents(brain)

    assert fake_openai.last_body()["model"] == "the-owners-choice"
    assert fake_openai.asks[-1].authorization == "Bearer SECRET-FOR-ONE-CALL"


@pytest.mark.asyncio
async def test_a_blank_model_sends_no_model_at_all(fake_openai):
    """Blank means "the connection's own default" (4.4). Sending `""` is
    naming a model nobody has."""
    fake_openai.answer("Fine.")
    await _intents(EndpointConverse(StubResolver(fake_openai.base_url,
                                                 models={"reply": ""})))
    assert "model" not in fake_openai.last_body()


# --------------------------------------------------------------------------- #
# the LAST block of a reply, and the one word that used to weld it on
# --------------------------------------------------------------------------- #
#: Written as a name rather than as a literal in every fixture below, because
#: a bare newline inside a triple-quoted fixture is invisible on review and
#: these tests are entirely ABOUT where the newlines are.
NL = chr(10)
@pytest.mark.asyncio
async def test_the_last_block_of_a_reply_keeps_its_own_line(fake_openai):
    """`buffer.rstrip()`, never `buffer.strip()`, at the end of a reply.

    `speech.drain_sentences` carries a block separator onto the leftover so
    the page can tell a list from a sentence. The end of a reply used to
    `strip()` that leftover, which threw the separator away -- so the FINAL
    block of every answer, the one most likely to be a list or a table or a
    code fence, welded onto the paragraph before it and rendered as prose.

    The assertion is on the JOIN and not on the pieces: what the page does is
    concatenate them, so the only property that matters is that the result
    still parses as markdown. Asserting the piece boundaries instead would
    pin the sentence splitter's behaviour, which is a different module's
    business and is allowed to change.
    """
    reply = ("Here is the summary." + NL + NL
             + "- first item" + NL
             + "- second item")
    fake_openai.answer(reply)

    said = [i.text for i in await _intents(_converse(fake_openai))
            if isinstance(i, Say)]
    joined = "".join(said)

    assert "- first item" in joined, f"the list did not survive: {joined!r}"
    lines = [ln for ln in joined.split(NL) if ln.strip()]
    assert any(ln.lstrip().startswith("- first item") for ln in lines), (
        f"the first list item is not at the start of a line, so the page "
        f"renders it as part of the paragraph above it: {joined!r}")
    assert not any("summary." in ln and "- first" in ln for ln in lines), (
        f"the paragraph and the list ended up on ONE line: {joined!r}")
