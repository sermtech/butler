/**
 * TTS worker entry point (isolated child_process).
 * Owns a single kokoro-js instance and runs synthesis in its own OS process.
 *
 * Why a child_process and not a worker_threads worker: kokoro runs ONNX through a
 * native addon. When the parent's watchdog has to kill a worker stuck inside a hung
 * native generate(), terminating a worker_thread mid-native-call crashes the whole
 * Node process (Windows 0xC0000409 STATUS_STACK_BUFFER_OVERRUN). A child_process can
 * be SIGKILLed cleanly by the OS without touching the parent. The IPC channel is
 * created by child_process.fork(); audio travels as Buffers over advanced (v8
 * structured-clone) serialization, so no transferList is needed.
 *
 * Protocol (IPC messages):
 *   in:  { type: "start",            id, voice?, dtype, threads }
 *        { type: "listVoices",       id }
 *        { type: "synthesize",       id, text, opts: { voice?, speed? } }
 *        { type: "synthesizeStream", id, text, opts: { voice?, speed? } }
 *        { type: "abort",            id }
 *
 *   out: { type: "started",   id, voice, voices }
 *        { type: "voices",    id, voices }
 *        { type: "result",    id, audio: Buffer, sampleRate, duration_ms }
 *        { type: "chunk",     id, audio: Buffer, sampleRate, sentenceIndex, text, isFinal }
 *        { type: "streamEnd", id }
 *        { type: "aborted",   id }
 *        { type: "error",     id, message }
 *
 * The worker serializes calls internally (kokoro's ONNX session is single-threaded).
 * Concurrency comes from multiple workers in the pool, not multiple calls per worker.
 */

import { loadKokoroModel } from './tts-model-loader.js';
import { languageOf, phonemize } from './tts-phonemes.js';
import { streamSentences, markCancelled, routeMessage } from './tts-sentence-stream.js';

if (typeof process.send !== 'function') {
  throw new Error('tts-worker.js must be launched as a forked child_process (no IPC channel)');
}

// If the parent dies or closes the IPC channel, don't linger as an orphan.
process.on('disconnect', () => process.exit(0));

/**
 * Send an IPC message to the parent. The channel can be torn down between the
 * native generate() returning and this call (parent killed the worker, or it is
 * shutting down); swallow EPIPE/ERR_IPC_CHANNEL_CLOSED so a late reply never
 * crashes the child.
 * @param {any} message
 */
function sendToParent(message) {
  try {
    process.send(message);
  } catch {
    /* channel closed; parent is gone */
  }
}

/** @type {any} */
let tts = null;
let currentVoice = 'af_heart';
/** @type {string[]} */
let voiceList = [];
/** dtype + intra-op thread budget delivered by the parent on the 'start' message. */
let configuredDtype = null;
let configuredThreads = null;

async function ensureLoaded() {
  if (tts) return;
  // Fail fast: the parent must set dtype + threads via a 'start' message before
  // any synthesis. A missing value is a protocol violation, never a silent default.
  if (!configuredDtype || !configuredThreads) {
    throw new Error('TTS worker: ensureLoaded() called before a "start" message set dtype/threads');
  }
  const loaded = await loadKokoroModel({ dtype: configuredDtype, intraOpNumThreads: configuredThreads });
  tts = loaded.tts;
  voiceList = loaded.voiceList;
}

/**
 * Encode Float32Array audio samples as raw little-endian PCM16 in an ArrayBuffer.
 * Produces an owned ArrayBuffer suitable for transferList.
 * @param {Float32Array} f32
 * @returns {ArrayBuffer}
 */
