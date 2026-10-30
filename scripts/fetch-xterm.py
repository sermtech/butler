"""Vendor xterm.js and its fit addon into web/vendor/xterm/.

`web/` may not fetch code at runtime (web/AGENTS.md: no build step, no CDN).
The in-page terminal (docs/chats-and-terminals.md CT-4) needs xterm.js, so the
files are fetched ONCE, here, from the npm registry, checked against the
registry's own integrity hash, and served by the daemon like everything else
in web/. Same treatment as web/fonts: on disk, gitignored, the fetch command
written down rather than a downloader that runs at boot.

Versions match an earlier prototype's dashboard client (@xterm/xterm ^6.0.0,
@xterm/addon-fit ^0.11.0), pinned exactly here.

    python -X utf8 scripts/fetch-xterm.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "web" / "vendor" / "xterm"

# package -> (exact version, {path inside the tarball: file name written})
PACKAGES = {
    "@xterm/xterm": ("6.0.0", {
        "package/lib/xterm.js": "xterm.js",
        "package/css/xterm.css": "xterm.css",
    }),
    "@xterm/addon-fit": ("0.11.0", {
        "package/lib/addon-fit.js": "addon-fit.js",
    }),
}


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def fetch(name: str, version: str, wanted: dict[str, str]) -> None:
    meta = json.loads(get(f"https://registry.npmjs.org/{name}/{version}"))
    dist = meta["dist"]
    data = get(dist["tarball"])
    integrity = dist.get("integrity", "")
    if integrity.startswith("sha512-"):
        got = base64.b64encode(hashlib.sha512(data).digest()).decode()
        if got != integrity[len("sha512-"):]:
            raise SystemExit(f"{name}@{version}: sha512 mismatch, refusing")
    elif hashlib.sha1(data).hexdigest() != dist["shasum"]:
        raise SystemExit(f"{name}@{version}: shasum mismatch, refusing")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for inner, outname in wanted.items():
            member = tar.getmember(inner)
            body = tar.extractfile(member).read()
            (OUT / outname).write_bytes(body)
            print(f"  {outname:16} {len(body):>8} bytes  ({name}@{version})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (version, wanted) in PACKAGES.items():
        fetch(name, version, wanted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
