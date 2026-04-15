"""Tests for navig.file_history — FileHistoryStore."""

from __future__ import annotations

from pathlib import Path
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path, enabled: bool = True):
    from navig.file_history import FileHistoryStore

    store = FileHistoryStore(cache_dir=tmp_path / "file-cache")
    # Monkeypatch _is_enabled so tests work without a full config setup
    store._is_enabled = lambda: enabled
    return store


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# checkpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckpoint:
    def test_creates_backup(self, tmp_path):
        store = _make_store(tmp_path)
        src = tmp_path / "app.php"
        _write_file(src, "<?php echo 'hello'; ?>")

        backup = store.checkpoint(src, session_id="sess1", turn_id="turn1")
        assert backup is not None
        assert backup.exists()
        assert backup.read_text() == "<?php echo 'hello'; ?>"

    def test_returns_none_when_disabled(self, tmp_path):
        store = _make_store(tmp_path, enabled=False)
        src = tmp_path / "app.php"
        _write_file(src, "content")
        assert store.checkpoint(src, session_id="s", turn_id="t") is None

    def test_returns_none_for_nonexistent_file(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.checkpoint(tmp_path / "nope.txt", session_id="s", turn_id="t") is None


# ─────────────────────────────────────────────────────────────────────────────
# list_versions
# ─────────────────────────────────────────────────────────────────────────────

class TestListVersions:
    def test_empty_when_no_snapshots(self, tmp_path):
        store = _make_store(tmp_path)
        versions = store.list_versions(tmp_path / "missing.php", session_id="sess1")
        assert versions == []

    def test_returns_versions_sorted_oldest_first(self, tmp_path):
        store = _make_store(tmp_path)
        src = tmp_path / "config.php"
        _write_file(src, "v1")
        store.checkpoint(src, session_id="sess2", turn_id="t001")

        _write_file(src, "v2")
        store.checkpoint(src, session_id="sess2", turn_id="t002")

        versions = store.list_versions(src, session_id="sess2")
        assert len(versions) == 2
        assert versions[0].turn_id == "t001"
        assert versions[1].turn_id == "t002"


# ─────────────────────────────────────────────────────────────────────────────
# restore
# ─────────────────────────────────────────────────────────────────────────────

class TestRestore:
    def test_restores_content(self, tmp_path):
        store = _make_store(tmp_path)
        src = tmp_path / "code.py"
        _write_file(src, "version A")
        store.checkpoint(src, session_id="sess3", turn_id="t1")

        _write_file(src, "version B (modified)")
        versions = store.list_versions(src, session_id="sess3")
        ok = store.restore(versions[0])
        assert ok is True
        assert src.read_text() == "version A"

    def test_restore_returns_false_for_missing_backup(self, tmp_path):
        from navig.file_history import FileVersion
        from datetime import datetime, timezone

        store = _make_store(tmp_path)
        fake_version = FileVersion(
            original_path=str(tmp_path / "ghost.txt"),
            backup_path=tmp_path / "nonexistent.bak",
            session_id="s",
            turn_id="t",
            captured_at=datetime.now(tz=timezone.utc),
            size_bytes=0,
        )
        assert store.restore(fake_version) is False


# ─────────────────────────────────────────────────────────────────────────────
# diff_versions
# ─────────────────────────────────────────────────────────────────────────────

class TestDiffVersions:
    def test_diff_shows_changes(self, tmp_path):
        store = _make_store(tmp_path)
        src = tmp_path / "script.sh"
        _write_file(src, "line1\nline2\n")
        store.checkpoint(src, session_id="sess4", turn_id="t1")

        _write_file(src, "line1\nline2\nline3\n")
        store.checkpoint(src, session_id="sess4", turn_id="t2")

        versions = store.list_versions(src, session_id="sess4")
        diff = store.diff_versions(versions[0], versions[1])
        assert "+line3" in diff

    def test_diff_reports_no_differences(self, tmp_path):
        store = _make_store(tmp_path)
        src = tmp_path / "same.txt"
        _write_file(src, "identical\n")
        store.checkpoint(src, session_id="sess5", turn_id="t1")
        store.checkpoint(src, session_id="sess5", turn_id="t2")

        versions = store.list_versions(src, session_id="sess5")
        diff = store.diff_versions(versions[0], versions[1])
        assert diff == "(no differences)"


# ─────────────────────────────────────────────────────────────────────────────
# Eviction
# ─────────────────────────────────────────────────────────────────────────────

class TestEviction:
    def test_evicts_oldest_when_cap_exceeded(self, tmp_path):
        from navig.file_history import FileHistoryStore

        store = FileHistoryStore(cache_dir=tmp_path / "cache", max_snapshots=3)
        store._is_enabled = lambda: True

        src = tmp_path / "target.py"
        for i in range(5):
            _write_file(src, f"version {i}")
            store.checkpoint(src, session_id="sess6", turn_id=f"t{i:03d}")

        versions = store.list_versions(src, session_id="sess6")
        assert len(versions) == 3
        # Oldest should be evicted
        assert versions[0].turn_id == "t002"
