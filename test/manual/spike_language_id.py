"""GO / NO-GO: can py3langid name the language of ONE utterance?

`docs/language-auto.md` step 1. Not a test -- nothing here asserts, and
`manual/` is excluded from pytest by `pytest.ini`. It answers three questions
with numbers, and the numbers go into the spec before anything depends on
them:

  1. Does the detector get a whole sentence right, in both languages?
  2. What does it do with SHORT utterances -- "sim", "yes", "obrigado" --
     where langid is documented to be weak below roughly 50 characters?
  3. How often does a minimum-confidence rule ABSTAIN, and on what?

Question 3 is the one that decides the design. An abstention is not a
failure: `Hub._turn_language` LATCHES the previous turn's language when the
detector will not commit, so a conversation that started in Portuguese stays
in Portuguese through "sim". What the numbers have to show is that the
abstentions land on the SHORT cases and not on the long ones, because a long
utterance the detector will not name is a turn answered in the wrong voice.

THE CANDIDATE SET IS THE ENGINE'S. `set_languages` is filled from
`Tts.info["voices"]`, reduced to BCP-47 primary subtags -- Butler can only
answer in a language it has a voice for, so offering the classifier all 97 of
its languages would let it pick one that has no voice and cost accuracy on
the two that do. No locale is written down in this file, which is the AC-42
rule: a list in the daemon is wrong the day a voice pack is added, and wrong
invisibly.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_language_id.py
    .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_language_id.py --engine fake
    .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_language_id.py --asr

`--engine fake` skips the Kokoro sidecar and takes the fake adapter's two
languages instead: same candidate set, no node, seconds instead of a warm.
Use it to re-run the text table; use the default when the question is what
the SHIPPED engine can say.

`--asr` is the expensive phase and it exists because the detector's real
input is not written prose -- it is whatever the recognizer produced. So it
synthesizes each case through the real speech engine, transcribes it back
through the real recognizer with NO language pinned (which is the
configuration this feature creates), and classifies THAT. It loads the GGUF
and takes minutes. It starts no daemon and touches no port.

TRAPS, both paid for elsewhere in this tree and both live here:

* `Session.run()` takes FLOAT32 and answers raw int16 bytes with `text=''` --
  fast, confident and empty. The adapter owns that conversion; this file goes
  through `Asr.transcribe`, which is the port, precisely so it cannot get it
  wrong (`ports.Asr.transcribe`).
* The speech engine is 24 kHz and the recognizer wants 16 kHz
  (`ports.SAMPLE_RATE_OUT` vs `SAMPLE_RATE_IN`). Handing it the wrong rate
  does not fail, it transcribes a chipmunk -- so this file resamples and says
  so rather than trusting the numbers to look wrong.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from daemon import ports                                    # noqa: E402
from daemon.adapters import tts_fake, tts_kokoro            # noqa: E402,F401


# The cases, with the language each one IS. `lang` is the primary subtag the
# detector must answer, so this table never names a region -- a region is the
# VOICE's business and the detector has no opinion about pt-BR versus pt-PT.
#
# The English long cases are the shipped fixtures' own transcripts
# (`test/assets/manifest.json`), read rather than retyped, so this measures
# the sentences the rest of the suite already speaks.
#
# The Portuguese long cases include the two the recognizer actually returned
# on 2026-09-11 when it was asked with no language at all. The rest are
# ordinary things somebody says to a voice assistant, written for this file.
#
# WRITTEN WITHOUT ACCENTS, which is this repository's no-unicode-in-code rule
# and is also the HARDER case: the recognizer emits `barometro` with its
# accent, and a cedilla or a tilde is about the strongest Portuguese signal a
# character-n-gram model can get. So every Portuguese number below is a floor
# on what the real transcript scores, not a stand-in for it. `--asr` measures
# the accented text the engine actually produces.
SHORT = "short"
LONG = "long"

PT_CASES = [
    (LONG, "O barometro esta na prateleira ao lado da porta longe do Sol."),
    (LONG, "Voce pode rodar os testes e me dizer quantos falharam?"),
    (LONG, "Abre o projeto butler e corrige o teste que esta quebrado."),
    (LONG, "Me explica por que essa funcao esta demorando tanto para responder."),
    (LONG, "Nao precisa fazer agora, so anota para depois."),
    (SHORT, "sim"),
    (SHORT, "nao"),
    (SHORT, "obrigado"),
    (SHORT, "pode parar"),
    (SHORT, "continua"),
]

EN_CASES_EXTRA = [
    (LONG, "Run the tests again and tell me which ones failed."),
    (LONG, "Do not do that now, just make a note of it for later."),
    (SHORT, "yes"),
    (SHORT, "no"),
    (SHORT, "thanks"),
    (SHORT, "stop"),
    (SHORT, "go ahead"),
]


def english_fixture_cases() -> list[tuple[str, str]]:
    """The shipped fixtures' transcripts, longest first. READ, never retyped."""
    manifest = json.loads(
        (REPO / "test" / "assets" / "manifest.json").read_text(
            encoding="utf-8"))
    out = []
    for f in manifest["fixtures"]:
        text = (f.get("transcript") or "").strip()
        if text:
            out.append((LONG if len(text) >= 50 else SHORT, text))
    return out


