"""Tests for navig.ui.tables — render_findings_table, render_fleet_table."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.ui.tables as tables_mod
from navig.ui.models import CauseScore
from navig.ui.tables import render_findings_table, render_fleet_table


def _cause(desc="High CPU", sev="high", conf=90) -> CauseScore:
    return CauseScore(description=desc, severity=sev, confidence=conf)


class TestRenderFindingsTable:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_findings_table([_cause()])

    def test_empty_list_does_not_print(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_findings_table([])
        mock_console.print.assert_not_called()

    def test_calls_console_print_with_findings(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_findings_table([_cause(), _cause("Memory leak", "critical", 95)])
        mock_console.print.assert_called_once()

    def test_never_raises_on_console_error(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = Exception("rich crash")
        with patch.object(tables_mod, "console", bad):
            render_findings_table([_cause()])  # must not propagate

    def test_custom_title_accepted(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_findings_table([_cause()], title="My Findings")

    def test_multiple_causes(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            causes = [_cause(f"c{i}") for i in range(5)]
            render_findings_table(causes)
        mock_console.print.assert_called_once()


class TestRenderFleetTable:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_fleet_table([{"host": "web1", "status": "online"}])

    def test_empty_prints_no_nodes(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_fleet_table([])
        mock_console.print.assert_called_once()

    def test_calls_console_print(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_fleet_table([{"host": "db1", "status": "running"}])
        mock_console.print.assert_called_once()

    def test_never_raises_on_console_error(self) -> None:
        bad_console = MagicMock()
        bad_console.print.side_effect = RuntimeError("crash")
        with patch.object(tables_mod, "console", bad_console):
            render_fleet_table([{"host": "x"}])  # must not propagate

    def test_custom_columns(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            render_fleet_table(
                [{"host": "x", "status": "ok", "region": "eu"}],
                columns=["host", "status"],
            )

    def test_multiple_nodes(self) -> None:
        mock_console = MagicMock()
        with patch.object(tables_mod, "console", mock_console):
            nodes = [{"host": f"node{i}", "status": "ok"} for i in range(3)]
            render_fleet_table(nodes)
        mock_console.print.assert_called_once()
