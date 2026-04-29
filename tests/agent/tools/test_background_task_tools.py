"""
Tests for navig.agent.tools.background_task_tools
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.tools.background_task_tools import (
    BackgroundTaskKillTool,
    BackgroundTaskOutputTool,
    BackgroundTaskStartTool,
    BackgroundTaskStatusTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_task_stub(task_id=1, pid=12345, label="test task"):
    t = MagicMock()
    t.task_id = task_id
    t.pid = pid
    t.label = label
    return t


# ---------------------------------------------------------------------------
# BackgroundTaskStartTool
# ---------------------------------------------------------------------------


class TestBackgroundTaskStartTool:
    def setup_method(self):
        self.tool = BackgroundTaskStartTool()

    def test_name(self):
        assert self.tool.name == "background_task_start"

    def test_missing_command_returns_failure(self):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "command" in result.error.lower()

    def test_empty_command_returns_failure(self):
        result = _run(self.tool.run({"command": "   "}))
        assert result.success is False

    def test_start_success(self):
        mock_manager = MagicMock()
        mock_task = _make_task_stub()
        mock_manager.start = AsyncMock(return_value=mock_task)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"command": "sleep 10", "label": "sleeper"}))

        assert result.success is True
        assert "1" in result.output  # task_id
        assert "sleeper" in result.output

    def test_manager_raises_returns_failure(self):
        mock_manager = MagicMock()
        mock_manager.start = AsyncMock(side_effect=RuntimeError("spawn error"))

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"command": "bad cmd"}))

        assert result.success is False
        assert "spawn error" in result.error

    def test_optional_cwd(self):
        mock_manager = MagicMock()
        mock_task = _make_task_stub()
        mock_manager.start = AsyncMock(return_value=mock_task)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(
                self.tool.run({"command": "ls", "cwd": "/tmp"})
            )

        assert result.success is True
        mock_manager.start.assert_awaited_once()
        call_kwargs = mock_manager.start.call_args.kwargs
        assert call_kwargs.get("cwd") == "/tmp"


# ---------------------------------------------------------------------------
# BackgroundTaskStatusTool
# ---------------------------------------------------------------------------


class TestBackgroundTaskStatusTool:
    def setup_method(self):
        self.tool = BackgroundTaskStatusTool()

    def test_name(self):
        assert self.tool.name == "background_task_status"

    def test_lists_all_when_no_task_id(self):
        mock_manager = MagicMock()
        mock_manager.list_tasks.return_value = [{"id": 1, "status": "running"}]

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({}))

        assert result.success is True
        assert "1" in result.output

    def test_empty_list_returns_message(self):
        mock_manager = MagicMock()
        mock_manager.list_tasks.return_value = []

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({}))

        assert result.success is True
        assert "No background tasks" in result.output

    def test_specific_task_id_success(self):
        mock_manager = MagicMock()
        mock_manager.status.return_value = {"id": 3, "status": "done"}

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 3}))

        assert result.success is True
        assert "done" in result.output

    def test_task_id_not_found(self):
        mock_manager = MagicMock()
        mock_manager.status.return_value = {"error": "not found"}

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 99}))

        assert result.success is False

    def test_invalid_task_id_type_treated_as_zero(self):
        """Non-parseable task_id falls back to list-all path."""
        mock_manager = MagicMock()
        mock_manager.list_tasks.return_value = []

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": "notanumber"}))

        # list_tasks called because task_id coerces to 0
        mock_manager.list_tasks.assert_called_once()
        assert result.success is True


# ---------------------------------------------------------------------------
# BackgroundTaskOutputTool
# ---------------------------------------------------------------------------


class TestBackgroundTaskOutputTool:
    def setup_method(self):
        self.tool = BackgroundTaskOutputTool()

    def test_name(self):
        assert self.tool.name == "background_task_output"

    def test_missing_task_id_returns_failure(self):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "task_id" in result.error

    def test_invalid_task_id_type_returns_failure(self):
        result = _run(self.tool.run({"task_id": "abc"}))
        assert result.success is False

    def test_output_returned(self):
        mock_manager = MagicMock()
        mock_manager.get_output.return_value = "line1\nline2\n"

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 1}))

        assert result.success is True
        assert "line1" in result.output

    def test_unknown_task_id_from_manager(self):
        mock_manager = MagicMock()
        mock_manager.get_output.return_value = "No task with id 999"

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 999}))

        assert result.success is False

    def test_custom_tail_lines(self):
        mock_manager = MagicMock()
        mock_manager.get_output.return_value = "output"

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            _run(self.tool.run({"task_id": 1, "tail": 10}))

        mock_manager.get_output.assert_called_once_with(1, tail=10)


# ---------------------------------------------------------------------------
# BackgroundTaskKillTool
# ---------------------------------------------------------------------------


class TestBackgroundTaskKillTool:
    def setup_method(self):
        self.tool = BackgroundTaskKillTool()

    def test_name(self):
        assert self.tool.name == "background_task_kill"

    def test_missing_task_id_returns_failure(self):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "task_id" in result.error

    def test_invalid_task_id_type_returns_failure(self):
        result = _run(self.tool.run({"task_id": "xyz"}))
        assert result.success is False

    def test_kill_success(self):
        mock_manager = MagicMock()
        mock_manager.kill = AsyncMock(return_value=True)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 5}))

        assert result.success is True
        assert "5" in result.output

    def test_kill_not_found(self):
        mock_manager = MagicMock()
        mock_manager.kill = AsyncMock(return_value=False)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mock_manager,
        ):
            result = _run(self.tool.run({"task_id": 5}))

        assert result.success is False
        assert "not found" in result.error.lower()
