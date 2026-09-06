"""The date grammar behind `/todo add Dentist tomorrow 10:30`.

Pure functions with `now` passed in, so every case here is deterministic — no frozen
clock, no timezone of the machine running it. The reference instant is a Friday
afternoon, chosen because it is the moment where "friday", "9am" and "next monday"
all mean different things depending on rules that are easy to get subtly wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from navig.pim.dates import (
    RECURRENCES,
    RecurrenceError,
    advance,
    describe_lead,
    format_local,
    humanize_delta,
    parse_lead,
    split_due,
    split_recurrence,
)

TZ = timezone(timedelta(hours=2))
# Friday 4 September 2026, 15:00 local. A Friday afternoon is the instant where
# "friday", "9am" and "next friday" each resolve through a DIFFERENT rule, so the
# weekday branches are actually exercised instead of accidentally agreeing.
NOW = datetime(2026, 9, 4, 15, 0, tzinfo=TZ)


def due(text: str, now: datetime = NOW):
    return split_due(text, now)


# ── the phrase is a SUFFIX ───────────────────────────────────────────────────

def test_a_month_name_in_the_middle_is_not_a_date() -> None:
    """The rule the whole design turns on.

    Scanning anywhere would make this task due in September. A missed date the
    operator adds with one tap beats a wrong one they never notice.
    """
    title, when = due("Call Sep about the invoice")
    assert when is None
    assert title == "Call Sep about the invoice"


def test_longest_suffix_wins() -> None:
    """`10:30` alone would leave the title as "Dentist tomorrow"."""
    title, when = due("Dentist tomorrow 10:30")
    assert title == "Dentist"
    assert when == datetime(2026, 9, 5, 10, 30, tzinfo=TZ)


def test_a_date_phrase_may_not_swallow_the_whole_line() -> None:
    """"tomorrow" typed alone is a title, not a task with no name."""
    title, when = due("tomorrow")
    assert title == "tomorrow"
    assert when is None


def test_a_leading_preposition_is_dropped_from_the_date_not_the_title() -> None:
    title, when = due("Pay the bill on friday")
    assert title == "Pay the bill"
    assert when == datetime(2026, 9, 11, 9, 0, tzinfo=TZ)


def test_a_trailing_comma_or_dash_is_trimmed_from_the_title() -> None:
    title, _ = due("Renew the domain - tomorrow")
    assert title == "Renew the domain"


# ── day anchors ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("x today 18:00", datetime(2026, 9, 4, 18, 0, tzinfo=TZ)),
        ("x tomorrow", datetime(2026, 9, 5, 9, 0, tzinfo=TZ)),
        ("x next monday", datetime(2026, 9, 7, 9, 0, tzinfo=TZ)),
        ("x sep 12", datetime(2026, 9, 12, 9, 0, tzinfo=TZ)),
        ("x 12 sep", datetime(2026, 9, 12, 9, 0, tzinfo=TZ)),
        ("x september 12th", datetime(2026, 9, 12, 9, 0, tzinfo=TZ)),
        ("x 2026-12-24 18:30", datetime(2026, 12, 24, 18, 30, tzinfo=TZ)),
        ("x 24/12", datetime(2026, 12, 24, 9, 0, tzinfo=TZ)),
        ("x in 3 days", datetime(2026, 9, 7, 15, 0, tzinfo=TZ)),
        ("x in 2 weeks", datetime(2026, 9, 18, 15, 0, tzinfo=TZ)),
        ("x in 90 minutes", datetime(2026, 9, 4, 16, 30, tzinfo=TZ)),
        ("x in 90m", datetime(2026, 9, 4, 16, 30, tzinfo=TZ)),
    ],
)
def test_day_anchors(text: str, expected: datetime) -> None:
    assert due(text)[1] == expected


def test_a_named_day_with_no_time_lands_at_nine_not_midnight() -> None:
    """A task due at 00:00 Saturday is one you see on Friday night and forget."""
    assert due("x saturday")[1] == datetime(2026, 9, 5, 9, 0, tzinfo=TZ)


def test_next_friday_on_a_friday_skips_today() -> None:
    """"next" never means today — the word is there precisely to exclude it."""
    assert due("x next friday")[1] == datetime(2026, 9, 11, 9, 0, tzinfo=TZ)


def test_a_bare_weekday_can_mean_today_when_the_hour_is_still_ahead() -> None:
    assert due("x friday 18:00")[1] == datetime(2026, 9, 4, 18, 0, tzinfo=TZ)


def test_a_bare_weekday_whose_hour_has_passed_rolls_a_week() -> None:
    """"friday 9am" typed on Friday afternoon means next Friday, not this morning."""
    assert due("x friday 9am")[1] == datetime(2026, 9, 11, 9, 0, tzinfo=TZ)


def test_a_past_month_day_means_next_year() -> None:
    """A task list cannot hold a date in the past.

    Silently creating one is how a list starts lying about what is overdue.
    """
    assert due("x jan 3")[1] == datetime(2027, 1, 3, 9, 0, tzinfo=TZ)


def test_yesterday_is_refused_rather_than_scheduled() -> None:
    assert due("x yesterday")[1] is None


def test_an_impossible_calendar_date_is_refused() -> None:
    assert due("x 2026-02-30")[1] is None
    assert due("x 31/02")[1] is None


def test_29_february_resolves_to_a_leap_year_rather_than_being_clamped() -> None:
    # 2027 is not a leap year; 2028 is. Clamping to the 28th would be a different day.
    assert due("x feb 29")[1] == datetime(2028, 2, 29, 9, 0, tzinfo=TZ)


# ── time of day ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "hour", "minute"),
    [
        ("x tomorrow 9am", 9, 0),
        ("x tomorrow 9 am", 9, 0),
        ("x tomorrow 9pm", 21, 0),
        ("x tomorrow 12am", 0, 0),
        ("x tomorrow 12pm", 12, 0),
        ("x tomorrow 9:30", 9, 30),
        ("x tomorrow 9.30pm", 21, 30),
        ("x tomorrow at 18:45", 18, 45),
    ],
)
def test_times(text: str, hour: int, minute: int) -> None:
    when = due(text)[1]
    assert when is not None
    assert (when.hour, when.minute) == (hour, minute)


def test_a_bare_number_is_not_a_time() -> None:
    """"Buy 3" is a quantity. Guessing 3 o'clock puts the task at a wrong hour with
    nothing to notice it by — a bare number needs a meridiem or a minute part."""
    assert due("Buy 3")[1] is None
    assert due("Order 12 chairs")[1] is None


def test_a_time_alone_with_no_day_uses_today_when_it_is_still_ahead() -> None:
    assert due("Standup 18:00")[1] == datetime(2026, 9, 4, 18, 0, tzinfo=TZ)


def test_a_time_alone_that_has_passed_means_tomorrow() -> None:
    """9am typed at 3pm is tomorrow morning; today's 9am is gone."""
    assert due("Standup 9am")[1] == datetime(2026, 9, 5, 9, 0, tzinfo=TZ)


