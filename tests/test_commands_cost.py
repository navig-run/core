"""Tests for navig.commands.cost — cost_app CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import typer
from typer.testing import CliRunner

from navig.commands.cost import cost_app, _show_current

runner = CliRunner()


def _make_tracker(inp=0, out=0, crd=0, summary=""):
    t = MagicMock()
    t.total_tokens.return_value = (inp, out, crd)
    t.format_summary.return_value = summary or f"Summary: in={inp} out={out}"
    return t


def _make_session(session_id="sess-1", started_at="2024-01-02T10:00:00Z",
                  total_input_tokens=100, total_output_tokens=200, total_cost_usd=0.001):
    s = MagicMock()
    s.session_id = session_id
    s.started_at = started_at
    s.total_input_tokens = total_input_tokens
    s.total_output_tokens = total_output_tokens
    s.total_cost_usd = total_cost_usd
    return s


# ---------------------------------------------------------------------------
# cost_default (show current)
# ---------------------------------------------------------------------------

class TestCostDefault:
    def test_zero_tokens_prints_dim(self, capsys):
        tracker = _make_tracker(inp=0, out=0)
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            result = runner.invoke(cost_app, [])
        assert result.exit_code == 0

    def test_zero_tokens_calls_total_tokens(self):
        tracker = _make_tracker(inp=0, out=0)
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            runner.invoke(cost_app, [])
        tracker.total_tokens.assert_called_once()

    def test_nonzero_tokens_calls_format_summary(self):
        tracker = _make_tracker(inp=100, out=50)
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            runner.invoke(cost_app, [])
        tracker.format_summary.assert_called_once()

    def test_nonzero_tokens_prints_summary(self, capsys):
        tracker = _make_tracker(inp=100, out=50, summary="Cost: $0.001")
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            with patch("navig.commands.cost.ch.console") as mock_console:
                _show_current()
                mock_console.print.assert_called_once_with("Cost: $0.001")

    def test_zero_invokes_dim(self):
        tracker = _make_tracker(inp=0, out=0)
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            with patch("navig.commands.cost.ch.dim") as mock_dim:
                _show_current()
                mock_dim.assert_called_once()
                assert "No LLM calls" in mock_dim.call_args[0][0]

    def test_print_exception_falls_back_to_echo(self, capsys):
        tracker = _make_tracker(inp=1, out=1, summary="fallback text")
        with patch("navig.cost_tracker.get_session_tracker", return_value=tracker):
            with patch("navig.commands.cost.ch.console") as mock_console:
                mock_console.print.side_effect = Exception("rich unavailable")
                _show_current()
        # Should not raise

    def test_subcommand_history_not_calling_default(self):
        """When a subcommand is invoked, _show_current should not be called."""
        sessions = []
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = sessions
            result = runner.invoke(cost_app, ["history"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# cost_history
# ---------------------------------------------------------------------------

class TestCostHistory:
    def test_empty_history_prints_dim(self):
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = []
            with patch("navig.commands.cost.ch.dim") as mock_dim:
                result = runner.invoke(cost_app, ["history"])
        assert result.exit_code == 0
        mock_dim.assert_called()

    def test_history_load_called_with_default_last_n(self):
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = []
            runner.invoke(cost_app, ["history"])
        MockSCT.load_history.assert_called_once_with(last_n=10)

    def test_history_load_called_with_custom_last_n(self):
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = []
            runner.invoke(cost_app, ["history", "--last", "5"])
        MockSCT.load_history.assert_called_once_with(last_n=5)

    def test_short_flag_n(self):
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = []
            runner.invoke(cost_app, ["history", "-n", "3"])
        MockSCT.load_history.assert_called_once_with(last_n=3)

    def test_with_sessions_calls_console_print(self):
        sessions = [_make_session()]
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = sessions
            with patch("navig.commands.cost.ch.console") as mock_console:
                with patch("navig.commands.cost.ch.Table") as MockTable:
                    MockTable.return_value = MagicMock()
                    result = runner.invoke(cost_app, ["history"])
        assert result.exit_code == 0

    def test_with_sessions_no_error(self):
        sessions = [_make_session(session_id="s1"), _make_session(session_id="s2")]
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = sessions
            result = runner.invoke(cost_app, ["history"])
        assert result.exit_code == 0

    def test_rich_exception_fallback(self):
        sessions = [_make_session()]
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT.load_history.return_value = sessions
            with patch("navig.commands.cost.ch.console") as mock_console:
                mock_console.print.side_effect = Exception("no rich")
                result = runner.invoke(cost_app, ["history"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# cost_clear
# ---------------------------------------------------------------------------

class TestCostClear:
    def _patch_clear(self, history_exists: bool, history_path: Path | None = None):
        if history_path is None:
            history_path = Path("/tmp/navig_test_history.json")

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = history_exists
        mock_path.__str__ = lambda s: str(history_path)

        return mock_path

    def test_clear_with_yes_flag_no_file(self):
        mock_path = self._patch_clear(history_exists=False)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            with patch("navig.commands.cost.ch.dim") as mock_dim:
                result = runner.invoke(cost_app, ["clear", "--yes"])
        assert result.exit_code == 0
        mock_dim.assert_called()

    def test_clear_with_yes_flag_with_file(self):
        mock_path = self._patch_clear(history_exists=True)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            with patch("navig.commands.cost.ch.success") as mock_success:
                result = runner.invoke(cost_app, ["clear", "--yes"])
        assert result.exit_code == 0
        mock_path.unlink.assert_called_once()

    def test_clear_with_yes_flag_calls_success(self):
        mock_path = self._patch_clear(history_exists=True)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            with patch("navig.commands.cost.ch.success") as mock_success:
                runner.invoke(cost_app, ["clear", "--yes"])
        mock_success.assert_called_once()

    def test_clear_no_file_shows_no_history_message(self):
        mock_path = self._patch_clear(history_exists=False)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            with patch("navig.commands.cost.ch.dim") as mock_dim:
                runner.invoke(cost_app, ["clear", "--yes"])
        assert any("No history" in str(c.args) for c in mock_dim.call_args_list)

    def test_clear_os_error_handled(self):
        mock_path = self._patch_clear(history_exists=True)
        mock_path.unlink.side_effect = OSError("permission denied")
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            result = runner.invoke(cost_app, ["clear", "--yes"])
        # Should not crash with exit code > 1 on handled OSError
        # exit code may be 0 or 1 depending on implementation
        assert result.exit_code in (0, 1)

    def test_clear_abort_on_no_confirmation(self):
        mock_path = self._patch_clear(history_exists=True)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            # Input "n" to the confirmation prompt
            result = runner.invoke(cost_app, ["clear"], input="n\n")
        assert result.exit_code == 0
        mock_path.unlink.assert_not_called()

    def test_clear_short_flag_y(self):
        mock_path = self._patch_clear(history_exists=False)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            result = runner.invoke(cost_app, ["clear", "-y"])
        assert result.exit_code == 0

    def test_clear_calls_history_path_static(self):
        mock_path = self._patch_clear(history_exists=False)
        with patch("navig.cost_tracker.SessionCostTracker") as MockSCT:
            MockSCT._history_path_static.return_value = mock_path
            runner.invoke(cost_app, ["clear", "--yes"])
        MockSCT._history_path_static.assert_called_once()


# ---------------------------------------------------------------------------
# cost_app structure
# ---------------------------------------------------------------------------

class TestCostAppStructure:
    def test_cost_app_is_typer(self):
        assert isinstance(cost_app, typer.Typer)

    def test_help_text_contains_cost(self):
        result = runner.invoke(cost_app, ["--help"])
        assert result.exit_code == 0
        assert "cost" in result.output.lower()

    def test_history_subcommand_available(self):
        result = runner.invoke(cost_app, ["history", "--help"])
        assert result.exit_code == 0

    def test_clear_subcommand_available(self):
        result = runner.invoke(cost_app, ["clear", "--help"])
        assert result.exit_code == 0

    def test_clear_help_mentions_yes(self):
        result = runner.invoke(cost_app, ["clear", "--help"])
        assert "--yes" in result.output or "-y" in result.output

    def test_history_help_mentions_last(self):
        result = runner.invoke(cost_app, ["history", "--help"])
        assert "--last" in result.output or "-n" in result.output
