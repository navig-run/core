"""
tests/net/test_ssrf.py
──────────────────────
Tests for navig.net.ssrf — SSRF guard (Item 6).

No network I/O: DNS resolution is monkeypatched throughout.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from navig.net.ssrf import (
    SsrfBlockedError,
    SsrfPolicy,
    _is_blocked,
    _parse_ip,
    check_url,
    is_safe_url,
    resolve_host,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _mock_resolve(ip: str):
    """Patch resolve_host to always return a single IP."""
    return patch("navig.net.ssrf.resolve_host", return_value=[ip])


# ──────────────────────────────────────────────────────────────────────────────
# _parse_ip
# ──────────────────────────────────────────────────────────────────────────────


class TestParseIp:
    def test_ipv4(self):
        from ipaddress import IPv4Address
        assert _parse_ip("1.2.3.4") == IPv4Address("1.2.3.4")

    def test_ipv6(self):
        from ipaddress import IPv6Address
        assert _parse_ip("::1") == IPv6Address("::1")

    def test_invalid_returns_none(self):
        assert _parse_ip("not-an-ip") is None


# ──────────────────────────────────────────────────────────────────────────────
# _is_blocked
# ──────────────────────────────────────────────────────────────────────────────


class TestIsBlocked:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",
        "127.255.255.255",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.0.1",        # cloud metadata
        "100.64.0.1",         # shared address space
        "::1",                # IPv6 loopback
        "fe80::1",            # IPv6 link-local
        "fc00::1",            # IPv6 unique local
    ])
    def test_blocked_ips(self, ip):
        addr = _parse_ip(ip)
        assert addr is not None
        assert _is_blocked(addr), f"{ip} should be blocked"

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",   # example.com
        "2600:1f18::1",    # AWS public IPv6
    ])
    def test_public_ips_not_blocked(self, ip):
        addr = _parse_ip(ip)
        assert addr is not None
        assert not _is_blocked(addr), f"{ip} should NOT be blocked"


# ──────────────────────────────────────────────────────────────────────────────
# check_url
# ──────────────────────────────────────────────────────────────────────────────


class TestCheckUrl:
    def test_public_ip_ok(self):
        with _mock_resolve("8.8.8.8"):
            check_url("https://example.com/path")  # no exception

    def test_loopback_blocked(self):
        with _mock_resolve("127.0.0.1"):
            with pytest.raises(SsrfBlockedError) as exc_info:
                check_url("http://internal.corp/api")
            assert "127.0.0.1" in str(exc_info.value)

    def test_private_ip_blocked(self):
        with _mock_resolve("10.0.0.5"):
            with pytest.raises(SsrfBlockedError):
                check_url("https://secret.corp/data")

    def test_non_http_raises_value_error(self):
        with pytest.raises(ValueError, match="only permits http"):
            check_url("ftp://example.com/file")

    def test_missing_host_raises_value_error(self):
        with pytest.raises(ValueError, match="no host"):
            check_url("https:///no-host")

    def test_allow_private_network_bypasses_block(self):
        with _mock_resolve("10.0.0.1"):
            policy = SsrfPolicy(allow_private_network=True)
            check_url("http://internal.corp/", policy)  # must not raise

    def test_domain_allowlist_bypasses_block(self):
        with _mock_resolve("127.0.0.1"):
            policy = SsrfPolicy(allowed_domains=("internal.corp",))
            check_url("http://internal.corp/", policy)  # must not raise

    def test_domain_allowlist_does_not_bypass_other_hosts(self):
        with _mock_resolve("127.0.0.1"):
            policy = SsrfPolicy(allowed_domains=("safe.example.com",))
            with pytest.raises(SsrfBlockedError):
                check_url("http://other.corp/", policy)

    def test_default_policy_used_when_none(self):
        with _mock_resolve("192.168.1.1"):
            with pytest.raises(SsrfBlockedError):
                check_url("http://router.local/")


# ──────────────────────────────────────────────────────────────────────────────
# is_safe_url
# ──────────────────────────────────────────────────────────────────────────────


class TestIsSafeUrl:
    def test_public_true(self):
        with _mock_resolve("8.8.8.8"):
            assert is_safe_url("https://example.com/") is True

    def test_private_false(self):
        with _mock_resolve("10.0.0.1"):
            assert is_safe_url("http://private.corp/") is False

    def test_invalid_scheme_false(self):
        assert is_safe_url("ftp://example.com/") is False


# ──────────────────────────────────────────────────────────────────────────────
# resolve_host (real DNS — skipped in CI via monkeypatch or by OS)
# ──────────────────────────────────────────────────────────────────────────────


class TestResolveHost:
    def test_returns_list_of_strings(self):
        # Use a stable public host; if DNS unavailable the test is skipped
        try:
            results = resolve_host("dns.google")
        except socket.gaierror:
            pytest.skip("DNS not available in this environment")
        assert isinstance(results, list)
        assert all(isinstance(r, str) for r in results)


# ──────────────────────────────────────────────────────────────────────────────
# SsrfBlockedError attributes
# ──────────────────────────────────────────────────────────────────────────────


class TestSsrfBlockedError:
    def test_attributes(self):
        err = SsrfBlockedError("http://x.com", "192.168.1.1")
        assert err.url == "http://x.com"
        assert err.resolved_ip == "192.168.1.1"
        assert "http://x.com" in str(err)
        assert "192.168.1.1" in str(err)

    def test_no_ip(self):
        err = SsrfBlockedError("http://x.com")
        assert err.resolved_ip == ""
        assert "http://x.com" in str(err)
