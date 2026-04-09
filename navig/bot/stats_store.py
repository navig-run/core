"""
NAVIG Bot Stats and Caching Layer.

.. deprecated::
    This module is superseded by ``navig.store.runtime.RuntimeStore`` which
    consolidates bot_data.db and daily_log.db into a single runtime.db with
    WAL mode, automatic migration, and unified maintenance.  New code should
    use ``from navig.store.runtime import get_runtime_store`` instead.

Provides:
- Usage statistics tracking (commands executed, errors, etc.)
- TTL-based caching for frequent queries
- Reminder storage and scheduling
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir


def _utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now()  # utcnow() deprecated in Py3.12+


def _bot_db_path() -> Path:
    """Get path to bot database."""
    db_dir = config_dir() / "bot"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "bot_data.db"


@dataclass
class Reminder:
    """A user reminder."""

    id: int
    user_id: int
    chat_id: int
    message: str
    remind_at: datetime
    created_at: datetime
    completed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "message": self.message,
            "remind_at": self.remind_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "completed": self.completed,
        }


@dataclass
class CommandStat:
    """Statistics for a command."""

    command: str
    count: int
    last_used: datetime
    avg_duration_ms: float
    error_count: int


class BotStatsStore:
    """
    SQLite-backed storage for bot statistics and reminders.

    Features:
    - Command usage tracking
    - Error logging
    - Reminder storage
    - TTL-based cache for frequent data
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or _bot_db_path()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}  # In-memory cache

        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_schema(self):
        """Initialize database schema."""
        conn = self._get_conn()

        conn.executescript(
            """
            -- Command usage statistics
            CREATE TABLE IF NOT EXISTS command_stats (
                command TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0,
                last_used TEXT,
                total_duration_ms INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0
            );

            -- Individual command logs (last 1000 per command)
            CREATE TABLE IF NOT EXISTS command_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                user_id INTEGER,
                chat_id INTEGER,
                duration_ms INTEGER,
                success INTEGER,
                error_message TEXT,
                executed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Reminders
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed INTEGER DEFAULT 0
            );

            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_reminders_remind_at ON reminders(remind_at);
            CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id);
            CREATE INDEX IF NOT EXISTS idx_command_log_command ON command_log(command);

            -- AI conversation state
            CREATE TABLE IF NOT EXISTS ai_state (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                mode TEXT,  -- 'active' or 'inactive'
                persona TEXT,
                started_at TEXT,
                context TEXT  -- JSON blob for conversation context
            );

            -- Cache table for TTL-based caching
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TEXT
            );

            -- User notes
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
        """
        )

        conn.commit()

    # ===== Command Statistics =====

    def log_command(
        self,
        command: str,
        user_id: int,
        chat_id: int,
        duration_ms: int,
        success: bool,
        error_message: str | None = None,
    ):
        """Log a command execution."""
        conn = self._get_conn()
        now = _utc_now().isoformat()

        with self._lock:
            # Insert into log
            conn.execute(
                """
                INSERT INTO command_log (command, user_id, chat_id, duration_ms, success, error_message, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    command,
                    user_id,
                    chat_id,
                    duration_ms,
                    1 if success else 0,
                    error_message,
                    now,
                ),
            )

            # Update aggregate stats
            conn.execute(
                """
                INSERT INTO command_stats (command, count, last_used, total_duration_ms, error_count)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(command) DO UPDATE SET
                    count = count + 1,
                    last_used = excluded.last_used,
                    total_duration_ms = total_duration_ms + excluded.total_duration_ms,
                    error_count = error_count + excluded.error_count
            """,
                (command, now, duration_ms, 0 if success else 1),
            )

            # Prune old logs (keep last 1000 per command)
            conn.execute(
                """
                DELETE FROM command_log WHERE id IN (
                    SELECT id FROM command_log WHERE command = ?
                    ORDER BY executed_at DESC
                    LIMIT -1 OFFSET 1000
                )
            """,
                (command,),
            )

            conn.commit()

    def get_stats_summary(self) -> dict[str, Any]:
        """Get summary statistics."""
        conn = self._get_conn()

        # Total commands today
        today_start = _utc_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = conn.execute(
            """
            SELECT COUNT(*) as count FROM command_log WHERE executed_at >= ?
        """,
            (today_start,),
        ).fetchone()
        commands_today = row["count"] if row else 0

        # Total commands all time
        row = conn.execute("SELECT SUM(count) as total FROM command_stats").fetchone()
        total_commands = row["total"] or 0

        # Total errors today
        row = conn.execute(
            """
            SELECT COUNT(*) as count FROM command_log WHERE executed_at >= ? AND success = 0
        """,
            (today_start,),
        ).fetchone()
        errors_today = row["count"] if row else 0

        # Most used commands
        rows = conn.execute(
            """
            SELECT command, count FROM command_stats ORDER BY count DESC LIMIT 5
        """
        ).fetchall()
        top_commands = [(r["command"], r["count"]) for r in rows]

        # Active reminders
        row = conn.execute(
            """
            SELECT COUNT(*) as count FROM reminders WHERE completed = 0 AND remind_at > ?
        """,
            (_utc_now().isoformat(),),
        ).fetchone()
        active_reminders = row["count"] if row else 0

        return {
            "commands_today": commands_today,
            "total_commands": total_commands,
            "errors_today": errors_today,
            "top_commands": top_commands,
            "active_reminders": active_reminders,
        }

    def get_command_stats(self) -> list[CommandStat]:
        """Get statistics for all commands."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT command, count, last_used, total_duration_ms, error_count
            FROM command_stats
            ORDER BY count DESC
        """
        ).fetchall()

        return [
            CommandStat(
                command=r["command"],
                count=r["count"],
                last_used=(datetime.fromisoformat(r["last_used"]) if r["last_used"] else None),
                avg_duration_ms=(r["total_duration_ms"] / r["count"] if r["count"] > 0 else 0),
                error_count=r["error_count"],
            )
            for r in rows
        ]

    # ===== Reminders =====

    def create_reminder(
        self,
        user_id: int,
        chat_id: int,
        message: str,
        remind_at: datetime,
    ) -> Reminder:
        """Create a new reminder."""
        conn = self._get_conn()
        now = _utc_now()

        with self._lock:
            cursor = conn.execute(
                """
                INSERT INTO reminders (user_id, chat_id, message, remind_at, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (user_id, chat_id, message, remind_at.isoformat(), now.isoformat()),
            )
            conn.commit()

            return Reminder(
                id=cursor.lastrowid,
                user_id=user_id,
                chat_id=chat_id,
                message=message,
                remind_at=remind_at,
                created_at=now,
            )

    def get_due_reminders(self) -> list[Reminder]:
        """Get all reminders that are due."""
        conn = self._get_conn()
        now = _utc_now().isoformat()

        rows = conn.execute(
            """
            SELECT * FROM reminders
            WHERE completed = 0 AND remind_at <= ?
            ORDER BY remind_at
        """,
            (now,),
        ).fetchall()

        return [
            Reminder(
                id=r["id"],
                user_id=r["user_id"],
                chat_id=r["chat_id"],
                message=r["message"],
                remind_at=datetime.fromisoformat(r["remind_at"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                completed=bool(r["completed"]),
            )
            for r in rows
        ]

    def get_user_reminders(self, user_id: int) -> list[Reminder]:
        """Get all active reminders for a user."""
        conn = self._get_conn()

        rows = conn.execute(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND completed = 0 AND remind_at > ?
            ORDER BY remind_at
            LIMIT 20
        """,
            (user_id, _utc_now().isoformat()),
        ).fetchall()

        return [
            Reminder(
                id=r["id"],
                user_id=r["user_id"],
                chat_id=r["chat_id"],
                message=r["message"],
                remind_at=datetime.fromisoformat(r["remind_at"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def complete_reminder(self, reminder_id: int):
        """Mark a reminder as completed."""
        conn = self._get_conn()
        with self._lock:
            conn.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,))
            conn.commit()

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        """Cancel (delete) a reminder. Returns True if deleted."""
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ===== AI State =====

    def get_ai_state(self, user_id: int) -> dict[str, Any] | None:
        """Get AI conversation state for a user."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM ai_state WHERE user_id = ?", (user_id,)).fetchone()

        if not row:
            return None

        return {
            "user_id": row["user_id"],
            "chat_id": row["chat_id"],
            "mode": row["mode"],
            "persona": row["persona"],
            "started_at": row["started_at"],
            "context": json.loads(row["context"]) if row["context"] else None,
        }

    def set_ai_state(
        self,
        user_id: int,
        chat_id: int,
        mode: str,
        persona: str | None = None,
        context: dict | None = None,
    ):
        """Set or update AI conversation state."""
        conn = self._get_conn()
        now = _utc_now().isoformat()
        context_json = json.dumps(context) if context else None

        with self._lock:
            conn.execute(
                """
                INSERT INTO ai_state (user_id, chat_id, mode, persona, started_at, context)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    mode = excluded.mode,
                    persona = excluded.persona,
                    started_at = CASE WHEN excluded.mode = 'active' AND ai_state.mode != 'active'
                        THEN excluded.started_at ELSE ai_state.started_at END,
                    context = excluded.context
            """,
                (user_id, chat_id, mode, persona, now, context_json),
            )
            conn.commit()

    def clear_ai_state(self, user_id: int):
        """Clear AI state for a user."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE ai_state SET mode = 'inactive', context = NULL WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()

    # ===== Caching =====

    def cache_get(self, key: str) -> Any | None:
        """Get a cached value if not expired."""
        # Check in-memory cache first
        if key in self._cache:
            cached = self._cache[key]
            if datetime.fromisoformat(cached["expires_at"]) > _utc_now():
                return cached["value"]
            else:
                del self._cache[key]

        # Check database
        conn = self._get_conn()
        row = conn.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()

        if not row:
            return None

        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= _utc_now():
            # Expired, delete it
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None

        value = json.loads(row["value"])

        # Store in memory cache
        self._cache[key] = {"value": value, "expires_at": row["expires_at"]}

        return value

    def cache_set(self, key: str, value: Any, ttl_seconds: int = 60):
        """Set a cached value with TTL."""
        expires_at = (_utc_now() + timedelta(seconds=ttl_seconds)).isoformat()
        value_json = json.dumps(value)

        # Store in memory cache
        self._cache[key] = {"value": value, "expires_at": expires_at}

        # Store in database
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """
                INSERT INTO cache (key, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at
            """,
                (key, value_json, expires_at),
            )
            conn.commit()

    def cache_delete(self, key: str):
        """Delete a cached value."""
        if key in self._cache:
            del self._cache[key]

        conn = self._get_conn()
        with self._lock:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def cache_clear_expired(self):
        """Clean up expired cache entries."""
        now = _utc_now().isoformat()

        # Clean memory cache
        expired_keys = [
            k
            for k, v in self._cache.items()
            if datetime.fromisoformat(v["expires_at"]) <= _utc_now()
        ]
        for k in expired_keys:
            del self._cache[k]

        # Clean database
        conn = self._get_conn()
        with self._lock:
            conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
            conn.commit()

    # ===== Notes =====

    def save_note(self, user_id: int, chat_id: int, text: str) -> int:
        """Save a note. Returns the note ID."""
        conn = self._get_conn()
        now = _utc_now().isoformat()

        with self._lock:
            cursor = conn.execute(
                """
                INSERT INTO notes (user_id, chat_id, text, created_at)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, chat_id, text, now),
            )
            conn.commit()
            return cursor.lastrowid

    def get_user_notes(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Get user's notes, most recent first."""
        conn = self._get_conn()

        rows = conn.execute(
            """
            SELECT id, text, created_at FROM notes
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (user_id, limit),
        ).fetchall()

        return [{"id": r["id"], "text": r["text"], "created_at": r["created_at"]} for r in rows]

    def delete_note(self, note_id: int, user_id: int) -> bool:
        """Delete a note. Returns True if deleted."""
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute(
                "DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0


# Global instance
_store: BotStatsStore | None = None


def get_bot_store() -> BotStatsStore:
    """Get or create the global bot stats store."""
    global _store
    if _store is None:
        _store = BotStatsStore()
    return _store
