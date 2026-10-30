"""WHERE DOES THE END PHRASE GO -- `committed` or `tentative`?

The end-word policy matches `partial.committed` only
(`pipeline.py`: `self._transcript = partial.committed or self._transcript`),
while the PAGE draws both halves. So a phrase sitting in the volatile suffix
is one the owner SEES and the policy never sees -- "it captures it, but it
never sends it". The engine's own API says the split exists:
`StreamText.committed` is append-only and stable, `tentative` is the volatile
suffix.

Not a test -- nothing asserts, and `manual/` is excluded by `pytest.ini`.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_endword_stream.py

Feeds a real utterance whose LAST WORDS are an end phrase, then feeds SILENCE
-- because the policy also needs `END_WORD_PAUSE_MS` (600 ms) of quiet after
the phrase, so the question is not only "does it commit" but "does it commit
while the room is still quiet". Prints one line per change, then the final.

Audio: Portuguese is synthesized by the repo's own kokoro (`pf_dora`), English
by Windows SAPI, because `test/assets/` holds no clip ending in a phrase.
Loads the real GGUF; starts no daemon and touches no port.
"""
from __future__ import annotations

import os
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test" / "manual"))

from daemon import ports                                       # noqa: E402
from daemon.adapters import tts_kokoro                         # noqa: E402,F401
from daemon.engines import stt_engine as S                     # noqa: E402
from daemon.endpointing import EndWordEnd                      # noqa: E402
from spike_language_id import resample_24k_to_16k              # noqa: E402

SLUG = "nemotron-3.5-asr-streaming-0.6b"
SAMPLE_RATE = 16000
CHUNK = 1024                     # 32 ms, the pipeline's own frame size
SILENCE_MS = 2000                # far more than END_WORD_PAUSE_MS (600)

# The owner's laptop list, verbatim from its state/settings.json.
PHRASES = ["over to you", "send it", "go ahead", "over", "pode ir"]
PT_LINE = "Por favor, verifique a minha agenda e depois pode ir."
EN_LINE = "Please check my calendar and then send it."


def phrase_in(text: str) -> str:
    t = (text or "").lower()
    # The policy's own rule, asked directly, so this file cannot disagree with
    # the thing it is measuring.
    hits = [p for p in PHRASES if p in t]
    return ",".join(hits) if hits else "-"


def portuguese_pcm() -> bytes:
    import asyncio

    async def go():
        tts = ports.build(ports.TTSS, "kokoro")
        await tts.warm()
        voice = ports.voice_ids(tts.info, "pt")[0]
        chunks = [c async for c in tts.synthesize(PT_LINE, voice, handle=("spike",))]
        print(f"[spike] portuguese audio from {voice!r}")
        return resample_24k_to_16k(b"".join(chunks))

    return asyncio.run(go())


def english_pcm() -> bytes:
    """Windows SAPI to a 16 kHz mono WAV, then its frames."""
    import subprocess
    out = Path(os.environ.get("TEMP", ".")) / "spike_endword_en.wav"
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$i = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        f"{SAMPLE_RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{out}', $i); "
        f"$s.Speak('{EN_LINE}'); $s.Dispose()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   check=True, capture_output=True)
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE, w.getframerate()
        return w.readframes(w.getnframes())


