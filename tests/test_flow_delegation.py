"""
Tests for flow command delegation.

Verifies that `navig flow` properly delegates to the workflow command group.
"""

import pytest
from typer.testing import CliRunner

from navig.cli import app

runner = CliRunner()


class TestFlowDelegation:
    """Tests for flow → workflow command delegation."""

    def test_flow_help_exits_zero(self):
        """navig flow --help should exit 0."""
        result = runner.invoke(app, ["flow", "--help"])
        assert result.exit_code == 0
        assert "Manage and execute reusable command flows" in result.output

    def test_flow_list_exits_zero(self):
        """navig flow list should exit 0."""
        result = runner.invoke(app, ["flow", "list"])
        assert result.exit_code == 0
        # Should show a table with workflows
        assert "Name" in result.output or "No flows" in result.output.lower()

    def test_flow_has_expected_subcommands(self):
        """navig flow should expose list, show, run, test, add, template."""
        result = runner.invoke(app, ["flow", "--help"])
        assert result.exit_code == 0
        for cmd in ["list", "show", "run", "test", "add", "template"]:
            assert cmd in result.output, f"Missing subcommand: {cmd}"

    def test_flow_run_missing_name_errors(self):
        """navig flow run without a name should error."""
        result = runner.invoke(app, ["flow", "run"])
        # Should fail with missing argument
        assert result.exit_code != 0

    def test_flow_show_missing_name_errors(self):
        """navig flow show without a name should error."""
        result = runner.invoke(app, ["flow", "show"])
        assert result.exit_code != 0

    def test_flow_template_help(self):
        """navig flow template --help should work."""
        result = runner.invoke(app, ["flow", "template", "--help"])
        assert result.exit_code == 0
        assert "template" in result.output.lower()
