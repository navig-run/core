"""Unit tests for navig.mcp_manager — MCPServer and MCPManager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.mcp_manager import MCPManager, MCPServer

# ─── MCPServer ────────────────────────────────────────────────────────────────


class TestMCPServerInit:
    def test_name_stored(self):
        s = MCPServer("my-server", {"enabled": True})
        assert s.name == "my-server"

    def test_config_stored(self):
        cfg = {"type": "npm", "enabled": False}
        s = MCPServer("s", cfg)
        assert s.config is cfg

    def test_process_is_none_on_init(self):
        s = MCPServer("s", {})
        assert s.process is None


class TestMCPServerIsEnabled:
    def test_true_when_enabled_true(self):
        s = MCPServer("s", {"enabled": True})
        assert s.is_enabled() is True

    def test_false_when_enabled_false(self):
        s = MCPServer("s", {"enabled": False})
        assert s.is_enabled() is False

    def test_false_when_key_absent(self):
        s = MCPServer("s", {})
        assert s.is_enabled() is False


class TestMCPServerIsRunning:
    def test_false_when_process_none(self):
        s = MCPServer("s", {})
        assert s.is_running() is False

    def test_true_when_process_alive(self):
        s = MCPServer("s", {})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # alive
        s.process = mock_proc
        assert s.is_running() is True

    def test_false_when_process_exited(self):
        s = MCPServer("s", {})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited
        s.process = mock_proc
        assert s.is_running() is False


class TestMCPServerGetStatus:
    def test_keys_present(self):
        s = MCPServer("my-srv", {"type": "npm", "command": "npx", "enabled": True})
        status = s.get_status()
        assert set(status.keys()) >= {"name", "enabled", "running", "pid", "type", "command"}

    def test_name_in_status(self):
        s = MCPServer("the-name", {})
        assert s.get_status()["name"] == "the-name"

    def test_pid_none_when_not_running(self):
        s = MCPServer("s", {})
        assert s.get_status()["pid"] is None

    def test_pid_set_when_running(self):
        s = MCPServer("s", {})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 1234
        s.process = mock_proc
        assert s.get_status()["pid"] == 1234


class TestMCPServerStart:
    def _make_server(self, enabled: bool = True) -> MCPServer:
        return MCPServer(
            "srv", {"command": "echo", "args": ["hello"], "env": {}, "enabled": enabled}
        )

    def test_start_success_returns_true(self):
        s = self._make_server()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # not alive before start
        mock_proc.pid = 99
        with patch("subprocess.Popen", return_value=mock_proc):
            result = s.start()
        assert result is True

    def test_start_sets_process(self):
        s = self._make_server()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        with patch("subprocess.Popen", return_value=mock_proc):
            s.start()
        assert s.process is mock_proc

    def test_start_while_running_returns_true(self):
        s = self._make_server()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # already alive
        s.process = mock_proc
        # Should short-circuit without calling Popen
        with patch("subprocess.Popen") as mock_popen:
            result = s.start()
        mock_popen.assert_not_called()
        assert result is True

    def test_start_exception_returns_false(self):
        s = self._make_server()
        with patch("subprocess.Popen", side_effect=OSError("not found")):
            result = s.start()
        assert result is False


class TestMCPServerStop:
    def _make_running_server(self) -> MCPServer:
        s = MCPServer("srv", {"command": "echo", "args": [], "env": {}})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # running
        mock_proc.pid = 42
        s.process = mock_proc
        return s

    def test_stop_running_returns_true(self):
        s = self._make_running_server()
        result = s.stop()
        assert result is True
        s.process.terminate.assert_called_once()

    def test_stop_not_running_returns_true(self):
        s = MCPServer("s", {})
        result = s.stop()
        assert result is True

    def test_stop_force_kills_on_timeout(self):
        import subprocess

        s = self._make_running_server()
        # First call wait(timeout=5) raises; second call wait() (after kill) succeeds
        s.process.wait.side_effect = [subprocess.TimeoutExpired(cmd="echo", timeout=5), None]
        result = s.stop()
        assert result is True
        s.process.kill.assert_called_once()


# ─── MCPManager ───────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_mcp_dir(tmp_path: Path) -> Path:
    return tmp_path / "mcp"


class TestMCPManagerInit:
    def test_config_dir_created(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert tmp_mcp_dir.is_dir()

    def test_servers_empty_when_no_file(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.servers == {}

    def test_servers_file_path_set(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.servers_file == tmp_mcp_dir / "servers.json"


class TestMCPManagerLoadServers:
    def test_loads_servers_from_json(self, tmp_mcp_dir: Path):
        tmp_mcp_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "test-srv": {"type": "npm", "command": "npx", "args": [], "env": {}, "enabled": True}
        }
        (tmp_mcp_dir / "servers.json").write_text(json.dumps(data), encoding="utf-8")

        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert "test-srv" in mgr.servers
        assert isinstance(mgr.servers["test-srv"], MCPServer)

    def test_bad_json_results_in_empty_servers(self, tmp_mcp_dir: Path):
        tmp_mcp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_mcp_dir / "servers.json").write_text("not-json!!!", encoding="utf-8")

        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.servers == {}


class TestMCPManagerSaveServers:
    def test_creates_servers_file(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["foo"] = MCPServer(
            "foo", {"type": "npm", "command": "npx", "args": [], "env": {}, "enabled": False}
        )
        mgr._save_servers()
        assert mgr.servers_file.exists()

    def test_serializes_all_servers(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["alpha"] = MCPServer(
            "alpha", {"type": "npm", "command": "npx", "args": [], "env": {}, "enabled": True}
        )
        mgr.servers["beta"] = MCPServer(
            "beta", {"type": "npm", "command": "node", "args": [], "env": {}, "enabled": False}
        )
        mgr._save_servers()

        saved = json.loads(mgr.servers_file.read_text())
        assert "alpha" in saved
        assert "beta" in saved

    def test_roundtrip_preserves_enabled_flag(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["srv"] = MCPServer(
            "srv", {"enabled": True, "command": "x", "args": [], "env": {}}
        )
        mgr._save_servers()

        saved = json.loads(mgr.servers_file.read_text())
        assert saved["srv"]["enabled"] is True


class TestMCPManagerListServers:
    def _mgr_with_servers(self, tmp_mcp_dir: Path) -> MCPManager:
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["en"] = MCPServer(
            "en", {"enabled": True, "command": "a", "args": [], "env": {}}
        )
        mgr.servers["dis"] = MCPServer(
            "dis", {"enabled": False, "command": "b", "args": [], "env": {}}
        )
        return mgr

    def test_list_all(self, tmp_mcp_dir: Path):
        mgr = self._mgr_with_servers(tmp_mcp_dir)
        assert len(mgr.list_servers()) == 2

    def test_enabled_only(self, tmp_mcp_dir: Path):
        mgr = self._mgr_with_servers(tmp_mcp_dir)
        enabled = mgr.list_servers(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "en"

    def test_running_only_returns_empty_when_none_running(self, tmp_mcp_dir: Path):
        mgr = self._mgr_with_servers(tmp_mcp_dir)
        running = mgr.list_servers(running_only=True)
        assert running == []


class TestMCPManagerGetServer:
    def test_returns_server_by_name(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["foo"] = MCPServer("foo", {})
        assert mgr.get_server("foo") is mgr.servers["foo"]

    def test_returns_none_for_unknown(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.get_server("nope") is None


class TestMCPManagerEnableDisable:
    def test_enable_sets_flag(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["s"] = MCPServer("s", {"enabled": False})
        mgr.enable_server("s")
        assert mgr.servers["s"].config["enabled"] is True

    def test_disable_sets_flag(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["s"] = MCPServer("s", {"enabled": True})
        mgr.disable_server("s")
        assert mgr.servers["s"].config["enabled"] is False

    def test_enable_unknown_returns_false(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.enable_server("no-such") is False

    def test_disable_unknown_returns_false(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.disable_server("no-such") is False


class TestMCPManagerUninstall:
    def test_uninstalls_existing_server(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["s"] = MCPServer("s", {"enabled": False})
        result = mgr.uninstall_server("s")
        assert result is True
        assert "s" not in mgr.servers

    def test_uninstall_unknown_returns_false(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        assert mgr.uninstall_server("ghost") is False


class TestMCPManagerStartStopAll:
    def test_start_all_enabled_zero_when_none_enabled(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["off"] = MCPServer("off", {"enabled": False})
        count = mgr.start_all_enabled()
        assert count == 0

    def test_stop_all_zero_when_none_running(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        mgr.servers["s"] = MCPServer("s", {"enabled": True})
        count = mgr.stop_all()
        assert count == 0


class TestMCPManagerSearchDirectory:
    def test_returns_list(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        result = mgr.search_directory("filesystem")
        assert isinstance(result, list)

    def test_filesystem_query_returns_match(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        results = mgr.search_directory("filesystem")
        names = [r["name"] for r in results]
        assert "filesystem" in names

    def test_empty_query_returns_all(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        results = mgr.search_directory("")
        assert len(results) > 0

    def test_nonsense_query_returns_empty_or_list(self, tmp_mcp_dir: Path):
        mgr = MCPManager(config_dir=tmp_mcp_dir)
        result = mgr.search_directory("zzz_no_match_xyzzy_9999")
        assert isinstance(result, list)
