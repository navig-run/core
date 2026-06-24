"""Tests for navig/commands/eval.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from navig.commands.eval import app

runner = CliRunner()

# NOTE: Typer auto-promotes a single named command to the root level.
# Invoke directly with the expression arg (no "run" prefix needed).


# ---------------------------------------------------------------------------
# Help / structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_expression():
    result = runner.invoke(app, ["--help"])
    assert "expression" in result.output.lower() or "EXPRESSION" in result.output


# ---------------------------------------------------------------------------
# Successful evaluation
# ---------------------------------------------------------------------------

def test_run_simple_addition():
    result = runner.invoke(app, ["1+1"])
    assert result.exit_code == 0


def test_run_simple_addition_output():
    result = runner.invoke(app, ["1+1"])
    assert "2" in result.output


def test_run_string_expression():
    result = runner.invoke(app, ["'hello'"])
    assert result.exit_code == 0
    assert "hello" in result.output


def test_run_numeric_expression():
    result = runner.invoke(app, ["10*5"])
    assert "50" in result.output


def test_run_boolean_expression():
    result = runner.invoke(app, ["True"])
    assert result.exit_code == 0


def test_run_exits_0_on_success():
    result = runner.invoke(app, ["42"])
    assert result.exit_code == 0


def test_run_numeric_literal_output():
    result = runner.invoke(app, ["99"])
    assert "99" in result.output


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_run_division_by_zero_exits_nonzero():
    result = runner.invoke(app, ["1/0"])
    assert result.exit_code != 0


def test_run_division_by_zero_shows_error():
    result = runner.invoke(app, ["1/0"])
    assert "error" in result.output.lower() or "Error" in result.output


def test_run_missing_expression_exits_nonzero():
    result = runner.invoke(app, [])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Config context integration
# ---------------------------------------------------------------------------

def test_run_with_config_available():
    with patch("navig.config.ConfigManager") as mock_cfg_cls:
        mock_cfg_cls.return_value = MagicMock()
        result = runner.invoke(app, ["42"])
    assert result.exit_code == 0


def test_run_config_unavailable_still_works():
    with patch("navig.config.ConfigManager", side_effect=ImportError("no config")):
        result = runner.invoke(app, ["1+1"])
    assert result.exit_code == 0
    assert "2" in result.output

