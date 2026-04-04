"""Tests for navig.commands.connector_cmd — CLI surface for connector engine."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.connector_cmd import connector_app
from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.registry import get_connector_registry
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

runner = CliRunner()


# ── Stubs ────────────────────────────────────────────────────────────────


class _FakeGmail(BaseConnector):
    manifest = ConnectorManifest(
        id="gmail",
        display_name="Gmail",
        description="Fake Gmail for tests",
        domain=ConnectorDomain.COMMUNICATION,
        icon="📧",
        requires_oauth=True,
    )

    async def search(self, query: str) -> list[Resource]:
        return [
            Resource(
                id="msg-1",
                source="gmail",
                title=f"Email: {query}",
                preview="hello",
            )
        ]

    async def fetch(self, resource_id: str) -> Resource:
        return Resource(
            id=resource_id,
            source="gmail",
            title="Fetched Email",
            preview="Full body here.",
        )

    async def act(self, action: Action) -> ActionResult:
        return ActionResult(success=True)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=5.0)


class _FakeCalendar(BaseConnector):
    manifest = ConnectorManifest(
        id="google_calendar",
        display_name="Google Calendar",
        description="Fake Calendar for tests",
        domain=ConnectorDomain.CALENDAR,
        icon="📅",
    )

    async def search(self, query: str) -> list[Resource]:
        return []

    async def fetch(self, resource_id: str) -> Resource:
        return Resource(
            id=resource_id,
            source="google_calendar",
            title="Event",
            preview="",
        )

    async def act(self, action: Action) -> ActionResult:
        return ActionResult(success=True)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=2.0)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Reset registry + skip lazy-load of real connectors."""
    registry = get_connector_registry()
    registry.reset()

    # Patch _ensure_connectors_loaded so it doesn't import real Gmail/Calendar
    import navig.commands.connector_cmd as cmd_mod

    monkeypatch.setattr(cmd_mod, "_CONNECTORS_LOADED", True)

    # Register fakes
    registry.register(_FakeGmail)
    registry.register(_FakeCalendar)
    yield
    registry.reset()


# ── Tests ────────────────────────────────────────────────────────────────


class TestConnectorList:
    def test_list_table(self):
        result = runner.invoke(connector_app, ["list"])
        assert result.exit_code == 0
        assert "gmail" in result.output
        assert "google_calendar" in result.output

    def test_list_json(self):
        result = runner.invoke(connector_app, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        ids = {c["id"] for c in data}
        assert ids == {"gmail", "google_calendar"}

    def test_list_filter_by_domain(self):
        result = runner.invoke(connector_app, ["list", "--domain", "communication"])
        assert result.exit_code == 0
        assert "gmail" in result.output

    def test_list_filter_unknown_domain(self):
        result = runner.invoke(connector_app, ["list", "--domain", "nonexistent"])
        assert result.exit_code == 1


class TestConnectorDisconnect:
    def test_disconnect_known(self):
        result = runner.invoke(connector_app, ["disconnect", "gmail"])
        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()

    def test_disconnect_unknown(self):
        result = runner.invoke(connector_app, ["disconnect", "bogus"])
        assert result.exit_code == 1


class TestConnectorSearch:
    def test_search_no_connected(self):
        """When no connectors are CONNECTED, should warn."""
        result = runner.invoke(connector_app, ["search", "meeting"])
        assert result.exit_code == 0
        assert "No connectors connected" in result.output or "no results" in result.output.lower()

    def test_search_with_connected(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "meeting"])
        assert result.exit_code == 0
        assert "meeting" in result.output.lower()

    def test_search_json(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "hello", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) >= 1
        assert data[0]["source"] == "gmail"

    def test_search_with_source_filter(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "test", "--source", "gmail"])
        assert result.exit_code == 0

    def test_search_unknown_source(self):
        result = runner.invoke(connector_app, ["search", "test", "--source", "bogus"])
        assert result.exit_code == 1


class TestConnectorFetch:
    def test_fetch_resource(self):
        result = runner.invoke(connector_app, ["fetch", "gmail:msg-1"])
        assert result.exit_code == 0
        assert "Fetched Email" in result.output

    def test_fetch_json(self):
        result = runner.invoke(connector_app, ["fetch", "gmail:msg-1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "msg-1"
        assert data["source"] == "gmail"

    def test_fetch_bad_format(self):
        result = runner.invoke(connector_app, ["fetch", "no-colon-here"])
        assert result.exit_code == 1

    def test_fetch_unknown_connector(self):
        result = runner.invoke(connector_app, ["fetch", "bogus:123"])
        assert result.exit_code == 1


class TestConnectorStatus:
    def test_status_no_connected(self):
        result = runner.invoke(connector_app, ["status"])
        assert result.exit_code == 0
        assert "No connectors connected" in result.output

    def test_status_with_connected(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["status"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower() or "gmail" in result.output.lower()
