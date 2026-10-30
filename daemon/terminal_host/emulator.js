/**
 * terminal-emulator.js -- a server-side terminal per live session.
 *
 * Every PTY chunk is parsed by an `@xterm/headless` Terminal, so the server
 * always holds the SCREEN, not just the bytes. A reconnecting view replays a
 * serialized snapshot of that screen (`@xterm/addon-serialize`) instead of a
 * raw byte ring: a ring cut mid escape sequence prints the tail of the
 * sequence as text, and a full-screen app's frames replayed from the middle
 * paint garbage. The serializer only ever emits complete sequences, and the
 * parser keeps a half-received sequence as parser state, never as cells.
 *
 * Headless `write` is ASYNC-batched (the parser runs on a later tick), so
 * every read queues an empty write and runs INSIDE its callback: callbacks
 * fire in write order, so the read sees exactly the bytes written before it
 * was asked, and none written after. Inside, not after an await of it: xterm
 * keeps parsing later chunks in the same batch before any promise
 * continuation runs, so a read after `await` saw bytes written AFTER the ask
 * (measured: asked after n-49, it held n-399) -- and a snapshot whose seq said
 * otherwise doubled lines on attach.
 *
 * A serialized screen can be bounded (`maxBytes`, measured as JSON, which is
 * how it travels): past it the OLDEST history rows are dropped until it fits,
 * logged loudly. The screen itself is never trimmed.
 */
// CJS packages: no named ESM exports, so destructure off the default.
import xtermHeadless from '@xterm/headless';
import xtermSerialize from '@xterm/addon-serialize';

const { Terminal } = xtermHeadless;
const { SerializeAddon } = xtermSerialize;

/**
 * Rows kept above the screen. 2000 is the same depth the voice read always
 * used (terminal-read.js), and it bounds both costs that grow with it: the
 * emulator holds ~12 bytes per cell (about 2.9 MB for a 120-column session)
 * and a serialized snapshot stays small.
 * The browser's own xterm keeps 5000, so a replay never overflows the view.
 */
export const EMULATOR_SCROLLBACK_ROWS = 2000;

/**
 * Modes a program can leave switched on, put back to their defaults. Excludes
 * the two whose reset MOVES THE CURSOR -- 1049 (restores the saved cursor) and
 * origin mode 6 (homes it) -- which are reset only when actually on.
 * CAN (0x18) first: it aborts any half-received sequence, so the reset is
 * never swallowed as the parameters of a sequence a truncated stream left open.
 */
const MODES_OFF = '\x18\x1b[0m\x1b[?25h\x1b[?1l\x1b[?66l'
  + '\x1b[?9l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l\x1b[?1006l\x1b[?1015l'
  + '\x1b[?2004l\x1b[?1004l\x1b[4l\x1b[?45l\x1b[?7h';

/**
 * Prefix for a replay into a VIEW whose state is unknown (the client clears
 * its xterm, but `clear()` keeps the cursor's line and every mode, and in the
 * alt screen wipes only the alt buffer): leave the alt screen, every mode off,
 * then wipe the screen AND the scrollback (3J -- 2J keeps it, so a reconnect
 * from inside a full-screen app duplicated the whole history) and home the
 * cursor, so the snapshot lands on a blank terminal as on a fresh one.
 * 1049l is safe here because the home that follows overrides its cursor move.
 */
export const VIEW_RESET = `\x18\x1b[?1049l${MODES_OFF}\x1b[H\x1b[2J\x1b[3J`;

/** Mouse encodings xterm tracks but does not expose in `terminal.modes`. */
const MOUSE_ENCODINGS = [1005, 1006, 1015];

export class TerminalEmulator {
  /**
   * @param {{ cols: number, rows: number, scrollback?: number }} dims
   */
  constructor({ cols, rows, scrollback = EMULATOR_SCROLLBACK_ROWS }) {
    this._term = new Terminal({ cols, rows, scrollback, allowProposedApi: true });
    this._serializer = new SerializeAddon();
    this._term.loadAddon(this._serializer);
    this._disposed = false;
    /**
     * Resolvers of reads waiting on the parser. A disposed Terminal never
     * fires its write callbacks, so dispose() releases them -- otherwise a
     * snapshot in flight on a killed shell would wait forever.
     * @type {Set<() => void>}
     */
    this._waiters = new Set();
    // State the serializer does not emit and the public API does not expose:
    // cursor visibility (a TUI hides it) and the mouse encoding (SGR 1006).
    this._cursorHidden = false;
    /** @type {Set<number>} */
    this._mouseEncodings = new Set();
    const track = (on) => (params) => {
      for (const p of params) {
        const n = Array.isArray(p) ? p[0] : p;
        if (n === 25) this._cursorHidden = !on;
        else if (MOUSE_ENCODINGS.includes(n)) on ? this._mouseEncodings.add(n) : this._mouseEncodings.delete(n);
      }
      return false; // observe only: xterm's own handler still runs
    };
    this._term.parser.registerCsiHandler({ prefix: '?', final: 'h' }, track(true));
    this._term.parser.registerCsiHandler({ prefix: '?', final: 'l' }, track(false));
    this._term.parser.registerEscHandler({ final: 'c' }, () => { // RIS: full reset
      this._cursorHidden = false;
      this._mouseEncodings.clear();
      return false;
    });
  }

