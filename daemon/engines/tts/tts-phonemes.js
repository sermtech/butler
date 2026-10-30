/**
 * Text -> Kokoro phonemes, for the languages kokoro-js will not do itself.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Kokoro-82M is a multilingual model and its 54 voice packs ship in this
 * repo's node_modules, Brazilian Portuguese among them. `kokoro-js` reaches
 * none of them: its voice table is 28 hardcoded English entries and
 * `_validate_voice` throws for anything outside it, and the `phonemizer`
 * package it calls is an eSpeak NG build compiled with ENGLISH DATA ONLY.
 * MEASURED 2026-09-10:
 *
 *   phonemize('Bom dia, como vai voce?', 'pt-br')
 *     -> Invalid language identifier: "pt-br". Should be one of:
 *        en, en-029, en-gb, ..., en-us, en-us-nyc
 *
 * Forced through en-us, the same Portuguese sentence takes 6.90 s of audio
 * against 4.47 s done properly -- a 54% inflation, which is what spelling
 * Portuguese words with English sounds sounds like.
 *
 * So this module does the phonemization with the FULL eSpeak NG build
 * (`espeak-ng` on npm, all languages) and hands the result to Kokoro's own
 * tokenizer. Nothing here is a fork: `KokoroTTS.generate_from_ids` is public
 * and never calls `_validate_voice`, and the voice loader reads
 * `../voices/<name>.bin` for any name. The same public-constructor route
 * `tts-model-loader.js` beside this file already takes, for the same reason
 * and with its reasoning written down there. An `npm install` cannot revert
 * it, which a patch to kokoro-js would not survive.
 *
 * WHAT IS AND IS NOT OFFERED
 * --------------------------
 * `LANGUAGES` is deliberately SHORTER than the set of packs on disk. A voice
 * in this table is one the owner can pick and be understood by; a pack whose
 * prefix is absent is not offered at all, because a picker that lists a voice
 * nobody has listened to is the control-that-half-works failure this project
 * keeps paying for (AC-7b).
 *
 * The gate for adding a row is TWO things, and the second is the one that
 * gets skipped: eSpeak NG must be upstream Kokoro's own G2P for that language
 * (misaki uses cutlet for Japanese and pypinyin for Mandarin, so espeak's
 * output for `j*` and `z*` packs is tokenizable and WRONG), and somebody must
 * have listened to it. Only `p` has been listened to here.
 *
 * MEASURED 2026-09-10, phoneme coverage against Kokoro's 115-token vocab:
 * a, b, e, h, i, j, p all clean; f (French) emits one "-" and z (Mandarin)
 * one "5" that the vocab does not hold. Coverage is necessary and not
 * sufficient -- j is clean and still wrong, for the G2P reason above.
 *
 * COST
 * ----
 * MEASURED 2026-09-10: 116 ms per sentence, each call instantiating the WASM
 * module afresh, because the build exports no `callMain` to reuse one. One
 * call carrying four sentences costs 128 ms total (32 ms each) and eSpeak
 * emits exactly one output line per input sentence, so batching a whole reply
 * would be nearly four times cheaper per sentence -- and is NOT done, because
 * batching needs the complete reply before synthesis starts and would throw
 * away the streaming that took first audio from 3925 ms to 673 ms.
 */
import ESpeakNg from 'espeak-ng';

// Kokoro's own `lang_code` prefix -> what it means and how to phonemize it.
// The prefix convention is the ENGINE's, which is why this table lives here
// beside the engine and not in the daemon: a language read off a name in
// daemon/ is what the AC-41 grep gate forbids.
export const LANGUAGES = {
  a: { language: 'en-us', espeak: 'en-us' },
  b: { language: 'en-gb', espeak: 'en-gb' },
  p: { language: 'pt-br', espeak: 'pt-br' },
};

// eSpeak's `--ipa=3` joins tie-bar pairs with a ZERO WIDTH JOINER, which is
// the ONE character in its output that Kokoro's vocab does not hold
// (measured: every other Portuguese phoneme is already in the 115 tokens).
// Upstream misaki strips it the same way. Written as an escape because
// nothing in this repo's source may carry a non-ASCII byte.
const ZERO_WIDTH_JOINER = new RegExp('\\u200d', 'g');

/**
 * The language a voice pack speaks, or null if this build will not offer it.
 * @param {string} voiceId
 */
export function languageOf(voiceId) {
  const entry = LANGUAGES[(voiceId || '')[0]];
  return entry ? entry.language : null;
}

/**
 * IPA for `text` in `language`, ready for Kokoro's tokenizer.
 *
 * Throws rather than falling back to English. A phonemizer that quietly
 * answered in the wrong language would produce audio of the right length and
 * the wrong sounds -- which passes every automated check there is and is only
 * detectable by ear.
 *
 * @param {string} text
 * @param {string} language one of LANGUAGES[*].language
 * @returns {Promise<string>}
 */
