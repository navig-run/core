"""Tests for navig.tools.web_fetch — WebFetchTool."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.web_fetch import WebFetchTool, _MAX_CHARS
from navig.tools.registry import ToolResult


def _make_web_result(
    success=True,
    text="Hello world content",
    error=None,
    status_code=200,
    final_url=None,
    cached=False,
):
    r = MagicMock()
    r.success = success
    r.text = text
    r.error = error
    r.status_code = status_code
    r.final_url = final_url
    r.cached = cached
    return r


# ---------------------------------------------------------------------------
# Metadata / structure
# ---------------------------------------------------------------------------

class TestWebFetchToolMeta:
    def test_name_is_web_fetch(self):
        tool = WebFetchTool()
        assert tool.name == "web_fetch"

    def test_description_mentions_url(self):
        assert "url" in WebFetchTool.description.lower() or "URL" in WebFetchTool.description

    def test_not_owner_only(self):
        assert WebFetchTool.owner_only is False

    def test_has_url_parameter(self):
        names = [p["name"] for p in WebFetchTool.parameters]
        assert "url" in names

    def test_url_parameter_required(self):
        url_param = next(p for p in WebFetchTool.parameters if p["name"] == "url")
        assert url_param.get("required") is True

    def test_has_css_selector_parameter(self):
        names = [p["name"] for p in WebFetchTool.parameters]
        assert "css_selector" in names

    def test_max_chars_constant(self):
        assert _MAX_CHARS == 5_000


# ---------------------------------------------------------------------------
# run() — successful fetch
# ---------------------------------------------------------------------------

class TestWebFetchToolRun:
    def test_success_returns_success_true(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="page text")
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is True

    def test_success_output_has_content(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="page content here")
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.output["content"] == "page content here"

    def test_success_output_has_url(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(
            text="content", final_url="https://example.com/final"
        )
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.output["url"] == "https://example.com/final"

    def test_success_falls_back_to_input_url_when_no_final(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c", final_url=None)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.output["url"] == "https://example.com"

    def test_success_output_has_status_code(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c", status_code=200)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.output["status_code"] == 200

    def test_success_output_has_cached(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c", cached=True)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.output["cached"] is True

    def test_text_truncated_to_max_chars(self):
        tool = WebFetchTool()
        long_text = "x" * (_MAX_CHARS + 100)
        mock_result = _make_web_result(text=long_text)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert len(result.output["content"]) <= _MAX_CHARS

    def test_calls_web_fetch_with_max_chars(self):
        tool = WebFetchTool()
        mock_result = _make_web_result()
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result) as mock_wf:
            asyncio.run(tool.run({"url": "https://example.com"}))
        mock_wf.assert_called_once()
        _, kwargs = mock_wf.call_args
        assert kwargs["max_chars"] == _MAX_CHARS

    def test_calls_web_fetch_with_markdown_extract_mode(self):
        tool = WebFetchTool()
        mock_result = _make_web_result()
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result) as mock_wf:
            asyncio.run(tool.run({"url": "https://example.com"}))
        _, kwargs = mock_wf.call_args
        assert kwargs["extract_mode"] == "markdown"


# ---------------------------------------------------------------------------
# run() — error cases
# ---------------------------------------------------------------------------

class TestWebFetchToolRunErrors:
    def test_empty_url_returns_failure(self):
        tool = WebFetchTool()
        result = asyncio.run(tool.run({"url": ""}))
        assert result.success is False
        assert "url" in result.error.lower()

    def test_missing_url_key_returns_failure(self):
        tool = WebFetchTool()
        result = asyncio.run(tool.run({}))
        assert result.success is False

    def test_url_without_scheme_prepends_https(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c")
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result) as mock_wf:
            asyncio.run(tool.run({"url": "example.com"}))
        _, kwargs = mock_wf.call_args
        assert kwargs["url"].startswith("https://")

    def test_failed_web_fetch_returns_failure(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(success=False, error="timeout")
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is False
        assert result.error == "timeout"

    def test_exception_in_fetch_returns_failure(self):
        tool = WebFetchTool()
        with patch(
            "navig.tools.web_fetch.web_fetch", side_effect=RuntimeError("network error")
        ):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is False
        assert "network error" in result.error

    def test_failed_fetch_with_no_error_uses_default_message(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(success=False, error=None)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is False

    def test_none_text_handled_gracefully(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(success=True, text=None)
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is True
        assert result.output["content"] == ""


# ---------------------------------------------------------------------------
# on_status callback
# ---------------------------------------------------------------------------

class TestWebFetchToolStatusCallback:
    def test_status_callback_called(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c")
        statuses = []

        async def on_status(msg, detail, pct):
            statuses.append((msg, pct))

        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            asyncio.run(tool.run({"url": "https://example.com"}, on_status=on_status))

        assert len(statuses) >= 1

    def test_no_callback_no_error(self):
        tool = WebFetchTool()
        mock_result = _make_web_result(text="c")
        with patch("navig.tools.web_fetch.web_fetch", return_value=mock_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}, on_status=None))
        assert result.success is True
