"""The rest of Step 6 (docs/lantern-ui.md Step 6c, AC-706, AC-709, AC-710,
CU-15, CU-16): the Files tab with its preview UNDER the tree and a visible
drop target, the transcript's artifact cards and styled audio player, the
"/" picker in the export's popover shape, the stop-the-agent confirm in the
one overlay shape, one toast, and the one hover-help mechanism restyled.

GREP GATES over `web/`, in the shape of `test_lantern_composer_page.py`:
nothing here runs `app.js`, `files.js` or `artifacts.js`, so these pin the
structure a browser run against a scratch daemon depends on. The untrusted-
content contracts (AC-94, AC-632, AC-314) are the ones that fail SILENTLY,
so each is pinned on the caller as well as the callee (D-18).
"""
import re
from pathlib import Path

import pytest

from test_lantern_shell_page import _element, _fn_body

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _code(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//[^\n\"']*$", "", source)


@pytest.fixture(scope="module")
def app():
    return _code((WEB / "app.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def files():
    return _code((WEB / "files.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def art():
    return _code((WEB / "artifacts.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def library():
    return _code((WEB / "library.js").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css(index):
    sheet = index[index.index("<style>"):index.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)


@pytest.fixture(scope="module")
def markup(index):
    body = index[index.index("<body>"):]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


# -- 1. Files: the preview sits UNDER the tree (CU-16, file-explorer 3.4) ---- #

def test_the_preview_sits_under_the_tree_and_never_hides_it(markup, files):
    """CU-16 reverses file-explorer.md 3.4: opening a file no longer swaps
    the tree out. The preview follows the tree in the panel body, and no
    path in files.js hides the tree."""
    body = _element(markup, "filesBody")
    assert body.index('id="filesTree"') < body.index('id="filesPreview"'), (
        "the preview is not under the tree")
    assert not re.search(r'\$\("filesTree"\)\.hidden\s*=\s*true', files), (
        "files.js still hides the tree to show a preview")
    # A drop or a turn end re-lists the tree even while a file is previewed.
    assert "if (previewing === null) await refresh()" not in files
    assert "if (previewing === null) render()" not in files


def test_the_preview_head_has_copy_path_and_close(markup):
    """The export's preview head: the path, what it is, Copy path and Close,
    icon-only controls named for a screen reader and the one tooltip."""
    prev = _element(markup, "filesPreview")
    for cid, help_ in (("filesCopy", "Copy path"), ("filesBack", "Close")):
        tag = re.search(r'<button\b[^>]*\bid="' + cid + r'"[^>]*>', prev)
        assert tag, f"no #{cid} in the preview head"
        assert f'data-help="{help_}"' in tag.group(0), tag.group(0)
        assert "aria-label=" in tag.group(0), tag.group(0)


def test_code_previews_draw_through_the_shared_renderer(files):
    """RC-12 widened by Step 6c: code joins the kinds artifacts.js draws in
    the panel, with the panel's own raw URL (caller-built)."""
    kinds = re.search(r"const ART_KINDS = new Set\(\[([^\]]*)\]\)", files)
    assert kinds, "files.js has no ART_KINDS"
    for k in ("image", "audio", "video", "csv", "markdown", "code"):
        assert f'"{k}"' in kinds.group(1), f"{k} is not drawn by artifacts.js"
    assert "url: rawUrl(path, false)" in files


# -- 1b. Files: a VISIBLE drop target while a file is dragged over ----------- #

def test_the_drop_target_exists_and_is_hidden_by_default(markup):
    panel = _element(markup, "filesPanel")
    tag = re.search(r'<div\b[^>]*\bid="filesDrop"[^>]*>', panel)
    assert tag, "no #filesDrop in the files panel"
    assert re.search(r"\bhidden\b", tag.group(0)), "#filesDrop is not hidden at rest"


def test_the_drop_target_shows_only_while_files_are_dragged(files):
    """Shown by the drag, hidden by a leave, a drop and a drag end; its words
    name the project the file lands in (RC-13's route is unchanged)."""
    assert re.search(r'\$\("filesDrop"\)\.hidden\s*=', files), (
        "files.js never shows or hides #filesDrop")
    assert '"Drop a file here to upload it to "' in files
    for ev in ("dragover", "dragleave", "drop", "dragend"):
        assert f'"{ev}"' in files, f"no {ev} listener"
    # The one write is still the upload POST (AC-310, RC-13).
    assert files.count('method: "POST"') == 1


# -- 2. transcript extras ----------------------------------------------------- #

def test_the_audio_player_is_styled_and_reads_its_own_duration(art):
    """CU-16 reverses rich-chat RC-7: no native controls for audio; a play
    button, a seek slider and the time, the duration read from the element's
    own metadata. No waveform: nothing here knows the samples."""
    assert "media.controls = true" not in art or re.search(
        r'if \(kind === "video"\)[^}]*media\.controls = true', art), (
        "audio still uses the browser's own controls")
    player = _fn_body(art, "audioPlayer")
    assert 'createElement("audio")' in player
    assert "loadedmetadata" in player and ".duration" in player
    assert "wave" not in player.lower(), "the player draws a waveform"
    assert 'type = "range"' in player or "type = \"range\"" in player


def test_cards_carry_the_exports_head(art):
    """Image, code, markdown and CSV cards carry a head line with the name
    and what it is; the image head links full size and download through the
    caller's URLs only."""
    head = _fn_body(art, "cardHead")
    assert "textContent" in art and "innerHTML" not in art
    assert "entry.name" in head
    img = _fn_body(art, "imageCard")
    assert "naturalWidth" in img, "the image size is not read off the element"


def test_every_new_element_source_is_still_the_callers(art):
    """AC-632 re-checked after the cards: every src/href is opts.url or
    opts.downloadUrl, and img/audio/video exist only in this module."""
    srcs = re.findall(r"\.(src|href)\s*=\s*([^;]+);", art)
    assert srcs and {v.strip() for _k, v in srcs} <= {"opts.url", "opts.downloadUrl"}, srcs
    for name in ("app.js", "files.js", "library.js", "automations.js", "settings.js"):
        code = _code((WEB / name).read_text(encoding="utf-8"))
        for tag in ("img", "audio"):
            assert f'createElement("{tag}")' not in code, f"{name} makes <{tag}>"


def test_the_via_mark_sits_in_the_actions_row_at_full_strength(app, css):
    """CU-17: the phone channel's mark is information, not a control; it
    sits under the owner's bubble with the actions and is never dimmed."""
    body = _fn_body(app, "markVia")
    assert ".acts" in body and "via.textContent = words" in body
    rule = re.search(r"\.acts \.via\s*\{([^}]*)\}", css)
    assert rule and "opacity" not in rule.group(1)
    assert "color: var(--muted)" in rule.group(1) or "color: var(--fg)" in rule.group(1)


def test_the_guest_lock_is_drawn_in_the_composers_place(app, css):
    """The guest-locked composer (AC-607): one textContent line with a lock
    glyph, in the export's locked box; the inert form is not drawn."""
    lock = _fn_body(app, "paintGuestLock")
    assert "composerForm.hidden = guestChat" in lock
    assert "LOCK_ICON()" in app[app.index("const guestNote"):][:600]
    assert re.search(r"#guestLock\s*\{[^}]*border-radius", css)


def test_stopped_is_a_shape_and_a_word(css):
    rule = re.search(r"\.stopnote::before\s*\{([^}]*)\}", css)
    assert rule, "Stopped. has no square before it (never colour alone, AC-168)"


def test_the_slash_picker_is_the_exports_popover(library):
    """The "/" picker: a "Recipes" title, rows with the name (the typed part
    underlined), the first line with the served placeholders as chips, the
    scope, and a footer of keys that says only what the keys do."""
    paint = _fn_body(library, "paintSlash")
    assert '"Recipes"' in paint
    assert "tokens(" in paint, "placeholders are not the served chips"
    assert 'el("u"' in paint, "the typed part of the name is not marked"
    assert '"slfoot"' in paint
    # Tab is NOT "run in a new chat" here: it fills in like Enter.
    assert "run in a new chat" not in paint


# -- 3. confirms: the one overlay shape, modal, Escape taken whole ----------- #

def test_the_stop_confirm_is_a_modal_sheet(markup, app):
    dlg = re.search(r'<dialog\b[^>]*\bid="stopConfirm"[^>]*>', markup)
    assert dlg and "sheet confirm" in dlg.group(0), (
        "the stop-the-agent confirm is not a dialog.sheet.confirm (AC-706)")
    assert 'id="stopConfirmScrim"' not in markup, "the old scrim survives"
    assert "Stop the running agent?" in _element(markup, "stopConfirm")
    assert ">Stop and start new chat<" in _element(markup, "stopConfirm")
    ask = _fn_body(app, "askStopConfirm")
    assert "showModal()" in ask
    assert "stopConfirmCancel" in ask


def test_the_chat_delete_arms_with_its_word_in_warn(app, css):
    body = _fn_body(app, "chatDropButton")
    assert '"Delete?"' in body
    rule = re.search(r"\.chatdrop\[data-armed\]\s*\{([^}]*)\}", css)
    assert rule and "var(--warn)" in rule.group(1)


# -- 4. toasts: one shape, one function -------------------------------------- #

def test_one_toast_function_and_one_toast_element(app, markup, css):
    assert len(re.findall(r"\bfunction toast\(", app)) == 1, "not ONE toast function"
    tag = re.search(r'<div\b[^>]*\bid="toast"[^>]*>', markup)
    assert tag and 'role="status"' in tag.group(0), "#toast is not a status region"
    assert re.search(r"prefers-reduced-motion: reduce\)\s*\{[^@]*#toast", css), (
        "#toast ignores reduced motion")


def test_the_transient_words_go_through_the_toast(app, files, library):
    assert "dataset.said" not in app, "a button still says its own word (second mechanism)"
    assert 'toast("Copied")' in app
    assert re.search(r'toast\("Restarting', app)
    assert 'env.toast("Copied")' in files
    assert '"Copied"' not in files.replace('env.toast("Copied")', "")
    assert 'env.toast("Saved")' in library
    assert "toast: (text) => toast(text)" in app


def test_no_css_word_bubble_survives(css):
    assert "data-said" not in css
    assert not re.search(r"content:\s*attr\(", css), (
        "a CSS-generated word is a second tooltip/toast mechanism")


# -- 5. hover help: the ONE #tip, in the export's look ----------------------- #

def test_tip_is_the_only_tooltip_and_wears_the_exports_look(markup, css, index):
    assert markup.count('id="tip"') == 1
    assert not re.search(r"\stitle=\"", index), "a browser title tooltip"
    rule = re.search(r"#tip\s*\{([^}]*)\}", css)
    assert rule, "no #tip rule"
    assert "background: var(--fg)" in rule.group(1)
    assert "color: var(--bg)" in rule.group(1)
    for name in ("app.js", "files.js", "artifacts.js", "library.js", "automations.js"):
        code = _code((WEB / name).read_text(encoding="utf-8"))
        assert ".title =" not in code, f"{name} sets a title tooltip"
