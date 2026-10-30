# Desk hotkeys -- spec and build order

**Status: PROPOSAL, 2026-09-08.** Criteria AC-113..AC-123 live here and move
into `plan.md` only as the owner accepts them. Until then `plan.md` is the
contract and this file is an argument.

Owner's ask, verbatim in substance: a key held on the SERVER machine starts
dictation; released, the text is pasted where the caret already is and is also
left on the clipboard. A second binding, Ctrl plus the same key, opens a
terminal running the pi coding agent in a directory the owner picks. The ask
was phrased with "for instance" twice, so the shape is a small set of NAMED
actions with a binding each, not two hardcoded keys and not a scripting
language.

AC-123 was added on 2026-09-09 and is not in the owner's ask: dictation
pastes into whatever the caret is in, and that can be a terminal, where a
line feed is a RETURN rather than a character.

> **SUPERSEDED IN PART BY `docs/shortcuts.md`, later the same day. Read that
> file before this one if you are touching a key or the terminal action.**
>
> Everything here about the CAPTURE, the ring, the paste, the ASR gate and the
> Job Object still holds. Three things do not.
>
> **There is no hardcoded key.** D2's numpad `*` is now the shipped DEFAULT of a
> configurable table (`daemon/shortcuts.py`, `desk/actions.py`), and its
> ARGUMENT is what survived: suppression stops a key producing its character
> system-wide, so only a key almost nothing needs may be bound with no modifier.
> `docs/shortcuts.md` 4.3 corrects the obvious generalisation of that -- the
> numpad DIGITS are not bare-safe, because they arrive only with NumLock on.
>
> **`POST /api/terminal` takes a path from the caller again**, which reverses
> D3's OVERRULED box below. That box is kept whole: its analysis is still why
> the pane offers directories rather than a free-for-all, and its record of a
> picker that shipped broken is still the standing argument against desktop UI
> in the desk agent. What changed is that the owner asked for a pane of project
> rows with a chord each, and a route that cannot be told which row was pressed
> cannot serve one. `terminal_cwd` is RETIRED and migrated into a row.
>
> **Section 9 step 5's "the bindings as schema settings" is false.** A binding is
> a row in a store, not a `Setting`: `kind="list"` is a flat list of strings and
> cannot carry an id, a label or a chord. AC-121 -- hotkeys are a setting and OFF
> is the default -- is unaffected and still true of `hotkeys_enabled`.

## 1. The one thing this does not depend on

Every other Butler feature reaching the owner today goes through the brain.
On 2026-09-08 the brain was the whole failure: `brain_mode` is `harness_only`,
so three consecutive utterances went to pi on `zen_bridge/big-pickle` and came
back `Connection error.` with zero tokens, twice in 1.4 s and once not at all.

Dictation touches NONE of that. It needs the `Asr` port and nothing else --
no router, no conversation model, no coding agent, no provider, no network
past localhost. The recognizer is the one engine in this repository with a
measured, working, local path.

That is the argument for building this before anything else in Phase 5: it is
the first Butler feature whose usefulness does not depend on a third party
being up.

## 2. What already exists (READ FROM CODE, 2026-09-08)

Five findings decide the design. Each is a file and a line, not a memory.

**The daemon has no microphone, and never has.** `ports.Source`
(`daemon/ports.py:53`) is "one stream of microphone audio from one place -- a
WebSocket satellite, the desk mic (Phase 5), or a WAV file in a test". Only
the WebSocket satellite is built. `requirements.txt` names `aiohttp`, `numpy`,
`transcribe-cpp` and `onnxruntime` and no audio device library at all. So
today the browser page owns the microphone and the daemon owns the
recognizer, and a hotkey on the server has neither.

**`Asr` already has the exact shape this needs.** `transcribe(pcm, cfg) -> str`
(`daemon/ports.py:149`) takes 16 kHz mono int16 for a clip whose boundaries
are already decided -- which is precisely what a key-down to key-up capture
is. It exists because ambient needed it, and this is its second caller with no
change to the port.

**Raw PCM over HTTP has a precedent.** `POST /api/ambient`
(`daemon/butlerd.py:179`) reads a raw int16 body off `request.content` in
64 KB chunks against a ceiling derived from the setting's own maximum, rather
than through `Request.read()`, so aiohttp's `client_max_size` is neither
raised for every other route nor relied on for this one. A dictation route is
the same shape and should be built by copying it, not by inventing a second
one.

**Two decodes on one model destroy both, silently.** MEASURED 2026-08-19,
`test/manual/spike_ambient_concurrent.py`: two `Session`s on one
`transcribe_cpp.Model` return EMPTY transcripts on BOTH sides, 9 times out of
9, with no exception, no `Final.failed` and nothing on stderr. This is why
ambient is queued after the final rather than run beside it
(`daemon/AGENTS.md`). Dictation is a THIRD caller of the same model and
inherits the same rule. Ignoring it produces a defect that only appears when
the owner dictates while a turn is live, returns empty on both sides, and
reports success.

