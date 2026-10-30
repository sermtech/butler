// AudioWorklet: downsample the mic (ctx sample rate, e.g. 48k) to 16 kHz mono
// Int16 PCM and post ~32 ms frames to the main thread. Linear interpolation is
// plenty for speech; transcribe.cpp wants exactly 16 kHz mono.
class DownsampleProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // input samples per output sample
    this.pos = 0;                    // fractional read cursor into `tail`
    this.tail = new Float32Array(0); // carry-over input samples
    this.out = [];                   // accumulated 16k float samples
    this.FRAME = 512;                // ~32 ms at 16 kHz
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    // Concatenate carry-over + this block.
    const buf = new Float32Array(this.tail.length + ch.length);
    buf.set(this.tail, 0);
    buf.set(ch, this.tail.length);

    // Resample from `pos` while a full interpolation window exists.
    let pos = this.pos;
    while (pos + 1 < buf.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      this.out.push(buf[i] * (1 - frac) + buf[i + 1] * frac);
      pos += this.ratio;
    }
    // Keep the unconsumed tail; reset cursor to its fractional part.
    const consumed = Math.floor(pos);
    this.tail = buf.slice(consumed);
    this.pos = pos - consumed;

    // Emit whole frames.
    while (this.out.length >= this.FRAME) {
      const frame = this.out.splice(0, this.FRAME);
      const pcm = new Int16Array(this.FRAME);
      let sum = 0;
      for (let k = 0; k < this.FRAME; k++) {
        let s = Math.max(-1, Math.min(1, frame[k]));
        sum += s * s;
        pcm[k] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      const rms = Math.sqrt(sum / this.FRAME);
      this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("downsample-processor", DownsampleProcessor);
