"""``EndpointStore`` — the read-through cache every surface asks for endpoints.

The plugin's two reads (inventory + live snapshots) are subprocess calls, so the
store caches their projection for :data:`TTL_SECONDS` and nothing longer. That is
deliberate:

* it is an **in-process cache only** — no DB, no second inventory, no scheduler;
* ``set`` invalidates it and then re-reads once, so ``navig endpoints set`` shows
  the provider's own answer instead of an optimistic guess;
* a repeated identical ``set`` reads the current state first and reports
  ``changed: False`` without touching the provider again (idempotent by design —
  the second run of ``navig endpoints set <id> on_off on`` is a no-op).

``list()``/``show()`` are the router's (B) public reads; ``set()`` returns the
facts a caller renders, including an Answer-shaped payload for the CLI.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from navig.core.coerce import coerce_bool
from navig.endpoints import projection
from navig.endpoints.client import HomeBridge
from navig.endpoints.errors import (
    EndpointDirectiveError,
    EndpointError,
    EndpointNotFound,
)
from navig.endpoints.model import DIRECTIVES, Capability, Endpoint

#: How long a projection may be reused. Long enough to keep one CLI invocation
#: to two child processes, short enough that a status view is never stale.
TTL_SECONDS = 5.0

_FAN_WORDS: frozenset[str] = frozenset(
    {"auto", "low", "medium", "high", "quiet", "turbo", "max", "min"}
)


@dataclass(frozen=True)
class SetResult:
    """One directive applied (or proved unnecessary) on one endpoint."""

    endpoint: Endpoint
    directive: str
    value: Any
    changed: bool
    payload: dict[str, Any] = field(default_factory=dict)


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _same(left: Any, right: Any) -> bool:
    """Value equality that tolerates JSON's int/float split."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower() == right.strip().lower()
    return left == right