def test_an_out_of_range_time_is_refused() -> None:
    assert due("x tomorrow 25:00")[1] is None
    assert due("x tomorrow 10:99")[1] is None
    assert due("x tomorrow 13pm")[1] is None


def test_the_result_keeps_the_zone_it_was_given() -> None:
    """Reminders compare as strings against a UTC value; a naive local time stored
    as-if-UTC fires off by the server's offset."""
    when = due("x tomorrow 10:00")[1]
    assert when is not None
    assert when.tzinfo is TZ


# ── recurrence ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "rest", "recur"),
    [
        ("Water plants every day", "Water plants", "daily"),
        ("Standup every monday", "Standup", "weekly"),
        ("Rent every month", "Rent", "monthly"),
        ("Insurance every year", "Insurance", "yearly"),
        ("Backup weekly", "Backup", "weekly"),
        ("Report daily", "Report", "daily"),
        ("Call mum", "Call mum", None),
    ],
)
def test_split_recurrence(text: str, rest: str, recur: str | None) -> None:
    assert split_recurrence(text) == (rest, recur)


def test_a_weekday_recurrence_does_not_store_the_weekday_twice() -> None:
    """`every monday` is `weekly`; WHICH day lives in the due date.

    Storing it in both places is two facts that can disagree — move the due date and
    the recurrence would still claim Monday.
    """
    assert split_recurrence("Standup every tuesday")[1] == "weekly"


