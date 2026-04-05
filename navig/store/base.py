"""
BaseStore — Abstract base class for all NAVIG SQLite stores.

Delegates connection management, PRAGMA configuration, and write
serialisation to ``navig.storage.Engine`` for unified infrastructure.

Subclasses MUST define:
    SCHEMA_VERSION: int
    _create_schema(conn): create tables/indexes/triggers
    _migrate(conn, from_version, to_version): run incremental migrations

Subclasses MAY override:
    PRAGMAS: dict — extra PRAGMA overrides applied *after* the Engine profile

Backward compatibility:
    Existing subclasses continue to work unchanged.  ``PRAGMAS`` overrides
    are applied on top of the profile-based PRAGMAs supplied by Engine.
"""

from __future__ import annotations

import logging
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Default PRAGMAs (kept for backward-compat imports) ────────
# New code should rely on navig.storage.pragma_profiles instead.

BASE_PRAGMAS: dict[str, Any] = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "foreign_keys": "ON",
    "busy_timeout": 5000,
    "cache_size": -8000,  # 8 MB
    "temp_store": "MEMORY",
    "mmap_size": 0,  # disabled by default
}

def _utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _get_engine():
    """Lazy import to avoid circular dependency at module level."""
    from navig.storage import get_engine

    return get_engine()

class BaseStore:
    """
    Abstract SQLite store backed by :class:`navig.storage.Engine`.

    Engine handles:
    - Thread-local connections with correct PRAGMA profiles
    - Write serialisation via per-database locks
    - Custom SQL functions (cosine_distance, json_text)

    Usage::

        class MyStore(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn: sqlite3.Connection) -> None:
                conn.executescript('''CREATE TABLE IF NOT EXISTS ...''')

            def _migrate(self, conn, from_v, to_v):
                pass
    """

    SCHEMA_VERSION: int = 1
    PRAGMAS: dict[str, Any] = {}  # Extra overrides applied after profile

    def __init__(self, db_path: Path, *, engine=None):
        self.db_path = Path(db_path)

        # Delegate to the unified Engine
        self._engine = engine or _get_engine()
        self._lock = self._engine.write_lock(self.db_path)

        # Ensure directory
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialise schema
        self._init_schema()

    # ── Connection management ─────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local connection via Engine, with subclass PRAGMA overrides."""
        conn = self._engine.connect(self.db_path)

        # Apply subclass PRAGMA overrides (backward-compat)
        if self.PRAGMAS:
            overrides = dict(self.PRAGMAS)
            if platform.system() == "Windows":
                overrides.pop("mmap_size", None)
            for pragma, value in overrides.items():
                try:
                    conn.execute(f"PRAGMA {pragma}={value}")
                except sqlite3.OperationalError:
                    pass
        return conn

    # ── Schema management ─────────────────────────────────────

    def _init_schema(self) -> None:
        """Create tables if needed, then run migrations."""
        conn = self._get_conn()

        # Ensure version table exists
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
        )

        # Let subclass create its tables
        self._create_schema(conn)

        # Check current version
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = row[0] if row else 0

        if current == 0:
            # Fresh DB — stamp version
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
            conn.commit()
        elif current < self.SCHEMA_VERSION:
            # Run migrations
            self._migrate(conn, current, self.SCHEMA_VERSION)
            conn.execute(
                "UPDATE schema_version SET version = ?",
                (self.SCHEMA_VERSION,),
            )
            conn.commit()

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Subclass MUST override: create tables, indexes, triggers."""
        raise NotImplementedError

    def _migrate(
        self,
        conn: sqlite3.Connection,
        from_version: int,
        to_version: int,
    ) -> None:
        """Subclass SHOULD override: incremental schema migrations."""
        pass

    # ── Write helpers ─────────────────────────────────────────

    def _write(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> sqlite3.Cursor:
        """Execute a single write statement under BEGIN IMMEDIATE."""
        with self._lock:
            conn = self._get_conn()
            old_iso = conn.isolation_level
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(sql, params or ())
                conn.execute("COMMIT")
                return cursor
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
                raise
            finally:
                conn.isolation_level = old_iso

    def _write_many(
        self,
        sql: str,
        seq_of_params: list[tuple],
    ) -> int:
        """Execute many writes in one BEGIN IMMEDIATE transaction."""
        if not seq_of_params:
            return 0
        with self._lock:
            conn = self._get_conn()
            old_iso = conn.isolation_level
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(sql, seq_of_params)
                conn.execute("COMMIT")
                return len(seq_of_params)
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
                raise
            finally:
                conn.isolation_level = old_iso

    def _write_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script inside the write lock."""
        with self._lock:
            conn = self._get_conn()
            conn.executescript(sql)

    # ── Read helpers ──────────────────────────────────────────

    def _read_one(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> sqlite3.Row | None:
        """Execute a read and return a single row or None."""
        return self._get_conn().execute(sql, params or ()).fetchone()

    def _read_all(
        self,
        sql: str,
        params: tuple | None = None,
    ) -> list[sqlite3.Row]:
        """Execute a read and return all rows."""
        return self._get_conn().execute(sql, params or ()).fetchall()

    # ── Maintenance ───────────────────────────────────────────

    def maintenance(self) -> dict[str, Any]:
        """Run periodic maintenance via Engine."""
        return self._engine.maintenance(self.db_path)

    # ── Backup ────────────────────────────────────────────────

    def backup(self, dest: Path) -> Path:
        """Hot backup using Engine's ``sqlite3.backup()`` wrapper."""
        return self._engine.backup(self.db_path, dest)

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        """Close this database's connection via Engine."""
        self._engine.close(self.db_path)

    def get_schema_version(self) -> int:
        """Return the current schema version stored in the database."""
        row = self._read_one("SELECT version FROM schema_version LIMIT 1")
        return row[0] if row else 0

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} db={self.db_path}>"
