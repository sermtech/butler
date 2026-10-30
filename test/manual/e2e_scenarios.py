"""End-to-end USER SCENARIOS, driven in a real browser against a real daemon.

    .venv\\Scripts\\python.exe -X utf8 test\\manual\\e2e_scenarios.py
    ... --only=A,B,C      run only those sections
    ... --keep            leave the scratch daemon's state and log behind
    ... --headed          watch it

Not a test and not collected by pytest (`manual/` is excluded by
`pytest.ini`). It is the instrument for the owner's ask of 2026-09-11: "we
need to imagine user scenarios and stories, write a list of tests and test
with playwright". Its OUTPUT is the deliverable -- a list of the things that
do and do not work for somebody using this app -- and it is written to be run
again after each one is fixed.

## How it differs from the two probes that already exist

`probe_page.py` asks "did the page load, and does the settings surface
navigate". `probe_ux.py` asks "does one criterion of `docs/satellite-ux.md`
hold". Both point at a daemon somebody else started, and both are mostly
single-element assertions.

This one BOOTS ITS OWN DAEMON and runs multi-step STORIES. A story fails here
when the steps do not compose, even when every element in it is present --
which is the class of defect neither probe can see and pytest cannot either,
because pytest asserts on the wire and the wire being right is not the same
as the app being usable.

## What it runs against, stated because it bounds every result below

* **A scratch daemon it starts itself**, on a free port, with its own
  `BUTLER_STATE` and `hotkeys_enabled: false` -- without that flag a scratch
  daemon installs a system-wide keyboard hook and takes the owner's numpad
  (AGENTS.md). It is killed by the PID found LISTENING on its own port, never
  by image name.
* **`brain_mode: harness_only` on the FAKE harness.** That is what gives a
  real conversation RECORD with no coding agent running: the session routes
  404 in endpoint mode, so the sidebar, search, delete and restore scenarios
  need a brain with `HAS_SESSIONS`. The fake answers a fixed two-turn script,
  which is why nothing here asserts on the WORDS of an answer.
* **The FAKE recognizer.** The page's wake detector is real and is what
  section G tests; what the daemon transcribes is scripted. A real GGUF would
  add minutes and would be measuring the recognizer, which `test_dictate.py`
  already does against recorded fixtures.
* **The REAL Kokoro speech engine**, named in a scratch `config/models.json`.
  Not faked, because section H is about which voice the room actually hears
  and a double with two invented voices cannot answer that.

So: anything below about WHAT BUTLER SAYS is the fake script; anything about
WHAT THE PAGE DOES is real.

## Two things about the harness that cost a run each, recorded so they do not

**`page.add_init_script` DOES NOT RUN under patchright.** MEASURED
2026-09-11: a script that sets `window.__e2eBinary = 0` left it `undefined`
after load, and installing one ALSO made every WebSocket attempt fail with
`net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` -- so the page showed
"NO LINK" and a disabled composer, which reads exactly like a broken app.
That is the same finding `test/AGENTS.md` already records for
`route_web_socket`, one layer down: patchright strips the init-script
mechanism. Anything this file needs from inside the page is installed with
`page.evaluate` AFTER load, and anything it needs from the wire comes from
`page.on("websocket")` instead.

**The wire is read PASSIVELY.** `Wire` below attaches to
`websocket -> framereceived`, which needs no page-side hook at all, sees
binary frames as well as text, and cannot perturb what it measures. It is
also the only honest way to answer "was this turn silent" -- the page keeps
no audio, so counting `reply` bubbles cannot tell a muted turn from a spoken
one.

## The rules it follows

* It never touches port 8140 and never writes the repository's own `state/`.
* Every check reports what it OBSERVED, not just pass or fail, so a
  regression reads as "was two chips, got one" rather than as a bare FAIL.
* A check that cannot be run says SKIP and why. A skip is not a pass and is
  counted separately: a green run that proved nothing is the thing this file
  exists to avoid (D-13).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
WAKE_WAV = REPO / "test" / "assets" / "wake_then_command.wav"

_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE

ONLY = next((a.split("=", 1)[1].upper().split(",")
             for a in sys.argv if a.startswith("--only=")), None)
KEEP = "--keep" in sys.argv
HEADED = "--headed" in sys.argv

# The smallest valid PNG, an 8-bit greyscale 1x1. Written out rather than
# checked in, `test_attachments.py`'s rule: nothing here depends on a binary
# fixture somebody has to trust.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
    "0000000a4944415408d76360000000020001e221bc330000000049454e44ae4260"
    "82")

PDF_TEXT = "the barometer is on the shelf by the door"
EN_LINE = "Run the tests again and tell me which ones failed."
PT_LINE = "Voce pode rodar os testes e me dizer quantos falharam?"


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
ROWS: list[tuple] = []


def record(section, sid, story, verdict, observed):
    ROWS.append((section, sid, story, verdict, observed))
    print(f"  [{verdict}] {sid}  {story}")
    print(f"         observed: {observed}")


def ok(section, sid, story, condition, observed):
    record(section, sid, story, "PASS" if condition else "FAIL", observed)
    return bool(condition)


def skip(section, sid, story, why):
    record(section, sid, story, "SKIP", why)


def note(section, sid, story, observed):
    """A MEASUREMENT with no pass/fail attached, printed into the report.

    For the questions whose answer is "it depends what you wanted" -- what
    each brain mode dims, what a control is called -- where inventing a
    verdict would be the report deciding something the owner has not.
    """
    ROWS.append((section, sid, story, "NOTE", observed))
    print(f"  [NOTE] {sid}  {story}")
    print(f"         observed: {observed}")


# --------------------------------------------------------------------------- #
# the scratch daemon
# --------------------------------------------------------------------------- #
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_pdf(text: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject,
                               DictionaryObject, NameObject, NumberObject)
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii"))
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Contents")] = writer._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)})})
    page[NameObject("/MediaBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(200),
         NumberObject(200)])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class Scratch:
    """A daemon of our own. Started here, killed by the PID on its own port.

    NEVER by image name: a subagent matched `butlerd.py` in `wmic` output on
    2026-08-19 and `taskkill` took the owner's live TLS daemon with it
    (AGENTS.md). The listening port is the only identity that cannot collide.
    """

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="butler-e2e-"))
        self.state = self.dir / "state"
        (self.state / "chats").mkdir(parents=True)
        if self.state.resolve() == (REPO / "state").resolve():
            raise SystemExit("refusing to run against the repository's state/")
        (self.state / "settings.json").write_text(json.dumps({
            # Without this a scratch daemon installs a SYSTEM-WIDE keyboard
            # hook and the owner's numpad dictates into the wrong Butler.
            "hotkeys_enabled": False,
            # A brain with HAS_SESSIONS, so there is a conversation RECORD for
            # the sidebar, search, delete and restore scenarios to be about.
            "brain_mode": "harness_only",
            "harness": "fake",
            "speak": True,
            "language": "auto",
            "voice": "",
            # The looping fake microphone in section G never falls silent, so
            # nothing else would end a turn. The ceiling IS a real terminal
            # path (D-1), so ending on it exercises the same re-arm.
            "absolute_max_turn_ms": 10000,
        }), encoding="utf-8")
        # A scratch config, so the owner's real one is never read AND the
        # speech engine is the real one. With no config at all every port
        # takes the all-fakes default, and section H would then be asserting
        # against two invented voice names.
        self.config = self.dir / "models.json"
        self.config.write_text(json.dumps(
            {"adapters": {"asr": "fake", "tts": "kokoro"}}), encoding="utf-8")
        self.port = free_port()
        self.url = f"https://127.0.0.1:{self.port}/"
        self.proc = None
        self.log = self.dir / "daemon.log"

    def start(self) -> None:
        env = {**os.environ,
               "BUTLER_STATE": str(self.state),
               "BUTLER_CONFIG": str(self.config),
               "BUTLER_PORT": str(self.port),
               "PYTHONUTF8": "1",
               "PYTHONIOENCODING": "utf-8"}
        handle = open(self.log, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "daemon.butlerd"],
            cwd=str(REPO), env=env, stdout=handle, stderr=subprocess.STDOUT)
        for _ in range(180):
            time.sleep(1)
            try:
                self.get("/api/settings")
                print(f"daemon up on {self.url}")
                return
            except Exception:
                continue
        raise SystemExit(f"the scratch daemon never answered; see {self.log}")

    def get(self, path):
        with urllib.request.urlopen(self.url.rstrip("/") + path,
                                    timeout=25, context=_TLS) as r:
            return json.loads(r.read())

    def post(self, path, body):
        req = urllib.request.Request(
            self.url.rstrip("/") + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=25, context=_TLS) as r:
            return json.loads(r.read() or b"{}")

    def setting(self, key):
        return {r["key"]: r["value"]
                for r in self.get("/api/settings")["settings"]}[key]

    def stop(self) -> None:
        pid = self._listening_pid()
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
            print(f"killed the scratch daemon by PID {pid} (found LISTENING "
                  f"on port {self.port}, never by name)")
        if self.proc:
            try:
                self.proc.wait(timeout=15)
            except Exception:
                pass
        if KEEP:
            print(f"--keep: state and log left at {self.dir}")
        else:
            shutil.rmtree(self.dir, ignore_errors=True)

    def _listening_pid(self):
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True).stdout
        for line in out.splitlines():
            if f":{self.port} " in line and "LISTENING" in line:
                return int(line.split()[-1])
        return None


# --------------------------------------------------------------------------- #
# the wire, read passively
# --------------------------------------------------------------------------- #
class Wire:
    """Every frame this page received, without touching the page.

    `page.on("websocket")` plus `framereceived` needs no init script and no
    page-side hook, which matters twice: patchright does not run init scripts
    at all (see the module docstring), and a hook installed in the page is a
    hook that can change what it is measuring.

    It is also the only way to answer "was that turn SILENT". The page keeps
    no audio -- `playChunk` posts samples into a worklet and never retains
    them -- so nothing in the DOM distinguishes a muted reply from a spoken
    one. A binary frame does.
    """

    def __init__(self, page):
        self.text: list[dict] = []
        self.binary = 0
        self.why = ""          # why the last send_typed gave up, if it did
        page.on("websocket", self._opened)

    def _opened(self, ws):
        ws.on("framereceived", self._frame)

    def _frame(self, payload):
        if isinstance(payload, (bytes, bytearray)):
            self.binary += 1
            return
        try:
            self.text.append(json.loads(payload))
        except Exception:                                  # noqa: BLE001
            pass

    def mark(self):
        return (len(self.text), self.binary)

    def since(self, mark, kind=None):
        rows = self.text[mark[0]:]
        return [m for m in rows if kind is None or m.get("type") == kind]

    def binary_since(self, mark):
        return self.binary - mark[1]

    def wait_for(self, kind, mark, page, timeout_s=30.0):
        end = time.time() + timeout_s
        while time.time() < end:
            hit = self.since(mark, kind)
            if hit:
                return hit[-1]
            page.wait_for_timeout(200)
        return None


class Asked:
    """Every request the PAGE itself made, without touching the page.

    `Wire` reads the socket; this reads the HTTP. It exists because a result
    on screen cannot say which request produced it: MEASURED 2026-09-11, a
    page that filtered locally left the old section L green while the
    daemon-side scope it was named after was gone. The REQUEST is the only
    observable that separates them -- section L now reads it to prove a birth
    is ONE `POST /api/session/new` and a project toggle writes nothing else.

    `page.on("request")` needs no init script (patchright runs none, see the
    module docstring) and sees what the page asked for rather than what this
    file asked for: `d.get(...)` goes out over urllib and never appears here,
    which is what lets the two stand side by side in one assertion.
    """

    def __init__(self, page):
        self.urls: list[tuple[str, str]] = []
        page.on("request", lambda r: self.urls.append((r.method, r.url)))

    def mark(self) -> int:
        return len(self.urls)

    def since(self, mark: int, needle: str = "/api/chats") -> list[str]:
        return [u for method, u in self.urls[mark:]
                if method == "GET" and needle in u]


def send_typed(page, wire, text, timeout_s=30.0):
    """Type into the composer, press send, wait for the turn to END.

    Waits for the `speak_end` FRAME rather than for a state chip to change
    words: the chip is copy and copy moves, and a turn that ends is a fact on
    the wire. An `error` frame ends the wait too, because a refused message
    never produces a `speak_end` and waiting the full timeout for one would
    turn every refusal into a slow, silent failure.
    """
    # WAIT UNTIL THE BUTTON IS ACTUALLY *SEND*. It is one control with two
    # jobs -- send, and stop while Butler is talking -- and the page stays in
    # its speaking state for a second or two AFTER `speak_end`, because it is
    # waiting for its own audio queue to drain. Clicking in that window STOPS
    # instead of sending, which is what made every second turn here silently
    # not happen and read as the feature under test being broken.
    #
    # That window is a real defect and it has its OWN scenario (F5). It is
    # waited out here so one defect does not contaminate the report of every
    # other scenario -- a suite in which one bug fails nine unrelated checks
    # is a suite nobody can read.
    def snap():
        return page.evaluate("""() => ({
          chip: (document.getElementById('stateChip')||{}).innerText,
          label: document.getElementById('sendBtn').getAttribute('aria-label'),
          disabled: document.getElementById('sendBtn').disabled})""")

    try:
        page.wait_for_function(
            "() => document.getElementById('sendBtn')"
            ".getAttribute('aria-label') === 'Send'", timeout=20000)
    except Exception:                                      # noqa: BLE001
        # A False with no reason is a bad instrument: it reads as "the
        # feature is broken" for every possible cause. Say WHICH wait ran
        # out and what the page looked like when it did.
        wire.why = f"the button never became Send: {snap()}"
        return False, wire.mark()
    mark = wire.mark()
    page.fill("#input", text)
    try:
        page.wait_for_function(
            "() => !document.getElementById('sendBtn').disabled", timeout=8000)
    except Exception:                                      # noqa: BLE001
        wire.why = f"send stayed disabled with text in the box: {snap()}"
        return False, mark
    page.click("#sendBtn")
    end = time.time() + timeout_s
    while time.time() < end:
        if wire.since(mark, "speak_end"):
            page.wait_for_timeout(700)
            wire.why = ""
            return True, mark
        if wire.since(mark, "error"):
            wire.why = f"the daemon answered with an error frame: "                        f"{wire.since(mark, 'error')[-1].get('message')!r}"
            page.wait_for_timeout(300)
            return False, mark
        page.wait_for_timeout(200)
    wire.why = (f"no speak_end in {timeout_s:.0f}s; frames seen were "
                f"{[m.get('type') for m in wire.since(mark)]}; page {snap()}")
    return False, mark


def turns(page):
    """(user text, butler text) per rendered transcript item.

    `#history > .histitem` is an ITEM and each one holds BOTH roles --
    `article.turn.user` and `article.turn.bot` -- which is worth stating
    because the obvious selector counts half as many messages as there are.
    `#workRow` is #history's last child since Step 3c (AC-694) and is not a
    turn.
    """
    return page.evaluate("""() => {
      const out = [];
      for (const item of document.querySelectorAll('#history > .histitem')) {
        const u = item.querySelector('.turn.user');
        const b = item.querySelector('.turn.bot .answered');
        out.push([(u ? u.innerText : '').trim().slice(0, 80),
                  (b ? b.innerText : '').trim().slice(0, 80)]);
      }
      return out;
    }""")


def page_error_text(page):
    """The USER-VISIBLE error text, off `#errStrip` under the header.

    `#errors` moved PHYSICALLY into the diagnostics fold, which is collapsed
    by default (`web/AGENTS.md` "THE CHAT-FIRST COMPOSER AND HEADER" phase
    2). Its own `hidden` attribute still clears on `showError`, but it now
    sits inside a `[hidden]` ancestor (`#diagPanel`), and `.innerText` is
    LAYOUT-based -- it returns "" for a node behind a `display:none`
    ancestor even though `.textContent` still holds the real message. That
    is exactly the "asserted on where errors appear" trap this file's own
    docstring warns a probe must follow when errors move. `#errStrip` is
    the one line a stranger actually sees without opening anything.
    """
    return page.evaluate("""() => {
      const s = document.getElementById('errStrip');
      const t = document.getElementById('errStripText');
      return (s && !s.hidden && t) ? (t.textContent || '').trim() : '';
    }""")


# --------------------------------------------------------------------------- #
# A -- first run: can somebody who has never used this do anything?
# --------------------------------------------------------------------------- #
def open_search(page):
    """The sidebar's Search row becomes the field when pressed (lantern-ui
    section 4); the field is the same `#chatSearch` and the matching the
    daemon's `?q=` (AC-188)."""
    if not page.is_visible("#chatSearch"):
        page.click("#searchRow")
        page.wait_for_timeout(200)


