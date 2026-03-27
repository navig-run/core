"""Tests for navig.deploy.engine and navig.deploy.rollback."""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, call, patch

import pytest

from navig.deploy.engine import DeployEngine
from navig.deploy.health import HealthChecker
from navig.deploy.history import DeployHistory
from navig.deploy.models import (
    ApplyConfig,
    BackupConfig,
    DeployConfig,
    DeployPhase,
    HealthConfig,
    PhaseResult,
    PushConfig,
    RestartConfig,
    SnapshotRecord,
)
from navig.deploy.rollback import RollbackManager

# ─── helpers ────────────────────────────────────────────────────────────────

SERVER = {
    "name": "prod",
    "user": "deploy",
    "host": "10.0.0.10",
    "port": 22,
    "ssh_key": "/home/user/.ssh/id_ed25519",
}


def _ok_proc(stdout="ok"):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail_proc(stderr="err"):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


def _make_config(
    *,
    health_url: str = "http://localhost/health",
    apply_cmds=None,
    backup_enabled: bool = True,
    adapter: str = "systemd",
    service: str = "myapp",
) -> DeployConfig:
    return DeployConfig(
        push=PushConfig(source="./dist/", target="/var/www/myapp/"),
        apply=ApplyConfig(commands=apply_cmds or []),
        restart=RestartConfig(adapter=adapter, service=service),
        health=HealthConfig(
            url=health_url, retries=1, interval_seconds=0, timeout_seconds=5
        ),
        backup=BackupConfig(
            enabled=backup_enabled, remote_path="/var/backups", keep_last=3
        ),
        host="prod",
        app="myapp",
    )


def _make_engine(config, remote_ops, cache_dir, project_root=None) -> DeployEngine:
    pr = project_root or Path(".")
    return DeployEngine(
        config=config,
        server_config=SERVER,
        remote_ops=remote_ops,
        cache_dir=cache_dir,
        project_root=pr,
    )


# ============================================================================
# DeployEngine — dry-run mode
# ============================================================================


