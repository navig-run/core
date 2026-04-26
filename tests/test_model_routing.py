"""Hermetic unit tests for navig.core.model_routing."""
from __future__ import annotations

import pytest

from navig.core.model_routing import (
    _coerce_bool,
    _coerce_int,
    choose_cheap_model_route,
    is_simple_turn,
)

# ---------------------------------------------------------------------------
# _coerce_bool
# ---------------------------------------------------------------------------


class TestCoerceBool:
    def test_true_bool(self):
        assert _coerce_bool(True) is True

    def test_false_bool(self):
        assert _coerce_bool(False) is False

    def test_string_true(self):
        assert _coerce_bool("true") is True

    def test_string_false(self):
        assert _coerce_bool("false") is False

    def test_string_0(self):
        assert _coerce_bool("0") is False

    def test_string_no(self):
        assert _coerce_bool("no") is False

    def test_string_off(self):
        assert _coerce_bool("off") is False

    def test_string_empty(self):
        assert _coerce_bool("") is False

    def test_string_yes(self):
        assert _coerce_bool("yes") is True

    def test_int_nonzero(self):
        assert _coerce_bool(1) is True

    def test_int_zero(self):
        assert _coerce_bool(0) is False

    def test_none_returns_default_false(self):
        assert _coerce_bool(None) is False

    def test_none_returns_custom_default(self):
        assert _coerce_bool(None, default=True) is True


# ---------------------------------------------------------------------------
# _coerce_int
# ---------------------------------------------------------------------------


class TestCoerceInt:
    def test_integer(self):
        assert _coerce_int(5, default=0) == 5

    def test_string_int(self):
        assert _coerce_int("42", default=0) == 42

    def test_none_returns_default(self):
        assert _coerce_int(None, default=10) == 10

    def test_float_truncated(self):
        assert _coerce_int(3.9, default=0) == 3

    def test_invalid_string_returns_default(self):
        assert _coerce_int("bad", default=7) == 7

    def test_empty_string_returns_default(self):
        assert _coerce_int("", default=5) == 5


# ---------------------------------------------------------------------------
# is_simple_turn
# ---------------------------------------------------------------------------


class TestIsSimpleTurn:
    def test_empty_is_not_simple(self):
        assert is_simple_turn("") is False

    def test_short_plain_is_simple(self):
        assert is_simple_turn("Hello, how are you?") is True

    def test_too_long_chars(self):
        msg = "a " * 100  # 200 chars
        assert is_simple_turn(msg) is False

    def test_too_many_words(self):
        msg = " ".join(["word"] * 30)
        assert is_simple_turn(msg) is False

    def test_multiline_not_simple(self):
        msg = "line one\nline two\nline three"
        assert is_simple_turn(msg) is False

    def test_single_newline_ok(self):
        assert is_simple_turn("hello\nworld") is True

    def test_code_block_not_simple(self):
        assert is_simple_turn("here is some `code`") is False

    def test_triple_backtick_not_simple(self):
        assert is_simple_turn("```python\nprint('hi')\n```") is False

    def test_url_not_simple(self):
        assert is_simple_turn("check https://example.com") is False

    def test_www_url_not_simple(self):
        assert is_simple_turn("visit www.navig.io today") is False

    def test_complex_keyword_debug(self):
        assert is_simple_turn("please debug this issue") is False

    def test_complex_keyword_implement(self):
        assert is_simple_turn("implement the feature") is False

    def test_complex_keyword_refactor(self):
        assert is_simple_turn("refactor the code") is False

    def test_complex_keyword_docker(self):
        assert is_simple_turn("deploy docker container") is False

    def test_simple_greeting(self):
        assert is_simple_turn("What time is it?") is True

    def test_simple_yes_no(self):
        assert is_simple_turn("Sure, go ahead.") is True

    def test_custom_max_chars(self):
        assert is_simple_turn("hello", max_chars=3) is False
        assert is_simple_turn("hi", max_chars=3) is True

    def test_custom_max_words(self):
        assert is_simple_turn("one two three", max_words=2) is False
        assert is_simple_turn("one two", max_words=2) is True

    def test_keyword_in_middle_punctuation(self):
        # "debug" with trailing punctuation should still match
        assert is_simple_turn("Can you debug this?") is False


# ---------------------------------------------------------------------------
# choose_cheap_model_route
# ---------------------------------------------------------------------------


def _cfg(
    enabled: bool = True,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    max_chars: int = 160,
    max_words: int = 28,
) -> dict:
    return {
        "enabled": enabled,
        "cheap_model": {"provider": provider, "model": model},
        "max_simple_chars": max_chars,
        "max_simple_words": max_words,
    }


class TestChooseCheapModelRoute:
    def test_disabled_returns_none(self):
        cfg = _cfg(enabled=False)
        result = choose_cheap_model_route("Hi there", cfg)
        assert result is None

    def test_none_config_returns_none(self):
        result = choose_cheap_model_route("Hi there", None)
        assert result is None

    def test_simple_turn_returns_route(self):
        result = choose_cheap_model_route("Hello there!", _cfg())
        assert result is not None
        assert result["provider"] == "deepseek"
        assert result["model"] == "deepseek-chat"
        assert result["routing_reason"] == "simple_turn"

    def test_complex_turn_returns_none(self):
        result = choose_cheap_model_route("Please refactor this code and add tests.", _cfg())
        assert result is None

    def test_missing_cheap_model_returns_none(self):
        cfg = {"enabled": True, "cheap_model": None}
        result = choose_cheap_model_route("Hello", cfg)
        assert result is None

    def test_empty_provider_returns_none(self):
        cfg = _cfg(provider="", model="some-model")
        result = choose_cheap_model_route("Hello", cfg)
        assert result is None

    def test_empty_model_returns_none(self):
        cfg = _cfg(provider="openai", model="")
        result = choose_cheap_model_route("Hello", cfg)
        assert result is None

    def test_provider_lowercased(self):
        cfg = _cfg(provider="OpenAI", model="gpt-4o-mini")
        result = choose_cheap_model_route("Hi there!", cfg)
        assert result is not None
        assert result["provider"] == "openai"

    def test_custom_max_chars_too_long(self):
        # With tight limit: long message forces no route
        cfg = _cfg(max_chars=5)
        result = choose_cheap_model_route("Hello there!", cfg)
        assert result is None

    def test_enabled_string_true(self):
        cfg = {
            "enabled": "true",
            "cheap_model": {"provider": "gemini", "model": "flash"},
            "max_simple_chars": 160,
            "max_simple_words": 28,
        }
        result = choose_cheap_model_route("Hi!", cfg)
        assert result is not None

    def test_url_in_message_returns_none(self):
        result = choose_cheap_model_route("Check https://example.com", _cfg())
        assert result is None

    def test_route_includes_extra_cheap_model_fields(self):
        cfg = {
            "enabled": True,
            "cheap_model": {"provider": "gemini", "model": "flash", "temperature": 0.3},
        }
        result = choose_cheap_model_route("Quick question?", cfg)
        assert result is not None
        assert result.get("temperature") == 0.3
