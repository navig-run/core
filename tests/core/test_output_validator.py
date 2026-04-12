"""Tests for navig.tools.output_validator."""

from __future__ import annotations

import pytest

from navig.tools.output_validator import OutputValidationError, validate_output

pytestmark = pytest.mark.unit


class TestValidateOutput:
    # -- Type checks ----------------------------------------------------------

    def test_string_valid(self):
        ok, msg = validate_output("hello", {"type": "string"})
        assert ok and msg is None

    def test_string_wrong_type(self):
        ok, msg = validate_output(42, {"type": "string"})
        assert not ok
        assert msg is not None

    def test_number_valid_int(self):
        ok, msg = validate_output(3, {"type": "number"})
        assert ok

    def test_number_valid_float(self):
        ok, msg = validate_output(3.14, {"type": "number"})
        assert ok

    def test_integer_rejects_float(self):
        ok, msg = validate_output(3.14, {"type": "integer"})
        assert not ok

    def test_boolean_valid(self):
        ok, msg = validate_output(True, {"type": "boolean"})
        assert ok

    def test_null_valid(self):
        ok, msg = validate_output(None, {"type": "null"})
        assert ok

    def test_array_valid(self):
        ok, msg = validate_output([1, 2, 3], {"type": "array"})
        assert ok

    def test_object_valid(self):
        ok, msg = validate_output({"a": 1}, {"type": "object"})
        assert ok

    # -- Object required fields -----------------------------------------------

    def test_required_field_present(self):
        schema = {"type": "object", "required": ["name"]}
        ok, msg = validate_output({"name": "Alice"}, schema)
        assert ok

    def test_required_field_missing(self):
        schema = {"type": "object", "required": ["name"]}
        ok, msg = validate_output({}, schema)
        assert not ok
        assert "name" in msg

    # -- Enum ------------------------------------------------------------------

    def test_enum_valid(self):
        ok, msg = validate_output("foo", {"enum": ["foo", "bar"]})
        assert ok

    def test_enum_invalid(self):
        ok, msg = validate_output("baz", {"enum": ["foo", "bar"]})
        assert not ok

    # -- Array items -----------------------------------------------------------

    def test_array_items_valid(self):
        schema = {"type": "array", "items": {"type": "string"}}
        ok, msg = validate_output(["a", "b"], schema)
        assert ok

    def test_array_items_invalid_first(self):
        schema = {"type": "array", "items": {"type": "string"}}
        ok, msg = validate_output([1, 2], schema)
        assert not ok

    # -- Empty schema (permissive) ----------------------------------------

    def test_empty_schema_always_valid(self):
        ok, msg = validate_output({"anything": True}, {})
        assert ok

    # -- Strict mode ----------------------------------------------------------

    def test_strict_raises_on_failure(self):
        with pytest.raises(OutputValidationError):
            validate_output(42, {"type": "string"}, strict=True)

    def test_strict_no_raise_on_success(self):
        ok, msg = validate_output("hi", {"type": "string"}, strict=True)
        assert ok
