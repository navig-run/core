"""Read-only endpoint resolution for the NAVIG Reflex.

The router needs exactly one thing from the endpoint layer: the current list of
endpoints, so an utterance's ``device``/``room`` slots can become stable
``provider:id`` targets. It does **not** mutate through this module, and it does
not parse ``devices.yaml`` — ``core/navig/endpoints`` (nav-core) owns the
projection and is the only inventory reader.

Two properties keep this honest and testable:

* **Duck-typed.** :func:`summarize` accepts a dataclass *or* a ``Mapping``, so it
  works against ``navig.endpoints.model.Endpoint`` the moment that lands, and
  against a plain dict fixture in tests.
* **Injectable.** :class:`EndpointResolver` is a two-method protocol; the default
  :class:`StoreResolver` imports ``navig.endpoints.store.EndpointStore`` lazily and
  degrades to an empty list (never raises) when endpoints are not installed. Tests
  pass :class:`StaticResolver`, so the suite is offline with no plugin present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

#: Capability type prefixes the projection uses; only the suffix matters here.
_CAPABILITY_PREFIX = "devices.capabilities."
_PROPERTY_PREFIX = "devices.properties."

#: Words that mean "the whole set" — an explicit total request (a batch by intent).
SCOPE_TOTAL_WORDS: frozenset[str] = frozenset(
    {"all", "everything", "every", "each", "все", "всё", "весь", "вся"}
)

#: Plural/mass nouns that scope to the lights rather than naming one device.
#: Singular forms ("light", "lamp", "лампа") are deliberately absent: they are device
#: names and must resolve against the inventory first.
SCOPE_LIGHT_WORDS: frozenset[str] = frozenset(
    {"lights", "lamps", "свет", "лампы", "освещение"}
)


@dataclass(frozen=True)
class EndpointView:
    """A normalized, router-facing view of one endpoint (never provider-specific)."""

    id: str
    name: str = ""
    type: str = ""
    room: str = ""
    capabilities: tuple[str, ...] = ()
    capability_instances: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "room": self.room,
            "capabilities": list(self.capabilities),
        }

    @property
    def local_id(self) -> str:
        """The device id without the provider prefix (``govee:desk-lamp`` → ``desk-lamp``)."""
        return self.id.split(":", 1)[1] if ":" in self.id else self.id

    @property
    def is_light(self) -> bool:
        """A light by projection type; capability fallback only when the type is absent."""
        if self.type:
            return self.type.endswith(".light") or self.type == "light"
        return "on_off" in self.capabilities

    def supports(self, capability_type: str, instance: str = "") -> bool:
        """Does this endpoint declare *capability_type* (and *instance*, when asked)?

        An endpoint with no capabilities at all is treated as "unknown, do not refuse":
        the frozen projection always emits them, but a hand-written fixture may not.
        The instance matters: brightness and target temperature are both ``range``, and
        a spoken "brightness" must not resolve to a thermostat.
        """
        if not capability_type:
            return True
        if not self.capabilities:
            return True
        if capability_type not in self.capabilities:
            return False
        if instance and self.capability_instances and instance not in self.capability_instances:
            return False
        return True


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _capability_names(raw: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in raw or ():
        if isinstance(item, str):  # already a normalized capability name
            text = item
        else:
            text = str(_get(item, "type", "") or "")
        if text.startswith(_CAPABILITY_PREFIX):
            text = text[len(_CAPABILITY_PREFIX) :]
        elif text.startswith(_PROPERTY_PREFIX):
            text = text[len(_PROPERTY_PREFIX) :]
        if text and text not in names:
            names.append(text)
    return tuple(names)


def _capability_instances(raw: Any) -> tuple[str, ...]:
    """The wire instances a device declares (``brightness``, ``temperature``, ``rgb``…)."""
    found: list[str] = []
    for item in raw or ():
        instance = "" if isinstance(item, str) else str(_get(item, "instance", "") or "")
        if instance and instance not in found:
            found.append(instance)
    return tuple(found)


def summarize(endpoint: Any) -> EndpointView:
    """Turn an ``Endpoint`` (dataclass or dict) into the router's view.

    Provider-specific fields are deliberately NOT copied: the router only ever sees
    ``id``/``name``/``type``/``room``/``capabilities``/``instances``/``aliases``. ``name``
    and ``aliases`` are optional (the frozen endpoint contract has neither), and the id's
    local part is the fallback display name. Idempotent: a view summarizes to itself.
    """
    if isinstance(endpoint, EndpointView):
        return endpoint
    identifier = str(_get(endpoint, "id", "") or "")
    name = str(_get(endpoint, "name", "") or "")
    aliases_raw = _get(endpoint, "aliases", ()) or ()
    aliases = tuple(str(alias) for alias in aliases_raw if str(alias))
    capabilities = _get(endpoint, "capabilities", ())
    return EndpointView(
        id=identifier,
        name=name or identifier.split(":", 1)[-1],
        type=str(_get(endpoint, "type", "") or ""),
        room=str(_get(endpoint, "room", "") or ""),
        capabilities=_capability_names(capabilities),
        capability_instances=_capability_instances(capabilities),
        aliases=aliases,
    )


@runtime_checkable
class EndpointResolver(Protocol):
    """What the router needs from the endpoint layer — reads only."""

    def list(self) -> list[Any]:
        """Every endpoint, unfiltered."""

    def show(self, identifier: str) -> Any | None:
        """One endpoint by its stable ``provider:id``."""


class StaticResolver:
    """A resolver over a fixed list — for tests and for a caller with a cached list."""

    def __init__(self, endpoints: Iterable[Any]) -> None:
        self._endpoints = [summarize(item) for item in endpoints]

    def list(self) -> list[EndpointView]:
        return list(self._endpoints)

    def show(self, identifier: str) -> EndpointView | None:
        wanted = str(identifier or "")
        for endpoint in self._endpoints:
            if endpoint.id == wanted:
                return endpoint
        return None


class StoreResolver:
    """The production resolver: reads ``navig.endpoints.store.EndpointStore``.

    Lazy import + graceful degradation: the router must keep working (by planning
    nothing and letting the caller fall through to ``ask_ai``) on an install without
    the endpoints layer, exactly like the voice plugin's optional home client.
    """

    def __init__(self) -> None:
        self._store: Any = None
        self._unavailable = False

    def _endpoint_store(self) -> Any:
        if self._store is None and not self._unavailable:
            try:
                from navig.endpoints.store import EndpointStore  # noqa: PLC0415

                self._store = EndpointStore()
            except Exception:  # noqa: BLE001 — absent/broken endpoints is not a router error
                self._unavailable = True
        return self._store

    def list(self) -> list[EndpointView]:
        store = self._endpoint_store()
        if store is None:
            return []
        try:
            return [summarize(item) for item in store.list()]
        except Exception:  # noqa: BLE001 — a read failure must not become a write or a crash
            return []

    def show(self, identifier: str) -> EndpointView | None:
        store = self._endpoint_store()
        if store is None:
            return None
        try:
            endpoint = store.show(str(identifier or ""))
        except Exception:  # noqa: BLE001
            return None
        return summarize(endpoint) if endpoint is not None else None