def section_a(page, box):
    print("\n=== A. FIRST RUN -- a new user, no microphone permission ===")
    errs = box["errors"]
    ok("A", "A1", "the page loads with no uncaught errors",
       not errs, f"{len(errs)} page error(s): {errs[:2]}")

    want = ["#composer", "#input", "#sendBtn", "#history", "#stateChip",
            "#micBtn", "#sidebar", "#chatList", "#newChat"]
    missing = [s for s in want if page.query_selector(s) is None]
    ok("A", "A2", "every control a first-time user needs is present",
       not missing, f"missing {missing}" if missing else f"all {len(want)}")

    # The greeting, then the suggestions and "Say hey jarvis, or type." under
    # the centred composer (AC-692): one empty state in two elements.
    text = " ".join(page.inner_text(sel).strip()
                    for sel in ("#histEmpty", "#emptyChips")
                    if page.query_selector(sel))
    ok("A", "A3", "the empty conversation explains what to do",
       len(text) > 20, f"empty state reads {text[:90]!r}")

    # SEND IS DISABLED WHILE THE BOX IS EMPTY, which is correct and is not the
    # thing being checked. The first version of this asserted both were
    # enabled at rest and reported a FAIL against a page behaving properly --
    # an instrument that manufactures a defect is worse than no instrument.
    # What matters is that typing makes it live with no microphone involved.
    before = page.evaluate("""() => ({
      input: document.getElementById('input').disabled,
      send: document.getElementById('sendBtn').disabled})""")
    page.fill("#input", "x")
    after = page.evaluate("""() => ({
      input: document.getElementById('input').disabled,
      send: document.getElementById('sendBtn').disabled})""")
    page.fill("#input", "")
    ok("A", "A4", "the composer works before the microphone ever does",
       not before["input"] and not after["send"],
       f"at rest {before}, after typing one character {after}")

    chip = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    ok("A", "A5", "the state chip says something a person can read",
       bool(chip), f"state chip reads {chip!r}")

    desc = page.eval_on_selector("#stateDesc", "e => e.innerText.trim()") \
        if page.query_selector("#stateDesc") else ""
    note("A", "A6", "what the page says it is doing at rest",
         f"chip {chip!r}, line under the composer {desc[:90]!r}")


# --------------------------------------------------------------------------- #
# B -- speech: the global switch versus one message
# --------------------------------------------------------------------------- #
def section_b(page, box):
    print("\n=== B. SPEECH -- the global switch vs playing ONE message ===")
    d, wire = box["daemon"], box["wire"]

    d.post("/api/settings", {"speak": True})
    page.wait_for_timeout(900)
    done, mark = send_typed(page, wire, "first question, speech on")
    loud = wire.binary_since(mark)
    ok("B", "B1", "with speech ON a turn produces audio",
       done and loud > 0,
       f"turn ended={done}, {loud} binary frame(s) of audio")

    d.post("/api/settings", {"speak": False})
    page.wait_for_timeout(900)
    before = len(turns(page))
    done, mark = send_typed(page, wire, "second question, speech off")
    quiet = wire.binary_since(mark)
    after = len(turns(page))
    ok("B", "B2", "with speech OFF the answer still appears and is silent",
       done and quiet == 0 and after > before,
       f"turn ended={done}, {quiet} audio frame(s), "
       f"transcript {before} -> {after} turns")

    # AC-183: the one control that asks for a piece of the audio back while
    # `speak` is off. A route that consulted that setting would make this
    # button dead exactly when it is wanted, and dead SILENTLY.
    status = page.evaluate("""async () => {
      const r = await fetch('/api/tts/say', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: 'replay check'})});
      return r.status;
    }""")
    ok("B", "B3", "while MUTED, replaying one message still works",
       status == 200, f"POST /api/tts/say answered {status} with speak=false")

    play = page.query_selector_all("#history .saybtn")
    ok("B", "B4", "every Butler answer carries a play control",
       len(play) >= after, f"{len(play)} play control(s) for {after} turn(s)")

    sides = page.evaluate("""() => {
      const r = {userPlay: 0, botRewind: 0, botPlay: 0, userRewind: 0};
      for (const t of document.querySelectorAll('#history .turn.user')) {
        if (t.querySelector('.saybtn')) r.userPlay++;
        if (t.querySelector('.rewindbtn')) r.userRewind++;
      }
      for (const t of document.querySelectorAll('#history .turn.bot')) {
        if (t.querySelector('.saybtn')) r.botPlay++;
        if (t.querySelector('.rewindbtn')) r.botRewind++;
      }
      return r;
    }""")
    ok("B", "B5", "play is on Butler's turns and rewind on the owner's, "
                  "never crossed",
       sides["userPlay"] == 0 and sides["botRewind"] == 0
       and sides["botPlay"] > 0 and sides["userRewind"] > 0,
       f"{sides}")

    d.post("/api/settings", {"speak": False})
    page.wait_for_timeout(1500)
    muted = page.eval_on_selector(
        "#speakBtn", "e => e.getAttribute('aria-checked') || ''")
    d.post("/api/settings", {"speak": True})
    page.wait_for_timeout(1500)
    unmuted = page.eval_on_selector(
        "#speakBtn", "e => e.getAttribute('aria-checked') || ''")
    ok("B", "B6", "the voice popover's Read replies aloud switch follows a "
                  "change made on another device",
       muted == "false" and unmuted == "true",
       f"muted={muted!r} vs unmuted={unmuted!r}")

    # The switch lives in the voice popover (lantern-ui AC-686): the pill's
    # chevron opens it.
    if page.eval_on_selector("#voicePop", "e => e.hidden"):
        page.click("#voiceChev")
        page.wait_for_timeout(300)
    page.click("#speakBtn")
    page.wait_for_timeout(1800)
    ok("B", "B7", "clicking the speaker button actually changes the setting",
       d.setting("speak") is False,
       f"speak is now {d.setting('speak')!r} after one click")
    d.post("/api/settings", {"speak": True})
    page.keyboard.press("Escape")


