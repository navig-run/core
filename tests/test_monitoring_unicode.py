"""
Tests for Unicode-safe monitoring output helpers.

Ensures that _traffic_light, _disk_status, and _health_icon return sensible
strings even when the terminal cannot encode emoji (Windows cp1252 / charmap).
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers to import monitoring module with Rich mocked out
# ---------------------------------------------------------------------------


def _import_monitoring():
    """Import monitoring module with its Rich + navig dependencies stubbed."""
    stub_rich_table = types.ModuleType("rich.table")
    stub_rich_panel = types.ModuleType("rich.panel")
    stub_rich_progress = types.ModuleType("rich.progress")
    stub_rich_table.Table = MagicMock
    stub_rich_panel.Panel = MagicMock
    stub_rich_progress.Progress = MagicMock
    stub_rich_progress.SpinnerColumn = MagicMock
    stub_rich_progress.TextColumn = MagicMock

    stub_cfg = types.ModuleType("navig.config")
    stub_cfg.get_config_manager = MagicMock
    stub_remote = types.ModuleType("navig.remote")
    stub_remote.RemoteOperations = MagicMock

    # Stub console_helper so it doesn't touch stdout
    stub_ch = types.ModuleType("navig.console_helper")
    stub_ch.console = MagicMock()

    def _fake_safe_symbol(preferred: str, fallback: str) -> str:
        return preferred  # always return preferred in unit test context

    stub_ch._safe_symbol = _fake_safe_symbol

    mocks = {
        "rich.table": stub_rich_table,
        "rich.panel": stub_rich_panel,
        "rich.progress": stub_rich_progress,
        "navig.config": stub_cfg,
        "navig.remote": stub_remote,
        "navig.console_helper": stub_ch,
    }
    with patch.dict("sys.modules", mocks):
        spec = importlib.util.spec_from_file_location(
            "navig.commands.monitoring",
            "navig/commands/monitoring.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_traffic_light_ok():
    mod = _import_monitoring()
    result = mod._traffic_light(30.0)
    assert "OK" in result


def test_traffic_light_medium():
    mod = _import_monitoring()
    result = mod._traffic_light(65.0)
    assert "MEDIUM" in result


def test_traffic_light_high():
    mod = _import_monitoring()
    result = mod._traffic_light(90.0)
    assert "HIGH" in result


def test_disk_status_ok():
    mod = _import_monitoring()
    result = mod._disk_status(50.0, 80)
    assert "OK" in result


def test_disk_status_warning():
    mod = _import_monitoring()
    result = mod._disk_status(75.0, 80)
    assert "WARNING" in result


def test_disk_status_alert():
    mod = _import_monitoring()
    result = mod._disk_status(95.0, 80)
    assert "ALERT" in result


def test_health_icon_healthy():
    mod = _import_monitoring()
    result = mod._health_icon("healthy")
    assert "healthy" in result
    assert "green" in result


def test_health_icon_stopped():
    mod = _import_monitoring()
    result = mod._health_icon("stopped")
    assert "stopped" in result
    assert "red" in result


def test_health_icon_not_installed():
    mod = _import_monitoring()
    result = mod._health_icon("not-installed")
    assert "N/A" in result


def test_health_icon_unknown():
    mod = _import_monitoring()
    result = mod._health_icon("unknown")
    assert "unknown" in result
    assert "yellow" in result


def test_safe_symbol_ascii_fallback():
    """_safe_symbol must return fallback when terminal cannot encode preferred.

    Injects a fake _real into _LazyConsole (bypassing __slots__) so that
    console.file.encoding reports 'ascii'.
    """
    import navig.console_helper as ch

    fc = MagicMock()
    fc.file.encoding = "ascii"
    orig = object.__getattribute__(ch.console, "_real")
    object.__setattr__(ch.console, "_real", fc)
    try:
        result = ch._safe_symbol(chr(0x1F534), "[HIGH]")
        assert result == "[HIGH]", f"Expected [HIGH], got {result!r}"
    finally:
        object.__setattr__(ch.console, "_real", orig)


def test_safe_symbol_utf8_preferred():
    """_safe_symbol returns preferred symbol when terminal supports UTF-8.

    Injects a fake _real into _LazyConsole (bypassing __slots__) so that
    console.file.encoding reports 'utf-8'.
    """
    import navig.console_helper as ch

    fc = MagicMock()
    fc.file.encoding = "utf-8"
    orig = object.__getattribute__(ch.console, "_real")
    object.__setattr__(ch.console, "_real", fc)
    try:
        result = ch._safe_symbol(chr(0x1F534), "[HIGH]")
        assert result == chr(0x1F534), f"Expected red circle, got {result!r}"
    finally:
        object.__setattr__(ch.console, "_real", orig)


def test_console_helper_load_reconfigures_stdout_on_windows():
    """On win32, _load() should attempt stdout.reconfigure(encoding='utf-8')."""
    import navig.console_helper as ch

    reconfigure_called = []

    class FakeStdout:
        def reconfigure(self, **kwargs):
            reconfigure_called.append(kwargs)

    lazy = ch._LazyConsole()
    with (
        patch("sys.platform", "win32"),
        patch("sys.stdout", FakeStdout()),
        patch("navig.console_helper._Console", return_value=MagicMock()),
    ):
        lazy._load()

    assert reconfigure_called, "reconfigure() was not called on Windows"
    assert reconfigure_called[0].get("encoding") == "utf-8"
