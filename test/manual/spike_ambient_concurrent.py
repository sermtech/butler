"""Ambient: does the 30 s back-fill decode run WHILE a live turn streams?

This is a GO/NO-GO for the design in `docs/ambient.md` section 3, and it is
the one thing nobody has ever run. That section says the satellite ships its
ring on wake and the daemon decodes it *while the owner is still speaking*.
AC-69 is the load-bearing claim: the live turn's `final` must arrive no later
than it does with ambient off. The offline decode costs about 1.9 s for 30 s
of audio, and that 1.9 s is only free if it OVERLAPS speech that is still
happening.

Both halves sit on ONE `transcribe_cpp.Model`. `stt_engine._get_model` caches
by (path, backend), so `TranscribeCppSession` (live, `Session.stream()`) and
`TranscribeCppAsr._decode_clip` (back-fill, `Session.run()`) open two
`Session` objects on the same instance -- and `stt_engine`'s own comment says
transcribe.cpp allows one stream in flight per `Model`. Three answers are
possible and they lead to three different designs:

  FINE       both run concurrently, both correct, neither much slower.
  SERIALISED one blocks the other. The live turn's final is late by about
             the decode, AC-69 fails, and the design needs a second `Model`
             (memory) or a queue (latency).
  CORRUPT    they interfere: wrong text, an exception, a native abort.

WHAT IT DOES. Three conditions, interleaved round by round so thermal drift
and background load hit all three equally:

  A  a live streaming turn alone      (LIVE_S seconds, fed in 32 ms frames
                                       the way `TurnPipeline` does, then
                                       finalized off the loop)
  B  a 30 s offline `transcribe` alone
  C  both: the offline decode is started first, given a moment to reach the
     native call, and the live turn is fed and finalized underneath it.

`--two-models` additionally runs C with the back-fill on its OWN `Model`,
loaded outside the cache, and reports what the second copy cost in memory.
That is the fallback the design would need if sharing is bad, so it also runs
AUTOMATICALLY when the shared C run inflates the live turn past
DEGRADE_RATIO.

TRAPS, inherited from the two sibling spikes and worth more than the numbers:

  1. `Session.run()` takes FLOAT32 PCM. Handed the int16 bytes the rest of
     the audio path carries it returns `text=''` -- fast, confident, empty.
     A numpy int16 array it refuses loudly. The one shape that fails silently
     is the one you get by doing nothing. The adapter converts; the
     two-Model path here converts too, and every row prints `chars` so a
     silent empty decode cannot pass as a fast one.
  2. `Result.__repr__` is ~214 characters of timings whatever was
     transcribed, so `len(str(result))` is flat at every length and reads
     exactly like success. Only `.text` is read here.
  3. Running the offline decode on the calling thread would "serialise"
     trivially and measure the harness. It goes through
     `TranscribeCppAsr.transcribe`, which is `asyncio.to_thread` -- the real
     adapter's own path -- and the two-Model variant uses `to_thread`
     directly. Overlap is not assumed: every row prints the measured fraction
     of the live turn during which the decode was actually in flight, and a
     row with low overlap has not tested anything.

An exception inside a condition is CAUGHT and printed as the result, because
"it raises" is one of the three answers this script exists to return. A
native abort still takes the process, and that is also an answer.

NOT collected by pytest (`manual/` is excluded in pytest.ini). It loads the
real engine on the CPU backend (the GPU is contended with llama-swap) and
takes a couple of minutes. It starts no daemon and touches no port.

Run:  .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_ambient_concurrent.py
      .venv\\Scripts\\python.exe -X utf8 test\\manual\\spike_ambient_concurrent.py --two-models

-----------------------------------------------------------------------------
THE ANSWER IS **CORRUPT**. [MEASURED 2026-08-19, cpu backend,
parakeet-unified-en-0.6b, 3 script runs x 3 rounds = 9 samples per condition.]

| condition                  | live -> final | live chars | back-fill | chars |
|----------------------------|---------------|------------|-----------|-------|
| A live turn alone (5 s)    | 1.71 s        | 94         | --        | --    |
| B back-fill alone (30 s)   | --            | --         | 2.11 s    | 507   |
| C both, ONE shared Model   | 2.87 s        | **0**      | 2.69 s    | **0** |
| D both, TWO Models         | 3.66 s        | 94         | 3.04 s    | 507   |

(Medians. Overlap was 60-100% on every C and D row, so both halves really
were in flight together.)

**Two sessions on one `Model` return EMPTY TRANSCRIPTS -- BOTH of them, every
time, silently.** 9 of 9 C rows: the live turn produced `Final(text='')` and
the back-fill produced `''`. Nothing raised, `Final.failed` was never set, and
the adapter printed nothing on stderr, so the failure shape is a plausible
empty answer at a plausible time -- the same shape section 1 of
`docs/ambient.md` already calls the worst one available. The damage is not
sticky: the very next A row decoded correctly, so a shared `Model` is
corrupted only for the duration of the overlap.

Giving the back-fill its OWN `Model` (condition D) fixes the CORRECTNESS
completely: 9 of 9 rows returned both transcripts byte-identical to their
alone baselines. It costs a second copy of the weights -- MEASURED +1060 MB
working set, +1232 MB private commit on top of the first Model's 1102/1733 MB.

**AC-69 FAILS EITHER WAY, and that is the second finding.** The criterion says
the live turn's `final` arrives no later than with ambient off. It never did:
1.55-1.86x the alone baseline sharing a Model, 1.65-2.42x with two Models. The
back-fill is not free while the owner is still speaking, because on the CPU
backend both decodes want the same cores. The 30 s decode does not ADD its
full 2 s to the turn -- C at 2.87 s is under the 3.82 s a full serialisation
would cost -- so there is real parallelism, but the live turn still lands
about 1.2 s late sharing a Model and about 2.0 s late with two.
-----------------------------------------------------------------------------
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import statistics
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BUTLER_TLS", "0")

from daemon import ports                                    # noqa: E402
from daemon.adapters import asr_transcribecpp               # noqa: E402,F401

MODEL = "parakeet-unified-en-0.6b"
BACKEND = "cpu"
FRAME_BYTES = 512 * 2          # the worklet's 32 ms frame at 16 kHz int16
BYTES_PER_S = 16000 * 2
LIVE_S = 5                     # a realistic live utterance, not a long one
AMBIENT_S = 30                 # the window docs/ambient.md section 3 names
RUNS = 3                       # one sample cannot tell an effect from noise
START_GRACE_S = 0.10           # let the decode thread reach the native call
DEGRADE_RATIO = 1.25           # above this, go measure the two-Model fallback


# -- memory -----------------------------------------------------------------
class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _mem_mb() -> tuple[float, float]:
    """(working set, private commit) for this process, in MB.

    Both, because a GGUF may be memory-MAPPED: working set then grows as
    pages are touched while private commit barely moves, and reporting only
    one of the two would understate or overstate the cost of a second copy.
    """
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    # restype must be declared: GetCurrentProcess returns the pseudo-handle
    # -1, and ctypes' default c_int restype truncates it to something the
    # 64-bit callee rejects.
    get_proc = ctypes.windll.kernel32.GetCurrentProcess
    get_proc.restype = ctypes.c_void_p
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(get_proc()), ctypes.byref(pmc), pmc.cb):
        raise OSError(f"GetProcessMemoryInfo failed: "
                      f"{ctypes.GetLastError()}")
    return pmc.WorkingSetSize / 2**20, pmc.PagefileUsage / 2**20


def _system_free_gb() -> tuple[float, float]:
    """(physical available, commit available) in GB.

    Printed at both ends of the run: this machine hit 0.1 GB of commit
    charge free earlier today from leaked orphans, and OpenBLAS allocation
    failures under that condition are the machine, not the code.
    """
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    ms = MS()
    ms.dwLength = ctypes.sizeof(MS)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        raise OSError("GlobalMemoryStatusEx failed")
    return ms.ullAvailPhys / 2**30, ms.ullAvailPageFile / 2**30


# -- audio ------------------------------------------------------------------
def _speech() -> bytes:
    """Every fixture concatenated: real speech, not a tone."""
    out = bytearray()
    for name in ("long_utterance.wav", "two_sentences.wav",
                 "wake_then_command.wav", "short_question.wav"):
        with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1:
                raise SystemExit(f"{name} is not 16 kHz mono")
            out += w.readframes(w.getnframes())
    return bytes(out)


def _tiled(seconds: float) -> bytes:
    want = int(seconds * BYTES_PER_S)
    base = _speech()
    return (base * ((want // len(base)) + 1))[:want]


def _float32(pcm: bytes):
    import numpy as np
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


# -- the three conditions ---------------------------------------------------
async def _live_turn(session, finals, pcm: bytes) -> tuple[float, float, str, bool]:
    """Feed one utterance in 32 ms frames and finalize, like TurnPipeline.

    Returns (start, end_of_final, text, failed). `finalize` only returns
    after the adapter has delivered the Final to the callback, so `end`
    really is time to final rather than time to a request.

    `failed` is the adapter's own flag and it is carried out because an empty
    transcript has two very different causes: the engine RAISED and
    `_emit("error")` synthesized a failed Final, or the engine finished
    normally and had nothing to say. Only the second is silent corruption.
    """
    finals.clear()
    t0 = time.perf_counter()
    for i in range(0, len(pcm), FRAME_BYTES):
        session.feed(pcm[i:i + FRAME_BYTES])
    await asyncio.to_thread(session.finalize)
    t1 = time.perf_counter()
    final = finals[-1] if finals else None
    return t0, t1, (final.text if final else ""), bool(final and final.failed)


async def _offline(asr, cfg, pcm: bytes) -> tuple[float, float, str]:
    """The port's whole-clip half, shared Model, on its own thread."""
    t0 = time.perf_counter()
    text = await asr.transcribe(pcm, cfg)
    return t0, time.perf_counter(), text


