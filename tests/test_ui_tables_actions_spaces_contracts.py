"""Batch 73 — ui/tables, ui/actions, spaces/contracts."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.ui.models import ActionItem, CauseScore


# ---------------------------------------------------------------------------
# navig.ui.tables — render_findings_table, render_fleet_table
# ---------------------------------------------------------------------------

class TestRenderFindingsTable:
    def test_empty_no_output(self):
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_findings_table
            render_findings_table([])
        mock_c.print.assert_not_called()

    def test_prints_table(self):
        findings = [CauseScore(confidence=80, description="High CPU", severity="warn")]
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_findings_table
            render_findings_table(findings)
        mock_c.print.assert_called_once()

    def test_multiple_findings_single_call(self):
        findings = [
            CauseScore(confidence=90, description="Memory spike", severity="critical"),
            CauseScore(confidence=50, description="Disk slow", severity="info"),
        ]
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_findings_table
            render_findings_table(findings)
        mock_c.print.assert_called_once()

    def test_no_raise_on_exception(self):
        findings = [CauseScore(confidence=50, description="x")]
        with patch("navig.ui.tables.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.tables import render_findings_table
            render_findings_table(findings)


class TestRenderFleetTable:
    def test_empty_nodes_prints_message(self):
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_fleet_table
            render_fleet_table([])
        mock_c.print.assert_called_once()
        assert "No nodes" in str(mock_c.print.call_args)

    def test_prints_table_for_nodes(self):
        nodes = [{"host": "srv1", "status": "online"}, {"host": "srv2", "status": "offline"}]
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_fleet_table
            render_fleet_table(nodes)
        mock_c.print.assert_called_once()

    def test_custom_columns(self):
        nodes = [{"host": "srv1", "status": "ok", "region": "eu"}]
        with patch("navig.ui.tables.console") as mock_c:
            from navig.ui.tables import render_fleet_table
            render_fleet_table(nodes, columns=["host", "region"])
        mock_c.print.assert_called_once()

    def test_no_raise_on_exception(self):
        with patch("navig.ui.tables.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.tables import render_fleet_table
            render_fleet_table([{"host": "x"}])


# ---------------------------------------------------------------------------
# navig.ui.actions — render_actions, render_fallback, render_action_queue
# ---------------------------------------------------------------------------

class TestRenderActions:
    def test_empty_items_no_output(self):
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_actions
            render_actions([])
        mock_c.print.assert_not_called()

    def test_prints_title_and_items(self):
        items = [ActionItem(index=1, description="Run health check", risk="low")]
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_actions
            render_actions(items)
        # title + 1 item = 2 calls
        assert mock_c.print.call_count == 2

    def test_estimated_value_included(self):
        items = [ActionItem(index=1, description="Scale out", estimated_value="+10%", risk="medium")]
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_actions
            render_actions(items)
        printed = str(mock_c.print.call_args_list)
        assert "+10%" in printed

    def test_no_raise_on_exception(self):
        items = [ActionItem(index=1, description="x")]
        with patch("navig.ui.actions.console") as mock_c:
            mock_c.print.side_effect = RuntimeError
            from navig.ui.actions import render_actions
            render_actions(items)


class TestRenderFallback:
    def test_calls_console_print(self):
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_fallback
            render_fallback("navig run", "Daemon offline")
        assert mock_c.print.called

    def test_alternatives_trigger_extra_prints(self):
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_fallback
            render_fallback("cmd", alternatives=["navig status", "navig host test"])
        # 1 main + 1 "Alternatives" label + 2 items = 4 calls
        assert mock_c.print.call_count == 4

    def test_no_raise_on_exception(self):
        with patch("navig.ui.actions.console") as mock_c:
            mock_c.print.side_effect = RuntimeError
            from navig.ui.actions import render_fallback
            render_fallback("cmd")


class TestRenderActionQueue:
    def test_empty_queue_prints_message(self):
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_action_queue
            render_action_queue([])
        mock_c.print.assert_called_once()
        assert "empty" in str(mock_c.print.call_args).lower()

    def test_prints_title_and_items(self):
        items = [
            ActionItem(index=1, description="Deploy", risk="high"),
            ActionItem(index=2, description="Backup", risk="low"),
        ]
        with patch("navig.ui.actions.console") as mock_c:
            from navig.ui.actions import render_action_queue
            render_action_queue(items)
        # title + 2 items = 3
        assert mock_c.print.call_count == 3

    def test_no_raise_on_exception(self):
        items = [ActionItem(index=1, description="x")]
        with patch("navig.ui.actions.console") as mock_c:
            mock_c.print.side_effect = RuntimeError
            from navig.ui.actions import render_action_queue
            render_action_queue(items)


# ---------------------------------------------------------------------------
# navig.spaces.contracts — normalize_space_name, validate_space_name, is_user_space
# ---------------------------------------------------------------------------

class TestNormalizeSpaceName:
    def test_none_returns_default(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name(None) == "default"

    def test_empty_string_returns_default(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("") == "default"

    def test_canonical_name_returned_as_is(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("project") == "project"
        assert normalize_space_name("devops") == "devops"

    def test_alias_resolved(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("ops") == "devops"
        assert normalize_space_name("operations") == "devops"

    def test_space_suffix_alias_resolved(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("career-space") == "career"
        assert normalize_space_name("devops-space") == "devops"

    def test_unknown_returns_default(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("unknown-form") == "default"

    def test_case_insensitive(self):
        from navig.spaces.contracts import normalize_space_name
        assert normalize_space_name("PROJECT") == "project"


class TestValidateSpaceName:
    def test_canonical_is_valid(self):
        from navig.spaces.contracts import validate_space_name
        assert validate_space_name("project") is True
        assert validate_space_name("sysops") is True

    def test_alias_is_valid(self):
        from navig.spaces.contracts import validate_space_name
        assert validate_space_name("ops") is True

    def test_unknown_is_invalid(self):
        from navig.spaces.contracts import validate_space_name
        assert validate_space_name("my-random-space") is False


class TestIsUserSpace:
    def test_canonical_is_not_user_space(self):
        from navig.spaces.contracts import is_user_space
        assert is_user_space("project") is False

    def test_custom_is_user_space(self):
        from navig.spaces.contracts import is_user_space
        assert is_user_space("my-blog") is True


class TestCanonicalSpaces:
    def test_default_first(self):
        from navig.spaces.contracts import CANONICAL_SPACES
        assert CANONICAL_SPACES[0] == "default"

    def test_contains_ops_spaces(self):
        from navig.spaces.contracts import CANONICAL_SPACES
        assert "devops" in CANONICAL_SPACES
        assert "sysops" in CANONICAL_SPACES
