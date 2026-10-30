# One language decision, and the ports that derive from it

A SPEC, not the contract. Its criteria (AC-152..AC-160) move into `plan.md`
only as the owner accepts them.

Written 2026-09-10, from the owner's ask: talk to Butler in Brazilian
Portuguese and be answered in Brazilian Portuguese. Every number below was
measured that day against this checkout, the installed `kokoro-js` and the
GGUF in `models/`, unless it carries a different tag.

The ask is a language. What the investigation found is an OWNERSHIP defect
that Portuguese happens to be the first thing to expose, so this spec fixes
the ownership and lets Portuguese fall out of it. That is the whole idea and
section 0 is the argument for it.

---

## 0. What this fixes, which is not what it looks like

Butler has no owner for the decision "what language is this conversation in".
Three places decide it independently and nothing reconciles them.

```
                      "what language is this?"
                                |
        +-----------------------+-----------------------+
        |                       |                       |
   asr_language              (nobody)                  voice
   (a setting)          the prompts never          (a setting, where
        |               mention language            the language hides
        |                       |                   in a name prefix)
        v                       v                       v
   Asr port                 brain prompts           Tts port
   language="pt-BR"           silent                voice="bm_george"
        |                       |                       |
   hears Portuguese        answers ...            READS PORTUGUESE
                                                  WITH ENGLISH SOUNDS
        \_______________________ _______________________/
                                v
                 NOTHING RECONCILES THESE THREE.
        `Settings.set` validates ONE KEY AT A TIME and there is
        no cross-setting check anywhere in `daemon/settings.py`.
```

`Settings.set` is `cleaned = self.validate(key, value)` and nothing else
(`daemon/settings.py:923`) [READ 2026-09-10]. So `asr_language = "pt-BR"`
beside `voice = "bm_george"` is a state the page can reach, the daemon will
save, and no test will notice. It is D-35.

> **CORRECTION, from building step 1.** The left-hand arrow in that diagram
> was worse than uncoordinated -- it did not exist. Nothing outside
> `settings.py` ever read `asr_language`, and the same is true of
> `asr_model`, `asr_backend` and `asr_latency`: the entire Recognition group
> was decoration. That is **D-36**, and 6.1 has the evidence. The argument
> below stands unchanged, because one owner for the decision is what fixes
> both defects; only the starting position was worse than drawn.

That is Meyer's Single Choice Principle in the shape this project already
has a number for: **D-18, a contract needs an assertion on the CALLER, not
just the callee.** Each port honours its own field correctly. The defect
lives in the space between them, which is exactly where this repository's
expensive defects have always lived.

So the fix is not "add a Portuguese voice". It is: ONE setting, THREE
derivations, and ONE gate that fails when they disagree.

---

## 1. What already exists (READ FROM CODE and MEASURED, 2026-09-10)

### 1.1 The recognizer is already multilingual and already on disk

`models/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf` is present. Loaded, and
asked what it can do [MEASURED 2026-09-10]:

```
capabilities.languages -> 32 locales, including 'pt-BR' and 'pt-PT'
stream(language="pt-BR")  ACCEPTED
stream(language="pt")     REJECTED -- transcribe_stream_begin:
                                     unsupported language (status 10)
stream(language="xx-XX")  REJECTED -- same
```

The locale string must be EXACT. `pt` is not a synonym for `pt-BR` here, and
the engine says so loudly rather than falling back, which is the behaviour
this project wants.

`daemon/engines/stt_engine.py:122` already publishes `"languages"` in
`Asr.info`. **The discovery half of this spec already exists on the ASR
side.** Everything below is bringing the other port up to that shape.

### 1.2 The speech engine can already say it, and refuses to

Three facts, and they point in different directions.

The weights are on disk. `node_modules/kokoro-js/voices/` ships 54 packs,
including `pf_dora.bin`, `pm_alex.bin` and `pm_santa.bin` -- Kokoro's
Brazilian Portuguese voices [READ 2026-09-10].

`kokoro-js` refuses to use them. Its voice table is 28 hardcoded entries, all
`en-us` or `en-gb`, and `_validate_voice` throws for anything outside it
[MEASURED 2026-09-10]:

```
tts.generate(text, { voice: 'pf_dora' })
  -> Error: Voice "pf_dora" not found. Should be one of: af_heart, af_alloy, ...
```

The bundled phonemizer has ENGLISH DATA ONLY. `kokoro-js` imports
`phonemizer`, an eSpeak NG WASM build, and [MEASURED 2026-09-10]:

```
phonemize('Bom dia, como vai voce?', 'pt-br')
  -> Invalid language identifier: "pt-br". Should be one of:
     en, en-029, en-gb, en-gb-scotland, ..., en-us, en-us-nyc
```

Forced through `en-us` it produces English-spelled nonsense -- "voce" comes
out rhyming with "poker", "codigo" with a hard English "oh". The IPA is not
reproduced here because this repository's documentation is ASCII, and the
measurement that carries the same finding in plain numbers is better anyway:
the same Portuguese sentence runs **6.90 s through the English phonemizer
against 4.47 s through the Portuguese one** [MEASURED 2026-09-10]. A 54%
duration inflation is the audible signature of spelling Portuguese words with
English sounds, and it is the thing to measure for a regression, because a
listener can hear it and a byte comparison cannot.

