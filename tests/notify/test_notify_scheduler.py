"""notify.scheduler._due_briefings — window-based briefing firing.

The loop fires the daily AI briefing when a scheduled "HH:MM" instant falls in
the half-open window (last_check, now]. This replaced an exact-minute string
match that a slow tick (SMS PATCH + network email scan overrunning the 45s
interval) could skip, dropping the briefing for the whole day.
"""

from __future__ import annotations

from datetime import datetime

import pytest

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
    # 23:59 is NOT due here because its instant (day-1 23:59:00) precedes the window
    # start (23:59:15) — it already fired in the previous window. It is *not* skipped
    # because it "resolves to tomorrow"; resolving it against tomorrow's date only was
    # the bug fixed below in TestStalledTickAcrossMidnight.
    assert _due_briefings(last, now, ["23:59"]) == []


def test_empty_times_is_empty():
    assert _due_briefings(_at(7, 30, 0), _at(7, 31, 0), []) == []


class TestStalledTickAcrossMidnight:
    """The instant must resolve against every date the window spans, not just now's.

    Regression: `_due_briefings` built the instant with `now.replace(hour=…, minute=…)`,
    i.e. always on *now's* date. The whole point of the window design is that a slow
    tick "cannot skip the briefing's minute" — but when the stalled tick carried the
    window across midnight, yesterday's 23:55 was looked up on *today's* date, landed
    in the future, and was dropped for the day. The 45s tick is documented as able to
    overrun (per-tick SMS PATCH + network email scan), so the stall is expected, not
    hypothetical.
    """

    # The tick stalls from 23:50 to 00:05 the next day.
    LAST = datetime(2026, 7, 19, 23, 50, 0)
    NOW = datetime(2026, 7, 20, 0, 5, 0)

    def test_late_evening_time_still_fires(self):
        assert _due_briefings(self.LAST, self.NOW, ["23:55"]) == ["23:55"], (
            "a briefing scheduled before midnight was dropped because the stalled "
            "window resolved it against the following day"
        )

    def test_after_midnight_time_still_fires(self):
        assert _due_briefings(self.LAST, self.NOW, ["00:02"]) == ["00:02"]

    def test_times_on_both_sides_of_midnight_both_fire(self):
        assert _due_briefings(self.LAST, self.NOW, ["23:55", "00:02"]) == ["23:55", "00:02"]

    def test_time_outside_the_stalled_window_does_not_fire(self):
        # 23:45 passed before the window opened; 08:00 has not arrived.
        assert _due_briefings(self.LAST, self.NOW, ["23:45", "08:00"]) == []

    def test_time_matching_on_both_dates_is_returned_once(self):
        # A >24h stall: 07:30 lands in the window on both the 19th and the 20th.
        last = datetime(2026, 7, 19, 7, 0, 0)
        now = datetime(2026, 7, 20, 8, 0, 0)
        assert _due_briefings(last, now, ["07:30"]) == ["07:30"]

    def test_malformed_entries_still_skipped_across_midnight(self):
        due = _due_briefings(self.LAST, self.NOW, ["bad", "25:00", "07:90", "23:55"])
        assert due == ["23:55"]


# ── The window anchor must never move backwards ───────────────────────────────
#
# The loop advanced `last_check = now` unconditionally. When the WALL clock steps
# back — DST fall-back, or an NTP correction — the anchor regressed and the window
# re-covered an interval already handled, so every briefing time inside the repeated
# interval fired a SECOND time. A monotonic clock is not an option here: briefing
# times are wall-clock "HH:MM" by definition.
#
# These drive the real `_loop` with a scripted clock rather than testing a predicate,
# so they measure the behaviour that actually shipped.


class _ClockExhausted(Exception):
    """Raised by the fake clock to terminate the loop under test."""


class _ScriptedDatetime:
    """Stands in for the scheduler's module-level ``datetime``.

    ``now()`` walks a scripted sequence; every other attribute (notably
    ``combine``, which ``_due_briefings`` needs) delegates to the real class.
    """

    def __init__(self, values):
        self._values = list(values)

    def now(self):
        if not self._values:
            raise _ClockExhausted
        return self._values.pop(0)

    def __getattr__(self, name):
        return getattr(datetime, name)


async def _run_loop_with_clock(monkeypatch, ticks, times=("02:30",)):
    """Run the scheduler loop over a scripted clock; return the briefing fire count."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from navig.modules import registry as modules_registry
    from navig.notify import briefings, prefs, scheduler, sms_webhook_config

    fired: list[int] = []

    async def _count():
        fired.append(1)

    monkeypatch.setattr(scheduler, "datetime", _ScriptedDatetime(ticks))
    monkeypatch.setattr(scheduler, "_TICK_SECONDS", 0)
    monkeypatch.setattr(briefings, "build_and_dispatch_briefing", _count)
    monkeypatch.setattr(
        prefs,
        "get_settings",
        lambda: {"briefing_enabled": True, "briefing_times": list(times)},
    )
    monkeypatch.setattr(sms_webhook_config, "auto_configure", AsyncMock())
    monkeypatch.setattr(
        modules_registry, "get_registry", lambda: MagicMock(is_enabled=lambda _n: False)
    )

    with pytest.raises(_ClockExhausted):
        await asyncio.wait_for(scheduler._loop(None), timeout=5)
    return len(fired)


async def test_a_backwards_clock_step_does_not_re_fire_the_briefing(monkeypatch):
    """DST fall-back: 02:30 already fired, then the hour repeats."""
    fires = await _run_loop_with_clock(
        monkeypatch,
        [
            datetime(2026, 11, 1, 2, 29, 50),   # seed anchor
            datetime(2026, 11, 1, 2, 30, 5),    # 02:30 fires — correct, once
            datetime(2026, 11, 1, 2, 59, 55),   # nothing due
            datetime(2026, 11, 1, 2, 0, 5),     # ← clock steps BACK an hour
            datetime(2026, 11, 1, 2, 30, 5),    # 02:30 again in the repeated hour
            datetime(2026, 11, 1, 2, 59, 59),
        ],
    )
    assert fires == 1, (
        f"the briefing fired {fires} times — a backwards clock step replayed the "
        "window, so every briefing in the repeated interval went out twice"
    )


async def test_briefings_resume_once_the_clock_catches_up(monkeypatch):
    """Holding the anchor must not wedge the scheduler permanently."""
    fires = await _run_loop_with_clock(
        monkeypatch,
        [
            datetime(2026, 11, 1, 1, 0, 0),     # seed anchor
            datetime(2026, 11, 1, 0, 30, 0),    # ← steps back; anchor held at 01:00
            datetime(2026, 11, 1, 1, 30, 0),    # still behind the held anchor? no — ahead
            datetime(2026, 11, 1, 8, 0, 5),     # 08:00 is due in (01:30, 08:00:05]
        ],
        times=("08:00",),
    )
    assert fires == 1, "a normal briefing after the step-back must still fire"


async def test_a_normal_forward_sequence_still_fires(monkeypatch):
    """Guard against over-blocking: nothing about the fix may suppress the happy path."""
    fires = await _run_loop_with_clock(
        monkeypatch,
        [
            datetime(2026, 7, 30, 7, 29, 50),
            datetime(2026, 7, 30, 7, 30, 40),   # 07:30 due
            datetime(2026, 7, 30, 7, 31, 25),
        ],
        times=("07:30",),
    )
    assert fires == 1