async def _offline_own_model(model, audio32, language) -> tuple[float, float, str]:
    """The same decode on a SECOND Model, outside stt_engine's cache."""
    def _go():
        session = model.session()
        try:
            return session.run(audio32, language=language).text.strip()
        finally:
            session.close()
    t0 = time.perf_counter()
    text = await asyncio.to_thread(_go)
    return t0, time.perf_counter(), text


def _overlap(live: tuple[float, float], off: tuple[float, float]) -> float:
    """Fraction of the live turn during which the decode was in flight."""
    span = live[1] - live[0]
    if span <= 0:
        return 0.0
    return max(0.0, min(live[1], off[1]) - max(live[0], off[0])) / span


# -- reporting --------------------------------------------------------------
def _row(rows: list[dict], **kw) -> None:
    rows.append(kw)
    print(f"{kw['cond']:<14} {kw['run']:>3} "
          f"{_fmt(kw.get('live_s')):>9} {_fmt_i(kw.get('live_chars')):>6} "
          f"{_fmt(kw.get('off_s')):>10} {_fmt_i(kw.get('off_chars')):>6} "
          f"{_fmt_pct(kw.get('overlap')):>8}   {kw.get('note', '')}")


def _fmt(v) -> str:
    return "--" if v is None else f"{v:.2f}s"


