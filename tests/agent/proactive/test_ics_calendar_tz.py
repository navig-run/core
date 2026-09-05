"""tz-safety of ICSCalendarProvider._filter_events (no ``icalendar`` required).

These live in their own file because ``test_ics_calendar.py`` does a module-level
``pytest.importorskip("icalendar")`` for its parsing tests — which would also skip these
pure-logic tests. A tz-aware ``DTSTART`` (``…Z`` / ``TZID``) used to raise
``TypeError: can't compare offset-naive and offset-aware datetimes`` against the naive
``datetime.now()`` bounds the CLI passes, crashing ``navig calendar list`` and the
proactive calendar watcher. ``_filter_events`` now coerces both sides to aware.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from navig.agent.proactive.ics_calendar import ICSCalendarProvider, _as_aware
from navig.agent.proactive.providers import CalendarEvent


def _p() -> ICSCalendarProvider:
    return ICSCalendarProvider(url="https://x.com/cal.ics")


def _evt(title: str, start: datetime) -> CalendarEvent:
    return CalendarEvent(id=title, title=title, start=start, end=start + timedelta(hours=1))


# A window wide enough that a fixed instant lands inside it for any real local tz (±14h).
_WIDE_START = datetime(2024, 6, 14, 0, 0)
_WIDE_END = datetime(2024, 6, 17, 0, 0)


def test_tz_aware_event_vs_naive_bounds_does_not_crash():
    aware = _evt("aware", datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    result = _p()._filter_events([aware], _WIDE_START, _WIDE_END)
    assert result == [aware]  # old code raised TypeError here


def test_mixed_naive_and_aware_events_both_filter():
    naive = _evt("naive", datetime(2024, 6, 15, 12, 0))
    aware = _evt("aware", datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc))
    result = _p()._filter_events([naive, aware], _WIDE_START, _WIDE_END)
    assert {e.title for e in result} == {"naive", "aware"}


def test_aware_event_outside_window_is_excluded():
    far = _evt("far", datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert _p()._filter_events([far], _WIDE_START, _WIDE_END) == []


def test_naive_only_still_filters_normally():
    inside = _evt("in", datetime(2024, 6, 15, 12, 0))
    before = _evt("before", datetime(2020, 1, 1, 0, 0))
    result = _p()._filter_events([inside, before], _WIDE_START, _WIDE_END)
    assert [e.title for e in result] == ["in"]


def test_as_aware_helper():
    naive = datetime(2024, 6, 15, 12, 0)
    aware = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert _as_aware(naive).tzinfo is not None  # naive → aware (local)
    assert _as_aware(aware) is aware  # already aware → unchanged
