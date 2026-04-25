"""
tests/test_handler_commands.py — Unit tests for navig-windows-automation/handler.py.

Tests:
- Argument validation (missing required fields) - cross-platform
- Windows-only guard (non-Windows returns error without raising)
- on_load / on_unload / on_event lifecycle (no CommandRegistry dependency)
- COMMANDS registry completeness
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDLER_PATH = ROOT / "handler.py"


def _load_handler():
    """Load the handler module fresh for each test to avoid state bleed."""
    spec = importlib.util.spec_from_file_location("_win_handler_test", HANDLER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# Argument validation tests (no Windows required)
# ===========================================================================

class TestArgValidation:
    def setup_method(self):
        self.h = _load_handler()
        # Force non-Windows so we hit validation before platform check
        self.h._IS_WIN = False

    def test_ahk_run_missing_script(self):
        r = self.h.cmd_ahk_run({})
        assert r["status"] == "error"
        assert "Missing" in r["message"]

    def test_ahk_type_missing_text(self):
        r = self.h.cmd_ahk_type({})
        assert r["status"] == "error"
        assert "Missing" in r["message"]

    def test_ahk_click_missing_coords(self):
        r = self.h.cmd_ahk_click({})
        assert r["status"] == "error"
        assert "Missing" in r["message"]

    def test_ahk_click_missing_y_only(self):
        r = self.h.cmd_ahk_click({"x": 100})
        assert r["status"] == "error"

    def test_ahk_open_app_missing_target(self):
        r = self.h.cmd_ahk_open_app({})
        assert r["status"] == "error"
        assert "Missing" in r["message"]

    def test_ahk_window_close_missing_title(self):
        r = self.h.cmd_ahk_window_close({})
        assert r["status"] == "error"
        assert "Missing" in r["message"]


# ===========================================================================
# Windows-only guard (non-Windows returns structured error)
# ===========================================================================

class TestWindowsOnlyGuard:
    def setup_method(self):
        self.h = _load_handler()
        self.h._IS_WIN = False

    def test_ahk_run_returns_error_non_windows(self):
        r = self.h.cmd_ahk_run({"script": "Send {Enter}"})
        assert r["status"] == "error"
        assert "Windows" in r["message"]

    def test_ahk_type_returns_error_non_windows(self):
        r = self.h.cmd_ahk_type({"text": "hello"})
        assert r["status"] == "error"
        assert "Windows" in r["message"]

    def test_ahk_click_returns_error_non_windows(self):
        r = self.h.cmd_ahk_click({"x": 10, "y": 20})
        assert r["status"] == "error"
        assert "Windows" in r["message"]

    def test_ahk_open_app_returns_error_non_windows(self):
        r = self.h.cmd_ahk_open_app({"target": "notepad.exe"})
        assert r["status"] == "error"
        assert "Windows" in r["message"]

    def test_ahk_window_list_returns_error_non_windows(self):
        r = self.h.cmd_ahk_window_list({})
        assert r["status"] == "error"
        assert "Windows" in r["message"]

    def test_ahk_window_close_returns_error_non_windows(self):
        r = self.h.cmd_ahk_window_close({"title": "Notepad"})
        assert r["status"] == "error"
        assert "Windows" in r["message"]


# ===========================================================================
# COMMANDS registry
# ===========================================================================

class TestCommandsRegistry:
    def test_all_commands_present(self):
        h = _load_handler()
        expected = {
            "ahk_run", "ahk_type", "ahk_click",
            "ahk_open_app", "ahk_window_list", "ahk_window_close",
        }
        assert set(h.COMMANDS.keys()) == expected

    def test_commands_are_callable(self):
        h = _load_handler()
        for name, fn in h.COMMANDS.items():
            assert callable(fn), f"{name} must be callable"

    def test_commands_map_to_correct_functions(self):
        h = _load_handler()
        assert h.COMMANDS["ahk_run"] is h.cmd_ahk_run
        assert h.COMMANDS["ahk_type"] is h.cmd_ahk_type
        assert h.COMMANDS["ahk_click"] is h.cmd_ahk_click
        assert h.COMMANDS["ahk_open_app"] is h.cmd_ahk_open_app
        assert h.COMMANDS["ahk_window_list"] is h.cmd_ahk_window_list
        assert h.COMMANDS["ahk_window_close"] is h.cmd_ahk_window_close


# ===========================================================================
# Lifecycle tests
# ===========================================================================

class TestLifecycle:
    def test_on_event_returns_none(self):
        h = _load_handler()
        result = h.on_event("tick", {})
        assert result is None

    def test_on_load_no_raise_without_registry(self):
        """on_load must not raise when CommandRegistry import fails."""
        h = _load_handler()
        h.on_load({"store_path": str(ROOT)})  # must not raise

    def test_on_unload_no_raise_without_registry(self):
        """on_unload must not raise when CommandRegistry import fails."""
        h = _load_handler()
        h.on_load({"store_path": str(ROOT)})
        h.on_unload({})  # must not raise

    def test_on_load_registers_with_registry(self):
        """on_load calls CommandRegistry.register for each command."""
        h = _load_handler()
        registry_mock = MagicMock()
        registry_mock.register = MagicMock()

        fake_registry_mod = MagicMock()
        fake_registry_mod.CommandRegistry = registry_mock

        with patch.dict("sys.modules", {"navig.commands._registry": fake_registry_mod}):
            h.on_load({"store_path": str(ROOT)})

        assert registry_mock.register.call_count == len(h.COMMANDS)

    def test_on_unload_deregisters_with_registry(self):
        """on_unload calls CommandRegistry.deregister for each command."""
        h = _load_handler()
        registry_mock = MagicMock()
        registry_mock.deregister = MagicMock()

        fake_registry_mod = MagicMock()
        fake_registry_mod.CommandRegistry = registry_mock

        with patch.dict("sys.modules", {"navig.commands._registry": fake_registry_mod}):
            h.on_unload({})

        assert registry_mock.deregister.call_count == len(h.COMMANDS)
