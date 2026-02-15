"""
RuntimeStore — Consolidated bot stats, daily log, cache, and runtime state.

Merges data from the legacy ``~/.navig/bot/bot_data.db`` (BotStatsStore)
and ``~/.navig/daily_log.db`` (DailyLog) into a single database at
``~/.navig/runtime.db``.

On first open, if legacy databases exist they are auto-migrated and
renamed to ``*.db.migrated``.

Tables:
    command_stats     — aggregate command counters
    command_log       — individual command executions (30-day retention)
    interactions      — daily log entries (30-day retention)
    daily_summaries   — per-day summaries
    reminders         — user reminders
    ai_state          — AI conversation state per user
    cache             — TTL key-value cache (lazy eviction)
    notes             — user notes
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)


def _runtime_db_path() -> Path:
    return Path.home() / ".navig" / "runtime.db"


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeStore(BaseStore):
    """
    Consolidated runtime data store.

    Usage::

        store = RuntimeStore()
        store.log_command("run", user_id=1, chat_id=1, duration_ms=120, success=True)
        store.add_interaction(role="user", content="Deploy the app")
    """

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -8000}  # 8 MB

    def __init__(self, db_path: Optional[Path] = None):
        super().__init__(db_path or _runtime_db_path())
        self._mem_cache: Dict[str, Dict[str, Any]] = {}
        # Auto-migrate legacy databases
        self._auto_migrate_legacy()

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            -- Command usage statistics
            CREATE TABLE IF NOT EXISTS command_stats (
                command         TEXT PRIMARY KEY,
                count           INTEGER DEFAULT 0,
                last_used       TEXT,
                total_duration_ms INTEGER DEFAULT 0,
                error_count     INTEGER DEFAULT 0
            );

            -- Individual command log
            CREATE TABLE IF NOT EXISTS command_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                command     TEXT NOT NULL,
                user_id     INTEGER,
                chat_id     INTEGER,
                duration_ms INTEGER,
                success     INTEGER,
                error_message TEXT,
                executed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_cmdlog_command
                ON command_log (command, executed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cmdlog_time
                ON command_log (executed_at DESC);
            -- Covering index for time-series dashboard
            CREATE INDEX IF NOT EXISTS idx_cmdlog_covering
                ON command_log (executed_at DESC, command, success);

            -- Interactions / daily log
            CREATE TABLE IF NOT EXISTS interactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                date        TEXT NOT NULL,
                session_id  TEXT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                channel     TEXT,
                server      TEXT,
                command     TEXT,
                metadata    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_interactions_date
                ON interactions (date);
            CREATE INDEX IF NOT EXISTS idx_interactions_session
                ON interactions (session_id, timestamp DESC);

            -- Daily summaries
            CREATE TABLE IF NOT EXISTS daily_summaries (
                date        TEXT PRIMARY KEY,
                summary     TEXT NOT NULL,
                entry_count INTEGER,
                topics      TEXT,
                created_at  TEXT NOT NULL
            );

            -- Reminders
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                message     TEXT NOT NULL,
                remind_at   TEXT NOT NULL,
                created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                completed   INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders (remind_at) WHERE completed = 0;
            CREATE INDEX IF NOT EXISTS idx_reminders_user
                ON reminders (user_id);

            -- AI conversation state
            CREATE TABLE IF NOT EXISTS ai_state (
                user_id     INTEGER PRIMARY KEY,
                chat_id     INTEGER,
                mode        TEXT,
                persona     TEXT,
                started_at  TEXT,
                context     TEXT
            );

            -- TTL cache
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                expires_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cache_expires
                ON cache (expires_at);

            -- User notes
            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                text        TEXT NOT NULL,
                created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_notes_user
                ON notes (user_id);
        """)

    def _migrate(
        self, conn: sqlite3.Connection, from_version: int, to_version: int
    ) -> None:
        pass  # Future migrations

    # ── Legacy migration ──────────────────────────────────────

    def _auto_migrate_legacy(self) -> None:
        """One-time migration from legacy bot_data.db + daily_log.db."""
        navig_dir = self.db_path.parent

        legacy_bot = navig_dir / "bot" / "bot_data.db"
        legacy_daily = navig_dir / "daily_log.db"

        if legacy_bot.exists() and not legacy_bot.with_suffix(".db.migrated").exists():
            try:
                self._copy_tables_from(
                    legacy_bot,
                    [
                        "command_stats",
                        "command_log",
                        "reminders",
                        "ai_state",
                        "cache",
                        "notes",
                    ],
                )
                legacy_bot.rename(legacy_bot.with_suffix(".db.migrated"))
                logger.info("Migrated bot_data.db → runtime.db")
            except Exception as exc:
                logger.warning("Legacy bot_data.db migration failed: %s", exc)

        if legacy_daily.exists() and not legacy_daily.with_suffix(".db.migrated").exists():
            try:
                self._copy_tables_from(
                    legacy_daily,
                    ["interactions", "daily_summaries"],
                )
                legacy_daily.rename(legacy_daily.with_suffix(".db.migrated"))
                logger.info("Migrated daily_log.db → runtime.db")
            except Exception as exc:
                logger.warning("Legacy daily_log.db migration failed: %s", exc)

    def _copy_tables_from(
        self, source_db: Path, tables: List[str]
    ) -> None:
        """Copy rows from *source_db* into this store's matching tables."""
        src = sqlite3.connect(str(source_db))
        src.row_factory = sqlite3.Row
        conn = self._get_conn()

        try:
            for table in tables:
                # Check table exists in source
                exists = src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue

                rows = src.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                if not rows:
                    continue

                columns = rows[0].keys()
                # Filter to columns that exist in our schema
                our_cols_row = conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
                our_cols = {r["name"] for r in our_cols_row}
                shared_cols = [c for c in columns if c in our_cols]

                if not shared_cols:
                    continue

                cols_str = ", ".join(shared_cols)
                placeholders = ", ".join("?" * len(shared_cols))

                with self._lock:
                    with conn:
                        for row in rows:
                            try:
                                conn.execute(
                                    f"INSERT OR IGNORE INTO {table} ({cols_str}) VALUES ({placeholders})",  # noqa: S608
                                    tuple(row[c] for c in shared_cols),
                                )
                            except sqlite3.IntegrityError:
                                pass
        finally:
            src.close()

    # ── Command Stats ─────────────────────────────────────────

    def log_command(
        self,
        command: str,
        user_id: int = 0,
        chat_id: int = 0,
        duration_ms: int = 0,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        """Log a command execution and update aggregate stats."""
        now = _utcnow()
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO command_log
                        (command, user_id, chat_id, duration_ms, success, error_message, executed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (command, user_id, chat_id, duration_ms, 1 if success else 0, error_message, now),
                )
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

    def get_stats_summary(self) -> Dict[str, Any]:
        """Aggregate command statistics."""
        conn = self._get_conn()
        today_start = _utc_now_dt().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        commands_today = conn.execute(
            "SELECT COUNT(*) FROM command_log WHERE executed_at >= ?",
            (today_start,),
        ).fetchone()[0]

        total_row = conn.execute(
            "SELECT SUM(count) FROM command_stats"
        ).fetchone()
        total_commands = total_row[0] or 0

        errors_today = conn.execute(
            "SELECT COUNT(*) FROM command_log WHERE executed_at >= ? AND success = 0",
            (today_start,),
        ).fetchone()[0]

        top_commands = conn.execute(
            "SELECT command, count FROM command_stats ORDER BY count DESC LIMIT 5"
        ).fetchall()

        active_reminders = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE completed = 0 AND remind_at > ?",
            (_utcnow(),),
        ).fetchone()[0]

        return {
            "commands_today": commands_today,
            "total_commands": total_commands,
            "errors_today": errors_today,
            "top_commands": [(r["command"], r["count"]) for r in top_commands],
            "active_reminders": active_reminders,
        }

    def get_command_stats(self) -> List[Dict[str, Any]]:
        """Per-command statistics."""
        rows = self._read_all(
            "SELECT command, count, last_used, total_duration_ms, error_count "
            "FROM command_stats ORDER BY count DESC"
        )
        return [
            {
                "command": r["command"],
                "count": r["count"],
                "last_used": r["last_used"],
                "avg_duration_ms": (
                    r["total_duration_ms"] / r["count"] if r["count"] else 0
                ),
                "error_count": r["error_count"],
            }
            for r in rows
        ]

    # ── Interactions / Daily Log ──────────────────────────────

    def add_interaction(
        self,
        role: str,
        content: str,
        channel: Optional[str] = None,
        server: Optional[str] = None,
        command: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Add a daily-log interaction entry."""
        now = _utc_now_dt()
        cursor = self._write(
            """
            INSERT INTO interactions
                (timestamp, date, session_id, role, content, channel, server, command, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(),
                now.strftime("%Y-%m-%d"),
                session_id,
                role,
                content,
                channel,
                server,
                command,
                json.dumps(metadata) if metadata else None,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]

    def get_recent_interactions(
        self, hours: int = 24, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Recent interaction entries."""
        cutoff = (
            _utc_now_dt() - timedelta(hours=hours)
        ).isoformat()
        rows = self._read_all(
            """
            SELECT * FROM interactions
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        return [dict(r) for r in rows]

    def get_interactions_for_date(self, date: str) -> List[Dict[str, Any]]:
        """Entries for a specific date (YYYY-MM-DD)."""
        rows = self._read_all(
            "SELECT * FROM interactions WHERE date = ? ORDER BY timestamp ASC",
            (date,),
        )
        return [dict(r) for r in rows]

    def save_daily_summary(
        self,
        date: str,
        summary: str,
        entry_count: int = 0,
        topics: Optional[str] = None,
    ) -> None:
        """Save or replace a daily summary."""
        self._write(
            """
            INSERT OR REPLACE INTO daily_summaries (date, summary, entry_count, topics, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (date, summary, entry_count, topics, _utcnow()),
        )

    # ── Reminders ─────────────────────────────────────────────

    def create_reminder(
        self,
        user_id: int,
        chat_id: int,
        message: str,
        remind_at: datetime,
    ) -> int:
        """Create a reminder. Returns the row id."""
        cursor = self._write(
            """
            INSERT INTO reminders (user_id, chat_id, message, remind_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, message, remind_at.isoformat(), _utcnow()),
        )
        return cursor.lastrowid  # type: ignore[return-value]

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        """Reminders whose time has come."""
        rows = self._read_all(
            "SELECT * FROM reminders WHERE completed = 0 AND remind_at <= ? ORDER BY remind_at",
            (_utcnow(),),
        )
        return [dict(r) for r in rows]

    def get_user_reminders(self, user_id: int) -> List[Dict[str, Any]]:
        """Active reminders for a user."""
        rows = self._read_all(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND completed = 0 AND remind_at > ?
            ORDER BY remind_at LIMIT 20
            """,
            (user_id, _utcnow()),
        )
        return [dict(r) for r in rows]

    def complete_reminder(self, reminder_id: int) -> None:
        self._write(
            "UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,)
        )

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        cursor = self._write(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, user_id),
        )
        return cursor.rowcount > 0

    # ── AI State ──────────────────────────────────────────────

    def get_ai_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self._read_one(
            "SELECT * FROM ai_state WHERE user_id = ?", (user_id,)
        )
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
        persona: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> None:
        self._write(
            """
            INSERT INTO ai_state (user_id, chat_id, mode, persona, started_at, context)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                mode = excluded.mode,
                persona = excluded.persona,
                started_at = CASE
                    WHEN excluded.mode = 'active' AND ai_state.mode != 'active'
                    THEN excluded.started_at ELSE ai_state.started_at END,
                context = excluded.context
            """,
            (
                user_id,
                chat_id,
                mode,
                persona,
                _utcnow(),
                json.dumps(context) if context else None,
            ),
        )

    def clear_ai_state(self, user_id: int) -> None:
        self._write(
            "UPDATE ai_state SET mode = 'inactive', context = NULL WHERE user_id = ?",
            (user_id,),
        )

    # ── Cache ─────────────────────────────────────────────────

    def cache_get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired. Checks in-memory first."""
        if key in self._mem_cache:
            cached = self._mem_cache[key]
            if cached["expires_at"] > _utcnow():
                return cached["value"]
            del self._mem_cache[key]

        row = self._read_one(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        )
        if not row:
            return None

        if row["expires_at"] and row["expires_at"] <= _utcnow():
            self._write("DELETE FROM cache WHERE key = ?", (key,))
            return None

        value = json.loads(row["value"])
        self._mem_cache[key] = {
            "value": value,
            "expires_at": row["expires_at"],
        }
        return value

    def cache_set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        expires_at = (
            _utc_now_dt() + timedelta(seconds=ttl_seconds)
        ).isoformat()
        value_json = json.dumps(value)
        self._mem_cache[key] = {"value": value, "expires_at": expires_at}
        self._write(
            """
            INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at
            """,
            (key, value_json, expires_at),
        )

    def cache_delete(self, key: str) -> None:
        self._mem_cache.pop(key, None)
        self._write("DELETE FROM cache WHERE key = ?", (key,))

    def cache_clear_expired(self) -> int:
        """Purge expired cache entries. Returns count deleted."""
        now = _utcnow()
        self._mem_cache = {
            k: v for k, v in self._mem_cache.items() if v["expires_at"] > now
        }
        cursor = self._write(
            "DELETE FROM cache WHERE expires_at <= ?", (now,)
        )
        return cursor.rowcount

    # ── Notes ─────────────────────────────────────────────────

    def save_note(self, user_id: int, chat_id: int, text: str) -> int:
        cursor = self._write(
            "INSERT INTO notes (user_id, chat_id, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, text, _utcnow()),
        )
        return cursor.lastrowid  # type: ignore[return-value]

    def get_user_notes(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._read_all(
            "SELECT id, text, created_at FROM notes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in rows]

    def delete_note(self, note_id: int, user_id: int) -> bool:
        cursor = self._write(
            "DELETE FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        return cursor.rowcount > 0

    # ── Retention / Maintenance ───────────────────────────────

    def prune(self, command_log_days: int = 30, interaction_days: int = 30) -> Dict[str, int]:
        """Purge old data. Returns counts deleted."""
        deleted = {}
        deleted["command_log"] = self._write(
            "DELETE FROM command_log WHERE executed_at < datetime('now', ? || ' days')",
            (str(-command_log_days),),
        ).rowcount
        deleted["interactions"] = self._write(
            "DELETE FROM interactions WHERE timestamp < datetime('now', ? || ' days')",
            (str(-interaction_days),),
        ).rowcount
        deleted["cache"] = self.cache_clear_expired()
        return deleted

    def get_full_stats(self) -> Dict[str, Any]:
        """Overall runtime store statistics."""
        conn = self._get_conn()
        stats: Dict[str, Any] = {}

        for table in [
            "command_stats", "command_log", "interactions",
            "daily_summaries", "reminders", "ai_state", "cache", "notes",
        ]:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
            stats[f"{table}_count"] = row[0] if row else 0

        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        stats["db_size_bytes"] = db_size
        stats["db_size_mb"] = round(db_size / 1024 / 1024, 2)

        return stats


# ── Module-level singleton ────────────────────────────────────

_store: Optional[RuntimeStore] = None


def get_runtime_store(db_path: Optional[Path] = None) -> RuntimeStore:
    """Get or create the global RuntimeStore instance."""
    global _store
    if _store is None:
        _store = RuntimeStore(db_path)
    return _store
