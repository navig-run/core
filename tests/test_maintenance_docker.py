"""
Batch 125: navig/commands/maintenance.py + navig/commands/docker.py

Maintenance functions use module-level imports → patch navig.commands.maintenance.*
Docker functions use lazy in-function imports → patch source modules.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_result(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail_result(stderr: str = "cmd failed") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def _make_maint_mocks(server_name: str = "prod"):
    """Return (mock_cfg, mock_remote, mock_require) for maintenance patches."""
    mock_cfg = MagicMock()
    mock_cfg.load_server_config.return_value = {"host": server_name}
    mock_remote = MagicMock()
    mock_require = MagicMock(return_value=server_name)
    return mock_cfg, mock_remote, mock_require


def _maint_patches(mock_cfg, mock_remote, mock_require):
    return [
        patch("navig.commands.maintenance.get_config_manager", return_value=mock_cfg),
        patch("navig.commands.maintenance.RemoteOperations", return_value=mock_remote),
        patch("navig.commands.maintenance.require_active_server", mock_require),
    ]


# ---------------------------------------------------------------------------
# navig/commands/maintenance.py
# ---------------------------------------------------------------------------
import navig.commands.maintenance as _maint


class TestUpdatePackages:
    def test_dry_run_no_remote_calls(self):
        cfg, remote, req = _make_maint_mocks()
        with _maint_patches(cfg, remote, req)[0], _maint_patches(cfg, remote, req)[1], _maint_patches(cfg, remote, req)[2]:
            _maint.update_packages({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_dry_run_json_output(self, capsys):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.update_packages({"dry_run": True, "json": True})
        # No exception

    def test_success_path(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result(stdout="10")
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.update_packages({"dry_run": False, "json": False})

    def test_failure_path_returns_early(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.update_packages({"dry_run": False, "json": False})
        # Should return after first failure without raising
        assert remote.execute_command.call_count == 1

    def test_exception_handling(self):
        cfg, remote, req = _make_maint_mocks()
        req.side_effect = RuntimeError("no server")
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            # Exception should be caught internally
            _maint.update_packages({"dry_run": False, "json": False})


class TestCleanPackages:
    def test_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.clean_packages({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_success_path(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.clean_packages({"dry_run": False, "json": False})


class TestRotateLogs:
    def test_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.rotate_logs({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_json_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.rotate_logs({"dry_run": True, "json": True})

    def test_success_path(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.rotate_logs({"dry_run": False, "json": False})

    def test_failure_path(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.rotate_logs({"dry_run": False, "json": False})


class TestCleanupTemp:
    def test_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.cleanup_temp({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_success(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.cleanup_temp({"dry_run": False, "json": False})


class TestCheckFilesystem:
    def test_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.check_filesystem({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_success(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result(stdout="/dev/sda1  10G  2G  8G")
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.check_filesystem({"dry_run": False, "json": False})


class TestSystemMaintenance:
    def test_dry_run_all_tasks(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.system_maintenance({"dry_run": True, "json": False})
        remote.execute_command.assert_not_called()

    def test_json_dry_run(self):
        cfg, remote, req = _make_maint_mocks()
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.system_maintenance({"dry_run": True, "json": True})


class TestSystemInfo:
    def test_json_output(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.side_effect = [
            _ok_result(stdout="Ubuntu 22.04"),
            _ok_result(stdout="16"),
            _ok_result(stdout="31G"),
            _ok_result(stdout="200G"),
        ]
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.system_info({"json": True})

    def test_text_output(self):
        cfg, remote, req = _make_maint_mocks()
        remote.execute_command.return_value = _ok_result(stdout="info")
        with patch("navig.commands.maintenance.get_config_manager", return_value=cfg), \
             patch("navig.commands.maintenance.RemoteOperations", return_value=remote), \
             patch("navig.commands.maintenance.require_active_server", req):
            _maint.system_info({"json": False})


# ---------------------------------------------------------------------------
# navig/commands/docker.py
# ---------------------------------------------------------------------------
import navig.commands.docker as _docker


def _make_docker_mocks(host_name: str = "prod"):
    mock_cfg = MagicMock()
    mock_cfg.load_host_config.return_value = {"host": host_name}
    mock_remote = MagicMock()
    mock_remote.execute_command.return_value = _ok_result()
    mock_require = MagicMock(return_value=host_name)
    return mock_cfg, mock_remote, mock_require


def _docker_ctx(mock_cfg, mock_remote, mock_require):
    return [
        patch("navig.config.get_config_manager", return_value=mock_cfg),
        patch("navig.remote.RemoteOperations", return_value=mock_remote),
        patch("navig.cli.recovery.require_active_host", mock_require),
    ]


class TestDockerPs:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_ps({"quiet": True})
        remote.execute_command.assert_called_once()

    def test_all_containers(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_ps({"quiet": True}, all=True)
        cmd = remote.execute_command.call_args[0][0]
        assert "docker ps -a" in cmd

    def test_with_filter(self):
        cfg, remote, req = _make_docker_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_ps({"quiet": True}, filter="nginx")

    def test_json_format(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_ps({"quiet": True}, format="json")
        cmd = remote.execute_command.call_args[0][0]
        assert "json" in cmd

    def test_names_format(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_ps({"quiet": True}, format="names")
        cmd = remote.execute_command.call_args[0][0]
        assert "Names" in cmd


class TestDockerLogs:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_logs("myapp", {"quiet": True})
        cmd = remote.execute_command.call_args[0][0]
        assert "docker logs" in cmd
        assert "myapp" in cmd

    def test_with_tail(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_logs("nginx", {"quiet": True}, tail=100)
        cmd = remote.execute_command.call_args[0][0]
        assert "--tail 100" in cmd

    def test_failure_path(self):
        cfg, remote, req = _make_docker_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_logs("missing", {"quiet": True})


class TestDockerExec:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_exec("app", "ls -la", {"quiet": True})
        cmd = remote.execute_command.call_args[0][0]
        assert "docker exec" in cmd
        assert "app" in cmd

    def test_with_user(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_exec("app", "id", {"quiet": True}, user="www-data")
        cmd = remote.execute_command.call_args[0][0]
        assert "www-data" in cmd


class TestDockerRestart:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_restart("nginx", {"quiet": True})
        remote.execute_command.assert_called()

    def test_failure(self):
        cfg, remote, req = _make_docker_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_restart("ghost", {"quiet": True})


class TestDockerStop:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_stop("nginx", {"quiet": True})
        remote.execute_command.assert_called()


class TestDockerStart:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_start("nginx", {"quiet": True})
        remote.execute_command.assert_called()


class TestDockerStats:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_stats({"quiet": True})
        remote.execute_command.assert_called()

    def test_specific_container(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_stats({"quiet": True}, container="myapp")
        cmd = remote.execute_command.call_args[0][0]
        assert "myapp" in cmd


class TestDockerInspect:
    def test_basic(self):
        cfg, remote, req = _make_docker_mocks()
        remote.execute_command.return_value = _ok_result(stdout='[{"Id":"abc"}]')
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_inspect("nginx", {"quiet": True})
        cmd = remote.execute_command.call_args[0][0]
        assert "inspect" in cmd
        assert "nginx" in cmd

    def test_failure(self):
        cfg, remote, req = _make_docker_mocks()
        remote.execute_command.return_value = _fail_result()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_inspect("unknown", {"quiet": True})


class TestDockerCompose:
    def test_up(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_compose("up", {"quiet": True})
        remote.execute_command.assert_called()

    def test_down(self):
        cfg, remote, req = _make_docker_mocks()
        with patch("navig.config.get_config_manager", return_value=cfg), \
             patch("navig.remote.RemoteOperations", return_value=remote), \
             patch("navig.cli.recovery.require_active_host", req):
            _docker.docker_compose("down", {"quiet": True})
