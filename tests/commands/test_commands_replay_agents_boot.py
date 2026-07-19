"""Tests for navig/commands/replay.py, agents.py, and boot_cmd.py."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_WARN = "navig.console_helper.warning"


# ===========================================================================
# replay.py
# ===========================================================================

from navig.commands.replay import app as replay_app

replay_runner = CliRunner()


def test_replay_help_exits_0():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert result.exit_code == 0


def test_replay_help_mentions_list():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert "list" in result.output.lower()


def test_replay_help_mentions_run():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert "run" in result.output.lower()


def test_replay_list_exits_0():
    with patch(_WARN):
        result = replay_runner.invoke(replay_app, ["list"])
    assert result.exit_code == 0


def test_replay_list_calls_warn():
    with patch(_WARN) as mock_warn:
        replay_runner.invoke(replay_app, ["list"])
    mock_warn.assert_called_once()


def test_replay_list_not_implemented():
    with patch(_WARN) as mock_warn:
        replay_runner.invoke(replay_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_replay_run_with_session_exits_0():
    with patch(_WARN):
        result = replay_runner.invoke(replay_app, ["run", "session-123"])
    assert result.exit_code == 0


def test_replay_run_calls_warn():
    with patch(_WARN) as mock_warn:
        replay_runner.invoke(replay_app, ["run", "sess-1"])
    mock_warn.assert_called_once()


def test_replay_run_missing_session_exits_nonzero():
    result = replay_runner.invoke(replay_app, ["run"])
    assert result.exit_code != 0


def test_replay_run_with_speed_option():
    with patch(_WARN):
        result = replay_runner.invoke(replay_app, ["run", "sess-1", "--speed", "2.0"])
    assert result.exit_code == 0


# ===========================================================================
# agents.py
# ===========================================================================

from navig.commands.agents import app as agents_app

agents_runner = CliRunner()


def test_agents_help_exits_0():
    result = agents_runner.invoke(agents_app, ["--help"])
    assert result.exit_code == 0


def test_agents_help_mentions_list():
    result = agents_runner.invoke(agents_app, ["--help"])
    assert "list" in result.output.lower()


# `navig.agents` has no `list_agents` — the command's `from navig.agents import
# list_agents` raises ImportError for real, which IS the fallback these tests mean to
# exercise. They used to fabricate the symbol with `create=True` just to make it raise;
# that mocked away the very condition under test. Let the real import fail.


def test_agents_list_exits_0():
    with patch(_WARN):
        result = agents_runner.invoke(agents_app, ["list"])
    assert result.exit_code == 0


def test_agents_list_fallback_warns_when_not_implemented():
    with patch(_WARN) as mock_warn:
        agents_runner.invoke(agents_app, ["list"])
    mock_warn.assert_called_once()
    assert "not yet" in mock_warn.call_args[0][0].lower()


def test_agents_run_with_name_exits_0():
    with patch(_WARN):
        result = agents_runner.invoke(agents_app, ["run", "myagent"])
    assert result.exit_code == 0


def test_agents_run_calls_warn():
    with patch(_WARN) as mock_warn:
        agents_runner.invoke(agents_app, ["run", "myagent"])
    mock_warn.assert_called_once()


def test_agents_run_not_implemented():
    with patch(_WARN) as mock_warn:
        agents_runner.invoke(agents_app, ["run", "myagent"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_agents_run_missing_name_exits_nonzero():
    result = agents_runner.invoke(agents_app, ["run"])
    assert result.exit_code != 0


# ===========================================================================
# boot_cmd.py
# ===========================================================================

from navig.commands.boot_cmd import boot_app

boot_runner = CliRunner()


def test_boot_help_exits_0():
    result = boot_runner.invoke(boot_app, ["--help"])
    assert result.exit_code == 0


def test_boot_help_mentions_show():
    result = boot_runner.invoke(boot_app, ["--help"])
    assert "show" in result.output.lower()


def test_boot_help_mentions_run():
    result = boot_runner.invoke(boot_app, ["--help"])
    assert "run" in result.output.lower()


def test_boot_show_exits_0():
    with patch(_WARN):
        result = boot_runner.invoke(boot_app, ["show"])
    assert result.exit_code == 0


def test_boot_show_calls_warn():
    with patch(_WARN) as mock_warn:
        boot_runner.invoke(boot_app, ["show"])
    mock_warn.assert_called_once()


def test_boot_show_not_implemented():
    with patch(_WARN) as mock_warn:
        boot_runner.invoke(boot_app, ["show"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_boot_run_exits_0():
    with patch(_WARN):
        result = boot_runner.invoke(boot_app, ["run"])
    assert result.exit_code == 0


def test_boot_run_calls_warn():
    with patch(_WARN) as mock_warn:
        boot_runner.invoke(boot_app, ["run"])
    mock_warn.assert_called_once()


def test_boot_run_with_dry_run_flag():
    with patch(_WARN):
        result = boot_runner.invoke(boot_app, ["run", "--dry-run"])
    assert result.exit_code == 0
