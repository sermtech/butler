/**
 * Opt a kokoro worker child out of Windows power throttling (EXECUTION_SPEED).
 *
 * Node has no FFI, so this runs engines/winqos.py (the single definition of the
 * Win32 call, also used by the daemon and Smart Turn's pool child) against the
 * pid, with the interpreter the daemon passed in BUTLER_PYTHON. Rejects LOUDLY
 * (missing env, spawn failure, non-zero exit); the caller logs it and carries
 * on, because this is a performance setting. Resolves false off Windows.
 */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WINQOS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'winqos.py');

/**
 * @param {number} pid
 * @param {{python?: string, script?: string}} [opts] test seams
 * @returns {Promise<boolean>}
 */
export function applyHighQos(pid, opts = {}) {
  if (process.platform !== 'win32') return Promise.resolve(false);
  const python = opts.python || process.env.BUTLER_PYTHON;
  if (!python) return Promise.reject(new Error('BUTLER_PYTHON is not set (sidecar not started by the daemon)'));
  return new Promise((resolve, reject) => {
    const proc = spawn(python, ['-X', 'utf8', opts.script || WINQOS, String(pid)], {
      stdio: ['ignore', 'ignore', 'pipe'],
      windowsHide: true,
    });
    let stderr = '';
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) resolve(true);
      else reject(new Error(`winqos.py exited ${code}: ${stderr.trim()}`));
    });
  });
}
