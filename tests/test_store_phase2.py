"""
Tests for Phase 2 — Vector search, BaseStore adoption, PG mirror.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── VectorIndex tests ─────────────────────────────────────────


class TestVectorHelpers:
    """Test float ↔ blob conversion helpers."""

    def test_floats_to_blob(self):
        from navig.memory.vector import floats_to_blob

        blob = floats_to_blob([1.0, 2.0, 3.0])
        assert isinstance(blob, bytes)
        assert len(blob) == 12  # 3 × 4 bytes

    def test_blob_to_floats(self):
        from navig.memory.vector import floats_to_blob, blob_to_floats

        original = [1.0, 2.0, 3.0, 4.0]
        blob = floats_to_blob(original)
        result = blob_to_floats(blob)
        assert result == original

    def test_roundtrip_large(self):
        from navig.memory.vector import floats_to_blob, blob_to_floats

        original = [float(i) for i in range(1536)]
        blob = floats_to_blob(original)
        assert len(blob) == 1536 * 4
        result = blob_to_floats(blob)
        assert result == original


class TestVectorIndexUnavailable:
    """Test VectorIndex when sqlite-vec is not installed."""

    def test_graceful_degradation(self, tmp_path):
        import sqlite3
        from navig.memory import vector

        # Force unavailable
        old = vector._VEC_AVAILABLE
        vector._VEC_AVAILABLE = False
        try:
            conn = sqlite3.connect(str(tmp_path / "test.db"))
            vi = vector.VectorIndex(conn, dimensions=3)
            assert vi.available is False
            assert vi.search([1.0, 2.0, 3.0]) == []
            assert vi.count() == 0
            vi.upsert("test", [1.0, 2.0, 3.0])  # no-op
            vi.upsert_batch([("a", [1.0, 2.0, 3.0])])  # no-op
            assert vi.migrate_text_embeddings() == 0
            conn.close()
        finally:
            vector._VEC_AVAILABLE = old

    def test_repr(self, tmp_path):
        import sqlite3
        from navig.memory import vector

        old = vector._VEC_AVAILABLE
        vector._VEC_AVAILABLE = False
        try:
            conn = sqlite3.connect(str(tmp_path / "test.db"))
            vi = vector.VectorIndex(conn, dimensions=128)
            assert "dim=128" in repr(vi)
            assert "unavailable" in repr(vi)
            conn.close()
        finally:
            vector._VEC_AVAILABLE = old


# ── MemoryStorage vector integration ──────────────────────────


class TestMemoryStorageVectorIntegration:
    """Test vector methods on MemoryStorage (without actual sqlite-vec)."""

    def _make_storage(self, tmp_path):
        from navig.memory.storage import MemoryStorage

        return MemoryStorage(tmp_path / "index.db", embedding_dimensions=3)

    def test_vec_available_property(self, tmp_path):
        """vec_available should be False when sqlite-vec not installed."""
        storage = self._make_storage(tmp_path)
        # On CI/dev machines without sqlite-vec, this should be False
        # The test verifies the property works without crashing
        assert isinstance(storage.vec_available, bool)
        storage.close()

    def test_vector_search_raises_without_vec(self, tmp_path):
        from navig.memory import vector

        old = vector._VEC_AVAILABLE
        vector._VEC_AVAILABLE = False
        try:
            storage = self._make_storage(tmp_path)
            with pytest.raises(RuntimeError, match="sqlite-vec not available"):
                storage.vector_search([1.0, 2.0, 3.0])
            storage.close()
        finally:
            vector._VEC_AVAILABLE = old

    def test_hybrid_search_degrades_to_fts(self, tmp_path):
        """hybrid_search falls back to FTS-only when vec unavailable."""
        from navig.memory import vector
        from navig.memory.storage import MemoryChunk

        old = vector._VEC_AVAILABLE
        vector._VEC_AVAILABLE = False
        try:
            storage = self._make_storage(tmp_path)

            # Insert a chunk so FTS returns something
            chunk = MemoryChunk(
                id="c1",
                file_path="test.py",
                content="hello world search term",
                line_start=1,
                line_end=5,
                token_count=10,
            )
            storage.upsert_chunks([chunk])

            # hybrid_search should fall back to FTS
            results = storage.hybrid_search("hello", [0.1, 0.2, 0.3], limit=5)
            assert len(results) >= 1
            assert results[0][0].id == "c1"
            storage.close()
        finally:
            vector._VEC_AVAILABLE = old


# ── ConversationStore BaseStore migration ─────────────────────


class TestConversationStoreBaseStore:
    """Verify ConversationStore still works after BaseStore migration."""

    def test_inherits_base_store(self):
        from navig.memory.conversation import ConversationStore
        from navig.store.base import BaseStore

        assert issubclass(ConversationStore, BaseStore)

    def test_crud_operations(self, tmp_path):
        from navig.memory.conversation import ConversationStore, Message

        store = ConversationStore(tmp_path / "memory.db")

        msg = Message(session_key="s1", role="user", content="Hello")
        stored = store.add_message(msg)
        assert stored.id == msg.id

        history = store.get_history("s1")
        assert len(history) == 1
        assert history[0].content == "Hello"

        session = store.get_session("s1")
        assert session is not None
        assert session.message_count == 1

        store.close()

    def test_wal_mode_from_base(self, tmp_path):
        from navig.memory.conversation import ConversationStore

        store = ConversationStore(tmp_path / "memory.db")
        conn = store._get_conn()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal == "wal"
        store.close()

    def test_maintenance_inherited(self, tmp_path):
        from navig.memory.conversation import ConversationStore

        store = ConversationStore(tmp_path / "memory.db")
        result = store.maintenance()
        assert result["integrity"] == "ok"
        store.close()

    def test_backup_inherited(self, tmp_path):
        from navig.memory.conversation import ConversationStore, Message

        store = ConversationStore(tmp_path / "memory.db")
        store.add_message(Message(session_key="bk", role="user", content="backup"))

        backup_path = tmp_path / "backup.db"
        store.backup(backup_path)
        assert backup_path.exists()
        store.close()


# ── MatrixStore BaseStore migration ───────────────────────────


class TestMatrixStoreBaseStore:
    """Verify MatrixStore still works after BaseStore migration."""

    def test_inherits_base_store(self):
        from navig.comms.matrix_store import MatrixStore
        from navig.store.base import BaseStore

        assert issubclass(MatrixStore, BaseStore)

    def test_crud_operations(self, tmp_path):
        from navig.comms.matrix_store import MatrixStore, MatrixRoom, MatrixEvent

        store = MatrixStore(tmp_path / "matrix.db")

        room = MatrixRoom(room_id="!test:server", name="Test Room")
        store.upsert_room(room)
        assert store.get_room("!test:server").name == "Test Room"
        assert store.count_rooms() == 1

        event = MatrixEvent(
            event_id="$ev1",
            room_id="!test:server",
            sender="@user:server",
            event_type="m.room.message",
        )
        store.add_event(event)
        assert store.count_events() == 1

        store.close()

    def test_schema_version(self, tmp_path):
        from navig.comms.matrix_store import MatrixStore

        store = MatrixStore(tmp_path / "matrix.db")
        assert store.get_schema_version() == 2
        store.close()

    def test_maintenance_inherited(self, tmp_path):
        from navig.comms.matrix_store import MatrixStore

        store = MatrixStore(tmp_path / "matrix.db")
        result = store.maintenance()
        assert result["integrity"] == "ok"
        store.close()


# ── PG Mirror tests ───────────────────────────────────────────


class TestPgMirror:
    """Test PG mirror in disabled mode (no real PG connection)."""

    def test_disabled_by_default(self):
        from navig.store.pg_mirror import PgMirror

        with patch.dict("os.environ", {}, clear=True):
            mirror = PgMirror(pg_url="")
            assert mirror.enabled is False

    def test_emit_noop_when_disabled(self):
        from navig.store.pg_mirror import PgMirror

        mirror = PgMirror(pg_url="")
        mirror.emit("test_table", "INSERT", {"key": "value"})
        assert mirror.flush() == 0  # nothing buffered

    def test_emit_buffers_when_enabled(self):
        from navig.store.pg_mirror import PgMirror

        mirror = PgMirror(pg_url="postgresql://fake:fake@localhost/navig")
        mirror.emit("navig_audit", "INSERT", {"action": "test"})
        assert len(mirror._buffer) == 1
        assert mirror._buffer[0]["table"] == "navig_audit"

    def test_flush_without_psycopg(self):
        """Flush degrades gracefully when psycopg2 is not available."""
        from navig.store.pg_mirror import PgMirror

        mirror = PgMirror(pg_url="postgresql://fake:fake@localhost/navig")
        mirror.emit("t", "INSERT", {"a": 1})

        # Mock psycopg2 import failure
        with patch.dict("sys.modules", {"psycopg2": None}):
            flushed = mirror.flush()
            assert flushed == 0

    def test_repr(self):
        from navig.store.pg_mirror import PgMirror

        mirror = PgMirror(pg_url="")
        assert "disabled" in repr(mirror)
        mirror2 = PgMirror(pg_url="postgresql://x")
        assert "enabled" in repr(mirror2)

    def test_close(self):
        from navig.store.pg_mirror import PgMirror

        mirror = PgMirror(pg_url="")
        mirror.close()  # should not raise

    def test_get_pg_mirror_singleton(self):
        from navig.store import pg_mirror

        # Reset singleton
        pg_mirror._mirror = None
        m1 = pg_mirror.get_pg_mirror()
        m2 = pg_mirror.get_pg_mirror()
        assert m1 is m2
        pg_mirror._mirror = None  # cleanup
