"""Every accepted Telegram update reaches a recorded terminal state.

Incident 2026-10-22: nothing could answer "was the question processed and did the reply
go out?" — the conversation JSONL records what was stored, not what the channel did with
the update. `navig.telegram.receipts` records received → terminal at the single choke
point (``_run_update_with_turn``), so a crash leaves a queryable recovery trace.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.telegram import receipts


@pytest.fixture(autouse=True)
def _isolated_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_path", lambda: tmp_path / "telegram" / "receipts.db")


def _message_update(update_id: int = 11, chat_id: int = 900, message_id: int = 5) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id},
            "from": {"id": 7},
            "text": "hello",
        },
    }


def test_a_received_update_has_a_row_and_is_pending():
    key = receipts.record_received(_message_update())

    assert key == "11"
    row = receipts.received(key)
    assert row is not None and row["status"] == "received" and row["terminal_at"] is None
    assert [r["update_id"] for r in receipts.pending()] == ["11"]


def test_a_terminal_update_leaves_the_pending_trace():
    key = receipts.record_received(_message_update())
    receipts.record_terminal(key, "responded")

    row = receipts.received(key)
    assert row["status"] == "responded" and row["terminal_at"] is not None
    assert receipts.pending() == []


def test_a_failure_keeps_its_reason():
    key = receipts.record_received(_message_update())
    receipts.record_terminal(key, "failed", "TimeoutError: slow model")

    row = receipts.received(key)
    assert row["status"] == "failed"
    assert "TimeoutError" in row["detail"]


def test_record_received_is_idempotent_on_the_update_id():
    receipts.record_received(_message_update(update_id=12))
    receipts.record_received(_message_update(update_id=12))

    assert len(receipts.pending()) == 1


def test_callbacks_and_unknown_updates_are_not_recorded():
    assert receipts.record_received({"update_id": 13, "callback_query": {"id": "x"}}) == "13"
    # No message payload: the row exists but names no chat — the wrapper skips it anyway.
    receipts.record_terminal(None, "responded")  # must be a no-op, not a crash
    assert receipts.received("999") is None


class _FakeChannel:
    _update_chat_id = staticmethod(TelegramChannel._update_chat_id)

    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom
        self.drained: list[int] = []
        self.processed: list[dict] = []

    async def _process_update(self, update: dict) -> None:
        self.processed.append(update)
        if self.boom:
            raise RuntimeError("model down")

    async def _drain_deferred_cards(self, chat_id: int) -> None:
        self.drained.append(chat_id)


def test_the_wrapper_records_responded_and_drains_the_turn():
    channel = _FakeChannel()
    asyncio.run(TelegramChannel._run_update_with_turn(channel, _message_update(update_id=21)))

    row = receipts.received("21")
    assert row["status"] == "responded"
    assert channel.drained == [900]
    assert receipts.pending() == []


def test_the_wrapper_records_failed_and_still_clears_the_turn_state():
    from navig.telegram import turn_state

    channel = _FakeChannel(boom=True)
    with pytest.raises(RuntimeError):
        asyncio.run(TelegramChannel._run_update_with_turn(channel, _message_update(update_id=22)))

    row = receipts.received("22")
    assert row["status"] == "failed" and "model down" in row["detail"]
    assert turn_state.in_flight(900) is False
    assert channel.drained == [900]


def test_a_callback_update_is_not_a_turn_and_is_not_recorded():
    channel = _FakeChannel()
    update = {"update_id": 23, "callback_query": {"id": "abc"}}
    asyncio.run(TelegramChannel._run_update_with_turn(channel, update))

    assert channel.processed == [update]
    assert channel.drained == []
    assert receipts.received("23") is None

def test_pending_count_tracks_only_open_receipts():
    receipts.record_received(_message_update(update_id=31))
    receipts.record_received(_message_update(update_id=32))
    receipts.record_terminal("32", "responded")

    assert receipts.pending_count() == 1
    receipts.record_terminal("31", "responded")
    assert receipts.pending_count() == 0
