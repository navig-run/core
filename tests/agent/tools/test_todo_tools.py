"""
Unit tests for navig/agent/tools/todo_tools.py

Covers: set_todo_list, get_todo_list, _auto_save, TodoCreateTool, TodoUpdateTool, TodoShowTool
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import navig.agent.tools.todo_tools as _mod
from navig.agent.tools.todo_tools import (
    TodoCreateTool,
    TodoShowTool,
    TodoUpdateTool,
    get_todo_list,
    set_todo_list,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_todo_list(session_id="session-1"):
    from navig.agent.todo_tracker import TodoList
    tl = TodoList(session_id=session_id)
    return tl


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ──────────────────────────────────────────────────────────────────────
# set_todo_list / get_todo_list
# ──────────────────────────────────────────────────────────────────────


class TestSetGetTodoList:
    def setup_method(self):
        # Reset module-level refs
        _mod._todo_list_ref = None
        _mod._persistence_ref = None

    def test_get_raises_when_not_initialised(self):
        with pytest.raises(RuntimeError, match="not initialised"):
            get_todo_list()

    def test_set_and_get_roundtrip(self):
        tl = _make_todo_list()
        set_todo_list(tl)
        assert get_todo_list() is tl

    def test_set_with_persistence(self):
        tl = _make_todo_list()
        pers = MagicMock()
        set_todo_list(tl, pers)
        assert _mod._persistence_ref is pers

    def test_set_none_clears_ref(self):
        tl = _make_todo_list()
        set_todo_list(tl)
        set_todo_list(None)
        _mod._todo_list_ref = None
        with pytest.raises(RuntimeError):
            get_todo_list()


# ──────────────────────────────────────────────────────────────────────
# _auto_save
# ──────────────────────────────────────────────────────────────────────


class TestAutoSave:
    def setup_method(self):
        _mod._todo_list_ref = None
        _mod._persistence_ref = None

    def test_no_op_when_no_persistence(self):
        tl = _make_todo_list()
        _mod._todo_list_ref = tl
        _mod._persistence_ref = None
        _mod._auto_save()  # Should not raise

    def test_calls_persistence_save(self):
        tl = _make_todo_list()
        pers = MagicMock()
        _mod._todo_list_ref = tl
        _mod._persistence_ref = pers
        _mod._auto_save()
        pers.save.assert_called_once_with(tl)

    def test_save_exception_is_suppressed(self):
        tl = _make_todo_list()
        pers = MagicMock()
        pers.save.side_effect = IOError("disk full")
        _mod._todo_list_ref = tl
        _mod._persistence_ref = pers
        _mod._auto_save()  # Must not propagate


# ──────────────────────────────────────────────────────────────────────
# TodoCreateTool
# ──────────────────────────────────────────────────────────────────────


class TestTodoCreateTool:
    def setup_method(self):
        self.tl = _make_todo_list()
        set_todo_list(self.tl)

    def test_tool_name(self):
        assert TodoCreateTool.name == "todo_create"

    def test_empty_items_fails(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": ""}))
        assert result.success is False
        assert "required" in result.output.lower()

    def test_json_array_creates_items(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": '[{"title": "Task A"}, {"title": "Task B"}]'}))
        assert result.success is True
        assert "Task A" in result.output or "2 items" in result.output

    def test_comma_separated_creates_items(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": "Task X, Task Y, Task Z"}))
        assert result.success is True

    def test_too_many_items_fails(self):
        from navig.agent.todo_tracker import MAX_ITEMS
        titles = [f"Task {i}" for i in range(MAX_ITEMS + 1)]
        items_json = str(titles).replace("'", '"')
        tool = TodoCreateTool()
        result = run(tool.run({"items": items_json}))
        assert result.success is False
        assert "Too many" in result.output

    def test_invalid_json_falls_back_to_csv(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": "alpha, beta"}))
        assert result.success is True

    def test_json_array_of_strings(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": '["Do this", "Do that"]'}))
        assert result.success is True

    def test_non_array_json_fails(self):
        tool = TodoCreateTool()
        result = run(tool.run({"items": '{"title": "Task A"}'}))
        assert result.success is False


# ──────────────────────────────────────────────────────────────────────
# TodoUpdateTool
# ──────────────────────────────────────────────────────────────────────


class TestTodoUpdateTool:
    def setup_method(self):
        self.tl = _make_todo_list()
        self.tl.add("Task A")
        self.item_id = self.tl.items[0].id
        set_todo_list(self.tl)

    def test_tool_name(self):
        assert TodoUpdateTool.name == "todo_update"

    def test_missing_id_fails(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"status": "completed"}))
        assert result.success is False
        assert "id" in result.output.lower()

    def test_invalid_id_type_fails(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"id": "not-a-number", "status": "completed"}))
        assert result.success is False

    def test_invalid_status_fails(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"id": self.item_id, "status": "flying"}))
        assert result.success is False
        assert "Invalid status" in result.output

    def test_nonexistent_id_fails(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"id": 9999, "status": "completed"}))
        assert result.success is False

    def test_valid_update_succeeds(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"id": self.item_id, "status": "in-progress"}))
        assert result.success is True
        assert "in-progress" in result.output

    def test_complete_item_succeeds(self):
        tool = TodoUpdateTool()
        result = run(tool.run({"id": self.item_id, "status": "completed"}))
        assert result.success is True


# ──────────────────────────────────────────────────────────────────────
# TodoShowTool
# ──────────────────────────────────────────────────────────────────────


class TestTodoShowTool:
    def setup_method(self):
        self.tl = _make_todo_list()
        set_todo_list(self.tl)

    def test_tool_name(self):
        assert TodoShowTool.name == "todo_show"

    def test_show_empty_list_succeeds(self):
        tool = TodoShowTool()
        result = run(tool.run({}))
        assert result.success is True

    def test_show_with_items(self):
        self.tl.add("My Task")
        tool = TodoShowTool()
        result = run(tool.run({}))
        assert result.success is True
        assert "My Task" in result.output

    def test_show_not_initialised_raises(self):
        _mod._todo_list_ref = None
        tool = TodoShowTool()
        with pytest.raises(RuntimeError):
            run(tool.run({}))
