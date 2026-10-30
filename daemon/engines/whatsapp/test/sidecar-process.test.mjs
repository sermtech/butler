/**
 * The real wa-sidecar.mjs process (AC-500): it binds loopback, prints its port
 * as the FIRST stdout line, refuses requests without the per-boot token, and
 * fails fast without its environment. No session exists in the temp records
 * directory, so the process never connects to WhatsApp.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { spawn, spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tempDir } from './fakes.mjs';

const SIDECAR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'wa-sidecar.mjs');

function start(env) {
  const child = spawn(process.execPath, [SIDECAR], { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  const firstLine = new Promise((resolve, reject) => {
    let buf = '';
    child.stdout.on('data', (d) => {
      buf += d;
      const nl = buf.indexOf('\n');
      if (nl >= 0) resolve(buf.slice(0, nl).trim());
    });
    child.on('exit', (code) => reject(new Error(`sidecar exited early with ${code}`)));
    setTimeout(() => reject(new Error('no port line within 15 s')), 15000).unref();
  });
  return { child, firstLine };
}

test('it prints its loopback port first, wants the token, and serves with it', async () => {
  const { child, firstLine } = start({ BUTLER_WA_TOKEN: 'tok-123', BUTLER_WA_DIR: tempDir('wa-proc-') });
  try {
    const port = Number(await firstLine);
    assert.ok(Number.isInteger(port) && port > 0, 'the first stdout line is the bare port number');
    const base = `http://127.0.0.1:${port}`;
    assert.equal((await fetch(base + '/status')).status, 401);
    assert.equal((await fetch(base + '/status', { headers: { 'x-butler-token': 'nope' } })).status, 401);
    const ok = await fetch(base + '/status', { headers: { 'x-butler-token': 'tok-123' } });
    assert.equal(ok.status, 200);
    const body = await ok.json();
    assert.equal(body.linked, false);
    assert.equal(body.status, 'idle', 'nothing connects before the user asks to link');
  } finally {
    child.kill();
  }
});

test('it refuses to start without its token or its records directory (fail fast)', () => {
  const run = (env) => spawnSync(process.execPath, [SIDECAR], {
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, ...env }, encoding: 'utf8', timeout: 15000,
  });
  const noToken = run({ BUTLER_WA_DIR: tempDir('wa-proc-') });
  assert.equal(noToken.status, 1);
  assert.match(noToken.stderr, /BUTLER_WA_TOKEN/);
  const noDir = run({ BUTLER_WA_TOKEN: 'x' });
  assert.equal(noDir.status, 1);
  assert.match(noDir.stderr, /BUTLER_WA_DIR/);
});
