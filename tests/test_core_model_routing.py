"""
Batch 88 — navig/core/model_routing.py
Tests for _coerce_bool, _coerce_int, is_simple_turn, choose_cheap_model_route.
"""
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

    def test_truthy_string(self):
        assert _coerce_bool("1") is True

    def test_false_string(self):
        assert _coerce_bool("false") is False

    def test_off_string(self):
        assert _coerce_bool("off") is False

    def test_empty_string(self):
        assert _coerce_bool("") is False

    def test_yes_string(self):
        assert _coerce_bool("yes") is True

    def test_int_nonzero(self):
        assert _coerce_bool(1) is True

    def test_int_zero(self):
        assert _coerce_bool(0) is False

    def test_none_uses_default(self):
        assert _coerce_bool(None, default=True) is True
        assert _coerce_bool(None, default=False) is False


# ---------------------------------------------------------------------------
# _coerce_int
# ---------------------------------------------------------------------------


class TestCoerceInt:
    def test_int_passthrough(self):
        assert _coerce_int(42, default=0) == 42

    def test_string_int(self):
        assert _coerce_int("100", default=0) == 100

    def test_none_uses_default(self):
        assert _coerce_int(None, default=99) == 99

    def test_invalid_string_uses_default(self):
        assert _coerce_int("bad", default=-1) == -1

    def test_float_coerced(self):
        assert _coerce_int(3.7, default=0) == 3


# ---------------------------------------------------------------------------
# is_simple_turn
# ---------------------------------------------------------------------------


class TestIsSimpleTurn:
    def test_empty_string_false(self):
        assert is_simple_turn("") is False

    def test_short_greeting_true(self):
        assert is_simple_turn("hi") is True

    def test_simple_question_true(self):
        assert is_simple_turn("what version is this?") is True

    def test_exceeds_max_chars_false(self):
        long = "word " * 50  # well over 160 chars
        assert is_simple_turn(long) is False

    def test_exceeds_max_words_false(self):
        many_words = " ".join(["word"] * 30)
        assert is_simple_turn(many_words) is False

    def test_code_block_backticks_false(self):
        assert is_simple_turn("run `echo hello`") is False

    def test_triple_backtick_false(self):
        assert is_simple_turn("```python\nprint('hi')\n```") is False

    def test_url_false(self):
        assert is_simple_turn("see https://example.com") is False

    def test_multiline_false(self):
        assert is_simple_turn("line one\nline two\nline three") is False

    def test_complex_keyword_debug_false(self):
        assert is_simple_turn("debug that error") is False

    def test_complex_keyword_implement_false(self):
        assert is_simple_turn("implement the feature") is False

    def test_complex_keyword_test_false(self):
        assert is_simple_turn("run test") is False

    def test_custom_max_chars(self):
        msg = "hello"
        assert is_simple_turn(msg, max_chars=3) is False
        assert is_simple_turn(msg, max_chars=10) is True

    def test_custom_max_words(self):
        msg = "one two three four five"
        assert is_simple_turn(msg, max_words=3) is False
        assert is_simple_turn(msg, max_words=10) is True


# ---------------------------------------------------------------------------
# choose_cheap_model_route
# ---------------------------------------------------------------------------


_CHEAP_CONFIG = {
    "enabled": True,
    "max_simple_chars": 160,
    "max_simple_words": 28,
    "cheap_model": {
        "provider": "deepseek",
        "model": "deepseek-chat",
    },
}


class TestChooseCheapModelRoute:
    def test_none_config_returns_none(self):
        assert choose_cheap_model_route("hello", None) is None

    def test_disabled_returns_none(self):
        cfg = {**_CHEAP_CONFIG, "enabled": False}
        assert choose_cheap_model_route("hello", cfg) is None

    def test_simple_turn_returns_route(self):
        route = choose_cheap_model_route("hi there", _CHEAP_CONFIG)
        assert route is not None

    def test_route_has_provider(self):
        route = choose_cheap_model_route("hi there", _CHEAP_CONFIG)
        assert route["provider"] == "deepseek"

    def test_route_has_model(self):
        route = choose_cheap_model_route("hi there", _CHEAP_CONFIG)
        assert route["model"] == "deepseek-chat"

    def test_route_has_routing_reason(self):
        route = choose_cheap_model_route("hi there", _CHEAP_CONFIG)
        assert route["routing_reason"] == "simple_turn"

    def test_complex_turn_returns_none(self):
        assert choose_cheap_model_route("implement the new feature", _CHEAP_CONFIG) is None

    def test_missing_provider_returns_none(self):
        cfg = {
            "enabled": True,
            "cheap_model": {"model": "some-model"},  # no provider
        }
        assert choose_cheap_model_route("hi", cfg) is None

    def test_missing_model_returns_none(self):
        cfg = {
            "enabled": True,
            "cheap_model": {"provider": "openai"},  # no model
        }
        assert choose_cheap_model_route("hi", cfg) is None

    def test_empty_config_returns_none(self):
        assert choose_cheap_model_route("hi", {}) is None

    def test_route_provider_lowercased(self):
        cfg = {
            **_CHEAP_CONFIG,
            "cheap_model": {"provider": "DeepSeek", "model": "deepseek-chat"},
        }
        route = choose_cheap_model_route("hi", cfg)
        assert route is not None
        assert route["provider"] == "deepseek"

    def test_long_message_returns_none(self):
        message = "hello " * 50  # too long
        assert choose_cheap_model_route(message, _CHEAP_CONFIG) is None