class EndpointStore:
    """A TTL cache over :class:`~navig.endpoints.client.HomeBridge`.

    ``clock`` measures the cache lifetime (monotonic); ``wall_clock`` stamps
    ``status.updated_at`` in the projection. Both are injectable so a test can
    freeze time without patching the module's globals.
    """

    def __init__(
        self,
        bridge: HomeBridge | None = None,
        *,
        ttl: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bridge = bridge or HomeBridge()
        self._ttl = float(ttl)
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._devices: list[dict[str, Any]] = []
        self._providers: list[dict[str, Any]] = []
        self._endpoints: list[Endpoint] = []
        self._by_id: dict[str, Endpoint] = {}
        self._fetched_at: float | None = None

    # ── public reads (the router imports list/show) ──────────────────────

    def list(
        self,
        *,
        provider: str | None = None,
        room: str | None = None,
    ) -> list[Endpoint]:
        """Every projected endpoint, optionally filtered by provider or room."""
        self._refresh()
        wanted_provider = (provider or "").strip().lower()
        wanted_room = (room or "").strip().lower()
        endpoints = self._endpoints
        if wanted_provider:
            endpoints = [
                endpoint for endpoint in endpoints
                if endpoint.id.split(":", 1)[0] == wanted_provider
            ]
        if wanted_room:
            endpoints = [
                endpoint for endpoint in endpoints
                if endpoint.room.strip().lower() == wanted_room
            ]
        return list(endpoints)

    def show(self, endpoint_id: str) -> Endpoint:
        """One endpoint by its ``provider:id``; raises :class:`EndpointNotFound`."""
        self._refresh()
        wanted = str(endpoint_id or "").strip()
        endpoint = self._by_id.get(wanted)
        if endpoint is None:
            raise EndpointNotFound(
                f"no endpoint {wanted!r}",
                hint="run `navig endpoints list` to see the current endpoints",
            )
        return endpoint

    def providers(self) -> list[dict[str, Any]]:
        """Provider readiness from the same live snapshot the endpoints came from."""
        self._refresh()
        return [dict(row) for row in self._providers]

    def readiness(self) -> dict[str, bool]:
        """``{provider: ready}`` — the agreement check for ``status.online``."""
        return {
            str(row.get("provider") or ""): bool(row.get("ready"))
            for row in self.providers()
            if row.get("provider")
        }

    def unmapped(self) -> dict[str, int]:
        """Inventory capability tags the slice does not model, counted per tag."""
        self._refresh()
        return projection.unmapped_capabilities(self._devices)

    def invalidate(self) -> None:
        """Drop the cached projection (the next read re-queries the plugin)."""
        with self._lock:
            self._fetched_at = None

    # ── public write ─────────────────────────────────────────────────────

    def set(
        self,
        endpoint_id: str,
        directive: str,
        value: Any = None,
    ) -> SetResult:
        """Apply one directive, idempotently, through the plugin's own CLI."""
        wanted_directive = str(directive or "").strip().lower()
        if wanted_directive not in DIRECTIVES:
            raise EndpointDirectiveError(
                f"unknown directive {directive!r}",
                hint="directives: " + ", ".join(DIRECTIVES),
            )
        endpoint = self.show(endpoint_id)  # refreshes + raises EndpointNotFound
        capability = self._capability_for(endpoint, wanted_directive, value)
        desired = self._desired(capability, wanted_directive, value)
        changed = wanted_directive == "toggle" or not _same(
            self._current(capability, wanted_directive), desired)
        payload: dict[str, Any] = {}
        if changed:
            payload = self._apply(endpoint, capability, wanted_directive, desired)
            self.invalidate()
            try:
                refreshed = self.show(endpoint.id)
            except EndpointError:
                refreshed = endpoint  # the action succeeded; the re-read did not
        else:
            refreshed = endpoint
        return SetResult(
            endpoint=refreshed,
            directive=wanted_directive,
            value=desired,
            changed=changed,
            payload=payload,
        )

    # ── cache ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        with self._lock:
            now = self._clock()
            if (
                self._fetched_at is not None
                and now - self._fetched_at < self._ttl
            ):
                return
            devices = self._bridge.devices()
            status = self._bridge.status()
            states = [row for row in status.get("devices") or [] if isinstance(row, Mapping)]
            projection_now = self._wall_clock()
            endpoints = projection.project(devices, states, now=projection_now)
            self._devices = devices
            self._providers = [
                dict(row) for row in status.get("providers") or []
                if isinstance(row, Mapping)
            ]
            self._endpoints = endpoints
            self._by_id = {endpoint.id: endpoint for endpoint in endpoints}
            self._fetched_at = self._clock()

    # ── directive resolution ─────────────────────────────────────────────

    def _capability_for(
        self,
        endpoint: Endpoint,
        directive: str,
        value: Any,
    ) -> Capability:
        candidates = [
            capability for capability in endpoint.capabilities
            if directive in capability.meta.supported_directives
        ]
        if not candidates:
            supported = sorted({
                name for capability in endpoint.capabilities
                for name in capability.meta.supported_directives
            })
            raise EndpointDirectiveError(
                f"{endpoint.id} does not support {directive}",
                hint=(
                    "supported: " + ", ".join(supported)
                    if supported
                    else f"`navig endpoints show {endpoint.id}` lists what it can do"
                ),
            )
        by_instance = {capability.instance: capability for capability in candidates}
        if directive == "set_range":
            number = _coerce_number(value)
            if number is None:
                raise EndpointDirectiveError(
                    f"{directive} needs a number, got {value!r}",
                    hint="e.g. `navig endpoints set <id> set_range 40`",
                )
            if "brightness" in by_instance and 0 <= number <= 100:
                return by_instance["brightness"]
            if "temperature" in by_instance:
                return by_instance["temperature"]
        elif directive == "set_color":
            if projection.normalise_colour(value) is not None and "rgb" in by_instance:
                return by_instance["rgb"]
            if _coerce_number(value) is not None and "temperature_k" in by_instance:
                return by_instance["temperature_k"]
            if "rgb" in by_instance:
                return by_instance["rgb"]
            if "temperature_k" in by_instance:
                return by_instance["temperature_k"]
        elif directive == "set_mode":
            text = str(value if value is not None else "").strip().lower()
            if text in _FAN_WORDS and "fan_speed" in by_instance:
                return by_instance["fan_speed"]
            if "mode" in by_instance:
                return by_instance["mode"]
        return candidates[0]

    def _desired(self, capability: Capability, directive: str, value: Any) -> Any:
        if directive == "on_off":
            # The canonical truth table: same tokens the rest of NAVIG reads, and
            # ``default=None`` keeps "unrecognised" distinguishable from ``False``.
            desired = coerce_bool(value, default=None)
            if not isinstance(desired, bool):
                raise EndpointDirectiveError(
                    f"on_off needs on/off, got {value!r}",
                    hint="`navig endpoints set <id> on_off on`",
                )
            return desired
        if directive == "toggle":
            return None
        if directive == "set_range":
            number = _coerce_number(value)
            if number is None:
                raise EndpointDirectiveError(
                    f"set_range needs a number, got {value!r}",
                    hint="`navig endpoints set <id> set_range 40`",
                )
            return int(number) if float(number).is_integer() else number
        if directive == "set_color":
            colour = projection.normalise_colour(value)
            if colour is not None and capability.instance == "rgb":
                return colour
            number = _coerce_number(value)
            if number is not None and capability.instance == "temperature_k":
                return int(number)
            if colour is not None:
                return colour
            raise EndpointDirectiveError(
                f"set_color needs #rrggbb or Kelvin, got {value!r}",
                hint="`navig endpoints set <id> set_color #ff8800`",
            )
        text = str(value if value is not None else "").strip()
        if not text:
            raise EndpointDirectiveError(
                "set_mode needs a value",
                hint="`navig endpoints set <id> set_mode cool`",
            )
        return text

    def _current(self, capability: Capability, directive: str) -> Any:
        state = capability.state
        if directive in ("on_off", "toggle"):
            return state.get("on")
        if directive == "set_range":
            return state.get("value")
        if directive == "set_color":
            if capability.instance == "rgb":
                return state.get("rgb")
            return state.get("temperature_k")
        return state.get("mode")

    def _assignment(self, capability: Capability, desired: Any) -> str:
        if capability.instance == "brightness":
            return f"brightness={int(desired)}"
        if capability.instance == "temperature":
            return f"target_temp={_display_number(desired)}"
        if capability.instance == "temperature_k":
            return f"temp={int(desired)}"
        if capability.instance == "rgb":
            return f"color={desired}"
        if capability.instance == "fan_speed":
            return f"fan={desired}"
        return f"mode={desired}"

    def _apply(
        self,
        endpoint: Endpoint,
        capability: Capability,
        directive: str,
        desired: Any,
    ) -> dict[str, Any]:
        device_id = endpoint.id.split(":", 1)[1] if ":" in endpoint.id else endpoint.id
        if directive == "on_off":
            return self._bridge.power(device_id, bool(desired))
        if directive == "toggle":
            return self._bridge.toggle(device_id)
        return self._bridge.set(device_id, [self._assignment(capability, desired)])
