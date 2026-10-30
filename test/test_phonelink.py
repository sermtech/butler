"""Phone link: the ts.net address as a QR code and the one set-up button.

docs/desktop-app.md section 5 (DA-15, DA-16): AC-553 (the address as a QR
code with three steps, served as the generic pairing document the settings
page renders) and AC-554 (set-up checks Tailscale, runs the port-forward
`serve`, shows Tailscale's error VERBATIM, and never exposes Butler to the
public internet).

Tailscale here is a FAKE: a `.cmd` wrapper over a Python script, first and
only on PATH beside node, driven by a scenario file and logging every argv.
The real CLI is never reachable from these tests (the fixture asserts it),
because a real `serve` changes the owner's tailnet. Listeners are real
sockets on 127.0.0.1 port 0 through `test_listeners._Three`.
"""
from __future__ import annotations

import ast
import base64
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from daemon import hub as hub_mod                            # noqa: E402
from daemon import phonelink                                 # noqa: E402
from daemon.settings import Settings                         # noqa: E402
from test_listeners import _Three, _hub, _lan_address        # noqa: E402

NAME = "desk.tail1234.ts.net"
CERT_ERROR = ("500 Internal Server Error: Serve requires HTTPS certificates."
              " Example tailnet text 42% \"quoted\" <b>not html</b>")

FAKE = r'''
import json, os, sys
scen = json.load(open(os.environ["FAKE_TS"], encoding="utf-8"))
log = os.environ["FAKE_TS"] + ".calls"
args = sys.argv[1:]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
if any("funnel" in a.lower() for a in args):
    sys.exit(99)
if args[:2] == ["status", "--json"]:
    print(json.dumps(scen["status"]))
elif args[:3] == ["serve", "status", "--json"]:
    print(json.dumps(scen.get("serve_status", {})))
elif args[:2] == ["serve", "--bg"]:
    if scen.get("serve_error"):
        sys.stderr.write(scen["serve_error"])
        sys.exit(1)
    port = args[2].split("=", 1)[1]
    web = scen.setdefault("serve_status", {}).setdefault("Web", {})
    web[scen["status"]["Self"]["DNSName"].rstrip(".") + ":" + port] = {
        "Handlers": {"/": {"Proxy": args[3]}}}
    json.dump(scen, open(os.environ["FAKE_TS"], "w", encoding="utf-8"))
else:
    sys.stderr.write("fake tailscale: unknown command %r" % args)
    sys.exit(2)
'''


def _status(backend="Running", certs=True):
    return {"BackendState": backend,
            "Self": {"DNSName": NAME + ".", "HostName": "DESK"},
            "CertDomains": [NAME] if certs else None}


class FakeTailscale:
    def __init__(self, root: Path):
        self.bin = root / "bin"
        self.bin.mkdir()
        self.scen = root / "scenario.json"
        script = root / "fake_tailscale.py"
        script.write_text(FAKE, encoding="utf-8")
        (self.bin / "tailscale.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")

    def set(self, **scen):
        self.scen.write_text(json.dumps(scen), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = Path(str(self.scen) + ".calls")
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text("utf-8").splitlines()]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """The fake first on a PATH that holds only it and node, so the real
    Tailscale CLI cannot run whatever the code under test does."""
    node = shutil.which("node")
    assert node, "node is needed for the QR code (the qrcode package)"
    ts = FakeTailscale(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([str(ts.bin),
                                                str(Path(node).parent)]))
    monkeypatch.setenv("FAKE_TS", str(ts.scen))
    found = phonelink.tailscale_path()
    assert found and Path(found).parent == ts.bin, (
        f"the fake is not the Tailscale this test would run: {found}")
    return ts


def _serving(proxy_url: str) -> dict:
    return {"Web": {f"{NAME}:443": {"Handlers": {"/": {"Proxy": proxy_url}}}}}


async def _get(three, kind):
    async with three.http.get(three.url(kind, "/api/phonelink"),
                              headers=await three.who(kind)) as r:
        return r.status, await r.json()


async def _post(three, kind):
    async with three.http.post(three.url(kind, "/api/phonelink"),
                               headers=await three.who(kind)) as r:
        return r.status, await r.json()


def _proxied(three) -> int:
    return three.socks[hub_mod.PROXIED].getsockname()[1]


def _serve_calls(ts):
    return [c for c in ts.calls() if c[:2] == ["serve", "--bg"]]


