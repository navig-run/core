"""Tests for navig.connectors.google_calendar.mappers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from navig.connectors.google_calendar.mappers import (
    _parse_event_time,
    calendar_event_to_resource,
)
from navig.connectors.types import ResourceType


class TestParseEventTime:
    def test_parses_datetime_string(self):
        result = _parse_event_time({"dateTime": "2024-03-15T10:00:00+00:00"})
        assert "2024-03-15" in result

    def test_parses_date_string(self):
        result = _parse_event_time({"date": "2024-06-01"})
        assert "2024-06-01" in result

    def test_empty_dict_returns_now(self):
        result = _parse_event_time({})
        # Should return a valid ISO string
        assert "T" in result or len(result) > 0

    def test_invalid_value_returned_as_is(self):
        result = _parse_event_time({"dateTime": "not-a-date"})
        assert result == "not-a-date"

    def test_prefers_datetime_over_date(self):
        result = _parse_event_time({"dateTime": "2024-01-01T09:00:00Z", "date": "2024-01-01"})
        assert "T" in result


class TestCalendarEventToResource:
    def _base_event(self, **kwargs) -> dict:
        event = {
            "id": "evt001",
            "summary": "Team Standup",
            "start": {"dateTime": "2024-03-15T09:00:00+00:00"},
            "end": {"dateTime": "2024-03-15T09:30:00+00:00"},
        }
        event.update(kwargs)
        return event

    def test_id_mapped(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.id == "evt001"

    def test_source_is_google_calendar(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.source == "google_calendar"

    def test_title_from_summary(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.title == "Team Standup"

    def test_default_title_when_no_summary(self):
        event = self._base_event()
        del event["summary"]
        r = calendar_event_to_resource(event)
        assert r.title == "(no title)"

    def test_resource_type_is_event(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.resource_type == ResourceType.EVENT

    def test_preview_from_description(self):
        r = calendar_event_to_resource(self._base_event(description="Important meeting"))
        assert r.preview == "Important meeting"

    def test_description_truncated_to_200(self):
        long_desc = "x" * 300
        r = calendar_event_to_resource(self._base_event(description=long_desc))
        assert len(r.preview) == 200

    def test_attendee_emails_extracted(self):
        attendees = [{"email": "a@x.com"}, {"email": "b@x.com"}, {"displayName": "no email"}]
        r = calendar_event_to_resource(self._base_event(attendees=attendees))
        assert "a@x.com" in r.metadata["attendees"]
        assert "b@x.com" in r.metadata["attendees"]
        assert len(r.metadata["attendees"]) == 2

    def test_all_day_event_detected(self):
        event = {
            "id": "allday",
            "summary": "Holiday",
            "start": {"date": "2024-12-25"},
            "end": {"date": "2024-12-25"},
        }
        r = calendar_event_to_resource(event)
        assert r.metadata["all_day"] is True

    def test_timed_event_not_all_day(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.metadata["all_day"] is False

    def test_recurring_detected(self):
        r = calendar_event_to_resource(self._base_event(recurringEventId="base001"))
        assert r.metadata["recurring"] is True

    def test_non_recurring_default_false(self):
        r = calendar_event_to_resource(self._base_event())
        assert r.metadata["recurring"] is False

    def test_html_link_mapped_to_url(self):
        r = calendar_event_to_resource(self._base_event(htmlLink="https://cal.google.com/e/123"))
        assert r.url == "https://cal.google.com/e/123"

    def test_location_in_metadata(self):
        r = calendar_event_to_resource(self._base_event(location="Room 101"))
        assert r.metadata["location"] == "Room 101"

    def test_organizer_email_in_metadata(self):
        r = calendar_event_to_resource(
            self._base_event(organizer={"email": "boss@company.com"})
        )
        assert r.metadata["organizer"] == "boss@company.com"
