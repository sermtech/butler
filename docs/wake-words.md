# Wake words: where a turn starts, and which words wake Butler

Status: section 1 BUILT 2026-10-29 (AC-800..AC-802); section 2 BUILT
2026-10-29 (AC-803..AC-809). Owner request, 2026-10-29: "hey jarvis" on the
iPhone over Tailscale serve to the Mac mini "starts and stops right away and
never answers"; and "hey butler" beside "hey jarvis", each with its own
setting, defaulting to Butler.

## 1. A turn starts where the wake word fired (AC-800..AC-802)

### Evidence [MEASURED, Mac mini `voice-trace.log`, 2026-10-29]

Four iPhone wakes, all the same shape:

```
capture_start turn=2 device=71025253
speech_start  turn=2 level=0.296          (+71 ms)
speech_end    turn=2 speech_ms=416
turn_end      turn=2 reason=silence elapsed_ms=263 silence_ms=640
final         turn=2 reason=silence chars=0
```

`speech_ms=416` is exactly the 13 x 32 ms lookback ring: the tail of the
wake phrase. The energy gate latched voicing on it and counted the quiet
after the phrase as the end of the request. Silence is counted in AUDIO time,
so the network delay is not the variable; the gap between the end of the
phrase and the owner's first word is. On the iPhone that gap passed 600 ms
(detector lag, then the listening cue, which the owner waits for); on the
desk it does not. Reviewed independently (Fable subagent, 2026-10-29):
the cue, the ambient ring and resampling were ruled out.

### Rule

- The page freezes `lookback` (the ring snapshot's byte count) at the fire
  and sends it in the `preroll` header beside `bytes` (`plan.md` 4.3).
- The pipeline latches voicing, and anchors the smart-turn onset, only on a
  frame after `lookback` (`TurnPipeline.note_lookback`). The recognizer is
  still fed every byte, so a command spoken without a pause, which lands in
  the frames buffered after the fire, is transcribed and arms the turn.
- A voiced turn under `min_speech_ms` is still subject to the no-speech
  timer (AC-801): a sliver of "...vis" after the fire is now the normal case
  and used to hold the recognizer to the 120 s cap.
- `lookback` is required and must lie in `0..bytes` (AC-802).

## 2. Two wake words, one setting each (AC-803..AC-809)

### What exists

- `web/models/hey_jarvis_v0.1.onnx` (openWakeWord) and
  `web/models/hey_butler.onnx` (trained in `E:/PROJECTS/prototype` with
  livekit-wakeword, conv_attention medium; its eval: recall 95.3%, 0 false
  wakes in 19.5 h). Both take `[batch, 16, 96]` embeddings and answer one
  score, so they share the melspectrogram and embedding models.

### Rules

- One setting per word in the Listening group: `wake_hey_butler` (default
  `screen`) and `wake_hey_jarvis` (default `off`). Values: `off`, `screen`
  (the conversation the device shows, today's behaviour), `new` (start a new
  conversation in the project of the one on screen, through
  `Hub.new_conversation` and `_tell_device`, the spoken "new conversation"'s
  path). A save that leaves every word off is refused (AC-804).
- `new` is dimmed with a reason in `endpoint` brain mode, where the daemon
  holds no conversations (AC-808).
- The daemon tells the page which words to load, with the threshold and
  cooldown, in `ready` and in a `wake` broadcast when any of them changes
  (`{type:"wake", words:[{id, phrase, url}], threshold, cooldown_ms}`). The
  page names no setting (AC-41) and loads only those words. This also makes
  `wake_threshold` and `wake_cooldown_ms` reach the page; before this,
  nothing read them (AC-805).
- The detector runs every loaded word on each 80 ms chunk; only the highest
  score over the threshold fires, and one shared refractory follows any fire,
  so two words never fire for one utterance (AC-806).
- `wake_candidate` names the `word`. The daemon refuses a word that is off
  (`wake_reject` reason `word_off`) and applies the winner's role after
  arbitration, before the turn is minted (AC-807).
- A `new` birth that fails is `wake_reject` reason `new_failed`: no turn is
  minted, so words meant for a fresh conversation never reach the old one,
  and the page's reject handler re-arms (D-1).
- The page's hints name the words that are on (AC-809).
- Known gap: the all-off refusal covers saves only; a hand-edited
  `settings.json` with both words off loads, and the page then reports
  "Butler sent no usable wake words".

Out of scope: binding a word to one project or agent; per-word thresholds.