# --------------------------------------------------------------------------- #
# The settings group (AC-41, AC-553)
# --------------------------------------------------------------------------- #
def test_the_devices_group_declares_the_phone_link_pairing(tmp_path):
    groups = {g["key"]: g for g in
              Settings(tmp_path / "s.json").describe()["groups"]}
    assert groups["devices"]["pairing"] == "/api/phonelink"
    assert groups["devices"]["stage"] is None


# --------------------------------------------------------------------------- #
# GET: the states (AC-553, AC-554)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tailscale_missing_says_install_it(tmp_path, monkeypatch):
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert phonelink.tailscale_path() is None
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _get(three, hub_mod.LOCAL)
    assert status == 200 and doc["ok"] is True
    assert doc["status"] == "missing" and doc["actions"] == []
    assert "tailscale.com/download" in doc["detail"]


@pytest.mark.asyncio
async def test_logged_out_says_sign_in_and_runs_no_serve(tmp_path,
                                                         monkeypatch, fake):
    fake.set(status=_status(backend="NeedsLogin"))
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _get(three, hub_mod.LOCAL)
        pstatus, _ = await _post(three, hub_mod.LOCAL)
    assert status == 200 and doc["status"] == "logged_out"
    assert "NeedsLogin" in doc["detail"] and doc["actions"] == []
    assert pstatus == 409
    assert fake.calls() == [["status", "--json"]] * 2


@pytest.mark.asyncio
async def test_serve_set_shows_the_address_qr_and_three_steps(
        tmp_path, monkeypatch, fake):
    """AC-553: linked when the name forwards to THIS daemon's PROXIED port."""
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        fake.set(status=_status(),
                 serve_status=_serving(f"http://127.0.0.1:{_proxied(three)}"))
        status, doc = await _get(three, hub_mod.PROXIED)
    assert status == 200 and doc["status"] == "linked", doc
    assert f"https://{NAME}/" in doc["headline"]
    for step in ("Scan", "microphone", "Add to Home Screen"):
        assert step in doc["detail"]
    prefix = "data:image/png;base64,"
    assert doc["qr"].startswith(prefix)
    assert base64.b64decode(doc["qr"][len(prefix):])[:8] == b"\x89PNG\r\n\x1a\n"
    assert doc["actions"] == [] and doc["poll"] is False
    # The pairing document's shape, the one `whatsapp.page_doc` serves.
    assert set(doc) == {"ok", "status", "headline", "detail", "tone", "qr",
                        "qr_count", "poll", "actions"}


@pytest.mark.asyncio
async def test_serve_not_set_offers_setup_only_on_this_machine(
        tmp_path, monkeypatch, fake):
    """GET is open (R2); only a local caller is offered the button."""
    fake.set(status=_status(), serve_status={})
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        _, local = await _get(three, hub_mod.LOCAL)
        status, phone = await _get(three, hub_mod.PROXIED)
    assert local["status"] == "ready"
    assert [a["id"] for a in local["actions"]] == ["setup"]
    assert local["actions"][0]["method"] == "POST"
    assert status == 200 and phone["status"] == "ready"
    assert phone["actions"] == []
    assert _serve_calls(fake) == []


@pytest.mark.asyncio
async def test_a_port_already_forwarding_elsewhere_is_never_replaced(
        tmp_path, monkeypatch, fake):
    """The desk's 443 already forwarded to another program [MEASURED
    2026-10-07]: Butler says so and runs nothing over it."""
    fake.set(status=_status(),
             serve_status=_serving("http://127.0.0.1:8787"))
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        _, doc = await _get(three, hub_mod.LOCAL)
        status, _ = await _post(three, hub_mod.LOCAL)
    assert doc["status"] == "taken" and "8787" in doc["detail"]
    assert doc["actions"] == []
    assert status == 409 and _serve_calls(fake) == []


@pytest.mark.asyncio
async def test_proxy_port_zero_means_phone_access_is_off(
        tmp_path, monkeypatch, fake):
    fake.set(status=_status(), serve_status={})
    hub = _hub(tmp_path, monkeypatch)
    three = _Three(hub, monkeypatch)
    three.socks.pop(hub_mod.PROXIED).close()
    async with three:
        _, doc = await _get(three, hub_mod.LOCAL)
        status, _ = await _post(three, hub_mod.LOCAL)
    assert doc["status"] == "off" and doc["actions"] == []
    assert status == 409 and fake.calls() == []


