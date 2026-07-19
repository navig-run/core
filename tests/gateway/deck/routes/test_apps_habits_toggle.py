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