  get cols() { return this._term.cols; }
  get rows() { return this._term.rows; }

  /**
   * Feed PTY output. Never throws: xterm refuses data past its 50 MB backlog
   * watermark by THROWING, and this runs inside node-pty's data callback,
   * where a throw is an uncaught exception that ends the server. The refusal
   * is logged loudly; the bytes still reach live views (the caller sends them).
   * @param {string} data
   */
  write(data) {
    if (this._disposed) return;
    this._writeNow(data);
  }

  /** @private */
  _writeNow(data, cb) {
    try {
      this._term.write(data, cb);
    } catch (err) {
      console.error(`[TerminalEmulator] Output dropped from the server-side screen: ${err.message}`);
      cb?.();
    }
  }

  /** @private Resolves once every byte written so far has been parsed (or on dispose). */
  _parsed() {
    return new Promise((resolve) => {
      const done = () => { this._waiters.delete(done); resolve(); };
      this._waiters.add(done);
      this._writeNow('', done);
    });
  }

  /**
   * Run `read` at the exact point every byte written so far is parsed and no
   * later byte is: synchronously inside the callback of an empty write queued
   * NOW (see the header for why not after an await). Rejects on dispose.
   * @private
   * @template T
   * @param {() => T} read
   * @returns {Promise<T>}
   */
  _readAtParsed(read) {
    return new Promise((resolve, reject) => {
      const release = () => { this._waiters.delete(release); reject(new Error('terminal emulator is disposed')); };
      this._waiters.add(release);
      this._writeNow('', () => {
        if (!this._waiters.delete(release)) return; // already released by dispose()
        if (this._disposed) { reject(new Error('terminal emulator is disposed')); return; }
        try { resolve(read()); } catch (err) { reject(err); }
      });
    });
  }

  /**
   * The serializer's output with as much history as fits `maxBytes` as JSON:
   * all EMULATOR_SCROLLBACK_ROWS when it fits, else fewer, oldest dropped.
   * Throws when even the bare screen does not fit (never a silent cut).
   * @private
   */
  _serializeFitting(opts, suffix, maxBytes, what) {
    let rows = EMULATOR_SCROLLBACK_ROWS;
    for (;;) {
      const ansi = this._serializer.serialize({ ...opts, scrollback: rows }) + suffix;
      const size = Buffer.byteLength(JSON.stringify(ansi));
      if (size <= maxBytes) {
        if (rows < EMULATOR_SCROLLBACK_ROWS) {
          console.warn(`[TerminalEmulator] ${what} TRIMMED to ${rows} of ${EMULATOR_SCROLLBACK_ROWS} history rows (${size} bytes as JSON) to fit ${maxBytes} bytes; older history dropped`);
        }
        return ansi;
      }
      if (rows === 0) throw new Error(`${what}: the screen alone serializes to ${size} bytes as JSON, past the ${maxBytes} byte budget`);
      rows = Math.max(0, Math.min(rows - 1, Math.floor(rows * (maxBytes / size) * 0.9)));
    }
  }

  /** @private A read on a disposed screen is a caller bug, never an empty answer. */
  _assertLive() {
    if (this._disposed) throw new Error('terminal emulator is disposed');
  }

  /**
   * Resize, ORDERED with the output: applied once every chunk written before
   * it has been parsed, so earlier output wraps at the width it was produced for.
   * @param {number} cols
   * @param {number} rows
   */
  resize(cols, rows) {
    if (this._disposed) return;
    const apply = () => { if (!this._disposed) this._term.resize(cols, rows); };
    this._writeNow('', apply);
  }

  /** Resolves when every byte written has been parsed. */
  async flush() {
    if (!this._disposed) await this._parsed();
  }

  /**
   * The whole terminal as ANSI that replays into a fresh terminal of the same
   * size: normal-buffer history, the alt screen if active, the modes that are
   * on, cursor visibility, mouse encoding, and (alt screen) the absolute cursor.
   * Covers exactly the bytes written before the call. `mark` is called
   * synchronously at that same point (the host reads its output seq there,
   * so the seq it reports is exactly what the snapshot holds); its return
   * value comes back as `mark`.
   * @param {{ maxBytes?: number, mark?: () => unknown }} [opts]
   * @returns {Promise<{ ansi: string, cols: number, rows: number, mark: unknown }>}
   */
  async snapshot({ maxBytes = Infinity, mark } = {}) {
    //     this._assertLive();
    const read = this._readAtParsed(() => {
      const t = this._term;
      let suffix = '';
      for (const n of this._mouseEncodings) suffix += `\x1b[?${n}h`;
      if (t.buffer.active.type === 'alternate') {
        suffix += `\x1b[${t.buffer.active.cursorY + 1};${t.buffer.active.cursorX + 1}H`;
      }
      if (this._cursorHidden) suffix += '\x1b[?25l';
      return { ansi: this._serializeFitting({}, suffix, maxBytes, 'snapshot'), cols: t.cols, rows: t.rows };
    });
    // Same tick as the empty write above: nothing can be written in between.
    const token = mark ? mark() : undefined;
    return { ...(await read), mark: token };
  }

  dispose() {
    if (this._disposed) return;
    this._disposed = true;
    this._term.dispose();
    for (const release of [...this._waiters]) release();
    this._waiters.clear();
  }
}
