"""The acceptance instrument for docs/satellite-ux.md AC-91..AC-106, and for
the four criteria docs/familiar-ui.md added that only a browser can reach
(AC-168, AC-169, AC-170, AC-171).

Sibling of probe_page.py, which covers the settings surface's own navigation.
It exists because both docs/satellite-ux.md section 9 and web/AGENTS.md name
these checks: a verification section is supposed to reflect a check somebody
can run.

Every row prints the BEFORE number measured on the page these criteria were
written against, the number required, and what it got -- so a regression reads
as "was 42, want 0, got 7" rather than as a bare failure.

UPDATED 2026-09-10 for the reshape that made the conversation the page
(docs/familiar-ui.md step 5, AC-172). Every criterion this file asserted
before that day it still asserts, with the same BEFORE number; what moved is
where the element lives. Two are worth naming here rather than in a diff:

  * `#micWrap button[data-mic]` is GONE and there is ONE mic control,
    `#micBtn`, in the composer. That selector matched nothing after the
    reshape, so the click that arms the microphone SILENTLY did not happen,
    the socket never opened, no conversation was restored, and AC-94 reported
    itself SKIPPED rather than failed. A dead selector in a probe is an
    acceptance criterion quietly leaving the room.
    THAT CLICK IS NOW GONE ON PURPOSE (AC-175, `docs/mic-optional.md`): the
    socket is opened at boot, so AC-94 reads a conversation restored onto a
    page that has never touched a microphone. `MIC` survives for AC-103,
    which measures where the control sits rather than pressing it.
  * AC-104's colour comparison was comparing STRINGS and is now comparing
    PIXELS. See the long comment at `sw_style`; the check was passing on this
    markup while the two colours were numerically identical, which is exactly
    the D-13 trap the previous version of that comment was written about.

It is READ-ONLY and must stay that way. It writes no setting, clicks nothing
that commits, and drives no turn, which is what makes it safe to point at a
Butler that is running for real. Two checks touch a control and neither
commits: AC-104 sets a checkbox PROPERTY without dispatching `change`, and
AC-171 toggles a CSS class on a row. That property is no longer taken on
trust -- the run now SNAPSHOTS `/api/settings`, `/api/mode` and
`/api/shortcuts` at the start and diffs them at the end, and reports the diff
as a check like any other.

  python -X utf8 test/manual/probe_ux.py https://127.0.0.1:<port>/

THE MARKDOWN FIXTURE (AC-94, the renderer rewritten 2026-09-10)
---------------------------------------------------------------
The closed element set grew from `strong em code pre>code ul ol li` to add
`h1 h2 h3 p br blockquote hr table thead tbody tr th td` and lists nested
inside an `li`, and a reply is now RE-RENDERED WHOLE on every streamed piece
instead of each piece being rendered into its own span. Both halves need a
document containing every one of those blocks, and neither can be measured
against whatever conversation a daemon happens to be holding.

So the document is a FIXTURE and this probe does not write it -- writing one
into the owner's live `state/chats/` would put an invented conversation in
their sidebar, which is the read-only rule in a second place. It is planted
by a SEPARATE, LOUDLY NAMED run against a SCRATCH state directory, before
that daemon boots (`ChatStore._attach` picks the newest record at boot):

  python -X utf8 test/manual/probe_ux.py --plant=E:\\scratch\\butler\\state
  set BUTLER_STATE=E:\\scratch\\butler\\state
  set BUTLER_CONFIG=E:\\scratch\\butler\\no-such-config.json
  .venv\\Scripts\\python.exe -X utf8 daemon\\butlerd.py --port 8177
  python -X utf8 test/manual/probe_ux.py https://127.0.0.1:8177/

`--plant` REFUSES the repository's own `state/`. The daemon must be in
`brain_mode` `harness_only` (or `harness`) or `GET /api/session/current`
answers 404 and nothing is restored at all -- the same trap AC-94 and AC-169
already record above -- and it must have `hotkeys_enabled: false`, or its desk
agent takes the owner's real keyboard (test/AGENTS.md). `--plant` writes both
into a `settings.json` it creates, and leaves one that already exists alone.
No model, no harness child and no audio are involved: `harness_only` starts a
coding agent on the first TURN, and this probe drives none.

Without the fixture the rich markdown checks say SKIP rather than passing on
a conversation that happens to contain no table.

THE STREAMING CASE, and why the restore path can reach it
---------------------------------------------------------
The regression that earned the rewrite is a list whose items arrive in
DIFFERENT streamed pieces coming out as three separate `ul` elements of one
`li` each. A probe cannot stream without a brain -- but it does not have to.
`renderMessages` calls `logReply` once per assistant message and `openItem`
survives between them, so N CONSECUTIVE ASSISTANT MESSAGES in one chat record
reach `logReply` exactly as N `reply` frames do: same function, same
accumulation onto `openItem.raw`, same whole-answer re-render. The fixture's
second turn is therefore four assistant messages that between them open a
list and a fence and close them, and the check is that the DOM holds one of
each. What this does NOT exercise is the socket path in `onMessage` and the
`tier`/`spoken` arguments a live `reply` carries; `test_typed_turn.py` and
`probe_page.py --typed` are where a real streamed turn is driven.

D-13, EXECUTED 2026-09-10, exit codes and FAIL lines only. Every mutation was
applied to a SCRATCH COPY of `web/app.js` served through a scratch daemon with
`butlerd.WEB_DIR` repointed -- never to the repository file, which another
agent owned that hour (the same trick `test_protocol_doc.py`'s witness used on
`hub.py`). Baseline 60/60 exit 0; restore 60/60 exit 0. Seven aimed
mutations, 7 of 7 caught:

  * `logReply` renders the PIECE instead of the accumulated answer -> 57/60,
    exit 1, and the three red rows are the streaming ones. "holding all three
    of its items" stays GREEN and that is honest rather than a gap: the item
    COUNT was always right, only the grouping was wrong.
  * a pipe row is never a table -> 56/60. Four red, and the fourth is
    COUPLING that is worth knowing: the pipe rows fall into a paragraph and
    their newlines become `br`, so the soft-break check goes red at 5.
  * a deeper indent does not nest -> 58/60, the two list rows.
  * a heading line is not a heading -> 57/60, the three heading rows. Note
    what it did NOT prove: the literal-`##` half of the first row came back
    False too, because `mdParagraph` still BREAKS on a heading line, so the
    text vanished instead of surviving. The witness for that half is the run
    against the whole previous renderer, where it was True.
  * a paragraph is built as a span -> 59/60, exactly one row.
  * a fence's body reaches `innerHTML` -> 57/60: the fence-is-text row, the
    new anchors/images row, AND the pre-existing "no links or images" row,
    which had no witness at all until this fixture gave it a source
    containing one of each.
  * an inline code span is built as an `a` -> 57/60, the anchor half.

The whole previous renderer was also served to the same fixture, which is
where every BEFORE number below comes from: 46/60, exit 1, 14 of the 16 new
rows red. The two that stayed green are the fence that arrives in ONE message
(it was already one `pre`) and inline emphasis.

AC-91 and AC-93 are NOT in the default run and cannot be: they need the daemon
to die underneath a page that has already loaded, which is a two-process dance
this script cannot do to itself. Drive them by hand -- load the page, arm the
mic, stop the daemon by the PID that started it (never by name; AGENTS.md
records what that cost), and read the header chip, the sidebar foot, the
composer microcopy and `data-state`. The BEFORE is `standing by` / `Listening`
/ "Armed and quiet." / `idle` for the whole outage; anything still saying that
is a regression.

AC-94 needs a daemon whose brain HOLDS A CONVERSATION (`brain_mode` =
`harness` or `harness_only`). With the fake brain `GET /api/session/current`
answers 404, there is nothing to restore, and the markdown, timestamp and
blank-answer checks have no turns to look at -- they say SKIP rather than
passing on an empty page.

The URL must be `https` -- the daemon serves TLS whenever ssl/cert.pem exists,
and a browser stopped at the certificate interstitial reports a page with NONE
of its elements, which reads exactly like the broken module probe_page.py was
written to catch.
"""
import json
import sys
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