def stream(pcm: bytes, language, label: str, realtime: bool = True) -> None:
    print(f"\n=== {label}  (language={language!r}"
          f"{', fed at wall-clock speed' if realtime else ', fed as fast as possible'}) ===")
    state = {"t": 0, "last_c": None, "last_t": None}
    first_any = {"ms": None}
    first_committed = {"ms": None}
    finals: list = []
    t_start = time.time()

    def emit(kind, p):
        if kind == "error":
            print(f"  !! ENGINE ERROR at {int((time.time()-t_start)*1000)}ms: "
                  f"{p.get('message')}")
            return
        if kind == "final":
            finals.append(p)
            return
        if kind != "partial":
            return
        c, t = p.get("committed", ""), p.get("tentative", "")
        if (c, t) == (state["last_c"], state["last_t"]):
            return
        state["last_c"], state["last_t"] = c, t
        now = int((time.time() - t_start) * 1000)     # REAL wall clock
        if first_any["ms"] is None and phrase_in(c + " " + t) != "-":
            first_any["ms"] = now
        if first_committed["ms"] is None and phrase_in(c) != "-":
            first_committed["ms"] = now
        print(f"  t={now:>5}ms  committed={c[-42:]!r}")
        print(f"            tentative={t!r}   phrase: committed={phrase_in(c)} "
              f"anywhere={phrase_in(c + ' ' + t)}")

    sess = S.StreamingSession(emit=emit, slug=SLUG, quant=None, backend="cpu",
                              latency="accurate", language=language)
    try:
        # WALL-CLOCK PACING IS THE POINT. `feed()` only queues
        # (stt_engine.StreamingSession), so pushing an utterance in faster than
        # it was spoken lets the model see the WHOLE thing before its first
        # commit -- which flatters `committed` and hides the volatile suffix
        # this file exists to look at. 32 ms of audio per 32 ms of sleep is
        # what the microphone does.
        for i in range(0, len(pcm), CHUNK):
            state["t"] = int(i / 2 / SAMPLE_RATE * 1000)
            sess.feed(pcm[i:i + CHUNK])
            if realtime:
                time.sleep(CHUNK / 2 / SAMPLE_RATE)
        # THE PAUSE. A real turn goes quiet here, and the policy needs 600 ms
        # of it AFTER the phrase is in `committed`.
        zeros = b"\x00" * CHUNK
        for k in range(int(SILENCE_MS / 32)):
            state["t"] = int(len(pcm) / 2 / SAMPLE_RATE * 1000) + k * 32
            sess.feed(zeros)
            if realtime:
                time.sleep(0.032)
        # THE WORKER IS A THREAD AND `feed()` ONLY QUEUES (stt_engine.Streaming
        # Session: `self._q.put(...)`), so the summary must wait for the FINAL,
        # which the worker emits LAST after every partial before it.
        sess.finalize()
        t0 = time.time()
        while not finals and time.time() - t0 < 90:
            time.sleep(0.05)
        time.sleep(0.3)                       # let the last prints land
        c, t = state["last_c"] or "", state["last_t"] or ""
        print(f"  --- {SILENCE_MS} ms of silence fed; phrase first seen anywhere "
              f"at {first_any['ms']} ms, first in COMMITTED at "
              f"{first_committed['ms']} ms")
        print(f"  --- when the room went quiet, the POLICY saw:  "
              f"committed={c[-42:]!r}  phrase_in_committed={phrase_in(c)}")
        print(f"  --- and the PAGE was drawing: tentative={t!r}  "
              f"phrase_anywhere={phrase_in(c + ' ' + t)}")
        if finals:
            f = finals[0]
            print(f"  FINAL text={f.get('text','')[-60:]!r}  "
                  f"(phrase in the final: {phrase_in(f.get('text',''))})")
        else:
            print("  FINAL: none emitted within 90 s")
    finally:
        sess.close()


def multi_turn(pcm: bytes, language, label: str, turns: int = 3) -> None:
    """THE SAME UTTERANCE, REPEATEDLY, on ONE held StreamingSession.

    "It worked once, and then it did not" is a claim about REPEAT, so the
    thing to measure is a repeat. The session is held for the conversation
    (AC-59's shape) and the engine begins a FRESH `stream()` per turn -- so
    whatever survives a turn lives in the SESSION's model, and if anything
    there is not reset, turn 2 onwards decodes in company it did not ask for.
    """
    print(f"\n=== {label}: {turns} turns on ONE session (language={language!r}) ===")
    finals: list = []
    t_start = time.time()

    def emit(kind, p):
        if kind == "final":
            finals.append((int((time.time() - t_start) * 1000), p))
        elif kind == "error":
            print(f"  !! ENGINE ERROR: {p.get('message')}")

    sess = S.StreamingSession(emit=emit, slug=SLUG, quant=None, backend="cpu",
                              latency="accurate", language=language)
    zeros = b"\x00" * CHUNK
    try:
        for turn in range(1, turns + 1):
            for i in range(0, len(pcm), CHUNK):
                sess.feed(pcm[i:i + CHUNK])
                time.sleep(CHUNK / 2 / SAMPLE_RATE)
            for _ in range(int(SILENCE_MS / 32)):
                sess.feed(zeros)
                time.sleep(0.032)
            sess.finalize()
            t0 = time.time()
            while len(finals) < turn and time.time() - t0 < 90:
                time.sleep(0.05)
            if len(finals) >= turn:
                _at, payload = finals[turn - 1]
                text = payload.get("text", "")
                tail = text[-46:]
                ends = "ENDS WITH THE PHRASE" if phrase_in(text) and \
                    EndWordEnd(phrases=PHRASES)._heard_end_phrase(text) else \
                    "does NOT end with a phrase"
                print(f"  turn {turn}: {_at:>6}ms  {tail!r}")
                print(f"           phrase={phrase_in(text)}  -> {ends}")
            else:
                print(f"  turn {turn}: NO FINAL within 90 s")
            time.sleep(0.4)              # a beat, as a person would
    finally:
        sess.close()


def main() -> int:
    policy = EndWordEnd(phrases=PHRASES)
    print(f"[spike] policy phrases: {policy.phrases}")
    pt = None
    try:
        pt = portuguese_pcm()
        stream(pt, "pt-BR", "PORTUGUESE (kokoro pf_dora)")
        multi_turn(pt, "pt-BR", "PORTUGUESE (kokoro pf_dora)")
    except Exception as e:                                     # noqa: BLE001
        print(f"[spike] portuguese failed: {type(e).__name__}: {e}")
    try:
        en = english_pcm()
        stream(en, "en-US", "ENGLISH (Windows SAPI)")
        multi_turn(en, "en-US", "ENGLISH (Windows SAPI)")
    except Exception as e:                                     # noqa: BLE001
        print(f"[spike] english failed: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
