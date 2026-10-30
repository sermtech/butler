# Kokoro CPU TTS benchmark -- the AC-4 gate

This is Phase 1 step 0 of `plan.md` section 5: measure Kokoro's CPU
first-sentence latency **before** AC-4's 1500 ms is treated as a commitment.
Section 7 names this as "the real AC-4 risk", and section 4.7's design assumed
a number nobody had. Here is the number.

Every figure below came from running `test/bench_tts.mjs` against the real
`TTSService` / `tts-worker.js` path on 2026-08-16. Nothing is estimated.

## Reproduce

```
cd E:\PROJECTS\butler
BENCH_RUNS=20 BENCH_WARMUP=3 node test/bench_tts.mjs
```

To reproduce the cold-start-with-download row, delete the transformers.js model
cache first:

```
rm -rf node_modules/@huggingface/transformers/.cache
```

The script prints a running log, writes the full raw data to
`test/bench_tts_results.json`, and echoes the same JSON between
`BENCH_JSON_BEGIN` / `BENCH_JSON_END`. It also runs an internal `--cold` mode in
freshly spawned child processes; you do not invoke that directly.

## Hardware and versions

| Item | Value |
| --- | --- |
| CPU | 12th Gen Intel Core i7-12700K, 20 logical processors (8 P + 4 E) |
| RAM | 68.5 GB |
| OS | Windows 11, win32 10.0.22631 |
| Node | v22.17.0 |
| kokoro-js | 1.2.1 |
| @huggingface/transformers | 3.8.1 |
| onnxruntime-node | 1.22.0 (pinned by the `overrides` block in `package.json`) |
| Model | `onnx-community/Kokoro-82M-v1.0-ONNX`, dtype **fp32**, `device: 'cpu'` |
| ONNX session | `intraOpNumThreads=4`, `interOpNumThreads=1`, `executionMode=sequential` |
| Worker pool | 1 (the `_resolvePoolSize` default) |
| Voice | `af_heart`, output 24 000 Hz mono |

The thread cap of 4 is not a choice made for this benchmark: `TTSService._ttsThreads()`
computes `max(2, min(4, floor(cpuCount / 2)))`, which is 4 on this box.

## 1. Cold start

`start()` covers model load, tokenizer load, and the `tts.generate(' ')` ONNX
pre-warm inside `tts-worker.js`. Each probe ran in a freshly spawned Node
process, so "launch to ready" includes Node boot and the native ONNX binding.

| Probe | Model cache | `start()` | process launch to ready |
| --- | --- | --- | --- |
| 1 (first ever run) | empty -> 325.5 MB | **7 759 ms** | 7 852 ms |
| 2 | populated | **1 215 ms** | 1 253 ms |
| 3 | populated | **1 128 ms** | 1 164 ms |
| in-process (already-booted Node) | populated | 1 105 ms | n/a |

**Download is one-time and costs about 6.6 seconds** (7 759 minus the 1 171 ms
mean of the two cached probes) for 325.5 MB, of which `onnx/model.onnx` at fp32
is the bulk. **Per-restart cold start is about 1.2 seconds**, which is the number
that matters operationally: a daemon restart puts TTS out of service for roughly
that long, and the separate 180 s `START_TIMEOUT_MS` in `tts-service.js` has
enormous headroom over it.

## 2. Warm first-sentence latency

n = 20 timed runs per case, **3 warm-up runs discarded per case** (plus 12
further synthesis calls consumed earlier by the CPU-sampling burst, and a
`generate(' ')` pre-warm inside `start()`). Percentiles are nearest-rank, so at
n = 20 the p95 is the 19th of 20 sorted values.

"First audio" = the first chunk yielded by `TTSService.synthesizeStream()`.
"Complete" = the generator finishing. RTF = audio seconds produced divided by
total synthesis seconds; higher is better, 1.0 is realtime.

| Case | Words | Sentences | Audio produced | First audio p50 | First audio p95 | Complete p50 | Complete p95 | RTF p50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| short reflex -- "It is twenty past four." | 5 | 1 | 2 050 ms | **448 ms** | **468 ms** | 448 ms | 468 ms | 4.57 |
| medium -- one 19-word sentence | 19 | 1 | 6 500 ms | 1 784 ms | 1 949 ms | 1 784 ms | 1 949 ms | 3.63 |
| long -- one 38-word sentence | 38 | 1 | 13 250 ms | 3 710 ms | 4 158 ms | 3 710 ms | 4 158 ms | 3.54 |
| long -- 40 words as 3 sentences | 40 | 3 | 13 800 ms | **673 ms** | **801 ms** | 3 309 ms | 3 885 ms | 4.16 |

Range for the short case: min 434 ms, max 473 ms, mean 453 ms. The 20 raw
first-audio samples in run order, in milliseconds:

```
447.9 445.3 434.2 434.7 441.6 448.1 446.6 444.1 466.8 473.3
467.1 468.4 464.7 465.8 457.8 457.7 434.3 456.1 446.0 448.6
```

