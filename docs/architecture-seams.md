# Architecture seams -- spec and refactor plan

Written 2026-08-17. A SPEC, not a change: nothing described in sections 3-6
exists unless it also appears in the git log. plan.md remains the build
contract; if this document and plan.md disagree, plan.md wins until the owner
promotes a decision from here into it.

**[STATUS 2026-08-18.]** All seven phases are ACCEPTED and AC-38..AC-45 live
in plan.md section 2, so this document is a record rather than a proposal.
One thing here was overtaken later the same day: the owner's four rulings
(docs/harness-mode.md section 10) deleted the spoken write grant, made the
harness child per-message, and replaced the session store with one flat chat
file. Where this document still says a harness brain supplies a tool set, or
names `session_dir`, read plan.md 4.4c and AC-35 instead. Section 3.5's
promotion of `Harness` to a fifth port is untouched by those rulings, and so
is section 3.4's rule that a preference reaches an adapter through a
CALLABLE and never through a store -- the rulings changed what the callable
answers, not that there is one.

Scope: the ports (plan.md 4.10), the places engine vocabulary has leaked out of
its adapter, the missing harness seam, and a home for the user-definable token
cap. Two owner decisions landed after the first draft and are folded in: the
seam refactors (phases 1-4) run BEFORE connections rather than after, and
`Harness` is PROMOTED to a fifth port rather than kept as a sub-seam (3.5).
Section 1 describes four ports because four is what the code had when it was
read; `Harness` landed in ports.py on 2026-08-17 (docs/harness-mode.md step
7.1) and the count is five. House rules honoured throughout: no plugin framework, no DI
container, no event bus, no entry-point discovery (plan.md section 7,
ports.py registry comment). Numbers carry provenance tags; line numbers are
[READ] against the working tree at commit ee1d8a0.

---

## 1. What is actually true today

Everything in this section was READ in the code unless marked INFERRED or
UNVERIFIED.

### 1.1 The four ports, seam by seam

**Source -- MISSING as a file, sound as a shape.** `daemon/ports.py:42-60`
defines the protocol; no adapter file implements it. The WebSocket source is
inlined in `daemon/hub.py` (the binary-frame branch at hub.py:244-249 feeds
`session.pipeline` directly). plan.md section 5 step 3 already states this
plainly ("Source has no adapter file at all"), so this is a known gap, not a
discovery. Verdict: missing, and honest about it.

**Asr -- SOUND shape, LEAKING vocabulary.** The session-not-a-call shape
(open/feed/finalize/reset_turn/close, exactly one Final even for silence,
D-7) is right and conformance-proven by two implementations
(`test/conformance/test_asr_conformance.py`). The leak is `AsrConfig` at
ports.py:66-71 [READ]:

- `backend: str = "auto"  # auto | vulkan | cpu` -- Vulkan is a
  transcribe.cpp deployment fact. Deepgram or Soniox has no backend axis.
- `latency: str = "balanced"  # the spike's (70,7,7) 560 ms chunking` -- the
  values low/balanced/high name parakeet chunking presets. The comment in the
  SHARED contract file explains one engine's internals.

`model` and `language` are universal and belong where they are. The
engine-specific two sit in the file whose docstring says "Nothing here imports
an engine"; they do not import one, but they NAME one's vocabulary.

**Tts -- SOUND core, LEAKING at the edges.** The chunk-iterator shape plus
handle-carrying abort is the port that paid off most (plan.md 4.10 item 1;
+3252 ms transport penalty removed [MEASURED 2026-08-16/17]). The leaks:

- `voice` is a loose positional `str` (ports.py:147). Workable, but there is
  no way to ask a Tts WHAT voices it has, so the voice list is hardcoded
  elsewhere (next two bullets).
- `daemon/hub.py:200-205` `_voice()` returns the literal `"af_heart"` -- a
  Kokoro voice name in the daemon's core turn logic, twice (the settings=None
  fallback and the `get` default). [READ]
- `daemon/settings.py:91-96` hardcodes eleven Kokoro voice names as the
  `voice` setting's choices, with default `"af_heart"`. The settings panel
  renders from the schema (web/AGENTS.md), so the browser UI inherits the
  Kokoro list from here. Swap the engine and the schema still offers voices
  the new engine has never heard of. [READ]
- `test/conformance/test_tts_conformance.py:31` pins `VOICE = "af_heart"` for
  every adapter -- acknowledged in its own comment as "a voice every adapter
  can actually speak", which is only true while every adapter is Kokoro or a
  fake that accepts anything. [READ]
- `SAMPLE_RATE_OUT = 24_000  # (Kokoro's native)` at ports.py:36. This one is
  NOT a leak: the port fixes the wire format and the comment records why the
  number is what it is. Adapters convert at their own edge (ports.py:33-34).
  Verdict: sound, keep.

**Brain -- SOUND security shape, MISSING three surfaces.** Intents-not-text
and proposes-not-acts are right and untouched here. Missing, each verified
against ports.py:269-279 [READ]:

1. No configuration of any kind crosses it: `turn(text, ctx)` and ctx carries
   turn_id, mode, history, workers. There is no home for a token cap or any
   generation parameter -- confirmed by grep: no `max_tokens`, `temperature`
   or `top_k` anywhere under `daemon/` [READ 2026-08-17].
2. No `warm()`. `butlerd.warmup()` at butlerd.py:128 iterates
   `("asr", hub.asr), ("tts", hub.tts)` only. plan.md step 6 lists "the
   router's own health check at boot" as STILL TO DO with nowhere to put it.
3. No `info`. `hub._hello` (hub.py:290-292) sends `stt=self.asr.info` and
   nothing for the other engines; `info` exists on Asr alone.
4. No cancellation surface. What happens on barge-in mid-Brain-turn, READ
   from hub.py:119-163: `abandon()` calls `tts.abort` (only when state is
   SPEAKING), then cancels `reply_task` with a 3.0 s bounded wait. The
   CancelledError lands at whatever await the async generator is suspended
   on. For `brain_fake` that is instant and harmless. INFERRED, not verified:
   for a future `brain_endpoint` the cancellation would abort the in-flight
   aiohttp request, and whether the SERVER then stops generating is a
   property of llama-server's disconnect handling, not of anything Butler
   asserts. For a future `brain_harness` (a child process mid-turn), task
   cancellation stops the READER and nothing else -- the child keeps working.
   That is "cancelling the consumer is not cancelling the producer", the
   exact D-4 failure, one port over, waiting for step 7.6 to trip it.