**Spawning through `cmd` already has a refusal list, paid for.**
`daemon/adapters/AGENTS.md`: pi is `pi.cmd` and `CreateProcess` does not read
PATHEXT, so `harness_pi` spawns through `cmd /c` -- which makes the spawn a
command LINE that cmd re-parses, so every argument built in `_argv` is refused
if it holds `" % & | < > ^`, loudly and by name. The recorded reason is a
model value of `claude" & echo PWNED & rem ` that ran a second command. A
directory path chosen in a folder picker and handed to a `cmd` line is the
same hazard through the same door.

Amended 2026-09-09, when step 4 shipped: the built spawn passes the directory
to `CreateProcess` as `cwd` and never onto a command line, so the refusal is
now a SECOND gate rather than the only one. It is kept, and kept as the same
helper, because `cmd /k pi` is still a command line and one edit away from
carrying the path again.

Also verified on this machine, 2026-09-08: `pi` resolves to
`C:\Users\user\AppData\Roaming\npm\pi.cmd` and reports version `0.65.0`;
`wt.exe` is present at `C:\Users\user\AppData\Local\Microsoft\WindowsApps`.
`daemon/childproc.py:39` sets the precedent for Win32 by ctypes: "ctypes on
purpose: no new dependency for four Win32 calls."

## 3. What the ask is NOT

`plan.md` already carries **AC-26 (push-to-talk)**: "A global hotkey opens a
capture without the wake word, from any foreground app." That is a different
feature and must not be collapsed into this one. AC-26 starts a Butler TURN --
the audio reaches the brain and Butler answers out loud. Dictation starts a
TRANSCRIPTION -- the audio reaches the recognizer, the text goes to the
owner's caret, and Butler never speaks.

They share the capture and share nothing else. Building dictation first makes
AC-26 cheap later (same hook, same capture, different sink); building AC-26
first buys dictation nothing, because the expensive half of dictation is the
sink.

## 4. Four decisions

**D1. The desk agent is a SEPARATE PROCESS, spawned and owned by butlerd.**
[DECIDED]

A low-level Windows keyboard hook needs its own message pump and WASAPI
capture blocks, and `daemon/AGENTS.md` is explicit: "Nothing blocking runs on
the event loop. This process is single-threaded; a blocking engine call
freezes every satellite, the heartbeat and the worker announcements." A hook
is also not an engine, so it is not a port and does not belong behind one.

The pattern already exists one seam over: the kokoro TTS sidecar is a separate
process the daemon spawns, assigns to the Job Object, talks to over local
HTTP, and stops in `_restart_cleanup` before `os.execv`. The desk agent copies
it, with the arrow reversed -- the sidecar is called BY the daemon, the desk
agent CALLS the daemon.

It owns: the keyboard hook, the microphone, the clipboard, `SendInput`, the
cue beeps. It owns no model and, since the owner's 2026-09-09 ruling, it draws
no user interface at all. It reads its bindings from
`GET /api/settings`, which is how it stays out of the settings store
(`daemon/AGENTS.md`: no adapter reads the settings store, and this is not even
an adapter).

**D2. The key is numpad `*` (`VK_MULTIPLY`, 0x6A), and the hook SUPPRESSES
it.** [DECIDED]

Suppression is not optional. A held key auto-repeats, so without it the first
thing a dictation does is type `*******************` into the document the
owner was about to dictate into. Suppressing means the key stops producing its
character system-wide while hotkeys are enabled -- which is exactly why numpad
`*` and not Shift+8: numpad `*` is a key almost nothing needs, and the ordinary
`*` above the 8 keeps working.

**D3. The pi terminal's directory comes from the `projects` list, with a
Browse row.** [DECIDED by the owner, 2026-09-08]

Three shapes were offered and the first was chosen:

- *(a) The `projects` list.* `settings.projects` already exists (AC-31): a
  curated list of directories, exact-match, refused aloud when a spoken name
  misses, and described in the schema as "directories you can send the coding
  agent to by voice". A small always-on-top list of those, arrow keys and
  Enter. Cheapest, reuses the one list that already bounds where a
  full-capability agent may run, and is useless on the day the owner wants a
  directory that is not on it.
- *(b) A native folder picker* seeded at `harness_cwd`. Reaches anywhere,
  costs a browse every time, and puts an arbitrary path into a `cmd` line --
  see section 2's refusal list.
- *(c) The folder of the active Explorer window*, with no prompt at all.
  Fastest when it works, silent and wrong when the foreground window is not
  Explorer.

