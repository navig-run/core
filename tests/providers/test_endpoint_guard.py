"""SSRF endpoint guard tests — the security gate for custom endpoints."""

from __future__ import annotations

import pytest

from navig.providers.connection_types import ConnectionValidationError
from navig.providers.endpoint_guard import assert_safe_endpoint

# ── always-blocked (every driver), even with allow_private ───────────────────


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",   # cloud metadata
    "http://169.254.1.1/",                         # link-local
    "http://[fe80::1]/",                           # ipv6 link-local
    "http://0.0.0.0/",                             # unspecified
    "http://224.0.0.1/",                           # multicast
])
def test_blocks_dangerous_addresses_even_for_local(url):
    with pytest.raises(ConnectionValidationError):
        assert_safe_endpoint(url, allow_private=True)


# ── private/loopback: blocked by default, allowed for local ──────────────────


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1",
    "http://10.0.0.5/v1",
    "http://192.168.1.10/v1",
    "http://172.16.0.9/v1",
])
def test_private_blocked_by_default_allowed_for_local(url):
    with pytest.raises(ConnectionValidationError):
        assert_safe_endpoint(url, allow_private=False)
    # local runtime templates may use loopback / LAN
    assert_safe_endpoint(url, allow_private=True)


# ── scheme + host validation ─────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "ws://x/y"])
def test_rejects_non_http_schemes(url):
    with pytest.raises(ConnectionValidationError):
        assert_safe_endpoint(url)


def test_allows_public_https_endpoint():
    # a clearly public host must pass (DNS resolves to a public IP)
    assert_safe_endpoint("https://api.openai.com/v1")


def test_empty_is_noop():
    assert_safe_endpoint(None)
    assert_safe_endpoint("")
