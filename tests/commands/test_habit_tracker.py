"""
Tests for navig.commands.habit — the habits.csv tracker (log / today / streak).

Regression guards for the two ways a tracker silently lies:
  - a second log on the same day appending instead of updating, so one day
    counts twice and every streak above it is wrong;
  - a missing day not breaking a streak, so a skipped week still reads green.

All tests are hermetic: the tracker path is redirected to tmp_path.
"""

from __future__ import annotations

import csv
from datetime import date

import pytest
from typer.testing import CliRunner

from navig.commands.habit import (
    NON_NEGOTIABLES,
    TRACKER_FIELDS,
    _is_done,
    _read_tracker,
    _streak_for,
    _write_tracker,
    habit_app,
)


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """Point the tracker at an isolated habits.csv."""
    path = tmp_path / "habits.csv"
    monkeypatch.setattr("navig.commands.habit._tracker_path", lambda *a, **k: path)
    return path


def _rows(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ===========================================================================
# _is_done
# ===========================================================================


class TestIsDone:
    @pytest.mark.parametrize("value", ["yes", "y", "2", "08:04", "done", "true"])
    def test_truthy_values_count(self, value):
        assert _is_done(value) is True

    @pytest.mark.parametrize("value", ["", "no", "No", "0", "false", "pending", "skipped"])
    def test_not_done_values(self, value):
        assert _is_done(value) is False

    def test_pending_seeded_row_is_not_done(self):
        """A day-1 seeded 'pending' row must never count as a completed day."""
        assert _is_done("pending") is False


# ===========================================================================
# _streak_for
# ===========================================================================


class TestStreak:
    def test_counts_consecutive_days(self):
        rows = [
            {"date": "2026-08-08", "habit": "wake", "completed": "yes", "notes": ""},
            {"date": "2026-08-09", "habit": "wake", "completed": "yes", "notes": ""},
            {"date": "2026-08-10", "habit": "wake", "completed": "yes", "notes": ""},
        ]
        assert _streak_for(rows, "wake", date(2026, 8, 10)) == 3

    def test_gap_breaks_the_streak(self):
        rows = [
            {"date": "2026-08-05", "habit": "wake", "completed": "yes", "notes": ""},
            # 08-06 and 08-07 missing
            {"date": "2026-08-08", "habit": "wake", "completed": "yes", "notes": ""},
            {"date": "2026-08-09", "habit": "wake", "completed": "yes", "notes": ""},
            {"date": "2026-08-10", "habit": "wake", "completed": "yes", "notes": ""},
        ]
        assert _streak_for(rows, "wake", date(2026, 8, 10)) == 3

    def test_today_not_logged_yet_keeps_yesterdays_streak(self):
        """An unfinished day must not zero a live streak before it is over."""
        rows = [
            {"date": "2026-08-08", "habit": "out", "completed": "yes", "notes": ""},
            {"date": "2026-08-09", "habit": "out", "completed": "yes", "notes": ""},
        ]
        assert _streak_for(rows, "out", date(2026, 8, 10)) == 2

    def test_not_done_row_does_not_extend(self):
        rows = [
            {"date": "2026-08-09", "habit": "ship", "completed": "yes", "notes": ""},
            {"date": "2026-08-10", "habit": "ship", "completed": "no", "notes": ""},
        ]
        assert _streak_for(rows, "ship", date(2026, 8, 10)) == 1

    def test_no_data_is_zero(self):
        assert _streak_for([], "wake", date(2026, 8, 10)) == 0


# ===========================================================================
# read / write round-trip
# ===========================================================================


class TestRoundTrip:
    def test_round_trip_is_stable(self, tmp_path):
        path = tmp_path / "habits.csv"
        rows = [
            {"date": "2026-08-10", "habit": "wake", "completed": "yes", "notes": "встал 08:04"},
            {"date": "2026-08-10", "habit": "deep", "completed": "2", "notes": ""},
        ]
        _write_tracker(path, rows)
        assert _read_tracker(path) == rows

        _write_tracker(path, _read_tracker(path))
        assert _read_tracker(path) == rows

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert _read_tracker(tmp_path / "nope.csv") == []

    def test_preserves_existing_column_shape(self, tmp_path):
        path = tmp_path / "habits.csv"
        path.write_text("date,habit,completed,notes\n", encoding="utf-8")
        _write_tracker(
            path, [{"date": "2026-08-10", "habit": "out", "completed": "yes", "notes": ""}]
        )
        with path.open(encoding="utf-8", newline="") as fh:
            assert next(csv.reader(fh)) == TRACKER_FIELDS


# ===========================================================================
# navig habit log
# ===========================================================================


class TestHabitLog:
    def test_creates_file_when_missing(self, tracker):
        result = CliRunner().invoke(habit_app, ["log", "wake"])
        assert result.exit_code == 0
        assert tracker.exists()
        assert _rows(tracker) == [
            {"date": date.today().isoformat(), "habit": "wake", "completed": "yes", "notes": ""}
        ]

    def test_second_log_same_day_updates_not_appends(self, tracker):
        runner = CliRunner()
        runner.invoke(habit_app, ["log", "wake", "--note", "first"])
        runner.invoke(habit_app, ["log", "wake", "--note", "second"])

        rows = _rows(tracker)
        assert len(rows) == 1, "same label on the same day must upsert, not duplicate"
        assert rows[0]["notes"] == "second"

    def test_value_is_stored(self, tracker):
        CliRunner().invoke(habit_app, ["log", "deep", "--value", "2"])
        assert _rows(tracker)[0]["completed"] == "2"

    def test_undo_marks_not_done(self, tracker):
        runner = CliRunner()
        runner.invoke(habit_app, ["log", "ship"])
        runner.invoke(habit_app, ["log", "ship", "--undo"])

        rows = _rows(tracker)
        assert len(rows) == 1
        assert rows[0]["completed"] == "no"
        assert _is_done(rows[0]["completed"]) is False

    def test_explicit_date_backfills(self, tracker):
        CliRunner().invoke(habit_app, ["log", "wake", "--date", "2026-08-01"])
        assert _rows(tracker)[0]["date"] == "2026-08-01"

    def test_bad_date_is_rejected(self, tracker):
        result = CliRunner().invoke(habit_app, ["log", "wake", "--date", "01-08-2026"])
        assert result.exit_code == 1
        assert not tracker.exists()

    def test_rows_stay_sorted(self, tracker):
        runner = CliRunner()
        runner.invoke(habit_app, ["log", "wake", "--date", "2026-08-09"])
        runner.invoke(habit_app, ["log", "out", "--date", "2026-08-08"])
        runner.invoke(habit_app, ["log", "ship", "--date", "2026-08-09"])

        rows = _rows(tracker)
        assert [(r["date"], r["habit"]) for r in rows] == sorted(
            (r["date"], r["habit"]) for r in rows
        )


# ===========================================================================
# navig habit today / streak
# ===========================================================================


class TestHabitToday:
    def test_names_which_of_the_three_are_open(self, tracker):
        """In words, not labels: "out" is a column name, "Walk" is a thing you do."""
        runner = CliRunner()
        runner.invoke(habit_app, ["log", "wake"])
        result = runner.invoke(habit_app, ["today"])

        assert result.exit_code == 0
        assert "Still open" in result.output
        assert "Walk" in result.output
        assert "Ship" in result.output

    def test_all_three_done_reports_the_day_counts(self, tracker):
        runner = CliRunner()
        for label in NON_NEGOTIABLES:
            runner.invoke(habit_app, ["log", label])
        result = runner.invoke(habit_app, ["today"])

        assert result.exit_code == 0
        assert "all three done" in result.output.lower()

    def test_empty_day_does_not_crash(self, tracker):
        result = CliRunner().invoke(habit_app, ["today"])
        assert result.exit_code == 0


class TestTrackerPathResolution:
    """Which habits.csv an invocation means.

    Regression: `get_active_working_dir()` prefers the `navig space switch` pin
    over the directory you are standing in, so logging from inside a space wrote
    into a *different* space's tracker and the row looked lost.
    """

    @staticmethod
    def _pin_active_space(monkeypatch, path):
        path.mkdir(exist_ok=True)
        monkeypatch.setattr(
            "navig.spaces.active.get_active_working_dir", lambda *a, **k: path
        )
        return path

    @staticmethod
    def _space_with_tracker(path):
        path.mkdir(exist_ok=True)
        (path / "habits.csv").write_text("date,habit,completed,notes\n", encoding="utf-8")
        return path

    def test_explicit_space_dir_wins(self, tmp_path):
        from navig.commands.habit import _tracker_path

        target = tmp_path / "other-space"
        target.mkdir()
        assert _tracker_path(str(target)) == target / "habits.csv"

    def test_explicit_csv_path_is_used_as_is(self, tmp_path):
        from navig.commands.habit import _tracker_path

        explicit = tmp_path / "custom-tracker.csv"
        assert _tracker_path(str(explicit)) == explicit

    def test_invocation_dir_tracker_beats_the_active_space_pin(self, tmp_path, monkeypatch):
        """The regression: main.py chdir's to the pin, so cwd is no longer the shell's dir."""
        from navig.commands import habit

        self._pin_active_space(monkeypatch, tmp_path / "pinned-space")
        here = self._space_with_tracker(tmp_path / "the-space-i-am-in")

        monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(here))
        monkeypatch.chdir(tmp_path / "pinned-space")  # what main.py already did

        assert habit._tracker_path() == here / "habits.csv"

    def test_falls_back_to_active_space_when_invocation_dir_has_no_tracker(
        self, tmp_path, monkeypatch
    ):
        from navig.commands import habit

        pinned = self._pin_active_space(monkeypatch, tmp_path / "pinned-space")
        empty = tmp_path / "no-tracker-here"
        empty.mkdir()

        monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(empty))
        assert habit._tracker_path() == pinned / "habits.csv"

    def test_uses_cwd_when_invocation_var_is_unset(self, tmp_path, monkeypatch):
        """In-process callers (tests, the gateway) never go through main.py."""
        from navig.commands import habit

        self._pin_active_space(monkeypatch, tmp_path / "pinned-space")
        here = self._space_with_tracker(tmp_path / "plain-cwd")

        monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
        monkeypatch.chdir(here)

        assert habit._tracker_path() == here / "habits.csv"

    def test_explicit_space_beats_the_invocation_dir(self, tmp_path, monkeypatch):
        from navig.commands import habit

        here = self._space_with_tracker(tmp_path / "standing-here")
        target = tmp_path / "explicit-space"
        target.mkdir()

        monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(here))
        assert habit._tracker_path(str(target)) == target / "habits.csv"


class TestHabitStreak:
    def test_empty_tracker_does_not_crash(self, tracker):
        result = CliRunner().invoke(habit_app, ["streak"])
        assert result.exit_code == 0

    def test_lists_the_logged_habit(self, tracker):
        runner = CliRunner()
        runner.invoke(habit_app, ["log", "wake"])
        result = runner.invoke(habit_app, ["streak"])

        assert result.exit_code == 0
        assert "wake" in result.output
