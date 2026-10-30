# Reflex tier serving -- research for Phase 1 step 6

Feeds plan.md section 5 Phase 1 step 6 ("Stand up the Reflex server") and
closes most of open question 8.2. Verdict schema is plan.md 4.4.

All numbers below were MEASURED on this machine on 2026-08-16 unless the line
says ESTIMATE or UNMEASURED. Probes were started and shut down inside the
research session; nothing was installed, downloaded, or left running.

---

## 1. What exists

### llama.cpp -- installed, verified, current

    E:/PROJECTS/other-project/cuda124-b10361/llama-server.exe
    version: 10361 (14e78ddef)
    built with Clang 20.1.8 for Windows x86_64

CUDA 12.4 build, self-contained (cublas64_12.dll etc. sit beside it). This is
the only mainline llama.cpp build kept on disk; the workspace AGENTS.md records
that b9194 through b10336 were deleted on 2026-08-12. A second lineage,
`cuda124-prism-b9591`, is the PrismML fork for low-bit Bonsai models and is not
relevant here.

VERIFIED TO ANSWER A REQUEST: yes. I started it directly on port 8901 three
separate times during this research and it served schema-constrained chat
completions each time. See section 4 for the numbers.

### llama-swap -- installed AND currently running

    C:/Users/user/AppData/Local/Microsoft/WinGet/Packages/
      mostlygeek.llama-swap_Microsoft.Winget.Source_8wekyb3d8bbwe/llama-swap.exe
    version: 197 (cc77139ff86da7055798c8b5dead414752e0b2f4)

Live right now as PID 23916, listening on 127.0.0.1:8077. `GET /v1/models`
returns 200 with roughly 127 aliases. `GET /running` returned `{"running":[]}`
-- the process is up but no model is loaded.

Config in use:
`E:/PROJECTS/other-project/cuda124-b10361/llama-swap-config-b10361-q8cache.yaml`

NOTE: plan.md 4.4 and section 7 both say "llama-swap was NOT running when last
probed". That is now stale. It is running, it answers, and it is started
automatically by an earlier prototype's dashboard (per the LLAMACPP AGENTS.md), not by
hand. Section 7 of plan.md should be corrected.

Two properties of that config make it UNUSABLE for the Reflex tier, and they
are the most important findings in this document:

  - `globalTTL: 900`. An idle model is unloaded after 15 minutes. Butler's
    Reflex slot is declared `"always_warm": true` in plan.md 4.4. A voice
    assistant is idle for 15 minutes constantly. Every first utterance after a
    quiet stretch would pay a full cold load.
  - It is a SWAP server. Any request for a different alias evicts the current
    model. An earlier prototype's dashboard and pi agents both drive port 8077. Butler
    would lose its warm Reflex model whenever anything else on this machine
    asked 8077 for a coding model -- silently, from Butler's point of view.

