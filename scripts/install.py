"""One-command installer for Butler (INSTALL.md).

    python scripts/install.py [--data DIR] [--nemotron] [--check]

Runs under ANY Python >= 3.10 and uses only the standard library, because the
project's virtual environment does not exist yet. Each step is idempotent and
prints one line `[step] ok|done|SKIP|FAILED reason`; the first FAILED stops the
run with exit 1 (fail fast, no fallbacks). It never installs uv, node or npm:
it names the fix and stops.

Steps: prereqs, venv, python packages, node packages, models, Pocket TTS
(the default speech engine, scripts/fetch-pocket-tts.py), fonts and the
terminal view, data root, persisting BUTLER_DATA.

It is also the UPDATE step on every machine: after `git push <machine>
master`, run it there and restart Butler; each step is a no-op when nothing
changed.

BUTLER_DATA is NEVER overwritten: it points at someone's records. An unset
value is saved with `setx` on Windows; on macOS and Linux no profile file is
edited, the exact `export` line is printed instead.

`--check` downloads nothing, changes nothing and runs no installer command; it
only probes (`node --version`, `npm ls --depth=0`, one import in the venv).
Model, font and terminal-view files are checked for PRESENCE only, not their
SHA-256 (`fetch-models.py` and `fetch-fonts.py` verify hashes when run).
Exit 0 only when every item is ok.

The decisions are pure functions (`resolve_data_root`, `persist_decision`,
`expected_files`, `start_message`); `Context` carries every side-effecting
dependency (subprocess, PATH lookup, environment, registry value) so the tests
inject them.
"""
from __future__ import annotations

import argparse
import importlib.util
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from daemon import paths  # noqa: E402

MIN_PYTHON = (3, 10)
MIN_NODE = 20
VENV_PYTHON_VERSION = "3.13"
KEY_PACKAGE = "aiohttp"      # first runtime line of requirements.txt
UV_URL = "https://docs.astral.sh/uv/getting-started/installation/"
NODE_URL = "https://nodejs.org/"


class InstallError(Exception):
    """A refusal with a message that says what to do."""


class Result(NamedTuple):
    status: str     # ok | done | done (manual) | SKIP | FAILED
    detail: str


class Decision(NamedTuple):
    action: str     # ok | keep | refuse | setx | manual | none_needed
    message: str


@dataclass
class Context:
    repo: Path
    os_name: str                    # "nt" or "posix"
    run: Callable                   # subprocess.run
    which: Callable                 # shutil.which
    env: dict
    registry_value: str | None      # HKCU\Environment BUTLER_DATA, or None
    home: Path
    data_flag: str | None
    nemotron: bool
    local_port: int
    py_version: tuple
    data: Path | None = None        # set by the data-root step
    legacy_default: Path | None = None  # launch.py's Windows fallback


# ------------------------------------------------------------ pure decisions

def same_path(a: str, b: str, os_name: str) -> bool:
    """Equality under the TARGET platform's rules, not the host's."""
    if os_name == "nt":
        return ntpath.normcase(ntpath.normpath(a)) == \
            ntpath.normcase(ntpath.normpath(b))
    return posixpath.normpath(a) == posixpath.normpath(b)


def resolve_data_root(flag, env, registry_value, home: Path,
                      repo: Path, legacy_default: Path | None = None) -> Path:
    """--data > BUTLER_DATA > saved Windows user value > existing records at
    launch.py's fallback > `<home>/Butler-Data`.

    launch.py's Windows fallback (the author's E: drive) is never CHOSEN for a
    new install, but records already there are ADOPTED: a machine running on
    that fallback with no BUTLER_DATA saved would otherwise be switched to an
    empty records folder. Refuses a folder inside the repository.
    """
    raw = flag or env or registry_value
    if raw:
        root = Path(raw).expanduser()
    elif legacy_default and (legacy_default / paths.RECORDS_DIR).is_dir():
        root = legacy_default
    else:
        root = home / "Butler-Data"
    root = root.absolute()
    where, code = root.resolve(), repo.resolve()
    if where == code or code in where.parents:
        raise InstallError(
            f"data root {root} is inside the repo {repo}; choose a folder "
            "outside it with --data")
    return root


