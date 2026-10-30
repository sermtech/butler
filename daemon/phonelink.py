"""Phone link: the PC's Tailscale address as a QR code, and the one button
that sets it up (docs/desktop-app.md section 5, DA-15, DA-16, AC-553,
AC-554).

The answer is the SAME pairing document `daemon/whatsapp.py` `page_doc`
serves (`{ok, status, headline, detail, tone, qr, qr_count, poll,
actions}`, or `{ok: false, error, poll, actions}`), so the settings page
renders it through its one generic block (`Group.pairing`, AC-41) and the
desktop app's Phone link page reads the same route.

WHAT IT RUNS
------------
Three Tailscale commands, every one through `_tailscale` as an argv list
(no shell), with a timeout and the output captured:

- `tailscale status --json`        read-only: installed, signed in, the name
- `tailscale serve status --json`  read-only: what the name forwards to now
- `tailscale serve --bg --https=<port> http://127.0.0.1:<PROXIED port>`
  only on `POST`, only from this machine (`hub.is_local`, AC-541)

The forward targets the daemon's ACTUAL PROXIED listener (read off the
app's listener map, never a constant), so a phone arrives on the listener
that is never trusted as local. Tailscale's public-internet exposure command
is never run: phone access stays inside the owner's tailnet (section 9); a
source test holds that no string in this module names it.

An HTTPS port already forwarding somewhere else is NEVER replaced: on the
desk 443 already served another program when this was written [MEASURED
2026-10-07], and a silent overwrite would break it. The port is the
`phone_link_port` setting.

QR codes come from the `qrcode` node package the WhatsApp sidecar already
depends on (`package.json`), through a one-shot `node -e`: no Python QR
library is installed, and adding one would be a second copy.

Blocking calls run through `asyncio.to_thread` (D-1, D-25).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from daemon import childproc
from daemon import hub as hub_mod

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REPO = Path(__file__).resolve().parents[1]

PORT_KEY = "phone_link_port"
STATUS_TIMEOUT_S = 15.0
# `serve` can stop and wait for the owner to enable Serve on the tailnet in
# the admin console; the timeout ends that wait and its words are shown.
SERVE_TIMEOUT_S = 30.0
QR_TIMEOUT_S = 20.0

SETUP = {"id": "setup", "label": "Set up phone access", "method": "POST",
         "query": "", "confirm": ""}
STEPS = ("1. Scan this code with your phone's camera; the phone needs "
         "Tailscale signed in to the same account. 2. Allow the microphone "
         "when the page asks. 3. Add to Home Screen from the browser's share "
         "menu, so Butler opens like an app.")
CERT_STEP = ("One step in the Tailscale admin console: open DNS "
             "(https://login.tailscale.com/admin/dns), switch on Enable HTTPS "
             "Certificates, then press the button again.")
_QR_JS = ("require('qrcode').toDataURL(process.argv[1], {margin: 1, "
          "width: 320}).then(u => process.stdout.write(u), e => "
          "{ process.stderr.write(String(e)); process.exit(1); })")


class LinkError(RuntimeError):
    """Something this module ran failed; the message is shown as is."""


#: macOS: the Tailscale app keeps its CLI inside the bundle, off PATH
#: (found 2026-10-29 on the Mac mini, docs/macos.md).
MAC_APP_CLI = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def tailscale_path() -> str | None:
    """The Tailscale CLI, or None when it is not installed. A function, so a
    test can put a fake one first on PATH. On macOS, the app bundle's CLI
    when none is on PATH."""
    found = shutil.which("tailscale")
    if found is None and sys.platform == "darwin" and MAC_APP_CLI.exists():
        return str(MAC_APP_CLI)
    return found


def _run(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """Run a one-shot child; (exit code or None on timeout, its words)."""
    proc = subprocess.Popen(argv, cwd=str(REPO), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    childproc.assign(proc)                                  # AC-36, D-24
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        code = None
    words = "\n".join(t.strip() for t in (out, err) if t and t.strip())
    return code, words


def _tailscale(exe: str, *args: str, timeout: float) -> tuple[int | None, str]:
    """Every Tailscale command this module runs goes through here."""
    return _run([exe, *args], timeout)


def _json(exe: str, *args: str) -> dict:
    code, words = _tailscale(exe, *args, timeout=STATUS_TIMEOUT_S)
    if code != 0:
        raise LinkError(f"`tailscale {' '.join(args)}` failed"
                        + ("" if code is not None else " (timed out)")
                        + f":\n{words}")
    if not words:
        return {}
    try:
        data = json.loads(words)
    except ValueError as e:
        raise LinkError(f"`tailscale {' '.join(args)}` did not answer "
                        f"JSON:\n{words}") from e
    if not isinstance(data, dict):
        raise LinkError(f"`tailscale {' '.join(args)}` answered {words!r}")
    return data


def qr_data_url(text: str) -> str:
    """A PNG data URL of `text`, drawn by the node `qrcode` package."""
    node = shutil.which("node")
    if node is None:
        raise LinkError("node is not on PATH, so the QR code cannot be drawn")
    code, words = _run([node, "-e", _QR_JS, text], QR_TIMEOUT_S)
    if code != 0 or not words.startswith("data:image/png;base64,"):
        raise LinkError(f"the QR code could not be drawn: {words[:300]}")
    return words


def proxied_port(app: web.Application) -> int | None:
    """The PROXIED listener's bound port, or None when it is off."""
    ports = app.get(hub_mod.LISTENERS) or {}
    found = [p for p, kind in ports.items() if kind == hub_mod.PROXIED]
    return found[0] if found else None


