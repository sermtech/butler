"""The turn starts where the wake word FIRED, not where the pre-roll ends.
AC-800..AC-802 (docs/wake-words.md section 1).

The defect these encode was measured on the Mac mini, iPhone over Tailscale
serve, 2026-10-29: every wake opened a turn that ended 156-361 ms later with
`reason=silence silence_ms=640` and an empty transcript. The pre-roll's
416 ms lookback holds the tail of the wake phrase; the energy gate latched
voicing on it, then counted the quiet after the phrase -- while the owner
waited for the listening cue -- as the end of the request.

Each test feeds the whole back-fill BEFORE run() starts, which is what a
satellite's flush looks like to the daemon: one burst inside a poll.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub, silence_pcm, tone_pcm  # noqa: E402
from daemon import hub as hub_mod  # noqa: E402
from daemon import ports  # noqa: E402
from daemon.pipeline import TurnPipeline  # noqa: E402
from test_pipeline_endpointing import FakeClock  # noqa: E402

LOOKBACK = tone_pcm(416)            # 13 ring frames of "...jarvis"


def make_pipeline(script=None):
    finals = []
    p = TurnPipeline(
        turn=1, asr=ports.build(ports.ASRS, "fake", script or []),
        on_partial=lambda _p: None,
        on_final=lambda f, reason: finals.append((f, reason)),
        asr_config=ports.AsrConfig(model="test"),
        clock=FakeClock(), poll_s=0.001)
    return p, finals


def feed(p: TurnPipeline, pcm: bytes) -> None:
    step = 32 * 32
    for i in range(0, len(pcm), step):
        p.feed(pcm[i:i + step])


async def feed_gradually(p: TurnPipeline, pcm: bytes) -> None:
    step = 32 * 32
    for i in range(0, len(pcm), step):
        p.feed(pcm[i:i + step])
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_the_wake_phrase_and_the_wait_after_it_never_end_a_turn():
    """AC-800. The iPhone trace, rebuilt: wake tail, then 900 ms of quiet
    buffered while the owner waited for the cue. Nothing after the fire was
    spoken, so the turn must end as `no_speech`, never as `silence`."""
    p, finals = make_pipeline()
    p.note_lookback(len(LOOKBACK))
    feed(p, LOOKBACK + silence_pcm(900))
    await asyncio.wait_for(p.run(), 10)
    assert [r for _, r in finals] == ["no_speech"]


@pytest.mark.asyncio
async def test_a_command_inside_the_back_fill_still_starts_the_turn():
    """AC-800, the other direction. A fluent "hey jarvis what time is it"
    puts the command in the frames buffered AFTER the fire. The boundary is
    the fire point, not the end of the flush, so the command arms voicing
    and the turn ends on the silence after it, with its words."""
    p, finals = make_pipeline(script=["what time is it"])
    p.note_lookback(len(LOOKBACK))
    feed(p, LOOKBACK + silence_pcm(96) + tone_pcm(640))
    run = asyncio.create_task(p.run())
    await feed_gradually(p, silence_pcm(1500))
    await asyncio.wait_for(run, 10)
    assert [r for _, r in finals] == ["silence"]
    assert finals[0][0].text == "what time is it"


@pytest.mark.asyncio
async def test_a_sliver_after_the_fire_ends_by_no_speech_not_the_cap():
    """AC-801. The fire lands inside "...vis", so a sliver under
    `min_speech_ms` (200) after the boundary is the NORMAL case. A voiced
    turn below that bar used to skip both the policy and the no-speech
    timer and run to the 120 s cap holding the recognizer."""
    p, finals = make_pipeline()
    p.note_lookback(len(LOOKBACK))
    feed(p, LOOKBACK + tone_pcm(96) + silence_pcm(900))
    await asyncio.wait_for(p.run(), 10)
    assert [r for _, r in finals] == ["no_speech"]


class _StubPipeline:
    def __init__(self):
        self.noted = []

    def note_lookback(self, n_bytes):
        self.noted.append(n_bytes)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [
    {"bytes": 22528, "ms": 704},                       # no lookback at all
    {"bytes": 22528, "ms": 704, "lookback": "most"},   # not an integer
    {"bytes": 22528, "ms": 704, "lookback": 30000},    # more than was flushed
    {"bytes": 22528, "ms": 704, "lookback": -1},
])
async def test_a_preroll_without_a_true_lookback_is_refused(tmp_path, header):
    """AC-802. The fire point is the only thing that says where the turn
    starts; a header that cannot say it is a page bug, refused loudly and
    never guessed at."""
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.state = hub_mod.CAPTURING
    s.pipeline = _StubPipeline()
    await hub._preroll(s, {"type": "preroll", "turn": 1, **header})
    assert s.pipeline.noted == []
    assert "error" in s.ws.text_types()


@pytest.mark.asyncio
async def test_the_hub_hands_the_pipeline_the_lookback_not_the_flush(tmp_path):
    """AC-802, the caller (D-18): the pipeline's boundary is `lookback`."""
    hub = make_hub(tmp_path)
    s = attach_session(hub, "desk")
    s.state = hub_mod.CAPTURING
    s.pipeline = _StubPipeline()
    await hub._preroll(s, {"type": "preroll", "turn": 1, "bytes": 22528,
                           "ms": 704, "lookback": 13312})
    assert s.pipeline.noted == [13312]
