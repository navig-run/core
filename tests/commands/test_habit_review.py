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

    def test_a_historical_labelled_entry_still_yields_its_setback(self, tracker):
        """⚠ The fixture is written in the OLD shape ON PURPOSE.

        `setback_for` matches "**What knocked me off:**", which `append_entry`
        stopped writing when the card stopped asking the question. Building this
        fixture through `append_entry` would test the parser against input it
        cannot match — green only because nothing is asserted about the parse.
        Journals written before the change are full of these labels and must
        keep feeding the weekly review, so the old shape is written directly.
        """
        _full_week(tracker)
        path = journal.entry_path(tracker, "2026-08-18")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Journal — Tuesday, 18 August 2026\n\n## Evening check-in\n\n"
            "1. **Proud of:** shipped the page\n"
            "2. **What knocked me off:** lost an hour to a client\n"
            "3. **Tomorrow's number one:** write the README\n",
            encoding="utf-8",
        )
        block = weekly_review.build(tracker, MON, SUN)

        assert "lost an hour to a client" in block
        assert "Tue 18" in block

    def test_a_free_form_entry_is_not_mined_for_a_setback(self, tracker):
        """Silence is the honest answer, and it is the DOCUMENTED one.

        `setback_for` says picking a sentence out of free text and presenting it
        as "what broke this week" would be the system inventing a finding. Now
        that every new entry is free text, that branch is the normal path rather
        than the exception — so it is pinned, not left to inference.
        """
        _full_week(tracker)
        journal.append_entry(
            tracker,
            "2026-08-18",
            "Long day. The client call ate an hour and I never got the README done.",
        )
        block = weekly_review.build(tracker, MON, SUN)

        assert "The client call ate an hour" not in block

    def test_a_dictated_entry_is_not_mined_for_a_setback(self, tracker):
        """Picking a sentence out of free speech and calling it "what broke" invents a finding."""
        _full_week(tracker)
        journal.append_entry(
            tracker, "2026-08-18", "today was fine but a client ate an hour", dictated=True
        )
        block = weekly_review.build(tracker, MON, SUN)

        # The entry EXISTS, so "nothing recorded this week" would be a lie about
        # the operator's week. It says what is actually true: there are entries,
        # they are free-form, and no line is pulled out of them.
        assert "_nothing recorded this week_" not in block
        assert "written free-form" in block
        assert "ate an hour" not in block

    def test_a_week_of_free_form_entries_is_never_called_unrecorded(self, tracker):
        """The regression the free-form change would otherwise have caused.

        `setback_for` only matches the card's OLD labelled shape, so once entries
        became free-form it returns nothing for every day — and this section's
        empty branch rendered unconditionally. The operator would have written
        every evening and read "nothing recorded this week" over seven full
        entries, every week, forever.

        Found by grepping for the old wording, not by a failing test: nothing was
        asserting this, because before the change it could not happen.
        """
        _full_week(tracker)
        for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
            journal.append_entry(
                tracker, day, "A long and ordinary day, written out properly in prose."
            )
        block = weekly_review.build(tracker, MON, SUN)

        assert "_nothing recorded this week_" not in block, (
            "the review told the operator nothing was recorded while three "
            "entries sat in the journal"
        )
        assert "3 entries this week" in block

    def test_the_cli_review_carries_the_weeks_reflection_when_enabled(self, tracker, monkeypatch):
        """Same switch, second surface. The review is built pure and synchronous;
        the reflection is appended after, so a review still renders with no model."""
        from navig.spaces import journal_reflection
        from navig.telegram import ai_actions

        _full_week(tracker)
        for d in ("2026-08-18", "2026-08-20"):
            journal.append_entry(tracker, d, "A day written out at some length, in prose.")
        monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)

        async def _fake(tool, content, **_k):
            assert tool == "reflect_week"
            return {"ok": True, "result": "You kept circling the same client."}

        monkeypatch.setattr(ai_actions, "run_text_action", _fake)

        from navig.commands import habit as habit_cmd

        printed: list[str] = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        monkeypatch.setattr(habit_cmd, "_tracker_path", lambda space: tracker)
        habit_cmd.habit_review(ending="2026-08-23", days=7, write=False, space=None)

        out = "\n".join(printed)
        assert "**Reading the week back**" in out
        assert "You kept circling the same client." in out

    def test_the_cli_review_renders_without_a_model_when_disabled(self, tracker, monkeypatch):
        from navig.spaces import journal_reflection
        from navig.telegram import ai_actions

        _full_week(tracker)
        monkeypatch.setattr(journal_reflection, "is_enabled", lambda: False)

        async def _never(*a, **k):
            raise AssertionError("the model was called with the switch off")

        monkeypatch.setattr(ai_actions, "run_text_action", _never)

        from navig.commands import habit as habit_cmd

        printed: list[str] = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        monkeypatch.setattr(habit_cmd, "_tracker_path", lambda space: tracker)
        habit_cmd.habit_review(ending="2026-08-23", days=7, write=False, space=None)

        assert "## Review" in "\n".join(printed)
        assert "Reading the week back" not in "\n".join(printed)

    def test_a_week_with_no_entries_at_all_still_says_so(self, tracker):
        """The other side of it — the empty branch must survive, or the fix
        would have replaced one inaccuracy with its mirror image."""
        _full_week(tracker)
        block = weekly_review.build(tracker, MON, SUN)

        assert "_nothing recorded this week_" in block

    def test_days_with_nothing_written_are_listed(self, tracker):
        _full_week(tracker)
        journal.append_entry(tracker, "2026-08-18", "a\nb\nc")
        block = weekly_review.build(tracker, MON, SUN)

        assert "**Nothing written:**" in block
        assert "Mon 17" in block
        assert "Tue 18" not in block.split("**Nothing written:**")[1].split("\n")[0]

    def test_a_written_review_does_not_count_as_that_day_s_entry(self, tracker):
        """--write creates the file; the review must not then claim the day has an entry."""
        _full_week(tracker)
        journal.append_block(tracker, SUN.isoformat(), "## Review — earlier\n\nsomething\n")
        block = weekly_review.build(tracker, MON, SUN)

        assert "**Nothing written:**" in block
        assert "Sun 23" in block.split("**Nothing written:**")[1]

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
            tracker,
            "2026-08-26",
            "shipped the page lost an hour to a client tomorrow the readme",
            dictated=True,
        )
        body = path.read_text(encoding="utf-8")

        assert written is True
        assert "## Evening check-in (dictated)" in body
        assert "shipped the page lost an hour to a client tomorrow the readme" in body
        for prompt in journal.PROMPTS:
            assert f"**{prompt}:**" not in body

    def test_three_spoken_lines_are_still_not_labelled(self, tracker):
        """Even if the transcript happens to arrive in three lines, it is speech."""
        path, _ = journal.append_entry(tracker, "2026-08-26", "one\ntwo\nthree", dictated=True)
        body = path.read_text(encoding="utf-8")

        assert "one" in body
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


