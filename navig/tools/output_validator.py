"""
navig.tools.output_validator — Soft schema validation for tool outputs.

Tool authors can declare an expected JSON schema for their tool's output by
setting ``output_schema`` on the ``ToolMeta`` descriptor.  After each
successful tool execution the router calls this validator.

The validator is *soft-failure* by design:
- A mismatch logs a WARNING and attaches a note to the ToolResult — it does
  **not** abort the pipeline or mark the result as an error.
- This keeps the system operational even when schemas drift from reality,
  while still surfacing discrepancies to the operator.

Dependencies
------------
``jsonschema`` is used when available (pip install jsonschema).  When absent,
a naive type-check fallback is used instead — sufficient for basic types.

Usage
-----
    from navig.tools.output_validator import validate_output

    ok, msg = validate_output(output_value, schema_dict)
    if not ok:
        logger.warning("Output schema violation: %s", msg)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("navig.tools.output_validator")

__all__ = ["validate_output", "OutputValidationError"]


class OutputValidationError(ValueError):
    """Raised only in strict mode; normally validation is soft."""


# =============================================================================
# Core validator
# =============================================================================


def validate_output(
    output: Any,
    schema: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[bool, str | None]:
    """
    Validate *output* against a JSON Schema *schema*.

    Args:
        output:  The value returned by a tool handler.
        schema:  A JSON Schema dict (supports ``type``, ``required``,
                 ``properties``, ``items``, ``enum`` at minimum).
        strict:  When True, raises ``OutputValidationError`` on failure
                 instead of returning ``(False, message)``.

    Returns:
        ``(True, None)`` on success, ``(False, error_message)`` on failure.

    Raises:
        OutputValidationError: Only when ``strict=True`` and validation fails.
    """
    ok, msg = _validate(output, schema)
    if not ok and strict:
        raise OutputValidationError(msg)
    return ok, msg


def _validate(output: Any, schema: dict[str, Any]) -> tuple[bool, str | None]:
    """Internal — try jsonschema first, fall back to naive check."""
    # 1. Try jsonschema
    try:
        import jsonschema  # type: ignore[import]

        try:
            jsonschema.validate(instance=output, schema=schema)
            return True, None
        except jsonschema.ValidationError as exc:
            return False, exc.message
        except jsonschema.SchemaError as exc:
            logger.warning("output_validator: invalid schema definition: %s", exc)
            return True, None  # don't punish the tool for a bad schema
    except ImportError:
        pass  # optional dependency not installed; feature disabled

    # 2. Naive fallback — checks top-level ``type`` and ``required`` only
    return _naive_check(output, schema)


def _naive_check(output: Any, schema: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Minimal type + required-field check when jsonschema is not installed.

    Supports: type (object/array/string/number/integer/boolean/null), required.
    """
    schema_type = schema.get("type")
    if schema_type:
        if not _check_type(output, schema_type):
            got = type(output).__name__
            return False, f"Expected type '{schema_type}', got '{got}'"

    if schema_type == "object" and isinstance(output, dict):
        required = schema.get("required", [])
        for field_name in required:
            if field_name not in output:
                return False, f"Missing required field: '{field_name}'"

    if schema_type == "array" and isinstance(output, list):
        items_schema = schema.get("items")
        if items_schema and output:
            # Only validate first item for naive check
            ok, msg = _naive_check(output[0], items_schema)
            if not ok:
                return False, f"Array item validation failed: {msg}"

    enum_values = schema.get("enum")
    if enum_values is not None and output not in enum_values:
        return False, f"Value '{output}' not in enum {enum_values}"

    return True, None


_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _check_type(value: Any, schema_type: str) -> bool:
    expected = _TYPE_MAP.get(schema_type)
    if expected is None:
        return True  # unknown type — pass
    return isinstance(value, expected)
