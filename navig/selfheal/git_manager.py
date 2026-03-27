"""navig.selfheal.git_manager — Fork, clone, branch, sync, commit, push.

Implements the automated Git flow for the Self-Heal contribution pipeline:

    1. GitHub API → Fork navig-run/core → {user}/navig (idempotent)
    2. git clone {user}/navig into ~/.navig/core-repo/
       (or git fetch + rebase upstream/main if already cloned)
    3. git checkout -b navig-selfheal/{YYYYMMDD}-{short-hash}
    4. git apply <patch>
    5. git commit + git push

Uses stdlib ``subprocess`` only (no gitpython — avoids ~20 MB overhead).
GitHub REST calls use ``httpx`` (already a project dependency).
"""

from __future__ import annotations

import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Single canonical upstream reference used throughout the codebase.
UPSTREAM_REPO = "navig-run/core"
from navig.platform.paths import config_dir as _navig_config_dir

UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"
_GITHUB_API = "https://api.github.com"
_CORE_REPO_DIR = _navig_config_dir() / "core-repo"
_GIT_TIMEOUT = 120  # seconds — generous for slow connections


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_git(
    *args: str,
    cwd: Path | None = None,
    ignore_errors: bool = False,
    timeout: int = _GIT_TIMEOUT,
) -> str:
    """Run a git sub-command, return stdout.

    Args:
        *args: Arguments appended after ``git``.
        cwd: Working directory.  Defaults to ``_CORE_REPO_DIR``.
        ignore_errors: When True, swallow ``CalledProcessError`` and return
            an empty string.  **Use only for idempotent operations** where a
            non-zero exit is expected and harmless — e.g., ``git remote add``
            when the remote already exists.  Never use this flag on fetch,
            push, or apply calls where a failure indicates a real problem.
        timeout: Maximum seconds to wait for the process.

    Returns:
        Decoded stdout string (stripped).

    Raises:
        subprocess.CalledProcessError: When the command fails and
            *ignore_errors* is False.
    """
    cmd = ["git"] + list(args)
    work_dir = cwd or _CORE_REPO_DIR
    logger.debug("git {}", " ".join(args[: min(len(args), 4)]))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        if ignore_errors:
            logger.debug("git {} suppressed: {}", args[0], exc.stderr.strip())
            return ""
        logger.error("git {} failed: {}", args[0], exc.stderr.strip())
        raise


