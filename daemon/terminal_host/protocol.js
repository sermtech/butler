/**
 * protocol.js -- what the terminal host and its clients agree on.
 *
 * The host (host.js) is a detached Node process that owns every PTY, so a
 * dashboard restart does not end the shells. It listens on a VERSIONED named
 * pipe whose name is derived from the terminals directory it serves. Both
 * sides hold the token the host wrote beside it, and each PROVES that to the
 * other with an HMAC over a nonce the other chose -- the token itself never
 * crosses the pipe, so a process that squats the pipe (it is free while no
 * host runs) learns nothing and is refused. Messages are NDJSON, one JSON
 * object per line, both ways.
 *
 *   client -> host   {type:'hello', version, nonce}
 *   host -> client   {type:'challenge', nonce, proof: proof(token, 'host', <client nonce>)}
 *                    | {type:'error', code:'bad_version'|'bad_hello', message} then close
 *   client -> host   {type:'auth', proof: proof(token, 'client', <host nonce>)}
 *                    (the client checks the host's proof FIRST and sends nothing more to a host that failed it)
 *   host -> client   {type:'hello_ok', hostPid, version, codeHash, sessions}
 *                    | {type:'error', code:'bad_token', message} then close
 *   client -> host   {type:'request', rid, op, ...params}
 *   host -> client   {type:'reply', rid, ok:true, result} | {type:'reply', rid, ok:false, error:{code, message}}
 *   (`rid` is the request id; `id` is always a terminal id, a param of most ops)
 *   host -> client   {type:'event', event:'output', id, seq, data}
 *                    {type:'event', event:'exit', id, exitCode, reason}
 *
 * ops: spawn, write, resize, kill, snapshot, list, shutdown.
 * `seq` is per terminal id and only ever increases within one host, across
 * re-spawns under the same id, so a snapshot's seq tells a client exactly
 * which output events it already contains.
 *
 * No line either side sends may exceed MAX_LINE_BYTES: the receiver closes
 * the connection on one. Writes are split (splitForWrite), snapshots are trimmed to MAX_SNAPSHOT_JSON_BYTES, and both sides refuse to
 * send any other line past the limit rather than kill the pipe with it.
 *
 * Bump PROTOCOL_VERSION on ANY incompatible change: the pipe and the token
 * file both carry it, so an old host keeps its own pipe and is left alone.
 */
import crypto from 'crypto';
import fsp from 'fs/promises';
import { builtinModules } from 'module';
import path from 'path';
import { fileURLToPath } from 'url';

/** v2: challenge-response hello (ported from an earlier prototype's v2). */
export const PROTOCOL_VERSION = 2;

/**
 * A host with NO sessions and NO connected client for this long exits by
 * itself, so a host left behind by a test, a crashed server or a data root
 * nobody uses any more does not live forever. A host with even one shell
 * never idles out (its shells are the point).
 */
export const IDLE_EXIT_MS = 10 * 60 * 1000;

/**
 * The largest NDJSON line either side accepts; past it the receiver closes
 * the connection rather than buffer without bound. Nothing legitimate comes
 * near it: see MAX_SNAPSHOT_JSON_BYTES and WRITE_CHUNK_CHARS.
 */
export const MAX_LINE_BYTES = 32 * 1024 * 1024;

/**
 * Budget for one serialized screen (a snapshot reply)
 * measured AS JSON, i.e. after escaping: every ESC becomes six bytes, so a
 * 500x200 truecolor screen with 2000 history rows measured 43.4 MB. Past the
 * budget the OLDEST history rows are dropped until it fits (loudly logged).
 * Trimming rather than chunking: a chunked reply would have to interleave
 * with the seq-numbered output events and be reassembled on both sides, for
 * history no browser view keeps anyway (its xterm holds 5000 rows).
 */
export const MAX_SNAPSHOT_JSON_BYTES = MAX_LINE_BYTES / 2;

/**
 * Characters per `write` request. JSON escapes a control byte to six bytes,
 * so the worst line is ~6 MB: a paste of any size goes as several requests,
 * never as one line that would kill the pipe.
 */
export const WRITE_CHUNK_CHARS = 1024 * 1024;

