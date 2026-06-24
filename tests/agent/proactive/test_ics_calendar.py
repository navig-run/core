"""
Tests for navig.agent.proactive.ics_calendar
"""

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.proactive.ics_calendar import ICSCalendarProvider
from navig.agent.proactive.providers import CalendarEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_event(
    title: str = "Meeting",
    start: datetime | None = None,
    end: datetime | None = None,
) -> CalendarEvent:
    start = start or datetime(2024, 6, 15, 10, 0)
    end = end or start + timedelta(hours=1)
    return CalendarEvent(
        id="evt-1",
        title=title,
        start=start,
        end=end,
    )


# ---------------------------------------------------------------------------
# ICSCalendarProvider.__init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_requires_url_or_path(self):
        with pytest.raises(ValueError, match="url.*path"):
            ICSCalendarProvider()  # neither url nor path

    def test_accepts_url(self):
        p = ICSCalendarProvider(url="https://example.com/cal.ics")
        assert p.url == "https://example.com/cal.ics"
        assert p.path is None

    def test_accepts_path(self, tmp_path):
        cal_file = tmp_path / "cal.ics"
        cal_file.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        p = ICSCalendarProvider(path=cal_file)
        assert p.path == cal_file
        assert p.url is None

    def test_default_cache_minutes(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        assert p.cache_minutes == 5

    def test_custom_cache_minutes(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics", cache_minutes=15)
        assert p.cache_minutes == 15

    def test_initial_cache_is_none(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        assert p._cache is None
        assert p._cache_time is None


# ---------------------------------------------------------------------------
# _to_datetime
# ---------------------------------------------------------------------------


class TestToDatetime:
    def test_passthrough_datetime(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        dt = datetime(2024, 6, 15, 10, 30)
        result = p._to_datetime(dt)
        assert result == dt

    def test_converts_date_to_datetime_at_midnight(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        d = date(2024, 6, 15)
        result = p._to_datetime(d)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 0
        assert result.minute == 0


# ---------------------------------------------------------------------------
# _parse_attendees
# ---------------------------------------------------------------------------


class TestParseAttendees:
    def _make_component(self, attendees):
        component = MagicMock()
        component.get.side_effect = lambda key, default=None: (
            attendees if key == "attendee" else default
        )
        return component

    def test_empty_attendees(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        comp = self._make_component([])
        result = p._parse_attendees(comp)
        assert result == []

    def test_mailto_stripped(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        attendee = MagicMock()
        attendee.__str__ = lambda s: "mailto:alice@example.com"
        comp = self._make_component([attendee])
        result = p._parse_attendees(comp)
        assert result == ["alice@example.com"]

    def test_multiple_attendees(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        attndees = []
        for email in ("alice@example.com", "bob@example.com"):
            a = MagicMock()
            a.__str__ = lambda s, e=email: f"mailto:{e}"
            attndees.append(a)
        comp = self._make_component(attndees)
        result = p._parse_attendees(comp)
        assert "alice@example.com" in result
        assert "bob@example.com" in result


# ---------------------------------------------------------------------------
# _filter_events
# ---------------------------------------------------------------------------


class TestFilterEvents:
    def test_event_within_range(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        start = datetime(2024, 6, 15, 9, 0)
        end = datetime(2024, 6, 15, 17, 0)
        evt = _make_event(start=datetime(2024, 6, 15, 10, 0))
        result = p._filter_events([evt], start, end)
        assert len(result) == 1

    def test_event_before_range_excluded(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        start = datetime(2024, 6, 15, 12, 0)
        end = datetime(2024, 6, 15, 17, 0)
        evt = _make_event(start=datetime(2024, 6, 15, 10, 0))
        result = p._filter_events([evt], start, end)
        assert result == []

    def test_event_after_range_excluded(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        start = datetime(2024, 6, 15, 9, 0)
        end = datetime(2024, 6, 15, 10, 0)
        evt = _make_event(start=datetime(2024, 6, 15, 11, 0))
        result = p._filter_events([evt], start, end)
        assert result == []

    def test_empty_events(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        result = p._filter_events([], datetime.now(), datetime.now() + timedelta(hours=1))
        assert result == []


# ---------------------------------------------------------------------------
# _parse_vevent
# ---------------------------------------------------------------------------


class TestParseVevent:
    def _make_component(self, *, dtstart=None, dtend=None, uid="evt-1", summary="Meeting"):
        component = MagicMock()

        def _get(key, default=None):
            vals = {
                "dtstart": dtstart,
                "dtend": dtend,
                "uid": uid,
                "summary": summary,
                "location": "",
                "description": "",
                "attendee": [],
            }
            return vals.get(key, default)

        component.get.side_effect = _get
        return component

    def test_missing_dtstart_returns_none(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        comp = self._make_component(dtstart=None)
        assert p._parse_vevent(comp) is None

    def test_valid_vevent_returns_calendar_event(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")

        start_mock = MagicMock()
        start_mock.dt = datetime(2024, 6, 15, 10, 0)
        end_mock = MagicMock()
        end_mock.dt = datetime(2024, 6, 15, 11, 0)

        comp = self._make_component(dtstart=start_mock, dtend=end_mock)
        result = p._parse_vevent(comp)

        assert result is not None
        assert isinstance(result, CalendarEvent)
        assert result.title == "Meeting"

    def test_missing_dtend_defaults_to_1h(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")

        start_mock = MagicMock()
        start_mock.dt = datetime(2024, 6, 15, 10, 0)

        comp = self._make_component(dtstart=start_mock, dtend=None)
        result = p._parse_vevent(comp)

        assert result is not None
        assert result.end == datetime(2024, 6, 15, 11, 0)

    def test_exception_in_component_returns_none(self):
        p = ICSCalendarProvider(url="https://x.com/cal.ics")
        bad_component = MagicMock()
        bad_component.get.side_effect = RuntimeError("bad data")
        result = p._parse_vevent(bad_component)
        assert result is None


# ---------------------------------------------------------------------------
# create_event raises NotImplementedError
# ---------------------------------------------------------------------------


def test_create_event_not_implemented():
    p = ICSCalendarProvider(url="https://x.com/cal.ics")
    evt = _make_event()
    with pytest.raises(NotImplementedError):
        _run(p.create_event(evt))


# ---------------------------------------------------------------------------
# _fetch_ics from file
# ---------------------------------------------------------------------------


def test_fetch_ics_from_file(tmp_path):
    cal_content = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
    cal_file = tmp_path / "cal.ics"
    cal_file.write_text(cal_content, encoding="utf-8")

    p = ICSCalendarProvider(path=cal_file)
    result = _run(p._fetch_ics())
    assert "BEGIN:VCALENDAR" in result


def test_fetch_ics_missing_file(tmp_path):
    nonexistent = tmp_path / "missing.ics"
    p = ICSCalendarProvider(path=nonexistent)
    result = _run(p._fetch_ics())
    assert result is None


# ---------------------------------------------------------------------------
# list_events — requires icalendar; skip if not installed
# ---------------------------------------------------------------------------


icalendar = pytest.importorskip("icalendar", reason="icalendar not installed")


def _make_ics(events: list[dict]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//Test//EN"]
    for evt in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{evt.get('uid', 'test-uid')}",
            f"SUMMARY:{evt.get('summary', 'Test Event')}",
            f"DTSTART:{evt.get('dtstart', '20240615T100000')}",
            f"DTEND:{evt.get('dtend', '20240615T110000')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def test_list_events_from_file(tmp_path):
    ics_data = _make_ics([{"summary": "Team Standup"}])
    cal_file = tmp_path / "cal.ics"
    cal_file.write_bytes(ics_data.encode("utf-8"))

    p = ICSCalendarProvider(path=cal_file)
    start = datetime(2024, 6, 15, 0, 0)
    end = datetime(2024, 6, 15, 23, 59)
    events = _run(p.list_events(start, end))

    assert len(events) == 1
    assert events[0].title == "Team Standup"


def test_list_events_uses_cache(tmp_path):
    ics_data = _make_ics([{"summary": "Event A"}])
    cal_file = tmp_path / "cal.ics"
    cal_file.write_bytes(ics_data.encode("utf-8"))

    provider = ICSCalendarProvider(path=cal_file)
    start = datetime(2024, 6, 15, 0, 0)
    end = datetime(2024, 6, 15, 23, 59)

    # First call populates cache
    events1 = _run(provider.list_events(start, end))
    # Overwrite file — should still return cached events
    cal_file.write_bytes(b"garbage")
    events2 = _run(provider.list_events(start, end))

    assert len(events1) == len(events2)
