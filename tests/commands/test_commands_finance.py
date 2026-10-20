"""Tests for navig/commands/finance.py — superseded: finance lives in the OS Finance app.

`finance balance` was a "not yet implemented" stub and `finance status` only reported
whether `beancount` was importable. Both now name the real surface and exit 1.
"""

import pytest
from typer.testing import CliRunner

from navig.commands.finance import finance_app

runner = CliRunner()


def test_help_exits_0():
    result = runner.invoke(finance_app, ["--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("argv", [[], ["status"], ["balance"], ["balance", "Assets:Cash"]])
def test_every_verb_points_at_the_finance_module_and_exits_1(argv):
    result = runner.invoke(finance_app, argv)
    assert result.exit_code == 1, result.output
    assert "navig store info module:finance" in result.output
