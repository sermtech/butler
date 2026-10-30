/**
 * Server-side Text-to-Speech service (pool manager).
 *
 * This module used to run kokoro-js directly on the Node main thread, which
 * starved the event loop (LLM streaming, WebSocket I/O, routes, scheduler)
 * during synthesis. It now owns a pool of child_process workers that each
 * hold their own kokoro instance. The public API is preserved so existing
 * call sites (chat.js, live-assist-handler.js, tts.js REST, telegram.js) work
 * unchanged.
 *
 * Why child_process and not worker_threads: kokoro's ONNX runs in a native
 * addon. When the watchdog must kill a worker hung inside native generate(),
 * terminating a worker_thread mid-native-call crashes the entire Node process
 * (Windows 0xC0000409). A child_process is SIGKILLed cleanly by the OS, so a
 * hung synthesis costs one dropped message instead of the whole dashboard.
 *
 * Pool size: max(1, min(2, floor(cpuCount / 4))), overridable via env
 * `TTS_WORKER_POOL_SIZE` or via CONFIG/advanced-settings.json key
 * `tts_worker_pool_size` (applied by the caller of start()). Each child's ONNX
 * thread budget is set via `TTS_ONNX_THREADS` (see _ttsThreads).
 */

import { fork } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const WORKER_PATH = path.join(__dirname, 'tts-worker.js');

/**
 * Respawn circuit breaker (F12). If a worker slot's child crashes and gets
 * respawned more than MAX_WORKER_RESPAWNS times within RESPAWN_WINDOW_MS, the
 * runtime is presumed persistently broken (e.g. a bad ONNX/kokoro binding)
 * rather than a one-off crash. Without a breaker the pool would kill-respawn
 * that slot forever, reloading an ~80MB model every cycle, pinning CPU and
 * never surfacing the failure. Once tripped, the slot is marked dead and
 * excluded from routing instead of being respawned again.
 */
const MAX_WORKER_RESPAWNS = 3;
const RESPAWN_WINDOW_MS = 60_000;

/**
 * Cold-start timeout for a `start` call (model load + ONNX warm-up), kept
 * SEPARATE from the 30s per-synthesis watchdog. A first-boot kokoro load can
 * take far longer than a warm synthesis on a slow/contended machine; reusing
 * the 30s synth watchdog for it would SIGKILL a worker that is merely loading,
 * which then respawns into another cold load and, after MAX_WORKER_RESPAWNS,
 * trips the circuit breaker mid-answer and silences the rest of the reply. A
 * generous start budget breaks that cascade.
 */
const START_TIMEOUT_MS = 180_000;

/**
 * Valid kokoro/onnx dtypes. fp32 is the default: measured ~6x faster and ~140x
 * less jittery than q8 on x86 CPU (int8 kernels are poorly optimized), which
 * fixes both the latency and the run-to-run non-determinism. q8 remains
 * selectable but is not recommended on this hardware.
 */
const VALID_TTS_DTYPES = ['fp32', 'fp16', 'q8', 'q4', 'q4f16'];
const DEFAULT_TTS_DTYPE = 'fp32';

/**
 * @typedef {Object} SynthesizeResult
 * @property {Buffer} audio - WAV audio buffer
 * @property {number} sampleRate
 * @property {number} duration_ms
 */

/**
 * @typedef {Object} StreamChunk
 * @property {Buffer} audio - raw PCM16 LE bytes
 * @property {number} sampleRate
 * @property {number} sentenceIndex
 * @property {string} text
 * @property {boolean} isFinal
 */

/**
 * @typedef {Object} WorkerEntry
 * @property {import('node:child_process').ChildProcess} worker
 * @property {Map<number, PendingCall>} pending
 * @property {number} pendingCount
 * @property {boolean} started
 * @property {boolean} [dead] - true once the respawn circuit breaker has tripped for this slot
 */

/**
 * @typedef {Object} PendingCall
 * @property {(msg: any) => void} resolve
 * @property {(err: Error) => void} reject
 * @property {((msg: any) => void) | null} onChunk
 * @property {(() => void) | null} onEnd
 */

