# Butler picks the voice to match the language being spoken

A SPEC, not the contract. Its criteria (AC-207..AC-215) move into `plan.md`
only as the owner accepts them.

Written and built 2026-09-11, from the owner's ask. Every number below was
measured that day against this checkout, the installed `kokoro-js`, the GGUF
in `models/` and `py3langid` 0.4.0, unless it carries a different tag.

This is the second half of `docs/language.md`. That spec gave Butler one
language and one owner for the decision; this one takes the decision away
from the settings page and gives it to the owner's own voice.

---

## 0. What this fixes

`docs/language.md` closed D-35 by making ONE setting name the language. It
left the owner switching it by hand. Their live daemon runs `language =
pt-BR` and `voice = pf_dora`, and every English turn pays for it -- an
English sentence transcribed by a Portuguese-pinned recognizer and read back
by a Portuguese voice.

So the ask is: decide the turn's language from THEIR QUESTION, before Butler
speaks.

That is a smaller change than it sounds, because the machinery
`docs/language.md` built is all reusable. `Tts.info["voices"]` already says
which voice speaks what (AC-155), `voice_ids` already filters by language
(AC-154), and `_announced_voice` already resolved a blank voice inside the
chosen language (AC-156). What was missing was something that answers "which
language is this?" from text, and a place to put the answer.

### 0.1 A live defect fixed on the way past

`hub._announced_voice()` resolved a blank voice inside the chosen language,
and `_speak` then handed the engine the RAW `self._voice()`. With `voice = ""`
and `language = pt-BR`, `speak_start` announced a Portuguese voice **while
the room heard the engine's English default reading Portuguese words** -- the
exact AC-7b shape `_announced_voice`'s own docstring says it exists to
prevent. `test_seams.py` missed it because it checked the announced name
against the UNFILTERED voice list, where an English default is a perfectly
legal answer.

It is gone by construction (AC-212): `Hub._voice_for(language)` is the ONE
resolver, and `_begin_speaking`, `_speak` and `tts_say` all ask it. There is
nothing left to diverge.

---

## 1. The two measurements that decided the shape

### 1.1 The recognizer will not say what it heard

nemotron reports `supports_language_detect: True` and 32 locales, and
`Result.language` comes back `''` on every call -- asked, unasked, English or
Portuguese. The STREAMING API a live turn uses has no language field at all:
`feed / finalize / last_status / reset / revision / state / text`.

So the recognizer is not a source for this, and detection has to come from
TEXT.

### 1.2 It does not need telling either

[MEASURED 2026-09-11, `test/manual/spike_asr_unpinned.py`,
nemotron-3.5-asr-streaming-0.6b, cpu backend, `accurate` preset, through the
real STREAMING path]

| audio | `language=None` | `language='en-US'` | `language='pt-BR'` |
|---|---|---|---|
| English fixture (`two_sentences.wav`) | `The tests are passing. I push the branch.` | identical | `I push the branch.` **-- a sentence lost** |
| Portuguese (synthesized, `pf_dora`) | `Voze pode rodar os testes e me dizer quantos falharam.` | `Voz se pode rodar...` | `Voz se pode rodar...` |

Unpinned is never worse than a pin and is sometimes much better. Note the
third column of the second row: pinning the RIGHT locale was no better than
pinning the wrong one, which is why all three were measured rather than the
two that were expected to differ.

Separately, by opening a stream per value: the engine accepts `None`, `''`
and `pt-BR`, and REJECTS the literal string `'auto'` with
`UnsupportedRequest: transcribe_stream_begin: unsupported language
(status 10)`.

**This falsified the plan's own premise** (see section 7).

---

## 2. The owner's rulings

Three, given 2026-09-11.

**R1. Decide from their question, before Butler speaks.** Not mid-sentence,
not from the recognizer, not after the fact. So the decision is made once, in
`_on_final`, on the finished utterance, and `speak_start` can announce a
voice that is settled for the whole turn.

**R2. Use py3langid, restricted to the languages Butler has voices for.** A
local character-n-gram classifier rather than a model call: the decision
happens on EVERY turn before Butler speaks, and a round trip there is added
straight to time-to-first-audio (AC-4). Restricted, because a detector free
to answer a language with no voice behind it would be right while the room
heard the engine's default.

**R3. Translate the eight fixed spoken lines.** `docs/language.md` D5
deliberately left them English and predicted this: "eight strings and a dict
keyed by language when the time comes".

---

## 3. What this REVERSES, and the argument that lost

`docs/language.md` section 2 says:

> **Not multi-language at once.** One language at a time, switched
> deliberately. Automatic language detection is a capability nemotron
> advertises (`lang_detect: true`) and this spec declines it, because a
> recognizer guessing wrong mid-sentence is a worse failure than a setting.

