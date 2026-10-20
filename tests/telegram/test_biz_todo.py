"""A Business chat's shared to-do list — typed by both people, owned by one.

Driven through the real `biz_commands.dispatch`, because the bare-word rules are the
whole safety story: "todo" must work as typed, and ordinary conversation ("Задача
сложная", "done?") must never turn into a list action.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders as pim_reminders
from navig.pim import service
from navig.pim.clock import LOCAL
from navig.store.board import BoardStore
from navig.telegram import biz_commands, biz_todo

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)
OWNER, CONTACT, CHAT = 7, 999, 555


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next = 100

    async def _api_call(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, data))
        self._next += 1
        return {"message_id": self._next}

    def sent(self) -> list[str]:
        return [d["text"] for m, d in self.calls if m == "sendMessage"]


class _Runtime:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    def create_reminder(self, user_id, chat_id, message, remind_at, *, bcid=None):  # noqa: ANN001
        rid = max(self.rows, default=0) + 1
        self.rows[rid] = {"user_id": user_id, "chat_id": chat_id, "bcid": bcid, "at": remind_at}
        return rid

    def cancel_reminder(self, reminder_id, user_id):  # noqa: ANN001
        return self.rows.pop(reminder_id, None) is not None


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> tuple[BoardStore, _Runtime]:
    store = BoardStore(tmp_path / "board.db")
    runtime = _Runtime()
    monkeypatch.setattr(service, "_store", lambda: store)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(pim_reminders, "_runtime_store", lambda: runtime)
    monkeypatch.setattr(pim_reminders, "_extension_is_off", lambda: False)
    monkeypatch.setattr(pim_reminders, "operator_delivery", lambda: (OWNER, OWNER))
    monkeypatch.setattr(biz_todo, "_enabled", lambda: True)
    biz_todo._WRITES.clear()
    return store, runtime


def _say(channel: _Channel, text: str, *, sender: int = CONTACT, mid: int = 1) -> bool:
    msg = {
        "text": text,
        "message_id": mid,
        "business_connection_id": "BC",
        "chat": {"id": CHAT, "first_name": "Sarah"},
        "from": {"id": sender, "first_name": "Sarah" if sender == CONTACT else "Me"},
    }
    return asyncio.run(biz_commands.dispatch(channel, msg, is_owner=sender == OWNER, owner_id=OWNER))


def _items(store: BoardStore) -> list[dict[str, Any]]:
    return store.list_todos(list_id=f"tg:{CHAT}", include_done=True)


def test_both_people_add_and_the_card_shows_who(env) -> None:
    store, _ = env
    ch = _Channel()
    assert _say(ch, "todo bring the contract") is True
    assert _say(ch, "todo print the slides", sender=OWNER) is True
    titles = sorted(t["title"] for t in _items(store))
    assert titles == ["bring the contract", "print the slides"]
    card = ch.sent()[-1]
    assert "#1" in card and "#2" in card
    assert "added by Sarah" in card  # the contact's item says so; the owner's does not twice
    assert card.count("added by") == 1
    assert all(d.get("business_connection_id") == "BC" for m, d in ch.calls if m == "sendMessage")


def test_tick_and_untick_by_number(env) -> None:
    store, _ = env
    ch = _Channel()
    _say(ch, "todo a")
    _say(ch, "todo b")
    assert _say(ch, "done 1, 2") is True
    assert all(t["completed_at"] for t in _items(store))
    assert _say(ch, "undo 2") is True
    assert [t["list_seq"] for t in _items(store) if not t["completed_at"]] == [2]


def test_only_the_owner_deletes_or_clears(env) -> None:
    store, _ = env
    ch = _Channel()
    _say(ch, "todo keep me")
    assert _say(ch, "del 1") is True  # handled — with a refusal
    assert len(_items(store)) == 1
    assert "Only the owner" in ch.sent()[-1]
    _say(ch, "del 1", sender=OWNER)
    assert _items(store) == []


def test_clear_removes_ticked_and_clear_all_empties(env) -> None:
    store, _ = env
    ch = _Channel()
    for t in ("a", "b", "c"):
        _say(ch, f"todo {t}")
    _say(ch, "done 1")
    _say(ch, "clear", sender=OWNER)
    assert sorted(t["title"] for t in _items(store)) == ["b", "c"]
    _say(ch, "clear all", sender=OWNER)
    assert _items(store) == []


def test_numbers_stay_stable_when_items_are_removed(env) -> None:
    store, _ = env
    ch = _Channel()
    for t in ("a", "b", "c"):
        _say(ch, f"todo {t}")
    _say(ch, "del 2", sender=OWNER)
    _say(ch, "done 3")  # still means "c"
    done = [t["title"] for t in _items(store) if t["completed_at"]]
    assert done == ["c"]


def test_the_previous_card_is_deleted(env) -> None:
    ch = _Channel()
    _say(ch, "todo a")
    _say(ch, "todo b")
    deletes = [d for m, d in ch.calls if m == "deleteBusinessMessages"]
    assert deletes and deletes[-1]["business_connection_id"] == "BC"


def test_a_dated_item_pings_this_chat(env) -> None:
    _, runtime = env
    ch = _Channel()
    _say(ch, "todo call the bank tomorrow 10:00")
    (row,) = runtime.rows.values()
    assert (row["chat_id"], row["bcid"], row["user_id"]) == (CHAT, "BC", OWNER)


@pytest.mark.parametrize(
    "text",
    [
        "Задача сложная, но решаемая",  # an everyday word, not a command
        "todo is a weird word",
        "done?",
        "done with this",
        "list of things we need",
        "Список длинный",
        "clear skies today",
        "tâche difficile",
        "del Monte bananas",
    ],
)
def test_ordinary_conversation_is_left_alone(env, text: str) -> None:
    store, _ = env
    ch = _Channel()
    assert _say(ch, text) is False
    assert _items(store) == []
    assert ch.calls == []


def test_an_explicit_slash_reaches_the_everyday_words(env) -> None:
    store, _ = env
    ch = _Channel()
    assert _say(ch, "/задача купить бумагу") is True
    assert [t["title"] for t in _items(store)] == ["купить бумагу"]


def test_the_rate_limit_stops_a_flood(env, monkeypatch) -> None:
    store, _ = env
    monkeypatch.setattr(biz_todo, "RATE_LIMIT", 3)
    ch = _Channel()
    for i in range(5):
        _say(ch, f"todo item {i}")
    assert len(_items(store)) == 3


def test_the_russian_card_speaks_russian(env) -> None:
    ch = _Channel()
    _say(ch, "todo купить бумагу")
    assert "Задачи" in ch.sent()[-1]


def test_a_shared_item_is_a_planner_item(env) -> None:
    """NAVIG OS and the Mini App see it: it is in the Planner's lists."""
    ch = _Channel()
    _say(ch, "todo bring the contract")
    lists = service.lists()
    shared = next(lst for lst in lists if lst["id"] == f"tg:{CHAT}")
    assert (shared["peer_name"], shared["open"], shared["kind"]) == ("Sarah", 1, "biz_chat")


def test_moving_due_shifts_the_chat_ping(env) -> None:
    store, runtime = env
    ch = _Channel()
    _say(ch, "todo pay the deposit tomorrow 9:00")
    item = _items(store)[0]
    service.update(item["id"], {"due_at": NOW + timedelta(days=3)})
    (row,) = runtime.rows.values()
    assert row["bcid"] == "BC"
