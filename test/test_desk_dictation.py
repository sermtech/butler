"""The desk agent's dictation: the ring, the state machine, the refusals.

`docs/desk-hotkeys.md` step 3. What this file can prove without a person is
everything except the physical microphone and the physical key -- so the
`Microphone` is driven by pushing blocks at its callback, and the `Agent` is
driven by pushing edges at its queue. Both are the seams the real thing uses.

What it deliberately does NOT do is press a key or write the clipboard. That
is `test/manual/probe_dictation.py`, which is manual for the reason an earlier prototype
recorded when they declined to automate the same thing: "a test that pressed
Ctrl+V would paste into whatever window the person running the suite had
focused".

Two claims.

**The ring is the feature.** A capture must start with audio that arrived
BEFORE the key went down, or the first word is clipped -- 260 ms of it on this
machine, measured.

**A failure writes nothing** (AC-115). Every path that cannot produce text
must reach neither the clipboard nor a keystroke, and must say which failure
it was.
"""
from __future__ import annotations

import asyncio
import queue
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desk import actions, audio, deskd, hotkeys              # noqa: E402

BLOCK_BYTES = audio.BLOCK * 2


# --------------------------------------------------------------------------- #
# a fake sounddevice: no test here may touch a real device
# --------------------------------------------------------------------------- #
class FakeInputStream:
    """Records construction, start, stop and close; can be told to fail."""

    instances: list = []
    fail_start = False
    fail_stop = False

    def __init__(self, **kw):
        self.kw = kw
        self.device = kw.get("device")
        self.samplerate = kw.get("samplerate", 16000)
        self.started = self.stopped = self.closed = False
        FakeInputStream.instances.append(self)

    def start(self):
        if FakeInputStream.fail_start:
            raise OSError("start refused")
        self.started = True

    def stop(self):
        self.stopped = True
        if FakeInputStream.fail_stop:
            raise OSError("device lost")

    def close(self):
        self.closed = True


class FakeSd:
    InputStream = FakeInputStream
    fail_query = False

    @staticmethod
    def query_devices(device=None, kind=None):
        if FakeSd.fail_query:
            raise ValueError("no input device")
        return {"name": "Fake Mic", "hostapi": 0}

    @staticmethod
    def query_hostapis(index):
        return {"name": "MME"}

    def open_streams(self):
        return [s for s in FakeInputStream.instances if not s.closed]


@pytest.fixture
def fake_sd(monkeypatch):
    FakeInputStream.instances = []
    FakeInputStream.fail_start = False
    FakeInputStream.fail_stop = False
    FakeSd.fail_query = False
    sd = FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def block(fill: int) -> bytes:
    """One capture block of a recognisable byte, for identity rather than sound."""
    return bytes([fill, 0]) * audio.BLOCK


