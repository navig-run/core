"""Tests for Telegram Reaction Intelligence module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.gateway.channels.telegram_reactions import (
    TelegramReactionsMixin,
    _REACTION_DISPATCH,
    _REACTION_ACKS,
    _MIN_NEW_REACTION_COUNT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(**overrides):
    """Return a minimal fake TelegramChannel-like object with the mixin methods bound."""
    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)
    ch.send_message = AsyncMock(return_value={"message_id": 99})
    ch._is_user_authorized = MagicMock(return_value=True)
    ch._is_group_chat_id = MagicMock(return_value=False)
    ch._keep_typing = AsyncMock()
    ch._send_response = AsyncMock()
    ch.on_message = None
    for k, v in overrides.items():
        setattr(ch, k, v)
    return ch


def _reaction_update(emoji: str, chat_id: int = 1, msg_id: int = 42, user_id: int = 7) -> dict:
    return {
        "chat": {"id": chat_id, "type": "private"},
        "message_id": msg_id,
        "user": {"id": user_id},
        "new_reaction": [{"type": "emoji", "emoji": emoji}],
        "old_reaction": [],
    }


# ---------------------------------------------------------------------------
# Dispatch table integrity
# ---------------------------------------------------------------------------


def test_dispatch_table_uses_only_telegram_native_emojis():
    """⭐ and 🔁 must NOT appear — they're not in Telegram's reaction set."""
    forbidden = {"⭐", "🔁", "🌟", "↩️"}
    violation = set(_REACTION_DISPATCH.keys()) & forbidden
    assert not violation, f"Forbidden emoji in dispatch table: {violation}"


def test_every_dispatch_key_has_an_ack():
    """Every emoji in the dispatch table should have a corresponding ack entry."""
    missing = set(_REACTION_DISPATCH.keys()) - set(_REACTION_ACKS.keys())
    assert not missing, f"Missing acks for: {missing}"


def test_dispatch_table_completeness():
    """All expected reaction emojis must be present."""
    expected = {"👍", "👎", "🔥", "🤔", "💯"}
    assert expected == set(_REACTION_DISPATCH.keys())


# ---------------------------------------------------------------------------
# _on_message_reaction routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removal_is_ignored():
    """Empty new_reaction list → no handler called."""
    ch = _make_channel()
    update = {
        "chat": {"id": 1},
        "message_id": 10,
        "user": {"id": 7},
        "new_reaction": [],
        "old_reaction": [{"type": "emoji", "emoji": "👍"}],
    }
    with patch.object(
        TelegramReactionsMixin,
        "_get_reactions_config",
        return_value={"reactions_enabled": True},
    ):
        await TelegramReactionsMixin._on_message_reaction(ch, update)
    ch._api_call.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorised_user_skipped():
    """Unauthorised users must not trigger any handlers."""
    ch = _make_channel()
    ch._is_user_authorized = MagicMock(return_value=False)
    update = _reaction_update("👍")
    with patch.object(
        TelegramReactionsMixin,
        "_get_reactions_config",
        return_value={"reactions_enabled": True},
    ):
        await TelegramReactionsMixin._on_message_reaction(ch, update)
    ch._api_call.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_emoji_is_silently_skipped():
    """An emoji not in the dispatch table should NOT raise or call any handler."""
    ch = _make_channel()
    update = _reaction_update("😎")  # not in table
    with patch.object(
        TelegramReactionsMixin,
        "_get_reactions_config",
        return_value={"reactions_enabled": True},
    ):
        with patch.object(
            TelegramReactionsMixin, "_safe_set_reaction", new=AsyncMock()
        ) as _ack:
            await TelegramReactionsMixin._on_message_reaction(ch, update)
    _ack.assert_not_called()
    ch.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_reactions_disabled_in_config():
    """When reactions_enabled=False nothing should happen."""
    ch = _make_channel()
    with patch.object(
        TelegramReactionsMixin,
        "_get_reactions_config",
        return_value={"reactions_enabled": False},
    ):
        with patch.object(
            TelegramReactionsMixin, "_reaction_positive_feedback", new=AsyncMock()
        ) as _pos:
            await TelegramReactionsMixin._on_message_reaction(ch, _reaction_update("👍"))
    _pos.assert_not_called()


@pytest.mark.asyncio
async def test_thumbs_up_calls_positive_feedback():
    """👍 should invoke _reaction_positive_feedback."""
    ch = _make_channel()
    # Set handler directly on the instance so getattr(self, name) returns our mock
    mock_handler = AsyncMock()
    ch._reaction_positive_feedback = mock_handler
    await TelegramReactionsMixin._on_message_reaction(ch, _reaction_update("👍"))
    mock_handler.assert_awaited_once()
    _, kwargs = mock_handler.call_args
    assert kwargs["emoji"] == "👍"


@pytest.mark.asyncio
async def test_fire_calls_bookmark_wiki():
    """🔥 should invoke _reaction_bookmark_wiki."""
    ch = _make_channel()
    mock_handler = AsyncMock()
    ch._reaction_bookmark_wiki = mock_handler
    await TelegramReactionsMixin._on_message_reaction(ch, _reaction_update("🔥"))
    mock_handler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Ring buffer helpers
# ---------------------------------------------------------------------------


def test_lookup_helpers_return_none_on_miss():
    """Ring buffer lookups return None when no data is recorded."""
    ch = _make_channel()
    # get_session_manager is lazily imported inside the method from telegram_sessions
    with patch(
        "navig.gateway.channels.telegram_sessions.get_session_manager"
    ) as mock_sm:
        mock_sm.return_value.get_query_for_bot_reply.return_value = None
        result = TelegramReactionsMixin._lookup_query_for_reply(ch, 1, 999)
    assert result is None


def test_record_bot_reply_delegates_to_session_manager():
    """_record_bot_reply should call sm.record_bot_reply."""
    ch = _make_channel()
    # get_session_manager is lazily imported inside the method from telegram_sessions
    with patch(
        "navig.gateway.channels.telegram_sessions.get_session_manager"
    ) as mock_sm:
        sm_instance = MagicMock()
        mock_sm.return_value = sm_instance
        TelegramReactionsMixin._record_bot_reply(ch, 1, 42, "query", "reply")
    sm_instance.record_bot_reply.assert_called_once_with(1, 42, "query", "reply")


# ---------------------------------------------------------------------------
# _safe_set_reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_set_reaction_swallows_errors():
    """_safe_set_reaction must not raise when _api_call fails."""
    ch = _make_channel()
    ch._api_call = AsyncMock(side_effect=RuntimeError("network error"))
    # Should not raise
    await TelegramReactionsMixin._safe_set_reaction(ch, 1, 42, "🫡")


@pytest.mark.asyncio
async def test_safe_set_reaction_skips_empty_emoji():
    """Empty emoji produces no API call."""
    ch = _make_channel()
    await TelegramReactionsMixin._safe_set_reaction(ch, 1, 42, "")
    ch._api_call.assert_not_called()
