/**
 * host.js -- the terminal host: a detached Node process that owns every PTY
 * of one data root, so a daemon restart does not end the shells.
 *
 *   node host.js --terminals-dir=<records>/terminals
 *
 * Started by the daemon (daemon/terminals.py) when no host
 * answers on the pipe; plain `node`, no loader. Protocol: protocol.js. The
 * PTYs and everything that watches their input and output: pty-sessions.js.
 *
 * Lifetime: it lives while it has a shell or a client. With neither for
 * IDLE_EXIT_MS it exits by itself. A `shutdown` request (the daemon's
 * host restart) ends every shell, then exits.
 *
 * It never unlinks a file it does not own: the token/info files are removed
 * on exit only while they still name THIS process, and a second host that
 * finds the pipe taken exits without touching anything (logging who it
 * thinks owns the pipe).
 *
 * The token never crosses the pipe: the hello is a mutual HMAC proof
 * (protocol.js). On Windows the token file is restricted to the current user
 * (icacls), because mode 0o600 means nothing there and the records
 * terminals folder can grant other local accounts read access.
 */
import childProcess from 'child_process';
import crypto from 'crypto';
import fsp from 'fs/promises';
import net from 'net';
import path from 'path';
import {
  PROTOCOL_VERSION,
  IDLE_EXIT_MS,
  MAX_LINE_BYTES,
  hostPaths,
  computeCodeHash,
  encode,
  createLineDecoder,
  newNonce,
  isNonce,
  proof,
  proofMatches,
} from './protocol.js';
import { PtySessionTable, HostOpError } from './pty-sessions.js';

/** A connection must say a valid hello within this long, or it is dropped. */
const HELLO_TIMEOUT_MS = 5000;
/**
 * A client whose socket buffers more than this unsent is too slow to keep
 * up with the shells' output; it is disconnected (it reconnects and gets a
 * fresh snapshot) rather than letting the host's memory grow without bound.
 */
const MAX_CLIENT_BACKLOG_BYTES = 64 * 1024 * 1024;
const IDLE_CHECK_MS = 30 * 1000;

function arg(name) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : null;
}

const log = (...a) => console.log(new Date().toISOString(), ...a);

const terminalsDir = arg('terminals-dir');
if (!terminalsDir) {
  console.error('[Host] --terminals-dir=<dir> is required');
  process.exit(2);
}

// node-pty's Windows kill() forks a helper (conpty_console_list_agent) with
// default options. This process is DETACHED and has no console, so Windows
// would give that console-app child a NEW, VISIBLE console window -- a flash
// on every terminal close. Default windowsHide for every fork in this process.
if (process.platform === 'win32') {
  /** @type {any} */
  const cp = childProcess;
  const fork = cp.fork;
  cp.fork = (/** @type {any} */ modulePath, /** @type {any} */ args, /** @type {any} */ options) => {
    if (args && !Array.isArray(args)) return fork(modulePath, { windowsHide: true, ...args });
    return fork(modulePath, args, { windowsHide: true, ...options });
  };
}

const paths = hostPaths(terminalsDir);
const codeHash = await computeCodeHash();
const token = crypto.randomBytes(32).toString('hex');

/** @type {Set<net.Socket>} sockets that completed hello */
const clients = new Set();

function broadcast(msg) {
  const line = encode({ type: 'event', ...msg });
  if (Buffer.byteLength(line) > MAX_LINE_BYTES) {
    // A client closes the pipe on an over-limit line; dropping this event
    // costs one event, sending it would cost every terminal its connection.
    log(`[Host] DROPPED a ${msg.event} event for ${msg.id}: ${Buffer.byteLength(line)} bytes is past the ${MAX_LINE_BYTES} byte line limit`);
    return;
  }
  for (const sock of clients) {
    if (sock.writableLength > MAX_CLIENT_BACKLOG_BYTES) {
      log(`[Host] Disconnecting a client ${sock.writableLength} bytes behind`);
      sock.destroy();
      continue;
    }
    sock.write(line);
  }
}

const table = new PtySessionTable({ onEvent: broadcast });

let lastBusyAt = Date.now();
let exiting = false;

/** Remove token/info only while they still name this process. */
async function removeOwnFiles() {
  try {
    const info = JSON.parse(await fsp.readFile(paths.info, 'utf8'));
    if (info.pid !== process.pid) return;
    await fsp.rm(paths.info, { force: true });
    const onDisk = await fsp.readFile(paths.token, 'utf8').catch(() => null);
    if (onDisk === token) await fsp.rm(paths.token, { force: true });
  } catch (err) {
    if (err.code !== 'ENOENT') log('[Host] Could not remove own files:', err.message);
  }
}

