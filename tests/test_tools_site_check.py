"""Tests for navig.tools.site_check — SiteCheckTool."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.site_check import SiteCheckTool


def _make_mock_httpx(FakeClient):
    """Build a sys.modules-compatible httpx mock."""
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
    @pytest.fixture
    def tool(self):
        return SiteCheckTool()

    async def test_missing_url_returns_error(self, tool):
        result = await tool.run({})
        assert result.success is False
        assert "url arg required" in (result.error or "")

    async def test_empty_url_returns_error(self, tool):
        result = await tool.run({"url": ""})
        assert result.success is False

    async def test_adds_https_prefix_for_bare_domain(self, tool):
        """If no scheme provided, https:// is prepended."""
        captured_urls = []

        class FakeResp:
            status_code = 200
            history = []
            url = "https://example.com"

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def head(self, url):
                captured_urls.append(url)
                return FakeResp()

        mock_httpx = _make_mock_httpx(FakeClient)
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            with patch("navig.tools.site_check._get_cert_expiry", new=AsyncMock(return_value=None)):
                result = await tool.run({"url": "example.com"})

        assert result.success is True
        assert captured_urls[0].startswith("https://")

    async def test_successful_response_has_output_keys(self, tool):
        class FakeResp:
            status_code = 200
            history = []
            url = "https://example.com"

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def head(self, url):
                return FakeResp()

        mock_httpx = _make_mock_httpx(FakeClient)
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            with patch("navig.tools.site_check._get_cert_expiry", new=AsyncMock(return_value=None)):
                result = await tool.run({"url": "https://example.com"})

        assert result.success is True
        for key in ("url", "status_code", "latency_ms", "online"):
            assert key in result.output

    async def test_connect_error_returns_failure(self, tool):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def head(self, url):
                raise ConnectionError("refused")

        mock_httpx = _make_mock_httpx(FakeClient)
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = await tool.run({"url": "https://bad.example.com"})

        assert result.success is False
        assert "connection failed" in (result.error or "")

    async def test_timeout_returns_failure(self, tool):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def head(self, url):
                raise TimeoutError("timeout")

        mock_httpx = _make_mock_httpx(FakeClient)
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = await tool.run({"url": "https://slow.example.com"})

        assert result.success is False
        assert "timed out" in (result.error or "")

    async def test_httpx_not_installed_returns_error(self, tool):
        with patch.dict(sys.modules, {"httpx": None}):
            result = await tool.run({"url": "https://example.com"})
        assert result.success is False
        assert "httpx not installed" in (result.error or "")
