/**
 * pty-sessions.js -- the live PTYs, as the terminal HOST owns them.
 *
 * Ported from an earlier prototype's terminal-host/pty-sessions.js, minus what Butler
 * does not need (checkpoints, shell-integration activity, Claude-resume
 * tracking): Butler's terminals survive a DAEMON restart, not a host death.
 * Each session is a node-pty shell plus one server-side screen
 * (emulator.js), so a reattaching view replays the screen, not a byte ring.
 *
 * `spawn` receives an already-resolved `file` and `args` from the daemon
 * (daemon/terminals.py builds them from the closed agent table); the host
 * never composes a command line itself.
 */
import * as pty from 'node-pty';
import { TerminalEmulator, VIEW_RESET } from './emulator.js';
import { clampGeometry, MAX_SNAPSHOT_JSON_BYTES } from './protocol.js';

/** A refusal the host answers with `{ ok: false, error: { code, message } }`. */
export class HostOpError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const noSession = (id) => new HostOpError('not_found', `No live terminal ${id}`);

/** Past node-pty's own 5 s fallback for killing a console's processes. */
const REAP_MS = 6000;

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (err) { return err.code === 'EPERM'; }
}

/**
 * End a shell, and make sure it ENDS. node-pty's Windows kill closes the
 * pseudoconsole at once but kills the console's processes only after an async
 * helper answers; a shell still running REAP_MS later is terminated by pid.
 * The pid is re-checked every 100 ms, so a pid that died is never killed.
 * @param {import('node-pty').IPty} p
 * @returns {Promise<void>}
 */
