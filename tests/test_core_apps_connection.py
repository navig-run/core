"""
Batch 92 — tests for:
  navig/core/apps.py        (AppManager: exists, list, load, save, delete, file ops)
  navig/core/connection.py  (CommandResult, LocalConnection, SSHConnection._build_ssh_args)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_app_provider(tmp_path: Path, *, app_config_dir=None, verbose=False):
    provider = MagicMock()
    provider.get_config_directories.return_value = [tmp_path]
    provider._is_directory_accessible.return_value = True
    provider.verbose = verbose
    provider.app_config_dir = app_config_dir
    provider.base_dir = tmp_path
    provider.list_hosts.return_value = []
    provider.load_host_config.side_effect = FileNotFoundError("no host")
    return provider


def _write_app_yaml(directory: Path, app_name: str, host: str, extra: dict | None = None):
    apps_dir = directory / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    data = {"name": app_name, "host": host, "webserver": {"type": "nginx"}}
    if extra:
        data.update(extra)
    (apps_dir / f"{app_name}.yaml").write_text(yaml.dump(data), encoding="utf-8")
    return data


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  apps.py — AppManager                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestAppManagerExists:
    def test_returns_true_for_existing_app_file(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "myapp", "prod")
        am = AppManager(_make_app_provider(tmp_path))
        assert am.exists("prod", "myapp") is True

    def test_returns_false_wrong_host(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "myapp", "prod")
        am = AppManager(_make_app_provider(tmp_path))
        assert am.exists("staging", "myapp") is False

    def test_returns_false_missing_app(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        assert am.exists("prod", "ghost") is False

    def test_legacy_embedded_format(self, tmp_path):
        from navig.core.apps import AppManager

        provider = _make_app_provider(tmp_path)
        provider.load_host_config.side_effect = None
        provider.load_host_config.return_value = {"apps": {"legapp": {"webserver": {"type": "nginx"}}}}

        am = AppManager(provider)
        assert am.exists("prod", "legapp") is True


class TestAppManagerListApps:
    def test_empty_returns_empty(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        assert am.list_apps("prod") == []

    def test_lists_apps_for_correct_host(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "api", "prod")
        _write_app_yaml(tmp_path, "web", "prod")
        _write_app_yaml(tmp_path, "other", "staging")

        am = AppManager(_make_app_provider(tmp_path))
        result = am.list_apps("prod")
        assert sorted(result) == ["api", "web"]

    def test_includes_legacy_embedded_apps(self, tmp_path):
        from navig.core.apps import AppManager

        provider = _make_app_provider(tmp_path)
        provider.load_host_config.side_effect = None
        provider.load_host_config.return_value = {"apps": {"legacyapp": {}}}

        am = AppManager(provider)
        result = am.list_apps("prod")
        assert "legacyapp" in result


class TestAppManagerLoadFromFile:
    def test_loads_valid_app_file(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "shop", "prod")
        am = AppManager(_make_app_provider(tmp_path))
        result = am.load_from_file("shop", tmp_path)
        assert result is not None
        assert result["name"] == "shop"
        assert result["host"] == "prod"

    def test_returns_none_when_file_missing(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        result = am.load_from_file("nonexistent", tmp_path)
        assert result is None

    def test_raises_on_missing_name_field(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "bad.yaml").write_text(yaml.dump({"host": "prod"}))

        am = AppManager(_make_app_provider(tmp_path))
        with pytest.raises(ValueError, match="missing required field 'name'"):
            am.load_from_file("bad", tmp_path)

    def test_raises_on_name_mismatch(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "myapp.yaml").write_text(yaml.dump({"name": "other_name", "host": "prod"}))

        am = AppManager(_make_app_provider(tmp_path))
        with pytest.raises(ValueError, match="name mismatch"):
            am.load_from_file("myapp", tmp_path)


class TestAppManagerSaveToFile:
    def test_saves_app_with_metadata_timestamps(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        config = {"name": "newapp", "host": "prod", "webserver": {"type": "nginx"}}
        am.save_to_file("newapp", config, tmp_path)

        saved_file = tmp_path / "apps" / "newapp.yaml"
        assert saved_file.exists()
        data = yaml.safe_load(saved_file.read_text())
        assert "metadata" in data
        assert "created" in data["metadata"]
        assert "updated" in data["metadata"]

    def test_raises_without_host_field(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        with pytest.raises(ValueError, match="host"):
            am.save_to_file("nohost", {"name": "nohost"}, tmp_path)

    def test_raises_on_name_mismatch(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        with pytest.raises(ValueError, match="name mismatch"):
            am.save_to_file("app1", {"name": "app2", "host": "prod"}, tmp_path)


class TestAppManagerLoad:
    def test_loads_existing_app_from_file(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "loadapp", "prod")
        am = AppManager(_make_app_provider(tmp_path))
        result = am.load("prod", "loadapp")
        assert result["name"] == "loadapp"

    def test_raises_file_not_found_for_missing_app(self, tmp_path):
        from navig.core.apps import AppManager

        provider = _make_app_provider(tmp_path)
        provider.load_host_config.side_effect = FileNotFoundError("host not found")
        am = AppManager(provider)
        with pytest.raises(FileNotFoundError):
            am.load("prod", "ghost")

    def test_raises_value_error_missing_webserver_type(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "badweb.yaml").write_text(
            yaml.dump({"name": "badweb", "host": "prod"}),
            encoding="utf-8",
        )

        am = AppManager(_make_app_provider(tmp_path))
        with pytest.raises(ValueError, match="webserver.type"):
            am.load("prod", "badweb")


class TestAppManagerDelete:
    def test_deletes_existing_app_file(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "todelete", "prod")
        am = AppManager(_make_app_provider(tmp_path))
        result = am.delete("prod", "todelete")
        assert result is True
        assert not (tmp_path / "apps" / "todelete.yaml").exists()

    def test_returns_false_for_missing_app(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        result = am.delete("prod", "nothere")
        assert result is False

    def test_does_not_delete_app_of_different_host(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "sharedapp", "staging")
        am = AppManager(_make_app_provider(tmp_path))
        result = am.delete("prod", "sharedapp")
        assert result is False
        assert (tmp_path / "apps" / "sharedapp.yaml").exists()


class TestAppManagerListFromFiles:
    def test_empty_dir_returns_empty(self, tmp_path):
        from navig.core.apps import AppManager

        am = AppManager(_make_app_provider(tmp_path))
        result = am.list_from_files(tmp_path)
        assert result == []

    def test_lists_only_apps_with_host(self, tmp_path):
        from navig.core.apps import AppManager

        _write_app_yaml(tmp_path, "app_with_host", "prod")
        apps_dir = tmp_path / "apps"
        (apps_dir / "no_host_app.yaml").write_text(yaml.dump({"name": "no_host_app"}))

        am = AppManager(_make_app_provider(tmp_path))
        result = am.list_from_files(tmp_path)
        assert "app_with_host" in result
        assert "no_host_app" not in result


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  connection.py — CommandResult                                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestCommandResult:
    def test_success_property_exit_0(self):
        from navig.core.connection import CommandResult

        r = CommandResult(stdout="ok", stderr="", exit_code=0)
        assert r.success is True

    def test_success_property_nonzero(self):
        from navig.core.connection import CommandResult

        r = CommandResult(stdout="", stderr="error", exit_code=1)
        assert r.success is False

    def test_output_combines_stdout_stderr(self):
        from navig.core.connection import CommandResult

        r = CommandResult(stdout="out", stderr="err", exit_code=0)
        assert "out" in r.output
        assert "err" in r.output

    def test_output_stdout_only(self):
        from navig.core.connection import CommandResult

        r = CommandResult(stdout="only_out", stderr="", exit_code=0)
        assert r.output == "only_out"

    def test_to_dict_keys(self):
        from navig.core.connection import CommandResult

        r = CommandResult(stdout="x", stderr="y", exit_code=0, duration=1.23)
        d = r.to_dict()
        assert set(d.keys()) == {"stdout", "stderr", "exit_code", "duration", "success"}
        assert d["success"] is True
        assert d["duration"] == 1.23


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  connection.py — LocalConnection                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestLocalConnection:
    def test_detect_os_returns_string(self):
        from navig.core.connection import LocalConnection

        conn = LocalConnection()
        os_type = conn.detect_os()
        assert os_type in ("windows", "linux", "darwin")

    def test_detect_os_override(self):
        from navig.core.connection import LocalConnection

        conn = LocalConnection(os_type="linux")
        assert conn.detect_os() == "linux"

    def test_run_returns_command_result(self):
        from navig.core.connection import CommandResult, LocalConnection

        conn = LocalConnection(os_type="linux")
        result = conn.run("echo hello")
        assert isinstance(result, CommandResult)

    def test_run_success_exit_code(self):
        from navig.core.connection import LocalConnection

        conn = LocalConnection(os_type="linux")
        result = conn.run("echo hi")
        assert result.exit_code == 0
        assert result.success is True

    def test_run_failure_exit_code(self):
        from navig.core.connection import LocalConnection

        conn = LocalConnection(os_type="linux")
        result = conn.run("exit 42", timeout=5)
        # exit_code may vary by shell but should not raise
        assert isinstance(result.exit_code, int)

    def test_upload_copies_file(self, tmp_path):
        from navig.core.connection import LocalConnection

        conn = LocalConnection()
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dst = tmp_path / "subdir" / "dst.txt"
        assert conn.upload(src, dst) is True
        assert dst.read_text() == "hello"

    def test_download_copies_file(self, tmp_path):
        from navig.core.connection import LocalConnection

        conn = LocalConnection()
        src = tmp_path / "remote.txt"
        src.write_text("data")
        dst = tmp_path / "local.txt"
        assert conn.download(src, dst) is True
        assert dst.read_text() == "data"

    def test_close_is_noop(self):
        from navig.core.connection import LocalConnection

        conn = LocalConnection()
        conn.close()  # should not raise

    def test_context_manager(self):
        from navig.core.connection import LocalConnection

        with LocalConnection() as conn:
            assert conn is not None


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  connection.py — SSHConnection._build_ssh_args                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestSSHConnectionBuildArgs:
    def _ssh(self, host_config: dict):
        from navig.core.connection import SSHConnection

        return SSHConnection(host_config)

    def test_standard_user_host(self):
        with patch("navig.core.connection._resolve_ssh_bin", return_value="ssh"):
            ssh = self._ssh({"host": "example.com", "user": "admin", "port": 22})
            args = ssh._build_ssh_args()
        assert "admin@example.com" in args

    def test_custom_port_added(self):
        with patch("navig.core.connection._resolve_ssh_bin", return_value="ssh"):
            ssh = self._ssh({"host": "h", "user": "u", "port": 2222})
            args = ssh._build_ssh_args()
        assert "-p" in args
        assert "2222" in args

    def test_no_port_flag_for_default_22(self):
        with patch("navig.core.connection._resolve_ssh_bin", return_value="ssh"):
            ssh = self._ssh({"host": "h", "user": "u", "port": 22})
            args = ssh._build_ssh_args()
        assert "-p" not in args

    def test_ssh_key_added(self):
        with patch("navig.core.connection._resolve_ssh_bin", return_value="ssh"):
            ssh = self._ssh({"host": "h", "user": "u", "port": 22, "ssh_key": "/tmp/key"})
            args = ssh._build_ssh_args()
        assert "-i" in args
        assert "/tmp/key" in args