# --------------------------------------------------------------------------- #
# C -- attachments
# --------------------------------------------------------------------------- #
def section_c(page, box):
    print("\n=== C. ATTACHMENTS -- showing Butler a picture and a document ===")
    d, wire = box["daemon"], box["wire"]
    tmp = d.dir

    png = tmp / "kitchen.png"
    png.write_bytes(PNG)
    pdf = tmp / "notes.pdf"
    pdf.write_bytes(make_pdf(PDF_TEXT))
    junk = tmp / "mystery.bin"
    junk.write_bytes(b"\x01\x02\x03not a file type anyone knows" * 20)

    def chips():
        return page.evaluate(
            "() => [...document.querySelectorAll('#attached .attchip')]"
            ".map(c => (c.innerText||'').trim().replace(/\\s+/g,' '))")

    def attach(path):
        page.set_input_files("#attachInput", str(path))
        page.wait_for_timeout(3000)
        return chips()

    after_png = attach(png)
    ok("C", "C1", "attaching an image shows it as attached",
       len(after_png) == 1, f"chips after a PNG: {after_png}")

    after_pdf = attach(pdf)
    ok("C", "C2", "attaching a document shows it beside the image",
       len(after_pdf) == 2, f"chips after a PDF: {after_pdf}")

    store = d.state / "attachments"
    stored = sorted(p.name for p in store.glob("*")) if store.exists() else []
    ok("C", "C3", "the files are on disk where a coding agent can open them",
       len(stored) >= 2, f"{len(stored)} file(s) in state/attachments: "
                         f"{stored[:4]}")

    before_err = page_error_text(page)
    after_junk = attach(junk)
    err = page_error_text(page)
    ok("C", "C4", "a file Butler cannot identify is refused BY NAME on screen",
       len(after_junk) == 2 and err and err != before_err,
       f"chips stayed at {len(after_junk)}, the page said {err[:110]!r}")

    before = len(turns(page))
    done, _ = send_typed(page, wire, "what is in these two files?")
    left = len(chips())
    ok("C", "C5", "the next message consumes what was attached",
       done and left == 0 and len(turns(page)) > before,
       f"turn ended={done}, chips left after it: {left}")

    # The stated limit from `docs/attachments.md`: a message still needs TEXT.
    # Measured rather than asserted, because HOW it fails is the usability
    # question and silence is the bad answer.
    attach(png)
    page.fill("#input", "")
    page.wait_for_timeout(600)
    blocked = page.evaluate("""() => ({
      disabled: document.getElementById('sendBtn').disabled,
      hint: (document.getElementById('stateDesc')||{}).innerText || ''})""")
    page.click("#sendBtn", force=True)
    page.wait_for_timeout(3000)
    err = page_error_text(page)
    still = len(chips())
    ok("C", "C6", "an attachment with NO text is kept rather than lost",
       still == 1, f"chips kept={still}")
    ok("C", "C6b", "and the page SAYS why nothing was sent",
       bool(err) or not blocked["disabled"],
       f"send was disabled={blocked['disabled']} with a picture attached and "
       f"an empty box; the page said {err[:80]!r}; the line under the "
       f"composer reads {blocked['hint'][:60]!r}")

    # TAKING A CHIP OFF IS A PAGE-ONLY ACT and the file still rides on the
    # next turn -- `paintAttached`'s own comment says so ("Butler still has
    # the file"), because the file is already pending on the SESSION and
    # there is no route that could withdraw it. Checked as two separate
    # things, because the chip disappearing is the part a person sees and the
    # file arriving anyway is the part that surprises them.
    dropped = page.evaluate("""() => {
      const b = document.querySelector('#attached .attchip button');
      if (!b) return 'no drop control on the chip';
      b.click();
      return 'clicked ' + (b.getAttribute('aria-label') || b.innerText);
    }""")
    page.wait_for_timeout(1200)
    ok("C", "C7", "an attachment can be taken off the message before sending",
       len(chips()) == 0, f"{dropped}; chips now {chips()}")

    n_files = len(list(store.glob("*"))) if store.exists() else 0
    done, mark = send_typed(page, wire, "did anything ride along with that?")
    note("C", "C8", "what REMOVING a chip actually withdraws",
         f"the chip is gone from the page; the file is still on disk "
         f"({n_files} there) and is still pending on the session, so the next "
         f"message takes it. The page says so in a note and there is no route "
         f"that could withdraw it (docs/attachments.md)")

    # `#camBtn` is GONE (mocks/q-familiar.html item 1): the camera is now an
    # ITEM inside `#attachBtn`'s menu, beside "Upload a file". Existence in
    # the DOM proves nothing -- both items start `hidden` -- so this OPENS
    # the menu the way a phone owner would, checks the item is there and
    # labelled, and closes the menu again (Escape) without picking it, since
    # clicking "Take a photo" would ask this headless browser for a camera
    # it does not have.
    page.click("#attachBtn")
    page.wait_for_timeout(300)
    photo_item = page.evaluate("""() => {
      const cam = document.getElementById('attachPhoto');
      return !!cam && cam.offsetParent !== null
             && (cam.innerText || '').trim().length > 0;
    }""")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    ok("C", "C9", "the camera control is present for a phone, in the "
                  "attach menu",
       photo_item, f"#attachPhoto usable after opening #attachBtn's menu="
                   f"{photo_item}")


# --------------------------------------------------------------------------- #
# D -- what the settings surface says in each brain mode
# --------------------------------------------------------------------------- #
def section_d(page, box):
    print("\n=== D. BRAIN MODES -- what is live and what is decoration ===")
    d = box["daemon"]
    was = d.setting("brain_mode")

    seen = {}
    for mode in ("endpoint", "harness", "harness_only"):
        d.post("/api/settings", {"brain_mode": mode})
        rows = {r["key"]: r for r in d.get("/api/settings")["settings"]}
        seen[mode] = sorted(k for k, v in rows.items() if v["inactive"])
        note("D", f"D0:{mode}", f"in {mode} the daemon dims",
             f"{len(seen[mode])}: {seen[mode]}")

    ok("D", "D1", "the three modes dim different things",
       len({tuple(v) for v in seen.values()}) == 3,
       f"endpoint={len(seen['endpoint'])}, harness={len(seen['harness'])}, "
       f"harness_only={len(seen['harness_only'])}")

    dimmed = {m: ("document_reading" in v) for m, v in seen.items()}
    ok("D", "D2", "reading documents is live in EVERY mode and there is no "
                  "separate vision model (AC-815) -- an attachment works in "
                  "all three",
       not any(dimmed.values()) and "vision_model" not in rows,
       f"document_reading dimmed in {dimmed}; "
       f"vision_model served: {'vision_model' in rows}")

    d.post("/api/settings", {"brain_mode": was})
    ok("D", "D3", "this section put the daemon back the way it found it",
       d.setting("brain_mode") == was,
       f"brain_mode {was!r} -> {d.setting('brain_mode')!r}")

    restart = page.evaluate("""async () => {
      const r = await fetch('/api/settings');
      const d = await r.json();
      return d.settings.filter(s => s.restart).map(s => s.key);
    }""")
    note("D", "D4", "settings that need a restart to take effect",
         f"{restart}")


