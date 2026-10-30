# Desk audio -- coexisting with another microphone client

**Status: PART BUILT, 2026-09-09; D3 RULED and AC-819..AC-823 BUILT,
2026-10-29.** Steps 1 and 2 of section 6 are DONE and the owner has tested the
result on their own desk ("so far, it works"). AC-819..AC-823 are in
`plan.md`; AC-124..AC-132 live here and move into `plan.md` only as the owner
accepts them. Until then `plan.md` is the contract and this file is an
argument.

**AC-126, AC-127 and AC-128 are MET.** AC-124, AC-125, AC-129, AC-131 and
AC-132 belong to steps 3 and 4 and are NOT built -- and their justification is
weaker than when this spec was written, because step 1 came back NOT
REPRODUCED and the defect it found instead is fixed without them. AC-130
(Butler and a browser at once) is neither met nor falsified: it never failed
in any measurement, which is not the same as passing.

The recurrence playbook lives in `desk/AGENTS.md`, not here, because that is
what a reader hits when dictation misbehaves.

Owner's ask, verbatim in substance: dictation works intermittently -- the cue
sounds and nothing is captured -- and Handy runs fine on this machine with a
browser holding the same microphone, so there must be a way for Butler to do
the same. Read Handy and take all of its solutions.

This is the tenth spec and the second one about `desk/`. `docs/desk-hotkeys.md`
built the feature; this one is about the single component it got wrong.

---

## 1. The failure, measured

The symptom is a dictation that returns nothing while the hotkey, the cue, the
paste path and the recognizer are all healthy.

**Every failed capture is EXACTLY 0.4 s**, which is `dictate_preroll_ms` and
therefore the lookback ring with zero live audio behind it. From
`butler.log`, four in a row:

```
[desk] no text (422): 'nothing was said in that clip' [0.4 s of audio, 0.2 s round trip]
```

So `Microphone.begin()` seeded the capture from the ring and not one callback
arrived before `end()`. The recognizer then correctly reported no speech in
0.4 s of stale room, and the agent passed that message on as though it were
the diagnosis. It is not: the truth is that the device delivered nothing.

**A second PROCESS reading the same device can receive zero callbacks --
silently.** MEASURED 2026-09-09, opening `sounddevice` at Butler's shipped
format while the desk agent held the device:

| condition | result |
|---|---|
| desk agent stopped, one process reading | 1.73 s captured, peak 10756 |
| desk agent running, second process reads | **0.00 s captured, peak 0** |
| desk agent running, second process reads | 1.44 s captured, peak 212 |
| two streams inside ONE process | both deliver |

No exception, no `PaErrorCode`, and `dropped_blocks == 0`. PortAudio simply
never calls back. The contention is per PROCESS, not per stream.

**Another process really was holding it.** Windows' consent store
(`HKCU\...\CapabilityAccessManager\ConsentStore\microphone`, where
`LastUsedTimeStop == 0` means in use now) shows Chrome held the microphone
from 15:01:48 to 15:09:14 on 2026-09-09 -- the satellite page, holding it for
the wake word. The same store lists Handy, Voquill, Voquill GPU, Zoom and OBS
as microphone clients on this machine, so the page is not the only candidate.

**IT IS INTERMITTENT AND IT DID NOT REPRODUCE ON DEMAND.** A later run of four
open-shapes x three repeats, all with the agent holding the device, delivered
audio in every cell that opened. Driving the LIVE agent with an injected 2.5 s
key hold logged `clipboard: 17 chars from 2.4 s of audio`. That is the single
most important fact in this document and section 6 is built around it: **there
is currently no reliable reproducer, so nothing here can be shown to work by
running it once.**

**Two causes were investigated and RULED OUT, so nobody repeats them.** The
default input device being the RDP `Remote Audio` endpoint is real but is not
the cause -- a single reader gets full-amplitude audio from it. And the agent's
main loop being blocked so that key-down and key-up drain together was
plausible and is wrong: `Daemon.poll` takes 8-33 ms against the live daemon
[MEASURED, 10 samples], which is 2% of the 2 s poll interval.

## 2. What Handy does (READ FROM ITS SOURCE, 2026-09-09)

Local checkout at `E:\PROJECTS\other-project\Handy`, HEAD `390729a`. Every claim
below is a file and a line, because web research about a codebase is a
hypothesis and the codebase is the answer -- a rule this project already paid
for once on this exact program.

**It never asks for a fixed sample rate.** `audio_toolkit/audio/recorder.rs`
`:403-410`, and the comment is the whole finding:

