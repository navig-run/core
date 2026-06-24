"""Tests for navig.ui.formatters — render_kv_diagnostics, render_command_row, render_section_divider."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from navig.ui import formatters as fmt_mod
from navig.ui.formatters import render_command_row, render_kv_diagnostics, render_section_divider


class TestRenderKvDiagnostics:
    def test_does_not_raise_with_empty_list(self):
        render_kv_diagnostics([])  # should be a no-op

    def test_does_not_raise_with_pairs(self):
        with patch.object(fmt_mod, "console", MagicMock()):
            render_kv_diagnostics([("key", "value"), ("host", "localhost")])

    def test_prints_title_when_given(self):
        mock_console = MagicMock()
        with patch.object(fmt_mod, "console", mock_console):
            render_kv_diagnostics([("a", "b")], title="Diagnostics")
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("Diagnostics" in c for c in calls)

    def test_no_title_does_not_print_none(self):
        mock_console = MagicMock()
        with patch.object(fmt_mod, "console", mock_console):
            render_kv_diagnostics([("x", "y")])
        # Should print only the kv pair, not a title line
        assert mock_console.print.call_count == 1

    def test_survives_console_exception(self):
        with patch.object(fmt_mod, "console", MagicMock(print=MagicMock(side_effect=Exception))):
            render_kv_diagnostics([("a", "b")])  # should not raise


class TestRenderCommandRow:
    def test_does_not_raise(self):
        with patch.object(fmt_mod, "console", MagicMock()):
            render_command_row("navig file", "navig file list /tmp")

    def test_includes_description(self):
        mock_console = MagicMock()
        with patch.object(fmt_mod, "console", mock_console):
            render_command_row("label", "cmd", description="Helpful hint")
        call_str = str(mock_console.print.call_args_list)
        assert "Helpful hint" in call_str

    def test_survives_console_exception(self):
        with patch.object(fmt_mod, "console", MagicMock(print=MagicMock(side_effect=Exception))):
            render_command_row("x", "y")  # should not raise


class TestRenderSectionDivider:
    def test_does_not_raise(self):
        with patch.object(fmt_mod, "console", MagicMock()):
            render_section_divider()

    def test_with_title(self):
        mock_console = MagicMock()
        with patch.object(fmt_mod, "console", mock_console):
            render_section_divider("Section Title")
        mock_console.rule.assert_called_once()

    def test_survives_console_exception(self):
        with patch.object(fmt_mod, "console", MagicMock(rule=MagicMock(side_effect=Exception))):
            render_section_divider("Title")  # should not raise
