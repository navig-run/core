"""Tests for navig.commands.log — log_app CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from navig.commands.log import log_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

class TestLogAppStructure:
    def test_log_app_is_typer(self):
        assert isinstance(log_app, typer.Typer)

    def test_help_shows_log(self):
        result = runner.invoke(log_app, ["--help"])
        assert result.exit_code == 0
        assert "log" in result.output.lower() or "show" in result.output.lower()

    def test_show_subcommand_available(self):
        result = runner.invoke(log_app, ["show", "--help"])
        assert result.exit_code == 0

    def test_run_subcommand_available(self):
        result = runner.invoke(log_app, ["run", "--help"])
        assert result.exit_code == 0

    def test_show_help_mentions_service(self):
        result = runner.invoke(log_app, ["show", "--help"])
        assert "service" in result.output.lower()

    def test_show_help_mentions_lines(self):
        result = runner.invoke(log_app, ["show", "--help"])
        assert "--lines" in result.output or "-n" in result.output

    def test_show_help_mentions_tail(self):
        result = runner.invoke(log_app, ["show", "--help"])
        assert "--tail" in result.output or "-f" in result.output

    def test_run_help_mentions_rotate(self):
        result = runner.invoke(log_app, ["run", "--help"])
        assert "--rotate" in result.output


# ---------------------------------------------------------------------------
# log_show — service path (non-container)
# ---------------------------------------------------------------------------

class TestLogShow:
    def test_show_service_logs_called(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            result = runner.invoke(log_app, ["show", "nginx"], obj={})
        mock_view.assert_called_once()

    def test_show_passes_service_name(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "mysql"], obj={})
        args = mock_view.call_args[0]
        assert args[0] == "mysql"

    def test_show_default_lines_is_50(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "nginx"], obj={})
        args = mock_view.call_args[0]
        assert args[2] == 50  # lines

    def test_show_custom_lines(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "nginx", "--lines", "100"], obj={})
        args = mock_view.call_args[0]
        assert args[2] == 100

    def test_show_tail_off_by_default(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "nginx"], obj={})
        args = mock_view.call_args[0]
        assert args[1] is False  # tail

    def test_show_tail_on_with_flag(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "nginx", "--tail"], obj={})
        args = mock_view.call_args[0]
        assert args[1] is True

    def test_show_short_n_flag(self):
        with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
            runner.invoke(log_app, ["show", "nginx", "-n", "200"], obj={})
        args = mock_view.call_args[0]
        assert args[2] == 200


# ---------------------------------------------------------------------------
# log_show — container path
# ---------------------------------------------------------------------------

class TestLogShowContainer:
    def test_show_with_container_calls_docker_logs(self):
        with patch("navig.commands.docker.docker_logs") as mock_docker:
            runner.invoke(log_app, ["show", "myservice", "--container", "app"], obj={})
        mock_docker.assert_called_once()

    def test_show_with_container_not_calling_view_service_logs(self):
        with patch("navig.commands.docker.docker_logs") as mock_docker:
            with patch("navig.commands.monitoring.view_service_logs", create=True) as mock_view:
                runner.invoke(log_app, ["show", "s", "--container", "c"], obj={})
        mock_view.assert_not_called()
        mock_docker.assert_called_once()

    def test_show_container_short_c_flag(self):
        with patch("navig.commands.docker.docker_logs") as mock_docker:
            runner.invoke(log_app, ["show", "myservice", "-c", "mycontainer"], obj={})
        mock_docker.assert_called_once()


# ---------------------------------------------------------------------------
# log_run
# ---------------------------------------------------------------------------

class TestLogRun:
    def test_run_without_rotate_shows_error(self):
        with patch("navig.commands.log.ch.error") as mock_error:
            result = runner.invoke(log_app, ["run"], obj={})
        mock_error.assert_called_once()

    def test_run_with_rotate_calls_rotate_logs(self):
        with patch("navig.commands.maintenance.rotate_logs") as mock_rotate:
            result = runner.invoke(log_app, ["run", "--rotate"], obj={})
        mock_rotate.assert_called_once()

    def test_run_without_rotate_does_not_call_rotate_logs(self):
        with patch("navig.commands.maintenance.rotate_logs") as mock_rotate:
            with patch("navig.commands.log.ch.error"):
                runner.invoke(log_app, ["run"], obj={})
        mock_rotate.assert_not_called()
