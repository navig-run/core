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
            "id": rid,
            "user_id": user_id,
            "message": message,
            "remind_at": to_utc_iso(remind_at),
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


class _RichChannel(_Channel):
    """A channel that can edit a rich message, like the live Telegram one."""

    def __init__(self) -> None:
        super().__init__()
        self.edits: list[dict[str, Any]] = []

    async def edit_rich_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        html: str | None = None,
        fallback_html: str | None = None,
        keyboard: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "html": html,
                "fallback": fallback_html,
                "keyboard": keyboard,
            }
        )
        return {"message_id": message_id}


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
        "Visio Sarah",
        due_at=to_utc_iso(datetime(2026, 10, 26, 10, 30, tzinfo=TZ)),
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


def test_an_appointment_title_is_a_headline_and_its_detail(stores) -> None:
    """A ping's title can be a whole appointment — headline first, detail behind it.

    Rendered as one bold paragraph (the shipped behaviour before 2026-10-27) the
    card showed a wall of bold lines the operator could not scan.
    """
    _, runtime = stores
    runtime.create_reminder(
        7,
        7,
        "Стоматолог — за 1 день (Пн 26 окт · 14:00)\n"
        "Клиника: Ленина 14\nВрач: д-р Соколова\nВзять: карту и полис",
        NOW + timedelta(hours=3),
    )

    text, _keyboard = reminders_view.build(7, now=NOW)

    assert "<b>Стоматолог — за 1 день (Пн 26 окт · 14:00)</b>" in text
    assert "<b>Стоматолог — за 1 день (Пн 26 окт · 14:00)\n" not in text
    assert "Клиника: Ленина 14 · Врач: д-р Соколова · Взять: карту и полис" in text


def test_two_rows_for_one_appointment_are_one_line_and_one_button(stores) -> None:
    _, runtime = stores
    for _ in range(2):  # the same appointment queued twice
        runtime.create_reminder(7, 7, "Dentist", NOW + timedelta(hours=3))

    text, keyboard = reminders_view.build(7, now=NOW)

    assert text.count("Dentist") == 1
    assert len([b for b in _labels(keyboard) if "Dentist" in b["text"]]) == 1


def test_a_long_list_pages_in_place_instead_of_scrolling(stores) -> None:
    """Eight pings do not become one wall: six show, and ‹ / › move without resending."""
    _, runtime = stores
    for index in range(8):
        runtime.create_reminder(7, 7, f"job {index}", NOW + timedelta(hours=1 + index))

    first, keyboard = reminders_view.build(7, now=NOW)
    labels = [b["text"] for b in _labels(keyboard)]
    datas = [b["callback_data"] for b in _labels(keyboard)]

    assert "job 0" in first and "job 5" in first
    assert "job 6" not in first and "job 7" not in first
    assert "1/2" in labels and "›" in labels and "‹" not in labels
    assert "rv:p:1" in datas

    second, keyboard = reminders_view.reminder_list_card(7, now=NOW, page=1)
    text = second.classic_html()
    labels = [b["text"] for b in _labels(keyboard)]
    datas = [b["callback_data"] for b in _labels(keyboard)]

    assert "job 6" in text and "job 7" in text
    assert "job 0" not in text
    assert "2/2" in labels and "‹" in labels and "›" not in labels
    assert "rv:p:0" in datas


def test_a_page_turn_keeps_the_card_rich_and_the_tapped_message(stores) -> None:
    """The rich card used to be downgraded to plain HTML on the first tap."""
    _, runtime = stores
    for index in range(8):
        runtime.create_reminder(7, 7, f"job {index}", NOW + timedelta(hours=1 + index))
    channel = _RichChannel()

    asyncio.run(reminders_view.handle_callback(channel, "rv:p:1", 7, 99, 7))

    assert channel.calls == []  # nothing was sent as classic text
    (edit,) = channel.edits
    assert (edit["chat_id"], edit["message_id"]) == (7, 99)
    assert "job 6" in edit["html"]
    assert "<h2>" in edit["html"] or "<h3>" in edit["html"]  # day headings survive
    assert edit["fallback"]  # an old client still gets a body


def test_cancelling_on_the_second_page_stays_on_the_second_page(stores) -> None:
    _, runtime = stores
    ids = [
        runtime.create_reminder(7, 7, f"job {index}", NOW + timedelta(hours=1 + index))
        for index in range(8)
    ]
    channel = _RichChannel()

    asyncio.run(reminders_view.handle_callback(channel, f"rv:x:{ids[7]}:1", 7, 99, 7))

    assert runtime.cancelled == [(ids[7], 7)]
    (edit,) = channel.edits
    rows = edit["keyboard"]
    assert "2/2" in [b["text"] for row in rows for b in row]


def test_the_page_counter_is_not_a_button(stores) -> None:
    """Pressing "1/2" must not redraw anything — it is a label that happens to be tappable."""
    _, runtime = stores
    for index in range(8):
        runtime.create_reminder(7, 7, f"job {index}", NOW + timedelta(hours=1 + index))
    channel = _RichChannel()

    toast = asyncio.run(reminders_view.handle_callback(channel, "rv:noop", 7, 99, 7))

    assert toast == ""
    assert channel.edits == []
    assert channel.calls == []


def test_cancel_removes_a_one_off_and_rerenders(stores) -> None:
    _, runtime = stores
    rid = runtime.create_reminder(7, 7, "check logs", NOW + timedelta(hours=3))
    channel = _Channel()

    toast = asyncio.run(reminders_view.handle_callback(channel, f"rv:x:{rid}", 7, 99, 7))

    assert runtime.cancelled == [(rid, 7)]
    assert toast
    ((method, payload),) = channel.calls
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
    assert [b["callback_data"] for b in _labels(keyboard)] == ["rv:v", "td:v:all", "hm:home"]


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


def test_long_titles_keep_the_card_inside_the_edit_limit(stores) -> None:
    """Refresh and cancel re-render with editMessageText, which is not split: a body
    past 4096 units fails the edit and leaves the card stale."""
    _, runtime = stores
    for i in range(25):
        runtime.create_reminder(7, 7, f"{i:02d} " + "x & y " * 60, NOW + timedelta(hours=i + 1))

    text, _ = reminders_view.build(7, now=NOW)

    assert len(text) <= 4096, len(text)
    assert "x & y " * 60 not in text  # each title is cut in the body too
