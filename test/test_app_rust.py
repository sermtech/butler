"""The desktop app's decision logic, by `cargo test`'s EXIT CODE
(docs/desktop-app.md section 8: "run from one pytest wrapper so the gate
sees it by exit code").

`app/src-tauri/src/logic.rs` holds every decision the shell makes: the
microphone gate (DA-13, AC-548: the microphone for http://127.0.0.1:8141
only, everything else denied), where links go (DA-14, AC-549), finding the
checkout (AC-551), the status machine (AC-544, AC-550), Quit's escalation
(DA-11, AC-545) and what `scripts/launch.py`'s output means (DA-9).
Fails as: a page in the window granted the camera, or the microphone for
another origin, with no prompt.

Needs a Rust toolchain that can BUILD on Windows: MSVC Build Tools, or --
the owner's choice on the desk, 2026-10-07 -- the Microsoft CRT and SDK
fetched by xwin, named by the user variable BUTLER_XWIN. When BUTLER_XWIN is
set, `xwin_env` points clang-cl, lld-link and llvm-lib (LLVM) at it; when it
is not, cargo runs as-is and MSVC must be installed. No skip: a gate that
cannot run says so by failing.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "app" / "src-tauri" / "Cargo.toml"
# A cold build compiles Tauri (minutes); a warm one takes seconds.
RUN_TIMEOUT_S = 1200


def xwin_env(env: dict) -> dict:
    """The build environment for an xwin CRT/SDK at BUTLER_XWIN; `env`
    unchanged when the variable is not set (MSVC is then required)."""
    root = env.get("BUTLER_XWIN")
    if not root:
        return env
    x = Path(root)
    assert (x / "crt" / "lib" / "x86_64").is_dir(), (
        f"BUTLER_XWIN={root} has no crt\lib\x86_64: not an xwin splat")
    return {**env,
            "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER": "lld-link",
            "CC": "clang-cl", "CXX": "clang-cl", "AR": "llvm-lib",
            "LIB": ";".join(str(x / d) for d in (
                "crt/lib/x86_64", "sdk/lib/um/x86_64", "sdk/lib/ucrt/x86_64")),
            "INCLUDE": ";".join(str(x / d) for d in (
                "crt/include", "sdk/include/ucrt", "sdk/include/um",
                "sdk/include/shared", "sdk/include/winrt"))}


def test_app_rust_unit_tests_pass():
    cargo = shutil.which("cargo")
    assert cargo, "cargo is not on PATH (install Rust: https://rustup.rs)"
    proc = subprocess.run(
        [cargo, "test", "--manifest-path", str(MANIFEST)],
        env=xwin_env(dict(os.environ)),
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=RUN_TIMEOUT_S)
    assert proc.returncode == 0, (
        f"cargo test exited {proc.returncode}\n{proc.stdout[-4000:]}\n"
        f"{proc.stderr[-4000:]}")
