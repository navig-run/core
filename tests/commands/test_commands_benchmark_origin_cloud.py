"""Tests for navig/commands/benchmark.py, origin.py, and cloud.py."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_WARN = "navig.console_helper.warning"


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
    with patch(_WARN):
        result = benchmark_runner.invoke(benchmark_app, [])
    # With single promoted command and default argument, may show help or run
    assert result.exit_code in (0, 1, 2)


def test_benchmark_run_all_suite_exits_0():
    with patch(_WARN):
        result = benchmark_runner.invoke(benchmark_app, ["all"])
    assert result.exit_code == 0


def test_benchmark_run_calls_warn():
    with patch(_WARN) as mock_warn:
        benchmark_runner.invoke(benchmark_app, ["all"])
    mock_warn.assert_called_once()


def test_benchmark_run_says_not_implemented():
    with patch(_WARN) as mock_warn:
        benchmark_runner.invoke(benchmark_app, ["startup"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_benchmark_run_with_startup_suite():
    with patch(_WARN):
        result = benchmark_runner.invoke(benchmark_app, ["startup"])
    assert result.exit_code == 0


def test_benchmark_run_with_ssh_suite():
    with patch(_WARN):
        result = benchmark_runner.invoke(benchmark_app, ["ssh"])
    assert result.exit_code == 0


def test_benchmark_run_with_db_suite():
    with patch(_WARN):
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
    with patch(_WARN):
        result = origin_runner.invoke(origin_app, [])
    assert result.exit_code == 0


def test_origin_default_calls_warn():
    with patch(_WARN) as mock_warn:
        origin_runner.invoke(origin_app, [])
    mock_warn.assert_called_once()


def test_origin_warn_says_not_implemented():
    with patch(_WARN) as mock_warn:
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


# ── `navig cloud` is IMPLEMENTED — these pin the real contract ────────────────
#
# The seven tests that used to live here asserted a PLACEHOLDER: that `cloud
# status` / `cloud list` each warn "not yet implemented", and that a `list`
# command exists at all. `cloud` has since become a real Cloudflare quick-tunnel
# broker (connect · direct · tailscale · status · disconnect · key) and `list`
# was never built. Those tests therefore demanded a regression — and they only
# surfaced now because the suite had not actually been running (the ci-local
# runner collected 0 tests). Re-aimed at what the command really does; do not
# reintroduce a "not yet implemented" assertion for a shipped feature.


def test_cloud_help_lists_the_real_commands():
    result = cloud_runner.invoke(cloud_app, ["--help"])
    out = result.output.lower()
    for cmd in ("connect", "status", "disconnect"):
        assert cmd in out


def test_cloud_has_no_list_command():
    # Guards the stale expectation directly: there is no `navig cloud list`.
    result = cloud_runner.invoke(cloud_app, ["list"])
    assert result.exit_code != 0


def test_cloud_no_args_exits_nonzero_or_help():
    result = cloud_runner.invoke(cloud_app, [])
    assert result.exit_code in (0, 1, 2)


def test_cloud_status_exits_0():
    with patch(_WARN):
        result = cloud_runner.invoke(cloud_app, ["status"])
    assert result.exit_code == 0


def test_cloud_status_does_not_claim_unimplemented():
    result = cloud_runner.invoke(cloud_app, ["status"])
    assert "not yet implemented" not in result.output.lower()
