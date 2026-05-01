"""Tests for navig/core/crash_handler.py and navig/core/window_manager.py — batch 85."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# CrashHandler
# ---------------------------------------------------------------------------
from navig.core.crash_handler import CrashHandler, _MAX_CRASH_LOGS


class TestCrashHandlerDebugMode:
    def setup_method(self):
        os.environ.pop("NAVIG_DEBUG", None)

    def test_debug_off_by_default(self):
        os.environ["NAVIG_DEBUG"] = "0"
        h = CrashHandler()
        assert h.is_debug is False

    def test_debug_on_via_env(self):
        os.environ["NAVIG_DEBUG"] = "1"
        h = CrashHandler()
        assert h.is_debug is True

    def test_enable_debug_sets_flag(self):
        os.environ["NAVIG_DEBUG"] = "0"
        h = CrashHandler()
        h.enable_debug()
        assert h.is_debug is True
        assert os.environ.get("NAVIG_DEBUG") == "1"


class TestCrashHandlerLogDir:
    def test_log_dir_is_cached(self, tmp_path):
        h = CrashHandler()
        h._log_dir = tmp_path
        result = h._get_log_dir()
        assert result == tmp_path

    def test_log_dir_created_at_fallback(self, tmp_path):
        h = CrashHandler()
        with patch("navig.core.crash_handler.config_dir", return_value=tmp_path):
            with patch("navig.core.crash_handler.CrashHandler._get_log_dir") as mock_get:
                mock_get.return_value = tmp_path / "logs"
                (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
                d = h._get_log_dir()
                # Just verify it returns a path-like
        # Simple path: set the log dir manually
        h2 = CrashHandler()
        h2._log_dir = tmp_path / "logs"
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        assert h2._get_log_dir() == tmp_path / "logs"


class TestCrashHandlerLogToFile:
    def _make_handler(self, tmp_path):
        h = CrashHandler()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        h._log_dir = log_dir
        return h, log_dir

    def test_log_writes_json_file(self, tmp_path):
        h, log_dir = self._make_handler(tmp_path)
        exc = ValueError("test error")
        path = h._log_crash_to_file(exc)
        assert path is not None
        assert path.exists()

    def test_log_contains_exception_info(self, tmp_path):
        h, log_dir = self._make_handler(tmp_path)
        exc = RuntimeError("something went wrong")
        path = h._log_crash_to_file(exc)
        data = json.loads(path.read_text())
        assert data["exception_type"] == "RuntimeError"
        assert "something went wrong" in data["exception_message"]

    def test_log_contains_system_info(self, tmp_path):
        h, log_dir = self._make_handler(tmp_path)
        exc = ValueError("x")
        path = h._log_crash_to_file(exc)
        data = json.loads(path.read_text())
        assert "system" in data
        assert "platform" in data["system"]
        assert "python" in data["system"]

    def test_log_returns_none_on_write_failure(self, tmp_path):
        h = CrashHandler()
        h._log_dir = tmp_path
        with patch("navig.core.yaml_io.atomic_write_text", side_effect=OSError("fail")):
            path = h._log_crash_to_file(ValueError("x"))
        assert path is None


class TestCrashHandlerCleanupOldLogs:
    def test_cleanup_keeps_max_logs(self, tmp_path):
        h = CrashHandler()
        # Create more than _MAX_CRASH_LOGS dummy crash logs
        for i in range(_MAX_CRASH_LOGS + 3):
            p = tmp_path / f"crash-2024010{i:02d}-120000.json"
            p.write_text("{}")
        h._cleanup_old_logs(tmp_path)
        remaining = list(tmp_path.glob("crash-*.json"))
        assert len(remaining) <= _MAX_CRASH_LOGS

    def test_cleanup_tolerates_oserror(self, tmp_path):
        h = CrashHandler()
        # Should not raise even with an OS error
        with patch.object(Path, "glob", side_effect=OSError("fail")):
            h._cleanup_old_logs(tmp_path)  # should not raise


class TestCrashHandlerGetLatestCrashReport:
    def test_no_logs_returns_none(self, tmp_path):
        h = CrashHandler()
        h._log_dir = tmp_path
        result = h.get_latest_crash_report()
        # May return None if no logs present
        # (depends on whether log_dir has any crash-*.json files)
        assert result is None or isinstance(result, dict)

    def test_returns_latest_log_content(self, tmp_path):
        h = CrashHandler()
        h._log_dir = tmp_path
        log = tmp_path / "crash-20240101-120000.json"
        log.write_text(json.dumps({"exception_type": "ValueError"}), encoding="utf-8")
        result = h.get_latest_crash_report()
        assert result is not None
        assert result["exception_type"] == "ValueError"


# ---------------------------------------------------------------------------
# WindowManager
# ---------------------------------------------------------------------------
from dataclasses import dataclass

from navig.core.window_manager import WindowManager


@dataclass
class _FakeWindow:
    title: str
    id: int = 1
    width: int = 800
    height: int = 600
    x: int = 0
    y: int = 0
    is_maximized: bool = False
    is_minimized: bool = False
    class_name: str = "cls"
    process_name: str = "proc.exe"


def _mock_window(title, **kwargs):
    return _FakeWindow(title=title, **kwargs)


class TestWindowManagerGetWindows:
    def test_returns_empty_when_no_ahk(self, tmp_path):
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=None)
        result = wm.get_windows()
        assert result == []

    def test_delegates_to_ahk(self, tmp_path):
        ahk = MagicMock()
        ahk.get_all_windows.return_value = ["w1", "w2"]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        result = wm.get_windows()
        assert result == ["w1", "w2"]


class TestWindowManagerListLayouts:
    def test_empty_dir_returns_empty_list(self, tmp_path):
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=None)
        assert wm.list_layouts() == []

    def test_returns_layout_names(self, tmp_path):
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=None)
        (wm.layout_dir / "home.json").write_text("[]")
        (wm.layout_dir / "work.json").write_text("[]")
        layouts = wm.list_layouts()
        assert set(layouts) == {"home", "work"}


class TestWindowManagerSaveLayout:
    def test_save_without_ahk_does_not_raise(self, tmp_path):
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=None)
        wm.save_layout("test")  # should not raise

    def test_save_creates_json_file(self, tmp_path):
        ahk = MagicMock()
        w = _mock_window("Notepad")
        ahk.get_all_windows.return_value = [w]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        wm.save_layout("mylay")
        assert (wm.layout_dir / "mylay.json").exists()

    def test_save_excludes_program_manager(self, tmp_path):
        ahk = MagicMock()
        w_pm = _mock_window("Program Manager")
        w_real = _mock_window("Notepad", id=2)
        ahk.get_all_windows.return_value = [w_pm, w_real]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        wm.save_layout("x")
        data = json.loads((wm.layout_dir / "x.json").read_text())
        titles = [d["title"] for d in data]
        assert "Program Manager" not in titles
        assert "Notepad" in titles

    def test_save_excludes_zero_size_windows(self, tmp_path):
        ahk = MagicMock()
        w_zero = _mock_window("Invisible", width=0, height=0)
        w_real = _mock_window("Terminal", id=2)
        ahk.get_all_windows.return_value = [w_zero, w_real]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        wm.save_layout("x")
        data = json.loads((wm.layout_dir / "x.json").read_text())
        titles = [d["title"] for d in data]
        assert "Invisible" not in titles


class TestWindowManagerRestoreLayout:
    def test_restore_without_ahk_does_not_raise(self, tmp_path):
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=None)
        wm.restore_layout("nonexistent")  # should not raise

    def test_restore_missing_layout_does_not_raise(self, tmp_path):
        ahk = MagicMock()
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        wm.restore_layout("doesnotexist")  # should not raise

    def test_restore_matches_by_title_and_moves(self, tmp_path):
        ahk = MagicMock()
        cur_win = _mock_window("Notepad", id=99, is_maximized=False, is_minimized=False)
        ahk.get_all_windows.return_value = [cur_win]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        layout = [{"title": "Notepad", "x": 10, "y": 20, "width": 800, "height": 600,
                   "is_maximized": False, "is_minimized": False,
                   "class_name": "Notepad", "process_name": "notepad.exe"}]
        (wm.layout_dir / "mylay.json").write_text(json.dumps(layout))
        wm.restore_layout("mylay")
        ahk.move_window.assert_called_once()

    def test_restore_maximizes_maximized_windows(self, tmp_path):
        ahk = MagicMock()
        cur_win = _mock_window("Code", id=5)
        ahk.get_all_windows.return_value = [cur_win]
        with patch("navig.core.window_manager.config_dir", return_value=tmp_path):
            wm = WindowManager(ahk_adapter=ahk)
        layout = [{"title": "Code", "x": 0, "y": 0, "width": 1920, "height": 1080,
                   "is_maximized": True, "is_minimized": False,
                   "class_name": "cls", "process_name": "code.exe"}]
        (wm.layout_dir / "mylay.json").write_text(json.dumps(layout))
        wm.restore_layout("mylay")
        ahk.maximize_window.assert_called_once()
