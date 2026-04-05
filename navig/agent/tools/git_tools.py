"""
navig.agent.tools.git_tools — Git operation agent tools.

Provides five tools for the agentic ReAct loop to inspect and manipulate
the local git repository without leaving the agent session:

    git_status  — Working tree status, branch, ahead/behind
    git_diff    — File changes (working tree or staged)
    git_log     — Recent commit history
    git_commit  — Stage files and create a commit (RISKY — requires approval)
    git_stash   — Stash management (push / pop / list)

All read-only tools (git_status, git_diff, git_log, git_stash list) are
declared in :attr:`~navig.agent.plan_mode.PlanInterceptor.READ_ONLY_TOOLS`
so they may be used during the planning phase.

``git_commit`` is ``owner_only = True`` and gated by the safety approval system.

Usage::

    from navig.agent.tools import register_git_tools
    register_git_tools()
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from navig.agent.tool_caps import cap_result
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30  # seconds


def _find_git_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* to find the nearest ``.git`` directory."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / ".git").exists():
            return directory
    return None


def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    """Run a git command and return ``(success, output)``."""
    git_exe = shutil.which("git") or "git"
    cmd = [git_exe, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(cwd),
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timed out after {_GIT_TIMEOUT}s"
    except FileNotFoundError:
        return False, "git executable not found in PATH"
    except Exception as exc:
        return False, f"git execution error: {exc}"


# ─────────────────────────────────────────────────────────────
# git_status
# ─────────────────────────────────────────────────────────────


class GitStatusTool(BaseTool):
    """Return working tree status: branch, staged files, modifications."""

    name = "git_status"
    description = (
        "Show the current git status: branch name, ahead/behind remote, staged files, "
        "modified files, and untracked files."
    )
    owner_only = False
    parameters = [
        {
            "name": "short",
            "type": "boolean",
            "description": "Use short one-line-per-file output (default: True)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        git_root = _find_git_root()
        if git_root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="Not inside a git repository",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        short = args.get("short", True)
        if short:
            ok, output = _run_git(["status", "--short", "--branch"], cwd=git_root)
        else:
            ok, output = _run_git(["status"], cwd=git_root)

        if not ok:
            return ToolResult(
                name=self.name,
                success=False,
                error=output,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        output = cap_result(output.strip() or "(clean working tree)", tool_name=self.name)
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


# ─────────────────────────────────────────────────────────────
# git_diff
# ─────────────────────────────────────────────────────────────


class GitDiffTool(BaseTool):
    """Show git diff for the working tree or a specific file."""

    name = "git_diff"
    description = (
        "Show git diff.  By default shows all unstaged changes.  "
        "Set staged=True for staged (index) diff.  "
        "Set path to restrict to a specific file or directory.  "
        "Output is capped; large diffs are truncated."
    )
    owner_only = False
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Restrict diff to this file or directory (optional)",
            "required": False,
        },
        {
            "name": "staged",
            "type": "boolean",
            "description": "Show staged (cached) diff instead of working tree diff (default: False)",
            "required": False,
        },
        {
            "name": "context_lines",
            "type": "integer",
            "description": "Number of context lines around changes (default: 3)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        git_root = _find_git_root()
        if git_root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="Not inside a git repository",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        diff_args = ["diff"]
        if args.get("staged", False):
            diff_args.append("--cached")
        context = args.get("context_lines", 3)
        diff_args.append(f"--unified={int(context)}")
        path = args.get("path")
        if path:
            diff_args.extend(["--", str(path)])

        ok, output = _run_git(diff_args, cwd=git_root)

        if not ok:
            return ToolResult(
                name=self.name,
                success=False,
                error=output,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        output = cap_result(output.strip() or "(no diff)", tool_name=self.name)
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


# ─────────────────────────────────────────────────────────────
# git_log
# ─────────────────────────────────────────────────────────────


class GitLogTool(BaseTool):
    """Show recent git commit history."""

    name = "git_log"
    description = (
        "Show recent git commit log.  "
        "Use n to control how many commits to show (default 10, max 100).  "
        "Use oneline=True for compact output.  "
        "Use path to show only commits affecting a specific file."
    )
    owner_only = False
    parameters = [
        {
            "name": "n",
            "type": "integer",
            "description": "Number of commits to show (default: 10, max: 100)",
            "required": False,
        },
        {
            "name": "oneline",
            "type": "boolean",
            "description": "Use oneline format: <hash> <subject> (default: True)",
            "required": False,
        },
        {
            "name": "path",
            "type": "string",
            "description": "Restrict history to commits that touched this file or directory",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        git_root = _find_git_root()
        if git_root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="Not inside a git repository",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        n = min(int(args.get("n", 10)), 100)
        oneline = args.get("oneline", True)
        path = args.get("path")

        log_args = ["log", f"-{n}"]
        if oneline:
            log_args.append("--oneline")
        else:
            log_args.extend(["--format=%H %ai %an%n%s%n%b%n---"])
        if path:
            log_args.extend(["--", str(path)])

        ok, output = _run_git(log_args, cwd=git_root)
        if not ok:
            return ToolResult(
                name=self.name,
                success=False,
                error=output,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        output = cap_result(output.strip() or "(no commits)", tool_name=self.name)
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


# ─────────────────────────────────────────────────────────────
# git_commit
# ─────────────────────────────────────────────────────────────


class GitCommitTool(BaseTool):
    """Stage files and create a git commit.

    This is a **WRITE** operation — ``owner_only = True`` triggers the
    safety approval system.
    """

    name = "git_commit"
    description = (
        "Stage files and create a git commit.  "
        "Provide a commit message.  "
        "Use add_all=True to stage all tracked and untracked files (git add -A).  "
        "Use files=['a.py', 'b.py'] to stage specific files only.  "
        "If neither add_all nor files is set, commits only already-staged changes.  "
        "WARNING: This is a write operation — it will create a permanent git commit."
    )
    owner_only = True
    parameters = [
        {
            "name": "message",
            "type": "string",
            "description": "Commit message (required)",
            "required": True,
        },
        {
            "name": "files",
            "type": "array",
            "description": "List of specific file paths to stage before committing (optional)",
            "required": False,
        },
        {
            "name": "add_all",
            "type": "boolean",
            "description": "If True, runs 'git add -A' before committing (default: False)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        git_root = _find_git_root()
        if git_root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="Not inside a git repository",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        message = str(args.get("message", "")).strip()
        if not message:
            return ToolResult(
                name=self.name,
                success=False,
                error="'message' is required for git_commit",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        add_all = args.get("add_all", False)
        files = args.get("files") or []

        # Stage files
        if add_all:
            ok, err = _run_git(["add", "-A"], cwd=git_root)
            if not ok:
                return ToolResult(
                    name=self.name,
                    success=False,
                    error=f"git add -A failed: {err}",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )
        elif files:
            ok, err = _run_git(["add", "--", *files], cwd=git_root)
            if not ok:
                return ToolResult(
                    name=self.name,
                    success=False,
                    error=f"git add failed: {err}",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )

        # Commit
        ok, output = _run_git(["commit", "-m", message], cwd=git_root)
        if not ok:
            if "nothing to commit" in output.lower():
                return ToolResult(
                    name=self.name,
                    success=True,
                    output="Nothing to commit — working tree clean.",
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )
            return ToolResult(
                name=self.name,
                success=False,
                error=output,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        return ToolResult(
            name=self.name,
            success=True,
            output=output.strip(),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


# ─────────────────────────────────────────────────────────────
# git_stash
# ─────────────────────────────────────────────────────────────


class GitStashTool(BaseTool):
    """Manage git stash: push, pop, or list stashes."""

    name = "git_stash"
    description = (
        "Manage git stash.  "
        "action='list' (default) — show all stashes.  "
        "action='push' — stash uncommitted changes (optionally with a message).  "
        "action='pop' — restore the most recent stash."
    )
    owner_only = False
    parameters = [
        {
            "name": "action",
            "type": "string",
            "description": "Stash action: 'list', 'push', or 'pop' (default: 'list')",
            "enum": ["list", "push", "pop"],
            "required": False,
        },
        {
            "name": "message",
            "type": "string",
            "description": "Message for 'push' action (optional)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        git_root = _find_git_root()
        if git_root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="Not inside a git repository",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        action = str(args.get("action", "list")).lower()
        message = args.get("message", "")

        stash_args: list[str]
        if action == "list":
            stash_args = ["stash", "list"]
        elif action == "push":
            stash_args = ["stash", "push"]
            if message:
                stash_args.extend(["-m", str(message)])
        elif action == "pop":
            stash_args = ["stash", "pop"]
        else:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Unknown stash action {action!r}. Use 'list', 'push', or 'pop'.",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        ok, output = _run_git(stash_args, cwd=git_root)
        if not ok:
            return ToolResult(
                name=self.name,
                success=False,
                error=output,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        output = cap_result(output.strip() or "(no stashes)", tool_name=self.name)
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )
