"""Git state of a project folder — branch, dirty count, recent commits.

Bounded: every git call has a timeout, and a folder that does not hold its own
``.git`` reports ``{"repo": False}`` — a project nested inside an unrelated outer
repo must not report that repo's state as its own. A call that runs out of time is
NOT "no repo": it reports ``unknown`` so nothing presents a guess as a fact.

Cost matters: the project list runs this once per project, and on a loaded Windows
machine one git process costs 0.6–2.7 s. So it is two processes, not four — one
``status --porcelain=v2 --branch`` (branch + changes) and one ``log``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["commits_since", "git_state", "has_own_repo", "head_sha", "same_repo",
           "toplevel"]

# Measured: a trivial call took up to 2.7 s on a loaded machine, and the old 2.0 s
# ceiling turned a slow repo into "not a repo".
_TIMEOUT = 6.0


class _TimedOut(Exception):
    """git did not answer in time — the state is unknown, not absent."""


def _env(root: Path) -> dict[str, str]:
    """Never let git climb above *root*: a broken `.git` there must read as "no repo",
    not as the state of whatever repo happens to contain the folder."""
    return {**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)}


def _git_strict(root: Path, *args: str) -> str | None:
    """stdout, or None when git ran and failed. Raises _TimedOut when it did not finish."""
    try:
        cp = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_TIMEOUT, env=_env(root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise _TimedOut from exc
    except (OSError, subprocess.SubprocessError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def _git(root: Path, *args: str) -> str | None:
    try:
        return _git_strict(root, *args)
    except _TimedOut:
        return None


def worktrees(root: str | Path) -> list[str]:
    """Every checkout of *root*'s repository — the main one first, then worktrees.

    A project is one repository even when work happens in a ``.dev/worktrees/<slug>``
    checkout, so session/project linking must see all of them. Reads only; returns
    ``[]`` for a non-repo or an unreadable git rather than raising.
    """
    base = Path(root)
    out = _git(base, "worktree", "list", "--porcelain")
    if not out:
        return []
    paths: list[str] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            p = line[len("worktree "):].strip()
            if p and p not in paths:
                paths.append(p)
    return paths


def has_own_repo(root: Path) -> bool:
    """Cheap pre-check: does *root* hold its OWN `.git` (dir, or a worktree's file)?

    Deliberately not "is it inside a repo": a project nested in an unrelated outer
    repo must not report that repo as its own. Callers that act on the answer
    confirm with ``rev-parse --show-toplevel`` (:func:`git_state` does).
    """
    try:
        return (root / ".git").exists()
    except OSError:
        return False


def commits_since(root: Path, timestamp: float) -> int | None:
    """Commits on HEAD newer than *timestamp* (epoch seconds), or None without a repo."""
    if not has_own_repo(root) or not timestamp:
        return None
    from datetime import datetime, timezone  # noqa: PLC0415

    since = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _git(root, "rev-list", "--count", f"--since={since}", "HEAD")
    try:
        return int((out or "").strip())
    except ValueError:
        return None


def head_branch(root: Path) -> str | None:
    """The checked-out branch, read from the files — no git process.

    ``"HEAD"`` when detached (the same word ``git_state`` uses), None when *root* holds
    no readable repo of its own. Follows a worktree's ``.git`` file to its real git dir.
    """
    git = root / ".git"
    try:
        if git.is_file():
            pointer = git.read_text(encoding="utf-8", errors="replace").strip()
            if not pointer.startswith("gitdir:"):
                return None
            gitdir = Path(pointer[len("gitdir:"):].strip())
            git = gitdir if gitdir.is_absolute() else (root / gitdir)
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    return "HEAD" if head else None


def head_sha(root: Path) -> str:
    """HEAD's sha, or "" — cheap enough to use in a fingerprint."""
    if not has_own_repo(root):
        return ""
    out = _git(root, "rev-parse", "HEAD")
    return (out or "").strip()


def git_state(root: Path, *, commits: int = 5, untracked: bool = True) -> dict[str, Any]:
    """Branch, change counts and recent commits of the repo *root* holds itself.

    ``{"repo": False}`` when *root* has no ``.git`` of its own, or git cannot read it.
    ``unknown: True`` when git did not answer in time — callers must not cache that.
    """
    if not has_own_repo(root):
        return {"repo": False}
    try:
        # A `.git` AT root makes root the toplevel by definition, so no separate
        # `rev-parse --show-toplevel`; a broken one fails this call instead (the
        # ceiling in _env stops git falling back to an outer repo).
        status = _git_strict(root, "status", "--porcelain=v2", "--branch",
                             "--untracked-files=normal" if untracked else "--untracked-files=no")
        if status is None:
            return {"repo": False}
        branch, has_commits, changed, new = _parse_status(status)
        log = _git_strict(root, "log", f"-{max(1, commits)}",
                          "--pretty=format:%h\x1f%s\x1f%cI\x1f%an") if has_commits else ""
    except _TimedOut:
        logger.debug("git timed out reading %s", root)
        return {"repo": True, "unknown": True, "branch": None, "dirty": None, "untracked": None,
                "recent": [], "last_commit": None}
    recent = []
    for line in (log or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            recent.append({"sha": parts[0], "subject": parts[1], "date": parts[2], "author": parts[3]})
    return {
        "repo": True,
        "branch": branch,
        "dirty": changed + new,
        "untracked": new,
        "recent": recent,
        "last_commit": recent[0]["date"] if recent else None,
    }


def _parse_status(text: str) -> tuple[str, bool, int, int]:
    """(branch, has commits, changed entries, untracked entries) from porcelain v2."""
    branch, has_commits, changed, new = "", True, 0, 0
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            # `rev-parse --abbrev-ref HEAD` said "HEAD" when detached; keep that contract.
            branch = "HEAD" if head == "(detached)" else head
        elif line.startswith("# branch.oid "):
            has_commits = line.strip() != "# branch.oid (initial)"
        elif line.startswith("? "):
            new += 1
        elif line and not line.startswith(("#", "!")):
            changed += 1
    return branch, has_commits, changed, new


def _git_open(root: Path, *args: str) -> str | None:
    """stdout or None — the CLIMBING variant of :func:`_git`.

    ``_env``'s ``GIT_CEILING_DIRECTORIES`` exists so ``git_state`` reads THIS folder's
    repo and never an outer one; ``toplevel``/``--git-common-dir`` ask "which repo
    contains this", which requires ascending at least one level — under a ceiling that
    starts at ``root.parent`` (any subdirectory of a checkout) git answers nothing and
    every subdir silently read as "not a repo".
    """
    try:
        cp = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_TIMEOUT,
            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def toplevel(path: str | Path) -> str | None:
    """Repository worktree root containing *path* (worktree-aware), else None.

    Deliberately climbs without a ceiling — the question IS "which repo contains
    this", including a git worktree whose root is a checkout directory. Returns the
    native absolute path string, or None when git cannot answer (not a repo, git
    missing, timeout). For a file path, resolves from its parent directory.
    """
    p = Path(path)
    start = p if p.is_dir() else p.parent
    if not start.exists():
        return None
    out = _git_open(start, "rev-parse", "--show-toplevel")
    if not out or not out.strip():
        return None
    try:
        return str(Path(out.strip()))
    except (OSError, ValueError):
        return out.strip()


def common_git_dir(path: str | Path) -> str | None:
    """Absolute ``--git-common-dir`` for *path* — equal for every checkout (worktree)
    of one repository — or None when git cannot answer."""
    p = Path(path)
    start = p if p.is_dir() else p.parent
    if not start.exists():
        return None
    raw_out = _git_open(start, "rev-parse", "--git-common-dir")
    if not raw_out or not raw_out.strip():
        return None
    raw = raw_out.strip()
    try:
        resolved = Path(raw) if Path(raw).is_absolute() else Path(start, raw)
        return str(resolved.resolve())
    except (OSError, ValueError):
        return raw


def same_repo(a: str | Path, b: str | Path) -> bool:
    """True when *a* and *b* belong to the SAME repository — main checkout and any
    of its worktrees share one ``--git-common-dir``. False when either is not a repo."""
    ca, cb = common_git_dir(a), common_git_dir(b)
    if not ca or not cb:
        return False
    return os.path.normcase(ca) == os.path.normcase(cb)