That is a spread of 39 ms across 20 runs -- fp32 Kokoro on this CPU is boringly
stable, which matches the dtype note in `tts-model-loader.js` about q8 being
~140x jitterier.

The three-sentence case yielded its chunks at 814 ms, 2 313 ms and 3 889 ms in
the first timed run: each sentence arrives as soon as it is synthesized.

## 3. Does the path stream?

**At sentence granularity, yes. Below a sentence, no. Over HTTP, not until
2026-08-17 -- see section 3a, which is the answer that holds now.**

`tts-sentence-stream.js:streamSentences()` splits on `.!?`, then `await`s a
whole `tts.generate(sentence)` before emitting. One kokoro `generate()` is a
single bounded native ONNX call that returns the complete waveform for that
sentence; there is no partial output to forward. The measurements confirm it:
for all three single-sentence cases, first-audio and complete differ by
0.1 ms -- one chunk, emitted at the end. Only the three-sentence case shows
incremental delivery.

The shipped `tts-sidecar.mjs` then throws that away. It drains the whole async
generator into an array, wraps it in one WAV, and sends it as a single
`audio/wav` response:

| HTTP case | Time to first response byte p50 | Time to complete body p50 | Body |
| --- | --- | --- | --- |
| short | 518 ms | 519 ms | 98 444 B WAV |
| medium | 1 959 ms | 1 960 ms | 312 044 B WAV |
| long (1 sentence) | 3 723 ms | 3 723 ms | 636 044 B WAV |
| long (3 sentences) | 3 925 ms | 3 926 ms | 662 444 B WAV |

First byte and last byte are the same instant everywhere. For the 3-sentence
reply this costs 3 252 ms of avoidable latency: 3 925 ms to first byte over
HTTP versus 673 ms to the first sentence through `synthesizeStream()` directly.

Implication for plan.md 4.3, which specifies "24 kHz mono int16 PCM" TTS frames
downstream: the underlying service already produces exactly that (measured
sample rate 24 000, raw PCM16 LE from `float32ToPcm16ArrayBuffer`), but the
sidecar's HTTP wrapper must be replaced by the daemon consuming
`synthesizeStream()` and forwarding each chunk over the WebSocket as it lands.
That is a rewrite of the transport, not of the engine.

## 3a. After the streaming transport (2026-08-17)

That rewrite landed (plan.md section 5 step 8). `tts-sidecar.mjs` now writes
one framed PCM chunk per sentence and takes `POST /abort`.

**Read the two dates against each other with care.** This machine measured
about half its 2026-08-16 throughput on the day of the rewrite -- the 5-word
case took 912 ms in-process against the 448 ms recorded above, and realtime
factor came out at 2.0-2.2 rather than 3.5-4.6. The cause was not investigated
and is not the transport, which this section is not measuring. So every
comparison below is drawn WITHIN the 2026-08-17 run, where both terms share
the same machine state.

| 2026-08-17 case | In-process first chunk | Over HTTP, first byte | Over HTTP, complete |
| --- | --- | --- | --- |
| short (5 words, 1 sentence) | 912 ms | 870 ms | 871 ms |
| medium (19 words, 1 sentence) | 3 045 ms | 3 458 ms | 3 459 ms |
| long (38 words, 1 sentence) | 6 495 ms | 7 109 ms | 7 150 ms |
| **long (40 words, 3 sentences)** | **1 351 ms** | **1 242 ms** | **6 070 ms** |

The three-sentence row is the whole point. First byte and complete are now
1 242 ms and 6 070 ms -- different instants, where before they were 3 925 ms
and 3 926 ms. And over-HTTP time to first audio now matches the in-process
generator within noise (1 242 against 1 351), so **the +3 252 ms transport
penalty is gone rather than reduced.** Single-sentence cases are unchanged by
construction: one sentence is one chunk, and there is nothing to overlap.

Nothing is lost in the framing, which is checkable rather than asserted. Every
response is the same PCM as before plus a 16-byte header per frame and one
end frame:

| case | PCM bytes | framing | expected | measured |
| --- | --- | --- | --- | --- |
| short | 98 400 | 2 x 16 | 98 432 | 98 432 |
| medium | 312 000 | 2 x 16 | 312 032 | 312 032 |
| long (1 sentence) | 636 000 | 2 x 16 | 636 032 | 636 032 |
| long (3 sentences) | 662 400 | 4 x 16 | 662 464 | 662 464 |

### Abort now reaches the engine, but not instantly

Measured through the real adapter on a deliberately hostile case -- ten
sentences in ONE `synthesize` call, aborted a quarter of the way in:

| | next reply's first audio |
| --- | --- |
| before the cancel route | 7 887 ms |
| after | 3 736 / 3 758 / 4 392 ms |

The residual is real and worth stating plainly, because plan.md predicted "the
orphan cost goes to about zero" and that is not what happened. Two sentences
are typically stranded: the one already inside kokoro's native `generate()`,
which has no cooperative cancellation, and usually the one after it, because
`tts-service.js` only sends its abort when the generator is resumed and the
generator is resumed by the next chunk. Removing the second would mean giving
`TTSService` a public abort-by-id instead of relying on generator
finalisation.

