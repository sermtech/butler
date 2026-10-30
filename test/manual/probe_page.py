"""Load the satellite page in a real browser and report what it actually did.

Written because the settings panel was reported as MISSING, and reading the
markup is not evidence: an ES module that throws takes the whole page with
it, so a broken import looks like "fewer controls than before" rather than
like an error.

Usage:  python -X utf8 test/manual/probe_page.py [url] [--live] [--turn]
                                                 [--write] [--typed]

Default mode reports console messages, page errors, failed requests, and
which of the expected elements are in the DOM, and it drives the settings
sheet's own navigation -- the categories, the signal-path strip, the search
box and the phone back stack. It grants no
microphone permission and CHANGES NOTHING ON THE DAEMON, which is what makes
it safe to point at a Butler that is running for real.

UPDATED 2026-09-10 for the reshape that made the conversation the page
(docs/familiar-ui.md step 5, AC-172). What it asserted before that day it
still asserts; what moved is where the element lives. The one behaviour
change worth naming outside a diff is the `--live` click -- see the comment
there, which used to explain a five-treatment mic that no longer exists.

--write adds the checks that change something and put it back: it edits a
restart-flagged setting from its own control to prove the restart bar counts
it, presses the other style card to prove it applies live, and it collapses the sidebar to prove the state is the DAEMON's and
survives a reload (AC-166). Both restore what they found.

--typed sends a REAL typed turn from the composer (AC-162, AC-163) and reports
what the transcript did with it. It is not in the default run and cannot be: a
typed turn appends to the daemon's conversation record and runs the brain,
which is a change no diagnostic may make to a Butler somebody is using.

IT USED TO IMPLY --live AND DELIBERATELY NO LONGER DOES (AC-175, AC-176,
`docs/mic-optional.md`). The reason it did was real and was the defect:
`connect()` was the last line of start(), so the socket did not open until the
microphone did, and a typed message had nowhere to go. Run WITHOUT --live it
is now the acceptance evidence for the decoupling -- a whole turn, sent and
answered and rendered, on a page that has never touched a microphone -- and
running it WITH --live would hide the regression it exists to catch.

--live grants a FAKE microphone and arms the page. It no longer decides
whether the daemon is reachable: the socket is opened at boot, so the engine
chips, the mode, the turn mode and the restored conversation are all filled in
the DEFAULT run. What still needs it is anything that requires the page to be
capturing -- `--turn`, and the states only a live microphone can reach.

--turn (implies --live) plays `test/assets/wake_then_command.wav` into
Chrome's fake microphone and reports what the transcript ended up saying. It
drives the page's OWN wake detector, socket and renderer -- nothing is
stubbed on the page side -- because reply RENDERING is exactly what the
pytest suite cannot see: the suite asserts on the wire, and the wire being
right is what let a page that displayed only the LAST sentence of every
multi-sentence answer look healthy for as long as replies were one sentence.

Mocking the WebSocket instead was tried first and does not work here:
patchright strips the init-script mechanism Playwright's `route_web_socket`
relies on, so the route never fires and the page talks to the real daemon
anyway. Measured, not assumed -- the handler printed nothing while
`page.on("websocket")` showed the socket opening normally.

Point --turn at a daemon running the FAKE brain: it answers in two
sentences, which is the case that matters.
"""
import ssl
import sys
from pathlib import Path

from patchright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
WAKE_WAV = REPO / "test" / "assets" / "wake_then_command.wav"

# The daemon's certificate is SELF-SIGNED (scripts/make-cert.py), so the two
# places this probe talks to the API directly have to accept it, exactly as
# the browser context does. Built once here rather than per call site so the
# two cannot drift into one working and one not.
_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
TURN = "--turn" in sys.argv
# A typed turn is a real turn: it appends to the daemon's chat record and runs
# the brain. Behind its own flag for the reason --write is.
#
# IT NO LONGER IMPLIES --live, AND THAT REVERSAL IS THE POINT (AC-175, AC-176,
# `docs/mic-optional.md`). It used to, because "the composer sends over the
# socket and the socket does not open until the microphone does" -- which was
# true, and was the defect: with the microphone off the app could not be used
# at all. `connect()` runs at boot now, so `--typed` ALONE is the acceptance
# evidence for the decoupling, and pairing it with `--live` would hide exactly
# the regression it exists to catch.
TYPED = "--typed" in sys.argv
LIVE = "--live" in sys.argv or TURN
# The commit-bar check WRITES a setting and puts it back. Everything else the
# probe does is read-only, and that is what makes it safe to point at the
# owner's own daemon to answer "did the page load". Gating the one write
# behind a flag keeps that true; leaving it in the default run quietly made
# `probe_page.py <owner's url>` a thing that edits their configuration, which
# is the rule in AGENTS.md widening under everyone's feet.
WRITE = "--write" in sys.argv
URL = ARGS[0] if ARGS else "http://127.0.0.1:8140/"
# How long to let the fake microphone run before reading the transcript. The
# clip loops, so the wake fires again and again -- which is the point: one
# turn proves the reply renders, and only SEVERAL prove the page re-arms
# between them (D-1, AC-2). Override with --wait=<seconds>.
TURN_WAIT_MS = next((int(float(a.split("=")[1]) * 1000)
                     for a in sys.argv if a.startswith("--wait=")), 60_000)
# What --typed sends. Deliberately says what it is, because it lands in a real
# conversation record that somebody may read back later.
TYPED_TEXT = "probe_page typed-turn check, please answer in one short sentence"
# How long to let the daemon answer it. Shorter than TURN_WAIT_MS: there is no
# looping clip to wait out, just one round trip through the brain.
TYPED_WAIT_MS = next((int(float(a.split("=")[1]) * 1000)
                      for a in sys.argv if a.startswith("--typed-wait=")),
                     45_000)

def _arm_if_needed(page) -> None:
    """Click `ARM` only if the page is not already armed.

    Shared by every post-reload re-arm in this file, for the reason the
    `--live` block above states in full: the mic button dictates now and
    must never be the thing clicked here, and clicking `ARM` while it
    already reads "Stop listening" would turn the wake word OFF.
    """
    one = page.query_selector(ARM)
    if one:
        label = one.get_attribute("aria-label") or ""
        if "stop" not in label.lower():
            one.click()
            page.wait_for_timeout(3000)


# The one mic control (docs/familiar-ui.md 6.4). `#micWrap` and four of the
# five treatments are gone; this one kept `data-mic`, so a selector on the
# attribute still finds it and finds nothing else.
#
# ITS JOB CHANGED (mocks/q-familiar.html item 2, `web/AGENTS.md` "THE
# CHAT-FIRST COMPOSER AND HEADER"). This button DICTATES into the composer
# now (`toggleDictation()`, ex-`#waveBtn`'s job); it does not arm the wake
# word and its `aria-label` no longer says "listening". Arming is `ARM`
# below.
MIC = "#micBtn[data-mic]"
# The ONLY control that arms or disarms the wake word since the mic button's
# job changed. `micAction()` (app.js) drives this element's `aria-label`
# between "Start listening" / "Stop listening" and nothing else's.
ARM = "#stateChip"

WANT = [
    ("#settingsRoot", "settings sheet's page column"),
    ("#navRoot", "settings sheet's category list (AC-700)"),
    ("#modeRoot", "permission mode block"),
    ("#committed", "live transcript"),
    ("#stateKicker", "session state"),
    ("#wave", "visualiser canvas"),
    (MIC, "dictate into the composer (AC-181)"),
    ("#panel", "settings surface"),
    # Added with the 2026-09-10 reshape. Each is the root of something that
    # has nowhere else to be reported: a missing #history is a page with no
    # conversation on it, and reading the markup would not say so, because an
    # ES module that throws takes the whole page down and looks the same.
    ("#sidebar", "the sidebar"),
    ("#chatList", "past conversations list"),
    ("#history", "the conversation"),
    ("#histEmpty", "the empty state (AC-170)"),
    ("#emptyChips", "the empty state's example chips (AC-170)"),
    ("#composer", "the composer (AC-162)"),
    ("#input", "the message box"),
    ("#sendBtn", "send, which is also stop"),
    (ARM, "the state in a word, and the ONLY control that arms the wake "
          "word (AC-168, AC-180)"),
    ("#diagPanel", "the diagnostics readouts, in the Diagnostics sheet "
                   "(AC-707)"),
    # The composer's other controls, the chat search, and the header
    # controls the redesign moved out of the composer. Listed for the reason
    # the mic control is: an ES module that throws takes the whole page with
    # it, so a missing control reads as "fewer buttons than before" rather
    # than as an error -- and a renamed id is a feature leaving the room
    # quietly, which is what the dead `#micWrap` selector cost and what the
    # dead `#waveBtn`/`#camBtn` selectors would have cost here.
    ("#attachBtn", "attach a file or take a photo, a menu trigger (AC-205)"),
    ("#attachMenu", "the attach/camera menu (AC-205)"),
    ("#attachUpload", "upload a file, inside the attach menu (AC-205)"),
    ("#attachPhoto", "take a photo, inside the attach menu (AC-205)"),
    ("#speakBtn", "spoken replies on or off (AC-182), the voice popover's "
                  "switch (AC-686)"),
    ("#modelBtn", "the model chip in the composer's row (AC-688)"),
    ("#agentBtn", "the agent chip in the composer's row (AC-688)"),
    ("#attached", "what is attached (AC-205)"),
    ("#chatSearch", "search past conversations (AC-188)"),
    ("#errStrip", "the error summary strip (mocks/q-familiar.html item 3)"),
    # docs/filing-and-pacing.md 8 (AC-172: the probes move with the markup).
    # `#groupBtn`, the rail's tiles and "All chats" went with the flat list;
    # the tree's own controls are built per project by app.js, so the two
    # fixed ones are listed here. Not clicked: a project press writes the
    # expanded set, and this file's default run is read-only.
    ("#projAdd", "add a project, at the head of the project tree (AC-291)"),
    ("#chatProject", "the header's project name (AC-292)"),
    # docs/lantern-ui.md Step 3b (AC-711: the probes move with the markup).
    # Section 2's homes for what left the header: the voice pill's chevron and
    # its popover, the panel toggle, the account row and menu, the sheets.
    ("#voiceChev", "the voice pill's chevron (AC-686, CU-10)"),
    ("#voicePop", "the voice popover (AC-686)"),
    ("#wakeSw", "the Wake word switch (AC-686)"),
    ("#roomSw", "the Room memory switch (AC-686)"),
    ("#panelBtn", "the one panel toggle (AC-699)"),
    ("#acctBtn", "the account row (AC-686)"),
    ("#acctPop", "the account menu (AC-686)"),
    ("#searchRow", "the sidebar's Search row (AC-188)"),
    ("#diagSheet", "the Diagnostics sheet (AC-707)"),
    ("#keysSheet", "the Keyboard shortcuts sheet (AC-708)"),
    # docs/lantern-ui.md Step 3c (AC-711): the composer and the conversation.
    ("#liveLine", "the live words, inside the box (AC-689)"),
    # docs/lantern-ui.md Step 4: Caption's two slots exist under BOTH styles
    # (AC-679: one DOM; a style hides, never adds).
    ("#captionSlot", "Caption's caption above the box (AC-689)"),
    ("#waveSeam", "Caption's seam on the box's top edge (AC-691)"),
    ("#inState", "the state's word inside the box (lantern-ui section 4)"),
    ("#stopWord", "the word Stop beside the stop square"),
    ("#askCard", "the question card, docked on the composer (AC-693)"),
    ("#workRow", "the working row, the conversation's last item (AC-694)"),
    ("#stateDesc", "the working row's words, painted by paint() (CU-12)"),
    ("#workStop", "the working row's Stop (AC-694)"),
    ("#orbLabel", "the voice pill's accessible description (CU-12)"),
    ("#greeting", "the empty state's greeting (AC-692)"),
    ("#errStripDetails", "the error line's Details (AC-696)"),
]