/** host.log is truncated when it is larger than this at host spawn. */
export const HOST_LOG_MAX_BYTES = 4 * 1024 * 1024;

const here = path.dirname(fileURLToPath(import.meta.url));

/** host.js itself. */
export const HOST_ENTRY = path.join(here, 'host.js');

/** Static `import ... from '<spec>'` / `export ... from '<spec>'` / `import '<spec>'` at a line start. */
const STATIC_IMPORT = /^\s*(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]/gm;

/** `@scope/name/sub` -> `@scope/name`; `name/sub` -> `name`. */
function packageName(spec) {
  const parts = spec.split('/');
  return spec.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}

/** The version of `pkg` as Node would resolve it from `fromDir` (nearest node_modules up the tree). */
async function packageVersion(pkg, fromDir) {
  for (let dir = fromDir; ; dir = path.dirname(dir)) {
    try {
      return JSON.parse(await fsp.readFile(path.join(dir, 'node_modules', pkg, 'package.json'), 'utf8')).version;
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
    if (path.dirname(dir) === dir) throw new Error(`terminal host imports ${pkg}, which is not installed above ${fromDir}`);
  }
}

/**
 * Everything whose code runs IN the host, derived from its real import graph
 * rather than a hand list (a hand list missed file-ops.js's own imports):
 * every file reachable from host.js by static relative imports, and every
 * package it imports with the installed version, so a dependency upgrade
 * makes the running host stale too. Type-only `import('...')` in JSDoc is not
 * an import and is not followed.
 * @returns {Promise<{ files: string[], packages: string[] }>} absolute paths and `name@version`, sorted
 */
export async function hostSourceGraph() {
  const files = new Set();
  /** @type {Map<string, string>} package -> the dir it is resolved from */
  const packages = new Map();
  const queue = [HOST_ENTRY];
  while (queue.length) {
    const file = /** @type {string} */ (queue.shift());
    if (files.has(file)) continue;
    files.add(file);
    const src = await fsp.readFile(file, 'utf8');
    for (const m of src.matchAll(STATIC_IMPORT)) {
      const spec = m[1];
      if (spec.startsWith('.')) queue.push(path.resolve(path.dirname(file), spec));
      else if (!spec.startsWith('node:') && !builtinModules.includes(spec)) {
        const name = packageName(spec);
        if (!packages.has(name)) packages.set(name, path.dirname(file));
      }
    }
  }
  const versions = await Promise.all([...packages].map(async ([name, dir]) => `${name}@${await packageVersion(name, dir)}`));
  return { files: [...files].sort(), packages: versions.sort() };
}

/**
 * sha256 over hostSourceGraph(): every file (by its path under the repo, then
 * its bytes) and every `package@version`. The host hashes it when it starts,
 * the server hashes the disk when it connects; a difference means the running
 * host is older than the code (it keeps running -- shells live in it -- and is
 * reported stale).
 * @returns {Promise<string>}
 */
export async function computeCodeHash() {
  const { files, packages } = await hostSourceGraph();
  const root = path.resolve(here, '..', '..');
  const h = crypto.createHash('sha256');
  for (const file of files) {
    h.update(path.relative(root, file).replace(/\\/g, '/'));
    h.update('\0');
    h.update(await fsp.readFile(file));
    h.update('\0');
  }
  for (const p of packages) {
    h.update(p);
    h.update('\0');
  }
  return h.digest('hex');
}

// ---------------------------------------------------------------------------
// The hello: each side proves it holds the token, neither sends it
// ---------------------------------------------------------------------------

/** A fresh 32-byte nonce, hex. */
export function newNonce() {
  return crypto.randomBytes(32).toString('hex');
}

/** True for a nonce as newNonce makes them. */
export function isNonce(value) {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
}

/**
 * Proof that the sender holds `token`: HMAC-SHA256 over the OTHER side's
 * nonce. The role label keeps a host's proof from ever passing as a
 * client's (a client cannot use the host as an oracle for its own proof).
 * @param {string} token
 * @param {'host' | 'client'} role - who is proving
 * @param {string} nonce - the nonce the other side chose
 */
export function proof(token, role, nonce) {
  return crypto.createHmac('sha256', token).update(`butler-terminal-host:${role}:${nonce}`).digest('hex');
}

/** Constant-time comparison of a received proof with the expected one. */
export function proofMatches(given, expected) {
  if (typeof given !== 'string') return false;
  const a = Buffer.from(given, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/**
 * Input split into `write` payloads of at most WRITE_CHUNK_CHARS, in order,
 * never between the two halves of a surrogate pair (a lone half would be
 * replaced by U+FFFD when encoded).
 * @param {string} data
 * @returns {string[]}
 */
export function splitForWrite(data) {
  if (data.length <= WRITE_CHUNK_CHARS) return [data];
  const out = [];
  let i = 0;
  while (i < data.length) {
    let end = Math.min(i + WRITE_CHUNK_CHARS, data.length);
    const last = data.charCodeAt(end - 1);
    if (end < data.length && last >= 0xd800 && last <= 0xdbff) end--;
    out.push(data.slice(i, end));
    i = end;
  }
  return out;
}

/**
 * Where one terminals directory's host lives. The pipe is keyed by the
 * RESOLVED terminals directory -- the directory the host owns (its token, its
 * log) -- so two data roots never share a host and
 * a test's temp root never reaches the live one.
 * @param {string} terminalsDir
 */
const BS = String.fromCharCode(92);
/** The Windows named-pipe namespace prefix. */
const PIPE_PREFIX = BS + BS + '.' + BS + 'pipe' + BS;

export function hostPaths(terminalsDir) {
  const resolved = path.resolve(terminalsDir);
  const key = crypto.createHash('sha256').update(resolved).digest('hex').slice(0, 12);
  const name = `butler-terminal-host-v${PROTOCOL_VERSION}-${key}`;
  return {
    // Windows: a named pipe keyed by the hash. Elsewhere: a Unix socket IN the terminals directory.
    pipe: process.platform === 'win32' ? PIPE_PREFIX + name : path.join(resolved, `host-v${PROTOCOL_VERSION}.sock`),
    token: path.join(terminalsDir, `host-v${PROTOCOL_VERSION}.token`),
    info: path.join(terminalsDir, `host-v${PROTOCOL_VERSION}.json`),
    log: path.join(terminalsDir, 'host.log'),
  };
}

/** A terminal size forced into the range the host and the server's mirror both accept. */
export function clampGeometry(cols, rows) {
  return {
    cols: Math.max(1, Math.min(500, Math.floor(cols))),
    rows: Math.max(1, Math.min(200, Math.floor(rows))),
  };
}

/** One message as an NDJSON line. */
export function encode(msg) {
  return JSON.stringify(msg) + '\n';
}

/**
 * An NDJSON line splitter for one socket. `onMessage` gets each parsed
 * object; `onError` gets a protocol fault (bad JSON, a line past
 * MAX_LINE_BYTES), after which the caller should close the socket.
 * @param {(msg: any) => void} onMessage
 * @param {(err: Error) => void} onError
 * @returns {(chunk: Buffer) => void}
 */
export function createLineDecoder(onMessage, onError) {
  /** @type {Buffer[]} */
  let parts = [];
  let size = 0;
  let failed = false;
  return (chunk) => {
    if (failed) return;
    let start = 0;
    for (;;) {
      const nl = chunk.indexOf(10, start);
      if (nl === -1) break;
      // The WHOLE line: what earlier chunks carried plus this chunk's part.
      if (size + (nl - start) > MAX_LINE_BYTES) {
        failed = true;
        onError(new Error(`NDJSON line larger than ${MAX_LINE_BYTES} bytes`));
        return;
      }
      parts.push(chunk.subarray(start, nl));
      const line = Buffer.concat(parts).toString('utf8');
      parts = [];
      size = 0;
      start = nl + 1;
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch (err) {
        failed = true;
        onError(new Error(`bad NDJSON line: ${err.message}`));
        return;
      }
      onMessage(msg);
    }
    if (start < chunk.length) {
      size += chunk.length - start;
      if (size > MAX_LINE_BYTES) {
        failed = true;
        onError(new Error(`NDJSON line larger than ${MAX_LINE_BYTES} bytes`));
        return;
      }
      parts.push(chunk.subarray(start));
    }
  };
}
