"""
Hermetic unit tests for navig.agent.todo_tracker

Covers:
- TodoStatus enum values
- _STATUS_EMOJI mapping
- TodoItem: creation, validation, to_dict/from_dict
- TodoList: add, update, get_progress, get_current, MAX_ITEMS
"""

import time
import pytest

from navig.agent.todo_tracker import (
    MAX_ITEMS,
    MAX_TITLE_LENGTH,
    NUDGE_INTERVAL,
    TodoItem,
    TodoList,
    TodoStatus,
    _STATUS_EMOJI,
)


# ─────────────────────────────────────────────────────────────
# TodoStatus
# ─────────────────────────────────────────────────────────────


class TestTodoStatus:
    def test_not_started_value(self):
        assert TodoStatus.NOT_STARTED.value == "not-started"

    def test_in_progress_value(self):
        assert TodoStatus.IN_PROGRESS.value == "in-progress"

    def test_completed_value(self):
        assert TodoStatus.COMPLETED.value == "completed"

    def test_all_values_unique(self):
        vals = [s.value for s in TodoStatus]
        assert len(vals) == len(set(vals))


class TestStatusEmoji:
    def test_emoji_for_all_statuses(self):
        for status in TodoStatus:
            assert status in _STATUS_EMOJI

    def test_completed_emoji(self):
        assert _STATUS_EMOJI[TodoStatus.COMPLETED] == "✅"

    def test_in_progress_emoji(self):
        assert _STATUS_EMOJI[TodoStatus.IN_PROGRESS] == "🔄"

    def test_not_started_emoji(self):
        assert _STATUS_EMOJI[TodoStatus.NOT_STARTED] == "⬜"


# ─────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────


class TestConstants:
    def test_max_items(self):
        assert MAX_ITEMS == 15

    def test_max_title_length(self):
        assert MAX_TITLE_LENGTH == 50

    def test_nudge_interval(self):
        assert NUDGE_INTERVAL == 3


# ─────────────────────────────────────────────────────────────
# TodoItem
# ─────────────────────────────────────────────────────────────


class TestTodoItem:
    def test_basic_creation(self):
        item = TodoItem(id=1, title="Write tests")
        assert item.id == 1
        assert item.title == "Write tests"
        assert item.status == TodoStatus.NOT_STARTED
        assert item.completed_at is None

    def test_title_too_long_raises(self):
        with pytest.raises(ValueError, match="Title exceeds"):
            TodoItem(id=1, title="x" * 51)

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="empty"):
            TodoItem(id=1, title="   ")

    def test_to_dict_keys(self):
        item = TodoItem(id=2, title="Review PR")
        d = item.to_dict()
        assert d["id"] == 2
        assert d["title"] == "Review PR"
        assert d["status"] == "not-started"
        assert d["completed_at"] is None

    def test_from_dict_roundtrip(self):
        original = TodoItem(id=3, title="Deploy")
        d = original.to_dict()
        restored = TodoItem.from_dict(d)
        assert restored.id == 3
        assert restored.title == "Deploy"
        assert restored.status == TodoStatus.NOT_STARTED

    def test_from_dict_completed(self):
        now = time.time()
        d = {
            "id": 5,
            "title": "Done task",
            "status": "completed",
            "created_at": now - 100,
            "completed_at": now,
        }
        item = TodoItem.from_dict(d)
        assert item.status == TodoStatus.COMPLETED
        assert item.completed_at == now


# ─────────────────────────────────────────────────────────────
# TodoList
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def todo_list():
    return TodoList()


class TestTodoListAdd:
    def test_add_returns_item(self, todo_list):
        item = todo_list.add("First task")
        assert isinstance(item, TodoItem)
        assert item.title == "First task"

    def test_add_auto_increments_id(self, todo_list):
        a = todo_list.add("A")
        b = todo_list.add("B")
        assert b.id == a.id + 1

    def test_items_stored(self, todo_list):
        todo_list.add("T1")
        todo_list.add("T2")
        assert len(todo_list.items) == 2

    def test_add_beyond_max_raises(self, todo_list):
        for i in range(MAX_ITEMS):
            todo_list.add(f"Task {i}")
        with pytest.raises(ValueError, match="full"):
            todo_list.add("One too many")


class TestTodoListUpdate:
    def test_mark_in_progress(self, todo_list):
        item = todo_list.add("Work")
        todo_list.update(item.id, TodoStatus.IN_PROGRESS)
        assert item.status == TodoStatus.IN_PROGRESS

    def test_mark_completed_sets_completed_at(self, todo_list):
        item = todo_list.add("Task")
        todo_list.update(item.id, TodoStatus.COMPLETED)
        assert item.completed_at is not None

    def test_two_in_progress_raises(self, todo_list):
        a = todo_list.add("A")
        b = todo_list.add("B")
        todo_list.update(a.id, TodoStatus.IN_PROGRESS)
        with pytest.raises(ValueError, match="already in-progress"):
            todo_list.update(b.id, TodoStatus.IN_PROGRESS)

    def test_update_nonexistent_raises(self, todo_list):
        with pytest.raises(KeyError):
            todo_list.update(999, TodoStatus.COMPLETED)

    def test_nudge_emitted_on_nudge_interval(self, todo_list):
        items = [todo_list.add(f"T{i}") for i in range(NUDGE_INTERVAL)]
        nudge = None
        for item in items:
            result = todo_list.update(item.id, TodoStatus.COMPLETED)
            if result:
                nudge = result
        assert nudge is not None
        assert "completed" in nudge.lower() or "todos" in nudge.lower()

    def test_no_nudge_before_interval(self, todo_list):
        item = todo_list.add("Only one")
        result = todo_list.update(item.id, TodoStatus.COMPLETED)
        assert result is None  # not at interval boundary (1 < 3)


class TestTodoListProgress:
    def test_empty_progress(self, todo_list):
        assert todo_list.get_progress() == "0/0 completed"

    def test_partial_progress(self, todo_list):
        a = todo_list.add("A")
        todo_list.add("B")
        todo_list.update(a.id, TodoStatus.COMPLETED)
        assert todo_list.get_progress() == "1/2 completed"

    def test_full_progress(self, todo_list):
        items = [todo_list.add(f"T{i}") for i in range(3)]
        for item in items:
            todo_list.update(item.id, TodoStatus.COMPLETED)
        assert todo_list.get_progress() == "3/3 completed"


class TestTodoListGetCurrent:
    def test_none_when_nothing_in_progress(self, todo_list):
        todo_list.add("Idle")
        assert todo_list.get_current() is None

    def test_returns_in_progress_item(self, todo_list):
        item = todo_list.add("Active")
        todo_list.update(item.id, TodoStatus.IN_PROGRESS)
        current = todo_list.get_current()
        assert current is not None
        assert current.id == item.id