export class TTSService {
  constructor() {
    this._ready = false;
    /** @type {Promise<void>|null} In-flight start() promise so concurrent callers coalesce. */
    this._startingPromise = null;
    /** @type {string} */
    this._voice = 'af_heart';
    /** @type {string} Resolved kokoro dtype for every worker start (set in _doStart). */
    this._dtype = DEFAULT_TTS_DTYPE;
    /** @type {string[]} */
    this._voiceList = [];
    /** @type {WorkerEntry[]} */
    this._workers = [];
    /** Next correlation id for worker messages. */
    this._nextId = 1;
    /** Resolved pool size. */
    this._poolSize = 0;
    this._shutdown = false;
    /** @type {number[][]} Per-slot respawn timestamps (ms) for the circuit breaker. */
    this._respawnTimestamps = [];
    /** @type {Set<number>} Slots whose respawn circuit breaker has tripped. */
    this._deadSlots = new Set();
  }

  /** @returns {boolean} */
  get isReady() {
    return this._ready;
  }

  /** @returns {string} */
  get currentVoice() {
    return this._voice;
  }

  /**
   * Compute pool size: env override > default formula.
   * @param {number|undefined} override
   * @returns {number}
   */
  _resolvePoolSize(override) {
    if (typeof override === 'number' && Number.isFinite(override) && override >= 1) {
      return Math.min(8, Math.floor(override));
    }
    if (process.env.TTS_WORKER_POOL_SIZE) {
      const n = parseInt(process.env.TTS_WORKER_POOL_SIZE, 10);
      if (Number.isFinite(n) && n >= 1) return Math.min(8, n);
    }
    // Default to ONE persistent warm worker. On a CPU box shared with the LLM
    // stream + STT + embedding workers, a pool of 2 only doubles onnxruntime
    // thread pressure and buys nothing (kokoro serializes per worker anyway);
    // the single-warm-worker + FIFO pattern is what deterministic local TTS
    // servers use. A user can still opt into >1 via TTS_WORKER_POOL_SIZE or the
    // tts_worker_pool_size advanced setting (the override paths above).
    return 1;
  }

  /**
   * Resolve the kokoro dtype: explicit override > TTS_DTYPE env > fp32 default.
   * Unlike pool size / thread count (which only clamp), an invalid dtype must
   * throw -- a bad value reaching onnxruntime produces a cryptic native error.
   * @param {string|undefined} override
   * @returns {string}
   */
  _resolveDtype(override) {
    if (override !== undefined) {
      if (!VALID_TTS_DTYPES.includes(override)) {
        throw new Error(`Invalid tts_dtype: ${override} (expected one of ${VALID_TTS_DTYPES.join(', ')})`);
      }
      return override;
    }
    if (process.env.TTS_DTYPE !== undefined) {
      if (!VALID_TTS_DTYPES.includes(process.env.TTS_DTYPE)) {
        throw new Error(`Invalid TTS_DTYPE env: ${process.env.TTS_DTYPE} (expected one of ${VALID_TTS_DTYPES.join(', ')})`);
      }
      return process.env.TTS_DTYPE;
    }
    return DEFAULT_TTS_DTYPE;
  }

  /**
   * ONNX intra/inter-op thread budget for each kokoro child process, exported via
   * the child's OMP_NUM_THREADS. Because TTS runs in its own process, this is
   * independent of the embedding worker's OMP_NUM_THREADS=1 throttle. A couple of
   * threads keeps a synthesis from starving to the 30s watchdog under load without
   * inviting a CPU storm. Override with env `TTS_ONNX_THREADS`.
   * @returns {number}
   */
  _ttsThreads() {
    const raw = process.env.TTS_ONNX_THREADS;
    if (raw) {
      const n = parseInt(raw, 10);
      if (Number.isFinite(n) && n >= 1) return Math.min(8, n);
    }
    const cpuCount = os.cpus().length || 2;
    return Math.max(2, Math.min(4, Math.floor(cpuCount / 2)));
  }

  /**
   * fork() options for a kokoro child. `serialization: 'advanced'` makes the IPC
   * channel use v8 structured clone so audio Buffers cross efficiently (the
   * default 'json' mode would balloon a Buffer into a {type,data:[...]} array).
   * @returns {import('node:child_process').ForkOptions}
   */
  _forkOptions() {
    return {
      serialization: 'advanced',
      stdio: ['inherit', 'inherit', 'inherit', 'ipc'],
      env: { ...process.env, OMP_NUM_THREADS: String(this._ttsThreads()) },
    };
  }

