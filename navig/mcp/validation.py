"""Argument validation for MCP tool calls.

The server advertises an ``inputSchema`` for every tool, and until this module existed
nothing ever checked a caller's arguments against it. Two failures followed from that:

* an **unknown argument was silently ignored** — a client that mistyped ``host`` as
  ``hostname`` got a successful-looking reply for the wrong question;
* a **wrong type escaped as a Python traceback message** (``'int' object has no
  attribute 'strip'``), which tells a model nothing it can act on.

Deliberately dependency-free and shallow: JSON-Schema features beyond ``type``,
``required``, ``additionalProperties`` and ``enum`` are ignored, and nested objects are
only checked to be objects. That covers every tool in this server today; a tool that
needs deeper validation should validate inside its handler.

A malformed schema must never break a call — the tool ran before this module existed,
so the failure mode here is "log and let it through", never "refuse everything".
"""

from __future__ import annotations

from typing import Any

#: JSON type name → the Python types that satisfy it. ``bool`` is excluded from the
#: numeric types on purpose: JSON has no bool/int overlap, so ``true`` is not an integer
#: even though Python's ``bool`` subclasses ``int``.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


class Problem(dict):
    """One rejected argument: ``{"field": str | None, "detail": str}``."""


def _type_ok(value: Any, expected: str) -> bool:
    if value is None:  # an explicit null is "absent", not a type error
        return True
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    types = _JSON_TYPES.get(expected)
    if types is None:  # a type we do not model (oneOf, anyOf, const, …)
        return True
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, types)


def _describe(value: Any) -> str:
    """A JSON-ish name for what the caller actually sent."""
    if value is None:
        return "null"
    for name in ("boolean", "integer", "number", "string", "array", "object"):
        if _type_ok(value, name):
            return name
    return type(value).__name__


def validate_arguments(schema: Any, arguments: Any) -> list[Problem]:
    """Return every way *arguments* violates *schema*; empty list means it is fine."""
    if not isinstance(schema, dict):
        return []  # tool double without a schema: nothing to enforce
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return [Problem(field=None, detail=f"arguments must be an object, got {_describe(arguments)}")]

    problems: list[Problem] = []

    required = schema.get("required") or []
    if isinstance(required, list):
        for name in required:
            if name not in arguments:
                expected = (properties.get(name) or {}).get("type", "value")
                problems.append(Problem(field=name, detail=f"missing required argument '{name}' ({expected})"))

    strict = schema.get("additionalProperties") is False
    for name, value in arguments.items():
        if name not in properties:
            if strict:
                allowed = ", ".join(sorted(properties)) or "none"
                problems.append(
                    Problem(field=name, detail=f"unknown argument '{name}' (accepted: {allowed})")
                )
            continue

        prop = properties.get(name)
        if not isinstance(prop, dict):
            continue

        expected = prop.get("type")
        if isinstance(expected, str) and not _type_ok(value, expected):
            problems.append(
                Problem(
                    field=name,
                    detail=f"argument '{name}' must be {expected}, got {_describe(value)}",
                )
            )
            continue

        enum = prop.get("enum")
        if isinstance(enum, list) and value is not None and value not in enum:
            problems.append(
                Problem(
                    field=name,
                    detail=f"argument '{name}' must be one of {', '.join(map(str, enum))}, got {value!r}",
                )
            )

    return problems


def summarise(problems: list[Problem], limit: int = 3) -> str:
    """One line naming the first problems — what a model needs to retry correctly."""
    if not problems:
        return ""
    shown = "; ".join(p.get("detail", "") for p in problems[:limit])
    extra = len(problems) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")
