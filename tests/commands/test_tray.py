"""
Tests for navig/commands/tray.py

Strategy: patch _is_tray_running, TRAY_SCRIPT, LOCK_FILE, and subprocess
to keep tests hermetic (no real processes).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.tray import tray_app, _is_tray_running

runner = CliRunner()

# ---------------------------------------------------------------------------
# _is_tray_running helper
# ---------------------------------------------------------------------------


class TestIsTrayRunning:
    def test_no_lock_file_returns_false(self, tmp_path):
        lock = tmp_path / "tray.lock"
        with patch("navig.commands.tray.LOCK_FILE", lock):
            running, pid = _is_tray_running()
        assert running is False
        assert pid is None

    def test_lock_file_with_garbage_returns_false(self, tmp_path):
        lock = tmp_path / "tray.lock"
        lock.write_text("not_a_pid")
        with patch("navig.commands.tray.LOCK_FILE", lock):
            running, pid = _is_tray_running()
        assert running is False
        assert pid is None

    def test_lock_file_empty_returns_false(self, tmp_path):
        lock = tmp_path / "tray.lock"
        lock.write_text("")
        with patch("navig.commands.tray.LOCK_FILE", lock):
            running, pid = _is_tray_running()
        assert running is False
        assert pid is None


# ---------------------------------------------------------------------------
# tray start
# ---------------------------------------------------------------------------


class TestTrayStart:
    def test_already_running_prints_warning(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(True, 1234)):
            result = runner.invoke(tray_app, ["start"])
        assert result.exit_code == 0
        assert "1234" in result.output or "already" in result.output.lower()

    def test_script_not_found_exits_1(self, tmp_path):
        missing = tmp_path / "nonexistent.py"
        with patch("navig.commands.tray._is_tray_running", return_value=(False, None)):
            with patch("navig.commands.tray.TRAY_SCRIPT", missing):
                result = runner.invoke(tray_app, ["start"])
        assert result.exit_code == 1

    def test_missing_pystray_dep_exits_1(self, tmp_path):
        script = tmp_path / "navig_tray.py"
        script.write_text("# fake")
        with patch("navig.commands.tray._is_tray_running", return_value=(False, None)):
            with patch("navig.commands.tray.TRAY_SCRIPT", script):
                with patch("builtins.__import__", side_effect=ImportError("no pystray")):
                    result = runner.invoke(tray_app, ["start"])
        # ImportError triggers exit 1
        assert result.exit_code == 1 or "dep" in result.output.lower() or "pip" in result.output.lower()


# ---------------------------------------------------------------------------
# tray stop
# ---------------------------------------------------------------------------


class TestTrayStop:
    def test_not_running_prints_warning(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(False, None)):
            result = runner.invoke(tray_app, ["stop"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_running_on_win32_calls_taskkill(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(True, 9999)):
            with patch("navig.commands.tray.sys") as mock_sys:
                mock_sys.platform = "win32"
                with patch("navig.commands.tray.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    with patch("navig.commands.tray.LOCK_FILE") as mock_lock:
                        mock_lock.unlink = MagicMock()
                        result = runner.invoke(tray_app, ["stop"])
        assert result.exit_code == 0

    def test_running_non_win32_calls_os_kill(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(True, 9999)):
            with patch("navig.commands.tray.sys") as mock_sys:
                mock_sys.platform = "linux"
                with patch("navig.commands.tray.os.kill") as mock_kill:
                    with patch("navig.commands.tray.LOCK_FILE") as mock_lock:
                        mock_lock.unlink = MagicMock()
                        result = runner.invoke(tray_app, ["stop"])
        assert result.exit_code == 0
        mock_kill.assert_called_once_with(9999, 15)


# ---------------------------------------------------------------------------
# tray status
# ---------------------------------------------------------------------------


class TestTrayStatus:
    def test_not_running_exits_0(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(False, None)):
            result = runner.invoke(tray_app, ["status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_running_shows_pid(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(True, 5678)):
            result = runner.invoke(tray_app, ["status"])
        assert result.exit_code == 0
        assert "5678" in result.output

    def test_json_output_not_running(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(False, None)):
            result = runner.invoke(tray_app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert data["running"] is False
        assert data["pid"] is None

    def test_json_output_running(self):
        with patch("navig.commands.tray._is_tray_running", return_value=(True, 42)):
            result = runner.invoke(tray_app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert data["running"] is True
        assert data["pid"] == 42


# ---------------------------------------------------------------------------
# tray install / uninstall — non-Windows exits immediately
# ---------------------------------------------------------------------------


class TestTrayInstallNonWindows:
    def test_install_non_win32_exits_1(self):
        with patch("navig.commands.tray.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = runner.invoke(tray_app, ["install"])
        assert result.exit_code == 1

    def test_uninstall_non_win32_exits_1(self):
        with patch("navig.commands.tray.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = runner.invoke(tray_app, ["uninstall"])
        assert result.exit_code == 1
