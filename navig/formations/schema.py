"""
Formation Schema Validation

JSON Schema definitions and validation for formation.json and .agent.json files.
Uses jsonschema library with fallback to manual validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from navig.formations.types import AgentSpec, Formation


class FormationValidationError(Exception):
    """Raised when a formation or agent file fails validation."""

    def __init__(self, message: str, path: Optional[Path] = None, errors: Optional[List[str]] = None):
        self.path = path
        self.errors = errors or []
        detail = f" ({path})" if path else ""
        if self.errors:
            detail += "\n  - " + "\n  - ".join(self.errors)
        super().__init__(f"{message}{detail}")


# --- JSON Schemas ---

AGENT_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "NAVIG Agent Specification",
    "type": "object",
    "required": ["id", "name", "role"],
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_-]*$",
            "description": "Unique agent identifier (kebab-case)",
        },
        "name": {"type": "string", "minLength": 1},
        "role": {"type": "string", "minLength": 1},
        "version": {
            "type": "integer",
            "description": "Profile format version (directory-based profiles)",
        },
        "summary": {
            "type": "string",
            "description": "One-line personality summary (directory-based profiles)",
        },
        "traits": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 10,
        },
        "personality": {"type": "string", "minLength": 1},
        "scope": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "kpis": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "system_prompt": {"type": "string", "minLength": 100},
        "council_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
        },
        "docs": {
            "type": "object",
            "description": "Links to markdown profile documents (directory-based profiles)",
            "properties": {
                "soul": {"type": "string"},
                "personality": {"type": "string"},
                "memory": {"type": "string"},
                "playbook": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "flags": {
            "type": "object",
            "description": "Machine-readable configuration flags",
            "properties": {
                "council_weight": {"type": "number"},
                "verbosity": {"type": "string"},
                "risk_tolerance": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "api_dependencies": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
    },
    "additionalProperties": False,
}

FORMATION_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "NAVIG Formation Manifest",
    "type": "object",
    "required": ["id", "name", "version", "description", "agents", "default_agent"],
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_-]*$",
        },
        "name": {"type": "string", "minLength": 1},
        "version": {
            "type": "string",
            "pattern": r"^\d+\.\d+\.\d+$",
        },
        "description": {"type": "string", "minLength": 1},
        "agents": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "default_agent": {"type": "string"},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "api_connectors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "url_pattern": {"type": "string"},
                    "auth_type": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
            "default": [],
        },
        "brief_templates": {
            "type": "array",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "required": ["file", "name", "prompt"],
                        "properties": {
                            "file": {"type": "string"},
                            "name": {"type": "string"},
                            "agent": {"type": "string"},
                            "prompt": {"type": "string"}
                        },
                        "additionalProperties": False
                    }
                ]
            },
            "default": [],
        },
    },
    "additionalProperties": False,
}

PROFILE_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "NAVIG Workspace Profile",
    "type": "object",
    "required": ["profile"],
    "properties": {
        "version": {"type": ["integer", "string"], "default": 1},
        "profile": {"type": "string", "minLength": 1},
        "overrides": {"type": "object", "default": {}},
    },
    "additionalProperties": False,
}


def _validate_with_jsonschema(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Validate data against JSON Schema. Returns list of error messages."""
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        return [e.message for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path))]
    except ImportError:
        return _validate_manually(data, schema)


def _validate_manually(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Fallback validation without jsonschema library."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["Root must be an object"]

    for req in schema.get("required", []):
        if req not in data:
            errors.append(f"Missing required field: '{req}'")

    props = schema.get("properties", {})
    for key, value in data.items():
        if key not in props and schema.get("additionalProperties") is False:
            errors.append(f"Unknown field: '{key}'")
            continue
        if key in props:
            prop_schema = props[key]
            expected_type = prop_schema.get("type")
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{key}' must be a string")
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(f"Field '{key}' must be an integer")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{key}' must be a number")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{key}' must be an array")
            elif expected_type == "object" and not isinstance(value, dict):
                errors.append(f"Field '{key}' must be an object")

            if expected_type == "string" and isinstance(value, str):
                min_len = prop_schema.get("minLength", 0)
                if len(value) < min_len:
                    errors.append(f"Field '{key}' must be at least {min_len} characters")

            if expected_type == "array" and isinstance(value, list):
                min_items = prop_schema.get("minItems", 0)
                if len(value) < min_items:
                    errors.append(f"Field '{key}' must have at least {min_items} items")

    return errors


def validate_agent_data(data: Dict[str, Any], path: Optional[Path] = None) -> AgentSpec:
    """Validate agent data and return AgentSpec. Raises FormationValidationError."""
    errors = _validate_with_jsonschema(data, AGENT_SCHEMA)
    if errors:
        raise FormationValidationError("Invalid agent file", path=path, errors=errors)
    return AgentSpec.from_dict(data, source_path=path)


def validate_agent_file(path: Path) -> AgentSpec:
    """Load and validate a .agent.json file. Returns AgentSpec."""
    if not path.exists():
        raise FormationValidationError(f"Agent file not found: {path}", path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FormationValidationError(f"Invalid JSON in agent file: {e}", path=path) from e
    return validate_agent_data(data, path=path)


def validate_formation_data(data: Dict[str, Any], path: Optional[Path] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Validate formation data. Returns (data, errors)."""
    errors = _validate_with_jsonschema(data, FORMATION_SCHEMA)

    # Cross-field validation
    if not errors:
        agents = data.get("agents", [])
        default = data.get("default_agent", "")
        if default and default not in agents:
            errors.append(f"default_agent '{default}' is not listed in agents[]")

    return data, errors


def validate_formation_file(path: Path) -> Formation:
    """Load and validate a formation.json file. Returns Formation."""
    if not path.exists():
        raise FormationValidationError(f"Formation manifest not found: {path}", path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FormationValidationError(f"Invalid JSON in formation manifest: {e}", path=path) from e

    _, errors = validate_formation_data(data, path=path)
    if errors:
        raise FormationValidationError("Invalid formation manifest", path=path, errors=errors)

    return Formation.from_dict(data, source_path=path)


def validate_profile_data(data: Dict[str, Any], path: Optional[Path] = None) -> List[str]:
    """Validate profile.json data. Returns list of errors."""
    return _validate_with_jsonschema(data, PROFILE_SCHEMA)