def _open_settings(page) -> None:
    """The gear sits on the account row (AC-686); on a collapsed desk or a
    phone the sidebar has to be opened first (CU-9: two presses). A phone's
    shut drawer is translated off screen, which `is_visible` does not see,
    so the gear's box is read instead."""
    on_screen = page.evaluate("""() => {
      const b = document.getElementById("settingsBtn").getBoundingClientRect();
      return b.width > 0 && b.right > 0 && b.left < window.innerWidth;
    }""")
    if not on_screen:
        page.click("#burger")
        page.wait_for_timeout(600)
    page.click("#settingsBtn")


def _open_menu_item(page, item: str) -> None:
    """A row of the account menu, opened the way the owner does: the account
    row's chevron, then the row. Opening writes nothing."""
    if not page.is_visible("#acctBtn"):
        page.click("#burger")
        page.wait_for_timeout(600)
    if page.query_selector("#acctPop[hidden]"):
        page.click("#acctBtn")
        page.wait_for_timeout(250)
    page.click(item)


def _open_search(page) -> None:
    """The Search row becomes the field when pressed (lantern-ui section 4)."""
    if not page.is_visible("#chatSearch"):
        page.click("#searchRow")
        page.wait_for_timeout(200)


def _end_turns_early(url: str) -> None:
    """Lower the absolute turn ceiling on the daemon under probe.

    The looping fake microphone never falls silent, so the end-of-turn
    policies never fire and the turn would run to the 120 s safety net. The
    ceiling IS a real terminal path (D-1), so ending on it exercises the same
    reply and re-arm the room would.

    This writes a setting, which is why --turn belongs against a scratch
    daemon with its own BUTLER_STATE and never against the owner's on 8140.
    """
    import json as _json
    import urllib.request

    body = _json.dumps({"absolute_max_turn_ms": 10000}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/settings", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10,
                                context=_TLS) as response:
        print(f"  --turn: absolute ceiling lowered "
              f"({response.status}), so the looping clip still ends a turn")