> Use the device's native/default sample rate and let the FrameResampler in
> run_consumer() downsample to 16kHz. This avoids forcing hardware into a
> non-native rate which can cause issues on some devices (Bluetooth codecs,
> certain ALSA drivers, etc.).

It reads `device.default_input_config()`, enumerates `supported_input_configs()`
and keeps only ranges spanning that same default rate (`:414-425`), scores
sample formats F32 > I16 > I32 (`:431-436`), and returns
`config.with_sample_rate(target_rate)` (`:447`).

**It resamples in software.** `rubato::FftFixedIn<f32>`,
`audio_toolkit/audio/resampler.rs:23-26`, built on the consumer thread
(`recorder.rs:527-531`) from whatever the device gave to
`constants::WHISPER_SAMPLE_RATE` = 16000. At equal rates it builds no
resampler and passes through. Channels are downmixed to mono in the callback
(`recorder.rs:377-384`).

**It never sets a buffer size.** The stream is built from `&config.clone().into()`
(`:396`) and there is no `BufferSize` anywhere in the tree, so cpal gets
`BufferSize::Default` and the host picks its own period.

**It uses the default host, in shared mode, and says nothing about sharing.**
`audio_toolkit/utils.rs:3-12` calls `cpal::default_host()` on every non-Linux
platform; that is the only host selection in the tree. There is no
`AUDCLNT_SHAREMODE`, no `exclusive`, no `DeviceInUse` handling anywhere in
`src-tauri/src`.

**It retries an open once, with a fresh device.** `managers/audio.rs:557-566`
invalidates the cached device and re-resolves before the second attempt, and
`recorder.rs:287` drops the cached config on failure.

**Its stream lifetime is a SETTING.** `always_on_microphone`
(`managers/audio.rs:337-341`) chooses between holding the stream open and
opening on key-down; on-demand closes immediately or, with `lazy_stream_close`,
after `STREAM_IDLE_TIMEOUT` = 30 s (`:20`, `:432-453`). `AudioRecorder::open`
is idempotent (`recorder.rs:140-142`), so a key-down inside that window reuses
the live stream.

**It has NO watchdog, and this is the one thing not to copy.** Greps for
`watchdog|heartbeat|reopen|no samples|stall` find nothing that notices a stream
which opened and never delivered. What exists is weaker: a 2 s drain timeout on
the STOP path only (`recorder.rs:655-674`), first-chunk latency LOGGED on
arrival and never policed (`:708-715`), and an empty result handled
cosmetically (`actions.rs:673-679` -- debug line, overlay hides, no error, no
reopen). The comment at `managers/audio.rs:576-580` states the hazard and then
does not handle it. **Handy would show Butler's exact symptom and not detect
it.** Its resistance comes from how it OPENS, not from recovery.

**Verification limit, stated rather than hidden.** cpal is not vendored in the
checkout and is not in the local cargo registry, so "`default_host()` is WASAPI
on Windows" is inference from `Cargo.lock:1032` (cpal depends on `windows
0.54.0`) plus the absence of an `asio` feature. It is not source-verified here.

## 3. Where Butler differs -- three axes at once

`desk/audio.py:85-88` opens:

```python
sd.InputStream(samplerate=16000, channels=1, dtype="int16",
               blocksize=512, device=None, callback=self._on_block)
