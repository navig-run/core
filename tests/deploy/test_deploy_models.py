"""Tests for navig/deploy/models.py — batch 84."""
from __future__ import annotations

from datetime import datetime

import pytest

from navig.deploy.models import (
    ApplyConfig,
    BackupConfig,
    DeployConfig,
    DeployPhase,
    DeployResult,
    HealthConfig,
    PhaseResult,
    PushConfig,
    RestartConfig,
    SnapshotRecord,
)


# ---------------------------------------------------------------------------
# DeployPhase enum
# ---------------------------------------------------------------------------

class TestDeployPhase:
    def test_all_phases_present(self):
        names = {p.value for p in DeployPhase}
        assert names == {"pre_check", "backup", "push", "apply", "restart", "health", "cleanup"}

    def test_phase_is_string_enum(self):
        assert isinstance(DeployPhase.PUSH, str)
        assert DeployPhase.PUSH == "push"


# ---------------------------------------------------------------------------
# PhaseResult
# ---------------------------------------------------------------------------

class TestPhaseResult:
    def test_defaults(self):
        p = PhaseResult(phase=DeployPhase.PUSH, success=True)
        assert p.message == ""
        assert p.detail == ""
        assert p.elapsed == 0.0
        assert p.skipped is False

    def test_skipped_phase(self):
        p = PhaseResult(phase=DeployPhase.BACKUP, success=True, skipped=True)
        assert p.skipped is True

    def test_failure_phase(self):
        p = PhaseResult(phase=DeployPhase.HEALTH, success=False, message="timeout")
        assert p.success is False
        assert p.message == "timeout"


# ---------------------------------------------------------------------------
# Simple config dataclasses
# ---------------------------------------------------------------------------

class TestPushConfig:
    def test_field_assignment(self):
        cfg = PushConfig(source="./src/", target="/srv/app/")
        assert cfg.source == "./src/"
        assert cfg.target == "/srv/app/"
        assert cfg.excludes == []

    def test_with_excludes(self):
        cfg = PushConfig(source=".", target="/tmp/", excludes=["*.pyc", "__pycache__"])
        assert "*.pyc" in cfg.excludes


class TestApplyConfig:
    def test_default_empty_commands(self):
        cfg = ApplyConfig()
        assert cfg.commands == []

    def test_with_commands(self):
        cfg = ApplyConfig(commands=["pip install -r requirements.txt"])
        assert len(cfg.commands) == 1


class TestRestartConfig:
    def test_defaults(self):
        cfg = RestartConfig()
        assert cfg.adapter == "systemd"
        assert cfg.service is None
        assert cfg.compose_file == "docker-compose.yml"
        assert cfg.command is None

    def test_custom_adapter(self):
        cfg = RestartConfig(adapter="docker-compose", compose_file="prod.yml")
        assert cfg.adapter == "docker-compose"


class TestHealthConfig:
    def test_defaults(self):
        cfg = HealthConfig()
        assert cfg.method == "GET"
        assert cfg.expected_status == 200
        assert cfg.retries == 5
        assert cfg.interval_seconds == 5
        assert cfg.timeout_seconds == 30
        assert cfg.url is None
        assert cfg.command is None

    def test_custom_url(self):
        cfg = HealthConfig(url="http://localhost/health", expected_status=204)
        assert cfg.url == "http://localhost/health"
        assert cfg.expected_status == 204


class TestBackupConfig:
    def test_defaults(self):
        cfg = BackupConfig()
        assert cfg.enabled is True
        assert cfg.remote_path == "/var/backups"
        assert cfg.keep_last == 5

    def test_disabled(self):
        cfg = BackupConfig(enabled=False)
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# DeployConfig.from_dict
# ---------------------------------------------------------------------------

class TestDeployConfigFromDict:
    def test_empty_dict_uses_defaults(self):
        cfg = DeployConfig.from_dict({})
        assert cfg.version == "1"
        assert cfg.push.source == "./dist/"
        assert cfg.restart.adapter == "systemd"
        assert cfg.health.retries == 5
        assert cfg.backup.enabled is True

    def test_push_fields_parsed(self):
        cfg = DeployConfig.from_dict({
            "push": {"source": "build/", "target": "/var/www/", "excludes": ["*.log"]}
        })
        assert cfg.push.source == "build/"
        assert cfg.push.target == "/var/www/"
        assert "*.log" in cfg.push.excludes

    def test_apply_commands_parsed(self):
        cfg = DeployConfig.from_dict({
            "apply": {"commands": ["make install", "systemctl restart nginx"]}
        })
        assert "make install" in cfg.apply.commands

    def test_restart_adapter_parsed(self):
        cfg = DeployConfig.from_dict({
            "restart": {"adapter": "docker-compose", "compose_file": "prod.yml"}
        })
        assert cfg.restart.adapter == "docker-compose"
        assert cfg.restart.compose_file == "prod.yml"

    def test_health_check_key(self):
        cfg = DeployConfig.from_dict({
            "health_check": {"url": "http://localhost/ping", "retries": 3}
        })
        assert cfg.health.url == "http://localhost/ping"
        assert cfg.health.retries == 3

    def test_backup_parsed(self):
        cfg = DeployConfig.from_dict({"backup": {"enabled": False, "keep_last": 2}})
        assert cfg.backup.enabled is False
        assert cfg.backup.keep_last == 2

    def test_host_app_parsed(self):
        cfg = DeployConfig.from_dict({"host": "prod-srv", "app": "myapp"})
        assert cfg.host == "prod-srv"
        assert cfg.app == "myapp"

    def test_version_converted_to_string(self):
        cfg = DeployConfig.from_dict({"version": 2})
        assert cfg.version == "2"