### 1.2 Cross-cutting facts

- **Configuration crosses the three engine ports three different ways.**
  Asr: a frozen dataclass into `open()`. Tts: a loose positional string per
  call. Brain: nothing. All three answer "how does the caller configure the
  engine" and no two answer it alike. [READ]
- **There is no Harness seam.** `grep -rn harness daemon/*.py` finds:
  `Action.harness: str | None` (a dispatch proposal field, ports.py:187),
  `RunningWorker.harness` (ports.py:232), and a comment in butlerd.py:43-44.
  plan.md 4.4c specifies a full backend contract (prompt / subscribe / abort
  / capabilities / native_resume, transposed from an earlier prototype) and 4.5 specifies
  worker spawning -- TWO future consumers of the same contract -- and no
  Python file holds it. plan.md 4.10's closing line "The worker harnesses are
  already adapter-shaped through config/models.json and need no port" was
  written before 4.4c made a harness something you TALK to; it is stale
  prose in the D-12 sense. [READ]
- **Connections (plan.md 4.4b) are design only.** No `daemon/connections.py`,
  no `state/connections.json` handling, no auth-pointer resolution anywhere
  in code. `endpoint_url` (settings.py:125) is the single-URL predecessor the
  plan says is superseded. [READ]
- **Tests exercise the settings=None fallback path.** `test/conftest.py:146-152`
  builds Hubs without settings, so hub.py's `"af_heart"` literal is live in
  the suite, not dead code. [READ]
- UNVERIFIED in this pass: `web/settings.js` internals (trusted from
  web/AGENTS.md and the handoff), `tts_kokoro.py` below line 120 and
  `asr_transcribecpp.py` below line 120 (their contracts are asserted by the
  conformance suite and adapters/AGENTS.md, which I read in full).

---

## 2. The failure modes this creates

Each tied to a swap the owner has said he wants.

**Swap Kokoro for Piper or ElevenLabs.** The new adapter passes the whole Tts
conformance suite and Butler still misbehaves, because the voice vocabulary
lives outside the adapter. What breaks and where:

- `daemon/settings.py:91-96`: the voice picker offers eleven Kokoro names;
  every one is invalid on the new engine. Saving reports success (the schema
  validates against its own stale list) and the engine then fails per reply
  -- or worse, falls back to some engine-default voice, the AC-7b
  spoke-anyway defect that already shipped once for a day.
- `daemon/hub.py:204-205`: the fallback voice is a Kokoro name.
- `test/conformance/test_tts_conformance.py:31`: the shared VOICE constant is
  a Kokoro name, so the new real adapter fails setup, not contract.
- Files that must change today for this swap: settings.py, hub.py, the
  conformance suite, plus the new adapter. Under the proposal: the new
  adapter only.

**Swap parakeet for whisper.cpp or Deepgram.** Structurally survivable -- the
session shape is engine-neutral -- but the new adapter receives
`cfg.backend="vulkan"` and `cfg.latency="balanced"` and must silently ignore
or reinterpret them, and `daemon/settings.py:195-208` (engines group) offers
parakeet model names and chunking presets that mean nothing to it. Files that
must change today: ports.py (AsrConfig vocabulary), settings.py, butlerd.py,
plus the adapter. Under the proposal: settings.py and butlerd.py wiring, plus
the adapter -- the shared contract file stays untouched, which is the point.

