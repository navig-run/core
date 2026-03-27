"""
Tests for NAVIG Store layer — BaseStore, AuditStore, RuntimeStore.

Phase 1 of the local-first SQLite migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── BaseStore tests ───────────────────────────────────────────


class TestBaseStore:
    """Test the BaseStore abstract class."""

    def _make_store(self, tmp_path: Path):
        """Create a minimal concrete subclass for testing."""
        from navig.store.base import BaseStore

        class DummyStore(BaseStore):
            SCHEMA_VERSION = 1
            PRAGMAS = {"cache_size": -4000}

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")

        return DummyStore(tmp_path / "test.db")

    def test_creates_db_file(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.db_path.exists()
        store.close()

    def test_schema_version_stamped(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.get_schema_version() == 1
        store.close()

    def test_wal_mode_enabled(self, tmp_path):
        store = self._make_store(tmp_path)
        row = store._get_conn().execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        store.close()

    def test_foreign_keys_enabled(self, tmp_path):
        store = self._make_store(tmp_path)
        row = store._get_conn().execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
        store.close()

    def test_write_and_read(self, tmp_path):
        store = self._make_store(tmp_path)
        store._write("INSERT INTO items (name) VALUES (?)", ("alpha",))
        row = store._read_one("SELECT name FROM items WHERE id = 1")
        assert row is not None
        assert row["name"] == "alpha"
        store.close()

    def test_write_many(self, tmp_path):
        store = self._make_store(tmp_path)
        count = store._write_many(
            "INSERT INTO items (name) VALUES (?)",
            [("a",), ("b",), ("c",)],
        )
        assert count == 3
        rows = store._read_all("SELECT name FROM items ORDER BY name")
        assert [r["name"] for r in rows] == ["a", "b", "c"]
        store.close()

    def test_read_one_returns_none(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store._read_one("SELECT * FROM items WHERE id = 999") is None
        store.close()

    def test_maintenance(self, tmp_path):
        store = self._make_store(tmp_path)
        store._write("INSERT INTO items (name) VALUES (?)", ("test",))
        result = store.maintenance()
        assert result["integrity"] == "ok"
        assert result["optimize"] == "done"
        assert result["analyze"] == "done"
        assert result["size_bytes"] > 0
        store.close()

    def test_backup(self, tmp_path):
        store = self._make_store(tmp_path)
        store._write("INSERT INTO items (name) VALUES (?)", ("backup_me",))
        dest = tmp_path / "backup" / "test_backup.db"
        store.backup(dest)
        assert dest.exists()

        # Verify backup content
        conn = sqlite3.connect(str(dest))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name FROM items WHERE id = 1").fetchone()
        assert row["name"] == "backup_me"
        conn.close()
        store.close()

    def test_close_and_reopen(self, tmp_path):
        store = self._make_store(tmp_path)
        store._write("INSERT INTO items (name) VALUES (?)", ("persist",))
        store.close()

        # Reopen
        store2 = self._make_store(tmp_path)
        row = store2._read_one("SELECT name FROM items WHERE id = 1")
        assert row["name"] == "persist"
        store2.close()

    def test_migration(self, tmp_path):
        """Test that migration runs when version increases."""
        from navig.store.base import BaseStore

        class V1Store(BaseStore):
            SCHEMA_VERSION = 1

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")

        class V2Store(BaseStore):
            SCHEMA_VERSION = 2
            migrated = False

            def _create_schema(self, conn):
                conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")

            def _migrate(self, conn, from_v, to_v):
                if from_v == 1 and to_v == 2:
                    conn.execute("ALTER TABLE items ADD COLUMN description TEXT DEFAULT ''")
                    V2Store.migrated = True

        db_path = tmp_path / "migrate.db"
        s1 = V1Store(db_path)
        s1._write("INSERT INTO items (name) VALUES (?)", ("old",))
        s1.close()

        s2 = V2Store(db_path)
        assert V2Store.migrated is True
        assert s2.get_schema_version() == 2
        # Old data preserved
        row = s2._read_one("SELECT name FROM items WHERE id = 1")
        assert row["name"] == "old"
        # New column exists
        s2._write("UPDATE items SET description = ? WHERE id = 1", ("new col",))
        row = s2._read_one("SELECT description FROM items WHERE id = 1")
        assert row["description"] == "new col"
        s2.close()

    def test_thread_safety(self, tmp_path):
        """Multiple threads can write concurrently without errors."""
        store = self._make_store(tmp_path)
        errors = []

        def writer(n):
            try:
                for i in range(50):
                    store._write(
                        "INSERT INTO items (name) VALUES (?)",
                        (f"thread_{n}_{i}",),
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        row = store._read_one("SELECT COUNT(*) as cnt FROM items")
        assert row["cnt"] == 200
        store.close()

    def test_repr(self, tmp_path):
        store = self._make_store(tmp_path)
        assert "DummyStore" in repr(store)
        assert "test.db" in repr(store)
        store.close()


# ── AuditStore tests ──────────────────────────────────────────


class TestAuditStore:
    """Test the AuditStore."""

    def _make_store(self, tmp_path: Path):
        from navig.store.audit import AuditStore

        return AuditStore(tmp_path / "audit.db")

    def test_log_and_query(self, tmp_path):
        store = self._make_store(tmp_path)
        row_id = store.log_event(
            action="command.run",
            actor="user",
            target="host:prod",
            details={"cmd": "ls -la"},
            channel="cli",
            host="prod",
        )
        assert row_id > 0

        events = store.query_events(action="command.run")
        assert len(events) == 1
        assert events[0]["actor"] == "user"
        assert events[0]["target"] == "host:prod"
        assert json.loads(events[0]["details"])["cmd"] == "ls -la"
        store.close()

    def test_batch_insert(self, tmp_path):
        store = self._make_store(tmp_path)
        events = [
            {"action": f"test.batch.{i}", "actor": "agent", "status": "success"} for i in range(100)
        ]
        count = store.log_events_batch(events)
        assert count == 100
        assert store.count_events() == 100
        store.close()

    def test_keyset_pagination(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(25):
            store.log_event(action=f"page.{i}", actor="user")

        page1 = store.query_events(limit=10)
        assert len(page1) == 10

        last_id = page1[-1]["id"]
        page2 = store.query_events(after_id=last_id, limit=10)
        assert len(page2) == 10
        assert page2[0]["id"] > last_id

        page3 = store.query_events(after_id=page2[-1]["id"], limit=10)
        assert len(page3) == 5  # remaining
        store.close()

    def test_failure_filter(self, tmp_path):
        store = self._make_store(tmp_path)
        store.log_event(action="ok.1", status="success")
        store.log_event(action="fail.1", status="failed")
        store.log_event(action="fail.2", status="partial")

        failures = store.get_failures(hours=1)
        assert len(failures) == 2
        store.close()

    def test_count_events(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(5):
            store.log_event(action="count.test")
        assert store.count_events(action="count.test") == 5
        assert store.count_events(action="nonexistent") == 0
        store.close()

    def test_prune(self, tmp_path):
        store = self._make_store(tmp_path)
        # Insert events with explicit old timestamps
        conn = store._get_conn()
        for i in range(10):
            store._write(
                "INSERT INTO audit_events (timestamp, action, status) VALUES (datetime('now', '-100 days'), ?, 'success')",
                (f"prune.{i}",),
            )

        assert store.count_events() == 10
        deleted = store.prune(days=90)
        assert deleted == 10
        assert store.count_events() == 0
        store.close()

    def test_stats(self, tmp_path):
        store = self._make_store(tmp_path)
        store.log_event(action="stat.a", status="success")
        store.log_event(action="stat.a", status="failed")
        store.log_event(action="stat.b", status="success")

        stats = store.get_stats()
        assert stats["total_events"] == 3
        assert stats["total_failures"] == 1
        assert stats["db_size_bytes"] > 0
        store.close()

    def test_query_filters(self, tmp_path):
        store = self._make_store(tmp_path)
        store.log_event(action="x", actor="alice", host="prod")
        store.log_event(action="y", actor="bob", host="dev")
        store.log_event(action="z", actor="alice", host="dev", status="failed")

        assert len(store.query_events(actor="alice")) == 2
        assert len(store.query_events(host="dev")) == 2
        assert len(store.query_events(status="failed")) == 1
        assert len(store.query_events(actor="alice", host="dev")) == 1
        store.close()


# ── RuntimeStore tests ────────────────────────────────────────


class TestRuntimeStore:
    """Test the RuntimeStore."""

    def _make_store(self, tmp_path: Path):
        from navig.store.runtime import RuntimeStore

        return RuntimeStore(tmp_path / "runtime.db")

    # -- Command Stats --

    def test_log_command(self, tmp_path):
        store = self._make_store(tmp_path)
        store.log_command("run", user_id=1, chat_id=1, duration_ms=150, success=True)
        store.log_command(
            "run",
            user_id=1,
            chat_id=1,
            duration_ms=200,
            success=False,
            error_message="timeout",
        )

        stats = store.get_command_stats()
        assert len(stats) == 1
        assert stats[0]["command"] == "run"
        assert stats[0]["count"] == 2
        assert stats[0]["error_count"] == 1
        store.close()

    def test_stats_summary(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(5):
            store.log_command(f"cmd_{i}", duration_ms=10)
        summary = store.get_stats_summary()
        assert summary["total_commands"] == 5
        assert summary["commands_today"] == 5
        store.close()

    # -- Interactions --

    def test_add_interaction(self, tmp_path):
        store = self._make_store(tmp_path)
        row_id = store.add_interaction(
            role="user", content="Deploy the app", channel="cli", session_id="s1"
        )
        assert row_id > 0

        entries = store.get_recent_interactions(hours=1)
        assert len(entries) == 1
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "Deploy the app"
        store.close()

    def test_interactions_date_query(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_interaction(role="agent", content="Done")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries = store.get_interactions_for_date(today)
        assert len(entries) == 1
        store.close()

    def test_daily_summary(self, tmp_path):
        store = self._make_store(tmp_path)
        store.save_daily_summary("2025-01-20", "Summary text", entry_count=5)
        row = store._read_one("SELECT * FROM daily_summaries WHERE date = '2025-01-20'")
        assert row["summary"] == "Summary text"
        assert row["entry_count"] == 5
        store.close()

    # -- Reminders --

    def test_create_and_complete_reminder(self, tmp_path):
        store = self._make_store(tmp_path)
        now = datetime.now(timezone.utc)
        past = now - timedelta(minutes=5)
        rid = store.create_reminder(user_id=1, chat_id=1, message="Call Bob", remind_at=past)
        assert rid > 0

        due = store.get_due_reminders()
        assert len(due) == 1
        assert due[0]["message"] == "Call Bob"

        store.complete_reminder(rid)
        assert len(store.get_due_reminders()) == 0
        store.close()

    def test_cancel_reminder(self, tmp_path):
        store = self._make_store(tmp_path)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        rid = store.create_reminder(user_id=1, chat_id=1, message="Later", remind_at=future)
        assert store.cancel_reminder(rid, user_id=1) is True
        assert store.cancel_reminder(rid, user_id=1) is False  # already deleted
        store.close()

    def test_user_reminders(self, tmp_path):
        store = self._make_store(tmp_path)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        store.create_reminder(user_id=42, chat_id=1, message="A", remind_at=future)
        store.create_reminder(user_id=42, chat_id=1, message="B", remind_at=future)
        store.create_reminder(user_id=99, chat_id=1, message="C", remind_at=future)

        reminders = store.get_user_reminders(42)
        assert len(reminders) == 2
        store.close()

    # -- AI State --

    def test_ai_state(self, tmp_path):
        store = self._make_store(tmp_path)
        store.set_ai_state(user_id=1, chat_id=100, mode="active", persona="helpful")
        state = store.get_ai_state(1)
        assert state is not None
        assert state["mode"] == "active"
        assert state["persona"] == "helpful"

        store.clear_ai_state(1)
        state = store.get_ai_state(1)
        assert state["mode"] == "inactive"
        store.close()

    def test_ai_state_none(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.get_ai_state(999) is None
        store.close()

    # -- Cache --

    def test_cache_set_get(self, tmp_path):
        store = self._make_store(tmp_path)
        store.cache_set("key1", {"data": 42}, ttl_seconds=60)
        assert store.cache_get("key1") == {"data": 42}
        store.close()

    def test_cache_miss(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.cache_get("nonexistent") is None
        store.close()

    def test_cache_delete(self, tmp_path):
        store = self._make_store(tmp_path)
        store.cache_set("x", "y")
        store.cache_delete("x")
        assert store.cache_get("x") is None
        store.close()

    def test_cache_clear_expired(self, tmp_path):
        store = self._make_store(tmp_path)
        store.cache_set("short", "val", ttl_seconds=0)  # expires immediately
        time.sleep(0.1)
        deleted = store.cache_clear_expired()
        assert deleted >= 1
        store.close()

    # -- Notes --

    def test_notes_crud(self, tmp_path):
        store = self._make_store(tmp_path)
        nid = store.save_note(user_id=1, chat_id=1, text="Remember this")
        assert nid > 0

        notes = store.get_user_notes(1)
        assert len(notes) == 1
        assert notes[0]["text"] == "Remember this"

        assert store.delete_note(nid, user_id=1) is True
        assert len(store.get_user_notes(1)) == 0
        store.close()

    # -- Retention --

    def test_prune(self, tmp_path):
        store = self._make_store(tmp_path)
        # Insert old command_log and interaction entries directly
        store._write(
            "INSERT INTO command_log (command, executed_at) VALUES ('old', datetime('now', '-60 days'))",
            (),
        )
        store._write(
            "INSERT INTO interactions (timestamp, date, role, content) VALUES (datetime('now', '-60 days'), '2024-11-01', 'agent', 'old entry')",
            (),
        )
        store.cache_set("stale", "value", ttl_seconds=0)
        time.sleep(0.1)

        result = store.prune(command_log_days=30, interaction_days=30)
        assert result["command_log"] >= 1
        assert result["interactions"] >= 1
        assert result["cache"] >= 1
        store.close()

    def test_full_stats(self, tmp_path):
        store = self._make_store(tmp_path)
        store.log_command("test")
        store.add_interaction(role="user", content="hello")

        stats = store.get_full_stats()
        assert stats["command_log_count"] >= 1
        assert stats["interactions_count"] >= 1
        assert stats["db_size_bytes"] > 0
        store.close()

    # -- Legacy Migration --

    def test_legacy_migration_bot_data(self, tmp_path):
        """Simulate legacy bot_data.db migration."""
        navig_dir = tmp_path
        bot_dir = navig_dir / "bot"
        bot_dir.mkdir()

        # Create legacy DB with old schema
        legacy_db = bot_dir / "bot_data.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.execute(
            """
            CREATE TABLE command_stats (
                command TEXT PRIMARY KEY, count INTEGER DEFAULT 0,
                last_used TEXT, total_duration_ms INTEGER DEFAULT 0, error_count INTEGER DEFAULT 0
            )
        """
        )
        conn.execute(
            "INSERT INTO command_stats (command, count, last_used) VALUES ('run', 42, '2025-01-01')"
        )
        conn.execute(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                text TEXT NOT NULL, created_at TEXT
            )
        """
        )
        conn.execute("INSERT INTO notes (user_id, chat_id, text) VALUES (1, 1, 'legacy note')")
        conn.commit()
        conn.close()

        # Create RuntimeStore — should auto-migrate
        from navig.store.runtime import RuntimeStore

        store = RuntimeStore(navig_dir / "runtime.db")

        # Verify migrated data
        stats = store.get_command_stats()
        assert any(s["command"] == "run" and s["count"] == 42 for s in stats)

        notes = store.get_user_notes(1)
        assert any(n["text"] == "legacy note" for n in notes)

        # Legacy file renamed
        assert legacy_db.with_suffix(".db.migrated").exists()
        assert not legacy_db.exists()
        store.close()

    def test_legacy_migration_daily_log(self, tmp_path):
        """Simulate legacy daily_log.db migration."""
        navig_dir = tmp_path

        legacy_db = navig_dir / "daily_log.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.execute(
            """
            CREATE TABLE interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, date TEXT NOT NULL,
                session_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL,
                channel TEXT, server TEXT, command TEXT, metadata TEXT
            )
        """
        )
        conn.execute(
            "INSERT INTO interactions (timestamp, date, role, content) VALUES ('2025-01-01T12:00:00Z', '2025-01-01', 'user', 'legacy interaction')"
        )
        conn.execute(
            """
            CREATE TABLE daily_summaries (
                date TEXT PRIMARY KEY, summary TEXT NOT NULL,
                entry_count INTEGER, topics TEXT, created_at TEXT NOT NULL
            )
        """
        )
        conn.execute(
            "INSERT INTO daily_summaries (date, summary, entry_count, created_at) VALUES ('2025-01-01', 'Legacy summary', 5, '2025-01-01T23:59:00Z')"
        )
        conn.commit()
        conn.close()

        from navig.store.runtime import RuntimeStore

        store = RuntimeStore(navig_dir / "runtime.db")

        entries = store.get_interactions_for_date("2025-01-01")
        assert any(e["content"] == "legacy interaction" for e in entries)

        row = store._read_one("SELECT * FROM daily_summaries WHERE date = '2025-01-01'")
        assert row is not None
        assert row["summary"] == "Legacy summary"

        assert legacy_db.with_suffix(".db.migrated").exists()
        store.close()

    def test_no_double_migration(self, tmp_path):
        """If .db.migrated exists, skip migration."""
        navig_dir = tmp_path
        bot_dir = navig_dir / "bot"
        bot_dir.mkdir()

        legacy_db = bot_dir / "bot_data.db"
        migrated_marker = legacy_db.with_suffix(".db.migrated")

        # Create legacy + marker
        conn = sqlite3.connect(str(legacy_db))
        conn.execute("CREATE TABLE command_stats (command TEXT PRIMARY KEY, count INTEGER)")
        conn.execute("INSERT INTO command_stats VALUES ('should_not_migrate', 99)")
        conn.commit()
        conn.close()
        migrated_marker.touch()

        from navig.store.runtime import RuntimeStore

        store = RuntimeStore(navig_dir / "runtime.db")

        stats = store.get_command_stats()
        assert not any(s["command"] == "should_not_migrate" for s in stats)
        # Original DB still exists (not renamed again)
        assert legacy_db.exists()
        store.close()
