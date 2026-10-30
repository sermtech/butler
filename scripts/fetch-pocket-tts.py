"""Install the pinned Pocket TTS binary and its weights (daemon/adapters/tts_pocket.py).

    python -X utf8 scripts/fetch-pocket-tts.py [--languages=en,pt] [--skip-weights]

1. If vendor/pocket-tts/pocket-tts(.exe) reports the pinned version: "ok", no
   download. Otherwise download this machine's pinned release asset, REFUSE it
   unless its SHA-256 equals the pin, and unpack the binary and its LICENSE
   there (made executable off Windows).
2. Unless --skip-weights: run one short `generate` per language (default:
   every language Butler offers a voice in), which makes the binary fetch that
   model's weights into the Hugging Face cache now rather than at speech time,
   and proves they load.

Standard library only. Windows x86-64 and Apple Silicon macOS, the pinned
assets (`pocket_pin.ASSETS`); any other machine is refused. Exit 0
only when every step passed; the first failure stops the run (no retries, no
fallbacks). Never run at speech time: with the binary missing, speech fails
with an error naming this command.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHUNK = 1 << 20


def _load_pin():
    """The pin by FILE: importing `daemon.adapters` would load every adapter."""
    path = REPO / "daemon" / "adapters" / "pocket_pin.py"
    spec = importlib.util.spec_from_file_location("pocket_pin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pin = _load_pin()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def installed_version(exe: Path) -> str:
    """Empty when missing or not runnable (a truncated download), so it is fetched again."""
    if not exe.is_file():
        return ""
    try:
        return subprocess.run([str(exe), "--version"], capture_output=True,
                              text=True, timeout=30,
                              creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def unpack(archive: Path, exe_name: str, dest: Path) -> None:
    """Copy the binary and its LICENSE out of a .zip or .tar.gz into `dest`.

    Only those two names, matched by basename and written by this code, so a
    member path inside the archive can never write anywhere else.
    """
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            members = {Path(n).name: n for n in z.namelist()}
            files = {n: z.read(members[n])
                     for n in (exe_name, "LICENSE") if n in members}
    else:
        with tarfile.open(archive, "r:gz") as t:
            members = {Path(m.name).name: m for m in t.getmembers() if m.isfile()}
            files = {n: t.extractfile(members[n]).read()
                     for n in (exe_name, "LICENSE") if n in members}
    if exe_name not in files:
        raise SystemExit(f"no {exe_name} inside {archive.name}")
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (dest / name).write_bytes(data)
    if os.name != "nt":
        os.chmod(dest / exe_name, 0o755)


def install_binary(work: Path) -> None:
    want = f"pocket-tts {pin.RELEASE['version']}"
    if installed_version(pin.EXE) == want:
        print(f"ok       {pin.EXE.relative_to(REPO)} ({want})")
        return
    asset = pin.asset_for()
    archive = work / asset["name"]
    print(f"fetching {pin.url(asset)}")
    with urllib.request.urlopen(pin.url(asset), timeout=60) as response, \
            archive.open("wb") as out:
        shutil.copyfileobj(response, out, CHUNK)
    got = sha256_of(archive)
    if got != asset["sha256"]:
        raise SystemExit(f"BAD      {asset['name']}: sha256 {got}, "
                         f"expected {asset['sha256']}. Refused.")
    unpack(archive, pin.EXE_NAME, pin.INSTALL_DIR)
    found = installed_version(pin.EXE)
    if found != want:
        raise SystemExit(f"installed {pin.EXE} reports {found!r}, not {want!r}")
    print(f"ok       {pin.EXE.relative_to(REPO)} (downloaded, sha256 verified, {want})")


def fetch_weights(subtags: list[str], work: Path) -> None:
    env = dict(os.environ, HF_HUB_CACHE=pin.hf_hub_cache(os.environ, Path.home()))
    print(f"weights  cache {env['HF_HUB_CACHE']}")
    for sub in subtags:
        model = pin.LANGUAGES[sub][0]
        wav = work / f"ready-{model}.wav"
        print(f"checking {model} loads (downloads its weights if missing) ...")
        done = subprocess.run(
            [str(pin.EXE), "generate", "--language", model, "--quantize",
             "--text", "Ready.", "--output-path", str(wav), "-q"],
            env=env, capture_output=True, text=True, timeout=1800,
            creationflags=NO_WINDOW)
        if done.returncode != 0 or not wav.is_file():
            raise SystemExit(f"FAILED   {model}: exit {done.returncode}\n"
                             f"{(done.stderr or done.stdout)[-2000:]}")
        print(f"ok       {model}")


def main(argv: list[str]) -> int:
    subtags, skip_weights = sorted(pin.LANGUAGES), False
    for arg in argv:
        if arg == "--skip-weights":
            skip_weights = True
        elif arg.startswith("--languages="):
            subtags = [s for s in arg.split("=", 1)[1].split(",") if s]
        else:
            print(__doc__.strip().splitlines()[2], file=sys.stderr)
            return 2
    unknown = [s for s in subtags if s not in pin.LANGUAGES]
    if unknown or not subtags:
        print(f"unknown language(s) {unknown}; known: {sorted(pin.LANGUAGES)}",
              file=sys.stderr)
        return 2
    try:
        pin.asset_for()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="pocket-tts-") as tmp:
        install_binary(Path(tmp))
        if not skip_weights:
            fetch_weights(subtags, Path(tmp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