The ruling is (a) with (b) as the last row of the same list ("Browse..."), so
the common case is two keystrokes and the uncommon one is still reachable.
That is one more control than (a) alone and one less guess than (c).

Consequence worth stating, because it is a widening: `projects` was written to
bound where a full-capability agent may run, and its schema help says exactly
that. A Browse row reaches directories that list does not name. It is
defensible here and not elsewhere -- this action opens a terminal the owner is
sitting in front of and typing into, where the spoken path (AC-31) hands a
directory to an agent nobody is watching. If a future action dispatches work
rather than opening a window, it takes the list without the Browse row.

> **OVERRULED BY THE OWNER, 2026-09-09, after living with it for one commit.**
> "I don't want to see an ugly tkinter interface. If we need python, it should
> be running on server/background and options/paths etc should be on our webui
> settings page." There is NO PICKER. The directory is `terminal_cwd`, a
> setting in the `listen` group whose choices are the `projects` list
> (`choices_from`, the mechanism `harness_model` already uses), blank meaning
> `harness_cwd` and then the TMP scratch dir. The chord opens a terminal
> immediately and asks nothing.
>
> The whole of section 4's D3 above is kept because its analysis of (a), (b)
> and (c) is still the reason the SETTING offers the projects list rather than
> a free path, and the widening paragraph is why the Browse row is gone rather
> than moved to the page: there is now no way for the hotkey to reach a
> directory that is not on the list or in `harness_cwd`.
>
> Three things fall out and two are improvements. The route no longer takes a
> path from the caller at all, which is strictly stronger than the allow-list
> it replaced -- an allow-list is only as good as the list, and this removes
> the input. The desk agent draws nothing, so it cannot take the foreground,
> which AC-114's second half needs and which a modal picker measurably did
> take (it owned the foreground 3 runs of 3). And the cost, stated plainly:
> changing project is now editing a setting on the page rather than two
> keystrokes at the moment of use.
>
> The picker also shipped BROKEN, which is worth recording because it is the
> argument for not having desktop UI in this process at all. `root.focus_force()`
> put keyboard focus on the toplevel, so `<Return>` bound on the listbox never
> fired: Escape worked, double-click worked, and Enter and the arrow keys did
> nothing. The window looked completely correct. Nothing in pytest could see
> it, and it was found only by driving a real window with injected keys.

**D4. Failure NEVER writes.** [DECIDED]

If the daemon is down, the decode is empty, the clip is over the ceiling, or
the recognizer fails, the desk agent changes no clipboard and sends no paste,
and says so with a distinct two-tone beep. The alternative -- pasting an error
sentence into whatever the owner had focused -- puts Butler's words inside the
owner's document, which is `D-20` (the record holds what the OWNER said) at
the wrong end of the pipe and is not undoable by anything Butler controls.

**D5. Capture is a solved problem and gets no spike.** [DECIDED by the owner,
2026-09-08]

`plan.md` 8.5 asks sounddevice/PortAudio vs a WASAPI-specific alternative and
sets the evidence bar at "a spike that runs 24 h at 16 k mono without drift,
device-change survival, and CPU cost". The owner ruled that bar does not apply
here. It was written for AC-24's ALWAYS-ON native source, and dictation opens
the device for seconds and closes it, so drift over hours is not a failure this
feature can have. 8.5 stays open for AC-24; this feature does not wait on it.

## 5. Actions are a closed set, not a scripting surface

Two named actions ship: `dictate` and `open_pi_terminal`. Each has one binding
in the settings schema and one function in the desk agent, registered the same
way ports are. A third action is a function plus a registry line; it is not a
config file that can name a command, because a binding table that can spawn an
arbitrary command line is a different security object than a binding table
that can pick between two written behaviours, and nothing in the ask needs the
first one.

## 6. The dictation action, end to end

1. Hook sees `VK_MULTIPLY` down, no modifiers, not already capturing. It
   suppresses the key and returns immediately -- a hook that does work on the
   callback stalls every keystroke on the machine.
2. The capture thread takes the clip from a PRE-ROLL RING it was already
   filling, at 16 kHz mono int16 (`ports.SAMPLE_RATE_IN`), and beeps 880 Hz /
   120 ms -- the same "listening" cue `plan.md` Appendix A already names.
   **The ring is not an optimisation; opening the device on key-down is a
   measured defect.** This step said "opens the default input device" until
   2026-09-09, which is exactly Handy's design and exactly Handy's worst bug:
   time to first captured sample went 25-76 ms to 380-416 ms to 488-606 ms
   across three of their releases, 1.4 s on Windows 11, with 6 of about 20
   recordings capturing nothing at all
   (`.references/push-to-talk-prior-art.md` section 1). Their only mitigation
   is an always-on microphone toggle they keep off by default. Butler already
   holds pre-roll rings for the wake word and for ambient, so the shape is
   familiar here; the key-down instant is an INDEX into the ring, not the
   moment capture starts.
   The cue is played BLOCKING and only once the device reports ready. A cue
   that sounds before capture is live tells the owner it is safe to speak when
   it is not, which is Handy's #1879 in one sentence.
