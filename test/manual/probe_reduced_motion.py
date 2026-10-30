"""Audit `prefers-reduced-motion` across every `voice_effect` treatment.

WRITES THE DAEMON'S `voice_effect` SETTING, once per value in the schema's
own choices list, and restores whatever it found there when the run
started -- in a `finally`, so a crash partway through still puts it back.
Point it at a SCRATCH daemon; do not run it against the owner's Butler.

Usage: python -X utf8 test/manual/probe_reduced_motion.py [url]
       (defaults to http://127.0.0.1:8199)

Step 6 of docs/voice-effects.md. The per-treatment reduced-motion fallback
(AC-259) was built and screenshotted one at a time as each treatment
shipped -- `web/AGENTS.md`'s voice-effects entries record each one -- but
never audited together, across all values of `voice_effect`, in one
browser session with `prefers-reduced-motion: reduce` actually forced. This
is that audit. (Ten values as of RULING 2, CLAUDE.md 2026-09-16 backlog --
`aurora` was cut; a daemon that has not restarted since still reports
eleven, which this file's own default audit notes rather than treats as a
defect.)

WHY A FAKE MICROPHONE IS REQUIRED, NOT OPTIONAL: `visualiser.js`'s `frame()`
gates the whole voice-effect canvas branch on `armed` (`state !== 'off'`),
and `armed` comes from the page's own capture state, not from a setting --
`fxLive` is a true zero the moment the microphone is off (that gating is
itself a fix: `dust` used to draw 1,776 pixels of motes at level 0 with the
mic off). So this probe grants a synthesised microphone through Chrome's
`--use-fake-device-for-media-stream` and waits for the page to arm itself
(`stt_enabled` defaults on, so a page with mic permission already granted
arms at boot with no click needed -- `web/AGENTS.md` "THE PAGE ARMS ITSELF
AT BOOT"), and it FAILS LOUDLY rather than silently auditing a dead canvas
if that does not happen within a generous window.

THE 140MS TRANSITION TRAP (web/AGENTS.md, under "voice-effects.md" step 2):
the page carries a blanket `* { transition-duration: 140ms !important }`
inside the SAME reduced-motion media query, and CSS's default
`transition-property` is `all` -- so a `getComputedStyle` read taken
immediately after a level (or treatment) change returns a value still
interpolating from the PREVIOUS one. Every computed-style read here waits
SETTLE_MS (250ms, with margin over the 140ms) after the page confirms it has
adopted the new value.

TWO TENSIONS THIS FILE MEASURES AND DOES NOT JUDGE: whether `lattice`
(which is not a "treatment" in `voice_effect`'s own sense -- it is the
classic ring/band/lattice wave, still gated by `calm()` reducing rather
than stopping) counts as a reduced-motion PASS under AC-259, and whether the
still halo's breathing floor (`fxRest = 0.15 + breathNow() * 0.05`) counts
as motion. Both are printed as NOTE/TENSION lines, never as a BAD verdict,
because the owner's ruling decides them and this script cannot.
TASK A (--normal) renders all nine canvas/CSS treatments at NORMAL motion
(prefers-reduced-motion NOT forced), armed, polled for a real nonzero
--fx-level, three frames ~700ms apart so an accumulating treatment (ember,
comet, sonar) shows its trail. Written to
test/manual/appearance/fx-<name>-{1,2,3}.png.

TASK B (--visibility) measures whether AC-259's reduced-motion fallback
(the `.fxstill` halo, opacity = --fx-level * 0.42) is actually visible, by
diffing real screenshots at a LOW level against a HIGH level. It does NOT
force --fx-level via `el.style.setProperty()` and trust it, because
visualiser.js's frame() rewrites that property every requestAnimationFrame
tick from the real audio signal (visualiser.js:1922) and that loop runs in
the page's MAIN JS world while patchright's page.evaluate() runs in an
ISOLATED world (this file's own "world isolation" note above) -- so a
forced value cannot survive past the next paint, which is EMPIRICALLY
checked once per run and printed as a NOTE rather than assumed. Instead it
drives real audio through Chrome's `--use-file-for-fake-audio-capture`: a
silent WAV for LOW, which lands on the engine's own documented resting
floor (`fxRest = 0.15 + breath*0.05`, visualiser.js:1918 -- the exact
"armed mic in a silent room" case) and a loud tone for HIGH. The diff
itself runs in a plain `about:blank` canvas (two Image elements,
getImageData, no app code), because the two screenshots come from two
separate browser processes and cannot be compared inside either page.
"""
import base64
import json
import shutil
import sys
import tempfile
import urllib.error
import ssl
import urllib.request
from pathlib import Path

from patchright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "test" / "manual" / "appearance"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

URL = next((a for a in sys.argv[1:] if not a.startswith("--")),
           "http://127.0.0.1:8199")

# See the module docstring's "140MS TRANSITION TRAP" paragraph.
SETTLE_MS = 250

# Shared by Task A and Task B: the composer's own rect, expanded by a
# generous margin on all four sides and clamped to the viewport. 180px is
# well past the canvas's own fixed insets (40/34/24/24, web/AGENTS.md), so
# nothing a treatment draws can be clipped.
_CLIP_JS = (
    "(() => { const r = document.getElementById"
    "('composer').getBoundingClientRect(); const m = 180; "
    "const x = Math.max(0, r.left - m); "
    "const y = Math.max(0, r.top - m); "
    "const x2 = Math.min(window.innerWidth, r.right + m); "
    "const y2 = Math.min(window.innerHeight, r.bottom + m); "
    "return {x: x, y: y, width: x2 - x, height: y2 - y}; })()")

_FX_LEVEL_JS = (
    "getComputedStyle(document.getElementById('composerFx'))"
    ".getPropertyValue('--fx-level').trim()")


# A scratch daemon serves TLS with a self-signed certificate (test/AGENTS.md:
# browser instruments need https), so the API calls accept it as the browser
# context below does (`ignore_https_errors`).
_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE


def _api(url: str, path: str, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url.rstrip("/") + path, data=body,
                                  method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10, context=_TLS) as resp:
        return json.loads(resp.read().decode())


def _get_settings_patiently(url: str):
    """Poll every 4s for up to 120s before giving up.

    A too-tight poll once reported a healthy-but-slow-to-boot Butler as
    dead (CLAUDE.md's backlog item 4, 2026-09-16) -- the daemon under this
    probe is scratch and should already be warm, but the cost of being
    patient here is small and the cost of a false "daemon is down" is a
    wasted run.
    """
    import time
    deadline = time.monotonic() + 120
    last_exc = None
    while time.monotonic() < deadline:
        try:
            return _api(url, "/api/settings")
        except (urllib.error.URLError, ConnectionError) as exc:
            last_exc = exc
            print(f"  ... /api/settings not answering yet ({exc}), "
                  f"retrying in 4s")
            time.sleep(4)
    raise RuntimeError(f"/api/settings never answered within 120s: "
                        f"{last_exc}")


