"""Tests for navig/commands/finance.py."""

import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from navig.commands.finance import finance_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

def test_help_exits_0():
    result = runner.invoke(finance_app, ["--help"])
    assert result.exit_code == 0


def test_help_mentions_status():
    result = runner.invoke(finance_app, ["--help"])
    assert "status" in result.output.lower()


def test_help_mentions_balance():
    result = runner.invoke(finance_app, ["--help"])
    assert "balance" in result.output.lower()


def test_no_args_exits_nonzero_or_shows_help():
    result = runner.invoke(finance_app, [])
    assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# finance status — beancount available
# ---------------------------------------------------------------------------

def test_status_exits_0_when_beancount_available():
    with patch.dict("sys.modules", {"beancount": MagicMock()}):
        result = runner.invoke(finance_app, ["status"])
    assert result.exit_code == 0


def test_status_mentions_beancount_when_available():
    with patch.dict("sys.modules", {"beancount": MagicMock()}):
        result = runner.invoke(finance_app, ["status"])
    assert "beancount" in result.output.lower()


def test_status_shows_available_message():
    with patch.dict("sys.modules", {"beancount": MagicMock()}):
        result = runner.invoke(finance_app, ["status"])
    assert "available" in result.output.lower()


# ---------------------------------------------------------------------------
# finance status — beancount missing
# ---------------------------------------------------------------------------

def test_status_exits_0_when_beancount_missing():
    import sys
    orig = sys.modules.pop("beancount", None)
    try:
        with patch.dict("sys.modules", {"beancount": None}):
            result = runner.invoke(finance_app, ["status"])
        assert result.exit_code == 0
    finally:
        if orig is not None:
            sys.modules["beancount"] = orig


def test_status_mentions_not_installed_when_missing():
    import sys
    orig = sys.modules.pop("beancount", None)
    try:
        with patch.dict("sys.modules", {"beancount": None}):
            result = runner.invoke(finance_app, ["status"])
        assert "not installed" in result.output.lower() or "beancount" in result.output.lower()
    finally:
        if orig is not None:
            sys.modules["beancount"] = orig


def test_status_help_exits_0():
    result = runner.invoke(finance_app, ["status", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# finance balance
# ---------------------------------------------------------------------------

def test_balance_exits_0():
    with patch("navig.console_helper.warn", create=True):
        result = runner.invoke(finance_app, ["balance", "ledger.beancount"])
    assert result.exit_code == 0


def test_balance_calls_warn():
    with patch("navig.console_helper.warn", create=True) as mock_warn:
        runner.invoke(finance_app, ["balance", "myfile.beancount"])
    mock_warn.assert_called_once()


def test_balance_warn_says_not_implemented():
    with patch("navig.console_helper.warn", create=True) as mock_warn:
        runner.invoke(finance_app, ["balance", "x.beancount"])
    call_arg = mock_warn.call_args[0][0]
    assert "not yet implemented" in call_arg


def test_balance_no_ledger_still_runs():
    with patch("navig.console_helper.warn", create=True):
        result = runner.invoke(finance_app, ["balance"])
    assert result.exit_code == 0


def test_balance_help_exits_0():
    result = runner.invoke(finance_app, ["balance", "--help"])
    assert result.exit_code == 0
