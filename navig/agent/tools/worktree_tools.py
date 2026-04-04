"""
navig.agent.tools.worktree_tools — Agent tools for git worktree isolation.

Provides four tools for the LLM to manage isolated worktrees:

* ``worktree_create``  — create a new worktree branch
* ``worktree_list``    — list active worktrees
* ``worktree_merge``   — merge a worktree back into the target branch
* ``worktree_remove``  — remove a worktree and its branch

These tools are registered in the ``"worktree"`` toolset.

FB-05 implementation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


def _get_manager():
    """Lazy import to avoid circular deps at module load time.

    If the singleton has not been initialised yet (no ``repo_root``),
    we attempt to discover it from ``os.getcwd()``.
    """
    import os

    from navig.agent.worktree import _manager, get_manager, reset_manager

    if _manager is None:
        # Try current working directory as repo root
        cwd = os.getcwd()
        return get_manager(repo_root=cwd)
    return get_manager()


# ─────────────────────────────────────────────────────────────
# worktree_create
# ─────────────────────────────────────────────────────────────


class WorktreeCreateTool(BaseTool):
    """Create an isolated git worktree for a parallel task."""

    name = "worktree_create"
    description = (
        "Create a new git worktree with its own branch for isolated work. "
        "Returns the worktree path and branch name.  The worker can then "
        "operate in that directory without conflicting with the main repo."
    )
    owner_only = False
    parameters = [
        {
            "name": "name",
            "type": "string",
            "description": (
                "Unique worker name (alphanumeric, hyphens, underscores). "
                "Used as directory name and branch suffix."
            ),
            "required": True,
        },
        {
            "name": "base_branch",
            "type": "string",
            "description": (
                "Git ref to branch from.  Defaults to HEAD (current branch)."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        name = args.get("name", "").strip()
        if not name:
            return ToolResult(
                name=self.name,
                success=False,
                error="'name' parameter is required and must not be empty.",
            )

        base_branch = args.get("base_branch", "HEAD") or "HEAD"

        try:
            manager = _get_manager()
            await self._emit(on_status, "creating", f"worktree '{name}'")
            wt = await manager.create(name=name, base_branch=base_branch)
        except (RuntimeError, ValueError, OSError) as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))

        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(wt.to_dict(), indent=2),
        )


# ─────────────────────────────────────────────────────────────
# worktree_list
# ─────────────────────────────────────────────────────────────


class WorktreeListTool(BaseTool):
    """List all active git worktrees managed by navig."""

    name = "worktree_list"
    description = (
        "List all active worktrees with their paths, branches, ages, "
        "and merge status."
    )
    owner_only = False
    parameters = []

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        try:
            manager = _get_manager()
            items = manager.list_worktrees()
        except (RuntimeError, ValueError, OSError) as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))

        if not items:
            return ToolResult(
                name=self.name,
                success=True,
                output="No active worktrees.",
            )

        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(items, indent=2),
        )


# ─────────────────────────────────────────────────────────────
# worktree_merge
# ─────────────────────────────────────────────────────────────


class WorktreeMergeTool(BaseTool):
    """Merge a worktree branch back into the target branch."""

    name = "worktree_merge"
    description = (
        "Merge changes from a worktree's branch back into the target "
        "branch (defaults to the current branch).  Returns whether the "
        "merge succeeded.  On conflict, the merge is aborted."
    )
    owner_only = False
    parameters = [
        {
            "name": "name",
            "type": "string",
            "description": "Name of the worktree to merge.",
            "required": True,
        },
        {
            "name": "target_branch",
            "type": "string",
            "description": (
                "Branch to merge into.  Defaults to the current branch."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        name = args.get("name", "").strip()
        if not name:
            return ToolResult(
                name=self.name,
                success=False,
                error="'name' parameter is required.",
            )

        target_branch = args.get("target_branch", "") or ""

        try:
            manager = _get_manager()
            await self._emit(on_status, "merging", f"worktree '{name}'")
            ok = await manager.merge_back(name=name, target_branch=target_branch)
        except (RuntimeError, ValueError, OSError) as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))

        if ok:
            return ToolResult(
                name=self.name,
                success=True,
                output=f"Worktree '{name}' merged successfully.",
            )
        else:
            return ToolResult(
                name=self.name,
                success=False,
                error=(
                    f"Merge conflict for worktree '{name}'. "
                    "The merge was aborted.  Manual resolution is needed."
                ),
            )


# ─────────────────────────────────────────────────────────────
# worktree_remove
# ─────────────────────────────────────────────────────────────


class WorktreeRemoveTool(BaseTool):
    """Remove a worktree and delete its branch."""

    name = "worktree_remove"
    description = (
        "Remove a worktree directory and its associated branch.  "
        "Use force=true to remove even if there are uncommitted changes."
    )
    owner_only = False
    parameters = [
        {
            "name": "name",
            "type": "string",
            "description": "Name of the worktree to remove.",
            "required": True,
        },
        {
            "name": "force",
            "type": "boolean",
            "description": (
                "Force removal even with uncommitted changes.  Default false."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        name = args.get("name", "").strip()
        if not name:
            return ToolResult(
                name=self.name,
                success=False,
                error="'name' parameter is required.",
            )

        force = bool(args.get("force", False))

        try:
            manager = _get_manager()
            await self._emit(on_status, "removing", f"worktree '{name}'")
            await manager.remove(name=name, force=force)
        except (RuntimeError, ValueError, OSError) as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))

        return ToolResult(
            name=self.name,
            success=True,
            output=f"Worktree '{name}' removed.",
        )
