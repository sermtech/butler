"""The one gate (docs/remote-access.md RA-1..RA-11, AC-720..AC-729, AC-731).

Every request reaches `access.gate` before any handler. These tests drive
the daemon's own `serve` over REAL sockets (`test_listeners._Three`): a
foreign peer is MAIN bound to this machine's LAN address, never a patched
`caller`. Route handlers are swapped for recorders, so a refusal is proven
by its consequence (the handler never ran) and a pass by the handler
running, without firing `/api/restart` or `/api/terminal` for real.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from multidict import CIMultiDict

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from daemon import access                                    # noqa: E402
from daemon import hub as hub_mod                            # noqa: E402
from daemon import phonelink                                 # noqa: E402
from test_listeners import _Three, _hub, _lan_address        # noqa: E402

GATE = access.GATE_HEADER
OWNER = "owner@example.com"
TS_HOST = "desk.tail1234.ts.net"
#: RA-5, spelled independently of `access.EXEMPT` so the source test means
#: something.
EXEMPT = {("POST", "/api/pair"), ("POST", "/api/hooks/{name}"),
          ("GET", "/manifest.json"), ("GET", "/icon.svg")}


def _lan_or_skip() -> str:
    ip = _lan_address()
    if ip is None:
        pytest.skip("this machine has no non-loopback IPv4 address")
    return ip


def _recorders(app: web.Application) -> list:
    """Swap every route's handler for one that records it ran. Static
    files keep their real handler (nothing to fire)."""
    hits: list = []
    for route in app.router.routes():
        if isinstance(route.resource, web.StaticResource):
            continue

        async def rec(_req, _key=(route.method, route.resource.canonical)):
            hits.append(_key)
            return web.Response(status=204)
        route._handler = rec
    return hits


def _routes(app: web.Application) -> list[tuple[str, str]]:
    """(method, canonical) for every routed handler, HEAD included."""
    return sorted({(r.method, r.resource.canonical)
                   for r in app.router.routes()
                   if not isinstance(r.resource, web.StaticResource)})


def _concrete(canonical: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", canonical)


def _as_served(kind, headers) -> dict:
    """`tailscale serve` keeps the phone's ts.net Host on PROXIED (RA-3)."""
    if kind == hub_mod.PROXIED:
        return {"Host": TS_HOST, **(headers or {})}
    return headers or {}


async def _send(three, kind, method, path, headers=None, body=None):
    async with three.http.request(
            method, three.url(kind, path), headers=_as_served(kind, headers),
            data=None if body is None else json.dumps(body),
            allow_redirects=False) as r:
        text = await r.text()
        return r.status, r.headers, text


def _owners(hub, value: str) -> None:
    hub.settings.update({"tailscale_owners": value})


# --------------------------------------------------------------------------- #
# AC-720 / AC-721: one gate over every route
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_every_route_is_refused_to_a_lan_peer_with_no_device(
        tmp_path, monkeypatch):
    """AC-720: a non-loopback peer on MAIN with no cookie reaches no handler
    except the RA-5 list, and no static file. The list is `app.router`."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        hits = _recorders(three.app)
        routes = _routes(three.app)
        assert ("POST", "/api/mode") in routes and ("GET", "/ws") in routes
        for method, canonical in routes:
            status, headers, _ = await _send(three, hub_mod.MAIN, method,
                                             _concrete(canonical))
            if (method, canonical) in EXEMPT:
                assert headers.get(GATE) is None, (method, canonical)
            else:
                assert headers.get(GATE) == "refused", (method, canonical)
                assert status in (401, 403), (method, canonical)
        assert set(hits) <= EXEMPT, sorted(set(hits) - EXEMPT)
        for path in ("/app.js", "/settings.js", "/"):
            status, headers, _ = await _send(three, hub_mod.MAIN, "GET", path)
            assert headers.get(GATE) == "refused", path
        for path in ("/manifest.json", "/icon.svg"):
            status, headers, _ = await _send(three, hub_mod.MAIN, "GET", path)
            assert status == 200 and headers.get(GATE) is None, path


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (hub_mod.MAIN, hub_mod.LOCAL))
async def test_every_route_reaches_its_handler_from_this_machine(
        tmp_path, monkeypatch, kind):
    """AC-721: on LOCAL, and on MAIN from 127.0.0.1, no request carries the
    gate header and every handler runs."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        routes = _routes(three.app)
        for method, canonical in routes:
            status, headers, _ = await _send(three, kind, method,
                                             _concrete(canonical))
            assert headers.get(GATE) is None, (method, canonical, status)
        assert set(hits) == set(routes)
        status, headers, _ = await _send(three, kind, "GET", "/app.js")
        assert status == 200 and headers.get(GATE) is None