async function exitHost(code, why) {
  if (exiting) return;
  exiting = true;
  log(`[Host] Exiting (${why})`);
  server.close();
  await removeOwnFiles();
  // Give queued replies a moment to flush before the sockets die with us.
  setTimeout(() => process.exit(code), 100);
}

const OPS = {
  spawn: (m) => table.spawn(m),
  write: (m) => table.write(m),
  resize: (m) => table.resize(m),
  kill: (m) => table.kill(m),
  snapshot: (m) => table.snapshot(m),
  list: () => ({ sessions: table.list() }),
  shutdown: async () => {
    const r = await table.shutdown();
    // After the reply is queued, so the client learns it happened.
    setImmediate(() => exitHost(0, 'shutdown requested'));
    return r;
  },
};

async function handleRequest(sock, msg) {
  const op = OPS[msg.op];
  let reply;
  try {
    if (!op) throw new HostOpError('bad_op', `Unknown op ${JSON.stringify(msg.op)}`);
    reply = { type: 'reply', rid: msg.rid, ok: true, result: (await op(msg)) ?? {} };
  } catch (err) {
    if (!(err instanceof HostOpError)) log(`[Host] ${msg.op} failed:`, err.stack || err.message);
    reply = { type: 'reply', rid: msg.rid, ok: false, error: { code: err.code || 'failed', message: err.message } };
  }
  let line = encode(reply);
  const bytes = Buffer.byteLength(line);
  if (bytes > MAX_LINE_BYTES) {
    // Never kill the pipe with an answer: the client closes it on an over-limit line.
    log(`[Host] ${msg.op} reply is ${bytes} bytes, past the ${MAX_LINE_BYTES} byte line limit; answering an error instead`);
    line = encode({ type: 'reply', rid: msg.rid, ok: false, error: { code: 'reply_too_large', message: `the ${msg.op} reply was ${bytes} bytes, past the line limit` } });
  }
  if (!sock.destroyed) sock.write(line);
}

const server = net.createServer((sock) => {
  /** @type {'hello' | 'auth' | 'open'} */
  let phase = 'hello';
  /** the nonce this host chose for the connection; the client must prove the token over it */
  let hostNonce = '';
  const refuse = (code, message) => {
    phase = 'hello';
    sock.end(encode({ type: 'error', code, message }));
  };
  const helloTimer = setTimeout(() => { if (phase !== 'open') refuse('bad_hello', 'no hello in time'); }, HELLO_TIMEOUT_MS);
  const decode = createLineDecoder((msg) => {
    if (phase === 'hello') {
      if (msg?.type !== 'hello') return refuse('bad_hello', 'the first message must be hello');
      if (msg.version !== PROTOCOL_VERSION) return refuse('bad_version', `this host speaks protocol ${PROTOCOL_VERSION}`);
      if (!isNonce(msg.nonce)) return refuse('bad_hello', 'hello needs a 64-hex nonce');
      // Prove the token first (the client checks it before saying anything else), then ask for its proof.
      hostNonce = newNonce();
      phase = 'auth';
      sock.write(encode({ type: 'challenge', nonce: hostNonce, proof: proof(token, 'host', msg.nonce) }));
      return;
    }
    if (phase === 'auth') {
      clearTimeout(helloTimer);
      if (msg?.type !== 'auth' || !proofMatches(msg.proof, proof(token, 'client', hostNonce))) {
        log('[Host] Refused a client that could not prove the token');
        return refuse('bad_token', 'wrong token proof');
      }
      phase = 'open';
      clients.add(sock);
      sock.write(encode({ type: 'hello_ok', hostPid: process.pid, version: PROTOCOL_VERSION, codeHash, sessions: table.list() }));
      return;
    }
    if (msg?.type === 'request' && Number.isInteger(msg.rid)) {
      handleRequest(sock, msg).catch((err) => log('[Host] request handling failed:', err.message));
    }
  }, (err) => {
    log('[Host] Protocol error, closing the connection:', err.message);
    sock.destroy();
  });
  sock.on('data', decode);
  sock.on('error', () => { /* the close handler cleans up */ });
  sock.on('close', () => {
    clearTimeout(helloTimer);
    if (clients.delete(sock)) lastBusyAt = Date.now();
  });
});

/**
 * Who the info file says owns this terminals directory's host, for the log
 * of a host that lost the pipe race. A pid that is not running means the pipe
 * is held by something that is NOT a host of ours (a squatter, or a host of
 * another install keyed to the same path).
 */
async function describePipeOwner() {
  let info;
  try {
    info = JSON.parse(await fsp.readFile(paths.info, 'utf8'));
  } catch (err) {
    return `no readable ${paths.info} (${err.code || err.message}): the pipe may be held by a process that is not a terminal host`;
  }
  let alive = false;
  try { process.kill(info.pid, 0); alive = true; } catch { /* not running */ }
  const started = typeof info.startedAt === 'number' ? new Date(info.startedAt).toISOString() : 'unknown';
  return alive
    ? `${paths.info} names host pid ${info.pid} (started ${started}), which is running`
    : `${paths.info} names host pid ${info.pid} (started ${started}), which is NOT running: the pipe is held by some other process`;
}

