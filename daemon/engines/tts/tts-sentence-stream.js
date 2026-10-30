/**
 * The TTS worker's sentence loop, and the cancellation registry behind it.
 *
 * This lives outside tts-worker.js for two reasons. The worker file refuses to
 * load without an IPC channel (it is a forked child by contract), so nothing in
 * it can be unit-tested; and the interesting decision here -- when to STOP -- is
 * pure logic that deserves tests more than the ONNX call around it does.
 *
 * WHY CANCELLATION EXISTS AT ALL. Interrupting an answer used to abandon only
 * the stream: the parent stopped forwarding chunks and the worker synthesized
 * the whole abandoned answer anyway. With the pool at its default of one worker,
 * the next answer queued behind that orphan until the parent's 30s no-chunk
 * watchdog SIGKILLed the worker mid-sentence, and the retry had no healthy
 * worker left to land on. Two or three barge-ins were enough to kill the voice.
 *
 * WHY BETWEEN SENTENCES, and not sooner: a single kokoro generate() is a native
 * ONNX call with no cooperative cancellation. Synthesis is per sentence, though,
 * so checking between them turns an abandoned thirty-second answer into one
 * abandoned sentence. That is the whole trick.
 */

/**
 * Calls cancelled by the parent, by call id. Module-level because the abort
 * arrives on a DIFFERENT message than the synthesis it cancels, and the two have
 * to meet somewhere.
 */
const cancelled = new Set();

/** @param {number} id */
export function markCancelled(id) {
  cancelled.add(id);
}

/** @param {number} id @returns {boolean} */
export function isCancelled(id) {
  return cancelled.has(id);
}

/** @param {number} id */
export function forgetCancellation(id) {
  cancelled.delete(id);
}

/** Size of the registry. Exists so a test can prove it does not leak. */
export function cancelledCount() {
  return cancelled.size;
}

/**
 * Where a message belongs: an abort must NOT go through the worker's FIFO queue.
 * The worker serializes calls, so an abort queued behind the synthesis it is
 * meant to cancel would only be handled once that synthesis had finished, which
 * is a cancellation that cancels nothing.
 * @param {string} type
 * @returns {'immediate' | 'queued'}
 */
export function routeMessage(type) {
  return type === 'abort' ? 'immediate' : 'queued';
}

/**
 * Split into sentences at .!? boundaries. Text with no punctuation (a
 * mid-sentence fragment from segmentTextForTts) is one sentence, and so is text
 * that is nothing but whitespace -- returning an empty list would silently drop
 * the call instead of synthesizing it.
 * @param {string} text
 * @returns {string[]}
 */
export function splitSentences(text) {
  const parts = text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
  return parts.length > 0 ? parts : [text];
}

/**
 * Synthesize sentence by sentence, stopping as soon as the call is cancelled.
 *
 * @param {object} args
 * @param {number} args.id                      call id, the key cancellation uses
 * @param {string} args.text
 * @param {(sentence: string) => Promise<{audio: Float32Array, sampling_rate: number}>} args.generate
 * @param {(chunk: {audio: Float32Array, sampleRate: number, sentenceIndex: number, text: string, isFinal: boolean}) => void} args.emit
 * @returns {Promise<{spoken: number, cancelled: boolean}>}
 */
export async function streamSentences({ id, text, generate, emit }) {
  const sentences = splitSentences(text);
  let spoken = 0;
  try {
    for (const sentence of sentences) {
      // Checked BEFORE the call, so an abort that arrives before any work stops
      // everything, and one that arrives mid-sentence stops the next.
      if (isCancelled(id)) return { spoken, cancelled: true };
      const result = await generate(sentence);
      spoken += 1;
      emit({
        audio: result.audio,
        sampleRate: result.sampling_rate,
        sentenceIndex: spoken,
        text: sentence,
        isFinal: false,
      });
    }
    return { spoken, cancelled: false };
  } finally {
    // Every exit path, including a throw: an id left behind would cancel a
    // future call that happened to reuse the number.
    forgetCancellation(id);
  }
}
