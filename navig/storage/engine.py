"""
Engine — Central SQLite connection factory and lifecycle manager.

Responsibilities:
    1. Open connections with the correct PRAGMA profile per database
    2. Maintain a thread-local connection pool (one conn per db per thread)
    3. Register custom SQL functions (e.g. cosine_distance)
    4. Provide a prepared statement cache per connection
    5. Run schema migrations via MigrationRunner
    6. Expose per-database WriteBatcher and QueryTimer instances
    7. Schedule periodic WAL checkpoints and maintenance tasks

Design principles:
    - All stores delegate connection management to Engine
    - Engine never owns the schema — stores call engine.connect() and
      manage their own tables
    - Thread-safe: one connection per thread per database
    - Cross-platform: mmap disabled on Windows automatically
"""

from __future__ import annotations

import logging
import math
import platform
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from navig.storage.pragma_profiles import (
    PragmaProfile,
    profile_for_db,
)
from navig.storage.query_timer import QueryTimer, get_query_timer
from navig.storage.write_batcher import WriteBatcher

logger = logging.getLogger(__name__)


# ── Custom SQL functions ──────────────────────────────────────

def _sql_cosine_distance(a: bytes, b: bytes) -> Optional[float]:
    """
    Cosine distance between two float32 BLOBs.

    Registered as ``cosine_distance(a, b)`` for use in SQL queries.
    Returns 1 - cosine_similarity, or NULL on error.
    """
    import struct

    try:
        n = len(a) // 4
        va = struct.unpack(f"<{n}f", a)
        vb = struct.unpack(f"<{n}f", b)

        dot = sum(x * y for x, y in zip(va, vb))
        norm_a = math.sqrt(sum(x * x for x in va))
        norm_b = math.sqrt(sum(x * x for x in vb))

        if norm_a == 0 or norm_b == 0:
            return None
        return 1.0 - (dot / (norm_a * norm_b))
    except Exception:
        return None


def _sql_json_extract_text(json_str: Optional[str], key: str) -> Optional[str]:
    """Extract a string from a JSON object.  Registered as ``json_text(json, key)``."""
    if not json_str:
        return None
    import json

    try:
        return str(json.loads(json_str).get(key, ""))
    except Exception:
        return None


# ── Prepared statement cache ──────────────────────────────────

class _StmtCache:
    """
    LRU-ish prepared statement cache per connection.

    sqlite3 in Python doesn't expose true prepared statements, but
    we cache ``sqlite3.Cursor`` objects to avoid re-parsing SQL.
    This cache is keyed by SQL string hash.
    """

    __slots__ = ("_conn", "_cache", "_max")

    def __init__(self, conn: sqlite3.Connection, max_size: int = 128):
        self._conn = conn
        self._cache: Dict[int, sqlite3.Cursor] = {}
        self._max = max_size

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        key = hash(sql)
        cursor = self._cache.get(key)
        if cursor is None:
            cursor = self._conn.cursor()
            if len(self._cache) >= self._max:
                # Evict oldest (FIFO)
                oldest = next(iter(self._cache))
                del self._cache[oldest]
            self._cache[key] = cursor
        return cursor.execute(sql, params)

    def clear(self) -> None:
        for c in self._cache.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        self._cache.clear()


# ── Engine ────────────────────────────────────────────────────


