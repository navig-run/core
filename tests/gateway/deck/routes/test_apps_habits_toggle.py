"""Regression: POST /api/deck/apps/habits/toggle must only ever touch HABIT cron jobs.

The file-fallback branch (used when no live scheduler is in-process) blindly sliced
``name[len("habit:"):]`` for EVERY cron job and matched on it — so a plain cron job whose
name, stripped of the first 6 chars, equalled the posted id got its ``last_run`` silently
rewritten (disrupting its real schedule). Every sibling that reads habit jobs (health,
tasks_get, _life_habits_today, and the live-scheduler branch) filters by the ``habit:`` prefix
first; the file branch didn't. Both branches now skip non-habit jobs.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _sandboxed_tracker(tmp_path, monkeypatch):
    """A tick also writes habits.csv, which resolves to the ACTIVE SPACE — the
    operator's real tracker. Every test here gets a throwaway one instead."""
    path = tmp_path / "habits.csv"
    monkeypatch.setattr("navig.spaces.habit_tracker.tracker_path", lambda space=None: path)
    return path


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import apps as apps_mod

    app = web.Application()
    app.router.add_post("/toggle", apps_mod.handle_deck_apps_habits_toggle)
    return app


def _seed(monkeypatch, tmp_path):
    """Point the cron store at a temp file and force the file-fallback branch."""
    from navig.gateway.deck.routes import apps as apps_mod

    cron_file = tmp_path / "cron.json"
    monkeypatch.setattr(apps_mod, "_cron_jobs_path", lambda: cron_file)
    # No live scheduler → the handler (and _load_cron_jobs) take the file branch.
    monkeypatch.setattr("navig.scheduler.cron_service.get_live_service", lambda: None)

    apps_mod._save_cron_jobs(
        [
            {"id": "1", "name": "habit:workout", "last_run": None, "command": "", "schedule": "0 9 * * *"},
            # A PLAIN cron job whose name[6:] == "daily" — the exact blind-slice collision.
            {"id": "2", "name": "backupdaily", "last_run": None, "command": "", "schedule": "0 3 * * *"},
        ],
        2,
    )
    return cron_file


