// Wake-word detector -- openWakeWord 3-stage ONNX pipeline (melspectrogram ->
// speech embedding -> one wake head PER WORD), ported VERBATIM from the
// app's verified scripts/dashboard/client/src/lib/wakeWord.ts. Pure chunking /
// scaling / windowing logic over three ONNX stages. Uses the global `ort`
// (onnxruntime-web, loaded from CDN in index.html). Runs fully in the browser.
//
// Verified constants (openWakeWord utils.py + hey-buddy JS):
//   - 16 kHz mono, 1280-sample (80 ms) chunks.
//   - mel input = chunk + 480 samples (160*3) of left context, scaled x32768.
//     Output 32 mel bins/frame, each transformed x/10+2.
//   - embedding input = last 76 mel frames as [1,76,32,1] -> 96-dim embedding.
//   - wake input = last 16 embeddings as [1,16,96] -> one sigmoid score in [0,1].
//
// Several words (docs/wake-words.md AC-806): every loaded head scores the SAME
// 16 embeddings on each chunk. Only the highest score at or over the threshold
// fires, and ONE refractory follows any fire, so two words never fire for one
// utterance. Which words, and the threshold, are the daemon's (AC-805).
(function () {
  const SAMPLE_RATE = 16000;
  const CONTEXT_SAMPLES = 480;
  const MEL_BINS = 32;
  const WINDOW_FRAMES = 76;
  const EMBED_DIM = 96;
  const WAKE_CONTEXT = 16;
  const INT16_SCALE = 32768;
  const MEL_TRANSFORM_DIV = 10;
  const MEL_TRANSFORM_ADD = 2;
  const SAMPLES_PER_MS = SAMPLE_RATE / 1000;

  function concat(a, b) {
    const out = new Float32Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
  }

  // `stages` = { mel, embed, wake: [{ id, run }], release }. `run` scores one
  // word's head; `release` frees every session and is awaited by `close()`.
  function createWakeWordDetector(stages, config) {
    const { refractoryMs, chunkSamples } = config;
    let threshold = config.threshold;
    if (!Array.isArray(stages.wake) || stages.wake.length === 0) {
      throw new Error('wake word: no word to listen for');
    }
    if (!(chunkSamples > CONTEXT_SAMPLES)) {
      throw new Error(`wake word: chunkSamples (${chunkSamples}) must exceed the ${CONTEXT_SAMPLES}-sample context`);
    }
    const refractorySamples = Math.max(0, refractoryMs) * SAMPLES_PER_MS;

    let inputBuf = new Float32Array(0);
    let rawTail = new Float32Array(CONTEXT_SAMPLES);
    let melFrames = new Float32Array(0);
    let embHistory = Array.from({ length: WAKE_CONTEXT }, () => new Float32Array(EMBED_DIM));
    let samplesSinceFire = Number.POSITIVE_INFINITY;

    function reset() {
      inputBuf = new Float32Array(0);
      rawTail = new Float32Array(CONTEXT_SAMPLES);
      melFrames = new Float32Array(0);
      embHistory = Array.from({ length: WAKE_CONTEXT }, () => new Float32Array(EMBED_DIM));
      samplesSinceFire = Number.POSITIVE_INFINITY;
    }

    async function processChunk(chunk) {
      samplesSinceFire += chunkSamples;

      const withContext = concat(rawTail, chunk);
      const scaled = new Float32Array(withContext.length);
      for (let i = 0; i < withContext.length; i++) scaled[i] = withContext[i] * INT16_SCALE;
      rawTail = chunk.slice(chunk.length - CONTEXT_SAMPLES);

      const melOut = await stages.mel(scaled);
      if (melOut.length === 0 || melOut.length % MEL_BINS !== 0) {
        throw new Error(`wake word: mel model returned ${melOut.length} values, not a multiple of ${MEL_BINS} bins`);
      }

      const transformed = new Float32Array(melOut.length);
      for (let i = 0; i < melOut.length; i++) transformed[i] = melOut[i] / MEL_TRANSFORM_DIV + MEL_TRANSFORM_ADD;
      const combined = concat(melFrames, transformed);
      const keep = WINDOW_FRAMES * MEL_BINS;
      melFrames = combined.length > keep ? combined.slice(combined.length - keep) : combined;

      if (melFrames.length < keep) {
        return { score: null, fired: false, word: null };
      }

      const embedding = await stages.embed(melFrames);
      if (embedding.length !== EMBED_DIM) {
        throw new Error(`wake word: embedding model returned ${embedding.length} values, expected ${EMBED_DIM}`);
      }
      embHistory.shift();
      embHistory.push(Float32Array.from(embedding));

      const feat = new Float32Array(WAKE_CONTEXT * EMBED_DIM);
      for (let i = 0; i < WAKE_CONTEXT; i++) feat.set(embHistory[i], i * EMBED_DIM);
      // Every word, one after another on the same features; the best wins.
      let score = -1;
      let best = null;
      for (const w of stages.wake) {
        const wakeOut = await w.run(feat);
        if (wakeOut.length < 1) {
          throw new Error(`wake word: ${w.id} model returned an empty output`);
        }
        if (wakeOut[0] > score) { score = wakeOut[0]; best = w.id; }
      }

      // The refractory is the detector's, not a word's (AC-806).
      const fired = score >= threshold && samplesSinceFire >= refractorySamples;
      if (fired) samplesSinceFire = 0;
      return { score, fired, word: fired ? best : null };
    }

    async function feedImpl(audio) {
      inputBuf = inputBuf.length === 0 ? audio.slice() : concat(inputBuf, audio);
      let firedWord = null;
      let lastScore = null;
      let offset = 0;
      while (inputBuf.length - offset >= chunkSamples) {
        const chunk = inputBuf.subarray(offset, offset + chunkSamples);
        const r = await processChunk(chunk);
        if (r.score !== null) lastScore = r.score;
        if (r.fired) firedWord = r.word;
        offset += chunkSamples;
      }
      inputBuf = offset > 0 ? inputBuf.slice(offset) : inputBuf;
      return { score: lastScore, fired: firedWord !== null, word: firedWord };
    }

    // Serialize overlapping feeds (an always-on caller emits PCM continuously).
    let chain = Promise.resolve();
    function feed(audio) {
      const run = chain.then(() => feedImpl(audio));
      chain = run.catch(() => {});
      return run;
    }

    // A threshold change needs no new sessions; it applies from the next chunk.
    function setThreshold(t) { threshold = t; }

    // Release the sessions once the feed in flight has finished with them.
    function close() { return chain.then(() => stages.release()); }

    return { feed, reset, setThreshold, close };
  }

  // `words` = [{ id, url }], in the daemon's order. The shared mel and
  // embedding models load once per detector, then one session per word.
  async function loadWakeWordDetector(urls, words, config) {
    if (typeof ort === 'undefined') {
      throw new Error('wake word: onnxruntime-web (global `ort`) is not loaded');
    }
    const opts = { executionProviders: ['wasm'] };
    // Load sequentially (never all at once) to keep peak memory low -- iOS/iPadOS
    // WASM memory limits OOM when several sessions instantiate together.
    const sessions = [];
    const release = async () => {
      for (const s of sessions) await s.release();
    };
    let melS, embS;
    const heads = [];
    try {
      melS = await ort.InferenceSession.create(urls.mel, opts);
      sessions.push(melS);
      embS = await ort.InferenceSession.create(urls.embedding, opts);
      sessions.push(embS);
      for (const w of words) {
        const s = await ort.InferenceSession.create(w.url, opts);
        sessions.push(s);
        heads.push({ id: w.id, session: s });
      }
    } catch (e) {
      await release();
      throw e;
    }

    const pick = (out, name, label) => {
      const tensor = out[name];
      if (!tensor) throw new Error(`wake word: ${label} model produced no "${name}" output`);
      return tensor.data;
    };

    const stages = {
      mel: async (audio) => {
        const t = new ort.Tensor('float32', audio, [1, audio.length]);
        const out = await melS.run({ [melS.inputNames[0]]: t });
        return pick(out, melS.outputNames[0], 'melspectrogram');
      },
      embed: async (window) => {
        const t = new ort.Tensor('float32', window, [1, WINDOW_FRAMES, MEL_BINS, 1]);
        const out = await embS.run({ [embS.inputNames[0]]: t });
        return pick(out, embS.outputNames[0], 'embedding');
      },
      wake: heads.map(({ id, session }) => ({
        id,
        run: async (feat) => {
          const t = new ort.Tensor('float32', feat, [1, WAKE_CONTEXT, EMBED_DIM]);
          const out = await session.run({ [session.inputNames[0]]: t });
          return pick(out, session.outputNames[0], id);
        },
      })),
      release,
    };
    return createWakeWordDetector(stages, config);
  }

  // The shared stages. Each word's own model URL comes from the daemon.
  const WAKE_WORD_MODEL_URLS = {
    mel: 'models/melspectrogram.onnx',
    embedding: 'models/embedding_model.onnx',
  };

  window.WakeWord = { createWakeWordDetector, loadWakeWordDetector, WAKE_WORD_MODEL_URLS };
})();
