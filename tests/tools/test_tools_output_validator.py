"""Tests for navig.tools.output_validator — validate_output and _naive_check."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from navig.tools.output_validator import (
    OutputValidationError,
    _naive_check,
    validate_output,
)


class TestValidateOutput:
    def test_returns_true_none_on_valid(self):
        ok, msg = validate_output({"x": 1}, {"type": "object"})
        assert ok is True
        assert msg is None

    def test_returns_false_message_on_invalid(self):
        ok, msg = validate_output("hello", {"type": "object"})
        assert ok is False
        assert msg is not None

    def test_strict_raises_on_failure(self):
        with pytest.raises(OutputValidationError):
            validate_output(42, {"type": "string"}, strict=True)

    def test_strict_does_not_raise_on_success(self):
        ok, msg = validate_output("hello", {"type": "string"}, strict=True)
        assert ok is True


class TestNaiveCheckTypes:
    def test_object_type_passes_dict(self):
        ok, msg = _naive_check({"a": 1}, {"type": "object"})
        assert ok is True

    def test_object_type_fails_on_string(self):
        ok, msg = _naive_check("not-a-dict", {"type": "object"})
        assert ok is False
        assert "object" in msg

    def test_array_type_passes_list(self):
        ok, msg = _naive_check([1, 2, 3], {"type": "array"})
        assert ok is True

    def test_string_type_passes_str(self):
        ok, msg = _naive_check("hello", {"type": "string"})
        assert ok is True

    def test_number_type_passes_int(self):
        ok, msg = _naive_check(42, {"type": "number"})
        assert ok is True

    def test_number_type_passes_float(self):
        ok, msg = _naive_check(3.14, {"type": "number"})
        assert ok is True

    def test_integer_type_fails_for_float(self):
        ok, msg = _naive_check(3.14, {"type": "integer"})
        assert ok is False

    def test_boolean_type(self):
        ok, _ = _naive_check(True, {"type": "boolean"})
        assert ok is True

    def test_null_type(self):
        ok, _ = _naive_check(None, {"type": "null"})
        assert ok is True

    def test_unknown_type_passes(self):
        ok, _ = _naive_check("anything", {"type": "custom_type_xyz"})
        assert ok is True


class TestNaiveCheckRequired:
    def test_required_field_present(self):
        ok, _ = _naive_check({"name": "Alice", "age": 30}, {"type": "object", "required": ["name"]})
        assert ok is True

    def test_required_field_missing(self):
        ok, msg = _naive_check({"age": 30}, {"type": "object", "required": ["name"]})
        assert ok is False
        assert "name" in msg

    def test_multiple_required_all_present(self):
        ok, _ = _naive_check({"a": 1, "b": 2}, {"type": "object", "required": ["a", "b"]})
        assert ok is True

    def test_multiple_required_one_missing(self):
        ok, msg = _naive_check({"a": 1}, {"type": "object", "required": ["a", "b"]})
        assert ok is False


class TestNaiveCheckEnum:
    def test_enum_value_valid(self):
        ok, _ = _naive_check("red", {"enum": ["red", "green", "blue"]})
        assert ok is True

    def test_enum_value_invalid(self):
        ok, msg = _naive_check("purple", {"enum": ["red", "green", "blue"]})
        assert ok is False
        assert "purple" in msg

    def test_enum_with_numbers(self):
        ok, _ = _naive_check(2, {"enum": [1, 2, 3]})
        assert ok is True


class TestNaiveCheckArrayItems:
    def test_array_item_type_check(self):
        ok, msg = _naive_check([1, 2, 3], {"type": "array", "items": {"type": "integer"}})
        assert ok is True

    def test_array_item_type_mismatch(self):
        ok, msg = _naive_check(["a", "b"], {"type": "array", "items": {"type": "integer"}})
        assert ok is False
        assert "Array item" in msg

    def test_empty_array_always_passes(self):
        ok, _ = _naive_check([], {"type": "array", "items": {"type": "integer"}})
        assert ok is True


class TestJsonschemaFallback:
    def test_falls_back_to_naive_when_jsonschema_missing(self):
        """When jsonschema is absent, naive check is used."""
        with patch.dict(sys.modules, {"jsonschema": None}):
            ok, msg = validate_output({"x": 1}, {"type": "object"})
        assert ok is True
