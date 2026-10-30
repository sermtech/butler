// AC-764: the terminal host's v2 hello, ported from an earlier prototype's hardening
// test. Run: node --test test/terminal_host/*.test.mjs
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  PROTOCOL_VERSION, hostPaths, encode, createLineDecoder, newNonce, proof, splitForWrite, WRITE_CHUNK_CHARS,
} from '../../daemon/terminal_host/protocol.js';

const HOST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../daemon/terminal_host/host.js');
let dir, paths, child, token;

before(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'butler-th-'));
  paths = hostPaths(dir);
  child = spawn(process.execPath, [HOST, `--terminals-dir=${dir}`], { stdio: 'ignore', windowsHide: true });
  for (let i = 0; i < 100 && !fs.existsSync(paths.info); i++) await new Promise((r) => setTimeout(r, 100));
  token = fs.readFileSync(paths.token, 'utf8').trim();
});

after(() => {
  try { child.kill(); } catch { /* gone */ }
  fs.rmSync(dir, { recursive: true, force: true });
});

/** Open a connection; collect every raw byte the host sends and each parsed message. */
function open() {
  return new Promise((resolve, reject) => {
    const sock = net.connect(paths.pipe);
    const conn = { sock, raw: '', msgs: [], waiters: [] };
    sock.on('data', (b) => { conn.raw += b.toString('utf8'); });
    sock.on('data', createLineDecoder((m) => {
      conn.msgs.push(m);
      conn.waiters.splice(0).forEach((w) => w());
    }, reject));
    conn.next = async () => {
      while (!conn.msgs.length) await new Promise((r) => conn.waiters.push(r));
      return conn.msgs.shift();
    };
    sock.once('connect', () => resolve(conn));
    sock.once('error', reject);
  });
}

test('a client that proves the token gets hello_ok, and the host proves itself first', async () => {
  const c = await open();
  const nonce = newNonce();
  c.sock.write(encode({ type: 'hello', version: PROTOCOL_VERSION, nonce }));
  const ch = await c.next();
  assert.equal(ch.type, 'challenge');
  assert.equal(ch.proof, proof(token, 'host', nonce));
  c.sock.write(encode({ type: 'auth', proof: proof(token, 'client', ch.nonce) }));
  const ok = await c.next();
  assert.equal(ok.type, 'hello_ok');
  assert.equal(ok.hostPid, child.pid);
  assert.ok(!c.raw.includes(token), 'the token must never be sent');
  c.sock.destroy();
});

test('a client with the wrong token is refused with bad_token and learns nothing', async () => {
  const c = await open();
  c.sock.write(encode({ type: 'hello', version: PROTOCOL_VERSION, nonce: newNonce() }));
  const ch = await c.next();
  c.sock.write(encode({ type: 'auth', proof: proof('a-guess', 'client', ch.nonce) }));
  const err = await c.next();
  assert.deepEqual([err.type, err.code], ['error', 'bad_token']);
  assert.ok(!c.raw.includes(token));
  c.sock.destroy();
});

test('the host proof cannot be replayed as a client proof (role label)', async () => {
  const c = await open();
  const nonce = newNonce();
  c.sock.write(encode({ type: 'hello', version: PROTOCOL_VERSION, nonce }));
  const ch = await c.next();
  c.sock.write(encode({ type: 'auth', proof: proof(token, 'host', ch.nonce) }));
  assert.equal((await c.next()).code, 'bad_token');
  c.sock.destroy();
});

test('a request before the hello is refused', async () => {
  const c = await open();
  c.sock.write(encode({ type: 'request', rid: 1, op: 'list' }));
  assert.equal((await c.next()).code, 'bad_hello');
  c.sock.destroy();
});

test('an old protocol version is refused', async () => {
  const c = await open();
  c.sock.write(encode({ type: 'hello', version: PROTOCOL_VERSION - 1, nonce: newNonce() }));
  assert.equal((await c.next()).code, 'bad_version');
  c.sock.destroy();
});

test('splitForWrite keeps order and never splits a surrogate pair', () => {
  const data = 'a'.repeat(WRITE_CHUNK_CHARS - 1) + '\u{1F600}' + 'b'.repeat(10);
  const parts = splitForWrite(data);
  assert.equal(parts.join(''), data);
  assert.ok(parts.every((p) => p.length <= WRITE_CHUNK_CHARS));
  for (const p of parts) assert.ok(!/[\ud800-\udbff]$/.test(p) || p === parts[parts.length - 1]);
});