def test_recurrence_composes_with_a_date() -> None:
    rest, recur = split_recurrence("Standup every monday 9:30")
    title, when = due(rest)
    assert (title, recur) == ("Standup", "weekly")
    assert when is not None
    assert (when.hour, when.minute) == (9, 30)


@pytest.mark.parametrize("recur", RECURRENCES)
def test_advance_always_moves_forward(recur: str) -> None:
    start = datetime(2026, 9, 5, 9, 0, tzinfo=TZ)
    assert advance(start, recur) > start


def test_monthly_clamps_to_the_month_length_rather_than_skipping() -> None:
    """31 Jan + 1 month is 28 Feb. Any other answer skips a month or invents a date,
    and for a recurrence that compounds every time it fires."""
    assert advance(datetime(2027, 1, 31, 9, 0, tzinfo=TZ), "monthly") == datetime(
        2027, 2, 28, 9, 0, tzinfo=TZ
    )


def test_monthly_from_a_leap_day_lands_on_a_real_date() -> None:
    assert advance(datetime(2028, 2, 29, 9, 0, tzinfo=TZ), "yearly") == datetime(
        2029, 2, 28, 9, 0, tzinfo=TZ
    )


def test_an_unknown_recurrence_raises_rather_than_standing_still() -> None:
    """Returning the input would make the task due forever and re-fire its reminder
    on every poll — a silent infinite loop rather than one loud error."""
    with pytest.raises(RecurrenceError):
        advance(NOW, "fortnightly")


# ── lead times and rendering ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("token", "seconds"),
    [("15m", 900), ("1h", 3600), ("1d", 86400), ("3d", 259200), ("1w", 604800)],
)
def test_parse_lead(token: str, seconds: int) -> None:
    delta = parse_lead(token)
    assert delta is not None
    assert delta.total_seconds() == seconds


def test_an_unknown_lead_is_none_not_zero() -> None:
    """Zero would schedule a "reminder" at the due moment itself, silently."""
    assert parse_lead("soon") is None
    assert parse_lead("3") is None


def test_describe_lead_reads_as_english_and_survives_a_bad_token() -> None:
    assert describe_lead("3d") == "3 days before"
    assert describe_lead("1h") == "1 hour before"
    assert describe_lead("nonsense") == "nonsense"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=3), "in 3h"),
        (timedelta(days=2), "in 2d"),
        (timedelta(minutes=45), "in 45m"),
        (timedelta(days=-2), "2d ago"),
        (timedelta(seconds=10), "now"),
        (timedelta(seconds=-10), "now"),
    ],
)
def test_humanize_delta(offset: timedelta, expected: str) -> None:
    assert humanize_delta(NOW + offset, NOW) == expected


def test_format_local_omits_the_year_for_this_year_and_shows_it_otherwise() -> None:
    assert format_local(datetime(2026, 9, 11, 19, 0, tzinfo=TZ), NOW) == "Fri 11 Sep · 19:00"
    assert "2027" in format_local(datetime(2027, 2, 3, 9, 0, tzinfo=TZ), NOW)


def test_format_local_does_not_claim_midnight_as_a_time() -> None:
    """A task due "on Friday" must not read as due at 00:00."""
    assert format_local(datetime(2026, 9, 11, 0, 0, tzinfo=TZ), NOW) == "Fri 11 Sep"


def test_format_local_works_on_this_platform() -> None:
    """`%-d` is glibc-only; Windows raises ValueError on it.

    A platform check inside a render function is the kind of thing that is only ever
    exercised on one OS, so it is asserted rather than assumed — this failing takes
    down the whole card, not one row.
    """
    assert format_local(datetime(2026, 9, 5, 9, 0, tzinfo=TZ), NOW).startswith("Sat 5 Sep")


# ── the whole line ───────────────────────────────────────────────────────────

def test_the_full_capture_line() -> None:
    """What the operator actually types."""
    rest, recur = split_recurrence("Water the plants every day 19:00")
    title, when = due(rest)
    assert title == "Water the plants"
    assert recur == "daily"
    assert when == datetime(2026, 9, 4, 19, 0, tzinfo=TZ)


def test_a_plain_task_stays_a_plain_task() -> None:
    """The common case: capture with no date at all lands in the inbox."""
    rest, recur = split_recurrence("Call the accountant")
    title, when = due(rest)
    assert (title, when, recur) == ("Call the accountant", None, None)
