"""Who may call Butler: the one gate (docs/remote-access.md RA-1..RA-11,
AC-720..AC-729, AC-731).

Every request -- route, static file and `/ws` -- passes `gate` before any
handler. The answer comes from the LISTENER the request arrived on
(`hub.listener_of`, read off the accepting socket), never from a header the
caller controls:

- LOCAL (8141, bound to 127.0.0.1): the owner.
- MAIN (8140): the owner from a loopback peer (`hub._is_local`); anyone
  else needs a paired device's cookie.
- PROXIED (8142, behind `tailscale serve`): the owner when Tailscale's
  `Tailscale-User-Login` is in the owner list (RA-6); serve strips a
  client's copy of that header, and only serve can reach a 127.0.0.1 port
  from the tailnet. Anyone else needs a paired device's cookie.

Before that, on every listener: no proxy in front of a loopback-trusted
listener (RA-4), and same-origin by `Origin`, `Sec-Fetch-Site` and, on
plain HTTP, `Host` (RA-3). `hub.is_local` stays the SECOND, narrower check
on the agent routes. A gate refusal carries `X-Butler-Gate: refused`; a
handler's own refusal never does.

A DEVICE has the owner's rights except the RA-11 actions, so a stolen
cookie cannot mint codes, pair more devices or edit the owner list, and
removing it ends it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import tempfile
import time
from pathlib import Path

from aiohttp import web
from yarl import URL

from daemon import hub as hub_mod
from daemon import phonelink

OWNER, DEVICE = "owner", "device"
GATE_HEADER = "X-Butler-Gate"
GATE = web.AppKey("butler.gate", object)
#: Who the gate let through, on the request: OWNER, DEVICE, or None for an
#: exempt route.
CALLER = web.RequestKey("butler.caller", object)
COOKIE = "butler_device"

#: RA-5, the closed list. RA-3 and RA-4 still apply to these. A source test
#: holds it (AC-731).
EXEMPT = frozenset({
    ("POST", "/api/pair"),            # pairing itself
    ("POST", "/api/hooks/{name}"),    # its own X-Butler-Token (AC-498)
    ("GET", "/manifest.json"),        # fetched by browsers without cookies
    ("GET", "/icon.svg"),
})
#: RA-11: a DEVICE is refused these, the owner is not.
OWNER_ONLY = frozenset({
    ("GET", "/api/devices"),
    ("HEAD", "/api/devices"),
    ("POST", "/api/devices"),
    ("DELETE", "/api/devices"),
    ("POST", "/api/settings/reset"),
    # H2 (owner, 2026-10-29): what outlives a removed device -- a webhook
    # and its token, a notify channel, a model endpoint, an automation, the
    # WhatsApp link -- is made only by the owner.
    ("POST", "/api/triggers"),
    ("DELETE", "/api/triggers/{name}"),
    ("POST", "/api/notify"),
    ("DELETE", "/api/notify/{name}"),
    ("POST", "/api/connections"),
    ("DELETE", "/api/connections/{cid}"),
    ("POST", "/api/automations"),
    ("POST", "/api/automations/bulk"),
    ("POST", "/api/whatsapp/pairing"),
    ("DELETE", "/api/whatsapp/pairing"),
    # docs/project-roots.md PJ-1: a project row is the file explorer's root.
    ("POST", "/api/shortcuts"),
    ("DELETE", "/api/shortcuts/{sid}"),
    # docs/chats-and-terminals.md CT-3 (AC-761): a terminal is a shell.
    ("GET", "/api/terminals"),
    ("HEAD", "/api/terminals"),
    ("POST", "/api/terminals"),
    ("DELETE", "/api/terminals/{tid}"),
    ("GET", "/ws/terminal/{tid}"),
    ("HEAD", "/ws/terminal/{tid}"),
})
#: RA-11: settings only the owner may change.
OWNER_KEYS = frozenset({"lan_access", "tailscale_owners",
                        "terminals_enabled"})
#: RA-4: what a forwarding proxy adds. Prefixes, lower case.
PROXY_HEADERS = ("tailscale-user-", "x-forwarded-", "forwarded",
                 "x-real-ip", "true-client-ip")
#: RA-3 on PROXIED: `tailscale serve` keeps the phone's Host, a name under
#: this suffix. Only Tailscale answers DNS for it, so a page cannot rebind
#: one to 127.0.0.1 (attack review 2026-10-29, H1).
TAILNET_SUFFIX = ".ts.net"
LOGIN = "Tailscale-User-Login"
#: RA-3: the fetch-metadata values a page Butler served sends.
SAME_SITE = frozenset({"same-origin", "none"})
#: Host spellings that mean "this machine", for `pair_url` only (not a
#: trust decision: that is `hub._is_local`).
THIS_MACHINE = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

CODE_TTL_S = 300.0          # RA-8: five minutes
PEER_TRIES = 5              # RA-8: wrong guesses per peer, then the code burns
TOTAL_TRIES = 20            # RA-8: wrong guesses in total
SELF_TTL_S = 60.0           # RA-6: the self login, re-read once a minute
SEEN_EVERY_S = 3600.0       # last-seen is written at most hourly


class Refused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _route_key(request: web.Request) -> tuple[str, str]:
    """(method, canonical route) -- a static file by its path."""
    resource = request.match_info.route.resource
    if resource is None or isinstance(resource, web.StaticResource):
        return request.method, request.path
    return request.method, resource.canonical


def _host_ok(host: str | None) -> bool:
    """RA-3 on plain HTTP: `localhost` or an IP literal. A hostname over
    plain HTTP is what a DNS-rebinding page looks like."""
    if not host or "@" in host:
        return False
    name = URL(f"http://{host}").host or ""
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    return True


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def self_login() -> tuple[str | None, str]:
    """This machine's Tailscale login, or None and why (RA-6). Blocking."""
    exe = phonelink.tailscale_path()
    if exe is None:
        return None, "Tailscale is not installed on this machine"
    try:
        status = phonelink._json(exe, "status", "--json")
    except phonelink.LinkError as e:
        return None, str(e)
    me = status.get("Self") or {}
    if me.get("Tags"):
        return None, "this machine is a tagged Tailscale node, so it has " \
                     "no owner login"
    user = (status.get("User") or {}).get(str(me.get("UserID"))) or {}
    login = str(user.get("LoginName") or "").strip().lower()
    if not login:
        return None, "Tailscale did not name this machine's login"
    return login, ""