**Add a second (or third) harness.** Nothing breaks today because nothing
exists -- and that is the trap. Step 7.6 (brain_harness) and Phase 2 step 3
(worker manager for claude) will BOTH need prompt/subscribe/abort/dispose
against a CLI, and if each grows its own, the third harness rewrites two call
sites. plan.md 4.4c already names the defect this causes ("a caller must
never test the harness's name to decide, or the third harness rewrites every
call site"). The seam must exist before the first consumer, not after the
second.

**Point the brain at a different server or model.** Blocked on connections
(4.4b), which is specified but unbuilt. The design risk this document rules
on: where a brain adapter gets its connection WITHOUT reaching into a global
store -- section 3.4.

**Barge-in over a slow brain.** With brain_fake, unobservable. The moment
brain_endpoint exists, a barge-in mid-generation cancels Butler's reader and
leaves the server generating a reply nobody will hear (INFERRED; llama.cpp
disconnect behaviour unmeasured). With brain_harness it leaves a child
process mid-turn with tools. Today there is no way for the daemon to TELL the
brain to stop, and D-18's history says the gap will read as working until the
room proves otherwise.

---

## 3. The proposed seams

The organising rule, stated once so every signature below follows from it:

> Universal per-call facts cross the PORT. Implementation-specific
> configuration is injected at the adapter's CONSTRUCTOR and never appears in
> ports.py.

Voice is universal (every TTS engine has voices). `model` and `language` are
universal to ASR. A Vulkan backend is not universal; a max_tokens body field
is not universal (a claude CLI turn has no such parameter); neither crosses a
port.

### 3.1 ports.py -- Asr changes

```python
@dataclass(frozen=True)
class AsrConfig:
    """Per-session facts every recognizer understands.

    Engine-specific options -- transcribe.cpp's backend and chunking
    presets, a cloud engine's endpoint region -- are CONSTRUCTOR kwargs on
    the adapter, named in config/models.json and validated by the adapter
    at boot (fail fast on a typo). They were fields here once, and the
    shared contract file ended up explaining one engine's 560 ms chunking.
    """
    model: str
    language: str | None = None    # None = model default; streaming rejects "auto"
```

`backend` and `latency` move to `asr_transcribecpp`'s constructor. The
settings rows `asr_backend` / `asr_latency` survive unchanged (restart-flagged,
as now); `butlerd.build_hub` passes them to `ports.build(...)` as kwargs
instead of packing them into AsrConfig. The `Asr` protocol itself
(open/warm/info) is unchanged.

### 3.2 ports.py -- Tts changes

```python
@runtime_checkable
class Tts(Protocol):
    def synthesize(self, text: str, voice: str,
                   handle=None) -> AsyncIterator[bytes]:
        """... existing docstring unchanged, plus:

        voice="" means the engine's own default voice, resolved by the
        adapter. Same rule as a blank router_model meaning the
        connection's own default (plan.md 4.4a): Butler ships no default
        voice name, because a shipped name is wrong on the first engine
        that does not have it.
        """
        ...

    async def abort(self, handle) -> None: ...      # unchanged
    async def warm(self) -> None: ...               # unchanged

    @property
    def info(self) -> dict:
        """At minimum {"engine": str, "sample_rate": int,
        "voices": [{"id": str, "language": str}, ...]}. Same warning as
        Asr.info: read during the hello handshake, must never trigger a
        load on the event loop -- warm at boot instead."""
        ...
```

`voices` entries carry a LANGUAGE and stopped being plain strings on
2026-09-10 (AC-155, `docs/language.md` D3), and that was FORCED rather than
chosen: the daemon has to offer a voice for the one `language` the owner
picked, and with a flat list the only way to know which voice speaks what is
to read an `af_`/`pf_` prefix off the id -- which puts one engine's naming
convention in the core, exactly what the AC-41 grep gate in
`test/test_seams.py` fails the build for. So the engine states each voice's
language and the daemon reads it, the same way `Asr.info["languages"]` has
always worked. `ports.voice_ids(info, language="")` is the ONE reader of the
shape; call it rather than reaching into the dicts.

Consequences, outside ports.py:

- `settings.py`: `voice` becomes `kind="text"`, `choices=()`, default `""`,
  `choices_from="tts_voices"`. The schema stops naming any voice; the picker
  fills from `GET /api/models?target=tts_voices`, which serves
  `hub.tts.info["voices"]` through the existing TARGETS mechanism
  (butlerd.py:46-49). The existing two-failures rule applies unchanged.
- `hub.py`: `_voice()` returns `self.settings.get("voice", "")` and the
  `"af_heart"` literals leave the daemon core. `""` crosses the port; the
  adapter resolves it (`tts_kokoro` maps it to its own DEFAULT_VOICE, which
  is already adapter-owned at tts_kokoro.py:71).
- Conformance: the shared `VOICE` constant moves into the per-adapter
  `ADAPTERS` params, so each adapter is tested with a voice it can speak and
  a second engine stops being un-addable. The two-voices-differ test remains
  engine-specific (`test_tts_kokoro.py`), per test/AGENTS.md's split.

### 3.3 ports.py -- Brain changes

```python
@runtime_checkable
class Brain(Protocol):
    def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]:
        ...                                          # unchanged

    async def abort(self, turn_id: int) -> None:
        """Stop working on `turn_id`. Butler's barge-in path calls this
        BEFORE cancelling its own reader task, for the same reason
        Tts.abort exists: cancelling the consumer is not cancelling the
        producer (D-4). A brain holding an HTTP stream drops it; a brain
        holding a harness child interrupts the turn; the fake records the
        call. An unknown or already-finished turn_id is a no-op, never an
        error -- barge-in must not care whether it raced completion.
        Asserted on the CALLER as well as here (D-18)."""
        ...

    async def warm(self) -> None:
        """Whatever makes the first real turn not pay a cold start
        (D-10): a router health check, a one-token completion, nothing at
        all for the fake. Called from butlerd.warmup alongside asr and
        tts. NOT the keep-warm loop: keep-warm (plan.md 4.4a) is daemon
        policy about a shared GPU and stays a hub-side interval task with
        its own settings; this runs once at boot."""
        ...

    @property
    def info(self) -> dict:
        """At minimum {"engine": str}. What the UI shows for the brain,
        next to stt and tts in the hello payload."""
        ...
```

`TurnContext` is unchanged. The token cap does NOT cross this port -- see
section 4 for where it crosses instead, and section 7 for why not here.

### 3.4 The connection seam for brain adapters (with 4.4b)

New module `daemon/connections.py` (this is plan step 7.1, restated with the
injection seam pinned so adapters cannot grow a global-store habit):

```python
@dataclass(frozen=True)
class ResolvedTarget:
    """Everything ONE completion call needs, resolved at call time.

    The key VALUE exists only in this frozen in-memory instance: read from
    the pointer (pi registry or environment) at resolve time, used for one
    request, never written, never serialized (AC-34, AC-37). A missing key
    raises here, naming the connection and the variable, not as a 401 from
    somewhere further away (plan.md 4.4b).
    """
    base_url: str
    model: str                 # "" = the connection's own default
    api_key: str | None
    max_tokens: int | None     # None = send no cap (section 4)


class TargetResolver(Protocol):
    def resolve(self, role: str) -> ResolvedTarget:
        """role is "router" or "reply". Unknown role raises KeyError."""
        ...
```

Butler's own brain adapters (`brain_endpoint`, later the router inside
`dialogue.py`) take a `TargetResolver` at CONSTRUCTION and call
`resolve(role)` per turn. butlerd builds the one resolver from Settings plus
the ConnectionStore.

The shape generalises, and it had to on 2026-08-18. A brain that drives a
coding agent needs preferences no CONNECTION can serve -- `harness_model`
names a model in the harness's own registry and `harness_cwd` is not a model
at all -- and the first answer was to hand `brain_harness` the settings store
itself. That is the habit this section exists to prevent, so it is now a
SECOND resolver of the same shape: `butlerd.harness_spec_resolver(settings,
chats)` returns `spec_for(cwd)`, called per MESSAGE, answering with the
`HarnessSpec` the daemon would spawn. The adapter supplies only THIS
SESSION's project, which is its own decision (AC-31). [AMENDED 2026-08-18,
twice on one day: this read `spec_for(tools)` and said the adapter supplied
the tool set, which is how the spoken grant widened a child -- AC-29 was
retired and there is no tool set. Its second argument was a `session_dir`
and is now the flat chat store (AC-35, rewritten), and a SECOND callable
joined it, `butlerd.harness_factory` -> `harness_for()`, because a child now
lives for one message and the adapter is built per message too (AC-46).]
One callable per question, never a store: a store can be read for
anything. This preserves three existing rules at once: settings
outside the engines group are read per turn (4.6a), adapters never import
settings or connections modules' state (they receive a callable), and no key
is held or written (AC-37). This contract lives in connections.py, not
ports.py -- it is vocabulary for Butler's own brains, and an external agent
framework behind the Brain port never needs to see it.

### 3.5 The harness contract -- PROMOTED to a fifth port

[DECIDED by the owner 2026-08-17. This section originally proposed a sub-seam
below the Brain port and offered promotion as the owner's call; the owner took
it. plan.md 4.10 and daemon/AGENTS.md now say five, with the argument recorded
in 4.10. What follows is the sub-seam reasoning, kept because it is the case
that was weighed and lost, not deleted as if it never existed.]

