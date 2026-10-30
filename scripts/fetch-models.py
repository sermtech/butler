"""Fetch (or verify) every model file Butler needs that is not in git.

One command for a fresh clone:

    python -X utf8 scripts/fetch-models.py              # required files
    python -X utf8 scripts/fetch-models.py --nemotron   # plus the optional
                                                        # multilingual model

Every file has a pinned SHA-256. The rules, none of them negotiable:

- present with the right hash: skipped ("ok");
- present with the WRONG hash: fail loudly and leave it alone (the user
  decides whether to delete it; this script never overwrites);
- absent: download to `<name>.part`, hash it, then rename. A mismatch deletes
  the part file and exits non-zero. The hash is the only gate, so a truncated
  download cannot pass. No retries, no fallbacks.

Exit 0 only when every requested file is present and verified.

Hash provenance:
- Hugging Face files: `lfs.sha256` from
  https://huggingface.co/api/models/<repo>?blobs=true (2026-10-29).
- openWakeWord v0.5.1 release assets: the GitHub API reports no digest for
  them, so they were downloaded once and hashed here (2026-10-29); the same
  hashes match the copies tracked in git under web/models/.
- onnxruntime-web 1.18.0 (jsdelivr): downloaded once and hashed here
  (2026-10-29); they match the copies already in web/ort18/.

onnxruntime-web is pinned to 1.18.0 on purpose: releases from 1.19 ship only
threaded wasm that iOS refuses to load.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent

HF = "https://huggingface.co"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
ORT = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist"

NVIDIA = "NVIDIA Open Model License"
OPENMDW = "OpenMDW-1.1"
BSD2 = "BSD-2-Clause"
OWW_LICENSE = "CC BY-NC-SA 4.0 (non-commercial)"
MIT = "MIT"


class Item(NamedTuple):
    dest: str       # relative to the repo root
    url: str
    sha256: str
    license: str
    optional: bool = False


ITEMS = (
    Item("models/parakeet-unified-en-0.6b-Q8_0.gguf",
         f"{HF}/handy-computer/parakeet-unified-en-0.6b-gguf/resolve/main/"
         "parakeet-unified-en-0.6b-Q8_0.gguf",
         "4b50b6dd862bf6e346929aaf4f5eaacec003bfa3f56462d6c874b41ef2f38795",
         NVIDIA),
    Item("models/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf",
         f"{HF}/handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/resolve/"
         "main/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf",
         "b94545b313b3223fda7b2857a52681da813935c2127643d1e9ff0c23d988089c",
         OPENMDW, optional=True),
    Item("models/smart-turn-v3.2-cpu.onnx",
         f"{HF}/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx",
         "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
         BSD2),
    Item("web/models/hey_jarvis_v0.1.onnx", f"{OWW}/hey_jarvis_v0.1.onnx",
         "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
         OWW_LICENSE),
    Item("web/models/melspectrogram.onnx", f"{OWW}/melspectrogram.onnx",
         "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
         OWW_LICENSE),
    Item("web/models/embedding_model.onnx", f"{OWW}/embedding_model.onnx",
         "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
         OWW_LICENSE),
    Item("web/ort18/ort.wasm.min.js", f"{ORT}/ort.wasm.min.js",
         "90533a3fd7006df7f897c0d63c468996da5c8e430e39964d92ebe586c4996614",
         MIT),
    Item("web/ort18/ort-wasm-simd.wasm", f"{ORT}/ort-wasm-simd.wasm",
         "f533b5f21790563d7556c611c3835ac65848ee326da2689aac54ed7d9cc8c6a4",
         MIT),
    Item("web/ort18/ort-wasm.wasm", f"{ORT}/ort-wasm.wasm",
         "ebe7ec327f83aabc07e3b58487522481ccff10941f24182ab0428d0123652ff2",
         MIT),
)

CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, part: Path) -> str:
    """Stream `url` into `part` and return the SHA-256 of what arrived."""
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, \
            part.open("wb") as out:
        while chunk := response.read(CHUNK):
            out.write(chunk)
            h.update(chunk)
    return h.hexdigest()


def fetch(item: Item, root: Path) -> bool:
    """Make `item` present and verified under `root`. False on any failure."""
    dest = root / item.dest
    if dest.exists():
        got = sha256_of(dest)
        if got == item.sha256:
            print(f"ok       {item.dest}")
            return True
        print(f"WRONG    {item.dest}: sha256 {got}, expected {item.sha256}. "
              "Not overwriting; delete it yourself to re-fetch.")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    print(f"fetching {item.dest}  [license: {item.license}]")
    try:
        got = download(item.url, part)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if got != item.sha256:
        part.unlink()
        print(f"BAD      {item.dest}: downloaded sha256 {got}, "
              f"expected {item.sha256}. Part file deleted.")
        return False
    part.replace(dest)
    print(f"ok       {item.dest} (downloaded, verified)")
    return True


def main(argv: list[str]) -> int:
    unknown = [a for a in argv if a != "--nemotron"]
    if unknown:
        print(f"unknown argument(s): {unknown}; only --nemotron is accepted")
        return 2
    with_nemotron = "--nemotron" in argv
    wanted = [i for i in ITEMS if with_nemotron or not i.optional]
    failed = [i.dest for i in wanted if not fetch(i, REPO)]
    print("openWakeWord pre-trained models are CC BY-NC-SA 4.0 "
          "(non-commercial); by using each model you accept its license.")
    if failed:
        print(f"{len(failed)} of {len(wanted)} files NOT verified: {failed}")
        return 1
    print(f"{len(wanted)} files present and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