class Gate:
    """The gate's state: paired devices, the live code, the owner cache."""

    def __init__(self, hub, records: Path, clock=time.monotonic):
        self.hub = hub
        self.path = records / "devices.json"
        self.clock = clock
        self.devices: list[dict] = self._load()
        self.code: dict | None = None
        self._self: tuple[float, frozenset, str] | None = None
        self._lock: asyncio.Lock | None = None

    # -- devices ----------------------------------------------------------- #
    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return list(data["devices"])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"devices": self.devices}, f, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def device_of(self, request: web.Request) -> dict | None:
        token = request.cookies.get(COOKIE)
        if not token:
            return None
        digest = _hash(token)
        for d in self.devices:
            if hmac.compare_digest(d["hash"], digest):
                now = time.time()
                if now - d["seen"] > SEEN_EVERY_S:
                    d["seen"] = now
                    self._save()
                return d
        return None

    # -- owner list (RA-6) --------------------------------------------------- #
    async def owners(self) -> tuple[frozenset, str]:
        settings = self.hub.settings
        raw = str(settings.get("tailscale_owners") or "") if settings else ""
        listed = frozenset(x.strip().lower() for x in raw.split(",")
                           if x.strip())
        if listed:
            return listed, ""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._self is None or self.clock() >= self._self[0]:
                login, why = await asyncio.to_thread(self_login)
                self._self = (self.clock() + SELF_TTL_S,
                              frozenset({login}) if login else frozenset(),
                              why)
            return self._self[1], self._self[2]

    # -- the decision ------------------------------------------------------ #
    async def decide(self, request: web.Request) -> tuple[str | None, dict | None]:
        """(OWNER | DEVICE | None for exempt, the device) or `Refused`."""
        listener = hub_mod.listener_of(request)
        if listener != hub_mod.PROXIED:
            for name in request.headers:
                if name.lower().startswith(PROXY_HEADERS):
                    raise Refused(403, "a proxy may not forward to this "
                                       "listener; point it at the proxy port")
        if not hub_mod.ws_origin_ok(request):
            raise Refused(403, "cross_site_origin")
        site = request.headers.get("Sec-Fetch-Site")
        if site is not None and site not in SAME_SITE and not (
                request.method == "GET" and request.path == "/"
                and request.headers.get("Sec-Fetch-Mode") == "navigate"):
            raise Refused(403, "cross_site_request")
        if (listener == hub_mod.LOCAL or (listener == hub_mod.MAIN
                                          and request.scheme != "https")) \
                and not _host_ok(request.headers.get("Host")):
            raise Refused(403, "foreign_host")
        if listener == hub_mod.PROXIED:
            host = request.headers.get("Host") or ""
            name = "" if "@" in host else (URL(f"http://{host}").host or "")
            if not name.lower().endswith(TAILNET_SUFFIX):
                raise Refused(403, "foreign_host")
            if len(request.headers.getall(LOGIN, [])) > 1:
                raise Refused(403, "two login headers")

        key = _route_key(request)
        if key in EXEMPT:
            return None, None
        if hub_mod._is_local(listener, request.remote or ""):
            return OWNER, None
        login = request.headers.get(LOGIN)
        refusal = Refused(401, "this device is not paired")
        if listener == hub_mod.PROXIED and login:
            owners, why = await self.owners()
            if login.strip().lower() in owners:
                return OWNER, None
            if not owners:
                refusal = Refused(503, "no Tailscale owner is known: "
                                       f"{why}. Set tailscale_owners on "
                                       "this machine's settings page.")
        device = self.device_of(request)
        if device is None:
            raise refusal
        if key in OWNER_ONLY:
            raise Refused(403, "only the owner may do this")
        if key == ("POST", "/api/settings"):
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if isinstance(body, dict) and OWNER_KEYS & set(body):
                raise Refused(403, "only the owner may change "
                                   + ", ".join(sorted(OWNER_KEYS & set(body))))
        return DEVICE, device

    # -- pairing (RA-8, RA-9) ---------------------------------------------- #
    def mint(self) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.code = {"code": code, "expires": self.clock() + CODE_TTL_S,
                     "fails": 0, "by_peer": {}}
        return code

    def redeem(self, given: str, peer: str) -> bool:
        live = self.code
        if live is None or self.clock() >= live["expires"]:
            self.code = None
            return False
        if hmac.compare_digest(given.encode(), live["code"].encode()):
            self.code = None
            return True
        live["fails"] += 1
        live["by_peer"][peer] = live["by_peer"].get(peer, 0) + 1
        if live["fails"] >= TOTAL_TRIES or live["by_peer"][peer] >= PEER_TRIES:
            self.code = None
        return False

    def add_device(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.devices.append({"id": secrets.token_hex(4), "name": name,
                             "hash": _hash(token), "created": now,
                             "seen": now})
        self._save()
        return token


def _refusal(request: web.Request, e: Refused) -> web.Response:
    headers = {GATE_HEADER: "refused"}
    if (e.status == 401 and request.method == "GET" and request.path == "/"
            and "text/html" in request.headers.get("Accept", "")):
        return web.Response(status=401, text=PAIR_PAGE,
                            content_type="text/html", headers=headers)
    # `ok: false` so a pairing block (`Group.pairing`) shows the reason.
    return web.json_response({"ok": False, "error": e.reason, "pair": "/"},
                             status=e.status, headers=headers)


@web.middleware
async def gate(request: web.Request, handler):
    """RA-1: the one gate, before every handler."""
    try:
        who, device = await request.app[GATE].decide(request)
    except Refused as e:
        return _refusal(request, e)
    request[CALLER] = who
    request[hub_mod.PAIRED] = device["id"] if device else None
    return await handler(request)


def _peer(request: web.Request) -> str:
    """Who is guessing: on PROXIED every peer is 127.0.0.1, so the login."""
    if hub_mod.listener_of(request) == hub_mod.PROXIED:
        return "ts:" + (request.headers.get("Tailscale-User-Login") or "")
    return request.remote or ""


def _lan_address() -> str | None:
    """This machine's LAN IPv4 (no packet is sent), or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") else ip


def pair_url(request: web.Request, settings) -> str | None:
    """Where a new device opens Butler, for the QR code (RA-8): the address
    this page was opened at when that is not this machine; else MAIN at the
    LAN address when `lan_access` is on; else none (the code is typed)."""
    host = request.headers.get("Host") or ""
    if hub_mod.listener_of(request) != hub_mod.LOCAL and \
            (URL(f"http://{host}").host or "") not in THIS_MACHINE:
        return f"https://{host}/"
    ports = request.app.get(hub_mod.LISTENERS) or {}
    main = [p for p, kind in ports.items() if kind == hub_mod.MAIN]
    ip = _lan_address()
    if settings is None or not settings.get("lan_access") or not main or \
            ip is None:
        return None
    return f"https://{ip}:{main[0]}/"


def _when(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def add_routes(app: web.Application, hub, records: Path) -> None:
    """`/api/devices`: the settings page's pairing block (`Group.pairing`,
    the document `phonelink` serves) and the owner's whole device API.
    GET lists, POST mints a code, DELETE `?device=<id>` removes one. Every
    method is owner-only (RA-11); `/api/pair` is the device's half."""
    g = Gate(hub, records)
    app[GATE] = g
    mint = {"id": "mint", "label": "Pair a device", "method": "POST"}

    def listing() -> dict:
        rows = [f"{d['name']}: paired {_when(d['created'])}, last used "
                f"{_when(d['seen'])}" for d in g.devices]
        n = len(rows)
        return {
            "ok": True, "status": "listed", "tone": "neutral", "poll": False,
            "headline": (f"{n} paired device" + ("" if n == 1 else "s"))
                        if n else "No paired devices",
            "detail": "\n".join(rows) or (
                "A device that is not this PC, and not signed in to your own "
                "Tailscale account, needs pairing once."),
            "qr": None,
            "devices": [{k: d[k] for k in ("id", "name", "created", "seen")}
                        for d in g.devices],
            "actions": [mint] + [
                {"id": f"remove-{d['id']}", "label": f"Remove {d['name']}",
                 "method": "DELETE", "query": f"device={d['id']}",
                 "confirm": f"{d['name']} will need pairing again."}
                for d in g.devices]}

    async def get_devices(_req):
        return web.json_response(listing())

    async def post_devices(req):
        code = g.mint()
        url = pair_url(req, hub.settings)
        where = f"open {url}" if url else "open Butler"
        doc = {**listing(), "status": "code", "tone": "success",
               "code": code, "headline": f"Pairing code {code}",
               "detail": f"On the new device, {where} and enter this code. "
                         f"It works once, for {int(CODE_TTL_S) // 60} "
                         f"minutes.",
               "actions": [{"id": "done", "label": "Done",
                            "method": "GET"}, mint]}
        if url:
            try:
                doc["qr"] = await asyncio.to_thread(
                    phonelink.qr_data_url, f"{url}#pair={code}")
            except phonelink.LinkError as e:
                doc["detail"] += f" (No QR code: {e})"
        return web.json_response(doc)

    async def delete_devices(req):
        did = req.query.get("device", "")
        if not any(d["id"] == did for d in g.devices):
            return web.json_response({"ok": False, "error": "no such device",
                                      "poll": False, "actions": [mint]},
                                     status=404)
        g.devices = [d for d in g.devices if d["id"] != did]
        g._save()
        for s in list(hub.sessions):
            if s.paired == did:
                await s.ws.close()
        return web.json_response(listing())

    async def post_pair(req):
        try:
            body = await req.json()
            code = str(body["code"])
            name = str(body.get("name") or "device").strip()[:60] or "device"
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": 'body must be {"code": ...}'},
                                     status=400)
        if not g.redeem(code, _peer(req)):
            return web.json_response(
                {"error": "that code is wrong or expired; ask for a new one "
                          "on the settings page"}, status=403)
        resp = web.json_response({"ok": True})
        resp.set_cookie(COOKIE, g.add_device(name), httponly=True,
                        secure=True, samesite="Strict", path="/")
        return resp

    app.router.add_get("/api/devices", get_devices)
    app.router.add_post("/api/devices", post_devices)
    app.router.add_delete("/api/devices", delete_devices)
    app.router.add_post("/api/pair", post_pair)


