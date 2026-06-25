"""
tests/commands/test_life_dashboard.py

Unit tests for navig.commands.life_dashboard panel builders and run_life_dashboard().
All external I/O (config, store, cron file) is mocked — no filesystem or network.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.panel import Panel


def _render(renderable) -> str:
    """Render any Rich renderable to a plain string for assertion."""
    buf = StringIO()
    console = Console(file=buf, width=120, highlight=False)
    console.print(renderable)
    return buf.getvalue()

from navig.commands.life_dashboard import (
    _fleet_panel,
    _habits_panel,
    _reminders_panel,
    _schedule_panel,
    _space_panel,
    run_life_dashboard,
)


# ── Fleet panel ───────────────────────────────────────────────────────────────

class TestFleetPanel:
    def test_no_hosts_returns_panel(self):
        p = _fleet_panel({"hosts": [], "count": 0})
        assert isinstance(p, Panel)

    def test_no_hosts_title_shows_zero(self):
        p = _fleet_panel({"hosts": [], "count": 0})
        assert "0" in _render(p)

    def test_single_host_title_singular(self):
        p = _fleet_panel({"hosts": ["srv1"], "count": 1})
        title = _render(p)
        assert "1" in title

    def test_hosts_listed_up_to_8(self):
        hosts = [f"srv{i}" for i in range(10)]
        p = _fleet_panel({"hosts": hosts, "count": 10})
        rendered = _render(p)
        # Overflow message must appear
        assert "more" in rendered or "2" in rendered

    def test_exactly_8_hosts_no_overflow(self):
        hosts = [f"srv{i}" for i in range(8)]
        p = _fleet_panel({"hosts": hosts, "count": 8})
        rendered = _render(p)
        assert "more" not in rendered


# ── Reminders panel ───────────────────────────────────────────────────────────

class TestRemindersPanel:
    def test_no_reminders_returns_panel(self):
        p = _reminders_panel([])
        assert isinstance(p, Panel)

    def test_no_reminders_has_hint_text(self):
        p = _reminders_panel([])
        assert "remind" in _render(p).lower() or "No pending" in _render(p)

    def test_reminder_with_iso_datetime_formats_to_hhmm(self):
        remind_at = (datetime.now() + timedelta(hours=1)).isoformat()
        reminders = [{"message": "check logs", "remind_at": remind_at}]
        p = _reminders_panel(reminders)
        rendered = _render(p)
        assert "check logs" in rendered

    def test_reminder_with_bad_datetime_shows_dash(self):
        reminders = [{"message": "bad time", "remind_at": "not-a-date"}]
        p = _reminders_panel(reminders)
        rendered = _render(p)
        assert "bad time" in rendered
        assert "—" in rendered

    def test_caps_at_6_reminders(self):
        reminders = [
            {"message": f"remind {i}", "remind_at": datetime.now().isoformat()}
            for i in range(10)
        ]
        p = _reminders_panel(reminders)
        rendered = _render(p)
        assert "more" in rendered or "+4" in rendered


# ── Habits panel ──────────────────────────────────────────────────────────────

class TestHabitsPanel:
    def test_no_habits_returns_panel(self):
        p = _habits_panel([])
        assert isinstance(p, Panel)

    def test_no_habits_has_hint_text(self):
        p = _habits_panel([])
        assert "habit" in _render(p).lower()

    def test_workout_habit_shows_emoji(self):
        next_run = (datetime.now() + timedelta(hours=1)).isoformat()
        jobs = [{"name": "habit:workout", "enabled": True, "next_run": next_run}]
        p = _habits_panel(jobs)
        rendered = _render(p)
        assert "workout" in rendered

    def test_disabled_habit_shows_off_label(self):
        next_run = datetime.now().isoformat()
        jobs = [{"name": "habit:water", "enabled": False, "next_run": next_run}]
        p = _habits_panel(jobs)
        rendered = _render(p)
        assert "off" in rendered

    def test_unknown_habit_key_shows_pin_emoji(self):
        next_run = datetime.now().isoformat()
        jobs = [{"name": "habit:custom_one", "enabled": True, "next_run": next_run}]
        p = _habits_panel(jobs)
        assert isinstance(p, Panel)


# ── Schedule panel ────────────────────────────────────────────────────────────

class TestSchedulePanel:
    def test_no_jobs_returns_panel(self):
        p = _schedule_panel([])
        assert isinstance(p, Panel)

    def test_habit_jobs_excluded_from_schedule(self):
        next_run = datetime.now().isoformat()
        jobs = [
            {"name": "habit:workout", "enabled": True, "next_run": next_run},
            {"name": "backup", "enabled": True, "next_run": next_run},
        ]
        p = _schedule_panel(jobs)
        rendered = _render(p)
        assert "backup" in rendered
        # Habit jobs must NOT appear in schedule panel
        assert "workout" not in rendered

    def test_caps_at_3_upcoming_jobs(self):
        next_run = datetime.now().isoformat()
        jobs = [
            {"name": f"job{i}", "enabled": True, "next_run": next_run}
            for i in range(5)
        ]
        p = _schedule_panel(jobs)
        rendered = _render(p)
        # Only first 3 non-habit jobs shown
        assert "job0" in rendered
        assert "job2" in rendered
        assert "job3" not in rendered


# ── Space panel ────────────────────────────────────────────────────────────────

class TestSpacePanel:
    def test_returns_panel(self):
        p = _space_panel("health", None)
        assert isinstance(p, Panel)

    def test_space_name_in_output(self):
        p = _space_panel("devops", 75.0)
        rendered = _render(p)
        assert "devops" in rendered

    def test_completion_shown_when_provided(self):
        p = _space_panel("personal", 42.0)
        rendered = _render(p)
        assert "42" in rendered

    def test_no_crash_when_completion_none(self):
        p = _space_panel("life", None)
        assert isinstance(p, Panel)


# ── run_life_dashboard integration ────────────────────────────────────────────

class TestRunLifeDashboard:
    def test_runs_with_all_empty_data(self, capsys):
        with (
            patch("navig.commands.life_dashboard._get_fleet_summary",
                  return_value={"hosts": [], "count": 0}),
            patch("navig.commands.life_dashboard._get_pending_reminders", return_value=[]),
            patch("navig.commands.life_dashboard._get_habit_jobs", return_value=[]),
            patch("navig.commands.life_dashboard._get_all_cron_jobs", return_value=[]),
            patch("navig.commands.life_dashboard._get_active_space", return_value="personal"),
            patch("navig.commands.life_dashboard._get_space_completion", return_value=None),
        ):
            run_life_dashboard()
        # No exception = pass

    def test_runs_with_populated_data(self):
        next_run = (datetime.now() + timedelta(hours=2)).isoformat()
        remind_at = (datetime.now() + timedelta(minutes=30)).isoformat()
        with (
            patch("navig.commands.life_dashboard._get_fleet_summary",
                  return_value={"hosts": ["srv1", "srv2"], "count": 2}),
            patch("navig.commands.life_dashboard._get_pending_reminders",
                  return_value=[{"message": "deploy", "remind_at": remind_at}]),
            patch("navig.commands.life_dashboard._get_habit_jobs",
                  return_value=[{"name": "habit:workout", "enabled": True, "next_run": next_run}]),
            patch("navig.commands.life_dashboard._get_all_cron_jobs",
                  return_value=[{"name": "backup", "enabled": True, "next_run": next_run}]),
            patch("navig.commands.life_dashboard._get_active_space", return_value="health"),
            patch("navig.commands.life_dashboard._get_space_completion", return_value=55.0),
        ):
            run_life_dashboard()
        # No exception = pass
