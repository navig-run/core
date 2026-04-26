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
    def test_adds_open_tag(self):
        result = wrap_external("hello world", source="https://example.com")
        assert result.startswith("[EXTERNAL CONTENT: https://example.com]")

    def test_adds_close_tag(self):
        result = wrap_external("hello", source="site")
        assert result.strip().endswith("[/EXTERNAL CONTENT]")

    def test_content_preserved(self):
        result = wrap_external("my content", source="src")
        assert "my content" in result

    def test_idempotent_already_wrapped(self):
        once = wrap_external("data", source="x")
        twice = wrap_external(once, source="x")
        assert once == twice

    def test_default_source_is_unknown(self):
        result = wrap_external("data")
        assert "unknown" in result

    def test_sanitises_brackets_in_source(self):
        result = wrap_external("data", source="[evil]")
        # brackets replaced with parens
        assert "[" not in result.split("\n")[0][len("[EXTERNAL CONTENT: "):]


class TestIsExternallyWrapped:
    def test_false_for_plain_text(self):
        assert is_externally_wrapped("just text") is False

    def test_true_for_wrapped_content(self):
        wrapped = wrap_external("content", source="src")
        assert is_externally_wrapped(wrapped) is True

    def test_false_for_empty_string(self):
        assert is_externally_wrapped("") is False

    def test_false_for_partial_tag(self):
        assert is_externally_wrapped("[EXTERNAL CONTENT]") is False


class TestUnwrapExternal:
    def test_returns_inner_content(self):
        wrapped = wrap_external("inner text", source="src")
        assert unwrap_external(wrapped) == "inner text"

    def test_raises_on_unwrapped_content(self):
        with pytest.raises(TrustBoundaryError):
            unwrap_external("not wrapped")

    def test_multiline_content(self):
        content = "line 1\nline 2\nline 3"
        wrapped = wrap_external(content, source="src")
        assert unwrap_external(wrapped) == content

    def test_roundtrip(self):
        original = "sensitive data"
        assert unwrap_external(wrap_external(original, source="s")) == original


class TestExtractSource:
    def test_returns_source_label(self):
        wrapped = wrap_external("data", source="https://example.com")
        assert extract_source(wrapped) == "https://example.com"

    def test_returns_none_for_unwrapped(self):
        assert extract_source("plain text") is None

    def test_sanitised_source_extracted_correctly(self):
        wrapped = wrap_external("data", source="[bad]")
        src = extract_source(wrapped)
        # brackets were sanitised to parens
        assert src == "(bad)"
