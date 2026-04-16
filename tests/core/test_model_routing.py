"""Tests for navig.core.model_routing.

Covers:
  - is_simple_turn() — single line, multiline, keywords, URLs, code blocks
  - choose_cheap_model_route() — disabled, missing config, simple, complex
"""

from __future__ import annotations

import pytest

from navig.core.model_routing import (
    _COMPLEX_KEYWORDS,
    choose_cheap_model_route,
    is_simple_turn,
)

_CHEAP_CFG = {
    "enabled": True,
    "max_simple_chars": 160,
    "max_simple_words": 28,
    "cheap_model": {
        "provider": "deepseek",
        "model": "deepseek-chat",
    },
}


# ---------------------------------------------------------------------------
# is_simple_turn
# ---------------------------------------------------------------------------

class TestIsSimpleTurn:
    def test_short_greeting_is_simple(self):
        assert is_simple_turn("hi there") is True

    def test_empty_string_is_not_simple(self):
        assert is_simple_turn("") is False

    def test_over_char_limit_is_not_simple(self):
        long_msg = "a " * 100  # 200 chars
        assert is_simple_turn(long_msg, max_chars=160) is False

    def test_over_word_limit_is_not_simple(self):
        wordy = " ".join(["word"] * 30)
        assert is_simple_turn(wordy, max_words=28) is False

    def test_multiline_is_not_simple(self):
        assert is_simple_turn("line1\nline2\nline3") is False

    def test_code_block_is_not_simple(self):
        assert is_simple_turn("```python\nprint('hi')\n```") is False

    def test_inline_code_is_not_simple(self):
        assert is_simple_turn("what does `ls -la` do?") is False

    def test_url_is_not_simple(self):
        assert is_simple_turn("check out https://example.com") is False

    @pytest.mark.parametrize("keyword", list(_COMPLEX_KEYWORDS)[:10])
    def test_complex_keyword_is_not_simple(self, keyword: str):
        assert is_simple_turn(f"please {keyword} this") is False

    def test_single_newline_is_simple(self):
        # Only one newline — should still be simple
        assert is_simple_turn("hello\nworld") is True

    def test_question_is_simple(self):
        assert is_simple_turn("What time is it?") is True


# ---------------------------------------------------------------------------
# choose_cheap_model_route
# ---------------------------------------------------------------------------

class TestChooseCheapModelRoute:
    def test_returns_none_when_disabled(self):
        cfg = {**_CHEAP_CFG, "enabled": False}
        assert choose_cheap_model_route("hi", cfg) is None

    def test_returns_none_when_config_none(self):
        assert choose_cheap_model_route("hi there", None) is None

    def test_returns_none_when_message_is_complex(self):
        assert choose_cheap_model_route("please debug this traceback", _CHEAP_CFG) is None

    def test_returns_route_for_simple_message(self):
        route = choose_cheap_model_route("good morning", _CHEAP_CFG)
        assert route is not None
        assert route["provider"] == "deepseek"
        assert route["model"] == "deepseek-chat"
        assert route["routing_reason"] == "simple_turn"

    def test_returns_none_when_provider_missing(self):
        cfg = {**_CHEAP_CFG, "cheap_model": {"model": "gpt-4o-mini"}}  # no provider
        assert choose_cheap_model_route("hi", cfg) is None

    def test_returns_none_when_model_missing(self):
        cfg = {**_CHEAP_CFG, "cheap_model": {"provider": "openai"}}  # no model
        assert choose_cheap_model_route("hi", cfg) is None

    def test_truthy_enabled_variants(self):
        for val in (True, "true", "1", "yes"):
            cfg = {**_CHEAP_CFG, "enabled": val}
            route = choose_cheap_model_route("hello", cfg)
            assert route is not None, f"enabled={val!r} should be truthy"

    def test_falsy_enabled_variants(self):
        for val in (False, "false", "0", "no", "off"):
            cfg = {**_CHEAP_CFG, "enabled": val}
            route = choose_cheap_model_route("hello", cfg)
            assert route is None, f"enabled={val!r} should be falsy"

    def test_long_message_uses_primary(self):
        long_msg = "word " * 50
        assert choose_cheap_model_route(long_msg, _CHEAP_CFG) is None
