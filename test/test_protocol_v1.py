"""Acceptance tests for Butler's daemon-satellite protocol v1 (plan.md 4.3).

These are ATDD tests: they encode the CONTRACT, not today's code. Every one of
them is expected to be red until Phase 1 steps 1-5 land (plan.md section 5).
The contract they pin down:

  * hello/ready handshake, versioned (4.3)
  * the DAEMON mints turn ids at capture_start; a client cannot name a turn
  * the pre-roll flush carries the first syllables of the command (AC-1a)
  * there is no check_turn -- endpointing is server-side (4.2)
  * an empty capture still ends the turn and re-arms (AC-6, D-7)
  * every terminal path re-arms (AC-2, D-1)
  * an abandoned turn never produces more wire traffic (AC-5, D-6)

Everything here asserts on OBSERVABLE WIRE BEHAVIOUR. No daemon module is
imported, nothing internal is inspected: a test that reaches inside the daemon
cannot tell the difference between "the protocol is right" and "the object
graph happens to look right today", and only the first one is the promise.
"""
from __future__ import annotations

import asyncio
import json
import time
import wave

import aiohttp
import pytest

from conftest import REPO, Satellite, silence_pcm, tone_pcm

# --------------------------------------------------------------------------- #
# Timeouts. Named, not magic: every one is a claim about the contract.
# --------------------------------------------------------------------------- #
HANDSHAKE_TIMEOUT_S = 5.0    # hello -> ready is local and instant
WAKE_TIMEOUT_S = 5.0         # arbitration window is ~300 ms (4.2); 5 s is generous
FINAL_TIMEOUT_S = 15.0       # covers the 6 s endpoint hard cap plus slack (4.2)
SPEAK_TIMEOUT_S = 20.0       # final -> brain -> Kokoro -> speak_end
QUIET_S = 2.0                # how long we watch to prove a message NEVER arrives

# A turn number the client has no right to name. Chosen absurdly high so it can
# never collide with a server counter that starts at 0 or 1.
CLIENT_BOGUS_TURN = 999_999

ASSETS = REPO / "test" / "assets"
WAKE_WAV = ASSETS / "wake_then_command.wav"
MANIFEST = ASSETS / "manifest.json"
ASSETS_MISSING = not (WAKE_WAV.exists() and MANIFEST.exists())


# --------------------------------------------------------------------------- #
# Helpers. conftest owns the fixtures and the Satellite; these are the two
# things it does not have: "start a turn" and "prove nothing arrived".
# --------------------------------------------------------------------------- #
async def start_turn(sat: Satellite, score: float = 0.92) -> int:
    """Report a wake candidate and return the turn id the SERVER minted."""
    await sat.send(type="wake_candidate", word="hey_butler", score=score, ts_ms=int(time.time() * 1000))
    cs = await sat.next("capture_start", timeout=WAKE_TIMEOUT_S)
    assert "turn" in cs, f"capture_start must carry the server-minted turn id: {cs}"
    return cs["turn"]


async def drain(sat: Satellite, seconds: float) -> list[dict]:
    """Collect every text message that arrives in the next `seconds`.

    Used only for negative assertions ("this must NEVER be sent"). Positive
    assertions always wait for a specific message via Satellite.next().
    """
    deadline = time.monotonic() + seconds
    collected: list[dict] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return collected
        try:
            msg = await asyncio.wait_for(sat.ws.receive(), timeout=remaining)
        except asyncio.TimeoutError:
            return collected
        if msg.type == aiohttp.WSMsgType.BINARY:
            sat.audio_chunks.append(msg.data)
            continue
        if msg.type is not aiohttp.WSMsgType.TEXT:
            return collected  # socket closed; the caller asserts on that itself
        m = json.loads(msg.data)
        sat.seen.append(m)
        collected.append(m)


async def next_turn_completes(sat: Satellite) -> int:
    """Prove the session re-armed: a fresh wake is honoured and its turn ends.

    Returns the new turn id. This is the shared tail of every AC-2 case.
    """
    turn = await start_turn(sat)
    await sat.send_pcm(tone_pcm(500))
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint")
    final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
    assert final.get("turn") == turn, (
        f"the re-armed turn must finalize into its own id {turn}, got {final}")
    return turn


