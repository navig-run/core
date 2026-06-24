"""
Tests for navig.mcp.tools.desktop
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.mcp.tools.desktop import (
    _desktop_audit_initialized,
    _desktop_client,
    _desktop_permission_check,
    register,
)

# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_expected_tool_names(self):
        server = MagicMock()
        server.tools = {}
        server._tool_handlers = {}
        register(server)

        for name in (
            "desktop_find",
            "desktop_tree",
            "desktop_click",
            "desktop_set_value",
            "desktop_ahk",
        ):
            assert name in server.tools
            assert name in server._tool_handlers

    def test_each_tool_has_name_and_description(self):
        server = MagicMock()
        server.tools = {}
        server._tool_handlers = {}
        register(server)

        for name, spec in server.tools.items():
            assert "name" in spec
            assert "description" in spec

    def test_tool_handlers_are_callable(self):
        server = MagicMock()
        server.tools = {}
        server._tool_handlers = {}
        register(server)

        for handler in server._tool_handlers.values():
            assert callable(handler)


# ---------------------------------------------------------------------------
# _desktop_client() — on non-Windows raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows only test")
def test_desktop_client_raises_on_non_windows():
    with pytest.raises(ValueError, match="Windows only"):
        _desktop_client()


# ---------------------------------------------------------------------------
# _desktop_permission_check()
# ---------------------------------------------------------------------------


class TestDesktopPermissionCheck:
    def test_no_permission_returns_error_dict(self):
        """When store can't be loaded or returns no missions, permission is denied."""
        with patch(
            "navig.contracts.store.get_runtime_store",
            side_effect=ImportError("no store"),
        ):
            result = _desktop_permission_check("desktop_click")

        assert result is not None
        assert result["error"] == "permission_denied"
        assert result["tool"] == "desktop_click"

    def test_exception_in_store_denies_permission(self):
        with patch(
            "navig.contracts.store.get_runtime_store",
            side_effect=RuntimeError("store exploded"),
        ):
            result = _desktop_permission_check("desktop_ahk")

        assert result is not None
        assert "permission_denied" in result["error"]

    def test_mission_with_desktop_permission_grants_access(self):
        mock_mission = MagicMock()
        mock_mission.payload = {"step_metadata": {"desktop_permission": True}}
        mock_store = MagicMock()
        mock_store.list_missions.return_value = [mock_mission]

        with patch("navig.contracts.store.get_runtime_store", return_value=mock_store):
            result = _desktop_permission_check("desktop_click")

        assert result is None  # None means granted

    def test_mission_without_desktop_permission_denies(self):
        mock_mission = MagicMock()
        mock_mission.payload = {"step_metadata": {}}
        mock_store = MagicMock()
        mock_store.list_missions.return_value = [mock_mission]

        with patch("navig.contracts.store.get_runtime_store", return_value=mock_store):
            result = _desktop_permission_check("desktop_click")

        assert result is not None
        assert result["error"] == "permission_denied"


# ---------------------------------------------------------------------------
# _desktop_audit_initialized()
# ---------------------------------------------------------------------------


class TestDesktopAuditInitialized:
    def test_returns_none_when_writable(self, tmp_path):
        audit_log = tmp_path / "logs" / "audit.jsonl"
        with patch.dict(os.environ, {"NAVIG_DESKTOP_AUDIT_LOG": str(audit_log)}):
            result = _desktop_audit_initialized()
        assert result is None

    def test_returns_error_when_path_not_writable(self, tmp_path):
        # Point to a path inside a non-existent and uncreateable location — use a file as parent
        # Create a file where a dir is expected
        not_a_dir = tmp_path / "notadir"
        not_a_dir.write_text("I am a file")
        bad_path = str(not_a_dir / "audit.jsonl")
        with patch.dict(os.environ, {"NAVIG_DESKTOP_AUDIT_LOG": bad_path}):
            result = _desktop_audit_initialized()
        assert result is not None
        assert result["error"] == "audit_log_unavailable"