class TestDeployEngineDryRun:
    def test_dry_run_returns_success(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok_proc()
        config = _make_config()

        # Create fake source directory
        src = tmp_path / "dist"
        src.mkdir()

        engine = _make_engine(config, remote, tmp_path, project_root=tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="sent 100 bytes  received 10 bytes", stderr=""
            )
            result = engine.run(dry_run=True)

        assert result.dry_run is True
        assert result.success is True

    def test_dry_run_does_not_call_remote_for_push(self, tmp_path):
        """In dry-run, rsync subprocess should NOT be called for file transfer."""
        remote = MagicMock()
        remote.execute_command.return_value = _ok_proc()
        config = _make_config()

        src = tmp_path / "dist"
        src.mkdir()

        engine = _make_engine(config, remote, tmp_path, project_root=tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = engine.run(dry_run=True)

        # In dry-run mode, rsync should never be called
        rsync_calls = [
            c
            for c in mock_run.call_args_list
            if c.args and isinstance(c.args[0], list) and "rsync" in c.args[0][0]
        ]
        assert (
            rsync_calls == []
        ), f"rsync was called during dry-run: {mock_run.call_args_list}"


# ============================================================================
# DeployEngine — phase sequencing
# ============================================================================


class TestDeployEnginePhases:
    def _run_engine(self, tmp_path, remote, *, skip_backup=False):
        config = _make_config()
        src = tmp_path / "dist"
        src.mkdir()
        engine = _make_engine(config, remote, tmp_path, project_root=tmp_path)

        phases_seen = []

        def on_progress(phase, status, msg):
            phases_seen.append((phase.value, status))

        with patch("subprocess.run") as mock_rsync:
            mock_rsync.return_value = MagicMock(
                returncode=0, stdout="sent 1 bytes  received 1 bytes", stderr=""
            )
            result = engine.run(skip_backup=skip_backup, on_progress=on_progress)

        return result, phases_seen

    def test_all_phases_run_on_success(self, tmp_path):
        remote = MagicMock()
        # Use a function so the mock never runs out; health check returns "200",
        # everything else returns ok.
        call_count = {"n": 0}

        def _smart_return(cmd, *args, **kwargs):
            call_count["n"] += 1
            # health checker fires curl which expects a status code
            if "curl" in str(cmd):
                return _ok_proc("200")
            return _ok_proc("ok")

        remote.execute_command.side_effect = _smart_return

        result, _ = self._run_engine(tmp_path, remote)
        phase_names = [p.phase.value for p in result.phases]
        assert DeployPhase.PRE_CHECK.value in phase_names
        assert DeployPhase.PUSH.value in phase_names

    def test_pre_check_failure_stops_deploy(self, tmp_path):
        remote = MagicMock()
        # SSH fails on first call
        remote.execute_command.return_value = _fail_proc("Connection refused")

        config = _make_config()
        src = tmp_path / "dist"
        src.mkdir()
        engine = _make_engine(config, remote, tmp_path, project_root=tmp_path)

        with patch("subprocess.run"):
            result = engine.run()

        assert result.success is False
        # No PUSH phase should appear
        phase_names = [p.phase.value for p in result.phases]
        assert DeployPhase.PUSH.value not in phase_names

    def test_skip_backup_marks_phase_skipped(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.side_effect = [
            _ok_proc("navig-ping"),  # pre_check SSH
            _ok_proc("50"),  # pre_check disk
            _ok_proc(),  # restart
            _ok_proc("200"),  # health
            _ok_proc(""),  # cleanup
        ]

        config = _make_config()
        src = tmp_path / "dist"
        src.mkdir()
        engine = _make_engine(config, remote, tmp_path, project_root=tmp_path)

        with patch("subprocess.run") as mock_rsync:
            mock_rsync.return_value = MagicMock(
                returncode=0, stdout="sent 1 bytes  received 1 bytes", stderr=""
            )
            result = engine.run(skip_backup=True)

        backup_phase = result.phase(DeployPhase.BACKUP)
        assert backup_phase is not None
        assert backup_phase.skipped is True


# ============================================================================
# HealthChecker
# ============================================================================


class TestHealthChecker:
    def _remote(self):
        remote = MagicMock()
        return remote

    def test_http_200_success(self):
        remote = self._remote()
        remote.execute_command.return_value = _ok_proc("200")

        cfg = HealthConfig(
            url="http://localhost/health",
            method="GET",
            expected_status=200,
            retries=1,
            interval_seconds=0,
            timeout_seconds=5,
        )
        checker = HealthChecker(cfg, SERVER, remote)
        ok, msg = checker.check()
        assert ok is True
        assert "200" in msg

    def test_http_wrong_status_fails(self):
        remote = self._remote()
        remote.execute_command.return_value = _ok_proc("503")

        cfg = HealthConfig(
            url="http://localhost/health",
            method="GET",
            expected_status=200,
            retries=1,
            interval_seconds=0,
            timeout_seconds=5,
        )
        checker = HealthChecker(cfg, SERVER, remote)
        ok, msg = checker.check()
        assert ok is False
        assert "503" in msg

    def test_retries_exhausted_fails(self):
        remote = self._remote()
        remote.execute_command.return_value = _ok_proc("502")

        cfg = HealthConfig(
            url="http://localhost/health",
            retries=2,
            interval_seconds=0,
            timeout_seconds=5,
        )
        checker = HealthChecker(cfg, SERVER, remote)
        ok, msg = checker.check()
        assert ok is False
        assert "2" in msg  # retries mentioned

    def test_command_check_exit_0_passes(self):
        remote = self._remote()
        remote.execute_command.return_value = _ok_proc()

        cfg = HealthConfig(
            command="php artisan health:check",
            retries=1,
            interval_seconds=0,
            timeout_seconds=5,
        )
        checker = HealthChecker(cfg, SERVER, remote)
        ok, _ = checker.check()
        assert ok is True

    def test_command_check_nonzero_fails(self):
        remote = self._remote()
        remote.execute_command.return_value = _fail_proc("service down")

        cfg = HealthConfig(
            command="curl -sf http://localhost/health", retries=1, interval_seconds=0
        )
        checker = HealthChecker(cfg, SERVER, remote)
        ok, _ = checker.check()
        assert ok is False

    def test_dry_run_always_passes(self):
        remote = self._remote()
        cfg = HealthConfig(url="http://localhost/health", retries=5, interval_seconds=0)
        checker = HealthChecker(cfg, SERVER, remote, dry_run=True)
        ok, msg = checker.check()
        assert ok is True
        assert "DRY RUN" in msg
        remote.execute_command.assert_not_called()

    def test_no_url_no_command_skips(self):
        remote = self._remote()
        cfg = HealthConfig()
        checker = HealthChecker(cfg, SERVER, remote)
        ok, msg = checker.check()
        assert ok is True
        assert "skipped" in msg.lower()


# ============================================================================
# RollbackManager
# ============================================================================


class TestRollbackManager:
    def _mgr(self, tmp_path, remote, *, backup_enabled=True, dry_run=False):
        cfg = BackupConfig(
            enabled=backup_enabled, remote_path="/var/backups", keep_last=3
        )
        return RollbackManager(
            backup_cfg=cfg,
            deploy_target="/var/www/myapp",
            app_name="myapp",
            server_config=SERVER,
            remote_ops=remote,
            cache_dir=tmp_path,
            dry_run=dry_run,
        )

    def test_create_snapshot_calls_mkdir_and_cp(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok_proc()

        mgr = self._mgr(tmp_path, remote)
        rec = mgr.create_snapshot()

        assert rec is not None
        assert "/var/backups/myapp/" in rec.path
        calls = [call[0][0] for call in remote.execute_command.call_args_list]
        assert any("mkdir" in c for c in calls)
        assert any("cp -r" in c for c in calls)

    def test_create_snapshot_returns_none_when_disabled(self, tmp_path):
        remote = MagicMock()
        mgr = self._mgr(tmp_path, remote, backup_enabled=False)
        rec = mgr.create_snapshot()
        assert rec is None
        remote.execute_command.assert_not_called()

    def test_save_and_load_state(self, tmp_path):
        remote = MagicMock()
        mgr = self._mgr(tmp_path, remote)
        rec = SnapshotRecord(
            path="/var/backups/myapp/20260317_142233", created_at="20260317_142233"
        )
        mgr.save_state(rec)
        loaded = mgr.load_state()
        assert loaded is not None
        assert loaded.path == rec.path
        assert loaded.created_at == rec.created_at

    def test_load_state_returns_none_no_file(self, tmp_path):
        remote = MagicMock()
        mgr = self._mgr(tmp_path, remote)
        assert mgr.load_state() is None

    def test_restore_snapshot(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _ok_proc()

        mgr = self._mgr(tmp_path, remote)
        rec = SnapshotRecord(
            path="/var/backups/myapp/20260317_142233", created_at="20260317_142233"
        )
        ok, msg = mgr.restore_snapshot(rec)

        assert ok is True
        call_args = remote.execute_command.call_args[0][0]
        assert "rm -rf /var/www/myapp" in call_args
        assert "mv /var/backups/myapp/20260317_142233 /var/www/myapp" in call_args

    def test_restore_snapshot_no_snapshot_returns_false(self, tmp_path):
        remote = MagicMock()
        mgr = self._mgr(tmp_path, remote)
        ok, msg = mgr.restore_snapshot(None)
        assert ok is False

    def test_dry_run_snapshot_no_remote_call(self, tmp_path):
        remote = MagicMock()
        mgr = self._mgr(tmp_path, remote, dry_run=True)
        rec = mgr.create_snapshot()
        assert rec is not None
        remote.execute_command.assert_not_called()


# ============================================================================
# DeployHistory
# ============================================================================


class TestDeployHistory:
    def test_append_and_read(self, tmp_path):
        h = DeployHistory(cache_dir=tmp_path, keep=10)
        h.append(
            {"app": "myapp", "host": "prod", "success": True, "elapsed_seconds": 5.0}
        )
        entries = h.read()
        assert len(entries) == 1
        assert entries[0]["app"] == "myapp"

    def test_read_empty_returns_empty_list(self, tmp_path):
        h = DeployHistory(cache_dir=tmp_path)
        assert h.read() == []

    def test_filter_by_app(self, tmp_path):
        h = DeployHistory(cache_dir=tmp_path, keep=20)
        h.append({"app": "myapp", "host": "prod", "success": True})
        h.append({"app": "otherapp", "host": "prod", "success": True})
        entries = h.read(app="myapp")
        assert all(e["app"] == "myapp" for e in entries)
        assert len(entries) == 1

    def test_trim_to_keep_last(self, tmp_path):
        h = DeployHistory(cache_dir=tmp_path, keep=3)
        for i in range(10):
            h.append({"app": "myapp", "success": True, "i": i})
        entries = h.read(limit=100)
        assert len(entries) <= 3

    def test_newest_first(self, tmp_path):
        h = DeployHistory(cache_dir=tmp_path, keep=20)
        h.append({"app": "myapp", "success": True, "seq": 1})
        h.append({"app": "myapp", "success": True, "seq": 2})
        entries = h.read(limit=2)
        assert entries[0]["seq"] == 2  # newest first
        assert entries[1]["seq"] == 1