Measured cold-load cost, which is what those two properties would charge on
each recovery: 10.2 s for LFM2.5-1.2B-Q8_0, 18.1 s for Qwen3-4B-Q6_K (from the
llama-server log's own `model loaded` timestamp).

### ollama -- NOT installed

`C:/Users/user/AppData/Local/Programs/Ollama` is on PATH but the directory does
not exist. `C:/Users/user/.ollama` contains only an ssh keypair from 2024-05;
there is no `models` subdirectory. The PATH entry is a leftover. Nothing to use
and nothing to uninstall.

### LM Studio -- CLI present, zero models, not running

`C:/Users/user/.cache/lm-studio/bin/lms` is on PATH. `.cache/lm-studio/models`
is EMPTY. No LM Studio server is listening. Not a candidate.

### 9router -- running, but wrong tool for this job

Listening on 127.0.0.1:20128 (node PID 16692); `GET /login` returns 200. It is
a proxy to CLOUD providers with a static catalog; it does not serve local
GGUFs. Irrelevant to the Reflex tier. Worth knowing only so nobody wires
Butler's `reflex` slot at it by mistake.

### Ports

Only 8077 (llama-swap) and 20128 (9router) were listening. Port 8901 -- the one
plan.md 4.4 already writes into the `reflex` slot -- is free. Butler's own 8140
is free.

---

## 2. Candidate models already on disk

Everything under `D:/models`. No download is needed. Sizes are the GGUF file
on disk; `mmproj-*` vision projectors are excluded.

| model | quant | size | notes |
|---|---|---|---|
| unsloth Qwen3-0.6B | Q8_0 | 0.60 GiB | hybrid-thinking family |
| lmstudio Qwen3-0.6B | Q8_0 | 0.75 GiB | duplicate of the above |
| LiquidAI LFM2.5-1.2B-Instruct | Q8_0 | 1.16 GiB | pure instruct, no thinking |
| DeepSeek-R1-Distill-Qwen-1.5B | Q6_K | 1.36 GiB | reasoning model, wrong shape |
| unsloth Qwen3.5-2B | UD-Q6_K_XL | 1.70 GiB | thinking hard-coded in template |
| unsloth Qwen3-1.7B | Q8_0 | 1.71 GiB | hybrid thinking |
| VibeThinker-1.5B | Q8_0 | 1.76 GiB | reasoning model, wrong shape |
| bartowski Qwen3.5-2B | Q8_0 | 1.88 GiB | thinking hard-coded in template |
| CoPaw-flash-2B | Q8_0 | 2.38 GiB | agent-tuned |
| LiquidAI LFM2-2.6B | Q8_0 | 2.55 GiB | pure instruct, no thinking |
| unsloth Phi-4-mini-instruct | Q5_K_M | 2.65 GiB | pure instruct |
| LiquidAI LFM2-2.6B-Exp | Q8_0 | 2.68 GiB | pure instruct |
| unsloth Qwen3.5-4B | UD-Q5_K_XL | 2.93 GiB | thinking hard-coded in template |
| **lmstudio Qwen3-4B-Instruct-2507** | **Q6_K** | **3.08 GiB** | **non-thinking instruct release** |
| unsloth Qwen3-4B | Q6_K | 3.08 GiB | hybrid thinking |

"Thinking" is disqualifying, not cosmetic. A model that emits a reasoning block
before its verdict spends hundreds of milliseconds of the budget producing text
Butler throws away. The LLAMACPP AGENTS.md records that every Qwen3.5 template
hard-codes the thinking strip with no variable to turn it off, so the Qwen3.5
entries cannot be quieted by a flag. `Qwen3-4B-Instruct-2507` is Qwen's
separate non-thinking instruct release and has no reasoning block at all.

### Grammar / constrained decoding

llama.cpp b10361 enforces BOTH mechanisms, verified live against the running
server:

  - OpenAI style: `response_format: {"type":"json_schema", "json_schema":
    {..., "strict": true, "schema": {...}}}` on `/v1/chat/completions`.
  - Native GBNF: a `grammar` string on `/completion`.

I sent 20 schema-constrained verdict requests and got 20/20 outputs that parsed
as JSON with a `route` value inside the declared enum. This is what satisfies
rule D-11: the schema is an ENFORCED constraint at sampling time, not a request
in the prompt. Prefer `json_schema` -- it is derived from the same object
Butler already needs for validation, so there is one source of truth.

---

## 3. VRAM budget on the 1080 Ti

Card total: 11264 MiB. Measured with `nvidia-smi` throughout.

Idle baseline with the owner's normal desktop (Chrome, Brave, Edge WebView2,
Windows Terminal, PowerToys, RustDesk, a conda python): **1815-1827 MiB**. This
is not free VRAM you can plan to reclaim; it is what the machine looks like
when Butler starts.

Measured deltas, each at `--ctx-size 4096 --n-gpu-layers 99 --flash-attn on`:

| model | total with model | delta = model + KV |
|---|---|---|
| LFM2.5-1.2B Q8_0 | 3276 MiB | 1451 MiB |
| LFM2-2.6B Q8_0 | 4718 MiB | 2897 MiB |
| Qwen3-4B-Instruct-2507 Q6_K | 5774 MiB | **3959 MiB** |

Parakeet is the other GPU tenant. `models/parakeet-unified-en-0.6b-Q8_0.gguf`
is 697 MiB on disk. Its VRAM footprint under Vulkan with a live streaming graph
is UNMEASURED -- Butler's daemon does not run yet. ESTIMATE 1.2-2.0 GiB
allowing for the streaming activations.

Kokoro takes ZERO VRAM: `daemon/engines/tts/tts-model-loader.js:60` hardcodes
`device: 'cpu'` (already recorded in plan.md 7).

Worst-case budget with the recommended model:

    desktop baseline           1825 MiB   measured
    Reflex Qwen3-4B Q6_K       3959 MiB   measured
    Parakeet streaming        ~2000 MiB   ESTIMATE, high end
    ----------------------------------
    total                     ~7784 MiB
    free on an 11264 MiB card ~3480 MiB