@pytest.mark.asyncio
async def test_a_lan_socket_with_no_origin_is_refused_before_the_hub(
        tmp_path, monkeypatch):
    """AC-722: the exploit chain's second half. No Origin used to pass."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        with pytest.raises(aiohttp.WSServerHandshakeError) as e:
            await three.http.ws_connect(three.url(hub_mod.MAIN, "/ws"))
        assert e.value.status == 401
        assert e.value.headers.get(GATE) == "refused"
        assert len(hub.sessions) == 0


@pytest.mark.asyncio
async def test_a_lan_neighbour_cannot_raise_the_mode(tmp_path, monkeypatch):
    """The Gherkin journey: the real `/api/mode` handler, its consequence."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    before = hub.modes.mode
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        status, headers, _ = await _send(three, hub_mod.MAIN, "POST",
                                         "/api/mode", body={"mode": "trusted"})
        assert status == 401 and headers.get(GATE) == "refused"
    assert hub.modes.mode == before


# --------------------------------------------------------------------------- #
# AC-723: same-origin on every request
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (hub_mod.MAIN, hub_mod.LOCAL,
                                  hub_mod.PROXIED))
async def test_a_cross_site_request_is_refused_on_every_listener(
        tmp_path, monkeypatch, kind):
    """AC-723: a foreign Origin on a POST, and a cross-site or same-site
    GET (a no-cors fetch from another tab, or another service on the same
    host), never reach a handler -- on PROXIED even with the owner's login."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, OWNER)
    who = {"Tailscale-User-Login": OWNER} if kind == hub_mod.PROXIED else {}
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        for method, path, headers in (
                ("POST", "/api/mode", {"Origin": "https://evil.example"}),
                ("GET", "/api/chats", {"Sec-Fetch-Site": "cross-site"}),
                ("GET", "/api/chats", {"Sec-Fetch-Site": "same-site"})):
            status, got, _ = await _send(three, kind, method, path,
                                         {**who, **headers})
            assert got.get(GATE) == "refused", (path, headers)
        assert hits == []
        # Same-origin fetch metadata passes (the control).
        status, got, _ = await _send(three, kind, "GET", "/api/chats",
                                     {**who, "Sec-Fetch-Site": "same-origin"})
        assert got.get(GATE) is None and hits == [("GET", "/api/chats")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (hub_mod.MAIN, hub_mod.LOCAL))
async def test_a_rebinding_host_is_refused_on_plain_http(tmp_path, monkeypatch,
                                                         kind):
    """AC-723 (RA-3): on LOCAL and plain-HTTP MAIN, a hostname in Host is
    what a DNS-rebinding page looks like; it never reaches a handler."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        port = three.socks[kind].getsockname()[1]
        status, got, _ = await _send(three, kind, "GET", "/api/chats",
                                     {"Host": f"evil.example:{port}"})
        assert got.get(GATE) == "refused" and hits == []
        status, got, _ = await _send(three, kind, "GET", "/api/chats",
                                     {"Host": f"localhost:{port}"})
        assert got.get(GATE) is None and hits == [("GET", "/api/chats")]