The rejected argument: the four ports are the seams the HUB consumes, and a
harness is consumed by two things that are not the hub -- the `brain_harness`
adapter (talk mode, 4.4c) and the Worker Manager (dispatch mode, 4.5). The
codebase already has an idiom for "swappable by name, conformance-tested, and
not one of the four": the endpointer (`ENDPOINTERS` at butlerd.py:42,
explicitly "NOT a fifth port").

The argument that won: a harness is a swappable ENGINE with six installed
candidates and only two ever observed finishing a turn (4.4), "switch harnesses
without breaking" is a stated requirement, and having two consumers is a reason
FOR a formal contract rather than against one. A seam that exists in fact but
not in `ports.py` is how `Action.harness: str | None` happened.

So `Harness` lives in `ports.py` beside the other four, with the same registry
and conformance treatment. Everything below is unchanged except its address:

- The `Harness` Protocol transposes an earlier prototype's agent-backend SHAPE (plan.md 4.4c) -- `prompt`, `subscribe` (an
  async iterator of normalized events: text_delta, tool_start, tool_end,
  turn_end, agent_end, error), `abort`, `dispose`, and a frozen
  `HarnessCaps` record carrying `native_resume: bool` so resume is a
  capability test, never a name test.
- A registry `HARNESSES: dict[str, type]` in ports.py beside SOURCES/ASRS/
  TTSS/BRAINS, using the same `register`/`build` helpers.
- A fake harness plus `test/conformance/test_harness_conformance.py`, pinned
  BEFORE the first real implementation, exactly as the Asr suite was
  ("the contract is pinned before the second implementation shows up").
- Exactly-one-terminal-event-per-prompt is asserted in conformance AND on the
  callers (D-18), because pi's backend shipped without it and held a turn
  lock forever (4.4c).

Two consequences of promotion that the sub-seam version did not carry. AC-7a
("the ports are real, not decorative") now covers five ports, so `Harness` owes
a real adapter and a fake against one conformance suite like every other port --
`claude` and `pi` are the two candidates measured completing turns here (4.4),
and the other four installed CLIs are named verification tasks, not assumptions.
And `ports.py`'s own module docstring, which opens "The four seams", is now
wrong; it is updated in the same commit as the code, never later (D-12).

### 3.6 What stays exactly as it is

Source (shape is right; the adapter file is Phase 5 work already planned).
The Intent closed set. TurnContext and its history invariant (D-20). The
registry-as-dict. The endpointer. Modes vs settings. The satellite protocol.

---

## 4. The token cap

### 4.1 Two settings, not one

A router verdict and a conversation reply are different animals:

- The verdict is schema-constrained JSON, target size well under 100 tokens
  [READ plan.md 4.4 verdict schema; no direct token count measured]. Its cap
  exists to bound a runaway or reasoning model. Its failure mode is a LOST
  ROUTING, which the daemon can recover from invisibly (3.8).
- The reply is open prose, hundreds to thousands of tokens. Its cap exists to
  bound spend and runaway generation. Its failure mode is TRUNCATED SPEECH,
  which the owner hears.

One knob sized for the reply does nothing useful for the router; one sized
for the router silences every substantive answer. The measured Ornith result
-- a 200-token cap fully consumed by the thinking block, 10 of 10 verdicts
empty [MEASURED 2026-08-17, plan.md 4.4] -- shows the router cap must be
sized including a reasoning budget, which is a different number from any
sane reply cap. Two settings.

A cap is also NOT a latency tool: the router's latency bound is the AC-4 time
budget and a request timeout, and capping tokens to make it faster is how the
empty-verdict failure was manufactured in the first place.

### 4.2 The settings rows

In `daemon/settings.py`'s `_s(...)` style, group `brain`, landing ONLY in the
same commit as the code that reads them (settings.py's own rule: a control
that saves and changes nothing is the AC-7b failure):

```python
_s("router_max_tokens", 0, "brain", "Router token cap", kind="number",
   minimum=0, maximum=32768,
   help="Most tokens the router may generate for one verdict. 0 sends no "
        "cap. A reasoning model can spend an entire small cap thinking "
        "and return an empty verdict; that turn then goes to the "
        "conversation model and the log says why."),
_s("reply_max_tokens", 0, "brain", "Reply token cap", kind="number",
   minimum=0, maximum=32768,
   help="Most tokens one spoken reply may generate. 0 sends no cap. This "
        "bounds what the model may produce, not what is read aloud -- "
        "long answers go to the screen either way."),
```

Both default 0 = "send no max_tokens field at all", for the same reason
Butler ships no default model (4.4): a shipped number is wrong on the first
model that reasons, and it silently outranks the owner's judgment. Both are
per-turn settings (no restart flag) and persist in `state/settings.json` like
every other preference [DECIDED here; the 32768 ceiling is an arbitrary
sanity bound, [DECIDED], not measured].

No warning on save, no probe, no annotation in the picker -- the owner's
2026-08-17 decision (4.4b) stands. The cap's consequences surface at RUNTIME
in the log and on the transcript, which is honesty about what happened, not
advice about what to choose.

### 4.3 Failure behaviour

**Capped verdict, empty or truncated (finish_reason=length).** This is the
plan.md 3.8 carve-out, not a new case. From the daemon's side, "the model
returned no usable verdict" already has a defined answer: route to
`converse`, log loudly with the raw output. Empty-because-capped joins that
path with ONE addition: the log line carries `finish_reason` and the
configured cap (`router verdict unusable: finish_reason=length, cap=200,
content empty`), so the owner can tell "capped" from "garbage" without Butler
editorializing. Justification for folding rather than forking: the measured
failure (empty content, 10/10 [MEASURED 2026-08-17]) is indistinguishable
from a parse failure in effect -- the turn must not be lost -- and 3.8's
reasoning ("the verdict is MODEL output, not our input; losing the user's
turn is the worse failure") applies verbatim.

**Capped reply with partial content.** Complete sentences that arrived are
spoken (the splitter already works sentence-wise); a trailing fragment cut
mid-sentence is NOT spoken -- a half-sentence read aloud sounds like a broken
Butler, and history records only what was spoken anyway (D-20). The full
text, fragment included, goes to the web UI with the reply marked truncated,
and the log names finish_reason and the cap. No audible error: a cap doing
its job is the owner's configured bound, not an engine failure -- the
audible-error rule (3.8, dead TTS segment) is for failures Butler did not
choose.

**Capped reply with EMPTY content** (the whole cap spent thinking). Silence
is indistinguishable from Butler ignoring the user (the principle
tts_kokoro.py states for SidecarError), so this speaks a fixed line -- "I ran
out of room to answer." -- plus the loud log line, and the turn re-arms
through the normal terminal path (D-1). The line names no model and offers no
advice; it reports what happened.

### 4.4 Where the value crosses each seam

`state/settings.json` -> `Settings` (read per turn) -> the `TargetResolver`
butlerd builds -> `ResolvedTarget.max_tokens` -> the POST body of one
completion call inside Butler's brain adapter. It crosses NO port signature:
`Brain.turn(text, ctx)` is unchanged, and Asr, Tts and Source never see it.
The proof it does not belong in the port: a harness brain has no per-turn
max_tokens to give, so a port field would be a parameter some implementations
must silently ignore -- the exact shape of leak section 3's rule forbids.

### 4.5 Relation to AC-32 (a long answer is not read at you)

Kept distinct on purpose. The cap bounds what the model may GENERATE (spend,
runaway risk); AC-32 bounds what is READ ALOUD (attention). A 2000-token
reply under a 4096 cap still goes to the screen with a spoken pointer; a
150-token capped reply may still be read in full. The cap crosses the
resolver into the request; AC-32 lives in the speak path. One mechanism
cannot serve both because they trigger on different quantities (tokens
generated vs sentences to speak) and fail differently (truncation vs
verbosity).

---

## 5. Acceptance criteria (AC-38 onward; AC-37 is the highest in plan.md)

- **AC-38 (the token caps are the owner's, persistent, and honoured on the
  wire).** Each cap set on the settings page survives a daemon restart. With
  a cap N set, the next completion request body carries `max_tokens: N` --
  asserted against a fake OpenAI endpoint's RECEIVED body, not against the
  setting being stored. With 0, the body carries no max_tokens field at all.
  A change takes effect on the next utterance without a restart.
- **AC-39 (an empty capped verdict loses the routing, never the turn).** With
  the router capped low against a model that returns empty content, the turn
  is answered by the conversation path (3.8) and the log line names
  finish_reason and the cap value. Behaviour is identical to an unparseable
  verdict; only the log distinguishes them.
- **AC-40 (a capped reply is visible, never silent, never half-spoken).**
  finish_reason=length with partial content: complete sentences are spoken, a
  trailing fragment is not, the wire `reply` carries the full text and a
  truncated marker. Empty content: the fixed spoken line is emitted and the
  log says why. Both paths re-arm (D-1).
- **AC-41 (no engine vocabulary outside its adapter).** `daemon/hub.py` and
  `daemon/ports.py` contain no engine-specific voice name, backend name, or
  latency preset -- enforced by a grep-gate test in the AC-8 style, and by
  the voice list being served live from `Tts.info` rather than from the
  schema.
- **AC-42 (every engine port warms and identifies itself).** `warm()` and
  `info` exist and are conformance-tested on Asr, Tts and Brain; boot warmup
  covers all three; the hello payload carries all three infos and the page
  shows them.
- **AC-43 (barge-in mid-thought reaches the brain, and names the turn).**
  During a Brain turn, a wake elsewhere causes `brain.abort(turn)` to be
  called with the abandoned turn id BEFORE the reader task is cancelled --
  asserted on the CALLER against the fake brain's recorded aborts (D-18), and
  mutation-checked by deleting the call site (D-13). Aborting an unknown turn
  is a no-op; aborting turn N does not disturb turn N+1.
- **AC-44 (a harness is an adapter behind one contract).** `brain_harness`
  and the worker manager consume only the `Harness` protocol; resume is
  decided by `caps.native_resume`, never by harness name; adding a harness is
  a new adapter file, a registry line, and a conformance row -- no call-site
  edits, demonstrated by the fake and the first real harness passing the same
  suite.

### Gherkin for the load-bearing ones

```gherkin
Scenario: Cap crosses the wire, and zero means absent (AC-38)
  Given reply_max_tokens is 512
  When the conversation model is called
  Then the request body contains "max_tokens": 512
  Given reply_max_tokens is 0
  When the conversation model is called
  Then the request body contains no max_tokens key

Scenario: Reasoning model spends the whole verdict cap thinking (AC-39)
  Given router_max_tokens is 200
  And the router model returns finish_reason "length" and empty content
  When the owner asks what time it is
  Then the turn is answered by the conversation path
  And the log contains finish_reason=length and cap=200
  And nothing about the model choice is shown as a warning in the UI

Scenario: Reply truncated mid-sentence (AC-40)
  Given reply_max_tokens is small enough to cut the third sentence short
  When a three-sentence answer is generated
  Then sentences one and two are spoken and the fragment is not
  And the reply message on the wire carries the full text marked truncated
  And the session re-arms

Scenario: Reply capped to nothing (AC-40)
  Given the model spends the entire reply cap in its thinking block
  When the turn completes with empty content
  Then Butler speaks the fixed out-of-room line
  And the log names finish_reason and the cap
  And the session re-arms

Scenario: Barge-in during a slow brain turn (AC-43)
  Given the brain is mid-generation for turn N
  When a wake candidate arrives from any satellite
  Then brain.abort(N) is called before the reader task is cancelled
  And no intent from turn N is spoken afterwards (D-6)

Scenario: Aborting a finished turn is inert (AC-43)
  Given turn N completed a moment ago
  When brain.abort(N) is called
  Then nothing raises and turn N+1 is unaffected
```

---

## 6. Phased refactor plan

Suite baseline: 194 passed, 0 xfailed; fast tier 170 in about 41 s
[MEASURED 2026-08-17, plan.md section 5]. Every phase lands green before the
next starts. No existing test is expected to need weakening; if
implementation finds one that must change, the change is an
`xfail(strict=True)` naming the phase that deletes it, per test/AGENTS.md.
Phases 1-4 are port-surface work and deliberately land BEFORE plan step 7's
brain code, so the seams exist before the first thing that would have leaked
through their absence.

**Phase 1 -- uniform engine surface (AC-42). DONE 2026-08-17.** Landed as
specified, 206 passed against a 194 baseline, no test weakened and no xfail
added. Three things the plan did not anticipate, recorded because they cost
time and will recur:

- `KokoroTts.info` already existed and reported singular `voice`; the adapter
  was ahead of the port. The port was the thing missing, not the adapter.
- The outbound handshake frame is `ready`, not `hello` -- `hello` is inbound.
  Nothing validates its key set on either side, so adding keys broke nothing.
- `probe_page.py` could not verify any of this. `connect()` runs at the END of
  `start()`, after getUserMedia, so with no microphone the socket never opens
  and every daemon-sent value still reads as its markup placeholder. A `--live`
  flag with a fake device now exists; without it the page looks identical
  whether it works or is entirely broken. Measured after the fix: `stt =
  'fake / none'`, `tts = 'fake'`, `brain = 'fake'` in real Chrome.

A code review after the first commit found two things, both real, and they are
now D-25 in plan.md section 6. The safety half of AC-42 -- that `info` never
blocks -- was FALSE for `asr_transcribecpp`, which opened a session to fill its
cache: 744 ms warm, 15.85 s cold, on the event loop, on every connect, whenever
a warm had failed. And the test written to guard that property was broken in
both available ways at once: it compared `info == info` (which an adapter
reloading on every call passes) and it built its own fake while accepting the
parametrized fixture (so the real adapter never ran). Both fixed: the property
is now timed against every adapter in all three suites, and the adapter reports
`unknown` instead of finding out. Suite went 206 -> 208, and the full run got
FASTER, 173.0 s to 123.5 s, because a model load left the handshake path.

Original plan, kept for the record: Add `info` to Tts and
`warm`/`info` to Brain in ports.py; implement in both fakes and tts_kokoro;
extend `butlerd.warmup` to the brain; extend hello with tts and brain info.
Test first: conformance `test_info_reports_what_the_ui_needs` cloned into the
Tts suite and a new (fake-only, like early Asr) Brain conformance file --
each fails with AttributeError on the un-extended fake, the right reason.
Must keep passing: everything; protocol tests do not read info today. Risk:
`runtime_checkable` isinstance tests fail until fakes are extended in the
SAME commit as the protocol; and Tts.info on kokoro must not lazily boot the
sidecar on the event loop (the Asr.info warning applies -- serve from the
adapter's static voice table, verified against the engine in the kokoro-only
test file).

**Phase 2 -- Brain.abort, asserted on the caller (AC-43). DONE 2026-08-17.**
Landed as specified. 216 passed against 208. Four mutations, each caught by
exactly the test named for it: restricting the call to SPEAKING kills the
THINKING test, moving it after cancellation kills only the ordering test,
dropping the try/except kills only the refusing-brain test, deleting it kills
all three abort tests.

One process note worth keeping, because it nearly produced a false result. The
first mutation run reported all three mutations SURVIVING. They had not -- the
harness parsed pytest output with `line.startswith("FAILED")`, and pytest
colours that word, so every FAILED line begins with an ANSI escape and matched
nothing. D-13 says validate the instrument; the instrument here was the
mutation harness itself, and "no test failed" is exactly the reading that would
have been accepted as good news.

A review after the commit found two tests that proved less than they read.
Both fixed, both now mutation-checked. `test_aborting_one_turn_does_not_disturb`
ran the abort and the second turn IN SEQUENCE, so at the moment abort fired
there was no other turn alive to damage -- a session-keyed abort passed it
unchanged. It now leaves turn 2 suspended mid-iteration while turn 1 is
aborted. And the idle-session test could never exercise the state guard at all:
with no turn claimed, `abandoned_turn is not None` short-circuits first, so the
test passed just as happily with the guard deleted. A CAPTURING case now covers
it -- a turn claimed, the brain never asked.

OPEN, carried forward rather than fixed blind: `abandon()` wraps each abort in
`except Exception`, which does not catch `CancelledError`. If the enclosing task
were cancelled while suspended inside either abort, cleanup below would be
skipped -- tasks left running, state never back to IDLE. That is D-17/D-1
shaped. It predates this phase (the tts abort had the same exposure) and
reachability under aiohttp's WebSocket-handler cancellation was NOT
established, so nothing was changed on a guess. It needs a deliberate look with
a test that can actually reach it.

Original plan, kept for the record: Port method, fake
records aborts, `hub.abandon()` calls `brain.abort(abandoned_turn)` when the
abandoned state is THINKING or SPEAKING, before task cancellation, wrapped in
the same try/except as the tts abort (an abort failure must not break
barge-in, D-1). Test first: a `test_hub_bargein.py` case asserting the fake
brain's recorded aborts name the turn -- fails because the method does not
exist. Then mutation-check by removing the hub call and watching THAT test
fail (D-18, D-13). Must keep passing: existing barge-in tests (they assert
tts abort and task teardown; the new call precedes them). Risk: the abort is
fake-proven only until brain_endpoint lands in step 7.5 -- named plainly, see
section 6's closing risk note.

**Phase 3 -- voice discovery (AC-41 first half). DONE 2026-08-17.** 223 passed
against 217. Two things the plan did not foresee, both found before they shipped:

- The favourites star is generic across EVERY `choices_from` control and writes
  one shared `model_favourites` list, which `paintFavs` paints under all of
  them. Making `voice` discovered would have put model ids under the voice
  picker as clickable buttons, and clicking one commits a model id as a voice.
  Fixed by construction: `favourites` is now an opt-in flag on the setting,
  true only for the two model pickers.
- `hub._announced_voice()` reads `tts.info`, so four Tts doubles in two test
  files broke at once with the same one-line gap. They now share `StubTts` in
  conftest.

Measured in real Chrome with `probe_page.py --live`: `voice` renders
star=False with 2 options from the fake engine; `router_model` and
`endpoint_model` render star=True with 130 from the real llama-swap.

Original plan: `Tts.info["voices"]` from
Phase 1 becomes the source: settings `voice` goes text-kind, empty-default,
`choices_from="tts_voices"`; the TARGETS table gains the `tts_voices` entry;
hub loses its `"af_heart"` literals; kokoro maps `""` to its DEFAULT_VOICE;
conformance's shared VOICE constant moves into the per-adapter params. Test
first: the AC-41 grep-gate over hub.py fails while the literal exists. Must
keep passing: `test_settings_api.py` voice-reaches-engine test (it sets an
explicit voice; unaffected), all Tts conformance (voice now per-adapter
param). Could break: any test relying on the schema OFFERING Kokoro choices,
and `web/settings.js` rendering of a text+choices_from control -- verify with
`probe_page.py` against a scratch daemon (D-22), since reading markup is not
evidence.

**One gap this phase must close, missed by the first draft of this document
and found in review.** `hub.py:588` sends `voice=self._voice()` to the
satellite in `speak_start`, so once `_voice()` returns `""` the page receives
an empty voice string and displays a blank where the voice belongs. The empty
string is the adapter's business, not the satellite's: resolve it before the
send (`hub` asks `tts.info` for the resolved name) rather than shipping `""`
onto the wire. This is small and it is exactly the shape of AC-7b -- the wire
saying one thing while the room hears another -- which has already shipped
here once, so the phase is not done until a protocol test asserts
`speak_start.voice` is a non-empty name the engine actually spoke with.

**Phase 4 -- AsrConfig slims (AC-41 second half). DONE 2026-08-17.** 224
passed including the needs_gpu tier, which this phase required rather than the
fast tier alone. `AsrConfig` is now `model` and `language`; backend and
latency are adapter constructor kwargs with the engine's own defaults, routed
from a new `asr_engine` block in config.

The plan under-specified one thing. It said the conformance suites would keep
passing because they build `AsrConfig(model="test")` only -- true, and that is
exactly why they prove nothing here: conformance builds the adapter with
DEFAULTS, so an adapter that dropped its constructor kwargs entirely would
stay green, the engine falling back to its own defaults which are the same
values. Added `test/test_asr_transcribecpp.py`, which builds with
`backend="cpu"` and asserts the engine reports CPU rather than this machine's
default Vulkan0. Mutation-checked: hardcoding `backend="auto"` in the adapter
fails that test alone, every conformance case green.

Original plan: `backend` and `latency`
leave AsrConfig for asr_transcribecpp constructor kwargs; butlerd wiring
follows; the settings rows are untouched. Test first: extend the AC-41
grep-gate to ports.py for `vulkan|balanced` vocabulary -- fails on
ports.py:69-70. Must keep passing: both Asr conformance suites (they build
`AsrConfig(model="test")` only [READ]), every pipeline test (same). Could
break: `asr_transcribecpp`'s engine kwargs plumbing -- run the needs_gpu tier
before calling the phase done, not just the fast tier.

**Phase 5 -- connections and the resolver. PARTLY DONE 2026-08-17.** 237
passed against 231. What landed: `daemon/connections.py` (Connection,
ResolvedTarget, ConnectionStore), boot-and-Reload import of pi's providers,
reference-only auth resolution, `GET/POST /api/connections`,
`POST /api/connections/reload`, and `GET /api/models?connection=`. Both
mandated negative tests were written FIRST and are mutation-checked: echoing
pi's provider objects from the connections route fails the no-key-in-response
test; persisting a resolved value beside its pointer fails the nothing-under-
state test.

A security review of the first commit found the hole that mattered, and it
was functional rather than confidential: `fetch_models` took no credential,
so the resolved key was computed and DISCARDED. Every authenticated provider
-- which is all three cloud ones, the entire reason connections exist --
would have come back 502 "could not be read", and the module docstring saying
the key is "used for one request" was false for the only call site there was.
`fetch_models` and `Catalogue.models` now carry it. The catalogue is still
keyed on base_url ALONE, deliberately: putting the key in the cache key would
make a process-lifetime object hold a secret, and not retaining a secret is
worth more than a catalogue being exactly right for two connections sharing
one base URL, which pi's per-provider URLs do not produce.

The same review found `auth.get("from")` crashing with AttributeError on a
non-object auth -- a 500 on a route reachable from the tailnet, where every
other handler answers a named 400. Validated in the handler AND in
`Connection.__post_init__`, because `state/connections.json` is a file the
owner can edit and one malformed entry took the WHOLE list down: `public()`
runs over every connection.

Two enabling changes fell out and are worth their own note. `BUTLER_STATE`
and `BUTLER_PI_REGISTRY` are now environment overrides, because the suite
otherwise shares the owner's live state directory and their real registry --
and a connections test writes a file carrying a key pointer and reads a
registry holding three real cloud keys. Isolation is the point of those
tests, not a convenience.

The settings migration landed too. `router_model` and `endpoint_model` are
`{conn, model}` pairs (AC-30, MET), `endpoint_url` and its `target=endpoint`
are retired, and an older on-disk string migrates to the model half with no
connection. The page renders a connection selector beside each model picker,
and a real browser drove it end to end: choosing the pi-imported llama-swap
connection filled 130 models and the pair persisted as
`{conn: "llama-swap", model: "qwen3-4b"}`.

That browser run found a defect no test had: pi stores every baseUrl WITH the
version (`.../v1`, OpenAI's own convention) while `fetch_models` appended
`/v1/models` unconditionally, so every imported connection asked for
`/v1/v1/models` and got a 404. The feature was broken for its main source and
every test built its base URL the one way that worked. Normalised, with a test
covering both conventions.

PHASE 5 COMPLETE 2026-08-17. The Add-connection panel is built: static markup
driven by `buildConnections()`, the same split the permission-mode block uses.
Three fields -- label, base URL, and the NAME of an environment variable --
plus a list showing where each connection's key comes from and never what it
is, a remove for the owner's own entries, and Reload from pi. A DELETE route
came with it, because a panel that adds and never removes leaves hand-editing
a file the owner was never told about as the only way out.

The browser found the integration seam the daemon tests could not see: each
pair control populates its connection selector once at build, and `refresh()`
does not rebuild rows when only values changed, so a newly added connection
was absent from the router's dropdown until a page reload. Both promise-memos
are now invalidated and every registered selector repopulates -- measured:
dropdown goes `[llama-swap, unreachable]` to `[my-box, llama-swap,
unreachable]` on add and back on remove.

A screenshot caught the other one. Every text input on this page is styled by
a descendant selector on its container, so the four new bare inputs rendered
as white boxes on a dark panel -- correct in structure, wrong on sight.

Original plan:
`daemon/connections.py` with ConnectionStore, ResolvedTarget, TargetResolver;
the two mandated negative tests FIRST (no apiKey in any response; nothing
under state/ holds a key value after a save -- both against the fixture
registry, never the owner's real one, test/AGENTS.md). ResolvedTarget carries
`max_tokens` from day one, returning None until Phase 6 wires the settings --
a field is not a dead control, so the AC-7b rule is not violated. Must keep
passing: `test_models_api.py` (the catalogue and two-failures rule survive
per plan step 6). Could break: the `/api/models` target scheme changes from
`target=` to `connection=` (AC-30) -- this phase owns that migration and its
tests.

**Phase 6 -- the token caps (AC-38, AC-39, AC-40), riding plan steps 7.4 and
7.5. DONE 2026-08-17.** Landed with the router and endpoint mode in one
commit, because the caps have to arrive with the code that reads them and
both halves share one resolver and one HTTP client. The fake OpenAI server
this phase needed did not exist and is now `conftest.FakeOpenAI`: it records
every body it was sent, which is what makes "the cap is on the wire" a
different assertion from "the cap is in the file".

Five things the plan did not anticipate, each recorded because it cost time
or changed a decision:

- **AC-40 needed two port fields, not zero.** The spec described the
  behaviour and not its shape. `Say.spoken` and `Done.truncated` are the
  smallest honest carriers: a fragment that is shown and not said has no
  other way through a closed intent set, and `Done` is the one frame that
  exists exactly once per turn. `Say.spoken` is also the shape AC-32 will
  want, which is what settled flag-versus-new-intent.
- **The router had to be a composite, not an adapter.** `Dialogue` holds the
  router and delegates to one substantive brain, so harness mode replaces the
  back half. Putting the router inside `brain_endpoint` would have meant a
  second copy of the route-to-intent mapping the day `brain_harness` landed
  -- 4.4c's own warning, one level up.
- **R9's negative test cannot be written against a live model cheaply.** The
  version that ships walks the built schema and fails on `not`, `if/then`,
  `oneOf`, `anyOf` -- the constructs llama.cpp drops silently. That is
  checkable in milliseconds, fails for the right reason, and encodes the
  actual rule: Butler depends only on what is enforced.
- **`Router.warm` was checking nothing.** It sent a real verdict, as
  docs/reflex-serving.md section 5 asks, and ignored the answer -- found by a
  live probe where the daemon warmed "successfully" with the router capped at
  16 tokens while every verdict it could produce was unusable. It now raises
  `VerdictUnusable`, which butlerd logs loudly and boots past (D-1).
- **`api_url` was duplicated.** Both completion calls needed the base-URL
  rule that `models.py` already had, and the copy in `models.py` is the one
  that shipped `/v1/v1/models`. It now lives in `connections.py`.

Proved against the real model as well as the fake, [MEASURED 2026-08-17]
through `test/manual/probe_brain.py` and a real browser: a verdict capped at
16 comes back `finish_reason=length` cut mid-string and the turn is answered
by the conversation model with the log naming both (AC-39); a reply capped at
12 speaks one whole sentence and renders "You'll need to" dimmed and
unspoken, with `speak_end truncated=true` (AC-40).

**Phase 7 -- the harness contract (AC-44). DONE 2026-08-18.** The `Harness`
port in `ports.py` (fifth port, 3.5), `HARNESSES` in the same registry block,
the fake harness and the conformance suite landed before any real harness
code, exactly as the Asr suite predated its second implementation, and the
ordering held: nothing consumed the module until `brain_harness`, so "could
break: nothing" was literally true for that commit.

The phase then ran well past this document's brief, to a usable harness mode.
`docs/harness-mode.md` is the spec and the build order for all of it -- seven
steps, twelve commits, the pi adapter, the child-lifetime job object, the
spoken write grant, the session store and the spoken project switch -- and
its section 9 records what that plan got wrong, while its section 10 is the
owner's simplification ruling that deleted the grant on the same day
(AC-29, retired). Its criteria (AC-46 to AC-49)
now live in `plan.md` section 2 beside AC-44, which is honestly PARTLY met:
two adapters pass one suite and no call site tests a name, but
`caps.native_resume` picks a display string and nothing else until a second
real harness exists. The exactly-one-terminal-event rule got both levels as
promised -- a conformance assertion and a caller-side one (D-18).

**The biggest risk in this plan, named, and how it turned out:** Phases 1-4
and 7 built seams ahead of their second real consumer, so their proofs were
fake-plus-conformance -- precisely the green-but-unproven condition D-18 and
D-13 describe. The mitigations were the caller-side assertions and mutation
checks, and the rule that a real engine's conformance ADAPTERS row is added
the day that engine lands (adapters/AGENTS.md). The risk was real and the
mitigations were not sufficient on their own: the harness conformance suite
HUNG rather than failing on the one defect it existed to catch, and it took a
review plus a deliberate mutation to find that out. The alternative --
designing the seams after brain_endpoint and brain_harness exist -- is still
rejected, because it is how `Action.harness: str | None` and the settings
voice list got where they are.

---

## 7. What is deliberately NOT proposed, and why

- **A shared EngineConfig base or a unified per-call params object across all
  three engine ports.** Considered, rejected as over-engineering: the three
  ports configure differently because their engines ARE different
  (session-scoped facts for Asr, one per-call fact for Tts, none that are
  universal for Brain). Forcing one shape is symmetry for its own sake --
  ports.py's own registry comment names that failure ("the abstraction tax
  has outgrown the benefit"). The organising rule in section 3 buys the
  consistency without the ceremony.
- **A `GenParams` field on TurnContext, or max_tokens in the Brain port.**
  Rejected in section 4.4: not universal across brain implementations, so it
  would be a port parameter some adapters silently ignore.
- **temperature / top_k settings.** No forcing function exists -- no defect,
  no owner request beyond the cap. `ResolvedTarget` is where they would go
  IF one arrives; adding them now is a guess wearing a schema row.
- **A fifth entry in ports.py for Harness -- PROPOSED AGAINST, then OVERRULED.**
  This document argued for a sub-seam below the Brain port, beside the
  endpointer idiom, and offered promotion as the owner's call. The owner
  promoted it on 2026-08-17 (3.5). Kept in this list rather than deleted
  because the rejected case is the one a future reader will want to weigh
  again if a sixth port is ever proposed.
- **Brain.close() / Tts.close() lifecycle methods.** Shutdown discipline is
  already owned adapter-side (atexit in tts_kokoro, the D-24 parent-watchdog
  rule for children, AC-36 for harness orphans). A port-level close would
  duplicate a contract that must hold even when close is never called --
  the daemon can die by TerminateProcess, which is exactly why D-24 exists.
- **Keep-warm as a Brain method.** It is policy about a shared GPU and a
  TTL'd server (4.4a), with settings, an interval, and refusal rules -- hub
  work, not an engine property. Brain.warm is boot-only.
- **A plugin loader, entry-point discovery, DI container, or event bus.**
  Already rejected by plan.md section 7 and ports.py; restated only because
  this document's brief demands it. Nothing here needs one: every proposed
  seam is a Protocol, a frozen dataclass, or a dict registry that already
  exists.
- **Fixing the settings schema's engines-group vocabulary** (parakeet model
  names, chunking presets at settings.py:195-208). A real leak, consciously
  deferred: those rows are restart-flagged construction inputs, the drift is
  visible in one file, and solving it properly means adapter-contributed
  schema fragments -- a bigger mechanism than the problem. Revisit when a
  second real Asr adapter actually lands; noted here so the deferral is a
  decision, not an oversight (D-12).
