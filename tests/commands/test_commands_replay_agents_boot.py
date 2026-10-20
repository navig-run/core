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


def test_replay_help_exits_nonzero():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert result.exit_code == 0


def test_replay_help_mentions_list():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert "list" in result.output.lower()


def test_replay_help_mentions_run():
    result = replay_runner.invoke(replay_app, ["--help"])
    assert "run" in result.output.lower()


def test_replay_list_exits_nonzero():
    with patch(_WARN):
        result = replay_runner.invoke(replay_app, ["list"])
    assert result.exit_code == 1


def test_replay_list_calls_warn():
    with patch(_WARN) as mock_warn:
        replay_runner.invoke(replay_app, ["list"])
    mock_warn.assert_called_once()


def test_replay_list_not_implemented():
    with patch(_WARN) as mock_warn:
        replay_runner.invoke(replay_app, ["list"])
    assert "not implemented" in mock_warn.call_args[0][0]


def test_replay_run_with_session_exits_nonzero():
    with patch(_WARN):
        result = replay_runner.invoke(replay_app, ["run", "session-123"])
    assert result.exit_code == 1


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
    assert result.exit_code == 1


# ===========================================================================
# agents.py — superseded: `navig formation agents` / `navig agent run`
# ===========================================================================

from navig.commands.agents import app as agents_app

agents_runner = CliRunner()


def test_agents_help_exits_0():
    result = agents_runner.invoke(agents_app, ["--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["list"], "navig formation agents"),
        (["run", "myagent", "do it"], "navig agent run"),
        (["run"], "navig agent run"),
        ([], "navig formation agents"),
    ],
)
def test_agents_verbs_point_at_the_real_command_and_exit_1(argv, target):
    result = agents_runner.invoke(agents_app, argv)
    assert result.exit_code == 1, result.output
    assert target in result.output


# ===========================================================================
# boot_cmd.py — superseded: NAVIG starts as the background service
# ===========================================================================

from navig.commands.boot_cmd import boot_app

boot_runner = CliRunner()


def test_boot_help_exits_0():
    result = boot_runner.invoke(boot_app, ["--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["show"], "navig service status"),
        (["run"], "navig service start"),
        (["run", "--dry-run"], "navig service start"),
        ([], "navig service status"),
    ],
)
def test_boot_verbs_point_at_the_service_and_exit_1(argv, target):
    result = boot_runner.invoke(boot_app, argv)
    assert result.exit_code == 1, result.output
    assert target in result.output
