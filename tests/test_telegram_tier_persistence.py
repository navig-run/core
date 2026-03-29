from __future__ import annotations

from pathlib import Path

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_sessions import SessionManager


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