Comfortable. VRAM is NOT the binding constraint at 4B, which was not obvious
before measuring. The binding constraint is compute contention (section 6).

If a future measurement shows Parakeet needs more than estimated, the shrink
levers in order of cost are: drop `--ctx-size` from 4096 to 2048 (the routing
prompt plus a worker list is nowhere near 2048 tokens), then swap to
LFM2-2.6B-Q8_0 (-1062 MiB), then LFM2.5-1.2B-Q8_0 (-2508 MiB). Section 4 shows
what each of those costs in accuracy and, surprisingly, in latency.

---

## 4. Measurements that decide the model

Same system prompt, same JSON schema, same 10 hand-labelled utterances covering
all four Phase 1 routes, temperature 0. Each model served by the same
llama-server binary on port 8901. This isolates the model.

| model | routing accuracy | latency p50 | latency max |
|---|---|---|---|
| LFM2.5-1.2B Q8_0 (GPU) | **3 / 10** | 93 ms | 109 ms |
| LFM2-2.6B Q8_0 (GPU) | 7 / 10 | 514 ms | 630 ms |
| Qwen3-4B-Instruct-2507 Q6_K (GPU) | **8 / 10** | 147 ms | 389 ms |

Three things in that table are worth stating plainly.

**The 1.2B is fast and wrong.** It emitted perfectly valid JSON every time and
still collapsed almost everything to `dispatch`, including "what time is it"
and "talk me through whether butler should use a message bus". Grammar
enforcement buys syntactic validity for free; it buys no semantic accuracy at
all. Anyone reading "constrained decoding solves the JSON problem" should read
this row first.

**Smaller is not faster here.** LFM2-2.6B is a gigabyte smaller than the 4B and
was 3.5x SLOWER per verdict, because it kept filling in the optional `answer`
and `question` string fields and so generated many more tokens. Verdict latency
tracks OUTPUT LENGTH far more than parameter count. The practical consequence:
keep the schema tight and do not let the model write prose it was not asked for.