function float32ToPcm16ArrayBuffer(f32) {
  const buf = new ArrayBuffer(f32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < f32.length; i++) {
    let s = f32[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    view.setInt16(i * 2, Math.round(s * 0x7fff), true);
  }
  return buf;
}

/**
 * Encode Float32Array audio samples as a complete WAV file in an ArrayBuffer.
 * @param {Float32Array} f32
 * @param {number} sampleRate
 * @returns {ArrayBuffer}
 */
function float32ToWavArrayBuffer(f32, sampleRate) {
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const dataSize = f32.length * 2;
  const headerSize = 44;
  const buffer = new ArrayBuffer(headerSize + dataSize);
  const view = new DataView(buffer);

  // RIFF header ("RIFF")
  view.setUint8(0, 0x52); view.setUint8(1, 0x49); view.setUint8(2, 0x46); view.setUint8(3, 0x46);
  view.setUint32(4, 36 + dataSize, true);
  // "WAVE"
  view.setUint8(8, 0x57); view.setUint8(9, 0x41); view.setUint8(10, 0x56); view.setUint8(11, 0x45);
  // "fmt "
  view.setUint8(12, 0x66); view.setUint8(13, 0x6d); view.setUint8(14, 0x74); view.setUint8(15, 0x20);
  view.setUint32(16, 16, true);           // fmt chunk size
  view.setUint16(20, 1, true);            // PCM format
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  // "data"
  view.setUint8(36, 0x64); view.setUint8(37, 0x61); view.setUint8(38, 0x74); view.setUint8(39, 0x61);
  view.setUint32(40, dataSize, true);

  for (let i = 0; i < f32.length; i++) {
    let s = f32[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    view.setInt16(headerSize + i * 2, Math.round(s * 0x7fff), true);
  }
  return buffer;
}

/**
 * Synthesize one piece of text, in whatever language the voice speaks.
 *
 * TWO PATHS, and the split is deliberate rather than lazy (AC-159).
 *
 * A voice kokoro-js NAMES goes through its own `generate()`, untouched. That
 * path carries a large English text normalizer -- numbers, times, currency,
 * "Dr." to "Doctor", "1990s" to "nineteen nineties" -- which is not
 * reimplemented here and would be silently lost by routing English through
 * the generic path. What works today keeps working byte for byte.
 *
 * A voice it REFUSES goes through the public `generate_from_ids`, which does
 * not call `_validate_voice`, with phonemes from the full eSpeak NG build.
 * That is the only way to reach Kokoro's Portuguese: the model has the
 * voices, the wrapper will not name them, and its bundled phonemizer has
 * English data only (tts-phonemes.js carries the measurements).
 *
 * The two paths meet at a RawAudio, so everything downstream is unchanged.
 *
 * @param {string} text
 * @param {{ voice: string, speed?: number }} opts
 */
async function generate(text, opts) {
  if (Object.hasOwn(tts.voices || {}, opts.voice)) {
    return tts.generate(text, opts);
  }
  const language = languageOf(opts.voice);
  if (!language) {
    // Never guess a language from an unknown name. Guessing wrong produces
    // audio of the right length and the wrong sounds, which no automated
    // check catches and only a listener notices.
    throw new Error(
      `TTS worker: no voice named "${opts.voice}", and this build cannot `
      + `infer a language for it`);
  }
  const phonemes = await phonemize(text, language);
  const { input_ids } = tts.tokenizer(phonemes, { truncation: true });
  return tts.generate_from_ids(input_ids, opts);
}

/**
 * Handle a single "synthesize" (non-streaming) call.
 * @param {{ id: number, text: string, opts?: { voice?: string, speed?: number } }} msg
 */
async function handleSynthesize(msg) {
  await ensureLoaded();
  const opts = { voice: (msg.opts && msg.opts.voice) || currentVoice };
  if (msg.opts && typeof msg.opts.speed === 'number') opts.speed = msg.opts.speed;

  const result = await generate(msg.text, opts);
  const sampleRate = result.sampling_rate;
  const f32 = result.audio;
  const wavBuf = float32ToWavArrayBuffer(f32, sampleRate);
  const duration_ms = Math.round((f32.length / sampleRate) * 1000);

  sendToParent({ type: 'result', id: msg.id, audio: Buffer.from(wavBuf), sampleRate, duration_ms });
}

/**
 * Handle a streaming synthesis call. Emits per-sentence PCM16 chunks.
 *
 * Uses tts.generate() per sentence instead of tts.stream(TextSplitterStream).
 * TextSplitterStream._process() stalls waiting for sentence-ending punctuation
 * that may never arrive (mid-sentence segments, all-caps acronyms like ONNX/ATAM),
 * causing the for-await loop to hang indefinitely (upstream kokoro-js PR #296).
 * tts.generate() is a single bounded async call with no internal iterator state.
 *
 * @param {{ id: number, text: string, opts?: { voice?: string, speed?: number } }} msg
 */
async function handleSynthesizeStream(msg) {
  await ensureLoaded();
  const opts = { voice: (msg.opts && msg.opts.voice) || currentVoice };
  if (msg.opts && typeof msg.opts.speed === 'number') opts.speed = msg.opts.speed;

  // The loop, the sentence splitting and the stop-when-cancelled rule all live
  // in lib/tts-sentence-stream.js, where they can be tested: this file refuses
  // to load without an IPC channel, so nothing in it can be.
  await streamSentences({
    id: msg.id,
    text: msg.text,
    generate: (sentence) => generate(sentence, opts),
    emit: (chunk) => sendToParent({
      type: 'chunk',
      id: msg.id,
      audio: Buffer.from(float32ToPcm16ArrayBuffer(chunk.audio)),
      sampleRate: chunk.sampleRate,
      sentenceIndex: chunk.sentenceIndex,
      text: chunk.text,
      isFinal: chunk.isFinal,
    }),
  });
  // Sent even when the call was cancelled: the parent has already dropped its
  // pending entry by then, so this is ignored -- but a worker that went quiet
  // instead would leave any future waiter hanging.
  sendToParent({ type: 'streamEnd', id: msg.id });
}

/**
 * Worker calls are serialized in FIFO order. kokoro's ONNX session is single-threaded
 * so overlapping calls on one worker would just block anyway. Serializing here gives
 * deterministic ordering and keeps the error handling trivial.
 */
let workerBusy = Promise.resolve();

/** @param {any} msg */
function enqueue(msg) {
  workerBusy = workerBusy.then(async () => {
    try {
      if (msg.type === 'start') {
        // dtype + intra-op thread budget are resolved by the parent (tts-service)
        // and delivered here; ensureLoaded() fails fast if either is missing.
        configuredDtype = msg.dtype;
        configuredThreads = msg.threads;
        await ensureLoaded();
        if (msg.voice && voiceList.some((v) => v.id === msg.voice)) {
          currentVoice = msg.voice;
        }
        // Pre-warm ONNX session with a trivial synth so the first real call is fast.
        try {
          await generate(' ', { voice: currentVoice });
        } catch (warmErr) {
          console.error('[TTS Worker] Pre-warm failed (non-fatal):', warmErr && warmErr.message);
        }
        sendToParent({ type: 'started', id: msg.id, voice: currentVoice, voices: voiceList });
      } else if (msg.type === 'listVoices') {
        await ensureLoaded();
        sendToParent({ type: 'voices', id: msg.id, voices: voiceList });
      } else if (msg.type === 'synthesize') {
        await handleSynthesize(msg);
      } else if (msg.type === 'synthesizeStream') {
        await handleSynthesizeStream(msg);
      } else {
        sendToParent({ type: 'error', id: msg.id, message: `Unknown message type: ${msg.type}` });
      }
    } catch (err) {
      sendToParent({ type: 'error', id: msg.id, message: (err && err.message) || String(err) });
    }
  });
}

process.on('message', (msg) => {
  if (!msg || typeof msg !== 'object' || typeof msg.id !== 'number') {
    console.error('[TTS Worker] Ignoring malformed message:', msg);
    return;
  }
  // An abort is handled HERE rather than in the FIFO queue. Its entire purpose
  // is to affect the call that is running right now, and the queue would hand it
  // over only once that call had finished -- a cancellation that cancels
  // nothing. Marking is synchronous and cheap; the sentence loop reads it
  // between sentences, which it reaches because generate() awaits.
  if (routeMessage(msg.type) === 'immediate') {
    markCancelled(msg.id);
    sendToParent({ type: 'aborted', id: msg.id });
    return;
  }
  enqueue(msg);
});