# ===========================================================================
# The body block — the review asks about "тело" and used to answer from nothing
# ===========================================================================


@pytest.fixture
def body_record(tmp_path, monkeypatch):
    """An isolated metrics.csv that the review's body block will resolve to."""
    from navig.spaces import body_metrics as bm

    path = tmp_path / "metrics.csv"
    path.write_text(
        "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "default_path", lambda: path)
    return path


def test_the_review_reports_the_weeks_weight(tracker, body_record):
    from navig.spaces import body_metrics as bm

    for i, kg in enumerate([89.0, 88.6, 88.2]):
        bm.upsert(body_record, date(2026, 8, 17 + i).isoformat(), {"weight_kg": str(kg)})

    text = weekly_review.build(tracker, MON, SUN)
    assert "**Weight:**" in text
    assert "89" in text and "88.2" in text


def test_the_review_names_the_days_with_no_reading(tracker, body_record):
    """Same rule as the tracker's blank days: a gap is invisible in a summary of
    what WAS recorded, and the average of three readings is not a week."""
    from navig.spaces import body_metrics as bm

    bm.upsert(body_record, MON.isoformat(), {"weight_kg": "89"})
    text = weekly_review.build(tracker, MON, SUN)
    assert "Recorded on 1 of 7 days" in text


def test_a_full_week_of_readings_does_not_nag(tracker, body_record):
    from navig.spaces import body_metrics as bm

    for i in range(7):
        bm.upsert(body_record, date(2026, 8, 17 + i).isoformat(), {"weight_kg": "88"})
    assert "Recorded on" not in weekly_review.build(tracker, MON, SUN)


def test_no_body_record_omits_the_block_entirely(tracker, body_record):
    """No weight is not a zero-weight week — the section simply does not appear."""
    text = weekly_review.build(tracker, MON, SUN)
    assert "**Weight:**" not in text


def test_readings_outside_the_window_are_not_counted(tracker, body_record):
    from navig.spaces import body_metrics as bm

    bm.upsert(body_record, "2026-07-01", {"weight_kg": "95"})
    assert "**Weight:**" not in weekly_review.build(tracker, MON, SUN)


def test_an_unreadable_body_record_does_not_take_the_review_down(tracker, monkeypatch):
    """The habit half is still worth rendering — the review must survive."""
    from navig.spaces import body_metrics as bm

    def boom(*_a, **_k):
        raise bm.MetricsReadError("locked")

    monkeypatch.setattr(bm, "default_path", boom)
    text = weekly_review.build(tracker, MON, SUN)
    assert "## Review" in text
    assert "**Weight:**" not in text
