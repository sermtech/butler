"""RV-25 (docs/messaging.md 6.5, AC-612): the page socket refuses cross-site
pages.

`/ws` cannot carry `X-Butler-Local` (a browser sets no header on a
WebSocket), and a socket on LOCAL is the desk: a web page in the owner's
browser that opened it could type turns into an agent with tools. So the
handshake checks `Origin` before the upgrade. NOT recoverable (a turn run
with tools), hence REAL sockets on all three listeners, ports from the OS
(never 8140-8142), and the refusal proven by its consequence: no session.
"""
from __future__ import annotations

import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from daemon import butlerd                                   # noqa: E402
from daemon import hub as hub_mod                            # noqa: E402
from test_listeners import _Three, _hub                      # noqa: E402

KINDS = (hub_mod.MAIN, hub_mod.LOCAL, hub_mod.PROXIED)
EVIL = "https://evil.example"


def _port(three, kind) -> int:
    return three.socks[kind].getsockname()[1]


async def _handshake(three, hub, kind, headers, scheme="http") -> int:
    """Open /ws on `kind` with `headers`; answer the handshake status (101
    when the upgrade happened). An accepted socket must become a session
    and a refused one must not: the consequence, not only the status."""
    before = len(hub.sessions)
    url = f"{scheme}://127.0.0.1:{_port(three, kind)}/ws"
    try:
        # Past the gate as the owner's phone or a paired device
        # (docs/remote-access.md RA-2): Origin is what is under test.
        ws = await three.http.ws_connect(
            url, headers={**await three.who(kind), **headers}, ssl=False)
    except aiohttp.WSServerHandshakeError as e:
        assert len(hub.sessions) == before, "a refused handshake left a session"
        return e.status
    await ws.send_json({"type": "hello", "proto": 1, "device_id": "p",
                        "caps": {"wake": False, "tts": False}})
    await ws.receive_json()
    assert len(hub.sessions) == before + 1
    await ws.close()
    return 101


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_evil_origin_is_refused_on_every_listener(
        tmp_path, monkeypatch, kind):
    """AC-612: Origin https://evil.example is refused 403, with the Host the
    browser really sends (the listener's own address) and with a Host the
    page names itself (a DNS-rebinding page's Host is its own name)."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        port = _port(three, kind)
        assert await _handshake(three, hub, kind, {"Origin": EVIL}) == 403
        assert await _handshake(three, hub, kind, {
            "Origin": f"http://evil.example:{port}",
            "Host": f"evil.example:{port}"}) == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_the_pages_own_loopback_origin_is_accepted_on_every_listener(
        tmp_path, monkeypatch, kind):
    """The desk page (http://127.0.0.1:8141, the Tauri window) and a
    loopback page on MAIN: the origin is one of `own_origins`."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        port = _port(three, kind)
        for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            assert await _handshake(three, hub, kind,
                                    {"Origin": origin}) == 101, origin


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_client_with_no_origin_is_accepted(tmp_path, monkeypatch,
                                                   kind):
    """A browser always sends Origin on a WebSocket; none means a
    non-browser client (tests, tools), which keeps today's behaviour."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        assert await _handshake(three, hub, kind, {}) == 101


@pytest.mark.asyncio
async def test_the_phone_through_tailscale_serve_is_accepted_on_proxied(
        tmp_path, monkeypatch):
    """`tailscale serve` terminates TLS and keeps the phone's Host (its
    reverse proxy sets Out.Host = In.Host), so PROXIED sees an https page
    whose Origin equals its Host, at 443 or at a phone-link port. No name
    is hardcoded: the rule is same-origin by Host."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        for host in ("desk.tail1234.ts.net", "desk.tail1234.ts.net:8443"):
            assert await _handshake(three, hub, hub_mod.PROXIED, {
                "Origin": f"https://{host}", "Host": host}) == 101, host
        # Same host, wrong scheme or a different host: refused.
        for origin, host in (
                ("http://desk.tail1234.ts.net", "desk.tail1234.ts.net"),
                ("https://other.tail1234.ts.net", "desk.tail1234.ts.net")):
            assert await _handshake(three, hub, hub_mod.PROXIED, {
                "Origin": origin, "Host": host}) == 403, origin


@pytest.mark.asyncio
async def test_local_accepts_only_loopback_origins_even_when_host_matches(
        tmp_path, monkeypatch):
    """LOCAL is the desk: a page named by Host there is a DNS-rebinding
    page, however exactly its Origin matches."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        assert await _handshake(three, hub, hub_mod.LOCAL, {
            "Origin": "https://desk.tail1234.ts.net",
            "Host": "desk.tail1234.ts.net"}) == 403
        assert await _handshake(three, hub, hub_mod.LOCAL, {
            "Origin": "http://10.0.0.8:8141", "Host": "10.0.0.8:8141"}) == 403


@pytest.mark.asyncio
async def test_plain_http_main_accepts_a_lan_ip_page_by_host(tmp_path,
                                                             monkeypatch):
    """A `--no-tls` MAIN: a page at a LAN IP literal is same-origin by Host;
    a hostname over plain http is refused (rebinding needs a name)."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        assert await _handshake(three, hub, hub_mod.MAIN, {
            "Origin": "http://10.0.0.8:8140", "Host": "10.0.0.8:8140"}) == 101
        assert await _handshake(three, hub, hub_mod.MAIN, {
            "Origin": "http://desk.local:8140",
            "Host": "desk.local:8140"}) == 403


def _cert(tmp_path) -> ssl.SSLContext:
    """A throwaway self-signed cert made with openssl (on PATH or Git's)."""
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=butler-test"],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


@pytest.mark.asyncio
async def test_a_phone_on_tls_main_is_accepted_by_host(tmp_path, monkeypatch):
    """The phone on 8140 (https://<lan-ip>:8140, https://<name>.local:8140):
    MAIN with TLS, the Origin equals https + Host. Over TLS a name is fine,
    because a rebinding page cannot present Butler's certificate."""
    hub = _hub(tmp_path, monkeypatch)
    ctx = _cert(tmp_path)
    real_serve = butlerd.serve
    monkeypatch.setattr(butlerd, "serve",
                        lambda app, _ctx, socks: real_serve(app, ctx, socks))
    async with _Three(hub, monkeypatch) as three:
        port = _port(three, hub_mod.MAIN)
        for host in (f"10.0.0.8:{port}", f"desk.local:{port}"):
            assert await _handshake(three, hub, hub_mod.MAIN, {
                "Origin": f"https://{host}", "Host": host},
                scheme="https") == 101, host
        assert await _handshake(three, hub, hub_mod.MAIN, {
            "Origin": f"http://desk.local:{port}",
            "Host": f"desk.local:{port}"}, scheme="https") == 403
        assert await _handshake(three, hub, hub_mod.MAIN, {"Origin": EVIL},
                                scheme="https") == 403
