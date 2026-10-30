"""Can a second process be starved of this microphone? A GO/NO-GO spike.

`docs/desk-audio.md` step 1, and it is deliberately not a fix. The failure it
hunts -- a `sounddevice` stream that opens cleanly and then receives ZERO
callbacks, with no exception and no dropped-frame flag -- was measured twice on
2026-09-09 and then did NOT reproduce in twelve consecutive attempts. Until
something makes it happen on demand, no change to `desk/audio.py` can be shown
to fix it, and the spec says so.

WHAT IT VARIES
--------------
Three OPEN SHAPES, which are the three axes Butler differs from Handy on:

    MME    16000 x 1, blocksize 512   <- exactly what `desk/audio.py` ships
    MME    native rate/channels, blocksize 0
    WASAPI native rate/channels, blocksize 0   <- what Handy's shape maps to

WASAPI at 16000 is included and is EXPECTED to fail at open with "Invalid
sample rate": that failure is the evidence that Butler's fixed rate is what
forces it onto MME, so it is recorded rather than skipped.

Three CONTENDERS, each holding the device while the probes run:

    nobody          -- the baseline; anything failing here is not contention
    a second PYTHON process
    CHROME, holding the REAL microphone through getUserMedia

**The Chrome contender inverts `probe_page.py`'s rule on purpose.** That probe
passes `--use-fake-device-for-media-stream` precisely so it never opens the
operator's real microphone. This one must, because "a browser is holding the
microphone" is the reported case and a synthesised device would prove nothing.
It grants permission with `--use-fake-ui-for-media-stream`, serves its page
from `127.0.0.1` because `getUserMedia` needs a secure context (a `data:` or
`file:` URL is not one), and it VERIFIES through Windows' own consent store
that Chrome really took the microphone before trusting a single result.

WHAT IT RECORDS
---------------
Frames received per probe, and the count of probes that received NOTHING. It
also records which processes Windows says hold the microphone at each phase,
and whether the desk agent is among them -- because the spec's own warning is
that RDP audio redirection may be the real trigger, in which case the browser
is a coincidence and the numbers must not be read as contention.

SAFETY
------
It opens the microphone and starts Chrome. It writes no setting, touches no
daemon state, and kills every process by a PID it recorded. It does NOT need
the desk agent stopped -- the agent holding the device is itself one of the
conditions worth measuring, and the run reports whether it was up.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/spike_mic_contention.py
      ... --seconds 3 --repeats 5      (longer, for a rarer failure)
"""
from __future__ import annotations

import argparse
import http.server
import json
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CONSENT = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
           r"\ConsentStore\microphone")

PAGE = b"""<!doctype html><meta charset=utf-8><title>mic holder</title>
<body><p id=s>opening...</p><script>
navigator.mediaDevices.getUserMedia({audio:true}).then(st => {
  window._held = st;                       // keep a reference forever
  document.getElementById('s').textContent = 'HOLDING ' + st.getAudioTracks().length;
}).catch(e => { document.getElementById('s').textContent = 'FAILED ' + e; });
</script></body>"""


# --------------------------------------------------------------------------- #
# who holds the microphone, according to Windows
# --------------------------------------------------------------------------- #
def mic_holders() -> list[str]:
    """Processes with `LastUsedTimeStop == 0`, i.e. holding it right now."""
    out: list[str] = []

    def walk(key):
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(key, sub) as k:
                try:
                    winreg.QueryValueEx(k, "LastUsedTimeStart")
                    if winreg.QueryValueEx(k, "LastUsedTimeStop")[0] == 0:
                        out.append(sub.split("#")[-1] or sub)
                except OSError:
                    pass
                walk(k)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT) as root:
            walk(root)
    except OSError:
        pass
    return sorted(set(out))


# --------------------------------------------------------------------------- #
# the worker: one stream, one measurement, its own PROCESS
# --------------------------------------------------------------------------- #
def worker(api: str, rate: int, channels: int, blocksize: int,
           seconds: float) -> int:
    """Open one stream and print JSON. A separate process ON PURPOSE.

    The failure is per PROCESS, not per stream: two streams inside one process
    both deliver, and a second process is what gets nothing [MEASURED
    2026-09-09]. A thread here would measure the wrong thing.
    """
    import sounddevice as sd

    got = {"frames": 0, "calls": 0}

    def cb(indata, frames, _t, _status):
        got["frames"] += frames
        got["calls"] += 1

    device = device_for(api)
    result = {"api": api, "rate": rate, "channels": channels,
              "blocksize": blocksize, "device": device}
    try:
        stream = sd.InputStream(samplerate=rate, channels=channels,
                                dtype="int16", blocksize=blocksize,
                                device=device, callback=cb)
    except Exception as e:                                # noqa: BLE001
        result["open_error"] = str(e)[:90]
        print(json.dumps(result), flush=True)
        return 0
    try:
        stream.start()
        time.sleep(seconds)
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:                                 # noqa: BLE001
            pass
    result.update(got)
    print(json.dumps(result), flush=True)
    return 0


def device_for(api: str):
    """The default input device of a host API, by NAME not by index.

    Indices move when devices appear; the host API name does not.
    """
    import sounddevice as sd

    for i, info in enumerate(sd.query_hostapis()):
        if info["name"].lower() == api.lower():
            dev = info.get("default_input_device", -1)
            if dev is not None and dev >= 0:
                return dev
            for j, d in enumerate(sd.query_devices()):
                if d["hostapi"] == i and d["max_input_channels"] > 0:
                    return j
    raise SystemExit(f"no host API named {api!r}")


