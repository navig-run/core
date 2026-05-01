"""
Batch 42 — navig/core/tokens.py + dict_utils.py + thresholds.py + safe_eval.py + file_permissions.py
Pure-logic and I/O-mocked tests.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock, call
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
# navig.core.tokens
# ─────────────────────────────────────────────────────────────

from navig.core.tokens import estimate_tokens


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_none_like_falsy_returns_zero(self):
        # Empty string is the only valid falsy input per signature
        assert estimate_tokens("") == 0

    def test_four_char_word_returns_one(self):
        # "test" = 4 chars / 4.0 = 1 token
        assert estimate_tokens("test") == 1

    def test_sixteen_chars_returns_four(self):
        assert estimate_tokens("a" * 16) == 4

    def test_single_char_returns_at_least_one(self):
        # 1 / 4 rounds to 0, but max(1, ...) ensures >= 1
        assert estimate_tokens("x") >= 1

    def test_custom_chars_per_token(self):
        # 10 chars with ratio 2.0 → 5 tokens
        assert estimate_tokens("a" * 10, chars_per_token=2.0) == 5

    def test_conservative_ratio(self):
        # 35 chars, ratio 3.5 → 10 tokens
        assert estimate_tokens("a" * 35, chars_per_token=3.5) == 10

    def test_returns_int(self):
        result = estimate_tokens("hello world")
        assert isinstance(result, int)

    def test_large_text_scales_linearly(self):
        result = estimate_tokens("a" * 4000)
        assert result == 1000


# ─────────────────────────────────────────────────────────────
# navig.core.dict_utils
# ─────────────────────────────────────────────────────────────

from navig.core.dict_utils import deep_merge, truncate_output, utc_now, now_iso


class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = deep_merge(base, override)
        assert result["a"] == 1
        assert result["b"] == 99

    def test_nested_dict_merged_recursively(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 99, "c": 3}}
        result = deep_merge(base, override)
        assert result["x"]["a"] == 1
        assert result["x"]["b"] == 99
        assert result["x"]["c"] == 3

    def test_lists_concatenated(self):
        base = {"items": [1, 2]}
        override = {"items": [3, 4]}
        result = deep_merge(base, override)
        assert result["items"] == [1, 2, 3, 4]

    def test_original_dicts_not_mutated(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        deep_merge(base, override)
        assert "y" not in base["a"]

    def test_new_key_added(self):
        result = deep_merge({"a": 1}, {"b": 2})
        assert result["b"] == 2

    def test_empty_override_returns_base(self):
        base = {"a": 1}
        assert deep_merge(base, {}) == {"a": 1}

    def test_empty_base_returns_override(self):
        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_deep_copy_of_override_values(self):
        obj = [1, 2, 3]
        result = deep_merge({}, {"items": obj})
        result["items"].append(99)
        assert obj == [1, 2, 3]  # original untouched


class TestTruncateOutput:
    def test_short_text_returned_unchanged(self):
        assert truncate_output("hello", 100) == "hello"

    def test_exact_limit_returned_unchanged(self):
        assert truncate_output("hello", 5) == "hello"

    def test_over_limit_truncated(self):
        result = truncate_output("a" * 20, 10)
        assert result.startswith("a" * 10)
        assert "truncated" in result

    def test_truncated_result_contains_total_chars(self):
        result = truncate_output("x" * 100, 10)
        assert "100" in result

    def test_empty_string(self):
        assert truncate_output("", 10) == ""


class TestUtcNow:
    def test_returns_aware_datetime(self):
        from datetime import timezone
        dt = utc_now()
        assert dt.tzinfo == timezone.utc

    def test_returns_datetime(self):
        from datetime import datetime
        assert isinstance(utc_now(), datetime)


class TestNowIso:
    def test_returns_string(self):
        assert isinstance(now_iso(), str)

    def test_contains_plus00(self):
        # ISO string should contain UTC offset
        result = now_iso()
        assert "+" in result or "Z" in result.upper() or "+00:00" in result


# ─────────────────────────────────────────────────────────────
# navig.core.thresholds
# ─────────────────────────────────────────────────────────────

from navig.core.thresholds import Threshold, REGISTRY, DEFAULTS, resolve


class TestThreshold:
    def test_frozen(self):
        t = Threshold(warn_pct=80.0, crit_pct=95.0)
        with pytest.raises((AttributeError, TypeError)):
            t.warn_pct = 50.0

    def test_fields_accessible(self):
        t = Threshold(warn_pct=75.0, crit_pct=90.0)
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0


class TestRegistry:
    def test_cpu_usage_registered(self):
        assert "cpu_usage" in REGISTRY

    def test_memory_usage_registered(self):
        assert "memory_usage" in REGISTRY

    def test_disk_usage_registered(self):
        assert "disk_usage" in REGISTRY

    def test_error_rate_registered(self):
        assert "error_rate" in REGISTRY

    def test_all_values_are_threshold(self):
        for v in REGISTRY.values():
            assert isinstance(v, Threshold)


class TestResolve:
    def test_known_metric_returns_registered(self):
        t = resolve("cpu_usage")
        assert t is REGISTRY["cpu_usage"]

    def test_unknown_metric_returns_defaults(self):
        t = resolve("nonexistent_metric_xyz")
        assert t is DEFAULTS

    def test_defaults_has_sensible_values(self):
        assert DEFAULTS.warn_pct > 0
        assert DEFAULTS.crit_pct > DEFAULTS.warn_pct

    def test_all_registered_metrics_warn_lt_crit(self):
        for name, t in REGISTRY.items():
            assert t.warn_pct < t.crit_pct, f"{name}: warn >= crit"


# ─────────────────────────────────────────────────────────────
# navig.core.safe_eval
# ─────────────────────────────────────────────────────────────

from navig.core.safe_eval import safe_eval


class TestSafeEvalArithmetic:
    def test_addition(self):
        assert safe_eval("1 + 2") == 3

    def test_subtraction(self):
        assert safe_eval("10 - 3") == 7

    def test_multiplication(self):
        assert safe_eval("4 * 5") == 20

    def test_division(self):
        assert safe_eval("10 / 4") == pytest.approx(2.5)

    def test_floor_division(self):
        assert safe_eval("10 // 3") == 3

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1

    def test_power(self):
        assert safe_eval("2 ** 8") == 256

    def test_unary_neg(self):
        assert safe_eval("-5") == -5


class TestSafeEvalComparison:
    def test_equal_true(self):
        assert safe_eval("1 == 1") is True

    def test_equal_false(self):
        assert safe_eval("1 == 2") is False

    def test_not_equal(self):
        assert safe_eval("1 != 2") is True

    def test_less_than(self):
        assert safe_eval("3 < 5") is True

    def test_greater_than(self):
        assert safe_eval("5 > 3") is True

    def test_in_operator(self):
        assert safe_eval("1 in [1, 2, 3]") is True

    def test_not_in_operator(self):
        assert safe_eval("4 not in [1, 2, 3]") is True


class TestSafeEvalLogic:
    def test_and_true(self):
        assert safe_eval("True and True") is True

    def test_and_false(self):
        assert safe_eval("True and False") is False

    def test_or_true(self):
        assert safe_eval("False or True") is True

    def test_not_true(self):
        assert safe_eval("not False") is True


class TestSafeEvalVariables:
    def test_variable_substitution(self):
        assert safe_eval("x + 1", {"x": 10}) == 11

    def test_multiple_variables(self):
        assert safe_eval("a + b", {"a": 3, "b": 5}) == 8

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Unknown variable"):
            safe_eval("z + 1")


class TestSafeEvalDataStructures:
    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self):
        assert safe_eval("(1, 2)") == (1, 2)

    def test_dict_literal(self):
        assert safe_eval('{"a": 1}') == {"a": 1}


class TestSafeEvalBlocked:
    def test_function_call_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("print('hello')")

    def test_invalid_syntax_raises(self):
        with pytest.raises(ValueError):
            safe_eval("this is not valid python!!!")


# ─────────────────────────────────────────────────────────────
# navig.core.file_permissions
# ─────────────────────────────────────────────────────────────

from navig.core.file_permissions import set_owner_only_file_permissions


class TestSetOwnerOnlyFilePermissions:
    def test_unix_calls_chmod_600(self, tmp_path):
        target = tmp_path / "test.txt"
        target.write_text("data")
        with patch("os.name", "posix"), \
             patch("os.chmod") as mock_chmod:
            set_owner_only_file_permissions(str(target))
            mock_chmod.assert_called_once_with(str(target), 0o600)

    def test_unix_accepts_path_object(self, tmp_path):
        target = tmp_path / "test.txt"
        target.write_text("data")
        with patch("os.name", "posix"), \
             patch("os.chmod") as mock_chmod:
            set_owner_only_file_permissions(target)
            mock_chmod.assert_called_once()

    def test_unix_chmod_failure_does_not_raise(self, tmp_path):
        target = tmp_path / "test.txt"
        target.write_text("data")
        with patch("os.name", "posix"), \
             patch("os.chmod", side_effect=PermissionError("denied")):
            set_owner_only_file_permissions(str(target))  # must not raise

    def test_windows_calls_icacls(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "nt"), \
             patch("getpass.getuser", return_value="testuser"), \
             patch("subprocess.run") as mock_run:
            set_owner_only_file_permissions(str(target))
            # icacls should be called at least once
            assert mock_run.call_count >= 1

    def test_windows_subprocess_failure_does_not_raise(self, tmp_path):
        import subprocess
        target = tmp_path / "secret.txt"
        target.write_text("data")
        with patch("os.name", "nt"), \
             patch("getpass.getuser", return_value="testuser"), \
             patch("subprocess.run", side_effect=OSError("no icacls")):
            set_owner_only_file_permissions(str(target))  # must not raise