# --------------------------------------------------------------------------- #
# E -- living with conversations
# --------------------------------------------------------------------------- #
def section_e(page, box):
    print("\n=== E. CONVERSATIONS -- new, list, search, reopen, delete ===")
    d, wire = box["daemon"], box["wire"]

    marker = "pineapple lighthouse"
    send_typed(page, wire, f"remember the phrase {marker} for later")
    page.wait_for_timeout(1200)

    page.click("#newChat")
    page.wait_for_timeout(3000)
    ok("E", "E1", "New chat empties the transcript",
       len(turns(page)) == 0, f"{len(turns(page))} turn(s) after New chat")

    rows = page.evaluate(
        "() => [...document.querySelectorAll('#chatList .chatrow')]"
        ".map(r => (r.innerText||'').trim().replace(/\\s+/g,' ').slice(0,60))")
    ok("E", "E2", "the conversation just left is in the sidebar",
       len(rows) >= 1, f"{len(rows)} row(s): {rows[:3]}")

    open_search(page)
    page.fill("#chatSearch", marker)
    page.wait_for_timeout(3000)
    hits = page.evaluate(
        "() => [...document.querySelectorAll('#chatList .chatrow')]"
        ".map(r => (r.innerText||'').trim().replace(/\\s+/g,' ').slice(0,90))")
    ok("E", "E3", "searching finds a conversation by what was SAID in it",
       len(hits) >= 1 and any("pineapple" in h.lower() for h in hits),
       f"{len(hits)} hit(s) for {marker!r}: {hits[:2]}")
    page.fill("#chatSearch", "")
    page.wait_for_timeout(2000)

    # NOT the first row. After New chat the newest row IS the live, empty
    # conversation, so clicking it correctly renders nothing -- and the first
    # version of this reported that as "reopening a conversation is broken".
    # `aria-current` is how the page marks the one you are in.
    idx = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#chatList .chatrow')];
      return rows.findIndex(r => r.getAttribute('aria-current') !== 'true');
    }""")
    if idx < 0:
        skip("E", "E4", "clicking a PAST conversation reopens it",
             "every row in the sidebar is the live conversation")
        skip("E", "E4b", "sending continues the conversation that was opened",
             "there was no past conversation to open")
    else:
        was_live = d.get("/api/session/current").get("id")
        page.query_selector_all("#chatList .chatrow")[idx].click()
        page.wait_for_timeout(3000)
        ok("E", "E4", "clicking a PAST conversation reopens it",
           len(turns(page)) > 0,
           f"row {idx} (not the live one) rendered {len(turns(page))} turn(s)")

        # AC-240. Until phase 4 the page had to LEAVE a past conversation
        # before it could send, so this message would have landed in the
        # conversation the click walked away from. Asserted on the DAEMON's
        # own records and on BOTH of them: the opened one gained the message,
        # and the one that was live is untouched. A page that merely drew the
        # right transcript passes every DOM check ever written here.
        opened = d.get("/api/session/current").get("id")
        mark = "quince observatory"
        send_typed(page, wire, f"the follow-up about {mark}")
        page.wait_for_timeout(1500)
        landed = d.get("/api/chats/" + opened).get("messages", [])
        left = d.get("/api/chats/" + was_live).get("messages", []) \
            if was_live and was_live != opened else []
        ok("E", "E4b", "sending continues the conversation that was opened",
           opened != was_live
           and any(mark in (m.get("content") or "") for m in landed)
           and not any(mark in (m.get("content") or "") for m in left),
           f"opened {str(opened)[:22]}, was live {str(was_live)[:22]}, "
           f"{len(landed)} message(s) there / {len(left)} in the one left")

    # ASSERT THE ID IS GONE, not that the count fell. Deleting the LIVE
    # conversation ROTATES before it unlinks (daemon/AGENTS.md), so a fresh
    # empty record replaces it and the count is unchanged -- which is correct
    # behaviour and which the first version of this read as a failed delete.
    before_ids = {c["id"] for c in d.get("/api/chats").get("chats", [])}
    drop = page.query_selector("#chatList .chatdrop")
    if drop is None:
        skip("E", "E5", "a conversation can be deleted, in two deliberate "
                        "presses", "no .chatdrop control in the sidebar")
    else:
        drop.click()
        page.wait_for_timeout(900)
        armed = drop.inner_text().strip()
        drop.click()
        page.wait_for_timeout(3500)
        after_ids = {c["id"] for c in d.get("/api/chats").get("chats", [])}
        ok("E", "E5", "a conversation can be deleted, in two deliberate "
                      "presses",
           bool(before_ids - after_ids),
           f"the first press armed it ({armed!r}); "
           f"{len(before_ids - after_ids)} conversation(s) gone, "
           f"{len(after_ids - before_ids)} new, "
           f"{len(before_ids)} -> {len(after_ids)} listed")

    send_typed(page, wire, "a message that should survive a reload")
    page.wait_for_timeout(1200)
    before_reload = len(turns(page))
    page.reload(wait_until="load")
    page.wait_for_timeout(4000)
    after_reload = len(turns(page))
    ok("E", "E6", "a reload brings the live conversation back",
       after_reload >= 1,
       f"{before_reload} turn(s) before the reload, {after_reload} after")


# --------------------------------------------------------------------------- #
# F -- stopping
# --------------------------------------------------------------------------- #
def section_f(page, box):
    print("\n=== F. STOPPING -- the button, Escape, and what comes after ===")
    wire = box["wire"]

    page.wait_for_function(
        "() => document.getElementById('sendBtn')"
        ".getAttribute('aria-label') === 'Send'", timeout=20000)
    mark = wire.mark()
    page.fill("#input", "a question that is going to be stopped")
    page.click("#sendBtn")
    page.wait_for_timeout(500)
    mid = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    page.keyboard.press("Escape")
    page.wait_for_timeout(3000)
    after = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    ok("F", "F1", "Escape ends a turn that is in flight",
       after.lower() != mid.lower() or "think" not in after.lower(),
       f"state was {mid!r} at the press and {after!r} 3 s later")

    # THE GAP IS THE WHOLE OF IT. Found by this file on 2026-09-11 and
    # isolated the same day: a turn started within about three seconds of
    # stopping the previous one NEVER FINISHES. The `reply` frames arrive and
    # `speak_end` never does, so the page sits in its speaking state with the
    # Stop button showing, indefinitely -- the app is unusable until a reload
    # and nothing anywhere says why.
    #
    # MEASURED, and the isolation is what makes it actionable:
    #   gap 3 s, real Kokoro, harness brain -- hangs 2 of 2
    #   gap 3 s, real Kokoro, fake brain    -- hangs 2 of 2
    #   gap 6 s and 11 s, real Kokoro       -- fine, 3 of 3
    #   gap 3 s, FAKE speech engine         -- fine, 3 of 3
    # FIXED 2026-09-11 as D-39, and the isolation above pointed at the WRONG
    # organ -- kept here because the next reader will make the same read. Two
    # brains hanging and the fake speech engine not hanging said "Kokoro",
    # and the real variable was the AUDIO: `_await_playback` is bounded at the
    # audio already sent plus `PLAYBACK_GRACE_S`, and only a real engine sends
    # enough of it for that gate to outlast the 3 s `abandon()` waits for the
    # cancelled turn. The stopped turn's `finally` then re-armed the session
    # after the next turn had started, and `_rearm` took no turn id. Nothing
    # in the speech path was involved. The gap numbers are the window, not the
    # engine: 3 s lands inside it and 6 s does not.
    done, _ = send_typed(page, wire, "and the next question still works")
    ok("F", "F2", "a turn started three seconds after a stop still finishes",
       done, f"the turn after the stop ended={done}"
             + (f" -- {wire.why}" if not done else ""))

    # THE CONTROL NEEDED A RELOAD while F2 failed, and needing one was half
    # the finding: nothing inside the page cleared a wedged turn -- no
    # timeout, no error, no control -- so waiting longer in the same session
    # proved nothing. The first version of this waited 8 s and failed for that
    # reason, which read as the gap not mattering when what it actually showed
    # is that nothing recovered. It is KEPT with D-39 fixed, because it is
    # still the control that gives F2 its meaning: the long gap has to pass
    # for the short one to be evidence of a window.
    wedged = page.eval_on_selector(
        "#sendBtn", "e => e.getAttribute('aria-label')")
    page.reload(wait_until="load")
    page.wait_for_timeout(4000)
    recovered = page.eval_on_selector(
        "#sendBtn", "e => e.getAttribute('aria-label')")
    page.wait_for_timeout(6000)
    done_slow, _ = send_typed(page, wire, "and one asked after a longer pause")
    ok("F", "F2b", "a RELOAD recovers, and a longer gap is fine (the control)",
       done_slow,
       f"before the reload the button read {wedged!r}, after it {recovered!r}; "
       f"a turn with a six-second gap ended={done_slow}"
       + (f" -- {wire.why}" if not done_slow else "") +
       ". While F2 failed, this passing beside it is what said the window "
       "was real and that the only way out of it was a reload (D-39).")

    # FROM AN IDLE PAGE, and that is the whole rule rather than a fixture
    # convenience: Escape clears the draft only when there is nothing else for
    # it to mean. With a turn in flight it still STOPS, draft or no draft --
    # which is the half of the old argument that survived (app.js, the five
    # cases). The first version of this ran straight after F2, which leaves
    # the page wedged in its speaking state, so it was measuring case three
    # and reporting case five.
    try:
        page.wait_for_function(
            "() => document.getElementById('sendBtn')"
            ".getAttribute('aria-label') !== 'Stop Butler'", timeout=20000)
        idle = True
    except Exception:                                      # noqa: BLE001
        idle = False
    page.fill("#input", "text that Escape should clear")
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    left = page.eval_on_selector("#input", "e => e.value")
    if not idle:
        skip("F", "F3", "Escape clears the composer on an IDLE page",
             "the page never became idle -- F2 above wedged it, so this "
             "would have measured the stop case instead")
    else:
        ok("F", "F3", "Escape clears the composer on an IDLE page",
           left == "", f"the composer still holds {left!r}")

    # And the half that must NOT change: with a turn running, Escape stops
    # Butler even though there is a draft in the box. A fifth case that
    # outranked the stop would be the failure the old comment warned about.
    mark = wire.mark()
    page.wait_for_function(
        "() => document.getElementById('sendBtn')"
        ".getAttribute('aria-label') === 'Send'", timeout=20000)
    page.fill("#input", "a turn to interrupt")
    page.click("#sendBtn")
    wire.wait_for("speak_start", mark, page, timeout_s=25)
    page.fill("#input", "a draft that must NOT disarm the stop key")
    page.keyboard.press("Escape")
    page.wait_for_timeout(3000)
    chip = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    kept = page.eval_on_selector("#input", "e => e.value")
    ok("F", "F3b", "with a turn running, Escape still STOPS and keeps the "
                   "draft",
       chip.lower() != "speaking" and kept != "",
       f"state is {chip!r} after the press and the box still holds "
       f"{kept[:40]!r}")

    # THE ONE CONTROL, TWO JOBS conflict. Found by this file on 2026-09-11,
    # and it is the most common thing in it: Butler answers, the owner reads
    # the answer and types the next question while he is still talking, and
    # presses the button. It is labelled "Stop Butler" at that moment, so it
    # stops him -- their message is not sent, it is still sitting in the box,
    # and nothing anywhere says a send did not happen.
    #
    # Measured the same day: the page stays in its speaking state for a
    # second or two AFTER the daemon's `speak_end`, because it waits for its
    # own audio queue to drain. So the window is open every single turn.
    d = box["daemon"]
    d.post("/api/settings", {"speak": True})
    page.wait_for_timeout(800)
    mark = wire.mark()
    page.fill("#input", "a question asked while Butler is still talking")
    page.wait_for_function(
        "() => document.getElementById('sendBtn')"
        ".getAttribute('aria-label') === 'Send'", timeout=20000)
    page.click("#sendBtn")
    # Wait for him to START talking, then type over the top of it.
    wire.wait_for("speak_start", mark, page, timeout_s=25)
    page.wait_for_timeout(300)
    label_speaking = page.eval_on_selector(
        "#sendBtn", "e => e.getAttribute('aria-label')")
    mark2 = wire.mark()
    page.fill("#input", "and the follow-up I want to ask")
    page.wait_for_timeout(300)
    # Read AFTER typing: the label has to say what the press will DO, and what
    # a person sees is the label with their message already in the box.
    label_then = page.eval_on_selector(
        "#sendBtn", "e => e.getAttribute('aria-label')")
    page.click("#sendBtn")
    page.wait_for_timeout(4000)
    sent = wire.since(mark2, "final")
    left = page.eval_on_selector("#input", "e => e.value")
    said = page_error_text(page)
    ok("F", "F5", "typing while Butler is talking SENDS, rather than "
                  "silently stopping him",
       bool(sent),
       f"the button read {label_speaking!r} while he talked and "
       f"{label_then!r} once a message was typed; "
       f"{len(sent)} message(s) sent; the box still holds {left!r}; "
       f"the page said {said[:60]!r}")

    # MEASURED WHILE IT IS SHOWING. The stop glyph has zero size at rest --
    # it is the send button's other face -- so reading it between turns gives
    # 0x0 and says nothing, which is what the first version of this reported.
    mark = wire.mark()
    page.fill("#input", "a turn to look at the stop glyph during")
    page.wait_for_function(
        "() => document.getElementById('sendBtn')"
        ".getAttribute('aria-label') === 'Send'", timeout=20000)
    page.click("#sendBtn")
    wire.wait_for("speak_start", mark, page, timeout_s=25)
    page.wait_for_timeout(300)
    icon = page.evaluate("""() => {
      const s = document.getElementById('stopIcon');
      if (!s) return 'no #stopIcon';
      const r = s.getBoundingClientRect();
      if (!r.width) return 'the stop glyph is not being drawn right now';
      const parts = [...s.querySelectorAll('rect, path, circle, line')];
      const inner = parts.map(el => {
        const b = el.getBoundingClientRect();
        return {tag: el.tagName,
                size: [Math.round(b.width * 10) / 10,
                       Math.round(b.height * 10) / 10],
                offCentreX: Math.round(((r.left + r.width / 2)
                                        - (b.left + b.width / 2)) * 10) / 10,
                offCentreY: Math.round(((r.top + r.height / 2)
                                        - (b.top + b.height / 2)) * 10) / 10};
      });
      return {outer: [Math.round(r.width), Math.round(r.height)],
              viewBox: s.getAttribute('viewBox'), parts: inner};
    }""")
    note("F", "F4", "the stop glyph's geometry while it IS the stop control "
                    "(it was an OUTLINED rect and is now filled; the owner "
                    "read the stroke's two edges as a square in a square)",
         f"{icon}")


# --------------------------------------------------------------------------- #
# G -- the audio harness
# --------------------------------------------------------------------------- #
def section_g(daemon, playwright):
    print("\n=== G. THE MICROPHONE -- a wake word from a real audio device ===")
    browser = playwright.chromium.launch(
        headless=not HEADED, executable_path=CHROME,
        args=["--use-fake-ui-for-media-stream",
              "--use-fake-device-for-media-stream",
              f"--use-file-for-fake-audio-capture={WAKE_WAV}"])
    try:
        ctx = browser.new_context(ignore_https_errors=True,
                                  permissions=["microphone"],
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        wire = Wire(page)
        page.goto(daemon.url, wait_until="load")
        page.wait_for_timeout(3000)

        mic = page.query_selector_all("#micBtn[data-mic]")
        if len(mic) != 1:
            skip("G", "G1", "the wake word starts a turn from the microphone",
                 f"{len(mic)} mic controls matched #micBtn[data-mic]")
            return
        # THE ARM CONTROL IS `#stateChip`, NOT THE MIC BUTTON (mocks/
        # q-familiar.html item 2, `web/AGENTS.md` "THE CHAT-FIRST COMPOSER
        # AND HEADER"). This section used to click `#micBtn`, which is what
        # armed and disarmed listening before that phase; it DICTATES now
        # (`toggleDictation()`) -- a click opens its own mic graph and, worse
        # for this section, SKIPS `pumpWake` for as long as it runs, so
        # clicking it would have suppressed the very wake detection G1 is
        # named after. `#micBtn[data-mic]` is still checked above as a sanity
        # count (the composer rendered exactly one), but the arm/disarm act
        # moved to `#stateChip` alone.
        #
        # THE PAGE ARMS ITSELF AT BOOT when `stt_enabled` is on -- the owner's
        # ruling of 2026-09-10 (AC-179). So `#stateChip` is a TOGGLE that may
        # already read "Stop listening", and the first version of this
        # section clicked the (then arming) mic button unconditionally and
        # turned the microphone OFF, then reported that the wake word does
        # not work. MEASURED: with no click at all, six turns in thirty
        # seconds.
        #
        # `probe_page.py --live` used to call the mic-button click "arming
        # the microphone" and got away with it by racing the self-arm; that
        # comment and the click it described are both fixed there now.
        arm = page.query_selector("#stateChip")
        label = (arm.get_attribute("aria-label") or "") if arm else ""
        armed = "stop" in label.lower()
        if arm and not armed:
            arm.click()
            page.wait_for_timeout(2000)
        note("G", "G0", "how the page reaches the microphone",
             f"at load #stateChip reads {label!r}; "
             f"{'it had already armed itself' if armed else 'it was clicked'}")
        mark = wire.mark()
        page.wait_for_timeout(50_000)

        starts = wire.since(mark, "capture_start")
        finals = wire.since(mark, "final")
        heard = [f.get("text", "")[:40] for f in finals]
        ok("G", "G1", "the wake word starts a turn from the microphone",
           len(starts) >= 1, f"{len(starts)} capture(s) started in 50 s")
        ok("G", "G2", "the turn completes and the page re-arms for a SECOND "
                      "(D-1)",
           len(starts) >= 2, f"{len(starts)} capture(s), {len(finals)} "
                             f"final(s): {heard[:3]}")
        ok("G", "G3", "what was heard reaches the transcript",
           len(turns(page)) >= 1,
           f"{len(turns(page))} turn(s) rendered: {turns(page)[:2]}")
        ok("G", "G4", "no page errors while the microphone is live",
           not errs, f"{len(errs)} error(s): {errs[:2]}")
    finally:
        browser.close()


# --------------------------------------------------------------------------- #
# N -- two styles, one page (docs/lantern-ui.md Step 4)
# --------------------------------------------------------------------------- #
# Every element id on the page and the Tab order through it. Read at rest
# with no popover open, so both styles are measured in the same state.
SHAPE_JS = """() => {
  const ids = [...document.querySelectorAll('[id]')].map(e => e.id).sort();
  return ids;
}"""
HOMES_JS = """() => {
  const w = document.getElementById('wave'), ll = document.getElementById('liveLine');
  let px = 0;
  if (w.width > 1) { const d = w.getContext('2d').getImageData(0, 0, w.width, w.height).data;
    for (let i = 3; i < d.length; i += 4) if (d[i]) px++; }
  return {style: document.documentElement.dataset.style,
          state: document.documentElement.dataset.state,
          live: ll.parentNode.id, liveShown: getComputedStyle(ll).display !== 'none',
          wave: w.parentNode.id, seam: getComputedStyle(document.getElementById('waveSeam')).display,
          px: px, inState: getComputedStyle(document.getElementById('inState')).display,
          lives: document.querySelectorAll('#liveLine').length,
          waves: document.querySelectorAll('#wave').length};
}"""


def _tab_order(page, presses=40):
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); }")
    page.mouse.click(700, 5)
    seen = []
    for _ in range(presses):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("""() => { const e = document.activeElement;
          return e ? (e.id || e.tagName + ':' + (e.getAttribute('aria-label') || '')) : ''; }"""))
    return seen


def _set_style(page, d, style):
    d.post("/api/settings", {"ui_style": style})
    for _ in range(40):
        if page.evaluate("document.documentElement.dataset.style") == style:
            return True
        page.wait_for_timeout(125)
    return False


def section_n(daemon, playwright):
    print("\n=== N. TWO STYLES -- one page, Caption and Lantern ===")
    d = daemon
    browser = playwright.chromium.launch(
        headless=not HEADED, executable_path=CHROME,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
    try:
        page = browser.new_context(ignore_https_errors=True,
                                   viewport={"width": 1440, "height": 900}).new_page()
        page.goto(d.url, wait_until="load")
        page.wait_for_timeout(4000)
        _set_style(page, d, "lantern")
        page.wait_for_timeout(500)
        page.evaluate("() => { window.__n1 = 1; }")
        ids_a, tab_a = page.evaluate(SHAPE_JS), _tab_order(page)
        live = _set_style(page, d, "caption")
        page.wait_for_timeout(500)
        same_doc = page.evaluate("() => window.__n1 === 1")
        ok("N", "N1", "a style change applies live, with no reload (AC-678)",
           live and same_doc, f"data-style caption={live}, same document={same_doc}")
        ids_b, tab_b = page.evaluate(SHAPE_JS), _tab_order(page)
        ok("N", "N2", "the element ids are identical under both styles (AC-679)",
           ids_a == ids_b, f"{len(ids_a)} vs {len(ids_b)} ids; only Lantern: "
           f"{sorted(set(ids_a) - set(ids_b))[:5]}, only Caption: "
           f"{sorted(set(ids_b) - set(ids_a))[:5]}")
        ok("N", "N3", "the Tab order is identical under both styles (AC-679)",
           tab_a == tab_b and len(set(tab_a)) > 10,
           f"{len(set(tab_a))} stops; first difference: "
           f"{next(((i, x, y) for i, (x, y) in enumerate(zip(tab_a, tab_b)) if x != y), None)}")
        h = page.evaluate(HOMES_JS)
        ok("N", "N4", "under Caption the live words and the canvas sit in its slots "
                      "(AC-689, AC-691)",
           h["live"] == "captionSlot" and h["wave"] == "waveSeam"
           and h["lives"] == 1 and h["waves"] == 1, f"{h}")
        # The effect and the style both move the canvas through ONE function:
        # out to the ring under lattice and back to the seam, no residue.
        effect = d.setting("voice_effect")
        d.post("/api/settings", {"voice_effect": "lattice"})
        page.wait_for_timeout(800)
        lat = page.evaluate(HOMES_JS)
        d.post("/api/settings", {"voice_effect": effect})
        page.wait_for_timeout(800)
        back = page.evaluate(HOMES_JS)
        ok("N", "N5", "switching the effect moves the one canvas and back (AC-260)",
           lat["wave"] == "waveMic" and back["wave"] == "waveSeam" and back["waves"] == 1,
           f"lattice -> #{lat['wave']}, {effect} -> #{back['wave']}")
        _set_style(page, d, "lantern")
        page.wait_for_timeout(400)
        h = page.evaluate(HOMES_JS)
        residue = page.evaluate("() => document.getElementById('captionSlot')"
                                ".querySelectorAll('[id]').length")
        ok("N", "N6", "back to Lantern the words return to the box, nothing left "
                      "in the caption (AC-681)",
           h["live"] == "liveSlot" and residue == 0 and h["lives"] == 1,
           f"#{h['live']}, ids left in #captionSlot: {residue}")
    finally:
        browser.close()

    # A REAL capture under each style: the fake microphone plays the wake word.
    browser = playwright.chromium.launch(
        headless=not HEADED, executable_path=CHROME,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
              f"--use-file-for-fake-audio-capture={WAKE_WAV}"])
    try:
        page = browser.new_context(ignore_https_errors=True, permissions=["microphone"],
                                   viewport={"width": 1440, "height": 900}).new_page()
        page.goto(d.url, wait_until="load")
        page.wait_for_timeout(3000)
        for sid, style in (("N7", "caption"), ("N8", "lantern")):
            _set_style(page, d, style)
            got = None
            for _ in range(400):
                h = page.evaluate(HOMES_JS)
                if h["state"] == "capturing" and h["liveShown"]:
                    page.wait_for_timeout(300)
                    got = page.evaluate(HOMES_JS)
                    break
                page.wait_for_timeout(100)
            if got is None:
                skip("N", sid, f"{style}: the live words land where you look "
                               "during capture", "no capture within 40 s")
                continue
            if style == "caption":
                good = (got["live"] == "captionSlot" and got["seam"] == "block"
                        and got["wave"] == "waveSeam" and got["px"] > 0
                        and got["inState"] == "none")
            else:
                # Lantern's canvas sits in the seam it never shows, so the
                # visualiser must keep running for the border's glow: the
                # level has to MOVE (it froze when only the canvas counted).
                levels = page.evaluate("""async () => { const out = [];
                  for (let i = 0; i < 10; i++) {
                    out.push(document.getElementById('composerFx').style
                             .getPropertyValue('--fx-level'));
                    await new Promise(r => setTimeout(r, 100)); }
                  return out; }""")
                got["fxLevels"] = len(set(levels) - {""})
                good = (got["live"] == "liveSlot" and got["seam"] == "none"
                        and got["inState"] != "none" and got["fxLevels"] >= 3)
            ok("N", sid, f"{style}: during a real capture the live words land in "
                         f"its slot (AC-689){' and the seam draws (AC-691)' if style == 'caption' else ''}",
               good, f"{got}")
        _set_style(page, d, "lantern")
    finally:
        browser.close()


# --------------------------------------------------------------------------- #
# H -- automatic language
# --------------------------------------------------------------------------- #
def section_h(page, box):
    print("\n=== H. LANGUAGE -- the voice follows what the owner says ===")
    d, wire = box["daemon"], box["wire"]
    d.post("/api/settings", {"language": "auto", "voice": "", "speak": True})
    page.wait_for_timeout(1200)

    said = []
    for line in (EN_LINE, PT_LINE, "sim"):
        done, mark = send_typed(page, wire, line)
        starts = wire.since(mark, "speak_start")
        said.append((starts[-1].get("voice"), starts[-1].get("lang"))
                    if starts else None)

    ok("H", "H1", "an English question is answered in an English voice",
       said[0] and str(said[0][1] or "").lower().startswith("en"),
       f"the English turn announced {said[0]}")
    ok("H", "H2", "a Portuguese question is answered in a Portuguese voice",
       said[1] and str(said[1][1] or "").lower().startswith("pt"),
       f"the Portuguese turn announced {said[1]}")
    ok("H", "H3", "a one-word answer KEEPS the language (the latch)",
       said[2] and said[1] and said[2][1] == said[1][1],
       f"after {said[1]}, the word 'sim' announced {said[2]}")

    rows = {r["key"]: r for r in d.get("/api/settings")["settings"]}
    ok("H", "H4", "the Voice control says Butler is choosing",
       bool(rows["voice"]["inactive"]),
       f"voice inactive={rows['voice']['inactive']!r}")

    voices = page.evaluate("""async () => {
      const r = await fetch('/api/models?target=tts_voices');
      if (!r.ok) return 'HTTP ' + r.status;
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.models || d.values || []);
      return list.length + ' voices, first few: '
             + JSON.stringify(list.slice(0, 3));
    }""")
    note("H", "H5", "what the voice picker offers while Butler is choosing",
         f"{voices}")


# --------------------------------------------------------------------------- #
# I -- a phone
# --------------------------------------------------------------------------- #
def section_k(page, box):
    """The owner's backlog item 5: change what is answering without leaving
    the conversation.

    Every check here is about the PAGE, because the daemon half is already
    covered by pytest and by section D's dimming rows. What only a browser can
    say is whether the flag draws a control, whether the word on it came from
    the daemon, and whether pressing it writes -- a schema flag that renders
    nothing is this project's recurring failure (AC-7b).
    """
    print("\n=== K. MODEL PICKERS -- changing what answers, from the chat ===")
    d = box["daemon"]
    was = {k: d.setting(k) for k in
           ("model_favourites", "harness_tools", "endpoint_model",
            "brain_mode")}
    # Two invented ids. The scratch daemon's favourites list is empty by
    # default, and a picker tested against an empty list proves only that an
    # empty list draws nothing.
    favs = ["scratchconn/alpha-9b", "scratchconn/beta-4b"]
    d.post("/api/settings", {"model_favourites": favs})
    page.wait_for_timeout(900)

    quick = {s["key"]: s["quick"]
             for s in d.get("/api/settings")["settings"] if s["quick"]}
    # THE HOST MOVED AND THE MECHANISM DID NOT (D8, phase 1 step 6). `#pickRow`
    # -- a row of its own under the text field -- is DELETED; the same controls,
    # built by the same `settings.js` from the same per-setting `quick` flag,
    # live in a popover the composer's own model control opens. Every check
    # below is the check it was, one press further in.
    page.evaluate("""() => {
      const b = document.getElementById('modelBtn');
      const p = document.getElementById('modelPop');
      if (b && p && p.hidden) b.click();
    }""")
    page.wait_for_timeout(400)
    shown = page.evaluate("""() => {
      const row = document.getElementById('modelPop');
      if (!row) return null;
      return {
        hidden: row.hidden,
        wrapHidden: row.parentElement.hidden,
        word: (document.getElementById('agentBtnName') || {}).textContent || '',
        value: (document.getElementById('modelBtnName') || {}).textContent || '',
        names: [...row.querySelectorAll('.pickname')].map(e => e.textContent),
        segs: [...row.querySelectorAll('.pickseg')].map(
                 e => e.getAttribute('aria-label')),
        vals: [...row.querySelectorAll('.pickval')].map(e => e.textContent),
        dim: [...row.querySelectorAll('.pick, .pickseg')].map(
                 e => e.classList.contains('inactive')),
      };
    }""")
    if not ok("K", "K1", "the composer's own control opens what is answering",
              shown and not shown["hidden"] and not shown["wrapHidden"]
              and shown["word"].strip(),
              f"{shown}"):
        d.post("/api/settings", was)
        return

    # AC-41 again, on the words the COMPOSER prints (AC-688): the agent chip
    # carries the short word of the quick setting the daemon says answers,
    # the model chip its value -- the page picks neither.
    ok("K", "K1b", "the agent chip's word came from the daemon",
       shown["word"] in quick.values() and shown["value"].strip(),
       f"the chips read {shown['word']!r} / {shown['value']!r}; the daemon "
       f"marked {sorted(quick.values())}")

    ok("K", "K2", "it holds one control per setting the daemon marked quick",
       len(shown["names"]) + len(shown["segs"]) == len(quick),
       f"{len(shown['names'])} pills + {len(shown['segs'])} segments "
       f"against {len(quick)} marked: {sorted(quick)}")

    # AC-41 in the only form a browser can check it: every word printed on a
    # control is a string the daemon served. A page that shortened
    # "Conversation model" to "Brain" for itself would pass every other check
    # here and be the hardcoded list that drifts.
    ok("K", "K3", "every word on the row came from the daemon, not the page",
       set(shown["names"]) <= set(quick.values()),
       f"page shows {shown['names']}; daemon marked {sorted(quick.values())}")

    opened = page.evaluate("""async () => {
      const btn = document.querySelector('#modelPop .pickbtn');
      btn.click();
      await new Promise(r => setTimeout(r, 200));
      const pop = btn.parentElement.querySelector('.pickpop');
      return {open: !pop.hidden,
              expanded: btn.getAttribute('aria-expanded'),
              opts: [...pop.querySelectorAll('.pickopt')].map(
                       e => e.textContent),
              more: !!pop.querySelector('.pickmore')};
    }""")
    ok("K", "K4", "opening a picker offers the favourites and a way out",
       opened["open"] and opened["expanded"] == "true"
       and all(f in opened["opts"] for f in favs) and opened["more"],
       f"{opened}")

    # The popover hangs UPWARDS, because this row sits at the bottom of the
    # page. Measured rather than asserted from the stylesheet: a rule that is
    # overridden still reads correctly in the file.
    geom = page.evaluate("""() => {
      const pop = document.querySelector('#modelPop .pickpop:not([hidden])');
      if (!pop) return null;
      const b = pop.getBoundingClientRect();
      return {top: Math.round(b.top), bottom: Math.round(b.bottom),
              vh: window.innerHeight};
    }""")
    ok("K", "K5", "the popover opens on screen rather than off the bottom",
       geom and geom["top"] >= 0 and geom["bottom"] <= geom["vh"],
       f"{geom}")

    # Escape closes the popover and nothing else. It cannot reach the page's
    # global handler, whose last case stops a reply (AC-112) -- which is why
    # the listener is capture-phase on the popover itself.
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    ok("K", "K6", "Escape inside a picker closes it",
       page.evaluate(
           "() => !!document.querySelector('#modelPop .pickpop[hidden]')"),
       "popover hidden after Escape")

    picked = page.evaluate("""async (model) => {
      const btn = document.querySelector('#modelPop .pickbtn');
      btn.click();
      await new Promise(r => setTimeout(r, 200));
      const pop = btn.parentElement.querySelector('.pickpop');
      const opt = [...pop.querySelectorAll('.pickopt')]
        .find(e => e.textContent === model);
      if (!opt) return "the favourite was not offered";
      opt.click();
      await new Promise(r => setTimeout(r, 900));
      return document.querySelector('#modelPop .pickval').textContent;
    }""", favs[0])
    stored = d.setting("endpoint_model")
    ok("K", "K7", "choosing a favourite writes it and the pill shows it",
       isinstance(stored, dict) and stored.get("model") == favs[0]
       and picked == favs[0].split("/")[-1],
       f"daemon holds {stored!r}; pill reads {picked!r}")

    flipped = page.evaluate("""async () => {
      const seg = document.querySelector('#modelPop .pickseg');
      if (!seg) return "no segmented control";
      const off = [...seg.querySelectorAll('button')]
        .find(b => b.getAttribute('aria-pressed') === 'false');
      const words = [...seg.querySelectorAll('button')].map(b => b.textContent);
      off.click();
      await new Promise(r => setTimeout(r, 900));
      return {words, pressed: [...seg.querySelectorAll('button')]
        .filter(b => b.getAttribute('aria-pressed') === 'true').length};
    }""")
    ok("K", "K8", "the tool posture is named buttons and one press writes",
       isinstance(flipped, dict) and flipped["pressed"] == 1
       and d.setting("harness_tools") != was["harness_tools"],
       f"{flipped}; harness_tools {was['harness_tools']!r} -> "
       f"{d.setting('harness_tools')!r}")

    # The dimming, read off the PAGE rather than off the wire. Section D
    # asserts the daemon says which settings are inert; this asserts the row
    # acts on it, which is the half that was missing when `vision_model` was
    # live in the schema and dead on screen.
    d.post("/api/settings", {"brain_mode": "harness_only"})
    page.wait_for_timeout(900)
    dim = page.evaluate("""() => {
      const out = {};
      for (const p of document.querySelectorAll('#modelPop .pick')) {
        out[p.querySelector('.pickname').textContent] =
          p.classList.contains('inactive');
      }
      return out;
    }""")
    note("K", "K9", "which pickers harness_only dims", f"{dim}")
    ok("K", "K10", "a picker that decides nothing in this mode is dimmed",
       any(dim.values()),
       f"{dim} -- in harness_only the conversation model is inert")

    for key, value in was.items():
        d.post("/api/settings", {key: value})
    page.wait_for_timeout(600)
    ok("K", "K11", "this section put the daemon back the way it found it",
       all(d.setting(k) == v for k, v in was.items()),
       f"{ {k: d.setting(k) for k in was} }")

    # THE VOICE PILL'S CHEVRON AND ITS THREE SWITCHES (AC-686, CU-10). Each
    # press is read back off the DAEMON, then put back.
    voice_was = {k: d.setting(k) for k in ("speak", "ambient_seconds",
                                            "stt_enabled")}
    page.click("#voiceChev")
    page.wait_for_timeout(400)
    opened = page.evaluate("""() => ({
      open: !document.getElementById('voicePop').hidden,
      expanded: document.getElementById('voiceChev')
                  .getAttribute('aria-expanded'),
      body: document.getElementById('stateChip').getAttribute('aria-label')})""")
    ok("K", "K12", "the pill's chevron opens the voice popover; its body keeps "
                   "micAction's job",
       opened["open"] and opened["expanded"] == "true" and opened["body"],
       f"{opened}")
    page.click("#roomSw")
    page.wait_for_timeout(1200)
    room_on = d.setting("ambient_seconds")
    tag = page.evaluate("""() => ({
      tag: !document.getElementById('ambientChip').hidden,
      warn: !document.getElementById('roomWarn').hidden,
      checked: document.getElementById('roomSw').getAttribute('aria-checked')})""")
    ok("K", "K13", "Room memory writes ambient_seconds, and the header says "
                   "Recording room while the ring is up (AC-687)",
       isinstance(room_on, (int, float)) and room_on > 0
       and tag["checked"] == "true"
       and tag["tag"] == tag["warn"],
       f"ambient_seconds {voice_was['ambient_seconds']!r} -> {room_on!r}; "
       f"{tag} (the tag needs a live microphone, so it may be down here)")
    page.click("#speakBtn")
    page.wait_for_timeout(1200)
    ok("K", "K14", "Read replies aloud writes speak",
       d.setting("speak") != voice_was["speak"],
       f"speak {voice_was['speak']!r} -> {d.setting('speak')!r}")
    page.click("#wakeSw")
    page.wait_for_timeout(1500)
    ok("K", "K15", "Wake word writes stt_enabled",
       d.setting("stt_enabled") != voice_was["stt_enabled"],
       f"stt_enabled {voice_was['stt_enabled']!r} -> "
       f"{d.setting('stt_enabled')!r}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    for key, value in voice_was.items():
        d.post("/api/settings", {key: value})
    page.wait_for_timeout(1500)
    ok("K", "K16", "the voice checks put the daemon back the way they found it",
       all(d.setting(k) == v for k, v in voice_was.items())
       and page.eval_on_selector("#voicePop", "e => e.hidden"),
       f"{ {k: d.setting(k) for k in voice_was} }")


def section_l(page, box):
    """Projects, the tree, and a chat that BELONGS to its project
    (`docs/filing-and-pacing.md` 8, R-A..R-D, AC-287..AC-292).

    REWRITTEN 2026-09-26, the second time. It asserted a rail of TILES that
    FILTERED one flat list, an "All chats" tile, and a "work here" control
    that MOVED the live conversation into a project; all of that is gone. A
    project is now a node of the sidebar's TREE, a press on it only opens or
    shuts it, "+ New chat" births a conversation there in ONE call, and "work
    in X" / "go to X" NAVIGATES. What the old section protected and still
    applies is kept: every row reachable, its controls one press away and on
    screen, the request the page actually made, and nothing moving that the
    owner did not ask to move.
    """
    from daemon.settings import project_name

    print("\n=== L. PROJECTS -- the tree, birth, and navigation ===")
    d = box["daemon"]
    asked = box["asked"]
    # A real directory the scratch daemon owns. Never a path in the repository:
    # a conversation born here starts a coding agent here, and this suite must
    # not aim one at the checkout it is running from.
    here = str(d.dir)

    rows = d.get("/api/shortcuts")["rows"]
    ok("L", "L1", "Butler's own directory is a project with no setting up",
       any(r["path"].lower().endswith("butler") for r in rows),
       f"{[r['path'] for r in rows]}")

    d.post("/api/shortcuts",
           {"row": {"id": "", "path": here, "label": "Scratch"}})
    page.reload(wait_until="load")
    page.wait_for_timeout(3500)
    rows = d.get("/api/shortcuts")["rows"]
    scratch = next(r for r in rows if r["label"] == "Scratch")

    # EVERY ROW IS A NODE OF THE TREE, each with its "...", and nothing on the
    # page offers "All chats" any more (R-D overturns R4).
    nodes = page.evaluate("""() => ({
      ids: [...document.querySelectorAll('#chatList .pnode[data-project-id]')]
             .map(n => ({id: n.dataset.projectId,
                         more: !!n.querySelector('.phead .tilemore')})),
      all: /all chats/i.test(document.getElementById('sidebar').innerText),
    })""")
    ok("L", "L1b", "every project row is a node of the tree, with no "
                   "'All chats'",
       [n["id"] for n in nodes["ids"]] == [r["id"] for r in rows]
       and all(n["more"] for n in nodes["ids"]) and not nodes["all"],
       f"{len(nodes['ids'])} nodes for {len(rows)} rows; "
       f"'All chats' present={nodes['all']}")

    # A PROJECT'S OWN CONTROLS, one press behind its "...". Same class names as
    # the pane they came from. `button.here` -- the old "work here" MOVE --
    # must be ABSENT: a chat's project is fixed at birth (R-A).
    def controls(row_id):
        return page.evaluate("""async (id) => {
          const node = document.querySelector(
            '#chatList .pnode[data-project-id="' + CSS.escape(id) + '"]');
          if (!node) return "no node for " + id;
          node.querySelector('.phead .tilemore').click();
          await new Promise(r => setTimeout(r, 500));
          const p = document.getElementById('projPop');
          const row = p.querySelector('.shortrow');
          if (!row) return "the popover drew no controls";
          const b = p.getBoundingClientRect();
          const out = {
            name: (row.querySelector('.who b') || {}).textContent || '',
            here: !!row.querySelector('button.here'),
            agents: row.querySelectorAll('button.agentbtn').length,
            edit: !!row.querySelector('button.edit'),
            drop: !!row.querySelector('button.drop'),
            chords: !!row.querySelector('#showChords'),
            onscreen: b.x >= 0 && b.y >= 0 && b.right <= window.innerWidth
                      && b.bottom <= window.innerHeight,
          };
          document.body.dispatchEvent(new PointerEvent('pointerdown',
                                                       {bubbles: true}));
          await new Promise(r => setTimeout(r, 200));
          return out;
        }""", row_id)

    # "remove" is withheld on the ROOT row only (e2e audit 2026-09-26: it was
    # offered there and then refused); every other row still carries it.
    root_id = d.get("/api/shortcuts").get("root_id")
    seen = [controls(r["id"]) for r in rows]
    full = [x for x in seen if isinstance(x, dict)]
    ok("L", "L2", "every project's controls are one press behind its '...', "
                  "on screen, with NO move control, and no remove on the root",
       len(full) == len(rows)
       and all(isinstance(x, dict) and x["agents"] == 3 and x["edit"]
               and x["drop"] == (r["id"] != root_id) and x["chords"]
               and x["onscreen"] and not x["here"]
               for r, x in zip(rows, seen)),
       f"{len(full)} of {len(rows)} popovers drew the full set: {seen[:2]}")

    # A PRESS ON A PROJECT LINE ONLY OPENS OR SHUTS IT (R1, amended). Nothing
    # is opened, nothing is born, and the one write is the expanded set.
    live_before = d.get("/api/session/current").get("id")
    mark = asked.mark()
    toggled = page.evaluate("""async (id) => {
      const q = () => document.querySelector(
        '#chatList .pnode[data-project-id="' + CSS.escape(id) + '"] .ptoggle');
      const was = q().getAttribute('aria-expanded');
      q().click();
      await new Promise(r => setTimeout(r, 1200));
      return {was, now: q().getAttribute('aria-expanded'),
              title: document.getElementById('chatTitle').textContent};
    }""", scratch["id"])
    wrote = [(m, u) for m, u in asked.urls[mark:] if m != "GET"]
    live_after = d.get("/api/session/current").get("id")
    stored = d.get("/api/shortcuts")["expanded_projects"]
    ok("L", "L3", "a press on a project line only toggles it, and writes "
                  "only the expanded set",
       toggled["was"] != toggled["now"] and live_after == live_before
       and all(u.endswith("/api/shortcuts") for _m, u in wrote)
       and ((scratch["id"] in stored) == (toggled["now"] == "true")),
       f"{toggled}; non-GET requests {wrote}; live chat "
       f"{str(live_before)[:22]} -> {str(live_after)[:22]}; stored {stored}")

    # "+ NEW CHAT" BIRTHS THE CONVERSATION THERE, IN ONE CALL (AC-287). The
    # request is read off the page's own traffic, not inferred from the result.
    mark = asked.mark()
    born = page.evaluate("""async (id) => {
      const node = () => document.querySelector(
        '#chatList .pnode[data-project-id="' + CSS.escape(id) + '"]');
      if (node().querySelector('.ptoggle').getAttribute('aria-expanded')
          !== 'true') {
        node().querySelector('.ptoggle').click();
        await new Promise(r => setTimeout(r, 900));
      }
      node().querySelector('.pnew').click();
      await new Promise(r => setTimeout(r, 3000));
      const cur = node().querySelector('.chatrow[aria-current="true"]');
      return {inNode: !!cur,
              header: document.getElementById('chatProject').textContent};
    }""", scratch["id"])
    posts = [u for m, u in asked.urls[mark:] if m == "POST"]
    current = d.get("/api/session/current")
    ok("L", "L4", "'+ New chat' on a project births the conversation there, "
                  "in one call",
       current.get("project") == here
       and sum(u.endswith("/api/session/new") for u in posts) == 1
       and not any(u.endswith("/api/project") for u in posts),
       f"the record says {current.get('project')!r}, wanted {here!r}; "
       f"the page POSTed {posts}")
    ok("L", "L5", "the new conversation is marked inside its project's node, "
                  "and the header names the project",
       born["inNode"] and born["header"] == "Scratch", f"{born}")

    # THE MOVE IS GONE (AC-288): the route answers no longer, and the record
    # is untouched.
    refused = page.evaluate("""async () => {
      const r = await fetch('/api/project', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: 's-anything'})});
      return r.status;
    }""")
    after = d.get("/api/session/current")
    ok("L", "L6", "the move route is gone, and nothing moved",
       refused in (404, 405) and after.get("project") == here,
       f"HTTP {refused}; the record still says {after.get('project')!r}")

    # THE EXPANDED SET IS THE DAEMON'S: shut a project, reload, still shut.
    butler_row = next(r for r in rows if r["path"].lower().endswith("butler"))
    page.evaluate("""async (id) => {
      const t = document.querySelector(
        '#chatList .pnode[data-project-id="' + CSS.escape(id) + '"] .ptoggle');
      if (t.getAttribute('aria-expanded') === 'true') t.click();
      await new Promise(r => setTimeout(r, 1200));
    }""", butler_row["id"])
    page.reload(wait_until="load")
    page.wait_for_timeout(3500)
    shut = page.evaluate("""(id) => document.querySelector(
      '#chatList .pnode[data-project-id="' + CSS.escape(id) + '"] .ptoggle')
      .getAttribute('aria-expanded')""", butler_row["id"])
    ok("L", "L7", "a shut project stays shut across a reload (the daemon "
                  "holds it)",
       shut == "false", f"after reload aria-expanded={shut!r}")

    # SEARCH IS GLOBAL, and a result names its project.
    send_typed(page, box["wire"], "the scratch marker is walnut harbour")
    open_search(page)
    page.fill("#chatSearch", "walnut harbour")
    page.wait_for_timeout(2500)
    found = page.evaluate("""() => [...document.querySelectorAll(
      '#chatList .chatrow')].map(r => (r.querySelector('.proj') || {})
      .textContent || '')""")
    ok("L", "L8", "a search is global and each result names its project",
       found and all(found) and "Scratch" in found,
       f"{len(found)} result(s), projects {found}")
    page.fill("#chatSearch", "")
    page.wait_for_timeout(1500)

    # "GO TO X" NAVIGATES (AC-290): typed here, which is the same `_on_final`
    # a spoken one reaches. It opens the project's newest conversation -- the
    # one just born in Scratch -- or births one; it moves no record, and the
    # page ends up SHOWING it because the daemon said so (`opened`).
    home = d.post("/api/session/new", {"project": butler_row["id"]})
    page.reload(wait_until="load")
    page.wait_for_timeout(3500)
    listed = d.get("/api/chats").get("chats", [])
    before = {c["id"]: c["project"] for c in listed}
    # An EMPTY chat is listed only while it is current (it is never on disk),
    # so leaving `home` legitimately drops it from the list. That is not a
    # re-filing; any OTHER disappearance, or any changed project, is.
    empty = {c["id"] for c in listed if not c.get("turns")}
    spoken = project_name(here)
    send_typed(page, box["wire"], f"go to project {spoken}")
    page.wait_for_timeout(2500)
    now = d.get("/api/session/current")
    after = {c["id"]: c["project"]
             for c in d.get("/api/chats").get("chats", [])}
    showing = page.evaluate("""() => ({
      header: document.getElementById('chatProject').textContent,
      current: (document.querySelector('#chatList .chatrow[aria-current]')
                || {dataset: {}}).dataset.chatId || ''})""")
    ok("L", "L9", "'go to project X' opens X's newest conversation and the "
                  "page shows it",
       now.get("project") == here and now.get("id") != home.get("id")
       and showing["current"] == now.get("id")
       and showing["header"] == "Scratch",
       f"daemon on {str(now.get('id'))[:22]} in {now.get('project')!r}; "
       f"page shows {showing}")
    ok("L", "L10", "and no conversation's project changed",
       all(after[k] == v for k, v in before.items() if k in after)
       and set(before) - set(after) <= empty,
       f"{sum(k in after and after[k] != v for k, v in before.items())} "
       f"re-filed; gone from the list: {sorted(set(before) - set(after))} "
       f"(allowed only if empty: {sorted(empty)})")


def section_m(page, box):
    """The owner's second-batch item 2: a sidebar they can widen.

    Explicitly at 1280. Section I leaves the viewport at 390 and the sections
    after it inherit that -- and the handle is deliberately hidden below 900,
    where the sidebar is a fixed drawer whose width is the viewport's.
    """
    print("\n=== M. A SIDEBAR THE OWNER CAN WIDEN ===")
    d = box["daemon"]
    page.set_viewport_size({"width": 1280, "height": 860})
    page.wait_for_timeout(900)
    was = d.get("/api/shortcuts")["sidebar_width"]

    shown = page.evaluate("""() => {
      const h = document.getElementById('sbResize');
      if (!h) return null;
      const b = h.getBoundingClientRect();
      return {w: Math.round(b.width), h: Math.round(b.height),
              role: h.getAttribute('role'), tab: h.tabIndex,
              label: h.getAttribute('aria-label'),
              // THE LIST'S width, which is what `sidebar_width` stores: the
              // sidebar is the 56px strip PLUS it (docs/filing-and-pacing.md
              // 8). Comparing the whole box to the stored number is what hid
              // the drag growing it by the strip on every release.
              sidebar: Math.round(document.querySelector('.sb-main')
                                  .getBoundingClientRect().width)};
    }""")
    ok("M", "M1", "the sidebar has a grab handle a keyboard can reach",
       shown and shown["w"] >= 4 and shown["h"] > 200
       and shown["role"] == "separator" and shown["tab"] == 0
       and shown["label"],
       f"{shown}")

    # DRAGGED with real pointer events, because that is the only thing that
    # exercises the capture and the commit-on-release.
    box_ = page.eval_on_selector("#sbResize", """e => {
      const b = e.getBoundingClientRect();
      return {x: b.x + b.width / 2, y: b.y + b.height / 2};
    }""")
    page.mouse.move(box_["x"], box_["y"])
    page.mouse.down()
    page.mouse.move(box_["x"] + 120, box_["y"], steps=8)
    page.mouse.up()
    page.wait_for_timeout(1200)
    after = page.eval_on_selector(
        ".sb-main", "e => Math.round(e.getBoundingClientRect().width)")
    stored = d.get("/api/shortcuts")["sidebar_width"]
    ok("M", "M2", "dragging the handle widens the sidebar and the daemon "
                  "keeps the number",
       after > shown["sidebar"] + 40 and abs(stored - after) <= 2,
       f"{shown['sidebar']}px -> {after}px on screen, daemon holds {stored}")

    # A RELOAD is the point of storing it at all.
    page.reload(wait_until="load")
    page.wait_for_timeout(3000)
    page.set_viewport_size({"width": 1280, "height": 860})
    page.wait_for_timeout(600)
    back = page.eval_on_selector(
        ".sb-main", "e => Math.round(e.getBoundingClientRect().width)")
    ok("M", "M3", "the width survives a reload",
       abs(back - stored) <= 2, f"{stored} stored, {back} after reload")

    # THE BOUND. A drag to the far right must stop somewhere sane rather than
    # leaving no page beside it.
    page.mouse.move(*[page.eval_on_selector("#sbResize", """e => {
      const b = e.getBoundingClientRect(); return b.x + b.width / 2; }"""),
      430])
    page.mouse.down()
    page.mouse.move(1270, 430, steps=10)
    page.mouse.up()
    page.wait_for_timeout(1200)
    wide = d.get("/api/shortcuts")["sidebar_width"]
    ok("M", "M4", "a drag to the edge is clamped, not obeyed",
       wide <= 624, f"the daemon stored {wide}px")

    # COLLAPSING STILL NARROWS IT. The width is an inline custom property on
    # <html>, and an inline one beats any stylesheet rule -- so the first
    # version of this feature silently broke the collapse button: the sidebar
    # kept whatever width had been dragged and only its CONTENTS hid. Found by
    # a review agent, twice, including against the shipped commit. `#burger`,
    # in the header, is the ONE toggle now -- `#sbCollapse` and the strip it
    # lived in are gone (docs/sidebar-collapse.md, AC-293).
    page.evaluate("() => document.getElementById('burger').click()")
    page.wait_for_timeout(1200)
    shrunk = page.evaluate("""() => ({
      w: Math.round(
           document.getElementById('sidebar').getBoundingClientRect().width),
      state: document.documentElement.dataset.sidebar})""")
    page.evaluate("() => document.getElementById('burger').click()")
    page.wait_for_timeout(1200)
    grown = page.eval_on_selector(
        "#sidebar", "e => Math.round(e.getBoundingClientRect().width)")
    ok("M", "M5", "collapsing still narrows it, whatever width was dragged",
       shrunk["state"] == "collapsed" and shrunk["w"] < 100 and grown > 200,
       f"collapsed to {shrunk['w']}px (state {shrunk['state']!r}), "
       f"expanded back to {grown}px")

    d.post("/api/shortcuts", {"sidebar_width": was})
    page.wait_for_timeout(400)
    ok("M", "M6", "this section put the daemon back the way it found it",
       d.get("/api/shortcuts")["sidebar_width"] == was,
       f"{was} -> {d.get('/api/shortcuts')['sidebar_width']}")


def section_i(page, box):
    print("\n=== I. A PHONE -- 390x844 ===")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(2000)

    m = page.evaluate("""() => {
      const r = (id) => {
        const e = document.getElementById(id);
        if (!e) return null;
        const b = e.getBoundingClientRect();
        return {top: Math.round(b.top), bottom: Math.round(b.bottom),
                w: Math.round(b.width), h: Math.round(b.height),
                shown: !!(b.width && b.height)};
      };
      return {input: r('input'), send: r('sendBtn'), mic: r('micBtn'),
              vh: window.innerHeight, vw: window.innerWidth,
              scrollW: document.documentElement.scrollWidth};
    }""")
    off = [k for k in ("input", "send", "mic")
           if not m[k] or not m[k]["shown"] or m[k]["bottom"] > m["vh"]
           or m[k]["top"] < 0]
    ok("I", "I1", "the composer, send and mic are all on screen on a phone",
       not off, f"off screen or missing: {off or 'none'}; "
                f"viewport {m['vw']}x{m['vh']}")

    ok("I", "I2", "nothing forces a horizontal scroll",
       m["scrollW"] <= m["vw"],
       f"scrollWidth {m['scrollW']} vs viewport {m['vw']}")

    small = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('#composer button')) {
        const b = el.getBoundingClientRect();
        if (b.width && b.height && (b.height < 40 || b.width < 40))
          out.push([el.id || el.className, Math.round(b.width),
                    Math.round(b.height)]);
      }
      return out;
    }""")
    ok("I", "I3", "every composer button meets a 40px touch target",
       not small, f"{len(small)} under 40px: {small[:5]}")

    # THE STRIP IS GONE AND THE CONVERSATION GETS ITS 56px BACK
    # (docs/sidebar-collapse.md, AC-293, AC-298, 2026-09-26). This used to ask
    # whether the RAIL beside the list stayed on screen; there is no rail now,
    # `#sidebar` is 0px on a phone, and `.sb-main` -- the drawer -- is the
    # whole sidebar, anchored to the viewport's own left edge.
    #
    # START FROM A KNOWN STATE. `data-drawer` is a gesture and dies with the
    # page, but this run has been driving the page for a while.
    if page.eval_on_selector(
            ".sb-main", "e => e.getBoundingClientRect().left >= 0"):
        closer = page.query_selector("#sbScrim") or page.query_selector("#sbClose")
        if closer:
            closer.click(force=True)
            page.wait_for_timeout(1200)

    shut = page.evaluate("""() => {
      const col = document.querySelector('.col');
      const b = col.getBoundingClientRect();
      return {x: Math.round(b.x), w: Math.round(b.width)};
    }""")
    ok("I", "I5", "the conversation is full width with the list SHUT",
       shut["x"] == 0 and shut["w"] >= 380,
       f"conversation column {shut} at 390px -- the strip that used to cost "
       f"56px of permanent chrome here is gone")

    burger = page.query_selector("#burger")
    if burger is None:
        skip("I", "I4", "the conversation list can be opened on a phone",
             "#burger is not in the markup")
    else:
        try:
            burger.click(timeout=8000)
            page.wait_for_timeout(1500)
            shown = page.evaluate("""() => {
              const m = document.querySelector('.sb-main');
              const mb = m.getBoundingClientRect();
              const at = document.elementFromPoint(mb.x + mb.width / 2,
                                                   mb.y + 200);
              return {listLeft: Math.round(mb.x), listW: Math.round(mb.width),
                      onTop: !!(at && m.contains(at))};
            }""")
            ok("I", "I4", "the conversation list opens from the LEFT EDGE",
               shown["listLeft"] == 0 and shown["listW"] > 0 and shown["onTop"],
               f"list at {shown['listLeft']}px, {shown['listW']}px wide, "
               f"list hit-testable={shown['onTop']}")
            closer = page.query_selector("#sbScrim")
            if closer:
                closer.click(force=True)
                page.wait_for_timeout(1000)
        except Exception as e:                             # noqa: BLE001
            ok("I", "I4", "the sidebar can be opened on a phone",
               False, f"tapping the burger failed: {type(e).__name__}")

    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(800)


