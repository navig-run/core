"""`/agenda` — appointments by day, apart from the to-do list, with working buttons."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from navig.core import i18n
from navig.pim import reminders, service
from navig.pim.clock import LOCAL
from navig.store.board import BoardStore
from navig.telegram import agenda_actions, todo_actions

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)


class _Runtime:
    def __init__(self) -> None:
        self.rows: dict[int, tuple] = {}

    def create_reminder(self, user_id, chat_id, message, remind_at, **_):  # noqa: ANN001
        rid = max(self.rows, default=0) + 1
        self.rows[rid] = (user_id, chat_id, message, remind_at)
        return rid

    def cancel_reminder(self, reminder_id, user_id):  # noqa: ANN001
        return self.rows.pop(reminder_id, None) is not None


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        return {"ok": True}


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> tuple[BoardStore, _Runtime]:
    store = BoardStore(tmp_path / "board.db")
    runtime = _Runtime()
    monkeypatch.setattr(service, "_store", lambda: store)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(reminders, "_runtime_store", lambda: runtime)
    monkeypatch.setattr(reminders, "_extension_is_off", lambda: False)
    monkeypatch.setattr(reminders, "operator_delivery", lambda: (7, 7))
    monkeypatch.setattr(todo_actions, "extension_is_off", lambda: False)
    return store, runtime


def _callbacks(keyboard: dict[str, Any]) -> list[str]:
    return [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]


def test_the_agenda_lists_appointments_by_day_and_never_tasks(env) -> None:
    service.create_task(title="Buy paper", due_at=NOW + timedelta(days=1))
    appt = service.create_appointment(
        "Visio Sarah", NOW + timedelta(days=2), location="Zoom", with_whom="Sarah"
    )
    text, keyboard = agenda_actions.build_view(NOW)
    assert "Visio Sarah" in text and "Buy paper" not in text
    assert "📍 Zoom" in text
    # Who matters once you are deciding about that appointment: the detail sheet
    # carries it, the row does not — two tails per row read as a wall of emoji.
    detail, _keyboard = agenda_actions.build_detail(appt["id"], NOW)  # type: ignore[misc]
    assert "👤" in detail and "Sarah" in detail
    assert f"ag:o:{appt['id']}" in _callbacks(keyboard)
    assert all(len(c.encode()) <= 64 for c in _callbacks(keyboard))


def test_a_proposal_is_offered_and_accepting_it_schedules(env) -> None:
    _, runtime = env
    prop = service.create_appointment("Coffee", NOW + timedelta(days=3), origin="ai")
    text, keyboard = agenda_actions.build_view(NOW)
    assert "Coffee" in text
    assert f"ag:ok:{prop['id']}" in _callbacks(keyboard)
    assert runtime.rows == {}

    channel = _Channel()
    toast = asyncio.run(agenda_actions.handle_callback(channel, f"ag:ok:{prop['id']}", 7, 5, 7))
    assert toast
    assert len(runtime.rows) == 3
    assert channel.calls[-1][0] == "editMessageText"


def test_dismiss_deletes_and_is_never_proposed_again(env) -> None:
    store, _ = env
    prop = service.create_appointment("Coffee", NOW + timedelta(days=3), origin="ai", origin_ref="tg:1:2")
    asyncio.run(agenda_actions.handle_callback(_Channel(), f"ag:no:{prop['id']}", 7, 5, 7))
    assert store.get_todo(prop["id"]) is None
    assert service.create_appointment("Coffee", NOW + timedelta(days=3), origin="ai", origin_ref="tg:1:2") is None


def test_a_lead_toggles_on_the_appointment(env) -> None:
    _, runtime = env
    appt = service.create_appointment("Dentist", NOW + timedelta(days=5), leads=["1d"])
    asyncio.run(agenda_actions.handle_callback(_Channel(), f"ag:r:{appt['id']}:3d", 7, 5, 7))
    assert service.get(appt["id"])["remind_before"] == ["1d", "3d"]
    assert len(runtime.rows) == 3


def test_cancel_removes_it_and_its_pings(env) -> None:
    store, runtime = env
    appt = service.create_appointment("Dentist", NOW + timedelta(days=5))
    asyncio.run(agenda_actions.handle_callback(_Channel(), f"ag:x:{appt['id']}", 7, 5, 7))
    assert store.get_todo(appt["id"]) is None
    assert runtime.rows == {}


def test_a_gone_appointment_answers_honestly(env) -> None:
    toast = asyncio.run(agenda_actions.handle_callback(_Channel(), "ag:o:nope", 7, 5, 7))
    assert toast


#: Tag names are markup, not prose: the card renders <pre>, <table> and <code>, so
#: scanning them would turn this guard into an HTML-element allowlist instead of a
#: check on what the operator actually reads.
_TAG = re.compile(r"<[^>]+>")
#: Latin left over from markup and entities, never from an untranslated string.
_BOILERPLATE = frozenset({"b", "i", "code", "lt", "gt", "amp", "nbsp"})


def test_the_russian_agenda_has_no_english(env, monkeypatch) -> None:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    try:
        appt = service.create_appointment("Стоматолог", NOW + timedelta(days=2), location="Клиника")
        # Both projections are probed: the classic body is what a chat without rich
        # messages receives, so markup only the rich render escapes must fail here too.
        for text, keyboard in (
            agenda_actions.build_view(NOW),
            agenda_actions.build_view_rich(NOW),
            agenda_actions.build_detail(appt["id"], NOW),
            agenda_actions.build_detail_rich(appt["id"], NOW),
        ):
            labels = " ".join(b["text"] for row in keyboard["inline_keyboard"] for b in row)
            english = set(re.findall(r"[A-Za-z]+", f"{_TAG.sub(' ', text)} {labels}")) - _BOILERPLATE
            assert not english, f"{english}\n{text}\n{labels}"
    finally:
        i18n.shared().reset()
