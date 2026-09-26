"""Guard: every shipped tool definition satisfies `tool.schema.json`, and the schema is honest.

The bug this exists for
-----------------------
`navig/builtin/tools/tool.schema.json` declared ``required: [id, name, runtime]``. Sixteen
of the fifty-eight ``*.tool.json`` files beside it have no ``runtime`` at all -- they are a
second family, wrappers over a navig CLI verb, keyed by ``execution`` instead. Both
families are 100% consistent within themselves; the SCHEMA was the only wrong thing. It
also omitted half of the first family's own keys (entrypoint, auth, rateLimits,
errorPolicy).

Nothing validated against it, so nothing noticed. Forty-two files point at it via
``$schema``, which means editors were showing red squiggles on correct data and would have
shown none on a genuinely malformed file missing ``entrypoint``.

Why a hand-rolled check
-----------------------
``jsonschema`` is not a dependency and one should not be added for a test. The schema uses
five constructs -- ``type``, ``required``, ``enum``, ``oneOf``, ``not`` -- and this evaluates
exactly those, deliberately not a general validator. A construct the schema does not use is
rejected loudly rather than silently ignored, because a keyword this file cannot evaluate
would otherwise turn into a schema that constrains nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "core" / "navig" / "builtin" / "tools"
SCHEMA = TOOLS / "tool.schema.json"

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "integer": int, "null": type(None),
}
# Every keyword this evaluator understands. Anything else in the schema fails the guard.
_KNOWN = {"$schema", "$comment", "type", "required", "properties", "enum", "oneOf", "not", "description"}

# Measured at 58 definitions. Far below that so churn cannot trip it, far above zero.
_MIN_DEFINITIONS = 40


def _violations(schema: dict[str, Any], value: Any, where: str = "$") -> list[str]:
    unknown = set(schema) - _KNOWN
    assert not unknown, f"{SCHEMA.name} uses keyword(s) this guard cannot evaluate: {sorted(unknown)}"
    out: list[str] = []
    if "type" in schema and not isinstance(value, _TYPES[schema["type"]]):
        return [f"{where}: expected {schema['type']}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{where}: missing required `{key}`")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                out.extend(_violations(sub, value[key], f"{where}.{key}"))
    if "not" in schema and not _violations(schema["not"], value, where):
        out.append(f"{where}: matches a forbidden shape ({json.dumps(schema['not'])})")
    if "oneOf" in schema:
        passing = [i for i, branch in enumerate(schema["oneOf"]) if not _violations(branch, value, where)]
        if len(passing) != 1:
            names = [b.get("$comment", f"branch {i}") for i, b in enumerate(schema["oneOf"])]
            out.append(f"{where}: must match exactly one family, matched {len(passing)} of {names}")
    return out


def _definitions() -> list[Path]:
    return sorted(TOOLS.glob("*.tool.json"))


def test_the_scan_actually_reads_the_tools() -> None:
    """Stated as a presence: a scan that found nothing reports no violations."""
    assert SCHEMA.is_file(), f"{SCHEMA} is missing; the guard has no subject"
    files = _definitions()
    assert len(files) >= _MIN_DEFINITIONS, (
        f"only {len(files)} tool definitions found under {TOOLS} (expected >= "
        f"{_MIN_DEFINITIONS}) -- the scan is mis-rooted and read almost nothing."
    )


def test_every_tool_definition_satisfies_the_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    bad: dict[str, list[str]] = {}
    for path in _definitions():
        problems = _violations(schema, json.loads(path.read_text(encoding="utf-8")))
        if problems:
            bad[path.name] = problems
    assert not bad, (
        "these tool definitions do not satisfy tool.schema.json:\n"
        + "\n".join(f"    {n}\n" + "\n".join(f"        {p}" for p in ps) for n, ps in bad.items())
        + "\n\nEither the definition is malformed, or the schema no longer describes what "
        "ships. Two families are legitimate (runtime / execution); a third is a decision."
    )


def test_the_evaluator_has_teeth() -> None:
    """The evaluator is hand-rolled, so prove it rejects what it must, on synthetic data."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    ok_runtime = {"id": "t", "name": "t", "description": "d", "version": "1",
                  "runtime": "python", "entrypoint": "x.py", "inputs": [], "outputs": []}
    ok_exec = {"id": "t", "name": "t", "description": "d", "version": "1",
               "execution": {"type": "cli", "target": "navig x"}, "parameters": {}, "requires_approval": False}
    assert not _violations(schema, ok_runtime)
    assert not _violations(schema, ok_exec)
    # Neither family: the shape the old schema would have rejected too.
    assert _violations(schema, {"id": "t", "name": "t", "description": "d", "version": "1"})
    # BOTH families at once is as wrong as neither.
    assert _violations(schema, {**ok_runtime, "execution": ok_exec["execution"],
                                "parameters": {}, "requires_approval": True})
    # A wrong enum inside the nested object is seen.
    assert _violations(schema, {**ok_exec, "execution": {"type": "http", "target": "x"}})
    # A wrong TYPE is seen, not just a missing key.
    assert _violations(schema, {**ok_runtime, "inputs": "not-a-list"})
