"""Digest cards defer while a turn is in flight; DETAIL cards and the drain do not."""

from __future__ import annotations

import asyncio

import pytest

from navig.telegram import cards, turn_state
from navig.telegram.cards import CardButton, Context, Screen


@pytest.fixture(autouse=True)
def _fresh_notify_db():
    from navig.notify import store

    store._initialised = False
    yield
    store._initialised = False


def _capture(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def fake_api(channel, method, payload):
        sent.append(dict(payload))
        return {"message_id": len(sent)}

    monkeypatch.setattr(cards, "_api", fake_api)
    return sent


def _screen(kind: str = cards.SUMMARY) -> Screen:
    return Screen(
        title="NAVIG · 2 task(s) due",
        body="two overdue tasks",
        kind=kind,
        rows=((CardButton("Done", action="done"),),),
    )


def test_a_digest_card_is_deferred_while_the_turn_is_in_flight(monkeypatch):
    sent = _capture(monkeypatch)
    chat = 515151
    turn_state.mark_started(chat)
    try:
        result = asyncio.run(cards.send_card(chat, _screen(), Context("nav", "review")))
    finally:
        turn_state.mark_finished(chat)

    assert result == {}
    assert sent == [], "nothing may reach Telegram while the reply is being written"
    assert len(turn_state.take_deferred(chat)) == 1


def test_a_detail_card_is_not_deferred(monkeypatch):
    sent = _capture(monkeypatch)
    chat = 515152
    turn_state.mark_started(chat)
    try:
        result = asyncio.run(
            cards.send_card(chat, _screen(kind=cards.DETAIL), Context("nav", "review"))
        )
    finally:
        turn_state.mark_finished(chat)

    assert result
    assert len(sent) == 1
    assert turn_state.take_deferred(chat) == []


def test_a_deferred_digest_is_delivered_after_the_turn(monkeypatch):
    sent = _capture(monkeypatch)
    chat = 515153
    turn_state.mark_started(chat)
    asyncio.run(cards.send_card(chat, _screen(), Context("nav", "review")))
    turn_state.mark_finished(chat)

    pending = turn_state.take_deferred(chat)
    assert len(pending) == 1
    screen, context = turn_state.build_card(pending[0])
    asyncio.run(cards.send_card(chat, screen, context, force=True))

    assert len(sent) == 1
    assert "NAVIG" in sent[0]["text"]
