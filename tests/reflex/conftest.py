"""Fixtures for the NAVIG Reflex suite — offline by injection, no subprocess.

Every test that plans or commits injects:
* a :class:`StaticResolver` over a two-provider fixture inventory (no endpoints layer),
* a :class:`RecordingRunner` in place of the CLI bridge (records argv, never spawns),
* a :class:`CommitPlanStore` over a throwaway ``RuntimeStore`` (``tmp_path``),
so the suite is deterministic, network-free and leaves no state behind.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest

from navig.reflex.executor import CommandOutcome
from navig.reflex.manifest import default_registry
from navig.reflex.resolve import StaticResolver
from navig.reflex.service import ReflexRouter
from navig.reflex.store import CommitPlanStore
from navig.store.runtime import RuntimeStore

GOLDEN_PATH = Path(__file__).with_name("golden_utterances.json")

#: The canonical two-provider, five-device inventory (the acceptance harness's own
#: fixture: plan §2.5 wants a govee lamp + an ha entity, and the operator-facing names
#: are the English ones an install actually carries). The golden Russian rows must
#: resolve against THIS inventory through the router's concept aliases — sharing one
#: inventory between the unit path and the public `navig ask` path is what makes that
#: divergence impossible to reintroduce.
FIXTURE_ENDPOINTS: list[dict[str, Any]] = [
    {
        "id": "govee:desk-lamp",
        "name": "Desk lamp",
        "room": "office",
        "type": "devices.types.light",
        "capabilities": [
            {"type": "devices.capabilities.on_off", "instance": "on"},
            {"type": "devices.capabilities.toggle", "instance": "toggle"},
            {"type": "devices.capabilities.range", "instance": "brightness"},
            {"type": "devices.capabilities.color_setting", "instance": "rgb"},
            {"type": "devices.capabilities.color_setting", "instance": "temperature_k"},
        ],
    },
    {
        "id": "ha:living-room-ac",
        "name": "Living-room AC",
        "room": "living room",
        "type": "devices.types.thermostat",
        "capabilities": [
            {"type": "devices.capabilities.on_off", "instance": "on"},
            {"type": "devices.capabilities.toggle", "instance": "toggle"},
            {"type": "devices.capabilities.range", "instance": "temperature"},
            {"type": "devices.capabilities.mode", "instance": "mode"},
            {"type": "devices.capabilities.mode", "instance": "fan_speed"},
        ],
    },
    {
        "id": "ha:kitchen-light",
        "name": "Kitchen light",
        "room": "kitchen",
        "type": "devices.types.light",
        "capabilities": [
            {"type": "devices.capabilities.on_off", "instance": "on"},
            {"type": "devices.capabilities.toggle", "instance": "toggle"},
            {"type": "devices.capabilities.range", "instance": "brightness"},
        ],
    },
    {
        "id": "ha:bedroom-sensor",
        "name": "Bedroom sensor",
        "room": "bedroom",
        "type": "devices.types.sensor",
        "capabilities": [
            {"type": "devices.properties.temperature", "instance": "temperature"},
            {"type": "devices.properties.humidity", "instance": "humidity"},
        ],
    },
    {
        "id": "ha:loft-humidifier",
        "name": "Loft humidifier",
        "room": "loft",
        "type": "devices.types.humidifier",
        "capabilities": [
            {"type": "devices.capabilities.on_off", "instance": "on"},
            {"type": "devices.capabilities.toggle", "instance": "toggle"},
            {"type": "devices.capabilities.mode", "instance": "mode"},
            {"type": "devices.properties.humidity", "instance": "humidity"},
        ],
    },
]

#: Every endpoint, in inventory order (the order targets resolve in).
ALL_TARGETS: list[str] = [str(entry["id"]) for entry in FIXTURE_ENDPOINTS]

#: Endpoints the on/off family can drive (each declares ``on_off``).
POWER_TARGETS: list[str] = [
    "govee:desk-lamp",
    "ha:living-room-ac",
    "ha:kitchen-light",
    "ha:loft-humidifier",
]

#: Endpoints with the ``brightness`` instance of the shared ``range`` capability.
BRIGHTNESS_TARGETS: list[str] = ["govee:desk-lamp", "ha:kitchen-light"]


def load_golden() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["utterances"]


@pytest.fixture(scope="session")
def golden() -> list[dict[str, Any]]:
    """The frozen EN+RU utterance table (>= 40 rows)."""
    return load_golden()


class RecordingRunner:
    """A :class:`CommandRunner` that records argv and returns canned payloads."""

    #: Commands that change state — a plan must never run one of these.
    MUTATING = ("home on", "home off", "home toggle", "home set", "tv power", "tv volume",
                "tv play")

    def __init__(self, *, fail: Iterable[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self._fail = tuple(str(item) for item in fail)

    def run(self, argv: Iterable[str]) -> CommandOutcome:
        args = [str(item) for item in argv]
        self.calls.append(args)
        text = " ".join(args)
        if any(marker in text for marker in self._fail):
            return CommandOutcome(argv=tuple(args), ok=False, exit_code=1, error="boom")
        payload = self._payload(text)
        output = " ".join(args[1:]).strip()
        return CommandOutcome(
            argv=tuple(args), ok=True, exit_code=0, payload=payload, output=output
        )

    @staticmethod
    def _payload(text: str) -> Any:
        if "home status" in text:
            return {
                "ok": True,
                "count": 1,
                "devices": [
                    {"name": "Desk lamp", "ok": True, "power": True},
                ],
            }
        if "assistant doctor" in text:
            return {"ok": True, "checks": []}
        if "tasks campaigns list" in text:
            return {"ok": True, "campaigns": [{"slug": "demo", "state": "active"}]}
        if "coordination inbox" in text:
            return {
                "recipient": "operator",
                "count": 1,
                "messages": [
                    {
                        "sender": "agent:lead",
                        "kind": "handoff",
                        "delivery_state": "persisted",
                        "content": {"note": "hi"},
                    },
                ],
            }
        return None

    def mutating_calls(self) -> list[list[str]]:
        return [
            call
            for call in self.calls
            if any(marker in " ".join(call) for marker in self.MUTATING)
        ]


class FakeClock:
    """A movable clock so plan expiry is proven without sleeping."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture()
