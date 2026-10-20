"""Slot times on the operator's local calendar — the only clock arithmetic here.

A slot is a wall-clock time (``"0800"``) on the operator's zone, which is the
SYSTEM zone (:mod:`navig.pim.clock` explains why). Everything that compares
instants does it on AWARE datetimes, so a slot on the night the clocks change is
still the slot the operator set — 08:00 is 08:00 on the 26th of October too —
and "120 minutes after the dose was due" is 120 real minutes even across the
change.

⚠ Python does WALL-CLOCK arithmetic on two datetimes that share a tzinfo:
``aware + timedelta`` moves the wall clock, and comparing or subtracting two of
them ignores their offsets. Across a DST change that is an hour wrong. So every
elapsed-time step here goes through UTC (:func:`shift`, :func:`before_or_at`) — never
``now + timedelta`` on a local datetime.

Pure functions over explicit inputs (``now``, ``tz``), so tests pass a zone and a
moment instead of freezing clocks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo


def local_tz() -> tzinfo:
    from navig.pim.clock import LOCAL  # noqa: PLC0415

    return LOCAL


def local_now(tz: tzinfo | None = None) -> datetime:
    return datetime.now(tz or local_tz())


def shift(instant: datetime, delta: timedelta) -> datetime:
    """*instant* moved by *delta* of REAL time, returned in its own zone."""
    return (instant.astimezone(UTC) + delta).astimezone(instant.tzinfo)


def before_or_at(a: datetime, b: datetime) -> bool:
    """``a <= b`` as instants (offsets honoured, unlike a same-tzinfo compare)."""
    return a.astimezone(UTC) <= b.astimezone(UTC)


def parse_slot(slot: str) -> time:
    """``"0800"`` / ``"08:00"`` → ``time(8, 0)``. Raises ``ValueError``."""
    s = (slot or "").replace(":", "")
    if len(s) != 4 or not s.isdigit():
        raise ValueError(f"bad slot {slot!r}")
    hour, minute = int(s[:2]), int(s[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"bad slot {slot!r}")
    return time(hour, minute)


def slot_instant(day: date, slot: str, tz: tzinfo) -> datetime:
    """The aware instant *slot* names on *day*, in *tz*.

    A wall time that does not exist (inside a spring-forward gap) is normalised
    through UTC, which lands it just after the gap — the dose is still asked, a
    little late, rather than never.
    """
    naive = datetime.combine(day, parse_slot(slot))
    aware = naive.replace(tzinfo=tz)
    return aware.astimezone(UTC).astimezone(tz)


def occurrence_day(now: datetime, slot: str, tz: tzinfo, *, lag: timedelta = timedelta(0)) -> date:
    """The local day of the most recent occurrence of *slot* at or before ``now - lag``.

    ``lag`` lets the missed-dose nudge (fired ``missed_after`` minutes after the
    slot) find the dose it is about, even when that crosses midnight: a 23:00
    dose nudged at 01:00 is yesterday's dose.
    """
    ref = shift(now, -lag).astimezone(tz)
    today = ref.date()
    if before_or_at(slot_instant(today, slot, tz), ref):
        return today
    return today - timedelta(days=1)


def local_date(instant: datetime, tz: tzinfo) -> date:
    """The operator's calendar day for an instant (UTC in, local day out)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(tz).date()


def slot_cron(slot: str, *, plus_minutes: int = 0) -> str:
    """A daily cron line for *slot* (+ an offset that may wrap past midnight)."""
    t = parse_slot(slot)
    total = (t.hour * 60 + t.minute + plus_minutes) % (24 * 60)
    return f"{total % 60} {total // 60} * * *"


def water_slots(every_hours: int, from_hour: int, to_hour: int) -> list[str]:
    """Reminder slots every *every_hours* from *from_hour* (inclusive) to *to_hour* (exclusive)."""
    if every_hours <= 0 or from_hour >= to_hour:
        return []
    return [f"{h:02d}00" for h in range(from_hour, to_hour, every_hours)]


def is_late(now: datetime, day: date, slot: str, tz: tzinfo, minutes: int) -> bool:
    """True once *minutes* have passed since *slot* on *day*."""
    return before_or_at(shift(slot_instant(day, slot, tz), timedelta(minutes=minutes)), now)
