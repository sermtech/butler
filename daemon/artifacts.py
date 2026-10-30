"""What an agent SHOWS in the chat (docs/rich-chat.md, AC-613..AC-640).

Three things live here, all PURE (no hub, no aiohttp; the routes call them
through `asyncio.to_thread`, D-25):

- `classify`: what a file IS, from its BYTES (RC-6). A name that disagrees
  with its bytes loses; the extension is read only where bytes say nothing
  (plain text has no magic).
- `ArtifactStore`: per-conversation SNAPSHOTS (RC-3). The file is copied at
  the moment it is shown, so a reload, a phone, or a later edit or delete of
  the original still shows what was shown.
- `validate_ui`: `show_ui`'s closed component set (RC-9). A layout the page
  would not draw as written is refused with the path and the reason, so the
  model can fix it -- an earlier prototype's rule, not its library.

Nothing here decides WHERE a file may come from; that is
`daemon/containment.py`, the one guard shared with the WhatsApp send (RC-11).
"""
from __future__ import annotations

import codecs
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
from pathlib import Path

from daemon import attachments

MB = 1024 * 1024
# RC-7 [DECIDED]: per kind, refused with the number when crossed (AC-619).
CAPS = {"image": 20 * MB}
DEFAULT_CAP = 100 * MB
PREVIEW = 256 * 1024     # `files.MAX_PREVIEW`, the explorer's bound (RC-7)
HEAD = 8192

TEXT_KINDS = frozenset({"markdown", "text", "code", "csv"})
SHOWN_KINDS = frozenset({"image", "audio", "video", "ui"}) | TEXT_KINDS
KINDS = SHOWN_KINDS | {"file"}

_MARKDOWN = {"md", "markdown"}
_CSV = {"csv": "text/csv", "tsv": "text/tab-separated-values"}
_PLAIN = {"", "txt", "log", "text"}
# Script-capable formats are CODE: their source is shown, never rendered
# (RC-7), and the route always serves them as an attachment (AC-629).
ACTIVE = frozenset({"svg", "html", "htm", "xhtml", "xml"})

_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def cap_for(kind: str) -> int:
    return CAPS.get(kind, DEFAULT_CAP)


def _media_magic(head: bytes) -> tuple[str, str] | None:
    """Audio and video by their bytes (RC-6, AC-617), or None."""
    if head.startswith(b"ID3"):
        return "audio", "audio/mpeg"
    if len(head) > 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        # An MPEG frame header is more than its 11 sync bits: a reserved
        # version, bitrate or sample rate means these bytes are something
        # else (`FF FE` opens UTF-16 text too).
        version, layer = (head[1] >> 3) & 3, (head[1] >> 1) & 3
        rate, freq = head[2] >> 4, (head[2] >> 2) & 3
        if version != 1 and freq != 3:
            if layer == 0 and head[1] & 0xF6 == 0xF0:   # ADTS
                return "audio", "audio/aac"
            if layer != 0 and rate not in (0, 15):
                return "audio", "audio/mpeg"
    if head.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio", "audio/flac"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _AUDIO_BRANDS:
            return "audio", "audio/mp4"
        if brand == b"qt  ":
            return "video", "video/quicktime"
        return "video", "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm"
    return None


