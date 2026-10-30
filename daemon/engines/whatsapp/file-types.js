/**
 * File kind and MIME by extension, ported from an earlier prototype's lib/file-types.js and
 * trimmed to what an outbound send needs (`kindOf`, `mimeOf`). PURE: no fs.
 * An extension in NO table is UNKNOWN (`kindOf` returns null); this module
 * never guesses.
 */

/** @typedef {'text'|'markdown'|'image'|'pdf'|'video'|'audio'|'binary'} FileKind */

/** @param {string[]} list */
const sorted = (list) => Object.freeze([...new Set(list)].sort());

const MARKDOWN = sorted(['md', 'markdown']);
const IMAGE = sorted(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'bmp', 'avif']);
const PDF = sorted(['pdf']);
const VIDEO = sorted(['mp4', 'webm', 'mov', 'm4v', 'ogv']);
const AUDIO = sorted(['mp3', 'wav', 'ogg', 'oga', 'm4a', 'aac', 'flac', 'opus']);
// Union of the old server TEXT_EXTENSIONS and the client CODE_EXTS, plus
// jsonl/ini/toml/tsv. md/markdown are their own kind and svg is an image.
const TEXT = sorted([
  'txt', 'json', 'jsonl', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx',
  'css', 'scss', 'less', 'html', 'htm', 'xml', 'yaml', 'yml',
  'py', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs', 'rb', 'php',
  'sh', 'bash', 'bat', 'ps1', 'cmd', 'sql', 'log', 'env', 'gitignore',
  'gitattributes', 'editorconfig', 'prettierrc', 'eslintrc', 'toml', 'ini',
  'cfg', 'conf', 'vue', 'svelte', 'astro', 'lock', 'csv', 'tsv',
]);
// Known to be binary: shown as a file card with Download, never decoded as text.
// HEIC/HEIF and Matroska are here because Chrome cannot render them inline.
const BINARY = sorted([
  'docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt', 'odt', 'ods', 'odp', 'rtf',
  'zip', '7z', 'rar', 'gz', 'tgz', 'tar', 'bz2', 'xz',
  'exe', 'dll', 'so', 'dylib', 'msi', 'bin', 'iso', 'dmg', 'class', 'jar', 'pyc', 'wasm',
  'heic', 'heif', 'psd', 'tif', 'tiff', 'mkv', 'avi', 'wmv', 'flv',
  'woff', 'woff2', 'ttf', 'otf', 'eot', 'db', 'sqlite', 'parquet',
]);

const FILE_KIND_EXTENSIONS = Object.freeze({
  markdown: MARKDOWN,
  image: IMAGE,
  pdf: PDF,
  video: VIDEO,
  audio: AUDIO,
  text: TEXT,
  binary: BINARY,
});

/** @type {Map<string, FileKind>} */
const KIND_BY_EXT = new Map();
for (const [kind, exts] of Object.entries(FILE_KIND_EXTENSIONS)) {
  for (const ext of exts) KIND_BY_EXT.set(ext, /** @type {FileKind} */ (kind));
}

/** The one MIME map. Anything absent is application/octet-stream. */
export const FILE_MIME_TYPES = Object.freeze({
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  svg: 'image/svg+xml', webp: 'image/webp', ico: 'image/x-icon', bmp: 'image/bmp',
  avif: 'image/avif',
  pdf: 'application/pdf',
  mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime', m4v: 'video/x-m4v',
  ogv: 'video/ogg', mkv: 'video/x-matroska',
  mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', oga: 'audio/ogg',
  m4a: 'audio/mp4', aac: 'audio/aac', flac: 'audio/flac', opus: 'audio/ogg',
  md: 'text/markdown', markdown: 'text/markdown',
  txt: 'text/plain', log: 'text/plain', csv: 'text/csv', tsv: 'text/tab-separated-values',
  json: 'application/json', jsonl: 'application/jsonl',
  html: 'text/html', htm: 'text/html', css: 'text/css', xml: 'application/xml',
  js: 'text/javascript', mjs: 'text/javascript', cjs: 'text/javascript',
  yaml: 'text/yaml', yml: 'text/yaml',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  doc: 'application/msword', xls: 'application/vnd.ms-excel', ppt: 'application/vnd.ms-powerpoint',
  zip: 'application/zip', gz: 'application/gzip', '7z': 'application/x-7z-compressed',
  heic: 'image/heic', heif: 'image/heif',
});

/**
 * Lowercase extension without the dot; '' when there is none. A leading-dot
 * name (".env") has no extension -- the same rule as the client's `extOf`.
 * @param {string} name
 */
export function extOf(name) {
  const base = String(name).split(/[\\/]/).pop() || '';
  const dot = base.lastIndexOf('.');
  if (dot <= 0) return '';
  return base.slice(dot + 1).toLowerCase();
}

/**
 * The kind an extension declares, or null when the extension is UNKNOWN.
 * @param {string} name
 * @returns {FileKind | null}
 */
export function kindOf(name) {
  return KIND_BY_EXT.get(extOf(name)) ?? null;
}

/** @param {string} name */
export function mimeOf(name) {
  return FILE_MIME_TYPES[extOf(name)] ?? 'application/octet-stream';
}
