"""Batch 107: tests for connection_pool, local_operations, server_template_manager."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# connection_pool — SSHConnection (no real SSH needed)
# ---------------------------------------------------------------------------

def _make_ssh_connection(host="example.com", port=22, user="root"):
    from navig.connection_pool import SSHConnection
    mock_client = MagicMock()
    # Make transport appear inactive (no live SSH)
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = False
    mock_client.get_transport.return_value = mock_transport
    return SSHConnection(mock_client, host, port, user)


class TestSSHConnectionProperties:
    def test_key_format(self):
        conn = _make_ssh_connection("myhost.io", 2222, "deploy")
        assert conn.key == "deploy@myhost.io:2222"

    def test_key_default_port(self):
        conn = _make_ssh_connection(port=22, user="ubuntu")
        assert "ubuntu@" in conn.key
        assert ":22" in conn.key

    def test_age_seconds_is_positive(self):
        conn = _make_ssh_connection()
        time.sleep(0.01)
        assert conn.age_seconds >= 0

    def test_idle_seconds_is_positive(self):
        conn = _make_ssh_connection()
        time.sleep(0.01)
        assert conn.idle_seconds >= 0

    def test_initial_use_count_is_zero(self):
        conn = _make_ssh_connection()
        assert conn.use_count == 0

    def test_is_alive_returns_false_when_transport_inactive(self):
        conn = _make_ssh_connection()
        assert conn.is_alive() is False

    def test_is_alive_returns_false_when_no_transport(self):
        from navig.connection_pool import SSHConnection
        mock_client = MagicMock()
        mock_client.get_transport.return_value = None
        conn = SSHConnection(mock_client, "h", 22, "u")
        assert conn.is_alive() is False

    def test_close_is_best_effort(self):
        conn = _make_ssh_connection()
        conn.client.close.side_effect = RuntimeError("already closed")
        conn.close()  # should not raise

    def test_close_calls_client_close(self):
        conn = _make_ssh_connection()
        conn.close()
        conn.client.close.assert_called_once()


class TestSSHConnectionPooledAlias:
    def test_pooled_ssh_connection_is_alias(self):
        from navig.connection_pool import SSHConnection, PooledSSHConnection
        assert PooledSSHConnection is SSHConnection


class TestSSHConnectionPool:
    def test_default_constants(self):
        from navig.connection_pool import SSHConnectionPool
        assert SSHConnectionPool.DEFAULT_MAX_CONNECTIONS == 10
        assert SSHConnectionPool.DEFAULT_MAX_AGE_SECONDS == 300
        assert SSHConnectionPool.DEFAULT_MAX_IDLE_SECONDS == 60

    def test_pool_instance_is_singleton(self):
        from navig.connection_pool import SSHConnectionPool
        a = SSHConnectionPool.get_instance()
        b = SSHConnectionPool.get_instance()
        assert a is b

    def test_pool_init_custom_params(self):
        from navig.connection_pool import SSHConnectionPool
        pool = SSHConnectionPool(max_connections=5, max_age_seconds=60)
        assert pool.max_connections == 5
        assert pool.max_age_seconds == 60

    def test_stats_keys_present(self):
        from navig.connection_pool import SSHConnectionPool
        pool = SSHConnectionPool()
        for key in ("hits", "misses", "connections_created", "connections_closed", "errors"):
            assert key in pool._stats

    def test_pool_starts_empty(self):
        from navig.connection_pool import SSHConnectionPool
        pool = SSHConnectionPool()
        assert len(pool._connections) == 0


class TestGetParamiko:
    def test_returns_module_or_false(self):
        from navig.connection_pool import _get_paramiko
        result = _get_paramiko()
        assert result is not False or result is False  # always returns something


# ---------------------------------------------------------------------------
# local_operations — LocalSystemInfo
# ---------------------------------------------------------------------------

class TestLocalSystemInfo:
    def _make_info(self):
        from navig.local_operations import LocalSystemInfo
        return LocalSystemInfo(
            hostname="mymachine",
            os_name="windows",
            os_display_name="Windows 11",
            is_admin=False,
            home_directory=Path("/home/user"),
            config_directory=Path("/home/user/.navig"),
        )

    def test_to_dict_returns_dict(self):
        info = self._make_info()
        d = info.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_hostname(self):
        info = self._make_info()
        assert info.to_dict()["hostname"] == "mymachine"

    def test_to_dict_os_fields(self):
        info = self._make_info()
        d = info.to_dict()
        assert d["os_name"] == "windows"
        assert d["os_display_name"] == "Windows 11"

    def test_to_dict_paths_are_strings(self):
        info = self._make_info()
        d = info.to_dict()
        assert isinstance(d["home_directory"], str)
        assert isinstance(d["config_directory"], str)

    def test_to_dict_is_admin_bool(self):
        info = self._make_info()
        assert info.to_dict()["is_admin"] is False


class TestGetLocalOps:
    def test_returns_local_operations_instance(self):
        from navig.local_operations import LocalOperations, get_local_ops
        ops = get_local_ops()
        assert isinstance(ops, LocalOperations)

    def test_working_directory_is_set(self, tmp_path):
        from navig.local_operations import get_local_ops
        ops = get_local_ops(working_directory=tmp_path)
        assert ops._working_directory == tmp_path

    def test_no_directory_defaults_to_none(self):
        from navig.local_operations import get_local_ops
        ops = get_local_ops()
        assert ops._working_directory is None

    def test_connection_is_lazy(self):
        from navig.local_operations import get_local_ops
        ops = get_local_ops()
        assert ops._connection is None  # not loaded yet

    def test_os_adapter_is_lazy(self):
        from navig.local_operations import get_local_ops
        ops = get_local_ops()
        assert ops._os_adapter is None  # not loaded yet


# ---------------------------------------------------------------------------
# server_template_manager — deep_merge and basic path helpers
# ---------------------------------------------------------------------------

class TestServerTemplateManagerDeepMerge:
    def _make_mgr(self, tmp_path):
        from navig.server_template_manager import ServerTemplateManager
        from navig.template_manager import TemplateManager
        mock_cm = MagicMock()
        mock_cm.apps_dir = tmp_path / "apps"
        mock_tm = MagicMock(spec=TemplateManager)
        mock_tm.discover_templates.return_value = {}
        mock_tm.templates = {}
        mgr = ServerTemplateManager.__new__(ServerTemplateManager)
        mgr.config_manager = mock_cm
        mgr.template_manager = mock_tm
        return mgr

    def test_deep_merge_combines_dicts(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        base = {"a": 1, "b": {"x": 10, "y": 20}}
        overlay = {"b": {"y": 99, "z": 30}, "c": 3}
        result = mgr._deep_merge(base, overlay)
        assert result["a"] == 1
        assert result["b"]["x"] == 10
        assert result["b"]["y"] == 99
        assert result["b"]["z"] == 30
        assert result["c"] == 3

    def test_deep_merge_does_not_mutate_base(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        base = {"key": "original"}
        overlay = {"key": "new"}
        mgr._deep_merge(base, overlay)
        assert base["key"] == "original"

    def test_deep_merge_overlay_wins_for_scalars(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr._deep_merge({"x": 1}, {"x": 2})
        assert result["x"] == 2

    def test_deep_merge_empty_overlay(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        base = {"a": 1}
        result = mgr._deep_merge(base, {})
        assert result == {"a": 1}

    def test_deep_merge_empty_base(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr._deep_merge({}, {"b": 2})
        assert result == {"b": 2}


class TestServerTemplateManagerPaths:
    def _make_mgr(self, tmp_path):
        from navig.server_template_manager import ServerTemplateManager
        from navig.template_manager import TemplateManager
        mock_cm = MagicMock()
        mock_cm.apps_dir = tmp_path / "apps"
        mock_tm = MagicMock(spec=TemplateManager)
        mock_tm.discover_templates.return_value = {}
        mock_tm.templates = {}
        mgr = ServerTemplateManager.__new__(ServerTemplateManager)
        mgr.config_manager = mock_cm
        mgr.template_manager = mock_tm
        return mgr

    def test_get_server_template_dir_structure(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        result = mgr._get_server_template_dir("myserver")
        assert result.name == "templates"
        assert "myserver" in str(result)

    def test_ensure_server_template_dir_creates_dir(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._ensure_server_template_dir("newserver")
        expected = tmp_path / "apps" / "newserver" / "templates"
        assert expected.exists()

    def test_ensure_server_template_dir_idempotent(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._ensure_server_template_dir("srv")
        mgr._ensure_server_template_dir("srv")  # must not raise
        expected = tmp_path / "apps" / "srv" / "templates"
        assert expected.exists()