URL = next((a for a in sys.argv[1:] if not a.startswith("--")),
           "https://127.0.0.1:8199/")
# Screenshots land beside the other probes' output, gitignored by
# `test/manual/page-*.png`.
OUT = Path(__file__).resolve().parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# The one mic control (AC-167, docs/familiar-ui.md 6.4). It keeps `data-mic`,
# so the attribute still identifies it, but there is no `#micWrap` and no
# second treatment to pick between.
MIC = "#micBtn[data-mic]"

# Read-only proof: everything this run must leave exactly as it found it.
WATCHED = ("/api/settings", "/api/mode", "/api/shortcuts")

rows = []

# --- the markdown fixture (AC-94) ------------------------------------------ #
# One conversation that contains every block the renderer is allowed to build,
# plus the two things it must REFUSE to build. It is found on the page by this
# marker in the first thing the owner said, so a daemon holding some other
# conversation SKIPs the rich checks instead of measuring nothing.
MD_MARK = "BUTLER_MD_FIXTURE_V1"

# The literal inside the fence. It is a real `img` tag on purpose: the claim is
# that a fenced block's contents are TEXT, so if anything below ever reached
# innerHTML this string becomes an element and the page issues an outbound
# request authored by whatever the coding agent read on disk.
MD_IN_FENCE = "<img src=x onerror=BUTLER_MD_NOT_HTML>"

# THE SOURCE CONTAINS A LINK, AN IMAGE AND A `javascript:` LINK (the last
# paragraph). The renderer MAKES LINKS since `mdLink` (an http(s) href only,
# a new tab, `noopener noreferrer`), so "zero anchors" stopped being the rule
# and became a stale check that failed on correct output (2026-09-26). The
# boundary that survives is sharper: NO image is ever an element, EVERY anchor
# is http(s) and opens a new tab with no opener and no referrer, a
# `javascript:` link is never an anchor, and nothing inside a fence is. The
# bad link is what gives the anchor half its teeth -- without it "every anchor
# is safe" cannot fail.
MD_BLOCKS = "\n".join([
    "# Butler",
    "",
    "## Ways to reach Butler",
    "",
    "Butler answers on the desk and on the tablet.",
    "Both use the same daemon.",
    "",
    "A second paragraph after a blank line.",
    "",
    "### Tiers",
    "",
    "#### A fourth-level heading folds into h3",
    "",
    "- first bullet",
    "- second bullet",
    "- third bullet",
    "  - a nested item",
    "  - another nested item",
    "",
    "1. numbered one",
    "2. numbered two",
    "",
    "| tier | latency |",
    "| --- | --- |",
    "| reflex | 20 ms |",
    "| router | 120 ms |",
    "",
    "```html",
    MD_IN_FENCE,
    "[not a link](https://example.com/inside)",
    "```",
    "",
    "> a quoted line",
    "> a second quoted line",
    "",
    "---",
    "",
    "See [the docs](https://example.com/docs) and "
    "![a picture](https://example.com/p.png) -- the picture may never become "
    "an element, and [this](javascript:alert(1)) may never become a link.",
    "",
    "Inline **bold**, *em* and `code`.",
])

# Four pieces of ONE answer. Each carries its own structural newline, which is
# what the daemon's sentence splitter does and what `logReply`'s space-joining
# rule expects; a piece ending mid-line would legitimately be glued to the next
# one with a space. Between them they open a list and a fence in one piece and
# close them in a later one, which is exactly the shape that used to come out
# as three `ul` elements and two `pre`.
MD_PIECES = [
    "Three things:\n\n- alpha\n",
    "- beta\n",
    "- gamma\n\n```txt\nfenced line one\n",
    "fenced line two\n```\n",
]

MD_SAID = f"show me every markdown block you can render ({MD_MARK})"
MD_SAID_2 = "now split a list and a code fence across the pieces"


def plant(state_dir: str) -> int:
    """Write the markdown fixture into a SCRATCH `BUTLER_STATE`. MUTATING.

    Named loudly because it writes: a diagnostic that edits configuration has
    to say so in its own name (AGENTS.md). It touches the FILESYSTEM and never
    a running daemon -- there is no request here -- but a chat record planted
    under the owner's live state would appear in their sidebar as a
    conversation they never had, so the repository's own `state/` is refused.

    Run it BEFORE the scratch daemon boots. `ChatStore._attach` picks the
    record with the newest `updatedAt` at construction time, so a fixture
    written afterwards is on disk and is not the conversation the page
    restores.
    """
    root = Path(state_dir).expanduser().resolve()
    live = (Path(__file__).resolve().parents[2] / "state").resolve()
    if root == live:
        print(f"REFUSED: {root} is this repository's own state/, which is the "
              f"owner's live daemon. Point --plant at a scratch BUTLER_STATE.")
        return 2

    now = int(time.time() * 1000)
    messages = []

    def add(role: str, content: str) -> None:
        messages.append({"id": f"msg-{MD_MARK}-{len(messages)}", "role": role,
                         "content": content, "timestamp": now + len(messages)})

    add("user", MD_SAID)
    add("assistant", MD_BLOCKS)
    add("user", MD_SAID_2)
    for piece in MD_PIECES:
        add("assistant", piece)

    chats = root / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    cid = f"chat-{now}-mdfix"
    record = chats / f"{cid}.json"
    record.write_text(json.dumps(
        {"id": cid, "createdAt": now, "updatedAt": now + len(messages),
         "harness": "fake", "sessionRef": "", "messages": messages},
        indent=1), encoding="utf-8")
    print(f"wrote {record}")
    print(f"  {len(messages)} messages, {sum(1 for m in messages if m['role'] == 'user')} turns")

    prefs = root / "settings.json"
    if prefs.exists():
        print(f"LEFT ALONE: {prefs} already exists. It MUST contain "
              f'"brain_mode": "harness_only" (or "harness") or '
              f"GET /api/session/current answers 404 and nothing is restored, "
              f'and "hotkeys_enabled": false or this daemon takes the owner\'s '
              f"real keyboard.")
    else:
        prefs.write_text(json.dumps(
            {"brain_mode": "harness_only", "hotkeys_enabled": False},
            indent=1), encoding="utf-8")
        print(f"wrote {prefs} (brain_mode harness_only, hotkeys OFF)")
    return 0

# Transitions do not COMPLETE in headless Chrome -- measured on 2026-09-10,
# a 300 ms transition still reported playState "running" with the start value
# in the computed style 3.5 s after it began, because finishing needs a
# committed frame and headless produces none. Anything read through
# getComputedStyle on a property this page transitions must therefore finish
# its animations first, or the measurement is of the value BEFORE the change.
SETTLE = """(el) => {
  for (const a of el.getAnimations({ subtree: true })) {
    try { a.finish(); } catch (e) { /* a paused or infinite one cannot */ }
  }
}"""

# A colour compared as a STRING is not compared at all. Chrome serialises a
# declared `oklch(...)` as oklch and an interpolated one as oklab, and
# oklch(0.62 0.02 275) and oklab(0.62 0.00174311 -0.0199239) are THE SAME
# COLOUR -- so a string comparison reports "they differ" about two identical
# pixels. This paints the colour into a 1x1 canvas and returns the RGBA bytes,
# which is the only comparison that cannot be fooled by a re-serialisation.
RGBA = """(css) => {
  const c = document.createElement("canvas");
  c.width = 1; c.height = 1;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.fillStyle = "rgba(1, 2, 3, 0.004)";        // sentinel
  g.fillStyle = css;
  if (g.fillStyle === "rgba(1, 2, 3, 0.004)") return "unparsed:" + css;
  g.clearRect(0, 0, 1, 1);
  g.fillRect(0, 0, 1, 1);
  return [...g.getImageData(0, 0, 1, 1).data].join(",");
}"""


