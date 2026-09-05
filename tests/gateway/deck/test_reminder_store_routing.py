"""Deck /schedule/reminders routes must use RuntimeStore, not the deprecated bot_store.

The firing poller (TelegramChannel reminder loop) reads RuntimeStore, and RuntimeStore
migrates the legacy bot_data.db exactly ONCE (then renames it .migrated and never re-reads
it). So a reminder written via get_bot_store() AFTER that migration lands in a fresh
bot_data.db the poller never sees — the deck's "create reminder" silently never fired.
These routes now go through RuntimeStore. Under the old code the create/cancel/list tests
below fail (the fake RuntimeStore is never touched).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import schedule  # noqa: E402


class _FakeReq:
    def __init__(self, body=None, query=None, match_info=None):
        self._body = body or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


class _FakeRuntimeStore:
    def __init__(self):
        self.created: list = []
        self.cancelled: list = []

    def create_reminder(self, user_id, chat_id, message, remind_at):
        self.created.append((user_id, chat_id, message, remind_at))
        return 77

    def get_user_reminders(self, user_id):
        return [{
            "id": 77, "user_id": user_id, "chat_id": 20, "message": "hi",
            "remind_at": "2099-01-01T00:00:00Z", "created_at": "2026-07-23T00:00:00Z",
            "completed": 0,
        }]

    def cancel_reminder(self, rid, user_id):
        self.cancelled.append((rid, user_id))
        return True


@pytest.fixture
def runtime(monkeypatch):
    fake = _FakeRuntimeStore()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake)
    return fake


def _data(resp):
    return json.loads(resp.body)["data"]


async def test_create_writes_to_runtime_store(runtime):
    resp = await schedule.handle_deck_reminders_create(
        _FakeReq({"message": "call mom", "in_minutes": 30, "user_id": "10", "chat_id": "20"})
    )
    assert resp.status == 201
    # The reminder landed in RuntimeStore — the store the firing poller actually reads.
    assert len(runtime.created) == 1
    uid, cid, msg, _ = runtime.created[0]
    assert (uid, cid, msg) == (10, 20, "call mom")


async def test_create_returns_reminder_row_shape(runtime):
    resp = await schedule.handle_deck_reminders_create(
        _FakeReq({"message": "x", "in_minutes": 5, "user_id": "1", "chat_id": "2"})
    )
    data = _data(resp)
    assert {"id", "user_id", "chat_id", "message", "remind_at", "created_at"} <= set(data)
    assert data["id"] == 77 and data["completed"] is False


async def test_list_reads_from_runtime_store(runtime):
    resp = await schedule.handle_deck_reminders_list(_FakeReq(query={"user_id": "10"}))
    assert resp.status == 200
    data = _data(resp)
    assert data["count"] == 1
    assert data["reminders"][0]["id"] == 77  # the fake RuntimeStore's row, not bot_store


async def test_cancel_uses_runtime_store(runtime):
    resp = await schedule.handle_deck_reminder_cancel(
        _FakeReq(query={"user_id": "10"}, match_info={"reminder_id": "77"})
    )
    assert resp.status == 200
    assert runtime.cancelled == [(77, 10)]
