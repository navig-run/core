"""CoordinationStore — durable inter-agent / inter-session messages.

This is the ONE additive primitive the collective-coordination work introduces. It is
deliberately NOT conversation history, not a task, not a notification: it is the durable,
policy-checked mailbox that lets an agent, session, mission or operator send a structured
message to another participant without corrupting any of those other stores.

It follows ``BaseStore`` (WAL, schema versioning, write serialisation) exactly like the
other NAVIG stores, and lives at ``~/.navig/data/store/coordination.db``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, utcnow

__all__ = ["CoordinationStore", "get_coordination_store", "reset_coordination_store"]

#: Delivery state progression. The store persists the string; :mod:`navig.coordination`
#: enforces the ordering so a caller cannot roll a consumed message back to delivered.
#: ``cancelled``/``failed``/``expired`` are terminal and inspectable (dead-letter).
DELIVERY_STATES = (
    "accepted",      # a caller accepted the write (not yet persisted)
    "persisted",     # durable; the row is safe to acknowledge to the producer
    "delivered",     # a recipient was notified/notified is pending
    "consumed",      # a recipient read it
    "acknowledged",  # the recipient acknowledged it
    "acted",         # the recipient acted on it (a task/delegation was created)
    "cancelled",     # terminal: a sender cancelled it
    "failed",        # terminal: delivery exhausted retries / dead-letter
    "expired",       # terminal: the message expired before it was acted on
)


def _default_db_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.store_dir() / "coordination.db"


class CoordinationStore(BaseStore):
    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -4000}

    def __init__(self, db_path: Path | None = None) -> None:
        super().__init__(db_path or _default_db_path())

    def _create_schema(self, conn) -> None:  # noqa: ANN001 — sqlite3.Connection
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS coordination_message (
                id               TEXT PRIMARY KEY,
                schema_version   INTEGER NOT NULL DEFAULT 1,
                kind             TEXT NOT NULL,
                sender           TEXT NOT NULL,
                recipient        TEXT NOT NULL,
                mission_id       TEXT,
                task_id          TEXT,
                session_id       TEXT,
                correlation_id   TEXT,
                causation_id     TEXT,
                reply_to         TEXT,
                priority         TEXT NOT NULL DEFAULT 'normal',
                content          TEXT NOT NULL DEFAULT '{}',
                evidence_refs    TEXT NOT NULL DEFAULT '[]',
                created_at       TEXT NOT NULL,
                expires_at       TEXT,
                delivery_state   TEXT NOT NULL DEFAULT 'persisted',
                retry_count      INTEGER NOT NULL DEFAULT 0,
                last_attempt_at  TEXT,
                ack_at           TEXT,
                consumed_at      TEXT,
                acted_at         TEXT,
                idempotency_key  TEXT NOT NULL,
                error            TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_coord_idem ON coordination_message(idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_coord_recipient ON coordination_message(recipient, delivery_state);
            CREATE INDEX IF NOT EXISTS idx_coord_correlation ON coordination_message(correlation_id);
            """
        )

    # ── rows ────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_dict(row) -> dict[str, Any]:  # noqa: ANN001
        def loads(text: str | None, default: Any) -> Any:
            if not text:
                return default
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return default

        return {
            "id": row["id"],
            "schema_version": row["schema_version"],
            "kind": row["kind"],
            "sender": row["sender"],
            "recipient": row["recipient"],
            "mission_id": row["mission_id"],
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "correlation_id": row["correlation_id"],
            "causation_id": row["causation_id"],
            "reply_to": row["reply_to"],
            "priority": row["priority"],
            "content": loads(row["content"], {}),
            "evidence_refs": loads(row["evidence_refs"], []),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "delivery_state": row["delivery_state"],
            "retry_count": row["retry_count"],
            "last_attempt_at": row["last_attempt_at"],
            "ack_at": row["ack_at"],
            "consumed_at": row["consumed_at"],
            "acted_at": row["acted_at"],
            "idempotency_key": row["idempotency_key"],
            "error": row["error"],
        }

    def put(self, message: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist one message. ``created`` is False when the idempotency key already existed.

        Idempotency is the store's guarantee: two producers sharing a key persist once.
        """
        mid = str(message.get("id") or uuid.uuid4().hex[:16])
        key = str(message.get("idempotency_key") or mid)
        now = utcnow()
        self._write(
            "INSERT OR IGNORE INTO coordination_message"
            "(id, schema_version, kind, sender, recipient, mission_id, task_id, session_id,"
            " correlation_id, causation_id, reply_to, priority, content, evidence_refs,"
            " created_at, expires_at, delivery_state, idempotency_key)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mid,
                int(message.get("schema_version", 1)),
                str(message.get("kind") or "inform"),
                str(message.get("sender") or ""),
                str(message.get("recipient") or ""),
                message.get("mission_id"),
                message.get("task_id"),
                message.get("session_id"),
                message.get("correlation_id"),
                message.get("causation_id"),
                message.get("reply_to"),
                str(message.get("priority") or "normal"),
                json.dumps(message.get("content") or {}, ensure_ascii=False),
                json.dumps(message.get("evidence_refs") or [], ensure_ascii=False),
                now,
                message.get("expires_at"),
                str(message.get("delivery_state") or "persisted"),
                key,
            ),
        )
        existing = self.get_by_key(key)
        if existing is not None and existing["id"] != mid:
            return existing, False
        row = self._read_one("SELECT * FROM coordination_message WHERE id = ?", (mid,))
        return self._to_dict(row), True

    def get(self, message_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM coordination_message WHERE id = ?", (message_id,))
        return self._to_dict(row) if row else None

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM coordination_message WHERE idempotency_key = ?", (key,))
        return self._to_dict(row) if row else None

    def list_inbox(
        self,
        recipient: str,
        *,
        states: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM coordination_message WHERE recipient = ?"
        params: list[Any] = [recipient]
        if states:
            sql += f" AND delivery_state IN ({','.join('?' * len(states))})"  # noqa: S608 — literals
            params.extend(states)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        return [self._to_dict(r) for r in self._read_all(sql, tuple(params))]

    def list_by_state(
        self,
        states: tuple[str, ...],
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Every message in the given states, oldest first (the delivery worker's scan)."""
        if not states:
            return []
        sql = f"SELECT * FROM coordination_message WHERE delivery_state IN ({','.join('?' * len(states))})"  # noqa: S608
        sql += " ORDER BY created_at ASC LIMIT ?"
        return [
            self._to_dict(r)
            for r in self._read_all(sql, (*states, max(1, min(int(limit), 1000))))
        ]

    def list_expired(self, *, now: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        now = now or utcnow()
        rows = self._read_all(
            "SELECT * FROM coordination_message WHERE expires_at IS NOT NULL"
            " AND expires_at <= ? AND delivery_state IN ('persisted','delivered') LIMIT ?",
            (now, max(1, min(int(limit), 1000))),
        )
        return [self._to_dict(r) for r in rows]

    def set_delivery_state(self, message_id: str, state: str, *, error: str = "") -> dict[str, Any] | None:
        """Persist a state transition and its timestamp, monotonically at the service layer."""
        stamp = {"consumed": "consumed_at", "acknowledged": "ack_at", "acted": "acted_at"}.get(state)
        if stamp:
            self._write(
                f"UPDATE coordination_message SET delivery_state = ?, {stamp} = ?, error = ? WHERE id = ?",
                (state, utcnow(), error, message_id),
            )
        else:
            self._write(
                "UPDATE coordination_message SET delivery_state = ?, error = ? WHERE id = ?",
                (state, error, message_id),
            )
        return self.get(message_id)

    def touch_retry(self, message_id: str) -> dict[str, Any] | None:
        self._write(
            "UPDATE coordination_message SET retry_count = retry_count + 1, last_attempt_at = ? WHERE id = ?",
            (utcnow(), message_id),
        )
        return self.get(message_id)

    def delete(self, message_id: str) -> bool:
        cursor = self._write("DELETE FROM coordination_message WHERE id = ?", (message_id,))
        return cursor.rowcount > 0


_store: CoordinationStore | None = None


def get_coordination_store() -> CoordinationStore:
    global _store
    if _store is None:
        _store = CoordinationStore()
    return _store


def reset_coordination_store() -> None:
    """Drop the singleton (tests, and after an isolation-env change)."""
    global _store
    _store = None
