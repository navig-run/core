"""
Tests for navig.gateway.channels.telegram_formatter

Covers:
  - Heading conversion at all 4 levels
  - Horizontal rule conversion
  - Blockquote conversion
  - Bold/italic conversion
  - Numbered list (emoji, plain, roman)
  - Bullet list
  - Code fence passthrough (no transformation inside fences)
  - convert_chunked splits correctly
"""
from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_formatter import (
    FormatterPrefs,
    MarkdownFormatter,
    NUMBERED_STYLE_EMOJI,
    NUMBERED_STYLE_PLAIN,
    NUMBERED_STYLE_ROMAN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fmt():
    return MarkdownFormatter()


@pytest.fixture()
def prefs():
    return FormatterPrefs()


# ---------------------------------------------------------------------------
# Heading tests
# ---------------------------------------------------------------------------

def test_h1_uppercased(fmt, prefs):
    out = fmt.convert("# Hello World", prefs)
    assert "■" in out
    assert "HELLO WORLD" in out


def test_h2_not_uppercased(fmt, prefs):
    out = fmt.convert("## Section Two", prefs)
    assert out.strip() == "◼ Section Two"


def test_h3(fmt, prefs):
    out = fmt.convert("### Sub-section", prefs)
    assert "▪️" in out
    assert "Sub-section" in out


def test_h4(fmt, prefs):
    out = fmt.convert("#### Detail", prefs)
    assert "▫️" in out
    assert "Detail" in out


def test_h4_does_not_match_h3_pattern(fmt, prefs):
    """H4 must be processed before H3 to avoid double-substitution."""
    out = fmt.convert("#### Fourth Level", prefs)
    assert out.count("▪️") == 0  # h3 symbol must NOT appear
    assert "▫️" in out


# ---------------------------------------------------------------------------
# Horizontal rule
# ---------------------------------------------------------------------------

def test_hr_dashes(fmt, prefs):
    out = fmt.convert("---", prefs)
    assert "─" in out
    assert "---" not in out


def test_hr_asterisks(fmt, prefs):
    out = fmt.convert("***", prefs)
    assert "─" in out


# ---------------------------------------------------------------------------
# Blockquote
# ---------------------------------------------------------------------------

def test_blockquote(fmt, prefs):
    out = fmt.convert("> A wise saying", prefs)
    assert out.strip() == "❝ A wise saying"


# ---------------------------------------------------------------------------
# Bold / italic
# ---------------------------------------------------------------------------

def test_bold_converts(fmt, prefs):
    out = fmt.convert("This is **important** text.", prefs)
    assert "*important*" in out
    assert "**important**" not in out


def test_italic_underscore_passthrough(fmt, prefs):
    # _italic_ should remain as _italic_ (Telegram-compatible)
    out = fmt.convert("_italic_ text", prefs)
    assert "_italic_" in out


# ---------------------------------------------------------------------------
# Numbered lists
# ---------------------------------------------------------------------------

def test_numbered_list_emoji(fmt, prefs):
    prefs.numbered_style = NUMBERED_STYLE_EMOJI
    out = fmt.convert("1. First\n2. Second\n3. Third", prefs)
    assert "1️⃣" in out
    assert "2️⃣" in out
    assert "3️⃣" in out


def test_numbered_list_plain(fmt, prefs):
    prefs.numbered_style = NUMBERED_STYLE_PLAIN
    out = fmt.convert("1. Alpha\n2. Beta", prefs)
    assert "1. Alpha" in out
    assert "2. Beta" in out


def test_numbered_list_roman(fmt, prefs):
    prefs.numbered_style = NUMBERED_STYLE_ROMAN
    out = fmt.convert("1. First\n2. Second\n4. Fourth", prefs)
    assert "i. First" in out
    assert "ii. Second" in out
    assert "iv. Fourth" in out


# ---------------------------------------------------------------------------
# Bullet lists
# ---------------------------------------------------------------------------

def test_bullet_dash(fmt, prefs):
    out = fmt.convert("- item one\n- item two", prefs)
    assert "• item one" in out
    assert "• item two" in out


def test_bullet_asterisk(fmt, prefs):
    out = fmt.convert("* item a\n* item b", prefs)
    assert "• item a" in out
    assert "• item b" in out


# ---------------------------------------------------------------------------
# Code fence passthrough
# ---------------------------------------------------------------------------

def test_code_fence_not_transformed(fmt, prefs):
    code = "```python\n# This is a comment\n**not bold**\n- not a bullet\n```"
    out = fmt.convert(code, prefs)
    assert "**not bold**" in out    # must be preserved as-is
    assert "- not a bullet" in out  # must NOT be converted to bullet


def test_code_fence_heading_inside_not_transformed(fmt, prefs):
    code = "```\n# not a heading\n## also not\n```"
    out = fmt.convert(code, prefs)
    assert "# not a heading" in out
    assert "■" not in out


# ---------------------------------------------------------------------------
# convert_chunked
# ---------------------------------------------------------------------------

def test_convert_chunked_short_text_single_chunk(fmt, prefs):
    chunks = fmt.convert_chunked("# Hello\n\n- item", prefs, max_chars=4096)
    assert len(chunks) == 1


def test_convert_chunked_splits_long_text(fmt, prefs):
    # 10 paragraphs × 500 chars each > 4096
    para = "A" * 490
    text = "\n\n".join(para for _ in range(10))
    chunks = fmt.convert_chunked(text, prefs, max_chars=2000)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 2000


# ---------------------------------------------------------------------------
# FormatterPrefs JSON round-trip
# ---------------------------------------------------------------------------

def test_prefs_json_roundtrip():
    prefs = FormatterPrefs(h1_symbol="🔶", bullet_style="▸", numbered_style=NUMBERED_STYLE_ROMAN)
    restored = FormatterPrefs.from_json(prefs.to_json())
    assert restored.h1_symbol == "🔶"
    assert restored.bullet_style == "▸"
    assert restored.numbered_style == NUMBERED_STYLE_ROMAN


def test_prefs_from_json_bad_data_returns_defaults():
    prefs = FormatterPrefs.from_json('{"invalid_key": true}')
    assert prefs.h1_symbol == "■"
