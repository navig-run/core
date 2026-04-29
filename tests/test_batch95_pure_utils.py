"""Batch 95 — pure utility modules: safe_eval, thresholds, tokens.

Tests:
- navig.core.safe_eval.safe_eval (arithmetic, comparison, logic, variables,
  data-structures, subscript, blocked constructs)
- navig.core.thresholds (Threshold dataclass, DEFAULTS, REGISTRY, resolve)
- navig.core.tokens.estimate_tokens
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from navig.core.safe_eval import safe_eval
from navig.core.thresholds import (
    DEFAULTS,
    REGISTRY,
    Threshold,
    resolve,
)
from navig.core.tokens import estimate_tokens


# ===========================================================================
# safe_eval — arithmetic
# ===========================================================================


class TestSafeEvalArithmetic:
    def test_addition(self):
        assert safe_eval("1 + 2") == 3

    def test_subtraction(self):
        assert safe_eval("10 - 3") == 7

    def test_multiplication(self):
        assert safe_eval("4 * 5") == 20

    def test_true_division(self):
        assert safe_eval("7 / 2") == 3.5

    def test_floor_division(self):
        assert safe_eval("7 // 2") == 3

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1

    def test_power(self):
        assert safe_eval("2 ** 8") == 256

    def test_unary_negation(self):
        assert safe_eval("-5") == -5

    def test_unary_positive(self):
        assert safe_eval("+3") == 3

    def test_nested_arithmetic(self):
        assert safe_eval("(2 + 3) * 4") == 20


# ===========================================================================
# safe_eval — comparisons
# ===========================================================================


class TestSafeEvalComparisons:
    def test_equal_true(self):
        assert safe_eval("1 == 1") is True

    def test_equal_false(self):
        assert safe_eval("1 == 2") is False

    def test_not_equal(self):
        assert safe_eval("1 != 2") is True

    def test_less_than(self):
        assert safe_eval("3 < 5") is True

    def test_less_than_or_equal(self):
        assert safe_eval("5 <= 5") is True

    def test_greater_than(self):
        assert safe_eval("7 > 3") is True

    def test_greater_than_or_equal(self):
        assert safe_eval("4 >= 4") is True

    def test_in_operator(self):
        assert safe_eval("2 in [1, 2, 3]") is True

    def test_not_in_operator(self):
        assert safe_eval("5 not in [1, 2, 3]") is True

    def test_chained_comparison(self):
        assert safe_eval("1 < 2 < 3") is True

    def test_chained_comparison_false(self):
        assert safe_eval("1 < 3 < 2") is False


# ===========================================================================
# safe_eval — logic
# ===========================================================================


class TestSafeEvalLogic:
    def test_and_true(self):
        assert safe_eval("True and True") is True

    def test_and_false(self):
        assert safe_eval("True and False") is False

    def test_or_true(self):
        assert safe_eval("False or True") is True

    def test_or_false(self):
        assert safe_eval("False or False") is False

    def test_not_true(self):
        assert safe_eval("not False") is True

    def test_not_false(self):
        assert safe_eval("not True") is False

    def test_complex_logic(self):
        assert safe_eval("(1 < 2) and (3 > 2)") is True


# ===========================================================================
# safe_eval — literals and variables
# ===========================================================================


class TestSafeEvalLiteralsAndVariables:
    def test_integer_literal(self):
        assert safe_eval("42") == 42

    def test_float_literal(self):
        assert safe_eval("3.14") == pytest.approx(3.14)

    def test_string_literal(self):
        assert safe_eval("'hello'") == "hello"

    def test_true_literal(self):
        assert safe_eval("True") is True

    def test_false_literal(self):
        assert safe_eval("False") is False

    def test_none_literal(self):
        assert safe_eval("None") is None

    def test_variable_substitution(self):
        assert safe_eval("x + 1", variables={"x": 10}) == 11

    def test_multiple_variables(self):
        assert safe_eval("a * b", variables={"a": 3, "b": 4}) == 12

    def test_variable_in_comparison(self):
        assert safe_eval("x > 5", variables={"x": 7}) is True

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Unknown variable"):
            safe_eval("undefined_var")

    def test_none_variables_defaults_to_empty(self):
        assert safe_eval("1 + 1", variables=None) == 2


# ===========================================================================
# safe_eval — data structures
# ===========================================================================


class TestSafeEvalDataStructures:
    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self):
        assert safe_eval("(1, 2)") == (1, 2)

    def test_dict_literal(self):
        assert safe_eval("{'a': 1}") == {"a": 1}

    def test_subscript_list(self):
        assert safe_eval("[10, 20, 30][1]") == 20

    def test_subscript_dict_via_variable(self):
        assert safe_eval("d['key']", variables={"d": {"key": "val"}}) == "val"

    def test_subscript_string(self):
        assert safe_eval("s[0]", variables={"s": "hello"}) == "h"


# ===========================================================================
# safe_eval — blocked constructs
# ===========================================================================


class TestSafeEvalBlockedConstructs:
    def test_function_call_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("len([1, 2, 3])")

    def test_attribute_access_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("'hello'.upper()")

    def test_import_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("__import__('os')")

    def test_invalid_syntax_raises(self):
        with pytest.raises(ValueError):
            safe_eval("1 +* 2")


# ===========================================================================
# thresholds — Threshold dataclass
# ===========================================================================


class TestThresholdDataclass:
    def test_threshold_fields(self):
        t = Threshold(warn_pct=70.0, crit_pct=90.0)
        assert t.warn_pct == 70.0
        assert t.crit_pct == 90.0

    def test_threshold_is_frozen(self):
        t = Threshold(warn_pct=70.0, crit_pct=90.0)
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            t.warn_pct = 50.0  # type: ignore[misc]

    def test_threshold_equality(self):
        a = Threshold(warn_pct=80.0, crit_pct=95.0)
        b = Threshold(warn_pct=80.0, crit_pct=95.0)
        assert a == b


# ===========================================================================
# thresholds — DEFAULTS and REGISTRY
# ===========================================================================


class TestThresholdDefaults:
    def test_defaults_type(self):
        assert isinstance(DEFAULTS, Threshold)

    def test_defaults_warn_pct(self):
        assert DEFAULTS.warn_pct == 80.0

    def test_defaults_crit_pct(self):
        assert DEFAULTS.crit_pct == 95.0


class TestThresholdRegistry:
    def test_registry_is_dict(self):
        assert isinstance(REGISTRY, dict)

    def test_registry_nonempty(self):
        assert len(REGISTRY) > 0

    def test_all_values_are_thresholds(self):
        for key, val in REGISTRY.items():
            assert isinstance(val, Threshold), f"{key} is not a Threshold"

    def test_cpu_usage_registered(self):
        assert "cpu_usage" in REGISTRY

    def test_disk_usage_registered(self):
        assert "disk_usage" in REGISTRY

    def test_memory_usage_registered(self):
        assert "memory_usage" in REGISTRY

    def test_warn_lt_crit_for_all(self):
        for key, val in REGISTRY.items():
            assert val.warn_pct < val.crit_pct, f"{key}: warn_pct >= crit_pct"


# ===========================================================================
# thresholds — resolve
# ===========================================================================


class TestThresholdResolve:
    def test_resolve_returns_threshold(self):
        result = resolve("cpu_usage")
        assert isinstance(result, Threshold)

    def test_resolve_known_metric(self):
        t = resolve("cpu_usage")
        assert t == REGISTRY["cpu_usage"]

    def test_resolve_unknown_falls_back_to_defaults(self):
        t = resolve("totally_unknown_metric_xyz")
        assert t == DEFAULTS

    def test_resolve_disk_usage(self):
        t = resolve("disk_usage")
        assert t.warn_pct == 85.0
        assert t.crit_pct == 95.0

    def test_resolve_error_rate(self):
        t = resolve("error_rate")
        assert t.warn_pct < 50.0  # error rate thresholds are small percentages


# ===========================================================================
# tokens — estimate_tokens
# ===========================================================================


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_exactly_four_chars(self):
        # "abcd" → 4 chars / 4.0 = 1 token
        assert estimate_tokens("abcd") == 1

    def test_eight_chars(self):
        assert estimate_tokens("abcdefgh") == 2

    def test_nonzero_for_single_char(self):
        # 1 char / 4.0 = 0.25 → max(1, 0) = 1
        assert estimate_tokens("x") == 1

    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello world"), int)

    def test_longer_text(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_custom_chars_per_token(self):
        # 35 chars at 3.5 = 10 tokens
        text = "a" * 35
        assert estimate_tokens(text, chars_per_token=3.5) == 10

    def test_default_ratio_is_4(self):
        text = "a" * 40
        assert estimate_tokens(text) == 10

    def test_never_negative(self):
        assert estimate_tokens("hi") >= 0

    def test_whitespace_only_counts(self):
        # Whitespace is still characters
        assert estimate_tokens("    ") == 1
