"""Tests for navig.agent.proactive.providers — dataclasses, MockCalendar, MockEmail."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from navig.agent.proactive.providers import (
    CalendarEvent,
    EmailMessage,
    MockCalendar,
    MockEmail,
)

_NOW = datetime(2024, 6, 1, 10, 0)


class TestCalendarEvent:
    def test_required_fields(self) -> None:
        ev = CalendarEvent(
            id="1", title="Meeting", start=_NOW, end=_NOW + timedelta(hours=1)
        )
        assert ev.id == "1"
        assert ev.title == "Meeting"

    def test_optional_defaults(self) -> None:
        ev = CalendarEvent(id="x", title="T", start=_NOW, end=_NOW)
        assert ev.location is None
        assert ev.description is None
        assert ev.attendees is None

    def test_with_attendees(self) -> None:
        ev = CalendarEvent(
            id="2", title="Sync", start=_NOW, end=_NOW, attendees=["a@b.com"]
        )
        assert ev.attendees == ["a@b.com"]


class TestEmailMessage:
    def test_required_fields(self) -> None:
        msg = EmailMessage(
            id="m1", subject="Hi", sender="x@y.com", snippet="...", received_at=_NOW
        )
        assert msg.id == "m1"
        assert msg.sender == "x@y.com"

    def test_default_read_false(self) -> None:
        msg = EmailMessage(id="m2", subject="S", sender="a@b", snippet="s", received_at=_NOW)
        assert msg.read is False


class TestMockCalendar:
    @pytest.mark.asyncio
    async def test_list_events_returns_items(self) -> None:
        cal = MockCalendar()
        start = _NOW
        end = _NOW + timedelta(days=1)
        events = await cal.list_events(start, end)
        assert isinstance(events, list)
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_list_events_has_title(self) -> None:
        cal = MockCalendar()
        events = await cal.list_events(_NOW, _NOW + timedelta(days=1))
        assert events[0].title

    @pytest.mark.asyncio
    async def test_list_events_has_attendees(self) -> None:
        cal = MockCalendar()
        events = await cal.list_events(_NOW, _NOW + timedelta(days=1))
        assert events[0].attendees

    @pytest.mark.asyncio
    async def test_create_event_returns_str(self) -> None:
        cal = MockCalendar()
        ev = CalendarEvent(id="new", title="New", start=_NOW, end=_NOW)
        result = await cal.create_event(ev)
        assert isinstance(result, str)
        assert result


class TestMockEmail:
    @pytest.mark.asyncio
    async def test_list_unread_returns_items(self) -> None:
        em = MockEmail()
        msgs = await em.list_unread()
        assert isinstance(msgs, list)
        assert len(msgs) >= 1

    @pytest.mark.asyncio
    async def test_list_unread_respects_limit(self) -> None:
        em = MockEmail()
        msgs = await em.list_unread(limit=1)
        assert len(msgs) <= 1

    @pytest.mark.asyncio
    async def test_list_unread_has_subject(self) -> None:
        em = MockEmail()
        msgs = await em.list_unread()
        assert msgs[0].subject

    @pytest.mark.asyncio
    async def test_draft_email_returns_str(self) -> None:
        em = MockEmail()
        draft_id = await em.draft_email(["x@y.com"], "Subject", "Body text")
        assert isinstance(draft_id, str)
