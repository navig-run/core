"""A wall-clock time stays that wall-clock time across a daylight-saving change.

Regression: `local_now()` returned `datetime.now().astimezone()`, a FIXED offset —
today's. Every PIM date is built from it, so in October (summer offset) the
appointment "lundi 26 octobre 2026 à 10h30" — the day after Europe's clocks go
back — was stored as 08:30Z and shown, and pinged, at 09:30.

These assertions hold in ANY system zone: on a machine with no DST they pass
trivially, and on one with DST they fail against the fixed-offset clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from navig.pim.clock import LOCAL, from_utc_iso, local_now, to_local, to_utc_iso
from navig.pim.dates import find_when
from navig.store.board import BoardStore


def _round_trip(when: datetime) -> datetime:
    back = to_local(from_utc_iso(to_utc_iso(when)))
    assert back is not None
    return back


def test_local_now_carries_the_zone_rules_not_one_offset() -> None:
    assert local_now().tzinfo is LOCAL


def test_an_appointment_after_the_clock_change_keeps_its_time() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)
    found = find_when("Ce rendez-vous a lieu le lundi 26 octobre 2026 à 10h30", now)
    assert found is not None
    back = _round_trip(found[0])
    assert (back.month, back.day, back.hour, back.minute) == (10, 26, 10, 30)


def test_every_day_of_a_year_round_trips_at_the_same_wall_time() -> None:
    start = datetime(2026, 1, 1, 10, 30, tzinfo=LOCAL)
    for offset in range(0, 366, 1):
        when = start + timedelta(days=offset)
        back = _round_trip(when)
        assert (back.hour, back.minute) == (10, 30), when


def test_a_lead_counts_back_in_wall_clock_days() -> None:
    due = datetime(2026, 10, 26, 10, 30, tzinfo=LOCAL)
    back = _round_trip(due - timedelta(days=3))
    assert (back.day, back.hour, back.minute) == (23, 10, 30)


def test_a_weekly_task_stays_at_its_hour_across_the_change(tmp_path) -> None:
    store = BoardStore(tmp_path / "board.db")
    due = datetime(2026, 10, 19, 10, 30, tzinfo=LOCAL)  # a Monday before the change
    todo = store.create_todo("Standup", due_at=to_utc_iso(due), recur="weekly")
    done = store.complete_todo(todo["id"])
    assert done is not None
    nxt = to_local(from_utc_iso(done["due_at"]))
    assert nxt is not None
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (10, 26, 10, 30)
