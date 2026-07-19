"""Hardened opt-in engine (formerly "clearcote"): resolution, checksum gate, arg
building, and back-compat (legacy config key + legacy engine alias)."""

from __future__ import annotations

import hashlib

import pytest

from navig.browser import hardened as hd


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_unavailable_when_no_binary(cfg, monkeypatch):
    monkeypatch.setattr(hd, "_hardened_config", lambda: {})
    assert hd.hardened_binary_path() is None
    assert hd.is_available() is False


def test_explicit_path_wins(cfg, monkeypatch, tmp_path):
    binary = tmp_path / "chrome.exe"
    binary.write_text("x")
    monkeypatch.setattr(hd, "_hardened_config", lambda: {"path": str(binary)})
    assert hd.hardened_binary_path() == binary
    assert hd.is_available() is True


def test_ensure_raises_without_url_or_binary(cfg, monkeypatch):
    monkeypatch.setattr(hd, "_hardened_config", lambda: {})
    with pytest.raises(hd.HardenedEngineUnavailable):
        hd.ensure_hardened()


def test_checksum_verify(tmp_path):
    f = tmp_path / "blob"
    f.write_bytes(b"hello hardened")
    good = hashlib.sha256(b"hello hardened").hexdigest()
    assert hd._verify_checksum(f, good) is True
    assert hd._verify_checksum(f, "0" * 64) is False


def test_download_rejected_on_checksum_mismatch(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(hd, "_hardened_config",
                        lambda: {"url": "https://x/engine.zip", "sha256": "0" * 64})

    def fake_retrieve(url, dest):
        from pathlib import Path
        Path(dest).write_bytes(b"malicious")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlretrieve", fake_retrieve)
    with pytest.raises(hd.HardenedEngineUnavailable, match="SHA-256 mismatch"):
        hd.ensure_hardened()


def test_build_args_includes_port_proxy_headless(cfg):
    from pathlib import Path

    ctl = hd.HardenedController(port=9401, headless=True, proxy="http://u:pw@h:8080",
                               webrtc_protection=True)
    args = ctl._build_args(Path("chrome"))
    assert "--remote-debugging-port=9401" in args
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert "--headless=new" in args
    # proxy server passed WITHOUT credentials on the CLI (creds don't belong on argv)
    assert "--proxy-server=http://h:8080" in args
    assert not any("pw" in a for a in args)
    assert any("webrtc-ip-handling-policy" in a for a in args)


def test_build_args_headful_omits_headless(cfg):
    from pathlib import Path

    ctl = hd.HardenedController(port=9402, headless=False)
    args = ctl._build_args(Path("chrome"))
    assert not any("headless" in a for a in args)


# ── Back-compat (pre-3.24 "clearcote" → "hardened" rename) ─────────────────────

def test_legacy_clearcote_config_key_is_honored(cfg, monkeypatch, tmp_path):
    """An existing browser.clearcote.* block still resolves via _hardened_config."""
    binary = tmp_path / "chrome.exe"
    binary.write_text("x")

    class _CM:
        global_config = {"browser": {"clearcote": {"path": str(binary)}}}

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
    assert hd._hardened_config() == {"path": str(binary)}
    assert hd.hardened_binary_path() == binary


def test_hardened_key_wins_over_legacy(monkeypatch):
    class _CM:
        global_config = {"browser": {"hardened": {"path": "/new"}, "clearcote": {"path": "/old"}}}

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
    assert hd._hardened_config() == {"path": "/new"}


def test_legacy_engine_alias_maps_to_hardened():
    """router.get_browser(engine="clearcote") still returns the hardened controller."""
    from navig.browser import router
    assert type(router.get_browser(engine="clearcote")).__name__ == "HardenedController"
    assert type(router.get_browser(engine="hardened")).__name__ == "HardenedController"
