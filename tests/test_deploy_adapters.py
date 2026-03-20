"""Tests for navig.deploy.adapters — command generation per adapter type."""

import pytest
from unittest.mock import MagicMock

from navig.deploy.adapters import (
    SystemdAdapter,
    DockerComposeAdapter,
    Pm2Adapter,
    CommandAdapter,
    build_adapter,
)
from navig.deploy.models import RestartConfig

# ─── shared fixtures ─────────────────────────────────────────────────────────

SERVER = {"user": "deploy", "host": "10.0.0.10", "port": 22}


def _ok_result():
    r = MagicMock()
    r.returncode = 0
    r.stdout = ""
    r.stderr = ""
    return r


def _fail_result(stderr="fail"):
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


# ============================================================================
# Adapter command generation
# ============================================================================

class TestSystemdAdapter:
    def test_restart_commands(self):
        adapter = SystemdAdapter(
            service="myapp",
            server_config=SERVER,
            remote_ops=MagicMock(),
            dry_run=False,
        )
        cmds = adapter.restart_commands()
        assert cmds == ["systemctl restart myapp"]

    def test_restart_success(self):
        remote = MagicMock()
        remote.execute_command.return_value = _ok_result()
        adapter = SystemdAdapter(service="myapp", server_config=SERVER, remote_ops=remote)
        ok, _ = adapter.restart()
        assert ok is True
        remote.execute_command.assert_called_once_with("systemctl restart myapp", SERVER)

    def test_restart_failure_returns_stderr(self):
        remote = MagicMock()
        remote.execute_command.return_value = _fail_result("Unit not found")
        adapter = SystemdAdapter(service="myapp", server_config=SERVER, remote_ops=remote)
        ok, msg = adapter.restart()
        assert ok is False
        assert "Unit not found" in msg


class TestDockerComposeAdapter:
    def test_restart_commands(self):
        adapter = DockerComposeAdapter(
            app_root="/var/www/myapp",
            compose_file="docker-compose.prod.yml",
            server_config=SERVER,
            remote_ops=MagicMock(),
            dry_run=False,
        )
        cmds = adapter.restart_commands()
        assert len(cmds) == 1
        assert "docker compose" in cmds[0]
        assert "docker-compose.prod.yml" in cmds[0]
        assert "up -d --remove-orphans" in cmds[0]

    def test_default_compose_file(self):
        adapter = DockerComposeAdapter(
            app_root="/app",
            server_config=SERVER,
            remote_ops=MagicMock(),
        )
        cmds = adapter.restart_commands()
        assert "docker-compose.yml" in cmds[0]


class TestPm2Adapter:
    def test_restart_commands(self):
        adapter = Pm2Adapter(service="myapp", server_config=SERVER, remote_ops=MagicMock())
        cmds = adapter.restart_commands()
        assert cmds == ["pm2 restart myapp --update-env"]

    def test_restart_dry_run_no_remote_call(self):
        remote = MagicMock()
        adapter = Pm2Adapter(service="myapp", server_config=SERVER, remote_ops=remote, dry_run=True)
        ok, _ = adapter.restart()
        assert ok is True
        remote.execute_command.assert_not_called()


class TestCommandAdapter:
    def test_restart_commands(self):
        adapter = CommandAdapter(
            command="supervisorctl restart myapp",
            server_config=SERVER,
            remote_ops=MagicMock(),
        )
        cmds = adapter.restart_commands()
        assert cmds == ["supervisorctl restart myapp"]


# ============================================================================
# build_adapter factory
# ============================================================================

class TestBuildAdapter:
    def _remote(self):
        return MagicMock()

    def test_systemd(self):
        cfg = RestartConfig(adapter="systemd", service="nginx")
        a = build_adapter(cfg, SERVER, self._remote(), dry_run=False)
        assert isinstance(a, SystemdAdapter)

    def test_docker_compose(self):
        cfg = RestartConfig(adapter="docker-compose", compose_file="docker-compose.yml")
        srv = {**SERVER, "_deploy_target_root": "/var/www/myapp"}
        a = build_adapter(cfg, srv, self._remote(), dry_run=False)
        assert isinstance(a, DockerComposeAdapter)

    def test_pm2(self):
        cfg = RestartConfig(adapter="pm2", service="api")
        a = build_adapter(cfg, SERVER, self._remote(), dry_run=False)
        assert isinstance(a, Pm2Adapter)

    def test_command(self):
        cfg = RestartConfig(adapter="command", command="sudo shutdown -r now")
        a = build_adapter(cfg, SERVER, self._remote(), dry_run=False)
        assert isinstance(a, CommandAdapter)

    def test_unknown_adapter_raises_valueerror(self):
        cfg = RestartConfig(adapter="kubernetes")
        with pytest.raises(ValueError, match="Unknown restart adapter"):
            build_adapter(cfg, SERVER, self._remote(), dry_run=False)

    def test_systemd_missing_service_raises(self):
        cfg = RestartConfig(adapter="systemd", service=None)
        with pytest.raises(ValueError, match="requires restart.service"):
            build_adapter(cfg, SERVER, self._remote(), dry_run=False)

    def test_command_missing_command_raises(self):
        cfg = RestartConfig(adapter="command", command=None)
        with pytest.raises(ValueError, match="requires restart.command"):
            build_adapter(cfg, SERVER, self._remote(), dry_run=False)
