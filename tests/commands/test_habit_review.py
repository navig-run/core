"""
Tests for the generated weekly review and for dictated journal entries.

Two things are load-bearing here. The review must show the days that are
*missing* — a summary built only from the rows that exist reports a broken week
as a good one. And a dictated entry must never be dressed up as three labelled
lines: speech-to-text returns one run of words, and attaching the card's
questions to it would be the system writing the owner's journal for him.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from navig.spaces import habit_tracker, journal, weekly_review

MON = date(2026, 8, 17)
SUN = date(2026, 8, 23)
CHAT = -555


@pytest.fixture
def tracker(tmp_path):
    return tmp_path / "habits.csv"


@pytest.fixture(autouse=True)
def _isolate_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "targets.json")


def _full_week(tracker, *, skip: set[str] = frozenset()):
    for i in range(7):
        day = date(2026, 8, 17 + i).isoformat()
        if day in skip:
            continue
        for label in habit_tracker.NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, day, label, "yes")


# ===========================================================================
# average_bedtime — the number the sleep rail is steered by
# ===========================================================================

class TestAverageBedtime:
    def test_after_midnight_does_not_average_to_midday(self):
        """23:40 and 00:20 are twenty minutes apart, not twelve hours."""
        assert weekly_review.average_bedtime(["23:40", "00:20"]) == "00:00"

    def test_plain_evening_times(self):
        assert weekly_review.average_bedtime(["23:00", "23:30"]) == "23:15"

    def test_junk_is_ignored_not_counted_as_zero(self):
        assert weekly_review.average_bedtime(["23:00", "soon", "", "25:99"]) == "23:00"

    def test_nothing_usable_gives_nothing(self):
        assert weekly_review.average_bedtime(["", "later"]) is None


# ===========================================================================
# The review block
# ===========================================================================

class TestBuild:
    def test_a_perfect_week_reports_seven_of_seven(self, tracker):
        _full_week(tracker)
        block = weekly_review.build(tracker, MON, SUN)

        assert "Complete days: 7 of 7" in block
        for label in habit_tracker.NON_NEGOTIABLES:
            assert "7/7" in block

    def test_missing_days_are_named_not_hidden(self, tracker):
        """A summary of only the logged days reports a broken week as a good one."""
        _full_week(tracker, skip={"2026-08-19", "2026-08-22"})
        block = weekly_review.build(tracker, MON, SUN)

        assert "Complete days: 5 of 7" in block
        assert "**No tracker row at all:**" in block
        assert "Wed 19" in block and "Sat 22" in block

    def test_the_journal_setback_lines_are_pulled_in(self, tracker):
        _full_week(tracker)
        journal.append_entry(
            tracker, "2026-08-18", "shipped the page\nlost an hour to a client\nwrite the README"
        )
        block = weekly_review.build(tracker, MON, SUN)

        assert "lost an hour to a client" in block
        assert "Tue 18" in block

    def test_a_dictated_entry_is_not_mined_for_a_setback(self, tracker):
        """Picking a sentence out of free speech and calling it "what broke" invents a finding."""
        _full_week(tracker)
        journal.append_entry(
            tracker, "2026-08-18", "today was fine but a client ate an hour", dictated=True
        )
        block = weekly_review.build(tracker, MON, SUN)

        assert "_nothing recorded this week_" in block
        assert "ate an hour" not in block

    def test_days_without_three_lines_are_listed(self, tracker):
        _full_week(tracker)
        journal.append_entry(tracker, "2026-08-18", "a\nb\nc")
        block = weekly_review.build(tracker, MON, SUN)

        assert "**No three lines:**" in block
        assert "Mon 17" in block
        assert "Tue 18" not in block.split("**No three lines:**")[1].split("\n")[0]

    def test_a_written_review_does_not_count_as_that_day_s_entry(self, tracker):
        """--write creates the file; the review must not then claim the day has an entry."""
        _full_week(tracker)
        journal.append_block(tracker, SUN.isoformat(), "## Review — earlier\n\nsomething\n")
        block = weekly_review.build(tracker, MON, SUN)

        assert "**No three lines:**" in block
        assert "Sun 23" in block.split("**No three lines:**")[1]

    def test_bedtime_and_score_averages_appear(self, tracker):
        _full_week(tracker)
        habit_tracker.upsert(tracker, "2026-08-17", "sleep_time", "23:00")
        habit_tracker.upsert(tracker, "2026-08-18", "sleep_time", "23:30")
        habit_tracker.upsert(tracker, "2026-08-17", "score", "6")
        habit_tracker.upsert(tracker, "2026-08-18", "score", "8")
        block = weekly_review.build(tracker, MON, SUN)

        assert "average **23:15**" in block
        assert "average **7**" in block

    def test_other_labels_are_counted_with_their_words(self, tracker):
        _full_week(tracker)
        habit_tracker.upsert(tracker, "2026-08-17", "train", "yes")
        habit_tracker.upsert(tracker, "2026-08-19", "train", "yes")
        block = weekly_review.build(tracker, MON, SUN)

        assert "Train 2/7" in block

    def test_the_decision_line_is_left_blank(self, tracker):
        _full_week(tracker)
        block = weekly_review.build(tracker, MON, SUN)

        assert "**One decision for next week:**" in block
        assert "____" in block

    def test_an_empty_tracker_says_so_instead_of_faking_a_week(self, tracker):
        block = weekly_review.build(tracker, MON, SUN)

        assert "Complete days: 0 of 7" in block
        assert "nothing above was measured" in block


# ===========================================================================
# Dictation
# ===========================================================================

class TestDictatedEntry:
    def test_a_transcript_is_kept_verbatim_and_marked(self, tracker):
        path, written = journal.append_entry(
            tracker, "2026-08-26", "shipped the page lost an hour to a client tomorrow the readme",
            dictated=True,
        )
        body = path.read_text(encoding="utf-8")

        assert written is True
        assert "## Evening check-in (dictated)" in body
        assert "- shipped the page lost an hour to a client tomorrow the readme" in body
        for prompt in journal.PROMPTS:
            assert f"**{prompt}:**" not in body

    def test_three_spoken_lines_are_still_not_labelled(self, tracker):
        """Even if the transcript happens to arrive in three lines, it is speech."""
        path, _ = journal.append_entry(tracker, "2026-08-26", "one\ntwo\nthree", dictated=True)
        body = path.read_text(encoding="utf-8")

        assert "- one" in body
        assert "**Proud of:**" not in body

    def test_the_gateway_passes_the_dictation_flag_through(self, tracker):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        class FakeChannel:
            def __init__(self):
                self.sent: list[str] = []

            async def send_message(self, chat_id, text, parse_mode=None):
                self.sent.append(text)

        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, "2026-08-26", 909)

        consumed = asyncio.run(
            TelegramCommandsMixin._handle_pending_journal_input(
                channel, CHAT, "spoken out loud", 909, True
            )
        )

        assert consumed is True
        body = journal.entry_path(tracker, "2026-08-26").read_text(encoding="utf-8")
        assert "(dictated)" in body