def _doc(status: str, headline: str, detail: str, tone: str,
         actions=(), qr: str | None = None) -> dict:
    return {"ok": True, "status": status, "headline": headline,
            "detail": detail, "tone": tone, "qr": qr, "qr_count": 0,
            "poll": False, "actions": [dict(a) for a in actions]}


def _error(text: str, actions=()) -> dict:
    return {"ok": False, "error": text, "poll": False,
            "actions": [dict(a) for a in actions]}


def survey(proxy: int | None, https: int) -> dict:
    """What Tailscale says now, as plain facts. Blocking."""
    if proxy is None:
        return {"state": "off"}
    exe = tailscale_path()
    if exe is None:
        return {"state": "missing"}
    status = _json(exe, "status", "--json")
    backend = str(status.get("BackendState") or "")
    if backend != "Running":
        return {"state": "logged_out", "backend": backend or "unknown"}
    name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not name:
        raise LinkError("Tailscale gives this PC no DNS name. Turn on "
                        "MagicDNS in the Tailscale admin console (DNS).")
    serve = _json(exe, "serve", "status", "--json")
    handlers = ((serve.get("Web") or {}).get(f"{name}:{https}") or {}) \
        .get("Handlers") or {}
    root = handlers.get("/") or {}
    raw = (serve.get("TCP") or {}).get(str(https)) or {}
    return {"state": "ready", "exe": exe, "name": name,
            "certs": bool(status.get("CertDomains")),
            "forward": str(root.get("Proxy") or "").rstrip("/") or None,
            "taken": ((bool(handlers) and not root.get("Proxy"))
                      or bool(raw.get("TCPForward")))}


def target(proxy: int) -> str:
    return f"http://127.0.0.1:{proxy}"


def address(name: str, https: int) -> str:
    return f"https://{name}/" if https == 443 else f"https://{name}:{https}/"


