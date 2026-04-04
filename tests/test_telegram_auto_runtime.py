from unittest.mock import AsyncMock

import pytest


pytest.importorskip("navig.gateway.channels.telegram")

from navig.gateway.channels.telegram import TelegramChannel


class _DummySession:
    session_key = "test:session"

    def get_context_messages(self, limit=10):
        return []


class _DummySessionManager:
    def __init__(self):
        self._meta = {}

    def get_session(self, chat_id, user_id, is_group):
        return _DummySession()

    def add_user_message(
        self, chat_id, user_id, text, message_id, reply_to, is_group, username
    ):
        return _DummySession()

    def get_session_metadata(self, chat_id, user_id, key, default=None, is_group=False):
        return self._meta.get((chat_id, user_id, bool(is_group), key), default)

    def set_session_metadata(
        self,
        chat_id,
        user_id,
        key,
        value,
        is_group=False,
        username="",
    ):
        self._meta[(chat_id, user_id, bool(is_group), key)] = value
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

    def set_ai_state(self, user_id, chat_id, mode, persona=None, context=None):
        self._state = {
            "user_id": user_id,
            "chat_id": chat_id,
            "mode": mode,
            "persona": persona,
            "context": context or {},
        }


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
async def test_telegram_metadata_includes_last_detected_language(monkeypatch):
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=lambda *args, **kwargs: None)
    channel._bot_username = "mybot"
    channel._dispatch_by_mode = AsyncMock()
    channel._match_cli_command = lambda _text: None

    session_manager = _DummySessionManager()
    session_manager.set_session_metadata(42, 42, "last_detected_language", "fr")

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram.get_session_manager",
        lambda: session_manager,
    )

    update = {
        "message": {
            "message_id": 11,
            "text": "bonjour",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    kwargs = channel._dispatch_by_mode.await_args.kwargs
    assert kwargs["metadata"]["last_detected_language"] == "fr"


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


@pytest.mark.asyncio
async def test_slash_settings_and_voice_route_to_distinct_handlers(monkeypatch):
    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )
    channel._bot_username = "mybot"

    calls: list[str] = []

    async def _settings_hub(chat_id: int, user_id: int = 0, message_id: int | None = None):
        calls.append(f"settings:{chat_id}:{user_id}")

    async def _voice_menu(chat_id: int, user_id: int = 0, message_id: int | None = None):
        calls.append(f"voice:{chat_id}:{user_id}")

    channel._handle_settings_hub = _settings_hub
    channel._handle_voice_menu = _voice_menu

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)

    settings_update = {
        "message": {
            "message_id": 3,
            "text": "/settings",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }
    voice_update = {
        "message": {
            "message_id": 4,
            "text": "/voice",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(settings_update)
    await channel._process_update(voice_update)

    assert calls == ["settings:42:42", "voice:42:42"]


@pytest.mark.asyncio
async def test_auto_continuation_executes_second_turn_when_policy_enabled(monkeypatch):
    state_store = _AutoStateStore(
        {
            "user_id": 42,
            "chat_id": 42,
            "mode": "active",
            "persona": "assistant",
            "context": {
                "continuation": {
                    "enabled": True,
                    "paused": False,
                    "skip_next": False,
                    "cooldown_seconds": 0,
                    "max_turns": 2,
                    "turns_used": 0,
                    "last_continued_at": "",
                    "dry_run": False,
                    "space": "project",
                }
            },
        }
    )

    async def _on_message(channel, user_id, message, metadata):
        if metadata.get("auto_continuation_turn"):
            return "Executing next concrete step now."
        return "Should I continue with the next step?"

    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=_on_message)
    channel._bot_username = "mybot"
    channel._send_response = AsyncMock()
    channel._match_cli_command = lambda _text: None

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: state_store,
    )

    update = {
        "message": {
            "message_id": 3,
            "text": "hello",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    assert channel._send_response.await_count == 2
    calls = channel._send_response.await_args_list
    assert "Should I continue" in calls[0].args[1]
    assert "↪️ Executing next concrete step now." in calls[1].args[1]


@pytest.mark.asyncio
async def test_auto_continuation_skips_for_choice_prompt_and_records_classifier(monkeypatch):
    state_store = _AutoStateStore(
        {
            "user_id": 42,
            "chat_id": 42,
            "mode": "active",
            "persona": "assistant",
            "context": {
                "continuation": {
                    "enabled": True,
                    "paused": False,
                    "skip_next": False,
                    "cooldown_seconds": 0,
                    "max_turns": 2,
                    "turns_used": 0,
                    "last_continued_at": "",
                    "dry_run": False,
                }
            },
        }
    )

    async def _on_message(channel, user_id, message, metadata):
        if metadata.get("auto_continuation_turn"):
            return "This should not execute"
        return "Should I choose option A or B for the next rollout?"

    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=_on_message)
    channel._bot_username = "mybot"
    channel._send_response = AsyncMock()
    channel._match_cli_command = lambda _text: None

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: state_store,
    )

    update = {
        "message": {
            "message_id": 4,
            "text": "hello",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "user42"},
        }
    }

    await channel._process_update(update)

    assert channel._send_response.await_count == 1
    continuation = (state_store._state.get("context") or {}).get("continuation") or {}
    assert continuation.get("last_classifier_state") == "choice"
    assert continuation.get("last_classifier_reason") == "choice_signal"


@pytest.mark.asyncio
async def test_auto_continuation_busy_suppression_blocks_immediate_retry(monkeypatch):
    state_store = _AutoStateStore(
        {
            "user_id": 42,
            "chat_id": 42,
            "mode": "active",
            "persona": "assistant",
            "context": {
                "continuation": {
                    "enabled": True,
                    "paused": False,
                    "skip_next": False,
                    "cooldown_seconds": 0,
                    "max_turns": 3,
                    "turns_used": 0,
                    "last_continued_at": "",
                    "dry_run": False,
                }
            },
        }
    )

    async def _on_message(channel, user_id, message, metadata):
        if metadata.get("auto_continuation_turn"):
            return "This follow-up should be suppressed"
        if message == "first":
            return "Still working on this, one moment"
        return "Should I continue with the next step?"

    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=_on_message)
    channel._bot_username = "mybot"
    channel._send_response = AsyncMock()
    channel._match_cli_command = lambda _text: None

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: state_store,
    )

    await channel._process_update(
        {
            "message": {
                "message_id": 5,
                "text": "first",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "username": "user42"},
            }
        }
    )

    await channel._process_update(
        {
            "message": {
                "message_id": 6,
                "text": "second",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "username": "user42"},
            }
        }
    )

    assert channel._send_response.await_count == 2
    continuation = (state_store._state.get("context") or {}).get("continuation") or {}
    assert continuation.get("last_skip_reason", "").startswith("busy_suppressed:")
