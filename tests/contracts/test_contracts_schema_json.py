"""JSON-schema ↔ dataclass drift guard + malformed-input contract tests.

``navig/schemas/{node,mission,execution_receipt}.schema.json`` declare the
wire contract (``additionalProperties: false``), but nothing compared them to
the dataclasses in ``navig.contracts`` — a field added on either side would
drift silently. These tests pin:

  - schema ``properties`` == dataclass fields (both directions),
  - schema ``required`` ⊆ dataclass fields,
  - schema enum values == the Python enums,
  - ``from_dict``/``from_json`` behaviour on malformed input,
  - serialize→parse→serialize string stability (idempotent round-trip).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

import navig
from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome
from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.node import Node, NodeOS, NodeStatus

pytestmark = pytest.mark.unit


def _schema(name: str) -> dict:
    path = Path(navig.__file__).resolve().parent / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


CASES = [
    ("node.schema.json", Node),
    ("mission.schema.json", Mission),
    ("execution_receipt.schema.json", ExecutionReceipt),
]


# ── Schema ↔ dataclass drift ─────────────────────────────────────────


@pytest.mark.parametrize(("schema_name", "cls"), CASES)
def test_schema_properties_match_dataclass_fields(schema_name, cls):
    schema = _schema(schema_name)
    schema_fields = set(schema["properties"])
    dataclass_fields = {f.name for f in dataclasses.fields(cls)}
    assert schema_fields == dataclass_fields, (
        f"{schema_name} drifted from {cls.__name__}: "
        f"schema-only={sorted(schema_fields - dataclass_fields)} "
        f"dataclass-only={sorted(dataclass_fields - schema_fields)}"
    )


@pytest.mark.parametrize(("schema_name", "cls"), CASES)
def test_schema_required_fields_exist_on_dataclass(schema_name, cls):
    schema = _schema(schema_name)
    dataclass_fields = {f.name for f in dataclasses.fields(cls)}
    missing = set(schema["required"]) - dataclass_fields
    assert not missing, f"{schema_name} requires fields {cls.__name__} lacks: {sorted(missing)}"


@pytest.mark.parametrize(("schema_name", "cls"), CASES)
def test_schema_rejects_unknown_fields_like_from_dict(schema_name, cls):
    """additionalProperties: false in the schema ↔ from_dict raising on extras."""
    assert _schema(schema_name).get("additionalProperties") is False


def test_node_schema_enums_match_python_enums():
    schema = _schema("node.schema.json")
    assert set(schema["properties"]["os"]["enum"]) == {e.value for e in NodeOS}
    assert set(schema["properties"]["status"]["enum"]) == {e.value for e in NodeStatus}


def test_mission_schema_status_enum_matches():
    schema = _schema("mission.schema.json")
    assert set(schema["properties"]["status"]["enum"]) == {e.value for e in MissionStatus}


def test_receipt_schema_outcome_enum_matches():
    schema = _schema("execution_receipt.schema.json")
    assert set(schema["properties"]["outcome"]["enum"]) == {e.value for e in ReceiptOutcome}


# ── Malformed input ──────────────────────────────────────────────────


def test_node_invalid_os_raises():
    with pytest.raises(ValueError):
        Node.from_dict({"hostname": "h", "os": "beos"})


def test_node_invalid_status_raises():
    with pytest.raises(ValueError):
        Node.from_dict({"hostname": "h", "status": "exploded"})


def test_node_unknown_field_raises():
    # Strict by design — mirrors additionalProperties: false in the schema.
    with pytest.raises(TypeError):
        Node.from_dict({"hostname": "h", "bogus": 1})


def test_mission_invalid_status_raises():
    with pytest.raises(ValueError):
        Mission.from_dict({"title": "t", "status": "exploded"})


def test_mission_unknown_field_raises():
    with pytest.raises(TypeError):
        Mission.from_dict({"title": "t", "bogus": 1})


def test_receipt_missing_outcome_raises():
    with pytest.raises(KeyError):
        ExecutionReceipt.from_dict(
            {
                "mission_id": "m",
                "node_id": "n",
                "title": "t",
                "capability": "c",
                "completed_at": "2026-01-01T00:00:00+00:00",
            }
        )


def test_receipt_invalid_outcome_raises():
    with pytest.raises(ValueError):
        ExecutionReceipt.from_dict(
            {
                "mission_id": "m",
                "node_id": "n",
                "title": "t",
                "capability": "c",
                "outcome": "exploded",
                "completed_at": "2026-01-01T00:00:00+00:00",
            }
        )


@pytest.mark.parametrize("cls", [Node, Mission, ExecutionReceipt])
def test_from_json_malformed_raises(cls):
    with pytest.raises(json.JSONDecodeError):
        cls.from_json("{not json")


# ── Round-trip string stability ──────────────────────────────────────


def _instances():
    node = Node(hostname="h", os=NodeOS.LINUX, capabilities=["ssh"])
    node.go_online()
    mission = Mission(title="t", node_id=node.node_id, capability="llm")
    mission.start()
    mission.succeed(result={"ok": True})
    receipt = ExecutionReceipt.from_mission(
        mission_id=mission.mission_id,
        node_id=node.node_id,
        title=mission.title,
        capability=mission.capability,
        outcome=ReceiptOutcome.SUCCEEDED,
        completed_at=mission.completed_at or "2026-01-01T00:00:00+00:00",
        started_at=mission.started_at,
        duration_secs=mission.duration_secs,
    )
    return [node, mission, receipt]


def test_serialize_parse_serialize_is_stable():
    """serialize(parse(serialized)) must be byte-identical on repeated runs."""
    for obj in _instances():
        raw = obj.to_json()
        assert type(obj).from_json(raw).to_json() == raw
