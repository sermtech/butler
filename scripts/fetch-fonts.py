"""Fetch (or verify) the web fonts the satellite page serves from web/fonts/.

`web/` may not fetch a font at runtime (web/AGENTS.md: no CDN, no fetched
fonts). A stylesheet link to a font CDN would make the page depend on the
public internet to render its own text, and this daemon is meant to keep
working on a tailnet with the router down. So the woff2 files are fetched ONCE,
here, and served by the daemon like everything else in web/. On disk,
gitignored, with the fetch command written down rather than a downloader that
runs at boot.

    python -X utf8 scripts/fetch-fonts.py                 # into web/fonts/
    python -X utf8 scripts/fetch-fonts.py --dest <dir>    # into <dir> instead

web/fonts.css is TRACKED and this script never reads or writes it; it declares
the faces, this script only supplies the binaries it points at.

Every file has a pinned SHA-256. Same rules as scripts/fetch-models.py:

- present with the right hash: skipped ("ok");
- present with the WRONG hash: fail loudly and leave it alone;
- absent: download to `<name>.part`, hash it, then rename. A mismatch deletes
  the part file and exits non-zero. No retries, no fallbacks.

Exit 0 only when every file is present and verified.

Provenance (2026-10-29): each URL is the latin-subset woff2 that
fonts.googleapis.com/css2 returns for a Chrome user agent. The path carries the
publisher's version (v12, v20, ...), and the hash pins the exact bytes. The
hashes equal the files vendored earlier by this script's previous form.
Latin only: this UI is English.

Licenses: every family is SIL Open Font License 1.1, read from each family's
METADATA.pb (`license: "OFL"`) and OFL.txt in github.com/google/fonts/ofl/.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent
GSTATIC = "https://fonts.gstatic.com/s"
OFL = "SIL OFL 1.1"
CHUNK = 1 << 20

# Google serves woff2 only to a browser-shaped request; the gstatic files
# themselves do not need it, but sending it costs nothing and matches how the
# URLs were obtained.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class Font(NamedTuple):
    name: str       # file name under the destination, as web/fonts.css expects
    url: str
    sha256: str
    license: str


FONTS = (
    Font("AtkinsonHyperlegible-400.woff2",
         f"{GSTATIC}/atkinsonhyperlegible/v12/"
         "9Bt23C1KxNDXMspQ1lPyU89-1h6ONRlW45G04pIo.woff2",
         "d64ba838ef5472bba248620ec4fd8b5aa7cf0db2908e0bb230600caf279ba7bc",
         OFL),
    Font("AtkinsonHyperlegible-700.woff2",
         f"{GSTATIC}/atkinsonhyperlegible/v12/"
         "9Bt73C1KxNDXMspQ1lPyU89-1h6ONRlW45G8Wbc9dCWP.woff2",
         "140e2bd25a7315c8a062508391426b0d8c3297400c947b8d847be28f73a199f0",
         OFL),
    Font("Figtree-var.woff2",
         f"{GSTATIC}/figtree/v9/_Xms-HUzqDCFdgfMm4S9DQ.woff2",
         "4ba7d3d096695818fe0686be4f1e82c6b05134e18a22260336130335027462dd",
         OFL),
    Font("IBMPlexMono-400.woff2",
         f"{GSTATIC}/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q1w.woff2",
         "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
         OFL),
    Font("IBMPlexMono-500.woff2",
         f"{GSTATIC}/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2",
         "01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22",
         OFL),
    Font("IBMPlexSans-var.woff2",
         f"{GSTATIC}/ibmplexsans/v23/"
         "zYXzKVElMYYaJe8bpLHnCwDKr932-G7dytD-Dmu1syxeKYY.woff2",
         "e2291e842cf5af167122a22881a740c7f2dda7716f1e8cd76680264f4a859470",
         OFL),
    Font("Inter-var.woff2",
         f"{GSTATIC}/inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7.woff2",
         "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62",
         OFL),
    Font("JetBrainsMono-var.woff2",
         f"{GSTATIC}/jetbrainsmono/v24/"
         "tDbv2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKwBNntkaToggR7BYRbKPxDcwg"
         ".woff2",
         "83c005d49d8a6a50474c73a5a36ac0468076e9c4a29da7bdb14995d80560a5be",
         OFL),
    Font("NunitoSans-var.woff2",
         f"{GSTATIC}/nunitosans/v19/"
         "pe0TMImSLYBIv1o4X1M8ce2xCx3yop4tQpF_MeTm0lfGWVpNn64CL7U8upHZIbMV51Q4"
         "2ptCp7t1R-s.woff2",
         "29e3890496844a9ea81975c52771c587c872b4eb317026422d1995b88d21b57d",
         OFL),
    Font("PlusJakartaSans-var.woff2",
         f"{GSTATIC}/plusjakartasans/v12/LDIoaomQNQcsA88c7O9yZ4KMCoOg4Ko20yw"
         ".woff2",
         "153fc85b70298beeb1d61a5f723331649e7f23bb77302a66e61cb3e2fbdb5e79",
         OFL),
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, part: Path) -> str:
    """Stream `url` into `part` and return the SHA-256 of what arrived."""
    h = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response, \
            part.open("wb") as out:
        while chunk := response.read(CHUNK):
            out.write(chunk)
            h.update(chunk)
    return h.hexdigest()


def fetch(font: Font, root: Path) -> bool:
    """Make `font` present and verified under `root`. False on any failure."""
    dest = root / font.name
    if dest.exists():
        got = sha256_of(dest)
        if got == font.sha256:
            print(f"ok       {font.name}  [license: {font.license}]")
            return True
        print(f"WRONG    {font.name}: sha256 {got}, expected {font.sha256}. "
              "Not overwriting; delete it yourself to re-fetch.")
        return False
    part = dest.with_name(dest.name + ".part")
    print(f"fetching {font.name}  [license: {font.license}]")
    try:
        got = download(font.url, part)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if got != font.sha256:
        part.unlink()
        print(f"BAD      {font.name}: downloaded sha256 {got}, "
              f"expected {font.sha256}. Part file deleted.")
        return False
    part.replace(dest)
    print(f"ok       {font.name} (downloaded, verified)")
    return True


def main(argv: list[str]) -> int:
    root = REPO / "web" / "fonts"
    if argv[:1] == ["--dest"] and len(argv) == 2:
        root = Path(argv[1])
    elif argv:
        print("usage: fetch-fonts.py [--dest DIR]")
        return 2
    root.mkdir(parents=True, exist_ok=True)
    results = [fetch(font, root) for font in FONTS]
    bad = results.count(False)
    if bad:
        print(f"{bad} of {len(FONTS)} font file(s) failed")
        return 1
    print(f"all {len(FONTS)} font files present and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
