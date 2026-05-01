"""Unit tests for navig.deploy.engine — all I/O mocked, no integration mark."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.deploy.engine import DeployEngine
from navig.deploy.models import (
    ApplyConfig,
    BackupConfig,
    DeployConfig,
    DeployPhase,
    HealthConfig,
    PushConfig,
    RestartConfig,
    SnapshotRecord,
)

# ─── helpers ────────────────────────────────────────────────────────────────

SERVER = {
    "name": "prod",
    "user": "deploy",
    "host": "10.0.0.10",
    "port": 22,
}


def _ok_proc(stdout: str = "ok"):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail_proc(stderr: str = "err"):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


def _snap() -> SnapshotRecord:
    return SnapshotRecord(path="/var/backups/testapp-snap", created_at="2026-01-01T00:00:00")


def _make_engine(tmp_path: Path) -> DeployEngine:
    cfg = DeployConfig()
    cfg.push = PushConfig(source="./dist/", target="/var/www/app/")
    cfg.backup = BackupConfig(enabled=True)
    cfg.apply = ApplyConfig(commands=[])
    cfg.restart = RestartConfig(adapter="systemd", service="app")
    cfg.health = HealthConfig(url=None)
    cfg.app = "testapp"
    remote = MagicMock()
    return DeployEngine(
        config=cfg,
        server_config=SERVER,
        remote_ops=remote,
        cache_dir=tmp_path,
        project_root=Path("/tmp"),
    )


# ─── engine lifecycle ────────────────────────────────────────────────────────

class TestDeployEngineUnit:
    def test_dry_run_full_success(self, tmp_path):
        engine = _make_engine(tmp_path)
        fired = []
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.HealthChecker") as MockHC,
            patch("navig.deploy.engine.build_adapter") as mock_af,
            patch("navig.deploy.engine.subprocess.run", return_value=_ok_proc()),
            patch("navig.deploy.engine.DeployHistory"),
        ):
            MockRM.return_value.create_snapshot.return_value = _snap()
            MockRM.return_value.load_state.return_value = None
            MockHC.return_value.check.return_value = (True, "healthy")
            mock_af.return_value.restart.return_value = (True, "restarted")
            result = engine.run(
                dry_run=True,
                on_progress=lambda ph, st, m: fired.append((ph, st)),
            )
        assert result.success is True
        assert len(result.phases) == 7
        assert all(p.success for p in result.phases)
        assert (DeployPhase.CLEANUP, "ok") in fired

    def test_pre_check_failure_stops_deploy(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._remote.execute_command.return_value = _fail_proc("connection refused")
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.subprocess.run", return_value=_ok_proc()),
            patch("navig.deploy.engine.DeployHistory"),
        ):
            MockRM.return_value.create_snapshot.return_value = None
            result = engine.run(dry_run=False)
        assert result.success is False
        assert len(result.phases) == 1
        assert result.phases[0].phase == DeployPhase.PRE_CHECK
        assert result.phases[0].success is False

    def test_backup_skipped_when_flag_set(self, tmp_path):
        engine = _make_engine(tmp_path)
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.HealthChecker") as MockHC,
            patch("navig.deploy.engine.build_adapter") as mock_af,
            patch("navig.deploy.engine.subprocess.run", return_value=_ok_proc()),
            patch("navig.deploy.engine.DeployHistory"),
        ):
            MockRM.return_value.create_snapshot.return_value = None
            MockRM.return_value.load_state.return_value = None
            MockHC.return_value.check.return_value = (True, "ok")
            mock_af.return_value.restart.return_value = (True, "ok")
            result = engine.run(dry_run=True, skip_backup=True)
        backup = result.phase(DeployPhase.BACKUP)
        assert backup is not None
        assert backup.skipped is True
        assert backup.success is True

    def test_push_failure_triggers_rollback(self, tmp_path):
        engine = _make_engine(tmp_path)
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.HealthChecker") as MockHC,
            patch("navig.deploy.engine.build_adapter") as mock_af,
            patch("navig.deploy.engine.subprocess.run") as mock_sub,
            patch("navig.deploy.engine.DeployHistory"),
            patch.object(Path, "exists", return_value=True),
        ):
            engine._remote.execute_command.return_value = _ok_proc("navig-ping")
            mock_sub.side_effect = [_ok_proc("abc1234"), _fail_proc("rsync error")]
            MockRM.return_value.create_snapshot.return_value = _snap()
            MockRM.return_value.load_state.return_value = _snap()
            MockRM.return_value.restore_snapshot.return_value = (True, "restored")
            MockRM.return_value.prune_old_snapshots.return_value = None
            MockHC.return_value.check.return_value = (True, "ok")
            mock_af.return_value.restart.return_value = (True, "ok")
            result = engine.run(dry_run=False, auto_rollback=True)
        assert result.success is False
        assert result.rolled_back is True

    def test_push_failure_no_rollback_when_disabled(self, tmp_path):
        engine = _make_engine(tmp_path)
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.subprocess.run") as mock_sub,
            patch("navig.deploy.engine.DeployHistory"),
            patch.object(Path, "exists", return_value=True),
        ):
            engine._remote.execute_command.return_value = _ok_proc("navig-ping")
            mock_sub.side_effect = [_ok_proc("abc1234"), _fail_proc("rsync error")]
            MockRM.return_value.create_snapshot.return_value = None
            result = engine.run(dry_run=False, auto_rollback=False)
        assert result.success is False
        assert result.rolled_back is False

    def test_health_failure_triggers_rollback(self, tmp_path):
        engine = _make_engine(tmp_path)
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.HealthChecker") as MockHC,
            patch("navig.deploy.engine.build_adapter") as mock_af,
            patch("navig.deploy.engine.subprocess.run") as mock_sub,
            patch("navig.deploy.engine.DeployHistory"),
            patch.object(Path, "exists", return_value=True),
        ):
            engine._remote.execute_command.side_effect = [
                _ok_proc("navig-ping"),
                _ok_proc("50"),
            ]
            mock_sub.side_effect = [
                _ok_proc("abc1234"),
                _ok_proc("sent 200 bytes  received 30 bytes"),
            ]
            MockRM.return_value.create_snapshot.return_value = _snap()
            MockRM.return_value.load_state.return_value = _snap()
            MockRM.return_value.restore_snapshot.return_value = (True, "restored")
            MockRM.return_value.prune_old_snapshots.return_value = None
            MockHC.return_value.check.return_value = (False, "503 Service Unavailable")
            mock_af.return_value.restart.return_value = (True, "restarted")
            result = engine.run(dry_run=False, auto_rollback=True)
        assert result.phase(DeployPhase.HEALTH).success is False
        assert result.rolled_back is True

    def test_progress_callback_exception_is_swallowed(self, tmp_path):
        engine = _make_engine(tmp_path)
        with (
            patch("navig.deploy.engine.RollbackManager") as MockRM,
            patch("navig.deploy.engine.HealthChecker") as MockHC,
            patch("navig.deploy.engine.build_adapter") as mock_af,
            patch("navig.deploy.engine.subprocess.run", return_value=_ok_proc()),
            patch("navig.deploy.engine.DeployHistory"),
        ):
            MockRM.return_value.create_snapshot.return_value = None
            MockRM.return_value.load_state.return_value = None
            MockHC.return_value.check.return_value = (True, "ok")
            mock_af.return_value.restart.return_value = (True, "ok")

            def _bad_cb(ph, st, m):
                raise RuntimeError("callback error")

            result = engine.run(dry_run=True, on_progress=_bad_cb)
        assert result is not None


# ─── static helpers ──────────────────────────────────────────────────────────

class TestDeployEngineStaticHelpers:
    def test_parse_rsync_summary_found(self):
        out = (
            "sending incremental file list\n"
            "sent 1234 bytes  received 100 bytes  890.00 bytes/sec\n"
            "total size is 10240\n"
        )
        assert "sent" in DeployEngine._parse_rsync_summary(out)

    def test_parse_rsync_summary_empty(self):
        assert DeployEngine._parse_rsync_summary("") == "synced"

    def test_parse_rsync_summary_no_match(self):
        assert DeployEngine._parse_rsync_summary("no useful lines\n") == "synced"

    def test_build_rsync_cmd_basic(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._server = {"host": "h", "user": "u", "port": 22}
        cmd = engine._build_rsync_cmd("/src/", "/dst/", [])
        assert cmd[0] == "rsync"
        assert "u@h:/dst/" in cmd[-1]

    def test_build_rsync_cmd_adds_excludes(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._server = {"host": "h", "user": "u", "port": 22}
        cmd = engine._build_rsync_cmd("/src/", "/dst/", ["*.pyc", "__pycache__"])
        assert "--exclude" in cmd
        assert "*.pyc" in cmd

    def test_build_rsync_cmd_trailing_slash_added(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._server = {"host": "h", "user": "u", "port": 22}
        cmd = engine._build_rsync_cmd("/src", "/dst/", [])
        # source without trailing slash should get one
        source_arg = [a for a in cmd if "/src" in a]
        assert source_arg and source_arg[0].endswith("/")


# ─── DeployConfig parsing ────────────────────────────────────────────────────

class TestDeployConfigParsing:
    def test_from_dict_full(self):
        data = {
            "version": "2",
            "push": {"source": "./build", "target": "/srv/app", "excludes": ["*.log"]},
            "apply": {"commands": ["npm run migrate"]},
            "restart": {"adapter": "docker-compose"},
            "health_check": {"url": "http://localhost/health", "retries": 3},
            "backup": {"enabled": True, "keep_last": 10},
            "app": "myapp",
        }
        cfg = DeployConfig.from_dict(data)
        assert cfg.version == "2"
        assert cfg.push.excludes == ["*.log"]
        assert cfg.apply.commands == ["npm run migrate"]
        assert cfg.health.retries == 3
        assert cfg.backup.keep_last == 10

    def test_from_dict_defaults(self):
        cfg = DeployConfig.from_dict({})
        assert cfg.push.source == "./dist/"
        assert cfg.backup.enabled is True
        assert cfg.restart.adapter == "systemd"

    def test_from_dict_partial_push(self):
        cfg = DeployConfig.from_dict({"push": {"target": "/srv/myapp"}})
        assert cfg.push.target == "/srv/myapp"
        assert cfg.push.source == "./dist/"  # default preserved

    def test_merge_global_defaults_adds_excludes(self):
        cfg = DeployConfig.from_dict(
            {"push": {"source": ".", "target": "/srv", "excludes": [".git"]}}
        )
        cfg.merge_global_defaults(
            {"deploy": {"default_push_excludes": [".git", "*.log"]}}
        )
        assert "*.log" in cfg.push.excludes
        # existing value not duplicated
        assert cfg.push.excludes.count(".git") == 1

    def test_merge_global_defaults_health_overrides(self):
        cfg = DeployConfig.from_dict({})
        cfg.merge_global_defaults(
            {"deploy": {"default_health_retries": 10, "snapshot_keep_last": 3}}
        )
        assert cfg.health.retries == 10
        assert cfg.backup.keep_last == 3

    def test_merge_global_defaults_noop_when_empty(self):
        cfg = DeployConfig.from_dict({})
        original_retries = cfg.health.retries
        cfg.merge_global_defaults({})
        assert cfg.health.retries == original_retries
