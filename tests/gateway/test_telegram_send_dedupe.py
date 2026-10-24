"""A long, identical outbound message is not re-sent to the same chat inside a window.

2026-10-22: the operator received 12 identical "☀️ Brief" messages inside 24 minutes from
a foreign test sender (pairs 8–50 s apart, singles ~5 min apart). The transport itself now
withholds an identical long body for five minutes — short replies and threaded answers are
exempt, because those repeat on purpose.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram import TelegramChannel


@pytest.fixture(autouse=True)
def _fresh_notify_db():
    from navig.notify import store

    store._initialised = False
    yield
    store._initialised = False


def _channel(monkeypatch) -> tuple[TelegramChannel, list[dict]]:
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[456])
    sent: list[dict] = []

    async def fake_api(method: str, payload: dict):
        sent.append(dict(payload))
        return {"message_id": len(sent), "chat": {"id": payload.get("chat_id")}}

    monkeypatch.setattr(channel, "_api_call", fake_api)
    return channel, sent


def _long(tag: str = "a") -> str:
    return ("Brief 2026-10-22 — " + tag + " ").ljust(300, ".")


def test_the_same_long_message_is_withheld_twice(monkeypatch):
    channel, sent = _channel(monkeypatch)

    first = asyncio.run(channel.send_message(700001, _long()))
    second = asyncio.run(channel.send_message(700001, _long()))

    assert first is not None
    assert second is None
    assert len(sent) == 1


def test_a_changed_long_message_is_a_new_delivery(monkeypatch):
    channel, sent = _channel(monkeypatch)

    asyncio.run(channel.send_message(700002, _long("a")))
    asyncio.run(channel.send_message(700002, _long("b")))

    assert len(sent) == 2


def test_a_short_message_may_repeat(monkeypatch):
    channel, sent = _channel(monkeypatch)

    asyncio.run(channel.send_message(700003, "OK"))
    asyncio.run(channel.send_message(700003, "OK"))

    assert len(sent) == 2


def test_an_answer_to_a_message_may_repeat(monkeypatch):
    channel, sent = _channel(monkeypatch)

    asyncio.run(channel.send_message(700004, _long(), reply_to_message_id=1))
    asyncio.run(channel.send_message(700004, _long(), reply_to_message_id=1))

    assert len(sent) == 2


def test_another_chat_is_not_affected(monkeypatch):
    channel, sent = _channel(monkeypatch)

    asyncio.run(channel.send_message(700005, _long()))
    asyncio.run(channel.send_message(700006, _long()))

    assert len(sent) == 2
