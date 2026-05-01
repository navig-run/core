"""Tests for navig/commands/webdash.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from navig.commands.webdash import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help / structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_dashboard():
    result = runner.invoke(app, ["--help"])
    assert "dashboard" in result.output.lower() or "port" in result.output.lower()


# ---------------------------------------------------------------------------
# Default invocation — server not available
# ---------------------------------------------------------------------------

def test_default_shows_starting_message_before_import_attempt():
    with patch("navig.api.server.run_api_server", create=True, side_effect=ImportError):
        result = runner.invoke(app, [])
    assert "dashboard" in result.output.lower() or "Starting" in result.output


def test_default_import_error_exits_1():
    with patch.dict("sys.modules", {"navig.api.server": None}):
        result = runner.invoke(app, [])
    assert result.exit_code == 1


def test_default_import_error_shows_not_available():
    with patch.dict("sys.modules", {"navig.api.server": None}):
        result = runner.invoke(app, [])
    assert "not available" in result.output.lower() or "dashboard" in result.output.lower()


# ---------------------------------------------------------------------------
# Default invocation — server available
# ---------------------------------------------------------------------------

def test_default_calls_run_api_server_when_available():
    mock_server = MagicMock()
    with patch.dict("sys.modules", {"navig.api.server": mock_server}):
        mock_server.run_api_server = MagicMock()
        result = runner.invoke(app, [])
    mock_server.run_api_server.assert_called_once()


def test_default_passes_default_port():
    mock_server = MagicMock()
    with patch.dict("sys.modules", {"navig.api.server": mock_server}):
        mock_server.run_api_server = MagicMock()
        runner.invoke(app, [])
    call_kwargs = mock_server.run_api_server.call_args[1]
    assert call_kwargs.get("port") == 7002 or mock_server.run_api_server.call_args[0]


def test_default_passes_default_host():
    mock_server = MagicMock()
    with patch.dict("sys.modules", {"navig.api.server": mock_server}):
        mock_server.run_api_server = MagicMock()
        runner.invoke(app, [])
    call_kwargs = mock_server.run_api_server.call_args[1]
    assert call_kwargs.get("host") == "127.0.0.1" or mock_server.run_api_server.call_args[0]


# ---------------------------------------------------------------------------
# Custom port / host options
# ---------------------------------------------------------------------------

def test_custom_port_option():
    with patch.dict("sys.modules", {"navig.api.server": None}):
        result = runner.invoke(app, ["--port", "8080"])
    assert result.exit_code in (0, 1)
    assert "8080" in result.output


def test_custom_host_option():
    with patch.dict("sys.modules", {"navig.api.server": None}):
        result = runner.invoke(app, ["--host", "0.0.0.0"])
    assert result.exit_code in (0, 1)
    assert "0.0.0.0" in result.output


def test_short_port_flag():
    with patch.dict("sys.modules", {"navig.api.server": None}):
        result = runner.invoke(app, ["-p", "9000"])
    assert result.exit_code in (0, 1)
    assert "9000" in result.output
