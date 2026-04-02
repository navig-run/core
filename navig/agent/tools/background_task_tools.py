"""
navig.agent.tools.background_task_tools — Agent tools for background tasks.

Provides four tools for the LLM to manage background processes:

* ``background_task_start``  — spawn a command in the background
* ``background_task_status`` — check a task's status
* ``background_task_output`` — retrieve tail output lines
* ``background_task_kill``   — terminate a running task

These tools are registered in the ``"background_task"`` toolset.

FB-04 implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


def _get_manager():
    """Lazy import to avoid circular imports at module load time."""
    from navig.agent.background_task import get_manager

    return get_manager()


# ─────────────────────────────────────────────────────────────
# background_task_start
# ─────────────────────────────────────────────────────────────


class BackgroundTaskStartTool(BaseTool):
    """Start a long-running command in the background."""

    name = "background_task_start"
    description = (
        "Start a long-running command (test suite, build, server, etc.) "
        "in the background. Returns immediately with a task ID that can "
        "be used to check status, read output, or kill the task."
    )
    owner_only = False
    parameters = [
        {
            "name": "command",
            "type": "string",
            "description": "Shell command to run in the background.",
            "required": True,
        },
        {
            "name": "label",
            "type": "string",
            "description": (
                "Short human-readable label for the task (e.g. 'test suite'). "
                "Defaults to the first 40 chars of the command."
            ),
            "required": False,
        },
        {
            "name": "cwd",
            "type": "string",
            "description": "Working directory for the command. Defaults to current directory.",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        command = args.get("command", "").strip()
        if not command:
            return ToolResult(
                name=self.name,
                success=False,
                error="'command' parameter is required and must not be empty.",
            )

        label = args.get("label", "")
        cwd = args.get("cwd") or None

        try:
            manager = _get_manager()
            task = await manager.start(command=command, label=label, cwd=cwd)
        except (RuntimeError, ValueError, OSError) as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
            )

        return ToolResult(
            name=self.name,
            success=True,
            output=(
                f"Started background task #{task.task_id}: {task.label} "
                f"(pid {task.pid})"
            ),
        )


# ─────────────────────────────────────────────────────────────
# background_task_status
# ─────────────────────────────────────────────────────────────


class BackgroundTaskStatusTool(BaseTool):
    """Check status of one or all background tasks."""

    name = "background_task_status"
    description = (
        "Check the status of a background task by its ID, or list all tasks "
        "if no task_id is provided.  Returns JSON with status, duration, "
        "exit code, and output line count."
    )
    owner_only = False
    parameters = [
        {
            "name": "task_id",
            "type": "integer",
            "description": (
                "Task ID to check. Omit or set to 0 to list all tasks."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        try:
            manager = _get_manager()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(name=self.name, success=False, error=str(exc))

        task_id = args.get("task_id")
        # Coerce to int
        if task_id is not None:
            try:
                task_id = int(task_id)
            except (TypeError, ValueError):
                task_id = 0

        if task_id and task_id > 0:
            info = manager.status(task_id)
            if "error" in info:
                return ToolResult(name=self.name, success=False, error=info["error"])
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps(info, indent=2),
            )

        # List all tasks
        tasks = manager.list_tasks()
        if not tasks:
            return ToolResult(
                name=self.name,
                success=True,
                output="No background tasks.",
            )
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(tasks, indent=2),
        )


# ─────────────────────────────────────────────────────────────
# background_task_output
# ─────────────────────────────────────────────────────────────


class BackgroundTaskOutputTool(BaseTool):
    """Get output from a background task."""

    name = "background_task_output"
    description = (
        "Retrieve the last N lines of output from a background task. "
        "Defaults to the last 50 lines."
    )
    owner_only = False
    parameters = [
        {
            "name": "task_id",
            "type": "integer",
            "description": "Task ID to read output from.",
            "required": True,
        },
        {
            "name": "tail",
            "type": "integer",
            "description": "Number of lines to return from the end (default 50).",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        task_id = args.get("task_id")
        if task_id is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="'task_id' parameter is required.",
            )

        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return ToolResult(
                name=self.name,
                success=False,
                error="'task_id' must be an integer.",
            )

        tail = 50
        raw_tail = args.get("tail")
        if raw_tail is not None:
            try:
                tail = int(raw_tail)
            except (TypeError, ValueError):
                pass

        try:
            manager = _get_manager()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(name=self.name, success=False, error=str(exc))

        output = manager.get_output(task_id, tail=tail)

        # Check if it's an error message from manager
        if output.startswith("No task with id"):
            return ToolResult(name=self.name, success=False, error=output)

        return ToolResult(name=self.name, success=True, output=output)


# ─────────────────────────────────────────────────────────────
# background_task_kill
# ─────────────────────────────────────────────────────────────


class BackgroundTaskKillTool(BaseTool):
    """Kill a running background task."""

    name = "background_task_kill"
    description = (
        "Terminate a running background task by its ID. Returns whether "
        "the task was successfully killed."
    )
    owner_only = False
    parameters = [
        {
            "name": "task_id",
            "type": "integer",
            "description": "Task ID to kill.",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        task_id = args.get("task_id")
        if task_id is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="'task_id' parameter is required.",
            )

        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return ToolResult(
                name=self.name,
                success=False,
                error="'task_id' must be an integer.",
            )

        try:
            manager = _get_manager()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(name=self.name, success=False, error=str(exc))

        killed = await manager.kill(task_id)

        if killed:
            return ToolResult(
                name=self.name,
                success=True,
                output=f"Task #{task_id} killed.",
            )
        return ToolResult(
            name=self.name,
            success=False,
            error=f"Task #{task_id} not found or already completed.",
        )
