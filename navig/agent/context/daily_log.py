"""
Daily Logs for NAVIG Agent

.. deprecated::
    This module is superseded by ``navig.store.runtime.RuntimeStore`` which
    consolidates bot_data.db and daily_log.db into a single runtime.db with
    WAL mode, automatic migration, and unified maintenance.  New code should
    use ``from navig.store.runtime import get_runtime_store`` instead.

Provides session-based interaction logging for agent continuity,
inspired by the Daily Logs architecture pattern.

Features:
- Automatic session logging
- Daily summaries for context injection
- Configurable retention period
- Privacy-aware storage (no sensitive data)

Usage:
    from navig.agent.context.daily_log import DailyLog

    log = DailyLog()

    # Log an interaction
    log.add_entry("user", "Deploy the app to production")
    log.add_entry("agent", "Deployed successfully to prod-server-01")

    # Get recent context for system prompt
    recent = log.get_recent_summary(hours=24)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_RETENTION_DAYS = 30
MAX_SUMMARY_ENTRIES = 50
MAX_CONTEXT_CHARS = 2000


def get_daily_log_path() -> Path:
    """Get path to daily log database."""
    navig_dir = Path.home() / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)
    return navig_dir / "daily_log.db"


# =============================================================================
# Database Schema
# =============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    date TEXT NOT NULL,
    session_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    channel TEXT,
    server TEXT,
    command TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_interactions_date ON interactions(date);
CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id);

CREATE TABLE IF NOT EXISTS daily_summaries (
    date TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    entry_count INTEGER,
    topics TEXT,
    created_at TEXT NOT NULL
);
"""


# =============================================================================
# Daily Log Class
# =============================================================================


