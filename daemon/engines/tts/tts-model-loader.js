/**
 * Kokoro model loader with explicit dtype and ONNX thread control.
 *
 * kokoro-js's own KokoroTTS.from_pretrained() forwards only { dtype, device,
 * progress_callback } to the underlying transformers.js model -- it does NOT
 * accept session_options, so there is no way through its public wrapper to cap
 * onnxruntime's thread count. But its KokoroTTS class constructor is public
 * (model, tokenizer), and @huggingface/transformers exports the model +
 * tokenizer classes whose from_pretrained() DOES plumb session_options into
 * InferenceSession.create (models.js). So we build the model ourselves with the
 * thread caps we want, then hand it to the stock KokoroTTS class.
 *
 * Why this matters (measured 2026-07-23): the previous default dtype 'q8' is
 * ~6x SLOWER and ~140x more jittery than 'fp32' on this x86 CPU (int8 kernels
 * are poorly optimized), and onnxruntime defaults intra-op threads to ALL
 * physical cores per session -- on a many-core box shared with the LLM/STT/
 * embedding workers that oversubscription is the residual source of run-to-run
 * variance. Both are fixed here: fp32 by default, threads capped via real
 * SessionOptions (OMP_NUM_THREADS alone is ignored by this ORT build).
 *
 * This module is pure (no top-level side effects, no IPC guard) so it can be
 * unit-tested by injecting fake transformers/kokoro modules.
 *
 */
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

import { languageOf } from './tts-phonemes.js';

// Where kokoro-js keeps the .bin style vectors. Resolved from THIS file so it
// follows the package rather than the process's working directory: the sidecar
// is spawned by the daemon and its cwd is not this folder.
const DEFAULT_VOICES_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../node_modules/kokoro-js/voices');

/**
 * The names of every voice pack on disk, or [] if the directory is missing.
 *
 * Empty is not an error here: it means this install has only the packs
 * kokoro-js names, which is a working English-only engine. The LOUD failure
 * for "no voices at all" lives one layer out, in the adapter, where it can
 * tell "could not ask" from "asked and got nothing" (AC-108).
 *
 * @param {string} dir
 * @returns {Promise<string[]>}
 */
export async function extraVoicePacks(dir) {
  try {
    const names = await fs.readdir(dir);
    return names.filter((n) => n.endsWith('.bin')).map((n) => n.slice(0, -4));
  } catch {
    return [];
  }
}

/**
 * Build the model with real SessionOptions and list what it can speak.
 * @param {Object} args
 * @param {string} args.dtype - required, e.g. 'fp32' | 'fp16' | 'q8'. No default: the caller resolves it.
 * @param {number} args.intraOpNumThreads - required, positive integer.
 * @param {number} [args.interOpNumThreads=1]
 * @param {'sequential'|'parallel'} [args.executionMode='sequential']
 * @param {string} [args.modelId]
 * @param {any} [args.transformersModule] - test seam: { StyleTextToSpeech2Model, AutoTokenizer }
 * @param {any} [args.kokoroModule] - test seam: { KokoroTTS }
 * @param {string} [args.voicesDir] - where the .bin voice packs live; a test seam.
 * @returns {Promise<{ tts: any, voiceList: {id: string, language: string}[] }>}
 */
export async function loadKokoroModel({
  dtype,
  intraOpNumThreads,
  interOpNumThreads = 1,
  executionMode = 'sequential',
  modelId = 'onnx-community/Kokoro-82M-v1.0-ONNX',
  transformersModule,
  kokoroModule,
  voicesDir = DEFAULT_VOICES_DIR,
} = {}) {
  if (typeof dtype !== 'string' || dtype.length === 0) {
    throw new Error('loadKokoroModel: dtype is required (e.g. "fp32")');
  }
  if (!Number.isInteger(intraOpNumThreads) || intraOpNumThreads < 1) {
    throw new Error(`loadKokoroModel: intraOpNumThreads must be a positive integer, got ${intraOpNumThreads}`);
  }

  // @ts-ignore - both are optional dependencies, imported lazily unless injected.
  const transformers = transformersModule || await import('@huggingface/transformers');
  // @ts-ignore
  const kokoro = kokoroModule || await import('kokoro-js');

  const { StyleTextToSpeech2Model, AutoTokenizer } = transformers;
  const { KokoroTTS } = kokoro;

  const model = await StyleTextToSpeech2Model.from_pretrained(modelId, {
    dtype,
    device: 'cpu',
    session_options: { intraOpNumThreads, interOpNumThreads, executionMode },
  });
  const tokenizer = await AutoTokenizer.from_pretrained(modelId);

  const tts = new KokoroTTS(model, tokenizer);
  // Each voice WITH THE LANGUAGE IT SPEAKS (AC-155, docs/language.md D3).
  // kokoro's own table carries it, so this is the engine stating a fact about
  // itself rather than the daemon inferring one from an `af_`/`pf_` prefix --
  // and a naming convention read in the daemon is what the AC-41 grep gate
  // forbids. Shape: [{ id, language }].
  const voiceList = Object.entries(tts.voices || {}).map(
    ([id, meta]) => ({ id, language: (meta && meta.language) || '' }));

  // The packs kokoro-js REFUSES to name (AC-159). Kokoro-82M is multilingual
  // and 54 voice packs ship in node_modules; its JS wrapper hardcodes a table
  // of the 28 English ones and `_validate_voice` throws for the rest.
  // MEASURED 2026-09-10: 54 on disk, 28 in the table, 26 unreachable --
  // pf_dora, pm_alex and pm_santa among them.
  //
  // `tts-phonemes.LANGUAGES` decides which of those 26 are OFFERED, and it is
  // deliberately short: a voice nobody has listened to is a control that half
  // works (AC-7b). Read from the directory rather than from a list here, for
  // the AC-42 reason -- a list written beside the engine is wrong the day the
  // packs change, and wrong invisibly.
  const known = new Set(voiceList.map((v) => v.id));
  for (const id of await extraVoicePacks(voicesDir)) {
    if (known.has(id)) continue;
    const language = languageOf(id);
    if (language) voiceList.push({ id, language });
  }
  return { tts, voiceList };
}
