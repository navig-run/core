"""tests/spaces/test_spaces_health.py — Unit tests for navig.spaces.health."""

from __future__ import annotations

import dataclasses

import pytest

from navig.spaces.health import (
    BUILTIN_HABITS,
    HabitTemplate,
    get_habit_template,
    list_habit_templates,
)

EXPECTED_KEYS = {"workout", "standup", "water", "sleep"}


class TestBuiltinHabits:
    def test_all_four_builtin_habits_exist(self):
        assert set(BUILTIN_HABITS.keys()) == EXPECTED_KEYS

    def test_habits_are_frozen_dataclasses(self):
        tmpl = BUILTIN_HABITS["workout"]
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            tmpl.key = "mutated"  # type: ignore[misc]

    def test_template_fields_nonempty(self):
        for key, tmpl in BUILTIN_HABITS.items():
            assert tmpl.key == key
            assert tmpl.display_name
            assert tmpl.description
            assert tmpl.default_schedule
            assert tmpl.reminder_message
            assert tmpl.emoji

    def test_workout_schedule_is_weekday_cron(self):
        assert BUILTIN_HABITS["workout"].default_schedule == "0 7 * * 1-5"

    def test_sleep_schedule_is_evening_cron(self):
        assert BUILTIN_HABITS["sleep"].default_schedule == "0 22 * * *"

    def test_standup_schedule_is_interval(self):
        assert "90" in BUILTIN_HABITS["standup"].default_schedule

    def test_water_schedule_is_interval(self):
        assert "2" in BUILTIN_HABITS["water"].default_schedule

    def test_emojis_are_distinct(self):
        emojis = [t.emoji for t in BUILTIN_HABITS.values()]
        assert len(emojis) == len(set(emojis))


class TestGetHabitTemplate:
    def test_returns_template_for_known_key(self):
        tmpl = get_habit_template("workout")
        assert isinstance(tmpl, HabitTemplate)
        assert tmpl.key == "workout"

    def test_returns_none_for_unknown_key(self):
        assert get_habit_template("does_not_exist") is None

    def test_returns_none_for_empty_string(self):
        assert get_habit_template("") is None

    @pytest.mark.parametrize("key", list(EXPECTED_KEYS))
    def test_all_builtin_keys_resolvable(self, key):
        tmpl = get_habit_template(key)
        assert tmpl is not None
        assert tmpl.key == key


class TestListHabitTemplates:
    def test_returns_all_four(self):
        templates = list_habit_templates()
        assert len(templates) == 4

    def test_returns_habit_template_instances(self):
        for tmpl in list_habit_templates():
            assert isinstance(tmpl, HabitTemplate)

    def test_keys_match_builtin_habits(self):
        keys = {t.key for t in list_habit_templates()}
        assert keys == EXPECTED_KEYS
