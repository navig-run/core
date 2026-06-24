"""Tests for media_engine/_retry, pattern_observer, providers/_local_defaults — batch 45."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# _retry helpers
# ---------------------------------------------------------------------------

def test_retry_default_retries_value():
    from navig.gateway.channels.media_engine._retry import DEFAULT_RETRIES

    assert DEFAULT_RETRIES == 2


def test_retry_default_timeout_value():
    from navig.gateway.channels.media_engine._retry import DEFAULT_TIMEOUT

    assert DEFAULT_TIMEOUT == 8.0


def test_retry_default_retries_is_int():
    from navig.gateway.channels.media_engine._retry import DEFAULT_RETRIES

    assert isinstance(DEFAULT_RETRIES, int)


def test_retry_default_timeout_is_float():
    from navig.gateway.channels.media_engine._retry import DEFAULT_TIMEOUT

    assert isinstance(DEFAULT_TIMEOUT, float)


@pytest.mark.asyncio
async def test_with_retry_success_first_attempt():
    from navig.gateway.channels.media_engine._retry import with_retry

    call_count = 0

    async def coro():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await with_retry(coro, retries=2)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_with_retry_succeeds_on_second_attempt():
    from navig.gateway.channels.media_engine._retry import with_retry

    attempts = 0

    async def coro():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise RuntimeError("transient")
        return "ok"

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(coro, retries=2)
    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_with_retry_raises_after_exhaustion():
    from navig.gateway.channels.media_engine._retry import with_retry

    async def coro():
        raise ValueError("always fail")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="always fail"):
            await with_retry(coro, retries=2)


@pytest.mark.asyncio
async def test_with_retry_total_attempts_equals_retries_plus_one():
    from navig.gateway.channels.media_engine._retry import with_retry

    attempts = 0

    async def coro():
        nonlocal attempts
        attempts += 1
        raise OSError("fail")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(OSError):
            await with_retry(coro, retries=3)
    assert attempts == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_with_retry_zero_retries_raises_immediately():
    from navig.gateway.channels.media_engine._retry import with_retry

    attempts = 0

    async def coro():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        await with_retry(coro, retries=0)
    assert attempts == 1


# ---------------------------------------------------------------------------
# PatternObserver
# ---------------------------------------------------------------------------

def test_pattern_record_creation():
    from navig.agent.pattern_observer import PatternRecord

    rec = PatternRecord(command="navig run ls")
    assert rec.command == "navig run ls"


def test_pattern_observer_default_db_path():
    from navig.agent.pattern_observer import PatternObserver

    obs = PatternObserver()
    assert obs.db_path is not None
    assert str(obs.db_path).endswith(".sqlite")


def test_pattern_observer_custom_db_path():
    from navig.agent.pattern_observer import PatternObserver

    obs = PatternObserver(db_path=Path("/tmp/test.sqlite"))
    assert obs.db_path == Path("/tmp/test.sqlite")


def test_pattern_observer_no_db_returns_empty():
    from navig.agent.pattern_observer import PatternObserver

    obs = PatternObserver(db_path=Path("/nonexistent/path/no.db"))
    result = obs.get_recent()
    assert result == []


def test_pattern_observer_get_recent_with_db(tmp_path):
    import sqlite3
    from navig.agent.pattern_observer import PatternObserver

    db = tmp_path / "pattern_log.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
    conn.execute("INSERT INTO patterns VALUES ('navig run ls', 1000)")
    conn.execute("INSERT INTO patterns VALUES ('navig db list', 999)")
    conn.commit()
    conn.close()

    obs = PatternObserver(db_path=db)
    results = obs.get_recent(limit=10)
    assert len(results) == 2
    cmds = [r.command for r in results]
    assert "navig run ls" in cmds


def test_pattern_observer_respects_limit(tmp_path):
    import sqlite3
    from navig.agent.pattern_observer import PatternObserver

    db = tmp_path / "pattern_log.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
    for i in range(20):
        conn.execute(f"INSERT INTO patterns VALUES ('cmd{i}', {i})")
    conn.commit()
    conn.close()

    obs = PatternObserver(db_path=db)
    results = obs.get_recent(limit=5)
    assert len(results) == 5


def test_pattern_observer_handles_exception():
    from navig.agent.pattern_observer import PatternObserver

    # Non-existent path that mimics a non-existent db
    obs = PatternObserver(db_path=Path("/tmp/__navig_test_no_exist_xyz.db"))
    results = obs.get_recent()
    assert results == []


def test_pattern_record_is_dataclass():
    from dataclasses import fields
    from navig.agent.pattern_observer import PatternRecord

    flds = [f.name for f in fields(PatternRecord)]
    assert "command" in flds


# ---------------------------------------------------------------------------
# providers/_local_defaults
# ---------------------------------------------------------------------------

def test_ollama_base_url():
    from navig.providers._local_defaults import _OLLAMA_BASE_URL

    assert _OLLAMA_BASE_URL == "http://127.0.0.1:11434"


def test_ollama_user_base_url():
    from navig.providers._local_defaults import _OLLAMA_USER_BASE_URL

    assert _OLLAMA_USER_BASE_URL == "http://localhost:11434"


def test_llamacpp_base_url():
    from navig.providers._local_defaults import _LLAMACPP_BASE_URL

    assert _LLAMACPP_BASE_URL == "http://127.0.0.1:8080"


def test_llamacpp_user_base_url():
    from navig.providers._local_defaults import _LLAMACPP_USER_BASE_URL

    assert _LLAMACPP_USER_BASE_URL == "http://localhost:8080"


def test_ollama_base_url_uses_loopback():
    from navig.providers._local_defaults import _OLLAMA_BASE_URL

    assert "127.0.0.1" in _OLLAMA_BASE_URL


def test_ollama_user_url_uses_localhost():
    from navig.providers._local_defaults import _OLLAMA_USER_BASE_URL

    assert "localhost" in _OLLAMA_USER_BASE_URL


def test_llamacpp_base_url_uses_loopback():
    from navig.providers._local_defaults import _LLAMACPP_BASE_URL

    assert "127.0.0.1" in _LLAMACPP_BASE_URL


def test_llamacpp_user_url_uses_localhost():
    from navig.providers._local_defaults import _LLAMACPP_USER_BASE_URL

    assert "localhost" in _LLAMACPP_USER_BASE_URL


def test_ollama_port_is_11434():
    from navig.providers._local_defaults import _OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL

    assert ":11434" in _OLLAMA_BASE_URL
    assert ":11434" in _OLLAMA_USER_BASE_URL


def test_llamacpp_port_is_8080():
    from navig.providers._local_defaults import _LLAMACPP_BASE_URL, _LLAMACPP_USER_BASE_URL

    assert ":8080" in _LLAMACPP_BASE_URL
    assert ":8080" in _LLAMACPP_USER_BASE_URL


def test_all_local_defaults_are_http():
    from navig.providers._local_defaults import (
        _LLAMACPP_BASE_URL,
        _LLAMACPP_USER_BASE_URL,
        _OLLAMA_BASE_URL,
        _OLLAMA_USER_BASE_URL,
    )

    for url in (_OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL, _LLAMACPP_BASE_URL, _LLAMACPP_USER_BASE_URL):
        assert url.startswith("http://"), f"Expected http, got: {url}"
