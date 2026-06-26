"""
tests/commands/test_habit_commands.py

Unit tests for navig.commands.habit:
- _build_schedule() schedule builder
- _DAY_MAP day-name mapping
- habit templates command via CliRunner
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.habit import _DAY_MAP, _build_schedule, habit_app
from navig.spaces.health import BUILTIN_HABITS, get_habit_template


@pytest.fixture()
def runner():
    return CliRunner()


class TestDayMap:
    def test_weekdays_maps_to_cron_1_5(self):
        assert _DAY_MAP["weekdays"] == "1-5"
        assert _DAY_MAP["weekday"] == "1-5"

    def test_weekends_maps_to_cron_0_6(self):
        assert _DAY_MAP["weekends"] == "0,6"
        assert _DAY_MAP["weekend"] == "0,6"

    def test_daily_maps_to_wildcard(self):
        assert _DAY_MAP["daily"] == "*"
        assert _DAY_MAP["everyday"] == "*"

    def test_individual_days_present(self):
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            assert day in _DAY_MAP


class TestBuildSchedule:
    @pytest.fixture()
    def workout(self):
        return get_habit_template("workout")

    @pytest.fixture()
    def standup(self):
        return get_habit_template("standup")

    def test_default_uses_template_schedule(self, workout):
        result = _build_schedule(workout, time_str=None, days="weekdays", every=None)
        assert result == workout.default_schedule

    def test_time_and_weekdays(self, workout):
        result = _build_schedule(workout, time_str="07:30", days="weekdays", every=None)
        assert result == "30 7 * * 1-5"

    def test_time_and_weekends(self, workout):
        result = _build_schedule(workout, time_str="09:00", days="weekends", every=None)
        assert result == "0 9 * * 0,6"

    def test_time_and_daily(self, workout):
        result = _build_schedule(workout, time_str="06:00", days="daily", every=None)
        assert result == "0 6 * * *"

    def test_time_hour_only_defaults_minute_to_zero(self, workout):
        result = _build_schedule(workout, time_str="08", days="weekdays", every=None)
        assert result == "0 8 * * 1-5"

    def test_every_90min(self, standup):
        result = _build_schedule(standup, time_str=None, days="daily", every="90min")
        assert result == "every 90 minutes"

    def test_every_2h(self, standup):
        result = _build_schedule(standup, time_str=None, days="daily", every="2h")
        assert result == "every 2 hours"

    def test_every_overrides_time_arg(self, standup):
        result = _build_schedule(standup, time_str="07:00", days="weekdays", every="30min")
        assert result.startswith("every")
        assert "30" in result

    def test_every_with_explicit_minutes_passthrough(self, standup):
        result = _build_schedule(standup, time_str=None, days="daily", every="45 minutes")
        assert result == "every 45 minutes"

    def test_unknown_days_falls_back_to_wildcard(self, workout):
        result = _build_schedule(workout, time_str="10:00", days="whenever", every=None)
        assert result == "0 10 * * *"


class TestHabitTemplatesCommand:
    def test_templates_lists_all_four_keys(self, runner):
        result = runner.invoke(habit_app, ["templates"])
        assert result.exit_code == 0
        for key in BUILTIN_HABITS:
            assert key in result.output

    def test_templates_output_contains_emojis(self, runner):
        result = runner.invoke(habit_app, ["templates"])
        assert result.exit_code == 0
        # At least one emoji must appear
        emojis = [t.emoji for t in BUILTIN_HABITS.values()]
        assert any(e in result.output for e in emojis)

    def test_templates_shows_schedule_hints(self, runner):
        result = runner.invoke(habit_app, ["templates"])
        assert result.exit_code == 0
        assert "workout" in result.output.lower() or "Workout" in result.output