async def engine_languages(engine: str) -> tuple[list[str], list[dict]]:
    """The primary subtags the SPEECH engine has voices for.

    Warmed, because `Tts.info` is served from a cache an adapter fills at boot
    and Kokoro's is empty until then -- reading it cold reports an engine with
    no voices, which is the AC-108 failure wearing the shape of a measurement
    (`ports.Tts.info`).
    """
    tts = ports.build(ports.TTSS, engine)
    await tts.warm()
    return ports.voice_languages(tts.info), list(tts.info.get("voices") or [])


def identifier(langs: list[str]):
    """The detector `dialogue.detect_language` builds, built the same way.

    `norm_probs=True` is not a nicety here: the module-level `py3langid.classify`
    answers an unnormalised LOG score (-195.8 for a Portuguese sentence, -5.9
    for "sim"), and a threshold over that is a threshold over utterance LENGTH
    rather than over confidence. With it on, the same two answer 1.000 and
    0.514 -- which is the number an abstention rule can be written against.
    """
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    ident = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
    ident.set_languages(langs)
    return ident


def run_table(ident, cases: list[tuple[str, str, str]], floor: float) -> dict:
    """Classify every case and print one row each. Returns the tallies.

    `cases` is `(want, kind, text)`. A row is WRONG only when the detector
    committed to the other language; a row below `floor` is an ABSTENTION and
    is counted separately, because the two have different consequences -- one
    is answered in the wrong voice and the other keeps the voice it had.
    """
    tally = {"n": 0, "right": 0, "wrong": 0, "abstain": 0,
             "short_abstain": 0, "long_abstain": 0, "long_wrong": 0}
    print(f"{'want':>5} {'got':>5} {'conf':>6} {'kind':>5}  verdict  text")
    for want, kind, text in cases:
        got, conf = ident.classify(text)
        tally["n"] += 1
        if conf < floor:
            verdict = "ABSTAIN"
            tally["abstain"] += 1
            tally["short_abstain" if kind == SHORT else "long_abstain"] += 1
        elif got == want:
            verdict = "ok     "
            tally["right"] += 1
        else:
            verdict = "WRONG  "
            tally["wrong"] += 1
            if kind == LONG:
                tally["long_wrong"] += 1
        print(f"{want:>5} {got:>5} {conf:6.3f} {kind:>5}  {verdict}  "
              f"{text[:58]}")
    return tally


def report(name: str, tally: dict, floor: float) -> None:
    print(f"\n  {name}: {tally['n']} cases, floor {floor:.2f} -- "
          f"{tally['right']} right, {tally['wrong']} wrong, "
          f"{tally['abstain']} abstained "
          f"({tally['short_abstain']} short, {tally['long_abstain']} long)")
    if tally["long_wrong"]:
        print(f"  *** {tally['long_wrong']} LONG utterance(s) were named as "
              f"the WRONG language. That is a turn answered in the wrong "
              f"voice and no latch recovers it.")


# --------------------------------------------------------------------------- #
# --asr: the round trip, because the detector's real input is a transcript
# --------------------------------------------------------------------------- #
def resample_24k_to_16k(pcm: bytes) -> bytes:
    """24 kHz int16 -> 16 kHz int16, linear. Good enough to be transcribed.

    The fixtures in `test/assets/` use a windowed sinc and this does not; what
    that costs is aliasing a recognizer hears as a little extra sibilance, and
    the question here is which LANGUAGE came back, not a word error rate. Said
    rather than left for a reader to assume the two paths match.
    """
    import numpy as np

    src = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if src.size == 0:
        return b""
    n = int(src.size * 2 / 3)
    idx = np.arange(n, dtype=np.float64) * 1.5
    lo = np.floor(idx).astype(np.int64)
    hi = np.minimum(lo + 1, src.size - 1)
    frac = (idx - lo).astype(np.float32)
    out = src[lo] * (1.0 - frac) + src[hi] * frac
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


