"""
Tests for navig.store.pg_mirror — PgMirror PostgreSQL write buffer.
"""
import os
from unittest.mock import MagicMock, patch

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
        """Reaching batch_size triggers a flush attempt; when PG is unavailable the
        events stay buffered for retry (they must NOT be dropped)."""
        m = self._enabled(batch_size=3)
        m._get_conn = MagicMock(return_value=None)  # PG unavailable → flush can't drain

        for i in range(3):
            m.emit("t", "INSERT", {"i": i})

        m._get_conn.assert_called()  # the flush WAS triggered at batch_size
        assert len(m._buffer) == 3   # …but the events are retained, not lost

    def test_flush_retains_buffer_when_no_conn(self):
        m = self._enabled()
        m.emit("events", "INSERT", {"id": 1})
        m._get_conn = MagicMock(return_value=None)
        flushed = m.flush()
        assert flushed == 0
        assert len(m._buffer) == 1  # retained — a transient outage must not drop events

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


# ---------------------------------------------------------------------------
# re-buffer on failure — a transient PG blip must not drop audit events
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Records executed statements; can be told to fail specific execute() calls."""

    def __init__(self, fail_indices=None):
        self.calls = []
        self._fail = set(fail_indices or ())
        self._n = 0

    def execute(self, sql, params=None):
        i, self._n = self._n, self._n + 1
        if i in self._fail:
            raise RuntimeError(f"row {i} rejected")
        self.calls.append((sql, params))


class _FakeConn:
    def __init__(self, *, commit_fails=False, cursor=None):
        self._commit_fails = commit_fails
        self._cursor = cursor if cursor is not None else _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        if self._commit_fails:
            raise RuntimeError("commit failed")
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class TestPgMirrorReBuffer:
    def _enabled(self, **kw):
        return PgMirror(pg_url="postgresql://fake/db", **kw)

    def test_no_conn_keeps_buffer_for_retry(self):
        # A transient PG outage (connection unavailable) must NOT drop buffered events.
        m = self._enabled()
        m.emit("t", "INSERT", {"i": 1})
        m.emit("t", "INSERT", {"i": 2})
        m._get_conn = MagicMock(return_value=None)
        assert m.flush() == 0
        assert len(m._buffer) == 2  # retained — retried on the next flush

    def test_commit_failure_keeps_buffer_and_resets_conn(self):
        m = self._enabled()
        m.emit("t", "INSERT", {"i": 1})
        conn = _FakeConn(commit_fails=True)
        m._get_conn = MagicMock(return_value=conn)
        m._conn = conn
        assert m.flush() == 0
        assert len(m._buffer) == 1     # kept for retry, not dropped
        assert conn.rolled_back is True  # the half-applied transaction was rolled back
        assert m._conn is None           # connection reset → next flush reconnects

    def test_successful_flush_drains_buffer(self):
        m = self._enabled()
        for i in range(3):
            m.emit("t", "INSERT", {"i": i})
        conn = _FakeConn()
        m._get_conn = MagicMock(return_value=conn)
        assert m.flush() == 3
        assert len(m._buffer) == 0
        assert conn.committed is True

    def test_retry_succeeds_after_outage(self):
        # PG down, then back: events buffered during the outage flush on recovery.
        m = self._enabled()
        m.emit("t", "INSERT", {"i": 1})
        m._get_conn = MagicMock(return_value=None)
        m.flush()
        assert len(m._buffer) == 1  # survived the outage
        conn = _FakeConn()
        m._get_conn = MagicMock(return_value=conn)
        assert m.flush() == 1
        assert len(m._buffer) == 0  # drained on recovery

    def test_bad_row_skipped_others_flush_and_batch_drops(self):
        # A single un-mirrorable row is logged+skipped; the rest commit and the whole
        # batch is dropped (a poison row must not wedge the buffer forever).
        m = self._enabled()
        for i in range(3):
            m.emit("t", "INSERT", {"i": i})
        conn = _FakeConn(cursor=_FakeCursor(fail_indices={1}))
        m._get_conn = MagicMock(return_value=conn)
        assert m.flush() == 2       # 2 of 3 mirrored
        assert len(m._buffer) == 0  # batch dropped, not retried forever
        assert conn.committed is True

    def test_buffer_capped_drops_oldest_when_pg_down(self):
        # With PG persistently down, the retained buffer is bounded; the OLDEST drop.
        # batch_size < max_buffer so the cap (5) — not the per-batch floor — is what binds.
        m = self._enabled(batch_size=2, max_buffer=5)
        m._get_conn = MagicMock(return_value=None)  # every flush is a no-op → buffer grows
        for i in range(8):
            m.emit("t", "INSERT", {"i": i})
        assert len(m._buffer) == 5
        assert [e["data"]["i"] for e in m._buffer] == [3, 4, 5, 6, 7]  # newest kept
