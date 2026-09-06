"""The `/todo` card: capture, the buttons, and the gate.

The renderers and the date grammar are tested in `tests/pim`; this is the wiring —
that a tap reaches the store, that the card is re-rendered afterwards, and that
switching the extension off actually stops it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from navig.pim.clock import from_utc_iso, to_local, to_utc_iso
from navig.store.board import BoardStore
from navig.telegram import todo_actions

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 4, 15, 0, tzinfo=TZ)  # a Friday afternoon


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict:
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}

    @property
    def last_text(self) -> str:
        return str(self.calls[-1][1].get("text", ""))


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BoardStore:
    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(todo_actions, "_store", lambda: st)
    monkeypatch.setattr(todo_actions, "local_now", lambda: NOW)
    # No reminder table in these tests — scheduling has its own suite, and reaching a
    # real RuntimeStore here would make every card test depend on it.
    monkeypatch.setattr("navig.pim.reminders._runtime_store", lambda: None)
    return st


async def tap(channel: _Channel, payload: str, user_id: int | None = 42) -> str:
    return await todo_actions.handle_callback(channel, payload, 100, 200, user_id)


# ── capture ──────────────────────────────────────────────────────────────────

def test_capture_pulls_the_date_off_the_line(store: BoardStore) -> None:
    todo = todo_actions.capture("Dentist tomorrow 10:30", now=NOW)
    assert todo is not None
    assert todo["title"] == "Dentist"
    due = to_local(from_utc_iso(todo["due_at"]))
    assert due is not None
    assert (due.day, due.hour, due.minute) == (5, 10, 30)


def test_capture_pulls_the_recurrence_off_FIRST(store: BoardStore) -> None:
    """Order matters. "every monday 9:30" has to lose "every monday" before the date
    parser sees it, or the suffix rule matches "monday 9:30" and the recurrence stays
    in the title."""
    todo = todo_actions.capture("Standup every monday 9:30", now=NOW)
    assert todo is not None
    assert todo["title"] == "Standup"
    assert todo["recur"] == "weekly"


def test_capture_with_no_date_lands_in_the_inbox(store: BoardStore) -> None:
    todo = todo_actions.capture("Call the accountant", now=NOW)
    assert todo is not None
    assert todo["due_at"] is None


def test_capture_of_an_empty_line_creates_nothing(store: BoardStore) -> None:
    """`/todo` alone opens the list; it must not create a task called ""."""
    assert todo_actions.capture("   ", now=NOW) is None
    assert store.list_todos() == []


# ── views ────────────────────────────────────────────────────────────────────

def test_the_default_view_shows_everything_open(store: BoardStore) -> None:
    store.create_todo("Loose end")
    store.create_todo("Dentist", due_at=to_utc_iso(NOW + timedelta(hours=3)))
    text, keyboard = todo_actions.build_view("all", now=NOW)

    assert "Dentist" in text and "Loose end" in text
    assert any("td:d:" in b["callback_data"] for row in keyboard["inline_keyboard"] for b in row)


def test_the_inbox_view_shows_only_undated_tasks(store: BoardStore) -> None:
    store.create_todo("Loose end")
    store.create_todo("Dentist", due_at=to_utc_iso(NOW + timedelta(hours=3)))
    text, _ = todo_actions.build_view("inbox", now=NOW)

    assert "Loose end" in text
    assert "Dentist" not in text


def test_the_today_view_includes_OVERDUE(store: BoardStore) -> None:
    """Something late is more "today" than anything else on the list.

    Hiding it because its date has passed is how a task disappears exactly when it
    matters most.
    """
    store.create_todo("Late", due_at=to_utc_iso(NOW - timedelta(days=1)))
    store.create_todo("Next week", due_at=to_utc_iso(NOW + timedelta(days=7)))
    text, _ = todo_actions.build_view("today", now=NOW)

    assert "Late" in text
    assert "Next week" not in text


def test_an_unknown_view_falls_back_to_the_list_rather_than_an_empty_card(store: BoardStore) -> None:
    store.create_todo("Something")
    text, _ = todo_actions.build_view("from-a-future-version", now=NOW)
    assert "Something" in text


# ── the keyboard's size limit ────────────────────────────────────────────────

def test_the_keyboard_is_capped_and_says_what_it_dropped(store: BoardStore) -> None:
    """Telegram rejects an over-large keyboard outright, so an unbounded list would
    fail to render exactly when it has the most in it."""
    for i in range(20):
        store.create_todo(f"Task {i}")
    _, keyboard = todo_actions.build_view("all", now=NOW)

    rows = keyboard["inline_keyboard"]
    assert len(rows) <= 11
    assert any("more" in b["text"] for row in rows for b in row)


def test_a_long_title_is_truncated_on_its_button_not_in_the_list(store: BoardStore) -> None:
    long_title = "Renew the domain registration before the auto-renew window closes"
    store.create_todo(long_title)
    text, keyboard = todo_actions.build_view("all", now=NOW)

    assert long_title in text, "the row itself must show the whole title"
    button = keyboard["inline_keyboard"][0][0]["text"]
    assert len(button) < len(long_title)


def test_category_buttons_carry_an_ORDINAL_not_the_name(store: BoardStore) -> None:
    """`callback_data` is capped at 64 bytes and a category is free-form text the
    operator invents — any length, any script."""
    store.create_todo("x", category="a-very-long-category-name-someone-could-plausibly-type-out")
    _, keyboard = todo_actions.build_view("cats", now=NOW)

    payloads = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    for payload in payloads:
        assert len(payload.encode()) <= 64, payload
    assert any(p.startswith("td:c:") for p in payloads)


def test_every_emitted_payload_fits_telegrams_limit(store: BoardStore) -> None:
    todo = store.create_todo("Dentist", due_at=to_utc_iso(NOW + timedelta(days=2)))
    for _, keyboard in (
        todo_actions.build_view("all", now=NOW),
        todo_actions.build_view("cats", now=NOW),
        todo_actions.build_detail(todo["id"], now=NOW),
    ):
        for row in keyboard["inline_keyboard"]:
            for button in row:
                assert len(button["callback_data"].encode()) <= 64, button


# ── the buttons ──────────────────────────────────────────────────────────────

async def test_ticking_a_task_completes_it_and_redraws_the_list(store: BoardStore) -> None:
    todo = store.create_todo("Post the letter")
    channel = _Channel()

    assert await tap(channel, f"td:d:{todo['id']}") == "Done ✓"
    assert store.get_todo(todo["id"])["completed_at"] is not None
    assert channel.calls[-1][0] == "editMessageText"
    assert "Post the letter" not in channel.last_text, "a done task leaves the list"


async def test_ticking_a_RECURRING_task_says_when_it_comes_back(store: BoardStore) -> None:
    """It stays on the list by design, so the toast has to explain why — otherwise
    the operator taps "done" and watches the row refuse to disappear."""
    todo = store.create_todo(
        "Water the plants", due_at=to_utc_iso(NOW + timedelta(hours=4)), recur="daily"
    )
    toast = await tap(_Channel(), f"td:d:{todo['id']}")

    assert toast.startswith("Done · next")
    assert store.get_todo(todo["id"])["completed_at"] is None


async def test_reopening_puts_it_back(store: BoardStore) -> None:
    todo = store.create_todo("Post the letter")
    await tap(_Channel(), f"td:d:{todo['id']}")
    assert await tap(_Channel(), f"td:u:{todo['id']}") == "Reopened"
    assert store.get_todo(todo["id"])["completed_at"] is None


async def test_a_quick_date_schedules_it(store: BoardStore) -> None:
    todo = store.create_todo("Dentist")
    toast = await tap(_Channel(), f"td:s:{todo['id']}:tmrw")

    assert "Sat 5 Sep" in toast
    due = to_local(from_utc_iso(store.get_todo(todo["id"])["due_at"]))
    assert due is not None and due.day == 5


async def test_the_weekend_button_never_means_today(store: BoardStore, monkeypatch) -> None:
    """Tapping "Weekend" ON a Saturday means the coming one, matching "next" everywhere
    else — a button that resolves to five minutes ago is not a scheduling option."""
    saturday = datetime(2026, 9, 5, 15, 0, tzinfo=TZ)
    monkeypatch.setattr(todo_actions, "local_now", lambda: saturday)
    todo = store.create_todo("Dentist")
    await tap(_Channel(), f"td:s:{todo['id']}:wknd")

    due = to_local(from_utc_iso(store.get_todo(todo["id"])["due_at"]))
    assert due is not None and due.day == 12, "a Saturday tap must mean NEXT Saturday"


async def test_clearing_a_date_sends_it_back_to_the_inbox(store: BoardStore) -> None:
    todo = store.create_todo("Dentist", due_at=to_utc_iso(NOW + timedelta(days=2)))
    assert await tap(_Channel(), f"td:s:{todo['id']}:clear") == "Back in the inbox"
    assert store.get_todo(todo["id"])["due_at"] is None


async def test_a_reminder_lead_toggles_both_ways(store: BoardStore) -> None:
    todo = store.create_todo("Dentist", due_at=to_utc_iso(NOW + timedelta(days=3)))

    assert "On:" in await tap(_Channel(), f"td:r:{todo['id']}:1d")
    assert store.get_todo(todo["id"])["remind_before"] == ["1d"]
    assert "Off:" in await tap(_Channel(), f"td:r:{todo['id']}:1d")
    assert store.get_todo(todo["id"])["remind_before"] == []


async def test_recurrence_toggles_off_when_tapped_again(store: BoardStore) -> None:
    todo = store.create_todo("Rent", due_at=to_utc_iso(NOW + timedelta(days=10)))
    assert await tap(_Channel(), f"td:p:{todo['id']}:monthly") == "Repeats monthly"
    assert await tap(_Channel(), f"td:p:{todo['id']}:off") == "Repeat off"
    assert store.get_todo(todo["id"])["recur"] is None


async def test_an_unknown_recurrence_is_refused_rather_than_stored(store: BoardStore) -> None:
    """A payload is remote input; a stored value `advance()` cannot read would make the
    task due forever and re-fire on every poll."""
    todo = store.create_todo("Rent")
    await tap(_Channel(), f"td:p:{todo['id']}:fortnightly")
    assert store.get_todo(todo["id"])["recur"] is None


async def test_deleting_removes_it(store: BoardStore) -> None:
    todo = store.create_todo("Mistake")
    assert await tap(_Channel(), f"td:x:{todo['id']}") == "Deleted"
    assert store.get_todo(todo["id"]) is None


# ── things that have gone away ───────────────────────────────────────────────

@pytest.mark.parametrize("payload", ["td:d:{}", "td:u:{}", "td:o:{}", "td:x:{}", "td:s:{}:tmrw"])
async def test_a_tap_on_a_task_that_no_longer_exists_says_so(store: BoardStore, payload: str) -> None:
    """A stale card is normal — the operator scrolls back to a message from Tuesday.
    Every one of these must answer, not raise into the router's generic failure."""
    assert await tap(_Channel(), payload.format("gone123")) == "That task is gone"


