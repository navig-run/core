"""Tests for navig/commands/explain.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from navig.commands.explain import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

def test_app_has_command_subcommand():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "command" in result.output.lower()


def test_app_has_config_subcommand():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output.lower()


def test_app_has_concept_subcommand():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "concept" in result.output.lower()


def test_help_exits_0():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_no_args_exits_nonzero_or_shows_help():
    # no_args_is_help=True means bare invocation shows help (exit 0) or nonzero
    result = runner.invoke(app, [])
    # Either prints help (exit 0) or shows error — just no crash
    assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# explain command <name>
# ---------------------------------------------------------------------------

_WARN = "navig.console_helper.warn"
_CREATE = {"create": True}


def test_explain_command_exits_0():
    with patch(_WARN, **_CREATE):
        result = runner.invoke(app, ["command", "mycommand"])
    assert result.exit_code == 0


def test_explain_command_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["command", "mycommand"])
    mock_warn.assert_called_once()


def test_explain_command_warn_includes_name():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["command", "my-special-cmd"])
    call_arg = mock_warn.call_args[0][0]
    assert "my-special-cmd" in call_arg


def test_explain_command_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["command", "test"])
    call_arg = mock_warn.call_args[0][0]
    assert "not yet implemented" in call_arg


def test_explain_command_missing_arg_exits_nonzero():
    result = runner.invoke(app, ["command"])
    assert result.exit_code != 0


def test_explain_command_help_exits_0():
    result = runner.invoke(app, ["command", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# explain config <key>
# ---------------------------------------------------------------------------

def test_explain_config_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(app, ["config", "MY_KEY"])
    assert result.exit_code == 0


def test_explain_config_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["config", "MY_KEY"])
    mock_warn.assert_called_once()


def test_explain_config_warn_includes_key():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["config", "database.host"])
    call_arg = mock_warn.call_args[0][0]
    assert "database.host" in call_arg


def test_explain_config_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["config", "timeout"])
    call_arg = mock_warn.call_args[0][0]
    assert "not yet implemented" in call_arg


def test_explain_config_missing_arg_exits_nonzero():
    result = runner.invoke(app, ["config"])
    assert result.exit_code != 0


def test_explain_config_help_exits_0():
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# explain concept <topic>
# ---------------------------------------------------------------------------

def test_explain_concept_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(app, ["concept", "vault"])
    assert result.exit_code == 0


def test_explain_concept_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["concept", "vault"])
    mock_warn.assert_called_once()


def test_explain_concept_warn_includes_topic():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["concept", "identity-sigil"])
    call_arg = mock_warn.call_args[0][0]
    assert "identity-sigil" in call_arg


def test_explain_concept_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["concept", "agents"])
    call_arg = mock_warn.call_args[0][0]
    assert "not yet implemented" in call_arg


def test_explain_concept_missing_arg_exits_nonzero():
    result = runner.invoke(app, ["concept"])
    assert result.exit_code != 0


def test_explain_concept_help_exits_0():
    result = runner.invoke(app, ["concept", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Cross-command isolation
# ---------------------------------------------------------------------------

def test_explain_command_warn_called_exactly_once():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(app, ["command", "ls"])
    assert mock_warn.call_count == 1


def test_each_subcommand_has_distinct_output_message():
    messages = []
    for sub, arg in [("command", "ls"), ("config", "timeout"), ("concept", "tunnel")]:
        with patch(_WARN, create=True) as mock_warn:
            runner.invoke(app, [sub, arg])
        messages.append(mock_warn.call_args[0][0])
    assert len(set(messages)) == 3
