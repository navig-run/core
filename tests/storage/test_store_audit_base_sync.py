"""Tests for store/base.py, store/audit.py, and memory/sync._as_chunk()."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# store/base.py — BASE_PRAGMAS + _utcnow()
# ──────────────────────────────────────────────────────────────────────────────
from navig.store.base import BASE_PRAGMAS, _utcnow


class TestBaseConstants:
    def test_base_pragmas_is_dict(self):
        assert isinstance(BASE_PRAGMAS, dict)

    def test_wal_mode(self):
        assert BASE_PRAGMAS.get("journal_mode") == "WAL"

    def test_foreign_keys_on(self):
        assert BASE_PRAGMAS.get("foreign_keys") == "ON"

    def test_synchronous_normal(self):
        assert BASE_PRAGMAS.get("synchronous") == "NORMAL"

    def test_busy_timeout_positive(self):
        assert BASE_PRAGMAS["busy_timeout"] > 0


class TestUtcNow:
    def test_returns_string(self):
        assert isinstance(_utcnow(), str)

    def test_contains_t_separator(self):
        assert "T" in _utcnow()

    def test_ends_with_z(self):
        assert _utcnow().endswith("Z")

    def test_parseable_as_datetime(self):
        ts = _utcnow()
        # Remove trailing Z and parse
        parsed = datetime.fromisoformat(ts.rstrip("Z").replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

    def test_monotonically_increasing(self):
        import time

        t1 = _utcnow()
        time.sleep(0.01)
        t2 = _utcnow()
        assert t2 >= t1


# ──────────────────────────────────────────────────────────────────────────────
# store/audit.py — AuditStore (SQLite, tmp_path)
# ──────────────────────────────────────────────────────────────────────────────
from navig.store.audit import AuditStore


class TestAuditStore:
    def test_log_event_returns_row_id(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        row_id = store.log_event(action="test.action")
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_query_returns_list(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="cmd.run")
        events = store.query_events()
        assert isinstance(events, list)

    def test_logged_event_appears_in_query(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="my.action", actor="tester")
        events = store.query_events(action="my.action")
        assert len(events) == 1
        assert events[0]["action"] == "my.action"
        assert events[0]["actor"] == "tester"

    def test_filter_by_action(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="a.one")
        store.log_event(action="b.two")
        result = store.query_events(action="a.one")
        assert all(e["action"] == "a.one" for e in result)

    def test_filter_by_actor(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="x", actor="alice")
        store.log_event(action="x", actor="bob")
        result = store.query_events(actor="alice")
        assert all(e["actor"] == "alice" for e in result)

    def test_filter_by_status(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="op", status="success")
        store.log_event(action="op", status="failure")
        failures = store.query_events(status="failure")
        assert all(e["status"] == "failure" for e in failures)

    def test_limit_respected(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        for i in range(10):
            store.log_event(action=f"ev.{i}")
        result = store.query_events(limit=3)
        assert len(result) <= 3

    def test_details_persisted(self, tmp_path):
        import json

        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="test", details={"key": "val"})
        events = store.query_events(action="test")
        details = json.loads(events[0]["details"])
        assert details["key"] == "val"

    def test_target_persisted(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="test", target="host:prod")
        events = store.query_events(action="test")
        assert events[0]["target"] == "host:prod"

    def test_host_filter(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="op", host="server-1")
        store.log_event(action="op", host="server-2")
        result = store.query_events(host="server-1")
        assert all(e["host"] == "server-1" for e in result)

    def test_count_events(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        for _ in range(5):
            store.log_event(action="counted")
        n = store.count_events(action="counted")
        assert n == 5

    def test_get_failures_empty_on_clean(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="good", status="success")
        failures = store.get_failures()
        assert failures == []

    def test_get_failures_returns_non_success(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        store.log_event(action="bad", status="error")
        failures = store.get_failures()
        assert len(failures) == 1
        assert failures[0]["status"] == "error"

    def test_batch_insert(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        events = [
            {"action": "batch.one", "actor": "bot"},
            {"action": "batch.two", "actor": "bot"},
        ]
        n = store.log_events_batch(events)
        assert n == 2
        assert len(store.query_events(actor="bot")) == 2

    def test_keyset_pagination(self, tmp_path):
        store = AuditStore(tmp_path / "audit.db")
        for i in range(5):
            store.log_event(action="paged")
        page1 = store.query_events(limit=2)
        last_id = page1[-1]["id"]
        page2 = store.query_events(after_id=last_id, limit=10)
        # All page2 IDs must be > last page1 ID
        assert all(e["id"] > last_id for e in page2)


# ──────────────────────────────────────────────────────────────────────────────
# memory/sync.py — _as_chunk() pure helper
# ──────────────────────────────────────────────────────────────────────────────
from navig.memory.sync import _as_chunk


class TestAsChunk:
    def test_returns_none_when_no_content(self):
        assert _as_chunk({}, "default.py") is None

    def test_returns_none_on_empty_content(self):
        assert _as_chunk({"content": "   "}, "file.py") is None

    def test_returns_chunk_on_valid_content(self):
        chunk = _as_chunk({"content": "hello world"}, "file.py")
        assert chunk is not None
        assert chunk.content == "hello world"

    def test_uses_provided_file_path(self):
        chunk = _as_chunk({"content": "text", "file_path": "src/app.py"}, "default.py")
        assert chunk is not None
        assert chunk.file_path == "src/app.py"

    def test_falls_back_to_default_file(self):
        chunk = _as_chunk({"content": "text"}, "default/path.py")
        assert chunk is not None
        assert "default" in chunk.file_path or chunk.file_path == "default/path.py"

    def test_uses_provided_id(self):
        chunk = _as_chunk({"content": "x", "id": "my-id-123"}, "f.py")
        assert chunk is not None
        assert chunk.id == "my-id-123"

    def test_generates_id_when_missing(self):
        chunk = _as_chunk({"content": "x"}, "f.py")
        assert chunk is not None
        assert len(chunk.id) > 0

    def test_parses_json_metadata(self):
        import json

        meta = json.dumps({"key": "value"})
        chunk = _as_chunk({"content": "x", "metadata": meta}, "f.py")
        assert chunk is not None
        assert chunk.metadata == {"key": "value"}

    def test_handles_invalid_metadata(self):
        chunk = _as_chunk({"content": "x", "metadata": "not-json{{"}, "f.py")
        assert chunk is not None
        assert isinstance(chunk.metadata, dict)

    def test_non_dict_metadata_becomes_empty(self):
        chunk = _as_chunk({"content": "x", "metadata": 42}, "f.py")
        assert chunk is not None
        assert chunk.metadata == {}

    def test_line_start_default(self):
        chunk = _as_chunk({"content": "x"}, "f.py")
        assert chunk is not None
        assert chunk.line_start == 1

    def test_line_range(self):
        chunk = _as_chunk({"content": "x", "line_start": 10, "line_end": 20}, "f.py")
        assert chunk is not None
        assert chunk.line_start == 10
        assert chunk.line_end == 20

    def test_chunk_id_uses_content_hash_when_no_id(self):
        # Two identical items from the same file should get the same generated id
        c1 = _as_chunk({"content": "hello"}, "file.py")
        c2 = _as_chunk({"content": "hello"}, "file.py")
        assert c1 is not None and c2 is not None
        assert c1.id == c2.id  # hash-based, deterministic