def load_wav_pcm(path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000, f"{path} must be 16 kHz"
        assert w.getnchannels() == 1, f"{path} must be mono"
        assert w.getsampwidth() == 2, f"{path} must be int16"
        return w.readframes(w.getnframes())


def manifest_entry(filename: str) -> dict:
    """The manifest.json record for one asset (it holds a `fixtures` list)."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest.get("fixtures", []):
        if entry.get("filename") == filename:
            return entry
    raise AssertionError(
        f"test/assets/manifest.json has no entry for {filename}; got "
        f"{[e.get('filename') for e in manifest.get('fixtures', [])]}")


def expected_first_command_word(entry: dict) -> str:
    """The word AC-1a says must survive the arbitration round trip.

    Fails loudly rather than guessing: a pre-roll test that does not know what
    word it is looking for cannot fail for the right reason.
    """
    for key in ("command_transcript", "first_command_word", "command_text",
                "transcript"):
        value = entry.get(key)
        if value:
            return str(value).strip().split()[0].strip(".,!?;:").lower()
    raise AssertionError(
        "the manifest entry must name the spoken command (one of: "
        "command_transcript, first_command_word, command_text, transcript) so "
        "AC-1a can assert the first word survived the pre-roll flush; got keys "
        f"{sorted(entry)}")


# --------------------------------------------------------------------------- #
# 1. Handshake
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_hello_returns_ready_with_proto_and_mode(daemon):
    """Protocol v1 handshake (plan.md 4.3).

    Catches a daemon that has no versioned handshake at all -- today server.py
    only answers a `config` message and never sends `mode` or `proto`, so a
    satellite has no way to learn what protocol or permission mode it joined.
    """
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{daemon}/ws") as ws:
            sat = Satellite(ws)
            await sat.send(type="hello", proto=1, device_id="test-handshake",
                           name="handshake", caps={"wake": True, "tts": True})
            ready = await sat.next("ready", timeout=HANDSHAKE_TIMEOUT_S)

    assert ready.get("proto") == 1, (
        f"ready must echo the negotiated protocol version 1: {ready}")
    assert "mode" in ready, (
        f"ready must carry the CURRENT server-side permission mode (4.6/D-2): "
        f"{ready}")
    assert isinstance(ready["mode"], str) and ready["mode"], (
        f"mode must be a non-empty mode name, got {ready.get('mode')!r}")


@pytest.mark.asyncio
async def test_ready_names_all_three_engines(daemon):
    """AC-42, and D-18 in its usual shape.

    The conformance suites prove every adapter HAS an `info`. Only this
    proves the daemon SENDS all three, which is a different claim and the
    one that was false: `_hello` reported `stt` and nothing else, so the HUD
    could say which recognizer was loaded while staying silent about which
    brain was answering. That gap matters most for the brain, because
    `brain_fake` matches five substrings and says "Let me think about that"
    to everything else -- indistinguishable, from the room, from a real model
    having a bad day.

    Asserted on the WIRE rather than on hub state: an info dict that never
    reaches the satellite is not shown to anybody.
    """
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{daemon}/ws") as ws:
            sat = Satellite(ws)
            await sat.send(type="hello", proto=1, device_id="test-engines",
                           name="engines", caps={"wake": True, "tts": True})
            ready = await sat.next("ready", timeout=HANDSHAKE_TIMEOUT_S)

    for key in ("stt", "tts", "brain"):
        assert key in ready, f"ready must name the {key} engine: {ready}"
        assert isinstance(ready[key], dict), (
            f"{key} must be the adapter's own info dict, got "
            f"{ready[key]!r}")
    # Only the shape the page actually reads is required. Beyond this each
    # adapter reports what it likes, and the page renders "?" for anything
    # missing rather than asserting a key set (web/app.js "ready" case).
    for key in ("tts", "brain"):
        assert ready[key].get("engine"), (
            f"{key} info must name its engine: {ready[key]}")


# --------------------------------------------------------------------------- #
# 2. The daemon mints turn ids
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_daemon_mints_turn_id_and_ignores_client_supplied_turn(satellite):
    """Turn-id ownership inverts to the server (plan.md 4.3, D-6).

    Catches exactly the spike's behaviour that is still in server.py today:
    `finalize` accepts whatever integer the client sends and echoes it back on
    `final`, so any device can name a turn it does not own. Here the client
    stamps CLIENT_BOGUS_TURN on two different messages; if that number ever
    appears on the wire, the server did not mint the id.
    """
    sat = satellite
    server_turn = await start_turn(sat)
    assert server_turn != CLIENT_BOGUS_TURN

    # The client stamps its own number on every turn-scoped message it sends.
    await sat.send(type="preroll", turn=CLIENT_BOGUS_TURN, bytes=0, ms=0,
                   lookback=0)
    await sat.send_pcm(tone_pcm(400))
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint", turn=CLIENT_BOGUS_TURN)

    final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
    assert final.get("turn") == server_turn, (
        f"final must carry the SERVER's turn {server_turn}, not the client's: "
        f"{final}")

    stamped = [m for m in sat.seen if m.get("turn") == CLIENT_BOGUS_TURN]
    assert stamped == [], (
        f"the daemon echoed a client-minted turn id: {stamped}")

    # And the counter is the server's: a second turn is a different turn.
    second_turn = await start_turn(sat)
    assert second_turn != server_turn, (
        "consecutive turns must get distinct server-minted ids, both were "
        f"{server_turn}")


# --------------------------------------------------------------------------- #
# 3. Pre-roll flush
# --------------------------------------------------------------------------- #
@pytest.mark.needs_audio
@pytest.mark.needs_gpu
@pytest.mark.slow
@pytest.mark.skipif(
    ASSETS_MISSING,
    reason="test/assets/wake_then_command.wav + manifest.json not generated yet")
@pytest.mark.asyncio
async def test_preroll_flush_puts_the_first_command_word_in_the_transcript(
        real_satellite):
    """AC-1a: the arbitration window costs latency but never audio.

    Catches the by-construction data loss described in 4.2: nothing streams
    while resting and PCM is illegal before capture_start, so unless the daemon
    accepts a `preroll` header plus its declared bytes as the FIRST audio of the
    turn, every command loses its opening syllables. The flush here is cut 150
    ms INTO the first word, so if the buffered bytes are dropped the word is
    truncated and cannot appear in the final transcript.
    """
    sat = real_satellite
    entry = manifest_entry(WAKE_WAV.name)
    pcm = load_wav_pcm(WAKE_WAV)
    wake_end = int(entry["wake_end_sample"])
    command_start = int(entry["command_start_sample"])
    first_word = expected_first_command_word(entry)

    # The satellite buffers from the moment its local detector fires (wake_end)
    # and keeps buffering until capture_start comes back.
    #
    # The cut must land AFTER the whole first word, not part-way through it.
    # An earlier version cut 150 ms in, and a mutation making the daemon
    # DISCARD the pre-roll still passed: the recognizer read "delete" from the
    # ~250 ms of the word left in the live audio. A test that survives the
    # exact defect it is named after is not a test (D-13). 550 ms puts the
    # whole word in the pre-roll, and the mutation fails as it should.
    FIRST_WORD_MS = 550
    flush_end = min(command_start + int(FIRST_WORD_MS / 1000 * 16000),
                    len(pcm) // 2)
    preroll = pcm[wake_end * 2:flush_end * 2]
    live = pcm[flush_end * 2:]
    assert preroll, "the fixture must place the command AFTER the wake phrase"

    turn = await start_turn(sat)
    # The flush starts AT the fire (wake_end), so none of it is lookback:
    # every byte is the owner's turn (AC-800).
    await sat.send(type="preroll", turn=turn, bytes=len(preroll),
                   ms=round(len(preroll) / 2 / 16), lookback=0)
    await sat.send_pcm(preroll)
    await sat.send_pcm(live)
    await sat.send_pcm(silence_pcm(1200))
    await sat.send(type="capture_end_hint")

    final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
    assert final.get("turn") == turn
    assert not [m for m in sat.seen if m.get("type") == "error"], (
        f"the daemon rejected the pre-roll flush: "
        f"{[m for m in sat.seen if m.get('type') == 'error']}")
    text = (final.get("text") or "").lower()
    assert first_word in text, (
        f"AC-1a: the first command word {first_word!r} was clipped -- the "
        f"pre-roll bytes never reached the ASR session. final text: {text!r}")


# --------------------------------------------------------------------------- #
# 4. No client endpointing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_check_turn_is_not_part_of_protocol_v1(satellite):
    """Endpointing is server-side (plan.md 4.2, 4.3).

    Catches the spike's client-authored endpointing surviving the fork: today
    server.py answers `check_turn` with a `turn` verdict, which means a
    satellite -- or a native mic source with no page at all (AC-24) -- decides
    when a turn ends. The daemon must ignore or reject the message, never act
    on it.
    """
    sat = satellite
    await sat.send(type="check_turn", id="probe-1")
    seen = await drain(sat, QUIET_S)

    verdicts = [m for m in seen if m.get("type") == "turn"]
    assert verdicts == [], (
        f"the daemon acted on a client check_turn: {verdicts}")
    # Ignoring silently and rejecting loudly are both acceptable; answering is
    # not. If it rejects, it must not also tear down the connection.
    assert not sat.ws.closed, (
        "an unsupported message must not kill the satellite's socket (D-1)")


# --------------------------------------------------------------------------- #
# 5. Empty capture still ends the turn
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_capture_emits_empty_final_and_rearms(satellite):
    """AC-6 / D-7: the final is the turn's END, not its text.

    Catches a daemon that only emits `final` when there are words: the turn
    claim would then leak, the session would sit in CAPTURING forever, and the
    next wake would be refused as busy. The re-arm half of the assertion is
    what makes that visible from the wire.
    """
    sat = satellite
    turn = await start_turn(sat)
    await sat.send_pcm(silence_pcm(2000))

    final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
    assert final.get("turn") == turn, f"final belongs to turn {turn}: {final}"
    assert (final.get("text") or "").strip() == "", (
        f"an empty capture must finalize with empty text, got {final!r}")

    # Re-armed: a wake in the following second is honoured with a NEW turn.
    next_id = await start_turn(sat)
    assert next_id != turn, (
        "the session did not re-arm: the second wake reused the dead turn id")


# --------------------------------------------------------------------------- #
# 6. Re-arm after each terminal path
# --------------------------------------------------------------------------- #
#
# NOT COVERED HERE, and deliberately so: the two remaining AC-2 terminal paths
# are "TTS synth failure" and "chat-tier timeout / malformed Reflex output".
# Both need fault injection that does not exist yet -- there is no
# BUTLER_TEST_FAIL_TTS knob on the Kokoro adapter and no way to point the
# Conversation tier at a hanging mock endpoint from a test. Those two cases
# belong with the fake `Tts` and fake `Brain` adapters (plan.md 4.10, AC-7a);
# writing them against imaginary knobs would produce tests that fail for a
# reason nobody can act on.
@pytest.mark.parametrize("terminal_path",
                         ["normal_reply", "empty_utterance", "malformed_message"])
@pytest.mark.asyncio
async def test_session_rearms_after_terminal_path(satellite, terminal_path):
    """AC-2 / D-1: every terminal path returns to wake-listening.

    Catches the inherited Phoenix defect -- an error or an odd turn latching the
    microphone off, so the NEXT wake is ignored and only a page refresh or a
    daemon restart recovers. Each case drives one terminal path to its end and
    then proves a fresh turn still completes on the same socket.
    """
    sat = satellite

    if terminal_path == "normal_reply":
        turn = await start_turn(sat)
        await sat.send_pcm(tone_pcm(600))
        await sat.send_pcm(silence_pcm(1200))
        await sat.send(type="capture_end_hint")
        final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
        assert final.get("turn") == turn
        end = await sat.next("speak_end", timeout=SPEAK_TIMEOUT_S)
        assert end.get("turn") == turn, (
            f"speak_end must name the turn it finishes: {end}")

    elif terminal_path == "empty_utterance":
        turn = await start_turn(sat)
        await sat.send_pcm(silence_pcm(2000))
        final = await sat.next("final", timeout=FINAL_TIMEOUT_S)
        assert final.get("turn") == turn
        assert (final.get("text") or "").strip() == ""

    elif terminal_path == "malformed_message":
        turn = await start_turn(sat)
        await sat.ws.send_str("{ this is not json")
        await sat.send(type="no_such_message_type", nonsense=True)
        err = await sat.next("error", timeout=HANDSHAKE_TIMEOUT_S)
        assert err.get("message"), (
            f"a malformed frame must be reported loudly, never swallowed: {err}")
        assert not sat.ws.closed, (
            "a malformed client frame must not drop the satellite's socket")
        del turn  # the turn may end however the daemon likes; re-arm is the claim

    else:  # pragma: no cover - parametrize guards this
        raise AssertionError(f"unknown terminal path {terminal_path}")

    await next_turn_completes(sat)


# --------------------------------------------------------------------------- #
# 7. Stale turn discard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_abandoned_turn_produces_no_further_messages(satellite):
    """AC-5 / D-6: a late answer for an abandoned turn is never spoken.

    Catches the stream-ownership defect of an earlier prototype: once a barge-in mints a new
    turn, anything still in flight for the old one (a slow Conversation reply, a
    queued TTS segment) must be dropped server-side. Asserting the ABSENCE of
    old-turn traffic is the only observable form of this -- a client-side
    discard would let the daemon keep talking about a turn nobody is listening
    to, and a test that only checked the client would pass while the bug lived.
    """
    sat = satellite
    abandoned = await start_turn(sat)
    await sat.send_pcm(tone_pcm(700))
    await sat.send(type="capture_end_hint")

    # Barge-in: the user says the wake word while the daemon is thinking.
    fresh = await start_turn(sat)
    assert fresh != abandoned, (
        f"barge-in must mint a NEW turn, got {fresh} twice")

    late = await drain(sat, QUIET_S)
    stale = [m for m in late if m.get("turn") == abandoned]
    assert stale == [], (
        f"the daemon kept emitting for the abandoned turn {abandoned}: {stale}")
