"""Turn modes end to end: settings, selection, and the closing warning.

The policies themselves are tested in test_endpointing_policies.py. This is
about the parts that only exist once they are wired: that the mode is
server-side and shared, that a spoken request changes it without going
anywhere near a model, and that the satellite is warned before it is cut
off.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub  # noqa: E402
from daemon import endpointing  # noqa: E402
from daemon.settings import Settings, spoken_mode_request  # noqa: E402


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
def test_settings_survive_a_restart(tmp_path):
    """D-2 / AC-8: server-side, so two devices cannot disagree."""
    a = Settings(tmp_path / "settings.json")
    a.set("turn_mode", "endword")
    assert Settings(tmp_path / "settings.json").turn_mode == "endword"


def test_an_unknown_value_is_refused(tmp_path):
    """One exception type for every refusal.

    SettingsError subclasses ValueError, so a bad value and an unknown key
    are caught the same way at the API edge -- there is no reason a caller
    should have to know which of the two it made.
    """
    from daemon.settings import SettingsError

    s = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        s.set("turn_mode", "whenever")
    with pytest.raises(SettingsError):
        s.set("colour", "blue")
    assert s.turn_mode == "patient", "a rejected write changed the setting"


def test_a_corrupt_file_falls_back_loudly_instead_of_refusing_to_boot(
        tmp_path, capsys):
    """Unlike a permission mode, a preference cannot make Butler unsafe, so
    failing to start over one would be the worse failure."""
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    s = Settings(path)
    assert s.turn_mode == "patient"
    assert "unreadable" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# spoken selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("said,expected", [
    ("long form", "endword"),
    ("switch to long form", "endword"),
    ("Let me think.", "endword"),
    ("use smart turn", "smart"),
    ("patient mode", "patient"),
])
def test_a_spoken_mode_request_is_recognised(said, expected):
    assert spoken_mode_request(said) == expected


@pytest.mark.parametrize("said", [
    "I think we should switch to long form later",
    "what does long form mean",
    "add long form support to the parser",
    "",
])
def test_a_sentence_merely_containing_the_phrase_is_not_a_request(said):
    """Matched on the WHOLE utterance. Submitting a mode change instead of
    answering the question would be maddening, and it is exactly what a
    substring match would do."""
    assert spoken_mode_request(said) is None


@pytest.mark.asyncio
async def test_a_spoken_request_changes_the_mode_without_the_brain(tmp_path):
    """A preference must not depend on a model's mood.

    Routing this to the brain would make a deterministic setting subject to
    whatever the router felt like that turn, and would cost a model call for
    something the daemon can decide by string comparison.
    """
    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")
    s = attach_session(hub, "desk")
    s.turn = 1

    from daemon.ports import Final
    await hub._on_final(s, 1, Final(text="switch to long form"), "silence")

    assert hub.settings.turn_mode == "endword"
    assert hub.brain.seen == [], "the mode change went to the brain"
    kinds = s.ws.text_types()
    assert "turn_mode" in kinds, "other devices were not told"
    assert "speak_start" in kinds, "the change was not confirmed out loud"


# --------------------------------------------------------------------------- #
# the policy actually used
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_hub_builds_the_policy_the_setting_names(tmp_path):
    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")

    hub.settings.set("turn_mode", "endword")
    assert hub._policy_for_turn().name == "endword"

    hub.settings.set("turn_mode", "patient")
    assert hub._policy_for_turn().name == "patient"


@pytest.mark.asyncio
async def test_each_turn_gets_a_fresh_policy(tmp_path):
    """SmartTurnEnd remembers when it last asked and what it heard, and
    EndWordEnd is matched against one turn's transcript. Sharing an instance
    would leak the previous turn's answer into this one's first decision."""
    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")
    assert hub._policy_for_turn() is not hub._policy_for_turn()


# --------------------------------------------------------------------------- #
# the closing warning
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_satellite_is_warned_before_being_cut_off():
    """Being cut off is only unpleasant because it is a surprise."""
    from daemon import ports
    from daemon.pipeline import TurnPipeline

    warned = []
    finals = []

    async def on_closing():
        warned.append(True)

    p = TurnPipeline(
        turn=1, asr=ports.build(ports.ASRS, "fake", ["hello"]),
        on_partial=lambda _p: None,
        on_final=lambda f, r: finals.append((f, r)),
        asr_config=ports.AsrConfig(model="test"),
        policy=endpointing.SilenceEnd("balanced"),
        on_closing=on_closing, poll_s=0.001)

    voiced = b"\x00\x20" * 512
    for _ in range(20):
        p.feed(voiced)

    async def go_quiet():
        for _ in range(80):
            p.feed(b"\x00\x00" * 512)
            await asyncio.sleep(0.001)

    feeder = asyncio.create_task(go_quiet())
    await asyncio.wait_for(p.run(), timeout=15)
    feeder.cancel()

    assert warned, "the turn ended with no warning at all"
    assert finals


# --------------------------------------------------------------------------- #
# the live capture ceiling (AC-92's other half, docs/voice-control.md
# section 0: "the daemon telling the page the LIVE policy's ceiling"). The
# page's watchdog is a separate anti-latch net and it is honest only if it
# tracks the SAME arithmetic the pipeline itself runs at spawn -- see
# `web/app.js` WATCHDOG_MS comment and `pipeline.py`'s own `ceiling = max(...)`.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_capture_ceiling_tracks_the_mode(tmp_path):
    """`_capture_ceiling_ms()` is a READ of `pipeline.py`'s own formula
    (`max(absolute_max_turn_ms, policy.max_turn_ms)`), not a second copy of
    it (D-18): patient/smart get the conversational safety net, endword gets
    its own much larger one."""
    from daemon.endpointing import END_WORD_MAX_MS
    from daemon.pipeline import ABSOLUTE_MAX_TURN_MS

    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")

    hub.settings.set("turn_mode", "patient")
    assert hub._capture_ceiling_ms() == ABSOLUTE_MAX_TURN_MS

    hub.settings.set("turn_mode", "smart")
    assert hub._capture_ceiling_ms() == ABSOLUTE_MAX_TURN_MS

    hub.settings.set("turn_mode", "endword")
    assert hub._capture_ceiling_ms() == END_WORD_MAX_MS, (
        "endword's own 300s ceiling did not win over the conversational one")


@pytest.mark.asyncio
async def test_the_ceiling_falls_back_with_no_settings_store(tmp_path):
    """Several fixtures here build a Hub with no settings at all (`_turn_mode`
    already falls back to \"patient\" for the same reason) -- this must not
    raise, and must answer the conversational ceiling rather than 0."""
    from daemon.pipeline import ABSOLUTE_MAX_TURN_MS
    hub = make_hub(tmp_path)
    assert hub._capture_ceiling_ms() == ABSOLUTE_MAX_TURN_MS


@pytest.mark.asyncio
async def test_ready_carries_the_live_ceiling(tmp_path):
    """`ready` already carries `turn_mode` for a fresh connection; the
    ceiling is the OTHER half of the same fact and belongs on the same
    frame rather than a new one (docs/voice-control.md section 0)."""
    from daemon.endpointing import END_WORD_MAX_MS

    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")
    hub.settings.set("turn_mode", "endword")
    s = attach_session(hub)

    await hub._hello(s, {})

    ready = next(json.loads(m) for m in s.ws.sent
                if isinstance(m, str) and json.loads(m)["type"] == "ready")
    assert ready.get("capture_ceiling_ms") == END_WORD_MAX_MS, (
        f"ready did not carry the live ceiling: {ready}")


@pytest.mark.asyncio
async def test_a_spoken_mode_change_carries_the_new_ceiling(tmp_path):
    """A live switch to long-form mode has to update the page's budget in
    the SAME frame that announces the mode changed, or a page that never
    reconnects keeps the old number for the rest of the conversation."""
    from daemon.endpointing import END_WORD_MAX_MS
    from daemon.ports import Final

    hub = make_hub(tmp_path)
    hub.settings = Settings(tmp_path / "settings.json")
    s = attach_session(hub, "desk")
    s.turn = 1

    await hub._on_final(s, 1, Final(text="switch to long form"), "silence")

    turn_mode_frames = [json.loads(m) for m in s.ws.sent
                        if isinstance(m, str)
                        and json.loads(m)["type"] == "turn_mode"]
    assert turn_mode_frames, "no turn_mode frame was sent"
    assert turn_mode_frames[-1].get("capture_ceiling_ms") == END_WORD_MAX_MS, (
        f"the mode change did not carry the new ceiling: "
        f"{turn_mode_frames[-1]}")


@pytest.mark.asyncio
async def test_the_warning_fires_once_per_pause_not_every_poll():
    """A tick per poll would be a stutter, not a warning."""
    from daemon import ports
    from daemon.pipeline import TurnPipeline

    warned = []

    async def on_closing():
        warned.append(True)

    p = TurnPipeline(
        turn=1, asr=ports.build(ports.ASRS, "fake", ["hello"]),
        on_partial=lambda _p: None, on_final=lambda f, r: None,
        asr_config=ports.AsrConfig(model="test"),
        policy=endpointing.SilenceEnd("patient"),
        on_closing=on_closing, poll_s=0.001)

    voiced = b"\x00\x20" * 512
    for _ in range(20):
        p.feed(voiced)
    # Land INSIDE the warning zone and stop feeding, so the turn sits there:
    # patient warns from 528 ms and finalizes at 800 ms, and silence only
    # accumulates from arriving frames. An earlier version fed past 800 ms,
    # so the turn ended on the first poll and the debounce was never
    # exercised at all -- the test passed with the guard removed.
    for _ in range(18):            # 576 ms: warning zone, short of the end
        p.feed(b"\x00\x00" * 512)

    runner = asyncio.create_task(p.run())
    await asyncio.sleep(0.15)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert len(warned) == 1, f"warned {len(warned)} times in one pause"