@pytest.mark.asyncio
async def test_the_owners_phone_through_tailscale_serve_keeps_its_host(
        tmp_path, monkeypatch):
    """AC-723 / AC-725: `tailscale serve` keeps the phone's ts.net Host on
    plain-HTTP PROXIED; same-origin by Host, plus the owner's login, is
    accepted -- on HTTP and on the socket."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, OWNER)
    phone = {"Host": TS_HOST, "Origin": f"https://{TS_HOST}",
             "Tailscale-User-Login": OWNER}
    async with _Three(hub, monkeypatch) as three:
        status, got, _ = await _send(three, hub_mod.PROXIED, "GET",
                                     "/api/settings", phone)
        assert status == 200 and got.get(GATE) is None
        ws = await three.http.ws_connect(three.url(hub_mod.PROXIED, "/ws"),
                                         headers=phone)
        await ws.send_json({"type": "hello", "proto": 1, "device_id": "p",
                            "caps": {"wake": False, "tts": False}})
        await ws.receive_json()
        assert len(hub.sessions) == 1
        await ws.close()


# --------------------------------------------------------------------------- #
# AC-724: no proxy in front of a loopback-trusted listener
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (hub_mod.MAIN, hub_mod.LOCAL))
@pytest.mark.parametrize("header", ("Tailscale-User-Login", "X-Forwarded-For",
                                    "Forwarded"))
async def test_a_proxied_request_on_a_loopback_listener_is_refused(
        tmp_path, monkeypatch, kind, header):
    """AC-724: a proxy aimed at 8140/8141 would make all its clients the
    owner (its peer is loopback). Its headers give it away."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        status, got, _ = await _send(three, kind, "GET", "/api/chats",
                                     {header: "for=10.0.0.9"})
        assert got.get(GATE) == "refused" and hits == []