# --------------------------------------------------------------------------- #
# POST: set-up (AC-554)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_setup_runs_the_port_forward_to_the_proxied_listener(
        tmp_path, monkeypatch, fake):
    fake.set(status=_status(), serve_status={})
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _post(three, hub_mod.LOCAL)
        proxy = _proxied(three)
    assert _serve_calls(fake) == [
        ["serve", "--bg", "--https=443", f"http://127.0.0.1:{proxy}"]]
    assert status == 200 and doc["status"] == "linked", doc


@pytest.mark.asyncio
async def test_setup_uses_the_https_port_setting(tmp_path, monkeypatch, fake):
    fake.set(status=_status(), serve_status=_serving("http://127.0.0.1:8787"))
    hub = _hub(tmp_path, monkeypatch)
    hub.settings.update({"phone_link_port": 10000})
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _post(three, hub_mod.LOCAL)
        proxy = _proxied(three)
    assert _serve_calls(fake) == [
        ["serve", "--bg", "--https=10000", f"http://127.0.0.1:{proxy}"]]
    assert doc["status"] == "linked" and f"https://{NAME}:10000/" in \
        doc["headline"]


@pytest.mark.asyncio
async def test_no_https_certificates_shows_tailscales_words_verbatim(
        tmp_path, monkeypatch, fake):
    fake.set(status=_status(certs=False), serve_status={},
             serve_error=CERT_ERROR)
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _post(three, hub_mod.LOCAL)
    assert status == 502 and doc["ok"] is False
    assert CERT_ERROR in doc["error"]
    assert "Enable HTTPS Certificates" in doc["error"]
    assert [a["id"] for a in doc["actions"]] == ["setup"]


@pytest.mark.asyncio
async def test_post_is_refused_on_the_proxied_listener(tmp_path, monkeypatch,
                                                       fake):
    """AC-541 applied: a phone through `tailscale serve` arrives from
    127.0.0.1 on PROXIED and must not change the tailnet. Refused BEFORE
    anything runs: no Tailscale call at all."""
    fake.set(status=_status(), serve_status={})
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        status, doc = await _post(three, hub_mod.PROXIED)
    assert status == 403 and doc["ok"] is False
    assert fake.calls() == []


@pytest.mark.asyncio
async def test_post_is_refused_on_main_from_another_address(
        tmp_path, monkeypatch, fake):
    ip = _lan_address()
    if ip is None:
        pytest.skip("this machine has no non-loopback IPv4 address")
    fake.set(status=_status(), serve_status={})
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch, main_host=ip) as three:
        status, _ = await _post(three, hub_mod.MAIN)
    assert status == 403 and fake.calls() == []


# --------------------------------------------------------------------------- #
# Never the public internet (DA-16, section 9)
# --------------------------------------------------------------------------- #
def _code_strings(path: Path) -> list[str]:
    """Every string literal in the module that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_no_string_in_the_module_names_funnel():
    strings = _code_strings(REPO / "daemon" / "phonelink.py")
    assert strings, "the sweep found no strings at all"
    bad = [s for s in strings if "funnel" in s.lower()]
    assert bad == [], f"phonelink.py names funnel in code: {bad}"


def test_the_sweep_would_catch_one(tmp_path):
    """A witness for the gate above (test/AGENTS.md: a gate carries one)."""
    p = tmp_path / "m.py"
    p.write_text('"""funnel in a docstring is fine."""\n'
                 'ARGV = ["tailscale", "Funnel", "--bg"]\n', encoding="utf-8")
    assert [s for s in _code_strings(p) if "funnel" in s.lower()] == ["Funnel"]


def test_macos_finds_the_cli_inside_the_app_bundle(tmp_path, monkeypatch):
    """docs/macos.md: the Mac App Store / standalone Tailscale puts its CLI
    inside the app, not on PATH. Found 2026-10-29 on the Mac mini: phone
    link said "not installed" and the gate's owner lookup (RA-6) found
    nobody. Off macOS the bundle path is never consulted."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    cli = tmp_path / "Tailscale"
    cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(phonelink, "MAC_APP_CLI", cli)
    monkeypatch.setattr(phonelink.sys, "platform", "darwin")
    assert phonelink.tailscale_path() == str(cli)
    monkeypatch.setattr(phonelink.sys, "platform", "win32")
    assert phonelink.tailscale_path() is None
    monkeypatch.setattr(phonelink.sys, "platform", "darwin")
    cli.unlink()
    assert phonelink.tailscale_path() is None
