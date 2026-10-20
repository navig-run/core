"""`/apps/tasks` habit streaks come from `habits.csv`, never a literal zero.

The old handler answered `"streak": 0` for every habit — "you broke your streak"
on every habit, every day — because the cron job only keeps `last_run`. The space's
tracker is the history, so the streak is counted from it, and a habit with no
history answers `null`: "not measured" and "you did zero" are different claims.

A server failure also used to answer 400; the request was fine, so it is a 500.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, query: dict | None = None) -> None:
        self.query = query or {}
        self.app: dict = {}


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _habit_job(key: str, last_run: str | None = None) -> dict:
    from navig.scheduler.habit_store import HABIT_NAME_PREFIX

    return {"id": f"j-{key}", "name": f"{HABIT_NAME_PREFIX}{key}", "command": "", "last_run": last_run}


@pytest.fixture
def tasks_env(monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    state: dict = {"jobs": [], "rows": []}

    async def _tasks():
        return []

    async def _jobs():
        return state["jobs"], 0

    monkeypatch.setattr(apps_mod, "_async_load_tasks", _tasks)
    monkeypatch.setattr(apps_mod, "_async_load_cron_jobs", _jobs)
    monkeypatch.setattr(apps_mod, "_read_habit_tracker_rows", lambda: state["rows"])
    return state


async def test_streak_is_counted_from_the_tracker(tasks_env):
    from navig.gateway.deck.routes import apps as apps_mod

    today = date.today()
    tasks_env["jobs"] = [_habit_job("wake")]
    tasks_env["rows"] = [
        {"date": (today - timedelta(days=n)).isoformat(), "habit": "wake", "completed": "yes"}
        for n in (1, 2, 3)
    ]
    habits = _body(await apps_mod.handle_deck_apps_tasks_get(_Req()))["data"]["habits"]
    assert habits[0]["streak"] == 3, "three consecutive days before today is a 3-day streak"


async def test_a_tick_today_extends_the_tracker_streak(tasks_env):
    """The deck tick stamps last_run but writes no tracker row — today still counts."""
    from navig.gateway.deck.routes import apps as apps_mod

    today = date.today()
    tasks_env["jobs"] = [_habit_job("wake", last_run=f"{today.isoformat()}T07:00:00")]
    tasks_env["rows"] = [
        {"date": (today - timedelta(days=1)).isoformat(), "habit": "wake", "completed": "yes"}
    ]
    habits = _body(await apps_mod.handle_deck_apps_tasks_get(_Req()))["data"]["habits"]
    assert habits[0]["completed_today"] is True
    assert habits[0]["streak"] == 2, "today counted from last_run + yesterday from the tracker"


async def test_no_history_is_null_not_zero(tasks_env):
    from navig.gateway.deck.routes import apps as apps_mod

    tasks_env["jobs"] = [_habit_job("read")]
    habits = _body(await apps_mod.handle_deck_apps_tasks_get(_Req()))["data"]["habits"]
    assert habits[0]["streak"] is None, "no history is 'not measured', not 'you did zero'"


async def test_unreadable_tracker_is_null_not_zero(tasks_env):
    from navig.gateway.deck.routes import apps as apps_mod

    tasks_env["jobs"] = [_habit_job("read")]
    tasks_env["rows"] = None
    habits = _body(await apps_mod.handle_deck_apps_tasks_get(_Req()))["data"]["habits"]
    assert habits[0]["streak"] is None


async def test_a_server_failure_is_500_not_400(monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    async def boom():
        raise OSError("tasks store locked")

    monkeypatch.setattr(apps_mod, "_async_load_tasks", boom)
    resp = await apps_mod.handle_deck_apps_tasks_get(_Req())
    assert resp.status == 500, "the request was well-formed; the server failed"