### 1.3 The bypass works, and it is measured

Kokoro's `generate_from_ids` is PUBLIC, and its voice loader reads
`../voices/<name>.bin` for any name -- neither goes through
`_validate_voice`. Driving it with a full-data eSpeak NG
(`espeak-ng` on npm, 9 MB, one dependency) [MEASURED 2026-09-10]:

```
espeak-ng -v pt-br --ipa=3 "Bom dia. Preciso arrumar o codigo do projeto."
  -> 56 IPA tokens of Brazilian Portuguese  (glyphs not reproduced: ASCII docs)

Kokoro tokenizer vocab = 115 tokens
phonemes in that line NOT in the vocab = [U+200D]  (espeak's tie bar; one strip)
  -- i.e. every real Portuguese phoneme espeak emits is already in Kokoro's
     vocabulary. The model was trained multilingual; only the JS wrapper is not.

generate_from_ids(ids, { voice: 'pf_dora' })
  -> 73,800 samples = 3.08 s at 24 kHz | peak 0.553 | rms 0.0670
```

Real audio, not silence, and the right duration. Longer samples were rendered
to `%TEMP%\butler-ptbr\` for the owner to judge by ear; intelligibility is
NOT claimed here and section 8 says so again.

**The precedent that makes this legitimate rather than a hack.**
`daemon/engines/tts/tts-model-loader.js` already performs this exact move and
documents it: kokoro's `from_pretrained` "forwards only { dtype, device,
progress_callback }", so that file builds the ONNX session itself and hands
it to "the stock KokoroTTS class". Using the same public-constructor bypass
one level up is the same technique, for the same reason, in the same file's
neighbourhood. It does not patch a dependency, so `npm install` cannot
silently revert it. A patch that `npm install` erases would be a patch and
not a fix.

### 1.4 The enforcement this spec plugs into already exists

`test/test_seams.py` is a working grep gate: it reads every voice id out of
`node_modules/kokoro-js/voices/*.bin` and FAILS if any of them appears in
`ports.py`, `hub.py`, `settings.py`, `pipeline.py` or `butlerd.py` (AC-41).
It also guards the guard -- an empty pool SKIPS rather than passing
vacuously, which is D-13 written as code.

This matters to the design more than it looks: **that gate is why the `Tts`
port has to change.** See D3.

### 1.5 What is unaffected

The wake word. `web/models/hey_jarvis_v0.1.onnx` matches an acoustic
pattern, not a language, so the owner goes on saying "hey jarvis" in a
Portuguese sentence. openWakeWord ships no Portuguese wake model and this
spec does not want one.

Desk dictation. `desk/deskd.py` "owns no model" and reaches the daemon's
recognizer, so it inherits every line of section 3 with no desk-side change
at all.

---

## 2. What this is NOT

- **Not a translation layer.** Nothing here translates anything. Each port is
  natively multilingual; the job is telling them all the same thing once.
- **Not a second speech engine.** Piper and a second `Tts` adapter were the
  textbook answer and were rejected in D3, with the argument recorded there.
- **Not localisation of Butler's own interface.** The settings page, its
  help text and its labels stay in English. Only what Butler HEARS and SAYS
  is in scope.
- **Not multi-language at once.** One language at a time, switched
  deliberately. Automatic language detection is a capability nemotron
  advertises (`lang_detect: true`) and this spec declines it, because a
  recognizer guessing wrong mid-sentence is a worse failure than a setting.

  > **PARTLY OVERTURNED 2026-09-11 by `docs/language-auto.md` (AC-207..AC-215),
  > and the bullet above is kept whole because most of it still stands.**
  >
  > What SURVIVES: a recognizer guessing wrong mid-sentence IS the worse
  > failure, and nothing in that spec asks the recognizer to guess. It cannot
  > even be asked -- MEASURED, `Result.language` comes back `''` on every
  > call and the streaming API has no language field at all. The decision is
  > made ONCE, after the final, on whole text.
  >
  > What LOST: "switched deliberately". `language` gained `auto`, and the
  > turn's language is read from the owner's own words before Butler speaks.
  > The reason this bullet did not see it coming is that it was reasoning
  > about the ASR path, which was the only place a language decision lived
  > when it was written.
  >
  > The half of the worry that was REAL is a short utterance, and it is
  > measured: "sim" classifies as English at 0.514 confidence. That is what
  > the per-conversation LATCH exists for (AC-210) -- below the floor the
  > conversation keeps the language it already had.
  >
  > It also DELETED a guard this spec's 6.4 installed: `stt_engine` no longer
  > raises for a blank language on a `cache_aware` model, because
  > `stream(language=None)` is accepted and decodes BOTH languages better
  > than any pin. See `docs/language-auto.md` 1.2 and 7.

- **Not the eight fixed spoken lines.** See D5.

  > **DONE 2026-09-11** (AC-211, `docs/language-auto.md` 4.4), exactly as D5
  > predicted: `dialogue.LINES` and `dialogue.line(text, language)`. What
  > Butler LISTENS for is still English and is still out of scope.

---

## 3. Decisions

### D1. `language` lives in the Recognition group, not a group of its own

**Ruled: the `stt` group.** It is where `asr_language` already is, and it is
where the audio path first consumes the decision, which is the page's own
organising idea -- `test_settings_groups_follow_the_pipeline` pins groups to
pipeline order precisely so that reading down the page is reading the path
your voice takes.

Rejected: a group of its own. It would need a stage number, which would
either break `test_pipeline_stages_are_contiguous_and_follow_the_key_order`
or make it stage-less like `look` -- and `look` is explicitly "not a step in
the audio path", which language is. Inventing a stage for a cross-cutting
setting is how the pipeline stops being the organising idea.

The cost, stated rather than hidden: the Speech group's voice list is
filtered by a setting configured in the Recognition group, two boxes higher.
That box's blurb says so in one sentence. A shorter voice list with no
explanation would be the AC-7b shape -- a control that quietly changed and
did not say why.

### D2. The brain gets NOTHING. Owner's ruling, 2026-09-10

The owner ruled that no language instruction goes into `ROUTER_SYSTEM` or
`REPLY_SYSTEM`: a model answers in the language it was addressed in, so a
setting for it is a control that duplicates something already true.

**Built that way.** The reasoning is sound and it deletes an entire step, a
setting, and a class of prompt drift. What follows is not an argument
against it; it is the OBSERVATION POINT that would falsify it, written down
now so that a future session does not rediscover it as a mystery.

Two places where "the model mirrors its input" is weaker than it sounds, and
neither is tested here:

1. **The router.** It is prompted in English and answers a JSON verdict whose
   `answer` field is READ ALOUD. A small model filling an English-labelled
   schema from Portuguese input may fill it in English. The router here is a
   9B (`bartowski/Ornith-1.0-9B-Q5_K_L`).
2. **Harness mode.** The router's cleaned `action` string reaches a coding
   agent whose OWN system prompt is English and long. `claude` and `pi` have
   a strong English prior that Butler does not control.

**The falsification is one observation, not a test suite:** speak Portuguese
with `brain_mode = harness_only` and listen to what comes back. If it is
English, D2 is wrong for that path only, and the fix is one sentence
appended to the prompt built from the `language` value -- the mechanism this
spec already needs for nothing else. That is a one-line reversal, so this
ruling is cheap to be wrong about, which is the reason to take it.

### D3. `Tts.info["voices"]` carries a language per voice, and this is FORCED

`voices` is `list[str]` today. To pick a Portuguese voice from that list, the
daemon would have to know that `pf_` means Portuguese -- and a naming
convention is engine vocabulary, which the AC-41 gate in `test_seams.py`
bans from the core BY NAME. There is no version of this that keeps the flat
list and passes the tests already in the repository.

So each entry becomes `{"id": str, "language": str}`. The engine goes on
naming its own voices AND their languages; the daemon filters by the one
language it owns and never parses an id. AC-41 survives whole, and the
cheapest possible argument for the change is that `Asr.info` has carried
`languages` since it was written (1.1).

Rejected: a second `Tts` adapter on Piper, which has real `pt_BR` voices and
is what the port exists for. It buys a second model, a second child process,
a second cold start, a second `info` implementation and a second full run of
the `Tts` conformance suite -- for a language whose voices are already in
`node_modules`. If the owner's ear rejects Kokoro's Portuguese (section 8),
this is where the plan resumes, and gates 1 through 3 do not change, because
none of them names an engine.

### D4. `asr_language` is MIGRATED and DELETED, never left beside `language`

Two settings that both name a language is the defect this spec exists to
remove, arriving by a different door. `Settings._migrate` already exists for
exactly this shape (`daemon/settings.py:679`, the AC-30 pair migration): a
stored `asr_language` becomes the new `language` on first read, and the old
key stops existing. A daemon that will not start after an update is the
outcome that migration exists to prevent.

### D5. The eight fixed spoken lines stay English in this spec

> **BUILT 2026-09-11, and D5's own prediction was the design.**
> `dialogue.LINES` plus `dialogue.line(key, language)` -- eight strings and a
> dict keyed by language, as written below (AC-211,
> `docs/language-auto.md` 4.4). The paragraph about the spoken COMMAND tables
> is STILL TRUE and still the gap: what Butler LISTENS for is English.


`"Working on it."`, `"I ran out of room to answer."`, `"I got nothing back."`,
`"Starting a new conversation."`, `"Working in {name}."`, `"I could not
finish transcribing that one."` and two more are constants in `hub.py`,
`brain_endpoint.py` and `brain_harness.py`.

Deliberately out of scope. They are eight strings and a dict keyed by
language when the time comes -- not a framework, and not worth designing
before there is a second language in daily use. Named here so the gap is
recorded rather than discovered: with this spec built, Butler will hear and
answer in Portuguese and say "Working on it." in English.

The spoken COMMAND tables (`SPOKEN_MODES`, `SPOKEN_NEW_CONVERSATION`,
`EXPLICIT_PROJECT_PREFIXES`) are the same call and the same gap. `end_phrases`
is already an editable list setting, so Portuguese phrases work there today
with no change at all.

---

## 4. The shape

### 4.1 After: one decision, three derivations

```
                          language = "pt-BR"
                    ONE setting. ONE owner. `stt` group.
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          v                       v                       v
   AsrConfig.language       (nothing -- D2)        voice resolution
          |                                               |
          v                                               v
   the recognizer                                 a voice whose
   decodes pt-BR                                  language is pt-BR
          |                                               |
          +--------------------- + ----------------------+
                                 v
                        speak_start carries
                        BOTH voice and lang
                        (AC-160, the stamp)
```

### 4.2 Where the choices come from: discovered, never declared

```
   Asr.info["languages"]              Tts.info["voices"][].language
   ('en-US','pt-BR','es-ES',...)      ('en-us','en-gb','pt-br','ja',...)
            |                                       |
            +------------------ + ------------------+
                                v
                          INTERSECTION
                                |
                                v
              the `language` setting's choices_from list
                                |
                                v
             +------------------+------------------+
             |                                     |
      offered on the page                  NOT offered, ever
      (both ports can serve it)      (AC-154 gate FAILS the build
                                      if one ever is)
```

No locale string is written into `daemon/settings.py`. The mechanism is
`choices_from`, already used by `tts_voices`, `connections`, `harnesses` and
`harness_models`. Locale spelling differs between the two engines
(`pt-BR` from nemotron, `pt-br` from Kokoro's own table), so the
intersection normalises case and nothing else -- see 8.3.

### 4.3 The Kokoro Portuguese path, and the path NOT taken

```
  TAKEN:
    text
      |
      v
    espeak-ng  -v <lang> --ipa=3        (npm 'espeak-ng', full data, 9 MB)
      |
      v
    strip U+200D                        (espeak's tie bar; not in the vocab)
      |
      v
    tts.tokenizer(phonemes)             (stock, 115-token vocab, unchanged)
      |
      v
    tts.generate_from_ids(ids, {voice}) (PUBLIC, no _validate_voice)
      |
      v
    24 kHz mono PCM  ->  the existing BTL1 frame  ->  unchanged adapter

  NOT TAKEN:
    tts.generate(text, {voice})
      -> refuses any non-English voice
      -> phonemizes English only
```

Everything downstream of the PCM is untouched: the framed wire format, the
per-sentence chunking, the abort-by-handle path, the split timeouts. This
spec adds a phonemizer and one call site inside `tts-worker.js`; it does not
rewrite the engine.

### 4.4 What the owner sees

```
  [2] Recognition                     [6] Speech
  +---------------------------+       +---------------------------+
  | Recognition model         |       | Voice                     |
  |   nemotron (multilingual) |       |   pf_dora                 |
  |                           |       |   pm_alex                 |
  | Language                  |       |   pm_santa                |
  |   pt-BR              [v]  |       |                           |
  |                           |       | "Filtered by the language |
  | (restart)                 |       |  set in Recognition."     |
  +---------------------------+       +---------------------------+
           |                                       ^
           +---------------------------------------+
                     changing this reshapes that
```

---

## 5. Acceptance criteria

Each says which way it fails and whether that is recoverable -- the AC-92
lesson from `docs/satellite-ux.md` 11.1.

- **AC-152 (one language, one owner). MET, step 1.** Exactly one setting names the
  conversation's language, and every port derives from it. No second setting
  anywhere in the schema names a language or a locale. Fails as: `asr_language`
  surviving beside `language`, which restores D-35 by a different door.
  Recoverable.
- **AC-153 (the list is discovered, never declared). MET, from the engine catalogue.** The offered languages
  come from `Asr.info["languages"]` intersected with the languages
  `Tts.info["voices"]` reports. No locale string is hardcoded in
  `daemon/settings.py`. Fails as: a hand-written list that is wrong the day an
  engine is swapped -- the AC-108 defect, where a tuple in the daemon declared
  eleven voices against a pool of 28. Recoverable.
- **AC-154 (an unspeakable language is never offered). MET, step 4.** A language the
  recognizer can hear but no installed voice can speak does not appear as a
  choice, and a test FAILS if one does. Fails as: a picker that offers Korean
  and answers in silence. Recoverable, and the gate is what makes it so.
- **AC-155 (the port carries each voice's language). MET, step 2.** `Tts.info["voices"]`
  entries carry an id and a language. The daemon never parses an id to learn
  a language. Fails as: `pf_` being special-cased in the core, which the
  AC-41 grep gate catches on the same run. Recoverable.
- **AC-156 (a blank voice resolves inside the chosen language). MET, step 3.**
  `synthesize(text, "")` yields the engine's default voice FOR the current
  language, and the daemon resolves it before announcing it. Fails as the
  exact AC-7b shape already recorded in `test_seams.py`: the wire says one
  voice and the room hears another. Recoverable.
- **AC-157 (a voice that disagrees is refused OUT LOUD).** Setting a voice
  whose language is not the current one is refused with a message, never
  clamped, never silently substituted. Fails as: a control that saves,
  reports success and changes nothing, which is what teaches the owner the
  whole panel is decoration (AC-107's rule, applied to a second field).
  Recoverable.
- **AC-158 (Butler hears Brazilian Portuguese).** With `language = pt-BR`, a
  Portuguese utterance is transcribed as Portuguese, through the satellite
  page and through desk dictation, with no desk-side change. Fails as: an
  empty final, or English words spelled from Portuguese sounds. Recoverable.
- **AC-159 (Butler speaks Brazilian Portuguese). MET, step 3.** A Portuguese reply is
  synthesized with a Portuguese voice and Portuguese phonemes. Fails as:
  audio that is the right length and the wrong sounds -- the failure mode
  section 1.2 measures, and the one a passing test suite would not catch.
  Recoverable, but only detectable by ear, which is why AC-160 exists.
- **AC-160 (the wire says what it decoded and what it spoke).** `speak_start`
  carries `lang` beside `voice`, and `Final` carries the language it was
  decoded as. Fails as: a transcript nobody can audit after the fact. **NOT
  recoverable** in the only sense that matters -- the provenance of every
  turn recorded before the stamp exists is gone permanently, which is why
  this is built in step 1 and not last. **HALF MET after step 1** -- `final`
  carries it, `speak_start` does not until step 2 (6.1). **MET in full at step 3**: `speak_start` carries the language of the VOICE.

---

## 6. Build order

Four steps, each ending at something observable. Steps 1 and 2 are useful on
their own and ship on their own.

```
  step 1 --- `language` setting, ASR derivation, D4 migration, AC-160 stamp
     |       VERIFY: speak Portuguese, watch Portuguese text appear.
     |               Desk dictation too, with no desk-side change.
     |       SHIPS ALONE. Butler now hears you, and answers in an English voice.
     v
  step 2 --- `Tts.info["voices"]` entries gain a language (AC-155)
     |       VERIFY: the Tts conformance suite stays green; the fake adapter
     |               and both test doubles are updated in the SAME commit.
     |       SHIPS ALONE. Nothing user-visible changes yet.
     v
  step 3 --- espeak-ng + generate_from_ids in `tts-worker.js` (AC-159)
     |       VERIFY: the characterization test, THEN the owner's ears.
     |               This is the step section 8 says might fail.
     v
  step 4 --- the two seam gates (AC-152, AC-154)
     |       VERIFY: MUTATE first -- break `language`, confirm the mutation
     |               applied, watch the RIGHT test fail, restore (D-13).
     v
  step 5 --- DOX, and the criteria into `plan.md` on the owner's acceptance
```

Step 4 is last on purpose and it is not optional. A gate written before there
is anything to gate is a gate nobody has seen fail.

### 6.1 What the build found that this spec did not predict

**STEP 1 BUILT 2026-09-10.** Five things were wrong or missing in sections 0
through 5, and the first is the one that matters.

**D-36: the whole Recognition group was INERT, not just `asr_language`.**
Section 0 draws an arrow from `asr_language` to the `Asr` port. That arrow
has never existed. `build_hub` builds the recognizer from
`config/models.json` alone -- `ports.build(ports.ASRS, ..., **cfg["asr_engine"])`
at `butlerd.py:980` and `AsrConfig(**cfg["asr"])` at `:1042` -- and
`prefs` is constructed four lines LATER, so the settings store could not have
reached the adapter even if something had asked it to. A repo-wide grep finds
`asr_model`, `asr_backend`, `asr_latency` and `asr_language` read by NOTHING
outside `settings.py`. All four save, report success, broadcast to every
satellite, show a restart badge, and change nothing. `plan.md` 4.6a's claim
that "the flag is asserted in both directions against which module actually
reads the value" is false: `test_the_engine_settings_are_marked_restart`
asserts the FLAG and never a reader, which is D-13 -- a test that survives
the defect it is named after.

So step 1 was not a rename plus a derivation. It was building the wire for
the first time, and the caller-side assertion section 7 scheduled for step 4
had to land in step 1 instead, because without it there is nothing to prove
the wire exists. `language` is now wired; **`asr_model`, `asr_backend` and
`asr_latency` are still inert** and are left that way deliberately -- fixing
them means ruling on what `config/models.json` is for once the page can set
the same values, and that is the owner's call, not this spec's.

**The setting is NOT restart-flagged, which contradicts 4.4's mock-up.**
Section 4.1 draws `language -> AsrConfig.language` and section 4.4 draws
`(restart)` under the control. Only one can be true. `AsrConfig` is per
`asr.open()`, a `TurnPipeline` is built for every turn, and the GGUF is cached
per (path, backend) inside the adapter -- so the locale is an argument to
`stream()` and nothing reloads. 4.1 wins, `restart=False`, and the change
lands on the next thing said. Carrying the old flag across the rename would
have told the owner to restart for a change already taken, which is AC-7b
pointing the other way -- the reason `harness` has its flag asserted absent.

**D4 named the wrong mechanism.** `Settings._migrate` takes a key and returns
a VALUE; it has no rename axis, and `_load` drops any key not in `BY_KEY`
BEFORE `_migrate` is reached, so a rename routed through it would have thrown
the value away. What shipped is a `RENAMES` table applied above that drop.
The renamed value still goes through `validate`, and that is what makes it
safe rather than merely convenient -- see the next item.

**The owner's stored value was `"PT"`, which no engine here accepts.** A
verbatim migration would have carried a locale the recognizer refuses into a
setting that finally reaches the recognizer, turning a dead control into a
daemon that fails every turn. Because `language` is a CLOSED choice built
from the catalogue, `validate` rejects it and `_load`'s existing bad-value
branch drops it with a line naming the fifteen it would have accepted. That
is the right outcome and it needed no new code: a value that never did
anything is not a choice worth preserving into a setting that does.

**The choices are a closed `choice`, not a `text` box with a datalist.**
Section 4.2 designs a runtime intersection behind `choices_from`. That would
have worked, but `web/settings.js` only reaches the discovered-values path
for `kind="text"` and `kind="pair"`, so `choices_from` would have bought a
free-text field that `validate` merely strips -- and AC-154 would have been a
page hint rather than enforcement, with nothing stopping a POST of `xx-XX`.
Instead `_catalog_languages()` reads `engines/catalog.json`, exactly as
`_latency_presets()` beside it already did for `asr_latency`, so the list is
still the ENGINE's and never a tuple written into the daemon. AC-153's
runtime intersection with the speech engine belongs to step 2, when
`Tts.info["voices"]` first carries a language to intersect WITH.

**AC-160 is HALF met, deliberately.** `final` carries `lang` on the wire,
stamped from what the engine resolved rather than from the store. It is not
on `speak_start`, because in step 1 the speech engine is still English-only:
a `speak_start` claiming `pt-BR` while the room hears English is precisely
the wire-versus-room lie the rest of this project exists to prevent. It lands
in step 2 with the voice that makes it true.

**One gap left open and named:** `POST /api/dictate` returns `{"text": ...}`
and `Hub.dictate` returns a bare string, so there is no `Final` to stamp on
the dictation path. Dictation DECODES in the chosen language -- it goes
through the same `_asr_config()` -- but its response carries no `lang`.

### 6.2 What step 2 found

**STEP 2 BUILT 2026-09-10.** `Tts.info["voices"]` entries are
`{"id", "language"}`, reported by the engine and passed through whole.

**The filter this replaced already existed, and it was a guess.**
`tts_kokoro.ENGLISH_PREFIXES = ("af_", "am_", "bf_", "bm_")` had been
narrowing the offered voices to the English packs since AC-108, with the
stated reason that "a picker offering voices Butler cannot be SPOKEN TO in is
a control that half works". That reasoning is right and is kept. The
mechanism was wrong twice: it inferred a LANGUAGE from a NAME, which puts one
engine's naming convention where AC-41's gate forbids it, and it hardcoded an
answer to a question that is now the owner's. So D3 was not adding a concept
-- it was replacing a guess with the fact the engine already had. `kokoro-js`
carries `language` on every entry of its own voice table; nothing had ever
asked for it.

**`ports.voice_ids(info, language="")` exists because three callers wanted
names the moment entries stopped being strings** -- the picker, the preview
route and a seam test. Three copies of `[v["id"] for v in ...]` is three
places to miss when a fourth field arrives, which is precisely how `Tts.info`
broke four implementations in two files the day it landed. Matching is
case-insensitive, because the recognizer catalogue says `pt-BR` and Kokoro's
table says `pt-br` and neither is wrong; normalising at the one seam that
compares them is the alternative to the per-engine mapping table 8.3 warns
about.

**The picker is NOT filtered by language yet, deliberately.** With no
non-English voice installed, filtering on `pt-BR` returns an empty list, and
an empty list at that seam RAISES by design (AC-108: an empty picker means
the engine could not be asked). Filtering starts in step 3, with the voices
that make it mean something. This is why step 2 changes nothing the owner can
see.

**Both fakes now speak two languages.** `tts_fake` and `conftest.StubTts`
report one `en-US` and one `pt-BR` voice, because with a single language
every test of "offer the voices for this language" passes whether the filter
works or not -- a gate that cannot fail (D-13).

**The conformance suite caught the change, which is the system working.**
`test_info_reports_what_the_ui_needs` failed for both adapters on the first
run, against a REAL kokoro sidecar which reported all 28 voices correctly
tagged `en-us`/`en-gb`. That run is the evidence the JS half works end to
end. It gained one assertion the port always implied and never checked: the
default `voice` must be one of the `voices`.

### 6.3 What steps 3 and 4 found

**STEPS 3 AND 4 BUILT 2026-09-10. Butler speaks Brazilian Portuguese.**
MEASURED through the real sidecar, adapter to audio:

```
adapter reports 31 voices; 3 pt-br  (pf_dora, pm_alex, pm_santa)
  bm_george   3.00 s  rms 0.0849   (English, unchanged path)
  pf_dora     3.33 s  rms 0.0722   (Portuguese, the new path)
```

**Two synthesis paths, and the split is deliberate.** A voice `kokoro-js`
names goes through its own `generate()` untouched, because that path carries
a large English text normalizer -- numbers, times, currency, "Dr." to
"Doctor" -- which is not reimplemented here and would be silently lost by
routing English through the generic path. A voice it refuses goes through
the public `generate_from_ids` with phonemes from the full eSpeak NG build.
What works today keeps working byte for byte. This is the one place the
"merge duplicate code paths" rule is knowingly not followed, and the reason
is the normalizer.

**`tts-phonemes.LANGUAGES` is deliberately SHORTER than the packs on disk.**
54 packs ship, 28 are English, and the prefix encodes Kokoro's own
`lang_code`. Only `a`, `b` and `p` are offered. Coverage was measured for all
nine -- a, b, e, h, i, j clean against the 115-token vocab; f emits one `-`
and z one `5` that are not in it -- and coverage is necessary and NOT
sufficient: upstream Kokoro uses cutlet for Japanese and pypinyin for
Mandarin, so espeak's output for `j*` and `z*` tokenizes cleanly and sounds
wrong. The gate for adding a row is that eSpeak is upstream's own G2P for
that language AND somebody has listened to it. Only `p` has been listened to.

**Cost, measured:** 116 ms per sentence, each call instantiating the WASM
module afresh because the build exports no `callMain`. One call carrying four
sentences costs 128 ms total -- 32 ms each, and eSpeak emits exactly one
output line per input sentence, so batching would align. NOT done: batching
needs the whole reply before synthesis starts and would give back the
streaming that took first audio from 3925 ms to 673 ms.

**The review found three defects that shipped in steps 1 and 2**, and all
three are fixed here:

1. **A 15-second dead turn, every turn.** The schema offers every locale the
   CATALOGUE's models can do, while the LOADED model speaks whatever it
   speaks -- and `asr_model` is inert, so the owner cannot switch to the
   model that would make their choice work. Choosing `pt-BR` with parakeet
   loaded MEASURED as: `open()` succeeding, one engine error per audio FRAME
   (about 150 lines on a 5 s turn), `finalize` waiting out its whole 15 s
   floor, and "I could not finish transcribing that one". Loud, recoverable,
   and useless -- a microphone that answers nothing every time is D-1's
   spirit if not its letter. `Hub._speakable_language` now drops an
   unspeakable language to the model's default and says so ONCE per distinct
   mismatch. That is a degradation rather than a silent one; the fail-fast
   alternative fails every turn forever.
2. **`voice_ids`' `language` argument was dead code.** Mutating it to ignore
   the argument left the whole suite green at exit 0, because every one of
   its six callers passed a single argument. 6.2's stated reason for giving
   both fakes two languages was therefore backwards -- there was no test to
   make toothless. `test_the_picker_offers_the_voices_for_the_chosen_language`
   is that test now, and the mutation dies against it.
3. **`Final.lang` was documented as something it is not.** Three places said
   it carried "what the ENGINE resolved, never what the store asked for". It
   is the requested locale echoed back: measured, with `pt-BR` asked for and
   parakeet loaded, the engine refuses every frame and the field still reads
   `pt-BR`. Nothing reports a resolved locale -- `capabilities.languages` is
   a static tuple. The comments are corrected rather than the code, because
   the requested locale is the more useful fact on a failed turn: it is the
   explanation. The failed and empty `Final`s now carry it too, which is
   exactly where it was missing and most wanted.

**A `language` key in `config/models.json`'s `asr_engine` block is now a
BOOT REFUSAL.** The adapter's constructor kwarg wins over `AsrConfig`, so a
value there would make the setting silently inert again -- D-36 reappearing
in the file the fix exists to close.

### 6.4 D-36 closed, and the trap it sprang

**THE RULING (2026-09-10): the SETTING wins, and `config/models.json` keeps
only what is not a setting -- which adapter to load.** `plan.md` 4.6a already
said which of the two is authoritative ("the schema IS the contract"); this
just makes the code agree. `asr_model`, `asr_backend` and `asr_latency` now
reach the recognizer, and a value left in `asr_engine` for any of them -- or
for `language` -- is a BOOT REFUSAL rather than a silent override, because
two places naming one value is D-35 and the failure is the invisible kind.

**Wiring them broke the test suite, and the way it broke is the lesson.**
The suite went from 4:04 to 17:54 and three real-recognizer tests failed on
timeouts. Nothing was wrong with the feature: `conftest` forced
`BUTLER_CONFIG` and `BUTLER_TLS` to hermetic values at import but never
`BUTLER_STATE`, and the `daemon` fixture's own docstring said "this suite
shares the owner's state directory". So the suite read the OWNER's
`asr_model` -- nemotron -- and booted their recognizer instead of the one
the tests meant.

That was harmless for as long as those settings were inert, which is exactly
why it survived: **a defect and the thing that hid it were the same defect.**
`conftest` now sets `BUTLER_STATE` beside the other two, and the suite is
back to 4:50 with the same four pre-existing failures.

The general form, worth carrying: wiring a dead control is not a small
change. Every place that read the store while the value did nothing has been
running an experiment whose result nobody checked.

**Now observable on this machine.** With the Recognition group live, an owner
can select nemotron and `pt-BR` from the page and AC-158 is reachable without
editing a file. A model that demands a concrete locale and has none resolves
to its OWN first language and says so once, rather than raising out of a
constructor reached from the websocket read loop -- which would have closed
the satellite's connection instead of failing one turn.

> **THE SECOND SENTENCE IS GONE, 2026-09-11** (AC-215,
> `docs/language-auto.md` 1.2 and 7). NO MODEL HERE DEMANDS A CONCRETE
> LOCALE. That belief came from `stt_engine`'s own `ValueError`, not from the
> engine -- and `hub._speakable_language`'s substitution was therefore a
> guard guarding a guard. MEASURED through the real streaming path:
> `stream(language=None)` is ACCEPTED and decodes English perfectly and
> Portuguese better than either pin, while pinning the wrong locale loses a
> whole sentence. What the engine rejects is the literal string `auto`
> (status 10), which no Butler code passes. Both the raise and
> `info["requires_language"]` are deleted; a blank language now means what it
> always said it meant, and the model decides per utterance.
> The first sentence still stands, and so does the unspeakable-language guard
> above it.

---

## 7. Tests, and the mutation discipline

Two new gates in `test/test_seams.py`, which is where this shape already
lives:

- **`test_every_offered_language_is_speakable`** (AC-154). Every language the
  schema offers appears in both `Asr.info["languages"]` and the languages of
  `Tts.info["voices"]`. Guard the guard the way `_voice_names` already does:
  an empty pool SKIPS rather than passing vacuously, because
  `node_modules` is gitignored and a fresh clone must not report a green gate
  it never ran.
- **`test_the_language_reaches_every_port`** (AC-152, and D-18 by name). A
  CALLER-side assertion: set `language`, run one turn, and assert the
  `AsrConfig` carried it and `speak_start` announced a voice of that
  language. Every one of this project's three worst defects passed the
  callee's own tests.

One characterization test (`test_tts_kokoro.py`): a fixed Portuguese
sentence through the real engine, asserting a stable sample count and a
non-trivial RMS. It proves the path is wired and CANNOT prove the audio is
intelligible. Section 8 is where that is said properly.

**The mutation, before any of these is believed.** Break `language`
resolution, confirm the mutation applied, watch the right test fail, restore.
A test that survives the defect it is named after is not a test (D-13).

---

## 8. What this spec could have wrong

**8.1 Kokoro's Portuguese may not be good enough, and that is the real
risk.** What is measured is that the path produces real, correctly-timed
audio: 3.08 s at rms 0.067 for a sentence that took 6.90 s through the
English phonemizer, and that inflation is the signature of spelling
Portuguese with English sounds. What is NOT measured is whether a Brazilian
listener finds it natural. Kokoro's `pf_`/`pm_` voices are trained on far
less data than `bm_george`. The owner should listen to
`%TEMP%\butler-ptbr\pf_dora.wav` before step 3 is started. If it fails that
test, D3's rejected option becomes the plan and steps 1, 2 and 4 do not
change -- none of them names an engine.

**8.2 The end-of-turn model on Portuguese is unverified.** Butler endpoints
with `smart-turn-v3.2-cpu.onnx`. Upstream describes v3 as multilingual and
Portuguese is in its training set [from memory, not verified here] -- there
was no labelled Portuguese audio to test against. If it endpoints badly, the
symptom is Butler cutting the owner off or waiting too long, and the existing
escape hatch is `turn_mode = endword` with Portuguese `end_phrases`, which
needs no code.

**8.3 The two engines spell locales differently and the intersection is
naive.** nemotron says `pt-BR`; Kokoro's own table says `pt-br`, and for
other languages just `ja` or `zh` with no region at all. Case-folding covers
Portuguese and is not a general answer. A language whose two engines
disagree on granularity will silently drop out of the intersection and simply
not be offered -- which is AC-154 behaving correctly and looking like a bug.
If that happens, the right fix is a normalisation the ADAPTERS do, not a
mapping table in the core.

**8.4 D2 may be wrong for harness mode**, and section 3 says where to look
and what the one-line reversal is. Listed again here because a spec's
section 8 is the first thing read when something is off.

**8.5 `espeak-ng` is a new runtime dependency in a process that had one job.**
It is 9 MB, it is WASM, and it initialises per call in the probe above.
Whether that per-call cost is acceptable inside the synthesis hot path is
UNMEASURED -- the probe measured synthesis, not steady-state latency. If it
is slow, the instance is created once at worker start beside the model, which
is what `tts-worker.js` already does for kokoro. Cheap to fix, but it is an
assumption and not a measurement, and this project's rule is that an untagged
number is a guess wearing a uniform.

**8.6 Nothing here was tested on the satellite.** No iPad, no phone, no
second device. The page changes are a filtered list and one extra field on
`speak_start`, so the risk is low, but "low risk" is not "checked".
