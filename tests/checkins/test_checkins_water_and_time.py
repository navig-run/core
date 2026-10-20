"""Water totals, slot arithmetic across midnight and DST, and the definitions."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
CHAT = 4242


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PARIS)


# ── cadence: DST ──────────────────────────────────────────────────────────────


def test_a_slot_on_the_night_the_clocks_go_back_is_still_the_set_wall_time():
    from navig.checkins import cadence

    # 2026-10-25: CEST (+02:00) → CET (+01:00) at 03:00.
    before = cadence.slot_instant(date(2026, 10, 24), "0800", PARIS)
    after = cadence.slot_instant(date(2026, 10, 25), "0800", PARIS)
    assert before.strftime("%H:%M") == after.strftime("%H:%M") == "08:00"
    assert before.utcoffset() == timedelta(hours=2)
    assert after.utcoffset() == timedelta(hours=1)
    # 25 real hours between them — the day is longer, the dose is not moved.
    assert after.astimezone(UTC) - before.astimezone(UTC) == timedelta(hours=25)


def test_a_slot_inside_the_spring_gap_is_asked_just_after_it():
    from navig.checkins import cadence

    # 2026-03-29: 02:00 → 03:00 does not exist in Paris.
    inst = cadence.slot_instant(date(2026, 3, 29), "0230", PARIS)
    assert inst.strftime("%H:%M") == "03:30"


def test_missed_after_counts_real_minutes_across_the_change():
    from navig.checkins import cadence

    # A 01:30 dose on 2026-10-25, missed_after 120: due 01:30 CEST = 23:30 UTC.
    due_day = date(2026, 10, 25)
    due = cadence.slot_instant(due_day, "0130", PARIS)

    def real(minutes):
        return (due.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(PARIS)

    assert not cadence.is_late(real(119), due_day, "0130", PARIS, 120)
    assert cadence.is_late(real(120), due_day, "0130", PARIS, 120)
    # 120 real minutes later the wall clock reads 02:30 CET — naive wall-clock
    # arithmetic (Python's default for one tzinfo) would have said 03:30.
    assert real(120).strftime("%H:%M") == "02:30"


def test_occurrence_day_finds_yesterdays_late_dose_after_midnight():
    from navig.checkins import cadence

    now = at(2026, 10, 9, 1, 30)
    assert cadence.occurrence_day(now, "2330", PARIS) == date(2026, 10, 8)
    assert cadence.occurrence_day(now, "2330", PARIS, lag=timedelta(minutes=120)) == date(
        2026, 10, 8
    )
    assert cadence.occurrence_day(at(2026, 10, 9, 8, 0), "0800", PARIS) == date(2026, 10, 9)
    assert cadence.occurrence_day(at(2026, 10, 9, 7, 59), "0800", PARIS) == date(2026, 10, 8)


def test_slot_cron_wraps_past_midnight():
    from navig.checkins import cadence

    assert cadence.slot_cron("0800") == "0 8 * * *"
    assert cadence.slot_cron("2330", plus_minutes=120) == "30 1 * * *"


def test_water_slots():
    from navig.checkins import cadence

    assert cadence.water_slots(2, 9, 21) == ["0900", "1100", "1300", "1500", "1700", "1900"]
    assert cadence.water_slots(0, 9, 21) == []


# ── definitions ───────────────────────────────────────────────────────────────


def test_pills_validation_keeps_the_dose_text_verbatim():
    from navig.checkins import defs as d

    item = d.validate("pills", {"name": "Vitamin D3", "dose": "1 drop", "times": "8:00, 20h30"})
    assert item.id == "vitamin-d3"
    assert item.times == ("08:00", "20:30")
    assert item.slots() == ["0800", "2030"]
    assert item.dose == "1 drop"


@pytest.mark.parametrize(
    "data, message",
    [
        ({"name": "", "times": "08:00"}, "name"),
        ({"name": "X", "times": ""}, "time"),
        ({"name": "X", "times": "25:00"}, "time"),
        ({"name": "X", "times": "08:00", "missed_after": 5}, "missed_after"),
        ({"name": "X" * 61, "times": "08:00"}, "name"),
    ],
)
def test_pills_validation_rejects(data, message):
    from navig.checkins import defs as d

    with pytest.raises(ValueError, match=message):
        d.validate("pills", data)


def test_a_non_ascii_name_still_gets_a_valid_id():
    from navig.checkins import defs as d

    item = d.validate("pills", {"name": "Метформин", "times": "08:00"})
    assert d.ID_RE.match(item.id)


def test_water_validation():
    from navig.checkins import defs as d

    item = d.validate("water", {"target": 8, "every": "2h", "from_hour": 9, "to_hour": 21})
    assert (item.target, item.every_hours) == (8, 2)
    with pytest.raises(ValueError):
        d.validate("water", {"target": 0})
    with pytest.raises(ValueError):
        d.validate("water", {"every": "2h", "from_hour": 21, "to_hour": 9})


def test_weigh_parser_and_bounds_are_the_body_record_ones():
    from navig.checkins import defs as d
    from navig.spaces import body_metrics as bm

    weigh = d.TEMPLATES["weigh"]
    assert weigh.kind == d.KIND_NUMBER and weigh.storage == d.STORAGE_METRICS
    assert weigh.parser("87,4 кг") == 87.4
    with pytest.raises(bm.WeightParseError):
        weigh.parser("874")
    assert weigh.extension == "health", "the weigh-in keeps honouring the Health extension"


# ── water ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_water_taps_edit_the_card_in_place_and_total_today(ci_env, tg, add_water):
    from navig.checkins import cards, engine

    add_water(target=3)
    out = await engine.run_scheduled(
        "water", CHAT, "0900", None, send=tg, now=at(2026, 10, 8, 9, 0)
    )
    assert "sent" in out
    mid = tg.next_id
    for action in ("a1", "a2"):
        await engine.handle_callback(
            None,
            cards.cb("water", action, "2026-10-08"),
            CHAT,
            mid,
            send=tg,
            now=at(2026, 10, 8, 10, 0),
        )
    assert len(tg.sent()) == 1, "taps edit the card — they never post a new one"
    edits = tg.sent("editMessageText")
    assert {e["message_id"] for e in edits} == {mid}
    assert "3/3" in edits[-1]["text"]
    # the next reminder is skipped: target reached
    skipped = await engine.run_scheduled(
        "water", CHAT, "1100", None, send=tg, now=at(2026, 10, 8, 11, 0)
    )
    assert skipped.startswith("skipped") and "target" in skipped


@pytest.mark.asyncio
async def test_water_undo_never_goes_below_zero(ci_env, tg, add_water):
    from navig.checkins import cards, engine, records

    add_water(target=8)
    await engine.handle_callback(
        None, cards.cb("water", "u", "2026-10-08"), CHAT, 5, send=tg, now=at(2026, 10, 8, 10, 0)
    )
    assert records.count_total(records.read(ci_env["records"]), "water", "2026-10-08") == 0


@pytest.mark.asyncio
async def test_water_totals_split_at_local_midnight_and_across_dst(ci_env, tg, add_water):
    """A glass belongs to the local day it was tapped for — midnight and DST included."""
    from navig.checkins import cards, engine, records

    add_water(target=8)
    # 23:50 on the 24th (CEST) and 00:10 on the 25th — then 02:30 twice on the 25th,
    # once before and once after the clocks went back (two different instants).
    taps = [
        ("2026-10-24", at(2026, 10, 24, 23, 50)),
        ("2026-10-25", at(2026, 10, 25, 0, 10)),
        ("2026-10-25", datetime(2026, 10, 25, 0, 30, tzinfo=UTC).astimezone(PARIS)),  # 02:30 CEST
        ("2026-10-25", datetime(2026, 10, 25, 1, 30, tzinfo=UTC).astimezone(PARIS)),  # 02:30 CET
    ]
    for day, now in taps:
        assert now.date().isoformat() == day
        await engine.handle_callback(None, cards.cb("water", "a1", day), CHAT, 9, send=tg, now=now)
    rows = records.read(ci_env["records"])
    assert records.count_total(rows, "water", "2026-10-24") == 1
    assert records.count_total(rows, "water", "2026-10-25") == 3


def test_project_indexer_never_ingests_the_checkins_record():
    from navig.memory import project_indexer as pi

    assert not pi._is_indexable("health/checkins.jsonl")
    assert "checkins.jsonl" in pi.DEFAULT_EXCLUDES
