"""Tests for navig.connectors.google_calendar — mappers and connector."""

from __future__ import annotations

import asyncio

import pytest

from navig.connectors.google_calendar.mappers import calendar_event_to_resource
from navig.connectors.types import ResourceType

pytestmark = pytest.mark.integration

# ── Mapper tests ─────────────────────────────────────────────────────────


class TestCalendarMappers:
    def test_timed_event(self):
        event = {
            "id": "evt-001",
            "summary": "Team Standup",
            "description": "Daily sync",
            "start": {"dateTime": "2024-01-15T09:00:00-05:00"},
            "end": {"dateTime": "2024-01-15T09:30:00-05:00"},
            "location": "Zoom",
            "attendees": [
                {"email": "alice@example.com"},
                {"email": "bob@example.com"},
            ],
            "organizer": {"email": "alice@example.com"},
            "htmlLink": "https://calendar.google.com/event?eid=xxx",
            "status": "confirmed",
        }
        resource = calendar_event_to_resource(event)
        assert resource.id == "evt-001"
        assert resource.source == "google_calendar"
        assert resource.title == "Team Standup"
        assert resource.resource_type == ResourceType.EVENT
        assert resource.metadata["location"] == "Zoom"
        assert len(resource.metadata["attendees"]) == 2
        assert resource.metadata["all_day"] is False

    def test_all_day_event(self):
        event = {
            "id": "evt-002",
            "summary": "Company Holiday",
            "start": {"date": "2024-12-25"},
            "end": {"date": "2024-12-26"},
        }
        resource = calendar_event_to_resource(event)
        assert resource.id == "evt-002"
        assert resource.metadata["all_day"] is True

    def test_event_no_title(self):
        event = {
            "id": "evt-003",
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"},
        }
        resource = calendar_event_to_resource(event)
        assert resource.title == "(no title)"

    def test_recurring_event_flag(self):
        event = {
            "id": "evt-004",
            "summary": "Weekly 1:1",
            "start": {"dateTime": "2024-01-15T14:00:00Z"},
            "end": {"dateTime": "2024-01-15T14:30:00Z"},
            "recurringEventId": "evt-base",
        }
        resource = calendar_event_to_resource(event)
        assert resource.metadata["recurring"] is True


# ── Connector tests (mocked HTTP) ────────────────────────────────────────


class TestGoogleCalendarConnector:
    @pytest.fixture
    def connector(self):
        from navig.connectors.google_calendar.connector import GoogleCalendarConnector

        c = GoogleCalendarConnector()
        c.set_access_token("fake-calendar-token")
        return c

    def test_manifest(self, connector):
        assert connector.manifest.id == "google_calendar"
        assert connector.manifest.requires_oauth is True
        assert connector.manifest.domain.value == "calendar"

    def test_build_time_obj_datetime(self, connector):
        t = connector._build_time_obj("2024-01-15T09:00:00Z")
        assert t == {"dateTime": "2024-01-15T09:00:00Z"}

    def test_build_time_obj_date(self, connector):
        t = connector._build_time_obj("2024-01-15")
        assert t == {"date": "2024-01-15"}

    def test_build_time_obj_empty(self, connector):
        t = connector._build_time_obj("")
        assert "dateTime" in t

    def test_search_returns_resources(self, connector):
        events_response = {
            "items": [
                {
                    "id": "e1",
                    "summary": "Meeting",
                    "start": {"dateTime": "2024-01-15T10:00:00Z"},
                    "end": {"dateTime": "2024-01-15T11:00:00Z"},
                },
            ]
        }

        async def mock_get(path, params=None):
            return events_response

        connector._api_get = mock_get
        results = asyncio.run(connector.search("meeting"))
        assert len(results) == 1
        assert results[0].source == "google_calendar"
        assert results[0].title == "Meeting"

    def test_search_honours_limit(self, connector):
        """search(limit=N) must request and return at most N results."""
        all_events = [
            {
                "id": f"e{i}",
                "summary": f"Event {i}",
                "start": {"dateTime": "2024-01-15T10:00:00Z"},
                "end": {"dateTime": "2024-01-15T11:00:00Z"},
            }
            for i in range(10)
        ]
        requested_max_results = []

        async def mock_get(path, params=None):
            if params and "maxResults" in params:
                requested_max_results.append(params["maxResults"])
                return {"items": all_events[: params["maxResults"]]}
            return {"items": all_events}

        connector._api_get = mock_get
        results = asyncio.run(connector.search("event", limit=2))
        assert requested_max_results and requested_max_results[0] == 2
        assert len(results) <= 2

    def test_health_check(self, connector):
        async def mock_get(path, params=None):
            return {"items": []}

        connector._api_get = mock_get
        health = asyncio.run(connector.health_check())
        assert health.ok is True

    def test_check_conflicts(self, connector):
        events_response = {
            "items": [
                {
                    "id": "conflict-1",
                    "summary": "Existing Meeting",
                    "start": {"dateTime": "2024-01-15T10:00:00Z"},
                    "end": {"dateTime": "2024-01-15T11:00:00Z"},
                },
            ]
        }

        async def mock_get(path, params=None):
            return events_response

        connector._api_get = mock_get
        conflicts = asyncio.run(
            connector.check_conflicts("2024-01-15T09:30:00Z", "2024-01-15T10:30:00Z")
        )
        assert len(conflicts) == 1
        assert conflicts[0].title == "Existing Meeting"
