/* Butler visualiser -- the lattice wave (docs/lattice-wave.md).

   window.ButlerVisualiser.create(mainCanvas, traceCanvas, opts) -> controller

   A CLASSIC script, published synchronously, because index.html loads it with
   a plain <script src> before the app.js module and this directory is served
   as-is: no bundler, no npm, no CDN (web/AGENTS.md).

   Two renderers over one measurement. #wave carries the TIME domain -- a
   triggered oscilloscope trace of the real waveform with a phosphor stack of
   its recent past behind it -- and #field carries the FREQUENCY domain as a
   proximity network whose link distance is the loudness. Neither ever reaches
   for the other's question, and that is physics rather than taste: a path is
   rebuilt from scratch every frame so it can follow a 118 Hz voice, while a
   particle reaches its target on a spring of tens of milliseconds and would
   low-pass the same waveform into a smudge.

   ANALYSER TAPS ONLY. Butler owns both audio graphs; this file opens no
   AudioContext, closes none, and connects nothing to any destination. An
   AnalyserNode is a pass-through tap, so nothing here changes a sample that
   reaches the recognizer, the detector or the speaker. A visualiser that makes
   its own noise on a device whose microphone is live is not a visualiser.

   NO BROWSER STORAGE, and there is a test that greps for it.

   This replaced a Claude Design comp kept nearly verbatim. Three of that
   file's diagnostics reported things that were not true, and they are named
   here so a future rewrite cannot reintroduce them: the wake score latched at
   a running maximum and could never fall (D-27), the trace's threshold line
   was hardcoded to mid-height while its label came from a constant (D-28),
   and the trace was pre-filled with 220 samples of Math.random() (D-29). */
