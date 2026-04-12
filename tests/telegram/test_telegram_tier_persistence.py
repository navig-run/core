from __future__ import annotations

from pathlib import Path

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_sessions import SessionManager

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("is_group", [False, True])
def test_session_metadata_roundtrip(tmp_path: Path, is_group: bool):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    chat_id = -100123 if is_group else 42
    user_id = 42

    sm.set_session_metadata(
        chat_id,
        user_id,
        "model_tier_pref",
        "big",
        is_group=is_group,
    )

    reloaded = SessionManager(storage_dir=storage)
    value = reloaded.get_session_metadata(
        chat_id,
        user_id,
        "model_tier_pref",
        default="",
        is_group=is_group,
    )
    assert value == "big"


def test_channel_tier_pref_reads_from_persisted_session(tmp_path: Path, monkeypatch):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr("navig.gateway.channels.telegram.get_session_manager", lambda: sm)

    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    chat_id = 42
    user_id = 42

    channel._set_user_tier_pref(chat_id, user_id, "small")

    # Simulate a process restart by clearing in-memory cache.
    channel._user_model_prefs.clear()

    assert channel._get_user_tier_pref(chat_id, user_id) == "small"


def test_noai_is_one_shot_and_does_not_overwrite_persistent_pref(tmp_path: Path, monkeypatch):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr("navig.gateway.channels.telegram.get_session_manager", lambda: sm)

    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    chat_id = 42
    user_id = 42

    channel._set_user_tier_pref(chat_id, user_id, "big")
    channel._set_one_shot_noai(user_id)

    assert channel._get_user_tier_pref(chat_id, user_id) == "noai"

    # Simulate one-shot consumption in _process_update.
    channel._user_model_prefs.pop(user_id, None)

    # Should restore from persisted preference.
    assert channel._get_user_tier_pref(chat_id, user_id) == "big"


async def test_noai_is_not_consumed_by_slash_commands(tmp_path: Path, monkeypatch):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr("navig.gateway.channels.telegram.get_session_manager", lambda: sm)

    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    async def _noop_handle_providers(chat_id: int, user_id: int = 0, message_id=None):
        return None

    channel._handle_providers = _noop_handle_providers
    channel._set_one_shot_noai(42)

    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "tester"},
            "text": "/provider",
            "message_id": 100,
        }
    }

    await channel._process_update(update)

    assert channel._get_user_tier_pref(42, 42) == "noai"


async def test_noai_non_command_text_shows_guidance_instead_of_cli(tmp_path: Path, monkeypatch):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr("navig.gateway.channels.telegram.get_session_manager", lambda: sm)

    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    captured: list[str] = []

    async def _fake_send_message(chat_id: int, text: str, **kwargs):
        captured.append(text)
        return {"ok": True}

    async def _never_cli(chat_id: int, user_id: int, metadata: dict, navig_cmd: str):
        raise AssertionError("CLI should not be called for plain chat text in no-AI mode")

    channel.send_message = _fake_send_message
    channel._handle_cli_command = _never_cli
    channel._set_one_shot_noai(42)

    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "tester"},
            "text": "hello navig",
            "message_id": 101,
        }
    }

    await channel._process_update(update)

    assert any("No-AI mode expects a command" in msg for msg in captured)


async def test_tier_command_with_bot_mention_uses_dynamic_registry(tmp_path: Path, monkeypatch):
    storage = tmp_path / "sessions"
    sm = SessionManager(storage_dir=storage)

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", True)
    monkeypatch.setattr("navig.gateway.channels.telegram.get_session_manager", lambda: sm)

    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    sent: list[str] = []

    async def _fake_send_message(chat_id: int, text: str, **kwargs):
        sent.append(text)
        return {"ok": True}

    channel.send_message = _fake_send_message

    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "tester"},
            "text": "/big@navig_test_bot",
            "message_id": 102,
        }
    }

    await channel._process_update(update)

    assert channel._get_user_tier_pref(42, 42) == "big"
    assert any("Big" in msg for msg in sent)
