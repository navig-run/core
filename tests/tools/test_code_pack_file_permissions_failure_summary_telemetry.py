"""
Batch 60: hermetic unit tests for
  - navig/tools/domains/code_pack.py          (code_sandbox tool registration)
  - navig/core/file_permissions.py            (set_owner_only_file_permissions)
  - navig/core/evolution/failure_summary.py   (summarize_check_failure)
  - navig/commands/telemetry.py               (Typer telemetry commands)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/tools/domains/code_pack.py
# ---------------------------------------------------------------------------

class TestCodePackRegisterTools:
    def test_callable(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        assert callable(register_tools)

    def test_calls_registry_register(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        mock_registry.register.assert_called_once()

    def test_tool_name_is_code_sandbox(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.name == "code_sandbox"

    def test_tool_is_dangerous(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        from navig.tools.router import SafetyLevel
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.safety == SafetyLevel.DANGEROUS

    def test_tool_has_code_tag(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert "code" in meta.tags

    def test_tool_has_module_path(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.module_path == "navig.tools.sandbox"

    def test_parameters_include_code(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert "code" in meta.parameters_schema

    def test_parameters_include_language(self) -> None:
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert "language" in meta.parameters_schema


# ---------------------------------------------------------------------------
# navig/core/file_permissions.py
# ---------------------------------------------------------------------------

class TestSetOwnerOnlyFilePermissions:
    def test_importable(self) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        assert callable(set_owner_only_file_permissions)

    def test_creates_no_exception_on_valid_file(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        # must not raise regardless of platform
        set_owner_only_file_permissions(f)

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        set_owner_only_file_permissions(str(f))  # string, not Path

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        set_owner_only_file_permissions(f)  # Path object

    def test_best_effort_nonexistent_file_no_raise(self) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        # non-posix path that doesn't exist — function is best-effort
        set_owner_only_file_permissions("/nonexistent/path/secret.txt")

    @pytest.mark.skipif(os.name == "nt", reason="Unix chmod only")
    def test_unix_sets_600(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        set_owner_only_file_permissions(f)
        mode = oct(os.stat(f).st_mode)[-3:]
        assert mode == "600"

    @pytest.mark.skipif(os.name != "nt", reason="Windows icacls only")
    def test_windows_calls_icacls(self, tmp_path: Path) -> None:
        from navig.core.file_permissions import set_owner_only_file_permissions
        f = tmp_path / "secret.txt"
        f.write_text("data")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            set_owner_only_file_permissions(f)
        assert mock_run.call_count >= 1


# ---------------------------------------------------------------------------
# navig/core/evolution/failure_summary.py
# ---------------------------------------------------------------------------

class TestSummarizeCheckFailure:
    def test_empty_both(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        assert summarize_check_failure("", "") == ""

    def test_none_both(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        assert summarize_check_failure(None, None) == ""

    def test_detects_failed_test_line(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        out = "FAILED tests/test_foo.py::TestBar::test_baz"
        result = summarize_check_failure(out, "")
        assert "tests/test_foo.py::TestBar::test_baz" in result

    def test_counts_failure_from_pytest_summary(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        out = "5 failed, 3 passed in 1.23s"
        result = summarize_check_failure(out, "")
        assert "5" in result

    def test_includes_top_traceback(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        out = "some output\nE   AssertionError: expected True"
        result = summarize_check_failure(out, "")
        assert "AssertionError" in result

    def test_includes_suggested_next_step(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        result = summarize_check_failure("some error", "")
        assert "next step" in result.lower() or "suggested" in result.lower()

    def test_returns_string(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        result = summarize_check_failure("error output", "")
        assert isinstance(result, str)

    def test_multiple_failures_preview_capped(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        lines = "\n".join(f"FAILED tests/test_{i}.py::T::t" for i in range(10))
        result = summarize_check_failure(lines, "")
        # Only shows 3 in preview
        assert result.count("tests/test_") <= 3

    def test_stderr_included_in_analysis(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        result = summarize_check_failure("", "E   RuntimeError: oops")
        assert "RuntimeError" in result

    def test_fallback_when_no_patterns(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        result = summarize_check_failure("generic failure text", "")
        assert result  # non-empty

    def test_failed_count_from_regex(self) -> None:
        from navig.core.evolution.failure_summary import summarize_check_failure
        out = "3 Failed, 10 passed"
        result = summarize_check_failure(out, "")
        assert "3" in result


# ---------------------------------------------------------------------------
# navig/commands/telemetry.py
# ---------------------------------------------------------------------------

class TestTelemetryApp:
    def test_importable(self) -> None:
        from navig.commands.telemetry import telemetry_app
        assert telemetry_app is not None

    def test_is_typer(self) -> None:
        import typer
        from navig.commands.telemetry import telemetry_app
        assert isinstance(telemetry_app, typer.Typer)

    def test_default_shows_status(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        mock_cfg = MagicMock()
        mock_cfg.get.return_value = False
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, [])
        assert result.exit_code == 0
        assert "Telemetry" in result.output

    def test_enable_sets_config(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        mock_cfg = MagicMock()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, ["enable"])
        mock_cfg.set.assert_called_once_with("telemetry.enabled", True)
        assert result.exit_code == 0

    def test_disable_sets_config(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        mock_cfg = MagicMock()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, ["disable"])
        mock_cfg.set.assert_called_once_with("telemetry.enabled", False)
        assert result.exit_code == 0

    def test_enable_shows_enabled_message(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        mock_cfg = MagicMock()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, ["enable"])
        assert "enabled" in result.output.lower()

    def test_disable_shows_disabled_message(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        mock_cfg = MagicMock()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, ["disable"])
        assert "disabled" in result.output.lower()

    def test_enable_handles_exception(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        with patch("navig.config.ConfigManager", side_effect=RuntimeError("oops")):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, ["enable"])
        # must not crash
        assert result.exit_code == 0

    def test_default_handles_exception(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.telemetry import telemetry_app

        with patch("navig.config.ConfigManager", side_effect=RuntimeError("oops")):
            runner = CliRunner()
            result = runner.invoke(telemetry_app, [])
        assert "unknown" in result.output.lower()
