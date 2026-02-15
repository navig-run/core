"""
Tests for navig.storage — the unified SQLite engine module.

Covers:
    - PragmaProfile + profile_for_db
    - Engine connect / close / lifecycle
    - PRAGMA application
    - Custom SQL functions (cosine_distance, json_text)
    - WriteBatcher (count + time flush)
    - QueryTimer (latency tracking, percentiles)
    - Transaction helpers (begin_immediate, savepoint)
    - Migration runner
    - Maintenance
    - Backup
    - Prepared statement cache
"""

from __future__ import annotations

import math
import os
import sqlite3
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────


def _make_db(tmp_path: Path, name: str = "test.db") -> Path:
    return tmp_path / name


def _float_blob(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


# ═══════════════════════════════════════════════════════════════
# PRAGMA Profiles
# ═══════════════════════════════════════════════════════════════


class TestPragmaProfiles:
    def test_three_profiles_exist(self):
        from navig.storage.pragma_profiles import FAST, BALANCED, DURABLE

        assert FAST.name == "FAST"
        assert BALANCED.name == "BALANCED"
        assert DURABLE.name == "DURABLE"

    def test_profile_values(self):
        from navig.storage.pragma_profiles import FAST, BALANCED, DURABLE

        assert FAST.synchronous == "OFF"
        assert BALANCED.synchronous == "NORMAL"
        assert DURABLE.synchronous == "FULL"

    def test_to_pragma_dict(self):
        from navig.storage.pragma_profiles import BALANCED

        d = BALANCED.to_pragma_dict()
        assert isinstance(d, dict)
        assert d["journal_mode"] == "WAL"
        assert d["synchronous"] == "NORMAL"
        assert d["foreign_keys"] == "ON"
        assert "cache_size" in d
        assert "mmap_size" in d

    def test_profile_for_db_mapping(self):
        from navig.storage.pragma_profiles import profile_for_db, FAST, BALANCED, DURABLE

        assert profile_for_db("runtime.db") is FAST
        assert profile_for_db("memory.db") is BALANCED
        assert profile_for_db("matrix.db") is BALANCED
        assert profile_for_db("index.db") is BALANCED
        assert profile_for_db("audit.db") is DURABLE
        assert profile_for_db("vault.db") is DURABLE

    def test_profile_for_unknown_db(self):
        from navig.storage.pragma_profiles import profile_for_db, BALANCED

        assert profile_for_db("unknown.db") is BALANCED

    def test_durable_has_incremental_vacuum(self):
        from navig.storage.pragma_profiles import DURABLE

        assert DURABLE.auto_vacuum == "INCREMENTAL"

    def test_fast_has_no_vacuum(self):
        from navig.storage.pragma_profiles import FAST

        assert FAST.auto_vacuum == "NONE"


# ═══════════════════════════════════════════════════════════════
# Engine Core
# ═══════════════════════════════════════════════════════════════


class TestEngine:
    def test_connect_creates_db(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        conn = engine.connect(db)
        assert conn is not None
        assert db.exists()
        engine.close_all()

    def test_connect_returns_same_conn_per_thread(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        c1 = engine.connect(db)
        c2 = engine.connect(db)
        assert c1 is c2
        engine.close_all()

    def test_connect_returns_different_conn_per_thread(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        c1 = engine.connect(db)

        conns = []

        def worker():
            conns.append(engine.connect(db))

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert len(conns) == 1
        assert conns[0] is not c1
        engine.close_all()

    def test_pragmas_applied(self, tmp_path):
        from navig.storage.engine import Engine
        from navig.storage.pragma_profiles import FAST

        engine = Engine()
        db = _make_db(tmp_path, "runtime.db")
        conn = engine.connect(db)

        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal.upper() == "WAL"

        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        # FAST profile: synchronous=OFF → value 0
        assert sync == 0

        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

        engine.close_all()

    def test_explicit_profile_override(self, tmp_path):
        from navig.storage.engine import Engine
        from navig.storage.pragma_profiles import DURABLE

        engine = Engine()
        db = _make_db(tmp_path, "runtime.db")  # would auto-detect FAST
        conn = engine.connect(db, profile=DURABLE)

        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        # DURABLE: synchronous=FULL → value 2
        assert sync == 2
        engine.close_all()

    def test_cosine_distance_function(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        conn = engine.connect(db)

        a = _float_blob([1.0, 0.0, 0.0])
        b = _float_blob([0.0, 1.0, 0.0])
        result = conn.execute("SELECT cosine_distance(?, ?)", (a, b)).fetchone()[0]
        # Orthogonal vectors → cosine distance = 1.0
        assert abs(result - 1.0) < 0.001

        # Same vector → distance 0
        c = _float_blob([1.0, 2.0, 3.0])
        result2 = conn.execute("SELECT cosine_distance(?, ?)", (c, c)).fetchone()[0]
        assert abs(result2) < 0.001
        engine.close_all()

    def test_json_text_function(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        conn = engine.connect(db)

        result = conn.execute(
            """SELECT json_text('{"name":"bob","age":42}', 'name')"""
        ).fetchone()[0]
        assert result == "bob"

        # Missing key
        result2 = conn.execute(
            """SELECT json_text('{"name":"bob"}', 'missing')"""
        ).fetchone()[0]
        assert result2 == ""

        # NULL input
        result3 = conn.execute(
            """SELECT json_text(NULL, 'key')"""
        ).fetchone()[0]
        assert result3 is None
        engine.close_all()

    def test_write_lock_is_shared(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        lock1 = engine.write_lock(db)
        lock2 = engine.write_lock(db)
        assert lock1 is lock2
        engine.close_all()

    def test_close_specific_db(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db1 = _make_db(tmp_path, "a.db")
        db2 = _make_db(tmp_path, "b.db")
        engine.connect(db1)
        engine.connect(db2)
        engine.close(db1)
        # db2 should still work
        conn2 = engine.connect(db2)
        assert conn2 is not None
        engine.close_all()

    def test_close_all(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        engine.connect(db)
        engine.close_all()
        # Verify internal state is cleaned
        assert len(engine._batchers) == 0
        assert len(engine._stmt_caches) == 0


# ═══════════════════════════════════════════════════════════════
# Migration Runner
# ═══════════════════════════════════════════════════════════════


class TestMigrationRunner:
    def test_fresh_database_creates_schema(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)

        def create(conn):
            conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")

        result = engine.run_migrations(db, target_version=1, create_schema=create)
        assert result["action"] == "created"
        assert result["from_version"] == 0
        assert result["to_version"] == 1

        conn = engine.connect(db)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 1

        # Table exists
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'").fetchall()
        assert len(rows) == 1
        engine.close_all()

    def test_migration_upgrades_version(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)

        def create_v1(conn):
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")

        engine.run_migrations(db, target_version=1, create_schema=create_v1)

        # Close and reopen to simulate restart
        engine.close_all()
        engine = Engine()

        def create_v2(conn):
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

        def migrate(conn, from_v, to_v):
            if from_v < 2:
                conn.execute("ALTER TABLE items ADD COLUMN name TEXT")

        result = engine.run_migrations(
            db, target_version=2, create_schema=create_v2, migrate=migrate
        )
        assert result["action"] == "migrated"
        assert result["from_version"] == 1
        assert result["to_version"] == 2
        engine.close_all()

    def test_dry_run_does_not_persist(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)

        created = []

        def create(conn):
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            created.append(True)

        result = engine.run_migrations(
            db, target_version=1, create_schema=create, dry_run=True
        )
        assert result["action"] == "dry_run_create"
        assert len(created) == 1  # create was called

        # But schema_version should be 0 (rolled back)
        conn = engine.connect(db)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is None or row[0] == 0
        engine.close_all()

    def test_current_version_noop(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)

        def create(conn):
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")

        engine.run_migrations(db, target_version=1, create_schema=create)
        result = engine.run_migrations(db, target_version=1, create_schema=create)
        assert result["action"] == "current"
        engine.close_all()

    def test_missing_migrate_callback_raises(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)

        def create(conn):
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")

        engine.run_migrations(db, target_version=1, create_schema=create)
        engine.close_all()

        engine = Engine()
        with pytest.raises(ValueError, match="migrate"):
            engine.run_migrations(db, target_version=2, create_schema=create)
        engine.close_all()


# ═══════════════════════════════════════════════════════════════
# Maintenance + Backup
# ═══════════════════════════════════════════════════════════════


class TestMaintenance:
    def test_maintenance_returns_results(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        conn = engine.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()

        result = engine.maintenance(db)
        assert result["db"] == "test.db"
        assert result["optimize"] == "done"
        assert result["analyze"] == "done"
        assert result["integrity"] == "ok"
        assert result["size_bytes"] > 0
        assert "duration_ms" in result
        engine.close_all()

    def test_checkpoint(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path)
        conn = engine.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()

        result = engine.checkpoint(db, mode="PASSIVE")
        assert "busy" in result
        assert "log_pages" in result
        assert "checkpointed_pages" in result
        engine.close_all()

    def test_backup(self, tmp_path):
        from navig.storage.engine import Engine

        engine = Engine()
        db = _make_db(tmp_path, "source.db")
        conn = engine.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'hello')")
        conn.commit()

        dest = tmp_path / "backup" / "source_backup.db"
        engine.backup(db, dest)
        assert dest.exists()

        # Verify backup content
        bconn = sqlite3.connect(str(dest))
        row = bconn.execute("SELECT val FROM t WHERE id=1").fetchone()
        assert row[0] == "hello"
        bconn.close()
        engine.close_all()


# ═══════════════════════════════════════════════════════════════
# Transaction Helpers
# ═══════════════════════════════════════════════════════════════


class TestTxHelpers:
    def test_begin_immediate_commits(self, tmp_path):
        from navig.storage.tx_helpers import begin_immediate

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()

        with begin_immediate(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")

        row = conn.execute("SELECT id FROM t WHERE id=1").fetchone()
        assert row[0] == 1
        conn.close()

    def test_begin_immediate_rollbacks_on_error(self, tmp_path):
        from navig.storage.tx_helpers import begin_immediate

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()

        with pytest.raises(ValueError):
            with begin_immediate(conn) as c:
                c.execute("INSERT INTO t VALUES (1)")
                raise ValueError("simulated error")

        row = conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert row[0] == 0
        conn.close()

    def test_savepoint_commits(self, tmp_path):
        from navig.storage.tx_helpers import savepoint

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("BEGIN")

        with savepoint(conn, "sp1") as c:
            c.execute("INSERT INTO t VALUES (1)")

        conn.execute("COMMIT")

        row = conn.execute("SELECT id FROM t WHERE id=1").fetchone()
        assert row[0] == 1
        conn.close()

    def test_savepoint_partial_rollback(self, tmp_path):
        from navig.storage.tx_helpers import savepoint

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("BEGIN")

        conn.execute("INSERT INTO t VALUES (1)")

        with pytest.raises(ValueError):
            with savepoint(conn, "sp1") as c:
                c.execute("INSERT INTO t VALUES (2)")
                raise ValueError("simulated")

        conn.execute("COMMIT")

        rows = conn.execute("SELECT id FROM t ORDER BY id").fetchall()
        # Row 1 should survive, row 2 should be rolled back
        assert [r[0] for r in rows] == [1]
        conn.close()


# ═══════════════════════════════════════════════════════════════
# QueryTimer
# ═══════════════════════════════════════════════════════════════


class TestQueryTimer:
    def test_track_records_samples(self):
        from navig.storage.query_timer import QueryTimer

        timer = QueryTimer()
        for _ in range(10):
            with timer.track("test.query"):
                time.sleep(0.001)

        stats = timer.get_stats("test.query")
        assert stats is not None
        assert stats["count"] == 10
        assert stats["p50_ms"] > 0
        assert stats["p95_ms"] >= stats["p50_ms"]

    def test_percentile_ordering(self):
        from navig.storage.query_timer import QueryTimer

        timer = QueryTimer()
        # Insert 10 samples with known latencies using track
        for i in range(1, 11):
            with timer.track("ordered"):
                time.sleep(i * 0.002)  # 2ms, 4ms, ... 20ms

        stats = timer.get_stats("ordered")
        assert stats["count"] == 10
        assert stats["p50_ms"] > 0
        assert stats["p95_ms"] >= stats["p50_ms"]
        assert stats["p99_ms"] >= stats["p50_ms"]

    def test_get_stats_empty_for_unknown(self):
        from navig.storage.query_timer import QueryTimer

        timer = QueryTimer()
        assert timer.get_stats("nonexistent") == {}

    def test_slow_query_logging(self):
        from navig.storage.query_timer import QueryTimer

        timer = QueryTimer(slow_threshold_ms=0.01)  # Very low threshold
        with timer.track("slow.op"):
            time.sleep(0.01)

        slow = timer.get_slow_queries()
        assert len(slow) >= 1
        assert slow[0]["label"] == "slow.op"


# ═══════════════════════════════════════════════════════════════
# WriteBatcher
# ═══════════════════════════════════════════════════════════════


class TestWriteBatcher:
    def test_count_flush(self, tmp_path):
        from navig.storage.write_batcher import WriteBatcher

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE items (id INTEGER)")

        lock = threading.Lock()
        batcher = WriteBatcher(
            get_conn=lambda: conn,
            lock=lock,
            batch_size=5,
            flush_interval_ms=10000,  # Very long — won't trigger
        )

        # Enqueue 5 items — should trigger count-based flush
        for i in range(5):
            batcher.enqueue("INSERT INTO items VALUES (?)", (i,))

        # Give a moment for flush
        time.sleep(0.1)

        rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert rows == 5

        stats = batcher.get_stats()
        assert stats["flushed"] == 5
        assert stats["flush_count"] >= 1
        batcher.close()
        conn.close()

    def test_manual_flush(self, tmp_path):
        """Verify flush() commits all pending writes when called explicitly."""
        from navig.storage.write_batcher import WriteBatcher

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE items (id INTEGER)")

        lock = threading.Lock()
        batcher = WriteBatcher(
            get_conn=lambda: conn,
            lock=lock,
            batch_size=1000,
            flush_interval_ms=60000,
        )

        batcher.enqueue("INSERT INTO items VALUES (?)", (42,))
        assert batcher.pending == 1

        count = batcher.flush()
        assert count == 1
        assert batcher.pending == 0

        rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert rows == 1
        batcher.close()
        conn.close()

    def test_enqueue_many(self, tmp_path):
        from navig.storage.write_batcher import WriteBatcher

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE items (id INTEGER)")

        lock = threading.Lock()
        batcher = WriteBatcher(
            get_conn=lambda: conn,
            lock=lock,
            batch_size=100,
            flush_interval_ms=60000,
        )

        batcher.enqueue_many("INSERT INTO items VALUES (?)", [(i,) for i in range(10)])

        count = batcher.flush()
        assert count == 10

        rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert rows == 10
        batcher.close()
        conn.close()

    def test_close_flushes_remaining(self, tmp_path):
        from navig.storage.write_batcher import WriteBatcher

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute("CREATE TABLE items (id INTEGER)")

        lock = threading.Lock()
        batcher = WriteBatcher(
            get_conn=lambda: conn,
            lock=lock,
            batch_size=1000,  # Won't count-trigger
            flush_interval_ms=60000,  # Won't time-trigger
        )

        batcher.enqueue("INSERT INTO items VALUES (?)", (1,))
        batcher.enqueue("INSERT INTO items VALUES (?)", (2,))

        # close() should flush remaining
        batcher.close()

        rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert rows == 2
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Prepared Statement Cache
# ═══════════════════════════════════════════════════════════════


class TestStmtCache:
    def test_cache_hit(self, tmp_path):
        from navig.storage.engine import _StmtCache

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()

        cache = _StmtCache(conn, max_size=10)
        c1 = cache.execute("SELECT * FROM t WHERE id = ?", (1,))
        c2 = cache.execute("SELECT * FROM t WHERE id = ?", (2,))

        # Same cursor object should be reused
        assert c1 is c2

        cache.clear()
        conn.close()

    def test_cache_eviction(self, tmp_path):
        from navig.storage.engine import _StmtCache

        db = _make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()

        cache = _StmtCache(conn, max_size=2)
        cache.execute("SELECT 1", ())
        cache.execute("SELECT 2", ())
        cache.execute("SELECT 3", ())  # Should evict oldest

        assert len(cache._cache) <= 2
        cache.clear()
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Module Singleton
# ═══════════════════════════════════════════════════════════════


class TestModuleSingleton:
    def test_get_engine_returns_same_instance(self):
        from navig.storage import get_engine
        import navig.storage as storage_mod

        # Reset the module-level singleton for test isolation
        storage_mod._engine = None

        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2

        # Cleanup
        e1.close_all()
        storage_mod._engine = None


# ═══════════════════════════════════════════════════════════════
# Integration: Engine + BaseStore
# ═══════════════════════════════════════════════════════════════


class TestEngineBaseStoreIntegration:
    """Verify that BaseStore correctly delegates to Engine."""

    def test_base_store_uses_engine(self, tmp_path):
        from navig.store.base import BaseStore

        class SimpleStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")

        store = SimpleStore(tmp_path / "test.db")

        # Write and read
        store._write("INSERT INTO items VALUES (?, ?)", (1, "hello"))
        row = store._read_one("SELECT name FROM items WHERE id = 1")
        assert row[0] == "hello"

        # Engine should be injected
        assert store._engine is not None
        store.close()

    def test_base_store_write_many(self, tmp_path):
        from navig.store.base import BaseStore

        class SimpleStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)")

        store = SimpleStore(tmp_path / "test.db")
        count = store._write_many(
            "INSERT INTO items VALUES (?)",
            [(i,) for i in range(100)],
        )
        assert count == 100

        total = store._read_one("SELECT COUNT(*) FROM items")[0]
        assert total == 100
        store.close()

    def test_base_store_maintenance(self, tmp_path):
        from navig.store.base import BaseStore

        class SimpleStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)")

        store = SimpleStore(tmp_path / "test.db")
        result = store.maintenance()
        assert result["integrity"] == "ok"
        assert "duration_ms" in result
        store.close()

    def test_base_store_backup(self, tmp_path):
        from navig.store.base import BaseStore

        class SimpleStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, val TEXT)")

        store = SimpleStore(tmp_path / "test.db")
        store._write("INSERT INTO items VALUES (?, ?)", (1, "backup_test"))

        dest = tmp_path / "backup.db"
        store.backup(dest)
        assert dest.exists()

        bconn = sqlite3.connect(str(dest))
        row = bconn.execute("SELECT val FROM items WHERE id=1").fetchone()
        assert row[0] == "backup_test"
        bconn.close()
        store.close()

    def test_begin_immediate_serialization(self, tmp_path):
        """Verify writes use BEGIN IMMEDIATE (no SQLITE_BUSY between BEGIN and first write)."""
        from navig.store.base import BaseStore

        class SimpleStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)")

        store = SimpleStore(tmp_path / "test.db")

        errors = []

        def writer(start, count):
            for i in range(count):
                try:
                    store._write("INSERT INTO items VALUES (?)", (start + i,))
                except Exception as e:
                    errors.append(e)

        # Run concurrent writers
        threads = [
            threading.Thread(target=writer, args=(0, 50)),
            threading.Thread(target=writer, args=(50, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        total = store._read_one("SELECT COUNT(*) FROM items")[0]
        assert total == 100
        store.close()

    def test_custom_pragmas_override(self, tmp_path):
        """Verify subclass PRAGMAS are applied on top of profile."""
        from navig.store.base import BaseStore

        class CustomStore(BaseStore):
            SCHEMA_VERSION = 1
            PRAGMAS = {"cache_size": -2000}  # 2 MB override

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")

        store = CustomStore(tmp_path / "test.db")
        conn = store._get_conn()
        cache = conn.execute("PRAGMA cache_size").fetchone()[0]
        assert cache == -2000
        store.close()
