"""
Tests for ConversationStore with FTS5 support (F-13).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.memory.conversation import ConversationStore, Message, SessionInfo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup_storage_engine():
    """Clean up storage engine connections after each test.

    This is necessary on Windows where WAL mode keeps files locked.
    """
    yield
    try:
        from navig.storage import get_engine

        get_engine().close_all()
    except Exception:  # noqa: BLE001
        pass


class TestConversationStore:
    """Test ConversationStore basic operations."""

    def _make_store(self, tmp_path: Path) -> ConversationStore:
        """Create a store instance for testing."""
        return ConversationStore(tmp_path / "conversation.db")

    def test_creates_db_file(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.db_path.exists()
        store.close()

    def test_schema_version_is_2(self, tmp_path):
        """Verify SCHEMA_VERSION is 2 for FTS5 support."""
        store = self._make_store(tmp_path)
        assert store.SCHEMA_VERSION == 2
        assert store.get_schema_version() == 2
        store.close()

    def test_add_message(self, tmp_path):
        store = self._make_store(tmp_path)
        msg = Message(
            session_key="test-session",
            role="user",
            content="Hello, this is a test message",
            token_count=10,
        )
        result = store.add_message(msg)
        assert result.id == msg.id
        assert result.session_key == "test-session"
        store.close()

    def test_get_history(self, tmp_path):
        store = self._make_store(tmp_path)
        session_key = "history-session"

        # Add multiple messages
        for i in range(5):
            msg = Message(
                session_key=session_key,
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message number {i}",
            )
            store.add_message(msg)

        history = store.get_history(session_key)
        assert len(history) == 5
        store.close()

    def test_add_message_logs_rollback_failure_and_reraises_original_error(self, tmp_path):
        store = self._make_store(tmp_path)

        class _FakeConn:
            def __init__(self):
                self.isolation_level = "DEFERRED"

            def execute(self, sql, params=None):
                text = str(sql)
                if text == "ROLLBACK":
                    raise sqlite3.OperationalError("rollback failed")
                if "INSERT INTO messages" in text:
                    raise sqlite3.OperationalError("insert failed")
                return None

        fake_conn = _FakeConn()
        store._get_conn = lambda: fake_conn  # type: ignore[method-assign]

        msg = Message(session_key="tx-session", role="user", content="hello")

        with patch("navig.memory.conversation._debug_log") as debug_log:
            with pytest.raises(sqlite3.OperationalError, match="insert failed"):
                store.add_message(msg)

        debug_log.assert_any_call("add_message rollback failed (non-fatal): rollback failed")
        store.close()


class TestFTS5Search:
    """Test FTS5 full-text search functionality (F-13)."""

    def _make_store(self, tmp_path: Path) -> ConversationStore:
        """Create a store instance for testing."""
        return ConversationStore(tmp_path / "conversation.db")

    def test_fts5_table_created(self, tmp_path):
        """Verify FTS5 virtual table is created."""
        store = self._make_store(tmp_path)
        conn = store._get_conn()

        # Check FTS5 table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        )
        assert cursor.fetchone() is not None
        store.close()

    def test_fts5_triggers_created(self, tmp_path):
        """Verify FTS5 triggers are created."""
        store = self._make_store(tmp_path)
        conn = store._get_conn()

        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        trigger_names = [t[0] for t in triggers]

        assert "messages_fts_insert" in trigger_names
        assert "messages_fts_update" in trigger_names
        assert "messages_fts_delete" in trigger_names
        store.close()

    def test_search_content_fts5(self, tmp_path):
        """Test search_content uses FTS5."""
        store = self._make_store(tmp_path)
        session_key = "search-session"

        # Add messages with different content
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="How do I handle Python exceptions?",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="assistant",
                content="Use try/except blocks for exception handling in Python.",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="What about JavaScript errors?",
            )
        )

        # Search for "exception"
        results = store.search_content("exception")
        assert len(results) == 2  # Both messages mention exception

        # Search for "JavaScript"
        results = store.search_content("JavaScript")
        assert len(results) == 1

        store.close()

    def test_search_content_with_session_filter(self, tmp_path):
        """Test search_content with session filter."""
        store = self._make_store(tmp_path)

        # Add messages to different sessions
        store.add_message(
            Message(
                session_key="session-1",
                role="user",
                content="Error handling in Python",
            )
        )
        store.add_message(
            Message(
                session_key="session-2",
                role="user",
                content="Error handling in JavaScript",
            )
        )

        # Search with session filter
        results = store.search_content("error", session_key="session-1")
        assert len(results) == 1
        assert results[0].session_key == "session-1"

        store.close()

    def test_fts_search_with_ranking(self, tmp_path):
        """Test fts_search returns ranked results with snippets."""
        store = self._make_store(tmp_path)
        session_key = "ranked-session"

        # Add messages with varying relevance
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="I need help with database queries.",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="assistant",
                content="Database queries can be optimized. SQL database performance matters.",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="Thanks!",
            )
        )

        # Search for "database"
        results = store.fts_search("database")
        assert len(results) == 2

        # Results should have score and snippet
        assert "score" in results[0]
        assert "snippet" in results[0]
        assert "message" in results[0]

        store.close()

    def test_fts_search_with_role_filter(self, tmp_path):
        """Test fts_search with role filter."""
        store = self._make_store(tmp_path)
        session_key = "role-session"

        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="How do I use async await?",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="assistant",
                content="Async await is for asynchronous operations.",
            )
        )

        # Search only assistant messages
        results = store.fts_search("async", role="assistant")
        assert len(results) == 1
        assert results[0]["message"].role == "assistant"

        store.close()

    def test_fts5_syntax_support(self, tmp_path):
        """Test FTS5 query syntax (AND, OR, quotes)."""
        store = self._make_store(tmp_path)
        session_key = "syntax-session"

        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="Python error handling guide",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="JavaScript async programming",
            )
        )
        store.add_message(
            Message(
                session_key=session_key,
                role="user",
                content="Python async await tutorial",
            )
        )

        # Test phrase search with quotes
        results = store.search_content('"error handling"')
        assert len(results) == 1

        # Test AND search
        results = store.search_content("Python AND async")
        assert len(results) == 1
        assert "async" in results[0].content.lower()

        store.close()


class TestFTS5Migration:
    """Test FTS5 migration from v1 to v2."""

    def test_migration_backfills_fts(self, tmp_path):
        """Test that migration backfills existing messages into FTS index."""
        db_path = tmp_path / "migration.db"

        # Create a v1 database manually
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Create v1 schema without FTS5
        conn.executescript(
            """
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version (version) VALUES (1);

            CREATE TABLE sessions (
                session_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                total_tokens INTEGER DEFAULT 0
            );

            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                token_count INTEGER DEFAULT 0,
                FOREIGN KEY (session_key) REFERENCES sessions(session_key)
            );
            """
        )

        # Insert test data
        import uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, '{}', 0)",
            ("migrate-session", now, now),
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, '{}', 0)",
                (str(uuid.uuid4()), "migrate-session", "user", f"Migration test message {i}", now),
            )
        conn.commit()
        conn.close()

        # Now open with ConversationStore - this should trigger migration
        store = ConversationStore(db_path)

        # Verify migration happened
        assert store.get_schema_version() == 2

        # Verify FTS5 table exists and is populated
        conn = store._get_conn()
        fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        assert fts_count == 3

        # Verify FTS search works on migrated data
        results = store.search_content("migration")
        assert len(results) == 3

        store.close()


class TestSearchEdgeCases:
    """Test edge cases for search functionality."""

    def _make_store(self, tmp_path: Path) -> ConversationStore:
        """Create a store instance for testing."""
        return ConversationStore(tmp_path / "conversation.db")

    def test_search_empty_results(self, tmp_path):
        """Test search with no matches."""
        store = self._make_store(tmp_path)
        store.add_message(
            Message(
                session_key="empty-session",
                role="user",
                content="Hello world",
            )
        )

        results = store.search_content("nonexistent")
        assert len(results) == 0
        store.close()

    def test_search_special_characters(self, tmp_path):
        """Test search with special characters."""
        store = self._make_store(tmp_path)
        store.add_message(
            Message(
                session_key="special-session",
                role="user",
                content="What is C++ vs C#?",
            )
        )

        # Should not crash on special chars
        results = store.search_content("C++")
        assert len(results) >= 0  # May or may not match depending on tokenizer
        store.close()

    def test_search_unicode(self, tmp_path):
        """Test search with unicode content."""
        store = self._make_store(tmp_path)
        store.add_message(
            Message(
                session_key="unicode-session",
                role="user",
                content="日本語テスト message with Japanese",
            )
        )

        results = store.search_content("Japanese")
        assert len(results) == 1
        store.close()