def main() -> int:
    console, errors, failed = [], [], []
    # EVERY VERDICT GOES THROUGH ONE FUNCTION, AND THE EXIT CODE ANSWERS FOR
    # ALL OF THEM. Until 2026-09-11 the `OK `/`BAD` lines were bare prints and
    # the exit code was `errors or missing` only -- so fifteen checks added
    # with the project rail (the rail's own measurements, the sticky footer,
    # the `elementFromPoint` reachability test, the hover-help gate, Escape,
    # and the AC-229 scoping assertion) could all print BAD in a run that
    # exited 0. MEASURED: a run printing `BAD harness section top=97` exited 0.
    # The rule in AGENTS.md is that a review agent reads the exit code and
    # never the output text; that rule needs the exit code to mean something.
    bad: list[str] = []

    def verdict(good, text: str) -> bool:
        """Print one OK/BAD line, remember the BAD ones, answer the condition.

        The text is the CLAIM being made, in the present tense, so the summary
        at the end reads as a list of things that are not true rather than as
        a list of check names nobody can interpret without the log.
        """
        if not good:
            bad.append(text)
        print(f"  {'OK ' if good else 'BAD'} {text}")
        return bool(good)

    if TURN:
        _end_turns_early(URL)
    with sync_playwright() as p:
        # The real installed Chrome, not a downloaded test build. The
        # download fails on this machine, and using the actual browser the
        # owner runs is the more faithful test anyway -- it is the one whose
        # module loader, AudioContext and CSS support decide whether this
        # page works.
        browser = p.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            # A synthesised mic, not the owner's real one: the probe must not
            # open the microphone on a desk machine that is running Butler for
            # real. The fake device answers getUserMedia with a tone, which is
            # all start() needs to reach connect().
            args=(["--use-fake-ui-for-media-stream",
                   "--use-fake-device-for-media-stream"]
                  # Chrome repeats the clip forever, which is what makes the
                  # wake reachable at all: the mic does not open until the
                  # page has loaded and the control is clicked, and a
                  # single-shot clip (%noloop) has finished playing by then --
                  # measured, one wake at score 0.817 with the loop and none
                  # at all without it. The cost is that the microphone never
                  # goes quiet, so the turn cannot end on silence; the probe
                  # lowers the absolute ceiling instead (see _end_turns_early).
                  + ([f"--use-file-for-fake-audio-capture={WAKE_WAV}"]
                     if TURN else [])
                  if LIVE else []))
        # The daemon serves https on a SELF-SIGNED certificate whenever
        # ssl/cert.pem exists, which is the default since TLS landed -- a
        # browser refuses the microphone off localhost without it. Chromium
        # would stop at the interstitial and this probe would report a page
        # with none of its elements, which reads exactly like the broken
        # module it was written to catch (D-22's shape, from a new cause).
        # Accepting the cert is right here and nowhere else: the probe is a
        # manual tool pointed at a daemon the operator names on the command
        # line.
        ctx = browser.new_context(
            ignore_https_errors=True,
            **({"permissions": ["microphone"]} if LIVE else {}))
        page = ctx.new_page()
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed",
                lambda r: failed.append((r.url, r.failure)))

        page.goto(URL, wait_until="load")
        page.wait_for_timeout(2500)

        if LIVE:
            # ARMING, THROUGH THE HEADER PILL -- NOT THE MIC BUTTON. Until the
            # chat-first composer landed (2026-09-16) this clicked `MIC`
            # (`#micBtn`), which is what used to arm and disarm listening. It
            # DICTATES now (`toggleDictation()`): a click opens its own mic
            # graph, records into the composer, and -- the part that would
            # have silently broken `--turn` -- SKIPS `pumpWake` for as long as
            # it runs (`web/AGENTS.md` "THE WAVE BUTTON IS ... R3"), so
            # clicking it here would have suppressed the very wake detection
            # this flag exists to drive. `ARM` (`#stateChip`) is the ONLY
            # control that still arms the wake word (AC-180).
            #
            # CLICK ONLY IF NOT ALREADY ARMED. The page ARMS ITSELF at boot
            # when `stt_enabled` is on (AC-179), so by the time this runs the
            # pill may already read "Stop listening" -- clicking it again
            # would turn listening OFF and `--turn` would see no wakes at
            # all. `test/manual/e2e_scenarios.py` section G hit exactly this
            # (with the old `MIC` click) and fixed it by reading the label
            # first; the same fix belongs here.
            buttons = page.query_selector_all(ARM)
            print(f"  --live: {len(buttons)} arm control(s) matching {ARM}")
            if len(buttons) == 1:
                label = buttons[0].get_attribute("aria-label") or ""
                armed = "stop" in label.lower()
                print(f"  --live: {ARM} reads {label!r} "
                      f"({'already armed' if armed else 'arming it now'})")
                if not armed:
                    buttons[0].click()
                    page.wait_for_timeout(3000)
            elif not buttons:
                print("  WARNING: --live found NO arm control -- the header "
                      "did not render, so nothing below sees the daemon")
            else:
                print(f"  WARNING: {len(buttons)} arm controls; there is "
                      f"meant to be exactly one")
                buttons[0].click()
                page.wait_for_timeout(3000)

        print("=== page errors ===")
        for e in errors:
            print("  ERROR:", e)
        if not errors:
            print("  (none)")

        print("=== failed requests ===")
        for url, why in failed:
            print("  FAILED:", url, "->", why)
        if not failed:
            print("  (none)")

        print("=== console ===")
        for kind, text in console[:25]:
            print(f"  [{kind}] {text}")
        if not console:
            print("  (none)")

        print("=== expected elements ===")
        missing = []
        for sel, name in WANT:
            found = page.query_selector(sel)
            if not found:
                missing.append(sel)
            kids = page.eval_on_selector(
                sel, "el => el.children.length") if found else 0
            print(f"  {'OK ' if found else 'MISSING'} {sel:<18} "
                  f"({name}) children={kids}")

        controls = page.eval_on_selector_all(
            "#settingsRoot select, #settingsRoot input, "
            "#settingsRoot button", "els => els.length")
        print(f"  settings controls rendered: {controls}")

        # The engine chips are filled from the `ready` frame, so their TEXT is
        # the only proof the handshake carried all three infos and the page
        # read them. Presence in the DOM proves nothing here -- the markup
        # ships them as "-" (AC-42).
        #
        # The whole HUD moved INSIDE the `diagnostics` fold with the reshape,
        # and that fold is collapsed by default. The fold is opened first and
        # `text_content` is used rather than `inner_text`, because the latter
        # is rendered text and answers "" for anything not being drawn -- an
        # empty chip and a hidden one would read identically, which is the
        # measurement defect this probe exists to catch in the page. The
        # readouts live in the Diagnostics SHEET now (AC-707), opened from the
        # account menu; opening it writes nothing anywhere, so the default
        # run stays read-only. Closed again with Escape, which a sheet takes
        # in its own capture phase and never as a stop (CU-15).
        print("=== engine chips (filled by `ready`) ===")
        if page.query_selector("#diagPanel[hidden]"):
            _open_menu_item(page, "#diagBtn")
            page.wait_for_timeout(300)
        print(f"  diagnostics sheet open: "
              f"{page.eval_on_selector('#diagSheet', 'e => e.open')}, "
              f"readouts shown: "
              f"{page.eval_on_selector('#diagPanel', 'e => !e.hidden')}")
        for hud_id, label in (("hStt", "stt"), ("hTts", "tts"),
                              ("hBrain", "brain")):
            el = page.query_selector("#" + hud_id)
            text = (el.text_content() or "").strip() if el else "<missing element>"
            state = "OK " if text and text != "-" else "EMPTY"
            print(f"  {state} {label:<6} #{hud_id} = {text!r}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        verdict(not page.eval_on_selector("#diagSheet", "e => e.open"),
                "Escape closes the Diagnostics sheet (its own capture-phase "
                "listener, CU-15)")

        # ------------------------------------------------------------------ #
        # THE LANTERN SHELL (docs/lantern-ui.md Step 3b, AC-683..AC-688,
        # AC-699, AC-707, AC-708). READ-ONLY: every press below opens or shuts
        # a popover or a sheet, and none of them writes -- the switches inside
        # the voice popover are counted and read, never pressed. Section 2's
        # table is the list: each control that left the header has a home
        # that is present AND hit-testable where it is drawn.
        # ------------------------------------------------------------------ #
        print("=== the Lantern shell (AC-683..AC-688, AC-699, AC-707) ===")
        HIT = """(id) => {
          const n = document.getElementById(id);
          if (!n) return false;
          const q = n.getBoundingClientRect();
          if (!q.width || !q.height) return false;
          const at = document.elementFromPoint(q.x + q.width / 2,
                                               q.y + q.height / 2);
          return !!(at && (at === n || n.contains(at)));
        }"""
        head = page.evaluate("""() => {
          const h = document.querySelector('header');
          const pop = document.getElementById('voicePop');
          return [...h.querySelectorAll('button')]
            .filter(b => !pop.contains(b) && b.getBoundingClientRect().width)
            .map(b => b.id);
        }""")
        verdict(head == ["burger", "stateChip", "voiceChev", "panelBtn"],
                f"the header holds the panel icon, the pill (two parts) and "
                f"the panel toggle, and nothing else (AC-683): {head}")
        page.click("#voiceChev")
        page.wait_for_timeout(300)
        voice = page.evaluate("""(hit) => {
          const f = eval(hit);
          return {open: !document.getElementById('voicePop').hidden,
                  switches: ['wakeSw', 'speakBtn', 'roomSw'].filter(f),
                  roles: [...document.querySelectorAll('#voicePop [role=switch]')]
                           .map(b => b.getAttribute('aria-checked'))};
        }""", HIT)
        verdict(voice["open"] and len(voice["switches"]) == 3
                and all(r in ("true", "false") for r in voice["roles"]),
                f"the chevron opens the voice popover with its three switches "
                f"hit-testable (AC-686): {voice}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        verdict(page.eval_on_selector("#voicePop", "e => e.hidden"),
                "Escape closes the voice popover")
        if not page.is_visible("#acctBtn"):
            page.click("#burger")
            page.wait_for_timeout(600)
        page.click("#acctBtn")
        page.wait_for_timeout(300)
        menu = page.evaluate("""(hit) => {
          const f = eval(hit);
          return ['acctSettings', 'tasksPageBtn', 'keysBtn', 'diagBtn',
                  'restartBtn'].filter(f).concat(
            document.querySelectorAll('#acctAppear button').length
              ? ['appearance x' + document.querySelectorAll(
                  '#acctAppear button').length] : []);
        }""", HIT)
        verdict(len(menu) == 6,
                f"the account menu offers Settings, Appearance, Tasks and "
                f"automations, Keyboard shortcuts, Diagnostics and Restart "
                f"(AC-686): {menu}")
        page.click("#keysBtn")
        page.wait_for_timeout(1200)
        keys = page.evaluate("""() => ({
          open: document.getElementById('keysSheet').open,
          fixed: document.querySelectorAll('#keysSheet .keylist > div').length,
          projects: (document.getElementById('keysProjects').textContent
                     || '').trim().length > 0})""")
        verdict(keys["open"] and keys["fixed"] >= 6 and keys["projects"],
                f"Keyboard shortcuts lists this page's keys and the daemon's "
                f"project chords (AC-708): {keys}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        page.click("#modelBtn")
        page.wait_for_timeout(300)
        chips = page.evaluate("""() => ({
          open: !document.getElementById('modelPop').hidden,
          inComposer: !!document.querySelector('#composer #agentBtn')
                      && !!document.querySelector('#composer #modelBtn'),
          pickers: document.querySelectorAll(
            '#modelPop .pick, #modelPop .pickseg').length})""")
        verdict(chips["open"] and chips["inComposer"] and chips["pickers"] > 0,
                f"the agent and model chips sit in the composer and open the "
                f"quick pickers (AC-688): {chips}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # ------------------------------------------------------------------ #
        # THE CONVERSATION (AC-161, AC-168, AC-170)
        #
        # New with the 2026-09-10 reshape, and it goes here rather than in
        # probe_ux.py's per-criterion table because it is the same question
        # this probe has always asked: did the page actually come up. The
        # transcript is the centre of the view now, so "no turns and no empty
        # state" is what a broken page looks like from the outside.
        # ------------------------------------------------------------------ #
        print("=== the conversation ===")
        convo = page.evaluate("""() => {
          const items = [...document.querySelectorAll('#history .histitem')];
          const e = document.getElementById('histEmpty');
          const chips = document.getElementById('emptyChips');
          return {
            turns: items.length,
            emptyShown: !!e && e.offsetParent !== null,
            chipsShown: !!chips && chips.offsetParent !== null,
            // NEGATED, because `.saybtn` (AC-183), `.rewindbtn` (AC-194)
            // and `.retrybtn` (mocks/q-familiar.html item 4) all REUSE
            // `.copybtn`'s treatment on purpose -- a bare selector counts
            // four controls per bot turn (five with rewind's turn) and
            // reads as a transcript that has grown copy buttons. This used
            // to exclude only saybtn/rewindbtn, which is why the retry
            // button's arrival silently inflated this count by one per bot
            // turn until it was named here too.
            copyButtons: document.querySelectorAll(
              '#history .copybtn:not(.saybtn):not(.rewindbtn)'
              + ':not(.retrybtn)').length,
            sayButtons: document.querySelectorAll('#history .saybtn').length,
            retryButtons: document.querySelectorAll(
              '#history .retrybtn').length,
            rewindArmed: document.querySelectorAll(
              '#history .rewindbtn[data-msg]').length,
            userTurns: document.querySelectorAll('#history .turn.user').length,
            botTurns: document.querySelectorAll('#history .turn.bot').length,
            stamps: document.querySelectorAll('#history .meta time').length,
            title: (document.getElementById('chatTitle') || {}).textContent,
            words: {
              chip: (document.getElementById('stateKicker') || {}).textContent,
              title: (document.getElementById('stateTitle') || {}).textContent,
              desc: (document.getElementById('stateDesc') || {}).textContent,
              mic: (document.getElementById('orbLabel') || {}).textContent,
            },
            state: document.documentElement.dataset.state,
          };
        }""")
        print(f"  {convo['turns']} turn(s): {convo['userTurns']} asked, "
              f"{convo['botTurns']} answered, {convo['stamps']} stamped, "
              f"{convo['copyButtons']} copy, {convo['sayButtons']} play "
              f"(AC-183), {convo['retryButtons']} retry, "
              f"{convo['rewindArmed']} rewindable (AC-194, AC-196)")
        verdict(convo["retryButtons"] == convo["botTurns"],
                f"every answered turn carries a Try again control "
                f"({convo['retryButtons']} retry for {convo['botTurns']} "
                f"bot turn(s))")
        print(f"  empty state shown: {convo['emptyShown']}, example chips "
              f"shown: {convo['chipsShown']} (should be the OPPOSITE of "
              f"having turns, and AGREE with each other)")
        verdict(convo["emptyShown"] == (convo["turns"] == 0),
                "transcript and empty state agree")
        # `#histEmpty` and `#emptyChips` are driven by the SAME writer,
        # `setEmptyState()` (web/AGENTS.md, mocks/q-familiar.html item 5) --
        # a defect already shipped where they disagreed on first paint
        # because each had its own call site. Two elements, one fact: this
        # is the assertion that holds them together rather than trusting
        # that whoever edits one remembers the other.
        verdict(convo["emptyShown"] == convo["chipsShown"],
                f"the empty state and its example chips agree "
                f"(emptyShown={convo['emptyShown']}, "
                f"chipsShown={convo['chipsShown']})")
        print(f"  chat title {convo['title']!r}")
        # AC-168: every state is named in words somewhere always-visible, which
        # is the standing answer to the light theme collapsing `thinking` and
        # `speaking` into one hue. All five slots, printed, because a slot that
        # silently went blank is exactly the regression the criterion is for.
        print(f"  state {convo['state']!r} in words: "
              + ", ".join(f"{k}={v!r}" for k, v in convo["words"].items()))
        blank = [k for k, v in convo["words"].items() if not (v or "").strip()]
        verdict(not blank, f"every state slot carries a word "
                           f"(blank: {blank or 'NONE'})")
        # Step 3c (CU-3, CU-12, AC-689, AC-694): where things now live.
        homes = page.evaluate("""() => ({
          rowLast: document.getElementById('history').lastElementChild
                   === document.getElementById('workRow'),
          style: document.documentElement.dataset.style,
          liveHome: document.getElementById('liveLine').parentNode.id,
          liveSlot: getComputedStyle(document.documentElement)
                      .getPropertyValue('--live-slot').trim(),
          // What the STYLE does with the box's word, read with its
          // `hidden` lifted for one synchronous read and put back (DOM-local:
          // nothing reaches the daemon, and paint() cannot run in between).
          inStateDrawn: (() => {
            const el = document.getElementById('inState'), was = el.hidden;
            el.hidden = false;
            const shown = getComputedStyle(el).display !== 'none';
            el.hidden = was;
            return shown; })(),
          bandGone: !document.getElementById('waveTop')
                    && !document.getElementById('bandWord')
                    && !document.getElementById('micLine'),
          describedBy: document.getElementById('stateChip')
                         .getAttribute('aria-describedby'),
        })""")
        verdict(homes["rowLast"], "the working row is #history's last child "
                                  "(AC-694)")
        # Step 4 (AC-689, AC-681): the words sit in the slot the SERVED style
        # names -- the box under Lantern, the caption under Caption -- so this
        # read-only run proves whichever style the daemon has set.
        want = {"lantern": "liveSlot", "caption": "captionSlot"}.get(homes["style"])
        verdict(want is not None and homes["liveHome"] == want == homes["liveSlot"],
                f"the live words sit in the {homes['style']} style's slot "
                f"(#{homes['liveHome']}, --live-slot {homes['liveSlot']!r}; "
                f"AC-689)")
        verdict(homes["inStateDrawn"] == (homes["style"] != "caption"),
                f"the box's state word is drawn only by Lantern "
                f"(style {homes['style']!r})")
        verdict(homes["bandGone"], "the band, its word and #micLine are gone "
                                   "(CU-3, CU-12)")
        verdict(homes["describedBy"] == "orbLabel",
                f"the pill is described by #orbLabel (CU-12; "
                f"{homes['describedBy']!r})")

        # ------------------------------------------------------------------ #
        # THE COMPOSER (AC-162), driven WITHOUT sending
        #
        # Typing is a peer of speaking, so the box has to behave like one --
        # and every check here is DOM-local: `growInput`/`paintSend` run on
        # input and nothing reaches the daemon until Enter, which is why this
        # is in the default read-only run and `--typed` is not.
        # ------------------------------------------------------------------ #
        print("=== the composer (AC-162, AC-111) ===")
        page.fill("#input", "")
        page.wait_for_timeout(200)
        empty_box = page.evaluate("""() => ({
          send: document.getElementById('sendBtn').disabled,
          label: document.getElementById('sendBtn').getAttribute('aria-label'),
          arrow: !document.getElementById('sendIcon').hasAttribute('hidden'),
          square: !document.getElementById('stopIcon').hasAttribute('hidden'),
        })""")
        print(f"  empty box  -> send disabled={empty_box['send']} "
              f"label={empty_box['label']!r} arrow={empty_box['arrow']} "
              f"square={empty_box['square']}")
        page.fill("#input", "probe: this is never sent")
        page.wait_for_timeout(250)
        typed_box = page.evaluate("""() => ({
          send: document.getElementById('sendBtn').disabled,
          height: document.getElementById('input').clientHeight,
        })""")
        print(f"  with text  -> send disabled={typed_box['send']} "
              f"(box {typed_box['height']}px)")
        # Shift+Enter newlines rather than sending (6.1). Checked by the
        # VALUE gaining a line, so a send would also be visible: the handler
        # clears the box.
        page.focus("#input")
        page.keyboard.press("Shift+Enter")
        page.wait_for_timeout(200)
        shifted = page.eval_on_selector("#input", "e => e.value")
        print(f"  Shift+Enter -> value {shifted!r} "
              f"(newline kept, nothing sent)")
        # ESCAPE CLEARS AN IDLE DRAFT, and this check was BACKWARDS until
        # 2026-09-11. It used to read "there is deliberately no fourth Escape
        # case", which was true when it was written and stopped being true in
        # `16a82f8`: the owner asked for "ESC should function just like stop
        # and cancel message input", so the handler gained a FIFTH case and
        # clearing is now the LAST of them (web/AGENTS.md, e2e F3/F3b). Half
        # of the old argument survives and decides the ORDER rather than the
        # existence of the case -- with a turn in flight Escape still STOPS,
        # draft or no draft. This probe was reporting BAD against a page that
        # was doing exactly what it was asked to, which is how a stale
        # instrument teaches the next reader to distrust it.
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        after_esc = page.evaluate("""() => ({
          draft: document.getElementById('input').value,
          state: document.documentElement.dataset.state,
        })""")
        verdict(after_esc["draft"] == ""
                and after_esc["state"] in ("off", "idle"),
                f"Escape on an IDLE page cleared the draft (was {shifted!r}, "
                f"state still {after_esc['state']!r})")
        page.fill("#input", "")
        page.wait_for_timeout(200)

        # ------------------------------------------------------------------ #
        # THE ATTACH MENU (AC-205, mocks/q-familiar.html item 1)
        #
        # `#camBtn` is gone: the camera is now an ITEM inside `#attachBtn`'s
        # menu, beside "Upload a file". Existence in the WANT list above only
        # proves the elements are in the DOM, and both start `hidden` -- so
        # this OPENS the menu the way the owner would, the same way the
        # settings surface's own hover-help check reaches into a popover
        # rather than trusting a selector on a hidden node. Read-only: it
        # opens the menu, reads it, and closes it with Escape, never picking
        # an item -- clicking "Take a photo" would ask this headless Chrome
        # for a camera it does not have.
        # ------------------------------------------------------------------ #
        print("=== the attach menu (AC-205) ===")
        page.click("#attachBtn")
        page.wait_for_timeout(300)
        menu = page.evaluate("""() => {
          const m = document.getElementById('attachMenu');
          const up = document.getElementById('attachUpload');
          const cam = document.getElementById('attachPhoto');
          return {
            // `.hidden`, NOT `offsetParent`. `#attachMenu` is `position:
            // fixed` (index.html's own comment: placed against the button's
            // rect and clamped to the viewport) -- Chromium reports
            // `offsetParent === null` for a fixed-position element WHETHER
            // OR NOT it is shown, which made this read "closed" against a
            // menu that had just been opened (MEASURED against the real
            // page while writing this check). `app.js`'s own
            // `setAttachMenuOpen` toggles `.hidden`, so that is the
            // property that means what this check needs. The two ITEMS
            // are ordinary flow children of the fixed menu, so their
            // `offsetParent` correctly resolves to the menu itself and is
            // left alone.
            open: !!m && !m.hidden,
            expanded: document.getElementById('attachBtn')
                        .getAttribute('aria-expanded'),
            upload: !!up && up.offsetParent !== null
                    && (up.innerText || '').trim().length > 0,
            photo: !!cam && cam.offsetParent !== null
                   && (cam.innerText || '').trim().length > 0,
          };
        }""")
        print(f"  #attachBtn pressed -> menu open={menu['open']} "
              f"aria-expanded={menu['expanded']!r}, "
              f"upload item usable={menu['upload']}, "
              f"photo item usable={menu['photo']}")
        verdict(menu["open"] and menu["expanded"] == "true",
                "the attach button opens its menu and says so in aria-expanded")
        verdict(menu["upload"],
                "the menu offers an upload item with a visible label "
                "(#attachUpload)")
        verdict(menu["photo"],
                "the menu offers a take-a-photo item with a visible label "
                "(#attachPhoto), which is where the camera control moved")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        closed = page.eval_on_selector("#attachMenu", "e => e.hidden")
        verdict(closed, "Escape closes the attach menu again")

        # ------------------------------------------------------------------ #
        # PAST CONVERSATIONS (AC-164)
        #
        # `GET /api/chats` is a read and the LIST half of this block stays in
        # the default run. **CLICKING A ROW IS NOW A WRITE** and is behind
        # `--write`: since `docs/parallel-agents.md` phase 4 a click POSTs
        # `/api/session/open`, which MOVES the conversation the daemon is
        # holding -- so against the owner's live Butler the next thing they
        # said would continue whatever this probe opened. It used to issue
        # `GET /api/chats/{id}`, which is why the comment here said the whole
        # block was safe.
        # ------------------------------------------------------------------ #
        # THE TREE GROUPS AND NEVER FILTERS (docs/filing-and-pacing.md 8,
        # D-D1). The page asks for EVERY conversation and draws each under
        # its project, so "every conversation the daemon serves has a row"
        # becomes "is a row, or sits inside a project the owner SHUT" -- a
        # shut project hides its rows and keeps their count on its line. A
        # served conversation that is in neither place is a row the page
        # dropped, which is what AC-229 exists to forbid.
        print("=== past conversations (AC-164, AC-291) ===")
        listed = page.request.get(URL.rstrip("/") + "/api/chats")
        served = (listed.json().get("chats") or []) if listed.ok else []
        drawn = page.eval_on_selector_all(
            "#chatList .chatrow",
            """els => els.map(r => ({
                 id: r.dataset.chatId || '',
                 title: (r.querySelector('.t') || {}).textContent || '',
                 current: r.getAttribute('aria-current'),
                 label: r.getAttribute('aria-label'),
               }))""")
        nodes = page.eval_on_selector_all(
            "#chatList .pnode[data-project-id]",
            """els => els.map(n => ({
                 id: n.dataset.projectId,
                 name: (n.querySelector('.nm') || {}).textContent || '',
                 open: n.querySelector('.ptoggle')
                        .getAttribute('aria-expanded') === 'true'}))""")
        note = page.eval_on_selector("#chatsNote", "e => e.textContent")
        print(f"  GET /api/chats -> {listed.status}, {len(served)} served; "
              f"{len(drawn)} rows drawn; note {note!r}")
        print(f"  projects: {[(n['name'], n['open']) for n in nodes]}")
        for row in drawn[:4]:
            print(f"    {row['title'][:44]!r:<48} current={row['current']}")
        shut = {n["id"] for n in nodes if not n["open"]}
        drawn_ids = {r["id"] for r in drawn}
        lost = [c["id"] for c in served
                if c["id"] not in drawn_ids and c.get("projectId") not in shut]
        verdict(not lost and drawn_ids <= {c["id"] for c in served},
                f"every conversation the daemon serves is a row or sits in a "
                f"SHUT project ({len(drawn)} drawn, {len(served)} served, "
                f"{len(lost)} lost)")
        if len(drawn) > 1 and not WRITE:
            print("  SKIP: opening a conversation MOVES the one the daemon is "
                  "holding, so it needs --write and a scratch daemon "
                  "(AC-240). The list above is still checked.")
        elif len(drawn) > 1:
            # Open one that is NOT the live conversation: opening that one
            # would prove nothing -- the claim is that a row the owner clicks
            # BECOMES the one Butler is holding (AC-240).
            before_turns = page.eval_on_selector_all(
                "#history .histitem", "els => els.length")
            # Any row that is NOT the one on screen. `:last-of-type` stopped
            # meaning "the oldest" when the list became a tree -- every
            # project's own group has a last row.
            page.click("#chatList .chatrow:not([aria-current])")
            page.wait_for_timeout(1200)
            opened = page.evaluate("""() => ({
              title: document.getElementById('chatTitle').textContent,
              turns: document.querySelectorAll('#history .histitem').length,
              notice: (document.getElementById('notice') || {}).textContent,
              current: [...document.querySelectorAll('#chatList .chatrow')]
                .findIndex(r => r.getAttribute('aria-current') === 'true'),
            })""")
            print(f"  opened a past one: title {opened['title']!r}, "
                  f"{before_turns} turn(s) -> {opened['turns']}, "
                  f"aria-current now on row {opened['current']}")
            print(f"  notice: {opened['notice']!r}")
            # Reloaded, which now shows the conversation that was just opened:
            # it IS the one the daemon holds, and `ready` says so. There is no
            # "back" to get to -- that was `backToLive()`, and the turn it
            # needed is the reason this used to be a reload.
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            if LIVE:
                _arm_if_needed(page)
            print("  reloaded back to the live conversation")
        else:
            print("  SKIP: fewer than two conversations on this daemon, so "
                  "there is no PAST one to open")

        # ------------------------------------------------------------------ #
        # THE PROJECT TREE (AC-291, docs/filing-and-pacing.md 8, R-D) AND THE
        # HEADER THAT REPLACED THE STRIP (docs/sidebar-collapse.md, AC-293,
        # AC-294, 2026-09-26)
        #
        # The rail of TILES that FILTERED one flat list, its "All chats" and
        # the filter head are gone; each project is a node of the sidebar's
        # tree. The 56px strip that used to sit beside the tree is ALSO gone
        # now -- New chat, the list toggle and Settings live in the header at
        # every width instead. READ in the default run. PRESSING a project is
        # behind `--write`: it writes the expanded set to the daemon
        # (AC-291), and this file's default run is read-only.
        # ------------------------------------------------------------------ #
        print("=== the project tree (AC-291, AC-230, AC-231) ===")
        rows = page.request.get(URL.rstrip("/") + "/api/shortcuts")
        rowdocs = (rows.json().get("rows") or []) if rows.ok else []
        tree = page.evaluate("""() => {
          const nodes = [...document.querySelectorAll(
                          '#chatList .pnode[data-project-id]')];
          const scroll = document.querySelector('.sb-scroll');
          const list = document.getElementById('chatList');
          const b = (n) => n.getBoundingClientRect();
          const visible = list && scroll
            ? Math.max(0, Math.min(b(list).bottom, b(scroll).bottom)
                          - Math.max(b(list).top, b(scroll).top)) : 0;
          const hit = (n) => {
            const q = b(n);
            const at = document.elementFromPoint(q.x + q.width / 2,
                                                 q.y + q.height / 2);
            return !!(at && (at === n || n.contains(at)));
          };
          const open = nodes.filter(n => n.querySelector('.ptoggle')
                                   .getAttribute('aria-expanded') === 'true');
          return {
            ids: nodes.map(n => n.dataset.projectId),
            more: nodes.filter(n => n.querySelector('.phead .tilemore')).length,
            open: open.length,
            // Every OPEN project offers its own labelled "+ New chat", and
            // the first one is hit-testable where it is drawn.
            newIn: open.filter(n => /New chat/.test(
                     (n.querySelector('.pnew') || {}).innerText || '')).length,
            newHit: open.length ? hit(open[0].querySelector('.pnew')) : null,
            all: /all chats/i.test(document.getElementById('sidebar').innerText),
            chatListVisible: Math.round(visible),
            noStrip: !document.getElementById('projRail')
              && !document.getElementById('railTiles'),
            // AC-683/AC-684 (lantern-ui CU-9 reverses AC-294): the panel
            // icon is in the HEADER; New chat is the sidebar's first row and
            // Settings the gear on its account row -- all three present and
            // hit-testable while the sidebar is open.
            inHeader: !!document.querySelector('header #burger')
              && !!document.querySelector('#sidebar .side-top #newChat')
              && !!document.querySelector('#sidebar .acct-row #settingsBtn'),
            headerReachable: ['burger', 'newChat', 'settingsBtn']
              .filter(id => hit(document.getElementById(id))),
          };
        }""")
        print(f"  {len(tree['ids'])} project nodes for {len(rowdocs)} rows, "
              f"{tree['open']} open; {tree['more']} second-click controls")
        print(f"  chat list visible: {tree['chatListVisible']}px")
        verdict(tree["ids"] == [r["id"] for r in rowdocs],
                f"every project row is a node of the tree, in the daemon's "
                f"order ({len(tree['ids'])} nodes, {len(rowdocs)} rows)")
        verdict(tree["more"] == len(rowdocs),
                "every project keeps its controls behind a '...'")
        verdict(not tree["all"], "no 'All chats' remains (R-D overturns R4)")
        verdict(tree["chatListVisible"] > 0,
                "the tree is on screen (AC-231)")
        verdict(tree["noStrip"],
                "the 56px strip (#projRail/#railTiles) is gone (AC-293)")
        verdict(tree["inHeader"],
                "the panel icon is in the header, New chat and Settings in "
                "the sidebar (AC-683, CU-9)")
        verdict(len(tree["headerReachable"]) == 3,
                f"the list toggle, New chat and Settings are all "
                f"hit-testable: {tree['headerReachable']}")
        # THE FILE EXPLORER AND THE LIBRARY (docs/file-explorer.md AC-310..
        # AC-313; docs/automations.md AU-10, AC-466). READ ONLY here:
        # pressing `#filesBtn` or `#libraryBtn` on a desk writes `right_panel`
        # to the store (it replaced `files_open`), so this run only checks
        # that the right column AGREES with the store's answer -- ONE panel,
        # the one it names -- and, when the files panel is open, that its
        # tree has one tab stop. Opening, running and the phone sheets were
        # driven with `playwright-cli` against a scratch daemon.
        store = rows.json() if rows.ok else {}
        fx = page.evaluate("""() => {
          const p = document.getElementById('filesPanel');
          const lib = document.getElementById('libraryPanel');
          const items = [...document.querySelectorAll(
                           '#filesTree [role="treeitem"]')];
          return {
            btn: !!document.querySelector('header #panelBtn'),
            key: (document.getElementById('panelBtn') || {}).dataset
                 ? document.getElementById('panelBtn').dataset.key : null,
            shown: p ? !p.hidden : null,
            libShown: lib ? !lib.hidden : null,
            libBtn: !!document.querySelector('#sidebar #libraryBtn')
                    && document.querySelectorAll('.ptab').length === 4,
            role: (document.getElementById('filesTree') || {}).getAttribute
                  ? document.getElementById('filesTree').getAttribute('role')
                  : null,
            items: items.length,
            stops: items.filter(i => i.tabIndex === 0).length,
          };
        }""")
        verdict(fx["btn"] and fx["key"] == "Ctrl+Shift+E",
                "the one panel toggle is in the header and names the files "
                "chord (FE-5, AC-699)")
        verdict(fx["libBtn"] and fx["libShown"] is not None,
                "the sidebar's Library row, both panels' Files | Library "
                "tabs and the library panel are on the page (AC-699)")
        if ("right_panel" in store
                and (page.viewport_size or {}).get("width", 0) > 900):
            want = store["right_panel"]
            verdict(fx["shown"] == (want == "files")
                    and fx["libShown"] == (want == "library"),
                    f"the desk's right column agrees with the store "
                    f"(right_panel={want!r}, files={fx['shown']}, "
                    f"library={fx['libShown']}) (AC-312, AC-466)")
        verdict(fx["role"] == "tree", "#filesTree is role=tree (AC-313)")
        if fx["shown"] and fx["items"]:
            verdict(fx["stops"] == 1,
                    f"the file tree has ONE tab stop ({fx['stops']} of "
                    f"{fx['items']} rows) (AC-313)")
        if tree["open"]:
            verdict(tree["newIn"] == tree["open"] and tree["newHit"],
                    f"every OPEN project offers a labelled, hit-testable "
                    f"'+ New chat' ({tree['newIn']} of {tree['open']}). "
                    f"MEASURED before 2026-09-15: the only visible New chat "
                    f"was an unlabelled pencil at the bottom of the rail -- "
                    f"\"I still don't see a clear easy button to start a NEW "
                    f"chat\"")
        else:
            print("  SKIP: no project is open, so no '+ New chat' is drawn")

        if WRITE:
            # ---- BROWSING MOVES NOTHING (R1, amended by R-D) --------------
            # A press on a project line only opens or shuts it. Asserted
            # AFTER EVERY PRESS rather than at the end, because a mutation
            # run once proved an end-state-only check TOOTHLESS (D-13): a
            # press that dragged the held conversation through two projects
            # ended exactly where it began. Each project is pressed TWICE, so
            # the expanded set -- the one thing a press may write -- ends as
            # it was found.
            print("=== browsing a project moves nothing (R1, R-D) ===")

            def filing():
                r = page.request.get(URL.rstrip("/") + "/api/chats")
                d = r.json() if r.ok else {}
                return {c["id"]: c.get("project") for c in d.get("chats", [])}

            def held_id():
                r = page.request.get(
                    URL.rstrip("/") + "/api/session/current")
                return r.json().get("id") if r.ok else None

            was_filed = filing()
            was_id = held_id()
            moved, opened = {}, {}
            for rid in [r["id"] for r in rowdocs][:3]:
                for _ in range(2):
                    page.click(f'#chatList .pnode[data-project-id="{rid}"] '
                               f'.ptoggle')
                    page.wait_for_timeout(700)
                    step = filing()
                    for k in set(was_filed) | set(step):
                        if was_filed.get(k) != step.get(k):
                            moved[k] = (was_filed.get(k), step.get(k))
                    if held_id() != was_id:
                        opened[rid] = held_id()
            verdict(not moved,
                    f"pressing projects re-filed NOTHING (changes: "
                    f"{moved or 'none'})")
            verdict(not opened,
                    f"pressing projects OPENED NOTHING -- the held "
                    f"conversation stayed {was_id} (changes: "
                    f"{opened or 'none'})")

        # ------------------------------------------------------------------ #
        # HOVER HELP (D8, REWRITTEN 2026-09-16). The rule this once asserted
        # -- "every interactive control carries non-empty help" -- IS RETIRED,
        # on the owner's ruling that the old help was "too obnoxious and long"
        # (measured: 143 controls, median 42 characters, longest 374). What
        # replaced it is a RULE rather than a list:
        #     a control with a VISIBLE WORD gets NO tooltip;
        #     an ICON-ONLY control gets one to three words plus its shortcut.
        # So the assertion inverts. A labelled control WITH help is now the
        # defect, because that is the noise being removed, and an icon-only
        # control WITHOUT help is the other one. The `title` ban is untouched:
        # its delay is not ours and it never shows on keyboard focus.
        # ------------------------------------------------------------------ #
        print("=== hover help: icon-only only (D8, rewritten) ===")
        help_ = page.evaluate("""() => {
          const sel = 'button, input, textarea, select, [tabindex]';
          const skip = new Set(['attachInput']);
          const out = {missing: [], noisy: [], titles: [], helped: 0, long: []};
          for (const n of document.querySelectorAll(sel)) {
            if (n.closest('#panel')) continue;   // the surface is its own page
            if (skip.has(n.id)) continue;
            if (!n.getBoundingClientRect().width) continue;
            const holder = n.closest('[data-help]');
            const help = holder ? (holder.getAttribute('data-help') || '') : '';
            // "Has a visible word" is read off the RENDERED text, not off a
            // list of ids kept here -- a list in this file is the second copy
            // that drifts the day a control gains a label.
            const worded = (n.innerText || '').trim().length > 0
                        || (n.getAttribute('placeholder') || '').trim().length > 0;
            if (help) {
              out.helped += 1;
              if (help.length > 60) out.long.push([n.id || n.className, help.length]);
              if (worded) {
                out.noisy.push(n.id || n.className || n.tagName);
              }
            } else if (!worded) {
              out.missing.push(n.id || n.className || n.tagName);
            }
          }
          for (const n of document.querySelectorAll('[title]')) {
            out.titles.push(n.id || n.className || n.tagName);
          }
          return out;
        }""")
        print(f"  {help_['helped']} controls carry help; "
              f"{len(help_['missing'])} icon-only lack it {help_['missing'][:8]}; "
              f"{len(help_['noisy'])} labelled are noisy {help_['noisy'][:8]}")
        verdict(not help_["missing"],
                "every ICON-ONLY control has hover help")
        verdict(not help_["noisy"],
                "no control that already shows a word repeats it in a tooltip")
        verdict(not help_["long"],
                f"no tooltip runs past 60 characters ({help_['long'][:4]})")
        verdict(not help_["titles"],
                f"nothing uses the browser's own `title` "
                f"({help_['titles'][:6]})")
        # KEYBOARD focus, not `.focus()`: the tip shows on `:focus-visible`
        # only (review 2026-09-26), so focus handed back after a pointer click
        # raises no stale tip -- and a script's `.focus()` after the probe's
        # own clicks is not keyboard focus. Tab back onto the button instead.
        page.evaluate("document.getElementById('micBtn').focus()")
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        tip = page.evaluate("""async () => {
          const btn = document.getElementById('micBtn');
          await new Promise(r => setTimeout(r, 250));
          const t = document.getElementById('tip');
          const shown = !t.hidden && (t.textContent || '').length > 0;
          btn.blur();
          await new Promise(r => setTimeout(r, 250));
          return {shown, gone: t.hidden};
        }""")
        verdict(tip["shown"] and tip["gone"],
                f"focus shows the help immediately and blur hides it again "
                f"({tip})")

        # ------------------------------------------------------------------ #
        # SEARCHING THEM (AC-185..AC-188, docs/chat-search.md)
        #
        # READ-ONLY, like the block above: typing in the box issues
        # `GET /api/chats?q=`, which changes nothing. The box is CLEARED at
        # the end, because the next block reads the same list.
        #
        # What only a browser can see here: that a content match draws its
        # snippet, that the day headings COLLAPSE when the list empties, and
        # that a query matching nothing says so instead of leaving a blank
        # column -- the three things a route test cannot reach.
        # ------------------------------------------------------------------ #
        print("=== searching past conversations (AC-188) ===")
        if served:
            def chat_rows():
                return page.evaluate("""() => ({
                  rows: [...document.querySelectorAll('#chatList .chatrow')]
                    .map(r => ({t: (r.querySelector('.t')||{}).textContent || '',
                                s: (r.querySelector('.s')||{}).textContent || ''})),
                  heads: document.querySelectorAll('#chatList .sb-head').length,
                  note: document.getElementById('chatsNote').textContent,
                  empty: !document.getElementById('chatsEmpty').hidden,
                })""")
            base = chat_rows()
            # A word out of the NEWEST conversation's own title, so the query
            # is guaranteed to match something on any daemon this is pointed
            # at rather than being a fixture's word hardcoded here.
            word = next((w for w in (served[0].get("title") or "").split()
                         if len(w) > 4), "")
            _open_search(page)
            if word:
                page.fill("#chatSearch", word)
                page.wait_for_timeout(900)
                hit = chat_rows()
                print(f"  {word!r}: {len(base['rows'])} rows -> "
                      f"{len(hit['rows'])}, note {hit['note']!r}, "
                      f"{sum(1 for r in hit['rows'] if r['s'])} with a snippet")
                verdict(hit["rows"],
                        "a word from the newest title still finds it")
            else:
                print("  SKIP: no conversation here has a title to search for")
            page.fill("#chatSearch", "zzzz-no-such-conversation")
            page.wait_for_timeout(900)
            none = chat_rows()
            print(f"  nothing matched: {len(none['rows'])} rows, "
                  f"{none['heads']} day heading(s), note {none['note']!r}, "
                  f"empty line shown={none['empty']}")
            verdict(not none["rows"] and not none["heads"] and none["empty"],
                    "on a no-match search the list empties, the headings go, "
                    "and the page says so")
            page.fill("#chatSearch", "")
            page.wait_for_timeout(900)
            back = chat_rows()
            verdict(len(back["rows"]) == len(base["rows"]),
                    f"clearing the search box puts the whole list back "
                    f"({len(back['rows'])} of {len(base['rows'])})")
        else:
            print("  SKIP: this daemon serves no conversations to search")

        if WRITE:
            # ---- AC-166: the sidebar's collapsed state is the DAEMON's ----
            # It rides on the shortcuts store (`sidebar_collapsed`) rather than
            # in browser storage, which AC-8 bans outright -- so the proof is a
            # RELOAD, not a class on an element. Behind --write because
            # clicking the control POSTs to the daemon; the original value is
            # read first and put back at the end.
            print("=== sidebar collapse survives a reload (AC-166) ===")
            was = page.request.get(URL.rstrip("/") + "/api/shortcuts")
            was_collapsed = bool((was.json() if was.ok else {})
                                 .get("sidebar_collapsed"))
            print(f"  store says sidebar_collapsed={was_collapsed}")
            open_box = page.evaluate("""() => {
              const q = document.getElementById('burger')
                .getBoundingClientRect();
              return [q.x, q.y, q.width, q.height].map(Math.round); }""")
            page.click("#burger")
            page.wait_for_timeout(900)
            now = page.evaluate(
                "document.documentElement.dataset.sidebar")
            stored = bool((page.request.get(URL.rstrip("/") + "/api/shortcuts")
                           .json()).get("sidebar_collapsed"))
            print(f"  after the click: data-sidebar={now!r}, "
                  f"store={stored}")
            page.reload(wait_until="load")
            page.wait_for_timeout(2500)
            survived = page.evaluate(
                "document.documentElement.dataset.sidebar")
            verdict(survived == now,
                    f"the collapsed state survived a RELOAD "
                    f"(data-sidebar={survived!r})")
            # And nothing in web/ may name a setting for it (AC-41): the value
            # is in the shortcuts store, so the settings schema must not carry
            # a key for it.
            schema = page.request.get(URL.rstrip("/") + "/api/settings")
            body = schema.text() if schema.ok else ""
            verdict("sidebar" not in body,
                    "the settings schema does not name a sidebar setting "
                    "(AC-41)")

            # ---- AC-293/AC-294: THE LIST GOES TO 0px, THE HEADER NEVER DOES
            #
            # This is the criterion's own witness and it did not exist before
            # the strip was deleted (docs/sidebar-collapse.md, 2026-09-26):
            # the tree block above measures an EXPANDED sidebar at the default
            # viewport, which says nothing about what is left after a
            # collapse. Both halves matter and they fail differently. A
            # header control that went with the list is a sidebar with no way
            # back; a list that stayed is a collapse that collapsed nothing.
            #
            # Measured at the SHORT viewport the plan names (1280x440) as well
            # as the default one, because the way back going unreachable is a
            # height failure: it was measured on the mock at a 620px viewport
            # with 18 projects, and a check at 720px would never see it.
            # `elementFromPoint` at each control's own centre, never
            # `offsetParent`, because the sidebar-resize work shipped a handle
            # that passed every visibility test and no mouse could hit.
            print("=== collapsed: the list is 0px, the header is not "
                  "(AC-293, AC-294, AC-295) ===")
            if survived != "collapsed":
                print("  SKIP: the sidebar is not collapsed, so there is "
                      "nothing to assert about what is left")
            else:
                def collapsed_shape():
                    return page.evaluate("""() => {
                      const sidebar = document.getElementById('sidebar');
                      const list = document.getElementById('chatList');
                      const b = (n) => n.getBoundingClientRect();
                      const hit = (n) => {
                        if (!n) return false;
                        const q = b(n);
                        if (!q.width || !q.height) return false;
                        const at = document.elementFromPoint(
                          q.x + q.width / 2, q.y + q.height / 2);
                        return !!(at && (at === n || n.contains(at)));
                      };
                      return {
                        sidebarWidth: Math.round(b(sidebar).width),
                        listShown: !!list && list.offsetParent !== null
                                   && !!b(list).width,
                        burger: Math.round(b(document.getElementById(
                          'burger')).left),
                        box: (() => { const q = b(document.getElementById(
                          'burger')); return [q.x, q.y, q.width, q.height]
                          .map(Math.round); })(),
                        foot: ['burger', 'newChat', 'settingsBtn']
                          .filter(id => hit(document.getElementById(id))),
                      };
                    }""")

                for w, h in ((1280, 720), (1280, 440)):
                    page.set_viewport_size({"width": w, "height": h})
                    page.wait_for_timeout(700)
                    shape = collapsed_shape()
                    print(f"  {w}x{h}: sidebar {shape['sidebarWidth']}px; "
                          f"chat list shown={shape['listShown']}; "
                          f"#burger left={shape['burger']}px; "
                          f"reachable {shape['foot']}")
                    verdict(shape["sidebarWidth"] == 0,
                            f"at {w}x{h} the collapsed sidebar is 0px wide "
                            f"(AC-293), measured {shape['sidebarWidth']}px")
                    verdict(not shape["listShown"],
                            f"at {w}x{h} the conversation list is hidden, "
                            f"which is what collapsing means")
                    verdict(shape["foot"] == ["burger"],
                            f"at {w}x{h} the panel icon is hit-testable and "
                            f"New chat and Settings went with the list -- "
                            f"one press behind it (CU-9): {shape['foot']}")
                    verdict(shape["box"] == open_box,
                            f"at {w}x{h} the panel icon's box is the same "
                            f"open and collapsed (AC-685): {open_box} -> "
                            f"{shape['box']}")
                page.set_viewport_size({"width": 1280, "height": 720})
                page.wait_for_timeout(500)

            if stored != was_collapsed:
                page.click("#burger")
                page.wait_for_timeout(900)
                back = bool((page.request.get(URL.rstrip("/") + "/api/shortcuts")
                             .json()).get("sidebar_collapsed"))
                verdict(back == was_collapsed,
                        f"the collapsed state was put back the way it was "
                        f"found (sidebar_collapsed={back})")
            if LIVE:
                _arm_if_needed(page)

        # THE SETTINGS SHEET (docs/lantern-ui.md 5.1, AC-700..AC-705). It
        # replaced the one-page surface, its scroll-spy rail and its phone
        # strip (AC-83, AC-84, AC-88 superseded). Nothing here is readable off
        # the markup: the nav, the strip, the pages and the cards are all
        # built by settings.js from the schema, so these prints and verdicts
        # are the only evidence that the payload crossed the wire and was
        # used. Read-only unless --write.
        served = page.request.get(URL.rstrip("/") + "/api/settings").json()
        groups = served.get("groups", [])
        listed = {g["key"] for g in groups} & {s["group"] for s in served["settings"]}
        listed |= {g["key"] for g in groups if g.get("pairing") or g.get("collection")}

        print("=== settings pages (nav order) ===")
        secs = page.eval_on_selector_all(
            "#settingsRoot .setgroup, #settingsRoot .modeblock",
            """els => els.map(sec => ({
                 key: sec.dataset.page || sec.id,
                 label: (sec.querySelector("h2") || {}).textContent || "?",
                 everyday: sec.querySelectorAll(":scope > .cards > .set").length,
                 advanced: sec.querySelectorAll("details.adv .set").length,
               }))""")
        for i, sec in enumerate(secs, 1):
            print(f"  {i:>2}. {sec['label']:<18} everyday={sec['everyday']:<3} "
                  f"advanced={sec['advanced']}")
        if not secs:
            print("  (no pages rendered)")

        # Discovered controls (choices_from). Their SHAPE is the thing that
        # cannot be read off the markup: whether the datalist actually filled,
        # and whether a favourites star was rendered. The star writes one
        # shared favourites list, so a control that should not have one and
        # does is a model id waiting to be committed as a voice (AC-41).
        print("=== discovered controls ===")
        rows = page.eval_on_selector_all(
            "#settingsRoot .disc",
            """els => els.map(w => {
                 const i = w.querySelector("input");
                 const dl = w.querySelector("datalist");
                 return {
                   key: i ? i.id.replace(/^set-/, "") : "?",
                   star: !!w.querySelector(".star"),
                   options: dl ? dl.querySelectorAll("option").length : 0,
                   note: (w.querySelector(".disc-note") || {}).textContent || "",
                 };
               })""")
        for r in rows:
            print(f"  {r['key']:<16} star={str(r['star']):<5} "
                  f"options={r['options']:<4} note={r['note']!r}")
        if not rows:
            print("  (none rendered)")

        # The connections block is hand-built, so nothing in the settings
        # schema would reveal it missing. What matters is that it LISTS
        # something and that no key value is on the page (AC-34, AC-37).
        print("=== connections block ===")
        conns = page.eval_on_selector_all(
            "#connList .connrow",
            """els => els.map(r => ({
                 name: (r.querySelector(".cname") || {}).textContent || "?",
                 meta: (r.querySelector(".cmeta") || {}).textContent || "",
                 removable: [...r.querySelectorAll("button")]
                              .some(b => /remove/i.test(b.textContent)),
               }))""")
        for c in conns:
            print(f"  {c['name']:<22} removable={str(c['removable']):<5} "
                  f"{c['meta']}")
        if not conns:
            print("  (none listed -- or the block is missing)")
        fields = page.eval_on_selector_all(
            "#connRoot .connform input", "els => els.map(e => e.id)")
        print(f"  form fields: {fields}")

        # AC-700: the nav is the served sections, in served order, each
        # group under the section it names, the two carve-outs under the
        # section their own markup names; ONE page on screen; the page behind
        # keeps its link.
        print("=== settings sheet: nav and one page (AC-700) ===")
        _open_settings(page)
        page.wait_for_timeout(700)
        nav = page.evaluate("""() => {
          const out = [];
          let sec = null;
          for (const n of document.getElementById("navRoot").children) {
            if (n.classList.contains("nsec")) { sec = n.textContent; continue; }
            for (const b of n.querySelectorAll(".nitem")) {
              out.push({sec, key: b.dataset.page,
                        current: b.getAttribute("aria-current")});
            }
          }
          const shown = [...document.querySelectorAll(
              "#settingsRoot .setgroup, #settingsRoot .modeblock")]
            .filter(e => !e.hidden).map(e => e.dataset.page);
          return {items: out, shown,
                  carved: {modeRoot: document.getElementById("modeRoot").dataset.section,
                           connRoot: document.getElementById("connRoot").dataset.section},
                  // `setLink` marks the account row's link word `data-ok`.
                  linkDown: document.getElementById("footLink").dataset.ok !== "true"};
        }""")
        labels = {s["key"]: s["label"] for s in served.get("sections", [])}
        want = []
        for s in served.get("sections", []):
            carved = [k for k, v in nav["carved"].items() if v == s["key"]]
            grouped = [g["key"] for g in groups
                       if g.get("section") == s["key"] and g["key"] in listed]
            want += [(s["label"], k) for k in carved + grouped]
        got = [(i["sec"], i["key"]) for i in nav["items"]]
        for sec_label, key in got:
            print(f"  {sec_label or '-':<10} {key}")
        verdict(got == want,
                f"the nav is the served sections in order, each group under "
                f"its served section and the carve-outs under theirs "
                f"(AC-700){'' if got == want else f': want {want}'}")
        verdict(len(nav["shown"]) == 1,
                f"exactly one category page is on screen: {nav['shown']}")
        current = [i["key"] for i in nav["items"] if i["current"] == "page"]
        verdict(current == nav["shown"],
                f"the nav marks the page on screen: {current}")
        verdict(not nav["linkDown"],
                "the page behind keeps its link while the sheet is open")
        del labels

        # AC-701: the strip on a staged page lists every staged group with
        # its headline, and pressing a stop opens that page.
        print("=== signal-path strip (AC-701) ===")
        staged = [g for g in groups if g.get("stage") is not None
                  and g["key"] in listed]
        if not staged:
            print("  SKIP: the daemon serves no staged group")
        else:
            page.click("#nav-" + staged[0]["key"])
            page.wait_for_timeout(400)
            stops = page.eval_on_selector_all(
                "#settingsRoot .strip .stop",
                """els => els.map(b => ({key: b.dataset.page,
                   n: (b.querySelector(".snode") || {}).textContent,
                   label: (b.querySelector(".slbl") || {}).textContent,
                   value: (b.querySelector(".ssub") || {}).textContent,
                   clipped: b.scrollWidth > b.clientWidth}))""")
            for s in stops:
                print(f"  {s['n']:<2} {s['label']:<16} = {s['value']!r}")
            verdict([s["key"] for s in stops] == [g["key"] for g in staged],
                    "the strip is the staged groups in served order")
            heads = {g["key"]: g.get("headline") for g in staged}
            blank = [s["key"] for s in stops if heads.get(s["key"]) and not s["value"]]
            verdict(not blank, f"every staged group with a headline shows its "
                               f"value on the strip: blank {blank}")
            for g in staged:
                page.click("#stop-" + g["key"])
                page.wait_for_timeout(250)
                where = page.evaluate("""(k) => ({
                  shown: [...document.querySelectorAll('#settingsRoot .setgroup, '
                           + '#settingsRoot .modeblock')].filter(e => !e.hidden)
                           .map(e => e.dataset.page),
                  step: (document.querySelector('#settingsRoot .stop[aria-current="step"]')
                         || {}).dataset,
                  strip: !!document.querySelector('[data-page="' + k + '"].setgroup .strip'),
                })""", g["key"])
                verdict(where["shown"] == [g["key"]] and where["strip"]
                        and (where["step"] or {}).get("page") == g["key"],
                        f"the {g['key']} stop opens its page and carries the "
                        f"strip (shown={where['shown']})")

        if LIVE:
            # The rule the strip is easiest to get wrong on, driven rather
            # than read. The write goes out from HERE, not from the page, so
            # it reaches the browser as a `settings` broadcast over the
            # socket -- the value path that never goes near a refresh. Two
            # things have to be true afterwards: the nav shows the new value,
            # and the settings row captured beforehand is still the same live
            # node. A sheet that rebuilt on a value change would detach it,
            # and that rebuild is the defect the sig guard exists to stop.
            print("=== nav repaint on the value path ===")
            import json as _json
            import urllib.request as _url
            page.evaluate("window.__probeRow = "
                          "document.querySelector('#settingsRoot .set')")
            was = page.evaluate("document.documentElement.dataset.theme")
            want_theme = "light" if was == "dark" else "dark"
            before = page.eval_on_selector_all(
                "#navRoot .nsub", "els => els.map(b => b.textContent)")
            body = _json.dumps({"theme": want_theme}).encode()
            with _url.urlopen(_url.Request(
                    URL.rstrip("/") + "/api/settings", data=body, method="POST",
                    headers={"Content-Type": "application/json"}),
                    timeout=10, context=_TLS) as r:
                print(f"  wrote theme={want_theme} from outside the page ({r.status})")
            page.wait_for_timeout(1500)
            after = page.eval_on_selector_all(
                "#navRoot .nsub", "els => els.map(b => b.textContent)")
            alive = page.evaluate(
                "!!(window.__probeRow && window.__probeRow.isConnected)")
            moved = [(b, a) for b, a in zip(before, after) if b != a]
            print(f"  nav lines whose text changed: {moved or 'NONE'}")
            verdict(alive, "a value broadcast repaints; the rows are not rebuilt")

        # AC-702: search reaches ACROSS categories, grouped, folds opened,
        # counted in the nav, and says so when nothing matches.
        print("=== search (AC-702) ===")
        was_page = page.evaluate(
            "(document.querySelector('#navRoot [aria-current=\"page\"]') || {}).id")
        for term, many in (("model", True), ("zzzznothing", False)):
            page.fill("#setSearch", term)
            page.wait_for_timeout(400)
            found = page.evaluate("""() => {
              const rows = [...document.querySelectorAll("#settingsRoot .set")]
                .filter(r => r.offsetParent !== null);
              const pages = [...document.querySelectorAll(
                "#settingsRoot .setgroup, #settingsRoot .modeblock")]
                .filter(s => !s.hidden).map(s => s.dataset.page);
              const counts = [...document.querySelectorAll("#navRoot .ncount")]
                .filter(c => !c.hidden).map(c => Number(c.textContent));
              const shut = [...document.querySelectorAll("#settingsRoot details.adv")]
                .filter(d => !d.open && d.querySelector(".set:not([hidden])")).length;
              const n = document.querySelector("#settingsRoot .nomatch");
              return {rows: rows.length, pages, counted: counts.reduce((a, b) => a + b, 0),
                      shut, empty: !!n && n.offsetParent !== null,
                      line: (document.querySelector("#settingsRoot .sresults p")
                             || {}).textContent || ""};
            }""")
            print(f"  {term!r:<14} rows={found['rows']:<3} pages={found['pages']}")
            print(f"                 {found['line']!r}")
            if many:
                verdict(found["rows"] > 0 and len(found["pages"]) >= 2,
                        f"{term!r} reaches rows on more than one page")
                verdict(found["counted"] >= found["rows"],
                        f"the nav counts the matches ({found['counted']} for "
                        f"{found['rows']} rows)")
                verdict(found["shut"] == 0, "no matching row is behind a shut fold")
                verdict(not found["empty"], "a hit does not say nothing matched")
            else:
                verdict(found["rows"] == 0 and found["empty"],
                        f"{term!r} shows no rows and says nothing matched")
        page.fill("#setSearch", "")
        page.wait_for_timeout(300)
        back_to = page.evaluate("""() => ({
          shown: [...document.querySelectorAll('#settingsRoot .setgroup, '
                   + '#settingsRoot .modeblock')].filter(e => !e.hidden).length,
          current: (document.querySelector('#navRoot [aria-current="page"]') || {}).id})""")
        verdict(back_to["shown"] == 1 and back_to["current"] == was_page,
                f"clearing the search puts back the page it left: {back_to}")

        # AC-704: the style row is drawn as preview cards because its served
        # `preview` flag says so, first on its page, the served default
        # marked, the served value pressed.
        print("=== style cards (AC-704) ===")
        prev = [s for s in served["settings"] if s.get("preview")]
        if not prev:
            print("  SKIP: the daemon flags no setting `preview`")
        for spec in prev:
            page.click("#nav-" + spec["group"])
            page.wait_for_timeout(400)
            cards = page.evaluate("""(k) => {
              const row = document.querySelector('#settingsRoot .set[data-key="' + k + '"]');
              if (!row) return null;
              const first = row.parentElement.querySelector('.set') === row
                && row.closest('.setgroup').querySelector('.cards') === row.parentElement;
              return {first,
                      cards: [...row.querySelectorAll('.pv')].map(b => ({
                        style: b.querySelector('.mini').dataset.style,
                        pressed: b.getAttribute('aria-pressed'),
                        dflt: !!b.querySelector('.pvdef')}))};
            }""", spec["key"])
            print(f"  {cards}")
            verdict(cards and cards["first"], f"{spec['key']} is its page's first row")
            verdict(cards and [c["style"] for c in cards["cards"]] == spec["choices"],
                    "one card per served choice, each wearing its value")
            verdict(cards and [c["style"] for c in cards["cards"] if c["pressed"] == "true"]
                    == [spec["value"]], "the served value's card is pressed")
            verdict(cards and [c["style"] for c in cards["cards"] if c["dflt"]]
                    == [spec["default"]], "the served default is marked Default")
            if WRITE and cards:
                other = [c for c in spec["choices"] if c != spec["value"]][0]
                page.click(f'#settingsRoot .set[data-key="{spec["key"]}"] '
                           f'.mini[data-style="{other}"]')
                page.wait_for_timeout(1200)
                now = page.evaluate("document.documentElement.dataset.style")
                verdict(now == other, f"choosing {other} applies it live ({now})")
                page.click(f'#settingsRoot .set[data-key="{spec["key"]}"] '
                           f'.mini[data-style="{spec["value"]}"]')
                page.wait_for_timeout(1200)

        # AC-703 (AC-86): the restart bar. Driven: a restart-flagged setting
        # is written from its own control, the docked bar has to rise with
        # the right count and the setting's name, and putting the value back
        # has to take it away again. Which setting that is comes off the page
        # -- `.set.restart` is a class settings.js puts on a row from the
        # schema's own flag, so nothing here names one.
        #
        # Behind --write, because it changes the daemon. It reverts what it
        # wrote, but "it puts it back" is not the same promise as "it never
        # touched it".
        if not WRITE:
            print("=== restart bar === skipped; pass --write to drive it")
        else:
            print("=== restart bar (AC-703) ===")
            target = page.evaluate("""() => {
              for (const row of document.querySelectorAll("#settingsRoot .set.restart")) {
                const sel = row.querySelector("select");
                if (!sel) continue;
                const other = [...sel.options].map(o => o.value)
                                .filter(v => v !== sel.value)[0];
                if (other === undefined) continue;
                return { key: row.dataset.key, page: row.closest(".setgroup").dataset.page,
                         label: row.querySelector(".set-name").textContent,
                         was: sel.value, to: other };
              }
              return null;
            }""")
            if not target:
                print("  SKIP: no restart-flagged choice with a second option")
            else:
                def bar():
                    return page.evaluate("""() => {
                      const b = document.getElementById("commitBar");
                      return {shown: b.classList.contains("show"),
                              docked: !!b.closest("#panel .spane"),
                              count: document.getElementById("commitCount").textContent,
                              names: document.getElementById("commitNames").textContent,
                              inert: b.inert};
                    }""")
                page.click("#nav-" + target["page"])
                page.wait_for_timeout(300)
                sel = f'#settingsRoot .set[data-key="{target["key"]}"] select'
                print(f"  before        {bar()}")
                page.select_option(sel, target["to"])
                page.wait_for_timeout(900)
                up = bar()
                print(f"  after change  {up}")
                verdict(up["shown"] and up["docked"] and up["count"] == "1"
                        and target["label"] in up["names"] and not up["inert"],
                        "one restart-flagged change raises the docked bar, "
                        "counted once and named")
                page.select_option(sel, target["was"])
                page.wait_for_timeout(900)
                down = bar()
                print(f"  after revert  {down}")
                verdict(not down["shown"] and down["inert"],
                        "putting the value back takes the bar away")

        # AC-705 (successor of AC-88): at 390 the sheet is the whole screen
        # and a back stack -- the list, a page, Back -- with nothing scrolling
        # sideways and every visible control 44 px tall.
        print("=== phone (390px, AC-705) ===")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(700)
        _open_settings(page)
        page.wait_for_timeout(800)
        view = lambda: page.evaluate("""() => {
          const p = document.getElementById("panel");
          const vis = (id) => { const e = document.getElementById(id);
                                return !!e && e.offsetParent !== null; };
          return {phone: p.dataset.phone, list: vis("navRoot"), back: vis("setBack"),
                  pageShown: vis("settingsRoot"),
                  width: Math.round(p.getBoundingClientRect().width)};
        }""")
        first = view()
        print(f"  opened        {first}")
        verdict(first["phone"] == "list" and first["list"] and not first["pageShown"]
                and first["width"] == 390,
                "the sheet opens full width on the category list")
        first_key = page.evaluate(
            "document.querySelector('#navRoot .nitem').dataset.page")
        page.click("#nav-" + first_key)
        page.wait_for_timeout(600)
        inside = view()
        print(f"  pressed {first_key:<6}{inside}")
        verdict(inside["phone"] == "page" and inside["back"] and not inside["list"],
                "pressing a category shows its page with Back")
        narrow = page.evaluate("""() => {
          const doc = document.documentElement;
          const box = document.getElementById("setScroll");
          const wide = [...document.querySelectorAll("#settingsRoot .set")]
            .filter(r => r.offsetParent !== null && r.scrollWidth > r.clientWidth)
            .map(r => r.dataset.key + " " + r.scrollWidth + ">" + r.clientWidth);
          const small = [...document.querySelectorAll(
              "#panel button, #panel select, #panel input:not([type=checkbox])")]
            .filter(e => e.offsetParent !== null && !e.closest('.strip')
                         && e.getBoundingClientRect().height < 43.5)
            .map(e => e.id || e.className || (e.tagName + ":" + e.type));
          return {docScroll: doc.scrollWidth, innerWidth: window.innerWidth,
                  boxScroll: box.scrollWidth, boxClient: box.clientWidth,
                  wide, small};
        }""")
        print(f"  document {narrow['docScroll']} / {narrow['innerWidth']}; "
              f"scrollport {narrow['boxScroll']} / {narrow['boxClient']}")
        verdict(narrow["docScroll"] <= narrow["innerWidth"]
                and narrow["boxScroll"] <= narrow["boxClient"] and not narrow["wide"],
                f"nothing scrolls sideways at 390: {narrow['wide'] or 'no wide rows'}")
        verdict(not narrow["small"],
                f"every visible control is 44 px tall: {narrow['small'][:8]}")
        page.screenshot(path="test/manual/page-390.png")
        print("screenshot -> test/manual/page-390.png")
        page.click("#setBack")
        page.wait_for_timeout(500)
        out = view()
        print(f"  pressed Back  {out}")
        verdict(out["phone"] == "list" and out["list"] and not out["pageShown"],
                "Back returns to the category list")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.set_viewport_size({"width": 1280, "height": 720})
        page.wait_for_timeout(500)

        # What the owner would actually read after a turn. The whole reply must
        # be here, in order, with the unspoken tail marked -- a page that
        # overwrote each frame would show one sentence and look perfectly
        # healthy. Shared by --turn and --typed, because a typed turn is a peer
        # of a spoken one and reads back through exactly the same renderer
        # (AC-162): one function, or the two drift and the second is the one
        # nobody looks at.
        def dump_transcript():
            items = page.eval_on_selector_all(
                "#history .histitem",
                """els => els.map(el => ({
                     said: (el.querySelector(".said") || {}).textContent || "",
                     answered: (el.querySelector(".answered")
                                || {}).textContent || "",
                     unspoken: [...el.querySelectorAll(".answered .unspoken")]
                                 .map(s => s.textContent.trim()),
                     pieces: el.querySelectorAll(".answered span").length,
                     pending: el.classList.contains("pending"),
                     when: (el.querySelector(".turn.user time")
                            || {}).textContent || "",
                     tier: (el.querySelector(".turn.bot .tiertag")
                            || {}).textContent || "",
                   }))""")
            if not items:
                print("  MISSING: no history item was written at all")
            # OLDEST FIRST IN THE DOM. The transcript grows DOWNWARD now
            # (AC-161): `historyItem` appends and the trim drops `firstChild`,
            # which is the reverse of the hero page, whose fold put the newest
            # turn on top. So this prints in document order and the LAST line
            # is the most recent turn -- a run that stopped after the first is
            # still obvious, which is the failure this probe is for (D-1:
            # every terminal path re-arms).
            for i, item in enumerate(items, 1):
                print(f"  turn {i}: said={item['said']!r} "
                      f"at {item['when']!r}")
                print(f"          answered={item['answered']!r} "
                      f"pieces={item['pieces']} unspoken={item['unspoken']} "
                      f"tier={item['tier']!r} pending={item['pending']}")
            answered = sum(1 for i in items if i["answered"].strip())
            print(f"  {len(items)} turn(s) shown, {answered} answered")
            state = page.query_selector("#stateKicker")
            notice = page.query_selector("#notice")
            print(f"  ended in state {(state.inner_text().strip() if state else '?')!r}"
                  f", notice {(notice.inner_text().strip() if notice else '')!r}")
            return items

        if TURN:
            print(f"  --turn: listening to {WAKE_WAV.name} for "
                  f"{TURN_WAIT_MS / 1000:.0f}s")
            page.wait_for_timeout(TURN_WAIT_MS)
            print("=== transcript ===")
            dump_transcript()

        if TYPED:
            # A TYPED TURN (AC-162, AC-163). The page sends
            # `{"type":"typed", text}` and everything past that frame is the
            # path a spoken utterance already takes. Two things are only
            # visible from here and neither is on the wire: that the turn is
            # RENDERED as an ordinary turn in the same transcript, and that
            # the page adopted a turn id it never minted -- a typed turn sends
            # no `capture_start`, so the `final` frame is the first thing that
            # names the turn, and the adoption runs ahead of the `stale()`
            # check that would otherwise throw the frame away.
            print("=== a typed turn (AC-162, AC-163) ===")
            before_n = page.eval_on_selector_all(
                "#history .histitem", "els => els.length")
            page.fill("#input", TYPED_TEXT)
            page.wait_for_timeout(200)
            page.focus("#input")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            straight_after = page.evaluate("""() => ({
              draft: document.getElementById('input').value,
              state: document.documentElement.dataset.state,
              turn: (document.getElementById('hTurn') || {}).textContent,
              errors: (document.getElementById('errors') || {}).textContent,
            })""")
            print(f"  sent {TYPED_TEXT!r}; box is now {straight_after['draft']!r}, "
                  f"state {straight_after['state']!r}, "
                  f"hud turn {straight_after['turn']!r}")
            if (straight_after["errors"] or "").strip():
                print(f"  ERRORS ON THE PAGE: {straight_after['errors']!r}")
            page.wait_for_timeout(TYPED_WAIT_MS)
            items = dump_transcript()
            print(f"  turns went {before_n} -> {len(items)}")
            mine = [i for i in items if TYPED_TEXT in i["said"]]
            verdict(mine, "the typed message is in the transcript as an "
                          "ordinary turn")
            if mine:
                verdict(mine[-1]["answered"].strip(),
                        "the typed turn was answered")
            print(f"  hud turn id after the round trip: " + str(
                page.eval_on_selector("#hTurn", "e => e.textContent")))

        page.screenshot(path="test/manual/page.png", full_page=True)
        print("screenshot -> test/manual/page.png")
        ctx.close()
        browser.close()
    # THE EXIT CODE ANSWERS FOR EVERY VERDICT, THE ELEMENTS AND THE ERRORS.
    #
    # It answered only for uncaught page errors until 2026-09-10, so renaming
    # `#micBtn` -- which takes the microphone with it, and until AC-175 took
    # the socket, the restored conversation and every daemon-supplied value
    # too -- printed MISSING in the middle of a long report and still exited 0.
    # The elements were added then; the `OK `/`BAD` lines were not, so fifteen
    # checks that arrived with the project rail were still prints in a run that
    # exited 0 (MEASURED 2026-09-11). Both holes are the same hole.
    # AGENTS.md's rule for a review agent is read the exit code, never the
    # output text; that rule needs the exit code to mean something.
    #
    # What stays a REPORT: the counts, the measurements, the settings tables
    # and the transcript. A number with no expectation attached cannot fail,
    # and inventing one here would be this file deciding something the owner
    # has not.
    if bad:
        print(f"\nFAIL: {len(bad)} check(s) did not hold:")
        for line in bad:
            print(f"  BAD {line}")
    if missing:
        print(f"\nFAIL: {len(missing)} expected element(s) missing: {missing}")
    if errors:
        print(f"\nFAIL: {len(errors)} uncaught page error(s)")
    return 1 if (errors or missing or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
