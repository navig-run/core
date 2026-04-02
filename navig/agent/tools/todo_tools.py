"""
navig.agent.tools.todo_tools — Agent tools for the todo tracker.

Provides three tools for the LLM to manage a persistent todo list:

* ``todo_create``  — create (or replace) the todo list
* ``todo_update``  — update a single item's status
* ``todo_show``    — display the todo list with progress

These tools are registered in the ``"todo"`` toolset.

FA-03 implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Shared state — set by register_todo_tools()
# ─────────────────────────────────────────────────────────────

_todo_list_ref: Any = None  # Will be TodoList
_persistence_ref: Any = None  # Will be TodoPersistence or None


def set_todo_list(todo_list: Any, persistence: Any = None) -> None:
    """Bind the module-level todo list and optional persistence."""
    global _todo_list_ref, _persistence_ref
    _todo_list_ref = todo_list
    _persistence_ref = persistence


def get_todo_list() -> Any:
    """Return the active todo list, raising if not initialised."""
    if _todo_list_ref is None:
        raise RuntimeError("Todo tools not initialised — call set_todo_list() first.")
    return _todo_list_ref


def _auto_save() -> None:
    """Persist after mutation if a persistence backend is configured."""
    if _persistence_ref is not None and _todo_list_ref is not None:
        try:
            _persistence_ref.save(_todo_list_ref)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Auto-save failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# todo_create
# ─────────────────────────────────────────────────────────────


class TodoCreateTool(BaseTool):
    """Create or replace the todo list with a set of items."""

    name = "todo_create"
    description = (
        "Create a new todo list.  Provide a JSON array of item objects with 'title' "
        "(string, max 50 chars).  Replaces any existing list.  Max 15 items."
    )
    owner_only = False
    parameters = [
        {
            "name": "items",
            "type": "string",
            "description": (
                "JSON array of objects: [{\"title\": \"Read existing code\"}, ...]. "
                "Or a comma-separated list of titles."
            ),
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        import json as _json

        from navig.agent.todo_tracker import MAX_ITEMS, TodoList

        raw = args.get("items", "").strip()
        if not raw:
            return ToolResult(
                name=self.name,
                success=False,
                output="items is required.",
                error="missing items",
            )

        # Parse items — accept JSON array or comma-separated titles
        titles: list[str] = []
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                for entry in parsed:
                    if isinstance(entry, dict):
                        titles.append(entry.get("title", "").strip())
                    elif isinstance(entry, str):
                        titles.append(entry.strip())
            else:
                return ToolResult(
                    name=self.name,
                    success=False,
                    output="items must be a JSON array.",
                    error="invalid format",
                )
        except _json.JSONDecodeError:
            # Fall back to comma-separated
            titles = [t.strip() for t in raw.split(",") if t.strip()]

        if not titles:
            return ToolResult(
                name=self.name,
                success=False,
                output="No valid titles provided.",
                error="empty items",
            )

        if len(titles) > MAX_ITEMS:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Too many items ({len(titles)}). Maximum is {MAX_ITEMS}.",
                error="too many items",
            )

        # Create fresh list, replacing old one
        new_list = TodoList(session_id=get_todo_list().session_id)
        errors: list[str] = []
        for title in titles:
            try:
                new_list.add(title)
            except ValueError as exc:
                errors.append(str(exc))

        if errors and not new_list.items:
            return ToolResult(
                name=self.name,
                success=False,
                output="All items failed: " + "; ".join(errors),
                error="validation failed",
            )

        # Replace the global reference
        global _todo_list_ref
        _todo_list_ref = new_list
        _auto_save()

        result_lines = [f"Created todo list with {len(new_list.items)} items."]
        if errors:
            result_lines.append(f"Skipped {len(errors)} invalid items.")
        result_lines.append("")
        result_lines.append(new_list.format_display())

        return ToolResult(
            name=self.name,
            success=True,
            output="\n".join(result_lines),
        )


# ─────────────────────────────────────────────────────────────
# todo_update
# ─────────────────────────────────────────────────────────────


class TodoUpdateTool(BaseTool):
    """Update the status of a single todo item."""

    name = "todo_update"
    description = (
        "Update the status of a todo item.  Provide the item id and the new status: "
        "'not-started', 'in-progress', or 'completed'."
    )
    owner_only = False
    parameters = [
        {
            "name": "id",
            "type": "integer",
            "description": "The numeric id of the todo item to update.",
            "required": True,
        },
        {
            "name": "status",
            "type": "string",
            "description": "New status: not-started, in-progress, or completed",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.todo_tracker import TodoStatus

        todo_list = get_todo_list()

        item_id = args.get("id")
        if item_id is None:
            return ToolResult(
                name=self.name,
                success=False,
                output="id is required.",
                error="missing id",
            )
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Invalid id: {item_id!r}",
                error="invalid id",
            )

        status_raw = args.get("status", "").strip().lower()
        try:
            new_status = TodoStatus(status_raw)
        except ValueError:
            valid = ", ".join(s.value for s in TodoStatus)
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Invalid status: {status_raw!r}. Valid: {valid}",
                error="invalid status",
            )

        try:
            nudge = todo_list.update(item_id, new_status)
        except KeyError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=str(exc),
                error="not found",
            )
        except ValueError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=str(exc),
                error="constraint violation",
            )

        _auto_save()

        lines = [
            f"Item {item_id} → {new_status.value}",
            todo_list.get_progress(),
        ]
        if nudge:
            lines.append("")
            lines.append(f"⚠️ {nudge}")

        return ToolResult(name=self.name, success=True, output="\n".join(lines))


# ─────────────────────────────────────────────────────────────
# todo_show
# ─────────────────────────────────────────────────────────────


class TodoShowTool(BaseTool):
    """Display the current todo list with progress."""

    name = "todo_show"
    description = (
        "Show the current todo list with all items, their status, "
        "and overall progress."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        status_callback: StatusCallback | None = None,
    ) -> ToolResult:
        todo_list = get_todo_list()
        return ToolResult(
            name=self.name,
            success=True,
            output=todo_list.format_display(),
        )


# ─────────────────────────────────────────────────────────────
# Registration helper
# ─────────────────────────────────────────────────────────────


def register_todo_tools(
    todo_list: Any,
    persistence: Any = None,
) -> None:
    """Register todo tools in the agent registry bound to *todo_list*.

    The tools are placed in the ``"todo"`` toolset.
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    set_todo_list(todo_list, persistence)

    _AGENT_REGISTRY.register(TodoCreateTool(), toolset="todo")
    _AGENT_REGISTRY.register(TodoUpdateTool(), toolset="todo")
    _AGENT_REGISTRY.register(TodoShowTool(), toolset="todo")

    logger.debug("Todo tools registered: todo_create, todo_update, todo_show")
