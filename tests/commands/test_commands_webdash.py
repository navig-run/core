"""Tests for navig/commands/webdash.py — superseded by `navig deck open`.

`webdash` used to boot a SECOND gateway (``navig.api.server.run_api_server``) to serve a
dashboard the running daemon already serves. It now names `navig deck open` and exits 1,
and must never start a server again.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.commands.webdash import app

runner = CliRunner()


def test_help_exits_0_and_names_the_replacement():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "deck open" in result.output


def test_default_points_at_deck_open_and_exits_1():
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "navig deck open" in result.output


def test_never_starts_a_second_gateway():
    mock_server = MagicMock()
    with patch.dict("sys.modules", {"navig.api.server": mock_server}):
        runner.invoke(app, [])
    mock_server.run_api_server.assert_not_called()


def test_old_flags_still_parse_and_get_the_pointer():
    for argv in (["--port", "8080"], ["--host", "0.0.0.0"], ["-p", "9000"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, argv
        assert "navig deck open" in result.output
