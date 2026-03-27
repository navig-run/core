"""
AuditStore — Unified audit log for all NAVIG operations.

Consolidates the PG ``navig_audit`` table and the vault ``audit_log``
into a single, local-first SQLite database at ``~/.navig/audit.db``.

Features:
- Append-only event log with structured JSON details
- Partial index on failures for fast error dashboards
- Keyset pagination (no OFFSET)
- Configurable retention (default 90 days)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)

# ── Default path ──────────────────────────────────────────────

_DEFAULT_PATH: Optional[Path] = None


def _audit_db_path() -> Path:
    """Default audit.db location."""
    return Path.home() / ".navig" / "audit.db"


class AuditStore(BaseStore):
    """
    Append-only audit event store.

    Usage::

        store = AuditStore()
        store.log_event(action="command.run", actor="user", target="host:prod",
                        details={"cmd": "ls -la"}, channel="cli")
        events = store.query_events(action="command.run", limit=50)
    """

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -4000}  # 4 MB — append-heavy, rarely read

    def __init__(self, db_path: Optional[Path] = None):
        super().__init__(db_path or _audit_db_path())

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                action      TEXT NOT NULL,
                actor       TEXT NOT NULL DEFAULT 'navig',
                target      TEXT,
                details     TEXT DEFAULT '{}',
                channel     TEXT,
                host        TEXT,
                session_id  TEXT,
                status      TEXT DEFAULT 'success',
                duration_ms INTEGER
            );

            -- Time-range scans
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON audit_events (timestamp DESC);
            -- Action filtering
            CREATE INDEX IF NOT EXISTS idx_audit_action
                ON audit_events (action, timestamp DESC);
            -- Actor filtering
            CREATE INDEX IF NOT EXISTS idx_audit_actor
                ON audit_events (actor, timestamp DESC);
            -- Host filtering
            CREATE INDEX IF NOT EXISTS idx_audit_host
                ON audit_events (host, timestamp DESC);
            -- Fast error dashboard (partial index)
            CREATE INDEX IF NOT EXISTS idx_audit_failures
                ON audit_events (timestamp DESC) WHERE status != 'success';
            -- Covering index for common dashboard query
            CREATE INDEX IF NOT EXISTS idx_audit_covering
                ON audit_events (action, timestamp DESC, status, actor);
        """
        )

    def _migrate(
        self, conn: sqlite3.Connection, from_version: int, to_version: int
    ) -> None:
        # AUDIT DECISION:
        # Is this the correct implementation? Yes — explicit step dispatch performs
        # incremental migrations and fails fast when a required step is undefined.
        # Does it break any existing callers? No — current schema versions are equal in normal flows.
        # Is there a simpler alternative? Yes, but silent no-op migration masks data risks.
        if from_version >= to_version:
            return

        for version in range(from_version, to_version):
            step_name = f"_migrate_v{version}_to_v{version + 1}"
            step = getattr(self, step_name, None)
            if not callable(step):
                raise RuntimeError(
                    f"AuditStore migration path missing: {version} -> {version + 1}. "
                    f"Implement {step_name}() before upgrading schema version."
                )
            step(conn)

    # ── Write ─────────────────────────────────────────────────

    def log_event(
        self,
        action: str,
        actor: str = "navig",
        target: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        host: Optional[str] = None,
        session_id: Optional[str] = None,
        status: str = "success",
        duration_ms: Optional[int] = None,
    ) -> int:
        """
        Record a single audit event. Returns the row id.
        """
        cursor = self._write(
            """
            INSERT INTO audit_events
                (timestamp, action, actor, target, details, channel, host, session_id, status, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utcnow(),
                action,
                actor,
                target,
                json.dumps(details) if details else "{}",
                channel,
                host,
                session_id,
                status,
                duration_ms,
            ),
        )

        # Best-effort PG mirror
        try:
            from navig.store.pg_mirror import get_pg_mirror

            mirror = get_pg_mirror()
            if mirror.enabled:
                mirror.emit(
                    "navig_audit",
                    "INSERT",
                    {
                        "action": action,
                        "actor": actor,
                        "target": target,
                        "details": details or {},
                        "channel": channel,
                        "host": host,
                        "session_id": session_id,
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return cursor.lastrowid  # type: ignore[return-value]

    def log_events_batch(self, events: List[Dict[str, Any]]) -> int:
        """
        Batch-insert multiple events in a single transaction.

        Each dict should have keys: action, actor, target, details, channel,
        host, session_id, status, duration_ms (all optional except action).
        """
        now = _utcnow()
        params = [
            (
                now,
                e["action"],
                e.get("actor", "navig"),
                e.get("target"),
                json.dumps(e.get("details", {})),
                e.get("channel"),
                e.get("host"),
                e.get("session_id"),
                e.get("status", "success"),
                e.get("duration_ms"),
            )
            for e in events
        ]
        return self._write_many(
            """
            INSERT INTO audit_events
                (timestamp, action, actor, target, details, channel, host, session_id, status, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )

    # ── Read ──────────────────────────────────────────────────

    def query_events(
        self,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        host: Optional[str] = None,
        status: Optional[str] = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Query events with optional filters and keyset pagination.
        """
        clauses = ["id > ?"]
        params: list = [after_id]

        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if host:
            clauses.append("host = ?")
            params.append(host)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where = " AND ".join(clauses)
        params.append(limit)

        rows = self._read_all(
            f"""
            SELECT id, timestamp, action, actor, target, details,
                   channel, host, session_id, status, duration_ms
            FROM audit_events
            WHERE {where}
            ORDER BY id ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    def get_failures(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get recent failures (uses partial index)."""
        rows = self._read_all(
            """
            SELECT id, timestamp, action, actor, target, details, status
            FROM audit_events
            WHERE status != 'success'
              AND timestamp > datetime('now', ? || ' hours')
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (str(-hours), limit),
        )
        return [dict(r) for r in rows]

    def count_events(
        self,
        action: Optional[str] = None,
        hours: Optional[int] = None,
    ) -> int:
        """Count events with optional filters."""
        clauses: list = []
        params: list = []

        if action:
            clauses.append("action = ?")
            params.append(action)
        if hours:
            clauses.append("timestamp > datetime('now', ? || ' hours')")
            params.append(str(-hours))

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        row = self._read_one(f"SELECT COUNT(*) FROM audit_events{where}", tuple(params))
        return row[0] if row else 0

    def get_stats(self) -> Dict[str, Any]:
        """Summary statistics for the audit store."""
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        today_count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE timestamp > datetime('now', '-24 hours')"
        ).fetchone()[0]
        failure_count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE status != 'success'"
        ).fetchone()[0]

        top_actions = conn.execute(
            """
            SELECT action, COUNT(*) as cnt
            FROM audit_events
            GROUP BY action
            ORDER BY cnt DESC
            LIMIT 5
            """
        ).fetchall()

        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "total_events": total,
            "events_24h": today_count,
            "total_failures": failure_count,
            "top_actions": [(r["action"], r["cnt"]) for r in top_actions],
            "db_size_bytes": db_size,
            "db_size_mb": round(db_size / 1024 / 1024, 2),
        }

    # ── Retention ─────────────────────────────────────────────

    def prune(self, days: int = 90) -> int:
        """Delete events older than *days*. Returns count deleted."""
        cursor = self._write(
            "DELETE FROM audit_events WHERE timestamp < datetime('now', ? || ' days')",
            (str(-days),),
        )
        return cursor.rowcount


# ── Module-level singleton ────────────────────────────────────

_store: Optional[AuditStore] = None


def get_audit_store(db_path: Optional[Path] = None) -> AuditStore:
    """Get or create the global AuditStore instance."""
    global _store
    if _store is None:
        _store = AuditStore(db_path)
    return _store
