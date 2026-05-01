"""
Batch 63: hermetic unit tests for
  - navig/storage/tx_helpers.py         (begin_immediate, savepoint)
  - navig/connectors/circuit_breaker.py (CircuitState, CircuitBreaker)
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# navig/storage/tx_helpers.py
# ---------------------------------------------------------------------------

def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v INTEGER)")
    return conn


class TestBeginImmediate:
    def test_importable(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        assert callable(begin_immediate)

    def test_commits_on_clean_exit(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")
        row = conn.execute("SELECT count(*) FROM t").fetchone()[0]
        assert row == 1

    def test_rolls_back_on_exception(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        try:
            with begin_immediate(conn) as c:
                c.execute("INSERT INTO t VALUES (99)")
                raise ValueError("oops")
        except ValueError:
            pass
        row = conn.execute("SELECT count(*) FROM t").fetchone()[0]
        assert row == 0

    def test_yields_connection(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            assert c is conn

    def test_restores_isolation_level_after_success(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        original = conn.isolation_level
        with begin_immediate(conn):
            pass
        assert conn.isolation_level == original

    def test_restores_isolation_level_after_exception(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        original = conn.isolation_level
        try:
            with begin_immediate(conn):
                raise RuntimeError("fail")
        except RuntimeError:
            pass
        assert conn.isolation_level == original

    def test_multiple_inserts_in_one_transaction(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")
            c.execute("INSERT INTO t VALUES (2)")
            c.execute("INSERT INTO t VALUES (3)")
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3

    def test_re_raises_exception(self) -> None:
        from navig.storage.tx_helpers import begin_immediate
        conn = _in_memory_db()
        with pytest.raises(ValueError, match="test error"):
            with begin_immediate(conn):
                raise ValueError("test error")


class TestSavepoint:
    def test_importable(self) -> None:
        from navig.storage.tx_helpers import savepoint
        assert callable(savepoint)

    def test_commits_on_clean_exit(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            with savepoint(c, "sp1") as sc:
                sc.execute("INSERT INTO t VALUES (10)")
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1

    def test_rolls_back_savepoint_on_exception(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")  # this should commit
            try:
                with savepoint(c, "sp2"):
                    c.execute("INSERT INTO t VALUES (99)")
                    raise ValueError("partial failure")
            except ValueError:
                pass  # savepoint rolled back, outer tx continues
        # Row 1 committed; row 99 rolled back
        rows = conn.execute("SELECT v FROM t").fetchall()
        values = [r[0] for r in rows]
        assert 1 in values
        assert 99 not in values

    def test_yields_connection(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            with savepoint(c, "sp") as sc:
                assert sc is c

    def test_default_name(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            with savepoint(c):  # default name="sp"
                c.execute("INSERT INTO t VALUES (7)")
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1

    def test_re_raises_exception(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn):
            with pytest.raises(RuntimeError):
                with savepoint(conn, "ex"):
                    raise RuntimeError("test")

    def test_nested_savepoints(self) -> None:
        from navig.storage.tx_helpers import begin_immediate, savepoint
        conn = _in_memory_db()
        with begin_immediate(conn) as c:
            with savepoint(c, "outer"):
                c.execute("INSERT INTO t VALUES (1)")
                with savepoint(c, "inner"):
                    c.execute("INSERT INTO t VALUES (2)")
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# navig/connectors/circuit_breaker.py
# ---------------------------------------------------------------------------

class TestCircuitState:
    def test_importable(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        assert CircuitState is not None

    def test_values(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_is_str_enum(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        assert isinstance(CircuitState.CLOSED, str)


class TestCircuitBreaker:
    def _cb(self, **kwargs) -> "CircuitBreaker":
        from navig.connectors.circuit_breaker import CircuitBreaker
        return CircuitBreaker("test_connector", **kwargs)

    def test_starts_closed(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb()
        assert cb.state == CircuitState.CLOSED

    def test_allow_request_when_closed(self) -> None:
        cb = self._cb()
        assert cb.allow_request() is True

    def test_trips_after_threshold_failures(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_does_not_trip_below_threshold(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=3)
        for _ in range(2):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_open_blocks_requests(self) -> None:
        cb = self._cb(failure_threshold=1)
        cb.record_failure()
        assert cb.allow_request() is False

    def test_success_resets_to_closed(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=1)
        cb.record_failure()  # trips
        # Force to HALF_OPEN by mocking time
        cb._state = __import__("navig.connectors.circuit_breaker", fromlist=["CircuitState"]).CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reset_forces_closed(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_reset_clears_failure_count(self) -> None:
        cb = self._cb(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        cb.reset()
        assert cb._failure_count == 0

    def test_to_dict_keys(self) -> None:
        cb = self._cb()
        d = cb.to_dict()
        for key in ("connector_id", "state", "failure_count", "total_trips", "recovery_timeout"):
            assert key in d

    def test_to_dict_connector_id(self) -> None:
        cb = self._cb()
        assert cb.to_dict()["connector_id"] == "test_connector"

    def test_to_dict_state_string(self) -> None:
        cb = self._cb()
        assert cb.to_dict()["state"] == "closed"

    def test_half_open_after_recovery_timeout(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=1, recovery_timeout=0.001)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN
        time.sleep(0.01)
        # Accessing state should auto-promote to HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_request(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=1, recovery_timeout=0.001)
        cb.record_failure()
        time.sleep(0.01)
        assert cb.allow_request() is True  # HALF_OPEN permits probe

    def test_failure_in_half_open_returns_to_open(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=1, recovery_timeout=0.001)
        cb.record_failure()
        time.sleep(0.01)
        _ = cb.state  # promote to HALF_OPEN
        cb.record_failure()  # recovery probe failed
        assert cb.state == CircuitState.OPEN

    def test_custom_failure_threshold(self) -> None:
        from navig.connectors.circuit_breaker import CircuitState
        cb = self._cb(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_total_trips_increments_on_open(self) -> None:
        from navig.connectors.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("c", failure_threshold=1)
        cb.record_failure()
        # total_trips should be 1 after first trip
        assert cb._total_trips >= 1
