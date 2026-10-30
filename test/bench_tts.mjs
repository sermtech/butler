/**
 * Kokoro CPU TTS benchmark -- the measurement that gates AC-4 (plan.md 5, Phase 1
 * step 0; 7 "Kokoro on CPU is the real AC-4 risk"; 4.7).
 *
 * AC-4 promises wake-word finalize to FIRST TTS AUDIO BYTE at the satellite in
 * under 1500 ms. Kokoro runs CPU-only (tts-model-loader.js hardcodes
 * device: 'cpu'), so first-sentence synthesis time is very likely the largest
 * single term in that budget. This script measures it against the REAL
 * TTSService / tts-worker path, not a re-implementation.
 *
 * What it measures
 *   cold      process launch -> TTSService.start() resolved (model load + ONNX
 *             pre-warm). Run twice: the first run pays the one-time HuggingFace
 *             download, the second is the per-restart load cost.
 *   warm      per sentence length: time from calling synthesizeStream() to the
 *             FIRST chunk, and to the LAST chunk. Reports p50/p95 and realtime
 *             factor (audio seconds produced / synthesis seconds).
 *   http      the shipped tts-sidecar.mjs HTTP path: time to first response
 *             body byte vs time to full body -- proves whether that path
 *             streams or buffers the whole WAV.
 *   cpu       worker-process CPU time sampled during synthesis, expressed as
 *             average cores busy.
 *
 * Usage (from the repo root):
 *   node test/bench_tts.mjs                 # full suite, JSON summary to stdout
 *   node test/bench_tts.mjs --cold          # internal: single cold-start probe
 *   BENCH_RUNS=20 BENCH_WARMUP=3 node test/bench_tts.mjs
 *
 * ASCII-only output on purpose (Windows console).
 */

import { spawn } from 'node:child_process';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';

import { TTSService } from '../daemon/engines/tts/tts-service.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..');
const SIDECAR = path.join(REPO_ROOT, 'daemon', 'engines', 'tts', 'tts-sidecar.mjs');
const TRANSFORMERS_CACHE = path.join(
  REPO_ROOT, 'node_modules', '@huggingface', 'transformers', '.cache',
);

const RUNS = Number(process.env.BENCH_RUNS || 20);
const WARMUP = Number(process.env.BENCH_WARMUP || 3);
const VOICE = process.env.KOKORO_VOICE || 'af_heart';

/**
 * The sentence set. "words" is the plain whitespace token count, "sentences" is
 * what tts-sentence-stream.js splitSentences() will produce -- which is what
 * decides how many chunks come back.
 */
const CASES = [
  {
    key: 'short',
    label: 'short reflex (1 sentence)',
    text: 'It is twenty past four.',
  },
  {
    key: 'medium',
    label: 'medium reply (1 sentence)',
    text: 'The build finished about ten minutes ago, and every test passed '
        + 'except the one that checks the audio queue.',
  },
  {
    key: 'long_single',
    label: 'long reply (1 sentence)',
    text: 'I checked the three repositories you asked about and the only one '
        + 'with uncommitted work is the daemon, where the speech pipeline and '
        + 'the wake word detector both have edits that have not been staged '
        + 'since Thursday morning.',
  },
  {
    key: 'long_multi',
    label: 'long reply (3 sentences)',
    text: 'The deployment finished at four minutes past two. Two of the '
        + 'fourteen integration tests failed, both in the audio queue module. '
        + 'I have left the logs open in the second terminal so you can read '
        + 'them whenever you are ready.',
  },
];

// ---------------------------------------------------------------------------
// stats
// ---------------------------------------------------------------------------

/** Nearest-rank percentile. @param {number[]} xs @param {number} p */
function pct(xs, p) {
  if (xs.length === 0) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const idx = Math.max(0, Math.ceil(p * s.length) - 1);
  return s[idx];
}
/** @param {number[]} xs */
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);
/** @param {number} n */
const r1 = (n) => Math.round(n * 10) / 10;
/** @param {number} n */
const r3 = (n) => Math.round(n * 1000) / 1000;

// ---------------------------------------------------------------------------
// cold start
// ---------------------------------------------------------------------------

/**
 * Cold-start probe. Runs in a FRESH process (spawned by runColdStarts) so the
 * timer covers a real launch: node boot, module graph, ONNX native binding,
 * model fetch/parse and the pre-warm generate(' ') in tts-worker.js.
 */