#: RA-10: what an unpaired browser sees at `/`. Self-contained: no asset,
#: so nothing else needs to be exempt. Reads `#pair=` (a fragment never
#: reaches a server or its log) and posts the code.
PAIR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair with Butler</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:22rem;margin:3rem auto;
padding:0 1rem;color:#222}
input,button{font:inherit;padding:.6rem;width:100%;box-sizing:border-box;
margin:.3rem 0}
#msg{min-height:1.5em}
</style></head><body>
<h1>Pair this device</h1>
<p>On the computer running Butler, open Settings, Devices, and choose
Pair a device. Enter the six-digit code it shows.</p>
<form id="f">
<input id="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6"
 placeholder="123456" autocomplete="one-time-code" required>
<input id="name" maxlength="60" placeholder="Name for this device">
<button>Pair</button>
<p id="msg"></p>
</form>
<script>
const f = document.getElementById("f");
const code = document.getElementById("code");
const msg = document.getElementById("msg");
const m = location.hash.match(/pair=(\\d{6})/);
if (m) { code.value = m[1]; history.replaceState(null, "", "/"); }
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "Pairing...";
  const r = await fetch("/api/pair", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({code: code.value,
                          name: document.getElementById("name").value})});
  if (r.ok) { location.replace("/"); return; }
  msg.textContent = (await r.json()).error;
});
</script>
</body></html>
"""