async def test_a_malformed_payload_is_inert(store: BoardStore) -> None:
    assert await tap(_Channel(), "td:from-a-future-version") == ""


async def test_an_exception_inside_the_handler_becomes_a_toast_not_a_raise(
    store: BoardStore, monkeypatch
) -> None:
    """A raise reaches the callback router, which answers generically and leaves the
    card showing state that no longer matches the database."""
    def _boom() -> Any:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(todo_actions, "_store", _boom)
    assert await tap(_Channel(), "td:v:all") == "Something went wrong"


# ── the gate ─────────────────────────────────────────────────────────────────

def test_the_banner_is_empty_while_the_extension_is_on(store: BoardStore, monkeypatch) -> None:
    """It is prepended unconditionally, so it has to be free when the feature is on."""
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: False)
    assert todo_actions.extension_banner() == ""


def test_the_banner_explains_that_TASKS_are_kept(store: BoardStore, monkeypatch) -> None:
    """Switching it off stops delivery, not scheduling. Left unexplained, "my reminder
    never came" reads as a broken bot rather than a switch the operator flipped."""
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: True)
    banner = todo_actions.extension_banner()
    assert "/extensions" in banner
    assert "still here" in banner


def test_the_banner_reaches_the_card(store: BoardStore, monkeypatch) -> None:
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: True)
    store.create_todo("Dentist")
    text, _ = todo_actions.build_view("all", now=NOW)
    assert "Todo extension is off" in text


def test_an_unreadable_gate_does_not_hide_the_list(store: BoardStore, monkeypatch) -> None:
    """Fail-open, like every other extension: a developer's omission must never
    silence the operator's own task list."""
    import navig.gateway.channels.telegram_extensions as tx

    def _raise(_id: str) -> bool:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(tx, "is_enabled", _raise)
    assert todo_actions.extension_is_off() is False