The daemon does not issue ten-sentence calls: `speech.segments` caps a segment
at 16 words, so a real barge-in strands roughly one short sentence. This
worst case exists to keep the cancel route honest, not to describe a turn.

## 4. CPU usage

Sampled the worker child process's `TotalProcessorTime` every 200 ms from a
single long-lived PowerShell process (198 usable intervals) across a 41.5 s
back-to-back burst of twelve 38-word synthesis calls producing 159 s of audio.

| Metric | Cores busy |
| --- | --- |
| p50 | 2.32 |
| p95 | 3.17 |
| max | 3.72 |
| mean | 2.32 |

So synthesis is genuinely multi-core but bounded: it uses two to four of the
twenty logical processors, capped by `intraOpNumThreads=4`. Sixteen logical
cores stay free for streaming ASR and the rest of the daemon. Contention with
ASR is therefore real but not catastrophic at the default settings -- and note
that raising `TTS_ONNX_THREADS` to buy first-sentence latency would eat directly
into whatever the ASR path needs. That trade has not been measured.

## 5. Verdict on AC-4's 1500 ms

**AC-4's 1500 ms survives, but only for short first sentences, and the plan must
now say so explicitly.** A five-word reflex answer reaches first audio in 448 ms
at p50 and 468 ms at p95 -- comfortably under the ~700 ms threshold that
plan.md section 5 step 0 set as the trigger for re-setting the target, and
leaving roughly 1 030 ms of the budget for wake finalize, ASR final, the Reflex
verdict, and the hop to the satellite. That remaining 1 030 ms is still
unmeasured, so AC-4 is not yet proven, only un-falsified on its TTS term. The
danger is not the short case, it is the assumption that replies are short:
a single 19-word sentence takes 1 784 ms to first audio and blows the entire
1500 ms budget on synthesis alone, and a 38-word one takes 3 710 ms. Realtime
factor sits at 3.5-4.6x across every length, so the rule this measurement yields
is simple and should be enforced in code rather than hoped for: **synthesis time
is roughly the spoken duration divided by four, so the first sentence of any
reflex reply must be under about two seconds of speech -- call it eight words --
or AC-4 fails on that turn.** The concrete Phase 1 actions are to cap the first
sentence in the splitter (section 4.7's sentence-level synthesis already gives
the seam), and to make the daemon forward chunks from `synthesizeStream()`
instead of going through the sidecar's whole-WAV HTTP response, which for a
multi-sentence reply is worth 3.2 seconds on its own.

## 6. Power throttling moves the worker onto the E-cores (2026-10-09)

Windows 11 EXECUTION_SPEED throttling demotes a background process's threads to
the E-cores (CPU16-19 on this 8P+4E box) after a few seconds of sustained work.
Butler's live sidecar measured rtf 0.31-0.87, against ~4x here at idle. The
sibling repo's kokoro worker showed the same and was fixed by clearing the
throttle bit; after it 120 s of synthesis held rtf median 4.72, min 3.89.

Fix in Butler: `daemon/engines/winqos.py`, applied to the daemon, the Smart Turn
child, the sidecar and every kokoro worker on start and respawn (contract in
`daemon/engines/AGENTS.md`). Fresh sidecar driven for 120 s, one request per
sentence (~20 words), same machine, Butler live alongside:

| run | pieces | median rtf | min | max | pieces < 1.5x |
| --- | --- | --- | --- | --- | --- |
| opt-out applied | 67 | 4.21 | 2.57 | 4.54 | 0 |
| control (no opt-out) | 55 | 3.23 | 2.60 | 4.12 | 0 |

The control run did NOT reproduce the 0.3-0.9x collapse: throttling depends on
OS state (background/occluded process, power policy) that a foreground-launched
bench does not enter, and the two runs drifted 10-30% on their own. So this
table shows the opt-out holds speed; it does not prove the demotion on demand.
The decisive check is the live daemon's rtf after its next restart.

## What this benchmark does not cover

- **Concurrent load.** Every number here was taken with an otherwise idle
  machine. The real question -- first-sentence latency while streaming ASR is
  running on the same box -- needs the ASR pipeline up and was not measured.
- **The satellite.** AC-4's clock stops at the first byte reaching the
  satellite. This measures only up to the daemon. WebSocket transport and
  client-side queueing are not included.
- **Sub-sentence latency.** Not measurable with the current engine: kokoro-js
  `generate()` returns a whole sentence or nothing.
- **Other dtypes.** Only the configured fp32 default was benchmarked; fp16 and
  q8 were not tried as a latency mitigation.
- **Very short utterances.** The shortest case measured is five words. Whether
  a one- or two-word reply ("Done.") is meaningfully faster than 448 ms is
  unknown; the 2 050 ms of audio Kokoro produced for five words suggests a
  fixed padding component that may dominate at that size.