def check(ac, claim, before, want, got):
    ok = want == got
    rows.append((ok, ac, claim, before, want, got))
    print(f"{'PASS' if ok else 'FAIL'}  {ac:<7} {claim}")
    print(f"        was {before!r} -> want {want!r}, got {got!r}")


def snapshot(page):
    """What the daemon holds, so the end of the run can prove it still does."""
    out = {}
    for path in WATCHED:
        r = page.request.get(URL.rstrip("/") + path)
        out[path] = r.text() if r.ok else f"HTTP {r.status}"
    return out


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=CHROME,
            args=["--use-fake-ui-for-media-stream",
                  "--use-fake-device-for-media-stream"])
        ctx = browser.new_context(ignore_https_errors=True,
                                  permissions=["microphone"],
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(2500)
        before_state = snapshot(page)

        # ---- AC-105: the page names the wake phrase before it is armed ----
        # `#stateDesc` is the first microcopy line under the composer now and
        # `#orbLabel` the second; both are painted by the same `paint()` that
        # wrote them under the hero's headline, so the words did not change.
        off = page.evaluate("""() => ({
          desc: document.getElementById('stateDesc').textContent,
          orb: document.getElementById('orbLabel').textContent,
        })""")
        names_wake = "hey jarvis" in (off["desc"] + off["orb"]).lower()
        check("AC-105", "wake phrase named before the mic is armed",
              "Arm the microphone to start listening for the wake word.",
              True, names_wake)
        print(f"        desc now: {off['desc']!r}")

        # ---- AC-170: the empty state teaches the wake phrase ----------------
        # NEW. The BEFORE is the hero page, which had no empty state at all: a
        # first-time user saw a headline and a microphone and was given
        # nothing to do.
        #
        # IT USED TO BE READ HERE BECAUSE THE SOCKET WAS NOT OPEN YET -- arming
        # the microphone was what opened it, and a restored conversation
        # replaces the empty state with a transcript, so "before the click" and
        # "no conversation" were the same moment. AC-175 separated them: the
        # socket now opens at boot, so on a daemon that HOLDS a conversation
        # this page is never empty at any point a probe can look at. The
        # criterion is unchanged and the evidence for it moved, which is the
        # AC-172 rule -- so it is asserted only when there is genuinely nothing
        # to restore, and SKIPPED with the reason otherwise. Skipping loudly is
        # the point: silently passing on a page that has a transcript is how
        # AC-94 lost its evidence to a dead selector.
        # `#emptyChips` is a NEW sibling of `#histEmpty`, not a descendant of
        # it (mocks/q-familiar.html item 5, `web/AGENTS.md` "THE CHAT-FIRST
        # COMPOSER AND HEADER" phase 2): the chips moved down to sit directly
        # above the composer, ChatGPT's own position for them. The old
        # `#histEmpty .exchip` selector always returns zero on this markup,
        # which is exactly the dead-selector failure this file's own docstring
        # names for `#micWrap` -- a criterion quietly passing on "0 of 0"
        # rather than failing loudly.
        empty = page.evaluate("""() => {
          const e = document.getElementById('histEmpty');
          const chipsRoot = document.getElementById('emptyChips');
          const chips = [...document.querySelectorAll('#emptyChips .exchip')];
          return {
            shown: !!e && e.offsetParent !== null,
            chipsShown: !!chipsRoot && chipsRoot.offsetParent !== null,
            // The wake phrase is named on the line under the suggestions
            // since Step 3c ("Say hey jarvis, or type.", AC-692).
            wake: /hey jarvis/i.test((e ? e.textContent : '')
                                     + (chipsRoot ? chipsRoot.textContent : '')),
            centre: (() => {
              const c = document.getElementById('composer').getBoundingClientRect();
              const col = document.querySelector('.col').getBoundingClientRect();
              return Math.abs((c.top + c.height / 2) - (col.top + col.height / 2))
                     / col.height;
            })(),
            chips: chips.length,
            chipButtons: chips.filter(c => c.tagName === 'BUTTON').length,
            turns: document.querySelectorAll('#history .histitem').length,
            text: e ? e.textContent.replace(/\\s+/g, ' ').trim().slice(0, 90) : '',
          };
        }""")
        if empty["turns"]:
            print(f"  SKIP  AC-170 -- this daemon restored a conversation "
                  f"({empty['turns']} turn(s)), so there is no EMPTY one to "
                  f"look at. The socket opens at boot now (AC-175), so run "
                  f"this against a daemon holding no conversation.")
        else:
            check("AC-170", "an empty conversation shows a teaching screen",
                  "no empty state at all", True, empty["shown"])
            check("AC-170", "it names the wake phrase where a new user reads it",
                  False, True, empty["wake"])
            check("AC-170",
                  "its example prompts are real buttons, not decoration",
                  0, empty["chips"], empty["chipButtons"])
            check("AC-692", "three suggestions under the centred composer",
                  4, 3, empty["chips"])
            check("AC-692", "the composer is centred (within 10% of the column)",
                  "at the bottom", True, empty["centre"] <= 0.10)
            # `#histEmpty` and `#emptyChips` are two elements driven by ONE
            # writer, `setEmptyState()` -- a defect already shipped where they
            # disagreed on first paint because each had its own call site
            # (web/AGENTS.md: "NOT hidden, and that is the whole of a defect
            # worth one line"). This is the check that would have caught it:
            # a page that shows the question but not the chips, or the
            # reverse, satisfies every check above it in this block.
            check("AC-170",
                  "the empty state and its example chips agree "
                  "(one writer, setEmptyState)",
                  "disagreed on first paint (shipped defect)",
                  empty["shown"], empty["chipsShown"])
            print(f"        empty state: {empty['text']!r} "
                  f"({empty['chips']} chips)")

        # ---- AC-168: the state is named in WORDS, always visible ------------
        # NEW, and the reason it is a criterion: in the light theme gold reads
        # as brown, so `thinking` and `speaking` are one hue and a page whose
        # only signal is a colour cannot say which of the two it is in. Three
        # places carry the word and all three must be non-empty with the
        # microphone off, which is the state that used to say the least.
        words = page.evaluate("""() => ({
          chip: (document.getElementById('stateKicker')
                 || {}).textContent || '',
          box: (document.getElementById('inStateWord') || {}).textContent || '',
          boxShown: !document.getElementById('inState').hidden,
          title: (document.getElementById('stateTitle') || {}).textContent || '',
          desc: (document.getElementById('stateDesc') || {}).textContent || '',
          attr: document.documentElement.dataset.state || '',
          chipShown: !!document.getElementById('stateChip')
                     && document.getElementById('stateChip').offsetParent !== null,
        })""")
        # The wave band and its word retired (CU-3); the box says the state
        # inside itself while Butler hears, thinks or speaks.
        named = [k for k in ("chip", "title", "desc") if words[k].strip()]
        check("AC-168", "the state is written in words in every always-on slot",
              "hue only in the light theme", 3, len(named))
        check("AC-168", "the header state chip is visible", True, True,
              words["chipShown"])
        check("AC-168", "the pill's word and the box's word agree when shown",
              "n/a", True, (not words["boxShown"])
              or words["chip"].strip() == words["box"].strip())
        print(f"        chip={words['chip']!r} box={words['box']!r} "
              f"title={words['title']!r} data-state={words['attr']!r}")

        # ---- AC-102: announced regions ----
        a11y = page.evaluate("""() => ({
          errors: document.getElementById('errors').getAttribute('role'),
          notice: document.getElementById('notice').getAttribute('role'),
          reply: document.getElementById('reply').getAttribute('aria-live'),
        })""")
        check("AC-102", "#errors is an alert region", None, "alert",
              a11y["errors"])
        check("AC-102", "#notice is a status region", None, "status",
              a11y["notice"])
        # `#reply` is now `.sronly` -- a visually hidden live region rather
        # than a drawn paragraph, because the visible answer is the transcript
        # bubble. The criterion is about what is ANNOUNCED, so it is unchanged.
        check("AC-102", "#reply is a polite live region", None, "polite",
              a11y["reply"])

        # ---- AC-106: install target ----
        pwa = page.evaluate("""() => ({
          manifest: (document.querySelector('link[rel=manifest]')||{}).href
                    || null,
          apple: document.querySelectorAll(
            'meta[name^="apple-mobile-web-app"]').length,
        })""")
        check("AC-106", "apple-mobile-web-app meta tags", 0, 3, pwa["apple"])
        check("AC-106", "a manifest is linked", False,
              True, bool(pwa["manifest"]))
        if pwa["manifest"]:
            r = page.request.get(pwa["manifest"])
            body = r.json() if r.ok else {}
            check("AC-106", "the manifest is served and parses", "n/a",
                  True, r.ok and body.get("display") == "standalone")
            icon = body.get("icons", [{}])[0].get("src")
            if icon:
                ir = page.request.get(pwa["manifest"].rsplit("/", 1)[0]
                                      + "/" + icon)
                check("AC-106", "its icon is served", "n/a", True, ir.ok)

        # ---- AC-94: markdown, timestamps, pending ----
        # THE MIC CLICK IS GONE, AND ITS ABSENCE IS THE ASSERTION (AC-175,
        # `docs/mic-optional.md`). It used to be here because arming was what
        # opened the socket -- `connect()` ran at the very end of `start()`,
        # after getUserMedia resolved, so with no click there was no `ready`
        # frame and `loadSession` never ran. The socket is opened at boot now,
        # so a restored conversation on a page that has never touched a
        # microphone is exactly what this block reads. Clicking anyway would
        # make the criterion pass either way, which is how it lost its
        # evidence the last time (the dead `#micWrap` selector).
        page.wait_for_timeout(4500)
        hist = page.evaluate("""() => ({
          items: document.querySelectorAll('#history .histitem').length,
          strong: document.querySelectorAll('#history .answered strong').length,
          em: document.querySelectorAll('#history .answered em').length,
          code: document.querySelectorAll('#history .answered code').length,
          // AC-632 (docs/rich-chat.md RC-8): img, audio and video exist
          // ONLY as artifacts.js builds them, with a src on Butler's own
          // artifact route. Counted as the BAD ones, so a shown image passes
          // and an image from model text fails.
          badImages: [...document.querySelectorAll(
              '#history img, #history audio, #history video')].filter(e =>
            !(e.getAttribute('src') || '').startsWith('/api/artifacts/')).length,
          // An artifact's links (open full size, download) point at the same
          // route and nowhere else.
          badArtLinks: [...document.querySelectorAll('#history .arts a')]
            .filter(a => !(a.getAttribute('href') || '')
                           .startsWith('/api/artifacts/')).length,
          // Any other anchor is allowed only as `mdLink` builds it (AC-94, see
          // the fixture's comment): http(s), a new tab, no opener, no referrer.
          badLinks: [...document.querySelectorAll('#history a')].filter(a =>
            !a.closest('.arts')).filter(a =>
            !/^https?:/i.test(a.getAttribute('href') || '')
            || a.target !== '_blank' || !/noopener/.test(a.rel)
            || !/noreferrer/.test(a.rel)).length,
          times: document.querySelectorAll('#history .meta time').length,
          rawAsterisk: [...document.querySelectorAll('#history .answered')]
            .filter(e => /\\*\\*/.test(e.textContent)).length,
          blankAnswers: [...document.querySelectorAll('#history .histitem')]
            .filter(e => !(e.querySelector('.answered')||{}).textContent
                          .trim()).length,
        })""")
        print("        history: " + json.dumps(hist))
        if hist["items"]:
            check("AC-94", "assistant markdown is rendered, not literal",
                  "raw ** in the answer", 0, hist["rawAsterisk"])
            # One <time> per turn still, and it is on the USER half of the
            # turn: a `.histitem` now holds two `article.turn` elements with a
            # `.meta` row each, and only the question is stamped.
            check("AC-94", "every turn carries a timestamp", 0,
                  hist["items"], hist["times"])
            check("AC-94", "no image except an artifact's on /api/artifacts/ "
                  "(AC-632), artifact links stay on that route, and every "
                  "other link is http(s) in a new tab with noopener "
                  "noreferrer", "n/a", (0, 0, 0),
                  (hist["badImages"], hist["badArtLinks"], hist["badLinks"]))
            check("AC-94", "no turn renders a blank answer", 1, 0,
                  hist["blankAnswers"])

            # ---- AC-169: per-message copy, KEYBOARD reachable -------------
            # NEW. Two per turn -- one on the question, one on the answer --
            # and the criterion is not "a copy button exists" but "it is not
            # hover-only", because on a tablet a hover-only control is no
            # control. So: a real <button> in the tab order, VISIBLE at rest
            # (opacity above zero rather than display:none), and brought fully
            # up by focus as well as by hover.
            #
            # THE SELECTOR IS NEGATED, and it has to be. `.saybtn` (AC-183),
            # `.rewindbtn` (AC-194) and now `.retrybtn`
            # (mocks/q-familiar.html item 4) all REUSE the `.copybtn`
            # treatment -- deliberately, because AC-169's argument about
            # hover-only controls applies to all of them and a second copy of
            # those rules would drift. A bare `.copybtn` therefore matched
            # more controls per turn than a criterion counting two, and
            # reported a FAILURE for a page that had gained a feature -- most
            # recently `.retrybtn`, which inflated this by one per bot turn
            # until it was named here too. The lesson is the same one the dead
            # `#micWrap` selector taught from the other direction: a probe's
            # selector is part of the criterion.
            copy = page.evaluate("""(SETTLE) => {
              const settle = eval("(" + SETTLE + ")");
              const bs = [...document.querySelectorAll(
                '#history .copybtn:not(.saybtn):not(.rewindbtn)'
                + ':not(.retrybtn)')];
              const first = bs[0];
              if (!first) return {n: 0};
              settle(document.documentElement);
              const rest = parseFloat(getComputedStyle(first).opacity);
              first.focus();
              settle(document.documentElement);
              const focused = parseFloat(getComputedStyle(first).opacity);
              const named = bs.filter(b => b.getAttribute('aria-label')).length;
              return {
                n: bs.length,
                buttons: bs.filter(b => b.tagName === 'BUTTON').length,
                tabbable: bs.filter(b => b.tabIndex >= 0 && !b.disabled).length,
                named: named,
                rest: rest,
                focused: focused,
                isActive: document.activeElement === first,
              };
            }""", SETTLE)
            print("        copy: " + json.dumps(copy))
            check("AC-169", "a copy control on every message",
                  0, hist["items"] * 2, copy.get("n"))
            check("AC-169", "each one is a real button in the tab order",
                  0, copy.get("n"), copy.get("tabbable"))
            check("AC-169", "each one has an accessible name",
                  0, copy.get("n"), copy.get("named"))
            check("AC-169", "it is not hover-only: visible at rest",
                  "no control at all", True, copy.get("rest", 0) > 0)
            check("AC-169", "keyboard focus reaches it and brings it up",
                  "no control at all", True,
                  bool(copy.get("isActive"))
                  and copy.get("focused", 0) >= copy.get("rest", 0))

            # ---- AC-183 and AC-194: the two controls beside it -------------
            # Counted here because they share `.copybtn`'s treatment and would
            # otherwise be invisible to every check in this file -- and both
            # are one-sided by RULING: replay is Butler's messages only
            # (replaying your own question is not a thing anyone wants), and
            # rewind is the owner's only (an assistant message is not a point
            # a conversation can be continued FROM).
            sides = page.evaluate("""() => ({
              say: document.querySelectorAll('#history .turn.bot .saybtn').length,
              sayOnUser: document.querySelectorAll('#history .turn.user .saybtn').length,
              rewind: document.querySelectorAll('#history .turn.user .rewindbtn').length,
              rewindOnBot: document.querySelectorAll('#history .turn.bot .rewindbtn').length,
              armed: document.querySelectorAll('#history .rewindbtn[data-msg]').length,
            })""")
            print("        sides: " + json.dumps(sides))
            check("AC-183", "a play control on every answer",
                  "none at all", hist["items"], sides.get("say"))
            check("AC-183", "and none on the owner's own messages",
                  "n/a", 0, sides.get("sayOnUser"))
            check("AC-194", "a rewind control on every question",
                  "none at all", hist["items"], sides.get("rewind"))
            check("AC-194", "and none on Butler's answers",
                  "n/a", 0, sides.get("rewindOnBot"))
            check("AC-196", "each one carries an id out of Butler's record",
                  "n/a", hist["items"], sides.get("armed"))

            # ---- RETRY: a new P0 affordance, and nothing measured it -------
            # `.retrybtn` (mocks/q-familiar.html item 4, `web/AGENTS.md` "THE
            # CHAT-FIRST COMPOSER AND HEADER") is the third action beside copy
            # and speak, on Butler's turns only (a question is not a point a
            # conversation can be re-asked FROM). It shares `.copybtn`'s
            # treatment -- rest opacity 0.42, brought up on hover AND
            # focus-within, 36x36 -- so it gets the SAME three checks AC-169
            # already runs on the plain copy control, rather than a new set of
            # rules that could drift from them.
            retry = page.evaluate("""(SETTLE) => {
              const settle = eval("(" + SETTLE + ")");
              const bs = [...document.querySelectorAll(
                '#history .turn.bot .retrybtn')];
              const bsOnUser = document.querySelectorAll(
                '#history .turn.user .retrybtn').length;
              const first = bs[0];
              if (!first) return {n: 0, onUser: bsOnUser};
              settle(document.documentElement);
              const r = first.getBoundingClientRect();
              const rest = parseFloat(getComputedStyle(first).opacity);
              first.focus();
              settle(document.documentElement);
              const focused = parseFloat(getComputedStyle(first).opacity);
              return {
                n: bs.length, onUser: bsOnUser,
                tabbable: bs.filter(b => b.tabIndex >= 0 && !b.disabled).length,
                named: bs.filter(b => b.getAttribute('aria-label')).length,
                w: Math.round(r.width), h: Math.round(r.height),
                rest: rest, focused: focused,
                isActive: document.activeElement === first,
              };
            }""", SETTLE)
            print("        retry: " + json.dumps(retry))
            check("RETRY", "a Try again control on every answered turn",
                  "no such control (new affordance)",
                  hist["items"], retry.get("n"))
            check("RETRY", "and none on the owner's own messages",
                  "n/a", 0, retry.get("onUser"))
            check("RETRY", "each one is a real button in the tab order",
                  0, retry.get("n"), retry.get("tabbable"))
            check("RETRY", "each one has an accessible name",
                  0, retry.get("n"), retry.get("named"))
            check("RETRY", "it rests VISIBLE, not hover-only (AC-169's 0.42)",
                  "no control at all", True, retry.get("rest", 0) > 0)
            check("RETRY", "its target is at least 36x36 (AC-169's size)",
                  "no control at all", True,
                  retry.get("w", 0) >= 36 and retry.get("h", 0) >= 36)
            check("RETRY", "keyboard focus reaches it and brings it up",
                  "no control at all", True,
                  bool(retry.get("isActive"))
                  and retry.get("focused", 0) >= retry.get("rest", 0))

            # ---- AC-94: the CLOSED ELEMENT SET, on a planted fixture -------
            # Every BEFORE number below was MEASURED, not remembered: the
            # previous renderer (git HEAD before 2026-09-10) was served to this
            # same fixture from a scratch copy of web/ and the run recorded
            # what it produced. That is also why the BEFOREs are not all zero
            # -- `pre`, `ul`, `ol`, `li`, `strong`, `em` and `code` were
            # already there and are re-asserted here rather than claimed as new.
            md = page.evaluate("""([MARK, IN_FENCE]) => {
              const items = [...document.querySelectorAll('#history .histitem')];
              let at = -1;
              for (let k = 0; k < items.length; k++) {
                const said = items[k].querySelector('.said');
                if (said && said.textContent.indexOf(MARK) >= 0) { at = k; break; }
              }
              if (at < 0) return null;
              const blocks = items[at].querySelector('.answered');
              const next = items[at + 1] || null;
              const stream = next ? next.querySelector('.answered') : null;
              const n = (root, sel) => root.querySelectorAll(sel).length;
              const txt = (el) => (el ? el.textContent : '');
              const paras = [...blocks.children]
                .filter(e => e.tagName === 'P');
              return {
                h1: n(blocks, 'h1'), h2: n(blocks, 'h2'), h3: n(blocks, 'h3'),
                headText: txt(blocks.querySelector('h2')).trim(),
                p: n(blocks, 'p'),
                splitPara: paras.filter(
                  e => /^(Butler answers|A second paragraph)/
                         .test(e.textContent)).length,
                br: n(blocks, 'br'),
                ul: n(blocks, 'ul'), ol: n(blocks, 'ol'), li: n(blocks, 'li'),
                nestedInLi: n(blocks, 'li > ul'),
                topUl: [...blocks.children]
                  .filter(e => e.tagName === 'UL').length,
                table: n(blocks, 'table'), thead: n(blocks, 'thead'),
                tbody: n(blocks, 'tbody'), tr: n(blocks, 'tr'),
                th: n(blocks, 'th'), td: n(blocks, 'td'),
                pre: n(blocks, 'pre'), preCode: n(blocks, 'pre > code'),
                quote: n(blocks, 'blockquote'), hr: n(blocks, 'hr'),
                anchors: n(blocks, 'a'), images: n(blocks, 'img'),
                fenceAnchors: n(blocks, 'pre a'),
                badLinks: [...blocks.querySelectorAll('a')].filter(a =>
                  !/^https?:/i.test(a.getAttribute('href') || '')
                  || a.target !== '_blank' || !/noopener/.test(a.rel)
                  || !/noreferrer/.test(a.rel)).length,
                strong: n(blocks, 'strong'), em: n(blocks, 'em'),
                code: n(blocks, 'code'),
                rawHash: blocks.textContent
                  .indexOf('## Ways to reach Butler') >= 0,
                rawPipe: blocks.textContent
                  .indexOf('| tier | latency |') >= 0,
                fenceLiteral: txt(blocks.querySelector('pre code'))
                  .indexOf(IN_FENCE) >= 0,
                stream: stream ? {
                  ul: n(stream, 'ul'), li: n(stream, 'li'),
                  pre: n(stream, 'pre'),
                  preText: txt(stream.querySelector('pre code')),
                } : null,
              };
            }""", [MD_MARK, MD_IN_FENCE])
            if md is None:
                print("  SKIP  the markdown fixture is not in this "
                      "conversation. Plant it and reboot the scratch daemon:\n"
                      "        python -X utf8 test/manual/probe_ux.py "
                      "--plant=<scratch BUTLER_STATE>")
            else:
                print("        markdown: " + json.dumps(
                    {k: v for k, v in md.items() if k != "stream"}))

                check("AC-94", "a `##` heading becomes an h2, not literal text",
                      0, (1, False), (md["h2"], md["rawHash"]))
                check("AC-94", "and the h2 carries the heading's own words",
                      "", "Ways to reach Butler", md["headText"])
                check("AC-94", "`#` is an h1 and `###`/`####` both fold to h3",
                      (0, 0), (1, 2), (md["h1"], md["h3"]))
                check("AC-94", "two paragraphs split by a blank line are two p",
                      0, 2, md["splitPara"])
                check("AC-94", "a soft line break inside a p becomes a br",
                      0, 2, md["br"])
                check("AC-94", "a bulleted list, a nested one and a numbered "
                      "one (ul, ol, li)", (1, 1, 7), (2, 1, 7),
                      (md["ul"], md["ol"], md["li"]))
                # The nesting claim needs BOTH halves: the deeper run is inside
                # an `li`, and exactly ONE list is a child of the answer
                # itself. The BEFORE for the second half is 0 rather than 1
                # and that is not an error -- the old renderer put each piece
                # in its own `span`, so no list was a child of `.answered` at
                # all. A single-number check here would have read that 0 as a
                # failure of the nesting rather than of the wrapper.
                check("AC-94", "the nested list sits INSIDE its parent li",
                      (0, 0), (1, 1), (md["nestedInLi"], md["topUl"]))
                check("AC-94", "a pipe table becomes ONE table with a head "
                      "and a body", (0, 0, 0), (1, 1, 1),
                      (md["table"], md["thead"], md["tbody"]))
                check("AC-94", "its cells are th and td in real rows",
                      (0, 0, 0), (2, 4, 3), (md["th"], md["td"], md["tr"]))
                check("AC-94", "and no raw pipe row is left in the answer",
                      True, False, md["rawPipe"])
                check("AC-94", "a blockquote and a thematic break are drawn",
                      (0, 0), (1, 1), (md["quote"], md["hr"]))
                check("AC-94", "a fenced block is exactly ONE pre holding ONE "
                      "code", (1, 1), (1, 1), (md["pre"], md["preCode"]))
                check("AC-94", "the fence's contents are TEXT, never parsed "
                      "as html", True, True, md["fenceLiteral"])
                # The fixture asks for a link, an image and a `javascript:`
                # link in its last paragraph, and one link inside the fence.
                # Without those this row cannot fail (AC-94 is a security
                # boundary, so a vacuous check here is the worst kind).
                check("AC-94", "no image, no unsafe link, no link inside a "
                      "fence -- from a source containing all three",
                      (0, 0, 0), (0, 0, 0),
                      (md["images"], md["badLinks"], md["fenceAnchors"]))
                check("AC-94", "and a safe http(s) link DOES render as one",
                      True, True, md["anchors"] >= 1)
                check("AC-94", "inline emphasis and code still render "
                      "(strong, em, code)", (1, 1, 2), (1, 1, 2),
                      (md["strong"], md["em"], md["code"]))

                # ---- the STREAMING case (the regression that earned it) ----
                # Four assistant messages, one turn: `renderMessages` calls
                # `logReply` once per message and `openItem` survives between
                # them, which is the same accumulation a live reply's frames
                # go through. BEFORE was measured against the old renderer on
                # this exact fixture: three `ul` of one `li`, and two `pre` of
                # which the second was empty.
                st = md["stream"]
                if st is None:
                    check("AC-94", "the fixture's streamed turn is on the page",
                          "n/a", True, False)
                else:
                    print("        streamed: " + json.dumps(st))
                    check("AC-94", "a list split across four pieces is ONE ul",
                          3, 1, st["ul"])
                    check("AC-94", "holding all three of its items", 3, 3,
                          st["li"])
                    check("AC-94", "a fence opened in one piece and closed in "
                          "another is ONE pre", 2, 1, st["pre"])
                    check("AC-94", "and that pre holds both of its lines",
                          "fenced line one\n",
                          "fenced line one\nfenced line two", st["preText"])
        else:
            print("  SKIP  AC-94, AC-169 -- this daemon restored no "
                  "conversation. Both need a brain that HOLDS one "
                  "(brain_mode harness or harness_only); with the fake brain "
                  "GET /api/session/current answers 404.")

        # ---- AC-100 / AC-101: the settings surface ----
        # `#settingsBtn` is the gear on the sidebar's account row (lantern-ui
        # AC-686) and kept its id; on a shut sidebar it is one press further.
        if not page.is_visible("#settingsBtn"):
            page.click("#burger")
            page.wait_for_timeout(600)
        page.click("#settingsBtn")
        page.wait_for_timeout(700)
        named = page.evaluate("""() => {
          const c = [...document.querySelectorAll(
            '#settingsRoot .set-ctl input, #settingsRoot .set-ctl select')];
          const blank = c.filter(e => {
            const l = e.labels && e.labels.length
                      ? e.labels[0].textContent.trim() : '';
            return !(l || e.getAttribute('aria-label')
                     || e.getAttribute('aria-labelledby'));
          });
          return {total: c.length, blank: blank.length,
                  which: blank.map(e => e.id || e.type).slice(0, 10)};
        }""")
        check("AC-100", f"controls with no accessible name (of {named['total']})",
              42, 0, named["blank"])
        if named["blank"]:
            print(f"        still blank: {named['which']}")

        # The settings SHEET (docs/lantern-ui.md 5.1): the sheet's own title
        # is an h2 (`#setTitle`) and every category page carries exactly one
        # h2, its title -- a heading per page, as the one-page surface had a
        # heading per section.
        heads = page.evaluate("""() => {
          const pages = [...document.querySelectorAll(
            '#settingsRoot .setgroup, #settingsRoot .modeblock')];
          return {
            title: !!document.querySelector('#panel #setTitle'),
            h1: document.querySelectorAll('#panel h1').length,
            pages: pages.length,
            titled: pages.filter(p => p.querySelectorAll('h2').length === 1).length,
          };
        }""")
        check("AC-101", "every category page has one real heading", 0,
              heads["pages"], heads["titled"])
        check("AC-101", "the sheet's title is a heading", False, True,
              heads["title"])
        check("AC-101", "no stray h1 inside the sheet", 0, 0, heads["h1"])

        focus = page.evaluate("""() => document.getElementById('panel')
          .contains(document.activeElement)""")
        check("AC-101", "focus moves into the surface on open", False, True,
              focus)
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        after = page.evaluate("""() => ({
          inPanel: document.getElementById('panel')
                     .contains(document.activeElement),
          id: document.activeElement.id || document.activeElement.className,
        })""")
        check("AC-101", "one Tab stays inside the surface", False, True,
              after["inPanel"])
        print(f"        Tab landed on {after['id']!r}")

        # ---- AC-104: a switch that shows it is on ----
        # The checked PROPERTY is set and no `change` event is dispatched, so
        # `commit()` never runs and nothing is written to the daemon. That is
        # what keeps this whole probe read-only and therefore safe to point at
        # a Butler that is running for real -- a diagnostic that edits
        # configuration has to say so in its own name (AGENTS.md), and this one
        # does not edit any. `:has(input:checked)` matches on the DOM state, so
        # the styling can be measured without a write.
        #
        # TWO instrument defects have now been found in this one check, and
        # both had it PASSING against a page where the switch looked identical
        # in both states (D-13 -- validate the instrument).
        #   1. (2026-08-19) Reading immediately after the change caught the
        #      transition's start value re-serialised into its interpolation
        #      space, so a string comparison saw a difference that was not one.
        #      The fix was a wait, and it was not enough:
        #   2. (2026-09-10) A CSS transition never FINISHES in headless
        #      Chrome -- measured, playState "running" and the start value
        #      still in the computed style 3.5 s into a 300 ms transition,
        #      because finishing needs a committed frame. So the wait expired
        #      and the OFF read was still the ON colour, serialised as oklab
        #      because it was mid-interpolation, against an ON read serialised
        #      as oklch because it was declared. Two identical colours,
        #      reported as different, check green.
        # So: finish the animations rather than waiting them out, and compare
        # PIXELS rather than strings.
        # The sheet's switch is the word, then a TRACK (`.swtrack`) whose fill
        # and knob key off the checkbox; the knob's travel stands in for the
        # old pill's border. What is measured is the track's pixels and where
        # its knob sits, both with the box checked and unchecked.
        def sw_style(want_checked):
            page.evaluate("""(v) => {
              document.querySelector('#settingsRoot label.switch input').checked = v;
            }""", want_checked)
            page.wait_for_timeout(150)
            return page.evaluate("""([SETTLE, RGBA]) => {
              const settle = eval("(" + SETTLE + ")");
              const rgba = eval("(" + RGBA + ")");
              const l = document.querySelector('#settingsRoot label.switch');
              const t = l.querySelector('.swtrack');
              settle(l);
              const s = getComputedStyle(t);
              const k = getComputedStyle(t, '::after');
              return {matches: l.matches(':has(input:checked)'),
                      border: k.transform, bg: rgba(s.backgroundColor),
                      borderCss: 'knob ' + k.transform, bgCss: s.backgroundColor};
            }""", [SETTLE, RGBA])

        on, offs = sw_style(True), sw_style(False)
        check("AC-104", "the on rule actually matches", "never", True,
              on["matches"])
        check("AC-104", "the off rule stops matching", "n/a", False,
              offs["matches"])
        check("AC-104", "the knob moves between off and on", "identical",
              True, on["border"] != offs["border"])
        check("AC-104", "background differs between off and on", "identical",
              True, on["bg"] != offs["bg"])
        print(f"        on  {on['borderCss']} / {on['bgCss']}")
        print(f"           -> rgba {on['border']} / {on['bg']}")
        print(f"        off {offs['borderCss']} / {offs['bgCss']}")
        print(f"           -> rgba {offs['border']} / {offs['bg']}")

        # ---- AC-171: a save is visible --------------------------------------
        # NEW. `settings.js` has emitted `.busy` and `.saved` on a row since it
        # was written and neither had a CSS rule, so a successful non-restart
        # save produced NO FEEDBACK AT ALL -- the owner changed a value and the
        # page said nothing. Driven read-only: the classes are toggled on a row
        # here and taken off again, which is a DOM change and never a write.
        # What is measured is that each class actually repaints something, and
        # that `saved` puts the word on the row rather than only tinting it,
        # since a tint alone is a colour channel and this project has been
        # bitten by those collapsing in the light theme (AC-168).
        # The sheet says it beside the control (`.fb`): its opacity is the
        # repaint, its `::after` the word ("Saving", "Saved").
        saved = page.evaluate("""([SETTLE, RGBA]) => {
          const settle = eval("(" + SETTLE + ")");
          const rgba = eval("(" + RGBA + ")");
          const row = document.querySelector('#settingsRoot .set');
          if (!row) return null;
          const fb = row.querySelector('.fb');
          const read = () => {
            settle(row);
            const s = fb ? getComputedStyle(fb) : null;
            return {border: s ? s.opacity : 'none',
                    word: fb ? getComputedStyle(fb, '::after').content : 'none'};
          };
          const had = [...row.classList];
          const plain = read();
          row.classList.add('busy');
          const busy = read();
          row.classList.remove('busy');
          row.classList.add('saved');
          const done = read();
          row.className = had.join(' ');       // put the row back exactly
          return {plain, busy, done, restored: row.className === had.join(' ')};
        }""", [SETTLE, RGBA])
        if not saved:
            print("  SKIP  AC-171 -- no settings row rendered")
        else:
            check("AC-171", "`busy` shows the row's save feedback while a "
                  "write is in flight", "no CSS rule at all", True,
                  saved["busy"]["border"] != saved["plain"]["border"]
                  and "saving" in (saved["busy"]["word"] or "").lower())
            check("AC-171", "`saved` shows it on the acknowledgement",
                  "no CSS rule at all", True,
                  saved["done"]["border"] != saved["plain"]["border"])
            check("AC-171", "`saved` says the WORD and not only a colour",
                  "none", True,
                  "saved" in (saved["done"]["word"] or "").lower())
            check("AC-171", "the probe put the row back the way it found it",
                  "n/a", True, saved["restored"])
            print(f"        saved ::after {saved['done']['word']!r}")

        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "page-ux-settings.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        back = page.evaluate("() => document.activeElement.id")
        check("AC-101", "Escape returns focus to the opener", "n/a",
              "settingsBtn", back)

        # ---- AC-231: the chrome fits, and both lists are visible at once --
        #
        # THE TWO NUMBERS THIS PHASE EXISTS FOR, at both of the widths the
        # criterion names. The before-numbers are the owner's real seven
        # projects measured at HEAD on 2026-09-11: 901 px of `#shortPanel`
        # inside a 755 px scroll viewport, and 0 px of chat list visible at the
        # moment a project control was usable. `#projRail`, the 56px strip
        # this phase originally measured beside the tree, is gone
        # (docs/sidebar-collapse.md, AC-293, 2026-09-26); the tree and the
        # chat rows are the SAME scroll now, so what is left to prove is that
        # they still fit one viewport together. Read-only -- drawn from
        # `GET /api/shortcuts`, and nothing here presses a project (a press
        # writes the expanded set, AC-291).
        PHASE1 = """() => {
          const scroll = document.querySelector('.sb-scroll');
          const list = document.getElementById('chatList');
          const b = (n) => n.getBoundingClientRect();
          const visible = (list && scroll)
            ? Math.max(0, Math.min(b(list).bottom, b(scroll).bottom)
                          - Math.max(b(list).top, b(scroll).top)) : 0;
          return {
            surface: list ? list.scrollHeight : -1,
            column: scroll ? Math.round(b(scroll).height) : -1,
            noStrip: !document.getElementById('projRail'),
            listVisible: Math.round(visible),
            projects: document.querySelectorAll(
              '#chatList .pnode[data-project-id]').length,
          };
        }"""
        for w, h in ((1440, 900), (390, 844)):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(700)
            if w < 900:
                # On a phone the list is an overlay, so it has to be OPEN for
                # "both are visible at once" to be a question at all.
                page.evaluate("() => document.getElementById('burger').click()")
                page.wait_for_timeout(700)
            m = page.evaluate(PHASE1)
            print(f"        {w}x{h}: no strip={m['noStrip']}, "
                  f"{m['projects']} projects in the tree, list "
                  f"{m['surface']}px in a "
                  f"{m['column']}px scroll column, chat list "
                  f"{m['listVisible']}px visible")
            check("AC-293", f"the strip is gone at {w}x{h}",
                  "#projRail, 56px", True, m["noStrip"])
            check("AC-231", f"some chat list is on screen with it at {w}x{h}",
                  "0px", True, m["listVisible"] > 0)
            if w < 900:
                # The drawer shuts on its scrim (lantern-ui: no close X).
                page.evaluate("() => document.getElementById('sbScrim').click()")
                page.wait_for_timeout(500)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(500)

        # ---- AC-263/AC-265 (docs/triage-list.md phase 2): a row draws its
        # own state, and a quiet row draws nothing extra ----
        #
        # READ-ONLY, like every other check in this file: `/api/chats` is a
        # GET and the DOM read below presses nothing. Matched by
        # `data-chat-id` rather than by position, because day-bucket headers
        # interleave with rows -- "the Nth chatline" is not "the Nth chat".
        # Mirrors the elapsed-clock check this file already has for the
        # single open turn (`#stateDesc`'s "<word> - m:ss" shape, above).
        chats_doc = json.loads(
            page.request.get(URL.rstrip("/") + "/api/chats").text())
        # `.rowaction` rides alongside `.rowstatus` (docs/triage-list.md
        # phase 2 continued, AC-270: the probes move with the markup) --
        # captured in the SAME evaluate call rather than a second one, so a
        # row's status and its action are read off one DOM snapshot.
        rows_by_id = page.evaluate("""() => {
          const out = {};
          for (const row of document.querySelectorAll('#chatList .chatrow')) {
            const st = row.querySelector('.rowstatus');
            const act = row.querySelector('.rowaction');
            out[row.dataset.chatId || ''] = {
              status: st ? st.textContent : '',
              action: act ? act.textContent : '',
            };
          }
          return out;
        }""")
        # ONLY THE ROWS THE TREE DREW. A conversation in a SHUT project is
        # not a row on screen (AC-291) -- its state rolls up to the project
        # line's dot instead -- so judging it by a row that does not exist
        # would fail correct output. How many were skipped is printed.
        listed = [c for c in chats_doc.get("chats", []) if c["id"] in rows_by_id]
        print(f"        {len(listed)} of {len(chats_doc.get('chats', []))} "
              f"conversations are rows in an OPEN project")
        loud = [c for c in listed
                if c.get("status") in ("running", "waiting", "error")]
        quiet = [c for c in listed
                 if c.get("status") in ("idle", "done")]
        if not loud and not quiet:
            print("  SKIP  AC-263/AC-265 -- this daemon lists no "
                  "conversations to check a row's status against.")
        else:
            if loud:
                loud_ok = all(rows_by_id.get(c["id"], {}).get("status", "").strip()
                             for c in loud)
                check("AC-263",
                      "a running/waiting/error row's status text is "
                      "non-empty",
                      "blank (no per-row status drawn)", True, loud_ok)
            else:
                print("  SKIP  AC-263 -- no running/waiting/error "
                      "conversation on this daemon right now.")
            if quiet:
                quiet_ok = all(not rows_by_id.get(c["id"], {}).get("status", "").strip()
                              for c in quiet)
                check("AC-265", "a quiet (idle/done) row draws no status "
                      "word", "n/a", True, quiet_ok)
            else:
                print("  SKIP  AC-265 -- no idle/done conversation on this "
                      "daemon right now.")

            # AC-270: a row with a remembered tool action shows it verbatim,
            # and a row with none shows none -- `.rowaction` is gated on
            # `loud && c.action` in app.js, so both halves are real checks.
            acted = [c for c in listed if c.get("action")]
            if acted:
                acted_ok = all(
                    rows_by_id.get(c["id"], {}).get("action", "").strip()
                    == c["action"] for c in acted)
                check("AC-270",
                      "a row with a remembered tool action shows it "
                      "verbatim under its status",
                      "blank or mismatched", True, acted_ok)
            else:
                print("  SKIP  AC-270 (action present) -- no conversation "
                      "on this daemon carries a remembered tool action "
                      "right now.")
            no_action = [c for c in listed if not c.get("action")]
            if no_action:
                no_action_ok = all(
                    not rows_by_id.get(c["id"], {}).get("action", "").strip()
                    for c in no_action)
                check("AC-270", "a row with no remembered tool action "
                      "shows none", "n/a", True, no_action_ok)
            else:
                print("  SKIP  AC-270 (no action) -- every conversation on "
                      "this daemon carries a remembered tool action right "
                      "now.")

        # ---- AC-291: the sidebar is a PROJECT TREE -------------------------
        # (docs/filing-and-pacing.md 8, R-D.) `#groupBtn` and the rail's "All
        # chats" went with the flat list they arranged, so this asks what
        # replaced them: one tree node per project row, in the daemon's order,
        # and no "All chats" anywhere. READ-ONLY: a press on a node writes the
        # expanded set to the daemon, so nothing here presses one.
        rows_doc = json.loads(
            page.request.get(URL.rstrip("/") + "/api/shortcuts").text())
        tree = page.evaluate("""() => ({
          ids: [...document.querySelectorAll(
                 '#chatList .pnode[data-project-id]')]
                 .map(n => n.dataset.projectId),
          all: /all chats/i.test(document.getElementById('sidebar').innerText),
          group: !!document.getElementById('groupBtn'),
        })""")
        check("AC-291", "one tree node per project row, in the daemon's "
              "order", "a rail of tiles over one flat list", True,
              tree["ids"] == [r["id"] for r in rows_doc.get("rows", [])])
        check("AC-291", "no 'All chats' and no grouping control remain",
              "n/a", (False, False), (tree["all"], tree["group"]))

        # ---- AC-103: landscape phone ----
        # 844x390 is the phone on its side, which is where the hero page put
        # its 84 px microphone below the fold. The composer is bottom-anchored
        # now, so this asks the same question of a different layout: is the
        # composer's own mic control reachable without scrolling.
        #
        # STALE UNTIL FIXED HERE: this comment used to say "the one control
        # that arms the microphone", which stopped being true when the mic
        # button's job changed to dictation (mocks/q-familiar.html item 2) --
        # arming moved to `#stateChip` in the header. `MIC` is unchanged and
        # the criterion is unchanged too: AC-103, per `index.html`'s own
        # comment ("never the composer's own 44 px targets"), is about the
        # COMPOSER's target staying reachable at every size, not about
        # whichever act that target currently performs.
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(800)
        land = page.evaluate("""(MIC) => {
          const b = document.querySelector(MIC);
          if (!b) return {missing: true};
          const r = b.getBoundingClientRect();
          return {below: r.bottom > window.innerHeight,
                  hidden: b.offsetParent === null,
                  bottom: Math.round(r.bottom), inner: window.innerHeight,
                  docH: document.documentElement.scrollHeight};
        }""", MIC)
        if land.get("missing"):
            check("AC-103", "the mic control exists at 844x390", "n/a", False,
                  True)
        else:
            check("AC-103", "mic control is inside the viewport at 844x390",
                  "bottom 409 in 390", False, land["below"])
            check("AC-103", "and it is actually rendered there", "n/a", False,
                  land["hidden"])
            print(f"        mic bottom {land['bottom']}px, viewport "
                  f"{land['inner']}px, document {land['docH']}px")
        page.screenshot(path=str(OUT / "page-ux-landscape.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(400)

        print("\n=== page errors ===")
        for e in errs:
            print("  ERROR:", e)
        if not errs:
            print("  (none)")
        check("page", "no uncaught page errors", "none", 0, len(errs))

        # ---- the read-only claim, PROVED rather than promised ---------------
        # The docstring has said this probe is read-only since it was written,
        # and it was proved once by hand with a diff. A property nothing checks
        # is a property that lasts until the next edit, so the diff is in the
        # run now: anything this probe did that reached the daemon shows up
        # here as a changed body.
        after_state = snapshot(page)
        moved = [k for k in WATCHED if before_state[k] != after_state[k]]
        check("read-only", "nothing on the daemon changed across this run",
              "proved by hand once", [], moved)
        for k in moved:
            print(f"        {k} CHANGED")

        ctx.close()
        browser.close()
    return report()


def report():
    bad = [r for r in rows if not r[0]]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} checks pass")
    for _, ac, claim, before, want, got in bad:
        print(f"  FAIL {ac} {claim}: want {want!r}, got {got!r}")
    return 1 if bad else 0


if __name__ == "__main__":
    # `--plant=<dir>` and never a bare positional: the URL is read as "the
    # first argument that is not a flag", so a directory sitting loose on the
    # command line would be taken for one.
    where = next((a.split("=", 1)[1] for a in sys.argv[1:]
                  if a.startswith("--plant=")), None)
    sys.exit(plant(where) if where is not None else main())