  /**
   * Start the pool: spawn workers, pre-warm the first one, return.
   * Other workers finish their start() in the background.
   *
   * Concurrent callers coalesce on the same in-flight promise so multiple
   * WebSocket connections arriving during warmup all wait for one start.
   *
   * @param {string} [voiceId]
   * @param {{ poolSize?: number, dtype?: string }} [opts]
   * @returns {Promise<void>}
   */
  start(voiceId, opts = {}) {
    if (this._ready) return Promise.resolve();
    if (this._startingPromise) return this._startingPromise;
    this._startingPromise = this._doStart(voiceId, opts).finally(() => {
      this._startingPromise = null;
    });
    return this._startingPromise;
  }

  /**
   * @param {string|undefined} voiceId
   * @param {{ poolSize?: number, dtype?: string }} opts
   * @returns {Promise<void>}
   */
  async _doStart(voiceId, opts) {
    try {
      const poolSize = this._resolvePoolSize(opts.poolSize);
      this._poolSize = poolSize;
      // Resolve dtype + intra-op thread budget once and deliver them to every
      // worker on its 'start' message (the worker fails fast without them).
      this._dtype = this._resolveDtype(opts.dtype);
      const threads = this._ttsThreads();
      console.log(`[TTS] Spawning kokoro worker pool (size=${poolSize}, dtype=${this._dtype}, threads=${threads})...`);

      for (let i = 0; i < poolSize; i++) {
        this._workers.push(this._spawnWorker(i));
      }

      // Pre-warm the first worker synchronously so start() resolves with a
      // known-good voice list and a warm ONNX session.
      const first = this._workers[0];
      const firstStart = await this._callOnce(first, { type: 'start', voice: voiceId, dtype: this._dtype, threads }, START_TIMEOUT_MS);
      if (firstStart.voice) this._voice = firstStart.voice;
      this._voiceList = firstStart.voices || [];
      first.started = true;

      // Kick the rest off in the background so the service is usable as soon as worker[0] is ready.
      for (let i = 1; i < this._workers.length; i++) {
        const w = this._workers[i];
        this._callOnce(w, { type: 'start', voice: this._voice, dtype: this._dtype, threads }, START_TIMEOUT_MS)
          .then(() => { w.started = true; })
          .catch((err) => {
            console.error(`[TTS] Worker ${i} start failed:`, err.message);
          });
      }

      this._ready = true;
      console.log(`[TTS] Pool ready: ${this._voiceList.length} voices, default=${this._voice}`);
    } catch (err) {
      // Roll back: shut down any half-started workers so a retry gets a clean slate.
      this._tearDownWorkers();
      const msg = err && err.message ? err.message : String(err);
      const isBindingError = msg.includes('cannot run %1') || msg.includes('not a valid Win32');
      const finalMsg = isBindingError
        ? `ONNX native binding failed to load. Try: npm rebuild onnxruntime-node -- Original: ${msg}`
        : msg;
      console.error('[TTS] Failed to start pool:', finalMsg);
      throw new Error(finalMsg);
    }
  }

  /** @returns {string[]} */
  listVoices() {
    return this._voiceList;
  }

  /**
   * @param {string} text
   * @param {{ voice?: string, speed?: number }} [options]
   * @returns {Promise<SynthesizeResult>}
   */
  async synthesize(text, { voice, speed } = {}) {
    if (!this._ready) throw new Error('TTS service not ready');
    const entry = this._pickWorker();
    const reply = await this._callOnce(entry, {
      type: 'synthesize',
      text,
      opts: { voice: voice || this._voice, speed },
    });
    return {
      audio: Buffer.from(reply.audio),
      sampleRate: reply.sampleRate,
      duration_ms: reply.duration_ms,
    };
  }

