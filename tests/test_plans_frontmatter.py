"""Tests for navig.plans.frontmatter — FRONTMATTER_RE, parse_frontmatter, render_frontmatter, etc."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.frontmatter import (
    FRONTMATTER_RE,
    _safe_read,
    first_h1,
    parse_frontmatter,
    parse_frontmatter_with_body,
    render_frontmatter,
)


# ---------------------------------------------------------------------------
# FRONTMATTER_RE
# ---------------------------------------------------------------------------

class TestFrontmatterRe:
    def test_matches_valid_frontmatter(self):
        text = "---\ntitle: Hello\n---\nbody"
        assert FRONTMATTER_RE.match(text) is not None

    def test_no_match_plain_text(self):
        assert FRONTMATTER_RE.match("just plain text") is None

    def test_no_match_incomplete_fence(self):
        assert FRONTMATTER_RE.match("---\ntitle: test\n") is None

    def test_captures_inner_block(self):
        text = "---\ntitle: My Title\nstatus: active\n---\n"
        m = FRONTMATTER_RE.match(text)
        assert m is not None
        assert "title: My Title" in m.group(1)


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_empty_text_returns_empty_dict(self):
        assert parse_frontmatter("") == {}

    def test_no_frontmatter_returns_empty_dict(self):
        assert parse_frontmatter("# Just a heading\n\nSome text.") == {}

    def test_basic_frontmatter(self):
        text = "---\ntitle: Hello World\n---\n"
        result = parse_frontmatter(text)
        assert result == {"title": "Hello World"}

    def test_multiple_keys(self):
        text = "---\ntitle: My Plan\nstatus: active\nphase: 1\n---\n"
        result = parse_frontmatter(text)
        assert result["title"] == "My Plan"
        assert result["status"] == "active"
        assert result["phase"] == "1"

    def test_value_with_colon(self):
        text = "---\nurl: https://example.com\n---\n"
        result = parse_frontmatter(text)
        assert result["url"] == "https://example.com"

    def test_whitespace_stripped_from_keys(self):
        text = "---\n  title  : spaced key  \n---\n"
        result = parse_frontmatter(text)
        assert "title" in result

    def test_line_without_colon_skipped(self):
        text = "---\ntitle: Good\njust-a-line-no-colon\nstatus: ok\n---\n"
        result = parse_frontmatter(text)
        assert "title" in result
        assert "status" in result
        # no key for the colon-free line
        assert "just-a-line-no-colon" not in result

    def test_body_after_frontmatter_ignored(self):
        text = "---\ntitle: T\n---\nbody: not parsed"
        result = parse_frontmatter(text)
        assert "body" not in result

    def test_empty_frontmatter_block(self):
        text = "---\n\n---\n"
        result = parse_frontmatter(text)
        assert result == {}


# ---------------------------------------------------------------------------
# parse_frontmatter_with_body
# ---------------------------------------------------------------------------

class TestParseFrontmatterWithBody:
    def test_returns_tuple_of_two(self):
        text = "---\ntitle: T\n---\nbody here"
        result = parse_frontmatter_with_body(text)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_body_extracted(self):
        text = "---\ntitle: T\n---\nbody here"
        fm, body = parse_frontmatter_with_body(text)
        assert "body here" in body

    def test_frontmatter_extracted(self):
        text = "---\ntitle: My Title\n---\nbody"
        fm, _ = parse_frontmatter_with_body(text)
        assert fm["title"] == "My Title"

    def test_no_frontmatter_returns_full_text_as_body(self):
        text = "No frontmatter here"
        fm, body = parse_frontmatter_with_body(text)
        assert fm == {}
        assert body == text

    def test_body_empty_when_only_frontmatter(self):
        text = "---\ntitle: T\n---\n"
        _, body = parse_frontmatter_with_body(text)
        assert body == "" or body == "\n"

    def test_multiline_body(self):
        text = "---\ntitle: Plan\n---\nLine 1\nLine 2\nLine 3"
        _, body = parse_frontmatter_with_body(text)
        assert "Line 1" in body
        assert "Line 2" in body

    def test_roundtrip_body_not_in_frontmatter(self):
        text = "---\nkey: val\n---\nbody content"
        fm, body = parse_frontmatter_with_body(text)
        assert "body content" not in str(fm)


# ---------------------------------------------------------------------------
# render_frontmatter
# ---------------------------------------------------------------------------

class TestRenderFrontmatter:
    def test_renders_single_key(self):
        rendered = render_frontmatter({"title": "Hello"})
        assert "title: Hello" in rendered

    def test_starts_with_dashes(self):
        rendered = render_frontmatter({"a": "b"})
        assert rendered.startswith("---")

    def test_ends_with_dashes_newline(self):
        rendered = render_frontmatter({"a": "b"})
        assert rendered.endswith("---\n")

    def test_multiple_keys(self):
        rendered = render_frontmatter({"title": "T", "status": "active"})
        assert "title: T" in rendered
        assert "status: active" in rendered

    def test_empty_dict_renders_fence(self):
        rendered = render_frontmatter({})
        assert "---" in rendered

    def test_parse_roundtrip(self):
        original = {"title": "My Plan", "status": "active", "phase": "2"}
        rendered = render_frontmatter(original) + "\nbody"
        parsed = parse_frontmatter(rendered)
        assert parsed["title"] == "My Plan"
        assert parsed["status"] == "active"

    def test_no_trailing_blank_line(self):
        rendered = render_frontmatter({"key": "val"})
        # ends with ---\n, no extra \n
        lines = rendered.split("\n")
        assert lines[-1] == "" and lines[-2] == "---"


# ---------------------------------------------------------------------------
# first_h1
# ---------------------------------------------------------------------------

class TestFirstH1:
    def test_extracts_h1(self):
        text = "# My Title\nSome body text"
        assert first_h1(text) == "My Title"

    def test_empty_text(self):
        assert first_h1("") == ""

    def test_no_h1(self):
        text = "## Sub heading\nsome text"
        assert first_h1(text) == ""

    def test_h2_not_matched(self):
        text = "## Not H1\n# Actual H1"
        assert first_h1(text) == "Actual H1"

    def test_strips_whitespace(self):
        text = "#   Spaced Title  "
        assert first_h1(text) == "Spaced Title"

    def test_returns_only_first(self):
        text = "# First\n# Second"
        assert first_h1(text) == "First"

    def test_h1_without_space_not_matched(self):
        # "#NoSpace" should not match "# Heading" (must have space)
        text = "#NoSpace"
        assert first_h1(text) == ""

    def test_frontmatter_body(self):
        text = "---\ntitle: T\n---\n# Real Heading\nbody"
        assert first_h1(text) == "Real Heading"


# ---------------------------------------------------------------------------
# _safe_read
# ---------------------------------------------------------------------------

class TestSafeRead:
    def test_reads_existing_file(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text("hello world", encoding="utf-8")
        assert _safe_read(p) == "hello world"

    def test_returns_empty_on_missing(self, tmp_path):
        p = tmp_path / "nonexistent.md"
        assert _safe_read(p) == ""

    def test_returns_empty_on_directory(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        assert _safe_read(d) == ""

    def test_reads_utf8_content(self, tmp_path):
        p = tmp_path / "utf8.md"
        p.write_text("héllo wörld", encoding="utf-8")
        result = _safe_read(p)
        assert "llo" in result

    def test_returns_str(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("data", encoding="utf-8")
        assert isinstance(_safe_read(p), str)
