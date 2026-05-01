"""Tests for navig/deploy/history.py and navig/deploy/adapters.py — batch 83."""
from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# DeployHistory
# ---------------------------------------------------------------------------
from navig.deploy.history import DeployHistory


class TestDeployHistoryAppend:
    def test_append_creates_file(self, tmp_path):
        h = DeployHistory(tmp_path)
        h.append({"app": "myapp", "host": "srv1", "status": "ok"})
        log = tmp_path / "deploy_history.jsonl"
        assert log.exists()

    def test_append_writes_json_line(self, tmp_path):
        h = DeployHistory(tmp_path)
        h.append({"app": "myapp", "host": "srv1", "status": "ok"})
        lines = (tmp_path / "deploy_history.jsonl").read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["app"] == "myapp"

    def test_append_multiple(self, tmp_path):
        h = DeployHistory(tmp_path)
        h.append({"app": "a", "n": 1})
        h.append({"app": "b", "n": 2})
        h.append({"app": "c", "n": 3})
        lines = (tmp_path / "deploy_history.jsonl").read_text().splitlines()
        assert len(lines) == 3


class TestDeployHistoryRead:
    def _seed(self, tmp_path, records):
        log = tmp_path / "deploy_history.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_read_empty_when_no_file(self, tmp_path):
        h = DeployHistory(tmp_path)
        assert h.read() == []

    def test_read_returns_newest_first(self, tmp_path):
        self._seed(tmp_path, [{"n": 1}, {"n": 2}, {"n": 3}])
        h = DeployHistory(tmp_path)
        result = h.read()
        assert [r["n"] for r in result] == [3, 2, 1]

    def test_read_respects_limit(self, tmp_path):
        self._seed(tmp_path, [{"n": i} for i in range(10)])
        h = DeployHistory(tmp_path)
        result = h.read(limit=3)
        assert len(result) == 3

    def test_read_filter_by_app(self, tmp_path):
        self._seed(tmp_path, [
            {"app": "a", "v": 1},
            {"app": "b", "v": 2},
            {"app": "a", "v": 3},
        ])
        h = DeployHistory(tmp_path)
        result = h.read(app="a")
        assert all(r["app"] == "a" for r in result)
        assert len(result) == 2

    def test_read_filter_by_host(self, tmp_path):
        self._seed(tmp_path, [
            {"host": "h1", "v": 1},
            {"host": "h2", "v": 2},
            {"host": "h1", "v": 3},
        ])
        h = DeployHistory(tmp_path)
        result = h.read(host="h1")
        assert all(r["host"] == "h1" for r in result)

    def test_read_skips_malformed_json(self, tmp_path):
        log = tmp_path / "deploy_history.jsonl"
        log.write_text('{"app": "ok"}\nnot valid json\n{"app": "also_ok"}\n')
        h = DeployHistory(tmp_path)
        result = h.read()
        assert len(result) == 2
        assert all("app" in r for r in result)

    def test_read_filter_app_and_host(self, tmp_path):
        self._seed(tmp_path, [
            {"app": "x", "host": "h1", "v": 1},
            {"app": "x", "host": "h2", "v": 2},
            {"app": "y", "host": "h1", "v": 3},
        ])
        h = DeployHistory(tmp_path)
        result = h.read(app="x", host="h1")
        assert len(result) == 1
        assert result[0]["v"] == 1


class TestDeployHistoryTrim:
    def test_trim_keeps_last_n(self, tmp_path):
        h = DeployHistory(tmp_path, keep=3)
        for i in range(10):
            h.append({"n": i})
        result = h.read(limit=100)
        assert len(result) == 3

    def test_trim_preserves_newest(self, tmp_path):
        h = DeployHistory(tmp_path, keep=3)
        for i in range(5):
            h.append({"n": i})
        result = h.read(limit=100)
        ns = {r["n"] for r in result}
        assert ns == {2, 3, 4}

    def test_trim_noop_when_under_limit(self, tmp_path):
        h = DeployHistory(tmp_path, keep=10)
        for i in range(5):
            h.append({"n": i})
        result = h.read(limit=100)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# ServiceAdapter & subclasses
# ---------------------------------------------------------------------------
from navig.deploy.adapters import (
    CommandAdapter,
    DockerComposeAdapter,
    Pm2Adapter,
    ServiceAdapter,
    SystemdAdapter,
    build_adapter,
)


