"""Regression: GET /api/deck/apps/health must report measurement, not invent it.

Every body field this endpoint returned was fabricated, and it shipped with no
test at all — which is how it stayed that way:

    "steps": 0,                              # wearable integration not available
    "active_minutes": completed_today * 20,  # rough proxy
    "streak_days": completed_today,          # neither a streak nor days

The renderer then built its own sparkline out of the step figure, so the desktop
Health tab drew a shape nobody had measured, next to a real body record.

The rule these tests exist to hold: **an unmeasured value is None, never 0.**
A green zero is indistinguishable from a real reading and actively tells the
reader not to look — the same honesty failure `navig doctor` was fixed for.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration

HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"


@pytest.fixture
def record(tmp_path, monkeypatch):
    """An isolated metrics.csv that the endpoint will resolve to."""
    from navig.spaces import body_metrics as bm

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    monkeypatch.setattr(bm, "default_path", lambda: path)
    return path


@pytest.fixture
def no_habits(monkeypatch):
    """No cron jobs, so the habit half contributes nothing to these assertions."""
    from navig.gateway.deck.routes import apps as apps_mod

    async def _none():
        return [], None

    monkeypatch.setattr(apps_mod, "_async_load_cron_jobs", _none)


async def _get() -> dict:
    from navig.gateway.deck.routes import apps as apps_mod

    class _Req:
        pass

    resp = await apps_mod.handle_deck_apps_health(_Req())
    return json.loads(resp.body.decode())["data"]


# ── the honesty rule ──────────────────────────────────────────────────────────


async def test_unmeasured_fields_are_null_never_zero(record, no_habits):
    """steps/active_minutes/streak/HR have no source. None says so; 0 lies."""
    data = await _get()
    for field in ("steps", "active_minutes", "streak_days", "heart_rate_zone"):
        assert data[field] is None, f"{field} was {data[field]!r}, expected None"


async def test_an_empty_record_reports_no_weight_rather_than_zero_kg(record, no_habits):
    data = await _get()
    assert data["weight_kg"] is None
    assert data["average_7d"] is None
    assert data["points"] == []
    assert data["recorded_days"] == 0


# ── it reports the real record ────────────────────────────────────────────────


async def test_weight_comes_from_the_file(record, no_habits):
    from navig.spaces import body_metrics as bm

    bm.upsert(record, date.today().isoformat(), {"weight_kg": "88.2"})
    data = await _get()
    assert data["weight_kg"] == 88.2
    assert data["weight_date"] == date.today().isoformat()
    assert data["recorded_days"] == 1


async def test_the_series_is_the_recorded_one(record, no_habits):
    from navig.spaces import body_metrics as bm

    end = date.today()
    for i, kg in enumerate([90, 89, 88]):
        bm.upsert(record, (end - timedelta(days=2 - i)).isoformat(), {"weight_kg": str(kg)})
    data = await _get()
    assert [p["value"] for p in data["points"]] == [90.0, 89.0, 88.0]


async def test_todays_sleep_and_mood_are_surfaced(record, no_habits):
    from navig.spaces import body_metrics as bm

    bm.upsert(record, date.today().isoformat(), {"sleep_hours": "7.5", "mood_1_10": "8"})
    data = await _get()
    assert data["sleep_hours"] == 7.5
    assert data["mood_1_10"] == 8.0


async def test_today_follows_the_LOCAL_calendar_the_writer_used(record, no_habits, monkeypatch):
    """The reader's "today" must be the same calendar the writer stamped.

    `navig body` and `navig habit` stamp rows with `date.today()` -- the LOCAL date --
    and cron's `last_run` is a naive `datetime.now()`. This route used
    `datetime.now(timezone.utc)`, so on any machine not on UTC there is a window each
    day where the two disagree and today's row is simply not found: the user records
    sleep and the Health tab reports nothing. On UTC+2 that window is 00:00-02:00 local.

    The three existing tests DO catch it -- but only while running inside that window,
    which reads as flake. This one forces the disagreement instead of waiting for it:
    UTC-now is pinned a day behind the local date, so a UTC-based reader looks up the
    wrong day whatever time the suite runs.
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from navig.gateway.deck.routes import apps as apps_mod
    from navig.spaces import body_metrics as bm

    class _SkewedClock(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt.now(tz) - _td(days=1)

    monkeypatch.setattr(apps_mod, "datetime", _SkewedClock)

    bm.upsert(record, date.today().isoformat(), {"sleep_hours": "7.5"})
    assert (await _get())["sleep_hours"] == 7.5, (
        "the row was written with the LOCAL date the CLI uses; a reader on a different "
        "calendar reports today as empty"
    )


async def test_a_comma_decimal_in_the_file_still_parses(record, no_habits):
    """A row typed by hand in a fr/ru locale must not read as null."""
    from navig.spaces import body_metrics as bm

    bm.upsert(record, date.today().isoformat(), {"sleep_hours": "7,5"})
    assert (await _get())["sleep_hours"] == 7.5


async def test_trend_is_null_not_zero_without_a_prior_window(record, no_habits):
    """0.0 reads as "no change"; None says "not enough recorded"."""
    from navig.spaces import body_metrics as bm

    bm.upsert(record, date.today().isoformat(), {"weight_kg": "88"})
    assert (await _get())["trend_7d"] is None


# ── a read failure is not an empty state ──────────────────────────────────────


async def test_an_unreadable_record_sets_unavailable_rather_than_reporting_nothing(
    record, no_habits, monkeypatch
):
    """"Could not read" and "you have logged nothing" must not look the same."""
    from navig.spaces import body_metrics as bm

    def boom(*_a, **_k):
        raise bm.MetricsReadError("locked")

    monkeypatch.setattr(bm, "summarize", boom)
    data = await _get()
    assert data["unavailable"]
    assert data["weight_kg"] is None
    # and it still answers 200 rather than erroring the whole dashboard
    assert data["date"]


async def test_a_healthy_read_leaves_unavailable_null(record, no_habits):
    assert (await _get())["unavailable"] is None


# ── habit counts, which were always real, still work ──────────────────────────


async def test_habit_counts_are_still_reported(record, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    today = date.today().isoformat()

    async def _jobs():
        return [
            {"name": "habit:weigh", "last_run": f"{today}T08:10:00"},
            {"name": "habit:ship", "last_run": "2020-01-01T00:00:00"},
            {"name": "not-a-habit", "last_run": f"{today}T09:00:00"},
        ], None

    monkeypatch.setattr(apps_mod, "_async_load_cron_jobs", _jobs)
    data = await _get()
    assert data["habits_total"] == 2          # the non-habit job is excluded
    assert data["habits_done_today"] == 1


# ── which file it reads ───────────────────────────────────────────────────────


def test_default_path_prefers_the_pinned_file_over_the_active_space(tmp_path, monkeypatch):
    """The deck has no chat id, and the ACTIVE space is usually not the health one.

    Reading the active space would report "nothing recorded" over a full record.
    """
    from navig.spaces import body_metrics as bm

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    pinned = tmp_path / "health" / "metrics.csv"
    pinned.parent.mkdir(parents=True)
    pinned.write_text(HEADER, encoding="utf-8")
    bm.remember_target(159901607, pinned)

    assert bm.default_path() == pinned


def test_default_path_refuses_to_guess_when_chats_disagree(tmp_path, monkeypatch):
    from navig.spaces import body_metrics as bm

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    bm.remember_target(1, tmp_path / "a" / "metrics.csv")
    bm.remember_target(2, tmp_path / "b" / "metrics.csv")

    resolved = bm.default_path()
    assert resolved not in (tmp_path / "a" / "metrics.csv", tmp_path / "b" / "metrics.csv")


def test_default_path_falls_back_when_nothing_is_pinned(tmp_path, monkeypatch):
    from navig.spaces import body_metrics as bm

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    assert bm.default_path().name == "metrics.csv"