/**
 * Restrict a file to the current user (Windows): inheritance removed, one
 * full-control entry for this user's SID. On other platforms the 0o600 mode
 * it was written with already does this.
 */
async function restrictToCurrentUser(file) {
  if (process.platform !== 'win32') return;
  if (!process.env.SystemRoot) throw new Error('SystemRoot is not set, so icacls.exe cannot be located');
  const sys32 = path.join(process.env.SystemRoot, 'System32');
  const run = (exe, args) => new Promise((resolve, reject) => {
    childProcess.execFile(path.join(sys32, exe), args, { windowsHide: true }, (err, stdout, stderr) => {
      if (err) reject(new Error(`${exe} ${args.join(' ')} failed: ${String(stderr || err.message).trim()}`));
      else resolve(String(stdout));
    });
  });
  const who = await run('whoami.exe', ['/user', '/fo', 'csv', '/nh']);
  const sid = /"(S-1-[0-9-]+)"/.exec(who)?.[1];
  if (!sid) throw new Error(`no SID in whoami output: ${who.trim()}`);
  await run('icacls.exe', [file, '/inheritance:r', '/grant:r', `*${sid}:F`]);
}

server.on('error', (/** @type {NodeJS.ErrnoException} */ err) => {
  if (err.code === 'EADDRINUSE') {
    // Another process already serves this pipe. Its files are its own.
    describePipeOwner().then((owner) => {
      log(`[Host] ${paths.pipe} is already taken; ${owner}. Exiting without touching any file`);
      process.exit(0);
    });
    return;
  }
  log('[Host] Server error:', err.stack || err.message);
  process.exit(1);
});

/**
 * macOS: a Unix socket file outlives a host that died. If the file exists and
 * nobody accepts on it, it is stale and is removed so listen() can bind; if
 * someone accepts, it is a live host and listen() will lose the race as usual.
 */
async function clearStaleSocket() {
  if (process.platform === 'win32') return;
  if (!(await fsp.stat(paths.pipe).then(() => true, () => false))) return;
  const live = await new Promise((resolve) => {
    const probe = net.connect(paths.pipe);
    probe.once('connect', () => { probe.destroy(); resolve(true); });
    probe.once('error', () => resolve(false));
  });
  if (!live) await fsp.rm(paths.pipe, { force: true });
}
await fsp.mkdir(terminalsDir, { recursive: true });
await clearStaleSocket();

server.listen(paths.pipe, async () => {
  // Only AFTER winning the pipe: a host that lost the race must not overwrite
  // the winner's token. Written to a temp name, restricted, THEN renamed into
  // place, so the token is never readable by another account even briefly.
  await fsp.mkdir(terminalsDir, { recursive: true });
  const tmpToken = `${paths.token}.${process.pid}.tmp`;
  await fsp.writeFile(tmpToken, token, { mode: 0o600 });
  try {
    await restrictToCurrentUser(tmpToken);
  } catch (err) {
    log(`[Host] SECURITY WARNING: could not restrict ${paths.token} to the current user (${err.message}). Any local account that can read ${terminalsDir} can read the token and drive these shells.`);
  }
  await fsp.rename(tmpToken, paths.token);
  await fsp.writeFile(paths.info, JSON.stringify({
    pid: process.pid, version: PROTOCOL_VERSION, startedAt: Date.now(), codeHash, pipe: paths.pipe,
  }));
  log(`[Host] Listening on ${paths.pipe} (pid ${process.pid}, protocol ${PROTOCOL_VERSION}, code ${codeHash.slice(0, 12)})`);
});

const idleTimer = setInterval(() => {
  if (table.size > 0 || clients.size > 0) { lastBusyAt = Date.now(); return; }
  if (Date.now() - lastBusyAt >= IDLE_EXIT_MS) exitHost(0, `idle: no shells and no clients for ${IDLE_EXIT_MS / 60000} min`);
}, IDLE_CHECK_MS);
idleTimer.unref();

// Fail fast: a host in an unknown state is worse than a dead one. Its shells
// die with it (ConPTY closes); the daemon sees the pipe go and marks every
// terminal ended.
process.on('uncaughtException', (err) => {
  log('[Host] FATAL uncaught exception:', err.stack || err.message);
  process.exit(1);
});
process.on('unhandledRejection', (/** @type {any} */ err) => {
  log('[Host] FATAL unhandled rejection:', err?.stack || err);
  process.exit(1);
});
