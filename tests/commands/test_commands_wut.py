"""Tests for navig/commands/wut.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from navig.commands.wut import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_default_exits_0():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": default
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert result.exit_code == 0


def test_default_produces_output():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": default
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert len(result.output) > 0


def test_help_exits_0():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_context():
    result = runner.invoke(app, ["--help"])
    assert "context" in result.output.lower() or "snapshot" in result.output.lower() or "wut" in result.output.lower()


# ---------------------------------------------------------------------------
# Output content
# ---------------------------------------------------------------------------

def test_output_shows_wut():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = "testhost"
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert "wut" in result.output.lower()


def test_output_shows_host():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": "my-host" if key == "active_host" else "my-app"
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert "my-host" in result.output


def test_output_shows_app():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": "prod-app" if key == "active_app" else "(none)"
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert "prod-app" in result.output


def test_output_shows_host_label():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": default
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert "host" in result.output.lower()


def test_output_shows_app_label():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": default
        mock_cfg_cls.return_value = mock_cfg
        result = runner.invoke(app, [])
    assert "app" in result.output.lower()


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_config_exception_still_exits_0():
    with patch("navig.config.ConfigManager", side_effect=RuntimeError("db error")):
        result = runner.invoke(app, [])
    assert result.exit_code == 0


def test_config_exception_shows_unavailable_message():
    with patch("navig.config.ConfigManager", side_effect=RuntimeError("db error")):
        result = runner.invoke(app, [])
    assert "unavailable" in result.output.lower() or "error" in result.output.lower() or "db error" in result.output


# ---------------------------------------------------------------------------
# No subcommands
# ---------------------------------------------------------------------------

def test_unrecognized_subcommand_exits_nonzero():
    result = runner.invoke(app, ["unknown-sub"])
    assert result.exit_code != 0
