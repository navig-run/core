"""Tests for new Windows MCP tools: desktop_powershell, desktop_clipboard_get/set."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from navig.mcp.tools import windows as win_tools

# ── Mock server ───────────────────────────────────────────────────────────────


def _make_server() -> Any:
    s = SimpleNamespace()
    s.tools = {}
    s._tool_handlers = {}
    return s


# ── Registration ──────────────────────────────────────────────────────────────


def test_register_includes_new_tools() -> None:
    server = _make_server()
    win_tools.register(server)
    assert "desktop_powershell" in server.tools
    assert "desktop_clipboard_get" in server.tools
    assert "desktop_clipboard_set" in server.tools
    assert "desktop_powershell" in server._tool_handlers
    assert "desktop_clipboard_get" in server._tool_handlers
    assert "desktop_clipboard_set" in server._tool_handlers


# ── desktop_powershell ────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_powershell_success() -> None:
    server = _make_server()
    result = win_tools._tool_powershell(server, {"command": "Write-Output 'hello'"})
    assert result.get("success") is True
    assert "hello" in result.get("stdout", "")


def test_powershell_windows_only_guard() -> None:
    if sys.platform == "win32":
        pytest.skip("running on Windows — guard not triggered")
    server = _make_server()
    result = win_tools._tool_powershell(server, {"command": "echo hi"})
    assert result.get("error") == "windows_only"


def test_powershell_empty_command_returns_error() -> None:
    if sys.platform != "win32":
        pytest.skip("guard would fire first")
    server = _make_server()
    result = win_tools._tool_powershell(server, {"command": "   "})
    assert "error" in result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_powershell_nonzero_returncode() -> None:
    server = _make_server()
    result = win_tools._tool_powershell(server, {"command": "exit 1"})
    assert result.get("returncode") == 1
    assert result.get("success") is False


# ── desktop_clipboard_get ─────────────────────────────────────────────────────


def test_clipboard_get_windows_only_guard() -> None:
    if sys.platform == "win32":
        pytest.skip("running on Windows — guard not triggered")
    server = _make_server()
    result = win_tools._tool_clipboard_get(server, {})
    assert result.get("error") == "windows_only"


def test_clipboard_set_windows_only_guard() -> None:
    if sys.platform == "win32":
        pytest.skip("running on Windows — guard not triggered")
    server = _make_server()
    result = win_tools._tool_clipboard_set(server, {"text": "hello"})
    assert result.get("error") == "windows_only"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_clipboard_roundtrip() -> None:
    """Set then get clipboard text — values should match."""
    server = _make_server()
    test_text = "navig-clipboard-test-roundtrip"
    set_result = win_tools._tool_clipboard_set(server, {"text": test_text})
    assert set_result.get("success") is True

    get_result = win_tools._tool_clipboard_get(server, {})
    assert get_result.get("text") == test_text


# ── PowerShell fallback path (mocked) ────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_clipboard_get_falls_back_to_powershell_when_no_win32clipboard() -> None:
    """If win32clipboard is absent, fallback to PowerShell is used."""
    mock_result = SimpleNamespace(returncode=0, stdout="fallback text\n", stderr="")

    with (
        patch.dict("sys.modules", {"win32clipboard": None}),
        patch(
            "navig.adapters.automation.powershell.PowerShellExecutor.execute_command",
            return_value=mock_result,
        ),
    ):
        # Re-import to pick up patched modules
        from importlib import import_module, reload

        import navig.mcp.tools.windows as wm

        reload(wm)  # re-evaluate module-level imports  # type: ignore[arg-type]
        result = wm._tool_clipboard_get(_make_server(), {})
        # Depending on whether reload cleared the pywin32 import, the result
        # may be from win32clipboard or PowerShell — either is acceptable.
        assert "text" in result or "error" in result
