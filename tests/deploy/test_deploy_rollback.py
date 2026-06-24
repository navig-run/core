"""Tests for navig/deploy/rollback.py"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.deploy.models import BackupConfig, SnapshotRecord
from navig.deploy.rollback import RollbackManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout="", stderr=""):
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = stderr
    return r


def _fail(stdout="", stderr="err"):
    r = MagicMock()
    r.returncode = 1
    r.stdout = stdout
    r.stderr = stderr
    return r


def _make_manager(tmp_path, backup_cfg=None, dry_run=False, remote=None):
    if backup_cfg is None:
        backup_cfg = BackupConfig(enabled=True, remote_path="/var/backups", keep_last=5)
    if remote is None:
        remote = MagicMock()
        remote.execute_command.return_value = _ok()
    return RollbackManager(
        backup_cfg=backup_cfg,
        deploy_target="/var/www/myapp",
        app_name="myapp",
        server_config={"host": "example.com"},
        remote_ops=remote,
        cache_dir=tmp_path,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_state_path_includes_app_name(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert "myapp" in mgr._state_path.name

    def test_target_trailing_slash_stripped(self, tmp_path):
        backup_cfg = BackupConfig(enabled=True, remote_path="/var/backups", keep_last=5)
        mgr = RollbackManager(
            backup_cfg=backup_cfg,
            deploy_target="/var/www/myapp/",
            app_name="myapp",
            server_config={},
            remote_ops=MagicMock(),
            cache_dir=tmp_path,
        )
        assert not mgr._target.endswith("/")

    def test_snapshot_base_combines_remote_path_and_app(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert "/var/backups" in mgr._snapshot_base
        assert "myapp" in mgr._snapshot_base


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------


class TestCreateSnapshot:
    def test_returns_none_when_backup_disabled(self, tmp_path):
        mgr = _make_manager(tmp_path, backup_cfg=BackupConfig(enabled=False))
        result = mgr.create_snapshot()
        assert result is None

    def test_dry_run_returns_record_without_remote_call(self, tmp_path):
        remote = MagicMock()
        mgr = _make_manager(tmp_path, dry_run=True, remote=remote)
        result = mgr.create_snapshot()
        assert isinstance(result, SnapshotRecord)
        remote.execute_command.assert_not_called()

    def test_creates_snapshot_on_remote(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok()
        mgr = _make_manager(tmp_path, remote=remote)
        result = mgr.create_snapshot()
        assert isinstance(result, SnapshotRecord)
        assert remote.execute_command.call_count == 2  # mkdir + cp

    def test_raises_on_mkdir_failure(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _fail(stderr="Permission denied")
        mgr = _make_manager(tmp_path, remote=remote)
        with pytest.raises(RuntimeError, match="Could not create snapshot dir"):
            mgr.create_snapshot()

    def test_raises_on_cp_failure(self, tmp_path):
        remote = MagicMock()
        # First call (mkdir) succeeds, second call (cp) fails
        remote.execute_command.side_effect = [_ok(), _fail(stderr="No space left")]
        mgr = _make_manager(tmp_path, remote=remote)
        with pytest.raises(RuntimeError, match="Snapshot failed"):
            mgr.create_snapshot()

    def test_snapshot_path_contains_timestamp(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok()
        mgr = _make_manager(tmp_path, remote=remote)
        result = mgr.create_snapshot()
        # Snapshot path should look like /var/backups/myapp/20240101_120000
        assert result.path.startswith("/var/backups/myapp/")
        assert len(result.created_at) > 0


# ---------------------------------------------------------------------------
# restore_snapshot
# ---------------------------------------------------------------------------


class TestRestoreSnapshot:
    def test_returns_false_when_no_snapshot(self, tmp_path):
        mgr = _make_manager(tmp_path)
        success, msg = mgr.restore_snapshot(snapshot=None)
        assert success is False
        assert "No snapshot" in msg

    def test_dry_run_returns_true_without_remote_call(self, tmp_path):
        remote = MagicMock()
        mgr = _make_manager(tmp_path, dry_run=True, remote=remote)
        snap = SnapshotRecord(path="/var/backups/myapp/20240101", created_at="20240101")
        success, msg = mgr.restore_snapshot(snapshot=snap)
        assert success is True
        assert "DRY RUN" in msg
        remote.execute_command.assert_not_called()

    def test_success_on_restore(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok()
        mgr = _make_manager(tmp_path, remote=remote)
        snap = SnapshotRecord(path="/var/backups/myapp/20240101", created_at="20240101")
        success, msg = mgr.restore_snapshot(snapshot=snap)
        assert success is True
        assert "Restored" in msg

    def test_failure_on_restore_error(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _fail(stderr="Disk error")
        mgr = _make_manager(tmp_path, remote=remote)
        snap = SnapshotRecord(path="/var/backups/myapp/20240101", created_at="20240101")
        success, msg = mgr.restore_snapshot(snapshot=snap)
        assert success is False
        assert "Restore failed" in msg


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


class TestStateFile:
    def test_save_and_load_roundtrip(self, tmp_path):
        mgr = _make_manager(tmp_path)
        record = SnapshotRecord(path="/var/backups/myapp/20240101_120000", created_at="20240101_120000")
        with patch("navig.deploy.rollback.atomic_write_text") as mock_write:
            # Capture written content
            written = {}
            def fake_write(path, content):
                written["content"] = content
            mock_write.side_effect = fake_write
            mgr.save_state(record)
        data = json.loads(written["content"])
        assert data["path"] == record.path
        assert data["created_at"] == record.created_at

    def test_load_state_returns_none_when_file_missing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        result = mgr.load_state()
        assert result is None

    def test_load_state_reads_existing_file(self, tmp_path):
        mgr = _make_manager(tmp_path)
        state = {"path": "/var/backups/myapp/20240101", "created_at": "20240101"}
        mgr._state_path.parent.mkdir(parents=True, exist_ok=True)
        mgr._state_path.write_text(json.dumps(state), encoding="utf-8")
        result = mgr.load_state()
        assert result is not None
        assert result.path == "/var/backups/myapp/20240101"

    def test_load_state_returns_none_on_corrupt_file(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._state_path.parent.mkdir(parents=True, exist_ok=True)
        mgr._state_path.write_text("not json", encoding="utf-8")
        result = mgr.load_state()
        assert result is None


# ---------------------------------------------------------------------------
# prune_old_snapshots
# ---------------------------------------------------------------------------


class TestPruneOldSnapshots:
    def test_dry_run_skips_pruning(self, tmp_path):
        remote = MagicMock()
        mgr = _make_manager(tmp_path, dry_run=True, remote=remote)
        mgr.prune_old_snapshots()
        remote.execute_command.assert_not_called()

    def test_keep_zero_skips_pruning(self, tmp_path):
        remote = MagicMock()
        backup_cfg = BackupConfig(enabled=True, remote_path="/var/backups", keep_last=0)
        mgr = _make_manager(tmp_path, backup_cfg=backup_cfg, remote=remote)
        mgr.prune_old_snapshots()
        remote.execute_command.assert_not_called()

    def test_prunes_old_snapshots_returned_by_ls(self, tmp_path):
        remote = MagicMock()
        old_snaps = "/var/backups/myapp/20230101/\n/var/backups/myapp/20230102/"
        remote.execute_command.side_effect = [
            _ok(stdout=old_snaps),  # ls call
            _ok(),  # rm first
            _ok(),  # rm second
        ]
        mgr = _make_manager(tmp_path, remote=remote)
        mgr.prune_old_snapshots()
        # Should have called rm for each old snapshot
        assert remote.execute_command.call_count == 3

    def test_no_prune_when_ls_returns_empty(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok(stdout="")
        mgr = _make_manager(tmp_path, remote=remote)
        mgr.prune_old_snapshots()
        # Only one call (the ls), no rm calls
        assert remote.execute_command.call_count == 1