export async function phonemize(text, language) {
  const entry = Object.values(LANGUAGES).find((l) => l.language === language);
  if (!entry) {
    throw new Error(
      `no phonemizer for language ${JSON.stringify(language)}; this build `
      + `offers ${Object.values(LANGUAGES).map((l) => l.language).join(', ')}`);
  }
  // `-b`, `1` AS TWO ARGV ELEMENTS. That flag names the input encoding and 1
  // means UTF-8. It used to read `-b=1`, and the `=` is the entire defect
  // (D-37): getopt hands the short option an optarg of "=1", `atoi` returns
  // 0, and espeak-ng falls back to SINGLE-BYTE input -- so the UTF-8 bytes
  // Emscripten puts into argv are read as Latin-1. Every accented letter
  // becomes mojibake and espeak then pronounces the NAME of each character it
  // thinks it found: an a-tilde as "a til", a cedilla-and-tilde pair as
  // "libra" and "simbolo de copyright" and "ordinal feminino".
  //
  // MEASURED 2026-09-10 on the shipped build, phonemizing c-cedilla-a-tilde:
  //
  //   -b=1  -b=2   broken -- the `=` makes the value irrelevant
  //   -b 1  -b1    CORRECT (1 is UTF-8)
  //   -b 2  -b2    broken, and correctly so: 2 means 8-bit
  //   no -b        correct, by auto-detection
  //
  // So the original author meant UTF-8 and lost it to one character. Written
  // explicitly rather than dropped, because auto-detection is a heuristic on
  // the text and this is a fact about the caller: everything arriving here is
  // a JS string, which is always valid UTF-8.
  //
  // ENGLISH IS BYTE-IDENTICAL either way, which is why this survived from
  // before the Portuguese voice shipped until somebody spoke Portuguese at
  // it. End to end through the real engine, one Portuguese sentence went from
  // 9.53 s of audio to 2.95 s.
  // NFC, and this is the SECOND way an accent gets lost (D-37). A decomposed
  // a-tilde is TWO codepoints -- a bare `a` followed by a combining tilde --
  // and espeak-ng reads the base letter and discards the mark. MEASURED
  // 2026-09-10, the same sentence in both forms: NFC gives the nasal vowel,
  // NFD turns "nao-with-tilde" into "nau" and drops every other accent too.
  // Nothing upstream normalises, so whatever the model emitted is what
  // arrives here, and a model may emit either form -- which is what makes
  // this one INTERMITTENT where the argv defect above was constant.
  const normalized = text.normalize('NFC');
  const mod = await ESpeakNg({
    arguments: ['--phonout', 'out', '--sep=""', '-q', '-b', '1', '--ipa=3',
                '-v', entry.espeak, normalized],
  });
  return tieToKokoro(mod.FS.readFile('out', { encoding: 'utf8' }))
    .replace(ZERO_WIDTH_JOINER, '')
    .replace(/\n/g, ' ')
    .trim();
}


// espeak TIES the two halves of an affricate or diphthong with a joiner, and
// KOKORO WAS TRAINED ON THE SINGLE SYMBOL rather than on the pair. `misaki`,
// the G2P that Kokoro's own `pipeline.py` calls, collapses each tied pair to
// one codepoint before tokenizing. This table is that mapping. Butler used to
// delete the joiner and leave the two halves, which tokenizes perfectly
// cleanly and is simply not the sequence the model saw in training -- so the
// failure mode is confident WRONG SOUND, never an error.
//
// Every TARGET is one token in the shipped vocabulary, VERIFIED by asking the
// loaded tokenizer rather than by reading a file: ids 82, 133, 25, 39, 24, 31
// and 41. So is every half of every pair, which is exactly why nothing ever
// complained about the old form.
//
// THIS IS NOT PORTUGUESE-SPECIFIC. English uses all seven constantly -- judge,
// church, price, mouth, face, goat, choice -- so it changes the English voice
// too, which is why it wants an ear on it rather than an assumption.
//
// Built from ESCAPES and not literals: this file is pure ASCII by project
// rule, and the pairs carry a zero-width joiner that is invisible in an editor
// and could not be reviewed as a literal anyway.
const TIE = '\u200d';
const TIE_TO_KOKORO = [
  ['d' + TIE + '\u0292', '\u02a4'],   // d + ezh          -> dezh ligature
  ['t' + TIE + '\u0283', '\u02a7'],   // t + esh          -> tesh ligature
  ['a' + TIE + '\u026a', 'I'],        // a + small-cap I
  ['a' + TIE + '\u028a', 'W'],        // a + upsilon
  ['e' + TIE + '\u026a', 'A'],        // e + small-cap I
  ['o' + TIE + '\u028a', 'O'],        // o + upsilon
  ['\u0254' + TIE + '\u026a', 'Y'],   // open-o + small-cap I
];

function tieToKokoro(phonemes) {
  let out = phonemes;
  for (const [pair, single] of TIE_TO_KOKORO) {
    out = out.split(pair).join(single);
  }
  return out;
}