# --------------------------------------------------------------------------- #
# AC-725 / AC-726: the Tailscale owner list
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_only_a_listed_login_is_the_owner_on_proxied(tmp_path,
                                                           monkeypatch):
    """AC-725: a listed login (any case) is the owner; a shared-in friend
    and a tagged device (no login header) are refused without a cookie."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, f" Someone@Else.com , {OWNER.upper()} ")
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        for login, gated in (("Owner@Example.com", False),
                             ("friend@example.com", True),
                             (None, True)):
            who = {} if login is None else {"Tailscale-User-Login": login}
            status, got, _ = await _send(three, hub_mod.PROXIED, "GET",
                                         "/api/chats", who)
            assert (got.get(GATE) == "refused") is gated, login
        assert hits == [("GET", "/api/chats")]


def _fake_tailscale(monkeypatch, status: dict | None):
    """Stand in for the `tailscale` binary at its one door (phonelink)."""
    calls = []
    monkeypatch.setattr(phonelink, "tailscale_path", lambda: "tailscale")

    def run(_exe, *args, timeout):
        calls.append(args)
        if status is None:
            return 1, "failed to connect to local tailscaled"
        return 0, json.dumps(status)
    monkeypatch.setattr(phonelink, "_tailscale", run)
    return calls


SELF = {"BackendState": "Running",
        "Self": {"UserID": 7, "DNSName": TS_HOST + "."},
        "User": {"7": {"LoginName": "me@example.com"}}}


@pytest.mark.asyncio
async def test_a_blank_owner_list_means_this_machines_own_login(
        tmp_path, monkeypatch):
    """AC-726: blank resolves to `tailscale status --json`'s self login,
    read once per minute, not once per request."""
    calls = _fake_tailscale(monkeypatch, SELF)
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        for login, gated in (("me@example.com", False), (OWNER, True),
                             ("me@example.com", False)):
            status, got, _ = await _send(three, hub_mod.PROXIED, "GET",
                                         "/api/chats",
                                         {"Tailscale-User-Login": login})
            assert (got.get(GATE) == "refused") is gated, login
        assert len(hits) == 2
        assert len(calls) == 1, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (
    {**SELF, "Self": {**SELF["Self"], "Tags": ["tag:server"]}},
    None), ids=("tagged", "unreadable"))
async def test_no_owner_login_means_nobody_never_everybody(
        tmp_path, monkeypatch, status):
    """AC-726: a tagged machine or an unreadable status resolves to nobody:
    503 naming the setting. Never allow-all."""
    _fake_tailscale(monkeypatch, status)
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        code, got, text = await _send(three, hub_mod.PROXIED, "GET",
                                      "/api/chats",
                                      {"Tailscale-User-Login": "me@example.com"})
        assert code == 503 and got.get(GATE) == "refused"
        assert "tailscale_owners" in text
        # An exempt route is unaffected (it needs no owner list).
        code, got, _ = await _send(three, hub_mod.PROXIED, "POST",
                                   "/api/hooks/x")
        assert got.get(GATE) is None
        assert hits == [("POST", "/api/hooks/{name}")]


# --------------------------------------------------------------------------- #
# AC-727..AC-729: pairing
# --------------------------------------------------------------------------- #

async def _mint(three) -> str:
    """The owner asks for a code from this machine (LOCAL)."""
    status, _, text = await _send(three, hub_mod.LOCAL, "POST",
                                  "/api/devices")
    assert status == 200, text
    code = json.loads(text)["code"]
    assert re.fullmatch(r"\d{6}", code)
    return code


async def _pair(three, kind, code, name="phone", headers=None):
    async with three.http.post(three.url(kind, "/api/pair"),
                               headers=_as_served(kind, headers),
                               json={"code": code, "name": name}) as r:
        return r.status, r.headers.getall("Set-Cookie", []), await r.text()


def _cookie(set_cookies: list[str]) -> str:
    [line] = [c for c in set_cookies if c.startswith("butler_device=")]
    return line.split(";", 1)[0]


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


@pytest.mark.asyncio
async def test_a_code_from_the_owner_pairs_a_lan_phone_once(tmp_path,
                                                            monkeypatch):
    """AC-727 / AC-728: the code pairs once; the cookie is HttpOnly,
    Secure, SameSite=Strict; the device reaches handlers with it; the
    records hold its hash, never the token."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        code = await _mint(three)
        status, cookies, text = await _pair(three, hub_mod.MAIN, code)
        assert status == 200, text
        [line] = [c for c in cookies if c.startswith("butler_device=")]
        attrs = {a.strip().lower() for a in line.split(";")[1:]}
        assert {"httponly", "secure", "samesite=strict", "path=/"} <= attrs
        cookie = _cookie(cookies)
        token = cookie.split("=", 1)[1]
        assert len(token) >= 43
        status, _, _ = await _pair(three, hub_mod.MAIN, code, "second")
        assert status in (401, 403)
        hits = _recorders(three.app)
        status, got, _ = await _send(three, hub_mod.MAIN, "GET", "/api/chats",
                                     {"Cookie": cookie})
        assert got.get(GATE) is None and hits == [("GET", "/api/chats")]
    raw = (tmp_path / "devices.json").read_text(encoding="utf-8")
    assert token not in raw
    assert hashlib.sha256(token.encode()).hexdigest() in raw
    assert json.loads(raw)["devices"][0]["name"] == "phone"


@pytest.mark.asyncio
async def test_a_code_expires_after_five_minutes(tmp_path, monkeypatch):
    """AC-727: the clock is injected; 5 minutes is the shipped value."""
    assert access.CODE_TTL_S == 300
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        gate = three.app[access.GATE]
        now = [1000.0]
        gate.clock = lambda: now[0]
        code = await _mint(three)
        now[0] += access.CODE_TTL_S + 1
        status, cookies, _ = await _pair(three, hub_mod.MAIN, code)
        assert status in (401, 403) and not cookies


