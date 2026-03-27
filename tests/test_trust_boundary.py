"""Tests for navig.tools.trust_boundary."""

from __future__ import annotations

import pytest

from navig.tools.trust_boundary import (
    TrustBoundaryError,
    extract_source,
    is_externally_wrapped,
    unwrap_external,
    wrap_external,
)


class TestWrapExternal:
    def test_wraps_content(self):
        result = wrap_external("hello world", source="https://example.com")
        assert result.startswith("[EXTERNAL CONTENT: https://example.com]")
        assert "hello world" in result
        assert result.strip().endswith("[/EXTERNAL CONTENT]")

    def test_default_source(self):
        result = wrap_external("data")
        assert "unknown" in result

    def test_idempotent(self):
        wrapped = wrap_external("data", source="http://x.com")
        double = wrap_external(wrapped, source="http://y.com")
        assert double == wrapped  # second wrap is a no-op

    def test_source_sanitised(self):
        # Square brackets in source must not break sentinel syntax
        result = wrap_external("data", source="evil [injected] source")
        assert "[EXTERNAL CONTENT: evil (injected) source]" in result


class TestIsExternallyWrapped:
    def test_wrapped_returns_true(self):
        assert is_externally_wrapped("[EXTERNAL CONTENT: x]\ndata\n[/EXTERNAL CONTENT]")

    def test_plain_returns_false(self):
        assert not is_externally_wrapped("just some plain text")

    def test_empty_returns_false(self):
        assert not is_externally_wrapped("")


class TestUnwrapExternal:
    def test_round_trip(self):
        original = "line1\nline2\nline3"
        wrapped = wrap_external(original, source="http://a.com")
        recovered = unwrap_external(wrapped)
        assert recovered == original

    def test_raises_on_plain_text(self):
        with pytest.raises(TrustBoundaryError):
            unwrap_external("not wrapped")

    def test_multiline_preserved(self):
        content = "a\nb\nc"
        assert unwrap_external(wrap_external(content)) == content


class TestExtractSource:
    def test_extracts_url(self):
        wrapped = wrap_external("data", source="https://api.example.com")
        assert extract_source(wrapped) == "https://api.example.com"

    def test_returns_none_for_plain(self):
        assert extract_source("plain text") is None
