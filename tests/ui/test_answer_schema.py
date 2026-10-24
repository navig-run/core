"""The versioned wire schema and its validator agree, and reject bad answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.ui.answer import (
    ANSWER_FIELDS,
    ANSWER_SCHEMA_VERSION,
    ANSWER_WIRE_FIELDS,
    DIRECTIVE_TYPES,
    Answer,
    validate_answer,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "navig" / "ui" / "answer.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def valid_payload() -> dict:
    return {
        "schema_version": "1",
        "ok": True,
        "speech": "Desk lamp is off.",
        "display_text": "Desk lamp: off",
        "table": {"columns": ["Device", "State"], "rows": [["Desk lamp", "off"]]},
        "json": {"device": "govee:desk-lamp", "power": "off"},
        "directives": [{"type": "open", "params": {"target": "govee:desk-lamp"}}],
        "suggestions": ["navig endpoints show govee:desk-lamp"],
        "data": {"device": "Desk lamp", "state": "off"},
        "plan": {"token": "plan-abc123", "tool": "home.off"},
        "intent": "home.off",
    }


def test_the_schema_file_is_valid_versioned_json() -> None:
    schema = load_schema()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["properties"]["schema_version"]["const"] == ANSWER_SCHEMA_VERSION
    assert set(schema["required"]) == {"ok", "speech", "display_text", "data"}
    assert set(schema["properties"]) == set(ANSWER_WIRE_FIELDS)
    assert schema["additionalProperties"] is False


def test_the_schema_agrees_with_the_python_contract() -> None:
    schema = load_schema()
    assert set(ANSWER_FIELDS) == set(ANSWER_WIRE_FIELDS) - {"schema_version"}
    directive_schema = schema["properties"]["directives"]["items"]
    assert directive_schema["required"] == ["type"]
    assert set(directive_schema["properties"]["type"]["enum"]) == set(DIRECTIVE_TYPES)
    table_schema = schema["properties"]["table"]
    assert set(table_schema["required"]) == {"columns", "rows"}
    assert table_schema["properties"]["rows"]["items"]["items"]["type"] == "string"


def test_a_valid_payload_and_an_answer_object_both_pass() -> None:
    assert validate_answer(valid_payload()) == []
    assert validate_answer(Answer.from_dict(valid_payload())) == []


@pytest.mark.parametrize(("mutate", "expected"), [
    (lambda payload: payload.pop("speech"), "speech"),
    (lambda payload: payload.pop("display_text"), "display_text"),
    (lambda payload: payload.pop("ok"), "ok"),
    (lambda payload: payload.update(data=None), "data"),
    (lambda payload: payload.update(suggestions="next"), "suggestions"),
    (lambda payload: payload.update(suggestions=[1]), "suggestions"),
    (lambda payload: payload.update(directives=[{"type": "send"}]), "type must be one of"),
    (lambda payload: payload["table"]["rows"].append(["too", "wide", "row"]), "cells for"),
    (lambda payload: payload["table"]["rows"].append([1, "off"]), "cells must be strings"),
    (lambda payload: payload.update(schema_version="2"), "schema_version"),
    (lambda payload: payload.update(permissions=["control"]), "unknown answer fields"),
])
def test_the_validator_rejects_each_broken_shape(mutate, expected: str) -> None:
    payload = valid_payload()
    mutate(payload)
    problems = validate_answer(payload)
    assert any(expected in problem for problem in problems), problems


def test_a_legacy_directive_kind_is_tolerated_but_type_is_canonical() -> None:
    payload = valid_payload()
    payload["directives"] = [{"kind": "highlight", "params": {}}]
    assert validate_answer(payload) == []
    payload["directives"] = [{"kind": "unknown", "params": {}}]
    assert validate_answer(payload)


def test_a_non_mapping_is_rejected_not_raised() -> None:
    assert validate_answer("not an answer") == ["answer must be a mapping or an Answer"]
