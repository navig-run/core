"""Tests for navig.core.window_manager — WindowManager."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.window_manager import WindowManager


@dataclass
class FakeWindow:
    id: int
    title: str
    class_name: str = "SomeClass"
    process_name: str = "app.exe"
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    is_maximized: bool = False
    is_minimized: bool = False


def _mgr(tmp_path: Path, ahk=None) -> WindowManager:
    mgr = WindowManager(ahk_adapter=ahk)
    mgr.layout_dir = tmp_path  # redirect to temp dir
    return mgr


class TestGetWindows:
    def test_returns_empty_when_no_ahk(self, tmp_path):
        mgr = _mgr(tmp_path, ahk=None)
        assert mgr.get_windows() == []

    def test_delegates_to_ahk(self, tmp_path):
        fake_ahk = MagicMock()
        fake_ahk.get_all_windows.return_value = [FakeWindow(id=1, title="App")]
        mgr = _mgr(tmp_path, ahk=fake_ahk)
        windows = mgr.get_windows()
        assert len(windows) == 1
        fake_ahk.get_all_windows.assert_called_once()


class TestSaveLayout:
    def test_no_op_when_no_ahk(self, tmp_path):
        mgr = _mgr(tmp_path, ahk=None)
        with patch("navig.core.window_manager.error") as mock_err:
            mgr.save_layout("mylay")
        mock_err.assert_called_once()
        assert not (tmp_path / "mylay.json").exists()

    def test_writes_json_file(self, tmp_path):
        ahk = MagicMock()
        ahk.get_all_windows.return_value = [FakeWindow(id=1, title="MyApp")]
        mgr = _mgr(tmp_path, ahk=ahk)
        mgr.save_layout("test")
        data = json.loads((tmp_path / "test.json").read_text())
        assert len(data) == 1
        assert data[0]["title"] == "MyApp"

    def test_filters_program_manager(self, tmp_path):
        ahk = MagicMock()
        ahk.get_all_windows.return_value = [
            FakeWindow(id=1, title="Program Manager"),
            FakeWindow(id=2, title="Real App"),
        ]
        mgr = _mgr(tmp_path, ahk=ahk)
        mgr.save_layout("filtered")
        data = json.loads((tmp_path / "filtered.json").read_text())
        assert len(data) == 1
        assert data[0]["title"] == "Real App"

    def test_filters_zero_size_windows(self, tmp_path):
        ahk = MagicMock()
        ahk.get_all_windows.return_value = [
            FakeWindow(id=1, title="Ghost", width=0, height=0),
            FakeWindow(id=2, title="Visible"),
        ]
        mgr = _mgr(tmp_path, ahk=ahk)
        mgr.save_layout("sized")
        data = json.loads((tmp_path / "sized.json").read_text())
        titles = [d["title"] for d in data]
        assert "Ghost" not in titles
        assert "Visible" in titles


class TestRestoreLayout:
    def test_no_op_when_no_ahk(self, tmp_path):
        mgr = _mgr(tmp_path, ahk=None)
        with patch("navig.core.window_manager.error") as mock_err:
            mgr.restore_layout("missing")
        mock_err.assert_called_once()

    def test_error_when_layout_not_found(self, tmp_path):
        ahk = MagicMock()
        mgr = _mgr(tmp_path, ahk=ahk)
        with patch("navig.core.window_manager.error") as mock_err:
            mgr.restore_layout("nonexistent")
        mock_err.assert_called_once()

    def test_restores_matched_window_position(self, tmp_path):
        layout = [
            {"id": 10, "title": "MyEditor", "class_name": "C", "process_name": "e.exe",
             "x": 100, "y": 200, "width": 1024, "height": 768,
             "is_maximized": False, "is_minimized": False}
        ]
        (tmp_path / "work.json").write_text(json.dumps(layout))

        cur_win = FakeWindow(id=10, title="MyEditor")
        ahk = MagicMock()
        ahk.get_all_windows.return_value = [cur_win]
        mgr = _mgr(tmp_path, ahk=ahk)
        mgr.restore_layout("work")

        ahk.move_window.assert_called_once_with("ahk_id 10", 100, 200, width=1024, height=768)

    def test_restores_maximized_window(self, tmp_path):
        layout = [
            {"id": 5, "title": "MaxApp", "class_name": "C", "process_name": "a.exe",
             "x": 0, "y": 0, "width": 1920, "height": 1080,
             "is_maximized": True, "is_minimized": False}
        ]
        (tmp_path / "maxed.json").write_text(json.dumps(layout))

        cur_win = FakeWindow(id=5, title="MaxApp")
        ahk = MagicMock()
        ahk.get_all_windows.return_value = [cur_win]
        mgr = _mgr(tmp_path, ahk=ahk)
        mgr.restore_layout("maxed")

        ahk.maximize_window.assert_called_once()


class TestListLayouts:
    def test_returns_empty_when_no_files(self, tmp_path):
        mgr = _mgr(tmp_path)
        assert mgr.list_layouts() == []

    def test_returns_stems_of_json_files(self, tmp_path):
        (tmp_path / "alpha.json").write_text("[]")
        (tmp_path / "beta.json").write_text("[]")
        mgr = _mgr(tmp_path)
        layouts = sorted(mgr.list_layouts())
        assert layouts == ["alpha", "beta"]

    def test_ignores_non_json_files(self, tmp_path):
        (tmp_path / "note.txt").write_text("hello")
        (tmp_path / "valid.json").write_text("[]")
        mgr = _mgr(tmp_path)
        assert mgr.list_layouts() == ["valid"]
