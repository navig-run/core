"""
DeliveryTracker — Compliance-grade delivery audit log.

Records every outbound send attempt with adapter, target, status, and
compliance classification.  Backed by :class:`BaseStore` for WAL and
write serialisation.

Used by the routing engine and adapter layer to produce an immutable
delivery audit trail (no deletes, no updates except status advancement).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from navig.messaging.adapter import ComplianceMode, DeliveryReceipt, DeliveryStatus
from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)


class DeliveryTracker(BaseStore):
    """
    Immutable delivery audit log.

    Usage::

        tracker = DeliveryTracker()
        row_id = tracker.record_send(
            adapter="sms",
            target="+33612345678",
            contact_alias="alice",
            thread_id=42,
            compliance=ComplianceMode.OFFICIAL,
        )
        tracker.update_status(row_id, DeliveryStatus.DELIVERED, message_id="abc123")
    """

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -4000}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS deliveries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter         TEXT NOT NULL,
                target          TEXT NOT NULL,
                contact_alias   TEXT,
                thread_id       INTEGER,
                compliance      TEXT NOT NULL DEFAULT 'official',
                status          TEXT NOT NULL DEFAULT 'queued',
                message_id      TEXT,
                error           TEXT,
                meta_json       TEXT DEFAULT '{}',
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_delivery_adapter
                ON deliveries (adapter);
            CREATE INDEX IF NOT EXISTS idx_delivery_status
                ON deliveries (status);
            CREATE INDEX IF NOT EXISTS idx_delivery_contact
                ON deliveries (contact_alias) WHERE contact_alias IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_delivery_thread
                ON deliveries (thread_id) WHERE thread_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_delivery_created
                ON deliveries (created_at);
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        pass  # v1 is initial

    # ── Record ────────────────────────────────────────────────

    def record_send(
        self,
        adapter: str,
        target: str,
        *,
        contact_alias: str | None = None,
        thread_id: int | None = None,
        compliance: ComplianceMode = ComplianceMode.OFFICIAL,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """
        Record an outbound send attempt. Returns the row ID.

        Call this *before* the actual send; update status afterward with
        :meth:`update_status`.
        """
        now = _utcnow()
        cursor = self._write(
            "INSERT INTO deliveries "
            "(adapter, target, contact_alias, thread_id, compliance, "
            "status, meta_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                adapter,
                target,
                contact_alias,
                thread_id,
                compliance.value,
                json.dumps(meta or {}),
                now,
                now,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]

    def update_status(
        self,
        delivery_id: int,
        status: DeliveryStatus,
        *,
        message_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        """
        Advance delivery status (forward-only or to FAILED).

        Returns ``False`` if the transition is invalid or row not found.
        """
        row = self._read_one("SELECT status FROM deliveries WHERE id = ?", (delivery_id,))
        if not row:
            return False

        current = DeliveryStatus(row["status"])
        if not current.can_transition_to(status):
            logger.warning(
                "delivery_status_blocked | id=%d | %s → %s",
                delivery_id,
                current.value,
                status.value,
            )
            return False

        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, _utcnow()]
        if message_id is not None:
            sets.append("message_id = ?")
            params.append(message_id)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(delivery_id)

        cursor = self._write(
            f"UPDATE deliveries SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return cursor.rowcount > 0

    def apply_receipt(self, delivery_id: int, receipt: DeliveryReceipt) -> bool:
        """Convenience: apply a :class:`DeliveryReceipt` to a tracked delivery."""
        return self.update_status(
            delivery_id,
            receipt.status,
            message_id=receipt.message_id,
            error=receipt.error,
        )

    # ── Queries ───────────────────────────────────────────────

    def get(self, delivery_id: int) -> dict[str, Any] | None:
        """Get a delivery record by ID."""
        row = self._read_one("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
        return dict(row) if row else None

    def recent(
        self,
        *,
        adapter: str | None = None,
        contact_alias: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query recent deliveries with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if adapter:
            clauses.append("adapter = ?")
            params.append(adapter)
        if contact_alias:
            clauses.append("contact_alias = ?")
            params.append(contact_alias)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._read_all(
            f"SELECT * FROM deliveries {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        """Return status distribution counts."""
        rows = self._read_all("SELECT status, COUNT(*) AS cnt FROM deliveries GROUP BY status")
        return {r["status"]: r["cnt"] for r in rows}


# ── Singleton ─────────────────────────────────────────────────

_tracker: DeliveryTracker | None = None


def _default_db_path() -> Path:
    from navig.config import get_config

    return Path(get_config().data_dir) / "deliveries.db"


def get_delivery_tracker() -> DeliveryTracker:
    """Return the global :class:`DeliveryTracker` singleton."""
    global _tracker
    if _tracker is None:
        _tracker = DeliveryTracker()
    return _tracker
