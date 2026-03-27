"""
navig.inbox.store — SQLite persistence for inbox events and routing decisions.

Tables
------
inbox_events        — one row per file/URL that enters the inbox
routing_decisions   — one row per routing action applied to an event
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Runtime path helper (avoids circular imports)
_DEFAULT_DB: Path | None = None


def _inbox_db() -> Path:
    """Return the path to the inbox SQLite database."""
    global _DEFAULT_DB
    if _DEFAULT_DB is not None:
        return _DEFAULT_DB
    try:
        from navig.platform.paths import navig_data_dir

        return navig_data_dir() / "inbox.db"
    except Exception:
        return Path.home() / ".navig" / "runtime" / "inbox.db"


# ── Dataclasses ──────────────────────────────────────────────


@dataclass
class InboxEvent:
    id: int | None = None
    created_at: float = field(default_factory=time.time)
    source_path: str = ""  # original path or URL
    source_type: str = "file"  # "file" | "url" | "telegram"
    filename: str = ""
    size_bytes: int = 0
    content_hash: str = ""  # sha256 of content (empty if not computed)
    status: str = "pending"  # pending | routed | ignored | error
    error: str | None = None
    metadata: str = "{}"  # JSON blob


@dataclass
class RoutingDecision:
    id: int | None = None
    event_id: int = 0
    decided_at: float = field(default_factory=time.time)
    category: str = ""  # wiki/knowledge, technical, hub, external …
    confidence: float = 0.0
    mode: str = "copy"  # copy | move | link
    destination: str = ""
    conflict_strategy: str = "rename"  # rename | skip | overwrite
    executed: bool = False
    result_path: str | None = None
    error: str | None = None
    classifier: str = "bm25"  # bm25 | llm | manual


# ── Schema SQL ───────────────────────────────────────────────

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS inbox_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at   REAL    NOT NULL DEFAULT (unixepoch('now')),
        source_path  TEXT    NOT NULL,
        source_type  TEXT    NOT NULL DEFAULT 'file',
        filename     TEXT    NOT NULL DEFAULT '',
        size_bytes   INTEGER NOT NULL DEFAULT 0,
        content_hash TEXT    NOT NULL DEFAULT '',
        status       TEXT    NOT NULL DEFAULT 'pending',
        error        TEXT,
        metadata     TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS routing_decisions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id          INTEGER NOT NULL REFERENCES inbox_events(id),
        decided_at        REAL    NOT NULL DEFAULT (unixepoch('now')),
        category          TEXT    NOT NULL DEFAULT '',
        confidence        REAL    NOT NULL DEFAULT 0.0,
        mode              TEXT    NOT NULL DEFAULT 'copy',
        destination       TEXT    NOT NULL DEFAULT '',
        conflict_strategy TEXT    NOT NULL DEFAULT 'rename',
        executed          INTEGER NOT NULL DEFAULT 0,
        result_path       TEXT,
        error             TEXT,
        classifier        TEXT    NOT NULL DEFAULT 'bm25'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_status    ON inbox_events(status)",
    "CREATE INDEX IF NOT EXISTS idx_events_created   ON inbox_events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_event  ON routing_decisions(event_id)",
]


# ── Store ────────────────────────────────────────────────────


class InboxStore:
    """
    Thread-safe SQLite store for inbox events and routing decisions.

    Uses raw sqlite3 (not the storage Engine) for simplicity —
    inbox is a lightweight side channel, not mission-critical state.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _inbox_db()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                timeout=5.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()

    # ── Event CRUD ──────────────────────────────────

    def insert_event(self, event: InboxEvent) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO inbox_events
                (created_at, source_path, source_type, filename,
                 size_bytes, content_hash, status, error, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.created_at,
                event.source_path,
                event.source_type,
                event.filename,
                event.size_bytes,
                event.content_hash,
                event.status,
                event.error,
                event.metadata,
            ),
        )
        conn.commit()
        event.id = cur.lastrowid
        return cur.lastrowid  # type: ignore[return-value]

    def update_event_status(self, event_id: int, status: str, error: str | None = None) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE inbox_events SET status=?, error=? WHERE id=?",
            (status, error, event_id),
        )
        conn.commit()

    def get_event(self, event_id: int) -> InboxEvent | None:
        row = (
            self._connect().execute("SELECT * FROM inbox_events WHERE id=?", (event_id,)).fetchone()
        )
        return _row_to_event(row) if row else None

    def list_events(
        self,
        status: str | None = None,
        limit: int = 200,
    ) -> list[InboxEvent]:
        q = "SELECT * FROM inbox_events"
        params: tuple = ()
        if status:
            q += " WHERE status=?"
            params = (status,)
        q += " ORDER BY created_at DESC LIMIT ?"
        params = params + (limit,)
        rows = self._connect().execute(q, params).fetchall()
        return [_row_to_event(r) for r in rows]

    # ── Decision CRUD ────────────────────────────────

    def insert_decision(self, decision: RoutingDecision) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO routing_decisions
                (event_id, decided_at, category, confidence, mode,
                 destination, conflict_strategy, executed,
                 result_path, error, classifier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.event_id,
                decision.decided_at,
                decision.category,
                decision.confidence,
                decision.mode,
                decision.destination,
                decision.conflict_strategy,
                int(decision.executed),
                decision.result_path,
                decision.error,
                decision.classifier,
            ),
        )
        conn.commit()
        decision.id = cur.lastrowid
        return cur.lastrowid  # type: ignore[return-value]

    def mark_decision_executed(
        self, decision_id: int, result_path: str | None, error: str | None = None
    ) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE routing_decisions SET executed=1, result_path=?, error=? WHERE id=?",
            (result_path, error, decision_id),
        )
        conn.commit()

    def decisions_for_event(self, event_id: int) -> list[RoutingDecision]:
        rows = (
            self._connect()
            .execute(
                "SELECT * FROM routing_decisions WHERE event_id=? ORDER BY decided_at",
                (event_id,),
            )
            .fetchall()
        )
        return [_row_to_decision(r) for r in rows]

    # ── Stats ────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM inbox_events").fetchone()[0]
        by_status = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM inbox_events GROUP BY status"
            ).fetchall()
        }
        by_category = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT category, COUNT(*) FROM routing_decisions GROUP BY category"
            ).fetchall()
        }
        return {
            "total_events": total,
            "by_status": by_status,
            "by_category": by_category,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ── Helpers ──────────────────────────────────────────────────


def _row_to_event(row: sqlite3.Row) -> InboxEvent:
    d = dict(row)
    return InboxEvent(**d)


def _row_to_decision(row: sqlite3.Row) -> RoutingDecision:
    d = dict(row)
    d["executed"] = bool(d["executed"])
    return RoutingDecision(**d)