  /**
   * Stream synthesis sentence by sentence.
   * Yields StreamChunk objects matching the pre-pool contract.
   *
   * @param {string} text
   * @param {{ voice?: string, speed?: number }} [options]
   * @returns {AsyncGenerator<StreamChunk>}
   */
  async *synthesizeStream(text, { voice, speed } = {}) {
    if (!this._ready) throw new Error('TTS service not ready');

    const entry = this._pickWorker();
    const id = this._nextId++;
    entry.pendingCount++;

    /** @type {Array<{ done: boolean, value?: any }>} */
    const queue = [];
    /** @type {Array<{ resolve: (v: any) => void, reject: (e: Error) => void }>} */
    const waiters = [];
    let ended = false;
    /** @type {Error | null} */
    let streamError = null;

    const push = (item) => {
      const waiter = waiters.shift();
      if (waiter) waiter.resolve(item);
      else queue.push(item);
    };

    entry.pending.set(id, {
      resolve: () => { /* stream uses onChunk/onEnd */ },
      reject: (err) => {
        streamError = err;
        // Wake every waiter so none hang.
        while (waiters.length > 0) {
          const w = waiters.shift();
          w.reject(err);
        }
      },
      onChunk: (msg) => push({ done: false, value: msg }),
      onEnd: () => {
        ended = true;
        push({ done: true });
      },
    });

    entry.worker.send({
      type: 'synthesizeStream',
      id,
      text,
      opts: { voice: voice || this._voice, speed },
    });

    try {
      while (true) {
        if (streamError) throw streamError;
        let item;
        if (queue.length > 0) {
          item = queue.shift();
        } else if (ended) {
          break;
        } else {
          // Race the chunk waiter against a watchdog. If no chunk or streamEnd
          // arrives within the timeout the kokoro ONNX session is hung. Terminate
          // the worker so _handleWorkerExit respawns it, then reject so the caller's
          // for-await loop receives a thrown error and chat.js can emit tts_error.
          item = await new Promise((resolve, reject) => {
            const timeoutId = setTimeout(() => {
              console.error('[TTS] Synthesis timeout: no chunk in 30s, killing hung worker');
              // SIGKILL a child_process stuck in a native ONNX generate(): the OS
              // reaps it without touching the parent (a worker_thread terminate()
              // here would crash the whole dashboard). _handleWorkerExit respawns.
              try { entry.worker.kill('SIGKILL'); } catch { /* already dead */ }
              reject(new Error('TTS synthesis timeout: kokoro worker unresponsive after 30s'));
            }, 30_000);
            waiters.push({
              resolve: (v) => { clearTimeout(timeoutId); resolve(v); },
              reject: (e) => { clearTimeout(timeoutId); reject(e); },
            });
          });
        }
        if (item.done) break;
        const msg = item.value;
        yield {
          audio: Buffer.from(msg.audio),
          sampleRate: msg.sampleRate,
          sentenceIndex: msg.sentenceIndex,
          text: msg.text,
          isFinal: !!msg.isFinal,
        };
      }
    } finally {
      // Drop the pending entry so the worker doesn't leak reference counts.
      if (entry.pending.has(id)) {
        entry.pending.delete(id);
        entry.pendingCount = Math.max(0, entry.pendingCount - 1);
      }
      // If the consumer broke out mid-stream, TELL the worker. Until 2026-07-26
      // this only stopped forwarding and let the worker synthesize the whole
      // abandoned answer, which is what killed the voice after two or three
      // barge-ins: with the pool at its default of one worker the next answer
      // queued behind the orphan until the 30s no-chunk watchdog SIGKILLed the
      // worker, and the retry had nowhere healthy to land. The worker honours
      // this between sentences (lib/tts-sentence-stream.js).
      //
      // Not sent when the stream ended on its own, and not sent when it failed:
      // in the second case the worker is already gone, which is also why the
      // send is guarded -- an abort must never be the thing that throws.
      if (!ended && !streamError) {
        try {
          entry.worker.send({ type: 'abort', id });
        } catch { /* the worker died; nothing left to cancel */ }
      }
    }
  }

