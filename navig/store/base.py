"""
BaseStore — Abstract base class for all NAVIG SQLite stores.

Extracts the repeated pattern from MemoryStorage, ConversationStore,
MatrixStore, and BotStatsStore:
- Thread-local connections with sqlite3.Row factory
- Write serialisation via threading.Lock
- WAL mode + configurable PRAGMAs per subclass
- Schema version tracking with migration support
- Hot backup via sqlite3.backup()
- Periodic maintenance (PRAGMA optimize, ANALYZE, checkpoint, integrity_check)

Subclasses MUST define:
    SCHEMA_VERSION: int
    _create_schema(conn): create tables/indexes/triggers
    _migrate(conn, from_version, to_version): run incremental migrations

Subclasses MAY override:
    PRAGMAS: dict — per-DB PRAGMA overrides
"""

from __future__ import annotations

import logging
import platform
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Default PRAGMAs applied to every database ────────────────

BASE_PRAGMAS: Dict[str, Any] = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "foreign_keys": "ON",
    "busy_timeout": 5000,
    "cache_size": -8000,       # 8 MB
    "temp_store": "MEMORY",
    "mmap_size": 0,            # disabled by default
}


def _utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class BaseStore:
    """
    Abstract SQLite store with WAL, thread-local connections, and schema versioning.

    Usage::

        class MyStore(BaseStore):
            SCHEMA_VERSION = 1
            PRAGMAS = {"cache_size": -16000}

            def _create_schema(self, conn: sqlite3.Connection) -> None:
                conn.executescript('''CREATE TABLE IF NOT EXISTS ...''')

            def _migrate(self, conn, from_v, to_v):
                pass
    """

    SCHEMA_VERSION: int = 1
    PRAGMAS: Dict[str, Any] = {}

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure directory
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialise schema
        self._init_schema()

    # ── Connection management ─────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating it lazily."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row

            # Merge pragmas: base → subclass overrides
            merged = {**BASE_PRAGMAS, **self.PRAGMAS}

            # Disable mmap on Windows (unreliable with file locking)
            if platform.system() == "Windows":
                merged["mmap_size"] = 0

            for pragma, value in merged.items():
                try:
                    conn.execute(f"PRAGMA {pragma}={value}")
                except sqlite3.OperationalError:
                    pass  # Ignore unsupported PRAGMAs (e.g. old SQLite)

            self._local.conn = conn

        return self._local.conn

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
        params: Optional[tuple] = None,
    ) -> sqlite3.Cursor:
        """Execute a single write statement inside the write lock."""
        with self._lock:
            conn = self._get_conn()
            with conn:
                return conn.execute(sql, params or ())

    def _write_many(
        self,
        sql: str,
        seq_of_params: List[tuple],
    ) -> int:
        """Execute many writes in one transaction. Returns row count."""
        if not seq_of_params:
            return 0
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.executemany(sql, seq_of_params)
            return len(seq_of_params)

    def _write_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script inside the write lock."""
        with self._lock:
            conn = self._get_conn()
            conn.executescript(sql)

    # ── Read helpers ──────────────────────────────────────────

    def _read_one(
        self,
        sql: str,
        params: Optional[tuple] = None,
    ) -> Optional[sqlite3.Row]:
        """Execute a read and return a single row or None."""
        return self._get_conn().execute(sql, params or ()).fetchone()

    def _read_all(
        self,
        sql: str,
        params: Optional[tuple] = None,
    ) -> List[sqlite3.Row]:
        """Execute a read and return all rows."""
        return self._get_conn().execute(sql, params or ()).fetchall()

    # ── Maintenance ───────────────────────────────────────────

    def maintenance(self) -> Dict[str, Any]:
        """
        Run periodic maintenance.

        - PRAGMA optimize (re-analyze tables that need it)
        - WAL checkpoint (reclaim disk space)
        - ANALYZE (refresh index statistics)
        - quick_check (integrity verification)
        - Conditional VACUUM if fragmentation > 20%
        """
        conn = self._get_conn()
        results: Dict[str, Any] = {"db": str(self.db_path)}

        # 1. Optimize
        conn.execute("PRAGMA optimize")
        results["optimize"] = "done"

        # 2. WAL checkpoint
        try:
            wal = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            results["wal_checkpoint"] = {
                "busy": wal[0],
                "log": wal[1],
                "checkpointed": wal[2],
            }
        except Exception as e:
            results["wal_checkpoint"] = str(e)

        # 3. Analyze
        conn.execute("ANALYZE")
        results["analyze"] = "done"

        # 4. Integrity
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        results["integrity"] = integrity

        # 5. Size info
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        results["size_bytes"] = page_count * page_size
        results["free_bytes"] = free_pages * page_size

        # 6. Conditional VACUUM
        if page_count > 0 and (free_pages / max(page_count, 1)) > 0.20:
            conn.execute("VACUUM")
            results["vacuum"] = "done"

        return results

    # ── Backup ────────────────────────────────────────────────

    def backup(self, dest: Path) -> Path:
        """
        Hot backup using sqlite3.backup() — works while writers are active.

        Args:
            dest: Destination file path.

        Returns:
            The destination path.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)

        src_conn = self._get_conn()
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn, pages=256)
        finally:
            dst_conn.close()

        return dest

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        """Close the thread-local connection (with WAL checkpoint on Windows)."""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                if platform.system() == "Windows":
                    self._local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._local.conn.close()
            self._local.conn = None

    def get_schema_version(self) -> int:
        """Return the current schema version stored in the database."""
        row = self._read_one("SELECT version FROM schema_version LIMIT 1")
        return row[0] if row else 0

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} db={self.db_path}>"
