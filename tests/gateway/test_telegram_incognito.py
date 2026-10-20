"""`/incognito` on Telegram: messages are not stored in the session or the catalog.

The conversational agent's own memory gates are covered in
``tests/memory/test_memory_controls.py``; this pins the Telegram-side stores.
"""

from __future__ import annotations

import asyncio

from navig.gateway.channels.telegram_sessions import INCOGNITO_KEY, SessionManager


def _manager(tmp_path) -> SessionManager:
    return SessionManager(storage_dir=tmp_path / "sessions")


def test_incognito_chat_messages_are_not_recorded(tmp_path):
    sm = _manager(tmp_path)
    sm.add_user_message(chat_id=7, user_id=7, text="before")
    sm.set_session_metadata(7, 7, INCOGNITO_KEY, True)
    sm.add_user_message(chat_id=7, user_id=7, text="my secret plan")
    sm.add_assistant_message(chat_id=7, user_id=7, text="noted")
    contents = [m.content for m in sm.get_session(7, 7).messages]
    assert contents == ["before"]
    assert not any(
        "my secret plan" in p.read_text(encoding="utf-8")
        for p in (tmp_path / "sessions").glob("*.json")
    )


def test_leaving_incognito_records_again(tmp_path):
    sm = _manager(tmp_path)
    sm.set_session_metadata(7, 7, INCOGNITO_KEY, True)
    sm.set_session_metadata(7, 7, INCOGNITO_KEY, None)
    sm.add_user_message(chat_id=7, user_id=7, text="back")
    assert [m.content for m in sm.get_session(7, 7).messages] == ["back"]
    assert sm.is_incognito_chat(7) is False


def test_is_incognito_chat(tmp_path):
    sm = _manager(tmp_path)
    sm.set_session_metadata(7, 7, INCOGNITO_KEY, True)
    assert sm.is_incognito_chat(7) is True
    assert sm.is_incognito_chat(8) is False


def test_catalog_skips_an_incognito_chat(monkeypatch, tmp_path):
    from navig.gateway.channels import telegram_catalog_ingest as ingest

    sm = _manager(tmp_path)
    sm.set_session_metadata(7, 7, INCOGNITO_KEY, True)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_sessions.get_session_manager", lambda: sm
    )
    monkeypatch.setattr(ingest, "catalog_enabled", lambda: True)
    calls: list = []
    monkeypatch.setattr(
        "navig.store.telegram_catalog.get_telegram_catalog", lambda: calls.append(1)
    )
    update = {"message": {"chat": {"id": 7, "type": "private"}, "text": "hi", "date": 1}}
    asyncio.run(ingest.ingest_update(None, update))
    assert calls == []
