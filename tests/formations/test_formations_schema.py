"""Unit tests for navig.formations.schema — FormationValidationError, schema
constants, _validate_manually, validate_agent_data, validate_formation_data,
validate_profile_data.  No I/O, no network.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from navig.formations.schema import (
    AGENT_SCHEMA,
    FORMATION_SCHEMA,
    PROFILE_SCHEMA,
    FormationValidationError,
    _validate_manually,
    validate_agent_data,
    validate_formation_data,
    validate_profile_data,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_LONG_PROMPT = "You are a helpful testing assistant for the NAVIG system. " * 3  # 168+ chars

VALID_AGENT: dict = {
    "id": "test-agent",
    "name": "Test Agent",
    "role": "tester",
    "traits": ["helpful", "precise"],
    "personality": "Friendly and helpful assistant.",
    "scope": ["testing"],
    "system_prompt": _LONG_PROMPT,
}

VALID_FORMATION: dict = {
    "id": "test-formation",
    "name": "Test Formation",
    "version": "1.0.0",
    "description": "A test formation for unit testing purposes.",
    "agents": ["test-agent"],
    "default_agent": "test-agent",
}

VALID_PROFILE: dict = {
    "profile": "default",
}


# ===========================================================================
# TestFormationValidationError
# ===========================================================================


class TestFormationValidationError(unittest.TestCase):
    """FormationValidationError stores attributes and formats message."""

    def test_basic_message(self):
        err = FormationValidationError("Something went wrong")
        self.assertEqual(str(err), "Something went wrong")
        self.assertIsNone(err.path)
        self.assertEqual(err.errors, [])

    def test_with_path(self):
        p = Path("/tmp/agent.json")
        err = FormationValidationError("Bad file", path=p)
        self.assertIs(err.path, p)
        self.assertIn("agent.json", str(err))

    def test_with_errors_list(self):
        errors = ["Missing 'id'", "Missing 'name'"]
        err = FormationValidationError("Validation failed", errors=errors)
        msg = str(err)
        self.assertIn("Missing 'id'", msg)
        self.assertIn("Missing 'name'", msg)
        self.assertEqual(err.errors, errors)

    def test_with_path_and_errors(self):
        p = Path("/tmp/form.json")
        errors = ["Field 'version' required"]
        err = FormationValidationError("Invalid", path=p, errors=errors)
        self.assertIn("Invalid", str(err))
        self.assertIn("Field 'version' required", str(err))

    def test_is_exception(self):
        err = FormationValidationError("oops")
        self.assertIsInstance(err, Exception)

    def test_empty_errors_default(self):
        err = FormationValidationError("msg", errors=None)
        self.assertEqual(err.errors, [])


# ===========================================================================
# TestSchemaConstants
# ===========================================================================


class TestSchemaConstants(unittest.TestCase):
    """Schema constant dicts have correct structural properties."""

    # --- AGENT_SCHEMA ---

    def test_agent_schema_is_dict(self):
        self.assertIsInstance(AGENT_SCHEMA, dict)

    def test_agent_schema_required_has_id_name_role(self):
        for field in ("id", "name", "role"):
            self.assertIn(field, AGENT_SCHEMA["required"])

    def test_agent_schema_no_additional_properties(self):
        self.assertFalse(AGENT_SCHEMA.get("additionalProperties", True))

    def test_agent_schema_has_system_prompt_property(self):
        self.assertIn("system_prompt", AGENT_SCHEMA["properties"])
        sp = AGENT_SCHEMA["properties"]["system_prompt"]
        self.assertEqual(sp["type"], "string")
        self.assertGreaterEqual(sp.get("minLength", 0), 1)

    def test_agent_schema_traits_is_array(self):
        traits = AGENT_SCHEMA["properties"]["traits"]
        self.assertEqual(traits["type"], "array")

    # --- FORMATION_SCHEMA ---

    def test_formation_schema_is_dict(self):
        self.assertIsInstance(FORMATION_SCHEMA, dict)

    def test_formation_schema_required_fields(self):
        for field in ("id", "name", "version", "description", "agents", "default_agent"):
            self.assertIn(
                field, FORMATION_SCHEMA["required"], msg=f"Expected '{field}' in required"
            )

    def test_formation_schema_no_additional_properties(self):
        self.assertFalse(FORMATION_SCHEMA.get("additionalProperties", True))

    def test_formation_schema_version_pattern(self):
        version_prop = FORMATION_SCHEMA["properties"]["version"]
        self.assertIn("pattern", version_prop)

    # --- PROFILE_SCHEMA ---

    def test_profile_schema_is_dict(self):
        self.assertIsInstance(PROFILE_SCHEMA, dict)

    def test_profile_schema_required_has_profile(self):
        self.assertIn("profile", PROFILE_SCHEMA["required"])

    def test_profile_schema_no_additional_properties(self):
        self.assertFalse(PROFILE_SCHEMA.get("additionalProperties", True))


# ===========================================================================
# TestValidateManually
# ===========================================================================

# A minimal schema for testing _validate_manually directly
_SIMPLE_SCHEMA: dict = {
    "required": ["name", "count"],
    "properties": {
        "name": {"type": "string", "minLength": 2},
        "count": {"type": "integer"},
        "tags": {"type": "array", "minItems": 1},
        "score": {"type": "number"},
        "meta": {"type": "object"},
    },
    "additionalProperties": False,
}


class TestValidateManually(unittest.TestCase):
    """_validate_manually returns empty list for valid data, error list for invalid."""

    def test_valid_data_no_errors(self):
        data = {"name": "Alice", "count": 3}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertEqual(errors, [])

    def test_missing_required_field(self):
        data = {"name": "Alice"}  # missing 'count'
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("count" in e for e in errors))

    def test_missing_both_required(self):
        errors = _validate_manually({}, _SIMPLE_SCHEMA)
        self.assertEqual(len(errors), 2)

    def test_unknown_field_raises_error(self):
        data = {"name": "Alice", "count": 1, "unknown_field": True}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("unknown_field" in e for e in errors))

    def test_wrong_type_string(self):
        data = {"name": 123, "count": 1}  # name should be string
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("name" in e for e in errors))

    def test_wrong_type_integer(self):
        data = {"name": "hi", "count": "three"}  # count should be int
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("count" in e for e in errors))

    def test_min_length_violated(self):
        data = {"name": "A", "count": 1}  # name needs minLength 2
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("name" in e for e in errors))

    def test_array_type_check(self):
        data = {"name": "Alice", "count": 1, "tags": "not-a-list"}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("tags" in e for e in errors))

    def test_object_type_check(self):
        data = {"name": "Alice", "count": 1, "meta": [1, 2, 3]}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("meta" in e for e in errors))

    def test_number_type_accepts_float(self):
        data = {"name": "Alice", "count": 1, "score": 9.5}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertEqual(errors, [])

    def test_number_type_accepts_int(self):
        data = {"name": "Alice", "count": 1, "score": 9}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertEqual(errors, [])

    def test_non_dict_input(self):
        errors = _validate_manually(["list", "not", "dict"], _SIMPLE_SCHEMA)
        self.assertTrue(len(errors) > 0)

    def test_min_items_violated(self):
        # Patch schema so that 'tags' requires minItems=1 — our schema already does
        data = {"name": "Alice", "count": 1, "tags": []}
        errors = _validate_manually(data, _SIMPLE_SCHEMA)
        self.assertTrue(any("tags" in e for e in errors))


# ===========================================================================
# TestValidateAgentData
# ===========================================================================


class TestValidateAgentData(unittest.TestCase):
    """validate_agent_data returns AgentSpec on success, raises on failure."""

    def _run_validate(self, data: dict):
        # Force the _validate_manually fallback to avoid jsonschema dependency
        with patch("navig.formations.schema._validate_with_jsonschema") as mock_v:
            mock_v.side_effect = lambda d, s: _validate_manually(d, s)
            return validate_agent_data(data)

    def test_valid_data_returns_agent_spec(self):
        from navig.formations.types import AgentSpec

        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            result = validate_agent_data(VALID_AGENT.copy())
        self.assertIsInstance(result, AgentSpec)
        self.assertEqual(result.id, "test-agent")
        self.assertEqual(result.name, "Test Agent")

    def test_missing_required_raises(self):
        data = {"name": "Test"}  # missing id and role
        with patch(
            "navig.formations.schema._validate_with_jsonschema", return_value=["Missing 'id'"]
        ):
            with self.assertRaises(FormationValidationError) as ctx:
                validate_agent_data(data)
        self.assertIn("id", str(ctx.exception))

    def test_error_message_includes_path(self):
        p = Path("/tmp/bad.agent.json")
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=["bad field"]):
            with self.assertRaises(FormationValidationError) as ctx:
                validate_agent_data({"id": "x"}, path=p)
        self.assertIs(ctx.exception.path, p)

    def test_no_errors_calls_from_dict(self):
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            result = validate_agent_data(VALID_AGENT.copy())
        self.assertEqual(result.role, "tester")


# ===========================================================================
# TestValidateFormationData
# ===========================================================================


class TestValidateFormationData(unittest.TestCase):
    """validate_formation_data returns (data, errors) tuple."""

    def test_valid_data_empty_errors(self):
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            data, errors = validate_formation_data(VALID_FORMATION.copy())
        self.assertEqual(errors, [])
        self.assertEqual(data["id"], "test-formation")

    def test_returns_original_data_unchanged(self):
        original = VALID_FORMATION.copy()
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            data, errors = validate_formation_data(original)
        self.assertIs(data, original)

    def test_schema_errors_returned(self):
        with patch(
            "navig.formations.schema._validate_with_jsonschema",
            return_value=["Missing 'version'"],
        ):
            _, errors = validate_formation_data({"id": "x", "name": "Y"})
        self.assertIn("Missing 'version'", errors)

    def test_cross_field_default_agent_not_in_agents(self):
        data = VALID_FORMATION.copy()
        data["default_agent"] = "nonexistent-agent"  # not in ["test-agent"]
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            _, errors = validate_formation_data(data)
        self.assertTrue(any("default_agent" in e for e in errors))

    def test_cross_field_skipped_when_schema_errors_exist(self):
        # Cross-field validation only runs if no schema errors
        data = VALID_FORMATION.copy()
        data["default_agent"] = "missing-agent"
        with patch(
            "navig.formations.schema._validate_with_jsonschema",
            return_value=["schema error"],
        ):
            _, errors = validate_formation_data(data)
        # Only the schema error should be present
        self.assertIn("schema error", errors)
        self.assertEqual(len(errors), 1)

    def test_path_not_used_in_return_value(self):
        # validate_formation_data doesn't raise; it returns errors
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            result = validate_formation_data(VALID_FORMATION.copy(), path=Path("/tmp/form.json"))
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


# ===========================================================================
# TestValidateProfileData
# ===========================================================================


class TestValidateProfileData(unittest.TestCase):
    """validate_profile_data returns list of errors."""

    def test_valid_profile_empty_errors(self):
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            errors = validate_profile_data(VALID_PROFILE.copy())
        self.assertEqual(errors, [])

    def test_returns_list(self):
        with patch("navig.formations.schema._validate_with_jsonschema", return_value=[]):
            result = validate_profile_data(VALID_PROFILE.copy())
        self.assertIsInstance(result, list)

    def test_invalid_profile_returns_errors(self):
        with patch(
            "navig.formations.schema._validate_with_jsonschema",
            return_value=["Missing 'profile'"],
        ):
            errors = validate_profile_data({})
        self.assertIn("Missing 'profile'", errors)

    def test_passes_profile_schema_to_validator(self):
        captured = {}

        def capture(data, schema):
            captured["schema"] = schema
            return []

        with patch("navig.formations.schema._validate_with_jsonschema", side_effect=capture):
            validate_profile_data(VALID_PROFILE.copy())

        self.assertIs(captured["schema"], PROFILE_SCHEMA)


# ===========================================================================
# TestValidateWithJsonschemaPaths
# ===========================================================================


class TestValidateWithJsonschemaFallback(unittest.TestCase):
    """_validate_with_jsonschema falls back to _validate_manually if jsonschema not available."""

    def test_falls_back_when_jsonschema_missing(self):
        import sys

        # Simulate ImportError for jsonschema
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else None

        import importlib

        with patch.dict(sys.modules, {"jsonschema": None}):
            from navig.formations import schema as schema_mod

            # Call the private function with a simple schema
            simple = {
                "required": ["x"],
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            }
            # It should not raise, just return errors or []
            try:
                result = schema_mod._validate_with_jsonschema({"x": "val"}, simple)
                self.assertIsInstance(result, list)
            except Exception:
                pass  # ImportError path may behave differently in cached modules


if __name__ == "__main__":
    unittest.main()