# ---------------------------------------------------------------------------
# DeployConfig.merge_global_defaults
# ---------------------------------------------------------------------------

class TestDeployConfigMergeGlobalDefaults:
    def test_adds_excludes_without_duplicates(self):
        cfg = DeployConfig.from_dict({"push": {"source": ".", "target": "/tmp/", "excludes": ["*.pyc"]}})
        cfg.merge_global_defaults({"deploy": {"default_push_excludes": ["*.pyc", "*.log"]}})
        # "*.pyc" already present — should not be duplicated
        assert cfg.push.excludes.count("*.pyc") == 1
        assert "*.log" in cfg.push.excludes

    def test_overrides_health_defaults(self):
        cfg = DeployConfig.from_dict({})
        cfg.merge_global_defaults({"deploy": {
            "default_health_retries": 10,
            "default_health_interval_seconds": 2,
            "default_health_timeout_seconds": 60,
        }})
        assert cfg.health.retries == 10
        assert cfg.health.interval_seconds == 2
        assert cfg.health.timeout_seconds == 60

    def test_does_not_override_project_health_values(self):
        cfg = DeployConfig.from_dict({"health_check": {"retries": 7}})
        cfg.merge_global_defaults({"deploy": {"default_health_retries": 20}})
        assert cfg.health.retries == 7  # project value preserved


# ---------------------------------------------------------------------------
# SnapshotRecord
# ---------------------------------------------------------------------------

class TestSnapshotRecord:
    def test_fields(self):
        snap = SnapshotRecord(path="/var/backups/myapp-20241201", created_at="2024-12-01T10:00:00")
        assert snap.path == "/var/backups/myapp-20241201"
        assert "2024" in snap.created_at


# ---------------------------------------------------------------------------
# DeployResult
# ---------------------------------------------------------------------------

class TestDeployResult:
    def _make_result(self, **kwargs):
        defaults = dict(
            success=True,
            host="prod-srv",
            app="myapp",
            started_at=datetime(2024, 1, 1, 10, 0, 0),
            finished_at=datetime(2024, 1, 1, 10, 0, 5),
        )
        defaults.update(kwargs)
        return DeployResult(**defaults)

    def test_elapsed_with_finished(self):
        r = self._make_result(
            started_at=datetime(2024, 1, 1, 10, 0, 0),
            finished_at=datetime(2024, 1, 1, 10, 0, 10),
        )
        assert r.elapsed == 10.0

    def test_elapsed_without_finished(self):
        r = self._make_result(finished_at=None)
        assert r.elapsed == 0.0

    def test_phase_lookup(self):
        phase_result = PhaseResult(phase=DeployPhase.PUSH, success=True, message="done")
        r = self._make_result()
        r.phases.append(phase_result)
        found = r.phase(DeployPhase.PUSH)
        assert found is phase_result

    def test_phase_lookup_missing_returns_none(self):
        r = self._make_result()
        assert r.phase(DeployPhase.BACKUP) is None

    def test_to_dict_keys(self):
        r = self._make_result()
        d = r.to_dict()
        assert "success" in d
        assert "host" in d
        assert "app" in d
        assert "started_at" in d
        assert "phases" in d
        assert "snapshot" in d
        assert "elapsed_seconds" in d

    def test_to_dict_success_flag(self):
        r = self._make_result(success=False, error="push failed")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "push failed"

    def test_to_dict_snapshot_none(self):
        r = self._make_result()
        assert r.to_dict()["snapshot"] is None

    def test_to_dict_snapshot_present(self):
        r = self._make_result(snapshot=SnapshotRecord(path="/bak/app", created_at="2024-01-01T10:00:00"))
        d = r.to_dict()
        assert d["snapshot"]["path"] == "/bak/app"

    def test_to_dict_phases_serialized(self):
        r = self._make_result()
        r.phases.append(PhaseResult(phase=DeployPhase.PUSH, success=True, message="ok", elapsed=1.5))
        phases = r.to_dict()["phases"]
        assert len(phases) == 1
        assert phases[0]["phase"] == "push"
        assert phases[0]["success"] is True
        assert phases[0]["elapsed"] == 1.5

    def test_to_dict_dry_run_flag(self):
        r = self._make_result(dry_run=True)
        assert r.to_dict()["dry_run"] is True

    def test_to_dict_git_ref(self):
        r = self._make_result(git_ref="abc1234")
        assert r.to_dict()["git_ref"] == "abc1234"
