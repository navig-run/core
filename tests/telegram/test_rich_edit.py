"""Editing a card in place keeps its rich body (``editMessageText.rich_message``)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.gateway.channels.telegram import TelegramChannel


def _channel(*, rich: bool = True, api_returns=None) -> TelegramChannel:
    channel = object.__new__(TelegramChannel)
    channel._rich_supported = rich
    channel._rich_messages_enabled = lambda: rich  # type: ignore[method-assign]
    channel._api_call = AsyncMock(return_value=api_returns)  # type: ignore[method-assign]
    channel.edit_message = AsyncMock(return_value={"message_id": 7})  # type: ignore[method-assign]
    return channel


@pytest.mark.asyncio
async def test_a_rich_edit_sends_the_rich_body_not_its_projection():
    channel = _channel(api_returns={"message_id": 7})
    await channel.edit_rich_message(
        1,
        7,
        html="<h2>Tasks</h2><ul><li>one</li></ul>",
        fallback_html="<b>Tasks</b>\n• one",
        keyboard=[[{"text": "▶", "callback_data": "todo:next"}]],
    )

    method, payload = channel._api_call.call_args.args
    assert method == "editMessageText"
    assert payload["rich_message"] == {"html": "<h2>Tasks</h2><ul><li>one</li></ul>"}
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "▶", "callback_data": "todo:next"}]]}
    channel.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_that_cannot_render_rich_gets_the_classic_projection():
    channel = _channel(rich=False)
    await channel.edit_rich_message(
        1, 7, html="<h2>Tasks</h2>", fallback_html="<b>Tasks</b>", keyboard=[]
    )

    channel._api_call.assert_not_awaited()
    args, kwargs = channel.edit_message.call_args
    assert args == (1, 7, "<b>Tasks</b>")
    assert kwargs["keyboard"] == []


@pytest.mark.asyncio
async def test_a_refused_rich_edit_falls_back_instead_of_showing_tags():
    channel = _channel(api_returns=None)
    await channel.edit_rich_message(
        1, 7, html="<h2>Tasks</h2>", fallback_html="<b>Tasks</b>"
    )
    channel.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_rich_only_body_is_never_retried_as_classic_text():
    # `<h2>` would be rejected as an unknown entity: better no edit than a card
    # that renders its own markup.
    channel = _channel(api_returns=None)
    assert await channel.edit_rich_message(1, 7, html="<h2>Tasks</h2>") is None
    channel.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_markdown_without_a_fallback_edits_as_plain_text():
    channel = _channel(rich=False)
    await channel.edit_rich_message(1, 7, markdown="**Tasks**")
    args, kwargs = channel.edit_message.call_args
    assert args == (1, 7, "**Tasks**")
    assert kwargs["parse_mode"] == ""