  /** Shut down all workers and release resources. */
  shutdown() {
    this._shutdown = true;
    this._tearDownWorkers();
    this._ready = false;
    this._voiceList = [];
    console.log('[TTS] Service shut down');
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  _tearDownWorkers() {
    for (const entry of this._workers) {
      try {
        entry.worker.removeAllListeners();
        entry.worker.kill('SIGKILL');
      } catch { /* ignore */ }
      // Reject any in-flight calls so callers see an error instead of hanging.
      for (const pending of entry.pending.values()) {
        try { pending.reject(new Error('TTS worker terminated')); } catch { /* ignore */ }
      }
      entry.pending.clear();
      entry.pendingCount = 0;
    }
    this._workers = [];
    // Clean slate for a retry: the circuit breaker's history belongs to the
    // old pool instance, not the fresh one about to be spawned.
    this._respawnTimestamps = [];
    this._deadSlots = new Set();
  }

  /**
   * @param {number} index
   * @returns {WorkerEntry}
   */
  _spawnWorker(index) {
    const worker = fork(WORKER_PATH, [], this._forkOptions());
    /** @type {WorkerEntry} */
    const entry = {
      worker,
      pending: new Map(),
      pendingCount: 0,
      started: false,
    };
    // Butler: every fork, first start and respawn alike, passes through here, so
    // this is the one place a per-child OS setting (power-throttling opt-out)
    // can be applied. The hook is set by tts-sidecar.mjs; it must never throw.
    if (typeof this.onWorkerSpawn === 'function') this.onWorkerSpawn(worker.pid, index);
    worker.on('message', (msg) => this._handleWorkerMessage(entry, msg));
    worker.on('error', (err) => this._handleWorkerError(entry, index, /** @type {Error} */ (err)));
    worker.on('exit', (code, signal) => this._handleWorkerExit(entry, index, code, signal));
    return entry;
  }

  /**
   * @param {WorkerEntry} entry
   * @param {any} msg
   */
  _handleWorkerMessage(entry, msg) {
    if (!msg || typeof msg.id !== 'number') return;
    const call = entry.pending.get(msg.id);
    if (!call) return;

    if (msg.type === 'chunk') {
      if (call.onChunk) call.onChunk(msg);
      return;
    }
    if (msg.type === 'streamEnd') {
      entry.pending.delete(msg.id);
      entry.pendingCount = Math.max(0, entry.pendingCount - 1);
      if (call.onEnd) call.onEnd();
      return;
    }
    if (msg.type === 'error') {
      entry.pending.delete(msg.id);
      entry.pendingCount = Math.max(0, entry.pendingCount - 1);
      call.reject(new Error(msg.message || 'Worker error'));
      return;
    }
    // One-shot replies: 'result', 'started', 'voices', 'aborted'.
    entry.pending.delete(msg.id);
    entry.pendingCount = Math.max(0, entry.pendingCount - 1);
    call.resolve(msg);
  }

  /**
   * @param {WorkerEntry} entry
   * @param {number} index
   * @param {Error} err
   */
  _handleWorkerError(entry, index, err) {
    console.error(`[TTS] Worker ${index} error:`, err && err.message);
    // All pending calls on this worker are toast.
    for (const call of entry.pending.values()) {
      try { call.reject(new Error(`TTS worker ${index} error: ${err && err.message}`)); } catch { /* ignore */ }
    }
    entry.pending.clear();
    entry.pendingCount = 0;
  }

  /**
   * @param {WorkerEntry} entry
   * @param {number} index
   * @param {number|null} code
   * @param {string|null} [signal]
   */
  _handleWorkerExit(entry, index, code, signal) {
    console.warn(`[TTS] Worker ${index} exited (code=${code} signal=${signal || 'none'})`);
    for (const call of entry.pending.values()) {
      try { call.reject(new Error(`TTS worker ${index} exited (code ${code})`)); } catch { /* ignore */ }
    }
    entry.pending.clear();
    entry.pendingCount = 0;
    // Replace the crashed worker in-place so the pool size stays stable.
    if (this._shutdown) return;
    const slot = this._workers.indexOf(entry);
    if (slot === -1) return;

    if (this._deadSlots.has(slot)) {
      // Breaker already tripped for this slot: never respawn again.
      entry.dead = true;
      return;
    }

    // Circuit breaker bookkeeping: count this crash against the slot's
    // rolling respawn window before deciding whether to respawn again.
    const now = Date.now();
    const recent = (this._respawnTimestamps[slot] || []).filter((t) => now - t < RESPAWN_WINDOW_MS);
    recent.push(now);
    this._respawnTimestamps[slot] = recent;

    if (recent.length > MAX_WORKER_RESPAWNS) {
      this._deadSlots.add(slot);
      entry.dead = true;
      const msg = `TTS worker slot ${slot} crashed ${recent.length} times within ${RESPAWN_WINDOW_MS}ms; ` +
        `circuit breaker tripped, respawning stopped (persistently broken TTS runtime).`;
      console.error(`[TTS] ${msg}`);
      return;
    }

    const replacement = this._spawnWorker(slot);
    this._workers[slot] = replacement;
    // Restart it so the replacement is warm. Carry the same dtype + thread
    // budget the pool started with so a respawn never silently reverts them.
    this._callOnce(replacement, { type: 'start', voice: this._voice, dtype: this._dtype, threads: this._ttsThreads() }, START_TIMEOUT_MS)
      .then(() => { replacement.started = true; console.log(`[TTS] Worker ${slot} respawned`); })
      .catch((startErr) => console.error(`[TTS] Worker ${slot} respawn start failed:`, startErr.message));
  }

  /**
   * Least-busy routing. With workers capped at 1-2, the loop is trivial.
   * @returns {WorkerEntry}
   */
  _pickWorker() {
    if (this._workers.length === 0) throw new Error('TTS worker pool is empty');
    const alive = this._workers.filter((w) => !w.dead);
    if (alive.length === 0) {
      throw new Error(
        `TTS worker pool exhausted: circuit breaker tripped on all ${this._workers.length} worker(s), ` +
        `TTS is unavailable`,
      );
    }
    let best = alive[0];
    for (let i = 1; i < alive.length; i++) {
      if (alive[i].pendingCount < best.pendingCount) best = alive[i];
    }
    return best;
  }

  /**
   * Send a one-shot message to a specific worker and await its single reply.
   * Used for start/synthesize/listVoices (not for streams). A watchdog mirrors
   * synthesizeStream: if no reply arrives within timeoutMs the kokoro ONNX
   * session is presumed hung, so we terminate the worker (triggering
   * _handleWorkerExit -> respawn) and reject instead of hanging forever.
   *
   * @param {WorkerEntry} entry
   * @param {any} payload
   * @param {number} [timeoutMs]
   * @returns {Promise<any>}
   */
  _callOnce(entry, payload, timeoutMs = 30_000) {
    const id = this._nextId++;
    entry.pendingCount++;
    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        if (entry.pending.has(id)) {
          entry.pending.delete(id);
          entry.pendingCount = Math.max(0, entry.pendingCount - 1);
        }
        console.error(`[TTS] _callOnce timeout after ${timeoutMs}ms; killing worker`);
        try { entry.worker.kill('SIGKILL'); } catch { /* ignore */ }
        reject(new Error(`TTS worker unresponsive after ${timeoutMs}ms`));
      }, timeoutMs);

