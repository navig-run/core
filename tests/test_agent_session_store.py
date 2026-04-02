"""Tests for navig.agent.session_store (FA4 — Session Transcript / Checkpoints).

Covers:
- SessionEntry dataclass (serialisation, to_message)
- SessionMetadata dataclass
- Session ID generation
- SessionStore CRUD (append, load, resume)
- Compact boundary markers
- Session finalization
- Session listing and workspace filtering
- get_latest helper
- Cleanup of old sessions
- Edge cases (corrupt lines, empty files, missing dirs)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.agent.session_store import (
    SessionEntry,
    SessionMetadata,
    SessionStore,
    _generate_session_id,
    cleanup_old_sessions,
)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


@pytest.fixture()
def session_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for session storage."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


def _make_store(session_dir: Path, sid: str = "test_001", workspace: str = "") -> SessionStore:
    return SessionStore(session_id=sid, base_dir=session_dir, workspace=workspace)


# ─────────────────────────────────────────────────────────────
# TestSessionEntry
# ─────────────────────────────────────────────────────────────


class TestSessionEntry:
    """SessionEntry dataclass basics."""

    def test_auto_timestamp(self):
        entry = SessionEntry(role="user", content="hello")
        assert entry.timestamp > 0

    def test_explicit_timestamp(self):
        entry = SessionEntry(role="user", content="hi", timestamp=1000.0)
        assert entry.timestamp == 1000.0

    def test_to_dict_roundtrip(self):
        entry = SessionEntry(role="assistant", content="answer", tokens_used=100)
        d = entry.to_dict()
        restored = SessionEntry.from_dict(d)
        assert restored.role == "assistant"
        assert restored.content == "answer"
        assert restored.tokens_used == 100

    def test_to_dict_all_fields(self):
        entry = SessionEntry(
            role="assistant",
            content="hi",
            timestamp=123.0,
            tool_calls=[{"id": "t1"}],
            tool_results=[{"tool_call_id": "t1", "content": "ok"}],
            is_compact_boundary=True,
            tokens_used=50,
            model="claude-4",
            cost=0.01,
        )
        d = entry.to_dict()
        assert d["role"] == "assistant"
        assert d["tool_calls"] == [{"id": "t1"}]
        assert d["is_compact_boundary"] is True
        assert d["model"] == "claude-4"
        assert d["cost"] == 0.01

    def test_to_message_basic(self):
        entry = SessionEntry(role="user", content="question")
        msg = entry.to_message()
        assert msg == {"role": "user", "content": "question"}

    def test_to_message_with_tool_calls(self):
        tc = [{"id": "tc_1", "function": {"name": "read_file", "arguments": "{}"}}]
        entry = SessionEntry(role="assistant", content="", tool_calls=tc)
        msg = entry.to_message()
        assert msg["tool_calls"] == tc

    def test_to_message_tool_role_with_results(self):
        results = [{"tool_call_id": "tc_1", "content": "file contents"}]
        entry = SessionEntry(role="tool", content="file contents", tool_results=results)
        msg = entry.to_message()
        assert msg["tool_call_id"] == "tc_1"

    def test_to_message_tool_role_empty_results(self):
        entry = SessionEntry(role="tool", content="data", tool_results=[])
        msg = entry.to_message()
        assert "tool_call_id" not in msg

    def test_from_dict_ignores_extra_keys(self):
        """Extra keys in the dict should not raise."""
        d = {"role": "user", "content": "hi", "unknown_key": True, "z_extra": 99}
        entry = SessionEntry.from_dict(d)
        assert entry.role == "user"
        assert not hasattr(entry, "unknown_key")

    def test_default_fields(self):
        entry = SessionEntry(role="user")
        assert entry.content == ""
        assert entry.tool_calls == []
        assert entry.tool_results == []
        assert entry.is_compact_boundary is False
        assert entry.model == ""
        assert entry.cost == 0.0
        assert entry.tokens_used == 0


# ─────────────────────────────────────────────────────────────
# TestSessionMetadata
# ─────────────────────────────────────────────────────────────


class TestSessionMetadata:
    """SessionMetadata dataclass."""

    def test_roundtrip(self):
        meta = SessionMetadata(
            session_id="s1",
            created_at=1000.0,
            last_active=2000.0,
            turn_count=5,
            total_tokens=500,
            summary="Test session",
            workspace="/tmp/proj",
            tags=["debug"],
        )
        d = meta.to_dict()
        restored = SessionMetadata.from_dict(d)
        assert restored.session_id == "s1"
        assert restored.turn_count == 5
        assert restored.tags == ["debug"]
        assert restored.workspace == "/tmp/proj"

    def test_defaults(self):
        meta = SessionMetadata(session_id="s2")
        assert meta.finalized is False
        assert meta.total_cost == 0.0
        assert meta.summary == ""
        assert meta.tags == []

    def test_from_dict_ignores_extra(self):
        d = {"session_id": "s3", "extra": "ignored"}
        meta = SessionMetadata.from_dict(d)
        assert meta.session_id == "s3"

    def test_total_cost_roundtrip(self):
        meta = SessionMetadata(session_id="s4", total_cost=1.234)
        d = meta.to_dict()
        restored = SessionMetadata.from_dict(d)
        assert abs(restored.total_cost - 1.234) < 1e-6


# ─────────────────────────────────────────────────────────────
# TestSessionIdGeneration
# ─────────────────────────────────────────────────────────────


class TestSessionIdGeneration:
    """Session ID format."""

    def test_format_pattern(self):
        import re
        sid = _generate_session_id()
        assert re.match(r"^\d{8}_\d{6}_[0-9a-f]{4}$", sid)

    def test_uniqueness(self):
        ids = {_generate_session_id() for _ in range(50)}
        assert len(ids) == 50  # all unique

    def test_sortable_across_seconds(self):
        """IDs generated in different seconds sort chronologically."""
        import re
        id1 = _generate_session_id()
        # Patch time to be 2 seconds later
        with patch("navig.agent.session_store.time") as mock_time:
            mock_time.strftime.return_value = "20301231_235959"
            mock_time.time.return_value = 9999999999.0
            id2 = _generate_session_id()
        assert id2 > id1  # later timestamp sorts after

    def test_length(self):
        sid = _generate_session_id()
        # YYYYMMDD_HHMMSS_XXXX = 8+1+6+1+4 = 20 chars
        assert len(sid) == 20


# ─────────────────────────────────────────────────────────────
# TestSessionStoreInit
# ─────────────────────────────────────────────────────────────


class TestSessionStoreInit:
    """Constructor behavior."""

    def test_auto_generated_id(self, session_dir: Path):
        import re
        store = SessionStore(base_dir=session_dir)
        assert re.match(r"^\d{8}_\d{6}_[0-9a-f]{4}$", store.session_id)

    def test_explicit_id(self, session_dir: Path):
        store = SessionStore(session_id="my_custom_id", base_dir=session_dir)
        assert store.session_id == "my_custom_id"

    def test_file_paths(self, session_dir: Path):
        store = SessionStore(session_id="test", base_dir=session_dir)
        assert store.file == session_dir / "test.jsonl"
        assert store.meta_file == session_dir / "test.meta.json"

    def test_workspace_stored(self, session_dir: Path):
        store = SessionStore(session_id="ws", base_dir=session_dir, workspace="/proj")
        store.append(SessionEntry(role="user", content="x"))
        meta = store.get_metadata()
        assert meta.workspace == "/proj"

    def test_creates_directories(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c"
        store = SessionStore(session_id="nested", base_dir=nested)
        store.append(SessionEntry(role="user", content="x"))
        assert nested.exists()


# ─────────────────────────────────────────────────────────────
# TestSessionStoreAppendLoad
# ─────────────────────────────────────────────────────────────


class TestSessionStoreAppendLoad:
    """Append and load operations."""

    def test_append_creates_file(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="hi"))
        assert store.file.exists()

    def test_append_creates_meta_file(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="hi"))
        assert store.meta_file.exists()

    def test_load_roundtrip(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="hello"))
        store.append(SessionEntry(role="assistant", content="hi"))
        entries = store.load()
        assert len(entries) == 2
        assert entries[0].role == "user"
        assert entries[1].content == "hi"

    def test_load_empty_session(self, session_dir: Path):
        store = _make_store(session_dir, sid="nonexistent")
        assert store.load() == []

    def test_append_increments_turn_count(self, session_dir: Path):
        store = _make_store(session_dir)
        for i in range(5):
            store.append(SessionEntry(role="user", content=f"msg {i}"))
        meta = store.get_metadata()
        assert meta.turn_count == 5

    def test_append_accumulates_tokens(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="a", tokens_used=100))
        store.append(SessionEntry(role="assistant", content="b", tokens_used=200))
        meta = store.get_metadata()
        assert meta.total_tokens == 300

    def test_append_accumulates_cost(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="a", cost=0.01))
        store.append(SessionEntry(role="assistant", content="b", cost=0.02))
        meta = store.get_metadata()
        assert abs(meta.total_cost - 0.03) < 1e-6

    def test_ndjson_format(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="line1"))
        store.append(SessionEntry(role="assistant", content="line2"))
        lines = store.file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "role" in obj
            assert "content" in obj

    def test_append_updates_last_active(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="a", timestamp=1000.0))
        meta = store.get_metadata()
        assert meta.last_active == 1000.0
        store.append(SessionEntry(role="user", content="b", timestamp=2000.0))
        meta = store.get_metadata()
        assert meta.last_active == 2000.0


# ─────────────────────────────────────────────────────────────
# TestCompactBoundary
# ─────────────────────────────────────────────────────────────


class TestCompactBoundary:
    """Compact boundary markers."""

    def test_mark_compact_boundary(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="old"))
        store.mark_compact_boundary()
        store.append(SessionEntry(role="user", content="new"))
        entries = store.load()
        assert len(entries) == 3
        assert entries[1].is_compact_boundary is True
        assert entries[1].role == "system"
        assert entries[1].content == "[compact boundary]"

    def test_resume_starts_after_boundary(self, session_dir: Path):
        store = _make_store(session_dir)
        for i in range(5):
            store.append(SessionEntry(role="user", content=f"old_{i}"))
        store.mark_compact_boundary()
        for i in range(3):
            store.append(SessionEntry(role="user", content=f"new_{i}"))

        messages = store.resume()
        assert len(messages) == 3
        assert messages[0]["content"] == "new_0"
        assert messages[2]["content"] == "new_2"

    def test_resume_multiple_boundaries(self, session_dir: Path):
        store = _make_store(session_dir)
        for i in range(3):
            store.append(SessionEntry(role="user", content=f"batch1_{i}"))
        store.mark_compact_boundary()
        for i in range(3):
            store.append(SessionEntry(role="user", content=f"batch2_{i}"))
        store.mark_compact_boundary()
        for i in range(2):
            store.append(SessionEntry(role="user", content=f"batch3_{i}"))

        messages = store.resume()
        assert len(messages) == 2
        assert messages[0]["content"] == "batch3_0"
        assert messages[1]["content"] == "batch3_1"

    def test_resume_no_boundary(self, session_dir: Path):
        store = _make_store(session_dir)
        for i in range(5):
            store.append(SessionEntry(role="user", content=f"msg_{i}"))
        messages = store.resume()
        assert len(messages) == 5

    def test_resume_max_entries(self, session_dir: Path):
        store = _make_store(session_dir)
        for i in range(20):
            store.append(SessionEntry(role="user", content=f"msg_{i}"))
        messages = store.resume(max_entries=5)
        assert len(messages) == 5
        assert messages[0]["content"] == "msg_15"

    def test_resume_empty_session(self, session_dir: Path):
        store = _make_store(session_dir, sid="empty")
        assert store.resume() == []

    def test_boundary_markers_excluded_from_resume(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="before"))
        store.mark_compact_boundary()
        store.append(SessionEntry(role="user", content="after"))
        messages = store.resume()
        for msg in messages:
            assert msg["content"] != "[compact boundary]"

    def test_resume_max_entries_after_boundary(self, session_dir: Path):
        store = _make_store(session_dir)
        store.mark_compact_boundary()
        for i in range(10):
            store.append(SessionEntry(role="user", content=f"post_{i}"))
        messages = store.resume(max_entries=3)
        assert len(messages) == 3
        assert messages[0]["content"] == "post_7"


# ─────────────────────────────────────────────────────────────
# TestFinalize
# ─────────────────────────────────────────────────────────────


class TestFinalize:
    """Session finalization."""

    def test_finalize_sets_flag(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="hi"))
        store.finalize()
        meta = store.get_metadata()
        assert meta.finalized is True

    def test_finalize_with_summary(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="work"))
        store.finalize(summary="Did some work")
        meta = store.get_metadata()
        assert meta.summary == "Did some work"

    def test_finalize_updates_last_active(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="x"))
        before = store.get_metadata().last_active
        time.sleep(0.02)
        store.finalize()
        after = store.get_metadata().last_active
        assert after >= before

    def test_not_finalized_by_default(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="x"))
        meta = store.get_metadata()
        assert meta.finalized is False


# ─────────────────────────────────────────────────────────────
# TestListSessions
# ─────────────────────────────────────────────────────────────


class TestListSessions:
    """Session listing and search."""

    def _create_session(
        self, session_dir: Path, sid: str, workspace: str = "", last_active: float = 0.0,
    ) -> SessionStore:
        store = SessionStore(session_id=sid, base_dir=session_dir, workspace=workspace)
        store.append(SessionEntry(role="user", content="hi"))
        if last_active:
            meta = store.get_metadata()
            meta.last_active = last_active
            store._save_meta(meta)
        return store

    def test_list_empty(self, session_dir: Path):
        assert SessionStore.list_sessions(base_dir=session_dir) == []

    def test_list_sorted_by_last_active(self, session_dir: Path):
        self._create_session(session_dir, "s1", last_active=1000.0)
        self._create_session(session_dir, "s2", last_active=3000.0)
        self._create_session(session_dir, "s3", last_active=2000.0)
        metas = SessionStore.list_sessions(base_dir=session_dir)
        assert [m.session_id for m in metas] == ["s2", "s3", "s1"]

    def test_list_limit(self, session_dir: Path):
        for i in range(10):
            self._create_session(session_dir, f"s{i:02d}", last_active=float(i))
        metas = SessionStore.list_sessions(limit=3, base_dir=session_dir)
        assert len(metas) == 3

    def test_list_missing_directory(self, tmp_path: Path):
        nonexistent = tmp_path / "does_not_exist"
        assert SessionStore.list_sessions(base_dir=nonexistent) == []


# ─────────────────────────────────────────────────────────────
# TestFindByWorkspace
# ─────────────────────────────────────────────────────────────


class TestFindByWorkspace:
    """Workspace filtering."""

    def _create_session(
        self, session_dir: Path, sid: str, workspace: str = "", last_active: float = 0.0,
    ) -> SessionStore:
        store = SessionStore(session_id=sid, base_dir=session_dir, workspace=workspace)
        store.append(SessionEntry(role="user", content="hi"))
        if last_active:
            meta = store.get_metadata()
            meta.last_active = last_active
            store._save_meta(meta)
        return store

    def test_find_by_workspace(self, session_dir: Path):
        self._create_session(session_dir, "proj_a", workspace="/home/user/proj_a")
        self._create_session(session_dir, "proj_b", workspace="/home/user/proj_b")
        self._create_session(session_dir, "proj_a2", workspace="/home/user/proj_a")
        results = SessionStore.find_by_workspace("/home/user/proj_a", base_dir=session_dir)
        assert len(results) == 2
        ids = {m.session_id for m in results}
        assert ids == {"proj_a", "proj_a2"}

    def test_find_by_workspace_case_insensitive(self, session_dir: Path):
        self._create_session(session_dir, "win_proj", workspace="C:\\Users\\Me\\Project")
        results = SessionStore.find_by_workspace("c:\\users\\me\\project", base_dir=session_dir)
        assert len(results) == 1

    def test_find_by_workspace_no_match(self, session_dir: Path):
        self._create_session(session_dir, "s1", workspace="/other")
        results = SessionStore.find_by_workspace("/nonexistent", base_dir=session_dir)
        assert len(results) == 0

    def test_find_by_workspace_empty_workspace(self, session_dir: Path):
        self._create_session(session_dir, "no_ws", workspace="")
        results = SessionStore.find_by_workspace("/some/path", base_dir=session_dir)
        assert len(results) == 0


# ─────────────────────────────────────────────────────────────
# TestGetLatest
# ─────────────────────────────────────────────────────────────


class TestGetLatest:
    """get_latest class method."""

    def test_get_latest_no_sessions(self, session_dir: Path):
        assert SessionStore.get_latest(base_dir=session_dir) is None

    def test_get_latest_returns_most_recent(self, session_dir: Path):
        s1 = SessionStore(session_id="old", base_dir=session_dir)
        s1.append(SessionEntry(role="user", content="old"))
        meta1 = s1.get_metadata()
        meta1.last_active = 1000.0
        s1._save_meta(meta1)

        s2 = SessionStore(session_id="new", base_dir=session_dir)
        s2.append(SessionEntry(role="user", content="new"))
        meta2 = s2.get_metadata()
        meta2.last_active = 9000.0
        s2._save_meta(meta2)

        latest = SessionStore.get_latest(base_dir=session_dir)
        assert latest is not None
        assert latest.session_id == "new"

    def test_get_latest_with_workspace(self, session_dir: Path):
        s1 = SessionStore(session_id="wrong", base_dir=session_dir, workspace="/other")
        s1.append(SessionEntry(role="user", content="x"))

        s2 = SessionStore(session_id="right", base_dir=session_dir, workspace="/my/proj")
        s2.append(SessionEntry(role="user", content="x"))

        latest = SessionStore.get_latest(base_dir=session_dir, workspace="/my/proj")
        assert latest is not None
        assert latest.session_id == "right"


# ─────────────────────────────────────────────────────────────
# TestCleanup
# ─────────────────────────────────────────────────────────────


class TestCleanup:
    """cleanup_old_sessions function."""

    def _create_old_session(self, session_dir: Path, sid: str, days_old: int) -> None:
        store = SessionStore(session_id=sid, base_dir=session_dir)
        store.append(SessionEntry(role="user", content="old data"))
        meta = store.get_metadata()
        meta.last_active = time.time() - (days_old * 86_400)
        store._save_meta(meta)

    def test_cleanup_removes_old(self, session_dir: Path):
        self._create_old_session(session_dir, "ancient", days_old=100)
        self._create_old_session(session_dir, "recent", days_old=10)
        removed = cleanup_old_sessions(max_age_days=90, base_dir=session_dir)
        assert removed == 1
        assert not (session_dir / "ancient.jsonl").exists()
        assert not (session_dir / "ancient.meta.json").exists()
        assert (session_dir / "recent.jsonl").exists()

    def test_cleanup_custom_age(self, session_dir: Path):
        self._create_old_session(session_dir, "s1", days_old=10)
        self._create_old_session(session_dir, "s2", days_old=3)
        removed = cleanup_old_sessions(max_age_days=5, base_dir=session_dir)
        assert removed == 1

    def test_cleanup_empty_dir(self, session_dir: Path):
        assert cleanup_old_sessions(base_dir=session_dir) == 0

    def test_cleanup_nonexistent_dir(self, tmp_path: Path):
        result = cleanup_old_sessions(base_dir=tmp_path / "nope")
        assert result == 0

    def test_cleanup_all_old(self, session_dir: Path):
        for i in range(5):
            self._create_old_session(session_dir, f"old_{i}", days_old=200)
        removed = cleanup_old_sessions(max_age_days=90, base_dir=session_dir)
        assert removed == 5

    def test_cleanup_preserves_recent(self, session_dir: Path):
        self._create_old_session(session_dir, "keep1", days_old=30)
        self._create_old_session(session_dir, "keep2", days_old=60)
        removed = cleanup_old_sessions(max_age_days=90, base_dir=session_dir)
        assert removed == 0


# ─────────────────────────────────────────────────────────────
# TestEdgeCases
# ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and robustness."""

    def test_unicode_content(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="日本語テスト 🚀"))
        entries = store.load()
        assert entries[0].content == "日本語テスト 🚀"

    def test_large_content(self, session_dir: Path):
        store = _make_store(session_dir)
        big = "x" * 100_000
        store.append(SessionEntry(role="assistant", content=big))
        entries = store.load()
        assert len(entries[0].content) == 100_000

    def test_empty_content(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="assistant", content=""))
        entries = store.load()
        assert entries[0].content == ""

    def test_reload_from_disk(self, session_dir: Path):
        """A new SessionStore instance can read what a previous one wrote."""
        store1 = _make_store(session_dir, sid="shared")
        store1.append(SessionEntry(role="user", content="persisted"))

        store2 = SessionStore(session_id="shared", base_dir=session_dir)
        entries = store2.load()
        assert len(entries) == 1
        assert entries[0].content == "persisted"

    def test_concurrent_appends_safe(self, session_dir: Path):
        """Multiple append calls don't corrupt the file."""
        store = _make_store(session_dir)
        for i in range(100):
            store.append(SessionEntry(role="user", content=f"msg_{i}"))
        entries = store.load()
        assert len(entries) == 100

    def test_corrupt_line_skipped(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="good"))
        # Inject a corrupt line
        with open(store.file, "a", encoding="utf-8") as f:
            f.write("NOT VALID JSON\n")
        store.append(SessionEntry(role="assistant", content="also good"))
        # Force reload
        entries = store.load()
        assert len(entries) == 2
        assert entries[0].content == "good"
        assert entries[1].content == "also good"

    def test_corrupt_meta_recovers(self, session_dir: Path):
        store = _make_store(session_dir)
        store.append(SessionEntry(role="user", content="first"))

        # Corrupt the meta file
        store.meta_file.write_text("NOT JSON", encoding="utf-8")
        store._meta = None  # force reload

        # Next append should create fresh metadata
        store.append(SessionEntry(role="user", content="second"))
        meta = store.get_metadata()
        # Fresh meta only counted "second"
        assert meta.turn_count == 1

    def test_meta_persists_across_instances(self, session_dir: Path):
        store1 = _make_store(session_dir, sid="persist_meta")
        store1.append(SessionEntry(role="user", content="a", tokens_used=50))
        store1.append(SessionEntry(role="user", content="b", tokens_used=75))

        store2 = SessionStore(session_id="persist_meta", base_dir=session_dir)
        meta = store2.get_metadata()
        assert meta.turn_count == 2
        assert meta.total_tokens == 125

    def test_empty_entries_file_with_existing_meta(self, session_dir: Path):
        """Meta exists but JSONL is empty — load returns empty."""
        store = _make_store(session_dir, sid="meta_only")
        store.append(SessionEntry(role="user", content="x"))
        # Wipe JSONL but keep meta
        store.file.write_text("", encoding="utf-8")
        entries = store.load()
        assert entries == []
