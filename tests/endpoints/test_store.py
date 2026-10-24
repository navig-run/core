"""The EndpointStore: TTL cache, filters, idempotent set, readiness agreement."""

from __future__ import annotations

import pytest

from navig.endpoints.client import HomeBridge
from navig.endpoints.errors import EndpointDirectiveError, EndpointNotFound
from navig.endpoints.store import EndpointStore
from tests.endpoints.fixtures import (
    READINESS,
    FakeClock,
    FakeHome,
    projection_now,
    snapshots,
)


def make_store(
    home: FakeHome | None = None,
    *,
    clock: FakeClock | None = None,
) -> tuple[EndpointStore, FakeHome, FakeClock]:
    fake = home or FakeHome()
    fake_clock = clock or FakeClock()
    store = EndpointStore(
        HomeBridge(runner=fake.runner),
        clock=fake_clock,
        wall_clock=projection_now,
    )
    return store, fake, fake_clock


def _govee_switch_home() -> FakeHome:
    """One provider, one switch: only ``on_off`` is supported — for refusals."""
    return FakeHome(
        devices=[{
            "id": "plug",
            "name": "Plug",
            "kind": "switch",
            "provider": "govee",
            "room": "office",
            "capabilities": ["power"],
        }],
        states=[{
            "device": "plug",
            "name": "Plug",
            "kind": "switch",
            "provider": "govee",
            "ok": True,
            "power": "on",
            "state": {"power": "on", "attributes": {}},
        }],
        providers=READINESS,
    )


def test_list_reads_the_plugin_once_per_ttl() -> None:
    store, home, clock = make_store()
    assert len(store.list()) == 2
    assert len(store.list()) == 2
    assert (home.list_calls, home.status_calls) == (1, 1)
    clock.advance(6.0)
    store.list()
    assert (home.list_calls, home.status_calls) == (2, 2)


def test_the_default_ttl_is_five_seconds() -> None:
    from navig.endpoints.store import TTL_SECONDS

    assert TTL_SECONDS == 5.0


def test_list_filters_by_provider_and_room() -> None:
    store, _home, _clock = make_store()
    assert [endpoint.id for endpoint in store.list(provider="govee")] == ["govee:desk-lamp"]
    assert [endpoint.id for endpoint in store.list(room="living room")] == ["ha:living-room-ac"]
    assert store.list(provider="nope") == []


def test_show_returns_the_endpoint_and_explains_a_miss() -> None:
    store, _home, _clock = make_store()
    endpoint = store.show("ha:living-room-ac")
    assert endpoint.type == "devices.types.thermostat"
    with pytest.raises(EndpointNotFound) as caught:
        store.show("govee:missing")
    assert "navig endpoints list" in caught.value.hint


def test_set_on_off_changes_once_then_replays_as_a_no_op() -> None:
    store, home, _clock = make_store()
    first = store.set("govee:desk-lamp", "on_off", "on")
    assert first.changed is True
    assert home.action_calls == 1
    second = store.set("govee:desk-lamp", "on_off", "on")
    assert second.changed is False
    assert home.action_calls == 1, "a replay must not call the provider again"
    assert second.endpoint.status.online is True


def test_set_uses_the_matching_power_verb() -> None:
    store, home, _clock = make_store()
    already_off = store.set("govee:desk-lamp", "on_off", "off")
    assert already_off.changed is False
    changed = store.set("govee:desk-lamp", "on_off", "on")
    assert changed.changed is True
    assert changed.payload["action"] == "on"
    assert ["home", "on", "desk-lamp", "--json"] in home.calls


def test_set_range_maps_brightness_and_is_idempotent() -> None:
    store, home, _clock = make_store()
    changed = store.set("govee:desk-lamp", "set_range", 40)
    assert changed.changed is True
    assert changed.directive == "set_range"
    assert ["home", "set", "desk-lamp", "brightness=40", "--json"] in home.calls
    replay = store.set("govee:desk-lamp", "set_range", "40")
    assert replay.changed is False
    assert home.action_calls == 1


def test_a_range_at_the_current_value_is_a_no_op() -> None:
    store, home, _clock = make_store()
    result = store.set("govee:desk-lamp", "set_range", 60)
    assert result.changed is False
    assert home.action_calls == 0


def test_set_color_maps_rgb_and_is_idempotent() -> None:
    store, home, _clock = make_store()
    shorthand = store.set("govee:desk-lamp", "set_color", "#f80")
    assert shorthand.changed is False, "#f80 is the fixture's current colour"
    changed = store.set("govee:desk-lamp", "set_color", "#0000ff")
    assert changed.changed is True
    assert ["home", "set", "desk-lamp", "color=#0000ff", "--json"] in home.calls
    replay = store.set("govee:desk-lamp", "set_color", "#0000FF")
    assert replay.changed is False
    assert home.action_calls == 1


def test_set_color_maps_kelvin_to_the_temperature_instance() -> None:
    store, home, _clock = make_store()
    result = store.set("govee:desk-lamp", "set_color", 4000)
    assert result.changed is True
    assert ["home", "set", "desk-lamp", "temp=4000", "--json"] in home.calls


def test_set_mode_maps_modes_and_fans() -> None:
    store, home, _clock = make_store()
    mode = store.set("ha:living-room-ac", "set_mode", "heat")
    assert mode.changed is True
    assert ["home", "set", "living-room-ac", "mode=heat", "--json"] in home.calls
    fan = store.set("ha:living-room-ac", "set_mode", "high")
    assert fan.changed is True
    assert ["home", "set", "living-room-ac", "fan=high", "--json"] in home.calls


