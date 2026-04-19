"""
ThreadStore — Conversation thread persistence for the unified messaging layer.

Tracks per-adapter conversation threads independently of the gateway
session system.  Each thread binds a ``(adapter, remote_conversation_id)``
pair and optionally links to a contact alias.

Backed by :class:`BaseStore` for WAL, schema versioning, and write
serialisation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from navig.messaging.adapter import Thread
from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)


class ThreadStore(BaseStore):
    """
    Conversation thread store for multi-adapter messaging.

    Usage::

        store = ThreadStore()
        thread = store.get_or_create("whatsapp", "chat-12345", contact_alias="alice")
        store.touch(thread.id)
        store.close_thread(thread.id)
    """

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -2000}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS threads (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter                 TEXT NOT NULL,
                remote_conversation_id  TEXT NOT NULL,
                contact_alias           TEXT,
                status                  TEXT NOT NULL DEFAULT 'open',
                created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                last_active             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                meta_json               TEXT DEFAULT '{}',

                UNIQUE(adapter, remote_conversation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_thread_adapter_remote
                ON threads (adapter, remote_conversation_id);
            CREATE INDEX IF NOT EXISTS idx_thread_alias
                ON threads (contact_alias) WHERE contact_alias IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_thread_status
                ON threads (status);
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        pass  # v1 is initial

    # ── Row conversion ────────────────────────────────────────

    @staticmethod
    def _row_to_thread(row: sqlite3.Row) -> Thread:
        return Thread(
            id=row["id"],
            adapter=row["adapter"],
            remote_conversation_id=row["remote_conversation_id"],
            contact_alias=row["contact_alias"],
            status=row["status"],
            created_at=row["created_at"] or "",
            last_active=row["last_active"] or "",
            meta=json.loads(row["meta_json"] or "{}"),
        )

    # ── Core operations ───────────────────────────────────────

    def get_or_create(
        self,
        adapter: str,
        remote_conversation_id: str,
        *,
        contact_alias: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Thread:
        """Return existing thread or create one."""
        row = self._read_one(
            "SELECT * FROM threads WHERE adapter = ? AND remote_conversation_id = ?",
            (adapter, remote_conversation_id),
        )
        if row:
            return self._row_to_thread(row)

        now = _utcnow()
        meta_json = json.dumps(meta or {})
        self._write(
            "INSERT OR IGNORE INTO threads "
            "(adapter, remote_conversation_id, contact_alias, status, "
            "created_at, last_active, meta_json) VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (adapter, remote_conversation_id, contact_alias, now, now, meta_json),
        )
        # Re-read (handles the IGNORE case for concurrent creation)
        row = self._read_one(
            "SELECT * FROM threads WHERE adapter = ? AND remote_conversation_id = ?",
            (adapter, remote_conversation_id),
        )
        return self._row_to_thread(row)  # type: ignore[arg-type]

    def get_by_id(self, thread_id: int) -> Thread | None:
        """Get a thread by its local ID."""
        row = self._read_one("SELECT * FROM threads WHERE id = ?", (thread_id,))
        return self._row_to_thread(row) if row else None

    def touch(self, thread_id: int) -> None:
        """Update ``last_active`` timestamp."""
        self._write(
            "UPDATE threads SET last_active = ? WHERE id = ?",
            (_utcnow(), thread_id),
        )

    def close_thread(self, thread_id: int) -> bool:
        """Mark thread as closed."""
        cursor = self._write(
            "UPDATE threads SET status = 'closed', last_active = ? WHERE id = ? AND status = 'open'",
            (_utcnow(), thread_id),
        )
        return cursor.rowcount > 0

    def reopen_thread(self, thread_id: int) -> bool:
        """Reopen a closed thread."""
        cursor = self._write(
            "UPDATE threads SET status = 'open', last_active = ? WHERE id = ? AND status = 'closed'",
            (_utcnow(), thread_id),
        )
        return cursor.rowcount > 0

    def link_contact(self, thread_id: int, contact_alias: str) -> bool:
        """Link a thread to a contact alias."""
        cursor = self._write(
            "UPDATE threads SET contact_alias = ?, last_active = ? WHERE id = ?",
            (contact_alias, _utcnow(), thread_id),
        )
        return cursor.rowcount > 0

    # ── Queries ───────────────────────────────────────────────

    def list_threads(
        self,
        *,
        adapter: str | None = None,
        status: str | None = None,
        contact_alias: str | None = None,
        limit: int = 100,
    ) -> list[Thread]:
        """List threads with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if adapter:
            clauses.append("adapter = ?")
            params.append(adapter)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if contact_alias:
            clauses.append("contact_alias = ?")
            params.append(contact_alias)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._read_all(
            f"SELECT * FROM threads {where} ORDER BY last_active DESC LIMIT ?",
            tuple(params),
        )
        return [self._row_to_thread(r) for r in rows]

    def find_by_contact(self, contact_alias: str) -> list[Thread]:
        """Find all threads for a contact alias."""
        rows = self._read_all(
            "SELECT * FROM threads WHERE contact_alias = ? ORDER BY last_active DESC",
            (contact_alias,),
        )
        return [self._row_to_thread(r) for r in rows]

    def count(self, *, status: str | None = None) -> int:
        """Count threads, optionally filtered by status."""
        if status:
            row = self._read_one("SELECT COUNT(*) AS cnt FROM threads WHERE status = ?", (status,))
        else:
            row = self._read_one("SELECT COUNT(*) AS cnt FROM threads")
        return row["cnt"] if row else 0

    def update_meta(self, thread_id: int, meta: dict[str, Any]) -> bool:
        """Merge new keys into thread meta."""
        row = self._read_one("SELECT meta_json FROM threads WHERE id = ?", (thread_id,))
        if not row:
            return False
        existing = json.loads(row["meta_json"] or "{}")
        existing.update(meta)
        cursor = self._write(
            "UPDATE threads SET meta_json = ?, last_active = ? WHERE id = ?",
            (json.dumps(existing), _utcnow(), thread_id),
        )
        return cursor.rowcount > 0


# ── Singleton ─────────────────────────────────────────────────

_store: ThreadStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "threads.db"


def get_thread_store() -> ThreadStore:
    """Return the global :class:`ThreadStore` singleton."""
    global _store
    if _store is None:
        _store = ThreadStore()
    return _store
