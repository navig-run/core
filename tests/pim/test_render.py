"""What the task card looks like.

Pure functions of (todos, now), so the layout is asserted with no bot, no database and
no system clock. The rules worth pinning are the ones that quietly mislead rather than
break: a task in the wrong section, a countdown that says the wrong thing, or an AI
suggestion that renders exactly like something the operator wrote.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from navig.pim.clock import to_utc_iso
from navig.pim.render import (
    BUCKETS,
    bucket_of,
    group_by_bucket,
    render_agenda,
    render_category_summary,
    render_detail,
    todo_line,
)

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 4, 15, 0, tzinfo=TZ)


def todo(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc123",
        "title": "A task",
        "notes": "",
        "category": "",
        "due_at": None,
        "completed_at": None,
        "recur": None,
        "remind_before": [],
        "space": None,
        "origin": "manual",
    }
    if "due_in_hours" in over:
        base["due_at"] = to_utc_iso(NOW + timedelta(hours=over.pop("due_in_hours")))
    base.update(over)
    return base


# ── buckets ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "inbox"),
        ({"due_in_hours": -3}, "overdue"),
        ({"due_in_hours": 3}, "today"),
        ({"due_in_hours": 48}, "soon"),
        ({"due_in_hours": 24 * 30}, "later"),
        ({"completed_at": "2026-09-04T10:00:00.000Z"}, "done"),
    ],
)
def test_bucket_of(kwargs: dict, expected: str) -> None:
    assert bucket_of(todo(**kwargs), NOW) == expected


def test_a_completed_task_is_done_even_when_overdue(_=None) -> None:
    """Completion outranks the clock.

    Otherwise finishing something late leaves it sitting in OVERDUE, which reads as
    "you still have to do this" for a task that is finished.
    """
    row = todo(due_in_hours=-48, completed_at="2026-09-04T10:00:00.000Z")
    assert bucket_of(row, NOW) == "done"


def test_the_bucket_is_derived_on_every_render(_=None) -> None:
    """No stored `bucket` column, so a task cannot sit in the wrong section because
    the clock moved and nothing wrote to it."""
    row = todo(due_in_hours=3)
    assert bucket_of(row, NOW) == "today"
    assert bucket_of(row, NOW + timedelta(hours=6)) == "overdue"


def test_grouping_follows_the_declared_order_and_omits_empty_sections() -> None:
    rows = [todo(id="a"), todo(id="b", due_in_hours=-1), todo(id="c", due_in_hours=2)]
    grouped = group_by_bucket(rows, NOW)
    assert list(grouped) == ["overdue", "today", "inbox"]
    assert [k for k, _ in BUCKETS if k in grouped] == list(grouped)


# ── one row ──────────────────────────────────────────────────────────────────

def test_a_dated_row_carries_the_countdown(_=None) -> None:
    """The operator asked for this by name — "how much time is left" — so it is on
    every dated row rather than behind a tap."""
    line = todo_line(todo(title="Ship deck", due_in_hours=3), NOW)
    assert "in 3h" in line
    assert "18:00" in line


def test_an_overdue_row_says_how_late_it_is(_=None) -> None:
    assert "2d ago" in todo_line(todo(due_in_hours=-48), NOW)


def test_a_row_for_a_future_day_shows_the_date_not_just_the_time(_=None) -> None:
    """"09:00" with no day is ambiguous the moment a task is not today."""
    line = todo_line(todo(due_in_hours=48), NOW)
    assert "Sep" in line


def test_an_ai_suggestion_is_visibly_a_suggestion(_=None) -> None:
    """Without the marker the operator cannot tell what they wrote from what was
    proposed — the difference between something to dismiss and a commitment they
    think they made."""
    assert "✨" in todo_line(todo(origin="ai"), NOW)
    assert "✨" not in todo_line(todo(origin="manual"), NOW)


def test_a_title_with_html_in_it_cannot_break_the_card(_=None) -> None:
    """The card is sent with parse_mode=HTML, so an unescaped `<` in a task the
    operator typed makes Telegram reject the whole message — every row lost because
    of one character in one title."""
    line = todo_line(todo(title="Fix <b>bold</b> & co"), NOW)
    assert "&lt;b&gt;" in line
    assert "&amp;" in line


def test_a_recurring_row_says_so(_=None) -> None:
    assert "🔁" in todo_line(todo(recur="daily"), NOW)


# ── the agenda ───────────────────────────────────────────────────────────────

def test_the_agenda_groups_and_counts(_=None) -> None:
    rows = [
        todo(id="1", title="Late thing", due_in_hours=-24),
        todo(id="2", title="Today thing", due_in_hours=3),
        todo(id="3", title="Loose end"),
    ]
    text = render_agenda(rows, NOW)
    assert "3 open" in text
    assert "OVERDUE</b> (1)" in text
    assert "TODAY</b> (1)" in text
    assert "INBOX</b> (1)" in text
    assert text.index("Late thing") < text.index("Today thing") < text.index("Loose end")


def test_the_inbox_section_says_why_those_rows_have_no_date(_=None) -> None:
    """"no date yet" reads as a state; a bare empty column reads as a bug."""
    assert "no date yet" in render_agenda([todo()], NOW)


def test_done_rows_never_appear_in_the_agenda(_=None) -> None:
    rows = [todo(id="1", title="Finished", completed_at="2026-09-01T10:00:00.000Z")]
    text = render_agenda(rows, NOW)
    assert "Finished" not in text
    assert "0 open" in text


def test_an_empty_list_tells_you_how_to_add_one(_=None) -> None:
    """An empty state that only says "nothing here" is a dead end on a phone."""
    text = render_agenda([], NOW)
    assert "/todo" in text


def test_the_count_is_of_OPEN_todos(_=None) -> None:
    rows = [todo(id="1"), todo(id="2", completed_at="2026-09-01T10:00:00.000Z")]
    assert "1 open" in render_agenda(rows, NOW)


# ── categories ───────────────────────────────────────────────────────────────

def test_an_empty_category_still_appears(_=None) -> None:
    """A category you can file INTO is useful; one that only shows up once it already
    has something is a picker that is empty exactly when you first need it."""
    text = render_category_summary(
        [{"name": "life", "open": 2}, {"name": "business", "open": 0}], NOW
    )
    assert "life" in text and "2 open" in text
    assert "business" in text and "empty" in text


def test_the_uncategorised_bucket_gets_a_readable_name(_=None) -> None:
    assert "uncategorised" in render_category_summary([{"name": "", "open": 3}], NOW)


# ── the detail card ──────────────────────────────────────────────────────────

def test_the_detail_card_shows_the_settings_the_list_has_no_room_for(_=None) -> None:
    """A setting the operator cannot SEE is one they cannot tell is wrong."""
    text = render_detail(
        todo(
            title="Renew the domain",
            due_in_hours=72,
            category="business",
            recur="yearly",
            remind_before=["3d", "1d"],
            space="homelab-space",
        ),
        NOW,
    )
    assert "Renew the domain" in text
    assert "business" in text
    assert "yearly" in text
    assert "3 days before" in text and "1 day before" in text
    assert "homelab-space" in text


def test_a_dated_todo_with_no_reminder_says_so(_=None) -> None:
    """Silence here reads as "a reminder is set" — the state that produces "why didn't
    it tell me"."""
    assert "no reminder set" in render_detail(todo(due_in_hours=48), NOW)


def test_an_inbox_todo_is_not_nagged_about_a_missing_reminder(_=None) -> None:
    """There is no date to remind about, so the line would be noise on every capture."""
    assert "no reminder" not in render_detail(todo(), NOW)
    assert "no date yet" in render_detail(todo(), NOW)


def test_the_detail_card_escapes_notes_too(_=None) -> None:
    assert "&lt;script&gt;" in render_detail(todo(notes="<script>x</script>"), NOW)
