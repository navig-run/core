from __future__ import annotations

from typing import Any

import pytest

from navig.integrations.firecrawl.client import (
    FirecrawlClient,
    FirecrawlError,
    get_firecrawl_client,
)
from navig.mcp.tools import system as system_tools


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload


def test_get_firecrawl_client_without_key(monkeypatch):
    monkeypatch.setattr("navig.integrations.firecrawl.client.resolve_secret", lambda *a, **k: None)
    client = get_firecrawl_client()
    assert isinstance(client, FirecrawlClient)
    assert client.api_key is None


def test_get_firecrawl_client_with_key(monkeypatch):
    monkeypatch.setattr(
        "navig.integrations.firecrawl.client.resolve_secret",
        lambda *a, **k: "fc-test-key",
    )
    client = get_firecrawl_client()
    assert client.api_key == "fc-test-key"


def test_firecrawl_429_maps_to_quota_message(monkeypatch):
    def _fake_request(*args, **kwargs):
        return _FakeResponse(429, payload={"message": "rate limited"})

    monkeypatch.setattr("requests.request", _fake_request)

    client = FirecrawlClient(api_key="fc-test")
    with pytest.raises(FirecrawlError) as exc:
        client.scrape(url="https://example.com", mode="scrape")

    assert exc.value.status_code == 429
    assert "quota reached" in str(exc.value).lower()


def test_firecrawl_5xx_is_retryable(monkeypatch):
    def _fake_request(*args, **kwargs):
        return _FakeResponse(503, payload={"message": "down"})

    monkeypatch.setattr("requests.request", _fake_request)

    client = FirecrawlClient(api_key="fc-test")
    with pytest.raises(FirecrawlError) as exc:
        client.scrape(url="https://example.com", mode="scrape")

    assert exc.value.status_code == 503
    assert exc.value.retryable is True


def test_system_register_includes_firecrawl_tool():
    class _Server:
        def __init__(self):
            self.tools = {}
            self._tool_handlers = {}

    server = _Server()
    system_tools.register(server)
    assert "firecrawl_scrape" in server.tools
    assert "firecrawl_scrape" in server._tool_handlers
    assert "firecrawl_crawl" in server.tools
    assert "firecrawl_search" in server.tools
    assert "firecrawl_crawl" in server._tool_handlers
    assert "firecrawl_search" in server._tool_handlers


def test_firecrawl_tool_uses_rest_fallback_when_no_mcp(monkeypatch):
    class _Client:
        def scrape(self, *, url: str, mode: str = "scrape", max_pages: int | None = None):
            return {"url": url, "mode": mode, "max_pages": max_pages}

    class _Server:
        pass

    seen_logs: list[str] = []

    class _Logger:
        @staticmethod
        def info(msg: str, *args):
            if args:
                msg = msg % args
            seen_logs.append(msg)

    monkeypatch.setattr("navig.integrations.firecrawl.get_firecrawl_client", lambda: _Client())
    monkeypatch.setattr(system_tools, "logger", _Logger())

    result = system_tools._tool_firecrawl_scrape(
        _Server(),
        {"url": "https://example.com", "mode": "crawl", "maxPages": 3},
    )

    assert result["success"] is True
    assert result["route"] == "rest"
    assert any("[firecrawl] MCP unavailable, using REST" in line for line in seen_logs)


def test_firecrawl_tool_prefers_mcp_when_client_available(monkeypatch):
    class _McpClient:
        def call_tool(self, name: str, params: dict[str, Any]):
            return {"name": name, "params": params}

    class _Server:
        def __init__(self):
            self._mcp_client = _McpClient()

    result = system_tools._tool_firecrawl_scrape(
        _Server(),
        {"url": "https://example.com", "mode": "scrape"},
    )

    assert result["success"] is True
    assert result["route"] == "mcp"
    assert result["result"]["name"] == "mcp_firecrawl_fir_firecrawl_scrape"


def test_firecrawl_search_and_crawl_use_rest_fallback(monkeypatch):
    class _Client:
        def search(self, *, query: str, limit: int = 5, scrape_inline: bool = False, sources=None):
            return {"query": query, "limit": limit, "scrape_inline": scrape_inline}

        def crawl(self, *, url: str, max_pages: int | None = None):
            return {"url": url, "max_pages": max_pages}

    class _Server:
        pass

    monkeypatch.setattr("navig.integrations.firecrawl.get_firecrawl_client", lambda: _Client())

    crawl_result = system_tools._tool_firecrawl_crawl(
        _Server(),
        {"url": "https://example.com", "maxPages": 4},
    )
    assert crawl_result["success"] is True
    assert crawl_result["route"] == "rest"

    search_result = system_tools._tool_firecrawl_search(
        _Server(),
        {"query": "python", "count": 3, "scrapeInline": True},
    )
    assert search_result["success"] is True
    assert search_result["route"] == "rest"