def _fmt_i(v) -> str:
    return "--" if v is None else str(v)


def _fmt_pct(v) -> str:
    return "--" if v is None else f"{v * 100:.0f}%"


def _median(rows: list[dict], cond: str, key: str):
    vals = [r[key] for r in rows if r["cond"] == cond and r.get(key) is not None]
    return statistics.median(vals) if vals else None


def _texts(rows: list[dict], cond: str, key: str) -> set[str]:
    return {r[key] for r in rows if r["cond"] == cond and r.get(key) is not None}


def _header() -> None:
    print(f"\n{'condition':<14} {'run':>3} {'live->final':>9} {'chars':>6} "
          f"{'offline':>10} {'chars':>6} {'overlap':>8}   note")
    print("-" * 78)


# -- main -------------------------------------------------------------------
async def main() -> int:
    force_two = "--two-models" in sys.argv
    phys, commit = _system_free_gb()
    print(f"free before: {phys:.1f} GB physical, {commit:.1f} GB commit "
          f"(an OpenBLAS allocation failure below ~1 GB commit is the "
          f"machine, not the code)")

    asr = ports.build(ports.ASRS, "transcribecpp", model=MODEL, backend=BACKEND)
    cfg = ports.AsrConfig(model=MODEL)
    t0 = time.perf_counter()
    await asr.warm()
    print(f"warm {time.perf_counter() - t0:.2f} s; backend="
          f"{asr.info.get('backend')} model={asr.info.get('slug')} "
          f"live={LIVE_S}s ambient={AMBIENT_S}s runs={RUNS}")
    after_first = _mem_mb()
    print(f"after ONE Model: working set {after_first[0]:.0f} MB, "
          f"private commit {after_first[1]:.0f} MB")

    live_pcm = _tiled(LIVE_S)
    amb_pcm = _tiled(AMBIENT_S)

    finals: list = []
    session = asr.open(cfg, on_partial=lambda _p: None, on_final=finals.append)
    rows: list[dict] = []
    try:
        _header()
        for run in range(1, RUNS + 1):
            # A -- live turn alone
            try:
                lt0, lt1, ltext, fail = await _live_turn(session, finals,
                                                         live_pcm)
                _row(rows, cond="A live alone", run=run, live_s=lt1 - lt0,
                     live_chars=len(ltext), live_text=ltext,
                     note="engine FAILED the final" if fail else "")
            except Exception as e:                       # noqa: BLE001
                _row(rows, cond="A live alone", run=run,
                     note=f"RAISED {type(e).__name__}: {e}")

            # B -- offline back-fill alone
            try:
                ot0, ot1, otext = await _offline(asr, cfg, amb_pcm)
                _row(rows, cond="B offline alone", run=run, off_s=ot1 - ot0,
                     off_chars=len(otext), off_text=otext)
            except Exception as e:                       # noqa: BLE001
                _row(rows, cond="B offline alone", run=run,
                     note=f"RAISED {type(e).__name__}: {e}")

            # C -- both at once, ONE shared Model
            try:
                task = asyncio.create_task(_offline(asr, cfg, amb_pcm))
                await asyncio.sleep(START_GRACE_S)
                lt0, lt1, ltext, fail = await _live_turn(session, finals,
                                                         live_pcm)
                ot0, ot1, otext = await task
                _row(rows, cond="C both", run=run, live_s=lt1 - lt0,
                     live_chars=len(ltext), off_s=ot1 - ot0,
                     off_chars=len(otext), overlap=_overlap((lt0, lt1), (ot0, ot1)),
                     live_text=ltext, off_text=otext,
                     note="engine FAILED the final" if fail else "")
            except Exception as e:                       # noqa: BLE001
                _row(rows, cond="C both", run=run,
                     note=f"RAISED {type(e).__name__}: {e}")

        base_live = _median(rows, "A live alone", "live_s")
        conc_live = _median(rows, "C both", "live_s")
        degraded = (base_live and conc_live
                    and conc_live > base_live * DEGRADE_RATIO)
        # Corruption is the louder trigger: a transcript that changed when
        # the two ran together makes the two-Model fallback the only design
        # left, so measure it without being asked.
        wrong = (_texts(rows, "A live alone", "live_text")
                 != _texts(rows, "C both", "live_text")
                 or _texts(rows, "B offline alone", "off_text")
                 != _texts(rows, "C both", "off_text"))

        if force_two or degraded or wrong:
            await _two_models(asr, cfg, session, finals, live_pcm, amb_pcm,
                              rows, after_first)
    finally:
        session.close()

    _verdict(rows)
    phys, commit = _system_free_gb()
    print(f"\nfree after: {phys:.1f} GB physical, {commit:.1f} GB commit")
    return 0


