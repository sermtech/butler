/**
 * Third-party output never carries key material (found live 2026-10-08 on the
 * laptop: libsignal's console.info("Closing session:", session) dumped
 * privKey/rootKey/chain keys, and the daemon copies this process's output into
 * butler.log). The rule: a library may print TEXT, never an object or a
 * Buffer -- whatever a future version decides to print.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { guardConsole, textOnly, MAX_LINE } from '../wa-console.js';
import { tempDir } from './fakes.mjs';

const SESSION = {
  currentRatchet: {
    ephemeralKeyPair: { pubKey: Buffer.from([5, 51, 237]), privKey: Buffer.from([200, 144, 190, 34]) },
    rootKey: Buffer.from([87, 189, 251]),
  },
  indexInfo: { baseKey: Buffer.from([5, 215, 199]) },
};

test('a library print keeps its words and loses every object and Buffer', () => {
  const lines = [];
  const fake = {};
  guardConsole(fake, (s) => lines.push(s));
  fake.info('Closing session:', SESSION);
  fake.warn('Unhandled bucket type (for naming):', 'object', SESSION);
  fake.error(Buffer.from('secret-bytes'));
  fake.log('Session error:' + 'boom', 'at x.js:1');
  const all = lines.join('');
  assert.match(all, /\[lib\] Closing session:/);
  assert.match(all, /Session error:boom at x\.js:1/);
  for (const bad of ['privKey', 'rootKey', 'baseKey', 'pubKey', 'secret-bytes', '200', 'currentRatchet']) {
    assert.ok(!all.includes(bad), `"${bad}" reached the output: ${all}`);
  }
  assert.ok(lines.every((l) => l.endsWith('\n')), 'one line per print');
});

test('every console method is guarded, and a long text line is cut', () => {
  const lines = [];
  const fake = {};
  guardConsole(fake, (s) => lines.push(s));
  for (const m of ['log', 'info', 'warn', 'error', 'debug', 'trace', 'dir', 'table']) {
    fake[m](SESSION);
  }
  assert.equal(lines.length, 8);
  assert.ok(!lines.join('').includes('Key'));
  assert.ok(textOnly(['x'.repeat(5000)]).length <= MAX_LINE);
});

test('the real sidecar installs the guard BEFORE Baileys loads: a print during load is guarded, the port line comes first', async () => {
  const sidecar = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'wa-sidecar.mjs');
  const dir = tempDir('wa-guard-');
  // RV-10 (F-20): prints like libsignal does AT THE MOMENT libsignal (a
  // CommonJS package under Baileys) loads its own files -- no timer, so no
  // race with a loaded machine. A guard installed after the load (a hoisted
  // static import) lets it out raw, on STDOUT, ahead of the port line.
  // (Node 22.17's module.registerHooks crashes on Baileys' graph with an
  // internal assertion, so the CommonJS loader is wrapped instead.)
  const preload = path.join(dir, 'libsignal-like.mjs');
  fs.writeFileSync(preload, `import Module from 'node:module';
const load = Module._load;
let fired = false;
Module._load = function (request, parent, isMain) {
  if (!fired && /[\\\\/]libsignal[\\\\/]/.test(parent?.filename || '')) {
    fired = true;
    console.info('Closing session:', ${JSON.stringify({
    currentRatchet: { ephemeralKeyPair: { privKey: 'PRIVKEY-MATERIAL' }, rootKey: 'ROOTKEY-MATERIAL' },
  })});
  }
  return load.call(this, request, parent, isMain);
};
`);
  const child = spawn(process.execPath, ['--import', pathToFileURL(preload).href, sidecar], {
    env: { ...process.env, BUTLER_WA_TOKEN: 'tok', BUTLER_WA_DIR: dir },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let out = '';
  let err = '';
  child.stdout.on('data', (d) => { out += d; });
  child.stderr.on('data', (d) => { err += d; });
  try {
    await new Promise((resolve, reject) => {
      // The port line is printed after listen(), so after every module loaded:
      // by then the hook's print has happened, guarded or not.
      const t = setTimeout(() => reject(new Error(`no stdout line within 30 s; stdout=${out} stderr=${err}`)), 30000);
      const check = () => { if (out.includes('\n')) { clearTimeout(t); resolve(); } };
      child.stdout.on('data', check);
      child.on('exit', (code) => reject(new Error(`sidecar exited with ${code}: ${err}`)));
    });
    const first = out.split('\n')[0].trim();
    assert.ok(/^\d+$/.test(first), `the first stdout line is still the bare port, got ${JSON.stringify(first)}`);
    assert.ok(err.includes('[lib] Closing session:'), `the load-time print must have fired, guarded: ${err}`);
    for (const bad of ['PRIVKEY-MATERIAL', 'ROOTKEY-MATERIAL', 'privKey', 'rootKey']) {
      assert.ok(!out.includes(bad) && !err.includes(bad), `"${bad}" reached the sidecar's output`);
    }
  } finally {
    child.kill();
  }
});
