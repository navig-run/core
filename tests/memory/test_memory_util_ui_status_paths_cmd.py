"""Batch 60 — memory/_util, ui/status, commands/paths_cmd."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.memory._util
# ---------------------------------------------------------------------------

class TestDebugLog:
    def test_logs_at_debug_level(self):
        from navig.memory._util import _debug_log
        with patch("navig.memory._util._logger") as mock_logger:
            _debug_log("hello memory")
        mock_logger.debug.assert_called_once_with("hello memory")

    def test_empty_message_does_not_raise(self):
        from navig.memory._util import _debug_log
        _debug_log("")  # should not raise

    def test_exception_in_logger_silenced(self):
        from navig.memory._util import _debug_log
        with patch("navig.memory._util._logger") as mock_logger:
            mock_logger.debug.side_effect = RuntimeError("logger broken")
            _debug_log("test")  # must not propagate

    def test_returns_none(self):
        from navig.memory._util import _debug_log
        result = _debug_log("msg")
        assert result is None


class TestAtomicWriteText:
    def test_delegates_to_canonical_impl(self, tmp_path):
        from navig.memory._util import _atomic_write_text
        with patch("navig.memory._util._atomic_write_text_impl") as mock_impl:
            _atomic_write_text(tmp_path / "out.txt", "content")
        mock_impl.assert_called_once_with(tmp_path / "out.txt", "content")

    def test_returns_none(self, tmp_path):
        from navig.memory._util import _atomic_write_text
        with patch("navig.memory._util._atomic_write_text_impl"):
            result = _atomic_write_text(tmp_path / "out.txt", "data")
        assert result is None


# ---------------------------------------------------------------------------
# navig.ui.status — render_status_header
# ---------------------------------------------------------------------------

class TestRenderStatusHeader:
    def _make_chip(self, label="daemon", value=None, color="white"):
        from navig.ui.models import StatusChip
        return StatusChip(icon="●", icon_safe="*", label=label, value=value, color=color)

    def test_empty_chips_no_raise(self):
        from navig.ui.status import render_status_header
        render_status_header([])  # must not raise

    def test_single_chip_no_value(self):
        from navig.ui.status import render_status_header
        chip = self._make_chip("host")
        with patch("navig.ui.status.console") as mock_console:
            render_status_header([chip])
        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        assert "host" in output

    def test_single_chip_with_value(self):
        from navig.ui.status import render_status_header
        chip = self._make_chip("peers", value="3")
        with patch("navig.ui.status.console") as mock_console:
            render_status_header([chip])
        output = mock_console.print.call_args[0][0]
        assert "peers" in output
        assert "3" in output

    def test_multiple_chips_joined(self):
        from navig.ui.status import render_status_header
        chips = [self._make_chip("a"), self._make_chip("b")]
        with patch("navig.ui.status.console") as mock_console:
            render_status_header(chips)
        output = mock_console.print.call_args[0][0]
        assert "a" in output
        assert "b" in output

    def test_custom_separator(self):
        from navig.ui.status import render_status_header
        chips = [self._make_chip("x"), self._make_chip("y")]
        with patch("navig.ui.status.console") as mock_console:
            render_status_header(chips, sep="|||")
        output = mock_console.print.call_args[0][0]
        assert "|||" in output

    def test_exception_silenced(self):
        from navig.ui.status import render_status_header
        broken_chip = MagicMock(spec=[])  # no attributes at all
        # Should not raise despite broken chip
        render_status_header([broken_chip])


# ---------------------------------------------------------------------------
# navig.commands.paths_cmd — paths_app
# ---------------------------------------------------------------------------

class TestPathsApp:
    @pytest.fixture(autouse=True)
    def runner(self):
        from typer.testing import CliRunner
        self.runner = CliRunner()

    def _invoke(self, args=None):
        from navig.commands.paths_cmd import paths_app
        return self.runner.invoke(paths_app, args or [])

    def test_default_exits_zero(self):
        result = self._invoke([])
        assert result.exit_code == 0

    def test_output_contains_paths_title(self):
        result = self._invoke([])
        assert "NAVIG" in result.output or "Path" in result.output

    def test_output_contains_config_key(self):
        result = self._invoke([])
        assert "config" in result.output

    def test_output_contains_logs_key(self):
        result = self._invoke([])
        assert "logs" in result.output

    def test_output_contains_data_key(self):
        result = self._invoke([])
        assert "data" in result.output

    def test_help_flag_works(self):
        result = self._invoke(["--help"])
        assert result.exit_code == 0
        assert "Usage" in result.output
