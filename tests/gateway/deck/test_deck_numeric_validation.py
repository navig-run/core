"""Malformed numeric fields in deck-route request bodies must be a clean 400,
not an uncaught ``ValueError`` that aiohttp turns into a raw 500.

Two routes parsed ``int()``/``float()`` on caller-supplied fields *outside* their
try/except, so ``{"user_id": "abc"}`` or ``{"limit": "abc"}`` crashed the request
to a 500. Their SIBLING routes in the same modules already guard the parse and
return 400 (``handle_deck_reminder_cancel``, ``handle_deck_trigger_create``) — this
brings the stragglers up to that standard. Under the old code every ``*_400`` test
here would ERROR (the handler raised) instead of asserting a 400 response.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import connectors, schedule  # noqa: E402


class _FakeReq:
    """Minimal aiohttp-request stand-in: an async json() body + a match_info dict."""

    def __init__(self, body: dict, match_info: dict | None = None):
        self._body = body
        self.match_info = match_info or {}

    async def json(self):
        return self._body


# --- schedule.handle_deck_reminders_create -------------------------------------

async def test_reminder_create_non_integer_user_id_is_400():
    # Valid in_minutes gets past the time parse; the bad user_id must 400, not crash.
    resp = await schedule.handle_deck_reminders_create(
        _FakeReq({"message": "hi", "in_minutes": 5, "user_id": "abc"})
    )
    assert resp.status == 400


async def test_reminder_create_non_integer_chat_id_is_400():
    resp = await schedule.handle_deck_reminders_create(
        _FakeReq({"message": "hi", "in_minutes": 5, "chat_id": "not-a-number"})
    )
    assert resp.status == 400


async def test_reminder_create_valid_ids_pass_the_guard(monkeypatch):
    """A well-formed user_id/chat_id must sail through to the store — proving the new
    guard doesn't reject the happy path."""

    class _Store:
        def __init__(self):
            self.calls = []

        def create_reminder(self, user_id, chat_id, message, remind_at):
            self.calls.append((user_id, chat_id))
            return 7  # RuntimeStore.create_reminder returns the new row id

    store = _Store()
    # Reminders route through RuntimeStore (the store the firing poller reads), not the
    # deprecated bot_store — see test_reminder_store_routing.py.
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)
    resp = await schedule.handle_deck_reminders_create(
        _FakeReq({"message": "hi", "in_minutes": 5, "user_id": "123", "chat_id": "456"})
    )
    assert resp.status == 201
    assert store.calls == [(123, 456)]  # parsed to ints, not left as strings


# --- connectors.handle_deck_connectors_search ----------------------------------

async def test_connector_search_non_integer_limit_is_400():
    resp = await connectors.handle_deck_connectors_search(
        _FakeReq({"query": "hello", "limit": "abc"}, match_info={"connector_id": "x"})
    )
    assert resp.status == 400


async def test_connector_search_valid_limit_passes_the_guard(monkeypatch):
    class _Conn:
        async def search(self, query, limit):
            assert limit == 5  # the parsed int reached the connector
            return []

    async def _fake_resolve(_cid):
        return _Conn(), None

    monkeypatch.setattr(connectors, "_resolve_connector", _fake_resolve)
    resp = await connectors.handle_deck_connectors_search(
        _FakeReq({"query": "hello", "limit": 5}, match_info={"connector_id": "x"})
    )
    assert resp.status == 200
