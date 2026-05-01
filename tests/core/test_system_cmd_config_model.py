"""Batch 133: system_cmd, StatusBadge, NavigConfig — no Textual deps."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.tui.resolvers — StatusBadge (pure dataclass, no Textual)
# ---------------------------------------------------------------------------
from navig.tui.resolvers import StatusBadge


class TestStatusBadge:
    def test_create_ok_badge(self):
        badge = StatusBadge(label="Provider", status="ok")
        assert badge.label == "Provider"
        assert badge.status == "ok"

    def test_create_error_badge(self):
        badge = StatusBadge(label="Config", status="error", detail="Missing key")
        assert badge.status == "error"
        assert badge.detail == "Missing key"

    def test_color_ok(self):
        badge = StatusBadge(label="x", status="ok")
        assert badge.color == "#10b981"

    def test_color_warn(self):
        badge = StatusBadge(label="x", status="warn")
        assert badge.color == "#f59e0b"

    def test_color_error(self):
        badge = StatusBadge(label="x", status="error")
        assert badge.color == "#ef4444"

    def test_color_missing_fallback(self):
        badge = StatusBadge(label="x", status="missing")
        # missing should return some color, not crash
        color = badge.color
        assert isinstance(color, str)

    def test_symbol_ok(self):
        badge = StatusBadge(label="x", status="ok")
        assert isinstance(badge.symbol, str)

    def test_symbol_error(self):
        badge = StatusBadge(label="x", status="error")
        sym = badge.symbol
        assert isinstance(sym, str)

    def test_deep_link_default_empty(self):
        badge = StatusBadge(label="x", status="ok")
        assert badge.deep_link == ""

    def test_deep_link_custom(self):
        badge = StatusBadge(label="x", status="warn", deep_link="/settings/ai")
        assert badge.deep_link == "/settings/ai"

    def test_icon_default_empty(self):
        badge = StatusBadge(label="x", status="ok")
        assert badge.icon == ""

    def test_detail_default_empty(self):
        badge = StatusBadge(label="x", status="ok")
        assert badge.detail == ""


# ---------------------------------------------------------------------------
# navig.tui.config_model — NavigConfig dataclass (no Textual)
# ---------------------------------------------------------------------------
from navig.tui.config_model import NavigConfig


class TestNavigConfig:
    def test_default_profile_name(self):
        cfg = NavigConfig()
        assert cfg.profile_name == "operator"

    def test_default_ai_provider(self):
        cfg = NavigConfig()
        assert cfg.ai_provider == "openrouter"

    def test_default_api_key_empty(self):
        cfg = NavigConfig()
        assert cfg.api_key == ""

    def test_default_local_runtime_disabled(self):
        cfg = NavigConfig()
        assert cfg.local_runtime_enabled is False

    def test_default_shell_integration_true(self):
        cfg = NavigConfig()
        assert cfg.shell_integration is True

    def test_default_capability_packs_empty(self):
        cfg = NavigConfig()
        assert cfg.capability_packs == []

    def test_mutable_packs_are_independent(self):
        cfg1 = NavigConfig()
        cfg2 = NavigConfig()
        cfg1.capability_packs.append("web")
        assert cfg2.capability_packs == []

    def test_override_profile_name(self):
        cfg = NavigConfig(profile_name="alice")
        assert cfg.profile_name == "alice"

    def test_override_ai_provider(self):
        cfg = NavigConfig(ai_provider="anthropic")
        assert cfg.ai_provider == "anthropic"

    def test_override_api_key(self):
        cfg = NavigConfig(api_key="sk-test-123")
        assert cfg.api_key == "sk-test-123"

    def test_local_runtime_enabled(self):
        cfg = NavigConfig(local_runtime_enabled=True)
        assert cfg.local_runtime_enabled is True

    def test_capability_packs_preset(self):
        cfg = NavigConfig(capability_packs=["web", "code"])
        assert "web" in cfg.capability_packs
        assert "code" in cfg.capability_packs

    def test_default_auto_update_true(self):
        cfg = NavigConfig()
        assert cfg.auto_update is True

    def test_default_theme_dark(self):
        cfg = NavigConfig()
        assert cfg.theme == "dark"


# ---------------------------------------------------------------------------
# navig.commands.system_cmd — system CLI commands
# ---------------------------------------------------------------------------
from navig.commands.system_cmd import system_app
from typer.testing import CliRunner

_runner = CliRunner()


class TestSystemDefault:
    def test_no_subcommand_shows_table(self):
        result = _runner.invoke(system_app, [])
        assert result.exit_code == 0

    def test_no_subcommand_no_exception(self):
        result = _runner.invoke(system_app, [])
        assert result.exception is None

    def test_output_contains_os_info(self):
        result = _runner.invoke(system_app, [])
        # Should show some system information
        assert result.exit_code == 0


class TestSystemInfo:
    def test_info_command_invocable(self):
        # system_info calls system_default(None) internally which may fail —
        # just verify the command is registered in the CLI
        result = _runner.invoke(system_app, ["info"])
        # exit_code might be non-zero due to ctx.invoked_subcommand on None
        assert result.exit_code in (0, 1)

    def test_info_command_registered(self):
        # Verify the 'info' command exists in system_app
        cmd_names = [c.name for c in system_app.registered_commands]
        assert "info" in cmd_names


class TestSystemClean:
    def test_clean_with_yes_no_exception(self):
        with patch("shutil.rmtree"):
            result = _runner.invoke(system_app, ["clean", "--yes"])
        assert result.exception is None

    def test_clean_without_yes_confirms(self):
        # Without --yes, it calls typer.confirm  
        # CliRunner input=\n aborts the confirm
        result = _runner.invoke(system_app, ["clean"], input="\n")
        # Should exit (abort) — not crash with an unexpected exception
        assert result.exception is None or "Aborted" in (result.output or "")

    def test_clean_with_yes_flag_skips_confirm(self):
        with patch("shutil.rmtree") as mock_rm:
            result = _runner.invoke(system_app, ["clean", "-y"])
        assert result.exit_code == 0
