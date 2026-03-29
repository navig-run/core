from unittest.mock import AsyncMock

import pytest


pytest.importorskip("navig.gateway.channels.telegram")

from navig.gateway.channels.telegram import TelegramChannel


class _DummySession:
    session_key = "test:session"

    def get_context_messages(self, limit=10):
        return []


class _DummySessionManager:
    def get_session(self, chat_id, user_id, is_group):
        return _DummySession()

    def add_user_message(
        self, chat_id, user_id, text, message_id, reply_to, is_group, username
    ):
        return _DummySession()


class _DummyMentionGate:
    def __init__(self, should_respond):
        self._should_respond = should_respond

    def should_respond(
        self,
        text,
        user_id,
        is_group,
        is_reply_to_bot,
        session,
        reply_to_message_id,
    ):
        return self._should_respond

    def strip_mention(self, text):
        return text


class _AutoStateStore:
    def __init__(self, state):
        self._state = state

    def get_ai_state(self, user_id):
        return self._state


@pytest.mark.asyncio
async def test_group_message_bypasses_mention_gate_when_auto_active(monkeypatch):
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=lambda *args, **kwargs: None)
    channel._bot_username = "mybot"
    channel._dispatch_by_mode = AsyncMock()
    channel._match_cli_command = lambda _text: None

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.get_session_manager",
        lambda: _DummySessionManager(),
    )
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.get_mention_gate",
        lambda username: _DummyMentionGate(should_respond=False),
    )
    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: _AutoStateStore(
            {
                "user_id": 42,
                "chat_id": -1001,
                "mode": "active",
                "persona": "teacher",
            }
        ),
    )

    update = {
        "message": {
            "message_id": 1,
            "text": "hello team",
            "chat": {"id": -1001, "type": "group"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    channel._dispatch_by_mode.assert_awaited_once()
    kwargs = channel._dispatch_by_mode.await_args.kwargs
    assert kwargs["metadata"]["auto_reply_active"] is True
    assert kwargs["metadata"]["auto_reply_persona"] == "teacher"


@pytest.mark.asyncio
async def test_group_message_still_blocked_when_auto_inactive(monkeypatch):
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=lambda *args, **kwargs: None)
    channel._bot_username = "mybot"
    channel._dispatch_by_mode = AsyncMock()
    channel._match_cli_command = lambda _text: None

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.get_session_manager",
        lambda: _DummySessionManager(),
    )
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.get_mention_gate",
        lambda username: _DummyMentionGate(should_respond=False),
    )
    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: _AutoStateStore(
            {
                "user_id": 42,
                "chat_id": -1001,
                "mode": "inactive",
                "persona": "teacher",
            }
        ),
    )

    update = {
        "message": {
            "message_id": 1,
            "text": "hello team",
            "chat": {"id": -1001, "type": "group"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    channel._dispatch_by_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_slash_status_routes_to_status_handler_not_models(monkeypatch):
    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )
    channel._bot_username = "mybot"
    channel._handle_status = AsyncMock()
    channel._handle_models_command = AsyncMock()

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)

    update = {
        "message": {
            "message_id": 2,
            "text": "/status",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    channel._handle_status.assert_awaited_once_with(42, 42)
    channel._handle_models_command.assert_not_awaited()