async def _two_models(asr, cfg, session, finals, live_pcm, amb_pcm, rows,
                      after_first) -> None:
    """Condition D: the back-fill gets its OWN Model. The design's fallback."""
    import transcribe_cpp as tc
    from daemon.engines import stt_engine as se

    print("\nloading a SECOND Model outside stt_engine's cache "
          "(the fallback if sharing one is bad)")
    path = se.gguf_path(MODEL)
    model2 = tc.Model(str(path), backend=BACKEND)
    warm = model2.session()
    warm.run(_float32(_tiled(1)), language="en")     # touch the weights
    warm.close()
    after_second = _mem_mb()
    print(f"after TWO Models: working set {after_second[0]:.0f} MB "
          f"(+{after_second[0] - after_first[0]:.0f}), private commit "
          f"{after_second[1]:.0f} MB (+{after_second[1] - after_first[1]:.0f})")

    audio32 = _float32(amb_pcm)
    _header()
    for run in range(1, RUNS + 1):
        try:
            task = asyncio.create_task(
                _offline_own_model(model2, audio32, cfg.language))
            await asyncio.sleep(START_GRACE_S)
            lt0, lt1, ltext, fail = await _live_turn(session, finals, live_pcm)
            ot0, ot1, otext = await task
            _row(rows, cond="D both, 2 mod", run=run, live_s=lt1 - lt0,
                 live_chars=len(ltext), off_s=ot1 - ot0, off_chars=len(otext),
                 overlap=_overlap((lt0, lt1), (ot0, ot1)),
                 live_text=ltext, off_text=otext,
                 note="engine FAILED the final" if fail else "")
        except Exception as e:                           # noqa: BLE001
            _row(rows, cond="D both, 2 mod", run=run,
                 note=f"RAISED {type(e).__name__}: {e}")


