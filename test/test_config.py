"""load_config and its BUTLER_CONFIG override.

The owner keeps a real `config/models.json` on disk for the live daemon
(transcribe.cpp, kokoro, a CPU ASR backend). The test suite must not read it,
or every daemon fixture boots real engines and fails -- the same reason
BUTLER_STATE exists (test/AGENTS.md). BUTLER_CONFIG is that isolation: it
points the loader somewhere hermetic, and conftest forces it session-wide.
"""
import json

import pytest

from daemon import butlerd


def test_a_missing_config_is_the_all_fakes_default(tmp_path, monkeypatch):
    """No file means fakes, so a fresh clone runs with no GPU or download, and
    NO brain is named -- which brain answers is the owner's `brain_mode`
    setting (AC-7b)."""
    monkeypatch.setenv("BUTLER_CONFIG", str(tmp_path / "nope.json"))
    cfg = butlerd.load_config()
    assert cfg["adapters"] == {"asr": "fake", "tts": "fake"}
    assert "brain" not in cfg["adapters"]


def test_butler_config_overrides_the_repo_config(tmp_path, monkeypatch):
    """The override is honoured, so a real config on disk never reaches a test.
    Before this, load_config read `config/models.json` unconditionally and a
    present file broke every daemon-booting test."""
    # A backend value the repo's own config does NOT use, so a test that
    # accidentally read `config/models.json` instead of the override would
    # fail rather than pass by coincidence.
    fixture = tmp_path / "models.json"
    fixture.write_text(json.dumps(
        {"adapters": {"asr": "transcribecpp", "tts": "kokoro"},
         "asr_engine": {"backend": "SENTINEL-vulkan"}}), encoding="utf-8")
    monkeypatch.setenv("BUTLER_CONFIG", str(fixture))
    cfg = butlerd.load_config()
    assert cfg["adapters"]["asr"] == "transcribecpp"
    assert cfg["asr_engine"] == {"backend": "SENTINEL-vulkan"}


def test_no_tls_is_plain_http():
    """--no-tls forces plain HTTP even where a cert exists (the suite's own
    posture, so an http client can reach the fixture daemon)."""
    assert butlerd.ssl_context(False) is None


def test_auto_without_a_cert_is_plain_http(tmp_path, monkeypatch):
    """The DEFAULT (auto) must not crash a fresh clone that has no cert -- it
    falls back to HTTP rather than failing, unlike an explicit --tls."""
    monkeypatch.setenv("BUTLER_TLS_CERT", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("BUTLER_TLS_KEY", str(tmp_path / "nope.key"))
    assert butlerd.ssl_context(None) is None


def test_explicit_tls_without_a_cert_fails_loudly(tmp_path, monkeypatch):
    """Fail fast (project philosophy). Asking for TLS and silently serving HTTP
    is the phone's microphone denied, several layers from the cause -- a secure
    context is exactly what LAN mic access needs. Auto falls back; --tls does
    not."""
    monkeypatch.setenv("BUTLER_TLS_CERT", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("BUTLER_TLS_KEY", str(tmp_path / "nope.key"))
    with pytest.raises(SystemExit):
        butlerd.ssl_context(True)
