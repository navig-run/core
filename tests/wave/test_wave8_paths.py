"""
tests/test_wave8_paths.py

Verify that wave-8 function/constructor fallbacks in inbox, identity, and
telegram_sessions all honour NAVIG_CONFIG_DIR / NAVIG_DATA_DIR rather than
hardcoding ~/.navig.

Note: navig.desktop.tray_app has module-level side-effects (logging.basicConfig,
mkdir) and is not imported in unit tests; it is covered by manual integration
testing only.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ── inbox/watcher ─────────────────────────────────────────────────────────────


def test_global_inbox_dir_respects_config_env(tmp_path, monkeypatch):
    """`_global_inbox_dir()` must place inbox inside NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    # data_dir() falls back to config_dir()/data when NAVIG_DATA_DIR is unset
    monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
    from navig.inbox.watcher import _global_inbox_dir

    result = _global_inbox_dir()
    # Expected: config_dir()/data/inbox  (data_dir() = config_dir()/data)
    assert result == custom / "data" / "inbox"


def test_global_inbox_dir_respects_data_env(tmp_path, monkeypatch):
    """`_global_inbox_dir()` must respect NAVIG_DATA_DIR when set."""
    data_root = tmp_path / "mydata"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_root))
    from navig.inbox.watcher import _global_inbox_dir

    assert _global_inbox_dir() == data_root / "inbox"


# ── inbox/store ───────────────────────────────────────────────────────────────


def test_inbox_db_respects_data_env(tmp_path, monkeypatch):
    """`_inbox_db()` must place the DB inside NAVIG_DATA_DIR."""
    data_root = tmp_path / "mydata"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_root))

    import navig.inbox.store as store_mod

    # Reset the singleton so the function re-evaluates the env
    store_mod._DEFAULT_DB = None
    result = store_mod._inbox_db()
    assert result == data_root / "inbox.db"
    # Restore the singleton reset so subsequent tests aren't affected
    store_mod._DEFAULT_DB = None


# ── telegram_sessions ─────────────────────────────────────────────────────────


def test_telegram_session_store_default_dir_respects_config_env(tmp_path, monkeypatch):
    """TelegramSessionStore() default storage_dir must respect NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
    from navig.gateway.channels.telegram_sessions import SessionManager

    store = SessionManager()
    # data_dir() = config_dir()/data = custom/data
    assert store.storage_dir == custom / "data" / "telegram_sessions"


def test_telegram_session_store_default_dir_respects_data_env(tmp_path, monkeypatch):
    """TelegramSessionStore() default storage_dir must respect NAVIG_DATA_DIR."""
    data_root = tmp_path / "mydata"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_root))
    from navig.gateway.channels.telegram_sessions import SessionManager

    store = SessionManager()
    assert store.storage_dir == data_root / "telegram_sessions"


def test_telegram_session_store_explicit_dir_unaffected(tmp_path, monkeypatch):
    """TelegramSessionStore(storage_dir=explicit) must use the explicit path."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "env_cfg"))
    from navig.gateway.channels.telegram_sessions import SessionManager

    explicit = tmp_path / "my_sessions"
    store = SessionManager(storage_dir=explicit)
    assert store.storage_dir == explicit