async function coldProbe() {
  const tProcStart = Date.now() - Math.round(process.uptime() * 1000);
  const svc = new TTSService();
  const t0 = performance.now();
  await svc.start(VOICE);
  const t1 = performance.now();
  const out = {
    start_ms: r1(t1 - t0),
    launch_to_ready_ms: r1(Date.now() - tProcStart),
    voices: svc.listVoices().length,
    voice: svc.currentVoice,
  };
  process.stdout.write(`\nCOLD_JSON ${JSON.stringify(out)}\n`);
  svc.shutdown();
  // Workers are SIGKILLed; give the event loop a tick then leave.
  setTimeout(() => process.exit(0), 200);
}

/** Recursive byte size of a directory, 0 if missing. @param {string} dir */
function dirSize(dir) {
  let total = 0;
  let stack = [dir];
  while (stack.length) {
    const d = stack.pop();
    let entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) stack.push(p);
      else { try { total += fs.statSync(p).size; } catch { /* raced */ } }
    }
  }
  return total;
}

/** Spawn one cold probe in a fresh process. @returns {Promise<any>} */
function spawnColdProbe(tag) {
  return new Promise((resolve, reject) => {
    const t0 = performance.now();
    const child = spawn(process.execPath, [__filename, '--cold'], {
      cwd: REPO_ROOT,
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    child.stdout.on('data', (c) => { out += c; });
    child.stderr.on('data', (c) => { err += c; });
    child.on('exit', (code) => {
      const wall = performance.now() - t0;
      const m = out.match(/COLD_JSON (\{.*\})/);
      if (!m) {
        reject(new Error(`cold probe ${tag} produced no COLD_JSON (exit ${code}).\n`
          + `stdout:\n${out}\nstderr:\n${err}`));
        return;
      }
      const parsed = JSON.parse(m[1]);
      parsed.tag = tag;
      parsed.spawn_to_exit_ms = r1(wall);
      resolve(parsed);
    });
    child.on('error', reject);
  });
}

/**
 * Cold start, measured twice on purpose. Probe 1 downloads the model if the
 * transformers.js cache is empty; probe 2 is a pure disk load. The download
 * cost is the DIFFERENCE, and it is one-time -- only probe 2 is paid on every
 * daemon restart.
 */
async function runColdStarts() {
  const cacheBefore = dirSize(TRANSFORMERS_CACHE);
  const downloadNeeded = cacheBefore === 0;
  console.log(`[bench] transformers cache before: ${(cacheBefore / 1e6).toFixed(1)} MB `
    + `(${downloadNeeded ? 'EMPTY -- probe 1 will download' : 'populated'})`);

  const probe1 = await spawnColdProbe(downloadNeeded ? 'cold+download' : 'cold(cached)');
  const cacheAfter = dirSize(TRANSFORMERS_CACHE);
  console.log(`[bench] cold probe 1: start=${probe1.start_ms} ms, `
    + `launch->ready=${probe1.launch_to_ready_ms} ms, voices=${probe1.voices}`);
  console.log(`[bench] transformers cache after: ${(cacheAfter / 1e6).toFixed(1)} MB`);

  const probe2 = await spawnColdProbe('cold(cached)');
  console.log(`[bench] cold probe 2: start=${probe2.start_ms} ms, `
    + `launch->ready=${probe2.launch_to_ready_ms} ms`);
  const probe3 = await spawnColdProbe('cold(cached)');
  console.log(`[bench] cold probe 3: start=${probe3.start_ms} ms, `
    + `launch->ready=${probe3.launch_to_ready_ms} ms`);

  return {
    cache_bytes_before: cacheBefore,
    cache_bytes_after: cacheAfter,
    download_happened: downloadNeeded,
    download_ms_estimate: downloadNeeded ? r1(probe1.start_ms - mean([probe2.start_ms, probe3.start_ms])) : null,
    probes: [probe1, probe2, probe3],
  };
}

// ---------------------------------------------------------------------------
// CPU sampling (Windows): poll the worker child's TotalProcessorTime
// ---------------------------------------------------------------------------

/**
 * Sample a PID's cumulative CPU time from a SINGLE long-lived PowerShell
 * process (spawning one PowerShell per sample would perturb what we measure).
 * @param {number} pid
 * @param {number} intervalMs
 * @param {number} count
 */
function startCpuSampler(pid, intervalMs, count) {
  const ps = `$ErrorActionPreference='Stop'; $p = Get-Process -Id ${pid};`
    + ` $sw = [Diagnostics.Stopwatch]::StartNew();`
    + ` for ($i=0; $i -lt ${count}; $i++) {`
    + `   $p.Refresh();`
    + `   Write-Output ("SAMPLE " + $sw.Elapsed.TotalMilliseconds + " " + $p.TotalProcessorTime.TotalMilliseconds);`
    + `   Start-Sleep -Milliseconds ${intervalMs} }`;
  const child = spawn('powershell', ['-NoProfile', '-NonInteractive', '-Command', ps],
    { stdio: ['ignore', 'pipe', 'pipe'] });
  /** @type {Array<[number, number]>} */
  const samples = [];
  let buf = '';
  let stderr = '';
  child.stdout.on('data', (c) => {
    buf += c;
    const lines = buf.split(/\r?\n/);
    buf = lines.pop() || '';
    for (const line of lines) {
      const m = line.match(/^SAMPLE ([\d.,]+) ([\d.,]+)/);
      if (m) samples.push([parseFloat(m[1].replace(',', '.')), parseFloat(m[2].replace(',', '.'))]);
    }
  });
  child.stderr.on('data', (c) => { stderr += c; });
  return {
    samples,
    stop: () => new Promise((resolve) => {
      child.on('exit', () => resolve({ samples, stderr }));
      try { child.kill(); } catch { /* already gone */ }
      setTimeout(() => resolve({ samples, stderr }), 1500);
    }),
  };
}

/** Turn cumulative CPU-ms samples into per-interval "cores busy". */
function coresFromSamples(samples) {
  const cores = [];
  for (let i = 1; i < samples.length; i++) {
    const dWall = samples[i][0] - samples[i - 1][0];
    const dCpu = samples[i][1] - samples[i - 1][1];
    if (dWall > 1) cores.push(dCpu / dWall);
  }
  return cores;
}

// ---------------------------------------------------------------------------
// warm streaming benchmark (the real TTSService interface)
// ---------------------------------------------------------------------------

/**
 * One timed synthesizeStream call.
 * @param {TTSService} svc
 * @param {string} text
 */
async function timedStream(svc, text) {
  const t0 = performance.now();
  let tFirst = NaN;
  let firstChunkBytes = 0;
  let firstChunkAudioMs = 0;
  let totalBytes = 0;
  let chunks = 0;
  let sampleRate = 0;
  /** @type {number[]} */
  const chunkTimes = [];
  for await (const chunk of svc.synthesizeStream(text, { voice: VOICE })) {
    const now = performance.now();
    chunks += 1;
    if (chunks === 1) {
      tFirst = now - t0;
      firstChunkBytes = chunk.audio.length;
      firstChunkAudioMs = (chunk.audio.length / 2 / chunk.sampleRate) * 1000;
    }
    chunkTimes.push(r1(now - t0));
    totalBytes += chunk.audio.length;
    sampleRate = chunk.sampleRate;
  }
  const tLast = performance.now() - t0;
  const audioMs = (totalBytes / 2 / (sampleRate || 24000)) * 1000;
  return {
    first_ms: r1(tFirst),
    complete_ms: r1(tLast),
    chunks,
    chunk_times_ms: chunkTimes,
    first_chunk_bytes: firstChunkBytes,
    first_chunk_audio_ms: r1(firstChunkAudioMs),
    total_bytes: totalBytes,
    audio_ms: r1(audioMs),
    sample_rate: sampleRate,
    rtf: r3(audioMs / tLast),
    rtf_first: r3(firstChunkAudioMs / tFirst),
  };
}

/**
 * @param {TTSService} svc
 * @param {{key:string,label:string,text:string}} c
 */
async function benchCase(svc, c) {
  for (let i = 0; i < WARMUP; i++) await timedStream(svc, c.text);   // discarded
  /** @type {any[]} */
  const runs = [];
  for (let i = 0; i < RUNS; i++) runs.push(await timedStream(svc, c.text));

  const first = runs.map((r) => r.first_ms);
  const complete = runs.map((r) => r.complete_ms);
  const rtf = runs.map((r) => r.rtf);
  const s = {
    key: c.key,
    label: c.label,
    text: c.text,
    words: c.text.trim().split(/\s+/).length,
    chars: c.text.length,
    warmup_discarded: WARMUP,
    n: RUNS,
    chunks: runs[0].chunks,
    chunk_times_ms_run0: runs[0].chunk_times_ms,
    audio_ms: runs[0].audio_ms,
    first_chunk_audio_ms: runs[0].first_chunk_audio_ms,
    sample_rate: runs[0].sample_rate,
    first_ms: { min: r1(Math.min(...first)), p50: r1(pct(first, 0.5)), p95: r1(pct(first, 0.95)), max: r1(Math.max(...first)), mean: r1(mean(first)) },
    complete_ms: { min: r1(Math.min(...complete)), p50: r1(pct(complete, 0.5)), p95: r1(pct(complete, 0.95)), max: r1(Math.max(...complete)), mean: r1(mean(complete)) },
    rtf: { p50: r3(pct(rtf, 0.5)), min: r3(Math.min(...rtf)), max: r3(Math.max(...rtf)) },
    raw_first_ms: first,
    raw_complete_ms: complete,
  };
  console.log(`[bench] ${c.key.padEnd(12)} words=${String(s.words).padStart(2)} `
    + `chunks=${s.chunks} audio=${s.audio_ms}ms  `
    + `first p50=${s.first_ms.p50} p95=${s.first_ms.p95}  `
    + `complete p50=${s.complete_ms.p50} p95=${s.complete_ms.p95}  rtf=${s.rtf.p50}`);
  return s;
}

// ---------------------------------------------------------------------------
// HTTP sidecar path
// ---------------------------------------------------------------------------

/** Start tts-sidecar.mjs and wait for its ready line. @param {number} port */
function startSidecar(port) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SIDECAR], {
      cwd: REPO_ROOT,
      env: { ...process.env, SIDECAR_PORT: String(port) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    const onData = (c) => {
      out += c;
      if (out.includes('TTS ready on http')) resolve(child);
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', (c) => { out += c; });
    child.on('exit', (code) => reject(new Error(`sidecar exited early (${code}):\n${out}`)));
    setTimeout(() => reject(new Error(`sidecar did not print ready in 60s:\n${out}`)), 60_000);
  });
}

/**
 * POST /tts and time the first body byte vs the last. If the sidecar streams,
 * ttfb is well below complete; if it buffers a whole WAV they are the same.
 * @param {number} port @param {string} text
 */
function postTts(port, text) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ text });
    const t0 = performance.now();
    const req = http.request({
      host: '127.0.0.1', port, path: '/tts', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, (res) => {
      let tHeaders = performance.now() - t0;
      let tFirstByte = NaN;
      let bytes = 0;
      res.on('data', (c) => {
        if (Number.isNaN(tFirstByte)) tFirstByte = performance.now() - t0;
        bytes += c.length;
      });
      res.on('end', () => resolve({
        status: res.statusCode,
        synth_ms_header: Number(res.headers['x-synth-ms']),
        headers_ms: r1(tHeaders),
        first_byte_ms: r1(tFirstByte),
        complete_ms: r1(performance.now() - t0),
        bytes,
      }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

async function benchHttp() {
  const port = 8149;
  console.log('[bench] starting tts-sidecar.mjs on port', port);
  const child = await startSidecar(port);
  try {
    // First POST also triggers TTSService.start() inside the sidecar (lazy).
    const lazy = await postTts(port, CASES[0].text);
    console.log(`[bench] sidecar first POST (includes lazy model load): ${lazy.complete_ms} ms`);
    for (let i = 0; i < WARMUP; i++) await postTts(port, CASES[0].text);  // discarded
    /** @type {Record<string, any>} */
    const byCase = {};
    for (const c of CASES) {
      const runs = [];
      for (let i = 0; i < Math.min(RUNS, 10); i++) runs.push(await postTts(port, c.text));
      const fb = runs.map((r) => r.first_byte_ms);
      const co = runs.map((r) => r.complete_ms);
      byCase[c.key] = {
        n: runs.length,
        warmup_discarded: WARMUP,
        first_byte_ms: { p50: r1(pct(fb, 0.5)), p95: r1(pct(fb, 0.95)) },
        complete_ms: { p50: r1(pct(co, 0.5)), p95: r1(pct(co, 0.95)) },
        bytes: runs[0].bytes,
        example: runs[0],
      };
      console.log(`[bench] http ${c.key.padEnd(12)} ttfb p50=${byCase[c.key].first_byte_ms.p50} `
        + `complete p50=${byCase[c.key].complete_ms.p50} bytes=${runs[0].bytes}`);
    }
    return { port, lazy_first_post_ms: lazy.complete_ms, cases: byCase };
  } finally {
    try { child.kill('SIGKILL'); } catch { /* gone */ }
  }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

async function main() {
  if (process.argv.includes('--cold')) return coldProbe();

  const meta = {
    date: new Date().toISOString(),
    node: process.version,
    platform: `${os.platform()} ${os.release()}`,
    cpu_model: os.cpus()[0] && os.cpus()[0].model,
    cpu_logical: os.cpus().length,
    total_mem_gb: r1(os.totalmem() / 1e9),
    voice: VOICE,
    runs: RUNS,
    warmup_discarded: WARMUP,
  };
  console.log('[bench] ' + JSON.stringify(meta));

  const cold = await runColdStarts();

  console.log('[bench] starting in-process TTSService for warm runs...');
  const svc = new TTSService();
  const tStart = performance.now();
  await svc.start(VOICE);
  const inProcStartMs = r1(performance.now() - tStart);
  const workerPid = svc._workers[0] && svc._workers[0].worker.pid;
  console.log(`[bench] warm service ready in ${inProcStartMs} ms, `
    + `pool=${svc._workers.length}, worker pid=${workerPid}, dtype=${svc._dtype}, `
    + `onnx threads=${svc._ttsThreads()}`);

  // --- CPU sampling around a back-to-back synthesis burst -------------------
  let cpu = { error: 'not run' };
  if (workerPid) {
    const sampler = startCpuSampler(workerPid, 200, 200);
    await new Promise((r) => setTimeout(r, 700));          // let the sampler settle
    const burstT0 = performance.now();
    let burstAudioMs = 0;
    for (let i = 0; i < 12; i++) {
      const r = await timedStream(svc, CASES[2].text);
      burstAudioMs += r.audio_ms;
    }
    const burstWall = performance.now() - burstT0;
    await new Promise((r) => setTimeout(r, 700));
    const { samples, stderr } = await sampler.stop();
    const cores = coresFromSamples(samples);
    // Restrict to the samples that overlap the burst: anything above 0.05 cores.
    const busy = cores.filter((c) => c > 0.05);
    cpu = {
      worker_pid: workerPid,
      samples: samples.length,
      burst_wall_ms: r1(burstWall),
      burst_audio_ms: r1(burstAudioMs),
      cores_busy_p50: r3(pct(busy, 0.5)),
      cores_busy_p95: r3(pct(busy, 0.95)),
      cores_busy_max: r3(busy.length ? Math.max(...busy) : NaN),
      cores_busy_mean: r3(mean(busy)),
      busy_sample_count: busy.length,
      sampler_stderr: stderr ? stderr.slice(0, 400) : '',
    };
    console.log(`[bench] worker CPU during burst: p50=${cpu.cores_busy_p50} cores, `
      + `max=${cpu.cores_busy_max} cores (of ${meta.cpu_logical} logical)`);
  }

  // --- warm per-case --------------------------------------------------------
  const cases = [];
  for (const c of CASES) cases.push(await benchCase(svc, c));

  svc.shutdown();
  await new Promise((r) => setTimeout(r, 300));

  const http_ = await benchHttp();

  const result = { meta, cold, in_process_start_ms: inProcStartMs, cpu, cases, http: http_ };
  const outPath = path.join(REPO_ROOT, 'test', 'bench_tts_results.json');
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
  console.log('[bench] wrote ' + outPath);
  console.log('BENCH_JSON_BEGIN');
  console.log(JSON.stringify(result, null, 2));
  console.log('BENCH_JSON_END');
  process.exit(0);
}

main().catch((err) => {
  console.error('[bench] FAILED:', err && err.stack ? err.stack : err);
  process.exit(1);
});
