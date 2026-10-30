// AudioWorklet processor for TTS PLAYBACK (Option B, 2026-07-23).
//
// Replaces the old main-thread `source.start(nextStartTime)` per-chunk audio-
// clock scheduling, which produced non-deterministic gaps/overlaps: on any
// producer stall the clock had to be reset and a late chunk could be dropped.
// This worklet owns a simple sample queue and drains it at the hardware rate.
// A producer stall yields graceful SILENCE that self-heals the instant more
// samples arrive -- no clock math, no dropped chunk, no permanent gap.
//
// The host (useTtsPlayback.ts) creates the AudioContext at the TTS sample rate
// (24 kHz for kokoro), so incoming samples map 1:1 to output frames and no
// resampling happens here. If the browser refuses that rate, the host
// resamples on the main thread BEFORE posting (worklet stays 1:1, dead simple).
//
// Messages in:
//   { type: 'write', samples: ArrayBuffer }  - Float32 PCM, appended to the queue
//   { type: 'clear' }                        - drop the queue (stop / barge-in)
//   { type: 'drain', token }                 - one-shot: answer with 'drained'
//                                              once every queued sample has been
//                                              PLAYED, not merely received
// Messages out:
//   { type: 'state', playing: boolean }      - emitted only on a transition, so
//                                              the host can drive its isPlaying UI
//                                              (true when audio is flowing, false
//                                              when the queue has fully drained).
//   { type: 'drained', token }               - answers the matching 'drain'
//
// Why 'drain' exists and 'state' is not enough: postMessage is asynchronous, so
// at the instant the host sees speak_end the writes it just posted may not have
// reached this processor yet -- `playing` would still read false and the host
// would call the speaker quiet while the reply had not started. The port
// delivers messages in order, so a 'drain' posted after the last 'write' is
// necessarily handled after that write is queued, which makes the answer exact.
// The remaining error is one render quantum plus the device's own output
// latency (a few ms); the host's 1300 ms wake cooldown covers that tail.

class TtsPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    /** @type {Float32Array[]} queued chunks, oldest first */
    this._queue = [];
    this._readOffset = 0; // read cursor into _queue[0]
    this._playing = false;
    /** @type {number|null} token of an outstanding 'drain' request */
    this._drainToken = null;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (!msg) return;
      if (msg.type === 'write' && msg.samples) {
        this._queue.push(new Float32Array(msg.samples));
      } else if (msg.type === 'clear') {
        this._queue = [];
        this._readOffset = 0;
        // A dropped queue will never be played, so an outstanding drain must
        // never be answered: reporting it would claim audio was heard that was
        // thrown away (barge-in).
        this._drainToken = null;
        if (this._playing) {
          this._playing = false;
          this.port.postMessage({ type: 'state', playing: false });
        }
      } else if (msg.type === 'drain') {
        this._drainToken = msg.token == null ? 0 : msg.token;
        this._answerDrain();   // the queue may already be empty
      }
    };
  }

  _answerDrain() {
    if (this._drainToken === null || this._queue.length > 0) return;
    const token = this._drainToken;
    this._drainToken = null;
    this.port.postMessage({ type: 'drained', token });
  }

  process(_inputs, outputs) {
    const out = outputs[0];
    if (!out || out.length === 0) return true;
    const chan = out[0];
    const frames = chan.length;

    let i = 0;
    while (i < frames && this._queue.length > 0) {
      const head = this._queue[0];
      const avail = head.length - this._readOffset;
      const need = frames - i;
      const n = avail < need ? avail : need;
      chan.set(head.subarray(this._readOffset, this._readOffset + n), i);
      this._readOffset += n;
      i += n;
      if (this._readOffset >= head.length) {
        this._queue.shift();
        this._readOffset = 0;
      }
    }
    // Underrun: fill the rest of this block with silence (self-healing).
    if (i < frames) chan.fill(0, i);
    // Mirror to any additional output channels.
    for (let c = 1; c < out.length; c++) out[c].set(chan);

    // State transitions: "playing" == we produced real audio this block.
    const producedAudio = i > 0;
    if (producedAudio && !this._playing) {
      this._playing = true;
      this.port.postMessage({ type: 'state', playing: true });
    } else if (!producedAudio && this._playing && this._queue.length === 0) {
      this._playing = false;
      this.port.postMessage({ type: 'state', playing: false });
    }

    // Answered only after a block that consumed the queue to the end, so the
    // samples are in the output buffer rather than still waiting in it.
    this._answerDrain();
    return true; // keep the node alive; it outputs silence when idle
  }
}

registerProcessor('tts-playback-processor', TtsPlaybackProcessor);
