"""
Matrix Store — SQLite-backed persistent storage for Matrix state.

Stores room memberships, event history, bridge configurations,
and device trust cache. Follows the same pattern as
``navig.memory.storage`` and ``navig.memory.conversation``.

DB path: ``~/.navig/matrix.db`` (CLI) or ``{storage_dir}/matrix.db`` (gateway).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.store.base import BaseStore

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3


# ── Data classes ──────────────────────────────────────────────


@dataclass
class MatrixRoom:
    """A known Matrix room."""

    room_id: str
    alias: str = ""
    name: str = ""
    topic: str = ""
    purpose: str = "general"  # general | notifications | alerts | bridge
    encrypted: bool = False
    joined_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "alias": self.alias,
            "name": self.name,
            "topic": self.topic,
            "purpose": self.purpose,
            "encrypted": self.encrypted,
            "joined_at": self.joined_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MatrixRoom":
        return cls(
            room_id=row["room_id"],
            alias=row["alias"] or "",
            name=row["name"] or "",
            topic=row["topic"] or "",
            purpose=row["purpose"] or "general",
            encrypted=bool(row["encrypted"]),
            joined_at=row["joined_at"] or "",
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


@dataclass
class MatrixEvent:
    """A logged Matrix event."""

    event_id: str
    room_id: str
    sender: str
    event_type: str
    content: Dict[str, Any] = field(default_factory=dict)
    origin_ts: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "room_id": self.room_id,
            "sender": self.sender,
            "event_type": self.event_type,
            "content": self.content,
            "origin_ts": self.origin_ts,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MatrixEvent":
        return cls(
            event_id=row["event_id"],
            room_id=row["room_id"],
            sender=row["sender"],
            event_type=row["event_type"],
            content=json.loads(row["content"]) if row["content"] else {},
            origin_ts=row["origin_ts"] or 0,
            created_at=row["created_at"] or "",
        )


@dataclass
class MatrixBridge:
    """A bridge mapping between a Matrix room and another channel."""

    id: int = 0
    room_id: str = ""
    bridge_type: str = ""  # telegram | deck | webhook
    config: Dict[str, Any] = field(default_factory=dict)
    active: bool = True
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "room_id": self.room_id,
            "bridge_type": self.bridge_type,
            "config": self.config,
            "active": self.active,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MatrixBridge":
        return cls(
            id=row["id"],
            room_id=row["room_id"],
            bridge_type=row["bridge_type"],
            config=json.loads(row["config"]) if row["config"] else {},
            active=bool(row["active"]),
            created_at=row["created_at"] or "",
        )


# ── Store ─────────────────────────────────────────────────────


class MatrixStore(BaseStore):
    """
    SQLite-backed storage for Matrix rooms, events, and bridge state.

    Thread-safe with WAL mode via ``BaseStore``.

    Usage::

        store = MatrixStore(Path.home() / ".navig" / "matrix.db")
        store.upsert_room(MatrixRoom(room_id="!abc:server", name="General"))
        rooms = store.list_rooms()
        store.add_event(MatrixEvent(event_id="$1", room_id="!abc:server", ...))
        events = store.get_events("!abc:server", limit=50)
    """

    SCHEMA_VERSION = 3

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".navig" / "matrix.db"
        super().__init__(Path(db_path))

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            -- Rooms the bot knows about
            CREATE TABLE IF NOT EXISTS rooms (
                room_id     TEXT PRIMARY KEY,
                alias       TEXT DEFAULT '',
                name        TEXT DEFAULT '',
                topic       TEXT DEFAULT '',
                purpose     TEXT DEFAULT 'general',
                encrypted   BOOLEAN DEFAULT 0,
                joined_at   TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            -- Event log (recent messages)
            CREATE TABLE IF NOT EXISTS events (
                event_id    TEXT PRIMARY KEY,
                room_id     TEXT NOT NULL,
                sender      TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                content     TEXT,
                origin_ts   INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_events_room_ts
                ON events(room_id, origin_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_sender
                ON events(sender);
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type);
            -- Per-room sender counting (v3)
            CREATE INDEX IF NOT EXISTS idx_events_room_sender
                ON events(room_id, sender);
            -- Time-based pruning (v3)
            CREATE INDEX IF NOT EXISTS idx_events_origin_ts
                ON events(origin_ts);

            -- Bridge mappings (Matrix <-> Telegram, Deck, webhook)
            CREATE TABLE IF NOT EXISTS bridges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id     TEXT NOT NULL,
                bridge_type TEXT NOT NULL,
                config      TEXT DEFAULT '{}',
                active      BOOLEAN DEFAULT 1,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_bridges_room
                ON bridges(room_id);

            -- Device trust cache (supplements nio's crypto store)
            CREATE TABLE IF NOT EXISTS device_trust (
                user_id     TEXT NOT NULL,
                device_id   TEXT NOT NULL,
                trust_state TEXT DEFAULT 'unset',
                verified_at TEXT,
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (user_id, device_id)
            );
        """
        )

    def _migrate(
        self, conn: sqlite3.Connection, from_version: int, to_version: int
    ) -> None:
        """Incremental schema migrations."""
        if from_version < 3:
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_events_room_sender
                    ON events(room_id, sender);
                CREATE INDEX IF NOT EXISTS idx_events_origin_ts
                    ON events(origin_ts);
            """)

    # ── Rooms ─────────────────────────────────────────────────

    def upsert_room(self, room: MatrixRoom) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """INSERT INTO rooms (room_id, alias, name, topic, purpose, encrypted, joined_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(room_id) DO UPDATE SET
                       alias=excluded.alias,
                       name=excluded.name,
                       topic=excluded.topic,
                       purpose=excluded.purpose,
                       encrypted=excluded.encrypted,
                       metadata=excluded.metadata
                """,
                (
                    room.room_id,
                    room.alias,
                    room.name,
                    room.topic,
                    room.purpose,
                    int(room.encrypted),
                    room.joined_at,
                    json.dumps(room.metadata),
                ),
            )
            conn.commit()

    def get_room(self, room_id: str) -> Optional[MatrixRoom]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        return MatrixRoom.from_row(row) if row else None

    def list_rooms(self, purpose: Optional[str] = None) -> List[MatrixRoom]:
        conn = self._get_conn()
        if purpose:
            rows = conn.execute(
                "SELECT * FROM rooms WHERE purpose = ? ORDER BY joined_at DESC", (purpose,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rooms ORDER BY joined_at DESC").fetchall()
        return [MatrixRoom.from_row(r) for r in rows]

    def remove_room(self, room_id: str) -> bool:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
            conn.commit()
            return cur.rowcount > 0

    def count_rooms(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()
        return row[0] if row else 0

    # ── Events ────────────────────────────────────────────────

    def add_event(self, event: MatrixEvent) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, room_id, sender, event_type, content, origin_ts, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.room_id,
                    event.sender,
                    event.event_type,
                    json.dumps(event.content),
                    event.origin_ts,
                    event.created_at,
                ),
            )
            conn.commit()

    def add_events_batch(self, events: List[MatrixEvent]) -> int:
        """Bulk insert events. Returns count inserted."""
        conn = self._get_conn()
        count = 0
        with self._lock:
            for ev in events:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO events
                           (event_id, room_id, sender, event_type, content, origin_ts, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ev.event_id,
                            ev.room_id,
                            ev.sender,
                            ev.event_type,
                            json.dumps(ev.content),
                            ev.origin_ts,
                            ev.created_at,
                        ),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass  # duplicate row; skip
            conn.commit()
        return count

    def get_events(
        self,
        room_id: str,
        *,
        limit: int = 50,
        since_ts: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> List[MatrixEvent]:
        conn = self._get_conn()
        query = "SELECT * FROM events WHERE room_id = ?"
        params: list = [room_id]
        if since_ts is not None:
            query += " AND origin_ts > ?"
            params.append(since_ts)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY origin_ts DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [MatrixEvent.from_row(r) for r in rows]

    def count_events(self, room_id: Optional[str] = None) -> int:
        conn = self._get_conn()
        if room_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE room_id = ?", (room_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0] if row else 0

    def count_unique_senders(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(DISTINCT sender) FROM events").fetchone()
        return row[0] if row else 0

    def prune_events(self, max_rows: int = 10000) -> int:
        """Delete oldest events beyond *max_rows*. Returns rows deleted."""
        conn = self._get_conn()
        total = self.count_events()
        if total <= max_rows:
            return 0
        to_delete = total - max_rows
        with self._lock:
            cur = conn.execute(
                """DELETE FROM events WHERE event_id IN (
                       SELECT event_id FROM events ORDER BY origin_ts ASC LIMIT ?
                   )""",
                (to_delete,),
            )
            conn.commit()
            deleted = cur.rowcount
        logger.info("MatrixStore: pruned %d events (kept %d)", deleted, max_rows)
        return deleted

    # ── Bridges ───────────────────────────────────────────────

    def add_bridge(self, bridge: MatrixBridge) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                """INSERT INTO bridges (room_id, bridge_type, config, active, created_at)
                   VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bridge.room_id,
                    bridge.bridge_type,
                    json.dumps(bridge.config),
                    int(bridge.active),
                    bridge.created_at or datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_bridges(self, room_id: Optional[str] = None) -> List[MatrixBridge]:
        conn = self._get_conn()
        if room_id:
            rows = conn.execute(
                "SELECT * FROM bridges WHERE room_id = ? ORDER BY created_at DESC",
                (room_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bridges ORDER BY created_at DESC").fetchall()
        return [MatrixBridge.from_row(r) for r in rows]

    def remove_bridge(self, bridge_id: int) -> bool:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("DELETE FROM bridges WHERE id = ?", (bridge_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── Device Trust ──────────────────────────────────────────

    def set_device_trust(
        self, user_id: str, device_id: str, trust_state: str
    ) -> None:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        verified_at = now if trust_state == "verified" else None
        with self._lock:
            conn.execute(
                """INSERT INTO device_trust (user_id, device_id, trust_state, verified_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, device_id) DO UPDATE SET
                       trust_state=excluded.trust_state,
                       verified_at=COALESCE(excluded.verified_at, device_trust.verified_at),
                       updated_at=excluded.updated_at
                """,
                (user_id, device_id, trust_state, verified_at, now),
            )
            conn.commit()

    def get_device_trust(self, user_id: str, device_id: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT trust_state FROM device_trust WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
        ).fetchone()
        return row["trust_state"] if row else None

    def list_trusted_devices(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM device_trust WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM device_trust ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Stats / Diagnostics ───────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return aggregate stats for diagnostics or webhook push."""
        return {
            "rooms": self.count_rooms(),
            "events": self.count_events(),
            "unique_senders": self.count_unique_senders(),
            "bridges": len(self.get_bridges()),
            "db_path": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
        }
