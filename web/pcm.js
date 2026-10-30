/**
 * Pure PCM helpers for the TTS playback path.
 *
 * The daemon streams raw PCM16 LE. The playback AudioContext is created at the
 * TTS sample rate (24 kHz), so normally samples map 1:1 to output frames and no
 * resampling is needed. `resampleLinear` is a safety net for the rare browser
 * that refuses the requested AudioContext sample rate (then ctx.sampleRate
 * differs and the host resamples on the main thread before posting).
 *
 * Plain JavaScript with JSDoc types on purpose: the web directory is served
 * directly by the daemon with no bundler, exactly as the spike ran it.
 */

/**
 * Decode raw little-endian PCM16 bytes into a Float32Array in [-1, 1].
 * Non-destructive: reads the buffer through a view, never detaches it, so the
 * same shared window-event buffer can be read by multiple consumers.
 *
 * @param {ArrayBuffer} buffer
 * @returns {Float32Array}
 */
export function pcm16ToFloat32(buffer) {
  const view = new DataView(buffer);
  const sampleCount = Math.floor(buffer.byteLength / 2);
  const out = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    out[i] = view.getInt16(i * 2, true) / 0x8000;
  }
  return out;
}

/**
 * Linear-interpolation resample. Returns the input unchanged when the rates
 * match (the common case, since we open the context at 24 kHz). Fail-fast on
 * nonsensical rates rather than producing garbage audio.
 *
 * @param {Float32Array} input
 * @param {number} fromRate
 * @param {number} toRate
 * @returns {Float32Array}
 */
export function resampleLinear(input, fromRate, toRate) {
  if (!(fromRate > 0) || !(toRate > 0)) {
    throw new Error(`resampleLinear: invalid rates from=${fromRate} to=${toRate}`);
  }
  if (fromRate === toRate || input.length === 0) return input;
  const ratio = toRate / fromRate;
  const outLength = Math.max(1, Math.round(input.length * ratio));
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcPos = i / ratio;
    const i0 = Math.floor(srcPos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = srcPos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

/**
 * Pure mirror of the drain loop inside `tts-playback.worklet.js`. The worklet
 * cannot import modules (AudioWorklet classic-script scope), so this executable
 * copy is the unit-tested spec for that algorithm. KEEP THE TWO IN SYNC:
 * identical queue and underrun semantics. Same golden-port discipline used for
 * the Smart Turn detector.
 *
 * Drains `frames` samples out of a chunk queue; fills silence on underrun.
 *
 * @param {Float32Array[]} queue
 * @param {number} readOffset
 * @param {number} frames
 * @returns {{ output: Float32Array, readOffset: number, producedAudio: boolean }}
 */
export function drainSampleQueue(queue, readOffset, frames) {
  const output = new Float32Array(frames);
  let i = 0;
  let offset = readOffset;
  while (i < frames && queue.length > 0) {
    const head = queue[0];
    const avail = head.length - offset;
    const need = frames - i;
    const n = avail < need ? avail : need;
    output.set(head.subarray(offset, offset + n), i);
    offset += n;
    i += n;
    if (offset >= head.length) {
      queue.shift();
      offset = 0;
    }
  }
  // remainder stays 0 (silence)
  return { output, readOffset: offset, producedAudio: i > 0 };
}
