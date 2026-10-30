"""LAN is opt-in: the bind address follows `lan_access` (docs/remote-access.md
RA-7, AC-730).

`butlerd.main` reads `lan_access` from the settings file BEFORE binding. False
binds MAIN to 127.0.0.1, true to 0.0.0.0 and REQUIRES TLS (no cert: exit
non-zero naming `scripts/make-cert.py`), and an explicit `--host` still wins.
The end-to-end rows read the address off the socket the daemon actually bound
(its own "listening (main)" line is printed from `getsockname()`), never a
variable.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from conftest import DAEMON, free_port                       # noqa: E402
from daemon import butlerd                                   # noqa: E402

import launch                                                # noqa: E402

BOOT_S = 90


def _settings(tmp_path, **values) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# the decision, as a pure function main calls
# --------------------------------------------------------------------------- #
def test_lan_access_off_binds_loopback(tmp_path):
    assert butlerd.bind_host(None, _settings(tmp_path, lan_access=False)) \
        == "127.0.0.1"


def test_lan_access_absent_binds_loopback(tmp_path):
    assert butlerd.bind_host(None, tmp_path / "missing.json") == "127.0.0.1"


def test_lan_access_on_binds_every_interface(tmp_path):
    assert butlerd.bind_host(None, _settings(tmp_path, lan_access=True)) \
        == "0.0.0.0"


def test_an_explicit_host_wins_over_the_setting(tmp_path):
    on = _settings(tmp_path, lan_access=True)
    assert butlerd.bind_host("127.0.0.1", on) == "127.0.0.1"
    off = _settings(tmp_path, lan_access=False)
    assert butlerd.bind_host("0.0.0.0", off) == "0.0.0.0"


def test_a_lan_bind_without_tls_is_refused_naming_the_cert_script():
    with pytest.raises(SystemExit) as e:
        butlerd.require_tls_off_loopback("0.0.0.0", None)
    assert "make-cert.py" in str(e.value)


def test_loopback_needs_no_tls_and_a_lan_bind_with_tls_passes():
    butlerd.require_tls_off_loopback("127.0.0.1", None)
    butlerd.require_tls_off_loopback("0.0.0.0", object())


def test_the_launcher_names_the_real_bind_address(tmp_path):
    assert "127.0.0.1" in launch.bind_label(
        _settings(tmp_path, lan_access=False))
    assert "0.0.0.0" in launch.bind_label(
        _settings(tmp_path, lan_access=True))


# --------------------------------------------------------------------------- #
# end to end: a scratch daemon
# --------------------------------------------------------------------------- #
def _scratch(tmp_path, **settings) -> tuple[dict, Path]:
    state = tmp_path / "state"
    state.mkdir()
    # Scratch-daemon rule: no system-wide keyboard hook on the real desk.
    (state / "settings.json").write_text(
        json.dumps({"hotkeys_enabled": False, **settings}), encoding="utf-8")
    env = {**os.environ, "BUTLER_STATE": str(state),
           "BUTLER_ENDPOINTER": "none", "BUTLER_TEST_STUB_STT": "1",
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("BUTLER_TLS", None)
    return env, tmp_path / "daemon.log"


def _spawn(env, log, *extra):
    out = open(log, "wb")
    try:
        return subprocess.Popen(
            [sys.executable, "-u", "-X", "utf8", str(DAEMON), *extra],
            cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT)
    finally:
        out.close()


def test_a_real_daemon_with_lan_access_and_no_tls_refuses_to_boot(tmp_path):
    env, log = _scratch(tmp_path, lan_access=True)
    proc = _spawn(env, log, "--no-tls", "--port", str(free_port()))
    try:
        code = proc.wait(timeout=BOOT_S)
    finally:
        if proc.poll() is None:
            proc.kill()
    text = log.read_text(encoding="utf-8", errors="replace")
    assert code != 0, text
    assert "make-cert.py" in text, text
    assert "listening (main)" not in text, text


def test_a_real_daemon_by_default_listens_on_loopback_only(tmp_path):
    env, log = _scratch(tmp_path)
    proc = _spawn(env, log, "--no-tls", "--port", "0")
    try:
        deadline = time.monotonic() + BOOT_S
        found = None
        while found is None:
            assert proc.poll() is None, log.read_text(errors="replace")
            assert time.monotonic() < deadline, log.read_text(errors="replace")
            found = re.search(r"listening \(main\) on http://([^:\s]+):(\d+)",
                              log.read_text(encoding="utf-8",
                                            errors="replace"))
            time.sleep(0.2)
        assert found.group(1) == "127.0.0.1", found.group(0)
    finally:
        if proc.poll() is None:
            proc.kill()


# --------------------------------------------------------------------------- #
# The restart trap: `lan_access` is restart-flagged, and AC-715 restarts the
# daemon by itself once idle. Turned on with no certificate, that restart
# would refuse to boot (RA-7) and leave Butler down. So the WRITE is refused.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_lan_access_cannot_be_turned_on_without_a_certificate(
        tmp_path, monkeypatch):
    from daemon import hub as hub_mod
    from test_listeners import _Three, _hub
    monkeypatch.setenv("BUTLER_TLS_CERT", str(tmp_path / "none.pem"))
    monkeypatch.setenv("BUTLER_TLS_KEY", str(tmp_path / "none-key.pem"))
    hub = _hub(tmp_path, monkeypatch)
    async with _Three(hub, monkeypatch) as three:
        async with three.http.post(three.url(hub_mod.LOCAL, "/api/settings"),
                                   json={"lan_access": True}) as r:
            assert r.status == 400
            assert "make-cert.py" in (await r.json())["error"]
        assert hub.settings.get("lan_access") is False
        # With a certificate present the same write is accepted (the control).
        for name in ("none.pem", "none-key.pem"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        async with three.http.post(three.url(hub_mod.LOCAL, "/api/settings"),
                                   json={"lan_access": True}) as r:
            assert r.status == 200, await r.text()
        assert hub.settings.get("lan_access") is True
