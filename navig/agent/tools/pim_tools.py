"""Agent tools for the operator's REAL task list.

⚠ Not to be confused with ``todo_tools`` (``todo_create`` / ``todo_update`` /
``todo_show``), which are the agent's own per-conversation plan checklist: those live
in memory, are keyed by session, and vanish when the chat ends. These write to the
persistent list behind `/todo` in Telegram and `navig todo` — the one the operator
actually lives out of.

Both exist in the same registry, so every description below says which is which. A
model that files a user's dentist appointment into a scratch checklist has lost it, and
one that fills the real list with its own plan steps has made it useless.

**Adding is not deciding.** An agent working inside a space can see work that needs
doing; it cannot know whether the operator wants it on their personal list. So a tool
add is recorded with ``origin='agent'`` and renders with ✨ — visibly a suggestion the
operator can dismiss, never indistinguishable from something they wrote.

There is deliberately NO delete tool. Ticking something off is reversible (`reopen`);
deleting is not, and an agent that mistakes one task for another should not be able to
destroy a commitment the operator made.

⚠ ``ToolResult`` takes a REQUIRED ``name`` and has no ``data`` field. Getting either
wrong is a construction that can only ever raise — the exact `call-arg` class this
repo gates — and it is invisible in review because the call reads perfectly. Every
result here therefore passes ``name=self.name`` and puts everything the model needs,
ids included, in ``output``.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_TITLE_CHARS = 300
_LIST_LIMIT = 50

_SHARED_NOTE = (
    "This is the operator's PERSISTENT personal task list (survives the conversation, "
    "same list as /todo in Telegram and `navig todo`). For a scratch checklist that "
    "belongs to this conversation only, use todo_create/todo_update instead."
)


def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


class TaskAddTool(BaseTool):
    """Put one task on the operator's real list."""

    name = "task_add"
    description = (
        "Add a task to the operator's personal task list. The date may be part of the "
        "text ('Dentist tomorrow 10:30', 'Renew the domain next friday') or left out "
        "entirely, in which case it lands in the inbox. " + _SHARED_NOTE
    )
    parameters: dict[str, Any] = {
        "title": {
            "type": "string",
            "description": (
                "The task, optionally with when it is due — 'Call the accountant "
                "tomorrow 9am'. A date is read off the END of the text only, so a "
                "month name in the middle of a sentence is left alone."
            ),
        },
        "category": {
            "type": "string",
            "description": "life · business · project · rendezvous, or any the operator uses.",
        },
        "space": {
            "type": "string",
            "description": "The space this came from, when it came from one.",
        },
        "notes": {"type": "string", "description": "Anything the title cannot hold."},
        "source": {
            "type": "string",
            "description": (
                "A stable identifier for where this came from (e.g. "
                "'homelab-space:CURRENT_PHASE.md:14'). Given one, the same source is "
                "never added twice — including after the operator deletes it, so a "
                "dismissed suggestion stays dismissed."
            ),
        },
    }

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        title = str(args.get("title") or "").strip()
        if not title:
            return ToolResult(name=self.name, success=False, error="title is required")
        if len(title) > _MAX_TITLE_CHARS:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"title is too long (max {_MAX_TITLE_CHARS} characters)",
            )

        try:
            from navig.pim.clock import local_now, to_utc_iso  # noqa: PLC0415
            from navig.pim.dates import (  # noqa: PLC0415
                format_local,
                split_due,
                split_recurrence,
            )

            store = _store()
            source = str(args.get("source") or "").strip() or None
            if source and store.todo_exists_for_source(source):
                # Not an error: re-running a scan is normal, and the honest answer is
                # "already handled" rather than a second identical row.
                return ToolResult(
                    name=self.name,
                    success=True,
                    output=f"Already on the list (or previously dismissed): {source}",
                )

            now = local_now()
            rest, recur = split_recurrence(title)
            clean_title, due = split_due(rest, now)
            clean_title = clean_title.strip() or title

            todo = store.create_todo(
                clean_title,
                category=str(args.get("category") or ""),
                due_at=to_utc_iso(due) if due else None,
                recur=recur,
                notes=str(args.get("notes") or ""),
                space=str(args.get("space") or "") or None,
                origin="agent",
                origin_ref=source,
            )
        except Exception as exc:  # noqa: BLE001 — a tool must never raise into the agent loop
            logger.exception("task_add failed")
            return ToolResult(
                name=self.name, success=False, error=f"could not add the task: {exc}"
            )

        when = f" (due {format_local(due, now)})" if due else " (in the inbox — no date)"
        return ToolResult(
            name=self.name,
            success=True,
            output=(
                f"Added: {todo['title']}{when}. id={todo['id']}. Marked as a suggestion "
                "for the operator to confirm."
            ),
        )


