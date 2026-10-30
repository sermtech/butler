"""The PAGE half of rich chat (docs/rich-chat.md, AC-632..AC-639): what a turn
SHOWED, drawn by `web/artifacts.js` in the chat and in the file explorer, and
drag-to-upload (RC-12, RC-13).

GREP GATES, `test_file_explorer_page.py`'s shape and reason: nothing in this
suite loads or executes `web/app.js`, `web/artifacts.js` or `web/files.js`,
so a regression here is invisible to every daemon-side test. The daemon half
(the artifact store, the route, the frame) is proved in the daemon's own
tests; the pixels are proved with a browser against a scratch daemon, and
`test/manual/probe_ux.py` counts `#history img` whose src is not on
`/api/artifacts/` (AC-632) there. This file pins the structure that browser
run depends on.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
ART = ROOT / "web" / "artifacts.js"
FILES = ROOT / "web" / "files.js"


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", js)


def _fn_body(source: str, name: str) -> str:
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{",
                  source)
    assert m, f"no `function {name}(...)` declaration found"
    depth = 0
    for i in range(m.end() - 1, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[m.end() - 1:i + 1]
    raise AssertionError(f"unbalanced braces reading function {name}")


@pytest.fixture(scope="module")
def app():
    return _strip_comments(APP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def art():
    assert ART.exists(), "web/artifacts.js does not exist"
    return _strip_comments(ART.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def files():
    return _strip_comments(FILES.read_text(encoding="utf-8"))


# -- AC-632: every element source is Butler's own route --------------------- #

def test_artifacts_js_has_no_markup_sink(art):
    """AC-632, AC-638 (AC-94's rule carried over). Names, captions, CSV cells
    and chart labels are untrusted; a markup sink would let a file named
    `<img src=x onerror=...>` run script in the page that owns every route.
    """
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "DOMParser", "createContextualFragment"):
        assert sink not in art, f"artifacts.js uses {sink}"


def test_every_element_source_is_a_url_the_caller_built(art):
    """AC-632 / RC-8. The ONLY values ever assigned as `src` or `href`, or
    fetched, are the caller's `opts.url` / `opts.downloadUrl` /
    `opts.previewUrl`. Catches a source taken from `entry.name`, a caption or
    any other field a model wrote (the spec's D-13 mutation)."""
    srcs = re.findall(r"\.(src|href)\s*=\s*([^;]+);", art)
    assert srcs, "artifacts.js sets no src/href at all -- the pin is blind"
    bad = [(k, v.strip()) for k, v in srcs
           if v.strip() not in ("opts.url", "opts.downloadUrl")]
    assert bad == [], f"a src/href not built by the caller: {bad}"
    assert not re.search(r'setAttribute\(\s*"(src|href|xlink:href)"', art), (
        "a src/href set through setAttribute escapes this pin")
    fetched = re.findall(r"fetch\(([^)]*)\)", art)
    assert fetched and set(fetched) <= {"opts.url", "opts.previewUrl"}, (
        f"artifacts.js fetches something else: {fetched}")


def test_the_only_route_artifacts_js_names_is_the_artifact_route(art):
    """RC-8: the module string-builds ONE route, `/api/artifacts/`, from the
    record's ids; the explorer's `/api/project/{id}/raw` is passed in. Any
    other `/api/` or `http` literal is a source the page did not decide."""
    # The SVG namespace is an identifier for createElementNS, never fetched.
    literals = [s for s in re.findall(r'"([^"\n]*)"', art)
                if s != "http://www.w3.org/2000/svg"]
    routes = [s for s in literals if "/api/" in s or s.startswith("http")
              or s.startswith("//")]
    assert routes == ["/api/artifacts/"], f"unexpected route literals: {routes}"
    builder = _fn_body(art, "artifactUrls")
    assert "encodeURIComponent(chat)" in builder
    assert "encodeURIComponent(id)" in builder


def test_the_chat_urls_cannot_be_overridden_by_options(art):
    """RC-8. `renderChatArtifact` spreads the caller's options FIRST and the
    route's URLs LAST, so no option can replace a source."""
    body = _fn_body(art, "renderChatArtifact")
    assert re.search(r"\{\s*\.\.\.\(opts \|\| \{\}\),\s*\.\.\.artifactUrls\(chat, entry\.id\)\s*\}",
                     body), body


def test_the_chat_markdown_renderer_still_never_makes_an_image(app):
    """AC-632's second half and AC-94: `![x](http://...)` in a reply stays
    text. app.js creates no `img` and no `audio` at all; its one `video` is
    the camera's capture element in `takePhoto`, fed `srcObject`, never put in
    the transcript."""
    assert '"img"' not in app, "app.js names an img element"
    assert '"audio"' not in app, "app.js names an audio element"
    assert app.count('createElement("video")') == 1
    assert 'createElement("video")' in _fn_body(app, "takePhoto")
    inline = _fn_body(app, "mdInline")
    assert 'text.charAt(m.index - 1) === "!"' in inline, (
        "markdown image syntax is no longer kept as text")


# -- AC-633 / AC-634: where artifacts are drawn ------------------------------ #

def test_app_imports_the_renderer(app):
    assert re.search(r'import\s*\{[^}]*renderChatArtifact[^}]*\}\s*from\s*"\./artifacts\.js"',
                     app), "app.js does not import artifacts.js"


def test_the_box_sits_after_the_answer_never_inside(app):
    """AC-633 / RC-8. `paintAnswer` rebuilds `.answered` on every streamed
    piece; a box INSIDE it would be erased by the next sentence. The box is
    placed with `.after(` on `.answered` (Butler) or `.said` (the owner,
    O-2), `markStopped`'s precedent."""
    box = _fn_body(app, "artifactBox")
    assert re.search(r'role === "user"\s*\?\s*it\.said\s*:\s*it\.answered', box)
    assert "anchor.after(box)" in box
    assert "append(" not in box.replace("box.append", ""), (
        "the box is appended somewhere instead of placed after the anchor")
    assert "arts" not in _fn_body(app, "paintAnswer")
    draw = _fn_body(app, "drawArtifacts")
    assert "artifactBox(it, role)" in draw and "renderChatArtifact(chat" in draw


def test_a_reload_draws_the_records_artifacts_for_both_roles(app):
    """AC-634 and O-2: `renderMessages` draws `m.artifacts` under the owner's
    message AND under Butler's answer, so a reload shows what the live turn
    showed. Catches either role being dropped."""
    body = _fn_body(app, "renderMessages")
    assert re.search(r'drawArtifacts\(openItem, "user", m\.artifacts', body)
    assert len(re.findall(r'drawArtifacts\(openItem, "assistant", m\.artifacts',
                          body)) >= 2, (
        "an answer with text AND one that only showed something must both draw")


def test_an_answer_that_only_showed_something_is_not_called_empty(app):
    """RC-5: an artifact alone is a reply; `closeItem` must not write "no
    reply came back" under a turn whose box is drawn."""
    assert ":scope > .arts" in _fn_body(app, "closeItem")


# -- the live `artifact` frame ----------------------------------------------- #

def test_the_artifact_frame_is_routed_by_chat_before_turn_routing(app):
    """docs/rich-chat.md section 11: `{type:"artifact", chat, role,
    artifact}` carries no turn, so it must be handled before
    `trackTurnFrame`/`stale()`, which would never see it as on-screen."""
    body = _fn_body(app, "onMessage")
    at = body.index('m.type === "artifact"')
    assert at < body.index("trackTurnFrame(m)")
    assert re.search(r'm\.type === "artifact"\)\s*\{\s*applyArtifactFrame\(m\);\s*return;',
                     body)


def test_an_off_screen_artifact_touches_nothing_drawn(app):
    """web/AGENTS.md: off-screen frames touch nothing drawn; the record holds
    them and `loadSession` draws them. Catches a frame for another chat
    landing in the conversation on screen."""
    body = _fn_body(app, "applyArtifactFrame")
    first = body.split(";")[0]
    assert "m.chat !== liveChatId" in first and "return" in body.split("\n")[1]
    assert "openItem" in body and "heldArtifacts.push(m)" in body


def test_a_held_artifact_is_drawn_when_its_item_appears(app):
    """The owner's attachment can precede the `final` that draws their
    message: it is held and drawn by `drawHeldArtifacts`, called right after
    `logUser` in the `final` case and after `logReply`; held frames are
    dropped when the record is re-read and when the turn ends."""
    body = _fn_body(app, "onMessage")
    final = body[body.index('case "final"'):body.index('case "reply"')]
    assert final.index("logUser(text)") < final.index("drawHeldArtifacts()")
    reply = body[body.index('case "reply"'):body.index('case "library"')]
    assert "drawHeldArtifacts()" in reply
    assert "heldArtifacts = []" in _fn_body(app, "loadSession")
    assert "heldArtifacts = []" in _fn_body(app, "goListen")


# -- RC-13: drag to attach / upload ------------------------------------------ #

def test_a_drop_on_the_conversation_goes_through_send_attachment(app):
    """RC-13 / AC-197: a dropped file takes the paperclip's path, one
    `sendAttachment` per file, never a second upload route."""
    m = re.search(r'dropZone\.addEventListener\("drop",(.*?)\n\}\);', app, re.S)
    assert m, "no drop handler on the conversation"
    drop = m.group(1)
    assert "sendAttachment(file" in drop and "for (const file of" in drop
    assert "fetch(" not in drop, "the drop uploads on its own"
    assert "dragHasFiles(e)" in drop, "a drag of text or a link is taken too"


def test_the_explorer_draws_previews_with_the_shared_renderer(files):
    """RC-12: the explorer uses artifacts.js with its OWN raw URLs and hands
    over the text it already fetched (no second request)."""
    assert re.search(r'import\s*\{\s*renderArtifact\s*\}\s*from\s*"\./artifacts\.js"',
                     files)
    body = _fn_body(files, "openPreview")
    call = body[body.index("renderArtifact("):]
    for want in ("url: rawUrl(path, false)", "downloadUrl: rawUrl(path, true)",
                 "previewUrl: null", "text:"):
        assert want in call, f"the preview call lacks {want}"


def test_the_explorer_posts_only_to_upload(files):
    """RC-13: the explorer's one write is `POST /api/project/{id}/upload`
    with the file as the raw body."""
    assert re.findall(r'method:\s*"(\w+)"', files) == ["POST"]
    up = _fn_body(files, "upload")
    assert '"/upload?path="' in up and "body: file" in up
    assert '"application/octet-stream"' in up


# -- AC-635..AC-639: what each kind becomes ---------------------------------- #

def test_csv_is_capped_at_200_rows_with_a_count_and_a_download(art):
    """AC-635."""
    assert re.search(r"const CSV_ROWS = 200;", art)
    body = _fn_body(art, "drawCsv")
    assert "rows.slice(1, 1 + CSV_ROWS)" in body
    assert '" more rows"' in body and "fileLink(entry, opts" in body


def test_svg_is_never_drawn_as_an_image(art):
    """AC-636 / RC-7: an `image` entry whose media type is svg is drawn as
    code text (the daemon already classifies svg and html as code)."""
    body = _fn_body(art, "kindOf")
    assert re.search(r'kind === "image" && /svg/i\.test\(.*\)\) return "code"', body)


def test_a_file_is_a_download_link_with_name_size_and_type(art):
    """AC-637."""
    body = _fn_body(art, "fileLink")
    assert 'setAttribute("download"' in body
    assert "a.href = opts.downloadUrl" in body
    assert "humanSize(entry.size)" in body and "typeLabel(entry)" in body


def test_charts_are_hand_built_svg(art):
    """AC-638 / RC-10: createElementNS in the SVG namespace, text via
    textContent, no chart library."""
    assert 'const SVG_NS = "http://www.w3.org/2000/svg";' in ART.read_text(
        encoding="utf-8")
    assert "document.createElementNS(SVG_NS" in _fn_body(art, "svgEl")
    chart = _fn_body(art, "chartEl")
    assert "t.textContent = " in chart
    assert "import " not in art.split("export")[0], "artifacts.js imports a library"


def test_an_unknown_block_is_a_note_and_never_throws(art):
    """AC-639: the closed set is the switch's cases; anything else is a plain
    "cannot show this" note, and a block that throws while drawing is a note
    too, so the blocks around it still draw."""
    block = _fn_body(art, "blockEl")
    cases = set(re.findall(r'case "(\w+)"', block))
    assert cases == {"Card", "Text", "Stat", "Table", "List", "BarChart",
                     "LineChart"}, cases
    assert re.search(r"default:\s*return cannotShow\(", block)
    blocks = _fn_body(art, "drawBlocks")
    assert re.search(r"try \{ node = blockEl\(b, opts, depth\); \} catch \(e\) "
                     r"\{ node = cannotShow\(", blocks)
