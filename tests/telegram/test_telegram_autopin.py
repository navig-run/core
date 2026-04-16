"""Tests for auto-pin briefing and session ring buffer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Session ring buffer
# ---------------------------------------------------------------------------


class TestSessionRingBuffer:
    """Unit tests for the new record_bot_reply / get_query_for_bot_reply / get_reply_text_for_msg methods."""

    def _make_manager(self):
        from navig.gateway.channels.telegram_sessions import SessionManager
        import tempfile
        from pathlib import Path

        tmpdir = Path(tempfile.mkdtemp())
        return SessionManager(storage_dir=tmpdir)

    def test_record_and_retrieve_query(self):
        sm = self._make_manager()
        sm.record_bot_reply(chat_id=1, msg_id=42, original_query="hello", reply_text="world")
        assert sm.get_query_for_bot_reply(1, 42) == "hello"

    def test_record_and_retrieve_reply_text(self):
        sm = self._make_manager()
        sm.record_bot_reply(1, 55, "query_text", "response_text")
        assert sm.get_reply_text_for_msg(1, 55) == "response_text"

    def test_miss_returns_none(self):
        sm = self._make_manager()
        assert sm.get_query_for_bot_reply(1, 999) is None
        assert sm.get_reply_text_for_msg(1, 999) is None

    def test_ring_is_per_chat(self):
        sm = self._make_manager()
        sm.record_bot_reply(chat_id=1, msg_id=1, original_query="chat1", reply_text="r1")
        sm.record_bot_reply(chat_id=2, msg_id=1, original_query="chat2", reply_text="r2")
        assert sm.get_query_for_bot_reply(1, 1) == "chat1"
        assert sm.get_query_for_bot_reply(2, 1) == "chat2"

    def test_ring_bounded_at_max(self):
        from navig.gateway.channels.telegram_sessions import _REPLY_RING_MAX

        sm = self._make_manager()
        # Fill beyond capacity
        for i in range(_REPLY_RING_MAX + 10):
            sm.record_bot_reply(1, i, f"q{i}", f"r{i}")

        ring = sm._reply_ring.get(1, {})
        assert len(ring) == _REPLY_RING_MAX

    def test_oldest_evicted_on_overflow(self):
        from navig.gateway.channels.telegram_sessions import _REPLY_RING_MAX

        sm = self._make_manager()
        # Add exactly MAX + 1 entries
        for i in range(_REPLY_RING_MAX + 1):
            sm.record_bot_reply(1, i, f"q{i}", f"r{i}")

        # msg_id=0 (first inserted) should be evicted
        assert sm.get_query_for_bot_reply(1, 0) is None
        # msg_id=_REPLY_RING_MAX (last inserted) should exist
        assert sm.get_query_for_bot_reply(1, _REPLY_RING_MAX) == f"q{_REPLY_RING_MAX}"

    def test_overwrite_existing_key(self):
        sm = self._make_manager()
        sm.record_bot_reply(1, 10, "first", "first_reply")
        sm.record_bot_reply(1, 10, "second", "second_reply")
        assert sm.get_query_for_bot_reply(1, 10) == "second"


# ---------------------------------------------------------------------------
# Auto-pin briefing in _handle_briefing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_pin_briefing_pins_in_group():
    """In a group with a valid send result, pinChatMessage must be called."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)  # pin succeeds

    with patch("navig.gateway.channels.telegram.TelegramChannel._is_group_chat_id", return_value=True):
        with patch("navig.config.get_config_manager") as mock_cfg:
            mock_cfg.return_value.get.return_value = {"auto_pin_briefings": True}
            with patch("navig.gateway.channels.telegram_sessions.get_session_manager") as mock_sm:
                mock_sm.return_value.get_session_metadata.return_value = None

                await TelegramCommandsMixin._auto_pin_briefing(ch, -100, 7, {"message_id": 42})

    # pinChatMessage must have been called as one of the _api_call invocations
    called_methods = [c.args[0] for c in ch._api_call.call_args_list]
    assert "pinChatMessage" in called_methods


@pytest.mark.asyncio
async def test_auto_pin_briefing_skipped_when_no_result():
    """When send_message returns None, no pin call should be made."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)

    # No result → should not call pinChatMessage
    await TelegramCommandsMixin._auto_pin_briefing(ch, -100, 7, None)
    ch._api_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pin_briefing_skipped_for_dm():
    """Auto-pin should not fire for private (DM) chats."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)

    # Patch the classmethod at the correct module path
    with patch("navig.gateway.channels.telegram.TelegramChannel._is_group_chat_id", return_value=False):
        await TelegramCommandsMixin._auto_pin_briefing(
            ch, 12345, 7, {"message_id": 3}
        )

    ch._api_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pin_briefing_stores_msg_id_in_session():
    """After pinning, the message_id should be persisted in session metadata."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)  # pin succeeds

    # Patch at the correct module path: TelegramChannel lives in telegram.py
    with patch("navig.gateway.channels.telegram.TelegramChannel._is_group_chat_id", return_value=True):
        with patch(
            "navig.config.get_config_manager"
        ) as mock_cfg:
            mock_cfg.return_value.get.return_value = {"auto_pin_briefings": True}
            with patch(
                "navig.gateway.channels.telegram_sessions.get_session_manager"
            ) as mock_sm:
                sm = MagicMock()
                sm.get_session_metadata.return_value = None
                mock_sm.return_value = sm

                await TelegramCommandsMixin._auto_pin_briefing(
                    ch, -100, 7, {"message_id": 88}
                )

    # Should store the new msg_id
    sm.set_session_metadata.assert_called_once_with(
        -100, 0, "pinned_briefing_msg_id", 88, is_group=True
    )


# ---------------------------------------------------------------------------
# /pin command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_cmd_in_dm_sends_error():
    """In a DM (non-group) chat, /pin should return an error message."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    ch = MagicMock()
    ch.send_message = AsyncMock()

    metadata = MagicMock()

    # Patch at the correct module path
    with patch("navig.gateway.channels.telegram.TelegramChannel._is_group_chat_id", return_value=False):
        await TelegramCommandsMixin._handle_pin_cmd(ch, chat_id=42, user_id=7, metadata=metadata)

    ch.send_message.assert_awaited_once()
    args, kwargs = ch.send_message.call_args
    # Should mention that pin only works in groups
    combined = " ".join(str(a) for a in args) + str(kwargs)
    assert "group" in combined.lower() or "pin" in combined.lower()
