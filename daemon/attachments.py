"""What the owner SHOWED Butler: one file per attachment, on disk.

`docs/attachments.md`, AC-197..AC-206.

```
state/attachments/att-<ms>-<rand>.<ext>
```

THE FILE IS KEPT, and its PATH is most of the feature. A coding agent can open
a file; so an attachment is useful in harness mode even when no model can
see, which is the half of items 5 and 6 that costs almost
nothing (AC-201).

**THE TYPE IS SNIFFED FROM THE BYTES** (AC-198). Both the filename and the
`Content-Type` header are the caller's, and this module writes a file
something may later be pointed at. A type nothing here recognises is REFUSED
by name rather than stored as probably-fine -- the closed-table rule
`butlerd.TERMINAL_AGENTS` already carries, in a second place and for the same
reason: the caller is unauthenticated on a tailnet.

**DOCUMENTS ARE READ BEFORE THEY ARE LOOKED AT** (AC-200). `pypdf` for a PDF's
text layer and `python-docx` for a `.docx` body, both local, both needing no
model and no GPU -- which matters here, because this machine's card is below
the compute-capability floor for most current vision stacks [MEASURED
2026-09-10, the TTS research]. A file that is already text is simply read.
Extraction failing is never fatal: the file is still on disk, its path still
travels, and the vision path is still there (D-1).

**BOUNDED BY COUNT, not by age.** The oldest files go when the store passes
`KEEP`. No clock logic to get wrong, and a conversation the owner is still
having keeps its attachments.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

#: How many attachments stay on disk. The path has to outlive the turn -- a
#: coding agent may open it minutes later -- so they are kept, and this is what
#: stops that being unbounded. Oldest first, by the millisecond in the name.
KEEP = 200

#: The most text one extraction contributes to a prompt. A 200-page PDF is a
#: document nobody meant to read aloud into a model's context; the cap is the
#: same order as `dialogue.SEED_MAX_CHARS` and for the same reason.
TEXT_MAX_CHARS = 24000

#: What a file has to LOOK like to be stored (AC-198). A closed table: the
#: leading bytes, the extension the file is saved under, the media type, and
#: whether it is something to look at or something to read.
#:
#: `webp` and `gif` are listed and `svg` deliberately is NOT: an SVG is a
#: document that can carry script, and nothing here should be the reason one
#: reaches a browser.
_MAGIC: tuple[tuple[bytes, str, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png", "image"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg", "image"),
    (b"GIF87a", "gif", "image/gif", "image"),
    (b"GIF89a", "gif", "image/gif", "image"),
    (b"%PDF-", "pdf", "application/pdf", "document"),
)
#: RIFF....WEBP and the ZIP container docx lives in both need a second look:
#: their first bytes are shared with other formats, so the sniff is two tests
#: rather than one prefix.
_ZIP = b"PK\x03\x04"

#: Extensions read as plain text when the bytes are not one of the shapes
#: above. The LIST is what makes this safe: anything else with no magic is
#: refused, so an unknown binary cannot be stored as "text".
_TEXT_EXTS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "toml",
    "ini", "cfg", "log", "py", "js", "ts", "jsx", "tsx", "html", "css",
    "sql", "sh", "bat", "ps1", "c", "h", "cpp", "hpp", "java", "go", "rs",
    "rb", "php", "xml", "diff", "patch",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class Unsupported(ValueError):
    """The bytes are not something this store will keep (AC-198)."""


@dataclass(frozen=True)
class Attachment:
    """One thing the owner showed Butler.

    `path` is where the bytes are, and it is what reaches a coding agent
    (AC-201). `text` is what could be READ out of it without a model, "" when
    there was nothing or when the owner asked for the vision path instead.
    """
    id: str
    name: str
    kind: str            # "image" or "document"
    media_type: str
    path: str
    bytes: int
    text: str = ""

    def public(self) -> dict:
        """What a route answers and what the page draws. The TEXT is not on
        this wire: the page has no use for 24000 characters of a PDF, and the
        prompt it belongs in is built in the daemon."""
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "media_type": self.media_type, "bytes": self.bytes,
                "chars": len(self.text)}


def _now_ms() -> int:
    return int(time.time() * 1000)


def safe_name(name: str) -> str:
    """A filename fit to put in a prompt and to show on a page.

    Nothing here is used to BUILD a path -- the stored name is minted -- so
    this is not a traversal defence; it is so a name from the wire cannot
    carry control characters or a newline into a coding agent's prompt, which
    is `butlerd.paste_safe`'s concern one layer along.
    """
    flat = _SAFE_NAME.sub(" ", " ".join(str(name or "").split()))
    return flat.strip()[:80] or "attachment"


def sniff(head: bytes, name: str) -> tuple[str, str, str]:
    """(extension, media type, kind) for these bytes, or raise `Unsupported`.

    THE BYTES DECIDE, and the extension is consulted only where the bytes
    cannot: a ZIP container is `.docx` only if it is named one, and a plain
    text file has no magic at all.
    """
    for magic, ext, media, kind in _MAGIC:
        if head.startswith(magic):
            return ext, media, kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp", "image"
    ext = (name.rsplit(".", 1)[-1] or "").lower() if "." in name else ""
    if head[:4] == _ZIP and ext == "docx":
        return ("docx",
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document", "document")
    if ext in _TEXT_EXTS:
        return ext, "text/plain", "document"
    raise Unsupported(
        f"Butler does not know what {safe_name(name)!r} is. It keeps images "
        f"(png, jpeg, gif, webp), PDFs, Word documents and plain text files; "
        f"what arrived matches none of those.")


def extract(path: Path, ext: str, mode: str) -> str:
    """Whatever text is in this file, without a model. "" when there is none.

    NEVER RAISES (D-1). A document that will not parse is still on disk, its
    path still travels, and the vision path is still there -- losing the
    extraction costs some context and must not cost the attachment.

    `mode` is the `document_reading` setting: `vision` means do not read it at
    all, so the file goes to the model whole.
    """
    if mode == "vision":
        return ""
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            pages = []
            total = 0
            for page in PdfReader(str(path)).pages:
                got = page.extract_text() or ""
                if not got.strip():
                    continue
                pages.append(got)
                total += len(got)
                if total >= TEXT_MAX_CHARS:
                    break
            return "\n\n".join(pages)[:TEXT_MAX_CHARS].strip()
        if ext == "docx":
            import docx
            body = docx.Document(str(path))
            out = [p.text for p in body.paragraphs if p.text.strip()]
            for table in body.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        out.append(" | ".join(cells))
            return "\n".join(out)[:TEXT_MAX_CHARS].strip()
        if ext in _TEXT_EXTS:
            # `errors="replace"`, because this is the owner's own file and a
            # mis-guessed encoding should cost a few characters rather than
            # the whole attachment.
            return path.read_text(encoding="utf-8",
                                  errors="replace")[:TEXT_MAX_CHARS].strip()
    except Exception as e:                                # noqa: BLE001
        print(f"[butler] could not read any text out of {path.name} ({e!r}). "
              f"The file is still attached; only its text is missing.")
    return ""


class AttachmentStore:
    """`state/attachments/`, and nothing else.

    Held on the Hub as a plain attribute like `connections` and `shortcuts`,
    so every existing test that builds a Hub keeps working and the routes are
    the only thing that reads it.
    """

    def __init__(self, root) -> None:
        self.root = Path(root)

    def add(self, data: bytes, name: str, mode: str) -> Attachment:
        """Store one file. Raises `Unsupported` for bytes this will not keep.

        The stored NAME is minted and never built from the caller's, so a
        traversal is unrepresentable rather than refused -- `ChatStore.find`'s
        rule, in the one module here that does write a file from the wire.
        """
        ext, media, kind = sniff(data[:16], name)
        att_id = f"att-{_now_ms()}-{secrets.token_hex(3)}"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{att_id}.{ext}"
        path.write_bytes(data)
        self._reap()
        return Attachment(id=att_id, name=safe_name(name), kind=kind,
                          media_type=media, path=str(path), bytes=len(data),
                          text=extract(path, ext, mode))

    def _reap(self) -> None:
        """Keep the newest `KEEP` files. LOUD and never fatal (D-1)."""
        try:
            found = sorted(p for p in self.root.glob("att-*") if p.is_file())
        except OSError:
            return
        for victim in found[:-KEEP] if len(found) > KEEP else []:
            try:
                victim.unlink()
            except OSError as e:
                print(f"[butler] WARNING: could not reap {victim} ({e})")
