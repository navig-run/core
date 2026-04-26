"""Tests for navig/commands/portable.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

from navig.commands.portable import portable_app

runner = CliRunner()

_WARN = "navig.console_helper.warn"


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(portable_app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_create():
    result = runner.invoke(portable_app, ["--help"])
    assert "create" in result.output.lower()


def test_help_mentions_validate():
    result = runner.invoke(portable_app, ["--help"])
    assert "validate" in result.output.lower()


def test_no_args_exits_nonzero_or_help():
    result = runner.invoke(portable_app, [])
    assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# portable create
# ---------------------------------------------------------------------------

def test_create_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(portable_app, ["create"])
    assert result.exit_code == 0


def test_create_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["create"])
    mock_warn.assert_called_once()


def test_create_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["create"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_create_with_custom_output_name():
    with patch(_WARN, create=True) as mock_warn:
        result = runner.invoke(portable_app, ["create", "my-bundle"])
    assert result.exit_code == 0
    mock_warn.assert_called_once()


def test_create_warn_called_once():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["create"])
    assert mock_warn.call_count == 1


def test_create_help_exits_0():
    result = runner.invoke(portable_app, ["create", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# portable validate
# ---------------------------------------------------------------------------

def test_validate_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(portable_app, ["validate"])
    assert result.exit_code == 0


def test_validate_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["validate"])
    mock_warn.assert_called_once()


def test_validate_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["validate"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_validate_with_path_argument():
    with patch(_WARN, create=True) as mock_warn:
        result = runner.invoke(portable_app, ["validate", "/tmp/bundle"])
    assert result.exit_code == 0
    mock_warn.assert_called_once()


def test_validate_warn_called_once():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(portable_app, ["validate"])
    assert mock_warn.call_count == 1


def test_validate_help_exits_0():
    result = runner.invoke(portable_app, ["validate", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Cross-command isolation
# ---------------------------------------------------------------------------

def test_create_and_validate_distinct_messages():
    messages = []
    for cmd in [["create"], ["validate"]]:
        with patch(_WARN, create=True) as mock_warn:
            runner.invoke(portable_app, cmd)
        messages.append(mock_warn.call_args[0][0])
    assert len(set(messages)) == 2
