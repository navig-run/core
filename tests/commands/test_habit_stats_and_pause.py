"""Tests for `navig habit stats` / `pause` / `resume`.

Stats exists because a count ("you missed some days") is not actionable and a
day-of-week breakdown is. Pause exists because a tracker you cannot switch off
gets muted at the OS instead — and a muted reminder looks exactly like a broken
one, which is how a silent delivery failure hides for a week.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from navig.commands.habit import habit_app
from navig.scheduler import habit_store
from navig.spaces import habit_tracker
from navig.spaces.habit_tracker import NON_NEGOTIABLES

MONDAY = date(2026, 8, 17)  # the cycle in these tests starts on a Monday


def _rows(*specs: tuple[str, list[str]]) -> list[dict]:
    return [
        {"date": day, "habit": habit, "completed": "yes", "notes": ""}
        for day, habits in specs
        for habit in habits
    ]


# ===========================================================================
# summarize
# ===========================================================================

class TestSummarize:
    def test_empty_tracker_is_not_an_error(self):
        s = habit_tracker.summarize([], MONDAY)
        assert s["days"] == 0
        assert s["complete"] == 0

    def test_counts_complete_days_only_when_all_three_are_done(self):
        rows = _rows(
            ("2026-08-17", list(NON_NEGOTIABLES)),
            ("2026-08-18", ["wake", "ship"]),
        )
        s = habit_tracker.summarize(rows, date(2026, 8, 18))
        assert s["complete"] == 1

    def test_span_counts_blank_days_too(self):
        """A day with no row is a day that happened — the span is the calendar."""
        rows = _rows(("2026-08-17", ["wake"]))
        s = habit_tracker.summarize(rows, date(2026, 8, 20))
        assert s["days"] == 4
        assert s["habits"]["wake"]["missed"] == 3

    def test_best_streak_survives_a_later_break(self):
        rows = _rows(
            ("2026-08-17", ["wake"]),
            ("2026-08-18", ["wake"]),
            ("2026-08-19", ["wake"]),
            ("2026-08-25", ["wake"]),
        )
        s = habit_tracker.summarize(rows, date(2026, 8, 25))
        assert s["habits"]["wake"]["best"] == 3
        assert s["habits"]["wake"]["streak"] == 1

    def test_weekday_breakdown_locates_the_lost_days(self):
        """THE INSIGHT: 'a bad week' is usually one weekday that never works."""
        rows = _rows(
            ("2026-08-17", list(NON_NEGOTIABLES)),  # Monday, complete
            ("2026-08-22", ["wake"]),               # Saturday, not complete
        )
        s = habit_tracker.summarize(rows, date(2026, 8, 23))
        assert s["by_weekday"][0]["complete"] == 1   # Monday
        assert s["by_weekday"][5]["complete"] == 0   # Saturday
        assert s["by_weekday"][6]["days"] == 1       # Sunday counted even with no rows

    def test_a_malformed_date_does_not_crash_the_summary(self):
        rows = [{"date": "not-a-date", "habit": "wake", "completed": "yes", "notes": ""}]
        rows += _rows(("2026-08-17", ["wake"]))
        assert habit_tracker.summarize(rows, MONDAY)["days"] == 1


class TestDayRows:
    def test_blank_days_are_included(self):
        out = habit_tracker.day_rows(_rows(("2026-08-17", ["wake"])), MONDAY, date(2026, 8, 19))
        assert [d for d, _ in out] == [MONDAY, date(2026, 8, 18), date(2026, 8, 19)]
        assert out[0][1]["wake"] is True
        assert out[1][1]["wake"] is False

    def test_every_row_carries_all_three(self):
        for _, marks in habit_tracker.day_rows([], MONDAY, MONDAY):
            assert set(marks) == set(NON_NEGOTIABLES)


# ===========================================================================
# navig habit stats
# ===========================================================================

@pytest.fixture
def tracker(tmp_path, monkeypatch):
    path = tmp_path / "habits.csv"
    monkeypatch.setattr("navig.commands.habit._tracker_path", lambda space=None: path)
    return path


class TestStatsCommand:
    def test_empty_tracker_explains_what_to_do(self, tracker):
        result = CliRunner().invoke(habit_app, ["stats"])
        assert result.exit_code == 0
        assert "navig habit log" in result.output

    def test_reports_in_words_not_labels(self, tracker):
        habit_tracker.upsert(tracker, date.today().isoformat(), "out", "yes")
        result = CliRunner().invoke(habit_app, ["stats"])

        assert result.exit_code == 0
        assert "Walk" in result.output

    def test_names_a_blank_day(self, tracker):
        """The failure mode worth seeing is the day with nothing in it at all."""
        today = date.today()
        habit_tracker.upsert(tracker, (today.replace(day=1)).isoformat(), "wake", "yes")
        result = CliRunner().invoke(habit_app, ["stats", "--all"])

        assert result.exit_code == 0
        assert "blank" in result.output

    def test_shows_complete_day_count(self, tracker):
        today = date.today().isoformat()
        for label in NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, today, label, "yes")
        result = CliRunner().invoke(habit_app, ["stats"])

        assert "Complete days: 1" in result.output


# ===========================================================================
# navig habit pause / resume
# ===========================================================================

class FakeService:
    """Stands in for the live CronService."""

    class Job:
        def __init__(self, job_id, name):
            self.id, self.name, self.enabled = job_id, name, True

        def to_dict(self):
            return {"id": self.id, "name": self.name, "enabled": self.enabled}

    def __init__(self, names):
        self.jobs = {f"id-{i}": self.Job(f"id-{i}", n) for i, n in enumerate(names)}

    def enable_job(self, job_id):
        self.jobs[job_id].enabled = True
        return True

    def disable_job(self, job_id):
        self.jobs[job_id].enabled = False
        return True


@pytest.fixture
def service(monkeypatch):
    svc = FakeService(["habit:wake", "habit:out", "backup-nightly"])
    monkeypatch.setattr(habit_store, "_live_service", lambda: svc)
    return svc


class TestPauseResume:
    def test_pause_all_switches_off_every_habit(self, service):
        changed = habit_store.set_jobs_enabled(None, enabled=False)
        assert sorted(changed) == ["out", "wake"]
        assert all(not j.enabled for j in service.jobs.values() if j.name.startswith("habit:"))

    def test_pause_all_never_touches_other_cron_jobs(self, service):
        """THE GUARD: a blanket pause must not switch off unrelated automation."""
        habit_store.set_jobs_enabled(None, enabled=False)
        backup = next(j for j in service.jobs.values() if j.name == "backup-nightly")
        assert backup.enabled is True

    def test_pause_one_leaves_the_others_running(self, service):
        habit_store.set_jobs_enabled("out", enabled=False)
        by_name = {j.name: j.enabled for j in service.jobs.values()}
        assert by_name["habit:out"] is False
        assert by_name["habit:wake"] is True

    def test_resume_turns_it_back_on(self, service):
        habit_store.set_jobs_enabled("out", enabled=False)
        assert habit_store.set_jobs_enabled("out", enabled=True) == ["out"]
        assert next(j for j in service.jobs.values() if j.name == "habit:out").enabled is True

    def test_an_unknown_habit_is_reported_not_silently_ignored(self, service):
        result = CliRunner().invoke(habit_app, ["pause", "nonexistent"])
        assert result.exit_code == 1
        assert "nonexistent" in result.output

    def test_pause_says_nothing_was_deleted(self, service):
        """Pausing must not read as quitting — that distinction is the whole point."""
        result = CliRunner().invoke(habit_app, ["pause"])
        assert result.exit_code == 0
        assert "Nothing is deleted" in result.output
        assert "resume" in result.output

    def test_pause_reports_habits_in_words(self, service):
        result = CliRunner().invoke(habit_app, ["pause"])
        assert "Walk" in result.output  # not the raw label "out"


# ===========================================================================
# The same two things, on the phone: /stats and /pause
# ===========================================================================

class TestTelegramStatsPanel:
    """`navig habit stats` in a terminal is a report you must go and fetch.

    The tracker lives on the phone — the cards arrive there, the taps land
    there — so the panel has to as well, or it does not get read.
    """

    def _panel(self, tracker, days=7):
        from navig.telegram import habit_actions

        return habit_actions.build_stats(tracker, date.today(), days)

    def test_empty_tracker_does_not_render_a_wall_of_zeroes(self, tracker):
        text, keyboard = self._panel(tracker)
        assert "Nothing logged yet" in text
        assert keyboard["inline_keyboard"] == []

    def test_panel_names_the_three_in_words(self, tracker):
        habit_tracker.upsert(tracker, date.today().isoformat(), "out", "yes")
        text, _ = self._panel(tracker)

        assert "Walk" in text
        assert "\nout " not in text, "a raw tracker label reached the screen"

    def test_panel_offers_every_range(self, tracker):
        from navig.telegram import habit_actions

        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        _, keyboard = self._panel(tracker)
        data = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
        assert data == [f"hb:st:{n}" for n, _ in habit_actions.STATS_RANGES]

    def test_the_selected_range_is_marked(self, tracker):
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        _, keyboard = self._panel(tracker, days=14)
        marked = [b["text"] for row in keyboard["inline_keyboard"] for b in row if "▶" in b["text"]]
        assert marked == ["▶ 14 days"]

    def test_blank_days_are_marked_and_the_mark_is_explained(self, tracker):
        """A bare symbol is a symbol you have to ask about — the "Groom" defect."""
        old = (date.today() - timedelta(days=3)).isoformat()
        habit_tracker.upsert(tracker, old, "wake", "yes")
        text, _ = self._panel(tracker, days=4)

        assert "←" in text
        assert "nothing logged" in text

    def test_no_legend_when_there_is_nothing_to_explain(self, tracker):
        for label in NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, date.today().isoformat(), label, "yes")
        text, _ = self._panel(tracker, days=1)
        assert "nothing logged" not in text

    def test_the_tables_are_monospace(self, tracker):
        """Telegram has no table markup; <pre> is the only alignment there is."""
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        text, _ = self._panel(tracker)
        assert text.count("<pre>") == 3

    def test_grid_marks_are_text_not_emoji(self, tracker):
        """Emoji are double-width and oversized — they cannot hold a column."""
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        text, _ = self._panel(tracker)
        grid = text[text.index("Day by day") :]
        assert "✅" not in grid and "❌" not in grid

    def test_the_day_column_names_both_wake_and_walk(self, tracker):
        """Truncating to two characters made "Wake" and "Walk" the same header."""
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        text, _ = self._panel(tracker)
        header = text[text.index("Day by day") :]
        assert "Wake" in header and "Walk" in header

    def test_callback_data_fits_telegram_budget(self, tracker):
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        _, keyboard = self._panel(tracker)
        for row in keyboard["inline_keyboard"]:
            for b in row:
                assert len(b["callback_data"].encode()) <= 64


class TestTelegramPauseMenu:
    def test_menu_lists_every_reminder(self, service):
        from navig.telegram import habit_actions

        _, keyboard = habit_actions.build_pause_menu()
        rows = [r[0]["callback_data"] for r in keyboard["inline_keyboard"][:-1]]
        assert rows == ["hb:p:wake", "hb:p:out"] or rows == ["hb:p:out", "hb:p:wake"]

    def test_menu_never_offers_a_non_habit_job(self, service):
        """THE GUARD: the operator's other cron work is not the tracker's to switch off."""
        from navig.telegram import habit_actions

        _, keyboard = habit_actions.build_pause_menu()
        data = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
        assert not any("backup" in d for d in data)

    def test_a_paused_reminder_offers_resume_not_pause(self, service):
        from navig.telegram import habit_actions

        habit_store.set_jobs_enabled("out", enabled=False)
        _, keyboard = habit_actions.build_pause_menu()
        out = next(
            b for row in keyboard["inline_keyboard"] for b in row if b["callback_data"].endswith(":out")
        )
        assert out["callback_data"] == "hb:r:out"
        assert out["text"].startswith("🔕")

    def test_menu_says_nothing_is_deleted(self, service):
        from navig.telegram import habit_actions

        text, _ = habit_actions.build_pause_menu()
        assert "Nothing is deleted" in text

    def test_menu_rows_read_as_words(self, service):
        """`ship_weekend` on a button is the raw-label defect on a different screen."""
        from navig.telegram import habit_actions

        _, keyboard = habit_actions.build_pause_menu()
        for row in keyboard["inline_keyboard"][:-1]:
            label = row[0]["text"].split(" ", 1)[1]
            assert "_" not in label, f"raw job key on a button: {label}"


class TestPhoneCallbacks:
    """The taps behind /stats and /pause."""

    def _tap(self, channel, tracker, data):
        habit_tracker.remember_target(-424, tracker)
        return asyncio.run(
            habit_actions_module().handle_callback(channel, data, -424, 7)
        )

    def test_range_button_re_renders_the_panel(self, tracker, service, monkeypatch, tmp_path):
        monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "t.json")
        habit_tracker.upsert(tracker, date.today().isoformat(), "wake", "yes")
        channel = FakeChannel()
        toast = self._tap(channel, tracker, "hb:st:0")

        assert "All days" in toast
        assert channel.calls[-1][0] == "editMessageText"

    def test_pause_button_switches_the_job_off(self, tracker, service, monkeypatch, tmp_path):
        monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "t.json")
        channel = FakeChannel()
        toast = self._tap(channel, tracker, "hb:p:out")

        assert "Paused" in toast and "Walk" in toast
        assert next(j for j in service.jobs.values() if j.name == "habit:out").enabled is False

    def test_resume_all_turns_everything_back_on(self, tracker, service, monkeypatch, tmp_path):
        monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "t.json")
        habit_store.set_jobs_enabled(None, enabled=False)
        channel = FakeChannel()
        toast = self._tap(channel, tracker, "hb:r:*")

        assert "On" in toast
        assert all(j.enabled for j in service.jobs.values() if j.name.startswith("habit:"))

    def test_a_tap_that_changes_nothing_says_so(self, tracker, service, monkeypatch, tmp_path):
        monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "t.json")
        channel = FakeChannel()
        assert "⚠️" in self._tap(channel, tracker, "hb:p:nosuchjob")


class FakeChannel:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}


def habit_actions_module():
    from navig.telegram import habit_actions

    return habit_actions
