"""Tests for navig.agent.proactive.providers — CalendarEvent, EmailMessage, providers, MockCalendar."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from navig.agent.proactive.providers import (
    CalendarEvent,
    CalendarProvider,
    EmailMessage,
    EmailProvider,
    MockCalendar,
)


NOW = datetime(2024, 6, 15, 10, 0, 0)
LATER = datetime(2024, 6, 15, 11, 0, 0)


# ---------------------------------------------------------------------------
# CalendarEvent dataclass
# ---------------------------------------------------------------------------

class TestCalendarEvent:
    def test_required_fields(self):
        e = CalendarEvent(id="1", title="Meeting", start=NOW, end=LATER)
        assert e.id == "1"
        assert e.title == "Meeting"
        assert e.start == NOW
        assert e.end == LATER

    def test_optional_fields_default_none(self):
        e = CalendarEvent(id="1", title="M", start=NOW, end=LATER)
        assert e.location is None
        assert e.description is None
        assert e.attendees is None

    def test_location_can_be_set(self):
        e = CalendarEvent(id="1", title="M", start=NOW, end=LATER, location="Room A")
        assert e.location == "Room A"

    def test_attendees_can_be_set(self):
        e = CalendarEvent(id="1", title="M", start=NOW, end=LATER,
                          attendees=["a@b.com"])
        assert e.attendees == ["a@b.com"]

    def test_description_can_be_set(self):
        e = CalendarEvent(id="1", title="M", start=NOW, end=LATER,
                          description="Weekly sync")
        assert e.description == "Weekly sync"


# ---------------------------------------------------------------------------
# EmailMessage dataclass
# ---------------------------------------------------------------------------

class TestEmailMessage:
    def test_required_fields(self):
        m = EmailMessage(
            id="msg-1",
            subject="Hello",
            sender="alice@example.com",
            snippet="Hi there...",
            received_at=NOW,
        )
        assert m.id == "msg-1"
        assert m.subject == "Hello"
        assert m.sender == "alice@example.com"
        assert m.snippet == "Hi there..."
        assert m.received_at == NOW

    def test_read_defaults_false(self):
        m = EmailMessage(id="1", subject="S", sender="s@x.com", snippet="...", received_at=NOW)
        assert m.read is False

    def test_read_can_be_true(self):
        m = EmailMessage(id="1", subject="S", sender="s@x.com", snippet="...",
                         received_at=NOW, read=True)
        assert m.read is True


# ---------------------------------------------------------------------------
# CalendarProvider abstract
# ---------------------------------------------------------------------------

class TestCalendarProviderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            CalendarProvider()  # type: ignore


# ---------------------------------------------------------------------------
# EmailProvider abstract
# ---------------------------------------------------------------------------

class TestEmailProviderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            EmailProvider()  # type: ignore


# ---------------------------------------------------------------------------
# MockCalendar
# ---------------------------------------------------------------------------

class TestMockCalendar:
    def test_is_calendar_provider(self):
        assert issubclass(MockCalendar, CalendarProvider)

    def test_list_events_returns_list(self):
        mc = MockCalendar()
        result = asyncio.run(mc.list_events(NOW, LATER))
        assert isinstance(result, list)

    def test_list_events_returns_at_least_one(self):
        mc = MockCalendar()
        result = asyncio.run(mc.list_events(NOW, LATER))
        assert len(result) >= 1

    def test_list_events_returns_calendar_events(self):
        mc = MockCalendar()
        result = asyncio.run(mc.list_events(NOW, LATER))
        for event in result:
            assert isinstance(event, CalendarEvent)

    def test_mock_event_has_id(self):
        mc = MockCalendar()
        events = asyncio.run(mc.list_events(NOW, LATER))
        assert events[0].id

    def test_mock_event_has_title(self):
        mc = MockCalendar()
        events = asyncio.run(mc.list_events(NOW, LATER))
        assert events[0].title

    def test_mock_event_has_attendees(self):
        mc = MockCalendar()
        events = asyncio.run(mc.list_events(NOW, LATER))
        assert events[0].attendees and len(events[0].attendees) >= 1

    def test_create_event_returns_string_id(self):
        mc = MockCalendar()
        event = CalendarEvent(id="new", title="Test", start=NOW, end=LATER)
        result = asyncio.run(mc.create_event(event))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_create_event_returns_mock_id(self):
        mc = MockCalendar()
        event = CalendarEvent(id="new", title="Test", start=NOW, end=LATER)
        result = asyncio.run(mc.create_event(event))
        assert "mock" in result

    def test_list_events_same_start_end(self):
        """Edge case: same start and end time should not crash."""
        mc = MockCalendar()
        result = asyncio.run(mc.list_events(NOW, NOW))
        assert isinstance(result, list)