async def test_toggle_marks_the_habit_and_leaves_plain_jobs_alone(tmp_path, monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    cron_file = _seed(monkeypatch, tmp_path)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/toggle", json={"id": "workout"})
        assert r.status == 200

    saved = {j["id"]: j for j in json.loads(cron_file.read_text(encoding="utf-8"))["jobs"]}
    assert saved["1"]["last_run"]          # the habit was marked complete
    assert saved["2"]["last_run"] is None  # the plain cron job was NOT touched


async def test_toggle_never_matches_a_plain_job_via_blind_slice(tmp_path, monkeypatch):
    """The bug: posting id="daily" matched "backupdaily"[6:] and rewrote its last_run.
    Now a non-habit job can never be matched — 404, and its schedule stays intact."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    cron_file = _seed(monkeypatch, tmp_path)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/toggle", json={"id": "daily"})
        assert r.status == 404  # was 200 (and clobbered "backupdaily") under the old code

    saved = {j["id"]: j for j in json.loads(cron_file.read_text(encoding="utf-8"))["jobs"]}
    assert saved["2"]["last_run"] is None  # plain cron job untouched


async def test_last_run_is_stamped_on_the_LOCAL_calendar(tmp_path, monkeypatch):
    """A habit ticked in the deck must read back as done TODAY, in the user's calendar.

    Both readers of last_run prefix-match a LOCAL date (`date.today()`), and the
    scheduler stamps a naive local `datetime.now()`. Both write paths here stamped
    UTC, so between local midnight and UTC midnight the deck wrote yesterday: the
    user ticked the habit and it immediately read back as NOT done. On UTC+2 that is
    00:00-02:00 every day.

    The clock is skewed so UTC-now is a day behind the local date, which forces the
    disagreement instead of waiting for the two-hour window to come round.
    """
    pytest.importorskip("aiohttp")
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import apps as apps_mod

    cron_file = _seed(monkeypatch, tmp_path)

    class _SkewedClock(_dt):
        @classmethod
        def now(cls, tz=None):
            # local stays real; anything asking for UTC gets the previous day
            return _dt.now() - _td(days=1) if tz is not None else _dt.now()

    monkeypatch.setattr(apps_mod, "datetime", _SkewedClock)

    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/toggle", json={"id": "workout"})
        assert resp.status == 200

    jobs = json.loads(cron_file.read_text(encoding="utf-8"))
    rows = jobs["jobs"] if isinstance(jobs, dict) else jobs
    stamped = next(j["last_run"] for j in rows if j.get("name", "") == "habit:workout")
    assert stamped.startswith(_date.today().isoformat()), (
        f"last_run {stamped!r} is not on the local calendar the readers use "
        f"({_date.today().isoformat()}) — a habit ticked now reads back as not done"
    )


async def test_last_run_is_LOCAL_on_the_live_scheduler_path_too(tmp_path, monkeypatch):
    """The same calendar contract, on the branch that runs in production.

    The sibling above forces the FILE-FALLBACK branch (_seed stubs get_live_service to
    None), so it cannot see the live-scheduler write at all -- measured: reverting that
    branch alone left it green. With a real daemon in-process this is the path that
    executes, so it needs its own cover.
    """
    pytest.importorskip("aiohttp")
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import apps as apps_mod

    class _Job:
        id = "9"
        name = "habit:workout"

    captured: dict = {}

    class _Svc:
        jobs = {"9": _Job()}

        def update_job(self, job_id, **kw):
            captured.update(kw)

    monkeypatch.setattr("navig.scheduler.cron_service.get_live_service", lambda: _Svc())

    class _SkewedClock(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt.now() - _td(days=1) if tz is not None else _dt.now()

    monkeypatch.setattr(apps_mod, "datetime", _SkewedClock)

    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/toggle", json={"id": "workout"})
        assert resp.status == 200

    stamped = captured.get("last_run")
    assert stamped is not None, "the live branch never wrote last_run"
    assert stamped.isoformat().startswith(_date.today().isoformat()), (
        f"live-scheduler last_run {stamped!r} is not on the local calendar the readers use"
    )


# ── the tick is recorded in habits.csv (the history streaks count) ────────────


async def test_a_tick_is_written_to_the_habit_log(tmp_path, monkeypatch, _sandboxed_tracker):
    """The tick used to stamp only last_run (ONE day), so a streak kept by tapping
    the deck reset every day and `navig habit streak` never saw it."""
    from datetime import date as _date

    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import habit_tracker

    _seed(monkeypatch, tmp_path)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/toggle", json={"id": "workout"})
        body = await resp.json()
    assert body["data"]["logged"] is True
    rows = habit_tracker.read_tracker(_sandboxed_tracker)
    assert rows == [{"date": _date.today().isoformat(), "habit": "workout", "completed": "yes", "notes": "deck"}]


async def test_a_value_logged_from_the_cli_is_not_overwritten(tmp_path, monkeypatch, _sandboxed_tracker):
    from datetime import date as _date

    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import habit_tracker

    _seed(monkeypatch, tmp_path)
    today = _date.today().isoformat()
    habit_tracker.upsert(_sandboxed_tracker, today, "workout", "2", "two sets")
    async with TestClient(TestServer(_app())) as client:
        body = await (await client.post("/toggle", json={"id": "workout"})).json()
    assert body["data"]["logged"] is False
    assert habit_tracker.day_map(_sandboxed_tracker, today) == {"workout": "2"}


async def test_an_unwritable_log_does_not_fail_the_tick(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    _seed(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr("navig.spaces.habit_tracker.mark_done_today", boom)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/toggle", json={"id": "workout"})
        body = await resp.json()
    assert resp.status == 200
    assert body["data"]["logged"] is None


def test_tracker_writes_are_atomic_and_keep_line_endings(tmp_path):
    """write_tracker truncated then wrote — a crash mid-write lost the history."""
    from navig.spaces import habit_tracker

    path = tmp_path / "habits.csv"
    habit_tracker.upsert(path, "2026-10-07", "wake", "yes")
    habit_tracker.upsert(path, "2026-10-08", "wake", "yes")
    raw = path.read_bytes()
    assert b"\r\r\n" not in raw
    assert habit_tracker.streak_for(habit_tracker.read_tracker(path), "wake", __import__("datetime").date(2026, 10, 8)) == 2
    assert not list(tmp_path.glob(".habits.csv.tmp*")), "temp file left behind"
