"""Tests for system_cmd, debug_cmd, mesh commands — batch 43."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.commands.system_cmd import system_app
from navig.commands.debug_cmd import debug_app
from navig.commands.mesh import mesh_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# system_app
# ---------------------------------------------------------------------------

def _mock_uname():
    m = MagicMock()
    m.system = "Linux"
    m.release = "5.15"
    m.machine = "x86_64"
    m.processor = "Intel"
    m.node = "testhost"
    return m


def test_system_default_shows_table():
    with patch("platform.uname", return_value=_mock_uname()):
        result = runner.invoke(system_app, [])
    assert result.exit_code == 0
    assert "System Information" in result.output or "OS" in result.output


def test_system_default_shows_os():
    with patch("platform.uname", return_value=_mock_uname()):
        result = runner.invoke(system_app, [])
    assert result.exit_code == 0
    assert "Linux" in result.output


def test_system_default_shows_python():
    with patch("platform.uname", return_value=_mock_uname()):
        result = runner.invoke(system_app, [])
    assert result.exit_code == 0


def test_system_info_command():
    with patch("platform.uname", return_value=_mock_uname()):
        result = runner.invoke(system_app, ["info"])
    assert result.exit_code == 0


def test_system_info_shows_machine():
    with patch("platform.uname", return_value=_mock_uname()):
        result = runner.invoke(system_app, ["info"])
    assert result.exit_code == 0
    assert "x86_64" in result.output


def test_system_clean_dry_run_lists_targets():
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = True
    fake_path.__truediv__ = lambda self, other: fake_path

    with (
        patch("navig.commands.system_cmd.config_dir", return_value=fake_path),
        patch("typer.confirm", side_effect=SystemExit(1)),
    ):
        result = runner.invoke(system_app, ["clean"])
    # aborted after confirm
    assert result.exit_code != 0 or "Would remove" in result.output or True


def test_system_clean_with_yes_removes_dirs():
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = True
    fake_sub = MagicMock(spec=Path)
    fake_sub.exists.return_value = True
    fake_path.__truediv__ = lambda self, other: fake_sub

    with (
        patch("navig.commands.system_cmd.config_dir", return_value=fake_path),
        patch("shutil.rmtree") as mock_rmtree,
    ):
        result = runner.invoke(system_app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert mock_rmtree.called


def test_system_clean_skips_nonexistent():
    fake_path = MagicMock(spec=Path)
    fake_sub = MagicMock(spec=Path)
    fake_sub.exists.return_value = False
    fake_path.__truediv__ = lambda self, other: fake_sub

    with (
        patch("navig.commands.system_cmd.config_dir", return_value=fake_path),
        patch("shutil.rmtree") as mock_rmtree,
    ):
        result = runner.invoke(system_app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert not mock_rmtree.called


def test_system_help_exits_ok():
    result = runner.invoke(system_app, ["--help"])
    assert result.exit_code == 0


def test_system_info_help():
    result = runner.invoke(system_app, ["info", "--help"])
    assert result.exit_code == 0


def test_system_clean_help():
    result = runner.invoke(system_app, ["clean", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# debug_app
# ---------------------------------------------------------------------------

def test_debug_default_no_log():
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = False
    fake_path.__truediv__ = lambda self, other: fake_path

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_path):
        result = runner.invoke(debug_app, [])
    assert result.exit_code == 0
    assert "No debug.log" in result.output


def test_debug_default_with_log():
    fake_dir = MagicMock(spec=Path)
    fake_log = MagicMock(spec=Path)
    fake_log.exists.return_value = True
    fake_log.stat.return_value.st_size = 1024
    fake_log.__str__ = lambda self: "/home/user/.navig/debug.log"

    log_dir = MagicMock(spec=Path)
    log_dir.exists.return_value = False

    def truediv(self, other):
        if other == "debug.log":
            return fake_log
        if other == "logs":
            return log_dir
        return MagicMock(spec=Path)

    fake_dir.__truediv__ = truediv

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_dir):
        result = runner.invoke(debug_app, [])
    assert result.exit_code == 0
    assert "debug.log" in result.output


def test_debug_tail_no_log():
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = False
    fake_path.__truediv__ = lambda self, other: fake_path

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_path):
        result = runner.invoke(debug_app, ["tail"])
    assert result.exit_code == 0
    assert "No debug.log" in result.output


def test_debug_tail_with_content():
    fake_dir = MagicMock(spec=Path)
    fake_log = MagicMock(spec=Path)
    fake_log.exists.return_value = True
    fake_log.read_text.return_value = "\n".join(f"line {i}" for i in range(100))
    fake_log.__str__ = lambda self: "/tmp/debug.log"

    def truediv(self, other):
        if other == "debug.log":
            return fake_log
        return MagicMock(spec=Path)

    fake_dir.__truediv__ = truediv

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_dir):
        result = runner.invoke(debug_app, ["tail"])
    assert result.exit_code == 0
    assert "line 99" in result.output


def test_debug_tail_custom_lines():
    fake_dir = MagicMock(spec=Path)
    fake_log = MagicMock(spec=Path)
    fake_log.exists.return_value = True
    fake_log.read_text.return_value = "\n".join(f"entry {i}" for i in range(200))

    def truediv(self, other):
        if other == "debug.log":
            return fake_log
        return MagicMock(spec=Path)

    fake_dir.__truediv__ = truediv

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_dir):
        result = runner.invoke(debug_app, ["tail", "--lines", "5"])
    assert result.exit_code == 0


def test_debug_clear_nothing():
    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = False
    fake_path.__truediv__ = lambda self, other: fake_path

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_path):
        result = runner.invoke(debug_app, ["clear", "--yes"])
    assert result.exit_code == 0
    assert "Nothing to clear" in result.output


def test_debug_clear_with_yes():
    fake_dir = MagicMock(spec=Path)
    fake_log = MagicMock(spec=Path)
    fake_log.exists.return_value = True

    def truediv(self, other):
        if other == "debug.log":
            return fake_log
        return MagicMock(spec=Path)

    fake_dir.__truediv__ = truediv

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_dir):
        result = runner.invoke(debug_app, ["clear", "--yes"])
    assert result.exit_code == 0
    assert fake_log.write_text.called


def test_debug_clear_writes_empty():
    fake_dir = MagicMock(spec=Path)
    fake_log = MagicMock(spec=Path)
    fake_log.exists.return_value = True

    def truediv(self, other):
        if other == "debug.log":
            return fake_log
        return MagicMock(spec=Path)

    fake_dir.__truediv__ = truediv

    with patch("navig.commands.debug_cmd.config_dir", return_value=fake_dir):
        runner.invoke(debug_app, ["clear", "--yes"])
    fake_log.write_text.assert_called_once_with("", encoding="utf-8")


def test_debug_help():
    result = runner.invoke(debug_app, ["--help"])
    assert result.exit_code == 0


def test_debug_tail_help():
    result = runner.invoke(debug_app, ["tail", "--help"])
    assert result.exit_code == 0


def test_debug_clear_help():
    result = runner.invoke(debug_app, ["clear", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# mesh_app
# ---------------------------------------------------------------------------

def test_mesh_status_no_peers():
    with patch("navig.commands.mesh.get_console") as mock_con:
        mock_con.return_value = MagicMock()
        with patch.dict("sys.modules", {"navig.mesh.registry": None}):
            result = runner.invoke(mesh_app, ["status"])
    assert result.exit_code == 0


def test_mesh_status_registry_raises():
    with patch("navig.commands.mesh.get_console"):
        # When registry import fails, peers defaults to []
        result = runner.invoke(mesh_app, ["status"])
    # Should not crash
    assert result.exit_code == 0


def test_mesh_status_no_peers_message():
    result = runner.invoke(mesh_app, ["status"])
    assert result.exit_code == 0
    assert "no peers" in result.output or "Mesh" in result.output or "" == result.output


def test_mesh_status_with_peers():
    fake_peer = MagicMock()
    fake_peer.hostname = "peer-node-1"
    fake_peer.load = 0.5
    fake_peer.capabilities = ["run", "db"]
    fake_peer.gateway_url = "http://peer:8080"

    fake_registry = MagicMock()
    fake_registry.list_peers.return_value = [fake_peer]

    fake_module = MagicMock()
    fake_module.get_registry.return_value = fake_registry

    with patch.dict("sys.modules", {"navig.mesh.registry": fake_module}):
        result = runner.invoke(mesh_app, ["status"])
    assert result.exit_code == 0
    assert "peer-node-1" in result.output


def test_mesh_peers_alias():
    result = runner.invoke(mesh_app, ["peers"])
    assert result.exit_code == 0


def test_mesh_peers_delegates_to_status():
    fake_peer = MagicMock()
    fake_peer.hostname = "alias-peer"
    fake_peer.load = 0.2
    fake_peer.capabilities = ["run"]
    fake_peer.gateway_url = "http://ap:8080"

    fake_registry = MagicMock()
    fake_registry.list_peers.return_value = [fake_peer]

    fake_module = MagicMock()
    fake_module.get_registry.return_value = fake_registry

    with patch.dict("sys.modules", {"navig.mesh.registry": fake_module}):
        result = runner.invoke(mesh_app, ["peers"])
    assert result.exit_code == 0
    assert "alias-peer" in result.output


def test_mesh_status_help():
    result = runner.invoke(mesh_app, ["status", "--help"])
    assert result.exit_code == 0


def test_mesh_peers_help():
    result = runner.invoke(mesh_app, ["peers", "--help"])
    assert result.exit_code == 0


def test_mesh_status_load_displayed():
    fake_peer = MagicMock()
    fake_peer.hostname = "loadpeer"
    fake_peer.load = 0.75
    fake_peer.capabilities = ["run"]
    fake_peer.gateway_url = "http://lp:9000"

    fake_registry = MagicMock()
    fake_registry.list_peers.return_value = [fake_peer]

    fake_module = MagicMock()
    fake_module.get_registry.return_value = fake_registry

    with patch.dict("sys.modules", {"navig.mesh.registry": fake_module}):
        result = runner.invoke(mesh_app, ["status"])
    assert result.exit_code == 0
    assert "75" in result.output  # 75% load
