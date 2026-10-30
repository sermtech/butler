"""GO / NO-GO: may the recognizer be left UNPINNED, and what does a pin cost?

`docs/language-auto.md` step 3, and the measurement that deleted a guard.
Not a test -- nothing asserts, and `manual/` is excluded by `pytest.ini`.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_asr_unpinned.py

It answers one question the plan for `language = auto` got wrong. The plan
said the adapter "already accepts" `language=None`; `stt_engine` did not --
it RAISED for a blank language on any `cache_aware` model, which made
`hub._speakable_language` substitute the model's first locale (`en-US`), and
`auto` would have pinned a Portuguese speaker's recognizer to English.

Feeding the same audio three ways is what separates "it opens" from "it
decodes". A stream OPENS for a language the model cannot use -- the
English-only parakeet opens for `pt-BR` and then errors on every frame -- so
constructing one proves nothing on its own.

RESULT 2026-09-11, nemotron-3.5-asr-streaming-0.6b, cpu backend, accurate:

  ENGLISH fixture (test/assets/two_sentences.wav)
    None    'The tests are passing. I push the branch.'
    en-US   'The tests are passing. I push the branch.'
    pt-BR   'I push the branch.'                          <- A SENTENCE LOST
  PORTUGUESE (synthesized through the real speech engine, pf_dora)
    None    'Voze pode rodar os testes e me dizer quantos falharam.'
    en-US   'Voz se pode rodar os testes e me dizer quantos falharam.'
    pt-BR   'Voz se pode rodar os testes e me dizer quantos falharam.'

Unpinned is never worse and is sometimes much better, so `auto` leaves the
recognizer alone. Note the third row of the second block: pinning the RIGHT
locale was no better than pinning the wrong one here, which is why this file
measures all three rather than just the two that were expected to differ.

Separately measured the same day, by opening a stream per value: the engine
accepts `None` and `''` and `pt-BR`, and REJECTS the literal `'auto'` with
`UnsupportedRequest: transcribe_stream_begin: unsupported language
(status 10)`. That string is not a value any Butler code passes -- `auto` is
the SETTING's word for "decide it from what I say" and is resolved to None
before it reaches a port -- and this file leaves it in the table so nobody
re-derives it from the setting's name.

Loads the real GGUF and starts the real speech sidecar; takes minutes,
starts no daemon and touches no port.
"""
from __future__ import annotations

import asyncio
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
from spike_language_id import resample_24k_to_16k              # noqa: E402

SLUG = "nemotron-3.5-asr-streaming-0.6b"
PT_LINE = "Voce pode rodar os testes e me dizer quantos falharam?"


def fixture_pcm(name: str) -> bytes:
    with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
        return w.readframes(w.getnframes())


def decode(pcm: bytes, language) -> str:
    """One whole utterance through the STREAMING path at one locale.

    `_lang` is set after construction rather than passed in, because the
    point of this file is to reach values the constructor used to refuse --
    and it still refuses the literal `auto`, which is the row worth keeping.
    """
    finals: list[dict] = []
    sess = S.StreamingSession(
        emit=lambda kind, p: finals.append(p) if kind == "final" else None,
        slug=SLUG, quant=None, backend="cpu", latency="accurate",
        language="en-US")
    sess._lang = language
    try:
        for i in range(0, len(pcm), 3200):
            sess.feed(pcm[i:i + 3200])
        sess.finalize()
        t0 = time.time()
        while not finals and time.time() - t0 < 60:
            time.sleep(0.05)
    finally:
        sess.close()
    if not finals:
        return "<NO FINAL>"
    return finals[0].get("text", "")


async def portuguese_pcm() -> bytes:
    """Real Portuguese audio from the real speech engine, at 16 kHz.

    Synthesized rather than checked in, because `test/assets/` holds English
    only and a fixture nobody can regenerate is worse than one built by the
    pipeline it measures (`test_attachments.py`'s rule).
    """
    tts = ports.build(ports.TTSS, "kokoro")
    await tts.warm()
    voice = ports.voice_ids(tts.info, "pt")[0]
    chunks = [c async for c in tts.synthesize(PT_LINE, voice,
                                              handle=("spike",))]
    print(f"[spike] portuguese audio synthesized with {voice!r}")
    return resample_24k_to_16k(b"".join(chunks))


def main() -> int:
    pt = asyncio.run(portuguese_pcm())
    en = fixture_pcm("two_sentences.wav")
    for label, pcm in (("ENGLISH fixture (two_sentences.wav)", en),
                       ("PORTUGUESE (synthesized)", pt)):
        print(f"\n{label}")
        for language in (None, "en-US", "pt-BR"):
            print(f"  language={str(language):<7} -> {decode(pcm, language)!r}")
    print("\nand the one value the ENGINE itself refuses:")
    try:
        decode(en[:3200], "auto")
        print("  language='auto'  -> accepted (the engine changed; re-read "
              "stt_engine's comment, it says this is rejected)")
    except Exception as e:                                     # noqa: BLE001
        print(f"  language='auto'  -> {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
