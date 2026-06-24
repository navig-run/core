"""Hermetic unit tests for navig.core.safe_eval."""

from __future__ import annotations

import pytest

from navig.core.safe_eval import safe_eval

# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


class TestLiterals:
    def test_integer(self):
        assert safe_eval("42") == 42

    def test_float(self):
        assert safe_eval("3.14") == pytest.approx(3.14)

    def test_string(self):
        assert safe_eval('"hello"') == "hello"

    def test_true(self):
        assert safe_eval("True") is True

    def test_false(self):
        assert safe_eval("False") is False

    def test_none(self):
        assert safe_eval("None") is None


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


class TestArithmetic:
    def test_addition(self):
        assert safe_eval("2 + 3") == 5

    def test_subtraction(self):
        assert safe_eval("10 - 4") == 6

    def test_multiplication(self):
        assert safe_eval("3 * 7") == 21

    def test_division(self):
        assert safe_eval("10 / 4") == pytest.approx(2.5)

    def test_floor_division(self):
        assert safe_eval("10 // 3") == 3

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1

    def test_exponentiation(self):
        assert safe_eval("2 ** 8") == 256

    def test_unary_neg(self):
        assert safe_eval("-5") == -5

    def test_nested(self):
        assert safe_eval("(2 + 3) * 4") == 20


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


class TestComparisons:
    def test_eq(self):
        assert safe_eval("1 == 1") is True

    def test_ne(self):
        assert safe_eval("1 != 2") is True

    def test_lt(self):
        assert safe_eval("1 < 2") is True

    def test_le(self):
        assert safe_eval("2 <= 2") is True

    def test_gt(self):
        assert safe_eval("3 > 2") is True

    def test_ge(self):
        assert safe_eval("3 >= 3") is True

    def test_in_list(self):
        assert safe_eval("2 in [1, 2, 3]") is True

    def test_not_in_list(self):
        assert safe_eval("5 not in [1, 2, 3]") is True


# ---------------------------------------------------------------------------
# Boolean logic
# ---------------------------------------------------------------------------


class TestBooleanLogic:
    def test_and_true(self):
        assert safe_eval("True and True") is True

    def test_and_false(self):
        assert safe_eval("True and False") is False

    def test_or_true(self):
        assert safe_eval("False or True") is True

    def test_not(self):
        assert safe_eval("not False") is True


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


class TestVariables:
    def test_simple_variable(self):
        assert safe_eval("x", {"x": 10}) == 10

    def test_arithmetic_with_variable(self):
        assert safe_eval("x + y", {"x": 3, "y": 4}) == 7

    def test_comparison_with_variable(self):
        assert safe_eval("x > 5", {"x": 10}) is True

    def test_unknown_variable_raises(self):
        with pytest.raises((ValueError, NameError)):
            safe_eval("z")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class TestDataStructures:
    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self):
        assert safe_eval("(1, 2)") == (1, 2)

    def test_dict_literal(self):
        assert safe_eval('{"a": 1}') == {"a": 1}

    def test_subscript_list(self):
        assert safe_eval("[10, 20, 30][1]") == 20

    def test_subscript_dict(self):
        assert safe_eval('{"key": 99}["key"]') == 99


# ---------------------------------------------------------------------------
# Blocked features
# ---------------------------------------------------------------------------


class TestBlockedFeatures:
    def test_function_call_blocked(self):
        with pytest.raises((ValueError, TypeError)):
            safe_eval("len([1, 2, 3])")

    def test_attribute_access_blocked(self):
        with pytest.raises((ValueError, AttributeError, TypeError)):
            safe_eval('"hello".upper()')