That sentence is kept in place rather than deleted, with a box beside it
naming this spec, because **most of it survives and only one clause does
not**.

What survives: a recognizer guessing wrong mid-sentence IS a worse failure
than a setting, and nothing here asks the recognizer to guess. Section 1.1
measured that it cannot even be asked. The decision made here is made ONCE,
after the final, on whole text, and the recognizer is told nothing at all.

What lost: "one language at a time, switched deliberately". The switch is no
longer deliberate, and the reason the old argument did not see this coming is
that it was reasoning about the ASR path -- the only place a language
decision lived when it was written.

The half of the old worry that is REAL is a short utterance, and it is what
the latch (AC-210) is for. See 4.2.

---

## 4. The design

### 4.1 One decision, one owner, one place

```
  owner speaks
       |
  final text ----> Hub._turn_language(s, text)        <- THE ONLY DECISION
       |                    |
       |            language == auto ?
       |             /               \
       |         no: the setting     yes: dialogue.detect_language(
       |             wins                   text, ports.voice_languages(tts.info))
       |                                       |
       |                             committed? -- yes -> that language
       |                                       \- no  -> KEEP s.language (the LATCH)
       v                                                   |
  s.language  <---------------------------------------------
       |
       +--> TurnContext.language  --> the adapters' fixed lines
       +--> Hub._voice_for()      --> speak_start.voice AND the engine
       +--> dialogue.line()       --> the hub's own fixed lines
```

`SatelliteSession.language` and not only `TurnContext.language`, because
`_speak` is reached from six places that have no context at all: the spoken
mode change, the new conversation, both project lines, the recognizer failure
and the reply loop.

### 4.2 The LATCH, and the numbers that make it necessary

[MEASURED 2026-09-11, `test/manual/spike_language_id.py`, py3langid 0.4.0
with `norm_probs=True`, candidates read from the speech engine]

Written text, 21 cases, floor 0.60:

```
 18 right, 0 wrong, 3 abstained (3 short, 0 long)
```

Round-tripped through the REAL engines -- each case synthesized by kokoro,
transcribed back by nemotron with no language pinned, then classified:

```
 20 cases: 16 right, 0 wrong, 4 abstained (4 short, 0 long)
```

Every LONG utterance was named correctly in both languages, at >= 0.999
confidence, in both runs. **Nothing was ever named wrong.** Everything that
abstained was a whole-word answer:

| utterance | verdict | confidence |
|---|---|---|
| `yes` | en | 0.576 |
| `no` | en | 0.500 |
| `sim` | **en** | 0.514 |
| `obrigado` | pt | 0.988 |
| `pode parar` | pt | 0.974 |
| `thanks` | en | 0.942 |

`sim` is the row that decides the design: the detector is WRONG about it, at
low confidence. So a daemon that trusted every verdict would flip a
Portuguese conversation into an English voice on the Portuguese word for
"yes" -- which is exactly the failure section 3's old argument named. The
latch is the answer: an abstention keeps the language the conversation is
already in.

`DETECT_MIN_CONFIDENCE = 0.60` is set above the highest confidence any wrong
or borderline answer reached and far below every long utterance's. It is
where the two failure modes separate, not a tuning knob.

Cost: 497 ms to load the model ONCE at boot (warmed beside the three engine
ports, D-10), 0.02 ms per classification.

### 4.3 The three vocabularies, reconciled by a RULE

The detector answers ISO-639-1 (`pt`), Kokoro reports `pt-br`, the recognizer
catalogue says `pt-BR`. `voice_ids` already folded case at that seam; it now
also matches on the BCP-47 PRIMARY SUBTAG when the wanted language carries no
region (AC-208). A want WITH a region still matches exactly, so a voice the
owner picked by hand is never widened.

A rule and not a locale table, which is what keeps the promise `voice_ids`'
own docstring already made: normalising happens HERE, at the one seam that
compares them, rather than as a mapping in the core.

`ports.voice_languages(info)` is the other half: the candidate set, read off
the engine, with regions collapsed. No language is written down anywhere in
the daemon (AC-42).

### 4.4 The fixed lines

`dialogue.LINES` is keyed by the ENGLISH LINE ITSELF, and `dialogue.line(text,
language)` returns the translation or the text unchanged. That makes English
the fallback BY CONSTRUCTION rather than by a branch, and it keeps the eight
English wordings at their call sites with the comments that argue for each.

The cost of that choice is that a reworded line silently orphans its
translation, so `test_language_auto.py` asserts every key in `LINES` is still
one of the eight live constants, and that a translation keeps the fields its
English has (one line carries `{name}`).

