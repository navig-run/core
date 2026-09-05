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

#: The original four. Kept as its own set so a regression that drops one of them
#: is still named as such, rather than hiding inside the wider roster below.
ORIGINAL_KEYS = {"workout", "standup", "water", "sleep"}

#: The life-rails set: a day's anchor, its one shipping block, the way out of the
#: house, plus the scaffolding that holds them.
LIFE_RAIL_KEYS = {"wake", "ship", "out", "nightclose", "checkin", "people", "review"}

#: The same floor, at hours that do not depend on a workday. Without these a weekend
#: has nothing to start it: the tracker showed every complete day falling Mon–Fri.
WEEKEND_KEYS = {"out_weekend", "ship_weekend"}

#: The three without which a day does not count at all.
NON_NEGOTIABLE_KEYS = {"wake", "out", "ship"}

EXPECTED_KEYS = ORIGINAL_KEYS | LIFE_RAIL_KEYS | WEEKEND_KEYS


class TestBuiltinHabits:
    def test_all_builtin_habits_exist(self):
        assert set(BUILTIN_HABITS.keys()) == EXPECTED_KEYS

    def test_original_four_are_still_present(self):
        """Adding rails must never quietly drop the habits that shipped first."""
        assert ORIGINAL_KEYS <= set(BUILTIN_HABITS.keys())

    def test_non_negotiables_are_present(self):
        assert NON_NEGOTIABLE_KEYS <= set(BUILTIN_HABITS.keys())

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

    def test_weekend_variants_run_on_weekends_only(self):
        """A weekend habit that also fires Mon–Fri would double every weekday reminder."""
        for key in WEEKEND_KEYS:
            assert BUILTIN_HABITS[key].default_schedule.endswith("0,6"), key

    def test_weekend_variants_fire_before_the_day_dissolves(self):
        """Earlier than their weekday twins — that is the entire point of them."""
        def hour(key: str) -> int:
            return int(BUILTIN_HABITS[key].default_schedule.split()[1])

        assert hour("out_weekend") < hour("out")

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

    # `sorted`, not `list`: EXPECTED_KEYS is a set, whose iteration order varies per
    # process. Under pytest-xdist each worker collected these params in a different
    # order and xdist aborted the run ("Different tests were collected"). The set is
    # still correct for the equality assertions above — only collection must be stable.
    @pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
    def test_all_builtin_keys_resolvable(self, key):
        tmpl = get_habit_template(key)
        assert tmpl is not None
        assert tmpl.key == key


class TestListHabitTemplates:
    def test_returns_every_builtin(self):
        templates = list_habit_templates()
        assert len(templates) == len(EXPECTED_KEYS)

    def test_returns_habit_template_instances(self):
        for tmpl in list_habit_templates():
            assert isinstance(tmpl, HabitTemplate)

    def test_keys_match_builtin_habits(self):
        keys = {t.key for t in list_habit_templates()}
        assert keys == EXPECTED_KEYS
