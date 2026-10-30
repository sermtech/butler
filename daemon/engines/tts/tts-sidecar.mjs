// Kokoro TTS sidecar: Butler's transport in front of the real TTSService.
//
// The service under this file already does the right thing -- it emits one
// PCM16 chunk per SENTENCE and it cancels between sentences when its consumer
// stops reading (tts-service.js, tts-sentence-stream.js). Until 2026-08-17 this
// file threw both away: it drained the whole generator into one WAV and
// answered a single HTTP request with it, so time-to-first-byte equalled
// time-to-last-byte (+3252 ms on a 3-sentence reply [MEASURED 2026-08-16]) and
// nothing could cancel a reply already in flight (the next reply waited 7887 ms
// behind an abandoned one [MEASURED 2026-08-17]).
//
// So this is a FRAMED STREAM plus a cancel route (plan.md 4.3, section 5 step 8).
//
//   POST /tts    {text, voice?, speed?, id?}  -> 200, then a sequence of frames
//   POST /abort  {id}                         -> 200 {aborted: bool}
//   GET  /voices                              -> 200 {voices: {id, language}[]}
//
// Frame layout, 16-byte header then the payload:
//
//   magic  4 bytes  "BTL1"
//   kind   uint32   0 audio | 1 error | 2 end
//   rate   uint32   sample rate for audio frames, 0 otherwise
//   len    uint32   payload length
//
// Three properties the framing buys, each of which a bare chunked body loses:
// the reader can tell one sentence from the next (HTTP chunk boundaries are not
// preserved by any client), a failure AFTER the 200 still arrives as an error
// instead of a short reply that sounds complete, and an explicit end frame
// makes a truncated stream detectable rather than silently shorter.
//
// The voice is per REQUEST. It used to be bound once from KOKORO_VOICE at boot,
// which meant the daemon's `voice` setting was accepted, echoed back on the
// wire, and discarded -- AC-7b's "the panel is decoration" failure. KOKORO_VOICE
// now only chooses which voice is pre-warmed. The SPEED is per request for the
// same reason and arrived the same way (AC-107): TTSService has always taken it
// and this file passed only the voice, so three layers of Butler threw away a
// number the engine has had all along.
//
// /voices exists because the daemon must not hold a list of its own (AC-108).
// The pool learns the names from the loaded model, so this route is only
// answerable once the model is up -- it awaits the same start the first
// synthesis does, which is why the adapter asks AFTER it has warmed rather
// than during boot.
//
// No CORS headers: nothing but daemon/adapters/tts_kokoro.py talks to this
// process. They were here for a spike page that let a browser A/B kokoro against
// speechSynthesis, and that page is not part of Butler.
//
// Run from the repo root so node_modules (kokoro-js, transformers) resolves:
//   node daemon/engines/tts/tts-sidecar.mjs      # http://localhost:8141/tts
import http from 'node:http';
import { once } from 'node:events';
import { TTSService } from './tts-service.js';
import { applyHighQos } from './tts-qos.js';

const PORT = Number(process.env.SIDECAR_PORT || 8141);
const WARM_VOICE = process.env.KOKORO_VOICE || 'af_heart';

const MAGIC = Buffer.from('BTL1', 'ascii');
const KIND_AUDIO = 0;
const KIND_ERROR = 1;
const KIND_END = 2;
const HEADER_BYTES = 16;

const svc = new TTSService();
// Every kokoro worker, first start and respawn, is opted out of Windows power
// throttling (EXECUTION_SPEED) the moment it is forked: left alone, Windows 11
// moves its ONNX threads to the E-cores and synthesis drops from ~4.3x to
// ~0.8x realtime. A performance setting: loud on failure, never fatal.
svc.onWorkerSpawn = (pid, slot) => {
  applyHighQos(pid).catch((e) => {
    console.error(`[TTS] WARNING: worker ${slot} (pid ${pid}) not opted out of power throttling: ${e.message}`);
  });
};
let started = null;
function ensureStarted() {
  if (!started) started = svc.start(WARM_VOICE); // fp32 default (no TTS_DTYPE env)
  return started;
}

/**
 * Live synthesis jobs by client-supplied id, so /abort can name one.
 * @type {Map<string, {gen: AsyncGenerator, aborted: boolean}>}
 */
const jobs = new Map();

/**
 * Write one frame, honouring backpressure.
 *
 * A sentence is ~100 KB of PCM and the daemon may be slower than kokoro, so
 * ignoring the return of res.write() would let node buffer a whole reply in
 * memory -- the server-side half of D-8, in the process that can least afford
 * it. Awaiting 'drain' costs nothing when the socket is keeping up.
 *
 * @param {import('node:http').ServerResponse} res
 * @param {number} kind @param {number} rate @param {Buffer} payload
 */
async function writeFrame(res, kind, rate, payload) {
  // The daemon aborts by tearing the socket down (tts_kokoro.abort step 3),
  // and it can do that while this write is parked on backpressure. A destroyed
  // stream NEVER emits 'drain', so awaiting drain alone hangs this loop
  // forever: the for-await never returns, its finally never runs, and the job
  // stays in `jobs` for the life of the process -- the leak this rewrite
  // exists to close, reopened one layer up. So: never write to a dead
  // response, and race the drain against the close.
  if (res.destroyed || res.writableEnded) return false;
  const header = Buffer.alloc(HEADER_BYTES);
  MAGIC.copy(header, 0);
  header.writeUInt32LE(kind, 4);
  header.writeUInt32LE(rate, 8);
  header.writeUInt32LE(payload.length, 12);
  if (!res.write(Buffer.concat([header, payload]))) {
    await Promise.race([once(res, 'drain'), once(res, 'close')]);
  }
  return !res.destroyed;
}

