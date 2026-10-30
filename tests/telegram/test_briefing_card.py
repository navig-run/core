"""The /briefing card: a monitor, and a refresh that rewrites the same message.

The old handler interpolated a `lines` list of escaped HTML and sent it as text;
a press on its Refresh button ran the same handler again, which posted a SECOND
message.  A card that cannot stay put is a worse answer than the text it
replaced, so both halves are checked here: the card is rich with its actions
inside the bubble, and a card opened from a button edits the message that was
pressed.
"""

from __future__ import annotations

import pytest


class _RichChannel:
    """Records what the handler sends, at whichever fidelity it used."""

    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.plain: list[dict] = []
        self.edits: list[dict] = []

    async def send_rich_message(self, chat_id, **kw):
        self.rich.append({"chat_id": chat_id, **kw})
        return {"message_id": 11}

    async def send_message(self, chat_id, text, **kw):
        self.plain.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": 12}


def _labels(rows) -> list[str]:
    return [button["text"] for row in rows or () for button in row]


@pytest.mark.asyncio
async def test_the_briefing_is_one_card_with_its_actions_inside_the_bubble(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    # One unreadable measurement must not cost the whole card: the vault probe is
    # a best-effort read and the rest still has to arrive.
    monkeypatch.setattr(
        "navig.vault.get_vault",
        lambda: (_ for _ in ()).throw(RuntimeError("vault unreadable")),
    )

    await channel._handle_briefing(chat_id=1, user_id=2, metadata={})

    assert recorder.rich, "the briefing must go out as a rich card"
    assert not recorder.plain, "the classic text is only a fallback"
    html = recorder.rich[0]["html"]
    assert "<h1>📋 System Briefing</h1>" in html
    # The verdict is the last prose line, and it names what needs attention or
    # says nothing does.
    assert "Needs attention:" in html or "Everything answers" in html
    # Actions ride in the bubble — the card kit's own button rows, styled, not a
    # grey keyboard bolted underneath.
    assert "<tg-button-row" in html
    labels = _labels(recorder.rich[0]["keyboard"])
    assert "🔄 Refresh" in labels
    assert "🖥️ Meters" in labels
    # The fallback is the plain-text projection: same words, none of the tags a
    # client without rich messages would print literally.
    fallback = recorder.rich[0]["fallback_html"]
    assert "System Briefing" in fallback
    assert "<h1>" not in fallback and "<tg-button-row" not in fallback


@pytest.mark.asyncio
async def test_a_refresh_rewrites_the_message_instead_of_posting_another(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.telegram import card_transport

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    seen: list[tuple[int, int]] = []

    async def _edit_card(chan, chat_id, message_id, card, *, keyboard=None):
        seen.append((chat_id, message_id))
        assert "<h1>📋 System Briefing</h1>" in card.rich_html()
        return True

    monkeypatch.setattr(card_transport, "edit_card", _edit_card)

    await channel.renderScreen(chat_id=1, screen_name="briefing", message_id=77, user_id=2)

    assert seen == [(1, 77)]
    assert not recorder.rich and not recorder.plain


@pytest.mark.asyncio
async def test_typing_slash_briefing_still_posts_a_card(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    await channel._handle_briefing(chat_id=1, user_id=2, metadata={})

    assert len(recorder.rich) == 1
    assert recorder.rich[0]["chat_id"] == 1
