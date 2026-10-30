// Third-party output carries TEXT only (found live 2026-10-08 on the laptop).
//
// libsignal, under Baileys, prints with the global console -- among others
// console.info("Closing session:", session), where `session` is a whole
// SessionEntry: privKey, rootKey, chain and base keys. The daemon copies this
// process's stdout and stderr into butler.log, so those keys landed in a
// plaintext log nothing prunes. Filtering known messages would fail again on
// the next library version; the rule instead is structural: anything printed
// through `console` keeps its strings, numbers and booleans, and every object,
// Buffer or array is replaced by a type tag before it can be written.
//
// The sidecar's OWN lines (the port handshake, its errors, `[wa]` lines) do
// not go through `console` at all: they are written directly by wa-sidecar.mjs.

export const MAX_LINE = 300;

const METHODS = ['log', 'info', 'warn', 'error', 'debug', 'trace', 'dir', 'table'];

/** The printable part of one console call: primitives kept, everything else tagged. */
export function textOnly(args) {
  const parts = args.map((a) => {
    if (a === null || a === undefined) return String(a);
    const t = typeof a;
    if (t === 'string' || t === 'number' || t === 'boolean' || t === 'bigint') return String(a);
    if (Buffer.isBuffer(a) || ArrayBuffer.isView(a)) return '[bytes]';
    if (a instanceof Error) return `[${a.name}]`;
    return `[${t}]`;
  });
  const line = parts.join(' ').replace(/[\r\n]+/g, ' ');
  return line.length > MAX_LINE ? line.slice(0, MAX_LINE - 3) + '...' : line;
}

/** Replace every console method on `target` with a text-only writer. */
export function guardConsole(target = console, write = (s) => process.stderr.write(s)) {
  for (const m of METHODS) {
    target[m] = (...args) => write('[lib] ' + textOnly(args) + '\n');
  }
}