/**
 * Stop a running job. Returns whether there was one.
 *
 * gen.return() runs the generator's own finally, which is what tells the kokoro
 * worker to stop (tts-service.js sends {type:'abort'} there, and the worker
 * handles it outside its FIFO queue so it cannot end up behind the synthesis it
 * cancels -- D-4). One sentence is already inside a native ONNX generate() and
 * cannot be interrupted, so an abort strands at most that one.
 *
 * @param {string} id @returns {boolean}
 */
function abortJob(id) {
  const job = jobs.get(id);
  if (!job || job.aborted) return false;
  job.aborted = true;
  job.gen.return(undefined).catch(() => { /* already finishing */ });
  return true;
}

/** @param {import('node:http').IncomingMessage} req @returns {Promise<any>} */
function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      try { resolve(JSON.parse(body || '{}')); } catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

/**
 * Read a JSON body, or answer 400 and return null. Both routes want exactly
 * this, and a second copy of it is a second place to get the failure wrong.
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 */
async function readBody(req, res) {
  try {
    return await readJson(req);
  } catch {
    res.writeHead(400); res.end('body must be json');
    return null;
  }
}

/**
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 */
async function handleTts(req, res) {
  const body = await readBody(req, res);
  if (!body) return;
  const { text, voice, speed, id } = body;
  if (!text || !String(text).trim()) { res.writeHead(400); res.end('no text'); return; }
  // A speed that is not a usable number is refused HERE as well as in the
  // adapter (AC-107). kokoro puts it straight into a float32 tensor and
  // validates nothing, so a zero or a NaN is not an error the engine reports
  // -- it is audio that comes out wrong. Two gates because this process has a
  // second caller in the bench script, and a 400 is legible where garbled
  // speech is not.
  if (speed != null && !(Number.isFinite(speed) && speed > 0)) {
    res.writeHead(400); res.end(`bad speed: ${JSON.stringify(speed)}`); return;
  }

  // Before the 200: a pool that will not start is a plain HTTP failure, and
  // the adapter's cold-start budget covers it (D-5). After the 200 the only
  // way to report anything is an error frame.
  try {
    await ensureStarted();
  } catch (e) {
    res.writeHead(500); res.end(String((e && e.message) || e)); return;
  }

  const jobId = id != null ? String(id) : `anon-${Date.now()}-${jobs.size}`;
  // Both options are passed only when the caller named them, so an omitted
  // field still means "the service's own default" rather than undefined
  // travelling down to the worker as a value (AC-107).
  const opts = {};
  if (voice) opts.voice = voice;
  if (speed != null) opts.speed = Number(speed);
  const gen = svc.synthesizeStream(String(text), opts);
  const job = { gen, aborted: false };
  jobs.set(jobId, job);

  // A daemon that disappears mid-reply must not leave the worker synthesizing
  // for nobody: that is the same stranded-engine failure as a missing abort,
  // arriving through a socket instead of a route. `abortJob` is already a
  // no-op for an unknown id, so this needs no guard of its own.
  res.on('close', () => abortJob(jobId));
  // An abrupt teardown from the far end surfaces here, and an 'error' with no
  // listener on a Node stream takes the WHOLE PROCESS down -- which would end
  // speech for every satellite, not just this reply. `req` was already
  // protected inside readJson; this is the other half.
  res.on('error', () => abortJob(jobId));

  res.writeHead(200, {
    'Content-Type': 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  try {
    for await (const chunk of gen) {
      // A false return means the far end is gone. Stop pulling from the
      // generator: breaking runs its finally, which is what tells the kokoro
      // worker to stop synthesizing for a client that is no longer there.
      if (!await writeFrame(res, KIND_AUDIO, chunk.sampleRate, chunk.audio)) break;
    }
    if (!job.aborted) await writeFrame(res, KIND_END, 0, Buffer.alloc(0));
  } catch (e) {
    // Loud, in-band. Swallowing this would end the reply early and sound
    // exactly like a short answer -- the silent-skip failure D-4 names.
    try {
      await writeFrame(res, KIND_ERROR, 0,
        Buffer.from(String((e && e.message) || e), 'utf8'));
    } catch { /* the socket went away with it */ }
  } finally {
    jobs.delete(jobId);
    res.end();
  }
}

/**
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 */
async function handleAbort(req, res) {
  const body = await readBody(req, res);
  if (!body) return;
  const id = body.id != null ? String(body.id) : '';
  if (!id) { res.writeHead(400); res.end('no id'); return; }
  const aborted = abortJob(id);
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ aborted }));
}

/**
 * What this engine can actually speak (AC-108).
 *
 * The pool reads the names off the loaded model, so this cannot be answered
 * before the model is up: `ensureStarted()` is the same await the first
 * synthesis pays, and a pool that will not start fails here as a 500 with the
 * reason rather than an empty array. An empty array would be read by the
 * daemon as "this engine speaks nothing", drawn as an empty picker, and look
 * like all was well.
 *
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 */
async function handleVoices(req, res) {
  try {
    await ensureStarted();
  } catch (e) {
    res.writeHead(500); res.end(String((e && e.message) || e)); return;
  }
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ voices: svc.listVoices() }));
}

const server = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/tts') { handleTts(req, res); return; }
  if (req.method === 'POST' && req.url === '/abort') { handleAbort(req, res); return; }
  if (req.method === 'GET' && req.url === '/voices') { handleVoices(req, res); return; }
  res.writeHead(404); res.end('not found');
});

server.listen(PORT, () => console.log(
  `[kokoro-sidecar] fp32 TTS ready on http://localhost:${PORT}/tts (warm voice=${WARM_VOICE})`));
