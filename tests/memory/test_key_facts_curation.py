"""Tests for memory curation: propose → approve/reject, retrieval gating, export/import.

Covers the "memory you own" flow added in the 2030 build-out.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from navig.memory.key_facts import KeyFact, KeyFactStore, _utcnow


def _store() -> KeyFactStore:
    return KeyFactStore(db_path=Path(tempfile.mkdtemp()) / "kf.db")


class TestApprovedRoundTrip:
    def test_default_is_approved(self):
        f = KeyFact(content="x")
        assert f.approved == 1
        assert "approved" in f.to_dict()

    def test_pending_persists_and_reads_back(self):
        s = _store()
        s.upsert(KeyFact(content="proposed thing", approved=None))
        pending = s.get_pending()
        assert [f.content for f in pending] == ["proposed thing"]
        assert pending[0].approved is None


class TestRetrievalGating:
    def test_get_active_approved_filter_excludes_pending_and_rejected(self):
        s = _store()
        s.upsert(KeyFact(content="approved fact", approved=1))
        s.upsert(KeyFact(content="pending fact", approved=None))
        s.upsert(KeyFact(content="rejected fact", approved=0))
        approved = {f.content for f in s.get_active(approved=1)}
        assert approved == {"approved fact"}
        # No filter returns everything active.
        assert len(s.get_active()) == 3


class TestApproveReject:
    def test_approve_moves_pending_to_active(self):
        s = _store()
        s.upsert(KeyFact(content="p", approved=None))
        fid = s.get_pending()[0].id
        assert s.approve(fid) is True
        assert s.get_pending() == []
        assert {f.content for f in s.get_active(approved=1)} == {"p"}

    def test_reject_records_reason_and_excludes_from_retrieval(self):
        s = _store()
        s.upsert(KeyFact(content="bad", approved=None))
        fid = s.get_pending()[0].id
        assert s.reject(fid, reason="not durable") is True
        assert s.get_pending() == []
        assert s.get_active(approved=1) == []
        assert s.get(fid).metadata.get("rejection_reason") == "not durable"

    def test_approve_all_pending(self):
        s = _store()
        s.upsert(KeyFact(content="a", approved=None))
        s.upsert(KeyFact(content="b", approved=None))
        assert s.approve_all_pending() == 2
        assert s.get_pending() == []


class TestExportImport:
    def test_json_round_trip(self):
        s = _store()
        s.upsert(KeyFact(content="keep me", category="preference", approved=1))
        s.upsert(KeyFact(content="pending", approved=None))
        js = s.export_json(approved_only=True)
        s2 = _store()
        added, merged = s2.import_json(js)
        assert added == 1 and merged == 0
        assert {f.content for f in s2.get_active(approved=1)} == {"keep me"}

    def test_markdown_export_groups_by_category(self):
        s = _store()
        s.upsert(KeyFact(content="likes dark mode", category="preference", approved=1))
        md = s.export_markdown()
        assert "## preference" in md
        assert "likes dark mode" in md

    def test_import_invalid_json_raises_valueerror(self):
        s = _store()
        with pytest.raises(ValueError):
            s.import_json("{not json")


class TestMigration:
    def test_legacy_db_backfilled_and_idempotent(self):
        d = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(str(d))
        conn.execute(
            """CREATE TABLE key_facts (id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT,
            tags TEXT DEFAULT '[]', confidence REAL DEFAULT 0.8, source_conversation_id TEXT,
            source_platform TEXT, created_at TEXT, updated_at TEXT, superseded_by TEXT,
            deleted INTEGER DEFAULT 0, access_count INTEGER DEFAULT 0, last_accessed TEXT,
            embedding TEXT, metadata TEXT DEFAULT '{}')"""
        )
        conn.execute(
            "INSERT INTO key_facts (id,content,category,created_at,updated_at) VALUES "
            "('x','legacy','context',?,?)",
            (_utcnow(), _utcnow()),
        )
        conn.commit()
        conn.close()

        s = KeyFactStore(db_path=d)
        # legacy row backfilled to approved=1 (still retrievable)
        assert {f.content for f in s.get_active(approved=1)} == {"legacy"}
        # a new pending insert stays pending
        s.upsert(KeyFact(content="new pending", approved=None))
        assert [f.content for f in s.get_pending()] == ["new pending"]
        # re-open: migration is idempotent (does NOT re-backfill the pending row)
        s2 = KeyFactStore(db_path=d)
        assert [f.content for f in s2.get_pending()] == ["new pending"]
