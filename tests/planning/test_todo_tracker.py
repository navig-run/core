"""Tests for FA-03: Todo Tracker — TodoItem, TodoList, TodoPersistence, todo tools."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from navig.agent.todo_tracker import (
    MAX_ITEMS,
    MAX_TITLE_LENGTH,
    NUDGE_INTERVAL,
    TodoItem,
    TodoList,
    TodoPersistence,
    TodoStatus,
)

pytestmark = pytest.mark.integration

# ─────────────────────────────────────────────────────────────
# TodoStatus
# ─────────────────────────────────────────────────────────────


class TestTodoStatus:
    def test_all_statuses_present(self):
        names = {s.name for s in TodoStatus}
        assert names == {"NOT_STARTED", "IN_PROGRESS", "COMPLETED"}

    def test_values_are_strings(self):
        for s in TodoStatus:
            assert isinstance(s.value, str)

    def test_value_format(self):
        assert TodoStatus.NOT_STARTED.value == "not-started"
        assert TodoStatus.IN_PROGRESS.value == "in-progress"
        assert TodoStatus.COMPLETED.value == "completed"


# ─────────────────────────────────────────────────────────────
# TodoItem
# ─────────────────────────────────────────────────────────────


class TestTodoItem:
    def test_defaults(self):
        item = TodoItem(id=1, title="Read code")
        assert item.id == 1
        assert item.title == "Read code"
        assert item.status == TodoStatus.NOT_STARTED
        assert item.completed_at is None
        assert item.created_at > 0

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="empty"):
            TodoItem(id=1, title="")

    def test_whitespace_title_raises(self):
        with pytest.raises(ValueError, match="empty"):
            TodoItem(id=1, title="   ")

    def test_title_too_long_raises(self):
        with pytest.raises(ValueError, match="exceeds"):
            TodoItem(id=1, title="x" * (MAX_TITLE_LENGTH + 1))

    def test_title_at_limit_ok(self):
        item = TodoItem(id=1, title="x" * MAX_TITLE_LENGTH)
        assert len(item.title) == MAX_TITLE_LENGTH

    def test_to_dict(self):
        item = TodoItem(id=3, title="Fix bug", status=TodoStatus.COMPLETED, completed_at=100.0)
        d = item.to_dict()
        assert d["id"] == 3
        assert d["title"] == "Fix bug"
        assert d["status"] == "completed"
        assert d["completed_at"] == 100.0

    def test_from_dict_roundtrip(self):
        item = TodoItem(id=5, title="Test it", status=TodoStatus.IN_PROGRESS)
        d = item.to_dict()
        restored = TodoItem.from_dict(d)
        assert restored.id == item.id
        assert restored.title == item.title
        assert restored.status == item.status


# ─────────────────────────────────────────────────────────────
# TodoList — basic operations
# ─────────────────────────────────────────────────────────────


class TestTodoListBasic:
    def test_add_item(self):
        tl = TodoList(session_id="s1")
        item = tl.add("Read docs")
        assert item.id == 1
        assert item.title == "Read docs"
        assert len(tl.items) == 1

    def test_add_increments_id(self):
        tl = TodoList()
        a = tl.add("First")
        b = tl.add("Second")
        assert a.id == 1
        assert b.id == 2

    def test_add_max_items(self):
        tl = TodoList()
        for i in range(MAX_ITEMS):
            tl.add(f"Item {i + 1}")
        assert len(tl.items) == MAX_ITEMS
        with pytest.raises(ValueError, match="full"):
            tl.add("One too many")

    def test_progress_empty(self):
        tl = TodoList()
        assert tl.get_progress() == "0/0 completed"

    def test_progress_with_items(self):
        tl = TodoList()
        tl.add("A")
        tl.add("B")
        tl.add("C")
        tl.update(1, TodoStatus.COMPLETED)
        assert tl.get_progress() == "1/3 completed"

    def test_get_current_none(self):
        tl = TodoList()
        tl.add("A")
        assert tl.get_current() is None

    def test_get_current_returns_in_progress(self):
        tl = TodoList()
        tl.add("A")
        tl.add("B")
        tl.update(2, TodoStatus.IN_PROGRESS)
        current = tl.get_current()
        assert current is not None
        assert current.id == 2


# ─────────────────────────────────────────────────────────────
# TodoList — status transitions and constraints
# ─────────────────────────────────────────────────────────────


class TestTodoListConstraints:
    def test_not_started_to_in_progress(self):
        tl = TodoList()
        tl.add("Task")
        tl.update(1, TodoStatus.IN_PROGRESS)
        assert tl.items[0].status == TodoStatus.IN_PROGRESS

    def test_in_progress_to_completed(self):
        tl = TodoList()
        tl.add("Task")
        tl.update(1, TodoStatus.IN_PROGRESS)
        tl.update(1, TodoStatus.COMPLETED)
        assert tl.items[0].status == TodoStatus.COMPLETED
        assert tl.items[0].completed_at is not None

    def test_single_in_progress_constraint(self):
        tl = TodoList()
        tl.add("A")
        tl.add("B")
        tl.update(1, TodoStatus.IN_PROGRESS)
        with pytest.raises(ValueError, match="already in-progress"):
            tl.update(2, TodoStatus.IN_PROGRESS)

    def test_same_item_in_progress_no_error(self):
        tl = TodoList()
        tl.add("A")
        tl.update(1, TodoStatus.IN_PROGRESS)
        # Re-setting same item to in-progress is fine
        tl.update(1, TodoStatus.IN_PROGRESS)
        assert tl.items[0].status == TodoStatus.IN_PROGRESS

    def test_update_nonexistent_raises(self):
        tl = TodoList()
        tl.add("A")
        with pytest.raises(KeyError, match="99"):
            tl.update(99, TodoStatus.COMPLETED)

    def test_back_to_not_started(self):
        tl = TodoList()
        tl.add("A")
        tl.update(1, TodoStatus.IN_PROGRESS)
        tl.update(1, TodoStatus.NOT_STARTED)
        assert tl.items[0].status == TodoStatus.NOT_STARTED


# ─────────────────────────────────────────────────────────────
# TodoList — verification nudge
# ─────────────────────────────────────────────────────────────


class TestVerificationNudge:
    def test_no_nudge_before_interval(self):
        tl = TodoList()
        for i in range(NUDGE_INTERVAL - 1):
            tl.add(f"Task {i + 1}")
        for i in range(1, NUDGE_INTERVAL):
            nudge = tl.update(i, TodoStatus.COMPLETED)
            assert nudge is None

    def test_nudge_at_interval(self):
        tl = TodoList()
        for i in range(NUDGE_INTERVAL):
            tl.add(f"Task {i + 1}")
        for i in range(1, NUDGE_INTERVAL):
            tl.update(i, TodoStatus.COMPLETED)
        nudge = tl.update(NUDGE_INTERVAL, TodoStatus.COMPLETED)
        assert nudge is not None
        assert "completed" in nudge.lower()
        assert "verif" in nudge.lower()

    def test_nudge_at_double_interval(self):
        tl = TodoList()
        count = NUDGE_INTERVAL * 2
        for i in range(count):
            tl.add(f"Task {i + 1}")
        nudges = []
        for i in range(1, count + 1):
            nudge = tl.update(i, TodoStatus.COMPLETED)
            if nudge is not None:
                nudges.append(nudge)
        assert len(nudges) == 2


# ─────────────────────────────────────────────────────────────
# TodoList — display format
# ─────────────────────────────────────────────────────────────


class TestDisplayFormat:
    def test_empty_display(self):
        tl = TodoList()
        text = tl.format_display()
        assert "empty" in text.lower()

    def test_display_has_emoji(self):
        tl = TodoList()
        tl.add("Read code")
        tl.add("Write tests")
        tl.update(1, TodoStatus.COMPLETED)
        text = tl.format_display()
        assert "✅" in text
        assert "⬜" in text

    def test_display_has_progress(self):
        tl = TodoList()
        tl.add("A")
        tl.add("B")
        tl.update(1, TodoStatus.IN_PROGRESS)
        text = tl.format_display()
        assert "0/2 completed" in text
        assert "🔄" in text

    def test_display_item_titles(self):
        tl = TodoList()
        tl.add("First thing")
        tl.add("Second thing")
        text = tl.format_display()
        assert "First thing" in text
        assert "Second thing" in text


# ─────────────────────────────────────────────────────────────
# TodoList — serialisation
# ─────────────────────────────────────────────────────────────


class TestTodoListSerialisation:
    def test_to_dict(self):
        tl = TodoList(session_id="sess123")
        tl.add("A")
        tl.add("B")
        d = tl.to_dict()
        assert d["session_id"] == "sess123"
        assert len(d["items"]) == 2
        assert d["next_id"] == 3

    def test_from_dict_roundtrip(self):
        tl = TodoList(session_id="s1")
        tl.add("Read files")
        tl.add("Write code")
        tl.update(1, TodoStatus.COMPLETED)
        d = tl.to_dict()
        restored = TodoList.from_dict(d)
        assert restored.session_id == "s1"
        assert len(restored.items) == 2
        assert restored.items[0].status == TodoStatus.COMPLETED
        assert restored._next_id == 3

    def test_from_dict_preserves_completion_count(self):
        tl = TodoList()
        for i in range(4):
            tl.add(f"T{i}")
        tl.update(1, TodoStatus.COMPLETED)
        tl.update(2, TodoStatus.COMPLETED)
        d = tl.to_dict()
        restored = TodoList.from_dict(d)
        assert restored._completion_count == 2


# ─────────────────────────────────────────────────────────────
# TodoPersistence
# ─────────────────────────────────────────────────────────────


class TestTodoPersistence:
    def test_save_creates_file(self, tmp_path):
        p = TodoPersistence(tmp_path)
        tl = TodoList(session_id="s1")
        tl.add("A")
        p.save(tl)
        assert p.file.exists()
        lines = p.file.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_load_latest_empty(self, tmp_path):
        p = TodoPersistence(tmp_path)
        assert p.load_latest() is None

    def test_save_load_roundtrip(self, tmp_path):
        p = TodoPersistence(tmp_path)
        tl = TodoList(session_id="s1")
        tl.add("Task A")
        tl.add("Task B")
        tl.update(1, TodoStatus.COMPLETED)
        p.save(tl)
        restored = p.load_latest()
        assert restored is not None
        assert restored.session_id == "s1"
        assert len(restored.items) == 2
        assert restored.items[0].status == TodoStatus.COMPLETED

    def test_multiple_saves_loads_latest(self, tmp_path):
        p = TodoPersistence(tmp_path)
        tl1 = TodoList(session_id="s1")
        tl1.add("Old task")
        p.save(tl1)

        tl2 = TodoList(session_id="s2")
        tl2.add("New task A")
        tl2.add("New task B")
        p.save(tl2)

        restored = p.load_latest()
        assert restored is not None
        assert restored.session_id == "s2"
        assert len(restored.items) == 2

    def test_load_corrupted_returns_none(self, tmp_path):
        p = TodoPersistence(tmp_path)
        p.file.write_text("not valid json\n")
        assert p.load_latest() is None

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        p = TodoPersistence(nested)
        tl = TodoList()
        tl.add("X")
        p.save(tl)
        assert p.file.exists()


# ─────────────────────────────────────────────────────────────
# Todo Tools — todo_create
# ─────────────────────────────────────────────────────────────


class TestTodoCreateTool:
    def _run(self, args):
        from navig.agent.tools.todo_tools import TodoCreateTool

        tool = TodoCreateTool()
        return asyncio.run(tool.run(args))

    def setup_method(self):
        from navig.agent.tools.todo_tools import set_todo_list

        set_todo_list(TodoList(session_id="test"))

    def test_create_json_array(self):
        result = self._run({"items": '[{"title": "Read code"}, {"title": "Write tests"}]'})
        assert result.success
        assert "2 items" in result.output

    def test_create_string_array(self):
        result = self._run({"items": '["Read code", "Write tests"]'})
        assert result.success
        assert "2 items" in result.output

    def test_create_comma_separated(self):
        result = self._run({"items": "Read code, Write tests, Run checks"})
        assert result.success
        assert "3 items" in result.output

    def test_create_empty_fails(self):
        result = self._run({"items": ""})
        assert not result.success

    def test_create_too_many_fails(self):
        titles = [f"Item {i}" for i in range(MAX_ITEMS + 1)]
        result = self._run({"items": json.dumps(titles)})
        assert not result.success
        assert "Maximum" in result.output

    def test_create_replaces_old_list(self):
        from navig.agent.tools.todo_tools import get_todo_list

        self._run({"items": '["First"]'})
        tl1 = get_todo_list()
        assert len(tl1.items) == 1

        self._run({"items": '["A", "B", "C"]'})
        tl2 = get_todo_list()
        assert len(tl2.items) == 3

    def test_create_shows_display(self):
        result = self._run({"items": '["Read code"]'})
        assert "📋" in result.output


# ─────────────────────────────────────────────────────────────
# Todo Tools — todo_update
# ─────────────────────────────────────────────────────────────


class TestTodoUpdateTool:
    def _run(self, args):
        from navig.agent.tools.todo_tools import TodoUpdateTool

        tool = TodoUpdateTool()
        return asyncio.run(tool.run(args))

    def setup_method(self):
        from navig.agent.tools.todo_tools import set_todo_list

        tl = TodoList(session_id="test")
        tl.add("Task A")
        tl.add("Task B")
        tl.add("Task C")
        set_todo_list(tl)

    def test_update_to_in_progress(self):
        result = self._run({"id": 1, "status": "in-progress"})
        assert result.success
        assert "in-progress" in result.output

    def test_update_to_completed(self):
        result = self._run({"id": 1, "status": "completed"})
        assert result.success
        assert "completed" in result.output

    def test_update_missing_id(self):
        result = self._run({"status": "completed"})
        assert not result.success

    def test_update_invalid_status(self):
        result = self._run({"id": 1, "status": "running"})
        assert not result.success
        assert "Invalid status" in result.output

    def test_update_nonexistent_id(self):
        result = self._run({"id": 99, "status": "completed"})
        assert not result.success

    def test_update_string_id(self):
        """Tool should handle string ids from LLM."""
        result = self._run({"id": "2", "status": "in-progress"})
        assert result.success

    def test_update_constraint_violation(self):
        self._run({"id": 1, "status": "in-progress"})
        result = self._run({"id": 2, "status": "in-progress"})
        assert not result.success
        assert "already in-progress" in result.output


# ─────────────────────────────────────────────────────────────
# Todo Tools — todo_show
# ─────────────────────────────────────────────────────────────


class TestTodoShowTool:
    def _run(self):
        from navig.agent.tools.todo_tools import TodoShowTool

        tool = TodoShowTool()
        return asyncio.run(tool.run({}))

    def setup_method(self):
        from navig.agent.tools.todo_tools import set_todo_list

        tl = TodoList(session_id="test")
        tl.add("Read code")
        tl.add("Write tests")
        set_todo_list(tl)

    def test_show_returns_display(self):
        result = self._run()
        assert result.success
        assert "📋" in result.output
        assert "Read code" in result.output

    def test_show_after_updates(self):
        from navig.agent.tools.todo_tools import TodoUpdateTool

        update = TodoUpdateTool()
        asyncio.run(update.run({"id": 1, "status": "completed"}))
        result = self._run()
        assert "✅" in result.output
        assert "1/2 completed" in result.output


# ─────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────


class TestTodoToolsRegistration:
    def test_register_adds_tools(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.todo_tools import (
            TodoCreateTool,
            TodoShowTool,
            TodoUpdateTool,
        )

        registry = AgentToolRegistry()
        registry.register(TodoCreateTool(), toolset="todo")
        registry.register(TodoUpdateTool(), toolset="todo")
        registry.register(TodoShowTool(), toolset="todo")

        names = registry.available_names()
        assert "todo_create" in names
        assert "todo_update" in names
        assert "todo_show" in names

    def test_register_todo_tools_helper(self):
        import navig.agent.agent_tool_registry as mod
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.todo_tools import register_todo_tools

        old = mod._AGENT_REGISTRY
        try:
            mod._AGENT_REGISTRY = AgentToolRegistry()
            tl = TodoList(session_id="test")
            register_todo_tools(tl)
            names = mod._AGENT_REGISTRY.available_names()
            assert "todo_create" in names
            assert "todo_update" in names
            assert "todo_show" in names
        finally:
            mod._AGENT_REGISTRY = old
