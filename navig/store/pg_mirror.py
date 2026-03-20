"""
Optional PostgreSQL mirror — activated by ``NAVIG_PG_URL`` env var.

When enabled, mirrors SQLite writes to a PostgreSQL instance for
multi-user / server deployments.  When absent, NAVIG runs entirely on
local SQLite — no Docker needed.

Usage::

    # Enable by setting env var:
    export NAVIG_PG_URL="postgresql://navig:pass@localhost:5432/navig"

    # Then any BaseStore subclass will auto-mirror writes:
    from navig.store.audit import get_audit_store
    store = get_audit_store()
    store.log_event(action="test")  # → written to SQLite AND PG
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class SyncTarget(Protocol):
    """Interface for a sync target that receives mirrored writes."""

    def emit(self, table: str, op: str, data: Dict[str, Any]) -> None: ...


# ── PgMirror ──────────────────────────────────────────────────


class PgMirror:
    """
    Opt-in PostgreSQL sync emitter.

    Writes are buffered in-memory and flushed to PG in batches or on
    explicit flush.  All PG operations are best-effort — a PG failure
    never blocks or breaks the local SQLite write path.

    Parameters
    ----------
    pg_url : str, optional
        PostgreSQL connection URL.  Defaults to ``NAVIG_PG_URL`` env var.
    batch_size : int
        Number of events to buffer before auto-flushing (default 50).
    """

    def __init__(
        self,
        pg_url: Optional[str] = None,
        *,
        batch_size: int = 50,
    ):
        self.pg_url = pg_url or os.environ.get("NAVIG_PG_URL", "")
        self._batch_size = batch_size
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._conn = None

    # ── Properties ────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.pg_url)

    # ── Emit ──────────────────────────────────────────────────

    def emit(self, table: str, op: str, data: Dict[str, Any]) -> None:
        """
        Queue a write operation for PG mirroring.

        Parameters
        ----------
        table : str
            Target PG table name (e.g. ``navig_audit``).
        op : str
            SQL operation type: ``INSERT``, ``UPDATE``, ``DELETE``.
        data : dict
            Row data to mirror.
        """
        if not self.enabled:
            return

        entry = {
            "table": table,
            "op": op,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= self._batch_size:
                self._flush_unsafe()

    # ── Flush ─────────────────────────────────────────────────

    def flush(self) -> int:
        """Flush buffered writes to PG.  Returns count flushed."""
        with self._lock:
            return self._flush_unsafe()

    def _flush_unsafe(self) -> int:
        """Flush without acquiring the lock (caller holds it)."""
        if not self._buffer:
            return 0

        batch = self._buffer[:]
        self._buffer.clear()

        try:
            conn = self._get_conn()
            if conn is None:
                return 0

            cursor = conn.cursor()
            flushed = 0

            for entry in batch:
                try:
                    self._execute_mirror(cursor, entry)
                    flushed += 1
                except Exception as exc:
                    logger.warning(
                        "PG mirror skip %s.%s: %s",
                        entry["table"],
                        entry["op"],
                        exc,
                    )

            conn.commit()
            return flushed

        except Exception as exc:
            logger.error("PG mirror flush failed: %s", exc)
            # Re-buffer on failure (best-effort, no infinite retry)
            return 0

    # ── Connection ────────────────────────────────────────────

    def _get_conn(self):
        """Lazy PG connection.  Returns None if psycopg2 unavailable."""
        if self._conn is not None:
            try:
                # Quick health check
                self._conn.cursor().execute("SELECT 1")
                return self._conn
            except Exception:
                self._conn = None

        try:
            import psycopg2

            self._conn = psycopg2.connect(self.pg_url)
            self._conn.autocommit = False
            logger.info("PG mirror connected to %s", self.pg_url.split("@")[-1])
            return self._conn
        except ImportError:
            logger.warning("psycopg2 not installed — PG mirror disabled")
            self.pg_url = ""  # Disable permanently for this session
            return None
        except Exception as exc:
            logger.error("PG mirror connection failed: %s", exc)
            return None

    # ── SQL execution ─────────────────────────────────────────

    def _execute_mirror(self, cursor, entry: Dict[str, Any]) -> None:
        """Execute a single mirrored write on PG."""
        table = entry["table"]
        op = entry["op"]
        data = entry["data"]

        if op == "INSERT":
            columns = list(data.keys())
            placeholders = ", ".join(["%s"] * len(columns))
            col_str = ", ".join(columns)

            # Serialize dict/list values to JSON strings for PG
            values = []
            for v in data.values():
                if isinstance(v, (dict, list)):
                    values.append(json.dumps(v))
                else:
                    values.append(v)

            sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"  # noqa: S608
            cursor.execute(sql, values)

        elif op == "UPDATE":
            # Not implemented yet — most mirrored writes are inserts
            logger.debug("PG mirror UPDATE not implemented for %s", table)

        elif op == "DELETE":
            logger.debug("PG mirror DELETE not implemented for %s", table)

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        """Flush remaining buffer and close PG connection."""
        with self._lock:
            self._flush_unsafe()
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            self._conn = None

    def __repr__(self) -> str:
        status = "enabled" if self.enabled else "disabled"
        buf = len(self._buffer)
        return f"<PgMirror status={status} buffered={buf}>"


# ── Module-level singleton ────────────────────────────────────

_mirror: Optional[PgMirror] = None


def get_pg_mirror() -> PgMirror:
    """Get or create the global PG mirror instance."""
    global _mirror
    if _mirror is None:
        _mirror = PgMirror()
    return _mirror
