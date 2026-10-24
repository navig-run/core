"""The frozen endpoint contract: vocabulary, round-trips, and rejection rules."""

from __future__ import annotations

import json

import pytest

from navig.endpoints import model
from navig.endpoints.model import (
    CAPABILITY_TYPES,
    DIRECTIVES,
    OUT_OF_SCOPE_CAPABILITY_TYPES,
    PROPERTY_INSTANCES,
    PROPERTY_TYPES,
    Capability,
    CapabilityMeta,
    Endpoint,
    EndpointInfo,
    EndpointStatus,
    validate_endpoint,
)

PLAN_EXAMPLE = {
    "id": "govee:desk-lamp",
    "type": "devices.types.light",
    "info": {"manufacturer": "", "model": "H6159", "hw": "", "sw": ""},
    "status": {"online": True, "updated_at": "2026-10-22T00:00:00Z"},
    "room": "office",
    "capabilities": [
        {
            "type": "devices.capabilities.on_off",
            "instance": "on",
            "parameters": {},
            "state": {"on": True},
            "meta": {
                "retrievable": True,
                "reportable": True,
                "supported_directives": ["on_off"],
                "supported_events": [],
            },
        },
        {
            "type": "devices.capabilities.range",
            "instance": "brightness",
            "parameters": {
                "range": {"min": 1, "max": 100, "precision": 1},
                "unit": "unit.percent",
            },
            "state": {"value": 60},
            "meta": {
                "retrievable": True,
                "reportable": True,
                "supported_directives": ["set_range"],
                "supported_events": [],
            },
        },
    ],
}


def test_the_vocabulary_is_the_frozen_five_plus_three() -> None:
    assert CAPABILITY_TYPES == (
        "devices.capabilities.on_off",
        "devices.capabilities.range",
        "devices.capabilities.mode",
        "devices.capabilities.color_setting",
        "devices.capabilities.toggle",
    )
    assert PROPERTY_TYPES == (
        "devices.properties.temperature",
        "devices.properties.humidity",
        "devices.properties.battery_level",
    )
    assert PROPERTY_INSTANCES == ("temperature", "humidity", "battery_level")
    assert DIRECTIVES == ("on_off", "set_range", "set_mode", "set_color", "toggle")
    assert model.META_KEYS == (
        "retrievable",
        "reportable",
        "supported_directives",
        "supported_events",
    )


def test_the_alice_superset_is_explicitly_out_of_scope() -> None:
    """The provider API has nine capability types; five ship, four are named."""
    assert OUT_OF_SCOPE_CAPABILITY_TYPES == (
        "devices.capabilities.custom.button",
        "devices.capabilities.quasar",
        "devices.capabilities.quasar.server_action",
        "devices.capabilities.video_stream",
    )
    assert not set(OUT_OF_SCOPE_CAPABILITY_TYPES) & set(CAPABILITY_TYPES)
    assert len(OUT_OF_SCOPE_CAPABILITY_TYPES) + len(CAPABILITY_TYPES) == 9


def test_an_out_of_scope_provider_type_is_rejected_not_projected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["capabilities"][0]["type"] = "devices.capabilities.quasar"
    problems = validate_endpoint(payload)
    assert any("undeclared capability type" in problem for problem in problems), problems


def test_meta_never_claims_reportability_by_default() -> None:
    meta = CapabilityMeta()
    assert meta.retrievable is True
    assert meta.reportable is False
    assert meta.supported_events == ()


def test_provider_addressing_is_declared_forbidden() -> None:
    for field in ("address", "entity_id", "dev_id", "token_env", "mac", "dps", "scale", "ir"):
        assert field in model.FORBIDDEN_FIELDS


def test_no_auth_or_permission_field_exists_on_the_endpoint_contract() -> None:
    assert "auth" not in model.ENDPOINT_FIELDS
    assert "permissions" not in model.ENDPOINT_FIELDS
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["auth"] = {"token": "secret"}
    payload["permissions"] = ["control"]
    problems = validate_endpoint(payload)
    assert any("unknown endpoint fields" in problem for problem in problems), problems


def test_the_plan_example_validates_clean() -> None:
    assert validate_endpoint(PLAN_EXAMPLE) == []


def test_an_endpoint_round_trips_through_json() -> None:
    endpoint = Endpoint(
        id="ha:living-room-ac",
        type="devices.types.thermostat",
        info=EndpointInfo(model="AC200"),
        status=EndpointStatus(online=True, updated_at="2026-10-22T00:00:00Z"),
        room="living room",
        capabilities=[
            Capability(
                type="devices.capabilities.mode",
                instance="mode",
                state={"mode": "cool"},
                meta=CapabilityMeta(supported_directives=("set_mode",)),
            ),
        ],
    )
    payload = json.loads(json.dumps(endpoint.as_dict()))
    assert Endpoint.from_dict(payload) == endpoint
    assert validate_endpoint(payload) == []