# --------------------------------------------------------------------------- #
# the lookback ring
# --------------------------------------------------------------------------- #
def test_a_capture_begins_with_audio_that_arrived_before_the_key():
    """The whole reason the stream is held open (`desk/audio.py`).

    Handy opens the device on key-down and its tracker measures 380-606 ms of
    lost first speech. Here the ring is already full when `begin` is called,
    so the capture starts in the past.
    """
    mic = audio.Microphone(preroll_ms=(BLOCK_BYTES * 3) // audio.BYTES_PER_MS)
    for i in range(1, 6):
        mic._on_block(block(i), audio.BLOCK, None, None)   # before the key

    mic.begin()
    mic._on_block(block(9), audio.BLOCK, None, None)       # after the key
    pcm, overflowed = mic.end()

    assert not overflowed
    assert pcm.endswith(block(9)), "the live block is missing from the capture"
    assert len(pcm) > BLOCK_BYTES, (
        "the capture began at the keypress; every word before it is gone, "
        "which is the defect the ring exists to prevent")
    assert pcm.startswith(block(3)), (
        f"the ring should hold the last three blocks (3, 4, 5) and start the "
        f"capture at the oldest of them; got a capture starting {pcm[:2]!r}")


def test_a_ring_of_zero_captures_only_what_arrives_after_the_key(fake_sd):
    """0 is the honest way to turn the lookback off, not a broken ring."""
    mic = audio.Microphone(preroll_ms=0)
    mic._on_block(block(1), audio.BLOCK, None, None)

    mic.begin()
    mic._on_block(block(2), audio.BLOCK, None, None)
    pcm, _ = mic.end()

    assert pcm == block(2), (
        f"a zero-length lookback still carried {len(pcm) // BLOCK_BYTES} "
        f"blocks of the past")


def test_a_key_held_forever_stops_at_the_ceiling(fake_sd):
    """A stuck key must not grow without bound.

    Handy has no maximum recording length anywhere and an unbounded buffer
    behind a physical key is a memory leak with a finger on it.
    """
    mic = audio.Microphone(preroll_ms=0, max_seconds=1)

    mic.begin()
    for _ in range(200):                       # ~6.4 s at 32 ms a block
        mic._on_block(block(7), audio.BLOCK, None, None)
    pcm, overflowed = mic.end()

    assert overflowed, "the ceiling did not fire on a capture that ran past it"
    assert len(pcm) <= mic.max_bytes + BLOCK_BYTES, (
        f"the capture kept growing past the ceiling: {len(pcm)} bytes")


def test_the_ring_keeps_filling_while_nothing_is_being_captured():
    """The stream is open between dictations; the ring must stay bounded."""
    mic = audio.Microphone(preroll_ms=(BLOCK_BYTES * 2) // audio.BYTES_PER_MS)
    for i in range(500):
        mic._on_block(block(i % 251), audio.BLOCK, None, None)

    assert len(mic._ring) == mic._ring.maxlen, "the ring is not full"
    assert mic._ring.maxlen * BLOCK_BYTES <= 4 * BLOCK_BYTES, (
        "an unbounded ring holds the whole session in memory")


# --------------------------------------------------------------------------- #
# hold the device only while capturing (AC-819..AC-821)
# --------------------------------------------------------------------------- #
def test_preroll_zero_opens_no_stream_until_the_press_and_closes_it_at_the_release(
        fake_sd):
    """AC-819. Arming describes the device; begin opens; end releases."""
    mic = audio.Microphone(preroll_ms=0)

    mic.open()
    assert FakeInputStream.instances == [], "arming pinned the device"
    assert mic._stream is None
    assert "opened per press" in mic.info and "Fake Mic" in mic.info

    mic.begin()
    assert len(FakeInputStream.instances) == 1
    stream = FakeInputStream.instances[0]
    assert stream.started and not stream.closed
    mic._on_block(block(5), audio.BLOCK, None, None)
    pcm, overflowed = mic.end()

    assert pcm == block(5) and not overflowed
    assert stream.stopped and stream.closed, "the release left the device held"
    assert mic._stream is None


def test_preroll_zero_with_no_input_device_fails_at_arming(fake_sd):
    """AC-819. A missing device is a loud MicrophoneError at open()."""
    FakeSd.fail_query = True
    with pytest.raises(audio.MicrophoneError):
        audio.Microphone(preroll_ms=0).open()


def test_preroll_above_zero_still_holds_the_stream_open(fake_sd):
    """AC-819. The ring needs a live stream: that behaviour is unchanged."""
    mic = audio.Microphone(preroll_ms=400)

    mic.open()
    assert len(fake_sd.open_streams()) == 1
    mic.begin()
    mic._on_block(block(1), audio.BLOCK, None, None)
    mic.end()

    assert len(FakeInputStream.instances) == 1
    assert len(fake_sd.open_streams()) == 1, "end() closed a ring-backed stream"


def test_a_stream_that_fails_to_start_is_closed_and_the_press_is_not_capturing(
        fake_sd):
    """AC-820, AC-821. A constructed-but-unstarted stream still holds the
    device, and the failed press must not leave the capture flag set."""
    FakeInputStream.fail_start = True
    mic = audio.Microphone(preroll_ms=0)

    with pytest.raises(audio.MicrophoneError):
        mic.begin()

    assert len(FakeInputStream.instances) == 1
    assert FakeInputStream.instances[0].closed, "the failed stream leaked"
    assert mic._stream is None
    assert mic._capturing is False


def test_close_still_closes_the_stream_when_stop_raises(fake_sd):
    """AC-820. A lost device raises on stop(); close() must still run."""
    mic = audio.Microphone(preroll_ms=400)
    mic.open()
    FakeInputStream.fail_stop = True

    mic.close()

    assert FakeInputStream.instances[0].closed, (
        "stop() raised and the stream was never closed, so it holds the device")
    assert mic._stream is None


def test_the_ceiling_releases_the_device_but_keeps_the_captured_audio(fake_sd):
    """AC-820 (ceiling path). The key is still down, nothing is recording, so
    the device must not stay pinned until the key-up; end() still answers."""
    mic = audio.Microphone(preroll_ms=0, max_seconds=1)
    mic.begin()
    for _ in range(200):
        mic._on_block(block(7), audio.BLOCK, None, None)
    stream = FakeInputStream.instances[0]
    assert not stream.closed

    mic.release_if_full()

    assert stream.closed and mic._stream is None
    pcm, overflowed = mic.end()
    assert overflowed and len(pcm) > 0, "releasing at the ceiling lost the audio"


def test_release_if_full_does_nothing_before_the_ceiling_or_with_a_ring(fake_sd):
    """AC-820. Only a per-press capture that hit the ceiling is released."""
    per_press = audio.Microphone(preroll_ms=0, max_seconds=1)
    per_press.begin()
    per_press._on_block(block(1), audio.BLOCK, None, None)
    per_press.release_if_full()
    assert not FakeInputStream.instances[0].closed, "released before the ceiling"

    ring = audio.Microphone(preroll_ms=400, max_seconds=1)
    ring.open()
    ring.begin()
    for _ in range(200):
        ring._on_block(block(7), audio.BLOCK, None, None)
    ring.release_if_full()
    assert not FakeInputStream.instances[1].closed, (
        "a ring-backed stream was closed at the ceiling")


# --------------------------------------------------------------------------- #
# the state machine
# --------------------------------------------------------------------------- #
PROJECT = r"E:\PROJECTS\butler"


# The shipped table, as the daemon serves it: dictation on a bare bare-safe key
# and one terminal chord carrying its row's directory and agent. Written as WIRE
# ROWS rather than as `Binding`s, because that is what the agent is given and
# building the table is part of what these tests exercise (AC-140).
BINDING_ROWS = [
    {"chord": "numpad_multiply", "action": "dictate"},
    {"chord": "ctrl+numpad_multiply", "action": "terminal", "cwd": PROJECT,
     "agent": "pi", "label": "butler"},
]


class FakeDaemon:
    """A `Daemon` that answers a script and records every request."""

    def __init__(self, values=None, answer=(200, {"text": "hello there"}),
                 bindings=None):
        self._values = {"hotkeys_enabled": True, "cues": False,
                        "projects": [PROJECT], **(values or {})}
        self.answer = answer
        self.terminal_answer = (200, {"cwd": PROJECT, "agent": "pi"})
        self.posted: list[bytes] = []
        # What each terminal request ASKED FOR, not merely that one happened.
        # The agent forwards its binding's directory and agent and chooses
        # neither (`docs/shortcuts.md` 4.6), and recording the arguments is the
        # only way a test can tell "forwarded" from "invented".
        self.terminals: list[tuple[str, str]] = []
        self.bindings = BINDING_ROWS if bindings is None else bindings

    def get(self, key, default=None):
        return self._values.get(key, default)

    def dictate(self, pcm):
        self.posted.append(pcm)
        return self.answer

    def open_terminal(self, cwd="", agent=""):
        self.terminals.append((cwd, agent))
        return self.terminal_answer


class FakeMic:
    def __init__(self):
        self.began = 0
        self.pcm = b"\x01\x02" * (audio.SAMPLE_RATE // 2)   # 0.5 s
        self.overflow = False
        self.closed = False
        self.dropped_blocks = 0
        # A healthy stream by default: blocks arrived during the capture, and
        # the device delivered something a moment ago.
        self.live_blocks = 16
        self.quiet_for = 0.0
        self.reopened = 0
        self.info = "Fake Device (Fake API) 16000 Hz"
        self.holds_open = True
        self.calls: list[str] = []
        self.begin_error: Exception | None = None
        self.released_if_full = 0

    def begin(self):
        self.calls.append("begin")
        if self.begin_error is not None:
            raise self.begin_error
        self.began += 1

    def release_if_full(self):
        self.released_if_full += 1

    def end(self):
        return self.pcm, self.overflow

    def silent_for(self):
        return self.quiet_for

    def reopen(self):
        self.reopened += 1
        self.quiet_for = 0.0
        self.live_blocks = 16

    def close(self):
        self.closed = True


class FakeHook:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self.stopped = False
        self.tables: list = []

    def set_table(self, table):
        self.tables.append(table)

    def stop(self):
        self.stopped = True


@pytest.fixture
def agent(monkeypatch):
    """An `Agent` wired to fakes, with the paste path recorded not performed."""
    pasted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        deskd.paste_mod, "paste",
        lambda text, method, ignore_mods=(): pasted.append((text, method))
        or "pasted")
    a = deskd.Agent(FakeDaemon())
    a.mic, a.hook, a._enabled = FakeMic(), FakeHook(), True
    a._table = actions.Table(BINDING_ROWS)
    a._rows = list(BINDING_ROWS)
    a.pasted = pasted
    return a


def binding_for(action="dictate"):
    """The shipped table's binding for one action.

    Taken from the TABLE rather than built by hand, so a test cannot press a
    chord the daemon would never have served -- which is how a test ends up
    proving a code path that no configuration can reach.
    """
    table = actions.Table(BINDING_ROWS)
    for candidate in table.bindings:
        if candidate.action == action:
            return candidate
    raise AssertionError(f"no {action!r} binding in the shipped table")


def press(a, action="dictate"):
    a._handle(binding_for(action), hotkeys.DOWN)


def release(a, action="dictate"):
    a._handle(binding_for(action), hotkeys.UP)


def hold(a, ms=250):
    """Press, wait a real interval, release.

    The wall-clock wait is load-bearing rather than lazy: what separates "the
    microphone is dead" from "the key was brushed" is how long the key was
    DOWN, because blocks arrive every 32 ms and a 10 ms touch legitimately
    produces none. A test that pressed and released instantly would be a
    mistouch, whatever it set `live_blocks` to.
    """
    press(a)
    time.sleep(ms / 1000.0)
    release(a)


def test_a_held_key_captures_and_the_text_is_pasted(agent):
    """AC-113 end to end through the agent, minus Win32."""
    press(agent)
    assert agent.stage == deskd.RECORDING
    release(agent)

    assert agent.stage == deskd.IDLE, "the agent did not return to idle"
    assert agent.daemon.posted, "nothing was sent to the daemon"
    assert agent.pasted == [("hello there", "ctrl_v")]


def test_auto_repeat_does_not_restart_the_capture(agent):
    """A low-level hook sees a held key as a STREAM of key-downs.

    Handy rebuilt its whole capture stream on every synthesised repeat before
    they fixed this, many times a second.
    """
    press(agent)
    for _ in range(20):
        press(agent)

    assert agent.mic.began == 1, (
        f"auto-repeat restarted the capture {agent.mic.began} times, throwing "
        f"away everything said before the last repeat")


def test_a_release_without_a_press_does_nothing(agent):
    """The key was already down when hotkeys were switched on, or the press
    was suppressed by something else. Either way there is no capture."""
    release(agent)

    assert agent.daemon.posted == [] and agent.pasted == []


def test_a_press_during_processing_is_refused_not_queued(agent):
    """The stage machine's whole job.

    Asserted by driving the delivery INTO the press: a second dictation
    starting while the first is still being decoded would race it for the
    recognizer, which the daemon would refuse with a 409 -- but the capture
    would already have been thrown away.
    """
    seen = []

    def slow_dictate(pcm):
        seen.append(agent.stage)
        press(agent)                       # the owner taps again while waiting
        seen.append(agent.stage)
        return 200, {"text": "hello there"}

    agent.daemon.dictate = slow_dictate
    press(agent)
    release(agent)

    assert seen == [deskd.PROCESSING, deskd.PROCESSING], (
        f"a press during processing changed the stage: {seen}")
    assert agent.mic.began == 1, "a press during processing started a capture"
    assert agent.stage == deskd.IDLE, "the agent did not recover"


@pytest.mark.parametrize("answer, why", [
    ((422, {"error": "nothing was said in that clip"}), "a quiet clip"),
    ((409, {"error": "a turn is using the recognizer"}), "the recognizer busy"),
    ((502, {"error": "the recognizer could not decode that clip"}), "a failure"),
    ((0, {"error": "the daemon could not be reached"}), "the daemon down"),
    ((200, {"text": ""}), "an empty 200 the daemon should never send"),
])
def test_no_failure_ever_reaches_the_clipboard(agent, answer, why):
    """AC-115, every refusal the route can give plus one it should not.

    The empty-200 row is deliberate belt and braces: the daemon is asserted
    never to send one (`test_dictate.py`), and if it ever did, the agent must
    still not paste an empty string into the owner's document.
    """
    agent.daemon.answer = answer

    press(agent)
    release(agent)

    assert agent.pasted == [], f"{why} reached the clipboard"
    assert agent.stage == deskd.IDLE, f"{why} left the agent wedged"


def test_a_delivery_that_raises_still_returns_the_agent_to_idle(agent,
                                                                monkeypatch):
    """The `finally` in `_finish`, which is Handy's drop guard (D-1).

    A state machine stuck in PROCESSING refuses every future press, so the
    owner has a dead hotkey and nothing on screen to say why.
    """
    def boom(pcm):
        raise RuntimeError("the transport fell over")

    agent.daemon.dictate = boom
    press(agent)
    with pytest.raises(RuntimeError):
        release(agent)

    assert agent.stage == deskd.IDLE, (
        "an exception left the agent in PROCESSING, which is a hotkey that "
        "never works again")


def test_a_brushed_key_is_silent(agent):
    """Shorter than a finger can mean. No POST, no cue, no complaint."""
    agent.mic.pcm = b"\x01\x02" * 100          # ~12 ms

    press(agent)
    release(agent)

    assert agent.daemon.posted == [], "a mistouch was sent to the recognizer"
    assert agent.pasted == []


# --------------------------------------------------------------------------- #
# a microphone that went deaf (AC-126, AC-127, AC-128, docs/desk-audio.md)
# --------------------------------------------------------------------------- #
def test_a_capture_with_no_live_audio_says_the_MICROPHONE_failed(agent, capsys):
    """AC-126, and it is the defect that cost an hour on 2026-09-09.

    MEASURED on the owner's machine: the desk agent's stream died, the agent
    went on believing hotkeys were ON -- `apply_settings` only acts on a
    CHANGE, so nothing ever re-examined it -- and every capture returned
    exactly the stale lookback ring. `POST /api/dictate` then answered
    'nothing was said in that clip', which is a statement about SPEECH, and
    the agent repeated it. The truth was that the device delivered no audio at
    all, and the agent had that fact and threw it away.

    Windows agreed: with hotkeys ON and the agent running, the consent store
    showed NOBODY holding the microphone.
    """
    agent.mic.live_blocks = 0

    hold(agent)

    said = capsys.readouterr().out
    assert "no audio" in said.lower(), (
        f"a dead microphone was not named as one: {said!r}")
    assert "Fake Device" in said, (
        f"the failure does not say WHICH device was silent: {said!r}")
    assert agent.daemon.posted == [], (
        "a capture with no live audio was sent to the recognizer, which can "
        "only answer 'nothing was said' and hide the real failure")
    assert agent.stage == deskd.IDLE


def test_a_dead_microphone_is_REOPENED_so_the_next_press_works(agent):
    """AC-127. Handy does not do this and would show the same symptom forever.

    Its stop-path drain timeout fires only after a Stop, its first-chunk
    latency is logged on arrival and never policed, and an empty recording is
    handled cosmetically (`actions.rs:673-679`). A stream that opens and goes
    deaf is never reopened there. Butler must, because the measured failure
    left dictation dead until the daemon was restarted.
    """
    agent.mic.live_blocks = 0

    hold(agent)

    assert agent.mic.reopened == 1, (
        "the deaf stream was left open, so every future press fails the same "
        "way until somebody restarts the daemon")


def test_a_stream_that_went_quiet_is_reopened_BEFORE_the_capture(agent):
    """The owner's press should WORK, not merely fail with a better message.

    A live stream delivers a block every 32 ms whether or not anyone is
    speaking -- silence is data. So a stream that has produced nothing for a
    second is dead, and that is knowable at key-down, before any audio is
    needed.
    """
    agent.mic.quiet_for = 5.0

    press(agent)

    assert agent.mic.reopened == 1, (
        "a stream that had been silent for 5 s was used for the capture")
    assert agent.stage == deskd.RECORDING, "the capture did not start"


def test_a_brushed_key_is_not_reported_as_a_broken_microphone(agent, capsys):
    """The two failures must not be confused, and the SEPARATOR is the hold.

    A key touched for 10 ms legitimately produces no block, because blocks
    arrive every 32 ms. Calling that a dead microphone would cry wolf on every
    mistouch -- and a cue the owner learns to ignore is worse than no cue.
    """
    agent.mic.live_blocks = 0
    agent.mic.pcm = b""

    press(agent)
    release(agent)                      # released immediately: a brush

    said = capsys.readouterr().out
    assert said == "", f"a mistouch was reported as a failure: {said!r}"
    assert agent.mic.reopened == 0, "a mistouch tore down a healthy stream"


# --------------------------------------------------------------------------- #
# the terminal action (AC-118, AC-120)
# --------------------------------------------------------------------------- #
def test_the_chord_opens_a_terminal_and_asks_nothing(agent):
    """AC-136 through the agent, minus Win32.

    Nothing is captured and nothing is pasted: this action shares the KEY with
    dictation and shares nothing else. And nothing is ASKED -- no window, no
    dialog, no prompt -- which is the half of the owner's 2026-09-09 ruling
    that survived them asking for per-row terminals (`docs/shortcuts.md` D6).
    """
    press(agent, action="terminal")

    assert len(agent.daemon.terminals) == 1, (
        f"the terminal was not asked for: {agent.daemon.terminals}")
    assert agent.mic.began == 0 and agent.pasted == [], (
        "the terminal chord started a dictation")
    assert agent.stage == deskd.IDLE


def test_the_agent_FORWARDS_its_binding_and_chooses_nothing(agent):
    """The REPLACEMENT for a test that asserted the opposite, and the reason is
    written down rather than quietly relaxed (D-12, `docs/shortcuts.md` 4.6).

    Until 2026-09-09 this file asserted that `Daemon.open_terminal` took NO
    argument, because a `cwd` coming back would mean the desk agent had started
    deciding where a coding agent runs. The owner then asked for a pane of
    project rows with a chord each, so it decides again -- in exactly one
    narrow sense, and this test is what pins that sense down.

    The agent FORWARDS what its binding carries. It does not read a setting, it
    does not pick a default, and it still draws nothing: the row, its directory
    and its agent were all typed on the page and reached here through the
    binding table. Asserted by comparing what the daemon was asked for against
    what the BINDING held, rather than against a literal, so a test cannot pass
    by agreeing with a hardcoded path.
    """
    binding = binding_for("terminal")

    press(agent, action="terminal")

    assert agent.daemon.terminals == [(binding.cwd, binding.agent)], (
        f"the agent asked for {agent.daemon.terminals} rather than the "
        f"directory and agent its own binding carries "
        f"({binding.cwd!r}, {binding.agent!r}). Either it invented one or it "
        f"dropped one; both mean this process is deciding again.")
    assert "picker" not in dir(deskd), (
        "the desk agent imports a picker again. The folder chooser is "
        "`desk/browse.py`, a short-lived CHILD of the DAEMON: a dialog on this "
        "thread stops the settings poll and refuses every dictation until "
        "somebody dismisses it (owner's ruling, 2026-09-09)")
    assert not hasattr(deskd.Agent, "_choose_directory"), (
        "the agent chooses a directory again; it forwards its binding's")


@pytest.mark.parametrize("answer, why", [
    ((400, {"error": "terminal_cwd contains &"}), "a path cmd would re-parse"),
    ((502, {"error": "the terminal would not start"}), "a dead console"),
    ((0, {"error": "the daemon could not be reached"}), "the daemon down"),
])
def test_a_terminal_that_did_not_open_is_reported(agent, capsys, answer, why):
    """Every refusal the route can give reaches a log line and a cue.

    The failure this catches is the quiet one: a chord that opens no window
    and says nothing is indistinguishable from a hotkey that never fired
    (AC-7b), and the owner's next move would be to press it again.
    """
    agent.daemon.terminal_answer = answer

    press(agent, action="terminal")

    said = capsys.readouterr().out
    assert answer[1]["error"] in said, f"{why} was not reported: {said!r}"
    assert agent.stage == deskd.IDLE, f"{why} left the agent wedged"


def test_a_request_that_throws_does_not_wedge_the_hotkey(agent):
    """D-1. A stage left in PROCESSING refuses every future press.

    `Daemon._post` is written never to raise, so this drives the case it
    cannot produce: whatever the next edit puts on that path, the `finally`
    is what keeps the key alive.
    """
    def boom(cwd="", agent=""):
        raise RuntimeError("the transport fell over")

    agent.daemon.open_terminal = boom
    with pytest.raises(RuntimeError):
        press(agent, action="terminal")

    assert agent.stage == deskd.IDLE


# --------------------------------------------------------------------------- #
# the auto-repeat latch, which is keyed on the KEY and not on the action
# --------------------------------------------------------------------------- #
def test_a_held_chord_opens_ONE_terminal(agent):
    """The sibling of `test_auto_repeat_does_not_restart_the_capture`.

    A low-level hook sees a held key as a stream of key-downs, and the
    terminal branch held no state at all: `butler.log` since the restart on
    2026-09-09 carried 138 lines of the not-built message against 4 real
    dictations, because every repeat of one held chord fired it again. Twenty
    windows opening at once is the same defect with a coding agent in each.
    """
    press(agent, action="terminal")
    for _ in range(19):
        press(agent, action="terminal")

    assert len(agent.daemon.terminals) == 1, (
        f"a held chord opened {len(agent.daemon.terminals)} terminals")


def test_the_latch_is_cleared_by_a_release_the_hook_called_something_else(agent):
    """The quirk that decided how the latch is keyed. THE HOOK NO LONGER
    PRODUCES IT, and this is now the belt to that braces.

    `hotkeys.Hook._callback` used to classify each edge INDEPENDENTLY by polling
    Ctrl at that instant, so pressing with Ctrl and releasing after letting go
    of Ctrl produced `("terminal", down)` then `("dictate", up)`. Since
    `docs/shortcuts.md` the DOWN resolves and the UP REPLAYS the binding it
    resolved to, so a release can no longer arrive labelled as another action
    at all -- `test_desk_hotkeys.py` is where that is proved, at the hook.

    Kept because the property it asserts is the AGENT's and is still real: the
    latch is keyed on the KEY, so whatever an edge is labelled, a release ends
    what the same key started. Both chords here are the same physical key, which
    is why the test can still drive the old shape.
    """
    press(agent, action="terminal")
    release(agent, action="dictate")        # Ctrl was let go first
    press(agent, action="terminal")

    assert len(agent.daemon.terminals) == 2, (
        f"the second chord did nothing: the latch was never cleared "
        f"({agent.daemon.terminals})")


def test_a_release_the_hook_called_terminal_still_ends_a_capture(agent):
    """The same quirk from the other side, and this one loses a dictation.

    Hold the key alone, speak, then touch Ctrl before releasing. That release
    used to arrive labelled `terminal`, and if only a `dictate` release could
    end a capture the microphone would keep filling to the ceiling and the
    owner's words would never be sent. The hook now replays the DOWN's binding
    so it cannot arrive labelled that way, and this still asserts the agent's
    half: a release ends what the KEY started, whatever it is labelled.
    """
    press(agent)                            # a plain dictation
    assert agent.stage == deskd.RECORDING

    release(agent, action="terminal")       # Ctrl was touched before letting go

    assert agent.daemon.posted, (
        "the capture was never delivered, so the dictation was lost and the "
        "microphone kept filling")
    assert agent.stage == deskd.IDLE


def test_turning_hotkeys_off_clears_the_latch(agent):
    """Otherwise the key-up is never seen and the chord is dead forever.

    `_stop` uninstalls the hook, so a key held across the transition never
    delivers its release here. A latch left set would read every later press
    as auto-repeat.
    """
    press(agent, action="terminal")         # latch set, key still down
    agent.daemon._values["hotkeys_enabled"] = False
    agent.apply_settings()

    agent.mic, agent.hook, agent._enabled = FakeMic(), FakeHook(), True
    press(agent, action="terminal")

    assert len(agent.daemon.terminals) == 2, (
        "the hotkey was dead after being switched off and on again")


def test_turning_hotkeys_off_uninstalls_the_hook_and_closes_the_mic(agent):
    """AC-121. Off is not "ignore the key": it is nothing running."""
    agent.daemon._values["hotkeys_enabled"] = False

    agent.apply_settings()

    assert agent.hook is None and agent.mic is None
    assert agent._enabled is False


def test_an_unreachable_input_desktop_is_named_not_silently_dead(agent,
                                                                 monkeypatch,
                                                                 capsys):
    """AC-7b, for the failure this step actually hit.

    A hook installed on a session that does not own the input desktop
    SUCCEEDS and then receives nothing, forever, with no error. Measured on
    2026-09-09: a probe that had worked an hour earlier stopped, and the cause
    was that the process had ended up in session 2 while the console session
    was 1. Nothing must be opened in that state, and the reason must be said.
    """
    monkeypatch.setattr(hotkeys, "input_desktop_reachable",
                        lambda: (False, "session 2, console session 1"))
    agent._enabled = False
    agent.hook = agent.mic = None

    agent._start()

    said = capsys.readouterr().out
    assert "session 2" in said, f"the cause was not reported: {said!r}"
    assert agent.hook is None and agent.mic is None, (
        "a hook was installed on a desktop it can never receive from, and "
        "the microphone was opened for it")
    assert agent._enabled is False


# --------------------------------------------------------------------------- #
# the settings wire -- the one thing every double was hiding
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_agent_reads_settings_off_the_REAL_route(tmp_path,
                                                           monkeypatch):
    """D-18: the caller needs the assertion, and this caller had none.

    Every double in this file overrides `Daemon.get`, and the end-to-end probe
    sets `_values` directly, so nothing ever ran `Daemon.poll` against the
    actual `/api/settings` body. It read a `values` dict the route has never
    served -- `describe()` answers `{groups, settings}` with each setting
    carrying its own `value` -- so `hotkeys_enabled` was permanently False and
    the agent silently did nothing at all. Found by pointing it at a live
    daemon, not by any test.
    """
    from aiohttp.test_utils import TestClient, TestServer

    from daemon import butlerd, hub as hub_mod, modes, ports, settings
    from daemon import shortcuts as shortcuts_mod
    from daemon.adapters.asr_fake import FakeAsr
    from daemon.adapters.brain_fake import FakeBrain
    from conftest import QuietTts

    store = settings.Settings(tmp_path / "settings.json")
    store.set("hotkeys_enabled", True)
    shorts = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    shorts.upsert(sid="", path=PROJECT, label="butler",
                  chords={"pi": "ctrl+numpad_multiply"})
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                      modes=modes.ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"), settings=store,
                      shortcuts=shorts)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        agent_daemon = deskd.Daemon(str(client.make_url("")).rstrip("/"))
        # In a THREAD. `poll` is a blocking urllib call and the test server
        # runs on this event loop, so calling it inline is a deadlock the
        # agent itself can never have -- out there the loop belongs to the
        # daemon in another process.
        ok = await asyncio.to_thread(agent_daemon.poll)
        assert ok is True, "the agent could not read settings"
    finally:
        await client.close()

    assert agent_daemon.get("hotkeys_enabled") is True, (
        f"the agent read no value for the setting that turns it on; it saw "
        f"{len(agent_daemon.values)} settings")
    assert agent_daemon.get("dictate_paste") == "ctrl_v", (
        "the agent's other settings did not come through either")

    # The BINDINGS half, and it is the cheapest high-value assertion in this
    # feature for the same reason the settings half above was: every other
    # double in this file supplies `bindings` directly, so nothing else ever
    # runs `poll` against the real route. `bindings` rides beside `settings` as
    # a top-level key composed by the ROUTE from the shortcuts store
    # (`docs/shortcuts.md` 4.2), and a table that never arrives is a hotkey
    # that silently does nothing -- exactly the shape of the `values`-dict
    # defect this test was written for.
    rows = agent_daemon.bindings
    assert isinstance(rows, list) and rows, (
        f"the agent read no binding table off the real route, so no key would "
        f"ever be bound: {rows!r}")
    assert {"chord": shortcuts_mod.DEFAULT_DICTATE,
            "action": "dictate"} in rows, (
        f"the dictation binding is missing from the wire: {rows}")
    terminals = [r for r in rows if r.get("action") == "terminal"]
    assert len(terminals) == 1, f"expected one terminal binding, got {rows}"
    assert terminals[0]["cwd"] == PROJECT and terminals[0]["agent"] == "pi", (
        f"a terminal binding must carry the row's directory and agent, or the "
        f"agent has nothing to forward: {terminals[0]}")
    # And the table the agent will actually install parses, which is the one
    # thing "the shape is right" does not prove.
    assert len(actions.Table(rows)) == 2, (
        f"the wire table would be refused at bind time: {rows}")


def test_a_daemon_that_serves_no_bindings_leaves_the_table_ALONE(agent):
    """AC-140. Absent and empty are DIFFERENT facts.

    A daemon that serves no `bindings` key at all has not said "unbind every
    key on this machine" -- it has said nothing, and the running table is the
    only sensible answer. This is the same class as the defect that made the
    whole desk feature silently do nothing: `Daemon.poll` read a `values` dict
    the route has never served and therefore saw an empty settings map forever.
    """
    before = agent._table

    agent.daemon.bindings = None
    agent.apply_settings()

    assert agent._table is before, "a silent daemon unbound the owner's keys"
    assert agent.hook.tables == [], (
        f"a table was published to the hook anyway: {agent.hook.tables}")


@pytest.mark.parametrize("rows, why", [
    ([], "an empty table"),
    ([{"chord": "e", "action": "dictate"}], "a bare letter"),
    ([{"chord": "nonsense", "action": "dictate"}], "a key nobody has"),
    ([{"chord": "numpad_multiply", "action": "dictate"},
      {"chord": "numpad_multiply", "action": "terminal", "cwd": PROJECT,
       "agent": "pi"}], "one chord bound twice"),
    ([{"chord": "f13", "action": "explode"}], "an action nobody wrote"),
])
def test_a_bad_binding_table_changes_NOTHING_and_says_why(agent, capsys, rows,
                                                          why):
    """AC-140, and the refusal is the whole criterion.

    Clearing turns the owner's hotkeys off silently; defaulting binds a key
    nobody asked for. Both are AC-7b with a system-wide keyboard hook attached,
    and only one of them is recoverable from the page -- a bare `e` suppressed
    everywhere means the owner cannot type into the pane that would undo it.

    Asserted on the observable consequence (no table reached the hook) as well
    as on the log line, because a refusal that said so and published anyway
    would pass a message check on its own.
    """
    before = agent._table

    agent.daemon.bindings = rows
    agent.apply_settings()

    said = capsys.readouterr().out
    assert agent._table is before, f"{why} replaced the running table"
    assert agent.hook.tables == [], (
        f"{why} was published to the hook: {agent.hook.tables}")
    assert "WARNING" in said, f"{why} was refused in silence: {said!r}"


def test_a_GOOD_table_reaches_the_hook_without_stopping_anything(agent):
    """`apply_settings` is split in two, and this is why (`docs/shortcuts.md` 4.5).

    A binding change must not run `_start`/`_stop`. That would drop a held key's
    key-up, CLOSE AND REOPEN the audio stream -- the fragile operation
    `docs/desk-audio.md` is entirely about -- and log hotkeys OFF then ON on
    every keystroke of an edit on the page.
    """
    agent.daemon.bindings = [
        {"chord": "f13", "action": "dictate"},
        {"chord": "ctrl+f14", "action": "terminal", "cwd": PROJECT,
         "agent": "claude"},
    ]

    agent.apply_settings()

    assert len(agent.hook.tables) == 1, (
        f"the rebind did not reach the hook: {agent.hook.tables}")
    assert agent.hook.tables[0] is agent._table
    assert agent.mic.closed is False and agent.hook.stopped is False, (
        "a binding change tore down the microphone and the hook")
    assert agent._enabled is True


def test_the_install_line_is_generated_from_the_TABLE(agent, capsys):
    """AC-142. `deskd.py` used to hardcode "numpad * dictates".

    That becomes a lie the moment the table is configurable, and it is the
    owner's only record of which keys this process has taken over system-wide.
    """
    agent._enabled = False
    agent.hook = agent.mic = None
    agent.daemon.bindings = [
        {"chord": "ctrl+f15", "action": "terminal", "cwd": PROJECT,
         "agent": "opencode"},
    ]
    agent._rows = None
    agent._apply_bindings()

    class Mic(FakeMic):
        def open(self):
            pass

    agent.mic = None
    # `_start` opens a real Microphone and a real Hook, neither of which may
    # run in the suite, so the two constructors are the seam this drives.
    import desk.audio as audio_mod
    import desk.hotkeys as hotkeys_mod
    made = Mic()
    orig_mic, orig_hook = audio_mod.Microphone, hotkeys_mod.Hook

    class Hook(FakeHook):
        def __init__(self, table=None):
            super().__init__()

        def start(self, timeout=5.0):
            return True

    audio_mod.Microphone = lambda **kw: made
    hotkeys_mod.Hook = Hook
    try:
        agent._start()
    finally:
        audio_mod.Microphone, hotkeys_mod.Hook = orig_mic, orig_hook

    said = capsys.readouterr().out
    assert "ctrl+f15" in said and "opencode" in said, (
        f"the install line does not name what is now suppressed: {said!r}")
    assert "numpad" not in said, (
        f"the install line still names a key that is not bound: {said!r}")


def test_a_second_hold_chord_cannot_end_the_FIRST_ones_capture(agent):
    """`_capturing_vk`, and the interleaving it exists for.

    With two hold bindings: press A, press B (refused -- already RECORDING),
    release B. A single boolean would end A's capture and post a clip the owner
    is still speaking into.
    """
    agent._table = actions.Table([
        {"chord": "f13", "action": "dictate"},
        {"chord": "f14", "action": "dictate"},
    ])
    first, second = agent._table.bindings

    agent._handle(first, hotkeys.DOWN)
    assert agent.stage == deskd.RECORDING
    agent._handle(second, hotkeys.DOWN)          # refused: one capture at a time
    agent._handle(second, hotkeys.UP)

    assert agent.stage == deskd.RECORDING, (
        "the second key's release ended the first key's capture, so a clip the "
        "owner was still speaking into was posted")
    assert agent.daemon.posted == []

    agent._handle(first, hotkeys.UP)
    assert agent.stage == deskd.IDLE and agent.daemon.posted, (
        "the capturing key's own release did not deliver the clip")


def test_a_lost_key_up_is_recovered_AFTER_THE_CEILING(agent):
    """AC-145, and the mechanism is a TIMEOUT because nothing else can work.

    Windows SKIPS a hook on one timeout rather than removing it, so a single
    stall can lose exactly one edge -- and a lost key-up leaves the key latched
    and the stage in RECORDING, which is a hotkey that is dead forever: the
    microphone stops accumulating at its ceiling but nothing ends the capture,
    so every later press finds a stage that is not IDLE.

    **THE FIRST VERSION ASKED WHETHER THE KEY WAS STILL DOWN AND BROKE
    DICTATION COMPLETELY.** `GetAsyncKeyState` reports a SUPPRESSED key as UP --
    returning non-zero from a low-level hook is exactly Windows not updating the
    async key-state table [MEASURED 2026-09-09, 5 of 5 each way,
    `test/manual/probe_asyncstate.py`] -- so the 10 ms tick ended every capture
    inside 200 ms and every clip was the lookback ring alone. Every key this
    agent waits on is a key it suppresses, so no poll can ever work.

    The first assertion here is the one with teeth: a capture that has not run
    past the ceiling must be left ALONE, however many ticks go by.
    """
    press(agent)
    assert agent.stage == deskd.RECORDING

    for _ in range(50):
        agent.check_stuck()
    assert agent.stage == deskd.RECORDING, (
        "a capture the owner was still speaking into was ended by the "
        "stuck-key net, which is the regression that broke dictation")
    assert agent.daemon.posted == []

    # Past the ceiling plus its slack, with no key-up ever delivered.
    ceiling = agent.daemon.get("dictate_max_seconds", 120)
    agent._began_at -= ceiling + deskd.STUCK_SLACK_S + 1
    agent.check_stuck()

    assert agent.stage == deskd.IDLE and agent.daemon.posted, (
        "the lost key-up was never recovered, so the hotkey is dead until "
        "hotkeys are toggled off and on")
    assert agent._held == {}, "the latch survived, so the next press is a repeat"


def test_the_stuck_net_NEVER_asks_whether_a_key_is_down(agent):
    """The regression, pinned at the one line that would bring it back.

    A suppressed key's physical state is unreadable, so a net that polls it ends
    every capture immediately -- and the symptom is not an error: the owner gets
    "nothing was said in that clip" for a clip they spoke into, which is a
    sentence about SPEECH. Asserted by grep, because the correct behaviour is
    the ABSENCE of a call and there is nothing to observe.
    """
    source = Path(deskd.__file__).read_text(encoding="utf-8")
    whole = source.split("def check_stuck")[1].split("\n    def ")[0]
    # The DOCSTRING is dropped before the grep, deliberately. It names the
    # forbidden call because explaining the rule requires naming it, and a gate
    # that could not tell prose from code would force the explanation out of the
    # one place a reader will look (D-12 from the other side).
    code = whole.split('"""')[2]
    assert "GetAsyncKeyState" not in code and "key_down" not in code, (
        f"check_stuck asks for a key state again: {code!r}")
    assert not hasattr(hotkeys, "key_down"), (
        "hotkeys.key_down is back. It cannot answer for any key this agent "
        "binds, because a bound key is a suppressed key (MEASURED 2026-09-09).")


def test_the_capturing_chords_own_modifiers_are_not_injected_UP(agent,
                                                                monkeypatch):
    """The second contract this feature changes on purpose (D-12).

    `paste.wait_for_modifiers` injects Ctrl-up for every modifier it finds held,
    which was correct while the only chord with a modifier pasted nothing. A
    HOLD binding on `ctrl+f13` makes it the normal case: the owner IS holding
    Ctrl at the moment the transcript lands, and injecting a key-up for a key
    they are pressing leaves Windows and the application disagreeing about it.
    """
    seen: list = []
    monkeypatch.setattr(
        deskd.paste_mod, "paste",
        lambda text, method, ignore_mods=(): seen.append(list(ignore_mods))
        or "pasted")
    agent._table = actions.Table([{"chord": "ctrl+f13", "action": "dictate"}])
    held = agent._table.bindings[0]

    agent._handle(held, hotkeys.DOWN)
    agent._handle(held, hotkeys.UP)

    assert seen == [["ctrl"]], (
        f"the capturing chord's own modifier did not reach the paste path: "
        f"{seen}. It would then be injected UP while the owner held it.")


# --------------------------------------------------------------------------- #
# per-press microphone: the agent side (AC-820..AC-823)
# --------------------------------------------------------------------------- #
@pytest.fixture
def tones(monkeypatch, agent):
    """Record every cue, and its order against the mic's own calls."""
    played: list[tuple[int, int]] = []

    def record(tone, enabled=True):
        played.append(tone)
        agent.mic.calls.append(f"cue{tone[0]}")

    monkeypatch.setattr(deskd, "cue", record)
    return played


def test_a_failed_open_at_the_press_cues_failure_posts_nothing_and_retries(
        agent, tones, capsys):
    """AC-821. The device would not open: no listening cue, no capture, no
    POST, no paste, and the NEXT press tries again."""
    agent.mic.begin_error = audio.MicrophoneError("it would not open")

    press(agent)

    assert agent.stage == deskd.IDLE
    assert agent._capturing_vk is None
    assert tones == [deskd.CUE_FAILED], f"cues were {tones}"
    assert agent.daemon.posted == [] and agent.pasted == []
    assert "WARNING" in capsys.readouterr().out
    release(agent)                      # key-up of a press that never started
    assert agent.stage == deskd.IDLE and agent.daemon.posted == []

    agent.mic.begin_error = None
    press(agent)
    assert agent.stage == deskd.RECORDING, "the next press did not retry"


def test_the_device_is_opened_BEFORE_the_listening_cue(agent, tones):
    """AC-821. The cue is a blocking 120 ms beep: the owner must hear it only
    once the device is ready."""
    press(agent)

    assert agent.mic.calls == ["begin", "cue880"], agent.mic.calls


def test_the_stuck_net_asks_the_microphone_to_release_at_the_ceiling(agent):
    """AC-820 (ceiling path). check_stuck runs every tick while RECORDING."""
    press(agent)
    agent.check_stuck()

    assert agent.mic.released_if_full == 1


def test_a_per_press_capture_with_no_audio_is_named_but_reopens_nothing(
        agent, capsys):
    """AC-823. The next press opens a fresh stream anyway; a reopen would log
    'open again' about a device nobody holds."""
    agent.mic.holds_open = False
    agent.mic.live_blocks = 0

    hold(agent)

    said = capsys.readouterr().out
    assert "NO AUDIO" in said
    assert agent.mic.reopened == 0
    assert "open again" not in said


def test_a_per_press_microphone_is_not_asked_whether_the_stream_went_deaf(agent):
    """AC-819. There is no stream between presses to be silent."""
    agent.mic.holds_open = False
    agent.mic.quiet_for = 99.0

    press(agent)

    assert agent.mic.reopened == 0 and agent.stage == deskd.RECORDING


@pytest.fixture
def real_agent(agent, fake_sd):
    """An agent whose microphone is the REAL class on the fake device."""
    agent.mic = audio.Microphone(preroll_ms=0, max_seconds=1)
    agent.mic.open()
    return agent


def test_end_to_end_the_device_is_held_only_between_press_and_release(
        real_agent, fake_sd):
    """AC-819, AC-820."""
    assert fake_sd.open_streams() == []
    press(real_agent)
    assert len(fake_sd.open_streams()) == 1
    real_agent.mic._on_block(block(3), audio.BLOCK, None, None)
    release(real_agent)
    assert fake_sd.open_streams() == [], "the release left the device held"


def test_the_stuck_key_recovery_leaves_no_open_stream(real_agent, fake_sd):
    """AC-820. A lost key-up is recovered past the ceiling and must release."""
    press(real_agent)
    real_agent._began_at -= 120 + deskd.STUCK_SLACK_S + 1   # daemon ceiling default

    real_agent.check_stuck()

    assert real_agent.stage == deskd.IDLE
    assert fake_sd.open_streams() == []


def test_hotkeys_off_during_a_capture_leaves_no_open_stream(real_agent, fake_sd):
    """AC-820. _stop() while RECORDING."""
    press(real_agent)
    assert len(fake_sd.open_streams()) == 1

    real_agent._stop()

    assert fake_sd.open_streams() == []


def test_the_ceiling_releases_the_device_while_the_key_is_still_down(
        real_agent, fake_sd):
    """AC-820 (ceiling path) through the agent tick."""
    press(real_agent)
    for _ in range(200):
        real_agent.mic._on_block(block(7), audio.BLOCK, None, None)

    real_agent.check_stuck()

    assert fake_sd.open_streams() == [], "the ceiling left the device pinned"
    assert real_agent.stage == deskd.RECORDING     # key-up still owed
