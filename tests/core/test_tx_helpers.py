"""Tests for navig.storage.tx_helpers — begin_immediate, savepoint context managers."""
from __future__ import annotations

import sqlite3

import pytest

from navig.storage.tx_helpers import begin_immediate, savepoint


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.commit()
    return conn


class TestBeginImmediate:
    def test_inserts_commit_on_success(self) -> None:
        conn = _fresh_db()
        with begin_immediate(conn):
            conn.execute("INSERT INTO t VALUES (1, 'hello')")
        row = conn.execute("SELECT val FROM t WHERE id=1").fetchone()
        assert row is not None
        assert row[0] == "hello"

    def test_rollback_on_exception(self) -> None:
        conn = _fresh_db()
        with pytest.raises(ValueError):
            with begin_immediate(conn):
                conn.execute("INSERT INTO t VALUES (2, 'gone')")
                raise ValueError("oops")
        row = conn.execute("SELECT val FROM t WHERE id=2").fetchone()
        assert row is None

    def test_yields_same_connection(self) -> None:
        conn = _fresh_db()
        with begin_immediate(conn) as c:
            assert c is conn

    def test_restores_isolation_level(self) -> None:
        conn = _fresh_db()
        original = conn.isolation_level
        with begin_immediate(conn):
            pass
        assert conn.isolation_level == original

    def test_multiple_rows_committed(self) -> None:
        conn = _fresh_db()
        with begin_immediate(conn):
            for i in range(5):
                conn.execute("INSERT INTO t VALUES (?, ?)", (i + 10, f"v{i}"))
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 5

    def test_exception_type_preserved(self) -> None:
        conn = _fresh_db()
        with pytest.raises(RuntimeError, match="specific"):
            with begin_immediate(conn):
                raise RuntimeError("specific")


class TestSavepoint:
    def test_inner_insert_committed(self) -> None:
        conn = _fresh_db()
        conn.isolation_level = None  # autocommit for outer
        with savepoint(conn, "sp1"):
            conn.execute("INSERT INTO t VALUES (1, 'inner')")
        row = conn.execute("SELECT val FROM t WHERE id=1").fetchone()
        assert row is not None
        assert row[0] == "inner"

    def test_inner_rollback_on_exception(self) -> None:
        conn = _fresh_db()
        conn.isolation_level = None
        # insert first row outside savepoint
        conn.execute("INSERT INTO t VALUES (10, 'outer')")
        with pytest.raises(ValueError):
            with savepoint(conn, "sp_fail"):
                conn.execute("INSERT INTO t VALUES (20, 'rolled')")
                raise ValueError("rollback me")
        row_outer = conn.execute("SELECT id FROM t WHERE id=10").fetchone()
        assert row_outer is not None
        row_inner = conn.execute("SELECT id FROM t WHERE id=20").fetchone()
        assert row_inner is None

    def test_exception_re_raised(self) -> None:
        conn = _fresh_db()
        conn.isolation_level = None
        with pytest.raises(KeyError):
            with savepoint(conn, "sp_re"):
                raise KeyError("rethrow")

    def test_yields_connection(self) -> None:
        conn = _fresh_db()
        conn.isolation_level = None
        with savepoint(conn, "sp_yield") as c:
            assert c is conn

    def test_nested_savepoints(self) -> None:
        conn = _fresh_db()
        conn.isolation_level = None
        with savepoint(conn, "outer"):
            conn.execute("INSERT INTO t VALUES (1, 'a')")
            with savepoint(conn, "inner"):
                conn.execute("INSERT INTO t VALUES (2, 'b')")
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 2
