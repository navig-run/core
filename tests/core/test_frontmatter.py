"""Tests for navig.plans.frontmatter — YAML frontmatter parse/render utilities."""

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


# ──────────────────────────────────────────────────────────────
# FRONTMATTER_RE
# ──────────────────────────────────────────────────────────────


class TestFrontmatterRe:
    def test_matches_valid_frontmatter(self):
        text = "---\ntitle: Hello\n---\nBody here"
        assert FRONTMATTER_RE.match(text) is not None

    def test_does_not_match_no_frontmatter(self):
        assert FRONTMATTER_RE.match("No frontmatter here") is None

    def test_does_not_match_unclosed_block(self):
        assert FRONTMATTER_RE.match("---\ntitle: Hello\n") is None


# ──────────────────────────────────────────────────────────────
# parse_frontmatter
# ──────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_parses_single_key(self):
        fm = parse_frontmatter("---\ntitle: My Title\n---\nBody")
        assert fm == {"title": "My Title"}

    def test_parses_multiple_keys(self):
        fm = parse_frontmatter("---\ntitle: T\nstatus: done\ndate: 2024-01-01\n---\n")
        assert fm["title"] == "T"
        assert fm["status"] == "done"
        assert fm["date"] == "2024-01-01"

    def test_returns_empty_dict_when_no_frontmatter(self):
        assert parse_frontmatter("No frontmatter") == {}

    def test_returns_empty_dict_for_empty_string(self):
        assert parse_frontmatter("") == {}

    def test_skips_lines_without_colon(self):
        fm = parse_frontmatter("---\njust a note\ntitle: Real\n---\n")
        assert "just a note" not in fm
        assert fm["title"] == "Real"

    def test_value_with_colon_inside_preserved(self):
        fm = parse_frontmatter("---\nurl: https://example.com\n---\n")
        assert fm["url"] == "https://example.com"

    def test_strips_whitespace_from_keys_and_values(self):
        fm = parse_frontmatter("---\n  key  :  value  \n---\n")
        assert fm["key"] == "value"


# ──────────────────────────────────────────────────────────────
# parse_frontmatter_with_body
# ──────────────────────────────────────────────────────────────


class TestParseFrontmatterWithBody:
    def test_returns_tuple(self):
        result = parse_frontmatter_with_body("---\ntitle: T\n---\nbody text")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_parses_frontmatter(self):
        fm, body = parse_frontmatter_with_body("---\ntitle: Hello\n---\nContent")
        assert fm["title"] == "Hello"

    def test_body_correct(self):
        _, body = parse_frontmatter_with_body("---\ntitle: T\n---\nContent here")
        assert body.strip() == "Content here"

    def test_no_frontmatter_returns_empty_dict_and_full_text(self):
        fm, body = parse_frontmatter_with_body("Just plain text")
        assert fm == {}
        assert body == "Just plain text"

    def test_empty_input(self):
        fm, body = parse_frontmatter_with_body("")
        assert fm == {}
        assert body == ""

    def test_multiline_body_preserved(self):
        text = "---\nk: v\n---\nline1\nline2\nline3"
        _, body = parse_frontmatter_with_body(text)
        assert "line1" in body
        assert "line3" in body


# ──────────────────────────────────────────────────────────────
# render_frontmatter
# ──────────────────────────────────────────────────────────────


class TestRenderFrontmatter:
    def test_renders_single_key(self):
        result = render_frontmatter({"title": "Hello"})
        assert "title: Hello" in result
        assert result.startswith("---")

    def test_renders_multiple_keys(self):
        result = render_frontmatter({"a": "1", "b": "2"})
        assert "a: 1" in result
        assert "b: 2" in result

    def test_output_starts_and_ends_with_separator(self):
        result = render_frontmatter({"k": "v"})
        assert result.startswith("---")
        assert "---" in result[3:]  # closing dashes

    def test_empty_dict_renders_empty_block(self):
        result = render_frontmatter({})
        assert result.startswith("---")
        assert result.count("---") >= 2

    def test_roundtrip(self):
        original = {"title": "Test", "status": "active"}
        rendered = render_frontmatter(original)
        parsed = parse_frontmatter(rendered)
        assert parsed["title"] == "Test"
        assert parsed["status"] == "active"


# ──────────────────────────────────────────────────────────────
# first_h1
# ──────────────────────────────────────────────────────────────


class TestFirstH1:
    def test_extracts_h1(self):
        assert first_h1("# My Title\nSome body") == "My Title"

    def test_returns_empty_when_no_h1(self):
        assert first_h1("## H2 only\nno h1 here") == ""

    def test_extracts_first_h1_only(self):
        assert first_h1("# First\n# Second") == "First"

    def test_empty_string(self):
        assert first_h1("") == ""

    def test_strips_extra_whitespace(self):
        assert first_h1("#   Spaced Title   ") == "Spaced Title"

    def test_skips_frontmatter_and_finds_h1(self):
        text = "---\ntitle: fm\n---\n# Real Title\nbody"
        # first_h1 scans all lines, frontmatter lines don't start with "# "
        assert first_h1(text) == "Real Title"


# ──────────────────────────────────────────────────────────────
# _safe_read
# ──────────────────────────────────────────────────────────────


class TestSafeRead:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Hello", encoding="utf-8")
        assert _safe_read(f) == "# Hello"

    def test_returns_empty_for_missing_file(self, tmp_path):
        assert _safe_read(tmp_path / "ghost.md") == ""