# --------------------------------------------------------------------------- #
# J -- the daemon goes away. LAST, because it kills it.
# --------------------------------------------------------------------------- #
def section_j(page, box):
    print("\n=== J. THE DAEMON GOES AWAY (AC-7b, AC-91) ===")
    before = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    box["daemon"].stop()
    page.wait_for_timeout(15_000)
    after = page.eval_on_selector("#stateChip", "e => e.innerText.trim()")
    said = page_error_text(page)
    ok("J", "J1", "with the daemon stopped, the page SAYS so",
       after != before or bool(said),
       f"state {before!r} -> {after!r}; the page said {said[:90]!r}")

    live = page.evaluate(
        "() => !document.getElementById('sendBtn').disabled")
    ok("J", "J2", "send does not stay live against a daemon that is gone",
       not live, f"#sendBtn still enabled={live}")


# --------------------------------------------------------------------------- #
def main() -> int:
    from patchright.sync_api import sync_playwright

    daemon = Scratch()
    print(f"scratch state: {daemon.state}")
    daemon.start()
    stopped = False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not HEADED, executable_path=CHROME,
                args=["--use-fake-ui-for-media-stream",
                      "--use-fake-device-for-media-stream"])
            ctx = browser.new_context(ignore_https_errors=True,
                                      viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            wire = Wire(page)
            asked = Asked(page)
            page.goto(daemon.url, wait_until="load")
            page.wait_for_timeout(4000)

            box = {"daemon": daemon, "errors": errors, "wire": wire,
                   "asked": asked}
            plan = [("A", section_a), ("B", section_b), ("C", section_c),
                    ("D", section_d), ("E", section_e), ("F", section_f),
                    ("H", section_h), ("I", section_i), ("K", section_k),
                    ("L", section_l), ("M", section_m)]
            for name, fn in plan:
                if ONLY and name not in ONLY:
                    continue
                try:
                    fn(page, box)
                except Exception as e:                     # noqa: BLE001
                    record(name, f"{name}!", "the section itself blew up",
                           "FAIL", f"{type(e).__name__}: {e}")
            browser.close()

            if not ONLY or "G" in ONLY:
                try:
                    section_g(daemon, p)
                except Exception as e:                     # noqa: BLE001
                    record("G", "G!", "the microphone section blew up",
                           "FAIL", f"{type(e).__name__}: {e}")

            if not ONLY or "N" in ONLY:
                try:
                    section_n(daemon, p)
                except Exception as e:                     # noqa: BLE001
                    record("N", "N!", "the styles section blew up",
                           "FAIL", f"{type(e).__name__}: {e}")

            if not ONLY or "J" in ONLY:
                b2 = p.chromium.launch(headless=not HEADED,
                                       executable_path=CHROME)
                c2 = b2.new_context(ignore_https_errors=True)
                pg2 = c2.new_page()
                pg2.goto(daemon.url, wait_until="load")
                pg2.wait_for_timeout(4000)
                try:
                    section_j(pg2, box)
                    stopped = True
                except Exception as e:                     # noqa: BLE001
                    record("J", "J!", "the outage section blew up", "FAIL",
                           f"{type(e).__name__}: {e}")
                b2.close()
    finally:
        if not stopped:
            daemon.stop()

    print("\n" + "=" * 74)
    fails = [r for r in ROWS if r[3] == "FAIL"]
    skips = [r for r in ROWS if r[3] == "SKIP"]
    passes = [r for r in ROWS if r[3] == "PASS"]
    notes = [r for r in ROWS if r[3] == "NOTE"]
    print(f"{len(passes)} pass, {len(fails)} FAIL, {len(skips)} skip, "
          f"{len(notes)} measurement(s) -- {len(ROWS)} checks")
    if fails:
        print("\nWHAT DOES NOT WORK")
        for _, sid, story, _, observed in fails:
            print(f"  {sid:<7} {story}\n          {observed}")
    if skips:
        print("\nWHAT COULD NOT BE TESTED")
        for _, sid, story, _, observed in skips:
            print(f"  {sid:<7} {story}\n          {observed}")
    if notes:
        print("\nMEASUREMENTS (no verdict attached)")
        for _, sid, story, _, observed in notes:
            print(f"  {sid:<7} {story}\n          {observed}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
