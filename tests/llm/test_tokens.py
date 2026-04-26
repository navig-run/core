"""Tests for navig.core.tokens — estimate_tokens utility."""

from __future__ import annotations

import pytest

from navig.core.tokens import estimate_tokens


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_single_char_returns_one(self):
        assert estimate_tokens("x") == 1

    def test_short_word_rounds_up(self):
        # "hi" = 2 chars / 4 = 0.5 → clamped to 1
        assert estimate_tokens("hi") == 1

    def test_exactly_four_chars(self):
        # "abcd" = 4 / 4.0 = 1.0 → 1
        assert estimate_tokens("abcd") == 1

    def test_eight_chars(self):
        assert estimate_tokens("abcdefgh") == 2

    def test_forty_chars(self):
        text = "a" * 40
        assert estimate_tokens(text) == 10

    def test_default_ratio_is_four(self):
        text = "x" * 400
        assert estimate_tokens(text) == 100

    def test_custom_ratio_lower(self):
        text = "a" * 35
        # 35 / 3.5 = 10
        assert estimate_tokens(text, chars_per_token=3.5) == 10

    def test_custom_ratio_higher(self):
        text = "a" * 80
        # 80 / 8.0 = 10
        assert estimate_tokens(text, chars_per_token=8.0) == 10

    def test_non_empty_always_at_least_one(self):
        assert estimate_tokens("a") >= 1

    def test_long_text_proportional(self):
        short = estimate_tokens("a" * 100)
        long = estimate_tokens("a" * 200)
        assert long == 2 * short

    def test_unicode_text(self):
        # Just checking it doesn't raise
        result = estimate_tokens("こんにちは世界")
        assert isinstance(result, int)
        assert result >= 0


class TestEstimateTokensEdge:
    def test_whitespace_only(self):
        result = estimate_tokens("    ")
        assert result >= 1  # 4 chars → 1 token

    def test_newlines(self):
        result = estimate_tokens("\n" * 8)
        assert result == 2
