"""Tests for navig.tools.web_fetch — WebFetchTool."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import navig.tools.web_fetch as wf_mod
from navig.tools.web_fetch import WebFetchTool


def _tool() -> WebFetchTool:
    return WebFetchTool()


def _mock_fetch_result(success=True, text="hello world", status_code=200, final_url=None, cached=False, error=None):
    r = MagicMock()
    r.success = success
    r.text = text
    r.status_code = status_code
    r.final_url = final_url
    r.cached = cached
    r.error = error
    return r


class TestWebFetchToolMeta:
    def test_name(self) -> None:
        assert _tool().name == "web_fetch"

    def test_description_not_empty(self) -> None:
        assert _tool().description

    def test_has_url_parameter(self) -> None:
        names = [p["name"] for p in _tool().parameters]
        assert "url" in names

    def test_url_required(self) -> None:
        params = {p["name"]: p for p in _tool().parameters}
        assert params["url"]["required"] is True


class TestWebFetchToolRun:
    @pytest.mark.asyncio
    async def test_missing_url_returns_failure(self) -> None:
        result = await _tool().run({})
        assert result.success is False
        assert "url" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_success_returns_ok(self) -> None:
        mock_result = _mock_fetch_result(text="page content")
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://example.com"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_output_contains_url(self) -> None:
        mock_result = _mock_fetch_result(text="content", final_url="https://example.com")
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://example.com"})
        assert "url" in result.output

    @pytest.mark.asyncio
    async def test_output_contains_content(self) -> None:
        mock_result = _mock_fetch_result(text="extracted text")
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://example.com"})
        assert result.output["content"] == "extracted text"

    @pytest.mark.asyncio
    async def test_url_prefixed_with_https(self) -> None:
        mock_result = _mock_fetch_result(text="content")
        captured = {}
        def fake_fetch(url, **kwargs):
            captured["url"] = url
            return mock_result
        with patch.object(wf_mod, "web_fetch", side_effect=fake_fetch):
            await _tool().run({"url": "example.com"})
        assert captured["url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_error(self) -> None:
        mock_result = _mock_fetch_result(success=False, error="connection refused")
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://fail.com"})
        assert result.success is False
        assert "connection refused" in (result.error or "")

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        with patch.object(wf_mod, "web_fetch", side_effect=RuntimeError("boom")):
            result = await _tool().run({"url": "https://example.com"})
        assert result.success is False
        assert "boom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_name_in_result(self) -> None:
        mock_result = _mock_fetch_result(text="ok")
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://example.com"})
        assert result.name == "web_fetch"

    @pytest.mark.asyncio
    async def test_content_truncated_to_max_chars(self) -> None:
        long_text = "x" * 10_000
        mock_result = _mock_fetch_result(text=long_text)
        with patch.object(wf_mod, "web_fetch", return_value=mock_result):
            result = await _tool().run({"url": "https://example.com"})
        assert len(result.output["content"]) <= 5_000
