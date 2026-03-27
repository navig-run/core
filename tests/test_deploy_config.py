"""Tests for navig.deploy.models — DeployConfig parsing and defaults merging."""

from datetime import datetime, timezone

from navig.deploy.models import (
    DeployConfig,
    DeployPhase,
    DeployResult,
    PhaseResult,
    SnapshotRecord,
)

# ============================================================================
# DeployConfig.from_dict
# ============================================================================


class TestDeployConfigFromDict:
    def test_minimal_config_push_required_fields(self):
        raw = {
            "push": {"source": "./out/", "target": "/var/www/myapp/"},
            "restart": {"adapter": "systemd", "service": "myapp"},
        }
        cfg = DeployConfig.from_dict(raw)
        assert cfg.push.source == "./out/"
        assert cfg.push.target == "/var/www/myapp/"
        assert cfg.restart.adapter == "systemd"
        assert cfg.restart.service == "myapp"

    def test_defaults_when_keys_absent(self):
        cfg = DeployConfig.from_dict({})
        assert cfg.push.source == "./dist/"
        assert cfg.push.target == "/var/www/app/"
        assert cfg.restart.adapter == "systemd"
        assert cfg.health.retries == 5
        assert cfg.health.interval_seconds == 5
        assert cfg.health.timeout_seconds == 30
        assert cfg.backup.enabled is True
        assert cfg.backup.keep_last == 5

    def test_apply_commands_parsed(self):
        raw = {"apply": {"commands": ["npm ci", "npm run build"]}}
        cfg = DeployConfig.from_dict(raw)
        assert cfg.apply.commands == ["npm ci", "npm run build"]

    def test_health_check_section_parsed(self):
        raw = {
            "health_check": {
                "url": "http://localhost/healthz",
                "method": "GET",
                "expected_status": 200,
                "retries": 3,
                "interval_seconds": 10,
                "timeout_seconds": 60,
            }
        }
        cfg = DeployConfig.from_dict(raw)
        assert cfg.health.url == "http://localhost/healthz"
        assert cfg.health.retries == 3
        assert cfg.health.timeout_seconds == 60

    def test_health_command_field(self):
        raw = {"health_check": {"command": "php artisan health:check"}}
        cfg = DeployConfig.from_dict(raw)
        assert cfg.health.command == "php artisan health:check"
        assert cfg.health.url is None

    def test_docker_compose_adapter(self):
        raw = {
            "restart": {
                "adapter": "docker-compose",
                "compose_file": "docker-compose.prod.yml",
            }
        }
        cfg = DeployConfig.from_dict(raw)
        assert cfg.restart.adapter == "docker-compose"
        assert cfg.restart.compose_file == "docker-compose.prod.yml"

    def test_command_adapter(self):
        raw = {"restart": {"adapter": "command", "command": "supervisorctl restart myapp"}}
        cfg = DeployConfig.from_dict(raw)
        assert cfg.restart.adapter == "command"
        assert cfg.restart.command == "supervisorctl restart myapp"

    def test_host_app_passthrough(self):
        raw = {"host": "production", "app": "myapi"}
        cfg = DeployConfig.from_dict(raw)
        assert cfg.host == "production"
        assert cfg.app == "myapi"

    def test_push_excludes_list(self):
        raw = {"push": {"source": "./", "target": "/app/", "excludes": [".env", "tmp/"]}}
        cfg = DeployConfig.from_dict(raw)
        assert ".env" in cfg.push.excludes
        assert "tmp/" in cfg.push.excludes

    def test_version_string_coerced(self):
        cfg = DeployConfig.from_dict({"version": 2})
        assert cfg.version == "2"


class TestDeployConfigMergeGlobalDefaults:
    def _base_cfg(self) -> DeployConfig:
        return DeployConfig.from_dict(
            {
                "push": {"source": "./dist/", "target": "/app/"},
            }
        )

    def test_push_excludes_merged_without_duplicates(self):
        cfg = self._base_cfg()
        cfg.push.excludes = [".env"]
        cfg.merge_global_defaults(
            {"deploy": {"default_push_excludes": [".env", "node_modules/", ".git/"]}}
        )
        # ".env" should appear only once
        assert cfg.push.excludes.count(".env") == 1
        assert "node_modules/" in cfg.push.excludes
        assert ".git/" in cfg.push.excludes

    def test_health_defaults_applied(self):
        cfg = self._base_cfg()
        cfg.merge_global_defaults(
            {
                "deploy": {
                    "default_health_retries": 8,
                    "default_health_interval_seconds": 10,
                    "default_health_timeout_seconds": 60,
                }
            }
        )
        assert cfg.health.retries == 8
        assert cfg.health.interval_seconds == 10
        assert cfg.health.timeout_seconds == 60

    def test_empty_global_defaults_safe(self):
        cfg = self._base_cfg()
        # Should not raise
        cfg.merge_global_defaults({})
        cfg.merge_global_defaults({"deploy": {}})


# ============================================================================
# DeployResult
# ============================================================================


class TestDeployResult:
    def _make_result(self, *, success=True) -> DeployResult:
        started = datetime.now(tz=timezone.utc)
        result = DeployResult(
            success=success,
            host="prod",
            app="myapp",
            started_at=started,
            finished_at=started,
        )
        result.phases = [
            PhaseResult(phase=DeployPhase.PRE_CHECK, success=True, message="ok", elapsed=0.5),
            PhaseResult(phase=DeployPhase.PUSH, success=True, message="synced", elapsed=3.2),
        ]
        return result

    def test_to_dict_structure(self):
        r = self._make_result(success=True)
        d = r.to_dict()
        assert d["success"] is True
        assert d["host"] == "prod"
        assert d["app"] == "myapp"
        assert isinstance(d["phases"], list)
        assert len(d["phases"]) == 2

    def test_phase_lookup(self):
        r = self._make_result()
        pr = r.phase(DeployPhase.PUSH)
        assert pr is not None
        assert pr.message == "synced"

    def test_phase_lookup_missing_returns_none(self):
        r = self._make_result()
        assert r.phase(DeployPhase.HEALTH) is None

    def test_snapshot_serialized(self):
        r = self._make_result()
        r.snapshot = SnapshotRecord(
            path="/var/backups/myapp/20260317_142233", created_at="20260317_142233"
        )
        d = r.to_dict()
        assert d["snapshot"]["path"] == "/var/backups/myapp/20260317_142233"

    def test_rolled_back_false_by_default(self):
        r = self._make_result()
        assert r.rolled_back is False
        assert r.to_dict()["rolled_back"] is False