def _verdict(rows: list[dict]) -> None:
    base_live = _median(rows, "A live alone", "live_s")
    base_off = _median(rows, "B offline alone", "off_s")
    conc_live = _median(rows, "C both", "live_s")
    conc_off = _median(rows, "C both", "off_s")
    raised = [r for r in rows if "RAISED" in r.get("note", "")]

    print("\nMEDIANS")
    print(f"  live turn alone      {_fmt(base_live)}")
    print(f"  offline alone        {_fmt(base_off)}")
    print(f"  live turn, together  {_fmt(conc_live)}")
    print(f"  offline, together    {_fmt(conc_off)}")
    if base_live and conc_live:
        print(f"  live turn cost of running both: "
              f"{conc_live / base_live:.2f}x  "
              f"({conc_live - base_live:+.2f}s)")
    if base_live and base_off and conc_live:
        serial = base_live + base_off
        print(f"  a fully SERIALISED live turn would be about "
              f"{serial:.2f}s; measured {conc_live:.2f}s")

    print("\nTEXT: same transcript with and without the other half?")
    corrupt = False
    for label, cond_a, cond_b, key in (
            ("live turn", "A live alone", "C both", "live_text"),
            ("back-fill", "B offline alone", "C both", "off_text")):
        alone, together = _texts(rows, cond_a, key), _texts(rows, cond_b, key)
        if not (alone and together):
            print(f"  {label:<10} no data")
            continue
        same = alone == together
        corrupt = corrupt or not same
        print(f"  {label:<10} alone {sorted(len(t) for t in alone)} chars, "
              f"together {sorted(len(t) for t in together)} chars -> "
              f"{'IDENTICAL' if same else 'DIFFERENT'}")

    d_live = _median(rows, "D both, 2 mod", "live_s")
    if d_live and base_live:
        d_live_t = _texts(rows, "D both, 2 mod", "live_text")
        d_off_t = _texts(rows, "D both, 2 mod", "off_text")
        print(f"\n  two Models, live turn together: {_fmt(d_live)} "
              f"({d_live / base_live:.2f}x the alone baseline); "
              f"live text {'IDENTICAL' if d_live_t == _texts(rows, 'A live alone', 'live_text') else 'DIFFERENT'}, "
              f"back-fill text {'IDENTICAL' if d_off_t == _texts(rows, 'B offline alone', 'off_text') else 'DIFFERENT'}")

    print("\nANSWER")
    if raised:
        print("  CORRUPT -- something RAISED. Rows above carry the exception.")
    elif corrupt:
        print("  CORRUPT -- the transcripts CHANGED when the two ran "
              "together. Timing is beside the point: sharing one Model is "
              "not safe and the design needs the two-Model rows.")
    elif not (base_live and conc_live):
        print("  NO ANSWER -- a baseline is missing; the rows above say why.")
    elif conc_live > base_live * DEGRADE_RATIO:
        print(f"  SERIALISED -- the live turn is {conc_live / base_live:.2f}x "
              f"slower when the back-fill runs beside it. AC-69 FAILS as "
              f"designed; read the two-Model rows before choosing the fix.")
    else:
        print(f"  FINE -- the live turn is {conc_live / base_live:.2f}x its "
              f"alone baseline with a 30 s decode running beside it.")
    print("  Check the overlap column first: a C row below ~60% overlap did "
          "not test concurrency and its live time means nothing.")
    print("  Transcripts must also be IDENTICAL above, or the answer is "
          "CORRUPT whatever the timings say.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
