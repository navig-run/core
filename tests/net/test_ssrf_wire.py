"""
tests/net/test_ssrf_wire.py
───────────────────────────
The SSRF guard wired into config (`policy_from_config`) and the `browser_fetch`
agent tool — the first production call sites (previously the guard protected
nothing).

No network I/O: literal IPs (169.254.x, 127.0.0.1) resolve without DNS, and
`get_config_manager` is monkeypatched.
"""
from __future__ import annotations

import pytest

from navig.net.ssrf import SsrfBlockedError, check_url, policy_from_config


class _FakeCM:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    def get_global_config(self) -> dict:
        return self._cfg


def _patch_config(monkeypatch, cfg: dict) -> None:
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM(cfg))


# ── policy_from_config ──────────────────────────────────────────────────────


def test_policy_default_blocks_private(monkeypatch):
    _patch_config(monkeypatch, {})
    policy = policy_from_config()
    assert policy.allow_private_network is False
    with pytest.raises(SsrfBlockedError):
        check_url("http://127.0.0.1:8765/", policy)


def test_policy_string_false_stays_secure(monkeypatch):
    # The bool("false") footgun: a stored "false" must NOT enable private —
    # otherwise being explicit about staying secure would *loosen* the guard.
    _patch_config(monkeypatch, {"net": {"ssrf": {"allow_private_network": "false"}}})
    assert policy_from_config().allow_private_network is False


def test_policy_enable_private_and_allowlist(monkeypatch):
    _patch_config(
        monkeypatch,
        {"net": {"ssrf": {
            "allow_private_network": "true",
            "allowed_domains": ["internal.example", " ", "svc.local"],
        }}},
    )
    policy = policy_from_config()
    assert policy.allow_private_network is True
    assert policy.allowed_domains == ("internal.example", "svc.local")  # blanks dropped
    check_url("http://127.0.0.1:8765/", policy)  # allow_private → no raise


def test_policy_broken_config_is_secure(monkeypatch):
    def boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("navig.config.get_config_manager", boom)
    assert policy_from_config().allow_private_network is False


# ── browser_fetch gate (the agent's arbitrary-URL fetcher) ──────────────────


async def test_browser_fetch_blocks_cloud_metadata(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    result = await BrowserFetchTool().run({"url": "http://169.254.169.254/latest/meta-data/"})
    assert result.success is False
    assert "SSRF guard" in (result.error or "")


async def test_browser_fetch_blocks_localhost_daemon(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    # bare host (no scheme) → tool prepends https:// → still resolves to loopback
    result = await BrowserFetchTool().run({"url": "127.0.0.1:8765/api/deck/exec"})
    assert result.success is False
    assert "private/internal" in (result.error or "")


async def test_browser_fetch_rejects_empty_url(monkeypatch):
    _patch_config(monkeypatch, {})
    from navig.tools.browser_fetch import BrowserFetchTool

    result = await BrowserFetchTool().run({"url": "   "})
    assert result.success is False
    assert "url arg required" in (result.error or "")