def get_voice_effect(url: str):
    data = _get_settings_patiently(url)
    for item in data["settings"]:
        if item["key"] == "voice_effect":
            return item["value"], list(item["choices"])
    raise KeyError("voice_effect is not in /api/settings -- the schema "
                   "moved and this probe needs updating")


def set_voice_effect(url: str, value: str) -> None:
    _api(url, "/api/settings", data={"voice_effect": value}, method="POST")


def _write_wav(path: Path, amplitude: float, seconds: float = 3.0,
                rate: int = 48000) -> None:
    """Mono 16-bit PCM WAV: pure digital silence at amplitude 0, or a
    steady 440Hz tone at the given amplitude (0..1). Chromium's
    --use-file-for-fake-audio-capture loops this file for the fake mic, so
    a short clip is enough."""
    import math
    import struct
    import wave

    n = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        samples = bytearray()
        for i in range(n):
            if amplitude <= 0:
                s = 0
            else:
                s = int(amplitude * 32767 *
                        math.sin(2 * math.pi * 440 * i / rate))
            samples += struct.pack("<h", s)
        w.writeframes(bytes(samples))


def _wait_armed(page) -> bool:
    for _ in range(60):
        st = page.evaluate("document.documentElement.dataset.state")
        if st and st != "off":
            return True
        page.wait_for_timeout(500)
    return False


def _wait_applied(page, value: str) -> bool:
    for _ in range(20):
        if page.evaluate(
                "document.documentElement.dataset.voicefx") == value:
            return True
        page.wait_for_timeout(250)
    return False


