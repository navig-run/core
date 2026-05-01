"""Tests for navig/commands/radar.py, watch_cmd.py, and deck.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

_WARN = "navig.console_helper.warn"


# ===========================================================================
# radar.py
# ===========================================================================

from navig.commands.radar import radar_app

radar_runner = CliRunner()


def test_radar_help_exits_0():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert result.exit_code == 0


def test_radar_help_mentions_list():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert "list" in result.output.lower()


def test_radar_help_mentions_add():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert "add" in result.output.lower()


def test_radar_list_exits_0():
    with patch(_WARN, create=True):
        result = radar_runner.invoke(radar_app, ["list"])
    assert result.exit_code == 0


def test_radar_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        radar_runner.invoke(radar_app, ["list"])
    mock_warn.assert_called_once()


def test_radar_list_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        radar_runner.invoke(radar_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_radar_add_exits_0():
    with patch(_WARN, create=True):
        result = radar_runner.invoke(radar_app, ["add", "error"])
    assert result.exit_code == 0


def test_radar_add_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        radar_runner.invoke(radar_app, ["add", "keyword"])
    mock_warn.assert_called_once()


def test_radar_add_missing_keyword_exits_nonzero():
    result = radar_runner.invoke(radar_app, ["add"])
    assert result.exit_code != 0


# ===========================================================================
# watch_cmd.py
# ===========================================================================

from navig.commands.watch_cmd import watch_app

watch_runner = CliRunner()


def test_watch_help_exits_0():
    result = watch_runner.invoke(watch_app, ["--help"])
    assert result.exit_code == 0


def test_watch_start_exits_0():
    with patch(_WARN, create=True):
        result = watch_runner.invoke(watch_app, ["start"])
    assert result.exit_code == 0


def test_watch_start_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        watch_runner.invoke(watch_app, ["start"])
    mock_warn.assert_called_once()


def test_watch_start_with_path():
    with patch(_WARN, create=True):
        result = watch_runner.invoke(watch_app, ["start", "/tmp"])
    assert result.exit_code == 0


def test_watch_list_exits_0():
    with patch(_WARN, create=True):
        result = watch_runner.invoke(watch_app, ["list"])
    assert result.exit_code == 0


def test_watch_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        watch_runner.invoke(watch_app, ["list"])
    mock_warn.assert_called_once()


def test_watch_list_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        watch_runner.invoke(watch_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


# ===========================================================================
# deck.py
# ===========================================================================

from navig.commands.deck import deck_app

deck_runner = CliRunner()


def test_deck_help_exits_0():
    result = deck_runner.invoke(deck_app, ["--help"])
    assert result.exit_code == 0


def test_deck_help_mentions_list():
    result = deck_runner.invoke(deck_app, ["--help"])
    assert "list" in result.output.lower()


def test_deck_help_mentions_new():
    result = deck_runner.invoke(deck_app, ["--help"])
    assert "new" in result.output.lower()


def test_deck_list_exits_0():
    with patch(_WARN, create=True):
        result = deck_runner.invoke(deck_app, ["list"])
    assert result.exit_code == 0


def test_deck_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        deck_runner.invoke(deck_app, ["list"])
    mock_warn.assert_called_once()


def test_deck_list_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        deck_runner.invoke(deck_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_deck_new_exits_0():
    with patch(_WARN, create=True):
        result = deck_runner.invoke(deck_app, ["new", "my-deck"])
    assert result.exit_code == 0


def test_deck_new_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        deck_runner.invoke(deck_app, ["new", "my-deck"])
    mock_warn.assert_called_once()


def test_deck_new_missing_name_exits_nonzero():
    result = deck_runner.invoke(deck_app, ["new"])
    assert result.exit_code != 0
