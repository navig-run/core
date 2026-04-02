"""
navig.agent.worktree — Git worktree isolation for multi-agent branches.

Manages git worktrees so parallel agent workers (coordinator mode, FB1)
operate in isolated directories without file conflicts.  Each worker gets
its own branch and working copy, merged back on completion.

Lifecycle::

    mgr = WorktreeManager(repo_root="/path/to/repo")
    wt  = await mgr.create("worker-a")
    # …worker operates in wt.path…
    ok  = await mgr.merge_back("worker-a")
    await mgr.remove("worker-a")

Or with the async context manager for automatic cleanup::

    async with WorktreeManager(repo_root="/path") as mgr:
        wt = await mgr.create("worker-a")
        # …on exit, all worktrees are cleaned up…

FB-05 implementation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

WORKTREE_DIR = ".navig_worktrees"
BRANCH_PREFIX = "navig/"
MAX_WORKTREES = 10
WINDOWS_RETRY_ATTEMPTS = 3
WINDOWS_RETRY_DELAY = 1.0  # seconds


# ─────────────────────────────────────────────────────────────
# Worktree dataclass
# ─────────────────────────────────────────────────────────────


@dataclass
class Worktree:
    """Represents a single git worktree instance."""

    name: str
    path: Path
    branch: str
    created_at: float = field(default_factory=time.time)
    merged: bool = False
    deleted: bool = False

    @property
    def age_seconds(self) -> float:
        """Wall-clock seconds since creation."""
        return time.time() - self.created_at

    def to_dict(self) -> dict:
        """Serialise for status display."""
        return {
            "name": self.name,
            "path": str(self.path),
            "branch": self.branch,
            "age": f"{self.age_seconds:.0f}s",
            "merged": self.merged,
            "deleted": self.deleted,
        }


# ─────────────────────────────────────────────────────────────
# WorktreeManager
# ─────────────────────────────────────────────────────────────


class WorktreeManager:
    """Manages git worktrees for agent isolation.

    Parameters
    ----------
    repo_root : str | Path
        Root directory of the git repository.
    worktree_dir : str
        Name of the subdirectory under *repo_root* that houses worktrees.
        Defaults to ``.navig_worktrees``.
    """

    def __init__(
        self,
        repo_root: str | Path,
        worktree_dir: str = WORKTREE_DIR,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.worktree_base = self.repo_root / worktree_dir
        self._worktrees: dict[str, Worktree] = {}

    # ── lifecycle helpers ────────────────────────────────────

    async def create(
        self,
        name: str,
        base_branch: str = "HEAD",
    ) -> Worktree:
        """Create an isolated worktree for a worker.

        Parameters
        ----------
        name : str
            Worker identifier (used as directory name and branch suffix).
        base_branch : str
            Git ref to branch from.  Defaults to HEAD.

        Returns
        -------
        Worktree
            The newly created worktree descriptor.

        Raises
        ------
        ValueError
            If *name* is invalid or already exists.
        RuntimeError
            If the git command fails.
        """
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Invalid worktree name '{name}': "
                "use alphanumeric characters, hyphens, and underscores only."
            )

        if name in self._worktrees:
            raise ValueError(f"Worktree '{name}' already exists.")

        if len(self._worktrees) >= MAX_WORKTREES:
            raise RuntimeError(
                f"Maximum worktree limit ({MAX_WORKTREES}) reached. "
                "Remove existing worktrees first."
            )

        # Verify we're inside a git repo
        check = await self._run_git("git rev-parse --is-inside-work-tree")
        if check.returncode != 0:
            raise RuntimeError(
                f"Not a git repository: {self.repo_root}"
            )

        branch = f"{BRANCH_PREFIX}{name}"
        path = self.worktree_base / name

        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create worktree with new branch
        result = await self._run_git(
            f'git worktree add "{path}" -b {branch} {base_branch}'
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create worktree '{name}': {result.stderr.strip()}"
            )

        wt = Worktree(name=name, path=path, branch=branch)
        self._worktrees[name] = wt
        logger.info("Created worktree '%s' at %s (branch %s)", name, path, branch)
        return wt

    async def merge_back(
        self,
        name: str,
        target_branch: str = "",
    ) -> bool:
        """Merge worktree branch back to the target branch.

        Parameters
        ----------
        name : str
            Worktree name to merge.
        target_branch : str
            Branch to merge into.  Auto-detected from HEAD if empty.

        Returns
        -------
        bool
            True if merge succeeded (or nothing to merge), False on conflict.

        Raises
        ------
        ValueError
            If the named worktree does not exist.
        """
        wt = self._worktrees.get(name)
        if not wt:
            raise ValueError(f"No worktree: {name}")

        if wt.merged:
            return True

        if not target_branch:
            result = await self._run_git("git rev-parse --abbrev-ref HEAD")
            target_branch = result.stdout.strip()
            if not target_branch or result.returncode != 0:
                raise RuntimeError("Cannot determine current branch.")

        # Check if there are any new commits to merge
        result = await self._run_git(
            f"git log {target_branch}..{wt.branch} --oneline"
        )
        if not result.stdout.strip():
            wt.merged = True
            logger.info("Worktree '%s': nothing to merge.", name)
            return True

        # Attempt merge
        result = await self._run_git(
            f"git merge {wt.branch} --no-edit"
        )

        if result.returncode != 0:
            # Merge conflict — abort and report
            await self._run_git("git merge --abort")
            logger.warning(
                "Worktree '%s': merge conflict with %s.", name, target_branch
            )
            return False

        wt.merged = True
        logger.info("Worktree '%s': merged into %s.", name, target_branch)
        return True

    async def remove(self, name: str, force: bool = False) -> None:
        """Remove a worktree and delete its branch.

        Parameters
        ----------
        name : str
            Worktree to remove.
        force : bool
            Force removal even if there are uncommitted changes.
        """
        wt = self._worktrees.get(name)
        if not wt:
            return  # already gone — idempotent

        force_flag = "--force" if force else ""

        if os.name == "nt":
            # Windows: file locks may prevent immediate removal — retry
            for attempt in range(WINDOWS_RETRY_ATTEMPTS):
                result = await self._run_git(
                    f'git worktree remove "{wt.path}" {force_flag}'
                )
                if result.returncode == 0:
                    break
                logger.debug(
                    "Windows worktree remove attempt %d/%d failed: %s",
                    attempt + 1,
                    WINDOWS_RETRY_ATTEMPTS,
                    result.stderr.strip(),
                )
                await asyncio.sleep(WINDOWS_RETRY_DELAY)
            else:
                # Last resort: manual directory removal + prune
                logger.warning(
                    "Worktree '%s': git remove failed after retries; "
                    "falling back to shutil.rmtree.",
                    name,
                )
                shutil.rmtree(str(wt.path), ignore_errors=True)
                await self._run_git("git worktree prune")
        else:
            result = await self._run_git(
                f'git worktree remove "{wt.path}" {force_flag}'
            )
            if result.returncode != 0:
                if force:
                    shutil.rmtree(str(wt.path), ignore_errors=True)
                    await self._run_git("git worktree prune")
                else:
                    logger.warning(
                        "Worktree '%s' remove failed: %s",
                        name,
                        result.stderr.strip(),
                    )

        # Delete the branch (best-effort)
        await self._run_git(f"git branch -D {wt.branch}")

        wt.deleted = True
        self._worktrees.pop(name, None)
        logger.info("Removed worktree '%s'.", name)

    async def cleanup_all(self) -> int:
        """Remove all tracked worktrees and prune stale entries.

        Returns
        -------
        int
            Number of worktrees removed.
        """
        names = list(self._worktrees.keys())
        for name in names:
            await self.remove(name, force=True)

        # Prune any stale git worktree references
        await self._run_git("git worktree prune")

        # Remove the worktree directory if it's now empty
        if self.worktree_base.exists():
            try:
                remaining = list(self.worktree_base.iterdir())
                if not remaining:
                    self.worktree_base.rmdir()
            except OSError:
                pass  # directory in use or permissions

        logger.info("Cleaned up %d worktree(s).", len(names))
        return len(names)

    def list_worktrees(self) -> list[dict]:
        """Return status dicts for all active (non-deleted) worktrees."""
        return [
            wt.to_dict()
            for wt in self._worktrees.values()
            if not wt.deleted
        ]

    def get_worktree(self, name: str) -> Optional[Worktree]:
        """Look up a tracked worktree by name."""
        wt = self._worktrees.get(name)
        if wt and not wt.deleted:
            return wt
        return None

    @property
    def active_count(self) -> int:
        """Number of non-deleted worktrees."""
        return sum(1 for wt in self._worktrees.values() if not wt.deleted)

    # ── git helper ───────────────────────────────────────────

    async def _run_git(self, cmd: str) -> subprocess.CompletedProcess:
        """Run a git command in the repo root and return the result."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.repo_root),
        )
        stdout, stderr = await proc.communicate()
        return subprocess.CompletedProcess(
            cmd,
            proc.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    # ── context manager ──────────────────────────────────────

    async def __aenter__(self) -> WorktreeManager:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.cleanup_all()

    # ── repr ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"WorktreeManager(repo_root={self.repo_root!r}, "
            f"active={self.active_count})"
        )


# ─────────────────────────────────────────────────────────────
# Module-level singleton (mirroring background_task.py pattern)
# ─────────────────────────────────────────────────────────────

_manager: Optional[WorktreeManager] = None


def get_manager(repo_root: str | Path | None = None) -> WorktreeManager:
    """Return the module-level WorktreeManager singleton.

    Parameters
    ----------
    repo_root : str | Path | None
        Repository root.  Required on first call; ignored on subsequent
        calls unless :func:`reset_manager` has been called.
    """
    global _manager
    if _manager is None:
        if repo_root is None:
            raise ValueError(
                "repo_root is required on the first call to get_manager()."
            )
        _manager = WorktreeManager(repo_root)
    return _manager


def reset_manager() -> None:
    """Tear down the singleton — mainly for tests."""
    global _manager
    _manager = None
