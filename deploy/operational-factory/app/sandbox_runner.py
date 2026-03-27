from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException

app = FastAPI(title="NAVIG Sandbox Runner", version="0.1.0")


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 120):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-8000:],
    }


def _validate_repo_path(repo_path: str) -> Path:
    """Resolve and validate repo_path to prevent path traversal (CWE-22).

    Only allows paths within the user home directory, system temp directory,
    or well-known Docker workspace mounts (/repo, /workspace).
    """
    resolved = Path(repo_path).resolve()
    allowed_roots: tuple[Path, ...] = (
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path("/repo").resolve(),
        Path("/workspace").resolve(),
    )
    if not any(
        resolved == root or str(resolved).startswith(str(root) + "/")
        for root in allowed_roots
    ):
        raise HTTPException(status_code=400, detail="repo_path is outside allowed directories")
    if not resolved.exists():
        raise HTTPException(status_code=400, detail=f"repo_path not found: {repo_path}")
    return resolved


def _copy_repo(repo_path: str) -> Path:
    src = _validate_repo_path(repo_path)

    tmp = Path(tempfile.mkdtemp(prefix="navig-sandbox-"))
    dst = tmp / "repo"
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".venv", "node_modules", "__pycache__", ".pytest_cache"
        ),
    )
    return dst


@app.get("/health")
def health():
    return {"ok": True, "service": "sandbox-runner"}


@app.post("/repo/scan")
def repo_scan(payload: dict):
    repo_path = payload.get("repo_path")
    if not repo_path:
        raise HTTPException(status_code=400, detail="repo_path required")

    repo = _validate_repo_path(repo_path)
    if (repo / ".git").exists():
        status = _run(["git", "status", "--short"], cwd=str(repo))
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
        return {
            "mode": "git",
            "branch": branch.get("stdout", "").strip(),
            "status": status,
        }

    files = []
    for p in repo.glob("*"):
        files.append(p.name)
    return {"mode": "filesystem", "top_level": files[:200], "note": "No .git found"}


@app.post("/repo/patch")
def repo_patch(payload: dict):
    repo_path = payload.get("repo_path")
    instructions = payload.get("instructions", "")
    if not repo_path:
        raise HTTPException(status_code=400, detail="repo_path required")

    sandbox_repo = _copy_repo(repo_path)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    patch_file = sandbox_repo / "OPERATIONAL_FACTORY_PATCH_PLAN.md"
    patch_file.write_text(
        "# Operational Factory Patch Plan\n\n"
        f"Generated: {timestamp} UTC\n\n"
        "## Instructions\n"
        f"{instructions}\n",
        encoding="utf-8",
    )

    git_present = Path(sandbox_repo / ".git").exists()
    artifacts = {
        "sandbox_path": str(sandbox_repo),
        "changed_files": [str(patch_file.relative_to(sandbox_repo))],
        "commands": [
            "git checkout -b of/<timestamp>",
            "git add OPERATIONAL_FACTORY_PATCH_PLAN.md",
            "git commit -m 'chore: add operational factory patch plan'",
            "pytest -q",
        ],
    }

    if git_present:
        _run(["git", "checkout", "-b", f"of/{timestamp}"], cwd=str(sandbox_repo))
        _run(["git", "add", "OPERATIONAL_FACTORY_PATCH_PLAN.md"], cwd=str(sandbox_repo))
        _run(
            ["git", "commit", "-m", "chore: add operational factory patch plan"],
            cwd=str(sandbox_repo),
        )
        diff = _run(
            ["git", "show", "--stat", "--patch", "--max-count=1"], cwd=str(sandbox_repo)
        )
        artifacts["diff_summary"] = diff.get("stdout", "")[:5000]
        artifacts["mode"] = "git"
    else:
        artifacts["mode"] = "filesystem"
        artifacts["diff_summary"] = (
            "No git repository found; generated patch plan file only."
        )

    return artifacts


# Allowlist of safe test commands accepted via the /repo/test endpoint.
# Free-form shell execution is never permitted — this prevents command-line
# injection through user-controlled ``command`` values (CWE-78).
_ALLOWED_TEST_COMMANDS: dict[str, list[str]] = {
    "pytest -q": ["pytest", "-q"],
    "pytest": ["pytest", "-q", "--tb=short"],
    "pytest -v": ["pytest", "-v", "--tb=short"],
    "python -m pytest": ["python", "-m", "pytest", "-q"],
    "python -m pytest -q": ["python", "-m", "pytest", "-q"],
    "python -m compileall -q .": ["python", "-m", "compileall", "-q", "."],
}


@app.post("/repo/test")
def repo_test(payload: dict):
    repo_path = payload.get("repo_path")
    command = payload.get("command", "pytest -q")
    if not repo_path:
        raise HTTPException(status_code=400, detail="repo_path required")

    # Security: reject any command not in the explicit allowlist.
    argv = _ALLOWED_TEST_COMMANDS.get(command)
    if argv is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"command '{command}' is not allowed. "
                f"Permitted values: {sorted(_ALLOWED_TEST_COMMANDS)}"
            ),
        )

    sandbox_repo = _copy_repo(repo_path)
    run = _run(argv, cwd=str(sandbox_repo), timeout=240)
    return {
        "sandbox_path": str(sandbox_repo),
        "command": command,
        "result": run,
    }
