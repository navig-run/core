"""Dates the operator actually writes: French, Russian, and anywhere in a sentence.

Regression for a real message. "Ce rendez-vous a lieu le lundi 26 octobre 2026 à
10h30 en visioconférence (1) avec CRABOS SARAH." reached the bot, which replied
"Rendez-vous noté" and saved nothing: the parser knew only English names, rejected
"10h30", and read dates off the END of a line only. `find_when` is the anywhere-scan
the appointment capture needs, and it is strict on purpose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from navig.pim.dates import find_date, find_when, split_due

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)

SCREENSHOT = (
    "Ce rendez-vous a lieu le lundi 26 octobre 2026 à 10h30 en visioconférence "
    "(1) avec CRABOS SARAH."
)


def _span(text: str, found: tuple[datetime, int, int]) -> str:
    return " ".join(text.split()[found[1] : found[2]])


def test_the_message_from_the_screenshot() -> None:
    found = find_when(SCREENSHOT, NOW)
    assert found is not None
    assert found[0] == datetime(2026, 10, 26, 10, 30, tzinfo=TZ)
    assert _span(SCREENSHOT, found) == "le lundi 26 octobre 2026 à 10h30"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Встреча в понедельник 26 октября в 10:30 с врачом", datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
        ("Dentist Oct 26 10:30 downtown", datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
        ("rdv 3 novembre 9h15 chez le notaire", datetime(2026, 11, 3, 9, 15, tzinfo=TZ)),
        ("Pick up keys 12/10 at 9am please", datetime(2026, 10, 12, 9, 0, tzinfo=TZ)),
        ("Interview 2026-10-20 14:00 on zoom", datetime(2026, 10, 20, 14, 0, tzinfo=TZ)),
    ],
)
def test_finds_a_date_anywhere(text: str, expected: datetime) -> None:
    found = find_when(text, NOW)
    assert found is not None, text
    assert found[0] == expected


@pytest.mark.parametrize(
    "text",
    [
        "✅ Записано: 124 кг · среднее за 7 дней 124.3 кг",  # a weight, not a date
        "we met on monday at 10:30",  # a weekday alone is a story, not an appointment
        "rdv le 5 janvier 2025 à 10h00",  # already in the past
        "lundi 26 octobre 2026",  # a date with no clock time
        "Call mom in 3h",
        "Call Sep about the invoice at 10:30",  # a name, not a month: no day number
    ],
)
def test_does_not_invent_an_appointment(text: str) -> None:
    assert find_when(text, NOW) is None


def test_french_time_needs_its_minutes() -> None:
    """A bare "3h" is a duration. Reading it as 03:00 would make "call mom in 3h" a
    task due at three in the morning."""
    assert split_due("Call mom in 3h", NOW) == ("Call mom", NOW + timedelta(hours=3))
    assert split_due("Call mom in 1h30", NOW) == ("Call mom in 1h30", None)


@pytest.mark.parametrize(
    ("text", "title", "expected"),
    [
        ("Visio Sarah Crabos oct 26 10:30", "Visio Sarah Crabos", datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
        ("rdv lundi 26 octobre 10h30", "rdv", datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
        ("Стоматолог 26 октября в 10:30", "Стоматолог", datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
    ],
)
def test_todo_capture_understands_the_new_forms(text: str, title: str, expected: datetime) -> None:
    assert split_due(text, NOW) == (title, expected)


# ── find_date: the detect-only scan behind offering the task tools ────────────
# find_when stays strict (it can silently capture an appointment); find_date only
# answers "does this message carry a date?", because a deadline date with no clock
# time ("renew on October 28") offered NO task tools and the model could only ask
# the user for the date it could already read (incident 2026-10-13).


def test_find_date_reads_a_deadline_without_a_clock_time() -> None:
    found = find_date("renew on October 28", NOW)
    assert found is not None
    assert found[0] == datetime(2026, 10, 28, 9, 0, tzinfo=TZ)


def test_find_date_reads_a_russian_deadline() -> None:
    found = find_date("оплата 28 октября", NOW)
    assert found is not None
    assert found[0] == datetime(2026, 10, 28, 9, 0, tzinfo=TZ)


def test_find_date_keeps_an_explicit_clock_time() -> None:
    """When a time IS present it wins over the default hour, like find_when."""
    found = find_date(SCREENSHOT, NOW)
    assert found is not None
    assert found[0] == datetime(2026, 10, 26, 10, 30, tzinfo=TZ)


def test_find_date_still_refuses_the_past() -> None:
    # An ISO date carries its year inside one token, so no shorter window can
    # drop the year and re-read it as a future date.
    assert find_date("meeting 2025-01-05 was cancelled", NOW) is None


@pytest.mark.parametrize("text", ["October", "28", ""])
def test_find_date_needs_a_date_not_a_lone_token(text: str) -> None:
    assert find_date(text, NOW) is None


def test_find_when_stays_strict_about_clock_time() -> None:
    """find_date loosens the GATE only — find_when's contract is untouched, so
    a bare deadline date is still never silently captured as an appointment."""
    assert find_when("renew on October 28", NOW) is None
    assert find_date("renew on October 28", NOW) is not None


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("Dentist tomorrow 10:30", "Dentist"),
        ("Report friday 9am", "Report"),
        ("Pay rent sep 12", "Pay rent"),
    ],
)
def test_english_suffixes_are_unchanged(text: str, title: str) -> None:
    got_title, due = split_due(text, NOW)
    assert got_title == title
    assert due is not None
    assert split_due("Call Sep about the invoice", NOW) == ("Call Sep about the invoice", None)


# ── relative days (Thursday 8 Oct 2026, noon) ────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rdv chez le dentiste demain à 10h", datetime(2026, 10, 9, 10, 0, tzinfo=TZ)),
        ("Rendez-vous après-demain à 14h30 avec Marc", datetime(2026, 10, 10, 14, 30, tzinfo=TZ)),
        ("Réunion lundi prochain à 9h15 au bureau", datetime(2026, 10, 12, 9, 15, tzinfo=TZ)),
        ("Appel dans 3 jours à 10h avec la banque", datetime(2026, 10, 11, 10, 0, tzinfo=TZ)),
        ("Встреча завтра в 15:00 с юристом", datetime(2026, 10, 9, 15, 0, tzinfo=TZ)),
        ("Врач послезавтра в 10:30 клиника", datetime(2026, 10, 10, 10, 30, tzinfo=TZ)),
        ("Созвон в следующий понедельник в 11:00 с командой", datetime(2026, 10, 12, 11, 0, tzinfo=TZ)),
        ("Звонок через 2 дня в 9:00 маме", datetime(2026, 10, 10, 9, 0, tzinfo=TZ)),
        ("Dentist tomorrow at 10:30 downtown", datetime(2026, 10, 9, 10, 30, tzinfo=TZ)),
    ],
)
def test_relative_days_in_every_language(text: str, expected: datetime) -> None:
    found = find_when(text, NOW)
    assert found is not None, text
    assert found[0] == expected


@pytest.mark.parametrize(
    "text",
    [
        "hier à 10h on a parlé",  # the past
        "вчера в 10:30 была встреча",
        "I slept 8h today ok",  # "8h" is a duration
        "Workout 2h tonight ok fine",
    ],
)
def test_relative_days_do_not_invent(text: str) -> None:
    assert find_when(text, NOW) is None


def test_a_bare_h_needs_a_day() -> None:
    """ "10h" is ten o'clock in French — and "2h" is two hours. Only a named day
    tells them apart, so without one it stays in the title."""
    assert split_due("Workout 2h", NOW) == ("Workout 2h", None)
    assert split_due("Dentiste demain 10h", NOW) == ("Dentiste", datetime(2026, 10, 9, 10, 0, tzinfo=TZ))
    assert split_due("Pay bills dans 3 jours", NOW)[1] == datetime(2026, 10, 11, 12, 0, tzinfo=TZ)


def test_a_past_date_is_not_shortened_into_tomorrow() -> None:
    """ "… 5 janvier 2025 10h00" failed as a whole (it is past), and the suffix scan
    then took just "10h00" — the task landed on tomorrow, nowhere near what was typed."""
    assert split_due("old thing 5 janvier 2025 10h00", NOW) == ("old thing 5 janvier 2025 10h00", None)