@pytest.mark.asyncio
async def test_five_wrong_guesses_from_one_peer_burn_the_code(tmp_path,
                                                              monkeypatch):
    """AC-727: after 5 wrong guesses the right code no longer pairs."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        code = await _mint(three)
        for _ in range(4):
            status, _, _ = await _pair(three, hub_mod.MAIN, _wrong(code))
            assert status in (401, 403)
        # Four wrong: still pairs (the control).
        status, _, _ = await _pair(three, hub_mod.MAIN, code)
        assert status == 200
        code = await _mint(three)
        for _ in range(5):
            await _pair(three, hub_mod.MAIN, _wrong(code))
        status, cookies, _ = await _pair(three, hub_mod.MAIN, code)
        assert status in (401, 403) and not cookies


@pytest.mark.asyncio
async def test_twenty_wrong_guesses_in_total_burn_the_code(tmp_path,
                                                           monkeypatch):
    """AC-727: many peers, each under 5 (PROXIED callers keyed by login)."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, OWNER)
    async with _Three(hub, monkeypatch) as three:
        code = await _mint(three)
        for i in range(20):
            await _pair(three, hub_mod.PROXIED, _wrong(code),
                        headers={"Tailscale-User-Login": f"p{i}@example.com"})
        status, cookies, _ = await _pair(
            three, hub_mod.PROXIED, code,
            headers={"Tailscale-User-Login": "fresh@example.com"})
        assert status in (401, 403) and not cookies


@pytest.mark.asyncio
async def test_only_the_owner_mints_codes_and_manages_devices(tmp_path,
                                                              monkeypatch):
    """AC-727 / AC-729 (RA-11): a stranger is refused; a paired DEVICE is
    refused the owner-only actions, so a stolen cookie cannot entrench."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        status, got, _ = await _send(three, hub_mod.MAIN, "POST",
                                     "/api/devices")
        assert status == 401 and got.get(GATE) == "refused"
        code = await _mint(three)
        _, cookies, _ = await _pair(three, hub_mod.MAIN, code)
        device = {"Cookie": _cookie(cookies)}
        before = hub.settings.get("tailscale_owners")
        for method, path, body in (
                ("POST", "/api/devices", None),
                ("GET", "/api/devices", None),
                ("DELETE", "/api/devices?device=x", None),
                ("POST", "/api/settings/reset", None),
                ("POST", "/api/settings",
                 {"tailscale_owners": "thief@example.com"}),
                ("POST", "/api/settings", {"terminals_enabled": True})):
            status, got, _ = await _send(three, hub_mod.MAIN, method, path,
                                         device, body)
            assert status == 403 and got.get(GATE) == "refused", path
        assert hub.settings.get("tailscale_owners") == before
        # An ordinary setting is still the device's to change (the control).
        status, got, _ = await _send(three, hub_mod.MAIN, "POST",
                                     "/api/settings", device,
                                     {"phone_link_port": 8444})
        assert status == 200 and hub.settings.get("phone_link_port") == 8444


@pytest.mark.asyncio
async def test_removing_a_device_refuses_it_and_closes_its_socket(
        tmp_path, monkeypatch):
    """AC-729: the owner removes a device; its next request is refused and
    its open socket closes within a second."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        code = await _mint(three)
        _, cookies, _ = await _pair(three, hub_mod.MAIN, code, "tablet")
        device = {"Cookie": _cookie(cookies)}
        ws = await three.http.ws_connect(three.url(hub_mod.MAIN, "/ws"),
                                         headers=device)
        await ws.send_json({"type": "hello", "proto": 1, "device_id": "t",
                            "caps": {"wake": False, "tts": False}})
        await ws.receive_json()
        status, _, text = await _send(three, hub_mod.LOCAL, "GET",
                                      "/api/devices")
        [row] = json.loads(text)["devices"]
        assert row["name"] == "tablet" and "hash" not in row
        status, _, _ = await _send(three, hub_mod.LOCAL, "DELETE",
                                   f"/api/devices?device={row['id']}")
        assert status == 200

        async def closed():
            while not ws.closed:
                msg = await ws.receive()
                if msg.type in (aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING):
                    return
        await asyncio.wait_for(closed(), 1.0)
        status, got, _ = await _send(three, hub_mod.MAIN, "GET",
                                     "/api/chats", device)
        assert status == 401 and got.get(GATE) == "refused"