The eight:

| line | where |
|---|---|
| `Working on it.` | `brain_harness.NARRATION` |
| `The harness could not finish that one.` | `brain_harness.FAILED` |
| `The harness finished without saying anything.` | `brain_harness.NOTHING_CAME_BACK` |
| `I ran out of room to answer.` | `brain_endpoint.OUT_OF_ROOM` |
| `I got nothing back.` | `brain_endpoint.NOTHING_CAME_BACK` |
| `I could not finish transcribing that one.` | `hub.ASR_FAILED` |
| `Starting a new conversation.` | `hub.NEW_CONVERSATION` |
| `Working in {name}.` | `hub.WORKING_IN` |

**OUT OF SCOPE, and said here rather than discovered:** what Butler LISTENS
for stays English -- `SPOKEN_MODES`, `SPOKEN_NEW_CONVERSATION`,
`EXPLICIT_PROJECT_PREFIXES`. Hearing a command in another language is a
different feature with its own ambiguity and the owner has not asked for it.
That is also why `NO_SUCH_PROJECT`, `PROJECT_FAILED` and the three turn-mode
confirmations stay English: they ANSWER a phrase that can only be said in
English, so translating the reply alone would be the odder half-step.

### 4.5 The controls say what they do

Under `auto`, `voice` stops deciding -- and the page DIMS it with the
sentence "Butler is choosing the voice to match what you say" (AC-213). Both
halves ship together and one test asserts both, because a setting that is
silently overruled is AC-7b and a control dimmed without being overruled is
the same lie pointing the other way.

`Settings._inert` gained a SECOND, independent pass for this. The existing
`INERT` table is keyed on `brain_mode`; folding a second axis into it would
mean repeating every brain mode three times.

The value is still stored and still validated, so turning `auto` off gives
the owner their voice back unchanged.

---

## 5. What was built

| Step | Where |
|---|---|
| 1. Measure the detector | `test/manual/spike_language_id.py` |
| 2. Primary-subtag matching, candidate set | `daemon/ports.py` |
| 3. The turn has one language | `daemon/hub.py`, `daemon/dialogue.py`, `ports.TurnContext.language` |
| 4. The engine gets the RESOLVED voice | `daemon/hub.py` |
| 5. The eight fixed lines | `daemon/dialogue.py`, both brain adapters, `daemon/hub.py` |
| 6. The controls | `daemon/settings.py`, `daemon/butlerd.py` |
| 7. Spec, DOX, probes | this file, `test/manual/mutate_language_auto.py` |

Nothing in `web/` changed. The settings page is generated from the daemon's
own schema (AC-41), so a new choice and a new `inactive` reason render with
no page edit; `speak_start.lang` was already on the wire and is still not
read by the page.

Verification, run 2026-09-11:

```
test/manual/spike_language_id.py        21 written + 20 round-tripped, 0 wrong
test/manual/spike_asr_unpinned.py       the table in 1.2
pytest test/ -q                         982 passed, 4 failed, 4 skipped
    the four are the configured pi provider answering HTTP 400
    ("gpt-5.4-mini is not supported when using Codex with a ChatGPT
    account"), which is the pre-existing baseline and is unrelated
test/manual/mutate_language_auto.py     13/13 caught
```

---

## 6. The criteria

**AC-207. `language` gains `auto`, and the detector's candidates are the
ENGINE's.** `auto` is the one entry in that picker not read from the engine
catalogue, because it is not a locale -- it is Butler's own instruction.
`ports.voice_languages` supplies the candidate set from
`Tts.info["voices"]`, so no language is named in the daemon. **Fails
visibly**: an unknown value in a closed `choice` is refused by `validate`.

**AC-208. A language with no region matches a voice's PRIMARY SUBTAG.** `pt`
finds `pt-BR` and `pt-PT`; `pt-BR` still finds only `pt-BR`. **Fails
silently if absent** -- `voice_ids(info, "pt")` returns nothing and every
detected turn falls through to the engine's default, which looks exactly like
the feature not being built.

**AC-209. The turn has ONE language, decided once, from the owner's words,
before anything is spoken.** Carried on `SatelliteSession.language` and
`TurnContext.language`. With a locale pinned, the setting wins and no
detector runs. **Recoverable**: pin a locale.

**AC-210. An abstention keeps the conversation's language.** The detector
returns None below `DETECT_MIN_CONFIDENCE`, and the previous turn's language
stands. A first turn with nothing latched takes the engine's own default
voice's language. **Fails audibly**: the wrong voice for one turn.

**AC-211. Butler's own eight fixed lines are said in the turn's language,
and English is the fallback for a language with no translation.** **Fails
audibly and harmlessly**: an English sentence in a Portuguese conversation.

