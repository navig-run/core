"""Offline fixtures for the endpoints tests: one Govee lamp + one Home Assistant AC.

The fake child speaks the plugin's real JSON shapes for
``navig home list|status|providers|on|off|toggle|set --json`` and mutates its own
snapshot on an action, so the store's idempotency and the CLI's envelope are
proven without a plugin install, a provider login or a network.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from navig.endpoints.client import DEFAULT_TIMEOUT, CommandResult

GOVEE_LAMP: dict[str, Any] = {
    "id": "desk-lamp",
    "name": "Desk lamp",
    "kind": "light",
    "provider": "govee",
    "room": "office",
    "model": "H6159",
    # Provider addressing that must never reach the wire.
    "address": "192.0.2.10",
    "mac": "aa:bb:cc:dd:ee:ff",
    "capabilities": ["power", "toggle", "brightness", "color", "color_temp"],
}

HA_CLIMATE: dict[str, Any] = {
    "id": "living-room-ac",
    "name": "Living room AC",
    "kind": "climate",
    "provider": "ha",
    "room": "living room",
    "model": "AC200",
    "entity_id": "climate.living",
    "capabilities": ["power", "toggle", "target_temp", "mode", "fan"],
}

READINESS: list[dict[str, Any]] = [
    {"provider": "govee", "ready": True, "detail": "LAN + cloud", "hint": ""},
    {"provider": "ha", "ready": True, "detail": "live: API running", "hint": ""},
    {"provider": "tuya", "ready": False, "detail": "not configured", "hint": "add a key"},
]


def snapshots() -> list[dict[str, Any]]:
    """The two live snapshots, matching ``navig home status --json``'s rows."""
    return [
        {
            "device": "desk-lamp",
            "name": "Desk lamp",
            "room": "office",
            "kind": "light",
            "provider": "govee",
            "ok": True,
            "ready": True,
            "power": "off",
            "online": True,
            "state": {
                "power": "off",
                "attributes": {
                    "brightness_pct": 60,
                    "color_temp_kelvin": 3000,
                    "rgb_color": [255, 136, 0],
                },
            },
        },
        {
            "device": "living-room-ac",
            "name": "Living room AC",
            "room": "living room",
            "kind": "climate",
            "provider": "ha",
            "ok": True,
            "ready": True,
            "power": "off",
            "online": True,
            "state": {
                "power": "off",
                "attributes": {"target_temp": 22.0, "mode": "cool", "fan": "auto"},
            },
        },
    ]


class FakeHome:
    """A fake ``navig home`` child: fixed argv in, JSON out, state mutated on action."""

    def __init__(
        self,
        devices: list[dict[str, Any]] | None = None,
        states: list[dict[str, Any]] | None = None,
        providers: list[dict[str, Any]] | None = None,
    ) -> None:
        self.devices = copy.deepcopy(devices if devices is not None else [GOVEE_LAMP, HA_CLIMATE])
        self.states = copy.deepcopy(states if states is not None else snapshots())
        self.providers = copy.deepcopy(providers if providers is not None else READINESS)
        self.calls: list[list[str]] = []
        self.list_calls = 0
        self.status_calls = 0
        self.provider_calls = 0
        self.action_calls = 0
        #: verb → (exit code, stderr) to simulate a failing child.
        self.fail: dict[str, tuple[int, str]] = {}

    # ── runner ───────────────────────────────────────────────────────────

    def runner(self, args: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        argv = [str(item) for item in args]
        self.calls.append(list(argv))
        if not argv or argv[0] != "home":
            raise AssertionError(f"unexpected argv: {argv}")
        verb = argv[1] if len(argv) > 1 else ""
        if verb in self.fail:
            code, message = self.fail[verb]
            return CommandResult(tuple(argv), code, "", message)
        if verb == "list":
            self.list_calls += 1
            return self._json(argv, {
                "ok": True,
                "count": len(self.devices),
                "devices": self.devices,
                "paths": {"devices": "fixture"},
            })
        if verb == "status":
            self.status_calls += 1
            return self._json(argv, {
                "ok": True,
                "count": len(self.states),
                "devices": self.states,
                "providers": self.providers,
                "paths": {"devices": "fixture"},
            })
        if verb == "providers":
            self.provider_calls += 1
            return self._json(argv, {
                "ok": True,
                "providers": self.providers,
                "speech": [],
                "paths": {"devices": "fixture"},
            })
        device_id = argv[2] if len(argv) > 2 else ""
        if verb in ("on", "off"):
            self._set_power(device_id, verb == "on")
            self.action_calls += 1
            return self._json(argv, self._action_payload(device_id, verb))
        if verb == "toggle":
            self._set_power(device_id, not self._power(device_id))
            self.action_calls += 1
            return self._json(argv, self._action_payload(device_id, verb))
        if verb == "set":
            assignments = [item for item in argv[3:] if item != "--json"]
            self._apply(device_id, assignments)
            self.action_calls += 1
            payload = self._action_payload(device_id, verb)
            payload["command"] = assignments
            return self._json(argv, payload)
        raise AssertionError(f"unexpected verb {verb!r}")

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _json(argv: list[str], payload: dict[str, Any]) -> CommandResult:
        return CommandResult(tuple(argv), 0, json.dumps(payload), "")

    def _snapshot(self, device_id: str) -> dict[str, Any] | None:
        for snapshot in self.states:
            if snapshot.get("device") == device_id:
                return snapshot
        return None

    def _power(self, device_id: str) -> bool:
        snapshot = self._snapshot(device_id) or {}
        state = snapshot.get("state") or {}
        return str(snapshot.get("power") or state.get("power") or "off").lower() == "on"

    def _set_power(self, device_id: str, on: bool) -> None:
        snapshot = self._snapshot(device_id)
        if snapshot is None:
            return
        snapshot["power"] = "on" if on else "off"
        state = snapshot.setdefault("state", {})
        state["power"] = snapshot["power"]

    def _apply(self, device_id: str, assignments: list[str]) -> None:
        snapshot = self._snapshot(device_id)
        if snapshot is None:
            return
        attributes = snapshot.setdefault("state", {}).setdefault("attributes", {})
        for assignment in assignments:
            key, _separator, raw = assignment.partition("=")
            key, raw = key.strip(), raw.strip()
            if key == "brightness":
                attributes["brightness_pct"] = int(raw)
            elif key == "color":
                text = raw.lstrip("#")
                attributes["rgb_color"] = [
                    int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)]
            elif key == "temp":
                attributes["color_temp_kelvin"] = int(raw)
            elif key == "target_temp":
                attributes["target_temp"] = float(raw)
            elif key == "mode":
                attributes["mode"] = raw
            elif key == "fan":
                attributes["fan"] = raw

    def _action_payload(self, device_id: str, action: str) -> dict[str, Any]:
        return {
            "ok": True,
            "device": device_id,
            "name": device_id,
            "provider": "fixture",
            "action": action,
            "result": {"provider": "fixture", "action": action},
        }


class FakeClock:
    """A monotonic clock a test can advance."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


def projection_now():
    """A fixed wall clock for ``status.updated_at`` assertions."""
    from datetime import datetime, timezone

    return datetime(2026, 10, 22, 0, 0, 0, tzinfo=timezone.utc)
