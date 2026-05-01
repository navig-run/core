"""Tests for navig/commands/blueprint.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

from navig.commands.blueprint import blueprint_app

runner = CliRunner()

_WARN = "navig.console_helper.warn"


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(blueprint_app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_list():
    result = runner.invoke(blueprint_app, ["--help"])
    assert "list" in result.output.lower()


def test_help_mentions_apply():
    result = runner.invoke(blueprint_app, ["--help"])
    assert "apply" in result.output.lower()


def test_no_args_exits_nonzero_or_help():
    result = runner.invoke(blueprint_app, [])
    assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# blueprint list
# ---------------------------------------------------------------------------

def test_list_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(blueprint_app, ["list"])
    assert result.exit_code == 0


def test_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["list"])
    mock_warn.assert_called_once()


def test_list_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_list_help_exits_0():
    result = runner.invoke(blueprint_app, ["list", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# blueprint apply
# ---------------------------------------------------------------------------

def test_apply_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(blueprint_app, ["apply", "myblueprint"])
    assert result.exit_code == 0


def test_apply_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["apply", "mybp"])
    mock_warn.assert_called_once()


def test_apply_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["apply", "mybp"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_apply_missing_name_exits_nonzero():
    result = runner.invoke(blueprint_app, ["apply"])
    assert result.exit_code != 0


def test_apply_with_target_option():
    with patch(_WARN, create=True) as mock_warn:
        result = runner.invoke(blueprint_app, ["apply", "mybp", "--target", "/tmp"])
    assert result.exit_code == 0


def test_apply_help_exits_0():
    result = runner.invoke(blueprint_app, ["apply", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Cross-command isolation
# ---------------------------------------------------------------------------

def test_list_warn_called_once():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["list"])
    assert mock_warn.call_count == 1


def test_apply_warn_called_once():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(blueprint_app, ["apply", "bp"])
    assert mock_warn.call_count == 1
