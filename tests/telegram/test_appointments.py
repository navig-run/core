"""An appointment typed in chat is SAVED, with reminders, and the reply says so.

Regression: the bot answered "Rendez-vous noté" to an appointment and saved nothing.
These tests pin that the capture writes a real APPOINTMENT (its own kind, never a
todo in a "rendezvous" category) with 3d/1d/1h reminders, that a re-delivered update
is not saved twice, that the card's buttons are the `/agenda` callbacks (`ag:`), and
that a store failure never produces a "saved" reply.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from navig.core import i18n
from navig.pim import reminders, service
from navig.pim.clock import LOCAL
from navig.pim.reminders import planned_fires
from navig.store import board
from navig.store.board import BoardStore
from navig.telegram import agenda_actions, appointments, todo_actions

# The real local zone with its DST rules: 26 Oct is after Europe's clock change, and a
# fixed +02:00 here is exactly the bug that put this appointment an hour early.
TZ = LOCAL
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
TEXT = (
    "Ce rendez-vous a lieu le lundi 26 octobre 2026 à 10h30 en visioconférence "
    "(1) avec CRABOS SARAH."
)


class _Runtime:
    def __init__(self) -> None:
        self.created: list[tuple[int, int, str, datetime]] = []

    def create_reminder(self, user_id: int, chat_id: int, message: str, remind_at: datetime) -> int:
        self.created.append((user_id, chat_id, message, remind_at))
        return len(self.created)

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        return True


class _Channel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((method, payload))
        return {"ok": True}


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BoardStore:
    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(board, "get_board_store", lambda: st)
    monkeypatch.setattr(todo_actions, "_store", lambda: st)
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: False)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr(service, "_SINK", None)
    # Appointments ping the operator's DM; this chat IS the operator's.
    monkeypatch.setattr(reminders, "operator_delivery", lambda: (7, 7))
    monkeypatch.setattr(reminders, "_extension_is_off", lambda: False)
    return st


@pytest.fixture
def runtime(monkeypatch) -> _Runtime:
    fake = _Runtime()
    monkeypatch.setattr(reminders, "_runtime_store", lambda: fake)
    monkeypatch.setattr(reminders, "delivery_suppressed", lambda _rid: False, raising=False)
    return fake


def _capture(channel: _Channel, *, text: str = TEXT, message_id: int = 42, is_group: bool = False) -> bool:
    return asyncio.run(
        appointments.maybe_capture(
            channel, chat_id=7, user_id=7, message_id=message_id, text=text, is_group=is_group
        )
    )


def test_detect_returns_the_moment_and_a_title() -> None:
    hit = appointments.detect(TEXT, NOW)
    assert hit is not None
    when, title = hit
    assert when == datetime(2026, 10, 26, 10, 30, tzinfo=TZ)
    assert title == "Ce rendez-vous a lieu en visioconférence avec CRABOS SARAH"


def test_the_appointment_is_actually_saved_with_reminders(store: BoardStore, runtime: _Runtime) -> None:
    channel = _Channel()
    assert _capture(channel) is True

    assert store.list_todos() == []  # never in the to-do list
    appts = store.list_appointments()
    assert len(appts) == 1
    todo = appts[0]
    assert (todo["kind"], todo["category"], todo["origin"]) == ("appointment", "", "chat")
    assert list(todo["remind_before"]) == ["3d", "1d", "1h"]
    assert todo["notes"] == TEXT  # the operator's words are never only in a derived title

    # 3 days, 1 day, 1 hour before — and at the moment itself.
    assert len(planned_fires(todo["due_at"], list(todo["remind_before"]), NOW)) == 4
    assert [r[3] for r in runtime.created] == [
        datetime(2026, 10, 23, 10, 30, tzinfo=TZ),
        datetime(2026, 10, 25, 10, 30, tzinfo=TZ),
        datetime(2026, 10, 26, 9, 30, tzinfo=TZ),
        datetime(2026, 10, 26, 10, 30, tzinfo=TZ),
    ]

    (method, payload), = channel.sent
    assert method == "sendMessage"
    callbacks = [b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert callbacks and all(c.startswith("ag:") for c in callbacks)
    assert f"ag:x:{todo['id']}" in callbacks  # Undo
    assert {f"ag:r:{todo['id']}:{t}" for t in ("3d", "1d", "1h")} <= set(callbacks)


def test_a_redelivered_update_is_not_saved_twice(store: BoardStore, runtime: _Runtime) -> None:
    channel = _Channel()
    assert _capture(channel) is True
    assert _capture(channel) is True
    assert len(store.list_appointments()) == 1
    assert len(channel.sent) == 1


@pytest.mark.parametrize(
    "text",
    ["Записано: 124 кг", "on se voit lundi 26 octobre 2026 à 10h30 ?", "/todo something oct 26 10:30"],
)
def test_ordinary_messages_fall_through(store: BoardStore, runtime: _Runtime, text: str) -> None:
    channel = _Channel()
    assert _capture(channel, text=text) is False
    assert store.list_todos() == [] and store.list_appointments() == []
    assert channel.sent == []


def test_groups_are_left_alone(store: BoardStore, runtime: _Runtime) -> None:
    channel = _Channel()
    assert _capture(channel, is_group=True) is False
    assert store.list_appointments() == []


def test_a_store_failure_never_claims_saved(store: BoardStore, runtime: _Runtime, monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(store, "create_todo", boom)
    channel = _Channel()
    assert _capture(channel) is False  # falls through to the ordinary chat path
    assert channel.sent == []


def test_the_card_is_localized(store: BoardStore, runtime: _Runtime, monkeypatch) -> None:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    try:
        todo = store.create_todo(
            "Встреча с врачом",
            kind="appointment",
            due_at="2026-10-26T08:30:00Z",
            remind_before=["3d", "1d", "1h"],
        )
        body, keyboard = appointments.build_reply(todo, NOW)
        labels = " ".join(b["text"] for row in keyboard["inline_keyboard"] for b in row)
        english = {w for w in re.findall(r"[A-Za-z]+", f"{body} {labels}") if w not in ("b",)}
        assert not english, f"English left in the Russian card: {english}\n{body}\n{labels}"
    finally:
        i18n.shared().reset()


def test_the_detail_card_shows_an_active_one_hour_lead(store: BoardStore) -> None:
    """Tapping a 🔔 on the appointment card re-renders the DETAIL card. Its picker
    showed only 1w/3d/1d, so the 1h reminder became one you could not switch off."""
    todo = store.create_todo("Visio", due_at="2026-10-26T08:30:00Z", remind_before=["1h"])
    keyboard = todo_actions.build_detail_keyboard(todo)
    callbacks = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert f"td:r:{todo['id']}:1h" in callbacks


# ── firm vs. asked: a relative day alone is offered, not saved ──────────────


@pytest.mark.parametrize(
    ("text", "firm"),
    [
        (TEXT, True),  # a calendar date
        ("rdv chez le dentiste demain à 10h", True),  # an appointment word
        ("Встреча завтра в 15:00 с юристом", True),
        ("on se voit demain à 10h ok", False),  # small talk as often as a plan
        ("see you tomorrow at 10:30 then", False),
    ],
)
def test_classify_tells_a_plan_from_small_talk(text: str, firm: bool) -> None:
    hit = appointments.classify(text, NOW)
    assert hit is not None
    assert hit[2] is firm


@pytest.mark.parametrize(
    "text",
    ["Rappelle-moi demain à 10h d'appeler Sarah", "напомни мне завтра в 9:00 позвонить маме"],
)
def test_a_remind_me_message_is_left_to_remindme(text: str) -> None:
    """It asked for ONE ping at that moment, not an appointment with three leads."""
    assert appointments.classify(text, NOW) is None


def test_small_talk_is_offered_and_schedules_nothing_until_kept(
    store: BoardStore, runtime: _Runtime
) -> None:
    channel = _Channel()
    assert _capture(channel, text="on se voit demain à 10h ok") is True

    (todo,) = store.list_appointments()
    assert todo["origin"] == "ai"  # a proposal, rendered with save/no
    assert runtime.created == []  # no ping for a plan nobody agreed to

    (_method, payload), = channel.sent
    callbacks = [b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert callbacks == [f"ag:ok:{todo['id']}", f"ag:no:{todo['id']}"]

    # Tapping Save makes it a real appointment AND starts its reminders.
    toast = asyncio.run(agenda_actions.handle_callback(channel, f"ag:ok:{todo['id']}", 7, 99, 7))
    assert toast
    assert store.get_todo(todo["id"])["origin"] == "manual"
    # Due TOMORROW: the 3-day and 1-day leads are already past and are skipped, not
    # fired late — what remains is 1 hour before and the moment itself.
    assert [r[3].hour for r in runtime.created] == [9, 10]


@pytest.mark.parametrize(
    "text",
    [
        "Cancel my appointment on October 26 2026 at 10:30",
        "Move my appointment from October 26 2026 at 10:30 to October 27 2026 at 11:30",
        "Annule le rendez-vous du 26 octobre 2026 à 10h30",
        "Отмени встречу 26 октября 2026 в 10:30",
        "rdv le 26 octobre 2026 à 10h30 puis le 27 octobre 2026 à 11h30",
    ],
)
def test_a_change_or_two_moments_is_left_to_the_agent(text: str) -> None:
    """Capturing would add a todo named "Cancel …" or save the obsolete time."""
    assert appointments.classify(text, NOW) is None