@pytest.mark.asyncio
async def test_an_unpaired_browser_gets_the_pair_page(tmp_path, monkeypatch):
    """RA-10: a refused `GET /` for HTML is the self-contained pair page
    (no other asset), and an API refusal names where to pair."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        status, got, text = await _send(three, hub_mod.MAIN, "GET", "/",
                                        {"Accept": "text/html"})
        assert status == 401 and got.get(GATE) == "refused"
        assert got["Content-Type"].startswith("text/html")
        assert "/api/pair" in text
        assert not re.search(r"""(src|href)\s*=\s*["'](?!#)""", text)
        status, got, text = await _send(three, hub_mod.MAIN, "GET",
                                        "/api/chats")
        assert status == 401 and json.loads(text)["pair"] == "/"


# --------------------------------------------------------------------------- #
# AC-731: the exempt list
# --------------------------------------------------------------------------- #

def test_the_exempt_list_is_exactly_ra5():
    """AC-731: a route joins the exempt list only through the spec."""
    assert set(access.EXEMPT) == EXEMPT


@pytest.mark.asyncio
async def test_a_hook_still_needs_its_token(tmp_path, monkeypatch):
    """AC-731: exempt from the gate is not exempt from the hook's own
    token (`triggers.authorised`, AC-498): the REAL handler refuses."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        status, got, _ = await _send(three, hub_mod.MAIN, "POST",
                                     "/api/hooks/nothing", body={})
        assert got.get(GATE) is None and status >= 400


# --------------------------------------------------------------------------- #
# Attack review 2026-10-29 (docs/remote-access.md section 8)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_rebinding_page_cannot_reach_proxied(tmp_path, monkeypatch):
    """H1: PROXIED is plain HTTP on 127.0.0.1 and trusts the login header.
    A page rebound to 127.0.0.1 is same-origin with its OWN name and can set
    that header, so PROXIED accepts only a `*.ts.net` Host: a name whose DNS
    only Tailscale answers, so no page can rebind it."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, OWNER)
    async with _Three(hub, monkeypatch) as three:
        hits = _recorders(three.app)
        port = three.socks[hub_mod.PROXIED].getsockname()[1]
        for host in (f"evil.com:{port}", f"127.0.0.1:{port}",
                     "ts.net.evil.com", "evilts.net"):
            status, got, _ = await _send(
                three, hub_mod.PROXIED, "GET", "/api/devices",
                {"Host": host, "Sec-Fetch-Site": "same-origin",
                 "Tailscale-User-Login": OWNER})
            assert got.get(GATE) == "refused", host
        assert hits == []
        status, got, _ = await _send(three, hub_mod.PROXIED, "GET",
                                     "/api/devices",
                                     {"Host": TS_HOST,
                                      "Tailscale-User-Login": OWNER})
        assert got.get(GATE) is None and hits == [("GET", "/api/devices")]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ("X-Real-IP", "True-Client-IP"))
async def test_other_proxy_headers_are_refused_too(tmp_path, monkeypatch,
                                                   header):
    """Low (attack review): a proxy that adds only one of these."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        for kind in (hub_mod.MAIN, hub_mod.LOCAL):
            status, got, _ = await _send(three, kind, "GET", "/api/chats",
                                         {header: "10.0.0.9"})
            assert got.get(GATE) == "refused", kind


@pytest.mark.asyncio
async def test_a_host_with_userinfo_is_refused(tmp_path, monkeypatch):
    """Low (attack review): `evil.com:80@127.0.0.1` parsed as 127.0.0.1."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        port = three.socks[hub_mod.LOCAL].getsockname()[1]
        status, got, _ = await _send(three, hub_mod.LOCAL, "GET",
                                     "/api/chats",
                                     {"Host": f"evil.com:80@127.0.0.1:{port}"})
        assert got.get(GATE) == "refused"


