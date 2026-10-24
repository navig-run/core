"""Pure projection: the home inventory's devices + live snapshots → Endpoints.

There is **no second inventory**. The shipped ``navig-home`` plugin owns the
inventory at ``home/devices.yaml`` under the operator's config directory, and
answers two JSON commands:

* ``navig home list --json``    → static rows (id, kind, provider, capabilities)
* ``navig home status --json``  → live snapshots (ok, ready, power, state)

:func:`project` is the only place the two shapes meet. It is pure: no I/O, no
clock read except the optional ``now``, so a test can freeze both inputs and the
timestamp. Capabilities come from the inventory's flat capability strings through
one explicit table (:data:`MAPPED_TAGS`) — the strings are never renamed because
``navig home set`` validates commands against them. Tags outside the five-plus-
three slice are omitted from the record and counted by
:func:`unmapped_capabilities`, so nothing is silently dropped from the list.

**Honesty rules:**

* ``meta.reportable`` is always ``False`` and ``meta.supported_events`` always
  empty — ``navig-home`` emits no reportability or event metadata, so the
  projection does not invent either. ``meta.retrievable`` is ``True`` (the live
  snapshot is the read path) and ``meta.supported_directives`` follows
  :data:`MAPPED_TAGS` only;
* the provider API defines nine capability types; the other four
  (:data:`navig.endpoints.model.OUT_OF_SCOPE_CAPABILITY_TYPES`) are intentionally
  out of scope;
* property records (``devices.properties.*``) are a separate namespace: they sit
  inside ``capabilities[]`` with empty ``supported_directives`` and no control
  path. Temperature/humidity come from the mapping table + live state; a
  ``battery`` reading (when the snapshot carries one) becomes a
  ``battery_level`` property;
* provider readiness never appears on an endpoint: ``status.online`` is the only
  liveness fact, and the store exposes the separate
  ``Readiness{ready,detail,hint}`` agreement next to it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from navig.endpoints.model import (
    Capability,
    CapabilityMeta,
    Endpoint,
    EndpointInfo,
    EndpointStatus,
)

#: Inventory kind → the endpoint type it projects to.
KIND_TYPES: dict[str, str] = {
    "light": "devices.types.light",
    "switch": "devices.types.switch",
    "climate": "devices.types.thermostat",
    "humidifier": "devices.types.humidifier",
    "media": "devices.types.media_device",
    "sensor": "devices.types.sensor",
    "scene": "devices.types.other",
    "vacuum": "devices.types.vacuum_cleaner",
    "other": "devices.types.other",
}

_ON_OFF = "devices.capabilities.on_off"
_RANGE = "devices.capabilities.range"
_MODE = "devices.capabilities.mode"
_COLOR = "devices.capabilities.color_setting"
_TOGGLE = "devices.capabilities.toggle"
_PROPERTY = "devices.properties"

#: Inventory capability string → (wire type, instance) inside the fixed slice.
#: ``sensor`` is context-dependent (kind + live state) and resolved in _build.
MAPPED_TAGS: dict[str, tuple[str, str]] = {
    "power": (_ON_OFF, "on"),
    "toggle": (_TOGGLE, "on"),
    "brightness": (_RANGE, "brightness"),
    "color": (_COLOR, "rgb"),
    "color_temp": (_COLOR, "temperature_k"),
    "target_temp": (_RANGE, "temperature"),
    "mode": (_MODE, "mode"),
    "fan": (_MODE, "fan_speed"),
    "humidity": (f"{_PROPERTY}.humidity", "humidity"),
}

#: Tags the v1 slice deliberately does not model (counted, not dropped). ``sensor``
#: is *not* here: it is context-dependent and resolved in :func:`_build_capabilities`.
OUT_OF_SCOPE_TAGS: frozenset[str] = frozenset({"vacuum", "media", "volume", "ir", "dp"})

BRIGHTNESS_PARAMETERS: dict[str, Any] = {
    "range": {"min": 1, "max": 100, "precision": 1},
    "unit": "unit.percent",
}
TEMPERATURE_PARAMETERS: dict[str, Any] = {
    "range": {"min": 16, "max": 32, "precision": 0.5},
    "unit": "unit.temperature.celsius",
}
COLOR_TEMPERATURE_PARAMETERS: dict[str, Any] = {
    "range": {"min": 2000, "max": 9000, "precision": 1},
    "unit": "unit.temperature.kelvin",
}
RGB_PARAMETERS: dict[str, Any] = {"color_model": "rgb"}

#: Stable record order, independent of the inventory's tag order.
_PREFERRED_ORDER: tuple[tuple[str, str], ...] = (
    (_ON_OFF, "on"),
    (_TOGGLE, "on"),
    (_RANGE, "brightness"),
    (_RANGE, "temperature"),
    (_MODE, "mode"),
    (_MODE, "fan_speed"),
    (_COLOR, "rgb"),
    (_COLOR, "temperature_k"),
    (f"{_PROPERTY}.temperature", "temperature"),
    (f"{_PROPERTY}.humidity", "humidity"),
    (f"{_PROPERTY}.battery_level", "battery_level"),
)
_ORDER = {key: index for index, key in enumerate(_PREFERRED_ORDER)}


def _iso_utc(now: datetime | None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: Any) -> float | None:
    """A finite number from a JSON value, or ``None`` (booleans are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number


def _lookup(attributes: Mapping[str, Any], state: Mapping[str, Any], *keys: str) -> Any:
    """First non-``None`` value among *keys*, attributes before top-level state."""
    for source in (attributes, state):
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def _power_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("on", "true", "1", "open", "cleaning", "docked", "returning"):
        return True
    if text in ("off", "false", "0", "closed", "idle"):
        return False
    return None


def normalise_colour(value: Any) -> str | None:
    """``[255, 136, 0]`` / ``"#ff8800"`` / ``"f80"`` → ``"#ff8800"`` (7 chars).

    ``None`` means "not a colour this slice can express" — the caller decides
    whether that is an omission (projection) or a refusal (a directive value).
    """
    if isinstance(value, (list, tuple)) and len(value) == 3:
        channels: list[int] = []
        for channel in value:
            number = _number(channel)
            if number is None:
                return None
            channels.append(max(0, min(255, int(number))))
        return "#{:02x}{:02x}{:02x}".format(*channels)
    text = str(value or "").strip().lower()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 4:  # #f80 → #ff8800
        text = "#" + "".join(character * 2 for character in text[1:])
    if len(text) != 7:
        return None
    try:
        int(text[1:], 16)
    except ValueError:
        return None
    return text


def _one_of(value: Any) -> bool | float | int | str | None:
    """Scalar state value, normalised to a JSON-friendly primitive."""
    if value is None or isinstance(value, (bool, str)):
        return value
    number = _number(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _capability_record(
    capability_type: str,
    instance: str,
    *,
    parameters: Mapping[str, Any] | None = None,
    state: Any = None,
    directives: tuple[str, ...] = (),
) -> Capability:
    payload: dict[str, Any] = {}
    if isinstance(state, Mapping):
        # ``None`` means "the snapshot did not carry it" — an empty state is honest,
        # a fabricated default is not.
        payload = {str(key): value for key, value in state.items() if value is not None}
    elif state is not None:
        payload = {"value": state}
    return Capability(
        type=capability_type,
        instance=instance,
        parameters=dict(parameters or {}),
        state=payload,
        meta=CapabilityMeta(
            retrievable=True,
            # navig-home declares no reportability: never invent an event channel.
            reportable=False,
            supported_directives=directives,
            supported_events=(),
        ),
    )


def _build_capabilities(
    device: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> list[Capability]:
    """Project one static device row + its live snapshot into typed records."""
    raw_state = snapshot.get("state")
    state: Mapping[str, Any] = raw_state if isinstance(raw_state, Mapping) else {}
    memory = state.get("attributes")
    attributes: Mapping[str, Any] = memory if isinstance(memory, Mapping) else {}
    power = _power_bool(snapshot.get("power", state.get("power")))
    kind = str(device.get("kind") or "other")
    tags = [str(tag) for tag in device.get("capabilities") or []]
    capabilities: list[Capability] = []
    seen: set[tuple[str, str]] = set()

    def add(capability: Capability) -> None:
        key = (capability.type, capability.instance)
        if key not in seen:
            seen.add(key)
            capabilities.append(capability)

    for tag in tags:
        mapped = MAPPED_TAGS.get(tag)
        if tag == "sensor":
            if kind == "sensor":
                add(_capability_record(
                    f"{_PROPERTY}.temperature",
                    "temperature",
                    state={"value": _one_of(
                        _lookup(attributes, state, "current_temp", "temperature"))},
                    directives=(),
                ))
            elif _number(_lookup(attributes, state, "humidity")) is not None:
                add(_capability_record(
                    f"{_PROPERTY}.humidity",
                    "humidity",
                    state={"value": _one_of(_lookup(attributes, state, "humidity"))},
                ))
            continue
        if mapped is None:
            continue  # out of the slice (counted by unmapped_capabilities)
        capability_type, instance = mapped
        if capability_type == _ON_OFF:
            add(_capability_record(
                _ON_OFF, "on",
                state={"on": power} if power is not None else {},
                directives=("on_off",),
            ))
        elif capability_type == _TOGGLE:
            add(_capability_record(
                _TOGGLE, "on",
                state={"on": power} if power is not None else {},
                directives=("toggle",),
            ))
        elif capability_type == _RANGE and instance == "brightness":
            add(_capability_record(
                _RANGE, "brightness",
                parameters=BRIGHTNESS_PARAMETERS,
                state={"value": _one_of(
                    _lookup(attributes, state, "brightness_pct", "brightness"))},
                directives=("set_range",),
            ))
        elif capability_type == _RANGE and instance == "temperature":
            add(_capability_record(
                _RANGE, "temperature",
                parameters=TEMPERATURE_PARAMETERS,
                state={"value": _one_of(
                    _lookup(attributes, state, "target_temp", "target_temperature"))},
                directives=("set_range",),
            ))
        elif capability_type == _MODE and instance == "mode":
            value = _lookup(attributes, state, "mode")
            add(_capability_record(
                _MODE, "mode",
                state={"mode": str(value)} if value is not None else {},
                directives=("set_mode",),
            ))
        elif capability_type == _MODE and instance == "fan_speed":
            value = _lookup(attributes, state, "fan", "fan_speed")
            add(_capability_record(
                _MODE, "fan_speed",
                state={"mode": str(value)} if value is not None else {},
                directives=("set_mode",),
            ))
        elif capability_type == _COLOR and instance == "rgb":
            colour = normalise_colour(
                _lookup(attributes, state, "rgb_color", "rgb", "color"))
            add(_capability_record(
                _COLOR, "rgb",
                parameters=RGB_PARAMETERS,
                state={"rgb": colour} if colour is not None else {},
                directives=("set_color",),
            ))
        elif capability_type == _COLOR and instance == "temperature_k":
            value = _number(
                _lookup(attributes, state, "color_temp_kelvin", "color_temp"))
            add(_capability_record(
                _COLOR, "temperature_k",
                parameters=COLOR_TEMPERATURE_PARAMETERS,
                state={"temperature_k": int(value)} if value is not None else {},
                directives=("set_color",),
            ))
        else:  # ``humidity`` static tag
            add(_capability_record(
                f"{_PROPERTY}.humidity", "humidity",
                state={"value": _one_of(
                    _lookup(attributes, state, "humidity"))},
            ))

    battery = _number(_lookup(attributes, state, "battery", "battery_level"))
    if battery is not None:
        add(_capability_record(
            f"{_PROPERTY}.battery_level", "battery_level",
            state={"value": _one_of(battery)},
        ))

    capabilities.sort(key=lambda capability: _ORDER.get(
        (capability.type, capability.instance), len(_ORDER)))
    return capabilities


def _index_states(states: Iterable[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for snapshot in states or []:
        if not isinstance(snapshot, Mapping):
            continue
        device_id = str(snapshot.get("device") or "")
        if device_id:
            index[device_id] = snapshot
    return index


def project(
    devices: Iterable[Mapping[str, Any]] | None,
    states: Iterable[Mapping[str, Any]] | None,
    now: datetime | None = None,
) -> list[Endpoint]:
    """Project static device rows + live snapshots into endpoints (pure)."""
    snapshots = _index_states(states)
    updated_at = _iso_utc(now)
    endpoints: list[Endpoint] = []
    seen: set[str] = set()
    for device in devices or []:
        if not isinstance(device, Mapping):
            continue
        device_id = str(device.get("id") or "")
        if not device_id:
            continue
        provider = str(device.get("provider") or "manual")
        endpoint_id = f"{provider}:{device_id}"
        if endpoint_id in seen:
            continue
        seen.add(endpoint_id)
        snapshot = snapshots.get(device_id) or {}
        online: bool | None = None
        if snapshot:
            online = bool(snapshot.get("ok"))
        kind = str(device.get("kind") or "other")
        endpoints.append(Endpoint(
            id=endpoint_id,
            type=KIND_TYPES.get(kind, KIND_TYPES["other"]),
            info=EndpointInfo(model=str(device.get("model") or "")),
            status=EndpointStatus(online=online, updated_at=updated_at),
            room=str(device.get("room") or ""),
            capabilities=_build_capabilities(device, snapshot),
        ))
    return endpoints


def unmapped_capabilities(devices: Iterable[Mapping[str, Any]] | None) -> dict[str, int]:
    """Inventory capability tags the v1 slice does not model, counted per tag."""
    counts: Counter[str] = Counter()
    for device in devices or []:
        if not isinstance(device, Mapping):
            continue
        for tag in device.get("capabilities") or []:
            name = str(tag)
            if name == "sensor":
                continue  # context-dependent; see _build_capabilities
            if name not in MAPPED_TAGS:
                counts[name] += 1
    return dict(sorted(counts.items()))
