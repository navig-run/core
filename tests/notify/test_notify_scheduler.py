"""notify.scheduler._due_briefings — window-based briefing firing.

The loop fires the daily AI briefing when a scheduled "HH:MM" instant falls in
the half-open window (last_check, now]. This replaced an exact-minute string
match that a slow tick (SMS PATCH + network email scan overrunning the 45s
interval) could skip, dropping the briefing for the whole day.
"""

from __future__ import annotations

from datetime import datetime

from navig.notify.scheduler import _due_briefings

_D = 2026, 7, 19  # a fixed date; the window logic is date-agnostic within a day


def _at(h, m, s=0):
    return datetime(*_D, h, m, s)


def test_fires_when_instant_falls_in_window():
    assert _due_briefings(_at(7, 29, 50), _at(7, 30, 40), ["07:30"]) == ["07:30"]


def test_startup_does_not_replay_past_times():
    # last_check == now (seeded at boot) → a time earlier today is NOT replayed.
    assert _due_briefings(_at(9, 0, 0), _at(9, 0, 0), ["07:30"]) == []


def test_missed_minute_still_fires():
    # A slow tick skips the 07:30 minute entirely; the window still contains the
    # instant, so it fires (a bit late) instead of being lost for the day.
    assert _due_briefings(_at(7, 29, 0), _at(7, 31, 0), ["07:30"]) == ["07:30"]


def test_not_yet_due():
    assert _due_briefings(_at(7, 29, 0), _at(7, 29, 30), ["07:30"]) == []


def test_fires_exactly_once_across_consecutive_windows():
    # Contiguous half-open windows: the instant is in the first, not the second.
    assert _due_briefings(_at(7, 29, 50), _at(7, 30, 0), ["07:30"]) == ["07:30"]
    assert _due_briefings(_at(7, 30, 0), _at(7, 30, 44), ["07:30"]) == []  # not re-fired


def test_malformed_or_out_of_range_times_skipped():
    due = _due_briefings(_at(7, 29, 0), _at(7, 31, 0), ["bad", "25:00", "07:30", "07:90"])
    assert due == ["07:30"]


def test_multiple_times_in_one_window_all_returned():
    # After a long stall both are due; the helper returns both (the loop dispatches
    # once). Contiguous windows still guarantee each fires only once overall.
    assert _due_briefings(_at(7, 29, 0), _at(7, 46, 0), ["07:30", "07:45"]) == ["07:30", "07:45"]


def test_window_spanning_midnight():
    last = datetime(2026, 7, 19, 23, 59, 15)
    now = datetime(2026, 7, 20, 0, 0, 30)
    assert _due_briefings(last, now, ["00:00"]) == ["00:00"]
    # A 23:59 time now resolves to *today* (day-2) 23:59, which is in the future.
    assert _due_briefings(last, now, ["23:59"]) == []


def test_empty_times_is_empty():
    assert _due_briefings(_at(7, 30, 0), _at(7, 31, 0), []) == []