3. Hook sees the key up. Capture stops. If the clip is shorter than
   `min_speech_ms` (200, already a setting) it is discarded silently: that is a
   mistouch, not a dictation.
4. `POST /api/dictate` with the raw body. The daemon answers `{"text": ...}`.
5. On non-empty text: set `CF_UNICODETEXT`, then `SendInput` the paste
   chord, then beep 660 then 988 Hz -- the existing "submitted" cue.
6. On anything else: D4.

Two properties the implementation must keep.

**Focus is never taken.** The hook does not create a window and the capture
does not either, so the foreground window at key-up is the window the owner
clicked, and the caret is where they left it. That is the whole of "pastes it
to where the mouse cursor is clicked" -- the click already did the hard part,
and the feature's job is to not undo it. Any progress window, tray balloon or
dialog during capture breaks this and must not be added.

**The paste chord is a setting with four values**, because Ctrl+V is not
universal: `ctrl_v` (default), `shift_insert` (what most terminals want),
`type` (SendInput the characters, for the apps that refuse both), and
`clipboard_only` (paste nothing, leave the text on the clipboard). The owner
asked for clipboard AND paste; `clipboard_only` exists so the answer to "it
pasted into the wrong app" is a setting rather than a bug report.

**The chord is not sent while the owner's own modifiers are down.** Handy
does not check, and it is a live hazard for a push-to-talk app rather than a
theoretical one: if the hotkey contains a modifier and the key is still
physically down when the transcript lands, the target sees `Ctrl+Alt+V`
instead of `Ctrl+V` (their #2051). Their only defence is timing -- a 50 ms
release grace plus a 60 ms paste delay -- which is a race rather than a rule.
Poll `GetAsyncKeyState` for VK_CONTROL, VK_SHIFT, VK_MENU and VK_LWIN, wait on
a bounded budget, then send explicit key-ups if the budget expires.
Also from source: use RAW VK CODES for the chord (`Key::Other(0x56)` is VK_V),
never characters, so a Russian, AZERTY or Dvorak layout cannot break paste.

**There is deliberately NO paste-delay setting.** Handy has one
(`paste_delay_ms`, 60, adjustable to 5000) and their issue #502 -- open, 82
comments, marked critical, all three platforms -- is the wrong clipboard
contents being pasted under load, where one reporter walked the delay through
ten values from 50 to 190 ms and reproduced at every one. The maintainer's own
conclusion is that a delay is not the mechanism. If a race shows up here, the
fix is a MECHANISM rather than a knob -- and it has to be designed here,
because Handy has not solved it either. A read of their SOURCE on 2026-09-09
found no delayed-render clipboard anywhere; an earlier web-sourced claim that
one shipped behind a beta flag was simply wrong
(`.references/push-to-talk-prior-art.md` section 3). Also from there: send the
chord only once the owner's own modifiers are released, or the target sees
`Ctrl+Alt+V` (Handy #2051).

**The clipboard is NOT restored.** The owner asked for the transcript to be on
it. Saying so here because the opposite is a common courtesy in this class of
tool and would silently defeat half the ask.

### The daemon's half

`POST /api/dictate`, built by copying `post_ambient` (`daemon/butlerd.py:179`):
a raw 16 kHz mono int16 body read in chunks against
`DICTATE_MAX_BYTES = dictate_max_seconds * SAMPLE_RATE_IN * 2`, 413 past it.

It differs from the ambient route in exactly one way and the difference is the
point: ambient is fire-and-forget and answers 202 because everything it can
lose is context. Dictation is the whole product, so it answers the TEXT, and
every failure is a status the caller can tell apart -- 409 while the
recognizer is busy, 503 when it is unreachable, 422 on an empty decode.

**Serialisation is the hard requirement (section 2).** One asyncio lock owned
by the daemon guards every use of the ASR model -- the turn pipeline's
`open`/`feed`/`finalize`, ambient's `transcribe`, and this route's
`transcribe`. Dictation does not WAIT on that lock: a key released with
nothing appearing, then text arriving four seconds later into whatever is
focused by then, is worse than a refusal. It answers 409 at once and the desk
agent beeps the failure. Waiting is the tempting implementation and it is the
wrong one.

Cost, MEASURED at the ceiling on 2026-09-09 through the PORT rather than the
engine (`test/manual/bench_dictate.py`): 5 s costs 0.33 s, 30 s costs 1.93 s,
60 s costs 4.40 s and **120 s costs 10.51 s**. The extrapolation this
paragraph used to carry said "roughly 8 s" and was wrong in the optimistic
direction, because the ratio is NOT flat -- it degrades from 15.2x real time
at 5 s to 11.4x at 120 s. The transcript grows with the audio (95, 507, 980,
1965 characters), which is the guard against the failure this project has hit
twice: a timing that looks fast because the decoder returned nothing.

## 7. The pi terminal action

1. Hook sees Ctrl + `VK_MULTIPLY`.
2. The directory is `terminal_cwd` from the settings, then `harness_cwd`,
   then the TMP scratch dir. Nobody is asked: see the OVERRULED box in D3.
3. The path is checked against the refusal list from
   `daemon/adapters/AGENTS.md` -- `" % & | < > ^` -- and a path holding any of
   them is refused by name rather than escaped. Reuse the existing helper; do
   not write a second one.
4. Spawn `wt.exe -d <cwd> cmd /k pi`, DETACHED.

> **THIS LINE IS WHAT SHIPS, and it took a detour to get back to it.**
> Step 4 first shipped `cmd /k pi` with `CREATE_NEW_CONSOLE`, on the reasoning
> that a `wt.exe` spawned from Python exits (measured: `poll()` returned 0 at
> 0.5 s in one run) so the pid in the route's answer would be a corpse and
> AC-119 could not be asserted by it.
>
> **The owner reported the result: "a ghost frame terminal, plus another
> cmd".** MEASURED 2026-09-09 by spawning from the LIVE daemon and enumerating
> processes and visible windows:
>
> | | `CREATE_NEW_CONSOLE` | `wt.exe -d <cwd> cmd /k pi` |
> |---|---|---|
> | processes | `cmd.exe` + a `conhost.exe` of its own | `cmd.exe` under `WindowsTerminal.exe` |
> | pi running? | **NO `node.exe` 16 s later** | `node.exe` under the cmd |
> | window title | generic `'Terminal'` | `'pi - TMP'`, titled by pi |
>
> So the console path opened an empty frame with a bare cmd in it and never
> started the coding agent. `wt.exe` is what ships.
>
> The pid objection was real and is answered by not answering it: the route
> returns `{"cwd"}` and no pid, because `wt.exe` is a launcher whose own pid
> was measured exiting inside 0.5 s in one run and alive at 1 s in another,
> depending on whether an existing Terminal adopted the command. Anything that
> needs to name the terminal finds it by a unique marker in its command line.
> Two costs, both paid in code: AC-120's refusal is load-bearing again because
> the directory goes onto wt's command line, and the route must check the
> directory EXISTS itself, since `Popen(cwd=missing)` used to raise and give a
> 502 for free while `wt -d missing` opens a window and complains inside it.
> Also decided here: the two-decodes-on-one-model rule does not apply, because
> the terminal action touches no recognizer at all.

**Detached is a contract, not a detail.** `daemon/childproc.py` puts every
child in a kill-on-close Job Object so the kernel kills the tree when the
daemon dies, however it died (AC-36, D-24). That is right for a kokoro sidecar
and wrong for a terminal the owner is typing in: `POST /api/restart` lands
between turns and would close their session mid-command. This one child is
deliberately outside the job, and the reason belongs in a comment beside the
spawn, because the next reader's correct instinct will be to assign it.

**Which is why the DAEMON spawns and the desk agent asks.** Found while
planning, and it is what forced `POST /api/terminal` into existence:
`daemon/childproc.py:110-111` sets `LimitFlags =
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and nothing else, so
`CREATE_BREAKAWAY_FROM_JOB` from inside the job fails with
ERROR_ACCESS_DENIED. The desk agent is in the job; a terminal it spawned would
die with the daemon. The daemon is in NO job -- `childproc` assigns children
and never the daemon -- so a child the daemon spawns without `assign` outlives
it, and `childproc.py` needed no change.

This action starts pi INTERACTIVELY and has nothing to do with
`adapters/harness_pi.py`, which runs `pi --mode rpc` as a managed child whose
every argument is fixed at spawn. Two ways to start the same binary for two
different jobs; neither should grow a branch for the other.

## 8. Acceptance criteria

Each says which way it fails and whether that is recoverable -- the AC-92
lesson from `docs/satellite-ux.md` 11.1, where a criterion whose own spec said
"the mechanism was measured; the human was not" was promoted to Tier 1 and
made the wake word deaf for five minutes.

- **AC-113 (dictation round trip).** With hotkeys on, the owner clicks into a
  text field in any application, holds the dictate key, speaks, releases, and
  the transcript appears at the caret. Fails as: nothing appears. Recoverable.
- **AC-114 (the key never types, and focus never moves).** While hotkeys are
  enabled the dictate key produces no character in any application, and the
  window focused at key-up is the window focused at key-down. Fails as: a row
  of asterisks in the owner's document, or the paste landing in the wrong
  window. Recoverable but visible, and the reason D2 suppresses.
- **AC-115 (a failed dictation writes nothing).** Daemon down, empty decode,
  over the ceiling, or recognizer busy: the clipboard is unchanged, no paste is
  sent, and a failure cue sounds. Fails as: an error sentence pasted into the
  owner's work. NOT recoverable by Butler -- this is the one criterion here
  whose failure the owner has to clean up by hand.
- **AC-116 (dictation never decodes beside a turn).** A dictation attempted
  while a turn is capturing or decoding is refused with 409 and never runs
  concurrently. The instrument is a mutate-and-restore in the D-13 style:
  remove the lock, prove both transcripts come back empty, restore. Fails as:
  a turn AND a dictation that both silently return nothing, intermittently.
  Recoverable, and undiagnosable without the instrument, which is why the
  instrument is part of the criterion.
- **AC-117 (the clipboard carries what was pasted).** After a successful
  dictation the clipboard holds exactly the pasted text. Fails as: the two
  disagree. Recoverable.
- **AC-118 (the pi terminal).** Ctrl plus the dictate key opens a visible
  terminal whose working directory is the CONFIGURED one (`terminal_cwd`, then
  `harness_cwd`, then the scratch dir) and in which pi is running and
  accepting input. Nothing is asked at the time and nothing is drawn by the
  desk agent. Fails as: no window, or a window in the wrong directory.
  Recoverable. Amended 2026-09-09 from "the chosen one": the owner overruled
  the picker, so there is no choosing at the moment of use.
- **AC-119 (that terminal outlives the daemon).** `POST /api/restart`, and a
  daemon kill, leave it running. Fails as: the owner's session closes under
  them. Recoverable but destructive of unsaved work.
- **AC-120 (a path cmd would re-parse is refused).** A directory whose name
  holds `" % & | < > ^` is refused by name and no process is spawned. Fails as:
  a second command running from a folder name. NOT recoverable.
- **AC-121 (hotkeys are settings, and OFF is the default).** The bindings, the
  paste chord and the ceiling are schema settings, so they render on the
  existing settings page with no page change (AC-41: nothing in `web/` names a
  setting). Shipping off means a fresh clone suppresses no keys on anyone's
  machine. Fails as: a global key suppressed on a machine whose owner did not
  ask. Recoverable.
- **AC-123 (what is pasted cannot execute a command).** The dictation route's
  answer carries no carriage return, line feed or other C0 control, and no
  DEL. Whitespace controls become a SPACE and everything else in that range is
  deleted. Fails as: a dictated sentence containing a line feed, pasted into a
  terminal that already holds a half-typed command, RUNS it. NOT recoverable
  -- this and AC-115 are the two criteria here whose failure the owner cleans
  up by hand, and this one may have already deleted the thing they would have
  cleaned up with.
  Found in an earlier prototype's tree on 2026-09-09 (`scripts/core/terminal-dictation.js`
  `toTerminalInput`), which paid for the rule first. The two halves are
  different on purpose: deleting a newline WELDS the words either side, so
  "git status
ls" becomes "git statusls" -- a command nobody dictated -- and
  an ESC that survives is the start of an ANSI escape sequence.
- **AC-122 (the desk agent dies with the daemon).** It is spawned at boot,
  assigned to the Job Object, and stopped in `_restart_cleanup` before
  `os.execv` -- the leak the kokoro sidecar already taught (AC-36, D-24). Fails
  as: one orphaned process holding a global keyboard hook per restart, which is
  the worst orphan in this repository because it eats keystrokes. Recoverable
  only from Task Manager.

## 9. Build order

Five steps, each ending at something observable. No spikes: the owner ruled
capture settled (D5) and the two open questions in section 10 are answered by
building rather than by measuring first.

1. **`POST /api/dictate` in the daemon, with the ASR gate.** **DONE
   2026-09-09, AC-113/AC-115/AC-116 met.** `Hub._decoding` (an
   `asyncio.Lock`) guards every whole-clip decode; `Hub._recognizer_in_use()`
   asks the PIPELINES, never the session states; the route answers 409 rather
   than waiting, and 422/502 rather than an empty or apologetic 200.
   Session state was tried first and was wrong in both directions, which is
   the record worth keeping. Too COARSE -- nothing decodes through `thinking`
   or `speaking`, so refusing there stopped dictation for the minutes a
   `harness_only` turn spends thinking, which is the wait section 1 says
   dictation exists to survive. Too NARROW -- `asyncio.to_thread` cannot be
   cancelled, so an abandoned turn's `finalize` keeps decoding after the
   session reads IDLE. `TurnPipeline.uses_recognizer()` answers both, and
   only the worker thread may clear the mark.
   Verified: `test_dictate.py` 10 passed; `test/manual/mutate_dictate.py`
   **4/4** rules caught (aimed exit 1, rest exit 0, restore exit 0); full
   fast tier `548 passed, 53 deselected in 72.29s`, exit 0.
   The mutation harness earned its keep twice: it caught that the naive fix
   -- clearing the mark in the coroutine's `finally` -- survived my first
   test, because that test drove `finalize` on its own task instead of the
   run loop the way production does.
   **The one window left open**: a turn that BEGINS during a decode is not
   blocked, because closing it means making a wake word wait on a dictation
   and drop the audio it was woken by.
   **Found beside it, not fixed, for the owner**: a completed turn LEAKS its
   engine session (`_rearm` drops the pipeline without `close()`, so every
   turn parks a worker thread holding a `model.session()` on the shared
   `Model`), and whether N idle sessions contend is untested --
   `spike_ambient_concurrent.py` measured two RUNNING ones.

2. **The desk agent, spawned and owned by butlerd.** **DONE 2026-09-09,
   AC-121 and AC-122 met.** `desk/deskd.py` is the process and
   `daemon/desk.py` the supervisor; `hotkeys_enabled` ships False and OFF
   means NOT SPAWNED rather than spawned-and-idle. `make_app` grew a
   `local_url` argument that only `main` fills in, so `make_app({})` -- every
   test -- can never install a system-wide keyboard hook on the machine
   running the suite. Job Object on spawn, `desk.stop()` in
   `_restart_cleanup` before the exec.
   Verified: `test_desk.py` 7 passed, and the AC-122 case is validated (D-13,
   exit codes only) -- removing `childproc.assign` reddened exactly that test
   at exit 1 with the rest of the file green, restore exit 0.
   **One thing this step learned that changes step 3.**
   `.venv/Scripts/python.exe` is a 238 KB uv TRAMPOLINE that re-spawns the
   real interpreter, so every child of this venv is two processes deep and
   `desk.pid()` names the launcher rather than the process that will hold the
   hook [MEASURED 2026-09-09]. The Job Object covers both, so AC-122 is
   unaffected; anything in a later step that needs to signal the agent itself
   has to walk the tree rather than trust that PID.

3. **Hook, capture, clipboard, paste.** **DONE 2026-09-09, AC-113 met and
   PROVED with a real voice on the owner's desk.** `desk/hotkeys.py` (the `WH_KEYBOARD_LL`
   hook, suppression, the injection marker, the input-desktop check),
   `desk/audio.py` (the always-open stream and the lookback ring) and
   `desk/paste.py` (clipboard, modifier wait, the chord). `deskd.Agent` is
   the state machine.
   Verified: `test_desk_dictation.py` 18 passed, `test_desk.py` 7 passed,
   full fast tier `582 passed, 55 deselected in 68.06s`, exit 0.
   `probe_dictation.py` drives the whole chain -- real hook, injected key,
   fixture audio at the callback seam, real parakeet, real clipboard -- and
   returns the fixture's GROUND TRUTH exactly, 5.6 s of audio in a 0.3 s round
   trip. `--paste` confirms the chord reaches a real window. And the owner
   held the key and spoke into the real microphone on 2026-09-09: it works.
   **Two defects that only a LIVE daemon could find, and both were silent.**
   `Daemon.poll` read a `values` dict `/api/settings` has never served, so
   `hotkeys_enabled` was permanently False and the agent would have done
   nothing forever -- survived the whole suite because every double overrides
   `get`, and survived the probe because that sets `_values` directly (D-18:
   the caller was the one thing never exercised). And the supervisor spawned
   the agent with stdout on DEVNULL, discarding every line it writes, for a
   feature whose commonest failure is silence.
   **The finding that came out of hitting it.** A hook installed on a session
   with no input desktop SUCCEEDS and then receives nothing, forever, with no
   error -- which is the shape of Handy's largest bug cluster.
   `hotkeys.input_desktop_reachable()` now names it and `Agent._start` refuses
   to open anything without it (AC-7b).
   **One deliberate reduction.** `dictate_paste` ships THREE values, not the
   four section 6 proposed: `ctrl_v`, `shift_insert`, `clipboard_only`. The
   fourth, `type`, is a fourth injection mechanism with its own failure modes
   and nothing has asked for it; `clipboard_only` is already the honest answer
   for an application that refuses both chords.

