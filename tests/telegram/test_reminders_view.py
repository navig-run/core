"""`/reminders` lists every upcoming ping by day with its exact time, named by task.

Before: raw English rows ("#11 — 2026-10-23 10:30 ⏰ Dentist — 3 days before (due …)")
with no idea which ping belonged to which task, and the `/todo` view switcher cut
every Russian label to its first letters. These pin the view, its buttons, and the
switcher's width.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from navig.core import i18n
from navig.pim import reminders
from navig.pim.clock import LOCAL, to_utc_iso
from navig.store.board import BoardStore
from navig.telegram import reminders_view, todo_actions

# The real local zone, DST rules included: the view shows times as the operator's
# clock reads them, and 26 Oct is after the European clock change.
TZ = LOCAL
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)


class _Runtime:
    """Stands in for RuntimeStore: a real-enough reminder queue."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.cancelled: list[tuple[int, int]] = []

    def create_reminder(self, user_id: int, chat_id: int, message: str, remind_at: datetime) -> int:
        rid = len(self.rows) + 1
        self.rows[rid] = {
            "id": rid, "user_id": user_id, "message": message, "remind_at": to_utc_iso(remind_at),
        }
        return rid

    def get_user_reminders(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in self.rows.values() if r["user_id"] == user_id]
        return sorted(rows, key=lambda r: r["remind_at"])[:limit]

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        row = self.rows.get(reminder_id)
        if row is None or row["user_id"] != user_id:
            return False
        del self.rows[reminder_id]
        self.cancelled.append((reminder_id, user_id))
        return True


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        return {"ok": True}


@pytest.fixture
def stores(tmp_path: Path, monkeypatch) -> tuple[BoardStore, _Runtime]:
    board = BoardStore(tmp_path / "board.db")
    runtime = _Runtime()
    monkeypatch.setattr(reminders_view, "_board", lambda: board)
    monkeypatch.setattr(reminders_view, "_runtime", lambda: runtime)
    monkeypatch.setattr(reminders, "_runtime_store", lambda: runtime)
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: False)
    return board, runtime


def _labels(keyboard: dict[str, Any]) -> list[dict[str, str]]:
    return [b for row in keyboard["inline_keyboard"] for b in row]


def test_every_ping_is_listed_by_day_with_its_task(stores) -> None:
    board, runtime = stores
    todo = board.create_todo(
        "Visio Sarah", due_at=to_utc_iso(datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
        remind_before=["3d", "1d", "1h"],
    )
    reminders.reschedule(board, todo, user_id=7, chat_id=7, now=NOW)
    runtime.create_reminder(7, 7, "check logs", NOW + timedelta(hours=3))

    text, keyboard = reminders_view.build(7, now=NOW)

    assert "· 5" in text  # four for the task + the one-off
    # Exact times, soonest first, under day headings.
    times = re.findall(r"<code>(\d\d:\d\d)</code>", text)
    assert times == ["15:00", "10:30", "10:30", "09:30", "10:30"]
    assert "Today" in text or "Сегодня" in text
    assert text.count("Visio Sarah") == 4
    assert "3 days before" in text  # the lead is named, not just the time

    buttons = _labels(keyboard)
    opens = [b for b in buttons if b["callback_data"] == f"td:o:{todo['id']}"]
    assert len(opens) == 1  # one button per TASK, not per ping
    assert any(b["callback_data"].startswith("rv:x:") for b in buttons)
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


def test_cancel_removes_a_one_off_and_rerenders(stores) -> None:
    _, runtime = stores
    rid = runtime.create_reminder(7, 7, "check logs", NOW + timedelta(hours=3))
    channel = _Channel()

    toast = asyncio.run(reminders_view.handle_callback(channel, f"rv:x:{rid}", 7, 99, 7))

    assert runtime.cancelled == [(rid, 7)]
    assert toast
    (method, payload), = channel.calls
    assert method == "editMessageText"
    assert "check logs" not in payload["text"]


def test_a_tasks_ping_is_not_cancelled_from_here(stores) -> None:
    """A todo's ping would come back on the next edit of its task — so the view
    sends you to the task instead of pretending to cancel it."""
    board, runtime = stores
    todo = board.create_todo(
        "Dentist", due_at=to_utc_iso(NOW + timedelta(days=2)), remind_before=["1d"]
    )
    reminders.reschedule(board, todo, user_id=7, chat_id=7, now=NOW)
    rid = next(iter(runtime.rows))
    toast = asyncio.run(reminders_view.handle_callback(_Channel(), f"rv:x:{rid}", 7, 99, 7))
    assert runtime.cancelled == []
    assert "task" in toast.lower()


def test_someone_elses_reminder_cannot_be_cancelled(stores) -> None:
    _, runtime = stores
    rid = runtime.create_reminder(8, 8, "not yours", NOW + timedelta(hours=1))
    asyncio.run(reminders_view.handle_callback(_Channel(), f"rv:x:{rid}", 7, 99, 7))
    assert rid in runtime.rows


def test_empty_state_says_how_to_add_one(stores) -> None:
    text, keyboard = reminders_view.build(7, now=NOW)
    assert "· 0" in text
    assert "/remindme" in text
    assert [b["callback_data"] for b in _labels(keyboard)] == ["rv:v", "td:v:all"]


def test_the_russian_card_has_no_english(stores, monkeypatch) -> None:
    board, runtime = stores
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    try:
        todo = board.create_todo(
            "Стоматолог", due_at=to_utc_iso(NOW + timedelta(days=4)), remind_before=["3d"]
        )
        reminders.reschedule(board, todo, user_id=7, chat_id=7, now=NOW)
        text, keyboard = reminders_view.build(7, now=NOW)
        labels = " ".join(b["text"] for b in _labels(keyboard))
        english = set(re.findall(r"[A-Za-z]+", f"{text} {labels}")) - {"b", "i", "code", "remindme"}
        assert not english, f"{english}\n{text}"
        # The ping that ARRIVES is localized too — it used to end "(due …)".
        assert "due" not in next(iter(runtime.rows.values()))["message"]
    finally:
        i18n.shared().reset()


def test_the_todo_switcher_is_two_rows_of_two(monkeypatch) -> None:
    """Four buttons in one row cut "Входящие" to "Вхо…". Two per row fit."""
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    try:
        keyboard = todo_actions.build_list_keyboard([], view="all")
    finally:
        i18n.shared().reset()
    rows = keyboard["inline_keyboard"]
    assert all(len(row) <= 2 for row in rows)
    datas = [b["callback_data"] for row in rows for b in row]
    assert datas == ["td:v:all", "td:v:inbox", "td:v:today", "td:v:cats", "ag:v", "rv:v", "td:add"]
    assert rows[0][0]["text"].startswith("•")  # the active view stays marked