```

That differs from Handy on the sample rate, the block size and the host API,
and the three are not independent.

**The fixed 16 kHz is what FORCES Butler onto MME, and this part is proved
rather than inferred.** MEASURED 2026-09-09, three attempts, three failures:

```
WASAPI 16000x1 : OPEN FAILED: Invalid sample rate [PaErrorCode -9997]
WASAPI 44100x2 : 1.80s | 1.87s | 1.95s   (the device's native config)
```

A WASAPI shared-mode capture client must match the endpoint's mix format.
While Butler demands 16 kHz it cannot reach WASAPI at all, so it falls to
PortAudio's first Windows host API, MME -- the legacy emulation layer, which
is where the silent starvation was measured. `requirements.txt` currently says
"MEASURED 2026-09-09: 16 kHz mono int16 is accepted DIRECTLY on this machine,
so nothing resamples", which is true and incomplete: it is accepted by MME
only, and that acceptance is the problem rather than a convenience.

Both host APIs report the same endpoint: `Remote Audio`, native **44100 Hz x
2ch**.

## 4. Decisions

**D1. Open on WASAPI, at the device's native config, with no pinned block
size.** [PROPOSED -- this is the substance of the ask]

Select the WASAPI host explicitly by walking `sd.query_hostapis()` rather than
trusting `device=None`, take that API's default input device, and open at its
`default_samplerate` and channel count with `blocksize=0`. Shared mode is the
default and must stay the default: `sd.WasapiSettings(exclusive=True)` would
be the exact opposite of the ask, and Handy sets no share mode at all.

**D2. Resample in Python, and the dependency is a real choice.** [OWNER'S
RULING NEEDED]

The `Asr` port takes 16 kHz mono int16 and nothing else (`ports.SAMPLE_RATE_IN`),
so 44100x2 must become 16000x1 inside `desk/audio.py`. Nothing installed can do
it: numpy 2.5.2 is present, and scipy, soxr, samplerate and resampy are not.

- *(a) `soxr`.* One small wheel, no scipy, high quality, the closest thing to
  Handy's rubato. Adds a dependency to a repository that currently has five.
- *(b) numpy only.* A hand-rolled polyphase filter plus a low-pass. 44100 to
  16000 is a ratio of 147:160, so naive decimation aliases speech into the
  band the recognizer reads; the filter has to be written and reviewed by
  somebody, and getting it subtly wrong degrades every transcript silently,
  which is this project's recurring failure shape.
- *(c) Ask the device for 16 kHz first and fall back to native.* Rejected on
  sight: it is a fallback, which this repository forbids, and it would keep
  the MME path alive as the common case on exactly the machine that fails.

Recommendation: (a). The measurement that would change it is a quality
comparison against the recorded fixtures, which step 3 of section 6 runs.

**D3. Does the stream stay open while hotkeys are on?** [RULED by the owner,
2026-10-29: per press when the lookback is 0, which is the new default]

With `dictate_preroll_ms` 0 the desk agent holds the microphone only while a
capture is running: arming hotkeys opens no stream, a press opens it and the
capture's end closes it, with no lazy close. The stream stays open while
hotkeys are on only when the owner sets a lookback above 0, because a ring
needs a live stream. Evidence (2026-10-29, the owner's PC used over Remote
Desktop): the only microphone is the RDP `Remote Audio` endpoint, whose
AUDIO_INPUT channel sometimes stalls and recovers ONLY when no process holds
the device (RdpCoreTS events 148, then 132). With Butler holding it for its 400
ms lookback, an earlier prototype's dashboard's browser microphone died every ~15.5 s and
never recovered, 152 times in one day. The same A/B was measured on 2026-10-25
in an earlier prototype (commit 2805ab2f). The cost the owner accepts: the first word may be
clipped by about 260 ms [MEASURED 2026-09-09]. The earlier options (a), (b) and
(c) are superseded. Handy opens per dictation and has always-on off by default.

**D4. A deaf stream is DETECTED and REOPENED.** [PROPOSED -- and it is the
half Handy does not have]

`Microphone` counts callbacks. A held-key capture that receives zero live
blocks is a device failure and must be reported as one, not forwarded as the
recognizer's "nothing was said". The stream is then torn down and reopened
before the next press, so a transient holder costs one dictation rather than
every dictation until the daemon restarts.

This is the only item that still helps if D1 turns out not to be the cause,
which is why it is not optional and why it is ordered first in section 6.

**D5. No microphone-device setting in this spec.** [PROPOSED]

The owner ruled explicitly that this is not a settings problem. A device
picker is a reasonable feature and is deliberately out of scope here; adding
one would let a real bug hide behind a control the owner has to find.

## 5. Acceptance criteria

Each says which way it fails and whether that is recoverable -- the AC-92
lesson.

- **AC-124 (the desk microphone opens on WASAPI in SHARED mode).** The stream
  is opened through the host API named `Windows WASAPI`, at the device's
  native sample rate and channel count, with no pinned block size, and never
  with `exclusive=True`. Fails as: silently back on MME, which is invisible
  until another application takes the microphone. Recoverable.
- **AC-125 (the port still yields 16 kHz mono int16).** Whatever the device
  gives, `Microphone.end()` answers 16 kHz mono int16, because
  `POST /api/dictate` and the `Asr` port take nothing else. Fails as: audio at
  the wrong rate reaching the recognizer, which is a transcript that is wrong
  rather than absent. NOT self-evident -- a 44.1 kHz buffer labelled 16 kHz
  decodes to plausible nonsense.
- **AC-126 (a deaf stream is named, never misattributed).** A held-key capture
  that received zero live callbacks is reported as the microphone delivering
  no audio, names the device and host API, and plays the failure cue. It must
  never surface the recognizer's "nothing was said in that clip", which is a
  statement about speech. Fails as: today -- an hour spent on the wrong
  organ. Recoverable, and this criterion exists because it was not.
- **AC-127 (a deaf stream is reopened).** After AC-126 fires, the stream is
  closed and reopened before the next capture. Fails as: dictation dead until
  the daemon restarts, which is what the owner met. Recoverable.
- **AC-128 (the device and host API are logged at open).** One line naming the
  device, the host API, the native rate and the resampling in force. Fails as:
  "which microphone is Butler using" being unanswerable from the log, which it
  is today (AC-7b).
- **AC-129 (an open failure retries once with a fresh enumeration).** Handy's
  `managers/audio.rs:557-566`. Fails as: a device that changed identity after
  an unplug or a format change looking exactly like contention. Recoverable.
- **AC-130 (Butler and a browser hold the microphone at the same time).** With
  a satellite page open and listening for the wake word, a dictation captures
  the owner's speech and both keep working. Fails as: the reported bug. **This
  is the criterion the owner actually asked for, and section 6 step 1 exists
  because it cannot currently be tested.**
- **AC-131 (the resampler is MEASURED, not assumed).** The recorded fixtures
  decode through the native-rate path to the same text as through the current
  16 kHz path, and the added per-block cost is written down with its number.
  Fails as: a quiet accuracy regression nobody attributes to this change --
  which is the failure mode `bench_dictate.py`'s `chars` column already exists
  to catch.
- **AC-132 (the lookback ring survives the change).** A capture still begins
  with audio that arrived before the key went down, at 16 kHz after
  resampling. Fails as: the first word clipped again, silently. Recoverable.
- **AC-819 (with a lookback of 0, a press owns the microphone).** With
  `dictate_preroll_ms` 0, arming hotkeys opens no stream; a press opens it and
  the capture's end closes it. Above 0 nothing changes: the stream stays open
  while hotkeys are on. `audio.Microphone.holds_open` is the one place that
  decides. Per press, the listening cue plays after the device opens. Fails
  as: the device held permanently, which breaks Remote Desktop audio for every
  other app. Recoverable.
- **AC-820 (every ending path releases the device).** Key-up, stuck-key
  recovery, the ceiling (released when reached, while the key is still held),
  hotkeys turned off, a failed open and a `stop()` that raises. Fails as: a
  leaked stream that holds the device until the daemon restarts. Recoverable.
- **AC-821 (a failed open at the press is loud and retried).** The failure is
  logged, plays the failure cue, returns the agent to idle, and posts and
  pastes nothing; the next press tries to open again. Fails as: a latched
  error that disables dictation. Recoverable.
- **AC-822 (the default is 0 and the help names both costs).** The default of
  `dictate_preroll_ms` is 0, and its help names the clipped first word and the
  broken Remote Desktop audio for every app. Fails as: the owner choosing a
  value without knowing the price. Recoverable.
- **AC-823 (per press, a deaf capture reopens nothing).** With no live audio a
  capture still says NO AUDIO and cues failure, but reopens nothing; the next
  press opens fresh. The reopen of AC-127 applies only to a held-open stream.
  Fails as: a reopen leaving the device held between presses. Recoverable.

## 6. Build order

Ordered by what is provable, not by what is interesting. The first step is not
a feature.

1. **A REPRODUCER, before any fix.** **DONE 2026-09-09, and it came back
   NOT REPRODUCED -- which turned out to be the most useful result in this
   document.** `test/manual/spike_mic_contention.py` ran three open shapes
   against three contenders (nobody, a second Python process, and real Chrome
   holding the REAL microphone through `getUserMedia`) and every shape that
   opened delivered audio every time. WASAPI at 16000 failed to open in every
   cell, which is the section 3 finding reproduced on demand.
   **What it found instead is the actual defect.** With `hotkeys_enabled` True
   and the agent running and having logged `hotkeys ON`, Windows' consent
   store reported NOBODY holding the microphone -- the agent's stream had died
   and the agent had no idea, because `apply_settings` acts only on a CHANGE.
   An injected key hold at the live agent in that state produced NOTHING AT
   ALL: no log line, no cue, because the ring was empty, the capture was 0
   bytes and `_deliver` swallowed it as a mistouch. A fresh stream on the same
   device delivered 1.28 s immediately, and the consent store was
   control-checked by opening a stream and watching it appear and disappear.
   So the failure is a LONG-LIVED stream dying, not a second reader being
   refused. Step 2 is the fix; steps 3 and 4 are no longer known to address
   anything and section 7's first warning now applies to them in full.
   The original text of this step follows. Nothing else in this document can be
   verified without one: the failure did not reproduce in 12 consecutive
   attempts and the live agent works right now. Build
   `test/manual/spike_mic_contention.py` -- two processes opening the same
   device in a matrix of host API, rate and open ORDER, run for minutes, and
   report which combinations ever receive zero callbacks. Drive a real browser
   holding `getUserMedia` as the second client, because that is the reported
   case and Chrome's capture path is not a Python stream.
   -> verify: a command that fails reliably. **If no combination reproduces,
   STOP and report that**: the fix would then be unfalsifiable, and D4 alone
   should ship, because it converts an invisible failure into a named one.
2. **D4, the detection and the reopen (AC-126, AC-127, AC-128).** **DONE
   2026-09-09.** `Microphone.live_blocks`, `silent_for()`, `reopen()` and
   `info`; `Agent._revive`, a reopen at `_begin` when the stream has been
   silent past `STREAM_DEAD_S`, and a refusal in `_deliver` that names the
   device. Verified: `test_desk_dictation.py` 32 passed, full fast tier
   `622 passed, 55 deselected in 119.34s`, exit 0. D-13 EXECUTED, exit codes
   only: disabling the no-audio check reddened its two honest witnesses --
   `..._says_the_MICROPHONE_failed` and `..._is_REOPENED_...` -- and nothing
   else in the file; disabling the reopen-before-capture reddened exactly its
   own test with the rest of the file green; both restores exit 0.
   The original text of this step follows. First
   because it is the only item that helps whatever the cause is, and because
   it makes every later step observable. Pure Python, no dependency, no format
   change.
   -> verify: with the stream artificially deafened, the agent says so and the
   next press works.
3. **The resampler (AC-125, AC-131, AC-132), behind the existing format.**
   Add the dependency chosen in D2 and resample 44100x2 to 16000x1 with the
   OLD open path still in place, so the resampling is proved on its own before
   the host API changes underneath it. Measure the fixtures both ways.
   -> verify: `bench_dictate.py` and the recorded fixtures decode identically.
4. **The WASAPI open (AC-124, AC-129).** Only now, with a resampler that is
   already proved, is the open shape changed.
   -> verify: step 1's reproducer, and the log line from AC-128.
5. **D3's ruling.** **DONE 2026-10-29** (AC-819..AC-823): per press at lookback
   0, no lazy close.
6. **DOX and the criteria.** `desk/AGENTS.md`, `requirements.txt`'s now-false
   comment about 16 kHz being accepted directly, and AC-124..AC-132 into
   `plan.md` on the owner's acceptance.

## 7. What this spec could have wrong

**The whole causal chain is inference.** What is measured: a second process
sometimes gets zero callbacks; WASAPI refuses 16 kHz so Butler is on MME; a
solo reader always works; Handy uses native rate on the default host. What is
NOT measured: that moving to WASAPI at native rate makes the contention go
away. The subagent that read Handy flagged the same gap -- nothing in that
checkout claims or tests coexistence, and its own comment blames Bluetooth
codecs and ALSA rather than sharing. Step 1 exists to close this, and step 1
is allowed to conclude that the fix is unprovable.

**The intermittency may have an entirely different trigger.** RDP audio
redirection is in the path on this machine, and a redirected endpoint is not a
sound card. If the failure is the RDP channel stalling rather than contention,
every item here is beside the point and the symptom would follow the remote
session rather than the browser. The reproducer must therefore record whether
a satellite was connected, not assume it.

**Resampling changes what the recognizer hears.** parakeet is fed 16 kHz today
from a device that produced 16 kHz. After this it is fed 16 kHz produced by a
filter. AC-131 measures it against the fixtures, and the honest expectation is
"no measurable difference" rather than "no difference".

**A second process is not a browser.** Every contention measurement so far
used a Python stream as the other client. Chrome's capture path may share
differently -- it may already be on WASAPI shared, which would mean Butler on
MME is the only party doing something unusual. That is a reason to expect the
fix to work and not a reason to skip step 1.

**`blocksize=0` changes the callback size**, and `desk/audio.py`'s ring is
sized in BLOCKS rather than in milliseconds (`frames = (preroll_ms *
BYTES_PER_MS) // (BLOCK * 2)`). With a host-chosen period the ring must be
computed from a duration instead, or `dictate_preroll_ms` silently stops
meaning milliseconds.
