"""NAVIG Vault Storage — SQLite backend with per-item DEK schema.

New vault DB: ~/.navig/vault/vault.db  (separate from old credentials/vault.db)
Uses the CryptoEngine for all encryption — this module is crypto-agnostic.

Schema
------
vault_items  : encrypted items (DEK + payload blobs)
vault_audit  : append-only event log per item
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .types import VaultItem, VaultItemKind

__all__ = ["VaultStore"]

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vault_items (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    label           TEXT NOT NULL,
    provider        TEXT,
    encrypted_dek   BLOB NOT NULL,
    encrypted_blob  BLOB NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_vault_label  ON vault_items(label);
CREATE INDEX IF NOT EXISTS idx_vault_provider        ON vault_items(provider);
CREATE INDEX IF NOT EXISTS idx_vault_kind            ON vault_items(kind);

CREATE TABLE IF NOT EXISTS vault_audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  TEXT    NOT NULL,
    action   TEXT    NOT NULL,
    actor    TEXT    NOT NULL DEFAULT 'user',
    ts       TEXT    NOT NULL,
    detail   TEXT    NOT NULL DEFAULT ''
);
"""


class VaultStore:
    """SQLite-backed storage for :class:`~navig.vault.types.VaultItem` objects.

    Parameters
    ----------
    vault_dir : Path
        Directory that contains (or will contain) ``vault.db``.
        Created automatically on first write.
    """

    DB_FILE = "vault.db"

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self._db_path = vault_dir / self.DB_FILE
        self._conn: Optional[sqlite3.Connection] = None

    # ── Connection ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we use explicit transactions
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_CREATE_SQL)
            self._conn = conn
        return self._conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── CRUD ────────────────────────────────────────────────────────────────

    def upsert(self, item: VaultItem) -> None:
        """Insert or update a vault item."""
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO vault_items
                    (id, kind, label, provider, encrypted_dek, encrypted_blob,
                     metadata_json, created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind           = excluded.kind,
                    label          = excluded.label,
                    provider       = excluded.provider,
                    encrypted_dek  = excluded.encrypted_dek,
                    encrypted_blob = excluded.encrypted_blob,
                    metadata_json  = excluded.metadata_json,
                    updated_at     = excluded.updated_at,
                    version        = excluded.version
                """,
                (
                    item.id,
                    item.kind.value,
                    item.label,
                    item.provider,
                    item.encrypted_dek,
                    item.encrypted_blob,
                    json.dumps(item.metadata),
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.version,
                ),
            )

    def get(self, label: str) -> Optional[VaultItem]:
        """Retrieve an item by label (exact match)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM vault_items WHERE label = ?", (label,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def get_by_id(self, item_id: str) -> Optional[VaultItem]:
        """Retrieve an item by its UUID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM vault_items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def list(
        self,
        kind: Optional[VaultItemKind] = None,
        provider: Optional[str] = None,
    ) -> list[VaultItem]:
        """List items, optionally filtered by kind and/or provider."""
        conn = self._connect()
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM vault_items {where} ORDER BY label", params
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def search(self, query: str) -> list[VaultItem]:
        """Full-text search over label and provider fields."""
        conn = self._connect()
        pat = f"%{query}%"
        rows = conn.execute(
            "SELECT * FROM vault_items WHERE label LIKE ? OR provider LIKE ? ORDER BY label",
            (pat, pat),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def delete(self, label: str) -> bool:
        """Delete item by label.  Returns True if a row was deleted."""
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM vault_items WHERE label = ?", (label,))
            return cur.rowcount > 0

    def count(self) -> int:
        """Total number of items in the vault."""
        return self._connect().execute("SELECT COUNT(*) FROM vault_items").fetchone()[0]

    # ── Audit log ────────────────────────────────────────────────────────────

    def audit(
        self,
        item_id: str,
        action: str,
        actor: str = "user",
        detail: str = "",
    ) -> None:
        """Append an audit entry for an item."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO vault_audit (item_id, action, actor, ts, detail) VALUES (?,?,?,?,?)",
                (
                    item_id,
                    action,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    detail,
                ),
            )

    def get_audit(self, item_id: str) -> list[dict]:
        """Return audit log for a specific item, newest first."""
        rows = (
            self._connect()
            .execute(
                "SELECT * FROM vault_audit WHERE item_id = ? ORDER BY ts DESC",
                (item_id,),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def db_path(self) -> Path:
        return self._db_path

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> VaultItem:
        return VaultItem(
            id=row["id"],
            kind=VaultItemKind(row["kind"]),
            label=row["label"],
            provider=row["provider"],
            encrypted_dek=row["encrypted_dek"],
            encrypted_blob=row["encrypted_blob"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
        )
