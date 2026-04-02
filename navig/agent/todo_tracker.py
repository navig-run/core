"""
navig.agent.todo_tracker — Persistent todo list for multi-step agent work.

Provides a visible progress tracker that the agent maintains across turns.
The agent creates todos before starting, marks them in-progress/completed
as it works, giving users real-time visibility into progress.

Constraints:
- Maximum 15 todo items per list
- Only 1 item can be ``in-progress`` at a time
- Creating a new list replaces the old one entirely
- Titles limited to 50 characters
- Verification nudge emitted every 3 completions

FA-03 implementation.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_ITEMS = 15
MAX_TITLE_LENGTH = 50
NUDGE_INTERVAL = 3


# ─────────────────────────────────────────────────────────────
# Status enum
# ─────────────────────────────────────────────────────────────


class TodoStatus(Enum):
    """Status for a single todo item."""

    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"


# ─────────────────────────────────────────────────────────────
# Status display mapping
# ─────────────────────────────────────────────────────────────

_STATUS_EMOJI: dict[TodoStatus, str] = {
    TodoStatus.NOT_STARTED: "⬜",
    TodoStatus.IN_PROGRESS: "🔄",
    TodoStatus.COMPLETED: "✅",
}


# ─────────────────────────────────────────────────────────────
# TodoItem
# ─────────────────────────────────────────────────────────────


@dataclass
class TodoItem:
    """A single actionable item in a todo list."""

    id: int
    title: str
    status: TodoStatus = TodoStatus.NOT_STARTED
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def __post_init__(self) -> None:
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValueError(
                f"Title exceeds {MAX_TITLE_LENGTH} characters: "
                f"{len(self.title)} chars"
            )
        if not self.title.strip():
            raise ValueError("Title cannot be empty")
        if not isinstance(self.status, TodoStatus):
            raise ValueError(f"Invalid status: {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        """Deserialise from a dict."""
        return cls(
            id=data["id"],
            title=data["title"],
            status=TodoStatus(data["status"]),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at"),
        )


# ─────────────────────────────────────────────────────────────
# TodoList
# ─────────────────────────────────────────────────────────────


@dataclass
class TodoList:
    """Ordered collection of todo items with progress tracking."""

    items: list[TodoItem] = field(default_factory=list)
    session_id: str = ""
    _next_id: int = field(default=1, repr=False)
    _completion_count: int = field(default=0, repr=False)

    def add(self, title: str) -> TodoItem:
        """Add a new item.  Returns the created ``TodoItem``.

        Raises ``ValueError`` if the list would exceed ``MAX_ITEMS``.
        """
        if len(self.items) >= MAX_ITEMS:
            raise ValueError(f"Todo list is full (max {MAX_ITEMS} items)")
        item = TodoItem(id=self._next_id, title=title)
        self._next_id += 1
        self.items.append(item)
        return item

    def update(self, item_id: int, status: TodoStatus) -> Optional[str]:
        """Update the status of an item by id.

        Enforces the single-in-progress constraint: setting an item
        to ``IN_PROGRESS`` will fail if another item is already in-progress.

        Returns a verification nudge string every ``NUDGE_INTERVAL``
        completions, or ``None``.

        Raises ``KeyError`` if *item_id* not found.
        Raises ``ValueError`` if the constraint is violated.
        """
        item = self._find(item_id)

        # Single in-progress constraint
        if status == TodoStatus.IN_PROGRESS:
            current = self.get_current()
            if current is not None and current.id != item_id:
                raise ValueError(
                    f"Item {current.id} ({current.title!r}) is already in-progress. "
                    "Complete or reset it first."
                )

        item.status = status
        nudge: Optional[str] = None

        if status == TodoStatus.COMPLETED:
            item.completed_at = time.time()
            self._completion_count += 1
            if self._completion_count % NUDGE_INTERVAL == 0:
                nudge = (
                    f"You have completed {self._completion_count} todos. "
                    "Consider verifying your work before proceeding. "
                    "Run tests, check for errors, or review changes."
                )

        return nudge

    def get_progress(self) -> str:
        """Human-readable progress string, e.g. ``'3/7 completed'``."""
        completed = sum(1 for i in self.items if i.status == TodoStatus.COMPLETED)
        return f"{completed}/{len(self.items)} completed"

    def get_current(self) -> Optional[TodoItem]:
        """Return the in-progress item, or ``None``."""
        for item in self.items:
            if item.status == TodoStatus.IN_PROGRESS:
                return item
        return None

    def format_display(self) -> str:
        """Render the todo list with emoji status indicators."""
        if not self.items:
            return "📋 Todo List (empty)"

        lines = [f"📋 Todo List ({self.get_progress()})"]
        for item in self.items:
            emoji = _STATUS_EMOJI.get(item.status, "⬜")
            lines.append(f"  {emoji} {item.id}. {item.title}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire list."""
        return {
            "session_id": self.session_id,
            "items": [item.to_dict() for item in self.items],
            "next_id": self._next_id,
            "completion_count": self._completion_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoList:
        """Deserialise from a dict."""
        todo = cls(
            session_id=data.get("session_id", ""),
            items=[TodoItem.from_dict(d) for d in data.get("items", [])],
        )
        todo._next_id = data.get("next_id", len(todo.items) + 1)
        todo._completion_count = data.get("completion_count", 0)
        return todo

    # ── internal ──

    def _find(self, item_id: int) -> TodoItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(f"No todo item with id {item_id}")


# ─────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────


class TodoPersistence:
    """Append-only JSONL persistence for todo lists.

    Each ``save()`` appends a full snapshot; ``load_latest()`` reads the
    last line to restore the most recent state.
    """

    def __init__(self, session_dir: Path) -> None:
        self.file = session_dir / "todos.jsonl"

    def save(self, todo_list: TodoList) -> None:
        """Append a snapshot of the todo list."""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(todo_list.to_dict()) + "\n")

    def load_latest(self) -> Optional[TodoList]:
        """Load the most recent snapshot, or ``None`` if nothing saved."""
        if not self.file.exists():
            return None
        text = self.file.read_text(encoding="utf-8").strip()
        if not text:
            return None
        last_line = text.split("\n")[-1]
        try:
            return TodoList.from_dict(json.loads(last_line))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to load todo list from %s: %s", self.file, exc)
            return None
