"""Find open meeting slots in a set of busy intervals — pure, no I/O.

Kept apart from the connector so the arithmetic (working hours, weekends, time zones,
overlapping busy blocks from several people) is testable without Google.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo


def merge_busy(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Sort and merge overlapping/touching busy intervals."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def find_free_slots(
    busy: list[tuple[datetime, datetime]],
    *,
    start: datetime,
    end: datetime,
    duration: timedelta,
    tz: tzinfo,
    work_start: time = time(9, 0),
    work_end: time = time(17, 0),
    weekdays_only: bool = True,
    step: timedelta = timedelta(minutes=15),
    limit: int = 5,
) -> list[tuple[datetime, datetime]]:
    """The first ``limit`` slots of ``duration`` inside working hours that avoid ``busy``.

    ``start``/``end``/``busy`` must be timezone-aware; working hours are read in ``tz``
    (the operator's zone), so "9 to 5" means their 9 to 5 whatever zone the calendar
    API answered in. Slot starts are aligned to ``step`` so proposals look like times a
    person would pick (10:00, 10:15), not 10:07.
    """
    if duration <= timedelta(0) or end <= start:
        return []
    blocks = merge_busy(busy)
    out: list[tuple[datetime, datetime]] = []
    day = start.astimezone(tz).date()
    last_day = end.astimezone(tz).date()
    while day <= last_day and len(out) < limit:
        if not (weekdays_only and day.weekday() >= 5):
            day_open = datetime.combine(day, work_start, tzinfo=tz)
            day_close = datetime.combine(day, work_end, tzinfo=tz)
            cursor = max(day_open, start.astimezone(tz))
            cursor = _align(cursor, day_open, step)
            limit_at = min(day_close, end.astimezone(tz))
            while cursor + duration <= limit_at and len(out) < limit:
                slot_end = cursor + duration
                clash = next((b for b in blocks if b[0] < slot_end and b[1] > cursor), None)
                if clash is None:
                    out.append((cursor, slot_end))
                    cursor = slot_end
                else:
                    cursor = _align(clash[1].astimezone(tz), day_open, step)
        day += timedelta(days=1)
    return out


def _align(moment: datetime, anchor: datetime, step: timedelta) -> datetime:
    """Round ``moment`` UP to the next ``step`` boundary counted from ``anchor``."""
    if moment <= anchor:
        return anchor
    steps = -(-(moment - anchor) // step)  # ceil division on timedeltas
    return anchor + steps * step
