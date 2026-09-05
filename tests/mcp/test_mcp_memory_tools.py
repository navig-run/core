# tests/test_mcp_memory_tools.py
"""Contract tests for MCP memory tool handlers (not storage layer)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

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


def _ranked(fact_id: str, content: str, score: float = 0.9):
    """A RankedFact-shaped stub — the retriever returns wrappers, not raw facts."""
    fact = SimpleNamespace(
        id=fact_id,
        content=content,
        category="preference",
        tags=["ui"],
        confidence=0.8,
        created_at="2026-07-28T00:00:00Z",
    )
    return SimpleNamespace(fact=fact, combined_score=score)


def _result(*ranked):
    """A FactRetrievalResult-shaped stub: facts live on `.facts`, it is NOT iterable."""
    return SimpleNamespace(facts=list(ranked), formatted="", token_estimate=0)


class TestMemoryRetrieve:
    async def test_returns_empty_list_when_no_facts(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            MockRetriever.return_value.retrieve.return_value = _result()
            from navig.mcp_server import memory_retrieve

            result = await memory_retrieve(query="anything")
        assert result == {"facts": []}

    async def test_calls_retrieve_with_its_real_signature(self):
        """`retrieve(query, category=None, max_tokens=None, config_override=None)`.

        This assertion used to read
        ``assert_called_once_with(query="q", limit=5, token_budget=500)`` — neither
        `limit` nor `token_budget` is a parameter of `retrieve`, so it pinned a call
        that raises TypeError against the real object. A plain MagicMock accepts any
        keyword, so the test passed and certified a dead tool.
        """
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            instance = MockRetriever.return_value
            instance.retrieve.return_value = _result()
            from navig.mcp_server import memory_retrieve

            await memory_retrieve(query="q", limit=5, token_budget=500)

        instance.retrieve.assert_called_once_with("q", max_tokens=500)

    async def test_the_asserted_call_is_accepted_by_the_real_retriever(self):
        """Bind the assertion above to the real signature, so a rename fails here."""
        import inspect

        from navig.memory.fact_retriever import FactRetriever

        inspect.signature(FactRetriever.retrieve).bind(
            object(), "q", max_tokens=500
        )  # raises TypeError if the call shape is wrong

    async def test_limit_caps_the_returned_facts(self):
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            MockRetriever.return_value.retrieve.return_value = _result(
                _ranked("a", "one"), _ranked("b", "two"), _ranked("c", "three")
            )
            from navig.mcp_server import memory_retrieve

            result = await memory_retrieve(query="q", limit=2)

        assert [f["id"] for f in result["facts"]] == ["a", "b"]

    async def test_facts_are_serialised_from_the_wrapped_fact(self):
        """`vars(RankedFact)` nests a dataclass and is not JSON-serialisable."""
        import json

        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            MockRetriever.return_value.retrieve.return_value = _result(
                _ranked("a", "prefers dark mode")
            )
            from navig.mcp_server import memory_retrieve

            result = await memory_retrieve(query="q")

        assert result["facts"][0]["content"] == "prefers dark mode"
        assert result["facts"][0]["score"] == 0.9
        json.dumps(result)  # must be serialisable — an MCP tool returns JSON

    async def test_a_result_without_facts_does_not_crash(self):
        """An empty retrieval returns a result whose `.facts` may be falsy."""
        with (
            patch("navig.mcp_server._memory_store", return_value=_make_store()),
            patch("navig.memory.fact_retriever.FactRetriever") as MockRetriever,
        ):
            MockRetriever.return_value.retrieve.return_value = SimpleNamespace(facts=None)
            from navig.mcp_server import memory_retrieve

            assert await memory_retrieve(query="q") == {"facts": []}


# ---------------------------------------------------------------------------
# memory.key_facts.remember
# ---------------------------------------------------------------------------


class TestMemoryRemember:
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
    async def test_soft_delete_success(self):
        with patch("navig.mcp_server._memory_store", return_value=_make_store(delete_ok=True)):
            from navig.mcp_server import memory_forget

            result = await memory_forget(fact_id="abc-123")
        assert result == {"deleted": True, "id": "abc-123"}

    async def test_soft_delete_not_found(self):
        with patch("navig.mcp_server._memory_store", return_value=_make_store(delete_ok=False)):
            from navig.mcp_server import memory_forget

            result = await memory_forget(fact_id="missing")
        assert result["deleted"] is False


# ---------------------------------------------------------------------------
# memory.key_facts.stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    async def test_returns_store_stats(self):
        expected = {"total": 42, "active": 38, "deleted": 4}
        with patch(
            "navig.mcp_server._memory_store",
            return_value=_make_store(stats_data=expected),
        ):
            from navig.mcp_server import memory_stats

            result = await memory_stats()
        assert result == expected

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
