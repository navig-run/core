"""
Conversation storage with SQLite backend.

Stores message history per session with full metadata,
token counting, and efficient retrieval.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from navig.store.base import BaseStore


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger

        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception as _e:  # noqa: BLE001
        import sys

        print(
            f"[navig/memory/conversation] logger init failed ({type(_e).__name__}): {_e}",
            file=sys.stderr,
        )


@dataclass
class Message:
    """A single conversation message."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_key: str = ""
    role: str = "user"  # user, assistant, system, tool
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)
    token_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "session_key": self.session_key,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": json.dumps(self.metadata),
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            session_key=data["session_key"],
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=(
                json.loads(data["metadata"])
                if isinstance(data["metadata"], str)
                else data["metadata"]
            ),
            token_count=data.get("token_count", 0),
        )


@dataclass
class SessionInfo:
    """Metadata about a conversation session."""

    session_key: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    total_tokens: int
    metadata: dict = field(default_factory=dict)


class ConversationStore(BaseStore):
    """
    SQLite-backed conversation storage.

    Stores messages with session keys for multi-tenant support.
    Thread-safe with connection pooling via ``BaseStore``.

    Usage:
        store = ConversationStore(Path.home() / '.navig' / 'memory.db')

        # Add message
        msg = Message(session_key='task-123', role='user', content='Hello')
        store.add_message(msg)

        # Get history
        history = store.get_history('task-123', limit=50)

        # FTS5 search (F-13)
        results = store.fts_search('error handling', limit=10)
    """

    SCHEMA_VERSION = 2  # Bumped for FTS5 support (F-13)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            -- Sessions table
            CREATE TABLE IF NOT EXISTS sessions (
                session_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                total_tokens INTEGER DEFAULT 0
            );

            -- Messages table
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                token_count INTEGER DEFAULT 0,
                FOREIGN KEY (session_key) REFERENCES sessions(session_key)
            );

            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_key, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_role
                ON messages(session_key, role);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC);
        """
        )
        # Create FTS5 virtual table for full-text search (F-13)
        self._create_fts5_table(conn)
        _debug_log(f"ConversationStore initialized at {self.db_path}")

    def _create_fts5_table(self, conn: sqlite3.Connection) -> None:
        """Create FTS5 virtual table for message content search."""
        conn.executescript(
            """
            -- FTS5 virtual table for full-text search (F-13)
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                session_key UNINDEXED,
                role UNINDEXED,
                message_id UNINDEXED,
                tokenize='porter unicode61'
            );

            -- Trigger to populate FTS on INSERT
            CREATE TRIGGER IF NOT EXISTS messages_fts_insert
            AFTER INSERT ON messages
            BEGIN
                INSERT INTO messages_fts (content, session_key, role, message_id)
                VALUES (NEW.content, NEW.session_key, NEW.role, NEW.id);
            END;

            -- Trigger to update FTS on UPDATE
            CREATE TRIGGER IF NOT EXISTS messages_fts_update
            AFTER UPDATE OF content ON messages
            BEGIN
                DELETE FROM messages_fts WHERE message_id = OLD.id;
                INSERT INTO messages_fts (content, session_key, role, message_id)
                VALUES (NEW.content, NEW.session_key, NEW.role, NEW.id);
            END;

            -- Trigger to delete from FTS on DELETE
            CREATE TRIGGER IF NOT EXISTS messages_fts_delete
            AFTER DELETE ON messages
            BEGIN
                DELETE FROM messages_fts WHERE message_id = OLD.id;
            END;
        """
        )

    def _migrate(
        self, conn: sqlite3.Connection, from_version: int, to_version: int
    ) -> None:
        """Run schema migrations."""
        _debug_log(f"Migrating ConversationStore from v{from_version} to v{to_version}")

        if from_version < 2 <= to_version:
            # Migration v1 → v2: Add FTS5 support (F-13)
            self._create_fts5_table(conn)

            # Backfill existing messages into FTS index
            cursor = conn.execute(
                "SELECT id, content, session_key, role FROM messages"
            )
            rows = cursor.fetchall()
            if rows:
                conn.executemany(
                    """
                    INSERT INTO messages_fts (content, session_key, role, message_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(r[1], r[2], r[3], r[0]) for r in rows],
                )
                _debug_log(f"Backfilled {len(rows)} messages into FTS5 index")

            conn.commit()

    def add_message(self, message: Message) -> Message:
        """
        Add a message to the store.

        Args:
            message: The message to store

        Returns:
            The stored message with ID
        """
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()

        with self._lock:
            old_iso = conn.isolation_level
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")

                # Ensure session exists
                conn.execute(
                    """
                    INSERT INTO sessions (session_key, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, '{}')
                    ON CONFLICT(session_key) DO UPDATE SET updated_at = excluded.updated_at
                """,
                    (message.session_key, now, now),
                )

                # Insert message
                data = message.to_dict()
                conn.execute(
                    """
                    INSERT INTO messages (id, session_key, role, content, timestamp, metadata, token_count)
                    VALUES (:id, :session_key, :role, :content, :timestamp, :metadata, :token_count)
                """,
                    data,
                )

                # Update session token count
                conn.execute(
                    """
                    UPDATE sessions
                    SET total_tokens = total_tokens + ?,
                        updated_at = ?
                    WHERE session_key = ?
                """,
                    (message.token_count, now, message.session_key),
                )

                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
                raise
            finally:
                conn.isolation_level = old_iso

        _debug_log(f"Added message {message.id} to session {message.session_key}")
        return message

    def get_history(
        self,
        session_key: str,
        limit: int = 100,
        before: datetime | None = None,
        roles: list[str] | None = None,
    ) -> list[Message]:
        """
        Get message history for a session.

        Args:
            session_key: The session to query
            limit: Maximum messages to return
            before: Only return messages before this time
            roles: Filter by message roles

        Returns:
            List of messages in chronological order
        """
        conn = self._get_conn()

        query = "SELECT * FROM messages WHERE session_key = ?"
        params: list = [session_key]

        if before:
            query += " AND timestamp < ?"
            params.append(before.isoformat())

        if roles:
            placeholders = ",".join("?" * len(roles))
            query += f" AND role IN ({placeholders})"
            params.extend(roles)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        # Convert to messages and reverse for chronological order
        messages = [Message.from_dict(dict(row)) for row in rows]
        messages.reverse()

        return messages

    def get_session(self, session_key: str) -> SessionInfo | None:
        """Get session info."""
        conn = self._get_conn()

        cursor = conn.execute("SELECT * FROM sessions WHERE session_key = ?", (session_key,))
        row = cursor.fetchone()

        if not row:
            return None

        # Count messages
        count_cursor = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_key = ?", (session_key,)
        )
        message_count = count_cursor.fetchone()[0]

        return SessionInfo(
            session_key=row["session_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            message_count=message_count,
            total_tokens=row["total_tokens"],
            metadata=json.loads(row["metadata"]),
        )

    def list_sessions(
        self,
        limit: int = 50,
        active_after: datetime | None = None,
    ) -> list[SessionInfo]:
        """List all sessions."""
        conn = self._get_conn()

        query = "SELECT * FROM sessions"
        params: list = []

        if active_after:
            query += " WHERE updated_at > ?"
            params.append(active_after.isoformat())

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        sessions = []
        for row in rows:
            # Get message count for each session
            count_cursor = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_key = ?",
                (row["session_key"],),
            )
            message_count = count_cursor.fetchone()[0]

            sessions.append(
                SessionInfo(
                    session_key=row["session_key"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    message_count=message_count,
                    total_tokens=row["total_tokens"],
                    metadata=json.loads(row["metadata"]),
                )
            )

        return sessions

    def delete_session(self, session_key: str) -> bool:
        """Delete a session and all its messages."""
        conn = self._get_conn()

        with self._lock:
            conn.execute("DELETE FROM messages WHERE session_key = ?", (session_key,))
            cursor = conn.execute("DELETE FROM sessions WHERE session_key = ?", (session_key,))
            conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            _debug_log(f"Deleted session {session_key}")
        return deleted

    def clear_old_messages(
        self,
        session_key: str,
        keep_last: int = 100,
    ) -> int:
        """
        Clear old messages from a session, keeping the most recent.

        Args:
            session_key: The session to compact
            keep_last: Number of recent messages to keep

        Returns:
            Number of messages deleted
        """
        conn = self._get_conn()

        with self._lock:
            # Find cutoff point
            cursor = conn.execute(
                """
                SELECT id FROM messages
                WHERE session_key = ?
                ORDER BY timestamp DESC
                LIMIT 1 OFFSET ?
            """,
                (session_key, keep_last),
            )

            row = cursor.fetchone()
            if not row:
                return 0

            # Delete older messages
            delete_cursor = conn.execute(
                """
                DELETE FROM messages
                WHERE session_key = ?
                AND timestamp < (
                    SELECT timestamp FROM messages WHERE id = ?
                )
            """,
                (session_key, row["id"]),
            )

            deleted = delete_cursor.rowcount

            # Recalculate token count
            token_cursor = conn.execute(
                """
                SELECT COALESCE(SUM(token_count), 0)
                FROM messages WHERE session_key = ?
            """,
                (session_key,),
            )
            new_total = token_cursor.fetchone()[0]

            conn.execute(
                """
                UPDATE sessions SET total_tokens = ? WHERE session_key = ?
            """,
                (new_total, session_key),
            )

            conn.commit()

        if deleted > 0:
            _debug_log(f"Compacted session {session_key}, deleted {deleted} messages")

        return deleted

    def search_content(
        self,
        query: str,
        session_key: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """
        Full-text search across message content using FTS5.

        Uses BM25 ranking for relevance scoring.

        Args:
            query: Search query (supports FTS5 syntax: AND, OR, quotes)
            session_key: Optional session filter
            limit: Maximum results

        Returns:
            Matching messages ranked by relevance
        """
        conn = self._get_conn()

        try:
            # Use FTS5 with BM25 ranking (F-13)
            if session_key:
                cursor = conn.execute(
                    """
                    SELECT m.*, bm25(messages_fts) AS rank
                    FROM messages_fts AS fts
                    JOIN messages AS m ON fts.message_id = m.id
                    WHERE messages_fts MATCH ?
                      AND fts.session_key = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, session_key, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT m.*, bm25(messages_fts) AS rank
                    FROM messages_fts AS fts
                    JOIN messages AS m ON fts.message_id = m.id
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, limit),
                )
            return [Message.from_dict(dict(row)) for row in cursor.fetchall()]

        except sqlite3.OperationalError as e:
            # Fallback to LIKE search if FTS5 not available
            _debug_log(f"FTS5 search failed, falling back to LIKE: {e}")
            search_pattern = f"%{query}%"

            if session_key:
                cursor = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE session_key = ? AND content LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (session_key, search_pattern, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE content LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (search_pattern, limit),
                )

            return [Message.from_dict(dict(row)) for row in cursor.fetchall()]

    def fts_search(
        self,
        query: str,
        session_key: str | None = None,
        role: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Full-text search with relevance ranking (F-13).

        Uses FTS5 MATCH with BM25 ranking for relevance scoring.
        Returns search results with matched snippets and scores.

        Args:
            query: Search query (supports FTS5 syntax: AND, OR, quotes)
            session_key: Optional session filter
            role: Optional role filter (user, assistant, system, tool)
            limit: Maximum results

        Returns:
            List of dicts with message, score, and snippet
        """
        conn = self._get_conn()

        try:
            # Build query with optional filters
            sql = """
                SELECT m.*, bm25(messages_fts) AS score,
                       snippet(messages_fts, 0, '[', ']', '...', 64) AS snippet
                FROM messages_fts AS fts
                JOIN messages AS m ON fts.message_id = m.id
                WHERE messages_fts MATCH ?
            """
            params: list = [query]

            if session_key:
                sql += " AND fts.session_key = ?"
                params.append(session_key)

            if role:
                sql += " AND fts.role = ?"
                params.append(role)

            sql += " ORDER BY score LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                results.append({
                    "message": Message.from_dict(row_dict),
                    "score": row_dict.get("score", 0),
                    "snippet": row_dict.get("snippet", ""),
                })
            return results

        except sqlite3.OperationalError as e:
            _debug_log(f"FTS5 search failed: {e}")
            # Fallback to search_content() which has LIKE fallback
            messages = self.search_content(query, session_key, limit)
            return [{"message": m, "score": 0, "snippet": m.content[:200]} for m in messages]

    def get_token_count(self, session_key: str) -> int:
        """Get total token count for a session."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT total_tokens FROM sessions WHERE session_key = ?", (session_key,)
        )
        row = cursor.fetchone()
        return row["total_tokens"] if row else 0

    def iter_messages(
        self,
        session_key: str,
        batch_size: int = 100,
    ) -> Iterator[Message]:
        """
        Iterate over all messages in a session.

        Memory-efficient for large histories.
        """
        conn = self._get_conn()
        offset = 0

        while True:
            cursor = conn.execute(
                """
                SELECT * FROM messages
                WHERE session_key = ?
                ORDER BY timestamp ASC
                LIMIT ? OFFSET ?
            """,
                (session_key, batch_size, offset),
            )

            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                yield Message.from_dict(dict(row))

            offset += batch_size

    # close() inherited from BaseStore
