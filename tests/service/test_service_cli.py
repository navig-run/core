from __future__ import annotations

import json

from typer.testing import CliRunner

from navig.commands.service import service_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


def _patch_daemon_start_failure(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return None

        @staticmethod
        def stop_running_daemon():
            return True

    class DummyPopen:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr("navig.daemon.service_manager._pythonw_exe", lambda: "python")
    monkeypatch.setattr("subprocess.Popen", DummyPopen)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)


def test_service_restart_failure_returns_exit_code_1(monkeypatch):
    _patch_daemon_start_failure(monkeypatch)

    result = runner.invoke(service_app, ["restart"])

    assert result.exit_code == 1


def test_service_start_failure_returns_exit_code_1(monkeypatch):
    _patch_daemon_start_failure(monkeypatch)

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 1


def test_service_restart_stop_failure_returns_exit_code_1(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 12345

        @staticmethod
        def stop_running_daemon():
            return False

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

    result = runner.invoke(service_app, ["restart"])

    assert result.exit_code == 1


def test_service_uninstall_uses_auto_backend_when_method_omitted(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return None

        @staticmethod
        def stop_running_daemon():
            return True

    captured: dict[str, object] = {}

    def _fake_uninstall(method=None):
        captured["method"] = method
        return True, "ok"

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr("navig.daemon.service_manager.uninstall", _fake_uninstall)
    monkeypatch.setattr(
        "navig.daemon.service_manager.detect_best_method",
        lambda: (_ for _ in ()).throw(AssertionError("detect_best_method should not be called")),
    )

    result = runner.invoke(service_app, ["uninstall"])

    assert result.exit_code == 0
    assert captured["method"] is None


def test_service_uninstall_stop_failure_returns_exit_code_1(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 999

        @staticmethod
        def stop_running_daemon():
            return False

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

    result = runner.invoke(service_app, ["uninstall"])

    assert result.exit_code == 1


def test_service_status_json_includes_service_manager_detail(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return 321

        @staticmethod
        def stop_running_daemon():
            return True

        @staticmethod
        def read_state():
            return {"children": [{"name": "gateway", "alive": True}]}

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr(
        "navig.daemon.service_manager.status",
        lambda method=None: (True, "Daemon process: RUNNING"),
    )

    result = runner.invoke(service_app, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["running"] is True
    assert payload["pid"] == 321
    assert payload["children"] == [{"name": "gateway", "alive": True}]
    assert payload["detail"] == "Daemon process: RUNNING"


def test_service_config_show_malformed_json_returns_exit_code_1(monkeypatch, tmp_path):
    bad = tmp_path / "bad-config.json"
    bad.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr("navig.daemon.entry.save_default_config", lambda: bad)

    result = runner.invoke(service_app, ["config", "--show"])

    assert result.exit_code == 1
    assert "Failed to read daemon config" in result.stdout


def test_service_logs_rejects_zero_lines():
    result = runner.invoke(service_app, ["logs", "--lines", "0"])
    assert result.exit_code != 0


def test_service_install_handles_malformed_existing_config(monkeypatch, tmp_path):
    bad_config = tmp_path / "daemon-config.json"
    bad_config.write_text("{bad-json", encoding="utf-8")

    monkeypatch.setattr("navig.daemon.entry.save_default_config", lambda: bad_config)
    monkeypatch.setattr("navig.daemon.service_manager.detect_best_method", lambda: "task")

    called: dict[str, object] = {}

    def _fake_install(method=None, start_now=True):
        called["method"] = method
        called["start_now"] = start_now
        return True, "ok"

    monkeypatch.setattr("navig.daemon.service_manager.install", _fake_install)

    result = runner.invoke(
        service_app,
        ["install", "--no-start", "--gateway", "--scheduler", "--health-port", "123"],
    )

    assert result.exit_code == 0
    assert called["method"] == "task"
    assert called["start_now"] is False

    cfg = json.loads(bad_config.read_text(encoding="utf-8"))
    assert cfg["telegram_bot"] is True
    assert cfg["gateway"] is True
    assert cfg["scheduler"] is True
    assert cfg["health_port"] == 123