def _is_utf8(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        # Not final: the head may cut a multibyte character in two.
        codecs.getincrementaldecoder("utf-8")("strict").decode(head)
    except UnicodeDecodeError:
        return False
    return True


def classify(head: bytes, name: str) -> tuple[str, str]:
    """(kind, media type) for a file whose first bytes are `head`."""
    try:
        _, media, kind = attachments.sniff(head, name)
    except attachments.Unsupported:
        media = kind = None
    if kind == "image":
        return "image", media
    av = _media_magic(head)
    if av:
        return av
    ext = _ext(name)
    if kind == "document" and media != "text/plain":
        return "file", media                      # PDF, Word: a link
    if _is_utf8(head):
        if ext in _MARKDOWN:
            return "markdown", "text/markdown"
        if ext in _CSV:
            return "csv", _CSV[ext]
        if ext in _PLAIN:
            return "text", "text/plain"
        return "code", "text/plain"
    guess = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return "file", guess


def served_type(kind: str, media: str) -> str:
    """The Content-Type the route sends (AC-628): text kinds are plain text
    whatever they are called, so nothing a browser could run is ever typed
    as runnable."""
    if kind in TEXT_KINDS:
        return "text/plain; charset=utf-8"
    if kind == "ui":
        return "application/json"
    return media


def inline(kind: str, name: str) -> bool:
    """Shown in place, or downloaded (AC-629)."""
    return kind in SHOWN_KINDS and _ext(name) not in ACTIVE


class Refused(ValueError):
    """A file or layout this module will not keep; the message says why."""


_CHAT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


class ArtifactStore:
    """`<root>/<chat>/<id>.data` plus `<id>.json` (the entry). The entry is
    written LAST, so an artifact exists exactly when its entry does.

    Lookups never join a caller's string onto a path: the chat folder and
    the artifact are matched against names LISTED off disk (`ChatStore.find`'s
    rule), so a traversal is unrepresentable (AC-631)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- writing ---------------------------------------------------------- #
    def _folder(self, chat: str) -> Path:
        if not isinstance(chat, str) or not _CHAT_ID.fullmatch(chat):
            raise Refused(f"{chat!r} is not a conversation id")
        return self.root / chat

    def _commit(self, chat: str, entry: dict, write) -> dict:
        folder = self._folder(chat)
        folder.mkdir(parents=True, exist_ok=True)
        data = folder / f"{entry['id']}.data"
        tmp = folder / f"{entry['id']}.part"
        try:
            write(tmp)
            os.replace(tmp, data)
        finally:
            tmp.unlink(missing_ok=True)
        meta = folder / f"{entry['id']}.json"
        meta.write_text(json.dumps(entry), encoding="utf-8")
        return entry

    def snapshot(self, chat: str, real: Path, caption: str | None = None,
                 name: str | None = None) -> dict:
        """Copy `real` (already passed by the containment guard) and answer
        its entry. The size is checked against the kind's cap while COPYING,
        not only from `stat`, so a file growing under the read cannot slip a
        larger body past it (AC-619)."""
        real = Path(real)
        if not real.is_file():
            raise Refused(f"{real} is not a file")
        with open(real, "rb") as src:
            head = src.read(HEAD)
            shown = attachments.safe_name(name or real.name)
            kind, media = classify(head, shown)
            cap = cap_for(kind)
            size = os.fstat(src.fileno()).st_size
            if size > cap:
                raise Refused(f"{shown} is {size} bytes; the limit for "
                              f"{kind} is {cap} bytes")
            entry = {"id": _mint(), "kind": kind, "media": media,
                     "name": shown, "size": size}
            if caption:
                entry["caption"] = str(caption)[:500]

            def write(tmp: Path):
                copied = len(head)
                with open(tmp, "wb") as out:
                    out.write(head)
                    while chunk := src.read(MB):
                        copied += len(chunk)
                        if copied > cap:
                            raise Refused(f"{shown} grew past the limit for "
                                          f"{kind} ({cap} bytes) while it "
                                          f"was copied")
                        out.write(chunk)
                entry["size"] = copied
            return self._commit(chat, entry, write)

    def put_ui(self, chat: str, layout: dict) -> dict:
        """A validated `show_ui` layout, stored as its JSON (RC-9)."""
        body = json.dumps(layout).encode("utf-8")
        entry = {"id": _mint(), "kind": "ui", "media": "application/json",
                 "name": "layout.json", "size": len(body),
                 "title": layout["title"]}
        return self._commit(chat, entry, lambda tmp: tmp.write_bytes(body))

    # -- reading ---------------------------------------------------------- #
    def find(self, chat: str, aid: str) -> tuple[Path, dict] | None:
        """(data path, entry) or None. Both ids are matched against what is
        listed, never joined."""
        try:
            folders = {p.name: p for p in self.root.iterdir() if p.is_dir()}
        except FileNotFoundError:
            return None
        folder = folders.get(chat)
        if folder is None:
            return None
        names = {p.name for p in folder.iterdir()}
        if f"{aid}.json" not in names or f"{aid}.data" not in names:
            return None
        meta = folder / f"{aid}.json"
        entry = json.loads(meta.read_text(encoding="utf-8"))
        if entry.get("id") != aid:
            return None
        return folder / f"{aid}.data", entry

    # -- lifetime --------------------------------------------------------- #
    def remove_chat(self, chat: str) -> None:
        """With its conversation (AC-622). Missing is success."""
        found = self.find_folder(chat)
        if found is not None:
            shutil.rmtree(found)

    def copy_chat(self, source: str, target: str) -> None:
        """A rewind's copy keeps what the original showed (`docs/rewind.md`):
        the copy is a new conversation id and its messages name these ids."""
        found = self.find_folder(source)
        if found is not None:
            shutil.copytree(found, self._folder(target), dirs_exist_ok=True)

    def find_folder(self, chat: str) -> Path | None:
        try:
            for p in self.root.iterdir():
                if p.is_dir() and p.name == chat:
                    return p
        except FileNotFoundError:
            pass
        return None


def _mint() -> str:
    return "art-" + secrets.token_hex(8)


def preview_bytes(path: Path) -> tuple[bytes, bool]:
    """At most `PREVIEW` bytes, and whether the file was longer."""
    with open(path, "rb") as fh:
        data = fh.read(PREVIEW + 1)
    return data[:PREVIEW], len(data) > PREVIEW


# --------------------------------------------------------------------------- #
# show_ui (RC-9, AC-625)
# --------------------------------------------------------------------------- #
MAX_DEPTH = 3
MAX_BLOCKS = 50
MAX_ROWS = 200
MAX_SERIES = 12
MAX_POINTS = 500
MAX_LAYOUT = 64 * 1024
MAX_TEXT = 4000


class _Count:
    blocks = 0


def _need(cond: bool, where: str, why: str) -> None:
    if not cond:
        raise Refused(f"{where}: {why}")


def _text(v, where: str, required: bool = True) -> None:
    if v is None and not required:
        return
    _need(isinstance(v, str) and (v.strip() or not required), where,
          "expected non-blank text")
    _need(len(v) <= MAX_TEXT, where, f"longer than {MAX_TEXT} characters")


def _number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _keys(block: dict, allowed: set, where: str) -> None:
    extra = sorted(set(block) - allowed - {"type"})
    _need(not extra, where, f"unknown field(s) {extra}")


def _block(b, where: str, depth: int, count: _Count) -> None:
    _need(isinstance(b, dict), where, "expected an object")
    count.blocks += 1
    _need(count.blocks <= MAX_BLOCKS, where,
          f"more than {MAX_BLOCKS} blocks in one layout")
    kind = b.get("type")
    if kind == "Card":
        _keys(b, {"title", "blocks"}, where)
        _text(b.get("title"), f"{where}.title", required=False)
        _need(depth < MAX_DEPTH, where, f"nested deeper than {MAX_DEPTH}")
        _blocks(b.get("blocks"), f"{where}.blocks", depth + 1, count)
    elif kind == "Text":
        _keys(b, {"markdown"}, where)
        _text(b.get("markdown"), f"{where}.markdown")
    elif kind == "Stat":
        _keys(b, {"label", "value", "unit", "delta"}, where)
        _text(b.get("label"), f"{where}.label")
        v = b.get("value")
        _need(_number(v) or (isinstance(v, str) and v.strip()
                             and len(v) <= 80),
              f"{where}.value", "expected a number or short text")
        _text(b.get("unit"), f"{where}.unit", required=False)
        if "delta" in b:
            _need(_number(b["delta"]), f"{where}.delta", "expected a number")
    elif kind == "Table":
        _keys(b, {"columns", "rows"}, where)
        cols = b.get("columns")
        _need(isinstance(cols, list) and cols, f"{where}.columns",
              "expected a non-empty list of text")
        for i, c in enumerate(cols):
            _text(c, f"{where}.columns[{i}]")
        rows = b.get("rows")
        _need(isinstance(rows, list), f"{where}.rows", "expected a list")
        _need(len(rows) <= MAX_ROWS, f"{where}.rows",
              f"more than {MAX_ROWS} rows")
        for i, row in enumerate(rows):
            _need(isinstance(row, list) and len(row) == len(cols),
                  f"{where}.rows[{i}]",
                  f"expected a list of {len(cols)} cells")
            for j, cell in enumerate(row):
                _need(cell is None or _number(cell)
                      or (isinstance(cell, str) and len(cell) <= MAX_TEXT),
                      f"{where}.rows[{i}][{j}]",
                      "expected text, a number or null")
    elif kind == "List":
        _keys(b, {"items"}, where)
        items = b.get("items")
        _need(isinstance(items, list) and items, f"{where}.items",
              "expected a non-empty list of text")
        _need(len(items) <= MAX_ROWS, f"{where}.items",
              f"more than {MAX_ROWS} items")
        for i, item in enumerate(items):
            _text(item, f"{where}.items[{i}]")
    elif kind in ("BarChart", "LineChart"):
        _keys(b, {"title", "labels", "series"}, where)
        _text(b.get("title"), f"{where}.title", required=False)
        labels = b.get("labels")
        _need(isinstance(labels, list) and labels, f"{where}.labels",
              "expected a non-empty list of text")
        _need(len(labels) <= MAX_POINTS, f"{where}.labels",
              f"more than {MAX_POINTS} points")
        for i, lab in enumerate(labels):
            _need(isinstance(lab, str) and len(lab) <= 80,
                  f"{where}.labels[{i}]", "expected short text")
        series = b.get("series")
        _need(isinstance(series, list) and series, f"{where}.series",
              "expected a non-empty list")
        _need(len(series) <= MAX_SERIES, f"{where}.series",
              f"more than {MAX_SERIES} series")
        for i, s in enumerate(series):
            at = f"{where}.series[{i}]"
            _need(isinstance(s, dict), at, "expected an object")
            extra = sorted(set(s) - {"name", "values"})
            _need(not extra, at, f"unknown field(s) {extra}")
            _text(s.get("name"), f"{at}.name")
            vals = s.get("values")
            _need(isinstance(vals, list)
                  and all(v is None or _number(v) for v in vals),
                  f"{at}.values", "expected numbers")
            _need(len(vals) == len(labels), f"{at}.values",
                  f"expected {len(labels)} values, one per label")
    else:
        _need(False, f"{where}.type",
              f"unknown component {kind!r}; use one of Card, Text, Stat, "
              f"Table, List, BarChart, LineChart")


def _blocks(blocks, where: str, depth: int, count: _Count) -> None:
    _need(isinstance(blocks, list) and blocks, where,
          "expected a non-empty list of blocks")
    for i, b in enumerate(blocks):
        _block(b, f"{where}[{i}]", depth, count)


def validate_ui(args) -> dict:
    """`{title, blocks}` exactly as the page will draw it, or `Refused`."""
    _need(isinstance(args, dict), "arguments", "expected an object")
    _text(args.get("title"), "title")
    _need(len(json.dumps(args)) <= MAX_LAYOUT, "arguments",
          f"the layout is larger than {MAX_LAYOUT} bytes")
    _blocks(args.get("blocks"), "blocks", 1, _Count())
    return {"title": args["title"], "blocks": args["blocks"]}


# --------------------------------------------------------------------------- #
# The tools a model is offered (RC-1). `daemon/adapters/pi_memory.ts` mirrors
# these; `test/test_artifacts.py` holds the two together.
# --------------------------------------------------------------------------- #
SHOW_FILE = "show_file"
SHOW_UI = "show_ui"

TOOLS = [
    {"type": "function", "function": {
        "name": SHOW_FILE,
        "description": (
            "Show the user ONE file in the chat: an image, chart, audio or "
            "video plays inline; text, markdown, code and CSV are shown as "
            "text or a table; any other file appears as a download link. "
            "The file must be inside this project's folder or Butler's data "
            "root. Use it after you make or find something worth seeing. "
            "Your spoken or written answer must still make sense without it."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string",
                     "description": "The file, relative to the project "
                                    "folder or absolute."},
            "caption": {"type": "string",
                        "description": "One short line shown under it."},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": SHOW_UI,
        "description": (
            "Show the user a small layout in the chat: headline numbers, "
            "tables, lists, bar or line charts, grouped in cards. Blocks: "
            "{type:'Card', title, blocks:[...]}, {type:'Text', markdown}, "
            "{type:'Stat', label, value, unit?, delta?}, {type:'Table', "
            "columns:[text], rows:[[cell]]}, {type:'List', items:[text]}, "
            "{type:'BarChart'|'LineChart', title?, labels:[text], "
            "series:[{name, values:[number]}]} with one value per label. "
            "An invalid layout is refused with the reason; fix it and call "
            "again. Your answer must still make sense without it."),
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "What this shows."},
            "blocks": {"type": "array", "items": {"type": "object"},
                       "description": "The blocks, top to bottom."},
        }, "required": ["title", "blocks"]}}},
]
TOOL_NAMES = tuple(t["function"]["name"] for t in TOOLS)


def shown_line(entry: dict) -> str:
    """What the model is told after a show (RC-1): never the content."""
    if entry["kind"] == "ui":
        return f"Shown: {entry['title']} (layout)."
    return (f"Shown: {entry['name']}, {entry['kind']}, "
            f"{entry['size']} bytes.")
