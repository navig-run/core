"""
Workspace ownership policy for NAVIG.

Personal/state workspace files are user-scoped and must live in:
    ~/.navig/workspace/

Project-level .navig/ is reserved for project-scoped config and state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# User-level canonical locations
USER_NAVIG_DIR = Path.home() / ".navig"
USER_WORKSPACE_DIR = USER_NAVIG_DIR / "workspace"

# Project-level workspace location (legacy / discouraged for personal files)
PROJECT_NAVIG_DIRNAME = ".navig"
PROJECT_WORKSPACE_SUBPATH = Path(PROJECT_NAVIG_DIRNAME) / "workspace"

# File ownership policy
PERSONAL_STATE_FILES = {
    "AGENTS.md",
    "SOUL.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
    "PERSONA.md",
    "COMPANION.md",
    "goals.json",
    "remediation_actions.json",
}

GENERATED_DEFAULT_FILES = {
    "AGENTS.md",
    "BOOTSTRAP.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "HEARTBEAT.md",
}


@dataclass
class WorkspaceDuplicate:
    file_name: str
    project_path: Path
    user_path: Path | None
    status: str


def classify_workspace_file(file_name: str) -> str:
    """
    Classify a workspace file for audits.

    Returns:
        "generated_default" or "personal_customized"
    """
    return "generated_default" if file_name in GENERATED_DEFAULT_FILES else "personal_customized"


def is_project_workspace_path(path: Path, project_root: Path | None = None) -> bool:
    """Return True when path points to a project-local .navig/workspace directory."""
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path.expanduser()
    root = (project_root or Path.cwd()).resolve()
    project_workspace = (root / PROJECT_WORKSPACE_SUBPATH).resolve()
    return resolved == project_workspace


def resolve_personal_workspace_path(
    requested_workspace: Path | None,
    *,
    project_root: Path | None = None,
) -> tuple[Path, Path | None]:
    """
    Resolve canonical user workspace path, preserving legacy project path as fallback.

    Returns:
        (canonical_user_workspace, legacy_workspace_or_none)
    """
    canonical = USER_WORKSPACE_DIR
    if requested_workspace is None:
        return canonical, None

    requested = requested_workspace.expanduser()
    try:
        requested_resolved = requested.resolve()
        canonical_resolved = canonical.resolve()
    except Exception:
        requested_resolved = requested
        canonical_resolved = canonical

    if requested_resolved == canonical_resolved:
        return canonical, None

    # Personal/state files are canonical at user-level workspace only.
    # Keep non-canonical path as legacy read fallback, never as write target.
    return canonical, requested


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_project_workspace_duplicates(
    *,
    project_root: Path | None = None,
    user_workspace: Path | None = None,
) -> list[WorkspaceDuplicate]:
    """
    Detect personal/state file duplication between project and user workspace.

    Does not modify files.
    """
    root = (project_root or Path.cwd()).resolve()
    project_workspace = root / PROJECT_WORKSPACE_SUBPATH
    user_ws = (user_workspace or USER_WORKSPACE_DIR).expanduser()

    duplicates: list[WorkspaceDuplicate] = []
    if not project_workspace.is_dir():
        return duplicates

    for file_name in sorted(PERSONAL_STATE_FILES):
        project_file = project_workspace / file_name
        if not project_file.exists():
            continue

        user_file = user_ws / file_name
        if user_file.exists():
            status = (
                "duplicate_identical"
                if _sha256(project_file) == _sha256(user_file)
                else "duplicate_conflict"
            )
            duplicates.append(
                WorkspaceDuplicate(
                    file_name=file_name,
                    project_path=project_file,
                    user_path=user_file,
                    status=status,
                )
            )
        else:
            duplicates.append(
                WorkspaceDuplicate(
                    file_name=file_name,
                    project_path=project_file,
                    user_path=None,
                    status="project_only_legacy",
                )
            )

    return duplicates


def summarize_duplicates(duplicates: list[WorkspaceDuplicate]) -> dict[str, int]:
    summary: dict[str, int] = {
        "duplicate_conflict": 0,
        "duplicate_identical": 0,
        "project_only_legacy": 0,
    }
    for item in duplicates:
        summary[item.status] = summary.get(item.status, 0) + 1
    return summary