4. **The pi terminal action.** **DONE 2026-09-09, AC-118, AC-119 and AC-120
   met.** `POST /api/terminal` in the daemon, the `terminal_cwd` setting on
   the page, and `Agent._open_terminal` out here. The daemon spawns because
   only the daemon can produce a child that outlives it (see section 7 for the
   Job Object finding and for the `wt.exe` design this replaced).
   **The route takes NO PATH from the caller** (owner's ruling; see the box in
   section 4's D3). It reads `terminal_cwd`, then `harness_cwd`, then the TMP
   scratch dir. A `cwd` in the body is IGNORED, and that is the assertion with
   teeth in `test_terminal_route.py` -- sending the RIGHT directory is the row
   that catches a route which reads the body, because every other row would
   pass by coincidence. This replaced an allow-list on a caller-supplied path,
   and it is strictly stronger: an allow-list is only as good as the list.
   `harness_spec_resolver` therefore keeps its own `projects` check inline --
   the spoken switch still hands over a path that came off a microphone.
   Verified: `test_terminal_route.py` 20 passed, `test_desk_dictation.py` 28
   passed, `test_desk.py` 8 passed, full fast tier
   `618 passed, 55 deselected in 79.66s`, exit 0.
   `probe_terminal.py` drives the whole chain -- real hook, injected Ctrl plus
   numpad `*`, real route, real spawn -- and all four verdicts came back True,
   including a body-supplied directory being ignored and the terminal
   surviving a hard-killed daemon.
   D-13 EXECUTED twice, and the second run is the interesting one. Against
   the console spawn, adding the `childproc.assign` that every other spawn in
   this repository makes reddened both tests naming AC-119 (exit 1 each) with
   the rest of `test_desk.py` green. Against the `wt.exe` spawn the SAME
   mutation leaves the end-to-end lifetime test GREEN (aimed exit 0), because
   Windows Terminal owns the shell and it is not a descendant of anything
   Butler holds -- the lifetime became structural. The unit-level
   `test_the_terminal_is_deliberately_NOT_put_in_the_job_object` still reddens
   at exit 1 and is now the only witness for the rule; both test files say so
   rather than implying coverage they lost.
   **A defect this step also fixed.** `Agent._handle` branched the terminal
   action on `edge == DOWN` and held no state, so every repeat of a held chord
   fired it: `butler.log` since the restart carried 138 repeats against 4 real
   dictations. The latch is keyed on the KEY and not the action, because the
   hook classifies each edge independently by polling Ctrl at that instant --
   so one press-and-release can arrive as `("terminal", down)` then
   `("dictate", up)`. That same quirk had a second half nobody had noticed: a
   release labelled `terminal` did not end a dictation capture, so touching
   Ctrl before letting go lost the whole clip and left the microphone filling
   to the ceiling. A release now ends whatever the press started.
   **One thing this step could not test and says so.** A process's working
   directory lives in its PEB and cannot be read from outside without walking
   it, so "the terminal is in the directory the owner picked" is asserted
   through a negative control -- a directory that does not exist answers 502,
   because `Popen(cwd=...)` raises -- rather than by reading it back.
5. **Settings and DOX.** The bindings, paste chord and ceiling as schema
   settings, default OFF (AC-121); `desk/AGENTS.md`; `daemon/AGENTS.md` for the
   route and the lock; AC-113..AC-123 into `plan.md` on the owner's acceptance.

## 10. What this spec could have wrong

Four, written down now so a later session can check them rather than
rediscover them.

**Elevated windows. VERIFIED 2026-09-09, and worse than this said.** Not
merely "the paste fails": Handy #434 has been open since 2025-12-11 and the
app FREEZES when a high-integrity window is focused, including when the user
switches into one mid-recording. UIPI is the cause -- a medium-integrity
process cannot send input to a high-integrity window. With push-to-talk off it
degrades instead: the clipboard write succeeds and the paste silently does
not. An elevated console often works anyway, because the window belongs to
conhost. The only real routes out are running elevated or a signed
`uiAccess="true"` manifest under Program Files, which Handy floated and never
shipped (`.references/push-to-talk-prior-art.md` section 4). Treat this as a
designed-for limit that the UI states, not as a bug to find later.

**Numpad `*` may already be taken.** No inventory of this machine's existing
global hotkeys was made. If something else owns it the symptom is a hook that
installs cleanly and never fires.

**`SendInput` paste is a race in slow apps.** Setting the clipboard and
immediately sending Ctrl+V can beat the target application's clipboard read.
The usual fix is a short delay, which is a guess wearing a uniform until it is
measured against the slowest app the owner actually uses.

**The microphone may be contended.** The browser page holds the microphone for
the wake word whenever a satellite is open, and a second reader in the desk
agent was never tested. RULED NOT A GATE by the owner (D5): capture is
working, so this is found by building rather than by spiking. Left written down
because the failure mode -- one of the two silently getting no audio -- is the
same shape as the 9-of-9 empty transcript in section 2, so if dictation ever
returns nothing while a satellite is open, read this paragraph before anything
else.