**Narrowing the enum fixed most of the 4B's errors.** With `steer` present in
the enum, Qwen3-4B scored 7/10 and every single failure was a spurious `steer`,
even though the system prompt said no workers were running. Removing `steer`
from the enum -- which plan.md 4.4 already requires ("offered to the model ONLY
when at least one worker is running") -- took it to 8/10 with no other change.
So build the schema PER TURN from the live worker list rather than shipping one
static schema. A route the model cannot emit is a route it cannot get wrong.

Latency distribution for the recommended configuration, 20 consecutive
schema-constrained verdicts across 5 varied utterances:

    n=20  valid JSON 20/20
    min 131 ms  p50 140 ms  p95 168 ms  max 170 ms

Against AC-4's 1500 ms wake-to-first-audio budget, the Reflex verdict is about
9-11 percent of it. This closes the "Reflex round trip" half of plan.md 8.2:
it is not the thing that will break AC-4. Kokoro's CPU first-sentence latency
(Phase 1 step 0) remains the unmeasured risk.

The two remaining 4B failures were "run the tests" -> dispatch where I labelled
clarify, and "turn off the lights" -> dispatch where I labelled answer. Both
are arguable labels rather than clear model errors, and both are the kind of
thing few-shot examples in the system prompt normally fix. I did no prompt
tuning at all; 8/10 is the FLOOR for this model, not its ceiling.

### CPU is not an escape hatch at 4B

plan.md 7 lists "move Reflex to CPU" as the last mitigation for GPU contention.
Measured, same model, `--n-gpu-layers 0 --threads 10`:

    Qwen3-4B-Instruct-2507 Q6_K on CPU:
    min 1077 ms  p50 1233 ms  p95 1493 ms  max 1936 ms

The p95 alone consumes the ENTIRE 1500 ms AC-4 budget before STT, TTS, or the
network have spent anything. Moving the 4B to CPU does not degrade Butler
gracefully, it breaks AC-4 outright. If contention ever forces Reflex off the
GPU, the honest options are a much smaller CPU model with the 1.2B's accuracy
problem, or a renegotiated AC-4. Plan.md 7's mitigation list should say so.

---

## 5. Recommendation

**Run a DEDICATED llama-server process for the Reflex tier on port 8901, owned
and supervised by butlerd. Do not route the Reflex tier through the shared
llama-swap on 8077.**

Model: `Qwen3-4B-Instruct-2507-Q6_K`, already on disk, fully GPU-offloaded.

Rationale in one line each:

  - Dedicated, not llama-swap, because `globalTTL: 900` and model swapping both
    break `"always_warm": true`, and 8077 is shared with an earlier prototype's dashboard
    which can evict Butler's model without Butler knowing.
  - Qwen3-4B-Instruct-2507 because it was both the most accurate (8/10) and,
    apart from the useless 1.2B, the fastest (p50 140 ms) model tested, it is
    a non-thinking instruct release so it emits no wasted reasoning block, and
    it costs 3959 MiB against roughly 3480 MiB of measured spare headroom.
  - Reuse the b10361 binary in place rather than vendoring a copy. plan.md's
    independence rule forbids importing CODE from an earlier prototype and sharing SERVICES;
    E:/PROJECTS/other-project is neither, it is a local toolchain, and it is the
    only build on this machine verified against this GPU. Record the absolute
    path and the exact version string in `config/models.json` so the
    dependency is explicit rather than ambient. If the owner would rather have
    zero cross-directory dependency, copy the ~15 files of the build folder
    into `butler/vendor/llama-b10361/` and change one path below; nothing else
    in this document changes.

### Exact start command

    E:\PROJECTS\other-project\cuda124-b10361\llama-server.exe ^
      --port 8901 ^
      --host 127.0.0.1 ^
      --model "D:\models\lmstudio-community\Qwen3-4B-Instruct-2507-GGUF\Qwen3-4B-Instruct-2507-Q6_K.gguf" ^
      --alias butler-reflex ^
      --ctx-size 4096 ^
      --n-gpu-layers 99 ^
      --threads 8 ^
      --flash-attn on ^
      --parallel 1 ^
      --temp 0.0 ^
      --top-k 1 ^
      --no-webui ^
      --metrics

Flag notes, since several are load-bearing:

  - `--n-gpu-layers 99` not `--fit on`. The LLAMACPP configs use `--fit on`
    everywhere, which auto-selects layers against whatever VRAM is free AT LOAD
    TIME. For an always-warm process that boots alongside Parakeet, that is
    non-determinism in the one number Butler most needs to be stable. Pin it.
    If 99 ever fails to fit, that is a loud load failure, which is what we
    want, rather than a silent slide to partial CPU offload and 1200 ms
    verdicts.
  - `--ctx-size 4096` is generous. The system prompt plus an utterance plus a
    live worker list is a few hundred tokens.
  - `--parallel 1` because Butler issues exactly one verdict at a time; extra
    slots only divide the KV cache.
  - `--temp 0.0 --top-k 1` -- classification wants determinism and
    reproducible tests, not sampling diversity.
  - No `--cache-type-k/v q8_0`. The LLAMACPP configs quantise KV to stretch big
    models; at 4 K context the saving is tens of megabytes and it is not worth
    any accuracy risk on the one call that decides every turn.
  - `--no-webui` -- nothing should be able to drive this port by hand.
  - `--metrics` exposes `/metrics` for the Phase 1 bench and the AC-7 soak.

### Supervised child, not an external dependency

butlerd SPAWNS and OWNS this process. Reasons: `always_warm` is a promise
butlerd makes, so butlerd must own the lifetime that keeps it true; a dead
Reflex server must be a Butler-visible event, not something the owner discovers
by voice; and one process tree means killing butlerd leaves no orphan holding
3959 MiB of VRAM.

Concretely:

  - Boot order: spawn the Reflex child EARLY, in parallel with the STT and
    Kokoro warmups, because its 18.1 s cold load is the longest of the three
    and there is no reason to serialise it.
  - Startup uses a SEPARATE, generous budget from the steady-state timeout.
    This is plan.md rule D-5 (the Kokoro cold-start lesson) applied to the same
    class of problem: measured load is 18.1 s, so poll `GET /health` for
    `{"status":"ok"}` with a 90 s ceiling. Do NOT reuse the per-verdict
    deadline for the load.
  - Health check is a real verdict, not just `/health`. At the end of boot
    warmup, send one schema-constrained classification of a fixed utterance and
    assert the response parses and the route is in the enum. `/health` returning
    ok only proves the HTTP server is up; it does not prove the grammar path
    works or the template rendered. This also warms the prompt cache -- the
    measured `cached_tokens: 75` on repeat calls is why p50 is 140 ms.
  - Provide `--reflex-external` for development, which skips the spawn and
    treats 8901 as a documented external dependency. It runs the SAME health
    check and fails just as loudly if nothing answers. This is a convenience
    for not paying 18 s on every daemon restart, not a fallback: there is no
    path in either mode where Butler runs without a working Reflex server.

Write into `config/models.json` (plan.md 4.4 already reserves the shape):

    "reflex": {
      "kind": "openai",
      "base_url": "http://127.0.0.1:8901/v1",
      "model": "butler-reflex",
      "always_warm": true,
      "managed": true,
      "server_bin": "E:/PROJECTS/other-project/cuda124-b10361/llama-server.exe",
      "server_version": "10361 (14e78ddef)",
      "gguf": "D:/models/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/Qwen3-4B-Instruct-2507-Q6_K.gguf",
      "load_timeout_s": 90,
      "verdict_deadline_ms": 1200
    }

Pinning `server_version` matters: llama.cpp changes grammar and template
behaviour between builds, and a future b10xxx that silently alters JSON schema
handling would otherwise be invisible until a verdict came back wrong.

---

## 6. Failure policy

The governing constraint is that "the Reflex server is down" must NEVER become
"quietly ask the cloud tier instead". That substitution would send every
utterance -- including ones the router would have refused or clarified -- to a
paid endpoint with no routing verdict at all, which is both a cost leak and a
safety hole, because from Phase 2 onward the verdict is what the Mode Store
gates writes on.

**One distinction has to be stated explicitly, because plan.md invites the
mistake.** Section 3.8 defines a deliberate exception to fail-fast: a Reflex
verdict that fails to PARSE routes to `converse` and is logged loudly. That
exception is about bad MODEL OUTPUT from a healthy server. It does NOT extend
to an absent, dead, or timed-out server. Those are infrastructure failures and
they fail fast. Anyone implementing step 6 will be tempted to reuse the
parse-failure branch for connection errors; that is the bug this paragraph
exists to prevent.

Four distinct failures, four defined behaviours:

**(a) Reflex will not start at boot.** butlerd EXITS non-zero. It does not
start the WebSocket server, does not accept satellites, and prints the last 40
lines of the llama-server log to stderr along with the command it tried to run.
A voice assistant that boots without its router is a trap: it looks alive and
answers the first question from the cloud tier. Refusing to boot is the honest
outcome, and it is the one moment when a hard exit costs the user nothing,
because nobody is mid-conversation.

**(b) Port 8901 is already occupied at boot.** Also a hard exit, with a message
naming the PID holding the port and telling the owner to kill it or pass
`--reflex-external`. Do not silently adopt an unknown server -- it could be any
model at any quant, and Butler would then be routing on something it cannot
name in its logs. Under `--reflex-external` this case is the normal path, and
the boot verdict check in section 5 is what makes adoption safe.

**(c) The child dies while Butler is running.** This is where fail-fast meets
rule D-1, which forbids latching an error into a state that disables the
microphone, and AC-2, which requires every terminal path to re-arm. Killing the
daemon would also strand any running worker. So:

  1. Mark the Reflex tier UNAVAILABLE in daemon state.
  2. Speak a short spoken error on the bound device -- "my local router is
     down, I can't route that" -- and emit `{type:"error", message:...}` to
     every satellite. The user learns immediately, by ear.
  3. END the turn and RE-ARM to wake-listening. The turn is lost. It is not
     retried against the cloud tier.
  4. Attempt supervised restart with backoff (1 s, 5 s, 30 s, then every
     60 s), logging each attempt. On a successful restart, run the same boot
     verdict check from section 5 before clearing UNAVAILABLE.
  5. While UNAVAILABLE, every wake still opens a capture and every turn still
     ends with the same spoken error and a clean re-arm. Butler stays
     responsive and stays useless in exactly one visible way, which is the
     behaviour that gets fixed. Silent cloud routing is the behaviour that
     never gets fixed.

**(d) A verdict exceeds its deadline.** Hard per-request deadline of 1200 ms
(measured p95 is 168 ms, so this is a 7x margin and should essentially never
fire). On expiry: cancel the request, treat it as case (c) for that turn --
spoken error, clean re-arm -- and log the utterance and elapsed time. Do not
retry inside the turn; a second 1200 ms wait turns a bad turn into an
unbearable one. A deadline that fires repeatedly is the early warning for the
contention risk in section 7, which is exactly why it must be logged rather
than swallowed.

Not recommended: continuous `/health` polling. It adds a timer and proves
little. The signals that matter are the child's process exit, which butlerd
already watches as its supervisor, and a failed or slow verdict on a real call.

---

## 7. Top risk

**GPU contention between Reflex generation and Parakeet's Vulkan streaming
graph, which I could not measure.**

Every latency number here was taken on an otherwise idle GPU. Butler's daemon
does not run yet, so Parakeet was not streaming during any probe. That is the
single largest gap in this research and I am flagging it rather than
interpolating past it.

Why it is the top risk rather than a footnote. The two tenants use different
APIs -- Parakeet on Vulkan, llama.cpp on CUDA -- so they contend through the
driver and the Pascal scheduler, not through a shared runtime that could
interleave them intelligently. The damage is bidirectional and the second
direction is worse: Reflex verdicts getting slower is a latency regression
against a budget that currently has 9x headroom, but Reflex generation stalling
Parakeet's decode is a TRANSCRIPTION regression, and plan.md 7 already records
an earlier prototype measuring STT falling about 30 s behind under GPU contention before the
560 ms chunk configuration landed. AC-4 has room to absorb the first. AC-1 does
not absorb the second.

The risk is made sharper by section 4's CPU result: the mitigation plan.md 7
names last -- move Reflex to CPU -- is measured at p95 1493 ms and does not
work at 4B. So if contention is severe, the fallback ladder is shorter than the
plan assumes.

**What would falsify it.** The Phase 1 bench in step 10, extended by one
scenario, run before the model choice is treated as final:

  - Start butlerd with Parakeet warm and streaming a known WAV in the
    `(70,7,7)` 560 ms configuration.
  - While STT is actively producing partials, issue 50 Reflex verdicts.
  - Record three things together: Reflex p50 and p95; the interval between STT
    partials, which should stay near 560 ms; and `nvidia-smi` peak memory.

Pass condition: Reflex p95 stays under about 600 ms AND STT partial cadence
shows no growing lag. Under those numbers, this recommendation stands as
written.

Fail condition and what it means: if Reflex p95 goes above 600 ms, or STT
partials start drifting, the recommendation is falsified and the next step is
to test LFM2-2.6B under the same contention -- accepting 7/10 accuracy and,
per section 4, likely no latency improvement -- before considering a renegotiated
AC-4. Do not reach for CPU offload; section 4 already ruled it out at 4B.

Second, smaller risk, stated for completeness: 8/10 routing accuracy on ten
hand-labelled utterances is a thin sample from a single author, and I did zero
prompt tuning. The routing accuracy number should be re-established in Phase 1
against a proper labelled set of the owner's real utterances, with few-shot
examples in the system prompt. Treat 8/10 as evidence that this model class is
viable, not as a measured quality bar.

---

## 8. Corrections this research makes to plan.md

  - Section 7 and section 4.4: "llama-swap was NOT running when last probed" is
    stale. It runs, on 8077, auto-started by an earlier prototype's dashboard.
  - Section 7's mitigation ladder: "move Reflex to CPU" does not work at 4B
    (p95 1493 ms). Either name a smaller CPU model or drop the rung.
  - Section 8.2: the "Reflex round trip" component is now measured at p50
    140 ms / p95 168 ms, about 9-11 percent of AC-4's budget. The VRAM
    component is measured at 3959 MiB against roughly 3480 MiB spare. What
    stays open is only (c), STT cadence under contention.
  - Appendix A should gain: Reflex verdict p50 140 ms / p95 168 ms
    [MEASURED 2026-08-16], Reflex VRAM 3959 MiB [MEASURED 2026-08-16], Reflex
    cold load 18.1 s [MEASURED 2026-08-16].

## 9. What was NOT verified

  - Parakeet's actual VRAM footprint under Vulkan (estimated 1.2-2.0 GiB).
  - Any concurrent Reflex-plus-Parakeet behaviour whatsoever. See section 7.
  - Kokoro's CPU first-sentence latency, which remains plan.md Phase 1 step 0
    and is untouched by this document.
  - Routing accuracy on real owner utterances, or with a tuned prompt.
  - Long-run stability of the Reflex server. The longest probe ran a few
    minutes; AC-7's 24-hour soak must include this process in its per-process
    RSS list.
