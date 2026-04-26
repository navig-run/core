"""Tests for navig.ui.actions — render_actions, render_fallback, render_action_queue."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.ui.actions as actions_mod
from navig.ui.models import ActionItem


def _item(index: int, desc: str, risk: str = "low", value: str | None = None) -> ActionItem:
    return ActionItem(index=index, description=desc, risk=risk, estimated_value=value)


@pytest.fixture
def mock_console():
    con = MagicMock()
    with patch.object(actions_mod, "console", con):
        yield con


class TestRenderActions:
    def test_empty_list_does_nothing(self, mock_console):
        actions_mod.render_actions([])
        mock_console.print.assert_not_called()

    def test_single_item_printed(self, mock_console):
        actions_mod.render_actions([_item(1, "restart service")])
        assert mock_console.print.call_count >= 2  # title + item

    def test_estimated_value_included(self, mock_console):
        actions_mod.render_actions([_item(1, "deploy", value="saves 2h")])
        output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "saves 2h" in output

    def test_risk_styles_all_levels(self, mock_console):
        items = [_item(i + 1, "action", risk=r) for i, r in enumerate(("low", "medium", "high"))]
        actions_mod.render_actions(items)
        assert mock_console.print.call_count >= 4  # title + 3 items

    def test_never_raises_on_console_error(self):
        con = MagicMock()
        con.print.side_effect = RuntimeError("boom")
        with patch.object(actions_mod, "console", con):
            actions_mod.render_actions([_item(1, "test")])  # must not raise

    def test_custom_title(self, mock_console):
        actions_mod.render_actions([_item(1, "x")], title="My Actions")
        output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "My Actions" in output


class TestRenderFallback:
    def test_prints_message(self, mock_console):
        actions_mod.render_fallback("navig status", "Daemon offline")
        mock_console.print.assert_called()

    def test_alternatives_printed(self, mock_console):
        actions_mod.render_fallback("navig run", alternatives=["navig ssh", "navig exec"])
        output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "navig ssh" in output

    def test_never_raises_on_console_error(self):
        con = MagicMock()
        con.print.side_effect = RuntimeError("boom")
        with patch.object(actions_mod, "console", con):
            actions_mod.render_fallback("cmd")  # must not raise


class TestRenderActionQueue:
    def test_empty_queue_prints_empty_message(self, mock_console):
        actions_mod.render_action_queue([])
        mock_console.print.assert_called_once()
        call_str = str(mock_console.print.call_args)
        assert "empty" in call_str.lower()

    def test_items_are_printed(self, mock_console):
        items = [_item(1, "deploy app", risk="medium"), _item(2, "run migration", risk="high")]
        actions_mod.render_action_queue(items)
        assert mock_console.print.call_count >= 3  # title + 2 items

    def test_never_raises_on_console_error(self):
        con = MagicMock()
        con.print.side_effect = RuntimeError("boom")
        with patch.object(actions_mod, "console", con):
            actions_mod.render_action_queue([_item(1, "x")])  # must not raise