def run_worker(api, rate, channels, blocksize, seconds) -> dict:
    out = subprocess.run(
        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
         "--worker", "--api", api, "--rate", str(rate),
         "--channels", str(channels), "--blocksize", str(blocksize),
         "--seconds", str(seconds)],
        capture_output=True, text=True, cwd=str(REPO))
    for line in reversed((out.stdout or "").splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"open_error": (out.stderr or "no output")[-90:]}


# --------------------------------------------------------------------------- #
# the contenders
# --------------------------------------------------------------------------- #
class PythonHolder:
    """A second process holding the device for the whole phase."""

    def __init__(self, api, rate, channels, seconds):
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
             "--worker", "--api", api, "--rate", str(rate),
             "--channels", str(channels), "--blocksize", "0",
             "--seconds", str(seconds)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(REPO))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()            # by the handle we hold, never by name


class ChromeHolder:
    """Real Chrome, holding the REAL microphone through getUserMedia."""

    def __init__(self):
        self.httpd = None
        self.thread = None
        self.play = None
        self.browser = None
        self.ok = False

    def start(self) -> bool:
        from patchright.sync_api import sync_playwright

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

        self.play = sync_playwright().start()
        # NO --use-fake-device-for-media-stream, unlike probe_page.py. The
        # whole question is whether a browser on the REAL microphone starves
        # Butler, and a synthesised device would answer a different one.
        self.browser = self.play.chromium.launch(
            headless=False, executable_path=CHROME,
            args=["--use-fake-ui-for-media-stream"])
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        for _ in range(40):
            time.sleep(0.25)
            if "HOLDING" in (page.text_content("#s") or ""):
                self.ok = True
                break
        self.state = page.text_content("#s")
        return self.ok

    def stop(self):
        for close in (lambda: self.browser and self.browser.close(),
                      lambda: self.play and self.play.stop(),
                      lambda: self.httpd and self.httpd.shutdown()):
            try:
                close()
            except Exception:                             # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
def shapes():
    import sounddevice as sd

    out = []
    for api in ("MME", "Windows WASAPI"):
        try:
            dev = device_for(api)
        except SystemExit:
            continue
        info = sd.query_devices(dev)
        native = int(info["default_samplerate"])
        ch = min(2, int(info["max_input_channels"]))
        out.append((f"{api} 16000x1 bs512 (SHIPS)", api, 16000, 1, 512))
        out.append((f"{api} {native}x{ch} bs0 (native)", api, native, ch, 0))
    return out


def phase(label: str, cells, repeats: int, seconds: float) -> list:
    print(f"\n=== {label} ===")
    print(f"    mic holders now: {mic_holders() or 'nobody'}")
    rows = []
    for name, api, rate, ch, bs in cells:
        runs = [run_worker(api, rate, ch, bs, seconds) for _ in range(repeats)]
        if any("open_error" in r for r in runs):
            why = next(r["open_error"] for r in runs if "open_error" in r)
            print(f"  {name:<34} OPEN FAILED: {why}")
            rows.append((label, name, None, None))
            continue
        frames = [r.get("frames", 0) for r in runs]
        zeros = sum(1 for f in frames if f == 0)
        mark = "  <-- STARVED" if zeros else ""
        print(f"  {name:<34} frames {frames}  zero {zeros}/{len(frames)}{mark}")
        rows.append((label, name, frames, zeros))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--api", default="MME")
    ap.add_argument("--rate", type=int, default=16000)
    ap.add_argument("--channels", type=int, default=1)
    ap.add_argument("--blocksize", type=int, default=512)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--no-chrome", action="store_true")
    args = ap.parse_args()

    if args.worker:
        return worker(args.api, args.rate, args.channels, args.blocksize,
                      args.seconds)

    cells = shapes()
    print("SHAPES UNDER TEST")
    for name, api, rate, ch, bs in cells:
        print(f"  {name}")
    agent_up = any("python" in h.lower() for h in mic_holders())
    print(f"\ndesk agent appears to hold the mic: {agent_up}")

    rows = phase("CONTENDER: nobody (baseline)", cells, args.repeats,
                 args.seconds)

    hold_for = (len(cells) * args.repeats * (args.seconds + 1.5)) + 10
    holder = PythonHolder("MME", 16000, 1, hold_for)
    time.sleep(1.5)
    try:
        rows += phase("CONTENDER: a second PYTHON process", cells,
                      args.repeats, args.seconds)
    finally:
        holder.stop()

    if not args.no_chrome:
        chrome = ChromeHolder()
        try:
            got = chrome.start()
            print(f"\nchrome getUserMedia state: {chrome.state!r}")
            holders = mic_holders()
            if not got or not any("chrome" in h.lower() for h in holders):
                print(f"  CHROME IS NOT HOLDING THE MIC ({holders}); the "
                      f"browser phase would prove nothing and is SKIPPED")
            else:
                rows += phase("CONTENDER: CHROME on the real mic", cells,
                              args.repeats, args.seconds)
        finally:
            chrome.stop()

    print("\n================ VERDICT ================")
    starved = [r for r in rows if r[3]]
    if starved:
        print("REPRODUCED. Shapes that received NOTHING at least once:")
        for label, name, frames, zeros in starved:
            print(f"  {zeros}/{args.repeats}  {name:<34} under {label}")
    else:
        print("NOT REPRODUCED in this run. Every shape that opened delivered")
        print("audio under every contender. Per docs/desk-audio.md step 1,")
        print("the format change cannot be shown to fix anything on this")
        print("evidence -- ship the DETECTION (AC-126, AC-127) regardless.")
    print(f"mic holders at exit: {mic_holders() or 'nobody'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
