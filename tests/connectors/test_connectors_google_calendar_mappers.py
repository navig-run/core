"""Tests for navig.connectors.google_calendar.mappers — _parse_event_time, calendar_event_to_resource."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from navig.connectors.google_calendar.mappers import (
    _parse_event_time,
    calendar_event_to_resource,
)
from navig.connectors.types import ResourceType


# ---------------------------------------------------------------------------
# _parse_event_time
# ---------------------------------------------------------------------------

class TestParseEventTime:
    def test_datetime_string_parsed(self):
        result = _parse_event_time({"dateTime": "2024-06-15T14:30:00+00:00"})
        assert "2024-06-15" in result

    def test_date_only_string_parsed(self):
        result = _parse_event_time({"date": "2024-06-15"})
        assert "2024-06-15" in result

    def test_datetime_takes_precedence_over_date(self):
        result = _parse_event_time({
            "dateTime": "2024-06-15T14:30:00+00:00",
            "date": "2024-06-16",
        })
        assert "2024-06-15" in result
        assert "2024-06-16" not in result

    def test_empty_dict_returns_current_time_iso(self):
        result = _parse_event_time({})
        # Should be a parseable ISO timestamp
        assert "T" in result or len(result) >= 10

    def test_invalid_datetime_returns_original_string(self):
        result = _parse_event_time({"dateTime": "not-a-date"})
        assert result == "not-a-date"

    def test_returns_iso_string(self):
        result = _parse_event_time({"dateTime": "2024-01-01T10:00:00+00:00"})
        assert isinstance(result, str)

    def test_date_only_gets_utc_timezone(self):
        result = _parse_event_time({"date": "2024-03-20"})
        # All-day events should be assigned UTC
        assert "+00:00" in result or "Z" in result or "UTC" in result

    def test_datetime_with_offset(self):
        result = _parse_event_time({"dateTime": "2024-06-15T14:30:00+05:30"})
        assert "2024-06-15" in result


# ---------------------------------------------------------------------------
# calendar_event_to_resource
# ---------------------------------------------------------------------------

class TestCalendarEventToResource:
    def _make_event(self, **kwargs):
        base = {
            "id": "event-abc",
            "summary": "Team Meeting",
            "description": "Discuss Q3 roadmap",
            "start": {"dateTime": "2024-06-15T10:00:00+00:00"},
            "end": {"dateTime": "2024-06-15T11:00:00+00:00"},
            "location": "Conference Room 1",
            "attendees": [
                {"email": "alice@example.com"},
                {"email": "bob@example.com"},
            ],
            "htmlLink": "https://calendar.google.com/event?id=abc",
        }
        base.update(kwargs)
        return base

    def test_source_is_google_calendar(self):
        r = calendar_event_to_resource(self._make_event())
        assert r.source == "google_calendar"

    def test_id_mapped(self):
        r = calendar_event_to_resource(self._make_event(id="my-event-id"))
        assert r.id == "my-event-id"

    def test_title_from_summary(self):
        r = calendar_event_to_resource(self._make_event(summary="Sprint Planning"))
        assert r.title == "Sprint Planning"

    def test_default_title_when_no_summary(self):
        event = self._make_event()
        del event["summary"]
        r = calendar_event_to_resource(event)
        assert r.title == "(no title)"

    def test_url_from_htmlLink(self):
        r = calendar_event_to_resource(self._make_event())
        assert r.url == "https://calendar.google.com/event?id=abc"

    def test_resource_type_is_event(self):
        r = calendar_event_to_resource(self._make_event())
        assert r.resource_type == ResourceType.EVENT

    def test_preview_from_description(self):
        r = calendar_event_to_resource(self._make_event(description="Meeting notes"))
        assert "Meeting notes" in r.preview

    def test_preview_truncated_to_200(self):
        long_desc = "x" * 300
        r = calendar_event_to_resource(self._make_event(description=long_desc))
        assert len(r.preview) <= 200

    def test_attendee_emails_in_metadata(self):
        r = calendar_event_to_resource(self._make_event())
        assert "alice@example.com" in r.metadata["attendees"]
        assert "bob@example.com" in r.metadata["attendees"]

    def test_no_attendees_empty_list(self):
        r = calendar_event_to_resource(self._make_event(attendees=[]))
        assert r.metadata["attendees"] == []

    def test_attendees_without_email_skipped(self):
        r = calendar_event_to_resource(self._make_event(
            attendees=[{"displayName": "No Email Person"}]
        ))
        assert r.metadata["attendees"] == []

    def test_location_in_metadata(self):
        r = calendar_event_to_resource(self._make_event(location="Conf Room A"))
        assert r.metadata["location"] == "Conf Room A"

    def test_all_day_event_detection(self):
        event = self._make_event()
        event["start"] = {"date": "2024-06-15"}
        event["end"] = {"date": "2024-06-15"}
        r = calendar_event_to_resource(event)
        assert r.metadata["all_day"] is True

    def test_timed_event_not_all_day(self):
        r = calendar_event_to_resource(self._make_event())
        assert r.metadata["all_day"] is False

    def test_recurring_event_detection(self):
        r = calendar_event_to_resource(
            self._make_event(recurringEventId="base-event-id")
        )
        assert r.metadata["recurring"] is True

    def test_non_recurring_event(self):
        r = calendar_event_to_resource(self._make_event())
        assert r.metadata["recurring"] is False

    def test_organizer_in_metadata(self):
        r = calendar_event_to_resource(
            self._make_event(organizer={"email": "organizer@example.com"})
        )
        assert r.metadata["organizer"] == "organizer@example.com"

    def test_timestamp_from_start(self):
        r = calendar_event_to_resource(self._make_event())
        assert "2024-06-15" in r.timestamp

    def test_empty_event_no_crash(self):
        # Minimal event should not raise
        r = calendar_event_to_resource({})
        assert r.source == "google_calendar"
        assert r.title == "(no title)"

    def test_metadata_has_status(self):
        r = calendar_event_to_resource(self._make_event(status="confirmed"))
        assert r.metadata["status"] == "confirmed"