def run_normal_motion(url: str) -> int:
    """TASK A. Render all nine canvas/CSS treatments at NORMAL motion
    (prefers-reduced-motion NOT forced), armed via the fake microphone, for
    the owner to actually look at. The default audit below forces reduced
    motion, under which every treatment deliberately draws nothing
    (AC-259) -- so those screenshots say nothing about how a treatment
    looks in ordinary use. This does. See the module docstring's TASK A
    paragraph for the mechanism.
    """
    bad: list[str] = []
    written: list[str] = []
    print(f"\n=== TASK A: normal-motion renders -> {url} ===")

    original_value, all_values = get_voice_effect(url)
    treatments = [v for v in all_values if v not in ("off", "lattice", "lantern")]
    print(f"treatments (schema, minus off/lattice/lantern): {treatments}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=CHROME,
                args=["--use-fake-ui-for-media-stream",
                      "--use-fake-device-for-media-stream"])
            try:
                ctx = browser.new_context(
                    ignore_https_errors=True,
                    permissions=["microphone"],
                    reduced_motion="no-preference",
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.goto(url, wait_until="load")
                page.wait_for_timeout(2500)

                reduced = page.evaluate(
                    "matchMedia('(prefers-reduced-motion: reduce)')"
                    ".matches")
                print(f"  prefers-reduced-motion: reduce = {reduced} "
                      f"(expected False for a normal-motion render)")

                if not _wait_armed(page):
                    print("ABORTING TASK A: the page never armed itself "
                          "(data-state stayed 'off'). Nothing draws with "
                          "the microphone off, so every frame below would "
                          "be blank -- TASK A IS INVALID.")
                    return 1

                for value in treatments:
                    set_voice_effect(url, value)
                    if not _wait_applied(page, value):
                        bad.append(f"[{value}] never applied on the page "
                                   f"(data-voicefx)")
                        print(f"  BAD [{value}] never applied on the page")
                        continue

                    # Let it run at least 3s before the first capture, so
                    # an accumulating treatment (ember spawns, comet
                    # trails, sonar rings) has something to show.
                    page.wait_for_timeout(3000)

                    level = 0.0
                    reached = False
                    for _ in range(20):
                        raw = page.evaluate(_FX_LEVEL_JS)
                        try:
                            level = float(raw)
                        except ValueError:
                            level = 0.0
                        if level >= 0.3:
                            reached = True
                            break
                        page.wait_for_timeout(400)
                    if not reached:
                        print(f"  NOTE [{value}]: --fx-level never reached "
                              f"0.3 (last read {level:.3f}) within the "
                              f"poll window -- capturing anyway, and this "
                              f"is said plainly rather than shipped as an "
                              f"ordinary frame.")

                    for i in range(1, 4):
                        clip = page.evaluate(_CLIP_JS)
                        shot = OUT_DIR / f"fx-{value}-{i}.png"
                        page.screenshot(path=str(shot), clip=clip)
                        raw = page.evaluate(_FX_LEVEL_JS)
                        try:
                            lvl = float(raw)
                        except ValueError:
                            lvl = 0.0
                        print(f"  wrote {shot.name} "
                              f"(--fx-level={lvl:.3f})")
                        written.append(shot.name)
                        if i < 3:
                            page.wait_for_timeout(700)
            finally:
                browser.close()
    finally:
        try:
            set_voice_effect(url, original_value)
            back, _ = get_voice_effect(url)
            restored = back == original_value
            print(f"\nrestore (Task A): voice_effect -> "
                  f"{original_value!r} "
                  f"({'confirmed' if restored else 'NOT CONFIRMED, daemon reports ' + repr(back)})")
            if not restored:
                bad.append(f"Task A restore not confirmed (daemon reports "
                           f"{back!r})")
        except Exception as exc:  # noqa: BLE001 -- report, do not swallow
            print(f"restore FAILED (Task A): {exc!r}")
            bad.append(f"Task A restore raised: {exc!r}")

    print(f"\nTask A wrote {len(written)} file(s): {written}")
    if bad:
        print(f"Task A: {len(bad)} problem(s):")
        for b in bad:
            print(f"  - {b}")
    return 1 if bad else 0


def run_visibility(url: str) -> int:
    """TASK B. Is AC-259's reduced-motion fallback (`.fxstill`, opacity =
    --fx-level * 0.42) actually visible? Measured with real pixel diffs
    between a LOW level and a HIGH level, both driven by real audio
    through Chrome's fake-audio-capture device rather than a forced CSS
    property -- see the module docstring's TASK B paragraph for why the
    forced-property approach is not trustworthy here, and the empirical
    check this function prints before falling back to real audio.
    """
    bad: list[str] = []
    print(f"\n=== TASK B: reduced-motion visibility -> {url} ===")

    original_value, all_values = get_voice_effect(url)
    treatments = [v for v in all_values if v not in ("off", "lattice", "lantern")]

    tmp_dir = Path(tempfile.mkdtemp(prefix="butler_fx_wav_"))
    silent_wav = tmp_dir / "silent.wav"
    _write_wav(silent_wav, amplitude=0.0)
    # NO loud WAV. MEASURED first (standalone getUserMedia + AnalyserNode
    # probe, not kept in this file): a steady 440Hz tone fed through
    # --use-file-for-fake-audio-capture peaked at 0.221 and fell to 0.112
    # over 2.3s of continuous playback -- app.js's own getUserMedia call
    # asks for `echoCancellation: true, noiseSuppression: true`
    # (app.js:3792), and Chromium's WebRTC audio processing module reads a
    # steady pure tone as echo/noise and actively cancels it toward
    # silence. A synthetic "loud" file would therefore measure the APM,
    # not the fallback. HIGH instead uses Chromium's own DEFAULT fake
    # device (no file flag) -- the same one Task A uses, whose bursty,
    # non-tonal content is NOT cancelled the same way and is what drove
    # --fx-level as high as 0.994 there -- polled until the level crosses
    # a real threshold rather than captured on a fixed clock.

    def _capture(wav_path, label: str, poll_high: bool = False) -> dict:
        out: dict = {}
        args = ["--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream"]
        if wav_path is not None:
            args.append(f"--use-file-for-fake-audio-capture={wav_path}")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, executable_path=CHROME, args=args)
            try:
                ctx = browser.new_context(
                    ignore_https_errors=True,
                    permissions=["microphone"],
                    reduced_motion="reduce",
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.goto(url, wait_until="load")
                page.wait_for_timeout(2500)

                reduced = page.evaluate(
                    "matchMedia('(prefers-reduced-motion: reduce)')"
                    ".matches")
                if not reduced:
                    bad.append(f"[{label}] reduced motion did not apply")
                    print(f"  BAD [{label}] reduced motion did not apply")
                    return out

                if not _wait_armed(page):
                    bad.append(f"[{label}] page never armed")
                    print(f"  BAD [{label}] page never armed -- aborting "
                          f"this condition")
                    return out

                if label == "low":
                    # EMPIRICAL CHECK, once: does a forced --fx-level
                    # survive past the next paint? Printed as a NOTE and
                    # never trusted as this condition's own measurement --
                    # see the module docstring's TASK B paragraph.
                    set_voice_effect(url, treatments[0])
                    _wait_applied(page, treatments[0])
                    page.wait_for_timeout(SETTLE_MS)
                    trace = page.evaluate(
                        """
                        (() => {
                            const el =
                                document.getElementById('composerFx');
                            el.style.setProperty('--fx-level', '0.05');
                            return new Promise(resolve => {
                                const out = [];
                                let n = 0;
                                function tick() {
                                    out.push(getComputedStyle(el)
                                        .getPropertyValue('--fx-level')
                                        .trim());
                                    n++;
                                    if (n < 5) requestAnimationFrame(tick);
                                    else resolve(out);
                                }
                                requestAnimationFrame(tick);
                            });
                        })()
                        """)
                    print(f"  NOTE: forced --fx-level=0.05 on a live "
                          f"treatment, then read back across 5 rAF "
                          f"ticks: {trace}. If this drifts away from "
                          f"'0.05' the forced-property approach is "
                          f"unreliable here, and the low/high values "
                          f"below come from real audio instead, never "
                          f"from this force.")

                for value in treatments:
                    set_voice_effect(url, value)
                    if not _wait_applied(page, value):
                        bad.append(f"[{label}/{value}] never applied")
                        print(f"  BAD [{label}/{value}] never applied")
                        continue

                    level = 0.0
                    if poll_high:
                        # The default fake device's content is bursty
                        # (Task A measured 0.15 up to 0.994 across a
                        # session), so poll for a real high reading rather
                        # than trusting a fixed wait.
                        reached = False
                        for _ in range(25):
                            raw = page.evaluate(_FX_LEVEL_JS)
                            try:
                                level = float(raw)
                            except ValueError:
                                level = 0.0
                            if level >= 0.6:
                                reached = True
                                break
                            page.wait_for_timeout(400)
                        if not reached:
                            print(f"  NOTE [{label}/{value}]: --fx-level "
                                  f"never reached 0.6 (last read "
                                  f"{level:.3f}) within the poll window "
                                  f"-- capturing the highest-seen moment "
                                  f"anyway, said plainly rather than "
                                  f"reported as a clean 'high' sample.")
                    else:
                        # Let the envelope follower settle onto the
                        # (silent) input before reading or capturing.
                        page.wait_for_timeout(2000)
                        raw = page.evaluate(_FX_LEVEL_JS)
                        try:
                            level = float(raw)
                        except ValueError:
                            level = 0.0

                    clip = page.evaluate(_CLIP_JS)
                    png_bytes = page.screenshot(clip=clip)
                    out[value] = {"level": level, "png": png_bytes,
                                   "w": clip["width"], "h": clip["height"]}
                    print(f"  [{label}/{value}] --fx-level={level:.3f}")

                if label == "low":
                    # BONUS baseline, not asked for by name but the more
                    # direct test of the owner's actual complaint ("I
                    # could see no halo at all" -- which was the FLOOR
                    # state, not a low-vs-high comparison): "off" leaves
                    # .fxstill at display:none entirely, so this is a
                    # genuine zero to compare the floor against, captured
                    # in the same session while the silent WAV is still
                    # attached.
                    set_voice_effect(url, "off")
                    _wait_applied(page, "off")
                    page.wait_for_timeout(SETTLE_MS)
                    clip = page.evaluate(_CLIP_JS)
                    png_bytes = page.screenshot(clip=clip)
                    out["__off__"] = {"level": 0.0, "png": png_bytes,
                                       "w": clip["width"],
                                       "h": clip["height"]}
                    print(f"  [{label}/off] baseline captured "
                          f"(.fxstill display:none)")
            finally:
                browser.close()
        return out

    try:
        low = _capture(silent_wav, "low")
        high = _capture(None, "high", poll_high=True)
    finally:
        try:
            set_voice_effect(url, original_value)
            back, _ = get_voice_effect(url)
            restored = back == original_value
            print(f"\nrestore (Task B): voice_effect -> "
                  f"{original_value!r} "
                  f"({'confirmed' if restored else 'NOT CONFIRMED, daemon reports ' + repr(back)})")
            if not restored:
                bad.append(f"Task B restore not confirmed (daemon "
                           f"reports {back!r})")
        except Exception as exc:  # noqa: BLE001 -- report, do not swallow
            print(f"restore FAILED (Task B): {exc!r}")
            bad.append(f"Task B restore raised: {exc!r}")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _DIFF_JS = """
        async ([aB64, bB64]) => {
            function loadImg(b64) {
                return new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => resolve(img);
                    img.onerror = reject;
                    img.src = 'data:image/png;base64,' + b64;
                });
            }
            const [imgA, imgB] = await Promise.all(
                [loadImg(aB64), loadImg(bB64)]);
            const w = imgA.width, h = imgA.height;
            const c = document.createElement('canvas');
            c.width = w; c.height = h;
            const ctx = c.getContext('2d');
            ctx.drawImage(imgA, 0, 0);
            const pa = ctx.getImageData(0, 0, w, h).data;
            ctx.clearRect(0, 0, w, h);
            ctx.drawImage(imgB, 0, 0);
            const pb = ctx.getImageData(0, 0, w, h).data;
            let diffPixels = 0, maxDelta = 0;
            for (let i = 0; i < pa.length; i += 4) {
                const dr = Math.abs(pa[i] - pb[i]);
                const dg = Math.abs(pa[i+1] - pb[i+1]);
                const db = Math.abs(pa[i+2] - pb[i+2]);
                const da = Math.abs(pa[i+3] - pb[i+3]);
                const m = Math.max(dr, dg, db, da);
                if (m > 0) diffPixels++;
                if (m > maxDelta) maxDelta = m;
            }
            return {diffPixels: diffPixels, maxDelta: maxDelta,
                    w: w, h: h, total: w * h};
        }
        """

    def _diff(page, png_a: bytes, png_b: bytes):
        a64 = base64.b64encode(png_a).decode()
        b64 = base64.b64encode(png_b).decode()
        return page.evaluate(_DIFF_JS, [a64, b64])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    off_row = low.get("__off__")
    rows_lh: list[tuple] = []
    rows_lo: list[tuple] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            for value in treatments:
                if value not in low or value not in high:
                    rows_lh.append((value, None))
                    rows_lo.append((value, None))
                    continue
                a, b = low[value], high[value]
                if (a["w"], a["h"]) != (b["w"], b["h"]):
                    bad.append(f"[{value}] low/high screenshots differ in "
                               f"size ({a['w']}x{a['h']} vs "
                               f"{b['w']}x{b['h']}), diff is meaningless")
                    rows_lh.append((value, "SIZE MISMATCH"))
                else:
                    result = _diff(page, a["png"], b["png"])
                    rows_lh.append((value, result, a["level"], b["level"]))
                    (OUT_DIR / f"fx-vis-{value}-low.png").write_bytes(
                        a["png"])
                    (OUT_DIR / f"fx-vis-{value}-high.png").write_bytes(
                        b["png"])

                if off_row is None:
                    rows_lo.append((value, None))
                elif (a["w"], a["h"]) != (off_row["w"], off_row["h"]):
                    rows_lo.append((value, "SIZE MISMATCH"))
                else:
                    off_result = _diff(page, a["png"], off_row["png"])
                    rows_lo.append((value, off_result, a["level"]))
            if off_row is not None:
                (OUT_DIR / "fx-vis-off.png").write_bytes(off_row["png"])
        finally:
            browser.close()

    print("\n=== TASK B: pixel diffs (low vs high, reduced motion) ===")
    print(f"{'treatment':<12} {'lvl-low':<9} {'lvl-high':<9} "
          f"{'diff-px':<10} {'total-px':<10} {'max-delta':<10} "
          f"verdict")
    for row in rows_lh:
        if len(row) == 2 and row[1] is None:
            print(f"{row[0]:<12} MISSING (see BAD lines above)")
        elif len(row) == 2:
            print(f"{row[0]:<12} {row[1]}, skipped")
        else:
            value, result, lvl_low, lvl_high = row
            verdict_txt = ("VISIBLE" if result["maxDelta"] >= 8
                           else "NOT VISIBLE (max delta < 8)")
            print(f"{value:<12} {lvl_low:<9.3f} {lvl_high:<9.3f} "
                  f"{result['diffPixels']:<10} {result['total']:<10} "
                  f"{result['maxDelta']:<10} {verdict_txt}")

    print("\n=== TASK B bonus: pixel diffs (low/FLOOR vs voice_effect="
          "off/ZERO) -- the owner's own complaint was the floor state "
          "alone, not a low-vs-high comparison ===")
    print(f"{'treatment':<12} {'lvl-low':<9} {'diff-px':<10} "
          f"{'total-px':<10} {'max-delta':<10} verdict")
    # RULING 3 (CLAUDE.md 2026-09-16 backlog): this used to be purely
    # informational (a printed verdict, never an assertion), which is how
    # the original 9-12 delta shipped unnoticed -- "mist at floor and off
    # look identical" was true and nothing here said so loudly. FLOOR_BAR is
    # comfortably above the stated visibility bar (8) rather than sitting on
    # it, so a regression partway back toward the old 9-12 range still fails
    # rather than scraping by. MEASURED after raising `fxRest` under
    # `calm()`: 30 across all nine treatments (the raised floor is shared
    # code, so it moves identically for all of them).
    FLOOR_BAR = 20
    for row in rows_lo:
        if len(row) == 2 and row[1] is None:
            print(f"{row[0]:<12} MISSING or no 'off' baseline captured")
        elif len(row) == 2:
            print(f"{row[0]:<12} {row[1]}, skipped")
        else:
            value, result, lvl_low = row
            verdict_txt = ("VISIBLE" if result["maxDelta"] >= 8
                           else "NOT VISIBLE (max delta < 8)")
            print(f"{value:<12} {lvl_low:<9.3f} "
                  f"{result['diffPixels']:<10} {result['total']:<10} "
                  f"{result['maxDelta']:<10} {verdict_txt}")
            if result["maxDelta"] < FLOOR_BAR:
                bad.append(
                    f"[{value}] reduced-motion floor-vs-off max delta is "
                    f"only {result['maxDelta']} (RULING 3 wants a raised "
                    f"floor comfortably above the 8-delta visibility bar, "
                    f"bar set at {FLOOR_BAR}) -- an armed microphone in a "
                    f"silent room must not look like an unarmed one")

    print()
    if bad:
        print(f"Task B: {len(bad)} problem(s):")
        for b in bad:
            print(f"  - {b}")
    return 1 if bad else 0


def _lattice_geometry_stable(url: str):
    """RULING 1 (CLAUDE.md 2026-09-16 backlog): "under calm(), the lattice's
    GEOMETRY is static ... with a constant level, two canvas captures a
    second apart are identical." The default audit's shared session (below)
    cannot test that claim in isolation -- its fake microphone audio is
    Chromium's bursty default device, and the lattice's own link distance is
    DERIVED from the level, so a diff there is real level noise as much as
    it could be geometry drift, and the two are not the same defect (the
    second half of ruling 1's own contract is that the drawing SHOULD change
    when the level does). A SEPARATE session with SILENT audio
    (`--use-file-for-fake-audio-capture`, the same mechanism Task B uses)
    holds the level at the engine's own resting floor, so any pixel diff
    left over here is the lattice's own geometry and nothing else.

    Returns (ok, samples, message). `ok` is False on any setup failure too
    (armed check, voice_effect not applying), never silently treated as a
    pass.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="butler_lattice_wav_"))
    silent_wav = tmp_dir / "silent.wav"
    _write_wav(silent_wav, amplitude=0.0)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, executable_path=CHROME,
                args=["--use-fake-ui-for-media-stream",
                      "--use-fake-device-for-media-stream",
                      f"--use-file-for-fake-audio-capture={silent_wav}"])
            try:
                ctx = browser.new_context(
                    ignore_https_errors=True, permissions=["microphone"],
                    reduced_motion="reduce",
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.goto(url, wait_until="load")
                page.wait_for_timeout(2500)
                if not page.evaluate(
                        "matchMedia('(prefers-reduced-motion: reduce)')"
                        ".matches"):
                    return False, [], "reduced motion did not apply"
                if not _wait_armed(page):
                    return False, [], "the page never armed itself"
                set_voice_effect(url, "lattice")
                if not _wait_applied(page, "lattice"):
                    return False, [], "voice_effect=lattice never applied"
                page.wait_for_timeout(SETTLE_MS)
                # WARM UP UNTIL TWO CONSECUTIVE READS MATCH, rather than a
                # fixed wait, and the reason is a real, measured false BAD:
                # fixed waits of 1500ms and (unchanged) 4000ms both caught
                # the SAME one-time change -- nonzero-alpha
                # [2765, 2765, 2728, 2728, 2728, 2728], changing exactly
                # once and then perfectly static for the rest of the
                # window. MEASURED directly against `#wave`'s own pixels
                # every 500ms to rule out this file's own particle physics
                # as the cause: canvas size never changed and `data-state`
                # stayed 'idle' throughout, so it is not a resize and not a
                # state transition -- something upstream of this canvas
                # (the WebRTC audio pipeline is the obvious suspect,
                # app.js's `echoCancellation`/`noiseSuppression`, but this
                # was not diagnosed further, being outside this file's own
                # scope) has a one-time settling transient whose timing
                # varies run to run (observed here anywhere from ~1.2s to
                # tens of seconds). A REAL ongoing geometry drift (the
                # pre-fix 4,272 -> 22,068 one) keeps moving for as long as
                # the page stays open, which is the property this warm-up
                # is checking for before trusting the window below.
                warmup = page.evaluate(
                    """
                    async () => {
                        const c = document.getElementById('wave');
                        const cx = c.getContext('2d');
                        function read() {
                            return cx.getImageData(
                                0, 0, c.width, c.height).data;
                        }
                        function same(a, b) {
                            for (let j = 0; j < a.length; j++) {
                                if (a[j] !== b[j]) return false;
                            }
                            return true;
                        }
                        let prev = read(), ms = 0;
                        while (ms < 30000) {
                            await new Promise(r => setTimeout(r, 1200));
                            ms += 1200;
                            const cur = read();
                            if (same(prev, cur)) return {ms: ms, ok: true};
                            prev = cur;
                        }
                        return {ms: ms, ok: false};
                    }
                    """)
                if not warmup["ok"]:
                    return False, [], (
                        f"never reached two consecutive identical frames "
                        f"within {warmup['ms']}ms of warm-up -- this is a "
                        f"real ongoing change, not a one-time settling "
                        f"transient")
                samples = page.evaluate(
                    """
                    async () => {
                        const c = document.getElementById('wave');
                        const cx = c.getContext('2d');
                        const w = c.width, h = c.height;
                        const out = [];
                        let prev = null;
                        for (let i = 0; i < 6; i++) {
                            const d = cx.getImageData(0, 0, w, h).data;
                            let nz = 0, diff = 0;
                            for (let j = 0; j < d.length; j += 4) {
                                if (d[j + 3] !== 0) nz++;
                                if (prev && (d[j] !== prev[j]
                                        || d[j + 1] !== prev[j + 1]
                                        || d[j + 2] !== prev[j + 2]
                                        || d[j + 3] !== prev[j + 3])) diff++;
                            }
                            out.push({nz: nz,
                                      diffFromPrev: prev ? diff : null});
                            prev = d;
                            await new Promise(r => setTimeout(r, 1200));
                        }
                        return out;
                    }
                    """)
                max_diff = max(
                    (s["diffFromPrev"] for s in samples
                     if s["diffFromPrev"] is not None), default=0)
                ok = max_diff == 0
                msg = (f"warm-up took {warmup['ms']}ms; max frame-to-frame "
                       f"pixel diff across 6 samples ~1.2s apart (~6s "
                       f"total) after that, SILENT audio, reduced motion = "
                       f"{max_diff} px (nonzero-alpha counts: "
                       f"{[s['nz'] for s in samples]})")
                return ok, samples, msg
            finally:
                browser.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_default_audit() -> int:
    bad: list[str] = []
    notes: list[str] = []

    def verdict(good, text: str) -> bool:
        """Print one OK/BAD line and remember the BAD ones for the exit code.

        Mirrors probe_page.py's rule: the exit code is the only thing a
        review agent may trust, never the printed text (test/AGENTS.md).
        """
        if not good:
            bad.append(text)
        print(f"  {'OK ' if good else 'BAD'} {text}")
        return bool(good)

    print(f"probe_reduced_motion: {URL}")
    original_value, values = get_voice_effect(URL)
    print(f"original voice_effect = {original_value!r} (will be restored)")
    print(f"values, read off the daemon's own schema rather than a "
          f"hardcoded copy: {values}")
    if len(values) != 10:
        # Not fatal by itself -- the schema is the source of truth and this
        # probe follows it -- but `aurora` was CUT (RULING 2, CLAUDE.md
        # 2026-09-16 backlog), ten values rather than eleven, so a mismatch
        # is worth a loud line rather than a silent short run. A daemon that
        # has not been RESTARTED since the settings.py edit still reports
        # eleven (choices are read at boot), which is expected and not a
        # bug in this probe.
        print(f"  NOTE: expected 10 values, the schema has {len(values)} "
              f"-- if this daemon has not restarted since aurora was cut, "
              f"that is expected rather than a defect")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    page_errors: list[str] = []
    exit_code = 1
    lat_ok = None  # set by the dedicated silent-audio check, below

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=CHROME,
                # Synthesised device + auto-accept, exactly probe_page.py's
                # --live args -- never the operator's real microphone.
                args=["--use-fake-ui-for-media-stream",
                      "--use-fake-device-for-media-stream"])
            try:
                ctx = browser.new_context(
                    ignore_https_errors=True,
                    permissions=["microphone"],
                    reduced_motion="reduce",
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.on("pageerror", lambda e: page_errors.append(str(e)))

                page.goto(URL, wait_until="load")
                page.wait_for_timeout(2500)

                reduced = page.evaluate(
                    "matchMedia('(prefers-reduced-motion: reduce)')"
                    ".matches")
                if not verdict(
                        reduced,
                        "prefers-reduced-motion: reduce is ACTUALLY set in "
                        "the page (matchMedia), not just requested of the "
                        "browser context"):
                    print("ABORTING: an audit that ran without this "
                          "actually set is worse than no audit at all.")
                    return 1

                # THE PAGE MUST BE ARMED. `armed` in visualiser.js is
                # `state !== 'off'`, and the page mirrors that state onto
                # <html data-state> (app.js, the one writer of it) -- so
                # that attribute is the honest cross-check available from
                # outside the module, without reaching into a private
                # closure variable.
                armed = False
                for _ in range(60):
                    st = page.evaluate(
                        "document.documentElement.dataset.state")
                    if st and st != "off":
                        armed = True
                        break
                    page.wait_for_timeout(500)
                if not verdict(
                        armed,
                        "the page armed itself within 30s of load "
                        "(data-state left 'off', stt_enabled defaults on)"):
                    print("ABORTING: nothing draws with the microphone "
                          "off (that gate is itself a fix -- see the "
                          "module docstring). Every check below would be "
                          "passing against a dead canvas, so the WHOLE "
                          "AUDIT IS INVALID. Reporting a wall of passes "
                          "here would be a lie.")
                    return 1

                for value in values:
                    row: dict = {"value": value}
                    set_voice_effect(URL, value)

                    applied = False
                    for _ in range(20):
                        got = page.evaluate(
                            "document.documentElement.dataset.voicefx")
                        if got == value:
                            applied = True
                            break
                        page.wait_for_timeout(250)
                    if not verdict(
                            applied,
                            f"[{value}] the page adopted voice_effect="
                            f"{value!r} (data-voicefx) within 5s of the "
                            f"settings write"):
                        row["error"] = "never applied on the page"
                        rows.append(row)
                        continue

                    page.wait_for_timeout(SETTLE_MS)
                    is_treatment = value not in ("off", "lattice")

                    rect_js = (
                        "(() => { const r = document.getElementById"
                        "('composer').getBoundingClientRect(); "
                        "return {w: r.width, h: r.height, top: r.top}; "
                        "})()")
                    fx_level_js = (
                        "getComputedStyle(document.getElementById"
                        "('composerFx')).getPropertyValue('--fx-level')"
                        ".trim()")

                    rect_a = page.evaluate(rect_js)
                    fx_a_raw = page.evaluate(fx_level_js)

                    # Canvas stability: two getImageData reads inside ONE
                    # evaluate call, diffed in the page, so no pixel array
                    # ever crosses the Python/JS boundary -- only three
                    # integers come back.
                    canvas = page.evaluate(
                        """
                        async (waitMs) => {
                            const c = document.getElementById('wave');
                            const ctx = c.getContext('2d');
                            const w = c.width, h = c.height;
                            const a = ctx.getImageData(0, 0, w, h).data;
                            await new Promise(r => setTimeout(r, waitMs));
                            const b = ctx.getImageData(0, 0, w, h).data;
                            let diff = 0, nzA = 0, nzB = 0;
                            for (let i = 0; i < a.length; i += 4) {
                                if (a[i] !== b[i] || a[i+1] !== b[i+1] ||
                                    a[i+2] !== b[i+2] ||
                                    a[i+3] !== b[i+3]) diff++;
                                if (a[i+3] !== 0) nzA++;
                                if (b[i+3] !== 0) nzB++;
                            }
                            return {diff: diff, nzA: nzA, nzB: nzB,
                                    w: w, h: h};
                        }
                        """, 1200)

                    # Reading B, taken after the 1200ms the canvas capture
                    # above already spent -- reused as the "second moment"
                    # rather than waiting again.
                    rect_b = page.evaluate(rect_js)
                    fx_b_raw = page.evaluate(fx_level_js)
                    fxstill = page.evaluate(
                        "(() => { const el = document.querySelector"
                        "('#composerFx .fxstill'); "
                        "const cs = getComputedStyle(el); "
                        "return {display: cs.display, "
                        "opacity: cs.opacity}; })()")
                    slots = page.evaluate(
                        "(() => ({"
                        "waveFx: document.getElementById('waveFx').hidden,"
                        "waveMic: document.getElementById('waveMic')"
                        ".hidden,"
                        "field: document.getElementById('field').hidden"
                        "}))()")

                    row.update({
                        "rect_a": rect_a, "rect_b": rect_b,
                        "fx_a": fx_a_raw, "fx_b": fx_b_raw,
                        "fxstill": fxstill, "slots": slots,
                        "canvas": canvas, "is_treatment": is_treatment,
                    })

                    composer_stable = (
                        rect_a["w"] == rect_b["w"]
                        and rect_a["h"] == rect_b["h"]
                        and rect_a["top"] == rect_b["top"])
                    verdict(
                        composer_stable,
                        f"[{value}] #composer's box never moves "
                        f"({rect_a['w']:.1f}x{rect_a['h']:.1f} "
                        f"-> {rect_b['w']:.1f}x{rect_b['h']:.1f})")

                    if value == "off":
                        verdict(
                            all(slots.values()),
                            f"[{value}] every wave surface is hidden "
                            f"({slots})")
                        verdict(
                            fxstill["display"] == "none",
                            f"[{value}] .fxstill stays display:none "
                            f"(got {fxstill['display']!r})")
                        verdict(
                            canvas["diff"] == 0,
                            f"[{value}] the shared canvas is frozen while "
                            f"hidden (diff={canvas['diff']} px over "
                            f"1.2s)")
                    elif value == "lantern":
                        # THE LIT BORDER (AC-690, CU-3): CSS only, no halo,
                        # no canvas. Its glow shows only while Butler hears,
                        # thinks or speaks, and this session sits at rest, so
                        # the rendered state is FORCED to `capturing` for two
                        # readings (written by this probe, not by paint())
                        # and put back: under reduced motion the glow must be
                        # identical at both moments while --fx-level moves,
                        # and the border must still be the style's blue.
                        verdict(
                            fxstill["display"] == "none",
                            f"[{value}] .fxstill stays display:none "
                            f"(got {fxstill['display']!r})")
                        verdict(
                            canvas["diff"] == 0 and canvas["nzB"] == 0,
                            f"[{value}] the canvas draws nothing "
                            f"(diff={canvas['diff']}, nonzero={canvas['nzB']})")
                        lit = page.evaluate("""async () => {
                          const html = document.documentElement;
                          const was = html.dataset.state;
                          html.dataset.state = 'capturing';
                          // Headless Chrome never finishes a transition on
                          // its own (test/AGENTS.md): finish the box's.
                          await new Promise(r => requestAnimationFrame(r));
                          document.getAnimations().forEach(a => a.finish());
                          const fx = document.getElementById('composerFx');
                          const glow = document.querySelector('#composerFx .fxlantern');
                          const read = () => [getComputedStyle(glow).boxShadow,
                            fx.style.getPropertyValue('--fx-level'),
                            getComputedStyle(document.getElementById('composer')).boxShadow,
                            getComputedStyle(glow).display];
                          const a = read();
                          await new Promise(r => setTimeout(r, 900));
                          const b = read();
                          // Caption's seam (AC-691): under reduced motion the
                          // canvas goes and STILL bars stay (AC-259).
                          const seam = document.getElementById('waveSeam');
                          const bars = getComputedStyle(seam, '::before');
                          const seamRead = {
                            seam: getComputedStyle(seam).display,
                            canvas: getComputedStyle(document.getElementById('wave')).display,
                            mask: bars.maskImage || bars.webkitMaskImage || 'none',
                            anim: bars.animationName};
                          html.dataset.state = was;
                          const blue = getComputedStyle(html).getPropertyValue('--blue').trim();
                          return {a, b, blue, style: html.dataset.style, seamRead};
                        }""")
                        a, b = lit["a"], lit["b"]
                        if lit["style"] == "caption":
                            sr = lit["seamRead"]
                            verdict(a[3] == "none",
                                    f"[{value}] Caption draws no glow (display "
                                    f"{a[3]!r})")
                            verdict(sr["seam"] == "block" and sr["canvas"] == "none"
                                    and sr["mask"] not in ("", "none")
                                    and sr["anim"] in ("", "none"),
                                    f"[{value}] Caption's seam freezes into still "
                                    f"bars while hearing ({sr})")
                        else:
                            verdict(a[3] == "block",
                                    f"[{value}] the glow is drawn while hearing "
                                    f"(display {a[3]!r})")
                            verdict(a[0] == b[0],
                                    f"[{value}] the glow holds still under reduced "
                                    f"motion (level {a[1]!r} -> {b[1]!r}; shadow "
                                    f"{'same' if a[0] == b[0] else 'MOVED'})")
                        verdict("1.5px" in a[2],
                                f"[{value}] the border keeps its colour "
                                f"({a[2]!r}, --blue {lit['blue']})")
                    elif value == "lattice":
                        verdict(
                            fxstill["display"] == "none",
                            f"[{value}] .fxstill stays display:none "
                            f"(got {fxstill['display']!r}; lattice is not "
                            f"a voice_effect TREATMENT, so it never sets "
                            f"--fx-level)")
                        # RULING 1 (CLAUDE.md 2026-09-16 backlog): this used
                        # to be a NOTE, never asserted, because `calm()`
                        # only reduced the lattice and never stopped it --
                        # this 1.2s reading, taken under the shared session's
                        # bursty fake-microphone audio, is kept as an
                        # observational note rather than an assertion for
                        # exactly the reason `_lattice_geometry_stable`'s own
                        # docstring gives: the level genuinely fluctuates
                        # here, so a nonzero diff is not by itself proof of a
                        # geometry defect. The HARD assertion is the
                        # dedicated silent-audio check below, after this
                        # session closes.
                        note = (
                            f"NOTE [{value}]: canvas diff over 1.2s under "
                            f"reduced motion, BURSTY fake-microphone audio "
                            f"= {canvas['diff']} px (nonzero-alpha "
                            f"{canvas['nzA']} -> {canvas['nzB']}) -- this "
                            f"session's level genuinely fluctuates, so this "
                            f"number alone proves nothing about geometry; "
                            f"see the LATTICE GEOMETRY STABILITY check "
                            f"below, which holds the level constant.")
                        notes.append(note)
                        print(f"  {note}")
                    else:
                        verdict(
                            fxstill["display"] == "block",
                            f"[{value}] .fxstill is display:block "
                            f"(got {fxstill['display']!r})")
                        try:
                            float(fx_a_raw)
                            fx_numeric = True
                        except ValueError:
                            fx_numeric = False
                        verdict(
                            fx_numeric,
                            f"[{value}] --fx-level reads as a number "
                            f"(got {fx_a_raw!r})")
                        verdict(
                            canvas["diff"] == 0,
                            f"[{value}] the canvas draws NOTHING under "
                            f"reduced motion, same branch as an unbuilt "
                            f"name (AC-259) (diff={canvas['diff']} px, "
                            f"nonzero-alpha {canvas['nzA']} -> "
                            f"{canvas['nzB']})")

                    clip = page.evaluate(
                        "(() => { const r = document.getElementById"
                        "('composer').getBoundingClientRect(); "
                        "const vw = window.innerWidth; "
                        "const bottom = Math.min(r.bottom + 150, "
                        "window.innerHeight); "
                        "return {x: 0, y: 0, width: vw, height: bottom}; "
                        "})()")
                    shot = OUT_DIR / f"rm-{value}.png"
                    page.screenshot(path=str(shot), clip=clip)
                    row["screenshot"] = shot.name
                    verdict(shot.exists() and shot.stat().st_size > 0,
                            f"[{value}] screenshot written to "
                            f"{shot.name}")

                    rows.append(row)

                # ---- TENSION 1: lattice over a longer window -------------
                set_voice_effect(URL, "lattice")
                for _ in range(20):
                    if page.evaluate(
                            "document.documentElement.dataset.voicefx"
                    ) == "lattice":
                        break
                    page.wait_for_timeout(250)
                page.wait_for_timeout(SETTLE_MS)
                lattice_samples = page.evaluate(
                    """
                    async () => {
                        const c = document.getElementById('wave');
                        const ctx = c.getContext('2d');
                        const w = c.width, h = c.height;
                        const out = [];
                        for (let i = 0; i < 6; i++) {
                            const d = ctx.getImageData(0, 0, w, h).data;
                            let nz = 0;
                            for (let j = 3; j < d.length; j += 4) {
                                if (d[j] !== 0) nz++;
                            }
                            out.push(nz);
                            await new Promise(r => setTimeout(r, 1200));
                        }
                        return out;
                    }
                    """)
                changed = len(set(lattice_samples)) > 1
                print()
                print(f"OBSERVATIONAL (bursty audio, NOT asserted) -- "
                      f"lattice nonzero-alpha pixel counts, 6 samples "
                      f"~1.2s apart under reduced motion (~7.2s total): "
                      f"{lattice_samples}")
                print(f"  {'CHANGED' if changed else 'IDENTICAL'} across "
                      f"the window. RULING 1 (CLAUDE.md 2026-09-16 "
                      f"backlog) is now built: `calm()` freezes motion, "
                      f"breathNow() and sweepP, and the position "
                      f"integration is gated on `!calm()` directly -- the "
                      f"HARD assertion for that is the dedicated "
                      f"silent-audio check below. These six numbers, taken "
                      f"under this session's BURSTY fake-microphone audio, "
                      f"moving up and down rather than only up (unlike the "
                      f"pre-fix 4,272 -> 22,068 one-way drift this file's "
                      f"own header docstring records) is consistent with "
                      f"real level noise driving `linkD` -- amplitude "
                      f"tracking level is the OTHER half of ruling 1's "
                      f"contract, not a defect.")

                # ---- TENSION 2: the still halo's breathing floor --------
                set_voice_effect(URL, "mist")
                for _ in range(20):
                    if page.evaluate(
                            "document.documentElement.dataset.voicefx"
                    ) == "mist":
                        break
                    page.wait_for_timeout(250)
                page.wait_for_timeout(SETTLE_MS)
                breathing = page.evaluate(
                    """
                    async () => {
                        const el = document.getElementById('composerFx');
                        const out = [];
                        for (let i = 0; i < 26; i++) {
                            const v = parseFloat(getComputedStyle(el)
                                .getPropertyValue('--fx-level'));
                            out.push(isNaN(v) ? 0 : v);
                            await new Promise(r => setTimeout(r, 200));
                        }
                        return out;
                    }
                    """)
                lo, hi = min(breathing), max(breathing)
                print()
                print(f"TENSION 2 -- --fx-level on 'mist', 26 samples "
                      f"every 200ms (~5.2s) under reduced motion: "
                      f"min={lo:.4f} max={hi:.4f} "
                      f"amplitude={hi - lo:.4f}")
                print(f"  samples: {[round(x, 3) for x in breathing]}")
                print(f"  fxRest = 0.15 + breathNow()*0.05 "
                      f"(visualiser.js:1918), breathNow() = "
                      f"0.5 + 0.5*sin(t/2600) (visualiser.js:1054) -- a "
                      f"period of about 16.3s from the source, so a 5.2s "
                      f"window sees roughly a third of one cycle and the "
                      f"FLOOR ALONE should range 0.15-0.20 "
                      f"(amplitude 0.05). The still halo's opacity is "
                      f"--fx-level * 0.42 (index.html), so this is what "
                      f"actually pulses on screen.")
                if hi - lo > 0.10:
                    # MEASURED, not assumed: the observed amplitude above
                    # is far wider than the 0.05 the floor formula alone
                    # predicts, and several samples hit 1.0 -- the floor's
                    # own ceiling is 0.20. Chrome's
                    # --use-fake-device-for-media-stream (no
                    # --use-file-for-fake-audio-capture pointed at a
                    # silent file) emits a nonzero synthetic signal, not
                    # true silence, so `raw = tap.rms()` is real and
                    # `fxLive = live > fxRest ? live : fxRest` is picking
                    # the FAKE MICROPHONE's own tone on most samples, not
                    # the breathing floor. Said plainly rather than
                    # reported as a clean "breathing" number: this
                    # amplitude is floor-plus-fake-device-noise, and the
                    # floor's own honest range is the 0.05 quoted above,
                    # read from source rather than isolated by this run.
                    print(f"  CAVEAT: the observed amplitude "
                          f"({hi - lo:.4f}) is far wider than the floor "
                          f"formula's own 0.05 range and repeatedly hits "
                          f"1.0, which the floor alone cannot reach "
                          f"(max 0.20). Chrome's fake microphone emits a "
                          f"nonzero synthetic tone, not silence, so this "
                          f"number is floor-plus-fake-audio, not the "
                          f"breathing floor in isolation. The floor's own "
                          f"amplitude is read from the source formula "
                          f"above, not isolated empirically by this run.")

                verdict(not page_errors,
                        f"no uncaught page errors "
                        f"({len(page_errors)} found: {page_errors})")

            finally:
                browser.close()

        # ---- LATTICE GEOMETRY STABILITY, RULING 1 (CLAUDE.md 2026-09-16
        # backlog) -- a SEPARATE, SILENT-audio session, opened only after
        # the shared bursty session above has closed. This is the HARD
        # assertion the observational note above cannot be: "static
        # geometry" needs a CONSTANT level to test in isolation, and this
        # daemon has only one microphone-shaped input per browser context. --
        print()
        print("=== LATTICE GEOMETRY STABILITY (RULING 1, silent audio) ===")
        lat_ok, lat_samples, lat_msg = _lattice_geometry_stable(URL)
        verdict(lat_ok, f"[lattice] {lat_msg}")
        if not lat_ok and not lat_samples:
            # A setup failure (armed / applied / reduced-motion), not a
            # geometry reading -- said plainly rather than folded into the
            # same BAD line as a real pixel diff would be.
            print(f"  ABORTED before any sample was taken: {lat_msg}")
    finally:
        # RESTORE, ALWAYS -- the one setting this probe writes, put back
        # even if the loop above raised or the browser crashed. Its own
        # docstring says it writes a setting; this is that promise kept.
        try:
            set_voice_effect(URL, original_value)
            back, _ = get_voice_effect(URL)
            restored = back == original_value
            print()
            print(f"restore: voice_effect -> {original_value!r} "
                  f"({'confirmed' if restored else 'NOT CONFIRMED, daemon now reports ' + repr(back)})")
            if not restored:
                bad.append(f"voice_effect did not restore to "
                           f"{original_value!r} (daemon reports "
                           f"{back!r})")
        except Exception as exc:  # noqa: BLE001 -- report, do not swallow
            print(f"restore FAILED: {exc!r} -- voice_effect may still be "
                  f"whatever value this run last set on the daemon")
            bad.append(f"restore raised: {exc!r}")

    # ---- summary table ------------------------------------------------
    print()
    print("=== per-treatment summary ===")
    print(f"{'value':<10} {'canvas-stable':<16} {'fxstill':<20} "
          f"{'fx-level-moves':<16} {'composer-h':<20} {'screenshot':<16}")
    for row in rows:
        if "error" in row:
            print(f"{row['value']:<10} {'FAILED: ' + row['error']}")
            continue
        canvas = row["canvas"]
        if row["value"] == "off":
            canvas_col = f"stable(diff=0)" if canvas["diff"] == 0 \
                else f"MOVED(diff={canvas['diff']})"
        elif row["value"] == "lattice":
            # RULING 1 (CLAUDE.md 2026-09-16 backlog): stable/moved comes
            # from the DEDICATED silent-audio check (`lat_ok`), not from
            # this row's own 1.2s bursty-audio reading -- that reading is
            # real level noise as much as it could be geometry, and is not
            # the assertion (see the OBSERVATIONAL section above).
            if lat_ok is None:
                canvas_col = f"UNKNOWN(bursty diff={canvas['diff']})"
            elif lat_ok:
                canvas_col = "stable(diff=0)"
            else:
                canvas_col = f"MOVED(silent diff, see above)"
        else:
            canvas_col = f"stable(diff=0)" if canvas["diff"] == 0 \
                else f"MOVED(diff={canvas['diff']})"
        fxstill_col = (f"{row['fxstill']['display']}"
                       f"/op={row['fxstill']['opacity']}")
        if row["is_treatment"]:
            try:
                a = float(row["fx_a"])
                b = float(row["fx_b"])
                fx_col = (f"{a:.3f}->{b:.3f}"
                          f"{' (moved)' if a != b else ' (static)'}")
            except ValueError:
                fx_col = f"non-numeric({row['fx_a']!r})"
        else:
            fx_col = "n/a"
        rect_a, rect_b = row["rect_a"], row["rect_b"]
        composer_col = (
            f"{rect_a['w']:.0f}x{rect_a['h']:.0f}"
            f"{'==' if (rect_a['w'], rect_a['h']) == (rect_b['w'], rect_b['h']) else '!='}"
            f"{rect_b['w']:.0f}x{rect_b['h']:.0f}")
        shot_col = row.get("screenshot", "MISSING")
        print(f"{row['value']:<10} {canvas_col:<16} {fxstill_col:<20} "
              f"{fx_col:<16} {composer_col:<20} {shot_col:<16}")

    print()
    if bad:
        print(f"{len(bad)} FAILED assertion(s):")
        for b in bad:
            print(f"  - {b}")
    else:
        print("all assertions passed")

    exit_code = 1 if bad else 0
    return exit_code


def main() -> int:
    """Dispatcher. No flags: the original default audit, unchanged
    behaviour and exit code. `--normal` and/or `--visibility` run ONLY the
    named task(s) instead -- they do not also run the default audit, so a
    single invocation stays inside one clear budget."""
    do_normal = "--normal" in sys.argv[1:]
    do_visibility = "--visibility" in sys.argv[1:]
    if not do_normal and not do_visibility:
        return run_default_audit()

    codes = []
    if do_normal:
        codes.append(run_normal_motion(URL))
    if do_visibility:
        codes.append(run_visibility(URL))
    return 1 if any(c != 0 for c in codes) else 0


if __name__ == "__main__":
    sys.exit(main())