def document(facts: dict, proxy: int | None, https: int, local: bool) -> dict:
    """The page's document for `facts` (`survey`). Pure, except the QR."""
    state = facts["state"]
    if state == "off":
        return _doc("off", "Phone access is off",
                    "Butler was started without its phone listener "
                    "(--proxy-port 0), so there is nothing for Tailscale to "
                    "forward to. Start Butler from the desktop app or "
                    "start.bat, which open it.", "neutral")
    if state == "missing":
        return _doc("missing", "Tailscale is not installed",
                    "Install Tailscale on this PC "
                    "(https://tailscale.com/download) and sign in, then open "
                    "this again.", "warning")
    if state == "logged_out":
        return _doc("logged_out", "Tailscale is not signed in",
                    f"Tailscale on this PC reports {facts['backend']}. Open "
                    f"Tailscale and sign in, then open this again.",
                    "warning")
    url = address(facts["name"], https)
    if facts["forward"] == target(proxy):
        return _doc("linked", f"Your phone opens {url}", STEPS, "success",
                    qr=qr_data_url(url))
    if facts["forward"] or facts["taken"]:
        what = facts["forward"] or "something other than a web forward"
        return _doc("taken", f"Port {https} is already in use",
                    f"{url} already forwards to {what}. Butler will not "
                    f"replace it. Choose another Phone link HTTPS port in "
                    f"Settings, under Devices, or remove that forward if "
                    f"nothing needs it.", "warning")
    if not local:
        return _doc("ready", "Phone access is not set up",
                    "Set it up from Butler on the PC itself: it runs "
                    "Tailscale there.", "neutral")
    return _doc("ready", "Phone access is not set up",
                f"Butler is not reachable from your phone yet. This makes it "
                f"available at {url}, only on your own Tailscale network, "
                f"never the public internet.", "neutral", actions=(SETUP,))


def set_up(facts: dict, proxy: int, https: int) -> None:
    """Run the forward. Raises `LinkError` with Tailscale's words VERBATIM
    (AC-554), plus the admin-console step when the tailnet has no HTTPS
    certificates. Blocking."""
    code, words = _tailscale(facts["exe"], "serve", "--bg",
                             f"--https={https}", target(proxy),
                             timeout=SERVE_TIMEOUT_S)
    if code == 0:
        return
    head = ("Tailscale did not finish in "
            f"{SERVE_TIMEOUT_S:g} s. Its own message, unchanged:"
            if code is None else
            "Tailscale refused. Its own message, unchanged:")
    tail = "" if facts["certs"] else f"\n\n{CERT_STEP}"
    raise LinkError(f"{head}\n{words}{tail}")


def add_routes(app: web.Application, hub) -> None:
    """`GET` and `POST /api/phonelink`.

    GET is open like every settings read (R2): any tailnet device may see
    the address its owner scans, and only a caller on this machine is
    offered the button. POST runs a command that changes the owner's
    tailnet, so it is LOCAL ONLY (`hub.is_local`, AC-541): 403 on PROXIED,
    and on MAIN from any other peer, before anything runs.
    """
    if hub.settings is None:
        return
    lock = asyncio.Lock()

    def https_port() -> int:
        value = hub.settings.get(PORT_KEY)
        if value != int(value):
            raise LinkError(f"{PORT_KEY} is {value!r}, not a whole port "
                            f"number")
        return int(value)

    async def read(req, local: bool) -> tuple[int, dict, dict | None]:
        try:
            proxy, https = proxied_port(req.app), https_port()
            facts = await asyncio.to_thread(survey, proxy, https)
            doc = await asyncio.to_thread(document, facts, proxy, https,
                                          local)
        except LinkError as e:
            return 502, _error(str(e)), None
        return 200, doc, facts

    async def get(req):
        status, doc, _ = await read(req, hub_mod.is_local(req))
        return web.json_response(doc, status=status)

    async def post(req):
        if not hub_mod.is_local(req):
            return web.json_response(
                _error("Phone access can only be set up from Butler on this "
                       "PC."), status=403)
        async with lock:
            status, doc, facts = await read(req, True)
            if status != 200 or doc["status"] != "ready":
                return web.json_response(
                    doc, status=status if status != 200 else (
                        200 if doc.get("status") == "linked" else 409))
            try:
                await asyncio.to_thread(set_up, facts, proxied_port(req.app),
                                        https_port())
            except LinkError as e:
                return web.json_response(_error(str(e), (SETUP,)),
                                         status=502)
            status, doc, _ = await read(req, True)
            return web.json_response(doc, status=status)

    app.router.add_get("/api/phonelink", get)
    app.router.add_post("/api/phonelink", post)
