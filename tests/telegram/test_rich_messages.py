"""Rich messages (sendRichMessage) — must degrade gracefully where unsupported.

sendRichMessage is brand-new; on a server/account that doesn't have it, the bot
must transparently fall back to a normal HTML send instead of dropping the reply.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_rich_message_falls_back_to_html_when_unsupported():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    channel._session = object()         # pass the "session up" guard
    channel._rich_supported = False     # learned: this server has no rich messages

    sent: dict = {}

    async def _send(chat_id, text, parse_mode="HTML", reply_to_message_id=None, keyboard=None):
        sent.update(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return {"message_id": 1}

    channel.send_message = _send
    out = await channel.send_rich_message(123, markdown="# Title\n\n- one\n- two")

    assert out == {"message_id": 1}
    assert sent.get("chat_id") == 123
    assert sent.get("parse_mode") == "HTML"
    assert sent.get("text")  # the markdown was rendered down to something non-empty


async def test_rich_message_draft_is_noop_when_unsupported():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    channel._session = object()
    channel._rich_supported = False
    assert await channel.send_rich_message_draft(123, 1, markdown="thinking…") is False


async def test_rich_message_empty_is_ignored():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    channel._session = object()
    assert await channel.send_rich_message(123) is None  # no markdown/html → no-op