@pytest.mark.asyncio
async def test_two_login_headers_are_refused(tmp_path, monkeypatch):
    """Low (attack review): serve strips a client's copy; two copies mean
    something in between did not, and the first must not win."""
    hub = _hub(tmp_path, monkeypatch)
    _owners(hub, OWNER)
    async with _Three(hub, monkeypatch) as three:
        headers = CIMultiDict(
            [("Host", TS_HOST), ("Tailscale-User-Login", OWNER),
             ("Tailscale-User-Login", "other@example.com")])
        async with three.http.get(three.url(hub_mod.PROXIED, "/api/chats"),
                                  headers=headers) as r:
            assert r.headers.get(GATE) == "refused"


@pytest.mark.asyncio
async def test_a_list_body_on_settings_is_a_400_not_a_crash(tmp_path,
                                                           monkeypatch):
    """Low (attack review): `post_settings` assumed a dict."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, _, text = await _send(three, hub_mod.LOCAL, "POST",
                                      "/api/settings", body=["lan_access"])
        assert status == 400, text


# --------------------------------------------------------------------------- #
# H2 (owner, 2026-10-29: "owner"): what outlives a device is the owner's.
# --------------------------------------------------------------------------- #

#: Routes that create a STANDING channel -- something that keeps working
#: after the device that made it is removed. Spelled here, independently of
#: `access.OWNER_ONLY`.
STANDING = {("POST", "/api/triggers"), ("DELETE", "/api/triggers/{name}"),
            ("POST", "/api/notify"), ("DELETE", "/api/notify/{name}"),
            ("POST", "/api/connections"),
            ("DELETE", "/api/connections/{cid}"),
            ("POST", "/api/automations"), ("POST", "/api/automations/bulk"),
            ("POST", "/api/whatsapp/pairing"),
            ("DELETE", "/api/whatsapp/pairing"),
            # docs/project-roots.md AC-750 (PJ-1)
            ("POST", "/api/shortcuts"), ("DELETE", "/api/shortcuts/{sid}")}


@pytest.mark.asyncio
async def test_a_device_cannot_create_a_standing_channel(tmp_path,
                                                         monkeypatch):
    """H2: a device made a webhook trigger, was removed, and the hook's
    token still worked. Every route that makes something that outlives the
    device is owner-only; the owner (this machine) still reaches them."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        routes = set(_routes(three.app))
        assert STANDING <= routes, sorted(STANDING - routes)
        assert STANDING <= access.OWNER_ONLY
        device = await three.who(hub_mod.MAIN)
        hits = _recorders(three.app)
        for method, canonical in sorted(STANDING):
            status, got, _ = await _send(three, hub_mod.MAIN, method,
                                         _concrete(canonical), device)
            assert status == 403 and got.get(GATE) == "refused", canonical
        assert hits == []
        for method, canonical in sorted(STANDING):
            await _send(three, hub_mod.LOCAL, method, _concrete(canonical))
        assert set(hits) == STANDING


# --------------------------------------------------------------------------- #
# docs/chats-and-terminals.md CT-3, AC-761: terminals are the owner's
# --------------------------------------------------------------------------- #

TERMINAL_ROUTES = {("GET", "/api/terminals"), ("POST", "/api/terminals"),
                   ("DELETE", "/api/terminals/{tid}"),
                   ("GET", "/ws/terminal/{tid}")}


@pytest.mark.asyncio
async def test_a_paired_device_cannot_reach_a_terminal(tmp_path, monkeypatch):
    """AC-761: every terminal route and the terminal socket are refused to a
    paired device before any handler runs (so nothing can spawn); the owner
    on this machine reaches each one."""
    ip = _lan_or_skip()
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        routes = set(_routes(three.app))
        assert TERMINAL_ROUTES <= routes, sorted(TERMINAL_ROUTES - routes)
        device = await three.who(hub_mod.MAIN)
        hits = _recorders(three.app)
        for method, canonical in sorted(TERMINAL_ROUTES):
            status, got, _ = await _send(three, hub_mod.MAIN, method,
                                         _concrete(canonical), device)
            assert status == 403 and got.get(GATE) == "refused", canonical
        assert hits == []
        for method, canonical in sorted(TERMINAL_ROUTES):
            await _send(three, hub_mod.LOCAL, method, _concrete(canonical))
        assert set(hits) == TERMINAL_ROUTES