async def round_trip(ident, engine: str, voices: list[dict],
                     cases: list[tuple[str, str, str]], floor: float) -> None:
    """Speak each case, transcribe it with NO language pinned, classify that.

    The recognizer is asked for `language=None` deliberately: that IS what
    `language = auto` produces (`Hub._speakable_language`), and the whole
    premise of this feature is the 2026-09-11 measurement that it transcribes
    both languages correctly unpinned and loses a whole sentence when the
    wrong locale is pinned.
    """
    from daemon.adapters import asr_transcribecpp                # noqa: F401

    from daemon import settings as settings_mod

    # The SETTING wins over `config/models.json`, which is `build_hub`'s own
    # ruling (D-36 CLOSED, `docs/language.md` 6.4) -- a spike that took the
    # config's model would measure a recognizer the daemon does not run. READ
    # only; nothing here writes the owner's state.
    prefs = settings_mod.Settings(REPO / "state" / "settings.json")
    model = str(prefs.get("asr_model", "") or "")
    backend = str(prefs.get("asr_backend", "") or "")
    latency = str(prefs.get("asr_latency", "") or "")
    if not model:
        raise SystemExit("no asr_model is set, so there is nothing to measure")
    asr = ports.build(ports.ASRS, "transcribecpp",
                      model=model, backend=backend, latency=latency)
    await asr.warm()
    tts = ports.build(ports.TTSS, engine)
    await tts.warm()

    # One voice per primary subtag, first as sorted -- `voice_ids(...)[0]` is
    # what the daemon itself picks, so the audio here is the audio a turn
    # would produce.
    by_lang: dict[str, str] = {}
    for v in sorted(voices, key=lambda e: str(e.get("id") or "")):
        sub = str(v.get("language") or "").split("-")[0].lower()
        by_lang.setdefault(sub, str(v["id"]))

    tally = {"n": 0, "right": 0, "wrong": 0, "abstain": 0,
             "short_abstain": 0, "long_abstain": 0, "long_wrong": 0}
    print(f"\n{'want':>5} {'got':>5} {'conf':>6} {'kind':>5}  verdict  "
          f"transcript")
    for want, kind, text in cases:
        voice = by_lang.get(want)
        if voice is None:
            print(f"{want:>5}     -      -  {kind:>5}  SKIP     "
                  f"no voice speaks {want!r}")
            continue
        pcm = b"".join([chunk async for chunk in tts.synthesize(
            text, voice, handle=("spike", want, text))])
        heard = (await asr.transcribe(
            resample_24k_to_16k(pcm),
            ports.AsrConfig(model=model, language=None)) or "").strip()
        if not heard:
            print(f"{want:>5}     -      -  {kind:>5}  EMPTY    "
                  f"nothing came back for {text[:40]!r}")
            continue
        got, conf = ident.classify(heard)
        tally["n"] += 1
        if conf < floor:
            verdict = "ABSTAIN"
            tally["abstain"] += 1
            tally["short_abstain" if kind == SHORT else "long_abstain"] += 1
        elif got == want:
            verdict = "ok     "
            tally["right"] += 1
        else:
            verdict = "WRONG  "
            tally["wrong"] += 1
            if kind == LONG:
                tally["long_wrong"] += 1
        print(f"{want:>5} {got:>5} {conf:6.3f} {kind:>5}  {verdict}  "
              f"{heard[:58]}")
    report("through the real engines", tally, floor)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="kokoro",
                    help="which Tts adapter names the candidate languages")
    ap.add_argument("--floor", type=float, default=0.60,
                    help="minimum confidence below which the detector abstains")
    ap.add_argument("--asr", action="store_true",
                    help="also measure through the real speech and recognition "
                         "engines (minutes)")
    args = ap.parse_args()

    langs, voices = await engine_languages(args.engine)
    print(f"[spike] {args.engine} reports {len(voices)} voices in "
          f"{len(langs)} languages: {langs}")
    if len(langs) < 2:
        print("[spike] one language means every row below passes whether the "
              "classifier works or not; nothing here is evidence (D-13).")
        return 2

    ident = identifier(langs)
    cases: list[tuple[str, str, str]] = []
    for kind, text in english_fixture_cases() + EN_CASES_EXTRA:
        cases.append(("en", kind, text))
    for kind, text in PT_CASES:
        cases.append(("pt", kind, text))
    cases = [c for c in cases if c[0] in langs]
    if not cases:
        print(f"[spike] the engine speaks {langs} and this file has cases for "
              f"none of them; add rows before trusting a number.")
        return 2

    print(f"\n[spike] written text, {len(cases)} cases\n")
    report("written text", run_table(ident, cases, args.floor), args.floor)

    if args.asr:
        await round_trip(ident, args.engine, voices, cases, args.floor)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
