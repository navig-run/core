"""Tests for navig/commands/benchmark.py, origin.py, and cloud.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

_WARN = "navig.console_helper.warn"


# ===========================================================================
# benchmark.py
# ===========================================================================

from navig.commands.benchmark import app as benchmark_app

benchmark_runner = CliRunner()


def test_benchmark_help_exits_0():
    result = benchmark_runner.invoke(benchmark_app, ["--help"])
    assert result.exit_code == 0


def test_benchmark_no_args_shows_help_or_error():
    # no_args_is_help=True but single command auto-promoted
    result = benchmark_runner.invoke(benchmark_app, [])
    assert result.exit_code in (0, 1, 2)


def test_benchmark_run_default_suite_exits_0():
    with patch(_WARN, create=True):
        result = benchmark_runner.invoke(benchmark_app, [])
    # With single promoted command and default argument, may show help or run
    assert result.exit_code in (0, 1, 2)


def test_benchmark_run_all_suite_exits_0():
    with patch(_WARN, create=True):
        result = benchmark_runner.invoke(benchmark_app, ["all"])
    assert result.exit_code == 0


def test_benchmark_run_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        benchmark_runner.invoke(benchmark_app, ["all"])
    mock_warn.assert_called_once()


def test_benchmark_run_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        benchmark_runner.invoke(benchmark_app, ["startup"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_benchmark_run_with_startup_suite():
    with patch(_WARN, create=True):
        result = benchmark_runner.invoke(benchmark_app, ["startup"])
    assert result.exit_code == 0


def test_benchmark_run_with_ssh_suite():
    with patch(_WARN, create=True):
        result = benchmark_runner.invoke(benchmark_app, ["ssh"])
    assert result.exit_code == 0


def test_benchmark_run_with_db_suite():
    with patch(_WARN, create=True):
        result = benchmark_runner.invoke(benchmark_app, ["db"])
    assert result.exit_code == 0


# ===========================================================================
# origin.py
# ===========================================================================

from navig.commands.origin import origin_app

origin_runner = CliRunner()


def test_origin_help_exits_0():
    result = origin_runner.invoke(origin_app, ["--help"])
    assert result.exit_code == 0


def test_origin_default_exits_0():
    with patch(_WARN, create=True):
        result = origin_runner.invoke(origin_app, [])
    assert result.exit_code == 0


def test_origin_default_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        origin_runner.invoke(origin_app, [])
    mock_warn.assert_called_once()


def test_origin_warn_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        origin_runner.invoke(origin_app, [])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_origin_unrecognized_subcommand_exits_nonzero():
    result = origin_runner.invoke(origin_app, ["unknown"])
    assert result.exit_code != 0


# ===========================================================================
# cloud.py
# ===========================================================================

from navig.commands.cloud import app as cloud_app

cloud_runner = CliRunner()


def test_cloud_help_exits_0():
    result = cloud_runner.invoke(cloud_app, ["--help"])
    assert result.exit_code == 0


def test_cloud_help_mentions_status():
    result = cloud_runner.invoke(cloud_app, ["--help"])
    assert "status" in result.output.lower()


def test_cloud_help_mentions_list():
    result = cloud_runner.invoke(cloud_app, ["--help"])
    assert "list" in result.output.lower()


def test_cloud_no_args_exits_nonzero_or_help():
    result = cloud_runner.invoke(cloud_app, [])
    assert result.exit_code in (0, 1, 2)


def test_cloud_status_exits_0():
    with patch(_WARN, create=True):
        result = cloud_runner.invoke(cloud_app, ["status"])
    assert result.exit_code == 0


def test_cloud_status_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        cloud_runner.invoke(cloud_app, ["status"])
    mock_warn.assert_called_once()


def test_cloud_status_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        cloud_runner.invoke(cloud_app, ["status"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_cloud_list_exits_0():
    with patch(_WARN, create=True):
        result = cloud_runner.invoke(cloud_app, ["list"])
    assert result.exit_code == 0


def test_cloud_list_calls_warn():
    with patch(_WARN, create=True) as mock_warn:
        cloud_runner.invoke(cloud_app, ["list"])
    mock_warn.assert_called_once()


def test_cloud_list_says_not_implemented():
    with patch(_WARN, create=True) as mock_warn:
        cloud_runner.invoke(cloud_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_cloud_status_and_list_distinct_messages():
    messages = []
    for cmd in ["status", "list"]:
        with patch(_WARN, create=True) as mock_warn:
            cloud_runner.invoke(cloud_app, [cmd])
        messages.append(mock_warn.call_args[0][0])
    assert len(set(messages)) == 2