class TaskListTool(BaseTool):
    """Read the operator's list, so the agent can answer questions about it."""

    name = "task_list"
    description = (
        "Read the operator's personal task list — what is overdue, due today, or sitting "
        "in the inbox with no date. Read-only. " + _SHARED_NOTE
    )
    parameters: dict[str, Any] = {
        "category": {"type": "string", "description": "Only this category."},
        "space": {"type": "string", "description": "Only tasks linked to this space."},
        "include_done": {
            "type": "boolean",
            "description": "Include completed tasks. Defaults to false.",
        },
    }

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        try:
            from navig.core.coerce import coerce_bool  # noqa: PLC0415
            from navig.pim.clock import local_now  # noqa: PLC0415
            from navig.pim.render import bucket_of  # noqa: PLC0415

            todos = _store().list_todos(
                category=(str(args["category"]) if args.get("category") else None),
                space=(str(args["space"]) if args.get("space") else None),
                include_done=coerce_bool(args.get("include_done"), default=False),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("task_list failed")
            return ToolResult(
                name=self.name, success=False, error=f"could not read the list: {exc}"
            )

        if not todos:
            return ToolResult(name=self.name, success=True, output="The task list is empty.")

        now = local_now()
        shown = todos[:_LIST_LIMIT]
        lines = [
            f"- {t['id']} [{bucket_of(t, now)}] {t['title']}"
            + (f" (due {t['due_at']})" if t.get("due_at") else "")
            + (f" [{t['category']}]" if t.get("category") else "")
            for t in shown
        ]
        if len(todos) > _LIST_LIMIT:
            # Say what was dropped. A silently truncated list reads as the whole list,
            # and an agent will then confidently report a task does not exist.
            lines.append(
                f"… and {len(todos) - _LIST_LIMIT} more (showing the {_LIST_LIMIT} soonest)"
            )
        return ToolResult(name=self.name, success=True, output="\n".join(lines))


class TaskDoneTool(BaseTool):
    """Tick one off. Reversible, which is why it needs no approval gate."""

    name = "task_done"
    description = (
        "Mark a task on the operator's personal list as done, by its id (from "
        "task_list). A recurring task rolls forward to its next occurrence instead of "
        "leaving the list. Reversible — the operator can reopen it. " + _SHARED_NOTE
    )
    parameters: dict[str, Any] = {
        "id": {"type": "string", "description": "The task id, from task_list."},
    }

    async def run(
        self, args: dict[str, Any], on_status: StatusCallback | None = None
    ) -> ToolResult:
        task_id = str(args.get("id") or "").strip()
        if not task_id:
            return ToolResult(
                name=self.name, success=False, error="id is required (get it from task_list)"
            )

        try:
            store = _store()
            if store.get_todo(task_id) is None:
                # An explicit miss, not a silent no-op: the agent must be able to tell
                # "done" from "there was nothing there".
                return ToolResult(
                    name=self.name, success=False, error=f"no task with id {task_id!r}"
                )
            todo = store.complete_todo(task_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("task_done failed")
            return ToolResult(
                name=self.name, success=False, error=f"could not complete the task: {exc}"
            )

        if todo is None:
            return ToolResult(name=self.name, success=False, error=f"no task with id {task_id!r}")
        if todo.get("completed_at"):
            return ToolResult(name=self.name, success=True, output=f"Done: {todo['title']}")
        return ToolResult(
            name=self.name,
            success=True,
            output=(
                f"Done: {todo['title']} — it repeats {todo.get('recur')}, "
                f"next due {todo.get('due_at')}"
            ),
        )


def register_pim_tools() -> None:
    """Register task_add / task_list / task_done.

    Toolset ``tasks``, deliberately NOT ``todo``: that name already belongs to the
    ephemeral per-conversation checklist, and two toolsets with one name is how an
    operator switching one off silently switches off the other.
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY  # noqa: PLC0415

    _AGENT_REGISTRY.register(TaskAddTool(), toolset="tasks")
    _AGENT_REGISTRY.register(TaskListTool(), toolset="tasks")
    _AGENT_REGISTRY.register(TaskDoneTool(), toolset="tasks")
    logger.debug("PIM agent tools registered: task_add, task_list, task_done")
