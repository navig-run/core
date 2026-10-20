"""Git provenance — metadata-only observations for Recall. Git stays the truth.

Every function shells out to ``git -C <root>`` with locale-stable formats, a short
timeout, and ``None`` on any failure — a CLI or a view must never crash because git
hiccupped or the folder is not a repository. Nothing here reads diffs, blobs or trees:
commit metadata and touched file NAMES only, so the ``commits`` cache can never hold
content, and no result is ever fabricated when git is unavailable.

Session→commit detection stays transcript-based (``session_links.commit_shas``); this
module ENRICHES those short ids with subject/author/dates and ancestor verdicts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

_TIMEOUT_S = 6.0
_FIELD = "\x1f"  # unit separator — cannot appear as a field boundary in practice
# %H sha · %an author · %aI author date (ISO) · %cn committer · %cI committer date · %s subject
_LOG_FMT = _FIELD.join(("%H", "%an", "%aI", "%cn", "%cI", "%s"))


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"LC_ALL": "C", "LANG": "C", "GIT_TERMINAL_PROMPT": "0"})
    return env


def _git(root: str | Path, *args: str) -> str | None:
    """Run one git command; stdout on success, ``None`` on any failure (never raises)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TIMEOUT_S, check=False, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return out.stdout if out.returncode == 0 else None


def _run_rc(root: str | Path, *args: str) -> int | None:
    """Run one git command; the return code, or ``None`` when git could not run."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, timeout=_TIMEOUT_S, check=False, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return out.returncode


def toplevel(path: str | Path) -> str | None:
    """Repository top-level for *path* (resolved by git, worktree-aware), or None."""
    out = _git(path, "rev-parse", "--show-toplevel")
    return out.strip() or None if out else None


def default_branch(root: str | Path) -> str | None:
    """The repository's default branch (``origin/HEAD`` → main → master), or None."""
    out = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if out and out.strip():
        ref = out.strip()
        return ref.split("/", 1)[1] if ref.startswith("origin/") else ref
    for candidate in ("main", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", candidate) is not None:
            return candidate
    return None


def commit_meta(root: str | Path, sha: str) -> dict[str, Any] | None:
    """Commit metadata (names/dates/subject + touched file names), or None when git
    cannot see *sha*. Never returns diff content."""
    out = _git(root, "log", "-1", f"--format={_LOG_FMT}", "--no-show-signature", sha)
    if out is None:
        return None
    parts = out.rstrip("\n").split(_FIELD, 5)
    if len(parts) != 6:
        return None
    full, author, author_ts, _committer, committer_ts, subject = parts
    files: list[dict[str, str]] = []
    raw = _git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "--root", full)
    if raw:
        for line in raw.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2:
                entry = {"status": cols[0], "path": cols[-1]}
                if cols[0].startswith("R") and len(cols) == 3:
                    entry["old_path"] = cols[1]
                files.append(entry)
    return {"sha": full, "subject": subject, "author": author, "author_ts": author_ts,
            "committer_ts": committer_ts, "files": files}


def commit_exists(root: str | Path, sha: str) -> bool | None:
    """True/False when git answers, ``None`` when there is no verdict (git died, or the
    folder is not a repository). ``cat-file -e`` exits 128 BOTH for "no such object" and
    for "not a repository" — one extra ``rev-parse --git-dir`` call disambiguates."""
    rc = _run_rc(root, "cat-file", "-e", f"{sha}^{{commit}}")
    if rc is None:
        return None
    if rc == 0:
        return True
    if rc == 1:
        return False
    # rc 128 (or anything else): ask git whether we are even inside a repository
    return False if _run_rc(root, "rev-parse", "--git-dir") == 0 else None


def is_ancestor(root: str | Path, sha: str, ref: str) -> bool | None:
    """``merge-base --is-ancestor``: True (merged into *ref*), False (not),
    None (no verdict — git could not run or rejected the arguments)."""
    rc = _run_rc(root, "merge-base", "--is-ancestor", sha, ref)
    if rc is None or rc not in (0, 1):
        return None
    return rc == 0


def commits_touching(root: str | Path, path: str, *, since: str | None = None,
                     limit: int = 50) -> list[str]:
    """Commit shas touching *path* (repo-relative or absolute), newest first. [] on failure."""
    args = ["log", f"--max-count={max(1, limit)}", "--format=%H"]
    if since:
        args.append(f"--since={since}")
    args += ["--", path]
    out = _git(root, *args)
    if not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]