def _make_result(returncode=0, stdout="ok", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _mock_remote(success=True, output="ok"):
    remote = MagicMock()
    rc = 0 if success else 1
    remote.execute_command = MagicMock(return_value=_make_result(rc, output))
    return remote


def _server_cfg(**extra):
    return {"host": "testhost", **extra}


class TestServiceAdapterBase:
    def test_restart_returns_true_on_success(self):
        class ConcreteAdapter(ServiceAdapter):
            def restart_commands(self):
                return ["echo hello"]

        remote = _mock_remote(success=True, output="hello")
        adapter = ConcreteAdapter(server_config=_server_cfg(), remote_ops=remote)
        ok, msg = adapter.restart()
        assert ok is True

    def test_restart_returns_false_on_failure(self):
        class ConcreteAdapter(ServiceAdapter):
            def restart_commands(self):
                return ["false"]

        remote = _mock_remote(success=False, output="error")
        adapter = ConcreteAdapter(server_config=_server_cfg(), remote_ops=remote)
        ok, msg = adapter.restart()
        assert ok is False

    def test_dry_run_does_not_call_remote(self):
        class ConcreteAdapter(ServiceAdapter):
            def restart_commands(self):
                return ["rm -rf /"]

        remote = _mock_remote()
        adapter = ConcreteAdapter(server_config=_server_cfg(), remote_ops=remote, dry_run=True)
        ok, msg = adapter.restart()
        remote.execute_command.assert_not_called()

    def test_dry_run_returns_true(self):
        class ConcreteAdapter(ServiceAdapter):
            def restart_commands(self):
                return ["echo hi"]

        remote = _mock_remote()
        adapter = ConcreteAdapter(server_config=_server_cfg(), remote_ops=remote, dry_run=True)
        ok, _ = adapter.restart()
        assert ok is True

    def test_no_commands_returns_false(self):
        class ConcreteAdapter(ServiceAdapter):
            def restart_commands(self):
                return []

        remote = _mock_remote()
        adapter = ConcreteAdapter(server_config=_server_cfg(), remote_ops=remote)
        ok, msg = adapter.restart()
        assert ok is False
        assert msg  # some explanation provided


class TestSystemdAdapter:
    def test_restart_commands_contains_systemctl(self):
        remote = _mock_remote()
        adapter = SystemdAdapter("nginx", server_config=_server_cfg(), remote_ops=remote)
        cmds = adapter.restart_commands()
        assert len(cmds) == 1
        assert "systemctl" in cmds[0]
        assert "nginx" in cmds[0]

    def test_restart_calls_remote(self):
        remote = _mock_remote(success=True)
        adapter = SystemdAdapter("nginx", server_config=_server_cfg(), remote_ops=remote)
        ok, _ = adapter.restart()
        remote.execute_command.assert_called_once()
        assert ok is True


class TestDockerComposeAdapter:
    def test_restart_commands_contains_docker_compose(self):
        remote = _mock_remote()
        adapter = DockerComposeAdapter(
            app_root="/app", server_config=_server_cfg(), remote_ops=remote
        )
        cmds = adapter.restart_commands()
        assert any("docker" in c for c in cmds)

    def test_restart_commands_use_custom_compose_file(self):
        remote = _mock_remote()
        adapter = DockerComposeAdapter(
            app_root="/app",
            compose_file="prod.yml",
            server_config=_server_cfg(),
            remote_ops=remote,
        )
        cmds = adapter.restart_commands()
        assert any("prod.yml" in c for c in cmds)


class TestPm2Adapter:
    def test_restart_commands_contains_pm2(self):
        remote = _mock_remote()
        adapter = Pm2Adapter("myapp", server_config=_server_cfg(), remote_ops=remote)
        cmds = adapter.restart_commands()
        assert len(cmds) == 1
        assert "pm2" in cmds[0]
        assert "myapp" in cmds[0]


class TestCommandAdapter:
    def test_restart_commands_returns_custom_command(self):
        remote = _mock_remote()
        adapter = CommandAdapter("sudo service foo restart", server_config=_server_cfg(), remote_ops=remote)
        cmds = adapter.restart_commands()
        assert cmds == ["sudo service foo restart"]

    def test_restart_executes_custom_command(self):
        remote = _mock_remote(success=True)
        adapter = CommandAdapter("echo done", server_config=_server_cfg(), remote_ops=remote)
        ok, _ = adapter.restart()
        assert ok is True
        remote.execute_command.assert_called_once()


class TestBuildAdapter:
    def _restart_cfg(self, adapter, **kwargs):
        cfg = MagicMock()
        cfg.adapter = adapter
        cfg.service = kwargs.get("service", "")
        cfg.compose_file = kwargs.get("compose_file", "docker-compose.yml")
        cfg.command = kwargs.get("command", "")
        return cfg

    def test_build_systemd(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("systemd", service="nginx")
        adapter = build_adapter(cfg, _server_cfg(), remote, dry_run=False)
        assert isinstance(adapter, SystemdAdapter)

    def test_build_docker_compose(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("docker-compose")
        adapter = build_adapter(
            cfg,
            _server_cfg(**{"_deploy_target_root": "/app"}),
            remote,
            dry_run=False,
        )
        assert isinstance(adapter, DockerComposeAdapter)

    def test_build_pm2(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("pm2", service="myapp")
        adapter = build_adapter(cfg, _server_cfg(), remote, dry_run=False)
        assert isinstance(adapter, Pm2Adapter)

    def test_build_command(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("command", command="echo hi")
        adapter = build_adapter(cfg, _server_cfg(), remote, dry_run=False)
        assert isinstance(adapter, CommandAdapter)

    def test_build_unknown_raises(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("nonexistent")
        with pytest.raises(ValueError, match="Unknown restart adapter"):
            build_adapter(cfg, _server_cfg(), remote, dry_run=False)

    def test_build_systemd_no_service_raises(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("systemd", service="")
        with pytest.raises(ValueError, match="requires restart.service"):
            build_adapter(cfg, _server_cfg(), remote, dry_run=False)

    def test_build_command_no_command_raises(self):
        remote = _mock_remote()
        cfg = self._restart_cfg("command", command="")
        with pytest.raises(ValueError, match="requires restart.command"):
            build_adapter(cfg, _server_cfg(), remote, dry_run=False)
