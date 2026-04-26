"""Tests for navig.tools.search.SearchTool."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import navig.tools.search as search_mod
from navig.tools.search import SearchTool


def _ok_web_search(query, **kwargs):
    return SimpleNamespace(
        success=True,
        error=None,
        provider="brave",
        results=[
            SimpleNamespace(title=f"Result {i}", url=f"https://example.com/{i}", snippet=f"Snippet {i}")
            for i in range(3)
        ],
    )


def _fail_web_search(query, **kwargs):
    return SimpleNamespace(success=False, error="service unavailable", provider="brave", results=[])


class TestSearchToolMeta:
    def test_name(self) -> None:
        assert SearchTool.name == "search"

    def test_description_non_empty(self) -> None:
        assert SearchTool.description

    def test_query_parameter_required(self) -> None:
        param = next(p for p in SearchTool.parameters if p["name"] == "query")
        assert param["required"] is True


class TestSearchToolRun:
    @pytest.fixture
    def tool(self) -> SearchTool:
        return SearchTool()

    async def test_missing_query_returns_failure(self, tool: SearchTool) -> None:
        result = await tool.run({})
        assert result.success is False
        assert "query" in result.error.lower()

    async def test_successful_search_returns_results(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=_ok_web_search):
            result = await tool.run({"query": "python tutorial"})
        assert result.success is True
        assert result.output is not None

    async def test_output_contains_query(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=_ok_web_search):
            result = await tool.run({"query": "pytest howto"})
        assert result.output["query"] == "pytest howto"

    async def test_output_contains_provider(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=_ok_web_search):
            result = await tool.run({"query": "test"})
        assert "provider" in result.output

    async def test_output_results_capped_at_five(self, tool: SearchTool) -> None:
        def many_results(query, **kwargs):
            return SimpleNamespace(
                success=True, error=None, provider="brave",
                results=[
                    SimpleNamespace(title=f"T{i}", url=f"https://x/{i}", snippet="...")
                    for i in range(10)
                ],
            )
        with patch.object(search_mod, "web_search", side_effect=many_results):
            result = await tool.run({"query": "many"})
        assert len(result.output["results"]) <= 5

    async def test_failed_search_returns_error(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=_fail_web_search):
            result = await tool.run({"query": "fail this"})
        assert result.success is False

    async def test_exception_in_web_search_returns_failure(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=RuntimeError("boom")):
            result = await tool.run({"query": "explode"})
        assert result.success is False
        assert "boom" in result.error

    async def test_name_in_result(self, tool: SearchTool) -> None:
        with patch.object(search_mod, "web_search", side_effect=_ok_web_search):
            result = await tool.run({"query": "test"})
        assert result.name == "search"