(function (global) {
  var TAU = Math.PI * 2;

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  /* EVERY smoothing coefficient in this file goes through here. A fixed lerp
     factor is a coefficient per FRAME, so the same code responds twice as fast
     on a 120 Hz tablet as on a 60 Hz laptop; tau is milliseconds and means the
     same thing on both. */
  function co(dt, tau) { return 1 - Math.exp(-dt / tau); }

  function alpha(col, a) {
    return col.indexOf('rgba') === 0 ? col.replace(/[\d.]+\s*\)$/, a + ')') : col;
  }

  /* Walk a rounded rect, returning points with their OUTWARD normal and an
     arc-length parameter `u` in 0..1. Ported whole from
     web/mocks/r-plume.html's `perimeter()` (STEP 3, docs/voice-effects.md) --
     no new shape, the same walk against the composer's own rectangle instead
     of the mock's fixed demo box. `ember` and `ribbon` both call this, so the
     two treatments can never disagree about where the edge is. A pure
     function of R = {x, y, w, h, r}: no canvas, no state, nothing here reads
     the level or the mode. */
  function perimeter(R, n) {
    var pts = [], x = R.x, y = R.y, w = R.w, h = R.h, r = R.r;
    var top = w - 2 * r, side = h - 2 * r, q = (Math.PI * r) / 2;
    var total = 2 * top + 2 * side + 4 * q;
    for (var i = 0; i < n; i++) {
      var s = (i / n) * total, px, py, nx, ny, a;
      if (s < top) { px = x + r + s; py = y; nx = 0; ny = -1; }
      else if ((s -= top) < q) {
        a = -Math.PI / 2 + s / r; px = x + w - r + r * Math.cos(a);
        py = y + r + r * Math.sin(a); nx = Math.cos(a); ny = Math.sin(a);
      } else if ((s -= q) < side) { px = x + w; py = y + r + s; nx = 1; ny = 0; }
      else if ((s -= side) < q) {
        a = s / r; px = x + w - r + r * Math.cos(a);
        py = y + h - r + r * Math.sin(a); nx = Math.cos(a); ny = Math.sin(a);
      } else if ((s -= q) < top) { px = x + w - r - s; py = y + h; nx = 0; ny = 1; }
      else if ((s -= top) < q) {
        a = Math.PI / 2 + s / r; px = x + r + r * Math.cos(a);
        py = y + h - r + r * Math.sin(a); nx = Math.cos(a); ny = Math.sin(a);
      } else if ((s -= q) < side) { px = x; py = y + h - r - s; nx = -1; ny = 0; }
      else {
        a = Math.PI + (s - side) / r; px = x + r + r * Math.cos(a);
        py = y + r + r * Math.sin(a); nx = Math.cos(a); ny = Math.sin(a);
      }
      pts.push({ x: px, y: py, nx: nx, ny: ny, u: i / n });
    }
    return pts;
  }

  /* Band edges in HERTZ, read off the tap's own sampleRate, never in bin
     indices. The microphone graph runs at the device rate and the playback
     graph is created at 24 kHz, so one bin number is two different frequencies
     on the two taps -- a bin-index band draws Butler's voice as systematically
     darker than the room's for no reason but the sample rate. Bark-ish across
     the speech band, and the MAXIMUM in each band rather than the mean,
     because the mean of a wide band is always small and always boring. */
  var EDGES = [120, 260, 400, 560, 740, 950, 1200, 1500, 1850, 2300, 2850,
               3500, 4300, 5300, 6500, 8000];
  var NB = EDGES.length - 1;

  /* The display tilt: a gain rising with frequency, so a band is judged
     against what a voice normally puts there. Density that follows the raw
     spectrum piles onto the left of the width and leaves the right third
     empty, because speech really does put its energy in the low bands.
     MULTIPLICATIVE and never additive -- adding lifts a silent high band off
     the floor and invents energy that is not in the room. 2.6 is the mock's
     measured figure and the untilted read stays available as `at`. */
  var TILT = new Float32Array(NB);
  for (var ti = 0; ti < NB; ti++) TILT[ti] = 1 + 2.6 * ti / (NB - 1);

  /* ---- the level chain --------------------------------------------------- *
   * The shape a voice needs, which `lerp(rms, r, 0.35)` did not have: an
   * asymmetric follower so a syllable arrives faster than it leaves, the
   * room's own floor tracked and SUBTRACTED (that subtraction is the gate, and
   * it is what stops makeup gain from lifting hiss to half height), a slow
   * loud reference so one set of numbers works on a laptop at arm's length and
   * a tablet across a kitchen, and height as AMPLITUDE under a power curve.
   * The last one is the non-obvious half: a dB axis is right for a meter and
   * wrong for a waveform, because it squashes a ten-to-one amplitude range
   * into a third of the canvas.                                              */
  function makeChain(opt) {
    opt = opt || {};
    var atk = opt.attack || 4, rel = opt.release || 90;
    var curve = opt.curve || 0.72, gateMul = opt.gate || 2.2;
    var env = 0, floorEst = 0.004, refEst = 0.10;
    var out = 0, prev = 0, tr = 0;
    return {
      push: function (x, dt) {
        env += (x - env) * co(dt, x > env ? atk : rel);
        // the room's own floor: falls onto a new quiet fast, climbs out slowly
        floorEst += (x - floorEst) * co(dt, x < floorEst ? 220 : 18000);
        floorEst = Math.max(floorEst, 5e-5);
        var lin = Math.max(0, env - floorEst * gateMul);
        // The loud reference is FROZEN while nothing is being said. Left
        // running it chases the hiss down through a silence and the wave then
        // pumps between words.
        if (lin > floorEst * 4) {
          refEst += (lin - refEst) * co(dt, lin > refEst ? 2600 : 20000);
        }
        refEst = clamp(refEst, 0.06, 1);
        var xr = lin / refEst;
        out = xr <= 1 ? Math.pow(xr, curve) : 1;
        // An onset is a RISE, and the rise is the part a listener sees, so it
        // is carried separately from the level rather than inferred from it.
        tr = Math.max(0, (out - prev) / (dt / 16.7));
        prev = out;
        return out;
      },
      level: function () { return out; },
      transient: function () { return tr; }
    };
  }

  /* ---- a tap ------------------------------------------------------------- *
   * One AnalyserNode plus everything derived from it. There is one per audio
   * graph and they keep SEPARATE auto-ranging state: a far-field microphone
   * and a synthesised reply do not live at the same amplitude, and one shared
   * reference would make whichever spoke last decide the other's height.     */
  function makeTap(node) {
    var an = node.context.createAnalyser();
    an.fftSize = 2048;                 // one read gives 1024 time samples
    /* 0.2, not the 0.55 this file used to ask for: the envelope follower above
       and the per-band followers below already own the smoothing, and
       smoothing twice is what makes a wave feel late. */
    an.smoothingTimeConstant = 0.2;
    node.connect(an);

    var RING = 1024, ring = new Float32Array(RING);
    var time = new Uint8Array(an.fftSize);
    var freq = new Uint8Array(an.frequencyBinCount);
    var env = new Float32Array(NB);
    var idx = null, rate = 0;
    var rms = 0, trig = 0, floorPk = 0.004, refPk = 0.15;

    function plan() {
      rate = an.context.sampleRate;
      var hzPerBin = (rate / 2) / an.frequencyBinCount;
      idx = [];
      for (var i = 0; i < NB; i++) {
        var a = Math.max(1, Math.floor(EDGES[i] / hzPerBin));
        var b = Math.min(an.frequencyBinCount - 1,
                         Math.max(a + 1, Math.floor(EDGES[i + 1] / hzPerBin)));
        idx.push(a, b);
      }
    }

    return {
      node: an,
      pump: function (dt) {
        if (!idx || rate !== an.context.sampleRate) plan();
        an.getByteTimeDomainData(time);
        var off = time.length - RING, i;
        for (i = 0; i < RING; i++) ring[i] = (time[off + i] - 128) / 128;

        an.getByteFrequencyData(freq);
        for (i = 0; i < NB; i++) {
          var lo = idx[i * 2], hi = idx[i * 2 + 1], m = 0;
          for (var k = lo; k <= hi; k++) if (freq[k] > m) m = freq[k];
          // the same asymmetry the level chain uses: a band arrives at once
          // and leaves slowly, which is the difference between a meter and a
          // flicker
          var target = m / 255;
          env[i] += (target - env[i]) * co(dt, target > env[i] ? 8 : 190);
        }

        var s = 0, base = RING - 256, pk = 0;
        for (i = base; i < RING; i++) {
          s += ring[i] * ring[i];
          var mag = ring[i] < 0 ? -ring[i] : ring[i];
          if (mag > pk) pk = mag;
        }
        rms = Math.sqrt(s / 256);
        /* An oscilloscope auto-ranges and so must this. Raw sample values off
           a far-field microphone are a few hundredths of full scale, so a
           trace drawn at their literal height is a flat line whatever the room
           does. Two trackers: the room's own peak floor, which falls onto a
           new quiet fast and climbs out slowly, and a slow reference peak the
           trace is drawn against. */
        floorPk += (pk - floorPk) * co(dt, pk < floorPk ? 300 : 22000);
        refPk += (pk - refPk) * co(dt, pk > refPk ? 120 : 2600);
        refPk = clamp(refPk, 0.03, 1);
        /* Trigger on the first rising zero crossing and draw from there. Each
           animation frame lands at an arbitrary phase, so an untriggered trace
           slides left and right every frame and reads as noise however clean
           the audio is. */
        trig = 0;
        for (i = 1; i < (RING >> 1); i++) {
          if (ring[i - 1] < 0 && ring[i] >= 0) { trig = i; break; }
        }
      },
      rms: function () { return rms; },
      /* p in 0..1 walks half the ring, which is 32 ms of sound at 16 kHz. */
      waveAt: function (p) {
        var span = RING >> 1;
        var x = trig + clamp(p, 0, 1) * (span - 1), i = x | 0;
        var b = i + 1 < RING ? i + 1 : RING - 1;
        return ring[i] + (ring[b] - ring[i]) * (x - i);
      },
      /* Auto-ranged, gated against the room's own floor and lifted by a power
         curve so a quiet syllable is still visible. Sign is preserved: this is
         a WAVE, not an envelope, and it has to cross zero where the sound
         did. */
      waveNorm: function (p) {
        var v = this.waveAt(p), a = v < 0 ? -v : v;
        var g = a - floorPk * 1.6;
        if (g <= 0) return 0;
        return (v < 0 ? -1 : 1) * Math.pow(Math.min(1, g / refPk), 0.72);
      },
      at: function (p) {
        var x = clamp(p, 0, 1) * (NB - 1), i = x | 0;
        var j = i + 1 < NB ? i + 1 : NB - 1;
        return env[i] + (env[j] - env[i]) * (x - i);
      },
      atTilt: function (p) {
        var x = clamp(p, 0, 1) * (NB - 1), i = x | 0;
        var j = i + 1 < NB ? i + 1 : NB - 1;
        var a = env[i] * TILT[i], b = env[j] * TILT[j];
        return Math.min(1, a + (b - a) * (x - i));
      },
      release: function () {
        try { an.disconnect(); } catch (e) { /* the graph may be gone */ }
      }
    };
  }

  function create(mainCanvas, traceCanvas, opts) {
    opts = opts || {};
    var ctx = mainCanvas.getContext('2d');
    var tctx = traceCanvas ? traceCanvas.getContext('2d') : null;
    var fieldCanvas = opts.field || null;
    var fctx = fieldCanvas ? fieldCanvas.getContext('2d') : null;
    /* The composer's voice-effect slot (STEP 2, docs/voice-effects.md,
       AC-257). Handed over by app.js exactly like `opts.field` above -- this
       file never queries the page for an id or a class, so a caller that
       forgot to pass it gets no effect written rather than a lookup this
       file has no business doing. */
    var fxEl = opts.fx || null;

    /* The three accessibility queries, held as MediaQueryList objects and read
       on every frame. A boolean captured at construction is the defect: the
       owner can turn reduced motion on from the system settings with this page
       already open, and a page that only honours it on reload has not honoured
       it. `matches` updates live, so no listener is needed for the reading --
       only for the caches a change invalidates (see onMedia below). */
    var mm = global.matchMedia ? global.matchMedia.bind(global) : null;
    var qMotion = mm && mm('(prefers-reduced-motion: reduce)');
    var qClear = mm && mm('(prefers-reduced-transparency: reduce)');
    var qContrast = mm && mm('(prefers-contrast: more)');
    function calm() { return !!(qMotion && qMotion.matches); }
    function flat() { return !!(qClear && qClear.matches); }
    function bold() { return !!(qContrast && qContrast.matches); }

    /* The palette. `app.js` Object.assigns into this on every theme change,
       so it is mutated in place and never replaced, and every cache below is
       keyed on the colour STRING -- a new palette then produces new cache
       entries by construction rather than by anyone remembering to invalidate
       one. */
    var C = Object.assign({
      meshIdle: ['rgba(58, 84, 128, 1)', 'rgba(104, 142, 190, 1)', 'rgba(70, 98, 148, 1)'],
      meshIn:   ['rgba(34, 211, 238, 1)', 'rgba(56, 148, 246, 1)', 'rgba(70, 106, 232, 1)'],
      meshOut:  ['rgba(255, 214, 92, 1)', 'rgba(255, 176, 48, 1)', 'rgba(255, 236, 168, 1)'],
      meshErr:  ['rgba(240, 96, 72, 1)', 'rgba(226, 62, 58, 1)', 'rgba(198, 44, 62, 1)'],
      nodeIdle: 'rgba(196, 212, 238, 1)',
      nodeIn:   'rgba(236, 248, 255, 1)',
      nodeOut:  'rgba(255, 248, 226, 1)',
      nodeErr:  'rgba(255, 232, 224, 1)',
      trace:    'rgba(176, 206, 232, 0.9)',
      threshold: 'rgba(96, 200, 240, 0.85)'
    }, opts.colors || {});

    /* The threshold the trace draws its line at comes from the CALLER, which
       is the same value its LABEL is generated from. Hardcoding it here is
       D-28: change the value and the text moves while the line does not. The
       daemon names it after this runs (AC-805), so `setThreshold` moves it. */
    var THRESHOLD = typeof opts.threshold === 'number' ? opts.threshold : 0.5;

    var inTap = null, outTap = null;
    var youChain = makeChain({});
    var botChain = makeChain({ release: 130, curve: 0.8 });
    /* `effect` mirrors the `voice_effect` daemon setting exactly the way
       `mode`/`state` mirror the caller's own turn state -- set by
       `setEffect` below and read nowhere but in `frame()`. `lattice` is the
       schema's default (daemon/settings.py), so a page that has not heard
       from the daemon yet draws exactly what it always drew. */
    var mode = 'idle', state = 'off', effect = 'lattice';
    var live = 0, flash = 0, sweepP = 0.5, t = 0, last = performance.now();
    var raf = null;

    /* ---- the wake score --------------------------------------------------- *
     * The ONLY writer is pushScore, and it SETS rather than maxes. `score =
     * Math.max(score, v)` is D-27: with a microphone attached nothing could
     * ever lower it, so the readout and the trace became a running maximum for
     * the life of the page -- they climbed to the loudest thing that ever
     * happened and stayed there.
     *
     * It decays only once the detector has stopped talking. The detector
     * reports about every 80 ms, so decaying between reports would sag a
     * steady score by a fifth; holding for three report periods and then
     * falling means a stopped microphone drops to the floor instead of
     * latching, which is the half D-27 got wrong.                            */
    var score = 0, sinceScore = 1e6;
    var SCORE_HOLD_MS = 240, SCORE_TAU = 420;
    /* EMPTY. The old file pre-filled this with 220 samples of Math.random(),
       so an instrument showed invented data for its first nine seconds
       (D-29). In a project whose rule is that an untagged number is a guess
       wearing a uniform, a drawn one is worse. */
    var trace = [], TRACE_N = 220, traceAcc = 0;

    function attachInput(node) {
      if (!node) return false;
      if (inTap) inTap.release();
      inTap = makeTap(node);
      return true;
    }
    function attachOutput(node) {
      if (!node) return false;
      if (outTap) outTap.release();
      outTap = makeTap(node);
      return true;
    }

    /* ---- canvas plumbing --------------------------------------------------- */
    function fit(c) {
      // Capped at 2: past that a phone renders four times the pixels for a
      // difference nobody can see at arm's length.
      var dpr = Math.min(global.devicePixelRatio || 1, 2);
      var r = c.getBoundingClientRect();
      var w = Math.max(1, Math.round(r.width * dpr));
      var h = Math.max(1, Math.round(r.height * dpr));
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      return { dpr: dpr, w: r.width, h: r.height };
    }

    /* The deformation envelope: nothing at the edges, everything in the
       middle, so the band reads as one membrane rather than as a strip cut off
       at both ends. */
    function att(x, k) {
      return Math.pow(k / (k + Math.pow(Math.abs(x), k)), k);
    }

    /* Gradients are cached: createLinearGradient per stroke is a real cost at
       seven strokes a frame. The alpha is QUANTISED to 1/60ths before it goes
       into the key, because a continuous alpha makes every frame a cache miss
       -- an uncapped cache would then be a leak and a capped one would
       thrash.

       `pad` widens the gradient PAST both edges of the canvas, and only the
       ring asks for it. The two zero-alpha end stops are the band's fade into
       the page at left and right, which is right for a strip that runs the
       whole width and wrong for a circle drawn in a 42 px box: they cut the
       ring's own leftmost and rightmost arc away. Measured before it was
       added -- horizontal ink span 0.69 of the canvas against a vertical 0.81,
       on a shape that is symmetric by construction. Padded by a third of the
       width, the whole canvas falls between the 0.16 and 0.84 stops, so the
       three-stop colour walk survives and the fade happens off-screen. */
    var gradCache = Object.create(null), gradN = 0;
    function gradFor(w, stops, a, pad) {
      var q = clamp(Math.round(a * 60), 0, 60) / 60;
      var p = pad || 0;
      var key = Math.round(w) + '|' + Math.round(p)
              + stops[0] + stops[1] + stops[2] + q;
      var lg = gradCache[key];
      if (!lg) {
        if (++gradN > 90) { gradCache = Object.create(null); gradN = 1; }
        lg = ctx.createLinearGradient(-p, 0, w + p, 0);
        lg.addColorStop(0, alpha(stops[0], 0));
        lg.addColorStop(0.16, alpha(stops[0], q));
        lg.addColorStop(0.5, alpha(stops[1], q));
        lg.addColorStop(0.84, alpha(stops[2], q));
        lg.addColorStop(1, alpha(stops[2], 0));
        gradCache[key] = lg;
      }
      return lg;
    }

    /* ---- the phosphor ------------------------------------------------------ *
     * Six past traces, captured every 45 to 70 ms, held as Float32Arrays
     * written in place and NEVER reallocated -- a fresh array per capture is
     * twenty allocations a second for the life of the page.
     *
     * Deliberately NOT done by fading the canvas with a translucent fill,
     * which is the usual trick and is wrong here: #wave is TRANSPARENT, and
     * alpha-fading a transparent canvas never reaches zero. Each pass
     * multiplies what is there by (1 - a) and adds nothing, so the residue
     * converges on a permanent ghost of several percent instead of on nothing
     * -- and `off`, the one state that must look dead, inherits it.
     *
     * What is stored is the deviation ALREADY multiplied by that frame's
     * amplitude, so a trace captured during a loud syllable stays loud when
     * the room goes quiet instead of the stack rewriting its own history. */
    var WAVE_N = 128, PHOS = 6;
    var phos = [], phosN = 0, phosHead = 0, capLeft = 0;
    for (var pi = 0; pi < PHOS; pi++) phos.push(new Float32Array(WAVE_N + 1));
    var front = new Float32Array(WAVE_N + 1);

    /* The ring's own nodes, allocated ONCE. These are NOT the lattice's pool:
       that one is sized to #field, positioned by the spectrum across a whole
       page, and off in every placement but `ambient`. These fourteen live
       inside a 42 px button and their positions are unit fractions of it, so
       they survive the canvas changing size without being re-seeded. */
    var RING_MAX = 14;
    var ringPts = [];
    var ringX = new Float32Array(RING_MAX), ringY = new Float32Array(RING_MAX);
    for (var qi = 0; qi < RING_MAX; qi++) {
      ringPts.push({
        x: Math.random(), y: Math.random(),
        // unit fractions per MILLISECOND, so the drift is the same speed on a
        // 60 Hz laptop and a 120 Hz tablet
        vx: (Math.random() - 0.5) * 0.00004,
        vy: (Math.random() - 0.5) * 0.00004
      });
    }

    function buildFrontWave(tap, amp) {
      var i;
      if (!tap) { for (i = 0; i <= WAVE_N; i++) front[i] = 0; return; }
      for (i = 0; i <= WAVE_N; i++) front[i] = tap.waveNorm(i / WAVE_N) * amp;
    }
    /* `thinking` has no audio at all, so amplitude can say nothing and a trace
       would be a flat line. A travelling bump says "work is moving" without
       pretending the room made a sound, and it is deliberately a shape no
       voice produces -- one smooth crest crossing the band -- so it can never
       be mistaken for a reading. The phosphor stack then draws where it has
       been, which is the whole comet. */
    function buildFrontSweep(amp) {
      for (var i = 0; i <= WAVE_N; i++) {
        var d = (i / WAVE_N - sweepP) / 0.085;
        front[i] = Math.exp(-d * d) * amp;
      }
    }
    function capture(dt) {
      capLeft -= dt;
      if (capLeft > 0) return;
      // jittered: a fixed interval beats against the frame clock and the stack
      // breathes in and out at the difference frequency
      capLeft = 45 + Math.random() * 25;
      var dst = phos[phosHead];
      for (var i = 0; i <= WAVE_N; i++) dst[i] = front[i];
      phosHead = (phosHead + 1) % PHOS;
      if (phosN < PHOS) phosN++;
    }

    function tracePath(buf, w, mid, span, scale) {
      ctx.beginPath();
      for (var i = 0; i <= WAVE_N; i++) {
        var p = i / WAVE_N, x = p * 4 - 2;
        var y = mid + buf[i] * scale * att(x, 4) * span;
        if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(p * w, y);
      }
    }
    /* The mirror. The filled shape is symmetric about the centreline -- the
       SAME shape for the room and for Butler, because who is talking is
       carried by hue and glow and never by geometry (owner, 2026-08-19) --
       and `open` is the floor under it: the half-height the band keeps when
       the waveform is at zero. That floor is the room's own measured residual
       level, never an invented noise, so an unarmed page collapses to a
       hairline and an armed one in a quiet room does not. */
    function mirrorPath(buf, w, mid, span, open) {
      var i, p, x, h;
      ctx.beginPath();
      for (i = 0; i <= WAVE_N; i++) {
        p = i / WAVE_N; x = p * 4 - 2;
        h = (Math.abs(buf[i]) + open) * att(x, 4) * span;
        if (i === 0) ctx.moveTo(0, mid - h); else ctx.lineTo(p * w, mid - h);
      }
      for (i = WAVE_N; i >= 0; i--) {
        p = i / WAVE_N; x = p * 4 - 2;
        h = (Math.abs(buf[i]) + open) * att(x, 4) * span;
        ctx.lineTo(p * w, mid + h);
      }
      ctx.closePath();
    }

    function drawWave(f, stops, open, lit, light, sweeping) {
      var mid = f.h * 0.5, span = f.h * 0.42;
      ctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
      ctx.clearRect(0, 0, f.w, f.h);
      /* `lighter` is right on the dark theme and WRONG on the light one:
         adding light to a near-white page is close to a no-op. The light
         palette is dark ink on a pale ground, so it wants ordinary compositing
         and a little more alpha, since none of the brightness now comes from
         accumulation. Reduced transparency takes the same branch and for a
         related reason -- additive blending is exactly the effect it asks
         not to have. */
      var add = !light && !flat();
      ctx.globalCompositeOperation = add ? 'lighter' : 'source-over';
      var amul = (add ? 1 : 1.5) * (bold() ? 1.25 : 1);

      // the past, oldest first, so the newest ghost lands on top of the older
      for (var k = phosN - 1; k >= 0; k--) {
        var i2 = (phosHead - 1 - k + PHOS * 2) % PHOS;
        var fade = Math.pow(1 - k / PHOS, 1.7);
        // slightly reduced amplitude as well as opacity, so the stack reads as
        // depth rather than as six copies of one line
        tracePath(phos[i2], f.w, mid, span, 1 - 0.085 * (k + 1));
        ctx.strokeStyle = gradFor(f.w, stops, (0.05 + fade * 0.30) * lit * amul);
        ctx.lineWidth = 0.7 + fade * 0.8;
        ctx.stroke();
      }

      // the envelope, filled: what the eye reads from across the room
      mirrorPath(front, f.w, mid, span, open);
      ctx.fillStyle = gradFor(f.w, stops, (0.05 + flash * 0.05) * lit * amul);
      ctx.fill();

      /* The front trace: the live oscilloscope line, the only thing here with
         real detail in it. This stroke and the sweep highlight below are the
         ONLY two shadowBlur calls in the whole file -- the glow budget is one
         cached wash, one pre-tinted node halo and these two, because a blur
         pass per particle is what drops a tablet to single-figure frame
         rates. */
      tracePath(front, f.w, mid, span, 1);
      ctx.strokeStyle = gradFor(f.w, stops,
                                Math.min(1, (0.5 + flash * 0.45) * lit * amul));
      ctx.lineWidth = 1.2 + flash * 1.9 + (bold() ? 0.6 : 0);
      if (!calm()) {
        ctx.shadowBlur = 14 + flash * 24;
        ctx.shadowColor = alpha(stops[1], 0.55);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;

      /* `thinking` gets a bright segment travelling along the trace as well as
         the bump displacing it. Two channels for one gesture, because at the
         two ends of the shuttle the edge envelope has flattened the bump to
         nothing and a state that goes still for a moment reads as a state that
         has stopped. The highlight is drawn on the line's own y, so the
         envelope cannot erase it. */
      if (sweeping) {
        var from = Math.max(0, sweepP - 0.085), to = Math.min(1, sweepP + 0.085);
        if (to > from) {
          ctx.beginPath();
          for (var q = from; q <= to; q += 0.006) {
            var iq = Math.min(WAVE_N, Math.round(q * WAVE_N));
            var yq = mid + front[iq] * att(q * 4 - 2, 4) * span;
            if (q === from) ctx.moveTo(q * f.w, yq);
            else ctx.lineTo(q * f.w, yq);
          }
          ctx.strokeStyle = alpha(stops[1], light ? 0.9 : 0.85);
          ctx.lineWidth = 2;
          if (!calm()) { ctx.shadowBlur = 20; ctx.shadowColor = alpha(stops[2], 0.7); }
          ctx.stroke();
          ctx.shadowBlur = 0;
        }
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    /* ======================================================================= *
     *  THE RING -- the `mic` placement (AC-167)
     *
     *  `#wave` is MOVED into the composer's 44 px microphone button, and a
     *  horizontal band drawn 42 px wide is not a smaller band: it is the wrong
     *  drawing at the wrong aspect. The approved mock draws a CIRCULAR
     *  oscilloscope there (`web/mocks/j-chat.html`, drawRing) -- the radius at
     *  each angle is the trace, so a syllable makes the ring bulge and ripple
     *  -- with a handful of drifting nodes that link as the level rises. That
     *  link distance is the same lever the full-page lattice pulls, at the
     *  scale of a button.
     *
     *  WHICH drawing a frame gets is asked of the CANVAS and never of the
     *  page: a near-square surface gets a ring, a wide one gets a band. This
     *  file still knows nothing about the page's markup, the way drawLattice
     *  asks the canvas for its rectangle rather than asking a CSS class -- and
     *  a placement that later puts the canvas in some other square slot draws
     *  the right thing without anyone remembering to say so.
     *
     *  It reuses the level chain, the `front` buffer and the phosphor stack
     *  whole. The GEOMETRY is what changes here; the signal does not.
     * ======================================================================= */

    /* Angle to trace sample. A ring has no ends, so the buffer's first and
       last samples must AGREE or there is a kink at one angle, and two facts
       make that nearly free. The tap triggers on a rising zero crossing, so
       `front[0]` is at or just above zero by construction; the far end, 32 ms
       of sound later, is wherever the waveform happened to be. So the last
       sixth is tapered to zero on a raised cosine -- the polar form of the
       edge envelope the band draws with `att`, and much gentler than it,
       because there is one join to hide rather than two ends.
       The mirrored alternative -- walk the trace out and back, which is
       exactly periodic -- was built first and looked WORSE: it puts a cusp at
       six o'clock wherever the far end of the buffer is loud, and it halves
       the detail for a symmetry nothing here asks for. */
    var RING_FADE = 0.16;
    function radialAt(buf, u) {
      var p = clamp(u, 0, 1);
      var x = p * WAVE_N, k = x | 0;
      var b = k + 1 <= WAVE_N ? k + 1 : WAVE_N;
      var v = buf[k] + (buf[b] - buf[k]) * (x - k);
      if (p <= 1 - RING_FADE) return v;
      return v * (0.5 + 0.5 * Math.cos((p - 1 + RING_FADE) / RING_FADE
                                       * Math.PI));
    }

    function ringPath(buf, cx, cy, base, swing, n) {
      ctx.beginPath();
      for (var i = 0; i <= n; i++) {
        var u = i / n, a = -Math.PI / 2 + u * TAU;
        var r = base + swing * radialAt(buf, u);
        var x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
    }

    function drawRing(dt, f, stops, nodeCol, open, lit, light, sweeping) {
      var m = Math.min(f.w, f.h), cx = f.w * 0.5, cy = f.h * 0.5;
      ctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
      ctx.clearRect(0, 0, f.w, f.h);
      // the same compositing rule the band uses, and for the same reason:
      // `lighter` adds nothing to a near-white page
      var add = !light && !flat();
      ctx.globalCompositeOperation = add ? 'lighter' : 'source-over';
      var amul = (add ? 1 : 1.5) * (bold() ? 1.25 : 1);
      ctx.lineCap = 'round';

      /* The base radius carries the LEVEL and the `open` floor, which is the
         band's mirrored fill written in polar: an armed page in a quiet room
         is a ring that breathes, and an unarmed one is a still hairline.
         CLAMPED, which the band does not have to be: the band's `open` is a
         fraction of a half-height that has a whole strip to grow into, and the
         same number as a radius walks the ring off the edge of a 42 px button
         -- measured, an `idle` ring at 0.51 of the button's own size against a
         half-size of 0.5. Ceiling plus swing stays inside 0.47, so the trace
         never touches the border the button is drawn with. */
      var base = m * clamp(0.26 + live * 0.05 + open * 0.08, 0.26, 0.33);
      var swing = m * 0.105;
      var N = calm() ? 32 : 72;
      var pad = f.w * 0.35;

      // the past, oldest first, so the newest ghost lands on top
      for (var k = phosN - 1; k >= 0; k--) {
        var i2 = (phosHead - 1 - k + PHOS * 2) % PHOS;
        var fade = Math.pow(1 - k / PHOS, 1.7);
        ringPath(phos[i2], cx, cy, base * (1 - 0.028 * (k + 1)),
                 swing * (1 - 0.085 * (k + 1)), N);
        ctx.strokeStyle = gradFor(f.w, stops, (0.05 + fade * 0.30) * lit * amul,
                                  pad);
        ctx.lineWidth = 0.6 + fade * 0.5;
        ctx.stroke();
      }

      /* The disc, then the live trace over the SAME path -- what carries at a
         glance on a control this small is the filled area, and what carries up
         close is the line. */
      ringPath(front, cx, cy, base, swing, N);
      ctx.fillStyle = gradFor(f.w, stops, (0.05 + flash * 0.06) * lit * amul,
                              pad);
      ctx.fill();
      ctx.strokeStyle = gradFor(f.w, stops,
                                Math.min(1, (0.5 + flash * 0.45) * lit * amul),
                                pad);
      ctx.lineWidth = 1.1 + flash * 1.1 + (bold() ? 0.5 : 0);
      if (!calm()) {
        ctx.shadowBlur = 6 + flash * 10;
        ctx.shadowColor = alpha(stops[1], 0.55);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;

      /* The nodes. ALL-PAIRS on purpose: fourteen points is ninety-one
         distance tests a frame, and the uniform grid the lattice needs would
         cost more in bookkeeping than it saves here. */
      var used = calm() ? 5 : RING_MAX;
      var lim = m * (0.16 + live * 0.34), lim2 = lim * lim;
      var spread = m * 0.86, i, j;
      for (i = 0; i < used; i++) {
        var q = ringPts[i];
        /* STILL under calm() (RULING 1: with a constant level the geometry
           does not move). The band used to be the default placement and
           drew no nodes; with the band retired (docs/lantern-ui.md CU-3) the
           lattice's default canvas is this ring, so its drift had to stop
           here too. The level still opens and closes the links. */
        if (!calm()) {
          q.x += q.vx * dt; q.y += q.vy * dt;
          if (q.x < 0 || q.x > 1) { q.vx = -q.vx; q.x = clamp(q.x, 0, 1); }
          if (q.y < 0 || q.y > 1) { q.vy = -q.vy; q.y = clamp(q.y, 0, 1); }
        }
        ringX[i] = cx + (q.x - 0.5) * spread;
        ringY[i] = cy + (q.y - 0.5) * spread;
      }
      ctx.lineWidth = bold() ? 1.2 : 0.8;
      for (i = 0; i < used; i++) {
        for (j = i + 1; j < used; j++) {
          var dx = ringX[i] - ringX[j], dy = ringY[i] - ringY[j];
          var d2 = dx * dx + dy * dy;
          if (d2 >= lim2) continue;
          var lc = tint(stops[1], (1 - d2 / lim2) * 0.30 * lit * amul);
          if (!lc) continue;
          ctx.strokeStyle = lc;
          ctx.beginPath();
          ctx.moveTo(ringX[i], ringY[i]); ctx.lineTo(ringX[j], ringY[j]);
          ctx.stroke();
        }
      }
      var dot = tint(nodeCol, (0.34 + live * 0.42) * lit * amul);
      if (dot) {
        ctx.fillStyle = dot;
        for (i = 0; i < used; i++) {
          ctx.beginPath();
          ctx.arc(ringX[i], ringY[i], 0.9 + flash * 0.5, 0, TAU);
          ctx.fill();
        }
      }

      /* `thinking` gets its crest ORBITING, the polar form of the band's
         travelling highlight: one gesture across the page rather than two
         unrelated ones, and a shape no voice produces so it can never be read
         as a measurement. */
      if (sweeping) {
        ctx.beginPath();
        for (var s = 0; s <= 12; s++) {
          var u2 = sweepP - 0.085 + 0.17 * s / 12;
          var a2 = -Math.PI / 2 + u2 * TAU;
          var r2 = base + swing * radialAt(front, u2);
          var x2 = cx + Math.cos(a2) * r2, y2 = cy + Math.sin(a2) * r2;
          if (s === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2);
        }
        ctx.strokeStyle = alpha(stops[1], light ? 0.9 : 0.85);
        ctx.lineWidth = 1.8;
        if (!calm()) { ctx.shadowBlur = 10; ctx.shadowColor = alpha(stops[2], 0.7); }
        ctx.stroke();
        ctx.shadowBlur = 0;
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    /* ---- the wake-score trace ---------------------------------------------- */
    function drawTrace(f) {
      // The diagnostics panel is collapsed by default, so this canvas is
      // routinely 0 x 0. Not an error, and not worth a frame's work either.
      if (f.w < 8 || f.h < 8) return;
      tctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
      tctx.clearRect(0, 0, f.w, f.h);
      var pad = 4, h = f.h - pad * 2;
      var ty = pad + h * (1 - clamp(THRESHOLD, 0, 1));
      tctx.setLineDash([3, 4]);
      tctx.strokeStyle = alpha(C.threshold, 0.7);
      tctx.lineWidth = 1;
      tctx.beginPath();
      tctx.moveTo(0, ty); tctx.lineTo(f.w, ty);
      tctx.stroke();
      tctx.setLineDash([]);
      if (trace.length < 2) return;

      tctx.beginPath();
      for (var i = 0; i < trace.length; i++) {
        var x = (i / (TRACE_N - 1)) * f.w;
        var y = pad + h * (1 - clamp(trace[i], 0, 1));
        if (i === 0) tctx.moveTo(x, y); else tctx.lineTo(x, y);
      }
      tctx.strokeStyle = C.trace;
      tctx.lineWidth = 1.25;
      tctx.shadowBlur = 7;
      tctx.shadowColor = alpha(C.trace, 0.6);
      tctx.stroke();
      tctx.shadowBlur = 0;
      // A dot on every local maximum that CROSSED the line the caller named.
      // The old file marked the window 0.38..0.5, which was the hardcoded
      // threshold written a second time (D-28).
      for (var j = 1; j < trace.length - 1; j++) {
        var v = trace[j];
        if (v >= THRESHOLD && v > trace[j - 1] && v >= trace[j + 1]) {
          tctx.beginPath();
          tctx.arc((j / (TRACE_N - 1)) * f.w, pad + h * (1 - clamp(v, 0, 1)),
                   1.9, 0, TAU);
          tctx.fillStyle = C.threshold;
          tctx.fill();
        }
      }
    }

    /* ======================================================================= *
     *  THE LATTICE
     *
     *  Sparse points over the whole viewport, linked to their near neighbours
     *  with hairlines. LINK DISTANCE IS THE VOLUME LEVER: in silence the
     *  points are too far apart to link and the field is a scatter of dust; as
     *  the level rises the radius grows and the network snaps into being. The
     *  number of neighbours inside a radius goes as the AREA of the disc, so a
     *  6.4x change in radius is a 40x change in geometry -- no alpha ramp can
     *  do that.
     *
     *  Positioned and made dense by the SPECTRUM and nothing else. A particle
     *  reaches its target on a spring of tens of milliseconds and a 118 Hz
     *  voice has a pitch period of 8.5 ms, so a field asked to follow the
     *  waveform low-passes it into a smudge. The band above takes the time
     *  domain; this takes the frequency domain, which moves at syllable rate.
     *  That is why there is no waveNorm below this line.
     * ======================================================================= */

    /* Colour, quantised to 1/60ths and memoised. Without this the field builds
       an rgba string per link per frame through a regex -- tens of thousands
       of allocations a second, all of them one of about fifty values. Keyed on
       the colour STRING, so a new palette produces new entries by construction
       rather than by anyone remembering to clear the cache. */
    var colCache = Object.create(null), colN = 0;
    function tint(c, a) {
      var q = Math.round(clamp(a, 0, 1) * 60);
      if (q <= 0) return null;
      var key = c + q, v = colCache[key];
      if (!v) {
        if (++colN > 400) { colCache = Object.create(null); colN = 1; }
        v = colCache[key] = alpha(c, (q / 60).toFixed(3));
      }
      return v;
    }

    /* The spectrum sampled once per frame. A hundred points each asking the
       tap for their own density is a hundred interpolations a frame, and the
       answer only has sixteen distinguishable values across the width. */
    var LUTN = 128, DENS = new Float32Array(LUTN + 1);
    function buildDens(tap) {
      for (var i = 0; i <= LUTN; i++) DENS[i] = tap ? tap.atTilt(i / LUTN) : 0;
    }
    function densAt(p) { return DENS[(clamp(p, 0, 1) * LUTN) | 0]; }
    /* Best of three, not rejection sampling: a silent spectrum makes every
       candidate equally bad and a reject-until-accepted loop would spin. */
    function pickP() {
      var best = Math.random(), bs = densAt(best);
      for (var k = 0; k < 2; k++) {
        var c = Math.random(), s = densAt(c);
        if (s > bs) { best = c; bs = s; }
      }
      return best;
    }

    /* SPARSE ON PURPOSE -- about 100 points at 1440x900. The elegance of a
       proximity graph is in seeing an individual thread; past roughly 140 at
       this link distance the mesh closes into a sheet and every frame looks
       like every other one. Scaled by the SQUARE ROOT of viewport area,
       because what reads as a field tracks the linear size of the thing and
       not its area: a 390x844 phone gets about 50 rather than the 25 a linear
       scale by area would give. */
    var pool = null, poolW = 0, poolH = 0;
    function wantCount(f) {
      var n = Math.round(100 * Math.sqrt((f.w * f.h) / (1440 * 900)));
      if (calm()) n = Math.round(n * 0.62);
      return clamp(n, 40, 150);
    }
    function ensurePool(f) {
      var want = wantCount(f);
      if (pool && pool.length === want) {
        if (f.w !== poolW || f.h !== poolH) {
          var sx = f.w / Math.max(1, poolW), sy = f.h / Math.max(1, poolH);
          for (var k = 0; k < pool.length; k++) {
            pool[k].x *= sx; pool[k].y *= sy;
          }
          poolW = f.w; poolH = f.h;
        }
        return;
      }
      pool = [];
      for (var i = 0; i < want; i++) {
        pool.push({
          x: Math.random() * f.w, y: Math.random() * f.h,
          vx: 0, vy: 0,
          s: 1.0 + Math.random() * 1.0,                 // node radius, px
          sp: 0.62 + Math.random() * 0.9,               // per-point eagerness
          ph: Math.random() * TAU,
          hp: (i + 0.5) / want,                         // slot across the width
          off: Math.random() * 2 - 1,
          drift: (Math.random() * 2 - 1) * 0.010,
          side: i % 2 ? 1 : -1,                         // which half of the field
          life: 0, lifeMax: 1,
          a: 0, fall: 0, lit: 0                         // this frame's three
        });
      }
      poolW = f.w; poolH = f.h;
    }

    /* The field must thin out where the page has TEXT. A SUPER-gaussian,
       exp(-u^4) rather than exp(-u^2), and that difference is the whole point:
       a plain gaussian tuned to dim the headline was already down to 0.57 at
       the edge of the band, so it dimmed the one place the field is supposed
       to live. The fourth power is flat across the middle and then falls off a
       cliff. Never a hard mask -- a mask edge is a second horizontal line on a
       page that already has one, and the eye finds it instantly.

       The scale is the BAND's height and not the viewport's, which is a
       correction to the mock this came from. That mock grew --wave-h to
       220-330 px because the wave was its whole subject; the shipped page
       keeps 150-212 px, and its sentence under the headline sits sixteen
       pixels above the band. A viewport-relative falloff drew the mesh
       straight across that sentence and across the transcript, which is the
       one thing this function exists to prevent. Tied to the band, the two
       move together whenever --wave-h does.

       At 1440x900 on the shipped layout: 1.0 at the centreline, 0.91 sixty
       pixels out, 0.42 at the band's own edge, 0.19 at the sentence under the
       headline and 0.01 at the transcript. It multiplies both the node alpha
       and the link alpha, and a point under 0.03 is left out of the grid
       entirely -- so the falloff buys back some of the pair loop it costs
       nothing to run. */
    function textFall(y, mid, scale) {
      var u = (y - mid) / scale, u2 = u * u;
      return Math.exp(-u2 * u2);
    }

    /* ---- the pair loop, on a coarse grid ----------------------------------- *
     * All-pairs at 100 points is 4950 distance tests a frame and would in fact
     * be survivable -- but the link radius is at its widest exactly when the
     * field is densest and the band renderer is busiest, so the naive loop is
     * worst in the worst frame. The points are binned into a uniform grid
     * whose cell is the current link distance, and each point is tested
     * against its own cell plus the four FORWARD neighbours, which visits
     * every pair exactly once.
     *
     * The bins are two Int32Arrays allocated ONCE -- head-of-chain per cell,
     * next-in-chain per point -- and refilled in place. A fresh array of
     * buckets per frame is sixty allocations a second for the life of the
     * page, and a links array would be another; links are stroked as they are
     * found and never collected.
     *
     * Squared distance only. Math.sqrt in the inner loop is the one call that
     * would show up here, and (1 - d2/lim2) is a perfectly good falloff which
     * is zero at the limit -- the only property that matters, because a link
     * that pops in at half brightness is a flicker. */
    var GRID_MAX = 4096;
    var gridHead = new Int32Array(GRID_MAX), gridNext = new Int32Array(160);
    /* Live counters. AC-75 says the link swing is MEASURED in a real browser
       and not asserted, so the numbers that would prove it are readable rather
       than reasoned about. */
    var lattice = { links: 0, tests: 0, dist: 0, nodes: 0, count: 0 };

    function linkPass(f, lim, stops, base, wide) {
      var i, j, n = pool.length;
      if (gridNext.length < n) gridNext = new Int32Array(n);
      var cell = Math.max(24, lim), cols, rows;
      for (;;) {
        cols = Math.max(1, Math.ceil(f.w / cell));
        rows = Math.max(1, Math.ceil(f.h / cell));
        if (cols * rows <= GRID_MAX) break;
        cell *= 1.5;
      }
      gridHead.fill(-1, 0, cols * rows);
      var nodes = 0;
      for (i = 0; i < n; i++) {
        var p = pool[i];
        gridNext[i] = -1;
        if (p.fall < 0.03 || p.x < -cell || p.x > f.w + cell) continue;
        var cx = clamp(p.x / cell | 0, 0, cols - 1);
        var cy = clamp(p.y / cell | 0, 0, rows - 1);
        var c = cy * cols + cx;
        gridNext[i] = gridHead[c]; gridHead[c] = i; nodes++;
      }

      var lim2 = lim * lim, links = 0, tests = 0;
      fctx.lineWidth = wide ? 1.6 : 1;
      for (var cy2 = 0; cy2 < rows; cy2++) {
        for (var cx2 = 0; cx2 < cols; cx2++) {
          for (i = gridHead[cy2 * cols + cx2]; i >= 0; i = gridNext[i]) {
            var a0 = pool[i];
            for (var k = 0; k < 5; k++) {
              // own cell first, and inside it only the rest of the chain: the
              // list is walked forward from i, so a same-cell pair is seen once
              var nx = cx2 + (k === 0 ? 0 : k === 1 ? 1 : k - 3);
              var ny = cy2 + (k < 2 ? 0 : 1);
              if (nx < 0 || nx >= cols || ny >= rows) continue;
              j = k === 0 ? gridNext[i] : gridHead[ny * cols + nx];
              for (; j >= 0; j = gridNext[j]) {
                var b0 = pool[j];
                var dx = a0.x - b0.x, dy = a0.y - b0.y;
                var d2 = dx * dx + dy * dy;
                tests++;
                if (d2 >= lim2) continue;
                /* Alpha falls off with distance -- lit when nearly touching,
                   nothing at the limit -- and is held down by whichever end is
                   further into the page's text. The colour comes from the
                   link's own midpoint across the width, so the field keeps the
                   three-stop horizontal gradient the band has. */
                var fall = a0.fall < b0.fall ? a0.fall : b0.fall;
                var mx = (a0.x + b0.x) * 0.5;
                var col = tint(stops[mx < f.w * 0.34 ? 0 : mx < f.w * 0.66 ? 1 : 2],
                               (1 - d2 / lim2) * base * fall);
                if (!col) continue;
                fctx.strokeStyle = col;
                fctx.beginPath();
                fctx.moveTo(a0.x, a0.y);
                fctx.lineTo(b0.x, b0.y);
                fctx.stroke();
                links++;
              }
            }
          }
        }
      }
      lattice.links = links; lattice.tests = tests; lattice.dist = lim;
      lattice.nodes = nodes; lattice.count = n;
    }

    /* One cached radial wash under the points, and it is the whole reason
       there is no shadowBlur in the loops above: a per-particle shadow is a
       blur pass per particle, and a hundred of them costs more than the entire
       mesh. A gradient built once and filled once buys the same "the field is
       glowing" read for one draw call. */
    var wash = null, washKey = '';
    function washFor(f, stops, mid) {
      var my = Math.round(mid / 8) * 8;
      var key = Math.round(f.w) + 'x' + Math.round(f.h) + ':' + my + ':' + stops[1];
      if (key !== washKey) {
        wash = fctx.createRadialGradient(f.w / 2, my, 0, f.w / 2, my,
                                         Math.max(f.w, f.h) * 0.46);
        wash.addColorStop(0, alpha(stops[1], 1));
        wash.addColorStop(0.42, alpha(stops[1], 0.34));
        wash.addColorStop(1, alpha(stops[1], 0));
        washKey = key;
      }
      return wash;
    }

    /* THE LINK DISTANCE IS THE LOUDNESS, stated plainly because it is the
       whole claim:  want = LINK_MIN + (LINK_MAX - LINK_MIN) * lvl.
       LINK_MIN is below the typical nearest-neighbour distance at this
       density, so silence is genuinely unconnected dust rather than a faint
       mesh. Smoothed asymmetrically like everything else here: 60 ms up so a
       syllable arrives at once, 260 ms down so the constellation dissolves
       rather than blinking out between words.

       `idle` gets a small floor of its own instead of the raw level, because
       an armed microphone in a silent room must not look like an unarmed one
       -- that is the one difference on this page that may never be subtle.
       `thinking` gets a fixed figure because there is no sound at all to
       measure and a fabricated level would be a drawing of a measurement
       nobody took. */
    /* THE BREATH, said once. Two draw functions computed this identically
       and the voice-effect resting floor needs it too; three copies of a
       rhythm is three chances for one of them to be retuned alone.
       RULING 1 (CLAUDE.md 2026-09-16 backlog): reduced motion FREEZES this
       rather than merely damping it. MEASURED before this fix: with the page
       armed and calm() true, the lattice's own nonzero-alpha pixel count
       moved 4,272 -> 22,068 over about 7s, and this sine was one of the
       still-running clocks behind that drift -- every consumer below (the
       lattice's idle sway, the still-halo floor in frame()) read a value that
       kept moving for as long as the page stayed open. 0.5 is the sine's own
       midpoint, chosen so the frozen reading matches the animated one's time
       average rather than landing on an arbitrary phase. */
    function breathNow() { return calm() ? 0.5 : 0.5 + 0.5 * Math.sin(t / 2600); }

    var LINK_MIN = 22, LINK_MAX = 140, linkD = LINK_MIN;
    var prevState = 'off', errKick = 0;

    function drawLattice(dt, f, stops, nodeCol) {
      fctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
      fctx.clearRect(0, 0, f.w, f.h);
      ensurePool(f);

      // The band's own rectangle, in viewport coordinates, because #field is
      // fixed at inset 0. Asked of the canvas rather than of a CSS class, so
      // this file still knows nothing about the page's markup.
      var band = mainCanvas.getBoundingClientRect();
      var mid = band.top + band.height / 2;
      /* How far out a point may be aimed, and how fast the text falloff bites,
         are BOTH fractions of the band's own height, so they cannot disagree.
         The cap is there for a very short viewport, where a band clamped to
         150 px would otherwise squeeze the whole network into a hairline. */
      var reach = clamp(band.height * 0.55, 70, f.h * 0.22);
      var fallScale = Math.max(80, band.height * 0.52);
      var listening = state === 'capturing'
                    || state === 'closing';
      var speaking = state === 'speaking';
      var thinking = state === 'thinking';
      var errored = state === 'error';
      var off = state === 'off';
      /* RULING 1 (CLAUDE.md 2026-09-16 backlog): this used to be 0.55, which
         only SLOWED the drift and never stopped it -- the 4,272 -> 22,068
         pixel drift quoted above `breathNow()` was measured with this exact
         damping in place. 0 is what actually freezes a particle: every
         velocity update below reads `(target * motion - p.vx) * kv`, so at
         motion=0 the target term vanishes and vx/vy decay toward zero
         instead of chasing a new one every frame. The position integration a
         little further down is ALSO gated on `!calm()` directly, as a second,
         independent guarantee -- a frozen velocity is not by itself proof of
         a frozen position, since one direct assignment (the listening-state
         re-slot) is not expressed as a velocity at all. */
      var motion = calm() ? 0 : 1;
      var add = !isLight() && !flat();
      var amul = (add ? 1 : 1.7) * (bold() ? 1.3 : 1);
      var glow = 1 + flash * 1.2;
      var breath = breathNow();

      /* A state change is an EVENT, not a value. Error is the only one that
         needs an impulse: the lattice TEARS and goes static, which no other
         state does, and that geometry is half of why an error cannot be
         mistaken for anything else. */
      if (state !== prevState) {
        if (errored) {
          errKick = 1;
          for (var q = 0; q < pool.length; q++) {
            var e0 = pool[q], ex = e0.x - f.w / 2, ey = e0.y - mid;
            var el = Math.max(30, Math.sqrt(ex * ex + ey * ey));
            // 0.19 px/ms and not more: faster and the whole field clears the
            // viewport inside a second, which leaves the error state as an
            // EMPTY page -- a crash rather than a scatter.
            e0.vx = ex / el * 0.19 * e0.sp * motion;
            e0.vy = ey / el * 0.19 * e0.sp * motion;
          }
        }
        prevState = state;
      }
      errKick *= Math.exp(-dt / 420);

      fctx.globalCompositeOperation = add ? 'lighter' : 'source-over';
      // Reduced transparency asks for no washes and no additive glow, so the
      // field keeps its structure and loses its atmosphere.
      var washAmt = flat() || off ? 0
                  : errored ? 0.05 * errKick
                  : (listening || speaking) ? 0.035 + live * 0.10
                  : thinking ? 0.035 : 0.018 + live * 0.05;
      if (washAmt > 0.002) {
        fctx.globalAlpha = Math.min(0.22, washAmt * glow * amul);
        fctx.fillStyle = washFor(f, stops, mid);
        fctx.fillRect(0, 0, f.w, f.h);
        fctx.globalAlpha = 1;
      }

      var sweepX = sweepP * f.w;
      fctx.lineCap = 'round';

      /* PASS 1 -- physics, and this frame's three per-point numbers. Nothing
         is drawn here: the links have to go UNDER the nodes, and one loop
         drawing both would put half the mesh on top of the points it
         connects. */
      for (var i = 0; i < pool.length; i++) {
        var p = pool[i], a = 0, d = 0;

        if (off) {
          // still, scattered, nearly invisible: the microphone is not armed
          // and the page must not suggest otherwise
          p.vx *= Math.exp(-dt / 200); p.vy *= Math.exp(-dt / 200);
          a = 0.05;
        } else if (errored) {
          // scattered by the impulse above, then still. Nothing re-arms the
          // motion, because an error is the one state that is not listening.
          p.vx *= Math.exp(-dt / 260); p.vy *= Math.exp(-dt / 260);
          a = 0.26 + 0.22 * errKick;
        } else if (thinking) {
          /* No spectrum to sit in, so the field gets the same travelling sweep
             the band has, as a column crossing the page. A field that only
             drifts is indistinguishable from `idle` in a screenshot, which is
             the test this state kept failing -- and it is the honest answer to
             "there is no audio here": move something that is plainly not a
             reading rather than invent a reading. */
          var kvT = co(dt, 320);
          var tyT = mid + p.off * reach * 0.52
                  + Math.sin(t / 2100 + p.ph) * reach * 0.06;
          var dxs = (p.x - sweepX) / (f.w * 0.10);
          var nearS = Math.exp(-dxs * dxs);
          p.vx += ((0.016 + nearS * 0.062) * p.sp * motion - p.vx) * kvT;
          p.vy += ((tyT - p.y) * 0.0013 * motion - p.vy) * kvT;
          a = 0.09 + nearS * 0.46;
        } else if (listening) {
          /* Drifting INWARD. The point is aimed at the envelope, which sits
             further out where its band is loud; when it arrives it is
             re-slotted outside the band at a density-picked position and
             drifts in again. That re-slot is what keeps the motion going -- a
             pure spring arrives once and then the field is a still image.

             STRATIFIED: p.off is fixed per point, so each aims at its own
             fraction of the way out instead of all of them landing on the
             envelope. Aimed at the envelope itself the field collapses into
             two dense horizontal slabs with the band empty between them, and a
             proximity graph on two lines is not a network, it is two ropes. */
          d = densAt(p.hp);
          var envY = mid + p.side * (0.08 + d * 0.80) * reach
                   * (0.26 + 0.74 * Math.abs(p.off));
          var kv = co(dt, 90 + 90 * (1 - p.sp));
          p.vx += ((p.hp * f.w - p.x) * 0.0016 * motion - p.vx) * kv;
          p.vy += ((envY - p.y) * 0.0030 * motion * (1 + flash * 0.6) - p.vy) * kv;
          // the onset kick: a word arriving jolts the field rather than only
          // brightening it, and the jolt is gone in about 150 ms with `flash`
          p.vx += (Math.random() - 0.5) * flash * 0.26 * p.sp * motion;
          p.vy += (Math.random() - 0.5) * flash * 0.42 * p.sp * motion;
          var gone = Math.abs(p.y - envY);
          if (gone < 9) {
            p.hp = pickP();
            p.x = p.hp * f.w;
            // 0.90 to 1.20 reach: with the text falloff above, a point
            // re-slotted further out spends most of its life at an alpha of
            // nothing and the field visibly thins between syllables
            p.y = mid + p.side * reach * (0.90 + Math.random() * 0.30);
            p.vy = -p.side * 0.012 * p.sp * motion;
          }
          // dim far out, bright as it lands, brighter where the band is loud:
          // existence, brightness and distance off one measurement
          var appr = 1 - Math.min(1, gone / (reach * 1.3));
          a = (0.06 + appr * 0.34) * (0.34 + d * 1.10) * (0.50 + live * 1.05);
        } else if (speaking) {
          // Drifting OUTWARD: emitted from the same envelope, accelerating
          // away. Same geometry, opposite flow -- who is talking is never
          // carried by a different SHAPE.
          p.life -= dt;
          if (p.life <= 0 || p.y < -30 || p.y > f.h + 30) {
            p.hp = pickP();
            p.x = p.hp * f.w;
            p.y = mid + p.side * (0.08 + densAt(p.hp) * 0.80) * reach
                * (0.26 + 0.74 * Math.abs(p.off));
            p.vx = (Math.random() - 0.5) * 0.014 * motion;
            p.vy = p.side * (0.008 + live * 0.032) * p.sp * motion;
            p.lifeMax = 1400 + Math.random() * 1800;
            p.life = p.lifeMax;
          }
          d = densAt(p.hp);
          p.vy += p.side * (0.00003 + live * 0.00022) * p.sp * motion * dt
                * (1 + flash * 0.7);
          p.vx += (Math.random() - 0.5) * 0.000015 * dt;
          var frac = p.life / p.lifeMax;
          a = frac * frac * (0.34 + d * 1.05) * (0.20 + live * 0.66);
        } else {
          // idle: armed, waiting for the wake word. A slow drift at low
          // opacity, loosely around the band, breathing.
          var kv3 = co(dt, 700);
          var ty3 = mid + p.off * reach * (0.42 + breath * 0.10 + live * 1.2)
                  + Math.sin(t / 3400 + p.ph) * reach * 0.06;
          p.vx += (p.drift * motion * (0.5 + live) - p.vx) * kv3;
          p.vy += ((ty3 - p.y) * 0.0011 * motion - p.vy) * kv3;
          a = (0.18 + live * 0.44) * (0.72 + 0.28 * breath);
        }

        /* RULING 1 (CLAUDE.md 2026-09-16 backlog): gated directly on
           `!calm()` rather than trusting motion=0 alone -- see the comment
           above `motion`'s declaration for why a frozen velocity is not by
           itself a frozen position. */
        if (!calm()) {
          p.x += p.vx * dt;
          p.y += p.vy * dt;
        }
        /* Wrap horizontally, clamp vertically -- but NOT in the two states
           that are supposed to be still. A scattered field that wraps re-enters
           from the other edge and reads as alive, which is the opposite of
           what `off` and `error` are saying. A point that leaves a full-page
           field otherwise never comes back and the field thins out over a
           session: a slow leak rather than a visible bug. */
        if (!off && !errored) {
          if (p.x < -40) p.x = f.w + 40;
          else if (p.x > f.w + 40) p.x = -40;
        }
        if (!speaking && !listening) {
          if (p.y < -40) { p.y = -40; p.vy = Math.abs(p.vy) * 0.3; }
          else if (p.y > f.h + 40) { p.y = f.h + 40; p.vy = -Math.abs(p.vy) * 0.3; }
        }

        /* The onset flare, staggered per point off its own phase, so a word
           lights the constellation unevenly like a real thing rather than
           switching the whole field up a stop. */
        p.lit = Math.max(0, flash * (0.55 + 0.45 * Math.sin(p.ph)));
        p.fall = textFall(p.y, mid, fallScale);
        p.a = bold() ? Math.max(a, 0.22) : a;
      }

      /* PASS 2 -- the links, under everything. `base` carries the state and
         the theme; the per-link alpha carries the distance and the falloff. */
      var lvl = off || errored ? 0
              : thinking ? 0.34 + breath * 0.06
              : (listening || speaking) ? Math.min(1, live * 1.15 + flash * 0.3)
              : 0.10 + live * 1.0;
      var wantD = LINK_MIN + (LINK_MAX - LINK_MIN) * clamp(lvl, 0, 1);
      linkD += (wantD - linkD) * co(dt, wantD > linkD ? 60 : 260);

      var linkBase = off ? 0
                   : errored ? 0.20 * (0.3 + errKick)
                   : thinking ? 0.34
                   : (listening || speaking) ? 0.30 + live * 0.42 + flash * 0.20
                   : 0.26;
      if (bold()) linkBase *= 1.35;
      if (linkBase > 0.004) {
        linkPass(f, linkD, stops, linkBase * amul, bold());
      } else {
        // `off` never runs the pair loop, so the counters are zeroed here
        // rather than left holding the last state's numbers: a diagnostic
        // reporting a stale figure is worse than one reporting nothing.
        lattice.links = 0; lattice.tests = 0; lattice.dist = linkD;
        lattice.nodes = 0; lattice.count = pool.length;
      }

      /* PASS 3 -- the nodes, on top and nearly white. A filled arc plus one
         wider dim arc for a halo when it is bright: two fills, never a
         shadowBlur, because a per-node blur pass costs more than the whole
         mesh under it. */
      for (var n2 = 0; n2 < pool.length; n2++) {
        var q2 = pool[n2];
        var na = q2.a * q2.fall * glow * amul * (1 + q2.lit * 1.15);
        var col = tint(nodeCol, na);
        if (!col) continue;
        var r = q2.s * (1 + q2.lit * 0.6);
        if (!calm() && !flat() && na > 0.10) {
          fctx.fillStyle = tint(nodeCol, na * 0.16);
          fctx.beginPath(); fctx.arc(q2.x, q2.y, r * 3.2, 0, TAU); fctx.fill();
        }
        fctx.fillStyle = col;
        fctx.beginPath(); fctx.arc(q2.x, q2.y, r, 0, TAU); fctx.fill();
      }
      fctx.globalCompositeOperation = 'source-over';
    }

    /* ======================================================================= *
     *  THE VOICE-EFFECT CANVAS TREATMENTS -- `ember` and `ribbon`
     *  (STEP 3, docs/voice-effects.md, PROPOSAL AC-252..AC-262)
     *
     *  `mist` and `bloom` are CSS and never reach here; these two are the
     *  ones that need a canvas, and the canvas is `mainCanvas` itself --
     *  moved into the composer's `#waveFx` slot by `app.js`'s
     *  `setVoiceEffect`, the same one that draws the ring/band/lattice below
     *  in every other mode. One canvas, one `create()`, still.
     *
     *  THE FIELD RECT comes from the composer's own geometry and nowhere
     *  else. `#composerFx` declares its four insets and its corner radius as
     *  custom properties (index.html, beside `.field`); this reads them off
     *  `mainCanvas`'s OWN computed style rather than restating "40/24/34/24"
     *  as a second literal that drifts the day either changes. The insets
     *  are negative (the layer is LARGER than the composer it surrounds), so
     *  their magnitude is exactly what recovers the composer's rectangle
     *  inside the canvas -- the same negation `.fxbloom` already performs in
     *  CSS. Cached after the first read: these are fixed pixel literals in
     *  the stylesheet, not something a frame can change, and a
     *  `getComputedStyle` call is not one worth repeating sixty times a
     *  second. `--fx-radius` is the ONE custom property this step adds to
     *  index.html -- nothing on the canvas's own inherited properties named
     *  a radius, and the alternative was a fourth hardcoded number here. */
    var fxInsets = null;
    function fxFieldRect(fw, fh) {
      if (!fxInsets) {
        var cs = global.getComputedStyle(mainCanvas);
        fxInsets = {
          t: Math.abs(parseFloat(cs.getPropertyValue('--fx-t')) || 0),
          r: Math.abs(parseFloat(cs.getPropertyValue('--fx-r')) || 0),
          b: Math.abs(parseFloat(cs.getPropertyValue('--fx-b')) || 0),
          l: Math.abs(parseFloat(cs.getPropertyValue('--fx-l')) || 0),
          rad: Math.abs(parseFloat(cs.getPropertyValue('--fx-radius')) || 0)
        };
      }
      var w = Math.max(1, fw - fxInsets.l - fxInsets.r);
      var h = Math.max(1, fh - fxInsets.t - fxInsets.b);
      // Clamped to the rect actually available: a composer that has wrapped
      // to two rows on a narrow phone is still whatever size it is, and a
      // fixed 26px radius on a rect narrower than 52px would send `perimeter`
      // a negative straight run.
      return { x: fxInsets.l, y: fxInsets.t, w: w, h: h,
               r: Math.min(fxInsets.rad, w / 2, h / 2) };
    }

    /* THE COLOUR (AC-254): `--in` while listening, `--out` while speaking,
       read off `mainCanvas`'s computed style rather than redeclared here.
       Each style declares them per theme (docs/lantern-ui.md CU-4), so the
       cache is keyed on the palette `app.js` assigns after every style or
       theme change -- the `isLight` pattern below: a new palette re-reads
       them, and an unchanged one costs no style read per frame. */
    var fxColorIn = null, fxColorOut = null, fxKey = '';
    function fxColour(speakingOut) {
      var key = C.meshIn[0] + C.meshOut[0] + C.nodeIdle;
      if (key !== fxKey) {
        fxKey = key;
        var cs = global.getComputedStyle(mainCanvas);
        fxColorIn = cs.getPropertyValue('--in').trim();
        fxColorOut = cs.getPropertyValue('--out').trim();
      }
      return speakingOut ? fxColorOut : fxColorIn;
    }

    /* Sizes the canvas and clears it in CSS-pixel space, exactly the
       preamble `drawWave`/`drawRing` each do for themselves. Shared here
       because every branch below -- ember, ribbon, and "nothing" -- starts
       with it: AC-260 is that switching treatments leaves no residue, and
       the cheapest way to guarantee that is to never skip the clear rather
       than to remember to call it on the one frame something changed. */
    function fxBeginFrame(f) {
      ctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
      ctx.clearRect(0, 0, f.w, f.h);
    }

    /* ---- ember --------------------------------------------------------- *
     * Particles born at the field's perimeter: OUT along the outward normal
     * while speaking, IN from the margin toward the edge while listening.
     * Ported whole from web/mocks/r-plume.html's `drawEmber` -- what changed
     * is the level's source (the SAME `live` the ring/band/lattice read, no
     * second follower) and the rect it walks (the composer's own rect from
     * `fxFieldRect`, not the mock's fixed demo box).
     *
     * An INBOUND particle dies AT the edge and must never enter the field --
     * the `inside` test below is the mock's own, kept whole. The composer's
     * opaque background would occlude a particle that got in anyway, but a
     * particle removed here is one the NEXT frame does not have to paint
     * over either, so the two guards are not redundant, only the second is
     * invisible. */
    var fxEmbers = [];
    function drawFxEmber(f, L, out, tintCol) {
      fxBeginFrame(f);
      var R = fxFieldRect(f.w, f.h);
      var ps = fxEmbers;

      if (L > 0.04) {
        var ring = perimeter(R, 160);
        var want = Math.round(L * 4);
        for (var i = 0; i < want; i++) {
          var p = ring[(Math.random() * ring.length) | 0];
          var sp = (14 + Math.random() * 46) * (0.35 + L);
          var jx = (Math.random() - 0.5) * 10, jy = (Math.random() - 0.5) * 10;
          ps.push(out
            // born ON the edge, travelling out along the outward normal
            ? { x: p.x, y: p.y, vx: p.nx * sp + jx, vy: p.ny * sp + jy,
                r: 1 + Math.random() * 2.3, a: 1, out: true }
            // born OUT in the margin, falling back toward the edge
            : { x: p.x + p.nx * 46, y: p.y + p.ny * 40,
                vx: -p.nx * sp * 0.8 + jx, vy: -p.ny * sp * 0.8 + jy,
                r: 0.9 + Math.random() * 2, a: 1, out: false });
        }
      }
      ctx.fillStyle = tintCol;
      for (var k = ps.length - 1; k >= 0; k--) {
        var q = ps[k];
        q.x += q.vx / 60; q.y += q.vy / 60;
        q.a -= q.out ? 0.014 : 0.018;
        var inside = q.x > R.x + 2 && q.x < R.x + R.w - 2
                  && q.y > R.y + 2 && q.y < R.y + R.h - 2;
        if (q.a <= 0 || inside ||
            q.x < -12 || q.x > f.w + 12 || q.y < -12 || q.y > f.h + 12) {
          ps.splice(k, 1); continue;
        }
        ctx.globalAlpha = Math.max(0, q.a) * 0.72;
        ctx.beginPath(); ctx.arc(q.x, q.y, q.r, 0, TAU); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    /* ---- ribbon ---------------------------------------------------------- *
     * A contour traced round the field, displaced outward by
     * `gap + amp * sin(u*34 + phase)`, plus a second fainter contour further
     * out once the level clears 0.25. Ported whole from
     * web/mocks/r-plume.html's `drawRibbon`, TUNED numbers kept exactly: an
     * earlier draft at amp `L*18` and frequency 22 read as an amoeba rather
     * than a ripple and was rejected on sight, so amp is `L*9` and the
     * frequency stays 34.
     *
     * `phase` is the one number that does NOT carry over unchanged: the
     * mock's `t` counts SECONDS (`t += dt` with `dt` in seconds) and this
     * file's `t` counts MILLISECONDS (`frame()`'s `t += dt` with `dt` in ms,
     * the same clock `sweepP` and the mist/lattice breathing already use) --
     * so `t / 1000` is what reproduces the mock's tuned angular speed rather
     * than spinning the ripple a thousand times too fast. */
    function drawFxRibbon(f, L, out, tintCol, tMs) {
      fxBeginFrame(f);
      if (L <= 0.02) return;
      var R = fxFieldRect(f.w, f.h);
      var pts = perimeter(R, 260);
      var amp = L * 9;
      var gap = 8 + L * (out ? 18 : 4);
      var phase = (tMs / 1000) * (out ? 2.6 : -2.0);

      ctx.lineWidth = 1.8; ctx.strokeStyle = tintCol;
      ctx.globalAlpha = 0.25 + L * 0.5;
      ctx.beginPath();
      for (var i = 0; i < pts.length; i++) {
        var p = pts[i];
        var d = gap + amp * Math.sin(p.u * 34 + phase);
        var x = p.x + p.nx * d, y = p.y + p.ny * d;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath(); ctx.stroke();

      // a second, fainter contour further out gives the ripple somewhere to go
      if (L > 0.25) {
        ctx.globalAlpha = (L - 0.25) * 0.35;
        ctx.beginPath();
        for (var j = 0; j < pts.length; j++) {
          var p2 = pts[j];
          var d2 = gap * 1.8 + amp * 1.15 * Math.sin(p2.u * 34 + phase - 0.9);
          var x2 = p2.x + p2.nx * d2, y2 = p2.y + p2.ny * d2;
          if (j === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2);
        }
        ctx.closePath(); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    /* ======================================================================= *
     *  THE CREATIVE FOUR -- `sonar`, `filaments`, `comet`, `dust`
     *  (STEP 5, docs/voice-effects.md, PROPOSAL AC-252..AC-262. A fifth,
     *  `aurora`, shipped here too and was CUT under RULING 2, CLAUDE.md
     *  2026-09-16 backlog -- see the note where it used to sit, just above.)
     *
     *  Same rules as ember/ribbon above: born at the field's perimeter
     *  (`fxFieldRect` plus `perimeter()`), drawn outward, the level and
     *  direction come from the SAME `live`/`chain`/`tap` this file already
     *  computes in `frame()` -- no second envelope follower, no second tap,
     *  no AudioContext.
     * ======================================================================= */

    /* Rounded-rect path, drawn with arcTo rather than ctx.roundRect: the rest
       of this file builds every curve by hand (see `perimeter()`), and a
       fourth way to describe the same rectangle was not worth the newer API
       for four calls a frame. */
    function roundRectPath(x, y, w, h, r) {
      var rr = Math.max(0, Math.min(r, w / 2, h / 2));
      ctx.beginPath();
      ctx.moveTo(x + rr, y);
      ctx.lineTo(x + w - rr, y);
      ctx.arcTo(x + w, y, x + w, y + rr, rr);
      ctx.lineTo(x + w, y + h - rr);
      ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
      ctx.lineTo(x + rr, y + h);
      ctx.arcTo(x, y + h, x, y + h - rr, rr);
      ctx.lineTo(x, y + rr);
      ctx.arcTo(x, y, x + rr, y, rr);
      ctx.closePath();
    }

    /* How far a ray from point (px,py) along unit normal (nx,ny) can travel
       before it leaves the canvas rectangle [0,w]x[0,h]. `dust` uses this to
       push a shape outward by a LEVEL-SCALED distance that can otherwise
       exceed the canvas's own small side insets (24px) even while
       comfortably inside the larger top/bottom ones (40px/34px) -- its
       speaking target reaching 54px is exactly that case, MEASURED as a
       near-total loss of visible motes rather than a partial one, because
       most of a wide, short composer's perimeter samples fall on the SHORT
       sides. (Formerly shared with `aurora`, which used the same cap for its
       curtain length before being CUT under RULING 2, CLAUDE.md 2026-09-16
       backlog.) */
    function rayExit(px, py, nx, ny, w, h) {
      var dx = nx > 0 ? (w - px) / nx : nx < 0 ? -px / nx : Infinity;
      var dy = ny > 0 ? (h - py) / ny : ny < 0 ? -py / ny : Infinity;
      return Math.min(dx, dy);
    }

    /* ---- sonar ------------------------------------------------------------ *
     * A ring per ONSET, not per volume -- the one treatment that draws the
     * STRUCTURE of speech rather than its loudness (docs/voice-effects.md 2).
     *
     * `chain.transient()` is an ENVELOPE, tuned for the 150ms decay `flash`
     * uses a few lines below in frame() -- it PEAKS on an onset but never
     * reports a discrete event. This derives one: a THRESHOLD crossing on the
     * raw transient value, held off by a REFRACTORY period so a syllable's
     * own rise cannot retrigger the same ring while it is still climbing.
     *
     * SONAR_THRESH was first set to 0.16 and MEASURED AS UNRELIABLE against a
     * more realistic burst (linear 40ms attack, 180ms decay, repeated every
     * 300ms, the shape a reviewer drove this with): the FIRST onset produces
     * a clean tr spike (0.7-0.9), but `makeChain`'s own loud reference
     * (`refEst`) then chases up toward that burst's peak within one cycle --
     * its "freeze while nothing is being said" branch never engages, because
     * this pattern's floor between bursts (~0.03-0.04 rms) sits well above
     * 4x the still-low noise-floor estimate, so refEst never gets to treat
     * the gaps as silence. Every later onset in the SAME run is then judged
     * against an already-adapted reference and produces tr in the 0.12-0.27
     * range -- some of it below 0.16, and MARGINAL ENOUGH that identical runs
     * disagreed: repeated headless captures of the identical 8-burst
     * stimulus at 0.16 gave ring counts of 3, 6 and 8 out of 8 across
     * different runs, and a reviewer's own run gave 0. This is not a second
     * DSP stage to fix (`makeChain` is shared with the band/ring/lattice and
     * is out of reach here) -- it is `drawFxSonar`'s own threshold sitting on
     * the wrong side of a real, load-bearing compression the chain performs
     * on purpose for continuous speech. SONAR_THRESH=0.08 (half the original)
     * was then MEASURED against both failure modes: the SAME bursty stimulus
     * now fires on 7-8 of 8 onsets across repeated runs (up from 3-8,
     * unreliably), while a sustained tone mixed with real white noise for 3
     * seconds still produces exactly 1 ring at 0.08 -- 0.06 already lets a
     * second false ring through and 0.04 lets five, so 0.08 is chosen with
     * real margin above the noise floor rather than at the edge of it.
     * SONAR_REFRACTORY_MS=180 is unchanged and was mutation-tested (D-13):
     * deleting its half of the gate on a 90ms onset ramp spanning several
     * frames above threshold produced 3 rings against the intact code's 1.
     * SONAR_LIFE_MS=620 and SONAR_GROW_PX=50 are chosen to read clearly
     * inside a 42-72px-tall composer without the ring outliving the syllable
     * that made it. */
    var SONAR_THRESH = 0.08;
    var SONAR_REFRACTORY_MS = 180;
    var SONAR_LIFE_MS = 620;
    var SONAR_GROW_PX = 50;
    var fxRings = [];
    var fxSonarCooldown = 0;
    function drawFxSonar(f, dt, tr, out, tintCol) {
      fxBeginFrame(f);
      fxSonarCooldown -= dt;
      if (tr > SONAR_THRESH && fxSonarCooldown <= 0) {
        fxRings.push({ age: 0 });
        fxSonarCooldown = SONAR_REFRACTORY_MS;
      }
      if (fxRings.length === 0) return;
      var R = fxFieldRect(f.w, f.h);
      ctx.strokeStyle = tintCol;
      /* Direction without going inside the field (AC-253, AC-254): speaking
         grows the ring away from the box; listening starts a ring already
         grown and lets it COLLAPSE back onto the box, so both read as
         motion relative to the edge and neither ever has a negative offset. */
      for (var k = fxRings.length - 1; k >= 0; k--) {
        var ring = fxRings[k];
        ring.age += dt;
        if (ring.age > SONAR_LIFE_MS) { fxRings.splice(k, 1); continue; }
        var frac = ring.age / SONAR_LIFE_MS;
        // MIN_GROW keeps even a just-born (or nearly-dead) ring's stroke
        // width from straddling the edge -- half the max lineWidth (2.2) is
        // about 1.1px, so 3px clears it with room to spare.
        var MIN_GROW = 3;
        var grow = MIN_GROW + (out ? frac * (SONAR_GROW_PX - MIN_GROW)
                                   : (1 - frac) * (SONAR_GROW_PX - MIN_GROW));
        roundRectPath(R.x - grow, R.y - grow, R.w + grow * 2, R.h + grow * 2,
                      R.r + grow);
        // Weight raised to match ember/ribbon's painted-pixel order rather
        // than the original thin, dim line: alpha up to 0.85 (was 0.6),
        // lineWidth up to 4.2 (was 2.2).
        ctx.globalAlpha = (1 - frac) * 0.85;
        ctx.lineWidth = 4.2 - frac * 2.6;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    /* ---- filaments --------------------------------------------------------- *
     * One tendril per frequency band -- a real spectrum wrapped around the
     * box rather than a decoration, reading the SAME per-band envelopes
     * (`atTilt`, NB=15 bands, the measured 2.6 tilt) the lattice already
     * consumes. Anchored at NB evenly-spaced perimeter points, one per band,
     * from a single `perimeter(R, NB)` call -- the same one-call-per-frame
     * pattern ember/ribbon use rather than a point lookup per band.
     * Speaking grows a tendril outward; listening keeps it short and close to
     * the edge, so the two are distinguishable by LENGTH as well as colour
     * (AC-253, AC-254). The curl is a sine off the frame clock so a loud band
     * reads as reaching rather than as a static spike.
     *
     * MEASURED WEAK, then reworked (`docs/voice-effects.md` step 5 review):
     * a pure test tone excites ONE band strongly and leaves the other 14 near
     * zero, and the original `band < 0.03` cutoff drew literal nothing for
     * those -- 15 anchor points with at most 1-2 visibly reaching read as
     * "effectively invisible" against a target the size of ember's own
     * painted area. There is now a FLOOR length every band draws regardless
     * of its own strength (a quiet band is a short stub, not nothing), and
     * the per-band scale is far larger (length, width and alpha all roughly
     * doubled to tripled) so even a narrowband stimulus -- or a real voice,
     * which excites more than one band but rarely all fifteen evenly --
     * reads as a cluster of visible reaching tendrils rather than a hint.
     *
     * CAPPED AGAINST THE SURROUNDING PAGE (RULING 4, CLAUDE.md 2026-09-16
     * backlog), not just against the canvas's own edge: a render showed bold
     * spikes crossing `#emptyChips` above the composer and `.microcopy`
     * (the "Butler is not listening" / "tap to arm" state line) below it --
     * both of which sit WELL INSIDE the canvas's own 40px/34px top/bottom
     * insets, which is exactly the room the un-capped `len` formula was
     * filling. MEASURED (patchright, 390px and 1280px viewports, both
     * agree, chips forced visible to check the worst case rather than
     * whatever one screenshot happened to show): the composer's own top
     * edge sits 6px below `.live` (ALWAYS rendered, even idle -- "awaiting
     * wake phrase" -- via its own 6px margin-bottom) and 10px below
     * `#emptyChips` when the conversation is empty, the SAME 10px whether
     * the four chips wrap into one row or several, because a margin does
     * not grow with the content above it. The composer's bottom edge sits
     * 8px above `.microcopy`, which like `.live` is always rendered. 6, the
     * smaller of the two numbers above the composer, is what actually
     * decides the top bound: a filament that never passes it never reaches
     * the chips either.
     *
     * `TOP_CLEAR`/`BOTTOM_CLEAR` shrink the usable rectangle a filament may
     * grow into, the same idea `rayExit` already applies to `dust` against
     * the canvas's own edge, but tighter and asymmetric because the hazard
     * here is content, not the canvas boundary. Applied only to the anchor's
     * OWN outward normal (`p.ny`): a flat top/bottom-edge anchor (`ny` = -1
     * or 1, `tx`/`ty` below both zero there) is capped exactly; a
     * side-edge anchor (`ny` = 0) is unrestricted, since nothing sits beside
     * the composer this close. Left approximate rather than exact at the
     * ROUNDED CORNERS, where `ny` is partial and the curl term's own `ty`
     * component (`tx = -ny`) can add a little extra vertical reach beyond
     * what the cap alone accounts for -- the same order of approximation
     * `dust`'s own offset cap already accepts, and far smaller a gap than
     * the one being closed. */
    function drawFxFilaments(f, tapRef, out, tintCol, tMs) {
      fxBeginFrame(f);
      if (!tapRef) return;
      var R = fxFieldRect(f.w, f.h);
      var pts = perimeter(R, NB);
      ctx.strokeStyle = tintCol;
      ctx.lineCap = 'round';
      var TOP_CLEAR = 6, BOTTOM_CLEAR = 8;
      var yTop = R.y - TOP_CLEAR, yBottom = R.y + R.h + BOTTOM_CLEAR;
      // TIP_CLEAR: a round line cap overshoots the mathematical end of a
      // stroke by about half its own width, the same reasoning `base`
      // already applies at the START of the stroke (half of the max
      // lineWidth, 6.6, is 3.3). MEASURED before this reservation existed:
      // with the cap ending exactly ON yTop/yBottom, the round cap's own
      // overshoot still painted 143 pixels inside `.microcopy`'s box even
      // though the mathematical tip never crossed it. Reserving it at the
      // FAR end too closed that gap (re-measured at 0 for both `.microcopy`
      // and `#emptyChips`, `manual/mutate_...` not needed -- this is the
      // same fix repeated, not a new mechanism).
      var TIP_CLEAR = 3.3;
      for (var i = 0; i < NB; i++) {
        var band = tapRef.atTilt(i / (NB - 1));
        var p = pts[i];
        // BASE holds the stroke's round start-cap clear of the edge -- half
        // the max lineWidth (6.6) is 3.3px, base 4 clears it.
        var base = 4;
        // FLOOR is what a silent band still draws -- a short stub rather
        // than nothing, so the whole ring of 15 anchors reads as present
        // even when only a few bands carry real energy.
        var len = out ? (22 + band * 90) : (18 + band * 42);
        // The content cap: how far THIS anchor's own ray can travel along
        // its outward normal before crossing yTop/yBottom. A purely
        // sideways ray (p.ny === 0) gets Infinity -- no content to cap
        // against there.
        var roomY = p.ny > 0 ? (yBottom - p.y) / p.ny
                  : p.ny < 0 ? (p.y - yTop) / -p.ny : Infinity;
        len = Math.min(len, Math.max(0, roomY - base - TIP_CLEAR));
        var tx = -p.ny, ty = p.nx;
        var curl = Math.sin(tMs / 460 + i * 1.7) * len * (out ? 0.34 : 0.16);
        var bx = p.x + p.nx * base, by = p.y + p.ny * base;
        var midx = p.x + p.nx * (base + len * 0.55) + tx * curl * 0.5;
        var midy = p.y + p.ny * (base + len * 0.55) + ty * curl * 0.5;
        var tipx = p.x + p.nx * (base + len) + tx * curl;
        var tipy = p.y + p.ny * (base + len) + ty * curl;
        ctx.globalAlpha = 0.48 + band * 0.5;
        ctx.lineWidth = 3.0 + band * 3.6;
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.quadraticCurveTo(midx, midy, tipx, tipy);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    /* ---- comet -------------------------------------------------------------- *
     * A bright point orbiting the perimeter with a fading tail. The cheapest
     * moving treatment in the catalogue: one point, a short tail of samples
     * off the SAME point array, and no per-particle physics. Speed and tail
     * length both come from `live`; direction flips with mode, so speaking
     * and listening orbit opposite ways and the two survive greyscale
     * (AC-254) without a second geometry.
     *
     * MEASURED WEAK, then reworked: `tailLen = 0.02 + L*0.16` put almost the
     * whole tail inside one arc-length step at low-to-moderate `L`, so at
     * the levels a real burst actually produces most of the run the tail
     * was too short to read as a tail at all -- "a single dot, no tail" is
     * the correct description of what that formula draws below about
     * L=0.3. The floor is now 0.10 of the loop rather than 0.02, tailN is
     * larger (36, was 22) so the same arc is covered by more, bigger dots,
     * and every radius and alpha below is roughly doubled.
     *
     * REWORKED AGAIN (RULING 2, CLAUDE.md 2026-09-16 backlog): the tail
     * above was still `tailN` SEPARATE circles, each fully opaque at its own
     * centre and fully transparent in the gap to its neighbour -- rendered
     * evidence confirmed a real head and a real trajectory, but the trail
     * itself read as a dotted line, most visibly where it curves round a
     * corner and the gaps between beads widen. The trail is now ONE
     * continuous stroked path: each of the `tailN` steps draws the SEGMENT
     * between two consecutive tail samples rather than a dot AT one sample,
     * with `lineCap: 'round'` so adjoining segments overlap at their ends
     * instead of leaving a seam. Width and alpha both taper toward the tail
     * exactly as the old dots' radius and alpha did, so the visual weight
     * at any one point along the trail is roughly unchanged -- what changed
     * is that the space between two samples is now painted too. */
    var fxCometU = 0;
    function drawFxComet(f, dt, L, out, tintCol) {
      fxBeginFrame(f);
      var R = fxFieldRect(f.w, f.h);
      var dir = out ? 1 : -1;
      var speed = (0.00004 + L * 0.00032) * dir;
      fxCometU = ((fxCometU + speed * dt) % 1 + 1) % 1;
      var PN = 220;
      var pts = perimeter(R, PN);
      var tailLen = 0.10 + L * 0.20, tailN = 36;
      // OFFSET clears the head's own radius (up to 7.0) plus a little room
      // for anti-aliasing, so no part of the trail straddles the edge the
      // way a plain point-on-the-perimeter circle would.
      var OFFSET = 9;
      ctx.strokeStyle = tintCol;
      ctx.lineCap = 'round';
      for (var i = tailN; i >= 1; i--) {
        var backA = (i / tailN) * tailLen, backB = ((i - 1) / tailN) * tailLen;
        var uA = ((fxCometU - dir * backA) % 1 + 1) % 1;
        var uB = ((fxCometU - dir * backB) % 1 + 1) % 1;
        var pA = pts[Math.round(uA * (PN - 1))];
        var pB = pts[Math.round(uB * (PN - 1))];
        // frac is the LEADING end of this segment's own taper -- the same
        // fraction the old dot at sample (i-1) used, so the trail's width
        // and alpha at any point match what the discrete version drew there.
        var frac = 1 - (i - 1) / tailN;
        ctx.globalAlpha = frac * frac * (0.28 + L * 0.6);
        ctx.lineWidth = 1.4 + frac * 6.6;
        ctx.beginPath();
        ctx.moveTo(pA.x + pA.nx * OFFSET, pA.y + pA.ny * OFFSET);
        ctx.lineTo(pB.x + pB.nx * OFFSET, pB.y + pB.ny * OFFSET);
        ctx.stroke();
      }
      /* No shadowBlur on the head: a blur radius large enough to read as a
         glow also spreads well past OFFSET's fixed clearance, which is what
         first put low-alpha pixels inside the field here. A second, larger
         plain fill reads as a bright point without that leak. */
      ctx.fillStyle = tintCol;
      var headIdx = Math.round(fxCometU * (PN - 1));
      var hp = pts[headIdx];
      ctx.globalAlpha = Math.min(1, 0.5 + L * 0.5);
      ctx.beginPath();
      ctx.arc(hp.x + hp.nx * OFFSET, hp.y + hp.ny * OFFSET, 3.6 + L * 3.4, 0, TAU);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    /* ---- dust --------------------------------------------------------------- *
     * Fine motes hovering near the edge -- a subtler `ember` with a FIXED
     * particle count (DUST_N=36), so unlike ember its per-frame cost never
     * grows with the level. Each mote has a fixed anchor on the perimeter and
     * an offset that eases toward a target set by level and direction:
     * speaking pushes the target out, listening pulls it toward the edge
     * (never past it -- the floor is clamped above zero, AC-253, AC-254).
     *
     * MEASURED WEAK, then reworked: 36 motes at radius under 2px each is a
     * few hundred painted pixels at best, an order of magnitude under
     * ember's own weight. DUST_N is now 96 (still FIXED -- the count never
     * grows with level, so the per-frame cost is still flat) and every
     * radius, alpha and offset range below is larger.
     *
     * MEASURED BROKEN in the same pass, on the SAME cause as `aurora`'s
     * clip: raising the speaking target to 54px pushed most motes' offset
     * past the composer's own SHORT-side insets (24px), off the canvas
     * entirely, and painted pixels dropped to 36 -- lower than before the
     * "fix". `rayExit` now caps every mote's offset at 80% of the room its
     * own anchor point actually has, so a mote near a short side stops
     * short rather than vanishing off it. */
    var DUST_N = 96;
    var fxDust = null;
    function ensureFxDust() {
      if (fxDust) return;
      fxDust = [];
      for (var i = 0; i < DUST_N; i++) {
        fxDust.push({ u: Math.random(), off: 6, ph: Math.random() * TAU,
                      r: 1.2 + Math.random() * 1.8 });
      }
    }
    function drawFxDust(f, dt, L, out, tintCol) {
      fxBeginFrame(f);
      ensureFxDust();
      var R = fxFieldRect(f.w, f.h);
      var pts = perimeter(R, 200);
      ctx.fillStyle = tintCol;
      // target's floor is 6, not 2: a mote's own radius (up to 3.0) plus the
      // +-1.2 jitter below must stay clear of the edge at the lowest offset
      // the easing can reach, or the round dot straddles it the way a plain
      // point-on-the-perimeter circle would.
      var target = out ? (8 + L * 46) : Math.max(6, 10 - L * 4);
      var tau = out ? 140 : 360;
      for (var i = 0; i < DUST_N; i++) {
        var d = fxDust[i];
        d.off += (target - d.off) * co(dt, tau);
        var jitter = Math.sin(t / 900 + d.ph) * 1.2;
        var idx = Math.round(d.u * 199);
        var p = pts[idx];
        // Capped at 80% of this point's own room: near a short side (24px
        // inset) that is well under `target`'s own 54px ceiling, and near
        // top/bottom (40px/34px) it rarely binds at all.
        var room = rayExit(p.x, p.y, p.nx, p.ny, f.w, f.h) * 0.8;
        var ox = Math.max(3.6, Math.min(d.off + jitter, room));
        ctx.globalAlpha = 0.4 + Math.min(1, d.off / 24) * 0.55;
        ctx.beginPath();
        ctx.arc(p.x + p.nx * ox, p.y + p.ny * ox, d.r, 0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    /* `aurora` was CUT here (RULING 2, CLAUDE.md 2026-09-16 backlog), not
       tuned: rendered evidence showed it as two or three faint vertical
       smudges above the composer at every level from 0.17 to 0.99 -- a
       rendering artefact rather than light. Its problem was its SHAPE
       (already reworked once for a clipping defect, see `rayExit` above),
       not its brightness, so a third tuning pass was declined in favour of
       removing it. It used to be the catch-all `else` in the dispatch chain
       in `frame()` below; see the comment there for what replaced it. */

    /* ---- work nobody can see is work not done ------------------------------ */
    /* `canvasOn` and `fxOn` are the two surfaces this file feeds. A STYLE may
       hide the canvas while a treatment still needs the level (Lantern keeps
       `lantern`'s canvas in Caption's hidden seam while its border glow reads
       `--fx-level` off `fxEl`), so under a treatment either one on screen
       keeps the loop running; otherwise only the canvas counts, as before. */
    var visible = true, canvasOn = true, fxOn = true;
    function onVis() {
      visible = !global.document.hidden;
      last = performance.now();
    }
    global.document.addEventListener('visibilitychange', onVis);
    var io = null;
    if (global.IntersectionObserver) {
      io = new global.IntersectionObserver(function (es) {
        for (var k = 0; k < es.length; k++) {
          if (es[k].target === mainCanvas) canvasOn = es[k].isIntersecting;
          else fxOn = es[k].isIntersecting;
        }
        last = performance.now();
      }, { rootMargin: '80px' });
      io.observe(mainCanvas);
      if (fxEl) io.observe(fxEl);
    }

    /* ---- the frame ---------------------------------------------------------- */
    function frame(now) {
      var onScreen = canvasOn
                   || (fxOn && !!fxEl && effect !== 'off' && effect !== 'lattice');
      if (!visible || !onScreen) {
        last = now;
        raf = global.requestAnimationFrame(frame);
        return;
      }
      var dt = Math.min(60, now - last);
      last = now;
      t += dt;

      var armed = state !== 'off';
      var listening = state === 'capturing'
                    || state === 'closing';
      var speaking = state === 'speaking';
      var thinking = state === 'thinking';
      var errored = state === 'error';

      /* WHICH tap is read comes from the caller's mode and never from the
         presence of a signal: the TTS worklet is connected for the whole
         session and emits silence between replies, so "there is something on
         the output tap" does not mean Butler is speaking. */
      var tap = mode === 'output' ? outTap : inTap;
      if (tap) tap.pump(dt);
      var raw = armed && tap ? tap.rms() : 0;
      var lv = youChain.push(mode === 'output' ? 0 : raw, dt);
      var lb = botChain.push(mode === 'output' ? raw : 0, dt);
      live = mode === 'output' ? lb : (armed ? lv : 0);
      var chain = mode === 'output' ? botChain : youChain;

      /* THE VOICE-EFFECT LAYER (STEP 2, docs/voice-effects.md, AC-257). The
         level is the SAME `live` the ring/band/lattice below already read --
         no second envelope follower, no second tap, and this file still
         opens no AudioContext of any kind. `--fx-dir` is 1 while speaking and
         0 otherwise, which is what `.fxmist`/`.fxbloom` in web/index.html key
         their colour (`var(--out)` / `var(--in)`) and their inward/outward
         motion off (AC-254) -- this file hands over two numbers and decides
         nothing about shape or colour. `lattice` and `off` are excluded:
         neither is a treatment, and both keep drawing the ring/band/lattice
         below exactly as they do today. */
      var treatment = effect !== 'off' && effect !== 'lattice';
      var fxShape = effect === 'lantern';
      if (treatment && fxEl) {
        /* THE RESTING FLOOR, and it is this file's own rule rather than a new
           one: "an armed microphone in a silent room must not look like an
           unarmed one -- that is the one difference on this page that may
           never be subtle" (the lattice's own comment, above). The nine
           treatments shipped without it and were WRONG for exactly that
           reason: `live` is 0 in a quiet room, ember spawns nothing below
           0.04, and the owner applied a treatment, saw an empty box, and
           reported it broken. They were right.
           So the level these draw is floored while ARMED and is a true zero
           when the microphone is off, which is the honest difference. The
           floor breathes for the reason the band's does -- a figure that
           moves three pixels in five seconds photographs as a dead hairline.
           `thinking` gets the floor and nothing more, because there is no
           sound to measure and a fabricated level would be a drawing of a
           measurement nobody took. */
        /* RULING 3 (CLAUDE.md 2026-09-16 backlog): reduced motion gets a
           HIGHER floor than ordinary motion does, and ONLY reduced motion --
           the animated treatments already carry the signal on the canvas
           and were left alone. MEASURED over a 260,208px clip before this
           change: the still halo's resting floor (armed, silent room)
           differed from `voice_effect: off` by a max channel delta of only
           9-12, against a stated visibility bar of 8 -- side by side, `mist`
           at floor and `off` looked identical, which is the exact failure
           this file's own rule (quoted a few lines up) exists to prevent.
           0.55 was chosen and then MEASURED, not guessed: see
           `web/AGENTS.md`'s voice-effects entry for this ruling for the
           re-run numbers. Reduced motion is a STILL floor, not a breathing
           one, because `breathNow()` is itself frozen under `calm()`
           (RULING 1, above) -- this reads the SAME frozen value rather than
           needing a second freeze here. */
        var fxRest = calm() ? 0.55 : 0.15 + breathNow() * 0.05;
        var fxLive = !armed ? 0
                   : thinking ? fxRest
                   : (live > fxRest ? live : fxRest);
        fxEl.style.setProperty('--fx-level', String(clamp(fxLive, 0, 1)));
        fxEl.style.setProperty('--fx-dir', mode === 'output' ? '1' : '0');

        /* THE TWO CANVAS TREATMENTS (STEP 3, docs/voice-effects.md).
           `mist` and `bloom` are CSS and never touch `mainCanvas`; `ember`
           and `ribbon` draw into it, and every other name -- known or not
           yet built -- clears it instead of leaving whatever it last held
           (AC-260). REDUCED MOTION (AC-259) takes the same branch as an
           unbuilt name: the canvas draws NOTHING and `.fxstill` carries the
           level on its own, through the `--fx-level` write above -- there is
           no partial-motion middle ground here the way the mock's flattened
           ribbon amplitude was one, because mist and bloom already set the
           precedent that reduced motion means the still halo alone. */
        var fxHasCanvas = effect === 'ember' || effect === 'ribbon'
                        || effect === 'sonar' || effect === 'filaments'
                        || effect === 'comet' || effect === 'dust';
        /* `lantern` is the STYLE's own voice (docs/lantern-ui.md CU-3): its
           light is CSS on the box, and its canvas -- moved by the page into
           Caption's seam -- draws the SHAPE figure below (the band, for a
           wide seam). Where a style hides that slot the canvas measures
           0 x 0 and nothing is drawn. So it is not cleared here: the figure
           below clears its own frame. */
        /* `armed` GATES THE CANVAS, not just the level, and that is a defect
           found by measuring rather than by reading: `dust` keeps a FIXED
           mote count and reads the level only for how far it pushes them, so
           with the microphone off it went on drawing 1,776 pixels of motes at
           level 0 -- the exact "an unarmed microphone looks armed" failure
           this floor exists to prevent, inverted. Gating here rather than in
           each treatment means a tenth one cannot reintroduce it. */
        if (armed && !calm() && fxHasCanvas) {
          var fxFrame = fit(mainCanvas);
          var fxOut = mode === 'output';
          var fxTint = fxColour(fxOut);
          if (effect === 'ember') drawFxEmber(fxFrame, fxLive, fxOut, fxTint);
          else if (effect === 'ribbon') {
            drawFxRibbon(fxFrame, fxLive, fxOut, fxTint, t);
          } else if (effect === 'sonar') {
            drawFxSonar(fxFrame, dt, chain.transient(), fxOut, fxTint);
          } else if (effect === 'filaments') {
            drawFxFilaments(fxFrame, tap, fxOut, fxTint, t);
          } else if (effect === 'comet') {
            drawFxComet(fxFrame, dt, fxLive, fxOut, fxTint);
          } else if (effect === 'dust') {
            drawFxDust(fxFrame, dt, fxLive, fxOut, fxTint);
          } else {
            /* RULING 2 (CLAUDE.md 2026-09-16 backlog): `aurora` used to be
               this catch-all, so a name nothing here recognises drew
               aurora's curtains instead of drawing nothing --
               `fxHasCanvas` just above is a SEPARATE list naming the same
               set, and the two could drift apart (D-18: two lists that name
               one thing can disagree). This branch is now reachable only if
               they already have, and it fails the same way AC-260 requires
               for any treatment this file has not built anything for: clear
               and draw nothing, explicitly, rather than fall through to
               whatever the last `else` happened to be. */
            fxBeginFrame(fxFrame);
          }
        } else {
          if (!fxShape) fxBeginFrame(fit(mainCanvas));
          fxEmbers.length = 0;
          fxRings.length = 0;
          fxSonarCooldown = 0;
        }
      }

      // the onset. 150 ms, about how long a person reads a flash as belonging
      // to the sound that caused it.
      flash = Math.max(flash * Math.exp(-dt / 150),
                       Math.min(1, chain.transient() * 1.4));

      /* The travelling sweep, shared by the band and the field so `thinking`
         is one gesture across the page rather than two unrelated ones. A
         TRIANGLE wave: a sawtooth spends a quarter of its cycle off the band,
         and a sine decelerates at exactly the two ends where the edge envelope
         has already made the bump invisible. */
      /* RULING 1 (CLAUDE.md 2026-09-16 backlog): reduced motion used to SLOW
         this (3400ms instead of 2200ms) rather than stop it -- a slower
         sweep is still a sweep, and the `thinking` highlight on the band and
         ring plus the lattice's own `nearS` pull (which reads `sweepP` a few
         lines up) both kept travelling under calm(). Frozen instead:
         `sweepP` simply keeps whatever value it last held. */
      if (!calm()) {
        var triT = (t / 2200) % 2;
        sweepP = 0.14 + Math.abs(triT - 1) * 0.72;
      }

      var breath = breathNow();
      var amp = (listening || speaking) ? 0.72 + live * 0.28 : 0.5;
      /* `open` is the half-height the band keeps where the waveform is at
         zero, in units of span. The idle figure BREATHES over a wide range on
         purpose: a lens that moves three pixels in five seconds photographs as
         a dead hairline. `live` is undivided because with a real microphone
         the room's residual level is the honest part of this number and the
         breath is only the rhythm. */
      var open = !armed ? 0.006
               : errored ? 0.012
               : thinking ? 0.03
               : (listening || speaking) ? 0.018 + live * 0.05
               : 0.035 + breath * 0.085 + live * 0.9;
      var lit = !armed ? 0.35 : errored ? 0.75
              : (listening || speaking) ? 1 : thinking ? 0.85 : 0.9;
      if (bold()) lit = Math.min(1.4, lit + 0.25);

      if (thinking) buildFrontSweep(0.46 + breath * 0.06);
      else buildFrontWave(tap, amp);
      capture(dt);

      /* The link stops and the node colour are chosen HERE, side by side, so
         the two halves of the palette can never disagree about which state is
         being drawn. `thinking` takes the warm set at a lower `lit`, which is
         what a dim warm pivot between the room's half of the turn and Butler's
         actually is. */
      var light = isLight();
      var stops = errored ? C.meshErr
                : speaking || thinking ? C.meshOut
                : listening ? C.meshIn : C.meshIdle;
      var nodeCol = errored ? C.nodeErr
                  : speaking || thinking ? C.nodeOut
                  : listening ? C.nodeIn : C.nodeIdle;
      /* Band or ring, decided by the SHAPE of the drawing surface. `#wave` is
         moved between the composer's microphone button (AC-167) and Caption's
         seam (AC-691) and this file is never told which; a near-square canvas
         is a ring and a wide one is a band, which is a fact about the surface
         rather than about the page's markup. Below six pixels there is
         nothing to draw: a hidden canvas measures 0 x 0. */
      /* THE OLD DRAWING STOPS FOR A TREATMENT (AC-260), except `lantern`'s
         shape figure. A treatment's #wave sits inside the composer's slot and
         #field is not what a treatment draws either, so both are skipped
         rather than painted with nothing to show for it. `stops`, `nodeCol`, `open` and `lit` above are still
         computed either way, so a switch back to `lattice` resumes with the
         right state instead of one frame's worth of stale colour. */
      if (!treatment || fxShape) {
        var wf = fit(mainCanvas);
        if (wf.w >= 6 && wf.h >= 6) {
          if (wf.w < wf.h * 2) {
            drawRing(dt, wf, stops, nodeCol, open, lit, light, thinking);
          } else {
            drawWave(wf, stops, open, lit, light, thinking);
          }
        }
      }
      if (!treatment) {
        buildDens(tap);
        if (fctx) drawLattice(dt, fit(fieldCanvas), stops, nodeCol);
      }

      /* The score decays where the detector stopped reporting, and the trace
         plots exactly what arrived. Sampled every 40 ms, which is half the
         detector's own reporting period -- fast enough that a spike cannot
         fall between two samples. */
      sinceScore += dt;
      if (sinceScore > SCORE_HOLD_MS) score *= Math.exp(-dt / SCORE_TAU);
      traceAcc += dt;
      if (traceAcc > 40) {
        traceAcc = 0;
        trace.push(score);
        while (trace.length > TRACE_N) trace.shift();
      }
      if (tctx) drawTrace(fit(traceCanvas));

      raf = global.requestAnimationFrame(frame);
    }

    /* Which theme is running is not passed in, and reading it off the document
       would be this file guessing at the page. What it actually renders with
       is `colors`, so the honest question is what the caller assigned: the
       nodes are near-WHITE on the dark theme and near-BLACK on the light one,
       because brightness on a pale ground is ink density. So the answer is in
       the palette itself. Recomputed only when that string changes, since it
       costs a regex. */
    var lightKey = '', lightVal = false;
    function isLight() {
      if (C.nodeIdle !== lightKey) {
        lightKey = C.nodeIdle;
        var m = /(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(lightKey);
        lightVal = !!m && (+m[1] * 0.299 + +m[2] * 0.587 + +m[3] * 0.114) < 128;
      }
      return lightVal;
    }

    raf = global.requestAnimationFrame(frame);

    return {
      attachInput: attachInput,
      attachOutput: attachOutput,
      setMode: function (m) { mode = m; },
      setState: function (s) { if (s) state = s; },
      /* Same shape as setMode/setState above -- stores the name and nothing
         else (STEP 2, docs/voice-effects.md, AC-257). `frame()` is the only
         reader. */
      setEffect: function (e) { effect = e; },
      setThreshold: function (t) { THRESHOLD = clamp(t, 0, 1); },
      pushScore: function (v) {
        score = clamp(v, 0, 1);
        sinceScore = 0;
      },
      colors: C,
      /* Live counters from the pair loop. AC-75's claim -- that the field
         draws an order of magnitude fewer links at silence than at a loud
         syllable -- is one nobody should have to take on trust, and this is
         what a browser can be asked. */
      lattice: lattice,
      /* Called from app.js's stop(), BEFORE it closes both AudioContexts. It
         releases the taps and nothing else: the page is still there, still
         shows `off`, and start() can attach again. Killing the render loop
         here would freeze the canvas mid-syllable for as long as the owner
         leaves the page open. Idempotent by construction -- stop() is
         reachable from more than one path and a released tap is null. */
      detach: function () {
        if (inTap) { inTap.release(); inTap = null; }
        if (outTap) { outTap.release(); outTap = null; }
      },
      /* Nothing calls this today. It exists because the listeners above
         outlive the canvas otherwise, and a page that grows a second
         visualiser would leak the first one's loop. */
      destroy: function () {
        global.cancelAnimationFrame(raf);
        global.document.removeEventListener('visibilitychange', onVis);
        if (io) io.disconnect();
        this.detach();
      }
    };
  }

  global.ButlerVisualiser = { create: create };
})(window);
