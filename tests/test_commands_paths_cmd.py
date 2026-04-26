"""Tests for navig/commands/paths_cmd.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from pathlib import Path

from navig.commands.paths_cmd import paths_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_default_invocation_exits_0():
    result = runner.invoke(paths_app, [])
    assert result.exit_code == 0


def test_help_exits_0():
    result = runner.invoke(paths_app, ["--help"])
    assert result.exit_code == 0


def test_default_produces_output():
    result = runner.invoke(paths_app, [])
    assert len(result.output) > 0


# ---------------------------------------------------------------------------
# Key names present in output
# ---------------------------------------------------------------------------

def test_output_contains_config_key():
    result = runner.invoke(paths_app, [])
    assert "config" in result.output


def test_output_contains_data_key():
    result = runner.invoke(paths_app, [])
    assert "data" in result.output


def test_output_contains_logs_key():
    result = runner.invoke(paths_app, [])
    assert "logs" in result.output


def test_output_contains_plugins_key():
    result = runner.invoke(paths_app, [])
    assert "plugins" in result.output


def test_output_contains_store_key():
    result = runner.invoke(paths_app, [])
    assert "store" in result.output


def test_output_contains_wiki_key():
    result = runner.invoke(paths_app, [])
    assert "wiki" in result.output


def test_output_contains_space_key():
    result = runner.invoke(paths_app, [])
    assert "space" in result.output


def test_output_contains_packs_key():
    result = runner.invoke(paths_app, [])
    assert "packs" in result.output


# ---------------------------------------------------------------------------
# Path content
# ---------------------------------------------------------------------------

def test_output_contains_navig_dir():
    result = runner.invoke(paths_app, [])
    assert ".navig" in result.output


def test_output_contains_home_segment():
    result = runner.invoke(paths_app, [])
    home = str(Path.home())
    # At least partial home path segment should appear
    home_parts = Path.home().parts
    # Check that some meaningful segment appears
    assert any(p in result.output for p in home_parts if len(p) > 1)


def test_output_contains_existence_marker():
    result = runner.invoke(paths_app, [])
    # Table has ✓ or – for each row
    assert "✓" in result.output or "–" in result.output or "-" in result.output


# ---------------------------------------------------------------------------
# Table structure
# ---------------------------------------------------------------------------

def test_output_is_table_like():
    """Output should contain multiple lines resembling a table."""
    result = runner.invoke(paths_app, [])
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert len(lines) >= 8  # at least one line per path entry


def test_all_eight_keys_present():
    result = runner.invoke(paths_app, [])
    expected_keys = ["config", "data", "logs", "plugins", "store", "wiki", "space", "packs"]
    for key in expected_keys:
        assert key in result.output, f"Expected key '{key}' in output"


# ---------------------------------------------------------------------------
# No subcommand needed — callback is the default action
# ---------------------------------------------------------------------------

def test_no_args_does_not_require_subcommand():
    result = runner.invoke(paths_app, [])
    # Should not say "Missing command" or similar error
    assert "Error" not in result.output or result.exit_code == 0


def test_unrecognized_subcommand_exits_nonzero():
    result = runner.invoke(paths_app, ["nonexistent-sub"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Idempotency / multiple calls
# ---------------------------------------------------------------------------

def test_multiple_invocations_consistent():
    result1 = runner.invoke(paths_app, [])
    result2 = runner.invoke(paths_app, [])
    assert result1.exit_code == result2.exit_code == 0
    assert "config" in result1.output
    assert "config" in result2.output