async function killShell(p) {
  try { p.kill(); } catch { /* already dead */ }
  const deadline = Date.now() + REAP_MS;
  while (Date.now() < deadline) {
    if (!pidAlive(p.pid)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!pidAlive(p.pid)) return;
  console.error(`[Host] Shell pid ${p.pid} was still running ${REAP_MS / 1000}s after its kill; terminating it`);
  try { process.kill(p.pid); } catch { /* already gone */ }
}

/**
 * node-pty (1.x) gives the Windows input pipe no 'error' listener, so killing
 * a shell with input still queued raised `write EOF` as an UNCAUGHT exception
 * and ended the host with every shell in it (an earlier prototype, measured). A node-pty
 * without that field fails the spawn loudly rather than run unguarded.
 * @param {import('node-pty').IPty} ptyProcess
 * @param {string} id
 */
function guardInputPipe(ptyProcess, id) {
  if (process.platform !== 'win32') return;
  const input = /** @type {any} */ (ptyProcess)._agent?.inSocket;
  if (!input || typeof input.on !== 'function') {
    try { ptyProcess.kill(); } catch { /* already dead */ }
    throw new Error('node-pty internals changed: no _agent.inSocket to guard (terminal_host/pty-sessions.js#guardInputPipe)');
  }
  input.on('error', (/** @type {Error} */ err) => console.error(`[Host] Input to ${id} failed: ${err.message}`));
}

export class PtySessionTable {
  /**
   * @param {{ onEvent: (event: { event: string, id: string, [k: string]: unknown }) => void }} opts
   */
  constructor({ onEvent }) {
    this._emit = onEvent;
    /** @type {Map<string, { pty: import('node-pty').IPty, emulator: TerminalEmulator, cwd: string, cols: number, rows: number }>} */
    this.sessions = new Map();
    /** Per-id output counter, so seq never repeats within one host. */
    this._seq = new Map();
    /** @type {Set<Promise<void>>} shells killed and not yet confirmed gone; shutdown waits for them */
    this._reaping = new Set();
  }

  get size() { return this.sessions.size; }

  /** @private */
  _get(id) {
    const s = this.sessions.get(id);
    if (!s) throw noSession(id);
    return s;
  }

  /** @private */
  _reap(p) {
    const r = killShell(p);
    this._reaping.add(r);
    r.finally(() => this._reaping.delete(r));
    return r;
  }

  /**
   * @param {{ id: string, file: string, args: string[], cwd: string, cols: number, rows: number }} p
   */
  spawn({ id, file, args, cwd, cols, rows }) {
    if (this.sessions.has(id)) throw new HostOpError('exists', `Terminal ${id} already has a shell`);
    if (typeof file !== 'string' || !file || !Array.isArray(args) || !args.every((a) => typeof a === 'string')) {
      throw new HostOpError('bad_request', 'spawn needs a file and a string args array');
    }
    const size = clampGeometry(cols, rows);
    const ptyProcess = pty.spawn(file, args, { name: 'xterm-256color', cols: size.cols, rows: size.rows, cwd, env: { ...process.env } });
    guardInputPipe(ptyProcess, id);
    const emulator = new TerminalEmulator(size);
    const session = { pty: ptyProcess, emulator, cwd, cols: size.cols, rows: size.rows };
    ptyProcess.onData((data) => {
      if (this.sessions.get(id) !== session) return;
      emulator.write(data);
      const seq = (this._seq.get(id) ?? 0) + 1;
      this._seq.set(id, seq);
      this._emit({ event: 'output', id, seq, data });
    });
    ptyProcess.onExit(({ exitCode }) => {
      if (this.sessions.get(id) !== session) return;
      console.log(`[Host] PTY ${id} exited (code ${exitCode})`);
      this.sessions.delete(id);
      emulator.dispose();
      this._reap(ptyProcess);
      this._emit({ event: 'exit', id, exitCode, reason: 'pty-exit' });
    });
    this.sessions.set(id, session);
    console.log(`[Host] Spawned ${id} (pid ${ptyProcess.pid}, cwd: ${cwd})`);
    return { pid: ptyProcess.pid, seq: this._seq.get(id) ?? 0 };
  }

  /** Close a terminal: kill the shell, no event (the daemon asked for it). @param {{ id: string }} p */
  kill({ id }) {
    const session = this._get(id);
    this.sessions.delete(id);
    this._reap(session.pty);
    session.emulator.dispose();
    console.log(`[Host] Killed ${id}`);
    return {};
  }

  /** @param {{ id: string, data: string }} p */
  write({ id, data }) {
    const session = this._get(id);
    if (typeof data !== 'string') throw new HostOpError('bad_request', 'write needs string data');
    session.pty.write(data);
    return {};
  }

  /** @param {{ id: string, cols: number, rows: number }} p */
  resize({ id, cols, rows }) {
    const session = this._get(id);
    const size = clampGeometry(cols, rows);
    session.pty.resize(size.cols, size.rows);
    session.emulator.resize(size.cols, size.rows);
    session.cols = size.cols;
    session.rows = size.rows;
    return size;
  }

  /**
   * The serialized screen (prefixed with VIEW_RESET so it lands on a blank
   * view) and the seq it covers: every output event with seq <= it is in the
   * serialization, so a client drops events <= seq and keeps the rest.
   * @param {{ id: string }} p
   */
  async snapshot({ id }) {
    const session = this._get(id);
    const snap = await session.emulator.snapshot({ maxBytes: MAX_SNAPSHOT_JSON_BYTES, mark: () => this._seq.get(id) ?? 0 });
    return { ansi: VIEW_RESET + snap.ansi, cols: snap.cols, rows: snap.rows, seq: /** @type {number} */ (snap.mark) };
  }

  list() {
    return [...this.sessions].map(([id, s]) => ({
      id, pid: s.pty.pid, cwd: s.cwd, cols: s.cols, rows: s.rows, seq: this._seq.get(id) ?? 0,
    }));
  }

  /** End every shell and wait until each is gone. */
  async shutdown() {
    const n = this.sessions.size;
    for (const [id, s] of [...this.sessions]) {
      this.sessions.delete(id);
      this._reap(s.pty);
      s.emulator.dispose();
      this._emit({ event: 'exit', id, exitCode: -1, reason: 'host-restart' });
    }
    await Promise.all([...this._reaping]);
    return { ended: n };
  }
}
