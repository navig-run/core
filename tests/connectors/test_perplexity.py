"""
Tests for the Perplexity AI connector.

All network calls are mocked — no real API key required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.perplexity.connector import PerplexityConnector
from navig.connectors.types import (
    Action,
    ActionType,
    ConnectorDomain,
    ConnectorStatus,
    ResourceType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def connector():
    return PerplexityConnector()


def _mock_response(data: dict):
    """Build a fake httpx Response-like object."""
    mock = MagicMock()
    mock.status_code = 200
    mock.is_success = True
    mock.json.return_value = data
    mock.text = ""
    return mock


_FAKE_COMPLETION = {
    "choices": [{"message": {"content": "The answer is 42."}}],
    "citations": [
        {"url": "https://example.com/1", "title": "Source 1", "snippet": "Relevant text"},
        {"url": "https://example.com/2", "title": "Source 2", "snippet": "More text"},
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_id():
    assert PerplexityConnector.manifest.id == "perplexity"


def test_manifest_domain():
    assert PerplexityConnector.manifest.domain == ConnectorDomain.AI_RESEARCH


def test_manifest_requires_no_oauth():
    assert PerplexityConnector.manifest.requires_oauth is False


def test_manifest_can_search():
    assert PerplexityConnector.manifest.can_search is True


def test_manifest_cannot_act():
    assert PerplexityConnector.manifest.can_act is False


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_reads_env_variable(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()
    assert connector.status == ConnectorStatus.CONNECTED
    assert connector._api_key == "sk-test-key"


@pytest.mark.asyncio
async def test_connect_raises_without_key(connector, monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key not found"):
        await connector.connect()
    assert connector.status == ConnectorStatus.ERROR


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_answer_and_citations(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    with patch.object(connector, "_post", new=AsyncMock(return_value=_FAKE_COMPLETION)):
        results = await connector.search("meaning of life", limit=2)

    assert len(results) >= 1
    # First result is the synthesised answer
    answer = results[0]
    assert "42" in answer.preview
    assert answer.source == "perplexity"
    assert answer.resource_type == ResourceType.DOCUMENT


@pytest.mark.asyncio
async def test_search_includes_citations(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    with patch.object(connector, "_post", new=AsyncMock(return_value=_FAKE_COMPLETION)):
        results = await connector.search("some query", limit=2)

    # Should have answer + up to 2 citations
    assert len(results) == 3
    cite_urls = {r.url for r in results[1:]}
    assert "https://example.com/1" in cite_urls


@pytest.mark.asyncio
async def test_search_raises_when_not_connected(connector):
    with pytest.raises(RuntimeError, match="not connected"):
        await connector.search("query")


@pytest.mark.asyncio
async def test_search_no_citations_still_returns_answer(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    completion = {"choices": [{"message": {"content": "Short answer."}}]}
    with patch.object(connector, "_post", new=AsyncMock(return_value=completion)):
        results = await connector.search("quick question")

    assert len(results) == 1
    assert results[0].preview == "Short answer."


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_always_returns_none(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()
    result = await connector.fetch("any-id")
    assert result is None


# ---------------------------------------------------------------------------
# act()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_act_returns_failure(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()
    action = Action(action_type=ActionType.CREATE)
    result = await connector.act(action)
    assert result.success is False
    assert result.error is not None
    assert len(result.error) > 0


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_ok(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    ping_response = {"choices": [{"message": {"content": "pong"}}]}
    with patch.object(connector, "_post", new=AsyncMock(return_value=ping_response)):
        health = await connector.health_check()

    assert health.ok is True
    assert health.latency_ms >= 0


@pytest.mark.asyncio
async def test_health_check_not_connected(connector):
    health = await connector.health_check()
    assert health.ok is False
    assert "Not connected" in health.message


@pytest.mark.asyncio
async def test_health_check_api_error(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    with patch.object(connector, "_post", new=AsyncMock(side_effect=RuntimeError("API down"))):
        health = await connector.health_check()

    assert health.ok is False
    assert "API down" in health.message


# ---------------------------------------------------------------------------
# disconnect()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_clears_state(connector, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()
    assert connector.status == ConnectorStatus.CONNECTED

    await connector.disconnect()
    assert connector.status == ConnectorStatus.DISCONNECTED
    assert connector._api_key is None


# ---------------------------------------------------------------------------
# _post() error hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_401_raises_connector_auth_error(connector, monkeypatch):
    """HTTP 401 from Perplexity API should raise ConnectorAuthError, not ValueError."""
    from navig.connectors.errors import ConnectorAuthError

    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-bad-key")
    await connector.connect()

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.is_success = False
    mock_resp.text = "Unauthorized"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        with pytest.raises(ConnectorAuthError):
            await connector._post("/chat/completions", {"model": "sonar", "messages": []})

    # Status set to ERROR after 401
    assert connector.status == ConnectorStatus.ERROR


@pytest.mark.asyncio
async def test_post_429_raises_connector_rate_limit_error(connector, monkeypatch):
    """HTTP 429 from Perplexity API should raise ConnectorRateLimitError, not RuntimeError."""
    from navig.connectors.errors import ConnectorRateLimitError

    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-test-key")
    await connector.connect()

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.is_success = False
    mock_resp.text = "Too Many Requests"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        with pytest.raises(ConnectorRateLimitError):
            await connector._post("/chat/completions", {"model": "sonar", "messages": []})
