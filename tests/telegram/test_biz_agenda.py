"""An appointment the OWNER writes in a Business chat is proposed in their DM — only."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders as pim_reminders
from navig.pim import service
from navig.pim.clock import LOCAL
from navig.store.board import BoardStore
from navig.telegram import biz_agenda

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, data))
        return {"message_id": 1}


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BoardStore:
    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(service, "_store", lambda: st)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(pim_reminders, "operator_delivery", lambda: (7, 7))
    monkeypatch.setattr(pim_reminders, "_runtime_store", lambda: None)
    monkeypatch.setattr(biz_agenda, "enabled", lambda: True)
    return st


def _msg(text: str, mid: int = 10) -> dict[str, Any]:
    return {"text": text, "message_id": mid, "chat": {"id": 555, "first_name": "Sarah"}}


def test_the_owners_appointment_is_proposed_in_their_dm(store: BoardStore) -> None:
    ch = _Channel()
    sent = asyncio.run(biz_agenda.maybe_propose(ch, _msg("On se voit lundi 26 octobre à 10h30 au bureau"), owner_id=7))
    assert sent is True
    (appt,) = store.list_appointments()
    assert (appt["origin"], appt["with_whom"]) == ("ai", "Sarah")
    (method, data), = ch.calls
    assert data["chat_id"] == 7  # the DM — never the Business chat
    assert "business_connection_id" not in data
    datas = [b["callback_data"] for row in data["reply_markup"]["inline_keyboard"] for b in row]
    assert f"ag:ok:{appt['id']}" in datas and f"ag:no:{appt['id']}" in datas


def test_the_same_message_is_never_proposed_twice(store: BoardStore) -> None:
    ch = _Channel()
    msg = _msg("On se voit lundi 26 octobre à 10h30 au bureau")
    asyncio.run(biz_agenda.maybe_propose(ch, msg, owner_id=7))
    assert asyncio.run(biz_agenda.maybe_propose(ch, msg, owner_id=7)) is False
    assert len(store.list_appointments()) == 1


def test_off_by_default_and_silent_without_a_date(store: BoardStore, monkeypatch) -> None:
    ch = _Channel()
    assert asyncio.run(biz_agenda.maybe_propose(ch, _msg("thanks, talk soon ok"), owner_id=7)) is False
    monkeypatch.setattr(biz_agenda, "enabled", lambda: False)
    assert asyncio.run(
        biz_agenda.maybe_propose(ch, _msg("On se voit lundi 26 octobre à 10h30 au bureau"), owner_id=7)
    ) is False
    assert ch.calls == [] and store.list_appointments() == []


class _RefusingChannel(_Channel):
    async def _api_call(self, method: str, data: dict[str, Any]) -> Any:
        self.calls.append((method, data))
        return None  # how `_api_call` reports a refusal


def test_a_proposal_telegram_refused_stays_retryable(store: BoardStore) -> None:
    """The DM card never arrived: nothing may sit unseen, and the redelivery of the
    same message must propose it again rather than count as a duplicate."""
    msg = _msg("On se voit lundi 26 octobre à 10h30 au bureau")
    assert asyncio.run(biz_agenda.maybe_propose(_RefusingChannel(), msg, owner_id=7)) is False
    assert store.list_appointments() == []
    ch = _Channel()
    assert asyncio.run(biz_agenda.maybe_propose(ch, msg, owner_id=7)) is True
    assert len(store.list_appointments()) == 1 and len(ch.calls) == 1
