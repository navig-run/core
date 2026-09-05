"""Tests for navig.tools.site_check — SiteCheckTool.

Covers the SSRF hardening: this agent-invokable tool fetches a model-supplied URL, so it
now validates the initial URL and re-checks every redirect hop via `navig.net.ssrf.check_url`
before any network I/O (an unchecked `head(url)` let the agent reach 169.254.169.254 etc.).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.net.ssrf import SsrfBlockedError
from navig.tools.site_check import SiteCheckTool


class _Resp:
    """Minimal httpx-response stand-in for the manual redirect loop."""

    def __init__(self, status_code: int = 200, location: str | None = None):
        self.status_code = status_code
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}


def _client(head_impl):
    """Build a FakeClient whose async .head(url) delegates to *head_impl*."""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def head(self, url):
            return head_impl(url)

    return FakeClient


def _make_mock_httpx(FakeClient):
    mock = MagicMock()
    mock.AsyncClient.return_value = FakeClient()
    mock.ConnectError = ConnectionError
    mock.TimeoutException = TimeoutError
    mock.Timeout = MagicMock(return_value=None)
    return mock


@pytest.fixture
def tool():
    return SiteCheckTool()


class TestSiteCheckTool:
    def test_name(self, tool):
        assert tool.name == "site_check"


class TestSiteCheckRun:
    async def test_missing_url_returns_error(self, tool):
        result = await tool.run({})
        assert result.success is False
        assert "url arg required" in (result.error or "")

    async def test_empty_url_returns_error(self, tool):
        result = await tool.run({"url": ""})
        assert result.success is False

    async def test_adds_https_prefix_for_bare_domain(self, tool):
        captured: list[str] = []
        FakeClient = _client(lambda url: captured.append(url) or _Resp(200))
        with patch("navig.tools.site_check.check_url"), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ), patch("navig.tools.site_check._get_cert_expiry", new=AsyncMock(return_value=None)):
            result = await tool.run({"url": "example.com"})
        assert result.success is True
        assert captured[0].startswith("https://")

    async def test_successful_response_has_output_keys(self, tool):
        FakeClient = _client(lambda url: _Resp(200))
        with patch("navig.tools.site_check.check_url"), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ), patch("navig.tools.site_check._get_cert_expiry", new=AsyncMock(return_value=None)):
            result = await tool.run({"url": "https://example.com"})
        assert result.success is True
        for key in ("url", "status_code", "latency_ms", "online", "redirects", "final_url"):
            assert key in result.output

    async def test_connect_error_returns_failure(self, tool):
        def _boom(url):
            raise ConnectionError("refused")

        FakeClient = _client(_boom)
        with patch("navig.tools.site_check.check_url"), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ):
            result = await tool.run({"url": "https://bad.example.com"})
        assert result.success is False
        assert "connection failed" in (result.error or "")

    async def test_timeout_returns_failure(self, tool):
        def _boom(url):
            raise TimeoutError("timeout")

        FakeClient = _client(_boom)
        with patch("navig.tools.site_check.check_url"), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ):
            result = await tool.run({"url": "https://slow.example.com"})
        assert result.success is False
        assert "timed out" in (result.error or "")

    async def test_httpx_not_installed_returns_error(self, tool):
        with patch.dict(sys.modules, {"httpx": None}):
            result = await tool.run({"url": "https://example.com"})
        assert result.success is False
        assert "httpx not installed" in (result.error or "")


class TestSiteCheckSsrf:
    """The URL is model-controlled — internal/metadata targets must be blocked, not fetched."""

    async def test_blocked_url_is_not_fetched(self, tool):
        fetched: list[str] = []
        FakeClient = _client(lambda url: fetched.append(url) or _Resp(200))
        with patch(
            "navig.tools.site_check.check_url",
            side_effect=SsrfBlockedError("http://169.254.169.254/", "169.254.169.254"),
        ), patch.dict(sys.modules, {"httpx": _make_mock_httpx(FakeClient)}):
            result = await tool.run({"url": "http://169.254.169.254/"})
        assert result.success is False
        assert "blocked by SSRF policy" in (result.error or "")
        assert fetched == [], "the blocked URL must never be fetched"

    async def test_redirect_to_blocked_target_is_stopped(self, tool):
        # First hop OK; it 302s to the cloud-metadata endpoint. check_url raises on hop 2.
        calls = {"n": 0}

        def _fake_check(url, policy):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise SsrfBlockedError(url, "169.254.169.254")

        FakeClient = _client(lambda url: _Resp(302, location="http://169.254.169.254/latest"))
        with patch("navig.tools.site_check.check_url", side_effect=_fake_check), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ):
            result = await tool.run({"url": "http://ok.example/"})
        assert result.success is False
        assert "blocked by SSRF policy" in (result.error or "")

    async def test_too_many_redirects_is_bounded(self, tool):
        FakeClient = _client(lambda url: _Resp(302, location="https://loop.example/next"))
        with patch("navig.tools.site_check.check_url"), patch.dict(
            sys.modules, {"httpx": _make_mock_httpx(FakeClient)}
        ):
            result = await tool.run({"url": "https://loop.example/"})
        assert result.success is False
        assert "too many redirects" in (result.error or "")
