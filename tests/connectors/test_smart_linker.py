"""Tests for navig.connectors.smart_linker — SmartLinker cross-connector linking."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.registry import get_connector_registry
from navig.connectors.smart_linker import SmartLinker
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)


class _MockGmail(BaseConnector):
    manifest = ConnectorManifest(
        id="gmail",
        display_name="Gmail",
        description="Mock Gmail",
        domain=ConnectorDomain.COMMUNICATION,
        icon="📧",
    )

    async def search(self, query):
        return [
            Resource(
                id="email-1",
                source="gmail",
                title=f"Email about {query}",
                preview=f"Content for {query}",
            )
        ]

    async def fetch(self, resource_id):
        return Resource(id=resource_id, source="gmail", title="Email", preview="")

    async def act(self, action):
        return ActionResult(success=True)

    async def health_check(self):
        return HealthStatus(ok=True, latency_ms=1.0)


class _MockCalendar(BaseConnector):
    manifest = ConnectorManifest(
        id="google_calendar",
        display_name="Calendar",
        description="Mock Calendar",
        domain=ConnectorDomain.CALENDAR,
        icon="📅",
    )

    async def search(self, query):
        return [
            Resource(
                id="evt-1",
                source="google_calendar",
                title=f"Event: {query}",
                preview="",
                resource_type=ResourceType.EVENT,
                metadata={
                    "start": "2024-01-15T10:00:00Z",
                    "end": "2024-01-15T11:00:00Z",
                    "attendees": ["bob@example.com"],
                },
            )
        ]

    async def fetch(self, resource_id):
        return Resource(id=resource_id, source="google_calendar", title="Event", preview="")

    async def act(self, action):
        return ActionResult(success=True)

    async def health_check(self):
        return HealthStatus(ok=True, latency_ms=1.0)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry = get_connector_registry()
    registry.reset()
    yield
    registry.reset()


class TestSmartLinker:
    def _setup_connectors(self):
        registry = get_connector_registry()
        registry.register(_MockGmail)
        registry.register(_MockCalendar)
        # Mark as connected
        gmail = registry.get("gmail")
        calendar = registry.get("google_calendar")
        gmail._status = ConnectorStatus.CONNECTED
        calendar._status = ConnectorStatus.CONNECTED
        return gmail, calendar

    def test_enrich_gmail_with_calendar(self):
        self._setup_connectors()
        linker = SmartLinker()

        email = Resource(
            id="msg-1",
            source="gmail",
            title="Meeting tomorrow at 10am",
            preview="Let's meet tomorrow to discuss the project.",
            metadata={"from": "alice@example.com"},
        )

        enriched = asyncio.run(linker.enrich(email))
        # Should have related_events
        assert "related_events" in enriched.metadata

    def test_enrich_calendar_with_gmail(self):
        self._setup_connectors()
        linker = SmartLinker()

        event = Resource(
            id="evt-1",
            source="google_calendar",
            title="Project Review",
            preview="",
            metadata={
                "attendees": ["bob@example.com"],
                "organizer": "alice@example.com",
            },
        )

        enriched = asyncio.run(linker.enrich(event))
        assert "related_emails" in enriched.metadata

    def test_find_related(self):
        self._setup_connectors()
        linker = SmartLinker()

        email = Resource(
            id="msg-1",
            source="gmail",
            title="Sprint Planning",
            preview="",
        )

        related = asyncio.run(linker.find_related(email))
        assert len(related) >= 1
        assert related[0].source == "google_calendar"

    def test_enrich_no_connectors(self):
        """Should handle gracefully when target connector is missing."""
        linker = SmartLinker()

        email = Resource(
            id="msg-1",
            source="gmail",
            title="Test",
            preview="",
        )
        # Should not raise
        enriched = asyncio.run(linker.enrich(email))
        assert enriched.id == "msg-1"

    def test_find_related_with_target(self):
        self._setup_connectors()
        linker = SmartLinker()

        email = Resource(
            id="msg-1",
            source="gmail",
            title="Sprint Planning",
            preview="",
        )

        related = asyncio.run(linker.find_related(email, target_source="google_calendar"))
        assert all(r.source == "google_calendar" for r in related)
