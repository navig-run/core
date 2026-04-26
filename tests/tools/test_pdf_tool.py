"""Tests for navig.tools.pdf_tool.PdfTool."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.tools.pdf_tool import PdfTool


class TestPdfToolMeta:
    def test_name(self) -> None:
        assert PdfTool.name == "pdf_tool"

    def test_description_non_empty(self) -> None:
        assert PdfTool.description

    def test_parameters_list(self) -> None:
        names = [p["name"] for p in PdfTool.parameters]
        assert "path" in names
        assert "max_pages" in names

    def test_path_parameter_required(self) -> None:
        path_param = next(p for p in PdfTool.parameters if p["name"] == "path")
        assert path_param["required"] is True


class TestPdfToolRun:
    @pytest.fixture
    def tool(self) -> PdfTool:
        return PdfTool()

    async def test_returns_failure_on_missing_path(self, tool: PdfTool) -> None:
        result = await tool.run({})
        assert result.success is False
        assert "path" in result.error.lower()

    async def test_returns_success_with_valid_path(self, tool: PdfTool) -> None:
        result = await tool.run({"path": "/tmp/sample.pdf"})
        assert result.success is True
        assert result.output is not None

    async def test_output_contains_text_key(self, tool: PdfTool) -> None:
        result = await tool.run({"path": "/tmp/sample.pdf"})
        assert "text" in result.output

    async def test_output_contains_metadata(self, tool: PdfTool) -> None:
        result = await tool.run({"path": "/tmp/sample.pdf"})
        assert "metadata" in result.output

    async def test_elapsed_ms_populated(self, tool: PdfTool) -> None:
        result = await tool.run({"path": "/tmp/sample.pdf"})
        assert result.elapsed_ms is not None
        assert result.elapsed_ms >= 0

    async def test_calls_on_status_when_provided(self, tool: PdfTool) -> None:
        on_status = AsyncMock()
        await tool.run({"path": "/tmp/sample.pdf"}, on_status=on_status)
        on_status.assert_called_once()

    async def test_skips_on_status_when_none(self, tool: PdfTool) -> None:
        # Should not raise when on_status is None
        result = await tool.run({"path": "/tmp/sample.pdf"}, on_status=None)
        assert result.success is True

    async def test_name_in_result(self, tool: PdfTool) -> None:
        result = await tool.run({"path": "/tmp/sample.pdf"})
        assert result.name == "pdf_tool"
