from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session

    def get_or_create_session(self, chat_id: int, user_id: int, is_group: bool = False):
        return self._session


async def test_edit_message_includes_inline_keyboard() -> None:
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    channel._api_call = AsyncMock(return_value={"ok": True})

    keyboard = [[{"text": "Run", "callback_data": "cb:1"}]]
    await channel.edit_message(123, 456, "hello", keyboard=keyboard)

    channel._api_call.assert_awaited_once()
    method, payload = channel._api_call.await_args.args
    assert method == "editMessageText"
    assert payload["chat_id"] == 123
    assert payload["message_id"] == 456
    assert payload["reply_markup"] == {"inline_keyboard": keyboard}


async def test_send_response_strips_search_tags_before_building_keyboard(
    monkeypatch,
) -> None:
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    channel._features = frozenset({"sessions"})
    channel._maybe_send_voice = AsyncMock(return_value=False)
    channel.send_message = AsyncMock(return_value={"message_id": 1})

    fake_session = SimpleNamespace(action_cards_enabled=True, voice_response_to_text="text")
    monkeypatch.setattr(tg, "get_session_manager", lambda: _FakeSessionManager(fake_session))

    builder = MagicMock()
    builder.build.return_value = [[{"text": "Card", "callback_data": "cb:1"}]]
    channel._kb_builder = builder

    raw = (
        "Let me search for the latest CNN news for you.\n\n"
        "<searchqualityreflection>internal</searchqualityreflection>\n\n"
        "<searchqualityscore>4</searchqualityscore>\n\n"
        "<search>latest CNN news today</search>\n\n"
        "Based on the latest from CNN:"
    )
    await channel._send_response(123, raw, "latest cnn news", user_id=99, is_group=False)

    sent_text = channel.send_message.await_args.args[1]
    sent_kwargs = channel.send_message.await_args.kwargs
    assert "searchqualityreflection" not in sent_text
    assert "searchqualityscore" not in sent_text
    assert "<search>" not in sent_text
    # Responses now use Markdown V1 formatting (with plain-text fallback on error)
    assert sent_kwargs.get("parse_mode") == "Markdown"

    built_text = builder.build.call_args.kwargs["ai_response"]
    assert "searchqualityreflection" not in built_text
    assert "searchqualityscore" not in built_text
    assert "<search>" not in built_text


async def test_handle_reason_edits_placeholder_with_generated_keyboard(
    monkeypatch,
) -> None:
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    async def _on_message(**_kwargs):
        return (
            "Let me search for the latest CNN news for you.\n\n"
            "<searchqualityreflection>internal</searchqualityreflection>\n\n"
            "<searchqualityscore>4</searchqualityscore>\n\n"
            "<search>latest CNN news today</search>\n\n"
            "Based on the latest from CNN:"
        )

    channel = TelegramChannel(bot_token="123:FAKE", on_message=_on_message)
    channel._features = frozenset({"sessions"})
    channel._keep_typing = AsyncMock()
    channel.send_message = AsyncMock(return_value={"message_id": 321})
    channel.edit_message = AsyncMock(return_value={"ok": True})
    channel._record_assistant_msg = MagicMock()
    channel._is_debug_mode = MagicMock(return_value=False)

    fake_session = SimpleNamespace(action_cards_enabled=True, voice_response_to_text="text")
    monkeypatch.setattr(tg, "get_session_manager", lambda: _FakeSessionManager(fake_session))

    builder = MagicMock()
    builder.build.return_value = [
        [{"text": "📰 Fetch CNN headlines", "callback_data": "card_exec:abc"}]
    ]
    channel._kb_builder = builder

    await channel._handle_reason(
        text="/big can you tell me latest ccn news ?",
        chat_id=123,
        user_id=77,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
    )

    channel.edit_message.assert_awaited_once()
    args = channel.edit_message.await_args.args
    kwargs = channel.edit_message.await_args.kwargs
    assert args[0] == 123
    assert args[1] == 321
    assert "searchqualityreflection" not in args[2]
    assert "searchqualityscore" not in args[2]
    assert "<search>" not in args[2]
    assert kwargs["keyboard"] == [
        [{"text": "📰 Fetch CNN headlines", "callback_data": "card_exec:abc"}]
    ]


# ---------------------------------------------------------------------------
# Reasoning card navigator tests
# ---------------------------------------------------------------------------


def test_split_into_cards_basic():
    """Short text returns a single card."""
    from navig.gateway.channels.telegram_navigator import split_into_cards

    text = "Hello world.\n\nThis is a second paragraph."
    cards = split_into_cards(text, max_chars=4000)
    assert len(cards) == 1


def test_split_into_cards_long_paragraph():
    """Long text results in multiple cards, each within max_chars."""
    from navig.gateway.channels.telegram_navigator import split_into_cards

    paragraph = "Word " * 1000  # ~5000 chars
    cards = split_into_cards(paragraph, max_chars=2000)
    assert len(cards) >= 2
    for card in cards:
        assert len(card) <= 2000


def test_card_nav_keyboard_last_card_has_accept():
    """The last card's keyboard must include an Accept button."""
    import time

    from navig.gateway.channels.telegram_navigator import (
        CardSession,
        build_nav_keyboard,
    )

    session = CardSession(
        cards=["Card 1 text", "Card 2 text", "Card 3 text"],
        current=2,
        chat_id=100,
        user_id=1,
        message_id=None,
        topic="test topic",
        session_key="nav:abc123",
        created_at=time.time(),
    )
    keyboard = build_nav_keyboard(session, idx=2)  # last card (0-based)
    flat_buttons = [btn["text"] for row in keyboard for btn in row]
    assert any("Accept" in t for t in flat_buttons), (
        f"Accept button missing from last card keyboard: {flat_buttons}"
    )
    assert any("Refine" in t for t in flat_buttons), (
        f"Refine button missing from last card keyboard: {flat_buttons}"
    )


class TestMdToHtml:
    """Unit tests for TelegramChannel._md_to_html()."""

    def setup_method(self):
        from navig.gateway.channels.telegram import TelegramChannel

        self.convert = TelegramChannel._md_to_html

    def test_bold(self):
        assert self.convert("**hello world**") == "<b>hello world</b>"

    def test_italic(self):
        assert self.convert("*emphasis*") == "<i>emphasis</i>"

    def test_bold_not_confused_with_italic(self):
        result = self.convert("**bold** and *italic*")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result

    def test_heading(self):
        result = self.convert("# Website Analysis")
        assert result == "<b>Website Analysis</b>"

    def test_bullet_star(self):
        result = self.convert("* Portfolio section")
        assert result == "• Portfolio section"

    def test_bullet_dash(self):
        result = self.convert("- About page")
        assert result == "• About page"

    def test_sub_bullet_plus(self):
        result = self.convert("+ nested item")
        assert result == "  ◦ nested item"

    def test_html_escape(self):
        result = self.convert("Score < 100 & latency > 200ms")
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result

    def test_full_llm_response(self):
        raw = (
            "**Website Analysis:**\n"
            "* **Website:** Cybesis\n"
            "* **What it does:** Digital studio\n"
            "+ Portfolio work\n"
            "+ About section\n"
        )
        result = self.convert(raw)
        assert "<b>Website Analysis:</b>" in result
        assert "• " in result
        assert "  ◦ " in result
        # No raw asterisks left as bullets
        assert result.count("* ") == 0

    def test_collapses_excess_blank_lines(self):
        result = self.convert("line1\n\n\n\n\nline2")
        assert "\n\n\n" not in result
