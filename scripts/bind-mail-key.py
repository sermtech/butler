"""Point a keyboard's MAIL button at Butler's page instead of a mail client.

`docs/shortcuts.md` section 10. A script rather than a hand-edit, for
`make-cert.py`'s reason: it is machine setup that has to survive a Windows
reset, and an undo nobody wrote down is not an undo.

WHY THIS IS NOT A BUTLER BINDING
--------------------------------
Butler CANNOT bind this key, and that is measured rather than assumed
[`test/manual/spike_launch_keys.py`, 2026-09-09]. Across 100 s of watching, a
`WH_KEYBOARD_LL` hook saw 55 ordinary key-downs -- so it was alive and
receiving -- and ZERO keys in 0xA6-0xB7. The mail button on this keyboard never
becomes a keyboard event: it arrives as a HID consumer usage that Windows turns
straight into `WM_APPCOMMAND`, and a hook cannot see what was never a keystroke.
There is no in-process way round it either, because `WM_APPCOMMAND` goes to the
FOREGROUND window and the shell and a process can only receive one for its own
windows -- the desk agent has none.

WHAT IT DOES INSTEAD
--------------------
Windows consults `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\
AppKey\\<n>` before falling back to an association, where `<n>` is the
APPCOMMAND value -- 15 for mail. A `ShellExecute` value there is what the key
runs.

READ from this machine on 2026-09-09, which is why this route was chosen:
`HKCU\\...\\AppKey` did not exist at all, the machine-wide `HKLM\\...\\AppKey`
had subkeys 7, 15, 16, 17 and 18 (browser-home, mail, media-select, app1, app2)
and **all five were EMPTY**, and `mailto:`'s UserChoice ProgId was `ChromeHTML`.
So nothing was overriding the key and it was falling through to Chrome, which is
exactly the symptom the owner reported.

**`ShellExecute` takes a FILE, not a command line**, so a browser plus arguments
cannot go in it directly -- one string would be treated as a filename. A `.lnk`
carries its own arguments and is the most ShellExecute-native thing there is, so
`--app-window` writes one and points the key at that. `--url-only` skips the
shortcut and puts the URL in the value, which opens an ordinary tab in the
default browser and is the variant with the fewest moving parts.

HKCU, so no administrator rights are needed and nothing machine-wide changes.
`--remove` deletes the one key and the shortcut, and the button goes back to
opening mail.

Run:
    .venv\\Scripts\\python.exe -X utf8 scripts\\bind-mail-key.py --show
    .venv\\Scripts\\python.exe -X utf8 scripts\\bind-mail-key.py --install
    .venv\\Scripts\\python.exe -X utf8 scripts\\bind-mail-key.py --install --url-only
    .venv\\Scripts\\python.exe -X utf8 scripts\\bind-mail-key.py --remove
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import winreg
from pathlib import Path

# The APPCOMMAND value for the mail button, which is also the registry subkey
# name. The others this machine registers, for anyone repointing a different
# button: 7 browser-home, 16 media-select, 17 app1, 18 app2.
APPKEY_MAIL = "15"
APPKEY_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\AppKey"

# `butlerd.DEFAULT_PORT` is the source of truth for the port and `--tls` is the
# default posture; both are restated here rather than imported, because importing
# `butlerd` pulls in aiohttp and onnxruntime to read one integer. `--url`
# overrides, and `localhost` is deliberate: it is in the certificate's SANs
# [MEASURED 2026-09-09] and a secure context, so the page's microphone works.
DEFAULT_URL = "https://localhost:8140/"

# Outside the repository on purpose. This is per-machine state, not a work
# product, and a shortcut carrying one machine's browser path has no business in
# version control.
SHORTCUT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Butler"
SHORTCUT = SHORTCUT_DIR / "open-butler.lnk"

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> str:
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    raise SystemExit(
        "no Chrome or Edge found in the usual places, so an app window cannot "
        "be built. Use --url-only, which hands the URL to whatever browser is "
        "the default.")


def write_shortcut(url: str) -> Path:
    """A `.lnk` running the browser in APP MODE, via WScript.Shell.

    App mode because the page is a satellite rather than a document: a chromeless
    window has no tab strip, no address bar and no other tabs to be buried under.
    Butler already ships a web app manifest (AC-106), so it behaves like an
    application either way.

    PowerShell rather than pywin32, because pywin32 is not a dependency of this
    project and adding one to write a single shortcut would be the "reach for a
    library to avoid one conditional" trap.
    """
    browser = find_browser()
    SHORTCUT_DIR.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell)."
        f"CreateShortcut('{SHORTCUT}');"
        f"$s.TargetPath = '{browser}';"
        f"$s.Arguments = '--app={url}';"
        f"$s.Description = 'Butler satellite (set by scripts/bind-mail-key.py)';"
        "$s.Save()"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True)
    if done.returncode != 0 or not SHORTCUT.is_file():
        raise SystemExit(f"the shortcut would not be written: "
                         f"{done.stderr.strip() or done.stdout.strip()}")
    print(f"  shortcut : {SHORTCUT}")
    print(f"             {browser} --app={url}")
    return SHORTCUT


def show() -> int:
    """What the key is bound to right now, and what it falls back to.

    Read-only, and worth having: this is the first thing to run if the button
    ever stops doing what it should.
    """
    print(f"HKCU\\{APPKEY_ROOT}")
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPKEY_ROOT)
    except OSError:
        print("  (absent -- no per-user app-key override at all)")
    else:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
                i += 1
            except OSError:
                break
            k = winreg.OpenKey(root, sub)
            vals = []
            j = 0
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, j)
                    vals.append(f"{n}={v!r}")
                    j += 1
                except OSError:
                    break
            print(f"  {sub}: {', '.join(vals) or '(no values)'}")
    print(f"shortcut   : {SHORTCUT} "
          f"{'exists' if SHORTCUT.is_file() else 'absent'}")
    try:
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\mailto\UserChoice")
        print(f"mailto:    : handled by {winreg.QueryValueEx(k, 'ProgId')[0]!r} "
              f"(what the key does with NO override)")
    except OSError:
        print("mailto:    : no UserChoice")
    return 0


def install(url: str, url_only: bool) -> int:
    target = url if url_only else str(write_shortcut(url))
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                             f"{APPKEY_ROOT}\\{APPKEY_MAIL}", 0,
                             winreg.KEY_SET_VALUE)
    with key:
        winreg.SetValueEx(key, "ShellExecute", 0, winreg.REG_SZ, target)
    print(f"  registry : HKCU\\{APPKEY_ROOT}\\{APPKEY_MAIL}")
    print(f"             ShellExecute = {target!r}")
    print("\nPRESS THE MAIL BUTTON. Three outcomes and they mean different "
          "things:")
    print("  Butler opens        -> done.")
    print("  nothing happens     -> Windows IS consulting AppKey, and the "
          "target is wrong.")
    print("                         Try --install --url-only.")
    print("  a mail tab opens    -> Windows is NOT consulting AppKey for this "
          "key on this")
    print("                         keyboard. Nothing here can redirect it; the "
          "vendor's own")
    print("                         software is the only route left.")
    print("\nUndo at any time:  scripts\\bind-mail-key.py --remove")
    return 0


def remove() -> int:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                         f"{APPKEY_ROOT}\\{APPKEY_MAIL}")
        print(f"  deleted  : HKCU\\{APPKEY_ROOT}\\{APPKEY_MAIL}")
    except FileNotFoundError:
        print("  registry : nothing to delete")
    if SHORTCUT.is_file():
        SHORTCUT.unlink()
        print(f"  deleted  : {SHORTCUT}")
    print("the mail button goes back to opening whatever handles mailto:")
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        raise SystemExit("Windows only")
    ap = argparse.ArgumentParser(prog="bind-mail-key")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--url-only", action="store_true",
                    help="put the URL straight in the value: an ordinary tab in "
                         "the default browser, and no shortcut file")
    args = ap.parse_args(argv)

    if args.remove:
        return remove()
    if args.install:
        return install(args.url, args.url_only)
    return show()


if __name__ == "__main__":
    sys.exit(main())
