/**
 * Generate the repo-owned speech fixtures in test/assets/.
 *
 * WHY THIS EXISTS
 *   Every inherited test pointed at C:\PROJECTS\other-project\...\jfk.wav, a file
 *   that does not exist on this machine, so the suite could not run at all.
 *   Phase 1 needs deterministic audio with known ground truth that lives IN the
 *   repo. These fixtures are small enough to commit.
 *
 * HOW
 *   Speech is synthesized with the project's own Kokoro TTS (the real
 *   daemon/engines/tts/TTSService child-process pool, same path bench_tts.mjs
 *   exercises), which emits 24 kHz mono PCM16. The ASR pipeline consumes
 *   16 kHz mono PCM16, so every clip is resampled 24000 -> 16000 with a
 *   windowed-sinc (Blackman) polyphase-style filter -- NOT by dropping every
 *   third sample, which would fold everything above 8 kHz back into the band
 *   and quietly degrade transcription accuracy. A fixture that lies about
 *   audio quality is a broken instrument.
 *
 * USAGE (from the repo root)
 *   node scripts/make-fixtures.mjs            # regenerate everything
 *   node scripts/make-fixtures.mjs --verify   # only re-read + check the WAVs
 *
 * ASCII-only output on purpose (Windows console).
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { TTSService } from '../daemon/engines/tts/tts-service.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.join(REPO_ROOT, 'test', 'assets');

const VOICE = process.env.KOKORO_VOICE || 'af_heart';
const TARGET_RATE = 16000;

/** Silence padded onto the head and tail of every synthesized clip. */
const PAD_MS = 100;
/** The gap inside wake_then_command. Load-bearing for AC-1a. */
const GAP_MS = 150;

// ---------------------------------------------------------------------------
// resampling
// ---------------------------------------------------------------------------

/**
 * Windowed-sinc resampler, arbitrary rate ratio, mono float input.
 *
 * output[n] = sum_k x[k] * g(tau - k),  tau = n * (inRate / outRate)
 * with g(t) = 2*fc*sinc(2*fc*t) * blackman(t), fc in cycles per INPUT sample.
 *
 * For 24k -> 16k the anti-alias cutoff must sit at the OUTPUT Nyquist (8 kHz),
 * i.e. fc = 8000/24000 = 1/3 of the input rate. HALF_WIDTH input samples per
 * side with a Blackman window puts the stopband far below the noise floor of
 * 16-bit audio, which is all this needs.
 *
 * @param {Float64Array} x
 * @param {number} inRate
 * @param {number} outRate
 * @returns {Float64Array}
 */
function resample(x, inRate, outRate) {
  if (inRate === outRate) return Float64Array.from(x);
  const HALF_WIDTH = 32;                       // input samples each side
  const fc = 0.5 * Math.min(1, outRate / inRate);   // cycles per input sample
  const step = inRate / outRate;
  const outLen = Math.floor(x.length / step);
  const out = new Float64Array(outLen);

  const sinc = (t) => (t === 0 ? 1 : Math.sin(Math.PI * t) / (Math.PI * t));
  // Blackman window over [-HALF_WIDTH, +HALF_WIDTH] in input samples.
  const win = (t) => {
    const a = (t + HALF_WIDTH) / (2 * HALF_WIDTH);   // 0..1
    return 0.42 - 0.5 * Math.cos(2 * Math.PI * a) + 0.08 * Math.cos(4 * Math.PI * a);
  };

  for (let n = 0; n < outLen; n++) {
    const tau = n * step;
    const kMin = Math.ceil(tau - HALF_WIDTH);
    const kMax = Math.floor(tau + HALF_WIDTH);
    let acc = 0;
    let norm = 0;
    for (let k = kMin; k <= kMax; k++) {
      const t = tau - k;
      const h = 2 * fc * sinc(2 * fc * t) * win(t);
      norm += h;
      if (k < 0 || k >= x.length) continue;     // zero outside the signal
      acc += x[k] * h;
    }
    // Normalise by the kernel sum so DC gain is exactly 1 regardless of where
    // the fractional phase lands; without it the output ripples at 8 kHz/3.
    out[n] = norm !== 0 ? acc / norm : acc;
  }
  return out;
}

// ---------------------------------------------------------------------------
// PCM helpers
// ---------------------------------------------------------------------------

/** @param {Buffer} buf raw PCM16 LE @returns {Float64Array} in [-1, 1) */
function pcm16ToFloat(buf) {
  const n = buf.length >> 1;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = buf.readInt16LE(i * 2) / 32768;
  return out;
}

/** @param {Float64Array} x @returns {Buffer} PCM16 LE, clipped */
function floatToPcm16(x) {
  const buf = Buffer.alloc(x.length * 2);
  for (let i = 0; i < x.length; i++) {
    let v = Math.round(x[i] * 32767);
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    buf.writeInt16LE(v, i * 2);
  }
  return buf;
}

/**
 * Trim leading/trailing near-silence using 10 ms frame RMS. Kokoro pads its
 * output with a variable amount of room tone; leaving it in would make the
 * wake_then_command gap a lie (the manifest offsets have to be exact).
 * @param {Float64Array} x @param {number} rate
 */