class DailyLog:
    """
    Session-based interaction logger for agent continuity.

    Stores interactions in SQLite with date partitioning for efficient
    retrieval and automatic cleanup.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        self.db_path = db_path or get_daily_log_path()
        self.retention_days = retention_days
        self._session_id: Optional[str] = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure database is initialized."""
        if self._initialized:
            return

        with self._get_connection() as conn:
            conn.executescript(SCHEMA)

        self._initialized = True

    @contextmanager
    def _get_connection(self):
        """Get database connection with auto-commit."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @property
    def session_id(self) -> str:
        """Get or create current session ID."""
        if self._session_id is None:
            self._session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return self._session_id

    def start_session(self, session_id: Optional[str] = None) -> str:
        """Start a new logging session."""
        self._session_id = session_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return self._session_id

    # =========================================================================
    # Logging
    # =========================================================================

    def add_entry(
        self,
        role: str,
        content: str,
        channel: Optional[str] = None,
        server: Optional[str] = None,
        command: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Add an interaction entry to the log.

        Args:
            role: "user" or "agent"
            content: The message content (will be truncated for privacy)
            channel: Channel used (telegram, discord, cli, etc.)
            server: Server context if applicable
            command: NAVIG command executed if applicable
            metadata: Additional metadata dict

        Returns:
            Entry ID
        """
        self._ensure_initialized()

        now = datetime.utcnow()
        date_str = now.strftime("%Y-%m-%d")

        # Truncate content for privacy (no full messages stored)
        safe_content = self._sanitize_content(content)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO interactions
                (timestamp, date, session_id, role, content, channel, server, command, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(),
                    date_str,
                    self.session_id,
                    role,
                    safe_content,
                    channel,
                    server,
                    command,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            return cursor.lastrowid

    def _sanitize_content(self, content: str, max_length: int = 200) -> str:
        """
        Sanitize content for storage.

        Truncates and removes potentially sensitive data.
        """
        # Import security module if available
        try:
            from navig.core.security import redact_sensitive_text

            content = redact_sensitive_text(content)
        except ImportError:
            pass  # optional dependency not installed; feature disabled

        # Truncate
        if len(content) > max_length:
            content = content[:max_length] + "..."

        return content

    # =========================================================================
    # Retrieval
    # =========================================================================

    def get_recent_entries(
        self, hours: int = 24, limit: int = MAX_SUMMARY_ENTRIES
    ) -> List[Dict[str, Any]]:
        """
        Get recent interaction entries.

        Args:
            hours: How many hours back to look
            limit: Maximum entries to return

        Returns:
            List of entry dicts
        """
        self._ensure_initialized()

        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM interactions
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_today_entries(self) -> List[Dict[str, Any]]:
        """Get all entries from today."""
        return self.get_entries_for_date(datetime.utcnow().strftime("%Y-%m-%d"))

    def get_entries_for_date(self, date: str) -> List[Dict[str, Any]]:
        """Get entries for a specific date (YYYY-MM-DD)."""
        self._ensure_initialized()

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM interactions
                WHERE date = ?
                ORDER BY timestamp ASC
                """,
                (date,),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_session_entries(
        self, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get entries for a specific session."""
        self._ensure_initialized()

        session_id = session_id or self.session_id

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # =========================================================================
    # Context Generation
    # =========================================================================

    def get_recent_summary(self, hours: int = 24) -> str:
        """
        Get a summary of recent interactions for context injection.

        Returns a formatted string suitable for system prompt.
        """
        entries = self.get_recent_entries(hours=hours, limit=20)

        if not entries:
            return ""

        # Group by session
        sessions: Dict[str, List[Dict]] = {}
        for entry in entries:
            sid = entry.get("session_id", "unknown")
            if sid not in sessions:
                sessions[sid] = []
            sessions[sid].append(entry)

        # Build summary
        lines = ["## Recent Activity"]

        for session_id, session_entries in sessions.items():
            if session_entries:
                first = session_entries[0]
                timestamp = first.get("timestamp", "")[:16]  # YYYY-MM-DDTHH:MM
                lines.append(f"\n### Session {timestamp}")

                for entry in session_entries[-5:]:  # Last 5 per session
                    role = entry.get("role", "?")
                    content = entry.get("content", "")[:100]
                    channel = entry.get("channel", "")

                    prefix = "👤" if role == "user" else "🤖"
                    channel_str = f" [{channel}]" if channel else ""
                    lines.append(f"- {prefix}{channel_str} {content}")

        summary = "\n".join(lines)

        # Truncate if too long
        if len(summary) > MAX_CONTEXT_CHARS:
            summary = summary[:MAX_CONTEXT_CHARS] + "\n...(truncated)"

        return summary

    def get_context_for_agent(self) -> str:
        """
        Get formatted context for agent system prompt.

        Returns context wrapped in XML tags.
        """
        summary = self.get_recent_summary(hours=24)
        if not summary:
            return ""

        return f"<recent_interactions>\n{summary}\n</recent_interactions>"

    # =========================================================================
    # Daily Summaries
    # =========================================================================

    def generate_daily_summary(self, date: Optional[str] = None) -> str:
        """
        Generate a summary for a specific date.

        Uses simple extraction for now; could be enhanced with LLM.
        """
        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        entries = self.get_entries_for_date(date)

        if not entries:
            return f"No activity recorded for {date}"

        # Extract topics from commands and content
        commands = set()
        channels = set()
        servers = set()

        for entry in entries:
            if entry.get("command"):
                commands.add(entry["command"])
            if entry.get("channel"):
                channels.add(entry["channel"])
            if entry.get("server"):
                servers.add(entry["server"])

        # Build summary
        lines = [f"Activity for {date}:"]
        lines.append(f"- {len(entries)} interactions recorded")

        if commands:
            lines.append(f"- Commands used: {', '.join(sorted(commands)[:10])}")
        if channels:
            lines.append(f"- Channels: {', '.join(sorted(channels))}")
        if servers:
            lines.append(f"- Servers: {', '.join(sorted(servers))}")

        return "\n".join(lines)

    def save_daily_summary(self, date: Optional[str] = None) -> None:
        """Save a daily summary to the database."""
        self._ensure_initialized()

        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        summary = self.generate_daily_summary(date)
        entries = self.get_entries_for_date(date)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_summaries
                (date, summary, entry_count, topics, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    date,
                    summary,
                    len(entries),
                    None,  # Topics could be extracted with NLP
                    datetime.utcnow().isoformat(),
                ),
            )

    # =========================================================================
    # Maintenance
    # =========================================================================

    def cleanup_old_entries(self) -> int:
        """
        Remove entries older than retention period.

        Returns number of entries removed.
        """
        self._ensure_initialized()

        cutoff = (datetime.utcnow() - timedelta(days=self.retention_days)).strftime(
            "%Y-%m-%d"
        )

        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM interactions WHERE date < ?", (cutoff,))
            deleted = cursor.rowcount

            # Also clean old summaries
            conn.execute("DELETE FROM daily_summaries WHERE date < ?", (cutoff,))

        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Get log statistics."""
        self._ensure_initialized()

        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE date = ?",
                (datetime.utcnow().strftime("%Y-%m-%d"),),
            ).fetchone()[0]

            oldest = conn.execute("SELECT MIN(date) FROM interactions").fetchone()[0]

            by_role = dict(
                conn.execute(
                    "SELECT role, COUNT(*) FROM interactions GROUP BY role"
                ).fetchall()
            )

        return {
            "total_entries": total,
            "today_entries": today,
            "oldest_date": oldest,
            "by_role": by_role,
            "retention_days": self.retention_days,
            "db_path": str(self.db_path),
        }


# =============================================================================
# Module-level convenience
# =============================================================================

_default_log: Optional[DailyLog] = None


def get_daily_log() -> DailyLog:
    """Get the default daily log instance."""
    global _default_log
    if _default_log is None:
        _default_log = DailyLog()
    return _default_log


def log_interaction(role: str, content: str, **kwargs) -> int:
    """Log an interaction to the daily log."""
    return get_daily_log().add_entry(role, content, **kwargs)


def get_recent_context() -> str:
    """Get recent interaction context for agent."""
    return get_daily_log().get_context_for_agent()
