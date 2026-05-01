"""Tests for navig/commands/node.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

from navig.commands.node import node_app

runner = CliRunner()

_WARN = "navig.console_helper.warn"


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(node_app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_list():
    result = runner.invoke(node_app, ["--help"])
    assert "list" in result.output.lower()


def test_help_mentions_add():
    result = runner.invoke(node_app, ["--help"])
    assert "add" in result.output.lower()


def test_help_mentions_remove():
    result = runner.invoke(node_app, ["--help"])
    assert "remove" in result.output.lower()


def test_no_args_shows_help_or_error():
    result = runner.invoke(node_app, [])
    assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# node list
# ---------------------------------------------------------------------------

def test_list_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(node_app, ["list"])
    assert result.exit_code == 0


def test_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["list"])
    mock_warn.assert_called_once()


def test_list_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_list_help_exits_0():
    result = runner.invoke(node_app, ["list", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# node add
# ---------------------------------------------------------------------------

def test_add_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(node_app, ["add", "192.168.1.10:9000"])
    assert result.exit_code == 0


def test_add_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["add", "10.0.0.1:8080"])
    mock_warn.assert_called_once()


def test_add_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["add", "host:port"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_add_missing_address_exits_nonzero():
    result = runner.invoke(node_app, ["add"])
    assert result.exit_code != 0


def test_add_help_exits_0():
    result = runner.invoke(node_app, ["add", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# node remove
# ---------------------------------------------------------------------------

def test_remove_exits_0():
    with patch(_WARN, create=True):
        result = runner.invoke(node_app, ["remove", "node-01"])
    assert result.exit_code == 0


def test_remove_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["remove", "node-01"])
    mock_warn.assert_called_once()


def test_remove_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        runner.invoke(node_app, ["remove", "mynode"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_remove_missing_name_exits_nonzero():
    result = runner.invoke(node_app, ["remove"])
    assert result.exit_code != 0


def test_remove_help_exits_0():
    result = runner.invoke(node_app, ["remove", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Cross-command isolation
# ---------------------------------------------------------------------------

def test_all_three_commands_have_distinct_messages():
    messages = []
    for cmd, args in [("list", []), ("add", ["host:1"]), ("remove", ["n1"])]:
        with patch(_WARN, create=True) as mock_warn:
            runner.invoke(node_app, [cmd] + args)
        messages.append(mock_warn.call_args[0][0])
    assert len(set(messages)) == 3
