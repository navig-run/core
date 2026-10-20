"""Free/busy + slot finding for Google Calendar, and explicit invitation sending.

The response fixture has the exact shape Google's ``POST /freeBusy`` returns,
including the ``notFound`` error for someone who does not share their calendar —
which must surface as UNKNOWN, never as "free all week".
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from navig.connectors.google_calendar.connector import GoogleCalendarConnector
from navig.connectors.google_calendar.slots import find_free_slots, merge_busy
from navig.connectors.types import Action, ActionType

PARIS = timezone(timedelta(hours=2))
MON = datetime(2026, 10, 5, 8, 0, tzinfo=PARIS)  # a Monday, 08:00 local


def _at(day_offset, hh, mm=0, tz=PARIS):
    base = MON + timedelta(days=day_offset)
    return base.replace(hour=hh, minute=mm, tzinfo=tz)


# ── slot arithmetic ──────────────────────────────────────────────────────────


def test_merge_busy_joins_overlaps_and_touching_blocks():
    merged = merge_busy([(_at(0, 10), _at(0, 11)), (_at(0, 10, 30), _at(0, 12)), (_at(0, 12), _at(0, 13))])
    assert merged == [(_at(0, 10), _at(0, 13))]


def test_slots_skip_busy_time_and_stay_in_working_hours():
    busy = [(_at(0, 9), _at(0, 10, 30)), (_at(0, 11), _at(0, 16, 45))]
    slots = find_free_slots(busy, start=MON, end=_at(0, 23), duration=timedelta(minutes=30), tz=PARIS)
    assert slots == [(_at(0, 10, 30), _at(0, 11))]  # 16:45–17:00 is too short


def test_busy_blocks_from_another_zone_are_respected():
    # 08:00–09:00 UTC is 10:00–11:00 in Paris.
    busy = [(datetime(2026, 10, 5, 8, 0, tzinfo=UTC), datetime(2026, 10, 5, 9, 0, tzinfo=UTC))]
    slots = find_free_slots(busy, start=MON, end=_at(0, 23), duration=timedelta(hours=1), tz=PARIS, limit=3)
    assert slots[:2] == [(_at(0, 9), _at(0, 10)), (_at(0, 11), _at(0, 12))]


def test_weekends_are_skipped_unless_asked():
    fri_evening = _at(4, 18)
    kw = dict(start=fri_evening, end=_at(7, 23), duration=timedelta(hours=1), tz=PARIS, limit=1)
    assert find_free_slots([], **kw)[0][0].date() == _at(7, 9).date()  # next Monday
    assert find_free_slots([], weekdays_only=False, **kw)[0][0] == _at(5, 9)  # Saturday


def test_slot_starts_are_aligned_to_the_step():
    busy = [(_at(0, 9), _at(0, 9, 7))]
    slots = find_free_slots(busy, start=MON, end=_at(0, 23), duration=timedelta(minutes=30), tz=PARIS, limit=1)
    assert slots[0][0] == _at(0, 9, 15)


def test_custom_hours_and_limit():
    slots = find_free_slots([], start=MON, end=_at(0, 23), duration=timedelta(hours=1), tz=PARIS,
                            work_start=time(14), work_end=time(16), limit=10)
    assert slots == [(_at(0, 14), _at(0, 15)), (_at(0, 15), _at(0, 16))]


# ── connector ────────────────────────────────────────────────────────────────


GOOGLE_FREEBUSY_RESPONSE = {
    "kind": "calendar#freeBusy",
    "timeMin": "2026-10-05T06:00:00.000Z",
    "timeMax": "2026-10-12T06:00:00.000Z",
    "calendars": {
        "primary": {"busy": [{"start": "2026-10-05T08:00:00Z", "end": "2026-10-05T09:30:00Z"}]},
        "ana@example.com": {"busy": [{"start": "2026-10-05T10:00:00+02:00", "end": "2026-10-05T11:00:00+02:00"}]},
        "private@example.com": {"errors": [{"domain": "global", "reason": "notFound"}], "busy": []},
    },
}


@pytest.fixture
def connector(monkeypatch):
    c = GoogleCalendarConnector()
    c.set_access_token("test-token")
    calls = []

    async def fake_post(path, json_body=None, params=None):
        calls.append((path, json_body, params))
        if path == "/freeBusy":
            return GOOGLE_FREEBUSY_RESPONSE
        return {"id": "evt1", "summary": json_body.get("summary"), "start": json_body["start"],
                "end": json_body["end"], "htmlLink": ""}

    monkeypatch.setattr(c, "_api_post", fake_post)
    c._calls = calls
    return c


async def test_free_busy_parses_every_calendar_and_reports_unshared_ones(connector):
    cals = ["primary", "ana@example.com", "private@example.com"]
    out = await connector.free_busy(cals, MON, MON + timedelta(days=7))

    path, body, _ = connector._calls[0]
    assert path == "/freeBusy" and [i["id"] for i in body["items"]] == cals
    assert out["primary"]["busy"] == [(datetime(2026, 10, 5, 8, 0, tzinfo=UTC), datetime(2026, 10, 5, 9, 30, tzinfo=UTC))]
    assert out["ana@example.com"]["busy"][0][0] == _at(0, 10)
    assert out["private@example.com"] == {"busy": [], "error": "notFound"}


async def test_create_event_sends_no_invitations_unless_asked(connector):
    base = {"summary": "Sync", "start": "2026-10-05T10:00:00+02:00", "end": "2026-10-05T10:30:00+02:00",
            "attendees": ["ana@example.com"]}

    await connector.act(Action(action_type=ActionType.CREATE, params=dict(base)))
    await connector.act(Action(action_type=ActionType.CREATE, params={**base, "send_updates": "all"}))
    bad = await connector.act(Action(action_type=ActionType.CREATE, params={**base, "send_updates": "everyone"}))

    assert connector._calls[0][2] == {"sendUpdates": "none"}
    assert connector._calls[1][2] == {"sendUpdates": "all"}
    assert bad.success is False and len(connector._calls) == 2


# ── agent tool ───────────────────────────────────────────────────────────────


async def test_find_slots_tool_proposes_times_and_flags_unknown_people(connector, monkeypatch):
    from navig.agent.tools import calendar_tools

    async def fake_connector():
        return connector

    monkeypatch.setattr(calendar_tools, "_connector", fake_connector)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: MON)

    result = await calendar_tools.CalendarFindSlotsTool().run(
        {"duration_minutes": 60, "attendees": ["ana@example.com", "private@example.com"], "limit": 2}
    )

    assert result.success, result.error
    # Monday, local time: the operator is busy 10:00–11:30 (08:00–09:30 UTC) and Ana
    # 10:00–11:00, so the first free hours are 09:00 and 11:30.
    assert "Mon 05 Oct 09:00–10:00" in result.output
    assert "Mon 05 Oct 11:30–12:30" in result.output
    assert "UNKNOWN for: private@example.com" in result.output


async def test_find_slots_tool_reports_a_disconnected_calendar(monkeypatch):
    from navig.agent.tools import calendar_tools

    async def no_connector():
        raise RuntimeError("Google Calendar is not connected")

    monkeypatch.setattr(calendar_tools, "_connector", no_connector)
    result = await calendar_tools.CalendarFindSlotsTool().run({})
    assert result.success is False and "not connected" in result.error
