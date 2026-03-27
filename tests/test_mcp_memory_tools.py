# tests/test_mcp_memory_tools.py
"""Contract tests for MCP memory tool handlers (not storage layer)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(facts=None, stats_data=None, delete_ok=True):
    store = MagicMock()
    store.stats.return_value = stats_data or {"total": 0, "active": 0, "deleted": 0}
    store.soft_delete.return_value = delete_ok
    return store


# ---------------------------------------------------------------------------
# memory.key_facts.retrieve
# ---------------------------------------------------------------------------


class TestMemoryRetrieve:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_facts(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            MockRetriever.return_value.retrieve.return_value = []
            from navig.mcp_server import memory_retrieve

            result = await memory_retrieve(query="anything")
        assert result == {"facts": []}

    @pytest.mark.asyncio
    async def test_passes_limit_and_token_budget(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            instance = MockRetriever.return_value
            instance.retrieve.return_value = []
            from navig.mcp_server import memory_retrieve

            await memory_retrieve(query="q", limit=5, token_budget=500)
            instance.retrieve.assert_called_once_with(query="q", limit=5, token_budget=500)


# ---------------------------------------------------------------------------
# memory.key_facts.remember
# ---------------------------------------------------------------------------


class TestMemoryRemember:
    @pytest.mark.asyncio
    async def test_returns_added_count(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_extractor.FactExtractor") as MockExtractor,
        ):
            from unittest.mock import AsyncMock

            MockExtractor.return_value.extract_and_store = AsyncMock(return_value=3)
            from navig.mcp_server import memory_remember

            result = await memory_remember(text="I prefer dark mode", source="mcp")
        assert result == {"added": 3}

    @pytest.mark.asyncio
    async def test_default_source_is_mcp(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_extractor.FactExtractor") as MockExtractor,
        ):
            instance = MockExtractor.return_value
            from unittest.mock import AsyncMock

            instance.extract_and_store = AsyncMock(return_value=1)
            from navig.mcp_server import memory_remember

            await memory_remember(text="hello")
            _, kwargs = instance.extract_and_store.call_args
            assert kwargs.get("source", "mcp") == "mcp"


# ---------------------------------------------------------------------------
# memory.key_facts.forget
# ---------------------------------------------------------------------------


class TestMemoryForget:
    @pytest.mark.asyncio
    async def test_soft_delete_success(self):
        with patch("navig.mcp_server._memory_store", return_value=_make_store(delete_ok=True)):
            from navig.mcp_server import memory_forget

            result = await memory_forget(fact_id="abc-123")
        assert result == {"deleted": True, "id": "abc-123"}

    @pytest.mark.asyncio
    async def test_soft_delete_not_found(self):
        with patch("navig.mcp_server._memory_store", return_value=_make_store(delete_ok=False)):
            from navig.mcp_server import memory_forget

            result = await memory_forget(fact_id="missing")
        assert result["deleted"] is False


# ---------------------------------------------------------------------------
# memory.key_facts.stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    @pytest.mark.asyncio
    async def test_returns_store_stats(self):
        expected = {"total": 42, "active": 38, "deleted": 4}
        with patch(
            "navig.mcp_server._memory_store",
            return_value=_make_store(stats_data=expected),
        ):
            from navig.mcp_server import memory_stats

            result = await memory_stats()
        assert result == expected

    @pytest.mark.asyncio
    async def test_empty_store_stats(self):
        expected = {"total": 0, "active": 0, "deleted": 0}
        with patch(
            "navig.mcp_server._memory_store",
            return_value=_make_store(stats_data=expected),
        ):
            from navig.mcp_server import memory_stats

            result = await memory_stats()
        assert result["total"] == 0
        assert result["active"] == 0


# ---------------------------------------------------------------------------
# Legacy integration-style helpers kept for reference (not collected by pytest)
# ---------------------------------------------------------------------------


def _call_legacy(handler, tool: str, arguments: dict) -> dict:
    """Invoke a tool through the MCP protocol handler and return the parsed result."""
    import json

    response = handler._handle_tools_call({"name": tool, "arguments": arguments})
    text = response["content"][0]["text"]
    return json.loads(text)