def test_an_undeclared_capability_type_is_rejected() -> None:
    payload = {
        "id": "govee:desk-lamp",
        "type": "devices.types.light",
        "info": {},
        "status": {"online": True, "updated_at": "x"},
        "room": "",
        "capabilities": [
            {"type": "devices.capabilities.volume", "instance": "volume",
             "parameters": {}, "state": {}, "meta": {"supported_directives": []}},
        ],
    }
    problems = validate_endpoint(payload)
    assert any("undeclared capability type" in problem for problem in problems), problems


def test_an_undeclared_meta_key_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["capabilities"][0]["meta"]["colour"] = "warm"
    problems = validate_endpoint(payload)
    assert any("meta: undeclared keys" in problem for problem in problems), problems


def test_an_undeclared_directive_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["capabilities"][0]["meta"]["supported_directives"] = ["set_volume"]
    problems = validate_endpoint(payload)
    assert any("unknown directive" in problem for problem in problems), problems


def test_an_undeclared_event_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["capabilities"][0]["meta"]["supported_events"] = ["battery_low"]
    problems = validate_endpoint(payload)
    assert any("unsupported event" in problem for problem in problems), problems


def test_an_unknown_top_level_field_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["provider"] = "govee"
    problems = validate_endpoint(payload)
    assert any("unknown endpoint fields" in problem for problem in problems), problems


def test_a_provider_specific_field_nested_anywhere_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["info"]["address"] = "192.0.2.10"
    payload["capabilities"][0]["parameters"]["dps"] = {"1": "on"}
    problems = validate_endpoint(payload)
    joined = "\n".join(problems)
    assert "address" in joined and "dps" in joined, problems


def test_a_read_only_property_must_not_list_directives() -> None:
    payload = {
        "id": "ha:thermometer",
        "type": "devices.types.sensor",
        "info": {},
        "status": {"online": True, "updated_at": "x"},
        "room": "",
        "capabilities": [{
            "type": "devices.properties.temperature",
            "instance": "temperature",
            "parameters": {},
            "state": {"value": 21.0},
            "meta": {"supported_directives": ["set_range"]},
        }],
    }
    problems = validate_endpoint(payload)
    assert any("must not list directives" in problem for problem in problems), problems


def test_a_property_type_requires_a_property_instance() -> None:
    payload = {
        "id": "ha:thermometer",
        "type": "devices.types.sensor",
        "info": {},
        "status": {"online": True, "updated_at": "x"},
        "room": "",
        "capabilities": [{
            "type": "devices.properties.temperature",
            "instance": "target_temp",
            "parameters": {},
            "state": {"value": 21.0},
            "meta": {"supported_directives": []},
        }],
    }
    problems = validate_endpoint(payload)
    assert any("unknown property instance" in problem for problem in problems), problems


def test_a_range_capability_may_use_the_temperature_instance_name() -> None:
    """``temperature`` is both a range instance and a property instance."""
    payload = {
        "id": "ha:living-room-ac",
        "type": "devices.types.thermostat",
        "info": {},
        "status": {"online": True, "updated_at": "x"},
        "room": "",
        "capabilities": [{
            "type": "devices.capabilities.range",
            "instance": "temperature",
            "parameters": {"range": {"min": 16, "max": 32, "precision": 0.5},
                           "unit": "unit.temperature.celsius"},
            "state": {"value": 22.0},
            "meta": {"supported_directives": ["set_range"]},
        }],
    }
    assert validate_endpoint(payload) == []


@pytest.mark.parametrize("endpoint_id", ["desk-lamp", "govee:", ":desk-lamp", "Govee:Desk"])
def test_the_id_must_be_provider_namespaced(endpoint_id: str) -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["id"] = endpoint_id
    problems = validate_endpoint(payload)
    assert any("must be '<provider>:<device id>'" in problem for problem in problems), problems


def test_a_non_devices_type_is_rejected() -> None:
    payload = json.loads(json.dumps(PLAN_EXAMPLE))
    payload["type"] = "light"
    problems = validate_endpoint(payload)
    assert any("devices.types" in problem for problem in problems), problems


def test_a_non_mapping_is_rejected_not_raised() -> None:
    assert validate_endpoint("not an endpoint") == ["endpoint must be a mapping or an Endpoint"]
