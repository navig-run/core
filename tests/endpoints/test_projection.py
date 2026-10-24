"""The pure projection: home tags → typed capabilities, live snapshot → state."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from navig.endpoints.model import validate_endpoint
from navig.endpoints.projection import (
    KIND_TYPES,
    MAPPED_TAGS,
    OUT_OF_SCOPE_TAGS,
    project,
    unmapped_capabilities,
)
from tests.endpoints.fixtures import HA_CLIMATE, READINESS, snapshots

LIVE_STATE = {
    "device": "thing",
    "ok": True,
    "power": "on",
    "state": {
        "power": "on",
        "attributes": {
            "brightness_pct": 60,
            "color_temp_kelvin": 3000,
            "rgb_color": [255, 136, 0],
            "target_temp": 22.0,
            "mode": "cool",
            "fan": "auto",
            "humidity": 45,
            "current_temp": 18.5,
        },
    },
}

BATTERY_STATE = {
    "device": "thing",
    "ok": True,
    "power": "on",
    "state": {"power": "on", "attributes": {"battery": 87}},
}


def _device(tag: str, *, kind: str = "light", **extra) -> dict:
    device = {
        "id": "thing",
        "name": "Thing",
        "kind": kind,
        "provider": "govee",
        "room": "office",
        "capabilities": [tag],
    }
    device.update(extra)
    return device


def _capabilities(endpoint) -> list[tuple[str, str]]:
    return [(capability.type, capability.instance) for capability in endpoint.capabilities]


TAG_EXPECTATIONS = {
    "power": ("devices.capabilities.on_off", "on"),
    "toggle": ("devices.capabilities.toggle", "on"),
    "brightness": ("devices.capabilities.range", "brightness"),
    "color": ("devices.capabilities.color_setting", "rgb"),
    "color_temp": ("devices.capabilities.color_setting", "temperature_k"),
    "target_temp": ("devices.capabilities.range", "temperature"),
    "mode": ("devices.capabilities.mode", "mode"),
    "fan": ("devices.capabilities.mode", "fan_speed"),
    "humidity": ("devices.properties.humidity", "humidity"),
}
OUT_OF_SCOPE = ("vacuum", "media", "volume", "ir", "dp")


@pytest.mark.parametrize("tag", sorted(TAG_EXPECTATIONS))
def test_each_mapped_home_tag_projects_to_its_typed_record(tag: str) -> None:
    endpoint = project([_device(tag)], [LIVE_STATE])[0]
    assert _capabilities(endpoint) == [TAG_EXPECTATIONS[tag]]


@pytest.mark.parametrize("tag", OUT_OF_SCOPE)
def test_each_out_of_scope_tag_is_counted_not_dropped(tag: str) -> None:
    devices = [_device(tag)]
    endpoints = project(devices, [LIVE_STATE])
    assert len(endpoints) == 1, "the device still appears"
    assert _capabilities(endpoints[0]) == []
    assert unmapped_capabilities(devices) == {tag: 1}


def test_the_fifteen_home_tags_are_accounted_for() -> None:
    all_tags = {
        "power", "toggle", "brightness", "color", "color_temp", "target_temp", "mode",
        "fan", "humidity", "vacuum", "volume", "media", "ir", "dp", "sensor",
    }
    assert all_tags == set(MAPPED_TAGS) | set(OUT_OF_SCOPE) | {"sensor"}
    assert OUT_OF_SCOPE_TAGS == frozenset(OUT_OF_SCOPE)


def test_the_sensor_tag_projects_temperature_for_a_sensor_kind() -> None:
    endpoint = project([_device("sensor", kind="sensor")], [LIVE_STATE])[0]
    assert _capabilities(endpoint) == [("devices.properties.temperature", "temperature")]
    assert endpoint.capabilities[0].state == {"value": 18.5}
    assert endpoint.capabilities[0].meta.supported_directives == ()


def test_the_sensor_tag_projects_humidity_when_the_state_carries_it() -> None:
    endpoint = project([_device("sensor", kind="switch")], [LIVE_STATE])[0]
    assert _capabilities(endpoint) == [("devices.properties.humidity", "humidity")]


def test_a_sensor_tag_without_a_reading_adds_no_record() -> None:
    snapshot = {"device": "thing", "ok": True, "power": "on", "state": {"attributes": {}}}
    endpoint = project([_device("sensor", kind="switch")], [snapshot])[0]
    assert endpoint.capabilities == []


@pytest.mark.parametrize("kind", sorted(KIND_TYPES))
def test_each_kind_projects_its_devices_type(kind: str) -> None:
    endpoint = project([_device("power", kind=kind)], [LIVE_STATE])[0]
    assert endpoint.type == KIND_TYPES[kind]


def test_an_unknown_kind_falls_back_to_other() -> None:
    endpoint = project([_device("power", kind="gadget")], [LIVE_STATE])[0]
    assert endpoint.type == "devices.types.other"


def test_the_id_is_provider_namespaced_and_provider_defaults_to_manual() -> None:
    device = {"id": "lamp", "name": "Lamp", "kind": "light", "capabilities": ["power"]}
    endpoint = project([device], [])[0]
    assert endpoint.id == "manual:lamp"


def test_online_comes_from_the_live_snapshot_only() -> None:
    device = [{"id": "lamp", "name": "Lamp", "kind": "light", "provider": "govee",
               "capabilities": ["power"]}]
    assert project(device, [])[0].status.online is None, "no snapshot must not invent online"
    offline = [{"device": "lamp", "ok": False, "power": "unknown", "state": {}}]
    assert project(device, offline)[0].status.online is False


def test_updated_at_is_frozen_iso_utc() -> None:
    fixed = datetime(2026, 10, 22, 12, 34, 56, tzinfo=timezone.utc)
    endpoint = project([_device("power")], [LIVE_STATE], now=fixed)[0]
    assert endpoint.status.updated_at == "2026-10-22T12:34:56Z"


def test_a_naive_now_is_treated_as_utc() -> None:
    endpoint = project(
        [_device("power")], [LIVE_STATE], now=datetime(2026, 10, 22, 1, 2, 3))[0]
    assert endpoint.status.updated_at == "2026-10-22T01:02:03Z"


def test_live_values_are_mapped_into_state() -> None:
    endpoint = project([_device("brightness")], [LIVE_STATE])[0]
    assert endpoint.capabilities[0].state == {"value": 60}
    assert endpoint.capabilities[0].parameters["unit"] == "unit.percent"
    rgb = project([_device("color")], [LIVE_STATE])[0].capabilities[0]
    assert rgb.state == {"rgb": "#ff8800"}
    warm = project([_device("color_temp")], [LIVE_STATE])[0].capabilities[0]
    assert warm.state == {"temperature_k": 3000}
    target = project([_device("target_temp")], [LIVE_STATE])[0].capabilities[0]
    assert target.state == {"value": 22.0}
    assert target.parameters["unit"] == "unit.temperature.celsius"
    mode = project([_device("mode")], [LIVE_STATE])[0].capabilities[0]
    assert mode.state == {"mode": "cool"}
    fan = project([_device("fan")], [LIVE_STATE])[0].capabilities[0]
    assert fan.state == {"mode": "auto"}


def test_every_directive_is_declared_on_the_capability_it_changes() -> None:
    endpoints = [
        project([_device(tag, id=f"thing-{tag}")], [LIVE_STATE])[0]
        for tag in ("power", "toggle", "brightness", "color", "color_temp",
                    "target_temp", "mode", "fan")
    ]
    directives = {
        name
        for endpoint in endpoints
        for capability in endpoint.capabilities
        for name in capability.meta.supported_directives
    }
    assert directives == {"on_off", "toggle", "set_range", "set_color", "set_mode"}


def test_a_battery_reading_becomes_a_battery_property_even_without_a_tag() -> None:
    endpoint = project([_device("power")], [BATTERY_STATE])[0]
    battery = [
        capability for capability in endpoint.capabilities
        if capability.type == "devices.properties.battery_level"
    ]
    assert len(battery) == 1
    assert battery[0].state == {"value": 87}
    assert battery[0].meta.supported_directives == ()
    assert (battery[0].type, battery[0].instance) in _capabilities(endpoint)


def test_the_fixture_projects_one_endpoint_per_device_across_two_providers() -> None:
    payload = snapshots()
    endpoints = project([_device("power"), HA_CLIMATE], payload)
    providers = {endpoint.id.split(":", 1)[0] for endpoint in endpoints}
    assert providers == {"govee", "ha"}
    govee = next(endpoint for endpoint in endpoints if endpoint.id.startswith("govee:"))
    assert govee.id == "govee:thing"
    assert validate_endpoint(govee) == []


def test_a_device_with_no_snapshot_still_appears_with_empty_state() -> None:
    endpoint = project([_device("power")], [])[0]
    assert endpoint.status.online is None
    assert endpoint.capabilities[0].state == {}


def test_reportability_and_events_are_never_invented() -> None:
    endpoints = project([_device("power"), HA_CLIMATE], snapshots())
    assert endpoints
    for endpoint in endpoints:
        for capability in endpoint.capabilities:
            assert capability.meta.retrievable is True
            assert capability.meta.reportable is False, (
                "navig-home declares no reportability — the projection must not claim it")
            assert capability.meta.supported_events == ()


def test_property_records_are_a_separate_namespace_with_no_control_path() -> None:
    endpoint = project([_device("sensor", kind="sensor")], [BATTERY_STATE])[0]
    properties = [
        capability for capability in endpoint.capabilities
        if capability.type.startswith("devices.properties.")
    ]
    assert properties, "battery/sensor readings must project as properties"
    for capability in properties:
        assert capability.instance in ("temperature", "humidity", "battery_level")
        assert capability.meta.supported_directives == ()
        assert capability.type.startswith("devices.properties.")
    controls = [
        capability for capability in endpoint.capabilities
        if not capability.type.startswith("devices.properties.")
    ]
    assert controls == []


def test_provider_specific_fields_never_serialize() -> None:
    device = _device(
        "power",
        address="ADDR-SENTINEL",
        entity_id="ENTITY-SENTINEL",
        dev_id="DEVID-SENTINEL",
        token_env="TOKEN-SENTINEL",
        mac="MAC-SENTINEL",
        devtype="DEVTYPE-SENTINEL",
        dps={"1": "DPS-SENTINEL"},
        scale={"brightness": 10},
        ir={"on": "IR-SENTINEL"},
        notes="NOTES-SENTINEL",
        auth={"token": "AUTH-SENTINEL"},
        permissions=["PERM-SENTINEL"],
        credentials={"key": "CRED-SENTINEL"},
    )
    endpoint = project([device], [LIVE_STATE])[0]
    assert validate_endpoint(endpoint) == []
    serialized = repr(endpoint.as_dict()) + repr(endpoint)
    for sentinel in ("ADDR", "ENTITY", "DEVID", "TOKEN", "MAC", "DEVTYPE", "DPS", "IR",
                     "NOTES", "AUTH", "PERM", "CRED"):
        assert sentinel not in serialized, sentinel


def test_the_projection_does_not_mutate_its_inputs() -> None:
    devices = [_device("power")]
    states = [copy.deepcopy(LIVE_STATE)]
    first = project(devices, states)
    second = project(devices, states)
    assert [endpoint.as_dict() for endpoint in first] == [
        endpoint.as_dict() for endpoint in second]
    assert devices == [_device("power")]
    assert states[0]["state"]["attributes"]["brightness_pct"] == 60


def test_duplicate_ids_project_once_and_non_mappings_are_skipped() -> None:
    dirty = [_device("power"), _device("power"), "not-a-device"]
    endpoints = project(dirty, [])
    assert len(endpoints) == 1


def test_unmapped_counts_accumulate_per_tag() -> None:
    devices = [
        {"id": "a", "provider": "govee", "kind": "vacuum",
         "capabilities": ["vacuum", "power"]},
        {"id": "b", "provider": "tuya", "kind": "other",
         "capabilities": ["dp", "media"]},
    ]
    assert unmapped_capabilities(devices) == {"dp": 1, "media": 1, "vacuum": 1}


def test_the_readiness_fixture_is_shaped_like_the_plugins() -> None:
    # The store agrees ``status.online`` with this readiness, so keep the shape pinned.
    assert READINESS[0]["provider"] == "govee"
    assert set(READINESS[0]) == {"provider", "ready", "detail", "hint"}