def endpoints() -> list[dict[str, Any]]:
    return [dict(item) for item in FIXTURE_ENDPOINTS]


@pytest.fixture()
def fixture_endpoints() -> list[dict[str, Any]]:
    """The frozen two-provider inventory (a fresh copy; tests may mutate theirs)."""
    return [dict(item) for item in FIXTURE_ENDPOINTS]


@pytest.fixture()
def resolver(endpoints: list[dict[str, Any]]) -> StaticResolver:
    return StaticResolver(endpoints)


@pytest.fixture()
def fixture_resolver(fixture_endpoints: list[dict[str, Any]]) -> StaticResolver:
    return StaticResolver(fixture_endpoints)


@pytest.fixture()
def runner() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture()
def make_runner() -> Callable[..., RecordingRunner]:
    def factory(**kwargs: Any) -> RecordingRunner:
        return RecordingRunner(**kwargs)

    return factory


@pytest.fixture()
def runtime(tmp_path: Path) -> RuntimeStore:
    store = RuntimeStore(tmp_path / "runtime.db")
    yield store
    store.close()


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def plan_store(runtime: RuntimeStore, clock: FakeClock) -> CommitPlanStore:
    return CommitPlanStore(runtime, clock=clock)


@pytest.fixture()
def make_router(
    resolver: StaticResolver, runner: RecordingRunner, plan_store: CommitPlanStore
) -> Callable[..., ReflexRouter]:
    def factory(**overrides: Any) -> ReflexRouter:
        kwargs: dict[str, Any] = {
            "registry": default_registry(),
            "resolver": resolver,
            "store": plan_store,
            "runner": runner,
            "security_level": "medium",
        }
        kwargs.update(overrides)
        return ReflexRouter(**kwargs)

    return factory


@pytest.fixture()
def router(make_router: Callable[..., ReflexRouter]) -> ReflexRouter:
    return make_router()
