"""Tests for navig/commands/radar.py, watch_cmd.py, and deck.py."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_WARN = "navig.console_helper.warning"


# ===========================================================================
# radar.py
# ===========================================================================

from navig.commands.radar import radar_app

radar_runner = CliRunner()


def test_radar_help_exits_0():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert result.exit_code == 0


def test_radar_help_mentions_list():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert "list" in result.output.lower()


def test_radar_help_mentions_add():
    result = radar_runner.invoke(radar_app, ["--help"])
    assert "add" in result.output.lower()


def test_radar_list_exits_0():
    with patch(_WARN):
        result = radar_runner.invoke(radar_app, ["list"])
    assert result.exit_code == 0


def test_radar_list_calls_warn():
    with patch(_WARN) as mock_warn:
        radar_runner.invoke(radar_app, ["list"])
    mock_warn.assert_called_once()


def test_radar_list_not_implemented():
    with patch(_WARN) as mock_warn:
        radar_runner.invoke(radar_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


def test_radar_add_exits_0():
    with patch(_WARN):
        result = radar_runner.invoke(radar_app, ["add", "error"])
    assert result.exit_code == 0


def test_radar_add_calls_warn():
    with patch(_WARN) as mock_warn:
        radar_runner.invoke(radar_app, ["add", "keyword"])
    mock_warn.assert_called_once()


def test_radar_add_missing_keyword_exits_nonzero():
    result = radar_runner.invoke(radar_app, ["add"])
    assert result.exit_code != 0


# ===========================================================================
# watch_cmd.py
# ===========================================================================

from navig.commands.watch_cmd import watch_app

watch_runner = CliRunner()


def test_watch_help_exits_0():
    result = watch_runner.invoke(watch_app, ["--help"])
    assert result.exit_code == 0


def test_watch_start_exits_0():
    with patch(_WARN):
        result = watch_runner.invoke(watch_app, ["start"])
    assert result.exit_code == 0


def test_watch_start_calls_warn():
    with patch(_WARN) as mock_warn:
        watch_runner.invoke(watch_app, ["start"])
    mock_warn.assert_called_once()


def test_watch_start_with_path():
    with patch(_WARN):
        result = watch_runner.invoke(watch_app, ["start", "/tmp"])
    assert result.exit_code == 0


def test_watch_list_exits_0():
    with patch(_WARN):
        result = watch_runner.invoke(watch_app, ["list"])
    assert result.exit_code == 0


def test_watch_list_calls_warn():
    with patch(_WARN) as mock_warn:
        watch_runner.invoke(watch_app, ["list"])
    mock_warn.assert_called_once()


def test_watch_list_not_implemented():
    with patch(_WARN) as mock_warn:
        watch_runner.invoke(watch_app, ["list"])
    assert "not yet implemented" in mock_warn.call_args[0][0]


# ===========================================================================
# deck.py
# ===========================================================================

from navig.commands.deck import deck_app

deck_runner = CliRunner()


def test_deck_help_exits_0():
    result = deck_runner.invoke(deck_app, ["--help"])
    assert result.exit_code == 0


# ── `navig deck` is IMPLEMENTED — these pin the real contract ────────────────
#
# The seven tests replaced here asserted a PLACEHOLDER: that `deck list` / `deck new`
# each warn "not yet implemented", and that the help mentions them. `deck` has since
# become a real command group — `open · dev · deploy` (see deck_app's help string) —
# and `list` / `new` were never built. Those tests therefore demanded a REGRESSION,
# and they only surfaced now because the suite had not actually been running (the
# ci-local runner collected 0 tests; fixed in #136).
#
# Note the `radar` / `watch` tests ABOVE are left untouched on purpose: those command
# groups genuinely are still "not yet implemented" placeholders, so their assertions
# are correct and they pass. Only the `deck` half went stale.


def test_deck_help_lists_the_real_commands():
    result = deck_runner.invoke(deck_app, ["--help"])
    out = result.output.lower()
    for cmd in ("open", "dev", "deploy"):
        assert cmd in out


def test_deck_has_no_list_command():
    result = deck_runner.invoke(deck_app, ["list"])
    assert result.exit_code != 0


def test_deck_has_no_new_command():
    result = deck_runner.invoke(deck_app, ["new", "my-deck"])
    assert result.exit_code != 0


def test_deck_does_not_claim_unimplemented():
    """`deck` is shipped — its help must never advertise itself as a stub."""
    result = deck_runner.invoke(deck_app, ["--help"])
    assert "not yet implemented" not in result.output.lower()