def test_toggle_always_acts() -> None:
    store, home, _clock = make_store()
    first = store.set("govee:desk-lamp", "toggle")
    second = store.set("govee:desk-lamp", "toggle")
    assert first.changed is True and second.changed is True
    assert home.action_calls == 2


def test_an_unsupported_directive_names_what_is_supported() -> None:
    store, _home, _clock = make_store(_govee_switch_home())
    with pytest.raises(EndpointDirectiveError) as caught:
        store.set("govee:plug", "set_range", 40)
    assert "does not support set_range" in str(caught.value)
    assert "on_off" in caught.value.hint


def test_an_unknown_directive_lists_the_vocabulary() -> None:
    store, _home, _clock = make_store()
    with pytest.raises(EndpointDirectiveError) as caught:
        store.set("govee:desk-lamp", "explode", 1)
    assert "unknown directive" in str(caught.value)
    assert "on_off" in caught.value.hint


def test_a_bad_value_is_refused_before_any_child_runs() -> None:
    store, home, _clock = make_store()
    with pytest.raises(EndpointDirectiveError):
        store.set("govee:desk-lamp", "on_off", "maybe")
    with pytest.raises(EndpointDirectiveError):
        store.set("govee:desk-lamp", "set_range", "wide")
    with pytest.raises(EndpointDirectiveError):
        store.set("govee:desk-lamp", "set_mode", "  ")
    assert home.action_calls == 0


def test_set_on_an_unknown_endpoint_is_not_found() -> None:
    store, _home, _clock = make_store()
    with pytest.raises(EndpointNotFound):
        store.set("govee:missing", "on_off", "on")


def test_a_property_only_device_has_no_control_path() -> None:
    home = FakeHome(
        devices=[{
            "id": "thermo",
            "name": "Thermo",
            "kind": "sensor",
            "provider": "ha",
            "room": "office",
            "capabilities": ["sensor"],
        }],
        states=[{
            "device": "thermo",
            "ok": True,
            "power": "unknown",
            "state": {"attributes": {"current_temp": 21.0, "battery": 90}},
        }],
        providers=READINESS,
    )
    store, _home, _clock = make_store(home)
    endpoint = store.show("ha:thermo")
    assert all(
        capability.type.startswith("devices.properties.")
        for capability in endpoint.capabilities
    )
    with pytest.raises(EndpointDirectiveError):
        store.set("ha:thermo", "on_off", "on")


def test_readiness_agrees_with_the_providers_command() -> None:
    """``status.online`` and ``navig home providers --json`` must not drift."""
    fake = FakeHome()
    bridge = HomeBridge(runner=fake.runner)
    store = EndpointStore(bridge, clock=FakeClock(), wall_clock=projection_now)
    endpoints = store.list()
    from_command = {row["provider"]: row["ready"] for row in bridge.providers()}
    readiness = store.readiness()
    assert readiness == from_command
    assert endpoints, "the fixture must project at least one endpoint"
    for endpoint in endpoints:
        provider = endpoint.id.split(":", 1)[0]
        if endpoint.status.online is True:
            assert readiness[provider] is True
        if readiness[provider] is False:
            assert endpoint.status.online is False


def test_readiness_and_online_agree() -> None:
    """``status.online`` must never claim a provider is ready when it is not."""
    home = FakeHome(
        states=[
            dict(snapshots()[0]),
            dict(snapshots()[1], ok=False, power="unknown",
                 state={"power": "unknown", "attributes": {}}),
        ],
        providers=[
            {"provider": "govee", "ready": True, "detail": "", "hint": ""},
            {"provider": "ha", "ready": False, "detail": "HA_URL unset", "hint": "set it"},
        ],
    )
    store, _home, _clock = make_store(home)
    readiness = store.readiness()
    assert readiness["govee"] is True and readiness["ha"] is False
    rows = store.list()
    assert rows, "the fixture must project at least one endpoint"
    for endpoint in rows:
        provider = endpoint.id.split(":", 1)[0]
        if endpoint.status.online is True:
            assert readiness[provider] is True
        if readiness[provider] is False:
            assert endpoint.status.online is False


def test_unmapped_counts_come_from_the_cached_inventory() -> None:
    home = FakeHome(devices=[
        {"id": "vac", "name": "Vac", "kind": "vacuum", "provider": "tuya",
         "capabilities": ["vacuum", "power"]},
    ])
    store, fake, _clock = make_store(home)
    assert store.unmapped() == {"vacuum": 1}
    assert fake.list_calls == 1


def test_providers_and_unmapped_share_the_same_cache_entry() -> None:
    store, home, _clock = make_store()
    store.list()
    store.providers()
    store.unmapped()
    store.readiness()
    assert (home.list_calls, home.status_calls) == (1, 1)


def test_invalidate_forces_a_fresh_projection() -> None:
    store, home, _clock = make_store()
    store.list()
    store.invalidate()
    store.list()
    assert (home.list_calls, home.status_calls) == (2, 2)


def test_the_store_returns_the_providers_own_answer() -> None:
    store, home, _clock = make_store()
    result = store.set("govee:desk-lamp", "on_off", "on")
    assert result.payload["ok"] is True
    assert result.payload["device"] == "desk-lamp"
    assert home.calls[-1][0] == "home"
