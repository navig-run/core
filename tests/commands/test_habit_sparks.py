"""Tests for navig.spaces.sparks — the lines sent between reminders.

Two properties carry the feature: the line is chosen from the day rather than at
random (a generic quote at 15:30 is wallpaper), and it never repeats inside a
fortnight (a repeated line proves nobody is home).
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from navig.spaces import habit_tracker, sparks

MONDAY = date(2026, 8, 17)
SATURDAY = date(2026, 8, 22)

POOL = {
    "recover": ("R1", "R2"),
    "walk": ("W1", "W2"),
    "ship": ("S1", "S2"),
    "weekend": ("E1", "E2"),
    "body": ("B1",),
    "focus": ("F1",),
    "affirm": ("A1",),
}

ALL_DONE = {"wake", "out", "ship"}


class TestCategoryChoice:
    """`category_for` is pure — no clock, no files, no chat."""

    def test_an_empty_yesterday_outranks_everything(self):
        assert sparks.category_for(SATURDAY, ALL_DONE, set(), 11) == "recover"

    def test_a_weekend_gets_its_own_bucket(self):
        assert sparks.category_for(SATURDAY, set(), {"wake"}, 11) == "weekend"

    def test_an_unmarked_walk_in_the_afternoon_asks_you_out(self):
        assert sparks.category_for(MONDAY, {"wake", "ship"}, {"wake"}, 16) == "walk"

    def test_the_walk_is_not_nagged_about_in_the_morning(self):
        """Nagging at 11:00 about a walk due at 19:00 trains you to ignore the nag."""
        assert sparks.category_for(MONDAY, {"wake", "ship"}, {"wake"}, 11) != "walk"

    def test_an_unstarted_ship_block_is_named_while_there_is_still_time(self):
        assert sparks.category_for(MONDAY, {"wake"}, {"wake"}, 11) == "ship"

    def test_after_the_workday_the_ship_block_is_not_raised(self):
        """A block that can no longer happen is not a nudge, it is an accusation."""
        assert sparks.category_for(MONDAY, {"wake", "out"}, {"wake"}, 19) != "ship"

    def test_a_day_going_fine_gets_no_forced_category(self):
        assert sparks.category_for(MONDAY, ALL_DONE, ALL_DONE, 12) == ""


class TestPick:
    def test_a_day_going_fine_draws_from_the_ambient_pool(self):
        category, line = sparks.pick(
            POOL, day=MONDAY, today_done=ALL_DONE, yesterday_done=ALL_DONE, hour=12
        )
        assert category in sparks.AMBIENT
        assert line

    def test_the_situation_wins_when_there_is_one(self):
        category, line = sparks.pick(
            POOL, day=MONDAY, today_done={"wake"}, yesterday_done=set(), hour=11
        )
        assert category == "recover"
        assert line in POOL["recover"]

    def test_a_recently_sent_line_is_not_chosen_again(self):
        used = [sparks.key_of("R1")]
        _, line = sparks.pick(
            POOL, day=MONDAY, today_done=set(), yesterday_done=set(), hour=11, recent=used
        )
        assert line == "R2"

    def test_an_exhausted_category_falls_through_rather_than_going_silent(self):
        """Sending nothing looks exactly like the daemon being down."""
        used = [sparks.key_of(x) for x in ("R1", "R2")]
        category, line = sparks.pick(
            POOL, day=MONDAY, today_done=set(), yesterday_done=set(), hour=11, recent=used
        )
        assert line
        assert line not in POOL["recover"]

    def test_a_fully_exhausted_pool_repeats_rather_than_saying_nothing(self):
        used = [sparks.key_of(ln) for lines in POOL.values() for ln in lines]
        _, line = sparks.pick(
            POOL, day=MONDAY, today_done=ALL_DONE, yesterday_done=ALL_DONE, hour=12, recent=used
        )
        assert line

    def test_an_empty_pool_is_not_an_error(self):
        assert sparks.pick({}, day=MONDAY, today_done=set(), yesterday_done=set(), hour=9) == ("", "")

    def test_selection_is_deterministic_for_a_seeded_rng(self):
        args = dict(day=MONDAY, today_done=ALL_DONE, yesterday_done=ALL_DONE, hour=12)
        a = sparks.pick(POOL, rng=random.Random(7), **args)
        b = sparks.pick(POOL, rng=random.Random(7), **args)
        assert a == b


class TestParsing:
    def test_headings_group_the_lines_under_them(self):
        parsed = sparks.parse("# body\nOne.\nTwo.\n\n# focus\nThree.\n")
        assert parsed == {"body": ("One.", "Two."), "focus": ("Three.",)}

    def test_comments_and_blank_lines_are_ignored(self):
        assert sparks.parse("\n\n# body\n\nOnly this.\n\n") == {"body": ("Only this.",)}

    def test_an_unknown_heading_is_kept_not_discarded(self):
        """An unrecognised category is a line the owner wrote, not an error."""
        assert "mine" in sparks.parse("# mine\nA line of my own.\n")

    def test_lines_before_any_heading_are_not_lost(self):
        assert sparks.parse("A stray line.\n")["affirm"] == ("A stray line.",)

    def test_a_file_with_no_lines_falls_back_to_the_builtins(self, tmp_path, monkeypatch):
        empty = tmp_path / "sparks.txt"
        empty.write_text("# body\n", encoding="utf-8")
        monkeypatch.setattr(sparks, "sparks_path", lambda space=None: empty)
        assert sparks.load() == sparks.BUILTIN

    def test_a_missing_file_falls_back_to_the_builtins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sparks, "sparks_path", lambda space=None: tmp_path / "nope.txt")
        assert sparks.load() == sparks.BUILTIN


class TestBuiltinPool:
    def test_every_context_category_has_lines(self):
        for category in sparks.CONTEXT_ORDER + sparks.AMBIENT:
            assert sparks.BUILTIN.get(category), category

    def test_no_line_is_duplicated_across_the_pool(self):
        every = [ln for lines in sparks.BUILTIN.values() for ln in lines]
        assert len(every) == len(set(every))


class TestSparkMemory:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "t.json")

    def test_a_sent_line_is_remembered(self):
        habit_tracker.remember_spark(-1, "abc")
        assert habit_tracker.recent_sparks(-1) == ["abc"]

    def test_memory_is_capped_so_a_small_pool_never_dries_up(self):
        for i in range(habit_tracker.SPARK_MEMORY + 10):
            habit_tracker.remember_spark(-1, f"k{i}")
        assert len(habit_tracker.recent_sparks(-1)) == habit_tracker.SPARK_MEMORY

    def test_the_oldest_line_is_the_one_forgotten(self):
        for i in range(habit_tracker.SPARK_MEMORY + 1):
            habit_tracker.remember_spark(-1, f"k{i}")
        assert "k0" not in habit_tracker.recent_sparks(-1)
        assert f"k{habit_tracker.SPARK_MEMORY}" in habit_tracker.recent_sparks(-1)

    def test_re_sending_a_line_moves_it_to_newest_without_duplicating(self):
        habit_tracker.remember_spark(-1, "a")
        habit_tracker.remember_spark(-1, "b")
        habit_tracker.remember_spark(-1, "a")
        assert habit_tracker.recent_sparks(-1) == ["b", "a"]

    def test_an_unknown_chat_has_no_memory(self):
        assert habit_tracker.recent_sparks(-999) == []

    def test_spark_memory_does_not_disturb_the_tracker_pin(self, tmp_path):
        target = tmp_path / "habits.csv"
        habit_tracker.remember_target(-5, target, message_id=3, day="2026-08-17")
        habit_tracker.remember_spark(-5, "x")

        assert habit_tracker.resolve_target(-5) == target
        assert habit_tracker.last_card(-5) == (3, "2026-08-17")
