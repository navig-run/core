"""Tests for navig-commands-core/commands/sysinfo.py"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "commands"))
from sysinfo import handle


class TestSectionCpu:
    def test_cpu_section_with_psutil(self):
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 12.5
        mock_psutil.cpu_count.side_effect = lambda logical: 8 if logical else 4
        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            result = handle({"section": "cpu"})
        assert result["status"] == "ok"
        assert result["data"]["cpu"]["percent"] == 12.5
        assert result["data"]["cpu"]["count_logical"] == 8
        assert result["data"]["cpu"]["count_physical"] == 4
        assert "memory" not in result["data"]
        assert "disk" not in result["data"]

    def test_cpu_section_without_psutil(self):
        with patch.dict(sys.modules, {"psutil": None}):
            with patch("sysinfo.os.cpu_count", return_value=4):
                result = handle({"section": "cpu"})
        assert result["status"] == "ok"
        assert result["data"]["cpu"]["count_logical"] == 4
        assert "note" in result["data"]["cpu"]


class TestSectionMemory:
    def test_memory_section_with_psutil(self):
        mock_psutil = MagicMock()
        vm = SimpleNamespace(total=16_000_000_000, available=8_000_000_000, percent=50.0)
        mock_psutil.virtual_memory.return_value = vm
        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            result = handle({"section": "memory"})
        assert result["status"] == "ok"
        data = result["data"]["memory"]
        assert data["total_gb"] == 16.0
        assert data["available_gb"] == 8.0
        assert data["percent"] == 50.0
        assert "cpu" not in result["data"]

    def test_memory_section_without_psutil(self):
        with patch.dict(sys.modules, {"psutil": None}):
            result = handle({"section": "memory"})
        assert result["status"] == "ok"
        assert "note" in result["data"]["memory"]


class TestSectionDisk:
    def test_disk_section_stdlib(self):
        fake_usage = SimpleNamespace(total=500_000_000_000, used=200_000_000_000, free=300_000_000_000)
        with patch("sysinfo.shutil.disk_usage", return_value=fake_usage):
            result = handle({"section": "disk"})
        assert result["status"] == "ok"
        disk = result["data"]["disk"]
        assert disk["total_gb"] == 500.0
        assert disk["used_gb"] == 200.0
        assert disk["free_gb"] == 300.0
        assert disk["percent"] == 40.0

    def test_disk_custom_path(self):
        fake_usage = SimpleNamespace(total=100_000_000_000, used=50_000_000_000, free=50_000_000_000)
        with patch("sysinfo.shutil.disk_usage", return_value=fake_usage) as mock_du:
            handle({"section": "disk", "path": "C:\\"})
        mock_du.assert_called_once_with("C:\\")

    def test_disk_error_captured(self):
        with patch("sysinfo.shutil.disk_usage", side_effect=FileNotFoundError("no such path")):
            result = handle({"section": "disk", "path": "/nonexistent"})
        assert result["status"] == "ok"
        assert "error" in result["data"]["disk"]


class TestSectionAll:
    def test_all_section_default(self):
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 5.0
        mock_psutil.cpu_count.side_effect = lambda logical: 4 if logical else 2
        vm = SimpleNamespace(total=8_000_000_000, available=4_000_000_000, percent=50.0)
        mock_psutil.virtual_memory.return_value = vm
        fake_usage = SimpleNamespace(total=100_000_000_000, used=10_000_000_000, free=90_000_000_000)
        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            with patch("sysinfo.shutil.disk_usage", return_value=fake_usage):
                result = handle({})
        assert result["status"] == "ok"
        assert "cpu" in result["data"]
        assert "memory" in result["data"]
        assert "disk" in result["data"]

    def test_unknown_section_returns_empty_data(self):
        result = handle({"section": "network"})
        assert result["status"] == "ok"
        assert result["data"] == {}
