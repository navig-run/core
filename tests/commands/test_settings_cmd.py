"""Unit tests for commands/settings_cmd.py — _mask, _group_for, _coerce, _badge."""
from __future__ import annotations

import pytest

from navig.commands.settings_cmd import (
    _GROUPS,
    _LAYER_COLORS,
    _SENSITIVE_KEYS,
    _badge,
    _coerce,
    _group_for,
    _mask,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestSettingsCmdConstants:
    def test_sensitive_keys_is_set(self):
        assert isinstance(_SENSITIVE_KEYS, (set, frozenset))

    def test_api_key_sensitive(self):
        assert "navig.ai.api_key" in _SENSITIVE_KEYS

    def test_groups_is_dict(self):
        assert isinstance(_GROUPS, dict)

    def test_layer_colors_has_defaults(self):
        assert "defaults" in _LAYER_COLORS

    def test_layer_colors_has_global(self):
        assert "global" in _LAYER_COLORS


# ---------------------------------------------------------------------------
# _mask
# ---------------------------------------------------------------------------

class TestMask:
    def test_non_sensitive_key_passthrough(self):
        result = _mask("navig.ai.model", "gpt-4")
        assert result == "gpt-4"

    def test_sensitive_key_short_value_no_mask(self):
        # Only masks values with len > 4
        result = _mask("navig.ai.api_key", "abc")
        assert result == "abc"

    def test_sensitive_key_long_value_masked(self):
        result = _mask("navig.ai.api_key", "sk-abcde12345")
        assert result.startswith("sk-a")
        assert "•" in result

    def test_sensitive_key_non_string_passthrough(self):
        # Non-str values pass through as str()
        result = _mask("navig.ai.api_key", 12345)
        assert result == "12345"

    def test_mask_preserves_prefix(self):
        result = _mask("navig.ai.api_key", "ABCDEF")
        assert result.startswith("ABCD")

    def test_mask_max_bullets_twelve(self):
        long_key = "navig.ai.api_key"
        long_val = "X" * 30
        result = _mask(long_key, long_val)
        bullet_count = result.count("•")
        assert bullet_count <= 12


# ---------------------------------------------------------------------------
# _group_for
# ---------------------------------------------------------------------------

class TestGroupFor:
    def test_ai_key_returns_ai_group(self):
        result = _group_for("navig.ai.model")
        assert result == "navig.ai"

    def test_telegram_key(self):
        # navig.telegram is not in _GROUPS, falls back to "navig" prefix
        result = _group_for("navig.telegram.bot_token")
        assert result == "navig"

    def test_exact_prefix_match(self):
        # key exactly equals a prefix
        result = _group_for("navig.ai")
        assert result == "navig.ai"

    def test_unknown_returns_navig(self):
        result = _group_for("totally.unknown.key")
        assert result == "navig"

    def test_navig_prefix(self):
        result = _group_for("navig.some_key")
        assert result == "navig"


# ---------------------------------------------------------------------------
# _coerce
# ---------------------------------------------------------------------------

class TestCoerce:
    # --- bool references ---
    def test_bool_true_variants(self):
        for truthy in ("1", "true", "yes", "on"):
            assert _coerce(truthy, True) is True

    def test_bool_false_variants(self):
        for falsy in ("0", "false", "no", "off"):
            assert _coerce(falsy, True) is False

    def test_bool_case_insensitive(self):
        assert _coerce("TRUE", True) is True

    # --- int references ---
    def test_int_valid(self):
        assert _coerce("42", 0) == 42

    def test_int_invalid_returns_raw(self):
        result = _coerce("notanint", 0)
        assert result == "notanint"

    def test_int_negative(self):
        assert _coerce("-5", 1) == -5

    # --- float references ---
    def test_float_valid(self):
        result = _coerce("3.14", 0.0)
        assert result == pytest.approx(3.14)

    def test_float_invalid_returns_raw(self):
        result = _coerce("abc", 1.5)
        assert result == "abc"

    # --- list references ---
    def test_list_json_array(self):
        result = _coerce('["a", "b"]', [])
        assert result == ["a", "b"]

    def test_list_csv_fallback(self):
        result = _coerce("a, b, c", [])
        assert result == ["a", "b", "c"]

    def test_list_invalid_json_uses_csv(self):
        result = _coerce("x,y,z", [])
        assert result == ["x", "y", "z"]

    def test_list_empty_csv_strips(self):
        result = _coerce(",,,", [])
        assert result == []

    # --- str reference passthrough ---
    def test_str_reference_passthrough(self):
        result = _coerce("hello", "default")
        assert result == "hello"

    def test_none_reference_passthrough(self):
        result = _coerce("anything", None)
        assert result == "anything"


# ---------------------------------------------------------------------------
# _badge
# ---------------------------------------------------------------------------

class TestBadge:
    def test_known_layer_global(self):
        result = _badge("global")
        assert "global" in result

    def test_known_layer_defaults(self):
        result = _badge("defaults")
        assert "defaults" in result

    def test_unknown_layer_uses_dim(self):
        result = _badge("unknown_layer")
        assert "unknown_layer" in result
        assert "[dim]" in result

    def test_colon_split(self):
        # badge should split on ':' for color lookup
        result = _badge("global:extra")
        assert "global:extra" in result

    def test_returns_string(self):
        result = _badge("project")
        assert isinstance(result, str)
