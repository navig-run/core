"""Batch 76 — tools/trust_boundary, tools/pdf_tool, tools/web_fetch."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.tools.trust_boundary — pure functions
# ---------------------------------------------------------------------------

class TestWrapExternal:
    def test_wraps_content(self):
        from navig.tools.trust_boundary import wrap_external
        result = wrap_external("hello world", source="https://example.com")
        assert "[EXTERNAL CONTENT: https://example.com]" in result
        assert "hello world" in result
        assert "[/EXTERNAL CONTENT]" in result

    def test_idempotent(self):
        from navig.tools.trust_boundary import wrap_external
        once = wrap_external("text", source="src")
        twice = wrap_external(once, source="src")
        assert once == twice

    def test_default_source_unknown(self):
        from navig.tools.trust_boundary import wrap_external
        result = wrap_external("content")
        assert "unknown" in result

    def test_sanitises_brackets_in_source(self):
        from navig.tools.trust_boundary import wrap_external
        result = wrap_external("content", source="[injection]")
        assert "[injection]" not in result
        assert "(injection)" in result


class TestIsExternallyWrapped:
    def test_true_for_wrapped(self):
        from navig.tools.trust_boundary import wrap_external, is_externally_wrapped
        wrapped = wrap_external("data", source="src")
        assert is_externally_wrapped(wrapped) is True

    def test_false_for_plain(self):
        from navig.tools.trust_boundary import is_externally_wrapped
        assert is_externally_wrapped("plain text") is False

    def test_false_for_empty(self):
        from navig.tools.trust_boundary import is_externally_wrapped
        assert is_externally_wrapped("") is False


class TestUnwrapExternal:
    def test_unwraps_correctly(self):
        from navig.tools.trust_boundary import wrap_external, unwrap_external
        wrapped = wrap_external("inner content", source="https://test.com")
        inner = unwrap_external(wrapped)
        assert inner == "inner content"

    def test_raises_for_unwrapped(self):
        from navig.tools.trust_boundary import unwrap_external, TrustBoundaryError
        with pytest.raises(TrustBoundaryError):
            unwrap_external("not wrapped")

    def test_multiline_roundtrip(self):
        from navig.tools.trust_boundary import wrap_external, unwrap_external
        original = "line one\nline two\nline three"
        wrapped = wrap_external(original, source="test")
        assert unwrap_external(wrapped) == original


class TestExtractSource:
    def test_extracts_url(self):
        from navig.tools.trust_boundary import wrap_external, extract_source
        wrapped = wrap_external("data", source="https://example.com")
        assert extract_source(wrapped) == "https://example.com"

    def test_returns_none_for_unwrapped(self):
        from navig.tools.trust_boundary import extract_source
        assert extract_source("plain text") is None


# ---------------------------------------------------------------------------
# navig.tools.pdf_tool — PdfTool
# ---------------------------------------------------------------------------

class TestPdfTool:
    def _make_tool(self):
        from navig.tools.pdf_tool import PdfTool
        return PdfTool()

    def test_name(self):
        assert self._make_tool().name == "pdf_tool"

    def test_description_not_empty(self):
        tool = self._make_tool()
        assert tool.description

    def test_parameters_has_path(self):
        tool = self._make_tool()
        names = [p["name"] for p in tool.parameters]
        assert "path" in names

    def test_run_missing_path_returns_failure(self):
        tool = self._make_tool()
        result = asyncio.run(tool.run({}))
        assert result.success is False
        assert "path" in (result.error or "").lower()

    def test_run_with_path_returns_success(self):
        tool = self._make_tool()
        result = asyncio.run(tool.run({"path": "/some/file.pdf"}))
        assert result.success is True
        assert result.output is not None

    def test_run_output_contains_text(self):
        tool = self._make_tool()
        result = asyncio.run(tool.run({"path": "/test.pdf"}))
        assert "text" in result.output

    def test_run_elapsed_ms_positive(self):
        tool = self._make_tool()
        result = asyncio.run(tool.run({"path": "/test.pdf"}))
        assert result.elapsed_ms >= 0

    def test_run_with_on_status_callback(self):
        tool = self._make_tool()
        calls = []
        async def on_status(event, msg, pct):
            calls.append((event, pct))
        result = asyncio.run(tool.run({"path": "/test.pdf"}, on_status=on_status))
        assert result.success is True
        assert len(calls) > 0


# ---------------------------------------------------------------------------
# navig.tools.web_fetch — WebFetchTool
# ---------------------------------------------------------------------------

class TestWebFetchTool:
    def _make_tool(self):
        from navig.tools.web_fetch import WebFetchTool
        return WebFetchTool()

    def test_name(self):
        assert self._make_tool().name == "web_fetch"

    def test_run_missing_url_returns_failure(self):
        tool = self._make_tool()
        result = asyncio.run(tool.run({}))
        assert result.success is False

    def test_url_without_scheme_gets_https(self):
        from navig.tools.web import web_fetch as _wf
        tool = self._make_tool()
        captured = []

        def fake_fetch(url, **kwargs):
            captured.append(url)
            r = MagicMock()
            r.success = True
            r.text = "page content"
            r.status_code = 200
            r.final_url = url
            r.cached = False
            r.error = None
            return r

        with patch("navig.tools.web_fetch.web_fetch", side_effect=fake_fetch):
            result = asyncio.run(tool.run({"url": "example.com"}))

        assert captured[0].startswith("https://")
        assert result.success is True

    def test_successful_fetch_returns_content(self):
        tool = self._make_tool()
        fake_result = MagicMock(
            success=True, text="page text", status_code=200,
            final_url="https://example.com", cached=False, error=None
        )
        with patch("navig.tools.web_fetch.web_fetch", return_value=fake_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is True
        assert result.output["content"] == "page text"

    def test_failed_fetch_returns_failure(self):
        tool = self._make_tool()
        fake_result = MagicMock(success=False, error="timeout", text=None, status_code=None)
        with patch("navig.tools.web_fetch.web_fetch", return_value=fake_result):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is False

    def test_exception_returns_failure(self):
        tool = self._make_tool()
        with patch("navig.tools.web_fetch.web_fetch", side_effect=RuntimeError("network error")):
            result = asyncio.run(tool.run({"url": "https://example.com"}))
        assert result.success is False
        assert "network error" in (result.error or "")
