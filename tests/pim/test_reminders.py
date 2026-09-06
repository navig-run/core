"""Reminder scheduling, and the ghost ping it exists to prevent.

A reminder row knows nothing about todos — it is a message, a chat and a time. So every
change to a todo's date, and every completion or deletion, has to explicitly cancel the
rows scheduled for the OLD state. Miss that and the operator is reminded, by name, about
a task they finished yesterday, with no way to stop it short of editing a database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from navig.pim import reminders
from navig.pim.clock import to_utc_iso
from navig.store.board import BoardStore

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 4, 15, 0, tzinfo=TZ)


class _FakeRuntime:
    """Stands in for RuntimeStore. Records instead of writing."""

    def __init__(self) -> None:
        self.created: list[tuple[int, int, str, datetime]] = []
        self.cancelled: list[tuple[int, int]] = []
        self.next_id = 100
        self.cancel_returns = True

    def create_reminder(self, user_id: int, chat_id: int, message: str, remind_at: datetime) -> int:
        self.created.append((user_id, chat_id, message, remind_at))
        self.next_id += 1
        return self.next_id

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        self.cancelled.append((reminder_id, user_id))
        return self.cancel_returns


@pytest.fixture
def runtime(monkeypatch) -> _FakeRuntime:
    fake = _FakeRuntime()
    monkeypatch.setattr(reminders, "_runtime_store", lambda: fake)
    return fake


@pytest.fixture
def store(tmp_path: Path) -> BoardStore:
    return BoardStore(tmp_path / "board.db")


def _due(hours: float) -> str:
    return to_utc_iso(NOW + timedelta(hours=hours))


# ── which leads actually fire ────────────────────────────────────────────────

def test_no_due_date_schedules_nothing(store: BoardStore, runtime: _FakeRuntime) -> None:
    """There is nothing to count back from. An inbox item is not a scheduling failure."""
    todo = store.create_todo("Someday", remind_before=["1d"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 0
    assert runtime.created == []


def test_the_due_moment_itself_always_fires(store: BoardStore, runtime: _FakeRuntime) -> None:
    todo = store.create_todo("Dentist", due_at=_due(48))
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 1
    assert "due now" in runtime.created[0][2]


def test_each_lead_gets_its_own_reminder_in_fire_order(store: BoardStore, runtime: _FakeRuntime) -> None:
    """`board_card.reminder_id` is a single INTEGER, which is why the link table exists."""
    todo = store.create_todo("Renew the domain", due_at=_due(24 * 10), remind_before=["1w", "1d"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 3

    moments = [c[3] for c in runtime.created]
    assert moments == sorted(moments), "reminders must be created soonest-first"
    assert len(store.todo_reminder_ids(todo["id"])) == 3


def test_a_lead_whose_moment_has_PASSED_is_skipped_not_fired_now(store: BoardStore, runtime: _FakeRuntime) -> None:
    """"3 days before" on a task due tomorrow means there is no 3-day warning.

    Firing it immediately is worse than skipping: the operator gets a "3 days before"
    ping about something due tomorrow, which teaches them the lead times are noise.
    """
    todo = store.create_todo("Dentist", due_at=_due(24), remind_before=["3d", "1h"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 2

    texts = [c[2] for c in runtime.created]
    assert not any("3 days before" in t for t in texts)
    assert any("1 hour before" in t for t in texts)


def test_a_due_date_entirely_in_the_past_schedules_nothing(store: BoardStore, runtime: _FakeRuntime) -> None:
    todo = store.create_todo("Overdue thing", due_at=_due(-48), remind_before=["1h"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 0


def test_an_unparseable_lead_is_ignored_and_the_rest_still_schedule(store: BoardStore, runtime: _FakeRuntime) -> None:
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["soon", "1h"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 2


# ── the ghost ping ───────────────────────────────────────────────────────────

def test_rescheduling_CANCELS_the_previous_set(store: BoardStore, runtime: _FakeRuntime) -> None:
    """The headline. Moving a date must not leave the old reminders alive."""
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW)
    first_ids = store.todo_reminder_ids(todo["id"])
    assert len(first_ids) == 2

    moved = store.update_todo(todo["id"], {"due_at": _due(96)})
    reminders.reschedule(store, moved, user_id=1, chat_id=2, now=NOW)

    assert {c[0] for c in runtime.cancelled} == set(first_ids)
    assert set(store.todo_reminder_ids(todo["id"])).isdisjoint(first_ids)


def test_completing_a_todo_leaves_nothing_scheduled(store: BoardStore, runtime: _FakeRuntime) -> None:
    """Being reminded about something you finished is the worst version of this bug:
    it is both wrong and impossible to act on."""
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW)

    done = store.complete_todo(todo["id"])
    assert reminders.reschedule(store, done, user_id=1, chat_id=2, now=NOW) == 0
    assert store.todo_reminder_ids(todo["id"]) == []
    assert runtime.cancelled, "the previously scheduled rows must be cancelled"


def test_cancel_for_reports_how_many_it_actually_cancelled(store: BoardStore, runtime: _FakeRuntime) -> None:
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW)

    assert reminders.cancel_for(store, todo["id"], user_id=1) == 2
    assert reminders.cancel_for(store, todo["id"], user_id=1) == 0, "idempotent"


def test_one_reminder_that_refuses_to_cancel_does_not_strand_the_others(
    store: BoardStore, runtime: _FakeRuntime, monkeypatch
) -> None:
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW)

    calls: list[int] = []

    def _explode(reminder_id: int, user_id: int) -> bool:
        calls.append(reminder_id)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return True

    monkeypatch.setattr(runtime, "cancel_reminder", _explode)
    assert reminders.cancel_for(store, todo["id"], user_id=1) == 1
    assert len(calls) == 2, "the second row must still be attempted"


def test_cancel_first_schedule_second(store: BoardStore, runtime: _FakeRuntime, monkeypatch) -> None:
    """Order is the safety property.

    Cancelling first means a crash in the middle leaves NOTHING scheduled — a missed
    ping. Scheduling first would leave both sets alive, which is the duplicate
    notification that gets a bot muted at the OS.
    """
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW)

    order: list[str] = []
    monkeypatch.setattr(
        runtime, "cancel_reminder", lambda *_a, **_k: (order.append("cancel"), True)[1]
    )
    monkeypatch.setattr(
        runtime,
        "create_reminder",
        lambda *_a, **_k: (order.append("create"), 999)[1],
    )
    reminders.reschedule(store, store.get_todo(todo["id"]), user_id=1, chat_id=2, now=NOW)

    assert order, "nothing ran"
    assert order.index("cancel") < order.index("create")


def test_an_unavailable_runtime_store_does_not_break_the_action(
    store: BoardStore, monkeypatch
) -> None:
    """A PIM action must still complete when reminders are down.

    It returns 0 and logs — the task is still captured, which is the part the operator
    cannot redo. Raising here would make "the reminder table is locked" look like
    "your task was not saved".
    """
    monkeypatch.setattr(reminders, "_runtime_store", lambda: None)
    todo = store.create_todo("Dentist", due_at=_due(48), remind_before=["1h"])
    assert reminders.reschedule(store, todo, user_id=1, chat_id=2, now=NOW) == 0


# ── what the ping says ───────────────────────────────────────────────────────

def test_the_message_names_the_task_AND_when_it_is_due(store: BoardStore) -> None:
    """"Dentist" alone makes you open the app to find out if that means now."""
    todo = store.create_todo("Dentist", due_at=_due(48))
    text = reminders.reminder_text(todo, "1d", NOW + timedelta(hours=24), NOW)
    assert "Dentist" in text
    assert "1 day before" in text
    assert "Sep" in text, f"the due date must be in the message: {text!r}"


def test_planned_fires_is_pure(store: BoardStore) -> None:
    """No store, no clock — so the scheduling POLICY is testable on its own."""
    fires = reminders.planned_fires(_due(48), ["1d", "1h"], NOW)
    assert [lead for lead, _ in fires] == ["1d", "1h", ""]
    assert reminders.planned_fires(None, ["1d"], NOW) == []


def test_planned_fires_returns_leads_soonest_first(store: BoardStore) -> None:
    fires = reminders.planned_fires(_due(24 * 10), ["1d", "1w"], NOW)
    assert [lead for lead, _ in fires] == ["1w", "1d", ""]
