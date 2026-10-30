"""The transport decision: a card leaves rich where the chat takes it, classic elsewhere.

Both projections of one :class:`~navig.telegram.card_doc.Card` are asserted here,
because the interesting failure is not "rich is broken" — it is a card that
reaches nobody because the fallback retried rich-only tags as classic HTML.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.telegram.card_doc import Bullets, Card, Heading
from navig.telegram.card_transport import rich_available, send_card


def _card() -> Card:
    # big=True: a briefing is the whole screen, so it keeps the display size.
    return Card("Доброе утро", glyph="☀️", meta="Пятница", big=True).add(
        Heading("Первые шаги"),
        Bullets(["Ответить на письма", "Собрать отчёт"]),
    )


class _ClassicOnly:
    """A chat adapter that predates rich messages: no ``send_rich_message`` at all."""

    def __init__(self) -> None:
        self.send_message = AsyncMock(return_value={"message_id": 11})


def _rigged_channel(**flags) -> AsyncMock:
    """A channel stub whose rich/classic sends both succeed."""
    channel = AsyncMock()
    channel.send_rich_message = AsyncMock(return_value={"message_id": 10})
    channel.send_message = AsyncMock(return_value={"message_id": 11})
    for name, value in flags.items():
        setattr(channel, name, value)
    return channel


@pytest.mark.asyncio
async def test_the_rich_body_travels_with_its_classic_fallback() -> None:
    channel = _rigged_channel()
    card = _card()

    sent = await send_card(channel, 777, card)

    assert sent == {"message_id": 10}
    kwargs = channel.send_rich_message.call_args.kwargs
    assert kwargs["html"] == card.rich_html()
    # Without this the channel's own retry sends <h1>/<ul> as classic HTML, which
    # Telegram rejects outright — the card would vanish instead of degrading.
    assert kwargs["fallback_html"] == card.classic_html()
    assert "<h1>" in kwargs["html"] and "<h1>" not in kwargs["fallback_html"]
    channel.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_channel_without_rich_gets_the_classic_projection() -> None:
    channel = _ClassicOnly()
    card = _card()

    sent = await send_card(channel, 777, card)

    assert sent == {"message_id": 11}
    text = channel.send_message.call_args.args[1]
    assert text == card.classic_html()
    assert channel.send_message.call_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_a_refused_rich_send_still_delivers_the_card() -> None:
    channel = _rigged_channel()
    channel.send_rich_message = AsyncMock(return_value=None)

    sent = await send_card(channel, 777, _card())

    assert sent == {"message_id": 11}
    channel.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_learned_refusal_is_not_asked_again() -> None:
    channel = _rigged_channel(_rich_supported=False)

    await send_card(channel, 777, _card())

    channel.send_rich_message.assert_not_awaited()
    channel.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_operator_switch_turns_rich_off(monkeypatch) -> None:
    channel = _rigged_channel()
    channel._rich_messages_enabled = lambda: False

    await send_card(channel, 777, _card())

    channel.send_rich_message.assert_not_awaited()


def test_rich_availability_reads_the_learned_flag() -> None:
    assert rich_available(_rigged_channel()) is True
    assert rich_available(_rigged_channel(_rich_supported=False)) is False
    assert rich_available(_ClassicOnly()) is False


@pytest.mark.asyncio
async def test_explicit_keyboard_rows_win_over_the_cards_own() -> None:
    channel = _rigged_channel()
    rows = [[{"text": "Открыть", "callback_data": "td:v:all"}]]

    await send_card(channel, 777, _card(), keyboard=rows)

    assert channel.send_rich_message.call_args.kwargs["keyboard"] == rows


@pytest.mark.asyncio
async def test_the_reply_and_business_contexts_are_forwarded() -> None:
    channel = _rigged_channel()

    await send_card(
        channel, 777, _card(), reply_to_message_id=42, business_connection_id="bc-1"
    )

    kwargs = channel.send_rich_message.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 42
    assert kwargs["business_connection_id"] == "bc-1"


@pytest.mark.asyncio
async def test_a_classic_only_channel_forwards_the_same_context() -> None:
    channel = _ClassicOnly()

    await send_card(
        channel, 777, _card(), reply_to_message_id=42, business_connection_id="bc-1"
    )

    kwargs = channel.send_message.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 42
    assert kwargs["business_connection_id"] == "bc-1"


@pytest.mark.asyncio
async def test_the_channels_own_fallback_honours_the_classic_body() -> None:
    """``_rich_fallback`` must send the caller's classic body, not rich tags."""
    channel = object.__new__(TelegramChannel)
    channel.send_message = AsyncMock(return_value={"message_id": 12})

    await channel._rich_fallback(
        777, None, "<h1>Заголовок</h1>",
        business_connection_id=None, reply_to_message_id=None, keyboard=None,
        fallback_html="<b>Заголовок</b>",
    )

    text = channel.send_message.call_args.args[1]
    assert text == "<b>Заголовок</b>"
    assert "<h1>" not in text
