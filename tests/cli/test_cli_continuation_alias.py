from typer.testing import CliRunner

from navig.cli import _register_external_commands, app
import pytest

pytestmark = pytest.mark.integration

# Register external command sub-apps for test discovery
_register_external_commands(register_all=True)

runner = CliRunner()


def test_top_level_continuation_alias_help():
    result = runner.invoke(app, ["continuation", "--help"])
    assert result.exit_code == 0
    assert "Manage autonomous continuation policy" in result.stdout


def test_top_level_continuation_status_runs():
    result = runner.invoke(app, ["continuation", "status", "--user-id", "999999"])
    assert result.exit_code == 0
    assert "No runtime AI state found" in result.stdout