def _github_headers(token: str) -> dict[str, str]:
    """Return standard GitHub REST API request headers for *token*.

    Extracted as a shared helper so ``git_manager`` and ``pr_builder`` use
    identical header sets without duplicating the dict literal.

    Args:
        token: Personal access token.

    Returns:
        Dict of HTTP headers suitable for ``httpx.request``.
    """
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_request(
    method: str,
    path: str,
    token: str,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Execute a GitHub REST API request.

    Args:
        method: HTTP method (e.g. ``"POST"``, ``"GET"``).
        path: Path after ``https://api.github.com`` (e.g. ``"/user"``).
        token: Personal access token with required scopes.
        json: Optional request body serialised as JSON.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response dict.

    Raises:
        ValueError: When the response status indicates failure (non-2xx).
    """
    # httpx is an existing dependency; import deferred to keep startup fast.
    import httpx  # noqa: PLC0415 — lazy import intentional

    url = f"{_GITHUB_API}{path}"
    response = httpx.request(
        method, url, headers=_github_headers(token), json=json, timeout=timeout
    )
    if not response.is_success:
        raise ValueError(
            f"GitHub API {method} {path} → {response.status_code}: {response.text[:200]}"
        )
    return response.json() if response.content else {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_github_username(token: str) -> str:
    """Return the authenticated GitHub username for *token*.

    Args:
        token: GitHub personal access token.

    Returns:
        GitHub login name (e.g. ``"octocat"``).

    Raises:
        ValueError: On API failure.
    """
    user = _github_request("GET", "/user", token=token)
    return user["login"]


def fork_repo(token: str, username: str) -> str:
    """Fork ``navig-run/core`` into ``{username}/navig`` (idempotent).

    The fork API is idempotent — calling it on an existing fork returns the
    existing fork without creating a duplicate.

    Args:
        token: PAT with ``repo`` and ``workflow`` scopes (or ``public_repo``
            for public fork targets).
        username: GitHub username; used only for logging.

    Returns:
        HTML URL of the fork (e.g. ``"https://github.com/octocat/navig"``).

    Raises:
        ValueError: On API failure.
    """
    logger.info("Forking {} → {}/navig", UPSTREAM_REPO, username)
    data = _github_request(
        "POST",
        f"/repos/{UPSTREAM_REPO}/forks",
        token=token,
        json={"default_branch_only": True},
    )
    fork_url: str = data["html_url"]
    logger.info("Fork ready: {}", fork_url)
    return fork_url


def clone_or_update(fork_clone_url: str) -> Path:
    """Clone the user's fork into ``~/.navig/core-repo/``, or update it.

    If the directory already contains a clone, fetches from origin and
    rebases onto ``upstream/main`` instead of re-cloning.

    Args:
        fork_clone_url: HTTPS clone URL of the user's fork
            (e.g. ``"https://github.com/octocat/navig.git"``).

    Returns:
        Absolute path to ``~/.navig/core-repo/``.
    """
    repo_path = _CORE_REPO_DIR
    if not (repo_path / ".git").exists():
        logger.info("Cloning {} → {}", fork_clone_url, repo_path)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git("clone", fork_clone_url, str(repo_path), cwd=repo_path.parent)
    else:
        logger.info("Repo already cloned; fetching latest")
        _run_git("fetch", "origin", cwd=repo_path)
    return repo_path


def sync_fork(repo_path: Path) -> None:
    """Ensure the local fork is up-to-date with ``upstream/main``.

    Steps:
        1. Add ``upstream`` remote (idempotent — ignores error if exists).
        2. Fetch upstream.
        3. Checkout ``main``.
        4. Rebase onto ``upstream/main``.
        5. Push ``main`` to ``origin`` with ``--force-with-lease``.

    Args:
        repo_path: Absolute path to the local git repository.
    """
    logger.info("Syncing fork with upstream {}", UPSTREAM_URL)
    # ignore_errors=True is intentional here: the remote may already exist
    # from a previous scan run.  All other git calls in this function must
    # NOT use ignore_errors so failures surface immediately.
    _run_git(
        "remote", "add", "upstream", UPSTREAM_URL, cwd=repo_path, ignore_errors=True
    )
    _run_git("fetch", "upstream", cwd=repo_path)
    _run_git("checkout", "main", cwd=repo_path)
    _run_git("rebase", "upstream/main", cwd=repo_path)
    _run_git("push", "origin", "main", "--force-with-lease", cwd=repo_path)
    logger.info("Fork synced successfully")


def create_branch(repo_path: Path) -> str:
    """Create and checkout a new ``navig-selfheal/{date}-{hash}`` branch.

    The short hash is derived from the current UTC timestamp to guarantee
    uniqueness across multiple scans per day.

    Args:
        repo_path: Absolute path to the local git repository.

    Returns:
        Branch name, e.g. ``"navig-selfheal/20260315-a3f8c2d1"``.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    # secrets.token_hex(4) produces 8 hex chars of cryptographically random
    # entropy — simpler and more idiomatic than mixing time_ns + os.urandom.
    short_hash = secrets.token_hex(4)
    branch = f"navig-selfheal/{date_str}-{short_hash}"
    _run_git("checkout", "-b", branch, cwd=repo_path)
    logger.info("Created branch {}", branch)
    return branch


def apply_patch(repo_path: Path, patch_content: str) -> None:
    """Apply a unified diff patch to the working tree via ``git apply``.

    Writes the patch to a temporary file (cleaned up in a ``finally`` block
    even on interrupt or error), runs ``git apply --check`` to detect
    conflicts without modifying any files, then applies for real.

    Args:
        repo_path: Absolute path to the local git repository.
        patch_content: Raw unified diff string.

    Raises:
        subprocess.CalledProcessError: When the patch does not apply cleanly.
    """
    import tempfile  # stdlib — deferred inside function per lazy-import rule

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".patch",
        prefix="navig-selfheal-",
        dir=repo_path,
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(patch_content)
        patch_path = Path(tmp.name)

    try:
        logger.info("Checking patch applicability")
        _run_git("apply", "--check", str(patch_path), cwd=repo_path)
        logger.info("Applying patch")
        _run_git("apply", str(patch_path), cwd=repo_path)
    finally:
        patch_path.unlink(missing_ok=True)


def commit_and_push(repo_path: Path, branch: str, summary: str) -> None:
    """Stage all changes, commit, and push to origin.

    Args:
        repo_path: Absolute path to the local git repository.
        branch: Branch name to push (e.g. ``"navig-selfheal/20260315-a3f8c2d1"``).
        summary: Short human-readable description included in the commit message.
    """
    _run_git("add", "--all", cwd=repo_path)
    commit_msg = f"fix: self-heal scan {summary}"
    _run_git("commit", "-m", commit_msg, cwd=repo_path)
    _run_git("push", "origin", branch, cwd=repo_path)
    logger.info("Pushed branch {} to origin", branch)