class Engine:
    """
    Central SQLite connection factory and infrastructure provider.

    Usage::

        engine = Engine()
        conn = engine.connect(Path.home() / ".navig" / "audit.db")
        # conn has correct PRAGMAs, custom functions, and stmt cache

        # Access per-db write batcher
        batcher = engine.batcher(db_path)
        batcher.enqueue("INSERT INTO ...", (1, 2))

        # Access global query timer
        timer = engine.timer
        with timer.track("audit.query_events"):
            rows = conn.execute(sql).fetchall()
    """

    def __init__(
        self,
        *,
        query_timer: Optional[QueryTimer] = None,
        slow_threshold_ms: float = 20.0,
    ):
        self._local = threading.local()
        self._write_locks: Dict[str, threading.Lock] = {}
        self._batchers: Dict[str, WriteBatcher] = {}
        self._stmt_caches: Dict[int, _StmtCache] = {}
        self._meta_lock = threading.Lock()
        self.timer = query_timer or get_query_timer(slow_threshold_ms=slow_threshold_ms)

    # ── Connection factory ────────────────────────────────────

    def connect(
        self,
        db_path: Path,
        *,
        profile: Optional[PragmaProfile] = None,
        register_functions: bool = True,
    ) -> sqlite3.Connection:
        """
        Open (or reuse) a thread-local connection to *db_path*.

        The connection is configured with the PRAGMA profile matching
        the database filename (or the explicit *profile* override).
        Custom SQL functions (cosine_distance, json_text) are registered.

        Parameters
        ----------
        db_path : Path
            Absolute path to the SQLite database file.
        profile : PragmaProfile, optional
            Override the auto-detected PRAGMA profile.
        register_functions : bool
            Register custom SQL functions (default True).
        """
        key = str(db_path)

        if not hasattr(self._local, "conns"):
            self._local.conns = {}

        existing = self._local.conns.get(key)
        if existing is not None:
            return existing

        # Resolve profile
        if profile is None:
            profile = profile_for_db(db_path.name)

        # Ensure parent directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Open connection
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Apply PRAGMAs
        pragmas = profile.to_pragma_dict()

        # Platform-specific overrides
        if platform.system() == "Windows":
            pragmas["mmap_size"] = 0  # Unreliable on Windows with WAL

        for pragma, value in pragmas.items():
            try:
                conn.execute(f"PRAGMA {pragma}={value}")
            except sqlite3.OperationalError:
                pass  # Old SQLite — skip unsupported PRAGMAs

        # Register custom functions
        if register_functions:
            conn.create_function("cosine_distance", 2, _sql_cosine_distance)
            conn.create_function("json_text", 2, _sql_json_extract_text)

        self._local.conns[key] = conn
        logger.debug(
            "Engine: opened %s [profile=%s, thread=%s]",
            db_path.name,
            profile.name,
            threading.current_thread().name,
        )
        return conn

    # ── Write lock ────────────────────────────────────────────

    def write_lock(self, db_path: Path) -> threading.Lock:
        """Return the shared write lock for *db_path*."""
        key = str(db_path)
        with self._meta_lock:
            if key not in self._write_locks:
                self._write_locks[key] = threading.Lock()
            return self._write_locks[key]

    # ── Write batcher ─────────────────────────────────────────

    def batcher(
        self,
        db_path: Path,
        *,
        batch_size: int = 50,
        flush_interval_ms: float = 100.0,
    ) -> WriteBatcher:
        """
        Return the WriteBatcher for *db_path*, creating it on first call.

        Each database gets its own batcher with configurable thresholds.
        """
        key = str(db_path)
        with self._meta_lock:
            if key not in self._batchers:
                self._batchers[key] = WriteBatcher(
                    get_conn=lambda p=db_path: self.connect(p),
                    lock=self.write_lock(db_path),
                    batch_size=batch_size,
                    flush_interval_ms=flush_interval_ms,
                )
            return self._batchers[key]

    # ── Prepared statement cache ──────────────────────────────

    def stmt_cache(self, conn: sqlite3.Connection) -> _StmtCache:
        """Return a prepared statement cache bound to *conn*."""
        cid = id(conn)
        if cid not in self._stmt_caches:
            self._stmt_caches[cid] = _StmtCache(conn)
        return self._stmt_caches[cid]

    # ── WAL checkpoint ────────────────────────────────────────

    def checkpoint(
        self,
        db_path: Path,
        *,
        mode: str = "PASSIVE",
    ) -> Dict[str, Any]:
        """
        Run a WAL checkpoint.

        Modes:
            PASSIVE   — Checkpoint as much as possible without blocking readers.
            FULL      — Block until all WAL frames are checkpointed.
            TRUNCATE  — Like FULL, then truncate the WAL file to zero bytes.
            RESTART   — Like FULL, then restart WAL from the beginning.
        """
        conn = self.connect(db_path)
        result = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return {
            "busy": result[0],
            "log_pages": result[1],
            "checkpointed_pages": result[2],
        }

    # ── Migration runner ──────────────────────────────────────

    def run_migrations(
        self,
        db_path: Path,
        target_version: int,
        create_schema: Callable[[sqlite3.Connection], None],
        migrate: Optional[Callable[[sqlite3.Connection, int, int], None]] = None,
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Schema versioning and forward-only migration runner.

        Parameters
        ----------
        db_path : Path
            Database to migrate.
        target_version : int
            Desired schema version.
        create_schema : callable
            Called on fresh databases to create all tables/indexes.
        migrate : callable, optional
            Called with (conn, from_version, to_version) for incremental migration.
        dry_run : bool
            If True, wraps everything in a SAVEPOINT and rolls back.

        Returns
        -------
        dict with keys: action (created|migrated|current), from_version, to_version.
        """
        conn = self.connect(db_path)
        lock = self.write_lock(db_path)

        with lock:
            # Ensure version table
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY)"
            )

            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            current = row[0] if row else 0

            result: Dict[str, Any] = {
                "from_version": current,
                "to_version": target_version,
            }

            if current == 0:
                # Fresh database
                if dry_run:
                    conn.execute("SAVEPOINT migration_dry_run")
                create_schema(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (target_version,),
                )
                if dry_run:
                    conn.execute("ROLLBACK TO SAVEPOINT migration_dry_run")
                    conn.execute("RELEASE SAVEPOINT migration_dry_run")
                    result["action"] = "dry_run_create"
                else:
                    conn.commit()
                    result["action"] = "created"

            elif current < target_version:
                # Run migration
                if migrate is None:
                    raise ValueError(
                        f"Schema version {current} → {target_version} requires "
                        f"a migrate() callback but none was provided."
                    )
                if dry_run:
                    conn.execute("SAVEPOINT migration_dry_run")
                migrate(conn, current, target_version)
                conn.execute(
                    "UPDATE schema_version SET version = ?",
                    (target_version,),
                )
                if dry_run:
                    conn.execute("ROLLBACK TO SAVEPOINT migration_dry_run")
                    conn.execute("RELEASE SAVEPOINT migration_dry_run")
                    result["action"] = "dry_run_migrate"
                else:
                    conn.commit()
                    result["action"] = "migrated"
            else:
                result["action"] = "current"

            return result

    # ── Maintenance ───────────────────────────────────────────

    def maintenance(self, db_path: Path) -> Dict[str, Any]:
        """
        Run periodic maintenance on a single database.

        - PRAGMA optimize (refreshes query planner stats)
        - WAL checkpoint (PASSIVE — non-blocking)
        - ANALYZE (full stats refresh)
        - PRAGMA quick_check (fast integrity check)
        - Conditional incremental_vacuum (if auto_vacuum = INCREMENTAL)
        """
        conn = self.connect(db_path)
        results: Dict[str, Any] = {"db": db_path.name}
        start = time.perf_counter()

        conn.execute("PRAGMA optimize")
        results["optimize"] = "done"

        ckpt = self.checkpoint(db_path, mode="PASSIVE")
        results["wal_checkpoint"] = ckpt

        conn.execute("ANALYZE")
        results["analyze"] = "done"

        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        results["integrity"] = integrity

        # Size info
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        results["size_bytes"] = page_count * page_size
        results["free_bytes"] = free_pages * page_size

        # Incremental vacuum if applicable
        auto_vac = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if auto_vac == 2 and free_pages > 0:  # 2 = INCREMENTAL
            pages_to_free = min(free_pages, 1000)
            conn.execute(f"PRAGMA incremental_vacuum({pages_to_free})")
            results["incremental_vacuum"] = pages_to_free

        results["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return results

    # ── Backup ────────────────────────────────────────────────

    def backup(
        self,
        db_path: Path,
        dest: Path,
        *,
        pages_per_step: int = 256,
    ) -> Path:
        """
        Online backup using ``sqlite3.backup()`` — safe under WAL.

        Progress is done in 256-page steps to avoid blocking writers
        for extended periods.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self.connect(db_path)
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst, pages=pages_per_step)
        finally:
            dst.close()
        return dest

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self, db_path: Optional[Path] = None) -> None:
        """
        Close connection(s).

        If *db_path* is given, close only that database's connection
        in the current thread.  Otherwise close all connections in the
        current thread.
        """
        if not hasattr(self._local, "conns"):
            return

        if db_path is not None:
            key = str(db_path)
            conn = self._local.conns.pop(key, None)
            if conn:
                self._close_conn(conn, db_path)
        else:
            for key, conn in list(self._local.conns.items()):
                self._close_conn(conn, Path(key))
            self._local.conns.clear()

    def close_all(self) -> None:
        """Close all batchers and clear internal state."""
        for batcher in self._batchers.values():
            batcher.close()
        self._batchers.clear()
        for cache in self._stmt_caches.values():
            cache.clear()
        self._stmt_caches.clear()
        self.close()

    def _close_conn(self, conn: sqlite3.Connection, db_path: Path) -> None:
        """Close a connection with platform-specific WAL cleanup."""
        try:
            if platform.system() == "Windows":
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def __repr__(self) -> str:
        n_conns = len(getattr(self._local, "conns", {}))
        n_batchers = len(self._batchers)
        return f"<Engine conns={n_conns} batchers={n_batchers}>"


# ── Module-level singleton ────────────────────────────────────

_engine: Optional[Engine] = None


def get_engine(**kwargs) -> Engine:
    """Get or create the global Engine instance."""
    global _engine
    if _engine is None:
        _engine = Engine(**kwargs)
    return _engine
