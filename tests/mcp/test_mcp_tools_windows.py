"""Tests for navig.mcp.tools.windows."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# All tests skip entirely on non-Windows to avoid import-time winreg failures.
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_server():
    server = SimpleNamespace()
    server.tools = {}
    server._tool_handlers = {}
    return server


# ─── register ─────────────────────────────────────────────────────────────────


def test_register_adds_tools():
    from navig.mcp.tools.windows import register

    server = _make_server()
    register(server)
    assert "desktop_process_list" in server.tools
    assert "desktop_process_kill" in server.tools
    assert "desktop_registry_get" in server.tools
    assert "desktop_registry_set" in server.tools
    assert "desktop_registry_delete" in server.tools
    assert "desktop_registry_list" in server.tools
    assert "desktop_notify" in server.tools


def test_register_adds_handlers():
    from navig.mcp.tools.windows import register

    server = _make_server()
    register(server)
    assert callable(server._tool_handlers.get("desktop_process_list"))


# ─── _coerce_bool ─────────────────────────────────────────────────────────────


def test_coerce_bool_true_string():
    from navig.mcp.tools.windows import _coerce_bool

    assert _coerce_bool("true") is True
    assert _coerce_bool("True") is True
    assert _coerce_bool("1") is True
    assert _coerce_bool("yes") is True


def test_coerce_bool_false_string():
    from navig.mcp.tools.windows import _coerce_bool

    assert _coerce_bool("false") is False
    assert _coerce_bool("0") is False
    assert _coerce_bool("no") is False


def test_coerce_bool_bool_passthrough():
    from navig.mcp.tools.windows import _coerce_bool

    assert _coerce_bool(True) is True
    assert _coerce_bool(False) is False


def test_coerce_bool_default():
    from navig.mcp.tools.windows import _coerce_bool

    assert _coerce_bool(None) is False
    assert _coerce_bool(None, default=True) is True


# ─── _parse_reg_path ──────────────────────────────────────────────────────────


def test_parse_reg_path_hkcu():
    import winreg
    from navig.mcp.tools.windows import _parse_reg_path

    hive, subkey = _parse_reg_path(r"HKCU:\Software\MyApp")
    assert hive == winreg.HKEY_CURRENT_USER
    assert subkey == r"Software\MyApp"


def test_parse_reg_path_hklm():
    import winreg
    from navig.mcp.tools.windows import _parse_reg_path

    hive, subkey = _parse_reg_path(r"HKLM:\SOFTWARE\MyApp")
    assert hive == winreg.HKEY_LOCAL_MACHINE


def test_parse_reg_path_invalid_raises():
    from navig.mcp.tools.windows import _parse_reg_path

    with pytest.raises(ValueError):
        _parse_reg_path(r"INVALID:\path")


# ─── _windows_only guard ──────────────────────────────────────────────────────


def test_windows_only_returns_error_dict_on_non_windows():
    from navig.mcp.tools.windows import _windows_only

    with patch("navig.mcp.tools.windows.sys") as mock_sys:
        mock_sys.platform = "linux"
        result = _windows_only("desktop_process_list")
    assert "error" in result


# ─── _tool_process_list ───────────────────────────────────────────────────────


def test_tool_process_list_returns_processes():
    from navig.mcp.tools.windows import _tool_process_list

    fake_proc = MagicMock()
    fake_proc.info = {
        "pid": 1234,
        "name": "notepad.exe",
        "cpu_percent": 0.1,
        "memory_info": MagicMock(rss=1024 * 1024),
        "status": "running",
    }

    class FakeNoSuchProcess(Exception):
        pass

    class FakeAccessDenied(Exception):
        pass

    with patch("navig.mcp.tools.windows.psutil") as mock_psutil:
        mock_psutil.process_iter.return_value = [fake_proc]
        mock_psutil.NoSuchProcess = FakeNoSuchProcess
        mock_psutil.AccessDenied = FakeAccessDenied
        result = _tool_process_list(None, {})

    assert "processes" in result
    assert len(result["processes"]) >= 1
    assert result["processes"][0]["pid"] == 1234


# ─── _tool_registry_get ───────────────────────────────────────────────────────


def test_tool_registry_get_returns_value():
    import winreg
    from navig.mcp.tools.windows import _tool_registry_get

    with patch("winreg.OpenKey") as mock_open, \
         patch("winreg.QueryValueEx", return_value=("hello", winreg.REG_SZ)):
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        result = _tool_registry_get(None, {
            "path": r"HKCU:\Software\Test",
            "name": "MyValue",
        })

    assert result.get("value") == "hello"


# ─── _tool_notify fallback ────────────────────────────────────────────────────


def test_tool_notify_falls_back_to_powershell():
    from navig.mcp.tools.windows import _tool_notify

    mock_executor_cls = MagicMock()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_executor_cls.return_value.execute_command.return_value = mock_result

    # Make win10toast import fail so code falls through to PowerShell path.
    with patch.dict("sys.modules", {"win10toast": None}), \
         patch("navig.mcp.tools.windows.PowerShellExecutor", mock_executor_cls):
        result = _tool_notify(None, {"title": "Test", "message": "Hello"})

    assert result.get("status") == "ok"