function trimSilence(x, rate) {
  const frame = Math.round(rate * 0.01);
  const thresh = 0.004;                          // ~ -48 dBFS RMS
  const loud = [];
  for (let i = 0; i + frame <= x.length; i += frame) {
    let s = 0;
    for (let j = 0; j < frame; j++) s += x[i + j] * x[i + j];
    loud.push(Math.sqrt(s / frame) > thresh);
  }
  let first = loud.indexOf(true);
  let last = loud.lastIndexOf(true);
  if (first === -1) return Float64Array.from(x);  // all silence: leave alone
  const a = Math.max(0, first * frame);
  const b = Math.min(x.length, (last + 1) * frame);
  return x.slice(a, b);
}

/** @param {number} ms @param {number} rate @returns {Float64Array} */
function silence(ms, rate) {
  return new Float64Array(Math.round((ms / 1000) * rate));
}

/** @param {Float64Array[]} parts */
function concat(parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Float64Array(total);
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}

/** Canonical 44-byte-header mono PCM16 WAV. @param {Buffer} pcm */
function wavFromPcm16(pcm, sampleRate) {
  const buf = Buffer.alloc(44 + pcm.length);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + pcm.length, 4);
  buf.write('WAVE', 8);
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);                 // PCM
  buf.writeUInt16LE(1, 22);                 // mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28);    // byte rate
  buf.writeUInt16LE(2, 32);                 // block align
  buf.writeUInt16LE(16, 34);                // bits
  buf.write('data', 36);
  buf.writeUInt32LE(pcm.length, 40);
  pcm.copy(buf, 44);
  return buf;
}

