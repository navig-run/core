"""
Tests for navig.store.pg_mirror — PgMirror PostgreSQL write buffer.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from navig.store.pg_mirror import PgMirror


# ---------------------------------------------------------------------------
# enabled property
# ---------------------------------------------------------------------------

class TestPgMirrorEnabled:
    def test_disabled_when_no_url_and_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NAVIG_PG_URL", None)
            m = PgMirror(pg_url=None)
        assert m.enabled is False

    def test_enabled_when_url_passed(self):
        m = PgMirror(pg_url="postgresql://user:pass@localhost/db")
        assert m.enabled is True

    def test_enabled_when_env_var_set(self):
        with patch.dict(os.environ, {"NAVIG_PG_URL": "postgresql://localhost/x"}):
            m = PgMirror()
        assert m.enabled is True

    def test_disabled_when_empty_string(self):
        m = PgMirror(pg_url="")
        assert m.enabled is False


# ---------------------------------------------------------------------------
# emit — disabled path
# ---------------------------------------------------------------------------

class TestPgMirrorEmitDisabled:
    def test_emit_noop_when_disabled(self):
        m = PgMirror(pg_url="")
        m.emit("events", "INSERT", {"id": 1})
        assert len(m._buffer) == 0

    def test_flush_returns_zero_when_disabled_and_empty(self):
        m = PgMirror(pg_url="")
        assert m.flush() == 0


# ---------------------------------------------------------------------------
# emit — enabled path (no real PG)
# ---------------------------------------------------------------------------

class TestPgMirrorEmitEnabled:
    def _enabled(self, batch_size=50):
        return PgMirror(pg_url="postgresql://fake/db", batch_size=batch_size)

    def test_emit_appends_to_buffer(self):
        m = self._enabled()
        m.emit("events", "INSERT", {"id": 1})
        assert len(m._buffer) == 1

    def test_buffer_entry_has_expected_keys(self):
        m = self._enabled()
        m.emit("events", "INSERT", {"id": 1})
        entry = m._buffer[0]
        assert entry["table"] == "events"
        assert entry["op"] == "INSERT"
        assert entry["data"] == {"id": 1}
        assert "timestamp" in entry

    def test_multiple_emits_buffer_all(self):
        m = self._enabled()
        for i in range(5):
            m.emit("t", "INSERT", {"i": i})
        assert len(m._buffer) == 5

    def test_auto_flush_at_batch_size(self):
        """When buffer reaches batch_size, _flush_unsafe is called automatically."""
        m = self._enabled(batch_size=3)
        # Mock _flush_unsafe so we don't need a real PG connection
        m._get_conn = MagicMock(return_value=None)  # no DB → flush returns 0

        for i in range(3):
            m.emit("t", "INSERT", {"i": i})

        # After 3 emits with batch_size=3, flush was triggered and buffer cleared
        # (flush returns 0 because _get_conn returned None, but buffer is cleared)
        assert len(m._buffer) == 0

    def test_flush_clears_buffer_when_no_conn(self):
        m = self._enabled()
        m.emit("events", "INSERT", {"id": 1})
        m._get_conn = MagicMock(return_value=None)
        flushed = m.flush()
        assert flushed == 0
        assert len(m._buffer) == 0  # buffer was cleared even with no conn

    def test_thread_safety_lock_exists(self):
        m = self._enabled()
        import threading
        assert isinstance(m._lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# flush — empty buffer
# ---------------------------------------------------------------------------

class TestPgMirrorFlushEmpty:
    def test_flush_empty_returns_zero(self):
        m = PgMirror(pg_url="postgresql://fake/db")
        m._get_conn = MagicMock(return_value=None)
        assert m.flush() == 0