def persist_decision(current, wanted, flag_given: bool, os_name: str,
                     posix_default) -> Decision:
    """What to do about BUTLER_DATA given what is already saved."""
    if current:
        if same_path(current, wanted, os_name):
            return Decision("ok", f"BUTLER_DATA is already {current}")
        if flag_given:
            return Decision(
                "refuse",
                f"BUTLER_DATA is already {current}; refusing to change it "
                "(it points at your existing records). Unset it yourself "
                "first if you mean to move.")
        return Decision("keep", f"BUTLER_DATA is already {current}; "
                        "keeping it")
    if os_name == "nt":
        return Decision("setx", f"save BUTLER_DATA={wanted}")
    if same_path(wanted, str(posix_default), os_name):
        return Decision("none_needed",
                        "launch.py defaults to ~/Butler-Data; nothing to set")
    return Decision("manual", f"export BUTLER_DATA={wanted}")


def venv_python(repo: Path, os_name: str) -> Path:
    sub = ("Scripts", "python.exe") if os_name == "nt" else ("bin", "python")
    return repo / ".venv" / sub[0] / sub[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_pin():
    """daemon/adapters/pocket_pin.py by file (the package would load every
    adapter, and this script runs before the venv exists)."""
    path = REPO / "daemon" / "adapters" / "pocket_pin.py"
    spec = importlib.util.spec_from_file_location("pocket_pin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_files(repo: Path, nemotron: bool) -> list[Path]:
    """Every file the three fetch scripts leave behind, read off THEIR tables."""
    models = _load("fetch_models", "fetch-models.py")
    fonts = _load("fetch_fonts", "fetch-fonts.py")
    xterm = _load("fetch_xterm", "fetch-xterm.py")
    found = [repo / i.dest for i in models.ITEMS
             if nemotron or not i.optional]
    pocket = _load_pin()
    found.append(repo / pocket.INSTALL_DIR.relative_to(pocket.REPO)
                 / pocket.EXE_NAME)
    # fetch-fonts.py spells its destination inline in main(): web/fonts.
    found += [repo / "web" / "fonts" / f.name for f in fonts.FONTS]
    out = xterm.OUT.relative_to(xterm.REPO)
    for _, files in xterm.PACKAGES.values():
        found += [repo / out / name for name in files.values()]
    return found


def start_message(os_name: str, local_port: int) -> str:
    launcher = ("start.bat" if os_name == "nt"
                else ".venv/bin/python -X utf8 scripts/launch.py")
    return (f"Start Butler with: {launcher}\n"
            f"Then open http://127.0.0.1:{local_port} on this machine. "
            "The certificate is made on first run.")


# ----------------------------------------------------------------- the runner

def run_steps(steps) -> int:
    """Run `(name, fn)` in order; the first FAILED stops everything."""
    for name, fn in steps:
        result = fn()
        print(f"[{name}] {result.status} {result.detail}".rstrip())
        if result.status == "FAILED":
            return 1
    return 0


# -------------------------------------------------------------- shared probes

def current_value(ctx: Context) -> str | None:
    value = ctx.env.get("BUTLER_DATA") or None
    if value is None and ctx.os_name == "nt":
        value = ctx.registry_value or None
    return value


def probe_prereqs(ctx: Context) -> list[tuple[str, bool, str]]:
    """(label, ok, detail) for python, uv, node, npm. Runs `node --version`."""
    items = []
    ver = ".".join(str(p) for p in ctx.py_version[:3])
    items.append(("python", tuple(ctx.py_version[:2]) >= MIN_PYTHON,
                  f"{ver} (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} to run "
                  "this script)"))
    uv = ctx.which("uv")
    items.append(("uv", bool(uv),
                  uv or f"uv not found on PATH. Install: {UV_URL}"))
    node = ctx.which("node")
    if not node:
        items.append(("node", False,
                      f"node >= {MIN_NODE} not found on PATH. "
                      f"Install: {NODE_URL}"))
    else:
        out = ctx.run([node, "--version"], cwd=str(ctx.repo),
                      capture_output=True, text=True).stdout.strip()
        try:
            major = int(out.lstrip("v").split(".")[0])
        except ValueError:
            major = 0
        items.append(("node", major >= MIN_NODE,
                      f"{out}" if major >= MIN_NODE else
                      f"node {out or '?'} is older than {MIN_NODE}. "
                      f"Install a newer one: {NODE_URL}"))
    npm = ctx.which("npm")
    items.append(("npm", bool(npm),
                  npm or f"npm not found on PATH (ships with Node.js): "
                  f"{NODE_URL}"))
    return items


def resolve_for(ctx: Context) -> Path:
    return resolve_data_root(
        ctx.data_flag, ctx.env.get("BUTLER_DATA") or None,
        ctx.registry_value if ctx.os_name == "nt" else None,
        ctx.home, ctx.repo, ctx.legacy_default)


def decide(ctx: Context, data: Path) -> Decision:
    return persist_decision(current_value(ctx), str(data),
                            ctx.data_flag is not None, ctx.os_name,
                            ctx.home / "Butler-Data")


# --------------------------------------------------------------- install mode

def _sh(ctx: Context, argv: list[str]) -> Result | None:
    """Run an installer command; a Result only on failure."""
    code = ctx.run(argv, cwd=str(ctx.repo)).returncode
    if code != 0:
        return Result("FAILED", f"exit {code}: {' '.join(argv)}")
    return None


def prereqs_step(ctx: Context) -> Result:
    items = probe_prereqs(ctx)
    bad = [f"{label}: {detail}" for label, ok, detail in items if not ok]
    if bad:
        return Result("FAILED", "; ".join(bad))
    return Result("ok", ", ".join(f"{label} {detail}"
                                  for label, _, detail in items[:1]))


def venv_step(ctx: Context) -> Result:
    if venv_python(ctx.repo, ctx.os_name).exists():
        return Result("SKIP", ".venv already exists")
    failed = _sh(ctx, [ctx.which("uv"), "venv", ".venv", "--python",
                       VENV_PYTHON_VERSION])
    return failed or Result("done", f".venv created (Python "
                            f"{VENV_PYTHON_VERSION})")


def pip_step(ctx: Context) -> Result:
    py = venv_python(ctx.repo, ctx.os_name)
    failed = _sh(ctx, [ctx.which("uv"), "pip", "install", "--python",
                       str(py), "-r", "requirements.txt"])
    return failed or Result("ok", "requirements.txt installed")


def npm_step(ctx: Context) -> Result:
    failed = _sh(ctx, [ctx.which("npm"), "install"])
    if failed:
        return failed
    if ctx.os_name != "nt":
        # npm 11 skips node-pty's install script, so nothing marks its
        # spawn-helper executable and every in-page terminal fails to start
        # (docs/macos.md section 1).
        for helper in (ctx.repo / "node_modules" / "node-pty" / "prebuilds"
                       ).glob("*/spawn-helper"):
            os.chmod(helper, 0o755)
    return Result("ok", "node packages installed")


def _script(ctx: Context, name: str, *extra: str) -> list[str]:
    return [str(venv_python(ctx.repo, ctx.os_name)), "-X", "utf8",
            str(ctx.repo / "scripts" / name), *extra]


def models_step(ctx: Context) -> Result:
    extra = ["--nemotron"] if ctx.nemotron else []
    failed = _sh(ctx, _script(ctx, "fetch-models.py", *extra))
    return failed or Result("ok", "models present and verified")


def pocket_step(ctx: Context) -> Result:
    failed = _sh(ctx, _script(ctx, "fetch-pocket-tts.py"))
    return failed or Result("ok", "Pocket TTS binary and weights present")


def assets_step(ctx: Context) -> Result:
    failed = (_sh(ctx, _script(ctx, "fetch-fonts.py"))
              or _sh(ctx, _script(ctx, "fetch-xterm.py")))
    return failed or Result("ok", "fonts and terminal view present")


def data_step(ctx: Context) -> Result:
    try:
        data = resolve_for(ctx)
    except InstallError as e:
        return Result("FAILED", str(e))
    decision = decide(ctx, data)
    if decision.action == "refuse":
        return Result("FAILED", decision.message)
    ctx.data = data
    records = data / paths.RECORDS_DIR
    created = not records.is_dir()
    records.mkdir(parents=True, exist_ok=True)
    return Result("done" if created else "ok", f"{data} (records in "
                  f"{records.name})")


def persist_step(ctx: Context) -> Result:
    decision = decide(ctx, ctx.data)
    action = decision.action
    if action in ("ok", "keep", "none_needed"):
        return Result("ok", decision.message)
    if action == "refuse":
        return Result("FAILED", decision.message)
    if action == "setx":
        code = ctx.run(["setx", "BUTLER_DATA", str(ctx.data)]).returncode
        if code != 0:
            return Result("FAILED", f"setx exited {code}")
        return Result("done", f"saved BUTLER_DATA={ctx.data}; open a NEW "
                      "terminal for it to take effect")
    shown = str(ctx.data)
    if " " in shown:
        shown = f'"{shown}"'
    return Result("done (manual)",
                  "add this line to your shell profile (for example "
                  f"~/.zshrc): export BUTLER_DATA={shown}")


def finish_step(ctx: Context) -> Result:
    return Result("ok", "\n" + start_message(ctx.os_name, ctx.local_port))


def install_steps(ctx: Context):
    return [("prereqs", lambda: prereqs_step(ctx)),
            ("venv", lambda: venv_step(ctx)),
            ("python packages", lambda: pip_step(ctx)),
            ("node packages", lambda: npm_step(ctx)),
            ("models", lambda: models_step(ctx)),
            ("pocket tts", lambda: pocket_step(ctx)),
            ("fonts and terminal view", lambda: assets_step(ctx)),
            ("data root", lambda: data_step(ctx)),
            ("persist BUTLER_DATA", lambda: persist_step(ctx)),
            ("start", lambda: finish_step(ctx))]


# ----------------------------------------------------------------- check mode

def check_items(ctx: Context) -> list[tuple[str, bool, str]]:
    items = probe_prereqs(ctx)

    py = venv_python(ctx.repo, ctx.os_name)
    if not py.exists():
        items.append((".venv", False, f"MISSING {py}"))
    else:
        code = ctx.run([str(py), "-c", f"import {KEY_PACKAGE}"],
                       cwd=str(ctx.repo), capture_output=True,
                       text=True).returncode
        items.append((".venv", code == 0, f"imports {KEY_PACKAGE}"
                      if code == 0 else f"cannot import {KEY_PACKAGE}"))

    npm = ctx.which("npm")
    if not npm:
        items.append(("node_modules", False, "npm not found"))
    else:
        code = ctx.run([npm, "ls", "--depth=0"], cwd=str(ctx.repo),
                       capture_output=True, text=True).returncode
        items.append(("node_modules", code == 0, "npm ls --depth=0 exit "
                      f"{code}"))

    for path in expected_files(ctx.repo, ctx.nemotron):
        rel = path.relative_to(ctx.repo).as_posix()
        items.append((rel, path.is_file(),
                      "present (hash not checked)" if path.is_file()
                      else "MISSING"))

    try:
        data = resolve_for(ctx)
    except InstallError as e:
        items.append(("data root", False, str(e)))
        return items
    records = data / paths.RECORDS_DIR
    items.append(("data root", records.is_dir(),
                  f"{data}" if records.is_dir()
                  else f"MISSING {records}"))
    decision = decide(ctx, data)
    persisted = decision.action in ("ok", "keep", "none_needed")
    items.append(("BUTLER_DATA", persisted,
                  decision.message if persisted else
                  "not saved; run the installer (" + decision.message + ")"))
    return items


def run_check(ctx: Context) -> int:
    items = check_items(ctx)
    for label, ok, detail in items:
        print(f"[{label}] {'ok' if ok else 'FAILED'} {detail}")
    good = sum(1 for _, ok, _ in items if ok)
    print(f"{good} of {len(items)} ok")
    return 0 if good == len(items) else 1


# ------------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="install")
    ap.add_argument("--data", help="data root, a folder OUTSIDE the repo")
    ap.add_argument("--nemotron", action="store_true",
                    help="also fetch the optional multilingual speech model")
    ap.add_argument("--check", action="store_true",
                    help="report what is installed; change nothing")
    args = ap.parse_args(argv)

    os_name = os.name
    launch = _load("launch", "launch.py")
    registry = launch._registry_data() if os_name == "nt" else None
    ctx = Context(
        repo=REPO, os_name=os_name, run=subprocess.run, which=shutil.which,
        env=dict(os.environ), registry_value=registry, home=Path.home(),
        data_flag=args.data, nemotron=args.nemotron,
        local_port=launch.LOCAL_PORT, py_version=tuple(sys.version_info[:3]),
        legacy_default=Path(launch.DEFAULT_DATA) if os_name == "nt" else None)
    if args.check:
        return run_check(ctx)
    return run_steps(install_steps(ctx))


if __name__ == "__main__":
    sys.exit(main())