/** Read back what we wrote. Never trust the writer. @param {string} file */
function readWavHeader(file) {
  const b = fs.readFileSync(file);
  if (b.toString('ascii', 0, 4) !== 'RIFF' || b.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${file}: not a RIFF/WAVE file`);
  }
  const fmtSize = b.readUInt32LE(16);
  const dataSize = b.readUInt32LE(40);
  return {
    audio_format: b.readUInt16LE(20),
    channels: b.readUInt16LE(22),
    sample_rate: b.readUInt32LE(24),
    byte_rate: b.readUInt32LE(28),
    block_align: b.readUInt16LE(32),
    bits_per_sample: b.readUInt16LE(34),
    fmt_size: fmtSize,
    data_bytes: dataSize,
    num_samples: dataSize / 2,
    file_bytes: b.length,
    duration_ms: (dataSize / 2 / b.readUInt32LE(24)) * 1000,
  };
}

// ---------------------------------------------------------------------------
// synthesis
// ---------------------------------------------------------------------------

/**
 * Synthesize one utterance and hand back 16 kHz float samples, silence-trimmed.
 * @param {TTSService} svc @param {string} text
 */
async function speak16k(svc, text) {
  /** @type {Buffer[]} */
  const chunks = [];
  let rate = 0;
  for await (const c of svc.synthesizeStream(text, { voice: VOICE })) {
    chunks.push(c.audio);
    rate = c.sampleRate;
  }
  if (!chunks.length) throw new Error(`kokoro produced no audio for: ${text}`);
  const raw = pcm16ToFloat(Buffer.concat(chunks));
  const at16k = resample(raw, rate, TARGET_RATE);
  const trimmed = trimSilence(at16k, TARGET_RATE);
  console.log(`[fixtures] synth "${text.slice(0, 46)}..." -> ${rate} Hz `
    + `${raw.length} samp -> 16k ${at16k.length} samp -> trimmed ${trimmed.length} samp`);
  return trimmed;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const SHORT_Q = 'What time is it';
const WAKE = 'Hey Jarvis';
const COMMAND = 'delete the old logs in project butler';
const LONG = 'Talk me through whether Butler should use a message bus or '
  + 'direct calls between its components';
const TWO_SENT = 'The tests are passing. I pushed the branch.';

/**
 * What the project's OWN ASR actually returned for these clips, measured
 * 2026-08-16 with daemon/engines/stt_engine.py, slug
 * parakeet-unified-en-0.6b, backend auto (Vulkan), latency preset "low".
 *
 * This is recorded because it is NOT identical to the ground truth: parakeet
 * re-cases proper nouns ("Project Butler"), inserts a comma after the wake
 * phrase, and on this voice mishears one or two function words. A test that
 * asserts exact string equality against `transcript` will therefore fail for
 * reasons that have nothing to do with the daemon. Assert on `transcript` only
 * after normalisation (casefold + strip punctuation), or assert substrings.
 *
 * Informational only -- nothing regenerates this map, so re-measure it if the
 * fixtures or the model change.
 */
const ASR_OBSERVED = {
  'short_question.wav': 'What time is it',
  'wake_then_command.wav': 'Hey Jarvis, delete the old logs in Project Butler',
  'silence_2s.wav': '',
  'long_utterance.wav': 'Talk me through whether Butler should use a message bus '
    + 'or a direct calls between its components',
  'two_sentences.wav': 'The tests are passing. I push the branch',
};

async function build() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const svc = new TTSService();
  console.log('[fixtures] starting kokoro (fp32, CPU) ...');
  const t0 = Date.now();
  await svc.start(VOICE);
  console.log(`[fixtures] kokoro ready in ${Date.now() - t0} ms, voice=${svc.currentVoice}`);

  /** @type {any[]} */
  const entries = [];
  const pad = silence(PAD_MS, TARGET_RATE);

  /** Write one fixture and record its manifest entry. */
  const emit = (filename, transcript, samples, extra = {}) => {
    const pcm = floatToPcm16(samples);
    const file = path.join(OUT_DIR, filename);
    fs.writeFileSync(file, wavFromPcm16(pcm, TARGET_RATE));
    entries.push({
      filename,
      transcript,
      sample_rate: TARGET_RATE,
      num_samples: samples.length,
      duration_ms: Math.round((samples.length / TARGET_RATE) * 1000),
      ...extra,
      asr_observed_parakeet: ASR_OBSERVED[filename],
    });
    console.log(`[fixtures] wrote ${filename} (${samples.length} samples)`);
  };

  // 1 -- short_question
  emit('short_question.wav', SHORT_Q,
    concat([pad, await speak16k(svc, SHORT_Q), pad]));

  // 2 -- wake_then_command: wake, an EXACT 150 ms gap, then the command.
  //      The offsets below are what a test uses to say "wake fired here", so
  //      they are computed from the real part lengths, not assumed.
  const wake = await speak16k(svc, WAKE);
  const command = await speak16k(svc, COMMAND);
  const gap = silence(GAP_MS, TARGET_RATE);
  const wakeEnd = pad.length + wake.length;
  emit('wake_then_command.wav', `${WAKE} ${COMMAND}`,
    concat([pad, wake, gap, command, pad]), {
      wake_end_sample: wakeEnd,
      command_start_sample: wakeEnd + gap.length,
      wake_transcript: WAKE,
      command_transcript: COMMAND,
      gap_ms: GAP_MS,
      lead_pad_ms: PAD_MS,
    });

  // 3 -- silence_2s: no TTS, exactly 2.000 s of digital zero.
  emit('silence_2s.wav', '', silence(2000, TARGET_RATE));

  // 4 -- long_utterance
  emit('long_utterance.wav', LONG,
    concat([pad, await speak16k(svc, LONG), pad]));

  // 5 -- two_sentences
  emit('two_sentences.wav', TWO_SENT,
    concat([pad, await speak16k(svc, TWO_SENT), pad]));

  svc.shutdown();

  const manifest = {
    generated_by: 'scripts/make-fixtures.mjs',
    tts: { engine: 'kokoro-js via daemon/engines/tts/tts-service.js', voice: VOICE, native_rate: 24000 },
    resample: '24000 -> 16000, windowed-sinc (Blackman, 32-sample half-width, cutoff 8 kHz)',
    format: { sample_rate: TARGET_RATE, channels: 1, bits_per_sample: 16, encoding: 'PCM16 LE' },
    asr_observed_note: 'asr_observed_parakeet is what daemon/engines/stt_engine.py '
      + '(parakeet-unified-en-0.6b, backend auto, latency low) actually returned on '
      + '2026-08-16. It differs from transcript in casing/punctuation and a couple of '
      + 'function words; normalise before asserting equality against transcript.',
    fixtures: entries,
  };
  fs.writeFileSync(path.join(OUT_DIR, 'manifest.json'),
    JSON.stringify(manifest, null, 2) + '\n');
  console.log('[fixtures] wrote manifest.json');
  return manifest;
}

/** Re-open every WAV and assert the header really says 16 kHz mono PCM16. */
function verify() {
  const manifest = JSON.parse(fs.readFileSync(path.join(OUT_DIR, 'manifest.json'), 'utf8'));
  let bad = 0;
  for (const f of manifest.fixtures) {
    const h = readWavHeader(path.join(OUT_DIR, f.filename));
    const ok = h.audio_format === 1 && h.channels === 1
      && h.sample_rate === 16000 && h.bits_per_sample === 16
      && h.num_samples === f.num_samples;
    if (!ok) bad++;
    console.log(`[verify] ${ok ? 'OK  ' : 'FAIL'} ${f.filename.padEnd(24)} `
      + `fmt=${h.audio_format} ch=${h.channels} rate=${h.sample_rate} bits=${h.bits_per_sample} `
      + `samples=${h.num_samples} dur=${h.duration_ms.toFixed(1)}ms bytes=${h.file_bytes}`);
  }
  if (bad) {
    console.error(`[verify] ${bad} fixture(s) FAILED header verification`);
    process.exit(1);
  }
  console.log('[verify] all fixtures are 16000 Hz mono PCM16');
}

async function main() {
  if (!process.argv.includes('--verify')) await build();
  verify();
  process.exit(0);
}

main().catch((err) => {
  console.error('[fixtures] FAILED:', err && err.stack ? err.stack : err);
  process.exit(1);
});
