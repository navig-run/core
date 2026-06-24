"""
Tests for navig.agent.context.daily_log — DailyLog SQLite interactions.
"""

from __future__ import annotations

import pytest

from navig.agent.context.daily_log import (
    DEFAULT_RETENTION_DAYS,
    MAX_CONTEXT_CHARS,
    MAX_SUMMARY_ENTRIES,
    DailyLog,
)


def _log(tmp_path) -> DailyLog:
    return DailyLog(db_path=tmp_path / "test_log.db")


# ─── Module constants ─────────────────────────────────────────────────────────


def test_default_retention_days():
    assert DEFAULT_RETENTION_DAYS == 30


def test_max_summary_entries():
    assert MAX_SUMMARY_ENTRIES == 50


def test_max_context_chars():
    assert MAX_CONTEXT_CHARS == 2000


# ─── DailyLog.__init__ ────────────────────────────────────────────────────────


def test_daily_log_custom_db_path(tmp_path):
    db = tmp_path / "custom.db"
    log = DailyLog(db_path=db)
    assert log.db_path == db


def test_daily_log_default_retention(tmp_path):
    log = _log(tmp_path)
    assert log.retention_days == DEFAULT_RETENTION_DAYS


def test_daily_log_custom_retention(tmp_path):
    log = DailyLog(db_path=tmp_path / "l.db", retention_days=7)
    assert log.retention_days == 7


# ─── session_id ───────────────────────────────────────────────────────────────


def test_session_id_auto_generated(tmp_path):
    log = _log(tmp_path)
    sid = log.session_id
    assert isinstance(sid, str)
    assert len(sid) > 0


def test_session_id_stable(tmp_path):
    log = _log(tmp_path)
    assert log.session_id == log.session_id


def test_start_session_custom(tmp_path):
    log = _log(tmp_path)
    sid = log.start_session("my-session-123")
    assert sid == "my-session-123"
    assert log.session_id == "my-session-123"


def test_start_session_auto_generates(tmp_path):
    log = _log(tmp_path)
    sid = log.start_session()
    assert isinstance(sid, str)


# ─── add_entry ────────────────────────────────────────────────────────────────


def test_add_entry_returns_int(tmp_path):
    log = _log(tmp_path)
    entry_id = log.add_entry("user", "Hello!")
    assert isinstance(entry_id, int)
    assert entry_id >= 1


def test_add_entry_creates_db_file(tmp_path):
    db_path = tmp_path / "log.db"
    log = DailyLog(db_path=db_path)
    log.add_entry("user", "test")
    assert db_path.exists()


def test_add_entry_multiple(tmp_path):
    log = _log(tmp_path)
    id1 = log.add_entry("user", "msg 1")
    id2 = log.add_entry("agent", "msg 2")
    assert id2 > id1


def test_add_entry_with_metadata(tmp_path):
    log = _log(tmp_path)
    entry_id = log.add_entry("user", "query", metadata={"key": "value"})
    assert entry_id is not None


def test_add_entry_channel_and_server(tmp_path):
    log = _log(tmp_path)
    entry_id = log.add_entry("user", "test", channel="telegram", server="prod-01")
    assert entry_id >= 1


def test_add_entry_with_command(tmp_path):
    log = _log(tmp_path)
    entry_id = log.add_entry("agent", "deploying", command="navig deploy")
    assert entry_id >= 1


# ─── _sanitize_content ────────────────────────────────────────────────────────


def test_sanitize_content_truncates(tmp_path):
    log = _log(tmp_path)
    long_text = "x" * 300
    result = log._sanitize_content(long_text, max_length=200)
    assert len(result) <= 203  # 200 + "..."
    assert result.endswith("...")


def test_sanitize_content_short_passes_through(tmp_path):
    log = _log(tmp_path)
    result = log._sanitize_content("short message")
    assert result == "short message"


def test_sanitize_content_exact_boundary(tmp_path):
    log = _log(tmp_path)
    text = "a" * 200
    result = log._sanitize_content(text, max_length=200)
    assert result == text  # exactly 200, no truncation


# ─── get_recent_entries ───────────────────────────────────────────────────────


def test_get_recent_entries_empty(tmp_path):
    log = _log(tmp_path)
    entries = log.get_recent_entries(hours=24)
    assert entries == []


def test_get_recent_entries_returns_added(tmp_path):
    log = _log(tmp_path)
    log.add_entry("user", "hello world")
    entries = log.get_recent_entries(hours=24)
    assert len(entries) >= 1


def test_get_recent_entries_respects_limit(tmp_path):
    log = _log(tmp_path)
    for i in range(10):
        log.add_entry("user", f"msg {i}")
    entries = log.get_recent_entries(hours=24, limit=3)
    assert len(entries) <= 3


# ─── Database schema ──────────────────────────────────────────────────────────


def test_ensure_initialized_creates_tables(tmp_path):
    log = _log(tmp_path)
    log._ensure_initialized()
    import sqlite3
    conn = sqlite3.connect(str(log.db_path))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "interactions" in tables
    assert "daily_summaries" in tables
