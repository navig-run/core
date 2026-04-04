"""Tests for navig.connectors.registry — ConnectorRegistry singleton."""

from __future__ import annotations

import pytest

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorNotFoundError
from navig.connectors.registry import ConnectorRegistry, get_connector_registry
from navig.connectors.types import (
    Action,
    ActionResult,
    ActionType,
    ConnectorDomain,
    HealthStatus,
    Resource,
)


class _StubConnector(BaseConnector):
    """Minimal concrete connector for testing."""

    manifest = ConnectorManifest(
        id="stub",
        display_name="Stub Connector",
        description="Unit-test stub.",
        domain=ConnectorDomain.COMMUNICATION,
        icon="🧪",
    )

    async def search(self, query: str) -> list[Resource]:
        return []

    async def fetch(self, resource_id: str) -> Resource:
        return Resource(
            id=resource_id, source="stub", title="Stub", preview="", url="", timestamp=""
        )

    async def act(self, action: Action) -> ActionResult:
        return ActionResult(success=True)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=1.0)


class _AnotherStub(BaseConnector):
    manifest = ConnectorManifest(
        id="another",
        display_name="Another",
        description="Another test stub.",
        domain=ConnectorDomain.CALENDAR,
        icon="📎",
    )

    async def search(self, query):
        return []

    async def fetch(self, resource_id):
        return Resource(
            id=resource_id, source="another", title="A", preview="", url="", timestamp=""
        )

    async def act(self, action):
        return ActionResult(success=True)

    async def health_check(self):
        return HealthStatus(ok=True, latency_ms=0.5)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset registry before each test."""
    registry = get_connector_registry()
    registry.reset()
    yield
    registry.reset()


class TestConnectorRegistry:
    def test_register_and_get(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        connector = registry.get("stub")
        assert connector is not None
        assert connector.manifest.id == "stub"

    def test_register_duplicate_overwrites(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        # Second registration overwrites without error
        registry.register(_StubConnector)
        assert registry.has("stub")

    def test_get_unknown_raises(self):
        registry = get_connector_registry()
        with pytest.raises(ConnectorNotFoundError):
            registry.get("nonexistent")

    def test_has(self):
        registry = get_connector_registry()
        assert not registry.has("stub")
        registry.register(_StubConnector)
        assert registry.has("stub")

    def test_list_all(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        registry.register(_AnotherStub)
        all_conns = registry.list_all()
        assert len(all_conns) == 2
        ids = {c["id"] for c in all_conns}
        assert ids == {"stub", "another"}

    def test_list_by_domain(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        registry.register(_AnotherStub)
        comms = registry.list_by_domain(ConnectorDomain.COMMUNICATION)
        assert len(comms) == 1
        assert comms[0]["id"] == "stub"

    def test_list_connected_empty(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        assert registry.list_connected() == []

    def test_reset(self):
        registry = get_connector_registry()
        registry.register(_StubConnector)
        assert registry.has("stub")
        registry.reset()
        assert not registry.has("stub")

    def test_singleton(self):
        r1 = get_connector_registry()
        r2 = get_connector_registry()
        assert r1 is r2
