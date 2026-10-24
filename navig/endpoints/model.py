"""The frozen Unified Endpoints contract: endpoint, capability, property, meta.

One controllable thing is an :class:`Endpoint`. What it can do is a list of
:class:`Capability` records — five capability types
(``devices.capabilities.{on_off, range, mode, color_setting, toggle}``) plus the
three read-only property types (``devices.properties.{temperature, humidity,
battery_level}``). A capability's :class:`CapabilityMeta` says whether the value
can be read, reported, and which directive(s) change it.

The vocabulary is deliberate and closed: an endpoint that advertises anything
outside these tuples is rejected by :func:`validate_endpoint`. That rejection is
what keeps this a *projection* of the home inventory instead of a second, slowly
diverging device model — and what stops a provider-specific field
(``address``/``entity_id``/``dps``/…) from leaking onto the wire.

**Honesty rules baked into the model:**

* the provider API defines nine capability types; this module supports five and
  lists the other four in :data:`OUT_OF_SCOPE_CAPABILITY_TYPES` — they are out of
  scope on purpose, never silently missing;
* ``meta.reportable`` defaults to ``False`` and ``meta.supported_events`` stays
  empty: ``navig-home`` does not emit reportability/event metadata today, so the
  projection must not invent it. ``meta.retrievable`` is true for a capability
  whose value the live snapshot can answer;
* property instances are a **separate namespace keyed by type**, not by name:
  ``devices.properties.*`` records sit inside ``capabilities[]`` with empty
  ``supported_directives`` — there is no control path for a read-only property.
  (``temperature`` is a legal instance name for both a range capability and the
  temperature property; only the TYPE tells them apart.)

The contract is also the one the CLI/JSON Answer envelope carries; see
``navig.ui.answer`` for the response half.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The five capability types the v1 slice supports.
CAPABILITY_TYPES: tuple[str, ...] = (
    "devices.capabilities.on_off",
    "devices.capabilities.range",
    "devices.capabilities.mode",
    "devices.capabilities.color_setting",
    "devices.capabilities.toggle",
)

#: The provider API (Alice / Yandex Smart Home) defines nine capability types.
#: Five are the intentional ship slice (:data:`CAPABILITY_TYPES`); these four are
#: **explicitly out of scope**, not silently missing. They are not projected, not
#: validated against, and a device carrying only these appears with an empty
#: capability list — no invented control surface.
OUT_OF_SCOPE_CAPABILITY_TYPES: tuple[str, ...] = (
    "devices.capabilities.custom.button",
    "devices.capabilities.quasar",
    "devices.capabilities.quasar.server_action",
    "devices.capabilities.video_stream",
)

#: The three read-only property types (modelled as records inside ``capabilities[]``).
PROPERTY_TYPES: tuple[str, ...] = (
    "devices.properties.temperature",
    "devices.properties.humidity",
    "devices.properties.battery_level",
)

#: Property instances, one per property type.
PROPERTY_INSTANCES: tuple[str, ...] = ("temperature", "humidity", "battery_level")

#: Directive names are NAVIG-native and 1:1 with the capability types.
DIRECTIVES: tuple[str, ...] = ("on_off", "set_range", "set_mode", "set_color", "toggle")

#: The only keys a ``meta`` block may carry.
META_KEYS: tuple[str, ...] = (
    "retrievable",
    "reportable",
    "supported_directives",
    "supported_events",
)

#: Events the v1 slice advertises (none — the wire key exists for compatibility).
SUPPORTED_EVENTS: tuple[str, ...] = ()

#: Top-level keys of an endpoint record.
ENDPOINT_FIELDS: tuple[str, ...] = ("id", "type", "info", "status", "room", "capabilities")
INFO_FIELDS: tuple[str, ...] = ("manufacturer", "model", "hw", "sw")
STATUS_FIELDS: tuple[str, ...] = ("online", "updated_at")
CAPABILITY_FIELDS: tuple[str, ...] = ("type", "instance", "parameters", "state", "meta")

#: Device fields that belong to the inventory/provider, never to an endpoint.
#: ``address``, ``entity_id``, ``dev_id``, ``token_env``, ``mac``, ``dps``,
#: ``scale`` and ``ir`` are the inventory's provider addressing; ``devtype`` is
#: Broadlink's, kept here for the same reason.
FORBIDDEN_FIELDS: tuple[str, ...] = (
    "address",
    "entity_id",
    "dev_id",
    "token_env",
    "mac",
    "devtype",
    "dps",
    "scale",
    "ir",
)

#: ``<provider>:<device id>`` — the home id is ``^[a-z0-9][a-z0-9_-]{0,63}$``.
_ID_RE = re.compile(r"^[a-z0-9_-]+:[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class CapabilityMeta:
    """What the caller may do with one capability's value.

    ``reportable`` defaults to ``False`` because no shipped provider declares
    reportability: a projection that set it ``True`` by default would claim an
    event channel NAVIG does not have.
    """

    retrievable: bool = True
    reportable: bool = False
    supported_directives: tuple[str, ...] = ()
    supported_events: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrievable": self.retrievable,
            "reportable": self.reportable,
            "supported_directives": list(self.supported_directives),
            "supported_events": list(self.supported_events),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityMeta:
        directives = data.get("supported_directives") or ()
        events = data.get("supported_events") or ()
        return cls(
            retrievable=bool(data.get("retrievable", True)),
            reportable=bool(data.get("reportable", True)),
            supported_directives=tuple(str(item) for item in directives),
            supported_events=tuple(str(item) for item in events),
        )


@dataclass(frozen=True)
class Capability:
    """One typed capability of an endpoint, or one read-only property record."""

    type: str
    instance: str
    parameters: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    meta: CapabilityMeta = field(default_factory=CapabilityMeta)

    @property
    def is_property(self) -> bool:
        return self.type in PROPERTY_TYPES

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "instance": self.instance,
            "parameters": dict(self.parameters),
            "state": dict(self.state),
            "meta": self.meta.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Capability:
        return cls(
            type=str(data.get("type") or ""),
            instance=str(data.get("instance") or ""),
            parameters=dict(data.get("parameters") or {}),
            state=dict(data.get("state") or {}),
            meta=CapabilityMeta.from_dict(data.get("meta") or {}),
        )


@dataclass(frozen=True)
class EndpointInfo:
    """Descriptive fields only — never provider addressing."""

    manufacturer: str = ""
    model: str = ""
    hw: str = ""
    sw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "manufacturer": self.manufacturer,
            "model": self.model,
            "hw": self.hw,
            "sw": self.sw,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EndpointInfo:
        return cls(
            manufacturer=str(data.get("manufacturer") or ""),
            model=str(data.get("model") or ""),
            hw=str(data.get("hw") or ""),
            sw=str(data.get("sw") or ""),
        )


@dataclass(frozen=True)
class EndpointStatus:
    """Liveness of the endpoint, straight from the live snapshot.

    ``online`` is ``None`` when no snapshot exists — it is never invented.
    """

    online: bool | None = None
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"online": self.online, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EndpointStatus:
        online = data.get("online")
        return cls(
            online=bool(online) if online is not None else None,
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class Endpoint:
    """One controllable thing, as every surface reads it."""

    id: str
    type: str = "devices.types.other"
    info: EndpointInfo = field(default_factory=EndpointInfo)
    status: EndpointStatus = field(default_factory=EndpointStatus)
    room: str = ""
    capabilities: list[Capability] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "info": self.info.as_dict(),
            "status": self.status.as_dict(),
            "room": self.room,
            "capabilities": [capability.as_dict() for capability in self.capabilities],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Endpoint:
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or "devices.types.other"),
            info=EndpointInfo.from_dict(data.get("info") or {}),
            status=EndpointStatus.from_dict(data.get("status") or {}),
            room=str(data.get("room") or ""),
            capabilities=[Capability.from_dict(item) for item in data.get("capabilities") or []],
        )


def _foreign_keys(value: Any, path: str) -> list[str]:
    """Every forbidden field name found anywhere in *value*, with its path."""
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            child = f"{path}.{name}" if path else name
            if name in FORBIDDEN_FIELDS:
                findings.append(f"{child}: provider-specific field must never serialize")
            findings.extend(_foreign_keys(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            findings.extend(_foreign_keys(item, f"{path}[{index}]"))
    return findings


def _validate_capability(raw: Any, index: int) -> list[str]:
    label = f"capabilities[{index}]"
    if not isinstance(raw, Mapping):
        return [f"{label}: must be an object"]
    problems: list[str] = []
    unknown = set(raw) - set(CAPABILITY_FIELDS)
    if unknown:
        problems.append(f"{label}: unknown keys {sorted(unknown)}")
    capability_type = str(raw.get("type") or "")
    if capability_type not in CAPABILITY_TYPES + PROPERTY_TYPES:
        problems.append(f"{label}: undeclared capability type {capability_type!r}")
    instance = str(raw.get("instance") or "")
    if not instance:
        problems.append(f"{label}: instance must be non-empty")
    if not isinstance(raw.get("parameters", {}), Mapping):
        problems.append(f"{label}: parameters must be an object")
    if not isinstance(raw.get("state", {}), Mapping):
        problems.append(f"{label}: state must be an object")
    meta = raw.get("meta")
    if not isinstance(meta, Mapping):
        problems.append(f"{label}: meta must be an object")
        return problems
    unknown_meta = set(meta) - set(META_KEYS)
    if unknown_meta:
        problems.append(f"{label}.meta: undeclared keys {sorted(unknown_meta)}")
    for key in ("retrievable", "reportable"):
        if key in meta and not isinstance(meta[key], bool):
            problems.append(f"{label}.meta.{key}: must be a boolean")
    directives = meta.get("supported_directives") or ()
    if not isinstance(directives, (list, tuple)):
        problems.append(f"{label}.meta.supported_directives: must be a list")
        directives = ()
    for directive in directives:
        if directive not in DIRECTIVES:
            problems.append(f"{label}.meta.supported_directives: unknown directive {directive!r}")
    events = meta.get("supported_events") or ()
    if not isinstance(events, (list, tuple)):
        problems.append(f"{label}.meta.supported_events: must be a list")
        events = ()
    for event in events:
        if event not in SUPPORTED_EVENTS:
            problems.append(f"{label}.meta.supported_events: unsupported event {event!r}")
    if capability_type in PROPERTY_TYPES:
        if directives:
            problems.append(f"{label}: a read-only property must not list directives")
        if instance not in PROPERTY_INSTANCES:
            problems.append(f"{label}: unknown property instance {instance!r}")
    # A range capability legitimately uses the instance name "temperature"
    # (range instances are brightness/temperature/…) — the namespaces are told
    # apart by TYPE (``devices.properties.*`` vs ``devices.capabilities.*``),
    # never by the instance string, so no cross-check here.
    return problems


def validate_endpoint(endpoint: Endpoint | Mapping[str, Any]) -> list[str]:
    """Return every structural problem with *endpoint*; ``[]`` means valid.

    Rejects an undeclared capability type, an undeclared ``meta`` key, any
    unknown top-level key, and any provider-specific field nested anywhere.
    """
    try:
        payload = endpoint.as_dict() if isinstance(endpoint, Endpoint) else dict(endpoint)
    except (TypeError, ValueError):
        return ["endpoint must be a mapping or an Endpoint"]
    problems: list[str] = []
    unknown = set(payload) - set(ENDPOINT_FIELDS)
    if unknown:
        problems.append(f"unknown endpoint fields {sorted(unknown)}")
    endpoint_id = str(payload.get("id") or "")
    if not _ID_RE.match(endpoint_id):
        problems.append(f"id {endpoint_id!r} must be '<provider>:<device id>'")
    if not str(payload.get("type") or "").startswith("devices.types."):
        problems.append(f"type {payload.get('type')!r} must be a devices.types.* value")
    if not isinstance(payload.get("room", ""), str):
        problems.append("room must be a string")
    info = payload.get("info")
    if not isinstance(info, Mapping):
        problems.append("info must be an object")
    else:
        unknown_info = set(info) - set(INFO_FIELDS)
        if unknown_info:
            problems.append(f"info: unknown keys {sorted(unknown_info)}")
    status = payload.get("status")
    if not isinstance(status, Mapping):
        problems.append("status must be an object")
    else:
        unknown_status = set(status) - set(STATUS_FIELDS)
        if unknown_status:
            problems.append(f"status: unknown keys {sorted(unknown_status)}")
        if status.get("online") is not None and not isinstance(status.get("online"), bool):
            problems.append("status.online must be a boolean or null")
        if not isinstance(status.get("updated_at", ""), str):
            problems.append("status.updated_at must be a string")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        problems.append("capabilities must be a list")
    else:
        for index, raw in enumerate(capabilities):
            problems.extend(_validate_capability(raw, index))
    problems.extend(_foreign_keys(payload, "endpoint"))
    return problems