      // Clear pool bookkeeping idempotently on settle. In the normal flow
      // _handleWorkerMessage already deletes this id and decrements
      // pendingCount before invoking resolve/reject; the guard below makes
      // the wrappers safe whether or not that happened (delete is a no-op on
      // an already-removed id; the count is floored at 0).
      const clearPending = () => {
        if (entry.pending.has(id)) {
          entry.pending.delete(id);
          entry.pendingCount = Math.max(0, entry.pendingCount - 1);
        }
      };

      entry.pending.set(id, {
        resolve: (msg) => { if (settled) return; settled = true; clearTimeout(timer); clearPending(); resolve(msg); },
        reject: (err) => { if (settled) return; settled = true; clearTimeout(timer); clearPending(); reject(err); },
        onChunk: null,
        onEnd: null,
      });

      try {
        entry.worker.send({ ...payload, id });
      } catch (err) {
        if (!settled) { settled = true; clearTimeout(timer); }
        entry.pending.delete(id);
        entry.pendingCount = Math.max(0, entry.pendingCount - 1);
        reject(err);
      }
    });
  }
}

/**
 * Check if kokoro-js is available (can be dynamically imported).
 * @returns {Promise<boolean>}
 */
export async function isTTSAvailable() {
  try {
    // @ts-ignore - kokoro-js is an optional dependency
    await import('kokoro-js');
    return true;
  } catch {
    return false;
  }
}