**AC-212. The engine is given the RESOLVED voice, and it is the same string
`speak_start` announced.** One resolver, `Hub._voice_for`. **Fails
INVISIBLY on the wire** -- this is the defect described in 0.1, and the only
instrument that catches it compares what was announced against what the
engine was asked for.

**AC-213. Under `auto`, `voice` stops deciding and the page says so.**
**Not recoverable by a later fix if only half ships**: a dimmed control that
still decides, or a live control that does not.

**AC-214. A replay detects on the message's own text.** `POST /api/tts/say`
has no turn and no session; the message may be an hour old and the
conversation may have moved on. **Fails audibly**: a Portuguese message read
in an English voice.

**AC-215. `auto` never pins the recognizer.** Measured better than any pin
(1.2). The `stt_engine` guard that raised for a blank language on a
`cache_aware` model is DELETED, and `requires_language` went with it.
**Fails loudly if wrong**: the engine refuses the literal string outright.

---

## 7. What the plan for this work got WRONG

Kept whole, as `docs/harness-mode.md` 9 is.

**"`Hub._speakable_language()` returns `None` for `auto` -- the adapter
already accepts it."** It did not. `stt_engine.StreamingSession` RAISED
`ValueError` for a blank language on any `cache_aware` model, which is
nemotron, which is what the owner runs. `hub._speakable_language` then
substituted the model's FIRST locale to avoid that raise -- and nemotron's
first locale is `en-US`. So `auto` as planned would have pinned a Portuguese
speaker's recognizer to English, quietly, and the feature would have half
worked: the detector would still have named Portuguese from a degraded
transcript, so the symptom would have been slightly worse recognition and
nothing obviously broken.

The guard's premise was never measured against the engine. Its comment said
nemotron "rejects a missing locale outright" -- what the engine rejects is
the literal string `auto` (status 10), and `None` it accepts and decodes
better than any pin. `hub._speakable_language`'s own docstring then described
`StreamingSession`'s raise as though it were the engine's, which is how a
guard came to be guarding a guard.

Deleting it is a real behaviour change for a shipped setting and it is stated
rather than buried: a BLANK `language` on nemotron used to mean "decode in
en-US, with a warning"; it now means "let the model decide", which the table
in 1.2 says is better on English and better on Portuguese.

**The spike's own test table was toothless in one place, and the mutation
harness found it.** `test_auto_never_pins_the_recognizer` was first written
against an adapter that lists its locales -- where `auto` is simply not in
the list, so the pre-existing unsupported-language branch answers None by
accident and the row SURVIVED its mutation. The case with teeth is an
adapter that reports NO languages, which `_speakable_language` deliberately
never second-guesses.

---

## 8. Six things this could have wrong

1. **Only TWO languages are reachable today.** `tts-phonemes.js` enables
   `a`/`b`/`p` only. The Spanish, French, Italian, Hindi, Japanese and
   Chinese packs are on disk (22 more voices) and are three lines each, given
   espeak-ng has the data. Everything here widens with no code change -- the
   candidate set is read from the engine -- but nothing about the detector's
   accuracy across six languages is measured, and langid's confusions are
   worst between close neighbours (es/pt, it/es), which is exactly what
   enabling those packs would introduce.

2. **Voices per language are FOUND, not chosen.** `voice_ids(...)[0]` picks
   the first. Three Portuguese voices exist and the owner gets whichever
   sorts first, with no way to say which. A per-language voice preference is
   the obvious next ask and is not built.

3. **AC-157 is specified and not built** -- a voice whose language disagrees
   with the setting is not refused out loud. This feature picks voices
   programmatically, which is exactly the path that bypasses the picker's
   guard.

4. **Code-switching mid-utterance is not handled.** One language per turn. A
   sentence that starts in English and finishes in Portuguese gets one voice
   and the detector's own verdict about which.

5. **The measurement is 41 utterances, not a corpus.** Zero wrong answers on
   41 is evidence that this works for the sentences an owner says to a voice
   assistant; it is not a claim about langid's published accuracy, and the
   round-trip half went through ONE synthetic voice per language rather than
   a real room.

6. **The `harness` observation point from `docs/language.md` D2 is still
   open, and now has a second reason to matter.** The brain is given no
   language instruction, on the ruling that a model answers in the language
   it hears. In harness mode the router's cleaned action reaches a coding
   agent behind a long English system prompt. Nothing here changes that, and
   `TurnContext.language` now exists, so the temptation to wire it into a
   prompt is one line away. It would be reversing an owner's ruling, not
   using a parameter -- which is why `ports.TurnContext.language`'s docstring
   says so.
